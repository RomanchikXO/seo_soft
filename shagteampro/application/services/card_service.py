from __future__ import annotations

import sqlite3

from shagteampro.domain.models import Card
from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


class CardService:
    def __init__(self, repository: SqliteRepository) -> None:
        self._repository = repository

    def list_cards(self) -> list[Card]:
        return self._repository.list_cards()

    def create_card(self, name: str) -> Card:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Название карточки не может быть пустым.")

        try:
            return self._repository.create_card(cleaned_name)
        except sqlite3.IntegrityError as error:
            raise ValueError("Карточка с таким названием уже существует.") from error

    def update_card(self, card_id: int, name: str) -> None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Название карточки не может быть пустым.")
        try:
            self._repository.update_card(card_id, cleaned_name)
        except sqlite3.IntegrityError as error:
            raise ValueError("Карточка с таким названием уже существует.") from error

    def delete_card(self, card_id: int) -> None:
        self._repository.delete_card(card_id)
