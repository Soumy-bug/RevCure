<#
.SYNOPSIS
    Starts all RevCure services with a single command.

.DESCRIPTION
    Launches PostgreSQL (Docker), FastAPI backend, and React frontend as
    background processes, verifies each is accepting connections, opens
    the dashboard, and returns control to your terminal.

    Run .\stop.ps1 to shut everything down cleanly.

.PARAMETER Seed
    Seed demo data after migrations.

.PARAMETER NoBrowser
    Skip opening the browser automatically.

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Seed
#>

param(
    [switch]$Seed,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RootDir = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$VenvDir = Join-Path $RootDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$PidFile = Join-Path $RootDir ".revcure_pids.json"

# ── Helpers ──────────────────────────────────────────────────────────

function Write-Banner {
    Write-Host ""
    Write-Host "  =============================" -ForegroundColor Cyan
    Write-Host "   RevCure - Local Dev Server" -ForegroundColor Cyan
    Write-Host "  =============================" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step($msg) { Write-Host "`n>>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    [ERROR] $msg" -ForegroundColor Red }

function Test-PortOpen($port) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", $port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

function Wait-ForPort($port, $timeoutSec, $label) {
    $waited = 0
    while ($waited -lt $timeoutSec) {
        if (Test-PortOpen $port) { return $true }
        Start-Sleep -Seconds 1
        $waited++
        if ($waited -eq 10 -or $waited -eq 25) {
            Write-Host "    Waiting for $label... (${waited}s / ${timeoutSec}s)" -ForegroundColor DarkGray
        }
    }
    return $false
}

function Save-Pids($backendId, $frontendId) {
    @{ backend = $backendId; frontend = $frontendId; started = (Get-Date -Format o) } |
        ConvertTo-Json | Set-Content $PidFile -Encoding UTF8
}

# ── Main ─────────────────────────────────────────────────────────────

try {
    Write-Banner

    # ── 1. Prerequisites ────────────────────────────────────────────
    Write-Step "Checking prerequisites..."

    if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Err "Docker is not installed."
        Write-Host "    Install: https://docs.docker.com/desktop/install/windows-install/" -ForegroundColor White
        exit 1
    }
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Docker Desktop is not running. Start it and try again."
        exit 1
    }
    Write-Ok "Docker is running"

    docker compose version 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Docker Compose is not available."
        exit 1
    }
    Write-Ok "Docker Compose is available"

    if (!(Test-Path $VenvPython)) {
        Write-Warn "Python virtual environment not found. Creating .venv..."
        python -m venv $VenvDir
        if (!(Test-Path $VenvPython)) {
            Write-Err "Failed to create Python virtual environment."
            exit 1
        }
    }
    $pyVer = & $VenvPython --version 2>&1
    Write-Ok "Python: $pyVer"

    if (!(Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Err "Node.js is not installed. Install from https://nodejs.org/"
        exit 1
    }
    Write-Ok "Node.js: $(node --version)"

    # ── 2. Docker: PostgreSQL ───────────────────────────────────────
    Write-Step "Starting PostgreSQL via Docker Compose..."

    Push-Location $RootDir
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose up -d 2>&1 | Out-Null
    $composeExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    Pop-Location
    if ($composeExit -ne 0) {
        Write-Err "Docker Compose failed to start."
        exit 1
    }

    Write-Step "Waiting for PostgreSQL to accept connections..."
    $pgReady = $false
    for ($i = 0; $i -lt 30; $i++) {
        docker exec revcure_postgres pg_isready -U revcure_user -d revcure_db 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pgReady = $true; break }
        Start-Sleep -Seconds 1
    }
    if (!$pgReady) {
        Write-Err "PostgreSQL did not become ready within 30 seconds."
        Write-Host "    Run: docker compose logs postgres" -ForegroundColor White
        exit 1
    }
    Write-Ok "PostgreSQL is ready (localhost:5433)"

    # ── 3. Backend dependencies ─────────────────────────────────────
    Write-Step "Checking backend Python packages..."

    $needInstall = $false
    & $VenvPython -c "import fastapi" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { $needInstall = $true }

    if ($needInstall) {
        Write-Warn "Installing backend dependencies (first time only)..."
        & $VenvPip install -q -r (Join-Path $BackendDir "requirements.txt") 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Failed to install backend dependencies."
            exit 1
        }
        Write-Ok "Backend dependencies installed"
    } else {
        Write-Ok "Backend dependencies already installed"
    }

    # ── 4. Database migrations ──────────────────────────────────────
    Write-Step "Running Alembic migrations..."

    Push-Location $BackendDir
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $migOutput = & $VenvPython -m alembic upgrade head 2>&1
    $migExit = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    Pop-Location
    if ($migExit -ne 0) {
        Write-Err "Migration failed:"
        $migOutput | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        exit 1
    }
    Write-Ok "Database schema up to date"

    # ── 5. Seed demo data (optional) ────────────────────────────────
    if ($Seed) {
        Write-Step "Seeding demo data..."
        Push-Location $BackendDir
        & $VenvPython -m scripts.seed_demo seed 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        Pop-Location
        Write-Ok "Demo data seeded"
    }

    # ── 6. Start backend ────────────────────────────────────────────
    $backendPid = $null
    $backendStartedNew = $false

    if (Test-PortOpen 8000) {
        Write-Step "Backend already running on port 8000."
        Write-Ok "Backend: http://localhost:8000"
    } else {
        Write-Step "Starting FastAPI backend on port 8000..."

        $backendLog = Join-Path $RootDir "backend.log"
        $backendErr = Join-Path $RootDir "backend_err.log"

        $proc = Start-Process -FilePath $VenvPython `
            -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload" `
            -WorkingDirectory $BackendDir `
            -PassThru -NoNewWindow `
            -RedirectStandardOutput $backendLog `
            -RedirectStandardError $backendErr

        $backendPid = $proc.Id
        $backendStartedNew = $true

        if (!(Wait-ForPort 8000 20 "backend")) {
            Write-Err "Backend did not start within 20 seconds."
            Write-Host "    Logs: $backendLog" -ForegroundColor White
            Write-Host "    Errors: $backendErr" -ForegroundColor White
            exit 1
        }
        Write-Ok "Backend running at http://localhost:8000  (PID $backendPid)"
    }

    # ── 7. Start frontend ───────────────────────────────────────────
    $frontendPid = $null
    $frontendStartedNew = $false

    if (Test-PortOpen 3000) {
        Write-Step "Frontend already running on port 3000."
        Write-Ok "Frontend: http://localhost:3000"
    } else {
        Write-Step "Starting React frontend on port 3000..."

        $frontendLog = Join-Path $RootDir "frontend.log"
        $frontendErr = Join-Path $RootDir "frontend_err.log"

        $env:BROWSER = "none"

        $proc = Start-Process -FilePath "cmd.exe" `
            -ArgumentList "/c", "npm", "start" `
            -WorkingDirectory $FrontendDir `
            -PassThru -NoNewWindow `
            -RedirectStandardOutput $frontendLog `
            -RedirectStandardError $frontendErr

        $frontendPid = $proc.Id
        $frontendStartedNew = $true

        if (!(Wait-ForPort 3000 60 "frontend")) {
            Write-Err "Frontend did not start within 60 seconds."
            Write-Host "    Logs: $frontendLog" -ForegroundColor White
            Write-Host "    Errors: $frontendErr" -ForegroundColor White
            exit 1
        }
        Write-Ok "Frontend running at http://localhost:3000  (PID $frontendPid)"
    }

    # ── 8. Save PIDs for stop.ps1 ──────────────────────────────────
    if ($backendStartedNew -or $frontendStartedNew) {
        Save-Pids $backendPid $frontendPid
    }

    # ── 9. Open browser ─────────────────────────────────────────────
    if (!$NoBrowser) {
        Write-Step "Opening dashboard in browser..."
        Start-Process "http://localhost:3000"
        Write-Ok "Browser opened"
    }

    # ── 10. Summary ─────────────────────────────────────────────────
    Write-Host ""
    Write-Host "  =======================================" -ForegroundColor Green
    Write-Host "   RevCure is up and running!" -ForegroundColor Green
    Write-Host "  =======================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "   Dashboard ........ http://localhost:3000" -ForegroundColor White
    Write-Host "   API .............. http://localhost:8000" -ForegroundColor White
    Write-Host "   API Docs ........ http://localhost:8000/docs" -ForegroundColor White
    Write-Host "   PostgreSQL ....... localhost:5433" -ForegroundColor White
    Write-Host ""
    Write-Host "   To stop:  .\stop.ps1" -ForegroundColor Yellow
    Write-Host "   To start: .\start.ps1" -ForegroundColor Yellow
    Write-Host ""

} catch {
    Write-Err "Unexpected error: $_"
    exit 1
}
