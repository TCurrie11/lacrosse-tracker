"""
Generate a demo season so you can click around and see flags fire.

    python scripts/make_demo.py          # refuses if data/ already has a database
    python scripts/make_demo.py --fresh  # wipes data/ first

Planted cases (so you can check the detector):
  - Marc Côté (midfield): ~18 h/week of passing, competency flat      -> hours vs. improvement
  - Théo Lavoie (defense): rates his own effort ~2.5 above the coaches -> effort gap
  - Zoé Roy & Liam Gagnon: identical report for one week               -> copied report
  - Noah Fortin: 3 weeks sent in one file, weeks late                  -> backfill
  - Jacob Morin: re-submits a week with 40% more hours                 -> edited resubmission
  - A few missing weeks, a defender who sometimes practises shooting (off-profile, not penalised),
    and one rejected file (bad skill name) in the import log.

All names are fictional. The random seed is fixed, so every run produces the same season.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.coach import save_week_sheet  # noqa: E402
from tracker.config import CFG  # noqa: E402
from tracker.db import connect  # noqa: E402
from tracker.ingest import REQUIRED_COLUMNS, ingest_file  # noqa: E402
from tracker.roster import add_player  # noqa: E402

PLAYERS = [  # (name, jersey number, position)
    ("Zoé Roy", 7, "attack"), ("Liam Gagnon", 11, "attack"), ("Nathan Tremblay", 19, "attack"),
    ("Rémi Lévesque", 23, "attack"), ("Marc Côté", 4, "midfield"), ("Jacob Morin", 8, "midfield"),
    ("Élise Gauthier", 13, "midfield"), ("Sam Bouchard", 15, "midfield"), ("Félix Pelletier", 21, "midfield"),
    ("Théo Lavoie", 3, "defense"), ("Noah Fortin", 6, "defense"), ("Olivier Bélanger", 9, "defense"),
    ("Charles Girard", 27, "defense"), ("Maxime Ouellet", 1, "goalie"),
]
COACHES = ["Coach Smith", "Coach Dubois"]
WEEKS = [date(2026, 8, 17) + timedelta(weeks=week_number) for week_number in range(8)]  # Aug 17 .. Oct 5
NOW = datetime(2026, 10, 12, 9, 0)
RANDOM_SEED = 42

# Planted cases (week numbers are 0-based indexes into WEEKS)
MISSING_REPORTS = {("Rémi Lévesque", 2), ("Rémi Lévesque", 5), ("Sam Bouchard", 6), ("Charles Girard", 7),
                   ("Maxime Ouellet", 3)}
COPY_WEEK = 5
BACKFILL_WEEKS = [2, 3, 4]


def on_scale(value: float) -> float:
    """Clamp to the rating scale and round to one decimal, like a real entry."""
    return round(min(CFG.scale_max, max(CFG.scale_min, value)), 1)


def main(fresh: bool) -> None:
    if CFG.db_path.exists():
        if not fresh:
            sys.exit(f"{CFG.db_path} already exists. Run with --fresh to wipe {CFG.data_dir} and regenerate.")
        shutil.rmtree(CFG.data_dir)
    conn = connect()
    rng = np.random.default_rng(RANDOM_SEED)

    player_ids = {name: add_player(conn, name, position, number) for name, number, position in PLAYERS}
    submit_code = {row["name"]: row["submit_code"] for row in conn.execute("SELECT name, submit_code FROM players")}

    # ----- true underlying ability per player/skill
    base_skill, weekly_growth, effort_level, usual_hours = {}, {}, {}, {}
    for name, _, position in PLAYERS:
        effort_level[name] = rng.uniform(5.5, 8.5)
        for skill in CFG.skills:
            is_core = CFG.is_core(position, skill)
            base_skill[name, skill] = rng.uniform(4.0, 7.0) if is_core else rng.uniform(2.5, 4.5)
            weekly_growth[name, skill] = rng.uniform(0.10, 0.22) if is_core else 0.02
            usual_hours[name, skill] = rng.uniform(1.0, 3.5) if is_core else 0.0
    effort_level["Théo Lavoie"] = 5.8
    weekly_growth["Marc Côté", "passing"] = 0.0
    usual_hours["Marc Côté", "passing"] = 18.0

    # ----- coach sheets (both coaches, end of each week)
    for week_number, week in enumerate(WEEKS):
        for coach in COACHES:
            skill_rows, trait_rows = [], []
            for name, _, position in PLAYERS:
                core_skills = [skill for skill in CFG.skills if CFG.is_core(position, skill)]
                for skill in core_skills:
                    if rng.random() < 0.12:  # not observed this week
                        continue
                    true_ability = base_skill[name, skill] + weekly_growth[name, skill] * week_number
                    skill_rows.append({"player_id": player_ids[name], "skill": skill,
                                       "competency": on_scale(true_ability + rng.normal(0, 0.25)),
                                       "effort": on_scale(effort_level[name] + rng.normal(0, 0.5))})
                for trait in CFG.coach_traits:
                    trait_rows.append({"player_id": player_ids[name], "trait": trait,
                                       "score": on_scale(effort_level[name] + rng.normal(0.3, 0.8))})
            save_week_sheet(conn, week.isoformat(), coach, skill_rows, trait_rows)

    # ----- player weekly reports
    def report_lines(name: str, week_number: int, hours_multiplier: float = 1.0) -> list[str]:
        lines = []
        self_rating_bias = 2.4 if name == "Théo Lavoie" else 0.5
        for skill in CFG.skills:
            hours = usual_hours[name, skill]
            if hours == 0 and name == "Olivier Bélanger" and skill == "shooting" and rng.random() < 0.4:
                hours = rng.uniform(0.5, 1.5)  # defender who sometimes shoots — off-profile, allowed
            if hours > 0:
                hours = max(0.5, hours + rng.normal(0, 0.6 if hours < 10 else 1.2)) * hours_multiplier
                effort = on_scale(effort_level[name] + self_rating_bias + rng.normal(0, 0.5))
                lines.append(f"{submit_code[name]},{WEEKS[week_number]},{skill},{round(hours, 1)},{effort}")
            else:
                lines.append(f"{submit_code[name]},{WEEKS[week_number]},{skill},0,")
        return lines

    def submit(file_name: str, lines: list[str], received: datetime) -> None:
        path = CFG.inbox_dir / file_name
        path.write_text(",".join(REQUIRED_COLUMNS) + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
        result = ingest_file(conn, path, received_at=min(received, NOW).isoformat(timespec="seconds"))
        if result.status != "imported" and "BAD" not in file_name:
            raise RuntimeError(f"{file_name}: {result.errors}")

    def end_of_week(week: date) -> datetime:
        return datetime.combine(week + timedelta(days=6), datetime.min.time())

    copied_lines = None
    for week_number, week in enumerate(WEEKS):
        for name, _, _ in PLAYERS:
            if (name, week_number) in MISSING_REPORTS:
                continue
            if name == "Noah Fortin" and week_number in BACKFILL_WEEKS:
                continue
            received = end_of_week(week) + timedelta(hours=float(rng.uniform(17, 40)))
            if name == "Liam Gagnon" and week_number == COPY_WEEK:
                lines = [line.replace(submit_code["Zoé Roy"], submit_code["Liam Gagnon"]) for line in copied_lines]
            else:
                lines = report_lines(name, week_number)
            if name == "Zoé Roy" and week_number == COPY_WEEK:
                copied_lines = lines
            submit(f"{name.split()[0].lower()}_{week}.csv", lines, received)
        if week_number == BACKFILL_WEEKS[-1] + 2:
            lines = [line for backfill_week in BACKFILL_WEEKS for line in report_lines("Noah Fortin", backfill_week)]
            submit("noah_catchup.csv", lines, end_of_week(week))

    # Jacob re-submits week 6 with more hours
    submit("jacob_2026-09-21_v2.csv", report_lines("Jacob Morin", 5, hours_multiplier=1.4),
           datetime.combine(WEEKS[6] + timedelta(days=2), datetime.min.time()))
    # One bad file so the import log shows a rejection
    submit("BAD_sam_typo.csv", [f"{submit_code['Sam Bouchard']},{WEEKS[7]},passes,3,7"], end_of_week(WEEKS[7]))

    report_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    print(f"Demo data written to {CFG.data_dir}: {len(PLAYERS)} players, {len(WEEKS)} weeks, "
          f"{report_count} weekly reports.")
    print("Run:  streamlit run app.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--fresh", action="store_true", help="wipe the data folder first")
    main(parser.parse_args().fresh)
