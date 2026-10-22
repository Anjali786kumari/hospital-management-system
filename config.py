import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")  # Set a strong value in deployment environment variables.
    # MySQL by default. Override with the DATABASE_URL environment variable.
    # Render and other providers may supply postgres:// or postgresql:// URLs.
    # Use the installed psycopg 3 driver explicitly for PostgreSQL deployments.
    _database_url = os.environ.get(
        "DATABASE_URL",
        "mysql+pymysql://hms_user:hms_pass@localhost:3306/hospital_db?charset=utf8mb4",
    )
    if _database_url.startswith("postgres://"):
        _database_url = _database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif _database_url.startswith("postgresql://"):
        _database_url = _database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    CSRF_ENABLED = True
    SLOT_MINUTES = 30          # length of one appointment slot
    CURRENCY = "₹"
    LOGIN_MAX_ATTEMPTS = 5     # failed logins before temporary lockout
    LOGIN_LOCK_SECONDS = 300


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    CSRF_ENABLED = False
    SECRET_KEY = "test"
