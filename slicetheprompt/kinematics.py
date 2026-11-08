"""Kinematik für Baugruppen: Gelenke als Zwangsbedingungen zwischen starren Körpern.

Keine Formeln je Mechanismus: Jedes Gelenk nimmt Freiheitsgrade weg, die möglichen Bewegungen
ergeben sich daraus. Geschlossene Ketten (Kurbeltrieb, Viergelenk) löst derselbe Löser.

Gelenke (alle Achsen/Punkte in Baugruppen-Koordinaten der Grundstellung = erste Pose):

    Fest(a)                         a ist Boden (bewegt sich nie)
    Fest(a, b)                      a starr an b
    Drehung(a, b, achse, punkt)     1 Drehung um die Achse
    Schiebung(a, b, achse)          1 Verschiebung entlang der Achse
    Zylinder(a, b, achse, punkt)    Drehung + Verschiebung auf einer Achse
    Kugel(a, b, punkt)              3 Drehungen um den Punkt
    Ebene(a, b, normale, punkt)     2 Verschiebungen in der Ebene + Drehung um die Normale
    Schraube(a, b, achse, punkt, steigung)   Drehung, Vorschub = steigung je Umdrehung
    Getriebe(gelenk_a, gelenk_b, verhaeltnis)   q_b = verhaeltnis · q_a (Zahnrad, Zahnstange, Riemen)

`grenzen=(min, max)` begrenzt die Gelenkgrösse (mm bzw. Grad, 0 = Grundstellung).

Der Löser (gedämpfte kleinste Quadrate) gibt es gleich in JavaScript (ui/kinematics.js) fürs Studio;
diese Python-Fassung rechnet Posen mit Gelenkwerten in build_assembly und dient als Referenz für Tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

TOL = 1e-7          # mm bzw. Rad: Restfehler der Zwangsbedingungen


# ---------------------------------------------------------------------------------------------
# Gelenk-Typen (Datenklassen für assembly.py)
# ---------------------------------------------------------------------------------------------
@dataclass
class _Gelenk:
    a: str
    b: str | None = None
    achse: tuple = (0.0, 0.0, 1.0)
    punkt: tuple = (0.0, 0.0, 0.0)
    grenzen: tuple | None = None
    name: str | None = None
    typ: str = field(default="", init=False)

    def to_json(self) -> dict:
        d = {"typ": self.typ, "name": self.name or (f"{self.a} – {self.b}" if self.b else self.a),
             "a": self.a, "b": self.b, "achse": _unit(self.achse).tolist(),
             "punkt": [float(v) for v in self.punkt],
             "grenzen": list(self.grenzen) if self.grenzen else None}
        return d


@dataclass
class Fest(_Gelenk):
    def __post_init__(self):
        self.typ = "fest" if self.b else "boden"


@dataclass
class Drehung(_Gelenk):
    def __post_init__(self):
        self.typ = "drehung"


@dataclass
class Schiebung(_Gelenk):
    def __post_init__(self):
        self.typ = "schiebung"


@dataclass
class Zylinder(_Gelenk):
    def __post_init__(self):
        self.typ = "zylinder"


@dataclass
class Kugel(_Gelenk):
    def __post_init__(self):
        self.typ = "kugel"


@dataclass
class Ebene(_Gelenk):
    """`achse` ist die Ebenen-Normale."""
    def __post_init__(self):
        self.typ = "ebene"


@dataclass
class Schraube(_Gelenk):
    steigung: float = 1.0          # mm je Umdrehung (+ = Rechtsgewinde: Vorschub in +achse bei +Drehung)

    def __post_init__(self):
        self.typ = "schraube"

    def to_json(self) -> dict:
        return {**super().to_json(), "steigung": float(self.steigung)}


@dataclass
class Getriebe:
    """Koppelt zwei Gelenke: q_b = verhaeltnis · q_a (q = Grad bei Dreh-/Schraubgelenk, mm bei Schiebung)."""
    gelenk_a: str
    gelenk_b: str
    verhaeltnis: float
    name: str | None = None
    typ: str = field(default="getriebe", init=False)

    def to_json(self) -> dict:
        return {"typ": "getriebe", "name": self.name or f"{self.gelenk_a} → {self.gelenk_b}",
                "gelenk_a": self.gelenk_a, "gelenk_b": self.gelenk_b, "verhaeltnis": float(self.verhaeltnis)}


GELENK_TYPEN = (Fest, Drehung, Schiebung, Zylinder, Kugel, Ebene, Schraube, Getriebe)
# Freiheitsgrade, die ein Gelenk wegnimmt (für Anzeige und Prüfung)
SPERRT = {"boden": 6, "fest": 6, "drehung": 5, "schiebung": 5, "zylinder": 4, "kugel": 3, "ebene": 3,
          "schraube": 5, "getriebe": 1}
# Hauptgrösse je Gelenk: Drehwinkel (Grad) oder Weg (mm)
HAUPT = {"drehung": "winkel", "schraube": "winkel", "zylinder": "weg", "schiebung": "weg"}


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n < 1e-12:
        raise ValueError("Gelenkachse darf nicht Null sein")
    return v / n


def _perp(u: np.ndarray) -> np.ndarray:
    a = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    v = np.cross(u, a)
    return v / np.linalg.norm(v)


def _rotvec(w: np.ndarray) -> np.ndarray:
    th = np.linalg.norm(w)
    if th < 1e-15:
        return np.eye(3)
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


def prepare(joints: list[dict], bodies: list[str]) -> list[dict]:
    """Gelenke prüfen und je Gelenk Hilfsvektor v ⟂ achse ergänzen (Bezug für Winkel)."""
    names = set(bodies)
    out, jn = [], set()
    for j in joints:
        j = dict(j)
        if j["typ"] == "getriebe":
            out.append(j)
            continue
        for k in ("a", "b"):
            if j.get(k) is not None and j[k] not in names:
                raise ValueError(f"Gelenk {j['name']}: unbekannter Körper '{j[k]}' (bekannt: {sorted(names)})")
        if j["typ"] not in ("boden",) and j.get("b") is None:
            raise ValueError(f"Gelenk {j['name']}: zweiter Körper fehlt")
        u = _unit(j["achse"])
        j["achse"], j["v"] = u.tolist(), _perp(u).tolist()
        # Bezugsrahmen je Seite (Welt-Koordinaten der Startlage); in der Grundstellung identisch
        for side in ("a", "b"):
            j.setdefault("p" + side, list(map(float, j["punkt"])))
            j.setdefault("u" + side, j["achse"])
            j.setdefault("v" + side, j["v"])
        if j["name"] in jn:
            raise ValueError(f"Gelenkname doppelt: {j['name']}")
        jn.add(j["name"])
        out.append(j)
    for j in out:
        if j["typ"] == "getriebe":
            for k in ("gelenk_a", "gelenk_b"):
                if j[k] not in jn:
                    raise ValueError(f"Getriebe {j['name']}: Gelenk '{j[k]}' unbekannt")
    return out


# ---------------------------------------------------------------------------------------------
# Löser
# ---------------------------------------------------------------------------------------------
class Mechanismus:
    """Starre Körper mit Lage (R, c) relativ zur Startlage; Gelenke als Gleichungen.

    bodies: Namen; start: {Name: 4×4 Welt-Matrix in der Startlage}. Gelenk-Punkte/-Achsen sind in
    Welt-Koordinaten der Startlage angegeben (bei Start ≠ Grundstellung vorher mit `move_joints`
    umrechnen). Bewegliche Körper: alle ausser Boden und `frozen`."""

    def __init__(self, bodies: list[str], joints: list[dict], frozen: set[str] | None = None):
        self.bodies = list(bodies)
        self.joints = prepare(joints, self.bodies)
        ground = {j["a"] for j in self.joints if j["typ"] == "boden"}
        self.fixed = ground | set(frozen or ())
        self.movable = [b for b in self.bodies if b not in self.fixed]
        self.idx = {b: i for i, b in enumerate(self.movable)}
        self.R = {b: np.eye(3) for b in self.bodies}
        self.c = {b: np.zeros(3) for b in self.bodies}      # Verschiebung des Ursprungs-Bezugs
        self.c0 = {b: np.zeros(3) for b in self.bodies}
        self.prev = {}                                       # letzter Winkel je Gelenk (Abwicklung)
        self.drives: dict[str, float] = {}
        self.active_limits: dict[str, float] = {}

    def set_centers(self, centers: dict[str, np.ndarray]):
        """Drehbezug je Körper (z. B. Mitte des Hüllquaders) – nur für gute Konvergenz."""
        for b, p in centers.items():
            self.c0[b] = np.asarray(p, float)
            self.c[b] = np.asarray(p, float).copy()

    # -- Lage -----------------------------------------------------------------------------
    def point(self, b, p, R=None, c=None):
        if b is None:
            return np.asarray(p, float)
        R = self.R[b] if R is None else R
        c = self.c[b] if c is None else c
        return R @ (np.asarray(p, float) - self.c0[b]) + c

    def vec(self, b, v, R=None):
        if b is None:
            return np.asarray(v, float)
        return (self.R[b] if R is None else R) @ np.asarray(v, float)

    def displacement(self, b) -> np.ndarray:
        """4×4: Startlage -> aktuelle Lage."""
        m = np.eye(4)
        m[:3, :3] = self.R[b]
        m[:3, 3] = self.c[b] - self.R[b] @ self.c0[b]
        return m

    # -- Gelenkgrössen -----------------------------------------------------------------------
    def _frames(self, j, st):
        R, c = st
        a, b = j["a"], j.get("b")
        Pa = R[a] @ (np.asarray(j["pa"]) - self.c0[a]) + c[a]
        ua, va = R[a] @ np.asarray(j["ua"]), R[a] @ np.asarray(j["va"])
        if b:
            Pb = R[b] @ (np.asarray(j["pb"]) - self.c0[b]) + c[b]
            ub, vb = R[b] @ np.asarray(j["ub"]), R[b] @ np.asarray(j["vb"])
        else:
            Pb, ub, vb = np.asarray(j["pb"], float), np.asarray(j["ub"], float), np.asarray(j["vb"], float)
        return Pa, Pb, ua, va, ub, vb, np.cross(ub, vb)

    def coord(self, j, st=None, unwrap=True) -> float:
        st = st or (self.R, self.c)
        Pa, Pb, ua, va, ub, vb, wb = self._frames(j, st)
        if HAUPT.get(j["typ"]) == "weg":
            return float(np.dot(Pa - Pb, ub))
        ang = math.degrees(math.atan2(np.dot(va, wb), np.dot(va, vb)))
        if unwrap and j["name"] in self.prev:
            p = self.prev[j["name"]]
            ang += 360.0 * round((p - ang) / 360.0)
        return ang

    def coords(self) -> dict[str, float]:
        return {j["name"]: self.coord(j) for j in self.joints if j["typ"] in HAUPT}

    # -- Gleichungen -------------------------------------------------------------------------
    def residuals(self, st, extra=True) -> np.ndarray:
        out = []
        byname = {j["name"]: j for j in self.joints}
        for j in self.joints:
            t = j["typ"]
            if t in ("boden", "getriebe"):
                continue
            Pa, Pb, ua, va, ub, vb, wb = self._frames(j, st)
            d = Pa - Pb
            if t in ("fest", "drehung", "kugel"):
                out += list(d)
            if t in ("fest", "drehung", "schiebung", "zylinder", "ebene", "schraube"):
                out += [np.dot(ua, vb), np.dot(ua, wb)]
            if t in ("schiebung", "zylinder", "schraube"):
                out += [np.dot(d, vb), np.dot(d, wb)]
            if t in ("fest", "schiebung"):
                out.append(np.dot(va, wb))
            if t == "ebene":
                out.append(np.dot(d, ub))
            if t == "schraube":
                ang = math.radians(self.coord(j, st))
                out.append(np.dot(d, ub) - j["steigung"] * ang / (2 * math.pi))
        for j in self.joints:
            if j["typ"] == "getriebe":
                qa = self._q(byname[j["gelenk_a"]], st)
                qb = self._q(byname[j["gelenk_b"]], st)
                out.append(qb - j["verhaeltnis"] * qa)
        if extra:
            for name, target in {**self.active_limits, **self.drives}.items():
                out.append(self._q(byname[name], st) - target)
        return np.array(out, float)

    def _q(self, j, st):
        """Gelenkgrösse für Gleichungen: Winkel in Rad·10 (ähnliche Grössenordnung wie mm), Weg in mm."""
        q = self.coord(j, st)
        return math.radians(q) * 10.0 if HAUPT.get(j["typ"]) == "winkel" else q

    def _target_q(self, j, value):
        return math.radians(value) * 10.0 if HAUPT.get(j["typ"]) == "winkel" else value

    def _state(self, x):
        R, c = dict(self.R), dict(self.c)
        for b, i in self.idx.items():
            w, t = x[6 * i:6 * i + 3], x[6 * i + 3:6 * i + 6]
            R[b] = _rotvec(w) @ self.R[b]
            c[b] = self.c[b] + t
        return R, c

    def jacobian(self, f, n, eps=1e-6):
        f0 = f(np.zeros(n))
        J = np.zeros((len(f0), n))
        for k in range(n):
            dx = np.zeros(n)
            dx[k] = eps
            J[:, k] = (f(dx) - f0) / eps
        return f0, J

    def _apply(self, x):
        R, c = self._state(x)
        self.R, self.c = R, c

    def solve(self, drives: dict[str, float] | None = None, drag=None, iters: int = 60) -> dict:
        """Gleichungen erfüllen; drives = {Gelenk: Wert (Grad/mm)}; drag = (Körper, Punkt, Ziel, Gewicht)."""
        byname = {j["name"]: j for j in self.joints}
        self.drives = {k: self._target_q(byname[k], v) for k, v in (drives or {}).items()}
        n = 6 * len(self.movable)
        if n == 0:
            return {"ok": True, "rest": 0.0}
        for it in range(iters):
            self.active_limits = self._limits()
            if drag is not None and it < iters // 2:
                body, p, target, wgt = drag

                def f(x):
                    st = self._state(x)
                    r = self.residuals(st)
                    pt = st[0][body] @ (np.asarray(p) - self.c0[body]) + st[1][body]
                    return np.concatenate([r * 1e3, (pt - target) * wgt])
            else:
                def f(x):
                    return self.residuals(self._state(x))
            f0, J = self.jacobian(f, n)
            if len(f0) == 0:
                break
            lam = 1e-9 + 1e-6 * float(np.max(np.abs(J))) if len(J) else 1e-9
            A = J.T @ J + lam * np.eye(n)
            dx = -np.linalg.solve(A, J.T @ f0)
            # je Körper höchstens 0,5 rad Drehung und 5 mm Weg je Iteration (Winkel-Abwicklung bleibt eindeutig)
            fac = 1.0
            for i in range(len(self.movable)):
                w = np.linalg.norm(dx[6 * i:6 * i + 3])
                t = np.linalg.norm(dx[6 * i + 3:6 * i + 6])
                fac = min(fac, 0.5 / w if w > 0.5 else 1.0, 5.0 / t if t > 5.0 else 1.0)
            dx *= fac
            step = np.linalg.norm(dx)
            self._apply(dx)
            self._unwrap()
            r = self.residuals((self.R, self.c))
            if (len(r) == 0 or np.max(np.abs(r)) < TOL) and (drag is None or it >= iters // 2) and step < 1e-9:
                break
        r = self.residuals((self.R, self.c))
        rest = float(np.max(np.abs(r))) if len(r) else 0.0
        return {"ok": rest < 1e-5, "rest": rest, "gelenke": self.coords()}

    def _unwrap(self):
        for j in self.joints:
            if HAUPT.get(j["typ"]) == "winkel":
                self.prev[j["name"]] = self.coord(j)

    def _limits(self) -> dict:
        act = {}
        for j in self.joints:
            g = j.get("grenzen")
            if not g or j["typ"] not in HAUPT:
                continue
            q = self.coord(j)
            if q < g[0] - 1e-9:
                act[j["name"]] = self._target_q(j, g[0])
            elif q > g[1] + 1e-9:
                act[j["name"]] = self._target_q(j, g[1])
        return act

    def dof(self) -> int:
        """Freiheitsgrade der beweglichen Körper (6·n − Rang der Gleichungen)."""
        n = 6 * len(self.movable)
        if n == 0:
            return 0
        saved, self.drives, self.active_limits = self.drives, {}, {}
        _, J = self.jacobian(lambda x: self.residuals(self._state(x), extra=False), n)
        self.drives = saved
        if J.size == 0:
            return n
        s = np.linalg.svd(J, compute_uv=False)
        rank = int(np.sum(s > 1e-6 * max(1.0, s[0])))
        return n - rank


def move_joints(joints: list[dict], disp: dict) -> list[dict]:
    """Gelenk-Rahmen von der Grundstellung in eine andere Stellung mitnehmen: Seite a mit Körper a,
    Seite b mit Körper b (disp = {Körper: 4×4 Grundstellung -> Stellung}). Gelenkgrössen bleiben auf
    die Grundstellung bezogen (Grenzen gelten weiter)."""
    out = []
    for j in joints:
        j = dict(j)
        if j["typ"] != "getriebe":
            u = _unit(j["achse"])
            base = {"p": list(map(float, j["punkt"])), "u": u.tolist(), "v": j.get("v") or _perp(u).tolist()}
            j["v"] = base["v"]
            for side in ("a", "b"):
                D = disp.get(j.get(side)) if j.get(side) else None
                if D is None:
                    D = np.eye(4)
                D = np.asarray(D, float)
                j["p" + side] = (D[:3, :3] @ np.asarray(base["p"]) + D[:3, 3]).tolist()
                j["u" + side] = (D[:3, :3] @ np.asarray(base["u"])).tolist()
                j["v" + side] = (D[:3, :3] @ np.asarray(base["v"])).tolist()
        out.append(j)
    return out
