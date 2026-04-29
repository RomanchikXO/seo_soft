from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from shagteampro.application.captcha.detector import CaptchaDetector
from shagteampro.application.captcha.solvers.base import CaptchaSolver
from shagteampro.application.captcha.solvers.manual import ManualCaptchaSolver


@dataclass(frozen=True)
class CaptchaResolution:
    """Результат проверки и обработки капчи."""

    detected: bool
    solved: bool
    strategy: str
    context: str
    message: str


class CaptchaService:
    """Единая точка обработки капчи: обнаружить, решить и залогировать результат."""

    def __init__(
        self,
        detector: CaptchaDetector | None = None,
        solver: CaptchaSolver | None = None,
        logger: Callable[[str], None] | None = None,
        default_wait_ms: int = 10000,
        verify_wait_ms: int = 2000,
    ) -> None:
        self._detector = detector or CaptchaDetector()
        self._solver = solver or ManualCaptchaSolver()
        self._logger = logger or (lambda _message: None)
        self._default_wait_ms = default_wait_ms
        self._verify_wait_ms = verify_wait_ms

    def check_and_resolve(
        self,
        page,
        context: str,
        wait_ms: int | None = None,
    ) -> CaptchaResolution:
        """Проверяет страницу на капчу и запускает solver, если капча найдена."""
        resolved_wait_ms = self._default_wait_ms if wait_ms is None else wait_ms
        self._logger(f"Проверяю капчу: {context}. Ожидание до {resolved_wait_ms} мс.")
        challenge = self._detector.detect(page, wait_ms=resolved_wait_ms)
        if challenge is None:
            message = f"Капча не появилась: {context}."
            self._logger(message)
            return CaptchaResolution(
                detected=False,
                solved=False,
                strategy=self._solver.name,
                context=context,
                message=message,
            )

        self._logger(f"Капча обнаружена: {context}. Solver: {self._solver.name}.")
        try:
            solved_by_solver = bool(self._solver.solve(page, challenge, context))
        except Exception as error:
            message = (
                f"Solver {self._solver.name} завершился с ошибкой: {context}. "
                f"Ошибка: {error}"
            )
            self._logger(message)
            return CaptchaResolution(
                detected=True,
                solved=False,
                strategy=self._solver.name,
                context=context,
                message=message,
            )

        if not solved_by_solver:
            message = (
                f"Solver {self._solver.name} не смог решить капчу: {context}. "
                "Причина должна быть в логах solver'а."
            )
            self._logger(message)
            return CaptchaResolution(
                detected=True,
                solved=False,
                strategy=self._solver.name,
                context=context,
                message=message,
            )

        remaining = self._detector.detect(page, wait_ms=self._verify_wait_ms)
        if remaining is not None:
            message = (
                f"Капча все еще видна после solver'а {self._solver.name}: {context}. "
                "Считаю попытку неуспешной."
            )
            self._logger(message)
            return CaptchaResolution(
                detected=True,
                solved=False,
                strategy=self._solver.name,
                context=context,
                message=message,
            )

        message = f"Капча успешно решена solver'ом {self._solver.name}: {context}."
        self._logger(message)
        return CaptchaResolution(
            detected=True,
            solved=True,
            strategy=self._solver.name,
            context=context,
            message=message,
        )

    def guard_step(
        self,
        page,
        context: str,
        action,
        retry_once: bool = True,
    ):
        """Выполняет browser-step и повторяет его один раз, если ошибка была вызвана капчей."""
        try:
            result = action()
        except Exception:
            resolution = self.check_and_resolve(page, f"после ошибки: {context}", wait_ms=3000)
            if not retry_once or not resolution.solved:
                raise
            self._logger(f"Повторяю действие после обработки капчи: {context}.")
            result = action()
        self.check_and_resolve(page, f"после действия: {context}", wait_ms=0)
        return result
