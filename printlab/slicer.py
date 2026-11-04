"""Slicen mit der Kommandozeile von Creality Print oder OrcaSlicer.

Beide nutzen dieselbe Engine und dasselbe Profilformat (JSON mit 'inherits'-Ketten).
setup_slicer() sucht das Programm, löst die Profile für den Hi mit 0,4-mm-Düse auf
("flatten") und legt sie unter config/slicer/ ab; slice() ruft dann nur noch die CLI auf.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .config import CONFIG_DIR, SLICER_YAML, load_config, write_yaml

PROFILE_DIR = CONFIG_DIR / "slicer"
QUALITY_LAYER = {"fein": 0.12, "standard": 0.20, "grob": 0.28}
IS_WIN = sys.platform.startswith("win")


# ---------------------------------------------------------------- Programm finden

def find_slicers() -> list[dict]:
    """Bekannte Installationsorte unter Windows durchsuchen."""
    found = []
    if not IS_WIN:
        return found
    bases = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    bases = [Path(b) for b in bases if b]
    patterns = [
        ("crealityprint", "Creality/*/CrealityPrint.exe"),
        ("crealityprint", "Creality/*/*/CrealityPrint.exe"),
        ("crealityprint", "Programs/Creality/*/CrealityPrint.exe"),
        ("orca", "OrcaSlicer/orca-slicer.exe"),
        ("orca", "Programs/OrcaSlicer/orca-slicer.exe"),
    ]
    for base in bases:
        for kind, pat in patterns:
            for exe in base.glob(pat):
                found.append({"kind": kind, "exe": str(exe), "version_dir": exe.parent.name})
    # Neueste Version zuerst (Ordnername enthält meist die Versionsnummer)
    found.sort(key=lambda f: (f["kind"] != "crealityprint", _version_key(f["exe"])), reverse=False)
    return found


def _version_key(s: str):
    nums = re.findall(r"\d+", s)
    return [-int(n) for n in nums[:4]]


# ---------------------------------------------------------------- Profile

def profile_roots(exe: str) -> list[Path]:
    roots = []
    exe_p = Path(exe)
    for cand in (exe_p.parent / "resources" / "profiles", exe_p.parent.parent / "resources" / "profiles"):
        if cand.exists():
            roots.append(cand)
    appdata = os.environ.get("APPDATA")
    if appdata:
        for sub in ("Creality", "OrcaSlicer"):
            p = Path(appdata) / sub
            if p.exists():
                roots.append(p)
    return roots


def index_profiles(roots: list[Path]) -> dict[str, dict]:
    """Name -> {path, type, data, user}. Spätere Wurzeln überschreiben frühere nicht."""
    idx: dict[str, dict] = {}
    for root in roots:
        for p in root.rglob("*.json"):
            try:
                if p.stat().st_size > 3_000_000:
                    continue
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or "name" not in data:
                continue
            parts = {x.lower() for x in p.parts}
            typ = data.get("type") or next((t for t in ("machine", "process", "filament") if t in parts), None)
            if typ not in ("machine", "process", "filament"):
                continue
            user = "user" in parts
            name = data["name"]
            # Benutzerprofile haben Vorrang vor Systemkopien gleichen Namens
            if name in idx and not (user and not idx[name]["user"]):
                continue
            idx[name] = {"path": str(p), "type": typ, "data": data, "user": user}
    return idx


def flatten(name: str, idx: dict, _depth: int = 0) -> dict:
    if _depth > 20:
        raise RuntimeError(f"Profilkette zu tief bei '{name}'")
    if name not in idx:
        raise KeyError(f"Profil '{name}' nicht gefunden")
    data = dict(idx[name]["data"])
    parent = data.get("inherits")
    if parent:
        base = flatten(parent, idx, _depth + 1)
        base.update(data)
        data = base
    data["inherits"] = ""
    data["type"] = idx[name]["type"]
    return data


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _first_float(v, default=0.0):
    try:
        return float(_as_list(v)[0])
    except (ValueError, IndexError, TypeError):
        return default


def select_profiles(idx: dict) -> dict:
    """Profile für Creality Hi, 0,4-mm-Düse, PLA und PETG auswählen."""
    hi = re.compile(r"(^|[^A-Za-z])Hi([^A-Za-z]|$)")
    machines = [n for n, e in idx.items() if e["type"] == "machine" and hi.search(n) and "0.4" in n]
    if not machines:
        raise RuntimeError("Kein Druckerprofil 'Creality Hi … 0.4' gefunden. Ist der Hi in Creality Print eingerichtet?")

    def m_rank(n):
        e = idx[n]
        return (0 if e["user"] else 1, 0 if "Creality Hi 0.4" in n else 1, len(n))

    machines.sort(key=m_rank)
    machine = machines[0]
    flat_m = flatten(machine, idx)
    system_machine = machine
    # Bei Benutzerprofil: Namen der System-Basis für Kompatibilitätsprüfung merken
    probe = idx[machine]["data"]
    while idx.get(machine, {}).get("user") and probe.get("inherits"):
        system_machine = probe["inherits"]
        probe = idx.get(system_machine, {}).get("data", {})
        if not idx.get(system_machine, {}).get("user"):
            break
    names_ok = {machine, system_machine}

    def compatible(entry_name: str, flat: dict) -> int:
        cp = set(_as_list(flat.get("compatible_printers")))
        if cp & names_ok:
            return 2
        if "Hi" in entry_name and "0.4" in entry_name:
            return 2
        if not cp and not flat.get("compatible_printers_condition"):
            return 1
        return 0

    processes = {}
    proc_cands = []
    for n, e in idx.items():
        if e["type"] != "process":
            continue
        try:
            f = flatten(n, idx)
        except Exception:
            continue
        c = compatible(n, f)
        if c < 2:
            continue
        lh = _first_float(f.get("layer_height"))
        proc_cands.append((n, lh, e["user"]))
    for q, target in QUALITY_LAYER.items():
        best = sorted(proc_cands, key=lambda x: (abs(x[1] - target), 0 if x[2] else 1, len(x[0])))
        if best and abs(best[0][1] - target) < 0.041:
            processes[q] = best[0][0]
    if "standard" not in processes:
        raise RuntimeError("Kein 0.20-mm-Prozessprofil für den Hi gefunden")

    bad_words = ("CF", "GF", "Silk", "Matte", "Wood", "Marble", "Glow", "Galaxy", "Rainbow", "Support",
                 "Tough", "HS", "HF", "Metal", "Translucent", "Fluo", "Sparkle", "Glitter")
    filaments = {}
    fil_cands: dict[str, list] = {"pla": [], "petg": []}
    for n, e in idx.items():
        if e["type"] != "filament":
            continue
        try:
            f = flatten(n, idx)
        except Exception:
            continue
        ftype = str(_as_list(f.get("filament_type"))[0] if f.get("filament_type") else "").upper()
        key = {"PLA": "pla", "PETG": "petg"}.get(ftype)
        if not key:
            continue
        c = compatible(n, f)
        if c == 0:
            continue
        score = c * 10
        score += 4 if e["user"] else 0
        score += 3 if "Hyper" in n else 0
        score += 2 if "Generic" in n else 0
        score -= 8 * sum(1 for w in bad_words if re.search(rf"(^|[^A-Za-z]){w}([^A-Za-z]|$)", n))
        fil_cands[key].append((score, n))
    for key, lst in fil_cands.items():
        lst.sort(key=lambda x: (-x[0], len(x[1])))
        if lst:
            filaments[key] = lst[0][1]
    missing = [k for k in ("pla", "petg") if k not in filaments]
    if missing:
        raise RuntimeError(f"Kein passendes Filamentprofil für: {', '.join(missing)}")
    return {
        "machine": machine,
        "process": processes,
        "filament": filaments,
        "alternatives": {
            "machine": machines[:5],
            "process": sorted({c[0] for c in proc_cands})[:15],
            "pla": [n for _, n in fil_cands["pla"][:8]],
            "petg": [n for _, n in fil_cands["petg"][:8]],
        },
    }




def _dump_profile(data: dict, name: str, kind: str, path: Path) -> str:
    data = dict(data)
    data["from"] = "User"
    data["name"] = name
    if kind != "machine":
        data["compatible_printers"] = []
        data["compatible_printers_condition"] = ""
    if kind == "machine":
        data["printer_settings_id"] = name
    elif kind == "process":
        data["print_settings_id"] = name
    else:
        data["filament_settings_id"] = [name]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_flat_profiles(sel: dict, idx: dict) -> dict:
    """Aufgelöste Profile nach config/slicer/*.json schreiben."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    files = {"machine": _dump_profile(flatten(sel["machine"], idx), sel["machine"], "machine",
                                      PROFILE_DIR / "machine.json")}
    files["process"] = {q: _dump_profile(flatten(n, idx), n, "process", PROFILE_DIR / f"process_{q}.json")
                        for q, n in sel["process"].items()}
    files["filament"] = {m: _dump_profile(flatten(n, idx), n, "filament", PROFILE_DIR / f"filament_{m}.json")
                         for m, n in sel["filament"].items()}
    return files


def _safe_name(n: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", n)[:90]


def write_filament_catalog(sel: dict, idx: dict) -> dict:
    """Alle Filamentprofile, die zum Hi passen, auflösen → config/slicer/filaments/*.json + Katalog."""
    machine = sel["machine"]
    cat = {}
    fdir = PROFILE_DIR / "filaments"
    for n, e in idx.items():
        if e["type"] != "filament":
            continue
        try:
            f = flatten(n, idx)
        except Exception:
            continue
        cp = set(_as_list(f.get("compatible_printers")))
        hi_named = re.search(r"(^|[^A-Za-z])Hi([^A-Za-z]|$)", n) is not None
        if not (machine in cp or hi_named):
            continue
        ftype = str(_as_list(f.get("filament_type"))[0] if f.get("filament_type") else "").upper()
        path = _dump_profile(f, n, "filament", fdir / f"{_safe_name(n)}.json")
        cat[n] = {"file": path, "type": ftype, "filament_id": str(f.get("filament_id") or ""),
                  "vendor": str(_as_list(f.get("filament_vendor"))[0] if f.get("filament_vendor") else ""),
                  "nozzle_temp": _first_float(f.get("nozzle_temperature")), "user": e["user"]}
    return dict(sorted(cat.items()))


def probe_cli(exe: str, kind: str) -> dict:
    """Welche Kommandozeilen-Optionen kennt dieses Programm? (Hilfe-Text auswerten)"""
    pre = ["--cli"] if kind == "crealityprint" else []
    kw = {"creationflags": 0x08000000} if IS_WIN else {}
    try:
        p = subprocess.run([exe, *pre, "--help"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, **kw)
        text = (p.stdout or "") + (p.stderr or "")
    except Exception as exc:
        return {"prefix": pre, "options": [], "help_error": str(exc)}
    opts = sorted({o.replace("_", "-") for o in re.findall(r"--([a-z][a-z0-9_-]+)", text)})
    return {"prefix": pre, "options": opts, "help_head": text[:600]}


def setup_slicer(exe: str | None = None, kind: str | None = None) -> dict:
    if not exe:
        cands = find_slicers()
        if not cands:
            raise RuntimeError("Weder Creality Print noch OrcaSlicer gefunden. Pfad mit --exe angeben.")
        exe, kind = cands[0]["exe"], cands[0]["kind"]
    kind = kind or ("orca" if "orca" in exe.lower() else "crealityprint")
    roots = profile_roots(exe)
    idx = index_profiles(roots)
    sel = select_profiles(idx)
    files = write_flat_profiles(sel, idx)
    catalog = write_filament_catalog(sel, idx)
    cli = probe_cli(exe, kind)
    data = {"kind": kind, "exe": exe, "cli": cli, "machine": sel["machine"], "process": sel["process"],
            "filament": sel["filament"], "files": files, "alternatives": sel["alternatives"],
            "profile_roots": [str(r) for r in roots], "catalog": catalog}
    write_yaml(SLICER_YAML, data, header=(
        "Von 'printlab setup' erzeugt. Profile ändern: Namen unten anpassen und\n"
        "'printlab setup --keep-selection' ausführen (löst die Profile neu auf)."))
    return data


def reflatten_from_yaml() -> dict:
    """Auswahl aus slicer.yaml übernehmen und Profile neu auflösen (nach Handänderung)."""
    s = load_config()["slicer"]
    idx = index_profiles([Path(r) for r in s["profile_roots"]])
    sel = {"machine": s["machine"], "process": s["process"], "filament": s["filament"]}
    s["files"] = write_flat_profiles(sel, idx)
    s["catalog"] = write_filament_catalog(sel, idx)
    s["cli"] = probe_cli(s["exe"], s.get("kind", "crealityprint"))
    write_yaml(SLICER_YAML, s, header="Von 'printlab setup' erzeugt.")
    return s


def filament_catalog(ftype: str | None = None) -> list[dict]:
    cat = (load_config().get("slicer") or {}).get("catalog") or {}
    out = [{"name": n, **{k: v for k, v in e.items() if k != "file"}} for n, e in cat.items()]
    if ftype:
        out = [e for e in out if e["type"] == ftype.upper()]
    return out


def filament_file(name: str, color: str | None = None, workdir: Path | None = None) -> str:
    """Pfad zum aufgelösten Filamentprofil; mit Farbe als eigene Kopie (für G-Code-Kopf und Vorschau)."""
    s = load_config()["slicer"]
    cat = s.get("catalog") or {}
    if name in cat:
        src = Path(cat[name]["file"])
    elif name.lower() in (s.get("files", {}).get("filament") or {}):
        src = Path(s["files"]["filament"][name.lower()])
    else:
        raise KeyError(f"Filamentprofil '{name}' nicht im Katalog. 'printlab filaments' zeigt alle.")
    if not color or workdir is None:
        return str(src)
    data = json.loads(src.read_text(encoding="utf-8"))
    data["filament_colour"] = [color.upper()]
    dst = Path(workdir) / f"fil_{_safe_name(name)}_{color.lstrip('#')}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(dst)


# ---------------------------------------------------------------- Slicen

def _run(cmd: list[str], timeout: int):
    kw = {"creationflags": 0x08000000} if IS_WIN else {}  # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, **kw)


def slice_model(model: str | Path, filament_files: list[str], quality: str = "standard",
                filament_ids: list[int] | None = None, outdir: str | Path | None = None,
                timeout: int = 900) -> dict:
    """Slicen mit der CLI. model: STL (einfarbig, filament_ids = [Werkzeug 1-basiert]) oder 3MF (mehrteilig).
    filament_files: Profile positionsgenau, Position i = Werkzeug T(i-1) = CFS-Platz i."""
    cfg = load_config()
    s = cfg.get("slicer") or {}
    if not s.get("exe"):
        raise RuntimeError("Slicer nicht eingerichtet – zuerst 'printlab setup' ausführen")
    quality = quality.lower()
    files = s["files"]
    if quality not in files["process"]:
        raise ValueError(f"Qualität '{quality}' nicht verfügbar: {', '.join(files['process'])}")
    model = Path(model).resolve()
    outdir = Path(outdir or model.parent / "slice").resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.gcode"):
        old.unlink()
    (outdir / "result.json").unlink(missing_ok=True)

    kind = s.get("kind", "crealityprint")
    cli = s.get("cli") or {}
    prefix = cli.get("prefix", ["--cli"] if kind == "crealityprint" else [])

    args: list[tuple[str, str | None]] = [
        ("slice", "0"),
        ("load-settings", f"{files['machine']};{files['process'][quality]}"),
        ("load-filaments", ";".join(filament_files)),
    ]
    if filament_ids and model.suffix.lower() != ".3mf":
        args.append(("load-filament-ids", ",".join(str(i) for i in filament_ids)))
    if kind == "crealityprint":
        args.append(("need-gcode-file", None))
    else:
        args += [("arrange", "0"), ("orient", "0")]
    args.append(("outputdir", str(outdir)))
    # Unbekannte Optionen werden unten anhand der Fehlermeldung erkannt und weggelassen;
    # der Hilfe-Text (cli.options) dient nur der Diagnose.

    dropped = []
    t0 = time.time()
    for _attempt in range(4):
        cmd = [s["exe"], *prefix]
        for k, v in args:
            cmd.append(f"--{k}")
            if v is not None:
                cmd.append(v)
        cmd.append(str(model))
        try:
            proc = _run(cmd, timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Slicer nach {timeout} s abgebrochen")
        bad = re.findall(r"(?:Invalid|Unknown|unrecognized) option:?\s*--?([A-Za-z0-9_-]+)",
                         (proc.stderr or "") + (proc.stdout or ""), re.I)
        bad = [b.replace("_", "-") for b in bad]
        removable = [b for b in bad if b not in ("slice", "load-settings", "load-filaments", "outputdir")
                     and any(k == b for k, _ in args)]
        if removable and not list(outdir.glob("*.gcode")):
            args = [a for a in args if a[0] not in removable]
            dropped += removable
            continue
        break
    secs = round(time.time() - t0, 1)
    gcodes = sorted(outdir.glob("*.gcode"), key=lambda p: p.stat().st_mtime)
    result_json = {}
    if (outdir / "result.json").exists():
        try:
            result_json = json.loads((outdir / "result.json").read_text(encoding="utf-8"))
        except Exception:
            pass
    if not gcodes:
        hint = ""
        if secs < 3 and not bad:
            hint = (" Der Slicer hat sofort beendet. Falls Creality Print offen ist: schliessen und erneut "
                    "versuchen.")
        raise RuntimeError(
            f"Kein G-Code erzeugt (Exit {proc.returncode}, {secs} s).{hint}\n"
            f"result.json: {json.dumps(result_json, ensure_ascii=False)[:800]}\n"
            f"stdout: {proc.stdout[-1500:]}\nstderr: {proc.stderr[-1500:]}\n"
            f"Befehl: {subprocess.list2cmdline(cmd)}")
    return {"gcode": str(gcodes[-1]), "seconds": secs, "returncode": proc.returncode,
            "command": subprocess.list2cmdline(cmd), "dropped_options": dropped}


def slice_stl(stl: str | Path, material: str, quality: str = "standard", outdir: str | Path | None = None,
              timeout: int = 900) -> dict:
    """Einfacher Fall (Setup-Test): ein STL mit dem Standardprofil des Materials auf Werkzeug 1."""
    files = load_config()["slicer"]["files"]
    if material.lower() not in files["filament"]:
        raise ValueError(f"Material '{material}' nicht verfügbar: {', '.join(files['filament'])}")
    return slice_model(stl, [files["filament"][material.lower()]], quality, filament_ids=[1], outdir=outdir,
                       timeout=timeout)


# ---------------------------------------------------------------- G-Code auswerten

_PATTERNS = {
    "time": [r"total estimated time:\s*([^;\n]+)", r";\s*estimated printing time \(normal mode\)\s*=\s*(.+)",
             r";\s*Print time:\s*(.+)"],
    "time_s": [r";TIME:(\d+)"],
    "filament_g": [r";\s*total filament used \[g\]\s*=\s*([\d.]+)", r";\s*filament used \[g\]\s*=\s*([\d.,;\s]+)",
                   r";\s*Filament weight:\s*([\d.]+)"],
    "filament_mm": [r";\s*filament used \[mm\]\s*=\s*([\d.,;\s]+)",
                    r";\s*total filament length \[mm\]\s*:\s*([\d.,;\s]+)"],
    "layers": [r";\s*total layers? count\s*=\s*(\d+)", r";\s*total layer number:\s*(\d+)", r";LAYER_COUNT:(\d+)"],
    "printer": [r";\s*printer_settings_id\s*=\s*(.+)", r";\s*printer_model\s*=\s*(.+)"],
    "layer_height": [r";\s*layer_height\s*=\s*([\d.]+)"],
    "filament_type": [r";\s*filament_type\s*=\s*(.+)"],
    "filament_settings": [r";\s*filament_settings_id\s*=\s*(.+)"],
    "filament_colour": [r";\s*filament_colour\s*=\s*(.+)"],
}
_TEMP = re.compile(r"^(M104|M109|M140|M190)\b[^;]*?\bS(\d+(?:\.\d+)?)", re.I)
_MACRO = re.compile(r"\b(EXTRUDER_TEMP|BED_TEMP|NOZZLE_TEMP|HOTEND_TEMP)\s*=\s*(\d+(?:\.\d+)?)", re.I)
_TOOL = re.compile(r"^T(\d+)\s*(?:;.*)?$")


def _sum_numbers(s: str) -> float:
    return sum(float(x) for x in re.findall(r"\d+(?:\.\d+)?", s))


def _split_list(s: str) -> list[str]:
    return [x.strip().strip('"') for x in re.split(r"[;,]", s) if x.strip()]


def gcode_info(path: str | Path) -> dict:
    info: dict = {}
    max_nozzle = 0.0
    max_bed = 0.0
    tools: list[int] = []
    changes = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(";"):
                for key, pats in _PATTERNS.items():
                    if key in info:
                        continue
                    for pat in pats:
                        m = re.search(pat, line)
                        if m:
                            info[key] = m.group(1).strip()
                            break
                continue
            mt = _TOOL.match(line)
            if mt:
                t = int(mt.group(1))
                if not tools or tools[-1] != t:
                    changes += 1 if tools else 0
                    tools.append(t)
                continue
            m = _TEMP.match(line)
            if m:
                v = float(m.group(2))
                if m.group(1).upper() in ("M104", "M109"):
                    max_nozzle = max(max_nozzle, v)
                else:
                    max_bed = max(max_bed, v)
            for mm in _MACRO.finditer(line):
                v = float(mm.group(2))
                if mm.group(1).upper() == "BED_TEMP":
                    max_bed = max(max_bed, v)
                else:
                    max_nozzle = max(max_nozzle, v)
    grams = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", info.get("filament_g", ""))]
    out = {
        "time": info.get("time") or (_fmt_secs(int(info["time_s"])) if "time_s" in info else None),
        "filament_g": round(_sum_numbers(info["filament_g"]), 1) if "filament_g" in info else None,
        "filament_g_per_tool": [round(g, 1) for g in grams] if len(grams) > 1 else None,
        "filament_m": round(_sum_numbers(info["filament_mm"]) / 1000, 2) if "filament_mm" in info else None,
        "layers": int(info["layers"]) if "layers" in info else None,
        "layer_height": float(info["layer_height"]) if "layer_height" in info else None,
        "printer": info.get("printer", "").strip('"'),
        "filament_types": _split_list(info.get("filament_type", "")),
        "filament_profiles": _split_list(info.get("filament_settings", "")),
        "tools_used": sorted(set(tools)),
        "tool_changes": changes,
        "max_nozzle_temp": max_nozzle,
        "max_bed_temp": max_bed,
        "size_kb": round(Path(path).stat().st_size / 1024),
    }
    return out


def _fmt_secs(s: int) -> str:
    h, r = divmod(s, 3600)
    return f"{h}h {r // 60}m" if h else f"{r // 60}m {r % 60}s"


def gcode_safety(info: dict, cfg: dict, material: str | None = None,
                 expected_tools: dict[int, str] | None = None) -> list[str]:
    """Gründe, warum dieser G-Code NICHT gestartet werden darf (leer = ok).
    expected_tools: {Werkzeug-Nr (0-basiert): Materialtyp} – genau diese müssen verwendet werden."""
    lim = cfg["limits"]
    problems = []
    if info["max_nozzle_temp"] > lim["max_nozzle_temp"]:
        problems.append(f"Düsentemperatur {info['max_nozzle_temp']:.0f} °C > Grenze {lim['max_nozzle_temp']} °C")
    if info["max_bed_temp"] > lim["max_bed_temp"]:
        problems.append(f"Betttemperatur {info['max_bed_temp']:.0f} °C > Grenze {lim['max_bed_temp']} °C")
    if info["max_nozzle_temp"] == 0:
        problems.append("Keine Düsentemperatur im G-Code gefunden")
    if info.get("printer") and not re.search(r"(^|[^A-Za-z])Hi([^A-Za-z]|$)", info["printer"]):
        problems.append(f"G-Code ist für '{info['printer']}', nicht für den Hi")
    types = info.get("filament_types") or []
    if expected_tools is not None:
        used = set(info.get("tools_used") or [])
        if used and used != set(expected_tools):
            problems.append(
                "G-Code verwendet Werkzeuge " + ", ".join(f"T{t}" for t in sorted(used)) + ", erwartet "
                + ", ".join(f"T{t}" for t in sorted(expected_tools))
                + " – Filamentzuordnung wurde vom Slicer nicht übernommen")
        for t, typ in expected_tools.items():
            if t < len(types) and typ and types[t].upper() != typ.upper():
                problems.append(f"Werkzeug T{t}: Profil ist {types[t]}, Spule ist {typ}")
    elif material and types:
        if material.upper() not in types[0].upper():
            problems.append(f"Filament im G-Code ist {types[0]}, gewünscht {material.upper()}")
    return problems
