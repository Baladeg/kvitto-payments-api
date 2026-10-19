import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_valid_signature_covers_original_body_bytes(client, payment, sign_webhook, ending):
    # Different ordering and whitespace must not be canonicalized before HMAC.
    body = (
        '{\n  "status" : "succeeded",\n  "payment_id" : "' + payment["id"] + '"\n}'
    ).encode("utf-8") + ending
    response = client.post("/webhooks/bank", content=body, headers=sign_webhook(body))
    assert response.status_code == 200
    assert response.json() == {"result": "ok"}
    assert client.get(f"/payments/{payment['id']}").json() == {**payment, "status": "succeeded"}


@pytest.mark.parametrize("supplied", [
    None, "", "wrong", "not-a-signature", "g" * 64, "a" * 63, "a" * 65,
    "sha256=" + "a" * 64,
])
def test_invalid_signature_is_401_and_preserves_payment(client, payment, sign_webhook, supplied):
    body = json.dumps({"payment_id": payment["id"], "status": "succeeded"}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if supplied == "wrong":
        valid = sign_webhook(body)["X-Signature"]
        supplied = ("0" if valid[0] != "0" else "1") + valid[1:]
    if supplied is not None:
        headers["X-Signature"] = supplied
    response = client.post("/webhooks/bank", content=body, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid webhook signature"}
    assert client.get(f"/payments/{payment['id']}").json() == payment


def test_signature_for_other_body_is_rejected(client, payment, sign_webhook):
    body = json.dumps({"payment_id": payment["id"], "status": "succeeded"}).encode("utf-8")
    # Same JSON value, different bytes: signing a reserialized object would miss this.
    response = client.post("/webhooks/bank", content=body + b"\n", headers=sign_webhook(body))
    assert response.status_code == 401
    assert client.get(f"/payments/{payment['id']}").json() == payment


def test_uppercase_hex_signature_is_accepted(client, payment, sign_webhook):
    body = json.dumps({"payment_id": payment["id"], "status": "succeeded"}).encode("utf-8")
    headers = sign_webhook(body)
    headers["X-Signature"] = headers["X-Signature"].upper()
    assert client.post("/webhooks/bank", content=body, headers=headers).status_code == 200
    assert client.get(f"/payments/{payment['id']}").json()["status"] == "succeeded"


def test_duplicate_signature_headers_are_rejected(client, payment, sign_webhook):
    body = json.dumps({"payment_id": payment["id"], "status": "succeeded"}).encode("utf-8")
    signature = sign_webhook(body)["X-Signature"]
    headers = [
        ("Content-Type", "application/json"),
        ("X-Signature", signature),
        ("X-Signature", signature),
    ]
    assert client.post("/webhooks/bank", content=body, headers=headers).status_code == 401
    assert client.get(f"/payments/{payment['id']}").json() == payment


def test_non_ascii_signature_is_rejected(client, payment):
    body = json.dumps({"payment_id": payment["id"], "status": "succeeded"}).encode("utf-8")
    headers = [(b"Content-Type", b"application/json"), (b"X-Signature", b"\xff" * 64)]
    assert client.post("/webhooks/bank", content=body, headers=headers).status_code == 401
    assert client.get(f"/payments/{payment['id']}").json() == payment


def test_unsigned_malformed_json_is_rejected_before_validation(client):
    response = client.post(
        "/webhooks/bank", content=b"{broken", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401


def test_signed_malformed_json_still_returns_422(client, sign_webhook):
    body = b"{broken"
    response = client.post("/webhooks/bank", content=body, headers=sign_webhook(body))
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("secret", [None, "", "   ", "\t\n"])
def test_startup_rejects_missing_or_blank_secret(test_database_url, monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("WEBHOOK_SECRET", secret)

    def database_must_not_be_opened(*args, **kwargs):
        pytest.fail("Configuration must be rejected before initializing the database")

    monkeypatch.setattr("app.main.make_engine", database_must_not_be_opened)
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"), TestClient(create_app(test_database_url)):
        pass


@pytest.fixture(params=[
    ("", ""),
    ("/api", ""),
    ("/api", "/api"),
    ("/api/v1", ""),
    ("/api/v1", "/api/v1"),
])
def rooted_client(client, request):
    root_path, prefix = request.param
    # Reuse the session fixture's initialized app without running a second lifespan.
    with closing(TestClient(client.app, root_path=root_path)) as test_client:
        yield test_client, prefix


@pytest.mark.parametrize("supplied", ["missing", "wrong", "malformed", "valid"])
def test_root_path_signature_protects_payment(client, payment, sign_webhook, rooted_client, supplied):
    test_client, prefix = rooted_client
    body = (
        '{\n "status": "succeeded", "payment_id": "' + payment["id"] + '"\n}\n'
    ).encode("utf-8")
    headers = sign_webhook(body)
    if supplied == "missing":
        del headers["X-Signature"]
    elif supplied == "wrong":
        signature = headers["X-Signature"]
        headers["X-Signature"] = ("0" if signature[0] != "0" else "1") + signature[1:]
    elif supplied == "malformed":
        headers["X-Signature"] = "g" * 64

    response = test_client.post(prefix + "/webhooks/bank", content=body, headers=headers)
    after = client.get(f"/payments/{payment['id']}").json()
    if supplied == "valid":
        assert response.status_code == 200
        assert response.json() == {"result": "ok"}
        assert after == {**payment, "status": "succeeded"}
    else:
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid webhook signature"}
        assert after == payment


@pytest.mark.parametrize("supplied", ["missing", "wrong", "valid"])
def test_root_path_authenticates_before_json_parsing(
    client, payment, sign_webhook, rooted_client, supplied
):
    test_client, prefix = rooted_client
    body = b"{broken"
    headers = sign_webhook(body)
    if supplied == "missing":
        del headers["X-Signature"]
    elif supplied == "wrong":
        signature = headers["X-Signature"]
        headers["X-Signature"] = ("0" if signature[0] != "0" else "1") + signature[1:]

    response = test_client.post(prefix + "/webhooks/bank", content=body, headers=headers)
    assert response.status_code == (422 if supplied == "valid" else 401)
    if supplied == "valid":
        assert isinstance(response.json()["detail"], list)
    else:
        assert response.json() == {"detail": "Invalid webhook signature"}
    assert client.get(f"/payments/{payment['id']}").json() == payment


@pytest.mark.parametrize("root_path, path", [
    ("", "/apix/webhooks/bank"),
    ("/api", "/apix/webhooks/bank"),
    ("/api/v1", "/api/v1x/webhooks/bank"),
    ("/api/v1", "/apix/v1/webhooks/bank"),
    ("/api", "/api/webhooks/bankx"),
    ("/api", "/api/webhooks/bank/extra"),
])
@pytest.mark.parametrize("signed", [False, True])
def test_similar_prefix_does_not_match_webhook(
    client, payment, sign_webhook, root_path, path, signed
):
    body = json.dumps({"payment_id": payment["id"], "status": "succeeded"}).encode("utf-8")
    headers = sign_webhook(body) if signed else {"Content-Type": "application/json"}
    with closing(TestClient(client.app, root_path=root_path)) as test_client:
        response = test_client.post(path, content=body, headers=headers)
    assert response.status_code == 404
    assert client.get(f"/payments/{payment['id']}").json() == payment
