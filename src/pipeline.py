"""Orchestrate the daily retention pipeline: load, segment, draft, sync.

Thin glue only — all the actual logic lives in data_loader.py,
segmentation.py, nba.py, and state.py. This module just calls the four in
order and packages a summary for the morning report.

Run directly to see the report against the demo/data pipeline (from the
project root, so the package-relative imports resolve):

    python -m src.pipeline [path/to/workbook.xlsx]

`run.py` at the project root is the intended command-line entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from .data_loader import (
    DEFAULT_DEMO_PATH,
    STATUS_COLUMN,
    UNKNOWN_STATUS,
    _build_demo_workbook,
    _flag_and_drop_duplicates,
    _merge_idempotent,
    _resolve_trend_baseline,
    load_and_clean,
    load_new_accounts_batch,
)
from .segmentation import segment_accounts
from .nba import draft_actions
from .state import DEFAULT_DB_PATH, sync_state


def run_pipeline(filepath, db_path=DEFAULT_DB_PATH) -> tuple[pd.DataFrame, dict]:
    """Run the full daily pipeline: load+clean, segment, draft, sync state.

    Returns (final_df, summary) where final_df is the fully drafted
    DataFrame and summary is a flat dict for the morning report.
    """
    clean_df, load_report = load_and_clean(filepath)
    segmented_df = segment_accounts(clean_df)
    drafted_df = draft_actions(segmented_df)
    state_summary = sync_state(drafted_df, db_path=db_path)

    segment_counts = {
        str(segment): int(count)
        for segment, count in drafted_df["segment"].value_counts(dropna=False).items()
    }
    action_counts = {
        str(action): int(count)
        for action, count in drafted_df["action"].value_counts(dropna=False).items()
    }

    summary = {
        "rows_before_cleaning": load_report["rows_before_cleaning"],
        "rows_after_cleaning": load_report["rows_after_cleaning"],
        "accounts_excluded": load_report["accounts_excluded"],
        "segment_counts": segment_counts,
        "action_counts": action_counts,
        "new_count": state_summary["new_count"],
        "follow_up_count": state_summary["follow_up_count"],
        "resolved_count": state_summary["resolved_count"],
        "unchanged_count": state_summary["unchanged_count"],
    }

    return drafted_df, summary


def run_batch_pipeline(existing_df, filepath, db_path=DEFAULT_DB_PATH) -> tuple[pd.DataFrame, dict]:
    """Batch-uploader counterpart to run_pipeline(): merge a new-accounts
    batch (read via load_new_accounts_batch(), which tolerates any sheet
    layout) into the already-loaded portfolio df, then re-segment,
    re-draft, and re-sync state against the merged whole.

    Unlike run_pipeline(), the uploaded file doesn't need to be a full
    two-sheet workbook -- just the batch of new/updated rows.
    """
    new_df = load_new_accounts_batch(filepath)

    rows_before = len(existing_df) + len(new_df)
    merged, updated_ids, new_ids = _merge_idempotent(existing_df, new_df)
    merged[STATUS_COLUMN] = merged[STATUS_COLUMN].fillna(UNKNOWN_STATUS)
    merged, duplicate_flags = _flag_and_drop_duplicates(merged)
    merged, trend_flags = _resolve_trend_baseline(merged)

    segmented_df = segment_accounts(merged)
    drafted_df = draft_actions(segmented_df)
    state_summary = sync_state(drafted_df, db_path=db_path)

    segment_counts = {
        str(segment): int(count)
        for segment, count in drafted_df["segment"].value_counts(dropna=False).items()
    }
    action_counts = {
        str(action): int(count)
        for action, count in drafted_df["action"].value_counts(dropna=False).items()
    }

    summary = {
        "rows_before_cleaning": rows_before,
        "rows_after_cleaning": len(drafted_df),
        "accounts_excluded": [f["account_id"] for f in duplicate_flags],
        "segment_counts": segment_counts,
        "action_counts": action_counts,
        "new_count": state_summary["new_count"],
        "follow_up_count": state_summary["follow_up_count"],
        "resolved_count": state_summary["resolved_count"],
        "unchanged_count": state_summary["unchanged_count"],
    }

    return drafted_df, summary


def print_report(summary: dict) -> None:
    """Print a morning-glance report: data loaded, accounts segmented,
    messages drafted, and what changed in the state database."""
    print("=" * 60)
    print("DAILY RETENTION PIPELINE REPORT")
    print("=" * 60)

    print("\nData loaded:")
    print(f"  Rows before cleaning : {summary['rows_before_cleaning']}")
    print(f"  Rows after cleaning  : {summary['rows_after_cleaning']}")
    excluded = summary["accounts_excluded"]
    print(f"  Accounts excluded    : {len(excluded)} {excluded}")

    print("\nAccounts segmented:")
    for segment, count in summary["segment_counts"].items():
        print(f"  {segment:<28} {count}")

    print("\nMessages drafted (by action):")
    for action, count in summary["action_counts"].items():
        print(f"  {action:<28} {count}")

    print("\nState database changes since last run:")
    print(f"  New accounts            : {summary['new_count']}")
    print(f"  Moved to follow-up      : {summary['follow_up_count']}")
    print(f"  Resolved (no action)    : {summary['resolved_count']}")
    print(f"  Unchanged               : {summary['unchanged_count']}")
    print("=" * 60)


if __name__ == "__main__":
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEMO_PATH

    if not filepath.exists():
        print(f"No workbook found at {filepath}, generating a demo workbook there...")
        _build_demo_workbook(filepath)

    _, summary = run_pipeline(filepath)
    print_report(summary)
