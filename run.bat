@echo off
REM Запуск ShagTeamPro в Windows. main.py сам поставит окружение, зависимости и Chromium.
setlocal
cd /d "%~dp0"

REM Пытаемся использовать py launcher, иначе python из PATH.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 main.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python main.py
    goto :end
)

echo Python 3.12+ не найден. Установите его с https://www.python.org/downloads/ и отметьте "Add Python to PATH".
pause
exit /b 1

:end
if %errorlevel% neq 0 pause
endlocal
