"""Konfiguration: config/printer.yaml (von Hand), config/calibration.yaml und config/slicer.yaml (generiert)."""

from __future__ import annotations

import copy
from pathlib import Path
import threading
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
PROJECTS_DIR = ROOT / "projects"
STATE_DIR = ROOT / ".slicetheprompt"

PRINTER_YAML = CONFIG_DIR / "printer.yaml"
CALIBRATION_YAML = CONFIG_DIR / "calibration.yaml"
SLICER_YAML = CONFIG_DIR / "slicer.yaml"

_YAML_CACHE_LOCK = threading.Lock()
_YAML_CACHE: dict[str, tuple[int, int, dict]] = {}


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        st = path.stat()
        key = str(path.resolve())
        with _YAML_CACHE_LOCK:
            hit = _YAML_CACHE.get(key)
            if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return copy.deepcopy(hit[2])
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        with _YAML_CACHE_LOCK:
            if len(_YAML_CACHE) > 128:
                _YAML_CACHE.clear()
            _YAML_CACHE[key] = (st.st_mtime_ns, st.st_size, copy.deepcopy(data))
        return data
    except OSError:
        return {}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    """printer.yaml, überlagert von calibration.yaml; slicer.yaml unter 'slicer'."""
    cfg = _read_yaml(PRINTER_YAML)
    cal = _read_yaml(CALIBRATION_YAML)
    if cal.get("materials"):
        cfg = _deep_merge(cfg, {"materials": cal["materials"]})
    cfg["slicer"] = _read_yaml(SLICER_YAML)
    return cfg


def material_cfg(cfg: dict, material: str | None) -> dict:
    mat = (material or cfg.get("default_material", "pla")).lower()
    mats = cfg.get("materials", {})
    if mat not in mats:
        raise ValueError(f"Unbekanntes Material '{mat}'. Erlaubt: {', '.join(mats)}")
    return {"name": mat, **mats[mat]}


def write_yaml(path: Path, data: dict, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if header:
            for line in header.strip().splitlines():
                f.write(f"# {line}\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    with _YAML_CACHE_LOCK:
        _YAML_CACHE.pop(str(path.resolve()), None)


def project_dir(name_or_path: str) -> Path:
    """Projektname (Ordner unter projects/) oder Pfad."""
    p = Path(name_or_path)
    if p.is_absolute() and p.exists():
        return p
    cand = PROJECTS_DIR / name_or_path
    if cand.exists():
        return cand
    # Teilübereinstimmung, z. B. ohne Datumspräfix
    matches = sorted(d for d in PROJECTS_DIR.glob(f"*{name_or_path}*") if d.is_dir())
    if len(matches) == 1:
        return matches[0]
    # exakter Name ohne Datumspräfix gewinnt (z. B. 'box' vor 'box-schablone')
    exact = [m for m in matches if m.name[11:] == name_or_path or m.name == name_or_path]
    if len(exact) == 1:
        return exact[0]
    if len(matches) > 1:
        raise FileNotFoundError(
            f"Projekt '{name_or_path}' ist mehrdeutig: {', '.join(m.name for m in matches)}"
        )
    raise FileNotFoundError(f"Projekt '{name_or_path}' nicht gefunden unter {PROJECTS_DIR}")
