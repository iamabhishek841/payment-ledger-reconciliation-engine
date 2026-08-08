"""Webhook signature verification tests.

We build the `Stripe-Signature` header the same way Stripe's own servers
do (t=<timestamp>,v1=<hmac-sha256 of "timestamp.payload">) rather than
relying on any private SDK test helper, so this test exercises exactly
the verification path `stripe.Webhook.construct_event` performs in
production.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
import stripe

from src.stripe_client import construct_webhook_event

WEBHOOK_SECRET = "whsec_test_secret_123"


def _sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    timestamp = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(
        secret.encode(), signed_payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _sample_payload() -> bytes:
    return json.dumps(
        {
            "id": "evt_test_123",
            "object": "event",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_test_123", "amount": 1000, "currency": "usd"}},
        }
    ).encode()


def test_valid_signature_is_accepted():
    payload = _sample_payload()
    sig_header = _sign(payload, WEBHOOK_SECRET)

    event = construct_webhook_event(payload, sig_header, WEBHOOK_SECRET)

    assert event["id"] == "evt_test_123"
    assert event["type"] == "payment_intent.succeeded"


def test_invalid_signature_is_rejected():
    payload = _sample_payload()
    bad_sig_header = _sign(payload, "whsec_wrong_secret")

    with pytest.raises(stripe.SignatureVerificationError):
        construct_webhook_event(payload, bad_sig_header, WEBHOOK_SECRET)


def test_tampered_payload_is_rejected():
    payload = _sample_payload()
    sig_header = _sign(payload, WEBHOOK_SECRET)
    tampered_payload = payload.replace(b"1000", b"999999")

    with pytest.raises(stripe.SignatureVerificationError):
        construct_webhook_event(tampered_payload, sig_header, WEBHOOK_SECRET)
