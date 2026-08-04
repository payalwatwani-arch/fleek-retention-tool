"""Tests for src/scoring.py — the account health score."""

from __future__ import annotations

from src.scoring import compute_health_score

BASE_ROW = {
    "ownership": "Account Managed",
    "broker_reliance_pct": 0,
    "app_active_days_6m": 0,
    "pdp_views_6m": 0,
    "make_an_offer_6m": 0,
    "has_trend_baseline": True,
    "gmv_trend_pct": 0,
}


def make_row(**overrides):
    row = dict(BASE_ROW)
    row.update(overrides)
    return row


def test_healthy_account_managed_scores_high():
    row = make_row(
        ownership="Account Managed",
        broker_reliance_pct=5,
        app_active_days_6m=150,
        has_trend_baseline=True,
        gmv_trend_pct=25,
    )
    score, factors = compute_health_score(row)
    assert score >= 80
    assert factors


def test_troubled_account_managed_scores_low():
    row = make_row(
        ownership="Account Managed",
        broker_reliance_pct=85,
        app_active_days_6m=1,
        has_trend_baseline=True,
        gmv_trend_pct=-100,
    )
    score, factors = compute_health_score(row)
    assert score <= 15
    assert factors


def test_healthy_self_serve_scores_high():
    row = make_row(
        ownership="Self Serve",
        pdp_views_6m=2000,
        make_an_offer_6m=10,
        app_active_days_6m=140,
        has_trend_baseline=True,
        gmv_trend_pct=30,
    )
    score, factors = compute_health_score(row)
    assert score >= 80
    assert factors


def test_troubled_beats_healthy():
    healthy = make_row(
        ownership="Account Managed", broker_reliance_pct=5, app_active_days_6m=150,
        has_trend_baseline=True, gmv_trend_pct=25,
    )
    troubled = make_row(
        ownership="Account Managed", broker_reliance_pct=85, app_active_days_6m=1,
        has_trend_baseline=True, gmv_trend_pct=-100,
    )
    healthy_score, _ = compute_health_score(healthy)
    troubled_score, _ = compute_health_score(troubled)
    assert healthy_score > troubled_score


def test_factors_reflect_high_broker_reliance_as_negative():
    row = make_row(
        ownership="Account Managed",
        broker_reliance_pct=72,
        app_active_days_6m=3,
        has_trend_baseline=True,
        gmv_trend_pct=10,
    )
    _, factors = compute_health_score(row)
    labels = {f["label"]: f for f in factors}

    broker_factor = next(f for label, f in labels.items() if label.startswith("Broker reliance"))
    assert broker_factor["direction"] == "up"
    assert broker_factor["impact"] == "negative"

    activity_factor = next(f for label, f in labels.items() if label.startswith("App activity"))
    assert activity_factor["direction"] == "down"
    assert activity_factor["impact"] == "negative"


def test_factors_reflect_low_broker_reliance_as_positive():
    row = make_row(
        ownership="Account Managed",
        broker_reliance_pct=2,
        app_active_days_6m=160,
        has_trend_baseline=True,
        gmv_trend_pct=40,
    )
    _, factors = compute_health_score(row)
    broker_factor = next(f for f in factors if f["label"].startswith("Broker reliance"))
    assert broker_factor["direction"] == "down"
    assert broker_factor["impact"] == "positive"


def test_self_serve_engagement_factor_reflects_high_usage():
    row = make_row(
        ownership="Self Serve",
        pdp_views_6m=2500,
        make_an_offer_6m=12,
        app_active_days_6m=100,
        has_trend_baseline=True,
        gmv_trend_pct=20,
    )
    _, factors = compute_health_score(row)
    engagement_factor = next(f for f in factors if f["label"].startswith("Engagement"))
    assert engagement_factor["direction"] == "up"
    assert engagement_factor["impact"] == "positive"


def test_no_trend_baseline_excludes_trend_factor():
    row = make_row(
        ownership="Account Managed",
        broker_reliance_pct=10,
        app_active_days_6m=50,
        has_trend_baseline=False,
        gmv_trend_pct=None,
    )
    score, factors = compute_health_score(row)
    assert 0 <= score <= 100
    assert len(factors) == 2
    assert all(not f["label"].startswith("GMV trend") for f in factors)


def test_factors_list_has_two_to_three_entries():
    row = make_row(
        ownership="Self Serve",
        pdp_views_6m=500,
        make_an_offer_6m=2,
        app_active_days_6m=40,
        has_trend_baseline=True,
        gmv_trend_pct=-10,
    )
    _, factors = compute_health_score(row)
    assert 2 <= len(factors) <= 3
    for f in factors:
        assert f["direction"] in ("up", "down")
        assert f["impact"] in ("positive", "negative")


def test_score_bounds_are_respected():
    extreme_low = make_row(
        ownership="Account Managed", broker_reliance_pct=100, app_active_days_6m=0,
        has_trend_baseline=True, gmv_trend_pct=-100,
    )
    extreme_high = make_row(
        ownership="Account Managed", broker_reliance_pct=0, app_active_days_6m=1000,
        has_trend_baseline=True, gmv_trend_pct=1000,
    )
    low_score, _ = compute_health_score(extreme_low)
    high_score, _ = compute_health_score(extreme_high)
    assert low_score == 0
    assert high_score == 100
