<#
.SYNOPSIS
    Stops all RevCure services started by .\start.ps1.

.DESCRIPTION
    Kills the backend and frontend background processes, then stops
    the PostgreSQL Docker container.

.EXAMPLE
    .\stop.ps1
#>

$ErrorActionPreference = "SilentlyContinue"
$RootDir = $PSScriptRoot
$PidFile = Join-Path $RootDir ".revcure_pids.json"

Write-Host ""
Write-Host "  Stopping RevCure services..." -ForegroundColor Yellow
Write-Host ""

# ── Kill backend and frontend via saved PIDs ────────────────────────

if (Test-Path $PidFile) {
    $pids = Get-Content $PidFile | ConvertFrom-Json

    if ($pids.backend) {
        Write-Host "  Stopping backend (PID $($pids.backend))..." -ForegroundColor DarkGray
        taskkill /F /T /PID $pids.backend 2>&1 | Out-Null
    }
    if ($pids.frontend) {
        Write-Host "  Stopping frontend (PID $($pids.frontend))..." -ForegroundColor DarkGray
        taskkill /F /T /PID $pids.frontend 2>&1 | Out-Null
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "  No saved PIDs found. Trying port-based cleanup..." -ForegroundColor DarkGray

    # Fallback: kill anything on port 8000 and 3000
    foreach ($port in @(8000, 3000)) {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess -and $c.OwningProcess -ne 0) {
                Write-Host "    Killing PID $($c.OwningProcess) on port $port..." -ForegroundColor DarkGray
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

# ── Stop Docker (PostgreSQL) ────────────────────────────────────────

Write-Host "  Stopping PostgreSQL (Docker)..." -ForegroundColor DarkGray
Push-Location $RootDir
docker compose down 2>&1 | Out-Null
Pop-Location

Write-Host ""
Write-Host "  All services stopped." -ForegroundColor Green
Write-Host ""
