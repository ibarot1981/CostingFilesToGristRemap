$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}
& $python -m uvicorn app.web:app --host 127.0.0.1 --port 4321 --reload
