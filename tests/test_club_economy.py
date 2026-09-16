"""
Kulup tesisleri ve sponsorluk ekonomisi ENTEGRASYON testleri (11. Asama).

Gercek PostgreSQL'e karsi calisir (conftest test veritabani; onerilen: TEST_DB_NAME=fm_db_test_facilities).
Her test kendi transaction'ini rollback eder. 'public' test dunyasi seed ile kurulur ve tesis/sponsor verisi
YOKTUR (kurulmamis kulup = eski davranis); testler ensure_club_setup'i rollback edilen oturumda cagirir.
Eski kayit ve kariyer izolasyonu testleri AYRI kariyer semalarinda calisir ve semayi sonunda siler.
"""

from __future__ import annotations

import random
import sys
import zlib
from pathlib import Path
from statistics import mean

import pytest
from sqlalchemy import func, select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facilities as fac  # noqa: E402
import fitness  # noqa: E402
from career_manager import (  # noqa: E402
    AI_FACILITY_BUDGET_SHARE,
    CareerManager,
    FacilityError,
    WeekReport,
)
from match_engine import play_fixture  # noqa: E402
from models import Fixture, FixtureStatus, Player, StaffRole, Team  # noqa: E402


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

CLUB_COLUMNS = ("stadium_capacity", "medical_facilities", "sponsor_name", "sponsor_weekly",
                "sponsor_until_season", "sponsor_offers")


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, seed=21) -> CareerManager:
    cm = CareerManager(db, seed=seed)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    return cm


def _ready(db, seed=21) -> CareerManager:
    """Sezon basi + tesis/sponsor doldurulmus kariyer (rollback ile geri alinir)."""
    cm = _manager(db, seed)
    cm.ensure_club_setup()
    return cm


def _no_ai_market(cm: CareerManager) -> None:
    cm.run_ai_transfer_window = lambda: []


def _finish_season_quickly(db, cm: CareerManager) -> None:
    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    assert cm.season_finished


def _club_snapshot(session) -> tuple:
    session.flush()
    return tuple(session.execute(
        select(Team.id, Team.stadium_capacity, Team.medical_facilities, Team.sponsor_name, Team.sponsor_weekly,
               Team.sponsor_until_season, Team.sponsor_offers).order_by(Team.id)
    ).all())


# ===========================================================================
# 1) DOLDURMA: yeni dunya ve eski kayit
# ===========================================================================

def test_seeded_world_is_unconfigured_and_backfill_is_idempotent(db):
    cm = _manager(db)
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    teams = cm.teams()
    assert all(t.stadium_capacity is None and t.medical_facilities is None and t.sponsor_name is None
               and t.sponsor_offers == [] for t in teams)
    assert cm.facility_status(user)["configured"] is False

    messages = cm.ensure_club_setup()
    n = len(teams)
    assert messages == [f"{n} kulübe stadyum kapasitesi atandı.", f"{n} kulübe sağlık merkezi seviyesi verildi.",
                        f"{n} kulübe başlangıç sponsor sözleşmesi ve sponsor teklifleri verildi."]
    for team in teams:
        assert fac.STADIUM_MIN <= team.stadium_capacity <= fac.STADIUM_MAX
        assert 1 <= team.medical_facilities <= 20
        defaults = fac.default_facilities(
            team.reputation, random.Random(zlib.crc32(f"club-facilities|{team.id}".encode())))
        assert (team.stadium_capacity, team.medical_facilities) == (defaults.stadium_capacity,
                                                                    defaults.medical_facilities)
        assert fac.sponsor_active(team.sponsor_name, team.sponsor_until_season, cm.season)
        assert team.sponsor_weekly <= fac.sponsor_base_weekly(team.reputation)
        offers = cm.sponsor_offers(team)
        assert len(offers) == 3 and team.sponsor_name not in {o.brand for o in offers}
        weeks = cm._projected_season_weeks()
        base = fac.sponsor_base_weekly(team.reputation)
        assert 0.5 * base * weeks - 10_000 <= offers[2].signing_bonus <= 0.7 * base * weeks + 10_000
    first = _club_snapshot(db)
    assert cm.ensure_club_setup() == [] and _club_snapshot(db) == first
    assert cm.facility_status(user)["configured"] is True

    # Kismi NULL: yalnizca eksik olan doldurulur, sponsoru biten kulup bedava sozlesme ALMAZ
    other = cm.find_team("Merseyside Reds")
    other.medical_facilities = None
    other.sponsor_name, other.sponsor_weekly = None, 0              # sozlesmesi bitmis (bitis sezonu duruyor)
    db.flush()
    assert cm.ensure_club_setup() == ["1 kulübe sağlık merkezi seviyesi verildi."]
    assert other.sponsor_name is None and other.medical_facilities is not None


OLD_SAVE_SCHEMA = "test_fac_old_save"


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
            with database.career_connection() as conn:                  # 11. Asama oncesi kayit
                for column in CLUB_COLUMNS:
                    conn.exec_driver_sql(f'ALTER TABLE "teams" DROP COLUMN "{column}"')
            assert {f"eksik sütun: teams.{c}" for c in CLUB_COLUMNS} <= set(database.schema_problems())

            applied = database.upgrade_schema()
            assert {f"sütun eklendi: teams.{c}" for c in CLUB_COLUMNS} <= set(applied)
            assert database.upgrade_schema() == [] and database.schema_problems() == []

            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=3)
                cm.set_user_team(cm.find_team("Kadıköy Canaries"))
                assert all(t.sponsor_offers == [] and t.sponsor_weekly == 0 for t in cm.teams())   # guvenli varsayilanlar
                messages = cm.ensure_club_setup()
                assert len(messages) == 3 and all("24 kulübe" in m for m in messages)
                first = _club_snapshot(session)
                session.rollback()                                   # ayni kulup id + itibar: ayni doldurma
                cm = CareerManager(session, seed=99)                 # kariyer tohumundan bagimsiz
                assert len(cm.ensure_club_setup()) == 3
                assert _club_snapshot(session) == first
                cm.set_user_team(cm.find_team("Kadıköy Canaries"))
                session.commit()
            finally:
                session.close()

            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=3)
                assert cm.ensure_club_setup() == [] and cm.ensure_youth_setup() == []      # idempotent
                assert _club_snapshot(session) == first
                user = cm.user_team
                assert len(cm.sponsor_offers(user)) == 3 and cm.facility_status(user)["configured"]
            finally:
                session.close()
        finally:
            database.drop_career_schema(OLD_SAVE_SCHEMA)


# ===========================================================================
# 2) TESIS YATIRIMI
# ===========================================================================

def test_facility_status_shape_and_previews(db):
    cm = _ready(db)
    team = cm.find_team("Milano Nerazzurri")
    status = cm.facility_status(team)
    assert set(status) == {"team_id", "team_name", "season", "transfer_budget", "configured",
                           "youth", "medical", "stadium", "sponsor"}
    youth_block, medical, stadium, sponsor = status["youth"], status["medical"], status["stadium"], status["sponsor"]
    assert youth_block["level"] == team.youth_facilities and youth_block["next_level"] == team.youth_facilities + 1
    assert youth_block["upgrade_cost"] == fac.facility_upgrade_cost("youth", team.youth_facilities)
    assert youth_block["potential_shift_next"] > youth_block["potential_shift_now"]
    assert youth_block["growth_multiplier_next"] > youth_block["growth_multiplier_now"]
    assert medical["recovery_multiplier_now"] == fac.medical_recovery_multiplier(team.medical_facilities)
    assert medical["recovery_multiplier_next"] > medical["recovery_multiplier_now"]
    assert stadium["capacity"] == team.stadium_capacity and stadium["next_capacity"] == team.stadium_capacity + 5000
    assert stadium["expansion_cost"] == fac.stadium_expansion_cost(team.stadium_capacity)
    assert stadium["gate_income_now"] == fac.gate_income(team.stadium_capacity, team.reputation)
    assert stadium["gate_income_next"] >= stadium["gate_income_now"] and stadium["demand"] > stadium["capacity"]
    assert youth_block["affordable"] and medical["affordable"] and stadium["affordable"]
    assert sponsor == {"name": team.sponsor_name, "weekly": team.sponsor_weekly,
                       "until_season": team.sponsor_until_season, "active": True,
                       "seasons_left": team.sponsor_until_season - cm.season + 1, "pending_offers": 3}

    team.youth_facilities, team.stadium_capacity = 20, fac.STADIUM_MAX
    team.transfer_budget = 0
    status = cm.facility_status(team)
    assert status["youth"]["at_max"] and status["youth"]["upgrade_cost"] is None
    assert status["youth"]["potential_shift_next"] is None and status["youth"]["next_level"] is None
    assert status["stadium"]["at_max"] and status["stadium"]["expansion_cost"] is None
    assert status["stadium"]["gate_income_next"] is None
    assert not status["medical"]["affordable"]


def test_upgrades_deduct_budget_and_enforce_caps(db):
    cm = _ready(db)
    team = cm.find_team("Torino Bianconeri")
    budget, youth_level, medical_level, capacity = (team.transfer_budget, team.youth_facilities,
                                                    team.medical_facilities, team.stadium_capacity)
    expected = cm.facility_status(team)

    cost = cm.upgrade_facility(team, "youth")
    assert cost == expected["youth"]["upgrade_cost"] and team.youth_facilities == youth_level + 1
    cost2 = cm.upgrade_facility(team, "medical")
    assert cost2 == expected["medical"]["upgrade_cost"] and team.medical_facilities == medical_level + 1
    cost3 = cm.upgrade_facility(team, "stadium")
    assert cost3 == expected["stadium"]["expansion_cost"] and team.stadium_capacity == capacity + 5000
    assert team.transfer_budget == budget - cost - cost2 - cost3
    db.expire_all()
    assert db.get(Team, team.id).transfer_budget == budget - cost - cost2 - cost3     # flush edildi

    team = db.get(Team, team.id)
    team.youth_facilities, team.medical_facilities, team.stadium_capacity = 20, 20, fac.STADIUM_MAX
    db.flush()
    before = team.transfer_budget
    with pytest.raises(FacilityError, match="Altyapı tesisleri zaten en üst seviyede"):
        cm.upgrade_facility(team, "youth")
    with pytest.raises(FacilityError, match="Sağlık merkezi zaten en üst seviyede"):
        cm.upgrade_facility(team, "medical")
    with pytest.raises(FacilityError, match="en büyük kapasitede"):
        cm.upgrade_facility(team, "stadium")
    with pytest.raises(FacilityError, match="Bilinmeyen tesis"):
        cm.upgrade_facility(team, "training")
    assert team.transfer_budget == before

    # Kasa yetmiyor: hicbir sey degismez
    poor = cm.find_team("Karadeniz Storm")
    level = poor.medical_facilities
    poor.transfer_budget = fac.facility_upgrade_cost("medical", level) - 1
    db.flush()
    with pytest.raises(FacilityError, match="Transfer bütçesi yetersiz: Sağlık merkezi"):
        cm.upgrade_facility(poor, "medical")
    assert poor.medical_facilities == level and poor.transfer_budget == fac.facility_upgrade_cost("medical", level) - 1
    poor.transfer_budget += 1
    assert cm.upgrade_facility(poor, "medical") > 0 and poor.transfer_budget == 0


def test_upgrade_on_unconfigured_club_uses_the_backfill_defaults(db):
    cm = _manager(db)
    team = cm.find_team("Vesuvio Azzurri")
    status = cm.facility_status(team)
    assert not status["configured"]
    cm.upgrade_facility(team, "medical")
    assert team.medical_facilities == status["medical"]["level"] + 1
    assert team.stadium_capacity == status["stadium"]["capacity"]          # diger tesisler de kaydedildi
    cm.ensure_club_setup()
    assert team.medical_facilities == status["medical"]["level"] + 1       # doldurma yatirimi ezmez


# ===========================================================================
# 3) SPONSOR IMZASI
# ===========================================================================

def test_sign_sponsor_replaces_contract_pays_bonus_and_clears_offers(db):
    cm = _ready(db)
    team = cm.find_team("Istanbul Lions")
    cm.set_user_team(team)
    offers = cm.sponsor_offers(team)
    budget = team.transfer_budget
    chosen = cm.sign_sponsor(team, 2)
    assert chosen == offers[2] and chosen.signing_bonus > 0
    assert (team.sponsor_name, team.sponsor_weekly) == (chosen.brand, chosen.weekly)
    assert team.sponsor_until_season == cm.season + chosen.seasons - 1
    assert team.transfer_budget == budget + chosen.signing_bonus
    assert team.sponsor_offers == [] and cm.sponsor_offers(team) == []
    with pytest.raises(FacilityError, match="Bekleyen sponsor teklifi yok"):
        cm.sign_sponsor(team, 0)
    # Tekrar giris (doldurma) yeni teklif uretmez: imza primi tekrar tekrar alinamaz
    assert cm.ensure_club_setup() == [] and cm.sponsor_offers(team) == []
    assert cm.facility_status(team)["sponsor"]["name"] == chosen.brand

    other = cm.find_team("London Gunners")
    snapshot = (other.sponsor_name, other.transfer_budget, list(other.sponsor_offers))
    for bad in (-1, 3, True, "0", None):
        with pytest.raises(FacilityError, match="Geçersiz sponsor teklifi"):
            cm.sign_sponsor(other, bad)
    assert (other.sponsor_name, other.transfer_budget, list(other.sponsor_offers)) == snapshot


# ===========================================================================
# 4) HAFTALIK EKONOMI
# ===========================================================================

def test_weekly_sponsor_and_gate_income_flow_into_the_transfer_budget(db):
    cm = _ready(db, seed=5)
    _no_ai_market(cm)
    week = cm.current_week
    league_fixtures = cm.fixtures_for_week(week)
    host = cm.db.get(Team, league_fixtures[0].home_team_id)
    cm.set_user_team(host)
    teams = cm.teams()
    before = {t.id: (t.transfer_budget, cm.wage_summary(t).free, t.sponsor_weekly) for t in teams}

    report = cm.play_week()
    db.flush()
    home_rows = dict(db.execute(
        select(Fixture.home_team_id, func.count()).where(
            Fixture.season == cm.season, Fixture.week == week, Fixture.status == FixtureStatus.PLAYED,
            Fixture.neutral_venue.is_(False)).group_by(Fixture.home_team_id)).all())
    away_only = 0
    tv_shares = cm._tv_shares(week)                                     # 12. Asama: lig TV payi da ayni adimda
    for team in teams:
        budget, free, sponsor = before[team.id]
        gate = home_rows.get(team.id, 0) * fac.gate_income(team.stadium_capacity, team.reputation)
        assert team.transfer_budget == max(0, budget + free + sponsor + gate + tv_shares[team.id]), team.name
        away_only += team.id not in home_rows
    assert away_only > 0                                                # deplasman: yalnizca sponsor

    assert home_rows[host.id] >= 1
    expected_gate = home_rows[host.id] * fac.gate_income(host.stadium_capacity, host.reputation)
    assert report.sponsor_income == host.sponsor_weekly > 0 and report.gate_income == expected_gate > 0
    assert report.tv_income == tv_shares[host.id] > 0
    note = report.finance_note
    assert "Maaşlar ödendi" in note and f"sponsor geliri ({host.sponsor_name})" in note
    assert "bilet geliri" in note and note.index("bilet geliri") < note.index("transfer kasası")


def test_no_income_without_contract_and_neutral_or_unconfigured_clubs(db):
    cm = _ready(db)
    team = cm.find_team("Bosphorus Eagles")
    cm.set_user_team(team)
    team.sponsor_name, team.sponsor_weekly = None, 0
    db.flush()
    budget, free = team.transfer_budget, cm.wage_summary(team).free
    report = WeekReport(cm.season, cm.current_week)
    cm._pay_weekly_wages(report, cm.current_week)                     # henuz mac oynanmadi: gate yok
    assert team.transfer_budget == budget + free
    assert report.sponsor_income == 0 and report.gate_income == 0
    assert "sponsor geliri yok (bekleyen teklifleri değerlendir)" in report.finance_note

    # Tarafsiz sahadaki mac (final) mac gunu geliri getirmez; kurulmamis kulup da almaz
    fx = cm.fixtures_for_week(1)[0]
    fx.status, fx.home_score, fx.away_score = FixtureStatus.PLAYED, 1, 0
    db.flush()
    assert cm._home_matches_played(1) == {fx.home_team_id: 1}
    fx.neutral_venue = True
    db.flush()
    assert cm._home_matches_played(1) == {}
    fx.neutral_venue = False
    host = db.get(Team, fx.home_team_id)
    host.stadium_capacity = None
    db.flush()
    budget, free, sponsor = host.transfer_budget, cm.wage_summary(host).free, host.sponsor_weekly
    cm._pay_weekly_wages(WeekReport(cm.season, 1), 1)
    assert host.transfer_budget == max(0, budget + free + sponsor)


# ===========================================================================
# 5) YENI SEZON: sozlesme bitisi, taze teklifler, AI imzasi ve yatirimi
# ===========================================================================

def test_contracts_expire_and_new_season_brings_offers_ai_signs(db):
    cm = _ready(db, seed=6)
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    old_season = cm.season
    expiring_ai = cm.find_team("Paris Rouge-Bleu")
    for team in (user, expiring_ai):
        team.sponsor_until_season = old_season
    long_ai = cm.find_team("Rhône Gones")
    long_ai.sponsor_until_season = old_season + 3
    long_ai.sponsor_weekly = fac.sponsor_base_weekly(long_ai.reputation) * 3     # cok iyi sozlesme: birakilmaz
    db.flush()
    season_weeks = cm._projected_season_weeks()
    before = {t.id: (t.transfer_budget, t.youth_facilities, t.medical_facilities, t.stadium_capacity,
                     t.sponsor_name) for t in cm.teams()}
    user_brand = user.sponsor_name

    _finish_season_quickly(db, cm)
    cm.start_new_season()
    db.flush()
    assert cm.season == old_season + 1

    # Kullanici: sozlesme bitti, teklifler bekliyor, hicbir sey otomatik imzalanmadi
    assert user.sponsor_name is None and user.sponsor_weekly == 0 and user.sponsor_until_season == old_season
    offers = cm.sponsor_offers(user)
    assert len(offers) == 3
    # 12. Asama: sezon sonu lig odulu (lig bu cagrida arsivlendi) ve baskan guvencesi kasaya girer; baska hareket yok
    payouts = {tid: sum(row.values()) for tid, row in cm.season_payouts.items()}
    assert payouts.get(user.id, 0) > 0
    assert (user.transfer_budget - payouts[user.id], user.youth_facilities, user.medical_facilities,
            user.stadium_capacity) == before[user.id][:4]
    notes = " ".join(cm.new_season_notes)
    assert f"Sponsor sözleşmen sona erdi ({user_brand})" in notes and "3 yeni sponsor teklifi var" in notes
    assert all(o.brand in notes for o in offers)
    assert cm.facility_status(user)["sponsor"] == {"name": None, "weekly": 0, "until_season": None,
                                                   "active": False, "seasons_left": 0, "pending_offers": 3}

    # AI: biten sozlesme -> en degerli teklif imzalandi, teklif saklanmaz
    rng = cm._club_rng("sponsor-offers", expiring_ai.id, cm.season)
    ai_offers = fac.generate_sponsor_offers(rng, expiring_ai.reputation, season_weeks=season_weeks)
    best = ai_offers[fac.best_offer_index(ai_offers, season_weeks)]
    assert expiring_ai.sponsor_name == best.brand and expiring_ai.sponsor_weekly == best.weekly
    assert expiring_ai.sponsor_until_season == cm.season + best.seasons - 1 and expiring_ai.sponsor_offers == []
    assert long_ai.sponsor_name == before[long_ai.id][4] and long_ai.sponsor_offers == []

    # Her AI kulubunun kasa hareketi = imza primi - (en fazla bir) tesis yatirimi (+ 12. Asama lig odulu / guvence)
    for team in cm.teams():
        if team.id == user.id:
            continue
        budget0, youth0, medical0, capacity0, _name = before[team.id]
        changes = [(team.youth_facilities - youth0, "youth", youth0), (team.medical_facilities - medical0, "medical", medical0),
                   ((team.stadium_capacity - capacity0) // 5000, "stadium", capacity0)]
        invested = [(kind, level) for step, kind, level in changes if step]
        assert len(invested) <= 1 and all(step in (0, 1) for step, _k, _l in changes), team.name
        cost = 0
        if invested:
            kind, level = invested[0]
            cost = (fac.stadium_expansion_cost(level) if kind == "stadium"
                    else fac.facility_upgrade_cost(kind, level))
        signed = team.sponsor_until_season is not None and team.sponsor_until_season >= cm.season
        assert signed, team.name
        bonus = team.transfer_budget - budget0 + cost - payouts.get(team.id, 0)
        assert bonus >= 0 and bonus % 10_000 == 0, team.name

    # Yeni sezonda sozlesmesiz kullanici sponsor geliri almaz; imzalayinca alir
    report = WeekReport(cm.season, 1)
    cm._pay_weekly_wages(report, 1)
    assert report.sponsor_income == 0
    signed = cm.sign_sponsor(user, 0)
    report = WeekReport(cm.season, 1)
    cm._pay_weekly_wages(report, 1)
    assert report.sponsor_income == signed.weekly


def test_contract_signed_after_the_final_whistle_counts_from_next_season(db):
    cm = _ready(db, seed=8)
    user = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(user)
    _finish_season_quickly(db, cm)
    one_season = cm.sign_sponsor(user, 0)
    assert one_season.seasons == 1 and user.sponsor_until_season == cm.season + 1
    cm.start_new_season()
    assert user.sponsor_name == one_season.brand                      # yeni sezonda gecerli, hemen bitmedi
    assert cm.facility_status(user)["sponsor"]["seasons_left"] == 1
    assert len(cm.sponsor_offers(user)) == 3
    assert any(f"Mevcut sponsor: {one_season.brand}" in note for note in cm.new_season_notes)


def test_ai_invests_in_the_cheapest_affordable_facility(db):
    cm = _ready(db)
    team = cm.find_team("Madrid Blancos")
    team.youth_facilities, team.medical_facilities = 12, 5
    team.stadium_capacity = fac.STADIUM_MAX
    team.transfer_budget = int(round(fac.facility_upgrade_cost("medical", 5) / AI_FACILITY_BUDGET_SHARE)) + 100
    db.flush()
    cost = cm._ai_invest_in_facilities(team)
    assert cost == fac.facility_upgrade_cost("medical", 5) and team.medical_facilities == 6
    assert team.youth_facilities == 12 and team.stadium_capacity == fac.STADIUM_MAX

    team.transfer_budget = 1_000_000                                 # %5 = 50K: hicbir sey karsilanmaz
    assert cm._ai_invest_in_facilities(team) == 0 and team.medical_facilities == 6
    unconfigured = cm.find_team("Sachsen Bullen")
    unconfigured.stadium_capacity = None
    unconfigured.transfer_budget = 10 ** 10
    assert cm._ai_invest_in_facilities(unconfigured) == 0


# ===========================================================================
# 6) ETKILER: saglik merkezi ve altyapi
# ===========================================================================

def _week_conditions_with_medical(level: int) -> dict:
    """
    Ayri oturumda (rollback) tum kuluplerin saglik merkezi `level` iken haftanin lig maclari oynatilir ve mac sonrasi
    kalicilik (_post_match) yazilir. Hafta ici kupa bilerek atlanir: saglik merkezi hafta ici toparlanmayi da
    degistirdiginden lig maclarinin kendisi farkli olurdu; burada ayni mac, farkli toparlanma olculur.
    """
    from database import SessionLocal

    session = SessionLocal()
    try:
        cm = _ready(session, seed=41)
        session.execute(update(Team).values(medical_facilities=level))
        session.flush()
        session.expire_all()
        week = cm.current_week
        physio = {t.id: cm._staff_rating(t, StaffRole.PHYSIO, "physiotherapy") for t in cm.teams()}
        report = WeekReport(cm.season, week)
        played: dict[int, tuple] = {}
        for fx in cm.fixtures_for_week(week):
            result = play_fixture(session, fx.id, seed=cm.match_seed(fx), persist=True,
                                  config=cm.engine_config, current_week=week)
            cm._post_match(fx, result, week, report)
            played.update({mp.id: (mp.energy, physio[team.id], mp.age)
                           for team in (result.home, result.away) for mp in team.players if mp.played})
        session.flush()
        session.expire_all()
        return {pid: (session.get(Player, pid).condition, *data) for pid, data in played.items()}
    finally:
        session.rollback()
        session.close()


def test_medical_level_measurably_speeds_condition_recovery():
    low = _week_conditions_with_medical(1)
    high = _week_conditions_with_medical(20)
    assert low.keys() == high.keys() and len(low) >= 100
    tired = [pid for pid, (_c, energy, _p, _a) in low.items() if energy < 95]
    assert len(tired) >= 50
    for pid in low:
        cond_low, energy, physio, age = low[pid]
        cond_high, energy_high, _physio, _age = high[pid]
        assert energy == energy_high                                 # ayni mac: saglik merkezi maci etkilemez
        assert cond_low == fitness.recover_condition(energy, physio, age=age,
                                                     medical_multiplier=fac.medical_recovery_multiplier(1))
        assert cond_high == fitness.recover_condition(energy, physio, age=age,
                                                      medical_multiplier=fac.medical_recovery_multiplier(20))
        assert cond_high >= cond_low
    gain = mean(high[pid][0] - low[pid][0] for pid in tired)
    assert gain >= 5, gain


def _intake_potentials(level: int, via_upgrades: bool = False) -> list[int]:
    """Ayri oturumda (rollback) tum kuluplerin altyapisi `level` iken sezonluk genc girisi."""
    from database import SessionLocal

    session = SessionLocal()
    try:
        cm = _ready(session, seed=12)
        user = cm.find_team("Istanbul Lions")
        cm.set_user_team(user)
        session.execute(update(Team).values(youth_facilities=level if not via_upgrades else 1))
        session.flush()
        session.expire_all()
        if via_upgrades:
            session.execute(update(Team).where(Team.id != user.id).values(youth_facilities=level))
            user = session.get(Team, user.id)
            user.transfer_budget = 10 ** 9
            while user.youth_facilities < level:
                cm.upgrade_facility(user, "youth")                   # oyuncunun yolu: yatirim
            session.flush()
            session.expire_all()
        existing = set(session.scalars(select(Player.id)))
        report = WeekReport(cm.season, cm.youth_intake_week())
        cm._youth_intake(cm.youth_intake_week(), report)
        session.flush()
        return [p.potential_rating for p in session.scalars(
            select(Player).where(Player.in_academy.is_(True)).order_by(Player.team_id, Player.id))
            if p.id not in existing]
    finally:
        session.rollback()
        session.close()


def test_youth_facility_level_shifts_intake_potential_upward():
    low = _intake_potentials(1)
    high = _intake_potentials(20, via_upgrades=True)
    assert len(low) >= 3 * 24 and len(high) >= 3 * 24
    assert mean(high) - mean(low) >= 4, (mean(low), mean(high))


# ===========================================================================
# 7) COK KULLANICILI IZOLASYON
# ===========================================================================

ISOLATION_SCHEMA = "test_fac_iso"


def _public_teams_digest(conn) -> str:
    return conn.scalar(text(
        "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM \"public\".\"teams\" t"))


def test_facility_upgrade_and_sponsor_signing_stay_inside_the_career_schema():
    import database
    import seed
    from database import SessionLocal

    with database.engine.connect() as conn:
        public_before = _public_teams_digest(conn)
    with database.career_context(ISOLATION_SCHEMA):
        database.drop_career_schema(ISOLATION_SCHEMA)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=4)
                assert cm.ensure_club_setup()
                team = cm.find_team("Istanbul Lions")
                cm.set_user_team(team)
                level, budget = team.youth_facilities, team.transfer_budget
                cost = cm.upgrade_facility(team, "youth")
                offer = cm.sign_sponsor(team, 1)
                team_id = team.id
                session.commit()
            finally:
                session.close()

            session = SessionLocal()
            try:
                assert session.scalar(text("SELECT current_schema()")) == ISOLATION_SCHEMA
                saved = session.get(Team, team_id)
                assert saved.youth_facilities == level + 1 and saved.sponsor_name == offer.brand
                assert saved.transfer_budget == budget - cost + offer.signing_bonus
                assert saved.sponsor_offers == []
            finally:
                session.close()
        finally:
            database.drop_career_schema(ISOLATION_SCHEMA)

    with database.engine.connect() as conn:
        assert _public_teams_digest(conn) == public_before
    from database import SessionLocal as PublicSession
    with PublicSession() as session:                                  # baglamsiz: public kariyer
        assert session.scalar(select(func.count()).select_from(Team).where(Team.sponsor_name.isnot(None))) == 0
        assert session.scalar(select(func.count()).select_from(Team).where(Team.stadium_capacity.isnot(None))) == 0
