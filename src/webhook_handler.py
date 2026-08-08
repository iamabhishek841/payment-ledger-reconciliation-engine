"""FastAPI webhook endpoint for Stripe events.

Account model (see ledger/models.py for schema):
  - stripe_receivable : asset account tracking funds Stripe holds for us
  - revenue           : revenue recognized on successful payments
  - payment_failures  : memo account recording failed-payment attempts
  - suspense           : memo counterpart for payment_failures (net zero,
                          never touches real money -- a failed payment
                          moves no funds, so it can't debit/credit the
                          real asset/revenue accounts, but the spec
                          requires every event type to post a balanced
                          double-entry pair, hence this inert memo pair)

payment_intent.succeeded  -> debit stripe_receivable, credit revenue
charge.refunded           -> debit revenue, credit stripe_receivable
payment_intent.payment_failed -> debit payment_failures, credit suspense (memo only)
"""

from __future__ import annotations

import logging
import sqlite3

import stripe
from fastapi import APIRouter, HTTPException, Request

from src.idempotency import claim_event
from src.ledger.models import Account, EntryType
from src.ledger.service import Leg, create_account, post_transaction

logger = logging.getLogger("webhook_handler")

STRIPE_RECEIVABLE = "stripe_receivable"
REVENUE = "revenue"
PAYMENT_FAILURES = "payment_failures"
SUSPENSE = "suspense"

_LEDGER_ACCOUNTS = [
    Account(STRIPE_RECEIVABLE, "Stripe Receivable", "asset"),
    Account(REVENUE, "Revenue", "revenue"),
    Account(PAYMENT_FAILURES, "Payment Failures (memo)", "memo"),
    Account(SUSPENSE, "Suspense (memo)", "memo"),
]

router = APIRouter()


def ensure_default_accounts(conn: sqlite3.Connection) -> None:
    for account in _LEDGER_ACCOUNTS:
        create_account(conn, account)


def handle_payment_intent_succeeded(conn: sqlite3.Connection, event: stripe.Event) -> None:
    intent = event["data"]["object"]
    amount = int(intent["amount"])
    currency = intent.get("currency", "usd")
    post_transaction(
        conn,
        legs=[
            Leg(STRIPE_RECEIVABLE, EntryType.DEBIT, amount, currency, "payment_intent.succeeded"),
            Leg(REVENUE, EntryType.CREDIT, amount, currency, "payment_intent.succeeded"),
        ],
        transaction_id=intent["id"],
        stripe_event_id=event["id"],
    )


def handle_payment_intent_failed(conn: sqlite3.Connection, event: stripe.Event) -> None:
    intent = event["data"]["object"]
    amount = int(intent["amount"])
    currency = intent.get("currency", "usd")
    post_transaction(
        conn,
        legs=[
            Leg(PAYMENT_FAILURES, EntryType.DEBIT, amount, currency, "payment_intent.payment_failed"),
            Leg(SUSPENSE, EntryType.CREDIT, amount, currency, "payment_intent.payment_failed"),
        ],
        transaction_id=intent["id"],
        stripe_event_id=event["id"],
    )


def handle_charge_refunded(conn: sqlite3.Connection, event: stripe.Event) -> None:
    charge = event["data"]["object"]
    amount = int(charge["amount_refunded"])
    currency = charge.get("currency", "usd")
    post_transaction(
        conn,
        legs=[
            Leg(REVENUE, EntryType.DEBIT, amount, currency, "charge.refunded"),
            Leg(STRIPE_RECEIVABLE, EntryType.CREDIT, amount, currency, "charge.refunded"),
        ],
        transaction_id=charge["id"],
        stripe_event_id=event["id"],
    )


_HANDLERS = {
    "payment_intent.succeeded": handle_payment_intent_succeeded,
    "payment_intent.payment_failed": handle_payment_intent_failed,
    "charge.refunded": handle_charge_refunded,
}


def process_event(conn: sqlite3.Connection, event: stripe.Event) -> str:
    """Apply a verified Stripe event to the ledger, idempotently.

    Returns "processed", "duplicate", or "ignored" (event type we don't
    handle -- accepted but not applied).
    """
    ensure_default_accounts(conn)

    if not claim_event(conn, event["id"], event["type"]):
        logger.info("duplicate webhook event ignored: %s", event["id"])
        return "duplicate"

    handler = _HANDLERS.get(event["type"])
    if handler is None:
        logger.info("unhandled event type accepted without ledger effect: %s", event["type"])
        return "ignored"

    handler(conn, event)
    return "processed"


@router.post("/webhook")
async def stripe_webhook(request: Request):
    from src.config import get_db, get_webhook_secret

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = get_webhook_secret()

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        logger.warning("rejected webhook with invalid signature/payload: %s", exc)
        raise HTTPException(status_code=400, detail="invalid signature") from exc

    conn = get_db()
    status = process_event(conn, event)
    return {"status": status, "event_id": event["id"]}
