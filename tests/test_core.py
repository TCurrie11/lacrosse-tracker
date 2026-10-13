import sqlite3
from datetime import date

import pytest

from tracker.coach import save_week_sheet
from tracker.config import CFG
from tracker.db import SCHEMA
from tracker.detection import run_detection
from tracker.ingest import ingest_file
from tracker import queries as q
from tracker.roster import add_player

TODAY = date(2026, 10, 12)
HEADER = "submit_code,week_start,skill,hours,effort\n"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for d in (CFG.inbox, CFG.archive, CFG.rejected):
        d.mkdir(parents=True, exist_ok=True)
    return c


def write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def test_valid_report_imports_and_fills_missing_skills(conn, tmp_path):
    pid = add_player(conn, "Ana", "attack", code="AAAA")
    res = ingest_file(conn, write(tmp_path, "a.csv", "AAAA,2026-10-05,passing,3.5,7.0\nAAAA,2026-10-05,shooting,0,9\n"),
                      today=TODAY)
    assert res.status == "imported", res.errors
    rep = q.reports_df(conn)
    assert len(rep) == len(CFG.skills)
    shooting = rep[rep.skill == "shooting"].iloc[0]
    assert shooting.hours == 0 and shooting.effort != shooting.effort  # effort ignored (NaN) on 0-hour rows
    assert rep[rep.filled_missing == 1].shape[0] == len(CFG.skills) - 2
    assert any("recorded as 0 hours" in w for w in res.warnings)
    assert rep.iloc[0].player_id == pid


@pytest.mark.parametrize("body,expected", [
    ("AAAA,2026-10-06,passing,3,7\n", "must be a Monday"),
    ("AAAA,2026-10-19,passing,3,7\n", "in the future"),
    ("AAAA,2026-10-05,passes,3,7\n", "unknown skill"),
    ("AAAA,2026-10-05,passing,3,\n", "effort is required"),
    ("AAAA,2026-10-05,passing,3,11\n", "outside"),
    ("AAAA,2026-10-05,passing,3.25,7\n", "more than one decimal"),
    ("AAAA,2026-10-05,passing,3,7\nBBBB,2026-10-05,catching,3,7\n", "more than one submit_code"),
    ("ZZZZ,2026-10-05,passing,3,7\n", "doesn't match any player"),
])
def test_bad_reports_are_rejected_whole(conn, tmp_path, body, expected):
    add_player(conn, "Ana", "attack", code="AAAA")
    add_player(conn, "Ben", "attack", code="BBBB")
    res = ingest_file(conn, write(tmp_path, "bad.csv", body), today=TODAY)
    assert res.status == "rejected"
    assert any(expected in e for e in res.errors), res.errors
    assert q.reports_df(conn).empty


def test_duplicate_file_refused_and_resubmission_supersedes(conn, tmp_path):
    add_player(conn, "Ana", "attack", code="AAAA")
    body = "AAAA,2026-10-05,passing,3,7\n"
    assert ingest_file(conn, write(tmp_path, "a.csv", body), today=TODAY).status == "imported"
    assert ingest_file(conn, write(tmp_path, "a.csv", body), today=TODAY).status == "duplicate"
    assert ingest_file(conn, write(tmp_path, "a2.csv", "AAAA,2026-10-05,passing,5,7\n"), today=TODAY).status == "imported"
    rep = q.reports_df(conn)
    assert rep[rep.skill == "passing"].hours.tolist() == [5.0]
    assert len(q.reports_df(conn, include_superseded=True)) == 2 * len(CFG.skills)


def test_coach_sheet_averages_coaches_and_keeps_history(conn):
    pid = add_player(conn, "Ana", "attack", code="AAAA")
    w = "2026-10-05"
    save_week_sheet(conn, w, "A", [{"player_id": pid, "skill": "passing", "competency": 6.0, "effort": 7.0}],
                    [{"player_id": pid, "trait": "attitude", "score": 8.0}])
    save_week_sheet(conn, w, "B", [{"player_id": pid, "skill": "passing", "competency": 7.0, "effort": None}], [])
    df = q.coach_skill_df(conn)
    assert df.competency.iloc[0] == 6.5 and df.effort.iloc[0] == 7.0
    n = save_week_sheet(conn, w, "A", [{"player_id": pid, "skill": "passing", "competency": 8.0, "effort": 7.0}], [])
    assert n["ratings"] == 1
    assert q.coach_skill_df(conn).competency.iloc[0] == 7.5
    assert conn.execute("SELECT COUNT(*) FROM coach_ratings").fetchone()[0] == 3
    with pytest.raises(ValueError):
        save_week_sheet(conn, w, "A", [{"player_id": pid, "skill": "passing", "competency": 10.5, "effort": None}], [])


def test_hours_vs_gain_flags_high_hours_flat_competency(conn, tmp_path):
    weeks = ["2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    ids = {}
    for i in range(6):
        ids[i] = add_player(conn, f"P{i}", "midfield", code=f"C{i}AA")
    for wi, w in enumerate(weeks):
        rows = []
        for i, pid in ids.items():
            cheat = i == 0
            rows.append({"player_id": pid, "skill": "passing",
                         "competency": round(5.0 + (0 if cheat else 0.2 * wi + 0.01 * i), 1), "effort": 6.0})
            hours = 18 if cheat else 3
            ingest_file(conn, write(tmp_path, f"{i}_{w}.csv", f"C{i}AA,{w},passing,{hours},6.5\n"),
                        received_at=f"{w}T00:00:00", today=TODAY)
        save_week_sheet(conn, w, "Coach", rows, [])
    res = run_detection(conn)
    hv = [f for f in res.flags if f.rule == "hours_vs_gain"]
    assert [f.player_id for f in hv] == [ids[0]]
    assert res.status_of(ids[0]) == "red"
    assert all(res.status_of(ids[i]) == "green" for i in range(1, 6))
