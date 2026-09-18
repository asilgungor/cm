"""
13H transfer masasi testleri: saf kurallar (transfer_rules, transfers menajer masasi) + gercek PostgreSQL uzerinde
transfer_desk akisi. Para guvenligi: taksitler toplami bedele esit, ek odeme / sonraki satis payi bir kez, kasa eksiye
dusmez (gecikme + borc kurali), iki yoldan tamamlama tek sefer (satir kilidi, iki thread).

Her DB testi kendi islemini geri alir; es zamanlilik testi commit eder ve modul sonunda dunyayi yeniden kurar.
Onerilen: TEST_DB_NAME=fm_db_test_13h.
"""

from __future__ import annotations

import logging
import random
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import transfer_desk  # noqa: E402
import transfer_rules as rules  # noqa: E402
import transfers  # noqa: E402
from career_manager import CareerManager, WeekReport  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    NewsItem,
    Player,
    PlayerMatchStat,
    Position,
    ScoutAssignment,
    SquadRole,
    Team,
    TransferDeal,
    TransferLog,
    TransferPayment,
)
from transfer_desk import DeskError, TransferDesk  # noqa: E402
from transfer_rules import AddOn, DealTerms  # noqa: E402
from transfers import ContractNegotiation, ContractOffer, NegotiationStatus  # noqa: E402

USER = "Istanbul Lions"
SELLER = "Karadeniz Storm"          # ayni lig: bilgi 35 (gozlemsiz teklif yapilabilir)
FOREIGN = "Rhône Gones"             # baska lig: once gozlem gerekir


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


DB = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, team: str = USER) -> CareerManager:
    cm = CareerManager(db, seed=7)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.set_user_team(cm.find_team(team))
    return cm


def _desk(db) -> tuple[CareerManager, TransferDesk]:
    cm = _manager(db)
    return cm, TransferDesk(cm)


def _negotiable(desk: TransferDesk, seller: Team, *, young: bool | None = None,
                kinds: tuple[str, ...] = ("Pazarlığa açık", "Kadro fazlası")) -> Player:
    """Satici kulubun pazarliga acik / fazlalik bir oyuncusu (kaleci degil)."""
    for p in sorted(seller.players, key=lambda p: (p.overall_rating, p.id)):
        if p.position is Position.GK:
            continue
        if young is False and p.age <= rules.SELL_ON_WANTED_AGE:
            continue
        view = desk.enquire(p.id)
        if view.stance_label in kinds:
            return p
    raise AssertionError("pazarlığa açık oyuncu yok")


def _generous(asking: int, **extra) -> DealTerms:
    fee = int(round(asking * 2.4 / 10_000) * 10_000)
    return DealTerms(fee=fee, upfront=fee, **extra)


def _agree(desk: TransferDesk, player: Player, terms: DealTerms, contract_patch: dict | None = None) -> int:
    """Teklif -> (karsi teklif kabul) -> kisisel sartlar (oyuncunun talebi) -> saglik -> AGREED. Dosya id'si."""
    view = desk.make_bid(player.id, terms)
    if view.can_accept_counter:
        view = desk.accept_counter(view.id)
    assert view.status == "TERMS", (view.status, view.club_message, view.demands)
    step = desk.open_terms(view.id)
    assert step.status is NegotiationStatus.OPEN, step.message
    demand = step.demand
    if contract_patch:
        demand = ContractOffer(**{**demand.__dict__, **contract_patch})
    step = desk.submit_terms(view.id, demand)
    assert step.status is NegotiationStatus.ACCEPTED, step.message
    view = desk.deal(view.id)
    if view.status == "MEDICAL":
        view = desk.confirm_medical(view.id, True)
    assert view.status == "AGREED"
    return view.id


def _payments(db, deal_id: int, kind: str | None = None) -> list[TransferPayment]:
    db.flush()
    stmt = select(TransferPayment).where(TransferPayment.deal_id == deal_id)
    if kind:
        stmt = stmt.where(TransferPayment.kind == kind)
    return list(db.scalars(stmt.order_by(TransferPayment.id)))


def _jump(cm: CareerManager, weeks: int) -> None:
    """Mutlak kariyer haftasini ileri alir (sezon haftasi degismez): vadeler / yasaklar icin."""
    cm.state.career_week_offset = int(cm.state.career_week_offset or 0) + int(weeks)
    cm.db.flush()


# ===========================================================================
# 1) SAF KURALLAR
# ===========================================================================

def test_window_scales_with_season_length():
    assert rules.window_weeks(7) == ((1, 2), (4, 4))
    assert rules.window_weeks(38) == ((1, 5), (20, 23))
    assert rules.window_weeks(2) == ((1, 2), None)
    opened = rules.transfer_window(1, 7)
    assert opened.open and opened.name == rules.WINDOW_SUMMER and opened.closes_after_week == 2
    closed = rules.transfer_window(3, 7)
    assert not closed.open and closed.next_open_week == 4 and "4. haftada" in closed.label
    assert rules.transfer_window(4, 7).name == rules.WINDOW_WINTER
    late = rules.transfer_window(6, 7)
    assert not late.open and late.next_open_week is None
    assert rules.transfer_window(8, 7, season_finished=True).name == rules.WINDOW_OFF_SEASON


@pytest.mark.parametrize("deferred", [1, 7, 999_999, 3_330_001, 12_345_679, 100_000_000])
@pytest.mark.parametrize("months", [6, 12, 24, 36])
def test_instalment_plan_sums_exactly_to_the_deferred_fee(deferred, months):
    plan = rules.instalment_plan(deferred, months, 7, 10)
    assert len(plan) == months // 3
    assert rules.sum_amounts(plan) == deferred
    assert all(amount >= 0 for _s, _d, amount in plan)
    dues = [due for _s, due, _a in plan]
    assert dues == sorted(dues) and dues[0] > 10 and len(set(dues)) == len(dues)
    assert [seq for seq, _d, _a in plan] == list(range(1, len(plan) + 1))
    assert rules.instalment_plan(0, months, 7, 10) == []


def test_terms_validation_and_description():
    good = DealTerms(fee=10_000_000, upfront=6_000_000, instalment_months=12,
                     add_ons=(AddOn("APPEARANCES", 20, 500_000), AddOn("LEAGUE_TITLE", 1, 1_000_000)), sell_on_pct=15)
    assert rules.validate_terms(good) == []
    text = good.describe()
    assert "6.0M EUR peşin" in text and "4 taksit" in text and "%15" in text and "20 resmi maçtan sonra" in text
    assert rules.validate_terms(DealTerms(fee=-1))
    assert rules.validate_terms(DealTerms(fee=10, upfront=11))
    assert rules.validate_terms(DealTerms(fee=10, upfront=5, instalment_months=0))        # taksit suresi yok
    assert rules.validate_terms(DealTerms(fee=10, instalment_months=18))
    assert rules.validate_terms(DealTerms(fee=10, sell_on_pct=51))
    assert rules.validate_terms(DealTerms(fee=10, add_ons=(AddOn("GOALS", 0, 5),)))
    assert rules.validate_terms(DealTerms(fee=10, add_ons=(AddOn("GOALS", 5, 5), AddOn("GOALS", 5, 7))))
    assert rules.validate_terms(DealTerms(fee=10, exchange_player_id=3), allow_exchange=False)
    normalized = rules.normalize_terms(DealTerms(fee=5_000_000, instalment_months=24))
    assert (normalized.upfront, normalized.instalment_months) == (5_000_000, 0)
    assert DealTerms.from_dict(good.to_dict()) == rules.normalize_terms(good)


def test_package_value_rewards_upfront_add_ons_and_sell_on():
    ctx = rules.ValuationContext(league_weeks=6, position="FWD", expected_role="STAR", title_odds=0.45,
                                 resale_value=10_000_000, young=True)
    cash = rules.package_value(DealTerms(fee=10_000_000), ctx)
    spread = rules.package_value(DealTerms(fee=10_000_000, upfront=4_000_000, instalment_months=36), ctx)
    assert cash == 10_000_000 and spread < cash
    extra = rules.package_value(DealTerms(fee=10_000_000, add_ons=(AddOn("APPEARANCES", 10, 1_000_000),)), ctx)
    assert cash < extra < cash + 1_000_000
    sell_on = rules.package_value(DealTerms(fee=10_000_000, sell_on_pct=20), ctx)
    assert sell_on == cash + int(10_000_000 * 0.2 * 0.45)
    fee = rules.fee_for_value(DealTerms(fee=1, upfront=1), 12_000_000, ctx)
    assert fee >= 12_000_000 and rules.package_value(DealTerms(fee=fee), ctx) >= 12_000_000


def _stance(kind=rules.STANCE_NEGOTIABLE, target=10_000_000, minimum=9_000_000, patience=4, upfront=0.35, sell_on=0):
    return rules.ClubStance(kind, rules.STANCE_LABELS[kind], target, minimum, patience, upfront, sell_on)


def test_seller_response_accepts_counters_rejects_and_ends():
    ctx = rules.ValuationContext()
    rng = random.Random(1)
    assert rules.seller_response(rng, DealTerms(fee=10_000_000), _stance(), ctx, 4, 1).action == rules.ACTION_ACCEPT
    counter = rules.seller_response(rng, DealTerms(fee=7_000_000), _stance(), ctx, 4, 1)
    assert counter.action == rules.ACTION_COUNTER and counter.patience_cost == 1
    assert counter.counter.fee > 7_000_000 and counter.demands and "Bonservis en az" in counter.demands[0]
    insult = rules.seller_response(rng, DealTerms(fee=3_000_000), _stance(), ctx, 4, 1)
    assert insult.action == rules.ACTION_REJECT and insult.patience_cost == 2
    assert rules.seller_response(rng, DealTerms(fee=3_000_000), _stance(), ctx, 2, 2).action == rules.ACTION_END
    assert rules.seller_response(rng, DealTerms(fee=7_000_000), _stance(), ctx, 1, 3).action == rules.ACTION_END
    # kasasi zayif kulup pesinat ister; hedefin %15 ustundeki paket yapi isteklerini asar
    poor = _stance(upfront=0.6, sell_on=20)
    low_upfront = DealTerms(fee=10_500_000, upfront=2_000_000, instalment_months=12)
    response = rules.seller_response(rng, low_upfront, poor, ctx, 4, 1)
    assert response.action == rules.ACTION_COUNTER
    assert response.counter.upfront_amount >= int(response.counter.fee * 0.6) - 10_000
    assert response.counter.sell_on_pct == 20 and any("peşin" in d for d in response.demands)
    generous = DealTerms(fee=14_000_000, upfront=4_000_000, instalment_months=12)
    assert rules.seller_response(rng, generous, poor, ctx, 4, 1).action == rules.ACTION_ACCEPT
    unwanted = rules.seller_response(rng, DealTerms(fee=20_000_000, exchange_player_id=5), _stance(),
                                     rules.ValuationContext(exchange_value=0), 4, 1)
    assert unwanted.action == rules.ACTION_COUNTER and unwanted.counter.exchange_player_id is None
    closed = rules.ClubStance(rules.STANCE_UNAVAILABLE, "", 0, 0, 1, 1.0, 0, "Kadro çok daralır")
    assert rules.seller_response(rng, DealTerms(fee=10 ** 9), closed, ctx, 4, 1).action == rules.ACTION_END


def test_buyer_response_accepts_counters_and_walks():
    ctx = rules.ValuationContext()
    rng = random.Random(3)
    assert rules.buyer_response(rng, DealTerms(fee=5_000_000), ctx, 5_000_000, 6_000_000, 3, 2).action == "ACCEPT"
    walk = rules.buyer_response(rng, DealTerms(fee=9_000_000), ctx, 5_000_000, 6_000_000, 3, 2)
    assert walk.action == rules.ACTION_END
    counters = [rules.buyer_response(random.Random(s), DealTerms(fee=7_000_000), ctx, 5_000_000, 6_500_000, 3, 2)
                for s in range(30)]
    assert {c.action for c in counters} <= {"ACCEPT", "COUNTER"}
    for c in counters:
        if c.action == "COUNTER":
            assert 5_000_000 < c.counter.fee <= 7_000_000


def test_medical_and_interest_and_scouting_rules():
    assert rules.medical_check(injured_weeks_left=0, proneness=5, recent_injuries=0, age=25).result == "PASS"
    risk = rules.medical_check(injured_weeks_left=2, proneness=17, recent_injuries=0, age=25)
    assert risk.result == "RISK" and len(risk.notes) == 2
    assert rules.medical_check(injured_weeks_left=6, proneness=1, recent_injuries=0, age=25).result == "FAIL"
    keen = rules.player_interest(overall=75, ambition=20, current_rep=70, buyer_rep=85, manager_rep=12,
                                 current_league_rep=70, buyer_league_rep=85, current_role=SquadRole.BACKUP,
                                 offered_role=SquadRole.FIRST_TEAM)
    assert keen.level == rules.INTEREST_KEEN and keen.wage_multiplier < 1
    gate = rules.player_interest(overall=92, ambition=10, current_rep=95, buyer_rep=60, manager_rep=3,
                                 current_league_rep=90, buyer_league_rep=60, current_role=SquadRole.STAR,
                                 offered_role=SquadRole.STAR)
    assert gate.refuses and gate.reason
    assert rules.hidden_trait("ambition", 42) == rules.hidden_trait("ambition", 42)
    assert rules.hidden_trait("ambition", 42, 17) == 17 and 1 <= rules.hidden_trait("injury", 9) <= 20
    assert rules.knowledge_margin(6, 10) is None and rules.knowledge_margin(6, 100) == 6
    assert rules.knowledge_margin(6, 25) > rules.knowledge_margin(6, 75) >= 6
    assert rules.weekly_scouting_gain(None) == 12 and rules.weekly_scouting_gain(20) == 35
    assert rules.rivalry("Milano Rossoneri", "Milano Nerazzurri", True) == 2
    assert rules.rivalry("Istanbul Lions", "Karadeniz Storm", True) == 1


# ===========================================================================
# 2) SOZLESME MASASI: ESKI AKIS AYNI, MENAJER MASASI GENIS
# ===========================================================================

def _fake(overall=80, age=26, wage=40_000, rep=70, buyer_rep=80, position=Position.MID, value=5_000_000):
    from types import SimpleNamespace
    player = SimpleNamespace(id=1, name="Test Oyuncu", overall_rating=overall, age=age, current_wage=wage,
                             position=position, market_value=value, team=SimpleNamespace(reputation=rep))
    buyer = SimpleNamespace(id=2, reputation=buyer_rep, players=[SimpleNamespace(overall_rating=r)
                                                                 for r in (84, 82, 80, 78, 76, 75, 74)])
    return player, buyer


def test_legacy_contract_offer_and_negotiation_are_unchanged():
    assert ContractOffer(50_000, 3, SquadRole.STAR) == ContractOffer(wage=50_000, years=3, role=SquadRole.STAR)
    assert ContractOffer(50_000, 3, SquadRole.STAR).describe() == "50,000 EUR/hafta · 3 yıl · Yıldız"
    player, buyer = _fake()
    legacy = ContractNegotiation(random.Random(5), player, buyer, 5_000_000, 10.0)
    demand = legacy.demand
    assert not demand.has_extras and legacy.min_wage == int(demand.wage * transfers.WAGE_RED_LINE)
    assert legacy._value(demand) == demand.wage
    offer = ContractOffer(int(demand.wage * 0.85), demand.years, demand.role)
    assert legacy.persuasion(offer) == transfers.persuasion_score(
        buyer.reputation, 10.0, transfers.wage_offer_score(offer.wage, demand.wage))
    response = legacy.respond(offer)
    assert response.status is NegotiationStatus.OPEN
    expected = int(round(max(offer.wage, (demand.wage + offer.wage) / 2) / 100) * 100)
    assert response.counter == ContractOffer(expected, demand.years, demand.role)
    # ayni tohum -> ayni sure talebi (RNG cekimi degismedi)
    assert legacy.demand.years == transfers.demanded_years(random.Random(5), player)


def test_agent_mode_values_the_package_and_guards_the_agent_fee():
    player, buyer = _fake()
    agent = ContractNegotiation(random.Random(5), player, buyer, 5_000_000, 10.0, agent=True)
    demand = agent.demand
    assert demand.signing_fee > 0 and demand.agent_fee >= 5_000_000 * transfers.AGENT_FEE_SHARE
    # menajer ucreti hakaret duzeyinde -> masa dagilir
    insult = ContractNegotiation(random.Random(5), player, buyer, 5_000_000, 10.0, agent=True)
    low_agent = ContractOffer(**{**demand.__dict__, "agent_fee": int(demand.agent_fee * 0.3)})
    assert insult.respond(low_agent).status is NegotiationStatus.WALKED_AWAY
    # imza primi daha dusuk maasi telafi eder
    lower = ContractOffer(**{**demand.__dict__, "wage": int(demand.wage * 0.86), "signing_fee": 0})
    plain = ContractNegotiation(random.Random(5), player, buyer, 5_000_000, 10.0, agent=True)
    boosted = ContractNegotiation(random.Random(5), player, buyer, 5_000_000, 10.0, agent=True)
    sweetened = ContractOffer(**{**lower.__dict__, "signing_fee": demand.signing_fee * 4})
    assert boosted.persuasion(sweetened) > plain.persuasion(lower)
    keen = ContractNegotiation(random.Random(5), player, buyer, 5_000_000, 10.0, agent=True,
                               demand_multiplier=0.94)
    assert keen.demand.wage < demand.wage
    roundtrip = ContractOffer(**{**demand.__dict__, "release_clause": 20_000_000, "appearance_bonus": 1_000})
    assert ContractOffer.from_dict(roundtrip.to_dict()) == roundtrip
    assert "serbest kalma bedeli" in roundtrip.describe()


# ===========================================================================
# 3) VERITABANI: GOZLEM, BILGI ALMA, TEKLIF
# ===========================================================================

@DB
def test_scouting_gate_knowledge_growth_and_fogged_report(db):
    cm, desk = _desk(db)
    foreign = cm.find_team(FOREIGN)
    target = foreign.players[3]
    with pytest.raises(DeskError, match="yeterli bilgin yok"):
        desk.enquire(target.id)
    report = desk.scout_report(target.id)
    assert not report.known and report.overall is None and report.value is None
    view = desk.scout(target.id)
    assert view.assigned and view.knowledge == 0 and not view.can_bid
    desk._scouting_progress()
    first_week = desk.knowledge(target.id).knowledge
    assert first_week == view.weekly_gain > 0
    desk._scouting_progress()                                        # ortalama gozlemciyle iki hafta
    grown = desk.knowledge(target.id)
    assert grown.knowledge >= rules.KNOWN_THRESHOLD and grown.can_bid
    report = desk.scout_report(target.id)
    assert report.known and report.overall.low <= target.overall_rating <= report.overall.high
    assert not report.overall.exact and report.potential is None and report.injury_label is None
    for _ in range(5):
        desk._scouting_progress()
    full = desk.scout_report(target.id)
    assert full.knowledge == 100 and full.injury_label and full.contract_text and full.potential is not None
    assert db.scalar(select(ScoutAssignment.status).where(ScoutAssignment.player_id == target.id)) == "DONE"
    assert desk.enquire(target.id).status == "ENQUIRY"
    same_league = cm.find_team(SELLER).players[5]
    assert desk.knowledge(same_league.id).knowledge == rules.SAME_LEAGUE_KNOWLEDGE


@DB
def test_enquiry_reports_stance_with_a_fogged_price_and_guards(db):
    cm, desk = _desk(db)
    seller = cm.find_team(SELLER)
    keeper = next(p for p in seller.players if p.position is Position.GK)
    closed = desk.enquire(keeper.id)
    assert closed.stance_label == "Satışa kapalı" and "yedeksiz" in closed.club_message
    player = _negotiable(desk, seller)
    view = desk.enquire(player.id)
    assert view.status == "ENQUIRY" and view.terms is None and view.can_bid and "civar" in view.club_message
    assert view.expires_in_weeks == transfer_desk.ENQUIRY_VALID_WEEKS
    assert "-" in view.value_text                                        # sisli deger araligi
    with pytest.raises(DeskError, match="zaten senin"):
        desk.enquire(cm.user_team.players[0].id)
    academy = next(iter(seller.academy_players), None)
    if academy is not None:
        with pytest.raises(DeskError, match="akademisinde"):
            desk.enquire(academy.id)
    # ayni oyuncu + alici icin tek acik dosya
    assert desk.enquire(player.id).id == view.id


@DB
def test_structured_bid_full_flow_moves_money_exactly(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    fee = int(round(asking * 2.4 / 10_000) * 10_000)
    terms = DealTerms(fee=fee, upfront=int(fee * 0.6), instalment_months=12,
                      add_ons=(AddOn("GOALS", 50, 300_000),), sell_on_pct=10)
    user.transfer_budget += 50_000_000
    db.flush()
    b0, s0 = int(user.transfer_budget), int(seller.transfer_budget)
    deal_id = _agree(desk, player, terms)
    assert (int(user.transfer_budget), int(seller.transfer_budget)) == (b0, s0)     # anlasma para hareketi yapmaz
    contract = ContractOffer.from_dict(db.get(TransferDeal, deal_id).contract)
    view = desk.complete(deal_id, shift_wage_room=True)
    assert view.status == "COMPLETED" and player.team_id == user.id
    upfront, deferred = terms.upfront_amount, fee - terms.upfront_amount
    shift = max(0, b0 - int(user.transfer_budget) - upfront - contract.signing_fee - contract.agent_fee)
    assert shift % 52 == 0                                                           # yalnizca maas kaydirmasi
    assert int(seller.transfer_budget) == s0 + upfront
    log_row = db.scalar(select(TransferLog).where(TransferLog.player_id == player.id).order_by(TransferLog.id.desc()))
    assert log_row.fee == fee                                                        # kayit toplam bedeli yazar
    kinds = {p.kind: p for p in _payments(db, deal_id)}
    assert kinds["UPFRONT"].status == "PAID" and kinds["UPFRONT"].amount == upfront
    assert kinds["SIGNING"].amount == contract.signing_fee and kinds["AGENT"].amount == contract.agent_fee
    instalments = _payments(db, deal_id, "INSTALMENT")
    assert len(instalments) == 4 and sum(p.amount for p in instalments) == deferred
    assert all(p.status == "SCHEDULED" for p in instalments)
    assert player.contract_clauses["deal_id"] == deal_id and player.squad_role is contract.role
    # tum vadeler gelir: taksitler bir kez odenir, satici toplam bedeli alir
    _jump(cm, 40)
    desk._process_payments()
    desk._process_payments()
    instalments = _payments(db, deal_id, "INSTALMENT")
    assert all(p.status == "PAID" and p.paid_amount == p.amount for p in instalments)
    assert int(seller.transfer_budget) == s0 + fee
    paid_to_seller = db.scalar(select(func.sum(TransferPayment.paid_amount)).where(
        TransferPayment.deal_id == deal_id, TransferPayment.payee_team_id == seller.id))
    assert paid_to_seller == fee
    summary = desk.finance_summary()
    assert summary.payable_scheduled == 0 and summary.overdue_payable == 0


@DB
def test_counter_offer_then_accept_and_talks_breakdown(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False, kinds=("Pazarlığa açık",))
    asking = transfers.asking_price(player, seller, user.reputation)
    b0 = int(user.transfer_budget)
    low = int(round(asking * 0.75 / 10_000) * 10_000)
    view = desk.make_bid(player.id, DealTerms(fee=low))
    assert view.status == "BIDDING" and view.turn == "MANAGER"
    if view.can_accept_counter:
        assert view.terms.fee > low and view.demands and view.mood
        agreed = desk.accept_counter(view.id)
        assert agreed.status == "TERMS" and int(user.transfer_budget) == b0
    # satilik olmayan (ya da kapali) oyuncuya sacma teklifler -> gorusmeler kesilir, soguma suresi
    keeper = next(p for p in seller.players if p.position is Position.GK)
    ended = desk.make_bid(keeper.id, DealTerms(fee=100_000))
    assert ended.status == "REJECTED"
    with pytest.raises(DeskError, match="görüşmeleri kesti"):
        desk.enquire(keeper.id)


@DB
def test_insulting_bids_exhaust_patience(db):
    cm, desk = _desk(db)
    seller = cm.find_team(SELLER)
    player = _negotiable(desk, seller)
    deal = None
    for _ in range(8):
        deal = db.scalar(select(TransferDeal).where(TransferDeal.player_id == player.id)
                         .order_by(TransferDeal.id.desc()))
        if deal is not None and deal.status == "REJECTED":
            break
        if deal is not None and deal.turn == "CLUB":            # haftalik yanit siniri: sirada bekleyen yanit
            desk._queued_responses(cm.career_week + 1)
            continue
        view = desk.make_bid(player.id, DealTerms(fee=50_000))
        assert view.status in ("BIDDING", "REJECTED") and view.terms.fee == 50_000
    assert deal.status == "REJECTED" and "kesti" in deal.reason
    assert deal.talks_blocked_until == cm.career_week + transfer_desk.TALKS_COOLDOWN_WEEKS


@DB
def test_club_responses_beyond_the_weekly_limit_are_queued(db):
    cm, desk = _desk(db)
    seller = cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False, kinds=("Pazarlığa açık",))
    asking = transfers.asking_price(player, seller, cm.user_team.reputation)
    answered = 0
    view = None
    for factor in (0.7, 0.72, 0.74):
        view = desk.make_bid(player.id, DealTerms(fee=int(round(asking * factor / 10_000) * 10_000)))
        if view.status != "BIDDING":
            pytest.skip("kulüp erken kabul etti / görüşmeyi kesti")
        if view.turn == "CLUB":
            break
        answered += 1
    assert answered == transfer_desk.RESPONSES_PER_WEEK and view.turn == "CLUB"
    with pytest.raises(DeskError, match="değerlendiriyor"):
        desk.make_bid(player.id, DealTerms(fee=asking))
    deal = db.get(TransferDeal, view.id)
    assert deal.response_due_week == cm.career_week + 1
    desk._queued_responses(cm.career_week + 1)
    assert deal.response_due_week is None and (deal.turn == "MANAGER" or deal.status != "BIDDING")


@DB
def test_outside_the_window_the_deal_waits_and_completes_once_when_it_opens(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    cm.state.current_week = 3                                       # 7 haftalik sezonda donem kapali
    db.flush()
    assert not desk.window().open and desk.window().next_open_week == 4
    deal_id = _agree(desk, player, _generous(asking))
    view = desk.deal(deal_id)
    assert view.status == "AGREED" and not view.can_complete and "4. haftada" in view.completes_text
    b0, s0 = int(user.transfer_budget), int(seller.transfer_budget)
    with pytest.raises(DeskError, match="dönem açılınca"):
        desk.complete(deal_id)
    report = WeekReport(season=cm.season, week=3, focus_team_id=user.id)
    transfer_desk.run_week(cm, 3, report)                           # 3. haftanin sonu: 4. hafta (kis) acik
    deal = db.get(TransferDeal, deal_id)
    assert deal.status == "COMPLETED" and player.team_id == user.id
    fee = int(deal.fee)
    contract = ContractOffer.from_dict(deal.contract)
    assert int(seller.transfer_budget) == s0 + fee
    assert int(user.transfer_budget) == b0 - fee - contract.signing_fee - contract.agent_fee
    assert any("tamamlandı" in n for n in report.transfer_notes)
    with pytest.raises(DeskError, match="zaten tamamlandı"):
        desk.complete(deal_id)
    desk._complete_waiting()
    assert int(seller.transfer_budget) == s0 + fee                  # ikinci yol para hareket ettirmez
    assert db.scalar(select(func.count()).select_from(TransferLog).where(TransferLog.player_id == player.id)) == 1


@DB
def test_add_on_is_paid_once_and_lapses_when_the_player_leaves(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    terms = _generous(asking, add_ons=(AddOn("APPEARANCES", 1, 700_000), AddOn("GOALS", 2, 400_000)))
    deal_id = _agree(desk, player, terms)
    desk.complete(deal_id, shift_wage_room=True)
    s0 = int(seller.transfer_budget)
    fixture = db.scalar(select(Fixture).where(Fixture.season == cm.season, Fixture.week >= cm.current_week,
                                              (Fixture.home_team_id == user.id) | (Fixture.away_team_id == user.id))
                        .order_by(Fixture.week))
    db.add(PlayerMatchStat(fixture_id=fixture.id, player_id=player.id, team_id=user.id, minutes=90, goals=1,
                           rating=7.5))
    db.flush()
    desk._check_add_ons()
    desk._check_add_ons()
    add_ons = _payments(db, deal_id, "ADD_ON")
    assert [(p.amount, p.status) for p in add_ons] == [(700_000, "PAID")]          # mac eki bir kez; gol eki 1/2
    assert int(seller.transfer_budget) == s0 + 700_000
    view = desk.deal(deal_id)
    assert any("ödendi" in line for line in view.add_on_progress) and any("1/2" in line
                                                                          for line in view.add_on_progress)
    # unique ref: ayni ek odeme elle yeniden yazilamaz
    assert desk._payment(deal=db.get(TransferDeal, deal_id), kind="ADD_ON", ref=add_ons[0].ref, payer_id=user.id,
                         payee_id=seller.id, amount=700_000, player_id=player.id) is None
    # oyuncu ayrilirsa gol eki duser (odenmez)
    _jump(cm, 10)
    other = cm.find_team("Bosphorus Eagles")
    other.transfer_budget += 50_000_000
    other.wage_budget += 1_000_000
    db.flush()
    cm.complete_transfer(other, player, 1_000_000, ContractOffer(player.current_wage, 2, SquadRole.FIRST_TEAM))
    db.flush()
    desk._check_add_ons()
    assert len(_payments(db, deal_id, "ADD_ON")) == 1
    assert any(e.get("kind") == "addon_lapsed" for e in db.get(TransferDeal, deal_id).history)


@DB
def test_sell_on_clause_is_paid_once_on_the_next_sale(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    deal_id = _agree(desk, player, _generous(asking, sell_on_pct=20))
    desk.complete(deal_id, shift_wage_room=True)
    _jump(cm, 10)                                                    # transfer yasagi biter
    buyer = cm.find_team("Bosphorus Eagles")
    buyer.transfer_budget += 50_000_000
    buyer.wage_budget += 1_000_000
    db.flush()
    s0, u0 = int(seller.transfer_budget), int(user.transfer_budget)
    sale = 9_000_000
    cm.complete_transfer(buyer, player, sale, ContractOffer(player.current_wage, 2, SquadRole.FIRST_TEAM))
    share = sale * 20 // 100
    assert int(seller.transfer_budget) == s0 + share
    assert int(user.transfer_budget) == u0 + sale - share
    rows = _payments(db, deal_id, "SELL_ON")
    assert [(r.amount, r.status, r.payee_team_id) for r in rows] == [(share, "PAID", seller.id)]
    assert db.get(TransferDeal, deal_id).sell_on_used_career_week == cm.career_week
    assert transfer_desk.settle_sell_on(cm, player, user, sale) == 0                 # ikinci satis: pay yok
    assert int(seller.transfer_budget) == s0 + share


@DB
def test_desk_sale_in_instalments_pays_the_sell_on_share_as_money_arrives(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    deal_in = _agree(desk, player, _generous(asking, sell_on_pct=20))
    desk.complete(deal_in, shift_wage_room=True)
    _jump(cm, 10)
    buyer = cm.find_team("Bosphorus Eagles")
    buyer.transfer_budget += 80_000_000
    buyer.wage_budget += 2_000_000
    db.flush()
    out = desk._new_deal(direction="OUT", player=player, seller=user, buyer=buyer, human=user, status="BIDDING",
                         patience=3)
    transfer_desk._set_terms(out, DealTerms(fee=8_000_000, upfront=4_000_000, instalment_months=12))
    out.turn, out.round = "MANAGER", 1
    db.flush()
    s0, u0 = int(seller.transfer_budget), int(user.transfer_budget)
    view = desk.accept_offer(out.id)
    if view.status != "COMPLETED":
        pytest.skip(f"AI kulübü oyuncuyla anlaşamadı: {view.reason}")
    assert int(seller.transfer_budget) == s0 + 800_000                  # pesinatin %20'si
    assert int(user.transfer_budget) == u0 + 4_000_000 - 800_000
    _jump(cm, 40)
    desk._process_payments()
    desk._process_payments()
    assert int(seller.transfer_budget) == s0 + 1_600_000                # toplam bedelin %20'si, bir kez
    assert int(user.transfer_budget) == u0 + 8_000_000 - 1_600_000
    shares = _payments(db, deal_in, "SELL_ON")
    assert sum(r.paid_amount for r in shares) == 1_600_000 and len(shares) == 5       # pesinat + 4 taksit
    assert db.get(TransferDeal, deal_in).sell_on_used_career_week is not None
    assert transfer_desk.settle_sell_on(cm, player, user, 8_000_000) == 0


@DB
def test_a_payment_without_a_paying_club_is_cancelled_not_paid(db):
    cm, desk = _desk(db)
    user = cm.user_team
    u0 = int(user.transfer_budget)
    row = desk._payment(deal=None, kind="INSTALMENT", ref="TEST#ORPHAN", payer_id=None, payee_id=user.id,
                        amount=1_000_000, player_id=None, due_cw=cm.career_week)
    assert desk._settle(row) == 0 and row.status == "CANCELLED" and int(user.transfer_budget) == u0


@DB
def test_budget_never_goes_negative_overdue_blocks_new_bids_then_settles(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    fee = int(round(asking * 2.4 / 10_000) * 10_000)
    terms = DealTerms(fee=fee, upfront=int(fee * 0.7), instalment_months=6)
    deal_id = _agree(desk, player, terms)
    desk.complete(deal_id, shift_wage_room=True)
    s0 = int(seller.transfer_budget)
    user.transfer_budget = 100_000                                   # kasa neredeyse bos
    db.flush()
    _jump(cm, 20)
    desk._process_payments()
    rows = _payments(db, deal_id, "INSTALMENT")
    assert int(user.transfer_budget) == 0 and all(r.status == "OVERDUE" for r in rows)
    assert sum(r.paid_amount for r in rows) == 100_000 and int(seller.transfer_budget) == s0 + 100_000
    assert desk.finance_summary().overdue_payable == terms.deferred - 100_000
    other = _negotiable(desk, seller)
    with pytest.raises(DeskError, match="Gecikmiş transfer ödemelerin"):
        desk.make_bid(other.id, DealTerms(fee=0))
    user.transfer_budget = 100_000_000
    db.flush()
    desk._process_payments()
    rows = _payments(db, deal_id, "INSTALMENT")
    assert all(r.status == "PAID" for r in rows) and sum(r.paid_amount for r in rows) == terms.deferred
    assert int(seller.transfer_budget) == s0 + terms.deferred
    assert db.scalar(select(func.min(Team.transfer_budget))) >= 0


@DB
def test_loyalty_bonus_and_match_bonus_and_broken_promise(db):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    deal_id = _agree(desk, player, _generous(asking),
                     {"loyalty_bonus": 200_000, "appearance_bonus": 5_000, "goal_bonus": 20_000,
                      "release_clause": 90_000_000, "role": SquadRole.FIRST_TEAM})
    desk.complete(deal_id, shift_wage_room=True)
    contract = ContractOffer.from_dict(db.get(TransferDeal, deal_id).contract)
    loyalty = _payments(db, deal_id, "LOYALTY")
    assert len(loyalty) == contract.years and all(r.due_season > cm.season for r in loyalty)
    assert player.release_clause == 90_000_000 and player.contract_clauses["appearance_bonus"] == 5_000
    fixture = db.scalar(select(Fixture).where(Fixture.season == cm.season, Fixture.week == cm.current_week,
                                              (Fixture.home_team_id == user.id) | (Fixture.away_team_id == user.id)))
    db.add(PlayerMatchStat(fixture_id=fixture.id, player_id=player.id, team_id=user.id, minutes=80, goals=2,
                           rating=8.0))
    db.flush()
    u0 = int(user.transfer_budget)
    desk._pay_match_bonuses(cm.current_week)
    desk._pay_match_bonuses(cm.current_week)
    assert int(user.transfer_budget) == u0 - (5_000 + 2 * 20_000)
    cm.state.season += 1
    db.flush()
    desk._process_payments()
    paid = [r for r in _payments(db, deal_id, "LOYALTY") if r.status == "PAID"]
    assert len(paid) == 1 and paid[0].due_season == cm.season
    # rol sozu tutulmadi: bir kez, moral duser, oyuncu ayrilmak ister
    player.concern_level = 3
    morale = int(player.morale)
    db.flush()
    desk._check_promises()
    desk._check_promises()
    assert player.contract_clauses.get("wants_away") and player.contract_clauses.get("promise_broken")
    assert int(player.morale) == max(0, morale + transfer_desk.PROMISE_BROKEN_MORALE)


# ===========================================================================
# 4) SATIS: GELEN TEKLIFLER VE SERBEST KALMA BEDELI
# ===========================================================================

def _incoming(db, cm, desk, monkeypatch) -> list:
    monkeypatch.setattr(transfer_desk, "AI_BID_LISTED_CHANCE", 1.0)
    user = cm.user_team
    for p in sorted(user.players, key=lambda p: (p.overall_rating, p.id))[:10]:
        if p.position is not Position.GK:
            desk.set_listing(p.id, transfer=True)
    for _bump in range(4):
        desk._ai_incoming_bids(True)
        views = desk.incoming()
        if views:
            return views
        _jump(cm, 1)
    return []


@DB
def test_incoming_ai_bid_accept_completes_and_schedules_receivables(db, monkeypatch):
    cm, desk = _desk(db)
    user = cm.user_team
    views = _incoming(db, cm, desk, monkeypatch)
    if not views:
        pytest.skip("bu tohumda AI teklifi gelmedi")
    view = views[0]
    assert view.direction == "OUT" and view.can_accept_offer and view.expires_in_weeks
    buyer = db.get(Team, view.buyer_team_id)
    u0, b0 = int(user.transfer_budget), int(buyer.transfer_budget)
    player = db.get(Player, view.player_id)
    done = desk.accept_offer(view.id)
    if done.status == "COLLAPSED":
        pytest.skip(f"oyuncu AI kulübüyle anlaşamadı: {done.reason}")
    assert done.status == "COMPLETED" and player.team_id == buyer.id
    deal = db.get(TransferDeal, view.id)
    assert int(user.transfer_budget) == u0 + int(deal.upfront)
    assert int(buyer.transfer_budget) <= b0 - int(deal.upfront)          # (+ olasi maas kaydirmasi)
    receivable = _payments(db, deal.id, "INSTALMENT")
    assert sum(r.amount for r in receivable) == int(deal.fee) - int(deal.upfront)
    assert all(r.payer_team_id == buyer.id and r.payee_team_id == user.id for r in receivable)
    assert player.transfer_listed is False


@DB
def test_incoming_ai_bid_counter_and_reject(db, monkeypatch):
    cm, desk = _desk(db)
    views = _incoming(db, cm, desk, monkeypatch)
    if not views:
        pytest.skip("bu tohumda AI teklifi gelmedi")
    view = views[0]
    countered = desk.counter_offer(view.id, DealTerms(fee=view.terms.fee * 3))
    assert countered.status in ("REJECTED", "BIDDING", "COMPLETED", "AGREED", "COLLAPSED")
    if countered.status == "REJECTED":
        assert "geri çekti" in countered.reason
    with pytest.raises(DeskError):
        desk.counter_offer(view.id, DealTerms(fee=1, exchange_player_id=cm.user_team.players[0].id))
    others = [v for v in desk.incoming() if v.id != view.id and v.can_reject_offer]
    if others:
        assert desk.reject_offer(others[0].id, "Satılık değil").status == "REJECTED"


@DB
def test_release_clause_is_triggered_by_an_ai_club_and_cannot_be_refused(db, monkeypatch):
    cm, desk = _desk(db)
    user = cm.user_team
    monkeypatch.setattr(rules, "RELEASE_TRIGGER_CHANCE", 1.0)
    candidates = sorted((p for p in user.players if p.position is not Position.GK),
                        key=lambda p: (-p.overall_rating, p.id))
    for p in candidates:
        p.release_clause = max(transfer_desk.MIN_RELEASE_CLAUSE, int(p.market_value * 0.8))
    db.flush()
    u0 = int(user.transfer_budget)
    desk._release_clause_triggers(True)
    moved = [p for p in candidates if p.team_id != user.id]
    if not moved:
        pytest.skip("uygun AI alıcı yok")
    player = moved[0]
    deal = db.scalar(select(TransferDeal).where(TransferDeal.player_id == player.id))
    assert deal.status == "COMPLETED" and deal.last_action == "RELEASE" and int(deal.fee) == int(deal.upfront)
    assert int(user.transfer_budget) >= u0 + int(deal.fee)
    assert player.release_clause is None                               # yeni sozlesme
    assert db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.kind == "RUMOUR"))


@DB
def test_asking_price_and_listing_are_validated(db):
    cm, desk = _desk(db)
    own = cm.user_team.players[4]
    desk.set_asking_price(own.id, 12_000_000)
    desk.set_listing(own.id, transfer=True, loan=True)
    assert (own.asking_price, own.transfer_listed, own.loan_listed) == (12_000_000, True, True)
    with pytest.raises(DeskError):
        desk.set_asking_price(own.id, -5)
    with pytest.raises(DeskError, match="senin oyuncun değil"):
        desk.set_asking_price(cm.find_team(SELLER).players[0].id, 1)
    desk.set_asking_price(own.id, None)
    assert own.asking_price is None


# ===========================================================================
# 5) HAFTA ENTEGRASYONU VE ESKI AKIS
# ===========================================================================

@DB
def test_play_week_runs_the_desk_without_errors_or_cm_rng_draws(db, caplog):
    cm, desk = _desk(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = _negotiable(desk, seller, young=False)
    asking = transfers.asking_price(player, seller, user.reputation)
    user.transfer_budget += 50_000_000
    user.wage_budget += 2_000_000
    db.flush()
    deal_id = _agree(desk, player, _generous(asking, add_ons=(AddOn("APPEARANCES", 1, 100_000),)))
    desk.complete(deal_id, shift_wage_room=True)
    desk.scout(cm.find_team(FOREIGN).players[0].id)
    state = cm.rng.getstate()
    report = WeekReport(season=cm.season, week=cm.current_week, focus_team_id=user.id)
    with caplog.at_level(logging.ERROR, logger="transfer_desk"):
        transfer_desk.run_week(cm, cm.current_week, report)
    assert cm.rng.getstate() == state
    assert not [r for r in caplog.records if "başarısız" in r.getMessage()]
    cm.run_ai_transfer_window = lambda: []
    with caplog.at_level(logging.ERROR, logger="transfer_desk"):
        week_report = cm.play_week()
    assert not [r for r in caplog.records if "başarısız" in r.getMessage()]
    assert isinstance(week_report.transfer_notes, list)
    assert db.scalar(select(ScoutAssignment.knowledge)) > 0


@DB
def test_legacy_simple_flow_still_works(db):
    cm = _manager(db)
    user, seller = cm.user_team, cm.find_team(SELLER)
    player = sorted(seller.players, key=lambda p: (p.overall_rating, p.id))[8]
    fee = transfers.asking_price(player, seller, user.reputation) * 3
    user.transfer_budget += fee
    db.flush()
    decision = cm.offer_fee(user, player, fee)
    negotiation = cm.open_negotiation(user, player, fee)
    assert decision.accepted and negotiation.open and not negotiation.agent
    response = negotiation.respond(negotiation.demand)
    assert response.status is NegotiationStatus.ACCEPTED
    user.wage_budget += negotiation.demand.wage
    db.flush()
    b0 = int(user.transfer_budget)
    news = cm.complete_transfer(user, player, fee, negotiation.demand)
    assert news.fee == fee and int(user.transfer_budget) == b0 - fee
    assert db.scalar(select(TransferLog.fee).where(TransferLog.player_id == player.id)) == fee


@DB
def test_old_save_is_upgraded_in_place_and_the_desk_works():
    """13H oncesi kayit: yeni tablolar ve oyuncu sutunlari yok -> upgrade_schema ekler, veri korunur, masa calisir."""
    from sqlalchemy import text

    import seed

    schema = "test_13h_old_save"
    with database.career_context(schema):
        database.drop_career_schema(schema)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            with database.career_connection() as conn:
                budgets = conn.execute(text("SELECT id, transfer_budget FROM teams ORDER BY id")).all()
                for table in ("transfer_payments", "scout_assignments", "transfer_deals"):
                    conn.exec_driver_sql(f'DROP TABLE "{table}"')
                for column in ("release_clause", "asking_price", "contract_clauses"):
                    conn.exec_driver_sql(f'ALTER TABLE "players" DROP COLUMN "{column}"')
            problems = set(database.schema_problems())
            assert {"eksik tablo: transfer_deals", "eksik sütun: players.contract_clauses"} <= problems
            applied = set(database.upgrade_schema())
            assert {"tablo eklendi: transfer_deals", "tablo eklendi: transfer_payments",
                    "sütun eklendi: players.release_clause", "sütun eklendi: players.contract_clauses"} <= applied
            assert database.upgrade_schema() == [] and database.schema_problems() == []
            with database.career_connection() as conn:
                assert conn.execute(text("SELECT id, transfer_budget FROM teams ORDER BY id")).all() == budgets
                assert conn.scalar(text("SELECT count(*) FROM players WHERE contract_clauses <> '{}'::jsonb")) == 0
            session = database.SessionLocal()
            try:
                cm = CareerManager(session, seed=5)
                cm.set_user_team(cm.find_team(USER))
                desk = TransferDesk(cm)
                assert desk.enquire(cm.find_team(SELLER).players[6].id).status == "ENQUIRY"
                assert cm.play_week().played_any
            finally:
                session.rollback()
                session.close()
        finally:
            database.drop_career_schema(schema)


def test_schema_is_additive_and_versioned():
    additive = {(t, c) for t, c, _ddl in database.ADDITIVE_COLUMNS}
    assert {("players", "release_clause"), ("players", "asking_price"), ("players", "contract_clauses")} <= additive
    assert database.SCHEMA_VERSION >= 16
    tables = database.Base.metadata.tables
    assert {"transfer_deals", "transfer_payments", "scout_assignments"} <= set(tables)
    assert "uq_transfer_payment_ref" in {i.name for i in tables["transfer_payments"].indexes if i.unique}
    assert "uq_transfer_deal_open" in {i.name for i in tables["transfer_deals"].indexes if i.unique}


# ===========================================================================
# 6) ES ZAMANLILIK: IKI THREAD AYNI ANLASMAYI TAMAMLAR (commit eder; modul sonunda dunya yeniden kurulur)
# ===========================================================================

def _reseed() -> None:
    import seed

    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")


@pytest.fixture(scope="module", autouse=True)
def _restore_world_after_module():
    yield
    if _db_available() and _COMMITTED["dirty"]:
        _reseed()


_COMMITTED = {"dirty": False}


@DB
def test_two_threads_completing_the_same_deal_move_money_once():
    _COMMITTED["dirty"] = True
    with database.session_scope() as db:
        cm = _manager(db)
        desk = TransferDesk(cm)
        user, seller = cm.user_team, cm.find_team(SELLER)
        player = _negotiable(desk, seller, young=False)
        asking = transfers.asking_price(player, seller, user.reputation)
        user.transfer_budget += 50_000_000
        user.wage_budget += 2_000_000
        db.flush()
        deal_id = _agree(desk, player, _generous(asking))
        player_id, user_id, seller_id = player.id, user.id, seller.id
    with database.session_scope() as db:
        b0 = int(db.get(Team, user_id).transfer_budget)
        s0 = int(db.get(Team, seller_id).transfer_budget)
    barrier = threading.Barrier(2, timeout=30)
    results: list = [None, None]

    def run(index: int) -> None:
        try:
            with database.session_scope() as session:
                cm = CareerManager(session, seed=7)
                desk = TransferDesk(cm)
                barrier.wait()
                results[index] = ("ok", desk.complete(deal_id).status)
        except DeskError as exc:
            results[index] = ("error", str(exc))
        except Exception as exc:                                   # noqa: BLE001 - her hata gorunur olmali
            results[index] = ("crash", repr(exc))

    threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(90)
    assert not any(t.is_alive() for t in threads)
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["error", "ok"], results
    with database.session_scope() as db:
        deal = db.get(TransferDeal, deal_id)
        contract = ContractOffer.from_dict(deal.contract)
        assert deal.status == "COMPLETED" and db.get(Player, player_id).team_id == user_id
        assert int(db.get(Team, seller_id).transfer_budget) == s0 + int(deal.fee)
        assert int(db.get(Team, user_id).transfer_budget) == b0 - int(deal.fee) - contract.signing_fee - \
            contract.agent_fee
        assert db.scalar(select(func.count()).select_from(TransferLog).where(TransferLog.player_id == player_id)) == 1
        assert db.scalar(select(func.count()).select_from(TransferPayment).where(
            TransferPayment.deal_id == deal_id, TransferPayment.kind == "UPFRONT")) == 1
