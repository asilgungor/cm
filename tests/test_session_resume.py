"""
14H oturum surdurme: belirtec (auth.py), accounts.sessions (accounts.py), cerez bileseni ve web akisi (web_common /
web_app) testleri.

    CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_session_resume.py     # yalnizca saf bolum
    TEST_DB_NAME=fm_db_test_14h python -m pytest -q -p no:cacheprovider tests/test_session_resume.py

Kapsam: belirtec entropisi ve bicimi, yalnizca ozet saklama, cerezden devam (AppTest: tarayici cerezleri
web_common.browser_cookies ile verilir -- AppTest cerez desteklemez), kayan sure + mutlak sinir, suresi dolmus /
iptal edilmis / sahte / bicim disi belirtec, baska kullanicinin belirteci (yalnizca kendi semasina), kariyer semasi
yoksa fail-closed, cikis (iptal + cerez silme), "tum cihazlarda cikis", acik oturumun periyodik yeniden dogrulamasi,
ayni koken denetimi ve cerez onay protokolu.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth  # noqa: E402

PASSWORD = "Gizli.Parola42"
USER_A, USER_B = "Devam_A", "Devam_B"


# ===========================================================================
# 1) SAF: belirtec, bilesen betigi, cerez adi, koken denetimi, onay protokolu
# ===========================================================================

def test_session_token_has_256_bits_and_a_strict_format():
    tokens = {auth.new_session_token() for _ in range(500)}
    assert len(tokens) == 500
    for token in list(tokens)[:50]:
        assert len(token) == auth.SESSION_TOKEN_CHARS == 43 and auth.is_session_token(token)
        raw = __import__("base64").urlsafe_b64decode(token + "=")
        assert len(raw) * 8 == 256
        digest = auth.session_token_hash(token)
        assert digest == hashlib.sha256(token.encode()).hexdigest() and len(digest) == auth.SESSION_HASH_CHARS
    token = next(iter(tokens))
    for bad in (None, 42, b"x" * 43, "", token[:-1], token + "A", token + "\n", token[:-1] + "=", token[:-1] + "+",
                token[:-1] + "/", token[:-1] + "ş", " " + token[1:], token[:-1] + ";"):
        assert not auth.is_session_token(bad), repr(bad)
        with pytest.raises(auth.AuthError):
            auth.session_token_hash(bad)
    assert auth.same_digest("ab" * 32, "ab" * 32) and not auth.same_digest("ab" * 32, "ab" * 31 + "ac")
    assert not auth.same_digest(None, "x") and not auth.same_digest("x", 1)


def test_malformed_tokens_never_reach_the_database(monkeypatch):
    import accounts

    def boom():
        raise AssertionError("veritabanina gidilmemeliydi")

    monkeypatch.setattr(accounts, "_accounts_scope", boom)
    for bad in (None, "", "kisa", "A" * 44, "A" * 42 + "\n", {"t": 1}, "' OR 1=1 --" + "A" * 32):
        assert accounts.resume_session(bad) is None
    assert accounts.touch_session(None) is False and accounts.revoke_session("id") is False


def test_session_table_stores_only_the_hash_and_cascades_with_the_user():
    import models

    table = models.UserSession.__table__
    assert table.schema == "accounts" and table.name == "sessions"
    assert "token_hash" in table.c and not {c.name for c in table.c} & {"token", "token_plain", "career_schema"}
    assert {fk.ondelete for fk in table.c.user_id.foreign_keys} == {"CASCADE"}
    assert any(getattr(c, "name", None) == "uq_session_token_hash" for c in table.constraints)
    assert "<UserSession" in repr(models.UserSession(id=1, user_id=2, token_hash="f" * 64))
    assert "f" * 64 not in repr(models.UserSession(id=1, user_id=2, token_hash="f" * 64))


def test_issued_session_repr_hides_the_token():
    import accounts

    binding = accounts.SessionBinding(token_id=1, token_hash="a" * 64, user_id=2, remember=True)
    issued = accounts.IssuedSession(token="T" * 43, binding=binding, max_age=604800)
    assert "T" * 43 not in repr(issued) and "604800" in repr(issued)


def test_cookie_component_script_sets_strict_flags_and_uses_no_dangerous_apis():
    js = (ROOT / "web_assets" / "session_cookie.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.lstrip().startswith("//"))
    assert "Path=/" in code and "SameSite=Strict" in code and '"; Secure"' in code and '"https:"' in code
    assert "Max-Age=" in code and "document.cookie" in code
    assert "/^(__Host-)?ofm_sid_[0-9]{1,5}$/" in code and "/^[A-Za-z0-9_-]{43}$/" in code   # ad / deger enjeksiyonu yok
    assert '"__Host-"' in code and "!secure()" in code                                  # __Host- yalnizca https
    for banned in ("eval(", "new Function", "innerHTML", "outerHTML", "fetch(", "XMLHttpRequest", "localStorage",
                   "sessionStorage", "location.href", "location.search", "history.", "console.", "HttpOnly",
                   "Domain="):
        assert banned not in code, banned
    assert 'setTriggerValue("done"' in code and "state.done[nonce]" in code         # nonce basina tek uygulama


def test_cookie_name_is_scoped_to_the_server_port(monkeypatch):
    import web_common

    headers: dict = {}
    monkeypatch.setattr(web_common, "browser_headers", lambda: headers)
    monkeypatch.setattr(web_common.st, "get_option", lambda name: 8502 if name == "server.port" else None)
    assert web_common.session_cookie_name() == "ofm_sid_8502"
    headers.update({"Origin": "https://ofm.example.com", "Host": "ofm.example.com"})
    assert web_common.session_cookie_name() == "__Host-ofm_sid_8502"                 # https: __Host- onekli
    headers.clear()
    headers["X-Forwarded-Proto"] = "https"
    assert web_common.session_cookie_name() == "__Host-ofm_sid_8502"
    headers.update({"Origin": "http://localhost:8502"})                              # Origin varsa o belirler
    assert web_common.session_cookie_name() == "ofm_sid_8502"
    monkeypatch.setattr(web_common.st, "get_option", lambda name: "bozuk")
    assert web_common.session_cookie_name() == "ofm_sid_0"


@pytest.mark.parametrize("headers,ok", [
    ({}, True),                                                                # AppTest / tarayici disi istemci
    ({"Origin": "http://localhost:8501", "Host": "localhost:8501"}, True),
    ({"Origin": "https://ofm.example.com", "Host": "127.0.0.1:8501", "X-Forwarded-Host": "ofm.example.com"}, True),
    ({"Origin": "http://localhost:9999", "Host": "localhost:8501"}, False),    # ayni site, baska port
    ({"Origin": "https://evil.example", "Host": "localhost:8501"}, False),
    ({"Origin": "null", "Host": "localhost:8501"}, False),
])
def test_resume_is_only_attempted_on_a_same_origin_handshake(monkeypatch, headers, ok):
    import web_common

    monkeypatch.setattr(web_common, "browser_headers", lambda: headers)
    assert web_common.same_origin_request() is ok


@pytest.fixture()
def web_state(monkeypatch):
    import web_common

    state: dict = {}
    monkeypatch.setattr(web_common.st, "session_state", state)
    return state


def test_resume_candidate_is_read_once_per_browser_session(web_state, monkeypatch):
    import web_common

    token = auth.new_session_token()
    monkeypatch.setattr(web_common, "browser_cookies", lambda: {web_common.session_cookie_name(): token})
    monkeypatch.setattr(web_common, "browser_headers", lambda: {})
    assert web_common.cookie_token_for_resume() == token
    assert web_common.cookie_token_for_resume() is None                      # ikinci cizim: veritabanina gidilmez
    web_state.clear()
    web_state["auth"] = object()
    assert web_common.cookie_token_for_resume() is None                      # oturum varken aday yok
    web_state.clear()
    monkeypatch.setattr(web_common, "browser_headers",
                        lambda: {"Origin": "http://localhost:1234", "Host": "localhost:8501"})
    assert web_common.cookie_token_for_resume() is None


def test_cookie_ack_must_match_the_pending_nonce(web_state):
    import web_common

    token = auth.new_session_token()
    web_common.queue_cookie_set(token, 604800)
    pending = web_state[web_common.COOKIE_OP_KEY]
    assert pending["op"] == "set" and pending["token"] == token and len(pending["nonce"]) == 16
    assert web_common._cookie_payload() == pending

    web_state[web_common.COOKIE_WIDGET_KEY] = {"done": {"n": "baska-nonce", "ok": True}}
    web_common._cookie_done()
    assert web_common.COOKIE_OP_KEY in web_state and web_common.COOKIE_OK_KEY not in web_state
    web_state[web_common.COOKIE_WIDGET_KEY] = {"done": "bozuk"}
    web_common._cookie_done()
    assert web_common.COOKIE_OP_KEY in web_state

    web_state[web_common.COOKIE_WIDGET_KEY] = {"done": {"n": pending["nonce"], "ok": True}}
    web_common._cookie_done()
    assert web_common.COOKIE_OP_KEY not in web_state and web_state[web_common.COOKIE_OK_KEY] is True
    assert token not in repr(web_state) and web_common._cookie_payload() == {"op": "none"}

    web_common.queue_cookie_clear()
    assert web_common.COOKIE_OK_KEY not in web_state and web_state[web_common.COOKIE_OP_KEY]["op"] == "clear"
    assert "token" not in web_state[web_common.COOKIE_OP_KEY]


def test_callback_guards_refuse_a_revoked_token_even_without_a_full_rerun(web_state, monkeypatch):
    """Parca (st.fragment) yeniden calismalari main'e ugramaz: iptal edilen belirtecle callback yine calismaz."""
    import accounts
    import web_common

    calls: list = []
    checks: list = []
    valid = {"ok": True}

    def touch(binding):
        checks.append(binding)
        return valid["ok"]

    monkeypatch.setattr(accounts, "touch_session", touch)
    guarded = [web_common.requires_auth(lambda: calls.append("r")), web_common.member_callback(lambda: calls.append("m")),
               web_common.admin_callback(lambda: calls.append("a"))]
    web_state["auth"] = accounts.AuthSession(user_id=7, username="x", career_schema="public")
    for fn in guarded:                                                   # belirtecsiz oturum: sorgu yok, calisir
        fn()
    assert calls == ["r", "m", "a"] and checks == []

    binding = accounts.SessionBinding(token_id=1, token_hash="a" * 64, user_id=7, remember=True)
    web_common.bind_session_token(binding)
    web_state[web_common.REVALIDATE_KEY] = 0.0                           # denetim vakti geldi
    guarded[0]()
    assert calls[-1] == "r" and checks == [binding]
    guarded[1]()
    assert calls[-1] == "m" and len(checks) == 1                         # dakikada bir: onbellek

    valid["ok"] = False
    web_state[web_common.REVALIDATE_KEY] = 0.0
    calls.clear()
    for fn in guarded:
        assert fn() is None
    assert calls == [] and web_common.revalidation_due()                 # main bir sonraki cizimde oturumu kapatir

    web_state[web_common.SESSION_BINDING_KEY] = accounts.SessionBinding(1, "a" * 64, 8, True)   # baska kullanici
    valid["ok"] = True
    assert guarded[0]() is None and calls == []


def test_world_switch_keeps_the_session_binding():
    import web_common

    for key in (web_common.SESSION_BINDING_KEY, web_common.COOKIE_OP_KEY, web_common.COOKIE_OK_KEY,
                web_common.RESUME_TRIED_KEY, web_common.REVALIDATE_KEY):
        assert key in web_common.SESSION_KEEP_KEYS


# ===========================================================================
# 2) VERITABANI: accounts.issue / resume / touch / revoke
# ===========================================================================

def _db_available() -> bool:
    if os.getenv("CM_TEST_NO_DB"):
        return False
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


def _assert_test_database() -> None:
    import database

    expected = os.getenv("TEST_DB_NAME", "fm_db_test")
    actual = database.engine.url.database
    if actual != expected or actual == os.getenv("DB_NAME", "fm_db") or actual == "fm_db":
        raise RuntimeError(f"Oturum testleri yalnızca test veritabanında çalışır (bağlantı: {actual}).")


def _wipe_accounts() -> None:
    from sqlalchemy import text

    import database

    _assert_test_database()
    database.init_accounts()
    with database.engine.begin() as conn:
        for schema in conn.execute(text(
                r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 'career\_%'")).scalars().all():
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.execute(text("DELETE FROM accounts.worlds"))
        conn.execute(text("DELETE FROM accounts.users"))                       # sessions: CASCADE
        if conn.scalar(text("SELECT to_regclass('public.game_state') IS NOT NULL")):
            conn.execute(text("UPDATE public.game_state SET user_id = NULL"))


def _sql(statement: str, **params):
    from sqlalchemy import text

    import database

    with database.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return result.first() if result.returns_rows else None


@pytest.fixture(scope="module")
def users():
    """A 'public' kariyeri devralir (kulubu secili), B yeni bir kariyer semasi alir (sentetik dunya)."""
    if not _db_available():
        pytest.skip("PostgreSQL erişilemiyor")
    import accounts
    from tests.test_web_app import _reseed, _set_user_team

    _assert_test_database()
    _wipe_accounts()
    _reseed()
    _set_user_team("Istanbul Lions")
    a = accounts.register(USER_A, PASSWORD)
    b = accounts.register(USER_B, PASSWORD)
    assert a.career_schema == "public" and b.career_schema.startswith("career_")
    yield {"A": a, "B": b}
    _wipe_accounts()
    _reseed()


@integration
@pytest.mark.integration
def test_issue_stores_only_the_hash_and_resume_rebuilds_the_login_session(users):
    import accounts

    a = users["A"]
    issued = accounts.issue_session(a.user_id, remember=True, user_agent="Mozilla/5.0 " + "x" * 300 + "\x00")
    token = issued.token
    assert auth.is_session_token(token) and issued.max_age == 7 * 24 * 3600
    row = _sql("SELECT t::text AS dump, token_hash, remember, user_agent, "
               "extract(epoch FROM expires_at - created_at) AS ttl FROM accounts.sessions t WHERE id = :i",
               i=issued.binding.token_id)
    assert token not in row.dump and row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.remember is True and len(row.user_agent) == 120 and "\x00" not in row.user_agent
    assert abs(row.ttl - 7 * 24 * 3600) < 5

    session, binding = accounts.resume_session(token)
    login = accounts.authenticate(USER_A, PASSWORD)
    assert (session.user_id, session.username, session.career_schema) == \
        (login.user_id, login.username, login.career_schema) == (a.user_id, USER_A, "public")
    assert session.world_id is None and session.world_kind is None                   # dunya: giristeki gibi web'de
    assert binding == issued.binding
    assert accounts.touch_session(binding) is True

    browser = accounts.issue_session(a.user_id, remember=False)
    assert browser.max_age is None and browser.binding.remember is False
    ttl = _sql("SELECT extract(epoch FROM expires_at - created_at) AS s FROM accounts.sessions WHERE id = :i",
               i=browser.binding.token_id).s
    assert abs(ttl - 12 * 3600) < 5


@integration
@pytest.mark.integration
def test_every_login_issues_a_fresh_token_and_unknown_users_get_none(users):
    import accounts

    first = accounts.issue_session(users["A"].user_id)
    second = accounts.issue_session(users["A"].user_id)
    assert first.token != second.token and first.binding.token_id != second.binding.token_id
    with pytest.raises(accounts.AccountError):
        accounts.issue_session(987_654)


@integration
@pytest.mark.integration
def test_expiry_slides_but_never_past_the_absolute_lifetime(users):
    import accounts

    issued = accounts.issue_session(users["A"].user_id)
    i = issued.binding.token_id
    _sql("UPDATE accounts.sessions SET created_at = now() - interval '29 days 12 hours', "
         "last_seen_at = now() - interval '1 day', expires_at = now() + interval '1 hour' WHERE id = :i", i=i)
    assert accounts.resume_session(issued.token) is not None
    row = _sql("SELECT extract(epoch FROM (created_at + interval '30 days') - expires_at) AS gap, "
               "extract(epoch FROM expires_at - now()) AS left, extract(epoch FROM now() - last_seen_at) AS seen "
               "FROM accounts.sessions WHERE id = :i", i=i)
    assert abs(row.gap) < 5 and 11 * 3600 < row.left < 13 * 3600 and row.seen < 5   # 7 gune degil 30. gune kadar

    fresh = accounts.issue_session(users["A"].user_id)
    _sql("UPDATE accounts.sessions SET created_at = now() - interval '2 days', expires_at = now() + interval '1 hour' "
         "WHERE id = :i", i=fresh.binding.token_id)
    assert accounts.resume_session(fresh.token) is not None
    left = _sql("SELECT extract(epoch FROM expires_at - now()) AS s FROM accounts.sessions WHERE id = :i",
                i=fresh.binding.token_id).s
    assert abs(left - 7 * 24 * 3600) < 5                                               # kayan pencere yenilendi

    old = accounts.issue_session(users["A"].user_id)
    _sql("UPDATE accounts.sessions SET created_at = now() - interval '31 days', expires_at = now() + interval '1 hour' "
         "WHERE id = :i", i=old.binding.token_id)
    assert accounts.resume_session(old.token) is None and accounts.touch_session(old.binding) is False


@integration
@pytest.mark.integration
def test_expired_revoked_forged_and_malformed_tokens_are_rejected(users):
    import accounts

    a = users["A"]
    expired = accounts.issue_session(a.user_id)
    _sql("UPDATE accounts.sessions SET created_at = now() - interval '8 days', expires_at = now() - interval '1 second' "
         "WHERE id = :i", i=expired.binding.token_id)
    assert accounts.resume_session(expired.token) is None and accounts.touch_session(expired.binding) is False

    revoked = accounts.issue_session(a.user_id)
    assert accounts.revoke_session(revoked.binding) is True
    assert accounts.revoke_session(revoked.binding) is False                           # ikinci kez: zaten iptal
    assert accounts.resume_session(revoked.token) is None and accounts.touch_session(revoked.binding) is False

    assert accounts.resume_session(auth.new_session_token()) is None                   # hic verilmemis (sahte)
    live = accounts.issue_session(a.user_id)
    tampered = live.token[:-1] + ("A" if live.token[-1] != "A" else "B")
    assert accounts.resume_session(tampered) is None and accounts.resume_session(live.token.lower()) is None
    stolen_hash = accounts.SessionBinding(token_id=live.binding.token_id, token_hash="0" * 64,
                                          user_id=a.user_id, remember=True)
    assert accounts.touch_session(stolen_hash) is False and accounts.revoke_session(stolen_hash) is False
    assert accounts.resume_session(live.token) is not None                             # gercek belirtec etkilenmedi


@integration
@pytest.mark.integration
def test_another_users_token_reaches_only_that_users_career(users):
    import accounts

    a, b = users["A"], users["B"]
    token_a = accounts.issue_session(a.user_id)
    token_b = accounts.issue_session(b.user_id)
    session_b, binding_b = accounts.resume_session(token_b.token)
    assert (session_b.user_id, session_b.career_schema) == (b.user_id, b.career_schema) != (a.user_id, "public")
    session_a, _ = accounts.resume_session(token_a.token)
    assert session_a.career_schema == "public"
    # B'nin satiri A'nin kimligiyle kullanilamaz (id + ozet + kullanici birlikte eslesmeli)
    forged = accounts.SessionBinding(binding_b.token_id, binding_b.token_hash, a.user_id, True)
    assert accounts.touch_session(forged) is False and accounts.revoke_session(forged) is False
    assert accounts.touch_session(binding_b) is True


@integration
@pytest.mark.integration
def test_missing_or_invalid_career_schema_fails_closed_and_revokes(users):
    import accounts

    b = users["B"]
    for broken in ("career_999999", None):
        issued = accounts.issue_session(b.user_id)
        _sql("UPDATE accounts.users SET career_schema = :s WHERE id = :u", s=broken, u=b.user_id)
        try:
            assert accounts.resume_session(issued.token) is None
        finally:
            _sql("UPDATE accounts.users SET career_schema = :s WHERE id = :u", s=b.career_schema, u=b.user_id)
        assert _sql("SELECT revoked_at IS NOT NULL AS r FROM accounts.sessions WHERE id = :i",
                    i=issued.binding.token_id).r
        assert accounts.resume_session(issued.token) is None                           # sema geri gelse de iptal
    assert accounts.resume_session(accounts.issue_session(b.user_id).token) is not None


@integration
@pytest.mark.integration
def test_revoke_all_counts_and_caps_active_sessions(users):
    import accounts

    a, b = users["A"], users["B"]
    accounts.revoke_all_sessions(a.user_id)
    mine = [accounts.issue_session(a.user_id) for _ in range(3)]
    other = accounts.issue_session(b.user_id)
    assert accounts.active_session_count(a.user_id) == 3
    assert accounts.revoke_all_sessions(a.user_id) == 3 and accounts.active_session_count(a.user_id) == 0
    assert all(accounts.resume_session(s.token) is None for s in mine)
    assert accounts.resume_session(other.token) is not None                             # baska hesap etkilenmez

    many = [accounts.issue_session(a.user_id) for _ in range(accounts.MAX_ACTIVE_SESSIONS + 3)]
    assert accounts.active_session_count(a.user_id) == accounts.MAX_ACTIVE_SESSIONS
    assert accounts.resume_session(many[-1].token) is not None                          # en yeniler kalir
    assert accounts.resume_session(many[0].token) is None                               # en eski iptal
    _sql("UPDATE accounts.sessions SET revoked_at = now() - interval '40 days', created_at = now() - interval '41 days' "
         "WHERE id = :i", i=many[0].binding.token_id)
    accounts.issue_session(a.user_id)                                                    # eski iptal satiri silinir
    assert _sql("SELECT count(*) AS n FROM accounts.sessions WHERE id = :i", i=many[0].binding.token_id).n == 0
    accounts.revoke_all_sessions(a.user_id)


# ===========================================================================
# 3) WEB (AppTest): giris -> cerez, yenileme -> devam, cikis, red, yeniden dogrulama
# ===========================================================================

APP = str(ROOT / "web_app.py")


def _component(at):
    """Cerez bileseni (yalnizca bekleyen islem varken cizilir); yoksa (None, None)."""
    import web_common

    for element in at.get("bidi_component"):
        if element.proto.component_name.endswith(web_common.COOKIE_COMPONENT):
            raw = element.proto.mixed.json if element.proto.WhichOneof("data") == "mixed" else element.proto.json
            return element, json.loads(raw)
    return None, None


def _cookie_op(at) -> dict:
    """Bekleyen cerez islemi; bilesen cizilmediyse {'op': 'none'} (sayfada cerez bileseni yok)."""
    return _component(at)[1] or {"op": "none"}


def _ack(at, ok: bool = True):
    """Tarayicinin onayi: bilesen 'done' tetigini {n: nonce, ok} ile yollar."""
    from streamlit.proto.WidgetStates_pb2 import WidgetState

    element, data = _component(at)
    assert element is not None, "onaylanacak çerez işlemi yok"
    states = at._tree.get_widget_states()
    states.widgets.extend([
        WidgetState(id=element.proto.id, json_value="{}"),
        WidgetState(id=f"$$STREAMLIT_INTERNAL_KEY_{element.proto.id}__events",
                    json_trigger_value=json.dumps({"event": "done", "value": {"n": data["nonce"], "ok": ok}})),
    ])
    at._run(states)
    assert not at.exception, at.exception
    return at


def _browser(monkeypatch, token: str | None, headers: dict | None = None, page: str | None = None):
    """Yeni tarayici oturumu (sayfa yenileme / sunucu yeniden baslatmasi): cerez el sikismasindan okunur."""
    from streamlit.testing.v1 import AppTest

    import web_common

    cookies = {web_common.session_cookie_name(): token} if token else {}
    monkeypatch.setattr(web_common, "browser_cookies", lambda: cookies)
    monkeypatch.setattr(web_common, "browser_headers", lambda: dict(headers or {}))
    at = AppTest.from_file(APP, default_timeout=120)
    if page is not None:
        at.query_params["sayfa"] = page
    at.run()
    assert not at.exception, at.exception
    return at


def _auth(at):
    try:
        return at.session_state["auth"]
    except KeyError:
        return None


def _state(at, key, default=None):
    try:
        return at.session_state[key]
    except KeyError:
        return default


def _login_ui(at, username: str, remember: bool = True):
    at.text_input(key="login_user").set_value(username)
    at.text_input(key="login_pass").set_value(PASSWORD)
    if not remember:
        at.checkbox(key="auth_remember").uncheck()
    at.button(key="login_btn").click()
    at.run()
    assert not at.exception, at.exception
    return at


def _revoked(token_id: int) -> bool:
    return bool(_sql("SELECT revoked_at IS NOT NULL AS r FROM accounts.sessions WHERE id = :i", i=token_id).r)


@integration
@pytest.mark.integration
def test_login_issues_a_token_and_the_component_writes_then_forgets_it(users, monkeypatch):
    import web_common

    at = _browser(monkeypatch, None)
    assert _auth(at) is None and at.checkbox(key="auth_remember").value is True
    assert _cookie_op(at) == {"op": "none"}
    at = _login_ui(at, USER_A)
    assert _auth(at).user_id == users["A"].user_id
    binding = _state(at, web_common.SESSION_BINDING_KEY)
    op = _cookie_op(at)
    assert op["op"] == "set" and op["name"] == web_common.session_cookie_name() and op["max_age"] == 7 * 24 * 3600
    assert hashlib.sha256(op["token"].encode()).hexdigest() == binding.token_hash
    assert _sql("SELECT user_id, remember FROM accounts.sessions WHERE id = :i", i=binding.token_id) == \
        (users["A"].user_id, True)
    assert _state(at, web_common.COOKIE_OK_KEY) is None
    token = op["token"]

    _ack(at)
    assert _component(at)[0] is None                                                     # onaydan sonra bilesen yok
    assert _cookie_op(at) == {"op": "none"} and _state(at, web_common.COOKIE_OK_KEY) is True
    assert web_common.COOKIE_OP_KEY not in at.session_state
    assert all(token not in repr(_state(at, key)) for key in at.session_state)          # duz belirtec unutuldu

    other = _login_ui(_browser(monkeypatch, None), USER_A, remember=False)
    op = _cookie_op(other)
    assert op["op"] == "set" and op["max_age"] is None                                   # tarayici oturumu cerezi
    assert _sql("SELECT remember FROM accounts.sessions WHERE id = :i",
                i=_state(other, web_common.SESSION_BINDING_KEY).token_id).remember is False


@integration
@pytest.mark.integration
def test_refresh_and_server_restart_resume_the_same_session(users, monkeypatch):
    import accounts
    import web_common

    issued = accounts.issue_session(users["A"].user_id)
    at = _browser(monkeypatch, issued.token, headers={"Origin": "http://localhost:8501", "Host": "localhost:8501"})
    session = _auth(at)
    assert session is not None and (session.user_id, session.username) == (users["A"].user_id, USER_A)
    assert session.career_schema == "public" and not [b for b in at.button if b.key == "login_btn"]
    assert _state(at, web_common.SESSION_BINDING_KEY) == issued.binding
    assert not any("Hoş geldin" in s.value for s in at.sidebar.success)                 # sessiz devam
    op = _cookie_op(at)
    assert op["op"] == "set" and op["token"] == issued.token and op["max_age"] == 7 * 24 * 3600   # Max-Age kayar
    assert _state(at, web_common.COOKIE_OK_KEY) is True

    at.run()                                                                             # sonraki cizimler: onbellek
    assert _auth(at).user_id == users["A"].user_id and not at.exception

    session_b = _auth(_browser(monkeypatch, accounts.issue_session(users["B"].user_id).token))
    assert (session_b.user_id, session_b.career_schema) == (users["B"].user_id, users["B"].career_schema)


@integration
@pytest.mark.integration
@pytest.mark.parametrize("case", ["forged", "expired", "revoked", "malformed"])
def test_bad_cookies_show_the_login_page_and_clear_the_cookie(users, monkeypatch, case):
    import accounts
    import web_app

    token = auth.new_session_token()
    if case in ("expired", "revoked"):
        issued = accounts.issue_session(users["A"].user_id)
        token = issued.token
        if case == "expired":
            _sql("UPDATE accounts.sessions SET created_at = now() - interval '8 days', "
                 "expires_at = now() - interval '1 second' WHERE id = :i", i=issued.binding.token_id)
        else:
            accounts.revoke_session(issued.binding)
    elif case == "malformed":
        token = "kisa;Path=/"
    at = _browser(monkeypatch, token)
    assert _auth(at) is None and at.button(key="login_btn")
    op = _cookie_op(at)
    assert op["op"] == "clear" and "token" not in op
    assert any(web_app.SESSION_ENDED_TEXT in i.value for i in at.info)
    at.run()
    assert _auth(at) is None                                                             # ayni oturumda tekrar yok


@integration
@pytest.mark.integration
def test_cross_origin_handshake_does_not_resume_or_touch_the_cookie(users, monkeypatch):
    import accounts

    issued = accounts.issue_session(users["A"].user_id)
    at = _browser(monkeypatch, issued.token, headers={"Origin": "http://localhost:9999", "Host": "localhost:8501"})
    assert _auth(at) is None and _cookie_op(at) == {"op": "none"}
    assert accounts.touch_session(issued.binding) is True                                # belirtec gecerli kaldi


@integration
@pytest.mark.integration
def test_logout_revokes_the_token_and_clears_the_cookie(users, monkeypatch):
    import accounts
    import web_common

    issued = accounts.issue_session(users["A"].user_id)
    at = _browser(monkeypatch, issued.token)
    assert _auth(at) is not None
    at.button(key="sb_logout").click()
    at.run()
    assert not at.exception and _auth(at) is None and at.button(key="login_btn")
    assert _revoked(issued.binding.token_id)
    op = _cookie_op(at)
    assert op["op"] == "clear" and web_common.SESSION_BINDING_KEY not in at.session_state
    assert any("çıkış yaptı" in i.value for i in at.info)
    at.run()                                                                             # bayat el sikisma cerezi
    assert _auth(at) is None
    _ack(at)
    assert _cookie_op(at) == {"op": "none"}

    again = _browser(monkeypatch, issued.token)                                          # silinmemis cerezle yenileme
    assert _auth(again) is None and _cookie_op(again)["op"] == "clear"


@integration
@pytest.mark.integration
def test_open_session_is_closed_after_its_token_is_revoked_elsewhere(users, monkeypatch):
    import accounts
    import web_app
    import web_common

    issued = accounts.issue_session(users["A"].user_id)
    at = _browser(monkeypatch, issued.token)
    assert _auth(at) is not None
    accounts.revoke_all_sessions(users["A"].user_id)                                      # baska cihazdan
    at.run()
    assert _auth(at) is not None                                                         # dakikada bir dogrulanir
    at.session_state[web_common.REVALIDATE_KEY] = 0.0
    at.run()
    assert not at.exception and _auth(at) is None and at.button(key="login_btn")
    assert _cookie_op(at)["op"] == "clear"
    assert any(web_app.SESSION_REVOKED_TEXT in w.value for w in at.warning)

    live = accounts.issue_session(users["A"].user_id)
    ok = _browser(monkeypatch, live.token)
    ok.session_state[web_common.REVALIDATE_KEY] = 0.0
    ok.run()
    assert _auth(ok) is not None and ok.session_state[web_common.REVALIDATE_KEY] > 0.0   # gecerli: suresi kaydi


@integration
@pytest.mark.integration
def test_revoked_token_cannot_play_a_week_before_the_session_closes(users, monkeypatch):
    """Callback'ler main'den ONCE calisir: iptal edilen belirtecle 'Devam' (hafta oynat) veriyi degistirmez."""
    import accounts
    import web_common

    issued = accounts.issue_session(users["A"].user_id)
    at = _browser(monkeypatch, issued.token)
    week = _sql("SELECT current_week FROM public.game_state WHERE id = 1").current_week
    assert at.button(key="nav_continue")
    accounts.revoke_session(issued.binding)
    at.session_state[web_common.REVALIDATE_KEY] = 0.0
    at.button(key="nav_continue").click()
    at.run()
    assert not at.exception and _auth(at) is None and at.button(key="login_btn")
    assert _sql("SELECT current_week FROM public.game_state WHERE id = 1").current_week == week


@integration
@pytest.mark.integration
def test_logout_everywhere_revokes_every_token_of_the_account(users, monkeypatch):
    import accounts

    accounts.revoke_all_sessions(users["A"].user_id)
    here = accounts.issue_session(users["A"].user_id)
    phone = accounts.issue_session(users["A"].user_id)
    b = accounts.issue_session(users["B"].user_id)
    at = _browser(monkeypatch, here.token, page="secenekler")
    assert "Açık oturum: **2**" in " ".join(c.value for c in at.caption)
    at.button(key="opt_logout_all").click()
    at.run()
    assert not at.exception and _auth(at) is None and _cookie_op(at)["op"] == "clear"
    assert _revoked(here.binding.token_id) and _revoked(phone.binding.token_id)
    assert not _revoked(b.binding.token_id)
    assert accounts.active_session_count(users["A"].user_id) == 0


@integration
@pytest.mark.integration
def test_preset_session_without_a_token_still_works_and_is_not_revalidated(users, monkeypatch):
    from streamlit.testing.v1 import AppTest

    import web_common
    from accounts import AuthSession

    monkeypatch.setattr(web_common, "browser_cookies", lambda: {})
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["auth"] = AuthSession(user_id=0, username="test_menajer", career_schema="public")
    at.session_state[web_common.REVALIDATE_KEY] = 0.0
    at.run()
    assert not at.exception and _auth(at).username == "test_menajer" and _cookie_op(at) == {"op": "none"}
    assert web_common.SESSION_BINDING_KEY not in at.session_state
