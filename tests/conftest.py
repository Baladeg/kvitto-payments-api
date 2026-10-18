import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url

from app.db import make_engine
from app.main import create_app
from app.models import Payment


@pytest.fixture(scope="session")
def test_database_url():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://kvitto:kvitto_local@127.0.0.1:5433/kvitto_test",
    )
    parsed = make_url(url)
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.database != "kvitto_test"
        or parsed.query
    ):
        raise pytest.UsageError(
            "Tests require PostgreSQL database kvitto_test without URL query options"
        )
    # Check the server's actual database before creating or clearing any tables.
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != "kvitto_test":
                raise pytest.UsageError("Refusing to modify a non-test database")
    finally:
        engine.dispose()
    return url


@pytest.fixture(scope="session")
def client(test_database_url):
    application = create_app(test_database_url)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def engine(client):
    return client.app.state.engine


@pytest.fixture(autouse=True)
def clean_payments(engine):
    with engine.begin() as connection:
        connection.execute(delete(Payment))
    yield
    with engine.begin() as connection:
        connection.execute(delete(Payment))


@pytest.fixture
def tariffs(client):
    response = client.get("/tariffs")
    assert response.status_code == 200
    return {tariff["title"]: tariff for tariff in response.json()}


@pytest.fixture
def payment_payload(tariffs):
    return {
        "tariff_id": tariffs["standard"]["id"],
        "email": "student@example.com",
        "method": "card",
    }


@pytest.fixture
def payment(client, payment_payload):
    response = client.post("/payments", json=payment_payload)
    assert response.status_code == 201
    return response.json()
