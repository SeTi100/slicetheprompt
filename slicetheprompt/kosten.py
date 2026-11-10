"""Kostenrechner: Filament (je Spule) + Strom + Verschleiss für einen gesliceten Druck.

Preise je Rolle stehen lokal in config/preise.yaml (nicht im Git). Ein Eintrag gilt für alle Spulen,
deren Merkmale passen (material / code = RFID / profil / farbe / slot); der genaueste Eintrag gewinnt.
Ohne passenden Eintrag gilt der Standardpreis des Materials aus printer.yaml → kosten.rollen.

Preisquelle je Rolle: manuell (preis + gewicht_g) oder ein Digitec-/Galaxus-Link (url). Der Abruf
ist ein einzelner normaler Seitenaufruf; blockt der Shop ihn (Bot-Schutz), bleibt der zuletzt
bekannte bzw. manuelle Preis stehen – der Schutz wird nicht umgangen.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .config import CONFIG_DIR, load_config, write_yaml, _read_yaml

PREISE_YAML = CONFIG_DIR / "preise.yaml"
SHOPS = ("digitec.ch", "galaxus.ch", "galaxus.de", "galaxus.at", "galaxus.fr", "galaxus.it", "galaxus.nl",
         "galaxus.be")

DEFAULTS = {
    "waehrung": "CHF",
    "strom_chf_kwh": 0.30,
    "leistung_w": 150,
    "verschleiss_chf_h": 0.20,
    "fehldruck_pct": 0,
    "rollen": {"pla": {"preis": 22.90, "gewicht_g": 1000}, "petg": {"preis": 24.90, "gewicht_g": 1000}},
}


class PreisFehler(RuntimeError):
    pass


# ---------------------------------------------------------------- Einstellungen

def settings() -> dict:
    k = {**DEFAULTS, **(load_config().get("kosten") or {})}
    k["rollen"] = {**DEFAULTS["rollen"], **(k.get("rollen") or {})}
    return k


def load_prices() -> list[dict]:
    return list((_read_yaml(PREISE_YAML).get("rollen")) or [])


def _save_prices(rollen: list[dict]) -> None:
    write_yaml(PREISE_YAML, {"rollen": rollen},
               header="Filamentpreise je Rolle (lokal, nicht im Git). Gepflegt über das Tool filament_price,\n"
                      "die CLI 'uv run slicetheprompt preis …' oder das Studio. Von Hand ändern ist erlaubt.")


_FILTER_KEYS = ("material", "code", "profil", "farbe", "slot")


def set_price(name: str, preis: float | None = None, gewicht_g: float | None = None, url: str | None = None,
              abrufen: bool | None = None, **filter) -> dict:
    """Rolle anlegen oder ändern. Mit url (Digitec/Galaxus) wird der Preis abgerufen, falls kein manueller
    preis angegeben ist (oder abrufen=True); schlägt der Abruf fehl, bleibt der manuelle bzw. alte Preis und es gibt einen Hinweis."""
    if not name:
        raise ValueError("name fehlt (z. B. 'Hyper PLA schwarz')")
    rollen = load_prices()
    e = next((r for r in rollen if r.get("name") == name), None)
    if e is None:
        e = {"name": name}
        rollen.append(e)
    for k in _FILTER_KEYS:
        v = filter.get(k)
        if v not in (None, ""):
            e[k] = str(v).lower() if k in ("material", "farbe") else str(v)
    if gewicht_g:
        e["gewicht_g"] = float(gewicht_g)
    hinweis = None
    if preis is not None:
        e.update(preis=round(float(preis), 2), quelle="manuell", stand=_today())
    if url:
        e["url"] = url
        if abrufen or (abrufen is None and preis is None):
            try:
                got = fetch_price(url)
                e.update(preis=got["preis"], quelle=got["shop"], stand=_today(), titel=got.get("titel"))
                if got.get("gewicht_g") and not gewicht_g:
                    e["gewicht_g"] = got["gewicht_g"]
            except PreisFehler as exc:
                hinweis = str(exc)
    if "preis" not in e:
        raise PreisFehler((hinweis or "Kein Preis") + " – bitte preis manuell angeben.")
    e.setdefault("gewicht_g", 1000.0)
    _save_prices(rollen)
    return {"ok": True, "rolle": e, "hinweis": hinweis, "datei": str(PREISE_YAML)}


def delete_price(name: str) -> dict:
    rollen = load_prices()
    neu = [r for r in rollen if r.get("name") != name]
    if len(neu) == len(rollen):
        raise KeyError(f"Rolle '{name}' nicht gefunden")
    _save_prices(neu)
    return {"ok": True, "geloescht": name}


def refresh_prices() -> dict:
    """Alle Rollen mit url neu abrufen."""
    rollen = load_prices()
    res = []
    for e in rollen:
        if not e.get("url"):
            continue
        try:
            got = fetch_price(e["url"])
            alt = e.get("preis")
            e.update(preis=got["preis"], quelle=got["shop"], stand=_today())
            res.append({"name": e["name"], "alt": alt, "neu": got["preis"]})
        except PreisFehler as exc:
            res.append({"name": e["name"], "fehler": str(exc), "bleibt": e.get("preis")})
    _save_prices(rollen)
    return {"ok": True, "rollen": res}


def _today() -> str:
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------- Abruf Digitec / Galaxus

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/130.0 Safari/537.36")


def fetch_price(url: str, timeout: float = 10.0) -> dict:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host not in SHOPS:
        raise PreisFehler(f"Nur Links von Digitec oder Galaxus ({', '.join(SHOPS[:2])}) werden abgerufen")
    import requests
    try:
        r = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "de-CH,de;q=0.9",
                                       "Accept": "text/html,application/xhtml+xml"}, timeout=timeout)
    except requests.RequestException as exc:
        raise PreisFehler(f"{host} nicht erreichbar: {exc}") from exc
    if r.status_code in (401, 403, 429) or "Access denied" in r.text[:2000]:
        raise PreisFehler(f"{host} blockt automatische Abfragen (HTTP {r.status_code}, Bot-Schutz)")
    if not r.ok:
        raise PreisFehler(f"{host}: HTTP {r.status_code}")
    out = parse_product_page(r.text)
    out["shop"] = host.split(".")[0]
    return out


def parse_product_page(text: str) -> dict:
    """Preis, Titel und Gewicht aus einer Produktseite (JSON-LD, Meta-Tags, eingebettetes JSON)."""
    preis = titel = None
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S | re.I):
        try:
            data = json.loads(html.unescape(block))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if not isinstance(item, dict) or "Product" not in str(item.get("@type")):
                continue
            titel = titel or item.get("name")
            offers = item.get("offers") or {}
            for o in offers if isinstance(offers, list) else [offers]:
                p = o.get("price") or o.get("lowPrice")
                if p not in (None, ""):
                    preis = preis or float(str(p).replace(",", "."))
    if preis is None:
        m = (re.search(r'(?:product:price:amount|itemprop="price")[^>]*content="([\d.,]+)"', text)
             or re.search(r'"price"\s*:\s*\{[^{}]*?"amountInclusive"\s*:\s*([\d.]+)', text)
             or re.search(r'"price"\s*:\s*"?([\d]+[.,]\d{1,2})"?', text))
        if m:
            preis = float(m.group(1).replace(",", "."))
    if titel is None:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', text) or \
            re.search(r"<title>(.*?)</title>", text, re.S)
        titel = html.unescape(m.group(1)).strip() if m else None
    if preis is None:
        raise PreisFehler("Kein Preis auf der Seite gefunden")
    return {"preis": round(preis, 2), "titel": titel, "gewicht_g": weight_from_title(titel or "")}


def weight_from_title(t: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kg|g)\b", t, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * 1000 if m.group(2).lower() == "kg" else v


# ---------------------------------------------------------------- Rechnen

def _color_close(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    from .cfs import COLOR_NAMES, _hex, color_distance
    ha = COLOR_NAMES.get(a.lower()) or _hex(a)
    hb = COLOR_NAMES.get(b.lower()) or _hex(b)
    return bool(ha and hb and color_distance(ha, hb) < 120)


def match_roll(spool: dict, rollen: list[dict] | None = None, k: dict | None = None) -> dict:
    """Passende Rolle für eine Spule {type, code, profile, color, slot}. Liefert preis_pro_g und Herkunft."""
    rollen = load_prices() if rollen is None else rollen
    k = k or settings()
    mat = (spool.get("type") or spool.get("material") or "pla").lower()
    best, best_score = None, -1
    for e in rollen:
        score = 0
        checks = (("material", mat, 1), ("code", spool.get("code"), 4), ("profil", spool.get("profile"), 3),
                  ("slot", spool.get("slot"), 2))
        ok = True
        for key, val, w in checks:
            if key in e:
                if val is None or str(e[key]).lower() != str(val).lower():
                    ok = False
                    break
                score += w
        if ok and "farbe" in e:
            if not _color_close(e["farbe"], spool.get("color")):
                ok = False
            else:
                score += 2
        if ok and score > best_score and e.get("preis") is not None:
            best, best_score = e, score
    if best:
        g = float(best.get("gewicht_g") or 1000)
        return {"rolle": best["name"], "preis": best["preis"], "gewicht_g": g, "preis_pro_g": best["preis"] / g,
                "quelle": best.get("quelle", "manuell"), "stand": best.get("stand"), "url": best.get("url")}
    d = k["rollen"].get(mat) or next(iter(k["rollen"].values()))
    g = float(d.get("gewicht_g") or 1000)
    return {"rolle": f"Standard {mat.upper()}", "preis": d["preis"], "gewicht_g": g, "preis_pro_g": d["preis"] / g,
            "quelle": "standard (printer.yaml)", "stand": None, "url": None}


def parse_duration(s: str | None) -> int | None:
    """'1d 2h 3m 4s', '20m 45s', '1:02:03' → Sekunden."""
    if not s:
        return None
    s = str(s).strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    total, found = 0, False
    for v, u in re.findall(r"(\d+(?:\.\d+)?)\s*([dhms])", s):
        total += float(v) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u]
        found = True
    return int(total) if found else None


def calc(grams_per_tool: dict[int, float], spools: dict[int, dict], seconds: int | None,
         k: dict | None = None) -> dict:
    """Kosten aufschlüsseln. grams_per_tool: {Werkzeug: g}; spools: {Werkzeug: Spulen-Info}."""
    k = k or settings()
    rollen = load_prices()
    posten, fil = [], 0.0
    for tool, g in sorted(grams_per_tool.items()):
        if g <= 0:
            continue
        sp = spools.get(tool) or {"type": "pla"}
        r = match_roll(sp, rollen, k)
        chf = g * r["preis_pro_g"]
        fil += chf
        posten.append({"werkzeug": f"T{tool}", "spule": sp.get("slot"), "material": (sp.get("type") or "").upper(),
                       "farbe": sp.get("color"), "gramm": round(g, 1), "rolle": r["rolle"],
                       "rollenpreis": r["preis"], "rollengewicht_g": r["gewicht_g"], "quelle": r["quelle"],
                       "stand": r["stand"], "chf": round(chf, 2)})
    h = (seconds or 0) / 3600
    strom = h * k["leistung_w"] / 1000 * k["strom_chf_kwh"]
    versch = h * k["verschleiss_chf_h"]
    summe = fil + strom + versch
    zuschlag = summe * (k.get("fehldruck_pct") or 0) / 100
    return {
        "waehrung": k["waehrung"],
        "filament": round(fil, 2), "strom": round(strom, 2), "verschleiss": round(versch, 2),
        "fehldruck_zuschlag": round(zuschlag, 2), "total": round(summe + zuschlag, 2),
        "gramm": round(sum(grams_per_tool.values()), 1), "stunden": round(h, 2), "posten": posten,
        "annahmen": {"strom_chf_kwh": k["strom_chf_kwh"], "leistung_w": k["leistung_w"],
                     "verschleiss_chf_h": k["verschleiss_chf_h"], "fehldruck_pct": k.get("fehldruck_pct") or 0},
    }


def for_gcode(gcode: str | Path, slots: list[dict] | None = None, material: str | None = None) -> dict:
    """Kosten eines G-Codes. slots: Plan aus dem Slicen [{tool, slot, type, code, color, profile}]."""
    from .slicer import gcode_info
    info = gcode_info(gcode)
    secs = parse_duration(info.get("time"))
    types = info.get("filament_types") or []
    default_type = (material or (types[0] if types else "pla")).lower()
    spools = {s["tool"]: s for s in (slots or []) if s.get("tool") is not None}
    if info.get("filament_g_per_tool"):
        grams = dict(enumerate(info["filament_g_per_tool"]))
    else:
        grams = {next(iter(spools), 0): info.get("filament_g") or 0.0}
    for t in grams:
        spools.setdefault(t, {"type": (types[t] if t < len(types) else default_type).lower()})
    res = calc(grams, spools, secs)
    res["gcode"] = Path(gcode).name
    res["zeit"] = info.get("time")
    return res


def for_project(project: str) -> dict:
    """Kosten zum zuletzt gesliceten G-Code eines Projekts (Plan aus out/slice.json, falls vorhanden)."""
    from .config import project_dir
    d = project_dir(project)
    meta_p = d / "out" / "slice.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    gcode = Path(meta["gcode"]) if meta.get("gcode") and Path(meta["gcode"]).exists() else None
    if gcode is None:
        cands = sorted((d / "out").glob("*.gcode"), key=lambda p: p.stat().st_mtime)
        if not cands:
            raise FileNotFoundError("Noch nicht gesliced – erst slice aufrufen")
        gcode = cands[-1]
    return for_gcode(gcode, meta.get("slots"), meta.get("material"))
