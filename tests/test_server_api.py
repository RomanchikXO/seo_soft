from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("SHAGTEAMPRO_APP_DIR", str(Path(__file__).resolve().parents[1] / ".test-app-data"))

from webapp.server import AppServices, build_services, create_app


class StubSearchRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, str]] = []
        self.optimization_calls: list[tuple[list[dict], int]] = []

    def run_yandex_searches(self, phrases, city: str, street: str) -> int:
        captured = list(phrases)
        self.calls.append((captured, city, street))
        return len(captured)

    def run_cards_optimization(self, cards: list[dict], threads: int) -> dict:
        self.optimization_calls.append((cards, threads))
        return {
            "processed_cards": len(cards),
            "total_search_target": 0,
            "total_search_performed": 0,
            "total_maps_target": 0,
            "total_maps_performed": 0,
            "cards": [],
        }


class StubYandexOrganizationService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def autofill_from_url(self, url: str) -> dict[str, str]:
        self.calls.append(url)
        if "bad" in url:
            raise ValueError("Некорректная ссылка.")
        return {
            "organization": "Тестовая организация",
            "address": "Москва, Тверская улица, 1",
            "city": "Москва",
            "street": "Тверская улица",
            "house": "1",
            "coordinates": "37.6176, 55.7558",
            "yandex_org_url": url,
        }


def test_api_health_and_cards_flow(tmp_path: Path) -> None:
    services = build_services(database_path=tmp_path / "api.db")
    stub_runner = StubSearchRunner()
    stub_yandex_org = StubYandexOrganizationService()
    services = AppServices(
        card_service=services.card_service,
        key_service=services.key_service,
        import_service=services.import_service,
        settings_service=services.settings_service,
        search_runner_service=stub_runner,
        yandex_organization_service=stub_yandex_org,
    )
    base_dir = Path(__file__).resolve().parents[1] / "webapp"
    app = create_app(base_dir=base_dir, services=services)

    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        card = client.post(
            "/api/cards",
            json={
                "name": "Card 1",
                "settings": {"city": "Moscow", "street": "Arbat", "search_transitions": 10},
            },
        ).json()
        card_id = card["id"]

        key = client.post(f"/api/cards/{card_id}/keys", json={"phrase": "query one"}).json()
        key_id = key["id"]
        payload = client.get(f"/api/cards/{card_id}/keys").json()
        assert payload["keys"][0]["search_enabled"] is False

        client.patch(f"/api/keys/{key_id}/targets", json={"search_enabled": True})
        payload = client.get(f"/api/cards/{card_id}/keys").json()
        assert payload["keys"][0]["search_enabled"] is True

        card_settings = client.get(f"/api/cards/{card_id}/settings").json()
        assert card_settings["city"] == "Moscow"
        assert card_settings["street"] == "Arbat"
        assert card_settings["search_transitions"] == 10

        update_result = client.put(f"/api/cards/{card_id}", json={"name": "Card 1 updated"})
        assert update_result.status_code == 200
        assert update_result.json() == {"ok": True}
        cards_payload = client.get("/api/cards").json()
        assert cards_payload[0]["name"] == "Card 1 updated"

        update_settings_result = client.post(
            f"/api/cards/{card_id}/settings",
            json={"city": "SPB", "street": "Nevsky", "allow_target_events": True},
        )
        assert update_settings_result.status_code == 200
        assert update_settings_result.json() == {"ok": True}

        run_result = client.post(f"/api/cards/{card_id}/run-search").json()
        assert run_result == {"executed": 1}
        assert stub_runner.calls[0] == (["query one"], "SPB", "Nevsky")

        delete_card_result = client.delete(f"/api/cards/{card_id}")
        assert delete_card_result.status_code == 200
        assert delete_card_result.json() == {"ok": True}
        assert client.get("/api/cards").json() == []
        assert client.get(f"/api/cards/{card_id}/keys").json()["keys"] == []
        missing_delete_result = client.delete(f"/api/cards/{card_id}")
        assert missing_delete_result.status_code == 404

        shutdown_result = client.post("/api/shutdown").json()
        assert shutdown_result == {"ok": True}


def test_api_import_and_delete_key(tmp_path: Path, monkeypatch) -> None:
    services = build_services(database_path=tmp_path / "api.db")
    stub_runner = StubSearchRunner()
    stub_yandex_org = StubYandexOrganizationService()
    services = AppServices(
        card_service=services.card_service,
        key_service=services.key_service,
        import_service=services.import_service,
        settings_service=services.settings_service,
        search_runner_service=stub_runner,
        yandex_organization_service=stub_yandex_org,
    )
    base_dir = Path(__file__).resolve().parents[1] / "webapp"
    app = create_app(base_dir=base_dir, services=services)

    monkeypatch.setattr(services.import_service, "import_keywords_from_excel", lambda _path: ["k1", "k2"])

    with TestClient(app) as client:
        card_id = client.post("/api/cards", json={"name": "Card 2"}).json()["id"]

        response = client.post(
            f"/api/cards/{card_id}/keys/import",
            files={"file": ("keywords.xlsx", b"dummy", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200
        assert response.json() == {"inserted": 2}

        keys_payload = client.get(f"/api/cards/{card_id}/keys").json()
        key_id = keys_payload["keys"][0]["id"]
        delete_response = client.delete(f"/api/keys/{key_id}")
        assert delete_response.status_code == 200
        assert delete_response.json() == {"ok": True}


def test_api_optimization_run_uses_selected_cards_and_flags(tmp_path: Path) -> None:
    services = build_services(database_path=tmp_path / "api.db")
    stub_runner = StubSearchRunner()
    stub_yandex_org = StubYandexOrganizationService()
    services = AppServices(
        card_service=services.card_service,
        key_service=services.key_service,
        import_service=services.import_service,
        settings_service=services.settings_service,
        search_runner_service=stub_runner,
        yandex_organization_service=stub_yandex_org,
    )
    base_dir = Path(__file__).resolve().parents[1] / "webapp"
    app = create_app(base_dir=base_dir, services=services)

    with TestClient(app) as client:
        card_a_id = client.post("/api/cards", json={"name": "Card A"}).json()["id"]
        card_b_id = client.post("/api/cards", json={"name": "Card B"}).json()["id"]

        key_a_id = client.post(f"/api/cards/{card_a_id}/keys", json={"phrase": "query a"}).json()["id"]
        key_b_id = client.post(f"/api/cards/{card_b_id}/keys", json={"phrase": "query b"}).json()["id"]
        client.patch(f"/api/keys/{key_a_id}/targets", json={"search_enabled": True})
        client.patch(f"/api/keys/{key_b_id}/targets", json={"maps_enabled": True})

        client.post(
            f"/api/cards/{card_a_id}/settings",
            json={"search_transitions": 3, "maps_transitions": 1},
        )
        client.post(
            f"/api/cards/{card_b_id}/settings",
            json={
                "search_transitions": 2,
                "maps_transitions": 4,
                "coordinates": " 37.631182, 55.771363 ",
                "competitor_open_chance_percent": 45,
                "max_open_competitor_cards": 3,
                "min_sleep_competitor_card_sec": 2,
                "max_sleep_competitor_card_sec": 6,
                "min_sleep_target_tab_sec": 1,
                "max_sleep_target_tab_sec": 4,
                "click_show_phone": 2,
                "click_website": 1,
                "click_route": 3,
                "click_messengers": 2,
                "click_book_story": 1,
                "map_zoom_clicks": 5,
            },
        )

        response = client.post(
            "/api/optimization/run",
            json={"card_ids": [card_a_id, card_b_id], "threads": 5},
        )
        assert response.status_code == 200
        assert response.json()["processed_cards"] == 2
        assert len(stub_runner.optimization_calls) == 1
        cards_payload, threads_value = stub_runner.optimization_calls[0]
        assert threads_value == 5
        assert {item["card_id"] for item in cards_payload} == {card_a_id, card_b_id}
        card_a_payload = next(item for item in cards_payload if item["card_id"] == card_a_id)
        card_b_payload = next(item for item in cards_payload if item["card_id"] == card_b_id)
        assert card_a_payload["search_target"] == 3
        assert card_a_payload["maps_target"] == 1
        assert card_b_payload["search_target"] == 2
        assert card_b_payload["maps_target"] == 4
        assert card_b_payload["coordinates"] == "37.631182, 55.771363"
        assert card_b_payload["competitor_open_chance_percent"] == 45
        assert card_b_payload["max_open_competitor_cards"] == 3
        assert card_b_payload["min_sleep_competitor_card_sec"] == 2
        assert card_b_payload["max_sleep_competitor_card_sec"] == 6
        assert card_b_payload["min_sleep_target_tab_sec"] == 1
        assert card_b_payload["max_sleep_target_tab_sec"] == 4
        assert card_b_payload["click_show_phone"] == 2
        assert card_b_payload["click_website"] == 1
        assert card_b_payload["click_route"] == 3
        assert card_b_payload["click_messengers"] == 2
        assert card_b_payload["click_book_story"] == 1
        assert card_b_payload["map_zoom_clicks"] == 5
        assert card_a_payload["keys"][0]["search_enabled"] is True
        assert card_b_payload["keys"][0]["maps_enabled"] is True

        bad_response = client.post("/api/optimization/run", json={"card_ids": [card_a_id], "threads": 0})
        assert bad_response.status_code in (400, 422)


def test_api_yandex_org_autofill(tmp_path: Path) -> None:
    services = build_services(database_path=tmp_path / "api.db")
    stub_runner = StubSearchRunner()
    stub_yandex_org = StubYandexOrganizationService()
    services = AppServices(
        card_service=services.card_service,
        key_service=services.key_service,
        import_service=services.import_service,
        settings_service=services.settings_service,
        search_runner_service=stub_runner,
        yandex_organization_service=stub_yandex_org,
    )
    base_dir = Path(__file__).resolve().parents[1] / "webapp"
    app = create_app(base_dir=base_dir, services=services)

    with TestClient(app) as client:
        ok_response = client.post(
            "/api/yandex-org/autofill",
            json={"url": "https://yandex.ru/maps/org/test/123"},
        )
        assert ok_response.status_code == 200
        assert ok_response.json()["organization"] == "Тестовая организация"
        assert ok_response.json()["city"] == "Москва"
        assert stub_yandex_org.calls == ["https://yandex.ru/maps/org/test/123"]

        bad_response = client.post(
            "/api/yandex-org/autofill",
            json={"url": "https://yandex.ru/maps/org/bad/123"},
        )
        assert bad_response.status_code == 400
