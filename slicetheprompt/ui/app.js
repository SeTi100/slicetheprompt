import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { STLLoader } from "/vendor/STLLoader.js";
import { Mechanism, freezePlan } from "/kinematics.js";
import { buildBVH, meshesPenetrate } from "/collide.js";
import { initUsageUI, refreshSidebarPartCard, refreshUsageOverview } from "/usage.js";

// ============================================================================
// Global Studio State
// ============================================================================
const S = {
  projects: [],
  currentProject: null,
  activeVersion: null,     // null = live working state, "v002" = historical
  ghostVersion: null,      // null or "v001"
  state: null,             // current /api/projects/<p>/state payload
  viewMode: "print",       // "print" | "model"
  tool: "orbit",           // "orbit" | "pin" | "box" | "move" | "measure"
  overlays: {
    errors: true,
    features: false,
    edges: true,
  },
  clip: {
    axis: "off",
    percent: 50,
    flip: false,
  },
  annotations: [],
  nextAnnId: 1,
  pendingPoint: null,      // first click for "box" / "move" / "measure"
  measurement: null,       // { a_model, b_model, a_print, b_print, dist, delta_model }
  partBounds: null,        // THREE.Box3 of active meshes in current viewMode
  flashTimer: null,
  imported: { file: null, plate: null, key: null, hidden: new Set(), prevViewMode: null },
  // Baugruppe (Projekt mit assembly.py): Pose, Explosion, ausgeblendete Teile, Netz-Cache
  asm: {
    pose: 0,
    explode: 0,
    hidden: new Set(),
    vectors: {},
    geoCache: new Map(),   // url -> Promise<BufferGeometry> (Pose wechseln ohne Neuladen)
    edgeCache: new Map(),  // url -> EdgesGeometry
    prevViewMode: null,
  },
  // Bewegen mit Gelenken (nur Baugruppen mit GELENKE)
  kin: {
    selected: new Set(),
    world: {},        // Körper -> aktuelle Welt-Lage (16 Werte zeilenweise)
    poseWorld: {},    // Lage in der gewählten Pose
    mech: null,
    bvh: new Map(),   // Netz-URL -> Hüllquader-Baum
    info: {},
    preexisting: new Set(),
    moved: false,
    live: null,
    drag: null,
    down: null,
    blocked: null,
  },
};

const stlLoader = new STLLoader();
window.__studio = { S }; // Lesezugriff für Tests/Fehlersuche (Konsole)

// ============================================================================
// Three.js Scene Setup (Z-Up Engineering Convention)
// ============================================================================
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

const canvas = document.getElementById("three-canvas");
const container = document.getElementById("canvas-container");
const labelsLayer = document.getElementById("labels-layer");

const renderer = new THREE.WebGLRenderer({
  canvas,
  antialias: true,
  preserveDrawingBuffer: true, // Essential for PNG feedback screenshot capture!
  alpha: false,
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setClearColor(0x0c111a, 1);
renderer.localClippingEnabled = true;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(42, 1, 0.5, 4000);
camera.up.set(0, 0, 1);
camera.position.set(220, -210, 180);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.screenSpacePanning = true;
controls.zoomSpeed = 1.6;
controls.minDistance = 2;
controls.maxDistance = 2500;
controls.target.set(130, 130, 15);

// Trackpad Wheel Normalization:
// Laptop-Trackpads senden bei 2-Finger-Gesten oft winzige deltaY-Werte (DOM_DELTA_PIXEL),
// wodurch OrbitControls im Standardmodus viel zu langsam skaliert.
canvas.addEventListener(
  "wheel",
  (ev) => {
    // Falls feine Pixel-Deltas vom Trackpad kommen, gezielt dynamisch verstaerken
    if (Math.abs(ev.deltaY) < 40 && ev.deltaMode === 0) {
      const factor = ev.deltaY < 0 ? 0.92 : 1.08;
      const offset = camera.position.clone().sub(controls.target);
      const newLen = Math.max(controls.minDistance, Math.min(controls.maxDistance, offset.length() * factor));
      offset.setLength(newLen);
      camera.position.copy(controls.target).add(offset);
      controls.update();
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }
  },
  { capture: true, passive: false }
);

// Lights
const hemiLight = new THREE.HemisphereLight(0xdbeafe, 0x1e293b, 0.85);
hemiLight.position.set(0, 0, 300);
scene.add(hemiLight);

const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.95);
dirLight1.position.set(220, -260, 340);
scene.add(dirLight1);

const dirLight2 = new THREE.DirectionalLight(0x93c5fd, 0.45);
dirLight2.position.set(-200, 240, 160);
scene.add(dirLight2);

// Scene Groups
const envGroup = new THREE.Group();
const meshGroup = new THREE.Group();
const edgesGroup = new THREE.Group();
const errorGroup = new THREE.Group();
const ghostCutGroup = new THREE.Group();
const ghostVersionGroup = new THREE.Group();
const featureGroup = new THREE.Group();
const annotationGroup = new THREE.Group();
const previewGroup = new THREE.Group();
const focusPulseGroup = new THREE.Group();
const asmGhostGroup = new THREE.Group();   // Geisterkörper einer Baugruppe (durchscheinend)

scene.add(
  envGroup,
  asmGhostGroup,
  meshGroup,
  edgesGroup,
  errorGroup,
  ghostCutGroup,
  ghostVersionGroup,
  featureGroup,
  annotationGroup,
  previewGroup,
  focusPulseGroup
);

const clipPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 500);
const raycaster = new THREE.Raycaster();
const mouseNDC = new THREE.Vector2();
const bedPlaneMath = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);

// Dynamic 3D -> 2D HTML labels registry: [{ el, pos: THREE.Vector3 }]
let labelAnchors = [];
let axisLabelAnchors = [];

// ============================================================================
// Coordinate Math Contract (model <-> print)
// ============================================================================
function getTransform() {
  const po = S.state?.print_info || {};
  const R = po.rotation_matrix || [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  const s = po.shift || [0, 0, 0];
  return { R, s };
}

function modelToPrint(m) {
  const { R, s } = getTransform();
  return [
    R[0][0] * m[0] + R[0][1] * m[1] + R[0][2] * m[2] + s[0],
    R[1][0] * m[0] + R[1][1] * m[1] + R[1][2] * m[2] + s[1],
    R[2][0] * m[0] + R[2][1] * m[1] + R[2][2] * m[2] + s[2],
  ];
}

function printToModel(p) {
  const { R, s } = getTransform();
  const dx = p[0] - s[0];
  const dy = p[1] - s[1];
  const dz = p[2] - s[2];
  return [
    dx * R[0][0] + dy * R[1][0] + dz * R[2][0],
    dx * R[0][1] + dy * R[1][1] + dz * R[2][1],
    dx * R[0][2] + dy * R[1][2] + dz * R[2][2],
  ];
}

function normalPrintToModel(n) {
  const { R } = getTransform();
  return [
    n[0] * R[0][0] + n[1] * R[1][0] + n[2] * R[2][0],
    n[0] * R[0][1] + n[1] * R[1][1] + n[2] * R[2][1],
    n[0] * R[0][2] + n[1] * R[1][2] + n[2] * R[2][2],
  ];
}

function normalModelToPrint(n) {
  const { R } = getTransform();
  return [
    R[0][0] * n[0] + R[0][1] * n[1] + R[0][2] * n[2],
    R[1][0] * n[0] + R[1][1] * n[1] + R[1][2] * n[2],
    R[2][0] * n[0] + R[2][1] * n[1] + R[2][2] * n[2],
  ];
}

function bboxCornersTransform(bbox6, fn) {
  const [x0, y0, z0, x1, y1, z1] = bbox6;
  const corners = [
    [x0, y0, z0], [x1, y0, z0], [x0, y1, z0], [x1, y1, z0],
    [x0, y0, z1], [x1, y0, z1], [x0, y1, z1], [x1, y1, z1],
  ].map(fn);
  const xs = corners.map((c) => c[0]);
  const ys = corners.map((c) => c[1]);
  const zs = corners.map((c) => c[2]);
  return [
    Math.min(...xs), Math.min(...ys), Math.min(...zs),
    Math.max(...xs), Math.max(...ys), Math.max(...zs),
  ];
}

function round3(arr) {
  return arr.map((v) => Math.round(v * 100) / 100);
}

function fmtVec(arr) {
  if (!arr) return "–";
  return arr.map((v) => Number(v).toFixed(1)).join(", ");
}

// ============================================================================
// Scene Helpers: Bed Plate, Grid & Axes
// ============================================================================
function clearGroup(group) {
  while (group.children.length > 0) {
    const obj = group.children.pop();
    if (obj.geometry && !obj.userData?.sharedGeo) obj.geometry.dispose();
    if (obj.material) {
      if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
      else obj.material.dispose();
    }
  }
}

// Präziser Punktmarker: Fadenkreuz mit offener Mitte + 1-px-Kernpunkt, feste
// Bildschirmgrösse (unabhängig vom Zoom), immer über dem Modell gezeichnet.
// Ersetzt die früheren Kugeln, die den exakten Punkt verdeckt haben.
let _markerTex = null;
function markerTexture() {
  if (_markerTex) return _markerTex;
  const N = 128, c = N / 2;
  const cv = document.createElement("canvas");
  cv.width = cv.height = N;
  const g = cv.getContext("2d");
  const draw = (stroke, w) => {
    g.strokeStyle = stroke;
    g.lineWidth = w;
    g.lineCap = "round";
    g.beginPath();
    g.arc(c, c, 30, 0, Math.PI * 2);
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      g.moveTo(c + dx * 12, c + dy * 12);
      g.lineTo(c + dx * 58, c + dy * 58);
    }
    g.stroke();
  };
  draw("rgba(0,0,0,0.85)", 12);   // dunkler Rand für Kontrast auf hellen Flächen
  draw("#ffffff", 5);             // wird über material.color eingefärbt
  g.fillStyle = "rgba(0,0,0,0.9)";
  g.beginPath(); g.arc(c, c, 5.5, 0, Math.PI * 2); g.fill();
  g.fillStyle = "#ffffff";
  g.beginPath(); g.arc(c, c, 2.8, 0, Math.PI * 2); g.fill();
  _markerTex = new THREE.CanvasTexture(cv);
  _markerTex.colorSpace = THREE.SRGBColorSpace;
  return _markerTex;
}

function pointMarker(pos, color, px = 26) {
  const geo = new THREE.BufferGeometry().setFromPoints([pos]);
  const mat = new THREE.PointsMaterial({
    color,
    map: markerTexture(),
    size: px * renderer.getPixelRatio(),
    sizeAttenuation: false,
    transparent: true,
    depthTest: false,
    depthWrite: false,
  });
  const pts = new THREE.Points(geo, mat);
  pts.renderOrder = 999;
  pts.raycast = () => {};          // Marker nie selbst treffen
  return pts;
}

function showPendingMarker(hit, color) {
  clearGroup(previewGroup);
  const p = S.viewMode === "print" ? hit.print_xyz : hit.model_xyz;
  previewGroup.add(pointMarker(new THREE.Vector3(...p), color));
}

// Pfeil A→B, dessen Spitze kurz vor B endet (B bleibt sichtbar).
function moveArrow(pA, pB, color) {
  const dir = pB.clone().sub(pA);
  const len = dir.length();
  if (len < 0.2) return new THREE.Group();
  const gap = Math.min(1.5, len * 0.2);
  const l = len - gap;
  return new THREE.ArrowHelper(dir.normalize(), pA, l, color, Math.min(4, l * 0.3), Math.min(2.2, l * 0.18));
}

function buildEnvironment() {
  clearGroup(envGroup);
  axisLabelAnchors.forEach((a) => a.el.remove());
  axisLabelAnchors = [];

  const bed = S.state?.printer_bed || { x: 260, y: 260, z: 300, usable_xy: [250, 250] };
  const bx = bed.x || 260;
  const by = bed.y || 260;

  if (isAssembly()) {
    buildAssemblyEnvironment();
    return;
  }

  if (S.viewMode === "print") {
    // Creality Hi 260x260 Build Plate ([0..260, 0..260, z=0])
    const plateGeo = new THREE.PlaneGeometry(bx, by);
    const plateMat = new THREE.MeshStandardMaterial({
      color: 0x141c29,
      roughness: 0.88,
      metalness: 0.12,
      side: THREE.DoubleSide,
    });
    const plate = new THREE.Mesh(plateGeo, plateMat);
    plate.position.set(bx / 2, by / 2, -0.08);
    envGroup.add(plate);

    // 10mm Grid Lines
    const pts = [];
    for (let x = 0; x <= bx; x += 10) {
      pts.push(x, 0, -0.03, x, by, -0.03);
    }
    for (let y = 0; y <= by; y += 10) {
      pts.push(0, y, -0.03, bx, y, -0.03);
    }
    const gridGeo = new THREE.BufferGeometry();
    gridGeo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    const gridLines = new THREE.LineSegments(
      gridGeo,
      new THREE.LineBasicMaterial({ color: 0x263449, transparent: true, opacity: 0.65 })
    );
    envGroup.add(gridLines);

    // Usable margin border (250x250 centered)
    const ux = bed.usable_xy?.[0] || 250;
    const uy = bed.usable_xy?.[1] || 250;
    const mx = (bx - ux) / 2;
    const my = (by - uy) / 2;
    const borderPts = [
      new THREE.Vector3(mx, my, 0.02),
      new THREE.Vector3(bx - mx, my, 0.02),
      new THREE.Vector3(bx - mx, by - my, 0.02),
      new THREE.Vector3(mx, by - my, 0.02),
      new THREE.Vector3(mx, my, 0.02),
    ];
    const borderGeo = new THREE.BufferGeometry().setFromPoints(borderPts);
    envGroup.add(
      new THREE.Line(borderGeo, new THREE.LineBasicMaterial({ color: 0x3b5275, transparent: true, opacity: 0.8 }))
    );

    addAxesHelper(new THREE.Vector3(0, 0, 0.1), 55);
  } else {
    // Model Space Origin (centered around 0,0,0)
    const size = 200;
    const pts = [];
    for (let v = -size / 2; v <= size / 2; v += 10) {
      pts.push(v, -size / 2, -0.03, v, size / 2, -0.03);
      pts.push(-size / 2, v, -0.03, size / 2, v, -0.03);
    }
    const gridGeo = new THREE.BufferGeometry();
    gridGeo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    envGroup.add(
      new THREE.LineSegments(
        gridGeo,
        new THREE.LineBasicMaterial({ color: 0x263449, transparent: true, opacity: 0.65 })
      )
    );
    addAxesHelper(new THREE.Vector3(0, 0, 0.05), 50);
  }
}

function addAxesHelper(origin, len) {
  const axes = [
    { dir: new THREE.Vector3(1, 0, 0), col: 0xef4444, txt: "+X", css: "#f87171" },
    { dir: new THREE.Vector3(0, 1, 0), col: 0x22c55e, txt: "+Y", css: "#4ade80" },
    { dir: new THREE.Vector3(0, 0, 1), col: 0x3b82f6, txt: "+Z", css: "#60a5fa" },
  ];
  for (const ax of axes) {
    const arrow = new THREE.ArrowHelper(ax.dir, origin, len, ax.col, 6, 3.2);
    envGroup.add(arrow);
    const el = document.createElement("div");
    el.className = "label-3d axis";
    el.style.color = ax.css;
    el.textContent = ax.txt;
    labelsLayer.appendChild(el);
    axisLabelAnchors.push({
      el,
      pos: origin.clone().addScaledVector(ax.dir, len + 5),
    });
  }
}

// ============================================================================
// Loading STL Geometry, Overlays, Features & Ghost Versions
// ============================================================================
function loadSTL(url) {
  return new Promise((resolve, reject) => {
    stlLoader.load(url, (geo) => resolve(geo), undefined, (err) => reject(err));
  });
}

function activeClipPlanes() {
  return S.clip.axis === "off" ? [] : [clipPlane];
}

// Jeder Aufruf bekommt eine Nummer. Läuft ein neuerer (Projekt-/Versionswechsel während des
// Ladens), bricht der ältere nach seinem nächsten await ab, statt seine Netze dazuzumischen.
let sceneGeneration = 0;

async function rebuildSceneGeometry(fitCameraAfter = false) {
  const gen = ++sceneGeneration;
  const stale = () => gen !== sceneGeneration;
  clearGroup(meshGroup);
  clearGroup(edgesGroup);
  clearGroup(errorGroup);
  clearGroup(ghostCutGroup);
  clearGroup(featureGroup);
  clearGroup(asmGhostGroup);

  if (!S.state) return;
  buildEnvironment();

  if (isAssembly()) {
    await buildAssemblyScene(stale);
    if (stale()) return;
    edgesGroup.visible = S.overlays.edges;
    errorGroup.visible = S.overlays.errors;
    rebuildAnnotationVisuals();
    updateClipPlane();
    renderSceneLegend();
    if (fitCameraAfter && S.partBounds) setCameraPreset("iso");
    return;
  }

  const meshesInfo = S.state.meshes || [];
  const overallBox = new THREE.Box3();
  const planes = activeClipPlanes();

  // 1. Load main body meshes
  for (const m of meshesInfo) {
    if (isImport() && S.imported.hidden.has(m.id)) continue;
    const url = S.viewMode === "print" ? m.print_url : m.model_url;
    if (!url) continue;
    try {
      const geo = await loadSTL(url);
      if (stale()) return;
      geo.computeVertexNormals();
      geo.computeBoundingBox();

      const mat = new THREE.MeshStandardMaterial({
        color: new THREE.Color(m.color || "#4682B4"),
        roughness: 0.42,
        metalness: 0.08,
        side: THREE.DoubleSide,
        clippingPlanes: planes,
        clipShadows: true,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.userData = { bodyName: m.name };
      meshGroup.add(mesh);
      overallBox.expandByObject(mesh);

      // Sharp CAD crease edges (15° threshold)
      const edgeGeo = new THREE.EdgesGeometry(geo, 15);
      const edgeMat = new THREE.LineBasicMaterial({
        color: 0x090d16,
        transparent: true,
        opacity: 0.55,
        clippingPlanes: planes,
      });
      edgesGroup.add(new THREE.LineSegments(edgeGeo, edgeMat));
    } catch (err) {
      console.warn("STL Ladefehler:", url, err);
    }
  }

  S.partBounds = overallBox.isEmpty() ? null : overallBox;
  edgesGroup.visible = S.overlays.edges;

  // 2. Load empty_cut ghost STLs (missed cuts floating outside the body!)
  for (const gm of S.state.ghost_meshes || []) {
    const gUrl = S.viewMode === "print" ? gm.print_url : gm.model_url;
    if (!gUrl) continue;
    try {
      const gGeo = await loadSTL(gUrl);
      if (stale()) return;
      gGeo.computeVertexNormals();
      const gMat = new THREE.MeshStandardMaterial({
        color: 0xff2e4c,
        emissive: 0x990018,
        transparent: true,
        opacity: 0.48,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const gMesh = new THREE.Mesh(gGeo, gMat);
      gMesh.userData = { isGhostCut: true, featureName: gm.name };
      ghostCutGroup.add(gMesh);

      const gEdges = new THREE.EdgesGeometry(gGeo, 20);
      ghostCutGroup.add(
        new THREE.LineSegments(gEdges, new THREE.LineBasicMaterial({ color: 0xff6b81 }))
      );
      overallBox.expandByObject(gMesh);
    } catch (e) {
      console.warn("Ghost STL Fehler:", e);
    }
  }
  ghostCutGroup.visible = S.overlays.errors;

  // 3. Build Triangle-Exact 3D Diagnostic Overlays (thin_walls, overhangs, bad_bridges, floating_parts, open_edges)
  await buildDiagnosticOverlays(gen);
  if (stale()) return;

  // 4. Build Tagged Feature Wireframes
  buildFeatureFrames();

  // 5. Rebuild Annotations & Ghost Version in active coordinate space
  rebuildAnnotationVisuals();
  await rebuildGhostVersionMesh();
  if (stale()) return;
  updateClipPlane();
  renderSceneLegend();

  if (fitCameraAfter && S.partBounds) {
    setCameraPreset("iso");
  }
}

async function buildDiagnosticOverlays(gen = sceneGeneration) {
  clearGroup(errorGroup);
  const diag = S.state?.diagnostics || {};
  const fullUrl = S.viewMode === "print" ? S.state?.full_print_stl_url : S.state?.full_model_stl_url;

  if (fullUrl) {
    try {
      const fullGeo = await loadSTL(fullUrl);
      if (gen !== sceneGeneration) return;
      const posAttr = fullGeo.getAttribute("position");
      const totalTris = Math.floor(posAttr.count / 3);

      const collectSubset = (items, hexColor) => {
        const triList = [];
        for (const it of items || []) {
          for (const ti of it.tri_indices || []) {
            if (ti >= 0 && ti < totalTris) triList.push(ti);
          }
        }
        if (!triList.length) return;
        const arr = new Float32Array(triList.length * 9);
        let ptr = 0;
        for (const ti of triList) {
          const base = ti * 3;
          for (let v = 0; v < 3; v++) {
            arr[ptr++] = posAttr.getX(base + v);
            arr[ptr++] = posAttr.getY(base + v);
            arr[ptr++] = posAttr.getZ(base + v);
          }
        }
        const subGeo = new THREE.BufferGeometry();
        subGeo.setAttribute("position", new THREE.BufferAttribute(arr, 3));
        subGeo.computeVertexNormals();
        const mat = new THREE.MeshBasicMaterial({
          color: hexColor,
          transparent: true,
          opacity: 0.78,
          side: THREE.DoubleSide,
          depthTest: true,
          polygonOffset: true,
          polygonOffsetFactor: -2,
          polygonOffsetUnits: -2,
          clippingPlanes: activeClipPlanes(),
        });
        errorGroup.add(new THREE.Mesh(subGeo, mat));
      };

      // Red: thin walls & floating parts
      collectSubset([...(diag.thin_walls || []), ...(diag.floating_parts || [])], 0xef4444);
      // Amber/Orange: overhangs & bad bridges
      collectSubset([...(diag.overhangs || []), ...(diag.bad_bridges || [])], 0xf59e0b);
      fullGeo.dispose();
    } catch (e) {
      console.warn("Fehler-Overlay Aufbau fehlgeschlagen:", e);
    }
  }

  // Open edges (3D line segments)
  for (const oe of diag.open_edges || []) {
    const segs = oe.segments_print || oe.segments || [];
    if (!segs.length) continue;
    const pts = [];
    for (const pair of segs) {
      if (pair.length === 2) {
        const p1 = S.viewMode === "print" ? pair[0] : printToModel(pair[0]);
        const p2 = S.viewMode === "print" ? pair[1] : printToModel(pair[1]);
        pts.push(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2]);
      }
    }
    if (pts.length) {
      const lGeo = new THREE.BufferGeometry();
      lGeo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      errorGroup.add(new THREE.LineSegments(lGeo, new THREE.LineBasicMaterial({ color: 0xff0055 })));
    }
  }

  errorGroup.visible = S.overlays.errors;
}

function buildFeatureFrames() {
  clearGroup(featureGroup);
  const feats = S.state?.diagnostics?.features || S.state?.build?.features || [];
  for (const f of feats) {
    let bb6 = null;
    if (S.viewMode === "print" && f.bbox_print?.length === 6) {
      bb6 = f.bbox_print;
    } else if (f.bbox_min && f.bbox_max) {
      const m6 = [...f.bbox_min, ...f.bbox_max];
      bb6 = S.viewMode === "print" ? bboxCornersTransform(m6, modelToPrint) : m6;
    }
    if (!bb6) continue;
    const box3 = new THREE.Box3(
      new THREE.Vector3(bb6[0], bb6[1], bb6[2]),
      new THREE.Vector3(bb6[3], bb6[4], bb6[5])
    );
    const col = f.empty_cut ? 0xef4444 : f.role === "cut" ? 0xf59e0b : 0x38bdf8;
    const helper = new THREE.Box3Helper(box3, col);
    helper.userData = { featureName: f.name, center: box3.getCenter(new THREE.Vector3()) };
    featureGroup.add(helper);
  }
  featureGroup.visible = S.overlays.features;
  syncHtmlLabels();
}

let ghostGeneration = 0;

async function rebuildGhostVersionMesh() {
  const gen = ++ghostGeneration;
  clearGroup(ghostVersionGroup);
  const banner = document.getElementById("ghost-active-banner");
  if (!S.ghostVersion || !S.currentProject) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  document.getElementById("ghost-version-label").textContent = S.ghostVersion;

  const fname = S.viewMode === "print" ? "print.stl" : "model.stl";
  const url = `/api/projects/${encodeURIComponent(S.currentProject)}/files/history/${encodeURIComponent(S.ghostVersion)}/${fname}?t=${Date.now()}`;
  try {
    const geo = await loadSTL(url);
    if (gen !== ghostGeneration) return;
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({
      color: 0xf59e0b,
      transparent: true,
      opacity: 0.28,
      roughness: 0.5,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    ghostVersionGroup.add(new THREE.Mesh(geo, mat));
    const eGeo = new THREE.EdgesGeometry(geo, 20);
    ghostVersionGroup.add(
      new THREE.LineSegments(
        eGeo,
        new THREE.LineBasicMaterial({ color: 0xfbbf24, transparent: true, opacity: 0.55 })
      )
    );
  } catch (err) {
    showToast(`Geisterbild ${S.ghostVersion} konnte nicht geladen werden.`);
  }
}

// ============================================================================
// Clipping Plane
// ============================================================================
function updateClipPlane() {
  const slider = document.getElementById("clip-slider");
  const flipChk = document.getElementById("clip-flip");
  const isOff = S.clip.axis === "off";
  slider.disabled = isOff;
  flipChk.disabled = isOff;

  const planes = activeClipPlanes();
  meshGroup.traverse((o) => {
    if (o.material) o.material.clippingPlanes = planes;
  });
  edgesGroup.traverse((o) => {
    if (o.material) o.material.clippingPlanes = planes;
  });
  errorGroup.traverse((o) => {
    if (o.material) o.material.clippingPlanes = planes;
  });
  asmGhostGroup.traverse((o) => {
    if (o.material) o.material.clippingPlanes = planes;
  });

  if (isOff || !S.partBounds) return;

  const min = S.partBounds.min;
  const max = S.partBounds.max;
  const t = S.clip.percent / 100;
  const sign = S.clip.flip ? 1 : -1;

  if (S.clip.axis === "x") {
    const val = min.x + (max.x - min.x) * t;
    clipPlane.set(new THREE.Vector3(sign, 0, 0), -sign * val);
  } else if (S.clip.axis === "y") {
    const val = min.y + (max.y - min.y) * t;
    clipPlane.set(new THREE.Vector3(0, sign, 0), -sign * val);
  } else if (S.clip.axis === "z") {
    const val = min.z + (max.z - min.z) * t;
    clipPlane.set(new THREE.Vector3(0, 0, sign), -sign * val);
  }
}

// ============================================================================
// Camera Presets & Smooth Focus
// ============================================================================
function setCameraPreset(preset) {
  const box = S.partBounds || new THREE.Box3(new THREE.Vector3(80, 80, 0), new THREE.Vector3(180, 180, 40));
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 35);
  const dist = maxDim * 2.05;

  const dirs = {
    iso: new THREE.Vector3(0.75, -0.85, 0.68).normalize(),
    top: new THREE.Vector3(0, -0.001, 1).normalize(),
    bottom: new THREE.Vector3(0, 0.001, -1).normalize(),
    front: new THREE.Vector3(0, -1, 0.12).normalize(),
    back: new THREE.Vector3(0, 1, 0.12).normalize(),
    right: new THREE.Vector3(1, 0, 0.12).normalize(),
    left: new THREE.Vector3(-1, 0, 0.12).normalize(),
    fit: camera.position.clone().sub(controls.target).normalize(),
  };
  const d = dirs[preset] || dirs.iso;
  controls.target.copy(center);
  camera.position.copy(center).addScaledVector(d, dist);
  controls.update();
}

function focusBox3D(bbox6InPrint, bbox6InModel) {
  let b6 = S.viewMode === "print" ? bbox6InPrint : bbox6InModel;
  if (!b6 && bbox6InModel) {
    b6 = S.viewMode === "print" ? bboxCornersTransform(bbox6InModel, modelToPrint) : bbox6InModel;
  }
  if (!b6) return;

  const box = new THREE.Box3(
    new THREE.Vector3(b6[0], b6[1], b6[2]),
    new THREE.Vector3(b6[3], b6[4], b6[5])
  );
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.length(), 18);

  const viewDir = camera.position.clone().sub(controls.target).normalize();
  controls.target.copy(center);
  camera.position.copy(center).addScaledVector(viewDir, span * 2.3);
  controls.update();

  // Pulse highlight box for 2.2 seconds
  clearGroup(focusPulseGroup);
  const expand = box.clone().expandByScalar(1.2);
  const helper = new THREE.Box3Helper(expand, 0x38bdf8);
  focusPulseGroup.add(helper);

  const g = new THREE.BoxGeometry(
    Math.max(expand.max.x - expand.min.x, 1.5),
    Math.max(expand.max.y - expand.min.y, 1.5),
    Math.max(expand.max.z - expand.min.z, 1.5)
  );
  const m = new THREE.MeshBasicMaterial({
    color: 0x38bdf8,
    transparent: true,
    opacity: 0.24,
    depthWrite: false,
  });
  const pulseMesh = new THREE.Mesh(g, m);
  pulseMesh.position.copy(center);
  focusPulseGroup.add(pulseMesh);

  if (S.flashTimer) clearTimeout(S.flashTimer);
  S.flashTimer = setTimeout(() => clearGroup(focusPulseGroup), 2400);
}

// ============================================================================
// Raycasting, Feature Detection & Interactive Tools
// ============================================================================
function getPointerRayHit(clientX, clientY, allowBedFallback = false) {
  const rect = canvas.getBoundingClientRect();
  mouseNDC.x = ((clientX - rect.left) / rect.width) * 2 - 1;
  mouseNDC.y = -((clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(mouseNDC, camera);

  const targets = [...meshGroup.children, ...ghostCutGroup.children.filter((c) => c.isMesh)].filter((o) => o.visible);
  const hits = raycaster.intersectObjects(targets, false);

  if (hits.length > 0) {
    const h = hits[0];
    const pScene = [h.point.x, h.point.y, h.point.z];
    const nFace = h.face
      ? h.face.normal.clone().transformDirection(h.object.matrixWorld)
      : new THREE.Vector3(0, 0, 1);
    const nScene = [nFace.x, nFace.y, nFace.z];

    const pPrint = S.viewMode === "print" ? pScene : modelToPrint(pScene);
    const pModel = S.viewMode === "model" ? pScene : printToModel(pScene);
    const nModel = S.viewMode === "model" ? nScene : normalPrintToModel(nScene);
    const nPrint = S.viewMode === "print" ? nScene : normalModelToPrint(nScene);

    const bodyName = h.object.userData?.bodyName || (h.object.userData?.isGhostCut ? `ghost:${h.object.userData.featureName}` : "_teil");
    const featName = h.object.userData?.featureName || detectFeatureAtModelPoint(pModel);

    return {
      onMesh: true,
      scene_xyz: pScene,
      print_xyz: round3(pPrint),
      model_xyz: round3(pModel),
      print_normal: round3(nPrint),
      model_normal: round3(nModel),
      body: bodyName,
      feature: featName,
    };
  }

  if (allowBedFallback) {
    const targetVec = new THREE.Vector3();
    if (raycaster.ray.intersectPlane(bedPlaneMath, targetVec)) {
      const pScene = [targetVec.x, targetVec.y, targetVec.z];
      const pPrint = S.viewMode === "print" ? pScene : modelToPrint(pScene);
      const pModel = S.viewMode === "model" ? pScene : printToModel(pScene);
      return {
        onMesh: false,
        scene_xyz: pScene,
        print_xyz: round3(pPrint),
        model_xyz: round3(pModel),
        print_normal: [0, 0, 1],
        model_normal: round3(S.viewMode === "model" ? [0, 0, 1] : normalPrintToModel([0, 0, 1])),
        body: null,
        feature: detectFeatureAtModelPoint(pModel),
      };
    }
  }
  return null;
}

function detectFeatureAtModelPoint(mPt) {
  const feats = S.state?.build?.features || [];
  let best = null;
  let bestVol = Infinity;
  const pad = 0.6;
  for (const f of feats) {
    if (!f.bbox_min || !f.bbox_max) continue;
    if (
      mPt[0] >= f.bbox_min[0] - pad && mPt[0] <= f.bbox_max[0] + pad &&
      mPt[1] >= f.bbox_min[1] - pad && mPt[1] <= f.bbox_max[1] + pad &&
      mPt[2] >= f.bbox_min[2] - pad && mPt[2] <= f.bbox_max[2] + pad
    ) {
      const vol = f.volume_mm3 || f.cutter_volume_mm3 || 1e9;
      if (vol <= bestVol) {
        bestVol = vol;
        best = f.name;
      }
    }
  }
  return best;
}

function detectFeaturesInsideModelBox(bbox6) {
  const feats = S.state?.build?.features || [];
  const inside = [];
  for (const f of feats) {
    if (!f.center) continue;
    const c = f.center;
    if (
      c[0] >= bbox6[0] && c[0] <= bbox6[3] &&
      c[1] >= bbox6[1] && c[1] <= bbox6[4] &&
      c[2] >= bbox6[2] && c[2] <= bbox6[5]
    ) {
      inside.push(f.name);
    }
  }
  return inside;
}

function setTool(toolName) {
  if (isImport() && !["orbit", "pin", "box", "move", "measure"].includes(toolName)) return;
  S.tool = toolName;
  S.pendingPoint = null;
  clearGroup(previewGroup);

  document.querySelectorAll(".tool-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.tool === toolName);
  });

  const hintBar = document.getElementById("tool-hint-bar");
  const hints = {
    orbit: "",
    pin: "📍 Pin-Modus: Klicke direkt auf die fehlerhafte Stelle, Wand oder Bohrung am 3D-Modell.",
    box: "⬚ Bereichsbox: 1. Klick = Startpunkt am Bauteil/Bett → 2. Klick = gegenüberliegende Ecke (Höhe Z rechts feinjustierbar).",
    move: "➔ Von→Nach: 1. Klick auf die aktuell falsche Position → 2. Klick auf die gewünschte Zielposition (Fläche oder Bettraster).",
    measure: "📏 Messband: 1. Klick = Startpunkt A → 2. Klick = Endpunkt B (zeigt Distanz & |ΔX|, |ΔY|, |ΔZ| in mm).",
    bewegen: "✥ Bewegen: Teil anklicken (Umschalt = mehrere) und ziehen – es folgt nur seinen Gelenken, nicht gewählte Teile bleiben fest, Kontakt stoppt.",
  };
  if (hints[toolName]) {
    hintBar.textContent = hints[toolName];
    hintBar.classList.remove("hidden");
  } else {
    hintBar.classList.add("hidden");
  }
  canvas.style.cursor = toolName === "orbit" ? "grab" : toolName === "bewegen" ? "move" : "crosshair";
  if (toolName === "bewegen" && kinData()) {
    if (S.asm.explode) {
      S.asm.explode = 0;
      document.getElementById("explode-slider").value = "0";
      document.getElementById("explode-value").textContent = "0 %";
      applyExplosion();
    }
    kinSession();
  }
  if (isAssembly()) kinHighlight();
}

// Bewegen-Werkzeug: vor OrbitControls (Capture-Phase)
canvas.addEventListener("pointerdown", (ev) => { kinPointerDown(ev); }, { capture: true });
canvas.addEventListener("pointermove", (ev) => { if (kinPointerMove(ev)) ev.stopImmediatePropagation(); }, { capture: true });
canvas.addEventListener("pointerup", (ev) => { kinPointerUp(ev); }, { capture: true });

// Distinguish clean click from orbit drag
let downXY = null;
canvas.addEventListener("pointerdown", (ev) => {
  if (ev.button === 0) downXY = [ev.clientX, ev.clientY];
});

canvas.addEventListener("pointermove", (ev) => {
  const hit = getPointerRayHit(ev.clientX, ev.clientY, S.tool !== "orbit");
  const curX = document.getElementById("cur-x");
  const curY = document.getElementById("cur-y");
  const curZ = document.getElementById("cur-z");
  const featLbl = document.getElementById("cur-feature-label");

  if (hit) {
    const coords = S.viewMode === "print" ? hit.print_xyz : hit.model_xyz;
    curX.textContent = coords[0].toFixed(1);
    curY.textContent = coords[1].toFixed(1);
    curZ.textContent = coords[2].toFixed(1);
    featLbl.textContent = hit.feature ? `· [${hit.feature}]` : "";
  } else {
    curX.textContent = "–";
    curY.textContent = "–";
    curZ.textContent = "–";
    featLbl.textContent = "";
  }

  // Live rubberband preview for 2-click tools
  if (S.pendingPoint && hit) {
    clearGroup(previewGroup);
    const pA = new THREE.Vector3(...(S.viewMode === "print" ? S.pendingPoint.print_xyz : S.pendingPoint.model_xyz));
    const pB = new THREE.Vector3(...(S.viewMode === "print" ? hit.print_xyz : hit.model_xyz));

    if (S.tool === "move" || S.tool === "measure") {
      const col = S.tool === "move" ? 0xf59e0b : 0x06b6d4;
      if (S.tool === "move") {
        previewGroup.add(moveArrow(pA, pB, col));
      } else {
        previewGroup.add(new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([pA, pB]),
          new THREE.LineBasicMaterial({ color: col })
        ));
      }
      previewGroup.add(pointMarker(pA, col));
      previewGroup.add(pointMarker(pB, S.tool === "move" ? 0x10b981 : col));
    } else if (S.tool === "box") {
      const partMaxZ = S.partBounds ? S.partBounds.max.z : 15;
      const z0 = Math.min(pA.z, pB.z, 0);
      const z1 = Math.max(pA.z, pB.z, partMaxZ);
      const b3 = new THREE.Box3(
        new THREE.Vector3(Math.min(pA.x, pB.x) - 1, Math.min(pA.y, pB.y) - 1, z0),
        new THREE.Vector3(Math.max(pA.x, pB.x) + 1, Math.max(pA.y, pB.y) + 1, z1)
      );
      previewGroup.add(new THREE.Box3Helper(b3, 0xa855f7));
    }
  }
});

canvas.addEventListener("pointerup", (ev) => {
  if (ev.button !== 0 || !downXY) return;
  const distPx = Math.hypot(ev.clientX - downXY[0], ev.clientY - downXY[1]);
  downXY = null;
  if (distPx > 5 || S.tool === "orbit") return;

  const hit = getPointerRayHit(ev.clientX, ev.clientY, S.tool !== "pin");
  if (!hit) return;

  if (S.tool === "pin") {
    S.annotations.push({
      id: S.nextAnnId++,
      type: "pin",
      body: hit.body || "_teil",
      feature: hit.feature || null,
      model_xyz: hit.model_xyz,
      model_normal: hit.model_normal,
      print_xyz: hit.print_xyz,
      print_normal: hit.print_normal,
      note: "",
    });
    switchRightTab("diagnose");
    rebuildAnnotationVisuals();
    renderAnnotationsList();
    focusNewestNoteInput();
  } else if (S.tool === "move") {
    if (!S.pendingPoint) {
      S.pendingPoint = hit;
      showPendingMarker(hit, 0xf59e0b);
      showToast("Startpunkt gesetzt – klicke jetzt auf die gewünschte Zielposition.");
    } else {
      const start = S.pendingPoint;
      S.pendingPoint = null;
      clearGroup(previewGroup);
      const deltaModel = round3([
        hit.model_xyz[0] - start.model_xyz[0],
        hit.model_xyz[1] - start.model_xyz[1],
        hit.model_xyz[2] - start.model_xyz[2],
      ]);
      S.annotations.push({
        id: S.nextAnnId++,
        type: "move",
        body: start.body || "_teil",
        feature: start.feature || null,
        from_model_xyz: start.model_xyz,
        to_model_xyz: hit.model_xyz,
        from_print_xyz: start.print_xyz,
        to_print_xyz: hit.print_xyz,
        delta_model_mm: deltaModel,
        note: "",
      });
      switchRightTab("diagnose");
      rebuildAnnotationVisuals();
      renderAnnotationsList();
      focusNewestNoteInput();
    }
  } else if (S.tool === "box") {
    if (!S.pendingPoint) {
      S.pendingPoint = hit;
      showToast("1. Ecke gesetzt – klicke jetzt auf die diagonal gegenüberliegende Ecke.");
    } else {
      const start = S.pendingPoint;
      S.pendingPoint = null;
      clearGroup(previewGroup);

      const mZMaxDefault = Math.max(
        start.model_xyz[2],
        hit.model_xyz[2],
        S.state?.build?.size_mm?.[2] || 10
      );
      const bboxModel = round3([
        Math.min(start.model_xyz[0], hit.model_xyz[0]) - 1.0,
        Math.min(start.model_xyz[1], hit.model_xyz[1]) - 1.0,
        Math.min(start.model_xyz[2], hit.model_xyz[2], 0),
        Math.max(start.model_xyz[0], hit.model_xyz[0]) + 1.0,
        Math.max(start.model_xyz[1], hit.model_xyz[1]) + 1.0,
        mZMaxDefault,
      ]);
      const bboxPrint = round3(bboxCornersTransform(bboxModel, modelToPrint));
      S.annotations.push({
        id: S.nextAnnId++,
        type: "box",
        bbox_model: bboxModel,
        bbox_print: bboxPrint,
        features_inside: detectFeaturesInsideModelBox(bboxModel),
        note: "",
      });
      switchRightTab("diagnose");
      rebuildAnnotationVisuals();
      renderAnnotationsList();
      focusNewestNoteInput();
    }
  } else if (S.tool === "measure") {
    if (!S.pendingPoint) {
      S.pendingPoint = hit;
      showPendingMarker(hit, 0x06b6d4);
      showToast("Messpunkt A gesetzt – klicke Punkt B.");
    } else {
      const a = S.pendingPoint;
      S.pendingPoint = null;
      clearGroup(previewGroup);
      const dx = hit.model_xyz[0] - a.model_xyz[0];
      const dy = hit.model_xyz[1] - a.model_xyz[1];
      const dz = hit.model_xyz[2] - a.model_xyz[2];
      const dist = Math.hypot(dx, dy, dz);
      S.measurement = {
        a_model: a.model_xyz,
        b_model: hit.model_xyz,
        a_print: a.print_xyz,
        b_print: hit.print_xyz,
        dist: Math.round(dist * 100) / 100,
        delta: round3([Math.abs(dx), Math.abs(dy), Math.abs(dz)]),
      };
      rebuildAnnotationVisuals();
      renderMeasureBox();
    }
  }
});

// ============================================================================
// 3D Annotation Visuals + Floating HTML Callout Labels
// ============================================================================
function rebuildAnnotationVisuals() {
  clearGroup(annotationGroup);
  syncHtmlLabels();

  for (const ann of S.annotations) {
    if (ann.type === "pin") {
      const pt = S.viewMode === "print" ? ann.print_xyz : ann.model_xyz;
      const nrm = S.viewMode === "print" ? (ann.print_normal || [0, 0, 1]) : (ann.model_normal || [0, 0, 1]);
      const vPt = new THREE.Vector3(...pt);
      const vN = new THREE.Vector3(...nrm).normalize();

      // Pfeil beginnt knapp über der Fläche, damit der Punkt selbst frei bleibt
      const arrow = new THREE.ArrowHelper(vN, vPt.clone().addScaledVector(vN, 2.5), 9, 0xef4444, 3.2, 1.8);
      annotationGroup.add(arrow);
      annotationGroup.add(pointMarker(vPt, 0xef4444));
    } else if (ann.type === "move") {
      const pFrom = new THREE.Vector3(...(S.viewMode === "print" ? ann.from_print_xyz : ann.from_model_xyz));
      const pTo = new THREE.Vector3(...(S.viewMode === "print" ? ann.to_print_xyz : ann.to_model_xyz));
      annotationGroup.add(moveArrow(pFrom, pTo, 0xf59e0b));
      annotationGroup.add(pointMarker(pFrom, 0xf59e0b));
      annotationGroup.add(pointMarker(pTo, 0x10b981));
    } else if (ann.type === "box") {
      const b6 = S.viewMode === "print"
        ? (ann.bbox_print || bboxCornersTransform(ann.bbox_model, modelToPrint))
        : ann.bbox_model;
      const box3 = new THREE.Box3(
        new THREE.Vector3(b6[0], b6[1], b6[2]),
        new THREE.Vector3(b6[3], b6[4], b6[5])
      );
      annotationGroup.add(new THREE.Box3Helper(box3, 0xa855f7));

      const sz = box3.getSize(new THREE.Vector3());
      const ctr = box3.getCenter(new THREE.Vector3());
      const boxMesh = new THREE.Mesh(
        new THREE.BoxGeometry(Math.max(sz.x, 0.5), Math.max(sz.y, 0.5), Math.max(sz.z, 0.5)),
        new THREE.MeshBasicMaterial({
          color: 0xa855f7,
          transparent: true,
          opacity: 0.18,
          depthWrite: false,
        })
      );
      boxMesh.position.copy(ctr);
      annotationGroup.add(boxMesh);
    }
  }

  // Active 2-point measurement line
  if (S.measurement) {
    const pA = new THREE.Vector3(...(S.viewMode === "print" ? S.measurement.a_print : S.measurement.a_model));
    const pB = new THREE.Vector3(...(S.viewMode === "print" ? S.measurement.b_print : S.measurement.b_model));
    const lGeo = new THREE.BufferGeometry().setFromPoints([pA, pB]);
    annotationGroup.add(new THREE.Line(lGeo, new THREE.LineBasicMaterial({ color: 0x06b6d4 })));
    for (const p of [pA, pB]) annotationGroup.add(pointMarker(p, 0x06b6d4));
  }

  syncHtmlLabels();
}

function syncHtmlLabels() {
  labelAnchors.forEach((a) => a.el.remove());
  labelAnchors = [];

  for (const ann of S.annotations) {
    const el = document.createElement("div");
    let anchor = null;
    if (ann.type === "pin") {
      el.className = "label-3d pin";
      el.textContent = `#${ann.id}${ann.feature ? " · " + ann.feature : ""}`;
      const pt = S.viewMode === "print" ? ann.print_xyz : ann.model_xyz;
      const nrm = S.viewMode === "print" ? (ann.print_normal || [0, 0, 1]) : (ann.model_normal || [0, 0, 1]);
      anchor = new THREE.Vector3(...pt).addScaledVector(new THREE.Vector3(...nrm).normalize(), 12);
    } else if (ann.type === "move") {
      el.className = "label-3d move";
      el.textContent = `#${ann.id} Δ(${fmtVec(ann.delta_model_mm)}) mm`;
      const p1 = new THREE.Vector3(...(S.viewMode === "print" ? ann.from_print_xyz : ann.from_model_xyz));
      const p2 = new THREE.Vector3(...(S.viewMode === "print" ? ann.to_print_xyz : ann.to_model_xyz));
      anchor = p1.add(p2).multiplyScalar(0.5);
    } else if (ann.type === "box") {
      el.className = "label-3d box";
      el.textContent = `#${ann.id} Bereich`;
      const b6 = S.viewMode === "print"
        ? (ann.bbox_print || bboxCornersTransform(ann.bbox_model, modelToPrint))
        : ann.bbox_model;
      anchor = new THREE.Vector3((b6[0] + b6[3]) / 2, (b6[1] + b6[4]) / 2, b6[5] + 2);
    }
    if (anchor) {
      labelsLayer.appendChild(el);
      labelAnchors.push({ el, pos: anchor, text: el.textContent, kind: ann.type });
    }
  }

  if (S.measurement) {
    const el = document.createElement("div");
    el.className = "label-3d measure";
    el.textContent = `${S.measurement.dist.toFixed(2)} mm`;
    const p1 = new THREE.Vector3(...(S.viewMode === "print" ? S.measurement.a_print : S.measurement.a_model));
    const p2 = new THREE.Vector3(...(S.viewMode === "print" ? S.measurement.b_print : S.measurement.b_model));
    labelsLayer.appendChild(el);
    labelAnchors.push({ el, pos: p1.add(p2).multiplyScalar(0.5), text: el.textContent, kind: "measure" });
  }

  if (S.overlays.features) {
    for (const child of featureGroup.children) {
      if (child.userData?.featureName && child.userData?.center) {
        const el = document.createElement("div");
        el.className = "label-3d feature";
        el.textContent = child.userData.featureName;
        labelsLayer.appendChild(el);
        labelAnchors.push({ el, pos: child.userData.center.clone(), text: el.textContent, kind: "feature" });
      }
    }
  }
}

function projectToScreen(pos3, width, height) {
  const v = pos3.clone().project(camera);
  if (!isFinite(v.x) || !isFinite(v.y) || !isFinite(v.z) || v.z < -1 || v.z > 1) return null;
  return {
    x: (v.x * 0.5 + 0.5) * width,
    y: (-v.y * 0.5 + 0.5) * height,
  };
}

function updateLabelPositions() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  for (const item of [...labelAnchors, ...axisLabelAnchors]) {
    const sc = projectToScreen(item.pos, w, h);
    if (!sc) {
      item.el.style.display = "none";
    } else {
      item.el.style.display = "block";
      item.el.style.left = `${sc.x}px`;
      item.el.style.top = `${sc.y}px`;
    }
  }
}

/**
 * Renders WebGL scene + crisp 2D callout badges (#1, #2, notes) onto a composite PNG DataURL
 * so that Antigravity / Claude sees both the 3D geometry AND clear marker numbers via read_file!
 */
function captureAnnotatedScreenshotDataUrl() {
  renderer.render(scene, camera);
  const w = canvas.width;
  const h = canvas.height;
  const dpr = w / Math.max(canvas.clientWidth, 1);

  const off = document.createElement("canvas");
  off.width = w;
  off.height = h;
  const ctx = off.getContext("2d");
  ctx.drawImage(canvas, 0, 0);

  // Header watermark bar for AI visual orientation
  ctx.fillStyle = "rgba(11, 15, 22, 0.82)";
  ctx.fillRect(12 * dpr, 12 * dpr, 430 * dpr, 28 * dpr);
  ctx.font = `bold ${Math.round(12 * dpr)}px Consolas, monospace`;
  ctx.fillStyle = "#38bdf8";
  ctx.fillText(
    `SliceThePrompt · ${S.currentProject} (${S.activeVersion || "live"}, Ansicht: ${S.viewMode})`,
    20 * dpr,
    30 * dpr
  );

  // Draw callout badges directly onto screenshot
  for (const item of [...axisLabelAnchors, ...labelAnchors]) {
    const sc = projectToScreen(item.pos, w, h);
    if (!sc) continue;
    const text = item.text || item.el.textContent;
    ctx.font = `bold ${Math.round(12 * dpr)}px Consolas, monospace`;
    const padX = 6 * dpr;
    const tw = ctx.measureText(text).width;
    const bw = tw + padX * 2;
    const bh = 20 * dpr;
    const bx = sc.x - bw / 2;
    const by = sc.y - bh - 4 * dpr;

    ctx.fillStyle =
      item.kind === "pin" ? "#ef4444" :
      item.kind === "move" ? "#f59e0b" :
      item.kind === "box" ? "#9333ea" : "rgba(15, 23, 42, 0.85)";
    ctx.fillRect(bx, by, bw, bh);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1 * dpr;
    ctx.strokeRect(bx, by, bw, bh);

    ctx.fillStyle = item.kind === "move" ? "#111827" : "#ffffff";
    ctx.fillText(text, bx + padX, by + 14 * dpr);
  }

  return off.toDataURL("image/png");
}

// ============================================================================
// UI Rendering: Projects, Versions, Diagnostics, Annotations, Params, Specs
// ============================================================================
async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

async function loadProjectsList(selectProjectName = null) {
  S.projects = await apiFetch("/api/projects");
  const sel = document.getElementById("project-select");
  sel.innerHTML = "";

  if (!S.projects.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Keine Projekte gefunden";
    sel.appendChild(opt);
    S.currentProject = null;
    S.state = null;
    S.annotations = [];
    S.measurement = null;
    S.partBounds = null;
    S.ghostVersion = null;
    S.imported.file = null;
    S.imported.plate = null;
    enterOrLeaveImportMode();
    enterOrLeaveAssemblyMode(false);
    renderProjectSummary();
    renderVersionList();
    renderHistoricalBanner();
    renderDiagnosticsList();
    renderAnnotationsList();
    renderMeasureBox();
    await rebuildSceneGeometry();
    rebuildAnnotationVisuals();
    renderSceneLegend();
    await rebuildGhostVersionMesh();
    return;
  }

  for (const p of S.projects) {
    const opt = document.createElement("option");
    opt.value = p.project;
    const statusIcon = p.passed === true ? "✓" : p.passed === false ? "✗" : "•";
    const fbIcon = p.has_feedback ? " 📌" : "";
    const kind = p.kind === "assembly" ? "🧩 " : p.kind === "import" ? "3MF · " : "";
    opt.textContent = `${statusIcon} ${kind}${p.project}${fbIcon}`;
    if (p.kind === "assembly") opt.title = `Baugruppe: ${(p.bodies || []).join(", ")} · ${p.pose_count || 0} Posen`;
    sel.appendChild(opt);
  }

  const urlProj = new URLSearchParams(window.location.search).get("project");
  const target = [selectProjectName, S.currentProject, urlProj, S.projects[0].project]
    .find((name) => S.projects.some((p) => p.project === name));

  sel.value = target;
  if (target !== S.currentProject) {
    await selectProject(target, null, true);
  }
}

let selectSeq = 0;

async function selectProject(projName, version = null, fitCam = true) {
  if (!projName) return;
  const projectChanged = projName !== S.currentProject;
  S.currentProject = projName;
  S.activeVersion = version;
  if (projectChanged) {
    S.ghostVersion = null;
    S.measurement = null;
  }

  const query = new URLSearchParams();
  if (version) query.set("version", version);
  if (!projectChanged && S.imported.file) query.set("file", S.imported.file);
  if (!projectChanged && S.imported.plate) query.set("plate", S.imported.plate);
  const q = query.size ? `?${query}` : "";
  const req = ++selectSeq;
  const state = await apiFetch(`/api/projects/${encodeURIComponent(projName)}/state${q}`);
  if (req !== selectSeq) return; // inzwischen anderes Projekt/andere Version gewählt
  S.state = state;
  enterOrLeaveAssemblyMode(projectChanged);
  enterOrLeaveImportMode();

  // Restore saved open feedback annotations when switching to project live view
  if (projectChanged) {
    const fb = S.state.feedback;
    if (fb && Array.isArray(fb.annotations) && fb.annotations.length > 0) {
      S.annotations = fb.annotations.map((a, idx) => ({
        ...a,
        id: a.id || idx + 1,
        from_print_xyz: a.from_print_xyz || (a.from_model_xyz ? round3(modelToPrint(a.from_model_xyz)) : undefined),
        to_print_xyz: a.to_print_xyz || (a.to_model_xyz ? round3(modelToPrint(a.to_model_xyz)) : undefined),
        bbox_print: a.bbox_print || (a.bbox_model ? round3(bboxCornersTransform(a.bbox_model, modelToPrint)) : undefined),
      }));
      S.nextAnnId = Math.max(...S.annotations.map((a) => a.id), 0) + 1;
      document.getElementById("general-note").value = fb.general_note || "";
    } else {
      S.annotations = [];
      S.nextAnnId = 1;
      document.getElementById("general-note").value = "";
    }
  }

  renderProjectSummary();
  renderVersionList();
  renderHistoricalBanner();
  renderDiagnosticsList();
  renderAnnotationsList();
  renderMeasureBox();
  renderParamsForm();
  renderChecksAndSpec();
  renderCosts();
  refreshSidebarPartCard(projName);
  await rebuildSceneGeometry(fitCam);
}

function renderProjectSummary() {
  if (isImport()) {
    const err = S.state.import.error;
    const badge = document.getElementById("project-status-badge");
    badge.className = err ? "badge badge-err" : "badge badge-neutral";
    badge.textContent = err ? "Importfehler" : "3MF-Vorschau";
    const sz = S.state.build.size_mm;
    document.getElementById("project-dims-text").textContent = sz ? `${sz.map((n) => Number(n).toFixed(1)).join(" × ")} mm` : "–";
    document.getElementById("saved-feedback-pill").classList.add("hidden");
    return;
  }
  if (isAssembly()) {
    renderAssemblySummary();
    return;
  }
  const rep = S.state?.report || {};
  const badge = document.getElementById("project-status-badge");
  const dimsEl = document.getElementById("project-dims-text");

  if (rep.passed === true) {
    const warns = (rep.warnings || []).length;
    badge.className = warns ? "badge badge-warn" : "badge badge-ok";
    badge.textContent = warns ? `OK (${warns} Hinw.)` : "Prüfung OK";
  } else if (rep.passed === false) {
    badge.className = "badge badge-err";
    badge.textContent = `${(rep.errors || []).length} Fehler`;
  } else {
    badge.className = "badge badge-neutral";
    badge.textContent = "Ungeprüft";
  }

  const sz = rep.model?.size_mm || S.state?.build?.size_mm;
  dimsEl.textContent = sz ? `${sz.map((n) => Number(n).toFixed(1)).join(" × ")} mm` : "–";

  const savedBar = document.getElementById("saved-feedback-pill");
  savedBar.classList.toggle("hidden", !S.state?.feedback);
}

function renderHistoricalBanner() {
  const banner = document.getElementById("historical-banner");
  if (S.state?.is_historical) {
    document.getElementById("hist-banner-ver").textContent = S.state.version;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function formatReason(reason) {
  if (!reason) return "Build";
  if (reason === "build_and_check") return "Build & Check";
  if (reason === "ui_param_edit") return "Parameter-Edit";
  if (reason === "ui_rebuild") return "UI-Prüflauf";
  if (reason.startsWith("restore_")) return `Rollback (${reason.slice(8)})`;
  if (reason === "auto_backup_before_restore") return "Auto-Backup";
  return reason;
}

function renderVersionList() {
  const wrap = document.getElementById("version-list");
  wrap.innerHTML = "";
  const versions = [...(S.state?.versions || [])].reverse();

  if (!versions.length) {
    wrap.innerHTML = `<div class="empty-hint">Noch keine Snapshots vorhanden. Klicke „Neu prüfen“, um v001 anzulegen.</div>`;
    return;
  }

  const shownVer = S.state?.version || S.state?.latest_version;
  for (const v of versions) {
    const card = document.createElement("div");
    card.className = "version-card";
    if (v.version === shownVer) card.classList.add("active");
    if (v.version === S.ghostVersion) card.classList.add("ghosted");

    const timeShort = (v.timestamp || "").replace("T", " ").slice(5, 16);
    const sz = (v.size_mm || []).map((n) => Number(n).toFixed(1)).join("×");

    card.innerHTML = `
      <div class="v-top">
        <span class="v-tag"><span class="v-dot ${v.passed ? "ok" : "err"}"></span>${v.version}${v.version === S.state?.latest_version ? " (Aktuell)" : ""}</span>
        <span class="v-time">${timeShort}</span>
      </div>
      <div class="v-meta">
        <span>${formatReason(v.reason)}</span>
        <span class="mono-dim">${sz} mm</span>
      </div>
      <div class="v-actions">
        <button class="btn-xs act-view">${v.version === shownVer ? "Aktiv" : "Ansehen"}</button>
        <button class="btn-xs act-ghost ${v.version === S.ghostVersion ? "active" : ""}">👻 Ghost</button>
        ${v.version !== S.state?.latest_version ? `<button class="btn-xs act-restore">↶ Rollback</button>` : ""}
      </div>
    `;

    card.querySelector(".act-view").addEventListener("click", () => {
      const targetVer = v.version === S.state?.latest_version ? null : v.version;
      selectProject(S.currentProject, targetVer, false);
    });

    card.querySelector(".act-ghost").addEventListener("click", async () => {
      S.ghostVersion = S.ghostVersion === v.version ? null : v.version;
      renderVersionList();
      await rebuildGhostVersionMesh();
    });

    const restoreBtn = card.querySelector(".act-restore");
    if (restoreBtn) {
      restoreBtn.addEventListener("click", () => restoreProjectVersion(v.version));
    }

    wrap.appendChild(card);
  }
}

function renderDiagnosticsList() {
  const wrap = document.getElementById("diagnostics-list");
  wrap.innerHTML = "";
  if (!S.state) {
    wrap.textContent = "3MF-Dateien ins Fenster ziehen oder ein Projekt auswählen.";
    return;
  }
  if (isImport()) {
    const data = S.state.import;
    wrap.innerHTML = `<div class="empty-hint">3MF-Vorschau · Druckbarkeit und Funktionsmasse sind ungeprüft.</div>` +
      (data.error ? `<div class="diag-item error">${escapeHtml(data.error)}</div>` : "") +
      data.warnings.map((w) => `<div class="diag-item warn">${escapeHtml(w)}</div>`).join("");
    return;
  }
  if (isAssembly()) {
    renderAssemblyDiagnostics(wrap);
    return;
  }

  const rep = S.state?.report || {};
  const diag = S.state?.diagnostics || {};
  const items = [];

  // 1. Spatial 3D diagnostic clusters (clickable camera fly-to!)
  for (const tw of diag.thin_walls || []) {
    items.push({
      severity: tw.severity || "error",
      title: tw.label || "Zu dünne Wand",
      sub: `Modell (${fmtVec(tw.center_model)}) mm · Klick fokussiert 3D-Stelle`,
      bbox_print: tw.bbox_print,
      bbox_model: tw.bbox_model,
    });
  }
  for (const fp of diag.floating_parts || []) {
    items.push({
      severity: "error",
      title: fp.label || "Schwebendes Teil ohne Bettauflage!",
      sub: `Modell (${fmtVec(fp.center_model)}) mm · Klick fokussiert`,
      bbox_print: fp.bbox_print,
      bbox_model: fp.bbox_model,
    });
  }
  for (const f of diag.features || S.state?.build?.features || []) {
    if (f.empty_cut) {
      const m6 = f.bbox_min && f.bbox_max ? [...f.bbox_min, ...f.bbox_max] : null;
      items.push({
        severity: "error",
        title: `Verfehlter Schnitt '${f.name}' (liegt im Leeren!)`,
        sub: `Geister-Zylinder rot eingeblendet bei (${fmtVec(f.center)}) mm`,
        bbox_print: f.bbox_print,
        bbox_model: m6,
      });
    }
  }
  for (const bb of diag.bad_bridges || []) {
    items.push({
      severity: "warn",
      title: bb.label || "Zu lange Brücke",
      sub: `Modell (${fmtVec(bb.center_model)}) mm · Klick fokussiert`,
      bbox_print: bb.bbox_print,
      bbox_model: bb.bbox_model,
    });
  }
  for (const oh of diag.overhangs || []) {
    items.push({
      severity: "warn",
      title: oh.label || "Kritischer Überhang (> 45°)",
      sub: `Modell (${fmtVec(oh.center_model)}) mm · Klick fokussiert`,
      bbox_print: oh.bbox_print,
      bbox_model: oh.bbox_model,
    });
  }

  // 2. General report errors/warnings
  for (const e of rep.errors || []) {
    items.push({
      severity: "error",
      title: e.rule,
      sub: `Ist: ${e.value} (Soll: ${e.limit})`,
    });
  }
  for (const w of rep.warnings || []) {
    items.push({
      severity: "warn",
      title: w.rule,
      sub: `${w.value}${w.limit ? " (Grenze: " + w.limit + ")" : ""}`,
    });
  }

  const pill = document.getElementById("badge-issues-count");
  if (items.length > 0) {
    pill.textContent = String(items.length);
    pill.classList.remove("hidden");
  } else {
    pill.classList.add("hidden");
  }

  if (!items.length) {
    wrap.innerHTML = `
      <div class="diag-item ok">
        <div>
          <div class="diag-title">✓ Alle Geometrie- &amp; Druckprüfungen bestanden</div>
          <div class="diag-sub">Keine dünnen Wände, Überhänge oder verfehlten Schnitte erkannt.</div>
        </div>
      </div>`;
    return;
  }

  for (const it of items) {
    const el = document.createElement("div");
    el.className = `diag-item ${it.severity}`;
    el.innerHTML = `
      <div>
        <div class="diag-title">${escapeHtml(it.title)}</div>
        <div class="diag-sub">${escapeHtml(it.sub)}</div>
      </div>
      ${it.bbox_print || it.bbox_model ? `<span class="btn-xs">⌖ Fokus</span>` : ""}
    `;
    if (it.bbox_print || it.bbox_model) {
      el.addEventListener("click", () => focusBox3D(it.bbox_print, it.bbox_model));
    }
    wrap.appendChild(el);
  }
}

function renderMeasureBox() {
  const box = document.getElementById("measure-box");
  if (!S.measurement) {
    box.classList.add("hidden");
    return;
  }
  box.classList.remove("hidden");
  const m = S.measurement;
  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <strong>📏 Distanz: ${m.dist.toFixed(2)} mm</strong>
      <button id="btn-clear-measure" class="btn-xs">✕</button>
    </div>
    <div>|ΔX| = ${m.delta[0].toFixed(2)} · |ΔY| = ${m.delta[1].toFixed(2)} · |ΔZ| = ${m.delta[2].toFixed(2)} mm</div>
  `;
  document.getElementById("btn-clear-measure").onclick = () => {
    S.measurement = null;
    rebuildAnnotationVisuals();
    renderMeasureBox();
  };
}

function renderAnnotationsList() {
  const wrap = document.getElementById("annotations-list");
  document.getElementById("ann-count").textContent = String(S.annotations.length);
  wrap.innerHTML = "";

  if (!S.annotations.length) {
    wrap.innerHTML = `<div class="empty-hint">Wähle oben <strong>📍 Pin</strong>, <strong>⬚ Bereichsbox</strong> oder <strong>➔ Von→Nach</strong> und klicke direkt auf die fehlerhafte Stelle am Bauteil.</div>`;
    return;
  }

  for (const ann of S.annotations) {
    const card = document.createElement("div");
    card.className = "ann-card";

    let typeTitle = "";
    let coordsHtml = "";
    let extraControls = "";

    if (ann.type === "pin") {
      typeTitle = `📍 #${ann.id} Pin${ann.feature ? " · " + escapeHtml(ann.feature) : ""}`;
      coordsHtml = `Modell XYZ: (${fmtVec(ann.model_xyz)}) mm · Normale: (${fmtVec(ann.model_normal)})`;
    } else if (ann.type === "move") {
      typeTitle = `➔ #${ann.id} Verschiebung${ann.feature ? " · " + escapeHtml(ann.feature) : ""}`;
      coordsHtml = `Von (${fmtVec(ann.from_model_xyz)}) → Nach (${fmtVec(ann.to_model_xyz)})<br/><strong>Δ = [${fmtVec(ann.delta_model_mm)}] mm</strong>`;
    } else if (ann.type === "box") {
      typeTitle = `⬚ #${ann.id} Bereichsbox`;
      const featsTxt = ann.features_inside?.length ? `<br/>Features darin: ${escapeHtml(ann.features_inside.join(", "))}` : "";
      coordsHtml = `Min (${fmtVec(ann.bbox_model.slice(0, 3))}) → Max (${fmtVec(ann.bbox_model.slice(3, 6))}) mm${featsTxt}`;
      extraControls = `
        <div class="ann-box-z">
          <span>Höhe Z (mm):</span>
          <input type="number" step="0.5" class="zmin-in" value="${ann.bbox_model[2]}" />
          <span>bis</span>
          <input type="number" step="0.5" class="zmax-in" value="${ann.bbox_model[5]}" />
        </div>
      `;
    }

    card.innerHTML = `
      <div class="ann-header">
        <span>${typeTitle}</span>
        <div style="display:flex;gap:4px;">
          <button class="btn-xs ann-focus" title="Kamera fokussieren">⌖</button>
          <button class="btn-xs btn-danger ann-del" title="Markierung entfernen">✕</button>
        </div>
      </div>
      <div class="ann-coords">${coordsHtml}</div>
      ${extraControls}
      <input type="text" class="ann-note-input" placeholder="Anweisung für Agent (z. B. Wand 90° drehen / Loch 6 mm nach +X) …" value="${escapeAttr(ann.note || "")}" />
    `;

    card.querySelector(".ann-note-input").addEventListener("input", (e) => {
      ann.note = e.target.value;
    });

    card.querySelector(".ann-del").addEventListener("click", () => {
      S.annotations = S.annotations.filter((x) => x.id !== ann.id);
      rebuildAnnotationVisuals();
      renderAnnotationsList();
    });

    card.querySelector(".ann-focus").addEventListener("click", () => {
      if (ann.type === "pin") {
        const m = ann.model_xyz;
        focusBox3D(null, [m[0] - 8, m[1] - 8, m[2] - 8, m[0] + 8, m[1] + 8, m[2] + 8]);
      } else if (ann.type === "move") {
        const a = ann.from_model_xyz;
        const b = ann.to_model_xyz;
        focusBox3D(null, [
          Math.min(a[0], b[0]) - 5, Math.min(a[1], b[1]) - 5, Math.min(a[2], b[2]) - 5,
          Math.max(a[0], b[0]) + 5, Math.max(a[1], b[1]) + 5, Math.max(a[2], b[2]) + 5,
        ]);
      } else if (ann.type === "box") {
        focusBox3D(ann.bbox_print, ann.bbox_model);
      }
    });

    if (ann.type === "box") {
      const zMinIn = card.querySelector(".zmin-in");
      const zMaxIn = card.querySelector(".zmax-in");
      const updateBoxZ = () => {
        ann.bbox_model[2] = parseFloat(zMinIn.value) || 0;
        ann.bbox_model[5] = parseFloat(zMaxIn.value) || 10;
        ann.bbox_print = round3(bboxCornersTransform(ann.bbox_model, modelToPrint));
        ann.features_inside = detectFeaturesInsideModelBox(ann.bbox_model);
        rebuildAnnotationVisuals();
      };
      zMinIn.addEventListener("change", updateBoxZ);
      zMaxIn.addEventListener("change", updateBoxZ);
    }

    wrap.appendChild(card);
  }
}

function focusNewestNoteInput() {
  const inputs = document.querySelectorAll(".ann-note-input");
  if (inputs.length > 0) inputs[inputs.length - 1].focus();
}

function renderParamsForm() {
  const orientSel = document.getElementById("orientation-select");
  orientSel.innerHTML = "";
  const curOrient = S.state?.print_info?.orientation || "wie modelliert";
  for (const o of S.state?.orientations || ["wie modelliert"]) {
    const opt = document.createElement("option");
    opt.value = o;
    opt.textContent = o;
    if (o === curOrient) opt.selected = true;
    orientSel.appendChild(opt);
  }

  const form = document.getElementById("params-form");
  form.innerHTML = "";
  if (isImport()) {
    form.innerHTML = `<div class="empty-hint">Importierte 3MF-Modelle haben keine bearbeitbaren CAD-Parameter.</div>`;
    return;
  }
  const params = S.state?.params || [];
  if (!params.length) {
    form.innerHTML = `<div class="empty-hint">Keine numerischen Top-Level-Parameter vor <code>part</code>/<code>bodies</code> gefunden.</div>`;
    return;
  }

  for (const p of params) {
    const row = document.createElement("div");
    row.className = "param-row";
    row.innerHTML = `
      <div>
        <div class="param-name">${escapeHtml(p.name)}</div>
        ${p.comment ? `<div class="param-comment">${escapeHtml(p.comment)}</div>` : ""}
      </div>
      <input type="number" step="${p.is_int ? "1" : "0.1"}" class="param-input" data-param="${escapeAttr(p.name)}" data-orig="${p.value}" value="${p.value}" />
    `;
    const inp = row.querySelector(".param-input");
    inp.addEventListener("input", () => {
      inp.classList.toggle("changed", Number(inp.value) !== Number(p.value));
    });
    form.appendChild(row);
  }
}

function renderChecksAndSpec() {
  if (isImport()) {
    document.getElementById("checks-table-wrap").textContent = "Vorschau: keine Prüfung der Druckbarkeit oder Funktionsmasse.";
    document.getElementById("spec-details").textContent = `Quelle: ${S.state.import.source || "–"}. Material und Druckeinstellungen wurden nicht übernommen.`;
    document.getElementById("render-preview-wrap").innerHTML = "";
    document.getElementById("model-code-view").textContent = "Kein CAD-Quellcode vorhanden.";
    return;
  }
  if (isAssembly()) {
    renderAssemblyChecksAndSpec();
    return;
  }
  const checksWrap = document.getElementById("checks-table-wrap");
  const checks = S.state?.report?.checks || [];
  if (!checks.length) {
    checksWrap.innerHTML = `<div class="empty-hint">Keine Prüfdaten vorhanden.</div>`;
  } else {
    const rows = checks
      .map(
        (c) => `
      <tr>
        <td>${c.ok ? `<span style="color:var(--green)">✓</span>` : `${c.severity === "info" ? `<span style="color:var(--muted, #888)">ℹ</span>` : `<span style="color:${c.severity === "warn" ? "var(--amber)" : "var(--red)"}">✗</span>`}`} ${escapeHtml(c.rule)}</td>
        <td class="mono">${escapeHtml(String(c.value ?? ""))}</td>
        <td class="mono">${escapeHtml(String(c.limit ?? ""))}</td>
      </tr>`
      )
      .join("");
    checksWrap.innerHTML = `
      <table class="checks-table">
        <thead><tr><th>Prüfregel / Maß</th><th>Ist</th><th>Soll / Grenze</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  const spec = S.state?.spec || {};
  const specWrap = document.getElementById("spec-details");
  const masseList = (spec.masse || []).map((m) => `<li>${escapeHtml(typeof m === "string" ? m : JSON.stringify(m))}</li>`).join("");
  const annList = (spec.annahmen || []).map((a) => `<li>${escapeHtml(String(a))}</li>`).join("");

  specWrap.innerHTML = `
    <div><strong>Beschreibung:</strong> ${escapeHtml(spec.beschreibung || "–")}</div>
    <div><strong>Material / Qualität:</strong> ${escapeHtml((spec.material || "pla").toUpperCase())} · ${escapeHtml(spec.qualitaet || "standard")}</div>
    <div><strong>Soll-BBox:</strong> <span class="mono-dim">${spec.bbox ? spec.bbox.join(" × ") + " mm" : "nicht gesetzt"}</span></div>
    ${masseList ? `<div><strong>Maße:</strong><ul class="spec-list">${masseList}</ul></div>` : ""}
    ${annList ? `<div><strong>Annahmen:</strong><ul class="spec-list">${annList}</ul></div>` : ""}
  `;

  const previewWrap = document.getElementById("render-preview-wrap");
  previewWrap.innerHTML = S.state?.render_png_url
    ? `<img src="${S.state.render_png_url}" alt="4-Ansichten-Render" />`
    : "";

  document.getElementById("model-code-view").textContent = S.state?.model_code || "";
}

function renderSceneLegend() {
  const leg = document.getElementById("scene-legend");
  const items = [];
  if (isAssembly()) {
    const aj = S.state.assembly;
    for (const t of aj?.teile || []) {
      if (S.asm.hidden.has(`teil:${t.name}`)) continue;
      items.push(`<span class="legend-item"><span class="legend-swatch" style="background:${t.farbe}"></span>${escapeHtml(t.name)}${t.anzahl > 1 ? ` ×${t.anzahl}` : ""}</span>`);
    }
    if ((asmPose()?.geister || []).length) {
      items.push(`<span class="legend-item"><span class="legend-swatch" style="background:#9aa5b1;opacity:.5"></span>Geist (nicht gedruckt)</span>`);
    }
    if (S.overlays.errors && (asmPose()?.ueberschneidungen || []).length) {
      items.push(`<span class="legend-item"><span class="legend-swatch" style="background:#ff2e4c"></span>Überschneidung</span>`);
    }
    leg.innerHTML = items.join("");
    return;
  }
  for (const m of S.state?.meshes || []) {
    if (isImport() && S.imported.hidden.has(m.id)) continue;
    items.push(`<span class="legend-item"><span class="legend-swatch" style="background:${m.color}"></span>${escapeHtml(m.name === "_teil" ? "Bauteil" : m.name)}</span>`);
  }
  if (S.overlays.errors && !isImport() && S.state) {
    items.push(`<span class="legend-item"><span class="legend-swatch" style="background:#ef4444"></span>Fehler / Dünnwand</span>`);
    items.push(`<span class="legend-item"><span class="legend-swatch" style="background:#f59e0b"></span>Überhang / Brücke</span>`);
  }
  leg.innerHTML = items.join("");
}

// ============================================================================
// Backend Actions: Save Feedback, Apply Params, Rebuild, Rollback, Diff Modal
// ============================================================================
function setBusy(busy, msg = "CAD-Modell wird neu gebaut & geprüft …") {
  const el = document.getElementById("busy-overlay");
  document.getElementById("busy-text").textContent = msg;
  el.classList.toggle("hidden", !busy);
}

async function saveFeedbackForAgent() {
  if (isImport()) return;
  if (!S.currentProject) return;
  const generalNote = document.getElementById("general-note").value.trim();
  if (!S.annotations.length && !generalNote) {
    showToast("Bitte setze mindestens eine 3D-Markierung oder schreibe einen Hinweis.");
    return;
  }

  const dataUrl = captureAnnotatedScreenshotDataUrl();
  const payload = {
    based_on_version: S.state?.version || S.state?.latest_version || null,
    view_mode: isAssembly() ? "baugruppe" : S.viewMode,
    pose: isAssembly() ? asmPose()?.name || null : undefined,
    gelenkwerte: isAssembly() && S.kin.moved ? S.kin.lastCoords || null : undefined,
    general_note: generalNote,
    screenshot_data_url: dataUrl,
    annotations: S.annotations,
  };

  const res = await apiFetch(`/api/projects/${encodeURIComponent(S.currentProject)}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  S.state.feedback = res.feedback;
  renderProjectSummary();
  await loadProjectsList(S.currentProject);

  const promptText = `Bitte setze mein markiertes 3D-Feedback in Projekt ${S.currentProject} um (get_ui_feedback aufrufen und Screenshot ansehen).`;
  try {
    await navigator.clipboard.writeText(promptText);
    showToast("✓ Gespeichert & Prompt in Zwischenablage kopiert! Einfach in Antigravity / Claude einfügen.");
  } catch {
    showToast(`✓ Feedback gespeichert! Sag im Chat: "${promptText}"`);
  }
}

async function applyParamsAndRebuild() {
  if (isImport()) return;
  if (!S.currentProject) return;
  const inputs = document.querySelectorAll("#params-form .param-input");
  const updates = {};
  inputs.forEach((inp) => {
    updates[inp.dataset.param] = Number(inp.value);
  });
  const orient = document.getElementById("orientation-select").value;

  setBusy(true, "Parameter werden in model.py aktualisiert & neu gebaut …");
  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(S.currentProject)}/params`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params: updates, orientation: orient }),
    });
    S.activeVersion = null;
    S.state = res.state;
    renderProjectSummary();
    renderVersionList();
    renderHistoricalBanner();
    renderDiagnosticsList();
    renderParamsForm();
    renderChecksAndSpec();
    await rebuildSceneGeometry(false);
    await loadProjectsList(S.currentProject);
    showToast(res.ok ? `✓ Neu gebaut (${res.run?.version || "aktuell"})` : `Build-Fehler: ${res.run?.error || "Prüfung fehlgeschlagen"}`);
  } catch (err) {
    showToast(`Fehler: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function rebuildCurrentProject() {
  if (!S.currentProject) return;
  if (isImport()) {
    try {
      await selectProject(S.currentProject, null, false);
    } catch (err) {
      showToast(`Fehler: ${err.message}`);
    }
    return;
  }
  const orient = document.getElementById("orientation-select")?.value || "wie modelliert";
  setBusy(true, "Projekt wird neu gebaut & auf 3D-Fehler geprüft …");
  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(S.currentProject)}/rebuild`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orientation: orient }),
    });
    S.activeVersion = null;
    S.state = res.state;
    renderProjectSummary();
    renderVersionList();
    renderHistoricalBanner();
    renderDiagnosticsList();
    renderParamsForm();
    renderChecksAndSpec();
    await rebuildSceneGeometry(false);
    showToast(`✓ Prüflauf abgeschlossen (${res.run?.version || "aktuell"})`);
  } catch (err) {
    showToast(`Fehler: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function restoreProjectVersion(version) {
  if (!S.currentProject || !version) return;
  setBusy(true, `Stelle Version ${version} wieder her …`);
  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(S.currentProject)}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    });
    S.activeVersion = null;
    S.state = res.state;
    renderProjectSummary();
    renderVersionList();
    renderHistoricalBanner();
    renderDiagnosticsList();
    renderParamsForm();
    renderChecksAndSpec();
    await rebuildSceneGeometry(false);
    showToast(`✓ Auf Stand von ${version} zurückgesetzt!`);
  } catch (err) {
    showToast(`Rollback fehlgeschlagen: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function openDiffModal() {
  if (!S.currentProject) return;
  const versions = S.state?.versions || [];
  if (versions.length < 1) {
    showToast("Mindestens eine gespeicherte Version erforderlich.");
    return;
  }
  const oldSel = document.getElementById("diff-old-select");
  const newSel = document.getElementById("diff-new-select");
  oldSel.innerHTML = "";
  newSel.innerHTML = "";

  for (const v of versions) {
    oldSel.add(new Option(`${v.version} (${formatReason(v.reason)})`, v.version));
    newSel.add(new Option(`${v.version} (${formatReason(v.reason)})`, v.version));
  }
  newSel.add(new Option("Arbeitsstand (model.py)", "current"));

  oldSel.value = versions.length >= 2 ? versions[versions.length - 2].version : versions[0].version;
  newSel.value = versions[versions.length - 1].version;

  document.getElementById("diff-modal").classList.remove("hidden");
  await refreshDiffContent();
}

async function refreshDiffContent() {
  const vOld = document.getElementById("diff-old-select").value;
  const vNew = document.getElementById("diff-new-select").value;
  const res = await apiFetch(
    `/api/projects/${encodeURIComponent(S.currentProject)}/diff?old=${encodeURIComponent(vOld)}&new=${encodeURIComponent(vNew)}`
  );
  const viewer = document.getElementById("diff-content");
  if (!res.diff) {
    viewer.textContent = "Keine Code-Unterschiede zwischen diesen Ständen.";
  } else {
    viewer.innerHTML = res.diff
      .split("\n")
      .map((line) => {
        const esc = escapeHtml(line);
        if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
          return `<span class="diff-hdr">${esc}</span>`;
        }
        if (line.startsWith("+")) return `<span class="diff-add">${esc}</span>`;
        if (line.startsWith("-")) return `<span class="diff-del">${esc}</span>`;
        return esc + "\n";
      })
      .join("");
  }

  const deltaEl = document.getElementById("diff-meta-delta");
  if (res.meta_old && res.meta_new) {
    const dv = (res.meta_new.volume_mm3 - res.meta_old.volume_mm3).toFixed(1);
    deltaEl.textContent = `ΔVolumen: ${dv > 0 ? "+" : ""}${dv} mm³`;
  } else {
    deltaEl.textContent = "";
  }
}

// ============================================================================
// Live SSE Updates & Read-Only Moonraker/CFS Polling
// ============================================================================
function connectSSE() {
  const dot = document.getElementById("sse-indicator");
  const es = new EventSource("/api/events");
  es.onopen = () => dot.classList.remove("disconnected");
  es.onerror = () => dot.classList.add("disconnected");
  es.addEventListener("project_updated", async (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      await loadProjectsList();
      if (S.currentProject && msg.projects?.includes(S.currentProject) && !S.activeVersion) {
        await selectProject(S.currentProject, null, false);
        showToast(`↻ Live-Update: ${S.currentProject} neu geladen`);
      }
    } catch (e) {
      console.warn("SSE Update Fehler:", e);
    }
  });
}

async function pollPrinterStatus() {
  try {
    const p = await apiFetch("/api/printer");
    const dot = document.getElementById("printer-conn-dot");
    const stText = document.getElementById("printer-state-text");
    const tmpText = document.getElementById("printer-temps-text");
    const cfsBar = document.getElementById("cfs-slots-bar");

    if (!p.connected || !p.status) {
      S.cfs = [];
      dot.className = "conn-dot offline";
      stText.textContent = "Creality Hi offline / Ruhemodus";
      tmpText.textContent = "";
      cfsBar.innerHTML = "";
      return;
    }

    dot.className = "conn-dot online";
    const s = p.status;
    const pct = s.progress_pct != null ? ` (${Number(s.progress_pct).toFixed(0)} %)` : "";
    stText.textContent = `Creality Hi: ${s.state || "bereit"}${pct}`;
    const tC = (v, t) => (v != null ? `${Number(v).toFixed(0)}${t ? `/${Number(t).toFixed(0)}` : ""}°C` : "–");
    const parts = [`Düse ${tC(s.nozzle_c, s.nozzle_target_c)}`, `Bett ${tC(s.bed_c, s.bed_target_c)}`];
    if (s.layer != null && s.layers_total) parts.push(`Schicht ${s.layer}/${s.layers_total}`);
    if (s.remaining_min != null && s.state === "printing") parts.push(`noch ${fmtMin(s.remaining_min)}`);
    tmpText.textContent = parts.join(" · ");
    S.cfs = p.cfs || [];
    fillSlotSelect();

    cfsBar.innerHTML = (p.cfs || [])
      .map((sl) => {
        if (!sl.loaded) {
          return `<span class="cfs-chip"><span class="cfs-swatch" style="background:transparent"></span>${escapeHtml(sl.slot)} leer</span>`;
        }
        const hex = sl.color?.startsWith("#") ? sl.color : `#${sl.color || "888888"}`;
        return `<span class="cfs-chip" title="${escapeAttr(sl.color_name || "")}"><span class="cfs-swatch" style="background:${hex}"></span>${escapeHtml(sl.slot)} ${escapeHtml(sl.type || "")} ${sl.remain_pct ?? ""}%</span>`;
      })
      .join("");
  } catch {
    // Ignore transient network errors
  }
}


// ============================================================================
// Kosten & Drucker-Details
// ============================================================================
function fmtMin(m) {
  const v = Math.round(Number(m) || 0);
  return v >= 60 ? `${Math.floor(v / 60)} h ${v % 60} min` : `${v} min`;
}

function chf(v, cur = "CHF") {
  return `${cur} ${Number(v || 0).toFixed(2)}`;
}

async function renderCosts() {
  const box = document.getElementById("cost-box");
  if (!box || !S.currentProject) return;
  if (isImport()) {
    box.textContent = "3MF-Vorschau – noch keine Druckkosten vorhanden.";
    return;
  }
  if (isAssembly()) {
    box.innerHTML = `<span class="status-muted">Baugruppen werden nicht gedruckt – Kosten je Teil-Projekt ansehen.</span>`;
    return;
  }
  const proj = S.currentProject;
  let k;
  try {
    k = await apiFetch(`/api/projects/${encodeURIComponent(proj)}/kosten`);
  } catch (e) {
    box.innerHTML = `<span class="status-muted">Kosten nicht berechenbar: ${escapeHtml(e.message)}</span>`;
    return;
  }
  if (proj !== S.currentProject) return;
  if (!k.ok) {
    box.innerHTML = `<span class="status-muted">${k.not_sliced ? "Noch nicht gesliced – nach dem Slicen erscheinen hier die Kosten." : escapeHtml(k.error || "")}</span>`;
    return;
  }
  const cur = k.waehrung || "CHF";
  const rows = (k.posten || [])
    .map((p) => {
      const sw = p.farbe ? `<span class="cost-swatch" style="background:${escapeAttr(p.farbe)}"></span>` : "";
      const src = p.quelle && !p.quelle.startsWith("standard")
        ? ` · ${escapeHtml(p.quelle)}${p.stand ? " " + escapeHtml(p.stand) : ""}`
        : " · Standardpreis";
      return `<tr><td>${sw}${escapeHtml(p.werkzeug)}${p.spule ? " (" + escapeHtml(p.spule) + ")" : ""} ${escapeHtml(p.material || "")} · ${p.gramm} g<br><span class="status-muted">${escapeHtml(p.rolle)} ${chf(p.rollenpreis, cur)}/${Math.round(p.rollengewicht_g)} g${src}</span></td><td class="num">${chf(p.chf, cur)}</td></tr>`;
    })
    .join("");
  const a = k.annahmen || {};
  box.innerHTML = `
    <div><span class="cost-total">${chf(k.total, cur)}</span> <span class="status-muted">· ${k.gramm} g · ${escapeHtml(k.zeit || fmtMin(k.stunden * 60))}</span></div>
    <table class="cost-table">
      ${rows}
      <tr><td>Strom (${a.leistung_w} W × ${a.strom_chf_kwh} ${cur}/kWh)</td><td class="num">${chf(k.strom, cur)}</td></tr>
      <tr><td>Verschleiss (${a.verschleiss_chf_h} ${cur}/h)</td><td class="num">${chf(k.verschleiss, cur)}</td></tr>
      ${k.fehldruck_zuschlag ? `<tr><td>Fehldruck-Zuschlag (${a.fehldruck_pct} %)</td><td class="num">${chf(k.fehldruck_zuschlag, cur)}</td></tr>` : ""}
    </table>
    <span class="status-muted">G-Code: ${escapeHtml(k.gcode || "")} · Annahmen in config/printer.yaml → kosten</span>`;
}

async function loadPrices() {
  const wrap = document.getElementById("price-list");
  if (!wrap) return;
  try {
    renderPrices(await apiFetch("/api/preise"));
  } catch (e) {
    wrap.innerHTML = `<span class="status-muted">${escapeHtml(e.message)}</span>`;
  }
}

function renderPrices(st) {
  const wrap = document.getElementById("price-list");
  const cur = st.waehrung || "CHF";
  const items = (st.rollen || []).map((r) => {
    const filt = ["material", "slot", "farbe", "code", "profil"].filter((f) => r[f]).map((f) => `${f}: ${r[f]}`).join(" · ");
    const src = r.url
      ? `<a href="${escapeAttr(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.quelle || "Link")}</a>`
      : escapeHtml(r.quelle || "manuell");
    return `<div class="price-item"><div class="pi-main"><span class="pi-name" title="${escapeAttr(r.titel || r.name)}">${escapeHtml(r.name)}</span>
      <span class="status-muted">${chf(r.preis, cur)} / ${Math.round(r.gewicht_g || 1000)} g · ${src}${r.stand ? " · " + escapeHtml(r.stand) : ""}${filt ? "<br>" + escapeHtml(filt) : ""}</span></div>
      <span><button class="btn-xs" data-price-edit="${escapeAttr(r.name)}" title="Bearbeiten">✎</button>
      <button class="btn-xs btn-danger" data-price-del="${escapeAttr(r.name)}" title="Löschen">✕</button></span></div>`;
  });
  const std = Object.entries(st.standard || {})
    .map(([m, v]) => `${m.toUpperCase()} ${chf(v.preis, cur)}/${Math.round(v.gewicht_g || 1000)} g`)
    .join(" · ");
  wrap.innerHTML = items.join("") + `<span class="status-muted">Standard (ohne passende Rolle): ${escapeHtml(std)}</span>`;
  S.prices = st.rollen || [];
}

function fillSlotSelect() {
  const sel = document.getElementById("pf-slot");
  if (!sel) return;
  const opts = (S.cfs || []).filter((x) => x.loaded);
  const sig = opts.map((x) => x.slot + x.color + x.code).join("|");
  if (sel.dataset.sig === sig) return;
  const keep = sel.value;
  sel.dataset.sig = sig;
  sel.innerHTML = `<option value="">Spule: alle</option>` +
    opts.map((x) => `<option value="${escapeAttr(x.slot)}">${escapeHtml(x.slot)} ${escapeHtml(x.type || "")} ${escapeHtml(x.color_name || "")}</option>`).join("");
  sel.value = keep;
}

async function savePrice() {
  const v = (id) => document.getElementById(id).value.trim();
  const slot = v("pf-slot");
  const body = {
    action: "set",
    name: v("pf-name"),
    preis: v("pf-preis").replace(",", ".") || null,
    gewicht_g: v("pf-gewicht").replace(",", ".") || null,
    url: v("pf-url") || null,
    material: v("pf-material") || null,
  };
  if (slot) {
    // Spule gewählt = deren Merkmale übernehmen (der Platz wechselt, Material/Farbe/RFID-Code nicht)
    const sp = (S.cfs || []).find((x) => x.slot === slot);
    if (sp) Object.assign(body, { material: (sp.type || "").toLowerCase() || body.material, farbe: sp.color, code: sp.code });
  }
  if (!body.name) {
    showToast("Bitte einen Namen für die Rolle eingeben");
    return;
  }
  const btn = document.getElementById("btn-price-save");
  btn.disabled = true;
  try {
    const res = await fetch("/api/preise", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    renderPrices(data.preise);
    showToast(data.hinweis ? `Gespeichert – Hinweis: ${data.hinweis}` : `Gespeichert: ${data.rolle.name} ${chf(data.rolle.preis)}`);
    ["pf-name", "pf-preis", "pf-gewicht", "pf-url"].forEach((id) => (document.getElementById(id).value = ""));
    renderCosts();
  } catch (e) {
    showToast(`Preis nicht gespeichert: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

async function priceAction(action, name) {
  try {
    const data = await apiFetch("/api/preise", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, name }),
    });
    renderPrices(data.preise);
    if (action === "refresh") {
      const errs = (data.rollen || []).filter((r) => r.fehler);
      showToast(errs.length ? `${errs.length} Abruf(e) blockiert – bisherige Preise bleiben` : "Preise aktualisiert");
    }
    renderCosts();
  } catch (e) {
    showToast(`Fehler: ${e.message}`);
  }
}

function editPrice(name) {
  const r = (S.prices || []).find((x) => x.name === name);
  if (!r) return;
  document.getElementById("price-form-wrap").open = true;
  document.getElementById("pf-name").value = r.name;
  document.getElementById("pf-preis").value = r.preis ?? "";
  document.getElementById("pf-gewicht").value = r.gewicht_g ?? "";
  document.getElementById("pf-url").value = r.url || "";
  document.getElementById("pf-material").value = r.material || "";
}

async function loadPrinterDetails(force = false) {
  const box = document.getElementById("printer-details");
  if (!box) return;
  if (!force && !document.getElementById("tab-kosten").classList.contains("active")) return;
  let d;
  try {
    d = await apiFetch("/api/printer/details");
  } catch (e) {
    box.innerHTML = `<span class="status-muted">${escapeHtml(e.message)}</span>`;
    return;
  }
  if (!d.connected) {
    box.innerHTML = `<span class="status-muted" title="${escapeAttr(d.error || "")}">Drucker nicht erreichbar (Ruhemodus oder falsche IP in config/printer.yaml).</span>`;
    return;
  }
  const kv = [];
  const add = (k, v) => v != null && v !== "" && kv.push(`<span>${escapeHtml(k)}</span><span>${v}</span>`);
  (d.sensors || []).forEach((x) =>
    add(x.name === "mcu_temp" ? "Platine" : x.name,
      `${x.c} °C${x.target ? ` → ${x.target} °C` : ""}${x.humidity_pct != null ? ` · ${x.humidity_pct} % Feuchte` : ""}`));
  (d.fans || []).forEach((x) => add(`Lüfter ${x.name}`, `${x.pct} %${x.rpm ? ` · ${x.rpm} U/min` : ""}`));
  const fs = (d.filament_sensors || []).filter((x) => x.enabled !== false);
  if (fs.length) add("Filamentsensoren", `${fs.length} aktiv · ${fs.filter((x) => x.filament).length} melden Filament`);
  const led = (d.pins || []).find((x) => x.name.toLowerCase() === "led");
  if (led) add("Licht", led.value ? "an" : "aus");
  if (d.live) add("Live", `${d.live.velocity_mm_s} mm/s · ${d.live.flow_mm3_s} mm³/s`);
  if (d.toolhead) add("Achsen", `${escapeHtml(d.toolhead.homed || "nicht referenziert")} · ${(d.toolhead.position || []).join(" / ")}`);
  if (d.objects_in_print?.length) add("Objekte", `${d.objects_in_print.length} (${(d.excluded_objects || []).length} ausgelassen)`);
  if (d.system) add("System", `${d.system.cpu_temp_c != null ? `CPU ${d.system.cpu_temp_c} °C · ` : ""}Last ${d.system.load ?? "–"}`);
  if (d.history_totals) {
    const h = d.history_totals;
    add("Gesamt", `${h.jobs} Drucke · ${h.print_h} h · ${h.filament_m} m Filament`);
  }
  if (d.machine) add("Firmware", escapeHtml(`Moonraker ${d.machine.moonraker || "?"} · Klipper ${d.machine.klipper_state || "?"}`));
  const last = (d.history_last || [])
    .map((j) => `<tr><td>${escapeHtml((j.file || "").replace(/\.gcode$/, "").replace(/^stp_\d{8}-\d{4}_/, ""))}</td><td class="num" title="${escapeAttr(j.status || "")}">${{ completed: "✓", cancelled: "✕", error: "⚠" }[j.status] || escapeHtml(j.status || "")} ${fmtMin(j.print_min)}</td></tr>`)
    .join("");
  const wartung = (d.wartung || [])
    .slice(0, 6)
    .map((w) => {
      const col = w.faellig || w.pct >= 100 ? "var(--red)" : w.pct >= 80 ? "var(--amber)" : "var(--green)";
      return `<div class="wt-row" title="${escapeAttr(w.gruppe)}"><span>${escapeHtml(w.name)}${w.faellig ? " – fällig" : ""}</span>
        <span class="wt-bar"><span style="width:${Math.min(100, w.pct)}%;background:${col}"></span></span><span class="num">${w.pct} %</span></div>`;
    })
    .join("");
  const cam = (d.webcams || [])[0];
  const camUrl = cam && (cam.snapshot || cam.stream);
  const camHtml = camUrl
    ? `<img class="cam-img" alt="Kamera ${escapeAttr(cam.name || "")}" src="${escapeAttr(camUrl + (camUrl.includes("?") ? "&" : "?") + "t=" + Date.now())}">`
    : "";
  box.innerHTML = `${camHtml}<div class="kv-grid">${kv.join("") || "<span>–</span><span>keine Zusatzdaten</span>"}</div>
    ${wartung ? `<div class="section-header" style="margin:6px 0 0">Wartung (Creality-Zähler)</div><div class="wt-list">${wartung}</div>` : ""}
    ${last ? `<div class="section-header" style="margin:6px 0 0">Letzte Drucke</div><table class="cost-table">${last}</table>` : ""}`;
}

// ============================================================================
// UI Event Bindings & Utilities
// ============================================================================
function switchRightTab(tabId) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
  document.querySelectorAll(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `tab-${tabId}`));
  if (tabId === "kosten") loadPrinterDetails(true);
}

function showToast(msg) {
  const wrap = document.getElementById("toast-container");
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => t.remove(), 4200);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

function bindEvents() {
  // Kosten & Drucker
  document.getElementById("btn-cost-reload").addEventListener("click", () => renderCosts());
  document.getElementById("btn-price-save").addEventListener("click", () => savePrice());
  document.getElementById("btn-prices-refresh").addEventListener("click", () => priceAction("refresh"));
  document.getElementById("btn-details-reload").addEventListener("click", () => loadPrinterDetails(true));
  document.getElementById("price-list").addEventListener("click", (e) => {
    const del = e.target.closest("[data-price-del]");
    if (del && confirm(`Rolle „${del.dataset.priceDel}“ löschen?`)) priceAction("delete", del.dataset.priceDel);
    const ed = e.target.closest("[data-price-edit]");
    if (ed) editPrice(ed.dataset.priceEdit);
  });
  // Project select
  document.getElementById("project-select").addEventListener("change", (e) => {
    selectProject(e.target.value, null, true);
  });
  document.getElementById("btn-refresh-projects").addEventListener("click", () => loadProjectsList());

  // Tool buttons
  document.querySelectorAll(".tool-btn").forEach((btn) => {
    btn.addEventListener("click", () => setTool(btn.dataset.tool));
  });

  // Keyboard shortcuts (1..5, Esc)
  window.addEventListener("keydown", (ev) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    if (ev.key === "1") setTool("orbit");
    else if (ev.key === "2") setTool("pin");
    else if (ev.key === "3") setTool("box");
    else if (ev.key === "4") setTool("move");
    else if (ev.key === "5") setTool("measure");
    else if (ev.key === "6" && kinData()) setTool("bewegen");
    else if (ev.key === "Escape") {
      S.pendingPoint = null;
      clearGroup(previewGroup);
      setTool("orbit");
    }
  });

  // Coordinate space switch
  const setSpace = async (mode) => {
    if (S.viewMode === mode) return;
    S.viewMode = mode;
    document.getElementById("btn-space-print").classList.toggle("active", mode === "print");
    document.getElementById("btn-space-model").classList.toggle("active", mode === "model");
    document.getElementById("cur-space-label").textContent = mode === "print" ? "(Druckbett mm)" : "(Modell mm)";
    await rebuildSceneGeometry(true);
  };
  document.getElementById("btn-space-print").addEventListener("click", () => setSpace("print"));
  document.getElementById("btn-space-model").addEventListener("click", () => setSpace("model"));

  // Overlays
  document.getElementById("btn-kin-check").addEventListener("click", () => kinCheckExact());
  document.getElementById("btn-kin-reset").addEventListener("click", () => kinResetButton());

  // Baugruppe: Pose, Explosion, alle/keine Teile
  document.getElementById("pose-select").addEventListener("change", (e) => setAssemblyPose(Number(e.target.value)));
  document.getElementById("explode-slider").addEventListener("input", (e) => {
    S.asm.explode = Number(e.target.value);
    document.getElementById("explode-value").textContent = `${S.asm.explode} %`;
    applyExplosion();
  });
  document.getElementById("btn-asm-all").addEventListener("click", () => {
    S.asm.hidden = new Set();
    applyAssemblyVisibility();
    renderAssemblyPanel();
    renderSceneLegend();
  });
  document.getElementById("btn-asm-none").addEventListener("click", () => {
    for (const t of S.state?.assembly?.teile || []) S.asm.hidden.add(`teil:${t.name}`);
    applyAssemblyVisibility();
    renderAssemblyPanel();
    renderSceneLegend();
  });

  document.getElementById("chk-overlay-errors").addEventListener("change", (e) => {
    S.overlays.errors = e.target.checked;
    errorGroup.visible = S.overlays.errors;
    ghostCutGroup.visible = S.overlays.errors;
    renderSceneLegend();
  });
  document.getElementById("chk-overlay-features").addEventListener("change", (e) => {
    S.overlays.features = e.target.checked;
    featureGroup.visible = S.overlays.features;
    syncHtmlLabels();
  });
  document.getElementById("chk-overlay-edges").addEventListener("change", (e) => {
    S.overlays.edges = e.target.checked;
    edgesGroup.visible = S.overlays.edges;
  });

  // Section clipping
  document.getElementById("clip-axis").addEventListener("change", (e) => {
    S.clip.axis = e.target.value;
    updateClipPlane();
  });
  document.getElementById("clip-slider").addEventListener("input", (e) => {
    S.clip.percent = Number(e.target.value);
    updateClipPlane();
  });
  document.getElementById("clip-flip").addEventListener("change", (e) => {
    S.clip.flip = e.target.checked;
    updateClipPlane();
  });

  // Camera bar
  document.querySelectorAll("#cam-bar button").forEach((b) => {
    b.addEventListener("click", () => setCameraPreset(b.dataset.cam));
  });

  // Tabs
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.addEventListener("click", () => switchRightTab(b.dataset.tab));
  });

  // Historical & Ghost banners
  document.getElementById("btn-clear-ghost").addEventListener("click", async () => {
    S.ghostVersion = null;
    renderVersionList();
    await rebuildGhostVersionMesh();
  });
  document.getElementById("btn-banner-live").addEventListener("click", () => {
    selectProject(S.currentProject, null, false);
  });
  document.getElementById("btn-banner-restore").addEventListener("click", () => {
    if (S.state?.version) restoreProjectVersion(S.state.version);
  });

  // Right sidebar actions
  document.getElementById("btn-rebuild-now").addEventListener("click", rebuildCurrentProject);
  document.getElementById("btn-clear-annotations").addEventListener("click", () => {
    S.annotations = [];
    S.measurement = null;
    rebuildAnnotationVisuals();
    renderAnnotationsList();
    renderMeasureBox();
  });
  document.getElementById("btn-save-feedback").addEventListener("click", saveFeedbackForAgent);
  document.getElementById("btn-delete-saved-fb").addEventListener("click", async () => {
    if (!S.currentProject) return;
    await apiFetch(`/api/projects/${encodeURIComponent(S.currentProject)}/feedback`, { method: "DELETE" });
    S.state.feedback = null;
    S.annotations = [];
    document.getElementById("general-note").value = "";
    renderProjectSummary();
    rebuildAnnotationVisuals();
    renderAnnotationsList();
    await loadProjectsList(S.currentProject);
    showToast("Erledigtes Feedback gelöscht.");
  });

  document.getElementById("btn-reset-params").addEventListener("click", renderParamsForm);
  document.getElementById("btn-apply-params").addEventListener("click", applyParamsAndRebuild);

  // Code diff modal
  document.getElementById("btn-open-diff").addEventListener("click", openDiffModal);
  document.getElementById("btn-close-diff").addEventListener("click", () => {
    document.getElementById("diff-modal").classList.add("hidden");
  });
  document.getElementById("diff-old-select").addEventListener("change", refreshDiffContent);
  document.getElementById("diff-new-select").addEventListener("change", refreshDiffContent);
}

// ============================================================================
// Resize & Render Loop
// ============================================================================
// ============================================================================
// Baugruppen-Ansicht (Projekt mit assembly.py → out/assembly.json)
// Teile werden je Teil einmal geladen und je Pose nur über Matrizen platziert.
// ============================================================================
function isAssembly() {
  return S.state?.kind === "assembly";
}

function isImport() {
  return S.state?.kind === "import";
}

function enterOrLeaveImportMode() {
  const imported = isImport();
  document.getElementById("import-panel").classList.toggle("hidden", !imported);
  document.getElementById("annotations-section").classList.remove("hidden");
  document.querySelectorAll('#tab-params .pane-section').forEach((el) => el.classList.toggle("hidden", imported));
  document.querySelectorAll('.tab-btn[data-tab="params"]').forEach((el) => el.classList.toggle("hidden", imported));
  if (imported && document.getElementById("tab-params").classList.contains("active")) switchRightTab("diagnose");
  for (const tool of ["pin", "box", "move"]) {
    document.querySelector(`.tool-btn[data-tool="${tool}"]`).disabled = false;
  }
  const reload = document.getElementById("btn-rebuild-now");
  reload.textContent = imported ? "↻ Neu laden" : "↻ Neu prüfen";
  reload.title = imported ? "3MF-Datei erneut laden" : "Projekt neu bauen & prüfen";
  if (!imported) {
    S.imported.file = null;
    S.imported.plate = null;
    S.imported.key = null;
    if (S.imported.prevViewMode !== null) {
      // On switching to an assembly, restore the pre-import choice for later.
      if (isAssembly()) S.asm.prevViewMode = S.imported.prevViewMode;
      else S.viewMode = S.imported.prevViewMode;
      S.imported.prevViewMode = null;
    }
    document.getElementById("btn-space-print").classList.toggle("active", S.viewMode === "print");
    document.getElementById("btn-space-model").classList.toggle("active", S.viewMode === "model");
    return;
  }
  if (S.imported.prevViewMode === null) S.imported.prevViewMode = S.viewMode;
  S.viewMode = "model";
  S.ghostVersion = null;
  if (!["orbit", "pin", "box", "move", "measure"].includes(S.tool)) setTool("orbit");
  const data = S.state.import;
  const key = JSON.stringify([S.currentProject, data.source, data.plate, data.sha256]);
  if (key !== S.imported.key) {
    S.imported.hidden = new Set();
    S.measurement = null;
  }
  S.imported.key = key;
  S.imported.file = data.source;
  S.imported.plate = data.plate;
  const fillSelect = (id, options, value) => {
    const select = document.getElementById(id);
    select.replaceChildren(...options.map(([key, label]) => new Option(label, key)));
    select.value = value || "";
  };
  fillSelect("import-file-select", data.sources.map((name) => [name, name]), data.source);
  fillSelect("import-plate-select", data.plates.map((p) => [p.id, p.name]), data.plate);
  document.getElementById("import-file-row").classList.toggle("hidden", data.sources.length <= 1);
  document.getElementById("import-plate-row").classList.toggle("hidden", data.plates.length <= 1);
  document.getElementById("import-source").textContent = data.error || `Quelle: ${data.source} · Vorschau`;
  const bodies = document.getElementById("import-bodies");
  bodies.innerHTML = S.state.meshes.map((m) => `<label class="asm-item" title="${escapeAttr(m.size_mm.join(" × "))} mm">
    <input type="checkbox" data-import-body="${m.id}" ${S.imported.hidden.has(m.id) ? "" : "checked"}>
    <span class="legend-swatch" style="background:${m.color}"></span><span>${escapeHtml(m.name)}</span></label>`).join("");
  bodies.querySelectorAll("input").forEach((input) => input.addEventListener("change", async () => {
    if (input.checked) S.imported.hidden.delete(input.dataset.importBody);
    else S.imported.hidden.add(input.dataset.importBody);
    await rebuildSceneGeometry(false);
  }));
}

// File drops are intercepted at document level, including over sidebars and modals.
function bindImportDrop() {
  let depth = 0;
  let uploading = false;
  const overlay = document.getElementById("drop-overlay");
  const hasFiles = (ev) => Array.from(ev.dataTransfer?.types || []).includes("Files");
  const isUsageModalOpen = () => {
    const m = document.getElementById("usage-modal");
    return !!(m && !m.classList.contains("hidden"));
  };
  const reset = () => { depth = 0; overlay.classList.add("hidden"); };
  document.addEventListener("dragenter", (ev) => {
    if (!hasFiles(ev) || isUsageModalOpen()) return;
    ev.preventDefault();
    depth++;
    overlay.classList.remove("hidden");
  }, true);
  document.addEventListener("dragover", (ev) => {
    if (!hasFiles(ev) || isUsageModalOpen()) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
    overlay.classList.remove("hidden");
  }, true);
  document.addEventListener("dragleave", (ev) => {
    if (!hasFiles(ev) || isUsageModalOpen()) return;
    if (--depth <= 0) reset();
  }, true);
  document.addEventListener("drop", async (ev) => {
    if (!hasFiles(ev) || isUsageModalOpen()) return;
    ev.preventDefault();
    ev.stopPropagation();
    reset();
    if (uploading) { showToast("Ein Import läuft bereits."); return; }
    const files = Array.from(ev.dataTransfer.files);
    const directories = Array.from(ev.dataTransfer.items || []).some((item) => item.webkitGetAsEntry?.()?.isDirectory);
    if (directories || !files.length || files.some((f) => !/\.3mf$/i.test(f.name))) {
      showToast("Bitte eine oder mehrere 3MF-Dateien ablegen.");
      return;
    }
    if (files.length > 32 || files.reduce((n, f) => n + f.size, 0) > 256 * 1024 * 1024) {
      showToast("Maximal 32 Dateien und insgesamt 256 MB pro Import.");
      return;
    }
    uploading = true;
    setBusy(true, "3MF-Dateien werden importiert …");
    try {
      const form = new FormData();
      files.forEach((file) => form.append("files", file, file.name));
      const result = await apiFetch("/api/imports", { method: "POST", body: form });
      await loadProjectsList(result.project);
      showToast(`${files.length} 3MF-Datei${files.length === 1 ? "" : "en"} importiert`);
    } catch (err) {
      showToast(`Import fehlgeschlagen: ${err.message}`);
    } finally {
      uploading = false;
      setBusy(false);
    }
  }, true);
  window.addEventListener("blur", reset);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") reset(); });
  for (const [id, field] of [["import-file-select", "file"], ["import-plate-select", "plate"]]) {
    document.getElementById(id).addEventListener("change", async (ev) => {
      S.imported[field] = ev.target.value;
      if (field === "file") S.imported.plate = null;
      try { await selectProject(S.currentProject, null, true); }
      catch (err) { showToast(`Import fehlgeschlagen: ${err.message}`); }
    });
  }
}

function asmPose() {
  const posen = S.state?.assembly?.posen || [];
  if (!posen.length) return null;
  return posen[Math.min(Math.max(S.asm.pose, 0), posen.length - 1)];
}

function enterOrLeaveAssemblyMode(projectChanged) {
  const asm = isAssembly();
  document.getElementById("assembly-panel").classList.toggle("hidden", !asm);
  document.getElementById("versions-block").classList.toggle("hidden", asm || isImport());
  document.getElementById("space-group").classList.toggle("hidden", asm || isImport());
  document.getElementById("tool-bewegen").classList.toggle("hidden", !(asm && kinData()));
  if (!(asm && kinData()) && S.tool === "bewegen") setTool("orbit");
  if (asm) {
    if (S.asm.prevViewMode === null) S.asm.prevViewMode = S.viewMode;
    S.viewMode = "model"; // Baugruppen-Koordinaten = „Modell“-Raum (kein Druckbett)
    if (projectChanged) {
      const urlPose = new URLSearchParams(window.location.search).get("pose");
      const posen = S.state.assembly?.posen || [];
      let idx = -1;
      if (urlPose !== null) {
        idx = posen.findIndex((p) => p.name === urlPose);
        if (idx < 0 && /^\d+$/.test(urlPose) && Number(urlPose) < posen.length) idx = Number(urlPose);
      }
      S.asm.pose = idx >= 0 ? idx : 0;
      S.kin.selected = new Set();
      S.kin.bvh = new Map();
      kinResetToPose();
      const urlExplode = Number(new URLSearchParams(window.location.search).get("explode"));
      S.asm.explode = Number.isFinite(urlExplode) ? Math.min(Math.max(urlExplode, 0), 100) : 0;
      S.asm.hidden = new Set();
      document.getElementById("explode-slider").value = String(S.asm.explode);
      document.getElementById("explode-value").textContent = `${S.asm.explode} %`;
    }
    if (!S.kin.moved) kinResetToPose();
  } else if (S.asm.prevViewMode !== null) {
    S.viewMode = S.asm.prevViewMode;
    S.asm.prevViewMode = null;
    document.getElementById("btn-space-print").classList.toggle("active", S.viewMode === "print");
    document.getElementById("btn-space-model").classList.toggle("active", S.viewMode === "model");
  }
  pruneAssemblyCache();
}

function pruneAssemblyCache() {
  const keep = new Set();
  const aj = S.state?.assembly;
  if (aj) {
    for (const t of aj.teile || []) keep.add(t.url);
    for (const p of aj.posen || []) {
      for (const g of p.geister || []) keep.add(g.url);
      for (const u of p.ueberschneidungen || []) keep.add(u.url);
    }
  }
  for (const [url, prom] of S.asm.geoCache) {
    if (keep.has(url)) continue;
    prom.then((g) => g.dispose()).catch(() => {});
    S.asm.geoCache.delete(url);
    const e = S.asm.edgeCache.get(url);
    if (e) e.dispose();
    S.asm.edgeCache.delete(url);
  }
}

function loadSharedGeo(url) {
  let prom = S.asm.geoCache.get(url);
  if (!prom) {
    prom = loadSTL(url).then((geo) => {
      geo.computeVertexNormals();
      geo.computeBoundingBox();
      return geo;
    });
    prom.catch(() => S.asm.geoCache.delete(url));
    S.asm.geoCache.set(url, prom);
  }
  return prom;
}

function sharedEdges(url, geo) {
  let e = S.asm.edgeCache.get(url);
  if (!e) {
    e = new THREE.EdgesGeometry(geo, 15);
    S.asm.edgeCache.set(url, e);
  }
  return e;
}

function m4(arr) {
  const m = new THREE.Matrix4();
  m.set(...arr);
  return m;
}

function bboxOf(b) {
  return new THREE.Box3(new THREE.Vector3(...b[0]), new THREE.Vector3(...b[1]));
}

// Richtung, in die jedes Teil beim Auseinanderziehen wandert: Vorgabe aus assembly.py
// (Teil(explosion=…)) oder radial von der Baugruppenmitte weg.
function explodeVectors(pose) {
  const aj = S.state.assembly;
  const teile = Object.fromEntries(aj.teile.map((t) => [t.name, t]));
  const box = new THREE.Box3();
  for (const i of pose.instanzen) box.union(bboxOf(i.bbox));
  const c = box.getCenter(new THREE.Vector3());
  const diag = box.getSize(new THREE.Vector3()).length();
  const out = {};
  for (const i of pose.instanzen) {
    const t = teile[i.teil];
    const ci = bboxOf(i.bbox).getCenter(new THREE.Vector3());
    let v;
    if (t?.explosion) {
      v = new THREE.Vector3(...t.explosion);
      if (v.lengthSq() > 0) v.normalize().multiplyScalar(diag * 0.3);
      if (t.anzahl > 1) v.add(ci.clone().sub(c).multiplyScalar(0.4)); // gleiche Teile zusätzlich auseinander
    } else {
      v = ci.sub(c).multiplyScalar(0.6);
    }
    out[i.name] = v;
  }
  for (const g of pose.geister) {
    if (g.folgt) out[g.name] = (out[g.folgt] || out[`${g.folgt} 1`] || new THREE.Vector3()).clone();
  }
  return out;
}

function buildAssemblyEnvironment() {
  const aj = S.state?.assembly;
  const bb = aj?.bbox || [[-100, -100, 0], [100, 100, 50]];
  const z = bb[0][2] - 2;
  const step = 10;
  const x0 = Math.floor((bb[0][0] - 20) / step) * step;
  const x1 = Math.ceil((bb[1][0] + 20) / step) * step;
  const y0 = Math.floor((bb[0][1] - 20) / step) * step;
  const y1 = Math.ceil((bb[1][1] + 20) / step) * step;
  const pts = [];
  for (let x = x0; x <= x1; x += step) pts.push(x, y0, z, x, y1, z);
  for (let y = y0; y <= y1; y += step) pts.push(x0, y, z, x1, y, z);
  const gridGeo = new THREE.BufferGeometry();
  gridGeo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  envGroup.add(
    new THREE.LineSegments(gridGeo, new THREE.LineBasicMaterial({ color: 0x263449, transparent: true, opacity: 0.55 }))
  );
  const span = Math.max(bb[1][0] - bb[0][0], bb[1][1] - bb[0][1], bb[1][2] - bb[0][2]);
  addAxesHelper(new THREE.Vector3(0, 0, 0), Math.min(40, Math.max(12, span * 0.25)));
}

async function buildAssemblyScene(stale) {
  const aj = S.state.assembly;
  S.partBounds = null;
  if (!aj || !(aj.posen || []).length) {
    renderSceneLegend();
    return;
  }
  const pose = asmPose();
  const planes = activeClipPlanes();
  const teile = Object.fromEntries(aj.teile.map((t) => [t.name, t]));

  // Alle Netze dieser Pose (aus dem Cache, sonst einmal laden)
  const urls = [
    ...aj.teile.map((t) => t.url),
    ...pose.geister.map((g) => g.url),
    ...pose.ueberschneidungen.map((u) => u.url),
  ];
  const geos = {};
  try {
    const all = await Promise.all(urls.map((u) => loadSharedGeo(u)));
    if (stale()) return;
    urls.forEach((u, k) => (geos[u] = all[k]));
  } catch (err) {
    console.warn("Baugruppe: STL Ladefehler", err);
    showToast("Baugruppe: Netze konnten nicht geladen werden – build_assembly erneut ausführen");
    return;
  }

  S.asm.vectors = explodeVectors(pose);
  const box = new THREE.Box3();

  for (const inst of pose.instanzen) {
    const t = teile[inst.teil];
    const geo = geos[t.url];
    const base = m4(inst.matrix);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(t.farbe),
      roughness: 0.45,
      metalness: 0.06,
      side: THREE.DoubleSide,
      clippingPlanes: planes,
      clipShadows: true,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(base);
    mesh.userData = {
      bodyName: inst.name,
      teil: inst.teil,
      sharedGeo: true,
      baseMatrix: base,
      explodeKey: inst.name,
      asmKey: `teil:${inst.teil}`,
    };
    meshGroup.add(mesh);
    const edges = new THREE.LineSegments(
      sharedEdges(t.url, geo),
      new THREE.LineBasicMaterial({ color: 0x090d16, transparent: true, opacity: 0.5, clippingPlanes: planes })
    );
    edges.matrixAutoUpdate = false;
    edges.matrix.copy(base);
    edges.userData = { ...mesh.userData };
    edgesGroup.add(edges);
    box.union(geo.boundingBox.clone().applyMatrix4(base));
  }

  for (const g of pose.geister) {
    const geo = geos[g.url];
    const base = m4(g.matrix);
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({
        color: new THREE.Color(g.farbe),
        transparent: true,
        opacity: 0.2,
        depthWrite: false,
        side: THREE.DoubleSide,
        roughness: 0.7,
        clippingPlanes: planes,
      })
    );
    mesh.matrixAutoUpdate = false;
    mesh.matrix.copy(base);
    mesh.renderOrder = 2;
    mesh.userData = { ghostName: g.name, sharedGeo: true, baseMatrix: base, explodeKey: g.name, asmKey: `geist:${g.name}` };
    asmGhostGroup.add(mesh);
    const outline = new THREE.LineSegments(
      sharedEdges(g.url, geo),
      new THREE.LineBasicMaterial({
        color: new THREE.Color(g.farbe).lerp(new THREE.Color(0xffffff), 0.45),
        transparent: true,
        opacity: 0.4,
        clippingPlanes: planes,
      })
    );
    outline.matrixAutoUpdate = false;
    outline.matrix.copy(base);
    outline.userData = { ...mesh.userData };
    asmGhostGroup.add(outline);
    box.union(geo.boundingBox.clone().applyMatrix4(base));
  }

  for (const u of pose.ueberschneidungen) {
    const geo = geos[u.url];
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({
        color: u.ok ? 0xff7a1a : 0xff2e4c,
        emissive: u.ok ? 0x803000 : 0xaa0018,
        transparent: true,
        opacity: 0.92,
        depthTest: false, // liegt im Inneren der Teile – immer sichtbar
        side: THREE.DoubleSide,
        clippingPlanes: planes,
      })
    );
    mesh.matrixAutoUpdate = false;
    mesh.renderOrder = 10;
    mesh.userData = { sharedGeo: true, overlapPair: [u.a, u.b], asmKey: "ueberschneidung" };
    errorGroup.add(mesh);
    // Rahmen, damit auch winzige Überschneidungen auffallen
    const baseBox = bboxOf(u.bbox).expandByScalar(0.8);
    const helper = new THREE.Box3Helper(baseBox.clone(), u.ok ? 0xff9a3d : 0xff2e4c);
    helper.material.depthTest = false;
    helper.renderOrder = 11;
    helper.userData = { overlapPair: [u.a, u.b], asmKey: "ueberschneidung", isHelper: true, baseBox };
    errorGroup.add(helper);
  }

  S.partBounds = box.isEmpty() ? null : box;
  applyAssemblyVisibility();
  if (S.kin.moved) kinApplyToMeshes();
  else applyExplosion();
  kinHighlight();
}

function applyAssemblyVisibility() {
  for (const grp of [meshGroup, edgesGroup, asmGhostGroup, errorGroup]) {
    for (const o of grp.children) {
      const k = o.userData?.asmKey;
      if (k) o.visible = !S.asm.hidden.has(k);
    }
  }
}

function applyExplosion() {
  const f = S.asm.explode / 100;
  const zero = new THREE.Vector3();
  const off = (key) => (S.asm.vectors[key] || zero).clone().multiplyScalar(f);
  for (const grp of [meshGroup, edgesGroup, asmGhostGroup]) {
    for (const o of grp.children) {
      if (!o.userData?.baseMatrix) continue;
      const v = off(o.userData.explodeKey);
      o.matrix.makeTranslation(v.x, v.y, v.z).multiply(o.userData.baseMatrix);
      o.matrixWorldNeedsUpdate = true;
    }
  }
  for (const o of errorGroup.children) {
    const pair = o.userData?.overlapPair;
    if (!pair) continue;
    const v = off(pair[0]).add(off(pair[1])).multiplyScalar(0.5);
    if (o.userData.isHelper) {
      o.box.copy(o.userData.baseBox).translate(v);
    } else {
      o.matrix.makeTranslation(v.x, v.y, v.z);
      o.matrixWorldNeedsUpdate = true;
    }
  }
}

async function setAssemblyPose(idx) {
  S.asm.pose = idx;
  S.measurement = null;
  kinResetToPose();
  renderAssemblyPanel();
  renderAssemblyDiagnostics(document.getElementById("diagnostics-list"));
  await rebuildSceneGeometry(false);
  renderMeasureBox();
  if (S.tool === "bewegen") await kinSession();
  renderKinPanel();
}

function renderAssemblySummary() {
  const aj = S.state?.assembly;
  const badge = document.getElementById("project-status-badge");
  const dimsEl = document.getElementById("project-dims-text");
  if (!aj) {
    badge.className = "badge badge-neutral";
    badge.textContent = "Baugruppe – nicht gebaut";
    dimsEl.textContent = "build_assembly ausführen";
  } else {
    const bad = aj.posen.filter((p) => !p.ok).length;
    badge.className = bad ? "badge badge-err" : "badge badge-ok";
    badge.textContent = bad ? `${bad} von ${aj.posen.length} Posen kollidieren` : `Baugruppe OK (${aj.posen.length} Posen)`;
    const bb = aj.bbox;
    dimsEl.textContent = bb ? `${[0, 1, 2].map((i) => (bb[1][i] - bb[0][i]).toFixed(1)).join(" × ")} mm` : "–";
  }
  document.getElementById("saved-feedback-pill").classList.toggle("hidden", !S.state?.feedback);
  renderAssemblyPanel();
}

function renderAssemblyPanel() {
  const aj = S.state?.assembly;
  const sel = document.getElementById("pose-select");
  const legend = document.getElementById("assembly-legend");
  const checks = document.getElementById("assembly-checks");
  const status = document.getElementById("pose-status");
  sel.innerHTML = "";
  if (!aj) {
    legend.innerHTML = `<div class="empty-hint">Noch nicht gebaut: <code>build_assembly</code> bzw. <code>uv run slicetheprompt baugruppe ${escapeHtml(S.currentProject || "")}</code></div>`;
    checks.innerHTML = "";
    status.className = "badge badge-neutral";
    status.textContent = "–";
    return;
  }
  aj.posen.forEach((p, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = `${p.ok ? "✓" : "✗"} ${p.name}`;
    sel.appendChild(opt);
  });
  sel.value = String(S.asm.pose);
  const pose = asmPose();
  status.className = pose.ok ? "badge badge-ok" : "badge badge-err";
  status.textContent = pose.ok ? "frei" : "Kollision";

  const rows = [];
  for (const t of aj.teile) {
    const key = `teil:${t.name}`;
    const on = !S.asm.hidden.has(key);
    const st = t.status || {};
    const warn = st.veraltet ? " ⚠ veraltet" : st.passed === false ? " ✗" : "";
    const tip = `${t.projekt}${st.veraltet ? " – model.py neuer als STEP, Teil neu bauen" : ""}`;
    rows.push(`<label class="asm-item${on ? "" : " off"}" title="${escapeAttr(tip)}">
      <input type="checkbox" data-asm-key="${escapeAttr(key)}" ${on ? "checked" : ""} />
      <span class="legend-swatch" style="background:${t.farbe}"></span>
      <span>${escapeHtml(t.name)}${warn}</span>
      <span class="asm-meta">${t.anzahl > 1 ? `×${t.anzahl}` : ""}</span>
    </label>`);
  }
  for (const g of pose.geister) {
    const key = `geist:${g.name}`;
    const on = !S.asm.hidden.has(key);
    rows.push(`<label class="asm-item${on ? "" : " off"}" title="Bezugskörper – wird nicht gedruckt">
      <input type="checkbox" data-asm-key="${escapeAttr(key)}" ${on ? "checked" : ""} />
      <span class="legend-swatch ghost" style="background:${g.farbe}"></span>
      <span>${escapeHtml(g.name)} <span class="asm-meta">(Geist)</span></span>
      <span></span>
    </label>`);
  }
  const onOv = !S.asm.hidden.has("ueberschneidung");
  rows.push(`<label class="asm-item${onOv ? "" : " off"}" title="Überschneidungen (rot = Fehler, orange = erlaubter Klemmkontakt)">
      <input type="checkbox" data-asm-key="ueberschneidung" ${onOv ? "checked" : ""} />
      <span class="legend-swatch overlap"></span>
      <span>Überschneidungen</span>
      <span class="asm-meta">${pose.ueberschneidungen.length}</span>
    </label>`);
  legend.innerHTML = rows.join("");
  legend.querySelectorAll("input[data-asm-key]").forEach((inp) => {
    inp.addEventListener("change", () => {
      const k = inp.dataset.asmKey;
      if (inp.checked) S.asm.hidden.delete(k);
      else S.asm.hidden.add(k);
      inp.closest(".asm-item").classList.toggle("off", !inp.checked);
      applyAssemblyVisibility();
      renderSceneLegend();
    });
  });

  // Prüfergebnis dieser Pose
  renderKinPanel();
  checks.innerHTML = "";
  for (const c of pose.checks) {
    const u = pose.ueberschneidungen.find((x) => `${x.a} + ${x.b}` === c.paar);
    const el = document.createElement("div");
    el.className = `diag-item ${c.ok ? (c.art === "klemmkontakt" ? "warn" : "ok") : "error"}`;
    const title = c.ok ? (c.art === "klemmkontakt" ? "Klemmkontakt (erlaubt)" : "Überschneidung ≤ Grenze") : "Kollision";
    el.innerHTML = `<div><div class="diag-title">${title}</div><div class="diag-sub">${escapeHtml(c.text)}${u ? " · Klick zeigt die Stelle" : ""}</div></div>`;
    if (u) el.addEventListener("click", () => focusAssemblyBox(u.bbox));
    checks.appendChild(el);
  }
  if (!pose.checks.length) {
    const el = document.createElement("div");
    el.className = "diag-item ok";
    el.innerHTML = `<div><div class="diag-title">Keine Überschneidung</div><div class="diag-sub">Alle Teile und Geister frei</div></div>`;
    checks.appendChild(el);
  }
  const gaps = pose.abstaende.filter((a) => a.genau && a.mm > 0).slice(0, 6);
  if (gaps.length) {
    const tbl = document.createElement("table");
    tbl.className = "asm-gaps";
    tbl.innerHTML = `<tbody><tr><td colspan="2">Engste Spalte:</td></tr>${gaps
      .map((a) => `<tr><td>${escapeHtml(a.a)} ↔ ${escapeHtml(a.b)}</td><td class="mono">${a.mm.toFixed(2)} mm</td></tr>`)
      .join("")}</tbody>`;
    checks.appendChild(tbl);
  }
}

function focusAssemblyBox(bb) {
  if (S.asm.explode) {
    S.asm.explode = 0;
    document.getElementById("explode-slider").value = "0";
    document.getElementById("explode-value").textContent = "0 %";
    applyExplosion();
  }
  focusBox3D(null, [...bb[0], ...bb[1]]);
}

function renderAssemblyDiagnostics(wrap) {
  wrap.innerHTML = "";
  const aj = S.state?.assembly;
  const badge = document.getElementById("badge-issues-count");
  if (!aj) {
    wrap.innerHTML = `<div class="empty-hint">Baugruppe noch nicht gebaut.</div>`;
    badge.classList.add("hidden");
    return;
  }
  let bad = 0;
  aj.posen.forEach((p, i) => {
    const fails = p.checks.filter((c) => !c.ok);
    bad += fails.length;
    const kontakt = p.checks.filter((c) => c.ok && c.art === "klemmkontakt");
    const sub = fails.length
      ? fails.map((c) => escapeHtml(c.text)).join("<br>")
      : kontakt.length
        ? kontakt.map((c) => escapeHtml(c.text)).join("<br>")
        : "keine Überschneidung";
    const el = document.createElement("div");
    el.className = `diag-item ${fails.length ? "error" : "ok"}`;
    el.style.cursor = "pointer";
    el.innerHTML = `<div><div class="diag-title">${i === S.asm.pose ? "▶ " : ""}${escapeHtml(p.name)}</div><div class="diag-sub">${sub}</div></div>`;
    el.addEventListener("click", () => setAssemblyPose(i));
    wrap.appendChild(el);
  });
  for (const h of aj.hinweise || []) {
    const el = document.createElement("div");
    el.className = "diag-item warn";
    el.innerHTML = `<div><div class="diag-title">Hinweis</div><div class="diag-sub">${escapeHtml(h)}</div></div>`;
    wrap.appendChild(el);
  }
  badge.textContent = String(bad);
  badge.classList.toggle("hidden", !bad);
}

function renderAssemblyChecksAndSpec() {
  const aj = S.state?.assembly;
  const checksWrap = document.getElementById("checks-table-wrap");
  const specWrap = document.getElementById("spec-details");
  if (!aj) {
    checksWrap.innerHTML = `<div class="empty-hint">Keine Prüfdaten vorhanden.</div>`;
    specWrap.innerHTML = "";
  } else {
    const rows = [];
    for (const p of aj.posen) {
      if (!p.checks.length) {
        rows.push(`<tr><td><span style="color:var(--green)">✓</span> ${escapeHtml(p.name)}</td><td class="mono">frei</td><td class="mono">–</td></tr>`);
      }
      for (const c of p.checks) {
        const ist = c.tiefe_mm != null ? `${c.volumen_mm3.toFixed(2)} mm³ · ${c.tiefe_mm.toFixed(2)} mm` : `${c.volumen_mm3.toFixed(2)} mm³`;
        const u = p.ueberschneidungen.find((x) => `${x.a} + ${x.b}` === c.paar);
        const lim = c.art === "klemmkontakt" ? `Tiefe ≤ ${u?.erlaubt_tiefe_mm ?? "?"} mm` : `≤ ${aj.grenze_mm3} mm³`;
        const mark = c.ok ? `<span style="color:var(--green)">✓</span>` : `<span style="color:var(--red)">✗</span>`;
        rows.push(`<tr><td>${mark} ${escapeHtml(p.name)}: ${escapeHtml(c.paar)}</td><td class="mono">${ist}</td><td class="mono">${lim}</td></tr>`);
      }
    }
    checksWrap.innerHTML = `<table class="checks-table"><thead><tr><th>Pose: Paar</th><th>Ist</th><th>Grenze</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
    const teile = aj.teile
      .map(
        (t) =>
          `<li><span class="legend-swatch" style="display:inline-block;width:10px;height:10px;background:${t.farbe}"></span> ${escapeHtml(t.name)}${t.anzahl > 1 ? ` ×${t.anzahl}` : ""} – <code>${escapeHtml(t.projekt)}</code>${t.status?.passed === false ? " ✗" : ""}${t.status?.veraltet ? " ⚠ veraltet" : ""}</li>`
      )
      .join("");
    specWrap.innerHTML = `
      <div><strong>Baugruppe:</strong> ${escapeHtml(aj.beschreibung || aj.projekt)}</div>
      <div><strong>Teile:</strong><ul class="spec-list">${teile}</ul></div>
      <div><strong>Posen:</strong> ${aj.posen.length} · gebaut ${escapeHtml(aj.erstellt || "")} (${aj.zeit_s} s)</div>`;
  }
  const previewWrap = document.getElementById("render-preview-wrap");
  previewWrap.innerHTML = S.state?.render_png_url ? `<img src="${S.state.render_png_url}" alt="Baugruppe" />` : "";
  document.getElementById("model-code-view").textContent = S.state?.model_code || "";
}

// ============================================================================
// Bewegen (Gelenke): Auswahl bewegt sich, alles andere ist fest
// ============================================================================
function kinData() {
  return isAssembly() ? S.state?.assembly?.kinematik || null : null;
}

const rowToM4 = (a) => new THREE.Matrix4().set(...a);
const m4ToRow = (m) => {
  const e = m.elements; // spaltenweise
  return [e[0], e[4], e[8], e[12], e[1], e[5], e[9], e[13], e[2], e[6], e[10], e[14], e[3], e[7], e[11], e[15]];
};

function globMatch(pat, name) {
  const re = new RegExp("^" + pat.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
  return re.test(name);
}

function kinBodyInfo() {
  // Körper → {teil, url} (Netz) aus der aktuellen Pose
  const aj = S.state.assembly;
  const pose = asmPose();
  const teile = Object.fromEntries(aj.teile.map((t) => [t.name, t]));
  const info = {};
  for (const i of pose.instanzen) info[i.name] = { teil: i.teil, url: teile[i.teil].url, geist: false };
  for (const g of pose.geister) info[g.name] = { teil: g.name, url: g.url, geist: true };
  return info;
}

function kinResetToPose() {
  const pose = asmPose();
  S.kin.poseWorld = {};
  if (pose) {
    for (const i of pose.instanzen) S.kin.poseWorld[i.name] = i.matrix.slice();
    for (const g of pose.geister) S.kin.poseWorld[g.name] = g.matrix.slice();
  }
  S.kin.world = Object.fromEntries(Object.entries(S.kin.poseWorld).map(([k, v]) => [k, v.slice()]));
  S.kin.mech = null;
  S.kin.moved = false;
  S.kin.live = null;
  S.kin.drag = null;
  S.kin.blocked = null;
}

function kinPairMatches(pairs, a, b) {
  const info = S.kin.info;
  const names = (x) => [x, info[x]?.teil].filter(Boolean);
  const m = (pat, x) => names(x).some((n) => globMatch(pat, n));
  return pairs.find(([p, q]) => (m(p, a) && m(q, b)) || (m(p, b) && m(q, a)));
}

// IGNORIEREN-Paare prüft das Bewegen nicht
function kinSkipPair(a, b) {
  return !!kinPairMatches(S.state.assembly.ignorieren || [], a, b);
}

// Toleranz je Paar: erlaubter Klemmkontakt (KONTAKT) oder 0,02 mm
function kinEps(a, b) {
  const k = (S.state.assembly.kontakt || []).find((x) => kinPairMatches([x.paar], a, b));
  return k ? Math.max(0.02, Number(k.tiefe_mm) || 0) : 0.02;
}

async function kinBVH(url) {
  let b = S.kin.bvh.get(url);
  if (!b) {
    const geo = await loadSharedGeo(url);
    b = buildBVH(geo.getAttribute("position").array);
    S.kin.bvh.set(url, b);
  }
  return b;
}

// Neue Sitzung: aktuelle Lage als Start, nicht ausgewählte Körper fest
async function kinSession() {
  const kin = kinData();
  if (!kin) return;
  S.kin.info = kinBodyInfo();
  // Nicht Gewähltes steht still – ausser es sitzt auf einem gewählten Teil, dann fährt es mit
  const plan = freezePlan(kin, S.kin.selected);
  S.kin.riders = plan.locks.map(([b]) => b);
  S.kin.mech = new Mechanism(kin, S.kin.world, plan.frozen, plan.locks);
  // Netze für die Kollision vorbereiten; Überschneidungen, die schon beim Start da waren, blockieren
  // nicht (wird erst beim ersten Treffer des Paars nachgesehen)
  for (const b of S.kin.mech.bodies) if (S.kin.info[b]) await kinBVH(S.kin.info[b].url);
  S.kin.preexisting = new Map();
  renderKinPanel();
}

function kinPairHit(a, b, Wa, Wb) {
  const ia = S.kin.info[a], ib = S.kin.info[b];
  if (!ia || !ib) return null;
  const A = S.kin.bvh.get(ia.url), B = S.kin.bvh.get(ib.url);
  if (!A || !B) return null;
  return meshesPenetrate(A, Wa, B, Wb, kinEps(a, b));
}

// Kollision der bewegten Körper gegen alle anderen (null = frei)
function kinCollision() {
  const m = S.kin.mech;
  const moving = [...m.movable, ...m.carried].filter((b) => m.moved(b, 1e-7));
  if (!moving.length) return null;
  const W = {}, D = {};
  for (const b of m.bodies) {
    const mv = moving.includes(b);
    W[b] = mv ? m.worldOf(b) : S.kin.world[b];
    D[b] = mv ? m.displacement(b) : null;
  }
  const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const sameMotion = (a, b) => {
    const da = D[a] || I, db = D[b] || I;
    for (let i = 0; i < 12; i++) if (Math.abs(da[i] - db[i]) > 1e-7) return false;
    return true;
  };
  for (const a of moving) {
    for (const b of m.bodies) {
      if (a === b || (moving.includes(b) && b < a)) continue;
      if (sameMotion(a, b) || kinSkipPair(a, b)) continue; // gemeinsam bewegt: keine neue Berührung möglich
      const key = [a, b].sort().join("|");
      if (S.kin.preexisting.get(key)) continue;
      const hit = kinPairHit(a, b, W[a], W[b]);
      if (!hit) continue;
      if (!S.kin.preexisting.has(key)) {
        const before = !!kinPairHit(a, b, S.kin.world[a], S.kin.world[b]);
        S.kin.preexisting.set(key, before);
        if (before) continue;
      }
      return { a, b, punkt: hit.punkt };
    }
  }
  return null;
}

// Einen Schritt lösen; dringt etwas ein, bis kurz vor den Kontakt zurück (Halbierung)
function kinStep(opts) {
  const m = S.kin.mech;
  if (!m) return;
  const t0 = performance.now();
  const before = m.snapshot();
  const res = m.solve({ iters: 24, ...opts });
  let hit = kinCollision();
  if (hit) {
    const after = m.snapshot();
    let lo = 0, hi = 1;
    for (let k = 0; k < 8; k++) {
      const t = (lo + hi) / 2;
      m.blend(before, after, t);
      if (kinCollision()) hi = t;
      else lo = t;
    }
    // Die schnelle Prüfung duldet 0,02 mm Eindringen (damit aufliegende Flächen gleiten können).
    // Für das anstossende Paar die Stelle mit 0,002 mm nachsuchen, damit auch die exakte Prüfung
    // keine Überschneidung findet – ausser es lag schon vorher so eng an.
    const fine = () => {
      const W = (b) => (m.movable.includes(b) ? m.worldOf(b) : S.kin.world[b]);
      const ia = S.kin.info[hit.a], ib = S.kin.info[hit.b];
      return meshesPenetrate(S.kin.bvh.get(ia.url), W(hit.a), S.kin.bvh.get(ib.url), W(hit.b), 0.002);
    };
    m.restore(before);
    if (kinEps(hit.a, hit.b) <= 0.02 && !fine()) {
      let flo = 0, fhi = lo;
      for (let k = 0; k < 10; k++) {
        const t = (flo + fhi) / 2;
        m.blend(before, after, t);
        if (fine()) fhi = t;
        else flo = t;
      }
      lo = flo;
    }
    m.blend(before, after, lo);
    S.kin.blocked = hit;
    flashBlocked(hit);
  } else {
    S.kin.blocked = null;
  }
  S.kin.lastRest = res.rest;
  S.kin.lastMs = performance.now() - t0;
  kinCommitVisual();
}

function kinCommitVisual() {
  const m = S.kin.mech;
  for (const b of [...m.movable, ...m.carried]) S.kin.world[b] = m.worldOf(b);
  S.kin.moved = Object.keys(S.kin.world).some((b) =>
    S.kin.world[b].some((v, i) => Math.abs(v - S.kin.poseWorld[b][i]) > 1e-6)
  );
  kinApplyToMeshes();
  renderKinValues();
}

function kinApplyToMeshes() {
  for (const grp of [meshGroup, edgesGroup, asmGhostGroup]) {
    for (const o of grp.children) {
      const name = o.userData?.bodyName || o.userData?.ghostName;
      if (name && S.kin.world[name] && o.userData.baseMatrix) o.userData.baseMatrix.copy(rowToM4(S.kin.world[name]));
    }
  }
  // Überschneidungen der Pose gelten für die bewegte Lage nicht mehr
  for (const o of errorGroup.children) {
    if (o.userData?.asmKey === "ueberschneidung" && !o.userData.live) o.visible = !S.kin.moved && !S.asm.hidden.has("ueberschneidung");
  }
  applyExplosion();
  kinHighlight();
}

function kinHighlight() {
  for (const grp of [meshGroup, asmGhostGroup]) {
    for (const o of grp.children) {
      if (!o.isMesh || !o.material?.emissive) continue;
      const name = o.userData?.bodyName || o.userData?.ghostName;
      const sel = S.tool === "bewegen" && S.kin.selected.has(name);
      o.material.emissive.setHex(sel ? 0x2a6fdb : 0x000000);
      o.material.emissiveIntensity = sel ? 0.3 : 1;
    }
  }
}

function flashBlocked(hit) {
  const now = performance.now();
  if (!S.kin.lastFlash || now - S.kin.lastFlash > 1500) {
    showToast(`Anschlag: ${hit.a} ↔ ${hit.b}`);
    S.kin.lastFlash = now;
  }
  clearGroup(focusPulseGroup);
  const s = new THREE.Mesh(
    new THREE.SphereGeometry(1.6, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0xff2e4c, transparent: true, opacity: 0.85, depthTest: false })
  );
  s.position.set(...hit.punkt);
  s.renderOrder = 20;
  focusPulseGroup.add(s);
  if (S.flashTimer) clearTimeout(S.flashTimer);
  S.flashTimer = setTimeout(() => clearGroup(focusPulseGroup), 1200);
}

const HAUPT_UI = { drehung: "°", schraube: "°", zylinder: "mm", schiebung: "mm" };

function renderKinPanel() {
  const panel = document.getElementById("kin-panel");
  const kin = kinData();
  panel.classList.toggle("hidden", !kin);
  document.getElementById("tool-bewegen").classList.toggle("hidden", !kin);
  if (!kin) return;
  const info = document.getElementById("kin-info");
  const list = document.getElementById("kin-joints");
  const m = S.kin.mech;
  const sel = [...S.kin.selected];
  if (!sel.length) {
    info.innerHTML = `Werkzeug <b>✥ Bewegen</b> (Taste 6): Teile anklicken (Umschalt = mehrere), dann ziehen oder Regler nutzen. Nicht gewählte Teile bleiben fest.`;
  } else {
    const dof = m ? m.dof() : "–";
    const riders = S.kin.riders || [];
    info.innerHTML = `Ausgewählt: <b>${sel.map(escapeHtml).join(", ")}</b>${
      riders.length ? `<br>fährt mit: ${riders.map(escapeHtml).join(", ")}` : ""}<br>Freiheitsgrade der Auswahl: <b>${dof}</b>${
      dof === 0 ? " – durch Gelenke und feste Teile blockiert; weitere Teile der Kette mitwählen" : ""}`;
  }
  list.innerHTML = "";
  const coords = m ? m.coords() : S.kin.lastCoords || asmPose()?.gelenkwerte || {};
  for (const j of kin.gelenke) {
    if (!HAUPT_UI[j.typ]) continue;
    const active = m && (S.kin.selected.has(j.a) || (j.b && S.kin.selected.has(j.b))) &&
      (m.movable.includes(j.a) || (j.b && m.movable.includes(j.b)));
    const unit = HAUPT_UI[j.typ];
    const v = coords[j.name] ?? 0;
    const lo = j.grenzen ? j.grenzen[0] : unit === "°" ? Math.min(-180, v - 180) : Math.min(-50, v - 50);
    const hi = j.grenzen ? j.grenzen[1] : unit === "°" ? Math.max(180, v + 180) : Math.max(50, v + 50);
    const row = document.createElement("div");
    row.className = "kin-row" + (active ? "" : " off");
    row.innerHTML = `<div class="kin-name" title="${escapeAttr(j.typ)}: ${escapeAttr(j.a)} ↔ ${escapeAttr(j.b || "Boden")}">${escapeHtml(j.name)}</div>
      <input type="range" min="${lo}" max="${hi}" step="${unit === "°" ? 1 : 0.1}" value="${v}" data-joint="${escapeAttr(j.name)}" ${active ? "" : "disabled"} />
      <span class="kin-val mono-dim" data-val="${escapeAttr(j.name)}">${v.toFixed(unit === "°" ? 1 : 2)} ${unit}</span>`;
    list.appendChild(row);
  }
  list.querySelectorAll("input[data-joint]").forEach((inp) => {
    inp.addEventListener("input", () => {
      const m = S.kin.mech;
      if (!m) return;
      // in Teilschritten fahren (≤ 90° bzw. 2 mm), damit nichts durch ein Hindernis springt
      const name = inp.dataset.joint;
      const j = m.byName[name];
      const from = m.coord(j), to = Number(inp.value);
      const maxStep = HAUPT_UI[j.typ] === "°" ? 90 : 2;
      const n = Math.max(1, Math.ceil(Math.abs(to - from) / maxStep));
      for (let k = 1; k <= n; k++) {
        kinStep({ drives: { [name]: from + ((to - from) * k) / n } });
        if (S.kin.blocked) break;
      }
      if (S.kin.blocked) inp.value = String(m.coord(j));
    });
    inp.addEventListener("change", () => kinSession());
  });
  document.getElementById("btn-kin-check").disabled = !S.kin.moved;
  document.getElementById("btn-kin-reset").disabled = !S.kin.moved;
  renderKinLive();
}

function renderKinValues() {
  const m = S.kin.mech;
  if (!m) return;
  const c = m.coords();
  S.kin.lastCoords = c;
  document.querySelectorAll("#kin-joints [data-val]").forEach((el) => {
    const n = el.dataset.val;
    if (c[n] === undefined) return;
    const unit = el.textContent.trim().endsWith("°") ? "°" : "mm";
    el.textContent = `${c[n].toFixed(unit === "°" ? 1 : 2)} ${unit}`;
    const inp = document.querySelector(`#kin-joints input[data-joint="${CSS.escape(n)}"]`);
    if (inp && document.activeElement !== inp) inp.value = String(c[n]);
  });
  document.getElementById("btn-kin-check").disabled = !S.kin.moved;
  document.getElementById("btn-kin-reset").disabled = !S.kin.moved;
  const st = document.getElementById("kin-status");
  const ms = S.kin.lastMs ? ` · ${Math.round(S.kin.lastMs)} ms` : "";
  st.textContent = (S.kin.blocked ? `Anschlag: ${S.kin.blocked.a} ↔ ${S.kin.blocked.b}` : S.kin.moved ? "bewegt – „Exakt prüfen“ für Überschneidungen" : "") + (S.kin.moved ? ms : "");
  st.className = "kin-status" + (S.kin.blocked ? " blocked" : "");
}

function renderKinLive() {
  const wrap = document.getElementById("kin-live");
  const r = S.kin.live;
  if (!r) {
    wrap.innerHTML = "";
    return;
  }
  if (r.error) {
    wrap.innerHTML = `<div class="diag-item error"><div><div class="diag-title">Prüfung fehlgeschlagen</div><div class="diag-sub">${escapeHtml(r.error)}</div></div></div>`;
    return;
  }
  const items = r.checks.length
    ? r.checks.map((c) => `<div class="diag-item ${c.ok ? (c.art === "klemmkontakt" ? "warn" : "ok") : "error"}"><div><div class="diag-title">${c.ok ? "ok" : "Kollision"}</div><div class="diag-sub">${escapeHtml(c.text)}</div></div></div>`)
    : [`<div class="diag-item ok"><div><div class="diag-title">Exakt geprüft: frei</div><div class="diag-sub">keine Überschneidung in der bewegten Lage (${r.zeit_s} s)</div></div></div>`];
  wrap.innerHTML = items.join("");
}

async function kinCheckExact() {
  const pose = asmPose();
  if (!pose) return;
  const versch = {};
  for (const [b, W] of Object.entries(S.kin.world)) {
    const P = S.kin.poseWorld[b];
    if (!P || W.every((v, i) => Math.abs(v - P[i]) < 1e-9)) continue;
    versch[b] = m4ToRow(rowToM4(W).multiply(rowToM4(P).invert()));
  }
  setBusy(true, "Bewegte Stellung wird exakt geprüft …");
  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(S.currentProject)}/check_state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pose: S.asm.pose, verschiebung: versch }),
    });
    S.kin.live = res;
    // rote Überschneidungen der bewegten Lage zeigen
    for (const o of [...errorGroup.children]) if (o.userData?.live) errorGroup.remove(o);
    for (const u of res.ueberschneidungen || []) {
      const geo = await loadSTL(u.url);
      geo.computeVertexNormals();
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
        color: u.ok ? 0xff7a1a : 0xff2e4c, emissive: u.ok ? 0x803000 : 0xaa0018, transparent: true, opacity: 0.92,
        depthTest: false, side: THREE.DoubleSide,
      }));
      mesh.renderOrder = 10;
      mesh.userData = { live: true, asmKey: "ueberschneidung" };
      errorGroup.add(mesh);
      const helper = new THREE.Box3Helper(bboxOf(u.bbox).expandByScalar(0.8), u.ok ? 0xff9a3d : 0xff2e4c);
      helper.material.depthTest = false;
      helper.userData = { live: true, asmKey: "ueberschneidung" };
      errorGroup.add(helper);
    }
    showToast(res.ok ? `✓ Exakt geprüft: frei (${res.zeit_s} s)` : `✗ ${res.checks.filter((c) => !c.ok).length} Kollision(en) in der bewegten Lage`);
  } catch (err) {
    S.kin.live = { error: err.message };
  } finally {
    setBusy(false);
    renderKinLive();
  }
}

async function kinResetButton() {
  kinResetToPose();
  for (const o of [...errorGroup.children]) if (o.userData?.live) errorGroup.remove(o);
  kinApplyToMeshes();
  if (S.tool === "bewegen") await kinSession();
  renderKinPanel();
}

async function kinSelect(name, additive) {
  if (!name) {
    if (!additive) S.kin.selected.clear();
  } else if (additive) {
    if (S.kin.selected.has(name)) S.kin.selected.delete(name);
    else S.kin.selected.add(name);
  } else {
    S.kin.selected = new Set([name]);
  }
  await kinSession();
  kinHighlight();
}

// Zeiger-Logik für das Werkzeug „Bewegen“ (vor den übrigen Werkzeugen)
const kinPlane = new THREE.Plane();
function kinPointerDown(ev) {
  if (S.tool !== "bewegen" || ev.button !== 0 || !kinData()) return false;
  const hit = getPointerRayHit(ev.clientX, ev.clientY, false);
  const body = hit?.onMesh ? hit.body : null;
  S.kin.down = { x: ev.clientX, y: ev.clientY, body, shift: ev.shiftKey || ev.ctrlKey || ev.metaKey };
  if (body && S.kin.selected.has(body) && S.kin.mech) {
    const p = new THREE.Vector3(...hit.scene_xyz);
    const n = camera.getWorldDirection(new THREE.Vector3());
    kinPlane.setFromNormalAndCoplanarPoint(n, p);
    S.kin.drag = { body, point: hit.scene_xyz, started: false };
    controls.enabled = false;
    canvas.setPointerCapture(ev.pointerId);
    return true;
  }
  return false;
}

let kinRaf = 0;
function kinPointerMove(ev) {
  if (!S.kin.drag) return false;
  const rect = canvas.getBoundingClientRect();
  mouseNDC.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  mouseNDC.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(mouseNDC, camera);
  const t = new THREE.Vector3();
  if (!raycaster.ray.intersectPlane(kinPlane, t)) return true;
  S.kin.drag.target = [t.x, t.y, t.z];
  S.kin.drag.started = true;
  if (!kinRaf) {
    kinRaf = requestAnimationFrame(() => {
      kinRaf = 0;
      const d = S.kin.drag;
      if (d?.target) kinStep({ drag: { body: d.body, point: d.point, target: d.target, weight: 1 }, iters: 36 });
    });
  }
  return true;
}

async function kinPointerUp(ev) {
  if (S.tool !== "bewegen" || !S.kin.down) return false;
  const down = S.kin.down;
  S.kin.down = null;
  const dragged = S.kin.drag?.started;
  if (S.kin.drag) {
    S.kin.drag = null;
    controls.enabled = true;
    try { canvas.releasePointerCapture(ev.pointerId); } catch { /* egal */ }
  }
  if (dragged) {
    await kinSession(); // neue Ausgangslage für den nächsten Zug
    return true;
  }
  if (Math.hypot(ev.clientX - down.x, ev.clientY - down.y) <= 5) await kinSelect(down.body, down.shift);
  return true;
}

Object.assign(window.__studio, { kinSelect, setAssemblyPose }); // Test-Hook

function handleResize() {
  const w = container.clientWidth;
  const h = container.clientHeight;
  if (w <= 0 || h <= 0) return;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h, false);
}

window.addEventListener("resize", handleResize);
new ResizeObserver(handleResize).observe(container);

let clockStart = performance.now();
function animate() {
  requestAnimationFrame(animate);
  controls.update();

  // Subtle pulse on ghost_cut meshes so missed cuts immediately catch the eye
  if (ghostCutGroup.children.length > 0 && ghostCutGroup.visible) {
    const t = (performance.now() - clockStart) * 0.004;
    const op = 0.35 + 0.25 * (0.5 + 0.5 * Math.sin(t));
    for (const c of ghostCutGroup.children) {
      if (c.isMesh && c.material) c.material.opacity = op;
    }
  }

  renderer.render(scene, camera);
  updateLabelPositions();
}

// Boot Studio
(async function init() {
  handleResize();
  bindEvents();
  bindImportDrop();
  animate();
  await loadProjectsList();
  connectSSE();
  pollPrinterStatus();
  setInterval(pollPrinterStatus, 4000);
  loadPrices();
  setInterval(() => loadPrinterDetails(false), 15000);
  initUsageUI({
    onOpenProjectVersion: (proj, ver) => selectProject(proj, ver, true),
    getCurrentProject: () => S.currentProject,
  });
  setInterval(() => refreshUsageOverview({ selectCurrentProject: S.currentProject }), 30000);
})();
