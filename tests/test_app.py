"""AppTest coverage for app.py's Kanban-style Pipeline board.

Runs the real app.py script via Streamlit's AppTest harness, pointed at a
scratch working directory seeded with a copy of the demo workbook — so a
test run never touches the repo's own data/raw or data/state files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.scoring import compute_health_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "app.py"
DEMO_WORKBOOK = PROJECT_ROOT / "data" / "raw" / "demo_accounts.xlsx"

COLUMN_TITLES = ["Self-Serve Nudge", "Trending", "At Risk", "Gone Cold", "Neutral"]

SEGMENTS_BY_COLUMN = {
    "Self-Serve Nudge": ["Broker-Reliant"],
    "Trending": ["Growth Headroom"],
    "At Risk": ["Declining"],
    "Gone Cold": ["Already Gone"],
    "Neutral": ["Healthy AM", "Self-Serve, No Headroom"],
}


@pytest.fixture
def app(tmp_path, monkeypatch) -> AppTest:
    monkeypatch.chdir(tmp_path)
    workbook = tmp_path / "data" / "raw" / "portfolio.xlsx"
    workbook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DEMO_WORKBOOK, workbook)

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    assert not at.exception
    return at


def _open_pipeline(at: AppTest) -> AppTest:
    at.sidebar.radio[0].set_value("Pipeline").run()
    assert not at.exception
    return at


def _column_headers(at: AppTest) -> list[str]:
    return [
        m.value for m in at.markdown
        if m.value.startswith("**") and m.value.endswith(")**") and "Health score" not in m.value
    ]


def test_pipeline_renders_without_exceptions(app):
    at = _open_pipeline(app)
    assert at.expander


def test_column_counts_match_real_segmentation(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    headers = _column_headers(at)
    assert len(headers) == len(COLUMN_TITLES)

    for title in COLUMN_TITLES:
        expected_count = df["segment"].isin(SEGMENTS_BY_COLUMN[title]).sum()
        expected_header = f"**{title} ({expected_count})**"
        assert expected_header in headers, f"missing/mismatched header for {title}: {headers}"


def test_column_gmv_totals_match_real_data(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    captions = [c.value for c in at.caption if "total GMV" in c.value]
    assert len(captions) == len(COLUMN_TITLES)

    for title, caption in zip(COLUMN_TITLES, captions):
        expected_total = df.loc[df["segment"].isin(SEGMENTS_BY_COLUMN[title]), "gmv_total_6m"].sum()
        assert caption == f"${expected_total:,.0f} total GMV"


def test_card_shows_correct_account_health_and_metric(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    declining = df[df["segment"] == "Declining"].iloc[0]
    account_id = declining["account_id"]
    expected_score, _ = compute_health_score(declining)
    expected_trend = f"{declining['gmv_trend_pct']:.0f}% trend"

    matches = [e for e in at.expander if e.label.startswith(f"**{account_id}**")]
    assert len(matches) == 1, f"expected exactly one card for {account_id}, found {len(matches)}"

    label = matches[0].label
    assert f"[{expected_score}]" in label
    assert expected_trend in label


def test_clicking_a_card_expands_correct_accounts_details(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    declining = df[df["segment"] == "Declining"].iloc[0]
    account_id = declining["account_id"]

    # Content inside an expander is always rendered by Streamlit (open or
    # not), so locating the widgets by their per-account key is equivalent
    # to "clicking" that card open and reading what's inside it.
    tone_radio = at.radio(key=f"tone_{account_id}")
    assert tone_radio.value == "Direct"

    subject = at.text_input(key=f"subject_{account_id}_Direct")
    message = at.text_area(key=f"message_{account_id}_Direct")
    assert account_id in subject.value or account_id in message.value

    # Health factor breakdown should list this account's real factors.
    _, expected_factors = compute_health_score(declining)
    markdown_values = {m.value for m in at.markdown}
    for factor in expected_factors:
        arrow = "↑" if factor["direction"] == "up" else "↓"
        color = "green" if factor["impact"] == "positive" else "red"
        assert f":{color}[{arrow}] {factor['label']}" in markdown_values


def test_tone_switching_changes_displayed_message(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    declining = df[df["segment"] == "Declining"].iloc[0]
    account_id = declining["account_id"]

    tone_radio = at.radio(key=f"tone_{account_id}")
    direct_message = at.text_area(key=f"message_{account_id}_Direct").value
    direct_subject = at.text_input(key=f"subject_{account_id}_Direct").value

    at = tone_radio.set_value("Warm").run()
    assert not at.exception

    warm_message = at.text_area(key=f"message_{account_id}_Warm").value
    warm_subject = at.text_input(key=f"subject_{account_id}_Warm").value

    assert warm_message != direct_message
    assert warm_subject != direct_subject


def test_mark_as_actioned_removes_button_and_shows_actioned(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    declining = df[df["segment"] == "Declining"].iloc[0]
    account_id = declining["account_id"]

    button = at.button(key=f"mark_actioned_{account_id}")
    at = button.click().run()
    assert not at.exception

    at = _open_pipeline(at)
    with pytest.raises(KeyError):
        at.button(key=f"mark_actioned_{account_id}")

    success_messages = [s.value for s in at.success]
    assert "Actioned" in success_messages


def test_neutral_card_has_no_action_and_no_mark_button(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    neutral = df[df["segment"] == "Healthy AM"]
    if neutral.empty:
        pytest.skip("no Healthy AM accounts in the demo workbook to exercise")
    account_id = neutral.iloc[0]["account_id"]

    with pytest.raises(KeyError):
        at.radio(key=f"tone_{account_id}")
    with pytest.raises(KeyError):
        at.button(key=f"mark_actioned_{account_id}")

    captions = [c.value for c in at.caption]
    assert "No action needed for this account." in captions
