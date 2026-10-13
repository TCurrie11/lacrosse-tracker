# Lacrosse Tracker

A local, single-user tracker for player development. Coaches fill in a weekly sheet; players send a weekly
CSV of their own training; the app cross-checks the two, charts progression, and flags players whose
reports don't line up with what the coaches see. Everything runs on your machine — Streamlit + SQLite,
no accounts, no server.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/make_demo.py --fresh     # optional: a fake 8-week season with planted cheaters
streamlit run app.py                    # opens http://localhost:8501
```

To start for real, skip the demo (or delete the `data/` folder) and add players on **Roster setup**.

## How it fits together

```
 Player CSVs ──► data/inbox/ ──► validate ──► data/archive/   (original kept, named by hash)
                                    │    └──► data/rejected/  (+ .errors.txt explaining why)
 Weekly coach sheet ───────────────►│
                                    ▼
                             data/tracker.db  ◄── detection (recomputed live for any week)
                                    │
                                    ▼
           Roster · Team charts · Player · Flags   (week view or overview)
```

| Path | What it is |
|---|---|
| `config.yaml` | Skills, coach-only traits, positions and their core skills, detection weights/thresholds |
| `app.py` | Streamlit entry point and page list |
| `views/` | One file per page |
| `tracker/ingest.py` | CSV validation and import |
| `tracker/coach.py` | Weekly coach sheet saving and pre-fill |
| `tracker/detection.py` | Cheat-detection rules and scoring |
| `tracker/charts.py` | Plotly figures |
| `tracker/queries.py` | Read-side queries |
| `tracker/db.py` | Schema |
| `scripts/make_demo.py` | Demo season generator |
| `tests/` | `python -m pytest` |

## Weekly workflow

1. **Players** send one CSV per week (template on the **Player reports** page):

   ```
   submit_code,week_start,skill,hours,effort
   K7Q2,2026-10-05,passing,6.0,7.5
   K7Q2,2026-10-05,shooting,0,
   ```

   - `week_start` is that week's Monday.
   - `hours = 0` means "didn't train it" — effort on that row is ignored.
   - `hours > 0` needs an effort from 1.0 to 10.0, one decimal.
   - A missing skill row is recorded as 0 hours, with a warning.
2. **You** drop the files on **Player reports** (or copy them into `data/inbox/` and click *Import inbox*).
   A file with any error is rejected whole, with the reason.
3. **Each coach** fills in the **Weekly coach sheet**: competency + effort per skill, plus attitude,
   coachability and competitiveness. It pre-fills from that coach's previous week, so you only change
   what moved. Blank = not observed. Multiple coaches are averaged.
4. Check **Roster** and **Flags**.

## Views

The sidebar switches between **Week** (one week's snapshot, with ‹ › arrows) and **Overview** (a range of
weeks, as trends). The choice carries across pages.

- **Roster**: every player with status colour, score, top reason, hours, report status and the share
  of hours the coaches can't verify. Red rows are flagged. Click a row to open the player.
- **Team charts**: box plots with every player as a dot — click a dot to open that player in the panel
  below. Metrics:
  - coach competency, coach effort, self-reported hours, self-rated effort
  - **total hours** (split by position in week view)
  - coach-only traits

  Zeros are hidden from hours boxes by default, with a toggle and a count of what's hidden. Tabs for a
  players × skills heatmap, an hours-vs-improvement scatter, and an effort-gap dumbbell.
- **Player**: competency against the team's middle-50% band, hours vs. competency, self vs. coach
  effort, coach-only traits, hours by skill (unverifiable hours in gray), and the source rows behind every
  number: file, CSV row, received time, superseded versions.
- **Flags**: every active flag with its evidence. Dismiss with a note, or restore.

## Cheat detection

Each rule gives a severity from 0 to 1. A player's score is `sum(weight × severity)`, capped at 1, over a
rolling window (6 weeks by default) ending at the selected week. Review starts at 0.30 and flagged at 0.65.
All of this is in `config.yaml`.

| Rule | Fires when | Weight |
|---|---|---|
| Hours vs. improvement | Hours on a skill well above the team median (≥4 h/week) while coach competency for that skill is flat relative to teammates with it as a core skill. Halved if *all* the player's skills are flat — that looks more like a plateau or a rating issue. | 1.0 |
| Effort gap | Self-rated effort minus coach effort is far above the team's typical gap (robust z > 2.5 and > 1.0 above median). | 0.6 |
| Implausible hours | Weekly total over 30 h, or a sudden jump to 2.5× the player's usual. | 0.7 |
| Copied report | A week's report identical to a teammate's (both flagged — you can't tell who copied). | 0.9 |
| Too-perfect data | Same effort on every row, nearly all 10.0s, or identical hours every week. | 0.5 |
| Late / backfilled | Week received >14 days late, or 3+ weeks in one file. | 0.4 |
| Edited resubmission | A week re-sent with ≥20% and ≥3 more hours. | 0.4 |

Design choices:

- **Off-profile skills**, like a defender practising shooting, are allowed. They're compared against
  players with that skill as core, and never penalised.
- **Unverifiable hours** are hours in skills no coach rated in the window. They're shown as a hint,
  not scored.
- **Coach-only traits are never used in detection**, so a low coachability rating can't make a player
  look like a cheater.
- **Missing reports are shown, not scored.** Players need 3 submitted weeks before they're judged;
  until then they show as gray, "Not enough data".
- **Detection is computed live**, so changing a weight or threshold takes effect on the next page load.
  A dismissal applies to one rule, player and window.

A red status means *have a conversation*, not *proven cheating*.

## Traceability

- **Raw files are never modified.** Every imported file is stored in `data/archive/` with its SHA-256.
  Every training row records its file and CSV row number.
- **Nothing is overwritten.** A resubmitted week or a corrected coach rating inserts a new row and marks
  the old one superseded. The history is visible on the Player page.
- **Scores are stored as integer tenths**, so 7.5 is saved as 75. The database itself enforces the
  1.0–10.0 scale.

## Backups

Copy the whole `data/` folder. `tracker.db` plus `archive/` is everything.
