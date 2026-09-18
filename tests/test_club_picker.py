"""
Kulup secimi (Faz 13G): ulke -> lig -> kulup secicinin saf yardimcilari ve kariyer kilidi (CareerManager).

    * saf: ulke sirasi (Turkiye once), lig gruplama, kulup sirasi, arama, kart HTML'i (kacis, sayisal kadro gucu yok)
    * entegrasyon (rollback): choose_club -- kariyerde kulup bir kez secilir ve KILITLENIR (eski kayit dahil), mod
      degisikligi kapanir; turnuva modunda yalnizca katilimcilar ve ilk maca kadar degisiklik; paylasilan dunyada
      hic; gecersiz kadroyla secilen kulupte asistan ilk 11'i kurar; career_club_cards tek sorguda ulkeyi ligden alir
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import club_picker_view as cp  # noqa: E402


def _card(team_id, name, country, league_id, league, reputation, **extra):
    return cp.ClubCard(team_id=team_id, name=name, league_id=league_id, league_name=league, country=country,
                       reputation=reputation, stadium_capacity=extra.pop("capacity", 40_000),
                       transfer_budget=extra.pop("budget", 10_000_000), squad_rating=extra.pop("rating", 75.0),
                       **extra)


CARDS = [
    _card(1, "Galatasaray", "Türkiye", 10, "Süper Lig", 82),
    _card(2, "Fenerbahçe", "Türkiye", 10, "Süper Lig", 80),
    _card(3, "Göztepe", "Türkiye", 11, "1. Lig", 60),
    _card(4, "Arsenal", "İngiltere", 20, "Premier Lig", 90),
    _card(5, "Bayern", "Almanya", 30, "Bundesliga", 93, eligible=False, reason="4. seviye gerekli"),
    _card(6, "Leverkusen", "Almanya", 30, "Bundesliga", 85, protected=True),
]


def test_countries_home_first_then_alphabetical_and_leagues_by_strength():
    assert cp.countries(CARDS) == ["Türkiye", "Almanya", "İngiltere"]
    assert cp.leagues_of(CARDS, "Türkiye") == [(10, "Süper Lig", 2), (11, "1. Lig", 1)]
    assert [c.name for c in cp.clubs_in(CARDS, 10)] == ["Galatasaray", "Fenerbahçe"]
    assert [c.name for c in cp.clubs_in(CARDS, 30)] == ["Leverkusen", "Bayern"]        # uygun olan once
    assert cp.leagues_of(CARDS, None) == []


def test_search_is_accent_insensitive_over_club_league_and_country():
    assert [c.name for c in cp.search(CARDS, "fenerbahce")] == ["Fenerbahçe"]
    assert {c.name for c in cp.search(CARDS, "super lig")} == {"Galatasaray", "Fenerbahçe"}
    assert {c.name for c in cp.search(CARDS, "ingiltere")} == {"Arsenal"}
    assert cp.search(CARDS, "  ") == []


def test_card_html_escapes_names_and_shows_no_raw_squad_number():
    evil = _card(9, '<img src=x onerror="alert(1)">', "Fransa", 40, "Ligue <b>1</b>", 71, rating=77.4,
                 capacity=52_280, eligible=False, reason="<script>x</script>")
    html = cp.card_html(evil)
    assert "<img" not in html and "<script>" not in html and "&lt;img" in html
    assert "77" not in html and "★" in html                         # kadro gucu yalnizca yildiz
    assert "52.280 koltuk" in html and "Transfer bütçesi" in html and "İtibar" in html
    assert "🛡️" in cp.card_html(CARDS[5]) and cp.capacity_text(None) == "—"


# ===========================================================================
# ENTEGRASYON
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, mode=None):
    from career_manager import CareerManager

    cm = CareerManager(db, seed=9)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil")
    cm.state.game_mode, cm.state.user_team_id = None, None          # onceki modulden bagimsiz (rollback edilir)
    cm.state.user_team = None
    db.flush()
    if mode is not None:
        cm.set_game_mode(mode)
    return cm


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_career_club_is_chosen_once_and_locked(db):
    from career_manager import MODE_LOCKED_TEXT, ClubChoiceError
    from models import GameMode, LineupStatus

    cm = _manager(db)
    team = cm.find_team("Karadeniz Storm")
    with pytest.raises(ClubChoiceError, match="Önce oyun modunu seç"):
        cm.choose_club(team)
    cm.set_game_mode(GameMode.CAREER)
    assert not cm.club_locked() and not cm.career_mode_locked()
    for p in team.players:                                          # yeni dunyadaki gibi gecersiz kayitli kadro
        p.lineup_status, p.lineup_role = LineupStatus.BENCH, None
    db.flush()
    notes = cm.choose_club(team)
    assert cm.user_team is team and notes and "Asistan" in notes[0]
    assert len(cm.lineup_of(team)[0]) == 11 and cm.lineup_check(team).ok
    assert cm.club_locked() and cm.career_mode_locked()
    assert cm.choose_club(team) == []                               # ayni kulup: sessizce gecer
    with pytest.raises(ClubChoiceError, match="Karadeniz Storm bu kariyerde senin kulübün"):
        cm.choose_club(cm.find_team("Istanbul Lions"))
    assert cm.user_team is team
    with pytest.raises(ValueError, match=MODE_LOCKED_TEXT):
        cm.reset_game_mode()
    with pytest.raises(ValueError, match=MODE_LOCKED_TEXT):
        cm.set_game_mode(GameMode.TOURNAMENT)                       # kariyer -> turnuva -> kariyer ile kacis yok
    assert cm.game_mode is GameMode.CAREER


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_legacy_save_with_a_club_stays_locked(db):
    from career_manager import ClubChoiceError
    from models import GameMode

    cm = _manager(db, GameMode.CAREER)
    cm.set_user_team(cm.find_team("Istanbul Lions"))                # eski kayit: kulup kilitsiz yoldan yazilmis
    assert cm.club_locked()
    with pytest.raises(ClubChoiceError, match="değiştirilemez"):
        cm.choose_club(cm.find_team("Kadıköy Canaries"))


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_tournament_mode_accepts_participants_until_the_first_match(db):
    from career_manager import ClubChoiceError
    from models import GameMode

    cm = _manager(db, GameMode.TOURNAMENT)
    cm.run_ai_transfer_window = lambda: []
    t = cm.tournaments.current()
    participants = cm.tournaments.participants(t)
    outsider = next(team for team in cm.teams() if not cm.tournaments.is_participant(t, team.id))
    with pytest.raises(ClubChoiceError, match="Devler Arenası'nda yok"):
        cm.choose_club(outsider)
    cm.choose_club(participants[0])
    assert not cm.club_locked() and not cm.career_mode_locked()
    cm.choose_club(participants[1])                                 # ilk maca kadar serbest
    assert cm.user_team is participants[1]
    cm.play_week()                                                  # kura + ilk tur
    assert cm.club_locked()
    with pytest.raises(ClubChoiceError, match="değiştirilemez"):
        cm.choose_club(participants[2])


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_career_club_cards_one_query_country_from_league(db):
    from sqlalchemy import event, func, select

    from database import engine
    from models import Team

    statements: list[str] = []

    def listener(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        cards = cp.career_club_cards(db)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(statements) == 1                                     # N+1 yok
    assert len(cards) == db.scalar(select(func.count()).select_from(Team))
    assert {c.country for c in cards} == {"Türkiye", "İngiltere", "İtalya", "İspanya", "Almanya", "Fransa"}
    assert all(c.squad_rating and c.league_id and c.transfer_budget >= 0 for c in cards)
    lions = next(c for c in cards if c.name == "Istanbul Lions")
    assert cp.career_club_cards(db, [lions.team_id]) == [lions] and cp.career_club_cards(db, []) == []
