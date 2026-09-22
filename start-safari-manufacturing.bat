@echo off
setlocal EnableExtensions

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Virtual environment not found. Run setup.bat first.
    exit /b 1
)

if not exist "%PROJECT_ROOT%\ui\package.json" (
    echo UI project not found under "%PROJECT_ROOT%\ui".
    exit /b 1
)

echo Stopping any previous Safari Manufacturing instance...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=[IO.Path]::GetFullPath($env:PROJECT_ROOT);" ^
  "$uiRoot=[IO.Path]::GetFullPath((Join-Path $root 'ui'));" ^
  "$self=$PID;" ^
  "$procs=@(Get-CimInstance Win32_Process);" ^
  "$targets=@($procs ^| Where-Object {" ^
    "$c=[string]$_.CommandLine;" ^
    "$isApi=(($c -match 'run-web\.ps1') -and ($c -match [regex]::Escape($root))) -or (($c -match 'app\.web:app') -and ($c -match '--port\s+4321'));" ^
    "$isUi=(($c -match 'vite') -and ($c -match '--port\s+4320') -and ($c -match [regex]::Escape($uiRoot))) -or (($c -match 'npm(?:\.cmd)?\s+run\s+dev') -and ($c -match [regex]::Escape($uiRoot)));" ^
    "$isApi -or $isUi;" ^
  "});" ^
  "$ids=New-Object 'System.Collections.Generic.HashSet[int]';" ^
  "foreach($p in $targets){ if([int]$p.ProcessId -ne $self){ [void]$ids.Add([int]$p.ProcessId) } };" ^
  "$changed=$true;" ^
  "while($changed){ $changed=$false; foreach($p in $procs){ if($ids.Contains([int]$p.ParentProcessId) -and $ids.Add([int]$p.ProcessId)){ $changed=$true } } };" ^
  "foreach($id in $ids){ Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }"

timeout /t 2 /nobreak >nul

echo Starting Safari Manufacturing API on http://127.0.0.1:4321 ...
start "Safari Manufacturing API" /D "%PROJECT_ROOT%" powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -File "%PROJECT_ROOT%\run-web.ps1"

echo Starting Safari Manufacturing UI on http://127.0.0.1:4320 ...
start "Safari Manufacturing UI" /D "%PROJECT_ROOT%\ui" powershell.exe -NoProfile -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%PROJECT_ROOT%\ui'; npm.cmd run dev -- --host 127.0.0.1 --port 4320"

echo.
echo Safari Manufacturing is starting.
echo Open http://127.0.0.1:4320/ in your browser.
echo.

endlocal
