<#
.SYNOPSIS
    Startet SliceThePrompt (3D-Inspektions- & Markierungs-GUI) für slicetheprompt.
.DESCRIPTION
    Prüft die Python-Umgebung, startet den lokalen Webserver auf Port 8765
    und öffnet SliceThePrompt automatisch im Standard-Webbrowser.
#>

[CmdletBinding()]
param(
    [string]$Project = "",
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  SliceThePrompt - 3D-Prüfstand (Creality Hi Combo)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Prüfe uv / .venv
if (-not (Test-Path "$ScriptDir\.venv")) {
    Write-Host "[*] Virtuelle Umgebung (.venv) wird vorbereitet..." -ForegroundColor Yellow
    uv sync
}

# 2. Argumente zusammenbauen
$ArgsList = @("run", "slicetheprompt", "ui", "--port", "$Port")
if ($Project) {
    $ArgsList += @("--project", $Project)
}
if ($NoBrowser) {
    $ArgsList += "--no-browser"
}

Write-Host "[+] Starte Studio-Server auf http://127.0.0.1:$Port ..." -ForegroundColor Green
Write-Host "[i] Beenden jederzeit mit Ctrl+C im Terminal." -ForegroundColor Gray
Write-Host ""

# 3. Server starten
& uv @ArgsList
