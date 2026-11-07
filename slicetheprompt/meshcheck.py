"""Druckbarkeitsprüfung eines Dreiecksnetzes gegen die Regeln aus printer.yaml.

Alles mit numpy/scipy, damit es ohne weitere Geometrie-Bibliotheken läuft.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict

import numpy as np

from .stlio import face_areas, face_normals


# ---------------------------------------------------------------- Topologie

def weld(tris: np.ndarray, decimals: int = 4):
    """Gemeinsame Eckpunkte zusammenführen. Gibt (vertices, faces) zurück."""
    flat = np.round(tris.reshape(-1, 3), decimals)
    verts, inverse = np.unique(flat, axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    return verts, faces


def edge_report(faces: np.ndarray) -> dict:
    """Kanten zählen: wasserdicht = jede Kante genau zweimal, gegenläufig."""
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    und = np.sort(e, axis=1)
    _, counts = np.unique(und, axis=0, return_counts=True)
    open_edges = int(np.sum(counts == 1))
    nonmanifold = int(np.sum(counts > 2))
    # Orientierung: jede gerichtete Kante darf nur einmal vorkommen
    _, dcounts = np.unique(e, axis=0, return_counts=True)
    flipped = int(np.sum(dcounts > 1))
    return {"open_edges": open_edges, "nonmanifold_edges": nonmanifold, "inconsistent_edges": flipped}


def signed_volume(tris: np.ndarray) -> float:
    return float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum() / 6.0)


def components(faces: np.ndarray) -> np.ndarray:
    """Zusammenhängende Körper (über gemeinsame Ecken). Gibt Label je Dreieck zurück."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(faces)
    nv = int(faces.max()) + 1 if n else 0
    rows = np.repeat(np.arange(n), 3)
    cols = faces.reshape(-1)
    m = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, nv)).tocsr()
    adj = m @ m.T
    _, labels = connected_components(adj, directed=False)
    return labels


def face_adjacency_components(faces: np.ndarray, mask: np.ndarray) -> list[np.ndarray]:
    """Gruppen benachbarter Dreiecke (gemeinsame Kante) innerhalb einer Auswahl."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return []
    sub = faces[idx]
    e = np.concatenate([sub[:, [0, 1]], sub[:, [1, 2]], sub[:, [2, 0]]])
    e = np.sort(e, axis=1)
    owner = np.tile(np.arange(len(idx)), 3)
    key = e[:, 0].astype(np.int64) * (int(faces.max()) + 1) + e[:, 1]
    order = np.argsort(key)
    key, owner = key[order], owner[order]
    same = key[1:] == key[:-1]
    a, b = owner[:-1][same], owner[1:][same]
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(idx), len(idx)))
    _, labels = connected_components(g, directed=False)
    return [idx[labels == k] for k in range(labels.max() + 1)]


# ---------------------------------------------------------------- Wandstärke

def _ray_hits(origins, dirs, tris, chunk=None, return_idx=False):
    """Kleinster positiver Abstand Strahl→Dreieck (Möller-Trumbore), vektorisiert.
    return_idx=True: zusätzlich Index des getroffenen Dreiecks (−1 = kein Treffer)."""
    if chunk is None:  # Speicher begrenzen: ca. 3 Mio. Strahl-Dreieck-Paare je Block
        chunk = max(1, int(3_000_000 / max(len(tris), 1)))
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    out = np.full(len(origins), np.inf)
    idx = np.full(len(origins), -1, dtype=np.int64)
    for s in range(0, len(origins), chunk):
        o = origins[s:s + chunk][:, None, :]
        d = dirs[s:s + chunk][:, None, :]
        p = np.cross(d, e2[None])
        det = np.einsum("ijk,jk->ij", p, e1)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = o - v0[None]
        u = np.einsum("ijk,ijk->ij", tv, p) * inv
        q = np.cross(tv, e1[None])
        v = np.einsum("ijk,ijk->ij", d, q) * inv
        t = np.einsum("ijk,jk->ij", q, e2) * inv
        hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-6)
        t = np.where(hit, t, np.inf)
        out[s:s + chunk] = t.min(axis=1)
        if return_idx:
            j = t.argmin(axis=1)
            idx[s:s + chunk] = np.where(np.isfinite(out[s:s + chunk]), j, -1)
    return (out, idx) if return_idx else out


def _ray_hits_local(origins, dirs, tris, maxlen=8.0, cell=6.0, return_idx=False):
    """Wie _ray_hits, aber je Gitterzelle nur Dreiecke in Reichweite (maxlen) prüfen."""
    tmin = tris.min(axis=1)
    tmax = tris.max(axis=1)
    keys = np.floor(origins / cell).astype(np.int64)
    out = np.full(len(origins), np.inf)
    idx = np.full(len(origins), -1, dtype=np.int64)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    for k in range(len(uniq)):
        sel = np.nonzero(inv == k)[0]
        lo = uniq[k] * cell - maxlen
        hi = (uniq[k] + 1) * cell + maxlen
        cand = np.all(tmax >= lo, axis=1) & np.all(tmin <= hi, axis=1)
        if not cand.any():
            continue
        if return_idx:
            d, j = _ray_hits(origins[sel], dirs[sel], tris[cand], return_idx=True)
            out[sel] = d
            idx[sel] = np.where(j >= 0, np.nonzero(cand)[0][np.maximum(j, 0)], -1)
        else:
            out[sel] = _ray_hits(origins[sel], dirs[sel], tris[cand])
    return (out, idx) if return_idx else out


def _inside(points, tris) -> np.ndarray:
    """Liegt der Punkt im Körper? Zählt Schnitte eines Strahls nach +z (gerade = aussen)."""
    if len(points) == 0:
        return np.zeros(0, bool)
    tmin = tris.min(axis=1)
    tmax = tris.max(axis=1)
    v0, e1, e2 = tris[:, 0], tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    d = np.array([0.0, 0.0, 1.0])
    out = np.zeros(len(points), bool)
    for i, p in enumerate(points):
        c = ((tmin[:, 0] <= p[0]) & (tmax[:, 0] >= p[0]) & (tmin[:, 1] <= p[1]) & (tmax[:, 1] >= p[1])
             & (tmax[:, 2] >= p[2]))
        if not c.any():
            continue
        a, b, e = v0[c], e1[c], e2[c]
        pv = np.cross(d, e)
        det = np.einsum("ij,ij->i", pv, b)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = p - a
        u = np.einsum("ij,ij->i", tv, pv) * inv
        q = np.cross(tv, b)
        v = q[:, 2] * inv
        t = np.einsum("ij,ij->i", q, e) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
        out[i] = int(hit.sum()) % 2 == 1
    return out


# Klassen dünner Stellen (unter min_wall)
WAND, KANTE, DETAIL, BEWUSST = 0, 1, 2, 3
KLASSEN = {WAND: "wand", KANTE: "kante", DETAIL: "detail", BEWUSST: "bewusst"}


def classify_thin(tris, pts, dirs, src_idx, hit_idx, rules, R=None, boxes=None) -> np.ndarray:
    """Ordnet dünne Stellen ein (Punkte/Strahlen in Modellkoordinaten):
    KANTE   – der Strahl trifft eine schräg anstossende Fläche (Winkel > ~18°): spitze Aussenkante, keine Wand;
    DETAIL  – höchstens 3 Schichten hoch und darunter Vollmaterial (Gravur-Stege, Rippen auf einer Platte);
    BEWUSST – liegt in einem Feature mit role="duenn" (Norm-Profil, Gitter, gewünschte dünne Wand);
    WAND    – alles andere: echte zu dünne Wand.
    R: Drehung Modell → Drucklage (für „unten“), boxes: [(min3, max3), …] in Modellkoordinaten."""
    n = len(pts)
    cls = np.full(n, WAND)
    if n == 0:
        return cls
    normals = face_normals(tris)
    ok_hit = hit_idx >= 0
    dot = np.full(n, -1.0)
    dot[ok_hit] = np.einsum("ij,ij->i", normals[src_idx[ok_hit]], normals[hit_idx[ok_hit]])
    cls[dot > -0.95] = KANTE
    R = np.eye(3) if R is None else np.asarray(R, float)
    tris_p = tris @ R.T
    depth = 3 * float(rules.get("layer_height", 0.2)) + 0.1
    rest = np.nonzero(cls == WAND)[0]
    if len(rest):
        q = (pts[rest] - dirs[rest] * 0.1) @ R.T          # knapp ausserhalb der Wand
        q[:, 2] -= depth                                   # 3 Schichten tiefer
        cls[rest[_inside(q, tris_p)]] = DETAIL
    for lo, hi in boxes or []:
        lo, hi = np.asarray(lo, float) - 0.3, np.asarray(hi, float) + 0.3
        cls[np.all((pts >= lo) & (pts <= hi), axis=1)] = BEWUSST
    return cls


def wall_thickness(tris: np.ndarray, samples: int = 1500, seed: int = 0, max_len: float = 8.0) -> dict:
    """Wandstärke: von Flächenpunkten nach innen (gegen die Normale) bis zur Gegenwand."""
    areas = face_areas(tris)
    normals = face_normals(tris)
    keep = areas > 1e-9
    idx_all = np.nonzero(keep)[0]
    if len(idx_all) == 0:
        return {"min": None, "p1": None, "samples": 0}
    rng = np.random.default_rng(seed)
    p = areas[idx_all] / areas[idx_all].sum()
    pick = rng.choice(idx_all, size=samples, p=p)   # auch grobe Netze (wenige grosse Dreiecke) dicht abtasten
    # Zufallspunkt im Dreieck
    r1, r2 = rng.random(len(pick)), rng.random(len(pick))
    flip = r1 + r2 > 1
    r1[flip], r2[flip] = 1 - r1[flip], 1 - r2[flip]
    t = tris[pick]
    pts = t[:, 0] + r1[:, None] * (t[:, 1] - t[:, 0]) + r2[:, None] * (t[:, 2] - t[:, 0])
    d = -normals[pick]
    origins = pts + d * 1e-4
    dist, hit = _ray_hits_local(origins, d, tris, maxlen=max_len, return_idx=True)
    dist = np.minimum(dist, max_len)  # dicker als max_len zählt als max_len
    if len(dist) == 0:
        return {"min": None, "p1": None, "samples": 0}
    return {
        "min": round(float(dist.min()), 3),
        "p1": round(float(np.percentile(dist, 1)), 3),
        "p5": round(float(np.percentile(dist, 5)), 3),
        "samples": int(len(dist)),
        "_dist": dist, "_pts": pts, "_dirs": d, "_src": pick, "_hit": hit,
    }


def _wall_checks(rep, tris, rules, prefix="", R=None, boxes=None, wand_soll=None) -> dict:
    """Wandstärke prüfen und dünne Stellen einordnen (siehe classify_thin).
    Fehler nur für echte Wände; spitze Kanten und Oberflächendetails als Warnung, bewusst Dünnes als Info."""
    wt = wall_thickness(tris)
    dist = wt.pop("_dist", None)
    pts, dirs, src, hit = (wt.pop(k, None) for k in ("_pts", "_dirs", "_src", "_hit"))
    if wt["p1"] is None:
        return wt
    mw = rules["min_wall"]
    thin = np.nonzero(dist < mw)[0]
    cls = np.full(len(dist), WAND)
    cls[thin] = classify_thin(tris, pts[thin], dirs[thin], src[thin], hit[thin], rules, R, boxes)
    wall = dist[cls == WAND]
    p1 = float(np.percentile(wall, 1)) if len(wall) else float(mw)
    p5 = float(np.percentile(wall, 5)) if len(wall) else float(mw)
    wt.update({"p1_wand": round(p1, 3), "p5_wand": round(p5, 3), "share_below_min": round(len(thin) / len(dist), 4),
               "duenn_nach_klasse": {KLASSEN[k]: int(((cls == k) & (dist < mw)).sum()) for k in KLASSEN}})
    rep.add(f"{prefix}Mindestwandstärke (1. Perzentil)", p1 >= mw * 0.98, f"{p1:.2f} mm", f"≥ {mw} mm")
    fw = float(rules["functional_wall"])
    if wand_soll:
        fw = min(fw, float(wand_soll))
    rep.add(f"{prefix}Wandstärke Funktionsteil (5. Perzentil)", p5 >= fw * 0.98, f"{p5:.2f} mm",
            f"≥ {fw:g} mm" + (" (spec: wand_soll)" if wand_soll else ""), severity="warn")
    edge_sev = "warn" if ((cls == KANTE) & (dist < mw)).mean() >= 0.02 else "info"   # vereinzelt = kosmetisch
    for k, rule, limit, sev in ((KANTE, "Spitze Kanten dünner als min_wall", "Kante stumpf (Fase) oder ≥ 90°", edge_sev),
                                (DETAIL, "Oberflächendetails dünner als min_wall (≤ 3 Schichten)",
                                 "druckt meist als 1 Linie; Stege breiter oder Teilung grösser", "warn"),
                                (BEWUSST, "Bewusst dünn (role=\"duenn\")", "vom Modell so gewollt", "info")):
            sel = dist[(cls == k) & (dist < mw)]
            if len(sel):
                rep.add(f"{prefix}{rule}", False, f"{len(sel)} Stellen, ab {sel.min():.2f} mm", limit, severity=sev)
    return wt


# ---------------------------------------------------------------- Bauteil auf dem Bett

def place_on_bed(tris: np.ndarray, bed_xy=(260.0, 260.0)) -> np.ndarray:
    """Auf z = 0 legen und in XY auf die Bettmitte zentrieren."""
    mn, mx = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    shift = np.array([bed_xy[0] / 2 - (mn[0] + mx[0]) / 2, bed_xy[1] / 2 - (mn[1] + mx[1]) / 2, -mn[2]])
    return tris + shift


def _rot(axis: str, deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


ORIENTATIONS = {
    "wie modelliert": np.eye(3),
    "kopfüber (X 180°)": _rot("x", 180),
    "X +90°": _rot("x", 90),
    "X -90°": _rot("x", -90),
    "Y +90°": _rot("y", 90),
    "Y -90°": _rot("y", -90),
}


def rotate(tris: np.ndarray, R: np.ndarray) -> np.ndarray:
    return tris @ R.T


def _edge_faces(faces: np.ndarray) -> dict:
    """Ungerichtete Kante -> Liste der angrenzenden Dreiecke."""
    out: dict = {}
    for fi, f in enumerate(faces):
        for a_, b_ in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            key = (a_, b_) if a_ < b_ else (b_, a_)
            out.setdefault(key, []).append(fi)
    return out


def _bridge_span(tris, faces, grp, emap) -> float:
    """Spannweite einer nach unten zeigenden ebenen Fläche: 2 × grösster Abstand
    eines Flächenpunkts zur nächsten gestützten Randkante (Wand darunter)."""
    in_grp = set(int(g) for g in grp)
    seg = []
    for fi in grp:
        f = faces[fi]
        for a_, b_ in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            key = (a_, b_) if a_ < b_ else (b_, a_)
            others = [o for o in emap.get(key, []) if o not in in_grp]
            if not others:
                continue
            ez = tris[fi][:, 2].mean()
            if any(tris[o][:, 2].mean() < ez - 1e-6 for o in others):
                # Eckpunkte der Kante aus dem Dreieck holen
                vi = list(f)
                p = tris[fi][vi.index(a_)]
                q = tris[fi][vi.index(b_)]
                seg.append((p[:2], q[:2]))
    if not seg:
        return float("inf")
    P = np.array([s[0] for s in seg])
    Q = np.array([s[1] for s in seg])
    t = tris[grp]
    samples = np.concatenate([t.reshape(-1, 3)[:, :2], t.mean(axis=1)[:, :2]])
    # Zusätzliche Punkte im Inneren grosser Dreiecke
    w = np.random.default_rng(1).dirichlet([1, 1, 1], size=(len(t), 4))
    samples = np.concatenate([samples, np.einsum("nkj,njd->nkd", w, t[:, :, :2]).reshape(-1, 2)])
    d = Q - P
    L2 = np.maximum((d ** 2).sum(1), 1e-12)
    best = np.full(len(samples), np.inf)
    for s in range(0, len(samples), 2048):
        X = samples[s:s + 2048][:, None, :]
        tt = np.clip(((X - P[None]) * d[None]).sum(-1) / L2[None], 0, 1)
        proj = P[None] + tt[..., None] * d[None]
        dist = np.linalg.norm(X - proj, axis=-1).min(1)
        best[s:s + 2048] = dist
    return float(2 * best.max())


def overhang_stats(tris: np.ndarray, max_overhang_deg: float, max_bridge: float,
                   faces: np.ndarray | None = None) -> dict:
    """Flächen, die ohne Stütze kritisch sind, und Brücken-Spannweiten."""
    n = face_normals(tris)
    a = face_areas(tris)
    zmin = tris[:, :, 2].min()
    on_bed = (tris[:, :, 2].max(axis=1) < zmin + 0.05) & (n[:, 2] < -0.99)
    # Überhang: Winkel zur Senkrechten grösser als erlaubt -> n_z < -sin(winkel)
    # +1° Toleranz: eine exakte 45°-Fase ist zulässig (Rundungsfehler der Tessellierung)
    thr = -math.sin(math.radians(max_overhang_deg + 1.0))
    down = (n[:, 2] < thr) & ~on_bed
    # Mehrere Körper: Unterseiten, die auf einem anderen Körper aufliegen, gelten als gestützt
    up_idx = np.nonzero(n[:, 2] > 0.5)[0]
    if down.any() and len(up_idx):
        di = np.nonzero(down)[0]
        cen = tris[di].mean(axis=1) + np.array([0, 0, 0.05])
        d = _ray_hits_local(cen, np.tile([0.0, 0.0, -1.0], (len(di), 1)), tris[up_idx], maxlen=0.2)
        supported = np.zeros(len(tris), bool)
        supported[di[d <= 0.1]] = True
        down &= ~supported
    over = down
    flat_down = (n[:, 2] < -0.99) & down
    res = {
        "bed_contact_mm2": round(float(a[on_bed].sum()), 1),
        "overhang_mm2": round(float(a[over].sum()), 1),
        "bridges": [],
    }
    if faces is not None and flat_down.any():
        emap = _edge_faces(faces)
        for grp in face_adjacency_components(faces, flat_down):
            area = float(a[grp].sum())
            if area < 1.0:
                continue
            pts = tris[grp].reshape(-1, 3)
            span = _bridge_span(tris, faces, grp, emap)
            res["bridges"].append({
                "z": round(float(pts[:, 2].mean()), 2),
                "span_mm": round(span, 1),
                "area_mm2": round(area, 1),
                "ok": span <= max_bridge,
            })
        res["bridges"].sort(key=lambda b: -b["span_mm"])
    # Überhang ohne die Brückenflächen, die als ok gelten
    if res["bridges"] and faces is not None:
        ok_area = sum(b["area_mm2"] for b in res["bridges"] if b["ok"])
        res["overhang_mm2_without_ok_bridges"] = round(max(res["overhang_mm2"] - ok_area, 0.0), 1)
    else:
        res["overhang_mm2_without_ok_bridges"] = res["overhang_mm2"]
    return res


def best_orientation(tris: np.ndarray, rules: dict) -> tuple[str, np.ndarray, list[dict]]:
    """Wählt aus 6 achsparallelen Lagen die mit wenig Überhang und viel Bettkontakt."""
    verts, faces = weld(tris)
    scores = []
    for name, R in ORIENTATIONS.items():
        t = rotate(tris, R)
        st = overhang_stats(t, rules["max_overhang_deg"], rules["max_bridge"], faces)
        h = float(t[:, :, 2].max() - t[:, :, 2].min())
        score = st["overhang_mm2_without_ok_bridges"] - 0.5 * st["bed_contact_mm2"] + 0.2 * h
        scores.append({"orientation": name, "score": round(score, 1), "height_mm": round(h, 1), **{
            k: st[k] for k in ("bed_contact_mm2", "overhang_mm2_without_ok_bridges")}})
    scores.sort(key=lambda s: s["score"])
    best = scores[0]["orientation"]
    return best, ORIENTATIONS[best], scores


# ---------------------------------------------------------------- Gesamtprüfung

@dataclass
class Check:
    rule: str
    ok: bool
    value: str
    limit: str = ""
    severity: str = "error"  # error | warn | info

    def as_dict(self):
        return asdict(self)


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def add(self, rule, ok, value, limit="", severity="error"):
        self.checks.append(Check(rule, bool(ok), str(value), str(limit), severity))

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    def as_dict(self):
        return {
            "passed": self.passed,
            "errors": [c.as_dict() for c in self.checks if not c.ok and c.severity == "error"],
            "warnings": [c.as_dict() for c in self.checks if not c.ok and c.severity == "warn"],
            "checks": [c.as_dict() for c in self.checks],
            "metrics": self.metrics,
        }


def analyse(tris: np.ndarray, cfg: dict, spec: dict | None = None, print_orientation: bool = False,
            walls: bool = True, R=None, thin_boxes=None) -> Report:
    """Prüft ein Netz. print_orientation=True: Netz liegt schon in Drucklage.
    walls=False: Wandstärke auslassen (wird schon an anderer Stelle geprüft). R: Drehung Modell → Drucklage,
    thin_boxes: Bereiche mit role="duenn" (Modellkoordinaten) – beides für die Einordnung dünner Stellen."""
    rules = cfg["rules"]
    usable = cfg["printer"]["usable"]
    rep = Report()
    pts = tris.reshape(-1, 3)
    mn, mx = pts.min(0), pts.max(0)
    size = mx - mn
    verts, faces = weld(tris)
    edges = edge_report(faces)
    vol = signed_volume(tris)
    labels = components(faces)
    n_bodies = int(labels.max()) + 1 if len(labels) else 0

    rep.metrics.update({
        "triangles": int(len(tris)),
        "size_mm": [round(float(s), 3) for s in size],
        "bbox_min": [round(float(v), 3) for v in mn],
        "volume_mm3": round(abs(vol), 1),
        "bodies": n_bodies,
        **edges,
    })

    rep.add("wasserdicht (keine offenen Kanten)", edges["open_edges"] == 0, str(edges["open_edges"]), "0")
    rep.add("manifold (keine Kante an >2 Flächen)", edges["nonmanifold_edges"] == 0,
            str(edges["nonmanifold_edges"]), "0")
    rep.add("Normalen einheitlich", edges["inconsistent_edges"] == 0 and vol > 0,
            f"{edges['inconsistent_edges']} Kanten, Volumen {'positiv' if vol > 0 else 'negativ'}", "0, positiv")
    want = int(spec["koerper"]) if spec and spec.get("koerper") else None   # spec.yaml: koerper: 3 (getrennte Teile)
    if want:
        rep.add("Anzahl Körper", n_bodies == want, str(n_bodies), f"{want} (spec.yaml: koerper)")
    else:
        rep.add("Anzahl Körper", n_bodies == 1, str(n_bodies), "1 (mehrere gewollt? spec.yaml: koerper: n)",
                severity="warn")

    # Bauraum: in jeder Lage prüfen, wie es tatsächlich gedruckt wird
    fits = size[0] <= usable["x"] and size[1] <= usable["y"] and size[2] <= usable["z"]
    fits_rot = size[1] <= usable["x"] and size[0] <= usable["y"] and size[2] <= usable["z"]
    rep.add("passt in den Bauraum", bool(fits or fits_rot),
            "{:.1f} × {:.1f} × {:.1f} mm".format(*size),
            "{x} × {y} × {z} mm".format(**usable))

    # Lage: model.py soll in Drucklage mit Unterkante auf z = 0 modellieren
    if not print_orientation and mn[2] < -0.01:
        rep.add("Teil beginnt auf z = 0", False, f"z_min = {mn[2]:.2f} mm",
                "≥ 0 – Fläche nach −Z extrudiert? Polygon-Punkte gegen den Uhrzeiger angeben", severity="warn")

    # Wandstärke (mit Einordnung dünner Stellen)
    if walls:
        rep.metrics["wall_thickness"] = _wall_checks(rep, tris, rules, R=R, boxes=thin_boxes,
                                                     wand_soll=(spec or {}).get("wand_soll"))

    # Spezifikation
    if spec and spec.get("bbox"):
        soll = [float(v) for v in spec["bbox"]]
        tol = float(spec.get("bbox_tol", 0.05))
        diffs = [abs(a - b) for a, b in zip(size, soll)]
        rep.add("Aussenmasse wie Spezifikation", max(diffs) <= tol,
                "{:.3f} × {:.3f} × {:.3f}".format(*size),
                "{} ± {}".format(" × ".join(f"{v:g}" for v in soll), tol))

    # Druckorientierung
    if print_orientation:
        st = overhang_stats(tris, rules["max_overhang_deg"], rules["max_bridge"], faces)
        rep.metrics["print"] = st
        rep.add("Bettkontakt", st["bed_contact_mm2"] >= rules["min_bed_contact_mm2"],
                f"{st['bed_contact_mm2']:.0f} mm²", f"≥ {rules['min_bed_contact_mm2']} mm²", severity="warn")
        bad = [b for b in st["bridges"] if not b["ok"]]
        rep.add("Brücken ohne Stütze", not bad,
                ", ".join(f"{b['span_mm']} mm bei z={b['z']}" for b in bad) or "keine zu lang",
                f"≤ {rules['max_bridge']} mm", severity="warn")
        rep.add("Überhang > {}° (ohne zulässige Brücken)".format(rules["max_overhang_deg"]),
                st["overhang_mm2_without_ok_bridges"] < 1.0,
                f"{st['overhang_mm2_without_ok_bridges']:.0f} mm²", "0 mm² oder Stützen", severity="warn")
        h = float(size[2])
        footprint = float(min(size[0], size[1]))
        rep.add("Standfestigkeit (Höhe/Fussbreite)", h <= 4 * max(footprint, 1e-6),
                f"{h / max(footprint, 1e-6):.1f}", "≤ 4", severity="warn")
    return rep


def _inlay_checks(rep, name, tp, rules) -> dict:
    """Farb-Einlage/Schrift (Netz in Drucklage): Tiefe (Z) und Strichbreite (XY) getrennt.
    Fehler unter der Druckgrenze (2 Schichten bzw. 1 Linie), Warnung unter dem Richtwert aus printer.yaml."""
    lh, lw = float(rules.get("layer_height", 0.2)), float(rules.get("line_width", 0.42))
    want_d = float(rules.get("inlay_depth", rules.get("min_inlay", 0.6)))
    want_s = float(rules.get("inlay_stroke", 0.8))
    pts = tp.reshape(-1, 3)
    depth = float(pts[:, 2].max() - pts[:, 2].min())
    rep.add(f"[{name}] Einlage-Tiefe", depth >= 2 * lh - 1e-3, f"{depth:.2f} mm", f"≥ {2 * lh:.1f} mm (2 Schichten)")
    if depth >= 2 * lh - 1e-3:
        rep.add(f"[{name}] Einlage-Tiefe Richtwert", depth >= want_d - 1e-3, f"{depth:.2f} mm", f"≥ {want_d} mm",
                severity="warn")
    wt = wall_thickness(tp)
    dist, dirs, src, hit = (wt.pop(k, None) for k in ("_dist", "_dirs", "_src", "_hit"))
    wt.pop("_pts", None)
    if dist is None:
        return wt
    nrm = face_normals(tp)
    dot = np.full(len(dist), -1.0)
    ok = hit >= 0
    dot[ok] = np.einsum("ij,ij->i", nrm[src[ok]], nrm[hit[ok]])
    # Strahlen quer = Strichbreite; Buchstabenecken (Strahl trifft schräge Nachbarfläche) zählen nicht
    side = dist[(np.abs(dirs[:, 2]) < 0.3) & (dot <= -0.95)]
    if len(side):
        s1 = float(np.percentile(side, 1))
        wt["strich_p1"] = round(s1, 3)
        rep.add(f"[{name}] Strichbreite (1. Perzentil)", s1 >= lw * 0.98, f"{s1:.2f} mm", f"≥ {lw} mm (1 Linie)")
        if s1 >= lw * 0.98:
            rep.add(f"[{name}] Strichbreite Richtwert", s1 >= want_s * 0.98, f"{s1:.2f} mm", f"≥ {want_s} mm",
                    severity="warn")
    return wt


def analyse_bodies(bodies: dict, cfg: dict, spec: dict | None = None, print_bodies: dict | None = None,
                   R=None, thin_boxes=None) -> Report:
    """Mehrteiliges Modell: Topologie und Wandstärke je Körper, Bauraum/Spezifikation/Überhänge fürs Ganze.
    bodies / print_bodies: {name: Dreiecke} in Modell- bzw. Drucklage."""
    rules = cfg["rules"]
    rep = Report()
    vols = {n: abs(signed_volume(t)) for n, t in bodies.items()}
    main = max(vols, key=vols.get)
    per = {}
    for name, t in bodies.items():
        verts, faces = weld(t)
        e = edge_report(faces)
        vol = signed_volume(t)
        rep.add(f"[{name}] wasserdicht und manifold", e["open_edges"] == 0 and e["nonmanifold_edges"] == 0,
                f"{e['open_edges']} offen, {e['nonmanifold_edges']} mehrfach", "0, 0")
        rep.add(f"[{name}] Normalen einheitlich", e["inconsistent_edges"] == 0 and vol > 0,
                f"{e['inconsistent_edges']} Kanten", "0")
        if name == main:
            wt = _wall_checks(rep, t, rules, prefix=f"[{name}] ", R=R, boxes=thin_boxes,
                              wand_soll=(spec or {}).get("wand_soll"))
        else:
            tp = (print_bodies or {}).get(name)
            tp = t if tp is None else tp
            wt = _inlay_checks(rep, name, tp, rules)
        per[name] = {"volume_mm3": round(abs(vol), 1), "triangles": int(len(t)), "wall_thickness": wt}
    union = np.concatenate(list(bodies.values()))
    pts = union.reshape(-1, 3)
    size = pts.max(0) - pts.min(0)
    usable = cfg["printer"]["usable"]
    fits = size[0] <= usable["x"] and size[1] <= usable["y"] and size[2] <= usable["z"]
    fits_rot = size[1] <= usable["x"] and size[0] <= usable["y"] and size[2] <= usable["z"]
    rep.add("passt in den Bauraum", bool(fits or fits_rot), "{:.1f} × {:.1f} × {:.1f} mm".format(*size),
            "{x} × {y} × {z} mm".format(**usable))
    if spec and spec.get("bbox"):
        soll = [float(v) for v in spec["bbox"]]
        tol = float(spec.get("bbox_tol", 0.05))
        diffs = [abs(float(a) - b) for a, b in zip(size, soll)]
        rep.add("Aussenmasse wie Spezifikation", max(diffs) <= tol,
                "{:.3f} × {:.3f} × {:.3f}".format(*size),
                "{} ± {}".format(" × ".join(f"{v:g}" for v in soll), tol))
    rep.metrics.update({"size_mm": [round(float(s), 3) for s in size],
                        "bbox_min": [round(float(v), 3) for v in pts.min(0)],
                        "volume_mm3": round(sum(vols.values()), 1), "bodies": len(bodies), "main_body": main,
                        "per_body": per, "wall_thickness": per[main]["wall_thickness"]})
    if print_bodies:
        pu = np.concatenate(list(print_bodies.values()))
        _, faces = weld(pu)
        st = overhang_stats(pu, rules["max_overhang_deg"], rules["max_bridge"], faces)
        rep.metrics["print"] = st
        rep.add("Bettkontakt", st["bed_contact_mm2"] >= rules["min_bed_contact_mm2"],
                f"{st['bed_contact_mm2']:.0f} mm²", f"≥ {rules['min_bed_contact_mm2']} mm²", severity="warn")
        bad = [b for b in st["bridges"] if not b["ok"]]
        rep.add("Brücken ohne Stütze", not bad,
                ", ".join(f"{b['span_mm']} mm bei z={b['z']}" for b in bad) or "keine zu lang",
                f"≤ {rules['max_bridge']} mm", severity="warn")
        rep.add("Überhang > {}° (ohne zulässige Brücken)".format(rules["max_overhang_deg"]),
                st["overhang_mm2_without_ok_bridges"] < 1.0,
                f"{st['overhang_mm2_without_ok_bridges']:.0f} mm²", "0 mm² oder Stützen", severity="warn")
    return rep


# ---------------------------------------------------------------- 3D-Fehlerdiagnostik & Feature-Tagging

def _slug(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "koerper"


def diagnostics_3d(print_tris: np.ndarray, cfg: dict, build_data: dict | None = None,
                   print_info: dict | None = None) -> dict:
    """Berechnet dreiecksgenaue 3D-Problemzonen, Kantenfehler und Feature-Bounding-Boxes in Druckbett-
    und Modellkoordinaten inkl. Transformationsmatrix R und Shift s."""
    rules = cfg.get("rules", {})
    min_wall = float(rules.get("min_wall", 0.84))
    max_overhang_deg = float(rules.get("max_overhang_deg", 45.0))
    max_bridge = float(rules.get("max_bridge", 10.0))

    if print_info and "rotation_matrix" in print_info and "shift" in print_info:
        R = np.array(print_info["rotation_matrix"], dtype=float)
        s = np.array(print_info["shift"], dtype=float)
    else:
        R = np.eye(3)
        s = np.zeros(3)

    def _to_model(pts_p: np.ndarray) -> np.ndarray:
        return (pts_p - s) @ R

    def _to_print(pts_m: np.ndarray) -> np.ndarray:
        return pts_m @ R.T + s

    def _calc_bbox_and_center(tri_indices_list: list[int] | np.ndarray):
        pts_p = print_tris[tri_indices_list].reshape(-1, 3)
        mn_p = pts_p.min(axis=0)
        mx_p = pts_p.max(axis=0)
        bbox_p = [round(float(v), 4) for v in np.concatenate([mn_p, mx_p])]
        center_p = [round(float(v), 4) for v in (mn_p + mx_p) / 2.0]

        pts_m = _to_model(pts_p)
        mn_m = pts_m.min(axis=0)
        mx_m = pts_m.max(axis=0)
        bbox_m = [round(float(v), 4) for v in np.concatenate([mn_m, mx_m])]
        center_m = [round(float(v), 4) for v in (mn_m + mx_m) / 2.0]
        return bbox_p, bbox_m, center_p, center_m

    if len(print_tris) == 0:
        return {
            "transform": {
                "rotation_matrix": R.tolist(),
                "shift": [round(float(v), 4) for v in s],
            },
            "thin_walls": [],
            "overhangs": [],
            "bad_bridges": [],
            "open_edges": [],
            "floating_parts": [],
            "features": [],
        }

    verts, faces = weld(print_tris)
    areas = face_areas(print_tris)
    normals = face_normals(print_tris)
    valid_tri_mask = areas > 1e-9

    # 1. Dünne Wände
    thin_walls = []
    thin_threshold = min_wall
    centers = print_tris.mean(axis=1)
    d = -normals
    origins = centers + d * 1e-4
    # Nur „dünner als min_wall?“ zählt: Suche auf diese Reichweite begrenzen (gleiches Ergebnis, ~100× schneller)
    dist, hit = _ray_hits_local(origins, d, print_tris, maxlen=thin_threshold + 0.05, cell=2.0, return_idx=True)
    thin_mask = (dist < thin_threshold) & valid_tri_mask

    # Bereiche mit role="duenn" in Drucklage (für die Einordnung)
    thin_boxes_p = []
    for feat in (build_data or {}).get("features", []):
        if feat.get("role") == "duenn" and feat.get("bbox_min"):
            (x0, y0, z0), (x1, y1, z1) = feat["bbox_min"], feat["bbox_max"]
            cp = _to_print(np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float))
            thin_boxes_p.append((cp.min(axis=0), cp.max(axis=0)))
    labels_thin = {
        WAND: ("error", "Wand zu dünn ({:.2f} mm < {:.2f} mm)"),
        KANTE: ("warn", "Spitze Kante ({:.2f} mm < {:.2f} mm) – stumpf machen"),
        DETAIL: ("warn", "Oberflächendetail ({:.2f} mm < {:.2f} mm, ≤ 3 Schichten)"),
        BEWUSST: ("info", "Bewusst dünn ({:.2f} mm < {:.2f} mm, role=duenn)"),
    }

    if faces is not None and thin_mask.any():
        for grp in face_adjacency_components(faces, thin_mask):
            grp_area = float(areas[grp].sum())
            if grp_area < 0.5:
                continue
            min_thick = float(dist[grp].min())
            sample = grp[np.unique(np.linspace(0, len(grp) - 1, min(12, len(grp))).astype(int))]
            cls = classify_thin(print_tris, centers[sample], d[sample], sample, hit[sample], rules, None, thin_boxes_p)
            k = int(np.bincount(cls, minlength=4).argmax())
            sev, fmt = labels_thin[k]
            bbox_p, bbox_m, cen_p, cen_m = _calc_bbox_and_center(grp)
            thin_walls.append({
                "id": f"thin_{len(thin_walls) + 1}",
                "kind": "thin_wall",
                "klasse": KLASSEN[k],
                "min_mm": round(min_thick, 3),
                "area_mm2": round(grp_area, 2),
                "severity": sev,
                "label": fmt.format(min_thick, thin_threshold),
                "bbox_print": bbox_p,
                "bbox_model": bbox_m,
                "center_print": cen_p,
                "center_model": cen_m,
                "tri_indices": [int(idx) for idx in grp],
            })

    # 2. Überhänge & Brücken
    zmin = print_tris[:, :, 2].min()
    on_bed = (print_tris[:, :, 2].max(axis=1) < zmin + 0.05) & (normals[:, 2] < -0.99)
    thr = -math.sin(math.radians(max_overhang_deg + 1.0))
    down = (normals[:, 2] < thr) & ~on_bed & valid_tri_mask

    up_idx = np.nonzero(normals[:, 2] > 0.5)[0]
    if down.any() and len(up_idx):
        di = np.nonzero(down)[0]
        cen = centers[di] + np.array([0, 0, 0.05])
        d_down = _ray_hits_local(cen, np.tile([0.0, 0.0, -1.0], (len(di), 1)), print_tris[up_idx], maxlen=0.2)
        supported = np.zeros(len(print_tris), bool)
        supported[di[d_down <= 0.1]] = True
        down &= ~supported

    flat_down = (normals[:, 2] < -0.99) & down
    bad_bridges = []
    handled_bridge_tris = np.zeros(len(print_tris), bool)

    if faces is not None and flat_down.any():
        emap = _edge_faces(faces)
        for grp in face_adjacency_components(faces, flat_down):
            grp_area = float(areas[grp].sum())
            if grp_area < 0.5:
                continue
            span = _bridge_span(print_tris, faces, grp, emap)
            handled_bridge_tris[grp] = True
            if span > max_bridge:
                bbox_p, bbox_m, cen_p, cen_m = _calc_bbox_and_center(grp)
                bad_bridges.append({
                    "id": f"bridge_{len(bad_bridges) + 1}",
                    "kind": "bad_bridge",
                    "severity": "warn",
                    "label": f"Brücke zu lang ({span:.1f} mm > {max_bridge:.1f} mm)",
                    "bbox_print": bbox_p,
                    "bbox_model": bbox_m,
                    "center_print": cen_p,
                    "center_model": cen_m,
                    "tri_indices": [int(idx) for idx in grp],
                })

    overhang_mask = down & ~handled_bridge_tris
    overhangs = []
    if faces is not None and overhang_mask.any():
        for grp in face_adjacency_components(faces, overhang_mask):
            grp_area = float(areas[grp].sum())
            if grp_area < 0.2:
                continue
            bbox_p, bbox_m, cen_p, cen_m = _calc_bbox_and_center(grp)
            overhangs.append({
                "id": f"overhang_{len(overhangs) + 1}",
                "kind": "overhang",
                "severity": "warn",
                "label": f"Kritischer Überhang (> {max_overhang_deg:.0f}°)",
                "bbox_print": bbox_p,
                "bbox_model": bbox_m,
                "center_print": cen_p,
                "center_model": cen_m,
                "tri_indices": [int(idx) for idx in grp],
            })

    # 3. Offene Kanten / Nonmanifold
    open_edges = []
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    und = np.sort(e, axis=1)
    uniq, counts = np.unique(und, axis=0, return_counts=True)
    open_edges_mask = (counts == 1) | (counts > 2)
    bad_edges = uniq[open_edges_mask]
    if len(bad_edges) > 0:
        capped_edges = bad_edges[:500]
        p1 = verts[capped_edges[:, 0]]
        p2 = verts[capped_edges[:, 1]]
        edges_segments = [
            [[round(float(c), 4) for c in p1[i]], [round(float(c), 4) for c in p2[i]]]
            for i in range(len(capped_edges))
        ]
        all_pts = np.concatenate([p1, p2])
        mn_p = all_pts.min(axis=0)
        mx_p = all_pts.max(axis=0)
        pts_m = _to_model(all_pts)
        mn_m = pts_m.min(axis=0)
        mx_m = pts_m.max(axis=0)

        edge_set = set((int(min(a, b)), int(max(a, b))) for a, b in capped_edges)
        tri_idxs = []
        for fi, f in enumerate(faces):
            for e0, e1 in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
                if (int(min(e0, e1)), int(max(e0, e1))) in edge_set:
                    tri_idxs.append(fi)
                    break

        open_edges.append({
            "id": "open_edge_1",
            "kind": "open_edge",
            "severity": "error",
            "label": f"{len(bad_edges)} offene / nicht-manifolde Kanten",
            "edges": edges_segments,
            "bbox_print": [round(float(v), 4) for v in np.concatenate([mn_p, mx_p])],
            "bbox_model": [round(float(v), 4) for v in np.concatenate([mn_m, mx_m])],
            "center_print": [round(float(v), 4) for v in (mn_p + mx_p) / 2.0],
            "center_model": [round(float(v), 4) for v in (mn_m + mx_m) / 2.0],
            "tri_indices": tri_idxs,
        })

    # 4. Schwebende Körper
    floating_parts = []
    labels = components(faces)
    n_comp = int(labels.max()) + 1 if len(labels) else 0
    if n_comp > 1:
        z_bed = float(print_tris[:, :, 2].min())
        for k in range(n_comp):
            comp_idx = np.nonzero(labels == k)[0]
            comp_zmin = float(print_tris[comp_idx, :, 2].min())
            if comp_zmin > z_bed + 0.05:
                bbox_p, bbox_m, cen_p, cen_m = _calc_bbox_and_center(comp_idx)
                floating_parts.append({
                    "id": f"float_{len(floating_parts) + 1}",
                    "kind": "floating_part",
                    "severity": "error",
                    "label": f"Schwebender Körper (z_min = {comp_zmin:.2f} mm > {z_bed:.2f} mm)",
                    "bbox_print": bbox_p,
                    "bbox_model": bbox_m,
                    "center_print": cen_p,
                    "center_model": cen_m,
                    "tri_indices": [int(idx) for idx in comp_idx],
                })

    # 5. Features (transformiert)
    features = []
    if build_data and "features" in build_data:
        for feat in build_data["features"]:
            name = feat.get("name", "")
            role = feat.get("role", "feature")
            x0, y0, z0 = feat["bbox_min"]
            x1, y1, z1 = feat["bbox_max"]
            corners_m = np.array([
                [x0, y0, z0], [x0, y0, z1], [x0, y1, z0], [x0, y1, z1],
                [x1, y0, z0], [x1, y0, z1], [x1, y1, z0], [x1, y1, z1],
            ], dtype=float)
            corners_p = _to_print(corners_m)
            mn_p = corners_p.min(axis=0)
            mx_p = corners_p.max(axis=0)
            bbox_p = [round(float(v), 4) for v in np.concatenate([mn_p, mx_p])]
            bbox_m = [round(float(v), 4) for v in [x0, y0, z0, x1, y1, z1]]
            c_m = feat.get("center") or [(x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0]
            center_m = [round(float(v), 4) for v in c_m]
            center_p = [round(float(v), 4) for v in _to_print(np.array(c_m, dtype=float))]

            f_entry = {
                "name": name,
                "role": role,
                "bbox_model": bbox_m,
                "bbox_print": bbox_p,
                "center_model": center_m,
                "center_print": center_p,
            }
            if feat.get("empty_cut"):
                f_entry["empty_cut"] = True
                slug = _slug(name)
                f_entry["ghost_stl"] = f"ghost_{slug}_print.stl"
            if "cutter_volume_mm3" in feat:
                f_entry["cutter_volume_mm3"] = feat["cutter_volume_mm3"]
            if "removed_volume_mm3" in feat:
                f_entry["removed_volume_mm3"] = feat["removed_volume_mm3"]
            if "volume_mm3" in feat:
                f_entry["volume_mm3"] = feat["volume_mm3"]
            features.append(f_entry)

    return {
        "transform": {
            "rotation_matrix": R.tolist(),
            "shift": [round(float(v), 4) for v in s],
        },
        "thin_walls": thin_walls,
        "overhangs": overhangs,
        "bad_bridges": bad_bridges,
        "open_edges": open_edges,
        "floating_parts": floating_parts,
        "features": features,
    }
