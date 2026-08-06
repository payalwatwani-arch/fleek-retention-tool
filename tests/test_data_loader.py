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

from src.data_loader import REQUIRED_ACCOUNT_COLUMNS, load_and_clean, load_new_accounts_batch

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


def _full_account_rows(ids) -> pd.DataFrame:
    """Rows carrying every column REQUIRED_ACCOUNT_COLUMNS lists, filled
    with values that clear load_and_clean's cleaning steps cleanly (a
    baseline-eligible trend, a real account_status) so these tests only
    exercise sheet selection, not the cleaning logic covered elsewhere."""
    n = len(ids)
    data = {col: [f"{col}-val"] * n for col in REQUIRED_ACCOUNT_COLUMNS}
    data.update(
        {
            "account_id": list(ids),
            "gmv_total_6m": [1000.0] * n,
            "gmv_sep": [1000.0] * n,
            "gmv_trend_pct": [0.0] * n,
            "tenure_months": [12] * n,
            "account_status": ["Active"] * n,
        }
    )
    return pd.DataFrame(data)


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


class TestLoadAndCleanSheetSelection:
    """load_and_clean()'s base-dataset sheet selection: same "check columns,
    not just names" principle as load_new_accounts_batch(), but validated
    against the full account schema (REQUIRED_ACCOUNT_COLUMNS) since the
    rest of the pipeline -- not just load_and_clean itself -- depends on it."""

    def test_uses_accounts_sheet_by_name_when_present(self, tmp_path):
        path = tmp_path / "book.xlsx"
        accounts = _full_account_rows(["ACC-001", "ACC-002"])
        new_accounts = _full_account_rows(["ACC-003"])
        _write_workbook(path, {"Accounts": accounts, "new_accounts": new_accounts})

        clean_df, report = load_and_clean(path)

        assert set(clean_df["account_id"]) == {"ACC-001", "ACC-002", "ACC-003"}
        assert report["accounts_new_count"] == 1

    def test_uses_accounts_sheet_when_new_accounts_absent(self, tmp_path):
        """A workbook with only an `Accounts` sheet (correctly named, no
        `new_accounts` companion) loads successfully as the base dataset,
        with 0 new accounts this run -- same principle as the single
        arbitrarily-named sheet case, but for the Accounts-named sheet."""
        path = tmp_path / "book.xlsx"
        accounts = _full_account_rows(["ACC-001", "ACC-002"])
        _write_workbook(path, {"Accounts": accounts})

        clean_df, report = load_and_clean(path)

        assert set(clean_df["account_id"]) == {"ACC-001", "ACC-002"}
        assert report["accounts_new_count"] == 0
        assert report["accounts_updated_count"] == 0

    def test_uses_single_arbitrarily_named_sheet_with_full_schema(self, tmp_path):
        path = tmp_path / "book.xlsx"
        rows = _full_account_rows(["ACC-001", "ACC-002"])
        _write_workbook(path, {"Q3 Export": rows})

        clean_df, report = load_and_clean(path)

        assert set(clean_df["account_id"]) == {"ACC-001", "ACC-002"}
        assert report["accounts_new_count"] == 0
        assert report["accounts_updated_count"] == 0

    def test_raises_when_single_sheet_missing_required_columns(self, tmp_path):
        path = tmp_path / "book.xlsx"
        _write_workbook(path, {"Sheet1": SAMPLE_ROWS})

        with pytest.raises(ValueError) as excinfo:
            load_and_clean(path)

        assert "Sheet1" in str(excinfo.value)
        assert "gmv_trend_pct" in str(excinfo.value)

    def test_raises_clear_error_on_ambiguous_sheets(self, tmp_path):
        path = tmp_path / "book.xlsx"
        rows = _full_account_rows(["ACC-001"])
        _write_workbook(path, {"January": rows, "February": rows})

        with pytest.raises(ValueError) as excinfo:
            load_and_clean(path)

        assert "January" in str(excinfo.value)
        assert "February" in str(excinfo.value)
