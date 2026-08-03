"""Load, clean, and idempotently merge account data for the retention tool.

Run directly to see a demo report:

    python src/data_loader.py [path/to/workbook.xlsx]

If no workbook exists at the given (or default demo) path, a synthetic one
is generated so the pipeline and report can be inspected end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ACCOUNTS_SHEET = "Accounts"
NEW_ACCOUNTS_SHEET = "new_accounts"

KEY_COLUMN = "account_id"
STATUS_COLUMN = "account_status"
DUPLICATE_STATUS = "Duplicate"
UNKNOWN_STATUS = "Unknown"

TREND_COLUMN = "gmv_trend_pct"
TENURE_COLUMN = "tenure_months"
SEPTEMBER_GMV_COLUMN = "gmv_sep"
MIN_TENURE_FOR_TREND = 6

DEFAULT_DEMO_PATH = Path("data/raw/demo_accounts.xlsx")


def _count_blanks(df: pd.DataFrame) -> dict:
    return {col: int(df[col].isna().sum()) for col in df.columns}


def _merge_idempotent(accounts_df: pd.DataFrame, new_df: pd.DataFrame, key: str = KEY_COLUMN):
    """Upsert new_df into accounts_df on `key`. Existing ids are updated in
    place (not duplicated), unseen ids are appended."""
    existing_ids = set(accounts_df[key])
    incoming_ids = set(new_df[key])

    updated_ids = sorted(existing_ids & incoming_ids)
    brand_new_ids = sorted(incoming_ids - existing_ids)

    untouched = accounts_df[~accounts_df[key].isin(incoming_ids)]
    merged = pd.concat([untouched, new_df], ignore_index=True, sort=False)
    return merged, updated_ids, brand_new_ids


def _flag_and_drop_duplicates(df: pd.DataFrame):
    """account_status == 'Duplicate' rows are excluded from the main output
    but logged, so they don't just silently vanish."""
    is_dup = df[STATUS_COLUMN] == DUPLICATE_STATUS
    flags = [
        {"account_id": row[KEY_COLUMN], "reason": "account_status=Duplicate"}
        for _, row in df[is_dup].iterrows()
    ]
    return df[~is_dup].reset_index(drop=True), flags


def _resolve_trend_baseline(df: pd.DataFrame):
    """gmv_trend_pct is legitimately un-computable (not just missing) when
    tenure_months < 6 (no September baseline yet) or gmv_sep == 0 (can't take
    a % change from zero). Those stay null rather than 0, since 0 would read
    as "flat" instead of "no trend to measure". has_trend_baseline lets
    downstream code branch on that without re-deriving the rule. A blank
    trend that ISN'T explained by either reason is a real data gap and gets
    flagged separately instead of being filled or ignored."""
    df = df.copy()
    no_baseline = (df[TENURE_COLUMN] < MIN_TENURE_FOR_TREND) | (df[SEPTEMBER_GMV_COLUMN] == 0)
    df["has_trend_baseline"] = ~no_baseline

    trend_blank = df[TREND_COLUMN].isna()
    unexplained = trend_blank & df["has_trend_baseline"]
    flags = [
        {"account_id": row[KEY_COLUMN], "reason": "gmv_trend_pct blank with no baseline exception"}
        for _, row in df[unexplained].iterrows()
    ]
    return df, flags


def load_and_clean(filepath) -> tuple[pd.DataFrame, dict]:
    """Read the Accounts + new_accounts tabs, merge new_accounts in
    idempotently, and clean blanks with intent. Returns (clean_df, report)."""
    accounts_df = pd.read_excel(filepath, sheet_name=ACCOUNTS_SHEET)
    new_df = pd.read_excel(filepath, sheet_name=NEW_ACCOUNTS_SHEET)

    rows_before = len(accounts_df) + len(new_df)

    merged, updated_ids, new_ids = _merge_idempotent(accounts_df, new_df)

    blanks_per_column = _count_blanks(merged)

    merged[STATUS_COLUMN] = merged[STATUS_COLUMN].fillna(UNKNOWN_STATUS)

    merged, duplicate_flags = _flag_and_drop_duplicates(merged)
    merged, trend_flags = _resolve_trend_baseline(merged)

    data_quality_flags = duplicate_flags + trend_flags

    report = {
        "rows_before_cleaning": rows_before,
        "rows_after_cleaning": len(merged),
        "blanks_per_column": blanks_per_column,
        "accounts_excluded_count": len(duplicate_flags),
        "accounts_excluded": [f["account_id"] for f in duplicate_flags],
        "accounts_unexplained_trend_blank_count": len(trend_flags),
        "accounts_updated_count": len(updated_ids),
        "accounts_updated": updated_ids,
        "accounts_new_count": len(new_ids),
        "accounts_new": new_ids,
        "data_quality_flags": data_quality_flags,
    }

    return merged, report


def _print_report(report: dict) -> None:
    print("=" * 60)
    print("DATA LOAD REPORT")
    print("=" * 60)
    print(f"Rows before cleaning : {report['rows_before_cleaning']}")
    print(f"Rows after cleaning  : {report['rows_after_cleaning']}")

    print("\nBlanks per column (before cleaning):")
    for col, n in report["blanks_per_column"].items():
        if n:
            print(f"  {col:<28} {n}")

    print(f"\nAccounts excluded (Duplicate status): {report['accounts_excluded_count']}")
    for aid in report["accounts_excluded"]:
        print(f"  - {aid}")

    print(f"\nAccounts with unexplained blank gmv_trend_pct: "
          f"{report['accounts_unexplained_trend_blank_count']}")

    print(f"\nMerge from new_accounts:")
    print(f"  Updated existing accounts: {report['accounts_updated_count']} {report['accounts_updated']}")
    print(f"  Genuinely new accounts   : {report['accounts_new_count']} {report['accounts_new']}")

    print(f"\nTotal data quality flags: {len(report['data_quality_flags'])}")
    for flag in report["data_quality_flags"]:
        print(f"  - {flag['account_id']}: {flag['reason']}")
    print("=" * 60)


def _build_demo_workbook(path: Path) -> None:
    """Generate a synthetic workbook matching the expected schema, with the
    edge cases the cleaning logic is meant to handle, so the pipeline can be
    demoed without a real export on hand."""
    rng = np.random.default_rng(42)
    path.parent.mkdir(parents=True, exist_ok=True)

    months = ["gmv_sep", "gmv_oct", "gmv_nov", "gmv_dec", "gmv_jan", "gmv_feb"]

    def make_rows(n, id_start, tenure_low_share=0.1, gmv_sep_zero_share=0.1):
        ids = [f"ACC-{i:03d}" for i in range(id_start, id_start + n)]
        tenure = rng.integers(1, 48, size=n).astype(float)
        low_tenure_idx = rng.choice(n, size=int(n * tenure_low_share), replace=False)
        tenure[low_tenure_idx] = rng.integers(1, 5, size=len(low_tenure_idx))

        gmv_sep = rng.uniform(500, 20000, size=n)
        zero_sep_idx = rng.choice(
            [i for i in range(n) if i not in low_tenure_idx],
            size=int(n * gmv_sep_zero_share),
            replace=False,
        )
        gmv_sep[zero_sep_idx] = 0.0

        no_baseline_idx = set(low_tenure_idx) | set(zero_sep_idx)
        trend = rng.uniform(-0.3, 0.3, size=n)
        for i in no_baseline_idx:
            trend[i] = np.nan

        data = {
            "account_id": ids,
            "ownership": rng.choice(["Account Managed", "Self Serve"], size=n),
            "broker_reliance_pct": rng.uniform(0, 1, size=n).round(3),
            "app_active_days_6m": rng.integers(0, 180, size=n),
            "pdp_views_6m": rng.integers(0, 5000, size=n),
            "make_an_offer_6m": rng.integers(0, 200, size=n),
            "chat_threads": rng.integers(0, 100, size=n),
            "video_call_requests": rng.integers(0, 20, size=n),
            "bundle_gmv_share_pct": rng.uniform(0, 1, size=n).round(3),
            "gmv_total_6m": rng.uniform(1000, 120000, size=n).round(2),
            "gmv_sep": gmv_sep.round(2),
            "gmv_oct": rng.uniform(500, 20000, size=n).round(2),
            "gmv_nov": rng.uniform(500, 20000, size=n).round(2),
            "gmv_dec": rng.uniform(500, 20000, size=n).round(2),
            "gmv_jan": rng.uniform(500, 20000, size=n).round(2),
            "gmv_feb": rng.uniform(500, 20000, size=n).round(2),
            "gmv_trend_pct": trend.round(4),
            "tenure_months": tenure,
            "orders_6m": rng.integers(0, 300, size=n),
            "buyer_persona": rng.choice(["Bargain Hunter", "Loyalist", "Explorer", "Bulk Buyer"], size=n),
            # "AMER" not "NA": pandas' default read_excel na_values treats the
            # literal string "NA" as missing, which would corrupt this demo
            # column with false blanks that have nothing to do with real data
            # quality. A real workbook using "NA" as a region code would hit
            # the same gotcha on read.
            "region": rng.choice(["AMER", "EMEA", "APAC", "LATAM"], size=n),
            "country": rng.choice(["US", "UK", "DE", "SG", "BR", "AU"], size=n),
            "account_status": rng.choice(["Active", "At Risk", "Churned", None], size=n, p=[0.6, 0.2, 0.15, 0.05]),
            "csm_owner": rng.choice(["csm_a", "csm_b", "csm_c"], size=n),
            "signup_date": pd.Timestamp("2022-01-01") + pd.to_timedelta(rng.integers(0, 1000, size=n), unit="D"),
            "last_login_date": pd.Timestamp("2026-06-01") + pd.to_timedelta(rng.integers(-60, 0, size=n), unit="D"),
            "notes": [None] * n,
        }
        return pd.DataFrame(data)

    accounts_df = make_rows(300, id_start=1)
    accounts_df.loc[accounts_df["account_id"] == "ACC-005", "account_status"] = "Duplicate"

    # 50 new_accounts rows: 15 overlap with existing ids (updates), 35 are new.
    overlap_ids = accounts_df["account_id"].iloc[10:25].tolist()
    new_df = make_rows(50, id_start=301)
    new_df.loc[:14, "account_id"] = overlap_ids

    readme_df = pd.DataFrame(
        {"Notes": [
            "Accounts: primary account book, 300 rows.",
            "new_accounts: incremental batch to merge in, 50 rows, same schema.",
            "account_status blank = not yet set. 'Duplicate' rows should be excluded and logged.",
        ]}
    )

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        accounts_df.to_excel(writer, sheet_name=ACCOUNTS_SHEET, index=False)
        new_df.to_excel(writer, sheet_name=NEW_ACCOUNTS_SHEET, index=False)
        readme_df.to_excel(writer, sheet_name="Readme", index=False)


if __name__ == "__main__":
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEMO_PATH

    if not filepath.exists():
        print(f"No workbook found at {filepath}, generating a demo workbook there...")
        _build_demo_workbook(filepath)

    clean_df, report = load_and_clean(filepath)
    _print_report(report)

    print(f"\nFinal DataFrame: {clean_df.shape[0]} rows x {clean_df.shape[1]} columns")
    print(clean_df.head())
