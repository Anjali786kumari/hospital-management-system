import secrets
from datetime import datetime

from flask import Flask, abort, g, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    app.config.from_object(config_object)
    db.init_app(app)

    from . import models  # noqa: F401  (register tables)
    from .auth import bp as auth_bp, init_auth
    from .main import bp as main_bp
    from .patients import bp as patients_bp
    from .appointments import bp as appt_bp
    from .clinical import bp as clinical_bp
    from .billing import bp as billing_bp
    from .admin import bp as admin_bp

    init_auth(app)
    for bp in (auth_bp, main_bp, patients_bp, appt_bp, clinical_bp, billing_bp, admin_bp):
        app.register_blueprint(bp)

    # ---------------------------------------------------------------- CSRF
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]

    @app.before_request
    def csrf_protect():
        if app.config.get("CSRF_ENABLED") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get("_csrf", "")
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                abort(400, "Invalid or missing CSRF token. Reload the page and try again.")

    # ------------------------------------------------------ template helpers
    def page_url(page):
        args = request.args.to_dict()
        args["page"] = page
        return url_for(request.endpoint, **(request.view_args or {}), **args)

    # globals (not just context) so imported macros can use them too
    app.jinja_env.globals.update(csrf_token=csrf_token, page_url=page_url)

    @app.context_processor
    def inject():
        return {
            "current_user": g.get("user"),
            "currency": app.config["CURRENCY"],
            "now": datetime.now(),
        }

    @app.template_filter("money")
    def money(v):
        return f"{app.config['CURRENCY']}{(v or 0):,.2f}"

    @app.template_filter("dt")
    def fmt_dt(v):
        return v.strftime("%d %b %Y, %I:%M %p") if v else "—"

    @app.template_filter("d")
    def fmt_d(v):
        return v.strftime("%d %b %Y") if v else "—"

    @app.template_filter("t")
    def fmt_t(v):
        return v.strftime("%I:%M %p") if v else "—"

    # -------------------------------------------------------- error pages
    for code, title in ((400, "Bad request"), (403, "Access denied"), (404, "Not found"), (405, "Method not allowed")):
        app.register_error_handler(
            code,
            lambda e, code=code, title=title: (
                render_template("error.html", code=code, title=title, message=getattr(e, "description", "")),
                code,
            ),
        )

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template("error.html", code=500, title="Something went wrong", message="Please try again."), 500

    # ---------------------------------------------------------------- CLI
    @app.cli.command("init-db")
    def init_db():
        """Create all tables."""
        db.create_all()
        print("Tables created.")

    @app.cli.command("seed")
    def seed_cmd():
        """Create tables and load demo data."""
        from .seed import seed
        seed()
        print("Demo data loaded. Use the configured demo account; admin credentials are not printed.")

    return app
