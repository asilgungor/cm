"""
Faz 13I / 14S: CM 01/02 iskeleti ve sayfa yonlendirme -- Streamlit AppTest (gercek web_app.py) + saf yardimcilar.

Kilitlenenler (14S, sahip karari 3): kenar cubugunda CM kisa menusu (Devam, [Kulup adi], Menajer, Yarismalar,
Ulkeler ve Kulupler, Bul, Gelen Kutusu (n), Oyun Secenekleri; nav_menu_{bolum}), kulubun / yarismalarin bolumleri
ekranin SEKME satirinda (nav_to_{slug}), yalnizca secili sayfanin cizilmesi, dev sayfa basligi yerine kulup renginde
bant, secimin URL'de (?sayfa=) tutulmasi ve yenilemede geri gelmesi, eski ad / 13I etiketi / gecersiz sayfa, telefon ust
menusu (nav_top), ziyaret gecmisinde geri / ileri (nav_back / nav_fwd, sayfa alti nav_foot_*), alt eylem dugmeleri
(nav_act_{hedef}), kenar cubugu tarihi + Devam, Gelen Kutusu sayaci, hafta raporundaki masa satirlarinin kacisi.

Kendi veritabaninda calistirin:
    TEST_DB_NAME=fm_db_test_14s python -m pytest -q -p no:cacheprovider tests/test_web_nav.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import all_pages, goto, menu, page, sections  # noqa: E402
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
CM_MENU = ["club", "manager", "comps", "nations", "find", "inbox", "options"]


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


def _df(at, key: str):
    return next((d for d in at.dataframe if d.key == key), None)


def _menu_labels(at) -> list[str]:
    return [b.label for b in at.sidebar.button if (b.key or "").startswith("nav_menu_")]


# ---------------------------------------------------------------------------
# 1) CM kisa menusu, sekme satiri, yalnizca secili sayfa
# ---------------------------------------------------------------------------

def test_cm_short_menu_and_tabs_and_only_the_selected_page_renders():
    import nav_view

    _set_user_team(TEAM)
    at = _app()
    assert page(at) == nav_view.HOME and _url_page(at) == nav_view.HOME        # varsayilan: Gelen Kutusu (CM haberleri)
    assert sections(at) == CM_MENU
    assert _menu_labels(at) == [TEAM, "Menajer", "Yarışmalar", "Ülkeler ve Kulüpler", "Bul", "Gelen Kutusu",
                                "Oyun Seçenekleri"]
    assert at.button(key="nav_continue").label == "Devam"                        # menunun ilk ogesi
    assert at.button(key="nav_menu_inbox").proto.type == "primary"
    assert all(at.button(key=f"nav_menu_{s}").proto.type == "secondary" for s in CM_MENU if s != "inbox")
    pages = menu(at)
    assert pages[0] == nav_view.HOME and set(nav_view.CAREER_PAGES) <= set(pages)
    assert nav_view.ADMIN not in pages and nav_view.INBOX not in pages          # tek kisilik kariyer
    assert not at.tabs and not at.title                                         # ust sekme ve dev baslik yok
    import re

    labels = " ".join(_menu_labels(at)) + " " + " ".join(b.label for b in at.button if (b.key or "").startswith("nav_"))
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", labels)       # menude / sekmelerde emoji yok

    # Gelen Kutusu: yalnizca onun widget'lari; kadro / transfer / taktik cizilmedi
    keys = _keys(at)
    assert {"home_prep", "home_live"} <= keys and "tac_auto" not in keys
    assert not [t for t in at.text_input if t.key == "mkt_name"]
    assert "Gelen Kutusu" in _texts(at.main.markdown) and 'class="ofm-band"' in _texts(at.main.markdown)

    _click(at, "nav_menu_club")                                                 # kulup -> Kadro sekmesi
    assert page(at) == nav_view.SQUAD
    keys = _keys(at)
    assert "tac_auto" in keys and "home_prep" not in keys and "tc_scout" not in keys
    assert at.button(key="nav_menu_club").proto.type == "primary"
    tabs = [b for b in at.button if (b.key or "").startswith("nav_to_")]
    assert [b.label for b in tabs] == ["Kadro", "Taktik", "Maçlar", "Canlı Maç", "Transfer", "Akademi",
                                       "Teknik Heyet", "Finans"]
    assert at.button(key="nav_to_kadro").proto.type == "primary"
    assert f'<span class="t">{TEAM}</span>' in _texts(at.main.markdown)          # bant: kulubun adi

    goto(at, "transfer")                                                        # sekmeyle
    assert at.text_input(key="mkt_name") and "tac_auto" not in _keys(at)
    assert at.button(key="nav_to_transfer").proto.type == "primary"


def test_every_page_renders_without_errors():
    """Her gorunen sayfa hatasiz cizilir (goto istisnayi da denetler) ve bant cizilir."""
    import nav_view

    _set_user_team(TEAM)
    at = _app()
    assert set(all_pages(at)) == set(menu(at)) | set(nav_view.SHELL_PAGES)
    for slug in all_pages(at):
        goto(at, slug)
        assert 'class="ofm-band"' in _texts(at.main.markdown) or slug == nav_view.MATCH, slug


def test_fixtures_belong_to_two_sections():
    """Fikstur: Kulup > Maclar ve Yarismalar > Fikstur ve Sonuclar; hangi bolumden gelindiyse o bolumun sekmeleri."""
    _set_user_team(TEAM)
    at = _app()
    _click(at, "nav_menu_comps")
    assert page(at) == "puan-durumu"
    _click(at, "nav_to_fikstur")
    assert page(at) == "fikstur" and at.button(key="nav_menu_comps").proto.type == "primary"
    assert at.button(key="nav_to_puan-durumu") and not [b for b in at.button if b.key == "nav_to_kadro"]
    _click(at, "nav_menu_club")
    _click(at, "nav_to_fikstur")
    assert at.button(key="nav_menu_club").proto.type == "primary" and at.button(key="nav_to_kadro")
    assert at.button(key="nav_to_fikstur").label == "Maçlar"


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
    ("lig", "puan-durumu"), ("finans", "kulup"), ("Kadro", "kadro"), ("📋 Kadro", "kadro"), ("Kulüp & Finans", "kulup"),
    ("bul", "bul"), ("ulkeler", "ulkeler"), ("menajer", "menajer"), ("secenekler", "secenekler"),
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
    assert top.value == nav_view.HOME and list(top.options) == [nav_view.label(s) for s in all_pages(at)]
    assert at.button(key="top_continue") and at.button(key="top_back") and at.button(key="top_fwd")
    top.set_value(nav_view.TACTICS)
    at.run()
    assert not at.exception, at.exception
    assert page(at) == nav_view.TACTICS and _url_page(at) == nav_view.TACTICS
    goto(at, "kadro")                                                  # sekme -> ust secici esitlenir
    assert at.selectbox(key="nav_top").value == nav_view.SQUAD
    # Masaustunde gizli, telefonda (<= 768 px) gorunur
    css = nav_view.NAV_CSS
    assert ".st-key-ofm_topnav{display:none !important}" in css
    assert "@media (max-width:768px)" in css.split(".st-key-ofm_topnav{display:none !important}")[1]


# ---------------------------------------------------------------------------
# 4) Geri / ileri gecmisi, alt eylemler
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
    _click(at, "nav_foot_back")                                        # sayfa altindaki Geri ayni gecmis
    assert page(at) == nav_view.HOME and at.button(key="nav_back").disabled
    assert at.button(key="nav_foot_back").label == "Geri" and at.button(key="nav_foot_fwd").label == "İleri"
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
    assert "Sezon 1<br>1. hafta" in _texts(at.sidebar.markdown)
    assert at.button(key="nav_continue").proto.type == "primary" and at.button(key="nav_continue").label == "Devam"
    _click(at, "nav_continue")
    assert _query(lambda db: __import__("career_manager").CareerManager(db).current_week) == 2
    assert page(at) == nav_view.TABLE                                  # Devam sayfayi degistirmez
    assert "Sezon 1<br>2. hafta" in _texts(at.sidebar.markdown)
    goto(at, "ana-sayfa")
    assert "2. haftayı oyna" in at.button(key="home_continue").label   # Gelen Kutusu'ndaki Devam da ayni eylem
    assert any("S1 H1" in b.label for b in at.button if (b.key or "").startswith("home_msg_"))   # hafta raporu


def test_inbox_and_tab_counts_show_pending_wage_demands():
    _set_user_team(TEAM)
    at = _app(page="kadro")
    assert at.button(key="nav_to_kadro").label == "Kadro" and at.button(key="nav_menu_inbox").label == "Gelen Kutusu"
    from database import session_scope

    with session_scope() as db:
        players = _team(db, TEAM).players[:2]
        for p in players:
            p.wage_demand = 90_000
    at.run()
    assert at.button(key="nav_to_kadro").label == "Kadro (2)"
    assert at.button(key="nav_menu_inbox").label == "Gelen Kutusu (2)"


def test_new_screens_render_and_open_profiles():
    """Menajer, Ulkeler ve Kulupler (ulke -> lig -> kulup -> kadro -> profil), Bul (oyuncu / kulup), Oyun Secenekleri."""
    import club_view
    import find_view
    import player_view as pv

    _set_user_team(TEAM)
    at = _app(page="menajer")
    assert "Menajer tanınırlığı" in _texts(at.main.markdown)
    goto(at, "secenekler")
    assert at.radio(key="theme_choice").value == "OFM Klasik"          # 14S: varsayilan tema
    goto(at, "ulkeler")
    assert at.button_group(key=club_view.COUNTRY_KEY).value == "Türkiye"   # kendi kulubunun ulkesi
    rival = _query(lambda db: _team(db, "Madrid Blancos").id)
    at.session_state[club_view.CLUB_KEY] = rival
    at.run()
    assert "Madrid Blancos" in _texts(at.main.markdown) and _df(at, club_view.SQUAD_KEY) is not None
    first = at.session_state[pv.table_ids_key(club_view.SQUAD_KEY)][0]
    at.session_state[pv.PROFILE_KEY] = (pv.AREA_CLUBS, first)          # satira tik ile ayni oturum durumu
    at.run()
    assert not at.exception and "pv-sheet" in _texts(at.main.markdown) and "gözlemci raporu" in _texts(at.main.markdown)
    _click(at, "pv_close")
    assert at.button(key="nc_back")

    goto(at, "bul")
    name = _query(lambda db: _team(db, "Madrid Blancos").players[0].name)
    at.text_input(key=find_view.QUERY_KEY).set_value(name)
    at.run()
    assert name in list(_df(at, find_view.PLAYERS_KEY).value["Oyuncu"])
    at.text_input(key=find_view.QUERY_KEY).set_value("lions")
    at.run()
    at.button_group(key=find_view.TAB_KEY).set_value(find_view.TAB_CLUB)
    at.run()
    frame = _df(at, find_view.CLUBS_KEY).value
    assert "Istanbul Lions" in list(frame["Kulüp"])


# ---------------------------------------------------------------------------
# 6) Saf yardimcilar
# ---------------------------------------------------------------------------

def test_pages_for_modes_and_roles():
    import nav_view

    career = nav_view.pages_for(tournament=False, shared=False, internationals=False, role=None)
    assert career == list(nav_view.CAREER_PAGES) and not set(nav_view.SHELL_PAGES) & set(career)
    assert nav_view.with_shell(career) == [*career, *nav_view.SHELL_PAGES]      # CM kabuk ekranlari her modda
    tournament = nav_view.pages_for(tournament=True, shared=False, internationals=False, role=None)
    assert tournament == list(nav_view.TOURNAMENT_PAGES) and nav_view.TRANSFER not in tournament
    member = nav_view.pages_for(tournament=False, shared=True, internationals=True, role="MEMBER")
    assert nav_view.INBOX in member and nav_view.NATIONAL in member and nav_view.ADMIN not in member
    owner = nav_view.pages_for(tournament=False, shared=True, internationals=False, role="OWNER")
    assert owner[-1] == nav_view.ADMIN
    import web_app

    for slug in nav_view.PAGES:                                         # her sayfanin bir cizicisi var
        assert slug in web_app.PAGE_RENDERERS or slug in (nav_view.HOME, nav_view.MATCH)
    for section in nav_view.SECTIONS:                                   # her sayfa en az bir bolumde
        assert all(slug in nav_view.PAGES for slug, _tab in section.pages)
    assert {s for sec in nav_view.SECTIONS for s, _t in sec.pages} == set(nav_view.PAGES)


def test_resolve_and_labels():
    import nav_view

    assert nav_view.resolve("kadro") == nav_view.resolve("Kadro") == nav_view.resolve("📋 Kadro") == "kadro"
    assert nav_view.resolve("LIG") == "puan-durumu" and nav_view.resolve(None) is None
    assert nav_view.resolve("Maçlar") == "fikstur" and nav_view.resolve("🏛️ Kulüp & Finans") == "kulup"
    assert nav_view.resolve("<script>") is None
    assert nav_view.label("transfer", {"transfer": 3}) == "Transfer Merkezi (3)"
    assert nav_view.label("transfer", {"transfer": 0}) == "Transfer Merkezi"
    assert nav_view.inbox_count({"transfer": 2, "kadro": 1, "mesajlar": 3, "puan-durumu": 9}) == 6


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
    state[nav_view.PROFILE_STATE_KEY] = ("squad", 1)
    nav_view.cb_menu("comps", "puan-durumu")                           # ekran degisince acik profil kapanir
    assert nav_view.PROFILE_STATE_KEY not in state and state[nav_view.SECTION_KEY] == "comps"
    for fn in (nav_view.cb_nav, nav_view.cb_menu, nav_view.cb_nav_top, nav_view.cb_nav_step):
        assert fn.requires_auth is True


def test_headers_bands_and_tables_escape_names():
    import nav_view

    html = nav_view.club_header_html("<b>X</b>", "<i>lig</i>", [("<u>", "<s>")])
    assert "<b>X</b>" not in html and "&lt;b&gt;X&lt;/b&gt;" in html and "<i>" not in html and "<u>" not in html
    band = nav_view.band_html("<script>x</script>", ("#ff0000", "red;background:url(x)"))
    assert "<script>" not in band and "url(" not in band and "--band-bg:#0a2a8a" in band     # gecersiz renk: varsayilan
    table = nav_view.table_html(["<b>", "x"], [["<i>", 1]], left=(0,), highlight={0}, avr=1)
    assert "<i>" not in table and "&lt;i&gt;" in table and 'class="me"' in table and 'class=" avr"' in table
    assert "<u>" not in nav_view.pairs_html([("<u>", "<s>")])


def test_club_band_colors_fall_back_to_the_crc32_palette():
    import match_day_view as md
    import nav_view
    from ofm_theme import AA_LARGE, contrast_ratio

    bg, fg = nav_view.club_band_colors("Istanbul Lions")                  # sentetik ad: acik veride yok
    assert bg in md.CLUB_COLORS and contrast_ratio(fg, bg) >= AA_LARGE
    assert nav_view.club_band_colors("Istanbul Lions") == (bg, fg)       # deterministik
    assert nav_view.club_band_colors(None) == nav_view.BAND_DEFAULT


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
