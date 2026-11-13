"""Tests für die vereinfachte API-Token-Log-Auswertung (Android Usage-Tab)."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from slicetheprompt import token_usage
from slicetheprompt.ui_server import StudioHandler


class TestTokenUsage(unittest.TestCase):
    def test_missing_file_returns_empty_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "missing.jsonl"
            res = token_usage.load_usage_summary(log_path=p)
            self.assertTrue(res["ok"])
            self.assertFalse(res["exists"])
            self.assertEqual(res["total_entries"], 0)
            self.assertEqual(res["periods"]["all"]["totals"]["requests"], 0)
            self.assertEqual(res["periods"]["all"]["totals"]["total_tokens"], 0)

    def test_aggregates_periods_models_daily_and_skips_invalid_lines(self) -> None:
        now = dt.datetime(2026, 11, 13, 14, 0, 0, tzinfo=dt.timezone.utc)
        rows = [
            # Heute
            {
                "ts": "2026-11-13T10:00:00+00:00",
                "user": "tim",
                "model": "gemini-3.8-flash",
                "steps": 3,
                "status": "ok",
                "duration_s": 5.5,
                "input_tokens": 1000,
                "output_tokens": 200,
                "thought_tokens": 50,
                "total_tokens": 1250,
                "cost_usd": 0.001,
            },
            # Heute, Fehler + fehlendes total_tokens (Fallback-Summe)
            {
                "ts": "2026-11-13T11:30:00Z",
                "model": "gemini-4-argon",
                "steps": 2,
                "status": "api_error",
                "duration_s": 2.5,
                "input_tokens": 400,
                "output_tokens": 100,
                "thought_tokens": 0,
                "cost_usd": 0.0005,
            },
            # Vor 3 Tagen (in 7 Tage, nicht heute)
            {
                "ts": "2026-11-10T09:00:00+00:00",
                "model": "gemini-3.8-flash",
                "steps": 4,
                "status": "ok",
                "duration_s": 8.0,
                "input_tokens": 2000,
                "output_tokens": 500,
                "thought_tokens": 100,
                "total_tokens": 2600,
                "cost_usd": 0.002,
            },
            # Vor 20 Tagen (nur in Gesamt)
            {
                "ts": "2026-10-24T12:00:00+00:00",
                "model": "gemini-3.8-flash",
                "steps": 1,
                "status": "ok",
                "duration_s": 2.0,
                "input_tokens": 300,
                "output_tokens": 100,
                "thought_tokens": 0,
                "total_tokens": 400,
                "cost_usd": 0.0002,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "token_usage.jsonl"
            content = "\n".join([
                json.dumps(rows[0]),
                "   ",
                "{kaputt json",
                json.dumps(rows[1]),
                json.dumps(rows[2]),
                json.dumps(rows[3]),
            ])
            p.write_text(content, encoding="utf-8")

            res = token_usage.load_usage_summary(log_path=p, now=now)
            self.assertTrue(res["exists"])
            self.assertEqual(res["total_entries"], 4)

            today = res["periods"]["today"]
            self.assertEqual(today["totals"]["requests"], 2)
            self.assertEqual(today["totals"]["ok_requests"], 1)
            self.assertEqual(today["totals"]["error_requests"], 1)
            self.assertEqual(today["totals"]["total_tokens"], 1750)
            self.assertEqual(today["totals"]["thought_tokens"], 50)
            self.assertEqual(today["totals"]["total_steps"], 5)
            self.assertAlmostEqual(today["totals"]["cost_usd"], 0.0015, places=6)
            # Neueste Anfrage zuerst
            self.assertEqual(today["recent"][0]["model"], "gemini-4-argon")

            week = res["periods"]["week"]
            self.assertEqual(week["totals"]["requests"], 3)
            self.assertEqual(week["totals"]["total_tokens"], 4350)
            self.assertEqual([d["date"] for d in week["daily"]], ["2026-11-10", "2026-11-13"])

            all_p = res["periods"]["all"]
            self.assertEqual(all_p["totals"]["requests"], 4)
            self.assertEqual(all_p["totals"]["total_tokens"], 4750)
            self.assertEqual(all_p["by_model"][0]["model"], "gemini-3.8-flash")
            self.assertEqual(all_p["by_model"][0]["requests"], 3)
            self.assertEqual(all_p["by_model"][1]["model"], "gemini-4-argon")

    def test_http_api_usage_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "token_usage.jsonl"
            log_path.write_text(
                json.dumps({
                    "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    "model": "gemini-3.8-flash",
                    "steps": 2,
                    "status": "ok",
                    "duration_s": 3.2,
                    "input_tokens": 500,
                    "output_tokens": 150,
                    "thought_tokens": 25,
                    "total_tokens": 675,
                    "cost_usd": 0.0005,
                }) + "\n",
                encoding="utf-8",
            )
            srv = ThreadingHTTPServer(("127.0.0.1", 0), StudioHandler)
            srv.projects_root = Path(tmp)
            srv.token_log_path = log_path
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                port = srv.server_address[1]
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/usage", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    data = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(data["ok"])
                self.assertEqual(data["total_entries"], 1)
                self.assertEqual(data["periods"]["today"]["totals"]["total_tokens"], 675)
            finally:
                srv.shutdown()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()
