from __future__ import annotations

import sqlite3
from pathlib import Path

from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


def test_repo_card_key_crud_and_flags(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "repo.db")

    card = repo.create_card("Main card")
    key = repo.add_key_phrase(card.id, "buy laptop")

    keys = repo.list_key_phrases(card.id)
    assert len(keys) == 1
    assert keys[0].phrase == "buy laptop"
    assert keys[0].search_enabled is True
    assert keys[0].maps_enabled is True

    repo.update_key_phrase_targets(key.id, search_enabled=False, maps_enabled=False)
    keys = repo.list_key_phrases(card.id)
    assert keys[0].search_enabled is False
    assert keys[0].maps_enabled is False

    repo.update_key_phrase(key.id, "best laptop")
    assert repo.list_key_phrases(card.id)[0].phrase == "best laptop"

    repo.delete_key_phrase(key.id)
    assert repo.list_key_phrases(card.id) == []


def test_repo_positions_and_settings(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "repo.db")

    card = repo.create_card("Card")
    key_a = repo.add_key_phrase(card.id, "alpha")
    key_b = repo.add_key_phrase(card.id, "beta")

    repo.upsert_position(key_a.id, "2026-04-01", "5")
    repo.upsert_position(key_b.id, "2026-04-01", "9")
    repo.upsert_position(key_a.id, "2026-04-02", "3")

    dates = repo.list_run_dates(card.id)
    assert dates == ["2026-04-02", "2026-04-01"]
    assert repo.positions_for_card_and_date(card.id, "2026-04-01") == {key_a.id: "5", key_b.id: "9"}

    repo.save_settings({"city": "Moscow", "street": "Tverskaya"})
    repo.save_settings({"street": "Arbat"})
    assert repo.get_settings() == {"city": "Moscow", "street": "Arbat"}

    repo.save_card_settings(card.id, {"city": "SPB", "search_transitions": "5"})
    repo.save_card_settings(card.id, {"city": "Kazan"})
    assert repo.get_card_settings(card.id) == {"city": "Kazan", "search_transitions": "5"}

    repo.update_card(card.id, "Card updated")
    assert repo.list_cards()[0].name == "Card updated"


def test_repo_delete_key_cascades_positions(tmp_path: Path) -> None:
    db_path = tmp_path / "repo.db"
    repo = SqliteRepository(db_path)
    card = repo.create_card("Card")
    key = repo.add_key_phrase(card.id, "alpha")
    repo.upsert_position(key.id, "2026-04-03", "11")

    repo.delete_key_phrase(key.id)

    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM key_phrase_positions").fetchone()[0]
    assert count == 0


def test_repo_delete_card_cascades_keys_positions_and_settings(tmp_path: Path) -> None:
    db_path = tmp_path / "repo.db"
    repo = SqliteRepository(db_path)
    card = repo.create_card("Card")
    key = repo.add_key_phrase(card.id, "alpha")
    repo.upsert_position(key.id, "2026-04-03", "11")
    repo.save_card_settings(card.id, {"city": "Moscow"})

    repo.delete_card(card.id)

    with sqlite3.connect(db_path) as connection:
        cards_count = connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        keys_count = connection.execute("SELECT COUNT(*) FROM key_phrases").fetchone()[0]
        positions_count = connection.execute("SELECT COUNT(*) FROM key_phrase_positions").fetchone()[0]
        card_settings_count = connection.execute("SELECT COUNT(*) FROM card_settings").fetchone()[0]

    assert cards_count == 0
    assert keys_count == 0
    assert positions_count == 0
    assert card_settings_count == 0
