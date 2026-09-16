"""
Asama 6 denetim bulgularinin regresyon testleri (7. Asama'da duzeltildi).

Her test, bagimsiz denetimde yeniden uretilen bir hatayi kilitler; test adinin
sonundaki numara denetim raporundaki bulgu numarasidir.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fm_parser  # noqa: E402
import seed  # noqa: E402
import transfers  # noqa: E402
from club_directory import canonical_league, lookup_club, plain_key  # noqa: E402
from finance import expected_wage  # noqa: E402
from models import Position, SquadRole  # noqa: E402
from tests.test_finance import FakeTeam, make_squad  # noqa: E402
from transfers import ContractNegotiation, ContractOffer, NegotiationStatus  # noqa: E402

# ===========================================================================
# Kulup ve lig rehberi
# ===========================================================================

def test_ligue_1_is_recognised_01():
    assert plain_key("Ligue 1") == "ligue 1"
    assert canonical_league("Ligue 1") == ("Ligue 1", "Fransa")
    assert canonical_league("Ligue 1 McDonald's") == ("Ligue 1", "Fransa")
    assert canonical_league("1. Bundesliga") == ("Bundesliga", "Almanya")


@pytest.mark.parametrize("name", [
    "Russian Premier League", "League of Ireland Premier Division", "Premier League 2",
    "LaLiga 2", "LaLiga Hypermotion", "Primera Federación", "Brasileirão Série A",
    "Austrian Bundesliga", "2. Bundesliga", "Ligue 2",
])
def test_foreign_and_second_tier_leagues_are_not_merged_05(name):
    league, country = canonical_league(name)
    assert league == name.strip() and country == "Diğer"


@pytest.mark.parametrize("name", ["Paris FC", "Barcelona SC", "City", "OM", "Bristol City"])
def test_similar_names_do_not_collide_with_directory_clubs_06(name):
    assert lookup_club(name) is None


@pytest.mark.parametrize("name,expected", [
    ("Juventus FC", "Juventus"), ("FC Barcelona", "Barcelona"), ("AS Roma", "Roma"),
    ("Beşiktaş JK", "Beşiktaş"), ("FC Bayern München", "Bayern München"), ("Paris SG", "Paris Saint-Germain"),
    ("Real Madrid CF", "Real Madrid"), ("SSC Napoli", "Napoli"),
])
def test_legitimate_suffixes_still_match_06(name, expected):
    assert lookup_club(name).name == expected


# ===========================================================================
# Parser
# ===========================================================================

def test_empty_or_symbol_header_is_not_club_02():
    report = fm_parser.parse_rows([["Name", "Age", "Club", "Position", ""], ["A B", "25", "Arsenal", "ST (C)", ""]])
    assert [p.club for p in report.players] == ["Arsenal"]
    report = fm_parser.parse_rows([["#", "Name", "Club", "Age", "Position"], ["1", "C D", "Inter", "22", "GK"]])
    assert [p.club for p in report.players] == ["Inter"]
    # "Name" + bos hucreler baslik sayilmaz
    assert fm_parser.find_header([["Name", "", ""], ["x", "y", "z"]])[0] is None


def test_trailing_comma_csv_keeps_clubs_02(tmp_path):
    path = tmp_path / "liste.csv"
    path.write_text("Name,Age,Club,Position,\nAli Veli,24,Galatasaray,ST (C),\n", encoding="utf-8")
    assert [p.club for p in fm_parser.parse_file(path, mask_names=False).players] == ["Galatasaray"]


def test_age_14_is_skipped_to_match_db_constraint_04():
    report = fm_parser.parse_rows([["Name", "Age", "Club", "Position"], ["Genç", "14", "Inter", "M (C)"]])
    assert not report.players and "yaş" in report.skipped[0][2]


def test_based_is_not_league_and_generic_id_is_not_uid_10_11(tmp_path):
    header = ["ID", "Name", "Age", "Club", "Position", "Division", "Based"]
    first = tmp_path / "a.csv"
    second = tmp_path / "b.csv"
    first.write_text(",".join(header) + "\n1,A B,25,Nice,ST (C),Ligue 1,France\n", encoding="utf-8")
    second.write_text(",".join(header) + "\n1,C D,23,Lens,GK,Ligue 1,France\n", encoding="utf-8")
    report = fm_parser.parse_files([first, second], mask_names=False)
    assert len(report.players) == 2 and report.duplicates == 0
    assert {p.league for p in report.players} == {"Ligue 1"}


@pytest.mark.parametrize("text,expected", [
    ("€10M-€20M", 15_000_000),
    ("€1,250K", 1_250_000),
    ("€1.250K", 1_250_000),
    ("€1,5M", 1_500_000),
])
def test_money_edge_cases_15(text, expected):
    assert fm_parser.parse_money(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("€1,5M /yıl", round(1_500_000 / 52)),
    ("€40.000 /ay", round(40_000 * 12 / 52)),
])
def test_turkish_wage_periods_15(text, expected):
    assert fm_parser.parse_wage(text) == expected


def test_html_without_closing_cell_tags_13():
    html = ("<table><tr><th>Name<th>Age<th>Club<th>Position"
            "<tr><td>A B<td>25<td>Inter<td>ST (C)</table>")
    rows = fm_parser._rows_from_html(html)
    report = fm_parser.parse_rows(rows)
    assert [(p.name, p.club) for p in report.players] == [("A B", "Inter")]


def test_nested_table_does_not_drop_outer_row_13():
    html = ("<table><tr><th>Name</th><th>Age</th><th>Club</th><th>Position</th></tr>"
            "<tr><td>A B</td><td>25</td><td>Inter<table><tr><td>logo</td></tr></table></td><td>ST (C)</td></tr>"
            "</table>")
    report = fm_parser.parse_rows(fm_parser._rows_from_html(html))
    assert [p.name for p in report.players] == ["A B"]


def test_txt_export_with_dash_separators_14(tmp_path):
    lines = ["| Name | Age | Club | Position |", "-" * 40, "| A B | 25 | Inter | ST (C) |",
             "-" * 40, "| C D | 30 | Milan | GK |", "-" * 40]
    path = tmp_path / "export.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    report = fm_parser.parse_file(path)
    assert report.files[0][1] == "txt" and len(report.players) == 2


def test_utf16_without_bom_14(tmp_path):
    path = tmp_path / "export.csv"
    path.write_bytes("Name,Age,Club,Position\nÇağrı,24,Beşiktaş,GK\n".encode("utf-16-le"))
    players = fm_parser.parse_file(path, mask_names=False).players
    assert [(p.name, p.club) for p in players] == [("Çağrı", "Beşiktaş")]


def test_rtf_text_export_is_supported_14(tmp_path):
    body = r"{\rtf1\ansi\ansicpg1254 {\fonttbl\f0 Courier;}\f0 | Name | Age | Club | Position |\par" \
           r"| \'c7a\'f0r\'fd | 24 | Inter | GK |\par}"
    path = tmp_path / "export.rtf"
    path.write_text(body, encoding="ascii")
    assert path in fm_parser.discover_files(tmp_path)
    players = fm_parser.parse_file(path, mask_names=False).players
    assert [(p.name, p.club) for p in players] == [("Çağrı", "Inter")]


# ===========================================================================
# Seed
# ===========================================================================

def _spec(rng, name, position, overall):
    return seed.generate_player_spec(rng, name, position, overall, (overall - 1, overall + 1))


def test_trim_keeps_real_players_for_positional_minimums_08():
    rng = random.Random(3)
    players = [_spec(rng, "GK", Position.GK, 70)]
    players += [_spec(rng, f"D{i}", Position.DEF, 60) for i in range(4)]
    players += [_spec(rng, f"M{i}", Position.MID, 85) for i in range(13)]
    players += [_spec(rng, f"F{i}", Position.FWD, 85) for i in range(12)]
    trimmed = seed.trim_squad(players)
    assert len(trimmed) == seed.FM_MAX_SQUAD
    assert sum(p.position is Position.DEF for p in trimmed) == 4          # gercek defanslar korundu
    padded, academy = seed.pad_squad(trimmed, rng, seed.NameFactory(rng), "Türkiye")
    assert academy == 1 and len(padded) == seed.FM_MAX_SQUAD + 1          # yalnizca eksik 2. kaleci


def test_world_validation_catches_duplicates_before_db_write_01():
    rng = random.Random(1)
    squad = [_spec(rng, f"K{i}", Position.GK, 70) for i in range(2)]
    club = seed.ClubSpec("Aynı", 70, 1_000_000, "4-4-2", squad)
    world = seed.WorldSpec("fm", [seed.LeagueSpec("Lig", "X", [club]), seed.LeagueSpec("Lig", "Y", [club])])
    problems = seed.validate_world(world)
    assert any("birden fazla lig" in p for p in problems)
    assert any("birden fazla kulüp" in p for p in problems)


# ===========================================================================
# Sozlesme masasi
# ===========================================================================

def _negotiation(overall=80, buyer_rep=88, manager=10.0):
    buyer, seller = FakeTeam(1, "Alici", buyer_rep), FakeTeam(2, "Satici", 78)
    make_squad(buyer, [84, 82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68])
    make_squad(seller, [overall] + [70] * 14)
    target = seller.players[0]
    target.current_wage = expected_wage(overall, 78)
    return ContractNegotiation(random.Random(1), target, buyer, 1, manager_reputation=manager)


def _needs_role_room(n):
    if n.demand.role is SquadRole.BACKUP:
        pytest.skip("oyuncu zaten yedek rolü istiyor")


def test_returning_to_original_role_after_spike_is_not_an_insult_07():
    n = _negotiation()
    _needs_role_room(n)
    base = n.demand
    lower = ContractNegotiation._min_role(base.role)
    assert n.respond(ContractOffer(base.wage, base.years, lower)).status is NegotiationStatus.OPEN
    response = n.respond(ContractOffer(base.wage, base.years, base.role))
    assert response.status is NegotiationStatus.ACCEPTED, response.message


def test_spike_triggering_offer_is_evaluated_immediately_07():
    n = _negotiation()
    _needs_role_room(n)
    base = n.demand
    lower = ContractNegotiation._min_role(base.role)
    generous = ContractOffer(base.wage * 3, base.years, lower)
    response = n.respond(generous)
    assert response.status is NegotiationStatus.ACCEPTED
    assert "fırladı" in response.message


def test_refusal_message_uses_weighted_gaps_12():
    check = transfers.check_interest(90, team_reputation=71, manager_reputation=10)
    assert not check.interested and check.reason == transfers.CLUB_GOALS_MESSAGE


def test_ai_offer_is_always_signable_once_interested_17():
    for seed_value in range(200):
        n = _negotiation(overall=82, buyer_rep=84)
        if not n.open:
            continue
        offer = transfers.ai_contract_offer(random.Random(seed_value), n, free_weekly=10_000_000)
        assert n.persuasion(offer) >= n.required_persuasion


# ===========================================================================
# Arayuz ve altyapi
# ===========================================================================

def test_parse_seed_rejects_tricky_input_16():
    pytest.importorskip("streamlit")
    import web_app

    assert web_app.parse_seed(" 42 ") == 42
    assert web_app.parse_seed("-7") == -7
    for bad in ("--5", "²", "", None, "abc"):
        assert web_app.parse_seed(bad) is None


def test_test_url_strips_dbname_query_03():
    from tests.db_urls import build_test_url

    url, main_db = build_test_url("postgresql+psycopg2://u:p@localhost:5433/?dbname=fm_db", "fm_db_test")
    assert "dbname" not in url and url.endswith("/fm_db_test") and main_db == "fm_db"
    url, main_db = build_test_url("postgresql+psycopg2://u:p@localhost:5433/fm_db?database=fm_db&sslmode=disable",
                                  "fm_db_test")
    assert "database=" not in url and "sslmode=disable" in url and main_db == "fm_db"


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_find_team_is_accent_and_case_insensitive_09():
    from career_manager import CareerManager
    from database import SessionLocal

    with SessionLocal() as db:
        cm = CareerManager(db)
        team = cm.find_team("Istanbul Lions")
        team.name = "İstanbulspor"
        db.flush()
        assert cm.find_team("İstanbulspor") is team
        assert cm.find_team("istanbulspor") is team
        assert cm.find_team("ISTANBULSPOR ") is team
        assert cm.find_team("KADIKOY canaries").name == "Kadıköy Canaries"
        assert cm.find_team("Yok Böyle Takım") is None
        db.rollback()


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_schema_check_reports_missing_columns_18():
    from sqlalchemy import text

    import database

    assert database.schema_problems() == []
    with database.engine.begin() as conn:
        conn.execute(text("ALTER TABLE players RENAME COLUMN condition TO condition_old"))
    try:
        assert "eksik sütun: players.condition" in database.schema_problems()
    finally:
        with database.engine.begin() as conn:
            conn.execute(text("ALTER TABLE players RENAME COLUMN condition_old TO condition"))
    assert database.schema_problems() == []
