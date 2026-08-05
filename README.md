# Fleek Retention Engine

A tool that runs Fleek's account-management portfolio automatically: cleans
the raw data, segments every account by real behavior, decides the next
useful action, drafts it, and tracks it — safely, every time it re-runs,
without duplicating work or losing history.

Built for Fleek's GTM Retention case study.

## What it actually does

Every account gets sorted into one of two situations the brief describes:

- **Account-managed customers over-reliant on their AM** → flagged for a
  self-serve nudge, with a message drafted to move them toward ordering
  directly, without losing their spend.
- **Self-serve customers with unused growth potential** → matched to one
  of five real levers (bundles, build-a-bundle, the offer tool, chat,
  video calls), based on which specific signal applies to them.

Declining or already-gone accounts get flagged separately, regardless of
which of the above also applies — so nothing important gets hidden behind
a single label.

Every account also gets a health score (0–100) with a plain-English
breakdown of what's driving it, and a status — New, Actioned, or
Follow-up — that tracks whether it's been handled, and whether it needs a
second touch.

## How to run it

```bash
pip install -r requirements.txt --break-system-packages

# Command line — run the full pipeline once against a workbook:
python run.py path/to/portfolio.xlsx

# Dashboard — the actual working tool:
python -m streamlit run app.py
```

The dashboard will ask you to upload the portfolio workbook (must contain
`Accounts` and `new_accounts` sheets, same shape as the case study file) on
first load.

## Architecture

```
[ Raw Excel ] ──► [ data_loader.py ]  clean, dedupe, merge new_accounts
                          │
                          ▼
                  [ segmentation.py ]  real thresholds, checked against
                          │            the actual data's distribution
                          ▼
                  [ scoring.py ]  0–100 health score + factor breakdown
                          │
          ┌───────────────┴────────────────┐
          ▼                                 ▼
 [ Self-serve / growth nudges ]   [ Declining / already-gone flag ]
 (draft ready — AM reviews          (surfaced regardless of primary
  and sends)                         segment, nothing hidden)
          │                                 │
          └───────────────┬────────────────┘
                           ▼
                    [ nba.py ]  drafts the message — 3 tones,
                          │      3 formats (Email / Text / Call script)
                          ▼
                  [ state.py ]  tracks New → Actioned → Follow-up,
                          │      idempotent — re-running never duplicates
                          ▼
                  [ app.py ]  the dashboard — Pipeline board, Account
                               Overview, Notes, filters, multi-select
```

Everything left of the dashboard runs unattended. The dashboard is where a
human reviews a draft and decides to send it — nothing in this tool sends
anything automatically, on purpose (see Limitations).

## Proof, not claims

- **Idempotency, on real data**: running the pipeline twice on the same
  344-account file shows `0 new, 0 reset, 344 unchanged` on the second
  run — nothing gets reprocessed or duplicated.
- **Idempotency, at scale**: the same test at 30,034 synthetic accounts
  completes in ~14 seconds, with the second run showing the identical
  zero-waste result — proving the "300 to 30,000" requirement with a real
  number, not an assumption.
- **64 automated tests**, covering segmentation logic, state transitions,
  scoring, the dashboard's rendering, and the scale test itself.

## Key decisions

Every threshold in this tool was checked against the real data before
being set — not guessed. The full reasoning, including a few places the
original plan turned out to be wrong and was rebuilt, is documented in
[`DECISIONS.md`](./DECISIONS.md).

## How I'd actually use this in week one

The tool tells you *what* needs attention. Here's how I'd actually
sequence it, given a real portfolio like this one.

**Start with the self-serve nudges, but not all 92 at once.** I'd
prioritize by health score first — the ones still scoring reasonably well
despite high broker reliance are the best odds of a clean migration; a
low-scoring, heavily-reliant account is more likely to churn if pushed
too hard, too fast. Those get a softer, slower approach — maybe the
"expansion" framing before the "migrate" ask.

**Treat gone-cold accounts as a completely separate campaign, not a
routine nudge.** Sending a "try self-serve" message to an account that's
already at zero spend misreads the situation entirely — they need a
win-back, with a different tone and likely an incentive, not a feature
pitch. The tool already drafts these differently for exactly this reason.

**The 23 growth-headroom accounts are the highest-leverage quick wins,
and I'd work these first, not last.** They're already spending, already
engaged — they just haven't tried the one feature that fits their
behavior. This is where the "Build-a-Bundle" split from the generic
bundle nudge actually matters: an account that's already hand-picking
more than they bundle needs a very different pitch than one that's never
tried bundles at all. Getting that distinction right is a five-minute
win, not a relationship to rebuild.

**Anything flagged "also declining" underneath a different primary
action gets a second look before I send anything.** A broker-reliant
account that's also losing spend isn't just "migrate them" — it might be
"find out why they're disappearing before asking them to change how they
buy." The tool surfaces this as a flag specifically so it isn't missed
inside a routine batch of self-serve nudges.

**By week four**, I'd expect the Follow-up column to be the busiest one —
the accounts from week one that didn't respond to a first touch. That's
where the tone shifts from "here's an opportunity" to "checking in
directly," and where I'd start looking at whether a call, not another
message, is the better next move.

## Running it on a schedule

`scheduled_run.py` is the unattended entry point: it runs the pipeline
against the newest `.xlsx` file in `data/incoming/` (gitignored — drop the
day's workbook there), reconciles it against the same state database the
dashboard uses (`data/state/portfolio.db`), and writes a morning briefing
to `data/briefings/briefing_{today's date}.md` (also gitignored). If
`data/incoming/` is empty, it prints a message and exits cleanly — nothing
to do until a new workbook shows up.

To run it every morning at 8am via cron:

```bash
crontab -e
```

Add this line (adjust the path to wherever the repo lives):

```
0 8 * * * cd /path/to/fleek-retention-tool && /usr/bin/python3 scheduled_run.py >> data/briefings/cron.log 2>&1
```

The next time the dashboard is opened, the Overview view picks up that
day's briefing file automatically if one exists, and falls back to
generating the same briefing live otherwise.

## Known limitations

- **No live send integration.** Email, Text, and Call tabs are all
  drafted and formatted, but nothing sends automatically — there's no
  phone number or verified contact channel anywhere in the real dataset,
  so building a send button would mean fabricating contact data for real,
  anonymised customers. A human sends; the tool drafts.
- **Schema is specific to this dataset**, not a generic plug-and-play
  tool. Column names and thresholds are grounded in this case study's
  real data — a genuinely different dataset would need its own threshold
  pass, the same way this one got one.
- **No literal "Resolved" stage.** Resolution happens automatically when
  an account's real data changes enough that segmentation no longer
  assigns it an action — a stronger signal than a manual button, since
  it's backed by evidence rather than a claim.

## How AI was used

Claude Code was used as an operational accelerator — writing the
boilerplate Pandas/Streamlit/SQLite code quickly once the underlying logic
was already decided. Most of the actual time went into the segmentation
thresholds, the next-best-action priority logic, and the edge cases (the
at-risk-account visibility gap, the checkbox-toolbar rendering bug) —
found by checking real numbers against what the code produced, not by
assuming the first working version was correct.

I used Claude Code's Plan Mode for the larger structural changes (the
Kanban board rebuild, the multi-select feature), since it's better suited
to reasoning through a change before writing it. That did mean running
into usage limits partway through the build — a real, practical
constraint worth naming honestly rather than glossing over, since it's
part of actually working with these tools day to day, not just a clean
demo of them.
