param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $AppArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Error "Virtual environment not found. Run .\setup.ps1 first."
}

& $VenvPython (Join-Path $ProjectRoot "main.py") @AppArgs
exit $LASTEXITCODE
