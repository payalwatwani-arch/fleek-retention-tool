"""AppTest coverage for app.py's Action Center tone-variant picker.

Runs the real app.py script via Streamlit's AppTest harness, pointed at a
scratch working directory seeded with a copy of the demo workbook — so a
test run never touches the repo's own data/raw or data/state files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "app.py"
DEMO_WORKBOOK = PROJECT_ROOT / "data" / "raw" / "demo_accounts.xlsx"


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


def _open_action_center(at: AppTest) -> AppTest:
    at.sidebar.radio[0].set_value("Action Center").run()
    assert not at.exception
    return at


def test_action_center_renders_without_exceptions(app):
    at = _open_action_center(app)
    # Either the queue is empty (all-caught-up message) or the account
    # picker + tone selector rendered — both are exception-free states.
    assert at.success or at.selectbox


def test_tone_switching_changes_displayed_message(app):
    at = _open_action_center(app)
    if not at.selectbox:
        pytest.skip("no pending actions in the demo workbook to exercise")

    account_id = at.selectbox[0].value
    tone_radio = at.radio(key=f"tone_{account_id}")

    assert tone_radio.value == "Direct"
    direct_message = at.text_area(key=f"message_{account_id}_Direct").value
    direct_subject = at.text_input(key=f"subject_{account_id}_Direct").value

    at = tone_radio.set_value("Warm").run()
    assert not at.exception

    warm_message = at.text_area(key=f"message_{account_id}_Warm").value
    warm_subject = at.text_input(key=f"subject_{account_id}_Warm").value

    assert warm_message != direct_message
    assert warm_subject != direct_subject

    at = at.radio(key=f"tone_{account_id}").set_value("Formal").run()
    assert not at.exception
    formal_message = at.text_area(key=f"message_{account_id}_Formal").value
    assert formal_message not in (direct_message, warm_message)
