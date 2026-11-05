@echo off
setlocal
cd /d "%~dp0"

echo ==========================================================
echo   SliceThePrompt - 3D-Pruefstand (Creality Hi Combo)
echo ==========================================================

REM Starte ueber uv
uv run slicetheprompt ui %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Ein Fehler ist aufgetreten (Fehlercode: %ERRORLEVEL%).
    pause
)
