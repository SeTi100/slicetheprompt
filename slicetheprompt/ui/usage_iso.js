import * as THREE from "three";
import { STLLoader } from "/vendor/STLLoader.js";

/**
 * Offscreen Three.js Studio-Renderer für hochauflösende isometrische CAD-Iterationen.
 * Nutzt genau EINEN WebGLRenderer + FIFO-Warteschlange + DataURL-Cache,
 * damit auch Projekte mit vielen History-Versionen blitzschnell und GPU-schonend rendern.
 */

const WIDTH = 560;
const HEIGHT = 420;
const _cache = new Map();
const _inFlight = new Map();
let _loader = null;
let _renderer = null;
let _queue = Promise.resolve();

function _getRenderer() {
  if (_renderer) return _renderer;
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  _renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    preserveDrawingBuffer: true,
    alpha: false,
  });
  _renderer.setPixelRatio(1);
  _renderer.setSize(WIDTH, HEIGHT, false);
  _renderer.setClearColor(0x0c111a, 1.0);
  _renderer.toneMapping = THREE.ACESFilmicToneMapping;
  _renderer.toneMappingExposure = 1.08;
  return _renderer;
}

function _getLoader() {
  if (!_loader) _loader = new STLLoader();
  return _loader;
}

function _fetchStlGeometry(stlUrl) {
  return new Promise((resolve, reject) => {
    _getLoader().load(
      stlUrl,
      (geo) => resolve(geo),
      undefined,
      (err) => reject(err || new Error(`STL Ladefehler: ${stlUrl}`))
    );
  });
}

function _canvasToImageUrl(canvas) {
  return new Promise((resolve) => {
    if (typeof canvas.toBlob === "function") {
      canvas.toBlob(
        (blob) => {
          if (blob) {
            resolve(URL.createObjectURL(blob));
          } else {
            resolve(canvas.toDataURL("image/png"));
          }
        },
        "image/png"
      );
      return;
    }
    resolve(canvas.toDataURL("image/png"));
  });
}

async function _renderGeometryToDataUrl(geo, passed = true) {
  const renderer = _getRenderer();
  const scene = new THREE.Scene();

  const hemi = new THREE.HemisphereLight(0xdbeafe, 0x1e293b, 0.9);
  hemi.position.set(0, 0, 400);
  scene.add(hemi);

  const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
  keyLight.position.set(220, -260, 340);
  scene.add(keyLight);

  const fillLight = new THREE.DirectionalLight(0x93c5fd, 0.48);
  fillLight.position.set(-200, 240, 160);
  scene.add(fillLight);

  geo.computeVertexNormals();
  geo.computeBoundingBox();
  const box = geo.boundingBox.clone();
  const center = new THREE.Vector3();
  const size = new THREE.Vector3();
  box.getCenter(center);
  box.getSize(size);

  const mat = new THREE.MeshStandardMaterial({
    color: passed ? 0x38bdf8 : 0xf59e0b,
    roughness: 0.4,
    metalness: 0.08,
  });
  const mesh = new THREE.Mesh(geo, mat);
  scene.add(mesh);

  const edgeGeo = new THREE.EdgesGeometry(geo, 24);
  const edgeMat = new THREE.LineBasicMaterial({
    color: passed ? 0xe0f2fe : 0xfef3c7,
    transparent: true,
    opacity: 0.42,
  });
  const edges = new THREE.LineSegments(edgeGeo, edgeMat);
  scene.add(edges);

  // Dezentes Bauplatten-Gitter unterhalb minZ
  const maxSpan = Math.max(size.x, size.y, size.z, 12);
  const gridSize = Math.ceil((maxSpan * 1.65) / 10) * 10;
  const grid = new THREE.GridHelper(gridSize, Math.min(20, Math.max(6, Math.round(gridSize / 10))), 0x334155, 0x1e293b);
  grid.rotation.x = Math.PI / 2;
  grid.position.set(center.x, center.y, box.min.z - 0.15);
  scene.add(grid);

  // Auto-framed Isometric Camera (Z nach oben)
  const camera = new THREE.PerspectiveCamera(36, WIDTH / HEIGHT, 0.2, maxSpan * 30);
  camera.up.set(0, 0, 1);
  const dir = new THREE.Vector3(1.0, -1.08, 0.84).normalize();
  const sphere = new THREE.Sphere();
  box.getBoundingSphere(sphere);
  const radius = Math.max(sphere.radius, 5);
  const fovRad = (camera.fov * Math.PI) / 180;
  const dist = (radius / Math.sin(fovRad / 2)) * 1.06;

  camera.position.copy(center).addScaledVector(dir, dist);
  camera.lookAt(center);
  camera.updateMatrixWorld(true);

  renderer.render(scene, camera);
  const imgUrl = await _canvasToImageUrl(renderer.domElement);

  // Aufräumen
  scene.remove(mesh, edges, grid);
  mat.dispose();
  edgeGeo.dispose();
  edgeMat.dispose();
  if (grid.geometry) grid.geometry.dispose();
  if (Array.isArray(grid.material)) grid.material.forEach((m) => m.dispose());
  else if (grid.material) grid.material.dispose();
  geo.dispose();

  return imgUrl;
}

/**
 * Rendert ein STL als hochauflösendes PNG (Blob-/Data-URL) in isometrischer Studio-Ansicht.
 * @param {string} stlUrl
 * @param {boolean} passed
 * @returns {Promise<string>}
 */
export function renderIsoStl(stlUrl, passed = true) {
  if (!stlUrl) return Promise.reject(new Error("STL-URL fehlt"));
  const key = `${stlUrl}|${passed ? "1" : "0"}`;
  if (_cache.has(key)) return Promise.resolve(_cache.get(key));
  if (_inFlight.has(key)) return _inFlight.get(key);

  const geoPromise = _fetchStlGeometry(stlUrl);

  const p = new Promise((resolve, reject) => {
    _queue = _queue
      .then(async () => {
        if (_cache.has(key)) return _cache.get(key);
        const geo = await geoPromise;
        const url = await _renderGeometryToDataUrl(geo, passed);
        _cache.set(key, url);
        await new Promise((r) => setTimeout(r, 0));
        return url;
      })
      .then(resolve, reject);
  }).finally(() => {
    _inFlight.delete(key);
  });

  _inFlight.set(key, p);
  return p;
}

export function clearIsoCache() {
  for (const url of _cache.values()) {
    if (typeof url === "string" && url.startsWith("blob:")) {
      URL.revokeObjectURL(url);
    }
  }
  _cache.clear();
}
