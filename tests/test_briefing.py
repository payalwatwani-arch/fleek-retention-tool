"""Tests for src/briefing.py's generate_briefing_text()."""

from __future__ import annotations

import pandas as pd

from src.briefing import generate_briefing_text
from src.segmentation import ACCOUNT_MANAGED


def _row(**overrides) -> dict:
    base = dict(
        account_id="A1",
        ownership=ACCOUNT_MANAGED,
        action="None",
        segment="Healthy AM",
        is_at_risk=False,
        broker_reliance_pct=10.0,
        app_active_days_6m=100.0,
        pdp_views_6m=500.0,
        make_an_offer_6m=2.0,
        gmv_trend_pct=0.0,
        has_trend_baseline=True,
    )
    base.update(overrides)
    return base


def _summary(**overrides) -> dict:
    base = dict(new_count=0, follow_up_count=0, resolved_count=0, unchanged_count=0)
    base.update(overrides)
    return base


def test_narrative_counts_self_serve_and_follow_up():
    df = pd.DataFrame(
        [
            _row(
                account_id="A1",
                action="Self-Serve Nudge",
                segment="Broker-Reliant",
                broker_reliance_pct=80.0,
            ),
            _row(account_id="A2", action="None"),
            _row(
                account_id="A3",
                action="Win-back play",
                is_at_risk=True,
                gmv_trend_pct=-100.0,
            ),
        ]
    )
    text = generate_briefing_text(df, _summary(new_count=3))

    assert "1 account(s) that could move to self-serve" in text
    assert "2 accounts need attention today" in text


def test_first_run_message():
    df = pd.DataFrame([_row()])
    text = generate_briefing_text(df, _summary(new_count=1))

    assert "This is the first run" in text
    assert "New accounts:" not in text


def test_since_last_run_digest_when_not_first_run():
    df = pd.DataFrame([_row()])
    text = generate_briefing_text(
        df, _summary(new_count=1, follow_up_count=2, resolved_count=1, unchanged_count=5)
    )

    assert "New accounts: 1" in text
    assert "Moved to Follow-up: 2" in text
    assert "Wins (improved to no action needed): 1" in text
    assert "Unchanged: 5" in text
    assert "first run" not in text.lower()


def test_top_factor_reported_for_at_risk_accounts():
    df = pd.DataFrame(
        [
            _row(
                account_id="A1",
                is_at_risk=True,
                broker_reliance_pct=95.0,
                segment="Broker-Reliant",
                action="Self-Serve Nudge",
            ),
            _row(
                account_id="A2",
                is_at_risk=True,
                broker_reliance_pct=90.0,
                segment="Broker-Reliant",
                action="Self-Serve Nudge",
            ),
        ]
    )
    text = generate_briefing_text(df, _summary(new_count=2))

    assert "broker reliance" in text.lower()


def test_no_at_risk_accounts_omits_top_factor_clause():
    df = pd.DataFrame([_row(), _row(account_id="A2")])
    text = generate_briefing_text(df, _summary(new_count=2))

    assert "most common reason" not in text
