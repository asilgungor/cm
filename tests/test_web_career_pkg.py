"""
Kariyer paketi ekranlari uctan uca testleri -- Streamlit AppTest.

Oyuncu memnuniyeti ve maas talebi (Kadro & Taktik), takip listesi ve transfer yasagi (Transfer Pazari),
hazirlik maci (Taktik Merkezi) ve Haberler & Tarih sekmesi gercek web_app.py uzerinden tiklanir; sonuc
veritabaninda dogrulanir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import menu  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _html,
    _reseed,
    _set_user_team,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed()


def _with_db(fn):
    from database import session_scope

    with session_scope() as db:
        return fn(db)


def _cm(db):
    from career_manager import CareerManager
    return CareerManager(db)


def test_unhappy_player_and_wage_demand_can_be_accepted_from_the_squad_tab():
    from models import Player

    team_id = _set_user_team(TEAM, wage_budget=50_000_000)

    def make_unhappy(db):
        cm = _cm(db)
        player = min(cm.find_team(TEAM).players, key=lambda p: p.overall_rating)
        player.concern_level = 2
        player.wage_demand = player.current_wage + 5_000
        return player.id, player.name, player.wage_demand

    pid, name, demand = _with_db(make_unhappy)
    at = _app(seed="4", page="kadro")
    assert any(name in df.value["Oyuncu"].tolist() for df in at.dataframe if "Maaş talebi" in df.value.columns)
    assert at.button(key=f"wage_accept_{pid}") and at.button(key=f"wage_refuse_{pid}")

    _click(at, f"wage_accept_{pid}")
    assert any("yeni sözleşmeyi imzaladı" in s.value for s in at.success)
    player = _with_db(lambda db: (lambda p: (p.current_wage, p.wage_demand, p.team_id))(db.get(Player, pid)))
    assert player == (demand, None, team_id)
    assert not [b for b in at.button if b.key == f"wage_accept_{pid}"]


def test_refusing_a_wage_demand_lowers_morale():
    from models import Player

    _set_user_team(TEAM)

    def make_demand(db):
        player = max(_cm(db).find_team(TEAM).players, key=lambda p: p.overall_rating)
        player.wage_demand = player.current_wage * 2
        return player.id, player.morale

    pid, morale = _with_db(make_demand)
    at = _app(seed="4", page="kadro")
    _click(at, f"wage_refuse_{pid}")
    assert any("reddedildi" in w.value for w in at.warning)
    after = _with_db(lambda db: (db.get(Player, pid).morale, db.get(Player, pid).wage_demand))
    assert after[0] < morale and after[1] is None


def test_shortlist_add_and_remove_and_transfer_ban_blocks_offers():
    from models import Player

    _set_user_team(TEAM)
    at = _app(seed="4", page="transfer")                                  # Faz 13I: Transfer Merkezi › Oyuncu ara
    target = at.selectbox(key="mkt_target").value

    _click(at, "mkt_shortlist")
    assert _with_db(lambda db: _cm(db).is_shortlisted(target))
    assert any("takip listesine eklendi" in s.value for s in at.success)
    listed = next(df.value for df in at.dataframe if "Eklendi" in df.value.columns)
    assert len(listed) == 1 and "İstenen bonservis" not in listed.columns    # K12: kulubun fiyati sisli
    assert "Değer (tahmin)" in listed.columns

    _click(at, "sl_remove")
    assert not _with_db(lambda db: _cm(db).is_shortlisted(target))

    def ban(db):
        cm = _cm(db)
        db.get(Player, target).transfer_locked_until = cm.career_week + 3
    _with_db(ban)
    at.run()
    at.selectbox(key="mkt_target").set_value(target)
    at.run()
    assert at.button(key="mkt_offer").disabled
    assert any("hafta daha satılamaz" in w.value for w in at.warning)


def test_friendly_is_played_once_per_week_without_touching_the_table():
    import web_app

    team_id = _set_user_team(TEAM)
    at = _app(seed="4", page="taktik")
    at.radio(key="prep_section").set_value(web_app.PREP_FRIENDLY)
    at.run()
    points_before = _with_db(lambda db: _cm(db).find_team(TEAM).points)

    _click(at, "fr_play")
    assert any("Hazırlık maçı" in s.value for s in at.success)
    assert at.button(key="fr_play").disabled                              # haftada bir
    friendlies = _with_db(lambda db: [(f.home_team_id, f.week) for f in _cm(db).friendlies()])
    assert friendlies == [(team_id, 1)]
    assert _with_db(lambda db: _cm(db).find_team(TEAM).points) == points_before


def test_news_honours_and_transfer_records_tab():
    import web_app
    from models import NewsItem, SeasonHonour, TransferLog

    team_id = _set_user_team(TEAM)

    def seed_history(db):
        cm = _cm(db)
        team = cm.find_team(TEAM)
        db.add(NewsItem(season=1, week=1, kind="BIG_RESULT", text="Istanbul Lions 5-0 kazandı", team_id=team.id))
        db.add(TransferLog(season=1, week=1, player_name="Rekor Oyuncu", from_team_name="Madrid Blancos",
                           to_team_id=team.id, to_team_name=team.name, fee=90_000_000, wage=400_000))
        db.add(SeasonHonour(season=1, kind="LEAGUE", league_id=team.league_id, competition_name="Süper Lig",
                            champion_team_id=team.id, champion_name=team.name, top_scorer_name="Golcü",
                            top_scorer_goals=12, user_team_position=1))
    _with_db(seed_history)

    at = _app(seed="4", page="haberler")
    assert "haberler" in menu(at)
    assert "Istanbul Lions 5-0 kazandı" in _html(at)

    at.radio(key="world_section").set_value(web_app.WORLD_HONOURS)
    at.run()
    assert not at.exception
    honours = next(df.value for df in at.dataframe if "Şampiyon" in df.value.columns)
    assert honours.iloc[0]["Şampiyon"] == TEAM and honours.iloc[0]["Gol kralı"] == "Golcü (12)"
    assert "Kulübün kupaları" in _html(at)

    at.radio(key="world_section").set_value(web_app.WORLD_TRANSFERS)
    at.run()
    records = next(df.value for df in at.dataframe if "Nereden" in df.value.columns)
    assert records.iloc[0]["Oyuncu"] == "Rekor Oyuncu" and team_id
