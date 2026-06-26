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


class UserDataStore:
    """SQLite-backed user data store for favorites."""

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


def _favorite_from_row(row: sqlite3.Row) -> FavoriteItem:
    return FavoriteItem(
        user_id=int(row["user_id"]),
        album_id=str(row["album_id"]),
        created_at=str(row["created_at"]),
    )
