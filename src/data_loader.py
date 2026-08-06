"""Load, clean, and idempotently merge account data for the retention tool.

Run directly to see a demo report (from the project root):

    python -m src.data_loader [path/to/workbook.xlsx]

`python src/data_loader.py [path]` also still works, since this module has
no imports of its own from elsewhere in `src`.

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

# The full account schema the rest of the pipeline (segmentation, scoring,
# nba) depends on -- kept in sync by hand with _build_demo_workbook's
# make_rows(). A sheet missing any of these would pass a narrower check here
# only to crash later, deeper in the pipeline, with a far more confusing
# error, so load_and_clean() validates against the whole set up front.
REQUIRED_ACCOUNT_COLUMNS = frozenset(
    {
        "account_id",
        "ownership",
        "broker_reliance_pct",
        "app_active_days_6m",
        "pdp_views_6m",
        "make_an_offer_6m",
        "chat_threads",
        "video_call_requests",
        "bundle_gmv_share_pct",
        "handpick_orders",
        "bundle_orders",
        "gmv_total_6m",
        "gmv_sep",
        "gmv_oct",
        "gmv_nov",
        "gmv_dec",
        "gmv_jan",
        "gmv_feb",
        "gmv_trend_pct",
        "tenure_months",
        "orders_6m",
        "buyer_persona",
        "region",
        "country",
        "account_status",
        "csm_owner",
        "signup_date",
        "last_login_date",
        "notes",
    }
)


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
    """Read the base account book + new_accounts tab, merge new_accounts in
    idempotently, and clean blanks with intent. Returns (clean_df, report).

    Sheet selection for the base account book, same principle as
    `load_new_accounts_batch()` but checking columns rather than just
    tolerating any single sheet:

      1. A sheet literally named `Accounts` (ACCOUNTS_SHEET). If a
         `new_accounts` sheet is also present, it's read and merged in as
         before (fully-combined-file behavior, unchanged). If it's absent,
         the Accounts sheet is still used as the base dataset, with 0 new
         accounts this run -- same as an Accounts-only upload standing in
         for the batch uploader.
      2. Else, if the workbook has exactly one sheet total, that one --
         but only if it actually has every column the rest of the pipeline
         (segmentation, scoring, nba) needs (REQUIRED_ACCOUNT_COLUMNS).
         There's no separate new_accounts sheet to merge in this case.

    Raises ValueError (listing sheet names, or missing columns) rather than
    letting a malformed file fail with a cryptic error, or silently pass a
    check here only to crash deeper in the pipeline later.
    """
    workbook = pd.ExcelFile(filepath)
    sheet_names = workbook.sheet_names

    if ACCOUNTS_SHEET in sheet_names:
        accounts_df = pd.read_excel(workbook, sheet_name=ACCOUNTS_SHEET)
        if NEW_ACCOUNTS_SHEET in sheet_names:
            new_df = pd.read_excel(workbook, sheet_name=NEW_ACCOUNTS_SHEET)
        else:
            new_df = pd.DataFrame(columns=accounts_df.columns)
    elif len(sheet_names) == 1:
        candidate_df = pd.read_excel(workbook, sheet_name=sheet_names[0])
        missing = sorted(REQUIRED_ACCOUNT_COLUMNS - set(candidate_df.columns))
        if missing:
            raise ValueError(
                f"Sheet '{sheet_names[0]}' is missing required account "
                f"columns: {missing!r}. Expected a sheet named "
                f"'{ACCOUNTS_SHEET}', or a single sheet with all of "
                f"{sorted(REQUIRED_ACCOUNT_COLUMNS)!r}."
            )
        accounts_df = candidate_df
        new_df = pd.DataFrame(columns=accounts_df.columns)
    else:
        raise ValueError(
            "Could not determine which sheet holds the account book: found "
            f"{len(sheet_names)} sheets {sheet_names!r}. Expected a sheet "
            f"named '{ACCOUNTS_SHEET}', or a workbook with exactly one sheet."
        )

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


def load_new_accounts_batch(filepath) -> pd.DataFrame:
    """Read a batch of new-account rows for the Import view's batch
    uploader, tolerant of sheet layout in a way `load_and_clean()` isn't --
    the batch uploader shouldn't require a full two-sheet workbook.

    Picks a sheet in this order:
      1. A sheet literally named `new_accounts` (NEW_ACCOUNTS_SHEET).
      2. Else a sheet literally named `Accounts` (ACCOUNTS_SHEET) -- lets a
         full combined workbook be uploaded as a "batch" too, treating its
         Accounts data as this batch.
      3. Else, if the workbook has exactly one sheet total, that one --
         supports a standalone file with just new-account rows on a single,
         arbitrarily-named sheet.

    Raises ValueError listing the sheet names found if none of the above
    apply, rather than letting a malformed file fail with a cryptic error
    from deep inside pandas/openpyxl.
    """
    workbook = pd.ExcelFile(filepath)
    sheet_names = workbook.sheet_names

    if NEW_ACCOUNTS_SHEET in sheet_names:
        sheet_name = NEW_ACCOUNTS_SHEET
    elif ACCOUNTS_SHEET in sheet_names:
        sheet_name = ACCOUNTS_SHEET
    elif len(sheet_names) == 1:
        sheet_name = sheet_names[0]
    else:
        raise ValueError(
            "Could not determine which sheet holds the new-accounts batch: "
            f"found {len(sheet_names)} sheets {sheet_names!r}. Expected a "
            f"sheet named '{NEW_ACCOUNTS_SHEET}' or '{ACCOUNTS_SHEET}', or a "
            "workbook with exactly one sheet."
        )

    return pd.read_excel(workbook, sheet_name=sheet_name)


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


def _build_demo_workbook(
    path: Path,
    n_accounts: int = 300,
    n_new: int = 50,
    n_overlap: int = 15,
    seed: int = 42,
) -> None:
    """Generate a synthetic workbook matching the expected schema, with the
    edge cases the cleaning logic is meant to handle, so the pipeline can be
    demoed without a real export on hand.

    n_accounts/n_new/n_overlap default to the original 300/50/15 demo size;
    callers (e.g. scale tests) can pass larger values to generate a bigger
    book using the same realistic column ranges."""
    rng = np.random.default_rng(seed)
    path.parent.mkdir(parents=True, exist_ok=True)

    months = ["gmv_sep", "gmv_oct", "gmv_nov", "gmv_dec", "gmv_jan", "gmv_feb"]

    # Kept in sync by hand with segmentation.py's BROKER_RELIANCE_THRESHOLD
    # (can't import it directly: segmentation.py imports from this module).
    DEMO_BROKER_RELIANCE_SPLIT = 30

    def make_rows(n, id_start, tenure_low_share=0.30, gmv_sep_zero_share=0.20):
        ids = [f"ACC-{i:03d}" for i in range(id_start, id_start + n)]

        # tenure_months / gmv_sep: drawn so ~half the book ends up with no
        # trend baseline (tenure < 6 months, or a zero September GMV to
        # divide by), matching the real ~50% blank rate in gmv_trend_pct.
        tenure = rng.integers(6, 48, size=n).astype(float)
        low_tenure_idx = rng.choice(n, size=int(n * tenure_low_share), replace=False)
        tenure[low_tenure_idx] = rng.integers(1, 6, size=len(low_tenure_idx))

        gmv_sep = rng.uniform(500, 20000, size=n)
        remaining_idx = [i for i in range(n) if i not in set(low_tenure_idx)]
        zero_sep_idx = rng.choice(remaining_idx, size=int(n * gmv_sep_zero_share), replace=False)
        gmv_sep[zero_sep_idx] = 0.0

        no_baseline_idx = set(low_tenure_idx) | set(zero_sep_idx)

        # gmv_trend_pct: 0-100 scale (matches segmentation.py's thresholds,
        # not the 0-1 fraction this used to be). Mirrors the real skew: ~77%
        # of trended accounts already flat at -100%, a smaller share genuinely
        # declining (-25 to -100), the rest holding steady or growing.
        # Accounts with no baseline stay null.
        trend = np.full(n, np.nan)
        for i in range(n):
            if i in no_baseline_idx:
                continue
            r = rng.random()
            if r < 0.77:
                trend[i] = -100.0
            elif r < 0.84:
                trend[i] = rng.uniform(-99, -25)
            else:
                trend[i] = rng.uniform(-24, 50)

        # broker_reliance_pct: 0-100 scale. Real data shows a genuine
        # two-cluster split with a gap between 20-40%, not a smooth spread.
        broker_reliance_pct = np.where(
            rng.random(n) < 0.35,
            rng.uniform(40, 90, size=n),
            rng.uniform(0, 20, size=n),
        ).round(1)

        # app_active_days_6m: high broker reliance tracks near-zero in-app
        # activity, per DECISIONS.md.
        app_active_days_6m = np.where(
            broker_reliance_pct > DEMO_BROKER_RELIANCE_SPLIT,
            rng.integers(0, 10, size=n),
            rng.integers(10, 180, size=n),
        )

        # gmv_total_6m: a minority of accounts are low-spend "headroom"
        # candidates (well under the $266 threshold); the rest spend normally.
        gmv_total_6m = np.where(
            rng.random(n) < 0.2,
            rng.uniform(10, 266, size=n),
            rng.uniform(266, 120000, size=n),
        ).round(2)

        pdp_views_6m = rng.integers(0, 3000, size=n)

        # make_an_offer_6m / chat_threads / video_call_requests: zero-use is
        # common rather than a rare edge case, so the nudge segments that key
        # off "never used this feature" actually get populated.
        make_an_offer_6m = np.where(rng.random(n) < 0.4, 0, rng.integers(1, 15, size=n))
        chat_threads = np.where(rng.random(n) < 0.5, 0, rng.integers(1, 10, size=n))
        video_call_requests = np.where(rng.random(n) < 0.6, 0, rng.integers(1, 5, size=n))

        # bundle_gmv_share_pct: 90% of self-serve accounts sit at exactly
        # 100% bundle share in the real data; the rest fall somewhere below.
        bundle_gmv_share_pct = np.where(
            rng.random(n) < 0.9,
            100.0,
            rng.uniform(0, 99, size=n),
        ).round(1)

        # handpick_orders / bundle_orders: how many of an account's orders
        # were assembled item-by-item vs. placed through a pre-made bundle.
        # Independent of bundle_gmv_share_pct (a dollar-share-of-GMV
        # measure) since an account can lean on a few large bundle orders
        # by spend while still placing more individual handpicked orders
        # by count, or vice versa.
        handpick_orders = rng.integers(0, 30, size=n)
        bundle_orders = rng.integers(0, 30, size=n)

        data = {
            "account_id": ids,
            "ownership": rng.choice(["Account Managed", "Self Serve"], size=n),
            "broker_reliance_pct": broker_reliance_pct,
            "app_active_days_6m": app_active_days_6m,
            "pdp_views_6m": pdp_views_6m,
            "make_an_offer_6m": make_an_offer_6m,
            "chat_threads": chat_threads,
            "video_call_requests": video_call_requests,
            "bundle_gmv_share_pct": bundle_gmv_share_pct,
            "handpick_orders": handpick_orders,
            "bundle_orders": bundle_orders,
            "gmv_total_6m": gmv_total_6m,
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

    accounts_df = make_rows(n_accounts, id_start=1)
    accounts_df.loc[accounts_df["account_id"] == "ACC-005", "account_status"] = "Duplicate"

    # n_overlap of the new_accounts rows overlap with existing ids (updates), the rest are new.
    overlap_ids = accounts_df["account_id"].iloc[10:10 + n_overlap].tolist()
    new_df = make_rows(n_new, id_start=n_accounts + 1)
    new_df.loc[: n_overlap - 1, "account_id"] = overlap_ids

    readme_df = pd.DataFrame(
        {"Notes": [
            f"Accounts: primary account book, {n_accounts} rows.",
            f"new_accounts: incremental batch to merge in, {n_new} rows, same schema.",
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
