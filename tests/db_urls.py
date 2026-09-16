"""
Test veritabani adresi uretimi (yan etkisiz; conftest ve testler kullanir).

conftest.py import edildigi anda ortam degiskenlerini degistirdigi icin URL mantigi
burada, test edilebilir saf bir fonksiyon olarak durur.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url


def build_test_url(base_url: str, test_db_name: str) -> tuple[str, str]:
    """
    Oyun veritabani adresinden test adresini turetir.
    Donus: (test URL'si, oyun veritabaninin adi).

    Sorgudaki dbname/database parametresi psycopg2'de veritabani adini EZER
    (".../fm_db_test?dbname=fm_db" oyun DB'sine baglanirdi); test adresinde kalmaz.
    """
    url = make_url(base_url)
    main_db = url.database or url.query.get("dbname") or url.query.get("database") or ""
    url = url.difference_update_query(["dbname", "database"]).set(database=test_db_name)
    return url.render_as_string(hide_password=False), main_db
