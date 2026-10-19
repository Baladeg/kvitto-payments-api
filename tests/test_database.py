from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import Payment, Tariff


@pytest.mark.parametrize("changes", [
    {"amount": 0}, {"amount": -1}, {"discount": -1}, {"status": "unknown"},
    {"method": "cash"}, {"method": "installment", "installment_months": None},
    {"method": "installment", "installment_months": 4},
    {"method": "card", "installment_months": 3},
    {"tariff_id": 2_147_483_647}, {"idempotency_key": ""},
])
def test_postgresql_rejects_invalid_payment(engine, tariffs, changes):
    values = {
        "id": uuid4(), "tariff_id": tariffs["standard"]["id"], "amount": 1_990_000,
        "discount": 0, "status": "pending", "method": "card", "installment_months": None,
        "email": "student@example.com", **changes,
    }
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(Payment.__table__.insert().values(**values))


def test_postgresql_unique_idempotency_key(engine, tariffs):
    values = {
        "tariff_id": tariffs["standard"]["id"], "amount": 1_990_000, "discount": 0,
        "method": "card", "email": "student@example.com", "idempotency_key": "unique-key",
    }
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(Payment.__table__.insert().values(**values))
        connection.execute(Payment.__table__.insert().values(**values))


def test_postgresql_rejects_nonpositive_tariff(engine):
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(Tariff.__table__.insert().values(title="invalid", price=0))


def test_actual_database_and_money_column_types(engine):
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == "kvitto_test"
    inspector = inspect(engine)
    for table, fields in (("payments", {"amount", "discount"}), ("tariffs", {"price"})):
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert all(isinstance(columns[field]["type"], BigInteger) for field in fields)
