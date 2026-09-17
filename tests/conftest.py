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

    from tests.db_urls import build_test_url

    load_dotenv(ROOT / ".env")
    base = os.getenv("DATABASE_URL")
    if not base:
        user = os.getenv("DB_USER", "manager")
        password = os.getenv("DB_PASSWORD", "fm_pass123")
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5433")
        base = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{os.getenv('DB_NAME', 'fm_db')}"
    return build_test_url(base, TEST_DB_NAME)


_TEST_URL, _MAIN_DB = _test_database_url()
if not TEST_DB_NAME.strip() or TEST_DB_NAME == _MAIN_DB:
    raise RuntimeError(
        f"TEST_DB_NAME ({TEST_DB_NAME}) oyun veritabanıyla aynı; testler kariyer verisini silebilirdi."
    )
os.environ["DATABASE_URL"] = _TEST_URL

# Isim maskeleme testlerde MAKINEDEN BAGIMSIZ olmalidir: .env'de kisisel ayarlar (orn.
# SEED_NAME_MASKING=off + OFM_ALLOW_REAL_NAMES=1, kendi FM verisiyle yerel oyun) olsa bile testler
# depo varsayilanini gorur: seviye light ve gercek isim izni KAPALI. 'off' sinayan testler iki
# degiskeni de monkeypatch ile acikca verir.
os.environ["SEED_NAME_MASKING"] = "light"
os.environ.pop("OFM_ALLOW_REAL_NAMES", None)


def pytest_configure(config):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    if os.getenv("CM_TEST_NO_DB"):          # saf (DB'siz) testler: dunya kurulmaz
        print("\n[conftest] CM_TEST_NO_DB: test veritabani hazirlanmadi.")
        return
    url = make_url(os.environ["DATABASE_URL"])
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:        # DB yok: entegrasyon testleri kendi skipif'leriyle atlanir
        print(f"\n[conftest] Test veritabani hazirlanamadi, entegrasyon testleri atlanacak: {exc}")
        return
    finally:
        admin.dispose()

    import database
    import seed

    if database.engine.url.database != TEST_DB_NAME:          # ikinci emniyet kilidi
        raise RuntimeError(f"Beklenmeyen veritabanı: {database.engine.url.database}")
    with database.engine.connect() as conn:                   # ucuncu kilit: gercekte baglanilan DB
        actual = conn.scalar(text("SELECT current_database()"))
    if actual != TEST_DB_NAME:
        raise RuntimeError(f"Test bağlantısı '{actual}' veritabanına gidiyor; sıfırlama iptal edildi.")
    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")
