"""MCP-Server 'printlab' für Claude Code (stdio).

Wichtig: Dieser Prozess darf nichts auf stdout schreiben (das ist der MCP-Kanal).
"""

from __future__ import annotations

import contextlib
import io
import sys
import traceback

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except Exception:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server

from . import calibration, jobs, pipeline
from .config import load_config
from .moonraker import Moonraker

INSTRUCTIONS = """\
printlab erzeugt druckfertige Teile für den Creality Hi Combo (0,4-mm-Düse, PLA/PETG).
Ablauf: project_new → spec.yaml und model.py schreiben → build_and_check → Render ansehen →
cfs_status/filament_plan → slice → Nutzer bestätigt mit 'drucken' → upload_and_print(token).
Mehrfarbig: in model.py `bodies = {name: Körper}`, Spule je Körper in spec.yaml → filament.
Regeln und Toleranzen: config/printer.yaml (und calibration.yaml). Nie ohne ausdrückliche Bestätigung drucken.
"""

server = _Server("printlab", instructions=INSTRUCTIONS)


def _safe(fn, *a, **k) -> dict:
    """Fehler als Ergebnis zurückgeben statt den Server zu stören; stdout abfangen."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            res = fn(*a, **k)
        if isinstance(res, list):
            res = {"items": res}
        return res
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=4)[-1500:], "stdout": buf.getvalue()[-500:]}


@server.tool()
def project_new(name: str, material: str = "pla", description: str = "") -> dict:
    """Neues Projekt unter projects/<datum>-<name>/ mit Vorlagen spec.yaml und model.py anlegen.
    material: 'pla' oder 'petg'. Danach spec.yaml und model.py mit dem Edit/Write-Tool ausfüllen."""
    return _safe(pipeline.new_project, name, material, description)


@server.tool()
def project_list() -> dict:
    """Alle Projekte mit Build-Status und vorhandenen G-Codes."""
    return _safe(pipeline.list_projects)


@server.tool()
def build_and_check(project: str, orientation: str = "wie modelliert") -> dict:
    """model.py ausführen (build123d), STEP+STL exportieren, Drucklage festlegen, gegen
    printer.yaml und spec.yaml prüfen und out/render.png erzeugen.
    orientation: 'wie modelliert' (Standard), 'auto' oder 'kopfüber (X 180°)', 'X +90°', 'X -90°', 'Y +90°', 'Y -90°'.
    Danach IMMER das PNG mit dem Read-Tool ansehen."""
    return _safe(pipeline.run_all, project, orientation)


@server.tool()
def check_report(project: str) -> dict:
    """Vollständigen Prüfbericht (alle Regeln, Wandstärken, Brücken, Bettkontakt) zurückgeben."""
    return _safe(pipeline.check, project)


@server.tool()
def render_views(project: str, which: str = "print") -> dict:
    """4-Ansichten-PNG erzeugen. which: 'print' (Drucklage) oder 'model'. Pfad mit Read ansehen."""
    return _safe(pipeline.render, project, which)


@server.tool()
def cfs_status() -> dict:
    """Spulen im CFS: Platz (T1A…T4D), Werkzeug im G-Code (T0…), Material, Farbe, Restmenge %,
    plus passendes Creality-Print-Profil. Vor jeder Filamentwahl aufrufen."""
    def _c():
        from . import cfs as cfsmod
        cfg = load_config()
        st = Moonraker().cfs()
        s = cfg.get("slicer") or {}
        for sl in st["slots"]:
            if sl["loaded"]:
                sl["profile"] = cfsmod.match_profile(sl, s.get("catalog") or {}, s.get("filament") or {})
        return st
    return _safe(_c)


@server.tool()
def list_filaments(material_type: str = "") -> dict:
    """Alle Creality-Print-Filamentprofile für den Hi (Name, Typ, filament_id, Hersteller).
    material_type: z. B. 'PLA' oder 'PETG' zum Filtern."""
    from .slicer import filament_catalog
    return _safe(filament_catalog, material_type or None)


@server.tool()
def filament_plan(project: str, filament: str = "", profile: str = "") -> dict:
    """Zeigt, welche Spule und welches Profil je Körper verwendet würde – ohne zu slicen.
    filament: leer = aus spec.yaml; sonst 'T1C' / 'schwarz' / '#FF0000' oder für mehrfarbig
    'koerper=weiss,schrift=T1D'. profile: exakter Profilname (optional, gleiche Syntax)."""
    return _safe(jobs.plan_filaments, project, None, _parse_map(filament), _parse_map(profile))


def _parse_map(v: str):
    """'' → None; 'T1C' → 'T1C'; 'a=T1A,b=schwarz' → {'a': 'T1A', 'b': 'schwarz'}"""
    v = (v or "").strip()
    if not v:
        return None
    if "=" in v:
        return {k.strip(): x.strip() for k, x in (p.split("=", 1) for p in v.split(",") if "=" in p)}
    return v


@server.tool(name="slice")
def slice_gcode(project: str, material: str = "", quality: str = "", filament: str = "",
                profile: str = "") -> dict:
    """Druckfertige Drucklage slicen (Creality-Print-CLI, Profile für den Hi 0,4 mm).
    Ein- oder mehrfarbig (bei 'bodies' in model.py automatisch als 3MF mit Filament je Körper).
    material: 'pla'|'petg' (Standard aus spec.yaml), quality: 'fein'|'standard'|'grob',
    filament/profile: wie bei filament_plan (leer = aus spec.yaml bzw. automatisch nach CFS-Spule).
    Gibt je Körper Spule/Farbe/Profil, Druckzeit, Filament und einen Freigabe-Token zurück.
    Danach dem Nutzer alles zeigen und auf ausdrückliches 'drucken' warten."""
    return _safe(jobs.slice_project, project, material or None, quality or None,
                 _parse_map(filament), _parse_map(profile))


@server.tool()
def printer_status() -> dict:
    """Zustand des Druckers: state, Fortschritt %, Restzeit, Düse/Bett °C."""
    return _safe(lambda: Moonraker().status())


@server.tool()
def upload_and_print(approval_token: str) -> dict:
    """G-Code zum Freigabe-Token auf den Hi laden und Druck STARTEN.
    Nur aufrufen, nachdem der Nutzer in diesem Chat ausdrücklich 'drucken' geschrieben hat,
    das Bett frei ist und das richtige Filament geladen ist."""
    return _safe(jobs.print_gcode, approval_token)


@server.tool()
def print_control(action: str) -> dict:
    """Laufenden Druck steuern. action: 'pause' | 'resume' | 'cancel'."""
    if action not in ("pause", "resume", "cancel"):
        return {"ok": False, "error": "action muss pause, resume oder cancel sein"}
    return _safe(lambda: (getattr(Moonraker(), action)(), {"ok": True, "action": action})[1])


@server.tool()
def calibration_part(material: str) -> dict:
    """Projekt mit Toleranz-Testteil (Löcher Ø5/8/10, Stift Ø8, Block 20 mm) für ein Material anlegen."""
    return _safe(calibration.create_calibration_project, material)


@server.tool()
def apply_calibration(material: str, block_x: float, block_y: float, block_z: float,
                      loch5: float, loch8: float, loch10: float, stift8: float,
                      gesamt_x: float = 0, gesamt_y: float = 0, platte_z: float = 0, stift_h: float = 0,
                      notes: str = "") -> dict:
    """Gemessene Werte (mm) des Testteils eintragen; schreibt config/calibration.yaml.
    Optional (0 = nicht gemessen): gesamt_x/gesamt_y (Gesamtmasse 70 × 20), platte_z (4), stift_h (12).
    notes: Auffälligkeiten (z. B. dickere Ecken)."""
    vals = dict(block_x=block_x, block_y=block_y, block_z=block_z, loch5=loch5, loch8=loch8, loch10=loch10,
                stift8=stift8, gesamt_x=gesamt_x, gesamt_y=gesamt_y, platte_z=platte_z, stift_h=stift_h)
    return _safe(calibration.apply_calibration, material, {k: v for k, v in vals.items() if v}, notes)


@server.tool()
def get_config() -> dict:
    """Aktuelle Regeln, Toleranzen (inkl. Kalibrierung), Grenzen und Slicer-Auswahl."""
    def _c():
        c = load_config()
        s = c.get("slicer", {})
        c["slicer"] = {k: s.get(k) for k in ("kind", "exe", "machine", "process", "filament")}
        return c
    return _safe(_c)


@server.tool()
def get_ui_feedback(project: str = "") -> dict:
    """Liest das vom Nutzer in printlab Studio (3D-GUI) gespeicherte räumliche Feedback
    (Pins, Bereichsboxen, Von->Nach-Verschiebepfeile in Modellkoordinaten `model_xyz` sowie
    den Pfad zum markierten Screenshot `out/ui_feedback.png`).
    Falls `project` leer ist, wird das zuletzt markierte Projekt zurückgegeben.
    Danach `screenshot` mit Read/read_file ansehen und nach erfolgreichem Umbau `clear_ui_feedback` aufrufen."""
    from .ui_server import read_project_feedback
    return _safe(read_project_feedback, project)


@server.tool()
def clear_ui_feedback(project: str) -> dict:
    """Löscht erledigtes 3D-UI-Feedback (`out/ui_feedback.json` + `out/ui_feedback.png`) nach erfolgreichem Umbau."""
    from .config import project_dir
    from .ui_server import clear_project_feedback
    return _safe(lambda: clear_project_feedback(project_dir(project)))


@server.tool()
def version_list(project: str) -> dict:
    """Listet alle gespeicherten Versions-Snapshots (`v001`, `v002`, ...) eines Projekts auf."""
    from . import history
    return _safe(history.list_versions, project)


@server.tool()
def version_restore(project: str, version: str) -> dict:
    """Stellt eine ältere Projektversion (`v001`, `v002`, ...) wieder her und führt Build + Prüfung aus."""
    from . import history
    return _safe(history.restore_version, project, version)


def main() -> None:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    server.run()


if __name__ == "__main__":
    main()
