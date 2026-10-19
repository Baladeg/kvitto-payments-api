import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, insert, select, text

from app.models import Payment


def payment_count(engine):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(Payment))


@pytest.mark.parametrize("title", ["basic", "standard", "premium"])
@pytest.mark.parametrize("promo_code", [None, "KVITTO10", "kvitto10", "KvItTo10"])
def test_amount_and_full_payment_contract(client, tariffs, title, promo_code):
    tariff = tariffs[title]
    payload = {"tariff_id": tariff["id"], "email": "student@example.com", "method": "card"}
    if promo_code is not None:
        payload["promo_code"] = promo_code
    response = client.post("/payments", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "id", "status", "tariff_id", "amount", "discount", "method",
        "installment_months", "schedule", "email", "created_at",
    }
    assert UUID(body["id"])
    assert body["status"] == "pending"
    assert body["tariff_id"] == tariff["id"]
    expected_discount = tariff["price"] // 10 if promo_code else 0
    assert body["discount"] == expected_discount
    assert body["amount"] == tariff["price"] - expected_discount
    assert type(body["amount"]) is int
    assert type(body["discount"]) is int
    assert body["method"] == "card"
    assert body["installment_months"] is None
    assert body["schedule"] is None
    assert body["email"] == payload["email"]
    timestamp = datetime.fromisoformat(body["created_at"])
    assert timestamp.utcoffset().total_seconds() == 0
    assert client.get(f"/payments/{body['id']}").json() == body


@pytest.mark.parametrize("title", ["basic", "standard", "premium"])
@pytest.mark.parametrize("months", [3, 6, 12])
@pytest.mark.parametrize("promo_code", [None, "KVITTO10"])
def test_installment_schedule(client, tariffs, title, months, promo_code):
    payload = {
        "tariff_id": tariffs[title]["id"], "email": "student@example.com",
        "method": "installment", "installment_months": months, "promo_code": promo_code,
    }
    response = client.post("/payments", json=payload)
    assert response.status_code == 201
    body = response.json()
    schedule = body["schedule"]
    assert body["installment_months"] == months
    assert len(schedule) == months
    assert all(type(amount) is int for amount in schedule)
    assert sum(schedule) == body["amount"]
    assert schedule == sorted(schedule, reverse=True)
    assert max(schedule) - min(schedule) <= 1
    if title == "standard" and months == 3 and promo_code is None:
        assert schedule == [663_334, 663_333, 663_333]


def test_sbp(client, payment_payload):
    response = client.post("/payments", json={**payment_payload, "method": "sbp"})
    assert response.status_code == 201
    assert response.json()["method"] == "sbp"
    assert response.json()["schedule"] is None
    assert response.json()["installment_months"] is None


@pytest.mark.parametrize("changes", [
    {"promo_code": "UNKNOWN"}, {"promo_code": " KVITTO10 "}, {"promo_code": ""},
    {"email": "invalid"}, {"method": "cash"}, {"tariff_id": 0},
    {"tariff_id": True}, {"tariff_id": 1.0}, {"tariff_id": "1"},
    {"tariff_id": 2_147_483_648},
    {"method": "installment"}, {"method": "installment", "installment_months": None},
    {"method": "installment", "installment_months": 4},
    {"method": "installment", "installment_months": "3"},
    {"method": "installment", "installment_months": 3.0},
    {"method": "installment", "installment_months": True},
    {"method": "card", "installment_months": 3},
    {"method": "sbp", "installment_months": 6},
])
def test_validation_is_standard_422(client, engine, payment_payload, changes):
    response = client.post("/payments", json={**payment_payload, **changes})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert payment_count(engine) == 0


@pytest.mark.parametrize("field, raw_value, expected_input, error_type", [
    ("installment_months", "1e309", "inf", "int_type"),
    ("installment_months", "-1e309", "-inf", "int_type"),
    ("installment_months", "NaN", "nan", "int_type"),
    ("promo_code", r'"\ud800"', "\ud800", "value_error"),
    ("promo_code", r'"\udfff"', "\udfff", "value_error"),
])
def test_raw_json_validation_errors_are_serializable(
    client, engine, payment_payload, field, raw_value, expected_input, error_type
):
    payload = {**payment_payload}
    if field == "installment_months":
        payload["method"] = "installment"
    # Keep the raw numeric token / Unicode escape intact instead of using json=.
    raw_body = json.dumps(payload)[:-1] + f', "{field}": {raw_value}' + "}"
    response = client.post(
        "/payments", content=raw_body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"detail"}
    assert len(body["detail"]) == 1
    error = body["detail"][0]
    assert error["loc"] == ["body", field]
    assert error["type"] == error_type
    assert isinstance(error["msg"], str)
    assert error["input"] == expected_input
    assert payment_count(engine) == 0


def test_payment_rolls_back_after_executed_insert(client, payment_payload, engine):
    executed = []
    headers = {"Idempotency-Key": "retry-after-insert-rollback"}

    def fail_after_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO payments"):
            executed.append(statement)
            raise RuntimeError("Injected failure after payment INSERT")

    event.listen(engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="Injected failure after payment INSERT"):
            client.post("/payments", json=payment_payload, headers=headers)
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_insert)

    assert len(executed) == 1
    assert payment_count(engine) == 0
    assert engine.pool.checkedout() == 0
    retry = client.post("/payments", json=payment_payload, headers=headers)
    assert retry.status_code == 201
    assert retry.json()["status"] == "pending"
    assert payment_count(engine) == 1


def test_unknown_tariff(client, payment_payload, engine):
    response = client.post("/payments", json={**payment_payload, "tariff_id": 2_147_483_647})
    assert response.status_code == 404
    assert payment_count(engine) == 0


def test_sequential_idempotency_and_changed_body(client, payment_payload, engine):
    headers = {"Idempotency-Key": "same-request"}
    first = client.post("/payments", json=payment_payload, headers=headers)
    second = client.post("/payments", json=payment_payload, headers=headers)
    changed = client.post("/payments", json={
        **payment_payload, "tariff_id": 2_147_483_647, "email": "other@example.com",
        "method": "installment", "installment_months": 12, "promo_code": "kvitto10",
    }, headers=headers)
    assert first.status_code == 201
    assert second.status_code == changed.status_code == 200
    assert first.json() == second.json() == changed.json()
    assert payment_count(engine) == 1


def test_replay_returns_current_status(client, payment_payload, post_webhook):
    headers = {"Idempotency-Key": "updated-payment"}
    first = client.post("/payments", json=payment_payload, headers=headers).json()
    assert post_webhook({
        "payment_id": first["id"], "status": "succeeded",
    }).status_code == 200
    replay = client.post("/payments", json=payment_payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["id"] == first["id"]
    assert replay.json()["status"] == "succeeded"


def test_invalid_replay_still_fails_validation(client, payment_payload, engine):
    headers = {"Idempotency-Key": "existing-key"}
    assert client.post("/payments", json=payment_payload, headers=headers).status_code == 201
    response = client.post("/payments", json={**payment_payload, "promo_code": "BAD"}, headers=headers)
    assert response.status_code == 422
    assert payment_count(engine) == 1


def test_validation_failure_does_not_consume_key(client, payment_payload, engine):
    headers = {"Idempotency-Key": "retry-after-error"}
    invalid = client.post("/payments", json={**payment_payload, "email": "bad"}, headers=headers)
    assert invalid.status_code == 422
    assert payment_count(engine) == 0
    assert client.post("/payments", json=payment_payload, headers=headers).status_code == 201
    assert payment_count(engine) == 1


@pytest.mark.parametrize("key", ["", "x" * 256])
def test_invalid_key(client, payment_payload, engine, key):
    response = client.post("/payments", json=payment_payload, headers={"Idempotency-Key": key})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert payment_count(engine) == 0


def test_concurrent_idempotency_forces_insert_race(client, payment_payload, engine):
    workers = 6
    insert_barrier = Barrier(workers)

    def before_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO payments"):
            # Force every request past its initial lookup before any INSERT runs.
            insert_barrier.wait(timeout=15)

    def send_request(index):
        return client.post(
            "/payments", json={**payment_payload, "email": f"student{index}@example.com"},
            headers={"Idempotency-Key": "concurrent-key"},
        )

    event.listen(engine, "before_cursor_execute", before_insert)
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            responses = list(executor.map(send_request, range(workers)))
    finally:
        event.remove(engine, "before_cursor_execute", before_insert)

    assert sorted(response.status_code for response in responses) == [200] * 5 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert all(response.json() == responses[0].json() for response in responses)
    assert payment_count(engine) == 1


def test_concurrent_idempotency_after_first_transaction_rollback(
    client, payment_payload, engine
):
    key = "concurrent-first-transaction-rollback"
    entered_insert = Event()
    waiting_backend = []

    def before_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO payments"):
            waiting_backend.append(connection.connection.driver_connection.info.backend_pid)
            entered_insert.set()

    with engine.connect() as first_connection:
        transaction = first_connection.begin()
        first_backend = first_connection.scalar(text("SELECT pg_backend_pid()"))
        aborted_id = first_connection.scalar(insert(Payment).values(
            tariff_id=payment_payload["tariff_id"], amount=1_990_000, discount=0,
            method="card", email="aborted@example.com", idempotency_key=key,
        ).returning(Payment.id))
        event.listen(engine, "before_cursor_execute", before_insert)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    client.post, "/payments", json=payment_payload,
                    headers={"Idempotency-Key": key},
                )
                try:
                    assert entered_insert.wait(timeout=15)
                    deadline = monotonic() + 15
                    while monotonic() < deadline:
                        with engine.connect() as observer:
                            blocked = observer.scalar(text(
                                "SELECT :first = ANY(pg_blocking_pids(:waiting))"
                            ), {"first": first_backend, "waiting": waiting_backend[0]})
                        if blocked:
                            break
                        sleep(0.02)
                    else:
                        pytest.fail("Second INSERT did not wait for the first transaction")
                finally:
                    # Release the first transaction even if an assertion fails.
                    transaction.rollback()
                response = future.result(timeout=15)
        finally:
            event.remove(engine, "before_cursor_execute", before_insert)
            if transaction.is_active:
                transaction.rollback()

    assert response.status_code == 201
    payment = response.json()
    assert payment["id"] != str(aborted_id)
    assert payment["email"] == payment_payload["email"]
    assert payment_count(engine) == 1
    assert client.get(f"/payments/{aborted_id}").status_code == 404
    replay = client.post(
        "/payments", json=payment_payload, headers={"Idempotency-Key": key}
    )
    assert replay.status_code == 200
    assert replay.json() == payment


def test_without_key_creates_separate_payments(client, payment_payload, engine):
    first = client.post("/payments", json=payment_payload)
    second = client.post("/payments", json=payment_payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert payment_count(engine) == 2


def test_keys_are_case_sensitive(client, payment_payload, engine):
    for key in ("Key", "key"):
        assert client.post("/payments", json=payment_payload, headers={
            "Idempotency-Key": key,
        }).status_code == 201
    assert payment_count(engine) == 2


def test_unknown_payment(client):
    assert client.get(f"/payments/{uuid4()}").status_code == 404


def test_invalid_payment_id(client):
    assert client.get("/payments/not-a-uuid").status_code == 422
