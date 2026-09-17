"""
seats.SeatStore testleri (Faz 12 / 14. Asama, A2): birincil koltuk / diger koltuk ayrimi, kulup benzersizligi,
birakma ve koruma, hazir isareti, adil oyun, tanınırlık yazimi.

Gercek PostgreSQL'e karsi calisir; her test kendi islemini rollback eder.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reputation  # noqa: E402
from models import FairPlayLog, GameState, SeatStatus, Team, User, WorldManager  # noqa: E402
from seats import Seat, SeatError, SeatStore, clean_display_name  # noqa: E402


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
        session.execute(WorldManager.__table__.delete())             # temiz koltuk tablosu (islem geri alinir)
        state = session.get(GameState, 1)
        state.user_team_id, state.user_id, state.manager_reputation = None, None, reputation.START_REPUTATION
        session.flush()
        yield session
    finally:
        session.rollback()
        session.close()


def _account(db, name: str = "koltuk") -> int:
    user = User(username=f"{name}_{uuid.uuid4().hex[:8]}"[:32], password_hash="scrypt$test$not-a-real-hash")
    db.add(user)
    db.flush()
    return user.id


def _team(db, name: str) -> Team:
    team = db.scalar(select(Team).where(Team.name == name))
    assert team is not None, name
    return team


def test_primary_seat_without_row_reads_game_state(db):
    store = SeatStore(db)
    state = db.get(GameState, 1)
    lions = _team(db, "Istanbul Lions")
    state.user_team_id, state.manager_reputation = lions.id, 11.5
    db.flush()
    primary = store.primary()
    assert isinstance(primary, Seat) and primary.id is None and primary.is_primary
    assert (primary.team_id, primary.reputation, primary.status) == (lions.id, 11.5, SeatStatus.ACTIVE.value)
    assert store.resolve(None) == primary and store.by_team(lions.id) == primary
    assert store.human_team_ids() == frozenset({lions.id}) and store.member_count() == 1
    assert [s.is_primary for s in store.active()] == [True] and store.reputation_for_team(lions.id) == 11.5
    assert store.by_team(_team(db, "Madrid Blancos").id) is None


def test_ensure_primary_row_is_idempotent_and_check_constraints_hold(db):
    store = SeatStore(db)
    owner = _account(db, "sahip")
    first = store.ensure_primary_row(owner, None)
    assert first.id is not None and first.user_id == owner and first.display_name.startswith("sahip_")
    assert store.ensure_primary_row(owner, "Baska Ad").id == first.id
    assert db.scalar(select(WorldManager).where(WorldManager.is_primary.is_(True))).display_name == first.display_name
    row = db.get(WorldManager, first.id)
    with pytest.raises(IntegrityError, match="ck_world_manager_primary_fields"), db.begin_nested():
        row.team_id = _team(db, "Madrid Blancos").id
        db.flush()
    with pytest.raises(IntegrityError, match="uq_world_manager_primary"), db.begin_nested():
        db.add(WorldManager(user_id=None, display_name="Ikinci", is_primary=True))
        db.flush()
    with pytest.raises(IntegrityError, match="ck_world_manager_user"), db.begin_nested():
        db.add(WorldManager(user_id=None, display_name="Kimliksiz", is_primary=False))
        db.flush()
    assert store.resolve(owner).is_primary                               # satirdaki kullanici da birincildir


def test_ensure_primary_row_links_ownerless_row(db):
    store = SeatStore(db)
    ownerless = store.ensure_primary_row(None, None)
    assert ownerless.user_id is None and ownerless.display_name == "Menajer"
    owner = _account(db, "sonradan")
    linked = store.ensure_primary_row(owner, "Yok Sayilir")
    assert (linked.id, linked.user_id, linked.display_name) == (ownerless.id, owner, "Menajer")
    member = _account(db, "uye")
    store.create_seat(member, "Uye", 8.0, 1)
    db.get(WorldManager, ownerless.id).user_id = None
    db.flush()
    assert store.ensure_primary_row(member, None).user_id is None        # baska koltuktaki kullanici baglanmaz


def test_create_seat_rules_and_rejoin(db):
    store = SeatStore(db)
    state = db.get(GameState, 1)
    owner, member = _account(db, "sahip"), _account(db, "uye")
    state.user_id = owner
    db.flush()
    with pytest.raises(SeatError, match="sahibi"):
        store.create_seat(owner, "Sahip", 8.0, 1)
    with pytest.raises(SeatError, match="boş"):
        store.create_seat(member, "   ", 8.0, 1)
    seat = store.create_seat(member, "  Uzun   Adli Menajer " + "x" * 40, 25.0, 3)
    assert not seat.is_primary and seat.team_id is None and seat.status == SeatStatus.ACTIVE.value
    assert seat.reputation == reputation.MAX_REPUTATION and seat.joined_career_week == 3
    assert len(seat.display_name) == 32 and seat.display_name.startswith("Uzun Adli Menajer")
    with pytest.raises(SeatError, match="zaten"):
        store.create_seat(member, "Tekrar", 8.0, 3)
    assert store.resolve(member) == seat and store.resolve(_account(db, "yabanci")) is None
    assert store.member_count() == 2 and [s.id for s in store.members()][1:] == [seat.id]

    store.release(seat, SeatStatus.LEFT.value, None)
    assert store.member_count() == 1 and store.resolve(member).status == SeatStatus.LEFT.value
    again = store.create_seat(member, "Geri Dönen", 9.0, 10)
    assert again.id == seat.id and again.status == SeatStatus.ACTIVE.value and again.joined_career_week == 10
    assert clean_display_name(" a  b ") == "a b"


def test_assign_team_uniqueness_includes_game_state_club(db):
    store = SeatStore(db)
    state = db.get(GameState, 1)
    lions, madrid, london = _team(db, "Istanbul Lions"), _team(db, "Madrid Blancos"), _team(db, "London Gunners")
    state.user_team_id = lions.id
    db.flush()
    a = store.create_seat(_account(db, "a"), "A", 8.0, 1)
    b = store.create_seat(_account(db, "b"), "B", 8.0, 1)
    with pytest.raises(SeatError, match="dünya sahibinin"):
        store.assign_team(a, lions.id)
    a = store.assign_team(a, madrid.id)
    assert a.team_id == madrid.id and store.by_team(madrid.id) == a
    with pytest.raises(SeatError, match="başka bir menajerin"):
        store.assign_team(b, madrid.id)
    with pytest.raises(SeatError, match="Kulüp bulunamadı"):
        store.assign_team(b, 987654)
    with pytest.raises(SeatError, match="başka bir menajerin"):
        store.assign_team(store.primary(), madrid.id)                   # birincil de alamaz
    assert store.human_team_ids() == frozenset({lions.id, madrid.id})

    moved = store.assign_team(a, london.id)                              # kulup degistirme
    assert moved.team_id == london.id and store.by_team(madrid.id) is None
    b = store.assign_team(b, madrid.id)
    primary = store.assign_team(store.primary(), None)
    assert primary.team_id is None and state.user_team_id is None
    assert store.human_team_ids() == frozenset({london.id, madrid.id})
    assert store.assign_team(store.primary(), lions.id).team_id == lions.id


def test_release_sets_protection_and_primary_cannot_leave(db):
    store = SeatStore(db)
    state = db.get(GameState, 1)
    lions, madrid = _team(db, "Istanbul Lions"), _team(db, "Madrid Blancos")
    state.user_team_id = lions.id
    madrid.ai_protected_until = 50
    db.flush()
    seat = store.assign_team(store.create_seat(_account(db), "Koltuk", 8.0, 1), madrid.id)
    store.set_ready(seat, 4)
    assert store.by_id(seat.id).is_ready(4) and not store.by_id(seat.id).is_ready(5)
    assert store.release(seat, SeatStatus.RELEASED.value, 12) == madrid.id
    released = store.by_id(seat.id)
    assert (released.team_id, released.status, released.ready_career_week) == (None, "RELEASED", None)
    assert madrid.ai_protected_until == 50                               # daha uzun koruma korunur
    assert store.member_count() == 2 and store.human_team_ids() == frozenset({lions.id})
    with pytest.raises(SeatError, match="Geçersiz koltuk durumu"):
        store.release(released, "ACTIVE", None)
    with pytest.raises(SeatError, match="ayrılamaz"):
        store.release(store.primary(), SeatStatus.LEFT.value, None)

    london = _team(db, "London Gunners")
    other = store.assign_team(released, london.id)
    assert other.status == SeatStatus.ACTIVE.value
    assert store.release(other, SeatStatus.KICKED.value, 9) == london.id and london.ai_protected_until == 9
    with pytest.raises(SeatError, match="ayrılmış"):
        store.assign_team(store.by_id(seat.id), madrid.id)
    assert store.release(store.primary(), SeatStatus.RELEASED.value, 7) == lions.id
    assert state.user_team_id is None and lions.ai_protected_until == 7
    with pytest.raises(SeatError, match="bulunamadı"):
        store.set_ready(store.primary(), 1)                              # birincil satiri yok


def test_apply_reputation_writes_game_state_for_primary_and_row_otherwise(db):
    store = SeatStore(db)
    state = db.get(GameState, 1)
    lions, madrid = _team(db, "Istanbul Lions"), _team(db, "Madrid Blancos")
    state.user_team_id, state.manager_reputation = lions.id, 8.0
    db.flush()
    seat = store.assign_team(store.create_seat(_account(db), "Koltuk", 12.0, 1), madrid.id)
    assert store.apply_reputation(lions.id, 0.456) == (8.0, 8.46) and state.manager_reputation == 8.46
    assert store.apply_reputation(madrid.id, -30.0) == (12.0, reputation.MIN_REPUTATION)
    assert db.get(WorldManager, seat.id).reputation == reputation.MIN_REPUTATION
    assert store.reputation_for_team(madrid.id) == reputation.MIN_REPUTATION
    with pytest.raises(SeatError, match="yöneten bir menajer yok"):
        store.apply_reputation(_team(db, "London Gunners").id, 1.0)


def test_adjust_fair_play_clamps_and_logs(db):
    store = SeatStore(db)
    seat = store.create_seat(_account(db), "Koltuk", 8.0, 1)
    db.get(GameState, 1).current_week = 3
    db.flush()
    assert store.adjust_fair_play(seat.id, -25.0, "Engellenen takas") == 75.0
    assert store.adjust_fair_play(seat.id, 40.0, "x" * 200) == 100.0
    logs = db.scalars(select(FairPlayLog).where(FairPlayLog.manager_id == seat.id).order_by(FairPlayLog.id)).all()
    assert [(log.delta, log.career_week) for log in logs] == [(-25.0, 3), (25.0, 3)]
    assert len(logs[1].reason) == 120
    with pytest.raises(SeatError):
        store.adjust_fair_play(999_999, 1.0, "yok")


def test_touch_is_throttled(db):
    store = SeatStore(db)
    seat = store.create_seat(_account(db), "Koltuk", 8.0, 1)
    now = datetime.now(timezone.utc)
    store.touch(seat, now)
    db.expire_all()
    first = db.get(WorldManager, seat.id).last_active_at
    assert first is not None
    store.touch(seat, now + timedelta(minutes=2))                        # 5 dakika dolmadi: yazilmaz
    db.expire_all()
    assert db.get(WorldManager, seat.id).last_active_at == first
    store.touch(seat, now + timedelta(minutes=6))
    db.expire_all()
    assert db.get(WorldManager, seat.id).last_active_at > first
    store.touch(store.primary(), now)                                    # satiri olmayan birincil: sessiz
