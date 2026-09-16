"""
Transfer yasagi testleri (12. Asama, Soccer Manager "transfer ban"): kulup degistiren oyuncu TRANSFER_BAN_WEEKS
oyun haftasi satilamaz ve teklif alamaz; sayac mutlak kariyer haftasidir ve sezon devrinde kesintisiz isler.

Gercek PostgreSQL'e karsi calisir; her test kendi transaction'ini rollback eder (onerilen: TEST_DB_NAME=fm_db_test_smpkg).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import career_manager as cm_module  # noqa: E402
from career_manager import TRANSFER_BAN_WEEKS, CareerManager  # noqa: E402
from models import Fixture, FixtureStatus, Player, Position, TransferLog  # noqa: E402
from transfers import ContractOffer, TransferError  # noqa: E402


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


def _manager(db, seed=7) -> CareerManager:
    cm = CareerManager(db, seed=seed)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    return cm


def _outfield(team) -> Player:
    """Satisa acik (kaleci olmayan, en zayif) A takim oyuncusu."""
    return min((p for p in team.players if p.position is not Position.GK), key=lambda p: (p.overall_rating, p.id))


def _buy(cm: CareerManager, buyer, player, fee: int = 1_000_000):
    buyer.transfer_budget = max(buyer.transfer_budget, fee)
    offer = ContractOffer(wage=min(player.current_wage, max(0, buyer.free_wage)), years=3, role=player.squad_role)
    return cm.complete_transfer(buyer, player, fee, offer)


def test_new_signing_is_locked_for_six_game_weeks(db):
    cm = _manager(db)
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    seller = cm.find_team("Merseyside Reds")
    player = _outfield(seller)
    assert cm.transfer_ban_info(player) == (False, "")

    news = _buy(cm, user, player)
    assert news.kind == "TRANSFER" and news.to_team_id == user.id and news.from_team_id == seller.id
    assert TRANSFER_BAN_WEEKS == 6
    assert player.transfer_locked_until == cm.career_week + TRANSFER_BAN_WEEKS
    assert cm.transfer_ban_info(player) == (True, "Yeni transfer: 6 hafta daha satılamaz")

    # Baska bir kulup (kullaniciyi degistirerek de olsa) teklif yapamaz; sozlesme masasi ve imza da kapali
    rival = cm.find_team("London Gunners")
    rival.transfer_budget = 10 ** 9
    with pytest.raises(TransferError, match="Yeni transfer: 6 hafta daha satılamaz"):
        cm.offer_fee(rival, player, 50_000_000)
    with pytest.raises(TransferError, match="teklif yapılamaz"):
        cm.open_negotiation(rival, player, 50_000_000)
    with pytest.raises(TransferError, match="Yeni transfer"):
        cm.complete_transfer(rival, player, 50_000_000, ContractOffer(wage=1, years=2, role=player.squad_role))
    assert player.team_id == user.id

    cm.state.current_week += 3
    assert cm.transfer_ban_info(player) == (True, "Yeni transfer: 3 hafta daha satılamaz")
    cm.state.current_week += 3
    assert cm.transfer_ban_info(player) == (False, "")
    decision = cm.offer_fee(rival, player, 50_000_000)                    # yasak bitti: kulup degerlendirir
    assert decision.asking > 0


def test_ban_counts_played_weeks_across_the_season_boundary(db):
    cm = _manager(db)
    user = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(user)
    last_week = cm._projected_season_weeks()
    cm.state.current_week = last_week                                     # sezonun son haftasi
    player = _outfield(cm.find_team("Paris Rouge-Bleu"))
    _buy(cm, user, player)
    assert player.transfer_locked_until == last_week + TRANSFER_BAN_WEEKS

    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=1, away_score=0))
    db.flush()
    db.expire_all()
    cm.state.current_week = last_week + 1                                 # son hafta oynandi
    remaining = TRANSFER_BAN_WEEKS - 1
    assert cm.transfer_ban_info(player) == (True, f"Yeni transfer: {remaining} hafta daha satılamaz")
    offset = cm.state.career_week_offset
    cm.start_new_season()
    assert cm.state.career_week_offset == offset + last_week and cm.current_week == 1
    # Sezon arasi hafta sayilmaz: yeni sezonun 1. haftasinda kalan sure ayni
    assert cm.transfer_ban_info(player) == (True, f"Yeni transfer: {remaining} hafta daha satılamaz")
    cm.state.current_week = 1 + remaining
    assert cm.transfer_ban_info(player) == (False, "")


def test_play_week_advances_the_ban_counter(db):
    cm = _manager(db)
    cm.run_ai_transfer_window = lambda: []
    user = cm.find_team("Bosphorus Eagles")
    cm.set_user_team(user)
    player = _outfield(cm.find_team("Milano Rossoneri"))
    _buy(cm, user, player)
    start = cm.career_week
    for weeks in range(1, 4):
        cm.play_week()
        assert cm.career_week == start + weeks
        assert cm.transfer_ban_info(player) == (True, f"Yeni transfer: {TRANSFER_BAN_WEEKS - weeks} hafta daha satılamaz")


def _ai_window_deals(banned: bool, monkeypatch) -> tuple[list, dict]:
    """Ayri oturumda (rollback) cok istekli AI pazari: tum oyuncular yasakliyken / degilken."""
    from database import SessionLocal

    monkeypatch.setattr(cm_module, "AI_TRANSFER_CHANCE", 1.0)
    monkeypatch.setattr(cm_module, "AI_MIN_TARGET_SCORE", -100.0)
    session = SessionLocal()
    try:
        cm = CareerManager(session, seed=19)
        cm.set_user_team(cm.find_team("Istanbul Lions"))
        for team in cm.teams():
            team.transfer_budget = 10 ** 9
            team.wage_budget = team.wage_bill + 5_000_000
        if banned:
            session.execute(update(Player).values(transfer_locked_until=cm.career_week + 2))
        session.flush()
        session.expire_all()
        deals = []
        for _ in range(3):
            deals += cm.run_ai_transfer_window()
        locks = {p.id: (p.transfer_locked_until, cm.career_week) for p in session.scalars(
            select(Player).where(Player.id.in_([d.player_id for d in deals])))}
        logged = session.scalars(select(TransferLog)).all()
        return deals, {"locks": locks, "logged": [(t.player_id, t.fee, t.kind) for t in logged]}
    finally:
        session.rollback()
        session.close()


def test_ai_market_skips_banned_players_and_bans_its_own_signings(monkeypatch):
    open_deals, info = _ai_window_deals(banned=False, monkeypatch=monkeypatch)
    if not open_deals:
        pytest.skip("bu tohumda AI transferi gerçekleşmedi")
    for deal in open_deals:
        locked, week = info["locks"][deal.player_id]
        assert locked == week + TRANSFER_BAN_WEEKS                           # AI transferi de yasak baslatir
        assert (deal.player_id, deal.fee, "TRANSFER") in info["logged"]
    # Ayni oyuncu yasak nedeniyle ayni pencerede/haftada ikinci kez el degistirmez
    assert len({d.player_id for d in open_deals}) == len(open_deals)

    banned_deals, _info = _ai_window_deals(banned=True, monkeypatch=monkeypatch)
    assert banned_deals == []
