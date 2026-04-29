from __future__ import annotations

from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


class SettingsService:
    def __init__(self, repository: SqliteRepository) -> None:
        self._repository = repository

    def load_settings(self) -> dict[str, str]:
        return self._repository.get_settings()

    def save_settings(self, settings: dict[str, object]) -> None:
        normalized = {key: self._normalize_value(value) for key, value in settings.items()}
        self._repository.save_settings(normalized)

    def load_card_settings(self, card_id: int) -> dict[str, str]:
        return self._repository.get_card_settings(card_id)

    def save_card_settings(self, card_id: int, settings: dict[str, object]) -> None:
        normalized = {key: self._normalize_value(value) for key, value in settings.items()}
        self._repository.save_card_settings(card_id, normalized)

    @staticmethod
    def _normalize_value(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value).strip()
