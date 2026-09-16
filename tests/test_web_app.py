"""
Web arayuzu uctan uca testleri (6. Asama) -- Streamlit AppTest (basliksiz).

Gercek uygulama betigini calistirir, kenar cubugundaki widget'lari ayarlar,
"Maci baslat"a basar ve olusan sayfayi denetler. Hiz "Anında" secilir ki
test beklemesin. Test veritabani (conftest) sentetik dunyadir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "web_app.py")


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def _html(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def _app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_initial_page_lists_teams_and_waits():
    at = _app()
    assert at.title[0].value.endswith("Canlı Maç")
    home = at.sidebar.selectbox[0]
    assert "Galatasaray" in home.options and "Manchester City" in home.options
    assert "Maçı başlat" in at.sidebar.button[0].label
    assert "cm-board" in _html(at) and "0 - 0" in _html(at)


def test_friendly_match_renders_full_live_screen():
    at = _app()
    at.sidebar.radio[0].set_value("Hazırlık maçı")
    at.sidebar.select_slider[0].set_value("Anında")
    at.sidebar.text_input[0].set_value("7")
    at.sidebar.selectbox[0].set_value("Galatasaray")
    at.sidebar.selectbox[1].set_value("Fenerbahce")
    at.sidebar.button[0].click()
    at.run()
    assert not at.exception, at.exception

    html = _html(at)
    assert "Galatasaray" in html and "Fenerbahce" in html
    assert "cm-feed" in html and "cm-stats" in html
    assert "MAÇ SONU" in html                          # son kare basildi
    assert "Topla oynama" in html                      # final istatistik tablosu
    assert any("Maç sonu" in h.value for h in at.markdown) or any("Maç sonu" in s.value for s in at.subheader)
    assert any("kaydedilmez" in c.value for c in at.caption)


def test_friendly_does_not_write_to_database():
    from sqlalchemy import func, select

    from database import SessionLocal
    from models import Fixture, FixtureStatus

    with SessionLocal() as db:
        played_before = db.scalar(select(func.count()).select_from(Fixture).where(Fixture.status == FixtureStatus.PLAYED))
    at = _app()
    at.sidebar.select_slider[0].set_value("Anında")
    at.sidebar.selectbox[0].set_value("Inter")
    at.sidebar.selectbox[1].set_value("Milan")
    at.sidebar.button[0].click()
    at.run()
    assert not at.exception
    with SessionLocal() as db:
        played_after = db.scalar(select(func.count()).select_from(Fixture).where(Fixture.status == FixtureStatus.PLAYED))
    assert played_after == played_before


def test_same_team_is_rejected():
    at = _app()
    at.sidebar.select_slider[0].set_value("Anında")
    at.sidebar.selectbox[0].set_value("Arsenal")
    at.sidebar.selectbox[1].set_value("Arsenal")
    at.sidebar.button[0].click()
    at.run()
    assert any("kendisiyle" in e.value for e in at.error)


def test_career_mode_requires_team_selection():
    from database import SessionLocal
    from models import GameState

    with SessionLocal() as db:
        state = db.get(GameState, 1)
        if state.user_team_id is not None:
            pytest.skip("test DB'de kullanıcı takımı ayarlı")
    at = _app()
    at.sidebar.radio[0].set_value("Kariyer: haftayı oyna")
    at.run()
    assert not at.exception
    assert any("Menajer tanınırlığı" in c.value for c in at.sidebar.caption)
    at.sidebar.select_slider[0].set_value("Anında")
    at.sidebar.button[-1].click()
    at.run()
    assert any("takımını seç" in w.value for w in at.warning)
