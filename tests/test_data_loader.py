"""Tests for `load_new_accounts_batch()`, the Import view's batch-uploader
sheet-selection logic.

Unlike `load_and_clean()` (which requires a workbook with both `Accounts`
and `new_accounts` sheets), the batch uploader accepts any of:

  - a sheet literally named `new_accounts`
  - a sheet literally named `Accounts` (a full combined workbook uploaded
    as a "batch", treating its Accounts data as the batch)
  - exactly one sheet, regardless of its name
  - otherwise: a clear error listing the sheet names found
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data_loader import load_new_accounts_batch

SAMPLE_ROWS = pd.DataFrame(
    {
        "account_id": ["ACC-001", "ACC-002"],
        "gmv_total_6m": [1000.0, 2000.0],
        "broker_reliance_pct": [10.0, 60.0],
    }
)


def _write_workbook(path, sheets: dict) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def test_reads_new_accounts_sheet_when_present(tmp_path):
    path = tmp_path / "batch.xlsx"
    _write_workbook(path, {"new_accounts": SAMPLE_ROWS})

    result = load_new_accounts_batch(path)

    assert list(result["account_id"]) == ["ACC-001", "ACC-002"]


def test_reads_accounts_sheet_when_new_accounts_absent(tmp_path):
    """A full combined workbook (just an `Accounts` sheet, no
    `new_accounts`) can be uploaded as a batch too -- its Accounts data is
    treated as this batch."""
    path = tmp_path / "batch.xlsx"
    _write_workbook(path, {"Accounts": SAMPLE_ROWS})

    result = load_new_accounts_batch(path)

    assert list(result["account_id"]) == ["ACC-001", "ACC-002"]


def test_new_accounts_sheet_preferred_over_accounts(tmp_path):
    """When both sheets are present, `new_accounts` wins."""
    path = tmp_path / "batch.xlsx"
    other_rows = pd.DataFrame({"account_id": ["ACC-099"], "gmv_total_6m": [500.0], "broker_reliance_pct": [5.0]})
    _write_workbook(path, {"Accounts": other_rows, "new_accounts": SAMPLE_ROWS})

    result = load_new_accounts_batch(path)

    assert list(result["account_id"]) == ["ACC-001", "ACC-002"]


def test_reads_single_arbitrarily_named_sheet(tmp_path):
    """A standalone file with just new-account rows on one sheet, named
    anything, is accepted."""
    path = tmp_path / "batch.xlsx"
    _write_workbook(path, {"Sheet1": SAMPLE_ROWS})

    result = load_new_accounts_batch(path)

    assert list(result["account_id"]) == ["ACC-001", "ACC-002"]


def test_raises_clear_error_on_ambiguous_sheets(tmp_path):
    """Two sheets, neither named `new_accounts` or `Accounts`: genuinely
    ambiguous, so this should fail with a helpful message listing the
    sheet names rather than a cryptic pandas/openpyxl error."""
    path = tmp_path / "batch.xlsx"
    _write_workbook(path, {"January": SAMPLE_ROWS, "February": SAMPLE_ROWS})

    with pytest.raises(ValueError) as excinfo:
        load_new_accounts_batch(path)

    assert "January" in str(excinfo.value)
    assert "February" in str(excinfo.value)
