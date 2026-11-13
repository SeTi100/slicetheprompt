"""Auswertung der exakten API-Token-Logs (out/token_usage.jsonl) für das Android-Studio."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from .config import ROOT


def default_token_log_path() -> Path:
    """Liefert den Standardpfad zur JSONL-Token-Logdatei."""
    env_path = os.environ.get("SLICETHEPROMPT_TOKEN_LOG", "").strip()
    if env_path:
        return Path(env_path)
    return ROOT / "out" / "token_usage.jsonl"


def _parse_ts(raw: Any) -> dt.datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    txt = raw.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(txt)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _as_int(val: Any) -> int:
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 0


def _as_float(val: Any) -> float:
    try:
        return max(0.0, float(val))
    except (TypeError, ValueError):
        return 0.0


def _normalize_entry(raw: dict[str, Any]) -> dict[str, Any]:
    inp = _as_int(raw.get("input_tokens"))
    out = _as_int(raw.get("output_tokens"))
    thought = _as_int(raw.get("thought_tokens"))
    tot = _as_int(raw.get("total_tokens")) or (inp + out + thought)
    ts_str = str(raw.get("ts") or "")
    dt_utc = _parse_ts(ts_str)
    return {
        "ts": ts_str,
        "_dt": dt_utc,
        "user": str(raw.get("user") or "unknown"),
        "model": str(raw.get("model") or "unknown"),
        "status": str(raw.get("status") or "ok"),
        "steps": _as_int(raw.get("steps")),
        "duration_s": round(_as_float(raw.get("duration_s")), 2),
        "input_tokens": inp,
        "output_tokens": out,
        "thought_tokens": thought,
        "total_tokens": tot,
        "cost_usd": round(_as_float(raw.get("cost_usd")), 6),
    }


def _aggregate_entries(entries: list[dict[str, Any]], local_tz: dt.tzinfo, max_days: int = 14) -> dict[str, Any]:
    requests = len(entries)
    ok_requests = sum(1 for e in entries if e["status"] == "ok")
    error_requests = requests - ok_requests

    inp = sum(e["input_tokens"] for e in entries)
    out = sum(e["output_tokens"] for e in entries)
    thought = sum(e["thought_tokens"] for e in entries)
    tot = sum(e["total_tokens"] for e in entries)
    cost = round(sum(e["cost_usd"] for e in entries), 6)
    steps = sum(e["steps"] for e in entries)
    duration = round(sum(e["duration_s"] for e in entries), 2)

    by_model_map: dict[str, dict[str, Any]] = {}
    daily_map: dict[str, dict[str, Any]] = {}

    for e in entries:
        m = e["model"]
        mb = by_model_map.setdefault(
            m,
            {
                "model": m,
                "requests": 0,
                "ok_requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "thought_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        mb["requests"] += 1
        if e["status"] == "ok":
            mb["ok_requests"] += 1
        mb["input_tokens"] += e["input_tokens"]
        mb["output_tokens"] += e["output_tokens"]
        mb["thought_tokens"] += e["thought_tokens"]
        mb["total_tokens"] += e["total_tokens"]
        mb["cost_usd"] = round(mb["cost_usd"] + e["cost_usd"], 6)

        if e["_dt"] is not None:
            day_key = e["_dt"].astimezone(local_tz).date().isoformat()
            db = daily_map.setdefault(
                day_key,
                {
                    "date": day_key,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thought_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            db["requests"] += 1
            db["input_tokens"] += e["input_tokens"]
            db["output_tokens"] += e["output_tokens"]
            db["thought_tokens"] += e["thought_tokens"]
            db["total_tokens"] += e["total_tokens"]
            db["cost_usd"] = round(db["cost_usd"] + e["cost_usd"], 6)

    by_model = sorted(by_model_map.values(), key=lambda x: (-x["total_tokens"], x["model"]))
    daily = sorted(daily_map.values(), key=lambda x: x["date"])[-max_days:]

    sorted_entries = sorted(
        entries,
        key=lambda e: (e["_dt"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc)),
        reverse=True,
    )
    recent = [{k: v for k, v in e.items() if k != "_dt"} for e in sorted_entries[:25]]

    return {
        "totals": {
            "requests": requests,
            "ok_requests": ok_requests,
            "error_requests": error_requests,
            "input_tokens": inp,
            "output_tokens": out,
            "thought_tokens": thought,
            "total_tokens": tot,
            "cost_usd": cost,
            "total_steps": steps,
            "total_duration_s": duration,
            "avg_tokens": round(tot / requests) if requests else 0,
            "avg_duration_s": round(duration / requests, 2) if requests else 0.0,
            "avg_steps": round(steps / requests, 1) if requests else 0.0,
        },
        "by_model": by_model,
        "daily": daily,
        "recent": recent,
    }


def load_usage_summary(
    log_path: Path | str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Liest die JSONL-Token-Logs und aggregiert sie für Heute, 7 Tage und Gesamt."""
    path = Path(log_path) if log_path is not None else default_token_log_path()
    entries: list[dict[str, Any]] = []
    exists = path.is_file()

    if exists:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                raw = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                entries.append(_normalize_entry(raw))

    ref_now = now if now is not None else dt.datetime.now().astimezone()
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=dt.timezone.utc)
    local_tz = ref_now.tzinfo
    today_start = ref_now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - dt.timedelta(days=6)

    today_entries = [
        e for e in entries if e["_dt"] is not None and e["_dt"].astimezone(local_tz) >= today_start
    ]
    week_entries = [
        e for e in entries if e["_dt"] is not None and e["_dt"].astimezone(local_tz) >= week_start
    ]

    return {
        "ok": True,
        "log_path": str(path),
        "exists": exists,
        "total_entries": len(entries),
        "periods": {
            "today": _aggregate_entries(today_entries, local_tz, max_days=1),
            "week": _aggregate_entries(week_entries, local_tz, max_days=7),
            "all": _aggregate_entries(entries, local_tz, max_days=14),
        },
    }
