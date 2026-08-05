#!/usr/bin/env python3
"""Scheduled entry point: run the retention pipeline unattended against the
newest workbook dropped in data/incoming/, and write a morning briefing to
data/briefings/.

Intended to run once a day via cron (see README.md for the crontab line):

    python scheduled_run.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from src.briefing import generate_briefing_text
from src.pipeline import print_report, run_pipeline

INCOMING_DIR = Path("data/incoming")
BRIEFINGS_DIR = Path("data/briefings")


def _newest_workbook(incoming_dir: Path) -> Path | None:
    workbooks = sorted(incoming_dir.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    return workbooks[-1] if workbooks else None


def main() -> int:
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)

    filepath = _newest_workbook(INCOMING_DIR)
    if filepath is None:
        print(
            f"No workbook found in {INCOMING_DIR}/ — nothing to run. "
            "Drop a .xlsx file there and re-run."
        )
        return 0

    print(f"Running pipeline against {filepath}...")
    drafted_df, summary = run_pipeline(filepath)
    print_report(summary)

    briefing_text = generate_briefing_text(drafted_df, summary)
    briefing_path = BRIEFINGS_DIR / f"briefing_{date.today().isoformat()}.md"
    briefing_path.write_text(briefing_text)
    print(f"\nBriefing written to {briefing_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
