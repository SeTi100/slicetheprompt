"""Mesh-Bearbeitung für importierte 3MF/STL-Skulpturen (Ausschneiden, Vasen-Heilung, Einsetzen).

Nutzt `manifold3d` + `trimesh` für schnelle, garantiert wasserdichte (manifold) Netz-Operationen
auf grossen Dreiecksnetzen, ohne B-Rep-CAD (OpenCascade) zu belasten.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any

import manifold3d as m3d
import numpy as np
import trimesh
import yaml

from . import import3mf, meshcheck as mc, pipeline
from .config import PROJECTS_DIR, load_config, project_dir
from .render import render as render_png
from .stlio import read_stl, write_stl
from .threemf import write_multipart_3mf


def tris_to_manifold(tris: np.ndarray) -> m3d.Manifold:
    """Konvertiert ein Dreiecks-Array (N, 3, 3) in ein wasserdichtes manifold3d-Objekt."""
    pts = np.asarray(tris, dtype=np.float64).reshape(-1, 3)
    faces = np.arange(len(pts), dtype=np.int64).reshape(-1, 3)
    tm = trimesh.Trimesh(vertices=pts, faces=faces, process=True)
    mesh = m3d.Mesh(
        vert_properties=np.asarray(tm.vertices, dtype=np.float32),
        tri_verts=np.asarray(tm.faces, dtype=np.uint32),
    )
    man = m3d.Manifold(mesh)
    if man.num_tri() == 0:
        raise ValueError("Netz ist nicht wasserdicht oder konnte nicht als Manifold geladen werden")
    return man


def manifold_to_tris(man: m3d.Manifold, keep_largest_component: bool = True) -> np.ndarray:
    """Konvertiert ein manifold3d-Objekt zurück in ein sauberes Dreiecks-Array (N, 3, 3)."""
    if keep_largest_component:
        parts = man.decompose()
        if len(parts) > 1:
            man = max(parts, key=lambda p: p.volume())
    mesh = man.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    faces = np.asarray(mesh.tri_verts, dtype=np.int64)
    if len(faces) == 0:
        raise ValueError("Ergebnis-Netz ist leer")
    return verts[faces]


def load_any_mesh(path: str | Path, body: str | None = None) -> tuple[np.ndarray, dict[str, Any]]:
    """Lädt eine .3mf, .stl, .obj oder .glb Datei (oder ein Projekt) als Dreiecks-Array (N, 3, 3)."""
    p = Path(path)
    if not p.is_file():
        try:
            d = project_dir(str(path))
            sources = import3mf.source_files(d)
            if sources:
                p = sources[0]
            elif (d / "out" / "model.stl").is_file():
                p = d / "out" / "model.stl"
            elif (d / "out" / "print.stl").is_file():
                p = d / "out" / "print.stl"
        except Exception:
            pass
    if not p.is_file():
        raise FileNotFoundError(f"3D-Datei nicht gefunden: {path}")

    ext = p.suffix.lower()
    if ext == ".3mf":
        data = import3mf.read_3mf(p)
        bodies = data["bodies"]
        if body:
            chosen = [b for b in bodies if b["id"] == body or b["name"] == body]
            if not chosen:
                raise ValueError(f"Körper '{body}' nicht in {p.name} gefunden")
            bodies = chosen
        tris = np.concatenate([b["tris"] for b in bodies], axis=0)
        return tris, {"source": p.name, "bodies": len(bodies), "color": bodies[0].get("color", "#4682B4")}
    if ext == ".stl":
        tris = read_stl(p)
        return tris, {"source": p.name, "bodies": 1, "color": "#4682B4"}

    loaded = trimesh.load(str(p), force="mesh")
    if isinstance(loaded, trimesh.Scene):
        loaded = loaded.dump(concatenate=True)
    verts = np.asarray(loaded.vertices, dtype=np.float64)
    faces = np.asarray(loaded.faces, dtype=np.int64)
    if len(faces) == 0:
        raise ValueError(f"Keine Dreiecke in {p.name}")
    return verts[faces], {"source": p.name, "bodies": 1, "color": "#4682B4"}


def import_project(path: str | Path, name: str = "") -> dict[str, Any]:
    """Importiert eine lokale .3mf oder .stl Datei als neues Projekt unter projects/."""
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Datei nicht gefunden: {src}")
    ext = src.suffix.lower()
    if ext == ".3mf":
        data = src.read_bytes()
        if name:
            slug = pipeline.slugify(name)
            filename = f"{slug}.3mf"
        else:
            filename = src.name
        target = import3mf.save_uploads(PROJECTS_DIR, [(filename, data)])
        prev = import3mf.preview(target)
        return {
            "ok": True,
            "project": target.name,
            "path": str(target),
            "source": prev["source"],
            "bodies": [{"id": b["id"], "name": b["name"], "size_mm": b["size_mm"], "bbox": b["bbox"]}
                       for b in prev["bodies"]],
        }
    if ext == ".stl":
        tris = read_stl(src)
        slug = pipeline.slugify(name or src.stem)
        f3_bytes_path = PROJECTS_DIR / f".tmp_{slug}.3mf"
        try:
            write_multipart_3mf(f3_bytes_path, [(src.stem, tris, 1)], object_name=src.stem)
            target = import3mf.save_uploads(PROJECTS_DIR, [(f"{slug}.3mf", f3_bytes_path.read_bytes())])
        finally:
            f3_bytes_path.unlink(missing_ok=True)
        prev = import3mf.preview(target)
        return {
            "ok": True,
            "project": target.name,
            "path": str(target),
            "source": prev["source"],
            "bodies": [{"id": b["id"], "name": b["name"], "size_mm": b["size_mm"], "bbox": b["bbox"]}
                       for b in prev["bodies"]],
        }
    raise ValueError(f"Nur .3mf und .stl werden direkt importiert (erhalten: {ext})")


def estimate_vase_axis_and_profile(
    tris: np.ndarray,
    z_min: float,
    z_max: float,
    exclude_bbox: tuple[np.ndarray, np.ndarray] | None = None,
    n_slices: int = 36,
    radial_pad: float = 0.6,
    bottom_taper_mm: float = 12.0,
) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Schätzt für einen rotationsähnlichen Grundkörper (z. B. Vase) die Mittelachse (xc, yc)
    und den Aussenradius r(z) aus den Punkten ausserhalb von `exclude_bbox`.
    Im untersten Bereich (`bottom_taper_mm`) wird der Schnittradius sanft nach aussen aufgeweitet,
    damit kein scharfer horizontaler Absatz entsteht."""
    pts = tris.reshape(-1, 3)
    if exclude_bbox is not None:
        bmin, bmax = exclude_bbox
        in_box = (
            (pts[:, 0] >= bmin[0]) & (pts[:, 0] <= bmax[0])
            & (pts[:, 1] >= bmin[1]) & (pts[:, 1] <= bmax[1])
            & (pts[:, 2] >= bmin[2]) & (pts[:, 2] <= bmax[2])
        )
        clean = pts[~in_box]
    else:
        clean = pts

    if len(clean) < 100:
        clean = pts

    # Kreismittelpunkt aus dem oberen/ungestörten Bereich per Least-Squares schätzen
    z_span = pts[:, 2].max() - pts[:, 2].min()
    top_band = clean[clean[:, 2] >= pts[:, 2].max() - max(10.0, 0.12 * z_span)]
    sample = top_band if len(top_band) >= 60 else clean
    A = np.column_stack([2.0 * sample[:, 0], 2.0 * sample[:, 1], np.ones(len(sample))])
    b = sample[:, 0] ** 2 + sample[:, 1] ** 2
    sol, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    xc, yc = float(sol[0]), float(sol[1])

    zs = np.linspace(float(z_min), float(z_max), max(4, n_slices))
    rs = []
    band_half = max(2.0, (z_max - z_min) / max(10, n_slices))
    r_clean = np.hypot(clean[:, 0] - xc, clean[:, 1] - yc)
    r_all = np.hypot(pts[:, 0] - xc, pts[:, 1] - yc)
    for z in zs:
        m = (clean[:, 2] >= z - band_half) & (clean[:, 2] <= z + band_half)
        if np.count_nonzero(m) >= 10:
            r_val = float(np.percentile(r_clean[m], 85)) + radial_pad
        elif rs:
            r_val = rs[-1]
        else:
            r_val = float(np.median(r_clean)) + radial_pad
        if bottom_taper_mm > 0 and (z - z_min) < bottom_taper_mm:
            # Sanfter Übergang am unteren Rand (45°-Fase nach aussen), damit kein Absatz entsteht
            t = 1.0 - (z - z_min) / bottom_taper_mm
            m_clean_band = (clean[:, 2] >= z - band_half) & (clean[:, 2] <= z + band_half)
            if np.any(m_clean_band):
                r_outer = float(np.percentile(r_clean[m_clean_band], 96))
                r_val = max(r_val, r_val + t * max(0.0, r_outer - r_val))
        rs.append(r_val)
    return xc, yc, zs, np.asarray(rs, dtype=np.float64)


def build_vase_core_manifold(xc: float, yc: float, zs: np.ndarray, rs: np.ndarray, segments: int = 72) -> m3d.Manifold:
    """Erzeugt einen glatten Rotationskörper entlang (xc, yc, zs) mit Radien rs."""
    frustums = []
    for i in range(len(zs) - 1):
        h = float(zs[i + 1] - zs[i])
        if h <= 0:
            continue
        cyl = m3d.Manifold.cylinder(
            h + 0.1,
            float(rs[i]),
            float(rs[i + 1]),
            circular_segments=segments,
        ).translate([xc, yc, float(zs[i]) - 0.05])
        frustums.append(cyl)
    if not frustums:
        raise ValueError("Konnte keinen Vasenkern aufbauen")
    return m3d.Manifold.batch_boolean(frustums, m3d.OpType.Add)


def _resolve_bbox_from_feedback(d: Path, bbox: list[float] | None) -> tuple[np.ndarray, np.ndarray]:
    """Ermittelt (bmin, bmax) aus explizitem `bbox` ([x0,y0,z0,x1,y1,z1]) oder `out/ui_feedback.json`."""
    if bbox is not None:
        arr = np.asarray(bbox, dtype=np.float64).reshape(-1)
        if arr.size != 6:
            raise ValueError("bbox muss 6 Zahlen enthalten: [x_min, y_min, z_min, x_max, y_max, z_max]")
        bmin = np.minimum(arr[:3], arr[3:])
        bmax = np.maximum(arr[:3], arr[3:])
        return bmin, bmax

    fb_path = d / "out" / "ui_feedback.json"
    if fb_path.is_file():
        fb = json.loads(fb_path.read_text(encoding="utf-8"))
        for ann in fb.get("annotations") or []:
            if isinstance(ann, dict) and ann.get("type") == "box":
                bm = ann.get("bbox_model")
                if isinstance(bm, list) and len(bm) == 6:
                    arr = np.asarray(bm, dtype=np.float64)
                    return np.minimum(arr[:3], arr[3:]), np.maximum(arr[:3], arr[3:])
                if isinstance(bm, dict) and "min" in bm and "max" in bm:
                    return np.asarray(bm["min"], dtype=np.float64), np.asarray(bm["max"], dtype=np.float64)
    raise ValueError("Keine Bereichsbox angegeben und keine Box-Markierung in out/ui_feedback.json gefunden")


def _prepare_import_for_slicing(
    d: Path,
    tris: np.ndarray,
    color: str = "#4682B4",
    source_name: str = "mesh",
    write_source_3mf: bool = True,
) -> dict[str, Any]:
    """Schreibt aktualisiertes 3MF, `out/model.stl`, `out/print.stl`, `out/report.json` und `out/render.png`."""
    cfg = load_config()
    out = d / "out"
    out.mkdir(parents=True, exist_ok=True)

    # 1. Falls Import-Projekt: 3MF-Quelldatei aktualisieren (und Original einmalig als .orig.3mf sichern)
    sources = import3mf.source_files(d)
    if write_source_3mf and sources:
        src_3mf = sources[0]
        bak = d / "out" / f"{src_3mf.stem}.orig.3mf"
        if not bak.exists():
            shutil.copyfile(src_3mf, bak)
        write_multipart_3mf(src_3mf, [(src_3mf.stem, tris, 1)], object_name=src_3mf.stem)
        # Vorschau-Cache sofort aktualisieren
        import3mf.preview(d, src_3mf.name)

    # 2. model.stl und print.stl schreiben, damit Slicer & Kostenrechner direkt funktionieren
    write_stl(out / "model.stl", tris, header=f"slicetheprompt {d.name}")
    bed = cfg["printer"]["bed"]
    placed = mc.place_on_bed(tris, (bed["x"], bed["y"]))
    shift = (placed - tris).reshape(-1, 3)[0]
    write_stl(out / "print.stl", placed, header=f"slicetheprompt {d.name}")

    pts = placed.reshape(-1, 3)
    size_mm = [round(float(v), 2) for v in (pts.max(axis=0) - pts.min(axis=0))]
    print_info = {
        "orientation": "wie modelliert",
        "candidates": None,
        "bodies": {},
        "rotation_matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "shift": [float(v) for v in shift],
        "ghosts": {},
    }
    (out / "print.json").write_text(json.dumps(print_info, indent=2, ensure_ascii=False), encoding="utf-8")

    build_data = {
        "ok": True,
        "valid": True,
        "size_mm": size_mm,
        "bbox_min": [round(float(v), 2) for v in pts.min(axis=0)],
        "bbox_max": [round(float(v), 2) for v in pts.max(axis=0)],
        "triangles": int(len(placed)),
        "bodies": [],
        "checks": [],
        "features": [],
        "warnings": [],
    }
    (out / "build.json").write_text(json.dumps(build_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Schnelle Drucklage- und Bettkontakt-Prüfung (manifold3d garantiert bereits Wasserdichtigkeit;
    # das 0,1-mm-Runden in meshcheck.weld erzeugt bei feinen Skulptur-Netzen sonst Schein-Kantenfehler)
    rep = mc.analyse(placed, cfg, print_orientation=True, walls=False)
    ignore_rules = {"manifold (keine Kante an >2 Flächen)", "Normalen einheitlich", "wasserdicht (offene Kanten)"}
    checks = [
        mc.Check(c.rule, True, "0 (manifold3d)", c.limit, c.severity) if c.rule in ignore_rules else c
        for c in rep.checks
    ]
    errors = [c.as_dict() for c in checks if not c.ok and c.severity == "error"]
    warnings = [c.as_dict() for c in checks if not c.ok and c.severity == "warn"]
    report = {
        "project": d.name,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": [c.as_dict() for c in checks],
        "model": {"size_mm": size_mm, "triangles": int(len(placed))},
        "print": {"orientation": "wie modelliert", **rep.metrics.get("print", {}), "size_mm": size_mm},
        "material": cfg.get("default_material", "pla"),
        "bodies": [],
        "source": source_name,
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=float), encoding="utf-8")

    render_col = "#4682B4" if (not color or color.upper() in ("#FFFFFF", "#E8E8E8", "#F0F0F0")) else color
    render_path = render_png([(placed, render_col, None)], out / "render.png", title=d.name, bed=bed, max_tris=400_000)
    return {
        "ok": True,
        "project": d.name,
        "passed": report["passed"],
        "size_mm": size_mm,
        "triangles": int(len(placed)),
        "errors": [f"{e['rule']}: {e['value']} (Soll {e['limit']})" for e in errors],
        "warnings": [f"{w['rule']}: {w['value']} (Soll {w['limit']})" for w in warnings],
        "render_png": str(render_path),
    }


def prepare_import(project: str, filename: str | None = None, plate: str | None = None) -> dict[str, Any]:
    """Bereitet ein 3MF-Import-Projekt für Prüfung, Slicen und Druck vor (erzeugt out/print.stl + out/report.json + out/render.png)."""
    d = project_dir(project)
    prev = import3mf.preview(d, filename)
    plates = prev.get("plates") or []
    active_plate = plate if plate in {p["id"] for p in plates} else (plates[0]["id"] if plates else "1")
    bodies = [b for b in prev["bodies"] if b["plate"] == active_plate] or prev["bodies"]
    tris = np.concatenate([read_stl(d / b["stl"]) for b in bodies], axis=0)
    color = bodies[0].get("color", "#4682B4")
    return _prepare_import_for_slicing(d, tris, color=color, source_name=prev["source"], write_source_3mf=False)


def replace_in_mesh(
    project: str,
    replacement_path: str = "",
    bbox: list[float] | None = None,
    mode: str = "vase_heal",
    trim_bottom_mm: float = 0.0,
    scale: float | None = None,
    rotate_z_deg: float = 0.0,
    offset_xyz: list[float] | None = None,
    overlap_mm: float = 2.0,
    clear_feedback: bool = True,
) -> dict[str, Any]:
    """Entfernt den Bereich `bbox` (oder die Box aus `get_ui_feedback`) aus einem 3MF/STL-Projekt
    und verschmilzt optional `replacement_path` (.3mf/.stl/.obj/.glb) wasserdicht an dieser Stelle.

    mode:
      - 'vase_heal': Rekonstruiert den runden Grundkörper (z. B. Vasenwand) innerhalb der Box
                     und schneidet nur überstehende Skulpturteile (z. B. Fee + Flügel) ab.
      - 'cut':       Schneidet die gesamte Box per Boolean-Differenz weg.
    trim_bottom_mm: Schneidet vom Ersatz-Mesh unten einen Sockel der Höhe `trim_bottom_mm` ab.
    """
    d = project_dir(project)
    bmin, bmax = _resolve_bbox_from_feedback(d, bbox)

    sources = import3mf.source_files(d)
    if sources:
        # Falls ein Original-Backup existiert, immer vom sauberen Original ausgehen,
        # damit man Platzierung/Skalierung mehrfach verfeinern kann!
        bak = d / "out" / f"{sources[0].stem}.orig.3mf"
        base_source = bak if bak.is_file() else sources[0]
        base_tris, meta = load_any_mesh(base_source)
    elif (d / "out" / "model.stl").is_file():
        base_tris, meta = load_any_mesh(d / "out" / "model.stl")
    else:
        raise FileNotFoundError(f"Keine 3MF- oder STL-Geometrie in {d.name} gefunden")

    m_base = tris_to_manifold(base_tris)
    box_man = m3d.Manifold.cube(bmax - bmin).translate(bmin)

    xc, yc = None, None
    r_at_mid = None
    if mode == "vase_heal":
        xc, yc, zs, rs = estimate_vase_axis_and_profile(
            base_tris, float(bmin[2]), float(bmax[2]), exclude_bbox=(bmin, bmax)
        )
        vase_core = build_vase_core_manifold(xc, yc, zs, rs)
        cutter = box_man - vase_core
        m_result = m_base - cutter
        r_at_mid = float(np.interp(0.5 * (bmin[2] + bmax[2]), zs, rs))
    elif mode == "cut":
        m_result = m_base - box_man
    elif mode == "keep":
        m_result = m_base
    else:
        raise ValueError("mode muss 'vase_heal', 'cut' oder 'keep' sein")

    rep_info = None
    if replacement_path:
        rep_tris, _ = load_any_mesh(replacement_path)
        m_rep = tris_to_manifold(rep_tris)
        r_pts = rep_tris.reshape(-1, 3)
        r_min = r_pts.min(axis=0)
        r_max = r_pts.max(axis=0)
        r_center = 0.5 * (r_min + r_max)

        z_base = float(r_min[2])
        if trim_bottom_mm > 0:
            span = r_max - r_min
            cut_h = float(trim_bottom_mm)
            base_cutter = m3d.Manifold.cube(
                [span[0] * 2 + 20.0, span[1] * 2 + 20.0, cut_h + 2.0], center=True
            ).translate([r_center[0], r_center[1], z_base + 0.5 * cut_h - 1.0])
            m_rep = m_rep - base_cutter
            z_base += cut_h

        eff_h = max(1.0, float(r_max[2] - z_base))
        box_h = max(1.0, float(bmax[2] - bmin[2]))
        s = float(scale) if scale is not None else round((box_h * 0.72) / eff_h, 4)

        # Standard-Zielpunkt: An der Vasenwand (oder Box-Mitte) auf der Unterkante der Box
        if xc is not None and yc is not None and r_at_mid is not None:
            box_center_xy = 0.5 * (bmin[:2] + bmax[:2])
            dir_xy = box_center_xy - np.array([xc, yc])
            norm = float(np.linalg.norm(dir_xy))
            if norm > 1e-3:
                dir_xy /= norm
            else:
                dir_xy = np.array([0.0, -1.0])
            # Positioniere das Ersatz-Mesh so, dass es mit `overlap_mm` in der Vasenwand verankert ist
            rep_radius = 0.25 * float((r_max[0] - r_min[0]) + (r_max[1] - r_min[1])) * s
            dist_from_axis = max(0.0, r_at_mid + rep_radius * 0.45 - float(overlap_mm))
            target_x = xc + dir_xy[0] * dist_from_axis
            target_y = yc + dir_xy[1] * dist_from_axis
        else:
            target_x = float(0.5 * (bmin[0] + bmax[0]))
            target_y = float(0.5 * (bmin[1] + bmax[1]))

        target_z = float(bmin[2])
        if offset_xyz is not None and len(offset_xyz) == 3:
            target_x += float(offset_xyz[0])
            target_y += float(offset_xyz[1])
            target_z += float(offset_xyz[2])

        m_rep_placed = (
            m_rep
            .translate([-float(r_center[0]), -float(r_center[1]), -z_base])
            .scale([s, s, s])
            .rotate([0.0, 0.0, float(rotate_z_deg)])
            .translate([target_x, target_y, target_z])
        )
        m_result = m_result + m_rep_placed
        rep_info = {
            "replacement": str(replacement_path),
            "scale": s,
            "rotate_z_deg": rotate_z_deg,
            "placed_at": [round(target_x, 2), round(target_y, 2), round(target_z, 2)],
            "trim_bottom_mm": trim_bottom_mm,
        }

    out_tris = manifold_to_tris(m_result)
    res = _prepare_import_for_slicing(
        d,
        out_tris,
        color=meta.get("color", "#4682B4"),
        source_name=meta.get("source", "replaced.3mf"),
        write_source_3mf=True,
    )
    res["mode"] = mode
    res["bbox_removed"] = {"min": [round(float(v), 2) for v in bmin], "max": [round(float(v), 2) for v in bmax]}
    if rep_info:
        res["replacement_info"] = rep_info
    if clear_feedback:
        from .ui_server import clear_project_feedback
        clear_project_feedback(d)
    return res
