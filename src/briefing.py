"""Generate the "morning briefing" markdown text shown at the top of the
Overview view — a plain-English narrative plus a "since last run" digest.

Pure text generation only: `generate_briefing_text(df, summary)` takes the
drafted DataFrame and the summary dict `run_pipeline()` returns and returns
a markdown string. No file I/O, no Streamlit — `scheduled_run.py` writes the
result to disk, and `app.py` either reads that file or calls this directly.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd

from .scoring import compute_health_score

NO_ACTION = "None"
SELF_SERVE_ACTION = "Self-Serve Nudge"


def _top_struggle_factor(df: pd.DataFrame) -> str | None:
    """The most common negative health-score factor (e.g. "broker
    reliance") among accounts currently flagged at-risk. Falls back to
    accounts with any action assigned if none are at-risk. None if there's
    nothing to report on."""
    if "is_at_risk" in df.columns:
        pool = df[df["is_at_risk"] == True]  # noqa: E712
    else:
        pool = df.iloc[0:0]

    if pool.empty and "action" in df.columns:
        pool = df[df["action"] != NO_ACTION]

    if pool.empty:
        return None

    counts = Counter()
    for _, row in pool.iterrows():
        _, factors = compute_health_score(row)
        negative = [f for f in factors if f["impact"] == "negative"]
        if not negative:
            continue
        label = negative[0]["label"]
        category = label.split(":")[0].strip().lower()
        counts[category] += 1

    if not counts:
        return None
    return counts.most_common(1)[0][0]


def generate_briefing_text(df: pd.DataFrame, summary: dict) -> str:
    """Markdown briefing: a short narrative plus a "since last run" digest
    of what changed in the state database."""
    self_serve_movable = int((df["action"] == SELF_SERVE_ACTION).sum()) if "action" in df.columns else 0
    needs_follow_up = int((df["action"] != NO_ACTION).sum()) if "action" in df.columns else 0
    top_factor = _top_struggle_factor(df)

    narrative = (
        f"Your portfolio has {self_serve_movable} account(s) that could move to self-serve. "
        f"{needs_follow_up} need a follow-up today."
    )
    if top_factor:
        narrative += (
            f" The most common reason accounts are struggling right now is {top_factor}."
        )

    lines = ["## Morning Briefing", "", narrative, "", "### Since last run"]

    new_count = summary.get("new_count", 0)
    follow_up_count = summary.get("follow_up_count", 0)
    resolved_count = summary.get("resolved_count", 0)
    unchanged_count = summary.get("unchanged_count", 0)

    is_first_run = (
        new_count > 0
        and follow_up_count == 0
        and resolved_count == 0
        and unchanged_count == 0
    )

    if is_first_run:
        lines.append("This is the first run — nothing to compare yet.")
    else:
        lines.append(f"- New accounts: {new_count}")
        lines.append(f"- Moved to Follow-up: {follow_up_count}")
        lines.append(f"- Wins (improved to no action needed): {resolved_count}")
        lines.append(f"- Unchanged: {unchanged_count}")

    return "\n".join(lines)
