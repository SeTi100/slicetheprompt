"""Lokaler Read-Only-Datenzugriff auf Antigravity-Datenbanken und Transkripte.

Liest direkt aus:
  1. ~/.gemini/antigravity/conversation_summaries.db (Workspaces, Titel, Subagent-Hierarchie)
  2. ~/.gemini/antigravity/conversations/<id>.db       (gen_metadata & steps Protobuf-Blobs)
  3. ~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl (Fallback/Text-Enrichment)

Alle SQLite-Zugriffe erfolgen strikt Read-Only (?mode=ro) und mit In-Memory mtime-Cache.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass, field
from typing import Any


def default_antigravity_dir() -> Path:
    """Standard-Datenverzeichnis von Antigravity (~/.gemini/antigravity)."""
    env_dir = os.environ.get("ANTIGRAVITY_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / ".gemini" / "antigravity"


# -----------------------------------------------------------------------------
# Minimaler Protobuf Wire-Format Decoder (ohne externe Abhängigkeiten)
# -----------------------------------------------------------------------------

def parse_pb(buf: bytes | None) -> list[tuple[int, str, Any]]:
    """Dekodiert Protobuf-Wire-Felder als Liste von (field_num, wire_kind, value).

    wire_kind: 'v' (varint), 'b' (length-delimited bytes), '32' (fixed32), '64' (fixed64).
    Bei ungültigen Bytes wird sicher eine leere Liste zurückgegeben.
    """
    if not buf:
        return []
    n = len(buf)
    i = 0
    out: list[tuple[int, str, Any]] = []
    try:
        while i < n:
            tag = 0
            shift = 0
            while i < n:
                b = buf[i]
                i += 1
                tag |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
                if shift > 63:
                    return []
            fn, wt = tag >> 3, tag & 7
            if fn <= 0:
                return []
            if wt == 0:
                v = 0
                shift = 0
                while i < n:
                    b = buf[i]
                    i += 1
                    v |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                    if shift > 63:
                        return []
                out.append((fn, "v", v))
            elif wt == 2:
                ln = 0
                shift = 0
                while i < n:
                    b = buf[i]
                    i += 1
                    ln |= (b & 0x7F) << shift
                    shift += 7
                    if not (b & 0x80):
                        break
                    if shift > 35:
                        return []
                if ln < 0 or i + ln > n:
                    return []
                out.append((fn, "b", buf[i : i + ln]))
                i += ln
            elif wt == 5:
                if i + 4 > n:
                    return []
                out.append((fn, "32", buf[i : i + 4]))
                i += 4
            elif wt == 1:
                if i + 8 > n:
                    return []
                out.append((fn, "64", buf[i : i + 8]))
                i += 8
            else:
                return []
    except Exception:
        return []
    return out


def unpack_varints(buf: bytes | None) -> list[int]:
    """Dekodiert packed-repeated Varints (z. B. step_indices in gen_metadata.f2)."""
    if not buf:
        return []
    n = len(buf)
    i = 0
    res: list[int] = []
    while i < n:
        v = 0
        shift = 0
        while i < n:
            b = buf[i]
            i += 1
            v |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
            if shift > 63:
                return res
        res.append(v)
    return res


def _pb_first_bytes(fields: list[tuple[int, str, Any]], target_fn: int) -> bytes:
    for fn, wt, val in fields:
        if fn == target_fn and wt == "b":
            return val
    return b""


def _pb_first_varint(fields: list[tuple[int, str, Any]], target_fn: int, default: int = 0) -> int:
    for fn, wt, val in fields:
        if fn == target_fn and wt == "v":
            return int(val)
    return default


def _pb_timestamp(buf: bytes | None) -> dt.datetime | None:
    """Parst ein Protobuf google.protobuf.Timestamp {seconds=f1, nanos=f2} in UTC."""
    if not buf:
        return None
    fields = parse_pb(buf)
    sec = _pb_first_varint(fields, 1, 0)
    if sec <= 1_000_000_000:
        return None
    nanos = _pb_first_varint(fields, 2, 0)
    try:
        return dt.datetime.fromtimestamp(sec + nanos / 1e9, tz=dt.timezone.utc)
    except Exception:
        return None


def _clean_text(raw_bytes: bytes | None) -> str:
    if not raw_bytes:
        return ""
    return raw_bytes.decode("utf-8", errors="ignore").strip()


# -----------------------------------------------------------------------------
# Datenstrukturen
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    title: str
    step_count: int
    last_modified: dt.datetime | None
    workspace_uris: tuple[str, ...]
    workspace_name: str
    parent_conversation_id: str
    project_id: str = ""


@dataclass(frozen=True)
class RawRequestRecord:
    """Ein einzelner LLM-API-Aufruf aus gen_metadata."""
    idx: int
    conversation_id: str
    execution_id: str
    step_indices: tuple[int, ...]
    timestamp: dt.datetime | None
    model_enum: str
    model_slug: str
    model_display: str
    fresh_in: int
    cache_read: int
    output_total: int
    thinking_tokens: int
    visible_output: int
    context_used: int
    context_limit: int


@dataclass(frozen=True)
class RawStepRecord:
    """Ein Schritt aus der steps-Tabelle (User-Prompt, Planner oder Tool-Call)."""
    idx: int
    step_type: int  # 14=USER_INPUT, 15=PLANNER_RESPONSE, 132=TOOL/GENERIC
    status: int
    execution_id: str
    timestamp: dt.datetime | None
    user_text: str
    tool_calls: tuple[dict[str, Any], ...] = ()
    raw_texts: tuple[str, ...] = ()


@dataclass
class ConversationData:
    conversation_id: str
    summary: ConversationSummary | None
    requests: list[RawRequestRecord] = field(default_factory=list)
    steps: list[RawStepRecord] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Workspace- & SQLite-Hilfen
# -----------------------------------------------------------------------------

def _workspace_name_from_uris(uris: list[str]) -> str:
    """Extrahiert einen kompakten, lesbaren Workspace-Namen aus URI-Strings."""
    for u in uris:
        decoded = urllib.parse.unquote(str(u)).rstrip("/\\")
        if decoded:
            name = Path(decoded).name
            if name:
                return name
    return "unbekannt"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def _parse_iso_dt(val: str | None) -> dt.datetime | None:
    if not val:
        return None
    s = str(val).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


# -----------------------------------------------------------------------------
# Cache für schnelles Live-Polling
# -----------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CONV_CACHE: dict[str, tuple[float, ConversationData]] = {}
_SUMMARIES_CACHE: tuple[float, dict[str, ConversationSummary]] | None = None


def clear_cache() -> None:
    """Leert den In-Memory-Cache (z. B. für Tests)."""
    global _SUMMARIES_CACHE
    with _CACHE_LOCK:
        _CONV_CACHE.clear()
        _SUMMARIES_CACHE = None


def load_conversation_summaries(agy_dir: Path | None = None) -> dict[str, ConversationSummary]:
    """Liest conversation_summaries.db (inkl. Subagent-Eltern und Workspace-URIs)."""
    global _SUMMARIES_CACHE
    base = agy_dir or default_antigravity_dir()
    db_path = base / "conversation_summaries.db"
    if not db_path.is_file():
        return {}

    try:
        mtime = db_path.stat().st_mtime
    except OSError:
        return {}

    with _CACHE_LOCK:
        if _SUMMARIES_CACHE is not None and _SUMMARIES_CACHE[0] == mtime:
            return _SUMMARIES_CACHE[1]

    out: dict[str, ConversationSummary] = {}
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_ro(db_path)
        rows = conn.execute(
            "SELECT conversation_id, title, step_count, last_modified_time, "
            "workspace_uris, parent_conversation_id, project_id "
            "FROM conversation_summaries"
        ).fetchall()
        for cid, title, scount, lmt, w_raw, parent_id, proj_id in rows:
            uris: list[str] = []
            if w_raw:
                try:
                    parsed = json.loads(w_raw)
                    if isinstance(parsed, list):
                        uris = [str(x) for x in parsed]
                except Exception:
                    uris = [str(w_raw)]
            out[str(cid)] = ConversationSummary(
                conversation_id=str(cid),
                title=str(title or ""),
                step_count=int(scount or 0),
                last_modified=_parse_iso_dt(lmt),
                workspace_uris=tuple(uris),
                workspace_name=_workspace_name_from_uris(uris),
                parent_conversation_id=str(parent_id or ""),
                project_id=str(proj_id or ""),
            )
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()

    with _CACHE_LOCK:
        _SUMMARIES_CACHE = (mtime, out)
    return out


# -----------------------------------------------------------------------------
# Parsing einer einzelnen Konversations-DB + Transcript-Fallback
# -----------------------------------------------------------------------------

def _decode_gen_metadata_row(idx: int, conv_id: str, blob: bytes) -> RawRequestRecord | None:
    top = parse_pb(blob)
    if not top:
        return None

    step_indices: list[int] = []
    for fn, wt, val in top:
        if fn == 2:
            if wt == "b":
                step_indices.extend(unpack_varints(val))
            elif wt == "v":
                step_indices.append(int(val))

    exec_id = _clean_text(_pb_first_bytes(top, 4))
    f1_bytes = _pb_first_bytes(top, 1)
    if not f1_bytes:
        return None
    f1 = parse_pb(f1_bytes)

    model_slug = _clean_text(_pb_first_bytes(f1, 19))
    model_display = _clean_text(_pb_first_bytes(f1, 21))

    model_enum = ""
    for fn, wt, val in f1:
        if fn == 20 and wt == "b":
            kv = parse_pb(val)
            k = _clean_text(_pb_first_bytes(kv, 1))
            if k == "model_enum":
                model_enum = _clean_text(_pb_first_bytes(kv, 2))
                break

    f4_bytes = _pb_first_bytes(f1, 4)
    f4 = parse_pb(f4_bytes)
    fresh_in = _pb_first_varint(f4, 2, 0)
    output_total = _pb_first_varint(f4, 3, 0)
    cache_read = _pb_first_varint(f4, 5, 0)
    thinking_tok = _pb_first_varint(f4, 9, 0)
    visible_out = _pb_first_varint(f4, 10, 0)
    if visible_out == 0 and output_total > 0 and thinking_tok == 0:
        visible_out = output_total

    # Context-Auslastung aus f1.f9.f10
    ctx_used = 0
    ctx_limit = 0
    f9_bytes = _pb_first_bytes(f1, 9)
    if f9_bytes:
        f9 = parse_pb(f9_bytes)
        f10_bytes = _pb_first_bytes(f9, 10)
        if f10_bytes:
            f10 = parse_pb(f10_bytes)
            ctx_used = _pb_first_varint(f10, 1, 0)
            ctx_limit = _pb_first_varint(f10, 4, 0)

    return RawRequestRecord(
        idx=idx,
        conversation_id=conv_id,
        execution_id=exec_id,
        step_indices=tuple(step_indices),
        timestamp=None,
        model_enum=model_enum,
        model_slug=model_slug,
        model_display=model_display,
        fresh_in=fresh_in,
        cache_read=cache_read,
        output_total=output_total,
        thinking_tokens=thinking_tok,
        visible_output=visible_out,
        context_used=ctx_used,
        context_limit=ctx_limit,
    )


def _decode_step_row(idx: int, step_type: int, status: int, md_blob: bytes | None, pl_blob: bytes | None) -> RawStepRecord:
    md_fields = parse_pb(md_blob)
    ts = _pb_timestamp(_pb_first_bytes(md_fields, 1))
    exec_id = _clean_text(_pb_first_bytes(md_fields, 12))

    user_text = ""
    tool_calls: list[dict[str, Any]] = []
    raw_texts: list[str] = []

    pl_fields = parse_pb(pl_blob)
    if not exec_id or not ts:
        f5_bytes = _pb_first_bytes(pl_fields, 5)
        if f5_bytes:
            f5 = parse_pb(f5_bytes)
            if not ts:
                ts = _pb_timestamp(_pb_first_bytes(f5, 1))
            if not exec_id:
                exec_id = _clean_text(_pb_first_bytes(f5, 12))

    # User Prompt aus step_type=14 (step_payload.f19.f2 oder erster langer Text)
    if step_type == 14:
        f19_bytes = _pb_first_bytes(pl_fields, 19)
        if f19_bytes:
            f19 = parse_pb(f19_bytes)
            user_text = _clean_text(_pb_first_bytes(f19, 2))
            if not user_text:
                # Fallback innerhalb f19
                for _, wt, val in f19:
                    if wt == "b":
                        txt = _clean_text(val)
                        if txt and not txt.startswith("{"):
                            user_text = txt
                            break

    # Tool-Calls aus step_type=132 (step_payload.f5.f4) und Tool-Args (f140/usw.)
    if step_type == 132:
        f5_bytes = _pb_first_bytes(pl_fields, 5)
        if f5_bytes:
            for fn, wt, val in parse_pb(f5_bytes):
                if fn == 4 and wt == "b":
                    tc_fields = parse_pb(val)
                    tname = _clean_text(_pb_first_bytes(tc_fields, 2))
                    targs_raw = _clean_text(_pb_first_bytes(tc_fields, 3))
                    if tname:
                        args_obj: Any = {}
                        if targs_raw:
                            try:
                                args_obj = json.loads(targs_raw)
                            except Exception:
                                args_obj = {"_raw": targs_raw}
                        tool_calls.append({"name": tname, "args": args_obj})

    # Auch lesbare Pfade/Projekthinweise im Payload sammeln (z. B. projects/2026-...)
    if pl_blob:
        for m in re.findall(rb"projects[/\\][0-9a-zA-Z_\-]+", pl_blob):
            raw_texts.append(m.decode("utf-8", errors="ignore"))

    return RawStepRecord(
        idx=idx,
        step_type=int(step_type or 0),
        status=int(status or 0),
        execution_id=exec_id,
        timestamp=ts,
        user_text=user_text,
        tool_calls=tuple(tool_calls),
        raw_texts=tuple(raw_texts),
    )


def _enrich_steps_from_transcript(
    conv_id: str,
    steps: list[RawStepRecord],
    agy_dir: Path,
) -> list[RawStepRecord]:
    """Ergänzt Schritt-Daten aus transcript.jsonl, falls User-Texte oder Tool-Args fehlen."""
    t_path = agy_dir / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not t_path.is_file():
        return steps

    by_idx: dict[int, dict[str, Any]] = {}
    try:
        with open(t_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    d = json.loads(s)
                    sidx = d.get("step_index")
                    if sidx is not None:
                        by_idx[int(sidx)] = d
                except Exception:
                    continue
    except Exception:
        return steps

    if not by_idx:
        return steps

    enriched: list[RawStepRecord] = []
    for st in steps:
        raw = by_idx.get(st.idx)
        if not raw:
            enriched.append(st)
            continue

        user_text = st.user_text
        if st.step_type == 14 and not user_text:
            content = str(raw.get("content") or "")
            m_req = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
            user_text = m_req.group(1).strip() if m_req else content.strip()

        tcalls = list(st.tool_calls)
        if not tcalls and raw.get("tool_calls"):
            for tc in raw["tool_calls"]:
                args = tc.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {"_raw": args}
                tcalls.append({"name": tc.get("name", ""), "args": args})

        ts = st.timestamp or _parse_iso_dt(raw.get("created_at"))
        enriched.append(
            RawStepRecord(
                idx=st.idx,
                step_type=st.step_type,
                status=st.status,
                execution_id=st.execution_id,
                timestamp=ts,
                user_text=user_text,
                tool_calls=tuple(tcalls),
                raw_texts=st.raw_texts,
            )
        )
    return enriched


def _load_from_transcript_only(
    conv_id: str,
    summary: ConversationSummary | None,
    agy_dir: Path,
) -> ConversationData | None:
    """Fallback, falls für eine alte Konversation keine .db existiert."""
    t_path = agy_dir / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not t_path.is_file():
        return None

    steps: list[RawStepRecord] = []
    requests: list[RawRequestRecord] = []
    req_idx = 0
    curr_exec = f"tr-{conv_id[:8]}-0"
    curr_model = ""

    with open(t_path, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            s = line.strip()
            if not s:
                continue
            try:
                raw = json.loads(s)
            except Exception:
                continue
            sidx = int(raw.get("step_index", i))
            stype_str = raw.get("type", "")
            ts = _parse_iso_dt(raw.get("created_at"))
            content = str(raw.get("content") or "")

            if stype_str == "USER_INPUT":
                curr_exec = f"tr-{conv_id[:8]}-{sidx}"
                m_mod = re.search(r"Model Selection[`\x60\x27]?\s+from\s+.*?\s+to\s+([^\r\n]+?)(?:\.\s+No need|\n|<|$)", content)
                if m_mod:
                    curr_model = m_mod.group(1).strip()
                m_req = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
                utxt = m_req.group(1).strip() if m_req else content.strip()
                steps.append(RawStepRecord(sidx, 14, 3, curr_exec, ts, utxt))
            elif stype_str == "PLANNER_RESPONSE":
                tcalls = []
                for tc in raw.get("tool_calls") or []:
                    args = tc.get("args") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {"_raw": args}
                    tcalls.append({"name": tc.get("name", ""), "args": args})
                steps.append(RawStepRecord(sidx, 15, 3, curr_exec, ts, "", tuple(tcalls)))
                f_in = int(raw.get("input_tokens") or 0)
                c_rd = int(raw.get("cache_read_tokens") or 0)
                o_tok = int(raw.get("output_tokens") or 0)
                th = str(raw.get("thinking") or "")
                th_tok = int(raw.get("thinking_tokens") or 0)
                if not th_tok and th:
                    th_tok = max(1, len(re.findall(r"\w+|[^\w\s]", th)))
                requests.append(
                    RawRequestRecord(
                        idx=req_idx,
                        conversation_id=conv_id,
                        execution_id=curr_exec,
                        step_indices=(sidx,),
                        timestamp=ts,
                        model_enum="",
                        model_slug="",
                        model_display=curr_model,
                        fresh_in=f_in,
                        cache_read=c_rd,
                        output_total=o_tok,
                        thinking_tokens=th_tok,
                        visible_output=max(0, o_tok - th_tok),
                        context_used=f_in + c_rd,
                        context_limit=256000,
                    )
                )
                req_idx += 1
            else:
                steps.append(RawStepRecord(sidx, 132, 3, curr_exec, ts, ""))

    return ConversationData(conv_id, summary, requests, steps)


def load_conversation(
    conv_id: str,
    agy_dir: Path | None = None,
    summaries: dict[str, ConversationSummary] | None = None,
) -> ConversationData | None:
    """Lädt eine Konversation bevorzugt aus conversations/<id>.db, sonst transcript.jsonl."""
    base = agy_dir or default_antigravity_dir()
    if summaries is None:
        summaries = load_conversation_summaries(base)
    summary = summaries.get(conv_id)

    db_path = base / "conversations" / f"{conv_id}.db"
    if not db_path.is_file():
        return _load_from_transcript_only(conv_id, summary, base)

    try:
        mtime = db_path.stat().st_mtime
    except OSError:
        return None

    t_path = base / "brain" / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    t_mtime = t_path.stat().st_mtime if t_path.is_file() else 0.0
    cache_sig = mtime + t_mtime

    with _CACHE_LOCK:
        cached = _CONV_CACHE.get(conv_id)
        if cached is not None and cached[0] == cache_sig:
            return cached[1]

    conn: sqlite3.Connection | None = None
    steps: list[RawStepRecord] = []
    requests: list[RawRequestRecord] = []

    try:
        conn = _connect_ro(db_path)
        for idx, st_type, status, md_blob, pl_blob in conn.execute(
            "SELECT idx, step_type, status, metadata, step_payload FROM steps ORDER BY idx"
        ).fetchall():
            steps.append(_decode_step_row(int(idx), st_type, status, md_blob, pl_blob))

        steps_by_idx = {s.idx: s for s in steps}

        for idx, data_blob in conn.execute("SELECT idx, data FROM gen_metadata ORDER BY idx").fetchall():
            rec = _decode_gen_metadata_row(int(idx), conv_id, data_blob)
            if rec is None:
                continue
            # Timestamp vom ersten zugehörigen Step übernehmen
            ts = None
            for sidx in rec.step_indices:
                st = steps_by_idx.get(sidx)
                if st and st.timestamp is not None:
                    ts = st.timestamp
                    break
            if ts is None:
                # Fallback auf User-Step mit gleicher execution_id
                for st in steps:
                    if st.execution_id == rec.execution_id and st.timestamp is not None:
                        ts = st.timestamp
                        break
            if ts is not None:
                rec = RawRequestRecord(
                    idx=rec.idx,
                    conversation_id=rec.conversation_id,
                    execution_id=rec.execution_id,
                    step_indices=rec.step_indices,
                    timestamp=ts,
                    model_enum=rec.model_enum,
                    model_slug=rec.model_slug,
                    model_display=rec.model_display,
                    fresh_in=rec.fresh_in,
                    cache_read=rec.cache_read,
                    output_total=rec.output_total,
                    thinking_tokens=rec.thinking_tokens,
                    visible_output=rec.visible_output,
                    context_used=rec.context_used,
                    context_limit=rec.context_limit,
                )
            requests.append(rec)
    except Exception:
        return _load_from_transcript_only(conv_id, summary, base)
    finally:
        if conn is not None:
            conn.close()

    steps = _enrich_steps_from_transcript(conv_id, steps, base)
    data = ConversationData(conv_id, summary, requests, steps)

    with _CACHE_LOCK:
        _CONV_CACHE[conv_id] = (cache_sig, data)
    return data


def list_recent_conversation_ids(
    since_utc: dt.datetime | None = None,
    agy_dir: Path | None = None,
) -> list[str]:
    """Listet alle Konversations-IDs (DB + Brain) optional gefiltert nach Änderungszeit."""
    base = agy_dir or default_antigravity_dir()
    cutoff_ts = since_utc.timestamp() if since_utc else 0.0
    ids_with_mtime: dict[str, float] = {}

    conv_dir = base / "conversations"
    if conv_dir.is_dir():
        for p in conv_dir.iterdir():
            if p.suffix == ".db":
                try:
                    mt = p.stat().st_mtime
                    if mt >= cutoff_ts:
                        ids_with_mtime[p.stem] = max(ids_with_mtime.get(p.stem, 0.0), mt)
                except OSError:
                    pass

    brain_dir = base / "brain"
    if brain_dir.is_dir():
        for p in brain_dir.iterdir():
            if p.is_dir():
                tr = p / ".system_generated" / "logs" / "transcript.jsonl"
                if tr.is_file():
                    try:
                        mt = tr.stat().st_mtime
                        if mt >= cutoff_ts:
                            ids_with_mtime[p.name] = max(ids_with_mtime.get(p.name, 0.0), mt)
                    except OSError:
                        pass

    return [cid for cid, _ in sorted(ids_with_mtime.items(), key=lambda kv: kv[1], reverse=True)]
