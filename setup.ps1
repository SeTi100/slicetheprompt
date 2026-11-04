# slicetheprompt - Einrichtung unter Windows 11
# Ausfuehren in PowerShell im Projektordner:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Optional: -Exe "C:\Pfad\zu\CrealityPrint.exe"   (falls der Slicer nicht gefunden wird)

param([string]$Exe = "")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($t) { Write-Host ""; Write-Host "### $t" -ForegroundColor Cyan }

Step "0/5 Claude-Code-Projektdateien (.claude: Befehle /teil /drucken /status /kalibrieren, Freigaberegeln)"
New-Item -ItemType Directory -Force -Path ".claude\commands" | Out-Null
Copy-Item -Force "claude-setup\settings.json" ".claude\settings.json"
Copy-Item -Force "claude-setup\commands\*.md" ".claude\commands\"
Get-ChildItem ".claude\commands" | ForEach-Object { Write-Host "   /$($_.BaseName)" }

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

Step "3/5 Drucker, Slicer, Profile, MCP-Konfiguration"
if ($Exe -ne "") { uv run printlab setup --exe "$Exe" } else { uv run printlab setup }
$setupOk = ($LASTEXITCODE -eq 0)

Step "4/5 Selbsttests (Netzpruefung, CFS, 3MF)"
uv run python tests/test_meshcheck.py
uv run python tests/test_cfs_3mf.py

Step "5/5 CAD-Tests: Beispiel-Box mit M3-Loechern, zweifarbiges Schild"
uv run printlab run beispiel-box-m3
uv run printlab run beispiel-schild-zweifarbig
uv run printlab plan beispiel-schild-zweifarbig

Write-Host ""
if ($setupOk) {
    Write-Host "Einrichtung abgeschlossen." -ForegroundColor Green
} else {
    Write-Host "Einrichtung mit Warnungen - Ausgabe oben pruefen (Slicer oder Drucker)." -ForegroundColor Yellow
}
Write-Host "Renders: projects\2026-11-04-beispiel-box-m3\out\render.png, projects\2026-11-04-beispiel-schild-zweifarbig\out\render.png"
Write-Host ""
Write-Host "Naechster Schritt: in diesem Ordner Claude Code starten:"
Write-Host "   claude" -ForegroundColor White
Write-Host "und z. B. eingeben:  /teil Wandhalter fuer Ladegeraet 68 x 32 x 15 mm, 2 Schraubloecher 4 mm, PETG"
