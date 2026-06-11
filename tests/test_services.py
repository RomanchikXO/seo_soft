from __future__ import annotations

from pathlib import Path

import pytest

from shagteampro.application.services.card_service import CardService
from shagteampro.application.services.key_service import KeyService
from shagteampro.application.services.notification_service import NotificationService
from shagteampro.application.services.search_runner_service import SearchRunnerService
from shagteampro.application.services.settings_service import SettingsService
from shagteampro.application.services.yandex_organization_service import YandexOrganizationService
from shagteampro.infrastructure.parsers.yandex_organization_parser import YandexOrganizationParser
from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


class _FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str]] = []

    def send_message(self, token: str, chat_id: str, text: str, proxy: str = "") -> bool:
        self.sent.append((token, chat_id, text, proxy))
        return True


def _optimization_summary() -> dict[str, object]:
    return {
        "processed_cards": 2,
        "total_search_target": 5,
        "total_search_performed": 4,
        "total_maps_target": 3,
        "total_maps_performed": 1,
        "cards": [
            {
                "card_id": 1,
                "card_name": "Card A",
                "organization": "Кофейня",
                "search_target": 3,
                "search_performed": 3,
                "maps_target": 1,
                "maps_performed": 0,
            },
            {
                "card_id": 2,
                "card_name": "Card B",
                "organization": "",
                "search_target": 2,
                "search_performed": 1,
                "maps_target": 2,
                "maps_performed": 1,
            },
        ],
    }


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


def test_notification_message_contains_statistics() -> None:
    text = NotificationService.build_optimization_message(_optimization_summary())
    assert "Оптимизация завершена" in text
    assert "Обработано карточек: <b>2</b>" in text
    assert "Переходы в поиске: <b>4/5</b>" in text
    assert "Переходы в карты: <b>1/3</b>" in text
    assert "Не выполнено действий: <b>3</b>" in text
    assert "Всего действий: <b>5/8</b>" in text
    assert "(80%)" in text
    assert "Карточки по статусу:" in text
    assert "Полностью: <b>0</b>" in text
    assert "Частично: <b>2</b>" in text
    assert "Без результата: <b>0</b>" in text
    assert "Поиск по организациям" in text
    assert "Карты по организациям" in text
    assert "Не удалось выполнить" in text
    assert "<b>Кофейня</b>: поиск 3/3" in text
    assert "<b>Кофейня</b>: карты 0/1" in text
    assert "<b>Кофейня</b>: карты 1 (всего 1)" in text
    assert "<b>Card B</b>: поиск 1/2" in text
    assert "<b>Card B</b>: карты 1/2" in text
    assert "<b>Card B</b>: поиск 1, карты 1 (всего 2)" in text
    assert "Кофейня" in text
    assert "Card B" in text


def test_notification_message_includes_timing() -> None:
    summary = _optimization_summary()
    summary["started_at"] = "2026-06-11T23:10:05"
    summary["finished_at"] = "2026-06-11T23:25:40"
    summary["duration_seconds"] = 935.0
    text = NotificationService.build_optimization_message(summary)
    assert "🟢 Начало работы: <i>11.06.2026 23:10:05</i>" in text
    assert "🔴 Завершение: <i>11.06.2026 23:25:40</i>" in text
    assert "⏱ Затрачено времени: <b>15 мин 35 сек</b>" in text


def test_notification_message_timing_optional() -> None:
    text = NotificationService.build_optimization_message(_optimization_summary())
    assert "Завершение:" in text
    assert "Начало работы:" not in text
    assert "Затрачено времени:" not in text


def test_notification_duration_formatting() -> None:
    assert NotificationService._format_duration(0) == "0 сек"
    assert NotificationService._format_duration(45) == "45 сек"
    assert NotificationService._format_duration(125) == "2 мин 5 сек"
    assert NotificationService._format_duration(3725) == "1 ч 2 мин 5 сек"


def test_notification_percent_suffix_and_card_states() -> None:
    assert NotificationService._percent_suffix(4, 5) == " (80%)"
    assert NotificationService._percent_suffix(0, 0) == ""
    assert NotificationService._percent_suffix(10, 5) == " (100%)"

    cards = [
        {"search_target": 2, "search_performed": 2, "maps_target": 1, "maps_performed": 1},  # completed
        {"search_target": 2, "search_performed": 1, "maps_target": 0, "maps_performed": 0},  # partial
        {"search_target": 1, "search_performed": 0, "maps_target": 1, "maps_performed": 0},  # idle
        {"search_target": 0, "search_performed": 0, "maps_target": 0, "maps_performed": 0},  # без цели
    ]
    assert NotificationService._count_card_states(cards) == (1, 1, 1)


def test_notification_mode_lines_include_percent_and_effect_keys() -> None:
    summary = {
        "processed_cards": 1,
        "total_search_target": 2,
        "total_search_performed": 1,
        "total_maps_target": 0,
        "total_maps_performed": 0,
        "cards": [
            {
                "card_id": 1,
                "card_name": "Card",
                "organization": "Кофейня",
                "search_target": 2,
                "search_performed": 1,
                "search_effect_keys": [10, 11],
                "maps_target": 0,
                "maps_performed": 0,
            }
        ],
    }
    text = NotificationService.build_optimization_message(summary)
    assert "поиск 1/2 (50%)" in text
    assert "результативных ключей: 2" in text


def test_notification_renders_target_action_totals() -> None:
    summary = {
        "processed_cards": 1,
        "total_search_target": 0,
        "total_search_performed": 0,
        "total_maps_target": 1,
        "total_maps_performed": 1,
        "total_action_counts": {"Маршрут": 2, "Показать телефон": 1, "мессенджер": 3},
        "cards": [
            {
                "card_id": 1,
                "card_name": "Card",
                "organization": "Кофейня",
                "search_target": 0,
                "search_performed": 0,
                "maps_target": 1,
                "maps_performed": 1,
                "maps_action_counts": {"Маршрут": 2, "Показать телефон": 1, "мессенджер": 3},
            }
        ],
    }
    text = NotificationService.build_optimization_message(summary)
    assert "🎯 Целевые действия:" in text
    assert "📞 Показать телефон: <b>1</b>" in text
    assert "🧭 Маршрут: <b>2</b>" in text
    assert "💬 Мессенджеры: <b>3</b>" in text
    # Поиск не включался — не должен числиться как «не выполнено».
    assert "Не удалось выполнить" not in text


def test_empty_card_result_zeroes_targets_without_enabled_keys() -> None:
    service = SearchRunnerService()
    card_payload = {
        "card_id": 7,
        "card_name": "Card",
        "organization": "Кофейня",
        "search_target": 3,
        "maps_target": 2,
        "keys": [
            {"id": 1, "phrase": "кофе", "search_enabled": False, "maps_enabled": True},
        ],
    }
    result = service._empty_card_result(card_payload)
    assert result["search_target"] == 0
    assert result["maps_target"] == 2
    assert result["maps_action_counts"] == {}


def test_notification_skipped_without_telegram_settings(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    settings = SettingsService(repo)
    notifier = _FakeNotifier()
    service = NotificationService(settings_service=settings, notifier=notifier)

    sent = service.notify_optimization_finished(_optimization_summary(), background=False)
    assert sent is False
    assert notifier.sent == []


def test_notification_sends_when_telegram_configured(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    settings = SettingsService(repo)
    settings.save_settings({"telegram_token": "  token-123  ", "telegram_chat_id": "  999  "})
    notifier = _FakeNotifier()
    service = NotificationService(settings_service=settings, notifier=notifier)

    sent = service.notify_optimization_finished(_optimization_summary(), background=False)
    assert sent is True
    assert len(notifier.sent) == 1
    token, chat_id, text, proxy = notifier.sent[0]
    assert token == "token-123"
    assert chat_id == "999"
    assert proxy == ""
    assert "Оптимизация завершена" in text


def test_notification_sends_to_multiple_chat_ids(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    settings = SettingsService(repo)
    settings.save_settings({"telegram_token": "token-123", "telegram_chat_id": "111, 222 ; 333"})
    notifier = _FakeNotifier()
    service = NotificationService(settings_service=settings, notifier=notifier)

    sent = service.notify_optimization_finished(_optimization_summary(), background=False)
    assert sent is True
    assert [entry[1] for entry in notifier.sent] == ["111", "222", "333"]


def test_notification_parse_chat_ids() -> None:
    assert NotificationService.parse_chat_ids("111, 222 ; 333") == ["111", "222", "333"]
    assert NotificationService.parse_chat_ids(" 999 ") == ["999"]
    assert NotificationService.parse_chat_ids("111\n222 111") == ["111", "222"]
    assert NotificationService.parse_chat_ids("") == []
    assert NotificationService.parse_chat_ids(None) == []


def test_notification_passes_valid_proxy(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    settings = SettingsService(repo)
    settings.save_settings(
        {
            "telegram_token": "token-123",
            "telegram_chat_id": "999",
            "telegram_proxy": "http://user:pass@123.45.67.89:8080",
        }
    )
    notifier = _FakeNotifier()
    service = NotificationService(settings_service=settings, notifier=notifier)

    service.notify_optimization_finished(_optimization_summary(), background=False)
    assert notifier.sent[0][3] == "http://user:pass@123.45.67.89:8080"


def test_notification_drops_invalid_proxy(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "svc.db")
    settings = SettingsService(repo)
    settings.save_settings(
        {
            "telegram_token": "token-123",
            "telegram_chat_id": "999",
            "telegram_proxy": "не-прокси-чушь",
        }
    )
    notifier = _FakeNotifier()
    service = NotificationService(settings_service=settings, notifier=notifier)

    service.notify_optimization_finished(_optimization_summary(), background=False)
    assert notifier.sent[0][3] == ""


def test_notification_proxy_validation() -> None:
    assert NotificationService.is_valid_proxy("http://123.45.67.89:8080") is True
    assert NotificationService.is_valid_proxy("https://user:pass@proxy.example.com:3128") is True
    assert NotificationService.is_valid_proxy("123.45.67.89:8080") is False
    assert NotificationService.is_valid_proxy("http://proxy-without-port") is False
    assert NotificationService.is_valid_proxy("ftp://host:21") is False


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


def test_yandex_org_service_requires_non_empty_url() -> None:
    class _StubParser:
        def parse(self, _url: str) -> dict[str, str]:
            return {}

    service = YandexOrganizationService(parser=_StubParser())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        service.autofill_from_url("   ")


def test_yandex_org_parser_extracts_coordinates_and_address_parts() -> None:
    parser = YandexOrganizationParser()

    assert (
        parser._coordinates_from_url("https://yandex.ru/maps/?ll=37.6176%2C55.7558&z=17")
        == "37.6176, 55.7558"
    )
    assert (
        parser._coordinates_from_url("https://yandex.ru/maps/#ll=37.6176%2C55.7558&z=17")
        == "37.6176, 55.7558"
    )
    assert parser._coordinates_from_url("https://yandex.ru/maps/") == ""

    city, street, house = parser._split_address("Россия, Москва, Тверская улица, дом 1")
    assert city == "Москва"
    assert street == "Тверская улица"
    assert house == "дом 1"

    city2, street2, house2 = parser._split_address("Московская область, Химки, Ленинский проспект, 7")
    assert city2 == "Химки"
    assert street2 == "Ленинский проспект"
    assert house2 == "7"


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

    def fake_maps(key_payload: dict[str, object], card_payload: dict[str, object], action_budget=None) -> bool:
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


def test_search_runner_maps_mode_uses_card_activity_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SearchRunnerService()
    calls: dict[str, object] = {}

    class _DummyLocator:
        def wait_for(self, **_kwargs) -> None:
            return

    class _DummyPage:
        def goto(self, *_args, **_kwargs) -> None:
            return

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

        def wait_for_load_state(self, *_args, **_kwargs) -> None:
            return

        def locator(self, *_args, **_kwargs):
            return _DummyLocator()

    class _DummyContext:
        def new_page(self):
            return _DummyPage()

        def close(self) -> None:
            return

    class _DummyBrowser:
        def close(self) -> None:
            return

    class _DummyPlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> None:
            return

    monkeypatch.setattr(service, "_prepare_runtime_browsers_path", lambda: Path("/tmp/pw"))
    monkeypatch.setattr(service, "_launch_chromium_with_recovery", lambda *_args, **_kwargs: _DummyBrowser())
    monkeypatch.setattr(service, "_create_human_like_context", lambda _browser: _DummyContext())
    monkeypatch.setattr(service, "_get_maps_search_input", lambda _page: object())
    monkeypatch.setattr(service, "_type_query_and_submit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_close_browser_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_build_maps_url", lambda _payload: "https://yandex.ru/maps/?lang=ru_RU")
    monkeypatch.setattr(
        service,
        "_find_and_open_maps_organization",
        lambda *_args, **_kwargs: True,
    )

    def fake_target_activity(page, **kwargs) -> dict[str, int]:
        calls["target"] = kwargs
        return {"Маршрут": 2}

    def fake_competitor_activity(page, **kwargs) -> None:
        calls["competitor"] = kwargs

    monkeypatch.setattr(service, "_run_maps_card_activity", fake_target_activity)
    monkeypatch.setattr(service, "_run_competitor_card_activity", fake_competitor_activity)
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.sync_playwright",
        lambda: _DummyPlaywrightContext(),
        raising=False,
    )

    result = service._simulate_browser_action_one_second(
        key_payload={"phrase": "кофейня"},
        card_payload={
            "organization": "Кофейня №1",
            "map_zoom_clicks": 4,
            "click_show_phone": 2,
            "click_website": 1,
            "click_route": 3,
            "click_messengers": 4,
            "click_book_story": 5,
            "min_sleep_target_tab_sec": 6,
            "max_sleep_target_tab_sec": 9,
            "competitor_open_chance_percent": 25,
            "max_open_competitor_cards": 3,
            "min_sleep_competitor_card_sec": 7,
            "max_sleep_competitor_card_sec": 11,
        },
    )

    assert result == {"effect": True, "actions": {"Маршрут": 2}}
    target_kwargs = calls["target"]
    assert target_kwargs["min_sleep_target_tab_sec"] == 6
    assert target_kwargs["max_sleep_target_tab_sec"] == 9
    # Лимиты целевых действий приходят как суммарный бюджет карточки.
    assert target_kwargs["action_budget"].snapshot() == {
        "Показать телефон": 2,
        "Сайт": 1,
        "Маршрут": 3,
        "мессенджер": 4,
        "Записаться": 5,
    }
    assert calls["competitor"] == {
        "chance_percent": 25,
        "max_open_cards": 3,
        "min_sleep_sec": 7,
        "max_sleep_sec": 11,
    }


def test_search_runner_phone_locator_uses_show_phone_text() -> None:
    class _FakePage:
        def __init__(self) -> None:
            self.selector = ""

        def locator(self, selector: str):
            self.selector = selector
            return object()

    page = _FakePage()
    SearchRunnerService._maps_action_locator(page, "Показать телефон")
    assert "card-phones-view__more-wrapper" in page.selector
    assert "Показать номер" not in page.selector


def test_search_runner_route_locator_uses_role_and_text_without_classes() -> None:
    class _FakePage:
        def __init__(self) -> None:
            self.selector = ""

        def locator(self, selector: str):
            self.selector = selector
            return object()

    page = _FakePage()
    SearchRunnerService._maps_action_locator(page, "Маршрут")
    assert "button[role='button']:has-text('Маршрут')" in page.selector
    assert "Маршрут" in page.selector
    assert "action-button-view__" not in page.selector


def test_search_runner_messenger_clicks_use_sameas_links_and_random_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SearchRunnerService()
    captured: dict[str, object] = {"selector": "", "indices": [], "clicks": 0}

    class _FakeNth:
        def __init__(self, bucket: dict[str, object], index: int) -> None:
            self.bucket = bucket
            self.index = index

        def click(self, **_kwargs) -> None:
            self.bucket["indices"].append(self.index)
            self.bucket["clicks"] += 1

    class _FakeLocator:
        def __init__(self, bucket: dict[str, object]) -> None:
            self.bucket = bucket

        def count(self) -> int:
            return 3

        def nth(self, index: int):
            return _FakeNth(self.bucket, index)

    class _FakePage:
        def locator(self, selector: str):
            captured["selector"] = selector
            return _FakeLocator(captured)

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    randint_calls = {"count": 0}

    def fake_randint(_a: int, _b: int) -> int:
        randint_calls["count"] += 1
        if randint_calls["count"] == 1:
            return 1
        return 500

    monkeypatch.setattr("shagteampro.application.services.search_runner_service.random.randint", fake_randint)
    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: None)

    service._perform_maps_messenger_clicks(_FakePage(), attempts=2)

    assert "itemprop='sameAs'" in str(captured["selector"])
    assert "business-contacts-view__social-button" not in str(captured["selector"])
    assert "aria-label*='Соцсети'" not in str(captured["selector"])
    assert captured["indices"] == [1, 1]
    assert captured["clicks"] == 2
    assert randint_calls["count"] == 3


def test_search_runner_route_clicks_with_fallback_locator(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SearchRunnerService()
    state = {"regular": 0, "forced": 0}

    class _FakeTarget:
        def scroll_into_view_if_needed(self) -> None:
            return

        def click(self, *, force: bool = False, timeout: int = 0) -> None:
            if not force:
                state["regular"] += 1
                raise RuntimeError("covered by overlay")
            state["forced"] += 1

    class _FakeLocator:
        def count(self) -> int:
            return 1

        @property
        def first(self):
            return _FakeTarget()

    class _FakePage:
        def locator(self, _selector: str):
            return _FakeLocator()

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 500,
    )

    service._perform_maps_route_clicks(_FakePage(), attempts=1)

    assert state["regular"] == 1
    assert state["forced"] == 1


def test_search_runner_restores_page_when_action_changes_url(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SearchRunnerService()
    state = {"go_back_called": 0}

    class _FakePage:
        def __init__(self) -> None:
            self.url = "https://yandex.ru/maps/org/after-click"

        def go_back(self, **_kwargs) -> None:
            state["go_back_called"] += 1
            self.url = "https://yandex.ru/maps/org/base"

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 900,
    )

    page = _FakePage()
    service._restore_maps_page_after_action(page, "https://yandex.ru/maps/org/base", "Маршрут")

    assert state["go_back_called"] == 1
    assert page.url == "https://yandex.ru/maps/org/base"


def test_search_runner_cta_clicks_use_container_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SearchRunnerService()
    captured: dict[str, object] = {"selector": "", "clicked": 0}

    class _FakeFirst:
        def __init__(self, bucket: dict[str, object]) -> None:
            self.bucket = bucket

        def click(self, **_kwargs) -> None:
            self.bucket["clicked"] += 1

    class _FakeLocator:
        def __init__(self, bucket: dict[str, object], count_value: int) -> None:
            self.bucket = bucket
            self._count_value = count_value

        def count(self) -> int:
            return self._count_value

        @property
        def first(self):
            return _FakeFirst(self.bucket)

    class _FakePage:
        def __init__(self) -> None:
            self.url = "https://yandex.ru/maps/org/base"
            self.calls = 0

        def locator(self, selector: str):
            self.calls += 1
            if self.calls == 1:
                captured["selector"] = selector
                return _FakeLocator(captured, 1)
            return _FakeLocator(captured, 0)

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 500,
    )
    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_restore_maps_page_after_action", lambda *_args, **_kwargs: None)

    page = _FakePage()
    service._perform_maps_cta_clicks(page, attempts=1)

    assert ".business-card-title-view__call-to-action [role='button']" in str(captured["selector"])
    assert captured["clicked"] == 1


def test_search_runner_cta_locator_prefers_announcement_over_route_icon() -> None:
    selectors: list[str] = []

    class _FakeLocator:
        def __init__(self, count_value: int) -> None:
            self._count_value = count_value

        def count(self) -> int:
            return self._count_value

    class _FakePage:
        def locator(self, selector: str):
            selectors.append(selector)
            # Контейнера call-to-action нет, но есть announcement-кнопка.
            if "call-to-action" in selector:
                return _FakeLocator(0)
            if "_view_announcement" in selector:
                return _FakeLocator(1)
            return _FakeLocator(5)

    chosen = SearchRunnerService._maps_cta_locator(_FakePage())
    assert chosen.count() == 1
    # Иконочный широкий фолбэк не должен быть выбран раньше announcement.
    assert any("_view_announcement" in selector for selector in selectors)
    assert "action-button-view__icon" not in selectors[-1]


def test_search_runner_phone_clicks_count_only_real_reveal(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SearchRunnerService()
    state = {"show_phone_calls": 0, "clicks": 0}

    class _FakeFirst:
        def click(self, **_kwargs) -> None:
            state["clicks"] += 1

    class _FakeLocator:
        def count(self) -> int:
            return 1

        @property
        def first(self):
            return _FakeFirst()

    class _FakePage:
        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    def fake_count(_page) -> int:
        state["show_phone_calls"] += 1
        return 1 if state["show_phone_calls"] == 1 else 0

    monkeypatch.setattr(service, "_maps_action_locator", lambda _page, _label: _FakeLocator())
    monkeypatch.setattr(service, "_count_show_phone_controls", fake_count)
    monkeypatch.setattr(service, "_count_visible_phone_values", lambda _page: 0)
    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 500,
    )

    service._perform_maps_phone_clicks(_FakePage(), attempts=1)

    assert state["clicks"] == 1
    assert state["show_phone_calls"] == 2


def test_search_runner_phone_clicks_accept_when_phone_already_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SearchRunnerService()
    state = {"clicks": 0, "slept": 0}

    class _FakeFirst:
        def click(self, **_kwargs) -> None:
            state["clicks"] += 1

    class _FakeLocator:
        def count(self) -> int:
            return 1

        @property
        def first(self):
            return _FakeFirst()

    class _FakePage:
        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    monkeypatch.setattr(service, "_maps_action_locator", lambda _page, _label: _FakeLocator())
    monkeypatch.setattr(service, "_count_show_phone_controls", lambda _page: 1)
    monkeypatch.setattr(service, "_count_visible_phone_values", lambda _page: 1)
    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_sleep_in_range_seconds",
        lambda *_args, **_kwargs: state.__setitem__("slept", state["slept"] + 1),
    )
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 500,
    )

    service._perform_maps_phone_clicks(
        _FakePage(),
        attempts=1,
        min_sleep_target_tab_sec=2,
        max_sleep_target_tab_sec=2,
    )

    assert state["clicks"] == 1
    assert state["slept"] == 1


def test_search_runner_budgeted_plan_reserves_and_shuffles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Бюджет: телефон=2, сайт=3, маршрут=2. randint резервирует 1, 0, 2 соответственно.
    from shagteampro.application.services.search_runner_service import _ActionBudget

    call_values = iter([1, 0, 2])
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: next(call_values),
    )
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.shuffle",
        lambda items: items.reverse(),
    )

    budget = _ActionBudget(
        {"Показать телефон": 2, "Сайт": 3, "Маршрут": 2}
    )
    plan = SearchRunnerService._build_budgeted_maps_action_plan(budget)

    assert plan == [("Маршрут", 2), ("Показать телефон", 1)]
    # Зарезервированное вычтено из общего бюджета карточки.
    assert budget.snapshot() == {"Показать телефон": 1, "Сайт": 3, "Маршрут": 0}


def test_search_runner_budgeted_plan_empty_when_nothing_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shagteampro.application.services.search_runner_service import _ActionBudget

    # Каждое действие резервирует 0 — план может быть пустым (это допустимо).
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 0,
    )
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.shuffle",
        lambda items: None,
    )

    budget = _ActionBudget({"Показать телефон": 2, "Сайт": 3})
    plan = SearchRunnerService._build_budgeted_maps_action_plan(budget)

    assert plan == []


def test_search_runner_budget_total_never_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shagteampro.application.services.search_runner_service import _ActionBudget

    # Имитируем 100 переходов карты: каждый раз резервируем и считаем "выполнено"
    # ровно столько, сколько зарезервировано (без возвратов). Суммарно не больше лимита.
    budget = _ActionBudget(
        {"Показать телефон": 1, "Сайт": 1, "Маршрут": 1, "мессенджер": 1, "Записаться": 1}
    )
    performed_totals: dict[str, int] = {}
    for _ in range(100):
        for action_label, attempts in SearchRunnerService._build_budgeted_maps_action_plan(budget):
            performed_totals[action_label] = performed_totals.get(action_label, 0) + attempts

    assert all(total <= 1 for total in performed_totals.values())


def test_search_runner_budget_refund_returns_unused(
) -> None:
    from shagteampro.application.services.search_runner_service import _ActionBudget

    budget = _ActionBudget({"Сайт": 5})
    budget.settle("Сайт", reserved=3, performed=1)
    assert budget.snapshot()["Сайт"] == 7


def test_search_runner_close_new_tab_if_opened_closes_extra_tab() -> None:
    service = SearchRunnerService()
    closed = {"extra": 0}

    class _FakeTab:
        def __init__(self, key: str) -> None:
            self.key = key
            self.waited_ms = 0

        def close(self) -> None:
            if self.key == "extra":
                closed["extra"] += 1

        def wait_for_timeout(self, ms: int) -> None:
            self.waited_ms += ms

    base = _FakeTab("base")
    extra = _FakeTab("extra")

    class _FakeContext:
        @property
        def pages(self):
            return [base, extra]

    class _FakePage:
        def __init__(self) -> None:
            self.context = _FakeContext()

    service._close_new_tab_if_opened(
        _FakePage(),
        pages_before=1,
        action_label="Сайт",
        min_sleep_target_tab_sec=2,
        max_sleep_target_tab_sec=2,
    )
    assert closed["extra"] == 1
    assert extra.waited_ms == 2000


def test_search_runner_run_maps_card_activity_forwards_sleep_range_to_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SearchRunnerService()
    captured: list[tuple[str, int, int, int]] = []

    from shagteampro.application.services.search_runner_service import _ActionBudget

    monkeypatch.setattr(
        service,
        "_build_budgeted_maps_action_plan",
        lambda _budget: [("Маршрут", 1), ("Показать телефон", 1), ("CTA", 1)],
    )
    monkeypatch.setattr(service, "_sleep_in_range_seconds", lambda *_args, **_kwargs: None)

    def fake_perform(page, action_label: str, attempts: int, **kwargs) -> int:
        captured.append(
            (
                action_label,
                attempts,
                int(kwargs["min_sleep_target_tab_sec"]),
                int(kwargs["max_sleep_target_tab_sec"]),
            )
        )
        return attempts

    monkeypatch.setattr(service, "_perform_maps_action_clicks", fake_perform)
    service._run_maps_card_activity(
        object(),
        action_budget=_ActionBudget({"Маршрут": 4, "Показать телефон": 2}),
        min_sleep_target_tab_sec=7,
        max_sleep_target_tab_sec=9,
    )

    assert captured == [
        ("Маршрут", 1, 7, 9),
        ("Показать телефон", 1, 7, 9),
        ("CTA", 1, 7, 9),
    ]


def test_search_runner_route_sleep_happens_before_captcha(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SearchRunnerService()
    order: list[str] = []

    class _FakeTarget:
        def scroll_into_view_if_needed(self) -> None:
            return

        def click(self, *, force: bool = False, timeout: int = 0) -> None:
            return

    class _FakeLocator:
        def count(self) -> int:
            return 1

        @property
        def first(self):
            return _FakeTarget()

    class _FakePage:
        url = "https://yandex.ru/maps/"

        def locator(self, _selector: str):
            return _FakeLocator()

        def wait_for_timeout(self, *_args, **_kwargs) -> None:
            return

    monkeypatch.setattr(service, "_sleep_in_range_seconds", lambda *_args, **_kwargs: order.append("sleep"))
    monkeypatch.setattr(service, "_handle_captcha_if_present", lambda *_args, **_kwargs: order.append("captcha"))
    monkeypatch.setattr(service, "_restore_maps_page_after_action", lambda *_args, **_kwargs: order.append("back"))
    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.random.randint",
        lambda _a, _b: 500,
    )

    service._perform_maps_route_clicks(
        _FakePage(),
        attempts=1,
        min_sleep_target_tab_sec=2,
        max_sleep_target_tab_sec=2,
    )

    assert order[:2] == ["sleep", "captcha"]
