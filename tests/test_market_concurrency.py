"""
Faz 12 / 14. Asama B2: pazar es zamanliligi -- iki thread, iki baglanti, gercek commit'ler (web callback'i gibi:
career_context -> SHARED dunya kilidi -> session_scope).

    * ayni teklifi iki kez tamamlama: biri basarir, para BIR KEZ hareket eder
    * ayni teklifi iki kez kabul: biri CONTRACT, digeri kapali teklif hatasi
    * ayni oyuncuya iki farkli teklifin es zamanli kabulu: uq_transfer_offer_contract karar verir
    * tamamlama ile ayni oyuncudaki baska teklifin kabulu yarisi: kilitlenme yok, son durum tutarli

Engel (threading.Barrier) iki thread'i kritik adimin hemen onunde bulusturur. Her test temiz dunyada baslar (commit
ettigi icin); modul sonunda dunya yeniden kurulur. Kendi veritabaninda calistirin: TEST_DB_NAME=fm_db_test_b2.
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from career_manager import CareerManager  # noqa: E402
from market_hub import MarketError, MarketHub, OfferDraft  # noqa: E402
from models import Player, Team, TransferLog, TransferOffer  # noqa: E402
from tests.test_market_hub import (  # noqa: E402
    MEMBER,
    MEMBER_TEAM,
    OWNER,
    OWNER_TEAM,
    SCHEMA,
    THIRD,
    agree_contract,
    player_named,
    shared_market_world,
    team_named,
    teardown_market_world,
)

TARGET = "Jack Edwards"            # Merseyside Reds, 12.5M
FEE = 12_000_000
JOIN_TIMEOUT = 90


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


_LAST: dict = {}


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup():
    yield
    if _LAST.get("world") is not None:
        teardown_market_world(_LAST["world"])


@pytest.fixture
def world():
    _LAST["world"] = shared_market_world()
    return _LAST["world"]


@contextmanager
def _as(world, name: str):
    """Web callback'i gibi: kariyer baglami -> SHARED dunya kilidi -> islem (blok sonunda commit)."""
    with database.career_context(SCHEMA), database.world_lock(SCHEMA, "shared"), database.session_scope() as db:
        yield db, MarketHub(CareerManager(db, seed=11, manager_user_id=world.user_ids[name]))


@contextmanager
def _read():
    with database.career_context(SCHEMA), database.session_scope() as db:
        yield db


def _race(*actions) -> list[tuple[str, object]]:
    """Her eylem ayri thread'de; sonuclar ("ok", deger) / ("error", mesaj) / ("crash", repr)."""
    results: list[tuple[str, object]] = [("missing", None)] * len(actions)

    def run(index, action):
        try:
            results[index] = ("ok", action())
        except MarketError as exc:
            results[index] = ("error", str(exc))
        except Exception as exc:                          # noqa: BLE001 - testte her hata gorunur olmali
            results[index] = ("crash", repr(exc))

    threads = [threading.Thread(target=run, args=(i, a), daemon=True) for i, a in enumerate(actions)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT)
    assert not any(t.is_alive() for t in threads), "thread takildi (kilitlenme?)"
    return results


def _gate(monkeypatch, *method_names: str, parties: int = 2) -> threading.Barrier:
    """MarketHub metotlarinin basinda iki thread'i bulusturan engel."""
    barrier = threading.Barrier(parties, timeout=30)
    for name in method_names:
        original = getattr(MarketHub, name)

        def gated(self, *args, __original=original, **kwargs):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return __original(self, *args, **kwargs)

        monkeypatch.setattr(MarketHub, name, gated)
    return barrier


def _make_offer(world, buyer: str, player: str = TARGET, fee: int = FEE) -> int:
    with _as(world, buyer) as (db, hub):
        return hub.make_offer(OfferDraft(player_id=player_named(db, player).id, fee=fee)).id


def _contract_ready(world) -> tuple[int, object]:
    offer_id = _make_offer(world, OWNER)
    with _as(world, MEMBER) as (_db, hub):
        assert hub.accept(offer_id).status == "CONTRACT"
    with _as(world, OWNER) as (_db, hub):
        contract = agree_contract(hub, offer_id)
    return offer_id, contract


def _budgets() -> tuple[int, int]:
    with _read() as db:
        return team_named(db, OWNER_TEAM).transfer_budget, team_named(db, MEMBER_TEAM).transfer_budget


# ===========================================================================

def test_two_threads_completing_the_same_offer_move_money_once(world, monkeypatch):
    offer_id, contract = _contract_ready(world)
    buyer_before, seller_before = _budgets()
    _gate(monkeypatch, "_lock_offer_group")

    def complete():
        with _as(world, OWNER) as (_db, hub):
            return hub.complete(offer_id, contract).fee

    results = _race(complete, complete)
    assert sorted(kind for kind, _ in results) == ["error", "ok"], results
    assert next(v for k, v in results if k == "ok") == FEE
    assert "Transfer tamamlandı" in next(v for k, v in results if k == "error")
    assert _budgets() == (buyer_before - FEE, seller_before + FEE)
    with _read() as db:
        player = player_named(db, TARGET)
        assert player.team_id == team_named(db, OWNER_TEAM).id
        assert db.get(TransferOffer, offer_id).status == "COMPLETED"
        assert db.scalar(select(func.count()).select_from(TransferLog).where(TransferLog.player_id == player.id)) == 1


def test_two_threads_accepting_the_same_offer(world, monkeypatch):
    offer_id = _make_offer(world, OWNER)
    _gate(monkeypatch, "_offer_row")

    def accept():
        with _as(world, MEMBER) as (_db, hub):
            return hub.accept(offer_id).status

    results = _race(accept, accept)
    assert sorted(results) == [("error", "Bonservis anlaşması yapıldı; teklif sözleşme masasında."),
                               ("ok", "CONTRACT")], results
    with _read() as db:
        offer = db.get(TransferOffer, offer_id)
        assert offer.status == "CONTRACT"
        assert len([e for e in offer.contract_log if e.get("kind") == "fairness"]) == 1


def test_simultaneous_accepts_for_one_player_are_decided_by_the_contract_index(world, monkeypatch):
    first, second = _make_offer(world, OWNER), _make_offer(world, THIRD, fee=FEE + 500_000)
    _gate(monkeypatch, "_evaluate_offer")                  # iki thread de kendi kilitlerini aldiktan sonra

    def accept(offer_id):
        def action():
            with _as(world, MEMBER) as (_db, hub):
                return hub.accept(offer_id).status
        return action

    results = _race(accept(first), accept(second))
    assert sorted(kind for kind, _ in results) == ["error", "ok"], results
    assert "başka bir kulüple sözleşme görüşmesi sürüyor" in next(v for k, v in results if k == "error")
    with _read() as db:
        statuses = sorted(db.get(TransferOffer, i).status for i in (first, second))
        assert statuses == ["CONTRACT", "PENDING"]
        player_id = player_named(db, TARGET).id
        assert db.scalar(select(func.count()).select_from(TransferOffer).where(
            TransferOffer.player_id == player_id, TransferOffer.status == "CONTRACT")) == 1


def test_completion_racing_an_accept_on_the_same_player_stays_consistent(world, monkeypatch):
    offer_id, contract = _contract_ready(world)
    rival = _make_offer(world, THIRD, fee=FEE + 1_000_000)
    buyer_before, seller_before = _budgets()
    _gate(monkeypatch, "_lock_offer_group", "_offer_row")

    def complete():
        with _as(world, OWNER) as (_db, hub):
            return hub.complete(offer_id, contract).fee

    def accept_rival():
        with _as(world, MEMBER) as (_db, hub):
            return hub.accept(rival).status

    results = _race(complete, accept_rival)
    assert results[0] == ("ok", FEE), results
    assert results[1][0] == "error", results
    assert _budgets() == (buyer_before - FEE, seller_before + FEE)
    with _read() as db:
        assert db.get(TransferOffer, offer_id).status == "COMPLETED"
        assert db.get(TransferOffer, rival).status == "VOIDED"
        player = db.scalar(select(Player).where(Player.name == TARGET))
        assert player.team_id == db.scalar(select(Team.id).where(Team.name == OWNER_TEAM))
