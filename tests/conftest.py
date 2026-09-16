"""
Test altyapisi (6. Asama): testler OYUN veritabanina DEGIL, ayri bir test
veritabanina (varsayilan fm_db_test) baglanir.

Neden: Gercek FM verisi yuklendiginde oyun dunyasi (kulupler, ligler) degisir.
Testler ise bilinen bir dunyaya (sentetik: Galatasaray, Manchester City, ...)
ihtiyac duyar. Ayrica testlerin kullanicinin kariyer kaydini asla etkilememesi gerekir.

Akis:
    1) Bu dosya import edilir edilmez DATABASE_URL test veritabanina cevrilir
       (database.py'den ONCE; database.py load_dotenv'i override etmez).
    2) pytest_configure: test DB yoksa olusturulur, sema sifirlanir ve sentetik
       dunya yazilir. Ana veritabani adina esitse test calismaz (koruma).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DB_NAME = os.getenv("TEST_DB_NAME", "fm_db_test")


def _test_database_url() -> tuple[str, str]:
    from dotenv import load_dotenv
    from sqlalchemy.engine import make_url

    load_dotenv(ROOT / ".env")
    base = os.getenv("DATABASE_URL")
    if not base:
        user = os.getenv("DB_USER", "manager")
        password = os.getenv("DB_PASSWORD", "fm_pass123")
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5433")
        base = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{os.getenv('DB_NAME', 'fm_db')}"
    url = make_url(base)
    main_db = url.database or ""
    return url.set(database=TEST_DB_NAME).render_as_string(hide_password=False), main_db


_TEST_URL, _MAIN_DB = _test_database_url()
if TEST_DB_NAME == _MAIN_DB:
    raise RuntimeError(
        f"TEST_DB_NAME ({TEST_DB_NAME}) oyun veritabanıyla aynı; testler kariyer verisini silebilirdi."
    )
os.environ["DATABASE_URL"] = _TEST_URL


def pytest_configure(config):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:        # DB yok: entegrasyon testleri kendi skipif'leriyle atlanir
        print(f"\n[conftest] Test veritabanı hazırlanamadı, entegrasyon testleri atlanacak: {exc}")
        return
    finally:
        admin.dispose()

    import database
    import seed

    if database.engine.url.database != TEST_DB_NAME:          # ikinci emniyet kilidi
        raise RuntimeError(f"Beklenmeyen veritabanı: {database.engine.url.database}")
    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")
