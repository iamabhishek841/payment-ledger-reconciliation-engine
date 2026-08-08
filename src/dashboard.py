"""Streamlit dashboard for the payment ledger reconciliation engine.

Reads ledger state directly from the SQLite database and, if a
reconciliation report JSON file exists (written by `reconciliation.py`
via the CLI or the `/reconciliation-report` API endpoint), renders its
matched/mismatched breakdown. Designed to look like an internal fintech
tool rather than a default Streamlit app: dark palette, card-style KPIs,
Plotly charts, and a proper multi-column grid.
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

DB_PATH = os.environ.get("LEDGER_DB_PATH", "ledger.db")
REPORT_PATH = os.environ.get("RECONCILIATION_REPORT_PATH", "reconciliation_report.json")

ACCENT = "#f0b429"  # amber -- the single accent color for alerts/mismatches
BG = "#0b0f14"
PANEL = "#131922"
PANEL_BORDER = "#1f2733"
TEXT = "#e6edf3"
MUTED = "#8b96a5"
POSITIVE = "#3fb950"
NEGATIVE = "#f85149"

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
        background-color: {PANEL};
        border-right: 1px solid {PANEL_BORDER};
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


@st.cache_resource
def get_connection(db_path: str):
    conn = connect(db_path)
    init_schema(conn)
    return conn


def load_entries_df(db_path: str) -> pd.DataFrame:
    conn = get_connection(db_path)
    rows = list_entries(conn, limit=5000)
    if not rows:
        return pd.DataFrame(
            columns=[
                "id", "transaction_id", "account_id", "entry_type",
                "amount_cents", "currency", "stripe_event_id", "description", "created_at",
            ]
        )
    df = pd.DataFrame([dict(r) for r in rows])
    df["created_at"] = pd.to_datetime(df["created_at"])
    df["amount"] = df["amount_cents"] / 100.0
    return df.sort_values("created_at")


def load_reconciliation_report(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


st.markdown("## 💳 Payment Ledger Reconciliation Engine")
st.markdown(
    f'<span style="color:{MUTED};">Double-entry ledger &middot; Stripe test-mode integration '
    f"&middot; idempotent webhooks &middot; drift reconciliation</span>",
    unsafe_allow_html=True,
)
st.write("")

with st.sidebar:
    st.markdown("### Data source")
    db_path_input = st.text_input("Ledger DB path", value=DB_PATH)
    report_path_input = st.text_input("Reconciliation report path", value=REPORT_PATH)
    st.caption(
        "The dashboard reads the ledger directly from SQLite and, if present, "
        "the last reconciliation report JSON written by `reconciliation.py`."
    )
    st.markdown("---")
    st.markdown("### About")
    st.caption(
        "Every posted transaction is a balanced debit/credit pair enforced at "
        "the database-transaction level. Balances are always computed live "
        "from summed entries, never cached."
    )

entries_df = load_entries_df(db_path_input)
report = load_reconciliation_report(report_path_input)

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
