"""Read-only 3MF preview import. Source archives are never changed or extracted.

CAD projects take precedence over loose archives. Cached STL files are previews,
not checked print inputs; neither a model.py nor a fictitious STEP is generated.
"""
from __future__ import annotations

import hashlib
import datetime as dt
import io
import json
from pathlib import Path
import posixpath
import re
import threading
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from .stlio import write_stl

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
NS = {"m": CORE}
UNITS = {"micron": .001, "millimeter": 1., "centimeter": 10., "inch": 25.4,
         "foot": 304.8, "meter": 1000.}
CACHE_VERSION = 1
MAX_MEMBER = 128 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_TRIANGLES = 2_000_000
_LOCK = threading.Lock()


def source_files(d: Path) -> list[Path]:
    root = d.resolve()
    return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".3mf"
                   and p.resolve().is_relative_to(root)), key=lambda p: p.name.casefold())


def project_kind(d: Path) -> str | None:
    if not d.is_dir():
        return None
    if (d / "model.py").is_file():
        return "cad"
    if (d / "assembly.py").is_file():
        return "assembly"
    return "import" if source_files(d) else None


def _archive_path(value: str, owner: str = "") -> str:
    value = urllib.parse.unquote(value)
    if not value or "\\" in value or ":" in value or "?" in value or "#" in value:
        raise ValueError("Ungültiger interner 3MF-Pfad")
    path = posixpath.normpath(value.lstrip("/") if value.startswith("/")
                              else posixpath.join(posixpath.dirname(owner), value))
    if path == ".." or path.startswith("../"):
        raise ValueError("3MF-Verweis verlässt das Archiv")
    return path


def _matrix(value: str | None) -> np.ndarray:
    result = np.eye(4)
    if value:
        numbers = np.array([float(v) for v in value.split()])
        if numbers.size != 12 or not np.isfinite(numbers).all():
            raise ValueError("Ungültige 3MF-Platzierung (12 endliche Zahlen erwartet)")
        # 3MF uses row vectors: first 9 values = 3x3, last 3 = translation.
        result[:3, :3] = numbers[:9].reshape(3, 3).T
        result[:3, 3] = numbers[9:]
    return result


def _color(value: str | None) -> str | None:
    return value[:7].upper() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value) else None


class Reader:
    def __init__(self, archive: zipfile.ZipFile):
        self.archive = archive
        infos = archive.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("3MF-Archiv ist zu gross für die Vorschau")
        if any(i.file_size > MAX_MEMBER for i in infos):
            raise ValueError("Eine 3MF-Datei im Archiv ist zu gross für die Vorschau")
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError("3MF enthält doppelte Archivpfade")
        self.models: dict = {}
        self.warnings: set[str] = set()
        self.triangles = 0
        self.bodies: list[dict] = []
        self.settings: dict = {}
        self.filaments: list[str] = []
        if "Metadata/model_settings.config" in archive.namelist():
            self.settings = self.xml("Metadata/model_settings.config")
        if "Metadata/project_settings.config" in archive.namelist():
            try:
                settings = json.loads(archive.read("Metadata/project_settings.config"))
                colors = settings.get("filament_colour") or settings.get("filament_color") or []
                self.filaments = colors.split(";") if isinstance(colors, str) else colors
            except (ValueError, AttributeError, TypeError):
                self.warnings.add("Filamentfarben aus den Slicer-Einstellungen konnten nicht gelesen werden.")

    def xml(self, path: str) -> ET.Element:
        try:
            raw = self.archive.read(path)
        except KeyError:
            raise ValueError(f"3MF-Verweis fehlt im Archiv: {path}") from None
        # Also recognise UTF-16/32 declarations; no DTD/entities are needed by 3MF.
        declarations = raw.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
            raise ValueError("XML-DTD/Entities werden beim 3MF-Import nicht unterstützt")
        return ET.fromstring(raw)

    def model(self, path: str) -> tuple:
        if path not in self.models:
            root = self.xml(path)
            if root.tag != f"{{{CORE}}}model":
                raise ValueError("Keine unterstützte 3MF-Modelldatei")
            # Prefixes are local to each model, not fixed strings such as 'p'.
            prefixes = dict(v for _, v in ET.iterparse(io.BytesIO(self.archive.read(path)), events=("start-ns",)))
            for prefix in root.get("requiredextensions", "").split():
                if prefixes.get(prefix) not in (CORE, PROD, MATERIAL):
                    raise ValueError(f"Nicht unterstützte erforderliche 3MF-Erweiterung: {prefix}")
            unit = root.get("unit", "millimeter")
            if unit not in UNITS:
                raise ValueError(f"Nicht unterstützte 3MF-Einheit: {unit}")
            resources = root.find("m:resources", NS)
            if resources is None:
                raise ValueError("3MF enthält keine Ressourcen")
            objects = {o.get("id"): o for o in resources.findall("m:object", NS)}
            self.models[path] = (root, objects, UNITS[unit], resources)
        return self.models[path]

    @staticmethod
    def metadata(element: ET.Element) -> dict:
        return {e.get("key"): e.get("value") for e in element.findall("metadata")}

    def details(self, top: str, obj: ET.Element) -> dict:
        if not isinstance(self.settings, ET.Element):
            return {}
        for parent in self.settings.findall("object"):
            if parent.get("id") != top:
                continue
            result = self.metadata(parent)
            for part in parent.findall("part"):
                if part.get("id") == obj.get("id"):
                    result.update(self.metadata(part))
                    if part.get("subtype", "normal_part") != "normal_part":
                        self.warnings.add("Slicer-Modifikatoren werden als separate Körper angezeigt, nicht verrechnet.")
            return result
        return {}

    def visit(self, path: str, obj_id: str, transform: np.ndarray, top: str, instance: int,
              stack: tuple = ()) -> None:
        key = (path, obj_id)
        if key in stack or len(stack) >= 40:
            raise ValueError("Zyklische oder zu tief verschachtelte 3MF-Bauteile")
        _, objects, scale, resources = self.model(path)
        obj = objects.get(obj_id)
        if obj is None:
            raise ValueError(f"3MF-Körper {obj_id} fehlt in {path}")
        if obj.get("type", "model") not in ("model", "solidsupport", "support"):
            self.warnings.add("Nicht druckbare Hilfsobjekte werden nicht angezeigt.")
            return
        components = obj.find("m:components", NS)
        if components is not None:
            for comp in components.findall("m:component", NS):
                child = _archive_path(comp.get(f"{{{PROD}}}path"), path) if comp.get(f"{{{PROD}}}path") else path
                local = _matrix(comp.get("transform"))
                local[:3, 3] *= scale
                self.visit(child, comp.get("objectid"), transform @ local, top, instance, (*stack, key))
            return
        mesh = obj.find("m:mesh", NS)
        if mesh is None:
            raise ValueError(f"Körper {obj_id} enthält kein unterstütztes Dreiecksnetz")
        vertices = np.array([[float(v.get(a)) for a in ("x", "y", "z")]
                             for v in mesh.findall("m:vertices/m:vertex", NS)], dtype=float)
        triangles = mesh.findall("m:triangles/m:triangle", NS)
        indices = np.array([[int(t.get(a)) for a in ("v1", "v2", "v3")] for t in triangles], dtype=np.int64)
        self.triangles += len(indices)
        if self.triangles > MAX_TRIANGLES:
            raise ValueError("3MF enthält zu viele Dreiecke für die Vorschau")
        if vertices.size == 0 or indices.size == 0 or not np.isfinite(vertices).all():
            raise ValueError("3MF enthält leere oder ungültige Geometrie")
        if indices.min() < 0 or indices.max() >= len(vertices):
            raise ValueError("3MF enthält ungültige Dreiecksindizes")
        tris = (vertices[indices] * scale) @ transform[:3, :3].T + transform[:3, 3]
        if not np.isfinite(tris).all() or np.abs(tris).max() > 1e9:
            raise ValueError("3MF-Platzierungen erzeugen ungültige Koordinaten")
        # Keep outward winding for mirrored instances.
        if np.linalg.det(transform[:3, :3]) < 0:
            tris = tris[:, [0, 2, 1]]
        details = self.details(top, obj)
        color = None
        pid, index = obj.get("pid"), obj.get("pindex", "0")
        for resource in resources:
            if resource.get("id") == pid:
                values = list(resource)
                if index.isdigit() and int(index) < len(values):
                    color = _color(values[int(index)].get("displaycolor") or values[int(index)].get("color"))
        try:
            ext = int(details.get("extruder", "0")) - 1
            if 0 <= ext < len(self.filaments):
                color = _color(self.filaments[ext]) or color
        except (ValueError, TypeError):
            pass
        if any(t.attrib.keys() - {"v1", "v2", "v3"} for t in triangles):
            # This preview deliberately does not claim to preserve painted faces/textures.
            self.warnings.add("Flächenbemalung und einzelne Dreiecksfarben werden nicht dargestellt.")
        if color is None:
            self.warnings.add("Körper ohne lesbare Farbe erscheinen in neutralem Blau.")
        self.bodies.append({"id": f"body_{len(self.bodies):04d}",
                            "name": details.get("name") or obj.get("name") or f"Körper {obj_id}",
                            "color": color or "#4682B4", "object_id": top, "instance": instance,
                            "tris": tris})

    def read(self) -> dict:
        rels = self.xml("_rels/.rels")
        main = None
        for rel in rels:
            if rel.get("Type", "").endswith("/3dmodel"):
                if rel.get("TargetMode", "Internal") != "Internal":
                    raise ValueError("Externe 3MF-Verweise werden nicht geladen")
                main = _archive_path(rel.get("Target", ""))
                break
        if main is None:
            raise ValueError("3MF enthält keinen Verweis auf ein 3D-Modell")
        root, _, scale, _ = self.model(main)
        instances: dict[str, int] = {}
        for item in root.findall("m:build/m:item", NS):
            obj_id = item.get("objectid")
            instance = instances.get(obj_id, 0)
            instances[obj_id] = instance + 1
            if item.get("printable", "1") in ("0", "false"):
                self.warnings.add("Als nicht druckbar markierte Objekte werden ausgeblendet.")
                continue
            path = _archive_path(item.get(f"{{{PROD}}}path"), main) if item.get(f"{{{PROD}}}path") else main
            transform = _matrix(item.get("transform"))
            transform[:3, 3] *= scale
            self.visit(path, obj_id, transform, obj_id, instance)
        if not self.bodies:
            raise ValueError("3MF enthält keine anzeigbaren Körper")
        plates = []
        assignments: dict[tuple, str] = {}
        if isinstance(self.settings, ET.Element):
            for i, plate in enumerate(self.settings.findall("plate")):
                md = self.metadata(plate)
                plate_id = str(i + 1)
                plates.append({"id": plate_id, "name": md.get("plater_name") or f"Druckplatte {i + 1}"})
                for item in plate.findall("model_instance"):
                    imd = self.metadata(item)
                    assignments[(imd.get("object_id"), imd.get("instance_id", "0"))] = plate_id
        for body in self.bodies:
            body["plate"] = assignments.get((body["object_id"], str(body["instance"])),
                                             "1" if len(plates) <= 1 else "unassigned")
        if any(b["plate"] == "unassigned" for b in self.bodies):
            plates.append({"id": "unassigned", "name": "Ohne Plattenzuordnung"})
            self.warnings.add("Einige Körper konnten keiner Druckplatte zugeordnet werden.")
        if not plates:
            plates = [{"id": "1", "name": "Druckplatte 1"}]
        return {"bodies": self.bodies, "plates": plates, "warnings": sorted(self.warnings)}


def read_3mf(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as archive:
            return Reader(archive).read()
    except (zipfile.BadZipFile, ET.ParseError, KeyError, TypeError, OverflowError, RuntimeError) as exc:
        raise ValueError(f"Ungültige oder nicht unterstützte 3MF-Datei: {exc}") from exc


def preview(d: Path, filename: str | None = None) -> dict:
    """Return manifest with project-relative preview paths, keyed by source content."""
    sources = source_files(d)
    if filename is None:
        if not sources:
            raise ValueError("Keine 3MF-Datei mehr im Projektordner vorhanden")
        source = sources[0]
    else:
        source = next((p for p in sources if p.name == filename), None)
        if source is None:
            raise ValueError("Gewählte 3MF-Datei ist nicht im Projektordner vorhanden")
    if source.stat().st_size > MAX_TOTAL:
        raise ValueError("3MF-Datei ist zu gross für die Vorschau")
    with _LOCK:
        before = source.stat()
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        after_hash = source.stat()
        if (before.st_mtime_ns, before.st_size) != (after_hash.st_mtime_ns, after_hash.st_size):
            raise ValueError("3MF-Datei wird gerade geändert – bitte nochmals laden")
        key = hashlib.sha256(source.name.encode("utf-8")).hexdigest()[:16]
        cache = d / "out" / "imports" / key / f"v{CACHE_VERSION}-{sha}"
        if not cache.resolve().is_relative_to(d.resolve()):
            raise ValueError("Vorschaupfad liegt ausserhalb des Projektordners")
        manifest = cache / "manifest.json"
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if all((d / b["stl"]).is_file() for b in data["bodies"]):
                return {**data, "sources": [p.name for p in sources], "source": source.name}
        data = read_3mf(source)
        after = source.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError("3MF-Datei wird gerade geändert – bitte nochmals laden")
        cache.mkdir(parents=True, exist_ok=True)
        for body in data["bodies"]:
            tris = body.pop("tris")
            body["size_mm"] = np.ptp(tris.reshape(-1, 3), axis=0).round(4).tolist()
            body["bbox"] = [tris.min(axis=(0, 1)).tolist(), tris.max(axis=(0, 1)).tolist()]
            stl = cache / f"{body['id']}.stl"
            write_stl(stl, tris)
            body["stl"] = stl.relative_to(d).as_posix()
        data["sha256"] = sha
        manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {**data, "sources": [p.name for p in sources], "source": source.name}


def save_uploads(root: Path, files: list[tuple[str, bytes]]) -> Path:
    """One drop = one new project. Validate the whole batch before writing anything."""
    if not files or len(files) > 32:
        raise ValueError("Bitte 1 bis 32 3MF-Dateien gleichzeitig ablegen")
    if sum(len(data) for _, data in files) > MAX_TOTAL:
        raise ValueError("Die Dateien sind zusammen grösser als 256 MB")
    names = set()
    for name, data in files:
        if (not name or len(name) > 180 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name.endswith((" ", ".")) or Path(name).suffix.lower() != ".3mf"
                or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
            raise ValueError("Bitte ausschliesslich 3MF-Dateien mit gültigen Dateinamen ablegen")
        if name.casefold() in names:
            raise ValueError(f"Doppelter Dateiname im Import: {name}")
        names.add(name.casefold())
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ValueError(f"{name}: keine gültige 3MF-Archivdatei")
    slug = re.sub(r"[^a-z0-9]+", "-", Path(files[0][0]).stem.lower()).strip("-")[:40] or "import"
    base = f"{dt.date.today():%Y-%m-%d}-{slug}-3mf"
    root.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        target = root / base
        index = 2
        while target.exists():
            target = root / f"{base}-{index}"
            index += 1
        # A staging directory has no .3mf files at its root while being written,
        # so project discovery cannot show a partial drop.
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=root) as staging:
            pending = Path(staging) / "project"
            pending.mkdir()
            for name, data in files:
                (pending / name).write_bytes(data)
            pending.rename(target)
    return target
