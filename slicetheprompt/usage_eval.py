"""Antigravity Usage-, Quota- & Iterations-Evaluations-Engine.

Berechnet und kalibriert auf Basis der lokalen SQLite-Protobuf-Datenbanken (`usage_store`):
  * Getrennte Modell-Pools mit 5h- und/oder Weekly-Limits (Gemini Models, Claude/GPT, Argon, erweiterbar)
  * Anker-Kalibrierung aus echten Antigravity-Rest-% + Reset-Timern (config/usage_calibration.yaml)
  * Subagent-Rollup über parent_conversation_id
  * Prompt- vs. Antwort-Kostenaufspaltung + Section-Summen (inkl. CHF/USD, % 5h, % Weekly, Energie Wh, Wasser ml)
  * Zeitlich präzise Verknüpfung mit CAD-Iterationen (projects/<teil>/history/vNNN/meta.json)
  * Vergleich KI-Entwicklungskosten vs. reale 3D-Druckkosten (kosten.py)
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR, PROJECTS_DIR, _read_yaml, write_yaml
from .history import list_versions
from .kosten import for_project as slice_costs_for_project
from .usage_store import (
    ConversationData,
    ConversationSummary,
    RawRequestRecord,
    RawStepRecord,
    default_antigravity_dir,
    list_recent_conversation_ids,
    load_conversation,
    load_conversation_summaries,
)

CALIBRATION_YAML = CONFIG_DIR / "usage_calibration.yaml"
USD_PER_CU = 0.001  # 1 Compute Unit = $0.001 (0.1 US-Cent)


# -----------------------------------------------------------------------------
# Modell-Spezifikationen & erweiterbares Pool-Register
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    key: str
    display_name: str
    pool_id: str
    input_usd_per_m: float
    cache_usd_per_m: float
    output_usd_per_m: float
    # Hardware-Leistungsannahmen (Wh pro 1M Tokens vor PUE)
    wh_per_m_fresh: float
    wh_per_m_cache: float
    wh_per_m_output: float
    # Faktor für What-If Thinking-Volumen-Schätzung
    thinking_effort_factor: float = 1.0


@dataclass(frozen=True)
class PoolConfig:
    pool_id: str
    label: str
    has_5h: bool
    has_weekly: bool
    default_5h_cu: float
    default_weekly_cu: float
    color: str


DEFAULT_POOLS: dict[str, PoolConfig] = {
    "gemini": PoolConfig(
        pool_id="gemini",
        label="Gemini Models",
        has_5h=True,
        has_weekly=True,
        default_5h_cu=2500.0,
        default_weekly_cu=15000.0,
        color="#38bdf8",
    ),
    "claude_gpt": PoolConfig(
        pool_id="claude_gpt",
        label="Claude & GPT Models",
        has_5h=True,
        has_weekly=True,
        default_5h_cu=2000.0,
        default_weekly_cu=12000.0,
        color="#a855f7",
    ),
    "argon": PoolConfig(
        pool_id="argon",
        label="Gemini 4 Argon",
        has_5h=False,
        has_weekly=True,
        default_5h_cu=0.0,
        default_weekly_cu=6000.0,
        color="#f59e0b",
    ),
}


MODEL_SPECS: dict[str, ModelSpec] = {
    "flash": ModelSpec(
        key="flash",
        display_name="Gemini 3.8 Flash",
        pool_id="gemini",
        input_usd_per_m=0.075,
        cache_usd_per_m=0.01875,
        output_usd_per_m=0.30,
        wh_per_m_fresh=8.0,
        wh_per_m_cache=1.2,
        wh_per_m_output=45.0,
        thinking_effort_factor=0.65,
    ),
    "hydrogen": ModelSpec(
        key="hydrogen",
        display_name="Gemini 4 Hydrogen",
        pool_id="gemini",
        input_usd_per_m=1.25,
        cache_usd_per_m=0.3125,
        output_usd_per_m=5.00,
        wh_per_m_fresh=55.0,
        wh_per_m_cache=8.0,
        wh_per_m_output=320.0,
        thinking_effort_factor=1.0,
    ),
    "pro": ModelSpec(
        key="pro",
        display_name="Gemini Pro",
        pool_id="gemini",
        input_usd_per_m=1.25,
        cache_usd_per_m=0.3125,
        output_usd_per_m=5.00,
        wh_per_m_fresh=55.0,
        wh_per_m_cache=8.0,
        wh_per_m_output=320.0,
        thinking_effort_factor=1.0,
    ),
    "argon": ModelSpec(
        key="argon",
        display_name="Gemini 4 Argon",
        pool_id="argon",
        input_usd_per_m=2.50,
        cache_usd_per_m=0.625,
        output_usd_per_m=10.00,
        wh_per_m_fresh=95.0,
        wh_per_m_cache=14.0,
        wh_per_m_output=550.0,
        thinking_effort_factor=1.2,
    ),
    "claude_opus": ModelSpec(
        key="claude_opus",
        display_name="Claude Opus 5.5",
        pool_id="claude_gpt",
        input_usd_per_m=5.00,
        cache_usd_per_m=0.50,
        output_usd_per_m=25.00,
        wh_per_m_fresh=130.0,
        wh_per_m_cache=18.0,
        wh_per_m_output=780.0,
        thinking_effort_factor=1.25,
    ),
    "claude_sonnet": ModelSpec(
        key="claude_sonnet",
        display_name="Claude Sonnet",
        pool_id="claude_gpt",
        input_usd_per_m=3.00,
        cache_usd_per_m=0.30,
        output_usd_per_m=15.00,
        wh_per_m_fresh=85.0,
        wh_per_m_cache=12.0,
        wh_per_m_output=500.0,
        thinking_effort_factor=1.1,
    ),
    "gpt": ModelSpec(
        key="gpt",
        display_name="GPT Model",
        pool_id="claude_gpt",
        input_usd_per_m=2.50,
        cache_usd_per_m=0.625,
        output_usd_per_m=10.00,
        wh_per_m_fresh=80.0,
        wh_per_m_cache=12.0,
        wh_per_m_output=480.0,
        thinking_effort_factor=1.0,
    ),
}

# Bekannte Protobuf model_enum Zuordnungen (Fallback falls slug/display leer)
MODEL_ENUM_MAP: dict[str, str] = {
    "MODEL_PLACEHOLDER_M318": "flash",
    "MODEL_PLACEHOLDER_M319": "flash",
    "MODEL_PLACEHOLDER_M320": "flash",
    "MODEL_PLACEHOLDER_M322": "flash",
    "MODEL_PLACEHOLDER_M401": "claude_opus",
    "MODEL_PLACEHOLDER_M418": "argon",
    "MODEL_PLACEHOLDER_M526": "hydrogen",
    "MODEL_PLACEHOLDER_M528": "hydrogen",
}

MODEL_ENUM_EFFORT_MAP: dict[str, str] = {
    "MODEL_PLACEHOLDER_M318": "High",
    "MODEL_PLACEHOLDER_M319": "Medium",
    "MODEL_PLACEHOLDER_M320": "Low",
    "MODEL_PLACEHOLDER_M401": "Medium",
    "MODEL_PLACEHOLDER_M418": "Medium",
    "MODEL_PLACEHOLDER_M526": "Medium",
    "MODEL_PLACEHOLDER_M528": "XHigh",
}


def extract_model_effort(
    model_display: str = "",
    model_slug: str = "",
    model_enum: str = "",
) -> str:
    """Extrahiert die Thinking-/Effort-Stufe (Low, Medium, High, XHigh, Extra) aus den Modell-Metadaten."""
    for src in (model_display, model_slug):
        if not src:
            continue
        m = re.search(r"\b(XHigh|Extra|Medium|High|Low)\b", src, re.IGNORECASE)
        if m:
            raw = m.group(1).lower()
            return {
                "xhigh": "XHigh",
                "extra": "Extra",
                "high": "High",
                "medium": "Medium",
                "low": "Low",
            }.get(raw, m.group(1))
    return MODEL_ENUM_EFFORT_MAP.get(model_enum, "")


def resolve_model_spec(
    model_display: str = "",
    model_slug: str = "",
    model_enum: str = "",
    custom_overrides: dict[str, Any] | None = None,
) -> ModelSpec:
    """Ordnet ein Modell eindeutig seiner Spezifikation und seinem Quota-Pool zu."""
    combined = f"{model_display} {model_slug} {model_enum}".lower()
    if custom_overrides:
        for pat, target_val in custom_overrides.items():
            if pat.lower() in combined:
                if isinstance(target_val, str) and target_val in MODEL_SPECS:
                    return MODEL_SPECS[target_val]
                if isinstance(target_val, dict):
                    in_p = float(target_val.get("input_per_1m") or target_val.get("input_usd_per_m") or 2.0)
                    out_p = float(target_val.get("output_per_1m") or target_val.get("output_usd_per_m") or 12.0)
                    c_p = float(target_val.get("cache_per_1m") or target_val.get("cache_usd_per_m") or (in_p * 0.1))
                    return ModelSpec(
                        key=pat,
                        display_name=str(target_val.get("display_name") or pat),
                        pool_id=str(target_val.get("pool_id") or "gemini"),
                        input_usd_per_m=in_p,
                        cache_usd_per_m=c_p,
                        output_usd_per_m=out_p,
                        wh_per_m_fresh=float(target_val.get("wh_per_m_fresh") or 65.0),
                        wh_per_m_cache=float(target_val.get("wh_per_m_cache") or 9.0),
                        wh_per_m_output=float(target_val.get("wh_per_m_output") or 400.0),
                    )

    if "flash" in combined:
        return MODEL_SPECS["flash"]
    if "argon" in combined:
        return MODEL_SPECS["argon"]
    if "hydrogen" in combined:
        return MODEL_SPECS["hydrogen"]
    if "opus" in combined:
        return MODEL_SPECS["claude_opus"]
    if "sonnet" in combined or "claude" in combined:
        return MODEL_SPECS["claude_sonnet"]
    if "gpt" in combined or "o3" in combined or "o4" in combined:
        return MODEL_SPECS["gpt"]
    if "pro" in combined:
        return MODEL_SPECS["pro"]

    if model_enum in MODEL_ENUM_MAP:
        return MODEL_SPECS[MODEL_ENUM_MAP[model_enum]]

    return MODEL_SPECS["hydrogen"]


# -----------------------------------------------------------------------------
# Kosten-, Energie- & Kalibrierungskonfiguration
# -----------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "currency": "CHF",
    "usd_to_chf": 0.90,
    "show_eco_metrics": False,
    "pue": 1.15,              # Power Usage Effectiveness Rechenzentrum
    "wue_ml_per_wh": 1.80,    # Wasserverbrauch (Kühlung + Strommix) in ml / Wh
    "custom_models": {},      # z. B. {"gemini 5": "argon"} für künftige Modelle
    "pools": {
        pid: {
            "label": cfg.label,
            "has_5h": cfg.has_5h,
            "has_weekly": cfg.has_weekly,
            "limit_5h_cu": cfg.default_5h_cu,
            "limit_weekly_cu": cfg.default_weekly_cu,
            "color": cfg.color,
            "anchor_5h": None,
            "anchor_weekly": None,
            "ring_history": [],
        }
        for pid, cfg in DEFAULT_POOLS.items()
    },
}

MAX_RING_HISTORY = 2000


def load_calibration(cal_path: Path | None = None) -> dict[str, Any]:
    """Lädt lokale Kalibrierung & Einstellungen und ergänzt fehlende Standardfelder."""
    p = cal_path or CALIBRATION_YAML
    raw = _read_yaml(p)
    cfg = {
        "currency": raw.get("currency", DEFAULT_SETTINGS["currency"]),
        "usd_to_chf": float(raw.get("usd_to_chf", DEFAULT_SETTINGS["usd_to_chf"])),
        "show_eco_metrics": bool(raw.get("show_eco_metrics", DEFAULT_SETTINGS["show_eco_metrics"])),
        "pue": float(raw.get("pue", DEFAULT_SETTINGS["pue"])),
        "wue_ml_per_wh": float(raw.get("wue_ml_per_wh", DEFAULT_SETTINGS["wue_ml_per_wh"])),
        "custom_models": dict(raw.get("custom_models") or {}),
        "pools": {},
    }
    raw_pools = raw.get("pools") or {}
    # Bestehende Standard-Pools + evtl. nutzerdefinierte künftige Pools übernehmen
    for pid in list(DEFAULT_POOLS.keys()) + [k for k in raw_pools if k not in DEFAULT_POOLS]:
        base = DEFAULT_SETTINGS["pools"].get(pid, {
            "label": pid,
            "has_5h": True,
            "has_weekly": True,
            "limit_5h_cu": 2500.0,
            "limit_weekly_cu": 15000.0,
            "color": "#10b981",
            "anchor_5h": None,
            "anchor_weekly": None,
            "ring_history": [],
        })
        ov = raw_pools.get(pid) or {}
        merged = {
            **base,
            **{k: v for k, v in ov.items() if v is not None},
        }
        merged["ring_history"] = list(merged.get("ring_history") or [])[-MAX_RING_HISTORY:]
        cfg["pools"][pid] = merged
    return cfg


def save_calibration(cfg: dict[str, Any], cal_path: Path | None = None) -> None:
    p = cal_path or CALIBRATION_YAML
    write_yaml(
        p,
        cfg,
        header="Antigravity Usage-Kalibrierung & UI-Einstellungen (lokal, nicht im Git).",
    )


def parse_resets_in(val: str | float | int | None) -> float | None:
    """Parst Strings wie '1d 6h', '3h 9m', '45m' oder Zahlen (Stunden) in Stunden."""
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return max(0.0, float(val))
    s = str(val).strip().lower()
    if not s:
        return None
    try:
        return max(0.0, float(s.replace(",", ".")))
    except ValueError:
        pass
    days = 0.0
    hours = 0.0
    mins = 0.0
    m_d = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:d|t)", s)
    m_h = re.search(r"(\d+(?:[.,]\d+)?)\s*h", s)
    m_m = re.search(r"(\d+(?:[.,]\d+)?)\s*m", s)
    if m_d:
        days = float(m_d.group(1).replace(",", "."))
    if m_h:
        hours = float(m_h.group(1).replace(",", "."))
    if m_m:
        mins = float(m_m.group(1).replace(",", "."))
    if not (m_d or m_h or m_m):
        return None
    return max(0.0, days * 24.0 + hours + mins / 60.0)


# -----------------------------------------------------------------------------
# Kosten- & Öko-Metrik je Request
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CostBreakdown:
    fresh_in: int = 0
    cache_read: int = 0
    output_total: int = 0
    thinking_tokens: int = 0
    visible_output: int = 0
    cost_usd: float = 0.0
    cost_chf: float = 0.0
    cu: float = 0.0
    energy_wh: float = 0.0
    water_ml: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.fresh_in + self.cache_read + self.output_total

    def __add__(self, other: "CostBreakdown") -> "CostBreakdown":
        return CostBreakdown(
            fresh_in=self.fresh_in + other.fresh_in,
            cache_read=self.cache_read + other.cache_read,
            output_total=self.output_total + other.output_total,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
            visible_output=self.visible_output + other.visible_output,
            cost_usd=self.cost_usd + other.cost_usd,
            cost_chf=self.cost_chf + other.cost_chf,
            cu=self.cu + other.cu,
            energy_wh=self.energy_wh + other.energy_wh,
            water_ml=self.water_ml + other.water_ml,
        )


def compute_token_cost(
    fresh_in: int,
    cache_read: int,
    output_total: int,
    thinking_tokens: int,
    visible_output: int,
    spec: ModelSpec,
    cfg: dict[str, Any],
) -> CostBreakdown:
    cost_usd = (
        fresh_in * spec.input_usd_per_m
        + cache_read * spec.cache_usd_per_m
        + output_total * spec.output_usd_per_m
    ) / 1_000_000.0
    cu = cost_usd / USD_PER_CU
    cost_chf = cost_usd * float(cfg.get("usd_to_chf", 0.90))

    raw_wh = (
        fresh_in * spec.wh_per_m_fresh
        + cache_read * spec.wh_per_m_cache
        + output_total * spec.wh_per_m_output
    ) / 1_000_000.0
    energy_wh = raw_wh * float(cfg.get("pue", 1.15))
    water_ml = energy_wh * float(cfg.get("wue_ml_per_wh", 1.80))

    return CostBreakdown(
        fresh_in=fresh_in,
        cache_read=cache_read,
        output_total=output_total,
        thinking_tokens=thinking_tokens,
        visible_output=visible_output,
        cost_usd=cost_usd,
        cost_chf=cost_chf,
        cu=cu,
        energy_wh=energy_wh,
        water_ml=water_ml,
    )


def split_request_prompt_answer(
    req: RawRequestRecord,
    is_first_in_turn: bool,
    spec: ModelSpec,
    cfg: dict[str, Any],
) -> tuple[CostBreakdown, CostBreakdown]:
    """Teilt einen Request exakt in Prompt-Input-Anteil und Antwort/Tool-Anteil.

    Erhaltungssatz: prompt_cost + answer_cost == total_cost dieses Requests.
    Beim ersten Request eines Turns zählt dessen Input-Verarbeitung (frisch + cache)
    zur User-Prompt-Bubble; dessen Output+Thinking sowie alle weiteren Tool-Loop-Requests
    zählen zur AI-Antwort-Bubble.
    """
    if is_first_in_turn:
        p_cost = compute_token_cost(req.fresh_in, req.cache_read, 0, 0, 0, spec, cfg)
        a_cost = compute_token_cost(0, 0, req.output_total, req.thinking_tokens, req.visible_output, spec, cfg)
        return p_cost, a_cost
    return CostBreakdown(), compute_token_cost(
        req.fresh_in, req.cache_read, req.output_total, req.thinking_tokens, req.visible_output, spec, cfg
    )


# -----------------------------------------------------------------------------
# Projekt-Erkennung, Subagent-Rollup & Version-Matching
# -----------------------------------------------------------------------------

PROJECT_PATTERN = re.compile(
    r"(?:projects[/\\]|project[\"':\s=]+|projekt[\"':\s=]+)?(20\d{2}-\d{2}-\d{2}-[a-zA-Z0-9_\-]+)",
    re.IGNORECASE,
)
PART_SUFFIX_PATTERN = re.compile(
    r"([a-zA-Z0-9_\-]+-(?:schablone|baugruppe|ritzel|adapter|halterung|gehaeuse))",
    re.IGNORECASE,
)


def _extract_project_from_text(text: str) -> str:
    if not text:
        return ""
    m = PROJECT_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    m2 = PART_SUFFIX_PATTERN.search(text)
    if m2:
        cand = m2.group(1).strip()
        if cand.lower() not in ("out", "build", "src", "tests", "lib"):
            return cand
    return ""


def _resolve_known_project(cand: str, known_projects: list[str] | None = None) -> str:
    """Löst Kurz- oder Slug-Namen (z. B. 'Kamera-Teleskop-Adapter') auf echte Projektordner auf."""
    if not cand or not known_projects:
        return cand
    if cand in known_projects:
        return cand
    cl = cand.lower().strip().replace(" ", "-")
    for kp in known_projects:
        if kp.lower() == cl:
            return kp
    # Datum-Präfix 20xx-xx-xx-<slug>
    for kp in sorted(known_projects, reverse=True):
        if kp.lower().endswith("-" + cl):
            return kp
    return cand


CAD_MCP_TOOLS = {
    "project_new", "build_and_check", "slice", "stencil", "get_ui_feedback",
    "clear_ui_feedback", "upload_and_print", "check_report", "render_views",
    "build_assembly", "assembly_new", "print_cost", "import_3mf", "mesh_replace",
}


def _detect_turn_project(
    prompt: str,
    turn_steps: list[RawStepRecord],
    sticky_project: str,
    known_projects: list[str] | None = None,
) -> str:
    # Prüfen ob dieser Turn primär Werkzeug-Code (slicetheprompt/, tools/, tests/) entwickelt
    edits_tooling = False
    for st in turn_steps:
        for tc in st.tool_calls:
            if tc.get("name") in ("write_file", "edit_file"):
                p = str((tc.get("args") or {}).get("Path") or "").replace("\\", "/")
                if any(seg in p for seg in ("/slicetheprompt/ui/", "/slicetheprompt/", "/tools/", "/tests/")) and "/projects/" not in p:
                    edits_tooling = True

    # 1. Echte CAD-Aktionen (MCP-Tools oder Bearbeitung von projects/<teil>/...)
    fallback = ""
    for st in turn_steps:
        for tc in st.tool_calls:
            tname = str(tc.get("name") or "")
            args = tc.get("args") or {}
            if not isinstance(args, dict):
                continue

            sub_tool = str(args.get("ToolName") or "")
            is_cad_tool = tname in CAD_MCP_TOOLS or sub_tool in CAD_MCP_TOOLS or tname.startswith("mcp_slicetheprompt")
            proj_arg = args.get("project")
            if not proj_arg and isinstance(args.get("Arguments"), dict):
                proj_arg = args["Arguments"].get("project") or args["Arguments"].get("name")
            if is_cad_tool and proj_arg and str(proj_arg).strip():
                res = _resolve_known_project(str(proj_arg).strip(), known_projects)
                if PROJECT_PATTERN.match(res):
                    return res
                if not fallback:
                    fallback = res

            # Schreiben/Editieren innerhalb projects/<name>/
            if tname in ("write_file", "edit_file"):
                p_arg = str(args.get("Path") or "")
                m_p = PROJECT_PATTERN.search(p_arg)
                if m_p:
                    return m_p.group(1).strip()

            # Allgemeine Tool-Argumente nur auswerten, wenn kein Werkzeug-Code bearbeitet wird
            if not edits_tooling:
                for k, v in args.items():
                    sv = str(v)
                    if k == "CommandLine" and ("usage_eval" in sv or "test_" in sv):
                        continue
                    if is_cad_tool or k in ("Path", "project", "Arguments"):
                        m = PROJECT_PATTERN.search(sv)
                        if m:
                            return m.group(1).strip()
                    if not fallback and is_cad_tool and k in ("Path", "name", "project"):
                        found = _extract_project_from_text(sv)
                        if found:
                            fallback = _resolve_known_project(found, known_projects)

    if fallback:
        return _resolve_known_project(fallback, known_projects)

    # 2. Explizite datierte Projektnamen im User-Prompt (nicht in Tool-Dev-Chats mit z.B.-Beispielen)
    if not edits_tooling and not re.search(r"z\.?\s*B\.?\s+[^\n]*projects[/\\]", prompt or "", re.IGNORECASE):
        m_prompt = PROJECT_PATTERN.search(prompt or "")
        if m_prompt:
            return m_prompt.group(1).strip()

        found_p = _extract_project_from_text(prompt)
        if found_p:
            resolved = _resolve_known_project(found_p, known_projects)
            if not known_projects or resolved in known_projects or PROJECT_PATTERN.match(resolved):
                return resolved

    if sticky_project:
        return sticky_project
    return ""


def _local_naive_to_utc(ts_str: str) -> dt.datetime | None:
    """Konvertiert lokale naive ISO-Zeit (z. B. aus history/vNNN/meta.json) sauber nach UTC."""
    if not ts_str:
        return None
    try:
        parsed = dt.datetime.fromisoformat(ts_str)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()  # interpretiert als System-Lokalzeit
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Turn- & Chat-Aufbau inklusive Subagenten
# -----------------------------------------------------------------------------

def _build_children_map(summaries: dict[str, ConversationSummary]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for cid, s in summaries.items():
        if s.parent_conversation_id:
            children.setdefault(s.parent_conversation_id, []).append(cid)
    return children


def _sum_conversation_tree(
    conv_id: str,
    agy_dir: Path,
    summaries: dict[str, ConversationSummary],
    children_map: dict[str, list[str]],
    cfg: dict[str, Any],
    visited: set[str],
) -> tuple[CostBreakdown, dict[str, float], int]:
    """Summiert rekursiv alle Kosten eines Subagent-Baums (inkl. verschachtelter Subagenten)."""
    if conv_id in visited:
        return CostBreakdown(), {}, 0
    visited.add(conv_id)

    data = load_conversation(conv_id, agy_dir, summaries)
    total = CostBreakdown()
    by_pool: dict[str, float] = {}
    req_count = 0

    if data:
        overrides = cfg.get("custom_models") or {}
        for req in data.requests:
            spec = resolve_model_spec(req.model_display, req.model_slug, req.model_enum, overrides)
            c = compute_token_cost(
                req.fresh_in, req.cache_read, req.output_total, req.thinking_tokens, req.visible_output, spec, cfg
            )
            total = total + c
            by_pool[spec.pool_id] = by_pool.get(spec.pool_id, 0.0) + c.cu
            req_count += 1

    for child_id in children_map.get(conv_id, []):
        c_tot, c_pool, c_cnt = _sum_conversation_tree(child_id, agy_dir, summaries, children_map, cfg, visited)
        total = total + c_tot
        for pid, cu_val in c_pool.items():
            by_pool[pid] = by_pool.get(pid, 0.0) + cu_val
        req_count += c_cnt

    return total, by_pool, req_count


def _extract_spawned_subagents(steps: list[RawStepRecord], children_ids: list[str]) -> list[str]:
    """Findet Subagent-IDs, die in den Steps per invoke_subagent erwähnt werden."""
    found: list[str] = []
    if not children_ids:
        return found
    child_set = set(children_ids)
    for st in steps:
        for tc in st.tool_calls:
            if tc.get("name") == "invoke_subagent":
                raw = json.dumps(tc.get("args", ""))
                for cid in child_set:
                    if cid in raw and cid not in found:
                        found.append(cid)
    return found


def _make_short_prompt(prompt: str, max_len: int = 130) -> str:
    clean = " ".join((prompt or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 1] + "…"


@dataclass
class EvaluatedTurn:
    turn_number: int
    conversation_id: str
    execution_id: str
    timestamp: dt.datetime | None
    prompt_short: str
    prompt_full: str
    project_name: str
    model_key: str
    model_display: str
    pool_id: str
    planner_calls: int
    subagent_calls: int
    subagent_ids: list[str]
    prompt_cost: CostBreakdown
    answer_cost: CostBreakdown
    total_cost: CostBreakdown
    cache_hit_pct: float
    cache_break: bool
    thinking_share_pct: float
    context_used: int
    context_limit: int
    versions: list[dict[str, Any]] = field(default_factory=list)
    effort: str = ""


def evaluate_conversation_turns(
    conv_id: str,
    cfg: dict[str, Any],
    agy_dir: Path | None = None,
    summaries: dict[str, ConversationSummary] | None = None,
    children_map: dict[str, list[str]] | None = None,
    known_projects: list[str] | None = None,
) -> list[EvaluatedTurn]:
    """Wertet eine Haupt-Konversation Turn-für-Turn inkl. Subagenten und Cost-Drivers aus."""
    base = agy_dir or default_antigravity_dir()
    if summaries is None:
        summaries = load_conversation_summaries(base)
    if children_map is None:
        children_map = _build_children_map(summaries)
    if known_projects is None and PROJECTS_DIR.is_dir():
        known_projects = [p.name for p in PROJECTS_DIR.iterdir() if p.is_dir()]

    data = load_conversation(conv_id, base, summaries)
    if not data or (not data.requests and not data.steps):
        return []

    overrides = cfg.get("custom_models") or {}
    # Gruppiere Steps und Requests nach execution_id (oder USER_INPUT-Blöcken)
    user_steps = [s for s in data.steps if s.step_type == 14]
    steps_by_exec: dict[str, list[RawStepRecord]] = {}
    for s in data.steps:
        if s.execution_id:
            steps_by_exec.setdefault(s.execution_id, []).append(s)

    reqs_by_exec: dict[str, list[RawRequestRecord]] = {}
    for r in data.requests:
        reqs_by_exec.setdefault(r.execution_id or f"idx-{r.idx}", []).append(r)

    # Turn-Reihenfolge aus user_steps; Requests ohne user_step ebenfalls zeitlich einsortieren
    ordered_execs: list[tuple[str, RawStepRecord | None]] = []
    seen_execs: set[str] = set()
    for us in user_steps:
        eid = us.execution_id or f"step-{us.idx}"
        if eid not in seen_execs:
            seen_execs.add(eid)
            ordered_execs.append((eid, us))
    for r in data.requests:
        eid = r.execution_id or f"idx-{r.idx}"
        if eid not in seen_execs:
            seen_execs.add(eid)
            ordered_execs.append((eid, None))

    def _exec_sort_key(item: tuple[str, RawStepRecord | None]) -> tuple[dt.datetime, int]:
        eid, us = item
        if us and us.timestamp:
            return (us.timestamp, us.idx)
        reqs = reqs_by_exec.get(eid)
        if reqs and reqs[0].timestamp:
            return (reqs[0].timestamp, reqs[0].idx)
        return (dt.datetime.max.replace(tzinfo=dt.timezone.utc), 0)

    ordered_execs.sort(key=_exec_sort_key)

    child_ids = children_map.get(conv_id, [])
    assigned_children: set[str] = set()
    turns: list[EvaluatedTurn] = []
    sticky_project = ""
    prev_model_key = ""

    for t_num, (eid, u_step) in enumerate(ordered_execs, start=1):
        t_steps = steps_by_exec.get(eid, [u_step] if u_step else [])
        t_reqs = reqs_by_exec.get(eid, [])
        if not t_reqs and not u_step:
            continue

        prompt_full = u_step.user_text if u_step else "(System / Fortsetzung)"
        ts = u_step.timestamp if u_step else (t_reqs[0].timestamp if t_reqs else None)

        proj_name = _detect_turn_project(prompt_full, t_steps, sticky_project, known_projects)
        if proj_name:
            sticky_project = proj_name

        # Haupt-Modell des Turns bestimmen (erstes bzw. dominantes Request)
        lead_req = t_reqs[0] if t_reqs else None
        spec = (
            resolve_model_spec(lead_req.model_display, lead_req.model_slug, lead_req.model_enum, overrides)
            if lead_req
            else MODEL_SPECS["hydrogen"]
        )
        model_display = (lead_req.model_display if lead_req and lead_req.model_display else spec.display_name)
        effort = (
            extract_model_effort(lead_req.model_display, lead_req.model_slug, lead_req.model_enum)
            if lead_req
            else ""
        )

        p_cost = CostBreakdown()
        a_cost = CostBreakdown()
        cache_break = False

        for i, req in enumerate(t_reqs):
            r_spec = resolve_model_spec(req.model_display, req.model_slug, req.model_enum, overrides)
            pc, ac = split_request_prompt_answer(req, i == 0, r_spec, cfg)
            p_cost = p_cost + pc
            a_cost = a_cost + ac
            if i == 0 and t_num > 1 and prev_model_key and prev_model_key != r_spec.key and req.cache_read == 0 and req.fresh_in > 5000:
                cache_break = True

        if t_reqs:
            last_spec = resolve_model_spec(t_reqs[-1].model_display, t_reqs[-1].model_slug, t_reqs[-1].model_enum, overrides)
            prev_model_key = last_spec.key

        # Subagenten die diesem Turn zugeordnet werden können
        sub_cost = CostBreakdown()
        sub_calls = 0
        spawned_ids = _extract_spawned_subagents(t_steps, child_ids)
        # Falls im ersten Turn noch nicht zugeordnete Subagenten zeitlich passen:
        if not spawned_ids and child_ids and ts:
            next_ts = None
            if t_num < len(ordered_execs):
                nxt_eid, nxt_us = ordered_execs[t_num]
                if nxt_us and nxt_us.timestamp:
                    next_ts = nxt_us.timestamp
            for cid in child_ids:
                if cid in assigned_children:
                    continue
                csum = summaries.get(cid)
                if csum and csum.last_modified:
                    if csum.last_modified >= ts and (next_ts is None or csum.last_modified <= next_ts):
                        spawned_ids.append(cid)

        for cid in spawned_ids:
            assigned_children.add(cid)
            sc, _, scnt = _sum_conversation_tree(cid, base, summaries, children_map, cfg, set())
            sub_cost = sub_cost + sc
            sub_calls += scnt

        a_cost = a_cost + sub_cost
        total_cost = p_cost + a_cost

        tot_in = total_cost.fresh_in + total_cost.cache_read
        cache_hit = (total_cost.cache_read / tot_in * 100.0) if tot_in > 0 else 0.0
        think_share = (total_cost.thinking_tokens / total_cost.output_total * 100.0) if total_cost.output_total > 0 else 0.0

        last_req = t_reqs[-1] if t_reqs else None
        turns.append(
            EvaluatedTurn(
                turn_number=t_num,
                conversation_id=conv_id,
                execution_id=eid,
                timestamp=ts,
                prompt_short=_make_short_prompt(prompt_full),
                prompt_full=prompt_full,
                project_name=proj_name,
                model_key=spec.key,
                model_display=model_display,
                pool_id=spec.pool_id,
                effort=effort,
                planner_calls=len(t_reqs),
                subagent_calls=sub_calls,
                subagent_ids=spawned_ids,
                prompt_cost=p_cost,
                answer_cost=a_cost,
                total_cost=total_cost,
                cache_hit_pct=round(cache_hit, 1),
                cache_break=cache_break,
                thinking_share_pct=round(think_share, 1),
                context_used=last_req.context_used if last_req else 0,
                context_limit=last_req.context_limit if last_req else 256000,
            )
        )

    # Falls Subagenten zeitlich übrig blieben, an letzten Turn hängen
    leftover = [cid for cid in child_ids if cid not in assigned_children]
    if leftover and turns:
        last_t = turns[-1]
        for cid in leftover:
            sc, _, scnt = _sum_conversation_tree(cid, base, summaries, children_map, cfg, set())
            last_t.answer_cost = last_t.answer_cost + sc
            last_t.total_cost = last_t.prompt_cost + last_t.answer_cost
            last_t.subagent_calls += scnt
            last_t.subagent_ids.append(cid)

    return turns


# -----------------------------------------------------------------------------
# Account-Pools (5h + Weekly) & Anker-Kalibrierung
# -----------------------------------------------------------------------------

def _summarize_pool_data_points(
    pool_id: str,
    pool_cfg: dict[str, Any],
    all_requests: list[tuple[dt.datetime, str, ModelSpec, CostBreakdown]],
) -> dict[str, Any]:
    """Zählt gespeicherte diskrete Snapshot-Datenpunkte und berechnet Intervall-Burn-Rates."""
    hist = pool_cfg.get("ring_history") or []
    by_h: dict[str, list[dict[str, Any]]] = {"weekly": [], "5h": []}
    last_ts_dt: dt.datetime | None = None
    last_ts_iso: str | None = None

    for entry in hist:
        if not isinstance(entry, dict):
            continue
        hk = entry.get("horizon")
        if hk not in by_h:
            continue
        ts_dt = _parse_utc(entry.get("ts"))
        if ts_dt is None or entry.get("pct_left") is None:
            continue
        if last_ts_dt is None or ts_dt > last_ts_dt:
            last_ts_dt = ts_dt
            last_ts_iso = ts_dt.isoformat()
        by_h[hk].append({
            "ts": ts_dt,
            "pct_left": float(entry["pct_left"]),
            "resets_at": _parse_utc(entry.get("resets_at")),
        })

    pool_reqs = [(ts, c.cu) for ts, pid, _, c in all_requests if pid == pool_id]
    stats: dict[str, dict[str, Any]] = {}
    for hk, win_h in (("weekly", 168.0), ("5h", 5.0)):
        pts = sorted(by_h[hk], key=lambda x: x["ts"])
        ivs = 0
        sum_dpct = 0.0
        sum_cu = 0.0
        sum_dt_h = 0.0
        reset_tol_sec = 7200.0 if hk == "weekly" else 1200.0
        for idx in range(len(pts) - 1):
            p0, p1 = pts[idx], pts[idx + 1]
            dt_h = (p1["ts"] - p0["ts"]).total_seconds() / 3600.0
            if dt_h <= 0.0:
                continue
            r0, r1 = p0["resets_at"], p1["resets_at"]
            if r0 is not None and r1 is not None:
                same_cycle = abs((r1 - r0).total_seconds()) <= reset_tol_sec
            else:
                same_cycle = dt_h < win_h and p1["pct_left"] <= p0["pct_left"] + 0.5
            if not same_cycle:
                continue
            ivs += 1
            dpct = p0["pct_left"] - p1["pct_left"]
            if dpct > 0.0:
                iv_cu = sum(cu for r_ts, cu in pool_reqs if p0["ts"] < r_ts <= p1["ts"])
                if iv_cu > 0.0:
                    sum_dpct += dpct
                    sum_cu += iv_cu
                    sum_dt_h += dt_h
        stats[hk] = {
            "points": len(pts),
            "intervals": ivs,
            "pct_per_1k_cu": round(sum_dpct / (sum_cu / 1000.0), 2) if sum_cu > 0.0 else None,
            "pct_per_h": round(sum_dpct / sum_dt_h, 2) if sum_dt_h > 0.01 and sum_dpct > 0.0 else None,
        }

    return {
        "total": stats["weekly"]["points"] + stats["5h"]["points"],
        "weekly": stats["weekly"]["points"],
        "h5": stats["5h"]["points"],
        "intervals_total": stats["weekly"]["intervals"] + stats["5h"]["intervals"],
        "intervals_weekly": stats["weekly"]["intervals"],
        "intervals_5h": stats["5h"]["intervals"],
        "last_ts": last_ts_iso,
        "burn_rate": {
            "weekly_pct_per_1k_cu": stats["weekly"]["pct_per_1k_cu"],
            "h5_pct_per_1k_cu": stats["5h"]["pct_per_1k_cu"],
            "weekly_pct_per_h": stats["weekly"]["pct_per_h"],
            "h5_pct_per_h": stats["5h"]["pct_per_h"],
        },
    }


def _compute_pool_status(
    pool_id: str,
    pool_cfg: dict[str, Any],
    all_requests: list[tuple[dt.datetime, str, ModelSpec, CostBreakdown]],
    now_utc: dt.datetime,
) -> dict[str, Any]:
    """Berechnet 5h- und Weekly-Stand inkl. Kalibrierungs-Anker für einen Modell-Pool."""

    def _horizon_stats(horizon_key: str, window_hours: float, default_limit: float) -> dict[str, Any] | None:
        enabled = pool_cfg.get("has_5h", True) if horizon_key == "5h" else pool_cfg.get("has_weekly", True)
        if not enabled:
            return None

        limit_cu = float(pool_cfg.get(f"limit_{horizon_key}_cu") or default_limit)
        anchor = pool_cfg.get(f"anchor_{horizon_key}") or None

        anchor_used_pct = 0.0
        window_start = now_utc - dt.timedelta(hours=window_hours)
        resets_at_dt: dt.datetime | None = None
        calibrated = False

        if isinstance(anchor, dict) and anchor.get("snapshot_at"):
            snap_dt = _parse_utc(anchor.get("snapshot_at"))
            reset_dt = _parse_utc(anchor.get("resets_at"))
            if snap_dt and (reset_dt is None or now_utc < reset_dt):
                calibrated = True
                pct_left = float(anchor.get("pct_left", 100.0))
                anchor_used_pct = max(0.0, min(100.0, 100.0 - pct_left))
                window_start = snap_dt
                resets_at_dt = reset_dt

        cu_since = sum(
            c.cu for ts, pid, _, c in all_requests
            if pid == pool_id and ts >= window_start
        )
        # Wenn kein aktiver Anker gesetzt ist, direkt ab Rollfenster summieren
        pct_since = (cu_since / limit_cu * 100.0) if limit_cu > 0 else 0.0
        pct_used = min(100.0, anchor_used_pct + pct_since)
        pct_left = max(0.0, 100.0 - pct_used)
        cu_used_equiv = limit_cu * (pct_used / 100.0)
        cu_left = max(0.0, limit_cu - cu_used_equiv)

        resets_in_str = ""
        if resets_at_dt and resets_at_dt > now_utc:
            rem_sec = int((resets_at_dt - now_utc).total_seconds())
            d_part = rem_sec // 86400
            h_part = (rem_sec % 86400) // 3600
            m_part = (rem_sec % 3600) // 60
            if d_part > 0:
                resets_in_str = f"{d_part}d {h_part}h"
            elif h_part > 0:
                resets_in_str = f"{h_part}h {m_part}m"
            else:
                resets_in_str = f"{m_part}m"

        return {
            "enabled": True,
            "limit_cu": round(limit_cu, 1),
            "used_cu": round(cu_used_equiv, 1),
            "left_cu": round(cu_left, 1),
            "pct_used": round(pct_used, 1),
            "pct_left": round(pct_left, 1),
            "calibrated": calibrated,
            "resets_at": resets_at_dt.isoformat() if resets_at_dt else None,
            "resets_in_text": resets_in_str,
            "window_hours": window_hours,
        }

    h5 = _horizon_stats("5h", 5.0, 2500.0)
    hw = _horizon_stats("weekly", 168.0, 15000.0)

    ring_tracking: dict[str, Any] | None = None
    ring_hist = pool_cfg.get("ring_history") or []
    if isinstance(ring_hist, list) and ring_hist:
        last_rt = ring_hist[-1]
        if isinstance(last_rt, dict):
            diff_val = last_rt.get("ocr_ring_diff")
            if diff_val is None and last_rt.get("ocr_pct") is not None and last_rt.get("ring_pct") is not None:
                diff_val = round(float(last_rt["ocr_pct"]) - float(last_rt["ring_pct"]), 1)
            ring_tracking = {
                "horizon": last_rt.get("horizon"),
                "pct_left": last_rt.get("pct_left"),
                "ring_pct": last_rt.get("ring_pct"),
                "ring_color": last_rt.get("ring_color"),
                "ocr_pct": last_rt.get("ocr_pct"),
                "diff_pct": diff_val,
                "source": last_rt.get("source"),
                "selected_model": last_rt.get("selected_model"),
                "selected_effort": last_rt.get("selected_effort"),
                "timestamp": last_rt.get("ts"),
            }
    if ring_tracking is None:
        anchor_cands: list[dict[str, Any]] = []
        for hk in ("weekly", "5h"):
            anc = pool_cfg.get(f"anchor_{hk}")
            if isinstance(anc, dict) and (anc.get("ring_pct") is not None or anc.get("source") not in (None, "", "manual")):
                diff_val = anc.get("ocr_ring_diff")
                if diff_val is None and anc.get("ocr_pct") is not None and anc.get("ring_pct") is not None:
                    diff_val = round(float(anc["ocr_pct"]) - float(anc["ring_pct"]), 1)
                anchor_cands.append({
                    "horizon": hk,
                    "pct_left": anc.get("pct_left"),
                    "ring_pct": anc.get("ring_pct"),
                    "ring_color": anc.get("ring_color"),
                    "ocr_pct": anc.get("ocr_pct"),
                    "diff_pct": diff_val,
                    "source": anc.get("source"),
                    "timestamp": anc.get("snapshot_at"),
                })
        if anchor_cands:
            anchor_cands.sort(key=lambda x: str(x.get("timestamp") or ""))
            ring_tracking = anchor_cands[-1]

    return {
        "pool_id": pool_id,
        "label": pool_cfg.get("label", pool_id),
        "color": pool_cfg.get("color", "#38bdf8"),
        "has_5h": bool(pool_cfg.get("has_5h", True)),
        "has_weekly": bool(pool_cfg.get("has_weekly", True)),
        "h5": h5,
        "weekly": hw,
        "ring_tracking": ring_tracking,
        "data_points": _summarize_pool_data_points(pool_id, pool_cfg, all_requests),
    }


def _parse_utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        cleaned = str(s).strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        d = dt.datetime.fromisoformat(cleaned)
        if d.tzinfo is None:
            return d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def calibrate_pool_snapshot(
    pool_id: str,
    horizon: str,  # "5h" | "weekly"
    pct_left: float | None = None,
    resets_in: str | float | None = None,
    limit_cu: float | None = None,
    agy_dir: Path | None = None,
    cal_path: Path | None = None,
    now_utc: dt.datetime | None = None,
    ring_pct: float | None = None,
    ring_color: str | None = None,
    ocr_pct: float | None = None,
    source: str = "manual",
    selected_model: str | None = None,
    selected_effort: str | None = None,
) -> dict[str, Any]:
    """Speichert eine Kalibrierung (Rest-% + Reset-Timer) und leitet ggf. die reale Kapazität ab."""
    cfg = load_calibration(cal_path)
    if pool_id not in cfg["pools"]:
        raise ValueError(f"Unbekannter Modell-Pool: {pool_id}")
    if horizon not in ("5h", "weekly"):
        raise ValueError("horizon muss '5h' oder 'weekly' sein.")

    now = now_utc or dt.datetime.now(dt.timezone.utc)
    p_cfg = cfg["pools"][pool_id]
    win_hours = 5.0 if horizon == "5h" else 168.0

    if limit_cu is not None and limit_cu > 0:
        p_cfg[f"limit_{horizon}_cu"] = float(limit_cu)

    if pct_left is not None:
        pct_left_clamped = max(0.0, min(100.0, float(pct_left)))
        res_h = parse_resets_in(resets_in)
        resets_at_dt = (now + dt.timedelta(hours=res_h)) if res_h is not None else (now + dt.timedelta(hours=win_hours))

        # Versuche echte lokale CU im laufenden Fenster [resets_at - win_hours, now] zu messen
        win_start_est = resets_at_dt - dt.timedelta(hours=win_hours)
        base = agy_dir or default_antigravity_dir()
        summaries = load_conversation_summaries(base)
        recent_ids = list_recent_conversation_ids(win_start_est - dt.timedelta(hours=24), base)

        consumed_in_win = 0.0
        reqs_in_win = 0
        fresh_in_win = 0
        cache_read_in_win = 0
        output_in_win = 0
        thinking_in_win = 0
        overrides = cfg.get("custom_models") or {}
        for cid in recent_ids:
            cdata = load_conversation(cid, base, summaries)
            if not cdata:
                continue
            for r in cdata.requests:
                if r.timestamp and win_start_est <= r.timestamp <= now:
                    spec = resolve_model_spec(r.model_display, r.model_slug, r.model_enum, overrides)
                    if spec.pool_id == pool_id:
                        consumed_in_win += compute_token_cost(
                            r.fresh_in, r.cache_read, r.output_total, r.thinking_tokens, r.visible_output, spec, cfg
                        ).cu
                        reqs_in_win += 1
                        fresh_in_win += int(r.fresh_in or 0)
                        cache_read_in_win += int(r.cache_read or 0)
                        output_in_win += int(r.output_total or 0)
                        thinking_in_win += int(r.thinking_tokens or 0)

        used_pct = 100.0 - pct_left_clamped
        # Empirische Kapazitäts-Ableitung wenn sinnvolle Daten vorliegen (z. B. > 20 CU verbraucht)
        if used_pct >= 2.0 and consumed_in_win >= 20.0 and limit_cu is None:
            inferred = consumed_in_win / (used_pct / 100.0)
            # Plausibilitäts-Grenze (zwischen 200 und 200.000 CU)
            if 200.0 <= inferred <= 200_000.0:
                p_cfg[f"limit_{horizon}_cu"] = round(inferred, 1)

        src_clean = str(source or "manual")
        anchor_data: dict[str, Any] = {
            "snapshot_at": now.isoformat(),
            "resets_at": resets_at_dt.isoformat(),
            "pct_left": round(pct_left_clamped, 2),
            "measured_cu_in_window": round(consumed_in_win, 1),
            "source": src_clean,
        }
        if ring_pct is not None:
            anchor_data["ring_pct"] = round(float(ring_pct), 1)
        if ring_color is not None:
            anchor_data["ring_color"] = str(ring_color)
        if ocr_pct is not None:
            anchor_data["ocr_pct"] = round(float(ocr_pct), 1)
        if ring_pct is not None and ocr_pct is not None:
            anchor_data["ocr_ring_diff"] = round(float(ocr_pct) - float(ring_pct), 1)

        p_cfg[f"anchor_{horizon}"] = anchor_data

        hist = list(p_cfg.get("ring_history") or [])
        hist_entry: dict[str, Any] = {
            "ts": now.isoformat(),
            "horizon": horizon,
            "pct_left": round(pct_left_clamped, 2),
            "resets_at": resets_at_dt.isoformat(),
            "cu_in_win": round(consumed_in_win, 1),
            "reqs_in_win": reqs_in_win,
            "fresh_in_win": fresh_in_win,
            "cache_read_in_win": cache_read_in_win,
            "output_in_win": output_in_win,
            "thinking_in_win": thinking_in_win,
            "source": src_clean,
        }
        if ring_pct is not None:
            hist_entry["ring_pct"] = round(float(ring_pct), 1)
        if ring_color is not None:
            hist_entry["ring_color"] = str(ring_color)
        if ocr_pct is not None:
            hist_entry["ocr_pct"] = round(float(ocr_pct), 1)
        if ring_pct is not None and ocr_pct is not None:
            hist_entry["ocr_ring_diff"] = round(float(ocr_pct) - float(ring_pct), 1)
        if selected_model:
            hist_entry["selected_model"] = str(selected_model)
        if selected_effort:
            hist_entry["selected_effort"] = str(selected_effort)
        hist.append(hist_entry)
        p_cfg["ring_history"] = hist[-MAX_RING_HISTORY:]

    save_calibration(cfg, cal_path)
    return cfg


def update_settings(
    updates: dict[str, Any],
    cal_path: Path | None = None,
) -> dict[str, Any]:
    """Aktualisiert allgemeine Einstellungen (z. B. Eco-Toggle, Kurs, Custom-Modelle)."""
    cfg = load_calibration(cal_path)
    if "show_eco_metrics" in updates:
        cfg["show_eco_metrics"] = bool(updates["show_eco_metrics"])
    if "usd_to_chf" in updates and float(updates["usd_to_chf"]) > 0:
        cfg["usd_to_chf"] = float(updates["usd_to_chf"])
    if "currency" in updates and str(updates["currency"]).strip():
        cfg["currency"] = str(updates["currency"]).strip().upper()
    if "custom_models" in updates and isinstance(updates["custom_models"], dict):
        cfg["custom_models"] = {**cfg.get("custom_models", {}), **updates["custom_models"]}
    if "new_pool" in updates and isinstance(updates["new_pool"], dict):
        np_d = updates["new_pool"]
        pid = str(np_d.get("pool_id") or np_d.get("id") or "").strip().lower()
        if pid:
            cfg["pools"][pid] = {
                "label": str(np_d.get("label") or pid),
                "has_5h": bool(np_d.get("has_5h", True)),
                "has_weekly": bool(np_d.get("has_weekly", True)),
                "limit_5h_cu": float(np_d.get("limit_5h_cu", 2500.0)),
                "limit_weekly_cu": float(np_d.get("limit_weekly_cu", 15000.0)),
                "color": str(np_d.get("color") or "#10b981"),
                "anchor_5h": None,
                "anchor_weekly": None,
                "ring_history": [],
            }
    save_calibration(cfg, cal_path)
    return cfg


# -----------------------------------------------------------------------------
# Version-Matching & Fading-Volume-Plot
# -----------------------------------------------------------------------------

def _match_versions_to_turns(
    project_name: str,
    turns: list[EvaluatedTurn],
    projects_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Verknüpft history/vNNN Versionen eines Teils zeitlich mit den passenden Chat-Turns."""
    p_dir = (projects_root or PROJECTS_DIR) / project_name
    if not p_dir.is_dir():
        return []
    try:
        versions = list_versions(str(p_dir))
    except Exception:
        return []

    if not versions:
        return []

    enriched_versions: list[dict[str, Any]] = []
    for v in versions:
        v_name = v.get("version", "")
        ts_utc = _local_naive_to_utc(v.get("timestamp", ""))
        stl_rel = "print.stl" if (p_dir / "history" / v_name / "print.stl").is_file() else "model.stl"
        v_item = {
            "version": v_name,
            "number": v.get("number", 0),
            "timestamp": v.get("timestamp", ""),
            "timestamp_utc": ts_utc.isoformat() if ts_utc else None,
            "passed": bool(v.get("passed", True)),
            "size_mm": v.get("size_mm", [0, 0, 0]),
            "volume_mm3": v.get("volume_mm3", 0),
            "errors_count": int(v.get("errors_count", 0)),
            "warnings_count": int(v.get("warnings_count", 0)),
            "reason": v.get("reason", ""),
            "stl_url": f"/api/projects/{project_name}/files/history/{v_name}/{stl_rel}",
            "_ts_dt": ts_utc,
        }
        enriched_versions.append(v_item)

    if not turns:
        for v_item in enriched_versions:
            v_item.pop("_ts_dt", None)
        return enriched_versions

    # Zeitliche Zuordnung: Turn i deckt [turn_i.ts - 5s, turn_{i+1}.ts) ab
    unmatched: list[dict[str, Any]] = []
    sorted_turns = sorted(
        [t for t in turns if t.timestamp is not None],
        key=lambda t: t.timestamp,  # type: ignore[arg-type]
    )

    for v_item in enriched_versions:
        v_ts = v_item.pop("_ts_dt", None)
        matched = False
        if v_ts and sorted_turns:
            for i, t in enumerate(sorted_turns):
                assert t.timestamp is not None
                t_start = t.timestamp - dt.timedelta(seconds=5)
                t_end = sorted_turns[i + 1].timestamp if i + 1 < len(sorted_turns) else None
                if v_ts >= t_start and (t_end is None or v_ts < t_end):
                    t.versions.append(v_item)
                    matched = True
                    break
        if not matched:
            unmatched.append(v_item)

    return unmatched


def _build_fading_volume_plot(
    all_requests: list[tuple[dt.datetime, str, ModelSpec, CostBreakdown]],
    pools_status: dict[str, dict[str, Any]],
    now_utc: dt.datetime,
    days: float = 7.0,
) -> dict[str, Any]:
    """Erstellt die Zeitreihen für den Fading-Volume-Plot je Pool (7-Tage-Verlauf)."""
    cutoff = now_utc - dt.timedelta(days=days)
    recent = sorted(
        [(ts, pid, spec, cost) for ts, pid, spec, cost in all_requests if ts >= cutoff],
        key=lambda x: x[0],
    )

    # Gruppierung in Zeit-Bins (z. B. 4h-Blöcke oder pro Session-Burst)
    per_pool_series: dict[str, list[dict[str, Any]]] = {}
    model_totals: dict[str, dict[str, Any]] = {}

    for pid in pools_status:
        per_pool_series[pid] = []

    running_cu: dict[str, float] = {pid: 0.0 for pid in pools_status}
    for ts, pid, spec, cost in recent:
        pstat = pools_status.get(pid)
        if not pstat or not pstat.get("weekly"):
            continue
        limit_wk = float(pstat["weekly"]["limit_cu"] or 15000.0)
        running_cu[pid] = running_cu.get(pid, 0.0) + cost.cu
        used_pct = min(100.0, running_cu[pid] / limit_wk * 100.0)
        rem_pct = max(0.0, 100.0 - used_pct)
        # Fading-Opacity nimmt bei sinkendem Restlimit ab (0.20 bis 1.00)
        fade_alpha = round(0.20 + 0.80 * (rem_pct / 100.0), 3)
        # Effektives Nutzungsvolumen = Tokens pro verbrauchtem 1%-Limit (Skalierungsmaß!)
        tokens_per_pct = (cost.total_tokens / max(0.001, (cost.cu / limit_wk * 100.0)))

        per_pool_series[pid].append({
            "ts": ts.isoformat(),
            "model_key": spec.key,
            "model_name": spec.display_name,
            "cu": round(cost.cu, 2),
            "tokens": cost.total_tokens,
            "rem_pct": round(rem_pct, 2),
            "fade_alpha": fade_alpha,
            "effective_tokens_per_pct": round(tokens_per_pct, 0),
        })

        mk = model_totals.setdefault(spec.key, {
            "model_key": spec.key,
            "model_name": spec.display_name,
            "pool_id": pid,
            "tokens": 0,
            "cu": 0.0,
            "cost_chf": 0.0,
            "calls": 0,
        })
        mk["tokens"] += cost.total_tokens
        mk["cu"] += cost.cu
        mk["cost_chf"] += cost.cost_chf
        mk["calls"] += 1

    # Tokens pro 1% Limit (Vergleich: wie viel mehr "Nutzungsvolumen" gibt Flash vs Argon?)
    for mk in model_totals.values():
        pstat = pools_status.get(mk["pool_id"], {})
        wk_lim = float((pstat.get("weekly") or {}).get("limit_cu") or 15000.0)
        pct_consumed = mk["cu"] / wk_lim * 100.0 if wk_lim > 0 else 0.0
        mk["pct_weekly"] = round(pct_consumed, 2)
        mk["tokens_per_pct"] = round(mk["tokens"] / pct_consumed, 0) if pct_consumed > 0.001 else 0
        mk["cu"] = round(mk["cu"], 1)
        mk["cost_chf"] = round(mk["cost_chf"], 3)

    return {
        "days": days,
        "series_by_pool": per_pool_series,
        "model_summary": sorted(model_totals.values(), key=lambda x: x["cu"], reverse=True),
    }


# -----------------------------------------------------------------------------
# Öffentliche High-Level API für Studio & CLI
# -----------------------------------------------------------------------------

def _cost_to_dict(
    c: CostBreakdown,
    pool_status: dict[str, Any] | None,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    lim_5h = float(((pool_status or {}).get("h5") or {}).get("limit_cu") or 0.0)
    lim_wk = float(((pool_status or {}).get("weekly") or {}).get("limit_cu") or 0.0)
    pct_5h = round(c.cu / lim_5h * 100.0, 2) if lim_5h > 0 else None
    pct_wk = round(c.cu / lim_wk * 100.0, 2) if lim_wk > 0 else None

    return {
        "fresh_in": c.fresh_in,
        "cache_read": c.cache_read,
        "output_total": c.output_total,
        "thinking_tokens": c.thinking_tokens,
        "visible_output": c.visible_output,
        "total_tokens": c.total_tokens,
        "cost_usd": round(c.cost_usd, 4),
        "cost_chf": round(c.cost_chf, 4),
        "cu": round(c.cu, 2),
        "pct_5h": pct_5h,
        "pct_weekly": pct_wk,
        "energy_wh": round(c.energy_wh, 3),
        "water_ml": round(c.water_ml, 3),
        "currency": cfg.get("currency", "CHF"),
    }


def _turn_to_dict(
    t: EvaluatedTurn,
    pools_status: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    pstat = pools_status.get(t.pool_id)
    return {
        "turn_number": t.turn_number,
        "conversation_id": t.conversation_id,
        "execution_id": t.execution_id,
        "timestamp": t.timestamp.isoformat() if t.timestamp else None,
        "prompt_short": t.prompt_short,
        "prompt_full": t.prompt_full,
        "project_name": t.project_name,
        "model_key": t.model_key,
        "model_display": t.model_display,
        "pool_id": t.pool_id,
        "effort": t.effort,
        "planner_calls": t.planner_calls,
        "subagent_calls": t.subagent_calls,
        "subagent_ids": t.subagent_ids,
        "cache_hit_pct": t.cache_hit_pct,
        "cache_break": t.cache_break,
        "thinking_share_pct": t.thinking_share_pct,
        "context_used": t.context_used,
        "context_limit": t.context_limit,
        "prompt_cost": _cost_to_dict(t.prompt_cost, pstat, cfg),
        "answer_cost": _cost_to_dict(t.answer_cost, pstat, cfg),
        "total_cost": _cost_to_dict(t.total_cost, pstat, cfg),
        "versions": t.versions,
    }


def build_usage_overview(
    days: float = 14.0,
    agy_dir: Path | None = None,
    projects_root: Path | None = None,
    cal_path: Path | None = None,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    """Erstellt die komplette Account- und Workspace-Übersicht inkl. Teile & Versionen."""
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    base = agy_dir or default_antigravity_dir()
    p_root = projects_root or PROJECTS_DIR
    cfg = load_calibration(cal_path)
    overrides = cfg.get("custom_models") or {}

    summaries = load_conversation_summaries(base)
    children_map = _build_children_map(summaries)
    recent_ids = list_recent_conversation_ids(now - dt.timedelta(days=days), base)

    # 1. Alle Requests für Pool- & Fading-Plot über ALLE Workspaces einsammeln
    all_requests: list[tuple[dt.datetime, str, ModelSpec, CostBreakdown]] = []
    # Root-Konversationen (keine Subagenten!) für Workspace- & Teile-Ansicht
    root_convs: list[str] = []
    latest_req_info: dict[str, Any] | None = None
    latest_req_ts: dt.datetime | None = None
    for cid in recent_ids:
        s = summaries.get(cid)
        if s and s.parent_conversation_id:
            continue
        root_convs.append(cid)
        # Rekursive Requests aus diesem Chat inkl. Subagenten sammeln
        stack = [cid]
        seen_sub: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen_sub:
                continue
            seen_sub.add(cur)
            cdata = load_conversation(cur, base, summaries)
            if cdata:
                for req in cdata.requests:
                    if req.timestamp is None:
                        continue
                    spec = resolve_model_spec(req.model_display, req.model_slug, req.model_enum, overrides)
                    cost = compute_token_cost(
                        req.fresh_in, req.cache_read, req.output_total,
                        req.thinking_tokens, req.visible_output, spec, cfg,
                    )
                    all_requests.append((req.timestamp, spec.pool_id, spec, cost))
                    if latest_req_ts is None or req.timestamp > latest_req_ts:
                        latest_req_ts = req.timestamp
                        eff = extract_model_effort(req.model_display, req.model_slug, req.model_enum)
                        disp = req.model_display or (f"{spec.display_name} ({eff})" if eff else spec.display_name)
                        latest_req_info = {
                            "model_name": spec.display_name,
                            "model_display": disp,
                            "effort": eff,
                            "pool_id": spec.pool_id,
                            "timestamp": req.timestamp.isoformat(),
                        }
            stack.extend(children_map.get(cur, []))

    pools_status = {
        pid: _compute_pool_status(pid, pcfg, all_requests, now)
        for pid, pcfg in cfg["pools"].items()
    }

    # 2. Turns je Root-Konversation auswerten & nach Workspaces + Projekten gruppieren
    known_projects = [p.name for p in p_root.iterdir() if p.is_dir()] if p_root.is_dir() else []
    workspaces: dict[str, dict[str, Any]] = {}
    project_turns_map: dict[str, list[EvaluatedTurn]] = {}
    project_ws_map: dict[str, str] = {}
    conv_items: list[dict[str, Any]] = []

    for cid in root_convs:
        turns = evaluate_conversation_turns(cid, cfg, base, summaries, children_map, known_projects)
        if not turns:
            continue
        csum = summaries.get(cid)
        ws_name = csum.workspace_name if csum else "unbekannt"
        ws_entry = workspaces.setdefault(ws_name, {
            "workspace": ws_name,
            "projects": {},
            "conversations": [],
            "total_cu": 0.0,
            "total_cost_chf": 0.0,
            "total_tokens": 0,
        })

        conv_tot = CostBreakdown()
        dom_pool = turns[-1].pool_id
        proj_names_in_conv: list[str] = []
        for t in turns:
            conv_tot = conv_tot + t.total_cost
            if t.project_name:
                project_turns_map.setdefault(t.project_name, []).append(t)
                project_ws_map.setdefault(t.project_name, ws_name)
                if t.project_name not in proj_names_in_conv:
                    proj_names_in_conv.append(t.project_name)

        ws_entry["total_cu"] += conv_tot.cu
        ws_entry["total_cost_chf"] += conv_tot.cost_chf
        ws_entry["total_tokens"] += conv_tot.total_tokens

        c_item = {
            "conversation_id": cid,
            "title": (csum.title if csum and csum.title else turns[0].prompt_short),
            "workspace": ws_name,
            "last_modified": (csum.last_modified.isoformat() if csum and csum.last_modified else (
                turns[-1].timestamp.isoformat() if turns[-1].timestamp else None
            )),
            "turns_count": len(turns),
            "planner_calls": sum(t.planner_calls for t in turns),
            "subagent_calls": sum(t.subagent_calls for t in turns),
            "dominant_pool": dom_pool,
            "projects": proj_names_in_conv,
            "cost": _cost_to_dict(conv_tot, pools_status.get(dom_pool), cfg),
        }
        ws_entry["conversations"].append(c_item)
        conv_items.append(c_item)

    # 3. Auch Projekte auf der Festplatte (mit history/) einsammeln
    if p_root.is_dir():
        for pd in p_root.iterdir():
            if pd.is_dir() and (pd / "history").is_dir():
                project_turns_map.setdefault(pd.name, [])
                project_ws_map.setdefault(pd.name, p_root.parent.name)

    projects_index: dict[str, dict[str, Any]] = {}
    for pname, pturns in project_turns_map.items():
        pturns.sort(key=lambda t: t.timestamp or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
        unmatched_versions = _match_versions_to_turns(pname, pturns, p_root)
        all_ver_count = sum(len(t.versions) for t in pturns) + len(unmatched_versions)

        ptot = CostBreakdown()
        pool_cu: dict[str, float] = {}
        for t in pturns:
            ptot = ptot + t.total_cost
            pool_cu[t.pool_id] = pool_cu.get(t.pool_id, 0.0) + t.total_cost.cu

        dom_pool = max(pool_cu, key=pool_cu.get) if pool_cu else "gemini"
        ws_name = project_ws_map.get(pname, p_root.parent.name)

        # Druckkosten aus kosten.py falls gesliced
        print_cost = None
        try:
            if (p_root / pname).is_dir():
                kc = slice_costs_for_project(str(p_root / pname))
                if isinstance(kc, dict) and kc.get("total") is not None:
                    print_cost = {
                        "total_chf": kc.get("total"),
                        "gramm": kc.get("gramm"),
                        "zeit": kc.get("zeit"),
                        "waehrung": kc.get("waehrung", "CHF"),
                    }
        except Exception:
            pass

        p_obj = {
            "project_name": pname,
            "workspace": ws_name,
            "turns_count": len(pturns),
            "versions_count": all_ver_count,
            "dominant_pool": dom_pool,
            "cost": _cost_to_dict(ptot, pools_status.get(dom_pool), cfg),
            "print_cost": print_cost,
            "last_activity": pturns[-1].timestamp.isoformat() if pturns and pturns[-1].timestamp else None,
        }
        projects_index[pname] = p_obj
        ws_entry = workspaces.setdefault(ws_name, {
            "workspace": ws_name,
            "projects": {},
            "conversations": [],
            "total_cu": 0.0,
            "total_cost_chf": 0.0,
            "total_tokens": 0,
        })
        ws_entry["projects"][pname] = p_obj

    # Workspace-Summaries aufräumen und sortieren
    ws_list = []
    for ws in workspaces.values():
        ws["total_cu"] = round(ws["total_cu"], 1)
        ws["total_cost_chf"] = round(ws["total_cost_chf"], 3)
        ws["projects_list"] = sorted(
            ws["projects"].values(),
            key=lambda x: (x["last_activity"] or "", x["versions_count"]),
            reverse=True,
        )
        ws_list.append(ws)
    ws_list.sort(key=lambda w: w["total_cu"], reverse=True)

    # 4. Reichweiten-Schätzung je Pool anhand durchschnittlicher Teil-CU
    cad_cus = [p["cost"]["cu"] for p in projects_index.values() if p["cost"]["cu"] > 20]
    ref_part_cu = (sum(cad_cus) / len(cad_cus)) if cad_cus else 650.0
    for pid, pstat in pools_status.items():
        if pstat["h5"]:
            pstat["h5"]["parts_remaining_est"] = round(pstat["h5"]["left_cu"] / ref_part_cu, 1) if ref_part_cu > 0 else 0.0
        if pstat["weekly"]:
            pstat["weekly"]["parts_remaining_est"] = round(pstat["weekly"]["left_cu"] / ref_part_cu, 1) if ref_part_cu > 0 else 0.0

    plot_data = _build_fading_volume_plot(all_requests, pools_status, now, days=min(days, 7.0))

    dp_total = sum((p.get("data_points") or {}).get("total", 0) for p in pools_status.values())
    dp_intervals = sum((p.get("data_points") or {}).get("intervals_total", 0) for p in pools_status.values())

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "settings": {
            "currency": cfg["currency"],
            "usd_to_chf": cfg["usd_to_chf"],
            "show_eco_metrics": cfg["show_eco_metrics"],
            "custom_models": cfg.get("custom_models", {}),
        },
        "pools": pools_status,
        "active_model": latest_req_info,
        "data_points_summary": {
            "total_points": dp_total,
            "total_intervals": dp_intervals,
        },
        "reference_part_cu": round(ref_part_cu, 1),
        "fading_plot": plot_data,
        "workspaces": ws_list,
        "projects": list(projects_index.values()),
        "conversations": conv_items[:40],
    }


def build_project_detail(
    project_name: str,
    agy_dir: Path | None = None,
    projects_root: Path | None = None,
    cal_path: Path | None = None,
    days: float = 30.0,
) -> dict[str, Any]:
    """Liefert die vollständige Phone-Messaging-Chat-Historie & Iterationen für ein Teil."""
    base = agy_dir or default_antigravity_dir()
    p_root = projects_root or PROJECTS_DIR
    cfg = load_calibration(cal_path)
    now = dt.datetime.now(dt.timezone.utc)

    overview = build_usage_overview(days=days, agy_dir=base, projects_root=p_root, cal_path=cal_path, now_utc=now)
    pools_status = overview["pools"]

    summaries = load_conversation_summaries(base)
    children_map = _build_children_map(summaries)
    recent_ids = list_recent_conversation_ids(now - dt.timedelta(days=days), base)

    known_projects = [p.name for p in p_root.iterdir() if p.is_dir()] if p_root.is_dir() else []
    matched_turns: list[EvaluatedTurn] = []
    for cid in recent_ids:
        s = summaries.get(cid)
        if s and s.parent_conversation_id:
            continue
        turns = evaluate_conversation_turns(cid, cfg, base, summaries, children_map, known_projects)
        for t in turns:
            if t.project_name == project_name:
                matched_turns.append(t)

    matched_turns.sort(key=lambda t: t.timestamp or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    unmatched_versions = _match_versions_to_turns(project_name, matched_turns, p_root)

    tot_cost = CostBreakdown()
    pool_cu: dict[str, float] = {}
    for t in matched_turns:
        tot_cost = tot_cost + t.total_cost
        pool_cu[t.pool_id] = pool_cu.get(t.pool_id, 0.0) + t.total_cost.cu
    dom_pool = max(pool_cu, key=pool_cu.get) if pool_cu else "gemini"

    # Was-wäre-wenn Simulation über alle registrierten Modelle
    sim: list[dict[str, Any]] = []
    for mkey, spec in MODEL_SPECS.items():
        # Bei alternativen Modellen geschätzter Thinking-Multiplikator auf sichtbarem/thinking Output
        sim_out = int(tot_cost.visible_output + tot_cost.thinking_tokens * spec.thinking_effort_factor)
        sim_c = compute_token_cost(
            tot_cost.fresh_in, tot_cost.cache_read, sim_out,
            int(tot_cost.thinking_tokens * spec.thinking_effort_factor),
            tot_cost.visible_output, spec, cfg,
        )
        pstat = pools_status.get(spec.pool_id)
        d = _cost_to_dict(sim_c, pstat, cfg)
        d.update({"model_key": mkey, "display_name": spec.display_name, "pool_id": spec.pool_id})
        sim.append(d)

    # Druckkosten
    print_cost = None
    try:
        if (p_root / project_name).is_dir():
            kc = slice_costs_for_project(str(p_root / project_name))
            if isinstance(kc, dict) and kc.get("total") is not None:
                print_cost = {"ok": True, **kc, "total_chf": kc.get("total")}
    except Exception:
        pass

    return {
        "ok": True,
        "kind": "project",
        "project_name": project_name,
        "dominant_pool": dom_pool,
        "summary": _cost_to_dict(tot_cost, pools_status.get(dom_pool), cfg),
        "print_cost": print_cost,
        "turns": [_turn_to_dict(t, pools_status, cfg) for t in matched_turns],
        "unmatched_versions": unmatched_versions,
        "simulation": sim,
        "settings": overview["settings"],
    }


def build_conversation_detail(
    conv_id: str,
    agy_dir: Path | None = None,
    projects_root: Path | None = None,
    cal_path: Path | None = None,
) -> dict[str, Any]:
    """Liefert den Phone-Messaging-Chat einer beliebigen Konversation (auch Nicht-CAD-Workspaces)."""
    base = agy_dir or default_antigravity_dir()
    p_root = projects_root or PROJECTS_DIR
    cfg = load_calibration(cal_path)
    now = dt.datetime.now(dt.timezone.utc)
    overview = build_usage_overview(days=14.0, agy_dir=base, projects_root=p_root, cal_path=cal_path, now_utc=now)
    pools_status = overview["pools"]

    summaries = load_conversation_summaries(base)
    children_map = _build_children_map(summaries)
    known_projects = [p.name for p in p_root.iterdir() if p.is_dir()] if p_root.is_dir() else []
    turns = evaluate_conversation_turns(conv_id, cfg, base, summaries, children_map, known_projects)

    # Falls Turns auf CAD-Projekte verweisen, deren Versionen zuordnen
    by_proj: dict[str, list[EvaluatedTurn]] = {}
    for t in turns:
        if t.project_name:
            by_proj.setdefault(t.project_name, []).append(t)
    for pname, pturns in by_proj.items():
        _match_versions_to_turns(pname, pturns, p_root)

    tot_cost = CostBreakdown()
    pool_cu: dict[str, float] = {}
    for t in turns:
        tot_cost = tot_cost + t.total_cost
        pool_cu[t.pool_id] = pool_cu.get(t.pool_id, 0.0) + t.total_cost.cu
    dom_pool = max(pool_cu, key=pool_cu.get) if pool_cu else "gemini"
    csum = summaries.get(conv_id)

    return {
        "ok": True,
        "kind": "conversation",
        "conversation_id": conv_id,
        "title": (csum.title if csum and csum.title else (turns[0].prompt_short if turns else conv_id)),
        "workspace": csum.workspace_name if csum else "unbekannt",
        "dominant_pool": dom_pool,
        "summary": _cost_to_dict(tot_cost, pools_status.get(dom_pool), cfg),
        "turns": [_turn_to_dict(t, pools_status, cfg) for t in turns],
        "unmatched_versions": [],
        "settings": overview["settings"],
    }
