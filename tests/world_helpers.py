"""
Paylasilan dunya test yardimcilari (Faz 12 / 14. Asama).

    world_auth(...)          -> dunya alanli oturum: accounts.AuthSession(world_id, world_kind) ya da (A1 alanlari
                                eklemeden once) ayni alanlari tasiyan alt sinif
    make_shared_public(...)  -> testlerin sentetik 'public' dunyasini PAYLASILAN dunyaya cevirir: accounts.users
                                (sahte ozet), accounts.worlds, world_memberships ve public.world_managers satirlari,
                                game_state.user_id / world_rules. Donus: SharedWorld (id'ler)
    cleanup_shared(world)    -> kayit satirlarini (dunya, uyelikler, kullanicilar) siler ve dunyayi yeniden kurar
    app_as(user_id, ad, W)   -> o menajerin paylasilan dunya oturumuyla AppTest (web_app.py); lobby=True: lobiden baslar
    add_user(ad, sema)       -> accounts.users satiri (sahte ozet; parola girisi yapilmaz), id
    cleanup_users(onek)      -> oneki tasiyan test hesaplari, sahip olduklari dunya kayitlari ve bu dunyalarin
                                (world_* / career_*) semalari silinir (Faz 12 A4 lobi testleri)

Kurallar: testler kendi DB'lerinde calisir (conftest TEST_DB_NAME). Web testleri gibi dunyayi degistirir; her
test sonunda cleanup_shared cagrilir.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass

LEGACY_SCHEMA = "public"
DUMMY_PASSWORD_HASH = "scrypt$test$not-a-real-hash"       # bu hesaplarla parola girisi yapilmaz


@dataclass(frozen=True)
class SharedWorld:
    world_id: int
    schema: str
    owner_id: int
    user_ids: dict[str, int]          # kullanici adi -> accounts.users.id (sahip dahil)
    seat_ids: dict[str, int]          # kullanici adi -> world_managers.id (sahip: birincil koltuk)


@functools.cache
def _fallback_session_class():
    from accounts import AuthSession

    @dataclass(frozen=True)
    class WorldAuthSession(AuthSession):
        world_id: int | None = None
        world_kind: str | None = None

    return WorldAuthSession


def world_auth(user_id: int, username: str, career_schema: str = LEGACY_SCHEMA, *,
               world_id: int | None = None, world_kind: str | None = None):
    from accounts import AuthSession

    try:
        return AuthSession(user_id, username, career_schema, world_id=world_id, world_kind=world_kind)
    except TypeError:                                     # Faz 12 A1 AuthSession alanlarini henuz eklemedi
        return _fallback_session_class()(user_id, username, career_schema, world_id, world_kind)


def make_shared_public(
    owner: str,
    members: Sequence[str | tuple[str, str | None]] = (),
    *,
    owner_team: str | None = None,
    rules=None,
    name: str = "Test Dünyası",
    visibility: str = "INVITE",
    max_managers: int = 8,
    invite_code: str | None = "TESTKOD123",
) -> SharedWorld:
    """
    'public' dunyasini paylasilan dunyaya cevirir. members: "Ad" ya da ("Ad", "Kulup adi" | None).
    Sahip birincil koltuktur (kulubu GameState.user_team_id: owner_team). Dunya ONCEDEN kurulmus olmali (_reseed).
    """
    from sqlalchemy import text

    import database
    from career_manager import CareerManager
    from models import WorldManager
    from world_rules import WorldRules

    rules = rules if rules is not None else WorldRules.shared_defaults()
    database.init_accounts()
    entries = [(m, None) if isinstance(m, str) else (m[0], m[1]) for m in members]
    user_ids: dict[str, int] = {}
    with database.engine.begin() as conn:
        for username, _team in [(owner, owner_team), *entries]:
            user_ids[username] = conn.execute(text(
                'INSERT INTO "accounts".users (username, password_hash, career_schema) '
                "VALUES (:u, :h, :s) RETURNING id"
            ), {"u": username, "h": DUMMY_PASSWORD_HASH, "s": LEGACY_SCHEMA if username == owner else None}).scalar_one()
        world_id = conn.execute(text(
            'INSERT INTO "accounts".worlds (name, schema_name, kind, owner_user_id, visibility, invite_code, '
            "max_managers, min_manager_level, status, schema_version) "
            "VALUES (:n, :s, 'SHARED', :o, :v, :c, :m, 1, 'ACTIVE', :ver) "
            "ON CONFLICT (schema_name) DO UPDATE SET name = EXCLUDED.name, kind = 'SHARED', "
            "owner_user_id = EXCLUDED.owner_user_id, visibility = EXCLUDED.visibility, "
            "invite_code = EXCLUDED.invite_code, max_managers = EXCLUDED.max_managers, status = 'ACTIVE' "
            "RETURNING id"
        ), {"n": name, "s": LEGACY_SCHEMA, "o": user_ids[owner], "v": visibility, "c": invite_code,
            "m": max_managers, "ver": database.SCHEMA_VERSION}).scalar_one()
        for username, uid in user_ids.items():
            conn.execute(text(
                'INSERT INTO "accounts".world_memberships (world_id, user_id, role, status, team_name_cache) '
                "VALUES (:w, :u, :r, 'ACTIVE', :t) ON CONFLICT (world_id, user_id) DO UPDATE "
                "SET role = EXCLUDED.role, status = 'ACTIVE', left_at = NULL"
            ), {"w": world_id, "u": uid, "r": "OWNER" if username == owner else "MEMBER",
                "t": owner_team if username == owner else dict(entries).get(username)})

    seat_ids: dict[str, int] = {}
    with database.career_context(LEGACY_SCHEMA), database.session_scope() as db:
        cm = CareerManager(db)
        state = cm.state
        state.user_id = user_ids[owner]
        state.world_rules = rules.to_dict()
        if owner_team is not None:
            cm.set_user_team(cm.find_team(owner_team))
        week = cm.career_week
        seats = [WorldManager(user_id=user_ids[owner], display_name=owner, is_primary=True,
                              joined_career_week=week)]
        for username, team_name in entries:
            team = cm.find_team(team_name) if team_name else None
            if team_name and team is None:
                raise ValueError(f"Kulüp bulunamadı: {team_name}")
            seats.append(WorldManager(user_id=user_ids[username], display_name=username, is_primary=False,
                                      team_id=team.id if team else None, reputation=8.0,
                                      joined_career_week=week))
        db.add_all(seats)
        db.flush()
        seat_ids = {seat.display_name: seat.id for seat in seats}
    return SharedWorld(world_id, LEGACY_SCHEMA, user_ids[owner], user_ids, seat_ids)


def cleanup_shared(world: SharedWorld | None = None, reseed: bool = True) -> None:
    """Dunya kaydi + test kullanicilari silinir (uyelikler ve koltuklar FK ile duser); dunya yeniden kurulur."""
    from sqlalchemy import text

    import database

    database.init_accounts()
    with database.engine.begin() as conn:
        if world is None:
            conn.execute(text('DELETE FROM "accounts".worlds WHERE schema_name = :s'), {"s": LEGACY_SCHEMA})
        else:
            conn.execute(text('DELETE FROM "accounts".worlds WHERE id = :w'), {"w": world.world_id})
            conn.execute(text('DELETE FROM "accounts".users WHERE id = ANY(:ids)'),
                         {"ids": list(world.user_ids.values())})
    if reseed:
        from tests.test_web_app import _reseed

        _reseed()


def app_as(user_id: int, name: str, world_id: int | None, *, schema: str = LEGACY_SCHEMA,
           world_kind: str | None = "SHARED", seed: str | None = None, run: bool = True, lobby: bool = False):
    """
    Paylasilan dunya oturumuyla AppTest (web_app.py). run=False: ilk cizimden once session_state ayarlanabilir.
    lobby=True: oturum lobiden baslar (dunyasiz oturumun baska kariyeri cizmemesi icin world_id=None ile kullan).
    """
    from streamlit.testing.v1 import AppTest

    from tests.test_web_app import APP

    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["auth"] = world_auth(user_id, name, schema, world_id=world_id, world_kind=world_kind)
    if seed is not None:
        at.session_state["career_seed"] = seed
    if lobby:
        at.session_state["world_lobby"] = True
    if run:
        at.run()
    return at


def add_user(username: str, career_schema: str | None = None) -> int:
    """Test hesabi (sahte parola ozeti: bu hesapla parola girisi yapilmaz)."""
    from sqlalchemy import text

    import database

    database.init_accounts()
    with database.engine.begin() as conn:
        return conn.execute(text(
            'INSERT INTO "accounts".users (username, password_hash, career_schema) VALUES (:u, :h, :s) RETURNING id'
        ), {"u": username, "h": DUMMY_PASSWORD_HASH, "s": career_schema}).scalar_one()


def cleanup_users(prefix: str) -> None:
    """
    Oneki tasiyan test hesaplari silinir; sahip olduklari dunya kayitlari ve o dunyalarin semalari (world_* /
    career_*; 'public' HARIC) dusurulur. Uyelikler FK ile duser. Kayitsiz kalan 'public' dunya kaydi da silinir.
    """
    from sqlalchemy import text

    import database

    database.init_accounts()
    pattern = prefix.replace("\\", "\\\\").replace("_", r"\_").replace("%", r"\%") + "%"
    with database.engine.begin() as conn:
        ids = conn.execute(text('SELECT id FROM "accounts".users WHERE username LIKE :p'), {"p": pattern}).scalars().all()
        schemas = set(conn.execute(text(
            'SELECT schema_name FROM "accounts".worlds WHERE owner_user_id = ANY(:ids)'), {"ids": ids}).scalars())
        schemas |= set(conn.execute(text(
            'SELECT career_schema FROM "accounts".users WHERE id = ANY(:ids) AND career_schema IS NOT NULL'),
            {"ids": ids}).scalars())
        conn.execute(text('DELETE FROM "accounts".worlds WHERE owner_user_id = ANY(:ids) OR schema_name = :s'),
                     {"ids": ids, "s": LEGACY_SCHEMA})
        conn.execute(text('DELETE FROM "accounts".users WHERE id = ANY(:ids)'), {"ids": ids})
    for schema in sorted(schemas - {LEGACY_SCHEMA}):
        if schema.startswith(("world_", "career_")):
            database.drop_career_schema(schema)
