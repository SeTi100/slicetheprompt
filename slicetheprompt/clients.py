"""slicetheprompt in KI-Clients eintragen:  uv run slicetheprompt install claude-desktop | codex | antigravity

Claude Code braucht nichts: die versionierte .mcp.json im Projekt startet slicetheprompt über uv.
Codex und Antigravity bekommen einen Eintrag mit absolutem Pfad zur Projekt-Python.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from .config import ROOT


def server_entry() -> dict:
    """Eintrag mit absolutem Pfad zur Projekt-Python (für Clients ohne Projektbezug)."""
    return {
        "command": sys.executable,
        "args": ["-m", "slicetheprompt.mcp_server"],
        "env": {"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    }


# Werkzeuge, die vor dem Aufruf immer nachfragen sollen (Druck starten/abbrechen, Kalibrierung schreiben)
ASK_TOOLS = ("upload_and_print", "print_control", "apply_calibration")
SLOW_TOOL_SEC = 900  # Slicen und erster Start (uv, CAD) dauern länger als die Standard-Timeouts


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _codex_block(name: str, entry: dict) -> str:
    lines = [f"[mcp_servers.{name}]",
             f"command = {_toml_str(entry['command'])}",
             "args = [" + ", ".join(_toml_str(a) for a in entry["args"]) + "]",
             "startup_timeout_sec = 120",
             f"tool_timeout_sec = {SLOW_TOOL_SEC}",
             # Windows: Codex reicht nur wenige Variablen durch; Slicer-Suche und uv brauchen diese
             'env_vars = ["APPDATA", "LOCALAPPDATA", "USERPROFILE", "PROGRAMFILES", "TEMP"]',
             "",
             f"[mcp_servers.{name}.env]"]
    lines += [f"{k} = {_toml_str(v)}" for k, v in entry["env"].items()]
    for tool in ASK_TOOLS:
        lines += ["", f"[mcp_servers.{name}.tools.{tool}]", 'approval_mode = "prompt"']
    return "\n".join(lines) + "\n"


def _merge_codex(path: Path, name: str, entry: dict) -> dict:
    """Ersetzt alle [mcp_servers.<name>…]-Tabellen (auch den alten Namen printlab) in config.toml."""
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old:
        try:
            tomllib.loads(old)
        except tomllib.TOMLDecodeError as exc:
            raise RuntimeError(f"{path} ist kein gültiges TOML ({exc}) – bitte zuerst reparieren, nichts geändert")
    kept, skip, before = [], False, False
    for line in old.splitlines():
        s = line.strip()
        if s.startswith("["):
            table = s.strip("[]").split("]")[0].strip().split(".")
            skip = len(table) >= 2 and table[0] == "mcp_servers" and table[1].strip('"') in (name, "printlab")
            before = before or skip
        if not skip:
            kept.append(line)
    text = "\n".join(kept).rstrip()
    text = (text + "\n\n" if text else "") + _codex_block(name, entry)
    tomllib.loads(text)  # Selbstprüfung vor dem Schreiben
    backup = None
    if old:
        backup = path.with_name(f"{path.name}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"file": str(path), "backup": str(backup) if backup else None,
            "action": "aktualisiert" if before else "hinzugefügt"}


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
    if client == "codex":
        p = Path(config) if config else Path.home() / ".codex" / "config.toml"
        return {"client": client, "results": [_merge_codex(p, "slicetheprompt", entry)],
                "next": "Codex (CLI, App oder IDE) neu starten; Prüfen mit 'codex mcp list' oder /mcp"}
    if client == "antigravity":
        p = Path(config) if config else Path.home() / ".gemini" / "config" / "mcp_config.json"
        entry = {**entry, "timeoutSeconds": SLOW_TOOL_SEC}
        return {"client": client, "results": [_merge(p, "slicetheprompt", entry)],
                "next": "Antigravity neu starten"}
    if client == "claude-code":
        return {"client": client, "results": [],
                "next": "Nichts zu tun: die .mcp.json im Projekt wird von Claude Code automatisch geladen "
                        "(beim ersten Start 'slicetheprompt' zulassen)."}
    raise ValueError("client muss claude-desktop, claude-code, codex oder antigravity sein")
