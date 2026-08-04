"""AppTest coverage for app.py's Kanban-style Pipeline board and the
dedicated Account Overview page a card navigates to.

Runs the real app.py script via Streamlit's AppTest harness, pointed at a
scratch working directory seeded with a copy of the demo workbook — so a
test run never touches the repo's own data/raw or data/state files.

Columns are contact stage (New / Actioned / Follow-up), not segment.
Segment (plus an at-risk detail, when it's genuinely extra information)
shows as tag(s) on the card instead.

Clicking a card's "View details →" button navigates away from the board to
a full Account Overview page (rather than expanding inline); "← Back to
Pipeline" returns to the board.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.scoring import compute_health_score
from src.state import STAGE_ACTIONED, STAGE_FOLLOW_UP, STAGE_NEW

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "app.py"
DEMO_WORKBOOK = PROJECT_ROOT / "data" / "raw" / "demo_accounts.xlsx"

STAGE_TITLES = [STAGE_NEW, STAGE_ACTIONED, STAGE_FOLLOW_UP]


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


def _card_markdown(at: AppTest, account_id: str) -> str:
    matches = [m.value for m in at.markdown if m.value.startswith(f"**{account_id}**")]
    assert len(matches) == 1, f"expected exactly one card for {account_id}, found {len(matches)}"
    return matches[0]


def _open_account(at: AppTest, account_id: str) -> AppTest:
    """"Click" a card's View details button, landing on its Account
    Overview page."""
    at = at.button(key=f"view_{account_id}").click().run()
    assert not at.exception
    return at


def _first_account(df, predicate):
    matches = df[predicate(df)]
    if matches.empty:
        pytest.skip("no matching account in the demo workbook to exercise this case")
    return matches.iloc[0]


# ---------------------------------------------------------------------
# Board structure: 3 stage columns, live counts
# ---------------------------------------------------------------------
def test_pipeline_renders_without_exceptions(app):
    at = _open_pipeline(app)
    assert at.button


def test_column_titles_are_contact_stages_not_segments(app):
    at = _open_pipeline(app)
    headers = _column_headers(at)
    assert len(headers) == len(STAGE_TITLES)
    for title in STAGE_TITLES:
        assert any(h.startswith(f"**{title} (") for h in headers), headers


def test_new_column_count_matches_all_accounts_on_first_run(app):
    # Nothing has been actioned yet, so every account starts at "New".
    at = _open_pipeline(app)
    df = at.session_state.df
    headers = _column_headers(at)
    assert f"**New ({len(df)})**" in headers
    assert "**Actioned (0)**" in headers
    assert "**Follow-up (0)**" in headers


# ---------------------------------------------------------------------
# Cards: health score + arrow, segment tag(s), status line
# ---------------------------------------------------------------------
def test_card_shows_health_score_with_direction_arrow(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = df.iloc[0]
    account_id = row["account_id"]
    expected_score, expected_factors = compute_health_score(row)
    expected_arrow = "↑" if expected_factors[0]["direction"] == "up" else "↓"

    card = _card_markdown(at, account_id)
    assert f"[{expected_score}] {expected_arrow}" in card


def test_card_shows_single_segment_tag_when_not_at_risk(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["is_at_risk"] == False)  # noqa: E712
    card = _card_markdown(at, row["account_id"])

    assert f"`{row['segment']}`" in card
    if row["at_risk_detail"] is not None:
        assert f"`{row['at_risk_detail']}`" not in card


def test_card_shows_two_tags_when_at_risk_detail_differs_from_segment(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(
        df,
        lambda d: (d["is_at_risk"] == True) & (d["segment"] != d["at_risk_detail"]),  # noqa: E712
    )
    card = _card_markdown(at, row["account_id"])

    assert f"`{row['segment']}`" in card
    assert f"`{row['at_risk_detail']}`" in card


def test_card_omits_duplicate_tag_when_at_risk_detail_equals_segment(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(
        df,
        lambda d: (d["is_at_risk"] == True) & (d["segment"] == d["at_risk_detail"]),  # noqa: E712
    )
    card = _card_markdown(at, row["account_id"])

    assert card.count(f"`{row['segment']}`") == 1


def test_new_account_status_line_is_blank(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = df.iloc[0]
    card = _card_markdown(at, row["account_id"])
    assert "Actioned" not in card
    assert "Follow-up needed" not in card


# ---------------------------------------------------------------------
# Account Overview page
# ---------------------------------------------------------------------
def test_neutral_account_shows_no_action_needed_and_no_buttons(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] == "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    with pytest.raises(KeyError):
        at.radio(key=f"tone_{account_id}")
    with pytest.raises(KeyError):
        at.button(key=f"mark_actioned_{account_id}")
    with pytest.raises(KeyError):
        at.button(key=f"undo_{account_id}")

    captions = [c.value for c in at.caption]
    assert "No action needed." in captions


def test_clicking_a_card_opens_correct_accounts_overview_page(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    titles = [t.value for t in at.title]
    assert account_id in titles

    tone_radio = at.radio(key=f"tone_{account_id}")
    assert tone_radio.value == "Direct"

    subject = at.text_input(key=f"subject_{account_id}_Direct")
    message = at.text_area(key=f"message_{account_id}_Direct")
    assert account_id in subject.value or account_id in message.value

    _, expected_factors = compute_health_score(row)
    markdown_values = {m.value for m in at.markdown}
    for factor in expected_factors:
        arrow = "↑" if factor["direction"] == "up" else "↓"
        color = "green" if factor["impact"] == "positive" else "red"
        assert f":{color}[{arrow}] {factor['label']}" in markdown_values


def test_account_overview_shows_full_account_numbers_grid(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    row = df.iloc[0]
    at = _open_account(at, row["account_id"])

    markdown_values = " ".join(m.value for m in at.markdown)
    assert f"${row['gmv_total_6m']:,.0f}" in markdown_values
    assert row["region"] in markdown_values
    assert row["ownership"] in markdown_values


def test_back_to_pipeline_returns_to_the_board(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    row = df.iloc[0]
    at = _open_account(at, row["account_id"])

    at = at.button(key="back_to_pipeline").click().run()
    assert not at.exception

    headers = _column_headers(at)
    assert len(headers) == len(STAGE_TITLES)
    assert at.button(key=f"view_{row['account_id']}") is not None


def test_tone_switching_changes_displayed_message(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    tone_radio = at.radio(key=f"tone_{account_id}")
    direct_message = at.text_area(key=f"message_{account_id}_Direct").value
    direct_subject = at.text_input(key=f"subject_{account_id}_Direct").value

    at = tone_radio.set_value("Warm").run()
    assert not at.exception

    warm_message = at.text_area(key=f"message_{account_id}_Warm").value
    warm_subject = at.text_input(key=f"subject_{account_id}_Warm").value

    assert warm_message != direct_message
    assert warm_subject != direct_subject


# ---------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------
def test_adding_a_note_persists_and_displays_most_recent_first(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    row = df.iloc[0]
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    assert "No notes yet." in [c.value for c in at.caption]

    at.text_area(key=f"new_note_{account_id}").set_value("First note")
    at = at.button(key=f"submit_note_{account_id}").click().run()
    assert not at.exception

    # st.write(note_text) renders each note as a markdown element.
    markdown_values = [m.value for m in at.markdown]
    assert "First note" in markdown_values
    assert "No notes yet." not in [c.value for c in at.caption]

    at.text_area(key=f"new_note_{account_id}").set_value("Second note")
    at = at.button(key=f"submit_note_{account_id}").click().run()
    assert not at.exception

    markdown_values = [m.value for m in at.markdown]
    assert "First note" in markdown_values
    assert "Second note" in markdown_values
    assert markdown_values.index("Second note") < markdown_values.index("First note")


# ---------------------------------------------------------------------
# Stage transitions and undo
# ---------------------------------------------------------------------
def test_mark_as_actioned_moves_card_from_new_to_actioned(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    at = at.button(key=f"mark_actioned_{account_id}").click().run()
    assert not at.exception

    at = at.button(key="back_to_pipeline").click().run()
    headers = _column_headers(at)
    assert "**Actioned (1)**" in headers
    assert f"**New ({len(df) - 1})**" in headers

    at = _open_account(at, account_id)
    with pytest.raises(KeyError):
        at.button(key=f"mark_actioned_{account_id}")
    undo_button = at.button(key=f"undo_{account_id}")
    assert undo_button is not None


def test_undo_reverts_actioned_card_back_to_new(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)
    at = at.button(key=f"mark_actioned_{account_id}").click().run()

    at = at.button(key=f"undo_{account_id}").click().run()
    assert not at.exception

    at = at.button(key="back_to_pipeline").click().run()
    headers = _column_headers(at)
    assert f"**New ({len(df)})**" in headers
    assert "**Actioned (0)**" in headers

    at = _open_account(at, account_id)
    with pytest.raises(KeyError):
        at.button(key=f"undo_{account_id}")
    assert at.button(key=f"mark_actioned_{account_id}") is not None


# ---------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------
def test_filter_bar_has_region_persona_ownership_and_at_risk_controls(app):
    at = _open_pipeline(app)
    labels = {sb.label for sb in at.selectbox}
    assert {"Region", "Buyer persona", "Ownership"} <= labels
    assert any(cb.label == "At-risk only" for cb in at.checkbox)


def test_at_risk_only_filter_narrows_board_and_updates_counts(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    expected_at_risk = int((df["is_at_risk"] == True).sum())  # noqa: E712
    if expected_at_risk == 0 or expected_at_risk == len(df):
        pytest.skip("demo workbook doesn't give a meaningful at-risk split to test")

    at_risk_checkbox = [c for c in at.checkbox if c.label == "At-risk only"][0]
    at = at_risk_checkbox.set_value(True).run()
    assert not at.exception

    headers = _column_headers(at)
    assert f"**New ({expected_at_risk})**" in headers


def test_region_filter_narrows_board_and_updates_counts(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    region = df["region"].value_counts().idxmax()
    expected_count = int((df["region"] == region).sum())

    region_select = [s for s in at.selectbox if s.label == "Region"][0]
    at = region_select.set_value(region).run()
    assert not at.exception

    headers = _column_headers(at)
    assert f"**New ({expected_count})**" in headers


def test_ownership_filter_narrows_board_and_updates_counts(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    ownership = df["ownership"].value_counts().idxmax()
    expected_count = int((df["ownership"] == ownership).sum())

    ownership_select = [s for s in at.selectbox if s.label == "Ownership"][0]
    at = ownership_select.set_value(ownership).run()
    assert not at.exception

    headers = _column_headers(at)
    assert f"**New ({expected_count})**" in headers


def test_filters_apply_across_all_three_columns_simultaneously(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)
    at = at.button(key=f"mark_actioned_{account_id}").click().run()
    at = at.button(key="back_to_pipeline").click().run()

    region = df["region"].value_counts().idxmax()
    expected_total = int((df["region"] == region).sum())
    # 1 if the actioned account is in this region, else 0.
    expected_actioned = 1 if row["region"] == region else 0
    expected_new = expected_total - expected_actioned

    region_select = [s for s in at.selectbox if s.label == "Region"][0]
    at = region_select.set_value(region).run()
    assert not at.exception

    headers = _column_headers(at)
    assert f"**New ({expected_new})**" in headers
    assert f"**Actioned ({expected_actioned})**" in headers
    assert "**Follow-up (0)**" in headers


def test_default_filters_show_everything(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    headers = _column_headers(at)
    assert f"**New ({len(df)})**" in headers
