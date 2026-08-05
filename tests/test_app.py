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
from datetime import date, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from src.briefing import generate_briefing_text
from src.scoring import compute_health_score
from src.state import STAGE_ACTIONED, STAGE_FOLLOW_UP, STAGE_NEW, add_note, add_task, DEFAULT_DB_PATH

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
    marker = f'<div class="card-line1">{account_id}</div>'
    matches = [m.value for m in at.markdown if marker in m.value]
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


def _first_n_accounts(df, predicate, n):
    matches = df[predicate(df)]
    if len(matches) < n:
        pytest.skip(f"demo workbook doesn't have {n} matching accounts to exercise this case")
    return matches.iloc[:n]


def _select_card(at: AppTest, account_id: str) -> AppTest:
    """"Check" a card's selection checkbox."""
    at = at.checkbox(key=f"select_{account_id}").set_value(True).run()
    assert not at.exception
    return at


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

    assert f">{row['segment']}</span>" in card
    if row["at_risk_detail"] is not None:
        assert f">{row['at_risk_detail']}</span>" not in card


def test_card_shows_two_tags_when_at_risk_detail_differs_from_segment(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(
        df,
        lambda d: (d["is_at_risk"] == True) & (d["segment"] != d["at_risk_detail"]),  # noqa: E712
    )
    card = _card_markdown(at, row["account_id"])

    assert f">{row['segment']}</span>" in card
    assert f">{row['at_risk_detail']}</span>" in card


def test_card_omits_duplicate_tag_when_at_risk_detail_equals_segment(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(
        df,
        lambda d: (d["is_at_risk"] == True) & (d["segment"] == d["at_risk_detail"]),  # noqa: E712
    )
    card = _card_markdown(at, row["account_id"])

    assert card.count(f">{row['segment']}</span>") == 1


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
        color = "sage" if factor["impact"] == "positive" else "rust"
        expected = (
            f'<span class="badge-inline badge-inline-{color}">{arrow}</span> '
            f'{factor["label"]}'
        )
        assert expected in markdown_values


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
# Format tabs: Email / Text / Call
# ---------------------------------------------------------------------
def test_drafted_outreach_shows_three_format_tabs(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    tabs = at.tabs
    assert [t.label for t in tabs] == ["Email", "Text", "Call"]


def test_email_tab_is_unchanged_subject_and_message(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    email_tab = at.tabs[0]
    subject = email_tab.text_input(key=f"subject_{account_id}_Direct")
    message = email_tab.text_area(key=f"message_{account_id}_Direct")
    assert account_id in subject.value or account_id in message.value


def test_text_tab_drops_signoff_and_has_copy_code_block(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    text_tab = at.tabs[1]
    code_blocks = text_tab.code
    assert len(code_blocks) == 1
    text_message = code_blocks[0].value
    assert "The Fleek Team" not in text_message
    assert "Best," not in text_message
    assert "Regards," not in text_message


def test_text_tab_shortens_long_messages(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    email_message = at.tabs[0].text_area(key=f"message_{account_id}_Direct").value
    text_message = at.tabs[1].code[0].value
    assert len(text_message) <= len(email_message)


def test_call_tab_shows_four_part_talking_points_script(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    call_tab = at.tabs[2]
    markdown_values = [m.value for m in call_tab.markdown]
    for label in ("**Opening line**", "**Key point to make**", "**If they push back**", "**Close with**"):
        assert label in markdown_values


def test_call_tab_script_is_personalized_with_account_numbers(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] == "Bundle nudge")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    call_tab = at.tabs[2]
    markdown_text = " ".join(m.value for m in call_tab.markdown)
    expected_pct = f"{row['bundle_gmv_share_pct']:.0f}%"
    assert expected_pct in markdown_text


def test_switching_tone_updates_all_three_format_tabs(app):
    at = _open_pipeline(app)
    df = at.session_state.df

    row = _first_account(df, lambda d: d["action"] != "None")
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    direct_email = at.tabs[0].text_area(key=f"message_{account_id}_Direct").value
    direct_text = at.tabs[1].code[0].value

    tone_radio = at.radio(key=f"tone_{account_id}")
    at = tone_radio.set_value("Warm").run()
    assert not at.exception

    warm_email = at.tabs[0].text_area(key=f"message_{account_id}_Warm").value
    warm_text = at.tabs[1].code[0].value

    assert warm_email != direct_email
    assert warm_text != direct_text


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
# Tasks
# ---------------------------------------------------------------------
def test_adding_a_task_shows_it_under_open_tasks(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    row = df.iloc[0]
    account_id = row["account_id"]
    at = _open_account(at, account_id)

    assert "No open tasks." in [c.value for c in at.caption]

    due = date.today() + timedelta(days=5)
    at.text_input(key=f"new_task_{account_id}").set_value("Follow up call")
    at.date_input(key=f"new_task_due_{account_id}").set_value(due)
    at = at.button(key=f"submit_task_{account_id}").click().run()
    assert not at.exception

    markdown_values = " ".join(m.value for m in at.markdown)
    assert "Follow up call" in markdown_values
    assert f"Due: {due.isoformat()}" in markdown_values
    assert "No open tasks." not in [c.value for c in at.caption]


def test_task_card_badge_shows_overdue_in_rust(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    account_id = df.iloc[0]["account_id"]

    yesterday = date.today() - timedelta(days=1)
    add_task(account_id, "Overdue task", yesterday, db_path=DEFAULT_DB_PATH)
    at = at.run()

    card = _card_markdown(at, account_id)
    assert "Overdue by 1 day" in card
    assert 'class="tag tag-rust">Overdue by 1 day</span>' in card


def test_task_card_badge_shows_due_in_n_days_neutral(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    account_id = df.iloc[0]["account_id"]

    add_task(account_id, "Upcoming task", date.today() + timedelta(days=3), db_path=DEFAULT_DB_PATH)
    at = at.run()

    card = _card_markdown(at, account_id)
    assert 'class="tag tag-neutral">Due in 3 days</span>' in card


def test_task_card_badge_absent_when_no_tasks(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    account_id = df.iloc[0]["account_id"]

    card = _card_markdown(at, account_id)
    assert "Due in" not in card
    assert "Overdue" not in card


def test_note_count_badge_shows_on_card_when_notes_exist(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    account_id = df.iloc[0]["account_id"]

    add_note(account_id, "Called the AM", db_path=DEFAULT_DB_PATH)
    at = at.run()

    card = _card_markdown(at, account_id)
    assert 'class="tag tag-neutral">1 note</span>' in card


def test_completing_a_task_removes_it_from_incomplete_and_updates_card_badge(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    account_id = df.iloc[0]["account_id"]

    yesterday = date.today() - timedelta(days=1)
    add_task(account_id, "Overdue task", yesterday, db_path=DEFAULT_DB_PATH)
    at = at.run()

    card = _card_markdown(at, account_id)
    assert "Overdue by 1 day" in card

    at = _open_account(at, account_id)
    complete_checkboxes = [cb for cb in at.checkbox if cb.key.startswith("complete_task_")]
    assert len(complete_checkboxes) == 1
    at = complete_checkboxes[0].set_value(True).run()
    assert not at.exception

    assert "No open tasks." in [c.value for c in at.caption]
    markdown_values = " ".join(m.value for m in at.markdown)
    assert "Overdue task" in markdown_values  # now shown under Completed

    at = at.button(key="back_to_pipeline").click().run()
    card = _card_markdown(at, account_id)
    assert "Overdue" not in card
    assert "Due in" not in card


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


# ---------------------------------------------------------------------
# Multi-select + bulk actions
# ---------------------------------------------------------------------
def test_selecting_accounts_shows_bulk_actions_bar_with_correct_count(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    rows = _first_n_accounts(df, lambda d: d["action"] != "None", 2)

    assert at.session_state.selected_account_ids == set()
    with pytest.raises(KeyError):
        at.button(key="bulk_mark_actioned")

    for _, row in rows.iterrows():
        at = _select_card(at, row["account_id"])

    assert at.session_state.selected_account_ids == set(rows["account_id"])

    button_labels = [b.label for b in at.button]
    assert "Mark 2 as Actioned" in button_labels
    assert "Generate messages for 2 selected" in button_labels


def test_bulk_mark_actioned_updates_all_selected_accounts_and_clears_selection(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    rows = _first_n_accounts(df, lambda d: d["action"] != "None", 3)
    account_ids = list(rows["account_id"])

    for account_id in account_ids:
        at = _select_card(at, account_id)

    at = at.button(key="bulk_mark_actioned").click().run()
    assert not at.exception

    # Selection cleared: no bulk actions bar, and every checkbox unchecked.
    assert at.session_state.selected_account_ids == set()
    with pytest.raises(KeyError):
        at.button(key="bulk_mark_actioned")
    for account_id in account_ids:
        assert at.checkbox(key=f"select_{account_id}").value is False

    # All three accounts actually moved to Actioned, not just the first.
    headers = _column_headers(at)
    assert "**Actioned (3)**" in headers
    assert f"**New ({len(df) - 3})**" in headers

    for account_id in account_ids:
        at2 = _open_account(at, account_id)
        assert at2.button(key=f"undo_{account_id}") is not None
        with pytest.raises(KeyError):
            at2.button(key=f"mark_actioned_{account_id}")
        at = at2.button(key="back_to_pipeline").click().run()


def test_generate_messages_preview_shows_distinct_per_account_content(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    rows = _first_n_accounts(df, lambda d: d["action"] != "None", 2)
    account_ids = list(rows["account_id"])

    for account_id in account_ids:
        at = _select_card(at, account_id)

    at = at.button(key="bulk_generate_messages").click().run()
    assert not at.exception

    subjects = {
        account_id: at.text_input(key=f"bulk_subject_{account_id}").value
        for account_id in account_ids
    }
    messages = {
        account_id: at.text_area(key=f"bulk_message_{account_id}").value
        for account_id in account_ids
    }

    # Each account's own subject/message actually appears, and they're
    # genuinely different across accounts, not one message duplicated.
    first_id, second_id = account_ids
    assert subjects[first_id] != subjects[second_id]
    assert messages[first_id] != messages[second_id]

    # Content matches what the account's own Account Overview page would
    # show for the Direct tone, confirming it's the real personalized draft.
    for account_id in account_ids:
        at_detail = _open_account(at, account_id)
        expected_subject = at_detail.text_input(key=f"subject_{account_id}_Direct").value
        expected_message = at_detail.text_area(key=f"message_{account_id}_Direct").value
        assert subjects[account_id] == expected_subject
        assert messages[account_id] == expected_message
        at = at_detail.button(key="back_to_pipeline").click().run()


def test_mark_all_as_actioned_button_in_preview_bulk_marks_after_review(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    rows = _first_n_accounts(df, lambda d: d["action"] != "None", 2)
    account_ids = list(rows["account_id"])

    for account_id in account_ids:
        at = _select_card(at, account_id)

    at = at.button(key="bulk_generate_messages").click().run()
    assert not at.exception

    # Nothing marked yet just from previewing.
    headers = _column_headers(at)
    assert f"**New ({len(df)})**" in headers
    assert "**Actioned (0)**" in headers

    at = at.button(key="bulk_mark_actioned_after_preview").click().run()
    assert not at.exception

    headers = _column_headers(at)
    assert "**Actioned (2)**" in headers
    assert f"**New ({len(df) - 2})**" in headers

    assert at.session_state.selected_account_ids == set()
    for account_id in account_ids:
        assert at.checkbox(key=f"select_{account_id}").value is False
    with pytest.raises(KeyError):
        at.button(key="bulk_mark_actioned")


def test_selection_clears_when_navigating_to_account_overview(app):
    at = _open_pipeline(app)
    df = at.session_state.df
    rows = _first_n_accounts(df, lambda d: d["action"] != "None", 2)
    account_ids = list(rows["account_id"])

    for account_id in account_ids:
        at = _select_card(at, account_id)
    assert at.session_state.selected_account_ids == set(account_ids)

    at = _open_account(at, account_ids[0])
    assert at.session_state.selected_account_ids == set()

    at = at.button(key="back_to_pipeline").click().run()
    assert not at.exception

    # The checkboxes themselves are unchecked too, not just the tracking set.
    for account_id in account_ids:
        assert at.checkbox(key=f"select_{account_id}").value is False
    with pytest.raises(KeyError):
        at.button(key="bulk_mark_actioned")


# ---------------------------------------------------------------------
# Overview: Morning Briefing (scheduled_run.py's file if present, else
# generated live from the current in-session results)
# ---------------------------------------------------------------------
def test_overview_falls_back_to_live_briefing_when_no_file_exists(app):
    at = app  # default view on load is Overview
    assert not at.exception

    df = at.session_state.df
    summary = at.session_state.sync_summary
    expected = generate_briefing_text(df, summary)

    rendered = "\n".join(m.value for m in at.markdown)
    assert "Morning Briefing" in rendered
    for line in expected.splitlines():
        assert line in rendered


def test_overview_reads_todays_briefing_file_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workbook = tmp_path / "data" / "raw" / "portfolio.xlsx"
    workbook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DEMO_WORKBOOK, workbook)

    briefings_dir = tmp_path / "data" / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    briefing_path = briefings_dir / f"briefing_{date.today().isoformat()}.md"
    briefing_path.write_text("## Morning Briefing\n\nA canned briefing from a scheduled run.")

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.run()
    assert not at.exception

    rendered = "\n".join(m.value for m in at.markdown)
    assert "A canned briefing from a scheduled run." in rendered
