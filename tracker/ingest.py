"""
Weekly player report import.

CSV format (one file per player; may contain one or more weeks):

    submit_code,week_start,skill,hours,effort
    K7Q2,2026-10-05,passing,6.0,7.5
    K7Q2,2026-10-05,shooting,0,

Rules:
- A file is imported atomically: any error rejects the whole file (moved to data/rejected/ with an
  .errors.txt next to it). Nothing half-imports.
- week_start must be a Monday and not in the future.
- hours = 0 means "didn't train this skill"; any effort on that row is ignored (stored NULL).
- hours > 0 requires an effort between 1.0 and 10.0 (one decimal).
- Skills missing from a week are filled in as 0 hours (with a warning) so a blank can't be
  confused with untrained.
- Re-importing a byte-identical file is refused. Sending a corrected report for the same week
  supersedes the old one (both are kept for the audit trail).

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import csv
import hashlib
import io
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import CFG
from .db import now_iso, to_tenths

REQUIRED_COLUMNS = ["submit_code", "week_start", "skill", "hours", "effort"]


@dataclass
class IngestResult:
    file: str
    status: str  # imported | rejected | duplicate
    player: str | None = None
    weeks: list[str] = field(default_factory=list)
    rows: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalise_skill(raw_skill: str) -> str:
    """'Ground Balls' / 'ground-balls' -> 'ground_balls'"""
    return raw_skill.strip().lower().replace(" ", "_").replace("-", "_")


def _has_at_most_one_decimal(number: float) -> bool:
    return abs(round(number, 1) - number) < 1e-9


def _parse_report(text: str, conn: sqlite3.Connection, today: date):
    """Validate every row. Returns (valid rows, matching player row or None, error messages)."""
    errors: list[str] = []
    valid_rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], None, ["File is empty."]
    reader.fieldnames = [header.strip().lower() for header in reader.fieldnames]
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
    if missing_columns:
        return [], None, [f"Missing column(s): {', '.join(missing_columns)}. Expected: {','.join(REQUIRED_COLUMNS)}"]

    scale_min, scale_max = CFG.scale_min, CFG.scale_max
    submit_codes: set[str] = set()
    seen_week_skills: set[tuple[str, str]] = set()
    for line_number, raw_row in enumerate(reader, start=2):  # line 1 is the header
        if all(not (value or "").strip() for value in raw_row.values() if isinstance(value, str)):
            continue
        code = (raw_row.get("submit_code") or "").strip().upper()
        week_text = (raw_row.get("week_start") or "").strip()
        skill = normalise_skill(raw_row.get("skill") or "")
        hours_text = (raw_row.get("hours") or "").strip()
        effort_text = (raw_row.get("effort") or "").strip()
        row_errors = []

        if not code:
            row_errors.append("submit_code is blank")
        submit_codes.add(code)

        week = None
        try:
            week = date.fromisoformat(week_text)
            if week.weekday() != 0:
                row_errors.append(f"week_start {week_text} is a {week.strftime('%A')}, must be a Monday")
            elif week > today:
                row_errors.append(f"week_start {week_text} is in the future")
        except ValueError:
            row_errors.append(f"week_start '{week_text}' is not a date (use YYYY-MM-DD)")

        if skill not in CFG.skills:
            row_errors.append(f"unknown skill '{raw_row.get('skill')}' (allowed: {', '.join(CFG.skills)})")

        hours = None
        try:
            hours = float(hours_text)
            if hours < 0 or hours > CFG.max_hours_per_skill_week:
                row_errors.append(f"hours {hours_text} outside 0–{CFG.max_hours_per_skill_week:g}")
            elif not _has_at_most_one_decimal(hours):
                row_errors.append(f"hours {hours_text} has more than one decimal place")
        except ValueError:
            row_errors.append(f"hours '{hours_text}' is not a number")

        effort = None
        if hours is not None and hours > 0:
            try:
                effort = float(effort_text)
                if not (scale_min <= effort <= scale_max):
                    row_errors.append(f"effort {effort_text} outside {scale_min:g}–{scale_max:g}")
                elif not _has_at_most_one_decimal(effort):
                    row_errors.append(f"effort {effort_text} has more than one decimal place")
            except ValueError:
                row_errors.append(f"effort is required when hours > 0 (got '{effort_text}')")

        if week and skill in CFG.skills:
            week_skill = (week.isoformat(), skill)
            if week_skill in seen_week_skills:
                row_errors.append(f"duplicate row for {skill} in week {week}")
            seen_week_skills.add(week_skill)

        if row_errors:
            errors.extend(f"Row {line_number}: {error}" for error in row_errors)
        else:
            valid_rows.append({"row": line_number, "week": week.isoformat(), "skill": skill,
                               "hours": round(hours, 1),
                               "effort": to_tenths(effort) if effort is not None else None})

    if not valid_rows and not errors:
        errors.append("File has a header but no data rows.")

    player = None
    submit_codes.discard("")
    if len(submit_codes) > 1:
        errors.append(f"File contains more than one submit_code ({', '.join(sorted(submit_codes))}). "
                      "One player per file.")
    elif len(submit_codes) == 1:
        code = next(iter(submit_codes))
        player = conn.execute("SELECT * FROM players WHERE submit_code = ?", (code,)).fetchone()
        if player is None:
            errors.append(f"submit_code '{code}' doesn't match any player.")
        elif not player["active"]:
            errors.append(f"submit_code '{code}' belongs to an inactive player.")
    return valid_rows, player, errors


def _log_ingest_file(conn, original_name, sha256, received_at, status, player_id=None, row_count=None,
                     weeks=None, message=None, stored_path=None) -> int:
    cursor = conn.execute(
        "INSERT INTO ingest_files (original_name, sha256, received_at, status, player_id, row_count, weeks,"
        " message, stored_path) VALUES (?,?,?,?,?,?,?,?,?)",
        (original_name, sha256, received_at, status, player_id, row_count, weeks, message, stored_path),
    )
    return cursor.lastrowid


def _store_copy(source: Path, destination_dir: Path, file_name: str, remove_source: bool) -> Path:
    """Copy `source` into `destination_dir` without overwriting anything there (adds _1, _2, ... if needed)."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / file_name
    suffix_number = 1
    while destination.exists():
        destination = destination_dir / f"{Path(file_name).stem}_{suffix_number}{Path(file_name).suffix}"
        suffix_number += 1
    shutil.copy2(source, destination)
    if remove_source:
        source.unlink(missing_ok=True)
    return destination


def ingest_file(conn: sqlite3.Connection, path: str | Path, received_at: str | None = None,
                move: bool = True, today: date | None = None) -> IngestResult:
    """Validate and import one CSV. `received_at` can be overridden (used by the demo generator)."""
    path = Path(path)
    file_bytes = path.read_bytes()
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    received = received_at or now_iso()
    today = today or (datetime.fromisoformat(received).date() if received_at else date.today())
    result = IngestResult(file=path.name, status="rejected")

    already_imported = conn.execute(
        "SELECT 1 FROM ingest_files WHERE sha256 = ? AND status = 'imported'", (sha256,)).fetchone()
    if already_imported:
        result.status = "duplicate"
        result.errors.append("This exact file was already imported.")
        with conn:
            stored = _store_copy(path, CFG.rejected_dir, path.name, move)
            _log_ingest_file(conn, path.name, sha256, received, "duplicate", message=result.errors[0],
                             stored_path=str(stored))
        return result

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
        result.errors.append("File isn't UTF-8 text. Save it as CSV (UTF-8).")

    rows, player = [], None
    if text is not None:
        rows, player, errors = _parse_report(text, conn, today)
        result.errors.extend(errors)

    if result.errors:
        with conn:
            stored = _store_copy(path, CFG.rejected_dir, path.name, move)
            stored.with_name(stored.name + ".errors.txt").write_text("\n".join(result.errors), encoding="utf-8")
            _log_ingest_file(conn, path.name, sha256, received, "rejected",
                             player_id=player["id"] if player else None, message="\n".join(result.errors),
                             stored_path=str(stored))
        return result

    weeks = sorted({row["week"] for row in rows})
    result.player = player["name"]
    result.weeks = weeks
    result.rows = len(rows)
    with conn:
        file_id = _log_ingest_file(conn, path.name, sha256, received, "imported", player_id=player["id"],
                                   row_count=len(rows), weeks=",".join(weeks))
        for week in weeks:
            previous = conn.execute(
                "SELECT id FROM submissions WHERE player_id=? AND week_start=? AND superseded_by IS NULL",
                (player["id"], week)).fetchone()
            submission_id = conn.execute(
                "INSERT INTO submissions (player_id, week_start, ingest_file_id, received_at) VALUES (?,?,?,?)",
                (player["id"], week, file_id, received)).lastrowid
            if previous:
                conn.execute("UPDATE submissions SET superseded_by=? WHERE id=?", (submission_id, previous["id"]))
                result.warnings.append(f"Week {week}: replaces an earlier report (kept in history).")

            rows_by_skill = {row["skill"]: row for row in rows if row["week"] == week}
            for skill in CFG.skills:
                row = rows_by_skill.get(skill)
                if row:
                    conn.execute(
                        "INSERT INTO training_reports (submission_id, skill, hours, effort, source_row)"
                        " VALUES (?,?,?,?,?)",
                        (submission_id, skill, row["hours"], row["effort"], row["row"]))
                else:
                    conn.execute(
                        "INSERT INTO training_reports (submission_id, skill, hours, effort, source_row, filled_missing)"
                        " VALUES (?,?,0,NULL,NULL,1)", (submission_id, skill))
            filled_skills = [skill for skill in CFG.skills if skill not in rows_by_skill]
            if filled_skills:
                result.warnings.append(f"Week {week}: no row for {', '.join(filled_skills)} — recorded as 0 hours.")

        stored = _store_copy(path, CFG.archive_dir, f"{sha256[:12]}_{path.name}", move)
        conn.execute("UPDATE ingest_files SET stored_path=?, message=? WHERE id=?",
                     (str(stored), "\n".join(result.warnings) or None, file_id))
    result.status = "imported"
    return result


def ingest_inbox(conn: sqlite3.Connection) -> list[IngestResult]:
    return [ingest_file(conn, csv_path) for csv_path in sorted(CFG.inbox_dir.glob("*.csv"))]


def template_csv(submit_code: str = "YOURCODE", week_start: str = "2026-10-05") -> str:
    """A blank weekly report with one 0-hour row per skill."""
    lines = [",".join(REQUIRED_COLUMNS)]
    lines += [f"{submit_code},{week_start},{skill},0," for skill in CFG.skills]
    return "\n".join(lines) + "\n"
