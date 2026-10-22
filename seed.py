"""Convenience wrapper: python seed.py   (same as `flask --app run seed`)."""
from app import create_app
from app.seed import seed

app = create_app()
with app.app_context():
    seed()
    print("Done. Demo data loaded. Keep administrator credentials private.")
