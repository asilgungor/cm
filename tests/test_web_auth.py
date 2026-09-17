"""
Giris kapisi ve cok kullanicili kariyer uctan uca testleri (10. Asama) -- Streamlit AppTest.

Gercek web_app.py calisir: giris yapmayan kullanici oyun sekmelerini goremez; kayit, giris,
hatali deneme kilidi ve cikis ekrandaki widget'larla yapilir. Veritabaninda parolanin ozet
olarak saklandigi ve her menajerin kariyerinin ayri semada izole oldugu dogrulanir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    _app,
    _career_tab_count,
    _click,
    _db_available,
    _html,
    _reseed,
    _set_user_team,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

PASSWORD = "Gizli1234"


def _clean_accounts() -> None:
    from sqlalchemy import text

    import database

    database.init_accounts()
    with database.engine.begin() as conn:
        schemas = conn.execute(text(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'career\\_%'")).scalars().all()
        for schema in schemas:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.execute(text('DELETE FROM "accounts".worlds'))          # Faz 12: uyelikler CASCADE ile duser
        conn.execute(text('DELETE FROM "accounts".users'))


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


def _has_key(at, kind: str, key: str) -> bool:
    return any(w.key == key for w in getattr(at, kind))


def _register(at, username: str, password: str = PASSWORD, again: str | None = None):
    if not _has_key(at, "text_input", "reg_user"):              # giris sayfasi: "Hemen kayıt ol!" baglantisi
        _click(at, "auth_to_register")
    at.text_input(key="reg_user").set_value(username)
    at.text_input(key="reg_pass").set_value(password)
    at.text_input(key="reg_pass2").set_value(password if again is None else again)
    return _click(at, "reg_btn")


def _login(at, username: str, password: str = PASSWORD):
    if not _has_key(at, "text_input", "login_user"):
        _click(at, "auth_to_login")
    at.text_input(key="login_user").set_value(username)
    at.text_input(key="login_pass").set_value(password)
    return _click(at, "login_btn")


def _auth(at):
    try:
        return at.session_state["auth"]
    except KeyError:
        return None


def test_without_login_no_game_tab_or_sidebar_is_reachable():
    at = _app(login=False)
    assert not at.tabs and not at.title                         # oyun sekmesi / panel basligi yok
    html = _html(at)
    assert 'class="ofm-brand">OFM' in html and "Online Football Manager" in html and "ofm-hero" in html
    assert "<img" not in html                                   # giris gorseli fotograf degil, cizim
    assert at.button(key="login_btn") and at.button(key="auth_to_register")
    assert not [b for b in at.button if b.key in ("lg_play", "live_start", "sb_set_team", "tac_save")]
    assert not [s for s in at.selectbox if s.key == "sb_team"]
    assert at.text_input(key="login_pass").proto.type == at.text_input(key="login_pass").proto.PASSWORD


def test_register_claims_existing_career_and_stores_only_a_hash():
    from sqlalchemy import select

    from database import session_scope
    from models import GameState, User

    at = _register(_app(login=False), "Mourinho")
    auth = _auth(at)
    assert auth is not None and auth.username == "Mourinho" and auth.career_schema == "public"
    assert len(at.tabs) == _career_tab_count()                                  # oyun sekmeleri acildi
    assert any("Hoş geldin, Mourinho" in s.value for s in at.sidebar.success)
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == "Mourinho"))
        assert user.password_hash.startswith("scrypt$") and PASSWORD not in user.password_hash
        assert db.get(GameState, 1).user_id == user.id


def test_register_validation_errors_are_shown_and_nothing_is_created():
    from sqlalchemy import func, select

    from database import session_scope
    from models import User

    at = _register(_app(login=False), "Guardiola", again="Baska1234")
    assert any("eşleşmiyor" in e.value for e in at.error)
    at = _register(at, "Guardiola", password="kisa")
    assert at.error and _auth(at) is None
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_login_logout_and_generic_error_with_lockout():
    at = _register(_app(login=False), "Ancelotti")
    _click(at, "sb_logout")
    assert _auth(at) is None and not at.tabs and at.button(key="login_btn")

    at = _login(at, "Ancelotti", "Yanlis1234")
    wrong = [e.value for e in at.error]
    at = _login(at, "YokBoyle", "Yanlis1234")
    assert wrong and [e.value for e in at.error] == wrong           # kullanici var/yok ayni mesaj
    for _ in range(3):
        at = _login(at, "Ancelotti", "Yanlis1234")
    at = _login(at, "Ancelotti", PASSWORD)                          # 6. deneme: kilitli
    assert _auth(at) is None and any("Çok fazla hatalı deneme" in e.value for e in at.error)

    fresh = _login(_app(login=False), "ancelotti", PASSWORD)         # kullanici adi buyuk/kucuk harf duyarsiz
    assert _auth(fresh) is not None and _auth(fresh).username == "Ancelotti"


def test_second_manager_gets_an_isolated_career():
    from career_manager import CareerManager
    from database import career_context, session_scope
    from models import GameState

    first = _register(_app(login=False), "Klopp")
    assert _auth(first).career_schema == "public"
    _set_user_team("Istanbul Lions")
    with session_scope() as db:                                     # ilk menajer bir hafta oynadi
        CareerManager(db, seed=3).play_week()

    second = _register(_app(login=False), "Simeone")
    schema = _auth(second).career_schema
    assert schema.startswith("career_")
    with career_context(schema), session_scope() as db:
        state = db.get(GameState, 1)
        assert state.current_week == 1 and state.user_team_id is None and state.user_id == _auth(second).user_id
    with session_scope() as db:
        assert db.get(GameState, 1).current_week == 2               # ilk kariyer degismedi
    assert "Oyun modunu seç" in _html(second)                       # yeni dunya: ilk giris ekrani
    _click(second, "mode_career")
    assert "Hafta 1" in " ".join(c.value for c in second.sidebar.caption)
    assert "Hafta 2" in " ".join(c.value for c in _login(_app(login=False), "Klopp").sidebar.caption)



def test_resolver_fails_closed_and_game_callbacks_require_login(monkeypatch):
    """Betik calisirken oturum yoksa kariyer cozucusu hata verir ('public'e dusmez); oyun callback'leri korunur."""
    import accounts
    import web_app

    state: dict = {}
    monkeypatch.setattr(web_app, "get_script_run_ctx", lambda: object())
    monkeypatch.setattr(web_app.st, "session_state", state)
    with pytest.raises(web_app.NoCareerSession):
        web_app.session_career_schema()
    state["auth"] = accounts.AuthSession(user_id=7, username="x", career_schema="career_7")
    assert web_app.session_career_schema() == "career_7"

    callbacks = {name: getattr(web_app, name) for name in dir(web_app) if name.startswith("cb_")}
    assert len(callbacks) > 20
    for name, fn in callbacks.items():
        assert getattr(fn, "requires_auth", False) is (name not in web_app.PUBLIC_CALLBACKS), name

    del state["auth"]
    touched: list = []
    monkeypatch.setattr(web_app, "manager", lambda db: touched.append(db))
    monkeypatch.setattr(web_app, "session_scope", lambda: touched.append("db") or (_ for _ in ()).throw(AssertionError))
    assert web_app.cb_play_week() is None and web_app.cb_promote() is None
    assert touched == []


def test_logout_and_play_week_in_one_request_never_touch_another_career():
    """Ayni istekte once Cikis sonra Haftayi oyna islense bile ilk menajerin kariyeri ('public') degismez."""
    from career_manager import CareerManager
    from database import career_context, session_scope
    from models import GameState

    _register(_app(login=False), "IlkMenajer")                    # public'i devralir
    second = _register(_app(login=False), "IkinciMenajer")
    schema = _auth(second).career_schema
    _click(second, "mode_career")
    with career_context(schema), session_scope() as db:
        cm = CareerManager(db)
        cm.set_user_team(cm.find_team("Istanbul Lions"))
    second.run()
    with session_scope() as db:
        public_week = db.get(GameState, 1).current_week

    second.button(key="sb_logout").click()
    second.button(key="lg_play").click()
    second.run()
    with session_scope() as db:
        assert db.get(GameState, 1).current_week == public_week
    assert _auth(second) is None
