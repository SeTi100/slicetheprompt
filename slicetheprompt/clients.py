"""slicetheprompt in KI-Clients eintragen:  uv run slicetheprompt install claude-desktop | antigravity

Claude Code braucht nichts: die versionierte .mcp.json im Projekt startet slicetheprompt über uv.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ROOT


def server_entry() -> dict:
    """Eintrag mit absolutem Pfad zur Projekt-Python (für Clients ohne Projektbezug)."""
    return {
        "command": sys.executable,
        "args": ["-m", "slicetheprompt.mcp_server"],
        "env": {"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    }


def _claude_desktop_configs() -> list[Path]:
    """Alle Orte, an denen die Claude-Desktop-App ihre Konfiguration lesen kann.
    Die App zeigt den Ort unter Einstellungen → Entwickler → Konfiguration bearbeiten.
    Store-Installation (MSIX): Windows leitet %APPDATA% je nach Version in
    %LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming um – darum beide Orte."""
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # Store-Version (neuere Fassungen): %LOCALAPPDATA%\\Claude-Data\\claude_<id>\\
        paths += sorted(Path(local, "Claude-Data").glob("*/claude_desktop_config.json"))
        for pkg in Path(local, "Packages").glob("Claude_*"):
            paths.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    if not sys.platform.startswith("win"):
        home = Path.home()
        paths += [home / "Library/Application Support/Claude/claude_desktop_config.json",   # macOS
                  home / ".config/Claude/claude_desktop_config.json"]                        # Linux
    # Nur bestehende Dateien ergänzen – eine neu angelegte Datei kann die echte Konfiguration verdecken.
    existing = [p for p in paths if p.exists() and p.stat().st_size > 0]
    return existing or paths[:1]


def _claude_desktop_running() -> bool | None:
    """True/False; None, wenn es sich nicht feststellen lässt."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Claude.exe", "/NH"], capture_output=True,
                                 timeout=10).stdout  # Bytes: Konsolen-Codepage ist nicht UTF-8
            return b"claude.exe" in out.lower()
        out = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:
        return None


def _merge(path: Path, name: str, entry: dict) -> dict:
    data: dict = {}
    if path.exists() and path.stat().st_size > 0:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{path} ist kein gültiges JSON ({exc}) – bitte zuerst reparieren, nichts geändert")
        backup = path.with_name(f"{path.name}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
    else:
        backup = None
    servers = data.setdefault("mcpServers", {})
    before = servers.get(name) or servers.pop("printlab", None)  # alter Name vor der Umbenennung
    servers[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"file": str(path), "backup": str(backup) if backup else None,
            "action": "aktualisiert" if before else "hinzugefügt"}


def install(client: str, force: bool = False, config: str | None = None) -> dict:
    entry = server_entry()
    if client == "claude-desktop":
        running = _claude_desktop_running()
        if running is None and not force:
            raise RuntimeError("Konnte nicht prüfen, ob die Claude-App läuft. App ganz beenden und mit "
                               "'--force' wiederholen.")
        if running and not force:
            raise RuntimeError(
                "Die Claude-Desktop-App läuft. Sie überschreibt ihre Konfiguration beim Beenden – "
                "bitte die App ganz beenden (Infobereich → Beenden) und den Befehl wiederholen.")
        targets = [Path(config)] if config else _claude_desktop_configs()
        res = [_merge(p, "slicetheprompt", entry) for p in targets]
        return {"client": client, "results": res, "next": "Claude-Desktop-App starten"}
    if client == "antigravity":
        p = Path.home() / ".gemini" / "config" / "mcp_config.json"
        return {"client": client, "results": [_merge(p, "slicetheprompt", entry)]}
    if client == "claude-code":
        return {"client": client, "results": [],
                "next": "Nichts zu tun: die .mcp.json im Projekt wird von Claude Code automatisch geladen "
                        "(beim ersten Start 'slicetheprompt' zulassen)."}
    raise ValueError("client muss claude-desktop, claude-code oder antigravity sein")
