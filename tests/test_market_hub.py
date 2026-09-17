"""
Faz 12 / 14. Asama B2: menajerler arasi pazar (market_hub.py) -- PostgreSQL testleri.

Sentetik 'public' dunyasi modul basina bir kez temiz kurulur ve paylasilan dunyaya cevrilir (tests/world_helpers):
sahip Manchester Blue (birincil koltuk, dunya sahibi), uye Merseyside Reds, ucuncu menajer London Gunners, kulupsuz
yonetici (ADMIN). Hesaplar 60 gunluk, koltuklar eski (yeni hesap / koltuk adil oyun puani eklemesin). Her test tek
oturumda calisir ve sonunda GERI ALINIR (kontrolcu commit etmez); farkli menajerler ayni oturumda kendi
CareerManager(manager_user_id=...) nesneleriyle oynar. Modul sonunda dunya yeniden kurulur.
Kendi veritabaninda calistirin: TEST_DB_NAME=fm_db_test_b2.

Yardimcilar (shared_market_world, hub_for, player_named ...) tests/test_loans.py ve tests/test_market_concurrency.py
tarafindan da kullanilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import fair_play  # noqa: E402
import market_hub  # noqa: E402
from career_manager import TRANSFER_BAN_WEEKS, CareerManager, WeekReport  # noqa: E402
from market_hub import (  # noqa: E402
    FairPlayBlocked,
    MarketError,
    MarketExtension,
    MarketHub,
    OfferDraft,
    OfferVoided,
)
from market_rules import OfferKind  # noqa: E402
from models import (  # noqa: E402
    FairPlayLog,
    GameMode,
    NewsItem,
    Notification,
    Player,
    SquadRole,
    Team,
    TransferLog,
    TransferOffer,
    WorldEvent,
    WorldManager,
)
from transfers import ContractOffer, NegotiationStatus  # noqa: E402
from world_rules import WorldRules  # noqa: E402

SCHEMA = "public"
OWNER, MEMBER, THIRD, ADMIN = "b2_sahip", "b2_uye", "b2_ucuncu", "b2_yonetici"
OWNER_TEAM, MEMBER_TEAM, THIRD_TEAM = "Manchester Blue", "Merseyside Reds", "London Gunners"
AI_TEAM = "Karadeniz Storm"
USERS = (OWNER, MEMBER, THIRD, ADMIN)
RULES = WorldRules.shared_defaults()


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


# ===========================================================================
# Ortak yardimcilar (diger B2 test modulleri de kullanir)
# ===========================================================================

def fresh_world(mode: GameMode | None = GameMode.CAREER) -> None:
    """conftest ile ayni temiz sentetik dunya (mod secilirse turnuva da kurulur)."""
    import seed

    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")
    if mode is not None:
        with database.session_scope() as db:
            CareerManager(db).set_game_mode(mode)


def drop_test_users(names=USERS) -> None:
    database.init_accounts()
    with database.engine.begin() as conn:
        conn.execute(text('DELETE FROM "accounts".users WHERE username = ANY(:names)'), {"names": list(names)})


def shared_market_world(rules: WorldRules = RULES):
    """Temiz dunya + paylasilan dunya kaydi; hesaplar ve koltuklar eski, ADMIN rolu atanir."""
    from tests.world_helpers import cleanup_shared, make_shared_public

    cleanup_shared(None, reseed=False)
    drop_test_users()
    fresh_world()
    world = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM), (THIRD, THIRD_TEAM), (ADMIN, None)],
                               owner_team=OWNER_TEAM, rules=rules)
    with database.engine.begin() as conn:
        conn.execute(text('UPDATE "accounts".users SET created_at = now() - interval \'60 days\' '
                          "WHERE id = ANY(:ids)"), {"ids": list(world.user_ids.values())})
        conn.execute(text('UPDATE "accounts".world_memberships SET role = \'ADMIN\' '
                          "WHERE world_id = :w AND user_id = :u"), {"w": world.world_id, "u": world.user_ids[ADMIN]})
    with database.career_context(SCHEMA), database.session_scope() as db:
        db.execute(update(WorldManager).values(joined_career_week=-20))
    return world


def teardown_market_world(world) -> None:
    from tests.world_helpers import cleanup_shared

    cleanup_shared(world, reseed=False)
    drop_test_users()
    fresh_world(mode=None)


def hub_for(db, world, name: str | None) -> MarketHub:
    """name None: sistem / birincil koltuk (manager_user_id None)."""
    uid = world.user_ids[name] if name is not None else None
    return MarketHub(CareerManager(db, seed=11, manager_user_id=uid))


def cm_for(db, world, name: str | None = None) -> CareerManager:
    uid = world.user_ids[name] if name is not None else None
    return CareerManager(db, seed=11, manager_user_id=uid)


def team_named(db, name: str) -> Team:
    return db.scalar(select(Team).where(Team.name == name))


def player_named(db, name: str) -> Player:
    return db.scalar(select(Player).where(Player.name == name))


def seat_row(db, world, name: str) -> WorldManager:
    return db.get(WorldManager, world.seat_ids[name])


def notifications_for(db, world, name: str, kind: str | None = None) -> list[Notification]:
    db.flush()
    stmt = select(Notification).where(Notification.manager_id == world.seat_ids[name])
    if kind is not None:
        stmt = stmt.where(Notification.kind == kind)
    return list(db.scalars(stmt.order_by(Notification.id)))


def agree_contract(hub: MarketHub, offer_id: int) -> ContractOffer:
    """Sozlesme masasini acar ve oyuncunun talebini kabul eder: anlasilan sozlesme."""
    step = hub.open_contract(offer_id)
    assert step.status is NegotiationStatus.OPEN, step.message
    step = hub.submit_contract(offer_id, step.demand)
    assert step.status is NegotiationStatus.ACCEPTED, step.message
    return step.demand


def contract_ready_offer(db, world, *, player: str = "Jack Edwards", fee: int = 12_000_000,
                         buyer: str = OWNER, exchange: str | None = None) -> tuple[int, ContractOffer]:
    """Alici teklif eder, uye (satici) kabul eder, alici oyuncuyla anlasir. (teklif id, sozlesme)."""
    draft = OfferDraft(player_id=player_named(db, player).id, fee=fee,
                       exchange_player_id=player_named(db, exchange).id if exchange else None)
    view = hub_for(db, world, buyer).make_offer(draft)
    accepted = hub_for(db, world, MEMBER).accept(view.id)
    assert accepted.status == "CONTRACT", (accepted.status, accepted.reason)
    return view.id, agree_contract(hub_for(db, world, buyer), view.id)


# ===========================================================================
# Fikstur
# ===========================================================================

@pytest.fixture(scope="module")
def world():
    shared = shared_market_world()
    try:
        yield shared
    finally:
        teardown_market_world(shared)


@pytest.fixture
def db(world):
    with database.career_context(SCHEMA):
        session = database.SessionLocal()
        try:
            yield session
        finally:
            session.rollback()
            session.close()


# ===========================================================================
# 1) Tam akis: teklif -> karsi teklif (takas) -> kabul -> sozlesme -> tamamlama
# ===========================================================================

def test_full_flow_with_counter_exchange_contract_and_completion(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    buyer, seller = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    target, swap = player_named(db, "Jack Edwards"), player_named(db, "Marcus Wood")
    swap_contract = (swap.current_wage, swap.contract_years, swap.squad_role)
    buyer_budget, seller_budget = buyer.transfer_budget, seller.transfer_budget
    logs_before = db.scalar(select(func.count()).select_from(TransferLog))

    view = owner.make_offer(OfferDraft(player_id=target.id, fee=9_000_000, note="Forvet lazım"))
    assert (view.status, view.round, view.direction, view.can_withdraw, view.can_accept) == \
        ("PENDING", 0, "OUT", True, False)
    assert view.expires_in_weeks == RULES.offer_expiry_weeks and view.turn == "SELLER"
    [incoming] = member.inbox()
    assert (incoming.id, incoming.direction, incoming.can_accept, incoming.can_counter, incoming.can_withdraw) == \
        (view.id, "IN", True, True, False)
    assert "Forvet lazım" in incoming.history[0]
    assert [n.kind for n in notifications_for(db, world, MEMBER)] == ["OFFER_IN"]
    counts = member.counts()
    assert (counts.offers_in, counts.offers_action, counts.notifications) == (1, 1, 1)

    with pytest.raises(MarketError, match="Kendi teklifini kabul edemezsin"):
        owner.accept(view.id)
    countered = member.counter(view.id, fee=6_000_000, exchange_player_id=swap.id)
    assert (countered.status, countered.round, countered.exchange_player_name, countered.fee) == \
        ("COUNTERED", 1, "Marcus Wood", 6_000_000)
    assert owner.counts().offers_action == 1 and member.counts().offers_action == 0

    accepted = owner.accept(view.id)
    assert accepted.status == "CONTRACT" and accepted.fairness.decision is fair_play.Decision.ALLOW
    assert accepted.can_contract and not accepted.can_counter
    offer = db.get(TransferOffer, view.id)
    assert offer.fairness_score == accepted.fairness.score and offer.expires_career_week == \
        CareerManager(db).career_week + RULES.offer_expiry_weeks

    step = owner.open_contract(view.id)
    assert step.status is NegotiationStatus.OPEN and step.demand.wage > 0 and step.rounds_left == 4
    low = ContractOffer(int(step.demand.wage * 0.9), step.demand.years, step.demand.role)
    step2 = owner.submit_contract(view.id, low)
    assert step2.status is NegotiationStatus.OPEN and step2.complaints
    with pytest.raises(MarketError, match="Önce oyuncuyla sözleşmede anlaşmalısın"):
        owner.complete(view.id, step2.demand)
    step3 = owner.submit_contract(view.id, step2.demand)
    assert step3.status is NegotiationStatus.ACCEPTED and step3.needs_room is None
    with pytest.raises(MarketError, match="anlaşılan şartlarla aynı değil"):
        owner.complete(view.id, ContractOffer(step3.demand.wage + 100, step3.demand.years, step3.demand.role))
    with pytest.raises(MarketError, match="yalnızca alıcı kulüp yapabilir"):
        member.complete(view.id, step3.demand)            # satici tamamlayamaz

    news = owner.complete(view.id, step3.demand)
    db.flush()
    assert (news.player_name, news.from_team_id, news.to_team_id, news.fee) == (target.name, seller.id, buyer.id,
                                                                                6_000_000)
    assert (buyer.transfer_budget, seller.transfer_budget) == (buyer_budget - 6_000_000, seller_budget + 6_000_000)
    assert target.team_id == buyer.id and (target.current_wage, target.contract_years, target.squad_role) == \
        (step3.demand.wage, step3.demand.years, step3.demand.role)
    assert swap.team_id == seller.id and (swap.current_wage, swap.contract_years, swap.squad_role) == swap_contract
    week = CareerManager(db).career_week
    assert target.transfer_locked_until == week + TRANSFER_BAN_WEEKS == swap.transfer_locked_until
    offer = db.get(TransferOffer, view.id)
    assert offer.status == "COMPLETED" and len(offer.transfer_log_ids) == 2
    kinds = {db.get(TransferLog, i).kind for i in offer.transfer_log_ids}
    assert kinds == {"TRANSFER", "EXCHANGE"}
    assert db.scalar(select(func.count()).select_from(TransferLog)) == logs_before + 2
    assert db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.text.like(f"%{target.name}%")))
    assert [n.kind for n in notifications_for(db, world, OWNER)][-1] == "OFFER_UPDATE"
    assert [n.kind for n in notifications_for(db, world, MEMBER)][-1] == "OFFER_UPDATE"
    final = owner.offer(view.id)
    assert final.status == "COMPLETED" and not (final.can_contract or final.can_withdraw)
    assert any("tamamlandı" in line for line in final.history)
    with pytest.raises(MarketError, match="Transfer tamamlandı"):
        owner.complete(view.id, step3.demand)             # para ikinci kez hareket etmez
    assert buyer.transfer_budget == buyer_budget - 6_000_000


def test_contract_table_is_rebuilt_deterministically_from_the_log(db, world):
    owner = hub_for(db, world, OWNER)
    target = player_named(db, "Connor Davies")
    view = owner.make_offer(OfferDraft(player_id=target.id, fee=18_000_000))
    assert hub_for(db, world, MEMBER).accept(view.id).status == "CONTRACT"
    first = owner.open_contract(view.id)
    again = hub_for(db, world, OWNER).open_contract(view.id)           # yeni kontrolcu: kayittan
    assert (again.demand, again.message, again.rounds_left) == (first.demand, first.message, first.rounds_left)
    assert len(market_hub._entries(db.get(TransferOffer, view.id), "open")) == 1

    bid = ContractOffer(int(first.demand.wage * 0.88), first.demand.years, first.demand.role)
    reply = owner.submit_contract(view.id, bid)
    # dunya degisse de (oyuncu gucu, kulup itibari, maas) masa acilis anindaki kosullarla yeniden kurulur
    target.overall_rating, target.current_wage = 95, target.current_wage * 3
    team_named(db, OWNER_TEAM).reputation = 40
    db.flush()
    db.expire_all()
    replay = hub_for(db, world, OWNER).open_contract(view.id)
    assert (replay.status, replay.demand, replay.message, replay.complaints, replay.rounds_left) == \
        (reply.status, reply.demand, reply.message, reply.complaints, reply.rounds_left)


def test_player_refusal_at_contract_table_rejects_offer(db, world):
    owner = hub_for(db, world, OWNER)
    target = player_named(db, "Ethan Green")
    view = owner.make_offer(OfferDraft(player_id=target.id, fee=5_000_000))
    assert hub_for(db, world, MEMBER).accept(view.id).status == "CONTRACT"
    team_named(db, OWNER_TEAM).reputation = 1                        # prestij kapisi: oyuncu masaya oturmaz
    db.flush()
    step = owner.open_contract(view.id)
    assert step.status is NegotiationStatus.WALKED_AWAY and "sözleşme masasına oturmadı" in step.message
    offer = db.get(TransferOffer, view.id)
    assert offer.status == "REJECTED" and "oturmadı" in offer.reason
    with pytest.raises(MarketError, match="Teklif kapandı"):
        owner.open_contract(view.id)


def test_insulting_contract_makes_player_walk_away(db, world):
    owner = hub_for(db, world, OWNER)
    view = owner.make_offer(OfferDraft(player_id=player_named(db, "James White").id, fee=15_000_000))
    hub_for(db, world, MEMBER).accept(view.id)
    step = owner.open_contract(view.id)
    walked = owner.submit_contract(view.id, ContractOffer(1_000, step.demand.years, step.demand.role))
    assert walked.status is NegotiationStatus.WALKED_AWAY and "hakaret" in walked.message
    assert db.get(TransferOffer, view.id).status == "REJECTED"


# ===========================================================================
# 2) Geri cekme, ret, sure, tekrar teklif
# ===========================================================================

def test_withdraw_reject_and_turn_rules(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    target = player_named(db, "Harry Green")
    view = owner.make_offer(OfferDraft(player_id=target.id, fee=16_000_000))
    with pytest.raises(MarketError, match="zaten açık bir teklifin var"):
        owner.make_offer(OfferDraft(player_id=target.id, fee=17_000_000))
    with pytest.raises(MarketError, match="Satıcı teklifi geri çekemez"):
        member.withdraw(view.id)
    rejected = member.reject(view.id, "Satılık değil")
    assert (rejected.status, rejected.reason) == ("REJECTED", "Satılık değil")
    assert notifications_for(db, world, OWNER)[-1].text.endswith("Satılık değil")

    again = owner.make_offer(OfferDraft(player_id=target.id, fee=17_000_000))    # kapanan teklif engel degil
    withdrawn = owner.withdraw(again.id)
    assert withdrawn.status == "WITHDRAWN" and not withdrawn.can_accept
    with pytest.raises(MarketError, match="Teklif kapandı"):
        member.accept(again.id)
    assert hub_for(db, world, THIRD).inbox() == [] and hub_for(db, world, THIRD).outbox() == []
    with pytest.raises(MarketError, match="Teklif bulunamadı"):
        hub_for(db, world, THIRD).offer(again.id)
    assert hub_for(db, world, ADMIN).offer(again.id).direction == "ADMIN"


def test_offer_expires_when_weeks_advance(db, world):
    owner = hub_for(db, world, OWNER)
    view = owner.make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=16_000_000))
    system = cm_for(db, world)
    system.run_ai_transfer_window = lambda: []
    system.play_week()
    assert db.get(TransferOffer, view.id).status == "PENDING"
    assert hub_for(db, world, OWNER).offer(view.id).expires_in_weeks == 1
    system.play_week()
    offer = db.get(TransferOffer, view.id)
    assert offer.status == "EXPIRED" and offer.reason == market_hub.EXPIRED_TEXT
    assert any("süresi doldu" in n.text for n in notifications_for(db, world, MEMBER))


# ===========================================================================
# 3) Ayni oyuncuya iki alici, oyuncu tasinmasi, kulup birakma
# ===========================================================================

def test_two_buyers_one_contract_slot_and_completion_voids_the_other(db, world):
    owner, member, third = hub_for(db, world, OWNER), hub_for(db, world, MEMBER), hub_for(db, world, THIRD)
    target = player_named(db, "Jack Edwards")
    first = owner.make_offer(OfferDraft(player_id=target.id, fee=12_000_000))
    second = third.make_offer(OfferDraft(player_id=target.id, fee=13_000_000))
    assert member.player_status(target.id).open_offers == 2
    assert member.accept(first.id).status == "CONTRACT"
    with pytest.raises(MarketError, match="başka bir kulüple sözleşme görüşmesi sürüyor"):
        member.accept(second.id)                                    # uq_transfer_offer_contract
    assert db.get(TransferOffer, second.id).status == "PENDING"

    contract = agree_contract(owner, first.id)
    owner.complete(first.id, contract)
    voided = db.get(TransferOffer, second.id)
    assert voided.status == "VOIDED" and "kulüp değiştirdi" in voided.reason
    assert any("geçersiz" in n.text for n in notifications_for(db, world, THIRD))


def test_exchange_player_moving_voids_offers_that_use_him(db, world):
    owner, third = hub_for(db, world, OWNER), hub_for(db, world, THIRD)
    swap = player_named(db, "Marcus Wood")
    with_swap = third.make_offer(OfferDraft(player_id=swap.id, fee=6_000_000))       # swap uzerine teklif
    offer_id, contract = contract_ready_offer(db, world, player="Jack Edwards", fee=6_000_000,
                                              exchange="Marcus Wood")
    owner.complete(offer_id, contract)
    assert db.get(TransferOffer, with_swap.id).status == "VOIDED"


def test_seller_releasing_club_voids_offers(db, world):
    from world_manager import WorldController
    from worlds import WorldContext

    owner = hub_for(db, world, OWNER)
    view = owner.make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=16_000_000))
    member_player = player_named(db, "Ethan Harris")
    hub_for(db, world, MEMBER).set_listing(member_player.id, transfer_listed=True)
    ctx = WorldContext(world.world_id, SCHEMA, "Test Dünyası", "SHARED", "MEMBER", world.user_ids[MEMBER], None)
    WorldController(db, ctx).release_club()                          # _club_released_hooks -> on_club_released
    offer = db.get(TransferOffer, view.id)
    assert offer.status == "VOIDED" and offer.reason == market_hub.CLUB_RELEASED_TEXT
    assert not member_player.transfer_listed
    with pytest.raises(MarketError, match="Önce yöneteceğin kulübü seç"):
        hub_for(db, world, MEMBER).make_offer(OfferDraft(player_id=player_named(db, "Marcus Wood").id, fee=1))


def test_weekly_sweep_voids_offers_after_leave_without_hook(db, world):
    """worlds.leave_world on_club_released cagirmaz: haftalik tarama ve islem anindaki dogrulama yakalar."""
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    swept = owner.make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=16_000_000))
    acted = owner.make_offer(OfferDraft(player_id=player_named(db, "James White").id, fee=15_000_000))
    member.counter(acted.id, fee=15_500_000)                        # sira aliciya gecer
    row = seat_row(db, world, MEMBER)
    row.team_id, row.status = None, "LEFT"                          # worlds._release_seat ile ayni etki
    db.flush()
    with pytest.raises(OfferVoided, match="Satıcı kulübün menajeri ayrıldı"):
        owner.accept(acted.id)
    assert db.get(TransferOffer, acted.id).status == "VOIDED"
    hub_for(db, world, None).run_week(1, None)
    assert db.get(TransferOffer, swept.id).status == "VOIDED"
    assert member.inbox() == []                                     # kulupsuz koltuk


# ===========================================================================
# 4) Tamamlamada yeniden dogrulama
# ===========================================================================

def test_completion_revalidates_budget_squad_floor_and_wage_room(db, world):
    owner = hub_for(db, world, OWNER)
    buyer, seller = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    target = player_named(db, "Jack Edwards")
    offer_id, contract = contract_ready_offer(db, world, fee=12_000_000)

    budget = buyer.transfer_budget
    buyer.transfer_budget = 11_999_999
    db.flush()
    with pytest.raises(MarketError, match="Transfer bütçen yetersiz"):
        owner.complete(offer_id, contract)
    assert target.team_id == seller.id and db.get(TransferOffer, offer_id).status == "CONTRACT"
    buyer.transfer_budget = budget

    parked = [p for p in seller.players if p.id != target.id and p.position.value != "GK"][:2]
    for p in parked:
        p.in_academy = True
    db.flush()
    db.expire(seller, ["players", "academy_players"])
    with pytest.raises(MarketError, match="13 oyuncunun altına düşer"):
        owner.complete(offer_id, contract)
    for p in parked:
        p.in_academy = False
    db.flush()

    buyer.wage_budget = buyer.wage_bill + contract.wage - 10_000                     # 10K eksik alan
    db.flush()
    wage_budget = buyer.wage_budget
    with pytest.raises(MarketError, match="Maaş havuzunda yer yok"):
        owner.complete(offer_id, contract)
    assert (buyer.transfer_budget, target.team_id) == (budget, seller.id)
    news = owner.complete(offer_id, contract, shift_wage_room=True)
    assert news.fee == 12_000_000 and target.team_id == buyer.id
    assert buyer.wage_budget == wage_budget + 10_000 and buyer.free_wage == 0
    assert buyer.transfer_budget == budget - 12_000_000 - 520_000                    # 10K x 52 kaydirildi
    done = market_hub._done_entry(db.get(TransferOffer, offer_id).contract_log)
    assert done["shift"] == 10_000


def test_failure_inside_completion_savepoint_leaves_no_partial_writes(db, world, monkeypatch):
    import career_manager

    owner = hub_for(db, world, OWNER)
    buyer, seller = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    target, swap = player_named(db, "Jack Edwards"), player_named(db, "Marcus Wood")
    offer_id, contract = contract_ready_offer(db, world, fee=6_000_000, exchange="Marcus Wood")
    budgets = (buyer.transfer_budget, seller.transfer_budget)
    logs = db.scalar(select(func.count()).select_from(TransferLog))

    def explode(self, *args, **kwargs):
        assert db.get(Player, swap.id).team_id == seller.id           # takas oyuncusu savepoint icinde tasindi
        raise career_manager.TransferError("Beklenmeyen transfer hatası")

    monkeypatch.setattr(career_manager.CareerManager, "complete_transfer", explode)
    with pytest.raises(MarketError, match="Beklenmeyen transfer hatası"):
        owner.complete(offer_id, contract)
    db.expire_all()
    assert (player_named(db, "Marcus Wood").team_id, player_named(db, "Jack Edwards").team_id) ==         (buyer.id, seller.id)
    assert (team_named(db, OWNER_TEAM).transfer_budget, team_named(db, MEMBER_TEAM).transfer_budget) == budgets
    assert db.get(TransferOffer, offer_id).status == "CONTRACT"
    assert db.scalar(select(func.count()).select_from(TransferLog)) == logs
    assert player_named(db, "Marcus Wood").transfer_locked_until is None
    assert target.id and swap.id


def test_squad_floor_and_exchange_interest_are_checked_when_offering(db, world):
    owner = hub_for(db, world, OWNER)
    seller = team_named(db, MEMBER_TEAM)
    keepers = [p for p in seller.players if p.position.value == "GK"]
    with pytest.raises(MarketError, match="GK mevkisinde en az 2"):
        owner.make_offer(OfferDraft(player_id=keepers[0].id, fee=15_000_000))
    swap = player_named(db, "Marcus Wood")
    seller.reputation = 5
    db.flush()
    with pytest.raises(MarketError, match="Marcus Wood Merseyside Reds kulübüne gitmek istemiyor"):
        owner.make_offer(OfferDraft(player_id=player_named(db, "Jack Edwards").id, fee=5_000_000,
                                    exchange_player_id=swap.id))


# ===========================================================================
# 5) Adil oyun: engel, inceleme, yonetici onayi / reddi, iade
# ===========================================================================

def test_block_writes_status_reason_and_penalises_both_seats(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    star = player_named(db, "Marcus Jones")                            # 33.5M
    preview = owner.preview_fairness(OfferDraft(player_id=star.id, fee=0))
    assert preview.decision is fair_play.Decision.BLOCK
    assert db.scalar(select(func.count()).select_from(TransferOffer)) == 0      # onizleme yazmaz
    view = owner.make_offer(OfferDraft(player_id=star.id, fee=0))
    blocked = member.accept(view.id)
    assert blocked.status == "BLOCKED" and "değerinin çok altında" in blocked.reason
    assert blocked.fairness.decision is fair_play.Decision.BLOCK and "VALUE_UNDERPRICED" in blocked.fairness.flags
    with pytest.raises(FairPlayBlocked) as caught:
        MarketHub.raise_if_blocked(blocked)
    assert caught.value.verdict.decision is fair_play.Decision.BLOCK
    for name in (OWNER, MEMBER):
        assert seat_row(db, world, name).fair_play == 90.0
        [entry] = db.scalars(select(FairPlayLog).where(FairPlayLog.manager_id == world.seat_ids[name]))
        assert (entry.delta, entry.offer_id) == (-10.0, view.id)
    assert star.team_id == team_named(db, MEMBER_TEAM).id


def test_review_then_admin_approve_and_deny(db, world):
    owner, member, admin = hub_for(db, world, OWNER), hub_for(db, world, MEMBER), hub_for(db, world, ADMIN)
    for name in (OWNER, MEMBER):
        seat_row(db, world, name).fair_play = 95.0
    db.flush()
    star = player_named(db, "Connor Davies")                           # 18.5M; 7M -> fark 0.62 -> 27 puan
    view = owner.make_offer(OfferDraft(player_id=star.id, fee=6_000_000))
    reviewed = member.accept(view.id)
    assert reviewed.status == "REVIEW" and reviewed.fairness.decision is fair_play.Decision.REVIEW
    assert [n.kind for n in notifications_for(db, world, ADMIN)] == ["REVIEW"]
    with pytest.raises(MarketError, match="Teklif yönetici incelemesinde"):
        owner.withdraw(view.id)
    with pytest.raises(MarketError, match="yalnızca dünya yöneticisi"):
        member.review_queue()
    with pytest.raises(MarketError, match="Kendi kulübünün anlaşmasını"):
        owner.approve_review(view.id)                                  # sahip yonetici ama taraf
    queue = admin.review_queue()
    assert [v.id for v in queue] == [view.id] and queue[0].can_review
    assert admin.counts().reviews == 1

    approved = admin.approve_review(view.id)
    assert approved.status == "CONTRACT"
    assert (seat_row(db, world, OWNER).fair_play, seat_row(db, world, MEMBER).fair_play) == (97.0, 97.0)
    event = db.scalar(select(WorldEvent).where(WorldEvent.kind == "REVIEW"))
    assert event.payload["decision"] == "APPROVED" and event.actor_manager_id == world.seat_ids[ADMIN]
    contract = agree_contract(owner, view.id)
    assert owner.complete(view.id, contract).fee == 6_000_000           # onaydan sonra yeniden degerlendirme yok

    other = owner.make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=5_000_000))
    assert member.accept(other.id).status == "REVIEW"
    denied = admin.deny_review(other.id, "Değerin çok altında")
    assert (denied.status, denied.reason) == ("BLOCKED", "Değerin çok altında")
    assert (seat_row(db, world, OWNER).fair_play, seat_row(db, world, MEMBER).fair_play) == (82.0, 82.0)
    assert db.scalar(select(func.count()).select_from(WorldEvent).where(WorldEvent.kind == "REVIEW")) == 2


def test_admin_reversal_restores_players_contracts_and_money(db, world):
    owner, admin = hub_for(db, world, OWNER), hub_for(db, world, ADMIN)
    buyer, seller = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    target, swap = player_named(db, "Jack Edwards"), player_named(db, "Marcus Wood")
    before = {p.id: (p.team_id, p.current_wage, p.contract_years, p.squad_role, p.transfer_locked_until)
              for p in (target, swap)}
    budgets = (buyer.transfer_budget, seller.transfer_budget)
    offer_id, contract = contract_ready_offer(db, world, fee=6_000_000, exchange="Marcus Wood")
    owner.complete(offer_id, contract)

    with pytest.raises(MarketError, match="yalnızca dünya yöneticisi"):
        hub_for(db, world, MEMBER).reverse_transfer(offer_id, "deneme")
    [reversible] = admin.reversible_offers(weeks=8)
    assert reversible.id == offer_id
    [log_row] = admin.reversible_transfers(weeks=8)
    assert log_row.offer_id == offer_id and log_row.player_id == target.id
    news = admin.reverse_transfer(offer_id, "Şüpheli takas")
    db.flush()
    assert [n.kind for n in news] == ["REVERSAL", "REVERSAL"]
    for p in (target, swap):
        assert (p.team_id, p.current_wage, p.contract_years, p.squad_role, p.transfer_locked_until) == before[p.id]
    assert (buyer.transfer_budget, seller.transfer_budget) == budgets
    offer = db.get(TransferOffer, offer_id)
    assert (offer.status, offer.reason) == ("REVERSED", "Şüpheli takas")
    assert (seat_row(db, world, OWNER).fair_play, seat_row(db, world, MEMBER).fair_play) == (75.0, 75.0)
    event = db.scalar(select(WorldEvent).where(WorldEvent.kind == "REVERSAL"))
    assert event.payload["refund"] == 6_000_000
    assert db.scalar(select(func.count()).select_from(TransferLog).where(TransferLog.kind == "REVERSAL")) == 2
    assert admin.reversible_offers() == []
    with pytest.raises(MarketError, match="yalnızca dünya yöneticisi geri alabilir|Teklif kapandı"):
        admin.reverse_transfer(offer_id, "tekrar")


def test_reversal_voids_open_offers_on_the_returned_player(db, world):
    owner, admin, third = hub_for(db, world, OWNER), hub_for(db, world, ADMIN), hub_for(db, world, THIRD)
    offer_id, contract = contract_ready_offer(db, world, fee=12_000_000)
    owner.complete(offer_id, contract)
    target = player_named(db, "Jack Edwards")
    target.transfer_locked_until = None                                # yeni sahibine teklif yapilabilsin
    db.flush()
    follow_up = third.make_offer(OfferDraft(player_id=target.id, fee=14_000_000))
    assert follow_up.seller_team == OWNER_TEAM
    admin.reverse_transfer(offer_id, "geri")
    voided = db.get(TransferOffer, follow_up.id)
    assert voided.status == "VOIDED" and "kulüp değiştirdi" in voided.reason


def test_player_leaving_on_ai_loan_voids_offers_for_him(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    target = player_named(db, "Harry Green")
    view = owner.make_offer(OfferDraft(player_id=target.id, fee=16_000_000))
    loan = member.request_ai_loan(OfferDraft(player_id=target.id, kind=OfferKind.LOAN, loan_wage_share=50,
                                             target_team_id=team_named(db, AI_TEAM).id))
    assert loan.direction == "OUT" and target.team_id == team_named(db, AI_TEAM).id
    offer = db.get(TransferOffer, view.id)
    assert offer.status == "VOIDED" and "kulüp değiştirdi" in offer.reason
    assert owner.player_status(target.id).on_loan


def test_reversal_refund_never_creates_money(db, world):
    owner, admin = hub_for(db, world, OWNER), hub_for(db, world, ADMIN)
    buyer, seller = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    offer_id, contract = contract_ready_offer(db, world, fee=12_000_000)
    owner.complete(offer_id, contract)
    seller.transfer_budget = 5_000_000                                  # satici parayi harcadi
    db.flush()
    total = buyer.transfer_budget + seller.transfer_budget
    admin.reverse_transfer(offer_id, "iade")
    assert seller.transfer_budget == 0 and buyer.transfer_budget + seller.transfer_budget == total
    assert "iade edilemedi" in notifications_for(db, world, OWNER)[-1].text


def test_admin_cannot_reverse_when_player_moved_on(db, world):
    owner, admin = hub_for(db, world, OWNER), hub_for(db, world, ADMIN)
    offer_id, contract = contract_ready_offer(db, world, fee=12_000_000)
    owner.complete(offer_id, contract)
    player_named(db, "Jack Edwards").team_id = team_named(db, AI_TEAM).id
    db.flush()
    assert admin.reversible_offers() == []
    with pytest.raises(MarketError, match="artık alıcı kulüpte değil"):
        admin.reverse_transfer(offer_id, "geç")


# ===========================================================================
# 6) Gorunumler, listeler, kural ve yetki hatalari
# ===========================================================================

def test_player_status_candidates_listing_and_counts(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    target = player_named(db, "Harry Green")
    status = owner.player_status(target.id)
    assert (status.owner_is_human, status.seller_seat_name, status.on_loan, status.block_reason,
            status.my_open_offer_id, status.open_offers) == (True, MEMBER, False, None, None, 0)
    ai_status = owner.player_status(player_named(db, "Yusuf Polat").id)
    assert not ai_status.owner_is_human and ai_status.seller_seat_name is None
    view = owner.make_offer(OfferDraft(player_id=target.id, fee=16_000_000))
    assert owner.player_status(target.id).my_open_offer_id == view.id

    mine = owner.exchange_candidates()
    assert mine and all(p.team_id == team_named(db, OWNER_TEAM).id for p in mine)
    assert [p.overall_rating for p in mine] == sorted((p.overall_rating for p in mine), reverse=True)
    theirs = member.exchange_candidates(view.id)                        # satici karsi teklifte alicidan ister
    assert {p.team_id for p in theirs} == {team_named(db, OWNER_TEAM).id}

    member.set_listing(target.id, transfer_listed=True, loan_listed=True)
    assert [p.id for p in owner.listed_players("TRANSFER")] == [target.id]
    assert [p.id for p in member.listed_players("loan", mine=True)] == [target.id]
    assert owner.listed_players("LOAN", mine=True) == []
    with pytest.raises(MarketError, match="senin oyuncun değil"):
        owner.set_listing(target.id, transfer_listed=False)
    member.set_listing(target.id, transfer_listed=False)
    assert owner.listed_players("TRANSFER") == [] and target.loan_listed
    with pytest.raises(MarketError, match="Liste türü"):
        owner.listed_players("SWAP")
    member_counts = member.counts()
    assert (member_counts.offers_in, member_counts.offers_action, member_counts.reviews) == (1, 1, 0)


def test_rules_ai_targets_spectators_and_input_errors(db, world):
    owner = hub_for(db, world, OWNER)
    ai_player = player_named(db, "Yusuf Polat")
    with pytest.raises(MarketError, match="yapay zekâ kulübü; teklifini transfer pazarındaki normal akışla yap"):
        owner.make_offer(OfferDraft(player_id=ai_player.id, fee=7_000_000))
    with pytest.raises(MarketError, match="zaten senin takımında"):
        owner.make_offer(OfferDraft(player_id=player_named(db, "Marcus Wood").id, fee=1))
    with pytest.raises(MarketError, match="negatif"):
        owner.make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=-5))
    with pytest.raises(MarketError, match="Oyuncu bulunamadı"):
        owner.make_offer(OfferDraft(player_id=999_999, fee=1))
    with pytest.raises(MarketError, match="menajer koltuğun yok"):
        MarketHub(CareerManager(db, manager_user_id=987_654)).make_offer(OfferDraft(player_id=ai_player.id))
    assert MarketHub(CareerManager(db, manager_user_id=987_654)).inbox() == []

    state = CareerManager(db).state
    state.world_rules = WorldRules.shared_defaults().with_changes({"human_market": False}).to_dict()
    db.flush()
    with pytest.raises(MarketError, match="transfer pazarı kapalı"):
        hub_for(db, world, OWNER).make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=1))
    state.world_rules = {}
    db.flush()
    with pytest.raises(MarketError, match="yalnızca paylaşılan dünyada"):
        hub_for(db, world, OWNER).make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=1))


def test_human_buyer_cannot_use_ai_flow_for_human_seller(db, world):
    cm = cm_for(db, world, OWNER)
    buyer, target = team_named(db, OWNER_TEAM), player_named(db, "Harry Green")
    with pytest.raises(market_hub.TransferError, match="Teklifler panelinden"):
        cm.offer_fee(buyer, target, 20_000_000)


def test_extension_is_loaded_for_market_rules_and_hooks_are_safe(db, world):
    cm = cm_for(db, world)
    loaded = cm._extensions()
    assert [type(e) for e in loaded] == [MarketExtension]
    ext = loaded[0]
    assert ext.new_season_blocker() is None and ext.transfer_block_reason(player_named(db, "Harry Green")) is None
    ext.on_player_moved(None, None, 1)                                # zararsiz
    ext.on_club_released(None)
    report = WeekReport(season=1, week=1)
    ext.on_week(1, report)                                           # bos dunyada hata yok

    for name in (OWNER, MEMBER):
        seat_row(db, world, name).fair_play = 50.0
    db.flush()
    ext.on_week(1, report)
    assert seat_row(db, world, OWNER).fair_play == 51.0
    recovery = db.scalar(select(FairPlayLog).where(FairPlayLog.manager_id == world.seat_ids[MEMBER]))
    assert (recovery.delta, recovery.reason) == (1.0, fair_play.FAIR_PLAY_REASONS["RECOVERY"])


def test_offer_history_and_view_fields(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    target, swap = player_named(db, "Harry Green"), player_named(db, "Marcus Wood")
    view = owner.make_offer(OfferDraft(player_id=target.id, fee=10_000_000, note="  merhaba   dünya "))
    member.counter(view.id, fee=11_000_000, exchange_player_id=swap.id)
    owner.counter(view.id, fee=10_500_000)                             # takas aynen kalir
    final = member.counter(view.id, fee=10_800_000, exchange_player_id=None)
    assert (final.round, final.exchange_player_id, final.fee, final.turn) == (3, None, 10_800_000, "BUYER")
    assert len(final.history) == 4 and "Not: merhaba dünya" in final.history[0]
    assert "takas Marcus Wood" in final.history[1] and "takas Marcus Wood" in final.history[2]
    assert (final.player_position, final.player_overall, final.kind_label) == ("FWD", 85, "Transfer")
    owner.counter(view.id, fee=10_700_000)
    with pytest.raises(MarketError, match="Pazarlık turu sınırı doldu"):
        member.counter(view.id, fee=10_750_000)
    assert member.accept(view.id).status == "CONTRACT"
    assert SquadRole(agree_contract(owner, view.id).role)
