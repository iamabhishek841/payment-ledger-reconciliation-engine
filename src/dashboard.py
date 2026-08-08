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

Designed to look like an internal fintech tool rather than a default
Streamlit app: dark palette, card-style KPIs, Plotly charts, and a
proper multi-column grid.
"""

from __future__ import annotations

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

ACCENT = "#f0b429"  # amber -- the single accent color for alerts/mismatches
BG = "#0b0f14"
PANEL = "#131922"
SIDEBAR_BG = "#0d1420"  # distinct tone from BG/PANEL so the sidebar reads as its own surface
SIDEBAR_BORDER = "#232e3f"
PANEL_BORDER = "#1f2733"
TEXT = "#e6edf3"
MUTED = "#9aa5b3"  # lightened from the original #8b96a5 for better readability on dark backgrounds
SIDEBAR_TEXT = "#c3cbd6"
POSITIVE = "#3fb950"
NEGATIVE = "#f85149"
LIVE_COLOR = "#3fb950"
DEMO_COLOR = "#58a6ff"

st.set_page_config(
    page_title="Payment Ledger Reconciliation Engine",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    f"""
    <style>
    .stApp {{
        background-color: {BG};
        color: {TEXT};
    }}
    section[data-testid="stSidebar"] {{
        background-color: {SIDEBAR_BG};
        border-right: 1px solid {SIDEBAR_BORDER};
    }}
    section[data-testid="stSidebar"] > div {{
        padding-top: 1.5rem;
    }}
    section[data-testid="stSidebar"] h3 {{
        font-size: 0.95rem;
        color: {TEXT};
        margin-bottom: 2px;
    }}
    section[data-testid="stSidebar"] .stCaption, section[data-testid="stSidebar"] small {{
        color: {SIDEBAR_TEXT} !important;
    }}
    section[data-testid="stSidebar"] label {{
        color: {SIDEBAR_TEXT} !important;
        font-size: 0.82rem;
    }}
    section[data-testid="stSidebar"] input {{
        background-color: {PANEL} !important;
        border: 1px solid {SIDEBAR_BORDER} !important;
        color: {TEXT} !important;
    }}
    .sidebar-divider {{
        border: none;
        border-top: 1px solid {SIDEBAR_BORDER};
        margin: 20px 0;
    }}
    .sidebar-section-title {{
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 0.95rem;
        font-weight: 600;
        color: {TEXT};
        margin: 4px 0 10px 0;
    }}
    h1, h2, h3, h4 {{
        font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
        letter-spacing: -0.01em;
    }}
    .kpi-card {{
        background-color: {PANEL};
        border: 1px solid {PANEL_BORDER};
        border-radius: 12px;
        padding: 20px 22px;
        margin-bottom: 8px;
    }}
    .kpi-label {{
        color: {MUTED};
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 6px;
    }}
    .kpi-value {{
        font-size: 2rem;
        font-weight: 700;
        color: {TEXT};
    }}
    .kpi-sub {{
        font-size: 0.78rem;
        color: {MUTED};
        margin-top: 4px;
    }}
    .accent {{ color: {ACCENT}; }}
    .positive {{ color: {POSITIVE}; }}
    .negative {{ color: {NEGATIVE}; }}
    .section-header {{
        font-size: 1.05rem;
        font-weight: 600;
        color: {TEXT};
        margin: 6px 0 12px 0;
        padding-bottom: 8px;
        border-bottom: 1px solid {PANEL_BORDER};
    }}
    .mismatch-pill {{
        display: inline-block;
        background-color: rgba(240, 180, 41, 0.15);
        color: {ACCENT};
        border: 1px solid rgba(240, 180, 41, 0.4);
        border-radius: 999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        font-weight: 600;
    }}
    div[data-testid="stDataFrame"] {{
        border: 1px solid {PANEL_BORDER};
        border-radius: 10px;
        overflow: hidden;
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


def mode_badge_html(size: str = "0.75rem") -> str:
    color = DEMO_COLOR if IS_DEMO_MODE else LIVE_COLOR
    label = "Demo data" if IS_DEMO_MODE else "Live"
    return (
        f'<span style="display:inline-block; background-color:{color}22; color:{color}; '
        f'border:1px solid {color}66; border-radius:999px; padding:2px 10px; '
        f'font-size:{size}; font-weight:600; letter-spacing:0.02em;">● {label}</span>'
    )


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
    st.dataframe(
        activity_df,
        use_container_width=True,
        height=380,
        hide_index=True,
    )


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
    f'## 💳 Payment Ledger Reconciliation Engine &nbsp; {mode_badge_html()}',
    unsafe_allow_html=True,
)
st.markdown(
    f'<span style="color:{MUTED};">Double-entry ledger &middot; Stripe test-mode integration '
    f"&middot; idempotent webhooks &middot; drift reconciliation</span>",
    unsafe_allow_html=True,
)
st.write("")

with st.sidebar:
    st.markdown(mode_badge_html(size="0.8rem"), unsafe_allow_html=True)
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
    st.markdown(
        kpi_card(
            "Reconciliation Match Rate",
            match_rate,
            f"{matched}/{total_checked} PaymentIntents matched" if report else "No report yet",
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
                    marker_color=[POSITIVE, ACCENT],
                    text=[matched, mismatched],
                    textposition="outside",
                )
            ]
        )
        fig.update_layout(
            paper_bgcolor=PANEL,
            plot_bgcolor=PANEL,
            font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10),
            height=320,
            showlegend=False,
            xaxis=dict(gridcolor=PANEL_BORDER),
            yaxis=dict(gridcolor=PANEL_BORDER, title="PaymentIntents"),
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
        palette = [ACCENT, "#58a6ff", POSITIVE, "#a371f7"]
        for i, account_id in enumerate(sorted(by_account["account_id"].unique())):
            sub = by_account[by_account["account_id"] == account_id]
            fig2.add_trace(
                go.Scatter(
                    x=sub["created_at"],
                    y=sub["running_balance"],
                    mode="lines+markers",
                    name=account_id,
                    line=dict(color=palette[i % len(palette)], width=2),
                )
            )
        fig2.update_layout(
            paper_bgcolor=PANEL,
            plot_bgcolor=PANEL,
            font_color=TEXT,
            margin=dict(l=10, r=10, t=10, b=10),
            height=320,
            legend=dict(orientation="h", y=-0.2),
            xaxis=dict(gridcolor=PANEL_BORDER),
            yaxis=dict(gridcolor=PANEL_BORDER, title="Balance ($)"),
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
    st.dataframe(
        filtered[display_cols].sort_values("created_at", ascending=False),
        use_container_width=True,
        height=320,
        hide_index=True,
    )
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
        st.json(
            {
                "payment_intent_id": mismatch["payment_intent_id"],
                "amount_cents": mismatch["stripe_amount"],
                "status": mismatch["stripe_status"],
            }
        )
    with detail_cols[1]:
        st.markdown("**Local ledger record**")
        st.json(
            {
                "payment_intent_id": mismatch["payment_intent_id"],
                "amount_cents": mismatch["local_amount"],
            }
        )
    st.warning(mismatch["detail"])
elif report:
    st.success("No mismatches in the last reconciliation run — Stripe and the local ledger agree.")
else:
    st.info("Run reconciliation to see flagged mismatches here.")
