from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    output_format: str = "pdf"
    chapters: tuple[int, ...] = ()
    stage: str = "queued"
    progress_current: int = 0
    progress_total: int = 0
    started_at: str | None = None


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

    def enqueue_group(
        self,
        album_id: str,
        *,
        group_id: int,
        user_id: int,
        output_format: str = "pdf",
        chapters: tuple[int, ...] = (),
    ) -> AlbumQueueJob:
        return self._enqueue(
            album_id,
            destination_type="group",
            group_id=group_id,
            user_id=user_id,
            output_format=output_format,
            chapters=chapters,
        )

    def enqueue_private(
        self,
        album_id: str,
        *,
        user_id: int,
        output_format: str = "pdf",
        chapters: tuple[int, ...] = (),
    ) -> AlbumQueueJob:
        return self._enqueue(
            album_id,
            destination_type="private",
            group_id=None,
            user_id=user_id,
            output_format=output_format,
            chapters=chapters,
        )

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
                    stage = 'starting',
                    progress_current = 0,
                    progress_total = 0,
                    started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
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

    def mark_done_if_processing(self, job_id: int) -> bool:
        """Atomically finish a processing job without overwriting cancellation."""

        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE album_queue
                SET status = 'done',
                    error = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status = 'processing'
                """,
                (job_id,),
            )
            return cursor.rowcount == 1

    def mark_failed(self, job_id: int, error: str) -> None:
        self._mark_finished(job_id, status="failed", error=error)

    def update_progress(
        self,
        job_id: int,
        *,
        stage: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE album_queue
                SET stage = ?,
                    progress_current = COALESCE(?, progress_current),
                    progress_total = COALESCE(?, progress_total),
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (stage, current, total, job_id),
            )

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

    def list_active_for_user(
        self,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> list[AlbumQueueJob]:
        with self._connection() as connection:
            where, params = self._context_filter(destination_type, group_id, user_id)
            rows = connection.execute(
                f"""
                SELECT *
                FROM album_queue
                WHERE {where}
                  AND status IN ('pending', 'processing')
                ORDER BY priority DESC, id
                """,
                tuple(params),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def list_history_for_user(
        self,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
        failed_only: bool = False,
        limit: int | None = None,
    ) -> list[AlbumQueueJob]:
        statuses = ("failed",) if failed_only else ("done", "failed", "canceled")
        placeholders = ", ".join("?" for _ in statuses)
        limit_sql = "" if limit is None else "LIMIT ?"
        query_params: list[str | int] = []
        with self._connection() as connection:
            where, params = self._context_filter(destination_type, group_id, user_id)
            query_params.extend([*params, *statuses])
            if limit is not None:
                query_params.append(limit)
            rows = connection.execute(
                f"""
                SELECT *
                FROM album_queue
                WHERE {where}
                  AND status IN ({placeholders})
                ORDER BY id DESC
                {limit_sql}
                """,
                tuple(query_params),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def find_latest_failed_for_user(
        self,
        album_id: str,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> AlbumQueueJob | None:
        with self._connection() as connection:
            where, params = self._context_filter(destination_type, group_id, user_id)
            row = connection.execute(
                f"""
                SELECT *
                FROM album_queue
                WHERE {where}
                  AND album_id = ?
                  AND status = 'failed'
                ORDER BY id DESC
                LIMIT 1
                """,
                tuple([*params, album_id]),
            ).fetchone()
        return None if row is None else _row_to_job(row)

    def list_failed_jobs(self, *, limit: int | None = None) -> list[AlbumQueueJob]:
        limit_sql = "" if limit is None else "LIMIT ?"
        params: tuple[int, ...] = () if limit is None else (limit,)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM album_queue
                WHERE status = 'failed'
                ORDER BY id DESC
                {limit_sql}
                """,
                params,
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def get_job(self, job_id: int) -> AlbumQueueJob | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM album_queue WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else _row_to_job(row)

    def delete_job(self, job_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM album_queue WHERE id = ?", (job_id,))
            return cursor.rowcount == 1

    def delete_failed_job(self, job_id: int) -> bool:
        """Delete one failed history record without touching active work."""
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM album_queue WHERE id = ? AND status = 'failed'", (job_id,)
            )
            return cursor.rowcount == 1

    def admin_cancel_job(self, job_id: int) -> bool:
        """Cancel one active job by id; processing jobs are cooperatively stopped."""
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE album_queue SET status='canceled', error='canceled by admin', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=? AND status IN ('pending','processing')",
                (job_id,),
            )
            return cursor.rowcount == 1

    def find_latest_active_for_user(
        self,
        *,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> AlbumQueueJob | None:
        with self._connection() as connection:
            where, params = self._context_filter(destination_type, group_id, user_id)
            row = connection.execute(
                f"""
                SELECT *
                FROM album_queue
                WHERE {where}
                  AND status IN ('pending', 'processing')
                ORDER BY id DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return None if row is None else _row_to_job(row)

    def prune_history(self, *, retention_days: int, max_per_user: int) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        if max_per_user < 1:
            raise ValueError("max_per_user must be at least 1")

        finished_statuses = ("done", "failed", "canceled")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%fZ")
        removed = 0
        with self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM album_queue
                WHERE status IN ('done', 'failed', 'canceled')
                  AND updated_at < ?
                """,
                (cutoff,),
            )
            removed += cursor.rowcount

            rows = connection.execute(
                """
                SELECT id, user_id
                FROM album_queue
                WHERE status IN ('done', 'failed', 'canceled')
                ORDER BY user_id, id DESC
                """
            ).fetchall()
            seen_by_user: dict[int, int] = {}
            delete_ids: list[int] = []
            for row in rows:
                user_id = int(row["user_id"])
                seen_by_user[user_id] = seen_by_user.get(user_id, 0) + 1
                if seen_by_user[user_id] > max_per_user:
                    delete_ids.append(int(row["id"]))

            if delete_ids:
                placeholders = ", ".join("?" for _ in delete_ids)
                cursor = connection.execute(
                    f"DELETE FROM album_queue WHERE id IN ({placeholders})",
                    tuple(delete_ids),
                )
                removed += cursor.rowcount

        return removed

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
            where, params = self._context_filter(destination_type, group_id, user_id)
            row = connection.execute(
                f"""
                SELECT *
                FROM album_queue
                WHERE {where}
                  AND album_id = ?
                  AND status IN ('pending', 'processing')
                ORDER BY priority DESC, id
                LIMIT 1
                """,
                tuple([*params, album_id]),
            ).fetchone()
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
        output_format: str,
        chapters: tuple[int, ...],
    ) -> AlbumQueueJob:
        chapters_text = _chapters_to_text(chapters)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = self._find_active_duplicate(
                connection,
                album_id,
                destination_type,
                group_id,
                user_id,
                output_format,
                chapters_text,
            )
            if duplicate is not None:
                raise DuplicateAlbumJobError(_row_to_job(duplicate))

            cursor = connection.execute(
                """
                INSERT INTO album_queue (
                    album_id, destination_type, group_id, user_id, output_format, chapters
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (album_id, destination_type, group_id, user_id, output_format, chapters_text),
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
                    stage = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (status, error, status, job_id),
            )

    def _find_active_duplicate(
        self,
        connection: sqlite3.Connection,
        album_id: str,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
        output_format: str = "pdf",
        chapters_text: str = "",
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
                  AND output_format = ?
                  AND chapters = ?
                  AND status IN ('pending', 'processing')
                ORDER BY priority DESC, id
                LIMIT 1
                """,
                (album_id, group_id, output_format, chapters_text),
            ).fetchone()

        return connection.execute(
            """
            SELECT *
            FROM album_queue
            WHERE album_id = ?
              AND destination_type = 'private'
              AND user_id = ?
              AND output_format = ?
              AND chapters = ?
              AND status IN ('pending', 'processing')
            ORDER BY priority DESC, id
            LIMIT 1
            """,
            (album_id, user_id, output_format, chapters_text),
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

    def _context_filter(
        self,
        destination_type: DestinationType,
        group_id: int | None,
        user_id: int,
    ) -> tuple[str, list[str | int]]:
        where = ["destination_type = ?", "user_id = ?"]
        params: list[str | int] = [destination_type, user_id]
        if destination_type == "group":
            if group_id is None:
                raise ValueError("group_id is required for group jobs")
            where.append("group_id = ?")
            params.append(group_id)
        elif destination_type == "private":
            where.append("group_id IS NULL")
        else:
            raise ValueError(f"Unsupported destination_type: {destination_type}")
        return " AND ".join(where), params

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
                    output_format TEXT NOT NULL DEFAULT 'pdf',
                    chapters TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'queued',
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
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
        if needs_rebuild:
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
                output_format TEXT NOT NULL DEFAULT 'pdf',
                chapters TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT 'queued',
                progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
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

        columns = [row["name"] for row in connection.execute("PRAGMA table_info(album_queue)").fetchall()]
        additions = {
            "priority": "INTEGER NOT NULL DEFAULT 0",
            "output_format": "TEXT NOT NULL DEFAULT 'pdf'",
            "chapters": "TEXT NOT NULL DEFAULT ''",
            "stage": "TEXT NOT NULL DEFAULT 'queued'",
            "progress_current": "INTEGER NOT NULL DEFAULT 0",
            "progress_total": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE album_queue ADD COLUMN {name} {definition}")


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
        output_format=str(row["output_format"]),
        chapters=_text_to_chapters(str(row["chapters"])),
        stage=str(row["stage"]),
        progress_current=int(row["progress_current"]),
        progress_total=int(row["progress_total"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
    )


def _chapters_to_text(chapters: tuple[int, ...]) -> str:
    return ",".join(str(chapter) for chapter in chapters)


def _text_to_chapters(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split(",") if part)
