#!/usr/bin/env python3
"""Command-line entry point for the daily retention pipeline.

    python run.py path/to/workbook.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.pipeline import print_report, run_pipeline


def main(argv: list[str]) -> int:
    if not argv:
        print("Usage: python run.py path/to/workbook.xlsx")
        return 1

    filepath = Path(argv[0])

    if not filepath.exists():
        print(f"File not found: {filepath}")
        return 1

    if filepath.suffix.lower() != ".xlsx":
        print(f"Expected an Excel (.xlsx) file, got: {filepath}")
        return 1

    _, summary = run_pipeline(filepath)
    print_report(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
