from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from playwright.sync_api import Page

from shagteampro.application.captcha.detector import CaptchaChallenge
from shagteampro.application.captcha.solvers.base import CaptchaSolver


@dataclass(frozen=True)
class CapsolaCaptchaSolver(CaptchaSolver):
    """
    Решатель капчи Yandex SmartCaptcha с использованием API Capsola.
    """

    token: str
    name: str = "capsola"

    def solve(self, page: Page, challenge: CaptchaChallenge, context: str) -> bool:
        """
        Решает SmartCaptcha:
        1. Кликает чекбокс (если есть).
        2. Ждет появления расширенной капчи.
        3. Скачивает картинки, отправляет в Capsola.
        4. Получает координаты, вычисляет масштаб и кликает.
        """
        if not self.token:
            self._log("Токен Capsola не задан, невозможно решить капчу.")
            return False

        self._log(f"Начало решения капчи ({context}) через Capsola...")

        # 1. Клик по чекбоксу
        try:
            if challenge.locator.is_visible():
                self._log("Клик по чекбоксу капчи...")
                challenge.locator.click()
                page.wait_for_timeout(2000)
        except Exception as error:
            self._log(f"Не удалось кликнуть чекбокс капчи: {error}")
            return False

        # 2. Ждем расширенную капчу
        advanced_captcha_selector = ".AdvancedCaptcha-ImageWrapper"
        advanced_locator = page.locator(advanced_captcha_selector)

        try:
            advanced_locator.wait_for(state="visible", timeout=5000)
            self._log("Появилась сложная картинка капчи.")
        except Exception:
            self._log(
                "Сложная капча не появилась после клика. "
                "Передаю управление сервису для финальной проверки исчезновения капчи."
            )
            return True

        for attempt in range(3):
            self._log(f"Попытка решения #{attempt + 1}")
            try:
                # 3. Находим картинки
                main_img_loc = page.locator(".AdvancedCaptcha-ImageWrapper img").first
                task_img_loc = page.locator(
                    "img.TaskImage, .AdvancedCaptcha-Silhouette img, .AdvancedCaptcha-TaskImage img"
                ).first

                if not main_img_loc.is_visible() or not task_img_loc.is_visible():
                    self._log(
                        f"Попытка #{attempt + 1}: не удалось найти изображения "
                        "(основное или задание)."
                    )
                    continue

                main_url = main_img_loc.get_attribute("src")
                task_url = task_img_loc.get_attribute("src")

                if not main_url or not task_url:
                    self._log(
                        f"Попытка #{attempt + 1}: не удалось получить URL "
                        f"(main_url={bool(main_url)}, task_url={bool(task_url)})."
                    )
                    continue

                self._log("Скачивание изображений капчи...")
                main_b64 = self._download_and_encode(page, main_url)
                task_b64 = self._download_and_encode(page, task_url)
                if not main_b64 or not task_b64:
                    self._log(
                        f"Попытка #{attempt + 1}: ошибка скачивания/кодирования "
                        "изображений для Capsola."
                    )
                    continue

                # 4. Отправляем в Capsola
                self._log("Отправка капчи в API Capsola...")
                task_id = self._create_task(page, main_b64, task_b64)
                if not task_id:
                    self._log(f"Попытка #{attempt + 1}: Capsola не вернула task_id.")
                    continue

                self._log(f"Задача создана, ID: {task_id}. Ожидание результата...")

                # 5. Опрашиваем результат
                solution_str = self._wait_for_result(page, task_id)
                if not solution_str:
                    self._log(f"Попытка #{attempt + 1}: не удалось получить решение от Capsola.")
                    continue

                self._log(f"Получено решение: {solution_str}")

                # 6. Парсинг координат
                coords = self._parse_coordinates(solution_str)
                if not coords:
                    self._log(
                        f"Попытка #{attempt + 1}: решение не содержит валидных координат."
                    )
                    continue

                self._log(f"Распарсены координаты: {coords}")

                # 7. Вычисление масштаба и клики
                natural_width = main_img_loc.evaluate("el => el.naturalWidth")
                box = main_img_loc.bounding_box()
                if not natural_width or not box:
                    self._log(
                        f"Попытка #{attempt + 1}: не удалось получить размеры "
                        "изображения для масштабирования."
                    )
                    continue

                scale = box["width"] / natural_width
                self._log(f"naturalWidth={natural_width}, box.width={box['width']}, масштаб={scale:.4f}")

                for i, (x, y) in enumerate(coords, 1):
                    target_x = x * scale
                    target_y = y * scale
                    self._log(
                        f"Клик {i}: оригинальные ({x}, {y}) -> "
                        f"масштабированные ({target_x:.1f}, {target_y:.1f})"
                    )
                    main_img_loc.click(position={"x": target_x, "y": target_y})
                    page.wait_for_timeout(500)

                # 8. Нажимаем кнопку Отправить
                submit_btn = page.locator(
                    ".AdvancedCaptcha-FormActions button[type='submit'], "
                    ".AdvancedCaptcha-FormActions button:has-text('Отправить')"
                ).first
                if submit_btn.is_visible():
                    self._log("Нажатие кнопки подтверждения капчи...")
                    submit_btn.click()
                    page.wait_for_timeout(2000)
                else:
                    self._log("Кнопка подтверждения не найдена, возможно подтверждение автоматическое.")
                    page.wait_for_timeout(2000)

                if not advanced_locator.is_visible():
                    self._log("Капча успешно пройдена на уровне окна AdvancedCaptcha.")
                    return True

                self._log(
                    f"Попытка #{attempt + 1}: окно AdvancedCaptcha осталось на экране, "
                    "будет повтор."
                )
            except Exception as error:
                self._log(f"Попытка #{attempt + 1}: исключение во время решения: {error}")
                page.wait_for_timeout(1000)

        self._log("Не удалось пройти капчу за 3 попытки.")
        self._log("Обработка Capsola завершена с ошибкой.")
        return False

    def _download_and_encode(self, page: Page, url: str) -> str | None:
        try:
            resp = page.context.request.get(url)
            if not resp.ok:
                return None
            return base64.b64encode(resp.body()).decode("utf-8")
        except Exception as e:
            self._log(f"Ошибка скачивания {url}: {e}")
            return None

    def _create_task(self, page: Page, main_b64: str, task_b64: str) -> str | None:
        try:
            resp = page.context.request.post(
                "https://api.capsola.cloud/create",
                data={
                    "type": "SmartCaptcha",
                    "api_key": self.token,
                    "image": main_b64,
                    "question": task_b64,
                }
            )
            if not resp.ok:
                self._log(f"Ошибка Capsola create: {resp.status} {resp.status_text}")
                return None
            
            data = resp.json()
            if data.get("error") == 0 and "id" in data:
                return str(data["id"])
            self._log(f"Ответ с ошибкой от Capsola: {data}")
            return None
        except Exception as e:
            self._log(f"Исключение при POST в Capsola: {e}")
            return None

    def _wait_for_result(self, page: Page, task_id: str, max_retries: int = 20) -> str | None:
        for _ in range(max_retries):
            try:
                resp = page.context.request.post(
                    "https://api.capsola.cloud/result",
                    data={
                        "api_key": self.token,
                        "id": task_id
                    }
                )
                if resp.ok:
                    data = resp.json()
                    if data.get("status") == "ready":
                        return data.get("solution")
                    elif data.get("status") == "processing":
                        page.wait_for_timeout(2000)
                        continue
                    else:
                        self._log(f"Неизвестный статус/ошибка из Capsola: {data}")
                        return None
            except Exception as e:
                self._log(f"Исключение при опросе результата: {e}")
            page.wait_for_timeout(2000)
        return None

    def _parse_coordinates(self, solution: str) -> list[tuple[float, float]]:
        # solution может быть "coordinates:x=56,y=82" или "coordinates:x=56,y=82;x=10,y=20"
        coords = []
        prefix = "coordinates:"
        if solution.startswith(prefix):
            solution = solution[len(prefix):]
        
        parts = solution.split(";")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # x=56,y=82
            xy = part.split(",")
            x_val, y_val = None, None
            try:
                for kv in xy:
                    if kv.startswith("x="):
                        x_val = float(kv[2:])
                    elif kv.startswith("y="):
                        y_val = float(kv[2:])
            except ValueError:
                self._log(f"Не удалось распарсить координату из части '{part}'.")
                continue
            if x_val is not None and y_val is not None:
                coords.append((x_val, y_val))
        return coords

    def _log(self, message: str) -> None:
        import datetime
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f"[{now_str}] [CapsolaCaptchaSolver] {message}")
