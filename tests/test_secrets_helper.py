from __future__ import annotations

from src.secrets_helper import get_secret


def test_get_secret_reads_from_environ(monkeypatch):
    monkeypatch.setenv("SOME_TEST_SECRET", "value-from-env")
    assert get_secret("SOME_TEST_SECRET") == "value-from-env"


def test_get_secret_returns_none_when_missing_everywhere(monkeypatch):
    monkeypatch.delenv("DEFINITELY_UNSET_SECRET", raising=False)
    assert get_secret("DEFINITELY_UNSET_SECRET") is None


def test_get_secret_prefers_environ_and_never_touches_streamlit_secrets(monkeypatch):
    """When the value is already in os.environ, get_secret must short-circuit
    before touching st.secrets at all -- so this must succeed even though no
    secrets.toml exists anywhere on this machine (st.secrets would raise
    StreamlitSecretNotFoundError if it were reached)."""
    monkeypatch.setenv("PREFER_ENV_SECRET", "from-env")
    assert get_secret("PREFER_ENV_SECRET") == "from-env"


def test_get_secret_falls_back_to_streamlit_secrets_when_env_is_unset(monkeypatch):
    """Simulates a Streamlit Cloud deployment where the key lives in
    st.secrets (configured via Settings -> Secrets) instead of .env."""
    monkeypatch.delenv("FALLBACK_SECRET", raising=False)

    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"FALLBACK_SECRET": "from-streamlit"})
    assert get_secret("FALLBACK_SECRET") == "from-streamlit"
