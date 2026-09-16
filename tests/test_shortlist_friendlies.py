"""
Izleme listesi (shortlist) ve kariyer hazirlik maclari (friendlies) testleri (12. Asama, Soccer Manager).

Gercek PostgreSQL'e karsi calisir; her test kendi transaction'ini rollback eder (onerilen: TEST_DB_NAME=fm_db_test_smpkg).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import concerns as cn  # noqa: E402
import transfers  # noqa: E402
from career_manager import CareerManager, FriendlyError, ShortlistError  # noqa: E402
from match_engine import EventType  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    FixtureStatus,
    Friendly,
    GameMode,
    LineupStatus,
    Player,
    PlayerMatchStat,
    Position,
    ShortlistEntry,
    Team,
    TournamentStatus,
)
from transfers import ContractOffer  # noqa: E402


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, seed=23) -> CareerManager:
    cm = CareerManager(db, seed=seed)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.run_ai_transfer_window = lambda: []
    return cm


# ===========================================================================
# 1) IZLEME LISTESI
# ===========================================================================

def test_shortlist_add_update_remove_and_rows(db):
    cm = _manager(db)
    with pytest.raises(ShortlistError, match="önce yöneteceğin takımı seç"):
        cm.shortlist_add(cm.find_team("Madrid Blancos").players[0])
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    seller = cm.find_team("Madrid Blancos")
    star = seller.players[0]
    kid = cm.academy_players(seller)[0]

    entry = cm.shortlist_add(star, "  forvet hattı için  ")
    assert (entry.player_id, entry.added_season, entry.added_week, entry.note) == (star.id, 1, 1, "forvet hattı için")
    assert cm.shortlist_add(star) is entry and entry.note == "forvet hattı için"       # not verilmedi: dokunulmaz
    cm.shortlist_add(star, "yeni not")
    assert entry.note == "yeni not"
    cm.shortlist_add(kid)
    assert cm.is_shortlisted(star.id) and cm.is_shortlisted(kid.id) and not cm.is_shortlisted(user.players[0].id)

    rows = {r.player_id: r for r in cm.shortlist()}
    assert len(rows) == 2
    row = rows[star.id]
    assert (row.name, row.team_id, row.team_name, row.market_value, row.note, row.in_academy) == (
        star.name, seller.id, seller.name, star.market_value, "yeni not", False)
    assert row.asking_price == transfers.asking_price(star, seller, user.reputation) > 0
    assert (row.transfer_banned, row.ban_reason, row.player) == (False, "", star)
    assert rows[kid.id].asking_price is None and rows[kid.id].in_academy               # akademi satilik degil

    with pytest.raises(ShortlistError, match="zaten senin oyuncun"):
        cm.shortlist_add(user.players[0])
    with pytest.raises(ShortlistError, match="en fazla 120 karakter"):
        cm.shortlist_add(star, "x" * 121)
    with pytest.raises(ShortlistError, match="Oyuncu bulunamadı"):
        cm.shortlist_add(None)
    assert entry.note == "yeni not"

    assert cm.shortlist_remove(kid.id) is True and cm.shortlist_remove(kid.id) is False
    assert [r.player_id for r in cm.shortlist()] == [star.id]


def test_shortlist_shows_bans_and_follows_transfers(db):
    cm = _manager(db)
    user = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(user)
    target = min((p for p in cm.find_team("London Gunners").players if p.position is not Position.GK),
                 key=lambda p: p.overall_rating)
    watched = cm.find_team("Torino Bianconeri").players[1]
    cm.shortlist_add(target)
    cm.shortlist_add(watched)

    # AI transferi: listede kalir ama yeni kulubu ve yasagi gorunur
    buyer = cm.find_team("Paris Rouge-Bleu")
    buyer.transfer_budget, buyer.wage_budget = 10 ** 9, buyer.wage_bill + 1_000_000
    cm.complete_transfer(buyer, watched, 5_000_000, ContractOffer(watched.current_wage, 3, watched.squad_role))
    row = next(r for r in cm.shortlist() if r.player_id == watched.id)
    assert row.team_name == buyer.name and row.transfer_banned and row.ban_reason.startswith("Yeni transfer: 6 hafta")

    # Kullanici alinca listeden duser; oyuncu silinirse (akademi kapasitesi vb.) satir da silinir
    user.transfer_budget, user.wage_budget = 10 ** 9, user.wage_bill + 1_000_000
    cm.complete_transfer(user, target, 1_000_000, ContractOffer(target.current_wage, 3, target.squad_role))
    assert not cm.is_shortlisted(target.id)
    kid = cm.academy_players(cm.find_team("Milano Rossoneri"))[0]
    cm.shortlist_add(kid)
    db.delete(kid)
    db.flush()
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(ShortlistEntry).where(ShortlistEntry.player_id == kid.id)) == 0


# ===========================================================================
# 2) HAZIRLIK MACLARI
# ===========================================================================

def _world_signature(db, cm: CareerManager) -> tuple:
    db.flush()
    teams = tuple(db.execute(select(Team.id, Team.points, Team.played, Team.goals_for, Team.transfer_budget)
                             .order_by(Team.id)).all())
    players = tuple(db.execute(select(Player.id, Player.form, Player.morale, Player.condition,
                                      Player.injured_until_week, Player.suspended_matches,
                                      Player.season_yellow_cards, Player.weeks_since_match)
                               .order_by(Player.id)).all())
    stats = db.scalar(select(func.count()).select_from(PlayerMatchStat))
    played = db.scalar(select(func.count()).select_from(Fixture).where(Fixture.status == FixtureStatus.PLAYED))
    return teams, players, stats, played, cm.manager_reputation, cm.current_week


def test_friendly_is_saved_without_touching_the_competitive_world(db):
    cm = _manager(db)
    user = cm.find_team("Bosphorus Eagles")
    cm.set_user_team(user)
    opponents = cm.friendly_opponents()
    assert user not in opponents and len(opponents) == len(cm.teams()) - 1
    assert [t.reputation for t in opponents] == sorted((t.reputation for t in opponents), reverse=True)
    opponent = cm.find_team("Manchester Blue")

    # SM kurallari: sakat ve cezali oyuncu oynayabilir; menajerin ilk 11'i uygulanir
    xi = cm.auto_lineup(user)
    injured, banned = [p for p in user.players if p.id in xi and p.position is not Position.GK][:2]
    injured.injured_until_week, banned.suspended_matches = 4, 2
    tired = next(p for p in user.players if p.id in xi and p.id not in (injured.id, banned.id))
    tired.condition = 55
    db.flush()
    windows = {p.id: list(p.minutes_window) for p in user.players + opponent.players}
    before = _world_signature(db, cm)

    result = cm.play_friendly(opponent)
    assert (result.season, result.week, result.home_team_id, result.away_team_id) == (1, 1, user.id, opponent.id)
    assert (result.home_score, result.away_score) == (result.match.home_score, result.match.away_score)
    assert result.score == f"{result.home_score}-{result.away_score}"
    goals = [e for e in result.match.events if e.type == EventType.GOAL]
    assert len(result.goals) == len(goals) == result.home_score + result.away_score
    assert all(e.type not in (EventType.YELLOW_CARD, EventType.RED_CARD, EventType.INJURY)
               for e in result.match.events)                                      # kart ve sakatlik yok
    mine = {p.id: p for p in result.match.home.players}
    assert mine[injured.id].played and mine[banned.id].played
    assert not result.match.home.unavailable and not result.match.away.unavailable

    after = _world_signature(db, cm)
    assert after == before                                  # tablo, istatistik, form, moral, kondisyon, itibar ayni
    saved = db.get(Friendly, result.friendly_id)
    assert (saved.home_team_name, saved.away_team_name, saved.home_score, saved.away_score) == (
        user.name, opponent.name, result.home_score, result.away_score)
    assert saved.events == result.goals and cm.friendlies() == [saved] and cm.friendlies(season=2) == []

    # Oynayanlar kaygi penceresine yarim agirlikli dakika alir (iki takim da); pencere olayi acilmaz
    for side in (result.match.home, result.match.away):
        for mp in side.players:
            window = db.get(Player, mp.id).minutes_window
            if mp.played:
                assert window == [[0.0, 0.0, window[0][2], round(min(mp.minutes_played, 90) * 0.5, 1)]]
                assert cn.playing_time(window).played_matches == round(min(mp.minutes_played, 90) * 0.5 / 90, 1)
            else:
                assert window == windows[mp.id]


def test_one_friendly_per_week_and_invalid_requests(db):
    cm = _manager(db)
    with pytest.raises(FriendlyError, match="önce yöneteceğin takımı seç"):
        cm.play_friendly(cm.find_team("Madrid Blancos"))
    user = cm.find_team("Karadeniz Storm")
    cm.set_user_team(user)
    with pytest.raises(FriendlyError, match="kendisiyle"):
        cm.play_friendly(user)
    for bad in (None, "Madrid Blancos", Team(name="Hayalet")):
        with pytest.raises(FriendlyError, match="Geçersiz rakip"):
            cm.play_friendly(bad)
    assert cm.friendlies() == []

    first = cm.play_friendly(cm.find_team("Madrid Blancos"))
    with pytest.raises(FriendlyError, match="Bu hafta zaten hazırlık maçı oynadın"):
        cm.play_friendly(cm.find_team("London Gunners"))
    cm.play_week()                                                        # yeni hafta: yeniden oynanabilir
    second = cm.play_friendly(cm.find_team("London Gunners"))
    assert (first.week, second.week) == (1, 2)
    assert [f.id for f in cm.friendlies()] == [second.friendly_id, first.friendly_id]
    # Hazirlik dakikasi son resmi mac olayina eklenir, resmi mac olayi sayisini arttirmaz
    played = [mp for mp in second.match.home.players if mp.played]
    player = db.get(Player, played[0].id)
    assert player.minutes_window[-1][3] > 0
    assert sum(1 for entry in player.minutes_window if entry[1] > 0) <= 1       # yalnizca 1. haftanin resmi maci

    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    db.expire_all()
    cm.tournaments.current().status = TournamentStatus.FINISHED
    db.flush()
    assert cm.season_finished
    with pytest.raises(FriendlyError, match="Sezon bitti"):
        cm.play_friendly(cm.find_team("London Gunners"))


def test_friendlies_are_career_mode_only(db):
    cm = _manager(db)
    cm.set_game_mode(GameMode.TOURNAMENT)
    participant = cm.tournaments.participants(cm.tournaments.current())[0]
    cm.set_user_team(participant)
    with pytest.raises(FriendlyError, match="yalnızca kariyer modunda"):
        cm.play_friendly(cm.find_team("Karadeniz Storm"))


def test_bench_lineup_choice_is_respected_in_friendlies(db):
    cm = _manager(db)
    user = cm.find_team("Rhône Gones")
    cm.set_user_team(user)
    xi = cm.auto_lineup(user)
    left_out = next(p for p in user.players if p.id not in xi and p.position is not Position.GK)
    left_out.lineup_status = LineupStatus.OUT
    db.flush()
    result = cm.play_friendly(cm.find_team("Rocher Monégasques"))
    starters = {p.id for p in result.match.home.players if p.entered_minute == 0}
    assert starters == set(xi)
