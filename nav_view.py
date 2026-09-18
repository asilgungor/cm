"""
nav_view.py
===========
Faz 13I: CM 01/02 tarzi oyun menusu ve sayfa yonlendirme. Kulup secildikten sonra sol kenar cubugu menajer masasi
gibi gruplanmis dikey bir menuye doner; ust sekmeler (st.tabs) yoktur ve her cizimde YALNIZCA secili sayfa cizilir.
SAF SUNUM + oturum durumu: veritabanina yazmaz, oyun kurallarini bilmez (sayfa cizicileri web_app'tadir).

    PAGES / pages_for(...)       sayfa kaydi (slug, etiket, grup) ve oyun moduna / dunyaya gore gorunen sayfalar
    current_page(pages)          secili sayfa: st.session_state["nav_page"], yoksa URL'deki ?sayfa=, yoksa Ana Sayfa;
                                 URL her cizimde guncel tutulur (sayfa yenilense de ayni sayfa acilir)
    menu(pages, current, counts) kenar cubugu menusu: grup basliklari + dugmeler (nav_to_{slug}); secili sayfa birincil
                                 dugme; yanit bekleyen sayilar etikette "(n)"
    top_nav(pages, current, ...) telefon genisligi (<= 768 px) icin sayfanin ustunde ◀ ▶ + menu (nav_top) + Devam;
                                 masaustunde CSS ile gizli, kenar cubugu cekmeceye donunce menuye cekmeceyi acmadan ulasilir
    date_bar(text)               kenar cubugu tepesi (CM): oyun tarihi (sezon / hafta) + geri / ileri oklari
    page_footer(slug, actions)   her sayfanin alti (CM iskeleti): eylem dugmeleri (nav_act_{hedef}) + ◀ Geri / İleri ▶
    goto(slug) / cb_nav / cb_nav_top / cb_nav_step  sayfa degistirme ve ziyaret gecmisi (nav_hist / nav_hist_pos;
                                 callback'ler yalnizca oturum durumu yazar -> requires_auth)
    club_header_html / page_header_html / NAV_CSS   kacisli HTML ve tema tokenlariyla (var(--ofm-*)) stil

Sayfa degisince bir sonraki cizim sayfanin basindan baslar (club_picker_view.SCROLL_TOP_KEY, web_app.main kullanir).
Widget durumu: Streamlit cizilmeyen widget'larin durumunu atar; secili sayfa widget OLMAYAN nav_page anahtarinda
tutulur, bu yuzden sayfalar arasi gecis secimi kaybetmez.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st

from web_common import requires_auth

NAV_KEY = "nav_page"
TOP_KEY = "nav_top"
QUERY_KEY = "sayfa"
BUTTON_PREFIX = "nav_to_"
SCROLL_TOP_KEY = "scroll_top"               # club_picker_view.SCROLL_TOP_KEY ile ayni (web_app.main tuketir)
MENU_KEY = "ofm_nav"                        # kenar cubugu menu kabi (CSS: .st-key-ofm_nav)
TOP_CONTAINER_KEY = "ofm_topnav"
HISTORY_KEY, HISTORY_POS_KEY, HISTORY_MAX = "nav_hist", "nav_hist_pos", 30
DATEBAR_KEY, FOOTER_KEY = "ofm_datebar", "ofm_footer"

HOME, INBOX, NEWS = "ana-sayfa", "mesajlar", "haberler"
SQUAD, TACTICS, MATCH, ACADEMY, STAFF = "kadro", "taktik", "canli-mac", "akademi", "teknik-heyet"
FIXTURES, TABLE, ARENA, NATIONAL = "fikstur", "puan-durumu", "devler-arenasi", "milli-takim"
TRANSFER, CLUB, ADMIN = "transfer", "kulup", "dunya-yonetimi"

GROUP_DESK, GROUP_TEAM, GROUP_COMP, GROUP_CLUB, GROUP_WORLD = "Masa", "Takım", "Müsabakalar", "Kulüp", "Dünya"


@dataclass(frozen=True)
class Page:
    slug: str
    label: str
    group: str
    hint: str = ""


_PAGE_LIST = (
    Page(HOME, "🏠 Ana Sayfa", GROUP_DESK, "Sıradaki maç, son sonuç, gelen kutusu ve devam"),
    Page(INBOX, "📨 Teklifler & Mesajlar", GROUP_DESK, "Menajerler arası teklifler, mesajlar, bildirimler"),
    Page(NEWS, "📰 Haberler & Tarih", GROUP_DESK, "Haber akışı, onur listesi, transfer kayıtları"),
    Page(SQUAD, "📋 Kadro", GROUP_TEAM, "Taktik tahtası, ilk 11, oyuncu memnuniyeti"),
    Page(TACTICS, "🎯 Taktik", GROUP_TEAM, "Maç önü, talimatlar, maç planı, hazırlık maçı"),
    Page(MATCH, "🏟️ Canlı Maç", GROUP_TEAM, "Maçını canlı yönet ya da izle"),
    Page(ACADEMY, "🎓 Akademi", GROUP_TEAM, "U-21 akademisi"),
    Page(STAFF, "👥 Teknik Heyet", GROUP_TEAM, "Personel, etkiler, işe alma"),
    Page(FIXTURES, "📅 Fikstür & Sonuçlar", GROUP_COMP, "Fikstürün, haftanın sonuçları, hafta raporu"),
    Page(TABLE, "🏆 Puan Durumu", GROUP_COMP, "Puan durumu ve gol krallığı"),
    Page(ARENA, "⭐ Devler Arenası", GROUP_COMP, "Kura, turnuva ağacı, gruplar"),
    Page(NATIONAL, "🌍 Milli Takım", GROUP_COMP, "Milli takım görevi ve Dünya Kupası"),
    Page(TRANSFER, "🔄 Transfer Merkezi", GROUP_CLUB, "Oyuncu arama, teklif dosyaları, gelen teklifler, ödemeler"),
    Page(CLUB, "🏛️ Kulüp & Finans", GROUP_CLUB, "Bütçe, maaşlar, tesisler, sponsorluk"),
    Page(ADMIN, "🛡️ Dünya Yönetimi", GROUP_WORLD, "Dünyanın sahibi / yöneticisi"),
)
PAGES: dict[str, Page] = {p.slug: p for p in _PAGE_LIST}
CAREER_PAGES = (HOME, NEWS, SQUAD, TACTICS, MATCH, ACADEMY, STAFF, FIXTURES, TABLE, ARENA, TRANSFER, CLUB)
TOURNAMENT_PAGES = (HOME, SQUAD, MATCH, STAFF, ARENA)
WORLD_PAGES = (INBOX, NATIONAL, ADMIN)                    # yalnizca paylasilan / milli takimli dunyada
ADMIN_ROLES = ("OWNER", "ADMIN")
# Eski sekme adlari ve kisaltmalar (?sayfa=lig gibi elle yazilan baglantilar)
ALIASES = {"lig": TABLE, "finans": CLUB, "tesisler": CLUB, "pazar": TRANSFER, "transfer-merkezi": TRANSFER,
           "mac": MATCH, "canli": MATCH, "heyet": STAFF, "arena": ARENA, "milli": NATIONAL, "haber": NEWS,
           "teklifler": INBOX, "yonetim": ADMIN, "fikstur-sonuclar": FIXTURES, "ana": HOME}


def pages_for(*, tournament: bool, shared: bool, internationals: bool, role: str | None) -> list[str]:
    """Gorunen sayfalar (menu sirasiyla). Dunya sayfalari: Teklifler & Mesajlar paylasilan dunyada, Milli Takim milli
    takimlar aciksa, Dunya Yonetimi paylasilan dunyada sahip / yoneticide."""
    wanted = set(TOURNAMENT_PAGES if tournament else CAREER_PAGES)
    if shared:
        wanted.add(INBOX)
    if internationals:
        wanted.add(NATIONAL)
    if shared and role in ADMIN_ROLES:
        wanted.add(ADMIN)
    return [p.slug for p in _PAGE_LIST if p.slug in wanted]


def resolve(value) -> str | None:
    """Slug, takma ad ya da etiket ("Kadro", "📋 Kadro") -> slug; bilinmeyen -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if text in PAGES:
        return text
    if text.casefold() in ALIASES:
        return ALIASES[text.casefold()]
    folded = text.casefold()
    for page in _PAGE_LIST:
        bare = page.label.split(" ", 1)[1] if " " in page.label else page.label
        if folded in (page.label.casefold(), bare.casefold()):
            return page.slug
    return None


def current_page(pages: Sequence[str]) -> str:
    """Secili sayfa: oturum -> URL (?sayfa=) -> Ana Sayfa. URL guncel tutulur (yenileme ayni sayfayi acar)."""
    ss = st.session_state
    page = ss.get(NAV_KEY)
    if page not in pages:
        wanted = resolve(st.query_params.get(QUERY_KEY))
        page = wanted if wanted in pages else (HOME if HOME in pages else pages[0])
        ss[NAV_KEY] = page
    if st.query_params.get(QUERY_KEY) != page:
        st.query_params[QUERY_KEY] = page
    history, pos = _history()
    if not history or history[pos] != page:          # ilk cizim ya da URL / callback'le gelinen sayfa
        _push(page)
    return page


def _history() -> tuple[list[str], int]:
    ss = st.session_state
    history = [s for s in (ss.get(HISTORY_KEY) or []) if s in PAGES]
    pos = ss.get(HISTORY_POS_KEY)
    pos = pos if isinstance(pos, int) and 0 <= pos < len(history) else len(history) - 1
    return history, max(pos, 0)


def _push(slug: str) -> None:
    history, pos = _history()
    if history and history[pos] == slug:
        return
    history = (history[: pos + 1] + [slug])[-HISTORY_MAX:]
    st.session_state[HISTORY_KEY] = history
    st.session_state[HISTORY_POS_KEY] = len(history) - 1


def can_step(delta: int) -> bool:
    history, pos = _history()
    return bool(history) and 0 <= pos + int(delta) < len(history)


def goto(slug: str, *, scroll: bool = True) -> None:
    """Callback'lerden sayfa degistirme (bilinmeyen slug yok sayilir). scroll: yeni sayfa en ustten baslar."""
    if slug in PAGES:
        st.session_state[NAV_KEY] = slug
        _push(slug)
        if scroll:
            st.session_state[SCROLL_TOP_KEY] = True


@requires_auth
def cb_nav(slug: str) -> None:
    """Menu dugmesi: yalnizca oturum durumu (veritabani yok)."""
    goto(slug)


@requires_auth
def cb_nav_top() -> None:
    """Telefon ust menusu (selectbox) degisti."""
    goto(str(st.session_state.get(TOP_KEY) or ""))


@requires_auth
def cb_nav_step(delta: int) -> None:
    """Geri / Ileri (CM'deki oklar): ziyaret edilen sayfalar arasinda gecis; gecmise yeni kayit eklemez."""
    history, pos = _history()
    target = pos + int(delta)
    if 0 <= target < len(history):
        st.session_state[HISTORY_POS_KEY] = target
        st.session_state[NAV_KEY] = history[target]
        st.session_state[SCROLL_TOP_KEY] = True


def step_buttons(prefix: str, *, labels: tuple[str, str] = ("◀", "▶")) -> None:
    """Geri / Ileri dugmeleri ({prefix}_back / {prefix}_fwd); cagiranin kabinda (yatay kap) cizilir."""
    st.button(labels[0], key=f"{prefix}_back", on_click=cb_nav_step, args=(-1,), disabled=not can_step(-1),
              help="Önceki sayfa")
    st.button(labels[1], key=f"{prefix}_fwd", on_click=cb_nav_step, args=(1,), disabled=not can_step(1),
              help="Sonraki sayfa")


def date_bar(date_text: str) -> None:
    """Kenar cubugunun tepesi (CM): oyun tarihi + geri / ileri oklari."""
    with st.container(horizontal=True, vertical_alignment="center", key=DATEBAR_KEY):
        st.markdown(f'<div class="ofm-date">{escape(date_text)}</div>', unsafe_allow_html=True)
        step_buttons("nav")


def page_footer(slug: str, actions: Sequence[tuple[str, str]] = ()) -> None:
    """
    Her sayfanin alti (CM iskeleti): eylem dugmeleri satiri (baska sayfalara kisayol: nav_act_{hedef}) ve
    ◀ Geri / İleri ▶ (ziyaret gecmisi: nav_foot_back / nav_foot_fwd).
    """
    with st.container(horizontal=True, horizontal_alignment="distribute", key=FOOTER_KEY):
        with st.container(horizontal=True, width="content", key="ofm_footer_actions"):
            for label_text, target in actions:
                if target in PAGES and target != slug:
                    st.button(label_text, key=f"nav_act_{target}", on_click=cb_nav, args=(target,))
        with st.container(horizontal=True, width="content", key="ofm_footer_steps"):
            step_buttons("nav_foot", labels=("◀ Geri", "İleri ▶"))


def label(slug: str, counts: Mapping[str, int] | None = None) -> str:
    page = PAGES[slug]
    count = int((counts or {}).get(slug) or 0)
    return f"{page.label} ({count})" if count > 0 else page.label


def button_key(slug: str) -> str:
    return f"{BUTTON_PREFIX}{slug}"


def menu(pages: Sequence[str], current: str, counts: Mapping[str, int] | None = None) -> None:
    """Kenar cubugu menusu (st.sidebar icinde cagrilir): grup basliklari ve sayfa dugmeleri."""
    with st.container(key=MENU_KEY):
        group = None
        for slug in pages:
            page = PAGES[slug]
            if page.group != group:
                group = page.group
                st.markdown(f'<div class="ofm-nav-group">{escape(group)}</div>', unsafe_allow_html=True)
            st.button(label(slug, counts), key=button_key(slug), on_click=cb_nav, args=(slug,),
                      type="primary" if slug == current else "secondary", width="stretch",
                      help=page.hint or None)


def top_nav(pages: Sequence[str], current: str, counts: Mapping[str, int] | None = None,
            continue_action: Callable[[str], None] | None = None, date_text: str = "") -> None:
    """
    Telefon genisligi (<= 768 px): ana alanin ustunde tarih + ◀ ▶ + menu secici + Devam (masaustunde CSS ile gizli;
    kenar cubugu cekmeceye donunce menuye cekmeceyi acmadan ulasilir). Secici her cizimde secili sayfaya esitlenir.
    """
    ss = st.session_state
    if ss.get(TOP_KEY) != current:
        ss[TOP_KEY] = current
    with st.container(key=TOP_CONTAINER_KEY):
        with st.container(horizontal=True, vertical_alignment="center", key="ofm_topnav_row"):
            step_buttons("top")
            st.selectbox("Menü", list(pages), key=TOP_KEY, format_func=lambda s: label(s, counts),
                         on_change=cb_nav_top, label_visibility="collapsed")
        if date_text:
            st.caption(date_text)
        if continue_action is not None:
            continue_action("top")


def page_header_html(slug: str) -> str:
    page = PAGES[slug]
    return (f'<div class="ofm-page"><span class="g">{escape(page.group)}</span>'
            f'<span class="t">{escape(page.label)}</span></div>')


def name_title_html(text: str) -> str:
    """Panel basligi, ad icerdigi icin buyuk harfe cevrilmeden (tr buyuk harf kurali "Lions"u "LİONS" yapar)."""
    return f'<div class="ofm-panel-title ofm-keepcase">{escape(text)}</div>'


def club_header_html(name: str, subtitle: str, chips: Sequence[tuple[str, str]] = ()) -> str:
    """Kenar cubugu kulup basligi (arma yok): ad, lig / durum satiri, butce ciplari. Tum metinler kacisli."""
    items = "".join(f"<span>{escape(str(k))} <b>{escape(str(v))}</b></span>" for k, v in chips)
    return (f'<div class="ofm-club"><div class="n">{escape(name)}</div><div class="l">{escape(subtitle)}</div>'
            + (f'<div class="m">{items}</div>' if items else "") + "</div>")


NAV_CSS = """
<style>
.ofm-club{background:var(--ofm-panel-alt);border:1px solid var(--ofm-border);border-left:5px solid var(--ofm-accent);
  border-radius:12px;padding:.55rem .75rem .6rem;margin:0 0 .35rem}
.ofm-club .n{font-family:"Barlow Condensed","Arial Narrow","Segoe UI",sans-serif;font-weight:800;font-size:1.4rem;
  line-height:1.05;color:var(--ofm-text);letter-spacing:.01em;overflow-wrap:anywhere}
.ofm-club .l{font-size:.8rem;color:var(--ofm-muted);margin:.15rem 0 .3rem}
.ofm-club .m{display:flex;flex-wrap:wrap;gap:.15rem .8rem;font-size:.8rem;color:var(--ofm-muted)}
.ofm-club .m b{color:var(--ofm-accent);font-weight:800}
.ofm-nav-group{font-size:.68rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--ofm-muted);
  margin:.45rem 0 0 .2rem}
[data-testid="stSidebar"] .ofm-nav-group{color:var(--ofm-muted)}
.st-key-ofm_nav{gap:.1rem}
.st-key-ofm_nav [data-testid="stElementContainer"]{margin:0}
.st-key-ofm_nav [data-testid="stMarkdownContainer"]{margin-bottom:0 !important}
.st-key-ofm_nav button{justify-content:flex-start !important;text-align:left;min-height:2.05rem;
  padding:.2rem .65rem !important}
.st-key-ofm_nav button>div{justify-content:flex-start !important}
.st-key-ofm_nav button p{font-size:.95rem;text-align:left}
.st-key-ofm_nav [data-testid^="stBaseButton-secondary"]{background:transparent !important;
  border-color:transparent !important;font-weight:600}
.st-key-ofm_nav [data-testid^="stBaseButton-secondary"]:hover{background:var(--ofm-panel-alt) !important;
  border-color:var(--ofm-border) !important}
.st-key-ofm_nav [data-testid^="stBaseButton-primary"]{box-shadow:inset 5px 0 0 var(--ofm-accent) !important}
.ofm-page{display:flex;flex-wrap:wrap;align-items:baseline;gap:.2rem .8rem;background:var(--ofm-panel-alt);
  border:1px solid var(--ofm-border);border-left:6px solid var(--ofm-accent);border-radius:10px;
  margin:.1rem 0 .7rem;padding:.4rem .9rem}
.ofm-page .g{font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;color:var(--ofm-muted);font-weight:700}
.ofm-page .t{font-family:"Barlow Condensed","Arial Narrow","Segoe UI",sans-serif;font-weight:800;font-size:1.6rem;
  line-height:1.1;color:var(--ofm-text);text-transform:uppercase;letter-spacing:.02em}
.ofm-date{font-family:"Barlow Condensed","Arial Narrow","Segoe UI",sans-serif;font-weight:800;font-size:1.05rem;
  color:var(--ofm-accent);letter-spacing:.03em;flex:1 1 auto}
[data-testid="stSidebar"] .ofm-date{color:var(--ofm-accent)}
.st-key-ofm_datebar{margin-bottom:.2rem}
.st-key-ofm_datebar button,.st-key-ofm_topnav_row button{min-height:2rem;padding:.1rem .6rem !important}
.st-key-nav_continue button,.st-key-nav_new_season button,.st-key-nav_live button,.st-key-top_continue button,
.st-key-top_new_season button,.st-key-home_continue button,.st-key-home_new_season button{min-height:2.9rem;
  font-size:1.08rem !important;letter-spacing:.02em}
.st-key-ofm_footer{border-top:2px solid var(--ofm-border);margin-top:1.1rem;padding-top:.6rem}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"]{background:var(--ofm-panel-alt) !important;
  border-color:var(--ofm-border) !important}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"] p{color:var(--ofm-text) !important;
  font-weight:600}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"]{
  background:var(--ofm-primary) !important;border-color:var(--ofm-primary) !important}
[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"] p{
  color:var(--ofm-primary-text) !important}
.st-key-home_msglist{gap:.2rem}
.st-key-home_msglist button{justify-content:flex-start !important;text-align:left;min-height:2rem;
  padding:.2rem .6rem !important}
.st-key-home_msglist button>div{justify-content:flex-start !important}
.st-key-home_msglist button p{text-align:left;font-size:.9rem}
.st-key-ofm_topnav{display:none !important}
@media (max-width:768px){
  .st-key-ofm_topnav{display:flex !important;background:var(--ofm-panel);border:1px solid var(--ofm-border);
    border-radius:12px;padding:.45rem .55rem}
  .ofm-page .t{font-size:1.3rem}
}
.ofm-panel-title.ofm-keepcase{text-transform:none;letter-spacing:.01em}   /* oyuncu / kulup / menajer adi */
.ofm-card{background:var(--ofm-panel);border:1px solid var(--ofm-border);border-radius:12px;padding:.7rem .9rem;
  margin:.2rem 0 .5rem}
.ofm-card .k{font-size:.72rem;letter-spacing:.1em;text-transform:uppercase;color:var(--ofm-muted);font-weight:700}
.ofm-card .v{font-family:"Barlow Condensed","Arial Narrow","Segoe UI",sans-serif;font-weight:800;font-size:1.45rem;
  line-height:1.15;color:var(--ofm-text);overflow-wrap:anywhere}
.ofm-card .s{font-size:.88rem;color:var(--ofm-muted);margin-top:.15rem}
.ofm-card .v b{color:var(--ofm-accent)}
</style>
"""
