"""
database.py
===========
Veritabani baglanti katmani.

Bu dosya SADECE "veriye nasil ulasilir" sorusunu cevaplar.
Oyun kurallari, simulasyon mantigi veya arayuz kodu buraya ASLA girmez.
Boylece ileride 2D arayuze gectigimizde bu katmana hic dokunmamiz gerekmez.

Disari actigi sey:
    engine        -> SQLAlchemy Engine (connection pool dahili olarak burada)
    SessionLocal  -> Yeni Session uretmek icin fabrika
    Base          -> Tum ORM modellerinin turedigi taban sinif
    session_scope -> "with" blogu ile otomatik commit/rollback yapan yardimci

Cok kullanicili kariyer izolasyonu (10. Asama):
    * Hesaplar (accounts.users) ayri 'accounts' semasindadir; dunya sifirlamasi kullanicilari silmez.
      14H: kalici oturumlar accounts.sessions'ta (yalnizca belirtec ozeti); init_accounts eksikse ekler.
    * Her kullanicinin kariyeri (ligler, takimlar, oyuncular, fiksturler...) KENDI PostgreSQL
      semasindadir: ilk kullanici eski tek kisilik kariyeri ('public') devralir, sonrakiler
      'career_<id>' semasi alir. Oyun kodu tablo adlarini niteleyerek yazmaz.
    * Hangi kariyerin kullanilacagi current_career_schema() ile cozulur: career_context(...)
      ile acikca verilen sema ya da set_career_schema_resolver ile kaydedilen cozucu (web
      arayuzu: oturumdaki kullanici). Her Session islemi basinda SET LOCAL search_path o
      semaya ayarlanir; SET LOCAL islem bitince sifirlanir, havuzdaki baglanti sizdirmaz.
    * Sema/cozucu yoksa (CLI, testler) davranis eskisiyle aynidir: 'public'.
    * upgrade_schema(): goc araci olmadan, yalnizca EKLEYEN degisiklikleri (eksik tablo, bilinen
      yeni sutunlar) uygular; kariyer kaydi silinmez. Eksik tablolar (orn. 12. Asama transfer_log,
      season_honours, news_items, shortlist, friendlies; 13. Asama tactic_presets) indeksleriyle
      birlikte olusturulur.

Paylasilan dunyalar (Faz 12 / 14. Asama):
    * upgrade_schema tek EKLEMEYEN ama KAYIPSIZ adimi da uygular: RELAXED_CONSTRAINTS (eski benzersiz kisit
      dusurulur, yerine daha GEVSEK benzersiz indeks kurulur; mevcut satirlar yeni kurala zaten uyar).
      SCHEMA_VERSION: accounts.worlds.schema_version ile karsilastirilir (esitse giriste DDL atlanabilir).
    * Dunya tur kilidi: world_lock(sema, kip) blogunda acilan HER oturum isleminin basinda (after_begin,
      search_path'ten hemen sonra) PostgreSQL islem seviyesi advisory lock alinir; commit/rollback'te duser.
          shared        -> pg_advisory_xact_lock_shared   (menajer callback'leri; birbirini beklemez)
          exclusive     -> pg_advisory_xact_lock          (hafta ilerletme, sema yukseltme; bekler)
          try_exclusive -> pg_try_advisory_xact_lock      (alinamazsa WorldBusyError, beklemez)
      Bekleme SET LOCAL lock_timeout ile sinirlidir (asilirsa psycopg2 OperationalError, pgcode 55P03);
      kilit alindiktan sonra onceki lock_timeout geri yuklenir. Ortam: OFM_WORLD_LOCK_TIMEOUT_MS.
      Kilit YALNIZCA blok acildigindaki kariyer baglaminda alinir: blok icinde yeni career_context acan
      islemler (accounts._accounts_scope 'public'e sabitlenir) kilit almaz -- 'public' dunyasi dahil.
      Kurallar: blok, kilidin semasi etkin kariyerken acilir (career_context DISARIDA); SHARED tutan acik bir
      oturum varken ayni dunyada exclusive istenmez (kendi kendini bekler).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateIndex

# .env dosyasini oku (proje kokunde aranir)
load_dotenv()


# ---------------------------------------------------------------------------
# Yapilandirma
# ---------------------------------------------------------------------------

def _build_url() -> str:
    """
    Once DATABASE_URL'e bakar. Yoksa parcali DB_* degiskenlerinden URL kurar.
    Boylece .env eksik/yarim olsa bile makul bir varsayilanla calisiriz.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        # Ciplak "postgresql://" verilmisse surucuyu acikca belirtelim.
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url

    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "fm_db")
    user = os.getenv("DB_USER", "manager")
    password = os.getenv("DB_PASSWORD", "fm_pass123")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


DATABASE_URL: str = _build_url()

_ECHO = os.getenv("DB_ECHO", "false").strip().lower() in {"1", "true", "yes"}
_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))


# ---------------------------------------------------------------------------
# Engine (connection pool)
# ---------------------------------------------------------------------------

engine: Engine = create_engine(
    DATABASE_URL,
    echo=_ECHO,
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    # Havuzdaki bayat baglantilari kullanmadan once test eder.
    # Docker container yeniden baslatildiginda "server closed the connection"
    # hatasi almamizi engeller.
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)


# ---------------------------------------------------------------------------
# Session yonetimi
# ---------------------------------------------------------------------------

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    # Commit sonrasi nesnelerin alanlarina erismeye devam edebilmek icin.
    # Simulasyon motoru commit'ten sonra da nesneleri elinde tutacak.
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """Tum ORM modellerinin ortak taban sinifi."""
    pass


# ---------------------------------------------------------------------------
# Kariyer semasi (cok kullanicili izolasyon)
# ---------------------------------------------------------------------------

ACCOUNTS_SCHEMA = "accounts"
LEGACY_CAREER_SCHEMA = "public"
_SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_RESERVED_SCHEMAS = frozenset({ACCOUNTS_SCHEMA, "information_schema"})

_career_schema: ContextVar[str | None] = ContextVar("career_schema", default=None)
# Her career_context girisi yeni bir isaret: dunya kilidi yalnizca kendi acildigi baglamdaki islemlere uygulanir
_career_pin: ContextVar[object | None] = ContextVar("career_pin", default=None)
_schema_resolver: Callable[[], str | None] | None = None


def valid_schema_name(name: str) -> str:
    """Sema adi SQL'e tirnakli yazilir; yine de yalnizca guvenli karakterlere izin verilir."""
    # fullmatch: sondaki satir sonu gibi kacamaklar gecmez; sistem semalari kariyer olamaz
    if (not isinstance(name, str) or not _SCHEMA_NAME.fullmatch(name) or name in _RESERVED_SCHEMAS
            or name.startswith("pg_")):
        raise ValueError(f"Geçersiz kariyer şeması: {name!r}")
    return name


def set_career_schema_resolver(resolver: Callable[[], str | None] | None) -> None:
    """Aktif kariyeri bulan fonksiyon (web arayuzu oturumdaki kullanicinin semasini dondurur)."""
    global _schema_resolver
    _schema_resolver = resolver


def current_career_schema() -> str | None:
    """career_context > kayitli cozucu > None ('public', eski davranis)."""
    schema = _career_schema.get()
    if schema is None and _schema_resolver is not None:
        schema = _schema_resolver()
    return valid_schema_name(schema) if schema else None


@contextmanager
def career_context(schema: str | None) -> Iterator[None]:
    """Blok icindeki tum veritabani islemleri verilen kariyer semasinda calisir."""
    token = _career_schema.set(valid_schema_name(schema) if schema else None)
    pin = _career_pin.set(object())
    try:
        yield
    finally:
        _career_pin.reset(pin)
        _career_schema.reset(token)


def _set_search_path(connection, schema: str | None) -> None:
    if schema:
        # Yalnizca kariyer semasi: eksik tablo 'public'e dusup baska kariyeri okumasin
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')


# ---------------------------------------------------------------------------
# Dunya tur kilidi (Faz 12 / 14. Asama)
# ---------------------------------------------------------------------------

LOCK_WORLD_TURN = 10_003                       # advisory lock ad alani (accounts.py: 10_001, 10_002)
WORLD_LOCK_MODES = ("shared", "exclusive", "try_exclusive")
WORLD_LOCK_TIMEOUT_ENV = "OFM_WORLD_LOCK_TIMEOUT_MS"
SHARED_LOCK_TIMEOUT_MS = 20_000                # menajer callback'i hafta ilerlemesini en fazla bu kadar bekler
EXCLUSIVE_LOCK_TIMEOUT_MS = 5_000              # hafta ilerletme / yukseltme acik callback'leri bu kadar bekler
LOCK_TIMEOUT_PGCODE = "55P03"                  # lock_not_available (lock_timeout asildi)

WorldLockKind = Literal["shared", "exclusive", "try_exclusive"]


class WorldBusyError(RuntimeError):
    """try_exclusive: dunya kilidi su an baskasinda (hafta oynuyor ya da menajer islemi suruyor)."""


@dataclass(frozen=True)
class WorldLockMode:
    schema: str
    mode: str
    timeout_ms: int | None
    pin: object | None                         # blok acildigindaki career_context isareti


_world_lock: ContextVar[WorldLockMode | None] = ContextVar("world_lock", default=None)


def world_lock_timeout_ms(mode: str, timeout_ms: int | None = None) -> int:
    """Bekleme siniri (ms): acik deger > OFM_WORLD_LOCK_TIMEOUT_MS > kipin varsayilani. 0 = sinirsiz."""
    if timeout_ms is not None:
        return timeout_ms
    raw = os.getenv(WORLD_LOCK_TIMEOUT_ENV, "").strip()
    if raw.isdigit():
        return int(raw)
    return SHARED_LOCK_TIMEOUT_MS if mode == "shared" else EXCLUSIVE_LOCK_TIMEOUT_MS


@contextmanager
def world_lock(schema: str, mode: WorldLockKind, timeout_ms: int | None = None) -> Iterator[None]:
    """
    Blok icinde (bu kariyer baglaminda) acilan her oturum isleminin basinda dunya tur kilidini alir.
    Kilit islem sonunda duser; blok ayni oturumda birden cok commit iceriyorsa her islem yeniden alir.
    Semanin etkin kariyer olmasi sarttir (career_context bloktan ONCE acilir ya da web cozucusu verir):
    aksi halde kilit sessizce hic alinmayacagindan ValueError.
    """
    schema = valid_schema_name(schema)
    if mode not in WORLD_LOCK_MODES:
        raise ValueError(f"Geçersiz dünya kilidi kipi: {mode!r}")
    if timeout_ms is not None and (isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int)
                                   or timeout_ms < 0):
        raise ValueError(f"Geçersiz kilit bekleme süresi: {timeout_ms!r}")
    active = current_career_schema() or LEGACY_CAREER_SCHEMA
    if active != schema:
        raise ValueError(f"Dünya kilidi '{schema}' için açıldı ama etkin kariyer şeması '{active}'.")
    token = _world_lock.set(WorldLockMode(schema, mode, timeout_ms, _career_pin.get()))
    try:
        yield
    finally:
        _world_lock.reset(token)


def _take_world_lock(connection, schema: str | None) -> None:
    """after_begin / career_connection: etkin world_lock bu baglamin semasiysa kilidi alir."""
    lock = _world_lock.get()
    if lock is None or lock.pin is not _career_pin.get() or lock.schema != (schema or LEGACY_CAREER_SCHEMA):
        return
    params = {"ns": LOCK_WORLD_TURN, "key": lock.schema}
    if lock.mode == "try_exclusive":
        if not connection.execute(text("SELECT pg_try_advisory_xact_lock(:ns, hashtext(:key))"), params).scalar():
            raise WorldBusyError("Dünya şu an haftayı oynatıyor ya da bir menajer işlem yapıyor.")
        return
    wait = world_lock_timeout_ms(lock.mode, lock.timeout_ms)
    # SET LOCAL lock_timeout yalnizca kilit beklemesi icin: sonra islemin onceki degeri geri yuklenir
    previous = connection.execute(text("SELECT current_setting('lock_timeout')")).scalar()
    connection.execute(text("SELECT set_config('lock_timeout', :wait, true)"), {"wait": f"{wait}ms"})
    function = "pg_advisory_xact_lock_shared" if lock.mode == "shared" else "pg_advisory_xact_lock"
    connection.execute(text(f"SELECT {function}(:ns, hashtext(:key))"), params)
    connection.execute(text("SELECT set_config('lock_timeout', :prev, true)"), {"prev": previous})


@event.listens_for(SessionLocal, "after_begin")
def _session_search_path(session, transaction, connection) -> None:
    schema = current_career_schema()
    _set_search_path(connection, schema)
    _take_world_lock(connection, schema)


@contextmanager
def career_connection() -> Iterator:
    """Sema DDL'i icin aktif kariyere yonlenmis islem baglantisi (commit blok sonunda; world_lock gecerli)."""
    with engine.begin() as conn:
        schema = current_career_schema()
        _set_search_path(conn, schema)
        _take_world_lock(conn, schema)
        yield conn


@contextmanager
def session_scope() -> Iterator[Session]:
    """
    Islem (transaction) sinirlarini yoneten yardimci.

    Kullanim:
        with session_scope() as db:
            db.add(team)
        # blok sorunsuz biterse commit, hata olursa rollback yapilir.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Yardimci fonksiyonlar
# ---------------------------------------------------------------------------

def wait_for_db(retries: int = 15, delay: float = 2.0, verbose: bool = True) -> bool:
    """
    Veritabani kabul eder hale gelene kadar bekler.

    'docker compose up' sonrasi PostgreSQL'in ilk acilisi birkac saniye surer;
    seed scriptini hemen calistirirsan baglanti reddedilir. Bu fonksiyon
    o yaris durumunu (race condition) ortadan kaldirir.
    """
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            if verbose:
                print(f"[db] Baglanti kuruldu ({attempt}. denemede).")
            return True
        except OperationalError as exc:
            if attempt == retries:
                if verbose:
                    print(f"[db] Baglanti kurulamadi: {exc}")
                return False
            if verbose:
                print(f"[db] Veritabani henuz hazir degil, bekleniyor... ({attempt}/{retries})")
            time.sleep(delay)
    return False


def _career_tables() -> list:
    import models  # noqa: F401  -- modellerin Base.metadata'ya kaydolmasi icin
    return [t for t in Base.metadata.sorted_tables if t.schema != ACCOUNTS_SCHEMA]


# accounts.users icin sonradan eklenen sutunlar (tablo daha once olusturulmussa eklenir)
ACCOUNT_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("failed_logins", "SMALLINT NOT NULL DEFAULT 0"),
    ("locked_until", "TIMESTAMP WITH TIME ZONE"),
)


def init_accounts() -> None:
    """
    accounts semasini ve tablolarini (users; Faz 12: worlds, world_memberships, manager_profiles; 14H: sessions)
    olusturur, users'a eksik ek sutunlari ekler (idempotent; var olan tablolara dokunulmaz). Eksik YENI hesap
    tablosu (orn. canli veritabaninda 14H oncesi sessions) create_all ile eklenir: upgrade_schema de bunu cagirir.
    """
    import models  # noqa: F401
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{ACCOUNTS_SCHEMA}"')
        tables = [t for t in Base.metadata.sorted_tables if t.schema == ACCOUNTS_SCHEMA]
        Base.metadata.create_all(bind=conn, tables=tables)
        existing = set(conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = 'users'"
        ), {"s": ACCOUNTS_SCHEMA}).scalars())
        for column, ddl in ACCOUNT_ADDITIVE_COLUMNS:
            if column not in existing:           # ALTER yalnizca gerekirse: tablo kilidi her giriste alinmasin
                conn.exec_driver_sql(
                    f'ALTER TABLE "{ACCOUNTS_SCHEMA}".users ADD COLUMN IF NOT EXISTS "{column}" {ddl}')


def init_db() -> None:
    """Hesap tablolari + aktif kariyer semasindaki oyun tablolari (varsa dokunmaz)."""
    init_accounts()
    schema = current_career_schema()
    if schema:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    with career_connection() as conn:
        Base.metadata.create_all(bind=conn, tables=_career_tables())


def drop_db() -> None:
    """Aktif kariyerin tablolari (ve ENUM tipleri) silinir; HESAPLAR KORUNUR. Veri kaybettirir."""
    with career_connection() as conn:
        Base.metadata.drop_all(bind=conn, tables=_career_tables())


def reset_db() -> None:
    """Aktif kariyer icin sifirdan temiz tablolar: once drop, sonra create."""
    drop_db()
    init_db()


def drop_career_schema(schema: str) -> None:
    """Bir kullanicinin kariyer semasini tamamen siler (eski 'public' kariyer silinemez)."""
    schema = valid_schema_name(schema)
    if schema == LEGACY_CAREER_SCHEMA:
        raise ValueError("Eski tek kişilik kariyer şeması silinemez.")
    with engine.begin() as conn:
        conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


# Goc araci yok: bilinen, YALNIZCA EKLEYEN sutunlar burada. (tablo, sutun, PostgreSQL tanimi)
# Kariyer kaydi korunur; eklenen sutunlarin oyun verisi CareerManager.ensure_youth_setup /
# ensure_club_setup ile doldurulur.
ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("game_state", "user_id",
     f'INTEGER REFERENCES "{ACCOUNTS_SCHEMA}".users(id) ON DELETE SET NULL'),
    ("game_state", "academy_seeded", "BOOLEAN NOT NULL DEFAULT false"),
    ("game_state", "last_youth_intake_season", "SMALLINT"),
    ("teams", "youth_facilities",
     "SMALLINT CHECK (youth_facilities IS NULL OR youth_facilities BETWEEN 1 AND 20)"),
    ("players", "potential_rating",
     "SMALLINT CHECK (potential_rating IS NULL OR potential_rating BETWEEN 1 AND 99)"),
    ("players", "in_academy", "BOOLEAN NOT NULL DEFAULT false"),
    ("players", "development_progress", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
    # 11. Asama: tesisler ve sponsorluk (veri CareerManager.ensure_club_setup ile doldurulur)
    ("teams", "stadium_capacity",
     "INTEGER CHECK (stadium_capacity IS NULL OR stadium_capacity BETWEEN 1000 AND 200000)"),
    ("teams", "medical_facilities",
     "SMALLINT CHECK (medical_facilities IS NULL OR medical_facilities BETWEEN 1 AND 20)"),
    ("teams", "sponsor_name", "VARCHAR(60)"),
    ("teams", "sponsor_weekly", "BIGINT NOT NULL DEFAULT 0 CHECK (sponsor_weekly >= 0)"),
    ("teams", "sponsor_until_season",
     "SMALLINT CHECK (sponsor_until_season IS NULL OR sponsor_until_season >= 1)"),
    ("teams", "sponsor_offers",
     "JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sponsor_offers) = 'array')"),
    # 12. Asama: transfer yasagi, oynama suresi kaygilari ve maas talepleri. Yeni tablolar (transfer_log,
    # season_honours, news_items, shortlist, friendlies) upgrade_schema'nin eksik tablo adiminda olusur.
    ("game_state", "career_week_offset", "INTEGER NOT NULL DEFAULT 0"),
    ("players", "transfer_locked_until", "INTEGER"),
    ("players", "minutes_window",
     "JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(minutes_window) = 'array')"),
    ("players", "concern_level", "SMALLINT NOT NULL DEFAULT 0 CHECK (concern_level BETWEEN 0 AND 3)"),
    ("players", "contract_overall",
     "SMALLINT CHECK (contract_overall IS NULL OR contract_overall BETWEEN 1 AND 99)"),
    ("players", "wage_demand", "BIGINT CHECK (wage_demand IS NULL OR wage_demand >= 0)"),
    # 13. Asama: kayitli taktik (bos {} = varsayilan: eski kayit eski davranisla oynar). Yeni tablo
    # tactic_presets upgrade_schema'nin eksik tablo adiminda olusur.
    ("teams", "tactic_instructions",
     "JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(tactic_instructions) = 'object')"),
    ("teams", "set_piece_roles",
     "JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(set_piece_roles) = 'object')"),
    ("teams", "match_plan",
     "JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(match_plan) = 'object')"),
    # Faz 12 / 14. Asama: paylasilan dunya (12A), insan pazari ve kiralik (12B), milli takimlar (12C).
    # Bos/NULL degerler eski davranistir ({} = WorldRules.legacy()). Yeni tablolar (world_managers,
    # transfer_offers, nations ...) upgrade_schema'nin eksik tablo adiminda olusur.
    ("game_state", "world_rules",
     "JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(world_rules) = 'object')"),
    ("game_state", "turn_opened_at", "TIMESTAMP WITH TIME ZONE"),
    ("game_state", "turn_deadline_at", "TIMESTAMP WITH TIME ZONE"),
    ("game_state", "last_advance_at", "TIMESTAMP WITH TIME ZONE"),
    ("game_state", "last_advance_trigger", "VARCHAR(10)"),
    ("game_state", "last_advance_by", "INTEGER"),
    ("teams", "ai_protected_until", "INTEGER"),
    ("players", "loan_id", "INTEGER"),
    ("players", "loan_from_team_id", 'INTEGER REFERENCES "teams"(id) ON DELETE SET NULL'),
    ("players", "loan_wage_share",
     "SMALLINT CHECK (loan_wage_share IS NULL OR loan_wage_share BETWEEN 0 AND 100)"),
    ("players", "transfer_listed", "BOOLEAN NOT NULL DEFAULT false"),
    ("players", "loan_listed", "BOOLEAN NOT NULL DEFAULT false"),
    ("players", "international_caps", "SMALLINT NOT NULL DEFAULT 0 CHECK (international_caps >= 0)"),
    ("players", "international_goals", "SMALLINT NOT NULL DEFAULT 0 CHECK (international_goals >= 0)"),
    # 13. Asama: dunyanin kuruldugu isim maskeleme seviyesi (name_masking.MASK_LEVELS).
    # Eski kayitlar 'light' sayilir; 'off' (gercek adlar) dunyalar paylasilan dunyaya cevrilemez.
    ("game_state", "mask_level", "VARCHAR(10) NOT NULL DEFAULT 'light'"),
    # 13H transfer masasi: sozlesme maddeleri ve istenen bedel (bos: eski kayit, madde yok). Yeni tablolar
    # (transfer_deals, transfer_payments, scout_assignments) upgrade_schema'nin eksik tablo adiminda olusur.
    ("players", "release_clause", "BIGINT CHECK (release_clause IS NULL OR release_clause >= 0)"),
    ("players", "asking_price", "BIGINT CHECK (asking_price IS NULL OR asking_price >= 0)"),
    ("players", "contract_clauses",
     "JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(contract_clauses) = 'object')"),
)

# Sema surumu (Faz 12 / 14. Asama): tablo, sutun, indeks ya da gevsetilen kisit eklendiginde ARTIRILIR.
# accounts.worlds.schema_version bu degere esitse giris sirasindaki upgrade_schema (DDL) atlanabilir.
SCHEMA_VERSION: int = 16          # 16: 13H transfer masasi (transfer_deals / transfer_payments / scout_assignments,
#                                   players.release_clause / asking_price / contract_clauses)

# Var olan tablolara sonradan eklenen modeller indeksleri: (tablo, indeks adi). Tanim models.py'den okunur;
# indeks yoksa CREATE INDEX IF NOT EXISTS (her giriste tablo kilidi alinmasin diye once varligi sorulur).
ADDITIVE_INDEXES: tuple[tuple[str, str], ...] = (
    ("players", "ix_player_team_academy"),
    ("players", "ix_player_loan_from"),                         # Faz 12: Team.loaned_out_players
)

# Kayipsiz gevsetme: (tablo, dusurulen benzersiz kisit, yerine kurulan benzersiz indeks). Yeni indeks eskisinden
# genis anahtarli oldugu icin mevcut satirlar ona zaten uyar; indeks tanimi models.py'den okunur.
RELAXED_CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    # Faz 12: haftada tek hazirlik maci artik kulup basina (paylasilan dunyada her insan kulubu oynar)
    ("friendlies", "uq_friendly_week", "uq_friendly_home_week"),
)


def upgrade_schema() -> list[str]:
    """
    Aktif kariyer semasina eksik tablolari, ADDITIVE_COLUMNS'taki eksik sutunlari ve ADDITIVE_INDEXES'teki
    eksik indeksleri ekler; RELAXED_CONSTRAINTS'i (kayipsiz) uygular. Idempotent; yapilan degisikliklerin
    listesini dondurur (bos liste: sema zaten guncel). Bilinmeyen eksikler (orn. yeniden adlandirilmis
    sutun) burada duzeltilmez: schema_problems. Etkin world_lock(sema, "exclusive") DDL'i de kapsar.
    """
    from sqlalchemy import inspect

    init_accounts()
    schema = current_career_schema() or LEGACY_CAREER_SCHEMA
    applied: list[str] = []
    with career_connection() as conn:
        inspector = inspect(conn)
        existing = set(inspector.get_table_names(schema=schema))
        missing_tables = [t for t in _career_tables() if t.name not in existing]
        if missing_tables:
            Base.metadata.create_all(bind=conn, tables=missing_tables)
            applied += [f"tablo eklendi: {t.name}" for t in missing_tables]
        for table, column, ddl in ADDITIVE_COLUMNS:
            if table not in existing:
                continue                       # tablo az once tam haliyle olusturuldu
            columns = {c["name"] for c in inspector.get_columns(table, schema=schema)}
            if column not in columns:
                # IF NOT EXISTS: es zamanli iki yukseltme ayni sutunu eklemeye calisirsa hata olmaz
                conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS "{column}" {ddl}')
                applied.append(f"sütun eklendi: {table}.{column}")
        for table, index_name in ADDITIVE_INDEXES:
            if table in existing and not _index_exists(conn, schema, index_name):
                conn.execute(CreateIndex(_model_index(table, index_name), if_not_exists=True))
                applied.append(f"indeks eklendi: {table}.{index_name}")
        for table, constraint, index_name in RELAXED_CONSTRAINTS:
            if table not in existing:
                continue                       # yeni tablo modelden (gevsek indeksle) olusturuldu
            if not _index_exists(conn, schema, index_name):
                conn.execute(CreateIndex(_model_index(table, index_name), if_not_exists=True))
                applied.append(f"indeks eklendi: {table}.{index_name}")
            if _constraint_exists(conn, schema, table, constraint):
                conn.exec_driver_sql(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{constraint}"')
                applied.append(f"kısıt gevşetildi: {table}.{constraint} → {index_name}")
    return applied


def _model_index(table: str, index_name: str):
    import models  # noqa: F401

    for index in Base.metadata.tables[table].indexes:
        if index.name == index_name:
            return index
    raise KeyError(f"{table}.{index_name} modelde tanımlı değil")


def _index_exists(conn, schema: str, index_name: str) -> bool:
    return bool(conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = :s AND indexname = :i)"
    ), {"s": schema, "i": index_name}))


def _constraint_exists(conn, schema: str, table: str, constraint: str) -> bool:
    return bool(conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = :s AND t.relname = :t AND c.conname = :c)"
    ), {"s": schema, "t": table, "c": constraint}))


def schema_problems() -> list[str]:
    """
    Modellerde olup veritabaninda OLMAYAN tablo/sutunlar (hesaplar + aktif kariyer semasi).
    Projede goc (migration) araci yok; eski semali bir veritabani anlasilmaz SQL hatalari
    yerine burada acikca yakalanir (upgrade_schema bilinen ekleri kendisi uygular).
    """
    import models  # noqa: F401

    career = current_career_schema() or LEGACY_CAREER_SCHEMA
    # Faz 13I: web her cizimde bu denetimi yapar; tablo basina yansitma (~40 sorgu, ~0.4 sn) yerine katalogdan TEK
    # sorgu (ayni sonuc: eksik tablo / eksik sutun, sorted_tables sirasiyla).
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT n.nspname, c.relname, a.attname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
            "WHERE n.nspname IN (:accounts, :career) AND c.relkind IN ('r', 'p')"),
            {"accounts": ACCOUNTS_SCHEMA, "career": career}).all()
    existing: dict[tuple[str, str], set[str]] = {}
    for schema, table_name, column in rows:
        existing.setdefault((schema, table_name), set()).add(column)
    problems: list[str] = []
    for schema in (ACCOUNTS_SCHEMA, career):
        for table in Base.metadata.sorted_tables:
            if (table.schema == ACCOUNTS_SCHEMA) != (schema == ACCOUNTS_SCHEMA):
                continue
            columns = existing.get((schema, table.name))
            if columns is None:
                problems.append(f"eksik tablo: {table.name}")
                continue
            problems += [f"eksik sütun: {table.name}.{c.name}" for c in table.columns if c.name not in columns]
    return problems


def masked_url() -> str:
    """Sifreyi gizleyerek baglanti adresini dondurur (loglamak icin guvenli)."""
    url = engine.url
    return url.render_as_string(hide_password=True)


if __name__ == "__main__":
    # Hizli saglik kontrolu:  python database.py
    print(f"[db] Hedef: {masked_url()}")
    ok = wait_for_db(retries=3, delay=1.0)
    print("[db] Durum:", "HAZIR" if ok else "ULASILAMIYOR")
