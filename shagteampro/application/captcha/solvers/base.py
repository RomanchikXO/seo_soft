from __future__ import annotations

from typing import Protocol

from shagteampro.application.captcha.detector import CaptchaChallenge


class CaptchaSolver(Protocol):
    """Контракт solver'а, который обрабатывает найденную капчу."""

    name: str

    def solve(self, page, challenge: CaptchaChallenge, context: str) -> bool:
        """Возвращает True, если solver считает капчу пройденной."""
