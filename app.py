"""Streamlit dashboard for the Fleek retention pipeline.

Presentation layer only — all pipeline logic lives in src/pipeline.py and
src/state.py; this file just calls those functions and renders the result.

Run with:

    streamlit run app.py
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import streamlit as st

from src.pipeline import run_pipeline
from src.scoring import compute_health_score
from src.segmentation import ACCOUNT_MANAGED, SELF_SERVE
from src.state import (
    DEFAULT_DB_PATH,
    STAGE_ACTIONED,
    STAGE_FOLLOW_UP,
    STAGE_NEW,
    get_by_stage,
    mark_actioned,
    undo_action,
)

WORKBOOK_PATH = Path("data/raw/portfolio.xlsx")
BATCH_UPLOAD_PATH = Path("data/raw/batch_upload.xlsx")

st.set_page_config(page_title="Fleek Retention Dashboard", layout="wide")

# Matches a trailing email sign-off paragraph like "Best,\nThe Fleek Team" or
# "Regards,\nThe Fleek Team" so it can be dropped from the WhatsApp preview —
# WhatsApp messages don't carry email-style sign-offs.
_SIGNOFF_RE = re.compile(r"^\w+,\s*The Fleek Team$", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")


def _format_for_whatsapp(message: str) -> str:
    """Reformat a drafted email-style message for a WhatsApp preview:
    drop the email sign-off paragraph and break paragraphs into one
    sentence per line, which reads closer to how WhatsApp messages
    are actually written."""
    paragraphs = [p.strip() for p in message.strip().split("\n\n") if p.strip()]
    paragraphs = [
        p for p in paragraphs if not _SIGNOFF_RE.match(re.sub(r"\s+", " ", p))
    ]

    lines: list[str] = []
    for paragraph in paragraphs:
        normalized = re.sub(r"\s+", " ", paragraph).strip()
        lines.extend(s.strip() for s in _SENTENCE_SPLIT_RE.split(normalized) if s.strip())

    return "\n".join(lines)


def _whatsapp_preview_html(message: str) -> str:
    bubble_html = html.escape(message).replace("\n", "<br>")
    return f"""
    <div style="font-family: -apple-system, Helvetica, Arial, sans-serif;">
      <div style="color:#54656f; font-size:13px; font-weight:600; margin-bottom:4px;">
        Fleek
      </div>
      <div style="background-color:#DCF8C6; border-radius:8px; padding:10px 12px;
                  max-width:420px; font-size:14px; line-height:1.4; color:#111b21;
                  box-shadow:0 1px 0.5px rgba(0,0,0,0.13);">
        {bubble_html}
      </div>
    </div>
    """


def _run_and_store(filepath) -> None:
    drafted_df, summary = run_pipeline(filepath, db_path=DEFAULT_DB_PATH)
    st.session_state.df = drafted_df
    st.session_state.sync_summary = summary


# ---------------------------------------------------------------------
# Pipeline board (Kanban) — columns are contact stage (New / Actioned /
# Follow-up), not segment. Segment (plus an at-risk detail, when it adds
# information) shows as tag(s) on each card instead.
# ---------------------------------------------------------------------
STAGES = [STAGE_NEW, STAGE_ACTIONED, STAGE_FOLLOW_UP]

NO_ACTION = "None"


def _score_color(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
        return "orange"
    return "red"


def _segment_tags(row) -> list[str]:
    """PRIMARY segment as one tag, plus a second tag when is_at_risk adds
    information the primary segment doesn't already carry."""
    tags = [row["segment"]]
    at_risk_detail = row.get("at_risk_detail")
    if row.get("is_at_risk") and at_risk_detail and at_risk_detail != row["segment"]:
        tags.append(at_risk_detail)
    return tags


def _status_line(row) -> str:
    """Card status text: stage-driven, except "New" is always blank and a
    resolved ("None") action always reads as no-action-needed regardless
    of which post-New stage it settled in."""
    if row["status"] == STAGE_NEW:
        return ""
    if row["action"] == NO_ACTION:
        return "No action needed"
    if row["status"] == STAGE_ACTIONED:
        return f"Actioned {row['actioned_date']}"
    return f"Follow-up needed (touch {row['touch_count']})"


def _apply_filters(df, region: str, persona: str, ownership: str, at_risk_only: bool):
    filtered = df
    if region != "All":
        filtered = filtered[filtered["region"] == region]
    if persona != "All":
        filtered = filtered[filtered["buyer_persona"] == persona]
    if ownership != "All":
        filtered = filtered[filtered["ownership"] == ownership]
    if at_risk_only:
        filtered = filtered[filtered["is_at_risk"] == True]  # noqa: E712
    return filtered


def _stage_accounts(df, stage: str):
    """Accounts from the (already filtered) dataframe currently at `stage`,
    joined with their stage bookkeeping (status/touch_count/actioned_date)."""
    state_df = get_by_stage(stage, db_path=DEFAULT_DB_PATH)
    return df.merge(
        state_df[["account_id", "status", "touch_count", "actioned_date"]],
        on="account_id",
        how="inner",
    )


def _render_card(row) -> None:
    account_id = row["account_id"]
    score, factors = compute_health_score(row)
    color = _score_color(score)
    arrow = "↑" if (factors[0]["direction"] == "up" if factors else True) else "↓"

    tags = " ".join(f"`{tag}`" for tag in _segment_tags(row))
    status_line = _status_line(row)

    label = f"**{account_id}**  ·  :{color}[{score}] {arrow}  ·  {tags}"
    if status_line:
        label += f"  ·  {status_line}"

    with st.expander(label):
        st.caption(f"{row['ownership']} · {row['region']} · {row['buyer_persona']}")

        # Kanban columns are narrow, so a KPI-tile grid (st.metric) clips its
        # values here — plain label/value markdown wraps instead of
        # truncating, so the full numbers stay legible at card width.
        trend_display = (
            f"{row['gmv_trend_pct']:.0f}%" if row["has_trend_baseline"] else "No baseline"
        )
        account_numbers = [
            ("GMV (6m)", f"${row['gmv_total_6m']:,.0f}"),
            ("Orders (6m)", f"{row['orders_6m']:,.0f}"),
            ("App active days", f"{row['app_active_days_6m']:.0f}"),
            ("PDP views (6m)", f"{row['pdp_views_6m']:.0f}"),
            ("Broker reliance", f"{row['broker_reliance_pct']:.0f}%"),
            ("Bundle share", f"{row['bundle_gmv_share_pct']:.0f}%"),
            ("GMV trend", trend_display),
            ("Make an offer (6m)", f"{row['make_an_offer_6m']:.0f}"),
        ]
        left_col, right_col = st.columns(2)
        for i, (num_label, num_value) in enumerate(account_numbers):
            target_col = left_col if i % 2 == 0 else right_col
            target_col.markdown(f"{num_label}: **{num_value}**")

        st.markdown(f"**Health score: :{color}[{score}]**")
        for factor in factors:
            factor_arrow = "↑" if factor["direction"] == "up" else "↓"
            factor_color = "green" if factor["impact"] == "positive" else "red"
            st.markdown(f":{factor_color}[{factor_arrow}] {factor['label']}")

        if row["is_at_risk"]:
            st.warning(f"At risk: {row['at_risk_detail']}")

        if row["action"] == NO_ACTION:
            st.caption("No action needed.")
            return

        variants = row["draft_variants"]
        st.write(f"**Action:** {row['action']}")

        variants_by_tone = {variant["tone"]: variant for variant in variants}
        tone = st.radio(
            "Tone",
            list(variants_by_tone),
            key=f"tone_{account_id}",
            horizontal=True,
        )
        variant = variants_by_tone[tone]

        st.text_input(
            "Subject", value=variant["subject"], key=f"subject_{account_id}_{tone}"
        )
        st.text_area(
            "Message",
            value=variant["message"],
            height=250,
            key=f"message_{account_id}_{tone}",
        )

        stage = row["status"]
        button_col1, button_col2 = st.columns(2)
        if stage in (STAGE_NEW, STAGE_FOLLOW_UP):
            if button_col1.button("Mark as Actioned", key=f"mark_actioned_{account_id}"):
                mark_actioned(account_id, db_path=DEFAULT_DB_PATH)
                st.rerun()
        if stage in (STAGE_ACTIONED, STAGE_FOLLOW_UP):
            if button_col2.button("Undo", key=f"undo_{account_id}"):
                undo_action(account_id, db_path=DEFAULT_DB_PATH)
                st.rerun()


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
    "View", ["Overview", "Pipeline", "Batch Ingestion"]
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
    changed = summary["new_count"] + summary["follow_up_count"] + summary["resolved_count"]
    st.write(f"**{changed} account(s) changed status since your last run.**")
    st.write(
        f"- New accounts: {summary['new_count']}\n"
        f"- Moved to follow-up (something changed): {summary['follow_up_count']}\n"
        f"- Resolved (no action needed anymore): {summary['resolved_count']}\n"
        f"- Unchanged: {summary['unchanged_count']}"
    )

# ---------------------------------------------------------------------
# VIEW 2 — Pipeline (Kanban board, by contact stage)
# ---------------------------------------------------------------------
elif view == "Pipeline":
    st.header("Pipeline")

    icon_col, region_col, persona_col, ownership_col, at_risk_col = st.columns(
        [1.1, 2, 2, 2, 1.5]
    )
    with icon_col:
        st.markdown("&nbsp;")
        st.markdown("🔍 **Filter by**")
    with region_col:
        region = st.selectbox(
            "Region", ["All"] + sorted(df["region"].dropna().unique().tolist())
        )
    with persona_col:
        persona = st.selectbox(
            "Buyer persona", ["All"] + sorted(df["buyer_persona"].dropna().unique().tolist())
        )
    with ownership_col:
        ownership = st.selectbox("Ownership", ["All", ACCOUNT_MANAGED, SELF_SERVE])
    with at_risk_col:
        st.markdown("&nbsp;")
        at_risk_only = st.checkbox("At-risk only")

    filtered_df = _apply_filters(df, region, persona, ownership, at_risk_only)

    board_columns = st.columns(len(STAGES))
    for board_col, stage in zip(board_columns, STAGES):
        with board_col:
            stage_df = _stage_accounts(filtered_df, stage)
            st.markdown(f"**{stage} ({len(stage_df)})**")
            for _, row in stage_df.iterrows():
                _render_card(row)

# ---------------------------------------------------------------------
# VIEW 3 — Batch ingestion
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
            st.metric("Moved to follow-up", previous_summary["follow_up_count"])
            st.metric("Resolved", previous_summary["resolved_count"])
            st.metric("Unchanged", previous_summary["unchanged_count"])
        with after_col:
            st.markdown("**After this upload**")
            st.metric("New", new_summary["new_count"])
            st.metric("Moved to follow-up", new_summary["follow_up_count"])
            st.metric("Resolved", new_summary["resolved_count"])
            st.metric("Unchanged", new_summary["unchanged_count"])
