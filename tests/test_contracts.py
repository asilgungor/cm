"""
Faz 15A sozlesme dongusu testleri: saf kurallar (contracts.py, transfers.agent_demands) + gercek PostgreSQL uzerinde
ContractCycle (AI yenileme, on sozlesme, serbest oyuncu, sezon devri) ve ContractDesk (menajer API'si).

Her DB testi kendi islemini geri alir (sentetik dunya, sezon basi). Onerilen: TEST_DB_NAME=fm_db_test_15a.
Saf testler CM_TEST_NO_DB=1 ile de kosar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import contracts  # noqa: E402
import squad_planner  # noqa: E402
import transfers  # noqa: E402
from models import (  # noqa: E402
    CONTRACT_TALK_KINDS,
    CONTRACT_TALK_STATUSES,
    RELEASED_TEAM_NAME,
    Position,
    SquadRole,
    TransferKind,
)

USER = "Istanbul Lions"
RIVAL = "Karadeniz Storm"              # ayni lig: bilgi 35


# ===========================================================================
# 1) SAF KURALLAR
# ===========================================================================

def test_constants_match_schema_checks():
    assert CONTRACT_TALK_KINDS == contracts.TALK_KINDS
    assert CONTRACT_TALK_STATUSES == contracts.TALK_STATUSES
    assert set(contracts.LIVE_TALK_STATUSES) <= set(contracts.TALK_STATUSES)
    assert set(contracts.KIND_LABELS) == set(contracts.TALK_KINDS)
    assert set(contracts.STATUS_LABELS) == set(contracts.TALK_STATUSES)
    assert all(len(k.value) <= 12 for k in TransferKind)            # transfer_log.kind String(12)
    assert contracts.SQUAD_MAX == 25


def test_calendar_scales_with_season_length():
    assert contracts.pre_contract_start(7) == 4
    assert contracts.pre_contract_start(38) == 20
    assert contracts.pre_contract_start(1) == 2
    assert not contracts.pre_contract_open(19, 38)
    assert contracts.pre_contract_open(20, 38)
    assert contracts.pre_contract_open(3, 38, season_finished=True)
    weeks = {contracts.renewal_decision_week(team_id, 1, 38) for team_id in range(1, 200)}
    assert min(weeks) >= 1 and max(weeks) <= 19 and len(weeks) > 10          # sezona yayilir
    assert contracts.renewal_decision_week(5, 3, 38) == contracts.renewal_decision_week(5, 3, 38)
    assert {contracts.renewal_decision_week(t, 1, 7) for t in range(50)} <= {1, 2, 3}


def test_contract_semantics_match_squad_planner():
    for season in (1, 4):
        for years in range(0, 7):
            assert contracts.expiry_season(season, years) == squad_planner.contract_expiry_season(season, years)
            assert contracts.expiring(years) == squad_planner.contract_ends_this_season(years)
    assert contracts.renewal_contract_years(1) == 2
    assert contracts.renewal_contract_years(3) == 4
    assert contracts.renewal_contract_years(5) == contracts.MAX_CONTRACT_YEARS == 6


def _shape(**counts) -> contracts.SquadShape:
    pairs = []
    for pos_name, spec in counts.items():
        n, rating = spec
        pairs += [(Position[pos_name], rating)] * n
    return contracts.SquadShape.of(pairs)


def test_squad_shape_counts_needs_and_ranks():
    shape = contracts.SquadShape.of([(Position.GK, 70), (Position.DEF, 75), (Position.DEF, 68), (Position.MID, 72)])
    assert shape.size == 4 and shape.counts[Position.DEF] == 2 and shape.weakest(Position.DEF) == 68
    assert shape.rank_of(Position.DEF, 80) == 0 and shape.rank_of(Position.DEF, 70) == 1
    assert shape.urgent_positions()[0] is Position.MID                         # 4 eksik
    assert set(shape.urgent_positions()) == set(Position)
    full = _shape(GK=(3, 70), DEF=(7, 70), MID=(7, 70), FWD=(5, 70))
    assert full.urgent_positions() == [] and full.size == 22


def test_club_renewal_score_rules():
    full = _shape(GK=(3, 70), DEF=(7, 70), MID=(7, 70), FWD=(5, 70))
    one_keeper = _shape(GK=(1, 70), DEF=(7, 70), MID=(7, 70), FWD=(5, 70))
    small = _shape(GK=(2, 70), DEF=(5, 70), MID=(5, 70), FWD=(3, 70))
    assert contracts.club_renewal_score(age=34, overall=60, potential=60, position=Position.GK, shape=one_keeper).must
    assert contracts.club_renewal_score(age=30, overall=60, potential=60, position=Position.MID, shape=small).must
    assert not contracts.club_renewal_score(age=36, overall=60, potential=60, position=Position.MID, shape=small).must
    young = contracts.club_renewal_score(age=21, overall=68, potential=80, position=Position.MID, shape=full)
    veteran = contracts.club_renewal_score(age=34, overall=68, potential=68, position=Position.MID, shape=full)
    assert young.probability > veteran.probability
    strong = contracts.club_renewal_score(age=27, overall=78, potential=78, position=Position.DEF, shape=full)
    weak = contracts.club_renewal_score(age=27, overall=62, potential=62, position=Position.DEF, shape=full)
    assert 0.0 < weak.probability < strong.probability < 1.0
    assert strong.reason == "ilk 11 oyuncusu"


def test_renewal_attitude_refusals_and_demands():
    happy = contracts.renewal_attitude(overall=70, club_reputation=80, manager_reputation=12)
    assert not happy.refuses and happy.wage_multiplier <= 1.0
    assert contracts.renewal_attitude(overall=70, club_reputation=80, manager_reputation=12, wants_away=True).refuses
    assert contracts.renewal_attitude(overall=70, club_reputation=80, manager_reputation=12, concern_level=3).refuses
    outgrown = contracts.renewal_attitude(overall=90, club_reputation=45, manager_reputation=3)
    assert outgrown.refuses and outgrown.reason == contracts.REFUSE_OUTGROWN
    unhappy = contracts.renewal_attitude(overall=70, club_reputation=80, manager_reputation=12, concern_level=2)
    assert unhappy.wage_multiplier > happy.wage_multiplier and unhappy.level == "DEMANDING"


def test_renewal_wage_basis_and_free_agent_desperation():
    assert contracts.renewal_wage_basis(50_000, 27, 75, 75) == 50_000
    assert contracts.renewal_wage_basis(50_000, 33, 75, 75) == 40_000
    assert contracts.renewal_wage_basis(50_000, 27, 70, 75) == 45_000             # gucu dustu
    assert contracts.desperation_points(0) == 0 and contracts.desperation_points(9) == 4
    assert contracts.desperation_points(200) == contracts.DESPERATION_MAX
    assert contracts.free_agent_wage_factor(0) == 1.0
    assert contracts.free_agent_wage_factor(100) == contracts.WAGE_FLOOR
    assert contracts.expectation_overall(70, 10) == 65
    assert contracts.weeks_free(None, 50) == 0 and contracts.weeks_free(40, 50) == 10


def test_termination_compensation_is_share_of_remaining_wages():
    # 38 haftalik sezon, 20. hafta: bu sezondan 52 x 19/38 = 26 hafta + 1 sezon (52) = 78 hafta
    assert contracts.remaining_contract_weeks(2, 20, 38, False) == pytest.approx(78.0)
    assert contracts.termination_compensation(10_000, 2, 20, 38) == int(round(0.5 * 10_000 * 78 / 1000) * 1000)
    assert contracts.termination_compensation(10_000, 1, 38, 38, season_finished=True) == 0
    assert contracts.termination_compensation(10_000, 3, 1, 38) > contracts.termination_compensation(10_000, 3, 30, 38)


def test_target_fit_prefers_need_and_youth():
    thin = _shape(GK=(2, 70), DEF=(4, 70), MID=(7, 70), FWD=(5, 70))
    assert contracts.target_fit(overall=70, age=25, position=Position.DEF, shape=thin) > \
        contracts.target_fit(overall=70, age=25, position=Position.MID, shape=thin)
    assert contracts.target_fit(overall=72, age=24, position=Position.MID, shape=thin) > \
        contracts.target_fit(overall=72, age=34, position=Position.MID, shape=thin)


def test_agent_demands_free_signing_flag_keeps_legacy_default():
    assert transfers.agent_demands(10_000, 0) == transfers.agent_demands(10_000, 0, None)
    assert transfers.agent_demands(10_000, 5_000_000) == transfers.agent_demands(10_000, 5_000_000, None)
    free, _agent = transfers.agent_demands(10_000, 0)
    renewal, _agent2 = transfers.agent_demands(10_000, 0, False)
    assert free == 3 * renewal == 3 * 10_000 * transfers.SIGNING_FEE_WEEKS


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


def _manager(db, *, flag: bool = True, team: str = USER):
    from career_manager import CareerManager
    from models import GameMode

    cm = CareerManager(db, seed=7)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.contract_cycle = flag
    cm.set_game_mode(GameMode.CAREER)
    cm.set_user_team(cm.find_team(team))
    return cm


def _desk(cm):
    from transfer_desk import ContractDesk

    return ContractDesk(cm)


def _play_season(cm) -> None:
    for _ in range(12):
        if cm.season_finished:
            break
        cm.play_week()
    assert cm.season_finished


def _set_week(cm, week: int) -> None:
    cm.state.current_week = week
    cm.db.flush()


def _count(db, model, *where) -> int:
    db.flush()
    return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


@pytest.mark.integration
def test_flag_off_changes_nothing(db):
    """Bayrak kapali: tam sezon + devir boyunca hic gorusme / serbest birakma yok, sozlesme eskisi gibi erir."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import ContractTalk, GameState, Player, TransferLog

    cm = _manager(db, flag=False)
    _play_season(cm)
    before = {p.id: (p.contract_years, p.team_id) for p in db.scalars(select(Player))}
    cm.start_new_season()
    assert _count(db, ContractTalk) == 0
    assert _count(db, TransferLog, TransferLog.kind.in_(("RELEASED", "BOSMAN", "TERMINATED"))) == 0
    assert _count(db, Player, Player.team_id.is_(None)) == 0
    assert db.get(GameState, 1).contracts_since_cw is None
    for p in db.scalars(select(Player)):
        if p.id in before:
            assert p.contract_years == max(0, before[p.id][0] - 1)
    assert cm._contract_hold_map() == {}



@pytest.mark.integration
def test_release_player_keeps_row_and_logs(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import NewsItem, Player, TransferLog

    cm = _manager(db)
    rival = cm.find_team(RIVAL)
    player = sorted(rival.players, key=lambda p: (p.overall_rating, p.id))[0]
    pid, wage = player.id, player.current_wage
    log = cm.release_player(player, news=True)
    db.expire_all()
    player = db.get(Player, pid)
    assert player is not None and player.team_id is None                 # delete-orphan: satir SILINMEDI
    assert player.free_agent_since == cm.career_week and player.current_wage == 0 and player.contract_years == 0
    assert pid not in {p.id for p in rival.players}
    row = db.get(TransferLog, log.id)
    assert row.kind == "RELEASED" and row.to_team_id is None and row.to_team_name == RELEASED_TEAM_NAME
    assert row.wage == wage and row.from_team_id == rival.id
    assert _count(db, NewsItem, NewsItem.kind == "CONTRACT") >= 1


@pytest.mark.integration
def test_sign_free_agent_and_bosman_log(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import Player, TransferLog
    from transfers import ContractOffer

    cm = _manager(db)
    rival = cm.find_team(RIVAL)
    buyer = cm.find_team("Kadıköy Canaries")
    player = sorted(rival.players, key=lambda p: (p.overall_rating, p.id))[0]
    cm.release_player(player)
    news = cm.sign_free_agent(buyer, player, ContractOffer(wage=12_000, years=2, role=SquadRole.BACKUP))
    db.expire_all()
    player = db.get(Player, player.id)
    assert player.team_id == buyer.id and player.contract_years == 2 and player.current_wage == 12_000
    assert player.free_agent_since is None and news.kind == "FREE_AGENT" and news.fee == 0
    kinds = [r.kind for r in db.scalars(select(TransferLog).where(TransferLog.player_id == player.id)
                                        .order_by(TransferLog.id))]
    assert kinds == ["RELEASED", "FREE_AGENT"]
    assert cm._transfer_news_text(news).startswith(f"Transfer: {player.name} serbest oyuncu olarak")


@pytest.mark.integration
def test_ai_renewal_decisions_and_rng_isolation(db):
    """AI kararlari on sozlesme donemine kadar verilir; cm.rng'den cekilmez; zorunlu kaleci yenilenir."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import transfer_desk
    from career_manager import WeekReport
    from models import ContractTalk, Player

    cm = _manager(db)
    state = cm.rng.getstate()
    report = WeekReport(season=cm.season, week=1)
    with cm._seat_snapshot():
        transfer_desk.ContractCycle(cm, report)._guarded("t", lambda: None)
        _set_week(cm, 3)                                       # son karar haftasi: herkes karar alir
        transfer_desk.run_contract_week(cm, 3, report)
    assert cm.rng.getstate() == state
    humans = cm.human_team_ids()
    expiring_ai = [p for p in db.scalars(select(Player).where(Player.team_id.isnot(None), Player.in_academy.is_(False),
                                                              Player.loan_from_team_id.is_(None)))
                   if p.team_id not in humans]
    decided = {t.player_id: t for t in db.scalars(select(ContractTalk).where(
        ContractTalk.kind == contracts.KIND_RENEWAL, ContractTalk.season == cm.season))}
    for p in expiring_ai:
        talk = decided.get(p.id)
        if talk is None:
            assert not contracts.expiring(p.contract_years)
            continue
        assert talk.human_team_id is None and talk.status in (contracts.SIGNED, contracts.DECLINED, contracts.REFUSED)
        if talk.status == contracts.SIGNED:
            assert p.contract_years >= 2 and talk.contract["wage"] == p.current_wage
    assert decided, "sentetik dunyada sozlesmesi biten AI oyuncusu olmali"
    assert cm.state.contracts_since_cw == cm.career_week


@pytest.mark.integration
def test_renewal_desk_flow_payments_and_cooldown(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import ContractTalk, TransferPayment
    from transfer_desk import DeskError
    from transfers import NegotiationStatus

    cm = _manager(db)
    desk = _desk(cm)
    user = cm.user_team
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    rows = [r for r in desk.contracts() if r.can_renew and not r.in_academy and r.attitude_label != "Yenilemek istemiyor"]
    assert rows and all(r.termination_cost is not None for r in rows)
    row = rows[0]
    step = desk.open_renewal(row.player_id)
    assert step.status is NegotiationStatus.OPEN and step.demand is not None and step.kind == contracts.KIND_RENEWAL
    assert desk.open_renewal(row.player_id).talk_id == step.talk_id          # ayni masa yeniden acilir
    before_budget = user.transfer_budget
    done = desk.submit(step.talk_id, step.demand)
    assert done.status is NegotiationStatus.ACCEPTED and done.talk_status == contracts.SIGNED, done.message
    player = db.get(type(cm.find_team(USER).players[0]), row.player_id)
    assert player.current_wage == step.demand.wage
    assert player.contract_years == max(contracts.renewal_contract_years(step.demand.years), 2)
    assert player.contract_clauses.get("talk_id") == step.talk_id
    paid = {r.kind: r for r in db.scalars(select(TransferPayment).where(TransferPayment.ref.like(f"K{step.talk_id}#%")))}
    assert paid["SIGNING"].status == "PAID" and paid["SIGNING"].amount == step.demand.signing_fee
    assert user.transfer_budget == before_budget - step.demand.signing_fee - step.demand.agent_fee
    assert desk.talk(step.talk_id).status == contracts.SIGNED
    # ikinci oyuncu: hakaret teklifi -> masadan kalkar, bekleme suresi
    other = rows[1]
    step2 = desk.open_renewal(other.player_id)
    insult = transfers.ContractOffer(wage=100, years=step2.demand.years, role=step2.demand.role,
                                     signing_fee=0, agent_fee=step2.demand.agent_fee)
    walked = desk.submit(step2.talk_id, insult)
    assert walked.status is NegotiationStatus.WALKED_AWAY and walked.talk_status == contracts.COLLAPSED
    with pytest.raises(DeskError, match="görüşmek istemiyor"):
        desk.open_renewal(other.player_id)
    statuses = {r.player_id: r.status for r in desk.contracts()}
    if contracts.expiring(other.contract_years):
        assert statuses[other.player_id] == contracts.ROW_REFUSED
    assert _count(db, ContractTalk, ContractTalk.human_team_id == user.id) == 2


@pytest.mark.integration
def test_renewal_waits_for_wage_room_then_signs_with_shift(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from transfer_desk import DeskError

    cm = _manager(db)
    desk = _desk(cm)
    user = cm.user_team
    user.transfer_budget += 80_000_000
    user.wage_budget = user.wage_bill                               # maas havuzunda yer yok
    db.flush()
    row = next(r for r in desk.contracts() if r.can_renew and not r.in_academy
               and r.attitude_label != "Yenilemek istemiyor")
    step = desk.open_renewal(row.player_id)
    raised = transfers.ContractOffer(**{**step.demand.__dict__, "wage": step.demand.wage + 30_000})
    agreed = desk.submit(step.talk_id, raised)
    assert agreed.talk_status == contracts.AGREED and agreed.needs_room and agreed.needs_room > 0
    with pytest.raises(DeskError, match="Maaş havuzunda yer yok"):
        desk.sign(step.talk_id)
    signed = desk.sign(step.talk_id, shift_wage_room=True)
    assert signed.talk_status == contracts.SIGNED


@pytest.mark.integration
def test_free_agent_desk_signing(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import Player, TransferLog
    from transfers import NegotiationStatus

    cm = _manager(db)
    desk = _desk(cm)
    user = cm.user_team
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    rival = cm.find_team(RIVAL)
    player = sorted(rival.players, key=lambda p: (p.overall_rating, p.id))[0]
    cm.release_player(player)
    rows = desk.free_agents()
    assert [r.player_id for r in rows] == [player.id]
    assert rows[0].previous_club == RIVAL and rows[0].knowledge >= contracts.FREE_AGENT_KNOWLEDGE
    assert rows[0].overall is not None and rows[0].can_approach
    step = desk.open_free_agent(player.id)
    if step.status is not NegotiationStatus.OPEN:
        pytest.skip(f"oyuncu masaya oturmadi: {step.message}")
    # serbest imza: imza primi talebi bonservissiz (x3)
    assert step.demand.signing_fee == transfers.agent_demands(step.demand.wage, 0)[0]
    done = desk.submit(step.talk_id, step.demand, shift_wage_room=True)
    assert done.talk_status == contracts.SIGNED, done.message
    db.expire_all()
    assert db.get(Player, player.id).team_id == user.id
    assert db.scalar(select(TransferLog.kind).where(TransferLog.player_id == player.id)
                     .order_by(TransferLog.id.desc()).limit(1)) == "FREE_AGENT"
    assert desk.free_agents() == []


@pytest.mark.integration
def test_pre_contract_desk_flow_blocks_transfer_and_executes_at_rollover(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import Player, TransferLog
    from transfer_desk import DeskError
    from transfers import NegotiationStatus

    cm = _manager(db)
    desk = _desk(cm)
    user = cm.user_team
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    rival = cm.find_team(RIVAL)
    target = sorted((p for p in rival.players if p.position is not Position.GK), key=lambda p: (p.overall_rating, p.id))[0]
    target.contract_years = 1
    db.flush()
    with pytest.raises(DeskError, match="Ön sözleşme dönemi"):
        desk.open_pre_contract(target.id)
    _set_week(cm, 5)
    assert desk.contract_window().pre_contract_open
    assert target.id in {r.player_id for r in desk.pre_contract_targets(limit=200)}
    step = desk.open_pre_contract(target.id)
    if step.status is not NegotiationStatus.OPEN:
        pytest.skip(f"oyuncu masaya oturmadi: {step.message}")
    agreed = desk.submit(step.talk_id, step.demand)
    assert agreed.talk_status == contracts.AGREED, agreed.message
    assert "Ön sözleşme imzaladı" in (cm.transfer_block_reason(target) or "")
    with pytest.raises(DeskError, match="geri çekilemez"):
        desk.withdraw(step.talk_id)
    view = desk.talk(step.talk_id)
    assert view.direction == "IN" and view.effective_season == cm.season + 1 and not view.can_withdraw
    # sezon devri: oyuncu bedelsiz katilir, sozlesme anlasilan sure
    _set_week(cm, 1)
    _play_season(cm)
    cm.start_new_season()
    db.expire_all()
    moved = db.get(Player, target.id)
    assert moved.team_id == user.id and moved.contract_years == step.demand.years
    last = db.scalar(select(TransferLog).where(TransferLog.player_id == target.id).order_by(TransferLog.id.desc())
                     .limit(1))
    assert last.kind == "BOSMAN" and last.fee == 0 and last.season == cm.season and last.week == 1
    assert desk.talk(step.talk_id).status == contracts.SIGNED


@pytest.mark.integration
def test_rollover_releases_unrenewed_and_protects_squad_floor(db, monkeypatch):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import transfer_desk
    from models import Player, TransferLog

    monkeypatch.setattr(contracts, "SAFETY_SQUAD", 5)
    monkeypatch.setattr(transfer_desk, "PRE_CONTRACT_APPROACH_CHANCE", 0.0)      # AI on sozlesmesi yok
    cm = _manager(db)
    user = cm.user_team
    mine = sorted(user.players, key=lambda p: (p.overall_rating, p.id))
    for p in mine[:3]:
        p.contract_years = 1                                    # menajer yenilemiyor
    keepers = [p for p in mine if p.position is Position.GK]
    for p in keepers:
        p.contract_years = 1                                    # iki kaleci de bitiyor: biri guvenceyle kalmali
    db.flush()
    _play_season(cm)
    cm.start_new_season()
    db.expire_all()
    released = {r.player_id for r in db.scalars(select(TransferLog).where(
        TransferLog.kind == "RELEASED", TransferLog.from_team_id == user.id))}
    assert released, "yenilenmeyen oyuncu serbest kalmali"
    for pid in released:
        p = db.get(Player, pid)
        assert p.team_id is None or p.team_id != user.id or p.contract_years >= 1
    squad = list(db.scalars(select(Player).where(Player.team_id == user.id, Player.in_academy.is_(False))))
    assert sum(1 for p in squad if p.position is Position.GK) >= contracts.MIN_KEEPERS     # guvence: 1 yil uzadi
    for p in mine[:3]:
        if p.position is not Position.GK:                       # serbest kaldi (AI hazirlik doneminde imzalamis olabilir)
            assert p.id in released and db.get(Player, p.id).team_id != user.id
    assert any("Kadro güvencesi" in n for n in cm.new_season_notes)
    notes = " ".join(cm.new_season_notes)
    assert "serbest kaldı" in notes


@pytest.mark.integration
def test_unwarned_human_players_are_not_released(db):
    """Dongu devirde ilk kez calisiyorsa (eski kayit gecisi) menajerin oyunculari serbest kalmaz."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import TransferLog

    cm = _manager(db, flag=False)
    user = cm.user_team
    for p in list(user.players)[:3]:
        p.contract_years = 1
    db.flush()
    _play_season(cm)
    cm.contract_cycle = True
    assert cm.state.contracts_since_cw is None
    cm.start_new_season()
    assert _count(db, TransferLog, TransferLog.kind == "RELEASED", TransferLog.from_team_id == user.id) == 0
    assert cm.state.contracts_since_cw is not None


@pytest.mark.integration
def test_termination_quote_and_terminate(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from models import Player, TransferLog
    from transfer_desk import DeskError

    cm = _manager(db)
    desk = _desk(cm)
    user = cm.user_team
    user.transfer_budget += 100_000_000
    db.flush()
    victim = sorted((p for p in user.players if p.position is not Position.GK), key=lambda p: (p.overall_rating, p.id))[0]
    quote = desk.termination_quote(victim.id)
    expected = contracts.termination_compensation(victim.current_wage, victim.contract_years, cm.current_week,
                                                  cm._projected_season_weeks(), False)
    assert quote.can_terminate and quote.compensation == expected
    budget = user.transfer_budget
    done = desk.terminate(victim.id)
    assert done.done and user.transfer_budget == budget - expected
    db.expire_all()
    assert db.get(Player, victim.id).team_id is None
    assert db.scalar(select(TransferLog.kind).where(TransferLog.player_id == victim.id)
                     .order_by(TransferLog.id.desc()).limit(1)) == "TERMINATED"
    # kadro tabani: kaleciler feshedilemez (2 kaleci kurali)
    keeper = next(p for p in cm.user_team.players if p.position is Position.GK)
    assert not desk.termination_quote(keeper.id).can_terminate
    with pytest.raises(DeskError):
        desk.terminate(keeper.id)


@pytest.mark.integration
def test_ai_pre_contract_on_human_player_shows_leaving(db, monkeypatch):
    """AI kulubu menajerin yenilemedigi oyuncusuyla on sozlesme imzalar; Sozlesmeler ekraninda 'ayriliyor'."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import transfer_desk
    from career_manager import WeekReport
    from models import ContractTalk

    monkeypatch.setattr(contracts, "SAFETY_SQUAD", 8)
    monkeypatch.setattr(transfer_desk, "PRE_CONTRACT_APPROACH_CHANCE", 1.01)
    monkeypatch.setattr(contracts, "PRE_CONTRACT_MIN_FIT", -99.0)
    cm = _manager(db)
    user = cm.user_team
    for p in user.players:
        if p.position is not Position.GK:
            p.contract_years = 1
    db.flush()
    report = WeekReport(season=cm.season, week=4)
    _set_week(cm, 4)
    with cm._seat_snapshot():
        transfer_desk.run_contract_week(cm, 4, report)
    talks = list(db.scalars(select(ContractTalk).where(ContractTalk.kind == contracts.KIND_PRE_CONTRACT,
                                                       ContractTalk.from_team_id == user.id)))
    if not talks:
        pytest.skip("bu tohumda AI kulubu menajerin oyuncusuyla anlasmadi")
    assert all(t.status == contracts.AGREED and t.human_team_id == user.id for t in talks)
    rows = {r.player_id: r for r in _desk(cm).contracts()}
    for t in talks:
        assert rows[t.player_id].status == contracts.ROW_LEAVING and rows[t.player_id].other_team
        assert not rows[t.player_id].can_renew
    assert any("ön sözleşme imzaladı" in n for n in report.transfer_notes)


@pytest.mark.integration
def test_ai_signs_free_agents_to_fill_needs(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import transfer_desk
    from career_manager import WeekReport
    from models import Player

    cm = _manager(db)
    rival = cm.find_team(RIVAL)
    keepers = [p for p in rival.players if p.position is Position.GK]
    donor = cm.find_team("Kadıköy Canaries")
    spare = [p for p in donor.players if p.position is Position.GK][0]
    for p in keepers:                                            # kulup kalecisiz kaldi (acil ihtiyac)
        cm.release_player(p)
    cm.release_player(spare)
    report = WeekReport(season=cm.season, week=1)
    with cm._seat_snapshot():
        signed = transfer_desk.run_contract_week(cm, 1, report)
    assert any(n.to_team_id == rival.id and n.kind == "FREE_AGENT" for n in signed)
    db.expire_all()
    assert _count(db, Player, Player.team_id == rival.id, Player.position == Position.GK) >= 1


@pytest.mark.integration
def test_schema_is_current_and_additive():
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import database

    assert database.SCHEMA_VERSION >= 17
    added = {(t, c) for t, c, _ddl in database.ADDITIVE_COLUMNS}
    assert {("players", "free_agent_since"), ("game_state", "contracts_since_cw")} <= added
    assert database.schema_problems() == []
    assert database.upgrade_schema() == []                       # idempotent


@pytest.mark.integration
def test_contract_desk_refuses_when_cycle_off_and_release_needs_club(db):
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    from transfer_desk import DeskError
    from transfers import TransferError

    cm = _manager(db, flag=False)
    with pytest.raises(DeskError, match="kapalı"):
        _desk(cm).contracts()
    cm.contract_cycle = True
    rival = cm.find_team(RIVAL)
    player = sorted(rival.players, key=lambda p: (p.overall_rating, p.id))[0]
    cm.release_player(player)
    with pytest.raises(TransferError, match="kulüpsüz"):
        cm.release_player(player)
    with pytest.raises(DeskError, match="serbest oyuncu değil"):
        _desk(cm).open_free_agent(sorted(rival.players, key=lambda p: p.id)[0].id)
