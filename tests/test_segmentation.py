"""Tests for the "Re-engage" fallback action in src/segmentation.py:
accounts still "None" after every other signal has had a chance, but with
essentially zero activity since onboarding (as opposed to Declining, which
requires a real drop from a real baseline)."""

from __future__ import annotations

import pandas as pd

from src.segmentation import segment_accounts

BASE_ROW = {
    "account_id": "ACC-1",
    "ownership": "Self Serve",
    "broker_reliance_pct": 0,
    "app_active_days_6m": 0,
    "pdp_views_6m": 0,
    "make_an_offer_6m": 1,
    "chat_threads": 1,
    "video_call_requests": 1,
    "bundle_gmv_share_pct": 100,
    "handpick_orders": 0,
    "bundle_orders": 0,
    "gmv_total_6m": 1000,
    "has_trend_baseline": False,
    "gmv_trend_pct": None,
    "orders_6m": 0,
}


def make_df(**overrides) -> pd.DataFrame:
    row = dict(BASE_ROW)
    row.update(overrides)
    return pd.DataFrame([row])


def test_dormant_since_onboarding_account_gets_reengage():
    df = segment_accounts(make_df())
    assert df.loc[0, "action"] == "Re-engage"


def test_account_with_real_activity_stays_none():
    df = segment_accounts(make_df(pdp_views_6m=50))
    assert df.loc[0, "action"] == "None"


def test_account_managed_dormant_since_onboarding_gets_reengage():
    df = segment_accounts(
        make_df(ownership="Account Managed", broker_reliance_pct=0)
    )
    assert df.loc[0, "action"] == "Re-engage"


def test_account_already_claimed_by_another_action_is_unaffected():
    # Broker-Reliant Account Managed account: claimed by Self-Serve Nudge
    # well before the Re-engage fallback runs, even though its activity
    # numbers alone would otherwise qualify.
    df = segment_accounts(
        make_df(ownership="Account Managed", broker_reliance_pct=50)
    )
    assert df.loc[0, "action"] == "Self-Serve Nudge"
