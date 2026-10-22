import click
from flask.cli import with_appcontext

from .extensions import db
from .seed_data import seed


def register_cli(app):
    @app.cli.command("init-db")
    @click.option("--demo/--no-demo", default=True, help="Also load demo doctors, patients, etc.")
    @with_appcontext
    def init_db(demo):
        """Create tables and seed roles, the admin account and (optionally) demo data."""
        db.create_all()
        seed(sample=demo)
        click.echo("Database ready. Logins: admin/admin123"
                   + (", reception/reception123, dr.sharma/doctor123" if demo else ""))
        click.echo("Change these passwords after first login.")
