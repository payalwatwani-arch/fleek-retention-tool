"""Draft next-best-action outreach copy for segmented accounts.

Takes the DataFrame produced by `segmentation.segment_accounts()` (which
carries `segment`, `action`, `is_at_risk`, `at_risk_detail`, plus all the
original account columns) and adds `draft_subject` / `draft_message`
columns — one drafting template per action type.

Run directly against the demo/data pipeline to see example drafts (from
the project root, so the package-relative imports resolve):

    python -m src.nba [path/to/workbook.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from .data_loader import DEFAULT_DEMO_PATH, load_and_clean, _build_demo_workbook
from .segmentation import segment_accounts

# Actions whose template is itself the at-risk message — appending the
# generic "also showing declining spend" sentence to these would be
# redundant, since is_at_risk is always True for rows carrying them.
AT_RISK_APPEND_EXEMPT = {"Win-back play", "Retention check-in", "None"}


def _account_label(row) -> str:
    name = row.get("name")
    if name is not None and not pd.isna(name):
        return str(name)
    return str(row["account_id"])


def _pct(value, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "an unclear amount"
    return f"{value:.{decimals}f}%"


def _draft_migration_play(row) -> tuple[str, str]:
    label = _account_label(row)
    broker_pct = _pct(row.get("broker_reliance_pct"))
    subject = f"A faster way to order for {label}"
    message = (
        f"Hi {label} team,\n\n"
        f"We noticed about {broker_pct} of your recent orders are still going through your account manager. "
        f"We'd love for you to try ordering directly in the product instead — it's built to be quicker for "
        f"routine orders, so you're not waiting on a reply to move. Your AM isn't going anywhere; they're still "
        f"there for anything that needs a human. Want a two-minute walkthrough of self-serve ordering?\n\n"
        f"Best,\nThe Fleek Team"
    )
    return subject, message


def _draft_win_back_play(row) -> tuple[str, str]:
    label = _account_label(row)
    subject = f"We'd love to have {label} back"
    message = (
        f"Hi {label} team,\n\n"
        f"We noticed your spend has dropped all the way to zero over the past few months, and honestly, we miss "
        f"having you order with us. If something changed on your end, or something we did made ordering harder "
        f"than it should be, we'd genuinely like to hear it and make it right. No pressure — just know the door's "
        f"wide open whenever you're ready to come back.\n\n"
        f"Warmly,\nThe Fleek Team"
    )
    return subject, message


def _draft_retention_checkin(row) -> tuple[str, str]:
    label = _account_label(row)
    trend = row.get("gmv_trend_pct")
    drop_pct = _pct(abs(trend)) if trend is not None and not pd.isna(trend) else "a noticeable amount"
    subject = f"Checking in with {label}"
    message = (
        f"Hi {label} team,\n\n"
        f"We noticed your order volume is down about {drop_pct} over the past few months and wanted to check in "
        f"early, before it goes further. Has anything changed on your end — pricing, product fit, a point of "
        f"contact — that we should know about? Happy to jump on a quick call if that's easier than writing it out.\n\n"
        f"Best,\nThe Fleek Team"
    )
    return subject, message


def _draft_bundle_nudge(row) -> tuple[str, str]:
    label = _account_label(row)
    bundle_pct = _pct(row.get("bundle_gmv_share_pct"))
    subject = "A quicker way to order: bundles"
    message = (
        f"Hi {label} team, only {bundle_pct} of your recent GMV has gone through bundles so far. Bundling your "
        f"frequently-ordered items together is a quick way to save time on your next order — want to give it a try?"
    )
    return subject, message


def _draft_offer_tool_nudge(row) -> tuple[str, str]:
    label = _account_label(row)
    subject = "Try the make-an-offer tool"
    message = (
        f"Hi {label} team, did you know you can make an offer directly in the product instead of ordering at "
        f"list price? It's a fast way to negotiate on items you're already browsing — worth a shot on your next order."
    )
    return subject, message


def _draft_chat_call_nudge(row) -> tuple[str, str]:
    label = _account_label(row)
    subject = "Got a minute to chat?"
    message = (
        f"Hi {label} team, we noticed you've been ordering steadily but haven't used chat or hopped on a call "
        f"with us yet. Want to grab 15 minutes, or just drop a message if anything's come up lately?"
    )
    return subject, message


ACTION_TEMPLATES = {
    "Migration play": _draft_migration_play,
    "Win-back play": _draft_win_back_play,
    "Retention check-in": _draft_retention_checkin,
    "Bundle nudge": _draft_bundle_nudge,
    "Offer tool nudge": _draft_offer_tool_nudge,
    "Chat/call nudge": _draft_chat_call_nudge,
}

AT_RISK_APPEND_SENTENCE = (
    "One more thing worth flagging: this account is also showing declining spend, "
    "so it may need attention beyond this message alone."
)


def draft_actions(df: pd.DataFrame) -> pd.DataFrame:
    """Add `draft_subject` and `draft_message` columns, one template per
    `action` value. "None" actions get null drafts. Accounts flagged
    `is_at_risk` under a non-at-risk primary action get one extra sentence
    appended noting the decline, without changing which template ran."""
    df = df.copy()
    subjects: list[str | None] = []
    messages: list[str | None] = []

    for _, row in df.iterrows():
        action = row["action"]
        template = ACTION_TEMPLATES.get(action)

        if template is None:
            subjects.append(None)
            messages.append(None)
            continue

        subject, message = template(row)

        if bool(row.get("is_at_risk")) and action not in AT_RISK_APPEND_EXEMPT:
            message = f"{message}\n\n{AT_RISK_APPEND_SENTENCE}"

        subjects.append(subject)
        messages.append(message)

    df["draft_subject"] = subjects
    df["draft_message"] = messages
    return df


def _print_summary(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("DRAFT SUMMARY")
    print("=" * 60)

    print("\nDrafts generated per action type:")
    action_counts = df["action"].value_counts(dropna=False)
    for action, count in action_counts.items():
        has_draft = df.loc[df["action"] == action, "draft_message"].notna().sum()
        print(f"  {str(action):<28} {count} accounts, {has_draft} drafted")

    with_appended_note = (
        (df["action"] != "None")
        & df["draft_message"].notna()
        & df["draft_message"].str.contains(AT_RISK_APPEND_SENTENCE, regex=False)
    )
    print(f"\nDrafts with the extra at-risk sentence appended: {int(with_appended_note.sum())}")

    print("\n" + "=" * 60)
    print("EXAMPLE DRAFTS")
    print("=" * 60)

    drafted = df[df["draft_message"].notna()]
    examples = []
    seen_actions = set()
    # Prefer one example per distinct action, then one that has the
    # appended at-risk sentence, so the samples cover different tones.
    for _, row in drafted.iterrows():
        if row["action"] not in seen_actions:
            examples.append(row)
            seen_actions.add(row["action"])
        if len(examples) >= 3:
            break

    appended_example = df[with_appended_note]
    if not appended_example.empty and not any(
        r["account_id"] == appended_example.iloc[0]["account_id"] for r in examples
    ):
        if len(examples) >= 3:
            examples[-1] = appended_example.iloc[0]
        else:
            examples.append(appended_example.iloc[0])

    for row in examples:
        print(f"\n--- {row['account_id']}  |  segment: {row['segment']}  |  action: {row['action']} ---")
        print(f"Subject: {row['draft_subject']}")
        print(row["draft_message"])

    print("\n" + "=" * 60)


if __name__ == "__main__":
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEMO_PATH

    if not filepath.exists():
        print(f"No workbook found at {filepath}, generating a demo workbook there...")
        _build_demo_workbook(filepath)

    clean_df, _report = load_and_clean(filepath)
    segmented_df = segment_accounts(clean_df)
    drafted_df = draft_actions(segmented_df)
    _print_summary(drafted_df)
