"""Player roster management. No accounts: each player gets a short random submit code
that must appear in their CSV. It's not authentication, just a guard against casually
submitting under a teammate's name."""
from __future__ import annotations

import secrets
import sqlite3

from .config import CFG
from .db import now_iso

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def new_code(conn: sqlite3.Connection, length: int = 4) -> str:
    while True:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if not conn.execute("SELECT 1 FROM players WHERE submit_code=?", (code,)).fetchone():
            return code


def add_player(conn: sqlite3.Connection, name: str, position: str, number: int | None = None,
               code: str | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Name is required.")
    if position not in CFG.positions:
        raise ValueError(f"Position must be one of: {', '.join(CFG.positions)}")
    with conn:
        return conn.execute(
            "INSERT INTO players (name, number, position, submit_code, created_at) VALUES (?,?,?,?,?)",
            (name, number, position, (code or new_code(conn)).upper(), now_iso())).lastrowid


def set_active(conn: sqlite3.Connection, player_id: int, active: bool) -> None:
    with conn:
        conn.execute("UPDATE players SET active=? WHERE id=?", (int(active), player_id))


def update_player(conn: sqlite3.Connection, player_id: int, name: str, number, position: str) -> None:
    if position not in CFG.positions:
        raise ValueError(f"Unknown position {position}")
    with conn:
        conn.execute("UPDATE players SET name=?, number=?, position=? WHERE id=?",
                     (name.strip(), number, position, player_id))


def regenerate_code(conn: sqlite3.Connection, player_id: int) -> str:
    code = new_code(conn)
    with conn:
        conn.execute("UPDATE players SET submit_code=? WHERE id=?", (code, player_id))
    return code
