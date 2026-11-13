"""Antigravity Usage Screenshot-Importer (RapidOCR + 360°-Kreisring-Tracker).

Liest Screenshots des Antigravity-Model-/Usage-Popovers (ein- oder zweispaltig):
  * Rechte Usage-Spalte: erkennt getrennte Pool-Limits (Weekly / 5h), Rest-% und Resets in
  * Kreisring-Tracker (PIL + numpy + scipy): lokalisiert farbige Fortschrittsringe + dunkle
    Ring-Spur im rechten Randstreifen, misst die Füllung per 360°-Winkel-Sampling ab 12 Uhr,
    klassifiziert die Ringfarbe (green / amber / red / empty) und prüft bzw. ersetzt fehlende
    oder fehlerhafte OCR-Zahlenwerte.
  * Linke Modell-Spalte (falls vorhanden): extrahiert verfügbare und neue/unbekannte Modelle
    inkl. Tier & Verfügbarkeit (z. B. Available, Notice, Unavailable) als Vorschlag für
    `custom_models`.
"""

from __future__ import annotations

import base64
import difflib
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image
from scipy import ndimage

from .usage_eval import DEFAULT_POOLS, MODEL_SPECS, load_calibration, parse_resets_in

DARK_BG_RGB = (24, 26, 31)

# Bekannte UI-Pills/Badges in der linken Modell-Spalte
_TIER_WORDS = ("XHigh", "Extra", "Medium", "High", "Low", "Trial")
_STATUS_BADGES = ("Unavailable", "Notice", "Deprecated", "Preview", "Beta", "New")
_SKIP_LEFT_PHRASES = ("view usage", "viewusage", "model selection", "select model")


@dataclass(frozen=True)
class OcrToken:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    score: float = 1.0

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) * 0.5

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) * 0.5

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)

    @property
    def width(self) -> float:
        return max(1.0, self.x1 - self.x0)


# -----------------------------------------------------------------------------
# Bild-Dekodierung & Lazy RapidOCR
# -----------------------------------------------------------------------------

def decode_image_input(image_input: bytes | bytearray | str | Image.Image) -> Image.Image:
    """Dekodiert Rohbytes, Base64/Data-URL oder PIL.Image nach RGB."""
    if isinstance(image_input, Image.Image):
        img = image_input
    else:
        if isinstance(image_input, str):
            raw_str = image_input.strip()
            if not raw_str:
                raise ValueError("Leerer Bild-String")
            if raw_str.startswith("data:") and "," in raw_str:
                raw_str = raw_str.split(",", 1)[1]
            try:
                raw_bytes = base64.b64decode(re.sub(r"\s+", "", raw_str), validate=True)
            except Exception as exc:
                raise ValueError(f"Ungültige Base64-Bilddaten: {exc}") from exc
        elif isinstance(image_input, (bytes, bytearray)):
            raw_bytes = bytes(image_input)
        else:
            raise TypeError(f"Nicht unterstützter Bildtyp: {type(image_input).__name__}")

        if not raw_bytes:
            raise ValueError("Bilddaten sind leer")
        try:
            img = Image.open(io.BytesIO(raw_bytes))
            img.load()
        except Exception as exc:
            raise ValueError(f"Bild konnte nicht dekodiert werden: {exc}") from exc

    if img.width <= 0 or img.height <= 0:
        raise ValueError("Bild hat ungültige Abmessungen")

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, DARK_BG_RGB + (255,))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB")


@lru_cache(maxsize=1)
def _get_ocr_engine() -> Any:
    """Lädt RapidOCR lazy beim ersten Screenshot-Import (keine Startverzögerung)."""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _normalize_ocr_boxes(raw_boxes: Any, scale: float = 1.0) -> list[OcrToken]:
    tokens: list[OcrToken] = []
    if not raw_boxes:
        return tokens
    inv = 1.0 / max(1e-6, float(scale))
    for item in raw_boxes:
        if not item or len(item) < 2:
            continue
        box = item[0]
        txt = str(item[1] or "").strip()
        score = float(item[2]) if len(item) > 2 and item[2] is not None else 1.0
        if not txt or not box:
            continue
        if isinstance(box[0], (list, tuple)):
            xs = [float(pt[0]) * inv for pt in box]
            ys = [float(pt[1]) * inv for pt in box]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
        elif len(box) == 4:
            x0, y0, x1, y1 = (float(v) * inv for v in box)
        else:
            continue
        tokens.append(OcrToken(text=txt, x0=x0, y0=y0, x1=x1, y1=y1, score=score))
    tokens.sort(key=lambda t: (t.yc, t.x0))
    return tokens


def _run_ocr_on_image(
    img_rgb: Image.Image,
    scale: float = 1.0,
) -> tuple[list[OcrToken], bool, str]:
    """Führt RapidOCR optional auf hochskalierter Kopie aus; fällt bei Fehler sauber zurück."""
    try:
        engine = _get_ocr_engine()
    except Exception:
        return [], False, "unavailable"

    try:
        if abs(scale - 1.0) > 1e-3:
            target_w = max(1, int(round(img_rgb.width * scale)))
            target_h = max(1, int(round(img_rgb.height * scale)))
            scaled = img_rgb.resize((target_w, target_h), Image.Resampling.BICUBIC)
            res, _ = engine(np.array(scaled))
        else:
            res, _ = engine(np.array(img_rgb))
        return _normalize_ocr_boxes(res, scale=scale), True, "rapidocr_onnxruntime"
    except Exception:
        return [], False, "error"


# -----------------------------------------------------------------------------
# Geometrischer 360°-Kreisring-Tracker (PIL + numpy)
# -----------------------------------------------------------------------------

def _bilinear_sample(arr: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    h, w, _ = arr.shape
    ys_c = np.clip(ys, 0.0, h - 1.001)
    xs_c = np.clip(xs, 0.0, w - 1.001)
    y0 = np.floor(ys_c).astype(np.int32)
    x0 = np.floor(xs_c).astype(np.int32)
    y1 = np.minimum(y0 + 1, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    wy = (ys_c - y0)[..., None]
    wx = (xs_c - x0)[..., None]
    return (
        arr[y0, x0] * (1.0 - wy) * (1.0 - wx)
        + arr[y0, x1] * (1.0 - wy) * wx
        + arr[y1, x0] * wy * (1.0 - wx)
        + arr[y1, x1] * wy * wx
    )


def _classify_ring_color(med_rgb: np.ndarray | list[float], filled_deg: int) -> str:
    if filled_deg <= 0:
        return "empty"
    r, g, b = float(med_rgb[0]), float(med_rgb[1]), float(med_rgb[2])
    if g > r + 15.0:
        return "green"
    if r > g + 20.0 and g > 75.0:
        return "amber"
    if r > g + 25.0 and g <= 75.0:
        return "red"
    if r > g + 12.0:
        return "amber" if g > 75.0 else "red"
    if b > r + 15.0 and g > r + 10.0:
        return "green"
    return "amber" if r > b + 20.0 else "green"


def _clean_circular_filled(filled: np.ndarray) -> np.ndarray:
    """Schliesst 1°-Kompressionslücken und entfernt einzelne 1°-Ausreisser."""
    out = filled.copy()
    n = len(out)
    # 1°-Gap schliessen (zirkulär)
    for i in range(n):
        if not out[i] and out[(i - 1) % n] and out[(i + 1) % n]:
            out[i] = True
    if out.sum() <= 2:
        return out
    # Zirkuläre Komponenten kleiner 2° verwerfen
    padded = np.concatenate([out, out])
    lbl, num = ndimage.label(padded)
    if num > 0:
        counts = np.bincount(lbl)
        keep = np.zeros(n, dtype=bool)
        for comp_id in range(1, num + 1):
            if counts[comp_id] >= 2:
                idx = np.where(lbl == comp_id)[0] % n
                keep[idx] = True
        if keep.any():
            return keep
    return out


def detect_and_measure_rings(
    img_rgb: Image.Image | np.ndarray,
    x_min: int = 0,
    *,
    x_max: int | None = None,
) -> list[dict[str, Any]]:
    """Sucht Fortschrittsringe im rechten Randstreifen und misst sie per 360°-Winkel-Sampling."""
    if isinstance(img_rgb, Image.Image):
        arr = np.asarray(img_rgb.convert("RGB"), dtype=np.float32)
    else:
        arr = np.asarray(img_rgb, dtype=np.float32)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return []
        arr = arr[:, :, :3]

    h, w, _ = arr.shape
    if h < 12 or w < 12:
        return []

    if x_max is not None and x_min > 0:
        x_start = max(0, min(w - 1, int(x_min)))
        x_end = max(x_start + 1, min(w, int(x_max)))
    else:
        x_start = max(0, min(w - 1, max(int(x_min), int(w * 0.70))))
        x_end = w

    strip = arr[:, x_start:x_end, :]
    if strip.shape[1] < 8:
        return []

    luma = strip.mean(axis=2)
    max_rgb = strip.max(axis=2)
    sat = max_rgb - strip.min(axis=2)
    bg_luma = float(np.percentile(luma, 25))

    # Sowohl farbige Arc-Pixel als auch graue/dunkle Ring-Spur (Track) erfassen
    mask = ((sat > 20.0) & (max_rgb > 40.0)) | (
        (luma > max(32.0, bg_luma + 10.5)) & (max_rgb > bg_luma + 12.0)
    )
    lbl, _ = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    slices = ndimage.find_objects(lbl)

    angles_rad = np.deg2rad(np.arange(360, dtype=np.float32) - 90.0)
    ca = np.cos(angles_rad)
    sa = np.sin(angles_rad)

    max_diam = max(72, int(round(h * 0.28)))
    rings: list[dict[str, Any]] = []

    for sl in slices:
        if sl is None:
            continue
        y0, y1 = sl[0].start, sl[0].stop - 1
        x0 = sl[1].start + x_start
        x1 = sl[1].stop - 1 + x_start
        bw = x1 - x0 + 1
        bh = y1 - y0 + 1
        if bw < 8 or bh < 8 or bw > max_diam or bh > max_diam:
            continue
        aspect = bw / float(bh)
        if not (0.65 <= aspect <= 1.55):
            continue

        cx0 = (x0 + x1) * 0.5
        cy0 = (y0 + y1) * 0.5
        r_box = (bw + bh) * 0.25

        # Hohles Ring-Zentrum prüfen (Textblöcke wie "1d 5h" oder "61%" sind innen hell!)
        rc = max(1, int(round(0.28 * r_box)))
        c_patch = arr[
            max(0, int(cy0) - rc) : min(h, int(cy0) + rc + 1),
            max(0, int(cx0) - rc) : min(w, int(cx0) + rc + 1),
        ]
        if c_patch.size == 0 or float(c_patch.mean()) > bg_luma + 18.0:
            continue

        # Zentrum & Radius feinjustieren (gekappte Track/Arc-Intensität bevorzugt die Strichmitte)
        shift_xy = max(1.0, r_box * 0.14)
        r_min = max(3.5, r_box * 0.65)
        r_max = max(r_min + 1.0, r_box * 1.02)
        r_pref = r_box * 0.84

        best_score = -1e9
        best_cx, best_cy, best_r = cx0, cy0, r_pref
        for dcx in np.linspace(-shift_xy, shift_xy, 5):
            for dcy in np.linspace(-shift_xy, shift_xy, 5):
                ccx = cx0 + float(dcx)
                ccy = cy0 + float(dcy)
                for cr in np.linspace(r_min, r_max, 11):
                    px = _bilinear_sample(arr, ccy + float(cr) * sa, ccx + float(cr) * ca)
                    pl = px.mean(axis=1)
                    ps = px.max(axis=1) - px.min(axis=1)
                    pres = np.maximum(
                        np.clip((pl - bg_luma) / 45.0, 0.0, 1.0),
                        np.clip(ps / 45.0, 0.0, 1.0),
                    )
                    score = (
                        float(pres.mean())
                        - 0.002 * (float(dcx) ** 2 + float(dcy) ** 2)
                        - 0.001 * (((float(cr) - r_pref) / max(1.0, r_box)) ** 2)
                    )
                    if score > best_score:
                        best_score = score
                        best_cx, best_cy, best_r = ccx, ccy, float(cr)

        # Prüfen, ob mindestens 72% des Kreisumfangs als Track oder Arc sichtbar sind
        px_perim = _bilinear_sample(arr, best_cy + best_r * sa, best_cx + best_r * ca)
        perim_luma = px_perim.mean(axis=1)
        perim_sat = px_perim.max(axis=1) - px_perim.min(axis=1)
        perim_cov = float(((perim_luma > bg_luma + 9.5) | (perim_sat > 18.0)).mean())
        if perim_cov < 0.72:
            continue

        # 360°-Winkel-Sampling über radiales Band [r - band, r + band] im Uhrzeigersinn ab 12 Uhr
        band = max(1.2, best_r * 0.18)
        radii = np.linspace(best_r - band, best_r + band, 5, dtype=np.float32)
        px_band = _bilinear_sample(
            arr,
            best_cy + sa[:, None] * radii[None, :],
            best_cx + ca[:, None] * radii[None, :],
        )
        s_max = px_band.max(axis=2)
        s_sat = s_max - px_band.min(axis=2)
        active = (s_sat > 18.0) & (s_max > 40.0)
        max_sat = np.where(active, s_sat, 0.0).max(axis=1)

        if int((max_sat > 20.0).sum()) >= 2:
            peak_sat = float(np.percentile(max_sat[max_sat > 20.0], 90))
            th = max(20.0, 0.45 * peak_sat)
            filled = _clean_circular_filled(max_sat > th)
        else:
            filled = np.zeros(360, dtype=bool)

        filled_deg = int(filled.sum())
        ring_pct = round(filled_deg / 360.0 * 100.0, 1)
        best_r_idx = s_sat.argmax(axis=1)
        rgb_per_deg = px_band[np.arange(360), best_r_idx]
        if filled_deg > 0:
            med_rgb = np.median(rgb_per_deg[filled], axis=0)
            mean_rgb = [round(float(v), 1) for v in med_rgb]
        else:
            med_rgb = np.zeros(3, dtype=np.float32)
            mean_rgb = [0.0, 0.0, 0.0]

        rings.append(
            {
                "y_center": round(best_cy, 2),
                "x_center": round(best_cx, 2),
                "radius_px": round(best_r, 2),
                "ring_pct": ring_pct,
                "ring_color": _classify_ring_color(med_rgb, filled_deg),
                "mean_rgb": mean_rgb,
                "filled_deg": filled_deg,
                "method": "angle_360",
            }
        )

    rings.sort(key=lambda item: item["y_center"])

    # Duplikate (falls innerer/äusserer Rand getrennt erkannt) deduplizieren
    deduped: list[dict[str, Any]] = []
    for ring in rings:
        if deduped:
            prev = deduped[-1]
            dist = ((ring["x_center"] - prev["x_center"]) ** 2 + (ring["y_center"] - prev["y_center"]) ** 2) ** 0.5
            if dist < max(4.0, prev["radius_px"]):
                if ring["filled_deg"] > prev["filled_deg"]:
                    deduped[-1] = ring
                continue
        deduped.append(ring)
    return deduped


# -----------------------------------------------------------------------------
# Spalten-Trennung, linke Modell-Liste & rechte Usage-Fusion
# -----------------------------------------------------------------------------

def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_usage_anchor_token(txt: str) -> bool:
    c = _compact(txt)
    if c in ("usage", "geminimodels", "weeklylimit", "5hlimit"):
        return True
    if "weeklylimit" in c or "5hlimit" in c:
        return True
    if "claude" in c and "gpt" in c:
        return True
    return False


def _detect_split_x(img_rgb: Image.Image, tokens: list[OcrToken]) -> tuple[float, bool, float, float]:
    """Bestimmt (split_x, has_model_column, usage_x0, usage_x1)."""
    w, _ = img_rgb.size
    usage_tokens = [t for t in tokens if _is_usage_anchor_token(t.text)]
    if usage_tokens:
        usage_x0 = min(t.x0 for t in usage_tokens)
        usage_x1 = max(t.x1 for t in usage_tokens)
        # Rechte Kante aller Tokens rechts von usage_x0 - 12 (um "Resets in ..." und "% left" einzubeziehen)
        right_tokens = [t for t in tokens if t.x0 >= usage_x0 - 16.0]
        if right_tokens:
            usage_x1 = max(usage_x1, max(t.x1 for t in right_tokens))
        if usage_x0 > w * 0.22:
            # Eventuelle Tokens links von usage_x0 prüfen -> Trennlinie dazwischen
            left_tokens = [t for t in tokens if t.x1 <= usage_x0 - 4.0]
            if left_tokens:
                left_max_x = max(t.x1 for t in left_tokens)
                split_x = (left_max_x + usage_x0) * 0.5
            else:
                split_x = max(0.0, usage_x0 - 14.0)
            return split_x, True, usage_x0, usage_x1
        return 0.0, False, usage_x0, usage_x1

    # Fallback ohne OCR: vertikale Popover-Trennlinie im Helligkeitsprofil suchen
    arr = np.asarray(img_rgb, dtype=np.float32)
    luma_x = arr.mean(axis=(0, 2))
    grad_x = np.abs(np.diff(luma_x))
    x_lo, x_hi = int(w * 0.28), int(w * 0.62)
    if x_hi > x_lo + 4:
        sub = grad_x[x_lo:x_hi]
        idx = int(np.argmax(sub))
        if sub[idx] > 2.2:
            sx = float(x_lo + idx)
            return sx, True, sx, float(w)
    return 0.0, False, 0.0, float(w)


def _group_tokens_by_y(tokens: list[OcrToken]) -> list[list[OcrToken]]:
    if not tokens:
        return []
    ordered = sorted(tokens, key=lambda t: (t.yc, t.x0))
    heights = sorted(t.height for t in ordered)
    med_h = heights[len(heights) // 2]
    tol = max(4.0, med_h * 0.55)

    lines: list[list[OcrToken]] = []
    for tok in ordered:
        if not lines:
            lines.append([tok])
            continue
        cur = lines[-1]
        cur_yc = sum(t.yc for t in cur) / len(cur)
        if abs(tok.yc - cur_yc) <= tol:
            cur.append(tok)
        else:
            lines.append([tok])
    for line in lines:
        line.sort(key=lambda t: t.x0)
    return lines


def _match_pool_header(
    text: str,
    pools_cfg: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Ordnet einen Header-Text einem Pool zu -> (pool_id, display_label)."""
    c = _compact(text)
    if not c or c in ("usage", "weeklylimit", "5hlimit") or "resetsin" in c or "left" in c:
        return None, None

    # 1. Exakte / kompakte Treffer gegen konfigurierte Pools
    for pid, pcfg in pools_cfg.items():
        lbl = str(pcfg.get("label") or pid)
        if c == _compact(lbl) or c == _compact(pid):
            return pid, lbl

    # 2. Robuste Bekannt-Regeln (auch bei verschluckten Leerzeichen wie "ClaudeandGPTmodels")
    if "gemini" in c and "model" in c:
        return "gemini", str(pools_cfg.get("gemini", {}).get("label") or "Gemini Models")
    if "claude" in c and "gpt" in c:
        return "claude_gpt", str(pools_cfg.get("claude_gpt", {}).get("label") or "Claude & GPT Models")
    if "argon" in c:
        return "argon", str(pools_cfg.get("argon", {}).get("label") or "Gemini 4 Argon")

    # 3. Fuzzy-Match auf benutzerdefinierte Pools
    best_pid: str | None = None
    best_lbl: str | None = None
    best_ratio = 0.0
    for pid, pcfg in pools_cfg.items():
        lbl = str(pcfg.get("label") or pid)
        r = difflib.SequenceMatcher(None, c, _compact(lbl)).ratio()
        if r > best_ratio:
            best_ratio = r
            best_pid = pid
            best_lbl = lbl
    if best_ratio >= 0.78 and best_pid:
        return best_pid, best_lbl
    return None, None


def _extract_pct_from_text(text: str) -> tuple[float | None, bool]:
    """Extrahiert Rest-% aus Token-Text -> (wert, has_left_marker)."""
    has_left = "left" in text.lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if m:
        return float(m.group(1).replace(",", ".")), has_left
    if has_left:
        # OCR-Fehler wie "1000/0 left" oder "1000 left"
        m2 = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:/\s*0\s*)?left", text, re.IGNORECASE)
        if m2:
            return float(m2.group(1).replace(",", ".")), True
    return None, has_left


def _extract_reset_text(text: str) -> str | None:
    m = re.search(r"resets?\s*in\s*(.+)", text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    # Normalisiere kompaktes "1d5h" -> "1d 5h" für sauberes UI-Formular
    parts: list[str] = []
    for unit in ("d", "h", "m"):
        mu = re.search(rf"(\d+(?:[.,]\d+)?)\s*{unit}", raw, re.IGNORECASE)
        if mu:
            parts.append(f"{mu.group(1)}{unit}")
    return " ".join(parts) if parts else raw


# -----------------------------------------------------------------------------
# Linke Modell-Spalte: Bereinigung & Abgleich mit MODEL_SPECS + custom_models
# -----------------------------------------------------------------------------

def _slugify_model(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "custom-model"


def _clean_model_line(raw_line: str) -> tuple[str, str, str, str]:
    """Bereinigt eine Modellzeile -> (clean_name, tier, badge, availability_status)."""
    s = raw_line.strip()
    # Häufige Icon-Artefakte entfernen (z. B. Info-Kreis ① / ⓘ / ✓ / > / ^ / @)
    s = re.sub(r"[①②③ⓘℹ✓✔✕×›»>^@]+", " ", s)
    # Fehlende Leerzeichen bei zusammengeklebtem OCR trennen (ClaudeOpus5.5Medium -> Claude Opus 5.5 Medium)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[a-zA-Z])(?=\d+(?:\.\d+)?(?:\s|$|[A-Z(]))", " ", s)
    s = re.sub(r"(?<=\d)(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"(?<=[a-zA-Z0-9])(?=\()", " ", s)
    s = re.sub(r"\)\s*(?=[A-Za-z(])", ") ", s)
    s = re.sub(r"\s+", " ", s).strip()

    status = "Available"
    badge = ""
    for sb in _STATUS_BADGES:
        pat = re.compile(rf"(?:^|\s)[\(\[]?\s*{sb}\s*[\)\]]?(?=\s|$)", re.IGNORECASE)
        if pat.search(s):
            if not badge:
                badge = sb
            if sb.lower() in ("unavailable", "notice", "deprecated"):
                status = sb.capitalize()
            s = pat.sub(" ", s)
            s = re.sub(r"\s+", " ", s).strip()

    # Einzelnes nachlaufendes 'O' / 'i' von Info-Kreis-Icon hinter Unavailable/Notice entfernen
    if badge.lower() in ("unavailable", "notice"):
        s = re.sub(r"\s+[OoIi0]$", "", s).strip()

    tier = ""
    # Klammer-Tier im Modellnamen behalten (z. B. "GPT-OSS 120B (Medium)"), aber Tier trotzdem erfassen
    m_paren = re.search(r"\((XHigh|Extra|Medium|High|Low)\)", s, re.IGNORECASE)
    if m_paren:
        tier = m_paren.group(1)
    for tw in _TIER_WORDS:
        pat_end = re.compile(rf"\s+{tw}$", re.IGNORECASE)
        if pat_end.search(s):
            tier = tw
            s = pat_end.sub("", s).strip()
            break

    s = re.sub(r"\s+", " ", s).strip(" -|")
    return s, tier, badge, status


def _is_known_model(clean_name: str, slug: str, custom_models: dict[str, Any]) -> tuple[bool, str | None]:
    """Prüft exakt gegen `custom_models` und konkrete `MODEL_SPECS`-Einträge."""
    low = clean_name.lower()
    comp = _compact(clean_name)

    for pat, target in (custom_models or {}).items():
        pat_low = str(pat).lower()
        if pat_low in low or pat_low in slug or _compact(pat_low) == comp:
            if isinstance(target, dict):
                return True, str(target.get("pool_id") or "gemini")
            if isinstance(target, str) and target in MODEL_SPECS:
                return True, MODEL_SPECS[target].pool_id
            return True, None
        if isinstance(target, dict):
            dn = str(target.get("display_name") or "")
            if dn and _compact(dn) == comp:
                return True, str(target.get("pool_id") or "gemini")

    for spec in MODEL_SPECS.values():
        s_comp = _compact(spec.display_name)
        if comp == s_comp or (len(s_comp) >= 8 and s_comp in comp and "sonnet" not in s_comp and "gpt" not in s_comp):
            return True, spec.pool_id
    return False, None


def _suggest_model_defaults(clean_name: str) -> tuple[str, float, float]:
    low = clean_name.lower()
    if "argon" in low:
        return "argon", 2.5, 10.0
    if "opus" in low:
        return "claude_gpt", 5.0, 25.0
    if "sonnet" in low or "claude" in low:
        return "claude_gpt", 3.0, 15.0
    if "gpt" in low or "o3" in low or "o4" in low:
        return "claude_gpt", 2.5, 10.0
    if "flash" in low:
        return "gemini", 0.075, 0.30
    return "gemini", 1.25, 5.0


def parse_model_column(
    left_tokens: list[OcrToken],
    custom_models: dict[str, Any],
) -> list[dict[str, Any]]:
    """Parst die linke Modell-Liste in bereinigte Modell-Vorschläge."""
    if not left_tokens:
        return []
    lines = _group_tokens_by_y(left_tokens)
    models: list[dict[str, Any]] = []
    seen_comp: set[str] = set()
    after_view_usage = False

    for line in lines:
        raw = " ".join(t.text for t in line).strip()
        raw_low = raw.lower()
        if any(skip in raw_low or _compact(skip) in _compact(raw_low) for skip in _SKIP_LEFT_PHRASES):
            if "view" in raw_low and "usage" in raw_low:
                after_view_usage = True
            continue
        if after_view_usage:
            # Unterhalb des "View Usage"-Buttons steht oft nur das aktuell aktive Modell nochmals
            continue

        clean_name, tier, badge, status = _clean_model_line(raw)
        if len(clean_name) < 3 or not re.search(r"[a-zA-Z]{2,}", clean_name):
            continue
        # Rausch-Zeilen ohne Modellbezug ausfiltern
        comp = _compact(clean_name)
        if comp in seen_comp:
            continue
        seen_comp.add(comp)

        slug = _slugify_model(clean_name)
        known, known_pool = _is_known_model(clean_name, slug, custom_models)
        sug_pool, sug_in, sug_out = _suggest_model_defaults(clean_name)
        models.append(
            {
                "name": clean_name,
                "slug": slug,
                "tier": tier,
                "badge": badge,
                "status": status,
                "availability": status.lower(),
                "known": known,
                "pool_id": known_pool or sug_pool,
                "suggested_pool_id": known_pool or sug_pool,
                "suggested_input_per_1m": sug_in,
                "suggested_output_per_1m": sug_out,
                "raw_text": raw,
            }
        )
    return models


def extract_selected_model(
    left_tokens: list[OcrToken],
    custom_models: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Erkennt das aktuell im UI gewählte Modell samt Effort-Stufe (z. B. unter 'View Usage' oder per '✓')."""
    if not left_tokens:
        return None
    lines = _group_tokens_by_y(left_tokens)
    after_view_usage = False
    fallback_checked: dict[str, Any] | None = None

    for line in lines:
        raw = " ".join(t.text for t in line).strip()
        raw_low = raw.lower()
        if "view" in raw_low and "usage" in raw_low:
            after_view_usage = True
            continue
        if after_view_usage:
            if any(skip in raw_low or _compact(skip) in _compact(raw_low) for skip in _SKIP_LEFT_PHRASES):
                continue
            clean_name, tier, _, _ = _clean_model_line(raw)
            if len(clean_name) >= 3 and re.search(r"[a-zA-Z]{2,}", clean_name):
                slug = _slugify_model(clean_name)
                _, known_pool = _is_known_model(clean_name, slug, custom_models)
                sug_pool, _, _ = _suggest_model_defaults(clean_name)
                return {
                    "name": clean_name,
                    "effort": tier,
                    "display": f"{clean_name} ({tier})" if tier else clean_name,
                    "pool_id": known_pool or sug_pool,
                    "source": "ocr_selector",
                }
        elif fallback_checked is None and ("✓" in raw or "✔" in raw):
            clean_name, tier, _, _ = _clean_model_line(raw)
            if len(clean_name) >= 3 and re.search(r"[a-zA-Z]{2,}", clean_name):
                slug = _slugify_model(clean_name)
                _, known_pool = _is_known_model(clean_name, slug, custom_models)
                sug_pool, _, _ = _suggest_model_defaults(clean_name)
                fallback_checked = {
                    "name": clean_name,
                    "effort": tier,
                    "display": f"{clean_name} ({tier})" if tier else clean_name,
                    "pool_id": known_pool or sug_pool,
                    "source": "ocr_checkmark",
                }
    return fallback_checked


# -----------------------------------------------------------------------------
# Rechte Usage-Spalte: Zustandsautomat + Ring-Fusion
# -----------------------------------------------------------------------------

def _build_expected_slots(pools_cfg: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    slots: list[tuple[str, str]] = []
    for pid, pcfg in pools_cfg.items():
        if pcfg.get("has_weekly", True):
            slots.append((pid, "weekly"))
        if pcfg.get("has_5h", True):
            slots.append((pid, "5h"))
    return slots


def _fuse_slot_with_ring(
    *,
    pool_id: str,
    pool_label: str,
    horizon: str,
    ocr_pct: float | None,
    resets_in: str,
    ring: dict[str, Any] | None,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    ring_pct = ring["ring_pct"] if ring else None
    ring_color = ring["ring_color"] if ring else None
    mismatch = False

    if ocr_pct is not None and ring is not None:
        diff = abs(ocr_pct - float(ring_pct))
        if ocr_pct > 100.0:
            mismatch = True
            pct_left = float(ring_pct)
            confidence = "medium"
            source = "ring_fallback"
            warnings.append(
                {
                    "code": "ocr_ring_mismatch",
                    "pool_id": pool_id,
                    "horizon": horizon,
                    "ocr_pct": ocr_pct,
                    "ring_pct": ring_pct,
                    "message": (
                        f"{pool_label} ({horizon}): Ungültiger OCR-Wert {ocr_pct:g}% "
                        f"durch Ring-Tracker ({ring_pct:g}%, {ring_color}) ersetzt."
                    ),
                }
            )
        elif diff <= 12.0:
            pct_left = float(ocr_pct)
            confidence = "high"
            source = "ocr+ring"
        else:
            mismatch = True
            pct_left = float(ocr_pct)
            confidence = "low"
            source = "ocr+ring"
            warnings.append(
                {
                    "code": "ocr_ring_mismatch",
                    "pool_id": pool_id,
                    "horizon": horizon,
                    "ocr_pct": ocr_pct,
                    "ring_pct": ring_pct,
                    "message": (
                        f"{pool_label} ({horizon}): Abweichung zwischen OCR ({ocr_pct:g}%) "
                        f"und Ring-Tracker ({ring_pct:g}%, Δ {diff:.1f}%)."
                    ),
                }
            )
    elif ocr_pct is None and ring is not None:
        pct_left = float(ring_pct)
        confidence = "medium"
        source = "ring_fallback"
    elif ocr_pct is not None and ring is None:
        if ocr_pct > 100.0:
            pct_left = None
            confidence = "low"
            source = "ocr_invalid"
            mismatch = True
            warnings.append(
                {
                    "code": "ocr_invalid",
                    "pool_id": pool_id,
                    "horizon": horizon,
                    "ocr_pct": ocr_pct,
                    "message": f"{pool_label} ({horizon}): Unmöglicher OCR-Wert {ocr_pct:g}% ohne Ring.",
                }
            )
        else:
            pct_left = float(ocr_pct)
            confidence = "medium"
            source = "ocr_only"
    else:
        pct_left = None
        confidence = "low"
        source = "missing"

    return {
        "pool_id": pool_id,
        "pool_label": pool_label,
        "horizon": horizon,
        "pct_left": round(pct_left, 1) if pct_left is not None else None,
        "resets_in": resets_in,
        "resets_in_hours": parse_resets_in(resets_in),
        "ocr_pct": round(ocr_pct, 1) if ocr_pct is not None else None,
        "ring_pct": round(float(ring_pct), 1) if ring_pct is not None else None,
        "ring_color": ring_color,
        "confidence": confidence,
        "source": source,
        "mismatch": mismatch,
        "ring": ring,
    }


def _parse_right_usage_column(
    right_tokens: list[OcrToken],
    rings: list[dict[str, Any]],
    pools_cfg: dict[str, dict[str, Any]],
    img_height: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    if not right_tokens:
        return [], warnings

    x_min_u = min(t.x0 for t in right_tokens)
    x_max_u = max(t.x1 for t in right_tokens)
    x_mid_u = x_min_u + max(60.0, (x_max_u - x_min_u) * 0.45)

    heights = sorted(t.height for t in right_tokens)
    med_h = heights[len(heights) // 2] if heights else 12.0

    # Pool-Header & Horizont-Marker auf der linken Seite des Popovers finden
    pool_headers: list[dict[str, Any]] = []
    horizon_markers: list[dict[str, Any]] = []
    pct_items: list[dict[str, Any]] = []
    reset_items: list[dict[str, Any]] = []

    lines = _group_tokens_by_y(right_tokens)
    for line in lines:
        # Tokens dieser y-Zeile links vs. rechts prüfen (da links "Weekly limit" und rechts "61% left" stehen)
        left_part = [t for t in line if t.xc <= x_mid_u or _match_pool_header(t.text, pools_cfg)[0] is not None]
        right_part = [t for t in line if t not in left_part]

        # Falls ganzer Zeilen-Text ein Pool-Header ist (z. B. "Claude and GPT models" ragt über Mitte)
        full_txt = " ".join(t.text for t in line).strip()
        pid_full, plbl_full = _match_pool_header(full_txt, pools_cfg)
        if pid_full and not any(_extract_pct_from_text(t.text)[0] is not None for t in line):
            pool_headers.append(
                {"pool_id": pid_full, "label": plbl_full, "y0": min(t.y0 for t in line), "yc": sum(t.yc for t in line) / len(line)}
            )
            continue

        if left_part:
            ltxt = " ".join(t.text for t in left_part).strip()
            pid, plbl = _match_pool_header(ltxt, pools_cfg)
            if pid:
                pool_headers.append(
                    {"pool_id": pid, "label": plbl, "y0": min(t.y0 for t in left_part), "yc": sum(t.yc for t in left_part) / len(left_part)}
                )
            else:
                lc = _compact(ltxt)
                if "weekly" in lc:
                    horizon_markers.append({"horizon": "weekly", "yc": sum(t.yc for t in left_part) / len(left_part)})
                elif "5h" in lc:
                    horizon_markers.append({"horizon": "5h", "yc": sum(t.yc for t in left_part) / len(left_part)})

        # Rechte Seite: Prozentwert und/oder "Resets in ..."
        for tok in (right_part or line):
            pct_val, has_left = _extract_pct_from_text(tok.text)
            if pct_val is not None or (has_left and not _extract_reset_text(tok.text)):
                pct_items.append({"yc": tok.yc, "pct": pct_val})
            rst = _extract_reset_text(tok.text)
            if rst:
                reset_items.append({"yc": tok.yc, "resets_in": rst})

        # Falls "61%" und "left" in getrennten Tokens auf derselben Zeile standen
        if right_part and not any(_extract_pct_from_text(t.text)[0] is not None for t in right_part):
            rtxt = " ".join(t.text for t in right_part).strip()
            pct_val, has_left = _extract_pct_from_text(rtxt)
            if pct_val is not None:
                pct_items.append({"yc": sum(t.yc for t in right_part) / len(right_part), "pct": pct_val})

    if not pool_headers:
        return [], warnings

    pool_headers.sort(key=lambda p: p["yc"])
    horizon_markers.sort(key=lambda h: h["yc"])
    pct_items.sort(key=lambda p: p["yc"])
    reset_items.sort(key=lambda r: r["yc"])

    # Dedupliziere pct_items die auf derselben Zeile entstanden sind
    dedup_pct: list[dict[str, Any]] = []
    for it in pct_items:
        if dedup_pct and abs(it["yc"] - dedup_pct[-1]["yc"]) <= med_h * 0.5:
            if dedup_pct[-1]["pct"] is None and it["pct"] is not None:
                dedup_pct[-1] = it
            continue
        dedup_pct.append(it)
    pct_items = dedup_pct

    used_rings: set[int] = set()
    used_pcts: set[int] = set()
    used_resets: set[int] = set()
    snapshots: list[dict[str, Any]] = []

    y_tol = max(6.0, med_h * 0.65)
    for idx, ph in enumerate(pool_headers):
        y_start = ph["y0"] - y_tol
        y_end = (pool_headers[idx + 1]["y0"] - y_tol) if idx + 1 < len(pool_headers) else float(img_height + 10)

        h_in_pool = [hm for hm in horizon_markers if y_start <= hm["yc"] < y_end]
        if not h_in_pool:
            # Fallback: aus Pool-Config ableiten falls Header erkannt wurde
            pcfg = pools_cfg.get(ph["pool_id"], {})
            if pcfg.get("has_weekly", True):
                h_in_pool.append({"horizon": "weekly", "yc": ph["yc"] + med_h * 1.4})
            if pcfg.get("has_5h", True):
                h_in_pool.append({"horizon": "5h", "yc": ph["yc"] + med_h * 3.8})

        ring_indices = [
            ri for ri, rg in enumerate(rings)
            if ri not in used_rings and y_start <= rg["y_center"] < y_end
        ]
        pct_indices = [
            pi for pi, pc in enumerate(pct_items)
            if pi not in used_pcts and y_start <= pc["yc"] < y_end
        ]
        reset_indices = [
            si for si, rs in enumerate(reset_items)
            if si not in used_resets and y_start <= rs["yc"] < y_end
        ]

        # Jeder Horizont im Pool bekommt das nächste passende (pct, ring, reset) im Teil-Band
        for hj, hm in enumerate(h_in_pool):
            sub_y0 = y_start if hj == 0 else (h_in_pool[hj - 1]["yc"] + hm["yc"]) * 0.5
            sub_y1 = y_end if hj + 1 == len(h_in_pool) else (hm["yc"] + h_in_pool[hj + 1]["yc"]) * 0.5

            # Prozent-Token wählen (im Teil-Band bevorzugt)
            cand_p = [pi for pi in pct_indices if pi not in used_pcts and sub_y0 <= pct_items[pi]["yc"] < sub_y1]
            chosen_p_idx = min(cand_p, key=lambda pi: abs(pct_items[pi]["yc"] - hm["yc"])) if cand_p else None
            ocr_pct: float | None = None
            ref_y = hm["yc"]
            if chosen_p_idx is not None:
                used_pcts.add(chosen_p_idx)
                ocr_pct = pct_items[chosen_p_idx]["pct"]
                ref_y = pct_items[chosen_p_idx]["yc"]

            # Ring wählen: anhand ref_y (wenn pct gefunden) oder Horizont-y
            cand_r = [ri for ri in ring_indices if ri not in used_rings and sub_y0 <= rings[ri]["y_center"] < sub_y1]
            if not cand_r and len(h_in_pool) == 1:
                cand_r = [ri for ri in ring_indices if ri not in used_rings]
            chosen_ring: dict[str, Any] | None = None
            if cand_r:
                # Toleranz ~ 1.2 * Zeilen-/Horizontabstand (innerhalb Pool-Band immer gültig)
                max_dy = max(24.0, (y_end - y_start) * 0.85)
                best_ri = min(cand_r, key=lambda ri: abs(rings[ri]["y_center"] - ref_y))
                if abs(rings[best_ri]["y_center"] - ref_y) <= max_dy:
                    used_rings.add(best_ri)
                    chosen_ring = rings[best_ri]

            # Reset-Text wählen (liegt unterhalb oder auf gleicher Höhe wie Horizont)
            cand_s = [si for si in reset_indices if si not in used_resets and sub_y0 <= reset_items[si]["yc"] < sub_y1]
            resets_in = ""
            if cand_s:
                best_si = min(cand_s, key=lambda si: abs(reset_items[si]["yc"] - hm["yc"]))
                used_resets.add(best_si)
                resets_in = reset_items[best_si]["resets_in"]

            snap = _fuse_slot_with_ring(
                pool_id=ph["pool_id"],
                pool_label=str(ph["label"] or ph["pool_id"]),
                horizon=hm["horizon"],
                ocr_pct=ocr_pct,
                resets_in=resets_in,
                ring=chosen_ring,
                warnings=warnings,
            )
            if snap["pct_left"] is not None or snap["resets_in"]:
                snapshots.append(snap)

    return snapshots, warnings


def parse_usage_screenshot(
    image_input: bytes | bytearray | str | Image.Image,
    *,
    agy_dir: Path | None = None,  # Signatur-Kompatibilität
    cal_path: Path | None = None,
    ocr_boxes: list[Any] | None = None,
) -> dict[str, Any]:
    """Parst einen Antigravity Usage-Screenshot per RapidOCR + Kreisring-Tracker.

    Args:
        image_input: PNG/JPG/WebP-Bytes, Base64/Data-URL oder PIL.Image.
        cal_path: Optionaler Pfad zu `usage_calibration.yaml`.
        ocr_boxes: Optionale injizierte OCR-Boxen (für deterministische Tests).
    """
    del agy_dir  # aktuell ungenutzt, für einheitliche API beibehalten
    img_rgb = decode_image_input(image_input)
    w, h = img_rgb.size
    cfg = load_calibration(cal_path)
    pools_cfg = cfg.get("pools") or {
        pid: {"label": p.label, "has_5h": p.has_5h, "has_weekly": p.has_weekly}
        for pid, p in DEFAULT_POOLS.items()
    }
    custom_models = cfg.get("custom_models") or {}

    if ocr_boxes is not None:
        tokens = _normalize_ocr_boxes(ocr_boxes, scale=1.0)
        ocr_available = len(tokens) > 0
        ocr_engine = "injected" if tokens else "mock_empty"
    else:
        init_scale = 2.0 if (w < 320 or h < 200) else 1.0
        tokens, ocr_available, ocr_engine = _run_ocr_on_image(img_rgb, scale=init_scale)

    split_x, has_model_col, usage_x0, usage_x1 = _detect_split_x(img_rgb, tokens)

    # Ring-Tracker immer auf unskaliertem Originalbild ausführen
    if tokens and usage_x1 > usage_x0 + 80:
        ring_x_min = max(int(split_x), int(usage_x0 + 0.65 * (usage_x1 - usage_x0)))
        ring_x_max = min(w, int(usage_x1 + 48))
        rings = detect_and_measure_rings(img_rgb, x_min=ring_x_min, x_max=ring_x_max)
    else:
        rings = detect_and_measure_rings(img_rgb, x_min=int(split_x))

    left_tokens = [t for t in tokens if has_model_col and t.xc < split_x]
    right_tokens = [t for t in tokens if not has_model_col or t.xc >= split_x]

    snapshots, warnings = _parse_right_usage_column(right_tokens, rings, pools_cfg, h)

    # Falls 1x-OCR auf kleinem Bild Zahlen oder Zeilen übersehen hat, einmal 2x BICUBIC nachprüfen
    if (
        ocr_boxes is None
        and ocr_available
        and init_scale == 1.0
        and rings
        and (
            len(snapshots) < len(rings)
            or any(s["ocr_pct"] is None for s in snapshots)
        )
    ):
        tokens_2x, ok_2x, _ = _run_ocr_on_image(img_rgb, scale=2.0)
        if ok_2x and tokens_2x:
            sx2, hm2, ux0_2, ux1_2 = _detect_split_x(img_rgb, tokens_2x)
            rt2 = [t for t in tokens_2x if not hm2 or t.xc >= sx2]
            snaps2, warns2 = _parse_right_usage_column(rt2, rings, pools_cfg, h)
            score_1 = sum(1 for s in snapshots if s["ocr_pct"] is not None)
            score_2 = sum(1 for s in snaps2 if s["ocr_pct"] is not None)
            if len(snaps2) > len(snapshots) or (len(snaps2) == len(snapshots) and score_2 > score_1):
                snapshots, warnings = snaps2, warns2
                if hm2 and not left_tokens:
                    left_tokens = [t for t in tokens_2x if t.xc < sx2]

    # Reiner Ring-Fallback wenn OCR fehlt oder keine rechten Usage-Header erkannt hat
    method = "ocr+ring"
    if not snapshots and rings:
        method = "ring_fallback"
        expected_slots = _build_expected_slots(pools_cfg)
        if not ocr_available:
            warnings.append(
                {
                    "code": "ocr_unavailable",
                    "message": "OCR nicht verfügbar – Werte rein über den 360°-Kreisring-Tracker bestimmt.",
                }
            )
        else:
            warnings.append(
                {
                    "code": "ring_fallback_only",
                    "message": "Keine Pool-Texte erkannt – Ringe in Standardreihenfolge zugeordnet.",
                }
            )
        for i, ring in enumerate(rings):
            if i < len(expected_slots):
                pid, horizon = expected_slots[i]
                plbl = str(pools_cfg.get(pid, {}).get("label") or pid)
            else:
                pid, horizon = f"pool_{i + 1}", "weekly"
                plbl = pid
            snapshots.append(
                _fuse_slot_with_ring(
                    pool_id=pid,
                    pool_label=plbl,
                    horizon=horizon,
                    ocr_pct=None,
                    resets_in="",
                    ring=ring,
                    warnings=warnings,
                )
            )
    elif snapshots and any(s["source"] == "ring_fallback" for s in snapshots):
        method = "ocr+ring_fallback"

    detected_models = parse_model_column(left_tokens, custom_models)
    selected_model = extract_selected_model(left_tokens, custom_models)

    return {
        "ok": True,
        "image_size": [w, h],
        "has_model_column": has_model_col,
        "split_x": round(split_x, 1),
        "ocr_available": ocr_available,
        "ocr_engine": ocr_engine,
        "method": method,
        "snapshots": snapshots,
        "rings": rings,
        "detected_models": detected_models,
        "selected_model": selected_model,
        "warnings": warnings,
    }
