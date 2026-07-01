"""Local-first SQLite store for Job Copilot.

Replaces the old Supabase dependency. Everything (jobs, tracker status,
profile, resume text, and API keys) lives in a single gitignored SQLite
file so the whole app runs offline-first with zero external services.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "copilot.db"

# Tracker columns shown on the Kanban board (in order).
TRACKER_STATUSES = ["saved", "applied", "interview", "offer"]
# All statuses a job row may hold.
ALL_STATUSES = ["new", *TRACKER_STATUSES, "dismissed"]

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    conn = _connect()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                title           TEXT,
                company         TEXT,
                location        TEXT,
                description     TEXT,
                apply_link      TEXT,
                posted          TEXT,
                salary_min      REAL,
                salary_max      REAL,
                employment_type TEXT,
                score           INTEGER DEFAULT 0,
                match_reasons   TEXT DEFAULT '',
                key_matches     TEXT DEFAULT '[]',
                red_flags       TEXT DEFAULT '',
                status          TEXT DEFAULT 'new',
                fetched_at      TEXT DEFAULT (datetime('now')),
                query           TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.commit()


# ── Settings (API keys + config, all local) ─────────────────────────────────
def get_setting(key: str, default: str = "") -> str:
    conn = _connect()
    with _lock:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = _connect()
    with _lock:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def get_settings(keys: Iterable[str]) -> dict[str, str]:
    return {k: get_setting(k) for k in keys}


# ── Jobs ─────────────────────────────────────────────────────────────────────
def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    try:
        job["key_matches"] = json.loads(job.get("key_matches") or "[]")
    except (json.JSONDecodeError, TypeError):
        job["key_matches"] = []
    return job


def get_seen_ids() -> set[str]:
    conn = _connect()
    with _lock:
        rows = conn.execute("SELECT id FROM jobs").fetchall()
    return {r["id"] for r in rows}


def upsert_job(job: dict[str, Any]) -> None:
    conn = _connect()
    with _lock:
        conn.execute(
            """
            INSERT INTO jobs (id, title, company, location, description, apply_link,
                              posted, salary_min, salary_max, employment_type,
                              score, match_reasons, key_matches, red_flags, query)
            VALUES (:id, :title, :company, :location, :description, :apply_link,
                    :posted, :salary_min, :salary_max, :employment_type,
                    :score, :match_reasons, :key_matches, :red_flags, :query)
            ON CONFLICT(id) DO UPDATE SET
                score         = excluded.score,
                match_reasons = excluded.match_reasons,
                key_matches   = excluded.key_matches,
                red_flags     = excluded.red_flags
            """,
            {
                **job,
                "key_matches": json.dumps(job.get("key_matches", [])),
            },
        )
        conn.commit()


def list_jobs(
    min_score: int = 0,
    search: str = "",
    statuses: Iterable[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = _connect()
    clauses = ["score >= ?"]
    params: list[Any] = [min_score]

    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)

    if search:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(description) LIKE ?)")
        like = f"%{search.lower()}%"
        params.extend([like, like, like])

    where = " AND ".join(clauses)
    params.append(limit)
    with _lock:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY score DESC, fetched_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def set_job_status(job_id: str, status: str) -> bool:
    if status not in ALL_STATUSES:
        return False
    conn = _connect()
    with _lock:
        cur = conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()
    return cur.rowcount > 0


def tracker_board() -> dict[str, list[dict[str, Any]]]:
    board = {s: [] for s in TRACKER_STATUSES}
    for job in list_jobs(statuses=TRACKER_STATUSES, limit=500):
        board[job["status"]].append(job)
    return board


def counts() -> dict[str, int]:
    conn = _connect()
    with _lock:
        rows = conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
    result = {s: 0 for s in ALL_STATUSES}
    for r in rows:
        result[r["status"]] = r["c"]
    return result
