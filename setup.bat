@echo off
setlocal
set "PROJECT_ROOT=%~dp0"

powershell -ExecutionPolicy Bypass -File "%PROJECT_ROOT%setup.ps1" %*
exit /b %ERRORLEVEL%
