"""Segment cleaned accounts into retention-relevant buckets and assign a
next-best action, per the thresholds in DECISIONS.md.

Run directly against the demo/data pipeline to see a summary report (from
the project root, so the package-relative imports resolve):

    python -m src.segmentation [path/to/workbook.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from .data_loader import DEFAULT_DEMO_PATH, load_and_clean, _build_demo_workbook

OWNERSHIP_COLUMN = "ownership"
ACCOUNT_MANAGED = "Account Managed"
SELF_SERVE = "Self Serve"

BROKER_RELIANCE_THRESHOLD = 30
HEADROOM_PDP_VIEWS_THRESHOLD = 80.5
HEADROOM_GMV_THRESHOLD = 266
BUNDLE_SHARE_THRESHOLD = 100
DECLINE_THRESHOLD = -25
ALREADY_GONE_TREND = -100


def segment_accounts(df: pd.DataFrame) -> pd.DataFrame:
    """Add `segment` and `action` columns to a cleaned accounts DataFrame."""
    df = df.copy()
    df["segment"] = pd.Series(pd.NA, index=df.index, dtype="object")
    df["action"] = pd.Series(pd.NA, index=df.index, dtype="object")

    is_am = df[OWNERSHIP_COLUMN] == ACCOUNT_MANAGED
    is_ss = df[OWNERSHIP_COLUMN] == SELF_SERVE

    # --- Account Managed: two-cluster split, no "Hybrid" middle segment ---
    broker_reliant = is_am & (df["broker_reliance_pct"] > BROKER_RELIANCE_THRESHOLD)
    df.loc[broker_reliant, "segment"] = "Broker-Reliant"
    df.loc[broker_reliant, "action"] = "Self-Serve Nudge"

    healthy_am = is_am & ~broker_reliant
    df.loc[healthy_am, "segment"] = "Healthy AM"
    df.loc[healthy_am, "action"] = "None"

    # --- Self Serve: headroom check, then a priority-ordered action pick ---
    headroom = is_ss & (df["pdp_views_6m"] > HEADROOM_PDP_VIEWS_THRESHOLD) & (
        df["gmv_total_6m"] < HEADROOM_GMV_THRESHOLD
    )
    df.loc[headroom, "segment"] = "Growth Headroom"

    no_headroom = is_ss & ~headroom
    df.loc[no_headroom, "segment"] = "Self-Serve, No Headroom"
    df.loc[no_headroom, "action"] = "None"

    self_serve_median_orders = df.loc[is_ss, "orders_6m"].median()

    bundle_nudge = headroom & (df["bundle_gmv_share_pct"] < BUNDLE_SHARE_THRESHOLD)
    df.loc[bundle_nudge, "action"] = "Bundle nudge"

    offer_nudge = headroom & ~bundle_nudge & (df["make_an_offer_6m"] == 0)
    df.loc[offer_nudge, "action"] = "Offer tool nudge"

    chat_nudge = (
        headroom
        & ~bundle_nudge
        & ~offer_nudge
        & (df["chat_threads"] == 0)
        & (df["video_call_requests"] == 0)
        & (df["orders_6m"] > self_serve_median_orders)
    )
    df.loc[chat_nudge, "action"] = "Chat/call nudge"

    headroom_no_lever = headroom & ~bundle_nudge & ~offer_nudge & ~chat_nudge
    df.loc[headroom_no_lever, "action"] = "None"

    # --- At-risk: checked after AM/Self-Serve, only overrides "None" actions ---
    has_baseline = df["has_trend_baseline"] == True  # noqa: E712
    overridable = df["action"] == "None"

    already_gone = has_baseline & overridable & (df["gmv_trend_pct"] == ALREADY_GONE_TREND)
    df.loc[already_gone, "segment"] = "Already Gone"
    df.loc[already_gone, "action"] = "Win-back play"

    declining = (
        has_baseline
        & overridable
        & ~already_gone
        & (df["gmv_trend_pct"] < DECLINE_THRESHOLD)
    )
    df.loc[declining, "segment"] = "Declining"
    df.loc[declining, "action"] = "Retention check-in"

    # --- is_at_risk / at_risk_detail: independent of segment/action above,
    # so a Broker-Reliant or Growth Headroom account can still surface as
    # at-risk instead of that signal being silently overridden. ---
    already_gone_signal = has_baseline & (df["gmv_trend_pct"] == ALREADY_GONE_TREND)
    declining_signal = has_baseline & ~already_gone_signal & (df["gmv_trend_pct"] < DECLINE_THRESHOLD)

    df["is_at_risk"] = already_gone_signal | declining_signal

    df["at_risk_detail"] = pd.Series(pd.NA, index=df.index, dtype="object")
    df.loc[already_gone_signal, "at_risk_detail"] = "Already Gone"
    df.loc[declining_signal, "at_risk_detail"] = "Declining"

    return df


def _print_summary(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("SEGMENTATION SUMMARY")
    print("=" * 60)

    print("\nAccounts per segment:")
    segment_counts = df["segment"].value_counts(dropna=False)
    for segment, count in segment_counts.items():
        print(f"  {str(segment):<28} {count}")

    print("\nAccounts per action:")
    action_counts = df["action"].value_counts(dropna=False)
    for action, count in action_counts.items():
        print(f"  {str(action):<28} {count}")

    at_risk = df["is_at_risk"] == True  # noqa: E712
    print(f"\nAt-risk accounts (independent of primary segment): {int(at_risk.sum())}")

    overlap_broker = at_risk & (df["segment"] == "Broker-Reliant")
    overlap_headroom = at_risk & (df["segment"] == "Growth Headroom")
    print("  ...of which also carry another primary signal:")
    print(f"    {'Broker-Reliant + at-risk':<30} {int(overlap_broker.sum())}")
    print(f"    {'Growth Headroom + at-risk':<30} {int(overlap_headroom.sum())}")

    print(f"\nTotal accounts processed: {len(df)}")
    print("=" * 60)


if __name__ == "__main__":
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEMO_PATH

    if not filepath.exists():
        print(f"No workbook found at {filepath}, generating a demo workbook there...")
        _build_demo_workbook(filepath)

    clean_df, _report = load_and_clean(filepath)
    segmented_df = segment_accounts(clean_df)
    _print_summary(segmented_df)
