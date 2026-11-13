"""HTTP-API- & Studio-Integrationstests für das Usage- & Chat-Timeline-Dashboard."""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from slicetheprompt import ui_server  # noqa: E402
from test_usage_eval import build_synthetic_env  # noqa: E402
from test_usage_screenshot import build_synthetic_usage_screenshot  # noqa: E402


def _req(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, str | bytes, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "json" in ctype or "text" in ctype or "javascript" in ctype:
                return resp.status, raw.decode("utf-8", errors="replace"), dict(resp.headers)
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", errors="replace"), dict(err.headers)


def test_usage_http_endpoints():
    with tempfile.TemporaryDirectory() as td:
        agy, proot, cal, _now = build_synthetic_env(Path(td))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.StudioHandler)
        srv.projects_dir = proot
        srv.agy_dir = agy
        srv.usage_cal_path = cal
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        try:
            # 1. Static /usage Route & Module
            st, body, _ = _req(f"{base}/usage")
            assert st == 200 and 'id="usage-modal"' in body and 'id="usage-status-pill"' in body
            assert 'id="ai-part-cost-box"' in body and 'id="btn-ai-cost-reload"' in body
            st_js, js_body, _ = _req(f"{base}/usage.js")
            assert st_js == 200 and "updateScannerAccumulator" in js_body
            assert "Modell-Varianten vergleichen" in js_body and "Tokens, Öko-Bilanz" in js_body
            st_iso, iso_body, _ = _req(f"{base}/usage_iso.js")
            assert st_iso == 200 and "renderIsoStl" in iso_body

            # 2. Overview JSON
            st_ov, ov_txt, _ = _req(f"{base}/api/usage/overview?days=7")
            assert st_ov == 200
            ov = json.loads(ov_txt)
            assert ov["ok"] is True
            assert set(ov["pools"].keys()) >= {"gemini", "claude_gpt", "argon"}
            assert any(p["project_name"] == "2026-11-13-test-adapter" for p in ov["projects"])

            # 3. Project Detail + History STL Serving
            st_pd, pd_txt, _ = _req(f"{base}/api/usage/projects/2026-11-13-test-adapter")
            assert st_pd == 200
            pd = json.loads(pd_txt)
            assert pd["ok"] is True and len(pd["turns"]) == 2
            stl_url = pd["turns"][0]["versions"][0]["stl_url"]
            st_stl, stl_bytes, _ = _req(f"{base}{stl_url}")
            assert st_stl == 200 and b"solid v1" in (stl_bytes if isinstance(stl_bytes, bytes) else stl_bytes.encode())

            # 4. Conversation Detail (Argon anderer Workspace)
            st_cd, cd_txt, _ = _req(f"{base}/api/usage/conversations/conv-other-ws")
            assert st_cd == 200
            cd = json.loads(cd_txt)
            assert cd["ok"] is True and cd["turns"][0]["pool_id"] == "argon"

            # 5. Kalibrierung & Eco-Toggle + neues Modell via POST
            st_cal, cal_txt, _ = _req(
                f"{base}/api/usage/calibrate",
                method="POST",
                payload={
                    "days": 7,
                    "snapshots": [
                        {"pool_id": "gemini", "horizon": "weekly", "pct_left": 64, "resets_in": "1d 6h"},
                        {"pool_id": "gemini", "horizon": "5h", "pct_left": 90, "resets_in": "3h 9m"},
                        {"pool_id": "argon", "horizon": "weekly", "pct_left": 4, "resets_in": "2d 3h"},
                    ],
                    "settings": {
                        "show_eco_metrics": True,
                        "new_pool": {"id": "gemini_next", "label": "Gemini Next", "has_weekly": True, "has_5h": False},
                    },
                },
            )
            assert st_cal == 200
            cal_res = json.loads(cal_txt)
            assert cal_res["ok"] is True
            ov2 = cal_res["overview"]
            assert ov2["settings"]["show_eco_metrics"] is True
            assert ov2["pools"]["gemini"]["weekly"]["pct_left"] == 64.0
            assert ov2["pools"]["gemini"]["h5"]["pct_left"] == 90.0
            assert ov2["pools"]["argon"]["weekly"]["pct_left"] == 4.0
            assert "gemini_next" in ov2["pools"]
            assert cal.is_file()

            # 6. Path-Traversal Guard
            st_sec, _, _ = _req(f"{base}/api/usage/projects/..%2F..%2Fetc")
            assert st_sec in (400, 403, 404)

            # 7. Screenshot-Importer API: POST /api/usage/parse-screenshot -> POST /api/usage/calibrate
            img = build_synthetic_usage_screenshot(two_column=True)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

            st_bad, _, _ = _req(
                f"{base}/api/usage/parse-screenshot",
                method="POST",
                payload={"image_base64": "not-valid-base64!!!"},
            )
            assert st_bad == 400

            st_ss, ss_txt, _ = _req(
                f"{base}/api/usage/parse-screenshot",
                method="POST",
                payload={"image_base64": data_url},
            )
            assert st_ss == 200
            ss_res = json.loads(ss_txt)
            assert ss_res["ok"] is True
            assert len(ss_res["rings"]) == 5
            assert len(ss_res["snapshots"]) == 5
            assert any(m["name"] == "Gemini 4 Carbon" and m["known"] is False for m in ss_res["detected_models"])

            # Übernahme der Screenshot-Snapshots + Ring-Metadaten + custom_models via /api/usage/calibrate
            snaps_payload = [
                {
                    "pool_id": sn["pool_id"],
                    "horizon": sn["horizon"],
                    "pct_left": sn["pct_left"],
                    "resets_in": sn["resets_in"],
                    "ring_pct": sn["ring_pct"],
                    "ring_color": sn["ring_color"],
                    "ocr_pct": sn["ocr_pct"],
                    "source": sn["source"],
                }
                for sn in ss_res["snapshots"]
            ]
            st_c2, c2_txt, _ = _req(
                f"{base}/api/usage/calibrate",
                method="POST",
                payload={
                    "days": 7,
                    "snapshots": snaps_payload,
                    "settings": {
                        "custom_models": {
                            "gemini-4-carbon": {
                                "display_name": "Gemini 4 Carbon",
                                "pool_id": "gemini",
                                "input_per_1m": 1.25,
                                "output_per_1m": 5.0,
                            }
                        }
                    },
                },
            )
            assert st_c2 == 200
            ov3 = json.loads(c2_txt)["overview"]
            assert ov3["pools"]["gemini"]["weekly"]["pct_left"] == 61.0
            assert ov3["pools"]["gemini"]["ring_tracking"]["ring_color"] == "green"
            assert abs(ov3["pools"]["gemini"]["ring_tracking"]["ring_pct"] - 81.0) <= 3.0
            assert ov3["pools"]["claude_gpt"]["ring_tracking"]["ring_color"] in ("amber", "green")
            assert ov3["pools"]["argon"]["ring_tracking"]["ring_color"] == "red"
            assert "gemini-4-carbon" in ov3["settings"]["custom_models"]

            raw_cal = yaml.safe_load(cal.read_text(encoding="utf-8"))
            assert len(raw_cal["pools"]["gemini"]["ring_history"]) >= 2
            assert raw_cal["pools"]["gemini"]["anchor_weekly"]["ring_color"] == "green"
            assert raw_cal["pools"]["gemini"]["anchor_weekly"]["source"] == "ocr+ring"
            assert raw_cal["pools"]["argon"]["anchor_weekly"]["ring_color"] == "red"

        finally:
            srv.shutdown()
            srv.server_close()


def run_all() -> None:
    test_usage_http_endpoints()
    print("OK: test_usage_http_endpoints")
    print("Alle Usage-UI-API-Tests bestanden.")


if __name__ == "__main__":
    run_all()
