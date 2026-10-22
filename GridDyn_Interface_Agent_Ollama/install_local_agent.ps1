[CmdletBinding()]
param(
    [string]$Model = "qwen3:8b",
    [switch]$Offline,
    [switch]$SkipOllama
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot ".venv"
$Requirements = Join-Path $ProjectRoot "requirements-local-agent.txt"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
    }
}

function Get-PythonLauncher {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        return @{ Path = $py.Source; Prefix = @("-3") }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        return @{ Path = $python.Source; Prefix = @() }
    }
    throw "Python 3 was not found. Install Python 3.10 or newer, then run this installer again."
}

function Resolve-Ollama {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $knownPaths = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    foreach ($path in $knownPaths) {
        if (Test-Path $path) {
            return $path
        }
    }
    return $null
}

function Wait-Ollama([string]$OllamaPath) {
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -Method Get -TimeoutSec 2 | Out-Null
            return
        } catch {
            if ($attempt -eq 1) {
                Write-Host "Starting the local Ollama service..."
                Start-Process -FilePath $OllamaPath -ArgumentList @("serve") -WindowStyle Hidden | Out-Null
            }
            Start-Sleep -Seconds 2
        }
    }
    throw "Ollama did not become available at http://127.0.0.1:11434."
}

Set-Location $ProjectRoot
$launcher = Get-PythonLauncher

Write-Step "Creating or reusing the local Python environment"
if (-not (Test-Path $VenvPython)) {
    & $launcher.Path @($launcher.Prefix) -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment." }
}

Write-Step "Installing the local agent dependencies"
if ($Offline) {
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--no-index", "-r", $Requirements)
} else {
    Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Checked $VenvPython @("-m", "pip", "install", "-r", $Requirements)
}

if (-not $SkipOllama) {
    Write-Step "Checking Ollama"
    $ollama = Resolve-Ollama
    if ($null -eq $ollama -and $Offline) {
        throw "Ollama is not installed. Re-run without -Offline while connected, or install Ollama and retry."
    }
    if ($null -eq $ollama) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($null -ne $winget) {
            Write-Host "Installing Ollama with winget..."
            Invoke-Checked $winget.Source @("install", "--id", "Ollama.Ollama", "--exact", "--accept-package-agreements", "--accept-source-agreements")
        } else {
            $installer = Join-Path $env:TEMP "OllamaSetup.exe"
            Write-Host "Downloading the official Ollama installer to $installer..."
            Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
            Write-Host "Complete the Ollama installer window, then return here."
            Start-Process -FilePath $installer -Wait
        }
        $ollama = Resolve-Ollama
    }
    if ($null -eq $ollama) {
        throw "Ollama was installed but its command was not found. Restart PowerShell and run this installer again."
    }

    Wait-Ollama $ollama
    if ($Offline) {
        $installedModels = & $ollama list
        if ($LASTEXITCODE -ne 0 -or -not ($installedModels | Select-String -SimpleMatch $Model)) {
            throw "The requested model '$Model' is not installed locally. Run without -Offline once to download it."
        }
    } else {
        Write-Step "Downloading the local model: $Model"
        Invoke-Checked $ollama @("pull", $Model)
    }
}

Write-Host "`nLocal agent installation is complete." -ForegroundColor Green
Write-Host "Model: $Model"
Write-Host "Start the complete local workflow with:"
Write-Host "  .\run_local_agent.ps1 -Model $Model -Demo"
Write-Host ""
Write-Host "For a real HELICS federation, use:"
Write-Host "  .\run_local_agent.ps1 -Model $Model -LiveHelics"
