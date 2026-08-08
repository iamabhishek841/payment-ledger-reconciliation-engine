from __future__ import annotations

from pathlib import Path

import pytest

from src.idempotency import claim_event, has_processed
from src.ledger.models import connect, init_schema
from src.ledger.service import get_balance
from src.webhook_handler import REVENUE, STRIPE_RECEIVABLE, ensure_default_accounts, process_event


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(str(tmp_path / "idem.db"))
    init_schema(c)
    yield c
    c.close()


def test_claim_event_succeeds_once_and_fails_on_repeat(conn):
    assert claim_event(conn, "evt_1", "payment_intent.succeeded") is True
    assert claim_event(conn, "evt_1", "payment_intent.succeeded") is False
    assert has_processed(conn, "evt_1") is True


def _fake_payment_intent_succeeded_event(event_id: str, intent_id: str, amount: int) -> dict:
    return {
        "id": event_id,
        "type": "payment_intent.succeeded",
        "data": {"object": {"id": intent_id, "amount": amount, "currency": "usd"}},
    }


def test_processing_the_same_event_id_twice_only_posts_ledger_entries_once(conn):
    ensure_default_accounts(conn)
    event = _fake_payment_intent_succeeded_event("evt_dup", "pi_dup", 1500)

    first = process_event(conn, event)
    second = process_event(conn, event)

    assert first == "processed"
    assert second == "duplicate"

    assert get_balance(conn, STRIPE_RECEIVABLE) == -1500
    assert get_balance(conn, REVENUE) == 1500

    entry_count = conn.execute(
        "SELECT COUNT(*) AS c FROM entries WHERE stripe_event_id = ?", ("evt_dup",)
    ).fetchone()["c"]
    assert entry_count == 2  # exactly one debit + one credit leg, not four
