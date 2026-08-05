"""Persistent state tracking so the retention pipeline can be re-run daily
without repeating work or losing track of what's already been handled.

Reconciles each day's `draft_actions()` output against a small SQLite
database (`data/state/portfolio.db`, gitignored). An account's `data_hash`
is a fingerprint of the input fields `segmentation.py` actually reads —
if none of those changed, the account's state is left completely alone,
regardless of what the daily run recomputed.

Each account sits in one of three contact stages:

  - "New"       — never actioned yet.
  - "Actioned"  — an AM has reached out. Also where an account lands (and
                  stays) once its action resolves to "None" — the pipeline
                  itself reflects that resolution via segment/action, so
                  there's no separate "Resolved" stage.
  - "Follow-up" — was actioned, but the account's real data changed since
                  and it still needs action. `touch_count` counts how many
                  times this has happened.

Run directly to print the current state summary (from the project root):

    python -m src.state
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import pandas as pd

DEFAULT_DB_PATH = Path("data/state/portfolio.db")

ACCOUNT_ID_COLUMN = "account_id"
SEGMENT_COLUMN = "segment"
ACTION_COLUMN = "action"

NO_ACTION = "None"

STAGE_NEW = "New"
STAGE_ACTIONED = "Actioned"
STAGE_FOLLOW_UP = "Follow-up"

# Reverts one stage backward: Follow-up -> Actioned -> New. "New" has no
# entry, since there's nothing before it to undo to.
_UNDO_TARGET = {
    STAGE_FOLLOW_UP: STAGE_ACTIONED,
    STAGE_ACTIONED: STAGE_NEW,
}

# The input fields segmentation.py actually reads. A data_hash over exactly
# these fields means the hash only changes when something that could change
# the segment/action assignment actually changed — not on cosmetic noise
# elsewhere in the row (notes, csm_owner, last_login_date, etc.).
HASH_FIELDS = [
    "broker_reliance_pct",
    "app_active_days_6m",
    "pdp_views_6m",
    "make_an_offer_6m",
    "bundle_gmv_share_pct",
    "handpick_orders",
    "bundle_orders",
    "chat_threads",
    "video_call_requests",
    "gmv_trend_pct",
    "gmv_total_6m",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_state (
  account_id TEXT PRIMARY KEY,
  last_segment TEXT NOT NULL,
  last_action TEXT NOT NULL,
  data_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('New', 'Actioned', 'Follow-up')),
  touch_count INTEGER NOT NULL DEFAULT 0,
  actioned_date TEXT,
  first_seen_date TEXT NOT NULL,
  last_updated_date TEXT NOT NULL,
  notes TEXT
);
"""

# Tasks are a distinct concept from notes: a task carries a due date and a
# completion state, rather than being a free-text log entry. Kept in their
# own table (one row per task, several per account) rather than folded into
# account_state's JSON columns.
_TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  text TEXT NOT NULL,
  due_date TEXT NOT NULL,
  completed INTEGER NOT NULL DEFAULT 0,
  created_date TEXT NOT NULL
);
"""


def _connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.execute(_TASKS_SCHEMA)
    # `notes` was added after the original table shipped, so existing DBs
    # need a migration — CREATE TABLE IF NOT EXISTS alone won't add it.
    try:
        conn.execute("ALTER TABLE account_state ADD COLUMN notes TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return conn


def compute_data_hash(row) -> str:
    """Hash the key input fields for one account row (a dict or a pandas
    Series). Stable across field order; values are stringified with a
    field-name prefix so e.g. an empty string vs. a genuinely missing field
    can't collide."""
    parts = []
    for field in HASH_FIELDS:
        value = row[field]
        if pd.isna(value):
            value = ""
        parts.append(f"{field}={value}")
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _today() -> str:
    return date.today().isoformat()


def sync_state(df: pd.DataFrame, db_path=DEFAULT_DB_PATH) -> dict:
    """Reconcile the full draft_actions() output against the state DB.

    - Unseen account_id: inserted at stage "New".
    - Known account_id, data_hash unchanged: left completely untouched.
    - Known account_id, data_hash changed, still at "New": segment/action
      updated in place, stage stays "New" (nobody has acted on it yet).
    - Known account_id, data_hash changed, action now "None": the account's
      data changed such that it no longer needs action (e.g. it moved to a
      neutral segment) — that resolution is reflected via segment/action
      alone, so the stage settles at "Actioned" (or stays there).
    - Known account_id, data_hash changed, action still needed: stage moves
      to "Follow-up" and touch_count increments, whether it came from
      "Actioned" or was already in "Follow-up".

    Returns {"new_count", "follow_up_count", "resolved_count", "unchanged_count"}.
    """
    today = _today()
    new_count = 0
    follow_up_count = 0
    resolved_count = 0
    unchanged_count = 0

    with closing(_connect(db_path)) as conn:
        cur = conn.cursor()
        for _, row in df.iterrows():
            account_id = row[ACCOUNT_ID_COLUMN]
            segment = row[SEGMENT_COLUMN]
            action = row[ACTION_COLUMN]
            data_hash = compute_data_hash(row)

            cur.execute(
                "SELECT last_action, data_hash, status FROM account_state "
                "WHERE account_id = ?",
                (account_id,),
            )
            existing = cur.fetchone()

            if existing is None:
                cur.execute(
                    "INSERT INTO account_state "
                    "(account_id, last_segment, last_action, data_hash, status, "
                    "touch_count, actioned_date, first_seen_date, last_updated_date) "
                    "VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?)",
                    (account_id, segment, action, data_hash, STAGE_NEW, today, today),
                )
                new_count += 1
                continue

            prev_action, prev_hash, prev_status = existing

            if data_hash == prev_hash:
                unchanged_count += 1
                continue

            if prev_status == STAGE_NEW:
                cur.execute(
                    "UPDATE account_state SET last_segment = ?, last_action = ?, "
                    "data_hash = ?, last_updated_date = ? WHERE account_id = ?",
                    (segment, action, data_hash, today, account_id),
                )
                unchanged_count += 1
            elif action == NO_ACTION:
                cur.execute(
                    "UPDATE account_state SET last_segment = ?, last_action = ?, "
                    "data_hash = ?, status = ?, last_updated_date = ? "
                    "WHERE account_id = ?",
                    (segment, action, data_hash, STAGE_ACTIONED, today, account_id),
                )
                resolved_count += 1
            else:
                cur.execute(
                    "UPDATE account_state SET last_segment = ?, last_action = ?, "
                    "data_hash = ?, status = ?, touch_count = touch_count + 1, "
                    "last_updated_date = ? WHERE account_id = ?",
                    (segment, action, data_hash, STAGE_FOLLOW_UP, today, account_id),
                )
                follow_up_count += 1

        conn.commit()

    return {
        "new_count": new_count,
        "follow_up_count": follow_up_count,
        "resolved_count": resolved_count,
        "unchanged_count": unchanged_count,
    }


def mark_actioned(account_id: str, db_path=DEFAULT_DB_PATH) -> None:
    """Move one account to "Actioned" today (from "New" or "Follow-up")."""
    today = _today()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE account_state SET status = ?, actioned_date = ?, "
            "last_updated_date = ? WHERE account_id = ?",
            (STAGE_ACTIONED, today, today, account_id),
        )
        conn.commit()


def undo_action(account_id: str, db_path=DEFAULT_DB_PATH) -> None:
    """Revert one account a single stage backward: Follow-up -> Actioned,
    Actioned -> New. No-op for an account already at "New" or not found."""
    today = _today()
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT status, touch_count FROM account_state WHERE account_id = ?",
            (account_id,),
        )
        row = cur.fetchone()
        if row is None:
            return

        status, touch_count = row
        target = _UNDO_TARGET.get(status)
        if target is None:
            return

        if target == STAGE_NEW:
            conn.execute(
                "UPDATE account_state SET status = ?, actioned_date = NULL, "
                "last_updated_date = ? WHERE account_id = ?",
                (target, today, account_id),
            )
        else:
            conn.execute(
                "UPDATE account_state SET status = ?, touch_count = ?, "
                "last_updated_date = ? WHERE account_id = ?",
                (target, max(0, touch_count - 1), today, account_id),
            )
        conn.commit()


def add_note(account_id: str, note_text: str, db_path=DEFAULT_DB_PATH) -> None:
    """Append a new timestamped note for one account. Notes accumulate (the
    `notes` column holds a JSON list of {"timestamp", "text"} entries) rather
    than overwrite. No-op if the account isn't in the state DB."""
    note_text = note_text.strip()
    if not note_text:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT notes FROM account_state WHERE account_id = ?", (account_id,)
        )
        row = cur.fetchone()
        if row is None:
            return

        notes = json.loads(row[0]) if row[0] else []
        notes.append({"timestamp": timestamp, "text": note_text})
        conn.execute(
            "UPDATE account_state SET notes = ? WHERE account_id = ?",
            (json.dumps(notes), account_id),
        )
        conn.commit()


def get_notes(account_id: str, db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Return this account's notes as a list of {"timestamp", "text"} dicts,
    most recent first. Empty list if there are none (or the account isn't
    in the state DB)."""
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT notes FROM account_state WHERE account_id = ?", (account_id,)
        )
        row = cur.fetchone()

    if row is None or not row[0]:
        return []
    return list(reversed(json.loads(row[0])))


def add_task(account_id: str, text: str, due_date, db_path=DEFAULT_DB_PATH) -> None:
    """Create a new open task for an account. `due_date` may be a `date` or
    an ISO ("YYYY-MM-DD") string. No-op if `text` is blank."""
    text = text.strip()
    if not text:
        return
    if isinstance(due_date, date):
        due_date = due_date.isoformat()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO tasks (account_id, text, due_date, completed, created_date) "
            "VALUES (?, ?, ?, 0, ?)",
            (account_id, text, due_date, _today()),
        )
        conn.commit()


def get_tasks(account_id: str, db_path=DEFAULT_DB_PATH) -> list[dict]:
    """Return this account's tasks as a list of {"task_id", "account_id",
    "text", "due_date", "completed", "created_date"} dicts: incomplete tasks
    first, sorted by due_date ascending within each group."""
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT task_id, account_id, text, due_date, completed, created_date "
            "FROM tasks WHERE account_id = ? ORDER BY completed ASC, due_date ASC",
            (account_id,),
        )
        rows = cur.fetchall()

    return [
        {
            "task_id": task_id,
            "account_id": acc_id,
            "text": text,
            "due_date": due_date,
            "completed": bool(completed),
            "created_date": created_date,
        }
        for task_id, acc_id, text, due_date, completed, created_date in rows
    ]


def complete_task(task_id: int, db_path=DEFAULT_DB_PATH) -> None:
    """Mark one task done."""
    with closing(_connect(db_path)) as conn:
        conn.execute("UPDATE tasks SET completed = 1 WHERE task_id = ?", (task_id,))
        conn.commit()


def get_task_summary(account_id: str, db_path=DEFAULT_DB_PATH) -> dict:
    """Return {"has_overdue", "next_due_date", "incomplete_count"} for one
    account's incomplete tasks. `next_due_date` is the soonest incomplete
    task's due date (an ISO string), or None if there are no open tasks."""
    today = _today()
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT due_date FROM tasks WHERE account_id = ? AND completed = 0 "
            "ORDER BY due_date ASC",
            (account_id,),
        )
        due_dates = [row[0] for row in cur.fetchall()]

    return {
        "has_overdue": any(due < today for due in due_dates),
        "next_due_date": due_dates[0] if due_dates else None,
        "incomplete_count": len(due_dates),
    }


def get_by_stage(stage: str, db_path=DEFAULT_DB_PATH) -> pd.DataFrame:
    """Return all accounts currently at the given contact stage."""
    with closing(_connect(db_path)) as conn:
        return pd.read_sql_query(
            "SELECT * FROM account_state WHERE status = ? ORDER BY account_id",
            conn,
            params=(stage,),
        )


def get_state_summary(db_path=DEFAULT_DB_PATH) -> dict:
    """Return {"total", "new", "actioned", "follow_up"} counts for the state DB."""
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT status, COUNT(*) FROM account_state GROUP BY status"
        )
        counts = dict(cur.fetchall())

    new = counts.get(STAGE_NEW, 0)
    actioned = counts.get(STAGE_ACTIONED, 0)
    follow_up = counts.get(STAGE_FOLLOW_UP, 0)
    return {
        "total": new + actioned + follow_up,
        "new": new,
        "actioned": actioned,
        "follow_up": follow_up,
    }


if __name__ == "__main__":
    summary = get_state_summary()
    print("=" * 60)
    print("STATE SUMMARY")
    print("=" * 60)
    print(f"Total tracked accounts : {summary['total']}")
    print(f"New                     : {summary['new']}")
    print(f"Actioned                : {summary['actioned']}")
    print(f"Follow-up               : {summary['follow_up']}")
    print("=" * 60)
