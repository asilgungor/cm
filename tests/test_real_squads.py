"""
16G (sahip karari K-S1 "IKISI BIRDEN"): gercek oyuncu kadrolari -- Wikidata iskeleti + sahibin FM yamasi.

    * onay bayragi: OFM_ALLOW_REAL_PLAYERS=1 + acik secim (real_players / --real-players) ikisi birden sart
    * varsayilan dunya bit-bit ayni (16A-0 taban ozeti, tests/test_open_world.py)
    * kadro boyutu ve yas akli; uretilmis tamamlama oyunculari ic etiketli (data_source="synthetic")
    * FM yamasi: tutucu esleme, eslesen oyuncuda FM ozellikleri kazanir, eslesmeyen listelenir
    * determinizm (ayni tohum + ayni dosyalar = ayni dunya; hash tuzundan bagimsiz)
    * arac (tools/build_squads.py): yalnizca Wikidata, --offline agsiz, kisisel veri yalnizca data/local/
    * paylasilan dunya reddi: kurulum, cevirme ve CLI hedef semasi (entegrasyon, test veritabani)

AG YOK. Kadro dosyasi KURGUSAL bir fiksturdur (gercek kisi adi icermez); gercek data/local/squads.json kullanilmaz.
DB'siz testler CM_TEST_NO_DB=1 ile de calisir. Entegrasyon:
    TEST_DB_NAME=fm_db_test_16g python -m pytest -q -p no:cacheprovider tests/test_real_squads.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fm_parser  # noqa: E402
import open_loader  # noqa: E402
import seed  # noqa: E402
from models import Position  # noqa: E402
from tests.test_open_world import OPEN_WORLD_TIER1_DIGEST, _tier1_digest  # noqa: E402

REFERENCE = date(2026, 7, 1)
CONSENT = seed.REAL_PLAYERS_ENV


def _load_tool():
    """tools/ paket degil: araci dosya yolundan yukle (ag baglantisi KURULMAZ)."""
    path = ROOT / "tools" / "build_squads.py"
    spec = importlib.util.spec_from_file_location("build_squads", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_squads"] = module
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# Kurgusal kadro fiksturu
# ===========================================================================

def _player(qid: int, name: str, birth: str, position: str | None, *, nationality: str | None = "Turkey",
            fresh: int = 2, sitelinks: int = 10, number: int | None = None) -> dict:
    return {"qid": f"Q{qid}", "name": name, "birth_date": birth, "nationality": nationality,
            "nationality_qid": None, "position": position, "positions": [], "shirt_number": number,
            "since": "2025-07-01", "fresh": fresh, "sitelinks": sitelinks}


@pytest.fixture(scope="module")
def open_data():
    return open_loader.load_open_data()


@pytest.fixture(scope="module")
def sample_clubs(open_data):
    """--open-sample dunyasindaki (lig basina 6 kulup) Super Lig ve Premier Lig kulupleri: {kod: [kulup]}."""
    out = {}
    for league in open_data.leagues["leagues"]:
        if league["code"] in ("tr.1", "en.1"):
            reps = open_loader.league_reputations(league)
            out[league["code"]] = sorted(open_loader.sample_roster(league["clubs"], reps, 6), key=lambda c: c["id"])
    return out


@pytest.fixture(scope="module")
def squads_file(tmp_path_factory, sample_clubs):
    """
    Kurgusal squads.json: A kulubu 3 oyuncu (kucuk kadro), B kulubu 35 oyuncu (kalabalik, bayat girdiler), C kulubu
    FM yamasi adaylari. Diger kulupler dosyada yok (tamami uretilir).
    """
    a, b = sample_clubs["tr.1"][0], sample_clubs["tr.1"][1]
    c = sample_clubs["en.1"][0]
    big = [_player(2000 + i, f"Kalabalık Oyuncu {i:02d}", f"{1990 + i % 12}-03-{1 + i % 27:02d}",
                   ("GK", "DEF", "MID", "FWD")[i % 4] if i % 9 else None,
                   fresh=2 if i < 20 else 0, sitelinks=100 - i)
           for i in range(35)]
    doc = {
        "schema": open_loader.SQUADS_SCHEMA, "version": 1, "license": "CC0-1.0", "source": "Wikidata",
        "personal_data": True, "fetched_at": "2026-09-19", "reference_date": REFERENCE.isoformat(),
        "clubs": {
            a["id"]: {"name": a["name"], "players": [
                _player(1001, "Deneme Kaleci", "1995-02-10", "GK", number=1, sitelinks=30),
                _player(1002, "Örnek Şükrü Çağlayan", "2003-11-30", "FWD", nationality="Brazil", number=9,
                        sitelinks=55),
                _player(1003, "Mevkisiz Oyuncu", "2008-01-15", None, nationality=None, sitelinks=2),
                _player(1004, "Çok Genç", "2012-05-05", "MID"),                     # 14 yas: alinmaz
            ]},
            b["id"]: {"name": b["name"], "players": big},
            c["id"]: {"name": c["name"], "players": [
                _player(3001, "Jørgen Ødegård Testsen", "1998-12-17", "MID", nationality="Norway", sitelinks=60),
                _player(3002, "Heung-tae Kurgu", "1992-07-08", "FWD", nationality="South Korea", sitelinks=50),
                _player(3003, "Yaşlı Eşleşmez", "1990-01-01", "DEF", sitelinks=20),
                _player(3004, "Ayni Ad", "2000-04-03", "DEF", sitelinks=5),
                _player(3005, "Ayni Ad", "2000-06-03", "DEF", sitelinks=4),
                _player(3006, "Dogum Tarihli", "1999-03-04", "GK", sitelinks=8),
            ]},
        },
    }
    path = tmp_path_factory.mktemp("squads") / "squads.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def consent(monkeypatch):
    monkeypatch.setenv(CONSENT, "1")


def _real_world(squads_file, seed_value: int = 7, **kwargs):
    squads = open_loader.load_squads(squads_file)
    return open_loader.build_real_world(seed_value, squads, sample=True, **kwargs)


def _club(world, name: str):
    return next(c for c in world.clubs if c.name == name)


def _fingerprint(world) -> list:
    return [[c.name, c.reputation, [(p.name, p.age, p.position.value, p.overall, sorted(p.attributes.items()),
                                     p.potential, p.data_source, p.nationality) for p in (*c.players, *c.academy)]]
            for c in world.clubs]


# ===========================================================================
# 1) Onay bayragi
# ===========================================================================

def test_consent_flag_is_required(squads_file, monkeypatch):
    monkeypatch.delenv(CONSENT, raising=False)
    monkeypatch.setenv("OFM_ALLOW_REAL_NAMES", "1")          # FM ad izni gercek kisi kadrolarina izin DEGILDIR
    squads = open_loader.load_squads(squads_file)
    with pytest.raises(open_loader.OpenDataError, match=CONSENT):
        open_loader.build_real_world(7, squads, sample=True)
    with pytest.raises(seed.SeedError, match=CONSENT):
        seed.resolve_world(7, source="open", open_sample=True, real_players=True, squads_path=squads_file)
    for value in ("0", "", "hayır", "false"):
        monkeypatch.setenv(CONSENT, value)
        assert not seed.real_players_allowed()


def test_env_flag_alone_never_builds_real_players(squads_file, consent):
    """Bayrak tek basina bir sey acmaz: acik secim (real_players / --real-players) yoksa dunya uretilmis kalir."""
    world = seed.resolve_world(2026, source="open", open_sample=True)
    assert world.real_players is None and world.mask_summary.level == "light"
    assert {p.data_source for c in world.clubs for p in c.players} <= {"open", "academy"}
    assert seed.build_arg_parser().parse_args([]).real_players is False
    assert seed.new_world_source() in seed.NEW_WORLD_SOURCES and "real" not in " ".join(seed.NEW_WORLD_SOURCES)


def test_real_players_only_on_the_open_world_with_mask_off(squads_file, consent):
    for kwargs in ({"source": "synthetic"}, {"source": "fm"}, {"source": "open", "mask_level": "light"},
                   {"source": "open", "mask_level": "strong"}):
        with pytest.raises(seed.SeedError, match="açık veri dünyasına"):
            seed.resolve_world(7, open_sample=True, real_players=True, squads_path=squads_file, **kwargs)
    world = seed.resolve_world(7, source="auto", open_sample=True, real_players=True, squads_path=squads_file,
                               fm_overlay=False)
    assert world.mask_summary.off and world.mask_summary.real_players > 0
    assert "GERÇEK OYUNCULAR" in world.mask_summary.text()


def test_missing_squads_file_is_a_clear_error(tmp_path, consent):
    with pytest.raises(seed.SeedError, match="build_squads"):
        seed.resolve_world(7, source="open", open_sample=True, real_players=True,
                           squads_path=tmp_path / "yok.json", fm_overlay=False)


# ===========================================================================
# 2) Varsayilan dunya degismedi
# ===========================================================================

def test_default_open_world_is_bit_identical(consent):
    """16A-0 taban ozeti: gercek oyuncu kodu ve onay bayragi varken bile varsayilan acik veri dunyasi ayni."""
    assert _tier1_digest(open_loader.build_open_world(42)) == OPEN_WORLD_TIER1_DIGEST


# ===========================================================================
# 3) Kadro boyutu, yas, uretilmis tamamlama
# ===========================================================================

def test_squad_sizes_ages_and_validation(squads_file, sample_clubs, consent):
    world = _real_world(squads_file)
    assert seed.validate_world(world) == []
    for club in world.clubs:
        keepers = sum(p.position is Position.GK for p in club.players)
        assert open_loader.REAL_SQUAD_TARGET <= len(club.players) <= open_loader.REAL_SQUAD_MAX, club.name
        assert keepers >= open_loader.REAL_POSITION_MINIMUM[Position.GK]
        for position, minimum in open_loader.REAL_POSITION_MINIMUM.items():
            assert sum(p.position is position for p in club.players) >= minimum, (club.name, position)
        assert all(15 <= p.age <= 45 for p in (*club.players, *club.academy))

    small = _club(world, sample_clubs["tr.1"][0]["name"])
    real = {p.name: p for p in small.players if p.data_source == "open"}
    assert set(real) == {"Deneme Kaleci", "Örnek Şükrü Çağlayan", "Mevkisiz Oyuncu"}        # 14 yasindaki alinmadi
    assert real["Deneme Kaleci"].age == 31 and real["Örnek Şükrü Çağlayan"].age == 22 and real["Mevkisiz Oyuncu"].age == 18
    assert real["Deneme Kaleci"].position is Position.GK and real["Mevkisiz Oyuncu"].position is Position.MID
    assert real["Deneme Kaleci"].nationality == "Türkiye" and real["Örnek Şükrü Çağlayan"].nationality == "Brezilya"
    assert (real["Deneme Kaleci"].shirt_number, real["Örnek Şükrü Çağlayan"].wikidata_id) == (1, "Q1002")
    coverage = next(c for c in world.real_players.clubs if c.club == small.name)
    assert (coverage.in_file, coverage.used, coverage.dropped_age, coverage.guessed) == (4, 3, 1, 1)
    assert coverage.generated == len(small.players) - 3


def test_generated_fill_ins_are_labelled_internally_only(squads_file, sample_clubs, consent):
    world = _real_world(squads_file)
    real_names = {p["name"] for club in json.loads(squads_file.read_text(encoding="utf-8"))["clubs"].values()
                  for p in club["players"]}
    for club in world.clubs:
        for p in club.players:
            assert p.data_source in ("open", "synthetic", "fm", "academy")
            if p.data_source != "open":
                assert p.name not in real_names and p.wikidata_id is None
            assert "üretilmiş" not in p.name.lower() and "generated" not in p.name.lower()   # arayuzde iz yok
    untouched = _club(world, sample_clubs["tr.1"][2]["name"])                               # dosyada yok
    assert {p.data_source for p in untouched.players} == {"synthetic"}
    report = world.real_players
    assert report.real == sum(p.data_source in ("open", "fm") for c in world.clubs for p in c.players)
    assert report.generated == sum(p.data_source in ("synthetic", "academy") for c in world.clubs for p in c.players)
    assert any("Gerçek oyuncu kadroları" in line for line in report.lines())


def test_ability_comes_from_the_club_band_and_real_players_top_the_ladder(squads_file, sample_clubs, consent):
    """Yetenek Wikidata'da yok: kulup itibar bandindan uretilir; taninmis gercek oyuncular merdivenin tepesinde."""
    world = _real_world(squads_file)
    crowded = _club(world, sample_clubs["tr.1"][1]["name"])
    low, high = open_loader.strength_band(crowded.reputation)
    assert all(low - 1 <= p.overall <= high + 1 for p in crowded.players if p.data_source == "open")
    top = max(crowded.players, key=lambda p: p.overall)
    assert top.wikidata_id in {f"Q{2000 + i}" for i in range(6)}          # en taninmis olanlardan biri
    small = _club(world, sample_clubs["tr.1"][0]["name"])                   # 3 gercek + uretilmis tamamlama
    real = [p.overall for p in small.players if p.data_source == "open"]
    fill = [p.overall for p in small.players if p.data_source == "synthetic"]
    assert sum(real) / len(real) > sum(fill) / len(fill)                    # tamamlama merdivenin altinda
    untouched = _club(world, sample_clubs["tr.1"][2]["name"])               # dosyada yok: ayni bant
    low2, high2 = open_loader.strength_band(untouched.reputation)
    assert all(low2 - 1 <= p.overall <= high2 + 1 for p in untouched.players if p.data_source == "synthetic")


def test_crowded_squad_is_capped_keeping_fresh_members(squads_file, sample_clubs, consent):
    world = _real_world(squads_file)
    crowded = _club(world, sample_clubs["tr.1"][1]["name"])
    kept = {p.wikidata_id for p in crowded.players if p.data_source == "open"}
    assert len(crowded.players) <= open_loader.REAL_SQUAD_MAX
    assert sum(p.position is Position.GK for p in crowded.players) <= open_loader.REAL_MAX_KEEPERS
    fresh = {f"Q{2000 + i}" for i in range(20)}                             # yuksek guven
    stale = {f"Q{2000 + i}" for i in range(20, 35)}                          # dusuk guven
    assert fresh <= kept and len(kept & stale) < len(stale)                  # tavanda once dusuk guven duser
    coverage = next(c for c in world.real_players.clubs if c.club == crowded.name)
    assert coverage.used + coverage.trimmed + coverage.dropped_age == coverage.in_file == 35
    assert coverage.confidence == {"high": 20, "low": coverage.used - 20}


def test_low_confidence_only_fills_up_to_the_target_and_can_be_excluded(squads_file, sample_clubs, consent):
    """Dusuk guvenli (bayat olabilecek) kayit kadroyu yalnizca REAL_SQUAD_TARGET'e kadar doldurur; esik yukseltilebilir."""
    stale = {f"Q{2000 + i}" for i in range(20, 35)}
    world = _real_world(squads_file)
    crowded = _club(world, sample_clubs["tr.1"][1]["name"])
    kept = {p.wikidata_id for p in crowded.players if p.data_source == "open"}
    assert len(kept & stale) == open_loader.LOW_CONFIDENCE_UNTIL - 20                # 20 yuksek guvenliden sonra
    strict = _real_world(squads_file, min_confidence="medium")
    crowded = _club(strict, sample_clubs["tr.1"][1]["name"])
    assert not {p.wikidata_id for p in crowded.players} & stale
    assert len(crowded.players) >= open_loader.REAL_SQUAD_TARGET                   # yerini uretilmis oyuncu doldurur
    with pytest.raises(open_loader.OpenDataError, match="güven"):
        _real_world(squads_file, min_confidence="bazen")
    assert seed.build_arg_parser().parse_args(["--min-confidence", "high"]).min_confidence == "high"
    assert seed.build_arg_parser().parse_args([]).min_confidence == "low"


# ===========================================================================
# 4) FM yamasi
# ===========================================================================

FM_HEADER = ("Name", "Position", "Nat", "Age", "DoB", "Club", "CA", "PA", "Fin", "Pas", "Tck", "Pac", "Acc",
             "Han", "Ref")


def _fm_html(tmp_path: Path, rows: list[tuple]) -> Path:
    cells = "".join(f"<th>{h}</th>" for h in FM_HEADER)
    body = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows)
    path = tmp_path / "kadro_disa_aktarim.html"
    path.write_text(f"<html><body><table><tr>{cells}</tr>{body}</table></body></html>", encoding="utf-8")
    return path


def test_fm_parser_reads_birth_dates():
    assert fm_parser.parse_birth_date("17/12/1998 (27 years old)") == "1998-12-17"
    assert fm_parser.parse_birth_date("4.3.1999") == "1999-03-04"
    assert fm_parser.parse_birth_date("1999-03-04") == "1999-03-04"
    assert fm_parser.parse_birth_date("12/17/1998") == "1998-12-17"          # ay-gun sirasi (ay > 12)
    assert fm_parser.parse_birth_date("-") is None and fm_parser.parse_birth_date("31/02/2000") is None


def test_fm_overlay_matches_conservatively_and_fm_attributes_win(tmp_path, squads_file, sample_clubs, consent):
    club = sample_clubs["en.1"][0]
    fm_club = club["name"]
    path = _fm_html(tmp_path, [
        # aksansiz ad + yas 1 fark: ESLESIR
        ("Jorgen Odegard Testsen", "AM (C)", "NOR", "28", "-", fm_club, "160", "165", "14", "18", "9", "14",
         "15", "-", "-"),
        # ayni kelimeler baska sirada: ESLESIR
        ("Kurgu Heung-tae", "ST (C)", "KOR", "33", "-", fm_club, "150", "150", "17", "13", "5", "15",
         "15", "-", "-"),
        # yas 3 fark: Wikidata'yla ESLESMEZ -> FM ONCELIKLI: yalnizca-FM oyuncusu olarak girer, ayni adli
        # Wikidata kaydi (ayni kisi olabilir) kadroya alinmaz
        ("Yaşlı Eşleşmez", "D (C)", "TUR", "33", "-", fm_club, "120", "120", "5", "10", "15", "10",
         "10", "-", "-"),
        # ayni kulupte iki "Ayni Ad" (ikisi de 26 yas): BELIRSIZ -> yok sayilir (ayni kisi iki kez yazilmasin)
        ("Ayni Ad", "D (C)", "TUR", "26", "-", fm_club, "110", "120", "5", "10", "15", "10", "10", "-", "-"),
        # dogum tarihi gun/ay yer degistirmis (3/4 -> 4/3) + farkli ad yazimi, ayni soyad: ESLESIR (2. tur)
        ("D. Tarihli", "GK", "TUR", "40", "3/4/1999", fm_club, "130", "140", "-", "-", "-", "-", "-",
         "15", "16"),
        # dunyada olmayan kulup: yok sayilir
        ("Başka Biri", "M (C)", "ESP", "25", "-", "Uydurma Kulüp FC", "100", "100", "10", "10", "10", "10",
         "10", "-", "-"),
    ])
    world = seed.resolve_world(7, source="open", open_sample=True, real_players=True, squads_path=squads_file,
                               fm_paths=[path])
    report = world.real_players.fm
    assert report is not None and report.records == 6 and report.files == [path.name]
    assert {qid for _club, qid, _name in report.matched} == {"Q3001", "Q3002", "Q3006"}
    reasons = {name: reason for name, _club, reason in report.unmatched}
    assert reasons == {"Ayni Ad": open_loader.FM_AMBIGUOUS, "Başka Biri": open_loader.FM_NO_CLUB}
    assert report.added == [(club["id"], "Yaşlı Eşleşmez")] and report.duplicates == [(club["id"], "Yaşlı Eşleşmez")]

    squad = _club(world, club["name"])
    by_id = {p.wikidata_id: p for p in squad.players if p.wikidata_id}
    ode = by_id["Q3001"]
    assert ode.data_source == "fm" and ode.name == "Jørgen Ødegård Testsen"          # ad Wikidata'dan kalir
    assert ode.fm_attributes["finishing"] == 14 and ode.fm_attributes["passing"] == 18
    assert (ode.current_ability, ode.potential_ability) == (160, 165)
    assert ode.overall == fm_parser_overall(ode) and ode.age == 27
    assert by_id["Q3006"].position is Position.GK and by_id["Q3006"].data_source == "fm"
    assert "Q3003" not in by_id                                                      # FM kaydina yol verdi
    only = [p for p in squad.players if p.name == "Yaşlı Eşleşmez"]
    assert len(only) == 1 and only[0].data_source == "fm" and only[0].wikidata_id is None
    assert (only[0].age, only[0].nationality, only[0].current_ability) == (33, "Türkiye", 120)
    assert {p.name for p in squad.players if p.name == "Ayni Ad"} == {"Ayni Ad"}      # iki Wikidata kaydi kalir
    assert world.mask_summary.fm_players == 4
    coverage = next(c for c in world.real_players.clubs if c.club == club["name"])
    assert (coverage.fm, coverage.fm_only, coverage.fm_duplicates) == (3, 1, 1)

    # FM yalnizca kendi kulubune dokunur: diger kuluplerin Wikidata oyunculari FM'siz dunyayla ayni kalir. (Uretilmis
    # tamamlama adlari dunya capinda tekildir, bir ad cakismasi o kulubun RNG'sini kaydirabilir; potansiyel/akademi
    # add_youth_world'un tek RNG akisindan gelir. Ikisi de belirlenimli, yalnizca FM'li ve FM'siz dunya arasinda farkli.)
    plain = seed.resolve_world(7, source="open", open_sample=True, real_players=True, squads_path=squads_file,
                               fm_overlay=False)

    def seniors(world_club):
        return [(p.name, p.age, p.position.value, p.overall, sorted(p.attributes.items()), p.data_source)
                for p in world_club.players if p.data_source == "open"]

    for a, b in zip(world.clubs, plain.clubs, strict=True):
        if a.name != club["name"]:
            assert seniors(a) == seniors(b), a.name


def test_fm_export_takes_precedence_for_an_exported_club(tmp_path, squads_file, sample_clubs, consent):
    """
    Sahip bir kulubu FM26'dan disa aktardiysa o kulubun FM oyunculari (gercek ad + ozellik) once gelir; Wikidata
    yalnizca eksikleri ekler, kalan yer uretilir. Tavan 30 (FM once, en yuksek CA). Baska kulupteki ayni kisi
    (ayni ad + yas) Wikidata'dan dusurulur.
    """
    target = sample_clubs["tr.1"][2]                                    # dosyada Wikidata oyuncusu yok
    crowded = sample_clubs["tr.1"][1]                                   # Wikidata'da 35 oyuncu
    positions = ("GK", "D (C)", "M (C)", "ST (C)")
    rows = [(f"Fm Oyuncu {i:02d}", positions[i % 4], "TUR", str(20 + i % 12), "-", target["name"], str(100 + i),
             str(120 + i), "10", "11", "12", "13", "14", "12" if i % 4 == 0 else "-", "13" if i % 4 == 0 else "-")
            for i in range(34)]
    # "Kalabalık Oyuncu 01" (1991-03-02 -> 35 yas) FM'de hedef kulupte: Wikidata'daki kulubunden dusurulur
    rows.append(("Kalabalik Oyuncu 01", "D (C)", "TUR", "35", "-", target["name"], "90", "90", "5", "5", "5", "5",
                 "5", "-", "-"))
    world = seed.resolve_world(7, source="open", open_sample=True, real_players=True, squads_path=squads_file,
                               fm_paths=[_fm_html(tmp_path, rows)])
    squad = _club(world, target["name"])
    assert len(squad.players) == open_loader.REAL_SQUAD_MAX
    assert {p.data_source for p in squad.players} == {"fm"}
    keepers = {p.name for p in squad.players if p.position is Position.GK}     # en fazla 4 kaleci, en yuksek CA
    assert keepers == {"Fm Oyuncu 20", "Fm Oyuncu 24", "Fm Oyuncu 28", "Fm Oyuncu 32"}
    assert "Kalabalik Oyuncu 01" in {p.name for p in squad.players}
    other = _club(world, crowded["name"])
    assert "Kalabalık Oyuncu 01" not in {p.name for p in other.players}
    coverage = next(c for c in world.real_players.clubs if c.club == target["name"])
    assert (coverage.fm_only, coverage.generated, coverage.real) == (30, 0, 30)
    assert (crowded["id"], "Kalabalık Oyuncu 01") in world.real_players.fm.duplicates


def fm_parser_overall(spec) -> int:
    from ratings import ca_to_overall

    return ca_to_overall(spec.current_ability)


def test_fm_overlay_auto_discovery_skips_samples_and_can_be_disabled(tmp_path, squads_file, consent):
    world = seed.resolve_world(7, source="open", open_sample=True, real_players=True, squads_path=squads_file,
                               fm_dir=tmp_path)                                 # bos klasor: yama yok
    assert world.real_players.fm is None and any("FM yaması yok" in n for n in world.notes)
    off = seed.resolve_world(7, source="open", open_sample=True, real_players=True, squads_path=squads_file,
                             fm_overlay=False)
    assert off.real_players.fm is None and off.mask_summary.fm_players == 0


# ===========================================================================
# 5) Determinizm
# ===========================================================================

def test_same_seed_same_files_same_world(squads_file, consent):
    assert _fingerprint(_real_world(squads_file)) == _fingerprint(_real_world(squads_file))
    assert _fingerprint(_real_world(squads_file, 8)) != _fingerprint(_real_world(squads_file))


def test_real_world_does_not_depend_on_python_hash_salt(squads_file, consent):
    code = (
        "import hashlib, json, sys; sys.path.insert(0, sys.argv[1]); import open_loader;"
        "w = open_loader.build_real_world(7, open_loader.load_squads(sys.argv[2]), sample=True);"
        "rows = [[c.name, [(p.name, p.age, p.overall, sorted(p.attributes.items()), p.data_source) "
        "for p in c.players]] for c in w.clubs];"
        "print(hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest())"
    )
    digests = set()
    for salt in ("1", "987"):
        env = dict(os.environ, PYTHONHASHSEED=salt, CM_TEST_NO_DB="1", PYTHONIOENCODING="utf-8")
        env[CONSENT] = "1"
        out = subprocess.run([sys.executable, "-c", code, str(ROOT), str(squads_file)], env=env,
                             capture_output=True, text=True, timeout=180, check=True)
        digests.add(out.stdout.strip().splitlines()[-1])
    assert len(digests) == 1


# ===========================================================================
# 6) Arac: tools/build_squads.py (AG YOK)
# ===========================================================================

def test_tool_only_talks_to_wikidata_and_offline_needs_no_network(tmp_path, monkeypatch):
    tool = _load_tool()
    assert tool.ALLOWED_PREFIXES == ("https://query.wikidata.org/sparql",)
    assert set(tool.ALLOWED_HOSTS) == {"query.wikidata.org", "www.wikidata.org"}
    for url in ("https://www.transfermarkt.com/x", "http://query.wikidata.org/sparql",
                "https://sortitoutsi.net/", "https://query.wikidata.org/sparql/../evil"):
        with pytest.raises(tool.BuildError):
            tool.check_url(url)
    assert "OFM-squads" in tool.USER_AGENT and tool.SPARQL_PAUSE >= 1.0

    def no_network(*_args, **_kwargs):
        raise AssertionError("testte ag istegi yapildi")

    monkeypatch.setattr(tool, "_http", no_network)
    client = tool.Wikidata(tmp_path / "cache", offline=True)
    with pytest.raises(tool.BuildError, match="--offline"):
        client.sparql("SELECT ?x WHERE { ?x ?y ?z }")
    assert tool.main(["--offline", "--cache", str(tmp_path / "cache"), "--out", str(tmp_path / "o.json"),
                      "--clubs", "tr/galatasaray-istanbul", "--quiet"]) == 1
    assert not (tmp_path / "o.json").exists()


def test_personal_data_is_written_only_under_data_local(tmp_path):
    tool = _load_tool()
    for bad in (ROOT / "data" / "open" / "squads.json", ROOT / "squads.json", ROOT / "tests" / "x.json"):
        with pytest.raises(tool.BuildError, match="data/local"):
            tool.check_output_path(bad)
    assert tool.check_output_path(ROOT / "data" / "local" / "squads.json")
    assert tool.check_output_path(tmp_path / "squads.json")                  # depo disi serbest
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/local/" in ignore
    try:
        codes = [subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, timeout=30).returncode
                 for path in ("data/local/squads.json", "data/local/_cache_squads/sparql.json",
                              "data/local/fm_overlay_report.json")]
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git yok")
    assert codes == [0, 0, 0]


def _rows():
    """Sahte SPARQL satirlari: kulup Q1 (tr.1 kulubu), Q2 (ikinci kulup). Q55 milli, Q77 B takimi."""
    squads = [
        # guncel, taze (P585), forma 10
        {"club": "Q1", "player": "Q100", "st": "s100", "start": "2023-08-01T00:00:00Z", "dob": "1999-05-05",
         "number": "10", "pit": "2026-05-01", "matches": "30", "sl": "40"},
        # kadin futbolu sinifi
        {"club": "Q1", "player": "Q101", "st": "s101", "start": "2020-01-01", "dob": "1998-01-01",
         "cls": "Q606060", "sl": "10"},
        # yalnizca kiralik
        {"club": "Q1", "player": "Q102", "st": "s102", "start": "2025-08-01", "dob": "2003-01-01",
         "acq": "Q2914547", "sl": "12"},
        # sonra baska kulupte daha gec baslamis ACIK uyelik
        {"club": "Q1", "player": "Q103", "st": "s103", "start": "2019-01-01", "dob": "1996-01-01", "sl": "20"},
        # uzun sure ayni kulupte, 33 yas, tazelik kaniti yok, baska uyelik yok: KALIR (yas dislamaz)
        {"club": "Q1", "player": "Q104", "st": "s104", "start": "2015-01-01", "dob": "1993-01-01", "sl": "25"},
        # isaretsiz kiralik donusu (son sonraki donem yakinda bitti): KALIR
        {"club": "Q1", "player": "Q105", "st": "s105", "start": "2008-01-01", "dob": "2000-07-07", "sl": "30"},
        # sonra teknik direktor olmus
        {"club": "Q1", "player": "Q106", "st": "s106", "start": "2012-01-01", "dob": "1988-01-01", "sl": "30"},
        # iki kulubumuzde birden: Q2'deki daha yeni
        {"club": "Q1", "player": "Q107", "st": "s107a", "start": "2021-01-01", "dob": "1997-01-01", "sl": "15",
         "pit": "2026-03-01"},
        {"club": "Q2", "player": "Q107", "st": "s107b", "start": "2025-07-01", "dob": "1997-01-01", "sl": "15"},
        # 15 yas (referans 2026-07-01)
        {"club": "Q2", "player": "Q108", "st": "s108", "start": "2026-01-01", "dob": "2011-01-01", "sl": "1"},
        # baslangicsiz acik uyelik, baska tarihli acik uyelik yok: KALIR (dusuk guven)
        {"club": "Q1", "player": "Q109", "st": "s109", "dob": "2002-02-02", "sl": "9"},
        # baslangicsiz acik uyelik, ama baska tarihli acik uyelik var: dislanir
        {"club": "Q2", "player": "Q110", "st": "s110", "dob": "2001-01-01", "sl": "9"},
        # sonra B takiminda acik uyelik: B takimi yok sayilir, KALIR
        {"club": "Q1", "player": "Q111", "st": "s111", "start": "2019-01-01", "dob": "2003-03-03", "sl": "7"},
        # bitisi gelecekte (sozlesme sonu P582'ye yazilmis): acik sayilir, KALIR
        {"club": "Q2", "player": "Q112", "st": "s112", "start": "2022-07-01", "end": "2027-06-30", "dob": "1998-08-08",
         "sl": "11"},
        # eski uyelik + daha gec baslamis ve COKTAN bitmis donem + tazelik yok: bayat (left)
        {"club": "Q1", "player": "Q113", "st": "s113", "start": "2014-01-01", "dob": "1994-04-04", "sl": "18"},
        # uzun sure ayni kulupte, 34 yas, taze (P585): KALIR
        {"club": "Q1", "player": "Q114", "st": "s114", "start": "2010-01-01", "dob": "1992-01-01", "sl": "60",
         "pit": "2026-04-01", "matches": "300"},
    ]
    memberships = [
        {"player": "Q103", "team": "Q9", "start": "2024-07-01"},                       # acik, daha yeni
        {"player": "Q105", "team": "Q8", "start": "2023-01-01", "end": "2024-01-01"},  # yakinda bitti -> dondu
        {"player": "Q105", "team": "Q7", "start": "2020-01-01", "end": "2022-01-01"},
        {"player": "Q100", "team": "Q55", "start": "2025-01-01"},                      # milli takim: sayilmaz
        {"player": "Q110", "team": "Q9", "start": "2020-01-01"},                       # tarihli acik baska uyelik
        {"player": "Q111", "team": "Q77", "start": "2024-01-01"},                      # B takimi: sayilmaz
        {"player": "Q113", "team": "Q6", "start": "2018-01-01", "end": "2020-01-01"},  # coktan bitmis sonraki donem
        {"player": "Q114", "team": "Q55", "start": "2012-01-01"},
    ]
    details = [
        {"player": "Q100", "labels": "tr=Yerel Ad||en=Test Player||mul=Mul Ad", "cits": "Q43 Q183",
         "preferred": "", "sports": "Q183", "positions": "Q336286 Q268258"},
        {"player": "Q105", "labels": "de=Nur Deutsch", "cits": "Q43 Q183", "preferred": "", "sports": "",
         "positions": "Q193592 Q11681748 Q280658"},
        {"player": "Q106", "labels": "en=Now Coach", "coachFrom": "2020-07-01T00:00:00Z"},
        {"player": "Q107", "labels": "en=Two Clubs", "cits": "Q155", "positions": ""},
        {"player": "Q104", "labels": "en=Long Server"}, {"player": "Q109", "labels": "en=No Start"},
        {"player": "Q111", "labels": "en=B Team Too"}, {"player": "Q112", "labels": "en=Future End"},
        {"player": "Q114", "labels": "en=Club Legend"},
    ]
    positions = {"Q336286": "DEF", "Q268258": "DEF", "Q193592": "MID", "Q11681748": "FWD", "Q280658": "FWD"}
    countries = {"Q43": "Turkey", "Q183": "Germany", "Q155": "Brazil"}
    return squads, memberships, {"Q55": "national", "Q77": "reserve"}, details, positions, countries


def test_tool_current_club_rules_on_fake_rows():
    """Guncel kulup = en gec baslamis acik uyelik (milli / B / kadin / kiralik yok sayilir); yas kimseyi dislamaz."""
    tool = _load_tool()
    clubs = [tool.ClubRef("tr/a", "A", "tr.1", 1, "tr", "Q1"), tool.ClubRef("tr/b", "B", "tr.1", 1, "tr", "Q2")]
    squads, memberships, ignored, details, positions, countries = _rows()
    doc = tool.assemble(clubs, squads, memberships, ignored, details, positions, countries, REFERENCE, "2026-09-19")
    a = {p["qid"]: p for p in doc["clubs"]["tr/a"]["players"]}
    b = {p["qid"]: p for p in doc["clubs"]["tr/b"]["players"]}
    assert set(a) == {"Q100", "Q104", "Q105", "Q109", "Q111", "Q114"} and set(b) == {"Q107", "Q112"}
    assert doc["clubs"]["tr/a"]["excluded"] == {"left": 1, "loan": 1, "moved": 2, "retired": 1, "women": 1}
    assert doc["clubs"]["tr/b"]["excluded"] == {"age": 1, "moved": 1}
    top = a["Q100"]
    assert (top["name"], top["nationality"], top["position"], top["shirt_number"], top["confidence"]) == \
        ("Test Player", "Germany", "DEF", 10, "high")                    # en etiketi, P1532 > P27, forma P1618
    assert (a["Q105"]["name"], a["Q105"]["nationality"], a["Q105"]["position"], a["Q105"]["confidence"]) == \
        ("Nur Deutsch", "Turkey", "FWD", "low")                          # tek dil, en kucuk QID, ozgul mevki oyu
    assert (a["Q109"]["since"], a["Q109"]["confidence"]) == ("", "low")  # baslangicsiz: dusuk guven
    assert a["Q114"]["confidence"] == "high" and a["Q104"]["confidence"] == "low"
    assert b["Q112"]["since"] == "2022-07-01"
    assert doc["personal_data"] is True and doc["schema"] == open_loader.SQUADS_SCHEMA
    assert doc["coverage"]["players"] == 8 and doc["coverage"]["confidence"] == {"high": 3, "medium": 0, "low": 5}


def test_tool_classifies_ignored_teams_by_class_and_label():
    tool = _load_tool()
    assert tool.classify_team("Turkey national under-21 football team", True, False, False) == "national"
    assert tool.classify_team("Levante UD Women", False, False, False) == "women"
    assert tool.classify_team("Anything", False, True, False) == "women"
    for label in ("FC Bayern Munich II", "Real Madrid Castilla", "FC Barcelona Atlètic", "Juventus Next Gen",
                  "Galatasaray U19", "Arsenal F.C. Under-21s and Academy", "Borussia Mönchengladbach II",
                  "FC Barcelona B"):
        assert tool.classify_team(label, False, False, False) == "reserve", label
    for label in ("Boca Juniors", "Argentinos Juniors", "Atalanta BC", "Club Brugge KV", "Galatasaray S.K."):
        assert tool.classify_team(label, False, False, False) is None, label


def test_tool_output_loads_into_the_game(tmp_path, consent):
    """Aracin urettigi belge (sahte satirlardan) oyunun yukleyicisinden gecer."""
    tool = _load_tool()
    clubs = [tool.ClubRef("tr/a", "A", "tr.1", 1, "tr", "Q1"), tool.ClubRef("tr/b", "B", "tr.1", 1, "tr", "Q2")]
    doc = tool.assemble(clubs, *_rows(), REFERENCE, "2026-09-19")
    path = tmp_path / "squads.json"
    tool.write_json(path, doc)
    squads = open_loader.load_squads(path)
    assert squads.player_count == 8 and squads.reference_date == REFERENCE
    player = next(p for p in squads.clubs["tr/a"] if p.qid == "Q100")
    assert (player.name, player.nationality, player.position, player.confidence) == \
        ("Test Player", "Almanya", Position.DEF, "high")
    assert next(p for p in squads.clubs["tr/a"] if p.qid == "Q109").confidence == "low"


# ===========================================================================
# 7) Paylasilan dunya reddi (entegrasyon: test veritabani)
# ===========================================================================

def _db_available() -> bool:
    if os.getenv("CM_TEST_NO_DB"):
        return False
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
USER_PREFIX = "g16_"
REAL_SCHEMA = "career_g16_real"


def _sql(statement: str, **params):
    from sqlalchemy import text

    import database

    with database.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return result.all() if result.returns_rows else result.rowcount


def _scalar(statement: str, **params):
    rows = _sql(statement, **params)
    return rows[0][0] if rows else None


def _world_schemas() -> set[str]:
    return {r[0] for r in _sql(r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 'world\_%'")}


@pytest.fixture
def clean_db():
    import database

    expected = os.getenv("TEST_DB_NAME", "fm_db_test")
    if database.engine.url.database != expected or expected == os.getenv("DB_NAME", "fm_db"):
        raise RuntimeError(f"16G testleri yalnızca test veritabanında çalışır ({database.engine.url.database}).")

    def cleanup():
        database.init_accounts()
        owned = [r[0] for r in _sql("SELECT w.schema_name FROM accounts.worlds w JOIN accounts.users u "
                                    "ON u.id = w.owner_user_id WHERE u.username LIKE :p", p=f"{USER_PREFIX}%")]
        for schema in {REAL_SCHEMA, *owned}:
            if _scalar("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)", s=schema):
                database.drop_career_schema(schema)
        _sql("DELETE FROM accounts.worlds WHERE schema_name = ANY(:s)", s=[REAL_SCHEMA, *owned])
        _sql("DELETE FROM accounts.users WHERE username LIKE :p", p=f"{USER_PREFIX}%")

    cleanup()
    yield
    cleanup()


def _user(label: str, schema: str | None = None) -> SimpleNamespace:
    name = f"{USER_PREFIX}{label}"
    uid = _scalar("INSERT INTO accounts.users (username, password_hash, career_schema) VALUES (:u, 'scrypt$x', :s) "
                  "RETURNING id", u=name, s=schema)
    return SimpleNamespace(id=uid, name=name)


def _seed_into(schema: str, world_seed: int, **kwargs) -> None:
    import database

    with database.career_context(schema):
        database.init_db()
        seed.seed(rng_seed=world_seed, **kwargs)


@pytest.mark.integration
@needs_db
def test_real_player_career_is_off_and_cannot_become_shared(clean_db, consent, squads_file, sample_clubs):
    import accounts
    import worlds

    kwargs = {"source": "open", "open_sample": True, "real_players": True, "squads_path": squads_file,
              "fm_overlay": False}
    _seed_into(REAL_SCHEMA, 11, **kwargs)
    expected = seed.resolve_world(11, **kwargs)
    assert _scalar(f'SELECT mask_level FROM "{REAL_SCHEMA}".game_state WHERE id = 1') == "off"
    sources = dict(_sql(f'SELECT data_source, count(*) FROM "{REAL_SCHEMA}".players '
                        "WHERE NOT in_academy GROUP BY data_source"))
    counts: dict[str, int] = {}
    for club in expected.clubs:
        for p in club.players:
            counts[p.data_source] = counts.get(p.data_source, 0) + 1
    assert sources == counts and sources["open"] > 0 and sources["synthetic"] > sources["open"]
    club = sample_clubs["tr.1"][0]["name"]
    assert _scalar(f'SELECT count(*) FROM "{REAL_SCHEMA}".players p JOIN "{REAL_SCHEMA}".teams t ON t.id = p.team_id '
                   "WHERE t.name = :n AND p.name = 'Örnek Şükrü Çağlayan' AND p.age = 22 "
                   "AND p.nationality = 'Brezilya' AND p.data_source = 'open'", n=club) == 1

    owner = _user("sahip", REAL_SCHEMA)
    _sql(f'UPDATE "{REAL_SCHEMA}".game_state SET user_id = :u WHERE id = 1', u=owner.id)
    personal = worlds.ensure_personal_world(accounts.AuthSession(owner.id, owner.name, REAL_SCHEMA))
    with pytest.raises(worlds.WorldError, match="gerçek oyuncu kadroları"):
        worlds.convert_personal_to_shared(owner.id, personal.id, "Gercek Kadro", "INVITE", 4)
    assert _scalar("SELECT kind FROM accounts.worlds WHERE id = :w", w=personal.id) == "PERSONAL"
    worlds.refuse_real_players_target(REAL_SCHEMA)                           # tek koltuklu kariyer: serbest


@pytest.mark.integration
@needs_db
def test_shared_world_creation_refuses_a_real_player_build(clean_db, consent, squads_file, monkeypatch):
    """Hatali / kotu niyetli bir yol paylasilan dunyaya gercek kadro kursa bile kurulum reddedilir, iz kalmaz."""
    import accounts
    import worlds
    from world_rules import WorldRules

    def real_build(schema, world_seed, source, **_):
        _seed_into(schema, world_seed, source="open", open_sample=True, real_players=True,
                   squads_path=squads_file, fm_overlay=False)

    owner = _user("kurucu")
    monkeypatch.setattr(accounts, "_build_world", real_build)
    before = _world_schemas()
    with pytest.raises(worlds.WorldError, match="gerçek isimlerle"):
        worlds.create_world(owner.id, "Gercek Kadro Paylasim", visibility="INVITE", max_managers=4,
                            min_manager_level=1, rules=WorldRules.shared_defaults(), world_seed=5, source="open")
    assert _world_schemas() == before
    assert _scalar("SELECT count(*) FROM accounts.worlds WHERE owner_user_id = :u", u=owner.id) == 0


@pytest.mark.integration
@needs_db
def test_cli_refuses_to_write_real_players_into_a_shared_world(clean_db, consent, squads_file, monkeypatch, capsys):
    import accounts
    import worlds
    from world_rules import WorldRules

    owner = _user("paylasan")
    monkeypatch.setattr(accounts, "_build_world",
                        lambda schema, world_seed, source, **_: _seed_into(schema, world_seed, source="synthetic"))
    ctx = worlds.create_world(owner.id, "Paylasilan Hedef", visibility="INVITE", max_managers=4,
                              min_manager_level=1, rules=WorldRules.shared_defaults(), world_seed=3,
                              source="synthetic")
    teams_before = _scalar(f'SELECT count(*) FROM "{ctx.schema}".teams')
    with pytest.raises(worlds.WorldError, match="paylaşılan bir dünyaya ait"):
        worlds.refuse_real_players_target(ctx.schema)
    _sql(f'UPDATE "{ctx.schema}".game_state SET world_rules = CAST(:r AS jsonb) WHERE id = 1', r="{}")
    with pytest.raises(worlds.WorldError, match="paylaşılan bir dünyaya ait"):   # kayit tek basina yeter
        worlds.refuse_real_players_target(ctx.schema)

    code = seed.main(["--real-players", "--open-sample", "--no-fm-overlay", "--squads", str(squads_file),
                      "--career-schema", ctx.schema, "--seed", "4"])
    out = capsys.readouterr().out
    assert code == 1 and "paylaşılan bir dünyaya ait" in out and "Veritabanına dokunulmadı" in out
    assert _scalar(f'SELECT count(*) FROM "{ctx.schema}".teams') == teams_before
    assert _scalar(f'SELECT mask_level FROM "{ctx.schema}".game_state WHERE id = 1') == "light"

    monkeypatch.delenv(CONSENT)                                                # onaysiz CLI: dunya kurulmaz
    assert seed.main(["--real-players", "--open-sample", "--squads", str(squads_file),
                      "--career-schema", REAL_SCHEMA]) == 1
    assert CONSENT in capsys.readouterr().out
    assert not _scalar("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)", s=REAL_SCHEMA)
