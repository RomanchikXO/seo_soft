from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from shagteampro.domain.models import Card, KeyPhrase


class SqliteRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS key_phrases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id INTEGER NOT NULL,
                    phrase TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_key_phrases_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_key_phrases_card_id
                ON key_phrases(card_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS key_phrase_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_phrase_id INTEGER NOT NULL,
                    run_date TEXT NOT NULL,
                    position TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(key_phrase_id, run_date),
                    FOREIGN KEY(key_phrase_id) REFERENCES key_phrases(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_positions_run_date
                ON key_phrase_positions(run_date)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS card_settings (
                    card_id INTEGER NOT NULL,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    PRIMARY KEY (card_id, setting_key),
                    FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
                )
                """
            )

    def _ensure_key_phrases_columns(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA table_info(key_phrases)").fetchall()
        existing_columns = {row["name"] for row in rows}
        if "search_enabled" not in existing_columns:
            connection.execute(
                "ALTER TABLE key_phrases ADD COLUMN search_enabled INTEGER NOT NULL DEFAULT 0"
            )
        if "maps_enabled" not in existing_columns:
            connection.execute(
                "ALTER TABLE key_phrases ADD COLUMN maps_enabled INTEGER NOT NULL DEFAULT 0"
            )

    def list_cards(self) -> list[Card]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, created_at FROM cards ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [
            Card(
                id=row["id"],
                name=row["name"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def create_card(self, name: str) -> Card:
        created_at = datetime.now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO cards(name, created_at) VALUES (?, ?)",
                (name, created_at),
            )
            card_id = cursor.lastrowid
        return Card(id=card_id, name=name, created_at=datetime.fromisoformat(created_at))

    def update_card(self, card_id: int, name: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE cards SET name = ? WHERE id = ?",
                (name, card_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Карточка не найдена.")

    def delete_card(self, card_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM cards WHERE id = ?",
                (card_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Карточка не найдена.")

    def list_key_phrases(self, card_id: int) -> list[KeyPhrase]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, card_id, phrase, created_at, search_enabled, maps_enabled
                FROM key_phrases
                WHERE card_id = ?
                ORDER BY id ASC
                """,
                (card_id,),
            ).fetchall()
        return [
            KeyPhrase(
                id=row["id"],
                card_id=row["card_id"],
                phrase=row["phrase"],
                created_at=datetime.fromisoformat(row["created_at"]),
                search_enabled=bool(row["search_enabled"]),
                maps_enabled=bool(row["maps_enabled"]),
            )
            for row in rows
        ]

    def add_key_phrase(self, card_id: int, phrase: str) -> KeyPhrase:
        created_at = datetime.now().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO key_phrases(card_id, phrase, created_at, search_enabled, maps_enabled)
                VALUES (?, ?, ?, 1, 1)
                """,
                (card_id, phrase, created_at),
            )
            key_id = cursor.lastrowid
        return KeyPhrase(
            id=key_id,
            card_id=card_id,
            phrase=phrase,
            created_at=datetime.fromisoformat(created_at),
            search_enabled=True,
            maps_enabled=True,
        )

    def update_key_phrase(self, key_id: int, phrase: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE key_phrases SET phrase = ? WHERE id = ?",
                (phrase, key_id),
            )

    def delete_key_phrase(self, key_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM key_phrases WHERE id = ?", (key_id,))

    def bulk_add_key_phrases(self, card_id: int, phrases: list[str]) -> int:
        created_at = datetime.now().isoformat()
        rows_to_insert = [(card_id, phrase, created_at) for phrase in phrases]
        if not rows_to_insert:
            return 0
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO key_phrases(card_id, phrase, created_at, search_enabled, maps_enabled)
                VALUES (?, ?, ?, 1, 1)
                """,
                rows_to_insert,
            )
        return len(rows_to_insert)

    def update_key_phrase_targets(
        self, key_id: int, *, search_enabled: bool | None = None, maps_enabled: bool | None = None
    ) -> None:
        updates: list[str] = []
        values: list[int] = []
        if search_enabled is not None:
            updates.append("search_enabled = ?")
            values.append(int(search_enabled))
        if maps_enabled is not None:
            updates.append("maps_enabled = ?")
            values.append(int(maps_enabled))
        if not updates:
            return
        values.append(key_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE key_phrases SET {', '.join(updates)} WHERE id = ?",
                values,
            )

    def list_run_dates(self, card_id: int) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT p.run_date
                FROM key_phrase_positions p
                JOIN key_phrases k ON k.id = p.key_phrase_id
                WHERE k.card_id = ?
                ORDER BY p.run_date DESC
                """,
                (card_id,),
            ).fetchall()
        return [row["run_date"] for row in rows]

    def positions_for_card_and_date(self, card_id: int, run_date: str) -> dict[int, str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.key_phrase_id, p.position
                FROM key_phrase_positions p
                JOIN key_phrases k ON k.id = p.key_phrase_id
                WHERE k.card_id = ? AND p.run_date = ?
                """,
                (card_id, run_date),
            ).fetchall()
        return {row["key_phrase_id"]: row["position"] for row in rows}

    def positions_for_card(self, card_id: int) -> dict[str, dict[int, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT p.run_date, p.key_phrase_id, p.position
                FROM key_phrase_positions p
                JOIN key_phrases k ON k.id = p.key_phrase_id
                WHERE k.card_id = ?
                """,
                (card_id,),
            ).fetchall()
        grouped: dict[str, dict[int, str]] = {}
        for row in rows:
            run_date = row["run_date"]
            grouped.setdefault(run_date, {})[row["key_phrase_id"]] = row["position"]
        return grouped

    def upsert_position(self, key_phrase_id: int, run_date: str, position: str) -> None:
        created_at = datetime.now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO key_phrase_positions(key_phrase_id, run_date, position, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key_phrase_id, run_date)
                DO UPDATE SET position = excluded.position
                """,
                (key_phrase_id, run_date, position, created_at),
            )

    def get_settings(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT setting_key, setting_value FROM app_settings"
            ).fetchall()
        return {row["setting_key"]: row["setting_value"] for row in rows}

    def save_settings(self, settings: dict[str, str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO app_settings(setting_key, setting_value)
                VALUES (?, ?)
                ON CONFLICT(setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
                """,
                list(settings.items()),
            )

    def get_card_settings(self, card_id: int) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT setting_key, setting_value
                FROM card_settings
                WHERE card_id = ?
                """,
                (card_id,),
            ).fetchall()
        return {row["setting_key"]: row["setting_value"] for row in rows}

    def save_card_settings(self, card_id: int, settings: dict[str, str]) -> None:
        with self._connect() as connection:
            exists_row = connection.execute(
                "SELECT 1 FROM cards WHERE id = ?",
                (card_id,),
            ).fetchone()
            if exists_row is None:
                raise ValueError("Карточка не найдена.")
            connection.executemany(
                """
                INSERT INTO card_settings(card_id, setting_key, setting_value)
                VALUES (?, ?, ?)
                ON CONFLICT(card_id, setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
                """,
                [(card_id, key, value) for key, value in settings.items()],
            )
