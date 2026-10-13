"""Weekly player report import.

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


def normalise_skill(raw: str) -> str:
    return raw.strip().lower().replace(" ", "_").replace("-", "_")


def _one_decimal(x: float) -> bool:
    return abs(round(x, 1) - x) < 1e-9


def _parse(text: str, conn: sqlite3.Connection, today: date):
    errors: list[str] = []
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], None, ["File is empty."]
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    missing = [c for c in REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        return [], None, [f"Missing column(s): {', '.join(missing)}. Expected: {','.join(REQUIRED_COLUMNS)}"]

    lo, hi = CFG.scale_min, CFG.scale_max
    codes: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for line_no, raw in enumerate(reader, start=2):
        if all(not (v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        code = (raw.get("submit_code") or "").strip().upper()
        week_s = (raw.get("week_start") or "").strip()
        skill = normalise_skill(raw.get("skill") or "")
        hours_s = (raw.get("hours") or "").strip()
        effort_s = (raw.get("effort") or "").strip()
        row_err = []

        if not code:
            row_err.append("submit_code is blank")
        codes.add(code)

        week = None
        try:
            week = date.fromisoformat(week_s)
            if week.weekday() != 0:
                row_err.append(f"week_start {week_s} is a {week.strftime('%A')}, must be a Monday")
            elif week > today:
                row_err.append(f"week_start {week_s} is in the future")
        except ValueError:
            row_err.append(f"week_start '{week_s}' is not a date (use YYYY-MM-DD)")

        if skill not in CFG.skills:
            row_err.append(f"unknown skill '{raw.get('skill')}' (allowed: {', '.join(CFG.skills)})")

        hours = None
        try:
            hours = float(hours_s)
            if hours < 0 or hours > CFG.max_hours_per_skill_week:
                row_err.append(f"hours {hours_s} outside 0–{CFG.max_hours_per_skill_week:g}")
            elif not _one_decimal(hours):
                row_err.append(f"hours {hours_s} has more than one decimal place")
        except ValueError:
            row_err.append(f"hours '{hours_s}' is not a number")

        effort = None
        if hours is not None and hours > 0:
            try:
                effort = float(effort_s)
                if not (lo <= effort <= hi):
                    row_err.append(f"effort {effort_s} outside {lo:g}–{hi:g}")
                elif not _one_decimal(effort):
                    row_err.append(f"effort {effort_s} has more than one decimal place")
            except ValueError:
                row_err.append(f"effort is required when hours > 0 (got '{effort_s}')")

        if week and skill in CFG.skills:
            key = (week.isoformat(), skill)
            if key in seen:
                row_err.append(f"duplicate row for {skill} in week {week}")
            seen.add(key)

        if row_err:
            errors.extend(f"Row {line_no}: {e}" for e in row_err)
        else:
            rows.append({"row": line_no, "week": week.isoformat(), "skill": skill,
                         "hours": round(hours, 1), "effort": to_tenths(effort) if effort is not None else None})

    if not rows and not errors:
        errors.append("File has a header but no data rows.")

    player = None
    codes.discard("")
    if len(codes) > 1:
        errors.append(f"File contains more than one submit_code ({', '.join(sorted(codes))}). One player per file.")
    elif len(codes) == 1:
        code = next(iter(codes))
        player = conn.execute("SELECT * FROM players WHERE submit_code = ?", (code,)).fetchone()
        if player is None:
            errors.append(f"submit_code '{code}' doesn't match any player.")
        elif not player["active"]:
            errors.append(f"submit_code '{code}' belongs to an inactive player.")
    return rows, player, errors


def _log(conn, name, sha, received, status, player_id=None, row_count=None, weeks=None, message=None, stored=None) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_files (original_name, sha256, received_at, status, player_id, row_count, weeks, message, stored_path)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (name, sha, received, status, player_id, row_count, weeks, message, stored),
    )
    return cur.lastrowid


def _move(src: Path, dest_dir: Path, name: str, move: bool) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    i = 1
    while dest.exists():
        dest = dest_dir / f"{Path(name).stem}_{i}{Path(name).suffix}"
        i += 1
    shutil.copy2(src, dest)
    if move:
        src.unlink(missing_ok=True)
    return dest


def ingest_file(conn: sqlite3.Connection, path: str | Path, received_at: str | None = None,
                move: bool = True, today: date | None = None) -> IngestResult:
    """Validate and import one CSV. `received_at` can be overridden (used by the demo generator)."""
    path = Path(path)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    received = received_at or now_iso()
    today = today or (datetime.fromisoformat(received).date() if received_at else date.today())
    result = IngestResult(file=path.name, status="rejected")

    if conn.execute("SELECT 1 FROM ingest_files WHERE sha256 = ? AND status = 'imported'", (sha,)).fetchone():
        result.status = "duplicate"
        result.errors.append("This exact file was already imported.")
        with conn:
            stored = _move(path, CFG.rejected, path.name, move)
            _log(conn, path.name, sha, received, "duplicate", message=result.errors[0], stored=str(stored))
        return result

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
        result.errors.append("File isn't UTF-8 text. Save it as CSV (UTF-8).")

    rows, player = [], None
    if text is not None:
        rows, player, errors = _parse(text, conn, today)
        result.errors.extend(errors)

    if result.errors:
        with conn:
            stored = _move(path, CFG.rejected, path.name, move)
            stored.with_name(stored.name + ".errors.txt").write_text("\n".join(result.errors), encoding="utf-8")
            _log(conn, path.name, sha, received, "rejected",
                 player_id=player["id"] if player else None, message="\n".join(result.errors), stored=str(stored))
        return result

    weeks = sorted({r["week"] for r in rows})
    result.player = player["name"]
    result.weeks = weeks
    result.rows = len(rows)
    with conn:
        file_id = _log(conn, path.name, sha, received, "imported", player_id=player["id"],
                       row_count=len(rows), weeks=",".join(weeks))
        for week in weeks:
            prev = conn.execute(
                "SELECT id FROM submissions WHERE player_id=? AND week_start=? AND superseded_by IS NULL",
                (player["id"], week)).fetchone()
            sub_id = conn.execute(
                "INSERT INTO submissions (player_id, week_start, ingest_file_id, received_at) VALUES (?,?,?,?)",
                (player["id"], week, file_id, received)).lastrowid
            if prev:
                conn.execute("UPDATE submissions SET superseded_by=? WHERE id=?", (sub_id, prev["id"]))
                result.warnings.append(f"Week {week}: replaces an earlier report (kept in history).")
            week_rows = {r["skill"]: r for r in rows if r["week"] == week}
            for skill in CFG.skills:
                r = week_rows.get(skill)
                if r:
                    conn.execute(
                        "INSERT INTO training_reports (submission_id, skill, hours, effort, source_row) VALUES (?,?,?,?,?)",
                        (sub_id, skill, r["hours"], r["effort"], r["row"]))
                else:
                    conn.execute(
                        "INSERT INTO training_reports (submission_id, skill, hours, effort, source_row, filled_missing)"
                        " VALUES (?,?,0,NULL,NULL,1)", (sub_id, skill))
            filled = [s for s in CFG.skills if s not in week_rows]
            if filled:
                result.warnings.append(f"Week {week}: no row for {', '.join(filled)} — recorded as 0 hours.")
        stored = _move(path, CFG.archive, f"{sha[:12]}_{path.name}", move)
        conn.execute("UPDATE ingest_files SET stored_path=?, message=? WHERE id=?",
                     (str(stored), "\n".join(result.warnings) or None, file_id))
    result.status = "imported"
    return result


def ingest_inbox(conn: sqlite3.Connection) -> list[IngestResult]:
    return [ingest_file(conn, p) for p in sorted(CFG.inbox.glob("*.csv"))]


def template_csv(submit_code: str = "YOURCODE", week_start: str = "2026-10-05") -> str:
    lines = [",".join(REQUIRED_COLUMNS)]
    lines += [f"{submit_code},{week_start},{s},0," for s in CFG.skills]
    return "\n".join(lines) + "\n"
