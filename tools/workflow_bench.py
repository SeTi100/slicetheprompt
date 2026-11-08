#!/usr/bin/env python3
"""workflow_bench.py – Antigravity-Benchmark- & Analyse-Tool für transcript.jsonl.

Liest eine Antigravity transcript.jsonl (oder transcript_full.jsonl) ein und wertet
pro User-Turn sowie über den gesamten Verlauf aus:
  * Gesamtdauer (Sekunden / mm:ss)
  * Rundenanzahl (PLANNER_RESPONSE) & Tool-Call-Anzahl
  * Planner-Denkzeit vs. Tool-/Wartezeit
  * Fehlgeschlagene Tool-Calls (status == 'ERROR' oder 'ok': false / Exit-Code != 0)
  * Unnötige Schema- und Explorations-Aufrufe (*.json unter mcp/, git, Get-ChildItem)
  * Token-Statistik (Max Input, Summe Output, Summe Thinking)
  * Kompakte Schritt-für-Schritt-Tabelle (#, Dauer, Phase/Tool, Status, Kurz-Info)

Verwendung:
  uv run python tools/workflow_bench.py <pfad/zu/transcript.jsonl>
  uv run python tools/workflow_bench.py <brain-verzeichnis-oder-conversation-id>
  uv run python tools/workflow_bench.py --help
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


# -----------------------------------------------------------------------------
# Zeit- und Parsing-Hilfen
# -----------------------------------------------------------------------------

def parse_iso_ts(ts_str: str | None) -> datetime | None:
    """Parst ISO-8601-Zeitstempel (UTC oder mit Offset)."""
    if not ts_str:
        return None
    try:
        cleaned = ts_str.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def format_duration(seconds: float) -> str:
    """Formatiert Sekunden lesbar: '4.2s' oder '2m 14s'."""
    if seconds < 0:
        return "0.0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m {s:04.1f}s"


def format_tokens(n: int) -> str:
    """Formatiert Token-Zahlen mit Tausendertrennpunkten."""
    return f"{n:,}".replace(",", ".")


# -----------------------------------------------------------------------------
# Explorations- und Fehler-Detektoren
# -----------------------------------------------------------------------------

MCP_SCHEMA_PATTERN = re.compile(r"mcp[/\\][a-zA-Z0-9_\-]+[/\\][a-zA-Z0-9_\-]+\.json", re.IGNORECASE)
GIT_COMMAND_PATTERN = re.compile(r"\bgit\s+(status|grep|log|show|diff|branch|checkout)\b", re.IGNORECASE)
EXPLORATORY_DIR_PATTERN = re.compile(r"\b(Get-ChildItem|dir\s+|ls\s+|find\s+)", re.IGNORECASE)


def classify_exploration(tool_name: str, args: dict[str, Any]) -> str | None:
    """Erkennt unnötige Schema-Reads und ungerichtete Repository-Explorationen."""
    # 1. MCP-Schema-Reads
    if tool_name == "read_file":
        path_str = str(args.get("Path") or args.get("path") or "")
        if MCP_SCHEMA_PATTERN.search(path_str):
            return f"MCP-Schema-Read: {Path(path_str).name}"
        if "TODO.md" in path_str or "SPEZIFIKATION" in path_str:
            return f"Repo-Archäologie: {Path(path_str).name}"

    # 2. CLI-Befehle (git, Get-ChildItem, dir)
    if tool_name == "run_command":
        cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip()
        m_git = GIT_COMMAND_PATTERN.search(cmd)
        if m_git:
            return f"Git-Exploration: '{m_git.group(0)}'"
        m_dir = EXPLORATORY_DIR_PATTERN.search(cmd)
        if m_dir:
            return f"Dateisystem-Scan: '{m_dir.group(0).strip()}'"

    # 3. Unnötige project_list-Aufrufe (wenn used_in_assemblies vorhanden ist)
    if tool_name in ("call_mcp_tool", "mcp_slicetheprompt_project_list"):
        mcp_tool = args.get("ToolName") if tool_name == "call_mcp_tool" else "project_list"
        if mcp_tool == "project_list":
            return "MCP project_list (Baugruppen-Suche per Tool)"

    # 4. Task-Polling
    if tool_name == "manage_task":
        action = args.get("Action")
        if action == "status":
            return "Task-Polling: manage_task(status)"

    return None


def is_tool_failure(step: dict[str, Any]) -> tuple[bool, str]:
    """Prüft, ob ein Tool-Ergebnis einen Fehler meldet."""
    # Expliziter Fehler-Status
    if step.get("status") == "ERROR":
        err_msg = step.get("error") or "Fehler in Step-Ausführung"
        return True, str(err_msg).strip()

    content = str(step.get("content") or "")

    # Tool-Rückgabewerte mit "ok": false
    if '"ok": false' in content or '"ok":false' in content:
        # Versuche eine kurze Fehlermeldung zu extrahieren
        m_err = re.search(r'"(?:error|message)":\s*"([^"]+)"', content)
        detail = m_err.group(1) if m_err else '"ok": false im Tool-Ergebnis'
        return True, detail

    # Command exited with code != 0
    m_exit = re.search(r"Command exited with code ([1-9]\d*)", content)
    if m_exit:
        return True, f"Befehl beendet mit Exit-Code {m_exit.group(1)}"

    # Typische Python-Tracebacks / Exceptions
    if "Traceback (most recent call last):" in content:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        last_line = lines[-1] if lines else "Traceback"
        return True, last_line

    if "Encountered error in step execution:" in content:
        m_enc = re.search(r"Encountered error in step execution:\s*(.+)", content)
        return True, m_enc.group(1).strip() if m_enc else "Step execution error"

    return False, ""


# -----------------------------------------------------------------------------
# Analyse-Strukturen
# -----------------------------------------------------------------------------

class StepInfo:
    def __init__(self, raw: dict[str, Any], idx: int):
        self.raw = raw
        self.step_index = raw.get("step_index", idx)
        self.source = raw.get("source", "")
        self.type = raw.get("type", "")
        self.status = raw.get("status", "DONE")
        self.created_at = parse_iso_ts(raw.get("created_at"))
        self.duration_seconds: float = 0.0

        # Token
        self.input_tokens = int(raw.get("input_tokens") or 0)
        self.cache_read_tokens = int(raw.get("cache_read_tokens") or 0)
        self.output_tokens = int(raw.get("output_tokens") or 0)
        self.thinking = raw.get("thinking") or ""
        self.thinking_tokens = int(raw.get("thinking_tokens") or 0)
        if not self.thinking_tokens and self.thinking:
            # Schätzung falls nicht separat ausgewiesen
            self.thinking_tokens = max(1, len(re.findall(r"\w+|[^\w\s]", self.thinking)))

        # Tool Calls
        self.tool_calls: list[dict[str, Any]] = raw.get("tool_calls") or []

        # Klassifizierungen
        self.is_failure, self.failure_reason = is_tool_failure(raw)
        self.explorations: list[str] = []
        for tc in self.tool_calls:
            tname = tc.get("name", "")
            targs = tc.get("args") or {}
            # Falls args als JSON-String kodiert ist
            if isinstance(targs, str):
                try:
                    targs = json.loads(targs)
                except Exception:
                    targs = {"_raw": targs}
            exp = classify_exploration(tname, targs)
            if exp:
                self.explorations.append(exp)


class TurnInfo:
    def __init__(self, turn_number: int, user_prompt: str, start_time: datetime | None):
        self.turn_number = turn_number
        self.user_prompt = user_prompt.strip()
        self.start_time = start_time
        self.steps: list[StepInfo] = []

    @property
    def total_duration(self) -> float:
        return sum(s.duration_seconds for s in self.steps)

    @property
    def planner_steps(self) -> list[StepInfo]:
        return [s for s in self.steps if s.type == "PLANNER_RESPONSE"]

    @property
    def tool_result_steps(self) -> list[StepInfo]:
        return [s for s in self.steps if s.type not in ("PLANNER_RESPONSE", "USER_INPUT")]

    @property
    def planner_time(self) -> float:
        return sum(s.duration_seconds for s in self.planner_steps)

    @property
    def tool_and_wait_time(self) -> float:
        return sum(s.duration_seconds for s in self.steps if s.type != "PLANNER_RESPONSE")

    @property
    def total_tool_calls(self) -> int:
        return sum(len(s.tool_calls) for s in self.planner_steps)

    @property
    def max_input_tokens(self) -> int:
        return max((s.input_tokens for s in self.planner_steps), default=0)

    @property
    def sum_output_tokens(self) -> int:
        return sum(s.output_tokens for s in self.planner_steps)

    @property
    def sum_thinking_tokens(self) -> int:
        return sum(s.thinking_tokens for s in self.planner_steps)

    @property
    def failures(self) -> list[tuple[StepInfo, str]]:
        res = []
        for s in self.steps:
            if s.is_failure:
                res.append((s, s.failure_reason))
        return res

    @property
    def explorations(self) -> list[tuple[StepInfo, str]]:
        res = []
        for s in self.steps:
            for exp in s.explorations:
                res.append((s, exp))
        return res


# -----------------------------------------------------------------------------
# Parser für Antigravity-Transkripte
# -----------------------------------------------------------------------------

def locate_transcript_file(path_input: str | Path | None) -> Path:
    """Ermittelt den absoluten Pfad zur transcript.jsonl."""
    if not path_input:
        # Default: Suche im aktuellen Arbeitsverzeichnis oder im letzten Brain-Ordner
        cwd_logs = Path("transcript.jsonl")
        if cwd_logs.is_file():
            return cwd_logs.resolve()
        raise FileNotFoundError(
            "Kein Pfad angegeben. Verwendung: uv run python tools/workflow_bench.py <pfad/zu/transcript.jsonl>"
        )

    p = Path(path_input).expanduser()

    # 1. Direkte Datei
    if p.is_file():
        return p.resolve()

    # 2. Ordner mit .system_generated/logs/transcript.jsonl
    sub = p / ".system_generated" / "logs" / "transcript.jsonl"
    if sub.is_file():
        return sub.resolve()

    # 3. Ordner mit transcript.jsonl
    sub2 = p / "transcript.jsonl"
    if sub2.is_file():
        return sub2.resolve()

    # 4. Suche in ~/.gemini/antigravity/brain/<id>/...
    brain_root = Path.home() / ".gemini" / "antigravity" / "brain"
    if brain_root.exists():
        direct_brain = brain_root / p.name / ".system_generated" / "logs" / "transcript.jsonl"
        if direct_brain.is_file():
            return direct_brain.resolve()

    raise FileNotFoundError(f"Konnte kein transcript.jsonl unter '{path_input}' finden.")


def parse_transcript(transcript_path: Path) -> list[TurnInfo]:
    """Liest die transcript.jsonl ein, berechnet Dauern und teilt in User-Turns ein."""
    raw_steps: list[dict[str, Any]] = []
    with open(transcript_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                raw_steps.append(json.loads(line))
            except Exception as e:
                # Malformed line überspringen
                continue

    if not raw_steps:
        return []

    # Schritt-Objekte erstellen
    steps: list[StepInfo] = [StepInfo(raw, i) for i, raw in enumerate(raw_steps)]

    # Schritt-Dauern berechnen (Differenz der Zeitstempel zum nächsten Schritt)
    for i in range(len(steps) - 1):
        t_curr = steps[i].created_at
        t_next = steps[i + 1].created_at
        if t_curr and t_next:
            dt = (t_next - t_curr).total_seconds()
            steps[i].duration_seconds = max(0.0, dt)
        else:
            steps[i].duration_seconds = 0.0

    # Letzter Schritt: Versuche Completed At aus dem content zu parsen
    last = steps[-1]
    if last.created_at and last.raw.get("content"):
        m_comp = re.search(r"Completed At:\s*([^\r\n]+)", str(last.raw["content"]))
        if m_comp:
            t_comp = parse_iso_ts(m_comp.group(1))
            if t_comp and t_comp >= last.created_at:
                last.duration_seconds = (t_comp - last.created_at).total_seconds()

    # In Turns aufteilen
    turns: list[TurnInfo] = []
    current_turn: TurnInfo | None = None
    turn_counter = 0

    for step in steps:
        is_turn_start = False
        prompt_text = ""

        if step.type == "USER_INPUT":
            is_turn_start = True
            prompt_text = str(step.raw.get("content") or "")
        elif step.step_index == 0 and step.type == "SYSTEM_MESSAGE":
            # Subagenten-Dispatch oder Initialer Start
            is_turn_start = True
            prompt_text = str(step.raw.get("content") or "")

        if is_turn_start:
            turn_counter += 1
            # Bereinige Prompt um <USER_REQUEST> Tags
            clean_prompt = prompt_text
            m_req = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", prompt_text, re.DOTALL)
            if m_req:
                clean_prompt = m_req.group(1).strip()
            elif "<SYSTEM_MESSAGE>" in prompt_text:
                m_sys = re.search(r"content=(.+)", prompt_text)
                clean_prompt = m_sys.group(1).strip() if m_sys else prompt_text

            first_line = clean_prompt.strip().splitlines()[0] if clean_prompt.strip() else ""
            if len(first_line) > 80:
                first_line = first_line[:77] + "..."

            current_turn = TurnInfo(turn_counter, first_line, step.created_at)
            turns.append(current_turn)

        if current_turn is None:
            turn_counter += 1
            current_turn = TurnInfo(turn_counter, "(Init)", step.created_at)
            turns.append(current_turn)

        current_turn.steps.append(step)

    return turns


# -----------------------------------------------------------------------------
# Formatierung und Berichtausgabe
# -----------------------------------------------------------------------------

def get_tool_call_summary(step: StepInfo) -> str:
    """Erzeugt eine kurze Zusammenfassung der im Schritt aufgerufenen Tools."""
    if step.type == "USER_INPUT":
        return "USER_INPUT"
    if step.type == "SYSTEM_MESSAGE":
        return "SYSTEM_MESSAGE"
    if step.type == "PLANNER_RESPONSE":
        if not step.tool_calls:
            return "Antwort an Nutzer"
        calls = []
        for tc in step.tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if name == "call_mcp_tool":
                tname = args.get("ToolName", "mcp")
                calls.append(f"mcp:{tname}")
            elif name == "read_file":
                p = Path(str(args.get("Path") or "")).name
                calls.append(f"read({p})")
            elif name == "edit_file":
                p = Path(str(args.get("Path") or "")).name
                calls.append(f"edit({p})")
            elif name == "run_command":
                cmd = str(args.get("CommandLine") or "").strip()
                short_cmd = cmd.splitlines()[0] if cmd else ""
                if len(short_cmd) > 28:
                    short_cmd = short_cmd[:25] + "..."
                calls.append(f"cmd('{short_cmd}')")
            elif name == "invoke_subagent":
                role = args.get("Role") or args.get("TypeName") or "agent"
                calls.append(f"subagent({role})")
            elif name == "manage_task":
                calls.append(f"task({args.get('Action')})")
            else:
                calls.append(name)
        return ", ".join(calls)

    # Tool-Ergebnis
    return "Tool-Ergebnis"


def print_turn_report(turn: TurnInfo) -> None:
    """Gibt den tabellarischen und metrischen Bericht für einen Turn aus."""
    print("=" * 86)
    print(f"  TURN #{turn.turn_number}: \"{turn.user_prompt}\"")
    print("=" * 86)

    # Kennzahlen
    tot_sec = turn.total_duration
    p_sec = turn.planner_time
    t_sec = turn.tool_and_wait_time
    p_pct = (p_sec / tot_sec * 100.0) if tot_sec > 0 else 0.0
    t_pct = (t_sec / tot_sec * 100.0) if tot_sec > 0 else 0.0

    print(f"  * Gesamtdauer:         {tot_sec:.1f} s ({format_duration(tot_sec)})")
    print(f"  * Runden / Calls:      {len(turn.planner_steps)} PLANNER_RESPONSE-Runden, {turn.total_tool_calls} Tool-Calls")
    print(f"  * Zeitverteilung:      Planner-Denkzeit: {p_sec:.1f} s ({p_pct:.1f}%) | Tool/Wartezeit: {t_sec:.1f} s ({t_pct:.1f}%)")
    print(f"  * Token-Statistik:     Max Input: {format_tokens(turn.max_input_tokens)} | Summe Output: {format_tokens(turn.sum_output_tokens)} | Summe Thinking: ~{format_tokens(turn.sum_thinking_tokens)}")

    # Fehlgeschlagene Tool-Calls
    failures = turn.failures
    if failures:
        print(f"  * Fehlversuche:        \033[31m{len(failures)} fehlgeschlagene Schritte\033[0m")
        for s, reason in failures:
            print(f"      - Step #{s.step_index}: {reason}")
    else:
        print("  * Fehlversuche:        0 (alle Schritte erfolgreich)")

    # Explorations-Reads
    explorations = turn.explorations
    if explorations:
        print(f"  * Unnötige Reads:      \033[33m{len(explorations)} Schema-/Explorations-Aktionen\033[0m")
        for s, exp in explorations:
            print(f"      - Step #{s.step_index}: {exp}")
    else:
        print("  * Unnötige Reads:      0 (keine unerwünschten Scans oder Schema-Reads)")

    # Kompakte Schritt-Tabelle
    print("\n  Schritt-für-Schritt-Ablauf:")
    print("  " + "-" * 82)
    print(f"  {'#':<4} | {'Dauer':<7} | {'Typ':<17} | {'Status':<6} | {'Aktion / Aufruf'}")
    print("  " + "-" * 82)

    for s in turn.steps:
        dur_str = f"{s.duration_seconds:.1f}s"
        stat_color = "\033[32mOK\033[0m" if not s.is_failure else "\033[31mERR\033[0m"
        stat_plain = "OK" if not s.is_failure else "ERR"

        # Farbformatierung für interaktive Terminals
        type_str = s.type
        if len(type_str) > 16:
            type_str = type_str[:15] + "…"

        summary = get_tool_call_summary(s)
        if len(summary) > 42:
            summary = summary[:39] + "..."

        status_display = stat_plain
        if sys.stdout.isatty():
            status_display = stat_color

        print(f"  {s.step_index:<4} | {dur_str:<7} | {type_str:<17} | {status_display:<6} | {summary}")

    print("  " + "-" * 82 + "\n")


def print_overall_summary(turns: list[TurnInfo]) -> None:
    """Gibt die Gesamtstatistik über die gesamte Konversation aus."""
    if not turns:
        print("Keine Turns vorhanden.")
        return

    all_steps = [s for t in turns for s in t.steps]
    total_dur = sum(s.duration_seconds for s in all_steps)
    all_planner = [s for s in all_steps if s.type == "PLANNER_RESPONSE"]
    tot_planner_time = sum(s.duration_seconds for s in all_planner)
    tot_tool_time = sum(s.duration_seconds for s in all_steps if s.type != "PLANNER_RESPONSE")
    tot_calls = sum(len(s.tool_calls) for s in all_planner)
    max_input = max((s.input_tokens for s in all_planner), default=0)
    sum_output = sum(s.output_tokens for s in all_planner)
    sum_thinking = sum(s.thinking_tokens for s in all_planner)
    all_failures = [f for t in turns for f in t.failures]
    all_explorations = [e for t in turns for e in t.explorations]

    p_pct = (tot_planner_time / total_dur * 100.0) if total_dur > 0 else 0.0
    t_pct = (tot_tool_time / total_dur * 100.0) if total_dur > 0 else 0.0

    print("=" * 86)
    print(f"  GESAMT-BENCHMARK ({len(turns)} Turns, {len(all_steps)} Schritte)")
    print("=" * 86)
    print(f"  * Gesamtdauer:         {total_dur:.1f} s ({format_duration(total_dur)})")
    print(f"  * Planner vs. Tools:   Planner: {tot_planner_time:.1f} s ({p_pct:.1f}%) | Tools/Warten: {tot_tool_time:.1f} s ({t_pct:.1f}%)")
    print(f"  * Runden & Calls:      {len(all_planner)} PLANNER_RESPONSE-Runden, {tot_calls} Tool-Calls (Schnitt: {len(all_planner)/len(turns):.1f} Runden/Turn)")
    print(f"  * Token-Gesamt:        Max Input: {format_tokens(max_input)} | Summe Output: {format_tokens(sum_output)} | Thinking: ~{format_tokens(sum_thinking)}")
    print(f"  * Fehlgeschlagen:      {len(all_failures)} fehlerhafte Tool-Calls")
    print(f"  * Unnötige Reads:      {len(all_explorations)} Schema-/Explorations-Reads (*.json, git, Get-ChildItem)")
    print("=" * 86)


# -----------------------------------------------------------------------------
# Haupt-Einstiegspunkt
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Antigravity transcript.jsonl Benchmark & Performance-Analyse"
    )
    parser.add_argument(
        "transcript",
        nargs="?",
        default=None,
        help="Pfad zu transcript.jsonl oder einem Brain-Verzeichnis / Conversation-ID",
    )
    parser.add_argument(
        "--turn",
        type=int,
        default=None,
        help="Nur einen bestimmten Turn analysieren (1-basiert)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Ergebnis als JSON ausgeben",
    )

    args = parser.parse_args()

    try:
        t_path = locate_transcript_file(args.transcript)
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    turns = parse_transcript(t_path)
    if not turns:
        print(f"Keine verwertbaren Schritte in '{t_path}' gefunden.", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "transcript_path": str(t_path),
            "turns_count": len(turns),
            "turns": [
                {
                    "turn_number": t.turn_number,
                    "prompt": t.user_prompt,
                    "duration_seconds": round(t.total_duration, 2),
                    "planner_time_seconds": round(t.planner_time, 2),
                    "tool_time_seconds": round(t.tool_and_wait_time, 2),
                    "planner_rounds": len(t.planner_steps),
                    "tool_calls": t.total_tool_calls,
                    "max_input_tokens": t.max_input_tokens,
                    "sum_output_tokens": t.sum_output_tokens,
                    "sum_thinking_tokens": t.sum_thinking_tokens,
                    "failures": [{"step": s.step_index, "reason": r} for s, r in t.failures],
                    "explorations": [{"step": s.step_index, "action": a} for s, a in t.explorations],
                }
                for t in turns
            ],
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    print(f"\nBenchmark-Analyse für: {t_path}\n")

    if args.turn is not None:
        target_turns = [t for t in turns if t.turn_number == args.turn]
        if not target_turns:
            print(f"Turn #{args.turn} nicht gefunden (Vorhanden: 1..{len(turns)})", file=sys.stderr)
            return 1
        for t in target_turns:
            print_turn_report(t)
    else:
        for t in turns:
            print_turn_report(t)
        print_overall_summary(turns)

    return 0


if __name__ == "__main__":
    sys.exit(main())
