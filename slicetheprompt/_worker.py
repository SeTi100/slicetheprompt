"""Führt eine Pipeline-Funktion in einem eigenen Prozess aus:  python -m slicetheprompt._worker <name> <json-args>

Der MCP-Server ruft schwere Schritte (Bauen, Prüfen, Rendern) hierüber auf. Dadurch
  * laufen Aufrufe mehrerer Sessions wirklich parallel (kein gemeinsamer Python-Prozess / GIL),
  * nutzt jeder Aufruf den aktuellen Code im Checkout, auch wenn der Server älter ist,
  * kann ein langer Lauf den Server nicht blockieren.
Ausgabe: genau ein JSON-Objekt auf stdout; alles, was die Pipeline druckt, geht nach stderr.
"""

from __future__ import annotations

import json
import sys
import traceback


def _funcs() -> dict:
    from . import assembly, history, mesh_edit, pipeline
    return {
        "build_assembly": assembly.build_assembly,
        "check_state": assembly.check_state,
        "run_all": pipeline.run_all,
        "check": pipeline.check,
        "render": pipeline.render,
        "new_stencil": pipeline.new_stencil,
        "restore_version": history.restore_version,
        "import_project": mesh_edit.import_project,
        "replace_in_mesh": mesh_edit.replace_in_mesh,
        "prepare_import": mesh_edit.prepare_import,
    }


def main() -> int:
    out = sys.stdout
    sys.stdout = sys.stderr                    # Pipeline-Ausgaben dürfen das JSON nicht stören
    try:
        name, args = sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
        res = _funcs()[name](*args)
        if isinstance(res, list):
            res = {"items": res}
    except Exception as exc:
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=4)[-1500:]}
    out.write(json.dumps(res, ensure_ascii=False, default=str))
    out.flush()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    if hasattr(sys.__stdout__, "reconfigure"):
        sys.__stdout__.reconfigure(encoding="utf-8")
    sys.exit(main())
