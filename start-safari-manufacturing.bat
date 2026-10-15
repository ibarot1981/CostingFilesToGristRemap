@echo off
setlocal EnableExtensions

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

if not exist "%PROJECT_ROOT%\ui\package.json" (
    echo UI project not found under "%PROJECT_ROOT%\ui".
    pause
    exit /b 1
)

"%VENV_PYTHON%" "%PROJECT_ROOT%\scripts\safari_launcher.py" --project-root "%PROJECT_ROOT%"
if errorlevel 1 (
    pause
    exit /b 1
)

echo Open http://127.0.0.1:4320/
endlocal
