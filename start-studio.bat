@echo off
setlocal
cd /d "%~dp0"

echo ==========================================================
echo   SliceThePrompt - 3D-Pruefstand (Creality Hi Combo)
echo ==========================================================

REM Starte ueber uv mit --no-sync (vermeidet Sperrfehler, wenn MCP im Hintergrund laeuft)
uv run --no-sync slicetheprompt ui %*

if errorlevel 1 (
    echo.
    echo Ein Fehler ist aufgetreten - Fehlercode: %ERRORLEVEL%
    pause
)
