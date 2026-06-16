from __future__ import annotations

import base64
import datetime
import logging
import re
from dataclasses import dataclass
from typing import Callable

from shagteampro.application.captcha.detector import CaptchaChallenge


@dataclass(frozen=True)
class ImageCoordinateCaptchaSolver:
    """Базовый solver Yandex SmartCaptcha, решаемой кликами по координатам на картинке.

    Инкапсулирует общий для всех сервисов сценарий: клик по чекбоксу, ожидание
    сложной капчи, скачивание изображений, масштабирование полученных координат
    и клики по картинке с последующей отправкой формы. Конкретные сервисы
    (Capsola, BotLab) переопределяют только обмен со своим API — методы
    :meth:`_create_task` и :meth:`_wait_for_result`.
    """

    token: str
    name: str = "image-coordinate"
    service_label: str = "сервис"
    logger: Callable[[str], None] | None = None
    max_attempts: int = 5
    poll_attempts: int = 20
    poll_interval_ms: int = 2000

    ADVANCED_SELECTOR = ".AdvancedCaptcha-ImageWrapper"
    MAIN_IMAGE_SELECTOR = ".AdvancedCaptcha-ImageWrapper img"
    TASK_IMAGE_SELECTOR = (
        "img.TaskImage, .AdvancedCaptcha-Silhouette img, .AdvancedCaptcha-TaskImage img"
    )
    SUBMIT_SELECTOR = (
        ".AdvancedCaptcha-FormActions button[type='submit'], "
        ".AdvancedCaptcha-FormActions button:has-text('Отправить')"
    )

    def solve(self, page, challenge: CaptchaChallenge, context: str) -> bool:
        """Решает SmartCaptcha: кликает чекбокс, отправляет картинки в сервис и кликает по координатам."""
        if not self.token:
            self._log(f"Токен {self.service_label} не задан, невозможно решить капчу.")
            return False

        self._log(f"Начало решения капчи ({context}) через {self.service_label}...")
        if not self._click_checkbox(page, challenge):
            return False

        advanced_locator = page.locator(self.ADVANCED_SELECTOR)
        try:
            advanced_locator.wait_for(state="visible", timeout=5000)
            self._log("Появилась сложная картинка капчи.")
        except Exception:
            self._log(
                "Сложная капча не появилась после клика. "
                "Передаю управление сервису для финальной проверки исчезновения капчи."
            )
            return True

        for attempt in range(1, self.max_attempts + 1):
            self._log(f"Попытка решения #{attempt}")
            try:
                if self._solve_once(page, advanced_locator, attempt):
                    self._log("Капча успешно пройдена на уровне окна AdvancedCaptcha.")
                    return True
                self._log(f"Попытка #{attempt}: окно AdvancedCaptcha осталось на экране, будет повтор.")
            except Exception as error:
                self._log(f"Попытка #{attempt}: исключение во время решения: {error}")
                page.wait_for_timeout(1000)

        self._log(f"Не удалось пройти капчу за {self.max_attempts} попытки.")
        return False

    def _click_checkbox(self, page, challenge: CaptchaChallenge) -> bool:
        """Кликает по чекбоксу капчи, если он виден. Возвращает False при ошибке клика."""
        try:
            if challenge.locator.is_visible():
                self._log("Клик по чекбоксу капчи...")
                challenge.locator.click()
                page.wait_for_timeout(2000)
            return True
        except Exception as error:
            self._log(f"Не удалось кликнуть чекбокс капчи: {error}")
            return False

    def _solve_once(self, page, advanced_locator, attempt: int) -> bool:
        """Выполняет один цикл решения и возвращает True, если капча исчезла с экрана."""
        main_img_loc = page.locator(self.MAIN_IMAGE_SELECTOR).first
        task_img_loc = page.locator(self.TASK_IMAGE_SELECTOR).first
        if not main_img_loc.is_visible() or not task_img_loc.is_visible():
            self._log(f"Попытка #{attempt}: не удалось найти изображения (основное или задание).")
            return False

        main_url = main_img_loc.get_attribute("src")
        task_url = task_img_loc.get_attribute("src")
        if not main_url or not task_url:
            self._log(f"Попытка #{attempt}: не удалось получить URL изображений.")
            return False

        self._log("Скачивание изображений капчи...")
        main_b64 = self._download_and_encode(page, main_url)
        task_b64 = self._download_and_encode(page, task_url)
        if not main_b64 or not task_b64:
            self._log(f"Попытка #{attempt}: ошибка скачивания/кодирования изображений.")
            return False

        self._log(f"Отправка капчи в API {self.service_label}...")
        task_id = self._create_task(page, main_b64, task_b64)
        if not task_id:
            self._log(f"Попытка #{attempt}: {self.service_label} не вернул task_id.")
            return False

        self._log(f"Задача создана, ID: {task_id}. Ожидание результата...")
        solution_str = self._wait_for_result(page, task_id)
        if not solution_str:
            self._log(f"Попытка #{attempt}: не удалось получить решение от {self.service_label}.")
            return False

        self._log(f"Получено решение: {solution_str}")
        coords = self._parse_coordinates(solution_str)
        if not coords:
            self._log(f"Попытка #{attempt}: решение не содержит валидных координат.")
            return False

        self._log(f"Распарсены координаты: {coords}")
        if not self._click_coordinates(page, main_img_loc, coords, attempt):
            return False

        self._submit(page)
        return not advanced_locator.is_visible()

    def _click_coordinates(self, page, main_img_loc, coords, attempt: int) -> bool:
        """Масштабирует координаты под отображаемый размер картинки и кликает по ним."""
        natural_width = main_img_loc.evaluate("el => el.naturalWidth")
        box = main_img_loc.bounding_box()
        if not natural_width or not box:
            self._log(f"Попытка #{attempt}: не удалось получить размеры изображения для масштабирования.")
            return False

        scale = box["width"] / natural_width
        self._log(f"naturalWidth={natural_width}, box.width={box['width']}, масштаб={scale:.4f}")
        for index, (x, y) in enumerate(coords, 1):
            target_x = x * scale
            target_y = y * scale
            self._log(f"Клик {index}: оригинальные ({x}, {y}) -> масштабированные ({target_x:.1f}, {target_y:.1f})")
            # force=True обязателен: контейнер силуэт-капчи (.AdvancedCaptcha-ImageWrapper)
            # перехватывает события указателя, из-за чего обычный клик зависает на 30с.
            main_img_loc.click(position={"x": target_x, "y": target_y}, force=True)
            page.wait_for_timeout(500)
        return True

    def _submit(self, page) -> None:
        """Нажимает кнопку подтверждения капчи (если она присутствует)."""
        submit_btn = page.locator(self.SUBMIT_SELECTOR).first
        if submit_btn.is_visible():
            self._log("Нажатие кнопки подтверждения капчи...")
            submit_btn.click()
        else:
            self._log("Кнопка подтверждения не найдена, возможно подтверждение автоматическое.")
        page.wait_for_timeout(2000)

    def _download_and_encode(self, page, url: str) -> str | None:
        """Скачивает изображение по URL и возвращает его в base64 (или None при ошибке)."""
        try:
            resp = page.context.request.get(url)
            if not resp.ok:
                return None
            return base64.b64encode(resp.body()).decode("utf-8")
        except Exception as error:
            self._log(f"Ошибка скачивания {url}: {error}")
            return None

    def _parse_coordinates(self, solution: str) -> list[tuple[float, float]]:
        """Разбирает строку решения в список координат.

        Поддерживает форматы `coordinates:x=..,y=..`, `coordinate:x=..,y=..`
        и `OK|coordinate:..`, разделяя точки с запятой.
        """
        if "|" in solution:
            solution = solution.split("|", 1)[1]
        for prefix in ("coordinates:", "coordinate:"):
            if solution.startswith(prefix):
                solution = solution[len(prefix):]
                break

        coords: list[tuple[float, float]] = []
        for part in filter(None, (segment.strip() for segment in solution.split(";"))):
            x_match = re.search(r"x\s*=\s*([\d.]+)", part)
            y_match = re.search(r"y\s*=\s*([\d.]+)", part)
            if x_match and y_match:
                coords.append((float(x_match.group(1)), float(y_match.group(1))))
            else:
                self._log(f"Не удалось распарсить координату из части '{part}'.")
        return coords

    def _create_task(self, page, main_b64: str, task_b64: str) -> str | None:
        """Создаёт задачу в API сервиса и возвращает её идентификатор (переопределяется наследником)."""
        raise NotImplementedError

    def _wait_for_result(self, page, task_id: str) -> str | None:
        """Опрашивает API сервиса до получения решения (переопределяется наследником)."""
        raise NotImplementedError

    def _log(self, message: str) -> None:
        """Пишет сообщение в лог через переданный логгер приложения или logging.info."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{now_str}] [{type(self).__name__}] {message}"
        if self.logger is not None:
            self.logger(line)
        else:
            logging.info(line)
