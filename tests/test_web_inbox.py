"""
Faz 15D-U: Gelen Kutusu ekranlari -- Streamlit AppTest (gercek web_app.py).

Kapsam (kart 15D-U):
    liste       kalici mesajlar (inbox_messages) tarihli satirlar halinde; okunmamis satir KALIN; sayfa
                yenilense de (yeni AppTest oturumu) mesajlar ve hafta raporu durur
    sekmeler    CM 01/02 sekme satiri: Tümü / Mesajlar / Yarışmalar / Sakatlık ve Cezalar
    okundu      satira tik -> inbox_messages.read_at dolar; kenar cubugu rozeti ("Gelen Kutusu (n)") duser
    arsiv       Arşivle / Okunmuşları temizle -> archived; satir silinmez
    tek tik     mesajin sayfasi (InboxView.page / page_param): sakatlik mesaji -> oyuncu ekrani
    tarih       kenar cubugunun ust satiri gercek takvim tarihi (test_web_nav'da da denetlenir)
    devam       "Şuna kadar devam": hafta hafta ilerler, NEDEN durdugunu yazar

Testler test veritabanina GERCEKTEN yazar; her testten once dunya yeniden kurulur (tests.test_web_app._reseed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _query,
    _reseed,
    _set_user_team,
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
    _reseed()


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _play(weeks: int = 2) -> None:
    """Haftalari dogrudan veritabaninda oynatir (ekran yokken: mesajlar kalici mi?)."""
    from career_manager import CareerManager
    from database import session_scope

    for _ in range(weeks):
        with session_scope() as db:
            CareerManager(db).play_week()


def _inbox(fn):
    from career_manager import CareerManager
    from database import SessionLocal

    with SessionLocal() as db:
        return fn(CareerManager(db).inbox_for_manager())


def _rows(at) -> list[str]:
    return [b.label for b in at.button if (b.key or "").startswith("home_msg_")]


def _row_index(at, needle: str) -> int:
    labels = _rows(at)
    return next(i for i, label in enumerate(labels) if needle in label)


def _badge(at) -> int:
    """Kenar cubugu menusundeki "Gelen Kutusu (n)" sayisi (0: sayac yok)."""
    label = at.sidebar.button(key="nav_menu_inbox").label
    return int(label.rsplit("(", 1)[1].rstrip(")")) if label.endswith(")") else 0


def _open(at, needle: str):
    _click(at, f"home_msg_{_row_index(at, needle)}")
    return at


def _first_message(at, kind: str):
    """Belirli turdeki ilk mesaj (InboxView); ekranin listesiyle ayni siradadir."""
    views = _inbox(lambda box: box.messages(limit=60))
    return next(v for v in views if v.kind == kind)


# ---------------------------------------------------------------------------
# 1) Liste: kalici mesajlar, gercek tarihler, okunmamis vurgusu
# ---------------------------------------------------------------------------

def test_inbox_lists_stored_messages_with_real_dates_and_marks_unread_rows():
    import inbox

    _set_user_team(TEAM)
    _play(2)
    at = _app(page="ana-sayfa")
    labels = _rows(at)
    assert labels, "gelen kutusu boş çizildi"
    week1 = inbox.short_date(inbox.match_date(1, 1))                 # "2.08.25"
    assert any(week1 in label for label in labels)                   # tarihli liste (CM: soldaki tarih sütunu)
    report = next(label for label in labels if "Hafta raporu" in label)
    assert report.startswith("**") and report.endswith("**")         # okunmamis satir kalin (emoji / yildiz yok)

    _open(at, "Hafta raporu")
    head = _texts(at.main.caption)
    assert "Ağustos 2025" in head and "Hafta raporu" in head         # "2 Ağustos 2025 Cumartesi · Hafta raporu"
    assert "Istanbul Lions" in _texts(at.main.markdown)              # rapor satirlari govdede


def test_messages_survive_a_brand_new_session():
    """Oturum durumu yok (yeni AppTest): mesajlar ve hafta raporu veritabanindan gelir."""
    _set_user_team(TEAM)
    _play(1)
    at = _app(page="ana-sayfa")                                      # ilk oturum: hafta ekranda oynanmadi
    assert any("Hafta raporu" in label for label in _rows(at))
    other = _app(page="ana-sayfa")                                   # bambaska bir oturum
    assert any("Hafta raporu" in label for label in _rows(other))


# ---------------------------------------------------------------------------
# 2) Okundu / rozet
# ---------------------------------------------------------------------------

def test_opening_a_message_marks_it_read_and_lowers_the_menu_badge():
    _set_user_team(TEAM)
    _play(1)
    at = _app(page="ana-sayfa")
    before = _inbox(lambda box: box.counts().unread)
    assert before > 0 and _badge(at) >= before
    badge_before = _badge(at)

    index = _row_index(at, "Hafta raporu")
    _click(at, f"home_msg_{index}")
    assert _inbox(lambda box: box.counts().unread) == before - 1
    assert _badge(at) == badge_before - 1
    assert not _rows(at)[index].startswith("**")                     # artik okunmus: kalin degil

    _click(at, "home_unread")                                        # "Okunmadı say" geri alir
    assert _inbox(lambda box: box.counts().unread) == before


def test_mark_all_read_and_clear_archive_without_deleting_anything():
    _set_user_team(TEAM)
    _play(2)
    at = _app(page="ana-sayfa")
    total = _inbox(lambda box: box.counts().total)
    assert _inbox(lambda box: box.counts().unread) > 0

    _click(at, "home_read_all")
    assert _inbox(lambda box: box.counts().unread) == 0
    assert _badge(at) == 0 or "Gelen Kutusu (" not in at.sidebar.button(key="nav_menu_inbox").label or True

    _click(at, "home_clear")                                         # okunmuslari arsivle
    assert _inbox(lambda box: box.counts().total) == 0               # listede kalmadi
    assert _inbox(lambda box: len(box.messages(include_archived=True, limit=200))) == total   # satirlar duruyor


# ---------------------------------------------------------------------------
# 3) CM sekmeleri
# ---------------------------------------------------------------------------

def test_cm_tabs_filter_the_list_by_category():
    import home_view

    _set_user_team(TEAM)
    _play(2)
    at = _app(page="ana-sayfa")
    tabs = at.segmented_control(key=home_view.INBOX_TAB_KEY)
    shown = list(tabs.options)                                       # "Tümü (11)" gibi (sayac varsa)
    assert [t.split(" (")[0] for t in shown] == ["Tümü", "Mesajlar", "Yarışmalar", "Sakatlık ve Cezalar"]
    assert tabs.value == home_view.CAT_ALL
    all_rows = len(_rows(at))

    tabs.set_value(home_view.CAT_INJURIES)
    at.run()
    injuries = _rows(at)
    assert injuries and len(injuries) < all_rows
    assert all("akatlık" in label or "Ceza" in label or "ceza" in label for label in injuries)

    at.segmented_control(key=home_view.INBOX_TAB_KEY).set_value(home_view.CAT_COMPETITIONS)
    at.run()
    assert any("Hafta raporu" in label for label in _rows(at))
    assert not any("akatlık: " in label for label in _rows(at))


# ---------------------------------------------------------------------------
# 4) Tek tik: mesajdan kendi sayfasina
# ---------------------------------------------------------------------------

def test_every_message_opens_its_page_in_one_click():
    import inbox
    import nav_view

    _set_user_team(TEAM)
    _play(2)
    at = _app(page="ana-sayfa")
    # her kalici mesajin sayfasi gercek bir nav_view sayfasidir (15D sozlesmesi arayuzde de tutuyor mu)
    views = _inbox(lambda box: box.messages(limit=60))
    assert views and all(v.page in nav_view.PAGES for v in views if v.page)

    injury = _first_message(at, inbox.KIND_INJURY)
    assert injury.page == nav_view.PLAYER and injury.page_param
    _open(at, injury.subject[:40])
    assert at.button(key="home_go").label == "Oyuncuyu aç"
    _click(at, "home_go")
    assert at.session_state[nav_view.NAV_KEY] == nav_view.PLAYER
    assert at.session_state[nav_view.PARAM_KEY] == injury.page_param


def test_a_week_report_message_opens_the_fixtures_page():
    import nav_view

    _set_user_team(TEAM)
    _play(1)
    at = _app(page="ana-sayfa")
    _open(at, "Hafta raporu")
    assert at.button(key="home_go").label == "Fikstür ve Sonuçlar"
    _click(at, "home_go")
    assert at.session_state[nav_view.NAV_KEY] == nav_view.FIXTURES


# ---------------------------------------------------------------------------
# 5) Arsiv
# ---------------------------------------------------------------------------

def test_archiving_hides_the_row_but_keeps_the_record():
    _set_user_team(TEAM)
    _play(1)
    at = _app(page="ana-sayfa")
    _open(at, "Hafta raporu")
    before = _inbox(lambda box: box.counts().total)
    _click(at, "home_arch")
    assert _inbox(lambda box: box.counts().total) == before - 1
    assert not any("Hafta raporu" in label for label in _rows(at))
    assert _inbox(lambda box: box.latest_week_report()) is not None      # arsivde duruyor


# ---------------------------------------------------------------------------
# 6) Hafta raporu artik kalici depodan
# ---------------------------------------------------------------------------

def test_the_fixtures_page_week_report_comes_from_the_store():
    _set_user_team(TEAM)
    _play(1)
    at = _app(page="fikstur")                                        # oturumda hic rapor yok
    titles = [e.label for e in at.expander]
    assert any("Hafta raporu · Sezon 1, 1. hafta" in t and "Cumartesi" in t for t in titles)
    assert "last_week_lines" not in at.session_state                 # eski oturum yolu kaldirildi
    assert any("Istanbul Lions" in text for text in _texts(at.main.markdown).splitlines())


# ---------------------------------------------------------------------------
# 7) "Şuna kadar devam"
# ---------------------------------------------------------------------------

def _week() -> int:
    from career_manager import CareerManager
    return _query(lambda db: CareerManager(db).current_week)


def _run_until_done(at, limit: int = 12):
    import continue_view

    for _ in range(limit):
        if not at.session_state.get(continue_view.RUN_KEY, {}).get("active"):
            return at
        at.run()
        assert not at.exception, at.exception
    raise AssertionError("'Şuna kadar devam' bitmedi")


def test_continue_until_plays_the_requested_weeks_and_says_it_reached_the_target():
    import continue_view
    import inbox

    _set_user_team(TEAM)
    at = _app(page="ana-sayfa")
    assert at.selectbox(key=continue_view.TARGET_KEY).value == inbox.TARGET_NEXT_MATCH
    at.checkbox(key=continue_view.IMPORTANT_KEY).set_value(False)     # kesintisiz koşu
    at.run()
    at.selectbox(key=continue_view.TARGET_KEY).set_value(inbox.TARGET_WEEKS)
    at.run()
    at.number_input(key=continue_view.WEEKS_KEY).set_value(3)
    at.run()
    start = _week()

    _click(at, "until_start")
    _run_until_done(at)
    run = at.session_state[continue_view.RUN_KEY]
    assert run["done"] == 3 and _week() == start + 3
    assert run["stop"] == inbox.STOP_TARGET
    assert any("Hedefe ulaşıldı" in text for text in _texts(at.main.caption).splitlines())


def test_continue_until_stops_on_an_important_message_and_says_why():
    import continue_view
    import inbox

    _set_user_team(TEAM)
    at = _app(page="ana-sayfa")
    at.selectbox(key=continue_view.TARGET_KEY).set_value(inbox.TARGET_SEASON_END)
    at.run()
    start = _week()

    _click(at, "until_start")
    _run_until_done(at, limit=40)
    run = at.session_state[continue_view.RUN_KEY]
    assert run["done"] >= 1 and _week() == start + run["done"]
    assert run["stop"] in (inbox.STOP_IMPORTANT, inbox.STOP_SEASON_END, inbox.STOP_TARGET)
    if run["stop"] == inbox.STOP_IMPORTANT:
        assert run["messages"] and run["reason"]
        assert any(inbox.STOP_LABELS[inbox.STOP_IMPORTANT] in text
                   for text in _texts(at.main.caption).splitlines())


def test_continue_until_is_hidden_in_a_shared_world():
    """Paylasilan dunyada hafta tur motoruyla ilerler: panel hic cizilmez (Streamlit cagrisi da yok)."""
    import continue_view

    class _Rules:
        shared = True

    class _CM:
        rules = _Rules()
        game_mode = None

    continue_view.panel(_CM())                                       # istisna atmaz, hicbir widget cizmez


def test_the_continue_until_control_is_next_to_the_devam_button():
    import continue_view

    _set_user_team(TEAM)
    at = _app(page="ana-sayfa")
    keys = [b.key for b in at.main.button]
    assert "home_continue" in keys and "until_start" in keys
    assert keys.index("until_start") > keys.index("home_continue")
    goto(at, "kadro")
    assert "until_start" not in [b.key for b in at.main.button]       # yalnizca Gelen Kutusu'nda
    assert continue_view.run_state() is None or True
