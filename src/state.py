"""Persistent state tracking so the retention pipeline can be re-run daily
without repeating work or losing track of what's already been handled.

Reconciles each day's `draft_actions()` output against a small SQLite
database (`data/state/portfolio.db`, gitignored). An account's `data_hash`
is a fingerprint of the input fields `segmentation.py` actually reads —
if none of those changed, the account's state is left completely alone,
regardless of what the daily run recomputed.

Run directly to print the current state summary (from the project root):

    python -m src.state
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT_DB_PATH = Path("data/state/portfolio.db")

ACCOUNT_ID_COLUMN = "account_id"
SEGMENT_COLUMN = "segment"
ACTION_COLUMN = "action"

STATUS_PENDING = "pending"
STATUS_ACTIONED = "actioned"

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
  status TEXT NOT NULL CHECK (status IN ('pending', 'actioned')),
  actioned_date TEXT,
  first_seen_date TEXT NOT NULL,
  last_updated_date TEXT NOT NULL
);
"""


def _connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
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

    - Unseen account_id: inserted as "pending".
    - Known account_id, data_hash unchanged: left completely untouched.
    - Known account_id, data_hash changed and the action changed: segment/
      action updated, status reset to "pending".
    - Known account_id, data_hash changed but the action is the same:
      segment/action updated, but status is left as-is.

    Returns {"new_count", "reset_to_pending_count", "unchanged_count"}.
    """
    today = _today()
    new_count = 0
    reset_to_pending_count = 0
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
                    "actioned_date, first_seen_date, last_updated_date) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                    (account_id, segment, action, data_hash, STATUS_PENDING, today, today),
                )
                new_count += 1
                continue

            prev_action, prev_hash, prev_status = existing

            if data_hash == prev_hash:
                unchanged_count += 1
                continue

            if action != prev_action:
                cur.execute(
                    "UPDATE account_state SET last_segment = ?, last_action = ?, "
                    "data_hash = ?, status = ?, last_updated_date = ? "
                    "WHERE account_id = ?",
                    (segment, action, data_hash, STATUS_PENDING, today, account_id),
                )
                reset_to_pending_count += 1
            else:
                cur.execute(
                    "UPDATE account_state SET last_segment = ?, last_action = ?, "
                    "data_hash = ?, last_updated_date = ? WHERE account_id = ?",
                    (segment, action, data_hash, today, account_id),
                )
                unchanged_count += 1

        conn.commit()

    return {
        "new_count": new_count,
        "reset_to_pending_count": reset_to_pending_count,
        "unchanged_count": unchanged_count,
    }


def mark_actioned(account_id: str, db_path=DEFAULT_DB_PATH) -> None:
    """Mark one account as actioned today."""
    today = _today()
    with closing(_connect(db_path)) as conn:
        conn.execute(
            "UPDATE account_state SET status = ?, actioned_date = ?, "
            "last_updated_date = ? WHERE account_id = ?",
            (STATUS_ACTIONED, today, today, account_id),
        )
        conn.commit()


def get_pending(db_path=DEFAULT_DB_PATH) -> pd.DataFrame:
    """Return all accounts currently in "pending" status."""
    with closing(_connect(db_path)) as conn:
        return pd.read_sql_query(
            "SELECT * FROM account_state WHERE status = ? ORDER BY account_id",
            conn,
            params=(STATUS_PENDING,),
        )


def get_state_summary(db_path=DEFAULT_DB_PATH) -> dict:
    """Return {"total", "pending", "actioned"} counts for the state DB."""
    with closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "SELECT status, COUNT(*) FROM account_state GROUP BY status"
        )
        counts = dict(cur.fetchall())

    pending = counts.get(STATUS_PENDING, 0)
    actioned = counts.get(STATUS_ACTIONED, 0)
    return {"total": pending + actioned, "pending": pending, "actioned": actioned}


if __name__ == "__main__":
    summary = get_state_summary()
    print("=" * 60)
    print("STATE SUMMARY")
    print("=" * 60)
    print(f"Total tracked accounts : {summary['total']}")
    print(f"Pending                : {summary['pending']}")
    print(f"Actioned               : {summary['actioned']}")
    print("=" * 60)
