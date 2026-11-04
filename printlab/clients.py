"""printlab in KI-Clients eintragen:  uv run printlab install claude-desktop | antigravity

Claude Code braucht nichts: die versionierte .mcp.json im Projekt startet printlab über uv.
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
        "args": ["-m", "printlab.mcp_server"],
        "env": {"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    }


def _claude_desktop_configs() -> list[Path]:
    paths = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:  # Installation aus dem Microsoft Store
        paths += list(Path(local, "Packages").glob("*Claude*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
    home = Path.home()
    paths += [home / "Library/Application Support/Claude/claude_desktop_config.json",   # macOS
              home / ".config/Claude/claude_desktop_config.json"]                        # Linux
    existing = [p for p in paths if p.exists()]
    return existing or paths[:1]


def _claude_desktop_running() -> bool:
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Claude.exe", "/NH"], capture_output=True,
                                 text=True, timeout=10).stdout
            return "Claude.exe" in out
        out = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True, text=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:
        return False


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
    before = (data.get("mcpServers") or {}).get(name)
    data.setdefault("mcpServers", {})[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"file": str(path), "backup": str(backup) if backup else None,
            "action": "aktualisiert" if before else "hinzugefügt"}


def install(client: str, force: bool = False) -> dict:
    entry = server_entry()
    if client == "claude-desktop":
        if _claude_desktop_running() and not force:
            raise RuntimeError(
                "Die Claude-Desktop-App läuft. Sie überschreibt ihre Konfiguration beim Beenden – "
                "bitte die App ganz beenden (Infobereich → Beenden) und den Befehl wiederholen.")
        res = [_merge(p, "printlab", entry) for p in _claude_desktop_configs()]
        return {"client": client, "results": res, "next": "Claude-Desktop-App starten"}
    if client == "antigravity":
        p = Path.home() / ".gemini" / "config" / "mcp_config.json"
        return {"client": client, "results": [_merge(p, "printlab", entry)]}
    if client == "claude-code":
        return {"client": client, "results": [],
                "next": "Nichts zu tun: die .mcp.json im Projekt wird von Claude Code automatisch geladen "
                        "(beim ersten Start 'printlab' zulassen)."}
    raise ValueError("client muss claude-desktop, claude-code oder antigravity sein")
