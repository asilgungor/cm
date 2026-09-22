"""
Faz 15B emeklilik ve yeni jenerasyon testleri: saf kurallar (development.retirement_*, youth.replacement_intake /
anchor_shift) + gercek PostgreSQL uzerinde sezon devri (emeklilik, kadro guvencesi, kayit / haber / gelen kutusu)
ve nufus hedefine gore olceklenen genc girisi.

YAVAS dosya (`slow`): entegrasyon testleri sentetik dunyada tam sezon oynatir. Her DB testi kendi islemini geri
alir. Onerilen: TEST_DB_NAME=fm_db_test_15b. Saf testler CM_TEST_NO_DB=1 ile de kosar.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import development  # noqa: E402
import youth  # noqa: E402
from models import (  # noqa: E402
    RETIRED_TEAM_NAME,
    NewsKind,
    Position,
    TransferKind,
)

pytestmark = pytest.mark.slow

USER = "Istanbul Lions"


# ===========================================================================
# 1) SAF KURALLAR
# ===========================================================================

def test_retirement_base_follows_the_ageing_curve():
    """Taban yaslanma egrisinden (season_decline) gelir: 30 alti sifir, yasla hizlanir, 41'de kesin."""
    assert development.retirement_base(29) == 0.0
    assert development.retirement_base(development.RETIRE_FORCED_AGE) == 1.0
    ages = range(development.RETIRE_MIN_AGE, development.RETIRE_FORCED_AGE)
    values = [development.retirement_base(a) for a in ages]
    assert all(b > a for a, b in zip(values, values[1:], strict=False) if b < 1.0)
    assert values[0] < 0.05 < development.retirement_base(32) < development.retirement_base(35) < 0.5
    # 32 yasin tabani dogrudan RETIRE_RATE'tir (season_decline(32) == 1.0)
    assert development.retirement_base(32) == pytest.approx(development.RETIRE_RATE)


def test_quality_and_contract_factors():
    """Iyi oyuncu daha uzun oynar; biten sozlesme emeklilige yaklastirir, uzun sozlesme uzaklastirir."""
    strong = development.retirement_quality_factor(78)
    weak = development.retirement_quality_factor(38)
    assert strong < 1.0 < weak
    lo, hi = development.RETIRE_QUALITY_BOUNDS
    assert lo <= development.retirement_quality_factor(99) and development.retirement_quality_factor(1) <= hi
    assert development.retirement_contract_factor(3) < development.retirement_contract_factor(1)
    assert development.retirement_contract_factor(1) < development.retirement_contract_factor(0)
    assert development.retirement_contract_factor(4, clubless=True) == development.RETIRE_NO_CONTRACT
    old = development.retirement_chance(35, 60, contract_years=0)
    young_contract = development.retirement_chance(35, 60, contract_years=4)
    assert young_contract < old


def test_clubless_floor_and_forced_age():
    """Kulupsuz oyuncu daha erken birakir; isizlik uzadikca taban buyur; 41 yas kesindir."""
    assert development.retirement_chance(24, 60, 0, clubless=True, weeks_clubless=200) == 0.0
    one = development.retirement_chance(27, 60, 0, clubless=True, weeks_clubless=52)
    two = development.retirement_chance(27, 60, 0, clubless=True, weeks_clubless=104)
    assert 0.0 < one < two
    assert development.retirement_chance(33, 60, 0, clubless=True) > development.retirement_chance(33, 60, 0)
    assert development.retirement_chance(41, 99, 6) == 1.0
    assert development.retirement_chance(39, 40, 0) <= development.RETIRE_MAX


def test_retirement_chance_is_monotone_in_age_and_bounded():
    values = [development.retirement_chance(a, 65, 2) for a in range(28, 42)]
    assert values[0] == 0.0 and values[-1] == 1.0
    assert all(0.0 <= v <= 1.0 for v in values)
    assert all(b >= a for a, b in zip(values, values[1:], strict=False))


def test_replacement_intake_shares_the_deficit():
    """Acik kuluplere bolusturulur; taban 0-8 arasinda kirpilir, kalan birer birer dagitilir."""
    assert youth.replacement_intake(0, 114) == (0, 0)
    assert youth.replacement_intake(40, 114) == (0, 40)
    per_club, extra = youth.replacement_intake(200, 114)
    assert (per_club, extra) == (1, 86) and per_club * 114 + extra == 200
    assert youth.replacement_intake(10_000, 114) == (youth.REPLACEMENT_MAX, 0)
    assert youth.replacement_intake(-50, 114) == (0, 0)
    assert youth.replacement_intake(100, 0) == (0, 0)


def test_replacement_deficit_smooths_the_retirement_wave():
    """Dalgali emeklilik dogrudan uretime yansimaz: sabit yenilenme oranina dogru yumusatilir."""
    target = 2958
    steady = youth.replacement_deficit(target, target, round(target / youth.REPLACEMENT_SEASONS))
    wave = youth.replacement_deficit(target, target, 260)
    calm = youth.replacement_deficit(target, target, 60)
    assert calm < steady < wave                                  # yon dogru
    assert wave - steady < 260 - steady                          # ama tam yansimiyor (sonumlu)
    assert steady - calm < steady - 60
    # Nufus acigi kismi kapatilir; fazla nufusta uretim duser
    assert youth.replacement_deficit(target, target - 200, 160) > steady
    assert youth.replacement_deficit(target, target + 200, 160) < steady
    assert youth.replacement_deficit(0, 0, 100) == 0


def test_anchor_shift_defaults_to_zero_and_keeps_the_gap():
    """Capa verilmezse uretim BIREBIR eskisi gibi; capa verilince seviye kayar, kalan pay degismez."""
    assert youth.anchor_shift(None) == 0.0
    assert youth.anchor_shift(youth.ANCHOR_REFERENCE) == 0.0
    assert youth.anchor_shift(73) == pytest.approx(73 - youth.ANCHOR_REFERENCE)
    plain = youth.generate_intake(random.Random(5), "Türkiye", 10, 70, 30, set())
    same = youth.generate_intake(random.Random(5), "Türkiye", 10, 70, 30, set(), shift=0.0)
    assert [(s.name, s.overall, s.potential) for s in plain] == [(s.name, s.overall, s.potential) for s in same]
    shifted = youth.generate_intake(random.Random(5), "Türkiye", 10, 70, 30, set(), shift=18.0)
    base_mean = sum(s.overall for s in plain) / len(plain)
    shift_mean = sum(s.overall for s in shifted) / len(shifted)
    assert 14 < shift_mean - base_mean < 22
    assert abs(sum(s.gap for s in shifted) - sum(s.gap for s in plain)) <= 3 * len(plain)
    assert all(s.potential <= youth.POTENTIAL_CAP for s in shifted)


def test_balanced_growth_keeps_the_profile_and_stops_attribute_inflation():
    """
    CM dersi 1: eski kural her +1 gucu tek ozellige yigiyordu (18 puanlik gelisimde forvetin sutu 99'a
    dayaniyor, kalecinin kaleciligi 86'da kaliyordu). Dengeli kural profili korur; guc ayni artar.
    """
    from ratings import ENGINE_ATTRIBUTES, POSITION_OFFSETS, POSITION_WEIGHTS, compute_overall

    for position in Position:
        start = {a: max(5, 60 + POSITION_OFFSETS[position][a]) for a in ENGINE_ATTRIBUTES}
        key = max(ENGINE_ATTRIBUTES, key=lambda a: POSITION_WEIGHTS[position][a])   # noqa: B023
        old = new = start
        for _ in range(18):
            old = development.raise_attributes(position, old)
            new = development.raise_attributes(position, new, balanced=True)
        assert new[key] - start[key] == 18, position            # profil korunur: anahtar ozellik guc kadar artar
        assert compute_overall(position, new) - compute_overall(position, start) >= 17, position
        weighted = [a for a in ENGINE_ATTRIBUTES if POSITION_WEIGHTS[position][a] > 0]
        assert all(new[a] - start[a] == 18 for a in weighted), position
        assert all(new[a] == start[a] for a in ENGINE_ATTRIBUTES if a not in weighted), position
        if position is not Position.GK:                         # saha oyuncusunda eski kural sisiriyordu
            assert old[key] > new[key] and old[key] >= 95, (position, old[key], new[key])


def test_retired_kind_fits_the_schema():
    assert len(TransferKind.RETIRED.value) <= 12
    assert len(RETIRED_TEAM_NAME) <= 80
    assert NewsKind.RETIREMENT.value == "RETIREMENT"


# ===========================================================================
# 2) VERITABANI (sentetik dunya, islem geri alinir)
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


def _manager(db, *, flag: bool = True, contracts_flag: bool = False):
    from career_manager import CareerManager
    from models import GameMode

    cm = CareerManager(db, seed=7)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.retirement = flag
    cm.contract_cycle = contracts_flag        # 15A dongusu ayri dosyada sinaniyor; burada izole kalsin
    cm.board = False                          # 15C kovulmasi bu testlerin konusu degil
    cm.set_game_mode(GameMode.CAREER)
    cm.set_user_team(cm.find_team(USER))
    cm.ensure_youth_setup()
    return cm


def _play_season(cm) -> None:
    for _ in range(12):
        if cm.season_finished:
            break
        cm.play_week()
    assert cm.season_finished


def _age_squad(cm, team, age: int, only=None) -> list[int]:
    """Kulubun A takim oyuncularini verilen yasa getirir; etkilenen oyuncu id'lerini dondurur."""
    ids = []
    for p in cm._senior_players(team):
        if only is not None and p.position is not only:
            continue
        p.age = age
        ids.append(p.id)
    cm.db.flush()
    return ids


def _count(db, model, *where) -> int:
    db.flush()
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


@pytest.mark.integration
def test_flag_off_changes_nothing(db):
    """Bayrak kapali: tam sezon + devir boyunca tek emeklilik yok, hedefler yazilmaz, genc girisi eski sabit."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import YOUTH_INTAKE_SIZE
    from models import GameState, Player, TransferLog

    cm = _manager(db, flag=False)
    for p in db.scalars(select(Player).where(Player.age >= 30)):
        p.age = 38                                             # bayrak acik olsa cogu birakirdi
    db.flush()
    before = _count(db, Player)
    max_id = db.scalar(select(func.max(Player.id)))
    _play_season(cm)
    intake = _count(db, Player, Player.id > max_id)
    cm.start_new_season()
    state = db.get(GameState, 1)
    assert state.population_target is None and state.strength_target is None
    assert _count(db, TransferLog, TransferLog.kind == TransferKind.RETIRED.value) == 0
    assert _count(db, Player) >= before                        # kimse silinmedi (giris ekledi)
    per_club = intake / len(cm.teams())
    assert YOUTH_INTAKE_SIZE[0] <= per_club <= YOUTH_INTAKE_SIZE[1]


@pytest.mark.integration
def test_rollover_retires_veterans_with_log_news_and_inbox(db):
    """40'lik kadro devirde birakir: oyuncu satiri silinir, transfer_log RETIRED, haber ve gelen kutusu mesaji."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import RETIREMENT_SQUAD_FLOOR
    from models import InboxMessage, NewsItem, Player, TransferLog

    cm = _manager(db)
    team = cm.find_team(USER)
    ids = _age_squad(cm, team, development.RETIRE_FORCED_AGE)
    names = {p.name for p in cm._senior_players(team)}
    notes = cm._retire_players(cm.season + 1)

    remaining = [pid for pid in ids if db.get(Player, pid) is not None]
    assert len(remaining) == RETIREMENT_SQUAD_FLOOR == len(cm._senior_players(team))    # kadro tabani korunur
    logs = list(db.scalars(select(TransferLog).where(TransferLog.kind == TransferKind.RETIRED.value,
                                                     TransferLog.from_team_id == team.id)))
    assert len(logs) == len(ids) - len(remaining) > 0
    log = logs[0]
    assert log.to_team_name == RETIRED_TEAM_NAME and log.to_team_id is None and log.player_id is None
    assert log.from_team_name == team.name and log.fee == 0 and log.player_name in names
    news = list(db.scalars(select(NewsItem).where(NewsItem.kind == NewsKind.RETIREMENT.value)))
    assert len(news) >= len(logs) and all("futbolu bıraktı" in n.text for n in news)
    assert len([n for n in news if n.team_id == team.id]) == len(logs)      # insan kulubunun HER emeklisi
    messages = list(db.scalars(select(InboxMessage).where(InboxMessage.team_id == team.id)))
    assert len(messages) == 1 and "futbolu bıraktı" in messages[0].subject
    assert messages[0].lines and len(messages[0].lines) == len(logs)
    assert notes and team.name in notes[0]


@pytest.mark.integration
def test_retirement_keeps_the_squad_floor_and_two_keepers(db):
    """Tum kadro emeklilik yasinda olsa bile kulup 13 oyuncunun ve 2 kalecinin altina dusmez."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import MIN_SENIOR_KEEPERS, RETIREMENT_SQUAD_FLOOR

    cm = _manager(db)
    for team in cm.teams():
        _age_squad(cm, team, development.RETIRE_FORCED_AGE)
    cm._retire_players(cm.season + 1)
    for team in cm.teams():
        squad = cm._senior_players(team)
        keepers = [p for p in squad if p.position is Position.GK]
        assert len(squad) >= RETIREMENT_SQUAD_FLOOR, team.name
        assert len(keepers) >= MIN_SENIOR_KEEPERS, team.name


@pytest.mark.integration
def test_clubless_veterans_leave_the_free_agent_pool(db):
    """Kulupsuz veteran havuzda sonsuza kadar beklemez: kaydi kulupsuz (from_team_id NULL) yazilir."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import Player, TransferLog

    cm = _manager(db)
    team = cm.find_team(USER)
    victim = sorted(cm._senior_players(team), key=lambda p: p.overall_rating)[0]
    victim.team_id = None
    victim.in_academy = False
    victim.age = development.RETIRE_FORCED_AGE          # zar bagimsiz: kesin birakir (tohumlu zar test etmiyoruz)
    victim.contract_years = 0
    victim.free_agent_since = max(1, cm.career_week - 60)
    db.flush()
    # Kulupsuzluk 36 yasinda bile riski buyutur (saf kural; burada kayit yolunu siniyoruz)
    assert (development.retirement_chance(36, victim.overall_rating, 0, True, 60)
            > development.retirement_chance(36, victim.overall_rating, 2))
    cm._retire_players(cm.season + 1)
    assert db.get(Player, victim.id) is None
    log = db.scalars(select(TransferLog).where(TransferLog.player_name == victim.name,
                                               TransferLog.kind == TransferKind.RETIRED.value)).first()
    assert log is not None and log.from_team_id is None and log.from_team_name is None


@pytest.mark.integration
def test_retirement_is_seeded_and_does_not_touch_cm_rng(db):
    """Ayni tohum ayni emeklileri verir; cm.rng'den tek zar bile cekilmez (mac sonuclari degismez)."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import Player, TransferLog

    cm = _manager(db)
    for p in db.scalars(select(Player).where(Player.team_id.isnot(None))):
        p.age = 35
    db.flush()
    state_before = cm.rng.getstate()
    with db.begin_nested() as nested:
        cm._retire_players(cm.season + 1)
        first = sorted(db.scalars(select(TransferLog.player_name)
                                  .where(TransferLog.kind == TransferKind.RETIRED.value)))
        assert first
        nested.rollback()
    assert cm.rng.getstate() == state_before
    cm._retire_players(cm.season + 1)
    second = sorted(db.scalars(select(TransferLog.player_name)
                               .where(TransferLog.kind == TransferKind.RETIRED.value)))
    assert first == second
    assert cm._retirement_rng(7, 3).random() == cm._retirement_rng(7, 3).random()
    assert cm._retirement_rng(7, 3).random() != cm._retirement_rng(7, 4).random()


@pytest.mark.integration
def test_intake_scales_to_the_replacement_need(db):
    """Genc girisi nufus acigini kapatir: hedef yazilir, acik yoksa giris kucuk kalir, acik varsa buyur."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import WeekReport
    from models import GameState, Player

    cm = _manager(db)
    clubs = len(cm.teams())
    start = _count(db, Player)
    report = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), report)
    state = db.get(GameState, 1)
    assert state.population_target == start and state.strength_target is not None
    small = report.youth_intake_total
    assert 0 < small <= clubs * 2                              # denge dunyasinda giris kucuk

    # Buyuk bir acik: yariya yakin kadro emekli olsun, hedef ayni kalsin
    for p in list(db.scalars(select(Player).where(Player.in_academy.is_(False)))):
        if p.id % 2 == 0:
            db.delete(p)
    db.flush()
    st = db.get(GameState, 1)
    st.last_youth_intake_season = None
    db.flush()
    big_report = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), big_report)
    assert big_report.youth_intake_total > small * 3
    assert db.get(GameState, 1).population_target == start     # hedef bir kez yazilir


@pytest.mark.integration
def test_new_generation_matches_the_world_strength_anchor(db):
    """Yeni jenerasyonun ortalama POTANSIYELI dunyanin guc capasi kadardir (CM dersi: kayma olmasin)."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import WeekReport
    from models import Player

    cm = _manager(db)
    world_mean = float(db.scalar(select(func.avg(Player.overall_rating))))
    anchor = cm.strength_anchor()
    assert abs(anchor - world_mean) <= 1
    before = set(db.scalars(select(Player.id)))
    report = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), report)
    newcomers = list(db.scalars(select(Player).where(Player.id.notin_(before))))
    assert len(newcomers) >= 3
    mean_potential = sum(p.potential_rating for p in newcomers) / len(newcomers)
    # Capasiz uretim ~ANCHOR_REFERENCE seviyesinde kalirdi (dunya 74'ken 55): kaydirma onu dunyaya tasir.
    assert anchor <= mean_potential <= anchor + 15
    assert mean_potential > youth.ANCHOR_REFERENCE + youth.anchor_shift(anchor) - 5


@pytest.mark.integration
def test_tournament_mode_has_no_retirement(db):
    """Turnuva modunda yas ilerlemez: emeklilik adimi hic calismaz."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import CareerManager
    from models import GameMode, Player, TransferLog

    cm = CareerManager(db, seed=7)
    cm.retirement = True
    cm.board = False
    cm.set_game_mode(GameMode.TOURNAMENT)
    for p in db.scalars(select(Player)):
        p.age = 40
    db.flush()
    assert not cm._retirement_on()
    assert cm._retire_players(cm.season + 1) == []
    assert _count(db, TransferLog, TransferLog.kind == TransferKind.RETIRED.value) == 0


@pytest.mark.integration
def test_full_season_rollover_with_contract_cycle(db):
    """15A ile birlikte: emeklilik serbest birakmadan once calisir, kadro tabani ve 2 kaleci korunur."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from career_manager import MIN_SENIOR_KEEPERS
    from models import Player, TransferLog

    cm = _manager(db, contracts_flag=True)
    for p in db.scalars(select(Player).where(Player.team_id.isnot(None), Player.in_academy.is_(False))):
        if p.id % 3 == 0:
            p.age, p.contract_years = 37, 1
    db.flush()
    _play_season(cm)
    cm.start_new_season()
    retired = _count(db, TransferLog, TransferLog.kind == TransferKind.RETIRED.value)
    assert retired > 0
    for team in cm.teams():
        squad = cm._senior_players(team)
        assert len([p for p in squad if p.position is Position.GK]) >= MIN_SENIOR_KEEPERS, team.name
    # Emekli olan oyuncu ayrica "serbest kaldi" diye yazilmaz
    names_retired = set(db.scalars(select(TransferLog.player_name)
                                   .where(TransferLog.kind == TransferKind.RETIRED.value)))
    names_released = set(db.scalars(select(TransferLog.player_name)
                                    .where(TransferLog.kind == TransferKind.RELEASED.value)))
    assert not (names_retired & names_released)


@pytest.mark.integration
def test_schema_is_additive_and_upgrades(db):
    """15B yalnizca iki sutun ekler; sema surumu artti ve dogrulama temiz."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import database

    assert database.SCHEMA_VERSION >= 20
    columns = {(table, column) for table, column, _ddl in database.ADDITIVE_COLUMNS}
    assert ("game_state", "population_target") in columns
    assert ("game_state", "strength_target") in columns
    assert database.schema_problems() == []
    assert database.upgrade_schema() == []                     # idempotent
