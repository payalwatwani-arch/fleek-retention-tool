"""Tests for src/nba.py's touch-count-aware Self-Serve Nudge variants.

draft_self_serve_nudge_stage() is called at display time (Account Overview
page), not inside draft_actions(), since touch_count only exists in the
state database after sync_state() runs, which happens after draft_actions().
These tests exercise the selector directly against a plain row dict.
"""

from __future__ import annotations

import math

import pytest

from src.nba import AT_RISK_APPEND_SENTENCE, TONES, draft_self_serve_nudge_stage


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


def test_at_risk_sentence_appended_across_all_touch_stages():
    row = make_row(is_at_risk=True)
    for touch_count in (0, 1, 2):
        _, variants = draft_self_serve_nudge_stage(row, touch_count)
        for variant in variants:
            assert AT_RISK_APPEND_SENTENCE in variant["message"]


def test_no_at_risk_sentence_when_not_at_risk():
    row = make_row(is_at_risk=False)
    for touch_count in (0, 1, 2):
        _, variants = draft_self_serve_nudge_stage(row, touch_count)
        for variant in variants:
            assert AT_RISK_APPEND_SENTENCE not in variant["message"]
