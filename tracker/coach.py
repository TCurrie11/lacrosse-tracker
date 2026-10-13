"""Weekly coach sheet: competency + effort per skill, plus coach-only traits.

Each coach fills one sheet per week. The app averages across coaches; every coach's own
ratings are kept. Saving again supersedes that coach's previous value (history is kept).
A blank value means "not observed" — it is never treated as a low score.
"""
from __future__ import annotations

import math
import sqlite3

import pandas as pd

from .config import CFG
from .db import now_iso, to_tenths


def _check(value, what: str):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    v = round(float(value), 1)
    if not (CFG.scale_min <= v <= CFG.scale_max):
        raise ValueError(f"{what} must be between {CFG.scale_min:g} and {CFG.scale_max:g} (got {value})")
    return to_tenths(v)


def save_week_sheet(conn: sqlite3.Connection, week: str, coach: str,
                    skill_rows: list[dict], trait_rows: list[dict]) -> dict:
    """skill_rows: {player_id, skill, competency, effort}; trait_rows: {player_id, trait, score}.

    Returns counts of changed values. Unchanged values are not re-inserted.
    """
    coach = coach.strip()
    if not coach:
        raise ValueError("Enter the coach's name before saving.")
    ts = now_iso()
    changed = {"ratings": 0, "traits": 0}
    with conn:
        for r in skill_rows:
            comp = _check(r.get("competency"), f"{r['skill']} competency")
            eff = _check(r.get("effort"), f"{r['skill']} effort")
            prev = conn.execute(
                "SELECT id, competency, effort FROM coach_ratings WHERE week_start=? AND player_id=? AND coach=?"
                " AND skill=? AND superseded_by IS NULL", (week, r["player_id"], coach, r["skill"])).fetchone()
            if prev is None and comp is None and eff is None:
                continue
            if prev is not None and prev["competency"] == comp and prev["effort"] == eff:
                continue
            new_id = conn.execute(
                "INSERT INTO coach_ratings (week_start, player_id, coach, skill, competency, effort, created_at)"
                " VALUES (?,?,?,?,?,?,?)", (week, r["player_id"], coach, r["skill"], comp, eff, ts)).lastrowid
            if prev is not None:
                conn.execute("UPDATE coach_ratings SET superseded_by=? WHERE id=?", (new_id, prev["id"]))
            changed["ratings"] += 1
        for r in trait_rows:
            score = _check(r.get("score"), f"{r['trait']}")
            prev = conn.execute(
                "SELECT id, score FROM coach_traits WHERE week_start=? AND player_id=? AND coach=? AND trait=?"
                " AND superseded_by IS NULL", (week, r["player_id"], coach, r["trait"])).fetchone()
            if prev is None and score is None:
                continue
            if prev is not None and prev["score"] == score:
                continue
            new_id = conn.execute(
                "INSERT INTO coach_traits (week_start, player_id, coach, trait, score, created_at) VALUES (?,?,?,?,?,?)",
                (week, r["player_id"], coach, r["trait"], score, ts)).lastrowid
            if prev is not None:
                conn.execute("UPDATE coach_traits SET superseded_by=? WHERE id=?", (new_id, prev["id"]))
            changed["traits"] += 1
    return changed


def coaches(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT coach FROM coach_ratings UNION SELECT coach FROM coach_traits ORDER BY 1").fetchall()
    return [r[0] for r in rows]


def prefill(conn: sqlite3.Connection, week: str, coach: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Values to pre-fill the sheet with, plus a note on where they came from.

    Order: this coach's values for this week -> this coach's most recent earlier week -> nothing.
    """
    def latest_week(table):
        row = conn.execute(f"SELECT MAX(week_start) FROM {table} WHERE coach=? AND week_start<?", (coach, week)).fetchone()
        return row[0]

    has_this = conn.execute("SELECT 1 FROM coach_ratings WHERE coach=? AND week_start=? AND superseded_by IS NULL",
                            (coach, week)).fetchone()
    if has_this:
        src_week, note = week, "Showing what you already saved for this week."
    else:
        src_week = latest_week("coach_ratings")
        note = (f"Pre-filled from your sheet for the week of {src_week} — change only what moved."
                if src_week else "No earlier sheet from this coach — starting blank.")
    ratings = pd.read_sql(
        "SELECT player_id, skill, competency/10.0 AS competency, effort/10.0 AS effort FROM coach_ratings"
        " WHERE coach=? AND week_start=? AND superseded_by IS NULL", conn, params=(coach, src_week or ""))
    traits_week = week if conn.execute(
        "SELECT 1 FROM coach_traits WHERE coach=? AND week_start=? AND superseded_by IS NULL", (coach, week)
    ).fetchone() else latest_week("coach_traits")
    traits = pd.read_sql(
        "SELECT player_id, trait, score/10.0 AS score FROM coach_traits"
        " WHERE coach=? AND week_start=? AND superseded_by IS NULL", conn, params=(coach, traits_week or ""))
    return ratings, traits, note
