from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import config
from src.webhook_handler import router

WEBHOOK_SECRET = "whsec_endpoint_test"


def _sign(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("LEDGER_DB_PATH", str(tmp_path / "endpoint.db"))
    config._connection = None  # reset the process-wide connection cache between tests

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _payload(event_id: str) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "object": "event",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_endpoint_1", "amount": 500, "currency": "usd"}},
        }
    ).encode()


def test_webhook_endpoint_accepts_validly_signed_event(client):
    payload = _payload("evt_endpoint_ok")
    headers = {"stripe-signature": _sign(payload, WEBHOOK_SECRET)}

    resp = client.post("/webhook", content=payload, headers=headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


def test_webhook_endpoint_rejects_invalid_signature_with_400(client):
    payload = _payload("evt_endpoint_bad")
    headers = {"stripe-signature": _sign(payload, "whsec_wrong")}

    resp = client.post("/webhook", content=payload, headers=headers)

    assert resp.status_code == 400
