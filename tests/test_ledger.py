from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from src.ledger.models import Account, EntryType, connect, init_schema
from src.ledger.service import (
    Leg,
    UnbalancedTransactionError,
    create_account,
    get_balance,
    post_transaction,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_ledger.db")


@pytest.fixture
def conn(db_path):
    c = connect(db_path)
    init_schema(c)
    create_account(c, Account("cash", "Cash", "asset"))
    create_account(c, Account("revenue", "Revenue", "revenue"))
    yield c
    c.close()


def test_balanced_transaction_posts_successfully(conn):
    txn_id = post_transaction(
        conn,
        legs=[
            Leg("cash", EntryType.DEBIT, 1000),
            Leg("revenue", EntryType.CREDIT, 1000),
        ],
    )
    assert get_balance(conn, "cash") == -1000
    assert get_balance(conn, "revenue") == 1000
    assert txn_id


def test_unbalanced_transaction_is_rejected_before_any_write(conn):
    with pytest.raises(UnbalancedTransactionError):
        post_transaction(
            conn,
            legs=[
                Leg("cash", EntryType.DEBIT, 1000),
                Leg("revenue", EntryType.CREDIT, 999),
            ],
        )
    # Nothing should have been written for the rejected transaction.
    assert get_balance(conn, "cash") == 0
    assert get_balance(conn, "revenue") == 0


def test_every_transaction_nets_to_zero_across_many_postings(conn):
    for i in range(25):
        post_transaction(
            conn,
            legs=[
                Leg("cash", EntryType.DEBIT, 100 + i),
                Leg("revenue", EntryType.CREDIT, 100 + i),
            ],
        )
    cash_balance = get_balance(conn, "cash")
    revenue_balance = get_balance(conn, "revenue")
    # Debits and credits across the whole ledger must still net to zero.
    assert cash_balance + revenue_balance == 0


def test_get_balance_is_computed_not_cached(conn):
    post_transaction(
        conn,
        legs=[Leg("cash", EntryType.DEBIT, 500), Leg("revenue", EntryType.CREDIT, 500)],
    )
    assert get_balance(conn, "cash") == -500
    post_transaction(
        conn,
        legs=[Leg("cash", EntryType.DEBIT, 250), Leg("revenue", EntryType.CREDIT, 250)],
    )
    # A second post must be reflected immediately since balance is a live SUM.
    assert get_balance(conn, "cash") == -750


def _post_one(db_path: str, i: int) -> str:
    """Open a fresh connection (simulating a separate process/worker) and post."""
    c = connect(db_path)
    try:
        return post_transaction(
            c,
            legs=[
                Leg("cash", EntryType.DEBIT, 10),
                Leg("revenue", EntryType.CREDIT, 10),
            ],
        )
    finally:
        c.close()


def test_concurrent_posts_do_not_corrupt_balances(db_path):
    setup_conn = connect(db_path)
    init_schema(setup_conn)
    create_account(setup_conn, Account("cash", "Cash", "asset"))
    create_account(setup_conn, Account("revenue", "Revenue", "revenue"))
    setup_conn.close()

    n_workers = 16
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
        txn_ids = list(pool.map(lambda i: _post_one(db_path, i), range(n_workers)))

    assert len(set(txn_ids)) == n_workers  # every post produced a distinct transaction

    verify_conn = connect(db_path)
    try:
        cash_balance = get_balance(verify_conn, "cash")
        revenue_balance = get_balance(verify_conn, "revenue")
        assert cash_balance == -10 * n_workers
        assert revenue_balance == 10 * n_workers
        assert cash_balance + revenue_balance == 0

        entry_count = verify_conn.execute("SELECT COUNT(*) AS c FROM entries").fetchone()["c"]
        assert entry_count == 2 * n_workers  # exactly two legs per transaction, none lost/duplicated
    finally:
        verify_conn.close()
