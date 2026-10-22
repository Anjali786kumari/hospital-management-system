from functools import wraps

from flask import abort, flash, g, redirect, request, url_for

ADMIN = "Admin"
DOCTOR = "Doctor"
RECEPTIONIST = "Receptionist"


def _to_login():
    flash("Please sign in to continue.", "warning")
    nxt = request.full_path.rstrip("?") if request.method == "GET" else None
    return redirect(url_for("auth.login", next=nxt))


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not g.user:
            return _to_login()
        return view(*a, **kw)
    return wrapper


def roles_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not g.user:
                return _to_login()
            if g.user.role_name not in roles:
                abort(403)
            return view(*a, **kw)
        return wrapper
    return deco
