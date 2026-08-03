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

## WhatsApp — static preview only, no real send integration

Fleek's customer base (small resellers, vintage shops) likely relies heavily
on WhatsApp for informal buyer communication, so the Action Center shows a
styled WhatsApp-format preview of each drafted message alongside the email
version, with a "Copy for WhatsApp" button.

Deliberately not built: an actual send integration (e.g. a wa.me link or the
WhatsApp Business API). The dataset has no phone number or contact field for
any account — building a real send button would mean either fabricating
contact info for real, anonymised customers, or leaving it broken. Since this
is real Fleek customer data, not fictional demo data, I didn't want to
invent contact details that don't exist. The preview still demonstrates the
channel-awareness the brief's context implies, without overstepping into
data the file doesn't contain.
