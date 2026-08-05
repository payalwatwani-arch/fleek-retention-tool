# Decisions log

A running record of judgment calls made while building this, and why — not just
what the code does, but the thinking behind it.

---

## Segmentation thresholds — grounded in the real data, not guessed

Before writing any segmentation logic, I checked the actual distribution of every
key column across the 300 real accounts, rather than picking round numbers.

**Broker-reliant threshold (30%)**: checked the real spread of
`broker_reliance_pct` among Account Managed accounts and found a genuine gap — zero
accounts sit between 20% and 40%. That's not noise, it's a real two-cluster split
in the population. The 30% threshold sits inside that gap on purpose, and I
confirmed every account above it also has under 10 active app days — the two
signals describe the same underlying cluster, not two separate conditions.

**Dropped the "Hybrid/Transition" segment.** My original plan sketched a
three-way split for Account Managed accounts (broker-reliant / hybrid / healthy).
Once I actually looked at the distribution, the data only supports two clusters,
cleanly separated by the gap above. Rather than force a middle category the data
doesn't back up, I dropped it.

**Bundle nudge threshold**: originally guessed `bundle_gmv_share_pct < 15%`.
Checked the real self-serve distribution and found it's heavily skewed —
90% of self-serve accounts sit at exactly 100% bundle share. Only 17 of 90 aren't
already fully bundle-based. Adjusted the rule to `< 100` instead of a made-up
percentage, since that's what the real tail actually looks like.

## At-risk definition — rebuilt after checking the real trend distribution

Original plan: flag anything with `gmv_trend_pct < -25%` as at-risk.

Checked the real numbers first. `gmv_trend_pct` is only computable for half the
book (150 of 300 accounts) — blank the rest of the time for two explainable
reasons: under 6 months' tenure (no September baseline yet), or September spend
was already zero (can't compute a % change from zero). Neither of those means
"at risk" — they mean "no trend to measure."

Of the 150 accounts that do have a computable trend, 77% are exactly -100% —
already at zero by February. A flat -25% cutoff would lump "declining a bit" in
with "already completely gone," which loses a meaningful distinction: those two
situations call for different actions. Split into three states instead:
declining-but-salvageable (retention check-in), already-gone (win-back play, a
different message entirely), and no-baseline (excluded — not enough history to
judge yet).

## Blank-handling in `data_loader.py`

Didn't default all blanks to the same fill value. Checked what each blank
actually meant before deciding:
- `account_status` blanks mean "genuinely not recorded" per the source data's own
  Readme tab — kept as an explicit "Unknown" category rather than assuming
  "Active," since that would be inventing a fact not in the data.
- `gmv_trend_pct` blanks are never filled with 0, since 0 would falsely read as
  "flat" when the real situation is "no trend exists yet." Added a
  `has_trend_baseline` flag instead, so downstream logic can check for this
  explicitly rather than silently treating a missing trend as a real one.

## Data quality — the flagged duplicate

One row (`account_id ACC-005`) had `account_status == "Duplicate"` — a data
hygiene issue already flagged in Fleek's own source file. Excluded it from the
pipeline's working output, but logged it separately in the load report rather
than silently dropping it, so nothing about the exclusion is hidden.

## Referral, feedback, and expansion nudges — considered, not built

Fleek runs a live referral program (confirmed via public forum activity —
referral codes actively shared and used by resellers). I considered adding
referral, feedback, and account-expansion nudges alongside the brief's four
named growth levers (chat, bundles, video calls, build-a-bundle).

Decided against it: the dataset has no columns backing any of these (no
referral code usage, no feedback log, no expansion-readiness signal), so
building segmentation logic around them would mean inventing eligibility
criteria rather than deriving it from real data — breaking the same
discipline used everywhere else in this build. The brief also names a closed
set of four levers explicitly, not an open invitation to add more.

If referral/feedback data existed in a future version of this dataset, these
would be natural additional levers — just not ones I'd fabricate criteria for
now.

## Email / Text / Call format tabs — static previews only, no real send integration

The Account Overview's drafted outreach now offers 3 format tabs per tone
variant: "Email" (the subject + editable message, unchanged), "Text" (the
same message reformatted shorter and casual, sign-off dropped, with a
"Copy for Text" button), and "Call" (a 4-part talking-points script — opening
line, key point, pushback reassurance, and close — for the AM to use live on
the phone, one template per action type, personalized with the account's
real numbers the same way the email templates are).

Deliberately not built: an actual send integration for Text (e.g. a phone
number field, an SMS API) or a real dialer for Call. The dataset has no
phone number or contact field for any account — building a real send button
would mean either fabricating contact info for real, anonymised customers,
or leaving it broken. Since this is real Fleek customer data, not fictional
demo data, I didn't want to invent contact details that don't exist. The
tabs still demonstrate the channel-awareness the brief's context implies,
without overstepping into data the file doesn't contain.

(This replaces an earlier WhatsApp-only preview, which used the same
"reformat + `st.code()` copy button" pattern now generalized into the Text
tab.)

## Scale test — 30,000 accounts, proven not assumed

The brief asks whether this would cope at 30,000 accounts. Rather than assert
it, I tested it: generated a synthetic 30,034-account workbook and ran the
full pipeline twice.

Results: full run completes in ~14 seconds. Re-running on unchanged data
correctly shows zero wasted work (new=0, reset_to_pending=0,
unchanged=30,034) — idempotency holds at 100x the original scale, not just on
the 344-account real file.

Segmentation is effectively free (~0.02s) since it's vectorized pandas.
Drafting and state sync are the real cost drivers, since both currently loop
row-by-row in Python — the next optimization, if the book grew further, would
be vectorizing those the same way segmentation already is.

## Build-a-Bundle nudge — why the count is 2, not 8

8 of 90 self-serve accounts hand-pick more than they buy bundles
(handpick_orders > bundle_orders). But Build-a-Bundle nudge only applies
within Growth Headroom accounts (high browsing, low spend) — verified only
2 of the 23 headroom accounts also hand-pick more than bundle. The other 6
hand-pick-heavy accounts aren't short on spend, so they correctly get no
growth nudge at all. The two signals (hand-pick preference, growth
headroom) are related but distinct — not every hand-pick-heavy account
needs a nudge.

## Deliberate scope decisions — deferred, not forgotten

A few real, well-reasoned next steps were identified but intentionally
not built tonight, given time constraints. Documenting the thinking here
rather than shipping them under pressure.

**Effectiveness digest.** Right now the tool tracks whether an account
was actioned, but nothing measures whether the action actually worked.
The natural next step: snapshot an account's segment at the exact moment
mark_actioned() is called (actioned_segment), then later compare it
against the account's current segment. A real stat like "of the 12
accounts actioned in the last 14 days, 3 have since moved to a healthier
segment" would close the loop from "did we act" to "did it work" — using
data the tool already has, no new inputs required.

**A more intelligent Overview page.** The current Overview shows a real
narrative briefing and a since-last-run digest. A fuller version, sketched
but not built: three structured sections mirroring how real CRM
dashboards are organized (a numbers view, an activity view, an analytics
view) — Visibility (portfolio distribution, core metrics), Engagement
(a smart "needs attention first" ranking combining health score and
touch count, a recently-actioned log), and Intelligence (the aggregate
factor analysis, a "wins" callout for accounts that improved since last
run).

**A fuller outreach cadence.** Cadence currently uses 3 touch stages,
triggered by real data changes on pipeline re-run — no calendar timing.
A more mature version: 5-6 touches over 2-6 weeks with real day-gaps
between them (e.g. 2 days, then 3, then 3-4, then 4, then a "breakup"
message on day 5) — closer to a standard B2B outreach sequence, with a
distinct final message type for a graceful close-out rather than fading
silently.

**A time-based follow-up trigger.** Currently, an account only moves to
Follow-up if its real data changes. An account that goes fully silent
(no data change at all) stays at Touch 1 indefinitely. A reasonable fix:
if an account sits at "Actioned" for 5+ days with zero data change,
treat that silence itself as a signal and move it to Follow-up anyway.
5 days is a business judgment, not verified against real response-time
data (which doesn't exist in this dataset) — a genuinely different kind
of threshold than the data-grounded ones elsewhere in this build, and
worth being explicit about that distinction.

**Live LLM-generated messages, considered and declined.** Every drafted
message in this tool is pre-written and deterministic, not generated at
runtime. A live LLM call could produce more dynamic phrasing, but at the
cost of the testability and reliability this build has prioritized
throughout — a generated message can't be unit-tested the way a template
can, and introduces a real risk of saying something not grounded in the
actual account data. Declined deliberately, not from lack of consideration.
