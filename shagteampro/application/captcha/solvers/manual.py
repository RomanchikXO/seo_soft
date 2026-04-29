from __future__ import annotations

import logging
from dataclasses import dataclass

from shagteampro.application.captcha.detector import CaptchaChallenge


@dataclass(frozen=True)
class ManualCaptchaSolver:
    """Ручной solver: кликает чекбокс капчи и ждет, пока оператор решит ее в браузере."""

    solve_wait_ms: int = 30000
    name: str = "manual"

    def solve(self, page, challenge: CaptchaChallenge, context: str) -> bool:
        """Нажимает на найденную капчу и оставляет браузер открытым для ручного решения."""
        try:
            challenge.locator.click()
            page.wait_for_timeout(self.solve_wait_ms)
            self._log(
                f"Ручной solver завершил ожидание {self.solve_wait_ms} мс "
                f"для контекста '{context}'."
            )
            return True
        except Exception as error:
            self._log(f"Ручной solver не смог обработать капчу ({context}): {error}")
            return False

    def _log(self, message: str) -> None:
        import datetime

        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logging.info(f"[{now_str}] [ManualCaptchaSolver] {message}")
