from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import threading
import venv
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_VENV_DIR = PROJECT_ROOT / ".runtime-venv"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
REQUIREMENTS_MARKER = RUNTIME_VENV_DIR / ".requirements.sha256"
MIN_PYTHON_VERSION = (3, 12)


def _runtime_python_path() -> Path:
    if os.name == "nt":
        return RUNTIME_VENV_DIR / "Scripts" / "python.exe"
    return RUNTIME_VENV_DIR / "bin" / "python3"


def _requirements_hash() -> str:
    content = REQUIREMENTS_FILE.read_bytes()
    return hashlib.sha256(content).hexdigest()


def _run_checked(command: list[str]) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(command)}")


def _is_supported_version(version: tuple[int, int]) -> bool:
    return version >= MIN_PYTHON_VERSION


def _current_version() -> tuple[int, int]:
    return (sys.version_info.major, sys.version_info.minor)


def _version_text(version: tuple[int, int] | None) -> str:
    if version is None:
        return "unknown"
    return f"{version[0]}.{version[1]}"


def _assert_supported_interpreter() -> None:
    current = _current_version()
    if not _is_supported_version(current):
        raise RuntimeError(
            "Python 3.12+ is required. "
            f"Current interpreter: {_version_text(current)}."
        )


def _runtime_python_version(python_path: Path) -> tuple[int, int] | None:
    if not python_path.exists():
        return None
    try:
        result = subprocess.run(
            [str(python_path), "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        major_str, minor_str = raw.split(".", maxsplit=1)
        return (int(major_str), int(minor_str))
    except Exception:
        return None


def _ensure_runtime_environment() -> None:
    _assert_supported_interpreter()

    runtime_python = _runtime_python_path()
    runtime_version = _runtime_python_version(runtime_python)

    if runtime_python.exists() and (runtime_version is None or not _is_supported_version(runtime_version)):
        print("Runtime initialization: recreating runtime venv for Python 3.12+...")
        venv.EnvBuilder(with_pip=True, clear=True).create(RUNTIME_VENV_DIR)
        runtime_version = _runtime_python_version(runtime_python)

    if not runtime_python.exists():
        print("Runtime initialization: creating runtime venv...")
        venv.EnvBuilder(with_pip=True, clear=False).create(RUNTIME_VENV_DIR)
        runtime_version = _runtime_python_version(runtime_python)

    if runtime_version is None or not _is_supported_version(runtime_version):
        raise RuntimeError(
            "Runtime interpreter version is invalid. "
            f"Expected >= {_version_text(MIN_PYTHON_VERSION)}, got {_version_text(runtime_version)}."
        )

    required_hash = _requirements_hash()
    installed_hash = REQUIREMENTS_MARKER.read_text(encoding="utf-8") if REQUIREMENTS_MARKER.exists() else ""
    if installed_hash != required_hash:
        print("Runtime initialization: installing dependencies (one-time)...")
        _run_checked([str(runtime_python), "-m", "ensurepip", "--upgrade"])
        _run_checked([str(runtime_python), "-m", "pip", "install", "--upgrade", "pip"])
        _run_checked([str(runtime_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)])
        REQUIREMENTS_MARKER.write_text(required_hash, encoding="utf-8")

    current_python = Path(sys.executable).resolve()
    target_python = runtime_python.resolve()
    if current_python != target_python:
        os.execv(str(target_python), [str(target_python), str(__file__), "--runtime"])

    _prepare_browser_runtime()


def _prepare_browser_runtime() -> None:
    from shagteampro.application.services.search_runner_service import SearchRunnerService

    print("Runtime initialization: checking Playwright Chromium...")
    installed_now = SearchRunnerService().ensure_chromium_installed()
    if installed_now:
        print("Runtime initialization: Chromium installed.")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _open_browser(url: str) -> None:
    webbrowser.open(url, new=1)


def _run_server() -> None:
    import uvicorn  # type: ignore[reportMissingImports]
    from webapp.server import create_app

    webapp_dir = _resolve_webapp_dir()
    app = create_app(base_dir=webapp_dir)

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"
    threading.Timer(1.2, _open_browser, args=(url,)).start()
    print(f"ShagTeamPro Web starting at: {url}")
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    server.run()


def _resolve_webapp_dir() -> Path:
    webapp_dir = PROJECT_ROOT / "webapp"
    if (webapp_dir / "templates" / "index.html").exists() and (webapp_dir / "static").exists():
        return webapp_dir
    raise RuntimeError("Web resources not found (templates/static).")


def main() -> int:
    try:
        _ensure_runtime_environment()
        _run_server()
        return 0
    except Exception as error:
        print(f"Startup error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
