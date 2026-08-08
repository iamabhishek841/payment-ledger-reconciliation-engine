"""End-to-end integration test against the real Stripe test-mode API.

Skipped automatically (not failed) when STRIPE_SECRET_KEY isn't set, e.g.
in CI, so this only runs where a developer has configured .env locally.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRIPE_SECRET_KEY"),
    reason="STRIPE_SECRET_KEY not set; skipping live Stripe test-mode integration test",
)


@pytest.fixture
def conn(tmp_path: Path):
    from src.ledger.models import connect, init_schema

    c = connect(str(tmp_path / "integration.db"))
    init_schema(c)
    yield c
    c.close()


def test_create_payment_intent_and_apply_simulated_webhook_updates_ledger(conn):
    from src.ledger.service import get_balance
    from src.stripe_client import create_payment_intent
    from src.webhook_handler import REVENUE, STRIPE_RECEIVABLE, ensure_default_accounts, process_event

    ensure_default_accounts(conn)

    intent = create_payment_intent(amount_cents=2000, currency="usd", payment_method_types=["card"])
    assert intent["status"] in ("requires_payment_method", "requires_confirmation", "requires_action")

    # We don't actually confirm the PaymentIntent with a real card here (that
    # requires a client-side confirm step); instead we simulate the webhook
    # Stripe would send once a succeeded confirmation happens, using the real
    # PaymentIntent id, to verify the ledger side of the pipeline end-to-end.
    simulated_event = {
        "id": f"evt_test_{intent['id']}",
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": intent["id"], "amount": 2000, "currency": "usd"}},
    }

    status = process_event(conn, simulated_event)

    assert status == "processed"
    assert get_balance(conn, STRIPE_RECEIVABLE) == -2000
    assert get_balance(conn, REVENUE) == 2000
