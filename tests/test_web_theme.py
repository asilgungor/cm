"""
Tema secimi uctan uca testleri -- Streamlit AppTest.

⚽ FM Dark / ☀️ FM Light secimi giris sayfasinda ve kenar cubugunda yapilir; secim
st.session_state.theme ve ?theme= URL parametresinde tutulur (sayfa yenilemesi = yeni AppTest
oturumu), giris/cikista korunur ve tema degisimi oturumu kilitlemez.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    APP,
    AppTest,
    _app,
    _career_tab_count,
    _click,
    _db_available,
    _html,
    _reseed,
    _set_user_team,
)
from tests.test_web_auth import _auth, _clean_accounts, _register  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

DARK_BG, LIGHT_BG = "--ofm-bg:#121824", "--ofm-bg:#f4f6f9"


@pytest.fixture(autouse=True)
def fresh_world():
    _clean_accounts()
    _reseed()
    yield
    _clean_accounts()


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _clean_accounts()
    _reseed()


def _set_theme(at, label: str):
    at.radio(key="theme_choice").set_value(label)
    at.run()
    assert not at.exception, at.exception
    return at


def test_login_page_theme_switch_persists_through_register_and_logout():
    at = _app(login=False)
    assert DARK_BG in _html(at) and at.session_state["theme"] == "dark" and at.query_params["theme"] == ["dark"]
    _set_theme(at, "☀️ FM Light")
    assert LIGHT_BG in _html(at) and DARK_BG not in _html(at)
    assert at.session_state["theme"] == "light" and at.query_params["theme"] == ["light"]

    at = _register(at, "TemaMenajeri")
    assert _auth(at) is not None and at.session_state["theme"] == "light"   # giris temayi sifirlamadi
    assert LIGHT_BG in _html(at) and at.radio(key="theme_choice").value == "☀️ FM Light"
    _click(at, "sb_logout")
    assert _auth(at) is None and at.session_state["theme"] == "light" and LIGHT_BG in _html(at)


def test_refresh_keeps_the_theme_from_the_url():
    at = AppTest.from_file(APP, default_timeout=90)
    at.query_params["theme"] = "light"                                  # sayfa yenilemesi: yeni oturum, ayni URL
    at.run()
    assert not at.exception and at.session_state["theme"] == "light" and LIGHT_BG in _html(at)


def test_switching_theme_in_the_sidebar_does_not_lock_the_session():
    from database import session_scope
    from models import GameState

    _set_user_team("Istanbul Lions")
    at = _app(seed="3")
    assert len(at.tabs) == _career_tab_count() and DARK_BG in _html(at)
    for label, marker in (("☀️ FM Light", LIGHT_BG), ("⚽ FM Dark", DARK_BG), ("☀️ FM Light", LIGHT_BG)):
        _set_theme(at, label)
        assert marker in _html(at) and len(at.tabs) == _career_tab_count() and _auth(at) is not None
    _click(at, "lg_play")                                                # oyun islemleri calismaya devam eder
    with session_scope() as db:
        assert db.get(GameState, 1).current_week == 2
    assert LIGHT_BG in _html(at) and "OFM · Online Football Manager" in at.title[0].value
