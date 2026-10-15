"""
Weekly coach sheet: competency + effort per skill, plus coach-only traits.

Each coach fills one sheet per week. The app averages across coaches; every coach's own
ratings are kept. Saving again supersedes that coach's previous value (history is kept).
A blank value means "not observed" — it is never treated as a low score.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import math
import sqlite3

import pandas as pd

from .config import CFG
from .db import now_iso, to_tenths


def _validated_tenths(value, field_name: str):
    """A score on the 1.0-10.0 scale as integer tenths, or None if blank. Raises if out of range."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    score = round(float(value), 1)
    if not (CFG.scale_min <= score <= CFG.scale_max):
        raise ValueError(f"{field_name} must be between {CFG.scale_min:g} and {CFG.scale_max:g} (got {value})")
    return to_tenths(score)


def save_week_sheet(conn: sqlite3.Connection, week: str, coach: str,
                    skill_rows: list[dict], trait_rows: list[dict]) -> dict:
    """skill_rows: {player_id, skill, competency, effort}; trait_rows: {player_id, trait, score}.

    Returns counts of changed values. Unchanged values are not re-inserted.
    """
    coach = coach.strip()
    if not coach:
        raise ValueError("Enter the coach's name before saving.")
    saved_at = now_iso()
    changed = {"ratings": 0, "traits": 0}
    with conn:
        for row in skill_rows:
            competency = _validated_tenths(row.get("competency"), f"{row['skill']} competency")
            effort = _validated_tenths(row.get("effort"), f"{row['skill']} effort")
            previous = conn.execute(
                "SELECT id, competency, effort FROM coach_ratings WHERE week_start=? AND player_id=? AND coach=?"
                " AND skill=? AND superseded_by IS NULL", (week, row["player_id"], coach, row["skill"])).fetchone()
            if previous is None and competency is None and effort is None:
                continue
            if previous is not None and previous["competency"] == competency and previous["effort"] == effort:
                continue
            new_id = conn.execute(
                "INSERT INTO coach_ratings (week_start, player_id, coach, skill, competency, effort, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (week, row["player_id"], coach, row["skill"], competency, effort, saved_at)).lastrowid
            if previous is not None:
                conn.execute("UPDATE coach_ratings SET superseded_by=? WHERE id=?", (new_id, previous["id"]))
            changed["ratings"] += 1

        for row in trait_rows:
            score = _validated_tenths(row.get("score"), f"{row['trait']}")
            previous = conn.execute(
                "SELECT id, score FROM coach_traits WHERE week_start=? AND player_id=? AND coach=? AND trait=?"
                " AND superseded_by IS NULL", (week, row["player_id"], coach, row["trait"])).fetchone()
            if previous is None and score is None:
                continue
            if previous is not None and previous["score"] == score:
                continue
            new_id = conn.execute(
                "INSERT INTO coach_traits (week_start, player_id, coach, trait, score, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (week, row["player_id"], coach, row["trait"], score, saved_at)).lastrowid
            if previous is not None:
                conn.execute("UPDATE coach_traits SET superseded_by=? WHERE id=?", (new_id, previous["id"]))
            changed["traits"] += 1
    return changed


def coaches(conn: sqlite3.Connection) -> list[str]:
    """Every coach name that has saved a sheet, alphabetically."""
    rows = conn.execute(
        "SELECT coach FROM coach_ratings UNION SELECT coach FROM coach_traits ORDER BY 1").fetchall()
    return [row[0] for row in rows]


def prefill(conn: sqlite3.Connection, week: str, coach: str) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Values to pre-fill the sheet with, plus a note on where they came from.

    Order: this coach's values for this week -> this coach's most recent earlier week -> nothing.
    """
    def latest_earlier_week(table: str) -> str | None:
        row = conn.execute(f"SELECT MAX(week_start) FROM {table} WHERE coach=? AND week_start<?",
                           (coach, week)).fetchone()
        return row[0]

    has_ratings_this_week = conn.execute(
        "SELECT 1 FROM coach_ratings WHERE coach=? AND week_start=? AND superseded_by IS NULL",
        (coach, week)).fetchone()
    if has_ratings_this_week:
        ratings_week, note = week, "Showing what you already saved for this week."
    else:
        ratings_week = latest_earlier_week("coach_ratings")
        note = (f"Pre-filled from your sheet for the week of {ratings_week} — change only what moved."
                if ratings_week else "No earlier sheet from this coach — starting blank.")
    ratings = pd.read_sql(
        "SELECT player_id, skill, competency/10.0 AS competency, effort/10.0 AS effort FROM coach_ratings"
        " WHERE coach=? AND week_start=? AND superseded_by IS NULL", conn, params=(coach, ratings_week or ""))

    has_traits_this_week = conn.execute(
        "SELECT 1 FROM coach_traits WHERE coach=? AND week_start=? AND superseded_by IS NULL", (coach, week)
    ).fetchone()
    traits_week = week if has_traits_this_week else latest_earlier_week("coach_traits")
    traits = pd.read_sql(
        "SELECT player_id, trait, score/10.0 AS score FROM coach_traits"
        " WHERE coach=? AND week_start=? AND superseded_by IS NULL", conn, params=(coach, traits_week or ""))
    return ratings, traits, note
