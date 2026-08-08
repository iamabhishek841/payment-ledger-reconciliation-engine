"""Thin wrapper around the Stripe SDK, pinned to test mode via STRIPE_SECRET_KEY.

Kept intentionally small: the rest of the codebase depends on this
module's functions rather than importing the `stripe` package directly,
so tests can monkeypatch a single seam.
"""

from __future__ import annotations

import stripe

from src.secrets_helper import get_secret


class StripeNotConfiguredError(RuntimeError):
    pass


def _get_secret_key() -> str:
    key = get_secret("STRIPE_SECRET_KEY")
    if not key:
        raise StripeNotConfiguredError(
            "STRIPE_SECRET_KEY is not set; copy .env.example to .env and fill it in, "
            "or configure it in Streamlit Cloud's Settings -> Secrets"
        )
    return key


def get_client() -> stripe.StripeClient:
    return stripe.StripeClient(api_key=_get_secret_key())


def create_payment_intent(amount_cents: int, currency: str = "usd", **kwargs) -> stripe.PaymentIntent:
    client = get_client()
    return client.payment_intents.create(
        params={"amount": amount_cents, "currency": currency, **kwargs}
    )


def retrieve_payment_intent(payment_intent_id: str) -> stripe.PaymentIntent:
    client = get_client()
    return client.payment_intents.retrieve(payment_intent_id)


def list_recent_payment_intents(limit: int = 20) -> list[stripe.PaymentIntent]:
    client = get_client()
    result = client.payment_intents.list(params={"limit": limit})
    return list(result)


def construct_webhook_event(payload: bytes, sig_header: str, webhook_secret: str) -> stripe.Event:
    """Verify a webhook's signature and return the parsed Event.

    Raises stripe.SignatureVerificationError if the signature is invalid.
    """
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
