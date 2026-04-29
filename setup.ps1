param(
    [switch] $Recreate
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

if ($Recreate -and (Test-Path -LiteralPath $VenvDir)) {
    Remove-Item -LiteralPath $VenvDir -Recurse -Force
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Python310 = $null
    try {
        $Python310 = & py -3.10 -c "import sys; print(sys.executable)" 2>$null
    }
    catch {
        $Python310 = $null
    }

    if ($Python310) {
        & py -3.10 -m venv $VenvDir
    }
    else {
        & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Python 3.10 or newer is required. Install Python 3.10+ and rerun setup.ps1."
        }
        & python -m venv $VenvDir
    }
}

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r $Requirements
& $VenvPython --version
