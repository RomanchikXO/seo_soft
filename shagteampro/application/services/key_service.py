from __future__ import annotations

from shagteampro.domain.models import KeyPhrase
from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


class KeyService:
    def __init__(self, repository: SqliteRepository) -> None:
        self._repository = repository

    def list_for_card(self, card_id: int) -> list[KeyPhrase]:
        return self._repository.list_key_phrases(card_id)

    def add_phrase(self, card_id: int, phrase: str) -> KeyPhrase:
        normalized = phrase.strip()
        if not normalized:
            raise ValueError("Ключевая фраза не может быть пустой.")
        return self._repository.add_key_phrase(card_id, normalized)

    def update_phrase(self, key_id: int, phrase: str) -> None:
        normalized = phrase.strip()
        if not normalized:
            raise ValueError("Ключевая фраза не может быть пустой.")
        self._repository.update_key_phrase(key_id, normalized)

    def delete_phrase(self, key_id: int) -> None:
        self._repository.delete_key_phrase(key_id)

    def add_phrases_bulk(self, card_id: int, phrases: list[str]) -> int:
        normalized = [phrase.strip() for phrase in phrases if phrase and phrase.strip()]
        return self._repository.bulk_add_key_phrases(card_id, normalized)

    def list_run_dates(self, card_id: int) -> list[str]:
        return self._repository.list_run_dates(card_id)

    def positions_for_date(self, card_id: int, run_date: str) -> dict[int, str]:
        return self._repository.positions_for_card_and_date(card_id, run_date)

    def positions_for_card(self, card_id: int) -> dict[str, dict[int, str]]:
        return self._repository.positions_for_card(card_id)

    def set_search_enabled(self, key_id: int, enabled: bool) -> None:
        self._repository.update_key_phrase_targets(key_id, search_enabled=enabled)

    def set_maps_enabled(self, key_id: int, enabled: bool) -> None:
        self._repository.update_key_phrase_targets(key_id, maps_enabled=enabled)
