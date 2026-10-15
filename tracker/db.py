"""
SQLite schema and connection.

Traceability rules baked into the schema:
- Every training row points to its submission, which points to the exact imported file (sha256 + stored copy).
- Nothing is overwritten: resubmissions and corrected coach ratings insert new rows and mark the old
  one with `superseded_by`.
- Scores are stored as integer tenths (7.5 -> 75) so the 1.0-10.0 scale is enforced by the database.

Version history
---------------
1.0.0  2026-10-13  Initial release.
1.1.0  2026-10-15  Readability pass: descriptive names, file header.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import CFG

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    number       INTEGER,
    position     TEXT NOT NULL,
    submit_code  TEXT NOT NULL UNIQUE,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_files (
    id            INTEGER PRIMARY KEY,
    original_name TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('imported', 'rejected', 'duplicate')),
    player_id     INTEGER REFERENCES players(id),
    row_count     INTEGER,
    weeks         TEXT,
    message       TEXT,
    stored_path   TEXT
);

CREATE TABLE IF NOT EXISTS submissions (
    id             INTEGER PRIMARY KEY,
    player_id      INTEGER NOT NULL REFERENCES players(id),
    week_start     TEXT NOT NULL,
    ingest_file_id INTEGER NOT NULL REFERENCES ingest_files(id),
    received_at    TEXT NOT NULL,
    superseded_by  INTEGER REFERENCES submissions(id)
);

CREATE TABLE IF NOT EXISTS training_reports (
    id             INTEGER PRIMARY KEY,
    submission_id  INTEGER NOT NULL REFERENCES submissions(id),
    skill          TEXT NOT NULL,
    hours          REAL NOT NULL CHECK (hours >= 0 AND hours <= 168),
    effort         INTEGER CHECK (effort IS NULL OR effort BETWEEN 10 AND 100),
    source_row     INTEGER,
    filled_missing INTEGER NOT NULL DEFAULT 0,
    UNIQUE (submission_id, skill)
);

CREATE TABLE IF NOT EXISTS coach_ratings (
    id            INTEGER PRIMARY KEY,
    week_start    TEXT NOT NULL,
    player_id     INTEGER NOT NULL REFERENCES players(id),
    coach         TEXT NOT NULL,
    skill         TEXT NOT NULL,
    competency    INTEGER CHECK (competency IS NULL OR competency BETWEEN 10 AND 100),
    effort        INTEGER CHECK (effort IS NULL OR effort BETWEEN 10 AND 100),
    created_at    TEXT NOT NULL,
    superseded_by INTEGER REFERENCES coach_ratings(id)
);

CREATE TABLE IF NOT EXISTS coach_traits (
    id            INTEGER PRIMARY KEY,
    week_start    TEXT NOT NULL,
    player_id     INTEGER NOT NULL REFERENCES players(id),
    coach         TEXT NOT NULL,
    trait         TEXT NOT NULL,
    score         INTEGER CHECK (score IS NULL OR score BETWEEN 10 AND 100),
    created_at    TEXT NOT NULL,
    superseded_by INTEGER REFERENCES coach_traits(id)
);

CREATE TABLE IF NOT EXISTS flag_dismissals (
    fingerprint  TEXT PRIMARY KEY,
    player_id    INTEGER REFERENCES players(id),
    rule         TEXT NOT NULL,
    note         TEXT,
    dismissed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sub_player_week ON submissions(player_id, week_start);
CREATE INDEX IF NOT EXISTS idx_cr_week ON coach_ratings(week_start, player_id);
CREATE INDEX IF NOT EXISTS idx_ct_week ON coach_traits(week_start, player_id);
"""


def connect(path: str | Path | None = None, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database (creating it and the data folders if needed) and apply the schema."""
    db_path = Path(path or CFG.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for folder in (CFG.inbox_dir, CFG.archive_dir, CFG.rejected_dir):
        folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def to_tenths(value) -> int | None:
    """7.5 -> 75. Blank, non-numeric and NaN values become None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return int(round(number * 10))
