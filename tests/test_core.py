"""
Core tests: CSV import and validation, resubmissions, coach-sheet averaging, and the
hours-vs-improvement detection rule.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
import sqlite3
from datetime import date

import pytest

from tracker import queries
from tracker.coach import save_week_sheet
from tracker.config import CFG
from tracker.db import SCHEMA
from tracker.detection import run_detection
from tracker.ingest import ingest_file
from tracker.roster import add_player

TODAY = date(2026, 10, 12)
HEADER = "submit_code,week_start,skill,hours,effort\n"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    for folder in (CFG.inbox_dir, CFG.archive_dir, CFG.rejected_dir):
        folder.mkdir(parents=True, exist_ok=True)
    return connection


def write_report(tmp_path, file_name, body):
    path = tmp_path / file_name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_valid_report_imports_and_fills_missing_skills(conn, tmp_path):
    player_id = add_player(conn, "Ana", "attack", code="AAAA")
    report = write_report(tmp_path, "a.csv", "AAAA,2026-10-05,passing,3.5,7.0\nAAAA,2026-10-05,shooting,0,9\n")
    result = ingest_file(conn, report, today=TODAY)
    assert result.status == "imported", result.errors
    reports = queries.reports_df(conn)
    assert len(reports) == len(CFG.skills)
    shooting = reports[reports.skill == "shooting"].iloc[0]
    assert shooting.hours == 0 and shooting.effort != shooting.effort  # effort ignored (NaN) on 0-hour rows
    assert reports[reports.filled_missing == 1].shape[0] == len(CFG.skills) - 2
    assert any("recorded as 0 hours" in warning for warning in result.warnings)
    assert reports.iloc[0].player_id == player_id


@pytest.mark.parametrize("body,expected_error", [
    ("AAAA,2026-10-06,passing,3,7\n", "must be a Monday"),
    ("AAAA,2026-10-19,passing,3,7\n", "in the future"),
    ("AAAA,2026-10-05,passes,3,7\n", "unknown skill"),
    ("AAAA,2026-10-05,passing,3,\n", "effort is required"),
    ("AAAA,2026-10-05,passing,3,11\n", "outside"),
    ("AAAA,2026-10-05,passing,3.25,7\n", "more than one decimal"),
    ("AAAA,2026-10-05,passing,3,7\nBBBB,2026-10-05,catching,3,7\n", "more than one submit_code"),
    ("ZZZZ,2026-10-05,passing,3,7\n", "doesn't match any player"),
])
def test_bad_reports_are_rejected_whole(conn, tmp_path, body, expected_error):
    add_player(conn, "Ana", "attack", code="AAAA")
    add_player(conn, "Ben", "attack", code="BBBB")
    result = ingest_file(conn, write_report(tmp_path, "bad.csv", body), today=TODAY)
    assert result.status == "rejected"
    assert any(expected_error in error for error in result.errors), result.errors
    assert queries.reports_df(conn).empty


def test_duplicate_file_refused_and_resubmission_supersedes(conn, tmp_path):
    add_player(conn, "Ana", "attack", code="AAAA")
    body = "AAAA,2026-10-05,passing,3,7\n"
    assert ingest_file(conn, write_report(tmp_path, "a.csv", body), today=TODAY).status == "imported"
    assert ingest_file(conn, write_report(tmp_path, "a.csv", body), today=TODAY).status == "duplicate"
    corrected = write_report(tmp_path, "a2.csv", "AAAA,2026-10-05,passing,5,7\n")
    assert ingest_file(conn, corrected, today=TODAY).status == "imported"
    reports = queries.reports_df(conn)
    assert reports[reports.skill == "passing"].hours.tolist() == [5.0]
    assert len(queries.reports_df(conn, include_superseded=True)) == 2 * len(CFG.skills)


def test_coach_sheet_averages_coaches_and_keeps_history(conn):
    player_id = add_player(conn, "Ana", "attack", code="AAAA")
    week = "2026-10-05"
    save_week_sheet(conn, week, "A", [{"player_id": player_id, "skill": "passing", "competency": 6.0, "effort": 7.0}],
                    [{"player_id": player_id, "trait": "attitude", "score": 8.0}])
    save_week_sheet(conn, week, "B", [{"player_id": player_id, "skill": "passing", "competency": 7.0, "effort": None}],
                    [])
    ratings = queries.coach_skill_df(conn)
    assert ratings.competency.iloc[0] == 6.5 and ratings.effort.iloc[0] == 7.0
    changed = save_week_sheet(conn, week, "A",
                              [{"player_id": player_id, "skill": "passing", "competency": 8.0, "effort": 7.0}], [])
    assert changed["ratings"] == 1
    assert queries.coach_skill_df(conn).competency.iloc[0] == 7.5
    assert conn.execute("SELECT COUNT(*) FROM coach_ratings").fetchone()[0] == 3
    with pytest.raises(ValueError):
        save_week_sheet(conn, week, "A",
                        [{"player_id": player_id, "skill": "passing", "competency": 10.5, "effort": None}], [])


def test_hours_vs_gain_flags_high_hours_flat_competency(conn, tmp_path):
    weeks = ["2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    player_ids = {}
    for player_number in range(6):
        player_ids[player_number] = add_player(conn, f"P{player_number}", "midfield", code=f"C{player_number}AA")
    for week_number, week in enumerate(weeks):
        ratings = []
        for player_number, player_id in player_ids.items():
            is_cheater = player_number == 0
            competency = 5.0 + (0 if is_cheater else 0.2 * week_number + 0.01 * player_number)
            ratings.append({"player_id": player_id, "skill": "passing",
                            "competency": round(competency, 1), "effort": 6.0})
            hours = 18 if is_cheater else 3
            report = write_report(tmp_path, f"{player_number}_{week}.csv",
                                  f"C{player_number}AA,{week},passing,{hours},6.5\n")
            ingest_file(conn, report, received_at=f"{week}T00:00:00", today=TODAY)
        save_week_sheet(conn, week, "Coach", ratings, [])
    detection = run_detection(conn)
    hours_vs_gain_flags = [flag for flag in detection.flags if flag.rule == "hours_vs_gain"]
    assert [flag.player_id for flag in hours_vs_gain_flags] == [player_ids[0]]
    assert detection.status_of(player_ids[0]) == "red"
    assert all(detection.status_of(player_ids[number]) == "green" for number in range(1, 6))
