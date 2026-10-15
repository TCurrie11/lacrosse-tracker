"""
Read-side queries. Everything the app shows comes through here as DataFrames.

Scores are stored as integer tenths and converted back to the 1.0-10.0 scale here.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd


def players_df(conn: sqlite3.Connection, active_only: bool = True) -> pd.DataFrame:
    sql = "SELECT id AS player_id, name, number, position, submit_code, active FROM players"
    if active_only:
        sql += " WHERE active = 1"
    return pd.read_sql(sql + " ORDER BY name", conn)


def weeks_available(conn: sqlite3.Connection) -> list[str]:
    """Every week (Monday, ISO date) that has a player report or a coach rating, oldest first."""
    rows = conn.execute(
        "SELECT week_start FROM submissions UNION SELECT week_start FROM coach_ratings"
        " UNION SELECT week_start FROM coach_traits ORDER BY 1").fetchall()
    return [row[0] for row in rows]


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def reports_df(conn: sqlite3.Connection, include_superseded: bool = False) -> pd.DataFrame:
    """One row per player / week / skill from the current (non-superseded) submission."""
    sql = """
        SELECT s.player_id, s.week_start, r.skill, r.hours, r.effort / 10.0 AS effort,
               r.source_row, r.filled_missing, s.id AS submission_id, s.received_at,
               s.superseded_by, f.id AS file_id, f.original_name AS file, f.sha256
        FROM training_reports r
        JOIN submissions s ON s.id = r.submission_id
        JOIN ingest_files f ON f.id = s.ingest_file_id
    """
    if not include_superseded:
        sql += " WHERE s.superseded_by IS NULL"
    return pd.read_sql(sql, conn)


def coach_skill_df(conn: sqlite3.Connection) -> pd.DataFrame:
    """Weekly coach score per player / skill = average across coaches."""
    ratings = pd.read_sql("""
        SELECT week_start, player_id, skill,
               AVG(competency) / 10.0 AS competency, AVG(effort) / 10.0 AS effort,
               COUNT(competency) AS n_coaches, GROUP_CONCAT(DISTINCT coach) AS coaches
        FROM coach_ratings WHERE superseded_by IS NULL
        GROUP BY week_start, player_id, skill
        HAVING COUNT(competency) > 0 OR COUNT(effort) > 0
    """, conn)
    for column in ("competency", "effort"):
        ratings[column] = ratings[column].round(1)
    return ratings


def coach_traits_df(conn: sqlite3.Connection) -> pd.DataFrame:
    """Weekly coach-only trait score per player / trait = average across coaches."""
    traits = pd.read_sql("""
        SELECT week_start, player_id, trait, AVG(score) / 10.0 AS score, COUNT(score) AS n_coaches
        FROM coach_traits WHERE superseded_by IS NULL
        GROUP BY week_start, player_id, trait HAVING COUNT(score) > 0
    """, conn)
    traits["score"] = traits["score"].round(1)
    return traits


def submissions_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT s.id AS submission_id, s.player_id, s.week_start, s.received_at, s.superseded_by,
               s.ingest_file_id, f.original_name AS file
        FROM submissions s JOIN ingest_files f ON f.id = s.ingest_file_id
    """, conn)


def ingest_log_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("""
        SELECT f.id, f.received_at, f.original_name AS file, f.status, p.name AS player,
               f.weeks, f.row_count AS rows, f.message, f.sha256, f.stored_path
        FROM ingest_files f LEFT JOIN players p ON p.id = f.player_id
        ORDER BY f.id DESC
    """, conn)


def missing_reports(conn: sqlite3.Connection, weeks: list[str]) -> pd.DataFrame:
    """Active players with no submission for each of `weeks`."""
    players = players_df(conn)
    submissions = submissions_df(conn)
    submitted = set(zip(submissions.player_id, submissions.week_start))
    rows = [(player.player_id, player.name, week) for week in weeks for player in players.itertuples()
            if (player.player_id, week) not in submitted]
    return pd.DataFrame(rows, columns=["player_id", "name", "week_start"])


def weekly_totals(reports: pd.DataFrame) -> pd.DataFrame:
    return (reports.groupby(["player_id", "week_start"], as_index=False)["hours"].sum()
            .rename(columns={"hours": "total_hours"}))
