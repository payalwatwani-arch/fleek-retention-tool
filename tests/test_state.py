"""Tests for src/state.py — the daily-safe-to-rerun reconciliation layer.

Each test builds a minimal DataFrame shaped like draft_actions() output
(account_id, segment, action, plus the 9 hash-relevant input fields) and
points state.py at a fresh, isolated sqlite file per test.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.state import (
    STAGE_ACTIONED,
    STAGE_FOLLOW_UP,
    STAGE_NEW,
    get_by_stage,
    get_state_summary,
    mark_actioned,
    sync_state,
    undo_action,
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


def test_new_account_starts_at_new_stage(db_path):
    day1 = make_df(
        make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge", broker_reliance_pct=45)
    )
    summary = sync_state(day1, db_path=db_path)
    assert summary == {
        "new_count": 1,
        "follow_up_count": 0,
        "resolved_count": 0,
        "unchanged_count": 0,
    }

    new_accounts = get_by_stage(STAGE_NEW, db_path=db_path)
    assert list(new_accounts["account_id"]) == ["ACC-001"]
    assert new_accounts.iloc[0]["status"] == STAGE_NEW
    assert new_accounts.iloc[0]["touch_count"] == 0
    assert get_state_summary(db_path=db_path) == {
        "total": 1,
        "new": 1,
        "actioned": 0,
        "follow_up": 0,
    }


def test_mark_actioned_moves_new_to_actioned(db_path):
    df = make_df(make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge"))
    sync_state(df, db_path=db_path)

    mark_actioned("ACC-001", db_path=db_path)

    actioned = get_by_stage(STAGE_ACTIONED, db_path=db_path)
    assert list(actioned["account_id"]) == ["ACC-001"]
    assert actioned.iloc[0]["actioned_date"] is not None
    assert get_state_summary(db_path=db_path) == {
        "total": 1,
        "new": 0,
        "actioned": 1,
        "follow_up": 0,
    }


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

    assert summary_run2 == {
        "new_count": 0,
        "follow_up_count": 0,
        "resolved_count": 0,
        "unchanged_count": 2,
    }
    assert summary_run3 == summary_run2

    after = get_state_summary(db_path=db_path)
    assert before == after == {"total": 2, "new": 1, "actioned": 1, "follow_up": 0}


def test_data_change_while_new_stays_new(db_path):
    day1 = make_df(
        make_row("ACC-001", "Healthy AM", "None", broker_reliance_pct=15)
    )
    sync_state(day1, db_path=db_path)

    # Data changes but the account was never actioned -- still "New", just
    # with fresh segment/action recorded.
    day2 = make_df(
        make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge", broker_reliance_pct=45)
    )
    summary = sync_state(day2, db_path=db_path)

    assert summary == {
        "new_count": 0,
        "follow_up_count": 0,
        "resolved_count": 0,
        "unchanged_count": 1,
    }
    new_accounts = get_by_stage(STAGE_NEW, db_path=db_path)
    assert new_accounts.iloc[0]["last_action"] == "Self-Serve Nudge"
    assert new_accounts.iloc[0]["last_segment"] == "Broker-Reliant"


def test_actioned_account_moves_to_follow_up_when_action_still_needed(db_path):
    df_day1 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-30)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-010", db_path=db_path)
    assert get_state_summary(db_path=db_path) == {
        "total": 1,
        "new": 0,
        "actioned": 1,
        "follow_up": 0,
    }

    # Trend worsens to "Already Gone" territory -- still needs action, just a
    # different one.
    df_day2 = make_df(
        make_row("ACC-010", "Already Gone", "Win-back play", gmv_trend_pct=-100)
    )
    summary = sync_state(df_day2, db_path=db_path)

    assert summary == {
        "new_count": 0,
        "follow_up_count": 1,
        "resolved_count": 0,
        "unchanged_count": 0,
    }
    follow_up = get_by_stage(STAGE_FOLLOW_UP, db_path=db_path)
    assert follow_up.iloc[0]["account_id"] == "ACC-010"
    assert follow_up.iloc[0]["last_action"] == "Win-back play"
    assert follow_up.iloc[0]["last_segment"] == "Already Gone"
    assert follow_up.iloc[0]["touch_count"] == 1
    assert get_state_summary(db_path=db_path) == {
        "total": 1,
        "new": 0,
        "actioned": 0,
        "follow_up": 1,
    }


def test_touch_count_increments_on_repeated_follow_up(db_path):
    df_day1 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-30)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-010", db_path=db_path)

    df_day2 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-40)
    )
    sync_state(df_day2, db_path=db_path)
    assert get_by_stage(STAGE_FOLLOW_UP, db_path=db_path).iloc[0]["touch_count"] == 1

    # Data keeps changing while already in Follow-up -- action is still
    # needed, so it stays in Follow-up and the touch count keeps climbing.
    df_day3 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-50)
    )
    summary = sync_state(df_day3, db_path=db_path)
    assert summary["follow_up_count"] == 1

    follow_up = get_by_stage(STAGE_FOLLOW_UP, db_path=db_path)
    assert follow_up.iloc[0]["touch_count"] == 2


def test_actioned_account_settles_at_actioned_when_action_resolves_to_none(db_path):
    df_day1 = make_df(
        make_row("ACC-020", "Broker-Reliant", "Self-Serve Nudge", broker_reliance_pct=45)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-020", db_path=db_path)

    # Broker reliance drops enough that the account is now "Healthy AM" with
    # no action needed -- the pipeline itself reflects the resolution, no
    # separate "Resolved" stage exists.
    df_day2 = make_df(
        make_row("ACC-020", "Healthy AM", "None", broker_reliance_pct=15)
    )
    summary = sync_state(df_day2, db_path=db_path)

    assert summary == {
        "new_count": 0,
        "follow_up_count": 0,
        "resolved_count": 1,
        "unchanged_count": 0,
    }
    actioned = get_by_stage(STAGE_ACTIONED, db_path=db_path)
    assert actioned.iloc[0]["account_id"] == "ACC-020"
    assert actioned.iloc[0]["last_segment"] == "Healthy AM"
    assert actioned.iloc[0]["last_action"] == "None"
    assert get_by_stage(STAGE_FOLLOW_UP, db_path=db_path).empty


def test_follow_up_account_settles_at_actioned_when_action_resolves_to_none(db_path):
    df_day1 = make_df(
        make_row("ACC-030", "Declining", "Retention check-in", gmv_trend_pct=-30)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-030", db_path=db_path)

    df_day2 = make_df(
        make_row("ACC-030", "Declining", "Retention check-in", gmv_trend_pct=-40)
    )
    sync_state(df_day2, db_path=db_path)
    assert not get_by_stage(STAGE_FOLLOW_UP, db_path=db_path).empty

    # Trend recovers -- account no longer needs a retention check-in.
    df_day3 = make_df(
        make_row("ACC-030", "Self-Serve, No Headroom", "None", gmv_trend_pct=5)
    )
    summary = sync_state(df_day3, db_path=db_path)

    assert summary["resolved_count"] == 1
    assert get_by_stage(STAGE_FOLLOW_UP, db_path=db_path).empty
    actioned = get_by_stage(STAGE_ACTIONED, db_path=db_path)
    assert actioned.iloc[0]["last_action"] == "None"


def test_undo_action_reverts_actioned_to_new(db_path):
    df = make_df(make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge"))
    sync_state(df, db_path=db_path)
    mark_actioned("ACC-001", db_path=db_path)
    assert get_by_stage(STAGE_ACTIONED, db_path=db_path).shape[0] == 1

    undo_action("ACC-001", db_path=db_path)

    new_accounts = get_by_stage(STAGE_NEW, db_path=db_path)
    assert list(new_accounts["account_id"]) == ["ACC-001"]
    assert new_accounts.iloc[0]["actioned_date"] is None


def test_undo_action_reverts_follow_up_to_actioned_and_decrements_touch(db_path):
    df_day1 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-30)
    )
    sync_state(df_day1, db_path=db_path)
    mark_actioned("ACC-010", db_path=db_path)

    df_day2 = make_df(
        make_row("ACC-010", "Declining", "Retention check-in", gmv_trend_pct=-40)
    )
    sync_state(df_day2, db_path=db_path)
    assert get_by_stage(STAGE_FOLLOW_UP, db_path=db_path).iloc[0]["touch_count"] == 1

    undo_action("ACC-010", db_path=db_path)

    actioned = get_by_stage(STAGE_ACTIONED, db_path=db_path)
    assert list(actioned["account_id"]) == ["ACC-010"]
    assert actioned.iloc[0]["touch_count"] == 0


def test_undo_action_on_new_account_is_a_no_op(db_path):
    df = make_df(make_row("ACC-001", "Broker-Reliant", "Self-Serve Nudge"))
    sync_state(df, db_path=db_path)

    undo_action("ACC-001", db_path=db_path)

    new_accounts = get_by_stage(STAGE_NEW, db_path=db_path)
    assert list(new_accounts["account_id"]) == ["ACC-001"]


def test_undo_action_on_unknown_account_is_a_no_op(db_path):
    # Should not raise, even though the account was never synced.
    undo_action("ACC-999", db_path=db_path)
    assert get_state_summary(db_path=db_path) == {
        "total": 0,
        "new": 0,
        "actioned": 0,
        "follow_up": 0,
    }
