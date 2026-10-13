"""Generate a demo season so you can click around and see flags fire.

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
from tracker.ingest import ingest_file  # noqa: E402
from tracker.roster import add_player  # noqa: E402

PLAYERS = [
    ("Zoé Roy", 7, "attack"), ("Liam Gagnon", 11, "attack"), ("Nathan Tremblay", 19, "attack"),
    ("Rémi Lévesque", 23, "attack"), ("Marc Côté", 4, "midfield"), ("Jacob Morin", 8, "midfield"),
    ("Élise Gauthier", 13, "midfield"), ("Sam Bouchard", 15, "midfield"), ("Félix Pelletier", 21, "midfield"),
    ("Théo Lavoie", 3, "defense"), ("Noah Fortin", 6, "defense"), ("Olivier Bélanger", 9, "defense"),
    ("Charles Girard", 27, "defense"), ("Maxime Ouellet", 1, "goalie"),
]
COACHES = ["Coach Smith", "Coach Dubois"]
WEEKS = [date(2026, 8, 17) + timedelta(weeks=i) for i in range(8)]  # Aug 17 .. Oct 5
NOW = datetime(2026, 10, 12, 9, 0)


def t1(x: float) -> float:
    return round(min(CFG.scale_max, max(CFG.scale_min, x)), 1)


def main(fresh: bool) -> None:
    if CFG.db_path.exists():
        if not fresh:
            sys.exit(f"{CFG.db_path} already exists. Run with --fresh to wipe {CFG.data_dir} and regenerate.")
        shutil.rmtree(CFG.data_dir)
    conn = connect()
    rng = np.random.default_rng(42)

    ids = {name: add_player(conn, name, pos, num) for name, num, pos in PLAYERS}
    pos = {name: p for name, _, p in PLAYERS}
    code = {r["name"]: r["submit_code"] for r in conn.execute("SELECT name, submit_code FROM players")}

    # ----- true underlying ability per player/skill
    base, growth, effort_lvl, hours_lvl = {}, {}, {}, {}
    for name, _, p in PLAYERS:
        effort_lvl[name] = rng.uniform(5.5, 8.5)
        for s in CFG.skills:
            core = CFG.is_core(p, s)
            base[name, s] = rng.uniform(4.0, 7.0) if core else rng.uniform(2.5, 4.5)
            growth[name, s] = rng.uniform(0.10, 0.22) if core else 0.02
            hours_lvl[name, s] = rng.uniform(1.0, 3.5) if core else 0.0
    effort_lvl["Théo Lavoie"] = 5.8
    growth["Marc Côté", "passing"] = 0.0
    hours_lvl["Marc Côté", "passing"] = 18.0

    # ----- coach sheets (both coaches, end of each week)
    for wi, week in enumerate(WEEKS):
        for coach in COACHES:
            skill_rows, trait_rows = [], []
            for name, _, p in PLAYERS:
                rated = [s for s in CFG.skills if CFG.is_core(p, s)]
                for s in rated:
                    if rng.random() < 0.12:  # not observed this week
                        continue
                    true = base[name, s] + growth[name, s] * wi
                    skill_rows.append({"player_id": ids[name], "skill": s,
                                       "competency": t1(true + rng.normal(0, 0.25)),
                                       "effort": t1(effort_lvl[name] + rng.normal(0, 0.5))})
                for tr in CFG.coach_traits:
                    trait_rows.append({"player_id": ids[name], "trait": tr,
                                       "score": t1(effort_lvl[name] + rng.normal(0.3, 0.8))})
            save_week_sheet(conn, week.isoformat(), coach, skill_rows, trait_rows)

    # ----- player weekly reports
    inbox = CFG.inbox
    missing = {("Rémi Lévesque", 2), ("Rémi Lévesque", 5), ("Sam Bouchard", 6), ("Charles Girard", 7),
               ("Maxime Ouellet", 3)}
    copy_week = 5
    backfill_weeks = [2, 3, 4]

    def week_rows(name: str, wi: int, scale: float = 1.0) -> list[str]:
        out = []
        bias = 2.4 if name == "Théo Lavoie" else 0.5
        for s in CFG.skills:
            h = hours_lvl[name, s]
            if h == 0 and name == "Olivier Bélanger" and s == "shooting" and rng.random() < 0.4:
                h = rng.uniform(0.5, 1.5)  # defender who sometimes shoots — off-profile, allowed
            if h > 0:
                h = max(0.5, h + rng.normal(0, 0.6 if h < 10 else 1.2)) * scale
                e = t1(effort_lvl[name] + bias + rng.normal(0, 0.5))
                out.append(f"{code[name]},{WEEKS[wi]},{s},{round(h, 1)},{e}")
            else:
                out.append(f"{code[name]},{WEEKS[wi]},{s},0,")
        return out

    def submit(fname: str, lines: list[str], received: datetime) -> None:
        path = inbox / fname
        path.write_text("submit_code,week_start,skill,hours,effort\n" + "\n".join(lines) + "\n", encoding="utf-8")
        res = ingest_file(conn, path, received_at=min(received, NOW).isoformat(timespec="seconds"))
        if res.status != "imported" and "BAD" not in fname:
            raise RuntimeError(f"{fname}: {res.errors}")

    copied_lines = None
    for wi, week in enumerate(WEEKS):
        for name, _, _ in PLAYERS:
            if (name, wi) in missing:
                continue
            if name == "Noah Fortin" and wi in backfill_weeks:
                continue
            received = datetime.combine(week + timedelta(days=6), datetime.min.time()) + timedelta(
                hours=float(rng.uniform(17, 40)))
            if name == "Liam Gagnon" and wi == copy_week:
                lines = [ln.replace(code["Zoé Roy"], code["Liam Gagnon"]) for ln in copied_lines]
            else:
                lines = week_rows(name, wi)
            if name == "Zoé Roy" and wi == copy_week:
                copied_lines = lines
            submit(f"{name.split()[0].lower()}_{week}.csv", lines, received)
        if wi == backfill_weeks[-1] + 2:
            lines = [ln for w in backfill_weeks for ln in week_rows("Noah Fortin", w)]
            submit("noah_catchup.csv", lines, datetime.combine(week + timedelta(days=6), datetime.min.time()))

    # Jacob re-submits week 6 with more hours
    submit("jacob_2026-09-21_v2.csv", week_rows("Jacob Morin", 5, scale=1.4),
           datetime.combine(WEEKS[6] + timedelta(days=2), datetime.min.time()))
    # One bad file so the import log shows a rejection
    submit("BAD_sam_typo.csv", [f"{code['Sam Bouchard']},{WEEKS[7]},passes,3,7"],
           datetime.combine(WEEKS[7] + timedelta(days=6), datetime.min.time()))

    n = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    print(f"Demo data written to {CFG.data_dir}: {len(PLAYERS)} players, {len(WEEKS)} weeks, {n} weekly reports.")
    print("Run:  streamlit run app.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="wipe the data folder first")
    main(ap.parse_args().fresh)
