import os

from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://kvitto:kvitto_local@127.0.0.1:5433/kvitto"
)


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    if make_url(database_url).drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL must use postgresql+psycopg")
    return database_url


def get_webhook_secret() -> bytes:
    secret = os.getenv("WEBHOOK_SECRET")
    if secret is None or not secret.strip():
        raise ValueError("WEBHOOK_SECRET must be set to a non-empty secret")
    return secret.encode("utf-8")
