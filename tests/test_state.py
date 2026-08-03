"""Tests for src/state.py — the daily-safe-to-rerun reconciliation layer.

Each test builds a minimal DataFrame shaped like draft_actions() output
(account_id, segment, action, plus the 9 hash-relevant input fields) and
points state.py at a fresh, isolated sqlite file per test.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.state import (
    get_pending,
    get_state_summary,
    mark_actioned,
    sync_state,
)

HASH_FIELD_DEFAULTS = {
    "broker_reliance_pct": 45,
    "app_active_days_6m": 5,
    "pdp_views_6m": 10,
    "make_an_offer_6m": 0,
    "bundle_gmv_share_pct": 100,
    "chat_threads": 0,
    "video_call_requests": 0,
    "gmv_trend_pct": -10,
    "gmv_total_6m": 5000,
}


def make_row(account_id, segment, action, **overrides):
    row = {"account_id": account_id, "segment": segment, "action": action}
    row.update(HASH_FIELD_DEFAULTS)
    row.update(overrides)
    return row


def make_df(*rows):
    return pd.DataFrame(list(rows))


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "portfolio.db"


def test_day1_day2_day3_walkthrough(db_path):
    # Day 1: new account, broker_reliance_pct = 45 -> Self-Serve Nudge.
    day1 = make_df(
        make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge", broker_reliance_pct=45)
    )
    summary1 = sync_state(day1, db_path=db_path)
    assert summary1 == {"new_count": 1, "reset_to_pending_count": 0, "unchanged_count": 0}

    pending = get_pending(db_path=db_path)
    assert list(pending["account_id"]) == ["ACC-001"]
    assert pending.iloc[0]["status"] == "pending"

    # Ops actions it.
    mark_actioned("ACC-001", db_path=db_path)
    summary_after_action = get_state_summary(db_path=db_path)
    assert summary_after_action == {"total": 1, "pending": 0, "actioned": 1}

    # Day 2: identical input data (broker_reliance_pct still 45).
    day2 = make_df(
        make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge", broker_reliance_pct=45)
    )
    summary2 = sync_state(day2, db_path=db_path)
    assert summary2 == {"new_count": 0, "reset_to_pending_count": 0, "unchanged_count": 1}
    assert get_state_summary(db_path=db_path) == {"total": 1, "pending": 0, "actioned": 1}

    # Day 3: broker_reliance_pct drops to 15 -> action becomes "None".
    day3 = make_df(
        make_row("ACC-001", "Healthy AM", "None", broker_reliance_pct=15)
    )
    summary3 = sync_state(day3, db_path=db_path)
    assert summary3 == {"new_count": 0, "reset_to_pending_count": 1, "unchanged_count": 0}

    final_summary = get_state_summary(db_path=db_path)
    assert final_summary == {"total": 1, "pending": 1, "actioned": 0}

    pending_after_day3 = get_pending(db_path=db_path)
    row = pending_after_day3.iloc[0]
    assert row["account_id"] == "ACC-001"
    assert row["last_action"] == "None"
    assert row["last_segment"] == "Healthy AM"
    assert row["status"] == "pending"


def test_rerunning_identical_data_twice_changes_nothing(db_path):
    df = make_df(
        make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge"),
        make_row("ACC-002", "Growth Headroom", "Bundle nudge"),
    )
    sync_state(df, db_path=db_path)
    mark_actioned("ACC-001", db_path=db_path)

    before = get_state_summary(db_path=db_path)

    summary_run2 = sync_state(df, db_path=db_path)
    summary_run3 = sync_state(df, db_path=db_path)

    assert summary_run2 == {"new_count": 0, "reset_to_pending_count": 0, "unchanged_count": 2}
    assert summary_run3 == {"new_count": 0, "reset_to_pending_count": 0, "unchanged_count": 2}

    after = get_state_summary(db_path=db_path)
    assert before == after == {"total": 2, "pending": 1, "actioned": 1}


def test_actioned_account_resets_to_pending_when_action_changes(db_path):
    df_day1 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-30)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-010", db_path=db_path)
    assert get_state_summary(db_path=db_path) == {"total": 1, "pending": 0, "actioned": 1}

    # Trend worsens to "Already Gone" territory -> a genuinely different action.
    df_day2 = make_df(
        make_row("ACC-010", "Already Gone", "Win-back play", gmv_trend_pct=-100)
    )
    summary = sync_state(df_day2, db_path=db_path)

    assert summary == {"new_count": 0, "reset_to_pending_count": 1, "unchanged_count": 0}
    pending = get_pending(db_path=db_path)
    assert pending.iloc[0]["account_id"] == "ACC-010"
    assert pending.iloc[0]["last_action"] == "Win-back play"
    assert pending.iloc[0]["last_segment"] == "Already Gone"
    assert get_state_summary(db_path=db_path) == {"total": 1, "pending": 1, "actioned": 0}


def test_actioned_account_stays_actioned_when_action_unchanged(db_path):
    df_day1 = make_df(
        make_row("ACC-020", "Growth Headroom", "Bundle nudge", pdp_views_6m=100)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-020", db_path=db_path)
    assert get_state_summary(db_path=db_path) == {"total": 1, "pending": 0, "actioned": 1}

    # pdp_views_6m moves (data_hash changes) but action stays "Bundle nudge".
    df_day2 = make_df(
        make_row("ACC-020", "Growth Headroom", "Bundle nudge", pdp_views_6m=150)
    )
    summary = sync_state(df_day2, db_path=db_path)

    assert summary == {"new_count": 0, "reset_to_pending_count": 0, "unchanged_count": 1}
    final = get_state_summary(db_path=db_path)
    assert final == {"total": 1, "pending": 0, "actioned": 1}

    with pd.option_context("display.max_rows", None):
        pending = get_pending(db_path=db_path)
    assert pending.empty
