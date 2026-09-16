"""
Finans, teknik heyet ve transfer testleri (5. Asama).

Saf kurallar (finance / staff / transfers) DB'siz; entegrasyon testleri gercek
PostgreSQL'e karsi rollback ile calisir.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import staff as staff_rules  # noqa: E402
import transfers  # noqa: E402
from finance import (  # noqa: E402
    WEEKS_PER_YEAR,
    BudgetError,
    auto_shift_for_wage,
    expected_wage,
    format_money,
    market_value,
    max_shiftable_to_transfer,
    max_shiftable_to_wages,
    plan_budget_shift,
    wage_summary,
    weekly_to_transfer,
)
from models import Position, SquadRole, StaffRole  # noqa: E402
from transfers import (  # noqa: E402
    ContractNegotiation,
    ContractOffer,
    NegotiationStatus,
    asking_price,
    fee_acceptance_probability,
)

# ===========================================================================
# Sahte nesneler
# ===========================================================================

@dataclass
class FakePlayer:
    id: int
    name: str
    position: Position
    overall_rating: int
    age: int = 26
    market_value: int = 0
    current_wage: int = 0
    contract_years: int = 3
    squad_role: SquadRole = SquadRole.FIRST_TEAM
    is_starter: bool = False
    team: object = None

    def __post_init__(self):
        if not self.market_value:
            self.market_value = market_value(self.overall_rating, self.age, self.position)


@dataclass
class FakeTeam:
    id: int
    name: str
    reputation: int
    players: list = field(default_factory=list)
    transfer_budget: int = 50_000_000
    wage_budget: int = 1_000_000


def make_squad(team, ratings, position=Position.MID):
    team.players = [
        FakePlayer(100 * team.id + i, f"{team.name}{i}", position, r, team=team)
        for i, r in enumerate(ratings)
    ]
    return team.players


# ===========================================================================
# 1) DEGERLEME
# ===========================================================================

def test_market_value_grows_with_overall_and_peaks_by_age():
    assert market_value(60, 26, Position.MID) < market_value(70, 26, Position.MID)
    assert market_value(70, 26, Position.MID) < market_value(85, 26, Position.MID)
    # Ayni OVR: zirve yastaki oyuncu, 34 yasindakinden cok daha degerli
    assert market_value(80, 24, Position.MID) > market_value(80, 34, Position.MID) * 3
    # Forvet en pahali, kaleci en ucuz mevki
    assert (market_value(80, 26, Position.FWD) > market_value(80, 26, Position.MID)
            > market_value(80, 26, Position.DEF) > market_value(80, 26, Position.GK))


def test_expected_wage_scales_with_overall_reputation_and_role():
    assert expected_wage(85, 85) > expected_wage(70, 85)
    assert expected_wage(80, 92) > expected_wage(80, 73)          # buyuk kulup daha cok oder
    assert (expected_wage(80, 85, SquadRole.STAR)
            > expected_wage(80, 85, SquadRole.FIRST_TEAM)
            > expected_wage(80, 85, SquadRole.BACKUP))


def test_format_money():
    assert format_money(12_500_000) == "12.5M EUR"
    assert format_money(850_000) == "850K EUR"
    assert format_money(-250_000) == "-250K EUR"
    assert format_money(900) == "900 EUR"


# ===========================================================================
# 2) BUTCE KAYDIRMA
# ===========================================================================

def test_weekly_to_transfer_uses_52_weeks():
    assert WEEKS_PER_YEAR == 52
    assert weekly_to_transfer(10_000) == 520_000        # kullanicinin ornegi


def test_shift_transfer_to_wages_costs_52x():
    transfer, wage = plan_budget_shift(5_000_000, 800_000, +10_000, committed_weekly=700_000)
    assert transfer == 5_000_000 - 520_000
    assert wage == 810_000


def test_shift_wages_back_to_transfer_refunds_52x():
    transfer, wage = plan_budget_shift(1_000_000, 800_000, -50_000, committed_weekly=700_000)
    assert transfer == 1_000_000 + 2_600_000
    assert wage == 750_000


def test_shift_blocked_when_transfer_budget_insufficient():
    with pytest.raises(BudgetError, match="Transfer bütçesi"):
        plan_budget_shift(100_000, 800_000, +10_000, committed_weekly=700_000)


def test_shift_blocked_below_committed_wages():
    with pytest.raises(BudgetError, match="maaş yükünün"):
        plan_budget_shift(5_000_000, 800_000, -150_000, committed_weekly=700_000)


def test_zero_shift_rejected():
    with pytest.raises(BudgetError):
        plan_budget_shift(1_000_000, 500_000, 0, committed_weekly=0)


def test_shift_limits():
    assert max_shiftable_to_wages(5_200_000) == 100_000
    assert max_shiftable_to_transfer(800_000, 700_000) == 100_000
    assert max_shiftable_to_transfer(800_000, 900_000) == 0        # zaten asim var


def test_wage_summary_detects_overspending():
    ok = wage_summary(600_000, 100_000, 800_000)
    assert ok.total == 700_000 and ok.free == 100_000 and not ok.overspending
    assert round(ok.usage_pct) == 88
    over = wage_summary(800_000, 100_000, 800_000)
    assert over.free == -100_000 and over.overspending


def test_auto_shift_for_wage_respects_reserved_fee():
    # 20K acik var, kasada 5.2M ama 5M bonservis icin ayrilmis -> sadece 200K serbest
    assert auto_shift_for_wage(5_200_000, 800_000, 0, 20_000, reserve_fee=5_000_000) == 3_846
    assert auto_shift_for_wage(5_200_000, 800_000, 0, 20_000, reserve_fee=0) == 20_000
    assert auto_shift_for_wage(5_200_000, 800_000, 50_000, 20_000) == 0      # zaten yer var


# ===========================================================================
# 3) TEKNIK HEYET ETKILERI
# ===========================================================================

def test_physio_shortens_injuries_and_absence_lengthens():
    assert staff_rules.apply_injury_multiplier(4, 20) == 2      # kullanicinin ornegi
    assert staff_rules.apply_injury_multiplier(4, 10) == 4
    assert staff_rules.apply_injury_multiplier(4, 1) > 4
    assert staff_rules.apply_injury_multiplier(4, None) > 4     # saglikci yok
    assert staff_rules.apply_injury_multiplier(1, 20) == 1      # asla 0 olmaz


def test_coach_scales_form_gain_and_softens_loss():
    assert staff_rules.apply_training(10, 20) > 10 > staff_rules.apply_training(10, 1)
    assert staff_rules.apply_training(10, None) == 10           # personel yoksa notr
    # Iyi antrenor dususu frenler, kotu antrenor buyutur
    assert staff_rules.apply_training(-10, 20) > -10
    assert staff_rules.apply_training(-10, 1) < -10
    assert staff_rules.apply_training(0, 20) == 0


def test_coach_attribute_depends_on_position():
    assert staff_rules.coach_attribute_for(Position.FWD) == "attacking"
    assert staff_rules.coach_attribute_for(Position.MID) == "attacking"
    assert staff_rules.coach_attribute_for(Position.DEF) == "defending"
    assert staff_rules.coach_attribute_for(Position.GK) == "defending"


def test_scout_margin_shrinks_with_ability():
    assert staff_rules.scout_margin(20) < staff_rules.scout_margin(10) < staff_rules.scout_margin(1)
    assert staff_rules.scout_margin(None) == staff_rules.NO_SCOUT_MARGIN


def test_scouted_value_is_stable_and_brackets_reality():
    a = staff_rules.scouted_value(80, 6, (3, 42, "overall_rating"))
    b = staff_rules.scouted_value(80, 6, (3, 42, "overall_rating"))
    assert (a.low, a.high) == (b.low, b.high)          # her acilista ayni
    assert not a.exact and a.high - a.low == 12
    # Farkli ozellik -> farkli sapma (sis her alanda ayni yonde degil)
    other = staff_rules.scouted_value(80, 6, (3, 42, "pace"))
    assert (other.low, other.high) != (a.low, a.high)
    exact = staff_rules.scouted_value(80, 0, (0, 42, "overall_rating"))
    assert exact.exact and exact.low == exact.high == 80


def test_scouted_value_clamped_to_valid_range():
    v = staff_rules.scouted_value(97, 10, (1, 2, "x"))
    assert 1 <= v.low <= v.high <= 99


def test_generate_attributes_only_fills_role_attributes():
    rng = random.Random(1)
    attrs = staff_rules.generate_attributes(rng, StaffRole.PHYSIO, 80)
    assert attrs["physiotherapy"] >= 10
    assert attrs["attacking"] == 1 and attrs["judging_ability"] == 1
    assert all(1 <= v <= 20 for v in attrs.values())


def test_staff_wage_scales_with_reputation():
    assert staff_rules.staff_wage(StaffRole.COACH, 90) > staff_rules.staff_wage(StaffRole.COACH, 40)
    assert staff_rules.staff_wage(StaffRole.ASSISTANT, 70) > staff_rules.staff_wage(StaffRole.SCOUT, 70)


# ===========================================================================
# 4) TRANSFER: 1. ASAMA (KULUP)
# ===========================================================================

def _buyer_seller():
    buyer = FakeTeam(1, "Alici", 88)
    seller = FakeTeam(2, "Satici", 78)
    make_squad(buyer, [84, 82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68])
    make_squad(seller, [83, 80, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66])
    return buyer, seller


def test_asking_price_above_market_and_higher_for_key_player():
    _buyer, seller = _buyer_seller()
    star, squad_player = seller.players[0], seller.players[-1]
    assert asking_price(star, seller) > star.market_value
    star_ratio = asking_price(star, seller) / star.market_value
    squad_ratio = asking_price(squad_player, seller) / squad_player.market_value
    assert star_ratio > squad_ratio


def test_asking_price_drops_as_contract_runs_down():
    _buyer, seller = _buyer_seller()
    p = seller.players[0]
    p.contract_years = 5
    long_deal = asking_price(p, seller)
    p.contract_years = 1
    assert asking_price(p, seller) < long_deal


def test_fee_probability_monotonic():
    asking = 10_000_000
    probs = [fee_acceptance_probability(f, asking) for f in (4_000_000, 8_000_000, 10_000_000, 14_000_000)]
    assert probs[0] == 0.0                       # ciddiye alinmaz
    assert probs[1] < probs[2] < probs[3]
    assert 0.4 < probs[2] < 0.6                  # istenen bedelde ~yazi tura
    assert probs[3] > 0.85                       # %40 fazlasinda neredeyse kesin


def test_lowball_always_rejected_and_generous_usually_accepted():
    buyer, seller = _buyer_seller()
    target = seller.players[0]
    rng = random.Random(1)
    asking = asking_price(target, seller, buyer.reputation)
    assert not transfers.evaluate_fee(rng, target, seller, int(asking * 0.3), 88).accepted
    accepted = sum(
        transfers.evaluate_fee(random.Random(s), target, seller, int(asking * 1.6), 88).accepted
        for s in range(40)
    )
    assert accepted >= 34


def test_club_refuses_to_sell_from_a_thin_squad():
    buyer, seller = _buyer_seller()
    seller.players = seller.players[:12]
    rng = random.Random(0)
    decision = transfers.evaluate_fee(rng, seller.players[0], seller, 99_000_000, 88)
    assert not decision.accepted and "Kadro" in decision.reason


# ===========================================================================
# 5) TRANSFER: 2. ASAMA (SOZLESME MASASI)
# ===========================================================================

def _negotiation(seed=1, overall=82, buyer_rep=88, seller_rep=78):
    buyer = FakeTeam(1, "Alici", buyer_rep)
    seller = FakeTeam(2, "Satici", seller_rep)
    make_squad(buyer, [84, 82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68])
    make_squad(seller, [overall, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65])
    target = seller.players[0]
    target.current_wage = expected_wage(overall, seller_rep)
    return ContractNegotiation(random.Random(seed), target, buyer, 15_000_000), target, buyer


def test_demand_is_reasonable_and_above_current_wage():
    n, target, _buyer = _negotiation()
    assert n.demand.wage > target.current_wage
    assert 1 <= n.demand.years <= 5
    assert n.demand.role in tuple(SquadRole)
    assert n.status is NegotiationStatus.OPEN and n.rounds_left == transfers.MAX_ROUNDS


def test_accepting_the_demand_closes_the_deal():
    n, _t, _b = _negotiation()
    response = n.respond(n.demand)
    assert response.status is NegotiationStatus.ACCEPTED
    assert n.status is NegotiationStatus.ACCEPTED


def test_offer_below_red_line_makes_player_walk_away():
    n, _t, _b = _negotiation()
    low = ContractOffer(wage=int(n.min_wage * 0.7), years=n.demand.years, role=n.demand.role)
    response = n.respond(low)
    assert response.status is NegotiationStatus.WALKED_AWAY
    assert "masadan kalktı" in response.message
    assert not n.open
    with pytest.raises(transfers.TransferError):
        n.respond(n.demand)


def test_role_insult_ends_negotiation():
    n, _t, _b = _negotiation()
    if n.demand.role is not SquadRole.STAR:
        pytest.skip("oyuncu yıldız rolü talep etmedi")
    offer = ContractOffer(wage=n.demand.wage * 2, years=n.demand.years, role=SquadRole.BACKUP)
    assert n.respond(offer).status is NegotiationStatus.WALKED_AWAY


def test_offer_just_above_red_line_is_not_auto_accepted():
    """Kirmizi cizgiyi gecmek imza icin yetmez; pazarlik anlamli kalmali."""
    n, _t, _b = _negotiation()
    offer = ContractOffer(wage=n.min_wage + 500, years=n.demand.years, role=n.demand.role)
    response = n.respond(offer)
    assert response.status is NegotiationStatus.OPEN and response.counter is not None


def test_generous_offer_is_accepted():
    n, _t, _b = _negotiation()
    offer = ContractOffer(wage=int(n.demand.wage * 0.99), years=n.demand.years, role=n.demand.role)
    assert n.respond(offer).status is NegotiationStatus.ACCEPTED


def test_slightly_low_offer_gets_a_counter_between_the_two():
    n, _t, _b = _negotiation()
    demand_wage = n.demand.wage
    offer = ContractOffer(wage=int(demand_wage * 0.88), years=n.demand.years, role=n.demand.role)
    response = n.respond(offer)
    assert response.status is NegotiationStatus.OPEN
    assert response.counter is not None
    assert offer.wage < response.counter.wage <= demand_wage
    assert response.complaints


def test_patience_runs_out_after_max_rounds():
    n, _t, _b = _negotiation()
    statuses = []
    for _ in range(transfers.MAX_ROUNDS + 1):
        if not n.open:
            break
        # Tam kirmizi cizgide, kisa sozlesme: asla tatmin etmez ama hakaret de degil
        offer = ContractOffer(wage=n.min_wage, years=1, role=n.demand.role)
        statuses.append(n.respond(offer).status)
    assert statuses[-1] is NegotiationStatus.WALKED_AWAY
    assert n.rounds_used <= transfers.MAX_ROUNDS


def test_step_down_in_reputation_costs_more():
    """Ayni alici icin: itibarli kulupten gelen oyuncu 'ikna primi' ister."""
    from_big, _t, _b = _negotiation(buyer_rep=80, seller_rep=95)
    from_small, _t2, _b2 = _negotiation(buyer_rep=80, seller_rep=65)
    assert from_big.demand.wage > from_small.demand.wage


def test_invalid_contract_length_is_rejected():
    n, _t, _b = _negotiation()
    assert n.respond(ContractOffer(n.demand.wage, 9, n.demand.role)).status is NegotiationStatus.WALKED_AWAY


# ===========================================================================
# 6) AI TRANSFER MANTIGI
# ===========================================================================

def test_squad_needs_ranks_weakest_position_first():
    team = FakeTeam(1, "T", 80)
    team.players = (
        [FakePlayer(1, "gk", Position.GK, 80), FakePlayer(2, "gk2", Position.GK, 78)]
        + [FakePlayer(10 + i, f"d{i}", Position.DEF, 79) for i in range(4)]
        + [FakePlayer(20 + i, f"m{i}", Position.MID, 78) for i in range(5)]
        + [FakePlayer(30 + i, f"f{i}", Position.FWD, 62) for i in range(4)]   # zayif nokta
    )
    averages = {Position.GK: 79.0, Position.DEF: 79.0, Position.MID: 78.0, Position.FWD: 80.0}
    needs = transfers.squad_needs(team, averages)
    assert needs[0].position is Position.FWD and needs[0].shortfall > 15
    assert needs[-1].shortfall == 0


def test_target_score_prefers_upgrades_and_young_players():
    team = FakeTeam(1, "T", 80)
    make_squad(team, [70] * 15, position=Position.FWD)
    need = transfers.SquadNeed(Position.FWD, 70.0, 8.0)
    young = FakePlayer(99, "genc", Position.FWD, 82, age=23)
    old = FakePlayer(98, "yasli", Position.FWD, 82, age=34)
    worse = FakePlayer(97, "zayif", Position.FWD, 64, age=25)
    assert transfers.target_score(young, team, need) > transfers.target_score(old, team, need)
    assert transfers.target_score(worse, team, need) < transfers.target_score(young, team, need)


def test_ai_opening_offer_respects_budget():
    rng = random.Random(3)
    assert transfers.ai_opening_offer(rng, 20_000_000, 5_000_000) <= 5_000_000
    assert 0 < transfers.ai_opening_offer(rng, 10_000_000, 99_000_000) <= 11_000_000


def test_ai_contract_offer_is_acceptable_when_funds_allow():
    n, _t, _b = _negotiation(seed=7)
    offer = transfers.ai_contract_offer(random.Random(7), n, free_weekly=10_000_000)
    assert n.respond(offer).status is NegotiationStatus.ACCEPTED
