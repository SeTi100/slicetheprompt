# slicetheprompt - Einrichtung unter Windows 11 (einmal nach "git clone")
# Ausfuehren in PowerShell im Projektordner:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Optionen:
#   -Exe "C:\Pfad\zu\CrealityPrint.exe"   falls der Slicer nicht gefunden wird
#   -Desktop                               printlab zusaetzlich in die Claude-Desktop-App eintragen
#                                          (App vorher ganz beenden)

param([string]$Exe = "", [switch]$Desktop)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($t) { Write-Host ""; Write-Host "### $t" -ForegroundColor Cyan }

Step "1/5 uv (Python-Paketverwaltung)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv wird installiert ..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

Step "2/5 Python 3.12 und Pakete (build123d, numpy, matplotlib, mcp ...) - dauert beim ersten Mal einige Minuten"
uv sync --python 3.12
if ($LASTEXITCODE -ne 0) { throw "uv sync fehlgeschlagen" }

Step "3/5 Drucker, Slicer, Profile"
if ($Exe -ne "") { uv run printlab setup --exe "$Exe" } else { uv run printlab setup }
$setupOk = ($LASTEXITCODE -eq 0)

Step "4/5 Selbsttests (Netzpruefung, CFS, 3MF)"
uv run python tests/test_meshcheck.py
uv run python tests/test_cfs_3mf.py

Step "5/5 CAD-Tests: Beispiel-Box mit M3-Loechern, zweifarbiges Schild"
uv run printlab run beispiel-box-m3
uv run printlab run beispiel-schild-zweifarbig

if ($Desktop) {
    Step "Claude-Desktop-App"
    uv run printlab install claude-desktop
}

Write-Host ""
if ($setupOk) {
    Write-Host "Einrichtung abgeschlossen." -ForegroundColor Green
} else {
    Write-Host "Einrichtung mit Warnungen - Ausgabe oben pruefen (Slicer oder Drucker)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Claude Code:    in diesem Ordner 'claude' starten (printlab wird ueber .mcp.json geladen)" -ForegroundColor White
Write-Host "Claude Desktop: App beenden, dann 'uv run printlab install claude-desktop', App starten" -ForegroundColor White
Write-Host "Beispiel:       /teil Wandhalter fuer Ladegeraet 68 x 32 x 15 mm, 2 Schraubloecher 4 mm, PETG"
