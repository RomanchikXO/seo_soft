from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import main as app_main


def test_run_checked_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_main.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError):
        app_main._run_checked(["python", "--version"])


def test_find_free_port() -> None:
    port = app_main._find_free_port()
    assert isinstance(port, int)
    assert port > 0


def test_python_version_support_checks() -> None:
    assert app_main._is_supported_version((3, 12)) is True
    assert app_main._is_supported_version((3, 13)) is True
    assert app_main._is_supported_version((3, 11)) is False


def test_runtime_python_version_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    python_path = tmp_path / "python3"
    python_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        app_main.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="3.12\n"),
    )
    assert app_main._runtime_python_version(python_path) == (3, 12)


def test_assert_supported_interpreter_rejects_old_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_main, "_current_version", lambda: (3, 11))
    with pytest.raises(RuntimeError, match="Python 3.12\\+ is required"):
        app_main._assert_supported_interpreter()


def test_resolve_webapp_dir_non_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    webapp_dir = tmp_path / "webapp"
    (webapp_dir / "templates").mkdir(parents=True)
    (webapp_dir / "static").mkdir(parents=True)
    (webapp_dir / "templates" / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(app_main, "PROJECT_ROOT", tmp_path)
    assert app_main._resolve_webapp_dir() == webapp_dir


def test_main_returns_error_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_main, "_ensure_runtime_environment", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert app_main.main() == 1


def test_prepare_browser_runtime_uses_search_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class DummyRunner:
        def ensure_chromium_installed(self) -> bool:
            calls.append("ensure")
            return False

    monkeypatch.setattr(
        "shagteampro.application.services.search_runner_service.SearchRunnerService", DummyRunner
    )
    app_main._prepare_browser_runtime()
    assert calls == ["ensure"]
