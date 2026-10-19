from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, update

from app.models import Payment


@pytest.mark.parametrize("source", ["pending", "succeeded", "failed", "refunded"])
@pytest.mark.parametrize("target", ["pending", "succeeded", "failed", "refunded"])
def test_all_status_transitions(client, payment, engine, source, target, post_webhook):
    with engine.begin() as connection:
        connection.execute(update(Payment).where(Payment.id == UUID(payment["id"])).values(status=source))
    before = client.get(f"/payments/{payment['id']}").json()
    response = post_webhook({"payment_id": payment["id"], "status": target})
    after = client.get(f"/payments/{payment['id']}").json()
    allowed = {
        ("pending", "succeeded"), ("pending", "failed"), ("succeeded", "refunded"),
    }
    if (source, target) in allowed:
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
        assert after == {**before, "status": target}
    else:
        assert response.status_code == 409
        assert response.json() == {"error": "invalid_transition"}
        assert after == before


def test_success_then_refund(client, payment, post_webhook):
    for status in ("succeeded", "refunded"):
        response = post_webhook({"payment_id": payment["id"], "status": status})
        assert response.status_code == 200
    assert client.get(f"/payments/{payment['id']}").json()["status"] == "refunded"


def test_unknown_webhook_payment(post_webhook):
    response = post_webhook({"payment_id": str(uuid4()), "status": "succeeded"})
    assert response.status_code == 404


@pytest.mark.parametrize("changes", [{"status": "unknown"}, {"payment_id": "bad"}])
def test_invalid_webhook(client, payment, changes, post_webhook):
    response = post_webhook({
        "payment_id": payment["id"], "status": "succeeded", **changes,
    })
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert client.get(f"/payments/{payment['id']}").json() == payment


def test_concurrent_conflicting_webhooks(client, payment, engine, post_webhook):
    select_barrier = Barrier(2)

    def before_lock(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            select_barrier.wait(timeout=15)

    def send_request(target):
        return target, post_webhook({"payment_id": payment["id"], "status": target})

    event.listen(engine, "before_cursor_execute", before_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(send_request, ["succeeded", "failed"]))
    finally:
        event.remove(engine, "before_cursor_execute", before_lock)

    assert sorted(response.status_code for _, response in results) == [200, 409]
    winner = next(target for target, response in results if response.status_code == 200)
    loser = next(response for _, response in results if response.status_code == 409)
    assert loser.json() == {"error": "invalid_transition"}
    assert client.get(f"/payments/{payment['id']}").json() == {**payment, "status": winner}


def test_webhook_rolls_back_after_executed_update(client, payment, engine, post_webhook):
    executed = []
    payload = {"payment_id": payment["id"], "status": "succeeded"}

    def fail_after_update(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE payments"):
            executed.append(statement)
            raise RuntimeError("Injected failure after payment UPDATE")

    event.listen(engine, "after_cursor_execute", fail_after_update)
    try:
        with pytest.raises(RuntimeError, match="Injected failure after payment UPDATE"):
            post_webhook(payload)
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_update)

    assert len(executed) == 1
    assert client.get(f"/payments/{payment['id']}").json() == payment
    assert engine.pool.checkedout() == 0
    retry = post_webhook(payload)
    assert retry.status_code == 200
    assert retry.json() == {"result": "ok"}
    assert client.get(f"/payments/{payment['id']}").json()["status"] == "succeeded"
