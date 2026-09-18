"""
Acik veri dunyasi testleri (13F. Asama): data/open/ vendor dosyalari (openfootball, CC0),
open_loader.py ve seed.py --source open.

    * vendor dosyalari ayristirilir ve lisans/kaynak/tarih bilgisi tasir
    * --source open: kulup ve lig adlari GERCEK ve maskesiz; sizinti kilidi yine de tetiklenmez
    * koken (data_source) kilidi: acik veri disindaki her ad icin find_leaks bugunku kadar kati
    * determinizm (ayni tohum -> ayni dunya; Python hash tuzundan bagimsiz)
    * itibarlar makul bantta ve club_directory'deki elle verilmis itibarlarla uyumlu
    * kadro buyuklugu, kaleci sayisi, yas araligi mevcut dogrulamalardan gecer
    * sentetik varsayilan degismez

DB'siz testler CM_TEST_NO_DB=1 ile de calisir; en sondaki entegrasyon testleri test
veritabaninda acik veri dunyasini transaction icinde yazar ve ROLLBACK eder.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import random
import statistics
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import open_loader  # noqa: E402
import seed  # noqa: E402
from club_directory import known_clubs, lookup_club  # noqa: E402
from models import Position  # noqa: E402
from name_masking import find_leaks  # noqa: E402

OPEN_DIR = ROOT / "data" / "open"
LEAGUE_NAMES = {"Süper Lig", "Premier Lig", "La Liga", "Bundesliga", "Serie A", "Ligue 1"}
COUNTRIES = {"Türkiye", "İngiltere", "İspanya", "Almanya", "İtalya", "Fransa"}
OPENFOOTBALL_RAW = "https://raw.githubusercontent.com/openfootball/"


def _load_tool():
    """tools/ paket degil: gelistirici aracini dosya yolundan yukle (ag baglantisi KURULMAZ)."""
    path = ROOT / "tools" / "build_open_data.py"
    spec = importlib.util.spec_from_file_location("build_open_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data():
    return open_loader.load_open_data()


@pytest.fixture(scope="module")
def world():
    return seed.resolve_world(2026, source="open")


def _fingerprint(world) -> str:
    rows = [
        (lg.name, lg.country, lg.data_source, c.name, c.reputation, c.transfer_budget, c.formation,
         c.youth_facilities, c.data_source,
         [(p.name, p.age, p.position.value, p.overall, sorted(p.attributes.items()), p.potential,
           p.market_value, p.form, p.morale, p.contract_years, p.data_source)
          for p in (*c.players, *c.academy)])
        for lg in world.leagues for c in lg.clubs
    ]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _directory_club(data, club: dict):
    """Acik veri kulubu -> club_directory kaydi (ad ya da openfootball takma adlari uzerinden)."""
    hit = lookup_club(club["name"])
    if hit is not None:
        return hit
    record = data.club_index.get(club["id"])
    for alias in (record["name"], *record["aliases"]) if record else ():
        hit = lookup_club(alias)
        if hit is not None:
            return hit
    return None


# ===========================================================================
# 1) Vendor dosyalari
# ===========================================================================

@pytest.mark.parametrize("filename", ["clubs.json", "leagues.json"])
def test_vendored_files_carry_licence_and_provenance(filename):
    path = OPEN_DIR / filename
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["license"] == "CC0-1.0"
    assert doc["license_url"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert doc["source"].startswith("openfootball/") and doc["source_url"].startswith(
        "https://github.com/openfootball/")
    assert doc["generator"] == "tools/build_open_data.py"
    assert date.fromisoformat(doc["fetched_at"]) <= date.today()
    assert doc["source_files"] and all(url.startswith(OPENFOOTBALL_RAW) for url in doc["source_files"])
    assert all(url.endswith((".txt", ".json")) for url in doc["source_files"])
    assert "CC0" in doc["notice"]
    assert path.stat().st_size < 300_000


def test_vendored_data_has_no_player_or_image_data(data):
    # Yalnizca veri govdesi (notice metni "Transfermarkt kullanilmadi" der, o haric)
    lowered = json.dumps([data.clubs["clubs"], data.leagues["leagues"]], ensure_ascii=False).casefold()
    for forbidden in ("transfermarkt", "sofifa", "sortitoutsi", "current_ability", "potential_ability",
                      ".png", ".svg", ".jpg", "crest", "badge", "\"players\""):
        assert forbidden not in lowered, forbidden


def test_vendored_leagues_cover_the_six_top_divisions(data):
    leagues = data.leagues["leagues"]
    assert [lg["code"] for lg in leagues] == ["tr.1", "en.1", "es.1", "de.1", "it.1", "fr.1"]
    assert {lg["name"] for lg in leagues} == LEAGUE_NAMES                  # Turkce gorunen adlar
    assert {lg["country"] for lg in leagues} == COUNTRIES
    index = data.club_index
    for league in leagues:
        assert 18 <= len(league["clubs"]) <= 20 and league["tier"] == 1
        assert all(club["id"] in index for club in league["clubs"])
        assert len({c["id"] for c in league["clubs"]}) == len(league["clubs"])
        assert len(league["seasons"]) >= 4                                  # itibar icin gecmis
        for season in league["seasons"]:
            assert season["source"].startswith(OPENFOOTBALL_RAW)
            for row in season["table"]:
                assert row["pts"] == 3 * row["w"] + row["d"] and row["pld"] == row["w"] + row["d"] + row["l"]
    clubs = data.clubs["clubs"]
    galatasaray = index["tr/galatasaray-istanbul"]
    assert galatasaray["city"] == "İstanbul" and "Galatasaray" in galatasaray["aliases"]
    assert all(set(c) == {"id", "name", "country", "founded", "city", "stadium", "aliases"} for c in clubs)


def test_missing_or_unlicensed_vendor_files_are_rejected(tmp_path):
    with pytest.raises(open_loader.OpenDataError, match="build_open_data"):
        open_loader.load_open_data(tmp_path)
    for filename in ("clubs.json", "leagues.json"):
        doc = json.loads((OPEN_DIR / filename).read_text(encoding="utf-8"))
        doc.pop("license")
        (tmp_path / filename).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(open_loader.OpenDataError, match="lisans"):
        open_loader.load_open_data(tmp_path)


# ===========================================================================
# 2) Gelistirici araci (ag YOK: yalnizca ayristiricilar ve adres kilidi)
# ===========================================================================

def test_tool_parses_openfootball_club_format():
    tool = _load_tool()
    text = (
        "= Turkey\n\n"
        "Galatasaray İstanbul, İstanbul   ## yorum\n"
        "  | Galatasaray | Galatasaray SK\n"
        "  | $$Sponsor$$ Galatasaray AŞ\n"
        "Arsenal FC, 1886, @ Emirates Stadium, London (Highbury)\n"
        "  | Arsenal | Bayern Munich [en]\n"
        "ii) Arsenal FC II\n"
    )
    clubs = tool.parse_clubs_txt(text, "tr")
    assert [c["name"] for c in clubs] == ["Galatasaray İstanbul", "Arsenal FC", "Arsenal FC II"]
    assert clubs[0]["id"] == "tr/galatasaray-istanbul" and clubs[0]["city"] == "İstanbul"
    assert clubs[0]["aliases"] == ["Galatasaray", "Galatasaray SK", "Galatasaray AŞ"]
    assert (clubs[1]["founded"], clubs[1]["stadium"]) == (1886, "Emirates Stadium")
    assert clubs[1]["aliases"] == ["Arsenal", "Bayern Munich"]


def test_tool_builds_tables_from_both_fixture_formats():
    tool = _load_tool()
    _, json_rows = tool.parse_matches_json({"name": "X", "matches": [
        {"team1": "A", "team2": "B", "score": {"ft": [2, 1]}},
        {"team1": "B", "team2": "C", "score": {"ft": [0, 0]}},
        {"team1": "C", "team2": "A"},                                        # oynanmamis
    ]})
    name, txt_rows = tool.parse_matches_txt(
        "= Turkish Süper Lig 2023/24\n\n▪ Matchday 1\n  Fri Aug 11 2023\n"
        "    21:00  A              v B              2-1 (1-0)\n"
        "           B              v C              0-0\n"
    )
    assert name == "Turkish Süper Lig 2023/24"
    for rows in (json_rows, txt_rows):
        table = tool.build_table(rows)
        assert table["A"]["pts"] == 3 and table["B"]["pts"] == 1 and table["C"]["pts"] == 1
        assert table["A"]["pld"] == 1 and table["B"]["pld"] == 2


def test_tool_only_fetches_plain_text_from_openfootball(tmp_path):
    tool = _load_tool()
    fetcher = tool.Fetcher(tmp_path, offline=True, quiet=True)
    for url in ("https://example.com/clubs.txt",
                "https://raw.githubusercontent.com/someone-else/clubs/master/x.txt",
                "https://raw.githubusercontent.com/openfootball/clubs/master/run.py"):
        with pytest.raises(tool.BuildError):
            fetcher.get(url)
    assert fetcher.get(f"{tool.CLUBS_BASE}/europe/turkey/tr.clubs.txt") is None   # offline, onbellek bos


# ===========================================================================
# 3) --source open: gercek adlar, sizinti kilidi tetiklenmez
# ===========================================================================

def test_open_world_has_real_unmasked_club_and_league_names(world):
    assert world.source == "open"
    assert {lg.name for lg in world.leagues} == LEAGUE_NAMES
    assert {lg.country for lg in world.leagues} == COUNTRIES
    names = {c.name for c in world.clubs}
    assert {"Galatasaray", "Fenerbahçe", "Beşiktaş", "Real Madrid CF", "FC Barcelona",
            "Manchester City FC", "FC Bayern München", "Juventus FC", "Paris Saint-Germain FC"} <= names
    assert lookup_club("Real Madrid CF").name == "Real Madrid"
    assert len(world.clubs) == 114 and len(names) == 114
    # Adlar GERCEK: sizinti denetimine verilseydi yakalanirdi ...
    assert "Galatasaray" in find_leaks(names) and "Süper Lig" in find_leaks({lg.name for lg in world.leagues})
    # ... ama koken acik veri oldugu icin kilide hic girmezler.
    assert seed.validate_world(world) == []
    assert all(lg.data_source == "open" for lg in world.leagues)
    assert all(c.data_source == "open" for c in world.clubs)
    summary = world.mask_summary
    assert (summary.renamed_clubs, summary.renamed_leagues, summary.open_clubs, summary.open_leagues) \
        == (0, 0, 114, 6)
    assert "açık veriden (CC0)" in summary.text()
    assert any("CC0-1.0" in note for note in world.notes)


def test_open_world_players_are_generated_not_real(world):
    players = [p for c in world.clubs for p in c.players]
    assert {p.data_source for p in players} == {"open"}
    assert all(p.fm_uid is None and p.current_ability is None and not p.fm_attributes for p in players)
    academy = [p for c in world.clubs for p in c.academy]
    assert academy and {p.data_source for p in academy} == {"academy"}
    everyone = [p.name for p in (*players, *academy)]
    assert len(everyone) == len(set(everyone))                             # 2900+ oyuncuda tekrar yok
    assert not any(" Jr." in n for n in everyone)


def test_open_world_survives_every_mask_level_unchanged():
    reference = _fingerprint(seed.resolve_world(7, source="open"))
    assert _fingerprint(seed.resolve_world(7, source="open", mask_level="strong")) == reference


# ===========================================================================
# 4) Koken kilidi (Asama 0): acik veri DISINDAKI adlar icin bugunku kadar kati
# ===========================================================================

def _squad(rng_seed: int):
    rng = random.Random(rng_seed)
    return [seed.generate_player_spec(rng, f"K{i}", Position.GK, 70, (69, 71)) for i in range(2)]


def test_provenance_exempts_only_open_entries():
    open_club = seed.ClubSpec("Galatasaray", 80, 1, "4-4-2", _squad(1), data_source="open")
    fake_open = seed.ClubSpec("Juventus", 80, 1, "4-4-2", _squad(2))            # koken yok: sentetik
    world = seed.WorldSpec("synthetic", [
        seed.LeagueSpec("Süper Lig", "Türkiye", [open_club], data_source="open"),
        seed.LeagueSpec("Serie A", "İtalya", [fake_open]),
    ])
    problems = seed.validate_world(world)
    assert "Maskelenmemiş gerçek isim: Serie A" in problems
    assert "Maskelenmemiş gerçek isim: Juventus" in problems
    assert not any("Galatasaray" in p or "Süper Lig" in p for p in problems)
    with pytest.raises(seed.SeedError, match="Juventus"):
        seed.write_world(object(), world, rng_seed=1)                      # DB'ye hic dokunmadan

    summary = seed.mask_world(world, "light")
    assert (open_club.name, world.leagues[0].name) == ("Galatasaray", "Süper Lig")   # dokunulmadi
    assert fake_open.name == "Torino Bianconeri" and world.leagues[1].name == "İtalya Elit Ligi"
    assert (summary.clubs, summary.leagues, summary.open_clubs, summary.open_leagues) == (1, 1, 1, 1)
    assert seed.validate_world(world) == []


def test_default_provenance_is_synthetic_and_keeps_the_guard():
    assert seed.ClubSpec("X", 1, 1, "4-4-2", []).data_source == "synthetic"
    assert seed.LeagueSpec("X", "Y", []).data_source == "synthetic"
    world = seed.WorldSpec("open", [seed.LeagueSpec("Premier League", "İngiltere", [
        seed.ClubSpec("Arsenal", 80, 1, "4-4-2", _squad(3))])])            # kaynak "open" ama koken yok
    assert len([p for p in seed.validate_world(world) if "Maskelenmemiş" in p]) == 2


# ===========================================================================
# 5) Determinizm
# ===========================================================================

def test_same_seed_builds_identical_world_twice(world):
    first = seed.resolve_world(2026, source="open")
    assert _fingerprint(first) == _fingerprint(world)
    other = seed.resolve_world(2027, source="open")
    assert [c.name for c in other.clubs] == [c.name for c in world.clubs]
    assert [c.reputation for c in other.clubs] == [c.reputation for c in world.clubs]   # itibar veriden
    assert _fingerprint(other) != _fingerprint(world)                      # kadrolar tohumdan


def test_world_does_not_depend_on_python_hash_salt(world):
    """Ayni tohum, FARKLI PYTHONHASHSEED ile ayri sureclerde birebir ayni dunya."""
    code = (
        f"import sys; sys.path.insert(0, r'{ROOT}'); sys.path.insert(0, r'{ROOT / 'tests'}');"
        "import seed, test_open_world as t;"
        "print(t._fingerprint(seed.resolve_world(2026, source='open')))"
    )
    digests = set()
    for salt in ("1", "987654"):
        env = {**os.environ, "PYTHONHASHSEED": salt, "CM_TEST_NO_DB": "1"}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                             cwd=ROOT, timeout=120, check=True)
        digests.add(out.stdout.strip().splitlines()[-1])
    assert digests == {_fingerprint(world)}


def test_club_seed_is_sha256_of_stable_id():
    expected = int.from_bytes(hashlib.sha256(b"ofm-open|2026|tr/galatasaray-istanbul").digest()[:8], "big")
    assert open_loader.club_seed(2026, "tr/galatasaray-istanbul") == expected
    assert open_loader.club_seed(2026, "tr/galatasaray-istanbul") != open_loader.club_seed(
        2026, "tr/fenerbahce-istanbul")


# ===========================================================================
# 6) Itibar ve guc bandi
# ===========================================================================

def test_reputations_land_in_sane_bands(world, data):
    for league in world.leagues:
        reps = sorted((c.reputation for c in league.clubs), reverse=True)
        assert all(open_loader.REPUTATION_MIN <= r <= open_loader.REPUTATION_MAX for r in reps)
        assert reps[0] >= 80 and reps[-1] <= 72 and reps[0] - reps[-1] >= 12, (league.name, reps)
    top = {lg.name: max(lg.clubs, key=lambda c: (c.reputation, c.name)).name for lg in world.leagues}
    assert top["Premier Lig"] == "Manchester City FC"
    assert top["Bundesliga"] == "FC Bayern München"
    assert top["Ligue 1"] == "Paris Saint-Germain FC"
    assert top["Serie A"] == "FC Internazionale Milano"
    by_name = {c.name: c.reputation for c in world.clubs}
    assert by_name["Galatasaray"] > by_name["Fenerbahçe"] > by_name["Beşiktaş"]
    assert min(by_name["Real Madrid CF"], by_name["FC Barcelona"]) >= 90
    assert by_name["Manchester City FC"] >= seed.ELITE_REPUTATION > by_name["Galatasaray"]
    # Lig tabanlari: Premier Lig ortalamasi Super Lig'in belirgin ustunde
    mean = {lg.name: statistics.fmean(c.reputation for c in lg.clubs) for lg in world.leagues}
    assert mean["Premier Lig"] > max(v for k, v in mean.items() if k != "Premier Lig")
    assert mean["Süper Lig"] < min(v for k, v in mean.items() if k != "Süper Lig")


def test_reputations_match_the_hand_set_directory(data):
    diffs = {}
    for league in data.leagues["leagues"]:
        reps = open_loader.league_reputations(league)
        for club in league["clubs"]:
            info = _directory_club(data, club)
            if info is not None and info.country == league["country"]:
                diffs[info.name] = reps[club["id"]] - info.reputation
    assert set(diffs) == {c.name for c in known_clubs()}                    # 38 kulubun hepsi eslesti
    assert max(abs(d) for d in diffs.values()) <= 5, diffs
    assert statistics.fmean(abs(d) for d in diffs.values()) <= 2.5, diffs


def test_newcomer_without_history_sits_near_the_bottom(data):
    league = next(lg for lg in data.leagues["leagues"] if lg["code"] == "en.1")
    newcomer = {**league, "clubs": [*league["clubs"], {"id": "en/test-town", "name": "Test Town"}]}
    reps = open_loader.league_reputations(newcomer)
    assert reps["en/test-town"] == open_loader.reputation_from_form(84.0, open_loader.NEWCOMER_Z)
    assert reps["en/test-town"] <= sorted(reps.values())[3]


def test_strength_band_matches_the_synthetic_league_data():
    """merkez = 0.484*R + 39.97, band ±5: sentetik LEAGUE_DATA bantlarinin regresyonu."""
    for row in seed.LEAGUE_DATA:
        for _name, reputation, _budget, band in row["teams"]:
            low, high = open_loader.strength_band(reputation)
            assert high - low == 10
            assert abs((low + high) / 2 - (band[0] + band[1]) / 2) <= 1.5, (reputation, band, low, high)


# ===========================================================================
# 7) Kadrolar
# ===========================================================================

def test_open_squads_pass_existing_validations(world):
    for club in world.clubs:
        counts = {pos: sum(p.position is pos for p in club.players) for pos in Position}
        assert seed.FM_MIN_SQUAD <= len(club.players) <= seed.FM_MAX_SQUAD
        assert 20 <= len(club.players) <= 22 and counts[Position.GK] == 3
        assert all(counts[pos] >= n for pos, n in seed.FM_MIN_PER_POSITION.items())
        assert club.academy_added == 0                                     # pad_squad eklemeye gerek duymadi
        low, high = open_loader.strength_band(club.reputation)
        assert all(low <= p.overall <= high for p in club.players), club.name
        assert all(17 <= p.age <= 37 for p in club.players)
        assert all(p.potential is not None and p.potential >= p.overall for p in club.players)
        assert club.transfer_budget > 0 and club.youth_facilities and 4 <= len(club.academy) <= 6
    assert seed.validate_world(world) == []


def test_open_sample_is_a_small_real_subset(world):
    sample = seed.resolve_world(2026, source="open", open_sample=True)
    assert len(sample.leagues) == 6 and len(sample.clubs) == 6 * open_loader.SAMPLE_CLUBS_PER_LEAGUE
    full = {c.name: c.reputation for c in world.clubs}
    assert all(full[c.name] == c.reputation for c in sample.clubs)         # itibarlar tam dunyayla ayni
    assert {"Galatasaray", "Real Madrid CF", "Manchester City FC"} <= {c.name for c in sample.clubs}
    for league in sample.leagues:                                          # her ligin EN itibarlilari
        full_league = next(lg for lg in world.leagues if lg.name == league.name)
        expected = sorted((c.reputation for c in full_league.clubs), reverse=True)
        assert sorted((c.reputation for c in league.clubs), reverse=True) == expected[:len(league.clubs)]
    assert seed.validate_world(sample) == []


# ===========================================================================
# 8) Varsayilan degismedi + CLI
# ===========================================================================

def test_synthetic_default_is_unchanged():
    assert seed.build_arg_parser().parse_args([]).source == "auto"
    synthetic = seed.resolve_world(2026, source="synthetic")
    assert synthetic.source == "synthetic" and (len(synthetic.clubs), synthetic.player_count) == (24, 360)
    assert {lg.data_source for lg in synthetic.leagues} == {c.data_source for c in synthetic.clubs} == {"synthetic"}
    assert find_leaks([lg.name for lg in synthetic.leagues] + [c.name for c in synthetic.clubs]) == []
    assert synthetic.mask_summary.open_clubs == 0 and "açık veri" not in synthetic.mask_summary.text()


def test_cli_accepts_open_source_and_sample_flag():
    args = seed.build_arg_parser().parse_args(["--source", "open", "--seed", "5"])
    assert (args.source, args.open_sample) == ("open", False)
    assert seed.build_arg_parser().parse_args(["--open-sample"]).open_sample is True


# ===========================================================================
# 9) Entegrasyon: veritabanina yaz, dogrula, ROLLBACK
# ===========================================================================

def _db_available() -> bool:
    if os.getenv("CM_TEST_NO_DB"):
        return False
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


@pytest.fixture
def open_db():
    from sqlalchemy import delete

    from database import SessionLocal
    from models import Fixture, GameState, League, Player, PlayerMatchStat, Staff, Team

    session = SessionLocal()
    try:
        for model in (PlayerMatchStat, Fixture, GameState, Staff, Player, Team, League):
            session.execute(delete(model))
        session.flush()
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_open_world_is_persisted_with_real_names(open_db, world):
    from sqlalchemy import func, select

    from match_engine import MatchEngine, build_match_team
    from models import Fixture, GameState, League, Player, Team

    seed.write_world(open_db, world, rng_seed=2026)
    open_db.flush()
    open_db.expire_all()
    assert set(open_db.scalars(select(League.name))) == LEAGUE_NAMES
    assert open_db.scalar(select(func.count()).select_from(Team)) == 114
    assert open_db.scalar(select(func.count()).select_from(Player).where(Player.data_source == "open")) \
        == world.player_count
    assert open_db.scalar(select(func.count()).select_from(Fixture)) == sum(
        len(lg.clubs) * (len(lg.clubs) - 1) for lg in world.leagues)
    gala = open_db.scalar(select(Team).where(Team.name == "Galatasaray"))
    assert gala.league.name == "Süper Lig" and gala.league.country == "Türkiye"
    assert gala.reputation == next(c.reputation for c in world.clubs if c.name == "Galatasaray")
    assert open_db.get(GameState, 1).mask_level == "light"                 # FM kaynakli gercek ad yok
    for team in open_db.scalars(select(Team)):
        assert len(team.players) >= seed.FM_MIN_SQUAD and team.free_wage >= 0 and team.staff

    fener = open_db.scalar(select(Team).where(Team.name == "Fenerbahçe"))
    result = MatchEngine(build_match_team(gala, True, 1), build_match_team(fener, False, 1), seed=9).simulate()
    assert result.events[-1].type.value == "FULL_TIME"


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_open_world_career_week_runs(open_db):
    """Kucuk ornek dunyada (6 lig x 6 kulup) menajer Galatasaray'i secer ve bir hafta oynanir."""
    from career_manager import CareerManager

    sample = seed.resolve_world(2026, source="open", open_sample=True)
    seed.write_world(open_db, sample, rng_seed=2026)
    open_db.flush()
    open_db.expire_all()
    cm = CareerManager(open_db, seed=4)
    team = cm.find_team("Galatasaray")
    assert team is not None and team.name == "Galatasaray"
    cm.set_user_team(team)
    report = cm.play_week()
    assert len(report.results) == 6 * 3                                     # 6 lig x 3 mac
    assert report.user_result is not None
