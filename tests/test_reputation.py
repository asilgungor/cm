"""
Menajer tanınırlığı ve ikna formulu testleri (6. Asama).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reputation  # noqa: E402
import transfers  # noqa: E402
from finance import expected_wage  # noqa: E402
from models import SquadRole  # noqa: E402
from tests.test_finance import FakeTeam, make_squad  # noqa: E402
from transfers import (  # noqa: E402
    CLUB_GOALS_MESSAGE,
    MANAGER_MESSAGE,
    ContractNegotiation,
    ContractOffer,
    NegotiationStatus,
    check_interest,
    persuasion_score,
    wage_offer_score,
)

# ===========================================================================
# 1) Menajer tanınırlığı kurallari
# ===========================================================================

def test_match_delta_rewards_wins_and_upsets():
    win = reputation.match_delta("W", 80, 80, 1)
    upset = reputation.match_delta("W", 75, 90, 1)
    draw = reputation.match_delta("D", 80, 80, 0)
    loss = reputation.match_delta("L", 80, 80, -1)
    thrashing = reputation.match_delta("L", 80, 80, -4)
    assert upset > win > draw > 0 > loss > thrashing


def test_season_delta_by_position():
    assert reputation.season_delta(1, 4) == reputation.SEASON_CHAMPION
    assert reputation.season_delta(2, 4) == reputation.SEASON_TOP_HALF
    assert reputation.season_delta(3, 4) == reputation.SEASON_BOTTOM_HALF
    assert reputation.season_delta(4, 4) == reputation.SEASON_LAST
    assert reputation.season_delta(1, 1) == 0.0


def test_reputation_is_clamped_to_1_20():
    assert reputation.apply(19.9, 5) == 20.0
    assert reputation.apply(1.05, -3) == 1.0


def test_labels_and_ai_manager():
    assert reputation.label(3) == "Tanınmıyor"
    assert reputation.label(8) == "Yerel"
    assert reputation.label(19.5) == "Dünyaca ünlü"
    assert reputation.ai_manager_reputation(92) > reputation.ai_manager_reputation(70)
    assert 1 <= reputation.ai_manager_reputation(20) <= reputation.ai_manager_reputation(99) <= 20


# ===========================================================================
# 2) Ikna formulu
# ===========================================================================

def test_persuasion_formula_weights_exactly_40_30_30():
    # Takim 80, menajer 10/20 (=50), maas puani 100
    assert persuasion_score(80, 10, 100) == pytest.approx(0.4 * 80 + 0.3 * 50 + 0.3 * 100)
    # Her bilesen tek basina 0-100 olcekte: maksimumlar agirliklarina esit katki yapar
    assert persuasion_score(100, 1, 0) == pytest.approx(40 + 1.5)
    assert persuasion_score(1, 20, 0) == pytest.approx(0.4 + 30)
    assert persuasion_score(1, 1, 100) == pytest.approx(0.4 + 1.5 + 30)


def test_wage_offer_score_curve():
    assert wage_offer_score(82, 100) == pytest.approx(0)
    assert wage_offer_score(90, 100) == pytest.approx(50)
    assert wage_offer_score(98, 100) == pytest.approx(100)
    assert wage_offer_score(150, 100) == 100            # tavan
    assert wage_offer_score(50, 100) == 0               # taban


def test_world_star_refuses_small_club_even_with_money():
    """Kullanicinin ornegi: 90 OVR dunya yildizi, 70 itibarli takima gelmez."""
    check = check_interest(90, team_reputation=70, manager_reputation=10)
    assert not check.interested
    assert check.reason == CLUB_GOALS_MESSAGE


def test_unknown_manager_blocks_star_at_big_club():
    check = check_interest(92, team_reputation=94, manager_reputation=3)
    assert not check.interested and check.reason == MANAGER_MESSAGE
    assert check_interest(92, team_reputation=94, manager_reputation=16).interested


def test_average_player_accepts_mid_club():
    assert check_interest(74, team_reputation=72, manager_reputation=6).interested


# ===========================================================================
# 3) Sozlesme masasi davranisi
# ===========================================================================

def _table(overall=82, buyer_rep=88, seller_rep=78, manager=10.0, seed=1):
    buyer = FakeTeam(1, "Alici", buyer_rep)
    seller = FakeTeam(2, "Satici", seller_rep)
    make_squad(buyer, [84, 82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68])
    make_squad(seller, [overall] + [70] * 14)
    target = seller.players[0]
    target.current_wage = expected_wage(overall, seller_rep)
    return ContractNegotiation(random.Random(seed), target, buyer, 20_000_000, manager_reputation=manager)


def test_uninterested_player_leaves_table_immediately():
    n = _table(overall=91, buyer_rep=70, seller_rep=90, manager=5)
    assert n.status is NegotiationStatus.WALKED_AWAY and not n.open
    assert CLUB_GOALS_MESSAGE in n.opening_message or MANAGER_MESSAGE in n.opening_message
    with pytest.raises(transfers.TransferError):
        n.respond(n.demand)


def test_meeting_demand_always_signs_once_interested():
    for manager in (4.0, 10.0, 18.0):
        n = _table(manager=manager)
        if n.open:
            assert n.respond(n.demand).status is NegotiationStatus.ACCEPTED


def test_prestige_surplus_lets_player_accept_lower_wage():
    """Ayni teklif: unlu menajer + buyuk kulup ikna eder, siradan olan etmez."""
    famous = _table(buyer_rep=95, manager=19)
    modest = _table(buyer_rep=78, manager=8)
    assert famous.open and modest.open
    ratio = 0.88
    famous_offer = ContractOffer(int(famous.demand.wage * ratio), famous.demand.years, famous.demand.role)
    modest_offer = ContractOffer(int(modest.demand.wage * ratio), modest.demand.years, modest.demand.role)
    assert famous.persuasion(famous_offer) > modest.persuasion(modest_offer)
    assert famous.respond(famous_offer).status is NegotiationStatus.ACCEPTED
    assert modest.respond(modest_offer).status is NegotiationStatus.OPEN


def test_role_conflict_spikes_wage_demand():
    n = _table()
    wanted = n.demand
    if wanted.role is SquadRole.BACKUP:
        pytest.skip("oyuncu zaten yedek rolü istiyor")
    lower = transfers.ContractNegotiation._min_role(wanted.role)
    response = n.respond(ContractOffer(wanted.wage, wanted.years, lower))
    assert response.status is NegotiationStatus.OPEN
    assert response.counter.wage == pytest.approx(wanted.wage * transfers.ROLE_CONFLICT_WAGE_SPIKE, abs=100)
    assert response.counter.role is lower
    assert "fırladı" in response.message
    # Eski maasla alt rol artik yetmez; yeni talep kabul edilir
    assert n.respond(ContractOffer(response.counter.wage, wanted.years, lower)).status is NegotiationStatus.ACCEPTED


def test_role_spike_happens_only_once():
    n = _table()
    if n.demand.role is SquadRole.BACKUP:
        pytest.skip("oyuncu zaten yedek rolü istiyor")
    lower = transfers.ContractNegotiation._min_role(n.demand.role)
    first = n.respond(ContractOffer(n.demand.wage, n.demand.years, lower)).counter.wage
    second = n.respond(ContractOffer(int(first * 0.9), n.demand.years, lower))
    assert second.counter is None or second.counter.wage <= first


# ===========================================================================
# 4) Menajer seviyeleri (SM tarzi 10 basamak)
# ===========================================================================

def test_manager_levels_are_monotonic_and_cover_the_scale():
    grid = [reputation.MIN_REPUTATION + i * 0.01 for i in range(1901)]      # 1.00 .. 20.00
    previous = 0
    seen = set()
    for rep in grid:
        lv = reputation.level(rep)
        assert 1 <= lv.level <= reputation.MAX_LEVEL and lv.level >= previous
        assert 0.0 <= lv.progress <= 1.0
        assert lv.title == reputation.MANAGER_LEVELS[lv.level - 1][1]
        previous = lv.level
        seen.add(lv.level)
    assert seen == set(range(1, 11)) and reputation.MAX_LEVEL == 10
    # Ayni seviye icinde ilerleme tanınırlıkla artar
    assert reputation.level(9.6).progress < reputation.level(10.0).progress < reputation.level(10.7).progress


def test_manager_level_boundaries():
    thresholds = [t for t, _ in reputation.MANAGER_LEVELS]
    titles = [title for _, title in reputation.MANAGER_LEVELS]
    assert thresholds == sorted(set(thresholds)) and thresholds[0] == reputation.MIN_REPUTATION
    assert thresholds[-1] <= reputation.MAX_REPUTATION
    steps = [b - a for a, b in zip(thresholds[1:], thresholds[2:], strict=False)]
    assert steps == sorted(steps)                                  # ust basamaklar zorlasir
    assert len(set(titles)) == 10 and titles[0] == "Çaylak" and titles[-1] == "OFM Efsanesi"
    for number, (threshold, title) in enumerate(reputation.MANAGER_LEVELS, start=1):
        at = reputation.level(threshold)
        assert (at.level, at.title) == (number, title)
        if number < 10:
            assert at.progress == 0.0 and at.next_at == thresholds[number]
        if number > 1:
            below = reputation.level(threshold - 0.01)
            assert below.level == number - 1 and below.next_at == threshold and below.progress > 0.9
    top = reputation.level(reputation.MAX_REPUTATION)
    assert (top.level, top.title, top.next_at, top.progress) == (10, "OFM Efsanesi", None, 1.0)


def test_level_progress_start_and_clamping():
    start = reputation.level(reputation.START_REPUTATION)
    assert (start.level, start.title, start.next_at) == (1, "Çaylak", 8.5)
    assert start.progress == pytest.approx((8.0 - 1.0) / 7.5, abs=1e-4)
    halfway = reputation.level(9.0)
    assert (halfway.level, halfway.title, halfway.progress) == (2, "Deneyimli", 0.5)
    assert reputation.level(-3) == reputation.level(reputation.MIN_REPUTATION)
    assert reputation.level(25) == reputation.level(reputation.MAX_REPUTATION)


def test_level_badges():
    badges = [reputation.badge(n) for n in range(1, 11)]
    assert len(set(badges)) == 10 and all(badges)
    lv = reputation.level(13.6)
    assert reputation.badge(lv) == reputation.badge(lv.level) == badges[5]
    assert reputation.badge(0) == badges[0] and reputation.badge(99) == badges[-1]


def test_levels_do_not_change_reputation_labels():
    samples = (1, 4.99, 5, 8.99, 9, 12.99, 13, 16.99, 17, 20)
    assert [reputation.label(x) for x in samples] == [
        "Tanınmıyor", "Tanınmıyor", "Yerel", "Yerel", "Ulusal", "Ulusal", "Kıtasal", "Kıtasal",
        "Dünyaca ünlü", "Dünyaca ünlü",
    ]
    assert reputation.START_REPUTATION == 8.0 and reputation.MATCH_DELTA == {"W": 0.12, "D": 0.02, "L": -0.08}
