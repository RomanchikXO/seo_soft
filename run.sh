#!/usr/bin/env bash
# Запуск ShagTeamPro в macOS/Linux. main.py сам поставит окружение, зависимости и Chromium.
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    exec python3 main.py
elif command -v python >/dev/null 2>&1; then
    exec python main.py
else
    echo "Python 3.12+ не найден. Установите его с https://www.python.org/downloads/"
    exit 1
fi
