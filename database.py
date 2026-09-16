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
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
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


def init_db() -> None:
    """models.py icinde tanimli tum tablolari olusturur (varsa dokunmaz)."""
    import models  # noqa: F401  -- modellerin Base.metadata'ya kaydolmasi icin
    Base.metadata.create_all(bind=engine)


def drop_db() -> None:
    """Tum tablolari (ve ENUM tiplerini) siler. Veri kaybettirir."""
    import models  # noqa: F401
    Base.metadata.drop_all(bind=engine)


def reset_db() -> None:
    """Sifirdan temiz bir sema olusturur: once drop, sonra create."""
    drop_db()
    init_db()


def schema_problems() -> list[str]:
    """
    Modellerde olup veritabaninda OLMAYAN tablo/sutunlar. Projede goc (migration) araci yok;
    eski semali bir veritabani anlasilmaz SQL hatalari yerine burada acikca yakalanir.
    """
    from sqlalchemy import inspect

    import models  # noqa: F401

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            problems.append(f"eksik tablo: {table.name}")
            continue
        columns = {c["name"] for c in inspector.get_columns(table.name)}
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
