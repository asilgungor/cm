"""
worlds.py
=========
Dunya kaydi (Faz 12 / 14. Asama, 12A): kisisel ve paylasilan dunyalar, uyelik, davet kodu, menajer profili.
Hesap kontrolcusu gibi calisir (accounts.py): Streamlit bilmez, "(own txn)" fonksiyonlar kendi islemlerini
acar; mark_membership cagiranin islemine katilir. Hatalar WorldError (ValueError) alt siniflaridir, mesajlar Turkce.

    accounts.worlds / world_memberships / manager_profiles (models.World / WorldMembership / ManagerProfile)

Kisisel dunya:
    Her kariyer semasinin (accounts.users.career_schema) bir kaydi vardir: kind PERSONAL, max_managers 1, sahibinin
    OWNER uyeligi. ensure_personal_world IDEMPOTENT doldurur (accounts.ensure_career_ready her giriste cagirir):
    INSERT ... ON CONFLICT (schema_name) DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id WHERE sahip bos.
    Sahipsiz kalmis (hesap silinmis) ya da bayat sahipli (sahibin career_schema'si artik baska) kisisel kayit,
    semanin guncel sahibine onarilir. Kaydin semasi hesapta yoksa (web testlerinin sahte oturumu) sahipsiz kayit.

Paylasilan dunya (create_world):
    Sema adi 'world_<id>' (id accounts.worlds dizisinden ONCEDEN ayrilir; ad doluysa world_<id>_2 ...). Kurulum
    accounts.register'in yolunu izler: sema + seed (accounts._build_world), dunya ayarlari (game_state.user_id,
    world_rules shared=True, tanınırlık profilden, CAREER modu, ilk tur, birincil koltuk) AYRI islemde, en son
    kayit + OWNER uyeligi tek islemde. Basarisizlikta sema ve kayit silinir (iz kalmaz). Kullanici basina es zamanli
    kurulum oturum seviyesi advisory lock (ad alani 10_004) ile siralanir; en fazla MAX_OWNED_WORLDS aktif dunya.

Katilim (join_by_code / join_public):
    career_context(dunya semasi) DISARIDA, icinde world_lock(sema, "shared") + tek oturum: once
    SELECT ... FROM accounts.worlds WHERE id = :id FOR UPDATE (kapasite yarisi: ikinci katilan ilk commit'i bekler
    ve dolu gorur), sonra uyelik (accounts, semayla nitelikli) ve world_managers koltugu (arama yolu: dunya semasi)
    TEK islemde yazilir. Koltugun tanınırlığı manager_profiles'tan, joined_career_week dunyanin kariyer haftasidir.
    Hafta ilerletme (exclusive dunya kilidi) suresince katilim bekler; asiri beklemede Turkce "mesgul" hatasi.
    Gecersiz davet kodu (yok / dondurulmus / ozel dunya / arsiv) icin TEK genel hata (kod tahmini sizmasin).

Koltuklar seats.SeatStore uzerinden yazilir (Faz 12 A2); SeatStore henuz iskeletse (NotImplementedError)
world_managers satirlari dogrudan ORM ile yazilir (_seat_store / _store_call). Ayrilan menajerin kulubu AI'ya
gecer: team_id NULL, teams.ai_protected_until = kariyer haftasi + rules.protection_weeks.

Rol matrisi (set_role): OWNER -> sahip olmayan her aktif uyeye ADMIN / MEMBER; ADMIN -> yalnizca kendini
MEMBER'a indirir; MEMBER -> hicbir rol. Sahiplik devredilemez, sahibin rolu degismez; sahip dunyadan ayrilamaz.
Yonetici islemleri (davet kodu, ust bilgi, rol, cevirme) ve ayrilma dunya semasindaki world_events'e yazilir.

Sozlesmeye ek: clean_world_name (UI dogrulamasi), registry_schema_version / record_schema_version
(accounts.ensure_career_ready'nin DDL atlamasi). Dunya adlari arayuzde yine de kacislanarak gosterilir.
"""

from __future__ import annotations

import dataclasses
import json
import math
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import case, func, null, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import aliased

import accounts
import database
import reputation
from models import (
    GameMode,
    GameState,
    ManagerProfile,
    MembershipRole,
    MembershipStatus,
    SeatStatus,
    Team,
    User,
    World,
    WorldEventKind,
    WorldKind,
    WorldManager,
    WorldMembership,
    WorldStatus,
    WorldVisibility,
)
from world_rules import WorldRules

if TYPE_CHECKING:
    from accounts import AuthSession

ADMIN_ROLES = frozenset({"OWNER", "ADMIN"})

PERSONAL_WORLD_NAME = "Kişisel kariyer"
WORLD_SCHEMA_PREFIX = "world_"
NAME_MAX = 40
NAME_EXTRA_CHARS = frozenset(" -_.")
MIN_SHARED_MANAGERS = 2
MAX_MANAGERS = 64
MAX_OWNED_WORLDS = 5                   # kullanicinin sahibi oldugu aktif paylasilan dunya siniri
PUBLIC_LIST_MAX = 100
MAX_WORLD_SEED = 2 ** 31 - 1
# Davet kodu: karismayan harf/rakamlar (0/O, 1/I yok), 8 karakter ~ 10^12 olasilik
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8
_INVITE_INPUT_MIN, _INVITE_INPUT_MAX = 4, 12
_LOCK_WORLD_CREATE = 10_004            # advisory lock ad alani (accounts: 10_001/10_002, dunya turu: 10_003)
_MAX_SCHEMA_SUFFIX = 50

_ACTIVE = MembershipStatus.ACTIVE.value
_OWNER = MembershipRole.OWNER.value
_ADMIN = MembershipRole.ADMIN.value
_MEMBER = MembershipRole.MEMBER.value
_PERSONAL = WorldKind.PERSONAL.value
_SHARED = WorldKind.SHARED.value
_PRIVATE = WorldVisibility.PRIVATE.value

# --- Mesajlar ---
INVALID_CODE = "Davet kodu geçersiz ya da artık kullanılmıyor."
NOT_JOINABLE = "Dünya bulunamadı ya da katılıma açık değil."
WORLD_FULL = "Bu dünyada boş menajer koltuğu kalmadı."
LEVEL_TOO_LOW = "Bu dünyaya katılmak için en az {need}. seviye ({title}) menajer olmalısın; şu an {mine}. seviyedesin."
NOT_A_MEMBER = "Bu dünyanın üyesi değilsin."
REMOVED = "Bu dünyadan çıkarıldın."
KICKED_REJOIN = "Bu dünyadan çıkarıldın; yeniden katılamazsın."
ARCHIVED = "Bu dünya arşivlendi."
OWNER_CANNOT_LEAVE = "Dünyanın sahibi dünyadan ayrılamaz."
PERSONAL_CANNOT_LEAVE = "Kişisel kariyerden ayrılınamaz."
ADMIN_ONLY = "Bu işlem için dünyanın sahibi ya da yöneticisi olmalısın."
OWNER_ONLY = "Bu işlem için dünyanın sahibi olmalısın."
OWNER_ROLE_FIXED = "Dünyanın sahibinin rolü değiştirilemez."
NO_TRANSFER = "Dünyanın sahipliği devredilemez."
TARGET_NOT_MEMBER = "Bu menajer dünyanın aktif üyesi değil."
BAD_ROLE = "Rol yönetici ya da üye olmalı."
BAD_STATUS = "Geçersiz üyelik durumu."
BAD_NAME = "Dünya adı 1-40 karakter olmalı; yalnızca harf, rakam, boşluk ve - _ . kullanılabilir."
BAD_VISIBILITY = "Görünürlük özel, davetli ya da açık olmalı."
BAD_MAX = f"Menajer sayısı {MIN_SHARED_MANAGERS}-{MAX_MANAGERS} arasında olmalı."
BAD_LEVEL = f"En düşük menajer seviyesi 1-{reputation.MAX_LEVEL} arasında olmalı."
BAD_SEED = f"Dünya tohumu 0 ile {MAX_WORLD_SEED} arasında bir tam sayı olmalı."
BAD_RULES = "Geçersiz dünya kuralları."
BAD_SEASON = "Geçersiz sezon bilgisi."
BAD_WORLD_STATUS = "Dünya durumu aktif ya da arşiv olmalı."
MAX_BELOW_ACTIVE = "Dünyada {active} aktif menajer var; menajer sınırı bundan düşük olamaz."
PERSONAL_META_ONLY_NAME = "Kişisel kariyerde yalnızca ad değiştirilebilir."
UNKNOWN_FIELDS = "Değiştirilemeyen dünya alanı: {fields}."
NO_INVITE_PERSONAL = "Kişisel kariyerin davet kodu yok."
NO_INVITE_PRIVATE = "Özel dünyanın davet kodu yok; önce görünürlüğü davetli ya da açık yap."
ALREADY_SHARED = "Bu dünya zaten paylaşılan dünya."
TOURNAMENT_CANNOT_SHARE = "Turnuva modundaki kariyer paylaşılan dünyaya çevrilemez."
TOO_MANY_WORLDS = "En fazla {n} paylaşılan dünyanın sahibi olabilirsin."
ACCOUNT_NOT_FOUND = "Menajer hesabı bulunamadı."
CREATE_FAILED = "Dünya kurulamadı. Lütfen tekrar deneyin."
BUSY = "Dünya şu an haftayı oynatıyor; birkaç saniye sonra tekrar dene."
FOREIGN_CONTEXT = "Bu dünya bağlamı başka bir menajere ait."


class WorldError(ValueError):
    """Dunya islemi yapilamaz (mesaj Turkce)."""


class WorldNotFound(WorldError):
    pass


class WorldPermissionError(WorldError):
    pass


class WorldFullError(WorldError):
    pass


class NotAMemberError(WorldError):
    pass


class LevelTooLowError(WorldError):
    pass


@dataclass(frozen=True)
class WorldInfo:
    id: int
    name: str
    schema: str
    kind: str
    visibility: str
    owner_user_id: int | None
    owner_name: str | None
    max_managers: int
    active_managers: int
    min_manager_level: int
    status: str
    created_at: datetime | None
    my_role: str | None
    invite_code: str | None          # yalnizca OWNER / ADMIN icin dolu


@dataclass(frozen=True)
class MembershipInfo:
    world_id: int
    user_id: int
    username: str
    role: str
    status: str
    joined_at: datetime | None
    last_seen_at: datetime | None
    team_name: str | None


@dataclass(frozen=True)
class WorldContext:
    world_id: int
    schema: str
    name: str
    kind: str
    role: str
    user_id: int
    world_seed: int | None


# ===========================================================================
# Dogrulama
# ===========================================================================

def clean_world_name(name) -> str:
    """Bosluklar sadelestirilir; 1-40 karakter, harf (Turkce dahil), ASCII rakam, bosluk ve - _ . disi reddedilir."""
    if not isinstance(name, str):
        raise WorldError(BAD_NAME)
    cleaned = " ".join(name.split())
    valid = 1 <= len(cleaned) <= NAME_MAX and all(
        ch.isalpha() or (ch.isascii() and ch.isdigit()) or ch in NAME_EXTRA_CHARS for ch in cleaned
    )
    if not valid:
        raise WorldError(BAD_NAME)
    return cleaned


def _clean_visibility(value) -> str:
    visibility = value.strip().upper() if isinstance(value, str) else getattr(value, "value", None)
    if visibility not in {v.value for v in WorldVisibility}:
        raise WorldError(BAD_VISIBILITY)
    return visibility


def _clean_int(value, low: int, high: int, message: str) -> int:
    if isinstance(value, bool):
        raise WorldError(message)
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise WorldError(message)
        value = int(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise WorldError(message) from None
    if not low <= number <= high:
        raise WorldError(message)
    return number


def _normalize_code(code) -> str | None:
    """Kullanicinin yazdigi kod: buyuk harf, bosluk/tire atilir. Bicimsiz girdi None (sorgu yapilmaz)."""
    if not isinstance(code, str):
        return None
    cleaned = "".join(ch for ch in code.strip().upper() if ch not in " -\t")
    if not _INVITE_INPUT_MIN <= len(cleaned) <= _INVITE_INPUT_MAX:
        return None
    if not all(ch.isascii() and ch.isalnum() for ch in cleaned):
        return None
    return cleaned


def _generate_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


def _new_invite_code(db, previous: str | None = None) -> str:
    """Kullanilmayan yeni kod (benzersiz kisit son emniyet)."""
    for _ in range(20):
        code = _generate_code()
        if code != previous and db.scalar(select(World.id).where(World.invite_code == code)) is None:
            return code
    raise WorldError(CREATE_FAILED)          # pratikte olasiliksiz


# ===========================================================================
# Altyapi: islemler, satir -> bilgi
# ===========================================================================

def _is_lock_timeout(exc: BaseException) -> bool:
    return getattr(getattr(exc, "orig", None), "pgcode", None) == database.LOCK_TIMEOUT_PGCODE


@contextmanager
def _world_txn(schema: str, lock: bool = True) -> Iterator:
    """
    Dunya semasi + hesap tablolari TEK islemde (hesap modelleri semayla nitelidir, oyun tablolari arama yolundan).
    career_context kilit blogunun DISINDA acilir; blok icinde yeni career_context acilmaz (database.world_lock).
    lock: dunya kilidi SHARED (hafta ilerlemesiyle ayni anda koltuk yazilmaz).
    """
    schema = database.valid_schema_name(schema)
    accounts._ensure_accounts()
    try:
        with database.career_context(schema):
            if lock:
                with database.world_lock(schema, "shared"), database.session_scope() as db:
                    yield db
            else:
                with database.session_scope() as db:
                    yield db
    except database.WorldBusyError as exc:
        raise WorldError(BUSY) from exc
    except OperationalError as exc:
        if _is_lock_timeout(exc):
            raise WorldError(BUSY) from exc
        raise


def _schema_exists(db, schema: str) -> bool:
    return bool(db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)"), {"s": schema}))


def _table_exists(db, schema: str, table: str) -> bool:
    return bool(db.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f'"{schema}"."{table}"'}))


def _info_select(user_id: int | None):
    """Dunya + aktif menajer sayisi + (kullanicinin aktif) rolu + sahibin adi."""
    counted = aliased(WorldMembership)
    active = (select(func.count(counted.id))
              .where(counted.world_id == World.id, counted.status == _ACTIVE)
              .correlate(World).scalar_subquery())
    owner = select(User.username).where(User.id == World.owner_user_id).correlate(World).scalar_subquery()
    if user_id is None:
        role = null()
    else:
        mine = aliased(WorldMembership)
        role = (select(mine.role)
                .where(mine.world_id == World.id, mine.user_id == user_id, mine.status == _ACTIVE)
                .correlate(World).scalar_subquery())
    return select(World, active.label("active"), role.label("role"), owner.label("owner_name"))


def _world_info(world: World, active, role: str | None, owner_name: str | None) -> WorldInfo:
    return WorldInfo(
        id=world.id,
        name=world.name,
        schema=world.schema_name,
        kind=world.kind,
        visibility=world.visibility,
        owner_user_id=world.owner_user_id,
        owner_name=owner_name,
        max_managers=world.max_managers,
        active_managers=int(active or 0),
        min_manager_level=world.min_manager_level,
        status=world.status,
        created_at=world.created_at,
        my_role=role,
        invite_code=world.invite_code if role in ADMIN_ROLES else None,
    )


def _load_info(db, world_id: int, user_id: int | None) -> WorldInfo:
    row = db.execute(_info_select(user_id).where(World.id == world_id)).first()
    if row is None:
        raise WorldNotFound(NOT_JOINABLE)
    return _world_info(*row)


def _context(world: World, role: str, user_id: int) -> WorldContext:
    return WorldContext(world_id=world.id, schema=world.schema_name, name=world.name, kind=world.kind,
                        role=role, user_id=user_id, world_seed=world.world_seed)


def _membership(db, world_id: int, user_id: int, *, lock: bool = False) -> WorldMembership | None:
    stmt = select(WorldMembership).where(WorldMembership.world_id == world_id, WorldMembership.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def _active_membership(db, world_id: int, user_id: int, *, lock: bool = False) -> WorldMembership:
    membership = _membership(db, world_id, user_id, lock=lock)
    if membership is None or membership.status != _ACTIVE:
        raise NotAMemberError(NOT_A_MEMBER)
    return membership


def _active_count(db, world_id: int) -> int:
    return int(db.scalar(select(func.count(WorldMembership.id)).where(
        WorldMembership.world_id == world_id, WorldMembership.status == _ACTIVE)) or 0)


def _owned_shared_count(db, user_id: int) -> int:
    return int(db.scalar(select(func.count(World.id)).where(
        World.owner_user_id == user_id, World.kind == _SHARED, World.status == WorldStatus.ACTIVE.value)) or 0)


def _detached_world(world_id: int) -> World | None:
    with accounts._accounts_scope() as db:
        return db.get(World, world_id)


def _audit(db, schema: str, kind: WorldEventKind, actor_user_id: int | None, payload: dict) -> None:
    """Yonetici islemi denetim kaydi (dunya semasi world_events). Kayit yazilamazsa islem bozulmaz (savepoint)."""
    schema = database.valid_schema_name(schema)
    if not (_table_exists(db, schema, "world_events") and _table_exists(db, schema, "world_managers")):
        return
    try:
        with db.begin_nested():
            db.execute(text(
                f'INSERT INTO "{schema}".world_events (season, week, career_week, kind, actor_manager_id, payload) '
                f"SELECT gs.season, gs.current_week, gs.career_week_offset + gs.current_week, :kind, "
                f'(SELECT wm.id FROM "{schema}".world_managers wm WHERE wm.user_id = :uid), CAST(:payload AS jsonb) '
                f'FROM "{schema}".game_state gs WHERE gs.id = 1'
            ), {"kind": kind.value, "uid": actor_user_id, "payload": json.dumps(payload, ensure_ascii=False)})
    except DBAPIError:
        pass


def _profile_row(db, user_id: int) -> ManagerProfile:
    db.execute(text(
        'INSERT INTO "accounts".manager_profiles (user_id) VALUES (:u) ON CONFLICT (user_id) DO NOTHING'
    ), {"u": user_id})
    return db.get(ManagerProfile, user_id, populate_existing=True)


def _career_week(state: GameState) -> int:
    return int(state.career_week_offset or 0) + int(state.current_week)


# ===========================================================================
# Koltuklar (seats.SeatStore; iskeletken dogrudan ORM)
# ===========================================================================

_MISSING = object()


def _seat_store(db):
    try:
        import seats

        return seats.SeatStore(db)
    except NotImplementedError:
        return None


def _store_call(db, method: str, *args):
    """SeatStore.<method>(*args); SeatStore/metot henuz yoksa _MISSING. SeatError -> WorldError."""
    store = _seat_store(db)
    fn = getattr(store, method, None) if store is not None else None
    if fn is None:
        return _MISSING
    try:
        return fn(*args)
    except NotImplementedError:
        return _MISSING
    except WorldError:
        raise
    except ValueError as exc:                  # seats.SeatError (Turkce)
        import seats

        if isinstance(exc, seats.SeatError):
            raise WorldError(str(exc)) from exc
        raise


def _ensure_primary_seat(db, user_id: int, display_name: str, career_week: int) -> None:
    """Birincil koltuk satiri (sahip). Kulubu ve tanınırlığı GameState'te kalir."""
    db.flush()
    if _store_call(db, "ensure_primary_row", user_id, display_name[:32]) is not _MISSING:
        return
    row = db.scalar(select(WorldManager).where(WorldManager.is_primary.is_(True)).with_for_update())
    if row is None:
        db.add(WorldManager(user_id=user_id, display_name=display_name[:32], is_primary=True,
                            status=SeatStatus.ACTIVE.value, joined_career_week=career_week))
    else:
        row.user_id = user_id
        row.display_name = display_name[:32]
        row.status = SeatStatus.ACTIVE.value
    db.flush()


def _add_seat(db, user: User, reputation_value: float, career_week: int) -> None:
    """Katilan menajerin koltugu: yeni satir ya da (ayrilip donen) eski satir yeniden etkin, kulupsuz."""
    rep = reputation.clamp(reputation_value)
    db.flush()
    existing = db.scalar(select(WorldManager).where(WorldManager.user_id == user.id).with_for_update())
    if existing is not None and existing.is_primary:
        return                                   # sahibin birincil koltugu (kulubu GameState'te)
    leftover = existing is not None and existing.status in (SeatStatus.ACTIVE.value, SeatStatus.RELEASED.value)
    if not leftover and _store_call(db, "create_seat", user.id, user.username, rep, career_week) is not _MISSING:
        return
    # SeatStore yok ya da uyeligi dusmus ama koltugu birakilmamis tutarsiz satir: kulupsuz, yeni katilim olarak kurulur
    row = existing
    if row is None:
        row = WorldManager(user_id=user.id, is_primary=False)
        db.add(row)
    row.display_name = user.username[:32]
    row.status = SeatStatus.ACTIVE.value
    row.team_id = None
    row.reputation = rep
    row.ready_career_week = None
    row.joined_career_week = career_week
    row.missed_deadlines = 0
    db.flush()


def _release_seat(db, user_id: int, status: str, rules: WorldRules, career_week: int) -> int | None:
    """Koltugun kulubu AI'ya gecer (koruma: career_week + protection_weeks). Birakilan kulup id'si."""
    protected_until = career_week + rules.protection_weeks if rules.protection_weeks > 0 else None
    store = _seat_store(db)
    if store is not None:
        try:
            seat = store.resolve(user_id)
            if seat is None or seat.is_primary or seat.user_id != user_id:
                return None
            return store.release(seat, status, protected_until if seat.team_id is not None else None)
        except NotImplementedError:
            pass
        except ValueError as exc:
            import seats

            if isinstance(exc, seats.SeatError):
                raise WorldError(str(exc)) from exc
            raise
    row = db.scalar(select(WorldManager).where(WorldManager.user_id == user_id,
                                               WorldManager.is_primary.is_(False)).with_for_update())
    if row is None:
        return None
    team_id = row.team_id
    if team_id is not None and protected_until is not None:
        team = db.get(Team, team_id, with_for_update=True)
        if team is not None:
            team.ai_protected_until = max(team.ai_protected_until or 0, protected_until)
    row.team_id = None
    row.status = status
    row.ready_career_week = None
    db.flush()
    return team_id


# ===========================================================================
# Kisisel dunya
# ===========================================================================

def _ensure_world_row(db, schema: str) -> World:
    """
    Semanin kaydi (yoksa kisisel). Sahip = accounts.users.career_schema'si bu sema olan hesap. Sahipsiz ya da
    bayat sahipli kisisel kayit onarilir; sahibin OWNER uyeligi yazilir. Satir islem sonuna kadar kilitli.
    """
    schema = database.valid_schema_name(schema)
    owner_id = db.scalar(select(User.id).where(User.career_schema == schema))
    db.execute(text(
        'INSERT INTO "accounts".worlds AS w (name, schema_name, kind, owner_user_id, visibility, max_managers, '
        "min_manager_level, status) VALUES (:name, :schema, 'PERSONAL', :owner, 'PRIVATE', 1, 1, 'ACTIVE') "
        "ON CONFLICT (schema_name) DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id "
        "WHERE w.owner_user_id IS NULL AND EXCLUDED.owner_user_id IS NOT NULL"
    ), {"name": PERSONAL_WORLD_NAME, "schema": schema, "owner": owner_id})
    world = db.scalar(select(World).where(World.schema_name == schema).with_for_update()
                      .execution_options(populate_existing=True))
    if owner_id is None:
        return world
    previous = world.owner_user_id
    if previous != owner_id and world.kind == _PERSONAL:
        previous_schema = db.scalar(select(User.career_schema).where(User.id == previous))
        if previous_schema != schema:           # eski sahip bu kariyeri artik tasimiyor: guncel sahibe onar
            world.owner_user_id = owner_id
            stale = _membership(db, world.id, previous, lock=True) if previous is not None else None
            if stale is not None and stale.role == _OWNER:
                stale.role = _MEMBER
                stale.status = MembershipStatus.LEFT.value
                stale.left_at = func.now()
            db.flush()
    if world.owner_user_id == owner_id:
        db.execute(text(
            'INSERT INTO "accounts".world_memberships AS m (world_id, user_id, role, status) '
            "VALUES (:w, :u, 'OWNER', 'ACTIVE') ON CONFLICT (world_id, user_id) DO UPDATE "
            "SET role = 'OWNER', status = 'ACTIVE', left_at = NULL WHERE m.role <> 'OWNER' OR m.status <> 'ACTIVE'"
        ), {"w": world.id, "u": owner_id})
    return world


def ensure_personal_world(session: AuthSession) -> WorldInfo:
    """
    (own txn) Hesabin kisisel kariyer kaydi: accounts.users.career_schema'nin dunyasi (hesap yoksa oturumun semasi,
    sahipsiz). Idempotent; sahipsiz kaydi onarir. Kariyer paylasilan dunyaya cevrildiyse o kayit doner.
    """
    with accounts._accounts_scope() as db:
        user = db.get(User, session.user_id)
        schema = user.career_schema if user is not None and user.career_schema else session.career_schema
        world = _ensure_world_row(db, schema)
        return _load_info(db, world.id, session.user_id)


def registry_schema_version(schema: str) -> int:
    """(own txn) Semanin kaydindaki schema_version (kayit yoksa sahibiyle kisisel kayit olusturulur)."""
    with accounts._accounts_scope() as db:
        world = db.scalar(select(World).where(World.schema_name == database.valid_schema_name(schema)))
        if world is None:
            world = _ensure_world_row(db, schema)
        return int(world.schema_version or 0)


def record_schema_version(schema: str, version: int) -> None:
    """(own txn) Semaya uygulanan database.SCHEMA_VERSION kaydedilir."""
    with accounts._accounts_scope() as db:
        db.execute(text('UPDATE "accounts".worlds SET schema_version = :v WHERE schema_name = :s '
                        "AND schema_version <> :v"),
                   {"v": int(version), "s": database.valid_schema_name(schema)})


def default_world(session: AuthSession) -> WorldContext:
    """
    (own txn) Girisin dunyasi: en son girilen (last_seen_at) aktif uyelik; hic yoksa kisisel kariyer.
    Semasi kaybolmus dunya atlanir. Hesabin kisisel kariyeri de yoksa NotAMemberError.
    """
    personal = ensure_personal_world(session)
    with accounts._accounts_scope() as db:
        rows = db.execute(
            select(World, WorldMembership.role)
            .join(WorldMembership, WorldMembership.world_id == World.id)
            .where(WorldMembership.user_id == session.user_id, WorldMembership.status == _ACTIVE,
                   World.status == WorldStatus.ACTIVE.value, WorldMembership.last_seen_at.is_not(None))
            .order_by(WorldMembership.last_seen_at.desc(), World.id.desc())
        ).all()
        for world, role in rows:
            if _schema_exists(db, world.schema_name):
                return _context(world, role, session.user_id)
        if personal.my_role is None:
            raise NotAMemberError(NOT_A_MEMBER)
        return _context(db.get(World, personal.id), personal.my_role, session.user_id)


def session_for(session: AuthSession, ctx: WorldContext) -> AuthSession:
    """Oturum secilen dunyaya baglanir: career_schema / world_id / world_kind (diger alanlar aynen)."""
    if ctx.user_id != session.user_id:
        raise WorldPermissionError(FOREIGN_CONTEXT)
    return dataclasses.replace(session, career_schema=database.valid_schema_name(ctx.schema),
                               world_id=ctx.world_id, world_kind=ctx.kind)


# ===========================================================================
# Listeler
# ===========================================================================

def list_my_worlds(user_id: int) -> list[WorldInfo]:
    """(own txn) Aktif uyelikler: once kisisel kariyer, sonra son girilenler. Kisisel kayit eksikse doldurulur."""
    with accounts._accounts_scope() as db:
        career = db.scalar(select(User.career_schema).where(User.id == user_id))
        if career is not None:
            has_row = db.scalar(select(World.id).join(WorldMembership, WorldMembership.world_id == World.id)
                                .where(World.schema_name == career, WorldMembership.user_id == user_id,
                                       WorldMembership.status == _ACTIVE))
            if has_row is None:
                _ensure_world_row(db, career)
        mine = aliased(WorldMembership)
        rows = db.execute(
            _info_select(user_id)
            .join(mine, (mine.world_id == World.id) & (mine.user_id == user_id))
            .where(mine.status == _ACTIVE)
            .order_by((World.kind == _PERSONAL).desc(), mine.last_seen_at.desc().nulls_last(), World.id)
        ).all()
        return [_world_info(*row) for row in rows]


def list_public_worlds(user_id: int, query: str = "", limit: int = 50) -> list[WorldInfo]:
    """
    (own txn) Katilinabilir acik dunyalar: SHARED + PUBLIC + ACTIVE, bos koltuk var; kullanicinin zaten aktif uyesi
    oldugu ya da atildigi dunyalar listelenmez. Davet kodu hic gosterilmez.
    """
    try:
        limit = max(1, min(int(limit), PUBLIC_LIST_MAX))
    except (TypeError, ValueError):
        limit = 50
    needle = " ".join(str(query or "").split())[:NAME_MAX]
    with accounts._accounts_scope() as db:
        counted = aliased(WorldMembership)
        active = (select(func.count(counted.id))
                  .where(counted.world_id == World.id, counted.status == _ACTIVE)
                  .correlate(World).scalar_subquery())
        blocked = aliased(WorldMembership)
        excluded = (select(blocked.id)
                    .where(blocked.world_id == World.id, blocked.user_id == user_id,
                           blocked.status.in_((_ACTIVE, MembershipStatus.KICKED.value)))
                    .correlate(World).exists())
        owner = select(User.username).where(User.id == World.owner_user_id).correlate(World).scalar_subquery()
        stmt = (select(World, active.label("active"), null().label("role"), owner.label("owner_name"))
                .where(World.kind == _SHARED, World.visibility == WorldVisibility.PUBLIC.value,
                       World.status == WorldStatus.ACTIVE.value, active < World.max_managers, ~excluded))
        if needle:
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(World.name.ilike(f"%{escaped}%", escape="\\"))
        rows = db.execute(stmt.order_by(active.desc(), World.created_at.desc(), World.id.desc()).limit(limit)).all()
        return [_world_info(*row) for row in rows]


# ===========================================================================
# Paylasilan dunya kurma
# ===========================================================================

def _reserve_world_id() -> int:
    accounts._ensure_accounts()
    with database.engine.begin() as conn:
        return int(conn.scalar(text("SELECT nextval(pg_get_serial_sequence('accounts.worlds', 'id'))")))


def _free_world_schema(world_id: int) -> str:
    base = f"{WORLD_SCHEMA_PREFIX}{world_id}"
    with accounts._accounts_scope() as db:
        for suffix in range(1, _MAX_SCHEMA_SUFFIX + 1):
            name = database.valid_schema_name(base if suffix == 1 else f"{base}_{suffix}")
            taken = _schema_exists(db, name) or db.scalar(select(World.id).where(World.schema_name == name))
            if not taken:
                return name
    raise WorldError(CREATE_FAILED)


def _configure_world(schema: str, user_id: int, username: str, rules: WorldRules, reputation_value: float) -> None:
    """Yeni kurulan dunya: sahip (birincil koltuk), kurallar, tanınırlık, kariyer modu, ilk tur."""
    from career_manager import CareerManager

    with database.career_context(schema), database.session_scope() as db:
        state = db.get(GameState, 1, with_for_update=True)
        if state is None:
            raise WorldError(CREATE_FAILED)
        now = datetime.now(timezone.utc)
        state.user_id = user_id
        state.world_rules = rules.to_dict()
        state.manager_reputation = reputation.clamp(reputation_value)
        state.turn_opened_at = now
        state.turn_deadline_at = now + timedelta(hours=rules.deadline_hours) if rules.auto_advance else None
        db.flush()
        CareerManager(db).set_game_mode(GameMode.CAREER)       # paylasilan dunya yalnizca kariyer modunda
        _ensure_primary_seat(db, user_id, username, _career_week(state))


def _discard_world(schema: str, world_id: int) -> None:
    try:
        database.drop_career_schema(schema)
    except Exception:
        pass
    try:
        with accounts._accounts_scope() as db:
            db.execute(World.__table__.delete().where(World.id == world_id))
    except Exception:
        pass


def create_world(
    user_id: int,
    name: str,
    *,
    visibility: str,
    max_managers: int,
    min_manager_level: int,
    rules: WorldRules,
    world_seed: int | None = None,
    source: str = "auto",
) -> WorldContext:
    """
    (own txn'ler) Yeni paylasilan dunya: 'world_<id>' semasi kurulur ve seed edilir (source: seed.py gibi),
    game_state.world_rules = rules (shared=True, max_seats=max_managers), sahip birincil koltuk + OWNER uyeligi.
    world_seed verilmezse rastgele secilir ve kayda yazilir. Basarisizlikta iz kalmaz (WorldError).
    """
    name = clean_world_name(name)
    visibility = _clean_visibility(visibility)
    max_managers = _clean_int(max_managers, MIN_SHARED_MANAGERS, MAX_MANAGERS, BAD_MAX)
    min_level = _clean_int(min_manager_level, 1, reputation.MAX_LEVEL, BAD_LEVEL)
    if not isinstance(rules, WorldRules):
        raise WorldError(BAD_RULES)
    rules = dataclasses.replace(rules, shared=True, max_seats=max_managers)
    problems = rules.validate()
    if problems:
        raise WorldError(problems[0])
    if world_seed is not None:
        world_seed = _clean_int(world_seed, 0, MAX_WORLD_SEED, BAD_SEED)
    rng_seed = world_seed if world_seed is not None else secrets.randbelow(MAX_WORLD_SEED)

    with accounts._advisory_lock(_LOCK_WORLD_CREATE, str(int(user_id))):
        with accounts._accounts_scope() as db:
            user = db.get(User, user_id)
            if user is None:
                raise WorldError(ACCOUNT_NOT_FOUND)
            if _owned_shared_count(db, user_id) >= MAX_OWNED_WORLDS:
                raise WorldError(TOO_MANY_WORLDS.format(n=MAX_OWNED_WORLDS))
            username = user.username
            owner_reputation = _profile_row(db, user_id).reputation
        world_id = _reserve_world_id()
        schema = _free_world_schema(world_id)
        try:
            accounts._build_world(schema, rng_seed, source)
            _configure_world(schema, user_id, username, rules, owner_reputation)
            with accounts._accounts_scope() as db:
                world = World(id=world_id, name=name, schema_name=schema, kind=_SHARED, owner_user_id=user_id,
                              visibility=visibility,
                              invite_code=None if visibility == _PRIVATE else _new_invite_code(db),
                              max_managers=max_managers, min_manager_level=min_level,
                              status=WorldStatus.ACTIVE.value, world_seed=rng_seed,
                              schema_version=database.SCHEMA_VERSION)
                db.add(world)
                db.flush()
                db.add(WorldMembership(world_id=world_id, user_id=user_id, role=_OWNER, status=_ACTIVE,
                                       last_seen_at=func.now()))
                db.flush()
                ctx = _context(world, _OWNER, user_id)
        except BaseException as exc:
            _discard_world(schema, world_id)
            if not isinstance(exc, Exception) or isinstance(exc, WorldError):
                raise
            raise WorldError(accounts._failure_message(exc, CREATE_FAILED)) from exc
    return ctx


# ===========================================================================
# Katilim, giris, ayrilma
# ===========================================================================

def _join(user_id: int, world_id: int, schema: str, check: Callable[[World], None], not_found: str) -> WorldContext:
    """Kapasite / seviye / atilma denetimi ve uyelik + koltuk tek islemde (dunya satiri FOR UPDATE)."""
    with accounts._accounts_scope() as db:
        if not _schema_exists(db, schema):
            raise WorldNotFound(not_found)
    with _world_txn(schema) as db:
        world = db.scalar(select(World).where(World.id == world_id).with_for_update()
                          .execution_options(populate_existing=True))
        if world is None or world.schema_name != schema:
            raise WorldNotFound(not_found)
        check(world)
        user = db.get(User, user_id)
        if user is None:
            raise WorldError(ACCOUNT_NOT_FOUND)
        membership = _membership(db, world_id, user_id, lock=True)
        if membership is not None and membership.status == _ACTIVE:
            membership.last_seen_at = func.now()
            return _context(world, membership.role, user_id)
        if membership is not None and membership.status == MembershipStatus.KICKED.value:
            raise WorldPermissionError(KICKED_REJOIN)
        if _active_count(db, world_id) >= world.max_managers:
            raise WorldFullError(WORLD_FULL)
        profile_row = _profile_row(db, user_id)
        mine = reputation.level(profile_row.reputation)
        if mine.level < world.min_manager_level:
            title = reputation.MANAGER_LEVELS[world.min_manager_level - 1][1]
            raise LevelTooLowError(LEVEL_TOO_LOW.format(need=world.min_manager_level, title=title, mine=mine.level))
        state = db.get(GameState, 1)
        if state is None:
            raise WorldNotFound(not_found)
        if membership is None:
            db.add(WorldMembership(world_id=world_id, user_id=user_id, role=_MEMBER, status=_ACTIVE,
                                   last_seen_at=func.now()))
        else:                                   # ayrilip geri donen menajer
            membership.status = _ACTIVE
            membership.role = _MEMBER
            membership.left_at = None
            membership.joined_at = func.now()
            membership.last_seen_at = func.now()
        db.flush()
        _add_seat(db, user, profile_row.reputation, _career_week(state))
        return _context(world, _MEMBER, user_id)


def join_by_code(user_id: int, code: str) -> WorldContext:
    """(own txn) Davet koduyla katilim. Yok / dondurulmus / ozel / arsiv kod: tek genel hata (WorldNotFound)."""
    normalized = _normalize_code(code)
    if normalized is None:
        raise WorldNotFound(INVALID_CODE)
    with accounts._accounts_scope() as db:
        found = db.execute(select(World.id, World.schema_name).where(World.invite_code == normalized)).first()
    if found is None:
        raise WorldNotFound(INVALID_CODE)

    def check(world: World) -> None:
        if (world.invite_code != normalized or world.kind != _SHARED or world.status != WorldStatus.ACTIVE.value
                or world.visibility == _PRIVATE):
            raise WorldNotFound(INVALID_CODE)

    return _join(user_id, found.id, found.schema_name, check, INVALID_CODE)


def join_public(user_id: int, world_id: int) -> WorldContext:
    """(own txn) Acik dunyalar listesinden katilim (yalnizca SHARED + PUBLIC + ACTIVE)."""
    world = _detached_world(world_id)
    if world is None:
        raise WorldNotFound(NOT_JOINABLE)

    def check(row: World) -> None:
        if (row.kind != _SHARED or row.visibility != WorldVisibility.PUBLIC.value
                or row.status != WorldStatus.ACTIVE.value):
            raise WorldNotFound(NOT_JOINABLE)

    check(world)
    return _join(user_id, world_id, world.schema_name, check, NOT_JOINABLE)


def enter_world(user_id: int, world_id: int) -> WorldContext:
    """(own txn) Dunyaya giris: yalnizca ACTIVE uyelik (LEFT / KICKED / uye degil -> NotAMemberError); last_seen_at."""
    with accounts._accounts_scope() as db:
        world = db.get(World, world_id)
        if world is None:
            raise NotAMemberError(NOT_A_MEMBER)
        membership = _membership(db, world_id, user_id, lock=True)
        if membership is None:
            career = db.scalar(select(User.career_schema).where(User.id == user_id))
            if career == world.schema_name:      # kisisel kaydi henuz doldurulmamis kendi kariyeri
                _ensure_world_row(db, career)
                membership = _membership(db, world_id, user_id, lock=True)
        if membership is None or membership.status != _ACTIVE:
            kicked = membership is not None and membership.status == MembershipStatus.KICKED.value
            raise NotAMemberError(REMOVED if kicked else NOT_A_MEMBER)
        if world.status != WorldStatus.ACTIVE.value:
            raise WorldError(ARCHIVED)
        if not _schema_exists(db, world.schema_name):
            raise WorldNotFound(NOT_JOINABLE)
        membership.last_seen_at = func.now()
        return _context(world, membership.role, user_id)


def leave_world(user_id: int, world_id: int) -> None:
    """(own txn) Sahip / kisisel kariyer ayrilamaz; ayrilanin uyeligi LEFT, koltugu birakilir (kulup AI'ya, korumali)."""
    world = _detached_world(world_id)
    if world is None:
        raise NotAMemberError(NOT_A_MEMBER)
    if world.kind == _PERSONAL:
        raise WorldPermissionError(PERSONAL_CANNOT_LEAVE)
    if world.owner_user_id == user_id:
        raise WorldPermissionError(OWNER_CANNOT_LEAVE)
    with accounts._accounts_scope() as db:
        schema_ok = _schema_exists(db, world.schema_name)
    with _world_txn(world.schema_name, lock=schema_ok) as db:
        membership = _active_membership(db, world_id, user_id, lock=True)
        if membership.role == _OWNER:
            raise WorldPermissionError(OWNER_CANNOT_LEAVE)
        membership.status = MembershipStatus.LEFT.value
        membership.role = _MEMBER
        membership.left_at = func.now()
        db.flush()
        if not schema_ok:
            return
        state = db.get(GameState, 1)
        if state is None:
            return
        rules = WorldRules.from_dict(state.world_rules)
        team_id = _release_seat(db, user_id, SeatStatus.LEFT.value, rules, _career_week(state))
        _audit(db, world.schema_name, WorldEventKind.RELEASE, user_id,
               {"reason": "LEFT", "user_id": user_id, "team_id": team_id})


def check_membership(user_id: int, world_id: int) -> MembershipInfo:
    """(own txn) ACTIVE uyelik (aktif dunyada); yoksa NotAMemberError. Her dunya callback'inde calisir: tek sorgu."""
    with accounts._accounts_scope() as db:
        row = db.execute(
            select(WorldMembership, User.username, World.status)
            .join(User, User.id == WorldMembership.user_id)
            .join(World, World.id == WorldMembership.world_id)
            .where(WorldMembership.world_id == world_id, WorldMembership.user_id == user_id)
        ).first()
    if row is None:
        raise NotAMemberError(NOT_A_MEMBER)
    membership, username, world_status = row
    if membership.status != _ACTIVE:
        raise NotAMemberError(REMOVED if membership.status == MembershipStatus.KICKED.value else NOT_A_MEMBER)
    if world_status != WorldStatus.ACTIVE.value:
        raise NotAMemberError(ARCHIVED)
    return _membership_info(membership, username, membership.team_name_cache)


def _membership_info(membership: WorldMembership, username: str, team_name: str | None) -> MembershipInfo:
    return MembershipInfo(world_id=membership.world_id, user_id=membership.user_id, username=username,
                          role=membership.role, status=membership.status, joined_at=membership.joined_at,
                          last_seen_at=membership.last_seen_at, team_name=team_name)


def _team_names(db, schema: str) -> dict[int, str | None] | None:
    """Kullanici id -> yonettigi kulup (dunya semasindan canli). Sema/tablo yoksa None (onbellek kullanilir)."""
    schema = database.valid_schema_name(schema)
    if not (_table_exists(db, schema, "game_state") and _table_exists(db, schema, "teams")):
        return None
    names: dict[int, str | None] = {}
    if _table_exists(db, schema, "world_managers"):
        for uid, name in db.execute(text(
            f'SELECT wm.user_id, t.name FROM "{schema}".world_managers wm '
            f'LEFT JOIN "{schema}".teams t ON t.id = wm.team_id '
            "WHERE wm.user_id IS NOT NULL AND NOT wm.is_primary"
        )):
            names[uid] = name
    primary = db.execute(text(
        f'SELECT gs.user_id, t.name FROM "{schema}".game_state gs '
        f'LEFT JOIN "{schema}".teams t ON t.id = gs.user_team_id WHERE gs.id = 1'
    )).first()
    if primary is not None and primary.user_id is not None:
        names[primary.user_id] = primary.name
    return names


def members(actor_id: int, world_id: int) -> list[MembershipInfo]:
    """
    (own txn) Dunyanin menajerleri (kulupleri canli). Uye yalnizca aktif uyeleri, sahip / yonetici ayrilan ve
    atilanlari da gorur. Istek sahibi aktif uye degilse NotAMemberError.
    """
    with accounts._accounts_scope() as db:
        actor = _active_membership(db, world_id, actor_id)
        world = db.get(World, world_id)
        stmt = (select(WorldMembership, User.username)
                .join(User, User.id == WorldMembership.user_id)
                .where(WorldMembership.world_id == world_id))
        if actor.role not in ADMIN_ROLES:
            stmt = stmt.where(WorldMembership.status == _ACTIVE)
        role_order = case((WorldMembership.role == _OWNER, 0), (WorldMembership.role == _ADMIN, 1), else_=2)
        rows = db.execute(stmt.order_by((WorldMembership.status != _ACTIVE), role_order,
                                        WorldMembership.joined_at, WorldMembership.id)).all()
        teams = _team_names(db, world.schema_name) if _schema_exists(db, world.schema_name) else None
        return [_membership_info(m, username, m.team_name_cache if teams is None else teams.get(m.user_id))
                for m, username in rows]


# ===========================================================================
# Yonetim
# ===========================================================================

def set_role(actor_id: int, world_id: int, target_user_id: int, role: str) -> None:
    """(own txn) Rol matrisi: sahip -> ADMIN/MEMBER (sahip olmayan aktif uye); yonetici yalnizca kendini MEMBER yapar."""
    wanted = role.strip().upper() if isinstance(role, str) else getattr(role, "value", None)
    if wanted == _OWNER:
        raise WorldPermissionError(NO_TRANSFER)
    if wanted not in (_ADMIN, _MEMBER):
        raise WorldError(BAD_ROLE)
    with accounts._accounts_scope() as db:
        world = db.get(World, world_id)
        actor = _active_membership(db, world_id, actor_id)
        if world.kind == _PERSONAL:
            raise WorldPermissionError(OWNER_ROLE_FIXED)
        if actor.role not in ADMIN_ROLES:
            raise WorldPermissionError(ADMIN_ONLY)
        target = _membership(db, world_id, target_user_id, lock=True)
        if target is None or target.status != _ACTIVE:
            raise NotAMemberError(TARGET_NOT_MEMBER)
        if target.role == _OWNER:
            raise WorldPermissionError(OWNER_ROLE_FIXED)
        allowed = actor.role == _OWNER or (actor_id == target_user_id and wanted == _MEMBER)
        if not allowed:
            raise WorldPermissionError(OWNER_ONLY)
        if target.role == wanted:
            return
        previous = target.role
        target.role = wanted
        db.flush()
        _audit(db, world.schema_name, WorldEventKind.RULES, actor_id,
               {"action": "ROLE", "user_id": target_user_id, "from": previous, "to": wanted})


def mark_membership(db, world_id: int, user_id: int, status: str) -> None:
    """
    Cagiranin islemine katilir (commit etmez): uyelik ACTIVE / LEFT / KICKED. Sahip LEFT/KICKED yapilamaz;
    ayrilan ya da atilan yonetici MEMBER'a iner. (Koltugu birakmak cagiranin isidir: seats / world_manager.)
    """
    wanted = status.strip().upper() if isinstance(status, str) else getattr(status, "value", None)
    if wanted not in {s.value for s in MembershipStatus}:
        raise WorldError(BAD_STATUS)
    membership = _membership(db, world_id, user_id, lock=True)
    if membership is None:
        raise NotAMemberError(TARGET_NOT_MEMBER)
    if wanted != _ACTIVE and membership.role == _OWNER:
        raise WorldPermissionError(OWNER_CANNOT_LEAVE)
    membership.status = wanted
    if wanted == _ACTIVE:
        membership.left_at = None
    else:
        membership.left_at = func.now()
        if membership.role == _ADMIN:
            membership.role = _MEMBER
    db.flush()


def rotate_invite_code(actor_id: int, world_id: int) -> str:
    """(own txn) Yeni davet kodu; eskisi aninda gecersiz. Yalnizca sahip / yonetici, davetli ya da acik dunyada."""
    with accounts._accounts_scope() as db:
        world = db.scalar(select(World).where(World.id == world_id).with_for_update())
        if world is None:
            raise NotAMemberError(NOT_A_MEMBER)
        actor = _active_membership(db, world_id, actor_id)
        if actor.role not in ADMIN_ROLES:
            raise WorldPermissionError(ADMIN_ONLY)
        if world.kind != _SHARED:
            raise WorldError(NO_INVITE_PERSONAL)
        if world.visibility == _PRIVATE:
            raise WorldError(NO_INVITE_PRIVATE)
        world.invite_code = _new_invite_code(db, previous=world.invite_code)
        db.flush()
        _audit(db, world.schema_name, WorldEventKind.RULES, actor_id, {"action": "INVITE_ROTATED"})
        return world.invite_code


_META_FIELDS = frozenset({"name", "visibility", "max_managers", "min_manager_level", "status"})


def update_world_meta(actor_id: int, world_id: int, **fields) -> WorldInfo:
    """
    (own txn) Ust bilgi (her an): name, visibility, max_managers, min_manager_level (sahip / yonetici), status
    (ACTIVE / ARCHIVED, yalnizca sahip). Kisisel kariyerde yalnizca ad. Ozel dunyada davet kodu silinir,
    davetli / acik olunca yoksa uretilir. max_managers aktif menajer sayisindan dusuk olamaz; kurallardaki
    max_seats esitlenir.
    """
    unknown = sorted(set(fields) - _META_FIELDS)
    if unknown:
        raise WorldError(UNKNOWN_FIELDS.format(fields=", ".join(unknown)))
    with accounts._accounts_scope() as db:
        world = db.scalar(select(World).where(World.id == world_id).with_for_update())
        if world is None:
            raise NotAMemberError(NOT_A_MEMBER)
        actor = _active_membership(db, world_id, actor_id)
        if actor.role not in ADMIN_ROLES:
            raise WorldPermissionError(ADMIN_ONLY)
        if world.kind == _PERSONAL and set(fields) - {"name"}:
            raise WorldPermissionError(PERSONAL_META_ONLY_NAME)
        changes: dict[str, object] = {}
        if "name" in fields:
            changes["name"] = clean_world_name(fields["name"])
        if "visibility" in fields:
            changes["visibility"] = _clean_visibility(fields["visibility"])
        if "max_managers" in fields:
            changes["max_managers"] = _clean_int(fields["max_managers"], MIN_SHARED_MANAGERS, MAX_MANAGERS, BAD_MAX)
        if "min_manager_level" in fields:
            changes["min_manager_level"] = _clean_int(fields["min_manager_level"], 1, reputation.MAX_LEVEL,
                                                      BAD_LEVEL)
        if "status" in fields:
            status = fields["status"]
            status = status.strip().upper() if isinstance(status, str) else getattr(status, "value", None)
            if status not in {s.value for s in WorldStatus}:
                raise WorldError(BAD_WORLD_STATUS)
            if actor.role != _OWNER:
                raise WorldPermissionError(OWNER_ONLY)
            changes["status"] = status
        if "max_managers" in changes:
            active = _active_count(db, world_id)
            if changes["max_managers"] < active:
                raise WorldError(MAX_BELOW_ACTIVE.format(active=active))
        applied = {key: value for key, value in changes.items() if getattr(world, key) != value}
        for key, value in applied.items():
            setattr(world, key, value)
        if "visibility" in applied:
            if world.visibility == _PRIVATE:
                world.invite_code = None
            elif world.invite_code is None:
                world.invite_code = _new_invite_code(db)
        db.flush()
        if "max_managers" in applied:
            _sync_max_seats(db, world.schema_name, world.max_managers)
        if applied:
            _audit(db, world.schema_name, WorldEventKind.RULES, actor_id,
                   {"action": "META", "changes": sorted(applied)})
        return _load_info(db, world_id, actor_id)


def _sync_max_seats(db, schema: str, max_managers: int) -> None:
    """Paylasilan dunyanin kurallarindaki max_seats kayittaki menajer sinirina esitlenir (eski kurallara dokunulmaz)."""
    if not _table_exists(db, schema, "game_state"):
        return
    db.execute(text(
        f'UPDATE "{database.valid_schema_name(schema)}".game_state '
        "SET world_rules = jsonb_set(world_rules, '{max_seats}', to_jsonb(CAST(:n AS integer))) "
        "WHERE id = 1 AND world_rules <> CAST('{}' AS jsonb)"
    ), {"n": int(max_managers)})


def convert_personal_to_shared(actor_id: int, world_id: int, name: str, visibility: str,
                               max_managers: int) -> WorldInfo:
    """
    (own txn) Kisisel kariyer paylasilan dunyaya cevrilir; sema ve kayit aynen kalir. Yalnizca sahip.
    world_rules: mevcut kurallar + shared=True, max_seats; sahip birincil koltuk olur; turnuva modu cevrilemez.
    """
    name = clean_world_name(name)
    visibility = _clean_visibility(visibility)
    max_managers = _clean_int(max_managers, MIN_SHARED_MANAGERS, MAX_MANAGERS, BAD_MAX)
    detached = _detached_world(world_id)
    if detached is None:
        raise NotAMemberError(NOT_A_MEMBER)
    with accounts._accounts_scope() as db:
        if not _schema_exists(db, detached.schema_name):
            raise WorldNotFound(NOT_JOINABLE)
    from career_manager import CareerManager

    with _world_txn(detached.schema_name) as db:
        world = db.scalar(select(World).where(World.id == world_id).with_for_update()
                          .execution_options(populate_existing=True))
        if world is None or world.owner_user_id != actor_id:
            raise WorldPermissionError(OWNER_ONLY)
        if world.kind != _PERSONAL:
            raise WorldError(ALREADY_SHARED)
        if world.status != WorldStatus.ACTIVE.value:
            raise WorldError(ARCHIVED)
        if _owned_shared_count(db, actor_id) >= MAX_OWNED_WORLDS:
            raise WorldError(TOO_MANY_WORLDS.format(n=MAX_OWNED_WORLDS))
        user = db.get(User, actor_id)
        state = db.get(GameState, 1, with_for_update=True)
        if user is None or state is None:
            raise WorldNotFound(NOT_JOINABLE)
        if state.game_mode is GameMode.TOURNAMENT:
            raise WorldError(TOURNAMENT_CANNOT_SHARE)
        rules = dataclasses.replace(WorldRules.from_dict(state.world_rules), shared=True, max_seats=max_managers)
        problems = rules.validate()
        if problems:
            raise WorldError(problems[0])
        state.world_rules = rules.to_dict()
        state.user_id = actor_id
        if state.turn_opened_at is None:
            now = datetime.now(timezone.utc)
            state.turn_opened_at = now
            state.turn_deadline_at = now + timedelta(hours=rules.deadline_hours) if rules.auto_advance else None
        db.flush()
        if state.game_mode is None:
            cm = CareerManager(db)
            try:
                cm.set_game_mode(GameMode.CAREER)
            except ValueError:                  # sezon basladi: secilmemis mod zaten kariyer gibi oynuyordu
                state.game_mode = GameMode.CAREER
                db.flush()
        _ensure_primary_seat(db, actor_id, user.username, _career_week(state))
        world.kind = _SHARED
        world.name = name
        world.visibility = visibility
        world.max_managers = max_managers
        world.invite_code = None if visibility == _PRIVATE else _new_invite_code(db)
        db.flush()
        db.execute(text(
            'INSERT INTO "accounts".world_memberships AS m (world_id, user_id, role, status) '
            "VALUES (:w, :u, 'OWNER', 'ACTIVE') ON CONFLICT (world_id, user_id) DO UPDATE "
            "SET role = 'OWNER', status = 'ACTIVE', left_at = NULL"
        ), {"w": world_id, "u": actor_id})
        _audit(db, world.schema_name, WorldEventKind.RULES, actor_id, {"action": "CONVERTED_TO_SHARED"})
        return _load_info(db, world_id, actor_id)


# ===========================================================================
# Menajer profili (hesap capinda)
# ===========================================================================

def profile(user_id: int) -> ManagerProfile:
    """(own txn) Hesabin menajer profili (yoksa varsayilanlarla olusturulur). Donen nesne oturumdan ayridir."""
    with accounts._accounts_scope() as db:
        if db.get(User, user_id) is None:
            raise WorldError(ACCOUNT_NOT_FOUND)
        row = _profile_row(db, user_id)
        db.expunge(row)
        return row


def record_season(user_id: int, reputation: float, titles: int) -> None:
    """(own txn) Sezon sonu: profil tanınırlığı guncellenir, en iyi seviye / sezon / kupa sayaclari artar."""
    import reputation as reputation_rules

    try:
        value = float(reputation)
    except (TypeError, ValueError):
        raise WorldError(BAD_SEASON) from None
    if not math.isfinite(value):
        raise WorldError(BAD_SEASON)
    value = reputation_rules.clamp(value)
    won = _clean_int(titles, 0, 10_000, BAD_SEASON)
    best = reputation_rules.level(value).level
    with accounts._accounts_scope() as db:
        if db.get(User, user_id) is None:
            raise WorldError(ACCOUNT_NOT_FOUND)
        db.execute(text(
            'INSERT INTO "accounts".manager_profiles AS p (user_id, reputation, best_level, seasons_completed, '
            "titles, updated_at) VALUES (:u, :rep, :best, 1, :titles, now()) "
            "ON CONFLICT (user_id) DO UPDATE SET reputation = EXCLUDED.reputation, "
            "best_level = GREATEST(p.best_level, EXCLUDED.best_level), "
            "seasons_completed = p.seasons_completed + 1, titles = p.titles + EXCLUDED.titles, updated_at = now()"
        ), {"u": user_id, "rep": value, "best": best, "titles": won})
