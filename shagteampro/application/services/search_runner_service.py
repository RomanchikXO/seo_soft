from __future__ import annotations

import concurrent.futures
import contextvars
import datetime
import logging
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

from shagteampro.application.captcha import CaptchaService
from shagteampro.application.services.settings_service import SettingsService

_RUN_LOG_PATH = Path.home() / ".shagteampro" / "logs" / "run.log"
_run_logger: logging.Logger | None = None
_run_logger_lock = threading.Lock()
_log_worker_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("log_worker_id", default=None)


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


_PLAYWRIGHT_ACTION_TIMEOUT_MS = 5_000
_DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY = 100
_CHROME_IGNORED_DEFAULT_ARGS = (
    "--enable-automation",
    "--no-sandbox",
)


class SearchRunnerService:
    """Сервис, который управляет Playwright-браузером и имитирует действия пользователя."""

    DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY = _DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY
    SEARCH_INPUT_XPATH = "/html/body/main/div[2]/form/div[4]/div/div[2]/div/textarea[1]"
    SEARCH_INPUT_CSS = "textarea#text.search3__input, textarea#text, textarea.search3__input"
    FORCED_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )

    def __init__(self, captcha_service: CaptchaService | None = None, settings_service: SettingsService | None = None) -> None:
        self._settings_service = settings_service
        self._captcha_service = captcha_service or CaptchaService(logger=self._log)
        self._browser_pid_lock = threading.Lock()
        self._browser_launch_lock = threading.Lock()
        self._claimed_browser_pids: set[int] = set()

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

        playwright = None
        browser = None
        context = None
        executed_count = 0
        try:
            playwright = sync_playwright().start()
            browser, context = self._launch_chromium_with_recovery(playwright, browsers_path)
            page = self._open_browser_page(context)
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
            self._stop_playwright_instance(playwright, "run_yandex_searches", browser=browser)
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
        browser, context = self._launch_chromium_with_recovery(playwright, browsers_path)
        page = self._open_browser_page(context)
        return context, browser, page

    def run_cards_optimization(
        self,
        cards: list[dict[str, object]],
        threads: int,
        *,
        progress_run_id: str | None = None,
    ) -> dict[str, object]:
        """Запускает оптимизацию карточек по выбранным поисковым и картографическим ключам."""
        from shagteampro.application.services.optimization_progress import (
            update_run_from_targets,
            was_stopped_by_user,
        )

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
        update_run_from_targets(
            progress_run_id,
            targets=targets,
            card_results=card_results,
        )
        self._run_targets_in_pool(
            targets,
            max_workers,
            card_results=card_results,
            progress_run_id=progress_run_id,
        )
        for state in targets:
            self._apply_target_state(card_results[state["card_id"]], state)

        summary = self._build_optimization_summary(card_results)
        if was_stopped_by_user(progress_run_id):
            summary["stopped_by_user"] = True
        return summary

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
            "search_failures": 0,
            "maps_failures": 0,
            "exhausted_keys": [],
            "key_failure_reports": [],
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
                        "key_failures": {},
                        "exhausted_key_records": [],
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

    def _run_targets_in_pool(
        self,
        targets: list[dict[str, object]],
        max_workers: int,
        *,
        card_results: dict[int, dict[str, object]] | None = None,
        progress_run_id: str | None = None,
    ) -> None:
        """Выполняет все цели в одном общем пуле, держа до max_workers действий одновременно.

        Единица параллелизма — это одно действие по одному ключу. Планировщик в главном
        потоке наполняет пул, поэтому при N потоках одновременно открывается до N браузеров,
        даже если все действия идут по одной фразе/ключу.
        """
        from shagteampro.application.services.optimization_progress import (
            get_dispatch_control,
            is_dispatch_allowed,
            update_run_from_targets,
        )

        if not targets:
            return

        future_meta: dict[concurrent.futures.Future, tuple[dict[str, object], dict[str, object]]] = {}

        def report_progress(
            key_payload: dict[str, object] | None = None,
            state: dict[str, object] | None = None,
            *,
            action_completed: bool = False,
        ) -> None:
            if card_results is None:
                return
            if state is not None:
                card_id = int(state["card_id"])
                self._apply_target_state(card_results[card_id], state)
            update_run_from_targets(
                progress_run_id,
                targets=targets,
                card_results=card_results,
                action_completed=action_completed,
            )

        def has_pending_work() -> bool:
            return self._pick_dispatchable_target(targets) is not None

        def fill(executor: concurrent.futures.Executor) -> None:
            if not is_dispatch_allowed(progress_run_id):
                return
            while len(future_meta) < max_workers:
                state = self._pick_dispatchable_target(targets)
                if state is None:
                    break
                key_payload = random.choice(state["active_keys"])
                state["in_flight"] += 1
                worker_id = uuid.uuid4().hex
                future = executor.submit(self._execute_single_action, state, key_payload, worker_id)
                future_meta[future] = (state, key_payload, worker_id)
                self._log(
                    f"Пул: запущено действие {state['mode']} для карточки #{state['card_id']} "
                    f"(in_flight={state['in_flight']}, performed={state['performed']}/{state['target']}).",
                    worker_id=worker_id,
                )
                report_progress(state=state)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            while True:
                control = get_dispatch_control(progress_run_id) if progress_run_id else "active"
                if control == "stopping":
                    if not future_meta:
                        self._log("Пул: остановка по запросу пользователя, новые окна не открываются.")
                        break
                elif control == "active":
                    fill(executor)
                    if not future_meta and not has_pending_work():
                        break

                if not future_meta:
                    if control == "paused":
                        time.sleep(0.3)
                        continue
                    if control == "stopping":
                        break
                    if control == "active" and not has_pending_work():
                        break
                    if control == "active":
                        time.sleep(0.1)
                        continue
                    break

                done, _ = concurrent.futures.wait(
                    future_meta.keys(), return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    state, key_payload, worker_id = future_meta.pop(future)
                    self._consume_action_result(state, key_payload, future)
                    self._log(
                        f"Пул: действие {state['mode']} для карточки #{state['card_id']} завершено "
                        f"(performed={state['performed']}/{state['target']}, in_flight={state['in_flight']}).",
                        worker_id=worker_id,
                    )
                    report_progress(key_payload, state, action_completed=True)

        for state in targets:
            mode = state["mode"]
            self._log(
                f"Карточка #{state['card_id']}: {mode} завершен, выполнено="
                f"{state['performed']}/{state['target']}."
            )

    @staticmethod
    def _scheduled_successes(state: dict[str, object]) -> int:
        """Возвращает число успешных переходов плюс уже запущенные (in_flight)."""
        return int(state.get("performed", 0) or 0) + int(state.get("in_flight", 0) or 0)

    @staticmethod
    def _pick_dispatchable_target(targets: list[dict[str, object]]) -> dict[str, object] | None:
        """Выбирает цель, которой ещё нужны успешные переходы и есть активные ключи."""
        for state in targets:
            if not state["active_keys"]:
                continue
            if SearchRunnerService._scheduled_successes(state) >= int(state["target"]):
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
                f"Карточка #{state['card_id']}: {state['mode']} — браузер закрыт вручную, "
                f"сразу запущу новый поток без штрафа."
            )
        elif result.get("count_failure"):
            self._handle_failed_action(state, key_payload)
        else:
            self._log(
                f"Карточка #{state['card_id']}: {state['mode']} — переход без результата "
                f"(транзитная ошибка), повторю без увеличения счётчика неудач."
            )

    def _increment_key_failure(
        self,
        state: dict[str, object],
        key_payload: dict[str, object],
    ) -> int:
        """Увеличивает счётчики неудач по режиму и ключу; при лимите убирает ключ из ротации."""
        state["failures"] = int(state.get("failures", 0) or 0) + 1
        key_id = int(key_payload.get("id", 0) or 0)
        if not key_id:
            return 0

        key_failures = state.setdefault("key_failures", {})
        key_failures[key_id] = int(key_failures.get(key_id, 0) or 0) + 1
        failures_for_key = key_failures[key_id]
        if failures_for_key < self.DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY:
            return failures_for_key

        state["active_keys"] = [
            key for key in state["active_keys"] if int(key.get("id", 0) or 0) != key_id
        ]
        card_payload = state["card_payload"]
        state.setdefault("exhausted_key_records", []).append(
            {
                "key_id": key_id,
                "phrase": str(key_payload.get("phrase", "")),
                "mode": state["mode"],
                "failures": failures_for_key,
                "card_name": str(
                    card_payload.get("organization") or card_payload.get("card_name") or ""
                ),
            }
        )
        return failures_for_key

    def _handle_failed_action(
        self,
        state: dict[str, object],
        key_payload: dict[str, object],
    ) -> None:
        """Обрабатывает провал: организация не найдена в списке или на выдаче нет блока организаций.

        Ключ остаётся в ротации до ``DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY`` неудач
        или пока не набрано целевое число успешных переходов (``target``). Транзитные
        сбои (капча, таймаут до отправки запроса и т.п.) сюда не попадают.
        """
        failures_for_key = self._increment_key_failure(state, key_payload)
        card_id = state["card_id"]
        mode = state["mode"]
        key_id = int(key_payload.get("id", 0) or 0)
        performed = int(state.get("performed", 0) or 0)
        target = int(state["target"])

        if failures_for_key >= self.DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY:
            self._log(
                f"Карточка #{card_id}: {mode} — ключ #{key_id} исчерпал лимит неудач "
                f"({failures_for_key}/{self.DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY}), убираю из ротации."
            )
            return

        self._log(
            f"Карточка #{card_id}: {mode} — неудачная попытка по ключу #{key_id} "
            f"{failures_for_key}/{self.DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY}, "
            f"успешно {performed}/{target}."
        )

    def _execute_single_action(
        self,
        state: dict[str, object],
        key_payload: dict[str, object],
        worker_id: str,
    ) -> dict[str, object]:
        """Выполняет одно действие по ключу в зависимости от режима цели."""
        token = _log_worker_id.set(worker_id)
        try:
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
        finally:
            _log_worker_id.reset(token)

    @staticmethod
    def _normalize_action_result(raw: object) -> dict[str, object]:
        """Приводит результат действия к единому виду для планировщика."""
        if isinstance(raw, dict):
            actions = raw.get("actions") or {}
            return {
                "effect": bool(raw.get("effect")),
                "actions": dict(actions) if isinstance(actions, dict) else {},
                "closed": bool(raw.get("closed")),
                "count_failure": bool(raw.get("count_failure")),
            }
        return {"effect": bool(raw), "actions": {}, "closed": False, "count_failure": False}

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

    @staticmethod
    def _merge_key_failure_reports(
        card_entry: dict[str, object],
        state: dict[str, object],
    ) -> None:
        """Сохраняет итоговые неудачи по каждому ключу режима для отчёта."""
        key_failures = state.get("key_failures")
        if not isinstance(key_failures, dict) or not key_failures:
            return

        mode = state["mode"]
        card_payload = state["card_payload"]
        card_name = str(card_payload.get("organization") or card_payload.get("card_name") or "")
        keys_by_id = {
            int(key.get("id", 0) or 0): key
            for key in SearchRunnerService._card_key_payloads(card_payload)
            if int(key.get("id", 0) or 0)
        }
        preserved = [
            report
            for report in (card_entry.get("key_failure_reports") or [])
            if isinstance(report, dict) and report.get("mode") != mode
        ]
        card_entry["key_failure_reports"] = preserved + [
            {
                "key_id": key_id,
                "phrase": str(keys_by_id.get(key_id, {}).get("phrase", "")),
                "mode": mode,
                "failures": int(count or 0),
                "card_name": card_name,
            }
            for key_id, count in key_failures.items()
            if int(count or 0) > 0
        ]

    def _apply_target_state(
        self,
        card_entry: dict[str, object],
        state: dict[str, object],
    ) -> None:
        """Записывает итог выполненной search/maps цели в строку карточки."""
        mode = state["mode"]
        card_entry[f"{mode}_performed"] = int(state["performed"])
        card_entry[f"{mode}_failures"] = int(state.get("failures", 0) or 0)
        card_entry[f"{mode}_effect_keys"] = sorted(state["effect_key_ids"])
        self._merge_key_failure_reports(card_entry, state)
        exhausted_records = state.get("exhausted_key_records")
        if isinstance(exhausted_records, list):
            preserved = [
                record
                for record in (card_entry.get("exhausted_keys") or [])
                if isinstance(record, dict) and record.get("mode") != mode
            ]
            card_entry["exhausted_keys"] = preserved + list(exhausted_records)
        if mode == "maps":
            # state["action_counts"] уже накопительный итог по цели maps — не суммируем повторно.
            card_entry["maps_action_counts"] = dict(state.get("action_counts", {}))

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

        key_failure_reports = [
            report
            for item in results
            for report in (item.get("key_failure_reports") or [])
            if isinstance(report, dict)
        ]
        exhausted_keys = [
            report
            for report in key_failure_reports
            if int(report.get("failures", 0) or 0) >= self.DEFAULT_MAX_FAILED_ATTEMPTS_PER_KEY
        ]
        total_failed_attempts = sum(
            int(item.get("search_failures", 0) or 0) + int(item.get("maps_failures", 0) or 0)
            for item in results
        )

        self._log(
            f"Оптимизация завершена. search={totals['search_performed']}/{totals['search_target']}, "
            f"maps={totals['maps_performed']}/{totals['maps_target']}, "
            f"неудач={total_failed_attempts}, действия={total_action_counts}"
        )
        return {
            "processed_cards": len(results),
            "total_search_target": totals["search_target"],
            "total_search_performed": totals["search_performed"],
            "total_maps_target": totals["maps_target"],
            "total_maps_performed": totals["maps_performed"],
            "total_failed_attempts": total_failed_attempts,
            "total_action_counts": total_action_counts,
            "key_failure_reports": key_failure_reports,
            "exhausted_keys": exhausted_keys,
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
        label = "simulate_search_action"
        playwright = None
        browser = None
        context = None
        effect = False
        force_fast_close = False
        browser_closed_externally = False
        search_results_reached = False
        step_label = "init"
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as error:
            self._log(f"{label}: не удалось импортировать Playwright: {error}")
            return False

        try:
            step_label = "запуск браузера"
            playwright = sync_playwright().start()
            self._log(f"{label}: запускаю браузер и контекст.")
            browser, context = self._launch_chromium_with_recovery(playwright, browsers_path)
            page = self._open_browser_page(context)
            step_label = "ya.ru"
            self._log(f"{label}: перехожу на ya.ru.")
            page.goto("https://ya.ru/", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            search_input = self._get_search_input(page)
            page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
            step_label = "отправка запроса"
            self._type_query_and_submit(page, search_input, query)
            resolution = self._handle_captcha_if_present(page, context=f"после отправки запроса '{query}'")
            if self._captcha_blocks_progress(resolution):
                self._log(
                    f"{label}: капча не решена после отправки запроса, "
                    "завершаю поток досрочно — слот пула освободится для следующей попытки."
                )
                force_fast_close = True
                return False
            page.wait_for_timeout(random.randint(2500, 4200))

            if not resolution.detected or resolution.solved:
                self._dismiss_distribution_modal(page, context=label)

            search_results_reached = True
            step_label = "открытие большой карты"
            if not self._open_large_map(page):
                self._log(
                    f"{label}: на выдаче нет блока организаций/карты, "
                    "завершаю без поиска — слот пула освободится сразу."
                )
                force_fast_close = True
            else:
                step_label = "поиск организации в списке"
                if self._find_and_open_organization(
                    page,
                    organization,
                    min_sleep_overview,
                    max_sleep_overview,
                ):
                    effect = True
                    self._log(f"{label}: результат достигнут до зума карты.")
                else:
                    step_label = "zoom-итерации"
                    if self._run_zoom_search(
                        page,
                        organization,
                        map_zoom_clicks,
                        min_sleep_overview,
                        max_sleep_overview,
                        PlaywrightTimeoutError,
                    ):
                        effect = True
                    else:
                        self._log(f"{label}: организация не найдена после всех zoom-итераций.")
        except PlaywrightTimeoutError:
            force_fast_close = True
            self._log(
                f"{label}: таймаут Playwright на шаге «{step_label}» "
                f"(лимит действия {_PLAYWRIGHT_ACTION_TIMEOUT_MS} мс)."
            )
        except Exception as error:
            if self._is_browser_closed_error(error):
                force_fast_close = True
                browser_closed_externally = True
                self._log(
                    f"{label}: браузер закрыт/потерян ({error}), переоткрою без штрафа."
                )
                return {"effect": False, "actions": {}, "closed": True}
            force_fast_close = True
            self._log(f"{label}: ошибка {error}")
            return False
        finally:
            self._close_browser_session(
                context,
                browser,
                label,
                force_fast=force_fast_close,
                skip_kill=browser_closed_externally,
            )
            self._stop_playwright_instance(
                playwright,
                label,
                browser=browser,
                force_fast=force_fast_close,
                skip_kill=browser_closed_externally,
            )
            self._log(f"{label}: cleanup завершён, worker освобождён.")
        self._log(f"{label}: завершено, effect={effect}.")
        if effect:
            return True
        return {
            "effect": False,
            "actions": {},
            "count_failure": search_results_reached,
        }

    _DISTRIBUTION_CLOSE_SELECTORS: tuple[str, ...] = (
        ".Modal-Content:has(.DistributionSplashScreenModalContent) .DistributionButtonClose",
        ".Modal-Content:has(.DistributionSplashScreenModalContent) .DistributionActions button.DistributionButtonClose",
        ".DistributionSplashScreenModalContent button.DistributionButtonClose",
        ".DistributionSplashScreenModalContent button:has-text('Нет, спасибо')",
        ".DistributionActions button.DistributionButtonClose",
        "button.DistributionSplashScreenModalCloseButtonOuter",
        "button[aria-label='Нет, спасибо']",
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

    # Вкладки карточки организации на Яндекс.Картах (класс _name_* + подпись).
    _MAPS_CARD_TAB_SPECS: tuple[tuple[str, str], ...] = (
        ("overview", "Обзор"),
        ("menu", "Меню"),
        ("posts", "Новости"),
        ("gallery", "Фото"),
        ("reviews", "Отзывы"),
        ("features", "Особенности"),
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

    def _dismiss_distribution_modal(
        self,
        page,
        context: str = "search",
        *,
        wait_load: bool = True,
        press_escape: bool = True,
    ) -> None:
        """Закрывает промо-модалку Яндекса (в т.ч. «Сделать Яндекс основным поиском?»).

        Сначала по возможности дожидается полной загрузки страницы, затем кликает
        реальную кнопку закрытия «Нет, спасибо» (естественное поведение), а при её
        отсутствии удаляет оверлей из DOM.

        Esc жмётся только если `press_escape=True`. На открытой карте/карте организации
        Esc закрывает саму карту — там его использовать нельзя.
        """
        if wait_load:
            self._wait_for_full_load(page)

        if not self._close_distribution_if_present(page, context):
            removed = self._remove_distribution_overlays(page)
            if removed:
                self._log(
                    f"{context}: кнопка закрытия не найдена, удалил промо-оверлеи через JS ({removed} шт.)."
                )

        if press_escape:
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
            const distributionSelectors = [
                '.DistributionSplashScreenModal',
                '.DistributionSplashScreenModalContent',
                '[class*="DistributionSplashScreen"]',
            ];
            const mapMarkers = [
                '.VerticalOrgsScroller',
                '.VerticalOrgsScroller-List',
                '.OrgmnColumn',
            ];
            let removed = 0;
            const seen = new Set();
            const hasMapContent = (node) => mapMarkers.some(
                (selector) => node.querySelector(selector)
            );
            for (const selector of distributionSelectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (seen.has(node)) continue;
                    seen.add(node);
                    node.remove();
                    removed += 1;
                }
            }
            for (const modal of document.querySelectorAll('.Modal, .Modal-Content')) {
                const hasDistribution = modal.querySelector(
                    '.DistributionSplashScreenModalContent, [class*="DistributionSplashScreen"], .DistributionTitle'
                );
                if (!hasDistribution || hasMapContent(modal) || seen.has(modal)) {
                    continue;
                }
                seen.add(modal);
                modal.remove();
                removed += 1;
            }
            return removed;
        }
        """
        try:
            return int(page.evaluate(script))
        except Exception:
            return 0

    def _open_large_map(self, page) -> bool:
        """Открывает большую карту из поисковой выдачи Яндекса."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        map_button_locator = page.locator("a.OrgmnColumn-MapButton").first
        try:
            map_button_locator.wait_for(state="visible", timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            self._log(
                "simulate_search_action: на странице нет блока организаций/кнопки карты "
                "(пустая выдача или другой формат SERP)."
            )
            return False
        page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
        try:
            map_button_locator.click(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            self._log("simulate_search_action: не удалось нажать кнопку карты.")
            return False
        self._log("simulate_search_action: открыта большая карта.")
        self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия большой карты")
        page.wait_for_timeout(random.randint(2000, 3000))
        self._dismiss_distribution_modal(
            page,
            context="после открытия большой карты",
            wait_load=False,
            press_escape=False,
        )
        return True

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
            page.locator("ul.VerticalOrgsScroller-List").first.wait_for(
                state="visible",
                timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS,
            )
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
        page.wait_for_timeout(random.randint(400, 800))
        self._dismiss_distribution_modal(
            page,
            context="simulate_search_action",
            wait_load=False,
            press_escape=False,
        )
        if not self._wait_for_organization_card_opened(page, organization):
            self._log(
                f"simulate_search_action: карточка '{actual_title}' не открылась после клика, "
                "повторно закрываю промо-модалку «Сделать Яндекс основным поиском?»."
            )
            self._dismiss_distribution_modal(
                page,
                context="simulate_search_action",
                wait_load=False,
                press_escape=False,
            )
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
                    locator.click(force=True, timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
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
        items.nth(count - 1).scroll_into_view_if_needed(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
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
        try:
            button.click(timeout=_PLAYWRIGHT_ACTION_TIMEOUT_MS)
        except timeout_error_type:
            return
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
        label = "simulate_maps_action"
        playwright = None
        browser = None
        context = None
        action_counts: dict[str, int] = {}
        found = False
        force_fast_close = False
        browser_closed_externally = False
        org_search_reached = False
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            browser, context = self._launch_chromium_with_recovery(playwright, browsers_path)
            page = self._open_browser_page(context)
            self._log(f"{label}: перехожу по ссылке {maps_url}")
            page.goto(maps_url, wait_until="domcontentloaded")
            resolution = self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия карт")
            if self._captcha_blocks_progress(resolution):
                self._log(
                    f"{label}: капча не решена после открытия карт, "
                    "завершаю поток досрочно — слот пула освободится для следующей попытки."
                )
                force_fast_close = True
                return {"effect": False, "actions": {}}
            maps_search_input = self._get_maps_search_input(page)
            self._type_query_and_submit(page, maps_search_input, query)
            resolution = self._handle_captcha_if_present(page, context=f"после отправки maps-запроса '{query}'")
            if self._captcha_blocks_progress(resolution):
                self._log(
                    f"{label}: капча не решена после отправки запроса, "
                    "завершаю поток досрочно — слот пула освободится для следующей попытки."
                )
                force_fast_close = True
                return {"effect": False, "actions": {}}
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                page.wait_for_timeout(1000)

            org_search_reached = True
            organization = str(card_payload.get("organization", ""))
            found = self._find_and_open_maps_organization(
                page,
                organization,
                map_zoom_clicks,
                PlaywrightTimeoutError,
            )

            if not found:
                self._log(f"{label}: организация не найдена на картах.")
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
        except PlaywrightTimeoutError:
            force_fast_close = True
            self._log(f"{label}: таймаут Playwright (лимит {_PLAYWRIGHT_ACTION_TIMEOUT_MS} мс).")
        except Exception as error:
            if self._is_browser_closed_error(error):
                force_fast_close = True
                browser_closed_externally = True
                self._log(
                    f"{label}: браузер закрыт/потерян ({error}), переоткрою без штрафа."
                )
                return {"effect": False, "actions": {}, "closed": True}
            force_fast_close = True
            self._log(f"{label}: ошибка {error}")
            return {"effect": False, "actions": {}}
        finally:
            self._close_browser_session(
                context,
                browser,
                label,
                force_fast=force_fast_close,
                skip_kill=browser_closed_externally,
            )
            self._stop_playwright_instance(
                playwright,
                label,
                browser=browser,
                force_fast=force_fast_close,
                skip_kill=browser_closed_externally,
            )
            self._log(f"{label}: cleanup завершён, worker освобождён.")
        self._log(f"{label}: завершено успешно.")
        return {
            "effect": found,
            "actions": action_counts,
            "count_failure": org_search_reached and not found,
        }

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
        self._run_maps_card_tabs_activity(
            page,
            min_sleep_target_tab_sec,
            max_sleep_target_tab_sec,
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

    def _run_maps_card_tabs_activity(
        self,
        page,
        min_sleep_sec: int,
        max_sleep_sec: int,
    ) -> None:
        """После целевых действий имитирует изучение разделов карточки на картах.

        Общее время «зависаний» (паузы + чтение) не превышает случайный бюджет
        в диапазоне min/max сна на целевом действии карточки.
        """
        safe_min = max(0, min_sleep_sec)
        safe_max = max(safe_min, max_sleep_sec)
        if safe_max <= 0:
            page.wait_for_timeout(random.randint(1000, 2000))
            return

        sleep_time_sec = random.randint(safe_min, safe_max)
        self._log(
            f"simulate_maps_action: имитация активности в разделах карточки "
            f"{sleep_time_sec} сек."
        )
        end_time = time.time() + sleep_time_sec
        visited_tabs: set[str] = set()
        tab_specs = list(self._MAPS_CARD_TAB_SPECS)
        random.shuffle(tab_specs)

        while time.time() < end_time:
            remaining_sec = end_time - time.time()
            if remaining_sec <= 0.5:
                break

            tab_candidates = [tab for tab in tab_specs if tab[0] not in visited_tabs]
            if not tab_candidates:
                tab_candidates = tab_specs
            tab_key, tab_label = random.choice(tab_candidates)

            if self._switch_maps_org_tab(page, tab_key, tab_label, end_time):
                visited_tabs.add(tab_key)
                self._wait_within_budget(page, end_time, random.randint(1500, 3200))
                section_budget_sec = min(
                    remaining_sec,
                    random.uniform(4.0, min(14.0, remaining_sec)),
                )
                self._browse_maps_org_tab(
                    page,
                    tab_key,
                    tab_label,
                    end_time=end_time,
                    section_end=time.time() + section_budget_sec,
                )

            self._wait_within_budget(page, end_time, random.randint(1000, 2500))

    @staticmethod
    def _wait_within_budget(page, end_time: float, requested_ms: int) -> None:
        """Ждёт не дольше, чем осталось до конца бюджета активности."""
        remaining_ms = int(max(0.0, end_time - time.time()) * 1000)
        if remaining_ms <= 0:
            return
        page.wait_for_timeout(min(requested_ms, remaining_ms))

    def _switch_maps_org_tab(self, page, tab_key: str, tab_label: str, end_time: float) -> bool:
        """Переключает вкладку карточки организации на картах."""
        tab = page.locator(f".tabs-select-view__title._name_{tab_key}").first
        if tab.count() == 0:
            tab = page.locator(
                f"[role='tab']:has(.tabs-select-view__label:text('{tab_label}'))"
            ).first
        if tab.count() == 0:
            self._log(f"simulate_maps_action: вкладка '{tab_label}' не найдена.")
            return False
        try:
            selected = tab.get_attribute("aria-selected")
            tab.scroll_into_view_if_needed()
            self._wait_within_budget(page, end_time, random.randint(800, 1500))
            if selected != "true":
                self._log(f"simulate_maps_action: перехожу во вкладку '{tab_label}'.")
                tab.click(force=True)
                self._wait_within_budget(page, end_time, random.randint(1200, 2200))
                self._handle_captcha_if_present(
                    page,
                    wait_ms=1500,
                    context=f"после перехода в раздел '{tab_label}'",
                )
            return True
        except Exception as error:
            self._log(f"simulate_maps_action: не удалось открыть вкладку '{tab_label}': {error}")
            return False

    def _browse_maps_org_tab(
        self,
        page,
        tab_key: str,
        tab_label: str,
        *,
        end_time: float,
        section_end: float,
    ) -> None:
        """Скроллит текущую вкладку, иногда нажимает элементы и делает паузы."""
        self._log(f"simulate_maps_action: изучаю раздел '{tab_label}'.")
        while time.time() < section_end and time.time() < end_time:
            self._scroll_visible_content(
                page,
                random.randint(250, 650) * random.choice([1, 1, -1]),
            )
            self._wait_within_budget(page, end_time, random.randint(1200, 2800))

            roll = random.random()
            if tab_key == "reviews" and roll < 0.55:
                self._scroll_maps_reviews_tab(page, end_time)
            elif tab_key == "gallery" and roll < 0.55:
                self._browse_maps_gallery_tab(page, end_time)
            elif tab_key == "menu" and roll < 0.45:
                self._browse_maps_menu_tab(page, end_time)
            elif tab_key == "overview" and roll < 0.5:
                self._browse_maps_overview_widgets(page, end_time)
            elif roll < 0.4:
                self._click_random_maps_card_control(page, tab_key, end_time)
            else:
                self._idle_on_current_screen_within_budget(page, end_time)

            self._wait_within_budget(page, end_time, random.randint(800, 1800))

    def _idle_on_current_screen_within_budget(self, page, end_time: float) -> None:
        """Короткая пауза «читаю экран» с учётом общего бюджета."""
        remaining_ms = int(max(0.0, end_time - time.time()) * 1000)
        if remaining_ms <= 0:
            return
        idle_ms = min(random.randint(2500, 7000), remaining_ms)
        self._log(f"simulate_maps_action: читаю текущий экран ({idle_ms / 1000:.1f} сек)...")
        page.wait_for_timeout(idle_ms)

    def _click_random_maps_card_control(
        self,
        page,
        tab_key: str,
        end_time: float,
    ) -> None:
        """Случайно нажимает раскрываемый блок или интерактивный элемент вкладки."""
        selectors = [
            ".card-feature-view._interactive[role='button'][aria-expanded='false']",
            ".business-working-status-flip-view._clickable",
            ".masstransit-stops-view._clickable .card-feature-view._interactive",
            ".business-header-rating-view__text._clickable",
            ".card-feature-view._interactive.business-features-view__more-info",
            ".search-sources-view__control",
        ]
        if tab_key in {"overview", "posts"}:
            selectors.append(".story-preview")
        if tab_key == "menu":
            selectors.extend(
                (
                    ".related-product-view .image[role='button']",
                    ".card-feature-view._interactive.business-card-view__menu-link",
                )
            )
        controls = page.locator(", ".join(selectors))
        count = controls.count()
        if count <= 0:
            return
        index = random.randint(0, min(count - 1, 10))
        target = controls.nth(index)
        try:
            if not target.is_visible():
                return
            label = (target.get_attribute("aria-label") or target.inner_text() or "элемент").strip()
            self._log(f"simulate_maps_action: нажимаю '{label[:60]}'.")
            target.scroll_into_view_if_needed()
            self._wait_within_budget(page, end_time, random.randint(700, 1400))
            target.click(force=True)
            self._handle_captcha_if_present(page, wait_ms=1200, context="после клика в карточке")
            self._wait_within_budget(page, end_time, random.randint(1500, 3500))
        except Exception:
            return

    def _browse_maps_overview_widgets(self, page, end_time: float) -> None:
        """Листает виджеты на вкладке «Обзор»: истории, меню-превью, карусели."""
        widgets = page.locator(
            ".story-preview, .carousel__scrollable, .card-related-products-view__top-items, "
            ".business-attendance-view__day, .card-similar-carousel__item"
        )
        count = widgets.count()
        if count <= 0:
            return
        index = random.randint(0, min(count - 1, 8))
        widget = widgets.nth(index)
        try:
            if not widget.is_visible():
                return
            widget.scroll_into_view_if_needed()
            self._wait_within_budget(page, end_time, random.randint(900, 1800))
            if random.random() < 0.35:
                widget.click(force=True)
                self._wait_within_budget(page, end_time, random.randint(1800, 4200))
        except Exception:
            return

    def _browse_maps_menu_tab(self, page, end_time: float) -> None:
        """Листает позиции меню и иногда открывает карточку блюда."""
        items = page.locator(".related-product-view, .card-feature-view._interactive")
        count = items.count()
        if count <= 0:
            return
        for _ in range(random.randint(2, 4)):
            if time.time() >= end_time:
                return
            self._scroll_visible_content(page, random.randint(220, 520))
            self._wait_within_budget(page, end_time, random.randint(1200, 2600))
        if random.random() < 0.4:
            self._click_random_maps_card_control(page, "menu", end_time)

    def _browse_maps_gallery_tab(self, page, end_time: float) -> None:
        """Листает фото и иногда открывает снимок."""
        for _ in range(random.randint(2, 5)):
            if time.time() >= end_time:
                return
            self._scroll_visible_content(page, random.randint(250, 600))
            self._wait_within_budget(page, end_time, random.randint(1500, 3200))
        photos = page.locator(".image[role='button'], .MediaGrid-Item, .Gallery-Item")
        if photos.count() <= 0:
            return
        photo = photos.nth(random.randint(0, min(photos.count() - 1, 12)))
        try:
            if not photo.is_visible():
                return
            photo.scroll_into_view_if_needed()
            photo.click(force=True)
            self._handle_captcha_if_present(page, wait_ms=1500, context="после открытия фото на картах")
            self._wait_within_budget(page, end_time, random.randint(2500, 5500))
            self._close_photo_viewer(page)
        except Exception:
            return

    def _scroll_maps_reviews_tab(self, page, end_time: float) -> None:
        """Листает отзывы на вкладке «Отзывы» и иногда раскрывает длинный текст."""
        for _ in range(random.randint(3, 6)):
            if time.time() >= end_time:
                return
            self._scroll_visible_content(page, random.randint(280, 720))
            self._wait_within_budget(page, end_time, random.randint(2200, 4500))
            if random.random() < 0.25:
                self._expand_random_review(page)

    def _launch_chromium_with_recovery(self, playwright, browsers_path: Path):
        """Запускает браузер и переустанавливает Chromium, если исполняемый файл пропал."""
        try:
            self._log("Пробую запустить браузер без переустановки.")
            with self._browser_launch_lock:
                before_pids = self._list_chrome_pids_near_python()
                browser, context = self._launch_human_like_session(playwright)
                self._remember_browser_pid(browser, before_pids=before_pids)
        except Exception as error:
            if not self._is_missing_executable_error(error):
                self._log(f"Ошибка запуска браузера: {error}")
                raise
            self._log("Браузерный бинарник отсутствует, выполняю переустановку Chromium.")
            self._install_chromium(browsers_path)
            self._log("Повторно запускаю браузер после установки.")
            with self._browser_launch_lock:
                before_pids = self._list_chrome_pids_near_python()
                browser, context = self._launch_human_like_session(playwright)
                self._remember_browser_pid(browser, before_pids=before_pids)
        return browser, context

    @staticmethod
    def _open_browser_page(context):
        if context.pages:
            return context.pages[0]
        return context.new_page()

    def _pids_matching_user_data_dir(self, pids: set[int], user_data_dir: str) -> set[int]:
        matched: set[int] = set()
        for pid in pids:
            command = self._process_command(pid) or ""
            if user_data_dir in command:
                matched.add(pid)
        return matched

    def _remember_browser_pid(self, browser, *, before_pids: set[int] | None = None) -> None:
        """Сохраняет PID subprocess браузера для принудительного завершения."""
        if browser is None:
            return
        process = self._browser_subprocess(browser)
        if process is not None:
            owned_pids = {process.pid}
            with self._browser_pid_lock:
                self._claimed_browser_pids.update(owned_pids)
            browser._seo_soft_browser_pid = process.pid  # type: ignore[attr-defined]
            browser._seo_soft_owned_pids = owned_pids  # type: ignore[attr-defined]
            self._log(f"Сохранён pid процесса браузера: {process.pid}.")
            return

        baseline = before_pids if before_pids is not None else self._list_chrome_pids_near_python()
        after_pids = self._list_chrome_pids_near_python()
        new_pids = after_pids - baseline
        if not new_pids:
            time.sleep(0.2)
            after_pids = self._list_chrome_pids_near_python()
            new_pids = after_pids - baseline

        candidate_pid = self._pick_browser_root_pid(new_pids)
        user_data_dir = getattr(browser, "_seo_soft_user_data_dir", None)
        if user_data_dir:
            owned_pids = self._pids_matching_user_data_dir(new_pids, user_data_dir)
            if not owned_pids:
                owned_pids = self._pids_matching_user_data_dir(after_pids, user_data_dir)
        else:
            owned_pids = set()
        if not owned_pids and candidate_pid is not None:
            owned_pids = {candidate_pid}

        with self._browser_pid_lock:
            if candidate_pid is not None and candidate_pid in self._claimed_browser_pids:
                unclaimed = sorted(pid for pid in owned_pids if pid not in self._claimed_browser_pids)
                if unclaimed:
                    candidate_pid = unclaimed[0]
                else:
                    candidate_pid = None
            if candidate_pid is not None and owned_pids:
                self._claimed_browser_pids.update(owned_pids)
                browser._seo_soft_browser_pid = candidate_pid  # type: ignore[attr-defined]
                browser._seo_soft_owned_pids = set(owned_pids)  # type: ignore[attr-defined]
                self._log(
                    f"Сохранены pid Chrome (diff pgrep): root={candidate_pid}, "
                    f"owned={sorted(owned_pids)}."
                )
                return

        self._log(
            "Не удалось сохранить pid процесса браузера — "
            "закрытие будет через os.kill по кэшу или пропуск kill."
        )

    def _release_browser_pid(self, browser) -> None:
        owned = getattr(browser, "_seo_soft_owned_pids", None)
        pid = getattr(browser, "_seo_soft_browser_pid", None)
        with self._browser_pid_lock:
            if owned:
                for item in owned:
                    self._claimed_browser_pids.discard(int(item))
            elif pid is not None:
                self._claimed_browser_pids.discard(int(pid))

    @staticmethod
    def _pick_browser_root_pid(candidate_pids: set[int]) -> int | None:
        if not candidate_pids:
            return None
        return min(candidate_pids)

    @staticmethod
    def _process_command(pid: int) -> str | None:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            return result.stdout.strip()
        except Exception:
            return None

    def _collect_descendant_pids(self, root_pid: int, *, max_depth: int = 4) -> list[int]:
        collected: list[int] = []
        frontier = [root_pid]
        for _ in range(max_depth):
            next_frontier: list[int] = []
            for parent_pid in frontier:
                try:
                    result = subprocess.run(
                        ["pgrep", "-P", str(parent_pid)],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=2,
                    )
                except Exception:
                    continue
                for pid_str in result.stdout.split():
                    try:
                        child_pid = int(pid_str)
                    except ValueError:
                        continue
                    collected.append(child_pid)
                    next_frontier.append(child_pid)
            frontier = next_frontier
            if not frontier:
                break
        return collected

    def _list_chrome_pids_near_python(self) -> set[int]:
        try:
            result = subprocess.run(
                ["pgrep", "-P", str(os.getpid())],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except Exception:
            return set()
        candidate_pids = [int(pid_str) for pid_str in result.stdout.split() if pid_str.strip().isdigit()]
        chrome_pids: set[int] = set()
        for pid in candidate_pids:
            command = self._process_command(pid)
            if command and ("chrome" in command.lower() or "chromium" in command.lower()):
                chrome_pids.add(pid)
            for child_pid in self._collect_descendant_pids(pid, max_depth=2):
                command = self._process_command(child_pid)
                if command and ("chrome" in command.lower() or "chromium" in command.lower()):
                    chrome_pids.add(child_pid)
        return chrome_pids

    def _prepare_runtime_browsers_path(self) -> Path:
        """Готовит каталог браузеров Playwright и записывает его в окружение."""
        browsers_path = self._runtime_browsers_path()
        browsers_path.mkdir(parents=True, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)
        return browsers_path

    @staticmethod
    def _is_browser_disconnected(browser) -> bool:
        if browser is None:
            return True
        try:
            return not browser.is_connected()
        except Exception:
            return True

    def _close_browser_session(
        self,
        context,
        browser,
        label: str,
        *,
        force_fast: bool = False,
        skip_kill: bool = False,
    ) -> None:
        """Завершает браузерную сессию без graceful close.

        Playwright Sync API нельзя вызывать из другого потока. Вызовы
        ``context.close()`` / ``browser.close()`` на локальном Chrome часто
        зависают на минуты даже после kill subprocess — worker не возвращается
        в пул. Поэтому всегда завершаем только через kill процесса.
        """
        del context  # контекст закрывается вместе с процессом браузера
        if skip_kill:
            self._log(f"{label}: браузер закрыт извне — принудительный kill пропущен.")
            self._release_browser_pid(browser)
            return
        if force_fast:
            self._log(f"{label}: закрываю браузер (быстрый режим после ошибки).")
        else:
            self._log(f"{label}: закрываю контекст и браузер.")
        if self._kill_browser_subprocess(browser, label):
            self._log(f"{label}: close-сессия завершена.")
            return
        if self._is_browser_disconnected(browser):
            self._log(f"{label}: браузер уже отключён, close-сессия завершена.")
            self._release_browser_pid(browser)
            return
        self._log(
            f"{label}: pid браузера не найден — graceful close пропущен, "
            "worker освобождён без ожидания Playwright."
        )

    @staticmethod
    def _browser_subprocess(browser):
        if browser is None:
            return None
        try:
            browser_process = browser._impl_obj._browser_process
            process = getattr(browser_process, "process", None)
            if process is not None:
                return process
            return getattr(browser_process, "_process", None)
        except Exception:
            return None

    def _browser_pids_to_kill(self, browser) -> list[int]:
        if browser is None:
            return []
        owned = getattr(browser, "_seo_soft_owned_pids", None)
        if owned:
            return sorted(int(pid) for pid in owned)

        root_pids: list[int] = []
        process = self._browser_subprocess(browser)
        if process is not None and process.poll() is None:
            root_pids.append(process.pid)
        cached_pid = getattr(browser, "_seo_soft_browser_pid", None)
        if cached_pid is not None and cached_pid not in root_pids:
            root_pids.append(int(cached_pid))
        return root_pids

    def _kill_browser_subprocess(self, browser, label: str) -> bool:
        if browser is None:
            return False
        if getattr(browser, "_seo_soft_killed", False):
            return False
        pids = self._browser_pids_to_kill(browser)
        if not pids:
            self._release_browser_pid(browser)
            return False

        process = self._browser_subprocess(browser)
        if process is not None and process.poll() is None:
            try:
                self._log(f"{label}: принудительно завершаю процесс браузера (pid={process.pid}).")
                process.kill()
                process.wait(timeout=5)
            except Exception as error:
                self._log(f"{label}: process.kill() не сработал ({error}), пробую os.kill.")

        killed_any = False
        for pid in reversed(pids):
            try:
                os.kill(pid, signal.SIGKILL)
                killed_any = True
            except ProcessLookupError:
                continue
            except Exception as error:
                self._log(f"{label}: os.kill({pid}) — {error}")
        self._release_browser_pid(browser)
        if killed_any:
            browser._seo_soft_killed = True  # type: ignore[attr-defined]
            self._log(f"{label}: завершены процессы браузера: {pids}.")
        return killed_any

    def _stop_playwright_instance(
        self,
        playwright,
        label: str,
        *,
        browser=None,
        force_fast: bool = False,
        skip_kill: bool = False,
    ) -> None:
        """Останавливает Playwright в потоке worker'а, где был вызван sync_playwright().start().

        Playwright Sync API привязан к greenlet текущего потока. Вызов stop() из другого
        потока ломает ThreadPoolExecutor worker и следующие start() на том же потоке
        падают с «Sync API inside the asyncio loop».
        """
        if playwright is None:
            return

        del force_fast
        if skip_kill:
            self._release_browser_pid(browser)
        else:
            self._kill_browser_subprocess(browser, label)

        started = time.monotonic()
        try:
            playwright.stop()
        except Exception as error:
            self._log(f"{label}: playwright.stop() завершился с ошибкой: {error}")
        else:
            elapsed = time.monotonic() - started
            if elapsed >= 0.3:
                self._log(f"{label}: playwright.stop() занял {elapsed:.1f} с.")

    def _launch_human_like_session(self, playwright):
        """Запускает изолированный Chrome/Chromium через persistent context."""
        user_data_dir = tempfile.mkdtemp(prefix="shagteampro-chrome-")
        launch_kwargs = {
            "headless": False,
            "no_viewport": True,
            "user_agent": self.FORCED_USER_AGENT,
            "locale": "ru-RU",
            "ignore_default_args": list(_CHROME_IGNORED_DEFAULT_ARGS),
            "args": [
                "--start-maximized",
            ],
            "extra_http_headers": {
                "sec-ch-ua": '"Not.A/Brand";v="99", "Chromium";v="136"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        }

        chrome_path = self._local_chrome_executable_path()
        if chrome_path is not None:
            self._log(f"Запускаю локальный Chrome по пути: {chrome_path}")
            context = playwright.chromium.launch_persistent_context(
                user_data_dir,
                executable_path=str(chrome_path),
                **launch_kwargs,
            )
        else:
            try:
                self._log("Локальный путь не найден, запускаю channel='chrome'.")
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    channel="chrome",
                    **launch_kwargs,
                )
            except Exception:
                self._log("channel='chrome' недоступен, запускаю встроенный Chromium.")
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    **launch_kwargs,
                )

        self._apply_human_like_fingerprint(context)
        browser = context.browser
        if browser is None:
            raise RuntimeError("Persistent context запущен без browser handle.")
        browser._seo_soft_user_data_dir = user_data_dir  # type: ignore[attr-defined]
        return browser, context

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
        self._apply_human_like_fingerprint(context)
        self._log("Контекст браузера успешно создан.")
        return context

    def _apply_human_like_fingerprint(self, context) -> None:
        """Подменяет fingerprint браузера под Windows/Chromium."""
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
                safeDefine(navigator, "webdriver", () => undefined);
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
                    '.business-card-view__main-wrapper', '.business-tab-wrapper__content',
                    '.business-card-overview-tab-view__content', '.business-card-view__tabs-view',
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
                "Капча не решена. "
                f"Детали: {resolution.message}"
            )
        return resolution

    @staticmethod
    def _captcha_blocks_progress(resolution) -> bool:
        """True, если капча обнаружена, но не решена — дальнейшие шаги бессмысленны."""
        return bool(resolution.detected and not resolution.solved)

    @staticmethod
    def _log(message: str, *, worker_id: str | None = None) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        thread_id = worker_id or _log_worker_id.get()
        thread_prefix = f"[{thread_id[:8]}] " if thread_id else ""
        line = f"[{now}] [SearchRunnerService] {thread_prefix}{message}"
        print(line, flush=True)
        try:
            _get_run_logger().info(line)
        except Exception:
            pass
