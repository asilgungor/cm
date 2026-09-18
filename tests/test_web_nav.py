"""
Faz 13I: CM 01/02 tarzi menu ve sayfa yonlendirme -- Streamlit AppTest (gercek web_app.py) + saf yardimcilar.

Kilitlenenler: gruplu kenar cubugu menusu (nav_to_{slug}), yalnizca secili sayfanin cizilmesi, secimin URL'de
(?sayfa=) tutulmasi ve yenilemede geri gelmesi, takma ad / gecersiz / bu modda olmayan sayfa, telefon ust menusu
(nav_top), ziyaret gecmisinde geri / ileri (nav_back / nav_fwd, sayfa alti nav_foot_*), sayfa alti eylem dugmeleri
(nav_act_{hedef}), kenar cubugu tarihi + belirgin Devam, menu sayaclari, hafta raporundaki masa satirlarinin kacisi.

Kendi veritabaninda calistirin:
    TEST_DB_NAME=fm_db_test_13i python -m pytest -q -p no:cacheprovider tests/test_web_nav.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, menu, page  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    APP,
    _app,
    _click,
    _db_available,
    _login,
    _query,
    _reseed,
    _set_user_team,
    _team,
    _texts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed(mode=None)


def _url_page(at) -> str | None:
    """AppTest calistiktan sonra query_params parse_qs listesi olarak doner."""
    value = at.query_params.get("sayfa")
    return value[0] if isinstance(value, list) and value else value


def _fresh(query: str | None = None, *, state: dict | None = None):
    """Yeni tarayici sekmesi: oturum durumu bos (giris hazir), URL'de ?sayfa=."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=90)
    _login(at)
    for key, value in (state or {}).items():
        at.session_state[key] = value
    if query is not None:
        at.query_params["sayfa"] = query
    at.run()
    assert not at.exception, at.exception
    return at


def _keys(at) -> set[str]:
    return {b.key for b in at.button if b.key}


# ---------------------------------------------------------------------------
# 1) Menu: gruplar, secili dugme, yalnizca secili sayfa
# ---------------------------------------------------------------------------

def test_menu_is_grouped_and_only_the_selected_page_renders():
    import nav_view

    _set_user_team(TEAM)
    at = _app()
    assert page(at) == nav_view.HOME and _url_page(at) == nav_view.HOME
    pages = menu(at)
    assert pages[0] == nav_view.HOME and set(nav_view.CAREER_PAGES) <= set(pages)
    assert nav_view.ADMIN not in pages and nav_view.INBOX not in pages          # tek kisilik kariyer
    groups = _texts(at.sidebar.markdown)
    for group in ("Masa", "Takım", "Müsabakalar", "Kulüp"):
        assert f'class="ofm-nav-group">{group}<' in groups
    assert at.button(key="nav_to_ana-sayfa").proto.type == "primary"
    assert all(at.button(key=nav_view.button_key(s)).proto.type == "secondary" for s in pages if s != nav_view.HOME)
    assert not at.tabs                                                            # ust sekmeler kalkti

    # Ana Sayfa: yalnizca ana sayfanin widget'lari; kadro / transfer / taktik cizilmedi
    keys = _keys(at)
    assert {"home_prep", "home_live"} <= keys and "tac_auto" not in keys
    assert not [t for t in at.text_input if t.key == "mkt_name"]
    assert "ANA SAYFA" in _texts(at.main.markdown).upper()

    goto(at, "Kadro")
    keys = _keys(at)
    assert "tac_auto" in keys and "home_prep" not in keys and "tc_scout" not in keys
    assert at.button(key="nav_to_kadro").proto.type == "primary"
    assert at.button(key="nav_to_ana-sayfa").proto.type == "secondary"
    assert 'class="t">📋 Kadro<' in _texts(at.main.markdown)                       # sayfa basligi bandi

    goto(at, "transfer")
    assert at.text_input(key="mkt_name") and "tac_auto" not in _keys(at)


def test_every_menu_page_renders_without_errors():
    """Her menu sayfasi hatasiz cizilir (goto istisnayi da denetler) ve sayfa basligi bandi o sayfanindir."""
    from html import escape

    import nav_view

    _set_user_team(TEAM)
    at = _app()
    for slug in menu(at):
        goto(at, slug)
        assert f'class="t">{escape(nav_view.PAGES[slug].label)}<' in _texts(at.main.markdown), slug


# ---------------------------------------------------------------------------
# 2) URL: ?sayfa= kalicilik, takma ad, gecersiz / bu modda olmayan sayfa
# ---------------------------------------------------------------------------

def test_page_is_kept_in_the_url_and_restored_on_reload():
    import nav_view

    _set_user_team(TEAM)
    at = _app()
    goto(at, "taktik")
    assert _url_page(at) == nav_view.TACTICS
    goto(at, "transfer")
    assert _url_page(at) == nav_view.TRANSFER

    reloaded = _fresh(_url_page(at))                        # yenileme: bos oturum, ayni URL
    assert page(reloaded) == nav_view.TRANSFER and reloaded.text_input(key="mkt_name")
    assert _url_page(reloaded) == nav_view.TRANSFER


@pytest.mark.parametrize("query,expected", [
    ("lig", "puan-durumu"), ("finans", "kulup"), ("Kadro", "kadro"), ("📋 Kadro", "kadro"),
    ("yok-boyle-sayfa", "ana-sayfa"), ("dunya-yonetimi", "ana-sayfa"), ("mesajlar", "ana-sayfa"), ("", "ana-sayfa"),
])
def test_url_aliases_and_unknown_pages(query, expected):
    _set_user_team(TEAM)
    at = _fresh(query)
    assert page(at) == expected and _url_page(at) == expected         # URL kanonik slug'a duzeltilir


def test_session_selection_wins_over_a_stale_url():
    _set_user_team(TEAM)
    at = _fresh("kadro")
    goto(at, "fikstur")
    at.run()                                                           # URL guncellendi, oturum ayni sayfada
    assert page(at) == "fikstur" and _url_page(at) == "fikstur"


# ---------------------------------------------------------------------------
# 3) Telefon ust menusu
# ---------------------------------------------------------------------------

def test_phone_top_nav_switches_pages_and_follows_the_menu():
    import nav_view

    _set_user_team(TEAM)
    at = _app()
    top = at.selectbox(key="nav_top")
    assert top.value == nav_view.HOME and list(top.options) == [nav_view.label(s) for s in menu(at)]
    assert at.button(key="top_continue") and at.button(key="top_back") and at.button(key="top_fwd")
    top.set_value(nav_view.TACTICS)
    at.run()
    assert not at.exception, at.exception
    assert page(at) == nav_view.TACTICS and _url_page(at) == nav_view.TACTICS
    goto(at, "kadro")                                                  # kenar menusu -> ust secici esitlenir
    assert at.selectbox(key="nav_top").value == nav_view.SQUAD
    # Masaustunde gizli, telefonda (<= 768 px) gorunur
    css = nav_view.NAV_CSS
    assert ".st-key-ofm_topnav{display:none !important}" in css
    assert "@media (max-width:768px)" in css.split(".st-key-ofm_topnav{display:none !important}")[1]


# ---------------------------------------------------------------------------
# 4) Geri / ileri gecmisi, sayfa alti eylemleri
# ---------------------------------------------------------------------------

def test_back_and_forward_walk_the_visit_history():
    import nav_view

    _set_user_team(TEAM)
    at = _app()
    assert at.button(key="nav_back").disabled and at.button(key="nav_fwd").disabled
    goto(at, "kadro")
    goto(at, "transfer")
    _click(at, "nav_back")
    assert page(at) == nav_view.SQUAD and not at.button(key="nav_fwd").disabled
    _click(at, "nav_foot_back")                                        # sayfa altindaki ◀ Geri ayni gecmis
    assert page(at) == nav_view.HOME and at.button(key="nav_back").disabled
    assert _url_page(at) == nav_view.HOME
    _click(at, "nav_fwd")
    assert page(at) == nav_view.SQUAD
    goto(at, "taktik")                                                 # yeni ziyaret ileri gecmisi keser
    assert at.button(key="nav_fwd").disabled
    assert at.session_state[nav_view.HISTORY_KEY] == [nav_view.HOME, nav_view.SQUAD, nav_view.TACTICS]


def test_footer_actions_jump_to_related_pages():
    import nav_view
    import web_app

    _set_user_team(TEAM)
    at = _app(page="kadro")
    wanted = {f"nav_act_{slug}" for _label, slug in web_app.FOOTER_ACTIONS[nav_view.SQUAD]}
    assert wanted <= _keys(at) and "nav_act_kadro" not in _keys(at)
    _click(at, "nav_act_taktik")
    assert page(at) == nav_view.TACTICS and _url_page(at) == nav_view.TACTICS
    goto(at, "canli-mac")                                              # canli mac: sayfa alti yok (13C)
    assert "nav_foot_back" not in _keys(at) and "nav_act_kadro" not in _keys(at)


# ---------------------------------------------------------------------------
# 5) Kenar cubugu: tarih, Devam, sayaclar
# ---------------------------------------------------------------------------

def test_sidebar_date_and_continue_play_the_week_without_leaving_the_page():
    import nav_view

    _set_user_team(TEAM)
    at = _app(page="puan-durumu")
    assert "Sezon 1 · 1. hafta" in _texts(at.sidebar.markdown)
    assert at.button(key="nav_continue").proto.type == "primary"
    _click(at, "nav_continue")
    assert _query(lambda db: __import__("career_manager").CareerManager(db).current_week) == 2
    assert page(at) == nav_view.TABLE                                  # Devam sayfayi degistirmez
    assert "Sezon 1 · 2. hafta" in _texts(at.sidebar.markdown)
    goto(at, "ana-sayfa")
    assert "2. haftayı oyna" in at.button(key="home_continue").label   # ana sayfadaki Devam da ayni eylem
    assert any("S1 H1" in b.label for b in at.button if (b.key or "").startswith("home_msg_"))   # hafta raporu


def test_menu_counts_show_pending_wage_demands():
    _set_user_team(TEAM)
    at = _app()
    assert at.button(key="nav_to_kadro").label == "📋 Kadro"
    from database import session_scope

    with session_scope() as db:
        players = _team(db, TEAM).players[:2]
        for p in players:
            p.wage_demand = 90_000
    at.run()
    assert at.button(key="nav_to_kadro").label == "📋 Kadro (2)"


# ---------------------------------------------------------------------------
# 6) Saf yardimcilar
# ---------------------------------------------------------------------------

def test_pages_for_modes_and_roles():
    import nav_view

    career = nav_view.pages_for(tournament=False, shared=False, internationals=False, role=None)
    assert career == list(nav_view.CAREER_PAGES)
    tournament = nav_view.pages_for(tournament=True, shared=False, internationals=False, role=None)
    assert tournament == list(nav_view.TOURNAMENT_PAGES) and nav_view.TRANSFER not in tournament
    member = nav_view.pages_for(tournament=False, shared=True, internationals=True, role="MEMBER")
    assert nav_view.INBOX in member and nav_view.NATIONAL in member and nav_view.ADMIN not in member
    owner = nav_view.pages_for(tournament=False, shared=True, internationals=False, role="OWNER")
    assert owner[-1] == nav_view.ADMIN
    for slug in nav_view.PAGES:                                         # her sayfanin bir cizicisi var
        import web_app

        assert slug in web_app.PAGE_RENDERERS or slug in (nav_view.HOME, nav_view.MATCH)


def test_resolve_and_labels():
    import nav_view

    assert nav_view.resolve("kadro") == nav_view.resolve("Kadro") == nav_view.resolve("📋 Kadro") == "kadro"
    assert nav_view.resolve("LIG") == "puan-durumu" and nav_view.resolve(None) is None
    assert nav_view.resolve("<script>") is None
    assert nav_view.label("transfer", {"transfer": 3}) == "🔄 Transfer Merkezi (3)"
    assert nav_view.label("transfer", {"transfer": 0}) == "🔄 Transfer Merkezi"


def test_history_is_bounded_and_callbacks_need_a_session(monkeypatch):
    import nav_view

    state: dict = {}
    monkeypatch.setattr(nav_view.st, "session_state", state)
    nav_view.cb_nav("kadro")                                           # oturum yok: hicbir sey yazmaz
    assert nav_view.NAV_KEY not in state
    state["auth"] = object()
    for n in range(nav_view.HISTORY_MAX + 10):
        nav_view.cb_nav("kadro" if n % 2 else "taktik")
    assert len(state[nav_view.HISTORY_KEY]) == nav_view.HISTORY_MAX
    assert state[nav_view.HISTORY_POS_KEY] == nav_view.HISTORY_MAX - 1
    nav_view.cb_nav("yok-boyle")                                       # bilinmeyen slug yok sayilir
    assert state[nav_view.NAV_KEY] == "kadro"
    for fn in (nav_view.cb_nav, nav_view.cb_nav_top, nav_view.cb_nav_step):
        assert fn.requires_auth is True


def test_headers_escape_names():
    import nav_view

    html = nav_view.club_header_html("<b>X</b>", "<i>lig</i>", [("<u>", "<s>")])
    assert "<b>X</b>" not in html and "&lt;b&gt;X&lt;/b&gt;" in html and "<i>" not in html and "<u>" not in html


def test_week_report_desk_lines_are_escaped(monkeypatch):
    """Transfer masasi notlari veritabani metni tasir (oyuncu / kulup adi): Markdown / HTML kacisli yazilir."""
    from types import SimpleNamespace

    import career_views as cv
    import web_app

    report = SimpleNamespace(played_any=True, season=1, week=3, results=[], injuries=[], suspensions=[],
                             transfers=[], lineup_notes=[], finance_note=None, manager_reputation=None,
                             season_finished=False, transfer_notes=["*Kötü* <b>[adam](http://x)</b> teklif yaptı"])
    desk = [(kind, text) for kind, text in cv.week_report_lines(report) if kind == "desk"]
    assert desk == [("desk", "🔄 *Kötü* <b>[adam](http://x)</b> teklif yaptı")]

    shown: list[str] = []

    class Expander:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(web_app.st, "expander", lambda *a, **k: Expander())
    monkeypatch.setattr(web_app.st, "markdown", lambda text, **k: shown.append(text))
    web_app.week_report_block(desk, "Rapor")
    assert len(shown) == 1 and "<b>" not in shown[0] and "&lt;b&gt;" in shown[0]
    assert "*Kötü*" not in shown[0] and "[adam](http" not in shown[0]
