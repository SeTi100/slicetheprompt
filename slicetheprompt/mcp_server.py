"""MCP-Server 'slicetheprompt' für Claude Code (stdio).

Wichtig: Dieser Prozess darf nichts auf stdout schreiben (das ist der MCP-Kanal).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except Exception:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server

from . import calibration, jobs, pipeline
from .config import load_config
from .moonraker import Moonraker

INSTRUCTIONS = """\
slicetheprompt erzeugt druckfertige Teile für den Creality Hi Combo (0,4-mm-Düse, PLA/PETG).
Ablauf: project_new → spec.yaml und model.py schreiben → build_and_check → Render ansehen →
cfs_status/filament_plan → slice → Nutzer bestätigt mit 'drucken' → upload_and_print(token).
Mehrfarbig: in model.py `bodies = {name: Körper}`, Spule je Körper in spec.yaml → filament.
Baugruppe (mehrere Teil-Projekte zusammen ansehen/prüfen): assembly_new → assembly.py → build_assembly.
Regeln und Toleranzen: config/printer.yaml (und calibration.yaml). Nie ohne ausdrückliche Bestätigung drucken.
3D-Feedback (SliceThePrompt UI): get_ui_feedback liefert bei Baugruppen direkt target_project und part_print_xyz in Drucklage; Umbau in einem Schritt mit build_and_check(project, also_assembly=..., clear_feedback=True).
"""

server = _Server("slicetheprompt", instructions=INSTRUCTIONS)

ROOT = Path(__file__).resolve().parents[1]
WORKER_TIMEOUT_S = 840          # unter dem MCP-Timeout von 900 s in .mcp.json


def _code_fingerprint() -> str:
    """Kennung des Codes im Checkout (Grösse + Änderungszeit aller .py in slicetheprompt/ und lib/)."""
    h = hashlib.sha1()
    for p in sorted([*(ROOT / "slicetheprompt").glob("*.py"), *(ROOT / "lib").glob("*.py")]):
        st = p.stat()
        h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns};".encode())
    return h.hexdigest()[:12]


def _git_head() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                           timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


_STARTED = dt.datetime.now().isoformat(timespec="seconds")
_START_CODE = _code_fingerprint()
_START_GIT = _git_head()


def code_stand() -> dict:
    """Ist der laufende Server älter als der Code im Checkout?"""
    now = _code_fingerprint()
    st = {"server_gestartet": _STARTED, "git_beim_start": _START_GIT, "git_jetzt": _git_head(),
          "veraltet": now != _START_CODE}
    if st["veraltet"]:
        st["hinweis"] = ("Code wurde nach dem Serverstart geändert. Bauen/Prüfen/Rendern laufen schon mit dem neuen "
                         "Code (eigener Prozess).")
    return st


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


def _worker(name: str, *args) -> dict:
    """Pipeline-Schritt in eigenem Prozess (parallel zu anderen Sessions, immer aktueller Code)."""
    try:
        proc = subprocess.run([sys.executable, "-m", "slicetheprompt._worker", name, json.dumps(list(args))],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=WORKER_TIMEOUT_S, env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{name} nach {WORKER_TIMEOUT_S} s abgebrochen",
                "hinweis": "Ergebnis ggf. in out/report.json; per CLI wiederholen: uv run slicetheprompt run <projekt>"}
    try:
        res = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"{name}: keine gültige Antwort (Exit {proc.returncode})",
                "stderr": proc.stderr[-2000:], "stdout": proc.stdout[-500:]}
    if isinstance(res, dict) and code_stand()["veraltet"]:
        res["server_veraltet"] = code_stand()["hinweis"]
    return res


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
def build_and_check(project: str, orientation: str = "wie modelliert",
                    also_assembly: str = "auto", clear_feedback: bool = True,
                    params: dict[str, float] | None = None,
                    spec: dict | None = None) -> dict:
    """Optional `params` ({"NAME": Wert}) in model.py und `spec` in spec.yaml setzen, dann model.py
    ausführen (build123d), STEP+STL exportieren, gegen printer.yaml und spec.yaml prüfen und out/render.png erzeugen.
    Bei passed=True werden automatisch alle Baugruppen mitgebaut, die das Teil enthalten (also_assembly='auto',
    abschaltbar mit 'none'), und erledigtes 3D-UI-Feedback gelöscht (clear_feedback=True).
    Danach IMMER das PNG mit dem Read-Tool ansehen."""
    return _worker("run_all", project, orientation, "build_and_check", also_assembly, clear_feedback, params, spec)


@server.tool()
def stencil(project: str, z: list[float] | None = None, height: float = 2.0) -> dict:
    """Schablone: dünnen Passungs-Testdruck aus einem Projekt erzeugen (Projekt '<name>-schablone',
    wird bei erneutem Aufruf aktualisiert). Schneidet Scheibe(n) der Dicke `height` (mm, Standard 2)
    ab den Höhen `z` (mm im Original, Standard [0] = Unterseite) und legt sie aufs Bett – Löcher,
    Stifte und Aussenmasse in XY bleiben exakt wie im Original. z dort wählen, wo die Passungen liegen
    (Hinweise in `features_z`). Baut, prüft und rendert sofort; danach PNG ansehen und normal slicen."""
    return _worker("new_stencil", project, z or [0.0], height)


@server.tool()
def build_assembly(project: str) -> dict:
    """Baugruppe bauen und prüfen (Projekt mit assembly.py statt model.py): lädt je Teil out/model.step,
    setzt die Teile in jeder Pose (POSEN) zusammen, misst Überschneidungen exakt (rote STLs) und den
    kleinsten Abstand je Paar, prüft erlaubte Klemmkontakte (KONTAKT) und schreibt out/assembly.json
    sowie out/render.png (erste Pose). Mit GELENKE in assembly.py werden Posen mit Gelenkwerten gelöst
    und Platzierungen gegen die Gelenke geprüft (Hinweise). Teile vorher einzeln mit build_and_check bauen.
    Danach das PNG ansehen; im Studio lassen sich alle Posen ohne Neubau umschalten."""
    return _worker("build_assembly", project)


@server.tool()
def assembly_new(name: str, parts: list[str], description: str = "") -> dict:
    """Baugruppen-Projekt projects/<datum>-<name>/ mit assembly.py-Vorlage anlegen.
    parts: Teil-Projekte (Namen unter projects/). Danach platzierung/POSEN/geister ausfüllen
    und build_assembly aufrufen."""
    from .assembly import new_assembly
    return _safe(new_assembly, name, parts, description)


@server.tool()
def check_report(project: str) -> dict:
    """Vollständigen Prüfbericht (alle Regeln, Wandstärken, Brücken, Bettkontakt) zurückgeben."""
    return _worker("check", project)


@server.tool()
def render_views(project: str, which: str = "print") -> dict:
    """4-Ansichten-PNG erzeugen. which: 'print' (Drucklage) oder 'model'. Pfad mit Read ansehen."""
    return _worker("render", project, which)


@server.tool()
def cfs_status() -> dict:
    """Spulen im CFS: Platz (T1A…T4D), Werkzeug im G-Code (T0…), Material, Farbe, Restmenge %,
    plus passendes Creality-Print-Profil. Vor jeder Filamentwahl aufrufen."""
    def _c():
        from . import cfs as cfsmod
        from .slicer import slicer_cfg
        st = Moonraker().cfs()
        try:
            s = slicer_cfg()
        except Exception:
            s = {}
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
def printer_details() -> dict:
    """Alles weitere, was Moonraker lesbar liefert: Kammer-/Zusatzsensoren, Lüfter, Filamentsensoren,
    Achsen/Position, Live-Geschwindigkeit und -Fluss, Druckstatistik (Summe, letzte Drucke), Kamera-URLs,
    System (CPU-Temperatur, Firmware). Rein lesend."""
    return _safe(lambda: Moonraker().details())


@server.tool()
def print_cost(project: str) -> dict:
    """Kosten des zuletzt gesliceten G-Codes: Filament je Spule (Rollenpreis aus config/preise.yaml,
    sonst Standardpreis), Strom, Verschleiss, Total in CHF. Wird auch bei `slice` unter `kosten` geliefert."""
    from .kosten import for_project
    return _safe(for_project, project)


@server.tool()
def filament_price(action: str = "list", name: str = "", preis: float = 0, gewicht_g: float = 0, url: str = "",
                   material: str = "", code: str = "", profil: str = "", farbe: str = "", slot: str = "") -> dict:
    """Rollenpreise pflegen. action: 'list' | 'set' | 'delete' | 'refresh'.
    set: name (z. B. 'Hyper PLA schwarz'), preis (CHF je Rolle) und/oder url (Digitec/Galaxus-Produktlink,
    Preis wird abgerufen), gewicht_g (Standard 1000), optional Filter material/code(RFID)/profil/farbe/slot –
    der genaueste passende Eintrag gilt für eine Spule. Blockt der Shop den Abruf (Bot-Schutz), Preis
    manuell angeben (z. B. selbst nachgeschlagen) – den Schutz nie umgehen."""
    from . import kosten as K

    def _run():
        if action == "list":
            return {"rollen": K.load_prices(), "standard": K.settings(), "datei": str(K.PREISE_YAML)}
        if action == "set":
            return K.set_price(name, preis or None, gewicht_g or None, url or None, material=material, code=code,
                               profil=profil, farbe=farbe, slot=slot)
        if action == "delete":
            return K.delete_price(name)
        if action == "refresh":
            return K.refresh_prices()
        raise ValueError("action muss list, set, delete oder refresh sein")
    return _safe(_run)


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
def doctor() -> dict:
    """Zustand prüfen: Drucker (Moonraker, Klipper, CFS), Slicer (gefunden, Profile aufgelöst – richtet
    sich bei Bedarf selbst ein), Python-Umgebung. Bei Problemen zuerst aufrufen."""
    from .doctor import run_doctor
    return _safe(run_doctor)


@server.tool()
def get_config() -> dict:
    """Aktuelle Regeln, Toleranzen (inkl. Kalibrierung), Grenzen, Slicer-Auswahl und unter `bausteine`
    alle CAD-Funktionen aus lib/ (Signatur + Kurzbeschreibung) – lib/*.py muss dafür nicht gelesen werden."""
    def _c():
        from .libref import lib_reference
        c = load_config()
        s = c.get("slicer", {})
        c["slicer"] = {k: s.get(k) for k in ("kind", "exe", "machine", "process", "filament")}
        c["bausteine"] = lib_reference()
        c["code_stand"] = code_stand()
        return c
    return _safe(_c)


@server.tool()
def get_ui_feedback(project: str = "") -> dict:
    """Liest das vom Nutzer in SliceThePrompt (3D-GUI) gespeicherte räumliche Feedback
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
    return _worker("restore_version", project, version)


def main() -> None:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    server.run()


if __name__ == "__main__":
    main()
