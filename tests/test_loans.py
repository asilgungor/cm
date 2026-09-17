"""
Faz 12 / 14. Asama B2: kiralik sistemi (market_hub.py kiralik akisi + MarketExtension kancalari) -- PostgreSQL.

Dunya tests/test_market_hub.py yardimcilariyla kurulur (sahip Manchester Blue, uye Merseyside Reds, ucuncu London
Gunners, kulupsuz yonetici). Her test tek oturumda calisir ve geri alinir; hafta ilerlemesi (play_week /
start_new_season) de ayni oturumda. Sentetik sezon: lig 6 hafta, kupa 7 hafta -> sezon sonu kariyer haftasi 8.
Kendi veritabaninda calistirin: TEST_DB_NAME=fm_db_test_b2.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import market_hub  # noqa: E402
from career_manager import AcademyError, PlayerNote, WeekReport  # noqa: E402
from loan_rules import wage_split  # noqa: E402
from market_hub import LoanError, MarketError, MarketExtension, OfferDraft  # noqa: E402
from market_rules import OfferKind  # noqa: E402
from models import (  # noqa: E402
    Loan,
    Notification,
    PlayerMatchStat,
    Position,
    TransferLog,
    TransferOffer,
)
from tests.test_market_hub import (  # noqa: E402
    ADMIN,
    AI_TEAM,
    MEMBER,
    MEMBER_TEAM,
    OWNER,
    OWNER_TEAM,
    SCHEMA,
    THIRD,
    cm_for,
    hub_for,
    notifications_for,
    player_named,
    seat_row,
    shared_market_world,
    team_named,
    teardown_market_world,
)
from transfers import TransferError  # noqa: E402
from world_rules import WorldRules  # noqa: E402

LOANEE = "Callum Wilson"          # Merseyside Reds, FWD 81, 63K/hafta, 10.1M


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


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


def _loan_to_owner(db, world, *, weeks: int | None = 8, share: int = 40, fee: int = 1_000_000,
                   player: str = LOANEE) -> tuple[int, Loan]:
    """Sahip (Manchester Blue) uyenin oyuncusunu kiralar: teklif -> kabul -> tamamlama."""
    owner = hub_for(db, world, OWNER)
    view = owner.make_offer(OfferDraft(player_id=player_named(db, player).id, kind=OfferKind.LOAN, fee=fee,
                                       loan_weeks=weeks, loan_wage_share=share))
    accepted = hub_for(db, world, MEMBER).accept(view.id)
    assert accepted.status == "CONTRACT", (accepted.status, accepted.reason)
    assert not accepted.contract_opened and accepted.can_accept is False
    news = owner.complete(view.id)
    assert news.kind == "LOAN"
    loan = db.scalar(select(Loan).where(Loan.offer_id == view.id))
    return view.id, loan


def _quiet(cm):
    cm.run_ai_transfer_window = lambda: []
    return cm


def _grow_ai_squad(db, team, extra: int = 3) -> None:
    """AI kulubunun akademisinden A takima oyuncu: kiraliga vermek icin 16+ kadro."""
    for p in team.academy_players[:extra]:
        p.in_academy = False
    db.flush()
    db.expire(team, ["players", "academy_players"])


# ===========================================================================
# 1) Insan <-> insan kiralik: para, kayitlar, maas paylasimi
# ===========================================================================

def test_loan_offer_moves_player_money_and_splits_wages(db, world):
    borrower, parent = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    player = player_named(db, LOANEE)
    wage = player.current_wage
    bills = (borrower.player_wage_bill, parent.player_wage_bill)
    budgets = (borrower.transfer_budget, parent.transfer_budget)
    cw = cm_for(db, world).career_week

    offer_id, loan = _loan_to_owner(db, world, weeks=8, share=40, fee=1_000_000)
    pays, rest = wage_split(wage, 40)
    assert (loan.status, loan.start_career_week, loan.end_career_week, loan.wage_share) == ("ACTIVE", cw, 8, 40)
    assert (loan.parent_team_id, loan.borrower_team_id) == (parent.id, borrower.id)
    assert (player.team_id, player.loan_from_team_id, player.loan_id, player.loan_wage_share) == \
        (borrower.id, parent.id, loan.id, 40)
    assert player.current_wage == wage and player.transfer_locked_until is None     # sozlesme ana kulupte kalir
    assert (borrower.transfer_budget, parent.transfer_budget) == (budgets[0] - 1_000_000, budgets[1] + 1_000_000)
    assert borrower.player_wage_bill == bills[0] + pays
    assert parent.player_wage_bill == bills[1] - wage + rest
    offer = db.get(TransferOffer, offer_id)
    assert offer.status == "COMPLETED" and db.get(TransferLog, offer.transfer_log_ids[0]).kind == "LOAN"
    assert player.id in {p.id for p in borrower.players} and player.id not in {p.id for p in parent.players}
    assert [p.id for p in parent.loaned_out_players] == [player.id]

    [incoming] = hub_for(db, world, OWNER).loans("IN")
    assert (incoming.id, incoming.direction, incoming.wage_share, incoming.weeks_left, incoming.can_recall) == \
        (loan.id, "IN", 40, 8 - cw, False)
    [outgoing] = hub_for(db, world, MEMBER).loans("OUT")
    assert outgoing.direction == "OUT" and not outgoing.can_recall and outgoing.recall_reason
    assert hub_for(db, world, OWNER).loans("OUT") == [] and hub_for(db, world, MEMBER).loans("IN") == []
    with pytest.raises(LoanError, match="Kiralık yönü"):
        hub_for(db, world, MEMBER).loans("SIDEWAYS")

    cm = cm_for(db, world)
    before = {t.id: (t.transfer_budget, cm.wage_summary(t).free) for t in (borrower, parent)}
    cm._pay_weekly_wages(WeekReport(season=1, week=1), 1)
    for team in (borrower, parent):
        budget, free = before[team.id]
        assert team.transfer_budget == max(0, budget + free)


def test_loan_completion_needs_wage_room_or_shift(db, world):
    owner, borrower = hub_for(db, world, OWNER), team_named(db, OWNER_TEAM)
    player = player_named(db, LOANEE)
    view = owner.make_offer(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, fee=0, loan_weeks=None,
                                       loan_wage_share=100))
    hub_for(db, world, MEMBER).accept(view.id)
    borrower.wage_budget = borrower.wage_bill + player.current_wage - 5_000
    db.flush()
    budget, wage_budget = borrower.transfer_budget, borrower.wage_budget
    with pytest.raises(LoanError, match="Maaş havuzunda yer yok"):
        owner.complete(view.id)
    assert player.loan_id is None
    owner.complete(view.id, None, shift_wage_room=True)
    assert (borrower.wage_budget, borrower.transfer_budget) == (wage_budget + 5_000, budget - 260_000)
    assert player.loan_from_team_id == team_named(db, MEMBER_TEAM).id


def test_loaned_player_plays_for_borrower_not_parent(db, world):
    _loan_to_owner(db, world)
    cm = _quiet(cm_for(db, world))
    borrower, parent = team_named(db, OWNER_TEAM), team_named(db, MEMBER_TEAM)
    player = player_named(db, LOANEE)
    xi = cm.auto_lineup(borrower)
    if player.id not in xi:
        replaced = min((pid for pid, role in xi.items() if role is Position.FWD),
                       key=lambda pid: db.get(type(player), pid).overall_rating)
        xi[player.id] = xi.pop(replaced)
    _xi, bench, _out = cm.lineup_of(borrower)
    check = cm.set_lineup(borrower, xi, [pid for pid in bench if pid != player.id])
    assert check.ok, check.errors
    rejected = cm.set_lineup(parent, {player.id: Position.FWD}, [])
    assert not rejected.ok and "Kadroda olmayan" in rejected.errors[0]

    cm.play_week()
    stat = db.scalar(select(PlayerMatchStat).where(PlayerMatchStat.player_id == player.id))
    assert stat is not None and stat.team_id == borrower.id


def test_loaned_player_cannot_be_sold_reloaned_swapped_listed_or_parked(db, world):
    _loan_to_owner(db, world)
    player = player_named(db, LOANEE)
    third, owner = hub_for(db, world, THIRD), hub_for(db, world, OWNER)
    with pytest.raises(MarketError, match="kiralık oyuncu; kiralık dönüşüne kadar satılamaz"):
        third.make_offer(OfferDraft(player_id=player.id, fee=10_000_000))
    with pytest.raises(LoanError, match="kiralık oyuncu"):
        third.make_offer(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, loan_wage_share=100))
    with pytest.raises(MarketError, match="kiralık oyuncu; takasta verilemez"):
        owner.make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id, fee=10_000_000,
                                    exchange_player_id=player.id))
    with pytest.raises(LoanError, match="kiralık oyuncu"):
        owner.request_ai_loan(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, loan_wage_share=50,
                                         target_team_id=team_named(db, AI_TEAM).id))
    with pytest.raises(LoanError, match="Kiralık oyuncu listeye konamaz"):
        owner.set_listing(player.id, transfer_listed=True)
    cm = cm_for(db, world)
    assert cm.transfer_block_reason(player) == "Kiralık oyuncu: kiralık dönemi bitmeden satılamaz"
    with pytest.raises(TransferError, match="Kiralık oyuncu"):
        cm.offer_fee(team_named(db, AI_TEAM), player, 50_000_000)
    assert market_hub.loan_guard_reason(player) == f"{player.name} kiralık oyuncu; akademiye gönderilemez."
    assert market_hub.loan_guard_reason(player_named(db, "Harry Green")) is None
    status = owner.player_status(player.id)
    assert status.on_loan and "Kiralık oyuncu" in status.block_reason


def test_loaned_player_cannot_be_sent_to_borrower_academy(db, world):
    _loan_to_owner(db, world)
    cm = cm_for(db, world)
    with pytest.raises(AcademyError):
        cm.send_to_academy(team_named(db, OWNER_TEAM), player_named(db, LOANEE))


# ===========================================================================
# 2) Bitis: sure dolunca, sezon sonu / basi, erken geri cagirma
# ===========================================================================

def test_loan_returns_when_end_week_is_reached(db, world):
    _offer_id, loan = _loan_to_owner(db, world, weeks=4)
    assert loan.end_career_week == 5
    cm = _quiet(cm_for(db, world))
    player, parent = player_named(db, LOANEE), team_named(db, MEMBER_TEAM)
    for _ in range(3):
        cm.play_week()
        assert loan.status == "ACTIVE" and player.loan_id == loan.id
    cm.play_week()                                              # 4. hafta oynandi, sonraki hafta 5 = bitis
    assert loan.status == "RETURNED" and cm.career_week == 5
    assert (player.team_id, player.loan_from_team_id, player.loan_id, player.loan_wage_share) == \
        (parent.id, None, None, None)
    returned = db.scalar(select(TransferLog).where(TransferLog.player_id == player.id,
                                                   TransferLog.kind == "LOAN_RETURN"))
    assert returned is not None and returned.to_team_id == parent.id
    assert any("ana kulübüne döndü" in n.text for n in notifications_for(db, world, MEMBER))
    assert any("ana kulübüne döndü" in n.text for n in notifications_for(db, world, OWNER))
    assert team_named(db, OWNER_TEAM).loaned_out_players == [] and parent.loaned_out_players == []


def test_loans_end_with_the_season_and_none_cross_into_the_next(db, world):
    _offer_id, loan = _loan_to_owner(db, world, weeks=None)
    cm = _quiet(cm_for(db, world))
    for _ in range(12):
        if cm.season_finished:
            break
        cm.play_week()
    assert cm.season_finished and loan.status == "RETURNED"
    with pytest.raises(LoanError, match="Sezon bitti"):
        hub_for(db, world, OWNER).make_offer(OfferDraft(player_id=player_named(db, "Harry Green").id,
                                                        kind=OfferKind.LOAN, loan_wage_share=100))

    # sezon arasi kalmis aktif kiralik (ornegin hafta kancasi atlandi): start_new_season kancasi dondurur
    hub = hub_for(db, world, None)
    player = player_named(db, "Ethan Green")
    parent, borrower = team_named(db, MEMBER_TEAM), team_named(db, OWNER_TEAM)
    with db.begin_nested():
        stray, _news, _log = hub._start_loan(None, player, parent, borrower, cm.career_week, cm.career_week, 50, 0)
    assert player.team_id == borrower.id
    cm.start_new_season()
    assert stray.status == "RETURNED" and player.team_id == parent.id and player.loan_id is None
    assert db.scalar(select(func.count()).select_from(Loan).where(Loan.status == "ACTIVE")) == 0


def test_season_hooks_return_active_loans_directly(db, world):
    _offer_id, loan = _loan_to_owner(db, world)
    ext = MarketExtension(cm_for(db, world))
    ext.on_season_start(2)
    assert loan.status == "RETURNED" and player_named(db, LOANEE).team_id == team_named(db, MEMBER_TEAM).id
    ext.on_season_end()                                          # tekrar: bos, hata yok


def test_parent_recall_rules(db, world):
    _offer_id, loan = _loan_to_owner(db, world, share=50)
    member, owner = hub_for(db, world, MEMBER), hub_for(db, world, OWNER)
    player = player_named(db, LOANEE)
    with pytest.raises(LoanError, match="süre alıyor"):
        member.recall_loan(loan.id)
    player.concern_level = 2
    db.flush()
    with pytest.raises(LoanError, match="en az 4 hafta sonra"):
        member.recall_loan(loan.id)
    with pytest.raises(LoanError, match="Kiralık bulunamadı"):
        owner.recall_loan(loan.id)
    loan.start_career_week = cm_for(db, world).career_week - 4
    db.flush()
    assert member.loans("OUT")[0].can_recall
    owner_notes = len(notifications_for(db, world, OWNER))
    member_notes = len(notifications_for(db, world, MEMBER))
    view = member.recall_loan(loan.id)
    assert (view.status, view.can_recall) == ("RECALLED", False)
    assert player.team_id == team_named(db, MEMBER_TEAM).id and player.loan_id is None
    assert loan.end_career_week == cm_for(db, world).career_week
    assert len(notifications_for(db, world, OWNER)) == owner_notes + 1
    assert len(notifications_for(db, world, MEMBER)) == member_notes          # kendine bildirim yok
    assert db.scalar(select(func.count()).select_from(TransferLog).where(TransferLog.kind == "LOAN_RETURN")) == 1
    with pytest.raises(LoanError, match="zaten sona erdi"):
        member.recall_loan(loan.id)


def test_parent_is_told_once_when_recall_becomes_possible(db, world):
    _offer_id, loan = _loan_to_owner(db, world)
    player_named(db, LOANEE).concern_level = 3
    loan.start_career_week = -5
    db.flush()
    hub = hub_for(db, world, None)
    hub.run_week(1, None)
    hub.run_week(1, None)
    notes = [n for n in notifications_for(db, world, MEMBER) if n.ref_type == market_hub.LOAN_RECALL_REF]
    assert len(notes) == 1 and notes[0].ref_id == loan.id and "geri çağırabilirsin" in notes[0].text
    assert loan.status == "ACTIVE"                               # insan ana kulup: otomatik geri cagirma yok


def test_loaned_player_wage_demand_is_dropped_from_week(db, world):
    _loan_to_owner(db, world)
    player = player_named(db, LOANEE)
    other = player_named(db, "Marcus Wood")
    player.wage_demand, other.wage_demand = 500_000, 90_000
    db.flush()
    report = WeekReport(season=1, week=1, focus_team_id=team_named(db, OWNER_TEAM).id)
    report.wage_demands = [PlayerNote(player.id, player.name, OWNER_TEAM, "talep"),
                           PlayerNote(other.id, other.name, OWNER_TEAM, "talep")]
    hub_for(db, world, None).run_week(1, report)
    assert player.wage_demand is None and other.wage_demand == 90_000
    assert [n.player_id for n in report.wage_demands] == [other.id]


# ===========================================================================
# 3) Insan <-> AI kiralik (aninda karar)
# ===========================================================================

def test_ai_club_loans_out_only_squad_fillers_on_its_terms(db, world):
    owner = hub_for(db, world, OWNER)
    ai = team_named(db, AI_TEAM)
    filler = player_named(db, "Kerem Erdem")                     # DEF 72, kadroda 14. sira
    draft = OfferDraft(player_id=filler.id, kind=OfferKind.LOAN, fee=500_000, loan_wage_share=75)
    with pytest.raises(LoanError, match="16 oyuncunun altına düşer"):
        owner.request_ai_loan(draft)                             # 15 kisilik kadro
    _grow_ai_squad(db, ai, 3)
    with pytest.raises(LoanError, match="ilk 11 oyuncusunu"):
        owner.request_ai_loan(OfferDraft(player_id=player_named(db, "Yusuf Polat").id, kind=OfferKind.LOAN,
                                         loan_wage_share=100))
    with pytest.raises(LoanError, match="en az %75"):
        owner.request_ai_loan(OfferDraft(player_id=filler.id, kind=OfferKind.LOAN, loan_wage_share=60))
    with pytest.raises(LoanError, match="en az 6 haftalık"):
        owner.request_ai_loan(OfferDraft(player_id=filler.id, kind=OfferKind.LOAN, loan_wage_share=75,
                                         loan_weeks=4))
    ai.ai_protected_until = cm_for(db, world).career_week + 3
    db.flush()
    with pytest.raises(LoanError, match="yönetim koruması altında"):
        owner.request_ai_loan(draft)
    ai.ai_protected_until = None
    db.flush()

    budgets = (team_named(db, OWNER_TEAM).transfer_budget, ai.transfer_budget)
    view = owner.request_ai_loan(draft)
    assert (view.status, view.direction, view.parent_team, view.wage_share) == ("ACTIVE", "IN", AI_TEAM, 75)
    assert filler.team_id == team_named(db, OWNER_TEAM).id and filler.loan_from_team_id == ai.id
    assert (team_named(db, OWNER_TEAM).transfer_budget, ai.transfer_budget) == (budgets[0] - 500_000,
                                                                                 budgets[1] + 500_000)
    offer = db.get(TransferOffer, view.offer_id)
    assert (offer.kind, offer.status, offer.responded_by_manager_id, offer.created_by_manager_id) == \
        ("LOAN", "COMPLETED", None, world.seat_ids[OWNER])
    assert hub_for(db, world, ADMIN).reversible_offers() == []    # AI anlasmasi yonetici iadesine girmez
    with pytest.raises(LoanError, match="bir menajerin kulübü"):
        owner.request_ai_loan(OfferDraft(player_id=player_named(db, "Harry Green").id, kind=OfferKind.LOAN,
                                         loan_wage_share=100))


def test_ai_parent_recalls_unhappy_loanee_on_week(db, world):
    owner = hub_for(db, world, OWNER)
    ai = team_named(db, AI_TEAM)
    _grow_ai_squad(db, ai, 3)
    filler = player_named(db, "Kerem Erdem")
    view = owner.request_ai_loan(OfferDraft(player_id=filler.id, kind=OfferKind.LOAN, loan_wage_share=75))
    loan = db.get(Loan, view.id)
    hub = hub_for(db, world, None)
    hub.run_week(1, None)
    assert loan.status == "ACTIVE"
    filler.concern_level, loan.start_career_week = 2, -3
    db.flush()
    hub.run_week(1, None)
    assert loan.status == "RECALLED" and filler.team_id == ai.id and filler.loan_id is None
    assert any("geri çağrıldı" in n.text for n in notifications_for(db, world, OWNER))


def test_ai_club_borrows_only_when_it_strengthens_and_pays_no_fee(db, world):
    owner = hub_for(db, world, OWNER)
    player = player_named(db, "Marcus Wood")                     # DEF 80, 58K
    weak_ai, strong_ai = team_named(db, AI_TEAM), team_named(db, "Madrid Blancos")
    with pytest.raises(LoanError, match="kiralık için bedel ödemez"):
        owner.request_ai_loan(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, fee=1, loan_wage_share=50,
                                         target_team_id=weak_ai.id))
    with pytest.raises(LoanError, match="daha iyi oyuncuları var"):
        owner.request_ai_loan(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, loan_wage_share=50,
                                         target_team_id=strong_ai.id))
    with pytest.raises(LoanError, match="kulübü seç"):
        owner.request_ai_loan(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, loan_wage_share=50))
    parent = team_named(db, OWNER_TEAM)
    bill = parent.player_wage_bill
    view = owner.request_ai_loan(OfferDraft(player_id=player.id, kind=OfferKind.LOAN, loan_wage_share=50,
                                            target_team_id=weak_ai.id))
    assert (view.direction, view.borrower_team, view.status) == ("OUT", AI_TEAM, "ACTIVE")
    assert player.team_id == weak_ai.id and parent.player_wage_bill == bill - player.current_wage + \
        wage_split(player.current_wage, 50)[1]


# ===========================================================================
# 4) Kurallar ve yonetici iadesi
# ===========================================================================

def test_loan_terms_and_period_validation(db, world):
    owner = hub_for(db, world, OWNER)
    target = player_named(db, LOANEE)
    with pytest.raises(LoanError, match="4-52 hafta"):
        owner.make_offer(OfferDraft(player_id=target.id, kind=OfferKind.LOAN, loan_weeks=3))
    with pytest.raises(LoanError, match="%0-100"):
        owner.make_offer(OfferDraft(player_id=target.id, kind=OfferKind.LOAN, loan_wage_share=120))
    with pytest.raises(LoanError, match="takas oyuncusu olamaz"):
        owner.make_offer(OfferDraft(player_id=target.id, kind=OfferKind.LOAN,
                                    exchange_player_id=player_named(db, "Marcus Wood").id))
    with pytest.raises(LoanError, match="doğrudan kulübe gönder"):
        owner.make_offer(OfferDraft(player_id=player_named(db, "Yusuf Polat").id, kind=OfferKind.LOAN))
    with pytest.raises(MarketError, match="Transfer teklifinde kiralık süresi olmaz"):
        owner.make_offer(OfferDraft(player_id=target.id, fee=10_000_000, loan_weeks=8))

    state = cm_for(db, world).state
    state.current_week = 6
    db.flush()
    with pytest.raises(LoanError, match="Sezonun bitmesine 2 hafta var"):
        owner.make_offer(OfferDraft(player_id=target.id, kind=OfferKind.LOAN))
    state.current_week = 1
    state.world_rules = WorldRules.shared_defaults().with_changes({"loans": False}).to_dict()
    db.flush()
    with pytest.raises(LoanError, match="kiralık sistemi kapalı"):
        hub_for(db, world, OWNER).make_offer(OfferDraft(player_id=target.id, kind=OfferKind.LOAN))


def test_counter_can_change_loan_terms(db, world):
    owner, member = hub_for(db, world, OWNER), hub_for(db, world, MEMBER)
    view = owner.make_offer(OfferDraft(player_id=player_named(db, LOANEE).id, kind=OfferKind.LOAN, fee=0,
                                       loan_weeks=6, loan_wage_share=30))
    countered = member.counter(view.id, fee=250_000, loan_weeks=None, loan_wage_share=70)
    assert (countered.fee, countered.loan_weeks, countered.loan_wage_share) == (250_000, None, 70)
    kept = owner.counter(view.id, fee=200_000)
    assert (kept.loan_weeks, kept.loan_wage_share) == (None, 70)
    with pytest.raises(LoanError, match="takas oyuncusu olamaz"):
        member.counter(view.id, fee=200_000, exchange_player_id=player_named(db, "Marcus Wood").id)
    assert member.accept(view.id).status == "CONTRACT"
    with pytest.raises(LoanError, match="Kiralıkta oyuncu sözleşmesi yapılmaz"):
        owner.open_contract(view.id)
    owner.complete(view.id)
    loan = db.scalar(select(Loan).where(Loan.offer_id == view.id))
    assert (loan.wage_share, loan.end_career_week) == (70, 8)


def test_admin_reverses_active_loan_and_refunds_fee(db, world):
    budgets = (team_named(db, OWNER_TEAM).transfer_budget, team_named(db, MEMBER_TEAM).transfer_budget)
    offer_id, loan = _loan_to_owner(db, world, fee=2_000_000)
    admin = hub_for(db, world, ADMIN)
    assert [v.id for v in admin.reversible_offers()] == [offer_id]
    news = admin.reverse_transfer(offer_id, "Kiralık paravanı")
    player = player_named(db, LOANEE)
    assert [n.kind for n in news] == ["REVERSAL"]
    assert loan.status == "REVERSED" and player.team_id == team_named(db, MEMBER_TEAM).id and player.loan_id is None
    assert (team_named(db, OWNER_TEAM).transfer_budget, team_named(db, MEMBER_TEAM).transfer_budget) == budgets
    assert db.get(TransferOffer, offer_id).status == "REVERSED"
    assert (seat_row(db, world, OWNER).fair_play, seat_row(db, world, MEMBER).fair_play) == (75.0, 75.0)
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.kind == "REVIEW")) >= 2
