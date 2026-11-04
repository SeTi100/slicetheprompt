"""Lokaler Web-Server (HTTP + SSE) für printlab Studio (WP-3).

Rein lesender Zugriff auf Moonraker/CFS – startet NIEMALS einen Druck!
"""

from __future__ import annotations

import base64
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import socket
import threading
import time
from typing import Any
import urllib.parse
import webbrowser

import yaml

from . import history, meshcheck as mc, params as params_mod, pipeline
from .config import PROJECTS_DIR, load_config, project_dir
from .moonraker import Moonraker

UI_DIR = Path(__file__).resolve().parent / "ui"

MIME_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".stl": "model/stl",
}

_PRINTER_CACHE_LOCK = threading.Lock()
_PRINTER_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _resolve_project_in_root(name: str, root: Path) -> Path:
    """Sucht ein Projekt sicher innerhalb von `root` (ohne Path-Traversal)."""
    clean = name.strip().strip("/\\")
    if not clean or ".." in clean or "/" in clean or "\\" in clean:
        raise PermissionError(f"Ungültiger Projektname: {name}")
    root_res = root.resolve()
    cand = (root_res / clean).resolve()
    if not cand.is_relative_to(root_res):
        raise PermissionError(f"Ungültiger Projektpfad: {name}")
    if cand.is_dir():
        return cand
    matches = sorted(
        d.resolve() for d in root_res.glob(f"*{clean}*")
        if d.is_dir() and d.resolve().is_relative_to(root_res)
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise FileNotFoundError(f"Projekt '{clean}' ist mehrdeutig")
    raise FileNotFoundError(f"Projekt '{clean}' nicht gefunden")


def list_projects_summary(projects_root: Path = PROJECTS_DIR) -> list[dict[str, Any]]:
    """Liefert alle Projekte inkl. Prüfstatus, Maßen, Versionen und Feedback-Status."""
    items: list[dict[str, Any]] = []
    if not projects_root.exists():
        return items

    for d in sorted(projects_root.iterdir()):
        if not d.is_dir() or not (d / "model.py").exists():
            continue
        out = d / "out"
        rep = _read_json(out / "report.json") or {}
        bi = _read_json(out / "build.json") or {}
        spec = _read_yaml(d / "spec.yaml")
        versions = history.list_versions(str(d))
        built = (out / "print.stl").exists() or (out / "model.stl").exists()
        passed = rep.get("passed") if rep else None
        errors_count = len(rep.get("errors") or [])
        warnings_count = len(rep.get("warnings") or [])
        size_mm = (
            rep.get("model", {}).get("size_mm")
            or bi.get("size_mm")
            or (versions[-1].get("size_mm") if versions else None)
        )
        bodies = [b["name"] for b in (bi.get("bodies") or []) if isinstance(b, dict) and "name" in b]
        mtimes = [
            p.stat().st_mtime
            for p in (d / "model.py", d / "spec.yaml", out / "report.json", out / "ui_feedback.json")
            if p.exists()
        ]
        items.append({
            "project": d.name,
            "beschreibung": spec.get("beschreibung") or "",
            "material": spec.get("material") or "pla",
            "built": built,
            "passed": passed,
            "errors_count": errors_count,
            "warnings_count": warnings_count,
            "size_mm": size_mm,
            "bodies": bodies or None,
            "version_count": len(versions),
            "latest_version": versions[-1]["version"] if versions else None,
            "has_feedback": (out / "ui_feedback.json").exists(),
            "mtime": max(mtimes) if mtimes else 0.0,
        })
    items.sort(key=lambda x: (x["mtime"], x["project"]), reverse=True)
    return items


def build_project_state(d: Path, version: str | None = None) -> dict[str, Any]:
    """Baut das vollständige Zustandspaket eines Projekts (aktuell oder historische Version)."""
    cfg = load_config()
    versions = history.list_versions(str(d))
    latest_version = versions[-1]["version"] if versions else None

    is_historical = False
    active_version = latest_version
    asset_dir = d / "out"
    asset_prefix = "out"
    model_path = d / "model.py"
    spec_path = d / "spec.yaml"

    if version and version not in ("current", "working", "head"):
        v_dir = (d / "history" / version).resolve()
        if not v_dir.is_relative_to((d / "history").resolve()) or not v_dir.is_dir():
            raise FileNotFoundError(f"Version '{version}' nicht gefunden")
        is_historical = True
        active_version = version
        asset_dir = v_dir
        asset_prefix = f"history/{version}"
        if (v_dir / "model.py").exists():
            model_path = v_dir / "model.py"
        if (v_dir / "spec.yaml").exists():
            spec_path = v_dir / "spec.yaml"

    spec = _read_yaml(spec_path)
    model_code = model_path.read_text(encoding="utf-8") if model_path.exists() else ""
    try:
        extracted_params = params_mod.extract_params(model_path) if model_path.exists() else []
    except Exception:
        extracted_params = []

    bi = _read_json(asset_dir / "build.json") or {}
    po = _read_json(asset_dir / "print.json") or {}
    rep = _read_json(asset_dir / "report.json") or {}
    diag = _read_json(asset_dir / "diagnostics_3d.json") or {}
    fb = _read_json(d / "out" / "ui_feedback.json")

    raw_bodies = bi.get("bodies") or []
    names = [b["name"] for b in raw_bodies if isinstance(b, dict) and "name" in b]
    colors = pipeline._body_colors(d, names if names else ["_single"])

    # Cache-Busting-Stempel anhand der STL-Änderungszeit
    stl_ref = asset_dir / "print.stl"
    ts_tag = int(stl_ref.stat().st_mtime * 1000) if stl_ref.exists() else int(time.time() * 1000)
    base_files = f"/api/projects/{urllib.parse.quote(d.name)}/files/{asset_prefix}"

    meshes: list[dict[str, Any]] = []
    if names:
        for n in names:
            slug = pipeline.body_slug(n)
            p_stl = asset_dir / f"print_{slug}.stl"
            m_stl = asset_dir / f"body_{slug}.stl"
            if p_stl.exists():
                meshes.append({
                    "name": n,
                    "color": colors.get(n, "#4682B4"),
                    "print_url": f"{base_files}/print_{slug}.stl?t={ts_tag}",
                    "model_url": (
                        f"{base_files}/body_{slug}.stl?t={ts_tag}"
                        if m_stl.exists()
                        else f"{base_files}/print_{slug}.stl?t={ts_tag}"
                    ),
                })
    else:
        p_stl = asset_dir / "print.stl"
        m_stl = asset_dir / "model.stl"
        if p_stl.exists() or m_stl.exists():
            meshes.append({
                "name": "_teil",
                "color": colors.get("_single", "#4682B4"),
                "print_url": f"{base_files}/print.stl?t={ts_tag}" if p_stl.exists() else None,
                "model_url": (
                    f"{base_files}/model.stl?t={ts_tag}"
                    if m_stl.exists()
                    else (f"{base_files}/print.stl?t={ts_tag}" if p_stl.exists() else None)
                ),
            })

    ghost_meshes: list[dict[str, Any]] = []
    for g_file in sorted(asset_dir.glob("ghost_*_print.stl")):
        stem = g_file.name[: -len("_print.stl")]
        orig_file = asset_dir / f"{stem}.stl"
        feat_name = stem[len("ghost_"):]
        ghost_meshes.append({
            "name": feat_name,
            "print_url": f"{base_files}/{g_file.name}?t={ts_tag}",
            "model_url": (
                f"{base_files}/{orig_file.name}?t={ts_tag}"
                if orig_file.exists()
                else f"{base_files}/{g_file.name}?t={ts_tag}"
            ),
        })

    bed_cfg = cfg.get("printer", {}).get("bed", {"x": 260, "y": 260, "z": 300})
    usable_cfg = cfg.get("rules", {}).get("max_part_xy", [250, 250])

    return {
        "project": d.name,
        "version": active_version,
        "latest_version": latest_version,
        "is_historical": is_historical,
        "spec": spec,
        "model_code": model_code,
        "params": extracted_params,
        "build": bi,
        "print_info": po,
        "report": rep,
        "diagnostics": diag,
        "feedback": fb,
        "versions": versions,
        "colors": colors,
        "meshes": meshes,
        "ghost_meshes": ghost_meshes,
        "full_print_stl_url": f"{base_files}/print.stl?t={ts_tag}" if (asset_dir / "print.stl").exists() else None,
        "full_model_stl_url": f"{base_files}/model.stl?t={ts_tag}" if (asset_dir / "model.stl").exists() else None,
        "render_png_url": f"{base_files}/render.png?t={ts_tag}" if (asset_dir / "render.png").exists() else None,
        "printer_bed": {"x": bed_cfg.get("x", 260), "y": bed_cfg.get("y", 260), "z": bed_cfg.get("z", 300), "usable_xy": usable_cfg},
        "orientations": ["auto"] + list(mc.ORIENTATIONS.keys()),
    }


def save_project_feedback(d: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Speichert 3D-Markierungen nach out/ui_feedback.json und dekodiert Screenshot nach out/ui_feedback.png."""
    out = d / "out"
    out.mkdir(parents=True, exist_ok=True)
    png_path = (out / "ui_feedback.png").resolve()

    data_url = payload.get("screenshot_data_url") or ""
    if isinstance(data_url, str) and data_url.startswith("data:image/png;base64,"):
        b64_part = data_url.split(",", 1)[1]
        png_bytes = base64.b64decode(b64_part)
        png_path.write_bytes(png_bytes)

    versions = history.list_versions(str(d))
    based_on = payload.get("based_on_version") or (versions[-1]["version"] if versions else None)

    record = {
        "project": d.name,
        "based_on_version": based_on,
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "view_mode": payload.get("view_mode") or "print",
        "general_note": (payload.get("general_note") or "").strip(),
        "screenshot": str(png_path) if png_path.exists() else None,
        "annotations": payload.get("annotations") or [],
    }
    fb_path = out / "ui_feedback.json"
    fb_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return record


def clear_project_feedback(d: Path) -> dict[str, Any]:
    """Entfernt erledigtes 3D-Feedback (ui_feedback.json + ui_feedback.png)."""
    out = d / "out"
    removed = []
    for fname in ("ui_feedback.json", "ui_feedback.png"):
        p = out / fname
        if p.exists():
            p.unlink(missing_ok=True)
            removed.append(fname)
    return {"ok": True, "project": d.name, "removed": removed}


def read_project_feedback(project: str = "", projects_root: Path = PROJECTS_DIR) -> dict[str, Any]:
    """Liest ui_feedback.json eines Projekts (oder des zuletzt markierten Projekts)."""
    if project:
        d = project_dir(project) if Path(project).is_absolute() else _resolve_project_in_root(project, projects_root)
    else:
        candidates = []
        if projects_root.exists():
            for sub in projects_root.iterdir():
                fb_file = sub / "out" / "ui_feedback.json"
                if sub.is_dir() and fb_file.exists():
                    candidates.append((fb_file.stat().st_mtime, sub))
        if not candidates:
            return {"ok": False, "has_feedback": False, "message": "Kein offenes UI-Feedback in projects/ gefunden."}
        candidates.sort(key=lambda t: t[0], reverse=True)
        d = candidates[0][1]

    fb_path = d / "out" / "ui_feedback.json"
    if not fb_path.exists():
        return {"ok": False, "has_feedback": False, "project": d.name, "message": f"Kein ui_feedback.json in {d.name}."}

    data = json.loads(fb_path.read_text(encoding="utf-8"))
    data["ok"] = True
    data["has_feedback"] = True
    data["hint"] = (
        "Sieh dir zusätzlich das markierte Screenshot-Bild unter `screenshot` mit Read/read_file an. "
        "Nutze `model_xyz` / `delta_model_mm` / `bbox_model` direkt für Änderungen in `model.py` und "
        "rufe nach erfolgreichem `build_and_check` `clear_ui_feedback` auf."
    )
    return data


def get_printer_readonly_cached(ttl: float = 2.0) -> dict[str, Any]:
    """Rein lesender Moonraker- und CFS-Status mit kurzem In-Memory-Cache."""
    now = time.monotonic()
    with _PRINTER_CACHE_LOCK:
        if _PRINTER_CACHE["data"] is not None and (now - _PRINTER_CACHE["ts"]) < ttl:
            return _PRINTER_CACHE["data"]

    try:
        m = Moonraker(timeout=1.5)
        st = m.status()
        cfs = m.cfs()
        res: dict[str, Any] = {
            "connected": True,
            "status": st,
            "cfs": cfs.get("slots", []),
        }
    except Exception as exc:
        res = {"connected": False, "error": str(exc), "status": None, "cfs": []}

    with _PRINTER_CACHE_LOCK:
        _PRINTER_CACHE["ts"] = time.monotonic()
        _PRINTER_CACHE["data"] = res
    return res


def _projects_watch_signature(projects_root: Path) -> dict[str, float]:
    sig: dict[str, float] = {}
    if not projects_root.exists():
        return sig
    for d in projects_root.iterdir():
        if not d.is_dir():
            continue
        out = d / "out"
        mtimes = [
            p.stat().st_mtime
            for p in (
                d / "model.py",
                d / "spec.yaml",
                out / "build.json",
                out / "report.json",
                out / "print.stl",
                out / "ui_feedback.json",
            )
            if p.exists()
        ]
        if mtimes:
            sig[d.name] = max(mtimes)
    return sig


class StudioServer(ThreadingHTTPServer):
    """ThreadingHTTPServer mit sauberem Shutdown für offene SSE-Verbindungen."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], projects_dir: Path = PROJECTS_DIR):
        self.projects_dir = Path(projects_dir).resolve()
        self.stop_event = threading.Event()
        self.build_lock = threading.Lock()
        super().__init__(server_address, StudioHandler)

    def shutdown(self) -> None:
        self.stop_event.set()
        super().shutdown()


class StudioHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Stilles Logging, um Konsole/Tests sauber zu halten
        return

    @property
    def projects_root(self) -> Path:
        return getattr(self.server, "projects_dir", PROJECTS_DIR).resolve()

    def _send_json(self, obj: Any, status: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False, default=float).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _read_body_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > 30 * 1024 * 1024:
            raise ValueError("Payload zu gross (> 30 MB)")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _serve_static(self, url_path: str) -> None:
        rel = url_path.lstrip("/") or "index.html"
        unquoted = urllib.parse.unquote(rel)
        if ".." in PurePosixPath(unquoted).parts or "\\" in unquoted:
            self._send_json({"ok": False, "error": "Forbidden"}, status=403)
            return

        ui_root = UI_DIR.resolve()
        target = (ui_root / unquoted).resolve()
        if not target.is_relative_to(ui_root):
            self._send_json({"ok": False, "error": "Forbidden"}, status=403)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send_json({"ok": False, "error": f"Nicht gefunden: {unquoted}"}, status=404)
            return

        ext = target.suffix.lower()
        ctype = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        cache = "public, max-age=3600" if unquoted.startswith("vendor/") else "no-cache"
        self._send_bytes(target.read_bytes(), ctype, status=200, cache=cache)

    def _serve_project_file(self, proj_name: str, raw_rel: str) -> None:
        unquoted_rel = urllib.parse.unquote(raw_rel).lstrip("/")
        norm = unquoted_rel.replace("\\", "/")
        parts = [p for p in norm.split("/") if p != ""]
        if not parts or ".." in parts or "." in parts:
            self._send_json({"ok": False, "error": "Path traversal blocked"}, status=403)
            return
        if parts[0] not in ("out", "history"):
            self._send_json({"ok": False, "error": "Nur Zugriff auf out/ und history/ erlaubt"}, status=403)
            return

        try:
            d = _resolve_project_in_root(urllib.parse.unquote(proj_name), self.projects_root)
        except PermissionError:
            self._send_json({"ok": False, "error": "Forbidden"}, status=403)
            return
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
            return

        allowed_base = (d / parts[0]).resolve()
        target = (d.joinpath(*parts)).resolve()
        if not target.is_relative_to(allowed_base):
            self._send_json({"ok": False, "error": "Path traversal blocked"}, status=403)
            return
        if not target.is_file():
            self._send_json({"ok": False, "error": "Datei nicht gefunden"}, status=404)
            return

        ext = target.suffix.lower()
        ctype = MIME_OVERRIDES.get(ext) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send_bytes(target.read_bytes(), ctype, status=200, cache="no-store")

    def _handle_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        stop_ev: threading.Event | None = getattr(self.server, "stop_event", None)
        last_sig = _projects_watch_signature(self.projects_root)
        last_ping = time.monotonic()

        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                if stop_ev is not None and stop_ev.wait(1.0):
                    break
                if stop_ev is None:
                    time.sleep(1.0)

                curr_sig = _projects_watch_signature(self.projects_root)
                changed = [
                    proj for proj, mtime in curr_sig.items()
                    if last_sig.get(proj) != mtime
                ]
                if changed or set(curr_sig.keys()) != set(last_sig.keys()):
                    last_sig = curr_sig
                    payload = json.dumps({"projects": changed, "ts": time.time()})
                    msg = f"event: project_updated\ndata: {payload}\n\n".encode("utf-8")
                    self.wfile.write(msg)
                    self.wfile.flush()
                elif time.monotonic() - last_ping >= 15.0:
                    last_ping = time.monotonic()
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        raw_path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if raw_path == "/api/projects":
                self._send_json(list_projects_summary(self.projects_root))
                return

            if raw_path == "/api/printer":
                self._send_json(get_printer_readonly_cached())
                return

            if raw_path == "/api/events":
                self._handle_sse()
                return

            if raw_path.startswith("/api/projects/"):
                rest = raw_path[len("/api/projects/"):]
                if "/files/" in rest:
                    proj_part, file_part = rest.split("/files/", 1)
                    self._serve_project_file(proj_part, file_part)
                    return

                segs = [urllib.parse.unquote(s) for s in rest.split("/") if s]
                if len(segs) == 2 and segs[1] == "state":
                    d = _resolve_project_in_root(segs[0], self.projects_root)
                    ver = (qs.get("version") or [None])[0]
                    self._send_json(build_project_state(d, version=ver))
                    return

                if len(segs) == 2 and segs[1] == "diff":
                    d = _resolve_project_in_root(segs[0], self.projects_root)
                    v_old = (qs.get("old") or [""])[0]
                    v_new = (qs.get("new") or ["current"])[0]
                    if not v_old:
                        versions = history.list_versions(str(d))
                        v_old = versions[-2]["version"] if len(versions) >= 2 else (versions[0]["version"] if versions else "current")
                    diff_txt = history.diff_code(str(d), v_old, v_new)
                    versions_map = {v["version"]: v for v in history.list_versions(str(d))}
                    self._send_json({
                        "ok": True,
                        "project": d.name,
                        "old": v_old,
                        "new": v_new,
                        "diff": diff_txt,
                        "meta_old": versions_map.get(v_old),
                        "meta_new": versions_map.get(v_new),
                    })
                    return

                self._send_json({"ok": False, "error": "Unbekannter API-Pfad"}, status=404)
                return

            if raw_path.startswith("/api/"):
                self._send_json({"ok": False, "error": "Unbekannter API-Endpunkt"}, status=404)
                return

            self._serve_static(raw_path)

        except PermissionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=403)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        raw_path = parsed.path

        try:
            if not raw_path.startswith("/api/projects/"):
                self._send_json({"ok": False, "error": "Unbekannter POST-Endpunkt"}, status=404)
                return

            segs = [urllib.parse.unquote(s) for s in raw_path[len("/api/projects/"):].split("/") if s]
            if len(segs) != 2:
                self._send_json({"ok": False, "error": "Ungültiger Pfad"}, status=404)
                return

            proj_name, action = segs
            d = _resolve_project_in_root(proj_name, self.projects_root)
            body = self._read_body_json()
            lock: threading.Lock = getattr(self.server, "build_lock", threading.Lock())

            if action == "feedback":
                rec = save_project_feedback(d, body)
                self._send_json({"ok": True, "feedback": rec})
                return

            if action == "params":
                updates = body.get("params") or {}
                orient = body.get("orientation") or "wie modelliert"
                clean_updates: dict[str, float | int] = {}
                for k, v in updates.items():
                    clean_updates[str(k)] = float(v) if isinstance(v, float) or ("." in str(v)) else int(v)
                with lock:
                    params_mod.update_params(d / "model.py", clean_updates)
                    run_res = pipeline.run_all(str(d), orient, reason="ui_param_edit")
                self._send_json({"ok": run_res.get("stage") == "done", "run": run_res, "state": build_project_state(d)})
                return

            if action == "rebuild":
                orient = body.get("orientation") or "wie modelliert"
                with lock:
                    run_res = pipeline.run_all(str(d), orient, reason="ui_rebuild")
                self._send_json({"ok": run_res.get("stage") == "done", "run": run_res, "state": build_project_state(d)})
                return

            if action == "restore":
                ver = body.get("version") or ""
                if not ver:
                    self._send_json({"ok": False, "error": "Feld 'version' fehlt"}, status=400)
                    return
                with lock:
                    run_res = history.restore_version(str(d), ver)
                self._send_json({"ok": run_res.get("stage") == "done", "run": run_res, "state": build_project_state(d)})
                return

            self._send_json({"ok": False, "error": f"Unbekannte Aktion: {action}"}, status=404)

        except PermissionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=403)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except KeyError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        raw_path = parsed.path

        try:
            if not raw_path.startswith("/api/projects/"):
                self._send_json({"ok": False, "error": "Unbekannter DELETE-Endpunkt"}, status=404)
                return
            segs = [urllib.parse.unquote(s) for s in raw_path[len("/api/projects/"):].split("/") if s]
            if len(segs) == 2 and segs[1] == "feedback":
                d = _resolve_project_in_root(segs[0], self.projects_root)
                self._send_json(clear_project_feedback(d))
                return
            self._send_json({"ok": False, "error": "Unbekannter Pfad"}, status=404)
        except PermissionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=403)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    projects_dir: Path = PROJECTS_DIR,
) -> StudioServer:
    """Erstellt einen gebundenen StudioServer (bei port != 0 mit Fallback 8765..8775)."""
    if port == 0:
        return StudioServer((host, 0), projects_dir=projects_dir)

    last_err: OSError | None = None
    for candidate in range(port, port + 11):
        try:
            return StudioServer((host, candidate), projects_dir=projects_dir)
        except OSError as exc:
            last_err = exc
    raise OSError(f"Kein freier Port im Bereich {port}..{port + 10}: {last_err}")


def serve_ui(
    host: str = "127.0.0.1",
    port: int = 8765,
    project: str | None = None,
    open_browser: bool = True,
) -> int:
    """Startet printlab Studio im Vordergrund bis Ctrl+C."""
    srv = create_server(host=host, port=port, projects_dir=PROJECTS_DIR)
    actual_port = srv.server_address[1]
    url = f"http://{host}:{actual_port}/"
    if project:
        try:
            resolved = project_dir(project).name
        except Exception:
            resolved = project
        url += f"?project={urllib.parse.quote(resolved)}"

    print(f"printlab Studio läuft auf: {url}", flush=True)
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nprintlab Studio beendet.", flush=True)
    finally:
        srv.shutdown()
        srv.server_close()
    return 0
