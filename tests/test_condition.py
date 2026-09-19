"""
Dinamik kondisyon testleri.

Saf kurallar (fitness.py), motor (enerji kondisyondan baslar, yorulma, efor,
enerji zaman serisi, yorgunluk notu), taktik (rotasyon, uyari) veritabanisiz;
kalicilik ve kondisyon -> not -> form zinciri gercek PostgreSQL'e karsi
(rollback ile) test edilir.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitness  # noqa: E402
from match_engine import EngineConfig, MatchEngine, MatchPlayer  # noqa: E402
from models import Position  # noqa: E402
from tactics import (  # noqa: E402
    pick_bench,
    pick_best_xi,
    player_power,
    selection_power,
    validate_lineup,
)
from tests.test_match_engine import make_player, make_team  # noqa: E402
from tests.test_tactics import squad  # noqa: E402

# ---------------------------------------------------------------------------
# Saf kurallar (fitness.py)
# ---------------------------------------------------------------------------

def test_condition_bands_and_warn_threshold():
    assert fitness.CONDITION_WARN == 70
    assert [fitness.condition_band(c) for c in (100, 80, 79, 60, 59, 0)] == \
        ["good", "good", "warn", "warn", "low", "low"]
    assert fitness.clamp_condition(104.6) == 100 and fitness.clamp_condition(-3) == 0
    assert fitness.condition_of(SimpleNamespace()) == 100                  # alan yok
    assert fitness.condition_of(SimpleNamespace(condition=None)) == 100    # flush edilmemis ORM
    assert fitness.condition_of(SimpleNamespace(condition=62)) == 62


def test_fatigue_factor_is_single_source_of_truth():
    assert fitness.fatigue_factor(100) == pytest.approx(1.0)
    assert fitness.fatigue_factor(0) == pytest.approx(0.75)
    assert fitness.fatigue_factor(40) == pytest.approx(0.85)
    assert fitness.fatigue_factor(150) == pytest.approx(1.0) and fitness.fatigue_factor(-5) == pytest.approx(0.75)
    p = make_player(1, Position.MID, 80)
    p.energy = 40
    assert p.fatigue_factor == pytest.approx(fitness.fatigue_factor(40))


def test_stamina_decay_multiplier():
    assert fitness.stamina_decay_multiplier(None) == 1.0
    assert fitness.stamina_decay_multiplier(10) == pytest.approx(1.0)
    assert fitness.stamina_decay_multiplier(20) == pytest.approx(0.8)
    assert fitness.stamina_decay_multiplier(1) == pytest.approx(1.18)


def test_recover_condition_math_and_physio_ordering():
    assert fitness.recovery_rate(None) == pytest.approx(0.60)
    assert fitness.recovery_rate(10) == pytest.approx(0.75)
    assert fitness.recovery_rate(20) == pytest.approx(0.90)
    assert fitness.recover_condition(56, 10) == 89                 # 56 + 44 * 0.75
    assert fitness.recover_condition(40, None) == 76               # 40 + 60 * 0.60
    assert fitness.recover_condition(100, None) == 100
    assert fitness.recover_condition(99.8, 20) == 100               # en fazla 100

    none, poor, good = (fitness.recover_condition(30, r) for r in (None, 1, 20))
    assert none < poor < good < 100


def test_midweek_recovery_is_half_the_weekly_rate():
    assert fitness.MIDWEEK_RECOVERY_SHARE == 0.5
    assert fitness.recover_condition(56, 10, share=0.5) == 72      # 56 + 44 * 0.75 * 0.5
    assert fitness.recover_condition(56, 10, share=1.0) == fitness.recover_condition(56, 10)
    assert fitness.recover_condition(56, 10, share=0.5) < fitness.recover_condition(56, 10)
    assert fitness.recover_condition(40, None, share=0) == 40


def test_fatigue_rating_penalty_curve():
    assert fitness.fatigue_rating_penalty(100) == 0
    assert fitness.fatigue_rating_penalty(fitness.TIRED_RATING_THRESHOLD) == 0
    assert fitness.fatigue_rating_penalty(20) == pytest.approx(0.45)
    assert fitness.fatigue_rating_penalty(0) == pytest.approx(1.05)
    values = [fitness.fatigue_rating_penalty(e) for e in range(100, -1, -5)]
    assert values == sorted(values)


# ---------------------------------------------------------------------------
# Motor: baslangic enerjisi ve guc
# ---------------------------------------------------------------------------

def _orm_like(pid: int, condition, fm_attributes=None, **kw) -> SimpleNamespace:
    base = dict(
        id=pid, name=f"O{pid}", position=Position.MID, age=26, overall_rating=80, pace=80,
        shooting=70, passing=83, defending=65, dribbling=80, goalkeeping=30, form=50, morale=70,
        condition=condition, fm_attributes=fm_attributes if fm_attributes is not None else {},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_energy_starts_from_db_condition():
    mp = MatchPlayer.from_orm(_orm_like(1, 62, {"stamina": 15}))
    assert mp.condition == 62 and mp.energy == 62.0 and mp.stamina == 15.0

    fresh = MatchPlayer.from_orm(_orm_like(2, None))                 # flush edilmemis: tam kondisyon
    assert fresh.condition == 100 and fresh.energy == 100.0 and fresh.stamina is None
    legacy = SimpleNamespace(**{k: v for k, v in vars(_orm_like(3, 70)).items()
                                if k not in ("condition", "fm_attributes")})
    assert MatchPlayer.from_orm(legacy).energy == 100.0

    team = make_team(1, "Ev", 80)
    for p in team.players:
        p.condition = 90
    MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    assert all(p.energy == 90.0 for p in team.players)
    assert all(p.energy_log == [(0, 90)] for p in team.on_pitch)
    assert all(p.energy_log == [] for p in team.bench)


def test_default_player_behaves_exactly_as_before():
    # 13B yorgunluk mekanizmasi (dayaniklilik verisi yok = notr): ozellik modeli KAPALI. 14B'de dayaniklilik ve
    # caliskanlik sayfadan gelir (tests/test_attribute_model.py, supurme: kanit/14B_supurme.txt).
    team = make_team(1, "Ev", 80)
    MatchEngine(team, make_team(2, "Dep", 80), seed=0, config=EngineConfig(attribute_model=False))
    p = team.on_pitch[0]
    assert p.condition == 100 and p.energy == 100.0 and p.stamina is None
    assert p.effective_power == pytest.approx(80.0) and p.selection_power == pytest.approx(80.0)
    assert p.decay_multiplier == pytest.approx(p.stamina_multiplier)


def _engine_with_forward(condition: int) -> tuple[MatchEngine, MatchPlayer]:
    team = make_team(1, "Ev", 80)
    fwd = next(p for p in team.players if p.position is Position.FWD)
    fwd.condition = condition
    team.preferred_xi = {fwd.id: Position.FWD}              # yorgun da olsa ilk 11'de
    eng = MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    assert fwd.on_pitch
    return eng, fwd


def test_low_condition_lowers_strength_and_power():
    eng_fresh, fresh = _engine_with_forward(100)
    eng_tired, tired = _engine_with_forward(40)
    for kind in ("attack", "midfield", "defense"):
        s_fresh, s_tired = eng_fresh._player_strength(fresh, kind), eng_tired._player_strength(tired, kind)
        assert s_tired < s_fresh
        assert s_tired / s_fresh == pytest.approx(fitness.fatigue_factor(40))
    assert tired.effective_power < fresh.effective_power == pytest.approx(80.0)
    assert tired.selection_power < fresh.selection_power
    assert eng_tired._team_strength(eng_tired.home, "attack") < eng_fresh._team_strength(eng_fresh.home, "attack")


def test_auto_lineup_rests_exhausted_player():
    team = make_team(1, "Ev", 80)
    gk1, gk2 = [p for p in team.players if p.position is Position.GK]
    gk1.condition = 50
    MatchEngine(team, make_team(2, "Dep", 80), seed=0)
    assert team.keeper is gk2 and gk1 in team.bench


# ---------------------------------------------------------------------------
# Motor: yorulma hizi ve efor
# ---------------------------------------------------------------------------

def _drop_after_one_minute(eng: MatchEngine, minute: int = 10) -> dict[int, float]:
    for p in eng.home.on_pitch + eng.away.on_pitch:
        p.energy = 100.0
    eng.minute = minute
    eng._apply_fatigue()
    return {p.id: 100.0 - p.energy for p in eng.home.on_pitch + eng.away.on_pitch}


def test_decay_faster_for_older_and_low_stamina():
    # 13B yorgunluk mekanizmasi (dayaniklilik verisi yok = notr): ozellik modeli KAPALI. 14B'de dayaniklilik ve
    # caliskanlik sayfadan gelir (tests/test_attribute_model.py, supurme: kanit/14B_supurme.txt).
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0,
                      config=EngineConfig(attribute_model=False))
    young, old, weak, strong = [p for p in eng.home.on_pitch if p.role is Position.MID]
    old.age = 34
    weak.stamina, strong.stamina = 4, 18
    drop = _drop_after_one_minute(eng)
    assert drop[old.id] > drop[young.id]
    assert drop[old.id] / drop[young.id] == pytest.approx(old.stamina_multiplier)
    assert drop[weak.id] > drop[young.id] > drop[strong.id]
    assert drop[weak.id] / drop[young.id] == pytest.approx(fitness.stamina_decay_multiplier(4))


def test_pressing_trailing_team_tires_faster():
    # 13B yorgunluk mekanizmasi (dayaniklilik verisi yok = notr): ozellik modeli KAPALI. 14B'de dayaniklilik ve
    # caliskanlik sayfadan gelir (tests/test_attribute_model.py, supurme: kanit/14B_supurme.txt).
    cfg = EngineConfig(attribute_model=False)
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, config=cfg)
    home_mid = next(p for p in eng.home.on_pitch if p.role is Position.MID)
    away_mid = next(p for p in eng.away.on_pitch if p.role is Position.MID)

    eng.away.stats.goals = 1                                    # ev sahibi 0-1 geride
    early = _drop_after_one_minute(eng, minute=cfg.desperation_from_minute - 1)
    assert early[home_mid.id] == pytest.approx(early[away_mid.id])
    late = _drop_after_one_minute(eng, minute=cfg.desperation_from_minute + 1)
    assert late[home_mid.id] / late[away_mid.id] == pytest.approx(cfg.trailing_fatigue_multiplier)

    eng.away.stats.goals = 1 + cfg.desperation_max_deficit      # mac bitmis: kimse bastirmaz
    hopeless = _drop_after_one_minute(eng, minute=cfg.desperation_from_minute + 1)
    assert hopeless[home_mid.id] == pytest.approx(hopeless[away_mid.id])


def test_shooter_assister_and_fouler_lose_extra_energy():
    # 13A: duran toplar artik varsayilan ACIK; rng.random()=0.0 ile pozisyon cekilisinin en alt
    # dilimi penaltiya duserdi. Bu test AKAN OYUNDAKI efor maliyetini sinar, bu yuzden kapatilir.
    # straight_red_share_v2: discipline_v2 acikken direkt kirmizi payi oradan okunur.
    cfg = EngineConfig(straight_red_share=0.0, straight_red_share_v2=0.0, set_pieces=False)
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0, config=cfg)
    shooter = next(p for p in eng.home.on_pitch if p.role is Position.FWD)
    eng.rng.random = lambda: 0.0                                # her olasilik gerceklesir -> gol + asist
    eng._weighted_choice = lambda players, weight: shooter if shooter in players else players[0]
    eng.minute = 30

    eng._attack(eng.home, eng.away)
    assert shooter.goals == 1 and shooter.energy == pytest.approx(100.0 - cfg.shot_energy_cost)
    assister = next(p for p in eng.home.players if p.assists)
    assert assister.energy == pytest.approx(100.0 - cfg.assist_energy_cost)
    untouched = [p for p in eng.home.on_pitch if p not in (shooter, assister)]
    assert all(p.energy == 100.0 for p in untouched)

    fouler = next(p for p in eng.away.on_pitch if p.role is Position.DEF)
    eng._weighted_choice = lambda players, weight: fouler
    eng._discipline(eng.home, eng.away)
    assert fouler.yellow_cards == 1 and fouler.energy == pytest.approx(100.0 - cfg.foul_energy_cost)


# ---------------------------------------------------------------------------
# Motor: enerji zaman serisi
# ---------------------------------------------------------------------------

def _non_increasing(values: list[int]) -> bool:
    return all(a >= b for a, b in zip(values, values[1:], strict=False))


def test_energy_log_populated_and_monotonic_within_halves():
    full_90_checked = subs_checked = 0
    for seed in range(8):
        r = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed).simulate()
        for team in (r.home, r.away):
            for p in team.players:
                if not p.played:
                    assert p.energy_log == []
                    continue
                minutes = [m for m, _ in p.energy_log]
                assert minutes == sorted(set(minutes)), p.energy_log      # artan, tekrarsiz
                assert minutes[0] == p.entered_minute and minutes[-1] == p.left_minute
                assert p.energy_log[-1][1] == round(p.energy)
                first = [e for m, e in p.energy_log if m <= 45]
                second = [e for m, e in p.energy_log if m > 45]
                assert _non_increasing(first) and _non_increasing(second), p.energy_log
                if p.entered_minute == 0:
                    assert p.energy_log[0] == (0, 100)
                    if p.left_minute == 90:
                        assert minutes == list(range(0, 91, 5))
                        full_90_checked += 1
                else:
                    assert p.energy_log[0] == (p.entered_minute, 100)     # yedek taze girer
                    subs_checked += 1
    assert full_90_checked > 50 and subs_checked > 0


def test_half_time_recovery_visible_in_log():
    r = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=2).simulate()
    gk = next(p for p in r.home.players if p.position is Position.GK and p.entered_minute == 0
              and p.left_minute == 90)
    log = dict(gk.energy_log)
    # kaleci yavas yorulur: uzatma + 5 dk kaybi (<2) devre arasi +6 ile asilir
    assert log[50] > log[45]


# ---------------------------------------------------------------------------
# Motor: yorgunluk mac notunu dusurur (kontrollu senaryo)
# ---------------------------------------------------------------------------

def _rating_with_end_energy(end_energy: float, entered: int = 0, left: int = 90) -> float:
    eng = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=0)
    eng.home.stats.goals, eng.away.stats.goals = 1, 1
    p = next(x for x in eng.home.on_pitch if x.role is Position.MID)
    p.goals, p.shots_on_target = 1, 2
    p.entered_minute, p.left_minute, p.energy = entered, left, end_energy
    eng._compute_ratings()
    return p.rating


def test_low_end_energy_lowers_rating():
    fresh = _rating_with_end_energy(80)
    assert fresh == pytest.approx(7.2)                          # 6 + 1 gol + 2 x 0.1 isabet
    assert _rating_with_end_energy(fitness.TIRED_RATING_THRESHOLD) == pytest.approx(fresh)
    exhausted = _rating_with_end_energy(0)
    assert exhausted == pytest.approx(fresh - fitness.fatigue_rating_penalty(0), abs=0.051)
    assert exhausted < _rating_with_end_energy(20) < fresh
    # 20 dakikadan az oynayan yorgunluk cezasi almaz (not zaten sonumlenir)
    assert _rating_with_end_energy(0, entered=80, left=90) == _rating_with_end_energy(80, entered=80, left=90)


def test_tired_side_rates_lower_over_many_matches():
    """Ayni tohumlar, ayni kadrolar: tek fark ev sahibinin kondisyonu (tum kadro 45)."""
    def home_avg(condition: int) -> float:
        rated = []
        for seed in range(12):
            home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 80)
            for p in home.players:
                p.condition = condition
            r = MatchEngine(home, away, seed=seed).simulate()
            rated += [p.rating for p in r.home.players if p.minutes_played >= 20]
        return sum(rated) / len(rated)

    assert home_avg(45) < home_avg(100) - 0.3


# ---------------------------------------------------------------------------
# Taktik: rotasyon ve uyari
# ---------------------------------------------------------------------------

def test_selection_power_signature_compatible():
    assert selection_power(80, 50, 70) == pytest.approx(80.0)
    assert selection_power(80, 50, 70, 100) == pytest.approx(80.0)
    assert selection_power(80, 50, 70, 40) == pytest.approx(80.0 * fitness.fatigue_factor(40))
    legacy = squad()[0]                                          # condition alani yok
    assert player_power(legacy) == pytest.approx(selection_power(legacy.overall_rating, 50, 70))


def test_assistant_rotates_tired_player():
    players = squad()
    mid1 = next(p for p in players if p.name == "MID1")          # en iyi orta saha (80)
    mid5 = next(p for p in players if p.name == "MID5")          # en zayif orta saha (72)
    rested = pick_best_xi(players, "4-4-2", week=1)
    assert mid1.id in rested and mid5.id not in rested

    mid1.condition = 40                                          # 80 x 0.85 = 68 < 72
    mid5.condition = 100
    assert player_power(mid5) > player_power(mid1)
    xi = pick_best_xi(players, "4-4-2", week=1)
    assert mid5.id in xi and mid1.id not in xi
    assert mid1.id in pick_bench(players, xi, week=1)            # dinlenir ama kulubede


def test_validate_lineup_warns_for_low_condition():
    players = squad()
    xi = pick_best_xi(players, "4-4-2", week=1)
    starters = [p for p in players if p.id in xi]
    starters[0].condition = 62
    starters[1].condition = fitness.CONDITION_WARN               # sinirda: uyari yok
    check = validate_lineup(players, "4-4-2", 1, xi, [])
    assert check.ok
    assert check.warnings == [f"{starters[0].name} kondisyonu düşük (%62)."]

    bench_only = next(p for p in players if p.id not in xi)
    bench_only.condition = 10                                    # ilk 11'de degil: uyari yok
    assert validate_lineup(players, "4-4-2", 1, xi, [bench_only.id]).warnings == check.warnings


# ---------------------------------------------------------------------------
# Entegrasyon (PostgreSQL, rollback)
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


def _manager(db, seed: int):
    from career_manager import CareerManager

    cm = CareerManager(db, seed=seed)
    if cm.season_finished:
        pytest.skip("Sezon bitmiş; test veritabanı yeniden kurulmalı")
    return cm


@integration
@pytest.mark.integration
def test_schema_default_and_check_constraint(db):
    from sqlalchemy import func, select, text, update
    from sqlalchemy.exc import IntegrityError

    from models import Player

    assert db.scalar(select(func.count()).select_from(Player).where(Player.condition != 100)) == 0
    default = db.scalar(text(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name = 'players' AND column_name = 'condition'"
    ))
    assert default is not None and "100" in default

    pid = db.scalar(select(Player.id).limit(1))
    for bad in (101, -1):
        with pytest.raises(IntegrityError, match="ck_player_condition"), db.begin_nested():
            db.execute(update(Player).where(Player.id == pid).values(condition=bad))


@integration
@pytest.mark.integration
def test_fm_seed_path_starts_full_and_carries_stamina(db):
    from sqlalchemy import delete, select

    import fm_parser
    import seed
    from match_engine import build_match_team
    from models import Fixture, GameState, League, Player, PlayerMatchStat, Staff, Team

    for model in (PlayerMatchStat, Fixture, GameState, Staff, Player, Team, League):
        db.execute(delete(model))
    db.flush()
    sample = Path(__file__).resolve().parent.parent / "data" / "fm" / "sample_fm_export.html"
    world = seed.build_fm_world(fm_parser.parse_files([sample]), rng_seed=2026)
    seed.write_world(db, world, rng_seed=2026)
    db.flush()
    db.expire_all()

    players = db.scalars(select(Player)).all()
    assert players and all(p.condition == 100 for p in players)
    fm_player = next(p for p in players if p.data_source == "fm" and "stamina" in p.fm_attributes)
    mt = build_match_team(fm_player.team, True, current_week=1)
    mp = next(p for p in mt.players if p.id == fm_player.id)
    assert mp.stamina == fm_player.fm_attributes["stamina"] and mp.energy == 100.0


@integration
@pytest.mark.integration
def test_play_week_persists_recovered_condition(db):
    from models import Player, StaffRole

    cm = _manager(db, seed=31)
    week = cm.current_week
    target = next(p for p in cm.find_team("London Gunners").players if p.is_available(week))
    target.injured_until_week = week + 2                        # sakat: oynamaz ama 100 olmali
    target.condition = 55
    db.flush()

    physio = {t.id: cm._staff_rating(t, StaffRole.PHYSIO, "physiotherapy") for t in cm.teams()}
    report = cm.play_week()
    expected: dict[int, int] = {}
    tired_starters = 0
    for _fx, result in report.results:
        for team in (result.home, result.away):
            for mp in team.players:
                if mp.played:
                    # 10. Asama: 32+ yas daha yavas toparlanir
                    expected[mp.id] = fitness.recover_condition(mp.energy, physio[team.id], age=mp.age)
                    if mp.minutes_played >= 45 and mp.role is not Position.GK:
                        assert expected[mp.id] < 100, (mp.name, mp.energy)
                        tired_starters += 1
                else:
                    expected[mp.id] = 100
            for mp, _reason in team.unavailable:
                expected[mp.id] = 100
    assert expected[target.id] == 100
    assert tired_starters >= 8 * 2 * len(report.results)

    db.flush()
    db.expire_all()                                             # bundan sonrasi DB'den okunur
    for pid, value in expected.items():
        assert db.get(Player, pid).condition == value, pid


@integration
@pytest.mark.integration
def test_start_new_season_resets_condition(db):
    from sqlalchemy import func, select, update

    from models import Fixture, FixtureStatus, Player

    cm = _manager(db, seed=3)
    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    tired = db.scalars(select(Player).limit(5)).all()
    for p in tired:
        p.condition = 40
    db.flush()
    assert cm.season_finished

    cm.start_new_season()
    db.flush()
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Player).where(Player.condition != 100)) == 0


def _user_week_with_key_condition(condition: int) -> dict:
    """Ayri bir oturumda (rollback) kullanici takiminin kilit defansini `condition` ile oynatir."""
    from database import SessionLocal
    from models import Player

    session = SessionLocal()
    try:
        cm = _manager(session, seed=77)
        # Devler Arenasi'na katilmayan takim: hafta ici kupa maci kondisyonu degistirmesin
        team = cm.find_team("Karadeniz Storm")
        assert not cm.tournaments.is_participant(cm.tournaments.ensure(), team.id)
        cm.set_user_team(team)
        cm.set_formation(team, "4-4-2")
        xi = cm.auto_lineup(team)                                 # kondisyon 100 iken kurulur: iki kosuda ayni 11
        key = max((p for p in team.players if xi.get(p.id) is Position.DEF),
                  key=lambda p: p.overall_rating)
        key.condition = condition
        form_before, morale_before = key.form, key.morale
        session.flush()

        report = cm.play_week()
        result = report.user_result
        mine = result.home if result.home.id == team.id else result.away
        mp = next(p for p in mine.players if p.id == key.id)
        session.flush()
        session.expire_all()
        persisted = session.get(Player, key.id)
        return {
            "id": key.id, "starter": mp.entered_minute == 0, "minutes": mp.minutes_played,
            "energy": mp.energy, "rating": mp.rating, "log": list(mp.energy_log),
            "form_delta": persisted.form - form_before, "morale_delta": persisted.morale - morale_before,
            "condition_after": persisted.condition,
        }
    finally:
        session.rollback()
        session.close()


@integration
@pytest.mark.integration
def test_low_condition_chain_rating_and_form():
    fresh = _user_week_with_key_condition(100)
    tired = _user_week_with_key_condition(35)

    assert fresh["id"] == tired["id"]
    assert fresh["starter"] and tired["starter"]
    assert tired["log"][0] == (0, 35) and fresh["log"][0] == (0, 100)
    assert tired["minutes"] >= 20
    assert tired["energy"] < fitness.TIRED_RATING_THRESHOLD <= fresh["energy"]
    assert tired["rating"] < fresh["rating"]
    # zincir: dusuk not -> daha kotu form (mevcut form dongusu)
    assert tired["form_delta"] < fresh["form_delta"]
    assert tired["condition_after"] < fresh["condition_after"]
