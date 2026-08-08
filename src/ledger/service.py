"""Ledger operations: posting balanced entries and computing balances.

The core invariant -- every transaction's debits equal its credits -- is
enforced two ways:

1. `post_transaction` only ever accepts a *list* of legs and asserts in
   Python that debits == credits before touching the database, so a
   caller can never even attempt an unbalanced post.
2. The insert itself happens inside a single `BEGIN IMMEDIATE` ...
   `COMMIT` transaction. `BEGIN IMMEDIATE` takes SQLite's write lock
   immediately (rather than on first write), so a second concurrent
   writer blocks until the first transaction fully commits or rolls
   back -- there is no window where one transaction's legs are visible
   without the other, and a crash partway through rolls back every leg
   written so far. This is what "at the database-transaction level"
   means here: the atomicity is a property of the SQLite transaction,
   not of application-level bookkeeping.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass

from src.ledger.models import Account, EntryType, utc_now_iso


class UnbalancedTransactionError(ValueError):
    pass


@dataclass(frozen=True)
class Leg:
    account_id: str
    entry_type: EntryType
    amount_cents: int
    currency: str = "usd"
    description: str | None = None


def create_account(conn: sqlite3.Connection, account: Account) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO accounts (id, name, account_type) VALUES (?, ?, ?)",
        (account.id, account.name, account.account_type),
    )


def _begin_immediate_with_retry(
    conn: sqlite3.Connection, max_attempts: int = 5
) -> None:
    """BEGIN IMMEDIATE, retrying briefly on SQLITE_BUSY from a concurrent writer."""
    attempt = 0
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError:
            attempt += 1
            if attempt >= max_attempts:
                raise
            time.sleep(0.05 * attempt)


def post_transaction(
    conn: sqlite3.Connection,
    legs: list[Leg],
    transaction_id: str | None = None,
    stripe_event_id: str | None = None,
) -> str:
    """Post a balanced set of ledger legs atomically.

    Raises UnbalancedTransactionError if the legs' debits and credits
    don't net to zero -- checked before any database write occurs.
    """
    if len(legs) < 2:
        raise UnbalancedTransactionError("a transaction needs at least two legs")

    debits = sum(leg.amount_cents for leg in legs if leg.entry_type == EntryType.DEBIT)
    credits = sum(
        leg.amount_cents for leg in legs if leg.entry_type == EntryType.CREDIT
    )
    if debits != credits:
        raise UnbalancedTransactionError(
            f"debits ({debits}) != credits ({credits}); transaction not balanced"
        )

    txn_id = transaction_id or str(uuid.uuid4())
    now = utc_now_iso()

    _begin_immediate_with_retry(conn)
    try:
        for leg in legs:
            conn.execute(
                """
                INSERT INTO entries
                    (transaction_id, account_id, entry_type, amount_cents,
                     currency, stripe_event_id, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    txn_id,
                    leg.account_id,
                    leg.entry_type.value,
                    leg.amount_cents,
                    leg.currency,
                    stripe_event_id,
                    leg.description,
                    now,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return txn_id


def get_balance(conn: sqlite3.Connection, account_id: str) -> int:
    """Compute an account's balance from summed entries (never a cached field).

    Convention: credits increase the balance, debits decrease it. This
    matches the mental model for a liability/revenue-style "amount owed
    to the merchant" account, which is how this project uses accounts
    (see webhook_handler.py). Computing this from a SUM over `entries`
    on every call -- rather than maintaining a mutable running-balance
    column -- means the balance can never drift out of sync with the
    entries that are supposed to justify it.
    """
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN entry_type = 'credit' THEN amount_cents ELSE 0 END), 0)
            - COALESCE(SUM(CASE WHEN entry_type = 'debit' THEN amount_cents ELSE 0 END), 0)
            AS balance
        FROM entries
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    return int(row["balance"])


def get_entries_for_transaction(conn: sqlite3.Connection, transaction_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries WHERE transaction_id = ? ORDER BY id",
        (transaction_id,),
    ).fetchall()


def get_entries_by_stripe_event(conn: sqlite3.Connection, stripe_event_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries WHERE stripe_event_id = ? ORDER BY id",
        (stripe_event_id,),
    ).fetchall()


def list_entries(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM entries ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


__all__ = [
    "Leg",
    "UnbalancedTransactionError",
    "create_account",
    "get_balance",
    "get_entries_by_stripe_event",
    "get_entries_for_transaction",
    "list_entries",
    "post_transaction",
]
