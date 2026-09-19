"""
Kulup renkleri testleri (14S): data/open/club_colors.json (Wikidata P6364/P465, CC0 1.0),
data/open/club_colors_curated.json (Wikidata rengi olmayan 1. kademe kulupleri icin elle derleme),
club_colors.py (baslik bandi zemin/yazi) ve tools/build_club_colors.py'nin ag kullanmayan parcalari.

    * vendor dosyasi semasi, kaynak/lisans/tarih alanlari, kapsam sayilari
    * renk kodu bicimi (#RRGGBB)
    * kontrast kurali (WCAG 2; yazi >= 3.0, acik birincil -> koyu zemin, beyaz/siyah kulupler)
    * bilinmeyen takim (sentetik / maskeli ad) -> None
    * bilinen kulupler: Fenerbahce lacivert, Galatasaray kirmizi, Besiktas siyah/beyaz,
      Trabzonspor bordo, Basaksehir turuncu zemin
    * elle derleme: ayni sema, Wikidata'yi ASLA ezmez, kontrast kuralina uyar; 1. kademe kapsami
    * cevrimdisi yeniden uretim (onbellekten) depodaki dosyayla birebir ayni

Tamamen saf: veritabani ve ag yok; CM_TEST_NO_DB=1 ile calisir.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import club_colors  # noqa: E402
from club_colors import band_colors, colors_for, contrast_ratio  # noqa: E402
from club_directory import known_clubs  # noqa: E402

OPEN_DIR = ROOT / "data" / "open"
CURATED_KEYS = {"name", "colors", "source", "note"}
HEX = re.compile(r"^#[0-9A-F]{6}$")
QID = re.compile(r"^Q\d+$")
ENTRY_KEYS = {"name", "wikidata", "colors", "source", "license"}


def _load_tool():
    """tools/ paket degil: gelistirici aracini dosya yolundan yukle (ag baglantisi KURULMAZ)."""
    path = ROOT / "tools" / "build_club_colors.py"
    spec = importlib.util.spec_from_file_location("build_club_colors", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module             # dataclass'lar modulu sys.modules'ta arar
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def doc() -> dict:
    return json.loads((OPEN_DIR / "club_colors.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def open_clubs() -> dict:
    return {c["id"]: c for c in json.loads((OPEN_DIR / "clubs.json").read_text(encoding="utf-8"))["clubs"]}


@pytest.fixture(scope="module")
def curated() -> dict:
    return json.loads((OPEN_DIR / "club_colors_curated.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def roster_names() -> dict[str, str]:
    """Oyundaki (DB) takim adi -> kulup kimligi: leagues.json kadrolari (open_loader bu adi yazar)."""
    leagues = json.loads((OPEN_DIR / "leagues.json").read_text(encoding="utf-8"))
    return {e["name"]: e["id"] for lg in leagues["leagues"] for e in lg["clubs"]}


# ---------------------------------------------------------------------------
# 1) sema ve lisans
# ---------------------------------------------------------------------------

def test_header_source_license_and_date(doc):
    assert doc["schema"] == club_colors.SCHEMA == "ofm/open-club-colors"
    assert doc["license"] == "CC0-1.0"
    assert "publicdomain/zero/1.0" in doc["license_url"]
    assert doc["source"] == "Wikidata"
    assert doc["source_url"].startswith("https://www.wikidata.org/")
    assert doc["generator"] == "tools/build_club_colors.py"
    assert doc["properties"]["official_color"] == "P6364"
    assert doc["properties"]["hex"] == "P465"
    date.fromisoformat(doc["fetched_at"])
    assert "Wikidata" in doc["notice"] and "CC0" in doc["notice"]


def test_entries_schema_and_license(doc):
    assert doc["clubs"], "renkli kulup yok"
    for club_id, entry in doc["clubs"].items():
        assert ENTRY_KEYS <= set(entry), club_id
        assert entry["source"] in ("wikidata", "curated"), club_id
        assert entry["license"] == "CC0", club_id
        if entry["source"] == "wikidata":
            assert QID.match(entry["wikidata"]), club_id
        else:
            assert entry["wikidata"] is None or QID.match(entry["wikidata"]), club_id
            assert entry["note"], club_id
        assert entry["colors"], club_id
        assert isinstance(entry["name"], str) and entry["name"], club_id
        assert entry["names"] and all(isinstance(n, str) for n in entry["names"]), club_id
        if "colors_from" in entry:
            assert QID.match(entry["colors_from"]), club_id


def test_every_open_club_is_accounted_for(doc, open_clubs):
    """Her acik veri kulubu ya renkli ya da gerekcesiyle renksiz listede; kapsam sayilari tutarli."""
    colored, without = set(doc["clubs"]), set(doc["without_colors"])
    assert not colored & without
    assert colored | without == set(open_clubs)
    coverage = doc["coverage"]
    assert coverage["clubs"] == len(open_clubs)
    assert coverage["with_colors"] == len(colored)
    sources = [e["source"] for e in doc["clubs"].values()]
    assert coverage["wikidata"] == sources.count("wikidata")
    assert coverage["curated"] == sources.count("curated")
    assert coverage["wikidata"] + coverage["curated"] == coverage["with_colors"]
    matched = {cid for section in ("clubs", "without_colors")
               for cid, e in doc[section].items() if e["wikidata"]}
    assert coverage["matched"] == len(matched)
    for club_id, entry in doc["without_colors"].items():
        assert entry["reason"], club_id
        assert entry["wikidata"] is None or QID.match(entry["wikidata"]), club_id
    for club_id, entry in doc["clubs"].items():
        assert entry["name"] == open_clubs[club_id]["name"], club_id


def test_one_wikidata_item_per_club(doc):
    items = [e["wikidata"] for e in doc["clubs"].values()]
    items += [e["wikidata"] for e in doc["without_colors"].values() if e["wikidata"]]
    assert len(items) == len(set(items)), "ayni Wikidata ogesi iki kulube eslenmis"


# ---------------------------------------------------------------------------
# 2) renk bicimi
# ---------------------------------------------------------------------------

def test_hex_format(doc):
    for club_id, entry in doc["clubs"].items():
        for code in entry["colors"]:
            assert HEX.match(code), (club_id, code)
        assert len(entry["colors"]) == len(set(entry["colors"])), club_id


def test_colors_for_returns_hex_pair(roster_names):
    hits = [colors_for(name) for name in roster_names]
    hits = [h for h in hits if h is not None]
    assert hits
    for background, text in hits:
        assert HEX.match(background) and HEX.match(text)


# ---------------------------------------------------------------------------
# 3) kontrast kurali
# ---------------------------------------------------------------------------

def test_contrast_ratio_reference_values():
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert contrast_ratio("#FFFFFF", "#FF0000") == pytest.approx(4.0, abs=0.01)
    with pytest.raises(ValueError):
        contrast_ratio("red", "#000000")


def test_secondary_used_when_contrast_is_enough():
    assert band_colors(["#FF0000", "#FFFFFF"]) == ("#FF0000", "#FFFFFF")
    assert band_colors(["#000080", "#FFFF00"]) == ("#000080", "#FFFF00")


def test_fallback_ink_when_secondary_too_close():
    # mavi uzerinde siyah 2.4:1 -> beyaz (8.6:1) ; sari uzerinde beyaz 1.07:1 -> koyu
    assert band_colors(["#0000FF", "#000000"]) == ("#0000FF", "#FFFFFF")
    assert band_colors(["#87CEEB", "#FFFFFF"]) == ("#87CEEB", club_colors.NEAR_BLACK)
    assert band_colors(["#FFFF00"]) == ("#FFFF00", club_colors.NEAR_BLACK)
    assert band_colors(["#C8102E"]) == ("#C8102E", "#FFFFFF")


def test_light_primary_moves_to_text():
    """Acik birincil (sari/beyaz) + koyu renk: koyu renk zemin, acik renk yazi (CM 01/02 bandi)."""
    assert band_colors(["#FFFF00", "#000080"]) == ("#000080", "#FFFF00")       # Fenerbahce sirasi
    assert band_colors(["#FFFF00", "#FF0000"]) == ("#FF0000", "#FFFF00")       # Galatasaray sirasi
    assert band_colors(["#FFFFFF", "#0000FF"]) == ("#0000FF", "#FFFFFF")
    assert band_colors(["#FFFF00", "#FF0000", "#0000FF"]) == ("#FF0000", "#FFFF00")


def test_white_and_black_only_clubs():
    white, black = "#FFFFFF", "#000000"
    assert band_colors([white]) == (white, club_colors.NEAR_BLACK)
    assert band_colors([black]) == (black, white)
    assert band_colors([white, black]) == (black, white)
    assert band_colors([black, white]) == (black, white)
    assert band_colors([white, white.lower()]) == (white, club_colors.NEAR_BLACK)


def test_band_input_hygiene():
    assert band_colors([]) is None
    assert band_colors(None) is None
    assert band_colors(["kirmizi", "#12345"]) is None
    assert band_colors(["ff0000", "#ffffff"]) == ("#FF0000", "#FFFFFF")


def test_every_club_band_is_legible(doc):
    """Verideki HER kulubun bandi: yazi zeminden farkli ve kontrast >= 3.0 (WCAG buyuk metin)."""
    for club_id, entry in doc["clubs"].items():
        band = band_colors(entry["colors"])
        assert band is not None, club_id
        background, text = band
        assert background in entry["colors"], club_id
        assert text in entry["colors"] or text in ("#FFFFFF", club_colors.NEAR_BLACK), club_id
        assert background != text, club_id
        assert contrast_ratio(background, text) >= club_colors.MIN_TEXT_CONTRAST, (club_id, band)


# ---------------------------------------------------------------------------
# 4) ad ile arama ve bilinmeyen takim
# ---------------------------------------------------------------------------

def test_every_colored_open_team_resolves_by_game_name(doc, roster_names):
    """Acik veri dunyasinin DB'deki her takim adi kendi kulubunun bandini verir; renksizler None."""
    for name, club_id in roster_names.items():
        entry = doc["clubs"].get(club_id)
        expected = band_colors(entry["colors"]) if entry else None
        assert colors_for(name) == expected, (name, club_id)


def test_lookup_is_accent_and_case_insensitive_but_exact():
    base = colors_for("Fenerbahçe")
    assert base is not None
    assert colors_for("FENERBAHCE") == base
    assert colors_for("  fenerbahçe ") == base
    assert colors_for("Fenerbahçe İstanbul") == base            # clubs.json resmi adi
    assert colors_for("Fenerbahçe SK") == base                  # takma ad
    assert colors_for("Fenerbahçe Kadın") is None               # tam esleme: onek yetmez
    assert colors_for("Fener") is None
    assert colors_for("Beşiktaş") == colors_for("besiktas")


def test_unknown_teams_return_none():
    for name in ("", "   ", "Istanbul Lions", "Kadıköy Canaries", "Takım 7", "Real Madrid Castilla"):
        assert colors_for(name) is None, name
    assert colors_for(None) is None                             # type: ignore[arg-type]
    assert colors_for(42) is None                               # type: ignore[arg-type]


def test_masked_and_synthetic_names_return_none():
    """Sentetik/maskeli dunyanin kulup adlari (club_directory.masked) hic renk almaz."""
    for club in known_clubs():
        assert colors_for(club.masked) is None, club.masked


def test_missing_or_broken_file_means_no_colors(tmp_path):
    assert club_colors.build_index(club_colors.load_document(tmp_path / "yok.json")) == {}
    broken = tmp_path / "bozuk.json"
    broken.write_text("{not json", encoding="utf-8")
    assert club_colors.load_document(broken) == {}
    wrong = tmp_path / "sema.json"
    wrong.write_text(json.dumps({"schema": "baska", "clubs": {}}), encoding="utf-8")
    assert club_colors.load_document(wrong) == {}


def test_ambiguous_and_colorless_names_are_not_used():
    doc = {"schema": club_colors.SCHEMA, "clubs": {
        "x/a": {"name": "Alpha FC", "colors": ["#FF0000"], "names": ["Alpha"], "aliases": ["Ortak", "Beta"]},
        "x/c": {"name": "Gamma FC", "colors": ["#0000FF"], "names": ["Gamma"], "aliases": ["Ortak"]},
    }, "without_colors": {
        "x/b": {"name": "Beta FC", "wikidata": None, "reason": "-", "names": ["Beta"], "aliases": []},
    }}
    index = club_colors.build_index(doc)
    # saf kirmizi: beyaz 4.0:1, koyu 4.7:1 -> kural geregi koyu yazi
    assert index[club_colors.name_key("Alpha")] == ("#FF0000", club_colors.NEAR_BLACK)
    assert index[club_colors.name_key("gamma fc")] == ("#0000FF", "#FFFFFF")
    assert club_colors.name_key("Ortak") not in index           # iki kulube cikan takma ad
    assert club_colors.name_key("Beta") not in index            # renksiz kulubun adi baskasina gecmez


# ---------------------------------------------------------------------------
# 5) bilinen kulupler (veride varsa)
# ---------------------------------------------------------------------------

def _rgb(code: str) -> tuple[int, int, int]:
    return int(code[1:3], 16), int(code[3:5], 16), int(code[5:7], 16)


def test_fenerbahce_band_is_navy():
    band = colors_for("Fenerbahçe")
    if band is None:
        pytest.skip("veride Fenerbahçe rengi yok")
    r, g, b = _rgb(band[0])
    assert b > r and b > g and max(r, g, b) <= 0x99, band       # koyu mavi / lacivert
    tr, tg, tb = _rgb(band[1])
    assert tr > 0xC0 and tg > 0xB0 and tb < 0x60, band          # sari yazi


def test_galatasaray_band_is_red_or_orange():
    band = colors_for("Galatasaray")
    if band is None:
        pytest.skip("veride Galatasaray rengi yok")
    r, g, b = _rgb(band[0])
    assert r >= 0xA0 and r > g + 0x30 and r > b + 0x60, band


def test_trabzonspor_band_is_claret():
    band = colors_for("Trabzonspor")
    if band is None:
        pytest.skip("veride Trabzonspor rengi yok")
    r, g, b = _rgb(band[0])
    assert 0x60 <= r <= 0xB0 and g < 0x40 and b < 0x60, band   # bordo


def test_basaksehir_band_is_orange():
    band = colors_for("İstanbul Başakşehir")
    if band is None:
        pytest.skip("veride Başakşehir rengi yok")
    r, g, b = _rgb(band[0])
    assert r >= 0xD0 and 0x50 <= g <= 0xB0 and b < 0x50, band   # turuncu


def test_besiktas_band_is_black_or_white():
    band = colors_for("Beşiktaş")
    if band is None:
        pytest.skip("veride Beşiktaş rengi yok")
    assert band[0] in ("#000000", "#FFFFFF"), band
    assert contrast_ratio(*band) >= 15, band


# ---------------------------------------------------------------------------
# 6) elle derlenmis yedek (club_colors_curated.json)
# ---------------------------------------------------------------------------

def test_curated_file_schema(curated, open_clubs):
    assert curated["schema"] == "ofm/open-club-colors-curated"
    assert curated["source"] == "curated"
    assert curated["license"] == "CC0-1.0"
    assert "SI/FM" in curated["notice"] and "Wikidata" in curated["notice"]
    assert curated["clubs"]
    for club_id, entry in curated["clubs"].items():
        assert CURATED_KEYS <= set(entry), club_id
        assert entry["source"] == "curated", club_id
        assert entry["note"] == "kulübün bilinen forma/arma renkleri", club_id
        assert entry["name"] == open_clubs[club_id]["name"], club_id
        assert entry["colors"] and len(entry["colors"]) == len(set(entry["colors"])), club_id
        for code in entry["colors"]:
            assert HEX.match(code), (club_id, code)
    for club_id, entry in curated.get("omitted", {}).items():
        assert club_id in open_clubs and entry["reason"], club_id
        assert club_id not in curated["clubs"], club_id        # emin olunmayan kulup TAHMIN edilmez


def test_curated_never_overrides_wikidata(doc, curated):
    """Vendor dosyasi: elle derleme yalnizca Wikidata rengi olmayan kulupte; olan kulupte yok sayilir."""
    ignored = set(doc["curated_ignored"])
    for club_id, item in curated["clubs"].items():
        entry = doc["clubs"][club_id]
        if club_id in ignored:
            assert entry["source"] == "wikidata", club_id
        else:
            assert entry["source"] == "curated", club_id
            assert entry["colors"] == item["colors"], club_id
    curated_ids = {cid for cid, e in doc["clubs"].items() if e["source"] == "curated"}
    assert curated_ids <= set(curated["clubs"])


def test_apply_curated_precedence():
    """Birim: Wikidata'da renkli kulup (x/a) ezilmez; renksiz kulup (x/b) yedegi alir."""
    tool = _load_tool()
    entries = {"x/a": {"name": "A", "wikidata": "Q1", "colors": ["#FF0000"], "source": "wikidata",
                       "license": "CC0"}}
    without = {"x/b": {"name": "B", "wikidata": "Q2", "reason": "-", "names": ["B"], "aliases": []}}
    curated = {"x/a": {"colors": ["#00853F"], "color_names": ["green"], "note": "n"},
               "x/b": {"colors": ["#00853F"], "color_names": ["green"], "note": "n"}}
    assert tool.apply_curated(entries, without, curated) == ["x/a"]
    assert entries["x/a"]["colors"] == ["#FF0000"] and entries["x/a"]["source"] == "wikidata"
    assert entries["x/b"]["colors"] == ["#00853F"] and entries["x/b"]["source"] == "curated"
    assert entries["x/b"]["wikidata"] == "Q2" and not without


def test_curated_loader_rejects_bad_entries(tmp_path):
    tool = _load_tool()
    club = tool.Club(id="tr/x", name="X Spor", country="tr", founded=None, game_names=["X"], aliases=[])

    def load(entry):
        doc = {"schema": "ofm/open-club-colors-curated", "clubs": entry}
        (tmp_path / "club_colors_curated.json").write_text(json.dumps(doc), encoding="utf-8")
        return tool.load_curated(tmp_path, {"tr/x": club})

    good = {"name": "X Spor", "colors": ["#d71920"], "source": "curated", "note": "n"}
    assert load({"tr/x": good})["tr/x"]["colors"] == ["#D71920"]
    for bad in ({"tr/y": good},                                         # bilinmeyen kulup
                {"tr/x": {**good, "name": "Baska"}},                    # ad uyusmuyor
                {"tr/x": {**good, "colors": ["kirmizi"]}},              # gecersiz renk
                {"tr/x": {**good, "colors": []}},                       # bos
                {"tr/x": {**good, "source": "wikidata"}}):              # yanlis kaynak
        with pytest.raises(tool.BuildError):
            load(bad)


def test_curated_bands_meet_contrast_rule(curated):
    for club_id, entry in curated["clubs"].items():
        band = band_colors(entry["colors"])
        assert band is not None, club_id
        assert contrast_ratio(*band) >= club_colors.MIN_TEXT_CONTRAST, (club_id, band)


def test_tier1_coverage(doc, curated):
    """Varsayilan dunya (1. kademe): her kulubun rengi var; yalnizca bilerek disarida birakilanlar haric."""
    leagues = json.loads((OPEN_DIR / "leagues.json").read_text(encoding="utf-8"))
    tier1 = {e["id"] for lg in leagues["leagues"] if lg["tier"] == 1 for e in lg["clubs"]}
    missing = tier1 - set(doc["clubs"])
    assert missing <= set(curated.get("omitted", {})), sorted(missing)
    for league in leagues["leagues"]:
        if league["tier"] != 1:
            continue
        for entry in league["clubs"]:
            if entry["id"] not in missing:
                assert colors_for(entry["name"]) is not None, entry["name"]


# ---------------------------------------------------------------------------
# 6) arac (ag YOK)
# ---------------------------------------------------------------------------

def test_tool_network_guard():
    tool = _load_tool()
    tool._check_url("https://query.wikidata.org/sparql")
    tool._check_url("https://www.wikidata.org/w/api.php")
    for url in ("https://www.transfermarkt.com/", "http://query.wikidata.org/sparql",
                "https://query.wikidata.org.evil.example/sparql", "https://sortitoutsi.net/",
                "https://www.wikidata.org/wiki/Q1"):
        with pytest.raises(tool.BuildError):
            tool._check_url(url)


def test_tool_statement_filtering_keeps_order():
    tool = _load_tool()
    rows = [
        {"value": "Q943", "rank": "normal", "qualifiers": {}},
        {"value": "Q5975887", "rank": "normal", "qualifiers": {}},
        {"value": "Q3142", "rank": "deprecated", "qualifiers": {}},
        {"value": "Q23444", "rank": "normal", "qualifiers": {"P582": ["+1990-00-00T00:00:00Z"]}},
        {"value": None, "rank": "normal", "qualifiers": {}},
    ]
    assert tool.current_colors(rows) == ["Q943", "Q5975887"]
    preferred = rows + [{"value": "Q1088", "rank": "preferred", "qualifiers": {}}]
    assert tool.current_colors(preferred) == ["Q1088"]
    ordinal = [{"value": "Q1", "rank": "normal", "qualifiers": {"P1545": ["2"]}},
               {"value": "Q2", "rank": "normal", "qualifiers": {"P1545": ["1"]}}]
    assert tool.current_colors(ordinal) == ["Q2", "Q1"]


def test_offline_rebuild_matches_vendored_file(doc, tmp_path):
    """Onbellekten (--offline, ag yok) uretilen dosya depodakiyle birebir ayni."""
    tool = _load_tool()
    cache = OPEN_DIR / "_cache_colors"
    if not (cache / "search.json").is_file():
        pytest.skip("renk onbellegi yok")
    assert tool.main(["--offline", "--quiet", "--out", str(tmp_path)]) == 0
    rebuilt = json.loads((tmp_path / "club_colors.json").read_text(encoding="utf-8"))
    assert rebuilt == doc
