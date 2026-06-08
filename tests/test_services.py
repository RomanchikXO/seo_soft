from __future__ import annotations

from pathlib import Path

import pytest

from shagteampro.application.services.card_service import CardService
from shagteampro.application.services.key_service import KeyService
from shagteampro.application.services.search_runner_service import SearchRunnerService
from shagteampro.application.services.settings_service import SettingsService
from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


def test_card_service_validation_and_duplicate(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    service = CardService(repo)

    with pytest.raises(ValueError):
        service.create_card("   ")

    service.create_card("Card A")
    with pytest.raises(ValueError):
        service.create_card("Card A")

    card = service.create_card("Card B")
    service.update_card(card.id, "Card C")
    assert service.list_cards()[1].name == "Card C"
    service.delete_card(card.id)
    assert [item.name for item in service.list_cards()] == ["Card A"]


def test_key_service_validation_and_targets(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    cards = CardService(repo)
    keys = KeyService(repo)
    card = cards.create_card("Card")

    with pytest.raises(ValueError):
        keys.add_phrase(card.id, "  ")

    key = keys.add_phrase(card.id, "query")
    keys.set_search_enabled(key.id, True)
    keys.set_maps_enabled(key.id, True)
    stored = keys.list_for_card(card.id)[0]
    assert stored.search_enabled is True
    assert stored.maps_enabled is True

    keys.update_phrase(key.id, "query updated")
    assert keys.list_for_card(card.id)[0].phrase == "query updated"


def test_settings_service_normalizes_values(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    card = CardService(repo).create_card("Card")
    service = SettingsService(repo)
    service.save_settings({"city": "  SPB  ", "street": " Nevsky "})
    assert service.load_settings() == {"city": "SPB", "street": "Nevsky"}
    service.save_card_settings(card.id, {"city": "  Moscow  ", "allow_target_events": True, "click_website": 4})
    assert service.load_card_settings(card.id) == {
        "city": "Moscow",
        "allow_target_events": "1",
        "click_website": "4",
    }


def test_search_runner_build_query() -> None:
    assert SearchRunnerService._build_query("alpha", "Moscow", "Arbat") == "alpha Moscow Arbat"
    assert SearchRunnerService._build_query("alpha", "", "Arbat") == "alpha Arbat"


def test_search_runner_build_maps_url_with_trimmed_coordinates() -> None:
    maps_url = SearchRunnerService._build_maps_url({"coordinates": " 37.631182, 55.771363 "})
    assert maps_url == "https://yandex.ru/maps/?ll=37.631182,55.771363&z=17&lang=ru_RU"

    fallback_url = SearchRunnerService._build_maps_url({"coordinates": ""})
    assert fallback_url == "https://yandex.ru/maps/?lang=ru_RU"


def test_search_runner_retries_after_missing_browser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = SearchRunnerService()
    install_calls: list[Path] = []
    launch_calls: list[str] = []

    def fake_launch(_playwright):
        launch_calls.append("launch")
        if len(launch_calls) == 1:
            raise RuntimeError("Executable doesn't exist")
        return "browser-object"

    def fake_install(path: Path) -> None:
        install_calls.append(path)

    monkeypatch.setattr(service, "_launch_human_like_browser", fake_launch)
    monkeypatch.setattr(service, "_install_chromium", fake_install)
    browser = service._launch_chromium_with_recovery(object(), tmp_path / "pw")
    assert browser == "browser-object"
    assert install_calls == [tmp_path / "pw"]


def test_search_runner_ensure_chromium_installed_skips_if_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = SearchRunnerService()
    browsers_path = tmp_path / "pw"
    executable = tmp_path / "chromium"
    executable.write_text("bin", encoding="utf-8")
    install_calls: list[Path] = []

    monkeypatch.setattr(service, "_runtime_browsers_path", lambda: browsers_path)
    monkeypatch.setattr(service, "_chromium_executable_path", lambda: executable)
    monkeypatch.setattr(service, "_install_chromium", lambda path: install_calls.append(path))

    installed = service.ensure_chromium_installed()
    assert installed is False
    assert install_calls == []


def test_search_runner_ensure_chromium_installed_installs_if_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = SearchRunnerService()
    browsers_path = tmp_path / "pw"
    install_calls: list[Path] = []

    monkeypatch.setattr(service, "_runtime_browsers_path", lambda: browsers_path)
    monkeypatch.setattr(service, "_chromium_executable_path", lambda: None)
    monkeypatch.setattr(service, "_install_chromium", lambda path: install_calls.append(path))

    installed = service.ensure_chromium_installed()
    assert installed is True
    assert install_calls == [browsers_path]


def test_search_runner_optimization_runs_search_and_maps_for_same_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SearchRunnerService()
    monkeypatch.setattr(service, "ensure_chromium_installed", lambda: False)
    search_calls: list[int] = []
    maps_calls: list[int] = []

    def fake_search(key_payload: dict[str, object], card_payload: dict[str, object]) -> bool:
        search_calls.append(int(key_payload["id"]))
        return True

    def fake_maps(key_payload: dict[str, object], card_payload: dict[str, object]) -> bool:
        maps_calls.append(int(key_payload["id"]))
        return True

    monkeypatch.setattr(service, "_simulate_search_action", fake_search)
    monkeypatch.setattr(service, "_simulate_browser_action_one_second", fake_maps)

    result = service.run_cards_optimization(
        cards=[
            {
                "card_id": 1,
                "card_name": "Card",
                "search_target": 2,
                "maps_target": 3,
                "keys": [
                    {"id": 10, "search_enabled": True, "maps_enabled": True},
                ],
            }
        ],
        threads=4,
    )

    assert len(search_calls) == 2
    assert len(maps_calls) == 3
    assert set(search_calls) == {10}
    assert set(maps_calls) == {10}
    assert result["processed_cards"] == 1
    assert result["total_search_performed"] == 2
    assert result["total_maps_performed"] == 3
    assert result["cards"][0]["search_effect_keys"] == [10]
    assert result["cards"][0]["maps_effect_keys"] == [10]
