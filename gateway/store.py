"""SQLite-backed job store. Small enough that synchronous access is fine."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

ACTIVE = ("queued", "running")
FINISHED = ("succeeded", "failed", "cancelled", "expired")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    prompt        TEXT NOT NULL,
    negative      TEXT NOT NULL DEFAULT '',
    width         INTEGER NOT NULL,
    height        INTEGER NOT NULL,
    steps         INTEGER NOT NULL,
    cfg           REAL NOT NULL,
    seed          INTEGER NOT NULL,
    status        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    expires_at    REAL,
    progress      REAL NOT NULL DEFAULT 0,
    step          INTEGER NOT NULL DEFAULT 0,
    total_steps   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    filename      TEXT,
    bytes         INTEGER,
    prompt_id     TEXT,
    source        TEXT NOT NULL DEFAULT 'web'
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at DESC);
"""


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["expires_in"] = (
        max(0, int(data["expires_at"] - time.time())) if data.get("expires_at") else None
    )
    return data


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writes ---------------------------------------------------------
    def create(self, job: dict[str, Any]) -> dict[str, Any]:
        cols = ", ".join(job)
        marks = ", ".join("?" for _ in job)
        with self._lock:
            self._db.execute(
                f"INSERT INTO jobs ({cols}) VALUES ({marks})", tuple(job.values())
            )
        return self.get(job["id"])  # type: ignore[return-value]

    def update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get(job_id)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(
                f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), job_id)
            )
        return self.get(job_id)

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically move the oldest queued job to running."""
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = time.time()
            self._db.execute(
                "UPDATE jobs SET status='running', started_at=?, progress=0 "
                "WHERE id=? AND status='queued'",
                (now, row["id"]),
            )
            return self.get(row["id"])

    def expire_due(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE status='succeeded' AND expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (now,),
            ).fetchall()
            jobs = [_row(r) for r in rows]  # type: ignore[misc]
            for job in jobs:
                self._db.execute(
                    "UPDATE jobs SET status='expired', filename=NULL, bytes=NULL WHERE id=?",
                    (job["id"],),
                )
            return jobs

    def purge_older_than(self, cutoff: float) -> int:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM jobs WHERE created_at < ? AND status IN "
                "('succeeded','failed','cancelled','expired')",
                (cutoff,),
            )
            return cur.rowcount

    # -- reads ----------------------------------------------------------
    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row(row)

    def list(self, limit: int = 24) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def active(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','running') "
                "ORDER BY created_at"
            ).fetchall()
        return [_row(r) for r in rows]  # type: ignore[misc]

    def queue_position(self, job_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='queued' AND created_at < "
                "(SELECT created_at FROM jobs WHERE id=?)",
                (job_id,),
            ).fetchone()
            running = self._db.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status='running'"
            ).fetchone()
        return int(row["n"]) + int(running["n"])

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS done, "
                "AVG(finished_at - started_at) AS avg_seconds "
                "FROM jobs WHERE status IN ('succeeded','expired') AND finished_at IS NOT NULL "
                "AND started_at IS NOT NULL"
            ).fetchone()
        return {
            "completed": int(row["done"] or 0),
            "avg_seconds": round(float(row["avg_seconds"] or 0), 1),
        }


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
