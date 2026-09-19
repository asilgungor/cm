"""
Tema secimi uctan uca testleri -- Streamlit AppTest.

OFM Klasik (varsayilan, 14S) / OFM Dark / OFM Light secimi giris sayfasinda ve Oyun Secenekleri'nde yapilir;
secim st.session_state.theme ve ?theme= (+ surum ?tv=) URL parametresinde tutulur (sayfa yenilemesi = yeni AppTest
oturumu), giris/cikista korunur ve tema degisimi oturumu kilitlemez. 14S: surumsuz eski tercih (?theme=dark) bir kez
Klasik'e doner.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, menu  # noqa: E402
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

CLASSIC_BG, DARK_BG, LIGHT_BG = "--ofm-bg:#231a2d", "--ofm-bg:#121824", "--ofm-bg:#f4f6f9"


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
    assert CLASSIC_BG in _html(at) and at.session_state["theme"] == "klasik"          # 14S: varsayilan OFM Klasik
    assert at.query_params["theme"] == ["klasik"] and at.query_params["tv"] == ["2"]
    assert list(at.radio(key="theme_choice").options) == ["OFM Klasik", "OFM Dark", "OFM Light"]
    _set_theme(at, "OFM Light")
    assert LIGHT_BG in _html(at) and CLASSIC_BG not in _html(at)
    assert at.session_state["theme"] == "light" and at.query_params["theme"] == ["light"]

    at = _register(at, "TemaMenajeri")
    assert _auth(at) is not None and at.session_state["theme"] == "light"   # giris temayi sifirlamadi
    assert LIGHT_BG in _html(at) and at.radio(key="theme_choice").value == "OFM Light"
    _click(at, "sb_logout")
    assert _auth(at) is None and at.session_state["theme"] == "light" and LIGHT_BG in _html(at)


def test_refresh_keeps_a_versioned_choice_and_migrates_an_old_one_to_classic():
    at = AppTest.from_file(APP, default_timeout=90)
    at.query_params["theme"] = "light"                                  # 13I yer imi: surumsuz -> bir kez Klasik
    at.run()
    assert not at.exception and at.session_state["theme"] == "klasik" and CLASSIC_BG in _html(at)

    at = AppTest.from_file(APP, default_timeout=90)
    at.query_params["theme"] = "light"                                  # 14S secimi: surumlu -> kalici
    at.query_params["tv"] = "2"
    at.run()
    assert not at.exception and at.session_state["theme"] == "light" and LIGHT_BG in _html(at)


def test_switching_theme_in_game_options_does_not_lock_the_session():
    from database import session_scope
    from models import GameState

    _set_user_team("Istanbul Lions")
    at = _app(seed="3", page="secenekler")
    assert len(menu(at)) == _career_tab_count() and CLASSIC_BG in _html(at)
    assert at.radio(key="theme_choice").value == "OFM Klasik"
    for label, marker in (("OFM Light", LIGHT_BG), ("OFM Dark", DARK_BG), ("OFM Klasik", CLASSIC_BG),
                          ("OFM Light", LIGHT_BG)):
        _set_theme(at, label)
        assert marker in _html(at) and len(menu(at)) == _career_tab_count() and _auth(at) is not None
        assert at.session_state["nav_page"] == "secenekler"            # tema degisimi sayfayi degistirmez
    goto(at, "kadro")                                                    # secim diger sayfalarda da gecerli
    assert LIGHT_BG in _html(at)
    _click(at, "nav_continue")                                           # oyun islemleri calismaya devam eder
    with session_scope() as db:
        assert db.get(GameState, 1).current_week == 2
    assert LIGHT_BG in _html(at) and not at.title                        # 14S: dev baslik yok
