from __future__ import annotations

import pytest

from shagteampro.application.captcha.solvers.botlab import BotlabCaptchaSolver
from shagteampro.application.captcha.solvers.capsola import CapsolaCaptchaSolver
from shagteampro.application.captcha.solvers.image_coordinate import ImageCoordinateCaptchaSolver
from shagteampro.application.services.search_runner_service import SearchRunnerService


class FakeResponse:
    """Поддельный HTTP-ответ Playwright APIRequestContext."""

    def __init__(self, json_data: dict, ok: bool = True, status: int = 200) -> None:
        self._json = json_data
        self.ok = ok
        self.status = status
        self.status_text = "OK" if ok else "ERROR"

    def json(self) -> dict:
        return self._json


class FakeRequestContext:
    """Поддельный request-контекст: отдаёт заранее заданные ответы и пишет вызовы."""

    def __init__(self, post_response: FakeResponse | None = None, get_responses: list[FakeResponse] | None = None) -> None:
        self._post_response = post_response
        self._get_responses = list(get_responses or [])
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def post(
        self,
        url: str,
        data: dict | None = None,
        form: dict | None = None,
        headers: dict | None = None,
    ) -> FakeResponse:
        self.post_calls.append({"url": url, "data": data, "form": form, "headers": headers})
        return self._post_response

    def get(self, url: str, params: dict | None = None) -> FakeResponse:
        self.get_calls.append({"url": url, "params": params})
        return self._get_responses.pop(0)


class FakeContext:
    def __init__(self, request: FakeRequestContext) -> None:
        self.request = request


class FakePage:
    """Поддельная страница: предоставляет request-контекст и считает паузы."""

    def __init__(self, request: FakeRequestContext) -> None:
        self.context = FakeContext(request)
        self.waits: list[int] = []

    def wait_for_timeout(self, timeout: int) -> None:
        self.waits.append(timeout)


def test_parse_coordinates_supports_known_formats() -> None:
    solver = BotlabCaptchaSolver(token="x")

    assert solver._parse_coordinates("OK|coordinate:x=39,y=59;x=252,y=72") == [(39.0, 59.0), (252.0, 72.0)]
    assert solver._parse_coordinates("coordinates:x=10.5,y=20") == [(10.5, 20.0)]
    assert solver._parse_coordinates("x=1,y=2;x=3,y=4") == [(1.0, 2.0), (3.0, 4.0)]


def test_parse_coordinates_skips_invalid_parts() -> None:
    solver = CapsolaCaptchaSolver(token="x")

    assert solver._parse_coordinates("coordinate:x=5,y=6;garbage;x=7,y=8") == [(5.0, 6.0), (7.0, 8.0)]
    assert solver._parse_coordinates("coordinate:;;") == []


def test_log_routes_to_provided_logger() -> None:
    messages: list[str] = []
    solver = BotlabCaptchaSolver(token="x", logger=messages.append)

    solver._log("проверка")

    assert len(messages) == 1
    assert "[BotlabCaptchaSolver] проверка" in messages[0]


def test_solve_logs_reason_through_logger_without_token() -> None:
    messages: list[str] = []
    solver = BotlabCaptchaSolver(token="", logger=messages.append)

    assert solver.solve(FakePage(FakeRequestContext()), object(), "ctx") is False
    assert any("Токен BotLab не задан" in message for message in messages)


def test_solve_returns_false_without_token() -> None:
    assert BotlabCaptchaSolver(token="").solve(FakePage(FakeRequestContext()), object(), "ctx") is False
    assert CapsolaCaptchaSolver(token="").solve(FakePage(FakeRequestContext()), object(), "ctx") is False


def test_base_solver_requires_api_overrides() -> None:
    solver = ImageCoordinateCaptchaSolver(token="x")

    with pytest.raises(NotImplementedError):
        solver._create_task(FakePage(FakeRequestContext()), "main", "task")
    with pytest.raises(NotImplementedError):
        solver._wait_for_result(FakePage(FakeRequestContext()), "1")


def test_botlab_create_task_builds_json_request() -> None:
    request = FakeRequestContext(post_response=FakeResponse({"status": 1, "response": "777"}))
    page = FakePage(request)

    task_id = BotlabCaptchaSolver(token="key")._create_task(page, "main64", "task64")

    assert task_id == "777"
    call = request.post_calls[0]
    assert call["url"] == "https://api.botlab.me/create"
    assert call["headers"] == {"X-API-Key": "key"}
    assert call["data"] == {"type": "SmartCaptcha", "click": "main64", "task": "task64"}


def test_botlab_create_task_returns_none_on_error_payload() -> None:
    request = FakeRequestContext(post_response=FakeResponse({"status": 0, "response": "ERROR_KEY_DOES_NOT_EXIST"}))
    page = FakePage(request)

    assert BotlabCaptchaSolver(token="key")._create_task(page, "m", "t") is None


def test_botlab_wait_for_result_polls_until_ready() -> None:
    responses = [
        FakeResponse({"status": 0, "response": "CAPCHA_NOT_READY"}),
        FakeResponse({"status": 1, "response": "coordinates:x=1,y=2"}),
    ]
    request = FakeRequestContext()
    request.post = lambda url, data=None, form=None, headers=None: responses.pop(0)  # type: ignore[assignment]
    page = FakePage(request)

    result = BotlabCaptchaSolver(token="key")._wait_for_result(page, "777")

    assert result == "coordinates:x=1,y=2"
    assert page.waits == [2000]


def test_capsola_create_task_uses_capsola_endpoint() -> None:
    request = FakeRequestContext(post_response=FakeResponse({"error": 0, "id": 42}))
    page = FakePage(request)

    task_id = CapsolaCaptchaSolver(token="key")._create_task(page, "main64", "task64")

    assert task_id == "42"
    call = request.post_calls[0]
    assert call["url"] == "https://api.capsola.cloud/create"
    assert call["data"] == {
        "type": "SmartCaptcha",
        "api_key": "key",
        "image": "main64",
        "question": "task64",
    }


def test_capsola_wait_for_result_handles_processing_then_ready() -> None:
    request = FakeRequestContext(
        post_response=None,
    )
    # Capsola опрашивает через POST, поэтому переопределяем поведение под последовательность.
    responses = [
        FakeResponse({"status": "processing"}),
        FakeResponse({"status": "ready", "solution": "coordinates:x=3,y=4"}),
    ]
    request.post = lambda url, data=None, form=None: responses.pop(0)  # type: ignore[assignment]
    page = FakePage(request)

    assert CapsolaCaptchaSolver(token="key")._wait_for_result(page, "42") == "coordinates:x=3,y=4"
    assert page.waits == [2000]


class FakeSettingsService:
    def __init__(self, settings: dict[str, str]) -> None:
        self._settings = settings

    def load_settings(self) -> dict[str, str]:
        return self._settings


def test_update_captcha_service_selects_botlab() -> None:
    runner = SearchRunnerService(
        settings_service=FakeSettingsService({"captcha_service": "botlab", "botlab_token": "abc"})
    )

    runner._update_captcha_service()

    solver = runner._captcha_service._solver
    assert isinstance(solver, BotlabCaptchaSolver)
    assert solver.token == "abc"


def test_update_captcha_service_selects_capsola() -> None:
    runner = SearchRunnerService(
        settings_service=FakeSettingsService({"captcha_service": "capsola", "capsola_token": "xyz"})
    )

    runner._update_captcha_service()

    solver = runner._captcha_service._solver
    assert isinstance(solver, CapsolaCaptchaSolver)
    assert solver.token == "xyz"
