"""Tests für die Open WebUI ReAct-Pipe (scripts/openwebui_gemini_react_pipe.py)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_pipe_module():
    if "google.genai" not in sys.modules:
        google_mod = sys.modules.setdefault("google", types.ModuleType("google"))
        genai_mod = types.ModuleType("google.genai")
        genai_mod.Client = MagicMock()
        google_mod.genai = genai_mod
        sys.modules["google.genai"] = genai_mod
    if "httpx" not in sys.modules:
        httpx_mod = types.ModuleType("httpx")
        httpx_mod.Response = MagicMock()
        httpx_mod.AsyncClient = MagicMock()
        sys.modules["httpx"] = httpx_mod

    path = Path(__file__).resolve().parents[1] / "scripts" / "openwebui_gemini_react_pipe.py"
    spec = importlib.util.spec_from_file_location("openwebui_gemini_react_pipe", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _step(step_type: str, **kwargs):
    return types.SimpleNamespace(type=step_type, **kwargs)


def _interaction(iid: str, steps=None, output_text: str | None = None):
    return types.SimpleNamespace(id=iid, steps=steps or [], output_text=output_text)


class TestOpenWebUIReactPipe(unittest.TestCase):
    def test_sanitize_schema_and_extract_messages(self):
        mod = _load_pipe_module()
        pipe = mod.Pipe()

        raw = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Args",
            "type": "object",
            "additionalProperties": False,
            "properties": {"name": {"type": "string", "default": "cube"}},
        }
        cleaned = pipe._sanitize_schema(raw)
        self.assertNotIn("$schema", cleaned)
        self.assertNotIn("additionalProperties", cleaned)
        self.assertNotIn("title", cleaned)
        self.assertNotIn("default", cleaned["properties"]["name"])
        self.assertEqual(cleaned["properties"]["name"]["type"], "string")

        sys_txt, user_txt = pipe._extract_system_and_input([
            {"role": "system", "content": "Custom CAD Prompt"},
            {"role": "user", "content": "Bau mir einen Würfel"},
        ])
        self.assertEqual(sys_txt, "Custom CAD Prompt")
        self.assertEqual(user_txt, "Bau mir einen Würfel")

    def test_parse_mcp_sse_and_json(self):
        mod = _load_pipe_module()
        sse_resp = types.SimpleNamespace(
            headers={"content-type": "text/event-stream"},
            text='event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"project_new"}]}}\n\n',
        )
        parsed = mod.Pipe._parse_mcp_response(sse_resp)
        self.assertEqual(parsed["result"]["tools"][0]["name"], "project_new")

        json_resp = types.SimpleNamespace(
            headers={"content-type": "application/json"},
            text='{"jsonrpc": "2.0", "id": 2, "result": {"ok": true}}',
            json=lambda: {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}},
        )
        self.assertTrue(mod.Pipe._parse_mcp_response(json_resp)["result"]["ok"])

    def test_react_self_healing_multi_turn(self):
        mod = _load_pipe_module()
        pipe = mod.Pipe()
        pipe.valves.GEMINI_API_KEY = "test-key"
        pipe.valves.MIN_DELAY_SECONDS = 0.0

        calls_made = []
        interactions = [
            _interaction("int-1", steps=[_step("function_call", id="c1", name="project_new", arguments={"name": "box", "code": "bad"})]),
            _interaction("int-2", steps=[_step("function_call", id="c2", name="build_and_check", arguments={"project": "box", "code": "good"})]),
            _interaction("int-3", steps=[], output_text="Würfel erfolgreich gebaut (20x20x20 mm)."),
        ]

        class FakeInteractions:
            def create(self, **kwargs):
                calls_made.append(kwargs)
                return interactions.pop(0)

        fake_client = types.SimpleNamespace(interactions=FakeInteractions())

        async def fake_project_new(**kwargs):
            return {"ok": False, "error": "SyntaxError in model.py"}

        async def fake_build_and_check(**kwargs):
            return {"stage": "done", "report": {"passed": True, "size_mm": [20, 20, 20]}}

        tools = {
            "project_new": {"callable": fake_project_new, "spec": {"name": "project_new", "description": "new", "parameters": {"type": "object", "properties": {}}}},
            "build_and_check": {"callable": fake_build_and_check, "spec": {"name": "build_and_check", "description": "build", "parameters": {"type": "object", "properties": {}}}},
        }

        statuses = []

        async def emitter(ev):
            statuses.append(ev["data"]["description"])

        with patch.object(mod.genai, "Client", return_value=fake_client):
            out = asyncio.run(pipe.pipe({"messages": [{"role": "user", "content": "20mm Würfel"}]}, __tools__=tools, __event_emitter__=emitter))

        self.assertIn("Würfel erfolgreich gebaut", out)
        self.assertEqual(len(calls_made), 3)
        self.assertEqual(calls_made[1]["previous_interaction_id"], "int-1")
        self.assertEqual(calls_made[2]["previous_interaction_id"], "int-2")
        self.assertEqual(calls_made[1]["input"][0]["type"], "function_result")
        self.assertEqual(calls_made[1]["input"][0]["call_id"], "c1")
        self.assertGreaterEqual(len(statuses), 3)

    def test_failsafe_duplicate_call_breaker(self):
        mod = _load_pipe_module()
        pipe = mod.Pipe()
        pipe.valves.GEMINI_API_KEY = "test-key"
        pipe.valves.MIN_DELAY_SECONDS = 0.0

        class FakeInteractions:
            def create(self, **kwargs):
                return _interaction("int-x", steps=[_step("function_call", id="c1", name="project_new", arguments={"name": "box"})])

        async def fake_tool(**kwargs):
            return {"ok": False, "error": "kaputt"}

        tools = {"project_new": {"callable": fake_tool, "spec": {"name": "project_new", "parameters": {"type": "object", "properties": {}}}}}
        with patch.object(mod.genai, "Client", return_value=types.SimpleNamespace(interactions=FakeInteractions())):
            out = asyncio.run(pipe.pipe({"messages": [{"role": "user", "content": "Test"}]}, __tools__=tools))

        self.assertIn("ReAct-Failsafe ausgelöst", out)
        self.assertIn("identischen Argumenten", out)

    def test_failsafe_circuit_breaker_consecutive_errors(self):
        mod = _load_pipe_module()
        pipe = mod.Pipe()
        pipe.valves.GEMINI_API_KEY = "test-key"
        pipe.valves.MIN_DELAY_SECONDS = 0.0
        pipe.valves.MAX_CONSECUTIVE_ERRORS = 2
        pipe.valves.MAX_ITERATIONS = 5

        idx = {"n": 0}

        class FakeInteractions:
            def create(self, **kwargs):
                idx["n"] += 1
                return _interaction(
                    f"int-{idx['n']}",
                    steps=[_step("function_call", id=f"c{idx['n']}", name="project_new", arguments={"name": f"box-{idx['n']}"})],
                )

        async def fake_tool(**kwargs):
            return {"ok": False, "error": "CAD Fehler"}

        tools = {"project_new": {"callable": fake_tool, "spec": {"name": "project_new", "parameters": {"type": "object", "properties": {}}}}}
        with patch.object(mod.genai, "Client", return_value=types.SimpleNamespace(interactions=FakeInteractions())):
            out = asyncio.run(pipe.pipe({"messages": [{"role": "user", "content": "Test"}]}, __tools__=tools))

        self.assertIn("Circuit-Breaker", out)
        self.assertIn("2 Tool-Fehler in Folge", out)

    def test_failsafe_rate_limit_backoff(self):
        mod = _load_pipe_module()
        pipe = mod.Pipe()
        pipe.valves.GEMINI_API_KEY = "test-key"
        pipe.valves.MIN_DELAY_SECONDS = 0.0
        pipe.valves.MAX_429_RETRIES = 2

        attempts = {"n": 0}

        class FakeInteractions:
            def create(self, **kwargs):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED: Quota exceeded")
                return _interaction("int-ok", steps=[], output_text="Erfolg nach Retry")

        async def fast_sleep(_):
            return None

        tools = {"project_new": {"callable": lambda **k: None, "spec": {"name": "project_new", "parameters": {"type": "object", "properties": {}}}}}
        with patch.object(mod.genai, "Client", return_value=types.SimpleNamespace(interactions=FakeInteractions())), \
             patch.object(mod.asyncio, "sleep", side_effect=fast_sleep):
            out = asyncio.run(pipe.pipe({"messages": [{"role": "user", "content": "Test"}]}, __tools__=tools))

        self.assertEqual(out, "Erfolg nach Retry")
        self.assertEqual(attempts["n"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
