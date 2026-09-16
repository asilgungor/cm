"""
Mac motoru testleri.

  python -m pytest                    # hepsi (DB ayaktaysa entegrasyon dahil)
  python -m pytest -m "not integration"

Saf motor testleri veritabanina dokunmaz; sentetik kadrolarla calisir.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from match_engine import (  # noqa: E402
    EngineConfig,
    EventType,
    MatchEngine,
    MatchPlayer,
    MatchTeam,
    update_standings,
)
from models import Position  # noqa: E402

# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

COMPOSITION = {Position.GK: 2, Position.DEF: 4, Position.MID: 5, Position.FWD: 4}


def make_player(pid: int, pos: Position, ovr: int, *, form=50, morale=70, age=26) -> MatchPlayer:
    gk = ovr + 6 if pos is Position.GK else 30
    return MatchPlayer(
        id=pid, name=f"P{pid}-{pos.value}", position=pos, age=age, overall=ovr,
        pace=ovr, shooting=ovr + (4 if pos is Position.FWD else -10),
        passing=ovr + (3 if pos is Position.MID else -5),
        defending=ovr + (6 if pos is Position.DEF else -15),
        dribbling=ovr, goalkeeping=gk, form=form, morale=morale,
    )


def make_team(tid: int, name: str, ovr: int, **kw) -> MatchTeam:
    players, pid = [], tid * 100
    for pos, n in COMPOSITION.items():
        for _ in range(n):
            pid += 1
            players.append(make_player(pid, pos, ovr, **kw))
    return MatchTeam(id=tid, name=name, reputation=80, players=players)


def run(home_ovr=80, away_ovr=80, seed=1, cfg=None):
    return MatchEngine(make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr),
                       seed=seed, config=cfg).simulate()


# ---------------------------------------------------------------------------
# Kadro secimi ve efektif guc
# ---------------------------------------------------------------------------

def test_lineup_has_eleven_with_formation():
    team = make_team(1, "T", 80)
    MatchEngine(team, make_team(2, "U", 80), seed=0)
    on = team.on_pitch
    assert len(on) == 11
    by_role = {pos: sum(1 for p in on if p.role is pos) for pos in Position}
    assert by_role == {Position.GK: 1, Position.DEF: 4, Position.MID: 4, Position.FWD: 2}
    assert len(team.bench) == 4


def test_lineup_picks_best_by_effective_power():
    team = make_team(1, "T", 80)
    weak_gk, strong_gk = [p for p in team.players if p.position is Position.GK]
    weak_gk.overall, strong_gk.overall = 70, 85
    MatchEngine(team, make_team(2, "U", 80), seed=0)
    assert team.keeper is strong_gk
    assert weak_gk in team.bench


def test_condition_factor_is_dampened_and_neutral_at_defaults():
    cfg = EngineConfig()
    team = make_team(1, "T", 80)
    MatchEngine(team, make_team(2, "U", 80), seed=0, config=cfg)
    neutral = next(p for p in team.players)
    assert neutral.form == 50 and neutral.morale == 70
    assert neutral.condition_factor == pytest.approx(1.0)
    assert neutral.effective_power == pytest.approx(80.0)

    hot = make_player(999, Position.FWD, 80, form=65, morale=85)
    cold = make_player(998, Position.FWD, 80, form=45, morale=60)
    t = MatchTeam(id=9, name="X", reputation=50, players=[hot, cold] + make_team(3, "Y", 70).players)
    MatchEngine(t, make_team(4, "Z", 70), seed=0, config=cfg)
    lo, hi = cfg.condition_clamp
    assert lo <= cold.condition_factor < 1.0 < hot.condition_factor <= hi
    # ham formul 2x fark verirdi; sonumlenmis fark cok daha dar olmali
    assert hot.condition_factor / cold.condition_factor < 1.5


# ---------------------------------------------------------------------------
# Mac akisi tutarliligi
# ---------------------------------------------------------------------------

def test_score_matches_goal_events_and_player_goals():
    r = run(seed=7)
    goals = [e for e in r.events if e.type is EventType.GOAL]
    assert r.home_score == sum(1 for e in goals if e.team_id == r.home.id)
    assert r.away_score == sum(1 for e in goals if e.team_id == r.away.id)
    assert r.home_score == sum(p.goals for p in r.home.players)
    assert r.away_score == sum(p.goals for p in r.away.players)
    assert r.events[0].type is EventType.KICK_OFF
    assert r.events[-1].type is EventType.FULL_TIME
    assert any(e.type is EventType.HALF_TIME for e in r.events)


def test_events_are_chronological():
    r = run(seed=3)
    keys = [(e.minute, e.added_time) for e in r.events]
    assert keys == sorted(keys)


def test_stoppage_time_within_bounds():
    for seed in range(20):
        r = run(seed=seed)
        assert 1 <= r.first_half_added <= 4
        assert 2 <= r.second_half_added <= 7
        assert all(e.minute <= 90 for e in r.events)


def test_deterministic_with_seed():
    a, b = run(seed=42), run(seed=42)
    assert a.home_score == b.home_score and a.away_score == b.away_score
    assert [e.description for e in a.events] == [e.description for e in b.events]


def test_shots_stats_consistent():
    r = run(seed=11)
    for t in (r.home, r.away):
        assert t.stats.shots == sum(p.shots for p in t.players)
        assert t.stats.shots_on_target <= t.stats.shots
        assert t.stats.goals <= t.stats.shots_on_target
    assert r.home.stats.possession_minutes + r.away.stats.possession_minutes == r.total_minutes


def test_fatigue_reduces_energy_and_late_subs_happen():
    r = run(seed=5)
    starters = [p for p in r.home.players if p.entered_minute == 0 and p.left_minute == 90]
    assert all(p.energy < 100 for p in starters)
    gk = next(p for p in r.home.players if p.role is Position.GK and p.entered_minute == 0)
    mids = [p for p in starters if p.role is Position.MID]
    assert gk.energy > max(p.energy for p in mids)  # kaleci daha az yorulur


# ---------------------------------------------------------------------------
# Kart ve sakatlik mekanikleri (zorla tetiklenir)
# ---------------------------------------------------------------------------

def test_red_card_reduces_players_and_strength():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.minute = 30
    before = eng._team_strength(eng.home, "defense")
    victim = next(p for p in eng.home.on_pitch if p.role is Position.DEF)
    eng._send_off(eng.home, victim, second_yellow=False)
    assert eng.home.player_count == 10
    assert victim.sent_off and not victim.on_pitch
    assert eng.home.stats.red_cards == 1
    assert eng._team_strength(eng.home, "defense") < before * 0.85
    assert eng.events[-1].type is EventType.RED_CARD


def test_second_yellow_becomes_red():
    # Kart kesin cikar, hep savunan takima gider ve secim hep p'ye duser
    cfg = EngineConfig(base_card=1.0, straight_red_share=0.0, defending_team_card_share=1.0)
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, config=cfg)
    p = eng.home.on_pitch[3]
    p.yellow_cards = 1
    eng._weighted_choice = lambda players, weight: p
    eng.minute = 50
    eng._discipline(eng.away, eng.home)
    assert p.sent_off and not p.on_pitch
    assert eng.home.player_count == 10
    assert eng.home.stats.red_cards == 1
    assert eng.events[-1].type is EventType.RED_CARD
    assert "ikinci sarı" in eng.events[-1].description


def test_injury_brings_same_position_sub():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.minute = 20
    out = next(p for p in eng.home.on_pitch if p.role is Position.FWD)
    out.injured = True
    eng.home.remove_player(out, 20)
    eng._substitute_for(eng.home, out)
    assert eng.home.player_count == 11
    sub = eng.events[-1]
    assert sub.type is EventType.SUBSTITUTION
    entered = next(p for p in eng.home.players if p.name == sub.player)
    assert entered.position is Position.FWD and entered.role is Position.FWD


def test_injury_without_same_position_uses_out_of_position_sub():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.minute = 20
    out = next(p for p in eng.home.on_pitch if p.role is Position.DEF)  # kulubede DEF yok
    out.injured = True
    eng.home.remove_player(out, 20)
    eng._substitute_for(eng.home, out)
    assert eng.home.player_count == 11
    entered = next(p for p in eng.home.players if p.name == eng.events[-1].player)
    assert entered.role is Position.DEF and entered.position is not Position.DEF
    assert "mevki dışı" in eng.events[-1].description


def test_gk_injury_brings_backup_gk():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.minute = 33
    gk = eng.home.keeper
    gk.injured = True
    eng.home.remove_player(gk, 33)
    eng._substitute_for(eng.home, gk)
    new_gk = eng.home.keeper
    assert new_gk is not None and new_gk is not gk
    assert new_gk.position is Position.GK
    assert eng.home.player_count == 11


def test_gk_red_card_sacrifices_outfield_for_backup_gk():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.minute = 40
    eng._send_off(eng.home, eng.home.keeper, second_yellow=False)
    assert eng.home.player_count == 10
    assert eng.home.keeper is not None and eng.home.keeper.position is Position.GK
    assert "feda" in eng.events[-1].description


def test_no_subs_left_team_plays_short_and_emergency_keeper():
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.minute = 60
    eng.home.subs_used = eng.cfg.max_subs
    gk = eng.home.keeper
    gk.injured = True
    eng.home.remove_player(gk, 60)
    eng._substitute_for(eng.home, gk)
    assert eng.home.player_count == 10
    k = eng.home.keeper
    assert k is not None and k.position is not Position.GK and k.role is Position.GK
    assert eng._keeper_strength(eng.home) < 40  # saha oyuncusu kalede zayif


# ---------------------------------------------------------------------------
# Kalibrasyon (istatistiksel sinirlar)
# ---------------------------------------------------------------------------

def test_monte_carlo_realistic_ranges():
    n, goals, yellows, reds, injuries, shots = 300, 0, 0, 0, 0, 0
    for seed in range(n):
        r = run(seed=seed)
        goals += r.home_score + r.away_score
        yellows += r.home.stats.yellow_cards + r.away.stats.yellow_cards
        reds += r.home.stats.red_cards + r.away.stats.red_cards
        injuries += r.home.stats.injuries + r.away.stats.injuries
        shots += r.home.stats.shots + r.away.stats.shots
    assert 2.2 <= goals / n <= 3.4, goals / n
    assert 2.5 <= yellows / n <= 5.5, yellows / n
    assert 0.05 <= reds / n <= 0.45, reds / n
    assert 0.15 <= injuries / n <= 0.8, injuries / n
    assert 16 <= shots / n <= 32, shots / n


def test_stronger_team_wins_more_and_home_advantage_exists():
    n = 300
    strong_wins = sum(1 for s in range(n) if run(86, 76, seed=s).home_score > run(86, 76, seed=s).away_score)
    assert strong_wins / n > 0.60

    home_wins = away_wins = 0
    for s in range(n):
        r = run(80, 80, seed=s)
        home_wins += r.home_score > r.away_score
        away_wins += r.away_score > r.home_score
    assert home_wins > away_wins


# ---------------------------------------------------------------------------
# Puan tablosu matematigi
# ---------------------------------------------------------------------------

def _row():
    return SimpleNamespace(played=0, won=0, drawn=0, lost=0, points=0, goals_for=0, goals_against=0)


def test_update_standings_math():
    t = _row()
    update_standings(t, 2, 1)
    update_standings(t, 1, 1)
    update_standings(t, 0, 3)
    assert (t.played, t.won, t.drawn, t.lost, t.points) == (3, 1, 1, 1, 4)
    assert (t.goals_for, t.goals_against) == (3, 5)
    assert t.played == t.won + t.drawn + t.lost


# ---------------------------------------------------------------------------
# Entegrasyon (PostgreSQL)
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@integration
@pytest.mark.integration
def test_schema_matches_models():
    from sqlalchemy import inspect

    import models  # noqa: F401
    from database import Base, engine

    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        db_cols = {c["name"] for c in insp.get_columns(table.name)}
        model_cols = {c.name for c in table.columns}
        assert db_cols == model_cols, f"{table.name}: DB {db_cols ^ model_cols}"


@integration
@pytest.mark.integration
def test_play_fixture_persists_then_rolls_back():
    from sqlalchemy import select

    from database import SessionLocal
    from match_engine import FixtureAlreadyPlayed, play_fixture
    from models import Fixture, FixtureStatus

    with SessionLocal() as db:
        fx = db.scalar(select(Fixture).where(Fixture.status == FixtureStatus.UNPLAYED).order_by(Fixture.id))
        assert fx is not None, "oynanmamış fikstür yok — python seed.py çalıştır"
        fx_id = fx.id  # rollback sonrasi nesne expire olur; id'yi simdiden al
        home, away = fx.home_team, fx.away_team
        before = (home.played, home.goals_for, away.played, away.goals_for)

        r = play_fixture(db, fx.id, seed=123, persist=True)
        db.flush()

        assert fx.status is FixtureStatus.PLAYED
        assert (fx.home_score, fx.away_score) == (r.home_score, r.away_score)
        assert home.played == before[0] + 1 and away.played == before[2] + 1
        assert home.goals_for == before[1] + r.home_score
        assert away.goals_for == before[3] + r.away_score
        assert home.played == home.won + home.drawn + home.lost

        with pytest.raises(FixtureAlreadyPlayed):
            play_fixture(db, fx.id, seed=1)

        db.rollback()  # veritabanini kirletme

    with SessionLocal() as db:
        fx2 = db.get(Fixture, fx_id)
        assert fx2.status is FixtureStatus.UNPLAYED
