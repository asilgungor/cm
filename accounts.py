"""
accounts.py
===========
Hesap kontrolcusu (10. Asama): kayit, giris ve kullanicinin kariyer semasi.
Streamlit bilmez. Parola kurallari ve ozetleme auth.py'dedir (saf).

Kariyer atamasi (kayitta ya da kariyeri olmayan eski bir hesabin ilk girisinde):
    1) 'public' semasinda dunyasi kurulmus, SAHIPSIZ eski tek kisilik kariyer varsa kullanici
       onu devralir (kayit kaybolmaz): users.career_schema = 'public' + game_state.user_id.
       Sahipsiz = game_state.user_id bos VE hicbir hesapta career_schema = 'public' yok
       ('python seed.py' dunyayi yeniden kurunca user_id bosalir ama sahibi hala vardir).
       Iki kaydin ayni anda devralmasini public.game_state satirindaki FOR UPDATE kilidi onler:
       kilidi bekleyen islem satiri yeniden okur, sahipli gorunce kendi kariyerini kurar.
       career_schema sutunundaki benzersiz kisit ikinci emniyet kilididir.
    2) Aksi halde 'career_<id>' semasi kurulur: init_db + seed.seed, sonra game_state.user_id.
    Sahiplik isareti: esas kaynak accounts.users.career_schema'dir. authenticate ve
    ensure_career_ready, sahibinin kariyerinde game_state.user_id bos kalmissa geri baglar.

Atomiklik (cagiranin gozunden):
    Yeni kullanicinin satiri, kariyeri TAMAMEN hazir olduktan sonra game_state.user_id ile
    birlikte TEK islemde yazilir; o ana kadar baska oturumlar yarim hesap gormez. Kimlik (id)
    sequence'ten onceden ayrilir, sema adi ona gore verilir. Kurulum ya da son islem basarisiz
    olursa sema silinir, kullanici satiri olusmaz (commit belirsiz kalmissa id ile yine silinir)
    ve AccountError firlatilir.
    Neden tek buyuk islem degil: yeni semadaki game_state tablosunun accounts.users'a FK'si
    CREATE TABLE sirasinda users tablosunda SHARE ROW EXCLUSIVE kilit ister; kullanici satirini
    ekleyip acik bekleyen islem bu DDL'i sonsuza dek bekletirdi (Python tarafinda kilitlenme).

Eszamanlilik:
    Ayni kullanici adiyla es zamanli kayitlar ve kariyeri olmayan ayni hesabin es zamanli
    girisleri oturum seviyesinde advisory lock ile siralanir; pahali kurulum iki kez yapilmaz.
    Kilit ayri bir baglantida tutulur, tablo kilidi almaz; DDL/seed ile cakismaz.

Hesap sorgulari aktif kariyer baglamindan bagimsizdir: User modeli 'accounts' semasini acikca
tasir, ham SQL tablolari semayla niteler ve web oturum cozucusu hesap islemlerinde cagrilmaz.
"""

from __future__ import annotations

import secrets
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import auth
import database
from models import User


class AccountError(ValueError):
    """Kullaniciya gosterilebilir hesap hatasi (mesaj Turkce)."""


class AccountValidationError(AccountError, auth.AuthError):
    """Kullanici adi/parola kural ihlali: AccountError ya da AuthError olarak yakalanabilir."""


@dataclass(frozen=True)
class AuthSession:
    user_id: int
    username: str
    career_schema: str


INVALID_CREDENTIALS = "Kullanıcı adı veya parola hatalı."
ACCOUNT_LOCKED = "Çok fazla hatalı giriş denemesi: hesap geçici olarak kilitlendi. {minutes} dk sonra tekrar dene."
MAX_FAILED_LOGINS = 5                            # art arda hatali parola -> hesap kilidi
LOCKOUT_MINUTES = 5
USERNAME_TAKEN = "Bu kullanıcı adı zaten alınmış."
REGISTER_FAILED = "Kariyer hazırlanamadı, kayıt tamamlanmadı. Lütfen tekrar deneyin."
CAREER_FAILED = "Kariyer hazırlanamadı. Lütfen tekrar deneyin."

CAREER_SCHEMA_PREFIX = "career_"
_USERNAME_INDEX = "uq_user_username_lower"       # models.User: lower(username) benzersiz
_LOCK_USERNAME = 10_001                          # advisory lock ad alani: kayit (kullanici adi)
_LOCK_USER = 10_002                              # advisory lock ad alani: kariyer kurulumu (id)
_MAX_SCHEMA_SUFFIX = 50

_accounts_ready = False

# attach(db, sema) -> kullanici id: kullanici satirini semaya baglar (ekler ya da gunceller)
Attach = Callable[[Session, str], int]


# ---------------------------------------------------------------------------
# Altyapi
# ---------------------------------------------------------------------------

def _ensure_accounts() -> None:
    """accounts semasi/tablosu (surec basina bir kez)."""
    global _accounts_ready
    if not _accounts_ready:
        database.init_accounts()
        _accounts_ready = True


@contextmanager
def _accounts_scope() -> Iterator[Session]:
    """
    Hesap islemi. Baglam acikca 'public'e sabitlenir: web oturum cozucusu cagrilmaz ve baska
    bir kariyerin baglami sizmaz. Hesap sorgulari semayi zaten nitelendirir.
    """
    _ensure_accounts()
    with database.career_context(database.LEGACY_CAREER_SCHEMA), database.session_scope() as db:
        yield db


@contextmanager
def _advisory_lock(namespace: int, key: str) -> Iterator[None]:
    """Oturum seviyesinde PostgreSQL advisory lock; ayri baglantida tutulur."""
    conn = database.engine.connect()
    params = {"ns": namespace, "key": key}
    try:
        conn.execute(text("SELECT pg_advisory_lock(:ns, hashtext(:key))"), params)
        conn.commit()                       # kilit oturumda kalir; acik islem birakilmaz
        try:
            yield
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:ns, hashtext(:key))"), params)
                conn.commit()
            except Exception:
                conn.invalidate()           # baglanti kapaninca kilit PostgreSQL'de duser
    finally:
        conn.close()


def _schema_exists(schema: str) -> bool:
    with database.engine.connect() as conn:
        return bool(conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)"), {"s": schema}
        ))


def _is_username_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == _USERNAME_INDEX


def _username_taken(name: str) -> bool:
    with _accounts_scope() as db:
        return db.scalar(
            select(User.id).where(func.lower(User.username) == func.lower(name))
        ) is not None


# ---------------------------------------------------------------------------
# Kariyer atamasi
# ---------------------------------------------------------------------------

def _new_user(name: str, password_hash: str, user_id: int | None = None) -> Attach:
    def attach(db: Session, schema: str) -> int:
        user = User(username=name, password_hash=password_hash, career_schema=schema,
                    last_login_at=func.now())
        if user_id is not None:
            user.id = user_id
        db.add(user)
        db.flush()
        return user.id
    return attach


def _existing_user(user_id: int) -> Attach:
    def attach(db: Session, schema: str) -> int:
        user = db.get(User, user_id, with_for_update=True)
        if user is None:                    # hesap bu arada silinmis
            raise AccountError(INVALID_CREDENTIALS)
        user.career_schema = schema
        db.flush()
        return user.id
    return attach


def _legacy_world_available() -> bool:
    """
    'public'te kurulu dunya tablolari var mi? (kilitsiz on kontrol). Eski semada sahiplik
    sutunu yoksa once eklenir (upgrade_schema yalnizca EKLER, kayit silinmez).
    """
    with database.engine.connect() as conn:
        if not conn.scalar(text(
            "SELECT to_regclass('public.game_state') IS NOT NULL "
            "AND to_regclass('public.teams') IS NOT NULL"
        )):
            return False
        has_owner = conn.scalar(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'game_state' AND column_name = 'user_id')"
        ))
    if not has_owner:
        with database.career_context(database.LEGACY_CAREER_SCHEMA):
            database.upgrade_schema()
    return True


def _try_claim_legacy(attach: Attach) -> int | None:
    """Sahipsiz eski kariyeri devralir; devralinamiyorsa hicbir sey yazmadan None."""
    legacy = database.LEGACY_CAREER_SCHEMA
    if not _legacy_world_available():
        return None
    with _accounts_scope() as db:
        # Kilit: es zamanli ikinci kayit burada bekler, sonra guncel user_id'yi gorur
        row = db.execute(text('SELECT user_id FROM "public".game_state WHERE id = 1 FOR UPDATE')).first()
        if row is None or row.user_id is not None:
            return None
        if db.scalar(select(User.id).where(User.career_schema == legacy)) is not None:
            return None     # hesaplara gore sahipli (dunya yeniden kurulmus): _relink_owner onarir
        if not db.scalar(text('SELECT EXISTS (SELECT 1 FROM "public".teams)')):
            return None
        user_id = attach(db, legacy)
        db.execute(text('UPDATE "public".game_state SET user_id = :uid WHERE id = 1'), {"uid": user_id})
        return user_id


def _reserve_user_id() -> int:
    """Kullanici satirindan ONCE kimlik ayrilir (sema adi icin); geri alinsa da tekrar verilmez."""
    _ensure_accounts()
    with database.engine.begin() as conn:
        return int(conn.scalar(text("SELECT nextval(pg_get_serial_sequence('accounts.users', 'id'))")))


def _free_schema_name(user_id: int) -> str:
    """
    career_<id>; o ad bir semada ya da baska bir hesapta kullaniliyorsa (orn. hesaplar elle
    sifirlanmis, eski sema kalmis) career_<id>_2, _3 ... Baskasinin verisine ASLA yazilmaz.
    """
    base = f"{CAREER_SCHEMA_PREFIX}{user_id}"
    with _accounts_scope() as db:
        for suffix in range(1, _MAX_SCHEMA_SUFFIX + 1):
            name = database.valid_schema_name(base if suffix == 1 else f"{base}_{suffix}")
            in_db = db.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)"),
                              {"s": name})
            owner = db.scalar(select(User.id).where(User.career_schema == name, User.id != user_id))
            if not in_db and owner is None:
                return name
    raise AccountError(CAREER_FAILED)


def _build_world(schema: str, world_seed: int | None, source: str) -> None:
    import seed as seed_module  # agir modul (FM ayristirici): yalnizca kurulumda yuklenir

    rng_seed = world_seed if world_seed is not None else secrets.randbelow(2 ** 31 - 1)
    with database.career_context(schema):
        database.init_db()
        seed_module.seed(rng_seed=rng_seed, source=source)


def _discard_career(schema: str, delete_user_id: int | None) -> None:
    """Basarisiz kurulumun izlerini temizler; temizlik hatasi asil hatayi gizlemez."""
    try:
        database.drop_career_schema(schema)
    except Exception:
        pass
    if delete_user_id is None:
        return
    try:
        with _accounts_scope() as db:
            db.execute(User.__table__.delete().where(User.id == delete_user_id))
    except Exception:
        pass


def _provision_career(
    user_id: int,
    attach: Attach,
    world_seed: int | None,
    source: str,
    *,
    new_user: bool,
) -> str:
    """Yeni kariyer semasi kurar ve kullaniciya baglar. Basarisizlikta iz birakmaz."""
    schema = _free_schema_name(user_id)
    try:
        _build_world(schema, world_seed, source)
        with _accounts_scope() as db:
            owner = attach(db, schema)
            done = db.execute(
                text(f'UPDATE "{schema}".game_state SET user_id = :uid WHERE id = 1'), {"uid": owner}
            ).rowcount
            if done != 1:
                raise AccountError(CAREER_FAILED)
    except BaseException as exc:
        _discard_career(schema, user_id if new_user else None)
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, IntegrityError) and _is_username_conflict(exc):
            raise AccountError(USERNAME_TAKEN) from exc
        raise AccountError(_failure_message(exc, REGISTER_FAILED if new_user else CAREER_FAILED)) from exc
    return schema


def _failure_message(exc: Exception, base: str) -> str:
    if isinstance(exc, AccountError) and str(exc) in (INVALID_CREDENTIALS, USERNAME_TAKEN):
        return str(exc)
    # seed yeniden import edilmez: import hatasinin kendisi burada ikinci kez patlamasin
    seed_error = getattr(sys.modules.get("seed"), "SeedError", None)
    if seed_error is not None and isinstance(exc, seed_error):   # anlamli (orn. FM verisi yok)
        return f"{base} ({exc})"
    return base


def _relink_owner(schema: str, user_id: int) -> bool:
    """
    'python seed.py' dunyayi yeniden kurunca game_state.user_id bos kalir, sahibi ise hala
    accounts.users.career_schema'dadir (esas kaynak). Sahibi bu hesapsa isaret geri baglanir.
    Tablo/sutun yoksa (henuz kurulmamis ya da yukseltilmemis sema) ya da sahip baskasiysa dokunmaz.
    """
    schema = database.valid_schema_name(schema)
    with _accounts_scope() as db:
        has_owner_column = db.scalar(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = :s "
            "AND table_name = 'game_state' AND column_name = 'user_id')"
        ), {"s": schema})
        if not has_owner_column:
            return False
        if db.scalar(select(User.id).where(User.career_schema == schema)) != user_id:
            return False
        return db.execute(
            text(f'UPDATE "{schema}".game_state SET user_id = :uid WHERE id = 1 AND user_id IS NULL'),
            {"uid": user_id},
        ).rowcount == 1


def _ensure_user_career(user_id: int) -> str:
    """Kariyeri olmayan (ya da semasi kaybolmus) mevcut hesaba kariyer atar."""
    with _advisory_lock(_LOCK_USER, str(user_id)):
        with _accounts_scope() as db:
            current = db.scalar(select(User.career_schema).where(User.id == user_id))
        if current is not None and _schema_exists(current):
            return current                  # es zamanli bir giris az once kurdu
        try:
            if _try_claim_legacy(_existing_user(user_id)) is not None:
                return database.LEGACY_CAREER_SCHEMA
        except IntegrityError:
            pass                            # 'public' baska hesapta: yeni kariyer kurulur
        return _provision_career(user_id, _existing_user(user_id), None, "auto", new_user=False)


# ---------------------------------------------------------------------------
# Disari acilan API
# ---------------------------------------------------------------------------

def register(
    username,
    password,
    *,
    world_seed: int | None = None,
    source: str = "auto",
) -> AuthSession:
    """
    Yeni hesap + kariyer. Kural ihlali -> AccountValidationError; alinmis ad (harf buyuklugunden
    bagimsiz) -> AccountError; kariyer kurulamazsa -> AccountError ve hesap olusmaz.
    """
    try:
        name = auth.validate_username(username)
        auth.validate_password(password, name)
    except auth.AuthError as exc:
        raise AccountValidationError(str(exc)) from exc

    with _advisory_lock(_LOCK_USERNAME, name.lower()):
        if _username_taken(name):
            raise AccountError(USERNAME_TAKEN)
        password_hash = auth.hash_password(password)
        try:
            claimed = _try_claim_legacy(_new_user(name, password_hash))
        except IntegrityError as exc:
            if _is_username_conflict(exc):
                raise AccountError(USERNAME_TAKEN) from exc
            claimed = None                  # 'public' baska hesapta: yeni kariyer kurulur
        if claimed is not None:
            return AuthSession(claimed, name, database.LEGACY_CAREER_SCHEMA)

        user_id = _reserve_user_id()
        schema = _provision_career(user_id, _new_user(name, password_hash, user_id),
                                   world_seed, source, new_user=True)
        return AuthSession(user_id, name, schema)


def authenticate(username, password) -> AuthSession:
    """
    Giris. Olmayan kullanici ve yanlis parola AYNI hatayi verir; ikisinde de bir ozet dogrulamasi
    yapilir (DUMMY_HASH), sure farki kullanici adini sizdirmaz. Basarida last_login_at guncellenir,
    ozet parametreleri eskiyse parola sessizce yeniden ozetlenir. Kariyeri olmayan eski hesaba
    kayittaki gibi kariyer atanir.
    """
    name = auth.normalize_username(username)
    row = locked_id = None
    if name and len(name) <= auth.USERNAME_MAX:
        with _accounts_scope() as db:
            # Deneme hakki DOGRULAMADAN ONCE ve atomik olarak harcanir: es zamanli istekler de en fazla
            # MAX_FAILED_LOGINS parola tahmini yaptirir. Var olan ve olmayan kullanicida ayni sorgu
            # calisir (sure farki kullanici adini sizdirmaz).
            not_locked = or_(User.locked_until.is_(None), User.locked_until <= func.now())
            row = db.execute(
                update(User)
                .where(func.lower(User.username) == func.lower(name), not_locked,
                       User.failed_logins < MAX_FAILED_LOGINS)
                .values(failed_logins=User.failed_logins + 1)
                .returning(User.id, User.username, User.password_hash, User.career_schema, User.failed_logins)
            ).first()
            if row is None:
                locked_id = db.scalar(
                    select(User.id).where(func.lower(User.username) == func.lower(name),
                                          or_(User.locked_until > func.now(),
                                              User.failed_logins >= MAX_FAILED_LOGINS))
                )
    stored = row.password_hash if row is not None else auth.DUMMY_HASH
    valid = auth.verify_password(password, stored)
    if locked_id is not None:
        _lock_account(locked_id)
        # Sunucu tarafi kilit: dogru parola bile kilit suresince kabul edilmez (tarayici oturumu
        # degistirmek kilidi asmaz).
        raise AccountError(ACCOUNT_LOCKED.format(minutes=LOCKOUT_MINUTES))
    if row is None or not valid:
        if row is not None and row.failed_logins >= MAX_FAILED_LOGINS:
            _lock_account(row.id)
        raise AccountError(INVALID_CREDENTIALS)

    new_hash = auth.hash_password(password) if auth.needs_rehash(row.password_hash) else None
    with _accounts_scope() as db:
        reset = db.execute(
            update(User)
            .where(User.id == row.id, or_(User.locked_until.is_(None), User.locked_until <= func.now()))
            .values(last_login_at=func.now(), failed_logins=0, locked_until=None)
            .returning(User.id)
        ).first()
        if reset is None:             # dogrulama surerken es zamanli hatali denemeler hesabi kilitledi
            raise AccountError(ACCOUNT_LOCKED.format(minutes=LOCKOUT_MINUTES))
        if new_hash is not None:
            # Arada parola degistiyse (baska oturum) yenisinin ustune yazilmaz
            db.execute(update(User)
                       .where(User.id == row.id, User.password_hash == row.password_hash)
                       .values(password_hash=new_hash))

    career = row.career_schema
    if career is None or not _schema_exists(career):
        career = _ensure_user_career(row.id)
    _relink_owner(career, row.id)
    return AuthSession(row.id, row.username, career)


def _lock_account(user_id: int) -> None:
    """Hakki biten hesap LOCKOUT_MINUTES kilitlenir (zaten kilitliyse sure uzatilmaz), sayac sifirlanir."""
    with _accounts_scope() as db:
        db.execute(
            update(User)
            .where(User.id == user_id, or_(User.locked_until.is_(None), User.locked_until <= func.now()))
            .values(failed_logins=0,
                    locked_until=func.now() + text(f"interval '{int(LOCKOUT_MINUTES)} minutes'"))
        )


def ensure_career_ready(session: AuthSession) -> list[str]:
    """
    Giris sonrasi: kariyer semasini guncel modele getirir (yalnizca EKLEYEN degisiklikler),
    sahiplik isaretini onarir ve (varsa) altyapi kurulumunu calistirir. Uygulananlarin listesi.
    Kullanici satiri sart degildir (testler/araclar sahte oturumla cagirir): hesap yoksa ya da
    sema hesaplara gore baskasininsa yalnizca sahiplik onarimi atlanir.
    """
    schema = database.valid_schema_name(session.career_schema)

    from career_manager import CareerManager

    with database.career_context(schema):
        messages = list(database.upgrade_schema())
        if _relink_owner(schema, session.user_id):
            messages.append("kariyer sahipliği onarıldı: game_state.user_id")
        if hasattr(CareerManager, "ensure_youth_setup"):
            with database.session_scope() as db:
                messages += _as_messages(CareerManager(db).ensure_youth_setup())
        if hasattr(CareerManager, "ensure_club_setup"):         # 11. Asama: tesisler ve sponsorluk
            with database.session_scope() as db:
                messages += _as_messages(CareerManager(db).ensure_club_setup())
    return messages


def _as_messages(result) -> list[str]:
    """ensure_youth_setup donusu: None/sayi -> yok, metin -> tek mesaj, liste ya da .messages -> metinler."""
    result = getattr(result, "messages", result)
    if result is None or isinstance(result, (bool, int, float)):
        return []
    if isinstance(result, str):
        return [result] if result else []
    try:
        return [str(m) for m in result if m]
    except TypeError:
        return []


def user_count() -> int:
    with _accounts_scope() as db:
        return int(db.scalar(select(func.count()).select_from(User)) or 0)


def career_owner(schema: str) -> int | None:
    """Kariyer semasinin sahibi (accounts.users.id); sahipsizse None. Sema adi dogrulanir."""
    schema = database.valid_schema_name(schema)
    with _accounts_scope() as db:
        return db.scalar(select(User.id).where(User.career_schema == schema))
