"""Streamlit dashboard for the payment ledger reconciliation engine.

Supports two modes via the DASHBOARD_MODE environment variable:

  - "demo" (default): reads a static, pre-generated JSON snapshot from
    demo_data/. Makes zero live Stripe API calls and needs no secret key
    at all -- safe to deploy publicly (e.g. on Streamlit Cloud) without
    any credentials configured.
  - "live": reads the real local SQLite ledger and the live
    reconciliation report, when that ledger DB file actually exists on
    disk. Streamlit Cloud has no persistent filesystem and can't run the
    local FastAPI webhook receiver, so the local ledger will never exist
    there -- in that case this mode automatically falls back to a
    read-only "Live Stripe Activity" view that lists real PaymentIntents
    straight from the Stripe API, with no local-vs-Stripe comparison
    (there's nothing local to compare against). Any Stripe key this mode
    needs is resolved via src.secrets_helper.get_secret (local .env
    first, then Streamlit Cloud's st.secrets).

Visual design follows a concrete financial-ledger token system (colors,
type scale, spacing) rather than generic dark-dashboard styling -- see
the design tokens block below. The signature detail is tabular
monospace numerals (JetBrains Mono, font-variant-numeric: tabular-nums)
applied to every numeric value on the page: amounts, counts,
percentages, IDs, and timestamps.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from src.ledger.models import connect, init_schema  # noqa: E402
from src.ledger.service import get_balance, list_entries  # noqa: E402
from src.secrets_helper import get_secret  # noqa: E402
from src.stripe_client import StripeNotConfiguredError, list_recent_payment_intents  # noqa: E402

DASHBOARD_MODE = os.environ.get("DASHBOARD_MODE", "demo").strip().lower()
IS_DEMO_MODE = DASHBOARD_MODE != "live"

DB_PATH = os.environ.get("LEDGER_DB_PATH", "ledger.db")
REPORT_PATH = os.environ.get("RECONCILIATION_REPORT_PATH", "reconciliation_report.json")

DEMO_LEDGER_PATH = os.environ.get("DEMO_LEDGER_PATH", "demo_data/sample_ledger.json")
DEMO_REPORT_PATH = os.environ.get("DEMO_REPORT_PATH", "demo_data/sample_reconciliation.json")

# ---------------------------------------------------------------------------
# Design tokens -- exact values, not approximations. See PR description /
# project brief for the source of truth this must match.
# ---------------------------------------------------------------------------
BG_PRIMARY = "#0B0E14"       # deep blue-black page background
BG_SURFACE = "#131826"       # card/panel background
BG_SURFACE_ALT = "#171D2E"   # sidebar background -- visibly distinct from BG_PRIMARY
BG_SURFACE_EVEN_ROW = "#1A1F2D"  # ~3% lighter than BG_SURFACE, for alternating ledger rows
BORDER_SUBTLE = "#232A3D"
TEXT_PRIMARY = "#E8EBF2"
TEXT_SECONDARY = "#9AA3B8"
ACCENT_EMERALD = "#10B981"   # matched / credit / positive
ACCENT_AMBER = "#F59E0B"     # flagged mismatch / warning
ACCENT_INDIGO = "#6366F1"    # primary interactive accent
ACCENT_ROSE = "#F43F5E"      # debit / negative, used sparingly

# Categorical palette for the running-balance chart's per-account lines --
# drawn strictly from the token set above, no improvised colors.
ACCOUNT_LINE_PALETTE = [ACCENT_INDIGO, ACCENT_EMERALD, ACCENT_AMBER, ACCENT_ROSE]

FONT_DISPLAY = "'Space Grotesk', sans-serif"
FONT_BODY = "'Inter', sans-serif"
FONT_MONO = "'JetBrains Mono', monospace"

st.set_page_config(
    page_title="Payment Ledger Reconciliation Engine",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

    :root {{
        --bg-primary: {BG_PRIMARY};
        --bg-surface: {BG_SURFACE};
        --bg-surface-alt: {BG_SURFACE_ALT};
        --border-subtle: {BORDER_SUBTLE};
        --text-primary: {TEXT_PRIMARY};
        --text-secondary: {TEXT_SECONDARY};
        --accent-emerald: {ACCENT_EMERALD};
        --accent-amber: {ACCENT_AMBER};
        --accent-indigo: {ACCENT_INDIGO};
        --accent-rose: {ACCENT_ROSE};
    }}

    .stApp {{
        background-color: var(--bg-primary);
        color: var(--text-primary);
        font-family: {FONT_BODY};
        font-size: 14px;
        font-weight: 400;
    }}

    /* ---- Sidebar: bg-surface-alt, visibly distinct from bg-primary ---- */
    section[data-testid="stSidebar"] {{
        background-color: var(--bg-surface-alt);
        border-right: 1px solid var(--border-subtle);
    }}
    section[data-testid="stSidebar"] > div {{
        padding: 24px 16px;
    }}
    section[data-testid="stSidebar"] .stCaption, section[data-testid="stSidebar"] small {{
        color: var(--text-secondary) !important;
        font-size: 12px !important;
    }}
    section[data-testid="stSidebar"] label {{
        color: var(--text-secondary) !important;
        font-size: 13px;
        font-family: {FONT_BODY};
    }}
    section[data-testid="stSidebar"] input {{
        background-color: var(--bg-surface) !important;
        border: 1px solid var(--border-subtle) !important;
        color: var(--text-primary) !important;
        font-family: {FONT_MONO};
        font-variant-numeric: tabular-nums;
    }}
    .sidebar-divider {{
        border: none;
        border-top: 1px solid var(--border-subtle);
        margin: 24px 0;
    }}
    .sidebar-section-title {{
        display: flex;
        align-items: center;
        gap: 8px;
        font-family: {FONT_DISPLAY};
        font-size: 15px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--text-primary);
        margin: 0 0 12px 0;
    }}

    /* ---- Headings / page title ---- */
    h1, h2, h3, h4 {{
        font-family: {FONT_DISPLAY};
        font-weight: 700;
        letter-spacing: -0.01em;
    }}
    .page-title {{
        font-family: {FONT_DISPLAY};
        font-weight: 700;
        font-size: 28px;
        color: var(--text-primary);
        margin: 0 0 8px 0;
        display: flex;
        align-items: center;
        gap: 16px;
    }}
    .page-subtitle {{
        font-family: {FONT_BODY};
        font-size: 14px;
        color: var(--text-secondary);
    }}

    /* ---- KPI cards ---- */
    .kpi-card {{
        background-color: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        padding: 20px;
        margin-bottom: 8px;
    }}
    .kpi-label {{
        font-family: {FONT_BODY};
        color: var(--text-secondary);
        font-size: 12px;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 8px;
    }}
    .kpi-value {{
        font-family: {FONT_MONO};
        font-variant-numeric: tabular-nums;
        font-size: 32px;
        font-weight: 700;
        color: var(--text-primary);
        line-height: 1.2;
    }}
    .kpi-sub {{
        font-family: {FONT_BODY};
        font-size: 12px;
        color: var(--text-secondary);
        margin-top: 8px;
    }}
    .kpi-value.positive {{ color: var(--accent-emerald); }}
    .kpi-value.accent {{ color: var(--accent-amber); }}
    .mono-num {{
        font-family: {FONT_MONO};
        font-variant-numeric: tabular-nums;
    }}

    /* ---- Section headers ---- */
    .section-header {{
        font-family: {FONT_DISPLAY};
        font-size: 15px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--text-primary);
        margin: 8px 0 16px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid var(--border-subtle);
    }}

    /* ---- Mode badge ---- */
    .mode-badge {{
        display: inline-block;
        border-radius: 999px;
        padding: 4px 12px;
        font-family: {FONT_BODY};
        font-weight: 600;
        letter-spacing: 0.02em;
    }}

    /* ---- Mismatch pill ---- */
    .mismatch-pill {{
        display: inline-block;
        background-color: rgba(245, 158, 11, 0.15);
        color: var(--accent-amber);
        border: 1px solid rgba(245, 158, 11, 0.4);
        border-radius: 999px;
        padding: 4px 12px;
        font-family: {FONT_BODY};
        font-size: 12px;
        font-weight: 600;
    }}

    /* ---- Ledger-style HTML tables (Ledger Entries, Live Stripe Activity) ---- */
    .ledger-table-wrap {{
        border: 1px solid var(--border-subtle);
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        overflow: auto;
        max-height: 420px;
    }}
    table.ledger-table {{
        width: 100%;
        border-collapse: collapse;
        font-family: {FONT_BODY};
        font-size: 13px;
    }}
    table.ledger-table thead th {{
        position: sticky;
        top: 0;
        background-color: var(--bg-surface-alt);
        color: var(--text-secondary);
        text-transform: uppercase;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.05em;
        text-align: left;
        padding: 12px;
        border-bottom: 1px solid var(--border-subtle);
        white-space: nowrap;
    }}
    table.ledger-table td {{
        padding: 8px 12px;
        border-bottom: 1px solid var(--border-subtle);
        color: var(--text-primary);
        white-space: nowrap;
    }}
    table.ledger-table td.mono {{
        font-family: {FONT_MONO};
        font-variant-numeric: tabular-nums;
        color: var(--text-secondary);
    }}
    table.ledger-table td.amount {{
        font-family: {FONT_MONO};
        font-variant-numeric: tabular-nums;
        text-align: right;
        font-weight: 500;
    }}
    .entry-badge {{
        display: inline-block;
        border-radius: 6px;
        padding: 2px 8px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }}
    .entry-badge.credit {{
        background-color: rgba(16, 185, 129, 0.15);
        color: var(--accent-emerald);
    }}
    .entry-badge.debit {{
        background-color: rgba(244, 63, 94, 0.15);
        color: var(--accent-rose);
    }}
    .entry-badge.succeeded {{
        background-color: rgba(16, 185, 129, 0.15);
        color: var(--accent-emerald);
    }}
    .entry-badge.other-status {{
        background-color: rgba(154, 163, 184, 0.15);
        color: var(--text-secondary);
    }}

    /* ---- Record blocks (Flagged Mismatch Detail) ---- */
    .record-block {{
        background-color: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
        padding: 16px;
    }}
    .record-row {{
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        padding: 4px 0;
        font-size: 13px;
        gap: 16px;
    }}
    .record-key {{
        font-family: {FONT_BODY};
        color: var(--text-secondary);
    }}
    .record-val {{
        font-family: {FONT_MONO};
        font-variant-numeric: tabular-nums;
        color: var(--text-primary);
        text-align: right;
        word-break: break-all;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


def kpi_card(label: str, value: str, sub: str = "", value_class: str = "") -> str:
    return f"""
    <div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value {value_class}">{value}</div>
        <div class="kpi-sub">{sub}</div>
    </div>
    """


def mode_badge_html(size: str = "12px") -> str:
    color = ACCENT_INDIGO if IS_DEMO_MODE else ACCENT_EMERALD
    label = "Demo data" if IS_DEMO_MODE else "Live"
    return (
        f'<span class="mode-badge" style="background-color:{color}22; color:{color}; '
        f'border:1px solid {color}66; font-size:{size};">● {label}</span>'
    )


def mono(value) -> str:
    """Wrap a numeric/ID/timestamp value in the tabular-mono span -- the
    one styling detail that must apply to every number on the page."""
    return f'<span class="mono-num">{html.escape(str(value))}</span>'


def truncate_id(value: str, head: int = 10, tail: int = 4) -> str:
    if value is None:
        return "—"
    value = str(value)
    if len(value) <= head + tail + 1:
        return value
    return f"{value[:head]}…{value[-tail:]}"


@st.cache_resource
def get_connection(db_path: str):
    conn = connect(db_path)
    init_schema(conn)
    return conn


def _entries_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=[
                "id", "transaction_id", "account_id", "entry_type",
                "amount_cents", "currency", "stripe_event_id", "description", "created_at",
            ]
        )
    df = pd.DataFrame(rows)
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["amount"] = df["amount_cents"] / 100.0
    return df.sort_values("created_at")


def load_entries_df_live(db_path: str) -> pd.DataFrame:
    conn = get_connection(db_path)
    rows = list_entries(conn, limit=5000)
    return _entries_to_df([dict(r) for r in rows])


def load_entries_df_demo(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return _entries_to_df([])
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)
    return _entries_to_df(rows)


def load_reconciliation_report(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def render_ledger_table(df: pd.DataFrame) -> None:
    """Ledger-style table: alternating-row tint plus a left border accent
    per row (emerald for credit, rose for debit) so the debit/credit
    nature of every row is visible at a glance."""
    header = (
        "<tr><th>Created</th><th>Transaction</th><th>Account</th><th>Type</th>"
        '<th style="text-align:right;">Amount</th><th>Currency</th>'
        "<th>Stripe Event</th><th>Description</th></tr>"
    )
    rows = []
    for i, row in enumerate(df.itertuples(index=False)):
        is_credit = row.entry_type == "credit"
        border_color = ACCENT_EMERALD if is_credit else ACCENT_ROSE
        row_bg = BG_SURFACE if i % 2 == 0 else BG_SURFACE_EVEN_ROW
        amount_color = ACCENT_EMERALD if is_credit else ACCENT_ROSE
        sign = "+" if is_credit else "−"
        created_str = row.created_at.strftime("%Y-%m-%d %H:%M:%S")
        txn_short = html.escape(truncate_id(row.transaction_id))
        event_short = html.escape(truncate_id(row.stripe_event_id))
        badge_class = "credit" if is_credit else "debit"
        rows.append(
            f'<tr style="background-color:{row_bg};">'
            f'<td class="mono" style="border-left:2px solid {border_color};">{mono(created_str)}</td>'
            f'<td class="mono" title="{html.escape(str(row.transaction_id))}">{mono(txn_short)}</td>'
            f"<td>{html.escape(str(row.account_id))}</td>"
            f'<td><span class="entry-badge {badge_class}">{row.entry_type.upper()}</span></td>'
            f'<td class="amount" style="color:{amount_color};">{mono(f"{sign}${row.amount:,.2f}")}</td>'
            f'<td class="mono">{mono(str(row.currency).upper())}</td>'
            f'<td class="mono" title="{html.escape(str(row.stripe_event_id))}">{mono(event_short)}</td>'
            f"<td>{html.escape(str(row.description or ''))}</td>"
            "</tr>"
        )
    table_html = (
        '<div class="ledger-table-wrap"><table class="ledger-table">'
        f"<thead>{header}</thead><tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def render_stripe_activity_table(df: pd.DataFrame) -> None:
    """Same ledger-table visual language, applied to the read-only Live
    Stripe Activity view: no credit/debit signal here (nothing local to
    compare against), instead a left-border accent by PaymentIntent
    status -- emerald for succeeded, neutral otherwise."""
    header = (
        "<tr><th>PaymentIntent ID</th><th>Created</th>"
        '<th style="text-align:right;">Amount</th><th>Currency</th><th>Status</th></tr>'
    )
    rows = []
    for i, row in enumerate(df.itertuples(index=False)):
        succeeded = row.status == "succeeded"
        border_color = ACCENT_EMERALD if succeeded else BORDER_SUBTLE
        row_bg = BG_SURFACE if i % 2 == 0 else BG_SURFACE_EVEN_ROW
        created_str = row.created.strftime("%Y-%m-%d %H:%M:%S")
        id_short = html.escape(truncate_id(row.id))
        badge_class = "succeeded" if succeeded else "other-status"
        rows.append(
            f'<tr style="background-color:{row_bg};">'
            f'<td class="mono" style="border-left:2px solid {border_color};" '
            f'title="{html.escape(str(row.id))}">{mono(id_short)}</td>'
            f'<td class="mono">{mono(created_str)}</td>'
            f'<td class="amount">{mono(f"${row.amount:,.2f}")}</td>'
            f'<td class="mono">{mono(str(row.currency).upper())}</td>'
            f'<td><span class="entry-badge {badge_class}">{html.escape(row.status.upper())}</span></td>'
            "</tr>"
        )
    table_html = (
        '<div class="ledger-table-wrap"><table class="ledger-table">'
        f"<thead>{header}</thead><tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def render_record_block(data: dict, mono_keys: set[str]) -> None:
    """Styled key/value block used in place of st.json so every numeric
    or ID value renders in tabular JetBrains Mono, not the browser's
    default JSON-viewer monospace font."""
    rows_html = []
    for key, value in data.items():
        if value is None:
            val_html = '<span class="mono-num" style="color:var(--text-secondary);">null</span>'
        elif key in mono_keys:
            val_html = mono(value)
        else:
            val_html = html.escape(str(value))
        rows_html.append(
            f'<div class="record-row"><span class="record-key">{html.escape(key)}</span>'
            f'<span class="record-val">{val_html}</span></div>'
        )
    st.markdown(f'<div class="record-block">{"".join(rows_html)}</div>', unsafe_allow_html=True)


def render_live_cloud_fallback() -> None:
    """Render the read-only Live Stripe Activity view.

    Used when DASHBOARD_MODE=live but no local ledger DB exists (the
    Streamlit Cloud scenario). Talks to the Stripe API directly and
    intentionally does NOT show a matched-vs-mismatched comparison,
    running-balance chart, or ledger entries table -- there is no local
    ledger to compare against or derive those from.
    """
    st.markdown(
        '<div class="section-header">📡 Live Stripe Activity (read-only)</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "No local ledger DB found, so this shows real PaymentIntents pulled "
        "directly from the Stripe API instead of a Stripe-vs-ledger "
        "reconciliation. Run this app locally with a populated ledger.db "
        "(see README) to see the full reconciliation view."
    )

    if not get_secret("STRIPE_SECRET_KEY"):
        st.error(
            "STRIPE_SECRET_KEY is not configured. Set it in your local .env, or in "
            "Streamlit Cloud's Settings -> Secrets, to load live Stripe activity."
        )
        return

    try:
        activity_df = load_live_stripe_activity(limit=20)
    except StripeNotConfiguredError as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # Stripe API/network errors surface here
        st.error(f"Failed to load live Stripe activity: {exc}")
        return

    if activity_df.empty:
        st.info("No PaymentIntents found in this Stripe test-mode account yet.")
        return

    succeeded_count = int((activity_df["status"] == "succeeded").sum())
    total_amount = float(activity_df.loc[activity_df["status"] == "succeeded", "amount"].sum())

    kpi_cols = st.columns(3)
    with kpi_cols[0]:
        st.markdown(
            kpi_card("PaymentIntents Fetched", f"{len(activity_df):,}", "Most recent, via Stripe API"),
            unsafe_allow_html=True,
        )
    with kpi_cols[1]:
        st.markdown(
            kpi_card("Succeeded", f"{succeeded_count:,}", "Status == succeeded"),
            unsafe_allow_html=True,
        )
    with kpi_cols[2]:
        st.markdown(
            kpi_card("Succeeded Amount", f"${total_amount:,.2f}", "Sum across succeeded PaymentIntents"),
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown('<div class="section-header">Recent PaymentIntents</div>', unsafe_allow_html=True)
    render_stripe_activity_table(activity_df)


def local_ledger_available(db_path: str) -> bool:
    """True if a real local ledger DB file exists at this path.

    False on Streamlit Cloud (no persistent filesystem, no local webhook
    receiver ever wrote one), which is exactly the signal live mode uses
    to fall back to the read-only Live Stripe Activity view.
    """
    return Path(db_path).exists()


@st.cache_data(ttl=30, show_spinner=False)
def load_live_stripe_activity(limit: int = 20) -> pd.DataFrame:
    """Fetch recent PaymentIntents directly from the Stripe API.

    Used only by the cloud fallback view -- there is no local ledger to
    read here, so this talks to Stripe directly instead of going through
    reconciliation.py (which compares Stripe against a local ledger that
    doesn't exist in this scenario).
    """
    intents = list_recent_payment_intents(limit=limit)
    rows = [
        {
            "id": pi["id"],
            "amount": pi["amount"] / 100.0,
            "currency": pi.get("currency", "usd"),
            "status": pi["status"],
            "created": pd.to_datetime(pi["created"], unit="s"),
        }
        for pi in intents
    ]
    columns = ["id", "amount", "currency", "status", "created"]
    df = pd.DataFrame(rows, columns=columns)
    return df.sort_values("created", ascending=False)


st.markdown(
    f'<div class="page-title">💳 Payment Ledger Reconciliation Engine {mode_badge_html()}</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="page-subtitle">Double-entry ledger &middot; Stripe test-mode integration '
    "&middot; idempotent webhooks &middot; drift reconciliation</div>",
    unsafe_allow_html=True,
)
st.write("")

with st.sidebar:
    st.markdown(mode_badge_html(), unsafe_allow_html=True)
    st.markdown('<hr class="sidebar-divider">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-section-title">🗄️ Data source</div>', unsafe_allow_html=True)

    if IS_DEMO_MODE:
        st.caption(
            "Reading a static snapshot exported from a real local Stripe "
            "test-mode run. No database connection, no Stripe API calls."
        )
        demo_ledger_input = st.text_input("Demo ledger snapshot", value=DEMO_LEDGER_PATH)
        demo_report_input = st.text_input("Demo reconciliation snapshot", value=DEMO_REPORT_PATH)
        db_path_input = None
        report_path_input = None
        local_db_found = None  # not applicable in demo mode
        st.caption("Set `DASHBOARD_MODE=live` to connect to a real ledger instead.")
    else:
        db_path_input = st.text_input("Ledger DB path", value=DB_PATH)
        report_path_input = st.text_input("Reconciliation report path", value=REPORT_PATH)
        demo_ledger_input = None
        demo_report_input = None

        local_db_found = local_ledger_available(db_path_input)
        if local_db_found:
            st.caption(
                "Reading the ledger directly from SQLite and, if present, "
                "the last reconciliation report JSON written by `reconciliation.py`."
            )
        else:
            st.caption(
                "⚠️ No local ledger DB found at this path (expected on Streamlit "
                "Cloud, which has no persistent filesystem and can't run the local "
                "webhook receiver). Falling back to a read-only **Live Stripe "
                "Activity** view below."
            )

        if get_secret("STRIPE_SECRET_KEY"):
            st.caption("✅ STRIPE_SECRET_KEY resolved")
        else:
            st.caption("⚠️ STRIPE_SECRET_KEY not configured — live data will fail to load")

    st.markdown('<hr class="sidebar-divider">', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-section-title">ℹ️ About</div>', unsafe_allow_html=True)
    st.caption(
        "Every posted transaction is a balanced debit/credit pair enforced at "
        "the database-transaction level. Balances are always computed live "
        "from summed entries, never cached."
    )

if IS_DEMO_MODE:
    view_mode = "demo"
    entries_df = load_entries_df_demo(demo_ledger_input)
    report = load_reconciliation_report(demo_report_input)
elif local_db_found:
    view_mode = "live_local"
    entries_df = load_entries_df_live(db_path_input)
    report = load_reconciliation_report(report_path_input)
else:
    view_mode = "live_cloud_fallback"
    entries_df = None
    report = None

if view_mode == "live_cloud_fallback":
    render_live_cloud_fallback()
    st.stop()

total_transactions = entries_df["transaction_id"].nunique() if not entries_df.empty else 0
matched = report["matched_count"] if report else 0
mismatched = report["mismatch_count"] if report else 0
total_checked = report["total_checked"] if report else 0
match_rate = f"{(matched / total_checked * 100):.1f}%" if total_checked else "—"

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.markdown(
        kpi_card("Total Transactions", f"{total_transactions:,}", "Distinct ledger transaction IDs"),
        unsafe_allow_html=True,
    )
with kpi_cols[1]:
    match_sub = (
        f'{mono(matched)}/{mono(total_checked)} PaymentIntents matched' if report else "No report yet"
    )
    st.markdown(
        kpi_card(
            "Reconciliation Match Rate",
            match_rate,
            match_sub,
            value_class="positive" if report and mismatched == 0 else "",
        ),
        unsafe_allow_html=True,
    )
with kpi_cols[2]:
    st.markdown(
        kpi_card(
            "Flagged Mismatches",
            f"{mismatched:,}",
            "Missing entries, amount or status drift",
            value_class="accent" if mismatched else "",
        ),
        unsafe_allow_html=True,
    )
with kpi_cols[3]:
    if IS_DEMO_MODE:
        revenue_balance = (
            entries_df[entries_df["account_id"] == "revenue"]
            .apply(lambda r: r["amount"] if r["entry_type"] == "credit" else -r["amount"], axis=1)
            .sum()
            if not entries_df.empty
            else 0.0
        )
    else:
        conn = get_connection(db_path_input)
        revenue_balance = get_balance(conn, "revenue") / 100.0 if not entries_df.empty else 0.0
    st.markdown(
        kpi_card("Revenue Balance", f"${revenue_balance:,.2f}", "Live SUM over ledger entries"),
        unsafe_allow_html=True,
    )

st.write("")
chart_cols = st.columns([1, 1])

CHART_FONT = dict(family=FONT_MONO, color=TEXT_PRIMARY)

with chart_cols[0]:
    st.markdown(
        '<div class="section-header">Reconciliation: Matched vs Mismatched</div>',
        unsafe_allow_html=True,
    )
    if report:
        fig = go.Figure(
            data=[
                go.Bar(
                    x=["Matched", "Mismatched"],
                    y=[matched, mismatched],
                    marker_color=[ACCENT_EMERALD, ACCENT_AMBER],
                    text=[matched, mismatched],
                    textposition="outside",
                    textfont=CHART_FONT,
                )
            ]
        )
        fig.update_layout(
            paper_bgcolor=BG_SURFACE,
            plot_bgcolor=BG_SURFACE,
            font=CHART_FONT,
            margin=dict(l=10, r=10, t=10, b=10),
            height=320,
            showlegend=False,
            xaxis=dict(gridcolor=BORDER_SUBTLE),
            yaxis=dict(gridcolor=BORDER_SUBTLE, title="PaymentIntents"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info(
            "No reconciliation report found yet. Run the reconciliation job "
            "(`python -m src.reconciliation` or `GET /reconciliation-report`) to populate this chart."
        )

with chart_cols[1]:
    st.markdown('<div class="section-header">Running Ledger Balance Over Time</div>', unsafe_allow_html=True)
    if not entries_df.empty:
        signed = entries_df.copy()
        signed["signed_amount"] = signed.apply(
            lambda r: r["amount"] if r["entry_type"] == "credit" else -r["amount"], axis=1
        )
        by_account = (
            signed.groupby(["account_id", "created_at"], as_index=False)["signed_amount"]
            .sum()
            .sort_values("created_at")
        )
        by_account["running_balance"] = by_account.groupby("account_id")["signed_amount"].cumsum()

        fig2 = go.Figure()
        for i, account_id in enumerate(sorted(by_account["account_id"].unique())):
            sub = by_account[by_account["account_id"] == account_id]
            fig2.add_trace(
                go.Scatter(
                    x=sub["created_at"],
                    y=sub["running_balance"],
                    mode="lines+markers",
                    name=account_id,
                    line=dict(color=ACCOUNT_LINE_PALETTE[i % len(ACCOUNT_LINE_PALETTE)], width=2),
                )
            )
        fig2.update_layout(
            paper_bgcolor=BG_SURFACE,
            plot_bgcolor=BG_SURFACE,
            font=CHART_FONT,
            margin=dict(l=10, r=10, t=10, b=10),
            height=320,
            legend=dict(orientation="h", y=-0.2),
            xaxis=dict(gridcolor=BORDER_SUBTLE),
            yaxis=dict(gridcolor=BORDER_SUBTLE, title="Balance ($)"),
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No ledger entries yet. Post a payment to see balances accrue here.")

st.write("")
st.markdown('<div class="section-header">Ledger Entries</div>', unsafe_allow_html=True)

if not entries_df.empty:
    filter_cols = st.columns([1, 1, 2])
    with filter_cols[0]:
        account_filter = st.selectbox(
            "Account", options=["All"] + sorted(entries_df["account_id"].unique().tolist())
        )
    with filter_cols[1]:
        type_filter = st.selectbox("Entry type", options=["All", "debit", "credit"])
    with filter_cols[2]:
        search = st.text_input("Search transaction / event ID", value="")

    filtered = entries_df.copy()
    if account_filter != "All":
        filtered = filtered[filtered["account_id"] == account_filter]
    if type_filter != "All":
        filtered = filtered[filtered["entry_type"] == type_filter]
    if search:
        mask = filtered["transaction_id"].str.contains(search, case=False, na=False) | filtered[
            "stripe_event_id"
        ].astype(str).str.contains(search, case=False, na=False)
        filtered = filtered[mask]

    display_cols = [
        "created_at", "transaction_id", "account_id", "entry_type",
        "amount", "currency", "stripe_event_id", "description",
    ]
    render_ledger_table(filtered[display_cols].sort_values("created_at", ascending=False))
else:
    st.info("No ledger entries in this database yet.")

st.write("")
st.markdown('<div class="section-header">Flagged Mismatch Detail</div>', unsafe_allow_html=True)

if report and report["mismatches"]:
    labels = [
        f"{m['payment_intent_id']} — {m['mismatch_type']}" for m in report["mismatches"]
    ]
    selected = st.selectbox("Select a flagged mismatch to inspect", options=labels)
    idx = labels.index(selected)
    mismatch = report["mismatches"][idx]

    pill_label = mismatch["mismatch_type"].replace("_", " ").upper()
    st.markdown(f'<span class="mismatch-pill">{pill_label}</span>', unsafe_allow_html=True)
    st.write("")
    detail_cols = st.columns(2)
    with detail_cols[0]:
        st.markdown("**Stripe record**")
        render_record_block(
            {
                "payment_intent_id": mismatch["payment_intent_id"],
                "amount_cents": mismatch["stripe_amount"],
                "status": mismatch["stripe_status"],
            },
            mono_keys={"payment_intent_id", "amount_cents"},
        )
    with detail_cols[1]:
        st.markdown("**Local ledger record**")
        render_record_block(
            {
                "payment_intent_id": mismatch["payment_intent_id"],
                "amount_cents": mismatch["local_amount"],
            },
            mono_keys={"payment_intent_id", "amount_cents"},
        )
    st.warning(mismatch["detail"])
elif report:
    st.success("No mismatches in the last reconciliation run — Stripe and the local ledger agree.")
else:
    st.info("Run reconciliation to see flagged mismatches here.")
