"""Schlanker CLI-Wrapper für Antigravity Usage-, Pool- & Kosten-Auswertung.

Delegiert vollständig an `slicetheprompt.usage_eval` und `slicetheprompt.usage_store`.
Beispiele:
  python tools/usage_eval.py
  python tools/usage_eval.py --project 2026-11-13-kamera-teleskop-adapter
  python tools/usage_eval.py --pool gemini --horizon weekly --pct-left 64 --resets-in "1d 6h"
  python tools/usage_eval.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from slicetheprompt.usage_eval import (
    build_conversation_detail,
    build_project_detail,
    build_usage_overview,
    calibrate_pool_snapshot,
    update_settings,
)


def _print_overview(ov: dict, limit: int = 12) -> None:
    print("\n=== ANTIGRAVITY USAGE & QUOTA-POOLS (ÜBER ALLE WORKSPACES) ===")
    for pid, p in ov["pools"].items():
        h5 = p.get("h5")
        wk = p.get("weekly")
        parts = []
        if wk:
            r = f" ({wk['resets_in_text']})" if wk.get("resets_in_text") else ""
            cal = " [kalibriert]" if wk.get("calibrated") else " [Schätzwert]"
            parts.append(f"Weekly: {wk['pct_left']:.1f}% frei{r}{cal}")
        if h5:
            r5 = f" ({h5['resets_in_text']})" if h5.get("resets_in_text") else ""
            parts.append(f"5h: {h5['pct_left']:.1f}% frei{r5}")
        print(f"  * {p['label']:<22} | " + " | ".join(parts))

    show_eco = ov.get("settings", {}).get("show_eco_metrics", False)
    print(f"\n=== WORKSPACES & TEILE ({len(ov['projects'])} CAD-Teile) ===")
    for ws in ov["workspaces"][:limit]:
        print(f"\n[Workspace: {ws['workspace']}]  CU: {ws['total_cu']:.1f} | CHF {ws['total_cost_chf']:.2f}")
        for pname, pitem in list(ws["projects"].items())[:limit]:
            pc_obj = pitem.get("cost") or {}
            eco = f" | {pc_obj.get('energy_wh', 0):.2f} Wh · {pc_obj.get('water_ml', 0):.2f} ml" if show_eco else ""
            prc = pitem.get("print_cost")
            pc = f" | Druck CHF {prc['total_chf']:.2f}" if prc and prc.get("total_chf") is not None else ""
            pct_w = pc_obj.get("pct_weekly") or 0.0
            pct_5 = pc_obj.get("pct_5h") or 0.0
            print(
                f"    - {pname:<38} v:{pitem['versions_count']:<2} turns:{pitem['turns_count']:<2} "
                f"| CHF {pc_obj.get('cost_chf', 0):.2f} ({pct_w:.2f}% Wk / {pct_5:.2f}% 5h){pc}{eco}"
            )


def _fmt_p(val: float | None) -> str:
    return f"{val:.2f}%" if val is not None else "–"


def _print_detail(det: dict) -> None:
    show_eco = det.get("settings", {}).get("show_eco_metrics", False)
    s = det["summary"]
    label = det.get("project_name") or det.get("title") or det.get("conversation_id")
    print(f"\n=== TIMELINE: {label} ===")
    print(f"  Gesamt: CHF {s['cost_chf']:.2f} (${s['cost_usd']:.3f}) | {_fmt_p(s.get('pct_5h'))} 5h | {_fmt_p(s.get('pct_weekly'))} Weekly | {s['cu']:.1f} CU")
    if show_eco:
        print(f"  Energie & Wasser: ~{s['energy_wh']:.2f} Wh · ~{s['water_ml']:.2f} ml")
    if det.get("print_cost"):
        pc = det["print_cost"]
        print(f"  3D-Druckkosten: CHF {pc.get('total_chf', 0):.2f} ({pc.get('gramm', 0):.1f} g)")

    for t in det["turns"]:
        vers = ", ".join(v["version"] for v in t.get("versions", []))
        vtag = f" [CAD: {vers}]" if vers else ""
        pc = t["prompt_cost"]
        ac = t["answer_cost"]
        tc = t["total_cost"]
        print(f"\n  Turn #{t['turn_number']} [{t['model_display']}] sub:{t['subagent_calls']}{vtag}")
        print(f"    -> Prompt : {t['prompt_short']}")
        print(f"       Kosten : CHF {pc['cost_chf']:.3f} | {_fmt_p(pc.get('pct_5h'))} 5h | {_fmt_p(pc.get('pct_weekly'))} Wk")
        print(f"    <- Antwort: Planner={t['planner_calls']}, Cache={t['cache_hit_pct']:.0f}%, Think={t['thinking_share_pct']:.0f}%")
        print(f"       Kosten : CHF {ac['cost_chf']:.3f} | {_fmt_p(ac.get('pct_5h'))} 5h | {_fmt_p(ac.get('pct_weekly'))} Wk")
        print(f"    == Abschnitt: CHF {tc['cost_chf']:.3f} ({_fmt_p(tc.get('pct_weekly'))} Wk)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Antigravity Usage & Quota Evaluator")
    ap.add_argument("--project", default="", help="Detailansicht für ein Teil-Projekt")
    ap.add_argument("--conversation", default="", help="Detailansicht für eine Konversations-ID")
    ap.add_argument("--days", type=float, default=7.0, help="Zeitraum in Tagen (Standard: 7)")
    ap.add_argument("--limit", type=int, default=15, help="Max. Einträge in Textausgabe")
    ap.add_argument("--pool", default="", help="Pool für Kalibrierung (gemini, claude_gpt, argon)")
    ap.add_argument("--horizon", choices=["5h", "weekly"], default="weekly", help="Limit-Horizont")
    ap.add_argument("--pct-left", type=float, default=None, help="Aktuell angezeigte Rest-%")
    ap.add_argument("--resets-in", default="", help="Restzeit bis Reset (z. B. '1d 6h')")
    ap.add_argument("--eco", choices=["on", "off"], default=None, help="Energie-/Wasser-Metriken ein/aus")
    ap.add_argument("--json", action="store_true", help="JSON ausgeben")
    a = ap.parse_args(argv)

    if a.eco is not None:
        update_settings({"show_eco_metrics": a.eco == "on"})
    if a.pool and (a.pct_left is not None or a.resets_in):
        calibrate_pool_snapshot(
            pool_id=a.pool,
            horizon=a.horizon,
            pct_left=a.pct_left,
            resets_in=a.resets_in or None,
        )

    if a.project:
        out = build_project_detail(a.project, days=max(a.days, 30.0))
        if a.json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            _print_detail(out)
        return 0

    if a.conversation:
        out = build_conversation_detail(a.conversation)
        if a.json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            _print_detail(out)
        return 0

    out = build_usage_overview(days=a.days)
    if a.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        _print_overview(out, limit=a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
