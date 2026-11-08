"""Baugruppen: mehrere Teil-Projekte zusammengesetzt ansehen und auf Kollisionen prüfen.

Eine Baugruppe ist ein Projektordner mit `assembly.py` (statt `model.py`). Die Datei ist Python,
weil Platzierungen meist von Parametern der Teile abhängen. Vertrag:

    from slicetheprompt.assembly import *

    TEILE = {"Basis": Teil("2026-11-04-halter-basis"),            # Projektname unter projects/
             "Knopf": Teil("2026-11-04-halter-knopf", anzahl=6)}  # gleiches Teil mehrfach
    POSEN = {"Mitte": {"x": 32}, "Anschlag": {"x": 57}}           # benannte Stellungen (Parameter frei)

    def platzierung(p):            # p = Werte einer Pose -> je Teil Tf (bei anzahl > 1: Liste)
        return {"Basis": pos(0, 0, 0), "Knopf": [pos(p["x"], 0, 0) * rot(0, 90, 0)] * 6}

    def geister(p):                # optional: Bezugskörper (Handy, Okular …), nur Ansicht + Prüfung
        return {"Handy": Pos(0, 0, 10) * Box(160, 75, 8)}

    GELENKE = [Fest("Basis"), Schraube("Knopf 1", "Basis", achse=(1, 0, 0), punkt=(32, 0, 0), steigung=0.7)]
                                          # optional: Mechanismus (siehe kinematics.py)
    KONTAKT = {("Basis", "Handy"): 0.3}   # erlaubte Eindringtiefe (mm) je Paar, Muster mit * erlaubt
    IGNORIEREN = [("Handy", "Okular")]    # Paare ohne Prüfung
    GRENZE_MM3 = 0.5                      # Überschneidung bis hierher gilt als Rundungsrest

Jedes Teil liegt in seinem `out/model.step` in **Drucklage**. `platzierung` liefert die Abbildung
Drucklage -> Baugruppen-System als `Tf` (4×4, darf spiegeln). `build_assembly` lädt jedes STEP einmal,
schreibt je Teil ein STL und in `out/assembly.json` je Pose nur Matrizen, Farben und Namen, dazu
Überschneidungen (kleine STLs), minimalen Abstand je Paar und `checks`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import itertools
import json
import math
import re
import runpy
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import ROOT, project_dir

ASSEMBLY_FILE = "assembly.py"
OUT_SUB = "assembly"                       # out/assembly/… für STLs
NAH_MM = 5.0                               # Paare mit Hüllquader-Abstand darunter: exakter Abstand
VOL_EPS = 1e-3                             # mm³ – darunter keine Überschneidung (wie zusammenbau_test)
PALETTE = ["#4E79A7", "#F28E2B", "#59A14F", "#B07AA1", "#EDC948", "#76B7B2", "#FF9DA7", "#9C755F",
           "#E15759", "#BAB0AC"]
GHOST_COLOR = "#9AA5B1"

from .kinematics import Drehung, Ebene, Fest, Getriebe, Kugel, Schiebung, Schraube, Zylinder  # noqa: E402,F401

__all__ = ["Fest", "Drehung", "Schiebung", "Zylinder", "Kugel", "Ebene", "Schraube", "Getriebe",
           "Teil", "Geist", "Tf", "pos", "rot", "spiegel", "parameter", "is_assembly", "load_assembly",
           "build_assembly", "new_assembly"]


# ---------------------------------------------------------------------------------------------
# Transformationen
# ---------------------------------------------------------------------------------------------
class Tf:
    """Affine 4×4-Abbildung (mm). `a * b` = erst b, dann a. Darf spiegeln (det < 0).

    `Tf * Tf` -> Tf, `Tf * Shape` -> transformierte Kopie (build123d), `Tf * (x, y, z)` -> Punkt.
    build123d-Locations (`Pos`, `Rot`, `Location`) werden mit `Tf(loc)` übernommen."""

    __slots__ = ("m",)

    def __init__(self, m=None):
        if m is None:
            self.m = np.eye(4)
        elif isinstance(m, Tf):
            self.m = m.m.copy()
        elif _is_loc(m):                                              # build123d Location
            t = m.wrapped.Transformation()
            self.m = np.array([[t.Value(r, c) for c in range(1, 5)] for r in range(1, 4)] + [[0, 0, 0, 1]],
                              dtype=float)
        else:
            a = np.asarray(m, dtype=float)
            if a.shape == (3, 4):
                a = np.vstack([a, [0, 0, 0, 1]])
            if a.shape == (16,):
                a = a.reshape(4, 4)
            if a.shape != (4, 4):
                raise ValueError(f"Tf erwartet 4×4, erhalten {a.shape}")
            self.m = a

    def __mul__(self, other):
        if isinstance(other, Tf):
            return Tf(self.m @ other.m)
        if _is_loc(other):
            return Tf(self.m @ Tf(other).m)
        if hasattr(other, "wrapped"):
            return transform_shape(other, self)
        p = np.asarray(other, dtype=float)
        if p.shape == (3,):
            return self.m[:3, :3] @ p + self.m[:3, 3]
        return NotImplemented

    def __rmul__(self, other):
        if _is_loc(other):                                            # Location * Tf
            return Tf(Tf(other).m @ self.m)
        return NotImplemented

    def inv(self) -> "Tf":
        return Tf(np.linalg.inv(self.m))

    @property
    def det(self) -> float:
        return float(np.linalg.det(self.m[:3, :3]))

    def points(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=float)
        return pts @ self.m[:3, :3].T + self.m[:3, 3]

    def key(self) -> bytes:
        return np.round(self.m, 6).tobytes()

    def as_list(self) -> list[float]:
        """Zeilenweise 16 Werte (Three.js: Matrix4.set(...))."""
        return [round(float(v), 6) for v in self.m.ravel()]

    def __repr__(self) -> str:
        return f"Tf({np.round(self.m, 4).tolist()})"


def _is_loc(obj) -> bool:
    from build123d import Location
    return isinstance(obj, Location)


def pos(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Tf:
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return Tf(m)


def rot(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Tf:
    """Drehung in Grad, gleiche Konvention wie build123d `Rot(x, y, z)`."""
    from build123d import Rot
    return Tf(Rot(x, y, z))


def spiegel(ebene: str = "XZ") -> Tf:
    """Spiegelung an einer Ursprungsebene: 'XZ' (y -> -y), 'YZ' (x -> -x), 'XY' (z -> -z)."""
    idx = {"YZ": 0, "XZ": 1, "XY": 2}[ebene.upper()]
    m = np.eye(4)
    m[idx, idx] = -1.0
    return Tf(m)


def transform_shape(shape, tf: Tf):
    """Kopie eines build123d-Körpers mit beliebiger affiner Abbildung (auch gespiegelt)."""
    from build123d.topology import Shape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    tr = gp_Trsf()
    tr.SetValues(*[float(v) for v in tf.m[:3].ravel()])
    res = BRepBuilderAPI_Transform(shape.wrapped, tr, True).Shape()
    return Shape.cast(res)


# ---------------------------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------------------------
@dataclass
class Teil:
    """Ein Teil-Projekt in der Baugruppe."""
    projekt: str
    anzahl: int = 1
    farbe: str | None = None
    explosion: tuple[float, float, float] | None = None   # Richtung beim Auseinanderziehen (sonst radial)
    beschreibung: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Geist:
    """Bezugskörper (nicht gedruckt): Form (build123d) und optional ihre Lage (Tf) und Farbe.
    Mit `lage` bleibt die Form je Pose gleich und wird nur einmal als STL gespeichert."""
    form: object
    farbe: str | None = None
    lage: object = None
    folgt: str | None = None      # Teilname: beim Auseinanderziehen (Studio) mit diesem Teil mitbewegen


def is_assembly(d: Path) -> bool:
    return (Path(d) / ASSEMBLY_FILE).exists()


def parameter(projekt: str) -> types.SimpleNamespace:
    """Parameter-Block eines Teil-model.py (GROSS geschriebene Namen oben in der Datei), ohne Geometrie.

    Führt Importe, Funktionsdefinitionen und Zuweisungen an GROSSBUCHSTABEN-Namen aus, bis zur ersten
    anderen Anweisung (meist der Geometrie). So bleibt die Baugruppe an den Massen der Teile."""
    import ast

    d = project_dir(projekt)
    src = (d / "model.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = []

    def upper_target(t) -> bool:
        if isinstance(t, ast.Name):
            return t.id.isupper() or t.id.startswith("_")
        if isinstance(t, (ast.Tuple, ast.List)):
            return all(upper_target(e) for e in t.elts)
        return False

    for st in tree.body:
        if isinstance(st, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
            keep.append(st)
        elif isinstance(st, ast.Expr):           # Docstring, set_material(...)
            keep.append(st)
        elif isinstance(st, ast.Assign) and all(upper_target(t) for t in st.targets):
            keep.append(st)
        elif isinstance(st, ast.AnnAssign) and upper_target(st.target):
            keep.append(st)
        else:
            break
    mod = ast.Module(body=keep, type_ignores=[])
    ns: dict = {"__file__": str(d / "model.py"), "__name__": "__slicetheprompt_param__"}
    for p in (str(ROOT), str(d)):
        if p not in sys.path:
            sys.path.insert(0, p)
    exec(compile(mod, str(d / "model.py"), "exec"), ns)
    return types.SimpleNamespace(**{k: v for k, v in ns.items() if k.isupper() and not k.startswith("_")})


def load_assembly(d: Path) -> dict:
    """assembly.py ausführen und prüfen. Liefert dict mit TEILE, POSEN, platzierung, geister, …"""
    d = Path(d)
    f = d / ASSEMBLY_FILE
    if not f.exists():
        raise FileNotFoundError(f"{f} fehlt")
    for p in (str(ROOT), str(d)):
        if p not in sys.path:
            sys.path.insert(0, p)
    ns = runpy.run_path(str(f), run_name="__slicetheprompt_baugruppe__")
    teile = ns.get("TEILE")
    posen = ns.get("POSEN")
    platz = ns.get("platzierung")
    if not isinstance(teile, dict) or not teile:
        raise ValueError("assembly.py: TEILE = {Name: Teil(projekt, anzahl=…)} fehlt")
    for n, t in teile.items():
        if not isinstance(t, Teil):
            raise ValueError(f"assembly.py: TEILE['{n}'] ist kein Teil(...)")
        if t.anzahl < 1:
            raise ValueError(f"assembly.py: TEILE['{n}'].anzahl muss ≥ 1 sein")
    if not isinstance(posen, dict) or not posen:
        posen = {"Standard": {}}
    if not callable(platz):
        raise ValueError("assembly.py: Funktion platzierung(pose) fehlt")
    geister = ns.get("geister")
    return {
        "doc": (ns.get("__doc__") or "").strip(),
        "TEILE": teile,
        "POSEN": posen,
        "platzierung": platz,
        "geister": geister if callable(geister) else None,
        "KONTAKT": dict(ns.get("KONTAKT") or {}),
        "IGNORIEREN": list(ns.get("IGNORIEREN") or []),
        "GRENZE_MM3": float(ns.get("GRENZE_MM3", 0.5)),
        "GELENKE": _gelenke(ns.get("GELENKE")),
    }


def _gelenke(g) -> list:
    from . import kinematics as K
    if not g:
        return []
    if not isinstance(g, (list, tuple)):
        raise ValueError("assembly.py: GELENKE muss eine Liste sein, z. B. [Fest('Rahmen'), Drehung(...)]")
    for x in g:
        if not isinstance(x, K.GELENK_TYPEN):
            raise ValueError(f"assembly.py: {x!r} ist kein Gelenk (Fest, Drehung, Schiebung, Zylinder, Kugel, "
                             "Ebene, Schraube, Getriebe)")
    return list(g)


def instances_for_pose(asm: dict, pose: dict) -> list[dict]:
    """[{name, teil, tf}] für eine Pose; prüft, dass jedes Teil genau seine Anzahl Lagen bekommt."""
    pl = asm["platzierung"](pose)
    if not isinstance(pl, dict):
        raise ValueError("platzierung(pose) muss ein dict {Teilname: Tf | [Tf, …]} liefern")
    unbekannt = set(pl) - set(asm["TEILE"])
    if unbekannt:
        raise ValueError(f"platzierung: unbekannte Teile {sorted(unbekannt)}")
    out = []
    for name, teil in asm["TEILE"].items():
        if name not in pl:
            raise ValueError(f"platzierung: Lage für '{name}' fehlt")
        v = pl[name]
        lst = list(v) if isinstance(v, (list, tuple)) else [v]
        if len(lst) != teil.anzahl:
            raise ValueError(f"platzierung: '{name}' braucht {teil.anzahl} Lage(n), erhalten {len(lst)}")
        for i, t in enumerate(lst, 1):
            tf = t if isinstance(t, Tf) else Tf(t)
            out.append({"name": name if teil.anzahl == 1 else f"{name} {i}", "teil": name, "tf": tf})
    return out


def _match(pat_pair, a: dict, b: dict) -> bool:
    pa, pb = pat_pair

    def m(p, x):
        return fnmatch.fnmatchcase(x["name"], p) or fnmatch.fnmatchcase(x["teil"], p)
    return (m(pa, a) and m(pb, b)) or (m(pa, b) and m(pb, a))


def contact_limit(asm: dict, a: dict, b: dict) -> float | None:
    for pair, depth in asm["KONTAKT"].items():
        if _match(pair, a, b):
            return float(depth)
    return None


def ignored(asm: dict, a: dict, b: dict) -> bool:
    return any(_match(p, a, b) for p in asm["IGNORIEREN"])


# ---------------------------------------------------------------------------------------------
# Geometrie-Helfer
# ---------------------------------------------------------------------------------------------
def _slug(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "x"


def tessellate(shape, tol: float = 0.01, ang: float = 0.1) -> np.ndarray:
    """build123d-Körper -> Dreiecke (n, 3, 3)."""
    verts, tris = shape.tessellate(tol, ang)
    if not tris:
        return np.zeros((0, 3, 3))
    v = np.array([tuple(p) for p in verts], dtype=float)
    return v[np.asarray(tris, dtype=int)]


def _bbox(shape) -> np.ndarray:
    bb = shape.bounding_box()
    return np.array([tuple(bb.min), tuple(bb.max)], dtype=float)


def bbox_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Abstand zweier Hüllquader (0 bei Überlappung) – untere Schranke des echten Abstands."""
    g = np.maximum(0.0, np.maximum(a[0] - b[1], b[0] - a[1]))
    return float(np.linalg.norm(g))


def bbox_overlap(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all(a[0] < b[1]) and np.all(b[0] < a[1]))


def min_distance(a, b) -> float:
    """Exakter kleinster Abstand zweier Körper (0 bei Berührung/Überschneidung)."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.Extrema import Extrema_ExtFlag

    d = BRepExtrema_DistShapeShape()
    d.LoadS1(a.wrapped)
    d.LoadS2(b.wrapped)
    d.SetFlag(Extrema_ExtFlag.Extrema_ExtFlag_MIN)
    d.SetMultiThread(True)
    d.Perform()
    if not d.IsDone():
        raise RuntimeError("Abstandsberechnung fehlgeschlagen")
    return float(d.Value())


def intersection(a, b):
    try:
        r = a & b
    except Exception:  # noqa: BLE001 – OCC-Boolesche Fehler als „unbekannt“ behandeln
        return None
    if r is None:
        return None
    try:
        return r if r.volume > VOL_EPS else None
    except Exception:  # noqa: BLE001
        return None


def _shells(s):
    """Oberfläche eines Körpers (alle Schalen) – Abstand dazu ist auch innen > 0."""
    from OCP.BRep import BRep_Builder
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Compound

    comp, bld = TopoDS_Compound(), BRep_Builder()
    bld.MakeCompound(comp)
    ex = TopExp_Explorer(s.wrapped, TopAbs_SHELL)
    while ex.More():
        bld.Add(comp, ex.Current())
        ex.Next()
    return comp


def _point_dist(p, shape_wrapped) -> float:
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, p))).Vertex()
    d = BRepExtrema_DistShapeShape(v, shape_wrapped)
    return float(d.Value()) if d.IsDone() else math.inf


def _inside(solid, p) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN

    c = BRepClass3d_SolidClassifier(solid.wrapped, gp_Pnt(*map(float, p)), 1e-7)
    return c.State() == TopAbs_IN


def ray_hits(orig: np.ndarray, dirs: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """Kleinste Trefferdistanz je Strahl auf ein Dreiecksnetz (Möller–Trumbore), inf ohne Treffer."""
    if len(tris) == 0 or len(orig) == 0:
        return np.full(len(orig), np.inf)
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    pv = np.cross(dirs[:, None, :], e2[None])                  # (m, k, 3)
    det = np.einsum("kj,mkj->mk", e1, pv)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = orig[:, None, :] - v0[None]
    u = np.einsum("mkj,mkj->mk", tv, pv) * inv
    qv = np.cross(tv, e1[None])
    v = np.einsum("mj,mkj->mk", dirs, qv) * inv
    t = np.einsum("kj,mkj->mk", e2, qv) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-6)
    return np.where(hit, t, np.inf).min(axis=1)


def penetration_depth(inter, a, b, max_rays: int = 600) -> float:
    """Eindringtiefe zweier Körper senkrecht zur Oberfläche.

    Jede Fläche der Überschneidung stammt von der Oberfläche von a oder von b. Von Flächen der einen
    Seite gehen Strahlen nach innen; wo sie auf eine Fläche der anderen Seite treffen, ist die Länge
    die örtliche Tiefe. Das Maximum ist die Eindringtiefe (bei einer dünnen Scheibe: ihre Dicke).
    Stirnflächen (Strahl trifft wieder dieselbe Seite) zählen nicht."""
    sa, sb = _shells(a), _shells(b)
    groups: dict[str, list[np.ndarray]] = {"a": [], "b": []}
    origins, dirs, src = [], [], []
    for f in inter.faces():
        tris = tessellate(f, 0.005, 0.1)
        if not len(tris):
            continue
        c = tris.mean(axis=1)
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        ln = np.linalg.norm(n, axis=1)
        keep = ln > 1e-12
        tris, c, n = tris[keep], c[keep], n[keep] / ln[keep, None]
        if not len(tris):
            continue
        i = int(np.argmax(ln[keep]))
        da, db = _point_dist(c[i], sa), _point_dist(c[i], sb)
        if da < 1e-5 and db < 1e-5:
            continue                       # bündige Fläche beider Körper: weder Quelle noch Ziel
        side = "a" if da <= db else "b"
        size = float(np.sqrt(ln[keep][i]))
        eps = min(1e-3, 0.05 * size)
        if not _inside(inter, c[i] - eps * n[i]):          # Normale nach innen drehen
            n = -n
        groups[side].append(tris)
        origins.append(c)
        dirs.append(-n)
        src.append(np.full(len(c), 0 if side == "a" else 1))
    if not origins:
        return 0.0
    O, D, Sx = np.concatenate(origins), np.concatenate(dirs), np.concatenate(src)
    if len(O) > max_rays:
        sel = np.random.default_rng(0).choice(len(O), max_rays, replace=False)
        O, D, Sx = O[sel], D[sel], Sx[sel]
    empty = np.zeros((0, 3, 3))
    ta = np.concatenate(groups["a"]) if groups["a"] else empty
    tb = np.concatenate(groups["b"]) if groups["b"] else empty
    hit_a, hit_b = ray_hits(O, D, ta), ray_hits(O, D, tb)
    # nur Strahlen, deren erster Treffer auf der anderen Seite liegt
    other = np.where(Sx == 0, hit_b, hit_a)
    same = np.where(Sx == 0, hit_a, hit_b)
    good = np.isfinite(other) & (other <= same + 1e-9)
    if good.any():
        return float(other[good].max())
    # Ein Körper geht ganz durch den anderen (Stift durch Block): keine Gegenfläche –
    # die Überschneidung ist so tief wie sie dick ist.
    first = np.minimum(hit_a, hit_b)
    return float(first[np.isfinite(first)].max()) if np.isfinite(first).any() else 0.0


# ---------------------------------------------------------------------------------------------
# Bauen
# ---------------------------------------------------------------------------------------------
def _clear_out(out: Path) -> None:
    sub = out / OUT_SUB
    if sub.exists():
        for f in sorted(sub.rglob("*"), reverse=True):
            try:
                f.unlink() if f.is_file() else f.rmdir()
            except OSError:
                pass
    sub.mkdir(parents=True, exist_ok=True)
    for name in ("assembly.json", "render.png"):
        try:
            (out / name).unlink(missing_ok=True)
        except OSError:
            pass


def _part_status(pd: Path) -> dict:
    out = pd / "out"
    rep = {}
    try:
        rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    step = out / "model.step"
    model = pd / "model.py"
    stale = step.exists() and model.exists() and model.stat().st_mtime > step.stat().st_mtime + 1
    return {"passed": rep.get("passed"), "errors": len(rep.get("errors") or []),
            "warnings": len(rep.get("warnings") or []), "veraltet": bool(stale)}


def build_assembly(project: str) -> dict:
    """Baugruppe bauen: STEPs laden, STLs und out/assembly.json schreiben, Kollisionen prüfen, rendern."""
    from build123d import import_step

    from .stlio import write_stl

    t0 = time.time()
    d = project_dir(project)
    asm = load_assembly(d)
    out = d / "out"
    out.mkdir(exist_ok=True)
    _clear_out(out)
    sub = out / OUT_SUB
    hinweise: list[str] = []

    # 1. Teile: STEP einmal laden, STL in Drucklage schreiben
    teile_json, shapes, h = _load_parts(asm, d, sub)
    hinweise += h

    # 2. Gelenke (optional) – Grundstellung = erste Pose
    ghost_files: dict[str, dict] = {}       # Hash -> {id, stl}
    tf_cache: dict[tuple, object] = {}       # (quelle, matrix) -> transformierter Körper
    pair_cache: dict[tuple, dict] = {}       # (key a, key b) -> Ergebnis
    posen = list(asm["POSEN"].items())
    kin = None
    if asm["GELENKE"]:
        ref_insts, _ = _pose_bodies(asm, dict(posen[0][1] or {}), shapes, sub, ghost_files, tf_cache)
        kin = _kin_setup(asm, ref_insts)
        hinweise += kin.pop("_hinweise")

    # 3. Posen
    posen_json = []
    for pi, (pname, pvals) in enumerate(posen):
        tp = time.time()
        pvals = dict(pvals or {})
        insts, g_json = _pose_bodies(asm, pvals, shapes, sub, ghost_files, tf_cache)
        werte = None
        if kin:
            werte, h = _kin_pose(kin, insts, g_json, pvals, pname, shapes, tf_cache)
            hinweise += h
        for it in insts:
            it["bbox"] = _bbox(it["shape"])
        pslug = f"p{pi + 1:02d}_{_slug(pname)}"
        ueber, abst, checks = check_pairs(asm, insts, sub, pslug, pname, pair_cache)
        posen_json.append({
            "name": pname, "werte": pvals, "ok": all(c["ok"] for c in checks),
            "gelenkwerte": werte,
            "instanzen": [{"name": it["name"], "teil": it["teil"], "matrix": it["tf"].as_list(),
                           "gespiegelt": it["tf"].det < 0, "bbox": it["bbox"].round(3).tolist()}
                          for it in insts if not it["geist"]],
            "geister": g_json,
            "ueberschneidungen": ueber,
            "abstaende": sorted(abst, key=lambda x: x["mm"]),
            "checks": checks,
            "zeit_s": round(time.time() - tp, 1),
        })

    bb_all = np.array([[min(i["bbox"][0][k] for p in posen_json for i in p["instanzen"]) for k in range(3)],
                       [max(i["bbox"][1][k] for p in posen_json for i in p["instanzen"]) for k in range(3)]])
    res = {
        "ok": all(p["ok"] for p in posen_json),
        "art": "baugruppe",
        "projekt": d.name,
        "beschreibung": asm["doc"].splitlines()[0] if asm["doc"] else "",
        "teile": teile_json,
        "posen": posen_json,
        "kinematik": kin,
        "grenze_mm3": asm["GRENZE_MM3"],
        "kontakt": [{"paar": list(k), "tiefe_mm": v} for k, v in asm["KONTAKT"].items()],
        "ignorieren": [list(p) for p in asm["IGNORIEREN"]],
        "bbox": bb_all.round(3).tolist(),
        "hinweise": hinweise,
        "zeit_s": round(time.time() - t0, 1),
        "erstellt": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (out / "assembly.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    try:
        png = render_assembly(d, res, 0)
    except Exception as exc:  # noqa: BLE001 – Bild ist Beigabe, Prüfung bleibt gültig
        png, res["hinweise"] = None, hinweise + [f"render.png fehlgeschlagen: {exc}"]
    return summary(res, png)


def _load_parts(asm: dict, d: Path, sub: Path | None) -> tuple[list, dict, list]:
    """STEP je Teil einmal laden; mit `sub` auch STL schreiben. -> (teile_json, shapes, hinweise)"""
    from build123d import import_step

    from .stlio import write_stl

    teile_json, shapes, hinweise = [], {}, []
    for i, (name, teil) in enumerate(asm["TEILE"].items()):
        pd = project_dir(teil.projekt)
        step = pd / "out" / "model.step"
        if not step.exists():
            raise FileNotFoundError(f"Teil '{name}': {pd.name}/out/model.step fehlt – erst build_and_check "
                                    f"für {pd.name}")
        shp = import_step(str(step))
        shapes[name] = shp
        if sub is None:
            continue
        stl = sub / f"teil_{_slug(name)}.stl"
        tris = tessellate(shp)
        write_stl(stl, tris, header=f"slicetheprompt {d.name} {name}")
        st = _part_status(pd)
        if st["veraltet"]:
            hinweise.append(f"{name}: model.py ist neuer als out/model.step – Teil neu bauen")
        if st["passed"] is False:
            hinweise.append(f"{name}: Teilprüfung hat {st['errors']} Fehler")
        teile_json.append({
            "name": name, "projekt": pd.name, "anzahl": teil.anzahl,
            "farbe": teil.farbe or PALETTE[i % len(PALETTE)],
            "stl": f"{OUT_SUB}/{stl.name}", "dreiecke": int(len(tris)),
            "volumen_mm3": round(float(shp.volume), 2), "bbox_druck": _bbox(shp).round(3).tolist(),
            "explosion": list(teil.explosion) if teil.explosion else None,
            "beschreibung": teil.beschreibung, "status": st,
        })
    return teile_json, shapes, hinweise


def _pose_bodies(asm, pvals, shapes, sub, ghost_files, tf_cache):
    """Körper einer Pose: Teile (aus platzierung) und Geister. -> (insts, geister_json)"""
    from .stlio import write_stl

    insts = instances_for_pose(asm, pvals)
    for it in insts:
        k = (it["teil"], it["tf"].key())
        if k not in tf_cache:
            tf_cache[k] = transform_shape(shapes[it["teil"]], it["tf"])
        it["shape"], it["key"], it["geist"] = tf_cache[k], k, False
    geister = asm["geister"](pvals) if asm["geister"] else {}
    g_json = []
    for gname, g in (geister or {}).items():
        if isinstance(g, (tuple, list)):
            g = Geist(*g)
        elif not isinstance(g, Geist):
            g = Geist(g)
        farbe = g.farbe or GHOST_COLOR
        local = tessellate(g.form, 0.02, 0.2)
        if g.lage is not None:                 # Form in eigenen Koordinaten, Lage als Matrix
            lage = g.lage if isinstance(g.lage, Tf) else Tf(g.lage)
            gshape, base = lage * g.form, g.form
        else:                                  # Form schon in Baugruppen-Koordinaten
            corner = local.reshape(-1, 3).min(axis=0) if len(local) else np.zeros(3)
            lage, local, gshape = pos(*corner), local - corner, g.form
            base = pos(*(-corner)) * g.form
        h = hashlib.sha1(np.round(local, 3).tobytes()).hexdigest()[:10]
        if h not in ghost_files and sub is not None:
            gid = f"{_slug(gname)}_{h}"
            write_stl(sub / f"geist_{gid}.stl", local, header=f"slicetheprompt geist {gname}")
            ghost_files[h] = {"id": gid, "stl": f"{OUT_SUB}/geist_{gid}.stl"}
        gf = ghost_files.get(h, {"id": h, "stl": None})
        g_json.append({"name": gname, "id": gf["id"], "stl": gf["stl"],
                       "farbe": farbe, "matrix": lage.as_list(), "gespiegelt": lage.det < 0,
                       "folgt": g.folgt})
        insts.append({"name": gname, "teil": gname, "tf": lage, "shape": gshape, "base": base,
                      "key": ("geist", h, lage.key()), "geist": True})
    return insts, g_json


def _move(it: dict, D: np.ndarray, shapes: dict, tf_cache: dict) -> None:
    """Körper um D (4×4, Welt) bewegen."""
    tf = Tf(D) * it["tf"]
    it["tf"] = tf
    if it["geist"]:
        it["shape"] = tf * it["base"]
        it["key"] = ("geist", it["key"][1], tf.key())
    else:
        k = (it["teil"], tf.key())
        if k not in tf_cache:
            tf_cache[k] = transform_shape(shapes[it["teil"]], tf)
        it["shape"], it["key"] = tf_cache[k], k


def _kin_setup(asm: dict, ref_insts: list[dict]) -> dict:
    """Gelenke prüfen und für Studio/JSON aufbereiten (Bezug: Grundstellung = erste Pose)."""
    from . import kinematics as K

    names = [it["name"] for it in ref_insts]
    joints = K.prepare([g.to_json() for g in asm["GELENKE"]], names)
    for it in ref_insts:
        it["bbox"] = _bbox(it["shape"])
    zentren = {it["name"]: ((it["bbox"][0] + it["bbox"][1]) / 2).round(4).tolist() for it in ref_insts}
    mech = K.Mechanismus(names, joints)
    mech.set_centers(zentren)
    hinweise = []
    for jn, e in joint_errors(mech).items():
        if e > 0.01:
            hinweise.append(f"Gelenk {jn}: passt in der Grundstellung nicht (Rest {e:.3f}) – Achse/Punkt prüfen")
    frei = [it["name"] for it in ref_insts if it["name"] not in mech.fixed and not _connected(it["name"], joints)]
    if frei:
        hinweise.append(f"Ohne Gelenk (bewegen sich frei): {', '.join(frei)}")
    return {
        "gelenke": joints,
        "koerper": names,
        "grundstellung": {it["name"]: it["tf"].as_list() for it in ref_insts},
        "zentren": zentren,
        "boden": sorted(mech.fixed),
        "freiheitsgrade": mech.dof(),
        "_hinweise": hinweise,
    }


def _connected(name: str, joints: list[dict]) -> bool:
    return any(j.get("a") == name or j.get("b") == name for j in joints)


def joint_errors(mech) -> dict:
    """Grösster Restfehler je Gelenk in der aktuellen Lage."""
    out = {}
    st = (mech.R, mech.c)
    saved = mech.joints
    try:
        for j in saved:
            if j["typ"] in ("boden", "getriebe"):
                continue
            mech.joints = [j]
            r = mech.residuals(st, extra=False)
            out[j["name"]] = float(np.max(np.abs(r))) if len(r) else 0.0
    finally:
        mech.joints = saved
    return out


def _kin_pose(kin, insts, g_json, pvals, pname, shapes, tf_cache):
    """Gelenkwerte einer Pose messen; mit pvals['gelenke'] die Teile per Löser dorthin bewegen."""
    from . import kinematics as K

    ref = {n: np.array(m, float).reshape(4, 4) for n, m in kin["grundstellung"].items()}
    by = {it["name"]: it for it in insts}
    hinweise = []

    def mech_now():
        for it in insts:
            it["bbox"] = _bbox(it["shape"])
        disp = {n: by[n]["tf"].m @ np.linalg.inv(ref[n]) for n in by if n in ref}
        m = K.Mechanismus(list(by), K.move_joints(kin["gelenke"], disp))
        m.set_centers({n: (by[n]["bbox"][0] + by[n]["bbox"][1]) / 2 for n in by})
        return m

    drives = pvals.get("gelenke") or {}
    if drives:
        m = mech_now()
        res = m.solve(drives)
        if not res["ok"]:
            hinweise.append(f"Pose {pname}: Gelenkwerte {drives} nicht erreichbar (Rest {res['rest']:.3g})")
        for n in m.movable:
            D = m.displacement(n)
            if np.abs(D - np.eye(4)).max() > 1e-9:
                _move(by[n], D, shapes, tf_cache)
        for g in g_json:
            g["matrix"] = by[g["name"]]["tf"].as_list()
    m = mech_now()
    fehler = {k: v for k, v in joint_errors(m).items() if v > 0.01}
    if fehler:
        worst = max(fehler, key=fehler.get)
        hinweise.append(f"Pose {pname}: verletzt Gelenk {worst} um {fehler[worst]:.3f} "
                        f"(platzierung und GELENKE passen nicht zusammen)")
    return {k: round(v, 4) for k, v in m.coords().items()}, hinweise


def check_pairs(asm, insts, sub, pslug, pname, pair_cache=None):
    """Alle Paare prüfen: Überschneidung (exakt), Eindringtiefe bei Klemmkontakt, kleinster Abstand."""
    from .stlio import write_stl

    pair_cache = {} if pair_cache is None else pair_cache
    ueber, abst, checks = [], [], []
    for it in insts:
        if "bbox" not in it:
            it["bbox"] = _bbox(it["shape"])
    for a, b in itertools.combinations(insts, 2):
        if ignored(asm, a, b):
            continue
        ck = tuple(sorted((a["key"], b["key"])))
        res = pair_cache.get(ck)
        if res is None:
            res = {"volumen": 0.0, "tiefe": None, "abstand": None, "genau": True, "inter": None}
            gap = bbox_gap(a["bbox"], b["bbox"])
            if bbox_overlap(a["bbox"], b["bbox"]):
                inter = intersection(a["shape"], b["shape"])
                if inter is not None:
                    res["volumen"] = float(inter.volume)
                    res["inter"] = inter
                    res["abstand"] = 0.0
            if res["abstand"] is None:
                if gap < NAH_MM:
                    res["abstand"] = min_distance(a["shape"], b["shape"])
                else:
                    res["abstand"], res["genau"] = gap, False
            pair_cache[ck] = res
        paar = f"{a['name']} + {b['name']}"
        abst.append({"a": a["name"], "b": b["name"], "mm": round(res["abstand"], 3), "genau": res["genau"]})
        if res["inter"] is None:
            continue
        limit = contact_limit(asm, a, b)
        if limit is not None and res["tiefe"] is None:
            res["tiefe"] = penetration_depth(res["inter"], a["shape"], b["shape"])
        stl = sub / f"{pslug}__{_slug(a['name'])}__{_slug(b['name'])}.stl"
        write_stl(stl, tessellate(res["inter"], 0.005, 0.1), header=f"ueberschneidung {paar}")
        if limit is not None:
            ok = res["tiefe"] <= limit + 1e-6
            art, text = "klemmkontakt", (f"{paar}: {res['volumen']:.2f} mm³, Tiefe {res['tiefe']:.2f} mm "
                                         f"({'≤' if ok else '>'} erlaubt {limit:g} mm)")
        else:
            ok = res["volumen"] <= asm["GRENZE_MM3"]
            art, text = "ueberschneidung", (f"{paar}: {res['volumen']:.2f} mm³ "
                                            f"({'≤' if ok else '>'} {asm['GRENZE_MM3']:g} mm³)")
        ueber.append({"a": a["name"], "b": b["name"], "volumen_mm3": round(res["volumen"], 3),
                      "tiefe_mm": None if res["tiefe"] is None else round(res["tiefe"], 3),
                      "erlaubt_tiefe_mm": limit, "art": art, "ok": ok, "stl": f"{OUT_SUB}/{stl.name}",
                      "bbox": _bbox(res["inter"]).round(3).tolist()})
        checks.append({"pose": pname, "paar": paar, "art": art, "ok": ok, "text": text,
                       "volumen_mm3": round(res["volumen"], 3),
                       "tiefe_mm": None if res["tiefe"] is None else round(res["tiefe"], 3)})
    return ueber, abst, checks


def check_state(project: str, pose_index: int, verschiebung: dict) -> dict:
    """Exakte Prüfung einer im Studio bewegten Stellung.

    verschiebung = {Körper: 16 Werte (4×4 zeilenweise, Welt): Lage in der Pose -> neue Lage}.
    Schreibt die Überschneidungen nach out/assembly/live__*.stl und gibt sie wie eine Pose zurück."""
    d = project_dir(project)
    asm = load_assembly(d)
    sub = d / "out" / OUT_SUB
    sub.mkdir(parents=True, exist_ok=True)
    for f in sub.glob("live__*.stl"):
        try:
            f.unlink()
        except OSError:
            pass
    _, shapes, _ = _load_parts(asm, d, None)
    posen = list(asm["POSEN"].items())
    pname, pvals = posen[int(pose_index)]
    tf_cache: dict = {}
    insts, _ = _pose_bodies(asm, dict(pvals or {}), shapes, None, {}, tf_cache)
    # Lage wie in assembly.json (Posen mit Gelenkwerten sind dort schon gelöst)
    aj = json.loads((d / "out" / "assembly.json").read_text(encoding="utf-8"))
    pj = aj["posen"][int(pose_index)]
    mats = {i["name"]: i["matrix"] for i in pj["instanzen"]}
    mats.update({g["name"]: g["matrix"] for g in pj["geister"]})
    for it in insts:
        D = None
        if it["name"] in mats:
            D = np.array(mats[it["name"]], float).reshape(4, 4) @ np.linalg.inv(it["tf"].m)
        v = verschiebung.get(it["name"])
        if v is not None:
            V = np.array(v, float).reshape(4, 4)
            D = V if D is None else V @ D
        if D is not None and np.abs(D - np.eye(4)).max() > 1e-9:
            _move(it, D, shapes, tf_cache)
        it.pop("bbox", None)
    t0 = time.time()
    ueber, abst, checks = check_pairs(asm, insts, sub, "live", f"{pname} (bewegt)")
    return {"ok": all(c["ok"] for c in checks), "pose": pname, "ueberschneidungen": ueber,
            "abstaende": sorted(abst, key=lambda x: x["mm"])[:12], "checks": checks,
            "zeit_s": round(time.time() - t0, 1)}


def summary(res: dict, png=None) -> dict:
    """Kurzfassung für Agenten (MCP/CLI)."""
    posen = []
    for p in res["posen"]:
        bad = [c["text"] for c in p["checks"] if not c["ok"]]
        info = [c["text"] for c in p["checks"] if c["ok"]]
        nah = [f"{a['a']} + {a['b']}: {a['mm']:.2f} mm" for a in p["abstaende"] if a["genau"] and 0 < a["mm"] < 0.5]
        posen.append({"pose": p["name"], "ok": p["ok"], "fehler": bad, "kontakt_info": info,
                      "engste_spalte": nah[:6], "zeit_s": p["zeit_s"]})
    return {"ok": res["ok"], "projekt": res["projekt"], "teile": [f"{t['name']} ×{t['anzahl']}" for t in res["teile"]],
            "posen": posen, "hinweise": res["hinweise"], "png": str(png) if png else None,
            "json": str(project_dir(res["projekt"]) / "out" / "assembly.json"), "zeit_s": res["zeit_s"]}


def render_assembly(d: Path, res: dict, pose_index: int = 0) -> Path:
    """out/render.png: Teile farbig, Geister durchscheinend, Überschneidungen rot (eine Pose)."""
    from .render import render
    from .stlio import read_stl

    out = d / "out"
    pose = res["posen"][pose_index]
    meshes = {t["name"]: read_stl(out / t["stl"]) for t in res["teile"]}
    farben = {t["name"]: t["farbe"] for t in res["teile"]}
    parts, legend_done = [], set()
    for inst in pose["instanzen"]:
        tf = Tf(inst["matrix"])
        tris = tf.points(meshes[inst["teil"]].reshape(-1, 3)).reshape(-1, 3, 3)
        if tf.det < 0:
            tris = tris[:, ::-1]
        name = inst["teil"] if inst["teil"] not in legend_done else None
        legend_done.add(inst["teil"])
        parts.append((tris, farben[inst["teil"]], name))
    for g in pose["geister"]:
        tris = Tf(g["matrix"]).points(read_stl(out / g["stl"]).reshape(-1, 3)).reshape(-1, 3, 3)
        parts.append((tris, (*_rgb(g["farbe"]), 0.18), f"{g['name']} (Geist)"))
    for u in pose["ueberschneidungen"]:
        parts.append((read_stl(out / u["stl"]), "#FF1E1E", None))
    status = "ok" if pose["ok"] else "KOLLISION"
    return render(parts, out / "render.png", title=f"{d.name} – {pose['name']} ({status})")


def _rgb(hexcol: str) -> tuple[float, float, float]:
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ---------------------------------------------------------------------------------------------
# Anlegen
# ---------------------------------------------------------------------------------------------
ASSEMBLY_TEMPLATE = '''\
"""{name}: {beschreibung}

Baugruppe aus mehreren Teil-Projekten (nur Ansicht und Kollisionsprüfung, wird nicht gedruckt).
Jedes Teil liegt in seinem out/model.step in Drucklage; platzierung() bildet es ins Baugruppen-System ab.
"""
from build123d import *
from slicetheprompt.assembly import *

# ---- Teile (Projektnamen unter projects/) ----
TEILE = {{
{teile}
}}

# ---- Stellungen (beliebige Werte, an platzierung/geister übergeben) ----
POSEN = {{
    "Standard": {{}},
}}


def platzierung(p):
    """Drucklage -> Baugruppe je Teil: pos(x, y, z), rot(x, y, z), spiegel("XZ"), kombiniert mit *."""
    return {{
{platz}
    }}


def geister(p):
    """Bezugskörper, die nicht gedruckt werden (z. B. Handy, Rohr), als build123d-Körper."""
    return {{}}


# Gelenke (optional): was sich wie bewegen darf – Achsen/Punkte in der Grundstellung (erste Pose)
# GELENKE = [Fest("Teil A"), Drehung("Teil B", "Teil A", achse=(0, 0, 1), punkt=(0, 0, 0), grenzen=(-90, 90))]
GELENKE = []

KONTAKT = {{}}        # {{("Teil A", "Geist"): 0.3}} erlaubte Eindringtiefe in mm (Klemmkontakt)
IGNORIEREN = []      # [("Teil A", "Teil B")] Paare ohne Prüfung
GRENZE_MM3 = 0.5     # kleinere Überschneidungen gelten als Rundungsrest
'''


def new_assembly(name: str, teile: list[str], beschreibung: str = "") -> dict:
    """Projekt `projects/<datum>-<name>/` mit assembly.py-Vorlage anlegen."""
    import datetime as dt

    from .config import PROJECTS_DIR
    from .pipeline import slugify

    d = PROJECTS_DIR / f"{dt.date.today().isoformat()}-{slugify(name)}"
    if d.exists():
        raise FileExistsError(f"{d.name} existiert schon")
    names = []
    for p in teile:
        pd = project_dir(p)
        n = pd.name[11:] if re.match(r"\d{4}-\d{2}-\d{2}-", pd.name) else pd.name
        names.append((n.replace("-", " ").title(), pd.name))
    d.mkdir(parents=True)
    (d / "out").mkdir()
    (d / ASSEMBLY_FILE).write_text(ASSEMBLY_TEMPLATE.format(
        name=name, beschreibung=beschreibung or "Baugruppe",
        teile="\n".join(f'    "{n}": Teil("{p}"),' for n, p in names),
        platz="\n".join(f'        "{n}": pos(0, 0, 0),' for n, _ in names)), encoding="utf-8")
    return {"project": d.name, "assembly": str(d / ASSEMBLY_FILE),
            "next": "assembly.py ausfüllen (platzierung, POSEN, geister), dann build_assembly"}

