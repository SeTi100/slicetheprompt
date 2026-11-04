"""Zustandsprüfung: Drucker, CFS, Slicer, Python – für den Chat (MCP-Werkzeug doctor) und die CLI."""

from __future__ import annotations

import platform
import sys

from .config import ROOT, load_config


def run_doctor(fix: bool = True) -> dict:
    out: dict = {"ok": True, "root": str(ROOT), "python": sys.version.split()[0],
                 "system": f"{platform.system()} {platform.release()}"}
    cfg = load_config()
    p = cfg.get("printer", {})
    out["printer"] = {"host": p.get("host"), "port": p.get("port", 7125), "model": p.get("name")}

    # Drucker
    try:
        from .moonraker import Moonraker
        m = Moonraker()
        info = m.info()
        st = m.status()
        cfs = m.cfs()
        out["printer"].update({
            "reachable": True, "moonraker": info.get("api_version_string"), "klipper": info.get("klippy_state"),
            "state": st.get("state"),
            "cfs": [f"{s['slot']}=T{s['tool']} {s['type']} {s['color_name']} {s['remain_pct']} %"
                    for s in cfs.get("slots", []) if s["loaded"]] or "kein CFS / leer",
        })
    except Exception as exc:
        out["ok"] = False
        out["printer"].update({"reachable": False, "error": str(exc),
                               "hint": "IP in config/printer.yaml prüfen; Drucker eingeschaltet und im selben Netz?"})

    # Slicer (richtet sich bei Bedarf selbst ein)
    try:
        from .slicer import _slicer_ok, slicer_cfg
        s = slicer_cfg(auto=fix)
        if not _slicer_ok(s):
            raise RuntimeError("nicht eingerichtet")
        out["slicer"] = {"ok": True, "exe": s.get("exe"), "machine": s.get("machine"),
                         "process": s.get("process"), "filament": s.get("filament"),
                         "catalog": len(s.get("catalog") or {})}
    except Exception as exc:
        out["ok"] = False
        out["slicer"] = {"ok": False, "error": str(exc),
                         "hint": "Creality Print installieren bzw. Pfad mit 'uv run printlab setup --exe …' angeben"}

    # CAD
    try:
        import build123d  # noqa: F401
        out["cad"] = {"ok": True, "build123d": getattr(build123d, "__version__", "?")}
    except Exception as exc:
        out["ok"] = False
        out["cad"] = {"ok": False, "error": str(exc), "hint": "uv sync ausführen"}
    return out
