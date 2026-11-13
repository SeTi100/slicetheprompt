"""Synchronisiert scripts/openwebui_gemini_react_pipe.py direkt in Open WebUIs webui.db (behält Valves/API-Key)."""

from __future__ import annotations

import asyncio
import inspect
import os
import pathlib


async def _maybe(x):
    return await x if inspect.isawaitable(x) else x


async def main() -> None:
    os.environ.setdefault("WEBUI_SECRET_KEY", "temp")
    from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
    from open_webui.models.users import Users

    pipe_path = pathlib.Path(__file__).resolve().parent / "openwebui_gemini_react_pipe.py"
    code = pipe_path.read_text(encoding="utf-8")
    fid = "slicetheprompt_gemini_react"
    meta = FunctionMeta(
        description="Multi-Turn ReAct Agent Pipe für Open WebUI mit Google GenAI SDK (>= 2.30.1), direkter MCP-Anbindung, Failsafes & Token-Tracking.",
        manifest={"title": "SliceThePrompt Gemini ReAct Agent", "author": "slicetheprompt", "version": "1.2.3"},
    )

    existing = await _maybe(Functions.get_function_by_id(fid))
    if existing:
        await _maybe(
            Functions.update_function_by_id(
                fid,
                {
                    "name": "SliceThePrompt Gemini ReAct Agent",
                    "content": code,
                    "meta": meta.model_dump(),
                    "is_active": True,
                },
            )
        )
        print(f"Open WebUI Pipe '{fid}' in webui.db aktualisiert (Valves/API-Key erhalten).")
    else:
        u_res = await _maybe(Users.get_users())
        users = u_res["users"] if isinstance(u_res, dict) else u_res
        admin = next((u for u in users if getattr(u, "role", "") == "admin"), users[0])
        form = FunctionForm(id=fid, name="SliceThePrompt Gemini ReAct Agent", content=code, meta=meta)
        await _maybe(Functions.insert_new_function(admin.id, "pipe", form))
        await _maybe(Functions.update_function_by_id(fid, {"is_active": True}))
        print(f"Open WebUI Pipe '{fid}' neu in webui.db angelegt & aktiviert.")


if __name__ == "__main__":
    asyncio.run(main())
