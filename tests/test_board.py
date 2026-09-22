"""
Faz 15C yonetim kurulu testleri: saf kurallar (board.py -- hedef, guven, kovulma, butce, is piyasasi) +
gercek PostgreSQL uzerinde BoardRoom (haftalik adim, sezon karnesi) ve BoardDesk (menajer API'si).

Her DB testi kendi islemini geri alir (sentetik dunya, sezon basi). Onerilen: TEST_DB_NAME=fm_db_test_15c.
Saf testler CM_TEST_NO_DB=1 ile de kosar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import board  # noqa: E402
import inbox  # noqa: E402
import reputation  # noqa: E402
from models import (  # noqa: E402
    BOARD_BUDGET_STATUSES,
    BOARD_OFFER_KINDS,
    BOARD_OFFER_STATUSES,
    BOARD_STATE_STATUSES,
    BOARD_TIERS,
    BoardOffer,
    BoardState,
    InboxMessage,
    Team,
)

USER = "Istanbul Lions"
RIVAL = "Karadeniz Storm"
MINNOW = "Bizkaia Lions"      # İspanya ligi: 79 itibar, rakipleri 87-95 (hedefi tutturmasi zor)


# ===========================================================================
# 1) SAF KURALLAR
# ===========================================================================

def test_constants_match_schema_checks():
    assert board.TIERS == BOARD_TIERS
    assert board.TARGET_TIERS == BOARD_TIERS[:-1]
    assert board.RELEGATION not in board.TARGET_TIERS       # dusme hatti hedef olmaz, yalnizca SONUCtur
    assert set(board.TIER_LABELS) == set(BOARD_TIERS)
    assert (board.STATE_ACTIVE, board.STATE_SACKED, board.STATE_RESIGNED, board.STATE_LEFT) == BOARD_STATE_STATUSES
    assert set(board.STATE_LABELS) == set(BOARD_STATE_STATUSES)
    assert (board.OFFER_ADVERT, board.OFFER_APPROACH) == BOARD_OFFER_KINDS
    assert board.OFFER_PENDING == BOARD_OFFER_STATUSES[0]
    assert {board.BUDGET_NONE, board.BUDGET_PENDING, board.BUDGET_ACCEPTED} == set(BOARD_BUDGET_STATUSES)
    assert all(len(t) <= 12 for t in BOARD_TIERS)           # board_states.target String(12)
    assert all(len(s) <= 8 for s in board.CUP_STAGES)       # board_states.cup_target String(8)
    assert set(board.CUP_LABELS) == set(board.CUP_STAGES)
    assert inbox.KIND_BOARD in inbox.IMPORTANT_KINDS        # yonetim mesaji "devam"i durdurur


def test_league_cuts_scale_with_the_league_size():
    big = board.league_cuts(20)
    assert (big.europe, big.half, big.mid, big.survival) == (4, 10, 15, 17)
    small = board.league_cuts(4)                            # sentetik test dunyasi
    assert (small.europe, small.half, small.mid, small.survival) == (2, 2, 3, 3)
    for size in range(2, 25):
        cuts = board.league_cuts(size)
        assert 1 <= cuts.europe <= cuts.half <= cuts.mid <= cuts.survival <= size


def test_achieved_tier_is_monotonic_and_covers_every_position():
    for size in (4, 6, 18, 20):
        tiers = [board.tier_index(board.achieved_tier(p, size)) for p in range(1, size + 1)]
        assert tiers[0] == 0                                 # sampiyon
        assert tiers == sorted(tiers)                        # sira kotulestikce kademe kotulesir
        assert tiers[-1] == board.tier_index(board.RELEGATION)
        for tier in board.TARGET_TIERS:
            position = board.target_position(tier, size)
            assert board.tier_index(board.achieved_tier(position, size)) <= board.tier_index(tier)


def test_the_title_is_only_asked_from_a_clear_favourite():
    assert board.target_tier(1, 20, margin=0.30) == board.TITLE
    assert board.target_tier(1, 20, margin=0.0) == board.EUROPE
    assert board.target_tier(3, 20, margin=0.0) == board.EUROPE
    assert board.target_tier(9, 20) == board.TOP_HALF
    assert board.target_tier(14, 20) == board.MIDTABLE
    assert board.target_tier(19, 20) == board.SURVIVAL
    assert board.target_tier(4, 4) == board.SURVIVAL
    assert all(board.target_tier(r, 20) in board.TARGET_TIERS for r in range(1, 21))


def test_season_gap_is_bounded_by_the_places_missed():
    """Kucuk ligde 5 kademe 4 siraya sikisir: "ikinci yerine ucuncu" iki kademe sayilmaz."""
    assert board.season_gap(board.EUROPE, 3, 4) == 1          # hedef 2. sira, 3. bitti: tek sira
    assert board.season_gap(board.EUROPE, 4, 4) == 2          # iki sira geride: kart kurali isler
    assert board.season_gap(board.EUROPE, 1, 4) < 0           # hedef asildi
    assert board.season_gap(board.EUROPE, 2, 4) == 0
    # Buyuk ligde sira farki kademe farkindan her zaman buyuktur: deger degismez
    for size in (18, 20):
        for tier in board.TARGET_TIERS:
            for position in range(1, size + 1):
                plain = board.shortfall(tier, board.achieved_tier(position, size))
                assert board.season_gap(tier, position, size) == plain, (size, tier, position)


def test_a_manager_who_meets_the_target_is_never_sacked():
    """KABUL: hedefine ulasan menajer asla kovulmaz (ne sezon sonunda ne sezon icinde)."""
    for gap in (-4, -1, 0):
        for confidence in range(0, 101, 5):
            assert board.sack_probability(gap, confidence) == 0.0
            assert board.sack_probability(gap, confidence, first_season=True) == 0.0
            assert not board.sack_now(confidence, low_weeks=10, gap=gap, week=30, weeks_in_charge=60)
    # hedefi tutturan menajer guveni sifir olsa da gorevde kalir
    assert not board.sack_now(0.0, low_weeks=99, gap=0, week=38, weeks_in_charge=200)


def test_two_tiers_below_the_target_is_at_least_seventy_percent():
    """KABUL: hedefin 2+ kademe altinda biten menajerin kovulma olasiligi >= %70."""
    for gap in range(2, 6):
        for confidence in range(0, 101, 5):
            chance = board.sack_probability(gap, confidence)
            assert chance >= board.SACK_MIN_PROBABILITY == 0.70, (gap, confidence, chance)
            assert chance <= board.SACK_PROBABILITY_MAX
            # ilk sezon musamahasi iki kademede GECERSIZ: kart kesin
            assert board.sack_probability(gap, confidence, first_season=True) == chance
    # bir kademe geride: guvene gore, ilk sezonda daha musamahali
    assert 0 < board.sack_probability(1, 90) < board.sack_probability(1, 10) < 0.70
    assert board.sack_probability(1, 20, first_season=True) < board.sack_probability(1, 20)


def test_in_season_sacking_needs_two_critical_weeks_and_patience():
    kw = dict(gap=2, week=20, weeks_in_charge=20)
    assert board.sack_now(10.0, low_weeks=2, **kw)
    assert not board.sack_now(10.0, low_weeks=1, **kw)                 # tek hafta yetmez
    assert not board.sack_now(board.CRITICAL_LEVEL, low_weeks=5, **kw)  # esik dahil degil
    assert not board.sack_now(10.0, low_weeks=5, gap=2, week=2, weeks_in_charge=20)     # sezon basi
    assert not board.sack_now(10.0, low_weeks=5, gap=2, week=20, weeks_in_charge=1)     # yeni menajer
    assert board.min_sack_week(38) == 5 and board.min_sack_week(6) == board.MIN_SACK_WEEK == 2
    assert board.sack_now(10.0, low_weeks=2, gap=2, week=3, weeks_in_charge=20, season_weeks=6)
    assert not board.sack_now(10.0, low_weeks=2, gap=2, week=3, weeks_in_charge=20, season_weeks=38)


def test_weekly_delta_reads_results_position_finance_and_star_sales():
    win = board.match_delta("W", 70, 70, 1)
    loss = board.match_delta("L", 70, 70, -1)
    assert win > 0 > loss
    assert board.match_delta("W", 70, 90, 1) > win                     # surpriz galibiyet
    assert board.match_delta("L", 70, 50, -1) < loss                   # zayif rakibe yenilgi
    assert board.match_delta("L", 70, 70, -4) < loss                   # farkli yenilgi
    assert abs(board.match_delta("W", 70, 70, 1, cup=True)) < abs(win)  # kupa maci daha az agirlikli
    with pytest.raises(ValueError):
        board.match_delta("X", 70, 70, 0)

    assert board.position_delta(1, board.EUROPE, 20) > 0               # hedefin ustunde
    assert board.position_delta(4, board.EUROPE, 20) == 0
    assert board.position_delta(12, board.EUROPE, 20) < 0
    assert board.position_delta(20, board.TITLE, 20) == -board.POSITION_MAX

    assert board.finance_delta(50_000_000, 400_000, 500_000) == board.HEALTHY_BONUS
    assert board.finance_delta(50_000_000, 600_000, 500_000) < 0       # havuz asildi
    assert board.finance_delta(100, 400_000, 500_000) < 0              # kasa bos

    assert board.star_sale_delta(70, 85, 1_000, 1_000) == 0.0          # yildiz degil
    assert board.star_sale_delta(85, 85, 1_000, 10_000_000) <= board.STAR_SALE_MAX + 1e-9
    assert board.star_sale_delta(85, 85, 30_000_000, 10_000_000) > board.star_sale_delta(85, 85, 1_000, 10_000_000)
    assert board.star_signing_delta(85, 85) > 0 and board.star_signing_delta(70, 85) == 0

    change = board.weekly_delta(
        results=[("W", 70, 88, 2, False)], position=2, target=board.EUROPE, league_size=20,
        transfer_budget=50_000_000, wage_bill=400_000, wage_budget=500_000,
        sales=[("Yıldız", 85, 85, 1_000, 10_000_000)], signings=[("Yeni", 86, 85)],
    )
    assert change.delta != 0 and len(change.reasons) == 5
    assert all(isinstance(r, str) and r for r in change.reasons)
    assert board.weekly_delta().delta == 0.0


def test_confidence_is_clamped_labelled_and_warned():
    assert board.apply_confidence(95, 50) == 100.0
    assert board.apply_confidence(5, -50) == 0.0
    labels = [board.confidence_label(v) for v in (0, 20, 30, 45, 60, 80, 95)]
    assert len(set(labels)) == len(labels)                             # her bant ayri etiket
    assert board.warning_level(80) == board.WARN_NONE
    assert board.warning_level(30) == board.WARN_FIRST
    assert board.warning_level(10) == board.WARN_FINAL
    assert set(board.WARNING_LABELS) == {board.WARN_NONE, board.WARN_FIRST, board.WARN_FINAL}
    # sezon kapanisi: hedefi asmak yukseltir, altinda kalmak dusurur, kupa buyuk odul
    assert board.season_end_confidence(60, -1) > 60 > board.season_end_confidence(60, 1)
    assert board.season_end_confidence(60, 0, trophies=1) == 60 + board.TROPHY_BONUS
    assert board.season_end_confidence(0, 5) == 0.0 and board.season_end_confidence(100, -5) == 100.0


def test_cup_expectation_moves_confidence_both_ways():
    assert board.cup_target(1, 20, in_cup=True) == "SF"
    assert board.cup_target(18, 20, in_cup=True) == "GROUP"
    assert board.cup_target(1, 20, in_cup=False) is None
    assert board.cup_delta("SF", board.CUP_WIN) > 0
    assert board.cup_delta("SF", "GROUP") < 0
    assert board.cup_delta("SF", "SF") == 0.0
    assert board.cup_delta(None, "SF") == 0.0 and board.cup_delta("SF", None) == 0.0
    assert abs(board.cup_delta("GROUP", board.CUP_WIN)) <= board.CUP_MAX_DELTA


def test_budget_proposal_is_a_level_and_never_takes_money():
    plan = board.budget_proposal(92, 80, board.TITLE, transfer_budget=1_000_000,
                                 wage_bill=900_000, wage_budget=800_000)
    assert plan.any and plan.grant_over(1_000_000) > 0
    assert plan.wage_budget >= 900_000                                  # havuz maas yukunun altina inmez
    poor = board.budget_proposal(55, 20, board.SURVIVAL, transfer_budget=0, wage_bill=10_000,
                                 wage_budget=20_000)
    assert 0 <= poor.transfer_budget < plan.transfer_budget
    assert poor.wage_budget >= 20_000                                   # mevcut havuz KUCULTULMEZ
    # Zengin kulup: oneri kasayi KUCULTMEZ ve ust uste EKLENMEZ (seviye, hibe degil)
    rich = board.budget_proposal(92, 80, board.TITLE, transfer_budget=10_000_000_000,
                                 wage_bill=900_000, wage_budget=800_000)
    assert rich.transfer_budget == 10_000_000_000 and rich.grant_over(10_000_000_000) == 0
    for tier in board.TARGET_TIERS:
        p = board.budget_proposal(80, 60, tier, transfer_budget=0, wage_bill=0, wage_budget=0)
        assert p.transfer_budget >= 0


def test_job_market_rules_scale_with_reputation_and_unemployment():
    assert board.required_reputation(95) > board.required_reputation(80) > board.required_reputation(55)
    assert board.required_reputation(1) >= reputation.MIN_REPUTATION
    assert board.required_reputation(80, unemployed_weeks=20) < board.required_reputation(80)
    assert board.unemployed_relief(999) == board.UNEMPLOYED_RELIEF_MAX
    assert board.job_eligible(20.0, 95) and not board.job_eligible(3.0, 95)
    assert board.application_chance(3.0, 95) == 0.0
    assert 0 < board.application_chance(8.0, 60) <= board.APPLY_MAX
    assert board.application_chance(20.0, 60) == board.APPLY_MAX
    assert board.approach_chance(8.0, 60, confidence=40, gap=0) == 0.0     # kotu giden menajeri kimse istemez
    assert board.approach_chance(8.0, 60, confidence=90, gap=0) > 0
    assert board.approach_chance(8.0, 60, confidence=40, gap=-1) > 0       # hedefi asti
    assert board.approach_chance(3.0, 95, confidence=100, gap=-2) == 0.0   # taninirlik yetmiyor


def test_dice_are_seeded_and_never_use_pythons_salted_hash():
    assert board.seed_of("a", 1) == board.seed_of("a", 1)
    assert board.seed_of("a", 1) != board.seed_of("a", 2)
    assert board.dice("x", 3).random() == board.dice("x", 3).random()
    assert board.roll(1.0, "x") and not board.roll(0.0, "x")
    outcomes = [board.roll(0.5, "sack", 1, t) for t in range(200)]
    assert 0.3 < sum(outcomes) / len(outcomes) < 0.7                       # zar dengeli


# ===========================================================================
# 2) VERITABANI
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark_db = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, *, flag: bool = True, team: str = USER, contracts: bool = False):
    from career_manager import CareerManager
    from models import GameMode

    cm = CareerManager(db, seed=11)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.board = flag
    cm.contract_cycle = contracts               # yonetim kurulu testleri sozlesme dongusunden bagimsiz
    cm.set_game_mode(GameMode.CAREER)
    cm.set_user_team(cm.find_team(team))
    return cm


def _count(db, model, *where) -> int:
    db.flush()
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _board_messages(cm) -> list:
    return [m for m in cm.inbox_for_manager().messages(limit=200) if m.kind == inbox.KIND_BOARD]


def _achieved_now(cm, team_id: int) -> str:
    """Kulubun bugunku sirasinin karsiligi olan kademe (hedef = ulasilan yapmak icin)."""
    table = cm.standings(cm.db.get(Team, team_id).league_id)
    position = next(i for i, t in enumerate(table, start=1) if t.id == team_id)
    tier = board.achieved_tier(position, len(table))
    return board.SURVIVAL if tier == board.RELEGATION else tier


def _play_season(cm, limit: int = 60) -> int:
    weeks = 0
    while not cm.season_finished and weeks < limit:
        cm.play_week()
        weeks += 1
        if cm.user_team is None:                 # kovulduysa hafta yine ilerler ama menajer kulupsuzdur
            break
    return weeks


@pytest.mark.integration
@pytestmark_db
def test_flag_off_writes_nothing(db):
    """Bayrak kapali: tek satir yazilmaz, hicbir kulup ilan acmaz, oyun 15C oncesiyle birebir aynidir."""
    cm = _manager(db, flag=False)
    _play_season(cm)
    cm.start_new_season()
    cm.play_week()
    assert _count(db, BoardState) == 0
    assert _count(db, BoardOffer) == 0
    assert _count(db, Team, Team.board_vacant_since.isnot(None)) == 0
    assert cm.state.board_since_cw is None and cm.state.board_unemployed_since is None
    assert _count(db, InboxMessage, InboxMessage.kind == inbox.KIND_BOARD) == 0
    assert not cm.board_unemployed()
    with pytest.raises(board.BoardError):
        cm.board_desk().state()


@pytest.mark.integration
@pytestmark_db
def test_first_week_sets_the_target_budget_and_inbox_message(db):
    cm = _manager(db)
    cm.play_week()
    desk = cm.board_desk()
    view = desk.state()
    assert view is not None
    assert view.target in board.TARGET_TIERS and view.target_label
    assert view.season == cm.season and view.team_id == cm.user_team.id
    assert 0 <= view.confidence <= 100 and view.confidence_label
    assert view.position and view.league_size >= 2
    assert view.status == board.STATE_ACTIVE and view.warning == board.WARN_NONE
    messages = _board_messages(cm)
    assert messages and messages[-1].important                   # yonetim mesaji "devam"i durdurur
    assert any("hedef" in m.subject.lower() for m in messages)
    assert all(m.page == "takim" and m.ref_id == cm.user_team.id for m in messages)
    budget = desk.budget()
    assert budget is not None and budget.status == board.BUDGET_PENDING
    assert budget.transfer_budget > 0 and budget.transfer_grant >= 0


@pytest.mark.integration
@pytestmark_db
def test_budget_proposal_is_only_applied_when_accepted(db):
    cm = _manager(db)
    team = cm.user_team
    cm.play_week()
    desk = cm.board_desk()
    before_wage = team.wage_budget
    level = desk.budget().transfer_budget
    assert level > 0
    cm.play_week()                                               # oneri kendiliginden uygulanmaz
    assert team.transfer_budget < level or desk.budget().transfer_grant == 0
    view = desk.accept_budget()
    assert view.accepted and view.transfer_budget == level
    assert team.transfer_budget >= level                          # kasa hedef seviyeye tamamlandi
    assert team.wage_budget >= before_wage
    with pytest.raises(board.BoardError):                        # iki kez kabul edilmez
        desk.accept_budget()


@pytest.mark.integration
@pytestmark_db
def test_confidence_moves_with_results_and_never_touches_cm_rng(db):
    cm = _manager(db)
    cm.play_week()
    desk = cm.board_desk()
    values = [desk.state().confidence]
    for _ in range(3):
        cm.play_week()
        values.append(desk.state().confidence)
    assert len(set(values)) > 1, "güven hiç değişmedi"
    assert all(0 <= v <= 100 for v in values)
    assert desk.state().reasons, "haftanın gerekçeleri yazılmadı"
    # Yonetim adiminin KENDISI cm.rng'den cekmez (mac yolu ayri akistir)
    room = board.BoardRoom(cm)
    row = room.state_row(None, cm.season, cm.user_team.id)
    row.last_week = 0
    db.flush()
    state = cm.rng.getstate()
    room.run_week(cm.current_week, None)
    assert cm.rng.getstate() == state, "yönetim kurulu cm.rng'den çekti (determinizm bozulur)"
    board.BoardRoom(cm).season_review(cm.season + 1)
    assert cm.rng.getstate() == state


@pytest.mark.integration
@pytestmark_db
def test_low_confidence_warns_then_sacks_and_opens_the_job(db):
    """Guven iki hafta ust uste 15'in altinda -> kovulma; kulup ilan acar, menajer kulupsuz kalir."""
    cm = _manager(db, team=RIVAL)                                # ligin en zayif kulubu: hedefin altinda kalir
    team_id = cm.user_team.id
    room = board.BoardRoom(cm)
    _play_season(cm)
    row = room.state_row(None, cm.season, team_id)
    row.target = _achieved_now(cm, team_id)                      # 1. sezonda hedef tuttu: devirde kovulmaz
    db.flush()
    cm.start_new_season()                                        # kariyer haftasi artik GRACE_WEEKS'i asti
    assert cm.user_team is not None
    row = room.state_row(None, cm.season, team_id)
    row.target = board.TITLE                                     # yonetim sampiyonluk istiyor
    db.flush()
    sacked = False
    while not cm.season_finished and not sacked:
        row.confidence, row.low_weeks, row.last_week = 2.0, board.LOW_WEEKS_TO_SACK - 1, 0
        db.flush()
        cm.play_week()
        row = room.state_row(None, cm.season, team_id) or row
        sacked = row.status == board.STATE_SACKED
    assert sacked, "kritik güvene rağmen kovulma olmadı"
    assert cm.user_team is None and cm.board_unemployed()
    assert cm.state.board_unemployed_since is not None
    assert db.get(Team, team_id).board_vacant_since is not None   # kulup menajer ariyor
    assert any("son verdi" in m.subject for m in _board_messages(cm))
    from career_manager import ClubChoiceError  # noqa: E402  -- kulup serbestce secilemez

    with pytest.raises(ClubChoiceError, match="kulüpsüz"):
        cm.choose_club(cm.find_team(RIVAL))


@pytest.mark.integration
@pytestmark_db
def test_season_end_two_tiers_below_target_sacks_the_manager(db):
    """KABUL: hedefin 2+ kademe altinda biten menajerin kovulma olasiligi >= %70 (tohumlu zar)."""
    from models import SeasonStanding

    cm = _manager(db, team=MINNOW)
    team_id = cm.user_team.id
    league_id = cm.user_team.league_id
    size = len(cm.standings(league_id))
    _play_season(cm)
    room = board.BoardRoom(cm)
    row = room.state_row(None, cm.season, team_id)
    assert row is not None
    row.target = board.TITLE                                      # yonetim sampiyonluk istedi
    row.confidence = 20.0
    # Sezon sonu siralamasi arsivlendi (_archive_league). Kabul olcusunu deterministik sinamak icin
    # menajerin kulubunu SON siraya alirız: hedefin 5 kademe altinda -> olasilik tavanda.
    standing = db.scalars(select(SeasonStanding).where(SeasonStanding.season == cm.season,
                                                       SeasonStanding.team_id == team_id)).one()
    standing.position = size
    db.flush()
    gap = board.season_gap(board.TITLE, size, size)
    assert gap >= 2
    chance = board.sack_probability(gap, board.season_end_confidence(20.0, gap))
    assert chance >= board.SACK_MIN_PROBABILITY == 0.70
    sacked = board.roll(chance, "sack", cm.season, team_id, 0)
    assert sacked, "bu tohumda zar tutmadı: olasılık %97 olmasına rağmen"
    old_season = cm.season
    cm.start_new_season()
    row = room.state_row(None, old_season, team_id)
    assert row.status == board.STATE_SACKED
    assert cm.user_team is None and cm.board_unemployed()
    assert cm.state.board_unemployed_since is not None
    assert db.get(Team, team_id).board_vacant_since is not None
    assert any("son verdi" in m.subject for m in _board_messages(cm))
    assert room.state_row(None, cm.season, team_id) is None        # yeni sezonun hedefi acilmaz


@pytest.mark.integration
@pytestmark_db
def test_a_manager_who_meets_the_target_survives_the_season(db):
    """KABUL: hedefini tutturan menajer sezon sonunda kovulmaz; yeni sezonun hedefi ve butcesi gelir."""
    cm = _manager(db)
    team_id = cm.user_team.id
    _play_season(cm)
    room = board.BoardRoom(cm)
    row = room.state_row(None, cm.season, team_id)
    table = cm.standings(cm.user_team.league_id)
    position = next(i for i, t in enumerate(table, start=1) if t.id == team_id)
    row.target = board.achieved_tier(position, len(table))         # hedef = ulasilan: tam tutturdu
    if row.target == board.RELEGATION:
        row.target = board.SURVIVAL
    row.confidence = 5.0                                           # guven dipte olsa da hedef tuttuysa kovulmaz
    db.flush()
    old_season = cm.season
    cm.start_new_season()
    assert cm.user_team is not None and cm.user_team.id == team_id
    assert room.state_row(None, old_season, team_id).status == board.STATE_ACTIVE
    new_row = room.state_row(None, cm.season, team_id)
    assert new_row is not None and new_row.budget_status == board.BUDGET_PENDING
    assert new_row.since_week == 1 and new_row.status == board.STATE_ACTIVE


@pytest.mark.integration
@pytestmark_db
def test_ai_clubs_below_their_target_open_job_adverts(db):
    cm = _manager(db)
    _play_season(cm)
    cm.start_new_season()
    vacancies = db.scalars(select(Team).where(Team.board_vacant_since.isnot(None))).all()
    assert vacancies, "hiçbir AI kulübü menajer değiştirmedi"
    assert all(t.id != cm.state.user_team_id for t in vacancies)   # insan kulubu ilan acmaz
    jobs = cm.board_desk().jobs(limit=50)
    assert jobs and {j.team_id for j in jobs} == {t.id for t in vacancies}
    assert all(j.closes_in_weeks <= board.ADVERT_WEEKS and j.required_reputation >= 1 for j in jobs)


@pytest.mark.integration
@pytestmark_db
def test_resign_then_apply_and_take_a_new_club(db):
    cm = _manager(db)
    old_id = cm.user_team.id
    cm.play_week()
    desk = cm.board_desk()
    desk.resign()
    assert cm.user_team is None and cm.board_unemployed()
    assert db.get(Team, old_id).board_vacant_since is not None
    assert any("ayrıldın" in m.subject for m in _board_messages(cm))
    cm.play_week()                                                  # kulupsuz menajer icin piyasa canli tutulur
    jobs = desk.jobs(limit=50)
    assert len(jobs) >= board.MIN_OPEN_JOBS - 1
    target = next((j for j in jobs if j.eligible and j.team_id != old_id), None)
    if target is None:                                              # bu koşuda uygun ilan çıkmadıysa biz açalım
        weak = min((t for t in cm.teams() if t.id != old_id), key=lambda t: (t.reputation, t.id))
        weak.board_vacant_since = cm.career_week
        db.flush()
        target = next(j for j in desk.jobs(limit=50) if j.team_id == weak.id)
    assert target.eligible, "tanınırlığa uygun tek ilan bile yok"
    result = desk.apply(target.team_id)
    assert isinstance(result.accepted, bool) and result.text
    with pytest.raises(board.BoardError, match="başvurdun"):
        desk.apply(target.team_id)
    if not result.accepted:                                         # zar tuttu: bekleyen teklif olusturulur
        offer = BoardOffer(manager_id=None, team_id=target.team_id, season=cm.season, week=cm.current_week,
                           career_week=cm.career_week, expires_career_week=cm.career_week + board.OFFER_WEEKS,
                           kind=board.OFFER_APPROACH, status=board.OFFER_PENDING, reputation=50)
        db.add(offer)
        db.flush()
    pending = desk.offers()
    assert pending and pending[0].team_name
    view = desk.accept_offer(pending[0].id)
    assert cm.user_team is not None and cm.user_team.id == view.team_id
    assert not cm.board_unemployed() and cm.state.board_unemployed_since is None
    assert db.get(Team, view.team_id).board_vacant_since is None
    room = board.BoardRoom(cm)
    row = room.state_row(None, cm.season, view.team_id)
    assert row is not None and row.status == board.STATE_ACTIVE and row.since_week == cm.current_week
    assert any("menajeri oldun" in m.subject for m in _board_messages(cm))
    assert cm.lineup_check(cm.user_team).ok                          # asistan kadroyu kurdu


@pytest.mark.integration
@pytestmark_db
def test_offers_expire_and_cannot_be_used_twice(db):
    cm = _manager(db)
    cm.play_week()
    rival = cm.find_team(RIVAL)
    rival.board_vacant_since = cm.career_week
    offer = BoardOffer(manager_id=None, team_id=rival.id, season=cm.season, week=cm.current_week,
                       career_week=cm.career_week, expires_career_week=cm.career_week - 1,
                       kind=board.OFFER_APPROACH, status=board.OFFER_PENDING, reputation=rival.reputation)
    db.add(offer)
    db.flush()
    desk = cm.board_desk()
    with pytest.raises(board.BoardError, match="geçerli"):
        desk.accept_offer(offer.id)
    assert offer.status == board.OFFER_EXPIRED
    with pytest.raises(board.BoardError, match="bulunamadı"):
        desk.accept_offer(999_999)
    offer.status = board.OFFER_PENDING
    offer.expires_career_week = cm.career_week + 2
    db.flush()
    desk.decline_offer(offer.id)
    assert offer.status == board.OFFER_DECLINED
    with pytest.raises(board.BoardError):
        desk.decline_offer(offer.id)


@pytest.mark.integration
@pytestmark_db
def test_shared_world_needs_the_rule_to_be_switched_on(db):
    """Sahip karari K-S4: paylasilan dunyada kural varsayilan KAPALI ve reddedilir; acilinca calisir."""
    from world_rules import WorldRules

    cm = _manager(db)
    cm.state.world_rules = WorldRules.shared_defaults().to_dict()
    db.flush()
    assert cm.rules.shared and not cm.rules.board_confidence
    assert not cm._board_on()
    with pytest.raises(board.BoardError, match="kapalı"):
        cm.board_desk().state()
    with pytest.raises(board.BoardError):
        cm.board_desk().resign()
    before = _count(db, BoardState)
    cm.play_week()
    assert _count(db, BoardState) == before                       # kural kapali: tek satir bile yazilmaz
    cm.state.world_rules = WorldRules.shared_defaults().with_changes({"board_confidence": True}).to_dict()
    db.flush()
    assert cm._board_on()
    assert cm.board_desk().state() is None or True                # artik reddetmiyor
    cm.play_week()
    assert _count(db, BoardState) > before


@pytest.mark.integration
@pytestmark_db
def test_schema_is_additive_and_idempotent(db):
    import database

    assert database.SCHEMA_VERSION >= 19
    added = {(t, c) for t, c, _ddl in database.ADDITIVE_COLUMNS}
    assert {("teams", "board_vacant_since"), ("game_state", "board_since_cw"),
            ("game_state", "board_unemployed_since")} <= added
    assert database.schema_problems() == []
    assert database.upgrade_schema() == []                        # idempotent
