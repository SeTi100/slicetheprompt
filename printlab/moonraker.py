"""Verbindung zum Creality Hi über die Moonraker-API im lokalen Netz."""

from __future__ import annotations

from pathlib import Path

import requests

from .config import load_config


class PrinterError(RuntimeError):
    pass


class Moonraker:
    def __init__(self, host: str | None = None, port: int | None = None, api_key: str | None = None,
                 timeout: float = 8.0):
        p = load_config()["printer"]
        self.base = f"http://{host or p['host']}:{port or p.get('port', 7125)}"
        key = api_key if api_key is not None else p.get("api_key") or ""
        self.headers = {"X-Api-Key": key} if key else {}
        self.timeout = timeout

    def _req(self, method: str, path: str, **kw):
        kw.setdefault("timeout", self.timeout)
        try:
            r = requests.request(method, self.base + path, headers=self.headers, **kw)
        except requests.RequestException as exc:
            raise PrinterError(f"Drucker unter {self.base} nicht erreichbar: {exc}") from exc
        if r.status_code in (401, 403):
            raise PrinterError(
                f"Zugriff verweigert ({r.status_code}). Moonraker verlangt einen API-Schlüssel: "
                f"in config/printer.yaml unter printer.api_key eintragen.")
        if not r.ok:
            raise PrinterError(f"{method} {path} → HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json().get("result", r.json())
        except ValueError:
            return r.text

    # ---- lesen
    def info(self) -> dict:
        return self._req("GET", "/server/info")

    def status(self) -> dict:
        q = "print_stats&extruder&heater_bed&display_status&virtual_sdcard&webhooks"
        st = self._req("GET", f"/printer/objects/query?{q}")["status"]
        ps = st.get("print_stats", {})
        prog = st.get("display_status", {}).get("progress") or st.get("virtual_sdcard", {}).get("progress") or 0
        dur = ps.get("print_duration") or 0
        remaining = None
        if prog and prog > 0.01 and dur:
            remaining = int(dur / prog - dur)
        return {
            "state": ps.get("state"),                       # standby | printing | paused | complete | cancelled | error
            "klipper": st.get("webhooks", {}).get("state"),
            "message": ps.get("message") or st.get("webhooks", {}).get("state_message"),
            "file": ps.get("filename"),
            "progress_pct": round(prog * 100, 1),
            "elapsed_min": round(dur / 60, 1),
            "remaining_min": round(remaining / 60, 1) if remaining is not None else None,
            "nozzle_c": round(st.get("extruder", {}).get("temperature", 0), 1),
            "nozzle_target_c": st.get("extruder", {}).get("target"),
            "bed_c": round(st.get("heater_bed", {}).get("temperature", 0), 1),
            "bed_target_c": st.get("heater_bed", {}).get("target"),
        }

    def cfs(self) -> dict:
        """CFS-Spulen (Platz, Werkzeug-Nr., Material, Farbe, Rest) – siehe printlab.cfs."""
        from .cfs import parse_box
        st = self._req("GET", "/printer/objects/query?box&filament_rack")["status"]
        if not st.get("box"):
            return {"connected": False, "slots": [], "external": None, "map_identity": True}
        return parse_box(st["box"], st.get("filament_rack"))

    # ---- schreiben
    def upload(self, gcode: str | Path, remote_name: str | None = None) -> str:
        gcode = Path(gcode)
        name = remote_name or gcode.name
        with open(gcode, "rb") as f:
            res = self._req("POST", "/server/files/upload", files={"file": (name, f, "application/octet-stream")},
                            data={"root": "gcodes"}, timeout=300)
        item = res.get("item", {}) if isinstance(res, dict) else {}
        return item.get("path", name)

    def start(self, remote_path: str) -> None:
        self._req("POST", "/printer/print/start", params={"filename": remote_path}, timeout=30)

    def cancel(self) -> None:
        self._req("POST", "/printer/print/cancel", timeout=30)

    def pause(self) -> None:
        self._req("POST", "/printer/print/pause", timeout=30)

    def resume(self) -> None:
        self._req("POST", "/printer/print/resume", timeout=30)
