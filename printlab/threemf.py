"""Mehrteilige 3MF im Bambu/Orca-Projektformat: EIN Objekt mit mehreren Teilkörpern,
jeder Teilkörper mit eigenem Filament (Extruder-Nummer, 1-basiert).

Aufbau (wie Bambu Studio / OrcaSlicer / Creality Print es schreiben):
  [Content_Types].xml, _rels/.rels
  3D/3dmodel.model                 Elternobjekt mit <components> → p:path Objects/object_1.model
  3D/_rels/3dmodel.model.rels
  3D/Objects/object_1.model        die Netze der Teilkörper
  Metadata/model_settings.config   Objekt/Teile mit Metadaten name und extruder
"""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from .meshcheck import weld

NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS_BBS = "http://schemas.bambulab.com/package/2021"
IDENT = "1 0 0 0 1 0 0 0 1 0 0 0"


def _mesh_xml(obj_id: int, tris: np.ndarray) -> str:
    verts, faces = weld(tris, decimals=5)
    v = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in verts)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    return (f'<object id="{obj_id}" p:UUID="{uuid.uuid4()}" type="model"><mesh>'
            f"<vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>")


def write_multipart_3mf(path: str | Path, bodies: list[tuple[str, np.ndarray, int]],
                        object_name: str = "teil") -> Path:
    """bodies: [(name, Dreiecke (n,3,3) in Bettkoordinaten, extruder 1-basiert), …]"""
    path = Path(path)
    parent_id = len(bodies) + 1
    objects_model = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{NS_CORE}" xmlns:BambuStudio="{NS_BBS}" '
        f'xmlns:p="{NS_PROD}" requiredextensions="p"><metadata name="BambuStudio:3mfVersion">1</metadata>'
        f'<resources>' + "".join(_mesh_xml(i + 1, b[1]) for i, b in enumerate(bodies)) +
        '</resources><build/></model>')
    comps = "".join(
        f'<component p:path="/3D/Objects/object_1.model" objectid="{i + 1}" p:UUID="{uuid.uuid4()}" '
        f'transform="{IDENT}"/>' for i in range(len(bodies)))
    main_model = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{NS_CORE}" xmlns:BambuStudio="{NS_BBS}" '
        f'xmlns:p="{NS_PROD}" requiredextensions="p">'
        f'<metadata name="Application">BambuStudio-01.09.00.70</metadata>'
        f'<metadata name="BambuStudio:3mfVersion">1</metadata>'
        f'<metadata name="Title">{object_name}</metadata>'
        f'<resources><object id="{parent_id}" p:UUID="{uuid.uuid4()}" type="model">'
        f'<components>{comps}</components></object></resources>'
        f'<build p:UUID="{uuid.uuid4()}"><item objectid="{parent_id}" p:UUID="{uuid.uuid4()}" '
        f'transform="{IDENT}" printable="1"/></build></model>')
    parts = "".join(
        f'<part id="{i + 1}" subtype="normal_part">'
        f'<metadata key="name" value={quoteattr(name)}/>'
        f'<metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>'
        f'<metadata key="extruder" value="{int(ext)}"/>'
        f'</part>' for i, (name, _, ext) in enumerate(bodies))
    first_ext = int(bodies[0][2])
    model_settings = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config>'
        f'<object id="{parent_id}"><metadata key="name" value={quoteattr(object_name)}/>'
        f'<metadata key="extruder" value="{first_ext}"/>{parts}</object>'
        '<plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value=""/>'
        '<metadata key="locked" value="false"/>'
        f'<model_instance><metadata key="object_id" value="{parent_id}"/>'
        '<metadata key="instance_id" value="0"/><metadata key="identify_id" value="1"/></model_instance>'
        '</plate></config>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        '<Default Extension="config" ContentType="text/xml"/></Types>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    model_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/Objects/object_1.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", main_model)
        z.writestr("3D/_rels/3dmodel.model.rels", model_rels)
        z.writestr("3D/Objects/object_1.model", objects_model)
        z.writestr("Metadata/model_settings.config", model_settings)
    return path


def read_parts(path: str | Path) -> list[dict]:
    """Zum Testen: Teile mit Extruder und Dreieckszahl aus einer geschriebenen 3MF lesen."""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(path) as z:
        cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
        objs = ET.fromstring(z.read("3D/Objects/object_1.model"))
    ns = {"m": NS_CORE}
    tri_count = {o.get("id"): len(o.findall(".//m:triangle", ns)) for o in objs.findall(".//m:object", ns)}
    out = []
    for part in cfg.iter("part"):
        md = {m.get("key"): m.get("value") for m in part.findall("metadata")}
        out.append({"id": part.get("id"), "name": md.get("name"), "extruder": int(md.get("extruder")),
                    "triangles": tri_count.get(part.get("id"))})
    return out
