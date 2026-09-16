"""
Sezon dongusu (career_manager) testleri.

Saf kurallar DB'siz test edilir. Entegrasyon testleri gercek PostgreSQL'e
karsi calisir ama her test kendi transaction'ini rollback eder; veritabani
kirlenmez. DB yoksa entegrasyon testleri atlanir.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from career_manager import (  # noqa: E402
    INJURY_TABLE,
    CareerManager,
    SeasonNotFinished,
    bench_form_drift,
    form_delta,
    injury_weeks,
    morale_delta,
    outcome_for,
    suspension_length,
)
from match_engine import EngineConfig, MatchEngine, MatchTeam, build_match_team  # noqa: E402
from models import Position  # noqa: E402
from tests.test_match_engine import make_player, make_team  # noqa: E402

# ---------------------------------------------------------------------------
# Saf kurallar
# ---------------------------------------------------------------------------

def test_form_delta_direction_and_bounds():
    assert form_delta(6.5) == 0
    assert form_delta(8.5) == 8
    assert form_delta(5.0) == -6
    assert form_delta(10.0) == 12 and form_delta(1.0) == -12


def test_morale_delta_combines_performance_and_result():
    assert morale_delta(6.5, "W") == 5
    assert morale_delta(6.5, "L") == -5
    assert morale_delta(8.0, "L") == -2          # iyi oynadi ama kaybetti
    assert morale_delta(None, "W") == 2          # oynamadi, sonucun yarisi (yuvarlanmis)
    assert morale_delta(None, "L") == -2


def test_bench_form_drift_moves_toward_50():
    assert bench_form_drift(70) == -2
    assert bench_form_drift(30) == 2
    assert bench_form_drift(51) == -1
    assert bench_form_drift(50) == 0


def test_injury_weeks_distribution():
    rng = random.Random(1)
    draws = [injury_weeks(rng) for _ in range(2000)]
    allowed = {w for w, _ in INJURY_TABLE}
    assert set(draws) <= allowed
    assert sum(draws) / len(draws) < 3.0         # cogu sakatlik kisa
    assert draws.count(1) > draws.count(10)


def test_suspension_length_rules():
    rng = random.Random(3)
    assert all(suspension_length(rng, second_yellow=True) == 1 for _ in range(50))
    straight = {suspension_length(rng, second_yellow=False) for _ in range(200)}
    assert straight == {1, 3}


def test_outcome_for():
    assert (outcome_for(2, 1), outcome_for(1, 1), outcome_for(0, 3)) == ("W", "D", "L")


# ---------------------------------------------------------------------------
# Motor: eksik kadro ve kadro disi listesi
# ---------------------------------------------------------------------------

def test_engine_runs_with_short_squad():
    players = make_team(1, "Ev", 80).players[:9]      # 2 GK + 4 DEF + 3 MID
    team = MatchTeam(id=1, name="Eksik", reputation=70, players=players)
    result = MatchEngine(team, make_team(2, "Dep", 80), seed=4).simulate()
    assert team.player_count <= 9
    assert result.events[-1].type.value == "FULL_TIME"
    on_pitch_roles = {p.role for p in team.players if p.played}
    assert Position.GK in on_pitch_roles


def test_unavailable_players_never_enter_pitch():
    team = make_team(1, "Ev", 80)
    star = make_player(999, Position.FWD, 95)
    team.unavailable.append((star, "sakat"))
    MatchEngine(team, make_team(2, "Dep", 80), seed=0).simulate()
    assert not star.played and star.id not in {p.id for p in team.players}


# ---------------------------------------------------------------------------
# Entegrasyon
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _fresh_manager(db, seed=1, **cfg) -> CareerManager:
    cm = CareerManager(db, seed=seed, engine_config=EngineConfig(**cfg) if cfg else None)
    if cm.season_finished:
        pytest.skip("Sezon bitmiş; 'python seed.py' ile sıfırla")
    return cm


def _stat_rows(db, player_id, week, season):
    from sqlalchemy import select

    from models import Fixture, PlayerMatchStat

    return list(db.scalars(
        select(PlayerMatchStat)
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .where(PlayerMatchStat.player_id == player_id, Fixture.week == week, Fixture.season == season)
    ))


@integration
@pytest.mark.integration
def test_build_match_team_excludes_injured_and_suspended(db):
    cm = _fresh_manager(db)
    team = cm.find_team("Galatasaray")
    week = cm.current_week
    injured, suspended = team.players[0], team.players[1]
    injured.injured_until_week = week + 3
    suspended.suspended_matches = 1
    db.flush()

    mt = build_match_team(team, True, current_week=week)
    ids = {p.id for p in mt.players}
    assert injured.id not in ids and suspended.id not in ids
    reasons = {p.id: r for p, r in mt.unavailable}
    assert "sakat" in reasons[injured.id] and "cezalı" in reasons[suspended.id]
    assert len(mt.players) == 13
    mt.select_lineup()
    assert mt.player_count == 11


@integration
@pytest.mark.integration
def test_play_week_plays_all_fixtures_and_advances(db):
    from sqlalchemy import func, select

    from models import Player, PlayerMatchStat

    cm = _fresh_manager(db, seed=11)
    week, season = cm.current_week, cm.season
    expected = [f for f in cm.fixtures_for_week(week) if not f.is_played]
    before_rows = db.scalar(select(func.count()).select_from(PlayerMatchStat))

    report = cm.play_week()

    assert len(report.results) == len(expected) > 0
    assert all(fx.is_played for fx, _ in report.results)
    assert cm.current_week == week + 1
    assert report.week == week and report.season == season

    played_players = sum(sum(1 for p in t.players if p.played) for _, r in report.results for t in (r.home, r.away))
    db.flush()
    after_rows = db.scalar(select(func.count()).select_from(PlayerMatchStat))
    assert after_rows - before_rows == played_players

    for _, r in report.results:
        for team in (r.home, r.away):
            for mp in team.players:
                p = db.get(Player, mp.id)
                assert 0 <= p.form <= 100 and 0 <= p.morale <= 100
                if mp.played:
                    assert p.match_rating_history and p.match_rating_history[-1] == mp.rating


@integration
@pytest.mark.integration
def test_injured_player_cannot_play_next_week(db):
    """Kullanicinin istedigi test: bu hafta sakatlanan, gelecek hafta sahaya cikamaz."""
    from models import Player

    cm = _fresh_manager(db, seed=3, base_injury=0.03)   # ~2.8 sakatlik/mac -> garanti ornek
    week = cm.current_week
    report = cm.play_week()
    assert report.injuries, "sakatlik uretilemedi"

    next_week = cm.current_week
    for note in report.injuries:
        p = db.get(Player, note.player_id)
        assert p.injured_until_week > next_week
        assert not p.is_available(next_week)
        mt = build_match_team(p.team, True, current_week=next_week)
        assert p.id not in {x.id for x in mt.players}
        assert any(x.id == p.id for x, _ in mt.unavailable)
        assert _stat_rows(db, p.id, week, cm.season)  # bu hafta oynadi (sakatlandigi mac)

    if not cm.season_finished:
        cm.play_week()
        for note in report.injuries:
            assert not _stat_rows(db, note.player_id, next_week, cm.season), "sakat oyuncu oynadi!"


@integration
@pytest.mark.integration
def test_suspended_player_sits_out_then_returns(db):
    cm = _fresh_manager(db, seed=5)
    team = cm.find_team("Fenerbahce")
    week = cm.current_week
    star = max(team.players, key=lambda p: p.overall_rating)
    star.suspended_matches = 1
    db.flush()

    cm.play_week()
    assert not _stat_rows(db, star.id, week, cm.season)
    assert star.suspended_matches == 0
    assert star.is_available(cm.current_week)
    mt = build_match_team(team, True, current_week=cm.current_week)
    assert star.id in {p.id for p in mt.players}


@integration
@pytest.mark.integration
def test_red_card_creates_suspension(db):
    from models import Player

    cm = _fresh_manager(db, seed=8, base_card=0.25, straight_red_share=0.5)
    report = cm.play_week()
    reds = [n for n in report.suspensions if "kırmızı" in n.detail or "ikinci sarı" in n.detail]
    assert reds, "kirmizi kart uretilemedi"
    for note in reds:
        p = db.get(Player, note.player_id)
        assert p.suspended_matches >= 1
        assert not p.is_available(cm.current_week)
        assert _stat_rows(db, p.id, report.week, cm.season)[0].red_card


@integration
@pytest.mark.integration
def test_full_season_then_new_season(db):
    from sqlalchemy import func, select

    from models import Fixture, FixtureStatus, Player

    cm = _fresh_manager(db, seed=21)
    ages_before = {p.id: p.age for p in db.scalars(select(Player))}
    with pytest.raises(SeasonNotFinished):      # sezon bitmeden yeni sezon acilamaz
        cm.start_new_season()
    for _ in range(12):
        if cm.season_finished:
            break
        cm.play_week()
    assert cm.season_finished

    old_season = cm.season
    for league in cm.leagues():
        table = cm.standings(league.id)
        assert cm.champion(league.id) is table[0]
        assert all(t.played == cm.total_weeks() for t in table)
        assert all(t.points == 3 * t.won + t.drawn for t in table)

    new_season = cm.start_new_season()
    assert new_season == old_season + 1
    assert cm.season == new_season and cm.current_week == 1
    unplayed = db.scalar(select(func.count()).select_from(Fixture).where(
        Fixture.season == new_season, Fixture.status == FixtureStatus.UNPLAYED))
    assert unplayed == 36
    assert all(t.played == 0 and t.points == 0 for t in cm.teams())
    for p in db.scalars(select(Player)):
        assert p.age == min(45, ages_before[p.id] + 1)
        assert p.injured_until_week == 0 and p.suspended_matches == 0 and p.match_rating_history == []


@integration
@pytest.mark.integration
def test_top_scorers_after_week(db):
    cm = _fresh_manager(db, seed=2)
    report = cm.play_week()
    total_goals = sum(r.home_score + r.away_score for _, r in report.results)
    rows = cm.top_scorers()
    assert sum(r.goals for r in rows) <= total_goals
    if total_goals:
        assert rows and rows[0].goals >= rows[-1].goals >= 1
