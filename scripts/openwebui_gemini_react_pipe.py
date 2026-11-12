"""
title: SliceThePrompt Gemini ReAct Agent
author: slicetheprompt
version: 1.0.0
description: Multi-Turn ReAct Agent Pipe für Open WebUI mit Google GenAI SDK (>= 2.30.1) und direkter MCP-Anbindung inkl. Failsafes.
"""
# Benötigte Pakete im Open WebUI venv: google-genai>=2.30.1, httpx>=0.27.0, pydantic>=2.0.0

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from google import genai
from pydantic import BaseModel, Field

DEFAULT_SYSTEM_PROMPT = """\
Du bist ein präziser CAD-Konstrukteur für 3D-Druckteile mit build123d.
Wenn der Nutzer ein Teil anfordert:
1. Schreibe sauberen build123d-Code (MUSS `part` oder `bodies` definieren, z=0 auf Druckbett):
   ```python
   from build123d import *
   from lib.parts import *
   set_material("pla")
   C = (Align.CENTER, Align.CENTER, Align.MIN)
   part = Box(20, 20, 20, align=C)
   part = bottom_chamfer(part)
   bb = part.bounding_box()
   checks = {"Breite": (bb.size.X, 20.0, 0.05)}
   ```
2. Rufe direkt `project_new(name="...", material="pla", code="...")` auf.
3. Tritt beim Bauen oder Prüfen ein Fehler auf, analysiere die Fehlermeldung, korrigiere den Code und rufe `build_and_check(project="...", code="...")` bzw. `project_new` erneut auf.
4. Berichte am Ende wahrheitsgetreu über Maße, bestandene Checks sowie Warnungen und verweise auf das 3D-Studio (http://127.0.0.1:8765).
"""


class Pipe:
    """Open WebUI Pipe mit echtem Multi-Turn-ReAct-Loop über die Google Interactions API."""

    class Valves(BaseModel):
        GEMINI_API_KEY: str = Field(
            default="",
            description="Google Gemini API Key (leer = nutzt GEMINI_API_KEY / GOOGLE_API_KEY aus ENV).",
        )
        GEMINI_MODEL: str = Field(
            default="gemini-3.8-flash",
            description="Gemini-Modell-ID (z. B. gemini-3.8-flash, gemini-4-argon, gemini-3.5-flash-lite).",
        )
        MCP_URL: str = Field(
            default="http://127.0.0.1:8766/mcp",
            description="Fallback-URL zum SliceThePrompt MCP Streamable-HTTP Server, falls Open WebUI keine __tools__ übergibt.",
        )
        MAX_ITERATIONS: int = Field(
            default=8,
            ge=1,
            le=15,
            description="Maximale ReAct-Schritte pro Nachricht (Failsafe gegen Endlos-Loops).",
        )
        MAX_CONSECUTIVE_ERRORS: int = Field(
            default=3,
            ge=1,
            le=10,
            description="Abbruch nach N aufeinanderfolgenden Tool-Fehlern (Circuit Breaker).",
        )
        MIN_DELAY_SECONDS: float = Field(
            default=0.5,
            ge=0.0,
            le=10.0,
            description="Mindestpause in Sekunden zwischen API-Aufrufen zum Schutz vor Rate-Limits.",
        )
        MAX_429_RETRIES: int = Field(
            default=3,
            ge=0,
            le=6,
            description="Anzahl Wiederholungen mit Exponential Backoff bei HTTP 429 / Quota-Limits.",
        )
        EMIT_STATUS: bool = Field(
            default=True,
            description="Zeigt Live-Statusmeldungen der ReAct-Schritte im Chat an.",
        )
        APPEND_USAGE_FOOTER: bool = Field(
            default=True,
            description="Hängt am Ende jeder Antwort eine kompakte Token- & Kosten-Zusammenfassung an.",
        )
        TOKEN_LOG_PATH: str = Field(
            default="/root/slicetheprompt/out/token_usage.jsonl",
            description="Pfad zur JSONL-Logdatei für kumuliertes Token-Tracking (leer = kein Dateilog).",
        )
        INPUT_PRICE_PER_1M_USD: float = Field(
            default=0.30,
            ge=0.0,
            description="Geschätzter Preis je 1 Mio. Input-Tokens in USD.",
        )
        OUTPUT_PRICE_PER_1M_USD: float = Field(
            default=2.50,
            ge=0.0,
            description="Geschätzter Preis je 1 Mio. Output-Tokens in USD.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def pipes(self) -> list[dict[str, str]]:
        return [{"id": "slicetheprompt-react", "name": f"SliceThePrompt ReAct ({self.valves.GEMINI_MODEL})"}]

    async def _emit(
        self,
        emitter: Callable[[dict[str, Any]], Awaitable[None]] | None,
        description: str,
        done: bool = False,
    ) -> None:
        if emitter and self.valves.EMIT_STATUS:
            try:
                await emitter({"type": "status", "data": {"description": description, "done": done}})
            except Exception:
                pass

    @staticmethod
    def _sanitize_schema(schema: Any) -> Any:
        """Entfernt JSON-Schema-Schlüssel, die von Gemini Function-Declarations abgelehnt werden können."""
        if isinstance(schema, list):
            return [Pipe._sanitize_schema(item) for item in schema]
        if not isinstance(schema, dict):
            return schema
        drop_keys = {"$schema", "additionalProperties", "title", "default"}
        cleaned: dict[str, Any] = {}
        for k, v in schema.items():
            if k in drop_keys:
                continue
            cleaned[k] = Pipe._sanitize_schema(v)
        return cleaned

    @staticmethod
    def _extract_system_and_input(messages: list[dict[str, Any]]) -> tuple[str, str]:
        """Extrahiert System-Prompt und formatiert den Chat-Verlauf als Eingabe für die Interactions API."""
        system_parts: list[str] = []
        turns: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                )
            content = str(content or "").strip()
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                turns.append(f"Nutzer: {content}")
            elif role == "assistant":
                turns.append(f"Assistent: {content}")

        system_instruction = "\n\n".join(system_parts) if system_parts else DEFAULT_SYSTEM_PROMPT
        if len(turns) == 1 and turns[0].startswith("Nutzer: "):
            user_input = turns[0][len("Nutzer: "):]
        else:
            user_input = "\n\n".join(turns) if turns else "Hallo"
        return system_instruction, user_input

    async def _discover_mcp_tools(self) -> tuple[list[dict[str, Any]], dict[str, Callable[..., Awaitable[Any]]], str | None]:
        """Lädt Tools direkt vom MCP Streamable-HTTP Endpunkt (Fallback, wenn __tools__ leer ist)."""
        url = self.valves.MCP_URL.strip()
        if not url:
            return [], {}, None

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        session_id: str | None = None

        async with httpx.AsyncClient(timeout=30.0) as http:
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "openwebui-react-pipe", "version": "1.0.0"},
                },
            }
            r_init = await http.post(url, json=init_payload, headers=headers)
            r_init.raise_for_status()
            session_id = r_init.headers.get("mcp-session-id")
            if session_id:
                headers["mcp-session-id"] = session_id

            notif_payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            await http.post(url, json=notif_payload, headers=headers)

            list_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            r_list = await http.post(url, json=list_payload, headers=headers)
            r_list.raise_for_status()
            data = self._parse_mcp_response(r_list)
            raw_tools = (data.get("result") or {}).get("tools") or []

        tool_decls: list[dict[str, Any]] = []
        callables: dict[str, Callable[..., Awaitable[Any]]] = {}

        for t in raw_tools:
            name = t["name"]
            params = self._sanitize_schema(t.get("inputSchema") or {"type": "object", "properties": {}})
            tool_decls.append({
                "type": "function",
                "name": name,
                "description": t.get("description", ""),
                "parameters": params,
            })
            callables[name] = self._make_mcp_caller(url, name, session_id)

        return tool_decls, callables, session_id

    @staticmethod
    def _parse_mcp_response(resp: httpx.Response) -> dict[str, Any]:
        """Parst sowohl application/json als auch text/event-stream (SSE) Antworten von MCP."""
        ct = resp.headers.get("content-type", "")
        text = resp.text.strip()
        if "text/event-stream" in ct or text.startswith("data:") or text.startswith("event:"):
            last_json: dict[str, Any] = {}
            for line in text.splitlines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload:
                        try:
                            last_json = json.loads(payload)
                        except json.JSONDecodeError:
                            pass
            return last_json
        return resp.json()

    def _make_mcp_caller(self, url: str, tool_name: str, session_id: str | None) -> Callable[..., Awaitable[Any]]:
        async def _call(**kwargs: Any) -> Any:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if session_id:
                headers["mcp-session-id"] = session_id
            payload = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000) % 1000000,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": kwargs},
            }
            async with httpx.AsyncClient(timeout=300.0) as http:
                r = await http.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = self._parse_mcp_response(r)
            if "error" in data:
                return {"ok": False, "error": data["error"]}
            result = data.get("result") or {}
            content = result.get("content") or []
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            joined = "\n".join(texts).strip()
            if joined:
                try:
                    return json.loads(joined)
                except json.JSONDecodeError:
                    return {"output": joined, "isError": bool(result.get("isError"))}
            return result

        return _call

    def _prepare_tools(self, owui_tools: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Callable[..., Awaitable[Any]]]]:
        """Extrahiert Tools aus Open WebUIs __tools__."""
        if not owui_tools or not isinstance(owui_tools, dict):
            return [], {}
        decls: list[dict[str, Any]] = []
        callables: dict[str, Callable[..., Awaitable[Any]]] = {}
        for key, entry in owui_tools.items():
            if not isinstance(entry, dict) or "callable" not in entry:
                continue
            spec = entry.get("spec") or {}
            fn_name = spec.get("name") or key
            decls.append({
                "type": "function",
                "name": fn_name,
                "description": spec.get("description", ""),
                "parameters": self._sanitize_schema(spec.get("parameters") or {"type": "object", "properties": {}}),
            })
            callables[fn_name] = entry["callable"]
        return decls, callables

    async def _create_interaction_with_backoff(self, client: genai.Client, **kwargs: Any) -> Any:
        """Ruft client.interactions.create mit Exponential Backoff bei HTTP 429 auf."""
        delay = 1.5
        for attempt in range(self.valves.MAX_429_RETRIES + 1):
            try:
                return await asyncio.to_thread(client.interactions.create, **kwargs)
            except Exception as exc:
                msg = str(exc).lower()
                is_rate_limit = "429" in msg or "resource_exhausted" in msg or "quota" in msg or "rate" in msg
                if is_rate_limit and attempt < self.valves.MAX_429_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2.0
                    continue
                raise

    @staticmethod
    def _extract_usage(interaction: Any) -> dict[str, int]:
        """Liest Token-Metriken sicher aus einem Interaction-Objekt (verschiedene SDK-Feldnamen)."""
        u = getattr(interaction, "usage", None)
        if u is None:
            return {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "total_tokens": 0}

        def _get(obj: Any, *keys: str) -> int:
            for k in keys:
                val = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
                if val is not None:
                    try:
                        return int(val)
                    except (TypeError, ValueError):
                        pass
            return 0

        inp = _get(u, "input_tokens", "total_input_tokens", "prompt_tokens", "prompt_token_count")
        out = _get(u, "output_tokens", "total_output_tokens", "completion_tokens", "candidates_token_count")
        thought = _get(u, "thought_tokens", "total_thought_tokens", "reasoning_tokens", "thoughts_token_count")
        tot = _get(u, "total_tokens", "total_token_count") or (inp + out + thought)
        return {"input_tokens": inp, "output_tokens": out, "thought_tokens": thought, "total_tokens": tot}

    def _estimate_cost_usd(self, usage: dict[str, int]) -> float:
        return (
            (usage["input_tokens"] / 1_000_000.0) * self.valves.INPUT_PRICE_PER_1M_USD
            + ((usage["output_tokens"] + usage["thought_tokens"]) / 1_000_000.0) * self.valves.OUTPUT_PRICE_PER_1M_USD
        )

    def _format_usage_line(self, usage: dict[str, int], steps: int, duration_s: float) -> str:
        tot = usage["total_tokens"]
        if tot <= 0:
            return ""
        cost = self._estimate_cost_usd(usage)
        thought_part = f" · Thought: {usage['thought_tokens']:,}" if usage["thought_tokens"] > 0 else ""
        return (
            f"📊 Tokens: {tot:,} (In: {usage['input_tokens']:,} · Out: {usage['output_tokens']:,}"
            f"{thought_part} · {steps} Schritte · {duration_s:.1f}s · ~${cost:.4f})"
        ).replace(",", ".")

    def _log_usage(
        self,
        usage: dict[str, int],
        steps: int,
        duration_s: float,
        status: str,
        user: dict[str, Any] | None,
    ) -> None:
        log_path = (self.valves.TOKEN_LOG_PATH or "").strip()
        if not log_path:
            return
        try:
            p = Path(log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "user": (user or {}).get("email") or (user or {}).get("name") or "unknown",
                "model": self.valves.GEMINI_MODEL,
                "steps": steps,
                "status": status,
                "duration_s": round(duration_s, 2),
                **usage,
                "cost_usd": round(self._estimate_cost_usd(usage), 6),
            }
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _is_tool_error(res: Any) -> bool:
        if isinstance(res, dict):
            if res.get("ok") is False or res.get("isError") is True:
                return True
            if res.get("error"):
                return True
            rep = res.get("report")
            if isinstance(rep, dict) and rep.get("passed") is False:
                return True
        return False

    async def pipe(
        self,
        body: dict[str, Any],
        __user__: dict[str, Any] | None = None,
        __tools__: dict[str, Any] | None = None,
        __event_emitter__: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str:
        api_key = (
            self.valves.GEMINI_API_KEY.strip()
            or os.getenv("GEMINI_API_KEY", "").strip()
            or os.getenv("GOOGLE_API_KEY", "").strip()
        )
        if not api_key:
            return "❌ Kein `GEMINI_API_KEY` konfiguriert. Bitte in den Pipe-Valves oder als Umgebungsvariable hinterlegen."

        client = genai.Client(api_key=api_key)
        system_instruction, user_input = self._extract_system_and_input(body.get("messages") or [])

        tool_decls, tool_callables = self._prepare_tools(__tools__)
        if not tool_decls:
            try:
                await self._emit(__event_emitter__, "Verbinde mit SliceThePrompt MCP-Server…")
                tool_decls, tool_callables, _ = await self._discover_mcp_tools()
            except Exception as exc:
                await self._emit(__event_emitter__, f"MCP-Verbindung fehlgeschlagen: {exc}", done=True)
                return f"❌ Konnte keine Werkzeuge laden (__tools__ leer und MCP unter `{self.valves.MCP_URL}` nicht erreichbar: {exc})."

        t_start = time.monotonic()
        total_usage = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "total_tokens": 0}
        seen_calls: set[str] = set()
        consecutive_errors = 0
        previous_interaction_id: str | None = None
        next_input: Any = user_input
        last_error_msg = ""
        steps_run = 0

        async def _finish(text: str, status_label: str, status_code: str) -> str:
            dur = time.monotonic() - t_start
            self._log_usage(total_usage, steps_run, dur, status_code, __user__)
            line = self._format_usage_line(total_usage, steps_run, dur)
            await self._emit(__event_emitter__, f"{status_label}{(' · ' + line) if line else ''}", done=True)
            if self.valves.APPEND_USAGE_FOOTER and line:
                return f"{text}\n\n---\n{line}"
            return text

        for iteration in range(1, self.valves.MAX_ITERATIONS + 1):
            steps_run = iteration
            if iteration > 1 and self.valves.MIN_DELAY_SECONDS > 0:
                await asyncio.sleep(self.valves.MIN_DELAY_SECONDS)

            await self._emit(__event_emitter__, f"Denkschritt {iteration} (max. {self.valves.MAX_ITERATIONS})…")

            req_kwargs: dict[str, Any] = {
                "model": self.valves.GEMINI_MODEL,
                "input": next_input,
                "tools": tool_decls,
                "system_instruction": system_instruction,
            }
            if previous_interaction_id:
                req_kwargs["previous_interaction_id"] = previous_interaction_id

            try:
                interaction = await self._create_interaction_with_backoff(client, **req_kwargs)
            except Exception as exc:
                return await _finish(f"❌ Gemini API Fehler in Schritt {iteration}: {exc}", f"API-Fehler: {exc}", "api_error")

            turn_usage = self._extract_usage(interaction)
            for k in total_usage:
                total_usage[k] += turn_usage[k]

            previous_interaction_id = getattr(interaction, "id", None)
            steps = getattr(interaction, "steps", None) or []
            function_calls = [s for s in steps if getattr(s, "type", None) == "function_call"]

            if not function_calls:
                out_text = getattr(interaction, "output_text", None) or "✅ Aufgabe abgeschlossen (keine Textausgabe vom Modell)."
                return await _finish(out_text, "Fertig", "ok")

            function_results: list[dict[str, Any]] = []
            for call in function_calls:
                fn_name = getattr(call, "name", "")
                fn_args = getattr(call, "arguments", None) or {}
                call_id = getattr(call, "id", fn_name)

                sig = hashlib.sha1(f"{fn_name}:{json.dumps(fn_args, sort_keys=True, default=str)}".encode()).hexdigest()
                if sig in seen_calls:
                    return await _finish(
                        f"⚠️ ReAct-Failsafe ausgelöst: Das Modell wollte `{fn_name}` mit identischen Argumenten "
                        f"erneut aufrufen. Letzter Fehler: {last_error_msg or 'keiner'}",
                        "Abbruch: Identischer Tool-Aufruf wiederholt",
                        "duplicate_call",
                    )
                seen_calls.add(sig)

                await self._emit(__event_emitter__, f"Schritt {iteration}/{self.valves.MAX_ITERATIONS}: Führe `{fn_name}` aus…")

                fn = tool_callables.get(fn_name)
                if fn is None:
                    res: Any = {"ok": False, "error": f"Unbekanntes Werkzeug '{fn_name}'."}
                else:
                    try:
                        res = await fn(**fn_args)
                    except Exception as exc:
                        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

                if self._is_tool_error(res):
                    consecutive_errors += 1
                    last_error_msg = json.dumps(res, ensure_ascii=False, default=str)[:500]
                else:
                    consecutive_errors = 0
                    last_error_msg = ""

                function_results.append({
                    "type": "function_result",
                    "call_id": call_id,
                    "name": fn_name,
                    "result": res if isinstance(res, (dict, list, str, int, float, bool)) else str(res),
                })

            if consecutive_errors >= self.valves.MAX_CONSECUTIVE_ERRORS:
                return await _finish(
                    f"⚠️ ReAct-Circuit-Breaker ausgelöst: {consecutive_errors} Tool-Fehler in Folge.\n"
                    f"Letzte Rückmeldung: ```json\n{last_error_msg}\n```",
                    "Abbruch: Zu viele Fehler in Folge",
                    "circuit_breaker",
                )

            next_input = function_results

        # Failsafe erreicht: eine abschliessende Text-Zusammenfassung ohne Tools anfordern
        try:
            await self._emit(__event_emitter__, "Schrittlimit erreicht – fasse Ergebnis zusammen…")
            final_req = {
                "model": self.valves.GEMINI_MODEL,
                "input": next_input,
                "system_instruction": system_instruction + "\n\nFasse den bisherigen Stand knapp und ehrlich zusammen (ohne weitere Tools aufzurufen).",
            }
            if previous_interaction_id:
                final_req["previous_interaction_id"] = previous_interaction_id
            final_int = await self._create_interaction_with_backoff(client, **final_req)
            turn_usage = self._extract_usage(final_int)
            for k in total_usage:
                total_usage[k] += turn_usage[k]
            summary = getattr(final_int, "output_text", None) or ""
        except Exception:
            summary = ""

        prefix = f"⚠️ Maximale Schrittzahl ({self.valves.MAX_ITERATIONS}) erreicht.\n\n"
        body_txt = summary or f"Letzter Fehler: {last_error_msg or 'keiner'}"
        return await _finish(prefix + body_txt, "Maximale Schrittzahl erreicht", "max_iterations")
