"""
nav_view.py
===========
Faz 13I / 14S: Championship Manager 01/02 iskeleti ve sayfa yonlendirme. SAF SUNUM + oturum durumu: veritabanina
yazmaz, oyun kurallarini bilmez (sayfa cizicileri web_app'tadir). Her cizimde YALNIZCA secili sayfa cizilir.

CM KISA MENUSU (14S, sahip karari 3) -- sol kenar cubugu, lacivert degrade:
    tarih (sezon / hafta) + ◄ ► (ziyaret gecmisi)
    Devam                      (web_app.continue_buttons: haftayi oynat / canli maca don / yeni sezon)
    [Kulup adi]                Kadro · Taktik · Maclar · Canli Mac · Transfer · Akademi · Teknik Heyet · Finans
    Menajer                    Profil · Haberler ve Tarih
    Yarismalar                 Puan Durumu · Fikstur ve Sonuclar · Devler Arenasi · Milli Takim
    Ulkeler ve Kulupler        ulke -> lig -> kulup -> kadro -> profil (club_view)
    Bul                        ad aramasi: oyuncu / kulup (find_view)
    Gelen Kutusu (n)           haber ekrani (home_view); paylasilan dunyada Teklifler ve Mesajlar alt eylemle
    Oyun Secenekleri           tema, hesap; paylasilan dunyada Dunya Yonetimi sekmesi
Kulubun ve yarismalarin bolumleri ekranin icinde SEKME satiridir (tab_row, nav_to_{slug}). Bir sayfa birden cok
bolumde olabilir (Fikstur: Kulup > Maclar ve Yarismalar > Fikstur ve Sonuclar); hangi bolumden gelindiyse o bolumun
sekmeleri gorunur (SECTION_KEY), yoksa sayfanin ilk bolumu.

    PAGES / SECTIONS / pages_for(...)   sayfa ve bolum kaydi; oyun moduna / dunyaya gore gorunen sayfalar
    current_page(pages)          secili sayfa: oturum -> URL (?sayfa=) -> Gelen Kutusu; URL guncel tutulur
    menu(pages, current, ...)    kenar cubugu CM menusu (nav_menu_{bolum}); secili bolum birincil dugme
    tab_row(pages, current, ...) ekranin sekme satiri (nav_to_{slug}); secili sekme birincil dugme
    band_html(baslik, renkler)   kulup renginde tam genislik baslik bandi (club_band_colors: club_colors.colors_for,
                                 yoksa match_day_view crc32 paleti)
    top_nav(...)                 telefon (<= 768 px): ◄ ► + menu secici + Devam (masaustunde CSS ile gizli)
    date_bar(lines)              kenar cubugu tepesi: tarih satirlari + ◄ ►
    page_footer(slug, actions)   alt eylem dugmeleri (nav_act_{hedef}) + kabartmali Geri / Ileri (nav_foot_*)
    goto / cb_nav / cb_menu / cb_nav_top / cb_nav_step   sayfa degistirme ve ziyaret gecmisi (yalnizca oturum durumu)

Eski ?sayfa= adlari ve 13I etiketleri ("📋 Kadro", "Kulüp & Finans") takma ad olarak calisir (resolve).
Widget durumu: secili sayfa widget OLMAYAN nav_page anahtarinda tutulur (sayfalar arasi gecis secimi kaybetmez).
"""

from __future__ import annotations

import functools
import re
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st

from web_common import requires_auth

NAV_KEY = "nav_page"
SECTION_KEY = "nav_section"                 # hangi menu bolumunden gelindi (Fikstur iki bolumde)
PAGES_KEY = "nav_pages"                     # bu cizimde gorunen sayfalar (testler: nav_helpers.menu)
TOP_KEY = "nav_top"
QUERY_KEY = "sayfa"
BUTTON_PREFIX = "nav_to_"                   # sekme dugmeleri (ekranin sekme satiri)
MENU_PREFIX = "nav_menu_"                   # kenar cubugu menu dugmeleri (bolumler)
SCROLL_TOP_KEY = "scroll_top"               # club_picker_view.SCROLL_TOP_KEY ile ayni (web_app.main tuketir)
MENU_KEY = "ofm_nav"                        # kenar cubugu menu kabi (CSS: .st-key-ofm_nav)
TABS_KEY = "ofm_tabs"
TOP_CONTAINER_KEY = "ofm_topnav"
HISTORY_KEY, HISTORY_POS_KEY, HISTORY_MAX = "nav_hist", "nav_hist_pos", 30
DATEBAR_KEY, FOOTER_KEY = "ofm_datebar", "ofm_footer"
PROFILE_STATE_KEY = "pv_open"               # player_view.PROFILE_KEY: ekran degisince acik profil kapanir (CM)

HOME, INBOX, NEWS = "ana-sayfa", "mesajlar", "haberler"
SQUAD, TACTICS, MATCH, ACADEMY, STAFF = "kadro", "taktik", "canli-mac", "akademi", "teknik-heyet"
FIXTURES, TABLE, ARENA, NATIONAL = "fikstur", "puan-durumu", "devler-arenasi", "milli-takim"
TRANSFER, CLUB, ADMIN = "transfer", "kulup", "dunya-yonetimi"
BOARD = "yonetim"                                              # 15C-U: yonetim kurulu (kural acikken)
MANAGER, NATIONS, FIND, OPTIONS = "menajer", "ulkeler", "bul", "secenekler"
PLAYER, CLUB_PAGE, NATION = "oyuncu", "takim", "ulke"          # 14F: parametreli, menude gorunmeyen sayfalar

SEC_CLUB, SEC_MANAGER, SEC_COMPS = "club", "manager", "comps"
SEC_NATIONS, SEC_FIND, SEC_INBOX, SEC_OPTIONS = "nations", "find", "inbox", "options"
SEC_WORLD = "world"                                            # parametreli sayfalar (menude dugmesi yok)

PARAM_KEY = "nav_param"                     # 14F: parametreli sayfanin parametresi (oyuncu / kulup id, lig id, ulke adi)
# Parametreli sayfanin URL anahtari: ?sayfa=oyuncu&id=123, ?sayfa=takim&id=45, ?sayfa=puan-durumu&lig=7, ?sayfa=ulke&ad=..
PARAM_QUERY: dict[str, str] = {PLAYER: "id", CLUB_PAGE: "id", NATION: "ad", TABLE: "lig"}
INT_PARAM_PAGES = frozenset({PLAYER, CLUB_PAGE, TABLE})
HISTORY_SEP = "|"


@dataclass(frozen=True)
class Page:
    slug: str
    label: str
    group: str                                  # birincil menu bolumu (SEC_*)
    hint: str = ""
    menu: bool = True                           # 14F: menude / telefon seciciside gorunur mu (parametreli sayfalar hayir)


@dataclass(frozen=True)
class Section:
    key: str
    label: str                                  # menu etiketi (kulup bolumunde kulubun adi yazilir)
    pages: tuple[tuple[str, str], ...]          # (slug, sekme etiketi) -- sekme satiri sirasiyla
    tabs: bool = True                           # sekme satiri cizilsin mi (Gelen Kutusu: CM'deki gibi alt eylemler)
    hidden: bool = False                        # menude dugmesi yok (parametreli sayfalar)


# Sira onemli: pages_for bu sirayla dondurur (paylasilan dunya testleri: ana-sayfa, mesajlar, kariyer sayfalari,
# dunya-yonetimi).
_PAGE_LIST = (
    Page(HOME, "Gelen Kutusu", SEC_INBOX, "Haberler, mesajlar, sıradaki maç ve devam"),
    Page(INBOX, "Teklifler ve Mesajlar", SEC_INBOX, "Menajerler arası teklifler, mesajlar, bildirimler"),
    Page(NEWS, "Haberler ve Tarih", SEC_MANAGER, "Haber akışı, onur listesi, transfer kayıtları"),
    Page(SQUAD, "Kadro", SEC_CLUB, "Kadro listesi, taktik tahtası, oyuncu memnuniyeti"),
    Page(TACTICS, "Taktik", SEC_CLUB, "Maç önü, talimatlar, maç planı, hazırlık maçı"),
    Page(MATCH, "Canlı Maç", SEC_CLUB, "Maçını canlı yönet ya da izle"),
    Page(ACADEMY, "Akademi", SEC_CLUB, "U-21 akademisi"),
    Page(STAFF, "Teknik Heyet", SEC_CLUB, "Personel, etkiler, işe alma"),
    Page(FIXTURES, "Fikstür ve Sonuçlar", SEC_CLUB, "Kulübün maçları, haftanın sonuçları, hafta raporu"),
    Page(TABLE, "Puan Durumu", SEC_COMPS, "Puan durumu ve gol krallığı"),
    Page(ARENA, "Devler Arenası", SEC_COMPS, "Kura, turnuva ağacı, gruplar"),
    Page(NATIONAL, "Milli Takım", SEC_COMPS, "Milli takım görevi ve Dünya Kupası"),
    Page(TRANSFER, "Transfer Merkezi", SEC_CLUB,
         "Oyuncu arama, teklif dosyaları, sözleşmeler, gelen teklifler, ödemeler"),
    Page(CLUB, "Finans ve Tesisler", SEC_CLUB, "Bütçe, maaşlar, tesisler, sponsorluk"),
    Page(BOARD, "Yönetim", SEC_CLUB,
         "Yönetim kurulu: sezon hedefi, güven, uyarılar, bütçe önerisi, iş ilanları"),
    Page(MANAGER, "Menajer", SEC_MANAGER, "Menajer profili, tanınırlık, kariyer"),
    Page(NATIONS, "Ülkeler ve Kulüpler", SEC_NATIONS, "Ülke, lig ve kulüp listesi; kadrolar"),
    Page(FIND, "Bul", SEC_FIND, "Oyuncu ya da kulüp ara"),
    Page(OPTIONS, "Oyun Seçenekleri", SEC_OPTIONS, "Tema ve oyun seçenekleri"),
    Page(ADMIN, "Dünya Yönetimi", SEC_OPTIONS, "Dünyanın sahibi / yöneticisi"),
    Page(PLAYER, "Oyuncu", SEC_WORLD, "Oyuncunun CM ekranı", menu=False),
    Page(CLUB_PAGE, "Kulüp", SEC_WORLD, "Kulübün sayfası: kadro, fikstür, bilgi, tarih", menu=False),
    Page(NATION, "Ülke", SEC_WORLD, "Ülkenin ligleri, kulüpleri ve en iyi oyuncuları", menu=False),
)
PAGES: dict[str, Page] = {p.slug: p for p in _PAGE_LIST}
PARAM_PAGES = tuple(p.slug for p in _PAGE_LIST if not p.menu)

SECTIONS: tuple[Section, ...] = (
    Section(SEC_CLUB, "Kulüp", ((SQUAD, "Kadro"), (TACTICS, "Taktik"), (FIXTURES, "Maçlar"), (MATCH, "Canlı Maç"),
                                (TRANSFER, "Transfer"), (ACADEMY, "Akademi"), (STAFF, "Teknik Heyet"),
                                (CLUB, "Finans"), (BOARD, "Yönetim"))),
    Section(SEC_MANAGER, "Menajer", ((MANAGER, "Profil"), (NEWS, "Haberler ve Tarih"))),
    Section(SEC_COMPS, "Yarışmalar", ((TABLE, "Puan Durumu"), (FIXTURES, "Fikstür ve Sonuçlar"),
                                      (ARENA, "Devler Arenası"), (NATIONAL, "Milli Takım"))),
    Section(SEC_NATIONS, "Ülkeler ve Kulüpler", ((NATIONS, "Ülkeler ve Kulüpler"),)),
    Section(SEC_FIND, "Bul", ((FIND, "Bul"),)),
    Section(SEC_INBOX, "Gelen Kutusu", ((HOME, "Gelen Kutusu"), (INBOX, "Teklifler ve Mesajlar")), tabs=False),
    Section(SEC_OPTIONS, "Oyun Seçenekleri", ((OPTIONS, "Oyun Seçenekleri"), (ADMIN, "Dünya Yönetimi"))),
    Section(SEC_WORLD, "Dünya", ((PLAYER, "Oyuncu"), (CLUB_PAGE, "Kulüp"), (NATION, "Ülke")), tabs=False,
            hidden=True),
)
SECTION_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}
# Gelen Kutusu sayaci: OKUNMAMIS mesajlar (15D kalici gelen kutusu) + yanit bekleyen isler (dosyalar, maas
# talepleri, paylasilan dunyada teklif / mesaj / bildirim)
INBOX_COUNT_PAGES = (HOME, TRANSFER, SQUAD, INBOX, BOARD)

CAREER_PAGES = (HOME, NEWS, SQUAD, TACTICS, MATCH, ACADEMY, STAFF, FIXTURES, TABLE, ARENA, TRANSFER, CLUB)
TOURNAMENT_PAGES = (HOME, SQUAD, MATCH, STAFF, ARENA)
# 15C-U: kovulan / istifa eden menajer kulupsuzdur (cm.user_team is None). Bu sayfalar kulupsuz de cizilir;
# kadro / taktik / transfer / canli mac / fikstur / akademi / teknik heyet / finans kulup ister.
NO_CLUB_PAGES = (HOME, INBOX, BOARD, TABLE, ARENA, NATIONAL, ADMIN, MANAGER, NATIONS, FIND, OPTIONS)
# 14S: her modda ve dunyada var olan CM kabuk ekranlari (menunun Menajer / Ulkeler ve Kulupler / Bul / Oyun
# Secenekleri ogeleri). Oyun sayfalari (pages_for) sozlesmesi 13I'deki gibi kalir; ekranda with_shell ile eklenir.
SHELL_PAGES = (MANAGER, NATIONS, FIND, OPTIONS)
WORLD_PAGES = (INBOX, NATIONAL, ADMIN)                    # yalnizca paylasilan / milli takimli dunyada
ADMIN_ROLES = ("OWNER", "ADMIN")
# Eski sekme adlari ve kisaltmalar (?sayfa=lig gibi elle yazilan baglantilar; 13I etiketleri)
ALIASES = {"lig": TABLE, "finans": CLUB, "tesisler": CLUB, "pazar": TRANSFER, "transfer-merkezi": TRANSFER,
           "mac": MATCH, "canli": MATCH, "heyet": STAFF, "arena": ARENA, "milli": NATIONAL, "haber": NEWS,
           "teklifler": INBOX, "yonetim": ADMIN, "fikstur-sonuclar": FIXTURES, "ana": HOME, "gelen-kutusu": HOME,
           "ana sayfa": HOME, "kulüp & finans": CLUB, "fikstür & sonuçlar": FIXTURES, "haberler & tarih": NEWS,
           "teklifler & mesajlar": INBOX, "ayarlar": OPTIONS, "secenekler": OPTIONS, "ulkeler-kulupler": NATIONS,
           "maçlar": FIXTURES, "finans ve tesisler": CLUB}


def pages_for(*, tournament: bool, shared: bool, internationals: bool, role: str | None,
              board: bool = False) -> list[str]:
    """Gorunen sayfalar (kayit sirasiyla). Dunya sayfalari: Teklifler ve Mesajlar paylasilan dunyada, Milli Takim milli
    takimlar aciksa, Dunya Yonetimi paylasilan dunyada sahip / yoneticide. 15C-U: Yonetim yalnizca yonetim kurulu
    kurali acikken (kisisel kariyerde varsayilan acik, paylasilan dunyada dunya kurali `board_confidence`)."""
    wanted = set(TOURNAMENT_PAGES if tournament else CAREER_PAGES)
    if shared:
        wanted.add(INBOX)
    if internationals:
        wanted.add(NATIONAL)
    if shared and role in ADMIN_ROLES:
        wanted.add(ADMIN)
    if board and not tournament:
        wanted.add(BOARD)
    return [p.slug for p in _PAGE_LIST if p.slug in wanted]


def pages_without_club(pages: Sequence[str]) -> list[str]:
    """15C-U: kulupsuz menajerin (kovulma / istifa) gorebilecegi sayfalar; sira korunur."""
    return [p for p in pages if p in NO_CLUB_PAGES]


def with_shell(pages: Sequence[str]) -> list[str]:
    """Oyun sayfalari + CM kabuk ekranlari (kayit sirasiyla): web_app.main'in menusu ve yonlendirmesi bunu kullanir."""
    wanted = set(pages) | set(SHELL_PAGES)
    return [p.slug for p in _PAGE_LIST if p.slug in wanted]


def _bare(text: str) -> str:
    """Bastaki simge (emoji) sozcugu atilir: "📋 Kadro" -> "Kadro"."""
    head, _, rest = text.partition(" ")
    if rest and not any(ch.isalnum() for ch in head):
        return rest
    return text


def resolve(value) -> str | None:
    """Slug, takma ad, sayfa ya da sekme etiketi ("Kadro", "📋 Kadro", "Maçlar") -> slug; bilinmeyen -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if text in PAGES:
        return text
    folded = _bare(text).casefold()
    if folded in ALIASES:
        return ALIASES[folded]
    for page in _PAGE_LIST:
        if folded == page.label.casefold():
            return page.slug
    for section in SECTIONS:
        for slug, tab in section.pages:
            if folded == tab.casefold():
                return slug
    return None


def clean_param(slug: str, value) -> str | int | None:
    """Parametre dogrulama (URL / gecmis / callback): id sayfalarinda pozitif tamsayi, ulkede kisa duz metin."""
    if value is None or slug not in PARAM_QUERY:
        return None
    if isinstance(value, list | tuple):
        value = value[0] if value else None
        if value is None:
            return None
    if slug in INT_PARAM_PAGES:
        if isinstance(value, bool):
            return None
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return number if 0 < number < 2**31 else None
    text = str(value).strip()
    return text[:60] if text and HISTORY_SEP not in text else None


def current_param() -> str | int | None:
    """Secili sayfanin parametresi (oyuncu / kulup id, lig id, ulke adi); yoksa None."""
    ss = st.session_state
    return clean_param(ss.get(NAV_KEY) or "", ss.get(PARAM_KEY))


def _entry(slug: str, param=None) -> str:
    return f"{slug}{HISTORY_SEP}{param}" if param is not None else slug


def _split(entry: str) -> tuple[str, str | int | None]:
    slug, _, raw = str(entry).partition(HISTORY_SEP)
    return slug, clean_param(slug, raw) if raw else None


def _sync_query(page: str, param) -> None:
    """URL: ?sayfa= + (parametreli sayfada) id / lig / ad; baska sayfanin parametresi URL'den silinir."""
    if st.query_params.get(QUERY_KEY) != page:
        st.query_params[QUERY_KEY] = page
    wanted = PARAM_QUERY.get(page) if param is not None else None
    for key in set(PARAM_QUERY.values()):
        if key == wanted:
            if st.query_params.get(key) != str(param):
                st.query_params[key] = str(param)
        elif key in st.query_params:
            del st.query_params[key]


def current_page(pages: Sequence[str]) -> str:
    """
    Secili sayfa: oturum -> URL (?sayfa= [+ id / lig / ad]) -> Gelen Kutusu. URL guncel tutulur (yenileme ayni sayfayi,
    parametresiyle acar). Parametreli sayfalar (oyuncu, kulup, ulke) menude yok ama her modda acilir.
    """
    ss = st.session_state
    ss[PAGES_KEY] = [p for p in pages if PAGES[p].menu]
    allowed = [*pages, *(p for p in PARAM_PAGES if p not in pages)]
    page = ss.get(NAV_KEY)
    if page not in allowed:
        wanted = resolve(st.query_params.get(QUERY_KEY))
        page = wanted if wanted in allowed else (HOME if HOME in pages else pages[0])
        ss[NAV_KEY] = page
        param = clean_param(page, st.query_params.get(PARAM_QUERY.get(page, ""))) if page in PARAM_QUERY else None
        if param is None:
            ss.pop(PARAM_KEY, None)
        else:
            ss[PARAM_KEY] = param
    param = current_param()
    if page in PARAM_PAGES and param is None:                 # parametresiz oyuncu / kulup sayfasi: Bul'a
        page = FIND if FIND in allowed else (HOME if HOME in pages else pages[0])
        ss[NAV_KEY] = page
    _sync_query(page, param)
    history, pos = _history()
    entry = _entry(page, param)
    if not history or history[pos] != entry:          # ilk cizim ya da URL / callback'le gelinen sayfa
        _push(entry)
    return page


def primary_section(slug: str) -> Section:
    return next((s for s in SECTIONS if any(p == slug for p, _ in s.pages)), SECTIONS[0])


def section_for(page: str, pages: Sequence[str] | None = None) -> Section:
    """Sayfanin menu bolumu: en son hangi bolumden gelindiyse o (sayfa o bolumdeyse), yoksa sayfanin ilk bolumu.
    Parametreli sayfalarda (oyuncu / kulup / ulke) menude secili kalan bolum, son gelinen bolumdur."""
    chosen = SECTION_BY_KEY.get(st.session_state.get(SECTION_KEY) or "")
    if chosen is not None and any(p == page for p, _ in chosen.pages):
        return chosen
    if page in PARAM_PAGES:
        return chosen if chosen is not None else SECTION_BY_KEY[SEC_FIND]
    return primary_section(page)


def section_pages(section: Section, pages: Sequence[str]) -> list[tuple[str, str]]:
    return [(slug, tab) for slug, tab in section.pages if slug in pages]


def _history() -> tuple[list[str], int]:
    ss = st.session_state
    history = [e for e in (ss.get(HISTORY_KEY) or []) if isinstance(e, str) and _split(e)[0] in PAGES]
    pos = ss.get(HISTORY_POS_KEY)
    pos = pos if isinstance(pos, int) and 0 <= pos < len(history) else len(history) - 1
    return history, max(pos, 0)


def _push(entry: str) -> None:
    history, pos = _history()
    if history and history[pos] == entry:
        return
    history = (history[: pos + 1] + [entry])[-HISTORY_MAX:]
    st.session_state[HISTORY_KEY] = history
    st.session_state[HISTORY_POS_KEY] = len(history) - 1


def can_step(delta: int) -> bool:
    history, pos = _history()
    return bool(history) and 0 <= pos + int(delta) < len(history)


def goto(slug: str, *, scroll: bool = True, section: str | None = None, param=None) -> None:
    """
    Callback'lerden sayfa degistirme (bilinmeyen slug yok sayilir). scroll: yeni sayfa en ustten baslar. param:
    parametreli sayfanin parametresi (oyuncu / kulup id, lig id, ulke adi); gecmise "slug|param" olarak yazilir.
    """
    if slug in PAGES:
        value = clean_param(slug, param)
        if slug in PARAM_PAGES and value is None:
            return
        st.session_state[NAV_KEY] = slug
        if value is None:
            st.session_state.pop(PARAM_KEY, None)
        else:
            st.session_state[PARAM_KEY] = value
        st.session_state.pop(PROFILE_STATE_KEY, None)
        if section in SECTION_BY_KEY:
            st.session_state[SECTION_KEY] = section
        _push(_entry(slug, value))
        if scroll:
            st.session_state[SCROLL_TOP_KEY] = True


def replace_param(param) -> None:
    """Ayni parametreli sayfada parametreyi degistirir, gecmise YENI kayit eklemeden (profil ◄ ►: listede gezinme)."""
    ss = st.session_state
    page = ss.get(NAV_KEY)
    value = clean_param(page or "", param)
    if page not in PARAM_QUERY or value is None:
        return
    ss[PARAM_KEY] = value
    history, pos = _history()
    if history:
        history[pos] = _entry(page, value)
        ss[HISTORY_KEY] = history
        ss[HISTORY_POS_KEY] = pos


PROFILE_LIST_KEY = "pv_list"               # player_view.LIST_KEY: oyuncu ekraninda ◄ ► listesi


@requires_auth
def cb_open_player(player_id: int, ids: Sequence[int] | None = None) -> None:
    """Oyuncu sayfasi (CM oyuncu ekrani): her tabloda oyuncu adina tik. ids: ◄ ► listesi. Yalnizca oturum durumu."""
    pid = clean_param(PLAYER, player_id)
    if pid is None:
        return
    listed = [int(i) for i in (ids or ()) if isinstance(i, int) and not isinstance(i, bool)]
    st.session_state[PROFILE_LIST_KEY] = listed if pid in listed else [pid]
    goto(PLAYER, param=pid)


@requires_auth
def cb_open_club(team_id: int) -> None:
    """Kulup sayfasi: her tabloda kulup adina tik. Yalnizca oturum durumu."""
    goto(CLUB_PAGE, param=team_id)


@requires_auth
def cb_open_league(league_id: int) -> None:
    """Lig (yarisma) sayfasi: Puan Durumu sayfasi o ligle."""
    goto(TABLE, param=league_id, section=SEC_COMPS)


@requires_auth
def cb_open_nation(name: str) -> None:
    """Ulke sayfasi: ligleri, kulupleri, en iyi oyunculari."""
    goto(NATION, param=name)


@requires_auth
def cb_nav(slug: str, section: str | None = None) -> None:
    """Sekme / alt eylem dugmesi: yalnizca oturum durumu (veritabani yok)."""
    goto(slug, section=section)


@requires_auth
def cb_menu(section: str, slug: str) -> None:
    """Kenar cubugu menu dugmesi: bolumun ilk sayfasi (bolum hatirlanir: sekme satiri o bolumunkidir)."""
    goto(slug, section=section)


@requires_auth
def cb_nav_top() -> None:
    """Telefon ust menusu (selectbox) degisti."""
    slug = str(st.session_state.get(TOP_KEY) or "")
    goto(slug, section=primary_section(slug).key)


@requires_auth
def cb_nav_step(delta: int) -> None:
    """Geri / Ileri (CM'deki oklar): ziyaret edilen sayfalar arasinda gecis; gecmise yeni kayit eklemez."""
    history, pos = _history()
    target = pos + int(delta)
    if 0 <= target < len(history):
        slug, param = _split(history[target])
        st.session_state[HISTORY_POS_KEY] = target
        st.session_state[NAV_KEY] = slug
        if param is None:
            st.session_state.pop(PARAM_KEY, None)
        else:
            st.session_state[PARAM_KEY] = param
        st.session_state.pop(PROFILE_STATE_KEY, None)
        st.session_state[SCROLL_TOP_KEY] = True


def step_buttons(prefix: str, *, labels: tuple[str, str] = ("◄", "►"), width: str = "content") -> None:
    """Geri / Ileri dugmeleri ({prefix}_back / {prefix}_fwd); cagiranin kabinda (yatay kap) cizilir."""
    st.button(labels[0], key=f"{prefix}_back", on_click=cb_nav_step, args=(-1,), disabled=not can_step(-1),
              help="Önceki ekran", width=width)
    st.button(labels[1], key=f"{prefix}_fwd", on_click=cb_nav_step, args=(1,), disabled=not can_step(1),
              help="Sonraki ekran", width=width)


def date_bar(lines: Sequence[str] | str) -> None:
    """
    Kenar cubugunun tepesi (CM 01/02: "Wednesday 7.11.01"): ILK satir gercek oyun tarihi (buyuk, ortali sari),
    sonraki satirlar ikincil (sezon / hafta -- 15D-U'dan beri yardimci bilgi) + ◄ ► (ziyaret gecmisi).
    """
    rows = [str(r) for r in ([lines] if isinstance(lines, str) else list(lines)) if str(r).strip()]
    with st.container(key=DATEBAR_KEY):
        head = f'<div class="ofm-date">{escape(rows[0])}</div>' if rows else ""
        sub = ("<div class=\"ofm-date-sub\">" + "<br>".join(escape(r) for r in rows[1:]) + "</div>"
               if len(rows) > 1 else "")
        st.markdown(head + sub, unsafe_allow_html=True)
        with st.container(horizontal=True, horizontal_alignment="center", key="ofm_datearrows"):
            step_buttons("nav")


def page_footer(slug: str, actions: Sequence[tuple[str, str]] = ()) -> None:
    """
    Her ekranin alti (CM iskeleti): alt eylem dugmeleri satiri (baska ekranlara kisayol: nav_act_{hedef}) ve genis,
    kabartmali Geri / Ileri (ziyaret gecmisi: nav_foot_back / nav_foot_fwd).
    """
    with st.container(key=FOOTER_KEY):
        shown = [(text, target) for text, target in actions if target in PAGES and target != slug]
        if shown:
            with st.container(horizontal=True, key="ofm_footer_actions"):
                for label_text, target in shown:
                    st.button(label_text, key=f"nav_act_{target}", on_click=cb_nav, args=(target,))
        with st.container(horizontal=True, key="ofm_footer_steps"):
            step_buttons("nav_foot", labels=("Geri", "İleri"), width="stretch")


def label(slug: str, counts: Mapping[str, int] | None = None) -> str:
    page = PAGES[slug]
    count = int((counts or {}).get(slug) or 0)
    return f"{page.label} ({count})" if count > 0 else page.label


def button_key(slug: str) -> str:
    return f"{BUTTON_PREFIX}{slug}"


def menu_key(section: str) -> str:
    return f"{MENU_PREFIX}{section}"


def inbox_count(counts: Mapping[str, int] | None) -> int:
    return sum(int((counts or {}).get(slug) or 0) for slug in INBOX_COUNT_PAGES)


def menu(pages: Sequence[str], current: str, counts: Mapping[str, int] | None = None, *,
         club_name: str = "Kulüp", continue_action: Callable[[str], None] | None = None) -> None:
    """Kenar cubugu CM menusu (st.sidebar icinde): Devam + bolumler. Secili bolum birincil dugme."""
    active = section_for(current, pages).key
    with st.container(key=MENU_KEY):
        if continue_action is not None:
            continue_action("nav")
        for section, text, first, visible in menu_items(pages, counts, club_name):
            st.button(text, key=menu_key(section.key), on_click=cb_menu, args=(section.key, first),
                      type="primary" if section.key == active else "secondary", width="stretch",
                      help=" · ".join(tab for _slug, tab in visible) if len(visible) > 1 else None)


def menu_items(pages: Sequence[str], counts: Mapping[str, int] | None, club_name: str
               ) -> list[tuple[Section, str, str, list[tuple[str, str]]]]:
    """CM kisa menusunun ogeleri (bolum, etiket, ilk sayfa, gorunen sekmeler): kenar cubugu ve telefon izgarasi."""
    items = []
    for section in SECTIONS:
        visible = section_pages(section, pages)
        if not visible or section.hidden:
            continue
        text = club_name if section.key == SEC_CLUB else section.label
        if section.key == SEC_INBOX:
            n = inbox_count(counts)
            text += f" ({n})" if n else ""
        items.append((section, text, visible[0][0], visible))
    return items


def tab_row(pages: Sequence[str], current: str, counts: Mapping[str, int] | None = None) -> None:
    """Ekranin CM sekme satiri (bolumun sayfalari; tek sayfali bolumde ya da tabs=False'ta cizilmez)."""
    section = section_for(current, pages)
    visible = section_pages(section, pages)
    if not section.tabs or len(visible) < 2:
        return
    with st.container(horizontal=True, key=TABS_KEY, gap="small"):
        for slug, tab in visible:
            n = int((counts or {}).get(slug) or 0)
            st.button(f"{tab} ({n})" if n else tab, key=button_key(slug), on_click=cb_nav, args=(slug, section.key),
                      type="primary" if slug == current else "secondary", width="stretch",
                      help=PAGES[slug].hint or None)


TOP_MENU_KEY = "top_menu"                       # telefon izgarasi: TEK segmented control (bolumler)


@requires_auth
def cb_top_menu() -> None:
    """Telefon izgarasinda bir bolume dokunuldu: bolumun ilk (gorunen) sayfasi."""
    section = SECTION_BY_KEY.get(str(st.session_state.get(TOP_MENU_KEY) or ""))
    if section is None:
        return
    visible = section_pages(section, st.session_state.get(PAGES_KEY) or [])
    if visible:
        goto(visible[0][0], section=section.key)


def top_nav(pages: Sequence[str], current: str, counts: Mapping[str, int] | None = None,
            continue_action: Callable[[str], None] | None = None, date_text: str = "", *,
            club_name: str = "Kulüp", notes_action: Callable[[], None] | None = None) -> None:
    """
    Telefon genisligi (<= 768 px): ana alanin ustunde CM 01/02 kisa menusu IZGARA olarak (14FG): ◄ tarih ► satiri,
    altinda 4 x 2 hucre -- Devam (top_continue) + [Kulup], Menajer, Yarismalar, Ulkeler ve Kulupler, Bul, Gelen Kutusu
    (n), Oyun Secenekleri. Bolumler TEK widget (segmented control, top_menu; cizim maliyeti dusuk), CSS izgarasi
    Devam'la ayni satirlara dizer. Yatay kaydirma yok; masaustunde CSS ile gizli (kenar cubugu var).
    """
    items = menu_items(pages, counts, club_name)
    labels = {section.key: text for section, text, _first, _visible in items}
    active = section_for(current, pages).key
    if st.session_state.get(TOP_MENU_KEY) != active:
        st.session_state[TOP_MENU_KEY] = active if active in labels else None
    with st.container(key=TOP_CONTAINER_KEY):
        with st.container(horizontal=True, vertical_alignment="center", key="ofm_topnav_row"):
            st.button("◄", key="top_back", on_click=cb_nav_step, args=(-1,), disabled=not can_step(-1),
                      help="Önceki ekran")
            st.markdown(f'<div class="ofm-topdate">{escape(date_text)}</div>' if date_text else "",
                        unsafe_allow_html=True)
            st.button("►", key="top_fwd", on_click=cb_nav_step, args=(1,), disabled=not can_step(1),
                      help="Sonraki ekran")
        with st.container(key="ofm_topgrid", gap=None):
            if continue_action is not None:
                continue_action("top")
            st.segmented_control("Menü", list(labels), key=TOP_MENU_KEY, format_func=lambda k: labels.get(k, k),
                                 on_change=cb_top_menu, label_visibility="collapsed")
        if notes_action is not None:                  # izgaranin altinda: Devam'in notu (yeni sezon neden bekliyor)
            notes_action()


# ---------------------------------------------------------------- baslik bandi (kulup renkleri)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
BAND_DEFAULT = ("#0a2a8a", "#ffdd00")


@functools.lru_cache(maxsize=1024)
def club_band_colors(name: str | None) -> tuple[str, str]:
    """
    Kulubun (zemin, yazi) renkleri. Kaynak: club_colors.colors_for (acik veri, CC0; modul yoksa ya da None donerse)
    -> match_day_view'in crc32 paleti (takim adindan deterministik). colors_for'un yazi rengine guvenilir (buyuk metin
    esigi 3.0); yalnizca bunun altindaysa en okunur ink.
    """
    if not name:
        return BAND_DEFAULT
    from match_day_view import CLUB_COLORS, ink_for
    from ofm_theme import AA_LARGE, contrast_ratio

    got = None
    try:
        import club_colors  # noqa: PLC0415 -- paralel W paketi; yoksa crc32 paleti

        got = club_colors.colors_for(str(name))
    except Exception:                                 # ImportError ya da bozuk veri: sessizce palete dus
        got = None
    if got and len(got) == 2 and all(isinstance(c, str) and _HEX.match(c) for c in got):
        bg, fg = got
        # Bant buyuk kalin yazidir: WCAG AA buyuk metin esigi 3.0 (colors_for zaten saglar); 4.5'e zorlanmaz,
        # yoksa gercek kimlikler bozulur (Galatasaray sari yazi, Arsenal / Liverpool beyaz)
        if contrast_ratio(fg, bg) < AA_LARGE:
            fg = ink_for(bg)
        return bg, fg
    bg = CLUB_COLORS[zlib.crc32(str(name).encode("utf-8")) % len(CLUB_COLORS)]
    return bg, ink_for(bg)


def band_html(title: str, colors: tuple[str, str] | None = None, *, sub: str = "") -> str:
    """CM baslik bandi: kulup renginde, ortali, golgeli buyuk yazi. Tum metinler kacisli; renkler yalnizca #rrggbb."""
    from ofm_theme import contrast_ratio

    bg, fg = colors if colors and all(_HEX.match(c or "") for c in colors) else BAND_DEFAULT
    # CM golgesi: acik yazida siyah, koyu yazida (sari / beyaz zemin) acik golge -- bulanik gorunmesin
    shadow = "2px 2px 0 #000" if contrast_ratio(fg, "#000000") > contrast_ratio(fg, "#ffffff") \
        else "1px 1px 0 rgba(255,255,255,.45)"
    small = f'<span class="s">{escape(sub)}</span>' if sub else ""
    return (f'<div class="ofm-band" style="--band-bg:{bg};--band-fg:{fg};--band-shadow:{shadow}">'
            f'<span class="t">{escape(title)}</span>{small}</div>')


def table_html(header: Sequence[str], rows: Sequence[Sequence], *, left: Sequence[int] = (),
               highlight: set[int] | frozenset[int] = frozenset(), avr: int | None = None) -> str:
    """
    CM tablosu (salt gorunen tablolar icin; satira tik gerekmeyen yerler): gri kabartmali baslik hucreleri, beyaz
    sayilar, kendi satirin sari (highlight: satir sirasi), avr: mor "Ort. not" sutunu. Tum hucreler kacisli.
    """
    lefts = set(left)
    head = "".join(f'<th class="{"l" if i in lefts else ""}{" avr" if i == avr else ""}{"" if h else " blank"}">'
                   f"{escape(str(h))}</th>" for i, h in enumerate(header))
    body = []
    for n, row in enumerate(rows):
        cells = "".join(
            f'<td class="{"l" if i in lefts else ""}{" avr" if i == avr else ""}">{escape(str(v))}</td>'
            for i, v in enumerate(row))
        body.append(f'<tr class="{"me" if n in highlight else ""}">{cells}</tr>')
    return (f'<div class="cm-table-wrap"><table class="cm-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def facts_html(items: Sequence[tuple[str, object]]) -> str:
    """CM bilgi satiri (st.metric kartlari yerine, 14FG): gri kabartmali baslik hucreleri, altinda beyaz degerler --
    tek satirlik yogun tablo. Tum metinler kacisli."""
    items = list(items)
    if not items:
        return ""
    return table_html([str(k) for k, _v in items], [[v for _k, v in items]]).replace(
        'class="cm-table-wrap"', 'class="cm-table-wrap cm-facts"', 1)


def pairs_html(rows: Sequence[tuple[str, object]], *, status: bool = False) -> str:
    """CM bilgi paneli: beyaz etiket, saga yasli kalin deger (status: turuncu degerler). Tum metinler kacisli."""
    body = "".join(f'<div class="row"><span class="k">{escape(str(k))}</span><b>{escape(str(v))}</b></div>'
                   for k, v in rows)
    return f'<div class="cm-pairs{" st" if status else ""}">{body}</div>'


def name_title_html(text: str) -> str:
    """Panel basligi, ad icerdigi icin buyuk harfe cevrilmeden (tr buyuk harf kurali "Lions"u "LİONS" yapar)."""
    return f'<div class="ofm-panel-title ofm-keepcase">{escape(text)}</div>'


def page_header_html(slug: str) -> str:
    """Eski (13I) sayfa basligi yerine: sayfanin adi CM bandinda (renk: varsayilan bant)."""
    return band_html(PAGES[slug].label)


def club_header_html(name: str, subtitle: str, chips: Sequence[tuple[str, str]] = ()) -> str:
    """Kulup ozeti (ad, lig / durum satiri, butce ciplari). Tum metinler kacisli."""
    items = "".join(f"<span>{escape(str(k))} <b>{escape(str(v))}</b></span>" for k, v in chips)
    return (f'<div class="ofm-club"><div class="n">{escape(name)}</div><div class="l">{escape(subtitle)}</div>'
            + (f'<div class="m">{items}</div>' if items else "") + "</div>")


NAV_CSS = """
<style>
[data-testid="stSidebar"],[data-testid="stSidebarContent"]{background:linear-gradient(180deg,var(--ofm-menu-top),
  var(--ofm-menu-bot)) !important}
.st-key-ofm_datebar{padding:.35rem .2rem .2rem;border-bottom:1px solid #000}
.ofm-date{text-align:center;color:var(--ofm-menu-text) !important;font-weight:700;font-size:.88rem;line-height:1.3;
  white-space:nowrap;text-shadow:1px 1px 0 var(--ofm-shadow)}
.ofm-date-sub{text-align:center;color:var(--ofm-menu-text) !important;font-weight:400;font-size:.8rem;
  line-height:1.25;opacity:.82;text-shadow:1px 1px 0 var(--ofm-shadow)}
[data-testid="stSidebar"] .ofm-date,[data-testid="stSidebar"] .ofm-date-sub{color:var(--ofm-menu-text) !important}
.st-key-ofm_datebar [data-testid="stMarkdownContainer"],.st-key-ofm_datebar [data-testid="stMarkdown"]{
  margin-bottom:0 !important}
.st-key-ofm_datearrows{gap:1.2rem !important;justify-content:center;margin-top:.3rem}
.st-key-ofm_datearrows button{background:transparent !important;border:0 !important;box-shadow:none !important;
  min-height:1.6rem !important;padding:0 .4rem !important}
.st-key-ofm_datearrows button p{color:var(--ofm-menu-text) !important;font-size:1.05rem;font-weight:700;
  text-shadow:1px 1px 0 var(--ofm-shadow)}
.st-key-ofm_datearrows button:disabled{opacity:.35 !important}
.st-key-ofm_nav{gap:0 !important;border-top:1px solid #000}
.st-key-ofm_nav [data-testid="stElementContainer"]{margin:0}
.st-key-ofm_nav button{width:100%;background:transparent !important;border:0 !important;border-radius:0 !important;
  border-bottom:1px solid rgba(0,0,0,.45) !important;box-shadow:none !important;min-height:2.55rem;
  padding:.45rem .25rem !important;justify-content:center !important}
.st-key-ofm_nav button>div{justify-content:center !important}
.st-key-ofm_nav button p{color:var(--ofm-menu-text) !important;font-weight:700 !important;text-align:center;
  font-size:.98rem;line-height:1.2;text-shadow:1px 1px 0 var(--ofm-shadow);white-space:normal}
.st-key-ofm_nav button:hover{background:rgba(255,255,255,.08) !important}
.st-key-ofm_nav [data-testid^="stBaseButton-primary"]{background:var(--ofm-menu-on) !important;
  box-shadow:inset 3px 0 0 var(--ofm-menu-text) !important}
.st-key-ofm_nav [class*="st-key-nav_continue"] button,.st-key-ofm_nav [class*="st-key-nav_new_season"] button,
.st-key-ofm_nav [class*="st-key-nav_live"] button{background:rgba(255,255,255,.07) !important;
  box-shadow:none !important;min-height:2.9rem}
.st-key-ofm_nav [class*="st-key-nav_continue"] button p,.st-key-ofm_nav [class*="st-key-nav_new_season"] button p,
.st-key-ofm_nav [class*="st-key-nav_live"] button p{font-size:1.08rem}
.ofm-band{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:3.3rem;
  background:var(--band-bg,var(--ofm-band));color:var(--band-fg,var(--ofm-band-text));border:1px solid #000;
  text-align:center;padding:.25rem .8rem;margin:0 0 .4rem}
.ofm-band .t{font-weight:700;font-size:1.85rem;line-height:1.15;letter-spacing:.01em;
  text-shadow:var(--band-shadow,2px 2px 0 #000);overflow-wrap:anywhere;color:var(--band-fg,var(--ofm-band-text))}
.ofm-band .s{font-size:.8rem;opacity:.9;color:var(--band-fg,var(--ofm-band-text))}
.st-key-ofm_bandrow{gap:6px !important;align-items:flex-start}
.st-key-ofm_bandrow>div:first-child{flex:1 1 auto;min-width:0}
.st-key-ofm_tabs{gap:2px !important;flex-wrap:wrap}
.st-key-ofm_tabs>div{flex:1 1 6.5rem;min-width:0}
[data-testid="stMain"] .st-key-ofm_tabs button[data-testid]{width:100%;background:var(--ofm-tab) !important;
  border:1px solid #000 !important;border-radius:0 !important;box-shadow:none !important;min-height:2.35rem;
  padding:.3rem .25rem !important}
[data-testid="stMain"] .st-key-ofm_tabs button[data-testid] p{color:var(--ofm-tab-text) !important;
  font-weight:400 !important;font-size:.9rem;text-shadow:none !important;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
[data-testid="stMain"] .st-key-ofm_tabs button[data-testid^="stBaseButton-primary"]{background:var(--ofm-tab-sel) !important;
  outline:1px solid var(--ofm-tab-text);outline-offset:-4px}
[data-testid="stMain"] .st-key-ofm_tabs button[data-testid^="stBaseButton-primary"] p{color:var(--ofm-tab-on) !important}
[data-testid="stMain"] .st-key-ofm_tabs button[data-testid]:hover p{color:var(--ofm-tab-on) !important}
[data-testid="stButtonGroup"] [role="radiogroup"],[data-testid="stButtonGroup"]>div{gap:2px}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"]{background:var(--ofm-tab) !important;
  border:1px solid #000 !important;border-radius:0 !important;box-shadow:none !important}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"] p{color:var(--ofm-tab-text) !important;
  font-weight:400;text-shadow:none !important}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"]{
  background:var(--ofm-tab-sel) !important;outline:1px solid var(--ofm-tab-text);outline-offset:-4px}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"] p{
  color:var(--ofm-tab-on) !important}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"]{gap:2px !important;
  flex-wrap:wrap}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"]>div{flex:1 1 auto}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]{
  background:var(--ofm-tab);border:1px solid #000;padding:.3rem .65rem;margin:0 !important;width:100%;
  justify-content:center;cursor:pointer}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"]>div>div:first-child{
  display:none}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"] p{
  color:var(--ofm-tab-text) !important;font-size:.88rem;text-shadow:none !important;white-space:nowrap}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"][data-selected="true"]{
  background:var(--ofm-tab-sel);outline:1px solid var(--ofm-tab-text);outline-offset:-4px}
[data-testid="stMain"] [data-testid="stRadioGroup"][aria-orientation="horizontal"] [data-testid="stRadioOption"][data-selected="true"] p{
  color:var(--ofm-tab-on) !important}
[data-testid="stButtonGroup"] button[data-variant="pills"]{background:var(--ofm-tab) !important;border:1px solid #000 !important;
  border-radius:0 !important;box-shadow:none !important}
[data-testid="stButtonGroup"] button[data-variant="pills"] p{color:var(--ofm-tab-text) !important;text-shadow:none !important}
[data-testid="stButtonGroup"] button[data-variant="pills"][aria-checked="true"]{background:var(--ofm-tab-sel) !important;
  outline:1px solid var(--ofm-tab-text);outline-offset:-4px}
[data-testid="stButtonGroup"] button[data-variant="pills"][aria-checked="true"] p{color:var(--ofm-tab-on) !important}
.st-key-ofm_footer{margin-top:.6rem}
.st-key-ofm_footer_actions{gap:4px !important;flex-wrap:wrap}
/* Alt kisayol satiri gezinmedir, eylem degil: CM'de gezinme sekme seridinin rengindedir (gri dugme degil). */
[data-testid="stMain"] .st-key-ofm_footer_actions button[data-testid]{background:var(--ofm-tab) !important;
  border:1px solid #000 !important;box-shadow:none !important;outline:0 !important;min-height:2.2rem}
[data-testid="stMain"] .st-key-ofm_footer_actions button[data-testid] p{color:var(--ofm-tab-text) !important;
  font-weight:400 !important;text-shadow:none !important}
[data-testid="stMain"] .st-key-ofm_footer_actions button[data-testid]:hover{background:var(--ofm-tab-sel) !important;
  outline:1px solid var(--ofm-tab-text) !important;outline-offset:-3px}
[data-testid="stMain"] .st-key-ofm_footer_actions button[data-testid]:hover p{color:var(--ofm-tab-on) !important}
.st-key-ofm_footer_steps{gap:6px !important;margin-top:.3rem}
.st-key-ofm_footer_steps>div{flex:1 1 0;min-width:0}
[data-testid="stMain"] .st-key-ofm_footer_steps button[data-testid],[data-testid="stMain"] .st-key-pv_steps button[data-testid]{
  width:100%;min-height:2.7rem;background:linear-gradient(180deg,var(--ofm-btn-hi),var(--ofm-btn)) !important;
  border:1px solid #000 !important;border-radius:0 !important;
  box-shadow:inset 1px 1px 0 rgba(255,255,255,.55),inset -1px -1px 0 var(--ofm-btn-lo) !important}
[data-testid="stMain"] .st-key-ofm_footer_steps button[data-testid] p,
[data-testid="stMain"] .st-key-pv_steps button[data-testid] p{color:var(--ofm-btn-text) !important;font-size:1.15rem;
  font-weight:600 !important;text-shadow:1px 1px 0 rgba(0,0,0,.55) !important}
[data-testid="stMain"] .st-key-ofm_footer_steps button:disabled p,
[data-testid="stMain"] .st-key-pv_steps button:disabled p{color:var(--ofm-btn-dim) !important;
  text-shadow:1px 1px 0 rgba(255,255,255,.28) !important}
.st-key-ofm_footer_steps button:disabled,.st-key-pv_steps button:disabled{opacity:1 !important}
.ofm-panel-title.ofm-keepcase{text-transform:none;letter-spacing:.01em}   /* oyuncu / kulup / menajer adi */
.ofm-club{background:var(--ofm-sheet);border:1px solid #000;padding:.45rem .7rem;margin:0 0 .35rem}
.ofm-club .n{font-weight:700;font-size:1.2rem;line-height:1.1;color:var(--ofm-text);overflow-wrap:anywhere}
.ofm-club .l{font-size:.8rem;color:var(--ofm-muted);margin:.15rem 0 .3rem}
.ofm-club .m{display:flex;flex-wrap:wrap;gap:.15rem .8rem;font-size:.8rem;color:var(--ofm-muted)}
.ofm-club .m b{color:var(--ofm-accent);font-weight:700}
.ofm-card{background:var(--ofm-sheet);border:1px solid var(--ofm-border);padding:.55rem .75rem;margin:.2rem 0 .45rem}
.ofm-card .k{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--ofm-muted);font-weight:700}
.ofm-card .v{font-weight:700;font-size:1.25rem;line-height:1.2;color:var(--ofm-text);overflow-wrap:anywhere}
.ofm-card .s{font-size:.86rem;color:var(--ofm-muted);margin-top:.15rem}
.ofm-card .v b{color:var(--ofm-accent)}
.cm-table{width:100%;border-collapse:separate;border-spacing:0 1px;font-size:.9rem;background:var(--ofm-sheet);
  border:1px solid #000;padding:.2rem .4rem}
/* CM'de sutun basliklari ACIK gumus + siyah yazidir (dugmelerin koyu metali degil): kendi tokenlari var. */
.cm-table th{font-weight:400;font-size:.78rem;color:var(--ofm-head-text);padding:.12rem .3rem;text-align:center;
  background:linear-gradient(180deg,var(--ofm-head-hi),var(--ofm-head));border:1px solid var(--ofm-btn-lo)}
.cm-table th.l,.cm-table td.l{text-align:left}
.cm-table th.blank{background:transparent;border:0}
.cm-table td{padding:.1rem .3rem;text-align:center;color:var(--ofm-value);font-variant-numeric:tabular-nums;
  text-shadow:1px 1px 0 var(--ofm-shadow);white-space:nowrap}
.cm-table td.l{color:var(--ofm-text);white-space:normal;overflow-wrap:anywhere}
.cm-table tr.me td{color:var(--ofm-bio)}
.cm-table td.avr{background:var(--ofm-avr);color:var(--ofm-avr-text)}
.cm-table td.zone{box-shadow:inset 3px 0 0 var(--ofm-pos)}
.cm-table-wrap{overflow-x:auto;max-width:100%}
.cm-table th.avr{background:var(--ofm-avr);color:var(--ofm-avr-text)}
.cm-pairs{background:var(--ofm-sheet);border:1px solid #000;padding:.4rem .9rem;max-width:40rem}
.cm-pairs .row{display:flex;justify-content:space-between;gap:1rem;padding:.14rem 0;
  border-bottom:1px solid rgba(0,0,0,.25);color:var(--ofm-text);text-shadow:1px 1px 0 var(--ofm-shadow)}
.cm-pairs .row b{color:var(--ofm-value);text-align:right}
.cm-pairs.st .row b{color:var(--ofm-status)}
.st-key-home_msglist{gap:1px !important}
.st-key-home_msglist button{justify-content:flex-start !important;text-align:left;min-height:1.9rem;
  padding:.15rem .5rem !important}
.st-key-home_msglist button>div{justify-content:flex-start !important}
.st-key-home_msglist button p{text-align:left;font-size:.9rem}
[data-testid="stMain"] .st-key-home_msglist button[data-testid]{background:var(--ofm-sheet) !important;
  border:0 !important;border-bottom:1px solid rgba(0,0,0,.5) !important;border-radius:0 !important;box-shadow:none !important}
[data-testid="stMain"] .st-key-home_msglist button[data-testid] p{color:var(--ofm-text) !important;
  text-shadow:1px 1px 0 var(--ofm-shadow) !important;font-weight:400 !important}
/* 15D-U: okunmamis satir KALIN (markdown <strong>); rengi okunmus satirla ayni kalir */
[data-testid="stMain"] .st-key-home_msglist button[data-testid] p strong{color:var(--ofm-text) !important;
  font-weight:700 !important}
[data-testid="stMain"] .st-key-home_msglist button[data-testid^="stBaseButton-primary"]{background:var(--ofm-tab-sel) !important;
  outline:1px solid var(--ofm-tab-text);outline-offset:-3px}
[data-testid="stMain"] .st-key-home_msglist button[data-testid^="stBaseButton-primary"] p,
[data-testid="stMain"] .st-key-home_msglist button[data-testid^="stBaseButton-primary"] p strong{
  color:var(--ofm-bio) !important}
.st-key-home_msgbody{background:var(--ofm-sheet);border:1px solid #000;padding:.5rem .7rem}
.cm-empty{background:var(--ofm-sheet);border:1px solid #000;padding:.6rem .8rem;color:var(--ofm-text)}
.st-key-home_actions{gap:4px !important}
/* 15D-U: gelen kutusu araclari (okundu / temizle), mesaj eylemleri ve "şuna kadar devam" -- yogun, duz */
.st-key-home_inbox_tools{gap:.45rem !important;align-items:center;margin:.15rem 0 .2rem;flex-wrap:wrap}
.st-key-home_inbox_tools [data-testid="stCaptionContainer"]{flex:1 1 8rem;margin:0}
.st-key-home_inbox_tools button{min-height:1.7rem;padding:.1rem .55rem !important}
.st-key-home_inbox_tools button p{font-size:.82rem}
.st-key-home_msgacts{gap:4px !important;margin-top:.45rem;flex-wrap:wrap}
.st-key-home_msgacts button{min-height:1.8rem}
.st-key-until_row{gap:6px !important;margin-top:.25rem;align-items:center;flex-wrap:wrap;
  justify-content:flex-start !important}
.st-key-until_row>[data-testid="stElementContainer"]{flex:0 0 auto !important;width:auto !important}
.st-key-until_row [data-testid="stSelectbox"]{flex:0 0 auto;width:15rem;max-width:100%}
.st-key-until_row [data-testid="stNumberInput"]{flex:0 0 auto;width:7.5rem}
.st-key-until_row [data-testid="stCheckbox"]{flex:0 0 auto}
.st-key-until_row [data-testid="stCheckbox"] p{font-size:.85rem}
.st-key-until_prog{margin-top:.3rem}
/* 15C-U: yonetim kurulu -- guven cubugu (CM: cubuk + sozcuk, cıplak sayı yok), gerekce satirlari, eylem satiri */
.ofm-conf{background:var(--ofm-sheet);border:1px solid #000;padding:.45rem .7rem;margin:.2rem 0 .45rem;
  max-width:44rem}
.ofm-conf .h{display:flex;justify-content:space-between;gap:1rem;font-size:.78rem;letter-spacing:.05em;
  text-transform:uppercase;font-weight:700;color:var(--ofm-muted)}
.ofm-conf .h b{color:var(--ofm-value);font-size:.95rem;letter-spacing:0;text-transform:none}
.ofm-conf .track{height:12px;border:1px solid #000;background:rgba(127,127,127,.25);overflow:hidden;
  margin-top:.3rem}
.ofm-conf .fill{display:block;height:100%}
.ofm-conf.ok .fill{background:#43a047}.ofm-conf.warn .fill{background:#fbc02d}.ofm-conf.low .fill{background:#e53935}
.ofm-conf .why{margin-top:.35rem;font-size:.84rem;color:var(--ofm-text);line-height:1.45}
.ofm-conf .why span{display:block}
.st-key-bd_acts{gap:.45rem !important;margin:.25rem 0 .1rem;flex-wrap:wrap;align-items:center}
.st-key-bd_acts button{min-height:1.8rem}
.st-key-bd_jobrow{gap:.45rem !important;margin:.2rem 0;flex-wrap:wrap;align-items:center}
.st-key-bd_jobrow [data-testid="stTextInput"]{flex:1 1 12rem;max-width:22rem}
.st-key-bd_jobacts{gap:.45rem !important;margin:.2rem 0;flex-wrap:wrap;align-items:center;
  justify-content:flex-start !important}
.st-key-bd_jobacts>[data-testid="stElementContainer"]{flex:0 0 auto !important;width:auto !important}
.st-key-bd_jobacts [data-testid="stSelectbox"]{flex:0 0 auto;width:16rem;max-width:100%}
.st-key-bd_acts{justify-content:flex-start !important}
.st-key-bd_acts>[data-testid="stElementContainer"]{flex:0 0 auto !important;width:auto !important}
.st-key-home_board{gap:.5rem !important;margin:.1rem 0 .35rem;flex-wrap:wrap;align-items:center}
.st-key-home_board [data-testid="stCaptionContainer"]{flex:1 1 10rem;margin:0}
.st-key-home_board button{min-height:1.8rem}
.st-key-ofm_topnav{display:none !important}
@media (max-width:768px){
  .st-key-ofm_topnav{display:flex !important;background:linear-gradient(180deg,var(--ofm-menu-top),var(--ofm-menu-bot));
    border:1px solid #000;padding:0 !important;gap:0 !important}
  .st-key-ofm_topnav [data-testid="stCaptionContainer"] *{color:var(--ofm-menu-text) !important}
  .st-key-ofm_topnav_row{gap:0 !important;padding:.15rem .3rem;border-bottom:1px solid #000;flex-wrap:nowrap !important}
  .st-key-ofm_topnav_row>div:has(.ofm-topdate){flex:1 1 auto;min-width:0}
  .ofm-topdate{text-align:center;color:var(--ofm-menu-text);font-weight:700;font-size:.85rem;
    text-shadow:1px 1px 0 var(--ofm-shadow)}
  [data-testid="stMain"] .st-key-ofm_topnav_row button[data-testid]{background:transparent !important;border:0 !important;
    box-shadow:none !important;min-height:1.9rem;padding:0 .7rem !important}
  [data-testid="stMain"] .st-key-ofm_topnav_row button[data-testid] p{color:var(--ofm-menu-text) !important;
    font-weight:700;font-size:1rem;text-shadow:1px 1px 0 var(--ofm-shadow) !important}
  .st-key-ofm_topgrid{display:grid !important;grid-template-columns:repeat(4,minmax(0,1fr));gap:0 !important}
  .st-key-ofm_topgrid>div{width:auto !important;min-width:0 !important;max-width:none !important}
  .st-key-ofm_topgrid .st-key-top_menu,.st-key-ofm_topgrid .st-key-top_menu div:not(button *){
    display:contents !important}
  [data-testid="stMain"] .st-key-ofm_topgrid button[data-testid],
  [data-testid="stMain"] .st-key-ofm_topgrid button[data-variant]{width:100%;min-height:2.75rem;height:100%;
    background:transparent !important;border:0 !important;border-radius:0 !important;outline:0 !important;
    border-right:1px solid rgba(0,0,0,.45) !important;border-bottom:1px solid rgba(0,0,0,.45) !important;
    box-shadow:none !important;padding:.2rem .15rem !important;margin:0 !important}
  [data-testid="stMain"] .st-key-ofm_topgrid button[data-testid] p,
  [data-testid="stMain"] .st-key-ofm_topgrid button[data-variant] p{color:var(--ofm-menu-text) !important;
    font-weight:700 !important;font-size:.76rem;line-height:1.12;white-space:normal !important;text-align:center;
    text-shadow:1px 1px 0 var(--ofm-shadow) !important;overflow:visible !important;text-overflow:clip !important}
  [data-testid="stMain"] .st-key-ofm_topgrid button span{white-space:normal !important;overflow:visible !important;
    text-overflow:clip !important;max-width:100% !important}
  .st-key-ofm_topgrid .st-key-top_menu label{display:none !important}
  [data-testid="stMain"] .st-key-ofm_topgrid button[aria-checked="true"]{background:var(--ofm-menu-on) !important;
    box-shadow:inset 0 -3px 0 var(--ofm-menu-text) !important}
  [data-testid="stMain"] .st-key-ofm_topgrid [class*="st-key-top_continue"] button[data-testid],
  [data-testid="stMain"] .st-key-ofm_topgrid [class*="st-key-top_new_season"] button[data-testid],
  [data-testid="stMain"] .st-key-ofm_topgrid [class*="st-key-top_live"] button[data-testid]{
    background:rgba(255,255,255,.12) !important;box-shadow:none !important}
  .ofm-band .t{font-size:1.3rem}
  .ofm-band{min-height:2.6rem}
  .st-key-ofm_tabs>div{flex:1 1 30%}
  [data-testid="stMain"] .st-key-ofm_tabs button[data-testid] p{font-size:.8rem}
  .cm-table{font-size:.8rem}
}
</style>
"""
