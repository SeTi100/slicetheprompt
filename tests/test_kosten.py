"""Tests für den Kostenrechner (ohne Drucker, Slicer und Internet)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slicetheprompt import kosten as K  # noqa: E402

SETTINGS = {"waehrung": "CHF", "strom_chf_kwh": 0.30, "leistung_w": 150, "verschleiss_chf_h": 0.20,
            "fehldruck_pct": 10,
            "rollen": {"pla": {"preis": 20.0, "gewicht_g": 1000}, "petg": {"preis": 30.0, "gewicht_g": 1000}}}


def test_parse_duration():
    assert K.parse_duration("20m 45s") == 1245
    assert K.parse_duration("1h 2m") == 3720
    assert K.parse_duration("1d 0h 1m 1s") == 86461
    assert K.parse_duration("1:02:03") == 3723
    assert K.parse_duration("") is None
    assert K.parse_duration("unbekannt") is None


def test_parse_product_page_jsonld_and_meta():
    page = ('<script type="application/ld+json">{"@type":"Product","name":"Creality Hyper PLA (1.75 mm, 1 kg)",'
            '"offers":{"@type":"Offer","price":"21.90","priceCurrency":"CHF"}}</script>')
    r = K.parse_product_page(page)
    assert r == {"preis": 21.9, "titel": "Creality Hyper PLA (1.75 mm, 1 kg)", "gewicht_g": 1000.0}
    r = K.parse_product_page('<meta property="product:price:amount" content="18.5">'
                             '<meta property="og:title" content="PETG 750 g Rolle">')
    assert r["preis"] == 18.5 and r["gewicht_g"] == 750.0
    try:
        K.parse_product_page("<html>nichts</html>")
        raise AssertionError("PreisFehler erwartet")
    except K.PreisFehler:
        pass


def test_fetch_only_shops():
    try:
        K.fetch_price("https://example.com/produkt")
        raise AssertionError("PreisFehler erwartet")
    except K.PreisFehler as exc:
        assert "Digitec" in str(exc)


def test_match_roll_specificity():
    rollen = [
        {"name": "PLA allgemein", "material": "pla", "preis": 25.0, "gewicht_g": 1000},
        {"name": "Hyper PLA schwarz", "material": "pla", "code": "101001", "farbe": "#000000", "preis": 19.0,
         "gewicht_g": 1000},
        {"name": "PETG 750", "material": "petg", "preis": 22.5, "gewicht_g": 750},
    ]
    schwarz = {"type": "PLA", "code": "101001", "color": "#050505", "slot": "T1D"}
    weiss = {"type": "PLA", "code": "101001", "color": "#FFFFFF", "slot": "T1B"}
    assert K.match_roll(schwarz, rollen, SETTINGS)["rolle"] == "Hyper PLA schwarz"
    assert K.match_roll(weiss, rollen, SETTINGS)["rolle"] == "PLA allgemein"
    assert abs(K.match_roll({"type": "PETG"}, rollen, SETTINGS)["preis_pro_g"] - 0.03) < 1e-9
    std = K.match_roll({"type": "PETG"}, [], SETTINGS)
    assert std["quelle"].startswith("standard") and std["preis"] == 30.0


def test_calc_and_price_file():
    with tempfile.TemporaryDirectory() as tmp:
        K.PREISE_YAML = Path(tmp) / "preise.yaml"
        res = K.set_price("PLA Test", preis=25.0, material="pla")
        assert res["rolle"]["preis"] == 25.0 and res["rolle"]["gewicht_g"] == 1000.0
        # Link ohne erreichbaren Shop und ohne Preis → Fehler, nichts gespeichert
        try:
            K.set_price("nur Link", url="https://example.com/x")
            raise AssertionError("PreisFehler erwartet")
        except K.PreisFehler:
            pass
        assert [r["name"] for r in K.load_prices()] == ["PLA Test"]

        out = K.calc({0: 40.0, 1: 10.0}, {0: {"type": "pla"}, 1: {"type": "petg"}}, 7200, SETTINGS)
        assert out["filament"] == round(40 * 0.025 + 10 * 0.03, 2)       # PLA aus Datei, PETG Standard
        assert out["strom"] == round(2 * 0.15 * 0.30, 2)
        assert out["verschleiss"] == 0.40
        assert abs(out["total"] - round((1.3 + 0.09 + 0.4) * 1.1, 2)) < 0.011
        assert [p["rolle"] for p in out["posten"]] == ["PLA Test", "Standard PETG"]

        K.delete_price("PLA Test")
        assert K.load_prices() == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("alle Kosten-Tests bestanden")
