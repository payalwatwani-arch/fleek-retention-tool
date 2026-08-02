"""Load the raw portfolio workbook into DataFrames."""

from pathlib import Path

import pandas as pd

ACCOUNTS_SHEET = "Accounts"
NEW_ACCOUNTS_SHEET = "new_accounts"

EXPECTED_COLUMNS = [
    "account_id",
    "ownership",
    "buyer_persona",
    "region",
    "country",
    "account_status",
    "tenure_months",
    "gmv_total_6m",
    "orders_6m",
    "gmv_sep",
    "gmv_oct",
    "gmv_nov",
    "gmv_dec",
    "gmv_jan",
    "gmv_feb",
    "gmv_trend_pct",
    "broker_reliance_pct",
    "manual_orders",
    "self_serve_orders",
    "app_active_days_6m",
    "pdp_views_6m",
    "make_an_offer_6m",
    "chat_threads",
    "video_call_requests",
    "handpick_orders",
    "bundle_orders",
    "bundle_gmv_share_pct",
]


def _load_sheet(path: Path, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name)
    missing = set(EXPECTED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{sheet_name} is missing expected columns: {sorted(missing)}")
    return df[EXPECTED_COLUMNS].copy()


def load_portfolio(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (accounts, new_accounts) raw DataFrames from the workbook."""
    path = Path(path)
    accounts = _load_sheet(path, ACCOUNTS_SHEET)
    new_accounts = _load_sheet(path, NEW_ACCOUNTS_SHEET)
    return accounts, new_accounts
