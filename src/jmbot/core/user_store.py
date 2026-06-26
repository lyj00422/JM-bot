from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FavoriteItem:
    user_id: int
    album_id: str
    created_at: str


@dataclass(frozen=True)
class FriendRequestRecord:
    id: int
    user_id: int
    flag: str
    comment: str | None
    status: str
    admin_user_id: int | None
    error: str | None
    created_at: str
    updated_at: str


class UserDataStore:
    """SQLite-backed user data store for favorites and friend requests."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def add_favorite(self, user_id: int, album_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO user_favorites (user_id, album_id)
                VALUES (?, ?)
                """,
                (user_id, album_id),
            )
            return cursor.rowcount == 1

    def remove_favorite(self, user_id: int, album_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM user_favorites WHERE user_id = ? AND album_id = ?",
                (user_id, album_id),
            )
            return cursor.rowcount == 1

    def list_favorites(self, user_id: int, *, limit: int = 20) -> list[FavoriteItem]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT user_id, album_id, created_at
                FROM user_favorites
                WHERE user_id = ?
                ORDER BY created_at DESC, album_id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [_favorite_from_row(row) for row in rows]

    def record_friend_request(self, *, user_id: int, flag: str, comment: str | None) -> FriendRequestRecord:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO friend_requests (user_id, flag, comment, status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(flag) DO UPDATE SET
                    user_id = excluded.user_id,
                    comment = excluded.comment,
                    status = 'pending',
                    admin_user_id = NULL,
                    error = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (user_id, flag, comment),
            )
            row = connection.execute("SELECT * FROM friend_requests WHERE flag = ?", (flag,)).fetchone()
        return _friend_request_from_row(row)

    def list_friend_requests(self, *, status: str | None = None, limit: int = 20) -> list[FriendRequestRecord]:
        query = "SELECT * FROM friend_requests"
        params: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_friend_request_from_row(row) for row in rows]

    def get_friend_request(self, record_id: int) -> FriendRequestRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM friend_requests WHERE id = ?", (record_id,)).fetchone()
        return None if row is None else _friend_request_from_row(row)

    def decide_friend_request(
        self,
        record_id: int,
        *,
        approve: bool,
        admin_user_id: int,
    ) -> FriendRequestRecord | None:
        status = "approved" if approve else "rejected"
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE friend_requests
                SET status = ?,
                    admin_user_id = ?,
                    error = NULL,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                  AND status = 'pending'
                """,
                (status, admin_user_id, record_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute("SELECT * FROM friend_requests WHERE id = ?", (record_id,)).fetchone()
        return _friend_request_from_row(row)

    def mark_friend_request_failed(self, record_id: int, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE friend_requests
                SET status = 'failed',
                    error = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (error, record_id),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_favorites (
                    user_id INTEGER NOT NULL,
                    album_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY (user_id, album_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS friend_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    flag TEXT NOT NULL UNIQUE,
                    comment TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    admin_user_id INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
                """
            )


def _favorite_from_row(row: sqlite3.Row) -> FavoriteItem:
    return FavoriteItem(
        user_id=int(row["user_id"]),
        album_id=str(row["album_id"]),
        created_at=str(row["created_at"]),
    )


def _friend_request_from_row(row: sqlite3.Row) -> FriendRequestRecord:
    return FriendRequestRecord(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        flag=str(row["flag"]),
        comment=None if row["comment"] is None else str(row["comment"]),
        status=str(row["status"]),
        admin_user_id=None if row["admin_user_id"] is None else int(row["admin_user_id"]),
        error=None if row["error"] is None else str(row["error"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
