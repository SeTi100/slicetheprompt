// Schnelle Netz-Kollision für das Bewegen im Studio (ohne Fremdbibliothek).
//
// Je Netz ein Hüllquader-Baum (BVH). Zwei Netze „dringen ein“, wenn sich zwei Dreiecke echt
// durchstossen: jedes liegt um mehr als `eps` auf beiden Seiten der Ebene des anderen, und die
// gemeinsame Schnittstrecke ist länger als `eps`. Bündig aufliegende Flächen, Kanten- und
// Punktberührungen zählen dadurch nicht – Teile dürfen aneinander gleiten.
// Matrizen: 16 Werte zeilenweise (wie assembly.json). Ohne THREE (mit Node testbar).

const LEAF = 8;

export function buildBVH(positions) {
  const nTri = Math.floor(positions.length / 9);
  const tri = new Float64Array(positions); // Kopie, doppelte Genauigkeit
  const cent = new Float64Array(nTri * 3);
  for (let t = 0; t < nTri; t++) {
    for (let k = 0; k < 3; k++) cent[3 * t + k] = (tri[9 * t + k] + tri[9 * t + 3 + k] + tri[9 * t + 6 + k]) / 3;
  }
  const order = new Int32Array(nTri);
  for (let i = 0; i < nTri; i++) order[i] = i;
  const nodes = []; // {min:[3], max:[3], left, right, start, count}

  function bounds(start, end) {
    const mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (let i = start; i < end; i++) {
      const t = order[i];
      for (let v = 0; v < 3; v++) for (let k = 0; k < 3; k++) {
        const x = tri[9 * t + 3 * v + k];
        if (x < mn[k]) mn[k] = x;
        if (x > mx[k]) mx[k] = x;
      }
    }
    return [mn, mx];
  }

  function build(start, end) {
    const [mn, mx] = bounds(start, end);
    const id = nodes.length;
    nodes.push({ min: mn, max: mx, left: -1, right: -1, start, count: end - start });
    if (end - start <= LEAF) return id;
    const ext = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]];
    const ax = ext[0] > ext[1] ? (ext[0] > ext[2] ? 0 : 2) : ext[1] > ext[2] ? 1 : 2;
    const sub = Array.from(order.subarray(start, end)).sort((a, b) => cent[3 * a + ax] - cent[3 * b + ax]);
    order.set(sub, start);
    const mid = (start + end) >> 1;
    nodes[id].left = build(start, mid);
    nodes[id].right = build(mid, end);
    return id;
  }
  if (nTri) build(0, nTri);
  return { tri, order, nodes, nTri };
}

function transformAABB(M, mn, mx) {
  const omn = [Infinity, Infinity, Infinity], omx = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < 8; i++) {
    const p = [i & 1 ? mx[0] : mn[0], i & 2 ? mx[1] : mn[1], i & 4 ? mx[2] : mn[2]];
    for (let r = 0; r < 3; r++) {
      const v = M[4 * r] * p[0] + M[4 * r + 1] * p[1] + M[4 * r + 2] * p[2] + M[4 * r + 3];
      if (v < omn[r]) omn[r] = v;
      if (v > omx[r]) omx[r] = v;
    }
  }
  return [omn, omx];
}

const overlap = (amn, amx, bmn, bmx) =>
  amn[0] <= bmx[0] && bmn[0] <= amx[0] && amn[1] <= bmx[1] && bmn[1] <= amx[1] && amn[2] <= bmx[2] && bmn[2] <= amx[2];

// ---------------------------------------------------------------- Dreieck gegen Dreieck
function planeOf(a, b, c) {
  const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
  const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
  const l = Math.hypot(n[0], n[1], n[2]);
  if (l < 1e-14) return null;
  return [n[0] / l, n[1] / l, n[2] / l];
}

function interval(P, d, L) {
  // Schnittstrecke des Dreiecks P mit der Ebene (Vorzeichen-Abstände d), projiziert auf Richtung L
  let lo = Infinity, hi = -Infinity;
  for (let i = 0; i < 3; i++) {
    const j = (i + 1) % 3;
    if ((d[i] < 0 && d[j] > 0) || (d[i] > 0 && d[j] < 0)) {
      const t = d[i] / (d[i] - d[j]);
      const p = [P[i][0] + (P[j][0] - P[i][0]) * t, P[i][1] + (P[j][1] - P[i][1]) * t, P[i][2] + (P[j][2] - P[i][2]) * t];
      const s = p[0] * L[0] + p[1] * L[1] + p[2] * L[2];
      if (s < lo) lo = s;
      if (s > hi) hi = s;
    }
  }
  return [lo, hi];
}

export function trianglesPenetrate(P, Q, eps) {
  const nQ = planeOf(Q[0], Q[1], Q[2]);
  const nP = planeOf(P[0], P[1], P[2]);
  if (!nQ || !nP) return false;
  const dP = P.map((p) => (p[0] - Q[0][0]) * nQ[0] + (p[1] - Q[0][1]) * nQ[1] + (p[2] - Q[0][2]) * nQ[2]);
  if (Math.min(...dP) > -eps || Math.max(...dP) < eps) return false;
  const dQ = Q.map((q) => (q[0] - P[0][0]) * nP[0] + (q[1] - P[0][1]) * nP[1] + (q[2] - P[0][2]) * nP[2]);
  if (Math.min(...dQ) > -eps || Math.max(...dQ) < eps) return false;
  let L = [nP[1] * nQ[2] - nP[2] * nQ[1], nP[2] * nQ[0] - nP[0] * nQ[2], nP[0] * nQ[1] - nP[1] * nQ[0]];
  const l = Math.hypot(L[0], L[1], L[2]);
  if (l < 1e-9) return false; // parallel
  L = [L[0] / l, L[1] / l, L[2] / l];
  const [a0, a1] = interval(P, dP, L);
  const [b0, b1] = interval(Q, dQ, L);
  return Math.min(a1, b1) - Math.max(a0, b0) > eps;
}

// Schneller Kern: Dreieck i von A (flach) gegen Dreieck aus Puffer q (9 Werte, A-Koordinaten)
function penetrateFlat(ta, ia, q, eps) {
  const p0x = ta[ia], p0y = ta[ia + 1], p0z = ta[ia + 2];
  const p1x = ta[ia + 3], p1y = ta[ia + 4], p1z = ta[ia + 5];
  const p2x = ta[ia + 6], p2y = ta[ia + 7], p2z = ta[ia + 8];
  const q0x = q[0], q0y = q[1], q0z = q[2], q1x = q[3], q1y = q[4], q1z = q[5], q2x = q[6], q2y = q[7], q2z = q[8];
  // Ebene Q
  let ux = q1x - q0x, uy = q1y - q0y, uz = q1z - q0z, vx = q2x - q0x, vy = q2y - q0y, vz = q2z - q0z;
  let nqx = uy * vz - uz * vy, nqy = uz * vx - ux * vz, nqz = ux * vy - uy * vx;
  let l = Math.sqrt(nqx * nqx + nqy * nqy + nqz * nqz);
  if (l < 1e-14) return false;
  nqx /= l; nqy /= l; nqz /= l;
  const d0 = (p0x - q0x) * nqx + (p0y - q0y) * nqy + (p0z - q0z) * nqz;
  const d1 = (p1x - q0x) * nqx + (p1y - q0y) * nqy + (p1z - q0z) * nqz;
  const d2 = (p2x - q0x) * nqx + (p2y - q0y) * nqy + (p2z - q0z) * nqz;
  if (Math.min(d0, d1, d2) > -eps || Math.max(d0, d1, d2) < eps) return false;
  // Ebene P
  ux = p1x - p0x; uy = p1y - p0y; uz = p1z - p0z; vx = p2x - p0x; vy = p2y - p0y; vz = p2z - p0z;
  let npx = uy * vz - uz * vy, npy = uz * vx - ux * vz, npz = ux * vy - uy * vx;
  l = Math.sqrt(npx * npx + npy * npy + npz * npz);
  if (l < 1e-14) return false;
  npx /= l; npy /= l; npz /= l;
  const e0 = (q0x - p0x) * npx + (q0y - p0y) * npy + (q0z - p0z) * npz;
  const e1 = (q1x - p0x) * npx + (q1y - p0y) * npy + (q1z - p0z) * npz;
  const e2 = (q2x - p0x) * npx + (q2y - p0y) * npy + (q2z - p0z) * npz;
  if (Math.min(e0, e1, e2) > -eps || Math.max(e0, e1, e2) < eps) return false;
  const P = [[p0x, p0y, p0z], [p1x, p1y, p1z], [p2x, p2y, p2z]];
  const Q = [[q0x, q0y, q0z], [q1x, q1y, q1z], [q2x, q2y, q2z]];
  let Lx = npy * nqz - npz * nqy, Ly = npz * nqx - npx * nqz, Lz = npx * nqy - npy * nqx;
  l = Math.sqrt(Lx * Lx + Ly * Ly + Lz * Lz);
  if (l < 1e-9) return false;
  const L = [Lx / l, Ly / l, Lz / l];
  const [a0, a1] = interval(P, [d0, d1, d2], L);
  const [b0, b1] = interval(Q, [e0, e1, e2], L);
  return Math.min(a1, b1) - Math.max(a0, b0) > eps;
}

/**
 * Dringen zwei Netze ein? A in Lage mA, B in Lage mB (Netz-Koordinaten → Welt).
 * Rückgabe: null oder {punkt} (Welt-Koordinaten eines Treffers).
 */
export function meshesPenetrate(A, mA, B, mB, eps = 0.02, invA = null) {
  if (!A.nTri || !B.nTri) return null;
  const iA = invA || invert(mA);
  const M = mul(iA, mB); // B → A-Koordinaten
  const boxB = new Map();
  const boxOf = (id) => {
    let b = boxB.get(id);
    if (!b) {
      b = transformAABB(M, B.nodes[id].min, B.nodes[id].max);
      boxB.set(id, b);
    }
    return b;
  };
  const tb = new Float64Array(9 * B.nTri);
  const have = new Uint8Array(B.nTri);
  const q = new Float64Array(9);
  const loadB = (t) => {
    const o = 9 * t;
    if (!have[t]) {
      for (let v = 0; v < 3; v++) {
        const x = B.tri[o + 3 * v], y = B.tri[o + 3 * v + 1], z = B.tri[o + 3 * v + 2];
        tb[o + 3 * v] = M[0] * x + M[1] * y + M[2] * z + M[3];
        tb[o + 3 * v + 1] = M[4] * x + M[5] * y + M[6] * z + M[7];
        tb[o + 3 * v + 2] = M[8] * x + M[9] * y + M[10] * z + M[11];
      }
      have[t] = 1;
    }
    for (let k = 0; k < 9; k++) q[k] = tb[o + k];
  };
  const stack = [0, 0];
  while (stack.length) {
    const ib = stack.pop(), ia = stack.pop();
    const na = A.nodes[ia], nb = B.nodes[ib];
    const [bmn, bmx] = boxOf(ib);
    if (!overlap(na.min, na.max, bmn, bmx)) continue;
    const leafA = na.left < 0, leafB = nb.left < 0;
    if (leafA && leafB) {
      for (let k = nb.start; k < nb.start + nb.count; k++) {
        loadB(B.order[k]);
        for (let i = na.start; i < na.start + na.count; i++) {
          const o = 9 * A.order[i];
          if (penetrateFlat(A.tri, o, q, eps)) {
            const c = [(A.tri[o] + A.tri[o + 3] + A.tri[o + 6]) / 3, (A.tri[o + 1] + A.tri[o + 4] + A.tri[o + 7]) / 3,
                       (A.tri[o + 2] + A.tri[o + 5] + A.tri[o + 8]) / 3];
            return { punkt: apply(mA, c) };
          }
        }
      }
    } else if (leafB || (!leafA && (na.max[0] - na.min[0]) + (na.max[1] - na.min[1]) + (na.max[2] - na.min[2]) >
        (bmx[0] - bmn[0]) + (bmx[1] - bmn[1]) + (bmx[2] - bmn[2]))) {
      stack.push(na.left, ib, na.right, ib);
    } else {
      stack.push(ia, nb.left, ia, nb.right);
    }
  }
  return null;
}

// ---------------------------------------------------------------- 4×4 zeilenweise
function mul(A, B) {
  const C = new Array(16);
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) C[4 * i + j] = A[4 * i] * B[j] + A[4 * i + 1] * B[4 + j] + A[4 * i + 2] * B[8 + j] + A[4 * i + 3] * B[12 + j];
  return C;
}
function apply(M, p) {
  return [M[0] * p[0] + M[1] * p[1] + M[2] * p[2] + M[3], M[4] * p[0] + M[5] * p[1] + M[6] * p[2] + M[7], M[8] * p[0] + M[9] * p[1] + M[10] * p[2] + M[11]];
}
export function invert(M) {
  // affine Inverse (auch gespiegelt): [R t] -> [R⁻¹ −R⁻¹t]
  const a = M[0], b = M[1], c = M[2], d = M[4], e = M[5], f = M[6], g = M[8], h = M[9], i = M[10];
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  const r = [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
             (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
             (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det];
  const t = [M[3], M[7], M[11]];
  const ti = [-(r[0] * t[0] + r[1] * t[1] + r[2] * t[2]), -(r[3] * t[0] + r[4] * t[1] + r[5] * t[2]), -(r[6] * t[0] + r[7] * t[1] + r[8] * t[2])];
  return [r[0], r[1], r[2], ti[0], r[3], r[4], r[5], ti[1], r[6], r[7], r[8], ti[2], 0, 0, 0, 1];
}
