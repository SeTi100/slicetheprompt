"""Kommandozeile:  uv run printlab <befehl> …   (dieselben Funktionen wie der MCP-Server)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _cube_stl(path: Path, a: float = 20.0) -> Path:
    from .stlio import write_stl
    v = np.array([[x, y, z] for x in (0, a) for y in (0, a) for z in (0, a)], float)
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = []
    for a_, b, c, d in q:
        tris += [[v[a_], v[b], v[c]], [v[a_], v[c], v[d]]]
    t = np.array(tris)
    from .meshcheck import signed_volume
    if signed_volume(t) < 0:
        t = t[:, ::-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_stl(path, t + [120, 120, 0])
    return path


def write_mcp_json() -> Path:
    from .config import ROOT
    p = ROOT / ".mcp.json"
    data = {"mcpServers": {"printlab": {"command": sys.executable, "args": ["-m", "printlab.mcp_server"],
                                        "env": {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}}}}
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def cmd_setup(a) -> int:
    from .config import STATE_DIR, load_config
    from .moonraker import Moonraker, PrinterError
    from .slicer import (find_slicers, gcode_info, gcode_safety, reflatten_from_yaml, setup_slicer,
                         slice_model, slice_stl)
    from .stlio import read_stl
    from .threemf import write_multipart_3mf

    ok = True
    print("== 1/5 MCP-Konfiguration")
    print("   ", write_mcp_json())

    print("== 2/5 Drucker und CFS")
    try:
        m = Moonraker()
        info = m.info()
        print(f"    Moonraker {info.get('api_version_string')}, Klipper: {info.get('klippy_state')}")
        st = m.cfs()
        for sl in st["slots"]:
            print(f"    {sl['slot']} = T{sl['tool']}: " + (f"{sl['type']} {sl['color']} ({sl['color_name']}), "
                  f"{sl['remain_pct']} %, Code {sl['code']}" if sl["loaded"] else "leer"))
    except PrinterError as exc:
        ok = False
        print("    FEHLER:", exc)

    print("== 3/5 Slicer und Profile")
    try:
        if a.keep_selection:
            s = reflatten_from_yaml()
        else:
            if not a.exe:
                for c in find_slicers():
                    print(f"    gefunden: {c['kind']:14s} {c['exe']}")
            s = setup_slicer(a.exe)
        print(f"    Programm : {s['exe']}")
        print(f"    CLI      : {' '.join(s['cli'].get('prefix') or [])} ({len(s['cli'].get('options') or [])} "
              f"Optionen im Hilfetext)")
        print(f"    Drucker  : {s['machine']}")
        for q, n in s["process"].items():
            print(f"    Qualität {q:8s}: {n}")
        for mat, n in s["filament"].items():
            print(f"    Filament {mat:8s}: {n}")
        print(f"    Filamentkatalog: {len(s.get('catalog') or {})} Profile für den Hi")
    except Exception as exc:
        print("    FEHLER:", exc)
        return 1

    print("== 4/5 Test-Slice einfarbig (Würfel 20 mm, PLA)")
    try:
        cube = _cube_stl(STATE_DIR / "test" / "cube20.stl")
        r = slice_stl(cube, "pla", "standard", outdir=STATE_DIR / "test" / "slice")
        gi = gcode_info(r["gcode"])
        print(f"    G-Code: {r['gcode']} ({r['seconds']} s)")
        if r.get("dropped_options"):
            print(f"    (vom Slicer nicht unterstützt, weggelassen: {', '.join(r['dropped_options'])})")
        print(f"    Druckzeit {gi['time']}, {gi['filament_g']} g, {gi['layers']} Schichten, "
              f"Düse max {gi['max_nozzle_temp']:.0f} °C, Bett max {gi['max_bed_temp']:.0f} °C, "
              f"Werkzeuge {gi['tools_used']}")
        probs = gcode_safety(gi, load_config(), "pla", expected_tools={0: "PLA"})
        if probs:
            print("    WARNUNG Sicherheitsprüfung:", "; ".join(probs))
            ok = False
    except Exception as exc:
        print("    FEHLER:", exc)
        ok = False

    print("== 5/5 Test-Slice mehrfarbig (Sockel auf T0, Deckel auf T1)")
    try:
        t = read_stl(_cube_stl(STATE_DIR / "test" / "cube20.stl"))
        base = t.copy()
        base[:, :, 2] *= 0.25                       # 20 x 20 x 5
        top = t.copy()
        top[:, :, 2] = top[:, :, 2] * 0.1 + 5.0     # 20 x 20 x 2 auf z = 5
        f3 = write_multipart_3mf(STATE_DIR / "test" / "multi.3mf", [("sockel", base, 1), ("deckel", top, 2)])
        fil = load_config()["slicer"]["files"]["filament"]["pla"]
        r = slice_model(f3, [fil, fil], "standard", outdir=STATE_DIR / "test" / "slice_multi")
        gi = gcode_info(r["gcode"])
        print(f"    G-Code: {r['gcode']} ({r['seconds']} s), Werkzeuge {gi['tools_used']}, "
              f"{gi['tool_changes']} Wechsel, {gi['time']}")
        if gi["tools_used"] != [0, 1]:
            ok = False
            print("    FEHLER: Mehrfarbzuordnung aus der 3MF wurde nicht übernommen (erwartet T0 und T1)")
    except Exception as exc:
        print("    FEHLER:", exc)
        ok = False
    print("\nFertig." if ok else "\nSetup mit Fehlern beendet – siehe oben.")
    return 0 if ok else 1


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="printlab", description="Prompt-to-Print für den Creality Hi")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="Slicer/Profile einrichten, Drucker testen, .mcp.json schreiben")
    s.add_argument("--exe", help="Pfad zu CrealityPrint.exe oder orca-slicer.exe")
    s.add_argument("--keep-selection", action="store_true", help="Profilnamen aus slicer.yaml beibehalten")

    sub.add_parser("doctor", help="Drucker und Slicer kurz prüfen")
    sub.add_parser("projects", help="Projekte auflisten")
    s = sub.add_parser("new", help="Projekt anlegen")
    s.add_argument("name")
    s.add_argument("--material", default="pla")
    s.add_argument("--desc", default="")
    for name in ("build", "check"):
        s = sub.add_parser(name)
        s.add_argument("project")
    s = sub.add_parser("render")
    s.add_argument("project")
    s.add_argument("--model", action="store_true", help="Modell statt Drucklage")
    s = sub.add_parser("prepare")
    s.add_argument("project")
    s.add_argument("--orient", default="wie modelliert")
    s = sub.add_parser("run", help="bauen + vorbereiten + prüfen + rendern")
    s.add_argument("project")
    s.add_argument("--orient", default=None)
    s = sub.add_parser("slice")
    s.add_argument("project")
    s.add_argument("--material")
    s.add_argument("--quality")
    s.add_argument("--filament", help="T1C | schwarz | #FF0000 | koerper=weiss,schrift=T1D")
    s.add_argument("--profile", help="exakter Profilname (oder koerper=Profil,…)")
    s = sub.add_parser("plan", help="Spulen-/Profilzuordnung anzeigen, ohne zu slicen")
    s.add_argument("project")
    s.add_argument("--filament")
    s.add_argument("--profile")
    sub.add_parser("cfs", help="Spulen im CFS anzeigen")
    s = sub.add_parser("filaments", help="Filamentprofile für den Hi auflisten")
    s.add_argument("--type")
    sub.add_parser("status")
    s = sub.add_parser("print", help="G-Code mit Freigabe-Token hochladen und starten")
    s.add_argument("token")
    for name in ("cancel", "pause", "resume"):
        sub.add_parser(name)
    s = sub.add_parser("calib", help="Kalibrier-Testteil anlegen")
    s.add_argument("material")
    s = sub.add_parser("applycal", help="Messwerte eintragen: block_x=20.1 loch5=4.8 …")
    s.add_argument("material")
    s.add_argument("values", nargs="+")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "setup":
            return cmd_setup(a)
        from . import calibration, jobs, pipeline
        from .moonraker import Moonraker
        if a.cmd == "doctor":
            from .config import load_config
            res = {"slicer": load_config().get("slicer", {}).get("exe") or "nicht eingerichtet"}
            try:
                res["printer"] = Moonraker().status()
            except Exception as exc:
                res["printer"] = f"FEHLER: {exc}"
            _out(res)
        elif a.cmd == "projects":
            _out(pipeline.list_projects())
        elif a.cmd == "new":
            _out(pipeline.new_project(a.name, a.material, a.desc))
        elif a.cmd == "build":
            _out(pipeline.build(a.project))
        elif a.cmd == "check":
            _out(pipeline._short(pipeline.check(a.project)))
        elif a.cmd == "render":
            _out(pipeline.render(a.project, "model" if a.model else "print"))
        elif a.cmd == "prepare":
            _out(pipeline.prepare(a.project, a.orient))
        elif a.cmd == "run":
            _out(pipeline.run_all(a.project, a.orient))
        elif a.cmd == "slice":
            from .mcp_server import _parse_map
            _out(jobs.slice_project(a.project, a.material, a.quality, _parse_map(a.filament or ""),
                                    _parse_map(a.profile or "")))
        elif a.cmd == "plan":
            from .mcp_server import _parse_map
            r = jobs.plan_filaments(a.project, None, _parse_map(a.filament or ""), _parse_map(a.profile or ""))
            r.pop("cfs", None)
            _out(r)
        elif a.cmd == "cfs":
            st = Moonraker().cfs()
            for sl in st["slots"]:
                print(f"{sl['slot']}  T{sl['tool']}  " + (f"{sl['type']:5s} {sl['color']} {sl['color_name']:8s} "
                      f"{sl['remain_pct']} %  Code {sl['code']}" if sl["loaded"] else "leer"))
            if not st["map_identity"]:
                print("Hinweis: Platzzuordnung (map) ist umgestellt")
        elif a.cmd == "filaments":
            from .slicer import filament_catalog
            for f in filament_catalog(a.type):
                print(f"{f['type']:6s} {f['filament_id']:7s} {f['name']}")
        elif a.cmd == "status":
            _out(Moonraker().status())
        elif a.cmd == "print":
            _out(jobs.print_gcode(a.token))
        elif a.cmd in ("cancel", "pause", "resume"):
            getattr(Moonraker(), a.cmd)()
            _out({"ok": True, "action": a.cmd})
        elif a.cmd == "calib":
            _out(calibration.create_calibration_project(a.material))
        elif a.cmd == "applycal":
            vals = dict(v.split("=", 1) for v in a.values)
            _out(calibration.apply_calibration(a.material, {k: float(x.replace(",", ".")) for k, x in vals.items()}))
    except Exception as exc:
        print(f"FEHLER: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
