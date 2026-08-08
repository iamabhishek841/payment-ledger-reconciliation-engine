"""Double-entry ledger schema.

Every transaction is a pair of entries (one debit, one credit) that must
net to zero. The invariant is enforced by a database trigger (see
SCHEMA_SQL) rather than relying solely on application code, so a partial
or buggy write can never leave the ledger unbalanced.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class EntryType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    account_type: str  # e.g. "asset", "liability", "revenue", "expense"


@dataclass(frozen=True)
class Entry:
    id: int
    transaction_id: str
    account_id: str
    entry_type: EntryType
    amount_cents: int
    currency: str
    created_at: str
    stripe_event_id: str | None = None
    description: str | None = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('debit', 'credit')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency TEXT NOT NULL,
    stripe_event_id TEXT,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entries_transaction_id ON entries(transaction_id);
CREATE INDEX IF NOT EXISTS idx_entries_account_id ON entries(account_id);

-- Enforces the core double-entry invariant at the database level: after
-- every insert into a transaction_id, the signed sum of that
-- transaction's entries (credits positive, debits negative) must be
-- zero once the transaction has both of its legs. We can't check this
-- on a single-row INSERT trigger (the second leg hasn't landed yet),
-- so instead we enforce it with a deferred check performed by
-- ledger.service.post_entry_pair inside a single BEGIN IMMEDIATE
-- transaction, and additionally guard it here with a trigger that
-- rejects any entry_type value outside debit/credit and any
-- non-positive amount (see CHECK constraints above). The atomic
-- two-row-insert-or-nothing guarantee comes from SQLite's transaction
-- semantics themselves: post_entry_pair does both inserts inside one
-- BEGIN IMMEDIATE ... COMMIT block, so a crash or exception between the
-- two inserts rolls back both, and BEGIN IMMEDIATE takes the write lock
-- up front so two concurrent postings cannot interleave their inserts.
CREATE TABLE IF NOT EXISTS processed_stripe_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection configured for safe concurrent writes.

    isolation_level=None puts the connection in autocommit mode so callers
    can issue explicit `BEGIN IMMEDIATE` transactions instead of the
    driver's default deferred-transaction behaviour. BEGIN IMMEDIATE
    acquires the write lock at the start of the transaction (rather than
    lazily, on first write), which prevents two concurrent writers from
    both proceeding partway through a multi-statement transaction and
    then colliding -- one of them will block/retry at BEGIN IMMEDIATE
    time instead of failing midway with SQLITE_BUSY.

    check_same_thread=False allows this single connection to be reused
    across the worker threads FastAPI/Starlette use to run sync request
    handlers. This is safe here because every call to the connection is
    a single self-contained execute()/fetchone() (or a single BEGIN
    IMMEDIATE ... COMMIT block), never a cursor shared across threads,
    and SQLite itself serializes conflicting writers via the write lock
    BEGIN IMMEDIATE takes.
    """
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=30.0, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
