"""
FM dunyasi ve menajer tanınırlığı ENTEGRASYON testleri (6. Asama).

Test veritabaninda calisir (conftest). FM dunyasi yazma testi mevcut sentetik
dunyayi transaction icinde silip FM ornegini yazar, dogrular ve ROLLBACK eder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fm_parser  # noqa: E402
import reputation  # noqa: E402
import seed  # noqa: E402
from career_manager import CareerManager  # noqa: E402
from match_engine import MatchEngine, build_match_team  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    GameState,
    League,
    Player,
    PlayerMatchStat,
    Position,
    Staff,
    Team,
)

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "fm" / "sample_fm_export.html"


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


def _wipe(db):
    for model in (PlayerMatchStat, Fixture, GameState, Staff, Player, Team, League):
        db.execute(delete(model))
    db.flush()


@pytest.fixture
def fm_world(db):
    """Sentetik dunyayi (transaction icinde) silip FM ornegini yazar."""
    _wipe(db)
    world = seed.build_fm_world(fm_parser.parse_files([SAMPLE]), rng_seed=2026)
    seed.write_world(db, world, rng_seed=2026)
    db.flush()
    db.expire_all()
    return world


def test_fm_world_is_persisted(db, fm_world):
    assert db.scalar(select(func.count()).select_from(League)) == 3
    assert db.scalar(select(func.count()).select_from(Team)) == 6
    assert db.scalar(select(func.count()).select_from(Player)) == fm_world.player_count

    real = db.scalars(select(Player).where(Player.data_source == "fm")).all()
    assert len(real) == 42                                             # 6 kulup x 7 (Kuzey Yildizi atlandi)
    sample = next(p for p in real if p.team.name == "Galatasaray")
    assert sample.fm_attributes and 1 <= min(sample.fm_attributes.values()) <= max(sample.fm_attributes.values()) <= 20
    assert sample.nationality == "TUR" and sample.current_ability and sample.potential_ability
    assert any(ch in p.name for p in real for ch in "ıçğöşüé")           # UTF-8 kaliciligi

    bayern = db.scalar(select(Team).where(Team.name == "Bayern München"))
    assert bayern.league.name == "Bundesliga" and bayern.league.country == "Almanya"
    assert bayern.reputation == 94
    state = db.get(GameState, 1)
    assert state.manager_reputation == reputation.START_REPUTATION


def test_fm_squads_are_playable(db, fm_world):
    for team in db.scalars(select(Team)):
        assert len(team.players) >= seed.FM_MIN_SQUAD
        assert sum(p.position is Position.GK for p in team.players) >= 2
        assert team.free_wage >= 0 and team.staff
    real, barca = (db.scalar(select(Team).where(Team.name == n)) for n in ("Real Madrid", "Barcelona"))
    result = MatchEngine(build_match_team(real, True, 1), build_match_team(barca, False, 1), seed=9).simulate()
    assert result.events[-1].type.value == "FULL_TIME"
    assert sum(p.played for p in result.home.players) >= 11


def test_fm_career_week_runs(db, fm_world):
    cm = CareerManager(db, seed=4)
    cm.set_user_team(cm.find_team("Galatasaray"))
    report = cm.play_week()
    assert len(report.results) == 3                                     # 3 lig x 1 mac
    assert report.user_result is not None


# ---------------------------------------------------------------------------
# Menajer tanınırlığı (sentetik dunya uzerinde)
# ---------------------------------------------------------------------------

@pytest.fixture
def cm(db):
    manager = CareerManager(db, seed=5)
    if manager.season_finished:
        pytest.skip("Sezon bitmiş")
    return manager


def test_manager_reputation_moves_with_results(cm):
    team = cm.find_team("Galatasaray")
    cm.set_user_team(team)
    cm.run_ai_transfer_window = lambda: []
    before = cm.manager_reputation
    report = cm.play_week()
    assert report.manager_reputation is not None
    old, new = report.manager_reputation
    assert old == before and new == cm.manager_reputation
    r = report.user_result
    mine, theirs = (r.home, r.away) if r.home.id == team.id else (r.away, r.home)
    if mine.stats.goals > theirs.stats.goals:
        assert new > old
    elif mine.stats.goals < theirs.stats.goals:
        assert new < old
    else:
        assert new >= old


def test_no_reputation_change_without_user_team(cm):
    cm.state.user_team_id = None
    before = cm.manager_reputation
    report = cm.play_week()
    assert report.manager_reputation is None and cm.manager_reputation == before


def test_season_end_applies_position_bonus_once(cm):
    cm.set_user_team(cm.find_team("Manchester City"))
    last = None
    for _ in range(12):
        if cm.season_finished:
            break
        last = cm.play_week()
    assert last is not None and last.season_finished
    assert last.season_reputation_delta is not None
    team = cm.user_team
    position = cm.position_of(team)
    assert last.season_reputation_delta == reputation.season_delta(position, len(cm.standings(team.league_id)))
    after = cm.manager_reputation
    extra = cm.play_week()                                              # sezon bitti: bir sey degismemeli
    assert not extra.played_any and cm.manager_reputation == after


def test_user_negotiation_uses_career_manager_reputation(cm):
    buyer = cm.find_team("Trabzonspor")
    cm.set_user_team(buyer)
    star = max(cm.find_team("Manchester City").players, key=lambda p: p.overall_rating)

    cm.state.manager_reputation = 1.0
    closed = cm.open_negotiation(buyer, star, 10_000_000)
    assert not closed.open and closed.manager_reputation == 1.0
    assert "masasına oturmadı" in closed.opening_message

    cm.state.manager_reputation = 20.0
    assert cm.open_negotiation(buyer, star, 10_000_000).manager_reputation == 20.0


def test_ai_clubs_use_reputation_derived_manager(cm):
    city, trabzon = cm.find_team("Manchester City"), cm.find_team("Trabzonspor")
    cm.state.user_team_id = None
    target = max(cm.find_team("Inter").players, key=lambda p: p.overall_rating)
    assert cm.open_negotiation(city, target, 1).manager_reputation == reputation.ai_manager_reputation(city.reputation)
    assert cm.manager_reputation_for(city) > cm.manager_reputation_for(trabzon)
