"""Tests for src/nba.py's touch-count-aware Self-Serve Nudge variants.

draft_self_serve_nudge_stage() is called at display time (Account Overview
page), not inside draft_actions(), since touch_count only exists in the
state database after sync_state() runs, which happens after draft_actions().
These tests exercise the selector directly against a plain row dict.
"""

from __future__ import annotations

import math

import pytest

from src.nba import (
    GROWTH_LEVER_ACTIONS,
    TONES,
    draft_growth_lever_stage,
    draft_self_serve_nudge_stage,
    explain_growth_lever_default,
)


def make_row(**overrides):
    row = {
        "account_id": "ACC-1",
        "name": "Acme Co",
        "broker_reliance_pct": 62,
        "is_at_risk": False,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("touch_count", [0, None, float("nan")])
def test_touch_count_zero_or_missing_shows_first_outreach_message(touch_count):
    row = make_row()
    stage, variants = draft_self_serve_nudge_stage(row, touch_count)

    assert stage == 1
    assert {v["tone"] for v in variants} == set(TONES)
    for variant in variants:
        assert "Following up" not in variant["message"]
        assert "one more try" not in variant["message"].lower()
        assert "Acme Co" in variant["message"]


def test_touch_count_one_shows_second_touch_variant():
    row = make_row()
    stage, variants = draft_self_serve_nudge_stage(row, 1)

    assert stage == 2
    assert {v["tone"] for v in variants} == set(TONES)
    for variant in variants:
        assert "Acme Co" in variant["message"]
        # Acknowledges this isn't the first contact.
        assert any(
            phrase in variant["message"]
            for phrase in ("Following up", "circling back", "following up")
        )


@pytest.mark.parametrize("touch_count", [2, 3, 10])
def test_touch_count_two_or_more_shows_third_attempt_variant(touch_count):
    row = make_row()
    stage, variants = draft_self_serve_nudge_stage(row, touch_count)

    assert stage == 3
    assert {v["tone"] for v in variants} == set(TONES)
    for variant in variants:
        assert "Acme Co" in variant["message"]
        assert "10%" in variant["message"]


def test_each_touch_stage_has_a_distinct_message_per_tone():
    row = make_row()
    _, touch1 = draft_self_serve_nudge_stage(row, 0)
    _, touch2 = draft_self_serve_nudge_stage(row, 1)
    _, touch3 = draft_self_serve_nudge_stage(row, 2)

    by_tone = lambda variants: {v["tone"]: v["message"] for v in variants}
    t1, t2, t3 = by_tone(touch1), by_tone(touch2), by_tone(touch3)

    for tone in TONES:
        assert t1[tone] != t2[tone] != t3[tone]
        assert t1[tone] != t3[tone]


def test_self_serve_nudge_messages_identical_at_risk_or_not():
    """Customer-facing drafts are pure behavior-focused content -- is_at_risk
    must never change what the customer sees. Account health concerns are
    surfaced separately via src.scoring.internal_recommendation."""
    at_risk_row = make_row(is_at_risk=True)
    healthy_row = make_row(is_at_risk=False)
    for touch_count in (0, 1, 2):
        _, at_risk_variants = draft_self_serve_nudge_stage(at_risk_row, touch_count)
        _, healthy_variants = draft_self_serve_nudge_stage(healthy_row, touch_count)
        assert at_risk_variants == healthy_variants


# ---------------------------------------------------------------------
# draft_growth_lever_stage(): the same touch-count cadence pattern, applied
# to each of the 5 Growth Headroom levers.
# ---------------------------------------------------------------------

def make_growth_row(**overrides):
    row = {
        "account_id": "ACC-2",
        "name": "Bloom & Co",
        "is_at_risk": False,
        "bundle_gmv_share_pct": 40,
        "handpick_orders": 12,
        "bundle_orders": 3,
        "make_an_offer_6m": 0,
        "chat_threads": 0,
        "video_call_requests": 0,
        "orders_6m": 20,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("action", GROWTH_LEVER_ACTIONS)
@pytest.mark.parametrize("touch_count,expected_stage", [(0, 1), (None, 1), (1, 2), (2, 3), (5, 3)])
def test_growth_lever_cadence_all_levers_all_stages(action, touch_count, expected_stage):
    row = make_growth_row()
    stage, variants = draft_growth_lever_stage(row, action, touch_count)

    assert stage == expected_stage
    assert {v["tone"] for v in variants} == set(TONES)
    for variant in variants:
        assert "Bloom & Co" in variant["message"]


@pytest.mark.parametrize("action", GROWTH_LEVER_ACTIONS)
def test_growth_lever_each_touch_stage_is_distinct_per_tone(action):
    row = make_growth_row()
    _, touch1 = draft_growth_lever_stage(row, action, 0)
    _, touch2 = draft_growth_lever_stage(row, action, 1)
    _, touch3 = draft_growth_lever_stage(row, action, 2)

    by_tone = lambda variants: {v["tone"]: v["message"] for v in variants}
    t1, t2, t3 = by_tone(touch1), by_tone(touch2), by_tone(touch3)

    for tone in TONES:
        assert t1[tone] != t2[tone]
        assert t2[tone] != t3[tone]
        assert t1[tone] != t3[tone]


@pytest.mark.parametrize("action", GROWTH_LEVER_ACTIONS)
def test_growth_lever_messages_identical_at_risk_or_not(action):
    at_risk_row = make_growth_row(is_at_risk=True)
    healthy_row = make_growth_row(is_at_risk=False)
    for touch_count in (0, 1, 2):
        _, at_risk_variants = draft_growth_lever_stage(at_risk_row, action, touch_count)
        _, healthy_variants = draft_growth_lever_stage(healthy_row, action, touch_count)
        assert at_risk_variants == healthy_variants


def test_offer_tool_nudge_never_mentions_discount_or_promotion():
    row = make_growth_row()
    banned_terms = ["discount", "% off", "promo", "coupon", "% discount"]
    for touch_count in (0, 1, 2):
        _, variants = draft_growth_lever_stage(row, "Offer tool nudge", touch_count)
        for variant in variants:
            text = f"{variant['subject']} {variant['message']}".lower()
            for term in banned_terms:
                assert term not in text, f"found banned term {term!r} in: {text}"


def test_explain_growth_lever_default_matches_auto_selected_action():
    for action in GROWTH_LEVER_ACTIONS:
        row = make_growth_row(action=action)
        reason = explain_growth_lever_default(row)
        assert reason.startswith("Auto-selected:")
