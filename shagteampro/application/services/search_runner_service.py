from __future__ import annotations

import concurrent.futures
import datetime
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from shagteampro.application.captcha import CaptchaService
from shagteampro.application.services.settings_service import SettingsService


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
            solver = CapsolaCaptchaSolver(token=token)
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
                    self._log(f"Открываю ya.ru для запроса: {query}")
                    page.goto("https://ya.ru/", wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                    search_input = self._get_search_input(page)
                    page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
                    self._type_query_and_submit(page, search_input, query)
                    resolution = self._handle_captcha_if_present(page, context=f"после отправки запроса '{query}'")
                    page.wait_for_timeout(random.randint(2500, 4200))
                    
                    if not resolution.detected or resolution.solved:
                        self._log("run_yandex_searches: нажимаю Esc 3 раза с интервалом 0.5с")
                        for _ in range(3):
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(500)
                            
                    executed_count += 1
                    self._log(f"Запрос обработан успешно. Выполнено: {executed_count}/{len(queries)}")
            except PlaywrightTimeoutError:
                self._log(f"Таймаут в run_yandex_searches. Успешно выполнено: {executed_count}")
                return executed_count
            finally:
                self._close_browser_session(context, browser, "run_yandex_searches")
        self._log(f"Пакетный поиск завершен. Итого выполнено: {executed_count}")
        return executed_count

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
        future_to_meta: dict[concurrent.futures.Future, tuple[int, str]] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for card_payload in prepared_cards:
                card_id = int(card_payload.get("card_id", 0))
                future_to_meta.update(self._submit_card_targets(executor, card_payload, card_id))

            for future in concurrent.futures.as_completed(future_to_meta):
                card_id, mode = future_to_meta[future]
                self._apply_target_result(card_results[card_id], mode, future.result())

        return self._build_optimization_summary(card_results)

    def _empty_card_result(self, card_payload: dict[str, object]) -> dict[str, object]:
        """Создает начальную строку результата оптимизации для одной карточки."""
        return {
            "card_id": int(card_payload.get("card_id", 0)),
            "card_name": str(card_payload.get("card_name", "")),
            "search_target": self._to_non_negative_int(card_payload.get("search_target", 0)),
            "search_performed": 0,
            "search_effect_keys": [],
            "maps_target": self._to_non_negative_int(card_payload.get("maps_target", 0)),
            "maps_performed": 0,
            "maps_effect_keys": [],
        }

    def _submit_card_targets(
        self,
        executor: concurrent.futures.Executor,
        card_payload: dict[str, object],
        card_id: int,
    ) -> dict[concurrent.futures.Future, tuple[int, str]]:
        """Создает фоновые задачи для search/maps целей одной карточки."""
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
        submitted: dict[concurrent.futures.Future, tuple[int, str]] = {}
        for mode, target, keys in target_specs:
            if target <= 0 or not keys:
                continue
            self._log(f"Карточка #{card_id}: запускаю {mode}-задачу, target={target}, ключей={len(keys)}")
            future = executor.submit(self._run_target_loop, keys, target, card_payload, mode)
            submitted[future] = (card_id, mode)
        return submitted

    @staticmethod
    def _card_key_payloads(card_payload: dict[str, object]) -> list[dict[str, object]]:
        """Возвращает список ключей карточки, если он передан в ожидаемом формате."""
        key_payloads = card_payload.get("keys", [])
        return key_payloads if isinstance(key_payloads, list) else []

    def _apply_target_result(
        self,
        card_entry: dict[str, object],
        mode: str,
        result: dict[str, object],
    ) -> None:
        """Записывает результат выполненной search/maps задачи в строку карточки."""
        card_entry[f"{mode}_performed"] = int(result["performed"])
        card_entry[f"{mode}_effect_keys"] = result["effect_keys"]
        self._log(f"Карточка #{card_entry['card_id']}: {mode} завершен, выполнено={card_entry[f'{mode}_performed']}")

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
        self._log(
            f"Оптимизация завершена. search={totals['search_performed']}/{totals['search_target']}, "
            f"maps={totals['maps_performed']}/{totals['maps_target']}"
        )
        return {
            "processed_cards": len(results),
            "total_search_target": totals["search_target"],
            "total_search_performed": totals["search_performed"],
            "total_maps_target": totals["maps_target"],
            "total_maps_performed": totals["maps_performed"],
            "cards": results,
        }

    def _run_target_loop(self, keys: list[dict[str, object]], target: int, card_payload: dict[str, object], mode: str) -> dict[str, object]:
        """Выполняет нужное количество результативных действий по ключам."""
        if target <= 0 or not keys:
            self._log(f"Цикл {mode}: пропуск, target={target}, ключей={len(keys)}")
            return {"performed": 0, "effect_keys": []}

        active_keys = list(keys)
        performed = 0
        effect_key_ids: set[int] = set()
        self._log(f"Цикл {mode}: старт, target={target}, ключей={len(active_keys)}")
        while performed < target and active_keys:
            pass_keys = random.sample(active_keys, len(active_keys))
            effectful_keys_this_pass: list[dict[str, object]] = []
            for key_payload in pass_keys:
                if performed >= target:
                    break
                if mode == "search":
                    effect = self._simulate_search_action(key_payload, card_payload)
                else:
                    effect = self._simulate_browser_action_one_second(key_payload, card_payload)
                if not effect:
                    self._log(f"Цикл {mode}: действие по ключу не дало эффекта.")
                    continue
                performed += 1
                key_id = int(key_payload.get("id", 0))
                if key_id:
                    effect_key_ids.add(key_id)
                effectful_keys_this_pass.append(key_payload)
                self._log(f"Цикл {mode}: засчитано действие {performed}/{target}.")

            if performed >= target:
                break
            if not effectful_keys_this_pass:
                self._log(f"Цикл {mode}: нет результативных ключей в проходе, завершаю.")
                break
            active_keys = effectful_keys_this_pass

        self._log(f"Цикл {mode}: завершен, выполнено={performed}.")
        return {"performed": performed, "effect_keys": sorted(effect_key_ids)}

    def _simulate_search_action(self, key_payload: dict[str, object], card_payload: dict[str, object]) -> bool:
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
                        self._log("simulate_search_action: нажимаю Esc 3 раза с интервалом 0.5с")
                        for _ in range(3):
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(500)
                    
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
            self._log(f"simulate_search_action: ошибка {error}")
            return False

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
        self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия карточки организации")
        self._run_target_overview_activity(page, min_sleep_overview, max_sleep_overview)
        return True

    @staticmethod
    def _click_organization_card(item_locator, title_locator) -> None:
        """Кликает по карточке организации через overlay или заголовок."""
        overlay_locator = item_locator.locator(".OrgCard-Overlay").first
        try:
            if overlay_locator.count() > 0:
                overlay_locator.click(force=True)
            else:
                title_locator.click(force=True)
        except Exception:
            return

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
        end_time = page.evaluate("Date.now()") + (sleep_time_sec * 1000)
        visited_sections: set[str] = set()
        actions = {
            "scroll": lambda: self._browse_card_screen(page),
            "click_photos": lambda: self._browse_photo_section(page, visited_sections),
            "click_reviews": lambda: self._browse_reviews_section(page, visited_sections),
            "idle": lambda: self._idle_on_current_screen(page),
        }

        while page.evaluate("Date.now()") < end_time:
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
        """Открывает фото, листает галерею и иногда рассматривает отдельные изображения."""
        try:
            photo_btn = page.locator(".PhotoTiles-More button, .OrgGallery-PhotoTiles .PhotoTiles-Item").first
            if photo_btn.count() == 0:
                return
            self._log("Заметил раздел с фотографиями, скроллю к нему...")
            photo_btn.scroll_into_view_if_needed()
            page.wait_for_timeout(random.randint(1000, 2500))
            if not photo_btn.is_visible():
                return

            visited_sections.add("photos")
            section_start = time.time()
            self._log("Открываю раздел фото...")
            photo_btn.click()
            self._handle_captcha_if_present(page, wait_ms=2000, context="после открытия раздела фото")
            wait_time = random.randint(1000, 3000)
            self._log(f"Смотрю открывшиеся фото ({wait_time} мс)...")
            page.wait_for_timeout(wait_time)
            self._scroll_photo_gallery(page)
            self._close_section(
                page,
                ".Gallery-CloseButton, .Modal-CloseButton, .OneOrgModal-CloseButton",
                "фото",
                section_start,
            )
        except Exception:
            return

    def _scroll_photo_gallery(self, page) -> None:
        """Листает фотогалерею и выборочно открывает отдельные фото."""
        scroll_steps = random.randint(3, 6)
        self._log(f"Делаю {scroll_steps} скроллов вниз по галерее фото, рассматривая их...")
        for _ in range(scroll_steps):
            self._scroll_visible_content(page, random.randint(200, 600))
            page.wait_for_timeout(random.randint(2000, 4000))
            if random.random() < 0.4:
                self._open_random_gallery_photo(page)

    def _open_random_gallery_photo(self, page) -> None:
        """Открывает случайную видимую фотографию и закрывает ее после просмотра."""
        try:
            inner_photos = page.locator(".MediaGallery-Item, .PhotoTiles-Item, .Gallery-Item")
            count = inner_photos.count()
            if count <= 0:
                return
            idx = random.randint(0, min(count - 1, 10))
            photo_to_click = inner_photos.nth(idx)
            if not photo_to_click.is_visible():
                return
            self._log(f"Кликаю на конкретное фото №{idx + 1} для увеличения...")
            photo_to_click.click()
            self._handle_captcha_if_present(page, wait_ms=2000, context=f"после открытия фото №{idx + 1}")
            view_time = random.randint(3000, 7000)
            self._log(f"Рассматриваю увеличенное фото ({view_time} мс)...")
            page.wait_for_timeout(view_time)
            self._log("Закрываю увеличенное фото (Escape).")
            page.keyboard.press("Escape")
            page.wait_for_timeout(random.randint(1000, 2000))
        except Exception:
            return

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
        """Пытается раскрыть один длинный отзыв."""
        try:
            expand_btns = page.locator(".Review-MoreText, .ReviewText-More, .BusinessReviews-MoreText")
            count = expand_btns.count()
            if count <= 0:
                return
            btn = expand_btns.nth(random.randint(0, count - 1))
            if not btn.is_visible():
                return
            self._log("Разворачиваю длинный отзыв ('Читать полностью')...")
            btn.click()
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
    ) -> bool:
        """Открывает Яндекс.Карты и выполняет там запрос (только по фразе/ключу)."""
        phrase = str(key_payload.get("phrase", ""))
        query = phrase.strip()
        if not query:
            self._log("simulate_maps_action: пустой запрос, пропуск.")
            return False

        maps_wait_after_input_ms = 15000
        self._log(f"simulate_maps_action: старт для запроса '{query}'.")
        maps_url = self._build_maps_url(card_payload)
        browsers_path = self._prepare_runtime_browsers_path()
        try:
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
                found = self._find_and_open_maps_organization(page, organization, maps_wait_after_input_ms)
                
                if not found:
                    self._log("simulate_maps_action: организация не найдена на картах, жду 15 секунд.")
                    page.wait_for_timeout(maps_wait_after_input_ms)

                self._close_browser_session(context, browser, "simulate_browser_action_one_second")
            self._log("simulate_maps_action: завершено успешно.")
            return True
        except Exception as error:
            self._log(f"simulate_maps_action: ошибка {error}")
            return False

    def _find_and_open_maps_organization(
        self,
        page,
        organization: str,
        maps_wait_after_input_ms: int,
    ) -> bool:
        """Ищет целевую организацию в списке на странице Яндекс.Карт и кликает по ней."""
        if not organization:
            self._log("simulate_maps_action: название организации пустое, поиск в списке невозможен.")
            return False

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
                if organization.lower() in actual_title.lower():
                    self._log(f"simulate_maps_action: найдена организация '{actual_title}'.")
                    page.wait_for_timeout(random.choice([1000, 1300, 1700, 2000]))
                    
                    try:
                        title_locator.click(force=True)
                    except Exception:
                        pass
                    
                    self._handle_captcha_if_present(page, wait_ms=3000, context="после открытия карточки организации на картах")
                    self._log("simulate_maps_action: жду 15 секунд после открытия карточки в картах.")
                    page.wait_for_timeout(maps_wait_after_input_ms)
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
        """Закрывает контекст и браузер, сохраняя понятный лог места вызова."""
        self._log(f"{label}: закрываю контекст и браузер.")
        context.close()
        browser.close()

    def _launch_human_like_browser(self, playwright):
        """Запускает локальный Chrome или встроенный Chromium в видимом режиме."""
        launch_options = {
            "headless": False,
            "args": ["--start-maximized"],
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
        """Скроллит видимый контент, избегая карты и списка организаций."""
        page.evaluate(
            """
            (delta) => {
                const isVisible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return (
                        rect.width > 0 &&
                        rect.height > 0 &&
                        style.visibility !== 'hidden' &&
                        style.display !== 'none'
                    );
                };

                const isMapRelated = (el) => {
                    const marker = `${el.className || ''} ${el.id || ''}`.toLowerCase();
                    return (
                        marker.includes('ymaps') ||
                        marker.includes('map-container') ||
                        marker.includes('map-pane')
                    );
                };

                const isOrgListRelated = (el) => {
                    const marker = `${el.className || ''} ${el.id || ''}`.toLowerCase();
                    return (
                        marker.includes('verticalorgsscroller') ||
                        marker.includes('orgcard') ||
                        marker.includes('orgmncolumn')
                    );
                };

                const candidates = Array.from(document.querySelectorAll('body *'))
                    .filter((el) => el instanceof HTMLElement)
                    .filter((el) => isVisible(el))
                    .filter((el) => !isMapRelated(el))
                    .filter((el) => !isOrgListRelated(el))
                    .filter((el) => !el.closest('[class*="ymaps"], [id*="ymaps"]'))
                    .filter((el) => !el.closest('[class*="VerticalOrgsScroller"], [class*="OrgCard"], [class*="OrgmnColumn"]'))
                    .filter((el) => {
                        const style = window.getComputedStyle(el);
                        const overflowY = style.overflowY;
                        const scrollable = el.scrollHeight > el.clientHeight + 20;
                        return scrollable && ['auto', 'scroll', 'overlay'].includes(overflowY);
                    })
                    .sort((a, b) => b.clientHeight - a.clientHeight);

                const target = candidates[0];
                if (target) {
                    target.scrollTop += delta;
                    return;
                }

                window.scrollBy(0, delta);
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
        self._log("Фокусирую поле поиска и ввожу запрос.")
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
        self._log("Отправляю запрос клавишей Enter.")
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
        print(f"[{now}] [SearchRunnerService] {message}", flush=True)
