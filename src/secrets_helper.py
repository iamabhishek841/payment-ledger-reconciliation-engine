"""Shared secret lookup used by any module that needs a Stripe key.

Checks `os.environ` first (populated from a local `.env` via
python-dotenv), then falls back to Streamlit's `st.secrets` -- the
encrypted secrets store configured through a deployed app's
Settings -> Secrets panel on Streamlit Cloud, as TOML, never committed
to the repo. The Streamlit import is done lazily and failures are
swallowed so this module works fine outside a Streamlit context (e.g.
in the FastAPI app, the webhook handler, or plain scripts/tests).
"""

from __future__ import annotations

import os


def get_secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value

    try:
        import streamlit as st

        return st.secrets.get(name)
    except Exception:
        return None
