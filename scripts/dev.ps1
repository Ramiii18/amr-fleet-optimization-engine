# Start the digital twin for local use: the engine's HTTP service and the browser viewer.
#
#     .\scripts\dev.ps1
#
# The service (amr-api) runs in its own window on http://127.0.0.1:8000. The viewer's development
# server runs in this window on http://127.0.0.1:5173 and forwards /api to the service. Close the
# service's window, and press Ctrl+C here, to stop.
#
# Needs: the project's .venv with the `api` extra, and Node.js with `npm install` done in web\.
# Everything is local: both servers listen on 127.0.0.1 only. The data is synthetic.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root ".venv\Scripts\amr-api.exe"
$web = Join-Path $root "web"

if (-not (Test-Path $api)) {
    Write-Error "amr-api is not installed. Run: .venv\Scripts\python.exe -m pip install -e "".[dev,viz,api]"""
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js is not installed. Install the LTS release, for example: winget install OpenJS.NodeJS.LTS"
}
if (-not (Test-Path (Join-Path $web "node_modules"))) {
    Write-Host "Installing the viewer's packages (first run only)..."
    Push-Location $web
    try { npm ci } finally { Pop-Location }
}

$running = $false
try {
    $running = (Invoke-WebRequest "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
} catch { }

if ($running) {
    Write-Host "The engine's service already answers on http://127.0.0.1:8000."
} else {
    Write-Host "Starting the engine's service in a new window (http://127.0.0.1:8000)..."
    Start-Process -FilePath $api -ArgumentList "--port", "8000" -WorkingDirectory $root
}

Write-Host "Starting the viewer on http://127.0.0.1:5173 (Ctrl+C to stop)..."
Push-Location $web
try { npm run dev -- --open } finally { Pop-Location }
