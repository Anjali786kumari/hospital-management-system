import time
from datetime import datetime
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from sqlalchemy import func

from . import db
from .models import User

bp = Blueprint("auth", __name__)
_failed = {}  # username -> {"count": int, "until": epoch}   (per-process lockout)


def init_auth(app):
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("user_id")
        if uid:
            u = db.session.get(User, uid)
            if u and u.is_active:
                g.user = u
            else:
                session.clear()


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if g.user is None:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(view):
        @wraps(view)
        @login_required
        def wrapper(*a, **kw):
            if g.user.role_name not in roles:
                abort(403, "Your role does not have access to this page.")
            return view(*a, **kw)
        return wrapper
    return deco


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        cfg = current_app.config
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        rec = _failed.get(username)
        if rec and rec["until"] > time.time():
            wait = int(rec["until"] - time.time())
            flash(f"Too many failed attempts. Try again in {wait}s.", "danger")
            return render_template("login.html"), 429
        user = User.query.filter(func.lower(User.username) == username).first()
        if user and user.is_active and user.check_password(password):
            _failed.pop(username, None)
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            user.last_login = datetime.now()
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
        rec = _failed.setdefault(username, {"count": 0, "until": 0})
        rec["count"] += 1
        if rec["count"] >= cfg["LOGIN_MAX_ATTEMPTS"]:
            rec.update(count=0, until=time.time() + cfg["LOGIN_LOCK_SECONDS"])
        flash("Invalid username or password.", "danger")
        return render_template("login.html"), 401
    return render_template("login.html")


@bp.route("/demo/reception", methods=["POST"])
def demo_reception():
    """Sign into the seeded receptionist account for the public portfolio demo."""
    if g.user:
        return redirect(url_for("main.dashboard"))

    user = User.query.filter_by(username="reception").first()
    if not user or not user.is_active:
        flash("The receptionist demo is currently unavailable.", "warning")
        return redirect(url_for("auth.login"))
    if user.role_name != "receptionist":
        abort(403, "The demo account is not configured with the receptionist role.")

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    user.last_login = datetime.now()
    db.session.commit()
    return redirect(url_for("main.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
