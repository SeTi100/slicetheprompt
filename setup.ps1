# slicetheprompt - Einrichtung unter Windows 11 (einmal nach "git clone")
# Ausfuehren in PowerShell im Projektordner:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Optionen:
#   -Exe "C:\Pfad\zu\CrealityPrint.exe"   falls der Slicer nicht gefunden wird
#   -Desktop                               slicetheprompt zusaetzlich in die Claude-Desktop-App eintragen
#                                          (App vorher ganz beenden)

param([string]$Exe = "", [switch]$Desktop)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($t) { Write-Host ""; Write-Host "### $t" -ForegroundColor Cyan }

Step "1/5 uv (Python-Paketverwaltung)"
# Die Claude-App startet slicetheprompt ueber "uv" aus dem dauerhaften PATH. Ein uv, das nur in einer
# aktivierten conda-/venv-Umgebung liegt, findet sie nicht -> eigenes uv installieren.
$persistPath = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
$uvPersistent = $persistPath -split ";" | Where-Object { $_ -and (Test-Path (Join-Path $_ "uv.exe")) } | Select-Object -First 1
if (-not $uvPersistent) {
    Write-Host "uv wird installiert (nach $env:USERPROFILE\.local\bin, dauerhaft im PATH) ..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

if ($Exe -eq "") {
    $cp = @("$env:ProgramFiles\Creality\*\CrealityPrint.exe", "$env:ProgramFiles\Creality\*\*\CrealityPrint.exe",
            "$env:LOCALAPPDATA\Programs\Creality\*\CrealityPrint.exe") | ForEach-Object { Resolve-Path $_ -ErrorAction SilentlyContinue }
    if (-not $cp) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "Creality Print wird installiert (winget, Creality.CrealityPrint) ..."
            winget install -e --id Creality.CrealityPrint --accept-package-agreements --accept-source-agreements
        } else {
            Write-Host "Creality Print fehlt und winget ist nicht vorhanden: von creality.com installieren." -ForegroundColor Yellow
        }
    }
}

Step "2/5 Python 3.12 und Pakete (build123d, numpy, matplotlib, mcp ...) - dauert beim ersten Mal einige Minuten"
uv sync --python 3.12
if ($LASTEXITCODE -ne 0) { throw "uv sync fehlgeschlagen" }

Step "3/5 Drucker, Slicer, Profile"
if ($Exe -ne "") { uv run slicetheprompt setup --exe "$Exe" } else { uv run slicetheprompt setup }
$setupOk = ($LASTEXITCODE -eq 0)

Step "4/5 Selbsttests (Netzpruefung, CFS, 3MF)"
uv run python tests/test_meshcheck.py
uv run python tests/test_cfs_3mf.py

Step "5/5 CAD-Tests: Beispiel-Box mit M3-Loechern, zweifarbiges Schild"
uv run slicetheprompt run beispiel-box-m3
uv run slicetheprompt run beispiel-schild-zweifarbig

if ($Desktop) {
    Step "Claude-Desktop-App"
    uv run slicetheprompt install claude-desktop
}

Write-Host ""
if ($setupOk) {
    Write-Host "Einrichtung abgeschlossen." -ForegroundColor Green
} else {
    Write-Host "Einrichtung mit Warnungen - Ausgabe oben pruefen (Slicer oder Drucker)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Claude Code:    in diesem Ordner 'claude' starten (slicetheprompt wird ueber .mcp.json geladen)" -ForegroundColor White
Write-Host "Claude Desktop: App beenden, dann 'uv run slicetheprompt install claude-desktop', App starten" -ForegroundColor White
Write-Host "Beispiel:       /teil Wandhalter fuer Ladegeraet 68 x 32 x 15 mm, 2 Schraubloecher 4 mm, PETG"
