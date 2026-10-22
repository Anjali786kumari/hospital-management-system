import time
from datetime import datetime

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request,
                   session, url_for)
from sqlalchemy import func

from ..extensions import db
from ..models import User
from ..security import login_required
from ..services import audit
from ..utils import safe_next

bp = Blueprint("auth", __name__)
_failures = {}  # "ip:username" -> [count, first_failure_ts]  (in-memory brute-force guard)


def _locked(key):
    rec = _failures.get(key)
    if not rec:
        return False
    if time.time() - rec[1] > current_app.config["LOGIN_LOCK_SECONDS"]:
        _failures.pop(key, None)
        return False
    return rec[0] >= current_app.config["LOGIN_MAX_ATTEMPTS"]


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        key = f"{request.remote_addr}:{username}"
        if _locked(key):
            flash("Too many failed attempts. Please wait a few minutes and try again.", "danger")
            return render_template("auth/login.html"), 429
        user = User.query.filter(func.lower(User.username) == username).first()
        if user and user.active and user.check_password(password):
            _failures.pop(key, None)
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            user.last_login = datetime.now()
            g.user = user
            audit("auth.login", user.username)
            db.session.commit()
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard.index"))
        rec = _failures.setdefault(key, [0, time.time()])
        rec[0] += 1
        flash("Invalid username or password." if not (user and not user.active)
              else "This account has been deactivated.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("auth.logout", g.user.username)
    db.session.commit()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        cur, new, confirm = (request.form.get(k, "") for k in ("current", "new", "confirm"))
        if not g.user.check_password(cur):
            flash("Current password is incorrect.", "danger")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new != confirm:
            flash("New passwords do not match.", "danger")
        else:
            g.user.set_password(new)
            audit("auth.password_change", g.user.username)
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/password.html")
