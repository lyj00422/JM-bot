from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


DestinationType = Literal["group", "private"]
JobStatus = Literal["pending", "processing", "done", "failed"]


@dataclass(frozen=True)
class AlbumQueueJob:
    """A persisted JM album request and its QQ delivery target."""

    id: int
    album_id: str
    destination_type: DestinationType
    group_id: int | None
    user_id: int
    status: JobStatus
    attempts: int
    error: str | None
    created_at: str
    updated_at: str


class PersistentAlbumQueue:
    """SQLite-backed FIFO queue for JM album jobs."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue_group(self, album_id: str, *, group_id: int, user_id: int) -> AlbumQueueJob:
        return self._enqueue(album_id, destination_type="group", group_id=group_id, user_id=user_id)

    def enqueue_private(self, album_id: str, *, user_id: int) -> AlbumQueueJob:
        return self._enqueue(album_id, destination_type="private", group_id=None, user_id=user_id)

    def claim_next(self) -> AlbumQueueJob | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM album_queue
                WHERE status = 'pending'
                ORDER BY id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None

            connection.execute(
                """
                UPDATE album_queue
                SET status = 'processing',
                    attempts = attempts + 1,
                    error = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (row["id"],),
            )
            updated = connection.execute("SELECT * FROM album_queue WHERE id = ?", (row["id"],)).fetchone()

        return _row_to_job(updated)

    def recover_interrupted_jobs(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE album_queue
                SET status = 'pending',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status = 'processing'
                """
            )
            return cursor.rowcount

    def mark_done(self, job_id: int) -> None:
        self._mark_finished(job_id, status="done", error=None)

    def mark_failed(self, job_id: int, error: str) -> None:
        self._mark_finished(job_id, status="failed", error=error)

    def count_ahead(self, job_id: int) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM album_queue
                WHERE id < ?
                  AND status IN ('pending', 'processing')
                """,
                (job_id,),
            ).fetchone()
        return int(row["total"])

    def pending_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM album_queue WHERE status IN ('pending', 'processing')"
            ).fetchone()
        return int(row["total"])

    def list_jobs(self, *, status: JobStatus | None = None) -> list[AlbumQueueJob]:
        query = "SELECT * FROM album_queue"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id"

        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_job(row) for row in rows]

    def _enqueue(
        self,
        album_id: str,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> AlbumQueueJob:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO album_queue (album_id, destination_type, group_id, user_id)
                VALUES (?, ?, ?, ?)
                """,
                (album_id, destination_type, group_id, user_id),
            )
            row = connection.execute("SELECT * FROM album_queue WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row_to_job(row)

    def _mark_finished(self, job_id: int, *, status: Literal["done", "failed"], error: str | None) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE album_queue
                SET status = ?,
                    error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (status, error, job_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS album_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id TEXT NOT NULL,
                    destination_type TEXT NOT NULL CHECK (destination_type IN ('group', 'private')),
                    group_id INTEGER,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'done', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    CHECK (
                        (destination_type = 'group' AND group_id IS NOT NULL)
                        OR (destination_type = 'private' AND group_id IS NULL)
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_album_queue_pending_order
                ON album_queue (status, id)
                """
            )


def _row_to_job(row: sqlite3.Row) -> AlbumQueueJob:
    return AlbumQueueJob(
        id=int(row["id"]),
        album_id=str(row["album_id"]),
        destination_type=row["destination_type"],
        group_id=None if row["group_id"] is None else int(row["group_id"]),
        user_id=int(row["user_id"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        error=row["error"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
