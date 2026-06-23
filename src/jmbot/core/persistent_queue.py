from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


DestinationType = Literal["group", "private"]
JobStatus = Literal["pending", "processing", "done", "failed", "canceled"]
ActiveJobStatus = Literal["pending", "processing"]


class DuplicateAlbumJobError(RuntimeError):
    """Raised when the same album is already active for the same target."""

    def __init__(self, existing_job: "AlbumQueueJob"):
        super().__init__(f"JM{existing_job.album_id} is already queued for this target")
        self.existing_job = existing_job


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
    priority: int
    error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CancelResult:
    """Result of a normal user's cancel request."""

    status: Literal["canceled", "processing", "missing"]
    job: AlbumQueueJob | None = None


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
                ORDER BY priority DESC, id
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

    def recover_interrupted_jobs(self) -> list[AlbumQueueJob]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM album_queue
                WHERE status = 'processing'
                ORDER BY priority DESC, id
                """
            ).fetchall()
            connection.execute(
                """
                UPDATE album_queue
                SET status = 'pending',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status = 'processing'
                """
            )
        return [_row_to_job(row) for row in rows]

    def mark_done(self, job_id: int) -> None:
        self._mark_finished(job_id, status="done", error=None)

    def mark_failed(self, job_id: int, error: str) -> None:
        self._mark_finished(job_id, status="failed", error=error)

    def count_ahead(self, job_id: int) -> int:
        with self._connection() as connection:
            job = connection.execute("SELECT id, priority FROM album_queue WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                return 0

            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM album_queue
                WHERE status IN ('pending', 'processing')
                  AND (
                    priority > ?
                    OR (priority = ? AND id < ?)
                  )
                """,
                (job["priority"], job["priority"], job_id),
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
        query += " ORDER BY priority DESC, id"

        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_job(row) for row in rows]

    def cancel_pending_for_user(
        self,
        album_id: str,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> CancelResult:
        """Cancel the caller's pending job, but leave processing jobs untouched."""

        with self._connection() as connection:
            row = self._find_user_job(connection, album_id, destination_type, group_id, user_id, ("pending",))
            if row is not None:
                connection.execute(
                    """
                    UPDATE album_queue
                    SET status = 'canceled',
                        error = 'canceled by user',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE id = ?
                    """,
                    (row["id"],),
                )
                updated = connection.execute("SELECT * FROM album_queue WHERE id = ?", (row["id"],)).fetchone()
                return CancelResult(status="canceled", job=_row_to_job(updated))

            row = self._find_user_job(connection, album_id, destination_type, group_id, user_id, ("processing",))
            if row is not None:
                return CancelResult(status="processing", job=_row_to_job(row))

        return CancelResult(status="missing")

    def clear_pending(self) -> int:
        """Cancel every pending job without touching the currently processing job."""

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE album_queue
                SET status = 'canceled',
                    error = 'cleared by admin',
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE status = 'pending'
                """
            )
            return cursor.rowcount

    def admin_cancel_album(
        self,
        album_id: str,
        *,
        destination_type: DestinationType | None = None,
        group_id: int | None = None,
        user_id: int | None = None,
    ) -> list[AlbumQueueJob]:
        """Cancel active jobs for an album globally or in one delivery target."""

        where = ["album_id = ?", "status IN ('pending', 'processing')"]
        params: list[str | int] = [album_id]
        if destination_type == "group":
            where.extend(["destination_type = 'group'", "group_id = ?"])
            if group_id is None:
                raise ValueError("group_id is required for group cancellation")
            params.append(group_id)
        elif destination_type == "private":
            where.extend(["destination_type = 'private'", "user_id = ?"])
            if user_id is None:
                raise ValueError("user_id is required for private cancellation")
            params.append(user_id)
        elif destination_type is not None:
            raise ValueError(f"Unsupported destination_type: {destination_type}")

        where_sql = " AND ".join(where)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM album_queue WHERE {where_sql} ORDER BY priority DESC, id", tuple(params)
            ).fetchall()
            if rows:
                connection.execute(
                    f"""
                    UPDATE album_queue
                    SET status = 'canceled',
                        error = 'canceled by admin',
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE {where_sql}
                    """,
                    tuple(params),
                )
        return [_row_to_job(row) for row in rows]

    def is_canceled(self, job_id: int) -> bool:
        with self._connection() as connection:
            row = connection.execute("SELECT status FROM album_queue WHERE id = ?", (job_id,)).fetchone()
        return row is not None and row["status"] == "canceled"

    def find_active_job(
        self,
        album_id: str,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> AlbumQueueJob | None:
        with self._connection() as connection:
            row = self._find_active_duplicate(connection, album_id, destination_type, group_id, user_id)
        return None if row is None else _row_to_job(row)

    def move_to_front(self, job_id: int) -> AlbumQueueJob:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM album_queue WHERE id = ? AND status IN ('pending', 'processing')", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Active job not found: {job_id}")

            max_row = connection.execute("SELECT COALESCE(MAX(priority), 0) AS max_priority FROM album_queue").fetchone()
            new_priority = int(max_row["max_priority"]) + 1
            connection.execute(
                """
                UPDATE album_queue
                SET priority = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (new_priority, job_id),
            )
            updated = connection.execute("SELECT * FROM album_queue WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(updated)

    def _enqueue(
        self,
        album_id: str,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> AlbumQueueJob:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = self._find_active_duplicate(connection, album_id, destination_type, group_id, user_id)
            if duplicate is not None:
                raise DuplicateAlbumJobError(_row_to_job(duplicate))

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

    def _find_active_duplicate(
        self,
        connection: sqlite3.Connection,
        album_id: str,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> sqlite3.Row | None:
        if destination_type == "group":
            if group_id is None:
                raise ValueError("group_id is required for group jobs")
            return connection.execute(
                """
                SELECT *
                FROM album_queue
                WHERE album_id = ?
                  AND destination_type = 'group'
                  AND group_id = ?
                  AND status IN ('pending', 'processing')
                ORDER BY priority DESC, id
                LIMIT 1
                """,
                (album_id, group_id),
            ).fetchone()

        return connection.execute(
            """
            SELECT *
            FROM album_queue
            WHERE album_id = ?
              AND destination_type = 'private'
              AND user_id = ?
              AND status IN ('pending', 'processing')
            ORDER BY priority DESC, id
            LIMIT 1
            """,
            (album_id, user_id),
        ).fetchone()

    def _find_user_job(
        self,
        connection: sqlite3.Connection,
        album_id: str,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
        statuses: tuple[ActiveJobStatus, ...],
    ) -> sqlite3.Row | None:
        placeholders = ", ".join("?" for _ in statuses)
        params: list[str | int] = [album_id, user_id, *statuses]
        where = [
            "album_id = ?",
            "user_id = ?",
            f"status IN ({placeholders})",
        ]
        if destination_type == "group":
            if group_id is None:
                raise ValueError("group_id is required for group jobs")
            where.extend(["destination_type = 'group'", "group_id = ?"])
            params.append(group_id)
        else:
            where.append("destination_type = 'private'")

        return connection.execute(
            f"""
            SELECT *
            FROM album_queue
            WHERE {" AND ".join(where)}
            ORDER BY priority DESC, id
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()

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
            self._migrate_schema(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS album_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id TEXT NOT NULL,
                    destination_type TEXT NOT NULL CHECK (destination_type IN ('group', 'private')),
                    group_id INTEGER,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'done', 'failed', 'canceled')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 0,
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
                ON album_queue (status, priority, id)
                """
            )

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'album_queue'"
        ).fetchone()
        if table is None:
            return

        columns = [row["name"] for row in connection.execute("PRAGMA table_info(album_queue)").fetchall()]
        status_sql = str(table["sql"])
        needs_rebuild = "canceled" not in status_sql
        if not needs_rebuild and "priority" not in columns:
            connection.execute("ALTER TABLE album_queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            return
        if not needs_rebuild:
            return

        connection.execute("ALTER TABLE album_queue RENAME TO album_queue_old")
        connection.execute(
            """
            CREATE TABLE album_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                album_id TEXT NOT NULL,
                destination_type TEXT NOT NULL CHECK (destination_type IN ('group', 'private')),
                group_id INTEGER,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'done', 'failed', 'canceled')),
                attempts INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 0,
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
        priority_expr = "priority" if "priority" in columns else "0"
        connection.execute(
            f"""
            INSERT INTO album_queue (
                id, album_id, destination_type, group_id, user_id, status, attempts,
                priority, error, created_at, updated_at
            )
            SELECT
                id, album_id, destination_type, group_id, user_id, status, attempts,
                {priority_expr}, error, created_at, updated_at
            FROM album_queue_old
            """
        )
        connection.execute("DROP TABLE album_queue_old")


def _row_to_job(row: sqlite3.Row) -> AlbumQueueJob:
    return AlbumQueueJob(
        id=int(row["id"]),
        album_id=str(row["album_id"]),
        destination_type=row["destination_type"],
        group_id=None if row["group_id"] is None else int(row["group_id"]),
        user_id=int(row["user_id"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        priority=int(row["priority"]),
        error=row["error"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
