"""Idempotency guard for Stripe webhook events.

Stripe can and does redeliver webhooks (at-least-once delivery), so the
webhook handler must be safe to invoke twice with the same event. The
`processed_stripe_events.event_id` column has a PRIMARY KEY constraint
(see ledger/models.py SCHEMA_SQL), so uniqueness is enforced by SQLite
itself -- not just by an in-memory set -- and holds even under
concurrent delivery of the same event to multiple worker processes.
"""

from __future__ import annotations

import sqlite3

from src.ledger.models import utc_now_iso


class DuplicateEventError(Exception):
    """Raised when an event_id has already been recorded as processed."""


def claim_event(conn: sqlite3.Connection, event_id: str, event_type: str) -> bool:
    """Attempt to claim an event_id as newly-processed.

    Returns True if this call is the first to see event_id (caller should
    proceed to apply side effects). Returns False if the event_id was
    already claimed (caller should short-circuit and return success
    without reprocessing). Relies on the PRIMARY KEY unique constraint on
    processed_stripe_events.event_id to make the claim atomic even under
    concurrent webhook delivery.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO processed_stripe_events (event_id, event_type, processed_at) "
            "VALUES (?, ?, ?)",
            (event_id, event_type, utc_now_iso()),
        )
        conn.execute("COMMIT")
        return True
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return False
    except Exception:
        conn.execute("ROLLBACK")
        raise


def has_processed(conn: sqlite3.Connection, event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM processed_stripe_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    return row is not None
