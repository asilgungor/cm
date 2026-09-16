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
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

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
    try:
        yield
    finally:
        _career_schema.reset(token)


def _set_search_path(connection, schema: str | None) -> None:
    if schema:
        # Yalnizca kariyer semasi: eksik tablo 'public'e dusup baska kariyeri okumasin
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')


@event.listens_for(SessionLocal, "after_begin")
def _session_search_path(session, transaction, connection) -> None:
    _set_search_path(connection, current_career_schema())


@contextmanager
def career_connection() -> Iterator:
    """Sema DDL'i icin aktif kariyere yonlenmis islem baglantisi (commit blok sonunda)."""
    with engine.begin() as conn:
        _set_search_path(conn, current_career_schema())
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
    """accounts semasini ve kullanici tablosunu olusturur; eksik ek sutunlari ekler (idempotent)."""
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
)


def upgrade_schema() -> list[str]:
    """
    Aktif kariyer semasina eksik tablolari ve ADDITIVE_COLUMNS'taki eksik sutunlari ekler.
    Idempotent; yapilan degisikliklerin listesini dondurur (bos liste: sema zaten guncel).
    Bilinmeyen eksikler (orn. yeniden adlandirilmis sutun) burada duzeltilmez: schema_problems.
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
        if "players" in existing:
            conn.exec_driver_sql(
                'CREATE INDEX IF NOT EXISTS ix_player_team_academy ON "players" (team_id, in_academy)')
    return applied


def schema_problems() -> list[str]:
    """
    Modellerde olup veritabaninda OLMAYAN tablo/sutunlar (hesaplar + aktif kariyer semasi).
    Projede goc (migration) araci yok; eski semali bir veritabani anlasilmaz SQL hatalari
    yerine burada acikca yakalanir (upgrade_schema bilinen ekleri kendisi uygular).
    """
    from sqlalchemy import inspect

    import models  # noqa: F401

    career = current_career_schema() or LEGACY_CAREER_SCHEMA
    inspector = inspect(engine)
    problems: list[str] = []
    for schema in (ACCOUNTS_SCHEMA, career):
        existing = set(inspector.get_table_names(schema=schema))
        for table in Base.metadata.sorted_tables:
            if (table.schema == ACCOUNTS_SCHEMA) != (schema == ACCOUNTS_SCHEMA):
                continue
            if table.name not in existing:
                problems.append(f"eksik tablo: {table.name}")
                continue
            columns = {c["name"] for c in inspector.get_columns(table.name, schema=schema)}
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
