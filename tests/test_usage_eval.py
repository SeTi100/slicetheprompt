"""Tests für slicetheprompt.usage_store & slicetheprompt.usage_eval."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slicetheprompt import usage_eval as UE  # noqa: E402
from slicetheprompt import usage_store as US  # noqa: E402


# -----------------------------------------------------------------------------
# Mini-Protobuf-Encoder für synthetische Antigravity-.db-Tests
# -----------------------------------------------------------------------------

def _pb_varint(val: int) -> bytes:
    out = bytearray()
    v = int(val)
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pb_field_v(fn: int, val: int) -> bytes:
    return _pb_varint((fn << 3) | 0) + _pb_varint(val)


def _pb_field_b(fn: int, data: bytes | str) -> bytes:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return _pb_varint((fn << 3) | 2) + _pb_varint(len(raw)) + raw


def _make_ts_pb(ts: dt.datetime) -> bytes:
    sec = int(ts.timestamp())
    return _pb_field_v(1, sec) + _pb_field_v(2, 0)


def _make_step_row(idx: int, step_type: int, exec_id: str, ts: dt.datetime, user_text: str = "", tool_name: str = "", tool_args: dict | None = None) -> tuple[int, int, int, bytes, bytes]:
    md = _pb_field_b(1, _make_ts_pb(ts)) + _pb_field_b(12, exec_id)
    pl = b""
    if step_type == 14:
        pl = _pb_field_b(19, _pb_field_b(2, user_text))
    elif step_type == 132 and tool_name:
        tc = _pb_field_b(2, tool_name) + _pb_field_b(3, json.dumps(tool_args or {}))
        pl = _pb_field_b(5, _pb_field_b(4, tc))
    return (idx, step_type, 3, md, pl)


def _make_gen_meta_row(
    idx: int,
    exec_id: str,
    step_indices: list[int],
    model_enum: str,
    model_slug: str,
    model_display: str,
    fresh_in: int,
    cache_read: int,
    output_total: int,
    thinking_tokens: int,
    visible_output: int,
    ctx_used: int = 40000,
    ctx_limit: int = 256000,
) -> tuple[int, bytes]:
    f4 = (
        _pb_field_v(2, fresh_in)
        + _pb_field_v(3, output_total)
        + _pb_field_v(5, cache_read)
        + _pb_field_v(9, thinking_tokens)
        + _pb_field_v(10, visible_output)
    )
    f9 = _pb_field_b(10, _pb_field_v(1, ctx_used) + _pb_field_v(4, ctx_limit))
    f20_entry = _pb_field_b(1, "model_enum") + _pb_field_b(2, model_enum)
    f1 = (
        _pb_field_b(4, f4)
        + _pb_field_b(9, f9)
        + _pb_field_b(19, model_slug)
        + _pb_field_b(20, f20_entry)
        + _pb_field_b(21, model_display)
    )
    packed_steps = b"".join(_pb_varint(s) for s in step_indices)
    blob = _pb_field_b(1, f1) + _pb_field_b(2, packed_steps) + _pb_field_b(4, exec_id)
    return (idx, blob)


def build_synthetic_env(tmp_dir: Path) -> tuple[Path, Path, Path, dt.datetime]:
    agy = tmp_dir / "agy"
    convs = agy / "conversations"
    convs.mkdir(parents=True)
    proot = tmp_dir / "projects"
    proot.mkdir(parents=True)
    cal = tmp_dir / "usage_calibration.yaml"

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    t_main1 = now - dt.timedelta(minutes=50)
    t_main2 = now - dt.timedelta(minutes=25)
    t_sub = now - dt.timedelta(minutes=45)
    t_argon = now - dt.timedelta(minutes=15)

    # conversation_summaries.db
    sdb = sqlite3.connect(agy / "conversation_summaries.db")
    sdb.execute(
        "CREATE TABLE conversation_summaries ("
        "conversation_id TEXT PRIMARY KEY, title TEXT, step_count INT, "
        "last_modified_time TEXT, workspace_uris TEXT, parent_conversation_id TEXT, project_id TEXT)"
    )
    sdb.execute(
        "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)",
        ("conv-main-1", "Halter bauen", 6, t_main2.isoformat(), json.dumps(["file:///c:/ws/slicetheprompt"]), "", ""),
    )
    sdb.execute(
        "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)",
        ("conv-sub-1", "Research Subagent", 2, t_sub.isoformat(), json.dumps(["file:///c:/ws/slicetheprompt"]), "conv-main-1", ""),
    )
    sdb.execute(
        "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)",
        ("conv-other-ws", "Quant Backtest", 2, t_argon.isoformat(), json.dumps(["file:///c:/ws/FinObsid_V2"]), "", ""),
    )
    sdb.commit()
    sdb.close()

    # Main conversation DB: 2 turns, 1st turn spawns subagent + creates CAD project versions
    mdb = sqlite3.connect(convs / "conv-main-1.db")
    mdb.execute("CREATE TABLE steps (idx INT, step_type INT, status INT, metadata BLOB, step_payload BLOB)")
    mdb.execute("CREATE TABLE gen_metadata (idx INT, data BLOB)")
    mdb.executemany(
        "INSERT INTO steps VALUES (?,?,?,?,?)",
        [
            _make_step_row(0, 14, "exec-1", t_main1, user_text="Erstelle Adapter 2026-11-13-test-adapter"),
            _make_step_row(1, 132, "exec-1", t_main1 + dt.timedelta(seconds=20), tool_name="call_mcp_tool", tool_args={"project": "2026-11-13-test-adapter"}),
            _make_step_row(2, 132, "exec-1", t_main1 + dt.timedelta(seconds=40), tool_name="invoke_subagent", tool_args={"conversationId": "conv-sub-1"}),
            _make_step_row(3, 14, "exec-2", t_main2, user_text="Wandstärke erhöhen"),
            _make_step_row(4, 132, "exec-2", t_main2 + dt.timedelta(seconds=15), tool_name="call_mcp_tool", tool_args={"project": "2026-11-13-test-adapter"}),
        ],
    )
    mdb.executemany(
        "INSERT INTO gen_metadata VALUES (?,?)",
        [
            _make_gen_meta_row(
                0, "exec-1", [0, 1],
                "MODEL_PLACEHOLDER_M526", "gemini-4-hydrogen", "Gemini 4 Hydrogen (Medium)",
                fresh_in=4000, cache_read=12000, output_total=1500, thinking_tokens=1100, visible_output=400,
            ),
            _make_gen_meta_row(
                1, "exec-1", [1, 2],
                "MODEL_PLACEHOLDER_M526", "gemini-4-hydrogen", "Gemini 4 Hydrogen (Medium)",
                fresh_in=800, cache_read=20000, output_total=1200, thinking_tokens=800, visible_output=400,
            ),
            _make_gen_meta_row(
                2, "exec-2", [3, 4],
                "MODEL_PLACEHOLDER_M401", "claude-opus-5-5-medium", "Claude Opus 5.5 (Medium)",
                fresh_in=15000, cache_read=0, output_total=2000, thinking_tokens=1300, visible_output=700,
            ),
        ],
    )
    mdb.commit()
    mdb.close()

    # Subagent DB (Gemini 3.8 Flash)
    subdb = sqlite3.connect(convs / "conv-sub-1.db")
    subdb.execute("CREATE TABLE steps (idx INT, step_type INT, status INT, metadata BLOB, step_payload BLOB)")
    subdb.execute("CREATE TABLE gen_metadata (idx INT, data BLOB)")
    subdb.execute(
        "INSERT INTO steps VALUES (?,?,?,?,?)",
        _make_step_row(0, 14, "exec-sub", t_sub, user_text="Subagent search"),
    )
    subdb.execute(
        "INSERT INTO gen_metadata VALUES (?,?)",
        _make_gen_meta_row(
            0, "exec-sub", [0],
            "MODEL_PLACEHOLDER_M318", "gemini-3.8-flash-n", "Gemini 3.8 Flash (High)",
            fresh_in=6000, cache_read=10000, output_total=1000, thinking_tokens=700, visible_output=300,
        ),
    )
    subdb.commit()
    subdb.close()

    # Other Workspace DB (Gemini 4 Argon - Weekly only!)
    odb = sqlite3.connect(convs / "conv-other-ws.db")
    odb.execute("CREATE TABLE steps (idx INT, step_type INT, status INT, metadata BLOB, step_payload BLOB)")
    odb.execute("CREATE TABLE gen_metadata (idx INT, data BLOB)")
    odb.execute(
        "INSERT INTO steps VALUES (?,?,?,?,?)",
        _make_step_row(0, 14, "exec-arg", t_argon, user_text="Backtest optimieren"),
    )
    odb.execute(
        "INSERT INTO gen_metadata VALUES (?,?)",
        _make_gen_meta_row(
            0, "exec-arg", [0],
            "MODEL_PLACEHOLDER_M418", "gemini-4-argon-trial", "Gemini 4 Argon (High)",
            fresh_in=5000, cache_read=25000, output_total=1800, thinking_tokens=1400, visible_output=400,
        ),
    )
    odb.commit()
    odb.close()

    # CAD Project + history v001 (in Turn 1) & v002 (in Turn 2) mit lokaler naiver Zeit!
    pdir = proot / "2026-11-13-test-adapter"
    (pdir / "history" / "v001").mkdir(parents=True)
    (pdir / "history" / "v002").mkdir(parents=True)
    local_v1 = (t_main1 + dt.timedelta(minutes=3)).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    local_v2 = (t_main2 + dt.timedelta(minutes=2)).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    (pdir / "history" / "v001" / "meta.json").write_text(
        json.dumps({"version": "v001", "number": 1, "timestamp": local_v1, "passed": False, "errors_count": 2, "size_mm": [20, 20, 10]}),
        encoding="utf-8",
    )
    (pdir / "history" / "v001" / "print.stl").write_bytes(b"solid v1\nendsolid v1\n")
    (pdir / "history" / "v002" / "meta.json").write_text(
        json.dumps({"version": "v002", "number": 2, "timestamp": local_v2, "passed": True, "errors_count": 0, "size_mm": [22, 22, 10]}),
        encoding="utf-8",
    )
    (pdir / "history" / "v002" / "print.stl").write_bytes(b"solid v2\nendsolid v2\n")

    US.clear_cache()
    return agy, proot, cal, now


def test_model_resolution_and_overrides():
    # Bug-Fix-Prüfung: Punkte in Gemini 3.8 dürfen nicht abgeschnitten werden
    flash = UE.resolve_model_spec("Gemini 3.8 Flash (High)", "gemini-3.8-flash-n", "MODEL_PLACEHOLDER_M318")
    assert flash.key == "flash" and flash.pool_id == "gemini"

    argon = UE.resolve_model_spec("Gemini 4 Argon (High)", "", "MODEL_PLACEHOLDER_M418")
    assert argon.key == "argon" and argon.pool_id == "argon"

    claude = UE.resolve_model_spec("Claude Opus 5.5 (Medium)", "", "MODEL_PLACEHOLDER_M401")
    assert claude.key == "claude_opus" and claude.pool_id == "claude_gpt"

    # Zukunfts-Modell über custom_models
    custom = {
        "gemini-5-nova": {
            "display_name": "Gemini 5 Nova",
            "pool_id": "gemini",
            "input_per_1m": 3.0,
            "output_per_1m": 18.0,
        }
    }
    nova = UE.resolve_model_spec("Gemini 5 Nova (High)", "gemini-5-nova", "", custom_overrides=custom)
    assert nova.key == "gemini-5-nova" and nova.input_usd_per_m == 3.0


def test_parse_resets_in():
    assert UE.parse_resets_in("1d 6h") == 30.0
    assert abs((UE.parse_resets_in("3h 30m") or 0) - 3.5) < 1e-6
    assert UE.parse_resets_in("45m") == 0.75
    assert UE.parse_resets_in("") is None


def test_subagent_rollup_cost_split_and_versions():
    with tempfile.TemporaryDirectory() as td:
        agy, proot, cal, now = build_synthetic_env(Path(td))
        det = UE.build_project_detail("2026-11-13-test-adapter", agy_dir=agy, projects_root=proot, cal_path=cal)
        assert det["ok"] is True
        assert len(det["turns"]) == 2
        assert det["unmatched_versions"] == []

        t1, t2 = det["turns"]
        # Turn 1 muss v001 und Subagent conv-sub-1 enthalten
        assert [v["version"] for v in t1["versions"]] == ["v001"]
        assert t1["subagent_calls"] == 1
        assert t1["subagent_ids"] == ["conv-sub-1"]

        # Turn 2 wechselte auf Claude Opus ohne Cache -> Cache-Break erkannt + v002 gematcht
        assert [v["version"] for v in t2["versions"]] == ["v002"]
        assert t2["pool_id"] == "claude_gpt"
        assert t2["cache_break"] is True

        # Prompt + Answer == Total (CU, USD, CHF, Tokens, Wh, ml)
        for t in (t1, t2):
            p, a, tot = t["prompt_cost"], t["answer_cost"], t["total_cost"]
            assert abs((p["cu"] + a["cu"]) - tot["cu"]) < 0.05
            assert abs((p["cost_usd"] + a["cost_usd"]) - tot["cost_usd"]) < 0.0005
            assert p["total_tokens"] + a["total_tokens"] == tot["total_tokens"]
            assert tot["energy_wh"] > 0 and tot["water_ml"] > 0


def test_three_pools_calibration_and_eco_settings():
    with tempfile.TemporaryDirectory() as td:
        agy, proot, cal, now = build_synthetic_env(Path(td))

        # Multi-Pool Kalibrierung exakt wie Screenshot (Gemini 5h+Weekly, Claude 5h+Weekly, Argon Weekly only)
        UE.calibrate_pool_snapshot("gemini", "weekly", pct_left=64.0, resets_in="1d 6h", agy_dir=agy, cal_path=cal, now_utc=now)
        UE.calibrate_pool_snapshot("gemini", "5h", pct_left=90.0, resets_in="3h 9m", agy_dir=agy, cal_path=cal, now_utc=now)
        UE.calibrate_pool_snapshot("claude_gpt", "weekly", pct_left=21.0, resets_in="2d 5h", agy_dir=agy, cal_path=cal, now_utc=now)
        UE.calibrate_pool_snapshot("argon", "weekly", pct_left=4.0, resets_in="2d 3h", agy_dir=agy, cal_path=cal, now_utc=now)

        UE.update_settings({"show_eco_metrics": True, "currency": "USD"}, cal_path=cal)

        ov = UE.build_usage_overview(days=7.0, agy_dir=agy, projects_root=proot, cal_path=cal, now_utc=now)
        assert ov["settings"]["show_eco_metrics"] is True
        assert ov["settings"]["currency"] == "USD"

        assert ov["pools"]["gemini"]["weekly"]["pct_left"] == 64.0
        assert ov["pools"]["gemini"]["h5"]["pct_left"] == 90.0
        assert ov["pools"]["claude_gpt"]["weekly"]["pct_left"] == 21.0
        assert ov["pools"]["argon"]["h5"] is None
        assert ov["pools"]["argon"]["weekly"]["pct_left"] == 4.0

        # Account-weit über alle Workspaces: FinObsid_V2 + slicetheprompt beide vorhanden
        ws_names = {w["workspace"] for w in ov["workspaces"]}
        assert "slicetheprompt" in ws_names and "FinObsid_V2" in ws_names
        assert len(ov["fading_plot"]["series_by_pool"]["argon"]) >= 1


def test_ring_history_and_metadata_cap():
    with tempfile.TemporaryDirectory() as td:
        agy, proot, cal, now = build_synthetic_env(Path(td))
        # Seed > MAX_RING_HISTORY Einträge über yaml -> cap auf MAX_RING_HISTORY prüfen
        cfg = UE.load_calibration(cal)
        seed_n = UE.MAX_RING_HISTORY + 4
        cfg["pools"]["gemini"]["ring_history"] = [
            {"ts": now.isoformat(), "horizon": "weekly", "pct_left": 50.0, "ring_pct": 50.0, "ring_color": "green", "source": "ocr+ring"}
            for _ in range(seed_n)
        ]
        UE.save_calibration(cfg, cal)

        UE.calibrate_pool_snapshot(
            "gemini", "weekly",
            pct_left=61.0, resets_in="1d 5h",
            agy_dir=agy, cal_path=cal, now_utc=now,
            ring_pct=60.6, ring_color="green", ocr_pct=61.0, source="ocr+ring",
        )
        cfg2 = UE.load_calibration(cal)
        hist = cfg2["pools"]["gemini"]["ring_history"]
        assert len(hist) == UE.MAX_RING_HISTORY
        assert hist[-1]["ring_pct"] == 60.6
        assert hist[-1]["ocr_ring_diff"] == 0.4

        ov = UE.build_usage_overview(days=7.0, agy_dir=agy, projects_root=proot, cal_path=cal, now_utc=now)
        rt = ov["pools"]["gemini"]["ring_tracking"]
        assert rt is not None
        assert rt["ring_pct"] == 60.6
        assert rt["ring_color"] == "green"
        assert rt["diff_pct"] == 0.4
        assert ov["pools"]["gemini"]["data_points"]["weekly"] == UE.MAX_RING_HISTORY
        assert ov["data_points_summary"]["total_points"] >= UE.MAX_RING_HISTORY


def test_data_points_intervals_and_burn_rate():
    with tempfile.TemporaryDirectory() as td:
        agy, proot, cal, now = build_synthetic_env(Path(td))
        # t1: vor den synthetischen Gemini-Requests in build_synthetic_env (now-40m bis now-10m)
        t1 = now - dt.timedelta(minutes=50)
        t2 = now
        UE.calibrate_pool_snapshot(
            "gemini", "weekly",
            pct_left=80.0, resets_in="2d 0h",
            agy_dir=agy, cal_path=cal, now_utc=t1,
            ring_pct=80.0, ring_color="green", ocr_pct=80.0, source="ocr+ring",
        )
        UE.calibrate_pool_snapshot(
            "gemini", "weekly",
            pct_left=74.0, resets_in="1d 23h",
            agy_dir=agy, cal_path=cal, now_utc=t2,
            ring_pct=74.0, ring_color="green", ocr_pct=74.0, source="ocr+ring",
        )
        # Manueller 5h-Snapshot zählt ebenfalls als Datenpunkt
        UE.calibrate_pool_snapshot(
            "gemini", "5h",
            pct_left=92.0, resets_in="4h 0m",
            agy_dir=agy, cal_path=cal, now_utc=t2,
        )

        ov = UE.build_usage_overview(days=7.0, agy_dir=agy, projects_root=proot, cal_path=cal, now_utc=now)
        dp = ov["pools"]["gemini"]["data_points"]
        assert dp["weekly"] == 2
        assert dp["h5"] == 1
        assert dp["total"] == 3
        assert dp["intervals_weekly"] == 1
        assert dp["intervals_5h"] == 0
        assert dp["intervals_total"] == 1
        assert dp["burn_rate"]["weekly_pct_per_1k_cu"] is not None
        assert dp["burn_rate"]["weekly_pct_per_1k_cu"] > 0
        assert ov["data_points_summary"]["total_points"] == 3
        assert ov["data_points_summary"]["total_intervals"] == 1
        assert ov["active_model"] is not None
        assert ov["active_model"]["effort"] in ("Medium", "High", "Low", "XHigh")

        assert UE.extract_model_effort("Gemini 4 Hydrogen (XHigh)", "", "") == "XHigh"
        assert UE.extract_model_effort("", "", "MODEL_PLACEHOLDER_M528") == "XHigh"
        assert UE.extract_model_effort("", "", "MODEL_PLACEHOLDER_M318") == "High"


def test_model_effort_burn_matrix_prior_and_ridge():
    with tempfile.TemporaryDirectory() as td:
        agy, proot, cal, now = build_synthetic_env(Path(td))
        # Ohne Intervalle: Prior-Werte nach Modell + Effort
        ov0 = UE.build_usage_overview(days=7.0, agy_dir=agy, projects_root=proot, cal_path=cal, now_utc=now)
        bm0 = ov0["burn_matrix"]
        assert len(bm0) >= 2
        keys0 = {(r["model_key"], r["effort_label"]) for r in bm0}
        assert ("hydrogen", "Medium") in keys0
        assert all(r["weekly"]["source"] == "prior" for r in bm0 if r.get("weekly"))

        # Mit Intervall [t1, t2] für Gemini: Wechsel auf empirical + angepasster Faktor
        t1 = now - dt.timedelta(minutes=50)
        t2 = now
        UE.calibrate_pool_snapshot(
            "gemini", "weekly",
            pct_left=85.0, resets_in="2d 0h",
            agy_dir=agy, cal_path=cal, now_utc=t1,
            ring_pct=85.0, ring_color="green", ocr_pct=85.0, source="ocr+ring",
        )
        UE.calibrate_pool_snapshot(
            "gemini", "weekly",
            pct_left=75.0, resets_in="1d 23h",
            agy_dir=agy, cal_path=cal, now_utc=t2,
            ring_pct=75.0, ring_color="green", ocr_pct=75.0, source="ocr+ring",
        )
        ov1 = UE.build_usage_overview(days=7.0, agy_dir=agy, projects_root=proot, cal_path=cal, now_utc=now)
        bm1 = ov1["burn_matrix"]
        gem_rows = [r for r in bm1 if r["pool_id"] == "gemini"]
        assert any(r["weekly"]["source"] == "empirical" and r["weekly"]["empirical_intervals"] >= 1 for r in gem_rows)
        for r in gem_rows:
            wk = r["weekly"]
            assert wk["pct_per_turn"] > 0
            assert wk["pct_per_1m_tokens"] > 0
            assert wk["est_turns_left"] is not None and wk["est_turns_left"] > 0
            assert 0.25 <= wk["calibration_factor"] <= 4.0


def run_all() -> None:
    tests = [
        test_model_resolution_and_overrides,
        test_parse_resets_in,
        test_subagent_rollup_cost_split_and_versions,
        test_three_pools_calibration_and_eco_settings,
        test_ring_history_and_metadata_cap,
        test_data_points_intervals_and_burn_rate,
        test_model_effort_burn_matrix_prior_and_ridge,
    ]
    for fn in tests:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"Alle {len(tests)} usage_eval-Tests bestanden.")


if __name__ == "__main__":
    run_all()
