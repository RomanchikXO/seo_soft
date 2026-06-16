from __future__ import annotations

import concurrent.futures
import datetime
import logging
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from shagteampro.application.captcha import CaptchaService
from shagteampro.application.services.settings_service import SettingsService

_RUN_LOG_PATH = Path.home() / ".shagteampro" / "logs" / "run.log"
_run_logger: logging.Logger | None = None
_run_logger_lock = threading.Lock()


def _get_run_logger() -> logging.Logger:
    """Лениво создаёт общий на процесс файловый логгер прогонов с ротацией.

    Логи дублируются в `~/.shagteampro/logs/run.log`, чтобы прогон сохранялся
    целиком и не терялся при перезаписи буфера терминала.
    """
    global _run_logger
    if _run_logger is not None:
        return _run_logger
    with _run_logger_lock:
        if _run_logger is None:
            _RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("shagteampro.run")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            handler = RotatingFileHandler(
                _RUN_LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            _run_logger = logger
    return _run_logger


class _ActionBudget:
    """Потокобезопасный счетчик оставшихся целевых действий на одну карточку.

    Значения `click_*` из настроек карточки трактуются как СУММАРНЫЙ лимит
    целевого действия на все переходы карты. Каждый параллельный переход
    резервирует часть оставшегося лимита, выполняет клики и возвращает в бюджет
    неиспользованный остаток. За счет общего на карточку бюджета суммарное число
    выполненных действий по каждому типу не может превысить заданный лимит.
    """

    # Порядок целевых действий в плане одного перехода карты.
    ACTION_ORDER: tuple[str, ...] = (
        "Показать телефон",
        "Сайт",
        "Маршрут",
        "мессенджер",
        "Записаться",
    )

    def __init__(self, limits: dict[str, int]) -> None:
        self._remaining: dict[str, int] = {
            label: int(value) for label, value in limits.items() if int(value) > 0
        }
        self._lock = threading.Lock()

    def reserve(self, label: str) -> int:
        """Резервирует случайную часть оставшегося лимита действия (0..remaining)."""
        with self._lock:
            remaining = self._remaining.get(label, 0)
            if remaining <= 0:
                return 0
            take = random.randint(0, remaining)
            self._remaining[label] = remaining - take
            return take

    def settle(self, label: str, reserved: int, performed: int) -> None:
        """Возвращает в бюджет зарезервированные, но не выполненные действия."""
        refund = int(reserved) - int(performed)
        if refund <= 0:
            return
        with self._lock:
            self._remaining[label] = self._remaining.get(label, 0) + refund

    def snapshot(self) -> dict[str, int]:
        """Возвращает копию оставшихся лимитов (для отладки/тестов)."""
        with self._lock:
            return dict(self._remaining)


class SearchRunnerService:
    """Сервис, который управляет Playwright-браузером и имитирует действия пользователя."""

    SEARCH_INPUT_XPATH = "/html/body/main/div[2]/form/div[4]/div/div[2]/div/textarea[1]"
    SEARCH_INPUT_CSS = "textarea#text.search3__input, textarea#text, textarea.search3__input"
    FORCED_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )

    def __init__(self, captcha_service: CaptchaService | None = None, settings_service: SettingsService | None = None) -> None:
        self._settings_service = settings_service
        self._captcha_service = captcha_service or CaptchaService(logger=self._log)

    def _update_captcha_service(self) -> None:
        """Обновляет экземпляр CaptchaService в зависимости от глобальных настроек."""
        if not self._settings_service:
            return
            
        settings = self._settings_service.load_settings()
        service_type = settings.get("captcha_service", "manual")
        
        if service_type == "capsola":
            from shagteampro.application.captcha.solvers.capsola import CapsolaCaptchaSolver
            token = settings.get("capsola_token", "")
            solver = CapsolaCaptchaSolver(token=token, logger=self._log)
        elif service_type == "botlab":
            from shagteampro.application.captcha.solvers.botlab import BotlabCaptchaSolver
            token = settings.get("botlab_token", "")
            solver = BotlabCaptchaSolver(token=token, logger=self._log)
        else:
            from shagteampro.application.captcha.solvers.manual import ManualCaptchaSolver
            solver = ManualCaptchaSolver()
            
        self._captcha_service = CaptchaService(solver=solver, logger=self._log)
        self._log(f"Настроен сервис обхода капчи: {solver.name}")

    def ensure_chromium_installed(self) -> bool:
        """Проверяет наличие Chromium для Playwright и устанавливает его при необходимости."""
        self._log("Проверяю наличие браузера Chromium для Playwright.")
        browsers_path = self._prepare_runtime_browsers_path()

        executable_path = self._chromium_executable_path()
        if executable_path is not None and executable_path.exists():
            self._log(f"Chromium уже установлен: {executable_path}")
            return False

        self._log("Chromium не найден, запускаю установку.")
        self._install_chromium(browsers_path)
        self._log("Установка Chromium завершена.")
        return True

    def run_yandex_searches(
        self,
        phrases: Iterable[str],
        city: str,
        street: str,
    ) -> int:
        """Выполняет простой пакетный поиск фраз в Яндексе."""
        self._update_captcha_service()
        queries = [self._build_query(phrase, city, street) for phrase in phrases]
        queries = [query for query in queries if query]
        if not queries:
            self._log("Список поисковых запросов пуст, выполнение пропущено.")
            return 0
        self._log(f"Запускаю пакетный поиск в Яндексе. Кол-во запросов: {len(queries)}")

        browsers_path = self._prepare_runtime_browsers_path()

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = self._launch_chromium_with_recovery(playwright, browsers_path)
            context = self._create_human_like_context(browser)
            page = context.new_page()
            executed_count = 0
            try:
                for query in queries:
                    # Каждый запрос изолирован: ошибка/ручное закрытие браузера на одном
                    # запросе не должны рушить весь пакетный прогон.
                    try:
                        if not self._is_page_alive(page):
                            self._log("run_yandex_searches: страница/браузер недоступны, пересоздаю сессию.")
                            context, browser, page = self._recreate_session(
                                playwright, browsers_path, context, browser
                            )

                        self._log(f"Открываю ya.ru для запроса: {query}")
                        page.goto("https://ya.ru/", wait_until="domcontentloaded")
                        page.wait_for_timeout(2000)
                        search_input = self._get_search_input(page)
                        page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
                        self._type_query_and_submit(page, search_input, query)
                        resolution = self._handle_captcha_if_present(page, context=f"после отправки запроса '{query}'")
                        page.wait_for_timeout(random.randint(2500, 4200))

                        if not resolution.detected or resolution.solved:
                            self._dismiss_distribution_modal(page, context="run_yandex_searches")

                        executed_count += 1
                        self._log(f"Запрос обработан успешно. Выполнено: {executed_count}/{len(queries)}")
                    except PlaywrightTimeoutError:
                        self._log(f"Таймаут на запросе '{query}', перехожу к следующему.")
                        continue
                    except Exception as error:
                        self._log(f"Ошибка на запросе '{query}': {error}. Перехожу к следующему.")
                        continue
            finally:
                self._close_browser_session(context, browser, "run_yandex_searches")
        self._log(f"Пакетный поиск завершен. Итого выполнено: {executed_count}")
        return executed_count

    @staticmethod
    def _is_page_alive(page) -> bool:
        """Проверяет, что страница и её браузер ещё открыты (не закрыты вручную)."""
        try:
            return not page.is_closed() and page.context.browser.is_connected()
        except Exception:
            return False

    def _recreate_session(self, playwright, browsers_path: Path, old_context, old_browser):
        """Закрывает мёртвую сессию и поднимает новую (браузер/контекст/страница)."""
        self._close_browser_session(old_context, old_browser, "run_yandex_searches:recreate")
        browser = self._launch_chromium_with_recovery(playwright, browsers_path)
        context = self._create_human_like_context(browser)
        page = context.new_page()
        return context, browser, page

    def run_cards_optimization(self, cards: list[dict[str, object]], threads: int) -> dict[str, object]:
        """Запускает оптимизацию карточек по выбранным поисковым и картографическим ключам."""
        self._update_captcha_service()
        prepared_cards = [card for card in cards if card.get("card_id") is not None]
        max_workers = max(1, min(int(threads), 50))
        self._log(
            f"Запускаю оптимизацию карточек. Карточек: {len(prepared_cards)}, потоков: {max_workers}"
        )
        if not prepared_cards:
            self._log("Нет карточек для обработки, возвращаю пустой результат.")
            return {
                "processed_cards": 0,
                "total_search_target": 0,
                "total_search_performed": 0,
                "total_maps_target": 0,
                "total_maps_performed": 0,
                "cards": [],
            }

        self.ensure_chromium_installed()
        card_results = {
            int(card_payload.get("card_id", 0)): self._empty_card_result(card_payload)
            for card_payload in prepared_cards
        }
        targets = self._build_target_states(prepared_cards)
        self._run_targets_in_pool(targets, max_workers)
        for state in targets:
            self._apply_target_state(card_results[state["card_id"]], state)

        return self._build_optimization_summary(card_results)

    def _empty_card_result(self, card_payload: dict[str, object]) -> dict[str, object]:
        """Создает начальную строку результата оптимизации для одной карточки.

        Цель по режиму учитывается только если по нему есть включённые ключи
        (стоит чекбокс). Иначе режим вообще не выполняется и не должен числиться
        как «не выполнено».
        """
        keys = self._card_key_payloads(card_payload)
        has_search_keys = any(bool(key.get("search_enabled")) for key in keys)
        has_maps_keys = any(bool(key.get("maps_enabled")) for key in keys)

        search_target = self._to_non_negative_int(card_payload.get("search_target", 0)) if has_search_keys else 0
        maps_target = self._to_non_negative_int(card_payload.get("maps_target", 0)) if has_maps_keys else 0

        return {
            "card_id": int(card_payload.get("card_id", 0)),
            "card_name": str(card_payload.get("card_name", "")),
            "organization": str(card_payload.get("organization", "")),
            "search_target": search_target,
            "search_performed": 0,
            "search_effect_keys": [],
            "maps_target": maps_target,
            "maps_performed": 0,
            "maps_effect_keys": [],
            "maps_action_counts": {},
        }

    def _build_target_states(self, prepared_cards: list[dict[str, object]]) -> list[dict[str, object]]:
        """Создает плоский список целей (карточка × режим), которые нужно набрать."""
        targets: list[dict[str, object]] = []
        for card_payload in prepared_cards:
            card_id = int(card_payload.get("card_id", 0))
            key_payloads = self._card_key_payloads(card_payload)
            target_specs = [
                (
                    "search",
                    self._to_non_negative_int(card_payload.get("search_target", 0)),
                    [key for key in key_payloads if bool(key.get("search_enabled"))],
                ),
                (
                    "maps",
                    self._to_non_negative_int(card_payload.get("maps_target", 0)),
                    [key for key in key_payloads if bool(key.get("maps_enabled"))],
                ),
            ]
            for mode, target, keys in target_specs:
                if target <= 0 or not keys:
                    continue
                self._log(f"Карточка #{card_id}: цель {mode}, target={target}, ключей={len(keys)}")
                targets.append(
                    {
                        "card_id": card_id,
                        "card_payload": card_payload,
                        "mode": mode,
                        "target": target,
                        "active_keys": list(keys),
                        "performed": 0,
                        "failures": 0,
                        "in_flight": 0,
                        "effect_key_ids": set(),
                        "action_counts": {},
                        # Бюджет целевых действий общий на все переходы карты этой карточки.
                        "action_budget": self._build_action_budget(card_payload) if mode == "maps" else None,
                    }
                )
        return targets

    def _build_action_budget(self, card_payload: dict[str, object]) -> _ActionBudget:
        """Создает суммарный бюджет целевых действий карты на одну карточку."""
        limits = {
            "Показать телефон": self._to_non_negative_int(card_payload.get("click_show_phone", 0)),
            "Сайт": self._to_non_negative_int(card_payload.get("click_website", 0)),
            "Маршрут": self._to_non_negative_int(card_payload.get("click_route", 0)),
            "мессенджер": self._to_non_negative_int(card_payload.get("click_messengers", 0)),
            "Записаться": self._to_non_negative_int(card_payload.get("click_book_story", 0)),
        }
        return _ActionBudget(limits)

    def _run_targets_in_pool(self, targets: list[dict[str, object]], max_workers: int) -> None:
        """Выполняет все цели в одном общем пуле, держа до max_workers действий одновременно.

        Единица параллелизма — это одно действие по одному ключу. Планировщик в главном
        потоке наполняет пул, поэтому при N потоках одновременно открывается до N браузеров,
        даже если все действия идут по одной фразе/ключу.
        """
        if not targets:
            return

        future_meta: dict[concurrent.futures.Future, tuple[dict[str, object], dict[str, object]]] = {}

        def fill(executor: concurrent.futures.Executor) -> None:
            while len(future_meta) < max_workers:
                state = self._pick_dispatchable_target(targets)
                if state is None:
                    break
                key_payload = random.choice(state["active_keys"])
                state["in_flight"] += 1
                future = executor.submit(self._execute_single_action, state, key_payload)
                future_meta[future] = (state, key_payload)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            fill(executor)
            while future_meta:
                done, _ = concurrent.futures.wait(
                    future_meta.keys(), return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    state, key_payload = future_meta.pop(future)
                    self._consume_action_result(state, key_payload, future)
                fill(executor)

        for state in targets:
            mode = state["mode"]
            self._log(
                f"Карточка #{state['card_id']}: {mode} завершен, выполнено="
                f"{state['performed']}/{state['target']}."
            )

    @staticmethod
    def _pick_dispatchable_target(targets: list[dict[str, object]]) -> dict[str, object] | None:
        """Выбирает цель, которой еще нужны действия и есть свободный «бюджет» на отправку."""
        for state in targets:
            if not state["active_keys"]:
                continue
            if state["performed"] + state["in_flight"] >= state["target"]:
                continue
            return state
        return None

    def _consume_action_result(
        self,
        state: dict[str, object],
        key_payload: dict[str, object],
        future: concurrent.futures.Future,
    ) -> None:
        """Учитывает результат одного завершенного действия (в главном потоке планировщика)."""
        state["in_flight"] -= 1
        try:
            result = self._normalize_action_result(future.result())
        except Exception as error:
            result = {"effect": False, "actions": {}}
            self._log(f"Карточка #{state['card_id']}: действие {state['mode']} упало с ошибкой {error}.")

        effect = bool(result["effect"])
        for action_label, count in result["actions"].items():
            if count:
                state["action_counts"][action_label] = (
                    state["action_counts"].get(action_label, 0) + int(count)
                )

        if effect:
            if state["performed"] < state["target"]:
                state["performed"] += 1
                key_id = int(key_payload.get("id", 0))
                if key_id:
                    state["effect_key_ids"].add(key_id)
                self._log(
                    f"Карточка #{state['card_id']}: {state['mode']} засчитано "
                    f"{state['performed']}/{state['target']}."
                )
        elif result.get("closed"):
            self._log(
                f"Карточка #{state['card_id']}: {state['mode']} — браузер закрыт вручную или потерян, "
                f"переоткрываю без штрафа."
            )
        else:
            self._handle_failed_action(state, key_payload)

    def _handle_failed_action(
        self,
        state: dict[str, object],
        key_payload: dict[str, object],
    ) -> None:
        """Обрабатывает неуспешный переход: переключает ключ или повторяет попытку.

        Если у цели есть другие ключи, текущий считается непродуктивным и
        убирается. Если ключ единственный, провал трактуется как транзитный
        (не открылась карта/капча) и попытка повторяется до исчерпания бюджета
        в `target` неудач — это защищает от зацикливания, но не обнуляет цель
        после первой же ошибки.
        """
        state["failures"] += 1
        card_id = state["card_id"]
        mode = state["mode"]

        if len(state["active_keys"]) > 1:
            state["active_keys"].remove(key_payload)
            return

        if state["failures"] >= state["target"]:
            state["active_keys"].clear()
            self._log(
                f"Карточка #{card_id}: {mode} — исчерпан лимит неудачных попыток "
                f"({state['failures']}/{state['target']}), останавливаю."
            )
            return

        self._log(
            f"Карточка #{card_id}: {mode} — неудачная попытка "
            f"{state['failures']}/{state['target']}, повторю с тем же ключом."
        )

    def _execute_single_action(
        self, state: dict[str, object], key_payload: dict[str, object]
    ) -> dict[str, object]:
        """Выполняет одно действие по ключу в зависимости от режима цели."""
        if state["mode"] == "search":
            return self._normalize_action_result(
                self._simulate_search_action(key_payload, state["card_payload"])
            )
        return self._normalize_action_result(
            self._simulate_browser_action_one_second(
                key_payload,
                state["card_payload"],
                action_budget=state.get("action_budget"),
            )
        )

    @staticmethod
    def _normalize_action_result(raw: object) -> dict[str, object]:
        """Приводит результат действия к виду {'effect': bool, 'actions': dict, 'closed': bool}."""
        if isinstance(raw, dict):
            actions = raw.get("actions") or {}
            return {
                "effect": bool(raw.get("effect")),
                "actions": dict(actions) if isinstance(actions, dict) else {},
                "closed": bool(raw.get("closed")),
            }
        return {"effect": bool(raw), "actions": {}, "closed": False}

    _BROWSER_CLOSED_MARKERS: tuple[str, ...] = (
        "has been closed",
        "target closed",
        "targetclosederror",
        "browser closed",
        "browser has been closed",
        "connection closed",
        "page, context or browser has been closed",
    )

    @staticmethod
    def _is_browser_closed_error(error: Exception) -> bool:
        """Определяет, что ошибка вызвана закрытием/потерей браузера (в т.ч. вручную).

        Такое событие считается транзитным: поток переоткрывается без расхода
        бюджета неудач, поэтому ручное закрытие одного браузера не останавливает
        работу всей программы.
        """
        text = f"{type(error).__name__} {error}".lower()
        return any(marker in text for marker in SearchRunnerService._BROWSER_CLOSED_MARKERS)

    @staticmethod
    def _card_key_payloads(card_payload: dict[str, object]) -> list[dict[str, object]]:
        """Возвращает список ключей карточки, если он передан в ожидаемом формате."""
        key_payloads = card_payload.get("keys", [])
        return key_payloads if isinstance(key_payloads, list) else []

    def _apply_target_state(
        self,
        card_entry: dict[str, object],
        state: dict[str, object],
    ) -> None:
        """Записывает итог выполненной search/maps цели в строку карточки."""
        mode = state["mode"]
        card_entry[f"{mode}_performed"] = int(state["performed"])
        card_entry[f"{mode}_effect_keys"] = sorted(state["effect_key_ids"])
        if mode == "maps":
            merged: dict[str, int] = dict(card_entry.get("maps_action_counts", {}))
            for action_label, count in state.get("action_counts", {}).items():
                merged[action_label] = merged.get(action_label, 0) + int(count)
            card_entry["maps_action_counts"] = merged

    def _build_optimization_summary(self, card_results: dict[int, dict[str, object]]) -> dict[str, object]:
        """Собирает итоговый ответ оптимизации по всем карточкам."""
        results = sorted(card_results.values(), key=lambda item: int(item["card_id"]))
        totals = {
            name: sum(int(item[name]) for item in results)
            for name in (
                "search_target",
                "search_performed",
                "maps_target",
                "maps_performed",
            )
        }
        total_action_counts: dict[str, int] = {}
        for item in results:
            action_counts = item.get("maps_action_counts", {})
            if not isinstance(action_counts, dict):
                continue
            for action_label, count in action_counts.items():
                total_action_counts[action_label] = total_action_counts.get(action_label, 0) + int(count)

        self._log(
            f"Оптимизация завершена. search={totals['search_performed']}/{totals['search_target']}, "
            f"maps={totals['maps_performed']}/{totals['maps_target']}, "
            f"действия={total_action_counts}"
        )
        return {
            "processed_cards": len(results),
            "total_search_target": totals["search_target"],
            "total_search_performed": totals["search_performed"],
            "total_maps_target": totals["maps_target"],
            "total_maps_performed": totals["maps_performed"],
            "total_action_counts": total_action_counts,
            "cards": results,
        }

    def _simulate_search_action(
        self, key_payload: dict[str, object], card_payload: dict[str, object]
    ) -> dict[str, object] | bool:
        """Выполняет полный сценарий поиска организации и активности в ее карточке."""
        phrase = str(key_payload.get("phrase", ""))
        city = str(card_payload.get("city", ""))
        street = str(card_payload.get("street", ""))
        organization = str(card_payload.get("organization", ""))
        map_zoom_clicks = self._to_non_negative_int(card_payload.get("map_zoom_clicks", 0))
        min_sleep_overview = self._to_non_negative_int(card_payload.get("min_sleep_target_overview_sec", 0))
        max_sleep_overview = self._to_non_negative_int(card_payload.get("max_sleep_target_overview_sec", 0))
        if max_sleep_overview < min_sleep_overview:
            max_sleep_overview = min_sleep_overview
        
        query = self._build_query(phrase, city, street)
        if not query:
            self._log("simulate_search_action: пустой запрос, пропуск.")
            return False
        self._log(f"simulate_search_action: старт для запроса '{query}'.")

        browsers_path = self._prepare_runtime_browsers_path()
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

            with sync_playwright() as playwright:
                self._log("simulate_search_action: запускаю браузер и контекст.")
                browser = self._launch_chromium_with_recovery(playwright, browsers_path)
                context = self._create_human_like_context(browser)
                page = context.new_page()
                effect = False
                try:
                    self._log("simulate_search_action: перехожу на ya.ru.")
                    page.goto("https://ya.ru/", wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    search_input = self._get_search_input(page)
                    page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
                    self._type_query_and_submit(page, search_input, query)
                    resolution = self._handle_captcha_if_present(page, context=f"после отправки запроса '{query}'")
                    page.wait_for_timeout(random.randint(2500, 4200))
                    
                    if not resolution.detected or resolution.solved:
                        self._dismiss_distribution_modal(page, context="simulate_search_action")

                    self._open_large_map(page)
                    if self._find_and_open_organization(
                        page,
                        organization,
                        min_sleep_overview,
                        max_sleep_overview,
                    ):
                        effect = True
                        self._log("simulate_search_action: результат достигнут до зума карты.")
                    elif self._run_zoom_search(
                        page,
                        organization,
                        map_zoom_clicks,
                        min_sleep_overview,
                        max_sleep_overview,
                        PlaywrightTimeoutError,
                    ):
                        effect = True
                    else:
                        self._log("simulate_search_action: организация не найдена после всех zoom-итераций.")

                except PlaywrightTimeoutError:
                    self._log("simulate_search_action: таймаут Playwright.")
                    pass
                finally:
                    self._close_browser_session(context, browser, "simulate_search_action")
            self._log(f"simulate_search_action: завершено, effect={effect}.")
            return effect
        except Exception as error:
            if self._is_browser_closed_error(error):
                self._log(
                    f"simulate_search_action: браузер закрыт/потерян ({error}), переоткрою без штрафа."
                )
                return {"effect": False, "actions": {}, "closed": True}
            self._log(f"simulate_search_action: ошибка {error}")
            return False

    _DISTRIBUTION_CLOSE_SELECTORS: tuple[str, ...] = (
        "button.DistributionSplashScreenModalCloseButtonOuter",
        "button[aria-label='Нет, спасибо']",
        ".DistributionButtonClose",
        ".DistributionSplashScreenModalContent .Button_view_clear",
    )

    _PHOTO_VIEWER_CLOSE_SELECTORS: tuple[str, ...] = (
        ".MediaViewer-ButtonClose",
        ".MediaViewer-Close",
        ".MediaViewer button[aria-label='Закрыть']",
        "[class*='MediaViewer'] button[aria-label='Закрыть']",
        "[class*='PhotoViewer'] button[aria-label='Закрыть']",
        "[class*='MediaViewer'] [class*='ButtonClose']",
        "[class*='MediaViewer'] [class*='Close']",
        ".MediaViewerModal .Modal-CloseButton",
        ".PhotosModal-CloseButton",
        ".Gallery-CloseButton",
    )

    _PHOTO_OVERLAY_CLOSE_SELECTORS: tuple[str, ...] = (
        ".OneOrgModal-CloseButton",
        ".OneOrgTabbed_overlay .OneOrgModal-CloseButton",
        ".Modal-Content > .OneOrgModal-CloseButton",
    )

    # Fallback: ищем кнопку закрытия строго внутри оверлея просмотрщика фото,
    # чтобы случайно не закрыть всю карточку организации или карту.
    _CLOSE_MEDIA_VIEWER_JS: str = """
        () => {
            const viewers = Array.from(
                document.querySelectorAll('[class*="MediaViewer"], [class*="PhotoViewer"]')
            );
            if (viewers.length === 0) return false;
            const root = viewers.find(
                (n) => !n.parentElement ||
                    !n.parentElement.closest('[class*="MediaViewer"], [class*="PhotoViewer"]')
            ) || viewers[0];
            const controls = root.querySelectorAll('button, [role="button"]');
            for (const btn of controls) {
                const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                const cls = (typeof btn.className === 'string' ? btn.className : '').toLowerCase();
                const hasCloseIcon = btn.querySelector(
                    '[class*="close" i], [class*="cross" i]'
                );
                if (
                    label.includes('закр') || label.includes('close') ||
                    cls.includes('close') || hasCloseIcon
                ) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }
    """

    def _dismiss_distribution_modal(self, page, context: str = "search") -> None:
        """Закрывает промо-модалку Яндекса (в т.ч. «Установить Яндекс Браузер?»).

        Сначала дожидается полной загрузки страницы, затем по возможности кликает
        реальную кнопку закрытия «Нет, спасибо» (естественное поведение), а при её
        отсутствии удаляет оверлей из DOM. В любом случае в конце 3 раза жмёт Esc
        как страховку на случай других всплывающих окон, закрываемых клавишей.
        """
        self._wait_for_full_load(page)

        if not self._close_distribution_if_present(page, context):
            removed = self._remove_distribution_overlays(page)
            if removed:
                self._log(
                    f"{context}: кнопка закрытия не найдена, удалил промо-оверлеи через JS ({removed} шт.)."
                )

        self._press_escape_safety(page, context)

    def _press_escape_safety(self, page, context: str = "search") -> None:
        """Нажимает Esc 3 раза с интервалом 0.5с как страховку от всплывающих окон."""
        self._log(f"{context}: нажимаю Esc 3 раза с интервалом 0.5с.")
        for _ in range(3):
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            page.wait_for_timeout(500)

    @staticmethod
    def _wait_for_full_load(page) -> None:
        """Ожидает полной загрузки страницы перед закрытием модалок (мягко)."""
        try:
            page.wait_for_load_state("load", timeout=5000)
        except Exception:
            pass

    def _close_distribution_if_present(self, page, context: str = "search") -> bool:
        """Кликает кнопку закрытия промо-модалки, если она видима. Возвращает успех."""
        for selector in self._DISTRIBUTION_CLOSE_SELECTORS:
            try:
                close_btn = page.locator(selector).first
                if close_btn.count() > 0 and close_btn.is_visible():
                    self._log(f"{context}: закрываю промо-модалку кнопкой '{selector}'.")
                    close_btn.click(force=True)
                    page.wait_for_timeout(random.randint(400, 800))
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _remove_distribution_overlays(page) -> int:
        """Удаляет из DOM промо-блоки Distribution и возвращает их число.

        Нужен для отложенного сплэша «Установить Яндекс Браузер?»
        (`DistributionSplashScreenModalContent`): у него нет кнопки закрытия,
        только ссылка «Да», которую жать нельзя. Поэтому такой оверлей просто
        убирается из DOM, чтобы он не перехватывал скроллы и клики.
        """
        script = """
        () => {
            const selectors = [
                '.DistributionSplashScreenModal',
                '.DistributionSplashScreenModalContent',
                '[class*="DistributionSplashScreen"]',
                '.DistributionSplashScreenModalContent .Distribution',
            ];
            let removed = 0;
            const seen = new Set();
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    const root = node.closest('.Modal, .Modal-Content') || node;
                    if (seen.has(root)) continue;
                    seen.add(root);
                    root.remove();
                    removed += 1;
                }
            }
            return removed;
        }
        """
        try:
            return int(page.evaluate(script))
        except Exception:
            return 0

    def _open_large_map(self, page) -> None:
        """Открывает большую карту из поисковой выдачи Яндекса."""
        map_button_locator = page.locator("a.OrgmnColumn-MapButton").first
        map_button_locator.wait_for(state="visible", timeout=5000)
        page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
        map_button_locator.click()
        self._log("simulate_search_action: открыта большая карта.")
        self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия большой карты")
        page.wait_for_timeout(random.randint(2000, 3000))

    def _find_and_open_organization(
        self,
        page,
        organization: str,
        min_sleep_overview: int,
        max_sleep_overview: int,
    ) -> bool:
        """Ищет целевую организацию в списке и открывает ее карточку."""
        if not organization:
            self._log("simulate_search_action: название организации пустое, поиск в списке невозможен.")
            return False
        try:
            page.locator("ul.VerticalOrgsScroller-List").first.wait_for(state="visible", timeout=5000)
        except Exception:
            return False

        last_count = 0
        retries = 0
        while retries < 3:
            items = page.locator("li.VerticalOrgsScroller-Item")
            count = items.count()
            if count <= 0:
                break

            for index in range(last_count, count):
                if self._try_open_organization_item(
                    page,
                    items.nth(index),
                    organization,
                    min_sleep_overview,
                    max_sleep_overview,
                ):
                    return True

            new_count = self._scroll_organization_list(page, items, count)
            if new_count == count:
                retries += 1
            else:
                retries = 0
                last_count = count
        return False

    def _try_open_organization_item(
        self,
        page,
        item_locator,
        organization: str,
        min_sleep_overview: int,
        max_sleep_overview: int,
    ) -> bool:
        """Проверяет один элемент списка организаций и открывает его при совпадении."""
        title_locator = item_locator.locator(".OrgCard-TitleText").first
        if title_locator.count() == 0:
            return False
        actual_title = title_locator.inner_text().strip()
        if organization.lower() not in actual_title.lower():
            return False

        self._log(f"simulate_search_action: найдена организация '{actual_title}'.")
        page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
        self._click_organization_card(item_locator, title_locator)
        if not self._wait_for_organization_card_opened(page, organization):
            self._log(
                f"simulate_search_action: карточка '{actual_title}' не открылась после клика, пропускаю."
            )
            return False
        self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия карточки организации")
        self._run_target_overview_activity(page, min_sleep_overview, max_sleep_overview)
        return True

    @staticmethod
    def _click_organization_card(item_locator, title_locator) -> None:
        """Кликает по карточке организации, открывая её в текущей вкладке.

        Приоритет — ссылка-заголовок `.OrgCard-Title` (`target="_self"`), которая
        открывает карточку в этой же вкладке. Оверлей `.OrgCard-Overlay` имеет
        `target="_blank"` и может открыть организацию в новой вкладке, поэтому он
        используется лишь как крайний фолбэк.
        """
        candidates = (
            item_locator.locator(".OrgCard-Title").first,
            title_locator,
            item_locator.locator(".OrgCard-Overlay").first,
        )
        for locator in candidates:
            try:
                if locator.count() > 0:
                    locator.click(force=True)
                    return
            except Exception:
                continue

    def _wait_for_organization_card_opened(self, page, organization: str) -> bool:
        """Подтверждает, что открылась карточка ИМЕННО нужной организации.

        В модалке карты выбранная организация показывается в панели
        `.CompaniesModal-OneOrg` с заголовком `.OrgHeader-Title`. Ждём появления
        этого заголовка и совпадения его текста с целью, чтобы не засчитать
        чужую авто-открытую карточку и не уйти в скроллы по списку/выдаче.
        """
        needle = organization.strip().lower()
        header = page.locator(".OrgHeader-Title").first
        deadline_ms = 8000
        step_ms = 500
        waited = 0
        while waited <= deadline_ms:
            try:
                if header.count() > 0 and header.is_visible():
                    title = header.inner_text().strip().lower()
                    if not needle or needle in title:
                        return True
            except Exception:
                pass
            page.wait_for_timeout(step_ms)
            waited += step_ms
        return False

    @staticmethod
    def _scroll_organization_list(page, items, count: int) -> int:
        """Прокручивает список организаций к последнему элементу и возвращает новый размер списка."""
        items.nth(count - 1).scroll_into_view_if_needed()
        page.wait_for_timeout(random.randint(1000, 1500))
        return items.count()

    def _run_zoom_search(
        self,
        page,
        organization: str,
        map_zoom_clicks: int,
        min_sleep_overview: int,
        max_sleep_overview: int,
        timeout_error_type,
    ) -> bool:
        """Ищет организацию после серии zoom-действий на карте."""
        self._log("simulate_search_action: организация не найдена, начинаю zoom-итерации.")
        for step in range(map_zoom_clicks):
            self._log(f"simulate_search_action: zoom-итерация {step + 1}/{map_zoom_clicks}.")
            self._apply_zoom_step(page, step, timeout_error_type)
            if self._find_and_open_organization(
                page,
                organization,
                min_sleep_overview,
                max_sleep_overview,
            ):
                self._log("simulate_search_action: организация найдена после zoom-итерации.")
                return True
        return False

    def _apply_zoom_step(self, page, step: int, timeout_error_type) -> None:
        """Выполняет один шаг изменения масштаба карты."""
        zoom_in_btn = page.locator("button:has(.ymaps3--zoom-control__in)").first
        zoom_out_btn = page.locator("button:has(.ymaps3--zoom-control__out)").first
        if step == 0:
            self._click_zoom_button(page, zoom_in_btn, timeout_error_type, wait_after=True)
            self._click_zoom_button(page, zoom_out_btn, timeout_error_type)
        elif step == 1:
            self._click_zoom_button(page, zoom_in_btn, timeout_error_type, wait_after=True)
            self._click_zoom_button(page, zoom_in_btn, timeout_error_type)
        else:
            self._click_zoom_button(page, zoom_in_btn, timeout_error_type, wait_before=True)
        self._handle_captcha_if_present(page, wait_ms=2000, context=f"после zoom-итерации #{step + 1}")

    @staticmethod
    def _click_zoom_button(
        page,
        button,
        timeout_error_type,
        wait_before: bool = False,
        wait_after: bool = False,
    ) -> None:
        """Кликает по кнопке zoom и мягко ждет загрузку карты."""
        if button.count() == 0:
            return
        page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
        if wait_before:
            page.wait_for_timeout(random.choice([1450, 1560]))
        button.click()
        if wait_after:
            page.wait_for_timeout(random.choice([1450, 1560]))
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
            if wait_after:
                page.wait_for_timeout(random.choice([1450, 1560]))
        except timeout_error_type:
            page.wait_for_timeout(1500 if wait_after else 2000)

    def _run_target_overview_activity(
        self,
        page,
        min_sleep_overview: int,
        max_sleep_overview: int,
    ) -> None:
        """Имитирует изучение карточки организации: скроллы, фото, отзывы и паузы."""
        if max_sleep_overview <= 0:
            page.wait_for_timeout(random.randint(1000, 2000))
            return

        sleep_time_sec = random.randint(min_sleep_overview, max_sleep_overview)
        self._log(f"simulate_search_action: имитация активности в карточке {sleep_time_sec} сек.")
        # Внутри модалки карты НЕ жмём Esc и не трогаем общие кнопки закрытия —
        # это закрыло бы саму карту. Гасим только промо-сплэш по спец-селекторам.
        self._close_distribution_if_present(page, context="overview")
        end_time = page.evaluate("Date.now()") + (sleep_time_sec * 1000)
        visited_sections: set[str] = set()
        actions = {
            "scroll": lambda: self._browse_card_screen(page),
            "click_photos": lambda: self._browse_photo_section(page, visited_sections),
            "click_reviews": lambda: self._browse_reviews_section(page, visited_sections),
            "idle": lambda: self._idle_on_current_screen(page),
        }

        while page.evaluate("Date.now()") < end_time:
            self._close_distribution_if_present(page, context="overview")
            self._scroll_visible_content(
                page,
                random.randint(200, 500) * random.choice([1, 1, -1]),
            )
            page.wait_for_timeout(random.randint(1000, 2000))
            action = self._choose_overview_action(visited_sections)
            actions[action]()
            page.wait_for_timeout(random.randint(500, 1500))

    @staticmethod
    def _choose_overview_action(visited_sections: set[str]) -> str:
        """Выбирает следующее действие в карточке без повторного открытия разделов."""
        action = random.choice(["scroll", "scroll", "click_photos", "click_reviews", "idle"])
        blocked_actions = {
            "click_photos": "photos",
            "click_reviews": "reviews",
        }
        if blocked_actions.get(action) in visited_sections:
            return "scroll"
        return action

    def _browse_card_screen(self, page) -> None:
        """Скроллит открытую карточку организации и делает паузу на чтение."""
        scroll_amount = random.randint(300, 700) * random.choice([1, 1, -1])
        self._log(f"Делаю скролл карточки на {scroll_amount} px (читаю информацию)")
        self._scroll_visible_content(page, scroll_amount)
        page.wait_for_timeout(random.randint(1500, 4000))

    def _browse_photo_section(self, page, visited_sections: set[str]) -> None:
        """Открывает блок фото, рассматривает отдельные снимки и закрывает блок."""
        try:
            # Плитка/кнопка в карточке, открывающая блок «Фото и видео».
            opener = page.locator(
                ".PhotoTiles-More button, .OrgGallery-PhotoTiles .PhotoTiles-Item, "
                ".OrgGallery .PhotoTiles-Item, .PhotoTiles-Item"
            ).first
            if opener.count() == 0:
                return
            self._log("Заметил раздел с фотографиями, скроллю к нему...")
            opener.scroll_into_view_if_needed()
            page.wait_for_timeout(random.randint(1000, 2500))
            if not opener.is_visible():
                return

            visited_sections.add("photos")
            section_start = time.time()
            self._log("Открываю блок с фотографиями...")
            opener.click(force=True)
            self._handle_captcha_if_present(page, wait_ms=2000, context="после открытия раздела фото")
            page.wait_for_timeout(random.randint(1500, 3000))

            self._scroll_photo_gallery(page)
            # Обязательно закрываем сам блок фото (оверлей OneOrgModal),
            # чтобы вернуться к карточке организации.
            self._close_photo_overlay(page, section_start)
        except Exception:
            return

    def _scroll_photo_gallery(self, page) -> None:
        """Листает сетку фото и открывает несколько РАЗНЫХ снимков в лайтбоксе."""
        scroll_steps = random.randint(3, 6)
        self._log(f"Делаю {scroll_steps} скроллов по сетке фото, рассматривая снимки...")
        opened: set[int] = set()
        for step in range(scroll_steps):
            self._scroll_visible_content(page, random.randint(200, 600))
            page.wait_for_timeout(random.randint(2000, 4000))
            # Часть проходов открываем конкретное фото и закрываем лайтбокс.
            if step == 0 or random.random() < 0.5:
                self._open_random_gallery_photo(page, opened)

    def _open_random_gallery_photo(self, page, opened: set[int] | None = None) -> None:
        """Открывает ещё не просмотренную фотографию из сетки и закрывает лайтбокс."""
        try:
            inner_photos = page.locator(
                ".MediaGrid-Item, .MediaGridItem, .MediaGallery-Item, .Gallery-Item"
            )
            count = inner_photos.count()
            if count <= 0:
                self._log("Не нашёл отдельных фотографий для увеличения.")
                return
            # Выбираем индекс, который ещё не открывали, чтобы не кликать
            # повторно по одному и тому же снимку.
            limit = min(count, 14)
            candidates = [i for i in range(limit) if opened is None or i not in opened]
            if not candidates:
                return
            idx = random.choice(candidates)
            if opened is not None:
                opened.add(idx)
            photo_to_click = inner_photos.nth(idx)
            if not photo_to_click.is_visible():
                return
            self._log(f"Кликаю на конкретное фото №{idx + 1} для увеличения...")
            photo_to_click.scroll_into_view_if_needed()
            photo_to_click.click(force=True)
            self._handle_captcha_if_present(page, wait_ms=2000, context=f"после открытия фото №{idx + 1}")
            view_time = random.randint(3000, 7000)
            self._log(f"Рассматриваю увеличенное фото ({view_time} мс)...")
            page.wait_for_timeout(view_time)
            self._close_photo_viewer(page)
            page.wait_for_timeout(random.randint(1000, 2000))
        except Exception:
            return

    def _close_photo_overlay(self, page, section_start: float) -> None:
        """Закрывает блок «Фото и видео» (оверлей OneOrgModal) по его кнопке-крестику."""
        for selector in self._PHOTO_OVERLAY_CLOSE_SELECTORS:
            try:
                close_btn = page.locator(selector).first
                if close_btn.count() > 0 and close_btn.is_visible():
                    self._log(f"Закрываю блок с фото кнопкой '{selector}'.")
                    close_btn.click(force=True)
                    page.wait_for_timeout(random.randint(1000, 2000))
                    spent = time.time() - section_start
                    self._log(f"Закрыл раздел фото. Время пребывания: {spent:.1f} сек.")
                    return
            except Exception:
                continue
        self._log("Кнопка закрытия блока фото не найдена, оставляю как есть.")

    def _close_photo_viewer(self, page) -> None:
        """Закрывает увеличенное фото только кнопкой лайтбокса.

        Esc внутри модалки карты закрыл бы всю карточку, поэтому используем
        исключительно специфичные для просмотрщика селекторы.
        """
        for selector in self._PHOTO_VIEWER_CLOSE_SELECTORS:
            try:
                close_btn = page.locator(selector).first
                if close_btn.count() > 0 and close_btn.is_visible():
                    self._log(f"Закрываю увеличенное фото кнопкой '{selector}'.")
                    close_btn.click(force=True)
                    return
            except Exception:
                continue
        # Запасной путь: ищем кнопку закрытия прямо внутри оверлея просмотрщика
        # (тема fiji и др.), не задевая кнопку закрытия карточки/карты.
        try:
            if page.evaluate(self._CLOSE_MEDIA_VIEWER_JS):
                self._log("Закрыл увеличенное фото кнопкой внутри просмотрщика (JS).")
                return
        except Exception:
            pass
        self._log("Кнопка закрытия фото не найдена, оставляю просмотрщик как есть.")

    def _browse_reviews_section(self, page, visited_sections: set[str]) -> None:
        """Открывает отзывы, листает их и иногда разворачивает длинный текст."""
        try:
            reviews_btn = page.locator(".ReviewMoreButton-Button, .TabsMenu-Tab:has-text('Отзывы')").first
            if reviews_btn.count() == 0:
                return
            self._log("Заметил раздел с отзывами, скроллю к нему...")
            reviews_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(random.randint(1000, 2500))
            if not reviews_btn.is_visible():
                return

            visited_sections.add("reviews")
            section_start = time.time()
            self._log("Открываю раздел отзывы...")
            reviews_btn.click()
            self._handle_captcha_if_present(page, wait_ms=2000, context="после открытия раздела отзывов")
            wait_time = random.randint(1000, 3000)
            self._log(f"Читаю первые отзывы ({wait_time} мс)...")
            page.wait_for_timeout(wait_time)
            self._scroll_reviews(page)
            self._close_section(
                page,
                ".ReviewViewer-CloseButton, .OneOrgModal-CloseButton",
                "отзывы",
                section_start,
            )
        except Exception:
            return

    def _scroll_reviews(self, page) -> None:
        """Медленно листает отзывы и иногда нажимает 'Читать полностью'."""
        scroll_steps = random.randint(4, 8)
        self._log(f"Делаю {scroll_steps} скроллов вниз, вдумчиво читаю отзывы...")
        for _ in range(scroll_steps):
            self._scroll_visible_content(page, random.randint(300, 800))
            read_time = random.randint(4000, 8000)
            self._log(f"Читаю отзывы на экране ({read_time} мс)...")
            page.wait_for_timeout(read_time)
            if random.random() < 0.3:
                self._expand_random_review(page)

    def _expand_random_review(self, page) -> None:
        """Пытается раскрыть один длинный отзыв (кнопка «Читать ещё»)."""
        try:
            expand_btns = page.locator(
                ".ReviewViewer-Review .Review-Text .Cut-More [role='button'], "
                ".Review-Text .Cut-More .Link, .Cut-More .Cut-MoreToggler, "
                ".Review-MoreText, .ReviewText-More, .BusinessReviews-MoreText"
            )
            count = expand_btns.count()
            if count <= 0:
                return
            btn = expand_btns.nth(random.randint(0, count - 1))
            if not btn.is_visible():
                return
            self._log("Разворачиваю длинный отзыв ('Читать ещё')...")
            btn.click(force=True)
            self._handle_captcha_if_present(page, wait_ms=2000, context="после раскрытия длинного отзыва")
            page.wait_for_timeout(random.randint(3000, 6000))
        except Exception:
            return

    def _close_section(self, page, close_selector: str, section_name: str, section_start: float) -> None:
        """Закрывает открытый раздел карточки и логирует время пребывания в нем."""
        close_btn = page.locator(close_selector).first
        if close_btn.count() > 0 and close_btn.is_visible():
            close_btn.click()
            page.wait_for_timeout(random.randint(1000, 2000))
        spent = time.time() - section_start
        self._log(f"Закрываю раздел {section_name}. Время пребывания: {spent:.1f} сек.")

    def _idle_on_current_screen(self, page) -> None:
        """Оставляет текущий экран без действий, как будто пользователь читает его."""
        idle_time = random.randint(4000, 10000)
        self._log(f"Изучаю текущий экран (бездействие {idle_time / 1000:.1f} сек)...")
        page.wait_for_timeout(idle_time)

    def _simulate_browser_action_one_second(
        self,
        key_payload: dict[str, object],
        card_payload: dict[str, object],
        action_budget: _ActionBudget | None = None,
    ) -> dict[str, object]:
        """Открывает Яндекс.Карты и выполняет там запрос (только по фразе/ключу)."""
        phrase = str(key_payload.get("phrase", ""))
        query = phrase.strip()
        if not query:
            self._log("simulate_maps_action: пустой запрос, пропуск.")
            return {"effect": False, "actions": {}}

        map_zoom_clicks = self._to_non_negative_int(card_payload.get("map_zoom_clicks", 0))
        competitor_open_chance_percent = max(
            0,
            min(100, self._to_non_negative_int(card_payload.get("competitor_open_chance_percent", 0))),
        )
        max_open_competitor_cards = self._to_non_negative_int(card_payload.get("max_open_competitor_cards", 0))
        min_sleep_competitor_card_sec = self._to_non_negative_int(
            card_payload.get("min_sleep_competitor_card_sec", 0)
        )
        max_sleep_competitor_card_sec = self._to_non_negative_int(
            card_payload.get("max_sleep_competitor_card_sec", 0)
        )
        if max_sleep_competitor_card_sec < min_sleep_competitor_card_sec:
            max_sleep_competitor_card_sec = min_sleep_competitor_card_sec
        min_sleep_target_tab_sec = self._to_non_negative_int(card_payload.get("min_sleep_target_tab_sec", 0))
        max_sleep_target_tab_sec = self._to_non_negative_int(card_payload.get("max_sleep_target_tab_sec", 0))
        if max_sleep_target_tab_sec < min_sleep_target_tab_sec:
            max_sleep_target_tab_sec = min_sleep_target_tab_sec
        # Если бюджет не передан (например, прямой вызов), считаем лимиты только
        # для этого перехода из настроек карточки.
        if action_budget is None:
            action_budget = self._build_action_budget(card_payload)
        self._log(f"simulate_maps_action: старт для запроса '{query}'.")
        maps_url = self._build_maps_url(card_payload)
        browsers_path = self._prepare_runtime_browsers_path()
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = self._launch_chromium_with_recovery(playwright, browsers_path)
                context = self._create_human_like_context(browser)
                page = context.new_page()
                self._log(f"simulate_maps_action: перехожу по ссылке {maps_url}")
                page.goto(maps_url, wait_until="domcontentloaded")
                self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия карт")
                maps_search_input = self._get_maps_search_input(page)
                self._type_query_and_submit(page, maps_search_input, query)
                self._handle_captcha_if_present(page, context=f"после отправки maps-запроса '{query}'")
                try:
                    page.wait_for_load_state("networkidle", timeout=7000)
                except Exception:
                    page.wait_for_timeout(1000)
                
                organization = str(card_payload.get("organization", ""))
                found = self._find_and_open_maps_organization(
                    page,
                    organization,
                    map_zoom_clicks,
                    PlaywrightTimeoutError,
                )

                action_counts: dict[str, int] = {}
                if not found:
                    self._log("simulate_maps_action: организация не найдена на картах.")
                else:
                    action_counts = self._run_maps_card_activity(
                        page,
                        action_budget=action_budget,
                        min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                        max_sleep_target_tab_sec=max_sleep_target_tab_sec,
                    )
                    self._run_competitor_card_activity(
                        page,
                        chance_percent=competitor_open_chance_percent,
                        max_open_cards=max_open_competitor_cards,
                        min_sleep_sec=min_sleep_competitor_card_sec,
                        max_sleep_sec=max_sleep_competitor_card_sec,
                    )

                self._close_browser_session(context, browser, "simulate_browser_action_one_second")
            self._log("simulate_maps_action: завершено успешно.")
            return {"effect": True, "actions": action_counts}
        except Exception as error:
            if self._is_browser_closed_error(error):
                self._log(
                    f"simulate_maps_action: браузер закрыт/потерян ({error}), переоткрою без штрафа."
                )
                return {"effect": False, "actions": {}, "closed": True}
            self._log(f"simulate_maps_action: ошибка {error}")
            return {"effect": False, "actions": {}}

    def _find_and_open_maps_organization(
        self,
        page,
        organization: str,
        map_zoom_clicks: int,
        timeout_error_type,
    ) -> bool:
        """Ищет целевую организацию в списке на странице Яндекс.Карт и кликает по ней."""
        if not organization:
            self._log("simulate_maps_action: название организации пустое, поиск в списке невозможен.")
            return False

        if self._find_and_open_maps_organization_in_list(page, organization):
            return True
        for step in range(map_zoom_clicks):
            self._log(f"simulate_maps_action: zoom-итерация в картах {step + 1}/{map_zoom_clicks}.")
            self._apply_zoom_step(page, step, timeout_error_type)
            if self._find_and_open_maps_organization_in_list(page, organization):
                self._log("simulate_maps_action: организация найдена после zoom-итерации в картах.")
                return True
        return False

    def _find_and_open_maps_organization_in_list(self, page, organization: str) -> bool:
        """Ищет и открывает карточку организации в текущем списке результатов карт."""
        try:
            page.locator("ul.search-list-view__list").first.wait_for(state="visible", timeout=5000)
        except Exception:
            return False

        last_count = 0
        retries = 0
        while retries < 3:
            items = page.locator("li.search-snippet-view")
            count = items.count()
            if count <= 0:
                break

            for index in range(last_count, count):
                item_locator = items.nth(index)
                title_locator = item_locator.locator(".search-business-snippet-view__title").first
                if title_locator.count() == 0:
                    continue
                actual_title = title_locator.inner_text().strip()
                if organization.lower() not in actual_title.lower():
                    continue
                self._log(f"simulate_maps_action: найдена организация '{actual_title}'.")
                page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
                try:
                    title_locator.click(force=True)
                except Exception:
                    return False
                self._handle_captcha_if_present(
                    page,
                    wait_ms=3000,
                    context="после открытия карточки организации на картах",
                )
                return True

            items.nth(count - 1).scroll_into_view_if_needed()
            page.wait_for_timeout(random.randint(1000, 1500))
            new_count = items.count()
            if new_count == count:
                retries += 1
            else:
                retries = 0
                last_count = count
        return False

    def _run_maps_card_activity(
        self,
        page,
        *,
        action_budget: _ActionBudget,
        min_sleep_target_tab_sec: int,
        max_sleep_target_tab_sec: int,
    ) -> dict[str, int]:
        """Выполняет целевые действия в карточке на картах в случайном порядке/объеме.

        Число попыток каждого действия резервируется из общего на карточку бюджета,
        поэтому суммарное число выполненных действий по всем переходам карты не
        превышает заданный в настройках лимит. Возвращает словарь
        {действие: число успешно выполненных} для статистики.
        """
        click_plan = self._build_budgeted_maps_action_plan(action_budget)
        action_counts: dict[str, int] = {}
        for action_label, attempts in click_plan:
            performed = self._perform_maps_action_clicks(
                page,
                action_label,
                attempts,
                min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                max_sleep_target_tab_sec=max_sleep_target_tab_sec,
            )
            performed = max(0, int(performed or 0))
            # Возвращаем в бюджет зарезервированные, но не выполненные клики.
            action_budget.settle(action_label, reserved=attempts, performed=performed)
            if performed:
                action_counts[action_label] = action_counts.get(action_label, 0) + performed
        self._sleep_in_range_seconds(
            page,
            min_sleep_target_tab_sec,
            max_sleep_target_tab_sec,
            "simulate_maps_action: нахожусь в целевой карточке",
        )
        return action_counts

    @staticmethod
    def _build_budgeted_maps_action_plan(action_budget: _ActionBudget) -> list[tuple[str, int]]:
        """Строит план одного перехода, резервируя попытки из общего бюджета карточки.

        Для каждого действия резервируется случайная часть оставшегося лимита
        (0..remaining). Действия с нулевым резервом в план не попадают, порядок
        перемешивается. План может быть пустым — на большинстве переходов целевые
        действия не выполняются, что и обеспечивает соблюдение суммарного лимита.
        """
        plan: list[tuple[str, int]] = []
        for action_label in _ActionBudget.ACTION_ORDER:
            reserved = action_budget.reserve(action_label)
            if reserved > 0:
                plan.append((action_label, reserved))
        random.shuffle(plan)
        return plan

    def _run_competitor_card_activity(
        self,
        page,
        *,
        chance_percent: int,
        max_open_cards: int,
        min_sleep_sec: int,
        max_sleep_sec: int,
    ) -> None:
        """Иногда открывает карточки конкурентов и задерживается на них."""
        if max_open_cards <= 0 or chance_percent <= 0:
            return
        roll = random.randint(1, 100)
        if roll > chance_percent:
            self._log(
                f"simulate_maps_action: карточки конкурентов пропущены (шанс={chance_percent}%, roll={roll})."
            )
            return

        items = page.locator("li.search-snippet-view")
        count = items.count()
        if count <= 1:
            return
        open_limit = min(max_open_cards, max(0, count - 1))
        for index in range(1, open_limit + 1):
            item_locator = items.nth(index)
            title_locator = item_locator.locator(".search-business-snippet-view__title").first
            if title_locator.count() == 0:
                continue
            try:
                title = title_locator.inner_text().strip()
            except Exception:
                title = ""
            try:
                title_locator.click(force=True)
            except Exception:
                continue
            self._handle_captcha_if_present(
                page,
                wait_ms=2500,
                context=f"после открытия карточки конкурента #{index}",
            )
            self._log(
                f"simulate_maps_action: открыта карточка конкурента {index}/{open_limit} '{title}'."
            )
            self._sleep_in_range_seconds(
                page,
                min_sleep_sec,
                max_sleep_sec,
                "simulate_maps_action: нахожусь в карточке конкурента",
            )

    def _perform_maps_action_clicks(
        self,
        page,
        action_label: str,
        attempts: int,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> int:
        """Пытается кликнуть по кнопкам действий в карточке. Возвращает число успехов."""
        if attempts <= 0:
            return 0
        if action_label == "Сайт":
            return self._perform_maps_website_clicks(
                page,
                attempts,
                min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                max_sleep_target_tab_sec=max_sleep_target_tab_sec,
            )
        if action_label == "Показать телефон":
            return self._perform_maps_phone_clicks(
                page,
                attempts,
                min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                max_sleep_target_tab_sec=max_sleep_target_tab_sec,
            )
        if action_label == "Маршрут":
            return self._perform_maps_route_clicks(
                page,
                attempts,
                min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                max_sleep_target_tab_sec=max_sleep_target_tab_sec,
            )
        if action_label == "мессенджер":
            return self._perform_maps_messenger_clicks(
                page,
                attempts,
                min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                max_sleep_target_tab_sec=max_sleep_target_tab_sec,
            )
        if action_label == "Записаться":
            return self._perform_maps_cta_clicks(
                page,
                attempts,
                min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                max_sleep_target_tab_sec=max_sleep_target_tab_sec,
            )
        locator = self._maps_action_locator(page, action_label)
        total = locator.count()
        if total <= 0:
            self._log(f"simulate_maps_action: действие '{action_label}' недоступно в карточке.")
            return 0

        success = 0
        for _ in range(attempts):
            if locator.count() <= 0:
                break
            try:
                target = locator.first
                target.click(force=True)
                success += 1
                page.wait_for_timeout(random.randint(400, 1200))
                self._handle_captcha_if_present(
                    page,
                    wait_ms=1500,
                    context=f"после клика '{action_label}'",
                )
            except Exception:
                continue
        self._log(
            f"simulate_maps_action: клики '{action_label}' выполнены {success}/{attempts}."
        )
        return success

    @staticmethod
    def _maps_action_locator(page, action_label: str):
        """Возвращает locator для кликов по стандартным действиям карточки."""
        if action_label == "Показать телефон":
            return page.locator(
                "div.card-phones-view__more-wrapper[role='button'], "
                "div.card-phones-view__more-wrapper, "
                "[role='button']:has(.card-phones-view__more), "
                "[role='button']:has-text('Показать телефон')"
            )
        if action_label == "Маршрут":
            return page.locator(
                "button[role='button']:has-text('Маршрут'), button:has-text('Маршрут'), "
                "a:has-text('Маршрут'), [role='link']:has-text('Маршрут')"
            )
        return page.locator(f"button:has-text('{action_label}'), a:has-text('{action_label}')")

    def _perform_maps_website_clicks(
        self,
        page,
        attempts: int,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> int:
        """Кликает по ссылке сайта в блоке контактов, если сайт указан у организации."""
        locator = page.locator("a[itemprop='url'][href]")
        total = locator.count()
        if total <= 0:
            self._log("simulate_maps_action: сайт в контактах отсутствует, клики 'Сайт' пропущены.")
            return 0

        success = 0
        for _ in range(attempts):
            if locator.count() <= 0:
                break
            previous_url = getattr(page, "url", "")
            pages_before = self._count_context_pages(page)
            try:
                locator.first.click(force=True)
                success += 1
                page.wait_for_timeout(random.randint(400, 1200))
                self._close_new_tab_if_opened(
                    page,
                    pages_before,
                    "Сайт",
                    min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                    max_sleep_target_tab_sec=max_sleep_target_tab_sec,
                )
                self._handle_captcha_if_present(
                    page,
                    wait_ms=1500,
                    context="после клика 'Сайт'",
                )
                self._restore_maps_page_after_action(page, previous_url, "Сайт")
            except Exception:
                continue
        self._log(f"simulate_maps_action: клики 'Сайт' выполнены {success}/{attempts}.")
        return success

    def _perform_maps_phone_clicks(
        self,
        page,
        attempts: int,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> int:
        """Кликает по 'Показать телефон' и засчитывает только подтвержденное раскрытие."""
        success = 0
        for _ in range(attempts):
            before_count = self._count_show_phone_controls(page)
            visible_phone_before = self._count_visible_phone_values(page)
            if before_count <= 0:
                break

            locator = self._maps_action_locator(page, "Показать телефон")
            if locator.count() <= 0:
                break
            try:
                locator.first.click(force=True)
                page.wait_for_timeout(random.randint(400, 1200))
                self._handle_captcha_if_present(
                    page,
                    wait_ms=1200,
                    context="после клика 'Показать телефон'",
                )
                after_count = self._count_show_phone_controls(page)
                visible_phone_after = self._count_visible_phone_values(page)
                # Успех: контрол раскрылся, либо телефон уже был видим и клик выполнен.
                if (
                    after_count < before_count
                    or visible_phone_after > visible_phone_before
                    or visible_phone_before > 0
                ):
                    success += 1
                    self._sleep_in_range_seconds(
                        page,
                        min_sleep_target_tab_sec,
                        max_sleep_target_tab_sec,
                        "simulate_maps_action: пауза после 'Показать телефон'",
                    )
            except Exception:
                continue
        self._log(f"simulate_maps_action: клики 'Показать телефон' выполнены {success}/{attempts}.")
        return success

    @staticmethod
    def _count_show_phone_controls(page) -> int:
        """Возвращает число видимых контролов с текстом 'Показать телефон'."""
        locator = page.locator(
            "div.card-phones-view__more-wrapper, "
            "div.card-phones-view__more:has-text('Показать телефон'), "
            "[role='button']:has-text('Показать телефон')"
        )
        return locator.count()

    @staticmethod
    def _count_visible_phone_values(page) -> int:
        """Возвращает количество видимых значений телефона в карточке."""
        return page.locator("[itemprop='telephone']").count()

    def _perform_maps_route_clicks(
        self,
        page,
        attempts: int,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> int:
        """Устойчиво кликает по кнопке 'Маршрут' через несколько безопасных локаторов."""
        locators = [
            page.locator("button[role='button']:has-text('Маршрут')"),
            page.locator("[role='button']:has-text('Маршрут')"),
            page.locator("button:has-text('Маршрут')"),
        ]
        if all(locator.count() <= 0 for locator in locators):
            self._log("simulate_maps_action: действие 'Маршрут' недоступно в карточке.")
            return 0

        success = 0
        for _ in range(attempts):
            clicked = False
            for locator in locators:
                if locator.count() <= 0:
                    continue
                target = locator.first
                previous_url = getattr(page, "url", "")
                try:
                    target.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    target.click(timeout=3000)
                    clicked = True
                except Exception:
                    try:
                        target.click(force=True, timeout=3000)
                        clicked = True
                    except Exception:
                        continue
                if clicked:
                    success += 1
                    page.wait_for_timeout(random.randint(400, 1200))
                    self._sleep_in_range_seconds(
                        page,
                        min_sleep_target_tab_sec,
                        max_sleep_target_tab_sec,
                        "simulate_maps_action: пауза после 'Маршрут'",
                    )
                    self._handle_captcha_if_present(
                        page,
                        wait_ms=1500,
                        context="после клика 'Маршрут'",
                    )
                    self._restore_maps_page_after_action(page, previous_url, "Маршрут")
                    break
        self._log(f"simulate_maps_action: клики 'Маршрут' выполнены {success}/{attempts}.")
        return success

    def _perform_maps_messenger_clicks(
        self,
        page,
        attempts: int,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> int:
        """Выбирает один случайный мессенджер и кликает по нему заданное число раз."""
        locator = page.locator("a[itemprop='sameAs'][href]")
        total = locator.count()
        if total <= 0:
            self._log("simulate_maps_action: соцсети/мессенджеры в контактах отсутствуют.")
            return 0

        selected_index = random.randint(0, total - 1)
        self._log(
            f"simulate_maps_action: выбран мессенджер/соцсеть #{selected_index + 1} из {total}."
        )
        success = 0
        for _ in range(attempts):
            available = locator.count()
            if available <= 0:
                break
            previous_url = getattr(page, "url", "")
            pages_before = self._count_context_pages(page)
            try:
                target = locator.nth(min(selected_index, available - 1))
                target.click(force=True)
                success += 1
                page.wait_for_timeout(random.randint(400, 1200))
                self._close_new_tab_if_opened(
                    page,
                    pages_before,
                    "мессенджер",
                    min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                    max_sleep_target_tab_sec=max_sleep_target_tab_sec,
                )
                self._handle_captcha_if_present(
                    page,
                    wait_ms=1500,
                    context="после клика в мессенджер/соцсеть",
                )
                self._restore_maps_page_after_action(page, previous_url, "мессенджер")
            except Exception:
                continue
        self._log(f"simulate_maps_action: клики в мессенджеры выполнены {success}/{attempts}.")
        return success

    # Текстовые исключения для не-CTA действий (Маршрут/Показать телефон тоже имеют action-button-view__icon).
    _NON_CTA_TEXT_EXCLUDES = ":not(:has-text('Маршрут')):not(:has-text('Показать телефон'))"

    @staticmethod
    def _maps_cta_locator(page):
        """Locator CTA-кнопки заведения (Забронировать/Заказать/Записаться) с приоритетом точных признаков."""
        excludes = SearchRunnerService._NON_CTA_TEXT_EXCLUDES

        # 1) Точный контейнер призыва к действию — только CTA-кнопки заведения.
        container_locator = page.locator(
            ".business-card-title-view__call-to-action [role='button']"
        )
        if container_locator.count() > 0:
            return container_locator

        # 2) Стиль "announcement" — устойчивый признак CTA (в отличие от _view_primary у "Маршрут").
        announcement_locator = page.locator(
            "button._view_announcement[role='button'], a._view_announcement[role='button'], "
            "button._view_announcement, a._view_announcement"
        )
        if announcement_locator.count() > 0:
            return announcement_locator

        # 3) Якорь на иконку action-button, но исключая стандартные не-CTA действия.
        icon_locator = page.locator(
            f"button:has(.action-button-view__icon){excludes}, "
            f"a:has(.action-button-view__icon){excludes}, "
            f"[role='button']:has(.action-button-view__icon){excludes}"
        )
        if icon_locator.count() > 0:
            return icon_locator

        # 4) Семантический фолбэк по внешней ссылке-кнопке (исключая сайт/мессенджеры).
        semantic_locator = page.locator(
            "a[role='button'][target='_blank'][rel*='nofollow']:not([itemprop='url']):not([itemprop='sameAs'])"
        )
        if semantic_locator.count() > 0:
            return semantic_locator

        # 5) Широкий фолбэк по кнопке, исключая явные не-CTA действия.
        return page.locator(f"button[role='button']{excludes}")

    def _perform_maps_cta_clicks(
        self,
        page,
        attempts: int,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> int:
        """Кликает по CTA-кнопке заведения. Якоримся на стабильный контейнер CTA."""
        locator = self._maps_cta_locator(page)
        total = locator.count()
        if total <= 0:
            self._log("simulate_maps_action: действие CTA недоступно в карточке.")
            return 0

        success = 0
        for _ in range(attempts):
            if locator.count() <= 0:
                break
            previous_url = getattr(page, "url", "")
            pages_before = self._count_context_pages(page)
            try:
                locator.first.click(force=True)
                success += 1
                page.wait_for_timeout(random.randint(400, 1200))
                self._close_new_tab_if_opened(
                    page,
                    pages_before,
                    "CTA",
                    min_sleep_target_tab_sec=min_sleep_target_tab_sec,
                    max_sleep_target_tab_sec=max_sleep_target_tab_sec,
                )
                self._handle_captcha_if_present(
                    page,
                    wait_ms=1500,
                    context="после клика CTA",
                )
                self._restore_maps_page_after_action(page, previous_url, "CTA")
                self._sleep_in_range_seconds(
                    page,
                    min_sleep_target_tab_sec,
                    max_sleep_target_tab_sec,
                    "simulate_maps_action: пауза после CTA",
                )
            except Exception:
                continue
        self._log(f"simulate_maps_action: клики CTA выполнены {success}/{attempts}.")
        return success

    @staticmethod
    def _count_context_pages(page) -> int:
        """Возвращает число вкладок в текущем контексте, если доступно."""
        context = getattr(page, "context", None)
        if context is None:
            return 0
        pages = getattr(context, "pages", None)
        if pages is None:
            return 0
        try:
            return len(pages)
        except Exception:
            return 0

    def _close_new_tab_if_opened(
        self,
        page,
        pages_before: int,
        action_label: str,
        *,
        min_sleep_target_tab_sec: int = 0,
        max_sleep_target_tab_sec: int = 0,
    ) -> None:
        """Закрывает новую вкладку, если действие открыло внешний сайт/мессенджер."""
        context = getattr(page, "context", None)
        if context is None:
            return
        pages = getattr(context, "pages", None)
        if pages is None:
            return
        try:
            current_pages = list(pages)
        except Exception:
            return
        if len(current_pages) <= pages_before:
            return
        for candidate in reversed(current_pages):
            if candidate is page:
                continue
            try:
                safe_min = max(0, min_sleep_target_tab_sec)
                safe_max = max(safe_min, max_sleep_target_tab_sec)
                if safe_max > 0:
                    dwell_sec = random.randint(safe_min, safe_max)
                    self._log(
                        f"simulate_maps_action: новая вкладка '{action_label}' открыта, жду {dwell_sec} сек перед закрытием."
                    )
                    candidate.wait_for_timeout(dwell_sec * 1000)
                candidate.close()
                self._log(
                    f"simulate_maps_action: закрыта новая вкладка после действия '{action_label}'."
                )
                return
            except Exception:
                continue

    def _restore_maps_page_after_action(self, page, previous_url: str, action_label: str) -> None:
        """Возвращает страницу назад, если действие увело из карточки в текущей вкладке."""
        current_url = getattr(page, "url", "")
        if not current_url or current_url == previous_url:
            return
        self._log(
            f"simulate_maps_action: после '{action_label}' URL изменился, возвращаюсь назад."
        )
        try:
            page.go_back(wait_until="domcontentloaded", timeout=5000)
            page.wait_for_timeout(random.randint(600, 1200))
        except Exception:
            self._log(
                f"simulate_maps_action: не удалось вернуться назад после '{action_label}'."
            )

    def _sleep_in_range_seconds(self, page, min_sec: int, max_sec: int, reason: str) -> None:
        """Выдерживает паузу в заданном диапазоне секунд."""
        safe_min = max(0, min_sec)
        safe_max = max(safe_min, max_sec)
        if safe_max <= 0:
            return
        sleep_sec = random.randint(safe_min, safe_max)
        self._log(f"{reason}: {sleep_sec} сек.")
        page.wait_for_timeout(sleep_sec * 1000)

    def _launch_chromium_with_recovery(self, playwright, browsers_path: Path):
        """Запускает браузер и переустанавливает Chromium, если исполняемый файл пропал."""
        try:
            self._log("Пробую запустить браузер без переустановки.")
            return self._launch_human_like_browser(playwright)
        except Exception as error:
            if not self._is_missing_executable_error(error):
                self._log(f"Ошибка запуска браузера: {error}")
                raise
            self._log("Браузерный бинарник отсутствует, выполняю переустановку Chromium.")
            self._install_chromium(browsers_path)
            self._log("Повторно запускаю браузер после установки.")
            return self._launch_human_like_browser(playwright)

    def _prepare_runtime_browsers_path(self) -> Path:
        """Готовит каталог браузеров Playwright и записывает его в окружение."""
        browsers_path = self._runtime_browsers_path()
        browsers_path.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
        return browsers_path

    def _close_browser_session(self, context, browser, label: str) -> None:
        """Безопасно закрывает контекст и браузер.

        Браузер мог быть закрыт вручную, поэтому любые ошибки закрытия
        проглатываются: они не должны прерывать прогон.
        """
        self._log(f"{label}: закрываю контекст и браузер.")
        try:
            if context is not None:
                context.close()
        except Exception as error:
            self._log(f"{label}: контекст уже закрыт или ошибка закрытия: {error}")
        try:
            if browser is not None:
                browser.close()
        except Exception as error:
            self._log(f"{label}: браузер уже закрыт или ошибка закрытия: {error}")

    def _launch_human_like_browser(self, playwright):
        """Запускает локальный Chrome или встроенный Chromium в видимом режиме."""
        launch_options = {
            "headless": False,
            "args": ["--start-maximized", "--incognito"],
        }

        chrome_path = self._local_chrome_executable_path()
        if chrome_path is not None:
            self._log(f"Запускаю локальный Chrome по пути: {chrome_path}")
            return playwright.chromium.launch(executable_path=str(chrome_path), **launch_options)

        # Prefer regular local Chrome channel to avoid "testing" branding when available.
        try:
            self._log("Локальный путь не найден, запускаю channel='chrome'.")
            return playwright.chromium.launch(channel="chrome", **launch_options)
        except Exception:
            self._log("channel='chrome' недоступен, запускаю встроенный Chromium.")
            return playwright.chromium.launch(**launch_options)

    def _create_human_like_context(self, browser):
        """Создает браузерный контекст с фиксированным профилем Windows/Chromium."""
        self._log("Создаю контекст браузера с принудительным user-agent и client hints.")
        context = browser.new_context(
            no_viewport=True,
            user_agent=self.FORCED_USER_AGENT,
            locale="ru-RU",
            extra_http_headers={
                "sec-ch-ua": '"Not.A/Brand";v="99", "Chromium";v="136"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

        context.add_init_script(
            """
            (() => {
                const safeDefine = (target, key, getter) => {
                    try {
                        Object.defineProperty(target, key, { get: getter });
                    } catch (_) {}
                };
                const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36";
                const brands = [
                    { brand: "Not.A/Brand", version: "99" },
                    { brand: "Chromium", version: "136" },
                ];
                const fullVersionList = [
                    { brand: "Not.A/Brand", version: "99.0.0.0" },
                    { brand: "Chromium", version: "136.0.7103.25" },
                ];

                safeDefine(navigator, "userAgent", () => userAgent);
                safeDefine(navigator, "platform", () => "Win32");
                safeDefine(
                    navigator,
                    "appVersion",
                    () => "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
                );

                const userAgentData = {
                    architecture: "arm",
                    bitness: "64",
                    brands,
                    fullVersionList,
                    mobile: false,
                    platform: "Windows",
                    platformVersion: "19.0.0",
                    uaFullVersion: "136.0.7103.25",
                    getHighEntropyValues: async (hints) => {
                        const payload = {
                            architecture: "arm",
                            bitness: "64",
                            brands,
                            fullVersionList,
                            mobile: false,
                            platform: "Windows",
                            platformVersion: "19.0.0",
                            uaFullVersion: "136.0.7103.25",
                        };
                        if (!Array.isArray(hints)) {
                            return payload;
                        }
                        const selected = {};
                        for (const hint of hints) {
                            if (hint in payload) {
                                selected[hint] = payload[hint];
                            }
                        }
                        if (!("brands" in selected)) {
                            selected.brands = brands;
                        }
                        if (!("mobile" in selected)) {
                            selected.mobile = false;
                        }
                        return selected;
                    },
                    toJSON: () => ({
                        architecture: "arm",
                        bitness: "64",
                        brands,
                        fullVersionList,
                        mobile: false,
                        platform: "Windows",
                        platformVersion: "19.0.0",
                        uaFullVersion: "136.0.7103.25",
                    }),
                };

                safeDefine(navigator, "userAgentData", () => userAgentData);
            })();
            """
        )

        self._log("Контекст браузера успешно создан.")
        return context

    def _install_chromium(self, browsers_path: Path) -> None:
        """Устанавливает Chromium Playwright в пользовательский runtime-каталог."""
        self._log(f"Устанавливаю Chromium в каталог: {browsers_path}")
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
        command = [sys.executable, "-m", "playwright", "install", "chromium"]

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            creationflags=creationflags,
            check=False,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            self._log(f"Установка Chromium завершилась с ошибкой: {details}")
            raise RuntimeError(
                "Не удалось установить Chromium для Playwright. "
                f"Код: {result.returncode}. {details}"
            )
        self._log("Установка Chromium выполнена успешно.")

    @staticmethod
    def _chromium_executable_path() -> Path | None:
        """Возвращает путь к текущему Chromium Playwright, если его можно определить."""
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return None

        with sync_playwright() as playwright:
            executable_path = str(playwright.chromium.executable_path).strip()
            if not executable_path:
                return None
            return Path(executable_path)

    @staticmethod
    def _is_missing_executable_error(error: Exception) -> bool:
        """Проверяет, что ошибка связана с отсутствующим browser executable."""
        return "Executable doesn't exist" in str(error)

    @staticmethod
    def _runtime_browsers_path() -> Path:
        """Возвращает каталог, где приложение хранит браузеры Playwright."""
        return Path.home() / ".shagteampro" / "playwright-browsers"

    @staticmethod
    def _local_chrome_executable_path() -> Path | None:
        """Ищет локальный Google Chrome/Chromium под текущую операционную систему."""
        candidates: list[Path] = []
        if sys.platform == "darwin":
            candidates.extend(
                [
                    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                    Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
                ]
            )
        elif os.name == "nt":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            program_files = os.environ.get("PROGRAMFILES", "")
            program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")
            candidates.extend(
                [
                    Path(local_app_data) / "Google/Chrome/Application/chrome.exe",
                    Path(program_files) / "Google/Chrome/Application/chrome.exe",
                    Path(program_files_x86) / "Google/Chrome/Application/chrome.exe",
                ]
            )
        else:
            for binary_name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
                resolved = shutil.which(binary_name)
                if resolved:
                    return Path(resolved)

        for candidate in candidates:
            if str(candidate) and candidate.exists():
                return candidate
        return None

    @staticmethod
    def _to_non_negative_int(value: object) -> int:
        """Преобразует значение в неотрицательное целое число."""
        try:
            converted = int(float(value))
        except (TypeError, ValueError):
            return 0
        return max(0, converted)

    @staticmethod
    def _build_maps_url(card_payload: dict[str, object]) -> str:
        """Формирует ссылку Яндекс.Карт с координатами карточки, если они заданы."""
        coordinates = SearchRunnerService._normalize_coordinates(card_payload.get("coordinates", ""))
        if not coordinates:
            return "https://yandex.ru/maps/?lang=ru_RU"
        return f"https://yandex.ru/maps/?ll={coordinates}&z=17&lang=ru_RU"

    @staticmethod
    def _normalize_coordinates(value: object) -> str:
        """Удаляет любые пробелы из строки координат."""
        return "".join(str(value).split())

    @staticmethod
    def _build_query(phrase: str, city: str, street: str) -> str:
        """Собирает поисковый запрос из фразы, города и улицы."""
        parts = [phrase.strip(), city.strip(), street.strip()]
        return " ".join(part for part in parts if part)

    @staticmethod
    def _scroll_visible_content(page, delta: int) -> None:
        """Скроллит контент именно открытой карточки организации.

        Привязывается к реальному контенту карточки (галерея/фото/отзывы/блоки
        бизнес-карточки) и скроллит его ближайший скролл-контейнер. Fallback на
        `window` намеренно убран: иначе при не найденном контейнере скроллилась
        поисковая страница за карточкой.
        """
        page.evaluate(
            """
            (delta) => {
                // Подстраницы «Все фото»/«Все отзывы» и сам контент карточки.
                // Открытая карточка организации (OneOrgSimple) имеет приоритет:
                // если она есть на экране, скроллим именно её, а не список выдачи.
                const cardSelectors = [
                    '.OneOrgMediaHorizontalPage', '.MediaGrid',
                    '.ReviewViewer-ReviewList', '.ReviewViewer-Main', '.ReviewViewer',
                    '.OneOrgSimple-Content', '.OneOrgSimple',
                    '.CompaniesModal-OneOrg',
                    '.ReviewsView', '.OneOrgReviews',
                    '.BusinessReviews', '.OrgReviewsPreview',
                    '.OrgGallery', '.PhotoTiles', '.OrgContacts',
                    '.OrgPrices', '.OrgAbout',
                    '.business-card-title-view', '.card-phones-view',
                    '[class*="business-card"]', '[class*="orgpage"]',
                    '.VerticalScroller'
                ];

                const isScrollable = (el) => {
                    if (!(el instanceof HTMLElement)) return false;
                    if (el.getClientRects().length === 0) return false;
                    const style = window.getComputedStyle(el);
                    return (
                        el.scrollHeight > el.clientHeight + 20 &&
                        ['auto', 'scroll', 'overlay'].includes(style.overflowY)
                    );
                };

                const scrollFrom = (anchor) => {
                    let node = anchor;
                    while (node && node !== document.body) {
                        if (isScrollable(node)) { node.scrollTop += delta; return true; }
                        node = node.parentElement;
                    }
                    const inner = Array.from(anchor.querySelectorAll('*'))
                        .filter(isScrollable)
                        .sort((a, b) => b.clientHeight - a.clientHeight)[0];
                    if (inner) { inner.scrollTop += delta; return true; }
                    return false;
                };

                // 1) Привязка к конкретному контенту карточки/подстраницы.
                for (const selector of cardSelectors) {
                    const el = document.querySelector(selector);
                    if (el && scrollFrom(el)) return true;
                }

                // 2) Fallback: крупнейший скролл-контейнер внутри модалки карты,
                //    чтобы листались открытые «Все фото»/«Все отзывы».
                const scope = document.querySelector(
                    '[class*="ModalWithMap"], [class*="CompaniesModal"], ' +
                    '[class*="Modal"], [role="dialog"]'
                ) || document;
                const candidate = Array.from(scope.querySelectorAll('*'))
                    .filter(isScrollable)
                    .sort((a, b) => b.clientHeight - a.clientHeight)[0];
                if (candidate) { candidate.scrollTop += delta; return true; }
                return false;
            }
            """,
            delta,
        )

    def _get_search_input(self, page):
        """Находит поисковое поле Яндекса через устойчивые CSS/XPath селекторы."""
        self._log("Ищу поле поиска через CSS-селекторы.")
        css_input = page.locator(self.SEARCH_INPUT_CSS).first
        try:
            css_input.wait_for(state="visible", timeout=10000)
            self._log("Поле поиска найдено через CSS-селектор.")
            return css_input
        except Exception:
            self._log("Не удалось найти поле по CSS, пробую XPath.")
        xpath_input = page.locator(f"xpath={self.SEARCH_INPUT_XPATH}").first
        xpath_input.wait_for(state="visible", timeout=15000)
        self._log("Поле поиска найдено через XPath.")
        return xpath_input

    def _get_maps_search_input(self, page):
        """Находит поисковое поле внутри интерфейса Яндекс.Карт."""
        self._log("simulate_maps_action: ищу поле поиска на странице карт.")
        selectors = (
            ".header-view__search input.input__control",
            ".search-form-view input.input__control",
            "input[aria-label='Search for and select places']",
            "input[placeholder='Search for and select places']",
            "input[aria-label*='Search']",
        )
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=5000)
                self._log(f"simulate_maps_action: поле найдено через '{selector}'.")
                return locator
            except Exception:
                continue
        raise RuntimeError("Не удалось найти поле поиска на странице Яндекс.Карт.")

    def _type_query_and_submit(self, page, search_input, query: str) -> None:
        """Фокусирует поле поиска, вводит запрос и отправляет его Enter."""
        search_input.click(timeout=5000)
        search_input.fill("")
        self._type_human_like(page, query, target_input=search_input)
        typed_value = (search_input.input_value() or "").strip()
        if typed_value != query:
            self._log(
                f"Текст введен не полностью ({len(typed_value)}/{len(query)}), "
                "выполняю надежный fill()."
            )
            search_input.fill(query)
            typed_value = (search_input.input_value() or "").strip()
        self._log(f"Перед Enter в поле: '{typed_value[:80]}'")
        if not typed_value:
            raise RuntimeError("Поле поиска пустое перед отправкой Enter.")
        search_input.press("Enter")

    @staticmethod
    def _type_human_like(page, text: str, target_input=None) -> None:
        """Печатает текст посимвольно с небольшими случайными паузами."""
        SearchRunnerService._log(f"Печатаю запрос посимвольно. Длина текста: {len(text)}")
        for char in text:
            if target_input is not None:
                target_input.type(char, delay=random.randint(60, 180))
            else:
                page.keyboard.type(char, delay=random.randint(60, 180))
            if random.random() < 0.08:
                page.wait_for_timeout(random.randint(120, 300))

    def _handle_captcha_if_present(
        self,
        page,
        wait_ms: int = 10000,
        context: str = "browser-step",
    ):
        """Ждет возможную капчу, нажимает чекбокс и дает время решить ее вручную."""
        resolution = self._captcha_service.check_and_resolve(page, context=context, wait_ms=wait_ms)
        if resolution.detected and not resolution.solved:
            self._log(
                "Капча не решена, дальнейший шаг может завершиться таймаутом. "
                f"Детали: {resolution.message}"
            )
        return resolution

    @staticmethod
    def _log(message: str) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now}] [SearchRunnerService] {message}"
        print(line, flush=True)
        try:
            _get_run_logger().info(line)
        except Exception:
            pass
