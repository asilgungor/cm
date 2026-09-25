"""
Altyapi akademisi, genc girisi ve gelisim ENTEGRASYON testleri (10. Asama).

Gercek PostgreSQL'e karsi calisir (conftest test veritabani). Her test kendi transaction'ini
rollback eder. Eski kayit testi AYRI bir kariyer semasinda (career_context) calisir ve semayi
sonunda siler: 'public' semasindaki test dunyasina ve diger testlere dokunmaz.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import development  # noqa: E402
import staff as staff_rules  # noqa: E402
import transfers  # noqa: E402
import youth  # noqa: E402
from career_manager import (  # noqa: E402
    ACADEMY_CAPACITY,
    ACADEMY_MAX_AGE,
    ACADEMY_OVERAGE_SLOTS,
    AI_MIN_SENIOR_SQUAD,
    SENIOR_SQUAD_MAX,
    YOUTH_INTAKE_SIZE,
    AcademyError,
    CareerManager,
    DevelopmentNote,
    WeekReport,
    YouthIntakeNote,
)
from match_engine import EngineConfig, build_match_team  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    FixtureStatus,
    GameMode,
    GameState,
    LineupStatus,
    Player,
    PlayerMatchStat,
    Position,
    StaffRole,
    Team,
)
from name_pools import YOUTH_NAME_POOLS  # noqa: E402
from ratings import ENGINE_ATTRIBUTES  # noqa: E402
from transfers import TransferError  # noqa: E402


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

QUIET = dict(base_injury=0.0, base_card=0.0)          # sakatlik/kart yok: kadrolar sabit kalsin


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, seed=11, **cfg) -> CareerManager:
    cm = CareerManager(db, seed=seed, engine_config=EngineConfig(**cfg) if cfg else None)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    return cm


def _academy_count(db, team_id: int) -> int:
    db.flush()
    return db.scalar(select(func.count()).select_from(Player).where(
        Player.team_id == team_id, Player.in_academy.is_(True))) or 0


def _add_academy_player(cm: CareerManager, team: Team, age: int = 17, overall: int = 50,
                        potential: int = 60, position: Position = Position.MID) -> Player:
    """Testin kontrol ettigi akademi oyuncusu (youth.YouthSpec -> CareerManager._academy_player)."""
    spec = youth.YouthSpec(
        name=f"Test Genç {team.id}-{cm.db.scalar(select(func.count()).select_from(Player))}",
        age=age, position=position, overall=overall, potential=potential,
        attributes=dict.fromkeys(ENGINE_ATTRIBUTES, overall),
    )
    player = cm._academy_player(team, spec)
    cm.db.add(player)
    cm.db.flush()
    return player


def _finish_season_quickly(db, cm: CareerManager) -> None:
    """Maclari oynamadan sezonu bitmis say (turnuva henuz kurulmadiysa kupa da bitmis sayilir)."""
    from sqlalchemy import update

    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    assert cm.season_finished


# ===========================================================================
# 1) SEED: akademiler, potansiyeller, A takimlar degismedi
# ===========================================================================

def test_seeded_world_has_academies_and_potentials(db):
    cm = _manager(db)
    state = db.get(GameState, 1)
    assert state.academy_seeded and state.last_youth_intake_season is None
    lo, hi = youth.INITIAL_ACADEMY_SIZE
    for team in cm.teams():
        assert len(team.players) == 15                               # A takim eskisi gibi tam 15
        assert all(not p.in_academy for p in team.players)
        academy = cm.academy_players(team)
        assert lo <= len(academy) <= hi
        assert [p.id for p in academy] == [p.id for p in team.academy_players]
        assert 1 <= team.youth_facilities <= 20
        for p in academy:
            assert p.in_academy and p.data_source == "academy" and 16 <= p.age <= 19
            assert p.lineup_status is LineupStatus.OUT and p.market_value > 0 and p.current_wage > 0
    no_potential = db.scalar(select(func.count()).select_from(Player).where(Player.potential_rating.is_(None)))
    below = db.scalar(select(func.count()).select_from(Player)
                      .where(Player.potential_rating < Player.overall_rating))
    assert no_potential == 0 and below == 0
    # Akademi id'leri A takimlardan sonra: kidemli oyuncu id'leri kaymadi
    max_senior = db.scalar(select(func.max(Player.id)).where(Player.in_academy.is_(False)))
    min_academy = db.scalar(select(func.min(Player.id)).where(Player.in_academy.is_(True)))
    assert min_academy > max_senior
    assert cm.ensure_youth_setup() == []                             # yeni dunyada yapilacak bir sey yok


def test_matches_never_include_academy_players(db):
    cm = _manager(db, seed=3)
    team = cm.find_team("Istanbul Lions")
    academy_ids = {p.id for p in cm.academy_players(team)}
    match_team = build_match_team(team, True, cm.current_week)
    assert academy_ids and not academy_ids & {mp.id for mp in match_team.players}
    cm.tournaments.draw_all()
    cm.play_week()
    db.flush()
    academy_rows = db.scalar(
        select(func.count()).select_from(PlayerMatchStat).join(Player, Player.id == PlayerMatchStat.player_id)
        .where(Player.in_academy.is_(True))
    )
    assert academy_rows == 0
    assert db.scalar(select(func.count()).select_from(PlayerMatchStat)) > 0


# ===========================================================================
# 2) ESKI KAYIT: sutunlar yok -> upgrade_schema -> ensure_youth_setup
# ===========================================================================

OLD_SAVE_SCHEMA = "test_youth_old_save"
NEW_COLUMNS = (
    ("players", "potential_rating"), ("players", "in_academy"), ("players", "development_progress"),
    ("teams", "youth_facilities"), ("game_state", "academy_seeded"), ("game_state", "last_youth_intake_season"),
)


def _snapshot(session) -> tuple:
    """Doldurmanin icerigi. Akademi oyuncularinin id'si karsilastirilmaz (rollback sekansi geri almaz)."""
    session.flush()
    seniors = tuple(session.execute(
        select(Player.id, Player.potential_rating).where(Player.in_academy.is_(False)).order_by(Player.id)).all())
    academy = tuple(sorted(session.execute(
        select(Player.team_id, Player.name, Player.age, Player.position, Player.overall_rating,
               Player.potential_rating).where(Player.in_academy.is_(True))).all()))
    teams = tuple(session.execute(select(Team.id, Team.youth_facilities).order_by(Team.id)).all())
    return seniors, academy, teams


def test_old_save_is_upgraded_and_backfilled_idempotently():
    import database
    import seed
    from database import SessionLocal

    with database.career_context(OLD_SAVE_SCHEMA):
        database.drop_career_schema(OLD_SAVE_SCHEMA)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            # Eski kayit: akademi yok, 10. Asama sutunlari yok
            with database.career_connection() as conn:
                conn.exec_driver_sql("DELETE FROM players WHERE in_academy")
                for table, column in NEW_COLUMNS:
                    conn.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
            problems = database.schema_problems()
            assert {f"eksik sütun: {t}.{c}" for t, c in NEW_COLUMNS} <= set(problems)

            applied = database.upgrade_schema()
            assert {f"sütun eklendi: {t}.{c}" for t, c in NEW_COLUMNS} <= set(applied)
            assert database.upgrade_schema() == [] and database.schema_problems() == []

            session = SessionLocal()
            try:
                seniors_before = session.scalar(select(func.count()).select_from(Player))
                assert seniors_before == 360
                assert session.scalar(select(func.count()).select_from(Player)
                                      .where(Player.potential_rating.isnot(None))) == 0
                cm = CareerManager(session, seed=3)
                messages = cm.ensure_youth_setup()
                assert len(messages) == 3
                assert "360 oyuncuya potansiyel" in messages[0] and "24 kulübe" in messages[1]
                assert "başlangıç akademisi" in messages[2]
                first = _snapshot(session)
                session.rollback()                               # ayni tohum + id: ayni doldurma
                cm = CareerManager(session, seed=3)
                assert len(cm.ensure_youth_setup()) == 3
                assert _snapshot(session) == first
                session.commit()
            finally:
                session.close()

            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=3)
                assert cm.ensure_youth_setup() == []             # idempotent
                assert _snapshot(session) == first
                assert cm.state.academy_seeded
                lo, hi = youth.INITIAL_ACADEMY_SIZE
                for team in cm.teams():
                    assert len(team.players) == 15
                    assert lo <= len(cm.academy_players(team)) <= hi
                    assert 1 <= team.youth_facilities <= 20
                for p in session.scalars(select(Player)):
                    assert p.potential_rating >= p.overall_rating and p.development_progress == 0
                    if p.age >= 28 and not p.in_academy:
                        assert p.potential_rating == p.overall_rating
                expected = {p.id: CareerManager._backfill_potential(p)
                            for p in session.scalars(select(Player).where(Player.in_academy.is_(False)))}
                assert expected == {p.id: p.potential_rating
                                    for p in session.scalars(select(Player).where(Player.in_academy.is_(False)))}
            finally:
                session.close()
        finally:
            database.drop_career_schema(OLD_SAVE_SCHEMA)


def test_ensure_youth_setup_backfills_nulls_in_place(db):
    cm = _manager(db)
    victims = list(db.scalars(select(Player).order_by(Player.id).limit(5)))
    team = cm.find_team("Merseyside Reds")
    for p in victims:
        p.potential_rating = None
    team.youth_facilities = None
    db.flush()
    messages = cm.ensure_youth_setup()
    assert messages == ["5 oyuncuya potansiyel atandı.", "1 kulübe altyapı tesisi puanı verildi."]
    assert all(p.potential_rating == CareerManager._backfill_potential(p) for p in victims)
    assert 1 <= team.youth_facilities <= 20
    assert cm.ensure_youth_setup() == []


# ===========================================================================
# 3) A TAKIM <-> AKADEMI
# ===========================================================================

def test_promote_and_demote_rules(db):
    cm = _manager(db)
    team = cm.find_team("Istanbul Lions")
    other = cm.find_team("London Gunners")
    cm.set_user_team(team)
    xi = cm.auto_lineup(team)

    prospect = cm.academy_players(team)[0]
    cm.promote_to_senior(team, prospect)
    assert not prospect.in_academy and prospect.lineup_status is LineupStatus.BENCH
    assert prospect in team.players and prospect.id not in {p.id for p in cm.academy_players(team)}
    assert len(team.players) == 16
    with pytest.raises(AcademyError, match="zaten A takım"):
        cm.promote_to_senior(team, prospect)
    with pytest.raises(AcademyError, match="oyuncusu değil"):
        cm.send_to_academy(team, other.players[0])
    with pytest.raises(AcademyError, match="oyuncusu değil"):
        cm.promote_to_senior(other, cm.academy_players(team)[0])

    # Ilk 11'deki oyuncu akademiye inince kadrodan cikar
    starter = next(p for p in team.players if p.id in xi and p.position is not Position.GK)
    cm.send_to_academy(team, starter)
    assert starter.in_academy and starter.lineup_status is LineupStatus.OUT and starter.lineup_role is None
    assert starter not in team.players and starter.id not in cm.lineup_of(team)[0]
    with pytest.raises(AcademyError, match="zaten akademide"):
        cm.send_to_academy(team, starter)

    # Kaleci kurali: A takimda 2 kaleci kalmali
    keeper = next(p for p in team.players if p.position is Position.GK)
    assert sum(p.position is Position.GK for p in team.players) == 2
    with pytest.raises(AcademyError, match="kaleci"):
        cm.send_to_academy(team, keeper)

    # SQUAD_FLOOR: A takim 13'un altina inemez
    outfield = [p for p in team.players if p.position is not Position.GK]
    while len(team.players) > transfers.SQUAD_FLOOR:
        cm.send_to_academy(team, outfield.pop())
    assert len(team.players) == transfers.SQUAD_FLOOR
    with pytest.raises(AcademyError, match=str(transfers.SQUAD_FLOOR)):
        cm.send_to_academy(team, outfield.pop())


def test_academy_capacity_overage_and_senior_max(db):
    cm = _manager(db)
    team = cm.find_team("Kadıköy Canaries")
    seniors = sorted(team.players, key=lambda p: p.age)
    veteran = next(p for p in seniors[::-1] if p.position is not Position.GK)
    youngster = next(p for p in seniors if p.position is not Position.GK)

    # Yas ustu kontenjani (22+ en fazla 3)
    for _ in range(ACADEMY_OVERAGE_SLOTS - sum(p.age > ACADEMY_MAX_AGE for p in cm.academy_players(team))):
        _add_academy_player(cm, team, age=23)
    assert veteran.age > ACADEMY_MAX_AGE
    with pytest.raises(AcademyError, match="yaş üstü"):
        cm.send_to_academy(team, veteran)

    # Kapasite 20
    while _academy_count(db, team.id) < ACADEMY_CAPACITY:
        _add_academy_player(cm, team, age=17)
    if youngster.age > ACADEMY_MAX_AGE:
        youngster.age = 20
    with pytest.raises(AcademyError, match="Akademi dolu"):
        cm.send_to_academy(team, youngster)

    # A takim en fazla 25
    candidates = [p for p in cm.academy_players(team) if p.age <= ACADEMY_MAX_AGE]
    while len(team.players) < SENIOR_SQUAD_MAX:
        cm.promote_to_senior(team, candidates.pop())
    assert len(team.players) == SENIOR_SQUAD_MAX
    with pytest.raises(AcademyError, match="A takım kadrosu dolu"):
        cm.promote_to_senior(team, candidates.pop())
    assert _academy_count(db, team.id) + len(team.players) == ACADEMY_CAPACITY + 15


def test_potential_estimate_uses_scout_judging(db):
    cm = _manager(db)
    team = cm.find_team("Milano Rossoneri")
    rival = cm.find_team("Paris Rouge-Bleu")
    scout = team.best_staff(StaffRole.SCOUT, "judging_potential")
    own = cm.academy_players(team)[0]
    foreign = cm.academy_players(rival)[0]

    scout.judging_potential = 20
    db.flush()
    for p in (own, foreign):
        assert cm.potential_estimate(team, p) == (p.potential_rating, p.potential_rating)

    scout.judging_potential = 1
    db.flush()
    margin = development.potential_scout_margin(1)
    for p in (own, foreign):
        low, high = cm.potential_estimate(team, p)
        assert low <= p.potential_rating <= high and low < high
        assert high - low <= 2 * margin
        assert cm.potential_estimate(team, p) == (low, high)            # her acilista ayni
    assert cm.potential_estimate(team, own)[0] >= own.overall_rating


# ===========================================================================
# 4) TRANSFER: akademiler satilik degil
# ===========================================================================

def test_transfer_targets_exclude_academy_players(db):
    cm = _manager(db)
    buyer = cm.find_team("Istanbul Lions")
    targets = cm.transfer_targets(buyer, limit=1000)
    assert targets and not any(p.in_academy for p in targets)
    assert len(targets) == 345                                        # 23 kulup x 15 A takim oyuncusu
    academy_player = cm.academy_players(cm.find_team("Madrid Blancos"))[0]
    assert cm.transfer_targets(buyer, query=academy_player.name, limit=50) == []
    with pytest.raises(TransferError, match="akademi"):
        cm.offer_fee(buyer, academy_player, 1_000_000)


# ===========================================================================
# 5) HAFTALIK GELISIM
# ===========================================================================

def test_young_player_who_plays_improves_more_than_identical_bench_player(db):
    cm = _manager(db, seed=8, **QUIET)
    team = cm.find_team("Rhein Werkself")
    cm.set_user_team(team)
    xi = cm.auto_lineup(team)
    # Gelisim mevkiden bagimsizdir (yalnizca ozelliklerin dagilimi): XI'daki bir orta saha ve XI disi bir saha oyuncusu
    playing = next(p for p in team.players if p.id in xi and p.position is Position.MID)
    idle = next(p for p in team.players if p.id not in xi and p.position is not Position.GK)
    for p in (playing, idle):
        p.age, p.overall_rating, p.potential_rating, p.development_progress = 18, 72, 90, 0.0
        p.morale, p.form = 70, 55
        for attr in ENGINE_ATTRIBUTES:
            setattr(p, attr, 72 if attr != "goalkeeping" else 20)
    bench = [p.id for p in team.players if p.id not in xi and p is not idle]
    assert cm.set_lineup(team, xi, bench).ok
    db.flush()

    notes: list = []
    minutes = 0
    for _ in range(4):
        report = cm.play_week()
        notes += report.development_notes
        minutes += cm._week_minutes(report.week).get(playing.id, (0, None))[0]
        assert cm._week_minutes(report.week).get(idle.id) is None       # kadro disi: hic oynamadi
    assert minutes >= 4 * 45

    gain_playing = playing.overall_rating - 72 + playing.development_progress
    gain_idle = idle.overall_rating - 72 + idle.development_progress
    assert gain_playing > 2 * gain_idle and gain_playing >= 2.0
    assert playing.overall_rating > idle.overall_rating
    mine = [n for n in notes if n.player_id == playing.id]
    assert mine and all(isinstance(n, DevelopmentNote) for n in mine)
    assert mine[-1].new_overall == playing.overall_rating and "→" in mine[-1].detail
    assert mine[-1].potential_low <= 90 <= mine[-1].potential_high
    # Birikim veritabanina yazildi (toplu UPDATE)
    expected = {p.id: (p.overall_rating, p.development_progress) for p in (playing, idle)}
    db.flush()
    db.expire_all()
    for pid, (overall, progress) in expected.items():
        stored = db.get(Player, pid)
        assert stored.overall_rating == overall and stored.development_progress == pytest.approx(progress)


def test_midweek_cup_minutes_count_for_development(db):
    cm = _manager(db, seed=13, **QUIET)
    cm.tournaments.draw_all()
    week = cm.current_week
    cm.play_midweek()
    cup_minutes = cm._week_minutes(week)
    assert cup_minutes and max(m for m, _ in cup_minutes.values()) >= 90
    cm.play_week()
    both = [pid for pid, (m, _r) in cm._week_minutes(week).items() if m > 90]
    assert both, "hafta ici kupa + hafta sonu lig oynayan oyuncunun dakikalari toplanmali"


def test_tournament_mode_does_not_develop_or_take_intake(db):
    cm = _manager(db, seed=5)
    cm.set_game_mode(GameMode.TOURNAMENT)
    before = {pid: (ovr, prog) for pid, ovr, prog in db.execute(
        select(Player.id, Player.overall_rating, Player.development_progress))}
    academy_before = db.scalar(select(func.count()).select_from(Player).where(Player.in_academy.is_(True)))
    for _ in range(3):
        report = cm.play_week()
        assert report.development_notes == [] and report.youth_intake == [] and report.youth_intake_total == 0
    db.flush()
    after = {pid: (ovr, prog) for pid, ovr, prog in db.execute(
        select(Player.id, Player.overall_rating, Player.development_progress))}
    assert after == before
    assert db.scalar(select(func.count()).select_from(Player).where(Player.in_academy.is_(True))) == academy_before


# ===========================================================================
# 6) BIR SEZON: genc girisi (bir kez, dogru hafta, tum kulupler) + yaslanma
# ===========================================================================

def test_full_season_intake_once_and_veterans_decline(db):
    cm = _manager(db, seed=21, **QUIET)
    cm.contract_cycle = False          # 15A: AI'nin kadro tabani yukseltmeleri akademi sayimini degistirmesin
    cm.retirement = False              # 15B: genc girisi sayisi burada SABIT aralik olmali (olcekleme kendi dosyasinda)
    cm.live_market = False             # 15F: dunya pazari kadro tabani icin akademiden oyuncu CIKARIR (kendi dosyasinda)
    user = cm.find_team("Bosphorus Eagles")
    cm.set_user_team(user)
    intake_week = cm.youth_intake_week()
    assert intake_week == max(1, cm._projected_season_weeks() - 1)

    counts = {t.id: _academy_count(db, t.id) for t in cm.teams()}
    max_id = db.scalar(select(func.max(Player.id)))
    veterans = {}
    for age, team_name in ((34, "Madrid Blancos"), (36, "München Roten")):
        p = next(pl for pl in cm.find_team(team_name).players if pl.position is Position.DEF)
        p.age, p.potential_rating, p.development_progress = age, p.overall_rating, 0.0
        veterans[age] = (p, p.overall_rating, {a: getattr(p, a) for a in ENGINE_ATTRIBUTES})
    db.flush()

    intake_reports = []
    while not cm.season_finished:
        report = cm.play_week()
        if report.youth_intake_total:
            intake_reports.append(report)
        else:
            assert report.youth_intake == []
    assert cm.total_weeks() - 1 == intake_week
    assert [r.week for r in intake_reports] == [intake_week]
    report = intake_reports[0]
    assert cm.state.last_youth_intake_season == cm.season

    new_players = list(db.scalars(select(Player).where(Player.id > max_id).order_by(Player.id)))
    assert report.youth_intake_total == len(new_players)
    by_team: dict[int, list[Player]] = {}
    for p in new_players:
        assert p.in_academy and p.age in (16, 17) and p.data_source == "academy"
        assert p.potential_rating > p.overall_rating
        by_team.setdefault(p.team_id, []).append(p)
    assert set(by_team) == set(counts)                                  # TUM kulupler
    for team_id, players in by_team.items():
        assert YOUTH_INTAKE_SIZE[0] <= len(players) <= YOUTH_INTAKE_SIZE[1]
        assert _academy_count(db, team_id) == counts[team_id] + len(players)

    # Kullanicinin raporu: yalnizca kendi gencleri, Turk adlari
    mine = by_team[user.id]
    assert [n.player_id for n in report.youth_intake] == [p.id for p in mine]
    firsts, lasts = YOUTH_NAME_POOLS["Turkiye"]
    for note, p in zip(report.youth_intake, mine, strict=True):
        assert isinstance(note, YouthIntakeNote) and note.team_name == user.name
        assert note.age == p.age and note.potential_low <= p.potential_rating <= note.potential_high
        first, last = p.name.split(" ", 1)
        assert first in firsts and last in lasts

    # Yaslanma: 36 yas 34'ten hizli, hiz en cok duser
    drops = {}
    for age, (p, overall0, attrs0) in veterans.items():
        drops[age] = overall0 - p.overall_rating
        assert p.potential_rating == p.overall_rating
        losses = {a: attrs0[a] - getattr(p, a) for a in ENGINE_ATTRIBUTES}
        assert losses["pace"] == max(losses.values()) and losses["pace"] > losses["defending"]
    assert drops[34] >= 1 and drops[36] >= 3 and drops[36] > drops[34]

    # Sezon bitti: ikinci kez genc girisi yok
    assert cm.play_week().youth_intake_total == 0


def test_youth_intake_enforces_academy_capacity_with_user_note(db):
    cm = _manager(db, seed=4)
    cm.retirement = False              # 15B: bu test SABIT genc girisi aralgina dayanir (>= 3 x kulup)
    user = cm.find_team("Karadeniz Storm")
    cm.set_user_team(user)
    while _academy_count(db, user.id) < ACADEMY_CAPACITY - 1:
        _add_academy_player(cm, user, potential=99)        # yuksek potansiyelliler kalmali
    weakest = _add_academy_player(cm, user, overall=36, potential=37)
    report = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), report)
    assert _academy_count(db, user.id) == ACADEMY_CAPACITY
    assert db.get(Player, weakest.id) is None
    assert report.academy_notes and "kapasitesi" in report.academy_notes[0]
    assert all(db.get(Player, n.player_id) is not None for n in report.youth_intake)
    assert report.youth_intake_total >= 3 * len(cm.teams())
    again = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), again)                     # ayni sezon: tekrar yok
    assert again.youth_intake_total == 0


# ===========================================================================
# 7) YENI SEZON: akademi de yaslanir, AI yonetir, kullanici icin yalnizca not
# ===========================================================================

def test_new_season_ages_academy_and_ai_manages_academies(db):
    cm = _manager(db, seed=6)
    user = cm.find_team("Istanbul Lions")
    ai_team = cm.find_team("Torino Bianconeri")
    cm.set_user_team(user)

    # AI: mevkisinin en zayifindan iyi bir genc + 5 yas ustu (21 -> 22) genc
    star = _add_academy_player(cm, ai_team, age=19, overall=95, potential=97, position=Position.FWD)
    overage = [_add_academy_player(cm, ai_team, age=ACADEMY_MAX_AGE, overall=40 + i, potential=45 + i)
               for i in range(5)]
    # Kullanici: ayni durum, ama hicbir sey otomatik tasinmaz
    user_star = _add_academy_player(cm, user, age=19, overall=95, potential=97, position=Position.FWD)
    user_overage = [_add_academy_player(cm, user, age=ACADEMY_MAX_AGE, overall=40, potential=45) for _ in range(5)]
    user_academy = {p.id for p in cm.academy_players(user)}
    ages = {p.id: p.age for p in db.scalars(select(Player))}
    seniors = {t.id: len(t.players) for t in cm.teams()}

    _finish_season_quickly(db, cm)
    cm.start_new_season()
    db.flush()

    for p in db.scalars(select(Player)):
        assert p.age == min(45, ages[p.id] + 1)                         # akademi dahil herkes yaslandi
        assert p.market_value == __import__("finance").market_value(
            p.overall_rating, p.age, p.position, p.potential_rating)

    # Kullanici: akademi aynen duruyor, notlar var
    assert {p.id for p in cm.academy_players(user)} == user_academy and len(user.players) == seniors[user.id]
    assert user_star.in_academy and all(p.in_academy for p in user_overage)
    notes = " ".join(cm.new_season_notes)
    assert "yaş üstü" in notes and user_star.name in notes

    # AI: yildiz yukseldi, yas ustu kontenjani asilmadi, A takim sinirlarda
    assert not star.in_academy
    remaining_overage = [p for p in cm.academy_players(ai_team) if p.age > ACADEMY_MAX_AGE]
    assert len(remaining_overage) <= ACADEMY_OVERAGE_SLOTS
    released = [p for p in overage if db.get(Player, p.id) is None]
    promoted = [p for p in overage if db.get(Player, p.id) is not None and not p.in_academy]
    assert len(released) + len(promoted) == len(overage) - ACADEMY_OVERAGE_SLOTS
    kept = sorted((p for p in overage if db.get(Player, p.id) is not None and p.in_academy),
                  key=lambda p: -p.potential_rating)
    assert [p.id for p in kept] == [p.id for p in sorted(overage, key=lambda p: -p.potential_rating)[:3]]
    for team in cm.teams():
        if team.id == user.id:
            continue
        assert AI_MIN_SENIOR_SQUAD <= len(team.players) <= SENIOR_SQUAD_MAX
        assert sum(p.position is Position.GK for p in team.players) >= 2


def test_staff_role_attribute_exists_for_youth_coach():
    assert "working_with_youngsters" in staff_rules.ROLE_ATTRIBUTES[StaffRole.COACH]
    assert "judging_potential" in staff_rules.ROLE_ATTRIBUTES[StaffRole.SCOUT]



# ===========================================================================
# 10. Asama bagimsiz denetim duzeltmeleri
# ===========================================================================

def test_academy_wages_stay_in_the_wage_bill(db):
    """Akademiye park edilen oyuncunun maasi yukten dusmez (maas alani acma acigi kapali)."""
    cm = _manager(db)
    team = cm.find_team("Istanbul Lions")
    cm.set_user_team(team)
    academy_wages = sum(p.current_wage for p in cm.academy_players(team))
    assert team.player_wage_bill == sum(p.current_wage for p in team.players) + academy_wages
    before = team.wage_bill
    young = min((p for p in team.players if p.position is not Position.GK), key=lambda p: p.age)
    cm.send_to_academy(team, young)
    assert team.wage_bill == before


def test_academy_players_recover_condition_during_the_week(db):
    cm = _manager(db)
    team = cm.find_team("Istanbul Lions")
    prospect = cm.academy_players(team)[0]
    prospect.condition = 55
    db.flush()
    cm.play_week()
    assert prospect.condition == 100


def test_academy_notes_are_shown_in_the_week_report():
    from types import SimpleNamespace

    import career_views as cv

    report = SimpleNamespace(
        played_any=True, season=1, week=6, results=[], injuries=[], suspensions=[], transfers=[],
        lineup_notes=[], finance_note=None, manager_reputation=None, season_finished=False,
        development_notes=[], youth_intake=[], academy_notes=["Akademi dolu: Ali Veli (16) serbest bırakıldı."],
    )
    lines = cv.week_report_lines(report)
    assert ("youth", "🎓 Akademi: Akademi dolu: Ali Veli (16) serbest bırakıldı.") in lines


def test_concurrent_youth_setup_seeds_academies_once():
    """Iki giris ayni eski kaydi ayni anda doldurursa akademiler iki kez kurulmaz (game_state kilidi)."""
    import threading

    import database
    import seed
    from database import SessionLocal

    schema = OLD_SAVE_SCHEMA + "_conc"
    with database.career_context(schema):
        database.drop_career_schema(schema)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            with database.career_connection() as conn:
                conn.exec_driver_sql("DELETE FROM players WHERE in_academy")
                conn.exec_driver_sql("UPDATE game_state SET academy_seeded = false")
        except Exception:
            database.drop_career_schema(schema)
            raise

    barrier, errors = threading.Barrier(2), []

    def worker():
        try:
            with database.career_context(schema):
                session = SessionLocal()
                try:
                    cm = CareerManager(session, seed=3)
                    barrier.wait()
                    cm.ensure_youth_setup()
                    session.commit()
                finally:
                    session.close()
        except Exception as exc:          # pragma: no cover - hata ana is parcacigina tasinir
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, errors
        with database.career_context(schema):
            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=3)
                lo, hi = youth.INITIAL_ACADEMY_SIZE
                assert all(lo <= len(cm.academy_players(team)) <= hi for team in cm.teams())
            finally:
                session.close()
    finally:
        database.drop_career_schema(schema)
