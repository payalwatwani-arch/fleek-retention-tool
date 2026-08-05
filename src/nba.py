"""Draft next-best-action outreach copy for segmented accounts.

Takes the DataFrame produced by `segmentation.segment_accounts()` (which
carries `segment`, `action`, `is_at_risk`, `at_risk_detail`, plus all the
original account columns) and adds a `draft_variants` column — a list of
3 pre-written tone variants (Direct, Warm, Formal) per action type, each
a `{"tone", "subject", "message"}` dict, so an AM can pick whichever fits
the account best.

Run directly against the demo/data pipeline to see example drafts (from
the project root, so the package-relative imports resolve):

    python -m src.nba [path/to/workbook.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from .data_loader import DEFAULT_DEMO_PATH, load_and_clean, _build_demo_workbook
from .segmentation import segment_accounts

# Actions whose template is itself the at-risk message — appending the
# generic "also showing declining spend" sentence to these would be
# redundant, since is_at_risk is always True for rows carrying them.
AT_RISK_APPEND_EXEMPT = {"Win-back play", "Retention check-in", "None"}


def _account_label(row) -> str:
    name = row.get("name")
    if name is not None and not pd.isna(name):
        return str(name)
    return str(row["account_id"])


def _pct(value, decimals: int = 0) -> str:
    if value is None or pd.isna(value):
        return "an unclear amount"
    return f"{value:.{decimals}f}%"


TONES = ["Direct", "Warm", "Formal"]


def _draft_migration_play(row) -> list[dict[str, str]]:
    label = _account_label(row)
    broker_pct = _pct(row.get("broker_reliance_pct"))
    return [
        {
            "tone": "Direct",
            "subject": f"Skip the wait: order directly for {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"{broker_pct} of your recent orders are still routed through your account manager. Ordering "
                f"directly in the product is faster for routine orders — no waiting on a reply. Your AM is still "
                f"there for anything that needs a human.\n\n"
                f"Want a two-minute walkthrough of self-serve ordering?\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Warm",
            "subject": f"A faster way to order for {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"We noticed about {broker_pct} of your recent orders are still going through your account manager. "
                f"We'd love for you to try ordering directly in the product instead — it's built to be quicker for "
                f"routine orders, so you're not waiting on a reply to move. Your AM isn't going anywhere; they're "
                f"still there for anything that needs a human. Want a two-minute walkthrough of self-serve "
                f"ordering?\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Formal",
            "subject": f"Streamlining the ordering process for {label}",
            "message": (
                f"Dear {label} team,\n\n"
                f"Our records indicate that approximately {broker_pct} of your recent orders were placed through "
                f"your account manager rather than directly within the platform. We would recommend transitioning "
                f"routine orders to our self-serve ordering tool, which is designed to reduce turnaround time. "
                f"Please note that your account manager remains available for any matters requiring personal "
                f"assistance.\n\n"
                f"Would you be available for a brief walkthrough of the self-serve ordering process?\n\n"
                f"Regards,\nThe Fleek Team"
            ),
        },
    ]


def _draft_migration_play_touch2(row) -> list[dict[str, str]]:
    """Second-touch variant for Self-Serve Nudge: touch_count == 1, i.e. one
    prior outreach already happened. Acknowledges the earlier contact rather
    than repeating the first-touch pitch verbatim."""
    label = _account_label(row)
    broker_pct = _pct(row.get("broker_reliance_pct"))
    return [
        {
            "tone": "Direct",
            "subject": f"Following up: order directly for {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"Following up on my note last week about self-serve ordering. {broker_pct} of your recent orders "
                f"are still routed through your account manager, and I'd still love to get you set up with direct "
                f"ordering — it's faster for routine orders, no waiting on a reply.\n\n"
                f"Do you have five minutes this week for a quick walkthrough?\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Warm",
            "subject": f"Circling back on self-serve ordering for {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"Just circling back on my note from last week — no worries if it got buried! About {broker_pct} "
                f"of your recent orders are still going through your account manager, and we'd still love to get "
                f"you set up with direct ordering whenever it's convenient. It's built to be quicker for routine "
                f"orders, so you're not waiting on a reply to move.\n\n"
                f"Any chance you have a few minutes this week for a quick walkthrough?\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Formal",
            "subject": f"Following up: streamlining the ordering process for {label}",
            "message": (
                f"Dear {label} team,\n\n"
                f"I am following up on my previous note regarding self-serve ordering. Our records continue to "
                f"show that approximately {broker_pct} of your recent orders were placed through your account "
                f"manager rather than directly within the platform. We would welcome the opportunity to walk "
                f"you through the self-serve ordering tool at your earliest convenience.\n\n"
                f"Please let me know a time this week that would suit you.\n\n"
                f"Regards,\nThe Fleek Team"
            ),
        },
    ]


def _draft_migration_play_touch3(row) -> list[dict[str, str]]:
    """Third-attempt variant for Self-Serve Nudge: touch_count >= 2, i.e. two
    or more prior outreaches. More direct, and pairs the ask with a concrete
    incentive to finally try self-serve."""
    label = _account_label(row)
    broker_pct = _pct(row.get("broker_reliance_pct"))
    return [
        {
            "tone": "Direct",
            "subject": f"One more try: 10% off your first self-serve order, {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"I've reached out twice now about moving some of your ordering to self-serve — {broker_pct} of "
                f"your recent orders are still going through your account manager. To make it worth trying, I can "
                f"apply 10% off your first order placed directly in the product.\n\n"
                f"Just reply and I'll get the discount set up on your account.\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Warm",
            "subject": f"Let's make it easy, {label}: 10% off your first self-serve order",
            "message": (
                f"Hi {label} team,\n\n"
                f"I know I've mentioned this a couple of times now, so let's make it easy: {broker_pct} of your "
                f"recent orders are still routed through your account manager, and I'd love for you to give "
                f"self-serve ordering a real try. As a thank-you for trying it out, I'll apply 10% off your first "
                f"order placed directly in the product.\n\n"
                f"Just reply and I'll get that set up for you.\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Formal",
            "subject": f"A final note on self-serve ordering for {label}, with an incentive",
            "message": (
                f"Dear {label} team,\n\n"
                f"Having reached out on two prior occasions regarding self-serve ordering, I wanted to make one "
                f"final, more concrete offer. Approximately {broker_pct} of your recent orders continue to be "
                f"placed through your account manager. To encourage a first attempt, we are pleased to offer a "
                f"10% discount on your first order placed directly within the platform.\n\n"
                f"Please reply at your convenience and we will arrange the discount.\n\n"
                f"Regards,\nThe Fleek Team"
            ),
        },
    ]


def draft_self_serve_nudge_stage(row, touch_count) -> tuple[int, list[dict[str, str]]]:
    """Select the Self-Serve Nudge message variants matching an account's
    real `touch_count` from the state database. Returns `(touch_stage,
    variants)` where `touch_stage` is 1/2/3 (for a "Touch N" display label)
    and `variants` is the usual list of 3 tone dicts, with the at-risk
    sentence appended when applicable — mirroring `draft_actions()`'s
    append behavior, since Self-Serve Nudge isn't in AT_RISK_APPEND_EXEMPT.

    touch_count is None or NaN before a state row exists yet, which is
    equivalent to touch_count == 0 (first outreach, never actioned)."""
    if touch_count is None or pd.isna(touch_count) or touch_count <= 0:
        touch_stage, variants = 1, _draft_migration_play(row)
    elif touch_count == 1:
        touch_stage, variants = 2, _draft_migration_play_touch2(row)
    else:
        touch_stage, variants = 3, _draft_migration_play_touch3(row)

    variants = [dict(variant) for variant in variants]
    if bool(row.get("is_at_risk")):
        for variant in variants:
            variant["message"] = f"{variant['message']}\n\n{AT_RISK_APPEND_SENTENCE}"

    return touch_stage, variants


def _draft_win_back_play(row) -> list[dict[str, str]]:
    label = _account_label(row)
    return [
        {
            "tone": "Direct",
            "subject": f"Come back to Fleek, {label}?",
            "message": (
                f"Hi {label} team,\n\n"
                f"Your spend with us has dropped to zero over the past few months. If something changed or we "
                f"made ordering harder than it should be, let us know and we'll fix it. Happy to have you back "
                f"whenever you're ready.\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Warm",
            "subject": f"We'd love to have {label} back",
            "message": (
                f"Hi {label} team,\n\n"
                f"We noticed your spend has dropped all the way to zero over the past few months, and honestly, "
                f"we miss having you order with us. If something changed on your end, or something we did made "
                f"ordering harder than it should be, we'd genuinely like to hear it and make it right. No pressure "
                f"— just know the door's wide open whenever you're ready to come back.\n\n"
                f"Warmly,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Formal",
            "subject": f"Re-engaging your account: {label}",
            "message": (
                f"Dear {label} team,\n\n"
                f"We have observed that your account's spend has declined to zero over the past several months. "
                f"Should there have been a change in circumstances, or a service shortfall on our part, we would "
                f"welcome the opportunity to understand it and address it directly. We remain available to resume "
                f"your account whenever it is convenient for you.\n\n"
                f"Regards,\nThe Fleek Team"
            ),
        },
    ]


def _draft_retention_checkin(row) -> list[dict[str, str]]:
    label = _account_label(row)
    trend = row.get("gmv_trend_pct")
    drop_pct = _pct(abs(trend)) if trend is not None and not pd.isna(trend) else "a noticeable amount"
    return [
        {
            "tone": "Direct",
            "subject": f"Order volume down for {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"Your order volume is down about {drop_pct} over the past few months. Wanted to flag it early. "
                f"Anything changed on your end — pricing, product fit, a point of contact? Happy to jump on a "
                f"quick call.\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Warm",
            "subject": f"Checking in with {label}",
            "message": (
                f"Hi {label} team,\n\n"
                f"We noticed your order volume is down about {drop_pct} over the past few months and wanted to "
                f"check in early, before it goes further. Has anything changed on your end — pricing, product "
                f"fit, a point of contact — that we should know about? Happy to jump on a quick call if that's "
                f"easier than writing it out.\n\n"
                f"Best,\nThe Fleek Team"
            ),
        },
        {
            "tone": "Formal",
            "subject": f"A check-in regarding your account activity, {label}",
            "message": (
                f"Dear {label} team,\n\n"
                f"We have observed a decline of approximately {drop_pct} in your order volume over the past few "
                f"months and wanted to reach out proactively. Please let us know if there has been a change on "
                f"your end — whether related to pricing, product fit, or your point of contact — that we should "
                f"be aware of. We would be glad to arrange a call at your convenience.\n\n"
                f"Regards,\nThe Fleek Team"
            ),
        },
    ]


def _draft_bundle_nudge(row) -> list[dict[str, str]]:
    label = _account_label(row)
    bundle_pct = _pct(row.get("bundle_gmv_share_pct"))
    return [
        {
            "tone": "Direct",
            "subject": "Try bundles to save time",
            "message": (
                f"Hi {label} team, only {bundle_pct} of your recent GMV has gone through bundles. Bundling your "
                f"frequently-ordered items saves time on your next order — worth trying."
            ),
        },
        {
            "tone": "Warm",
            "subject": "A quicker way to order: bundles",
            "message": (
                f"Hi {label} team, only {bundle_pct} of your recent GMV has gone through bundles so far. Bundling "
                f"your frequently-ordered items together is a quick way to save time on your next order — want to "
                f"give it a try?"
            ),
        },
        {
            "tone": "Formal",
            "subject": "An opportunity to streamline ordering via bundles",
            "message": (
                f"Dear {label} team, our records show that only {bundle_pct} of your recent GMV has been "
                f"processed through bundled orders. We would like to recommend bundling your frequently-ordered "
                f"items, as this is designed to reduce the time required to place your next order. Please let us "
                f"know if you would like assistance getting started."
            ),
        },
    ]


def _draft_offer_tool_nudge(row) -> list[dict[str, str]]:
    label = _account_label(row)
    return [
        {
            "tone": "Direct",
            "subject": "Make an offer instead of paying list price",
            "message": (
                f"Hi {label} team, you can make an offer directly in the product instead of ordering at list "
                f"price. Fast way to negotiate on items you're already browsing — worth a shot."
            ),
        },
        {
            "tone": "Warm",
            "subject": "Try the make-an-offer tool",
            "message": (
                f"Hi {label} team, did you know you can make an offer directly in the product instead of ordering "
                f"at list price? It's a fast way to negotiate on items you're already browsing — worth a shot on "
                f"your next order."
            ),
        },
        {
            "tone": "Formal",
            "subject": "Introducing the make-an-offer tool",
            "message": (
                f"Dear {label} team, we would like to draw your attention to our make-an-offer tool, which allows "
                f"you to negotiate pricing directly within the product rather than ordering at list price. We "
                f"encourage you to consider it on items you are currently browsing."
            ),
        },
    ]


def _draft_build_a_bundle_nudge(row) -> list[dict[str, str]]:
    label = _account_label(row)
    handpick = row.get("handpick_orders")
    bundled = row.get("bundle_orders")
    handpick_str = "an unclear number of" if handpick is None or pd.isna(handpick) else f"{handpick:.0f}"
    bundled_str = "an unclear number of" if bundled is None or pd.isna(bundled) else f"{bundled:.0f}"
    return [
        {
            "tone": "Direct",
            "subject": "Turn your handpicked orders into a reusable bundle",
            "message": (
                f"Hi {label} team, you've handpicked {handpick_str} orders item-by-item in the last 6 months, "
                f"versus {bundled_str} through pre-made bundles. Our build-a-bundle tool lets you save your own "
                f"selections as a bundle you can reorder in one click — worth a try."
            ),
        },
        {
            "tone": "Warm",
            "subject": "A faster way to reorder your usual picks",
            "message": (
                f"Hi {label} team, we noticed you've handpicked {handpick_str} orders item-by-item in the last 6 "
                f"months, compared to {bundled_str} through pre-made bundles. Since you already know what you "
                f"want, our build-a-bundle tool lets you save that exact selection as a bundle and reorder it in "
                f"one click next time — want to give it a try?"
            ),
        },
        {
            "tone": "Formal",
            "subject": "Introducing the build-a-bundle tool",
            "message": (
                f"Dear {label} team, our records show {handpick_str} handpicked, item-by-item orders from your "
                f"account over the past 6 months, compared to {bundled_str} placed through pre-made bundles. We "
                f"would like to introduce our build-a-bundle tool, which allows you to save your own selections "
                f"as a reusable bundle for faster reordering. Please let us know if you would like assistance "
                f"getting started."
            ),
        },
    ]


def _draft_chat_nudge(row) -> list[dict[str, str]]:
    label = _account_label(row)
    return [
        {
            "tone": "Direct",
            "subject": "Got a question? Try chat",
            "message": (
                f"Hi {label} team, you've been ordering steadily but haven't used chat with us yet. It's there "
                f"for quick questions on sizing, availability, or anything else — worth a try on your next "
                f"order."
            ),
        },
        {
            "tone": "Warm",
            "subject": "We're a chat message away",
            "message": (
                f"Hi {label} team, we noticed you've been ordering steadily but haven't tried chatting with us "
                f"yet. If anything ever comes up — sizing, availability, timing — we're just a message away. "
                f"Want to give it a try?"
            ),
        },
        {
            "tone": "Formal",
            "subject": "Chat support now available to you",
            "message": (
                f"Dear {label} team, we have noticed steady ordering activity on your account, though you have "
                f"not yet made use of our chat support. We would encourage you to reach out via chat should any "
                f"questions arise regarding your orders."
            ),
        },
    ]


def _draft_video_call_nudge(row) -> list[dict[str, str]]:
    label = _account_label(row)
    call_count = row.get("video_call_requests", 0)
    if call_count is None or pd.isna(call_count):
        call_count = 0
    call_count = int(call_count)
    return [
        {
            "tone": "Direct",
            "subject": "Try Virtual Handpick — curate live with a supplier",
            "message": (
                f"Hi {label} team, you've been ordering steadily but haven't tried Virtual Handpick yet "
                f"({call_count} so far). It's a live video call directly with the supplier, where you curate and "
                f"handpick your bundle together in real time using moodboards. Want us to set one up with your "
                f"next order?"
            ),
        },
        {
            "tone": "Warm",
            "subject": "Want to handpick your next bundle live?",
            "message": (
                f"Hi {label} team, we noticed you've been ordering steadily but haven't yet tried Virtual "
                f"Handpick ({call_count} so far). It's a video call directly with the supplier, so you can walk "
                f"through moodboards and curate a bundle together in real time — no middleman, just you and the "
                f"people making it. Want to give it a try on your next order?"
            ),
        },
        {
            "tone": "Formal",
            "subject": "Introducing Virtual Handpick for your account",
            "message": (
                f"Dear {label} team, we have noticed steady ordering activity on your account, though you have "
                f"not yet made use of Virtual Handpick ({call_count} to date). Virtual Handpick connects you "
                f"directly with our suppliers via live video call, allowing you to curate and handpick your "
                f"bundle together in real time using moodboards. We would welcome the opportunity to arrange a "
                f"session at your convenience."
            ),
        },
    ]


ACTION_TEMPLATES = {
    "Self-Serve Nudge": _draft_migration_play,
    "Win-back play": _draft_win_back_play,
    "Retention check-in": _draft_retention_checkin,
    "Build-a-Bundle nudge": _draft_build_a_bundle_nudge,
    "Bundle nudge": _draft_bundle_nudge,
    "Offer tool nudge": _draft_offer_tool_nudge,
    "Chat nudge": _draft_chat_nudge,
    "Video call nudge": _draft_video_call_nudge,
}

AT_RISK_APPEND_SENTENCE = (
    "One more thing worth flagging: this account is also showing declining spend, "
    "so it may need attention beyond this message alone."
)


def draft_actions(df: pd.DataFrame) -> pd.DataFrame:
    """Add a `draft_variants` column: a list of 3 tone-variant dicts
    (`{"tone", "subject", "message"}`, in Direct/Warm/Formal order), one
    set per `action` value. "None" actions get `None`. Accounts flagged
    `is_at_risk` under a non-at-risk primary action get one extra sentence
    appended to every variant's message, without changing which template ran."""
    df = df.copy()
    all_variants: list[list[dict[str, str]] | None] = []

    for _, row in df.iterrows():
        action = row["action"]
        template = ACTION_TEMPLATES.get(action)

        if template is None:
            all_variants.append(None)
            continue

        variants = [dict(variant) for variant in template(row)]

        if bool(row.get("is_at_risk")) and action not in AT_RISK_APPEND_EXEMPT:
            for variant in variants:
                variant["message"] = f"{variant['message']}\n\n{AT_RISK_APPEND_SENTENCE}"

        all_variants.append(variants)

    df["draft_variants"] = all_variants
    return df


def _print_summary(df: pd.DataFrame) -> None:
    print("=" * 60)
    print("DRAFT SUMMARY")
    print("=" * 60)

    has_variants = df["draft_variants"].apply(lambda v: v is not None)

    print("\nDrafts generated per action type:")
    action_counts = df["action"].value_counts(dropna=False)
    for action, count in action_counts.items():
        has_draft = has_variants[df["action"] == action].sum()
        print(f"  {str(action):<28} {count} accounts, {has_draft} drafted")

    with_appended_note = (
        (df["action"] != "None")
        & has_variants
        & df["draft_variants"].apply(
            lambda v: v is not None and AT_RISK_APPEND_SENTENCE in v[0]["message"]
        )
    )
    print(f"\nDrafts with the extra at-risk sentence appended: {int(with_appended_note.sum())}")

    print("\n" + "=" * 60)
    print("EXAMPLE DRAFTS")
    print("=" * 60)

    drafted = df[has_variants]
    examples = []
    seen_actions = set()
    # Prefer one example per distinct action, then one that has the
    # appended at-risk sentence, so the samples cover different tones.
    for _, row in drafted.iterrows():
        if row["action"] not in seen_actions:
            examples.append(row)
            seen_actions.add(row["action"])
        if len(examples) >= 3:
            break

    appended_example = df[with_appended_note]
    if not appended_example.empty and not any(
        r["account_id"] == appended_example.iloc[0]["account_id"] for r in examples
    ):
        if len(examples) >= 3:
            examples[-1] = appended_example.iloc[0]
        else:
            examples.append(appended_example.iloc[0])

    for row in examples:
        print(f"\n--- {row['account_id']}  |  segment: {row['segment']}  |  action: {row['action']} ---")
        for variant in row["draft_variants"]:
            print(f"\n[{variant['tone']}]")
            print(f"Subject: {variant['subject']}")
            print(variant["message"])

    print("\n" + "=" * 60)


if __name__ == "__main__":
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEMO_PATH

    if not filepath.exists():
        print(f"No workbook found at {filepath}, generating a demo workbook there...")
        _build_demo_workbook(filepath)

    clean_df, _report = load_and_clean(filepath)
    segmented_df = segment_accounts(clean_df)
    drafted_df = draft_actions(segmented_df)
    _print_summary(drafted_df)
