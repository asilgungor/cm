"""
Taktik ve kadro secimi testleri (4. Asama).

Saf kurallar (tactics.py) sahte oyuncularla, motor sentetik kadrolarla,
entegrasyon testleri gercek PostgreSQL'e karsi (rollback ile) calisir.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from career_manager import bench_form_drift, idle_morale_penalty  # noqa: E402
from match_engine import MatchEngine, MatchTeam  # noqa: E402
from models import Position  # noqa: E402
from tactics import (  # noqa: E402
    FORMATIONS,
    MAX_BENCH,
    arrange_slots,
    formation_slots,
    pick_bench,
    pick_best_xi,
    selection_power,
    validate_lineup,
)
from tests.test_match_engine import make_player, make_team  # noqa: E402

# ---------------------------------------------------------------------------
# Sahte oyuncu (ORM Player'in taktik icin gereken yuzu)
# ---------------------------------------------------------------------------

@dataclass
class FakePlayer:
    id: int
    name: str
    position: Position
    overall_rating: int
    form: int = 50
    morale: int = 70
    injured_until_week: int = 0
    suspended_matches: int = 0

    def is_available(self, week: int) -> bool:
        return self.unavailability_reason(week) is None

    def unavailability_reason(self, week: int) -> str | None:
        if self.injured_until_week > week:
            return f"sakat, {self.injured_until_week}. haftada dönüyor"
        if self.suspended_matches > 0:
            return f"cezalı, {self.suspended_matches} maç"
        return None


def squad() -> list[FakePlayer]:
    spec = [(Position.GK, 2), (Position.DEF, 4), (Position.MID, 5), (Position.FWD, 4)]
    players, pid = [], 0
    for pos, n in spec:
        for i in range(n):
            pid += 1
            players.append(FakePlayer(pid, f"{pos.value}{i + 1}", pos, 80 - i * 2))
    return players


def by_role(xi: dict[int, Position]) -> dict[Position, int]:
    return {r: sum(1 for v in xi.values() if v is r) for r in Position}


# ---------------------------------------------------------------------------
# Secim gucu ve asistan
# ---------------------------------------------------------------------------

def test_selection_power_is_overall_at_neutral_and_scales_with_condition():
    assert selection_power(80, 50, 70) == pytest.approx(80.0)
    assert selection_power(80, 65, 85) > 80 > selection_power(80, 45, 60)


def test_formation_slots_and_counts():
    assert formation_slots("4-3-3").count(Position.FWD) == 3
    assert len(formation_slots("3-5-2")) == 11
    assert set(FORMATIONS) == {"4-4-2", "4-3-3", "3-5-2"}


def test_pick_best_xi_respects_formation_and_skips_unavailable():
    players = squad()
    players[0].injured_until_week = 5          # en iyi kaleci sakat
    players[2].suspended_matches = 1           # en iyi defans cezali
    xi = pick_best_xi(players, "4-3-3", week=1)
    assert len(xi) == 11
    assert by_role(xi) == {Position.GK: 1, Position.DEF: 4, Position.MID: 3, Position.FWD: 3}
    assert players[0].id not in xi and players[2].id not in xi
    assert xi[players[1].id] is Position.GK


def test_pick_best_xi_fills_missing_role_out_of_position():
    players = [p for p in squad() if p.name != "DEF4"]        # sadece 3 defans
    players[0].form = 50
    xi = pick_best_xi(players, "4-4-2", week=1)
    defs = [pid for pid, r in xi.items() if r is Position.DEF]
    assert len(defs) == 4
    filler = next(p for p in players if p.id in defs and p.position is not Position.DEF)
    assert filler.position is not Position.GK


def test_pick_best_xi_prefers_better_condition():
    players = squad()
    weak, strong = players[9], players[10]      # MID4 (74) ve MID5 (72)
    strong.form, strong.morale = 70, 90         # form/moral ile one gecer
    weak.form, weak.morale = 40, 50
    xi = pick_best_xi(players, "4-4-2", week=1)
    assert strong.id in xi and weak.id not in xi


def test_pick_bench_keeps_a_keeper_and_limit():
    players = squad()
    xi = pick_best_xi(players, "4-4-2", week=1)
    bench = pick_bench(players, xi, week=1)
    assert len(bench) <= MAX_BENCH and not set(bench) & set(xi)
    assert any(p.position is Position.GK for p in players if p.id in bench)


def test_arrange_slots_follows_formation_order():
    players = squad()
    xi = pick_best_xi(players, "3-5-2", week=1)
    slots = arrange_slots(players, "3-5-2", xi)
    assert [r for r, _ in slots] == formation_slots("3-5-2")
    assert all(p is not None for _, p in slots)
    assert slots[0][1].position is Position.GK


# ---------------------------------------------------------------------------
# Dogrulama
# ---------------------------------------------------------------------------

def test_validate_lineup_ok_and_bench():
    players = squad()
    xi = pick_best_xi(players, "4-4-2", week=1)
    bench = pick_bench(players, xi, week=1)
    check = validate_lineup(players, "4-4-2", 1, xi, bench)
    assert check.ok and not check.warnings


def test_validate_lineup_empty_xi_is_only_a_warning():
    check = validate_lineup(squad(), "4-4-2", 1, {}, [])
    assert check.ok and "asistan" in check.warnings[0]


def test_validate_lineup_errors():
    players = squad()
    xi = pick_best_xi(players, "4-4-2", week=1)

    ten = dict(list(xi.items())[:10])
    assert any("11 olmalı" in e for e in validate_lineup(players, "4-4-2", 1, ten, []).errors)

    wrong = dict(xi)
    fwd_id = next(pid for pid, r in wrong.items() if r is Position.FWD)
    wrong[fwd_id] = Position.MID                       # 5 MID / 1 FWD
    errs = validate_lineup(players, "4-4-2", 1, wrong, []).errors
    assert any("MID" in e for e in errs) and any("FWD" in e for e in errs)

    injured_id = next(pid for pid, r in xi.items() if r is Position.DEF)
    next(p for p in players if p.id == injured_id).injured_until_week = 9
    errs = validate_lineup(players, "4-4-2", 1, xi, []).errors
    assert any("ilk 11'de olamaz" in e and "sakat" in e for e in errs)

    check = validate_lineup(players, "4-4-2", 1, xi, [injured_id])
    assert any("hem ilk 11'de hem kulübede" in e for e in check.errors)

    too_many = [p.id for p in players if p.id not in xi]
    too_many += [999_001, 999_002, 999_003, 999_004]
    assert any("Kadroda olmayan" in e for e in validate_lineup(players, "4-4-2", 1, xi, too_many).errors)

    assert validate_lineup(players, "5-5-0", 1, xi, []).errors


def test_validate_lineup_out_of_position_is_warning():
    players = squad()
    xi = pick_best_xi(players, "4-4-2", week=1)
    mid_id = next(pid for pid, r in xi.items() if r is Position.MID)
    fwd_id = next(pid for pid, r in xi.items() if r is Position.FWD)
    xi[mid_id], xi[fwd_id] = Position.FWD, Position.MID
    check = validate_lineup(players, "4-4-2", 1, xi, [])
    assert check.ok and len(check.warnings) == 2


# ---------------------------------------------------------------------------
# Motor: dizilis carpanlari ve menajer tercihi
# ---------------------------------------------------------------------------

def _strengths(formation):
    team = make_team(1, "Ev", 80)
    team.formation = formation
    eng = MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    return {k: eng._team_strength(team, k) for k in ("attack", "midfield", "defense")}


def test_formation_style_shifts_balance():
    base, att, mid = _strengths((4, 4, 2)), _strengths((4, 3, 3)), _strengths((3, 5, 2))
    assert att["attack"] > base["attack"] and att["defense"] < base["defense"]
    assert mid["midfield"] > base["midfield"] and mid["defense"] < base["defense"]


def test_engine_uses_team_formation_over_config_default():
    team = make_team(1, "Ev", 80)
    team.formation = (3, 5, 2)
    MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    roles = [p.role for p in team.on_pitch]
    assert roles.count(Position.DEF) == 3 and roles.count(Position.MID) == 5 and roles.count(Position.FWD) == 2


def test_engine_applies_preferred_xi_and_autofills_missing():
    team = make_team(1, "Ev", 80)
    backup_gk = [p for p in team.players if p.position is Position.GK][1]
    backup_gk.overall = 60                                  # normalde secilmezdi
    a_mid = [p for p in team.players if p.position is Position.MID][4]
    preferred = {backup_gk.id: Position.GK, a_mid.id: Position.FWD, 999_999: Position.DEF}
    team.preferred_xi = preferred
    MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    assert team.keeper is backup_gk
    assert a_mid.on_pitch and a_mid.role is Position.FWD    # mevki disi
    assert team.player_count == 11
    assert any("#999999" in n for n in team.lineup_notes)
    assert any("Asistan" in n for n in team.lineup_notes)


def test_engine_out_players_are_last_resort_only():
    team = make_team(1, "Ev", 80)
    fwd = [p for p in team.players if p.position is Position.FWD][0]
    fwd.overall = 99
    fwd.matchday = False
    MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    assert not fwd.on_pitch and fwd not in team.bench

    short = MatchTeam(id=3, name="Eksik", reputation=60, players=make_team(3, "X", 80).players[:11])
    short.players[10].matchday = False                       # 11 kisiden biri kadro disi
    MatchEngine(short, make_team(4, "Y", 80), seed=0)
    assert short.player_count == 11 and any("kadro dışından" in n for n in short.lineup_notes)


def test_gradual_form_drift_and_idle_penalty():
    assert [bench_form_drift(70, w) for w in (1, 2, 3, 5, 9)] == [-2, -3, -4, -6, -6]
    assert bench_form_drift(52, 4) == -2 and bench_form_drift(30, 2) == 3
    assert idle_morale_penalty(2) == 0 and idle_morale_penalty(3) == -1


def test_make_player_helper_still_neutral():
    p = make_player(1, Position.MID, 80)
    assert selection_power(p.overall, p.form, p.morale) == pytest.approx(80.0)


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


def _manager(db, seed=1, **engine_cfg):
    from career_manager import CareerManager
    from match_engine import EngineConfig

    cm = CareerManager(db, seed=seed, engine_config=EngineConfig(**engine_cfg) if engine_cfg else None)
    if cm.season_finished:
        pytest.skip("Sezon bitmiş; 'python seed.py' ile sıfırla")
    return cm


def _my_match_team(report, team_id):
    r = report.user_result
    assert r is not None
    return r.home if r.home.id == team_id else r.away


@integration
@pytest.mark.integration
def test_formation_and_assistant_xi_reach_engine(db):
    # Ayni hafta kupa maci da var: oradaki sakatlik/ceza lig 11'ini degistirmesin
    cm = _manager(db, seed=4, base_injury=0.0, base_card=0.0)
    team = cm.find_team("Istanbul Lions")
    cm.set_user_team(team)
    cm.set_formation(team, "3-5-2")
    xi = cm.auto_lineup(team)
    assert cm.lineup_check(team).ok
    assert all(p.is_starter == (p.id in xi) for p in team.players)

    report = cm.play_week()
    mt = _my_match_team(report, team.id)
    assert mt.formation == (3, 5, 2)
    starters = {p.id: p.role for p in mt.players if p.entered_minute == 0}
    assert starters == xi                           # asistanin 11'i aynen sahaya cikti
    assert by_role(starters) == {Position.GK: 1, Position.DEF: 3, Position.MID: 5, Position.FWD: 2}
    assert not report.lineup_notes


@integration
@pytest.mark.integration
def test_manual_xi_reaches_engine_and_stats(db):
    from sqlalchemy import select

    from models import PlayerMatchStat

    # Ayni hafta kupa maci da var: oradaki sakatlik/ceza lig 11'ini degistirmesin
    cm = _manager(db, seed=6, base_injury=0.0, base_card=0.0)
    team = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(team)
    xi = cm.auto_lineup(team)
    first_gk = next(p for p in team.players if xi.get(p.id) is Position.GK)
    backup_gk = next(p for p in team.players if p.position is Position.GK and p.id != first_gk.id)

    new_xi = {pid: r for pid, r in xi.items() if pid != first_gk.id}
    new_xi[backup_gk.id] = Position.GK
    _, bench, _ = cm.lineup_of(team)
    check = cm.set_lineup(team, new_xi, [i for i in bench if i != backup_gk.id] + [first_gk.id])
    assert check.ok, check.errors

    report = cm.play_week()
    mt = _my_match_team(report, team.id)
    assert next(p for p in mt.players if p.id == backup_gk.id).entered_minute == 0
    assert next(p for p in mt.players if p.id == first_gk.id).entered_minute != 0
    db.flush()
    league_fx = next(fx for fx, r in report.results if team.id in (r.home.id, r.away.id))
    rows = {r.player_id: r for r in db.scalars(
        select(PlayerMatchStat).where(PlayerMatchStat.team_id == team.id, PlayerMatchStat.fixture_id == league_fx.id)
    )}
    assert rows[backup_gk.id].minutes > 0


@integration
@pytest.mark.integration
def test_set_lineup_rejects_unavailable_and_changes_nothing(db):
    from models import LineupStatus

    cm = _manager(db, seed=2)
    team = cm.find_team("Bosphorus Eagles")
    xi = cm.auto_lineup(team)
    victim_id = next(pid for pid, r in xi.items() if r is Position.MID)
    victim = next(p for p in team.players if p.id == victim_id)
    before = {p.id: (p.lineup_status, p.lineup_role) for p in team.players}

    victim.injured_until_week = cm.current_week + 2
    check = cm.set_lineup(team, xi, cm.lineup_of(team)[1])
    assert not check.ok and any(victim.name in e and "sakat" in e for e in check.errors)
    assert {p.id: (p.lineup_status, p.lineup_role) for p in team.players} == before
    assert cm.lineup_check(team).errors                 # ekran da ayni hatayi gosterir

    victim.suspended_matches = 0
    victim.injured_until_week = 0
    assert cm.set_lineup(team, xi, cm.lineup_of(team)[1]).ok
    assert victim.lineup_status is LineupStatus.XI


@integration
@pytest.mark.integration
def test_form_and_morale_loop_is_persisted(db):
    from career_manager import BAD_RATING, GOOD_RATING
    from models import Player

    cm = _manager(db, seed=9)
    checked = {"good": 0, "bad": 0, "loser": 0, "bench": 0}
    # Tek haftada dort durumun hepsi cikmayabilir (onceki modullerin biraktigi dunyaya ve kupa takvimine bagli):
    # en cok 4 hafta oynanir, her hafta ayni kurallar denetlenir, sayaclar birikir.
    for _week in range(4):
        before = {p.id: (p.form, p.morale, p.weeks_since_match)
                  for p in db.scalars(__import__("sqlalchemy").select(Player))}
        report = cm.play_week()

        db.flush()
        db.expire_all()          # bellekteki degerleri at; bundan sonrasi DB'den okunur

        # Ayni hafta kupa maci da oynayan takimlarin oyunculari iki mactan etkilenir; lig dongusu
        # yalnizca kupada olmayan takimlarda olculur (kupa etkisi test_tournament.py'de)
        cup_teams = {side.id for _fx, r in report.cup_results for side in (r.home, r.away)}
        for _fx, result in report.results:
            for team in (result.home, result.away):
                if team.id in cup_teams:
                    continue
                lost = team.stats.goals < (result.away if team is result.home else result.home).stats.goals
                for mp in team.players:
                    p = db.get(Player, mp.id)
                    f0, m0, w0 = before[p.id]
                    if mp.played:
                        assert p.match_rating_history[-1] == mp.rating and p.weeks_since_match == 0
                        if mp.rating >= GOOD_RATING and f0 < 100:
                            assert p.form > f0 and p.morale >= m0
                            checked["good"] += 1
                        if mp.rating < BAD_RATING and m0 > 0:
                            # 15G: BAD_RATING 6.0 -> 6.22. Esigin hemen altinda kalan bir not GALIBIYETLE
                            # birlesince perf (-4) + sonuc (+3) = -1 oluyor ve staff_rules.apply_training
                            # yuvarlamasi bunu 0'a indirebiliyor: yon korunur ama esitlik mumkun.
                            assert p.morale <= m0
                            checked["bad"] += 1
                        if lost and mp.rating < GOOD_RATING and m0 > 0:
                            assert p.morale < m0
                            checked["loser"] += 1
                    else:
                        assert p.weeks_since_match == w0 + 1
                        if f0 != 50:
                            assert abs(p.form - 50) < abs(f0 - 50)
                            checked["bench"] += 1
        if all(v > 0 for v in checked.values()) or cm.season_finished:
            break
    assert all(v > 0 for v in checked.values()), checked


@integration
@pytest.mark.integration
def test_idle_player_form_decays_gradually(db):
    from models import LineupStatus, Player

    cm = _manager(db, seed=12)
    team = cm.find_team("Karadeniz Storm")
    cm.set_user_team(team)
    xi = cm.auto_lineup(team)
    idle = max((p for p in team.players if p.id not in xi and p.position is not Position.GK), key=lambda p: p.overall_rating)
    idle.form, idle.weeks_since_match = 80, 0
    _, bench, _ = cm.lineup_of(team)
    assert cm.set_lineup(team, xi, [i for i in bench if i != idle.id]).ok
    assert idle.lineup_status is LineupStatus.OUT

    weeks = 0
    for _ in range(3):
        if cm.season_finished:
            break
        cm.play_week()
        weeks += 1
    if weeks < 3:
        pytest.skip("3 hafta kalmamış")

    p = db.get(Player, idle.id)
    if p.weeks_since_match != 3:
        pytest.skip("oyuncu son care olarak sahaya cikti")
    assert p.form == 80 - (2 + 3 + 4)
