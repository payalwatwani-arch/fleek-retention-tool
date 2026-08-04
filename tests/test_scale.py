"""Scale verification: prove the pipeline still copes at 30,000 accounts,
not just the ~300-account demo book the brief was originally sized against.

This is not a correctness test (segmentation/state correctness is covered by
test_state.py and test_app.py) — it's a timing measurement against a
synthetic 30k-account workbook, run twice against the same unchanged file to
also prove the idempotency check in state.py doesn't get slower or do wasted
work at scale.

Run directly to see the timing numbers printed:

    pytest tests/test_scale.py -s
"""

from __future__ import annotations

import time

from src import pipeline
from src.data_loader import _build_demo_workbook, load_and_clean
from src.segmentation import segment_accounts
from src.nba import draft_actions
from src.state import sync_state

N_ACCOUNTS = 30_000
SLOW_THRESHOLD_SECONDS = 30


def _instrument(monkeypatch, timings: dict):
    """Wrap the four stage functions pipeline.run_pipeline() calls so each
    call's wall-clock time is recorded in `timings`, without touching
    run_pipeline's own logic at all.

    Always wraps the true, un-patched originals (imported directly from
    data_loader/segmentation/nba/state) rather than whatever is currently
    bound on the pipeline module -- calling this twice in the same test
    must not nest a run 2 wrapper around a run 1 wrapper, which would keep
    writing run 2's timings into run 1's dict too."""

    def timed_load(*args, **kwargs):
        start = time.perf_counter()
        result = load_and_clean(*args, **kwargs)
        timings["data loading"] = time.perf_counter() - start
        return result

    def timed_segment(*args, **kwargs):
        start = time.perf_counter()
        result = segment_accounts(*args, **kwargs)
        timings["segmentation"] = time.perf_counter() - start
        return result

    def timed_draft(*args, **kwargs):
        start = time.perf_counter()
        result = draft_actions(*args, **kwargs)
        timings["drafting"] = time.perf_counter() - start
        return result

    def timed_sync(*args, **kwargs):
        start = time.perf_counter()
        result = sync_state(*args, **kwargs)
        timings["state sync"] = time.perf_counter() - start
        return result

    monkeypatch.setattr(pipeline, "load_and_clean", timed_load)
    monkeypatch.setattr(pipeline, "segment_accounts", timed_segment)
    monkeypatch.setattr(pipeline, "draft_actions", timed_draft)
    monkeypatch.setattr(pipeline, "sync_state", timed_sync)


def _print_run(label: str, timings: dict, total: float, summary: dict) -> None:
    print(f"\n{label}")
    for stage, elapsed in timings.items():
        flag = "  <-- over 30s, worth investigating" if elapsed > SLOW_THRESHOLD_SECONDS else ""
        print(f"  {stage:<16} {elapsed:8.3f}s{flag}")
    flag = "  <-- over 30s, worth investigating" if total > SLOW_THRESHOLD_SECONDS else ""
    print(f"  {'TOTAL':<16} {total:8.3f}s{flag}")
    print(
        f"  new={summary['new_count']} "
        f"follow_up={summary['follow_up_count']} "
        f"resolved={summary['resolved_count']} "
        f"unchanged={summary['unchanged_count']}"
    )


def test_pipeline_scales_to_30000_accounts(tmp_path, monkeypatch):
    workbook = tmp_path / "demo_accounts_30k.xlsx"
    db_path = tmp_path / "state" / "portfolio_scale_test.db"

    gen_start = time.perf_counter()
    _build_demo_workbook(workbook, n_accounts=N_ACCOUNTS)
    gen_elapsed = time.perf_counter() - gen_start

    assert not db_path.exists(), "state database must be fresh/empty for run 1"

    timings_1: dict = {}
    _instrument(monkeypatch, timings_1)
    run1_start = time.perf_counter()
    drafted_df, summary_1 = pipeline.run_pipeline(workbook, db_path=db_path)
    run1_total = time.perf_counter() - run1_start

    accounts_processed = summary_1["rows_after_cleaning"]

    # Run 1 against a fresh DB: every account is unseen, so it should all
    # land as "new" and nothing should be follow-up/resolved/unchanged.
    assert summary_1["new_count"] == accounts_processed
    assert summary_1["follow_up_count"] == 0
    assert summary_1["resolved_count"] == 0
    assert summary_1["unchanged_count"] == 0

    timings_2: dict = {}
    _instrument(monkeypatch, timings_2)
    run2_start = time.perf_counter()
    _, summary_2 = pipeline.run_pipeline(workbook, db_path=db_path)
    run2_total = time.perf_counter() - run2_start

    # Run 2 against the SAME unchanged file: nothing should be new, moved to
    # follow-up, or resolved -- this is the idempotency guarantee holding at
    # 30k scale, not just the ~300-account demo scale.
    assert summary_2["new_count"] == 0
    assert summary_2["follow_up_count"] == 0
    assert summary_2["resolved_count"] == 0
    assert summary_2["unchanged_count"] == accounts_processed

    print("\n" + "=" * 60)
    print("SCALE TEST: 300 -> 30,000 ACCOUNTS")
    print("=" * 60)
    print(f"\nSynthetic workbook generated: {gen_elapsed:8.3f}s")
    print(f"Total accounts processed    : {accounts_processed}")

    _print_run("Run 1 (fresh/empty state DB):", timings_1, run1_total, summary_1)
    _print_run("Run 2 (same file, unchanged data -- idempotency check):", timings_2, run2_total, summary_2)

    slow_stages = [
        (f"run1:{stage}", elapsed)
        for stage, elapsed in timings_1.items()
        if elapsed > SLOW_THRESHOLD_SECONDS
    ] + [
        (f"run2:{stage}", elapsed)
        for stage, elapsed in timings_2.items()
        if elapsed > SLOW_THRESHOLD_SECONDS
    ]

    print("\nVerdict:")
    if run1_total > SLOW_THRESHOLD_SECONDS or run2_total > SLOW_THRESHOLD_SECONDS or slow_stages:
        print(f"  FLAGGED: one or more stages/totals exceeded the {SLOW_THRESHOLD_SECONDS}s threshold.")
        for name, elapsed in slow_stages:
            print(f"    - {name}: {elapsed:.3f}s")
    else:
        print(f"  OK: every stage and both full runs completed under {SLOW_THRESHOLD_SECONDS}s at {N_ACCOUNTS:,} accounts.")
    print("=" * 60)
