"""CFS (Creality Filament System): Spulen auslesen und Filament-Wünsche einer Spule zuordnen.

Der Drucker meldet über Moonraker das Klipper-Objekt `box`:
  T1 … T4 = CFS-Einheiten, je 4 Plätze A–D; material_type = RFID-Code (z. B. 101001 = Hyper PLA),
  color_value = '0RRGGBB', remain_len = Rest in %, map = logischer → physischer Platz.
Im G-Code wählt `T<n>` den n-ten logischen Platz (T0 = T1A, T1 = T1B, … T4 = T2A …).
"""

from __future__ import annotations

import math
import re

SLOT_ORDER = [f"T{u}{s}" for u in range(1, 5) for s in "ABCD"]

COLOR_NAMES = {
    "weiss": "FFFFFF", "weiß": "FFFFFF", "white": "FFFFFF",
    "schwarz": "000000", "black": "000000",
    "grau": "808080", "grey": "808080", "gray": "808080", "silber": "C0C0C0",
    "rot": "D02020", "red": "D02020",
    "gruen": "20A040", "grün": "20A040", "green": "20A040",
    "blau": "2050D0", "blue": "2050D0", "hellblau": "60A0E0",
    "gelb": "F0D020", "yellow": "F0D020",
    "orange": "F08020",
    "lila": "8040B0", "violett": "8040B0", "purple": "8040B0", "flieder": "9A8AC8",
    "braun": "8B5A2B", "brown": "8B5A2B", "rost": "9D432C",
    "pink": "F070B0", "rosa": "F0A0C0",
    "tuerkis": "30C0C0", "türkis": "30C0C0", "cyan": "30C0C0",
    "beige": "E0D0B0", "transparent": "F0F0F0",
}

# grobe Zuordnung RFID-Materialcode → Typ, falls der Drucker keinen Typ nennt
TYPE_BY_CODE_PREFIX = {"01": "PLA", "02": "PLA", "03": "PLA", "04": "PLA", "06": "PETG", "07": "ABS", "08": "ASA",
                       "09": "TPU", "05": "PETG"}


def _hex(c: str) -> str | None:
    if not c or c in ("-1", "None"):
        return None
    c = c.strip().lstrip("#")
    if len(c) == 7:          # Format '0RRGGBB'
        c = c[1:]
    return c.upper() if re.fullmatch(r"[0-9A-Fa-f]{6}", c) else None


def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def color_distance(a: str, b: str) -> float:
    """Wahrgenommener Farbabstand (gewichtetes RGB, 'redmean')."""
    r1, g1, b1 = _rgb(a)
    r2, g2, b2 = _rgb(b)
    rm = (r1 + r2) / 2
    return math.sqrt((2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - rm) / 256) * (b1 - b2) ** 2)


def color_name(h: str) -> str:
    best = min(((color_distance(h, v), k) for k, v in COLOR_NAMES.items()
                if k in ("weiss", "schwarz", "grau", "rot", "gruen", "blau", "gelb", "orange", "lila",
                         "braun", "pink", "tuerkis", "beige", "hellblau", "flieder", "rost")), default=(0, "?"))
    return best[1]


def parse_box(box: dict, rack: dict | None = None) -> dict:
    """Moonraker-Status von `box` (und `filament_rack`) → Liste der Plätze."""
    types_by_slot = {}
    for entry in box.get("same_material", []) or []:
        try:
            code, color, slots, typ = entry
            for s in slots:
                types_by_slot[s] = typ
        except (ValueError, TypeError):
            continue
    mapping = box.get("map") or {s: s for s in SLOT_ORDER}
    slots = []
    for unit in range(1, 5):
        u = box.get(f"T{unit}") or {}
        if u.get("state") in (None, "None") and u.get("sn") in (None, "-1"):
            continue
        codes = u.get("material_type") or []
        colors = u.get("color_value") or []
        remains = u.get("remain_len") or []
        for i, letter in enumerate("ABCD"):
            phys = f"T{unit}{letter}"
            code = str(codes[i]) if i < len(codes) else "-1"
            col = _hex(str(colors[i])) if i < len(colors) else None
            loaded = code not in ("-1", "None", "", "0") and col is not None
            # logischer Platz L mit map[L] == phys → G-Code-Werkzeug T<index von L>
            logical = next((L for L, P in mapping.items() if P == phys), phys)
            tool = SLOT_ORDER.index(logical) if logical in SLOT_ORDER else None
            typ = types_by_slot.get(phys)
            if not typ and loaded:
                typ = TYPE_BY_CODE_PREFIX.get(code[1:3], None)
            try:
                remain = int(float(remains[i])) if i < len(remains) else None
            except (TypeError, ValueError):
                remain = None
            slots.append({
                "slot": phys, "tool": tool, "loaded": loaded, "code": code if loaded else None,
                "type": typ if loaded else None, "color": f"#{col}" if loaded else None,
                "color_name": color_name(col) if loaded else None,
                "remain_pct": remain if loaded else None,
                "unit_humidity": u.get("dry_and_humidity"), "unit_temp": u.get("temperature"),
            })
    ext = None
    if rack and str(rack.get("material_type")) not in ("-1", "None", ""):
        col = _hex(str(rack.get("color_value")))
        ext = {"slot": "EXT", "code": str(rack.get("material_type")), "color": f"#{col}" if col else None}
    identity = all(mapping.get(s, s) == s for s in SLOT_ORDER)
    return {"connected": box.get("state") == "connect", "slots": slots, "external": ext,
            "map_identity": identity, "auto_refill": box.get("auto_refill")}


def match_profile(slot: dict, catalog: dict, default_by_type: dict) -> str | None:
    """Filamentprofil für eine Spule: gleiche filament_id wie der RFID-Code, sonst Standard des Typs."""
    code = slot.get("code") or ""
    cands = [n for n, e in catalog.items() if e.get("filament_id") and code.endswith(str(e["filament_id"]))]
    if cands:
        cands.sort(key=lambda n: (0 if re.search(r"Hi 0\.4", n) else 1 if "Hi" in n else 2,
                                  0 if not e_user(catalog, n) else 1, len(n)))
        return cands[0]
    typ = (slot.get("type") or "").lower()
    return default_by_type.get(typ)


def e_user(catalog: dict, name: str) -> bool:
    return bool(catalog.get(name, {}).get("user"))


def choose_slot(want: str | None, material: str | None, cfs: dict) -> dict:
    """Spule wählen. want: 'T1C', Farbname ('schwarz'), Hex ('#FF0000') oder None (erste passende).
    material: 'pla'/'petg' – nur Spulen dieses Typs."""
    loaded = [s for s in cfs["slots"] if s["loaded"]]
    if material:
        same = [s for s in loaded if (s["type"] or "").lower() == material.lower()]
        if not same:
            raise LookupError(f"Keine {material.upper()}-Spule im CFS geladen. Geladen: "
                              + ", ".join(f"{s['slot']} {s['type']} {s['color_name']}" for s in loaded))
        loaded = same
    if not loaded:
        raise LookupError("Keine Spule im CFS erkannt")
    if not want:
        return max(loaded, key=lambda s: (s["remain_pct"] or 0, -SLOT_ORDER.index(s["slot"])))
    w = want.strip()
    if re.fullmatch(r"T[1-4][A-Da-d]", w, re.I):
        s = next((s for s in cfs["slots"] if s["slot"] == w.upper()), None)
        if not s or not s["loaded"]:
            raise LookupError(f"Platz {w.upper()} ist leer oder nicht vorhanden")
        if material and (s["type"] or "").lower() != material.lower():
            raise LookupError(f"Platz {w.upper()} enthält {s['type']}, nicht {material.upper()}")
        return s
    target = COLOR_NAMES.get(w.lower()) or _hex(w)
    if not target:
        raise LookupError(f"'{want}' ist weder Platz (z. B. T1C) noch bekannte Farbe")
    best = min(loaded, key=lambda s: color_distance(target, s["color"].lstrip("#")))
    return best
