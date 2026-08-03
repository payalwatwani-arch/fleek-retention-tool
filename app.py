"""Streamlit dashboard for the Fleek retention pipeline.

Presentation layer only — all pipeline logic lives in src/pipeline.py and
src/state.py; this file just calls those functions and renders the result.

Run with:

    streamlit run app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.pipeline import run_pipeline
from src.state import DEFAULT_DB_PATH, get_pending, mark_actioned

WORKBOOK_PATH = Path("data/raw/portfolio.xlsx")
BATCH_UPLOAD_PATH = Path("data/raw/batch_upload.xlsx")

st.set_page_config(page_title="Fleek Retention Dashboard", layout="wide")


def _run_and_store(filepath) -> None:
    drafted_df, summary = run_pipeline(filepath, db_path=DEFAULT_DB_PATH)
    st.session_state.df = drafted_df
    st.session_state.sync_summary = summary


# ---------------------------------------------------------------------
# SETUP: load the portfolio workbook once, keep the result in session
# state so we don't re-run the pipeline on every click.
# ---------------------------------------------------------------------
if "df" not in st.session_state:
    if WORKBOOK_PATH.exists():
        _run_and_store(WORKBOOK_PATH)
    else:
        st.title("Fleek Retention Dashboard — Setup")
        st.write(
            "No portfolio workbook is configured yet. Upload the initial "
            "workbook (must contain `Accounts` and `new_accounts` sheets) "
            "to run the pipeline for the first time."
        )
        uploaded = st.file_uploader("Portfolio workbook (.xlsx)", type=["xlsx"])
        if uploaded is not None:
            WORKBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
            WORKBOOK_PATH.write_bytes(uploaded.getvalue())
            _run_and_store(WORKBOOK_PATH)
            st.rerun()
        st.stop()

df = st.session_state.df

st.title("Fleek Retention Dashboard")
view = st.sidebar.radio(
    "View", ["Overview", "Segmentation", "Action Center", "Batch Ingestion"]
)

# ---------------------------------------------------------------------
# VIEW 1 — Overview
# ---------------------------------------------------------------------
if view == "Overview":
    st.header("Overview")

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total accounts", f"{len(df):,}")
    col2.metric("Total GMV (6m)", f"${df['gmv_total_6m'].sum():,.0f}")

    ownership_pct = df["ownership"].value_counts(normalize=True) * 100
    col3.metric("Account Managed", f"{ownership_pct.get('Account Managed', 0.0):.0f}%")
    col4.metric("Self Serve", f"{ownership_pct.get('Self Serve', 0.0):.0f}%")

    col5.metric("At-risk accounts", f"{int(df['is_at_risk'].sum()):,}")

    st.subheader("Since last run")
    summary = st.session_state.sync_summary
    changed = summary["new_count"] + summary["reset_to_pending_count"]
    st.write(f"**{changed} account(s) changed status since your last run.**")
    st.write(
        f"- New accounts: {summary['new_count']}\n"
        f"- Reset to pending (something changed): {summary['reset_to_pending_count']}\n"
        f"- Unchanged: {summary['unchanged_count']}"
    )

# ---------------------------------------------------------------------
# VIEW 2 — Segmentation
# ---------------------------------------------------------------------
elif view == "Segmentation":
    st.header("Segmentation")

    fcol1, fcol2, fcol3, fcol4, fcol5 = st.columns(5)
    segment_filter = fcol1.multiselect("Segment", sorted(df["segment"].dropna().unique()))
    action_filter = fcol2.multiselect("Action", sorted(df["action"].dropna().unique()))
    ownership_filter = fcol3.multiselect("Ownership", sorted(df["ownership"].dropna().unique()))
    region_filter = fcol4.multiselect("Region", sorted(df["region"].dropna().unique()))
    at_risk_filter = fcol5.selectbox("At risk", ["All", "At risk only", "Not at risk"])

    filtered = df
    if segment_filter:
        filtered = filtered[filtered["segment"].isin(segment_filter)]
    if action_filter:
        filtered = filtered[filtered["action"].isin(action_filter)]
    if ownership_filter:
        filtered = filtered[filtered["ownership"].isin(ownership_filter)]
    if region_filter:
        filtered = filtered[filtered["region"].isin(region_filter)]
    if at_risk_filter == "At risk only":
        filtered = filtered[filtered["is_at_risk"] == True]  # noqa: E712
    elif at_risk_filter == "Not at risk":
        filtered = filtered[filtered["is_at_risk"] == False]  # noqa: E712

    st.caption(f"{len(filtered)} of {len(df)} accounts")
    st.dataframe(
        filtered[
            ["account_id", "segment", "action", "is_at_risk", "gmv_total_6m", "broker_reliance_pct"]
        ],
        width="stretch",
        hide_index=True,
    )

# ---------------------------------------------------------------------
# VIEW 3 — Action Center
# ---------------------------------------------------------------------
elif view == "Action Center":
    st.header("Action Center")

    # Read straight from the state DB (not session_state) so a
    # mark_actioned() call is reflected immediately, with no pipeline rerun.
    pending = get_pending(db_path=DEFAULT_DB_PATH)
    pending_ids = pending.loc[pending["last_action"] != "None", "account_id"]
    queue = df[df["account_id"].isin(pending_ids)].reset_index(drop=True)

    if queue.empty:
        st.success("All caught up — no pending actions.")
    else:
        st.caption(f"{len(queue)} account(s) awaiting action")
        selected_id = st.selectbox("Select an account", queue["account_id"])
        row = queue[queue["account_id"] == selected_id].iloc[0]

        mcol1, mcol2, mcol3 = st.columns(3)
        mcol1.metric("GMV (6m)", f"${row['gmv_total_6m']:,.0f}")
        mcol2.metric("Broker reliance", f"{row['broker_reliance_pct']:.0f}%")
        mcol3.metric("Segment", row["segment"])

        st.write(f"**Action:** {row['action']}")
        if row["is_at_risk"]:
            st.warning(f"At risk: {row['at_risk_detail']}")

        variants_by_tone = {variant["tone"]: variant for variant in row["draft_variants"]}
        tone = st.radio(
            "Tone",
            list(variants_by_tone),
            key=f"tone_{selected_id}",
            horizontal=True,
        )
        variant = variants_by_tone[tone]

        st.text_input(
            "Subject", value=variant["subject"], key=f"subject_{selected_id}_{tone}"
        )
        st.text_area(
            "Message",
            value=variant["message"],
            height=250,
            key=f"message_{selected_id}_{tone}",
        )

        if st.button("Mark as actioned", key=f"mark_actioned_{selected_id}"):
            mark_actioned(selected_id, db_path=DEFAULT_DB_PATH)
            st.rerun()

# ---------------------------------------------------------------------
# VIEW 4 — Batch ingestion
# ---------------------------------------------------------------------
elif view == "Batch Ingestion":
    st.header("Batch Ingestion")
    st.write(
        "Upload a new workbook (e.g. the next `new_accounts` batch) to "
        "re-run the pipeline and reconcile it against the existing state."
    )

    batch_file = st.file_uploader("New workbook (.xlsx)", type=["xlsx"], key="batch_uploader")

    if batch_file is not None:
        previous_summary = st.session_state.sync_summary

        BATCH_UPLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
        BATCH_UPLOAD_PATH.write_bytes(batch_file.getvalue())
        _run_and_store(BATCH_UPLOAD_PATH)

        new_summary = st.session_state.sync_summary

        st.success(f"Pipeline re-run on {len(st.session_state.df)} accounts.")

        st.subheader("Sync summary — before vs. after this upload")
        before_col, after_col = st.columns(2)
        with before_col:
            st.markdown("**Before this upload**")
            st.metric("New", previous_summary["new_count"])
            st.metric("Reset to pending", previous_summary["reset_to_pending_count"])
            st.metric("Unchanged", previous_summary["unchanged_count"])
        with after_col:
            st.markdown("**After this upload**")
            st.metric("New", new_summary["new_count"])
            st.metric("Reset to pending", new_summary["reset_to_pending_count"])
            st.metric("Unchanged", new_summary["unchanged_count"])
