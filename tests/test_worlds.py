"""
Dunya kaydi (worlds.py, Faz 12 / 14. Asama -- A1) entegrasyon testleri.

Kendi test veritabaninda calistirilir (conftest olusturur ve sentetik 'public' dunyasini kurar):
    TEST_DB_NAME=fm_db_test_a1 python -m pytest -q -p no:cacheprovider tests/test_worlds.py

Modul tek bir gercek paylasilan dunya kurar (create_world, sentetik seed) ve testler arasinda kayit/koltuk
satirlarini sifirlar. 'public' oyun verisine yazilmaz; yalnizca 'public' kaydi (accounts.worlds) kullanilir.
Temizlik (modul basinda ve sonunda): 'a1_' kullanicilari, world_% semalari ve bu testlerin kayit satirlari.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _db_available() -> bool:
    if os.getenv("CM_TEST_NO_DB"):
        return False
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

PASSWORD = "Gizli.Parola42"
USER_PREFIX = "a1_"
SCRATCH_SCHEMAS = ("career_a1_orphan", "career_a1_stale", "career_a1_moved", "career_a1_conv", "career_a1_enter",
                   "career_a1_leave", "career_a1_real")
_names = itertools.count(1)


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _assert_test_database() -> None:
    import database

    expected = os.getenv("TEST_DB_NAME", "fm_db_test")
    actual = database.engine.url.database
    if actual != expected or actual == os.getenv("DB_NAME", "fm_db"):
        raise RuntimeError(f"Dünya testleri yalnızca test veritabanında çalışır (bağlantı: {actual}).")


def _sql(statement: str, **params):
    from sqlalchemy import text

    import database

    with database.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return result.all() if result.returns_rows else result.rowcount


def _scalar(statement: str, **params):
    rows = _sql(statement, **params)
    return rows[0][0] if rows else None


def _schema_exists(schema: str) -> bool:
    return bool(_scalar("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)", s=schema))


def _world_schemas() -> set[str]:
    return {row[0] for row in _sql(r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 'world\_%'")}


def _cleanup() -> None:
    import database

    _assert_test_database()
    database.init_accounts()
    for schema in _world_schemas() | {s for s in SCRATCH_SCHEMAS if _schema_exists(s)}:
        database.drop_career_schema(schema)
    career_schemas = [row[0] for row in _sql(
        "SELECT career_schema FROM accounts.users WHERE username LIKE :p AND career_schema LIKE 'career\\_%'",
        p=f"{USER_PREFIX}%")]
    for schema in career_schemas:
        if _schema_exists(schema):
            database.drop_career_schema(schema)
    _sql(r"DELETE FROM accounts.worlds WHERE schema_name LIKE 'world\_%' OR schema_name = ANY(:s) "
         "OR schema_name = 'public' OR schema_name = ANY(:c)", s=list(SCRATCH_SCHEMAS), c=career_schemas)
    _sql("DELETE FROM accounts.users WHERE username LIKE :p", p=f"{USER_PREFIX}%")


def _user(label: str = "menajer", career_schema: str | None = None) -> SimpleNamespace:
    import auth

    name = f"{USER_PREFIX}{label}{next(_names)}"[:32]
    uid = _scalar("INSERT INTO accounts.users (username, password_hash, career_schema) VALUES (:u, :h, :s) "
                  "RETURNING id", u=name, h=auth.hash_password(PASSWORD) if label == "giris" else "scrypt$x",
                  s=career_schema)
    return SimpleNamespace(id=uid, name=name)


def _membership(world_id: int, user_id: int):
    rows = _sql("SELECT role, status, last_seen_at, left_at FROM accounts.world_memberships "
                "WHERE world_id = :w AND user_id = :u", w=world_id, u=user_id)
    return rows[0] if rows else None


def _seat(schema: str, user_id: int):
    rows = _sql(f'SELECT id, is_primary, team_id, reputation, status, joined_career_week FROM "{schema}".world_managers '
                "WHERE user_id = :u", u=user_id)
    return rows[0] if rows else None


def _code(world_id: int) -> str | None:
    return _scalar("SELECT invite_code FROM accounts.worlds WHERE id = :w", w=world_id)


def _career_week(schema: str) -> int:
    return int(_scalar(f'SELECT career_week_offset + current_week FROM "{schema}".game_state WHERE id = 1'))


def _set_profile(user_id: int, rep: float) -> None:
    _sql("INSERT INTO accounts.manager_profiles (user_id, reputation) VALUES (:u, :r) "
         "ON CONFLICT (user_id) DO UPDATE SET reputation = EXCLUDED.reputation", u=user_id, r=rep)


# ---------------------------------------------------------------------------
# Ortak paylasilan dunya
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def world():
    import worlds
    from world_rules import WorldRules

    _cleanup()
    owner = _user("sahip")
    ctx = worlds.create_world(owner.id, "  Test   Dünyası 1 ", visibility="INVITE", max_managers=3,
                              min_manager_level=1, rules=WorldRules.shared_defaults(), world_seed=99,
                              source="synthetic")
    try:
        yield SimpleNamespace(ctx=ctx, owner=owner, schema=ctx.schema, id=ctx.world_id)
    finally:
        _cleanup()


@pytest.fixture
def shared(world):
    """Her test temiz dunya kaydiyla baslar: yalnizca sahip uye, koltuk yalnizca birincil."""
    _sql("DELETE FROM accounts.world_memberships WHERE world_id = :w AND user_id <> :o", w=world.id, o=world.owner.id)
    _sql("UPDATE accounts.world_memberships SET role = 'OWNER', status = 'ACTIVE', left_at = NULL "
         "WHERE world_id = :w", w=world.id)
    _sql("UPDATE accounts.worlds SET max_managers = 3, min_manager_level = 1, visibility = 'INVITE', "
         "status = 'ACTIVE', kind = 'SHARED', name = 'Test Dünyası 1', "
         "invite_code = COALESCE(invite_code, 'A1KODX23') WHERE id = :w", w=world.id)
    _sql(f'DELETE FROM "{world.schema}".world_managers WHERE NOT is_primary')
    _sql(f'DELETE FROM "{world.schema}".world_events')
    _sql(f'UPDATE "{world.schema}".teams SET ai_protected_until = NULL')
    _sql(f'UPDATE "{world.schema}".game_state SET user_team_id = NULL WHERE id = 1')
    return world


# ---------------------------------------------------------------------------
# Oturum alanlari, register / authenticate
# ---------------------------------------------------------------------------

def test_auth_session_world_fields_default_and_register_authenticate_unchanged(world, monkeypatch):
    import accounts
    import database
    import seed
    import worlds
    from models import GameState

    plain = accounts.AuthSession(5, "x", "public")
    assert (plain.world_id, plain.world_kind) == (None, None)
    assert plain == accounts.AuthSession(user_id=5, username="x", career_schema="public", world_id=None,
                                         world_kind=None)

    if _scalar("SELECT count(*) FROM accounts.users WHERE career_schema = 'public'") == 0:
        _user("public_sahibi", career_schema="public")            # yeni kayit public'i devralmasin

    def fake_seed(rng_seed, source="auto", **_kwargs):
        with database.session_scope() as db:
            db.add(GameState(id=1, season=1, current_week=1))

    monkeypatch.setattr(seed, "seed", fake_seed)
    registered = accounts.register(f"{USER_PREFIX}kayit", PASSWORD, source="synthetic")
    assert registered == accounts.AuthSession(registered.user_id, f"{USER_PREFIX}kayit", registered.career_schema)
    assert (registered.world_id, registered.world_kind) == (None, None)
    assert registered.career_schema == f"career_{registered.user_id}"
    logged_in = accounts.authenticate(f"{USER_PREFIX.upper()}KAYIT", PASSWORD)
    assert logged_in == registered and logged_in.world_id is None

    # Web girisi dunyayi session_for ile baglar; diger alanlar aynen kalir, baskasinin baglami reddedilir
    ctx = worlds.default_world(logged_in)
    assert (ctx.kind, ctx.role, ctx.schema, ctx.user_id) == ("PERSONAL", "OWNER", registered.career_schema,
                                                              registered.user_id)
    bound = worlds.session_for(logged_in, ctx)
    assert dataclasses.replace(bound, world_id=None, world_kind=None) == logged_in
    assert (bound.world_id, bound.world_kind, bound.career_schema) == (ctx.world_id, "PERSONAL", ctx.schema)
    with pytest.raises(worlds.WorldPermissionError):
        worlds.session_for(accounts.AuthSession(world.owner.id, world.owner.name, "public"), ctx)


# ---------------------------------------------------------------------------
# Kisisel dunya doldurma
# ---------------------------------------------------------------------------

def test_personal_world_backfill_is_idempotent_and_repairs_orphans(world):
    import accounts
    import worlds

    owner = _user("kisisel", career_schema="career_a1_orphan")
    session = accounts.AuthSession(owner.id, owner.name, "career_a1_orphan")
    first = worlds.ensure_personal_world(session)
    assert (first.kind, first.visibility, first.max_managers, first.owner_user_id, first.my_role) == (
        "PERSONAL", "PRIVATE", 1, owner.id, "OWNER")
    assert first.name == worlds.PERSONAL_WORLD_NAME and first.invite_code is None and first.active_managers == 1

    # Es zamanli girisler: tek kayit, tek uyelik
    results: list = []
    barrier = threading.Barrier(4)

    def ensure():
        barrier.wait()
        try:
            results.append(worlds.ensure_personal_world(session))
        except Exception as exc:                      # pragma: no cover - hata raporu icin
            results.append(exc)

    threads = [threading.Thread(target=ensure) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 4 and all(isinstance(r, worlds.WorldInfo) and r.id == first.id for r in results), results
    assert _scalar("SELECT count(*) FROM accounts.worlds WHERE schema_name = 'career_a1_orphan'") == 1
    assert _scalar("SELECT count(*) FROM accounts.world_memberships WHERE world_id = :w", w=first.id) == 1

    # Hesap silinir (test temizligi): kayit sahipsiz kalir; semayi alan yeni hesap sahipligi onarir
    _sql("DELETE FROM accounts.users WHERE id = :u", u=owner.id)
    assert _scalar("SELECT owner_user_id FROM accounts.worlds WHERE id = :w", w=first.id) is None
    heir = _user("varis", career_schema="career_a1_orphan")
    repaired = worlds.ensure_personal_world(accounts.AuthSession(heir.id, heir.name, "career_a1_orphan"))
    assert (repaired.id, repaired.owner_user_id, repaired.my_role, repaired.owner_name) == (
        first.id, heir.id, "OWNER", heir.name)

    # Bayat sahip: eski sahibin kariyeri artik baska semada -> guncel sahibe onarilir, eski OWNER uyeligi duser
    stale = _user("bayat", career_schema="career_a1_stale")
    stale_info = worlds.ensure_personal_world(accounts.AuthSession(stale.id, stale.name, "career_a1_stale"))
    _sql("UPDATE accounts.users SET career_schema = 'career_a1_moved' WHERE id = :u", u=stale.id)
    newcomer = _user("yeni", career_schema="career_a1_stale")
    moved = worlds.ensure_personal_world(accounts.AuthSession(newcomer.id, newcomer.name, "career_a1_stale"))
    assert (moved.id, moved.owner_user_id, moved.my_role) == (stale_info.id, newcomer.id, "OWNER")
    assert tuple(_membership(stale_info.id, stale.id)[:2]) == ("MEMBER", "LEFT")

    # Sahipsiz 'public' kaydi (web testlerinin sahte oturumu) ve onu devralan hesap
    if _scalar("SELECT count(*) FROM accounts.users WHERE career_schema = 'public'"):
        _sql("UPDATE accounts.users SET career_schema = NULL WHERE career_schema = 'public' AND username LIKE :p",
             p=f"{USER_PREFIX}%")
    if _scalar("SELECT count(*) FROM accounts.users WHERE career_schema = 'public'") == 0:
        _sql("DELETE FROM accounts.worlds WHERE schema_name = 'public'")
        fake = worlds.ensure_personal_world(accounts.AuthSession(0, "test_menajer", "public"))
        assert (fake.schema, fake.owner_user_id, fake.my_role) == ("public", None, None)
        with pytest.raises(worlds.NotAMemberError):
            worlds.default_world(accounts.AuthSession(0, "test_menajer", "public"))
        claimer = _user("devralan", career_schema="public")
        claimed = worlds.ensure_personal_world(accounts.AuthSession(claimer.id, claimer.name, "public"))
        assert (claimed.id, claimed.owner_user_id, claimed.my_role) == (fake.id, claimer.id, "OWNER")
        assert [w.id for w in worlds.list_my_worlds(claimer.id)] == [fake.id]
        _sql("DELETE FROM accounts.worlds WHERE schema_name = 'public'")
        _sql("DELETE FROM accounts.users WHERE id = :u", u=claimer.id)


# ---------------------------------------------------------------------------
# Dunya kurma
# ---------------------------------------------------------------------------

def test_create_world_provisions_schema_owner_primary_seat_and_rules(shared):
    import accounts
    import database
    import worlds
    from models import GameMode, GameState, Team, WorldManager
    from world_rules import WorldRules

    ctx = shared.ctx
    assert ctx.schema == f"world_{ctx.world_id}" and _schema_exists(ctx.schema)
    assert (ctx.name, ctx.kind, ctx.role, ctx.user_id, ctx.world_seed) == (
        "Test Dünyası 1", "SHARED", "OWNER", shared.owner.id, 99)

    with database.career_context(ctx.schema):
        assert database.schema_problems() == []
        with database.session_scope() as db:
            state = db.get(GameState, 1)
            assert state.user_id == shared.owner.id and state.game_mode is GameMode.CAREER
            assert state.world_rules == dataclasses.replace(WorldRules.shared_defaults(), max_seats=3).to_dict()
            assert WorldRules.from_dict(state.world_rules).shared and state.turn_opened_at is not None
            assert state.manager_reputation == 8.0 and state.user_team_id is None
            primary = db.query(WorldManager).one()
            assert primary.is_primary and primary.user_id == shared.owner.id
            assert primary.team_id is None and primary.reputation is None and primary.status == "ACTIVE"
            assert db.query(Team).count() >= 8

    row = _sql("SELECT kind, visibility, invite_code, max_managers, min_manager_level, status, schema_version, "
               "world_seed, owner_user_id FROM accounts.worlds WHERE id = :w", w=ctx.world_id)[0]
    assert (row.kind, row.visibility, row.max_managers, row.min_manager_level, row.status) == (
        "SHARED", "INVITE", 3, 1, "ACTIVE")
    assert row.schema_version == database.SCHEMA_VERSION and row.world_seed == 99
    assert row.owner_user_id == shared.owner.id
    assert len(row.invite_code) == worlds.INVITE_CODE_LENGTH and set(row.invite_code) <= set(worlds.INVITE_ALPHABET)
    assert tuple(_membership(ctx.world_id, shared.owner.id)[:2]) == ("OWNER", "ACTIVE")

    mine = worlds.list_my_worlds(shared.owner.id)
    assert [w.id for w in mine] == [ctx.world_id]
    assert mine[0].invite_code == row.invite_code and mine[0].active_managers == 1 and mine[0].my_role == "OWNER"
    bound = accounts.AuthSession(shared.owner.id, shared.owner.name, ctx.schema, world_id=ctx.world_id,
                                 world_kind="SHARED")
    assert worlds.default_world(bound) == ctx


def test_create_world_validation_and_failed_seed_leave_no_trace(shared, monkeypatch):
    import seed
    import worlds
    from world_rules import WorldRules

    owner = _user("kurucu")
    schemas_before = _world_schemas()
    worlds_before = _scalar("SELECT count(*) FROM accounts.worlds")
    good = {"visibility": "PUBLIC", "max_managers": 4, "min_manager_level": 1, "rules": WorldRules.shared_defaults(),
            "source": "synthetic"}
    bad_calls = [
        ("", {}), ("x" * 41, {}), ("<script>alert(1)</script>", {}), ("Dünya; DROP", {}), ("Dünya²", {}),
        ("Geçerli", {"visibility": "SECRET"}), ("Geçerli", {"max_managers": 1}), ("Geçerli", {"max_managers": 65}),
        ("Geçerli", {"max_managers": True}), ("Geçerli", {"min_manager_level": 0}),
        ("Geçerli", {"min_manager_level": 11}), ("Geçerli", {"rules": {"shared": True}}),
        ("Geçerli", {"world_seed": -1}),
        ("Geçerli", {"rules": dataclasses.replace(WorldRules.shared_defaults(), ready_check=False,
                                                  auto_advance=False)}),
    ]
    for name, override in bad_calls:
        with pytest.raises(worlds.WorldError):
            worlds.create_world(owner.id, name, **{**good, **override})
    with pytest.raises(worlds.WorldError, match="hesabı bulunamadı"):
        worlds.create_world(987654321, "Sahipsiz", **good)
    assert worlds.clean_world_name("  Süper   Lig-2 v1.0_a ") == "Süper Lig-2 v1.0_a"

    def broken_seed(rng_seed, source="auto", **_kwargs):
        raise RuntimeError("disk dolu")

    monkeypatch.setattr(seed, "seed", broken_seed)
    with pytest.raises(worlds.WorldError) as err:
        worlds.create_world(owner.id, "Kurulamayan", **good)
    assert "Dünya kurulamadı" in str(err.value) and "disk" not in str(err.value)
    assert _world_schemas() == schemas_before
    assert _scalar("SELECT count(*) FROM accounts.worlds") == worlds_before


# ---------------------------------------------------------------------------
# Katilim
# ---------------------------------------------------------------------------

def test_join_by_code_generic_error_levels_capacity_and_seat(shared):
    import worlds
    from world_rules import WorldRules

    code = _code(shared.id)
    member = _user("uye")
    _set_profile(member.id, 12.3)

    messages = set()
    for bad in ("", None, "ZZZZZZZZ", "12", "x" * 50, code[:-1] + ("A" if code[-1] != "A" else "B"), "';--"):
        with pytest.raises(worlds.WorldNotFound) as err:
            worlds.join_by_code(member.id, bad)
        messages.add(str(err.value))
    # Ozel dunyada kalmis kod, arsivlenmis dunya: ayni genel hata (kod tahmini sizmaz)
    _sql("UPDATE accounts.worlds SET visibility = 'PRIVATE' WHERE id = :w", w=shared.id)
    with pytest.raises(worlds.WorldNotFound) as err:
        worlds.join_by_code(member.id, code)
    messages.add(str(err.value))
    _sql("UPDATE accounts.worlds SET visibility = 'INVITE', status = 'ARCHIVED' WHERE id = :w", w=shared.id)
    with pytest.raises(worlds.WorldNotFound) as err:
        worlds.join_by_code(member.id, code)
    messages.add(str(err.value))
    assert messages == {worlds.INVALID_CODE}
    _sql("UPDATE accounts.worlds SET status = 'ACTIVE' WHERE id = :w", w=shared.id)
    assert _membership(shared.id, member.id) is None and _seat(shared.schema, member.id) is None

    # Seviye kapisi (profil tanınırlığı -> reputation.level)
    worlds.update_world_meta(shared.owner.id, shared.id, min_manager_level=6)
    with pytest.raises(worlds.LevelTooLowError, match="en az 6. seviye"):
        worlds.join_by_code(member.id, code)
    worlds.update_world_meta(shared.owner.id, shared.id, min_manager_level=5)

    spaced = " ".join([code[:4].lower(), code[4:].lower()])
    ctx = worlds.join_by_code(member.id, spaced)
    assert (ctx.world_id, ctx.schema, ctx.kind, ctx.role, ctx.user_id) == (
        shared.id, shared.schema, "SHARED", "MEMBER", member.id)
    membership = _membership(shared.id, member.id)
    assert (membership.role, membership.status) == ("MEMBER", "ACTIVE") and membership.last_seen_at is not None
    seat = _seat(shared.schema, member.id)
    assert not seat.is_primary and seat.team_id is None and seat.status == "ACTIVE"
    assert seat.reputation == pytest.approx(12.3) and seat.joined_career_week == _career_week(shared.schema)

    again = worlds.join_by_code(member.id, code)                       # tekrar katilim: ayni koltuk
    assert again == ctx and _seat(shared.schema, member.id).id == seat.id
    assert _scalar(f'SELECT count(*) FROM "{shared.schema}".world_managers') == 2
    assert worlds.join_by_code(shared.owner.id, code).role == "OWNER"  # sahip zaten uye

    # Kapasite: 2 kisilik dunya dolu
    worlds.update_world_meta(shared.owner.id, shared.id, max_managers=2, min_manager_level=1)
    third = _user("ucuncu")
    with pytest.raises(worlds.WorldFullError):
        worlds.join_by_code(third.id, code)
    assert _membership(shared.id, third.id) is None and _seat(shared.schema, third.id) is None
    assert WorldRules.from_dict(_scalar(f'SELECT world_rules FROM "{shared.schema}".game_state')).max_seats == 2
    with pytest.raises(worlds.WorldError, match="2-64"):
        worlds.update_world_meta(shared.owner.id, shared.id, max_managers=1)


def test_two_threads_joining_last_slot_exactly_one_world_full_error(shared, monkeypatch):
    import worlds

    code = _code(shared.id)
    worlds.join_by_code(_user("ilk").id, code)                          # 3 koltuktan 2'si dolu
    rivals = [_user("yaris"), _user("yaris")]
    real_count = worlds._active_count

    def slow_count(db, world_id):
        count = real_count(db, world_id)
        time.sleep(0.4)                     # dunya satiri kilitliyken bekle: kilitsiz olsaydi ikisi de 2 okurdu
        return count

    monkeypatch.setattr(worlds, "_active_count", slow_count)
    results: list = []
    barrier = threading.Barrier(2)

    def join(user_id):
        barrier.wait()
        try:
            results.append(worlds.join_by_code(user_id, code))
        except Exception as exc:
            results.append(exc)

    threads = [threading.Thread(target=join, args=(r.id,)) for r in rivals]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    joined = [r for r in results if isinstance(r, worlds.WorldContext)]
    full = [r for r in results if isinstance(r, worlds.WorldFullError)]
    assert len(joined) == 1 and len(full) == 1, results
    assert _scalar("SELECT count(*) FROM accounts.world_memberships WHERE world_id = :w AND status = 'ACTIVE'",
                   w=shared.id) == 3
    assert _scalar(f'SELECT count(*) FROM "{shared.schema}".world_managers') == 3
    with pytest.raises(worlds.WorldError, match="3 aktif menajer var"):
        worlds.update_world_meta(shared.owner.id, shared.id, max_managers=2)


def test_public_list_excludes_private_full_archived_and_member_worlds(shared):
    import worlds

    viewer = _user("izleyici")
    host = _user("ev_sahibi")
    other = _user("diger")

    def fake_world(name, visibility="PUBLIC", max_managers=4, status="ACTIVE", kind="SHARED", members=()):
        schema = f"world_a1_list_{next(_names)}"
        wid = _scalar("INSERT INTO accounts.worlds (name, schema_name, kind, owner_user_id, visibility, invite_code, "
                      "max_managers, min_manager_level, status) VALUES (:n, :s, :k, :o, :v, :c, :m, 3, :st) "
                      "RETURNING id", n=name, s=schema, k=kind, o=host.id, v=visibility,
                      c=None if visibility == "PRIVATE" else f"L{next(_names):07d}", m=max_managers, st=status)
        for uid, role, status_ in ((host.id, "OWNER", "ACTIVE"), *members):
            _sql("INSERT INTO accounts.world_memberships (world_id, user_id, role, status) VALUES (:w, :u, :r, :s)",
                 w=wid, u=uid, r=role, s=status_)
        return wid

    open_a = fake_world("Açık Lig 100% _A")
    open_b = fake_world("Açık Kupa", members=((other.id, "MEMBER", "ACTIVE"),))
    private = fake_world("Gizli Lig", visibility="PRIVATE")
    invite = fake_world("Davetli Lig", visibility="INVITE")
    full = fake_world("Dolu Lig", max_managers=2, members=((other.id, "MEMBER", "ACTIVE"),))
    archived = fake_world("Arşiv Lig", status="ARCHIVED")
    mine = fake_world("Benim Ligim", members=((viewer.id, "MEMBER", "ACTIVE"),))
    kicked = fake_world("Atıldığım Lig", members=((viewer.id, "MEMBER", "KICKED"),))
    left = fake_world("Ayrıldığım Lig", members=((viewer.id, "MEMBER", "LEFT"),))
    personal = fake_world("Kişisel", kind="PERSONAL", max_managers=1)

    listed = worlds.list_public_worlds(viewer.id)
    ids = {w.id for w in listed}
    assert {open_a, open_b, left} <= ids
    assert not ids & {private, invite, full, archived, mine, kicked, personal}
    assert all(w.invite_code is None and w.my_role is None and w.visibility == "PUBLIC" for w in listed)
    b_info = next(w for w in listed if w.id == open_b)
    assert (b_info.active_managers, b_info.max_managers, b_info.min_manager_level, b_info.owner_name) == (
        2, 4, 3, host.name)
    assert [w.id for w in listed].index(open_b) < [w.id for w in listed].index(open_a)   # dolulukta once

    assert [w.id for w in worlds.list_public_worlds(viewer.id, query=" 100%  _a")] == [open_a]
    assert [w.id for w in worlds.list_public_worlds(viewer.id, query="kupa")] == [open_b]
    assert worlds.list_public_worlds(viewer.id, query="Açık%Kupa") == []          # % joker degil
    assert len(worlds.list_public_worlds(viewer.id, limit=1)) == 1

    for world_id in (private, invite, archived, personal, 99_999_999):
        with pytest.raises(worlds.WorldNotFound) as err:
            worlds.join_public(viewer.id, world_id)
        assert str(err.value) == worlds.NOT_JOINABLE
    with pytest.raises(worlds.WorldNotFound):                          # listelenen ama semasi olmayan dunya
        worlds.join_public(viewer.id, open_a)

    # Gercek acik dunya: listede gorunur ve katilinir
    worlds.update_world_meta(shared.owner.id, shared.id, visibility="PUBLIC")
    assert shared.id in {w.id for w in worlds.list_public_worlds(viewer.id)}
    ctx = worlds.join_public(viewer.id, shared.id)
    assert ctx.role == "MEMBER" and _seat(shared.schema, viewer.id) is not None
    assert shared.id not in {w.id for w in worlds.list_public_worlds(viewer.id)}


# ---------------------------------------------------------------------------
# Giris, uyelik, ayrilma
# ---------------------------------------------------------------------------

def test_enter_world_rejects_left_kicked_and_non_members(shared):
    import accounts
    import database
    import worlds

    code = _code(shared.id)
    member = _user("girisci", career_schema="career_a1_enter")
    session = accounts.AuthSession(member.id, member.name, "career_a1_enter")
    personal = worlds.ensure_personal_world(session)
    assert worlds.default_world(session).world_id == personal.id          # yeni hesap: kisisel kariyer

    worlds.join_by_code(member.id, code)
    _sql("UPDATE accounts.world_memberships SET last_seen_at = now() - interval '1 day' "
         "WHERE world_id = :w AND user_id = :u", w=personal.id, u=member.id)
    ctx = worlds.enter_world(member.id, shared.id)
    assert (ctx.world_id, ctx.role, ctx.schema) == (shared.id, "MEMBER", shared.schema)
    assert worlds.default_world(session).world_id == shared.id           # en son girilen dunya
    info = worlds.check_membership(member.id, shared.id)
    assert (info.username, info.role, info.status) == (member.name, "MEMBER", "ACTIVE")
    assert [w.id for w in worlds.list_my_worlds(member.id)] == [personal.id, shared.id]
    assert worlds.list_my_worlds(member.id)[1].invite_code is None         # uye kodu gormez

    outsider = _user("yabanci")
    for uid, wid in ((outsider.id, shared.id), (member.id, 99_999_999)):
        with pytest.raises(worlds.NotAMemberError):
            worlds.enter_world(uid, wid)
        with pytest.raises(worlds.NotAMemberError):
            worlds.check_membership(uid, wid)

    with database.session_scope() as db:                                  # yonetici atar (cagiranin islemi)
        with pytest.raises(worlds.WorldPermissionError):
            worlds.mark_membership(db, shared.id, shared.owner.id, "KICKED")
        worlds.mark_membership(db, shared.id, member.id, "KICKED")
    with pytest.raises(worlds.NotAMemberError, match="çıkarıldın"):
        worlds.enter_world(member.id, shared.id)
    with pytest.raises(worlds.NotAMemberError):
        worlds.check_membership(member.id, shared.id)
    with pytest.raises(worlds.WorldPermissionError):
        worlds.join_by_code(member.id, code)                              # atilan geri donemez
    assert worlds.default_world(session).world_id == personal.id
    assert [w.id for w in worlds.list_my_worlds(member.id)] == [personal.id]

    with database.session_scope() as db:
        with pytest.raises(worlds.WorldError):
            worlds.mark_membership(db, shared.id, member.id, "BANNED")
        worlds.mark_membership(db, shared.id, member.id, "LEFT")
    with pytest.raises(worlds.NotAMemberError):
        worlds.enter_world(member.id, shared.id)

    _sql("UPDATE accounts.worlds SET status = 'ARCHIVED' WHERE id = :w", w=shared.id)
    with pytest.raises(worlds.WorldError):
        worlds.enter_world(shared.owner.id, shared.id)
    with pytest.raises(worlds.NotAMemberError):
        worlds.check_membership(shared.owner.id, shared.id)


def test_leave_releases_seat_to_ai_with_protection_and_owner_cannot_leave(shared):
    import accounts
    import worlds
    from world_rules import WorldRules

    code = _code(shared.id)
    member = _user("ayrilan")
    worlds.join_by_code(member.id, code)
    seat = _seat(shared.schema, member.id)
    team_id = _scalar(f'SELECT id FROM "{shared.schema}".teams ORDER BY id LIMIT 1')
    _sql(f'UPDATE "{shared.schema}".world_managers SET team_id = :t, ready_career_week = 1 WHERE id = :s',
         t=team_id, s=seat.id)

    with pytest.raises(worlds.WorldPermissionError):
        worlds.leave_world(shared.owner.id, shared.id)
    with pytest.raises(worlds.NotAMemberError):
        worlds.leave_world(_user("uye_degil").id, shared.id)
    personal_owner = _user("tek", career_schema="career_a1_leave")
    personal = worlds.ensure_personal_world(accounts.AuthSession(personal_owner.id, personal_owner.name,
                                                                 "career_a1_leave"))
    with pytest.raises(worlds.WorldPermissionError):
        worlds.leave_world(personal_owner.id, personal.id)

    worlds.leave_world(member.id, shared.id)
    membership = _membership(shared.id, member.id)
    assert (membership.role, membership.status) == ("MEMBER", "LEFT") and membership.left_at is not None
    released = _seat(shared.schema, member.id)
    assert (released.id, released.team_id, released.status) == (seat.id, None, "LEFT")
    rules = WorldRules.from_dict(_scalar(f'SELECT world_rules FROM "{shared.schema}".game_state'))
    assert _scalar(f'SELECT ai_protected_until FROM "{shared.schema}".teams WHERE id = :t', t=team_id) == (
        _career_week(shared.schema) + rules.protection_weeks)
    event = _sql(f'SELECT kind, actor_manager_id, payload FROM "{shared.schema}".world_events')
    assert len(event) == 1 and event[0].kind == "RELEASE" and event[0].actor_manager_id == seat.id
    assert event[0].payload == {"reason": "LEFT", "user_id": member.id, "team_id": team_id}
    with pytest.raises(worlds.NotAMemberError):
        worlds.leave_world(member.id, shared.id)

    # Ayrilan geri donebilir: ayni koltuk satiri kulupsuz yeniden etkin
    back = worlds.join_by_code(member.id, code)
    assert back.role == "MEMBER"
    rejoined = _seat(shared.schema, member.id)
    assert (rejoined.id, rejoined.team_id, rejoined.status) == (seat.id, None, "ACTIVE")


# ---------------------------------------------------------------------------
# Roller, davet kodu, ust bilgi
# ---------------------------------------------------------------------------

def test_role_matrix_members_listing_and_admin_permissions(shared):
    import worlds

    code = _code(shared.id)
    alice, bob = _user("alice"), _user("bob")
    worlds.join_by_code(alice.id, code)
    worlds.join_by_code(bob.id, code)
    owner = shared.owner.id

    with pytest.raises(worlds.WorldPermissionError):
        worlds.set_role(alice.id, shared.id, bob.id, "ADMIN")          # uye rol veremez
    worlds.set_role(owner, shared.id, alice.id, "admin")
    assert _membership(shared.id, alice.id).role == "ADMIN"
    worlds.set_role(owner, shared.id, alice.id, "ADMIN")                # degisiklik yok: sorun yok
    with pytest.raises(worlds.WorldPermissionError):
        worlds.set_role(alice.id, shared.id, bob.id, "ADMIN")          # yonetici baskasini atayamaz
    for actor in (owner, alice.id):
        with pytest.raises(worlds.WorldPermissionError):
            worlds.set_role(actor, shared.id, owner, "MEMBER")          # sahibin rolu sabit
    with pytest.raises(worlds.WorldPermissionError):
        worlds.set_role(owner, shared.id, alice.id, "OWNER")            # sahiplik devredilemez
    with pytest.raises(worlds.WorldError):
        worlds.set_role(owner, shared.id, alice.id, "SUPERUSER")
    with pytest.raises(worlds.NotAMemberError):
        worlds.set_role(owner, shared.id, _user("kimse").id, "ADMIN")

    # Yonetici: davet kodunu gorur ve dondurur, ust bilgiyi degistirir; arsivleyemez
    alice_view = next(w for w in worlds.list_my_worlds(alice.id) if w.id == shared.id)
    assert alice_view.my_role == "ADMIN" and alice_view.invite_code == code
    bob_view = next(w for w in worlds.list_my_worlds(bob.id) if w.id == shared.id)
    assert bob_view.invite_code is None
    info = worlds.update_world_meta(alice.id, shared.id, name="Yeni Ad")
    assert info.name == "Yeni Ad" and info.invite_code == code
    with pytest.raises(worlds.WorldPermissionError):
        worlds.update_world_meta(alice.id, shared.id, status="ARCHIVED")
    with pytest.raises(worlds.WorldPermissionError):
        worlds.update_world_meta(bob.id, shared.id, name="Uye Adi")
    with pytest.raises(worlds.WorldPermissionError):
        worlds.rotate_invite_code(bob.id, shared.id)
    with pytest.raises(worlds.WorldError):
        worlds.update_world_meta(owner, shared.id, invite_code="HACKED12")
    assert worlds.rotate_invite_code(alice.id, shared.id) != code

    worlds.set_role(alice.id, shared.id, alice.id, "MEMBER")            # yonetici kendini indirir
    assert _membership(shared.id, alice.id).role == "MEMBER"
    kinds = [row.payload["action"] for row in _sql(
        f'SELECT payload FROM "{shared.schema}".world_events WHERE kind = \'RULES\' ORDER BY id')]
    assert kinds == ["ROLE", "META", "INVITE_ROTATED", "ROLE"]

    # Kulupler canli okunur (birincil: game_state; digerleri: koltuk)
    team = _sql(f'SELECT id, name FROM "{shared.schema}".teams ORDER BY id LIMIT 2')
    _sql(f'UPDATE "{shared.schema}".game_state SET user_team_id = :t WHERE id = 1', t=team[0].id)
    _sql(f'UPDATE "{shared.schema}".world_managers SET team_id = :t WHERE user_id = :u', t=team[1].id, u=bob.id)
    worlds.leave_world(alice.id, shared.id)
    as_member = worlds.members(bob.id, shared.id)
    assert [(m.username, m.role, m.team_name) for m in as_member] == [
        (shared.owner.name, "OWNER", team[0].name), (bob.name, "MEMBER", team[1].name)]
    as_owner = worlds.members(owner, shared.id)
    assert [(m.username, m.status) for m in as_owner][-1] == (alice.name, "LEFT")
    with pytest.raises(worlds.NotAMemberError):
        worlds.members(alice.id, shared.id)


def test_invite_rotation_kills_old_code_and_visibility_controls_code(shared):
    import worlds

    old = _code(shared.id)
    new = worlds.rotate_invite_code(shared.owner.id, shared.id)
    assert new != old and len(new) == worlds.INVITE_CODE_LENGTH and _code(shared.id) == new
    first = _user("davetli")
    with pytest.raises(worlds.WorldNotFound) as err:
        worlds.join_by_code(first.id, old)
    assert str(err.value) == worlds.INVALID_CODE
    assert worlds.join_by_code(first.id, new).world_id == shared.id

    info = worlds.update_world_meta(shared.owner.id, shared.id, visibility="PRIVATE")
    assert info.visibility == "PRIVATE" and info.invite_code is None and _code(shared.id) is None
    with pytest.raises(worlds.WorldError):
        worlds.rotate_invite_code(shared.owner.id, shared.id)
    with pytest.raises(worlds.WorldNotFound):
        worlds.join_by_code(_user("gec").id, new)
    info = worlds.update_world_meta(shared.owner.id, shared.id, visibility="INVITE")
    assert info.invite_code and info.invite_code != new
    with pytest.raises(worlds.WorldError, match="Değiştirilemeyen"):
        worlds.update_world_meta(shared.owner.id, shared.id, kind="PERSONAL")


# ---------------------------------------------------------------------------
# Kisisel kariyeri paylasima acma
# ---------------------------------------------------------------------------

def test_convert_personal_to_shared_keeps_data_and_opens_joining(world):
    import accounts
    import database
    import worlds
    from models import GameMode, GameState, WorldManager
    from world_rules import WorldRules

    schema = "career_a1_conv"
    owner = _user("cevirici", career_schema=schema)
    accounts._build_world(schema, 7, "synthetic")
    _sql(f'UPDATE "{schema}".game_state SET user_id = :u WHERE id = 1', u=owner.id)
    personal = worlds.ensure_personal_world(accounts.AuthSession(owner.id, owner.name, schema))
    teams_before = _scalar(f'SELECT count(*) FROM "{schema}".teams')

    stranger = _user("el")
    with pytest.raises(worlds.WorldPermissionError):
        worlds.convert_personal_to_shared(stranger.id, personal.id, "Başkasının", "INVITE", 4)
    with pytest.raises(worlds.WorldError):
        worlds.convert_personal_to_shared(owner.id, personal.id, "Geçerli", "INVITE", 1)
    with pytest.raises(worlds.WorldPermissionError):
        worlds.update_world_meta(owner.id, personal.id, visibility="PUBLIC")   # kisiselde yalnizca ad
    assert worlds.update_world_meta(owner.id, personal.id, name="Benim Kariyerim").name == "Benim Kariyerim"

    info = worlds.convert_personal_to_shared(owner.id, personal.id, "Arkadaş Ligi", "INVITE", 4)
    assert (info.id, info.kind, info.name, info.visibility, info.max_managers, info.my_role) == (
        personal.id, "SHARED", "Arkadaş Ligi", "INVITE", 4, "OWNER")
    assert info.invite_code and info.schema == schema
    with database.career_context(schema), database.session_scope() as db:
        state = db.get(GameState, 1)
        rules = WorldRules.from_dict(state.world_rules)
        assert rules.shared and rules.max_seats == 4 and rules.validate() == []
        assert state.game_mode is GameMode.CAREER and state.user_id == owner.id
        primary = db.query(WorldManager).filter(WorldManager.is_primary.is_(True)).one()
        assert primary.team_id is None and primary.reputation is None
    assert _scalar(f'SELECT count(*) FROM "{schema}".teams') == teams_before
    with pytest.raises(worlds.WorldError, match="zaten paylaşılan"):
        worlds.convert_personal_to_shared(owner.id, personal.id, "Tekrar", "INVITE", 4)

    guest = _user("misafir")
    ctx = worlds.join_by_code(guest.id, info.invite_code)
    assert ctx.schema == schema and ctx.kind == "SHARED" and _seat(schema, guest.id) is not None
    assert worlds.ensure_personal_world(accounts.AuthSession(owner.id, owner.name, schema)).kind == "SHARED"


def test_world_with_real_names_cannot_become_shared(world):
    """
    İsim maskelemesi kapali (game_state.mask_level = 'off') kisisel dunya paylasima acilamaz:
    gercek kulup/lig/oyuncu adlari yalnizca sahibinin kendi makinesindeki tek kisilik oyun icindir.
    """
    import accounts
    import worlds
    from models import GameState

    schema = "career_a1_real"
    owner = _user("gercekad", career_schema=schema)
    accounts._build_world(schema, 11, "synthetic")
    _sql(f'UPDATE "{schema}".game_state SET user_id = :u WHERE id = 1', u=owner.id)
    personal = worlds.ensure_personal_world(accounts.AuthSession(owner.id, owner.name, schema))
    assert _scalar(f'SELECT mask_level FROM "{schema}".game_state WHERE id = 1') == "light"  # seed varsayilani

    _sql(f"UPDATE \"{schema}\".game_state SET mask_level = 'off' WHERE id = 1")
    assert worlds.world_has_real_names(SimpleNamespace(mask_level="off"))
    assert not worlds.world_has_real_names(SimpleNamespace(mask_level="light"))
    assert not worlds.world_has_real_names(GameState(id=1))                  # eski kayit: varsayilan maskeli
    with pytest.raises(worlds.WorldError, match="gerçek isimlerle"):
        worlds.convert_personal_to_shared(owner.id, personal.id, "Gerçek Adlı", "INVITE", 4)
    assert _scalar("SELECT kind FROM accounts.worlds WHERE id = :w", w=personal.id) == "PERSONAL"

    _sql(f"UPDATE \"{schema}\".game_state SET mask_level = 'light' WHERE id = 1")  # maskeliyse serbest
    assert worlds.convert_personal_to_shared(owner.id, personal.id, "Maskeli Lig", "INVITE", 4).kind == "SHARED"


# ---------------------------------------------------------------------------
# ensure_career_ready: dunya kilidi ve schema_version atlamasi
# ---------------------------------------------------------------------------

def test_ensure_career_ready_skips_ddl_when_schema_version_matches(shared, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    import accounts
    import database
    from career_manager import CareerManager

    session = accounts.AuthSession(shared.owner.id, shared.owner.name, shared.schema, world_id=shared.id,
                                   world_kind="SHARED")
    upgrades: list = []
    real_upgrade = database.upgrade_schema

    def spy_upgrade():
        lock = database._world_lock.get()
        upgrades.append((database.current_career_schema(), lock.mode if lock is not None else None))
        return real_upgrade()

    hook_calls: list = []
    real_hook = getattr(CareerManager, "ensure_world_setup", None)

    def world_setup_spy(self):
        hook_calls.append(database.current_career_schema())
        return list(real_hook(self)) if real_hook is not None else ["koltuk kontrolü yapıldı"]

    monkeypatch.setattr(database, "upgrade_schema", spy_upgrade)
    monkeypatch.setattr(CareerManager, "ensure_world_setup", world_setup_spy, raising=False)

    # create_world surumu kaydetti: DDL ve dunya kilidi hic yok
    messages = accounts.ensure_career_ready(session)
    assert upgrades == [] and isinstance(messages, list)
    assert hook_calls == [shared.schema]

    # Eski surum: yukseltme YALNIZCA exclusive dunya kilidi altinda, sonra surum kaydedilir
    _sql("UPDATE accounts.worlds SET schema_version = 3 WHERE id = :w", w=shared.id)
    accounts.ensure_career_ready(session)
    assert upgrades == [(shared.schema, "exclusive")]
    assert _scalar("SELECT schema_version FROM accounts.worlds WHERE id = :w", w=shared.id) == database.SCHEMA_VERSION
    accounts.ensure_career_ready(session)
    assert len(upgrades) == 1                                           # ikinci giris: DDL yok

    # Surum esit ama katalog eksik (surum artirmasi unutulmus / elle bozulmus): yine yukseltilir
    _sql(f'ALTER TABLE "{shared.schema}".players DROP COLUMN development_progress')
    messages = accounts.ensure_career_ready(session)
    assert "sütun eklendi: players.development_progress" in messages and len(upgrades) == 2
    accounts.ensure_career_ready(session)
    assert len(upgrades) == 2

    # Menajer islemi (SHARED dunya kilidi) acikken: surum esitse giris beklemez; yukseltme gerekirse kilidi bekler
    holder = database.engine.connect()
    try:
        # islem seviyesi: holder.rollback() kilidi birakir (baglanti havuza kilitli donmez)
        holder.execute(text("SELECT pg_advisory_xact_lock_shared(:ns, hashtext(:k))"),
                       {"ns": database.LOCK_WORLD_TURN, "k": shared.schema})
        started = time.monotonic()
        accounts.ensure_career_ready(session)
        assert time.monotonic() - started < 5 and len(upgrades) == 2
        _sql("UPDATE accounts.worlds SET schema_version = 0 WHERE id = :w", w=shared.id)
        monkeypatch.setenv(database.WORLD_LOCK_TIMEOUT_ENV, "300")
        with pytest.raises(OperationalError) as err:
            accounts.ensure_career_ready(session)
        assert getattr(err.value.orig, "pgcode", None) == database.LOCK_TIMEOUT_PGCODE
        assert _scalar("SELECT schema_version FROM accounts.worlds WHERE id = :w", w=shared.id) == 0
    finally:
        holder.rollback()
        holder.close()
    monkeypatch.delenv(database.WORLD_LOCK_TIMEOUT_ENV)
    accounts.ensure_career_ready(session)
    assert _scalar("SELECT schema_version FROM accounts.worlds WHERE id = :w", w=shared.id) == database.SCHEMA_VERSION
    with database.career_context(shared.schema):
        assert database.schema_problems() == []


# ---------------------------------------------------------------------------
# Menajer profili
# ---------------------------------------------------------------------------

def test_profile_and_record_season(world):
    import reputation
    import worlds

    user = _user("profil")
    fresh = worlds.profile(user.id)
    assert (fresh.user_id, fresh.reputation, fresh.best_level, fresh.seasons_completed, fresh.titles) == (
        user.id, 8.0, 1, 0, 0)
    worlds.record_season(user.id, 13.0, 2)
    worlds.record_season(user.id, 9.0, 0)
    after = worlds.profile(user.id)
    assert after.reputation == 9.0 and after.best_level == reputation.level(13.0).level
    assert (after.seasons_completed, after.titles) == (2, 2)
    worlds.record_season(user.id, 99.0, 1)                              # tanınırlık 1-20'ye kirpilir
    assert worlds.profile(user.id).reputation == 20.0
    for bad in (float("nan"), "yuksek", None):
        with pytest.raises(worlds.WorldError):
            worlds.record_season(user.id, bad, 0)
    with pytest.raises(worlds.WorldError):
        worlds.record_season(user.id, 10.0, -1)
    with pytest.raises(worlds.WorldError):
        worlds.profile(987654321)
