"""End-to-end retention pipeline: load, segment, draft outreach, and
reconcile against persistent state, in one call.

Run directly against the demo/data pipeline (from the project root, so the
package-relative imports resolve):

    python -m src.pipeline [path/to/workbook.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

from .data_loader import DEFAULT_DEMO_PATH, load_and_clean, _build_demo_workbook
from .nba import draft_actions
from .segmentation import segment_accounts
from .state import DEFAULT_DB_PATH, sync_state


def run_pipeline(filepath, db_path=DEFAULT_DB_PATH) -> dict:
    """Run the full pipeline against one workbook: load + clean, segment,
    draft outreach copy, then reconcile against the state DB.

    Returns {"df", "load_report", "sync_summary"}:
    - `df`: one row per account, carrying every column draft_actions()
      produces (account_id, segment, action, is_at_risk, at_risk_detail,
      draft_subject, draft_message, plus all original account fields).
    - `load_report`: the dict returned by data_loader.load_and_clean().
    - `sync_summary`: the dict returned by state.sync_state()
      ({"new_count", "reset_to_pending_count", "unchanged_count"}).
    """
    clean_df, load_report = load_and_clean(filepath)
    segmented_df = segment_accounts(clean_df)
    drafted_df = draft_actions(segmented_df)
    sync_summary = sync_state(drafted_df, db_path=db_path)

    return {
        "df": drafted_df,
        "load_report": load_report,
        "sync_summary": sync_summary,
    }


if __name__ == "__main__":
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEMO_PATH

    if not filepath.exists():
        print(f"No workbook found at {filepath}, generating a demo workbook there...")
        _build_demo_workbook(filepath)

    result = run_pipeline(filepath)
    print(f"Pipeline ran on {len(result['df'])} accounts.")
    print(f"Load report: {result['load_report']}")
    print(f"Sync summary: {result['sync_summary']}")
