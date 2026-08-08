from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import config


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_api_test")
    monkeypatch.setenv("LEDGER_DB_PATH", str(tmp_path / "api.db"))
    config._connection = None

    from src.api import app

    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ledger_balance_endpoint_returns_zero_for_fresh_account(client):
    resp = client.get("/ledger/revenue/balance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == "revenue"
    assert body["balance_cents"] == 0


def test_create_payment_without_stripe_key_returns_503(client, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    resp = client.post("/create-payment", json={"amount_cents": 500})
    assert resp.status_code == 503


def test_create_payment_rejects_non_positive_amount(client):
    resp = client.post("/create-payment", json={"amount_cents": 0})
    assert resp.status_code == 400
