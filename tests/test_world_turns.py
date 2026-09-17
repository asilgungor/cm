"""
Faz 12 / 14. Asama A3: tur motoru ve dunya yonetimi (world_manager.py) -- PostgreSQL + thread testleri.

Her test iki arka uc ile calisir (backend parametresi):
    fake -> seats.SeatStore / worlds.mark_membership / WeekReport.view_for yerine bu dosyadaki kucuk taklitler
            (A1 / A2 paketleri henuz iskeletken tur motorunun kilit, islem ve algoritma davranisi dogrulanir)
    real -> gercek A1 (worlds) ve A2 (seats, cok koltuklu CareerManager) katmani; iskeletse ACIK nedenle atlanir
            (modul yuklenirken bir kez yoklanir: _REAL_STACK_MISSING)

Testler sentetik 'public' dunyasini paylasilan dunyaya cevirir (tests/world_helpers.make_shared_public) ve sonunda
yeniden kurar (cleanup_shared). Kendi veritabaninda calistirin: TEST_DB_NAME=fm_db_test_a3.
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import world_manager as wm  # noqa: E402
import worlds  # noqa: E402
from career_manager import CareerManager, WeekReport  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    FixtureStatus,
    GameState,
    ManagerWeekReport,
    Team,
    World,
    WorldEvent,
    WorldManager,
    WorldMembership,
)
from seats import Seat, SeatError  # noqa: E402
from turn_rules import AdvanceTrigger  # noqa: E402
from world_rules import RulesError, WorldRules  # noqa: E402
from worlds import WorldContext  # noqa: E402

SCHEMA = "public"
OWNER, MEMBER, SPARE = "a3_sahip", "a3_uye", "a3_bos"
OWNER_TEAM, MEMBER_TEAM = "Istanbul Lions", "Kadıköy Canaries"
SMALL_CLUB, BIG_CLUB = "Bosphorus Eagles", "Madrid Blancos"       # seviye 1 icin uygun / uygun degil


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


def _probe_real_stack() -> str | None:
    """Gercek A1 / A2 katmani hazir mi? Hazirsa None, degilse Turkce neden (yan etkisiz yoklama)."""
    import seats

    try:
        seats.SeatStore(None)
    except NotImplementedError:
        return "seats.SeatStore (Faz 12 A2) henüz iskelet"
    except Exception:                        # gercek kurucu None ile hata verebilir: uygulanmis demektir
        pass
    if "manager_user_id" not in inspect.signature(CareerManager.__init__).parameters:
        return "CareerManager(manager_user_id=...) (Faz 12 A2) henüz yok"
    if not hasattr(WeekReport, "view_for"):
        return "WeekReport.view_for (Faz 12 A2) henüz yok"
    try:
        worlds.mark_membership(None, 0, 0, "ACTIVE")
    except NotImplementedError:
        return "worlds.mark_membership (Faz 12 A1) henüz iskelet"
    except Exception:
        pass
    return None


_REAL_STACK_MISSING = _probe_real_stack()


# ===========================================================================
# Taklitler (fake arka uc): A2 SeatStore sozlesmesi world_managers + GameState uzerinde
# ===========================================================================

class FakeSeatStore:
    """seats.SeatStore sozlesmesinin test taklidi: birincil koltugun kulubu / tanınırlığı GameState'te."""

    TOUCH_EVERY = timedelta(minutes=5)

    def __init__(self, db) -> None:
        self.db = db

    def _state(self) -> GameState:
        return self.db.get(GameState, 1)

    def _rows(self, *where) -> list[WorldManager]:
        return list(self.db.scalars(select(WorldManager).where(*where).order_by(WorldManager.id)
                                    .execution_options(populate_existing=True)))

    def _seat(self, row: WorldManager) -> Seat:
        state = self._state()
        team_id = state.user_team_id if row.is_primary else row.team_id
        rep = state.manager_reputation if row.is_primary else (row.reputation or 8.0)
        return Seat(row.id, row.user_id, row.display_name, row.is_primary, team_id, float(rep), row.status,
                    row.ready_career_week, int(row.missed_deadlines), float(row.fair_play),
                    int(row.joined_career_week), row.nation_id)

    def _row(self, seat_id: int) -> WorldManager:
        return self._rows(WorldManager.id == seat_id)[0]

    def primary(self) -> Seat:
        rows = self._rows(WorldManager.is_primary.is_(True))
        if rows:
            return self._seat(rows[0])
        state = self._state()
        return Seat(None, state.user_id, "Menajer", True, state.user_team_id, state.manager_reputation, "ACTIVE",
                    None, 0, 100.0, 1, None)

    def resolve(self, user_id):
        if user_id is None or user_id == self._state().user_id:
            return self.primary()
        rows = self._rows(WorldManager.user_id == user_id)
        return self._seat(rows[0]) if rows else None

    def by_team(self, team_id):
        if team_id == self._state().user_team_id:
            return self.primary()
        rows = self._rows(WorldManager.team_id == team_id)
        return self._seat(rows[0]) if rows else None

    def by_id(self, seat_id):
        rows = self._rows(WorldManager.id == seat_id)
        return self._seat(rows[0]) if rows else None

    def active(self):
        return [self._seat(r) for r in self._rows(WorldManager.status == "ACTIVE")]

    def human_team_ids(self):
        ids = {r.team_id for r in self._rows(WorldManager.status == "ACTIVE", WorldManager.is_primary.is_(False))}
        ids.add(self._state().user_team_id)
        return frozenset(i for i in ids if i is not None)

    def release(self, seat, status, protected_until):
        row, state = self._row(seat.id), self._state()
        team_id = state.user_team_id if row.is_primary else row.team_id
        if row.is_primary:
            state.user_team_id = None
        else:
            row.team_id = None
        row.status = status
        if team_id is not None and protected_until is not None:
            self.db.get(Team, team_id).ai_protected_until = protected_until
        self.db.flush()
        return team_id

    def assign_team(self, seat, team_id):
        if team_id in self.human_team_ids():
            raise SeatError("Bu kulübü başka bir menajer yönetiyor.")
        row = self._row(seat.id)
        if row.is_primary:
            self._state().user_team_id = team_id
        else:
            row.team_id = team_id
        row.status = "ACTIVE"
        self.db.flush()
        return self._seat(row)

    def set_ready(self, seat, career_week):
        self._row(seat.id).ready_career_week = career_week
        self.db.flush()

    def touch(self, seat, now):
        row = self._row(seat.id)
        if row.last_active_at is None or now - row.last_active_at >= self.TOUCH_EVERY:
            row.last_active_at = now
            self.db.flush()

    def ensure_primary_row(self, user_id, display_name):
        rows = self._rows(WorldManager.is_primary.is_(True))
        if not rows:
            self.db.add(WorldManager(user_id=user_id, display_name=display_name, is_primary=True))
            self.db.flush()
        return self.primary()


def _fake_mark_membership(db, world_id, user_id, status):
    db.execute(update(WorldMembership).where(WorldMembership.world_id == world_id, WorldMembership.user_id == user_id)
               .values(status=status, left_at=func.now()))


@pytest.fixture(params=["fake", "real"])
def backend(request, monkeypatch):
    if request.param == "real":
        if _REAL_STACK_MISSING:
            pytest.skip(f"Gerçek A1/A2 katmanı hazır değil: {_REAL_STACK_MISSING}")
        return "real"
    monkeypatch.setattr(wm, "SeatStore", FakeSeatStore)
    monkeypatch.setattr(worlds, "mark_membership", _fake_mark_membership)
    monkeypatch.setattr(WeekReport, "view_for", lambda self, team_id: self, raising=False)
    return "fake"


@pytest.fixture
def world(backend):
    from tests.world_helpers import cleanup_shared, make_shared_public

    cleanup_shared(None, reseed=False)                     # onceki kosudan kalan kayit / kullanici
    _drop_test_users()
    shared = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM), (SPARE, None)], owner_team=OWNER_TEAM)
    try:
        yield shared
    finally:
        cleanup_shared(shared)


def _drop_test_users() -> None:
    database.init_accounts()
    with database.engine.begin() as conn:
        conn.execute(text('DELETE FROM "accounts".users WHERE username = ANY(:names)'),
                     {"names": [OWNER, MEMBER, SPARE]})


# ===========================================================================
# Yardimcilar
# ===========================================================================

def _ctx(shared, username: str, role: str = "MEMBER") -> WorldContext:
    return WorldContext(world_id=shared.world_id, schema=SCHEMA, name="Test Dünyası", kind="SHARED", role=role,
                        user_id=shared.user_ids[username], world_seed=None)


@contextmanager
def _controller(ctx: WorldContext):
    """Web callback'i gibi: SHARED dunya kilidi altinda kontrolcu; blok sonunda commit."""
    with database.career_context(ctx.schema), database.world_lock(ctx.schema, "shared"), \
            database.session_scope() as db:
        yield wm.WorldController(db, ctx)


def _read(fn):
    with database.career_context(SCHEMA), database.session_scope() as db:
        return fn(db)


def _state_tuple() -> tuple[int, int]:
    return _read(lambda db: (db.get(GameState, 1).season, db.get(GameState, 1).current_week))


def _set_state(**values) -> None:
    with database.career_context(SCHEMA), database.session_scope() as db:
        state = db.get(GameState, 1)
        for key, value in values.items():
            setattr(state, key, value)


def _set_rules(**changes) -> None:
    with database.career_context(SCHEMA), database.session_scope() as db:
        state = db.get(GameState, 1)
        state.world_rules = WorldRules.from_dict(state.world_rules).with_changes(changes).to_dict()


def _count(model, *where) -> int:
    return _read(lambda db: db.scalar(select(func.count()).select_from(model).where(*where)))


def _career_week() -> int:
    return _read(lambda db: CareerManager(db).career_week)


def _team_id(name: str) -> int:
    return _read(lambda db: db.scalar(select(Team.id).where(Team.name == name)))


def _ready(ctx: WorldContext) -> wm.TurnStatus:
    with _controller(ctx) as wc:
        return wc.set_ready(True, wc.career_week)


def _open_turn(opened: datetime, hours: int = 24) -> None:
    _set_state(turn_opened_at=opened, turn_deadline_at=opened + timedelta(hours=hours))


class _LockHolder:
    """Ayri ham baglantida islem seviyesi dunya kilidi (menajer callback'i ya da baska ilerleme gibi)."""

    def __init__(self, function: str = "pg_advisory_xact_lock_shared") -> None:
        self.function = function
        self.started, self.released = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        with database.engine.connect() as conn:
            conn.execute(text(f"SELECT {self.function}(:ns, hashtext(:k))"),
                         {"ns": database.LOCK_WORLD_TURN, "k": SCHEMA})
            self.started.set()
            self.released.wait(30)
            conn.rollback()

    def __enter__(self):
        self.thread.start()
        assert self.started.wait(10)
        return self

    def __exit__(self, *exc):
        self.released.set()
        self.thread.join(10)


# ===========================================================================
# Hafta ilerletme
# ===========================================================================

def test_all_ready_advances_exactly_once_with_two_concurrent_requests(world):
    owner, member, spare = _ctx(world, OWNER, "OWNER"), _ctx(world, MEMBER), _ctx(world, SPARE)
    status = _ready(owner)
    assert (status.active, status.ready, status.me_ready) == (2, 1, True)
    assert status.waiting_names == [MEMBER] and status.opened_at is not None       # ilk islem turu acti
    with _controller(spare) as wc:                                                 # kulupsuz koltuk sayilmaz
        assert not wc.turn_status().can_ready
    status = _ready(member)
    assert (status.active, status.ready, status.waiting_names) == (2, 2, [])

    barrier = threading.Barrier(2)
    outcomes: list = []

    def request(ctx):
        barrier.wait(10)
        try:
            outcomes.append(wm.try_advance(ctx, AdvanceTrigger.READY, expected=(1, 1)))
        except Exception as exc:                          # AdvanceInProgress beklenir
            outcomes.append(exc)

    threads = [threading.Thread(target=request, args=(ctx,)) for ctx in (owner, member)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    advanced = [o for o in outcomes if isinstance(o, wm.AdvanceResult) and o.advanced]
    others = [o for o in outcomes if o not in advanced]
    assert len(advanced) == 1 and len(others) == 1, outcomes
    other = others[0]
    assert isinstance(other, wm.AdvanceInProgress) or (other.advanced is False and other.message == wm.MOVED_TEXT)
    result = advanced[0]
    assert (result.kind, result.trigger, result.season, result.week) == ("WEEK", AdvanceTrigger.READY, 1, 2)
    assert result.world_id == world.world_id and result.released == []
    assert _state_tuple() == (1, 2)
    assert _count(WorldEvent, WorldEvent.kind == "ADVANCE") == 1

    state = _read(lambda db: db.get(GameState, 1))
    assert state.last_advance_trigger == "READY" and state.last_advance_at is not None
    assert state.turn_deadline_at - state.turn_opened_at == timedelta(hours=24)
    assert state.last_advance_by in (world.user_ids[OWNER], world.user_ids[MEMBER])
    with _controller(owner) as wc:
        status = wc.turn_status()
        assert (status.week, status.ready, status.me_ready) == (2, 0, False)       # yeni tur temiz
        # ayni hafta icin ikinci istek (bayat expected) hicbir sey yapmaz
    again = wm.try_advance(member, AdvanceTrigger.READY, expected=(1, 1))
    assert not again.advanced and again.message == wm.MOVED_TEXT and _state_tuple() == (1, 2)


def test_per_seat_week_reports_are_persisted_for_every_managed_club(world, backend):
    owner, member, spare = _ctx(world, OWNER, "OWNER"), _ctx(world, MEMBER), _ctx(world, SPARE)
    _set_state(manager_reputation=12.0)                          # sahibin tanınırlığı uyeninkinden (8.0) farkli
    assert wm.try_advance(owner, AdvanceTrigger.FORCED, expected=(1, 1)).advanced

    rows = _read(lambda db: [(r.manager_id, r.season, r.week, r.midweek)
                             for r in db.scalars(select(ManagerWeekReport).order_by(ManagerWeekReport.manager_id))])
    assert rows == sorted([(world.seat_ids[OWNER], 1, 1, False), (world.seat_ids[MEMBER], 1, 1, False)])
    for ctx in (owner, member):
        with _controller(ctx) as wc:
            reports = wc.my_reports()
            assert len(reports) == 1
            report = reports[0]
            assert (report.season, report.week, report.midweek) == (1, 1, False)
            assert report.lines[0] == ("info", "Sezon 1, 1. hafta oynandı.")
            assert any(kind == "result" for kind, _ in report.lines)
            assert any(line.startswith("Menajer tanınırlığı") for _, line in report.lines)
    with _controller(spare) as wc:
        assert wc.my_reports() == []
    if backend == "real":                                        # WeekReport.view_for: her koltuk kendi kulubunu gorur
        def reputation_line(ctx):
            with _controller(ctx) as wc:
                return next(line for _, line in wc.my_reports()[0].lines if line.startswith("Menajer tanınırlığı"))

        assert reputation_line(owner).startswith("Menajer tanınırlığı 12.00 →")
        assert reputation_line(member).startswith("Menajer tanınırlığı 8.00 →")


def test_member_cannot_force_the_week_but_the_owner_can(world):
    owner, member = _ctx(world, OWNER, "OWNER"), _ctx(world, MEMBER, "OWNER")      # ctx.role'e guvenilmez
    with pytest.raises(wm.AdvanceNotAllowed, match="yalnızca dünyanın sahibi ya da yöneticisi"):
        wm.try_advance(member, AdvanceTrigger.FORCED, expected=(1, 1))
    assert _state_tuple() == (1, 1) and _count(WorldEvent) == 0

    not_ready = wm.try_advance(member, AdvanceTrigger.READY, expected=(1, 1))
    assert not not_ready.advanced and not_ready.message == "2 menajer daha hazır değil." and _state_tuple() == (1, 1)

    result = wm.try_advance(owner, AdvanceTrigger.FORCED, expected=(1, 1))
    assert result.advanced and result.trigger is AdvanceTrigger.FORCED and (result.season, result.week) == (1, 2)
    state = _read(lambda db: db.get(GameState, 1))
    assert state.last_advance_trigger == "FORCED" and state.last_advance_by == world.user_ids[OWNER]
    event = _read(lambda db: db.scalar(select(WorldEvent).where(WorldEvent.kind == "ADVANCE")))
    assert event.actor_manager_id == world.seat_ids[OWNER] and (event.season, event.week) == (1, 1)
    assert event.payload["from"] == [1, 1] and event.payload["to"] == [1, 2]
    # tur ilk kez acilmadan zorlandi: kimse kacirmis sayilmaz
    assert _count(WorldManager, WorldManager.missed_deadlines > 0) == 0

    stale = wm.try_advance(owner, AdvanceTrigger.FORCED, expected=(1, 1))
    assert not stale.advanced and stale.kind == "NONE" and _state_tuple() == (1, 2)


def test_deadline_advances_through_maybe_advance_due_and_counts_missed_turns(world):
    owner = _ctx(world, OWNER, "OWNER")
    now = datetime.now(timezone.utc)
    _open_turn(now - timedelta(hours=30))                   # bitis: 6 saat once
    _set_rules(auto_advance=False)
    assert wm.maybe_advance_due(owner, now) is None          # otomatik ilerleme kapali
    _set_rules(auto_advance=True)
    assert wm.maybe_advance_due(owner, now - timedelta(hours=7)) is None       # sure henuz dolmadi
    assert _state_tuple() == (1, 1)

    _ready(owner)                                            # sahip hazir, uye hic gelmedi
    result = wm.maybe_advance_due(owner, now)
    assert result is not None and result.advanced and result.trigger is AdvanceTrigger.DEADLINE
    assert (result.season, result.week) == (1, 2)
    state = _read(lambda db: db.get(GameState, 1))
    assert state.last_advance_trigger == "DEADLINE" and state.last_advance_by is None
    assert state.turn_opened_at == now and state.turn_deadline_at == now + timedelta(hours=24)
    missed = _read(lambda db: dict(db.execute(select(WorldManager.display_name, WorldManager.missed_deadlines)).all()))
    assert missed == {OWNER: 0, MEMBER: 1, SPARE: 0}         # kulupsuz koltuk sayilmaz
    assert wm.maybe_advance_due(owner, now + timedelta(hours=1)) is None       # yeni tur henuz bitmedi


def test_advance_while_a_callback_holds_the_shared_lock_is_busy_and_writes_nothing(world):
    owner, member = _ctx(world, OWNER, "OWNER"), _ctx(world, MEMBER)
    _ready(owner)
    _ready(member)
    now = datetime.now(timezone.utc)
    _open_turn(now - timedelta(hours=48))
    before = _read(lambda db: (db.get(GameState, 1).turn_opened_at,
                               db.scalar(select(func.count()).select_from(Fixture)
                                         .where(Fixture.status == FixtureStatus.PLAYED))))

    with _LockHolder("pg_advisory_xact_lock_shared"):
        started = time.monotonic()
        with pytest.raises(wm.AdvanceInProgress, match="birkaç saniye sonra"):
            wm.try_advance(owner, AdvanceTrigger.FORCED, expected=(1, 1))
        assert time.monotonic() - started < 3                      # beklemez
        assert wm.maybe_advance_due(owner, now) is None            # sayfa acilisi: mesgul -> None

    after = _read(lambda db: (db.get(GameState, 1).turn_opened_at,
                              db.scalar(select(func.count()).select_from(Fixture)
                                        .where(Fixture.status == FixtureStatus.PLAYED))))
    assert _state_tuple() == (1, 1) and after == before and before[1] == 0
    assert _count(WorldEvent) == 0 and _count(ManagerWeekReport) == 0
    assert _count(WorldManager, WorldManager.ready_career_week.is_not(None)) == 2          # hazirlar korunur


def test_cli_tick_waits_boundedly_for_running_callbacks(world, monkeypatch):
    owner = _ctx(world, OWNER, "OWNER")
    now = datetime.now(timezone.utc)
    _open_turn(now - timedelta(hours=48))
    monkeypatch.setenv(database.WORLD_LOCK_TIMEOUT_ENV, "300")
    with _LockHolder("pg_advisory_xact_lock_shared"):                # callback bitmiyor: bekleme asilir
        started = time.monotonic()
        with pytest.raises(wm.AdvanceInProgress):
            wm.try_advance(owner, AdvanceTrigger.DEADLINE, expected=(1, 1), now=now, wait=True)
        assert 0.2 < time.monotonic() - started < 5
        assert wm.advance_due_worlds(now, schema=SCHEMA) == []
    assert _state_tuple() == (1, 1) and _count(WorldEvent) == 0

    monkeypatch.setenv(database.WORLD_LOCK_TIMEOUT_ENV, "15000")
    holder = _LockHolder("pg_advisory_xact_lock_shared")
    with holder:
        threading.Timer(0.8, holder.released.set).start()            # callback kisa surede biter
        started = time.monotonic()
        results = wm.advance_due_worlds(now, schema=SCHEMA)
    assert time.monotonic() - started > 0.5
    assert len(results) == 1 and results[0].advanced and _state_tuple() == (1, 2)


def test_callback_during_an_advance_waits_and_then_sees_the_new_week(world, monkeypatch):
    owner = _ctx(world, OWNER, "OWNER")
    inside = threading.Event()
    original = wm._play_turn

    def slow_play_turn(cm, facts):
        inside.set()
        time.sleep(1.0)
        return original(cm, facts)

    monkeypatch.setattr(wm, "_play_turn", slow_play_turn)
    outcome: dict = {}

    def advance():
        try:
            outcome["result"] = wm.try_advance(owner, AdvanceTrigger.FORCED, expected=(1, 1))
        except Exception as exc:                     # pragma: no cover - testte gorunsun
            outcome["error"] = exc
        outcome["done_at"] = time.monotonic()

    worker = threading.Thread(target=advance)
    worker.start()
    assert inside.wait(20)
    waited_from = time.monotonic()
    with _controller(_ctx(world, MEMBER)) as wc:                 # SHARED kilit: ilerleme bitene kadar bekler
        status = wc.turn_status()                                # ilk sorgu kilidi alir
        seen_at = time.monotonic()
    worker.join(60)
    assert "error" not in outcome and outcome["result"].advanced
    assert status.week == 2                                      # yarim hafta degil, commit edilmis yeni hafta
    assert seen_at - waited_from > 0.5 and seen_at >= outcome["done_at"] - 0.5


def test_inactive_manager_loses_the_club_which_stays_protected(world):
    owner, member, spare = _ctx(world, OWNER, "OWNER"), _ctx(world, MEMBER), _ctx(world, SPARE)
    _set_rules(max_missed_deadlines=1, protection_weeks=2, club_offers_by_level=False)
    member_team = _team_id(MEMBER_TEAM)
    now = datetime.now(timezone.utc)
    _open_turn(now - timedelta(hours=100))

    _ready(owner)
    first = wm.maybe_advance_due(owner, now - timedelta(hours=75))
    assert first is not None and first.released == []
    assert _read(lambda db: db.get(WorldManager, world.seat_ids[MEMBER]).missed_deadlines) == 1

    _ready(owner)
    second = wm.maybe_advance_due(owner, now - timedelta(hours=50))
    assert second is not None and second.released == [MEMBER] and "kulübünü kaybeden" in second.message
    career_week = _career_week()
    seat = _read(lambda db: db.get(WorldManager, world.seat_ids[MEMBER]))
    assert (seat.status, seat.team_id, seat.missed_deadlines) == ("RELEASED", None, 0)
    assert _read(lambda db: db.get(Team, member_team).ai_protected_until) == career_week + 2
    event = _read(lambda db: db.scalar(select(WorldEvent).where(WorldEvent.kind == "RELEASE")))
    assert event.payload["reason"] == "INACTIVE" and event.payload["team_id"] == member_team
    assert _read(lambda db: db.get(WorldManager, world.seat_ids[OWNER]).missed_deadlines) == 0

    with _controller(member) as wc:                              # kaybettigi kulubu koruma surerken geri alamaz
        offer = next(o for o in wc.club_offers(only_eligible=False) if o.team_id == member_team)
        assert offer.protected and not offer.eligible and "geri alamazsın" in offer.reason
        assert member_team not in {o.team_id for o in wc.club_offers()}
        with pytest.raises(wm.ClubUnavailableError, match="geri alamazsın"):
            wc.claim_club(member_team)
    with _controller(spare) as wc:                               # baska menajer alabilir; koruma kalkar
        assert next(o for o in wc.club_offers() if o.team_id == member_team).protected
        claimed = wc.claim_club(member_team)
        assert claimed.team_id == member_team
    assert _read(lambda db: db.get(Team, member_team).ai_protected_until) is None


def test_season_end_advance_starts_the_new_season_and_clears_ready_flags(world):
    owner = _ctx(world, OWNER, "OWNER")
    _set_rules(max_missed_deadlines=20)                          # zorlanan haftalarda kimse kulubunu kaybetmesin
    kinds = []
    for _ in range(20):
        result = wm.try_advance(owner, AdvanceTrigger.FORCED, expected=_state_tuple())
        assert result.advanced
        kinds.append(result.kind)
        if result.kind == "NEW_SEASON":
            break
        with _controller(owner) as wc:
            status = wc.turn_status()
            if status.phase == "SEASON_END":
                _ready(owner)                                   # sezon sonu turunda hazir
                assert wc.turn_status().me_ready
    assert kinds[-1] == "NEW_SEASON" and set(kinds[:-1]) == {"WEEK"} and len(kinds) >= 3
    last_week = len(kinds)                                       # sezon sonu turu = oynanmamis son hafta
    assert (result.season, result.week) == (2, 1) and result.message == "Sezon 2 başladı!"
    with _controller(owner) as wc:
        status = wc.turn_status()
        assert (status.season, status.week, status.phase) == (2, 1, "SEASON")
        assert not status.me_ready                               # mutlak hafta ayni kalsa da hazir temizlendi
        report = wc.my_reports(1)[0]
        assert (report.season, report.week) == (1, last_week) and report.lines[0] == ("season", "Sezon 2 başladı!")
    assert _count(Fixture, Fixture.season == 2) > 0


# ===========================================================================
# Kontrolcu: hazir, kulup, yonetici islemleri
# ===========================================================================

def test_set_ready_rejects_stale_weeks_and_clubless_seats(world):
    member, spare = _ctx(world, MEMBER), _ctx(world, SPARE)
    with _controller(member) as wc:
        with pytest.raises(wm.StaleTurnError):
            wc.set_ready(True, wc.career_week + 1)
    with _controller(spare) as wc:
        with pytest.raises(wm.TurnError, match="Kulübü olmayan"):
            wc.set_ready(True, wc.career_week)
    with _controller(member) as wc:
        status = wc.set_ready(True, wc.career_week)
        assert status.me_ready and status.ready_names == [MEMBER] and status.can_ready and not status.can_force
        assert status.seconds_left is not None and 0 < status.seconds_left <= 24 * 3600
        status = wc.set_ready(False, wc.career_week)
        assert not status.me_ready and status.ready == 0
    stranger = WorldContext(world.world_id, SCHEMA, "x", "SHARED", "MEMBER", 999_999, None)
    with _controller(stranger) as wc:
        with pytest.raises(worlds.NotAMemberError):
            wc.set_ready(True, wc.career_week)
        wc.touch()                                              # koltuksuz: sessizce hicbir sey


def test_claim_and_release_club_follow_level_and_ownership_rules(world):
    spare = _ctx(world, SPARE)
    small, big, owner_team = _team_id(SMALL_CLUB), _team_id(BIG_CLUB), _team_id(OWNER_TEAM)
    with _controller(spare) as wc:
        offers = wc.club_offers()
        ids = {o.team_id for o in offers}
        assert small in ids and big not in ids and owner_team not in ids and _team_id(MEMBER_TEAM) not in ids
        assert all(o.eligible and o.squad_rating > 0 and o.stars > 0 for o in offers)
        locked = next(o for o in wc.club_offers(only_eligible=False) if o.team_id == big)
        assert not locked.eligible and "10. seviye" in locked.reason
        assert [o.team_name for o in wc.club_offers("bosphorus")] == [SMALL_CLUB]
        with pytest.raises(wm.ClubUnavailableError, match="başka bir menajer"):
            wc.claim_club(owner_team)
        with pytest.raises(wm.ClubUnavailableError, match="seviye"):
            wc.claim_club(big)
        with pytest.raises(wm.ClubUnavailableError, match="bulunamadı"):
            wc.claim_club(987_654)
        seat = wc.claim_club(small)
        assert seat.team_id == small and seat.status == "ACTIVE"
        with pytest.raises(wm.ClubUnavailableError, match="Zaten"):
            wc.claim_club(_team_id("Karadeniz Storm"))
    assert _read(lambda db: db.scalar(select(WorldMembership.team_name_cache).where(
        WorldMembership.user_id == world.user_ids[SPARE]))) == SMALL_CLUB
    assert _read(lambda db: db.get(WorldManager, world.seat_ids[SPARE]).last_active_at) is not None

    with _controller(spare) as wc:
        wc.release_club()
        with pytest.raises(wm.ClubUnavailableError, match="Yönettiğin bir kulüp yok"):
            wc.release_club()
    career_week = _career_week()
    assert _read(lambda db: db.get(Team, small).ai_protected_until) == career_week + 4
    assert _read(lambda db: db.get(WorldManager, world.seat_ids[SPARE]).status) == "RELEASED"
    with _controller(spare) as wc:                               # gonullu birakan geri alabilir
        assert wc.claim_club(small).team_id == small
        kinds = [e.kind for e in wc.events()]
        assert kinds == ["CLAIM", "RELEASE", "CLAIM"] and wc.events()[0].text.endswith("başına geçti.")


def test_admin_actions_kick_and_rules_are_owner_or_admin_only(world):
    owner, member = _ctx(world, OWNER, "OWNER"), _ctx(world, MEMBER, "OWNER")
    member_team = _team_id(MEMBER_TEAM)
    with _controller(member) as wc:
        assert not wc.can_draw_cup() and not wc.is_admin()
        with pytest.raises(worlds.WorldPermissionError):
            wc.kick(world.seat_ids[SPARE], "deneme")
        with pytest.raises(worlds.WorldPermissionError):
            wc.update_rules({"deadline_hours": 48})

    with _controller(owner) as wc:
        assert wc.can_draw_cup() and wc.role() == "OWNER"
        rows = wc.managers()                                     # birincil once, sonra ada gore
        assert [r.name for r in rows] == [OWNER, SPARE, MEMBER]
        assert [r.role for r in rows] == ["OWNER", "MEMBER", "MEMBER"]
        assert rows[0].team_name == OWNER_TEAM and rows[1].team_name is None and rows[2].team_name == MEMBER_TEAM
        assert rows[0].level_title and rows[2].reputation == 8.0 and not any(r.ready for r in rows)
        with pytest.raises(worlds.WorldPermissionError, match="sahibi"):
            wc.kick(world.seat_ids[OWNER], "")
        with pytest.raises(worlds.WorldNotFound):
            wc.kick(123_456, "")

        rules = wc.update_rules({"deadline_hours": "48", "win_points": 2})
        assert rules.deadline_hours == 48 and rules.win_points == 2
        state = wc.state
        assert state.turn_opened_at is None                       # tur acilmamis: bitis hesaplanmaz
        with pytest.raises(RulesError, match="Bilinmeyen"):
            wc.update_rules({"turbo": 1})
        with pytest.raises(RulesError, match="en az 2 menajer"):
            wc.update_rules({"max_seats": 1})
        with pytest.raises(RulesError, match=r"menajer sayısından \(3\)"):
            wc.update_rules({"max_seats": 2})
        assert wc.update_rules({"max_seats": 12}).max_seats == 12
        assert wc.update_rules({}) == wc.rules                     # degisiklik yok: kayit yok

    assert _read(lambda db: db.scalar(select(World.max_managers).where(World.id == world.world_id))) == 12
    assert _count(WorldEvent, WorldEvent.kind == "RULES") == 2
    rules_event = _read(lambda db: db.scalar(select(WorldEvent).where(WorldEvent.kind == "RULES")
                                             .order_by(WorldEvent.id)))
    assert rules_event.payload["changes"] == {"deadline_hours": [24, 48], "win_points": [3, 2]}

    wm.try_advance(owner, AdvanceTrigger.FORCED, expected=(1, 1))
    with _controller(owner) as wc:
        with pytest.raises(RulesError, match="oyun kuralı"):
            wc.update_rules({"win_points": 3})
        opened = wc.state.turn_opened_at
        assert wc.update_rules({"deadline_hours": 6}).deadline_hours == 6
        assert wc.state.turn_deadline_at == opened + timedelta(hours=6)
        wc.update_rules({"auto_advance": False})                   # otomatik ilerleme yoksa tur bitisi de yok
        assert wc.state.turn_deadline_at is None and wc.turn_status().seconds_left is None
        wc.update_rules({"auto_advance": True})
        assert wc.state.turn_deadline_at == opened + timedelta(hours=6)

        wc.kick(world.seat_ids[MEMBER], "  kurallara   uymadı ")
    career_week = _career_week()
    seat = _read(lambda db: db.get(WorldManager, world.seat_ids[MEMBER]))
    assert (seat.status, seat.team_id) == ("KICKED", None)
    assert _read(lambda db: db.get(Team, member_team).ai_protected_until) == career_week + 4
    membership = _read(lambda db: db.scalar(select(WorldMembership.status).where(
        WorldMembership.user_id == world.user_ids[MEMBER])))
    assert membership == "KICKED"
    kick = _read(lambda db: db.scalar(select(WorldEvent).where(WorldEvent.kind == "KICK")))
    assert kick.payload["reason"] == "kurallara uymadı" and kick.actor_manager_id == world.seat_ids[OWNER]
    with _controller(owner) as wc:
        with pytest.raises(worlds.WorldError, match="zaten"):
            wc.kick(world.seat_ids[MEMBER], "")
        assert [r.name for r in wc.managers()] == [OWNER, SPARE]


# ===========================================================================
# CLI: world-tick / world-list
# ===========================================================================

def test_world_tick_advances_only_due_worlds(world, capsys):
    import main

    owner = _ctx(world, OWNER, "OWNER")
    extra: list[int] = []
    database.init_accounts()
    with database.engine.begin() as conn:
        for name, schema, kind, status in (("Hayalet", "test_a3_ghost", "SHARED", "ACTIVE"),
                                           ("Arsiv", "test_a3_archived", "SHARED", "ARCHIVED"),
                                           ("Kisisel", "test_a3_personal", "PERSONAL", "ACTIVE")):
            extra.append(conn.execute(text(
                'INSERT INTO "accounts".worlds (name, schema_name, kind, visibility, max_managers, status) '
                "VALUES (:n, :s, :k, 'PRIVATE', 2, :st) RETURNING id"), {"n": name, "s": schema, "k": kind, "st": status}
            ).scalar_one())
    try:
        contexts = wm.shared_world_contexts()
        assert [c.world_id for c in contexts] == sorted([world.world_id, extra[0]])
        assert all(c.role == wm.SYSTEM_ROLE and c.user_id == 0 for c in contexts)

        now = datetime.now(timezone.utc)
        _open_turn(now - timedelta(hours=1))                     # bitis 23 saat sonra: vakti gelmedi
        assert wm.advance_due_worlds(now) == []                  # hayalet sema hatasi loglanir, atlanir
        assert main.main(["world-tick", "--all"]) == 0
        assert "İlerlemesi gereken dünya yok." in capsys.readouterr().out
        assert _state_tuple() == (1, 1)

        assert main.main(["world-list"]) == 0
        listing = capsys.readouterr().out
        assert "Test Dünyası" in listing and "S1 H1" in listing and "0/2" in listing and "[HATA]" in listing

        _open_turn(now - timedelta(hours=30))                    # suresi doldu
        assert wm.advance_due_worlds(now, world_ids=[extra[0]]) == []
        results = wm.advance_due_worlds(now, schema=SCHEMA)
        assert len(results) == 1 and results[0].world_id == world.world_id and results[0].week == 2
        assert results[0].trigger is AdvanceTrigger.DEADLINE

        _open_turn(datetime.now(timezone.utc) - timedelta(hours=30))
        assert main.main(["world-tick", "--world", str(world.world_id)]) == 0
        out = capsys.readouterr().out
        assert f"Dünya #{world.world_id}: Sezon 1, 2. hafta oynandı." in out and "tetik: DEADLINE" in out
        assert _state_tuple() == (1, 3)
        assert wm.maybe_advance_due(owner) is None               # yeni tur yeni acildi
    finally:
        with database.engine.begin() as conn:
            conn.execute(text('DELETE FROM "accounts".worlds WHERE id = ANY(:ids)'), {"ids": extra})
