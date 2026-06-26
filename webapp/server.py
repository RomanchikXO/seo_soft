from __future__ import annotations

import datetime
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shagteampro.application.services.card_service import CardService
from shagteampro.application.services.import_service import ImportService
from shagteampro.application.services.key_service import KeyService
from shagteampro.application.services.notification_service import NotificationService
from shagteampro.application.services.optimization_progress import (
    create_run,
    finish_run,
    get_run_snapshot,
    pause_run,
    request_stop_run,
    resume_run,
)
from shagteampro.application.services.search_runner_service import (
    SearchRunnerService,
    cleanup_registered_user_data_dirs,
)
from shagteampro.application.services.settings_service import SettingsService
from shagteampro.application.services.yandex_organization_service import YandexOrganizationService
from shagteampro.infrastructure.importers.excel_importer import ExcelImporter
from shagteampro.infrastructure.parsers.yandex_organization_parser import YandexOrganizationParser
from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


@dataclass
class AppServices:
    card_service: CardService
    key_service: KeyService
    import_service: ImportService
    settings_service: SettingsService
    search_runner_service: SearchRunnerService
    yandex_organization_service: YandexOrganizationService
    notification_service: NotificationService | None = None


class CardSettingsRequest(BaseModel):
    city: str = ""
    street: str = ""
    house: str = ""
    organization: str = ""
    coordinates: str = ""
    search_transitions: float = Field(default=0, ge=0)
    maps_transitions: float = Field(default=0, ge=0)
    competitor_open_chance_percent: float = Field(default=0, ge=0)
    max_open_competitor_cards: float = Field(default=0, ge=0)
    min_sleep_competitor_card_sec: float = Field(default=0, ge=0)
    max_sleep_competitor_card_sec: float = Field(default=0, ge=0)
    min_sleep_target_overview_sec: float = Field(default=0, ge=0)
    max_sleep_target_overview_sec: float = Field(default=0, ge=0)
    min_sleep_target_tab_sec: float = Field(default=0, ge=0)
    max_sleep_target_tab_sec: float = Field(default=0, ge=0)
    allow_target_events: bool = False
    click_show_phone: float = Field(default=0, ge=0)
    click_website: float = Field(default=0, ge=0)
    click_route: float = Field(default=0, ge=0)
    click_messengers: float = Field(default=0, ge=0)
    click_book_story: float = Field(default=0, ge=0)
    map_zoom_clicks: float = Field(default=0, ge=0)


CARD_DEFAULTS_PREFIX = "card_default__"


class CardDefaultsRequest(BaseModel):
    competitor_open_chance_percent: float = Field(default=0, ge=0)
    max_open_competitor_cards: float = Field(default=0, ge=0)
    min_sleep_competitor_card_sec: float = Field(default=0, ge=0)
    max_sleep_competitor_card_sec: float = Field(default=0, ge=0)
    min_sleep_target_overview_sec: float = Field(default=0, ge=0)
    max_sleep_target_overview_sec: float = Field(default=0, ge=0)
    min_sleep_target_tab_sec: float = Field(default=0, ge=0)
    max_sleep_target_tab_sec: float = Field(default=0, ge=0)
    click_show_phone: float = Field(default=0, ge=0)
    click_website: float = Field(default=0, ge=0)
    click_route: float = Field(default=0, ge=0)
    click_messengers: float = Field(default=0, ge=0)
    click_book_story: float = Field(default=0, ge=0)
    map_zoom_clicks: float = Field(default=0, ge=0)


class CardCreateRequest(BaseModel):
    name: str
    settings: CardSettingsRequest | None = None


class CardUpdateRequest(BaseModel):
    name: str


class KeyCreateRequest(BaseModel):
    phrase: str


class KeyUpdateRequest(BaseModel):
    phrase: str


class KeyTargetsRequest(BaseModel):
    search_enabled: bool | None = None
    maps_enabled: bool | None = None


class SettingsRequest(BaseModel):
    city: str = ""
    street: str = ""
    house: str = ""
    organization: str = ""
    coordinates: str = ""
    captcha_service: str = "manual"
    capsola_token: str = ""
    botlab_token: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    telegram_proxy: str = ""


class OptimizationRunRequest(BaseModel):
    card_ids: list[int]
    threads: int = Field(default=1, ge=1, le=50)


class YandexOrgAutofillRequest(BaseModel):
    url: str


_CLICK_LABELS_BY_SHORT = {
    "tel": "Показать телефон",
    "site": "Сайт",
    "route": "Маршрут",
    "msg": "мессенджер",
    "story": "Записаться",
}


def _coerce_card_settings(settings: dict[str, str]) -> dict[str, object]:
    defaults = CardSettingsRequest().model_dump()
    result: dict[str, object] = dict(defaults)
    for key, value in settings.items():
        if key not in defaults:
            continue
        default_value = defaults[key]
        if isinstance(default_value, bool):
            result[key] = value.strip().lower() in {"1", "true", "yes", "on"}
        elif isinstance(default_value, (int, float)):
            try:
                result[key] = float(value)
            except (TypeError, ValueError):
                result[key] = default_value
        else:
            result[key] = value
    return result


def _coerce_card_defaults(stored: dict[str, str]) -> dict[str, object]:
    defaults = CardDefaultsRequest().model_dump()
    result: dict[str, object] = dict(defaults)
    for full_key, value in stored.items():
        if not full_key.startswith(CARD_DEFAULTS_PREFIX):
            continue
        key = full_key[len(CARD_DEFAULTS_PREFIX):]
        if key not in defaults:
            continue
        default_value = defaults[key]
        if isinstance(default_value, bool):
            result[key] = value.strip().lower() in {"1", "true", "yes", "on"}
        elif isinstance(default_value, (int, float)):
            try:
                result[key] = float(value)
            except (TypeError, ValueError):
                result[key] = default_value
        else:
            result[key] = value
    return result


def _database_path() -> Path:
    app_dir = Path(os.environ.get("SHAGTEAMPRO_APP_DIR", Path.home() / ".shagteampro"))
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "shagteampro.db"


def build_services(database_path: Path | None = None) -> AppServices:
    repository = SqliteRepository(database_path=database_path or _database_path())
    settings_service = SettingsService(repository=repository)
    return AppServices(
        card_service=CardService(repository=repository),
        key_service=KeyService(repository=repository),
        import_service=ImportService(excel_importer=ExcelImporter()),
        settings_service=settings_service,
        search_runner_service=SearchRunnerService(settings_service=settings_service),
        yandex_organization_service=YandexOrganizationService(parser=YandexOrganizationParser()),
        notification_service=NotificationService(settings_service=settings_service),
    )


def _build_optimization_cards_payload(
    resolved_services: AppServices,
    card_ids: list[int],
) -> list[dict[str, object]]:
    all_cards = {card.id: card for card in resolved_services.card_service.list_cards()}
    unique_card_ids = list(dict.fromkeys(card_ids))
    cards_payload: list[dict[str, object]] = []
    for card_id in unique_card_ids:
        card = all_cards.get(card_id)
        if card is None:
            continue
        card_settings = _coerce_card_settings(resolved_services.settings_service.load_card_settings(card_id))
        keys = resolved_services.key_service.list_for_card(card_id)
        cards_payload.append(
            {
                "card_id": card.id,
                "card_name": card.name,
                "search_target": card_settings.get("search_transitions", 0),
                "maps_target": card_settings.get("maps_transitions", 0),
                "city": card_settings.get("city", ""),
                "street": card_settings.get("street", ""),
                "house": card_settings.get("house", ""),
                "organization": card_settings.get("organization", ""),
                "coordinates": card_settings.get("coordinates", ""),
                "map_zoom_clicks": card_settings.get("map_zoom_clicks", 0),
                "competitor_open_chance_percent": card_settings.get("competitor_open_chance_percent", 0),
                "max_open_competitor_cards": card_settings.get("max_open_competitor_cards", 0),
                "min_sleep_competitor_card_sec": card_settings.get("min_sleep_competitor_card_sec", 0),
                "max_sleep_competitor_card_sec": card_settings.get("max_sleep_competitor_card_sec", 0),
                "min_sleep_target_overview_sec": card_settings.get("min_sleep_target_overview_sec", 0),
                "max_sleep_target_overview_sec": card_settings.get("max_sleep_target_overview_sec", 0),
                "min_sleep_target_tab_sec": card_settings.get("min_sleep_target_tab_sec", 0),
                "max_sleep_target_tab_sec": card_settings.get("max_sleep_target_tab_sec", 0),
                "click_show_phone": card_settings.get("click_show_phone", 0),
                "click_website": card_settings.get("click_website", 0),
                "click_route": card_settings.get("click_route", 0),
                "click_messengers": card_settings.get("click_messengers", 0),
                "click_book_story": card_settings.get("click_book_story", 0),
                "keys": [
                    {
                        "id": key.id,
                        "phrase": key.phrase,
                        "search_enabled": key.search_enabled,
                        "maps_enabled": key.maps_enabled,
                    }
                    for key in keys
                ],
            }
        )
    return cards_payload


def _build_interrupted_optimization_summary(
    snapshot: dict[str, object] | None,
    cards_payload: list[dict[str, object]],
    *,
    started_at: datetime.datetime,
    finished_at: datetime.datetime,
) -> dict[str, object]:
    """Собирает стандартный summary по частичным данным при прерывании запуска."""
    typed_snapshot = snapshot if isinstance(snapshot, dict) else {}
    cards_by_id = {
        int(card.get("card_id", 0)): card
        for card in cards_payload
        if int(card.get("card_id", 0) or 0) > 0
    }
    snapshot_cards_raw = typed_snapshot.get("cards", [])
    snapshot_cards = snapshot_cards_raw if isinstance(snapshot_cards_raw, list) else []
    progress_by_id = {
        int(item.get("card_id", 0)): item
        for item in snapshot_cards
        if isinstance(item, dict) and int(item.get("card_id", 0) or 0) > 0
    }

    total_action_counts: dict[str, int] = {}
    summary_cards: list[dict[str, object]] = []
    for card_id in sorted(cards_by_id):
        payload = cards_by_id[card_id]
        progress = progress_by_id.get(card_id, {})

        search_target = int(payload.get("search_target", 0) or 0)
        maps_target = int(payload.get("maps_target", 0) or 0)
        search_performed = max(0, min(search_target, int(progress.get("search_performed", 0) or 0)))
        maps_performed = max(0, min(maps_target, int(progress.get("maps_performed", 0) or 0)))
        card_failures = max(0, int(progress.get("failures", 0) or 0))

        remaining_search = max(0, search_target - search_performed)
        search_failures = min(card_failures, remaining_search)
        maps_failures = max(0, card_failures - search_failures)

        raw_clicks = progress.get("clicks")
        typed_clicks = raw_clicks if isinstance(raw_clicks, dict) else {}
        maps_action_counts = {
            label: int(typed_clicks.get(short, 0) or 0)
            for short, label in _CLICK_LABELS_BY_SHORT.items()
        }
        maps_action_counts = {key: value for key, value in maps_action_counts.items() if value > 0}
        for action_label, count in maps_action_counts.items():
            total_action_counts[action_label] = total_action_counts.get(action_label, 0) + int(count)

        summary_cards.append(
            {
                "card_id": card_id,
                "card_name": str(payload.get("card_name") or "Без названия"),
                "organization": str(payload.get("organization") or ""),
                "search_target": search_target,
                "search_performed": search_performed,
                "maps_target": maps_target,
                "maps_performed": maps_performed,
                "search_failures": search_failures,
                "maps_failures": maps_failures,
                "search_effect_keys": [],
                "maps_effect_keys": [],
                "maps_action_counts": maps_action_counts,
                "key_failure_reports": [],
                "exhausted_keys": [],
            }
        )

    total_search_target = sum(int(card.get("search_target", 0) or 0) for card in summary_cards)
    total_search_performed = sum(int(card.get("search_performed", 0) or 0) for card in summary_cards)
    total_maps_target = sum(int(card.get("maps_target", 0) or 0) for card in summary_cards)
    total_maps_performed = sum(int(card.get("maps_performed", 0) or 0) for card in summary_cards)

    return {
        "processed_cards": len(summary_cards),
        "total_search_target": total_search_target,
        "total_search_performed": total_search_performed,
        "total_maps_target": total_maps_target,
        "total_maps_performed": total_maps_performed,
        "total_failed_attempts": int(typed_snapshot.get("total_failed_attempts", 0) or 0),
        "total_action_counts": total_action_counts,
        "key_failure_reports": [],
        "exhausted_keys": [],
        "cards": summary_cards,
        "stopped_by_user": bool(typed_snapshot.get("stopped_by_user")),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
    }


def _run_optimization_worker(
    resolved_services: AppServices,
    cards_payload: list[dict[str, object]],
    threads: int,
    run_id: str,
) -> None:
    started_at = datetime.datetime.now()
    try:
        summary = resolved_services.search_runner_service.run_cards_optimization(
            cards_payload,
            threads,
            progress_run_id=run_id,
        )
        finished_at = datetime.datetime.now()
        summary["started_at"] = started_at.isoformat()
        summary["finished_at"] = finished_at.isoformat()
        summary["duration_seconds"] = (finished_at - started_at).total_seconds()
        finish_run(run_id, summary=summary)

        if resolved_services.notification_service is not None:
            try:
                resolved_services.notification_service.notify_optimization_finished(summary)
            except Exception:
                pass
    except BaseException as error:
        finished_at = datetime.datetime.now()
        reason = str(error) or type(error).__name__
        finish_run(run_id, error=reason)

        if resolved_services.notification_service is not None:
            try:
                snapshot = get_run_snapshot(run_id)
                summary = _build_interrupted_optimization_summary(
                    snapshot,
                    cards_payload,
                    started_at=started_at,
                    finished_at=finished_at,
                )
                resolved_services.notification_service.notify_optimization_finished(summary)
            except Exception:
                pass
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise


def create_app(base_dir: Path | None = None, services: AppServices | None = None) -> FastAPI:
    resolved_base_dir = base_dir or Path(__file__).resolve().parent
    resolved_services = services or build_services()

    app = FastAPI(title="ShagTeamPro Web")
    app.mount("/static", StaticFiles(directory=resolved_base_dir / "static"), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(resolved_base_dir / "templates" / "index.html")


    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}


    @app.get("/api/cards")
    def list_cards() -> list[dict]:
        return [{"id": card.id, "name": card.name} for card in resolved_services.card_service.list_cards()]


    @app.post("/api/cards")
    def create_card(payload: CardCreateRequest) -> dict:
        card = resolved_services.card_service.create_card(payload.name)
        if payload.settings is not None:
            resolved_services.settings_service.save_card_settings(card.id, payload.settings.model_dump())
        return {"id": card.id, "name": card.name}

    @app.put("/api/cards/{card_id}")
    def update_card(card_id: int, payload: CardUpdateRequest) -> dict:
        try:
            resolved_services.card_service.update_card(card_id, payload.name)
        except ValueError as error:
            status_code = 404 if "не найдена" in str(error).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(error)) from error
        return {"ok": True}

    @app.delete("/api/cards/{card_id}")
    def delete_card(card_id: int) -> dict:
        try:
            resolved_services.card_service.delete_card(card_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"ok": True}


    @app.get("/api/cards/{card_id}/keys")
    def list_keys(card_id: int) -> dict:
        key_phrases = resolved_services.key_service.list_for_card(card_id)
        run_dates = resolved_services.key_service.list_run_dates(card_id)
        positions_by_date = resolved_services.key_service.positions_for_card(card_id)
        keys = []
        for key in key_phrases:
            keys.append(
                {
                    "id": key.id,
                    "phrase": key.phrase,
                    "search_enabled": key.search_enabled,
                    "maps_enabled": key.maps_enabled,
                    "positions": {
                        run_date: positions_by_date.get(run_date, {}).get(key.id, "")
                        for run_date in run_dates
                    },
                }
            )
        return {"run_dates": run_dates, "keys": keys}


    @app.post("/api/cards/{card_id}/keys")
    def add_key(card_id: int, payload: KeyCreateRequest) -> dict:
        key = resolved_services.key_service.add_phrase(card_id, payload.phrase)
        return {"id": key.id}


    @app.put("/api/keys/{key_id}")
    def update_key(key_id: int, payload: KeyUpdateRequest) -> dict:
        resolved_services.key_service.update_phrase(key_id, payload.phrase)
        return {"ok": True}


    @app.delete("/api/keys/{key_id}")
    def delete_key(key_id: int) -> dict:
        resolved_services.key_service.delete_phrase(key_id)
        return {"ok": True}


    @app.patch("/api/keys/{key_id}/targets")
    def update_key_targets(key_id: int, payload: KeyTargetsRequest) -> dict:
        if payload.search_enabled is not None:
            resolved_services.key_service.set_search_enabled(key_id, payload.search_enabled)
        if payload.maps_enabled is not None:
            resolved_services.key_service.set_maps_enabled(key_id, payload.maps_enabled)
        return {"ok": True}


    @app.post("/api/cards/{card_id}/keys/import")
    async def import_keys(card_id: int, file: UploadFile = File(...)) -> dict:
        suffix = Path(file.filename or "keys.xlsx").suffix
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                content = await file.read()
                tmp.write(content)
                temp_path = Path(tmp.name)
            phrases = resolved_services.import_service.import_keywords(temp_path)
            inserted_count = resolved_services.key_service.add_phrases_bulk(card_id, phrases)
            return {"inserted": inserted_count}
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)


    @app.get("/api/settings")
    def get_settings() -> dict[str, str]:
        base = SettingsRequest().model_dump()
        base.update(resolved_services.settings_service.load_settings())
        return base


    @app.post("/api/settings")
    def save_settings(payload: SettingsRequest) -> dict:
        resolved_services.settings_service.save_settings(payload.model_dump())
        return {"ok": True}

    @app.get("/api/card-defaults")
    def get_card_defaults() -> dict[str, object]:
        stored = resolved_services.settings_service.load_settings()
        return _coerce_card_defaults(stored)

    @app.post("/api/card-defaults")
    def save_card_defaults(payload: CardDefaultsRequest) -> dict:
        prefixed = {
            f"{CARD_DEFAULTS_PREFIX}{key}": value
            for key, value in payload.model_dump().items()
        }
        resolved_services.settings_service.save_settings(prefixed)
        return {"ok": True}

    @app.get("/api/cards/{card_id}/settings")
    def get_card_settings(card_id: int) -> dict[str, object]:
        stored = resolved_services.settings_service.load_card_settings(card_id)
        return _coerce_card_settings(stored)

    @app.post("/api/cards/{card_id}/settings")
    def save_card_settings(card_id: int, payload: CardSettingsRequest) -> dict:
        try:
            resolved_services.settings_service.save_card_settings(card_id, payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"ok": True}


    @app.post("/api/cards/{card_id}/run-search")
    def run_search(card_id: int) -> dict:
        settings = resolved_services.settings_service.load_card_settings(card_id)
        city = settings.get("city", "")
        street = settings.get("street", "")
        house = settings.get("house", "")
        if not city and not street and not house:
            fallback_settings = resolved_services.settings_service.load_settings()
            city = fallback_settings.get("city", "")
            street = fallback_settings.get("street", "")
            house = fallback_settings.get("house", "")
        keys = resolved_services.key_service.list_for_card(card_id)
        phrases = [item.phrase for item in keys if item.search_enabled]
        if not phrases:
            return {"executed": 0}
        executed = resolved_services.search_runner_service.run_yandex_searches(
            phrases=phrases,
            city=city,
            street=street,
            house=house,
        )
        return {"executed": executed}

    @app.post("/api/optimization/run")
    def run_optimization(payload: OptimizationRunRequest) -> dict[str, object]:
        if payload.threads <= 0:
            raise HTTPException(status_code=400, detail="Количество потоков должно быть больше 0.")

        cards_payload = _build_optimization_cards_payload(resolved_services, payload.card_ids)
        run_id = create_run(payload.threads, cards_payload)
        worker = threading.Thread(
            target=_run_optimization_worker,
            args=(resolved_services, cards_payload, payload.threads, run_id),
            name=f"optimization-{run_id[:8]}",
            daemon=True,
        )
        worker.start()
        return {"run_id": run_id}

    @app.get("/api/optimization/status/{run_id}")
    def optimization_status(run_id: str) -> dict[str, object]:
        snapshot = get_run_snapshot(run_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Задача оптимизации не найдена.")
        return snapshot

    @app.post("/api/optimization/pause/{run_id}")
    def optimization_pause(run_id: str) -> dict[str, bool]:
        if not pause_run(run_id):
            raise HTTPException(status_code=409, detail="Задачу нельзя поставить на паузу.")
        return {"ok": True}

    @app.post("/api/optimization/resume/{run_id}")
    def optimization_resume(run_id: str) -> dict[str, bool]:
        if not resume_run(run_id):
            raise HTTPException(status_code=409, detail="Задачу нельзя возобновить.")
        return {"ok": True}

    @app.post("/api/optimization/stop/{run_id}")
    def optimization_stop(run_id: str) -> dict[str, bool]:
        if not request_stop_run(run_id):
            raise HTTPException(status_code=409, detail="Задачу нельзя остановить.")
        return {"ok": True}

    @app.post("/api/yandex-org/autofill")
    def yandex_org_autofill(payload: YandexOrgAutofillRequest) -> dict[str, str]:
        try:
            return resolved_services.yandex_organization_service.autofill_from_url(payload.url)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=502,
                detail="Не удалось получить данные организации из Яндекс.Карт.",
            ) from error

    @app.post("/api/shutdown")
    def shutdown(request: Request) -> dict[str, bool]:
        cleanup_registered_user_data_dirs("shutdown", log=False)
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
        return {"ok": True}

    return app


app = create_app()
