"""Compute a 0-100 account health score from real usage/spend signals.

Only pure logic lives here: `compute_health_score(row)` takes one account
row (a dict or pandas Series carrying the columns produced by
`data_loader.load_and_clean`) and returns `(score, factors)`. No UI, no
DataFrame plumbing — app.py wires this in separately.

--- Weighting, in plain English ---

The score is a weighted average of up to three 0-100 "component" scores,
each already oriented so that higher = healthier:

  1. Ownership-specific signal — 40% of the score.
     - Account Managed rows: broker_reliance_pct, inverted (lower reliance
       on a human broker to place orders is healthier).
     - Self Serve rows: an engagement score averaging pdp_views_6m and
       make_an_offer_6m (more product usage is healthier).
  2. App activity (app_active_days_6m) — 30% of the score. Applies to both
     ownership types.
  3. GMV trend (gmv_trend_pct) — 30% of the score, but ONLY when
     has_trend_baseline is True. Some accounts are too new or had zero
     September GMV, so there's nothing to trend against yet.

     When there's no trend baseline, that 30% doesn't just vanish or get
     treated as neutral — it's redistributed proportionally across the
     other two components, so they still sum to 100% and keep their 4:3
     ratio (40:30 -> ~57%:~43%).

So: "40% ownership signal, 30% app activity, 30% trend when available."
"""

from __future__ import annotations

import pandas as pd

from .segmentation import ACCOUNT_MANAGED, OWNERSHIP_COLUMN, SELF_SERVE

# Component weights when a trend baseline is available.
WEIGHT_OWNERSHIP = 0.40
WEIGHT_ACTIVITY = 0.30
WEIGHT_TREND = 0.30

# Raw-value caps: the point at which a metric is scored as "fully healthy"
# (100). Chosen above the middle of the ranges seen in the demo data (see
# data_loader._build_demo_workbook), not the observed max, so a merely
# decent account doesn't automatically max out that component.
APP_ACTIVE_DAYS_CAP = 120.0   # out of ~180 days in a 6-month window
PDP_VIEWS_CAP = 1500.0        # observed range is 0-3000
MAKE_AN_OFFER_CAP = 8.0       # observed range is 0-14

# gmv_trend_pct is scored on a fixed -100..+50 scale: -100% ("Already Gone")
# is the worst possible trend, +50% is treated as strong-growth ceiling.
GMV_TREND_FLOOR = -100.0
GMV_TREND_CEIL = 50.0


def _num(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass
    return float(value)


def _capped_pct(raw: float, cap: float) -> float:
    """Scale raw onto 0-100, clamped, treating `cap` as the 100 point."""
    if cap <= 0:
        return 0.0
    return min(100.0, max(0.0, (raw / cap) * 100.0))


def compute_health_score(row) -> tuple[int, list[dict]]:
    """Score one account row 0-100. Returns (score, factors), where factors
    lists the 2-3 biggest contributors, most-impactful first, as
    {"label", "direction" ("up"/"down"), "impact" ("positive"/"negative")}.
    """
    ownership = row.get(OWNERSHIP_COLUMN)
    is_am = ownership == ACCOUNT_MANAGED

    # Each component: name, weight-key, 0-100 healthy-scale score, display
    # label, and polarity (+1 if a high raw value is healthy, -1 if a high
    # raw value is unhealthy). Direction/impact are both derived from the
    # score + polarity below, so they always agree with each other.
    components = []

    if is_am:
        broker_pct = min(max(_num(row.get("broker_reliance_pct")), 0.0), 100.0)
        components.append({
            "key": "ownership",
            "score": 100.0 - broker_pct,
            "polarity": -1,
            "label": f"Broker reliance: {broker_pct:.0f}%",
        })
    else:
        pdp_views = _num(row.get("pdp_views_6m"))
        offers = _num(row.get("make_an_offer_6m"))
        engagement_score = (
            _capped_pct(pdp_views, PDP_VIEWS_CAP) + _capped_pct(offers, MAKE_AN_OFFER_CAP)
        ) / 2.0
        components.append({
            "key": "ownership",
            "score": engagement_score,
            "polarity": 1,
            "label": f"Engagement: {pdp_views:.0f} PDP views, {offers:.0f} offers (6m)",
        })

    active_days = _num(row.get("app_active_days_6m"))
    components.append({
        "key": "activity",
        "score": _capped_pct(active_days, APP_ACTIVE_DAYS_CAP),
        "polarity": 1,
        "label": f"App activity: {active_days:.0f} days",
    })

    has_baseline = bool(row.get("has_trend_baseline"))
    trend_raw = row.get("gmv_trend_pct")
    if has_baseline and trend_raw is not None and not pd.isna(trend_raw):
        trend = float(trend_raw)
        trend_clamped = min(max(trend, GMV_TREND_FLOOR), GMV_TREND_CEIL)
        trend_score = (trend_clamped - GMV_TREND_FLOOR) / (GMV_TREND_CEIL - GMV_TREND_FLOOR) * 100.0
        components.append({
            "key": "trend",
            "score": trend_score,
            "polarity": 1,
            "label": f"GMV trend: {trend:.0f}%",
        })

    has_trend_component = any(c["key"] == "trend" for c in components)
    if has_trend_component:
        weights = {"ownership": WEIGHT_OWNERSHIP, "activity": WEIGHT_ACTIVITY, "trend": WEIGHT_TREND}
    else:
        # No trend baseline: redistribute its 30% proportionally so the
        # remaining two components still sum to 100%, keeping their 4:3 ratio.
        remaining = WEIGHT_OWNERSHIP + WEIGHT_ACTIVITY
        weights = {
            "ownership": WEIGHT_OWNERSHIP / remaining,
            "activity": WEIGHT_ACTIVITY / remaining,
        }

    total = sum(weights[c["key"]] * c["score"] for c in components)
    score = int(round(min(100.0, max(0.0, total))))

    ranked = sorted(
        components,
        key=lambda c: abs(weights[c["key"]] * (c["score"] - 50.0)),
        reverse=True,
    )
    factors = []
    for c in ranked:
        is_up = (c["score"] >= 50.0) == (c["polarity"] == 1)
        factors.append({
            "label": c["label"],
            "direction": "up" if is_up else "down",
            "impact": "positive" if c["score"] >= 50.0 else "negative",
        })

    return score, factors


# Health score at or below this threshold is flagged as a general account
# health concern in internal_recommendation(), even when is_at_risk is False
# — e.g. an account with a middling score on each individual component that
# still adds up to a weak overall picture. Chosen well below the midpoint
# (50) so this only fires for accounts that are clearly struggling, not
# merely below average.
CRITICAL_HEALTH_SCORE_THRESHOLD = 35


def internal_recommendation(row, score: int) -> str | None:
    """Internal-only guidance for an Account Manager, kept separate from the
    customer-facing draft in src/nba.py so account-health context never
    leaks into what the customer sees. Returns None when there's nothing to
    flag (healthy account, not at risk).

    Wording is deliberately supportive/growth-oriented rather than
    monitoring-focused -- this reads to the AM as "here's an opportunity to
    help," not a compliance flag. Still cites the real number behind the
    flag, since the AM needs that to act on it -- only the framing softened."""
    at_risk_detail = row.get("at_risk_detail")

    if bool(row.get("is_at_risk")):
        if at_risk_detail == "Already Gone":
            trend = row.get("gmv_trend_pct")
            drop = f"{abs(float(trend)):.0f}%" if trend is not None and not pd.isna(trend) else "to zero"
            return (
                f"This account may need extra support — spend has dropped {drop}. Worth a "
                "personal check-in alongside the automated outreach, since this looks like "
                "more than routine reliance."
            )
        if at_risk_detail == "Declining":
            trend = row.get("gmv_trend_pct")
            trend_str = f" ({trend:.0f}%)" if trend is not None and not pd.isna(trend) else ""
            return (
                f"Spend is trending down{trend_str}. A quick personal touch alongside the "
                "automated nudge could help catch this early."
            )
        return (
            "This account is flagged at-risk. Worth a closer look alongside the automated "
            "outreach."
        )

    if score <= CRITICAL_HEALTH_SCORE_THRESHOLD:
        return (
            f"Overall account health is low ({score}/100). Worth a closer look before "
            "relying on automated outreach alone."
        )

    return None
