@echo off
setlocal
set "PROJECT_ROOT=%~dp0"
set "VENV_PYTHON=%PROJECT_ROOT%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Virtual environment not found. Run setup.bat first.
    exit /b 1
)

"%VENV_PYTHON%" "%PROJECT_ROOT%main.py" %*
exit /b %ERRORLEVEL%
