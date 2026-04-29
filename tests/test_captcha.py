from __future__ import annotations

from shagteampro.application.captcha import CaptchaDetector, CaptchaResolution, CaptchaService
from shagteampro.application.captcha.detector import CaptchaChallenge
from shagteampro.application.captcha.solvers.manual import ManualCaptchaSolver
from shagteampro.application.services.search_runner_service import SearchRunnerService


class FakeLocator:
    def __init__(
        self,
        visible: bool = False,
        raise_on_wait: bool = False,
        hide_on_click: bool = False,
    ) -> None:
        self.visible = visible
        self.raise_on_wait = raise_on_wait
        self.hide_on_click = hide_on_click
        self.clicks = 0
        self.wait_calls: list[tuple[str, int]] = []

    @property
    def first(self):
        return self

    def wait_for(self, state: str, timeout: int) -> None:
        self.wait_calls.append((state, timeout))
        if self.raise_on_wait or not self.visible:
            raise TimeoutError("not visible")

    def count(self) -> int:
        return 1 if self.visible else 0

    def is_visible(self) -> bool:
        return self.visible

    def click(self) -> None:
        self.clicks += 1
        if self.hide_on_click:
            self.visible = False


class FakePage:
    def __init__(self, locator: FakeLocator) -> None:
        self._locator = locator
        self.locator_queries: list[str] = []
        self.waits: list[int] = []

    def locator(self, selector: str) -> FakeLocator:
        self.locator_queries.append(selector)
        return self._locator

    def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


def test_captcha_detector_returns_challenge_when_visible() -> None:
    locator = FakeLocator(visible=True)
    page = FakePage(locator)

    challenge = CaptchaDetector().detect(page, wait_ms=5000)

    assert isinstance(challenge, CaptchaChallenge)
    assert challenge.locator is locator
    assert locator.wait_calls == [("visible", 5000)]
    assert "CheckboxCaptcha" in page.locator_queries[0]


def test_captcha_detector_returns_none_when_not_visible() -> None:
    locator = FakeLocator(visible=False, raise_on_wait=True)
    page = FakePage(locator)

    assert CaptchaDetector().detect(page, wait_ms=5000) is None


def test_manual_captcha_solver_clicks_and_waits() -> None:
    locator = FakeLocator(visible=True)
    page = FakePage(locator)
    challenge = CaptchaChallenge(selector="#captcha", locator=locator)

    solved = ManualCaptchaSolver(solve_wait_ms=1234).solve(page, challenge, "test")

    assert solved is True
    assert locator.clicks == 1
    assert page.waits == [1234]


def test_captcha_service_resolves_visible_captcha() -> None:
    messages: list[str] = []
    locator = FakeLocator(visible=True, hide_on_click=True)
    page = FakePage(locator)
    service = CaptchaService(
        detector=CaptchaDetector(),
        solver=ManualCaptchaSolver(solve_wait_ms=2000),
        logger=messages.append,
    )

    result = service.check_and_resolve(page, context="после запроса", wait_ms=3000)

    assert result.detected is True
    assert result.solved is True
    assert result.strategy == "manual"
    assert locator.clicks == 1
    assert page.waits == [2000]
    assert any("Капча обнаружена" in message for message in messages)
    assert any("Капча успешно решена" in message for message in messages)


def test_captcha_service_reports_absent_captcha() -> None:
    messages: list[str] = []
    page = FakePage(FakeLocator(visible=False, raise_on_wait=True))
    service = CaptchaService(logger=messages.append)

    result = service.check_and_resolve(page, context="после запроса", wait_ms=10)

    assert result.detected is False
    assert result.solved is False
    assert any("Капча не появилась" in message for message in messages)


def test_captcha_service_reports_unsolved_when_captcha_still_visible() -> None:
    messages: list[str] = []
    locator = FakeLocator(visible=True, hide_on_click=False)
    page = FakePage(locator)
    service = CaptchaService(
        detector=CaptchaDetector(),
        solver=ManualCaptchaSolver(solve_wait_ms=10),
        logger=messages.append,
        verify_wait_ms=1,
    )

    result = service.check_and_resolve(page, context="после запроса", wait_ms=10)

    assert result.detected is True
    assert result.solved is False
    assert any("все еще видна после solver'а" in message for message in messages)


def test_search_runner_delegates_captcha_handling() -> None:
    calls: list[tuple[str, int]] = []

    class FakeCaptchaService:
        def check_and_resolve(self, _page, context: str, wait_ms: int | None = None):
            calls.append((context, wait_ms or 0))
            return CaptchaResolution(
                detected=False,
                solved=False,
                strategy="manual",
                context=context,
                message="Капча отсутствует.",
            )

    service = SearchRunnerService(captcha_service=FakeCaptchaService())
    service._handle_captcha_if_present(object(), wait_ms=777, context="после клика")

    assert calls == [("после клика", 777)]
