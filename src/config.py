"""Environment/config accessors shared by api.py and webhook_handler.py."""

from __future__ import annotations

import os
import sqlite3

from dotenv import load_dotenv

from src.ledger.models import connect, init_schema

load_dotenv()

_connection: sqlite3.Connection | None = None


def get_db_path() -> str:
    return os.environ.get("LEDGER_DB_PATH", "ledger.db")


def get_db() -> sqlite3.Connection:
    """Return a process-wide SQLite connection, creating the schema on first use."""
    global _connection
    if _connection is None:
        _connection = connect(get_db_path())
        init_schema(_connection)
    return _connection


def get_webhook_secret() -> str:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError(
            "STRIPE_WEBHOOK_SECRET is not set; copy .env.example to .env and fill it in"
        )
    return secret
