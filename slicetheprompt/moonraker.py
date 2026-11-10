"""Verbindung zum Creality Hi über die Moonraker-API im lokalen Netz."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

import requests

from .config import load_config


class PrinterError(RuntimeError):
    pass


class PrinterOffline(PrinterError):
    """Keine Verbindung (Ruhemodus, falsche IP) – im Gegensatz zu einem einzelnen fehlenden Endpunkt."""


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
            raise PrinterOffline(f"Drucker unter {self.base} nicht erreichbar: {exc}") from exc
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
        q = "print_stats&extruder&heater_bed&display_status&virtual_sdcard&webhooks&fan&gcode_move"
        st = self._req("GET", f"/printer/objects/query?{q}")["status"]
        ps = st.get("print_stats", {})
        layer = ps.get("info") or {}
        gm = st.get("gcode_move", {})
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
            "layer": layer.get("current_layer"),
            "layers_total": layer.get("total_layer"),
            "filament_used_m": round((ps.get("filament_used") or 0) / 1000, 2),
            "fan_pct": round((st.get("fan", {}).get("speed") or 0) * 100) if st.get("fan") else None,
            "speed_factor_pct": round(gm["speed_factor"] * 100) if gm.get("speed_factor") else None,
            "flow_factor_pct": round(gm["extrude_factor"] * 100) if gm.get("extrude_factor") else None,
        }

    # Klipper-Objekte, die über print_stats/extruder hinaus nützlich sind (soweit der Hi sie meldet)
    _DETAIL_PREFIXES = ("temperature_sensor", "temperature_fan", "heater_fan", "controller_fan", "fan_generic",
                        "filament_switch_sensor", "filament_motion_sensor", "motion_report", "toolhead",
                        "system_stats", "exclude_object", "output_pin")

    # Creality-Wartungszähler (gcode_macro MAINTENANCE_ITEM): cur_value/threshold, timeout = fällig
    WARTUNG = {
        "cut_calibration": "Schneider kalibrieren", "shaper_calibrate": "Input Shaping kalibrieren",
        "belt_tensioning": "Riemen spannen", "motion_mechanism_lubrication": "Führungen schmieren",
        "camera_maintenance": "Kamera reinigen", "fan_inspection": "Lüfter prüfen", "nozzle": "Düse",
        "cut": "Schneidmesser", "machine_teflon_tube": "PTFE-Schlauch Drucker", "wipe_mouth_strip": "Abstreifer",
        "air_filter": "Luftfilter", "cfs_teflon_tube": "PTFE-Schlauch CFS", "cfs_desiccant": "Trockenmittel CFS",
    }

    def _safe(self, path: str):
        try:
            return self._req("GET", path)
        except PrinterOffline:
            raise
        except PrinterError:
            return None

    def details(self) -> dict:
        """Alles Lesbare über den Status hinaus: Sensoren, Lüfter, Achsen, Statistik, Kamera, System.
        Fehlt ein Endpunkt (Creality-Firmware ist ein abgespecktes Moonraker), bleibt der Eintrag leer."""
        objs = (self._safe("/printer/objects/list") or {}).get("objects", [])
        want = [o for o in objs if o.split(" ")[0] in self._DETAIL_PREFIXES
                or o in ("gcode_macro MAINTENANCE_ITEM", "box")]
        st = {}
        if want:
            q = "&".join(urllib.parse.quote(o) for o in want)
            st = (self._safe(f"/printer/objects/query?{q}") or {}).get("status", {})
        sensors, fans, switches, pins = [], [], [], []
        for name, v in st.items():
            kind, _, label = name.partition(" ")
            label = label or kind
            if kind in ("temperature_sensor", "temperature_fan") and "temperature" in v:
                sensors.append({"name": label, "c": round(v["temperature"], 1), "target": v.get("target")})
            if "fan" in kind and "speed" in v:
                fans.append({"name": label, "pct": round((v.get("speed") or 0) * 100), "rpm": v.get("rpm")})
            elif kind == "output_pin" and "fan" in label and "value" in v:   # Creality: Lüfter als output_pin
                val = v["value"] or 0
                fans.append({"name": label, "pct": round(val * 100 if val <= 1 else val / 2.55), "rpm": None})
            if kind.startswith("filament_") and "filament_detected" in v:
                switches.append({"name": label, "filament": v["filament_detected"], "enabled": v.get("enabled")})
            if kind == "output_pin" and "fan" not in label and "value" in v:
                pins.append({"name": label, "value": v["value"]})
        box = st.get("box") or {}
        for unit in range(1, 5):
            u = box.get(f"T{unit}") or {}
            if u.get("state") == "connect":
                try:
                    sensors.append({"name": f"CFS {unit}", "c": float(u.get("temperature")),
                                    "humidity_pct": float(u.get("dry_and_humidity")), "target": None})
                except (TypeError, ValueError):
                    pass
        wartung = []
        for gruppe, items in (st.get("gcode_macro MAINTENANCE_ITEM") or {}).items():
            for key, w in (items or {}).items():
                if not isinstance(w, dict) or not w.get("threshold"):
                    continue
                wartung.append({"key": key, "name": self.WARTUNG.get(key, key), "gruppe": gruppe,
                                "pct": round(100 * (w.get("cur_value") or 0) / w["threshold"]),
                                "faellig": bool(w.get("timeout"))})
        wartung.sort(key=lambda w: (not w["faellig"], -w["pct"]))
        th = st.get("toolhead", {})
        mr = st.get("motion_report", {})
        ss = st.get("system_stats", {})
        ex = st.get("exclude_object", {})
        totals = (self._safe("/server/history/totals") or {}).get("job_totals") or {}
        last = (self._safe("/server/history/list?limit=5&order=desc") or {}).get("jobs") or []
        cams = (self._safe("/server/webcams/list") or {}).get("webcams") or []
        sysinfo = (self._safe("/machine/system_info") or {}).get("system_info") or {}
        info = self._safe("/server/info") or {}
        return {
            "objects": objs,
            "sensors": sensors, "fans": fans, "filament_sensors": switches, "pins": pins, "wartung": wartung,
            "toolhead": {"homed": th.get("homed_axes"), "position": [round(x, 2) for x in th.get("position", [])[:3]],
                         "max_velocity": th.get("max_velocity"), "max_accel": th.get("max_accel")} if th else None,
            "live": {"velocity_mm_s": round(mr.get("live_velocity") or 0, 1),
                     "flow_mm3_s": round((mr.get("live_extruder_velocity") or 0) * 2.405, 1)} if mr else None,
            "system": {"cpu_temp_c": round(ss["cputemp"], 1) if ss.get("cputemp") else None,
                       "load": ss.get("sysload"), "mem_kb": ss.get("memavail")} if ss else None,
            "objects_in_print": [o.get("name") for o in ex.get("objects", [])] if ex else None,
            "excluded_objects": ex.get("excluded_objects") if ex else None,
            "history_totals": {"jobs": totals.get("total_jobs"),
                               "print_h": round((totals.get("total_print_time") or 0) / 3600, 1),
                               "filament_m": round((totals.get("total_filament_used") or 0) / 1000, 1),
                               "longest_h": round((totals.get("longest_print") or 0) / 3600, 1)} if totals else None,
            "history_last": [{"file": j.get("filename"), "status": j.get("status"),
                              "end": j.get("end_time"), "print_min": round((j.get("print_duration") or 0) / 60),
                              "filament_m": round((j.get("filament_used") or 0) / 1000, 2)} for j in last],
            "webcams": [{"name": c.get("name"), "stream": self._abs(c.get("stream_url")),
                         "snapshot": self._abs(c.get("snapshot_url"))} for c in cams if c.get("enabled", True)],
            "machine": {"host": self.base,
                        "moonraker": (info.get("moonraker_version") if info.get("moonraker_version") not in (None, "?")
                                      else f"API {info.get('api_version_string', '?')}"),
                        "klipper_state": info.get("klippy_state"),
                        "components": info.get("components"),
                        "cpu": (sysinfo.get("cpu_info") or {}).get("processor"),
                        "os": (sysinfo.get("distribution") or {}).get("name")},
        }

    def _abs(self, url: str | None) -> str | None:
        """Relative Kamera-URLs (/webcam/?action=stream) auf den Drucker beziehen."""
        if not url:
            return None
        if url.startswith("http"):
            return url
        host = self.base.rsplit(":", 1)[0]
        return host + (url if url.startswith("/") else "/" + url)

    def cfs(self) -> dict:
        """CFS-Spulen (Platz, Werkzeug-Nr., Material, Farbe, Rest) – siehe slicetheprompt.cfs."""
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
