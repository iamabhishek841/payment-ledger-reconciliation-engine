"""FastAPI application tying together the ledger, Stripe integration,
webhook handling, and reconciliation reporting."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.config import get_db
from src.ledger.service import get_balance
from src.reconciliation import reconcile
from src.stripe_client import StripeNotConfiguredError, create_payment_intent
from src.webhook_handler import ensure_default_accounts
from src.webhook_handler import router as webhook_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_default_accounts(get_db())
    yield


app = FastAPI(
    title="Payment Ledger Reconciliation Engine",
    description="Double-entry ledger with Stripe test-mode integration and drift reconciliation.",
    lifespan=lifespan,
)
app.include_router(webhook_router)


class CreatePaymentRequest(BaseModel):
    amount_cents: int
    currency: str = "usd"


@app.post("/create-payment")
def create_payment(req: CreatePaymentRequest):
    if req.amount_cents <= 0:
        raise HTTPException(status_code=400, detail="amount_cents must be positive")
    try:
        intent = create_payment_intent(amount_cents=req.amount_cents, currency=req.currency)
    except StripeNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "id": intent["id"],
        "client_secret": intent["client_secret"],
        "status": intent["status"],
        "amount": intent["amount"],
        "currency": intent["currency"],
    }


@app.get("/reconciliation-report")
def reconciliation_report(limit: int = 20):
    try:
        report = reconcile(get_db(), limit=limit)
    except StripeNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return report.to_dict()


@app.get("/ledger/{account_id}/balance")
def ledger_balance(account_id: str):
    return {"account_id": account_id, "balance_cents": get_balance(get_db(), account_id)}


@app.get("/health")
def health():
    return {"status": "ok"}
