from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shagteampro.application.services.card_service import CardService
from shagteampro.application.services.import_service import ImportService
from shagteampro.application.services.key_service import KeyService
from shagteampro.application.services.search_runner_service import SearchRunnerService
from shagteampro.application.services.settings_service import SettingsService
from shagteampro.infrastructure.importers.excel_importer import ExcelImporter
from shagteampro.infrastructure.storage.sqlite_repo import SqliteRepository


@dataclass
class AppServices:
    card_service: CardService
    key_service: KeyService
    import_service: ImportService
    settings_service: SettingsService
    search_runner_service: SearchRunnerService


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


class OptimizationRunRequest(BaseModel):
    card_ids: list[int]
    threads: int = Field(default=1, ge=1, le=50)


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
    )


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
            phrases = resolved_services.import_service.import_keywords_from_excel(temp_path)
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
        if not city and not street:
            fallback_settings = resolved_services.settings_service.load_settings()
            city = fallback_settings.get("city", "")
            street = fallback_settings.get("street", "")
        keys = resolved_services.key_service.list_for_card(card_id)
        phrases = [item.phrase for item in keys if item.search_enabled]
        if not phrases:
            return {"executed": 0}
        executed = resolved_services.search_runner_service.run_yandex_searches(
            phrases=phrases,
            city=city,
            street=street,
        )
        return {"executed": executed}

    @app.post("/api/optimization/run")
    def run_optimization(payload: OptimizationRunRequest) -> dict[str, object]:
        if payload.threads <= 0:
            raise HTTPException(status_code=400, detail="Количество потоков должно быть больше 0.")

        all_cards = {card.id: card for card in resolved_services.card_service.list_cards()}
        unique_card_ids = list(dict.fromkeys(payload.card_ids))
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
                    "organization": card_settings.get("organization", ""),
                    "coordinates": card_settings.get("coordinates", ""),
                    "map_zoom_clicks": card_settings.get("map_zoom_clicks", 0),
                    "min_sleep_target_overview_sec": card_settings.get("min_sleep_target_overview_sec", 0),
                    "max_sleep_target_overview_sec": card_settings.get("max_sleep_target_overview_sec", 0),
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

        return resolved_services.search_runner_service.run_cards_optimization(cards_payload, payload.threads)

    @app.post("/api/shutdown")
    def shutdown(request: Request) -> dict[str, bool]:
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
        return {"ok": True}

    return app


app = create_app()
