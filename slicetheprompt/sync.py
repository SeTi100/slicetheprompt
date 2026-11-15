"""Google-Drive-Synchronisation über rclone (non-blocking Build-Hook + CLI)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR, PROJECTS_DIR, STATE_DIR, _read_yaml, project_dir

SYNC_YAML = CONFIG_DIR / "sync.yaml"
SYNC_LOG = STATE_DIR / "sync.log"

DEFAULT_SYNC_CFG: dict[str, Any] = {
    "enabled": "auto",       # "auto" (aktiv wenn rclone + Remote vorhanden), true, false
    "remote": "gdrive",      # rclone-Remote-Name (ohne Doppelpunkt)
    "remote_path": "SliceThePrompt/projects",
    "exclude": [],           # z. B. ["*.gcode", "history/**"]
    "conflict_resolve": "newer",  # bei bisync: neuere Datei gewinnt (Verlierer als .conflict)
}

_REMOTE_CACHE: dict[tuple[str, str], bool] = {}


def load_sync_config() -> dict[str, Any]:
    """Lädt optionale config/sync.yaml über den Standardwerten."""
    cfg = dict(DEFAULT_SYNC_CFG)
    user = _read_yaml(SYNC_YAML)
    if isinstance(user, dict):
        for k, v in user.items():
            if v is not None:
                cfg[k] = v
    cfg["remote"] = str(cfg.get("remote") or "gdrive").rstrip(":")
    cfg["remote_path"] = str(cfg.get("remote_path") or "").strip("/")
    return cfg


def _is_disabled(val: Any) -> bool:
    if val is False:
        return True
    if isinstance(val, str) and val.strip().lower() in ("false", "0", "aus", "off", "no", "nein"):
        return True
    return False


def _remote_exists(rclone_bin: str, remote: str, use_cache: bool = True) -> bool:
    key = (rclone_bin, remote)
    if use_cache and key in _REMOTE_CACHE:
        return _REMOTE_CACHE[key]
    try:
        proc = subprocess.run(
            [rclone_bin, "listremotes"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        ok = proc.returncode == 0 and any(
            line.strip().rstrip(":") == remote for line in proc.stdout.splitlines()
        )
    except Exception:
        ok = False
    _REMOTE_CACHE[key] = ok
    return ok


def _target_for(cfg: dict[str, Any], project_name: str | None = None) -> str:
    base = cfg["remote_path"]
    if project_name:
        sub = f"{base}/{project_name}" if base else project_name
    else:
        sub = base
    return f"{cfg['remote']}:{sub}"


def sync_status() -> dict[str, Any]:
    """Prüft, ob rclone installiert und der konfigurierte Remote eingerichtet ist."""
    cfg = load_sync_config()
    rclone_bin = shutil.which("rclone")
    remote_ok = _remote_exists(rclone_bin, cfg["remote"], use_cache=False) if rclone_bin else False
    disabled = _is_disabled(cfg["enabled"])
    return {
        "enabled": cfg["enabled"],
        "rclone_installed": bool(rclone_bin),
        "rclone_bin": rclone_bin,
        "remote": cfg["remote"],
        "remote_configured": remote_ok,
        "remote_path": cfg["remote_path"],
        "active": bool(rclone_bin and remote_ok and not disabled),
        "config_file": str(SYNC_YAML) if SYNC_YAML.exists() else None,
        "log_file": str(SYNC_LOG),
    }


def _copy_cmd(rclone_bin: str, src: Path, target: str, cfg: dict[str, Any], dry_run: bool = False) -> list[str]:
    cmd = [rclone_bin, "copy", str(src), target]
    for pat in cfg.get("exclude") or []:
        cmd += ["--exclude", str(pat)]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def sync_project_async(project: str | Path) -> dict[str, Any]:
    """Startet `rclone copy` für ein Projekt im Hintergrund (non-blocking).

    Bricht bei fehlendem rclone/Remote oder deaktivierter Konfiguration lautlos ab,
    damit der CAD-Build niemals blockiert oder fehlschlägt.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("SLICETHEPROMPT_NO_SYNC") == "1":
        return {"triggered": False, "reason": "disabled_by_env"}

    cfg = load_sync_config()
    if _is_disabled(cfg["enabled"]):
        return {"triggered": False, "reason": "disabled_in_config"}

    rclone_bin = shutil.which("rclone")
    if not rclone_bin:
        return {"triggered": False, "reason": "rclone_not_installed"}

    if not _remote_exists(rclone_bin, cfg["remote"]):
        return {"triggered": False, "reason": f"remote '{cfg['remote']}:' not configured"}

    try:
        p = Path(project)
        d = p if p.is_dir() else project_dir(str(project))
        target = _target_for(cfg, d.name)
        cmd = _copy_cmd(rclone_bin, d, target, cfg)

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        log_fh = open(SYNC_LOG, "a", encoding="utf-8")
        try:
            log_fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {' '.join(cmd)}\n")
            log_fh.flush()
            popen_kw: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": log_fh,
                "stderr": subprocess.STDOUT,
            }
            if os.name == "nt":
                popen_kw["creationflags"] = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
            else:
                popen_kw["start_new_session"] = True
            proc = subprocess.Popen(cmd, **popen_kw)
        finally:
            log_fh.close()
        return {"triggered": True, "pid": proc.pid, "target": target}
    except Exception as exc:  # noqa: BLE001 – Build darf nie am Sync scheitern
        return {"triggered": False, "reason": f"error: {exc}"}


def _require_ready() -> tuple[str, dict[str, Any]]:
    cfg = load_sync_config()
    rclone_bin = shutil.which("rclone")
    if not rclone_bin:
        raise RuntimeError("rclone ist nicht installiert. Siehe docs/google-drive-sync.md")
    if not _remote_exists(rclone_bin, cfg["remote"], use_cache=False):
        raise RuntimeError(
            f"rclone-Remote '{cfg['remote']}:' ist nicht eingerichtet. "
            "Siehe docs/google-drive-sync.md"
        )
    return rclone_bin, cfg


def sync_project(project: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Synchronisiert ein Projekt (oder alle unter projects/) per `rclone copy` (blockierend)."""
    rclone_bin, cfg = _require_ready()
    if project:
        d = project_dir(project)
        src, target = d, _target_for(cfg, d.name)
    else:
        src, target = PROJECTS_DIR, _target_for(cfg)
    cmd = _copy_cmd(rclone_bin, src, target, cfg, dry_run=dry_run)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "ok": proc.returncode == 0,
        "mode": "copy",
        "source": str(src),
        "target": target,
        "dry_run": dry_run,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:] if proc.stdout else "",
        "stderr": proc.stderr[-2000:] if proc.stderr else "",
    }


def bisync(project: str | None = None, resync: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """2-Wege-Sync per `rclone bisync` (für Handy ↔ PC)."""
    rclone_bin, cfg = _require_ready()
    if project:
        d = project_dir(project)
        src, target = d, _target_for(cfg, d.name)
    else:
        src, target = PROJECTS_DIR, _target_for(cfg)
    cmd = [rclone_bin, "bisync", str(src), target, "--create-empty-src-dirs"]
    if cfg.get("conflict_resolve"):
        cmd += ["--conflict-resolve", str(cfg["conflict_resolve"])]
    for pat in cfg.get("exclude") or []:
        cmd += ["--exclude", str(pat)]
    if resync:
        cmd.append("--resync")
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return {
        "ok": proc.returncode == 0,
        "mode": "bisync",
        "source": str(src),
        "target": target,
        "resync": resync,
        "dry_run": dry_run,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:] if proc.stdout else "",
        "stderr": proc.stderr[-2000:] if proc.stderr else "",
    }


def watch_bisync(project: str | None = None, interval_s: int = 120,
                 auto_resync: bool = True, max_runs: int | None = None) -> None:
    """Führt periodisch einen 2-Wege-Sync (`rclone bisync`) aus (z. B. aus start_all.sh)."""
    interval_s = max(15, int(interval_s))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    runs = 0
    while max_runs is None or runs < max_runs:
        runs += 1
        res = bisync(project, resync=False)
        if not res["ok"] and auto_resync and "prior" in (res.get("stderr") or "").lower():
            res = bisync(project, resync=True)
        with open(SYNC_LOG, "a", encoding="utf-8") as fh:
            status = "OK" if res["ok"] else f"ERR({res['returncode']})"
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] bisync {status} -> {res['target']}\n")
            if not res["ok"] and res.get("stderr"):
                fh.write(res["stderr"].rstrip() + "\n")
        if max_runs is not None and runs >= max_runs:
            break
        time.sleep(interval_s)
