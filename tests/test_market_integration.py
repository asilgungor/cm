"""
Finans / transfer / teknik heyet ENTEGRASYON testleri (5. Asama).

Gercek PostgreSQL'e karsi calisir; her test kendi transaction'ini rollback eder.
DB yoksa hepsi atlanir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import finance  # noqa: E402
import staff as staff_rules  # noqa: E402
import transfers  # noqa: E402
from career_manager import CareerManager  # noqa: E402
from finance import WEEKS_PER_YEAR, BudgetError  # noqa: E402
from match_engine import EngineConfig  # noqa: E402
from models import Player, SquadRole, Staff, StaffRole, Team  # noqa: E402
from transfers import ContractOffer, NegotiationStatus, TransferError  # noqa: E402


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


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def cm(db):
    manager = CareerManager(db, seed=5)
    if manager.season_finished:
        pytest.skip("Sezon bitmiş; 'python seed.py' ile sıfırla")
    return manager


# ===========================================================================
# 1) SEED VERISININ FINANSAL TUTARLILIGI
# ===========================================================================

def test_seed_gives_every_team_two_budgets_and_staff(cm):
    for team in cm.teams():
        assert team.transfer_budget > 0
        assert team.wage_budget > 0
        assert team.wage_bill > 0
        assert team.free_wage >= 0, f"{team.name} maaş bütçesini aşıyor"
        assert team.staff, f"{team.name} teknik heyeti yok"
        roles = {s.role for s in team.staff}
        assert roles == set(StaffRole), f"{team.name} eksik rol: {set(StaffRole) - roles}"


def test_every_player_has_value_wage_and_contract(cm, db):
    for p in db.scalars(select(Player)):
        assert p.market_value > 0 and p.current_wage > 0
        assert 0 <= p.contract_years <= 6
        assert p.squad_role in tuple(SquadRole)
        assert p.market_value == finance.market_value(p.overall_rating, p.age, p.position)


def test_richer_clubs_have_bigger_budgets(cm):
    city = cm.find_team("Manchester City")
    trabzon = cm.find_team("Trabzonspor")
    assert city.transfer_budget > trabzon.transfer_budget
    assert city.wage_budget > trabzon.wage_budget


def test_free_agent_staff_pool_exists(cm, db):
    pool = cm.free_agent_staff()
    assert len(pool) >= 10
    assert all(s.team_id is None for s in pool)
    physios = cm.free_agent_staff(StaffRole.PHYSIO)
    assert physios and all(s.role is StaffRole.PHYSIO for s in physios)
    assert pool == sorted(pool, key=lambda s: -s.reputation)


# ===========================================================================
# 2) BUTCE KAYDIRMA
# ===========================================================================

def test_budget_shift_moves_money_both_ways(cm):
    team = cm.find_team("Galatasaray")
    t0, w0 = team.transfer_budget, team.wage_budget

    cm.shift_budget(team, +10_000)
    assert team.wage_budget == w0 + 10_000
    assert team.transfer_budget == t0 - 520_000

    cm.shift_budget(team, -10_000)
    assert team.wage_budget == w0 and team.transfer_budget == t0


def test_budget_shift_rejected_when_it_would_go_negative(cm):
    team = cm.find_team("Trabzonspor")
    t0, w0 = team.transfer_budget, team.wage_budget
    too_much = team.transfer_budget // WEEKS_PER_YEAR + 1_000

    with pytest.raises(BudgetError):
        cm.shift_budget(team, too_much)
    assert (team.transfer_budget, team.wage_budget) == (t0, w0)

    with pytest.raises(BudgetError):
        cm.shift_budget(team, -(team.free_wage + 1_000))
    assert (team.transfer_budget, team.wage_budget) == (t0, w0)


# ===========================================================================
# 3) TEKNIK HEYET: ISE ALMA / GONDERME VE ETKILER
# ===========================================================================

def test_hire_and_release_staff_updates_wage_bill(cm, db):
    team = cm.find_team("Fenerbahce")
    physio = team.staff_by_role(StaffRole.PHYSIO)[0]
    bill_before = team.wage_bill

    cm.release_staff(team, physio)
    assert physio.team_id is None
    assert team.wage_bill == bill_before - physio.wage
    assert physio in cm.free_agent_staff(StaffRole.PHYSIO)

    cm.shift_budget(team, +200_000)          # bol maas alani ac
    candidate = cm.free_agent_staff(StaffRole.PHYSIO)[0]
    assert candidate.reputation >= physio.reputation or candidate is physio   # itibara gore sirali
    cm.hire_staff(team, candidate)
    assert candidate.team_id == team.id
    assert team.wage_bill == bill_before - physio.wage + candidate.wage


def test_hire_blocked_without_wage_room(cm):
    team = cm.find_team("Besiktas")
    cm.release_staff(team, team.staff_by_role(StaffRole.PHYSIO)[0])
    cm.shift_budget(team, -cm.wage_summary(team).free)     # tum bos alani geri cek
    assert team.free_wage == 0
    with pytest.raises(TransferError, match="Maaş havuzunda yer yok"):
        cm.hire_staff(team, cm.free_agent_staff(StaffRole.PHYSIO)[0])


def test_hire_blocked_when_role_is_full(cm):
    team = cm.find_team("Milan")
    cm.shift_budget(team, +300_000)
    limit = staff_rules.MAX_PER_ROLE[StaffRole.PHYSIO]
    assert len(team.staff_by_role(StaffRole.PHYSIO)) == limit
    with pytest.raises(TransferError, match="kadrosu dolu"):
        cm.hire_staff(team, cm.free_agent_staff(StaffRole.PHYSIO)[0])


def test_release_other_clubs_staff_is_rejected(cm):
    team, other = cm.find_team("Inter"), cm.find_team("Napoli")
    with pytest.raises(TransferError, match="bu kulübün personeli değil"):
        cm.release_staff(team, other.staff[0])


def test_physio_quality_changes_injury_length(cm, db):
    """Ayni sakatlik, farkli sağlıkçı: iyi olan süreyi kısaltır."""
    team = cm.find_team("Juventus")
    physio = team.staff_by_role(StaffRole.PHYSIO)[0]

    physio.physiotherapy = 20
    db.flush()
    good = staff_rules.apply_injury_multiplier(4, cm.physio_rating(team))

    physio.physiotherapy = 1
    db.flush()
    bad = staff_rules.apply_injury_multiplier(4, cm.physio_rating(team))

    assert good == 2 and bad > 4


def test_scout_quality_changes_report_fog(cm, db):
    buyer = cm.find_team("Arsenal")
    target = cm.find_team("Liverpool").players[0]
    scout = buyer.staff_by_role(StaffRole.SCOUT)[0]

    scout.judging_ability = 20
    db.flush()
    sharp = cm.scouted_report(buyer, target)

    scout.judging_ability = 1
    db.flush()
    blurry = cm.scouted_report(buyer, target)

    assert sharp["margin"] < blurry["margin"]
    sharp_ovr, blurry_ovr = sharp["overall_rating"], blurry["overall_rating"]
    assert (sharp_ovr.high - sharp_ovr.low) < (blurry_ovr.high - blurry_ovr.low)
    assert not sharp_ovr.exact


def test_own_players_are_never_fogged(cm):
    team = cm.find_team("Napoli")
    report = cm.scouted_report(team, team.players[0])
    assert report["margin"] == 0
    assert report["overall_rating"].exact
    assert report["overall_rating"].low == team.players[0].overall_rating
    assert report["market_value"].low == team.players[0].market_value


# ===========================================================================
# 4) TRANSFER AKISI (IKI ASAMA)
# ===========================================================================

def _rich_buyer(cm, name="Manchester City"):
    team = cm.find_team(name)
    team.transfer_budget = 500_000_000
    team.wage_budget = team.wage_bill + 2_000_000
    cm.db.flush()
    return team


def test_fee_offer_rejected_without_budget(cm):
    buyer = cm.find_team("Trabzonspor")
    buyer.transfer_budget = 1_000
    cm.db.flush()
    target = cm.find_team("Manchester City").players[0]
    with pytest.raises(TransferError, match="Transfer bütçen yetersiz"):
        cm.offer_fee(buyer, target, 50_000_000)


def test_cannot_buy_own_player(cm):
    team = cm.find_team("Inter")
    with pytest.raises(TransferError, match="zaten senin takımında"):
        cm.offer_fee(team, team.players[0], 1_000_000)


def test_generous_fee_is_accepted_and_opens_negotiation(cm):
    buyer = _rich_buyer(cm)
    seller = cm.find_team("Trabzonspor")
    target = max(seller.players, key=lambda p: p.overall_rating)
    asking = transfers.asking_price(target, seller, buyer.reputation)

    decision = cm.offer_fee(buyer, target, int(asking * 2))
    assert decision.accepted and decision.asking == asking

    negotiation = cm.open_negotiation(buyer, target, decision.offer)
    assert negotiation.open and negotiation.demand.wage > 0


def test_full_transfer_moves_player_and_money(cm, db):
    buyer = _rich_buyer(cm)
    seller = cm.find_team("Napoli")
    target = max(seller.players, key=lambda p: p.overall_rating)

    buyer_t0, seller_t0 = buyer.transfer_budget, seller.transfer_budget
    buyer_squad0, seller_squad0 = len(buyer.players), len(seller.players)
    fee = transfers.asking_price(target, seller, buyer.reputation) * 2

    negotiation = cm.open_negotiation(buyer, target, fee)
    response = negotiation.respond(negotiation.demand)
    assert response.status is NegotiationStatus.ACCEPTED

    news = cm.complete_transfer(buyer, target, fee, negotiation.demand)
    db.flush()

    assert target.team_id == buyer.id
    assert len(buyer.players) == buyer_squad0 + 1
    assert len(seller.players) == seller_squad0 - 1
    assert buyer.transfer_budget == buyer_t0 - fee
    assert seller.transfer_budget == seller_t0 + fee
    assert target.current_wage == negotiation.demand.wage
    assert target.contract_years == negotiation.demand.years
    assert target.squad_role is negotiation.demand.role
    assert news.fee == fee and news.to_team == buyer.name

    # Yeni oyuncunun maasi artik alicinin maas yukune dahil
    assert target.current_wage <= buyer.wage_budget
    assert buyer.wage_bill >= target.current_wage


def test_transfer_blocked_when_wage_room_is_missing(cm):
    buyer = cm.find_team("Besiktas")
    buyer.transfer_budget = 500_000_000
    cm.db.flush()
    cm.shift_budget(buyer, -cm.wage_summary(buyer).free)       # bos maas alani sifir
    assert buyer.free_wage == 0

    seller = cm.find_team("Manchester City")
    target = max(seller.players, key=lambda p: p.overall_rating)
    fee = transfers.asking_price(target, seller, buyer.reputation)
    negotiation = cm.open_negotiation(buyer, target, fee)

    with pytest.raises(TransferError, match="Maaş havuzunda yer yok"):
        cm.complete_transfer(buyer, target, fee, negotiation.demand)
    assert target.team_id == seller.id                          # hicbir sey degismedi


def test_walking_away_leaves_everything_untouched(cm):
    buyer = _rich_buyer(cm)
    seller = cm.find_team("Milan")
    target = max(seller.players, key=lambda p: p.overall_rating)
    fee = transfers.asking_price(target, seller, buyer.reputation)
    t0, w0 = buyer.transfer_budget, target.current_wage

    negotiation = cm.open_negotiation(buyer, target, fee)
    insult = ContractOffer(int(negotiation.min_wage * 0.5), negotiation.demand.years, negotiation.demand.role)
    assert negotiation.respond(insult).status is NegotiationStatus.WALKED_AWAY

    assert target.team_id == seller.id
    assert buyer.transfer_budget == t0 and target.current_wage == w0


def test_transfer_targets_excludes_own_squad_and_filters_by_name(cm):
    buyer = cm.find_team("Arsenal")
    targets = cm.transfer_targets(buyer, limit=50)
    assert targets and all(p.team_id != buyer.id for p in targets)
    assert targets == sorted(targets, key=lambda p: -p.overall_rating)

    sample = targets[0]
    named = cm.transfer_targets(buyer, query=sample.name.split()[0])
    assert named and all(sample.name.split()[0].lower() in p.name.lower() for p in named)


# ===========================================================================
# 5) HAFTALIK FINANS AKISI VE AI PAZARI
# ===========================================================================

def test_weekly_wages_flow_into_transfer_budget(cm):
    team = cm.find_team("Galatasaray")
    cm.set_user_team(team)
    before_transfer = team.transfer_budget
    surplus = cm.wage_summary(team).free
    assert surplus > 0

    report = cm.play_week()
    assert team.transfer_budget == before_transfer + surplus
    assert report.finance_note and "Maaşlar ödendi" in report.finance_note


def test_overspending_club_loses_transfer_money_each_week(cm):
    team = cm.find_team("Trabzonspor")
    cm.set_user_team(team)
    team.wage_budget = team.wage_bill - 50_000          # yapay butce asimi
    team.transfer_budget = 10_000_000
    cm.db.flush()
    assert cm.wage_summary(team).overspending

    cm.play_week()
    assert team.transfer_budget == 10_000_000 - 50_000


def test_no_team_ends_the_week_with_negative_budget(cm):
    cm.play_week()
    for team in cm.teams():
        assert team.transfer_budget >= 0
        assert team.wage_budget >= 0


def test_ai_transfers_respect_budgets_and_are_reported(cm, db):
    """AI birkaç hafta boyunca pazara çıkar; her transfer bütçe kurallarına uymalı."""
    cm.set_user_team(cm.find_team("Galatasaray"))
    all_news = []
    for _ in range(4):
        if cm.season_finished:
            break
        report = cm.play_week()
        all_news.extend(report.transfers)
        for team in cm.teams():
            assert team.transfer_budget >= 0, f"{team.name} eksi bütçeye düştü"

    if not all_news:
        pytest.skip("bu tohumda AI transfer gerçekleşmedi")

    for news in all_news:
        assert news.fee >= 0 and news.wage > 0
        assert news.from_team != news.to_team
        assert news.to_team != "Galatasaray"        # AI kullanicinin takimini yonetmez
        moved = db.scalar(select(Player).where(Player.name == news.player_name))
        assert moved.team.name == news.to_team
        assert moved.current_wage == news.wage

    # Bir oyuncu bir pencerede yalnizca bir kez el degistirebilir
    names = [n.player_name for n in all_news]
    assert len(names) == len(set(names)), f"aynı oyuncu birden fazla transfer edildi: {names}"


def test_squad_sizes_stay_sane_after_ai_window(cm):
    for _ in range(3):
        if cm.season_finished:
            break
        cm.play_week()
    for team in cm.teams():
        assert len(team.players) >= transfers.SQUAD_FLOOR - 1
        assert any(p.position.value == "GK" for p in team.players)


def test_new_season_ages_contracts_and_revalues_players(cm, db):
    for _ in range(12):
        if cm.season_finished:
            break
        cm.play_week()
    if not cm.season_finished:
        pytest.skip("sezon bitmedi")

    before = {p.id: (p.contract_years, p.age) for p in db.scalars(select(Player))}
    cm.start_new_season()
    for p in db.scalars(select(Player)):
        years0, age0 = before[p.id]
        assert p.contract_years == max(0, years0 - 1)
        assert p.age == min(45, age0 + 1)
        assert p.market_value == finance.market_value(p.overall_rating, p.age, p.position)
        assert p.weeks_since_match == 0


def test_staff_wages_are_part_of_the_weekly_bill(cm, db):
    team = cm.find_team("Liverpool")
    cm.set_user_team(team)
    staff_cost = team.staff_wage_bill
    assert staff_cost > 0
    assert cm.wage_summary(team).staff_wages == staff_cost

    before = team.transfer_budget
    surplus = cm.wage_summary(team).free
    cm.play_week()
    # Personel maasi da havuzdan dustugu icin kasaya yansiyan tutar bunu icerir
    assert team.transfer_budget == before + surplus


def test_coach_quality_changes_form_outcome(cm, db):
    """Aynı maç motoru ayarıyla: iyi antrenör formu daha çok artırır."""
    from career_manager import form_delta

    team = cm.find_team("Inter")
    coach = team.staff_by_role(StaffRole.COACH)[0]
    for other in team.staff_by_role(StaffRole.COACH)[1:]:
        other.attacking = other.defending = 1
    db.flush()

    coach.attacking = 20
    db.flush()
    good = staff_rules.apply_training(form_delta(8.0, "W"), cm._staff_rating(team, StaffRole.COACH, "attacking"))

    coach.attacking = 1
    db.flush()
    bad = staff_rules.apply_training(form_delta(8.0, "W"), cm._staff_rating(team, StaffRole.COACH, "attacking"))

    assert good > bad


def test_injury_uses_club_physio_in_real_match(cm, db):
    """Sakatlık üretmeye zorla; süre sağlıkçı çarpanıyla uyumlu olmalı."""
    manager = CareerManager(db, seed=3, engine_config=EngineConfig(base_injury=0.03))
    manager.set_user_team(manager.find_team("Galatasaray"))
    week = manager.current_week
    report = manager.play_week()
    if not report.injuries:
        pytest.skip("sakatlik uretilemedi")

    for note in report.injuries:
        player = db.get(Player, note.player_id)
        weeks_out = player.injured_until_week - week - 1
        physio = manager.physio_rating(player.team)
        lo = staff_rules.apply_injury_multiplier(min(w for w, _ in __import__("career_manager").INJURY_TABLE), physio)
        hi = staff_rules.apply_injury_multiplier(max(w for w, _ in __import__("career_manager").INJURY_TABLE), physio)
        assert lo <= weeks_out <= hi


def test_staff_table_has_all_roles_and_valid_attributes(cm, db):
    total = db.scalar(select(func.count()).select_from(Staff))
    assert total >= 60
    for member in db.scalars(select(Staff)):
        for attr in staff_rules.ATTR_LABELS:
            assert 1 <= getattr(member, attr) <= 20
        for attr in staff_rules.ROLE_ATTRIBUTES[member.role]:
            assert getattr(member, attr) >= 1
        assert member.wage > 0
        assert member.employed == (member.team_id is not None)


def test_team_relationship_exposes_staff(cm):
    team = cm.find_team("Juventus")
    assert isinstance(team, Team)
    assert all(s.team is team for s in team.staff)
    best = team.best_staff(StaffRole.COACH, "attacking")
    assert best is not None
    assert best.attacking == max(s.attacking for s in team.staff_by_role(StaffRole.COACH))
