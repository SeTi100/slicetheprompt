// Gelenk-Löser für Baugruppen im Studio – gleiche Rechnung wie slicetheprompt/kinematics.py.
//
// Jeder Körper ist starr (6 Freiheitsgrade). Gelenke sind Gleichungen zwischen Bezugsrahmen zweier
// Körper; die Lage folgt aus gedämpften kleinsten Quadraten. Geschlossene Ketten (Kurbeltrieb,
// Viergelenk) brauchen keine eigene Formel. Ohne THREE, damit es sich auch mit Node testen lässt.
//
// Matrizen: 16 Werte zeilenweise (wie assembly.json), Vektoren: [x, y, z].

const HAUPT = { drehung: "winkel", schraube: "winkel", zylinder: "weg", schiebung: "weg" };
const ANGLE_SCALE = 10; // Winkel in Rad·10 – ähnliche Grössenordnung wie mm in den Gleichungen

// ---------------------------------------------------------------- Vektor-Helfer
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => Math.hypot(a[0], a[1], a[2]);
const scale = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const unit = (a) => scale(a, 1 / norm(a));

function perp(u) {
  const a = Math.abs(u[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  return unit(cross(u, a));
}

// 3×3 zeilenweise
const mv = (R, v) => [
  R[0] * v[0] + R[1] * v[1] + R[2] * v[2],
  R[3] * v[0] + R[4] * v[1] + R[5] * v[2],
  R[6] * v[0] + R[7] * v[1] + R[8] * v[2],
];
function mm(A, B) {
  const C = new Array(9);
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) C[3 * i + j] = A[3 * i] * B[j] + A[3 * i + 1] * B[3 + j] + A[3 * i + 2] * B[6 + j];
  return C;
}
function rotvec(w) {
  const th = norm(w);
  if (th < 1e-15) return [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const [x, y, z] = scale(w, 1 / th);
  const s = Math.sin(th), c = Math.cos(th), t = 1 - c;
  return [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
          t * x * y + s * z, t * y * y + c, t * y * z - s * x,
          t * x * z - s * y, t * y * z + s * x, t * z * z + c];
}

// 4×4 zeilenweise
export function mat4mul(A, B) {
  const C = new Array(16).fill(0);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += A[4 * i + k] * B[4 * k + j];
    C[4 * i + j] = s;
  }
  return C;
}
export function mat4inv(M) {
  // allgemeine Inverse (Gauss-Jordan), auch für gespiegelte Lagen
  const a = M.slice(), inv = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  for (let c = 0; c < 4; c++) {
    let p = c;
    for (let r = c + 1; r < 4; r++) if (Math.abs(a[4 * r + c]) > Math.abs(a[4 * p + c])) p = r;
    for (let k = 0; k < 4; k++) {
      [a[4 * c + k], a[4 * p + k]] = [a[4 * p + k], a[4 * c + k]];
      [inv[4 * c + k], inv[4 * p + k]] = [inv[4 * p + k], inv[4 * c + k]];
    }
    const d = a[4 * c + c];
    for (let k = 0; k < 4; k++) { a[4 * c + k] /= d; inv[4 * c + k] /= d; }
    for (let r = 0; r < 4; r++) {
      if (r === c) continue;
      const f = a[4 * r + c];
      for (let k = 0; k < 4; k++) { a[4 * r + k] -= f * a[4 * c + k]; inv[4 * r + k] -= f * inv[4 * c + k]; }
    }
  }
  return inv;
}
const m4point = (M, p) => [M[0] * p[0] + M[1] * p[1] + M[2] * p[2] + M[3], M[4] * p[0] + M[5] * p[1] + M[6] * p[2] + M[7], M[8] * p[0] + M[9] * p[1] + M[10] * p[2] + M[11]];
const m4vec = (M, v) => [M[0] * v[0] + M[1] * v[1] + M[2] * v[2], M[4] * v[0] + M[5] * v[1] + M[6] * v[2], M[8] * v[0] + M[9] * v[1] + M[10] * v[2]];

// Lineares Gleichungssystem A x = b (n×n, Zeilen-Array), Gauss mit Spaltenpivot
function solveLinear(A, b, n) {
  const M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    const d = M[c][c];
    if (Math.abs(d) < 1e-300) continue;
    for (let r = c + 1; r < n; r++) {
      const f = M[r][c] / d;
      if (f === 0) continue;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  const x = new Array(n).fill(0);
  for (let r = n - 1; r >= 0; r--) {
    let s = M[r][n];
    for (let k = r + 1; k < n; k++) s -= M[r][k] * x[k];
    x[r] = Math.abs(M[r][r]) < 1e-300 ? 0 : s / M[r][r];
  }
  return x;
}

function rankOf(J, tol = 1e-6) {
  const A = J.map((r) => r.slice());
  const m = A.length, n = m ? A[0].length : 0;
  let maxAbs = 0;
  for (const r of A) for (const v of r) maxAbs = Math.max(maxAbs, Math.abs(v));
  const eps = tol * Math.max(1, maxAbs);
  let rank = 0;
  for (let c = 0; c < n && rank < m; c++) {
    let p = rank;
    for (let r = rank + 1; r < m; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    if (Math.abs(A[p][c]) <= eps) continue;
    [A[rank], A[p]] = [A[p], A[rank]];
    for (let r = rank + 1; r < m; r++) {
      const f = A[r][c] / A[rank][c];
      for (let k = c; k < n; k++) A[r][k] -= f * A[rank][k];
    }
    rank++;
  }
  return rank;
}

// ---------------------------------------------------------------- Gelenke vorbereiten
// Gelenk-Rahmen aus der Grundstellung in die aktuelle Lage mitnehmen (Seite a mit Körper a usw.)
export function moveJoints(joints, disp) {
  return joints.map((j0) => {
    const j = { ...j0 };
    if (j.typ === "getriebe") return j;
    const u = unit(j.achse), v = j.v || perp(u);
    for (const side of ["a", "b"]) {
      const D = (j[side] && disp[j[side]]) || null;
      j["p" + side] = D ? m4point(D, j.punkt) : j.punkt.slice();
      j["u" + side] = D ? m4vec(D, u) : u.slice();
      j["v" + side] = D ? m4vec(D, v) : v.slice();
    }
    return j;
  });
}

// ---------------------------------------------------------------- Mechanismus
export class Mechanism {
  /**
   * kin: assembly.json → kinematik; world: {Körper: 4×4 aktuelle Welt-Lage}; frozen: Set der Körper,
   * die sich nicht bewegen dürfen (zusätzlich zu Boden-Gelenken).
   */
  constructor(kin, world, frozen = new Set()) {
    this.kin = kin;
    this.bodies = kin.koerper.filter((b) => world[b]);
    this.world0 = Object.fromEntries(Object.entries(world).map(([k, v]) => [k, v.slice()]));
    const disp = {};
    this.c0 = {};
    for (const b of this.bodies) {
      const ref = kin.grundstellung[b];
      disp[b] = ref ? mat4mul(world[b], mat4inv(ref)) : [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
      this.c0[b] = m4point(disp[b], kin.zentren[b] || [0, 0, 0]);
    }
    this.joints = moveJoints(kin.gelenke, disp).filter((j) => j.typ === "getriebe" || (this.bodies.includes(j.a) && (!j.b || this.bodies.includes(j.b))));
    this.byName = Object.fromEntries(this.joints.map((j) => [j.name, j]));
    const ground = new Set(this.joints.filter((j) => j.typ === "boden").map((j) => j.a));
    this.fixed = new Set([...ground, ...frozen]);
    this.movable = this.bodies.filter((b) => !this.fixed.has(b));
    this.R = {};
    this.c = {};
    for (const b of this.bodies) {
      this.R[b] = [1, 0, 0, 0, 1, 0, 0, 0, 1];
      this.c[b] = this.c0[b].slice();
    }
    this.prev = {};
    this.drives = {};
    this.limits = {};
    for (const j of this.joints) if (HAUPT[j.typ] === "winkel") this.prev[j.name] = this.coord(j);
  }

  snapshot() {
    return { R: { ...this.R }, c: { ...this.c }, prev: { ...this.prev } };
  }

  restore(s) {
    this.R = { ...s.R };
    this.c = { ...s.c };
    this.prev = { ...s.prev };
  }

  // Zwischenlage zwischen zwei Schnappschüssen (t = 0 … 1) – für den Kollisionsanschlag
  blend(a, b, t) {
    for (const k of this.bodies) {
      const Ra = a.R[k], Rb = b.R[k];
      const Rrel = mm(Rb, [Ra[0], Ra[3], Ra[6], Ra[1], Ra[4], Ra[7], Ra[2], Ra[5], Ra[8]]);
      const cosA = Math.min(1, Math.max(-1, (Rrel[0] + Rrel[4] + Rrel[8] - 1) / 2));
      const ang = Math.acos(cosA);
      let w = [0, 0, 0];
      if (ang > 1e-12) {
        const s = 2 * Math.sin(ang);
        w = scale([(Rrel[7] - Rrel[5]) / s, (Rrel[2] - Rrel[6]) / s, (Rrel[3] - Rrel[1]) / s], ang * t);
      }
      this.R[k] = mm(rotvec(w), Ra);
      this.c[k] = add(a.c[k], scale(sub(b.c[k], a.c[k]), t));
    }
    this.prev = { ...b.prev };
  }

  // Lage-Änderung seit Sitzungsbeginn (4×4) und aktuelle Welt-Lage
  displacement(b) {
    const R = this.R[b], c = this.c[b], c0 = this.c0[b];
    const t = sub(c, mv(R, c0));
    return [R[0], R[1], R[2], t[0], R[3], R[4], R[5], t[1], R[6], R[7], R[8], t[2], 0, 0, 0, 1];
  }

  worldOf(b) {
    return mat4mul(this.displacement(b), this.world0[b]);
  }

  moved(b, tol = 1e-9) {
    const D = this.displacement(b);
    const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    return D.some((v, i) => Math.abs(v - I[i]) > tol);
  }

  pointOf(b, p, st = null) {
    const R = (st ? st.R : this.R)[b], c = (st ? st.c : this.c)[b];
    return add(mv(R, sub(p, this.c0[b])), c);
  }

  _frames(j, st) {
    const R = st.R, c = st.c;
    const Pa = add(mv(R[j.a], sub(j.pa, this.c0[j.a])), c[j.a]);
    const ua = mv(R[j.a], j.ua), va = mv(R[j.a], j.va);
    let Pb, ub, vb;
    if (j.b) {
      Pb = add(mv(R[j.b], sub(j.pb, this.c0[j.b])), c[j.b]);
      ub = mv(R[j.b], j.ub);
      vb = mv(R[j.b], j.vb);
    } else {
      Pb = j.pb; ub = j.ub; vb = j.vb;
    }
    return { Pa, Pb, ua, va, ub, vb, wb: cross(ub, vb) };
  }

  coord(j, st = null, unwrap = true) {
    const f = this._frames(j, st || this);
    if (HAUPT[j.typ] === "weg") return dot(sub(f.Pa, f.Pb), f.ub);
    let ang = (Math.atan2(dot(f.va, f.wb), dot(f.va, f.vb)) * 180) / Math.PI;
    if (unwrap && this.prev[j.name] !== undefined) ang += 360 * Math.round((this.prev[j.name] - ang) / 360);
    return ang;
  }

  coords() {
    const out = {};
    for (const j of this.joints) if (HAUPT[j.typ]) out[j.name] = this.coord(j);
    return out;
  }

  _q(j, st) {
    const q = this.coord(j, st);
    return HAUPT[j.typ] === "winkel" ? ((q * Math.PI) / 180) * ANGLE_SCALE : q;
  }

  _targetQ(j, v) {
    return HAUPT[j.typ] === "winkel" ? ((v * Math.PI) / 180) * ANGLE_SCALE : v;
  }

  residuals(st, extra = true) {
    const out = [];
    for (const j of this.joints) {
      const t = j.typ;
      if (t === "boden" || t === "getriebe") continue;
      const f = this._frames(j, st);
      const d = sub(f.Pa, f.Pb);
      if (t === "fest" || t === "drehung" || t === "kugel") out.push(d[0], d[1], d[2]);
      if (["fest", "drehung", "schiebung", "zylinder", "ebene", "schraube"].includes(t)) out.push(dot(f.ua, f.vb), dot(f.ua, f.wb));
      if (t === "schiebung" || t === "zylinder" || t === "schraube") out.push(dot(d, f.vb), dot(d, f.wb));
      if (t === "fest" || t === "schiebung") out.push(dot(f.va, f.wb));
      if (t === "ebene") out.push(dot(d, f.ub));
      if (t === "schraube") {
        const ang = (this.coord(j, st) * Math.PI) / 180;
        out.push(dot(d, f.ub) - (j.steigung * ang) / (2 * Math.PI));
      }
    }
    for (const j of this.joints) {
      if (j.typ !== "getriebe") continue;
      const ja = this.byName[j.gelenk_a], jb = this.byName[j.gelenk_b];
      if (!ja || !jb) continue;
      out.push(this._q(jb, st) - j.verhaeltnis * this._q(ja, st));
    }
    if (extra) {
      for (const [name, target] of Object.entries({ ...this.limits, ...this.drives })) {
        out.push(this._q(this.byName[name], st) - target);
      }
    }
    return out;
  }

  _state(x) {
    const R = { ...this.R }, c = { ...this.c };
    this.movable.forEach((b, i) => {
      R[b] = mm(rotvec([x[6 * i], x[6 * i + 1], x[6 * i + 2]]), this.R[b]);
      c[b] = add(this.c[b], [x[6 * i + 3], x[6 * i + 4], x[6 * i + 5]]);
    });
    return { R, c };
  }

  _jacobian(f, n, eps = 1e-6) {
    const zero = new Array(n).fill(0);
    const f0 = f(zero);
    const J = f0.map(() => new Array(n).fill(0));
    for (let k = 0; k < n; k++) {
      const dx = zero.slice();
      dx[k] = eps;
      const fk = f(dx);
      for (let i = 0; i < f0.length; i++) J[i][k] = (fk[i] - f0[i]) / eps;
    }
    return { f0, J };
  }

  _limitsNow() {
    const act = {};
    for (const j of this.joints) {
      const g = j.grenzen;
      if (!g || !HAUPT[j.typ]) continue;
      const q = this.coord(j);
      if (q < g[0] - 1e-9) act[j.name] = this._targetQ(j, g[0]);
      else if (q > g[1] + 1e-9) act[j.name] = this._targetQ(j, g[1]);
    }
    return act;
  }

  /**
   * Gleichungen erfüllen. drives = {Gelenk: Wert (Grad/mm)}; drag = {body, point (Welt, Sitzungsbeginn),
   * target (Welt), weight}. Ergebnis {ok, rest, gelenke}.
   */
  solve({ drives = {}, drag = null, iters = 40 } = {}) {
    this.drives = {};
    for (const [k, v] of Object.entries(drives)) if (this.byName[k]) this.drives[k] = this._targetQ(this.byName[k], v);
    const n = 6 * this.movable.length;
    if (!n) return { ok: true, rest: 0, gelenke: this.coords() };
    const dragIters = drag ? Math.floor(iters / 2) : 0;
    for (let it = 0; it < iters; it++) {
      this.limits = this._limitsNow();
      let f;
      if (drag && it < dragIters) {
        const w = drag.weight ?? 0.05;
        f = (x) => {
          const st = this._state(x);
          const r = this.residuals(st).map((v) => v * 1e3);
          const p = this.pointOf(drag.body, drag.point, st);
          return r.concat(sub(p, drag.target).map((v) => v * w));
        };
      } else {
        f = (x) => this.residuals(this._state(x));
      }
      const { f0, J } = this._jacobian(f, n);
      if (!f0.length) break;
      let maxJ = 0;
      for (const r of J) for (const v of r) maxJ = Math.max(maxJ, Math.abs(v));
      const lam = 1e-9 + 1e-6 * maxJ;
      const A = Array.from({ length: n }, () => new Array(n).fill(0));
      const g = new Array(n).fill(0);
      for (let i = 0; i < J.length; i++) {
        const Ji = J[i];
        for (let a = 0; a < n; a++) {
          if (Ji[a] === 0) continue;
          g[a] += Ji[a] * f0[i];
          for (let b = a; b < n; b++) A[a][b] += Ji[a] * Ji[b];
        }
      }
      for (let a = 0; a < n; a++) {
        A[a][a] += lam;
        for (let b = 0; b < a; b++) A[a][b] = A[b][a];
      }
      const dx = solveLinear(A, g.map((v) => -v), n);
      // Schritt begrenzen: je Körper höchstens 0,5 rad Drehung und 5 mm Weg je Iteration
      // (grössere Drehungen würden die Winkel-Abwicklung von Dreh-/Schraubgelenken verfälschen)
      let fac = 1;
      for (let i = 0; i < this.movable.length; i++) {
        const w = Math.hypot(dx[6 * i], dx[6 * i + 1], dx[6 * i + 2]);
        const t = Math.hypot(dx[6 * i + 3], dx[6 * i + 4], dx[6 * i + 5]);
        fac = Math.min(fac, w > 0.5 ? 0.5 / w : 1, t > 5 ? 5 / t : 1);
      }
      if (fac < 1) for (let k = 0; k < n; k++) dx[k] *= fac;
      const step = Math.hypot(...dx);
      const st = this._state(dx);
      this.R = st.R;
      this.c = st.c;
      for (const j of this.joints) if (HAUPT[j.typ] === "winkel") this.prev[j.name] = this.coord(j);
      if (it >= dragIters && step < 1e-10) break;
    }
    const r = this.residuals(this);
    const rest = r.length ? Math.max(...r.map(Math.abs)) : 0;
    return { ok: rest < 1e-4, rest, gelenke: this.coords() };
  }

  // Freiheitsgrade der beweglichen Körper
  dof() {
    const n = 6 * this.movable.length;
    if (!n) return 0;
    const saved = [this.drives, this.limits];
    this.drives = {};
    this.limits = {};
    const { J } = this._jacobian((x) => this.residuals(this._state(x), false), n);
    [this.drives, this.limits] = saved;
    return n - (J.length ? rankOf(J) : 0);
  }

  // Grösster Restfehler je Gelenk (für Meldungen „Gelenk klemmt“)
  jointErrors() {
    const out = {};
    const all = this.joints;
    for (const j of all) {
      if (j.typ === "boden" || j.typ === "getriebe") continue;
      this.joints = [j];
      const r = this.residuals(this, false);
      out[j.name] = r.length ? Math.max(...r.map(Math.abs)) : 0;
    }
    this.joints = all;
    return out;
  }
}

export { HAUPT };
