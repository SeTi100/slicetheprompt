"""Unit- & Bild-Tests für slicetheprompt.usage_screenshot (RapidOCR + 360°-Kreisring-Tracker)."""

from __future__ import annotations

import base64
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slicetheprompt import usage_eval as UE  # noqa: E402
from slicetheprompt import usage_screenshot as US  # noqa: E402


BG_DARK = (24, 26, 31)       # #181a1f
BG_POPOVER = (19, 21, 26)
TRACK_GRAY = (62, 65, 74)
COLOR_GREEN = (74, 195, 92)
COLOR_AMBER = (232, 145, 22)
COLOR_RED = (218, 60, 62)


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    candidates = (
        ["C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"]
    )
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            continue
    return ImageFont.load_default(size=size)


def _draw_aa_ring(
    img: Image.Image,
    cx: float,
    cy: float,
    radius: float,
    pct: float,
    color: tuple[int, int, int] | None,
    stroke_px: float = 2.6,
) -> None:
    """Zeichnet einen weichen, anti-aliased Fortschrittsring mittels 4x-Supersampling."""
    ss = 4
    pad = int(radius + stroke_px + 6)
    box_sz = (pad * 2 + 1) * ss
    patch = Image.new("RGBA", (box_sz, box_sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(patch)

    pcx = pad * ss
    pcy = pad * ss
    pr = radius * ss
    sw = max(2, int(round(stroke_px * ss)))
    bbox = [pcx - pr, pcy - pr, pcx + pr, pcy + pr]

    # Volle graue Ring-Spur (Track)
    draw.ellipse(bbox, outline=TRACK_GRAY + (255,), width=sw)

    # Farbiger Fortschrittsbogen ab 12 Uhr (-90°) im Uhrzeigersinn
    if pct > 0.0 and color is not None:
        sweep = min(360.0, max(0.0, pct / 100.0 * 360.0))
        if sweep >= 359.5:
            draw.ellipse(bbox, outline=color + (255,), width=sw)
        else:
            draw.arc(bbox, start=-90.0, end=-90.0 + sweep, fill=color + (255,), width=sw)

    down = patch.resize((pad * 2 + 1, pad * 2 + 1), Image.Resampling.LANCZOS)
    img.paste(down, (int(round(cx)) - pad, int(round(cy)) - pad), down)


def build_synthetic_usage_screenshot(
    *,
    two_column: bool = True,
    values: dict[tuple[str, str], tuple[float, tuple[int, int, int] | None, str]] | None = None,
) -> Image.Image:
    """Erzeugt einen realitätsnahen Dark-Mode-Antigravity-Screenshot per PIL.ImageDraw."""
    vals = values or {
        ("gemini", "weekly"): (61.0, COLOR_GREEN, "1d 5h"),
        ("gemini", "5h"): (81.0, COLOR_GREEN, "2h 15m"),
        ("claude_gpt", "weekly"): (21.0, COLOR_AMBER, "2d 4h"),
        ("claude_gpt", "5h"): (100.0, COLOR_GREEN, ""),
        ("argon", "weekly"): (4.0, COLOR_RED, "2d 2h"),
    }

    w = 680 if two_column else 380
    h = 370
    img = Image.new("RGB", (w, h), BG_DARK)
    draw = ImageDraw.Draw(img)

    f_head = _load_font(15, bold=True)
    f_body = _load_font(14, bold=False)
    f_bold = _load_font(14, bold=True)
    f_small = _load_font(12, bold=False)

    u_x0 = 285 if two_column else 10
    if two_column:
        # Linke Modell-Spalte zeichnen
        draw.rounded_rectangle([6, 6, 274, h - 10], radius=8, fill=BG_DARK, outline=(48, 52, 62), width=1)
        left_rows = [
            ("Gemini 4 Argon", "Medium", "New", True),
            ("Gemini 4 Hydrogen", "Extra", "New", True),
            ("Gemini 3.8 Flash", "Medium", "", True),
            ("Claude Opus 5.5", "Medium", "New", True),
            ("Claude Sonnet 5.5", "Medium", "New", True),
            ("GPT-OSS 120B (Medium)", "", "Notice", True),
            ("Gemini 4 Carbon", "", "Unavailable", False),
        ]
        y_m = 22
        for name, tier, badge, avail in left_rows:
            col_txt = (228, 232, 240) if avail else (112, 118, 130)
            draw.text((18, y_m), name, fill=col_txt, font=f_body)
            x_cur = 18 + int(draw.textlength(name, font=f_body)) + 10
            if tier:
                draw.text((x_cur, y_m), tier, fill=(142, 148, 162), font=f_body)
                x_cur += int(draw.textlength(tier, font=f_body)) + 10
            if badge:
                draw.rounded_rectangle([x_cur - 4, y_m - 2, x_cur + int(draw.textlength(badge, font=f_small)) + 6, y_m + 18], radius=8, fill=(38, 42, 52))
                draw.text((x_cur + 1, y_m + 1), badge, fill=(170, 176, 190), font=f_small)
            y_m += 40
        draw.text((28, h - 46), "View Usage", fill=(210, 214, 224), font=f_body)

    # Rechte Usage-Spalte zeichnen
    draw.rounded_rectangle([u_x0, 10, w - 10, h - 10], radius=10, fill=BG_POPOVER, outline=(52, 56, 66), width=1)
    lx = u_x0 + 18
    rx_ring = w - 34
    rx_pct_anchor = rx_ring - 20

    draw.text((lx, 22), "Usage", fill=(168, 174, 188), font=f_body)

    def _draw_pct_line(y: int, pct: float, color: tuple[int, int, int] | None, reset_str: str) -> None:
        pct_txt = f"{int(round(pct))}% left"
        tw = draw.textlength(pct_txt, font=f_bold)
        draw.text((rx_pct_anchor - tw, y), pct_txt, fill=(242, 245, 250), font=f_bold)
        _draw_aa_ring(img, rx_ring, y + 8.0, 9.0, pct, color, stroke_px=2.5)
        if reset_str:
            rtxt = f"Resets in {reset_str}"
            rw = draw.textlength(rtxt, font=f_small)
            draw.text((w - 22 - rw, y + 22), rtxt, fill=(148, 154, 168), font=f_small)

    # Gemini Models
    draw.text((lx, 54), "Gemini Models", fill=(240, 244, 250), font=f_head)
    draw.text((lx, 80), "Weekly limit", fill=(148, 154, 168), font=f_body)
    _draw_pct_line(80, *vals[("gemini", "weekly")])

    draw.text((lx, 134), "5h limit", fill=(148, 154, 168), font=f_body)
    _draw_pct_line(134, *vals[("gemini", "5h")])

    # Claude and GPT models
    draw.text((lx, 186), "Claude and GPT models", fill=(240, 244, 250), font=f_head)
    draw.text((lx, 212), "Weekly limit", fill=(148, 154, 168), font=f_body)
    _draw_pct_line(212, *vals[("claude_gpt", "weekly")])

    draw.text((lx, 266), "5h limit", fill=(148, 154, 168), font=f_body)
    _draw_pct_line(266, *vals[("claude_gpt", "5h")])

    # Gemini 4 Argon
    draw.text((lx, 310), "Gemini 4 Argon", fill=(240, 244, 250), font=f_head)
    draw.text((lx, 334), "Weekly limit", fill=(148, 154, 168), font=f_body)
    _draw_pct_line(330, *vals[("argon", "weekly")])

    return img


def test_ring_tracker_accuracy_all_levels():
    """Prüft 360°-Winkelgenauigkeit (±3%) und Farbklassifizierung für 0%, 4%, 21%, 61%, 81%, 100%."""
    cases = [
        (0.0, None, "empty"),
        (4.0, COLOR_RED, "red"),
        (21.0, COLOR_AMBER, "amber"),
        (61.0, COLOR_GREEN, "green"),
        (81.0, COLOR_GREEN, "green"),
        (100.0, COLOR_GREEN, "green"),
    ]
    img = Image.new("RGB", (220, len(cases) * 44 + 20), BG_DARK)
    for i, (pct, col, _) in enumerate(cases):
        _draw_aa_ring(img, 190.0, 26.0 + i * 44.0, 9.0, pct, col, stroke_px=2.6)

    rings = US.detect_and_measure_rings(img, x_min=140)
    assert len(rings) == len(cases), f"Erwartet {len(cases)} Ringe, gefunden {len(rings)}: {rings}"
    for rg, (soll, _, exp_color) in zip(rings, cases):
        assert abs(rg["ring_pct"] - soll) <= 3.0, f"Ring {soll}% -> {rg['ring_pct']}% ausserhalb ±3%"
        assert rg["ring_color"] == exp_color, f"Ring {soll}% Farbe {rg['ring_color']} != {exp_color}"
        assert rg["method"] == "angle_360"


def test_full_screenshot_ocr_ring_and_models():
    """Prüft RapidOCR + Ring-Fusion + linke Modell-Erkennung auf synthetischem Dark-Mode-Screenshot."""
    img = build_synthetic_usage_screenshot(two_column=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

    with tempfile.TemporaryDirectory() as td:
        cal = Path(td) / "usage_calibration.yaml"
        res = US.parse_usage_screenshot(data_url, cal_path=cal)

    assert res["ok"] is True
    assert res["has_model_column"] is True
    assert res["ocr_available"] is True
    assert res["method"] == "ocr+ring"
    assert len(res["rings"]) == 5

    by_slot = {(s["pool_id"], s["horizon"]): s for s in res["snapshots"]}
    expected = {
        ("gemini", "weekly"): (61.0, "green", "1d 5h"),
        ("gemini", "5h"): (81.0, "green", "2h 15m"),
        ("claude_gpt", "weekly"): (21.0, "amber", "2d 4h"),
        ("claude_gpt", "5h"): (100.0, "green", ""),
        ("argon", "weekly"): (4.0, "red", "2d 2h"),
    }
    for key, (exp_pct, exp_col, exp_rst) in expected.items():
        assert key in by_slot, f"Slot {key} fehlt: {list(by_slot.keys())}"
        sn = by_slot[key]
        assert sn["pct_left"] == exp_pct
        assert abs(sn["ring_pct"] - exp_pct) <= 3.0
        assert sn["ring_color"] == exp_col
        assert sn["source"] == "ocr+ring"
        assert sn["confidence"] == "high"
        if exp_rst:
            assert UE.parse_resets_in(sn["resets_in"]) == UE.parse_resets_in(exp_rst)

    # Linke Modell-Spalte prüfen
    models = {m["name"]: m for m in res["detected_models"]}
    assert "View Usage" not in models
    assert "Gemini 4 Argon" in models and models["Gemini 4 Argon"]["known"] is True
    assert "Gemini 4 Hydrogen" in models and models["Gemini 4 Hydrogen"]["known"] is True
    assert "Gemini 3.8 Flash" in models and models["Gemini 3.8 Flash"]["known"] is True
    assert "Claude Opus 5.5" in models and models["Claude Opus 5.5"]["known"] is True

    assert "Claude Sonnet 5.5" in models and models["Claude Sonnet 5.5"]["known"] is False
    assert "GPT-OSS 120B (Medium)" in models and models["GPT-OSS 120B (Medium)"]["known"] is False
    assert models["GPT-OSS 120B (Medium)"]["status"] == "Notice"
    assert "Gemini 4 Carbon" in models
    assert models["Gemini 4 Carbon"]["known"] is False
    assert models["Gemini 4 Carbon"]["status"] == "Unavailable"


def test_empty_ocr_ring_fallback():
    """Ohne OCR (Fallback) liefert der Ring-Tracker trotzdem alle Pools auf ±3%."""
    img = build_synthetic_usage_screenshot(two_column=False)
    with tempfile.TemporaryDirectory() as td:
        cal = Path(td) / "usage_calibration.yaml"
        # 1. Explizit leere OCR-Boxen
        res = US.parse_usage_screenshot(img, cal_path=cal, ocr_boxes=[])
        assert res["ocr_available"] is False
        assert res["method"] == "ring_fallback"
        assert any(w["code"] == "ocr_unavailable" for w in res["warnings"])
        by_slot = {(s["pool_id"], s["horizon"]): s for s in res["snapshots"]}
        expected = {
            ("gemini", "weekly"): (61.0, "green"),
            ("gemini", "5h"): (81.0, "green"),
            ("claude_gpt", "weekly"): (21.0, "amber"),
            ("claude_gpt", "5h"): (100.0, "green"),
            ("argon", "weekly"): (4.0, "red"),
        }
        for key, (soll, col) in expected.items():
            assert abs(by_slot[key]["pct_left"] - soll) <= 3.0
            assert by_slot[key]["ring_color"] == col
            assert by_slot[key]["source"] == "ring_fallback"

        # 2. Ausfall von RapidOCR-Engine simulieren
        with mock.patch.object(US, "_get_ocr_engine", side_effect=ImportError("no rapidocr")):
            res2 = US.parse_usage_screenshot(img, cal_path=cal)
        assert res2["ocr_available"] is False
        assert res2["method"] == "ring_fallback"
        assert len(res2["snapshots"]) == 5


def test_ocr_ring_mismatch_and_correction():
    """Unmöglicher OCR-Wert (1000) wechselt auf Ring; starke Abweichung (1% vs 21%) warnt."""
    img = build_synthetic_usage_screenshot(two_column=False)
    # Synthetische OCR-Boxen passend zu build_synthetic_usage_screenshot(two_column=False)
    boxes = [
        ([[28, 22], [80, 22], [80, 38], [28, 38]], "Usage", 0.99),
        ([[28, 54], [140, 54], [140, 72], [28, 72]], "Gemini Models", 0.99),
        ([[28, 80], [110, 80], [110, 96], [28, 96]], "Weekly limit", 0.99),
        ([[260, 80], [326, 80], [326, 96], [260, 96]], "1000/0 left", 0.90),  # unmöglicher WinRT-Fehler!
        ([[28, 134], [90, 134], [90, 150], [28, 150]], "5h limit", 0.99),
        ([[270, 134], [326, 134], [326, 150], [270, 150]], "left", 0.90),        # Zahl verschluckt!
        ([[28, 186], [190, 186], [190, 204], [28, 204]], "Claude and GPT models", 0.99),
        ([[28, 212], [110, 212], [110, 228], [28, 228]], "Weekly limit", 0.99),
        ([[270, 212], [326, 212], [326, 228], [270, 228]], "1% left", 0.95),     # 1% statt 21% -> Warnung!
        ([[28, 266], [90, 266], [90, 282], [28, 282]], "5h limit", 0.99),
        ([[265, 266], [326, 266], [326, 282], [265, 282]], "100% left", 0.99),
        ([[28, 310], [145, 310], [145, 326], [28, 326]], "Gemini 4 Argon", 0.99),
        ([[28, 334], [110, 334], [110, 350], [28, 350]], "Weekly limit", 0.99),
        ([[270, 330], [326, 330], [326, 346], [270, 346]], "4% left", 0.99),
    ]
    with tempfile.TemporaryDirectory() as td:
        cal = Path(td) / "usage_calibration.yaml"
        res = US.parse_usage_screenshot(img, cal_path=cal, ocr_boxes=boxes)

    by_slot = {(s["pool_id"], s["horizon"]): s for s in res["snapshots"]}
    # 1000/0 -> direkt durch Ring (~61%) ersetzt + Mismatch-Warnung
    gw = by_slot[("gemini", "weekly")]
    assert gw["mismatch"] is True
    assert gw["source"] == "ring_fallback"
    assert abs(gw["pct_left"] - 61.0) <= 3.0

    # "left" ohne Zahl -> Ring-Fallback (~81%)
    g5 = by_slot[("gemini", "5h")]
    assert g5["source"] == "ring_fallback"
    assert g5["ocr_pct"] is None
    assert abs(g5["pct_left"] - 81.0) <= 3.0

    # 1% vs 21% -> Mismatch-Warnung + confidence="low"
    cw = by_slot[("claude_gpt", "weekly")]
    assert cw["mismatch"] is True
    assert cw["confidence"] == "low"
    assert abs(cw["ring_pct"] - 21.0) <= 3.0

    mismatch_warns = [w for w in res["warnings"] if w["code"] == "ocr_ring_mismatch"]
    assert len(mismatch_warns) == 2


def test_selected_model_and_effort_extraction():
    img = build_synthetic_usage_screenshot(two_column=True)
    def _bx(x0, y0, x1, y1, txt):
        return ([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], txt, 0.99)
    ocr_boxes = [
        _bx(12, 24, 180, 40, "Gemini 4 Hydrogen Extra"),
        _bx(12, 168, 130, 182, "View Usage"),
        _bx(12, 200, 195, 214, "Gemini 4 Hydrogen Extra ^"),
        _bx(300, 20, 360, 32, "Usage"),
        _bx(300, 42, 410, 56, "Gemini Models"),
        _bx(300, 62, 385, 75, "Weekly limit"),
        _bx(520, 62, 570, 75, "61% left"),
    ]
    with tempfile.TemporaryDirectory() as td:
        cal = Path(td) / "usage_calibration.yaml"
        res = US.parse_usage_screenshot(img, cal_path=cal, ocr_boxes=ocr_boxes)
    sel = res.get("selected_model")
    assert sel is not None
    assert sel["name"] == "Gemini 4 Hydrogen"
    assert sel["effort"] == "Extra"
    assert sel["pool_id"] == "gemini"


def run_all() -> None:
    tests = [
        test_ring_tracker_accuracy_all_levels,
        test_full_screenshot_ocr_ring_and_models,
        test_empty_ocr_ring_fallback,
        test_ocr_ring_mismatch_and_correction,
        test_selected_model_and_effort_extraction,
    ]
    for fn in tests:
        fn()
        print(f"OK: {fn.__name__}")
    print(f"Alle {len(tests)} usage_screenshot-Tests bestanden.")


if __name__ == "__main__":
    run_all()
