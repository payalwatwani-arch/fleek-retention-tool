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

import pandas as pd
import streamlit as st

from src.pipeline import run_pipeline
from src.scoring import compute_health_score
from src.segmentation import ACCOUNT_MANAGED, SELF_SERVE
from src.state import (
    DEFAULT_DB_PATH,
    STAGE_ACTIONED,
    STAGE_FOLLOW_UP,
    STAGE_NEW,
    add_note,
    get_by_stage,
    get_notes,
    mark_actioned,
    undo_action,
)

WORKBOOK_PATH = Path("data/raw/portfolio.xlsx")
BATCH_UPLOAD_PATH = Path("data/raw/batch_upload.xlsx")

st.set_page_config(page_title="Fleek Retention Dashboard", layout="wide")

# ---------------------------------------------------------------------
# Brand theme: fonts + the sage/mustard/rust semantic colors used by the
# tag/badge/stepper helpers below. Base widget colors (background, primary
# accent, text) come from .streamlit/config.toml instead, since Streamlit
# picks those up natively.
# ---------------------------------------------------------------------
THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo+Black&family=Space+Grotesk:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Space Grotesk', sans-serif;
}
h1, h2, h3 {
    font-family: 'Archivo Black', sans-serif !important;
    letter-spacing: 0.01em;
}

/* Segment tags */
.tag {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    margin-right: 4px;
}
.tag-sage    { background: #E4EDE5; color: #3F5B43; border: 1px solid #6B8F71; }
.tag-mustard { background: #FBEFC2; color: #7A5C00; border: 1px solid #D9A800; }
.tag-rust    { background: #F6DCD2; color: #7A2F16; border: 1px solid #C1502E; }

/* Health score badge */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-weight: 700;
}
.badge-sage    { background: #6B8F71; color: #FDFBF6; }
.badge-mustard { background: #D9A800; color: #FDFBF6; }
.badge-rust    { background: #C1502E; color: #FDFBF6; }

/* Health score factor breakdown */
.badge-inline {
    display: inline-block;
    font-weight: 700;
}
.badge-inline-sage { color: #6B8F71; }
.badge-inline-rust { color: #C1502E; }

/* Stage stepper */
.stepper-step {
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
}
.stepper-current   { color: #1C1A17; background: rgba(245, 196, 0, 0.30); }
.stepper-completed { color: #4A4640; }
.stepper-upcoming  { color: #B9B2A6; }

/* Card / notes containers, matching the Paper card background */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background-color: #F6F1E7;
    border-color: #D9A800 !important;
    border-radius: 10px;
}
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

# Matches a trailing email sign-off paragraph like "Best,\nThe Fleek Team" or
# "Regards,\nThe Fleek Team" so it can be dropped from the Text tab — text
# messages don't carry email-style sign-offs.
_SIGNOFF_RE = re.compile(r"^\w+,\s*The Fleek Team$", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+")
# Roughly the point a message stops reading like a text and starts reading
# like a forwarded email; past this, the Text tab trims to opening + ask.
_TEXT_LENGTH_THRESHOLD = 280


def _strip_signoff(message: str) -> list[str]:
    """Paragraphs of a drafted email-style message with the trailing
    sign-off paragraph dropped."""
    paragraphs = [p.strip() for p in message.strip().split("\n\n") if p.strip()]
    return [p for p in paragraphs if not _SIGNOFF_RE.match(re.sub(r"\s+", " ", p))]


def _format_for_text(message: str) -> str:
    """Reformat a drafted email-style message into a short, casual text:
    sign-off dropped, and if what's left still runs long for a text,
    trimmed to its opening line plus its closing ask."""
    paragraphs = _strip_signoff(message)
    normalized = re.sub(r"\s+", " ", " ".join(paragraphs)).strip()

    if len(normalized) <= _TEXT_LENGTH_THRESHOLD:
        return normalized

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(normalized) if s.strip()]
    if len(sentences) <= 2:
        return normalized
    return " ".join([sentences[0], sentences[-1]])


def _pct(value, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "an unclear amount"
    return f"{value:.{decimals}f}%"


def _call_script_migration_play(row) -> dict[str, str]:
    broker_pct = _pct(row.get("broker_reliance_pct"))
    return {
        "opening": f"Mention: {broker_pct} of their orders still go through their AM.",
        "key_point": (
            "Ordering directly in the product is faster for routine orders — no waiting on a "
            "reply. Their AM is still there for anything that needs a human."
        ),
        "pushback": (
            "If they worry about losing support, reassure them their AM relationship doesn't "
            "change — this just adds a faster option for routine reorders."
        ),
        "close": "Offer a two-minute walkthrough of self-serve ordering.",
    }


def _call_script_win_back(row) -> dict[str, str]:
    return {
        "opening": "Mention: their spend has dropped to $0 over the past few months.",
        "key_point": (
            "We want to know if something changed, or if we made ordering harder than it "
            "should be — happy to fix it."
        ),
        "pushback": (
            "If they raise a specific frustration, acknowledge it directly and offer to loop "
            "in the right person to resolve it."
        ),
        "close": "Ask what it would take to get them ordering again, and offer a follow-up call.",
    }


def _call_script_retention_checkin(row) -> dict[str, str]:
    trend = row.get("gmv_trend_pct")
    drop_pct = _pct(abs(trend)) if trend is not None and not pd.isna(trend) else "a noticeable amount"
    return {
        "opening": f"Mention: order volume is down about {drop_pct} over the past few months.",
        "key_point": (
            "Wanted to flag it early — ask if anything's changed: pricing, product fit, "
            "point of contact."
        ),
        "pushback": (
            "If they say everything's fine, gently probe for a specific reason — early "
            "signals like this are usually explainable."
        ),
        "close": "Offer a quick follow-up call to talk it through.",
    }


def _call_script_bundle_nudge(row) -> dict[str, str]:
    bundle_pct = _pct(row.get("bundle_gmv_share_pct"))
    return {
        "opening": f"Mention: only {bundle_pct} of their GMV has gone through bundles.",
        "key_point": "Bundling their frequently-ordered items saves time on their next order.",
        "pushback": "If they're unsure how it works, offer to walk them through it right now.",
        "close": "Ask if they want to set up their first bundle together on the call.",
    }


def _call_script_offer_tool_nudge(row) -> dict[str, str]:
    return {
        "opening": "Mention: they've been ordering at list price without trying the make-an-offer tool.",
        "key_point": "They can negotiate pricing directly in the product on items they're already browsing.",
        "pushback": (
            "If they think it sounds too good to be true, reassure them it's a standard "
            "pricing tool on the platform."
        ),
        "close": "Ask if they want to try it live on an item during the call.",
    }


def _call_script_build_a_bundle_nudge(row) -> dict[str, str]:
    handpick = row.get("handpick_orders")
    bundled = row.get("bundle_orders")
    handpick_str = "an unclear number of" if handpick is None or pd.isna(handpick) else f"{handpick:.0f}"
    bundled_str = "an unclear number of" if bundled is None or pd.isna(bundled) else f"{bundled:.0f}"
    return {
        "opening": (
            f"Mention: {handpick_str} handpicked orders in the last 6 months vs. "
            f"{bundled_str} through pre-made bundles."
        ),
        "key_point": (
            "The build-a-bundle tool lets them save their own selections as a reusable "
            "bundle for one-click reordering."
        ),
        "pushback": (
            "If they say their picks vary too much to bundle, point out it's editable any "
            "time — not a fixed list."
        ),
        "close": "Offer to set up their first custom bundle together on the call.",
    }


def _call_script_chat_nudge(row) -> dict[str, str]:
    return {
        "opening": "Mention: they've been ordering steadily but haven't used chat with us yet.",
        "key_point": "Chat is there for quick questions on sizing, availability, or anything else.",
        "pushback": "If they say they'd rather call, let them know chat is just another option, not a replacement.",
        "close": "Ask them to try it on their next question, big or small.",
    }


def _call_script_video_call_nudge(row) -> dict[str, str]:
    return {
        "opening": (
            "Mention: they've been ordering steadily but haven't done a video call with us "
            "yet (0 so far)."
        ),
        "key_point": (
            "A quick 15-minute video call is a chance for a live walkthrough or to talk "
            "through their account."
        ),
        "pushback": "If they're pressed for time, offer to keep it to 15 minutes or less.",
        "close": "Offer to grab 15 minutes on video this week.",
    }


CALL_SCRIPTS = {
    "Self-Serve Nudge": _call_script_migration_play,
    "Win-back play": _call_script_win_back,
    "Retention check-in": _call_script_retention_checkin,
    "Build-a-Bundle nudge": _call_script_build_a_bundle_nudge,
    "Bundle nudge": _call_script_bundle_nudge,
    "Offer tool nudge": _call_script_offer_tool_nudge,
    "Chat nudge": _call_script_chat_nudge,
    "Video call nudge": _call_script_video_call_nudge,
}

CALL_SCRIPT_LABELS = [
    ("opening", "Opening line"),
    ("key_point", "Key point to make"),
    ("pushback", "If they push back"),
    ("close", "Close with"),
]


def _render_call_script(row, action: str) -> None:
    """Talking-points script for the AM to use on an actual phone call —
    not a message to send, so no copy button."""
    builder = CALL_SCRIPTS.get(action)
    if builder is None:
        st.caption("No call script available for this action yet.")
        return
    script = builder(row)
    for field_key, label in CALL_SCRIPT_LABELS:
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.write(script[field_key])


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
    """Brand color tier for a health score: sage (healthy), mustard
    (watch), rust (at-risk)."""
    if score >= 70:
        return "sage"
    if score >= 40:
        return "mustard"
    return "rust"


def _score_badge_html(score: int, arrow: str) -> str:
    color = _score_color(score)
    return f'<span class="badge badge-{color}">[{score}] {arrow}</span>'


def _tag_category(tag: str) -> str:
    """Brand color tier for a segment/at-risk-detail tag: rust for urgent
    (at-risk) signals, mustard for a growth opportunity, sage for
    everything else (healthy or a routine nudge)."""
    if tag in ("Already Gone", "Declining"):
        return "rust"
    if tag == "Growth Headroom":
        return "mustard"
    return "sage"


def _tag_html(tag: str) -> str:
    category = _tag_category(tag)
    return f'<span class="tag tag-{category}">{html.escape(str(tag))}</span>'


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
    """One Kanban card: a compact summary that navigates to the account's
    dedicated Account Overview page when clicked, rather than expanding
    inline (accounts_details() is what used to live here)."""
    account_id = row["account_id"]
    score, factors = compute_health_score(row)
    arrow = "↑" if (factors[0]["direction"] == "up" if factors else True) else "↓"

    tags = " ".join(_tag_html(tag) for tag in _segment_tags(row))
    status_line = _status_line(row)

    label = f"**{account_id}**  ·  {_score_badge_html(score, arrow)}  ·  {tags}"
    if status_line:
        label += f"  ·  {status_line}"

    with st.container(border=True):
        st.markdown(label, unsafe_allow_html=True)
        if st.button("View details →", key=f"view_{account_id}", use_container_width=True):
            st.session_state.selected_account = account_id
            st.rerun()


def _account_with_state(df: pd.DataFrame, account_id: str):
    """Look up one account by id, joined with its stage bookkeeping,
    regardless of the Kanban board's current filters — used by the Account
    Overview page, which should stay reachable even if a filter would have
    hidden the card that linked to it. Returns None if not found."""
    all_state = pd.concat(
        [get_by_stage(stage, db_path=DEFAULT_DB_PATH) for stage in STAGES],
        ignore_index=True,
    )
    merged = df.merge(
        all_state[["account_id", "status", "touch_count", "actioned_date"]],
        on="account_id",
        how="inner",
    )
    match = merged[merged["account_id"] == account_id]
    if match.empty:
        return None
    return match.iloc[0]


def _render_stage_stepper(current_stage: str) -> None:
    current_index = STAGES.index(current_stage)
    steps = []
    for i, stage in enumerate(STAGES):
        if i < current_index:
            css_class, dot = "stepper-completed", "●"
        elif i == current_index:
            css_class, dot = "stepper-current", "●"
        else:
            css_class, dot = "stepper-upcoming", "○"
        steps.append(f'<span class="stepper-step {css_class}">{dot} {stage}</span>')
    st.markdown(
        "&nbsp;&nbsp;→&nbsp;&nbsp;".join(steps), unsafe_allow_html=True
    )


def _render_account_overview(row) -> None:
    """The dedicated Account Overview page a Kanban card navigates to."""
    account_id = row["account_id"]
    score, factors = compute_health_score(row)
    arrow = "↑" if (factors[0]["direction"] == "up" if factors else True) else "↓"

    if st.button("← Back to Pipeline", key="back_to_pipeline"):
        st.session_state.selected_account = None
        st.rerun()

    st.title(account_id)
    st.markdown(
        f"## {_score_badge_html(score, arrow)}  Health Score", unsafe_allow_html=True
    )
    st.caption(f"{row['ownership']} · {row['region']} · {row['buyer_persona']}")

    _render_stage_stepper(row["status"])

    st.markdown(
        " ".join(_tag_html(tag) for tag in _segment_tags(row)), unsafe_allow_html=True
    )
    status_line = _status_line(row)
    if status_line:
        st.caption(status_line)

    if row["is_at_risk"]:
        st.warning(f"At risk: {row['at_risk_detail']}")

    st.divider()
    st.subheader("Account numbers")
    trend_display = (
        f"{row['gmv_trend_pct']:.0f}%" if row["has_trend_baseline"] else "No baseline"
    )
    account_numbers = [
        ("Region", f"{row['region']}"),
        ("Ownership", f"{row['ownership']}"),
        ("GMV (6m)", f"${row['gmv_total_6m']:,.0f}"),
        ("Orders (6m)", f"{row['orders_6m']:,.0f}"),
        ("App active days", f"{row['app_active_days_6m']:.0f}"),
        ("Broker reliance", f"{row['broker_reliance_pct']:.0f}%"),
        ("PDP views (6m)", f"{row['pdp_views_6m']:.0f}"),
        ("Bundle share", f"{row['bundle_gmv_share_pct']:.0f}%"),
        ("GMV trend", trend_display),
        ("Make an offer (6m)", f"{row['make_an_offer_6m']:.0f}"),
    ]
    left_col, right_col = st.columns(2)
    for i, (num_label, num_value) in enumerate(account_numbers):
        target_col = left_col if i % 2 == 0 else right_col
        target_col.markdown(f"{num_label}: **{num_value}**")

    st.divider()
    st.subheader("Health score breakdown")
    for factor in factors:
        factor_arrow = "↑" if factor["direction"] == "up" else "↓"
        factor_color = "sage" if factor["impact"] == "positive" else "rust"
        st.markdown(
            f'<span class="badge-inline badge-inline-{factor_color}">{factor_arrow}</span> '
            f'{html.escape(factor["label"])}',
            unsafe_allow_html=True,
        )

    st.divider()
    if row["action"] == NO_ACTION:
        st.caption("No action needed.")
    else:
        st.subheader("Drafted outreach")
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

        email_tab, text_tab, call_tab = st.tabs(["Email", "Text", "Call"])

        with email_tab:
            st.text_input(
                "Subject", value=variant["subject"], key=f"subject_{account_id}_{tone}"
            )
            st.text_area(
                "Message",
                value=variant["message"],
                height=250,
                key=f"message_{account_id}_{tone}",
            )

        with text_tab:
            st.caption("Copy for Text:")
            st.code(_format_for_text(variant["message"]), language=None)

        with call_tab:
            _render_call_script(row, row["action"])

    st.divider()
    st.subheader("Notes")
    with st.container(border=True):
        with st.form(key=f"note_form_{account_id}", clear_on_submit=True):
            new_note = st.text_area("Add a note", key=f"new_note_{account_id}")
            submitted = st.form_submit_button("Add note", key=f"submit_note_{account_id}")
            if submitted and new_note.strip():
                add_note(account_id, new_note, db_path=DEFAULT_DB_PATH)
                st.rerun()

        notes = get_notes(account_id, db_path=DEFAULT_DB_PATH)
        if notes:
            for note in notes:
                st.markdown(f"**{note['timestamp']}**")
                st.write(note["text"])
        else:
            st.caption("No notes yet.")

    # "No action needed" accounts are driven purely by the pipeline's own
    # resolution (segment/action moving them to a neutral state) -- same as
    # the old inline card, there's no manual Mark as Actioned/Undo for them.
    if row["action"] != NO_ACTION:
        st.divider()
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

st.title("Fleek Retention Engine")
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
    selected_account = st.session_state.get("selected_account")

    if selected_account:
        account_row = _account_with_state(df, selected_account)
        if account_row is None:
            st.session_state.selected_account = None
            st.rerun()
        else:
            _render_account_overview(account_row)
    else:
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
