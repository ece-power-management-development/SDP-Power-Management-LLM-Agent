[CmdletBinding()]
param(
    [string]$Model = "qwen3:8b",
    [switch]$Demo,
    [switch]$LiveHelics,
    [int]$Interval = 30
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "The local environment was not found. Run .\install_local_agent.ps1 first."
}
if ($Demo -and $LiveHelics) {
    throw "Choose either -Demo or -LiveHelics, not both."
}
if (-not $Demo -and -not $LiveHelics) {
    $Demo = $true
}

function Test-Endpoint([string]$Uri) {
    try {
        Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Endpoint([string]$Uri) {
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if (Test-Endpoint $Uri) { return }
        Start-Sleep -Seconds 1
    }
    throw "Service did not become available at $Uri."
}

$interfaceProcess = $null
$gatewayProcess = $null
$startedInterface = $false
$startedGateway = $false

try {
    if (-not (Test-Endpoint "http://127.0.0.1:8765/api/status")) {
        $interfaceProcess = Start-Process -FilePath $Python -ArgumentList @("app.py", "--host", "127.0.0.1", "--port", "8765") -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Minimized
        $startedInterface = $true
    }
    Wait-Endpoint "http://127.0.0.1:8765/api/status"

    if ($LiveHelics) {
        if (-not (Test-Endpoint "http://127.0.0.1:8766/v1/health")) {
            $gatewayProcess = Start-Process -FilePath $Python -ArgumentList @("agent_gateway.py", "--config", "helics_agent_config.json", "--host", "127.0.0.1", "--port", "8766") -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Minimized
            $startedGateway = $true
        }
        Wait-Endpoint "http://127.0.0.1:8766/v1/health"
        $contextUrl = "http://127.0.0.1:8766/v1/context"
        Write-Host "Using live HELICS context." -ForegroundColor Green
    } else {
        $contextUrl = "http://127.0.0.1:8765/api/agent/demo-context"
        Write-Host "Using synthetic demo context." -ForegroundColor Yellow
    }

    Start-Process "http://127.0.0.1:8765/review" | Out-Null
    Write-Host "Review console: http://127.0.0.1:8765/review" -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop the local agent and services started by this script."

    & $Python "local_agent.py" --model $Model --context-url $contextUrl --interval $Interval
    if ($LASTEXITCODE -ne 0) { throw "The local agent exited with code $LASTEXITCODE." }
} finally {
    if ($startedGateway -and $null -ne $gatewayProcess -and -not $gatewayProcess.HasExited) {
        Stop-Process -Id $gatewayProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($startedInterface -and $null -ne $interfaceProcess -and -not $interfaceProcess.HasExited) {
        Stop-Process -Id $interfaceProcess.Id -Force -ErrorAction SilentlyContinue
    }
}
