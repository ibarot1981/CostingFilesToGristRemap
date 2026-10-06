$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}
Set-Location -LiteralPath $PSScriptRoot
$apiArguments = @("-m", "uvicorn", "app.web:app", "--host", "127.0.0.1", "--port", "4321", "--reload")
$configurationPath = Join-Path $PSScriptRoot ".env"
if (Test-Path -LiteralPath $configurationPath) {
    $apiArguments += @("--env-file", $configurationPath)
}
& $python @apiArguments
