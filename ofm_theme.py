"""
ofm_theme.py
============
OFM (Online Football Manager) gorsel temalari. SAF SUNUM: yalnizca CSS/HTML metni uretir;
Streamlit, veritabani ya da oyun kurallarini BILMEZ.

Iki tema (menajer secer; web_app st.session_state["theme"] + ?theme= URL parametresinde tutar):
    dark   ⚽ OFM Dark   gece mavisi zemin, lacivert paneller, beyaz metin, altin sarisi vurgu, mor dugmeler
    light  ☀️ OFM Light  acik gri zemin, beyaz golgeli paneller, antrasit metin, yesil dugmeler, mavi vurgu

    theme_css(theme, login=False)   sayfaya basilan TEK <style> blogu (bos satir yok: Streamlit
                                     markdown'i HTML blogunu bolmesin). login=True giris sayfasinin
                                     mor degrade zeminini, alt cizgili alanlarini ve egik dugmesini ekler.
    theme_sync_script(theme, ...)   Streamlit'in KENDI temasini uygulama secimine sabitler (asagiya bak)
    streamlit_theme_options(theme)  .streamlit/config.toml [theme.dark] / [theme.light] degerleri
    login_hero_html(theme)          giris sayfasi sol gorseli: egik ucgenler + soyut top (fotograf YOK)
    brand_html() / login_headline_html(view)
    panel_title_html() / stat_strip_html()
    contrast_ratio() / contrast_pairs(theme)   WCAG kontrast orani ve denetlenen tum token ciftleri

TEMANIN IKI KATMANI (Faz 13 duzeltmesi)
---------------------------------------
1) Streamlit'in kendi temasi: widget iclerini (acilir liste, kaydirici, onay kutusu) ve canvas ile
   cizilen st.dataframe / st.data_editor tablolarini Streamlit boyar; CSS ile boyanamazlar. Streamlit
   config.toml'da [theme.light] ve [theme.dark] varsa hangisini kullanacagini TARAYICININ
   prefers-color-scheme degerine gore secer -- uygulama icindeki secime BAKMAZ. Isletim sistemi acik,
   uygulama OFM Dark ise widget'lar beyaz kalir, uzerlerindeki yazi ise CSS ile beyaza zorlanir: okunmaz.
   Cozum: theme_sync_script() Streamlit'in temayi sakladigi localStorage anahtarini
   ("stActiveTheme-<yol>-v2" -> "Dark" / "Light") uygulamanin secimine yazar; boylece Streamlit de
   ayni paleti kullanir. Deger degistiginde sayfanin bir kez yenilenmesi gerekir (Streamlit temayi
   acilista okur); yenileme oturumu dusurecegi icin YALNIZCA oturum yokken (giris ekrani) yapilir.
2) OFM kabugu: paneller, basliklar, sekmeler, dugmeler ve giris sayfasi theme_css() ile boyanir.
   Secicilerin Streamlit 1.64 ile uyumlu olmasi sarttir: 1.64 artik BaseWeb kullanmaz (data-baseweb
   secicileri BOSA DUSER), widget'lar data-testid / ARIA rolleri ile hedeflenir.

GUVENLIK: HTML'e giden her metin kacirilir; theme_sync_script yalnizca sabit iki degerden birini yazar.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

THEME_DARK, THEME_LIGHT = "dark", "light"
DEFAULT_THEME = THEME_DARK
THEME_LABELS: dict[str, str] = {THEME_DARK: "⚽ OFM Dark", THEME_LIGHT: "☀️ OFM Light"}
LEGACY_THEME_LABELS: dict[str, str] = {"⚽ FM Dark": THEME_DARK, "☀️ FM Light": THEME_LIGHT}   # eski oturum / baglanti
APP_NAME = "Online Football Manager"
APP_SHORT = "OFM"
BRAND_TITLE = f"{APP_NAME} ({APP_SHORT})"           # resmi ad: sekme basligi ve sayfa basligi
FONT_STACK = '"Barlow", "Segoe UI", "Helvetica Neue", Arial, sans-serif'
CONDENSED_STACK = '"Barlow Condensed", "Arial Narrow", "Segoe UI", sans-serif'


@dataclass(frozen=True)
class Palette:
    bg: str
    bg_alt: str
    panel: str
    panel_alt: str
    border: str
    text: str
    muted: str
    accent: str              # vurgu (baslik, secili sekme, onemli sayi)
    primary: str             # birincil dugme zemini
    primary_hover: str
    primary_text: str
    input_bg: str
    hero_from: str           # giris sayfasi zemini
    hero_via: str            # giris karti
    hero_to: str             # adim kartlari
    tri_a: str               # taktik tahtasi: cim
    tri_b: str               # taktik tahtasi: cim seridi
    tri_c: str               # taktik tahtasi: kosu oklari
    login_text: str
    login_button: str
    login_button_text: str


PALETTES: dict[str, Palette] = {
    THEME_DARK: Palette(
        bg="#121824", bg_alt="#1a1f2c", panel="#1e2538", panel_alt="#242b3d", border="#323b55",
        text="#ffffff", muted="#aeb6c8", accent="#FFCD00",
        primary="#635BFF", primary_hover="#4f46e5", primary_text="#ffffff", input_bg="#161c2b",
        hero_from="#121824", hero_via="#1e2538", hero_to="#1a1f2c",
        tri_a="#1d6f47", tri_b="#227a4f", tri_c="#FFCD00",
        login_text="#ffffff", login_button="#FFCD00", login_button_text="#121824",
    ),
    THEME_LIGHT: Palette(
        bg="#f4f6f9", bg_alt="#eef2f6", panel="#ffffff", panel_alt="#f8fafc", border="#dbe2ea",
        text="#1e293b", muted="#5b6b82", accent="#0369a1",
        primary="#10b981", primary_hover="#059669", primary_text="#0b1220", input_bg="#ffffff",   # vurgu: koyu mavi (beyaz panelde 6:1)
        hero_from="#f4f6f9", hero_via="#ffffff", hero_to="#ffffff",
        tri_a="#1f7a4d", tri_b="#23865a", tri_c="#fde047",
        login_text="#1e293b", login_button="#10b981", login_button_text="#0b1220",
    ),
}


# Streamlit'in kendi tema anahtari: localStorage["stActiveTheme-<pathname>-v2"] = '"Dark"' | '"Light"' | '"System"'
STREAMLIT_THEME_NAMES: dict[str, str] = {THEME_DARK: "Dark", THEME_LIGHT: "Light"}
THEME_STORAGE_KEY = "stActiveTheme"
THEME_STORAGE_VERSION = 2


def streamlit_theme_options(theme: str) -> dict[str, object]:
    """
    .streamlit/config.toml icindeki [theme.dark] / [theme.light] (ve .sidebar) degerleri.
    Streamlit widget iclerini ve canvas tablolarini bu paletle cizer; OFM paletiyle BIREBIR ayni olmali
    (test_ofm_theme dosyayi bu sozlukle karsilastirir). Belirtilmeyen ayarlar Streamlit'in kendi acik /
    koyu varsayilanlarindan gelir (uyari, hata, bilgi kutularinin renkleri gibi).
    """
    p = PALETTES[normalize_theme(theme)]
    sidebar_input = p.input_bg if p.input_bg.lower() != p.panel.lower() else p.bg_alt
    return {
        "primaryColor": p.primary,
        "backgroundColor": p.bg,
        "secondaryBackgroundColor": p.panel,
        "textColor": p.text,
        "borderColor": p.border,
        "linkColor": p.accent,
        "codeBackgroundColor": p.bg_alt,
        "dataframeHeaderBackgroundColor": p.panel_alt,
        "dataframeBorderColor": p.border,
        "showWidgetBorder": True,
        "sidebar": {
            "primaryColor": p.primary,
            "backgroundColor": p.panel,
            "secondaryBackgroundColor": sidebar_input,
            "textColor": p.text,
            "borderColor": p.border,
            "linkColor": p.accent,
        },
    }


def theme_sync_script(theme: str, reload: bool = False) -> str:
    """
    Streamlit'in kendi temasini (widget icleri + canvas tablolar) uygulamanin secimine sabitler.

    localStorage'a "Dark" / "Light" yazar. reload=True ise deger degistiginde sayfayi BIR KEZ yeniler
    (Streamlit temayi yalnizca acilista okur). Yenileme oturumu dusurdugu icin cagiran taraf bunu
    yalnizca oturum yokken (giris ekrani) ister. sessionStorage'daki nobet degeri, localStorage yazilamayan
    tarayicida (gizli pencere) sonsuz yenileme dongusunu engeller. st.html(..., unsafe_allow_javascript=True)
    ile basilir.
    """
    want = STREAMLIT_THEME_NAMES[normalize_theme(theme)]
    do_reload = "true" if reload else "false"
    return (
        "<script>(function(){try{"
        f'var want="{want}";'
        f'var key="{THEME_STORAGE_KEY}-"+window.location.pathname+"-v{THEME_STORAGE_VERSION}";'
        'var cur=null;try{cur=JSON.parse(window.localStorage.getItem(key));}catch(e){}'
        'if(cur===want){return;}'
        'window.localStorage.setItem(key,JSON.stringify(want));'
        f'if(!{do_reload}){{return;}}'
        'var mark="ofmThemeReload";'
        'if(window.sessionStorage.getItem(mark)===want){return;}'
        'window.sessionStorage.setItem(mark,want);'
        'window.location.reload();'
        "}catch(e){}})()</script>"
    )


def normalize_theme(value: object) -> str:
    """'light' / 'dark' ya da etiket ('☀️ OFM Light'; eski '☀️ FM Light' de); bilinmeyen -> varsayilan (koyu)."""
    if value in PALETTES:
        return str(value)
    for key, label in THEME_LABELS.items():
        if value == label:
            return key
    if isinstance(value, str) and value in LEGACY_THEME_LABELS:
        return LEGACY_THEME_LABELS[value]
    return DEFAULT_THEME


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2 kontrast orani (1-21). Normal metin icin >= 4.5, buyuk metin/ikon icin >= 3."""
    a, b = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (a + 0.05) / (b + 0.05)


AA_TEXT, AA_LARGE = 4.5, 3.0          # WCAG 2.1 AA: normal metin 4.5:1, buyuk metin / arayuz ogesi 3:1


def contrast_pairs(theme: str) -> tuple[tuple[str, str, str, float], ...]:
    """
    Ekranda gercekten yan yana cizilen (on plan, arka plan) token ciftleri:
    (aciklama, on plan, arka plan, gereken en dusuk kontrast). tests/test_ofm_theme.py hepsini dener;
    yeni bir renk cifti kullanmaya baslayan herkes buraya eklemeli -- okunmayan bir cift testi dusurur.
    """
    p = PALETTES[normalize_theme(theme)]
    pairs: list[tuple[str, str, str, float]] = [
        ("gövde metni / zemin", p.text, p.bg, AA_TEXT),
        ("gövde metni / panel", p.text, p.panel, AA_TEXT),
        ("gövde metni / ikincil panel", p.text, p.panel_alt, AA_TEXT),
        ("gövde metni / üst şerit", p.text, p.bg_alt, AA_TEXT),
        ("giriş alanı metni / giriş alanı", p.text, p.input_bg, AA_TEXT),
        ("soluk metin / panel", p.muted, p.panel, AA_TEXT),
        ("soluk metin / zemin", p.muted, p.bg, AA_TEXT),
        ("soluk metin / ikincil panel", p.muted, p.panel_alt, AA_TEXT),
        ("soluk metin / üst şerit", p.muted, p.bg_alt, AA_TEXT),
        ("yer tutucu / giriş alanı", p.muted, p.input_bg, AA_TEXT),
        ("vurgu / panel", p.accent, p.panel, AA_TEXT),            # panel basligi: normal boy, kalin
        ("vurgu / zemin", p.accent, p.bg, AA_TEXT),
        ("vurgu / ikincil panel", p.accent, p.panel_alt, AA_TEXT),
        ("vurgu / giriş alanı", p.accent, p.input_bg, AA_TEXT),   # bag (linkColor) giris alani icinde de cikar
        ("düğme yazısı / birincil düğme", p.primary_text, p.primary, AA_TEXT),
        ("düğme yazısı / birincil düğme (üzerine gelince)", p.primary_text, p.primary_hover, AA_TEXT),
        ("giriş düğmesi yazısı / giriş düğmesi", p.login_button_text, p.login_button, AA_TEXT),
        ("soluk metin / giriş kartı", p.muted, p.hero_via, AA_TEXT),
        ("bilgi şeridi değeri / panel", p.accent, p.panel, AA_LARGE),
    ]
    pairs += [(f"giriş sayfası metni / {name}", p.login_text, hero, AA_TEXT)
              for name, hero in (("zemin", p.hero_from), ("kart", p.hero_via), ("adım kartı", p.hero_to))]
    return tuple(pairs)


def _one_line(css: str) -> str:
    return "\n".join(line for line in css.splitlines() if line.strip())


# Sayfa dili: CSS text-transform:uppercase dile gore calisir. Streamlit <html lang="en"> verir; o zaman
# "Giriş" -> "GIRIŞ", "Tesisleri" -> "TESISLERI" olur. Turkce kurallar (i -> İ) icin belge dili tr yapilir.
HTML_LANG = "tr"
LANG_SCRIPT = f"<script>document.documentElement.setAttribute('lang', '{HTML_LANG}')</script>"


def theme_css(theme: str, login: bool = False) -> str:
    p = PALETTES[normalize_theme(theme)]
    css = f"""
<style>
@import url("https://fonts.googleapis.com/css2?family=Barlow:wght@400;600;700&family=Barlow+Condensed:ital,wght@0,600;0,800;1,700;1,800&display=swap");
:root{{--ofm-bg:{p.bg};--ofm-bg-alt:{p.bg_alt};--ofm-panel:{p.panel};--ofm-panel-alt:{p.panel_alt};--ofm-border:{p.border};
  --ofm-text:{p.text};--ofm-muted:{p.muted};--ofm-accent:{p.accent};--ofm-primary:{p.primary};
  --ofm-primary-hover:{p.primary_hover};--ofm-primary-text:{p.primary_text};--ofm-input:{p.input_bg}}}
html,body,[data-testid="stAppViewContainer"],[data-testid="stSidebar"],button,input,textarea,select{{
  font-family:{FONT_STACK} !important}}
[data-testid="stAppViewContainer"],[data-testid="stMain"]{{background:var(--ofm-bg);color:var(--ofm-text)}}
[data-testid="stHeader"],[data-testid="stToolbar"]{{background:var(--ofm-bg-alt);border-bottom:1px solid var(--ofm-border)}}
[data-testid="stToolbar"] *,[data-testid="stMainMenuButton"],[data-testid="stAppDeployButton"] *{{
  color:var(--ofm-muted) !important}}
[data-testid="stSidebar"],[data-testid="stSidebarContent"]{{background:var(--ofm-panel);
  border-right:1px solid var(--ofm-border)}}
[data-testid="stSidebar"] *{{color:var(--ofm-text)}}
[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{{color:var(--ofm-accent) !important;text-transform:uppercase;
  letter-spacing:.06em;font-family:{CONDENSED_STACK} !important}}
[data-testid="stMarkdownContainer"],[data-testid="stCaptionContainer"],label,p,li{{color:var(--ofm-text)}}
[data-testid="stCaptionContainer"]{{color:var(--ofm-muted) !important}}
h1{{color:var(--ofm-accent) !important;font-family:{CONDENSED_STACK} !important;font-style:italic;font-weight:800;
  text-transform:uppercase;letter-spacing:.02em}}
h2,h3,h4{{color:var(--ofm-text) !important;font-family:{CONDENSED_STACK} !important}}
h4{{border-left:4px solid var(--ofm-accent);padding:.15rem .6rem;text-transform:uppercase;letter-spacing:.04em}}
[data-testid="stTabs"] [role="tablist"]{{gap:4px;background:var(--ofm-panel);padding:4px;border-radius:12px;
  border:1px solid var(--ofm-border)}}
[data-testid="stTab"]{{background:transparent;color:var(--ofm-muted);border-radius:9px;padding:.4rem .85rem;
  font-weight:700}}
[data-testid="stTab"] p{{color:var(--ofm-muted)}}
[data-testid="stTab"][aria-selected="true"],[data-testid="stTabs"] [role="tab"][aria-selected="true"]{{
  background:var(--ofm-primary) !important;color:var(--ofm-primary-text) !important}}
[data-testid="stTab"][aria-selected="true"] *{{color:var(--ofm-primary-text) !important}}
[data-testid="stTabs"] [role="tablist"]::after,[data-testid="stTabs"] [role="tablist"]>div:last-child:empty{{display:none}}
[data-testid^="stBaseButton-primary"]{{background:var(--ofm-primary) !important;border:1px solid var(--ofm-primary) !important;
  color:var(--ofm-primary-text) !important;font-weight:700;border-radius:10px !important}}
[data-testid^="stBaseButton-primary"] *{{color:var(--ofm-primary-text) !important}}
[data-testid^="stBaseButton-primary"]:hover{{background:var(--ofm-primary-hover) !important;
  border-color:var(--ofm-primary-hover) !important}}
[data-testid^="stBaseButton-secondary"],[data-testid="stBaseLinkButton-secondary"],[data-testid="stPopoverButton"],
[data-testid="stBaseButton-elementToolbar"]{{background:var(--ofm-panel-alt) !important;color:var(--ofm-text) !important;
  border:1px solid var(--ofm-border) !important;border-radius:10px !important;font-weight:600}}
[data-testid^="stBaseButton-secondary"] *,[data-testid="stBaseLinkButton-secondary"] *,
[data-testid="stPopoverButton"] *{{color:var(--ofm-text)}}
[data-testid^="stBaseButton-secondary"]:hover,[data-testid="stPopoverButton"]:hover{{
  border-color:var(--ofm-accent) !important;color:var(--ofm-accent) !important}}
[data-testid="stBaseButton-tertiary"]{{background:transparent !important;border:0 !important}}
[data-testid="stBaseButton-tertiary"] *{{color:var(--ofm-accent) !important}}
button:disabled,button[disabled]{{opacity:.45 !important;cursor:not-allowed}}
[data-testid="stTextInputRootElement"],[data-testid="stTextAreaRootElement"],[data-testid="stNumberInputContainer"],
[data-testid="stSelectbox"] div[role="group"],[data-testid="stMultiSelect"] div[role="group"],
[data-testid="stDateInput"] div[role="group"],[data-testid="stTimeInput"] div[role="group"],
[data-testid="stChatInput"]{{background:var(--ofm-input) !important;border:1px solid var(--ofm-border) !important;
  border-radius:10px !important}}
[data-testid="stTextInputField"],[data-testid="stNumberInputField"],[data-testid="stDateInputField"],
[data-testid="stTimeInputTimeDisplay"],[data-testid="stTextArea"] textarea,[data-testid="stSelectbox"] input,
[data-testid="stMultiSelect"] input,[data-testid="stDateInput"] input,[data-testid="stChatInput"] textarea{{
  background:transparent !important;color:var(--ofm-text) !important;-webkit-text-fill-color:var(--ofm-text)}}
[data-testid="stTextInputField"]::placeholder,[data-testid="stTextArea"] textarea::placeholder,
[data-testid="stSelectbox"] input::placeholder,[data-testid="stMultiSelect"] input::placeholder,
[data-testid="stNumberInputField"]::placeholder{{color:var(--ofm-muted) !important;opacity:1}}
input:-webkit-autofill,input:-webkit-autofill:focus{{-webkit-text-fill-color:var(--ofm-text) !important;
  caret-color:var(--ofm-text);-webkit-box-shadow:0 0 0 1000px var(--ofm-input) inset !important;
  transition:background-color 9999s}}
[data-testid="stMultiSelectTagsContainer"]>div{{background:var(--ofm-panel-alt) !important;
  border:1px solid var(--ofm-border) !important}}
[data-testid="stMultiSelectTagsContainer"] *{{color:var(--ofm-text) !important}}
[data-testid="stNumberInputStepUp"],[data-testid="stNumberInputStepDown"]{{background:var(--ofm-panel-alt) !important;
  color:var(--ofm-text) !important;border-color:var(--ofm-border) !important}}
[role="listbox"],[role="menu"],[role="dialog"],[data-testid="stMainMenu"]{{background:var(--ofm-panel) !important;
  color:var(--ofm-text) !important;border:1px solid var(--ofm-border) !important;border-radius:10px}}
[role="option"],[role="menuitem"],[data-testid="stMainMenu"] li{{color:var(--ofm-text) !important}}
[role="option"]:hover,[role="option"][data-focused="true"],[role="menuitem"]:hover{{
  background:var(--ofm-panel-alt) !important}}
[role="option"][aria-selected="true"]{{background:var(--ofm-primary) !important;color:var(--ofm-primary-text) !important}}
[role="tooltip"]{{background:var(--ofm-panel-alt) !important;color:var(--ofm-text) !important;
  border:1px solid var(--ofm-border) !important;border-radius:8px}}
[role="tooltip"] *{{color:var(--ofm-text) !important}}
[data-testid="stTooltipIcon"] *,[data-testid="stTooltipHoverTarget"]{{color:var(--ofm-muted) !important}}
[data-testid="stExpander"]{{background:var(--ofm-panel);border:1px solid var(--ofm-border);border-radius:12px}}
[data-testid="stExpander"] summary,[data-testid="stExpander"] summary *{{color:var(--ofm-text) !important}}
[data-testid="stExpanderDetails"]{{background:var(--ofm-panel)}}
[data-testid="stMetric"]{{background:var(--ofm-panel);border:1px solid var(--ofm-border);border-radius:12px;padding:.5rem .8rem}}
[data-testid="stMetricValue"],[data-testid="stMetricValue"] *{{color:var(--ofm-accent)}}
[data-testid="stMetricLabel"] *{{color:var(--ofm-muted)}}
[data-testid="stDataFrame"],[data-testid="stDataFrameResizable"]{{border:1px solid var(--ofm-border);border-radius:10px;
  overflow:hidden}}
[data-testid="stElementToolbar"]{{background:var(--ofm-panel-alt) !important;border:1px solid var(--ofm-border);
  border-radius:8px}}
[data-testid="stTableStyledTable"] th{{background:var(--ofm-panel-alt);color:var(--ofm-text);
  border-color:var(--ofm-border) !important}}
[data-testid="stTableStyledTable"] td{{color:var(--ofm-text);border-color:var(--ofm-border) !important}}
[data-testid="stCode"] pre,[data-testid="stJson"]{{background:var(--ofm-input) !important;
  border:1px solid var(--ofm-border);border-radius:10px}}
[data-testid="stFileUploaderDropzone"]{{background:var(--ofm-panel-alt) !important;
  border:1px dashed var(--ofm-border) !important}}
[data-testid="stFileUploaderDropzoneInstructions"] *{{color:var(--ofm-muted) !important}}
[data-testid="stSliderTickBar"] *{{color:var(--ofm-muted) !important}}
[data-testid="stSliderThumbValue"],[data-testid="stSliderThumbValue"] *{{color:var(--ofm-accent) !important}}
[data-testid="stProgressBarTrack"]{{background:var(--ofm-panel-alt) !important}}
[data-testid="stRadioOption"] p,[data-testid="stCheckbox"] p,[data-testid="stButtonGroup"] p{{color:var(--ofm-text)}}
[data-testid="stAlert"],[data-testid="stAlertContainer"]{{border-radius:10px}}
[data-testid="stMarkdownContainer"] a,[data-testid="stCaptionContainer"] a{{color:var(--ofm-accent)}}
[data-testid="stMainBlockContainer"]{{padding-top:2.5rem}}
@media (max-width:640px){{
  [data-testid="stMainBlockContainer"]{{padding-top:1rem !important;padding-bottom:3rem !important}}
  h1{{font-size:1.75rem !important;line-height:1.08}}
  h2{{font-size:1.4rem !important}}
  [data-testid="stTab"]{{padding:.35rem .6rem;font-size:.92rem}}
  [data-testid="stMetricValue"]{{font-size:1.5rem}}
  .ofm-strip .cell{{flex:1 1 8rem;padding:.35rem .55rem}}
  .ofm-strip .v{{font-size:1.1rem}}
  .cm-score{{font-size:2.1rem}}
  .cm-team{{font-size:1.05rem}}
  .cm-ev{{grid-template-columns:2.6rem 1fr;row-gap:.2rem;font-size:.88rem}}
  .cm-ev .tag{{grid-column:2}}
}}
.cm-p-wrap{{border:2px solid var(--ofm-border);border-radius:14px;overflow:hidden;
  box-shadow:0 0 0 1px var(--ofm-panel),0 8px 24px rgba(0,0,0,.18)}}
.cm-board{{border:1px solid var(--ofm-border);box-shadow:0 0 0 1px var(--ofm-panel)}}
.cm-wonder{{color:var(--ofm-accent);font-weight:800}}
.ofm-panel-title{{background:var(--ofm-panel);color:var(--ofm-accent);border:1px solid var(--ofm-border);
  border-left:5px solid var(--ofm-primary);border-radius:10px;font-family:{CONDENSED_STACK};font-weight:800;
  text-transform:uppercase;letter-spacing:.06em;padding:.35rem .8rem;margin:.4rem 0}}
.ofm-strip{{display:flex;flex-wrap:wrap;gap:6px;margin:.3rem 0 .6rem}}
.ofm-strip .cell{{flex:1 1 9rem;min-width:0;background:var(--ofm-panel);border:1px solid var(--ofm-border);border-radius:10px;
  padding:.4rem .7rem}}
.ofm-strip .k{{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--ofm-muted)}}
.ofm-strip .v{{font-size:1.3rem;font-weight:800;color:var(--ofm-accent);white-space:nowrap}}
div[class*="st-key-arena_ball_"] button{{border-radius:50% !important;aspect-ratio:1/1;min-height:3rem;padding:0 !important;
  background:radial-gradient(circle at 32% 28%,#ffffff 0,#e2e8f0 40%,#94a3b8 100%) !important;color:#1e293b !important;
  font-weight:900;border:1px solid var(--ofm-border) !important;box-shadow:inset -3px -4px 6px rgba(0,0,0,.25) !important;
  transition:transform .12s ease,box-shadow .12s ease}}
div[class*="st-key-arena_ball_"] button *{{color:#1e293b !important}}
div[class*="st-key-arena_ball_"] button:hover{{transform:translateY(-2px) scale(1.06);
  box-shadow:inset -3px -4px 6px rgba(0,0,0,.25),0 0 12px 2px var(--ofm-accent) !important}}
</style>
"""
    if login:
        css = css.replace("</style>", _login_css(p) + "\n</style>")
    return _one_line(css)


def _login_css(p: Palette) -> str:
    """
    Giris sayfasi (taktik tahtasi vitrini): tema zemini, solda slogan + giris karti, sagda hareketli taktik
    tahtasi, altta adimlar. Hareketler CSS animasyonudur; "hareketi azalt" tercihinde durur.
    """
    link = p.accent if p.bg.lower() != "#f4f6f9" else "#0369a1"
    return f"""
[data-testid="stAppViewContainer"],[data-testid="stMain"]{{background:{p.hero_from} !important}}
[data-testid="stHeader"]{{background:transparent;border:0}}
[data-testid="stMainBlockContainer"]{{padding-top:1.4rem;max-width:1360px}}
.ofm-brandrow{{display:flex;align-items:baseline;gap:.7rem;flex-wrap:wrap}}
.ofm-brand{{font-family:{CONDENSED_STACK};font-style:italic;font-weight:800;font-size:2.3rem;line-height:1;color:{p.login_text}}}
.ofm-brand-sub{{font-size:.78rem;letter-spacing:.16em;text-transform:uppercase;color:{p.muted}}}
.ofm-pill{{display:inline-block;padding:.35rem .8rem;border-radius:999px;background:{p.primary}22;color:{p.login_text};
  font-size:.78rem;font-weight:700;letter-spacing:.08em;margin:.4rem 0 .9rem}}
h1.ofm-slogan,.ofm-slogan{{font-family:{CONDENSED_STACK} !important;font-weight:800;font-size:3.9rem;line-height:.98;
  color:{p.login_text} !important;margin:0 0 .8rem;padding:0;text-transform:none !important;font-style:normal !important;
  letter-spacing:0 !important;border:0 !important}}
.ofm-lead{{font-size:1.12rem;line-height:1.55;color:{p.muted};margin:0 0 1.1rem;max-width:34rem}}
.st-key-ofm_login_card{{background:{p.hero_via};border:1px solid {p.border};border-radius:16px;
  padding:1.1rem 1.25rem 1.2rem !important;box-shadow:0 12px 30px rgba(15,23,42,.08)}}
.ofm-headline{{font-family:{CONDENSED_STACK};font-weight:800;font-size:1.55rem;line-height:1.1;color:{p.login_text};
  margin:.1rem 0 .5rem;text-transform:uppercase;letter-spacing:.03em}}
.st-key-ofm_login_card [data-testid="stBaseButton-primary"]{{background:{p.login_button} !important;
  border:0 !important;border-radius:10px !important;min-height:2.9rem}}
.st-key-ofm_login_card [data-testid="stBaseButton-primary"] p{{color:{p.login_button_text} !important;font-weight:700;
  font-size:1.02rem}}
.st-key-ofm_login_card [data-testid="stBaseButton-tertiary"] p{{color:{link} !important;font-weight:700;
  text-decoration:underline}}
.ofm-login-note{{color:{p.muted};font-size:.9rem;margin:.35rem 0 0}}
.st-key-ofm_login_card [data-testid="stTextInputRootElement"]{{
  background:{p.input_bg} !important;border:1px solid {p.muted}66 !important;border-radius:10px !important}}
.st-key-ofm_login_card [data-testid="stTextInputRootElement"] div{{background:transparent !important}}
.st-key-ofm_login_card [data-testid="stTextInputRootElement"] button{{background:{p.input_bg} !important;
  color:{p.muted} !important}}
.st-key-ofm_login_card input{{background:{p.input_bg} !important;color:{p.text} !important;-webkit-text-fill-color:{p.text}}}
.st-key-ofm_login_card input:-webkit-autofill{{-webkit-text-fill-color:{p.text} !important;caret-color:{p.text};
  -webkit-box-shadow:0 0 0 1000px {p.input_bg} inset !important;transition:background-color 9999s}}
.st-key-ofm_login_card input::placeholder{{color:{p.muted} !important;opacity:.75}}
.st-key-ofm_login_card label p{{color:{p.muted} !important;font-weight:600}}
.ofm-hero{{position:relative;width:100%;aspect-ratio:760/500;min-height:320px;border-radius:20px;overflow:hidden;
  background:{p.tri_a};box-shadow:0 18px 40px rgba(15,23,42,.18)}}
.ofm-hero svg{{position:absolute;inset:0;width:100%;height:100%;display:block}}
.ofm-chips{{position:absolute;left:16px;top:16px;display:flex;gap:8px;flex-wrap:wrap}}
.ofm-chip{{padding:.4rem .7rem;border-radius:8px;background:#ffffff;color:#0f172a;font-size:.86rem;font-weight:600}}
.ofm-chip.dark{{background:#0f172a;color:#ffffff;font-weight:700}}
.ofm-plan{{position:absolute;right:16px;bottom:16px;width:min(300px,62%);min-height:5.4rem;padding:.75rem .9rem;
  border-radius:12px;background:#ffffff;color:#0f172a;box-shadow:0 8px 20px rgba(15,23,42,.2)}}
.ofm-plan b{{display:block;font-size:.72rem;letter-spacing:.12em;color:#0369a1;margin-bottom:.25rem}}
.ofm-plan span{{position:absolute;left:.9rem;right:.9rem;top:2rem;font-size:.95rem;line-height:1.35;
  animation:ofm-swap 12s infinite}}
.ofm-plan span:nth-of-type(2){{animation-delay:-6s}}
.ofm-run{{stroke-dasharray:10 10;animation:ofm-dash 1.1s linear infinite}}
.ofm-p{{animation:ofm-press 6s ease-in-out infinite}}
.ofm-p.d{{animation-name:ofm-step}}
.ofm-o{{animation:ofm-drop 6s ease-in-out infinite}}
.ofm-ball{{animation:ofm-ball 6s linear infinite}}
@keyframes ofm-dash{{to{{stroke-dashoffset:-20}}}}
@keyframes ofm-press{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(26px,0)}}}}
@keyframes ofm-step{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(14px,0)}}}}
@keyframes ofm-drop{{0%,100%{{transform:translate(0,0)}}50%{{transform:translate(16px,0)}}}}
@keyframes ofm-ball{{0%{{transform:translate(165px,190px);opacity:1}}16%{{transform:translate(288px,240px)}}
  32%{{transform:translate(310px,150px)}}50%{{transform:translate(456px,110px)}}64%{{transform:translate(491px,240px)}}
  78%{{transform:translate(706px,236px);opacity:1}}82%{{transform:translate(706px,236px);opacity:0}}
  99%{{transform:translate(165px,190px);opacity:0}}100%{{transform:translate(165px,190px);opacity:1}}}}
@keyframes ofm-swap{{0%,45%{{opacity:1}}50%,95%{{opacity:0}}100%{{opacity:1}}}}
.ofm-steps{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-top:1.2rem}}
.ofm-step{{background:{p.hero_to};border:1px solid {p.border};border-radius:12px;padding:.8rem .9rem}}
.ofm-step .n{{font-family:{CONDENSED_STACK};font-weight:800;font-size:1.6rem;color:{p.primary};line-height:1}}
.ofm-step .t{{font-weight:700;color:{p.login_text};margin:.2rem 0 .1rem}}
.ofm-step .d{{font-size:.88rem;line-height:1.35;color:{p.muted}}}
@media (prefers-reduced-motion:reduce){{.ofm-hero *{{animation:none !important}}.ofm-ball{{display:none}}
  .ofm-plan span:nth-of-type(2){{opacity:0}}}}
@media (max-width:900px){{.ofm-slogan{{font-size:2.6rem}}.ofm-steps{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}"""


# 4-3-3 (sola kaleci, saga hucum): (x, y, sinif) -- d: savunma (daha az one cikar)
_BOARD_PLAYERS: tuple[tuple[int, int, str], ...] = (
    (70, 240, "k"),
    (175, 95, "d"), (165, 190, "d"), (165, 290, "d"), (175, 385, "d"),
    (290, 150, "m"), (275, 240, "m"), (290, 330, "m"),
    (430, 110, "f"), (465, 240, "f"), (430, 370, "f"),
)
_BOARD_OPPONENTS: tuple[tuple[int, int], ...] = ((560, 125), (585, 205), (585, 285), (560, 365), (700, 240))


def login_hero_html(theme: str) -> str:
    """
    Giris sayfasi vitrini: hareketli taktik tahtasi (4-3-3 pres, pas zinciri ve kosu oklari) ile uzerinde
    dizilis/talimat etiketleri ve donen mac plani karti. Tamamen cizim; fotograf ya da dis kaynak yok.
    """
    p = PALETTES[normalize_theme(theme)]
    stripes = "".join(f'<rect x="{x}" y="0" width="76" height="500" fill="{p.tri_b}"/>' for x in range(0, 760, 152))
    players = []
    for i, (x, y, role) in enumerate(_BOARD_PLAYERS):
        cls = "ofm-p d" if role == "d" else ("" if role == "k" else "ofm-p")
        delay = f' style="animation-delay:{i * 0.12:.2f}s"' if cls else ""
        players.append(f'<g class="{cls}"{delay}><circle cx="{x}" cy="{y}" r="15" fill="#ffffff" stroke="#0f172a" '
                       'stroke-width="3"/></g>')
    opponents = "".join(
        f'<g class="ofm-o" style="animation-delay:{i * 0.15:.2f}s"><circle cx="{x}" cy="{y}" r="12" fill="none" '
        'stroke="#fecaca" stroke-width="3"/></g>'
        for i, (x, y) in enumerate(_BOARD_OPPONENTS)
    )
    return (
        '<div class="ofm-hero" aria-hidden="true">'
        '<svg viewBox="0 0 760 500" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">'
        f'<rect x="0" y="0" width="760" height="500" fill="{p.tri_a}"/>{stripes}'
        '<g fill="none" stroke="#e8f5ee" stroke-width="3" opacity=".85">'
        '<rect x="24" y="24" width="712" height="432"/><line x1="380" y1="24" x2="380" y2="456"/>'
        '<circle cx="380" cy="240" r="62"/><rect x="24" y="135" width="100" height="210"/>'
        '<rect x="636" y="135" width="100" height="210"/><rect x="736" y="205" width="10" height="70"/></g>'
        f'<g fill="none" stroke="{p.tri_c}" stroke-width="4" stroke-linecap="round">'
        '<path class="ofm-run" d="M290 150 C 360 110, 400 100, 430 110"/>'
        '<path class="ofm-run" d="M275 240 C 350 255, 400 250, 465 240"/>'
        '<path class="ofm-run" d="M175 385 C 300 430, 480 430, 560 390"/></g>'
        f'<polygon points="566,386 546,378 552,398" fill="{p.tri_c}"/>'
        f'{opponents}{"".join(players)}'
        '<circle class="ofm-ball" cx="0" cy="0" r="7" fill="#ffffff" stroke="#0f172a" stroke-width="2"/>'
        "</svg>"
        '<div class="ofm-chips"><span class="ofm-chip dark">4-3-3</span>'
        '<span class="ofm-chip">Kısa pas · Tüm sahada pres</span></div>'
        '<div class="ofm-plan"><b>MAÇ PLANI</b>'
        "<span>60. dakikada gerideysek: Çok Ofansif, hızlı tempo, yedek forvet oyuna girsin.</span>"
        "<span>75. dakikada öndeysek: Otobüsü çek, sakin oyna, orta sahaya taze oyuncu.</span></div>"
        "</div>"
    )


def brand_html() -> str:
    return ('<div class="ofm-brandrow">'
            f'<div class="ofm-brand">{APP_SHORT}</div>'
            f'<div class="ofm-brand-sub" lang="en">{escape(APP_NAME)}</div>'      # Ingilizce buyuk harf: ONLINE
            "</div>")


def login_intro_html() -> str:
    """Giris sayfasi sol ust: etiket, slogan ve kisa tanitim."""
    return ('<div class="ofm-pill">SIRA TABANLI · TARAYICIDA · ÜCRETSİZ</div>'
            '<h1 class="ofm-slogan">Taktiği sen kur,<br>maçı canlı yönet.</h1>'
            '<p class="ofm-lead">Kadronu seç, Taktik Merkezi’nde planını yap, maç günü kenardan müdahale et. '
            "Transfer pazarında pazarlık et, altyapından yıldız çıkar, kupayı kaldır.</p>")


def login_headline_html(view: str) -> str:
    text = "Yeni menajer kaydı" if view == "register" else "Çevrimiçi menajer girişi"
    return f'<div class="ofm-headline">{escape(text)}</div>'


_LOGIN_STEPS: tuple[tuple[str, str, str], ...] = (
    ("1", "Hesap aç", "Kullanıcı adı ve parola yeter."),
    ("2", "Kulübünü seç", "Liglerden birinde takımı devral."),
    ("3", "Sahaya çık", "Haftayı oyna ya da maçı canlı yönet."),
    ("⚑", "Taktik Merkezi", "Pas stili, pres, duran top ve maç planı."),
    ("▶", "Canlı maç", "2D sahada durdur, değiştir, dizilişi çevir."),
    ("✦", "Altyapı", "Her sezon yeni gençler; cevherini keşfet."),
)


def login_steps_html() -> str:
    """Giris sayfasi alt seridi: uc adim ve uc ozellik."""
    cards = "".join(
        f'<div class="ofm-step"><div class="n">{escape(n)}</div><div class="t">{escape(t)}</div>'
        f'<div class="d">{escape(d)}</div></div>'
        for n, t, d in _LOGIN_STEPS
    )
    return f'<div class="ofm-steps">{cards}</div>'


def stat_strip_html(items) -> str:
    """Bilgi seridi: [(etiket, deger), ...]. Dar ekranda satir atlar, degerler kesilmez."""
    cells = "".join(
        f'<div class="cell"><div class="k">{escape(str(label))}</div><div class="v">{escape(str(value))}</div></div>'
        for label, value in items
    )
    return f'<div class="ofm-strip">{cells}</div>'


def panel_title_html(text: str) -> str:
    return f'<div class="ofm-panel-title">{escape(text)}</div>'
