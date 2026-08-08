"""Reconciliation: compare Stripe's PaymentIntents against the local ledger.

For each recent PaymentIntent pulled from Stripe, we look for the
corresponding ledger transaction (transaction_id == payment_intent.id)
and flag any of:
  - missing_local_entry : Stripe has it, ledger doesn't
  - amount_mismatch      : ledger transaction total != Stripe amount
  - status_mismatch      : Stripe status implies ledger effects that
                            aren't present (e.g. succeeded but no
                            corresponding stripe_receivable/revenue entries)

Output is a structured JSON report, not just log lines, so it can be
consumed by the API/dashboard or archived for audit purposes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from src.ledger.models import utc_now_iso
from src.ledger.service import get_entries_for_transaction
from src.stripe_client import list_recent_payment_intents
from src.webhook_handler import REVENUE, STRIPE_RECEIVABLE


@dataclass
class ReconciliationMismatch:
    payment_intent_id: str
    mismatch_type: str  # missing_local_entry | amount_mismatch | status_mismatch
    stripe_amount: int
    stripe_status: str
    local_amount: int | None
    detail: str


@dataclass
class ReconciliationReport:
    generated_at: str
    total_checked: int
    matched_count: int
    mismatch_count: int
    mismatches: list[ReconciliationMismatch]

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "total_checked": self.total_checked,
            "matched_count": self.matched_count,
            "mismatch_count": self.mismatch_count,
            "mismatches": [asdict(m) for m in self.mismatches],
        }


def _local_amount_for_succeeded(conn: sqlite3.Connection, payment_intent_id: str) -> int | None:
    entries = get_entries_for_transaction(conn, payment_intent_id)
    if not entries:
        return None
    receivable_debits = sum(
        e["amount_cents"]
        for e in entries
        if e["account_id"] == STRIPE_RECEIVABLE and e["entry_type"] == "debit"
    )
    revenue_credits = sum(
        e["amount_cents"]
        for e in entries
        if e["account_id"] == REVENUE and e["entry_type"] == "credit"
    )
    if receivable_debits != revenue_credits:
        return None
    return receivable_debits


def reconcile(conn: sqlite3.Connection, limit: int = 20) -> ReconciliationReport:
    payment_intents = list_recent_payment_intents(limit=limit)
    mismatches: list[ReconciliationMismatch] = []
    matched = 0

    for intent in payment_intents:
        stripe_amount = int(intent["amount"])
        stripe_status = intent["status"]
        entries = get_entries_for_transaction(conn, intent["id"])

        if stripe_status != "succeeded":
            # We only expect ledger entries once a PaymentIntent succeeds;
            # anything else (requires_payment_method, canceled, etc.) has
            # no local counterpart to compare, so it's not a mismatch.
            matched += 1
            continue

        if not entries:
            mismatches.append(
                ReconciliationMismatch(
                    payment_intent_id=intent["id"],
                    mismatch_type="missing_local_entry",
                    stripe_amount=stripe_amount,
                    stripe_status=stripe_status,
                    local_amount=None,
                    detail="Stripe reports this PaymentIntent as succeeded but no "
                    "ledger transaction exists for it (webhook may have been missed).",
                )
            )
            continue

        local_amount = _local_amount_for_succeeded(conn, intent["id"])
        if local_amount is None:
            mismatches.append(
                ReconciliationMismatch(
                    payment_intent_id=intent["id"],
                    mismatch_type="status_mismatch",
                    stripe_amount=stripe_amount,
                    stripe_status=stripe_status,
                    local_amount=None,
                    detail="Local ledger entries exist for this transaction but don't "
                    "form a balanced stripe_receivable/revenue pair.",
                )
            )
            continue

        if local_amount != stripe_amount:
            mismatches.append(
                ReconciliationMismatch(
                    payment_intent_id=intent["id"],
                    mismatch_type="amount_mismatch",
                    stripe_amount=stripe_amount,
                    stripe_status=stripe_status,
                    local_amount=local_amount,
                    detail=f"Stripe amount {stripe_amount} != local ledger amount {local_amount}.",
                )
            )
            continue

        matched += 1

    return ReconciliationReport(
        generated_at=utc_now_iso(),
        total_checked=len(payment_intents),
        matched_count=matched,
        mismatch_count=len(mismatches),
        mismatches=mismatches,
    )


def write_report(report: ReconciliationReport, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)
