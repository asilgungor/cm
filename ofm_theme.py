"""
ofm_theme.py
============
OFM (Online Football Manager) gorsel temalari. SAF SUNUM: yalnizca CSS/HTML metni uretir;
Streamlit, veritabani ya da oyun kurallarini BILMEZ.

Iki tema (menajer secer; web_app st.session_state["theme"] + ?theme= URL parametresinde tutar):
    dark   ⚽ FM Dark   gece mavisi zemin, lacivert paneller, beyaz metin, FM sarisi vurgu, mor dugmeler
    light  ☀️ FM Light  acik gri zemin, beyaz golgeli paneller, antrasit metin, yesil dugmeler, mavi vurgu

    theme_css(theme, login=False)   sayfaya basilan TEK <style> blogu (bos satir yok: Streamlit
                                     markdown'i HTML blogunu bolmesin). login=True giris sayfasinin
                                     mor degrade zeminini, alt cizgili alanlarini ve egik dugmesini ekler.
    login_hero_html(theme)          giris sayfasi sol gorseli: egik ucgenler + soyut top (fotograf YOK)
    brand_html() / login_headline_html(view)
    panel_title_html() / stat_strip_html()
    contrast_ratio()                WCAG kontrast orani (okunabilirlik testleri)

Streamlit'in canvas ile cizdigi tablolar CSS ile boyanamaz; onlar .streamlit/config.toml'daki
[theme.light] / [theme.dark] paletleriyle ayni renklerdedir. GUVENLIK: HTML'e giden her metin kacirilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

THEME_DARK, THEME_LIGHT = "dark", "light"
DEFAULT_THEME = THEME_DARK
THEME_LABELS: dict[str, str] = {THEME_DARK: "⚽ FM Dark", THEME_LIGHT: "☀️ FM Light"}
APP_NAME = "Online Football Manager"
APP_SHORT = "OFM"
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
    hero_from: str           # giris sayfasi degradesi
    hero_via: str
    hero_to: str
    tri_a: str               # ucgenler
    tri_b: str
    tri_c: str
    login_text: str
    login_button: str
    login_button_text: str


PALETTES: dict[str, Palette] = {
    THEME_DARK: Palette(
        bg="#121824", bg_alt="#1a1f2c", panel="#1e2538", panel_alt="#242b3d", border="#323b55",
        text="#ffffff", muted="#aeb6c8", accent="#FFCD00",
        primary="#635BFF", primary_hover="#4f46e5", primary_text="#ffffff", input_bg="#161c2b",
        hero_from="#241845", hero_via="#4b2f7d", hero_to="#7d4ba6",
        tri_a="#4f46e5", tri_b="#d63fa7", tri_c="#8b3fd9",
        login_text="#ffffff", login_button="#2a1b4f", login_button_text="#ffffff",
    ),
    THEME_LIGHT: Palette(
        bg="#f4f6f9", bg_alt="#eef2f6", panel="#ffffff", panel_alt="#f8fafc", border="#dbe2ea",
        text="#1e293b", muted="#5b6b82", accent="#0284c7",
        primary="#10b981", primary_hover="#059669", primary_text="#0b1220", input_bg="#ffffff",
        hero_from="#ede9fe", hero_via="#ddd6fe", hero_to="#fbcfe8",
        tri_a="#818cf8", tri_b="#f472b6", tri_c="#a78bfa",
        login_text="#1e293b", login_button="#1e293b", login_button_text="#ffffff",
    ),
}


def normalize_theme(value: object) -> str:
    """'light' / 'dark' ya da etiket ('☀️ FM Light'); bilinmeyen -> varsayilan (koyu)."""
    if value in PALETTES:
        return str(value)
    for key, label in THEME_LABELS.items():
        if value == label:
            return key
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


def _one_line(css: str) -> str:
    return "\n".join(line for line in css.splitlines() if line.strip())


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
[data-testid="stHeader"]{{background:var(--ofm-bg-alt);border-bottom:1px solid var(--ofm-border)}}
[data-testid="stSidebar"]{{background:var(--ofm-panel);border-right:1px solid var(--ofm-border)}}
[data-testid="stSidebar"] *{{color:var(--ofm-text)}}
[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{{color:var(--ofm-accent) !important;text-transform:uppercase;
  letter-spacing:.06em;font-family:{CONDENSED_STACK} !important}}
[data-testid="stMarkdownContainer"],[data-testid="stCaptionContainer"],label,p,li{{color:var(--ofm-text)}}
[data-testid="stCaptionContainer"]{{color:var(--ofm-muted) !important}}
h1{{color:var(--ofm-accent) !important;font-family:{CONDENSED_STACK} !important;font-style:italic;font-weight:800;
  text-transform:uppercase;letter-spacing:.02em}}
h2,h3,h4{{color:var(--ofm-text) !important;font-family:{CONDENSED_STACK} !important}}
h4{{border-left:4px solid var(--ofm-accent);padding:.15rem .6rem;text-transform:uppercase;letter-spacing:.04em}}
.stTabs [data-baseweb="tab-list"]{{gap:4px;background:var(--ofm-panel);padding:4px;border-radius:12px;
  border:1px solid var(--ofm-border)}}
.stTabs [data-baseweb="tab"]{{background:transparent;color:var(--ofm-muted);border-radius:9px;padding:.4rem .85rem;
  font-weight:700}}
.stTabs [aria-selected="true"]{{background:var(--ofm-primary) !important;color:var(--ofm-primary-text) !important}}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{{display:none}}
[data-testid="stBaseButton-primary"]{{background:var(--ofm-primary) !important;border:1px solid var(--ofm-primary) !important;
  color:var(--ofm-primary-text) !important;font-weight:700;border-radius:10px !important}}
[data-testid="stBaseButton-primary"] *{{color:var(--ofm-primary-text) !important}}
[data-testid="stBaseButton-primary"]:hover{{background:var(--ofm-primary-hover) !important;border-color:var(--ofm-primary-hover) !important}}
[data-testid="stBaseButton-secondary"]{{background:var(--ofm-panel-alt) !important;color:var(--ofm-text) !important;
  border:1px solid var(--ofm-border) !important;border-radius:10px !important;font-weight:600}}
[data-testid="stBaseButton-secondary"]:hover{{border-color:var(--ofm-accent) !important;color:var(--ofm-accent) !important}}
.stButton button:disabled{{opacity:.5}}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{{background:var(--ofm-input) !important;
  border-color:var(--ofm-border) !important;border-radius:10px !important}}
[data-baseweb="input"] input,[data-baseweb="textarea"] textarea,[data-baseweb="select"] *{{color:var(--ofm-text) !important}}
[data-testid="stExpander"]{{background:var(--ofm-panel);border:1px solid var(--ofm-border);border-radius:12px}}
[data-testid="stMetric"]{{background:var(--ofm-panel);border:1px solid var(--ofm-border);border-radius:12px;padding:.5rem .8rem}}
[data-testid="stMetricValue"]{{color:var(--ofm-accent)}}
[data-testid="stDataFrame"]{{border:1px solid var(--ofm-border);border-radius:10px;overflow:hidden}}
[data-testid="stAlert"]{{border-radius:10px}}
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
    """Giris sayfasi: mor degrade zemin, alt cizgili alanlar, egik koyu dugme (referans tasarim)."""
    return f"""
[data-testid="stAppViewContainer"],[data-testid="stMain"]{{background:
  linear-gradient(125deg,{p.hero_from} 0%,{p.hero_via} 55%,{p.hero_to} 100%) !important}}
[data-testid="stHeader"]{{background:transparent;border:0}}
[data-testid="stMainBlockContainer"] label,[data-testid="stMainBlockContainer"] p{{color:{p.login_text} !important}}
[data-testid="stMainBlockContainer"] [data-testid="stTextInput"] label p{{font-family:{CONDENSED_STACK} !important;
  font-style:italic;text-transform:uppercase;letter-spacing:.12em;font-size:.95rem;opacity:.85}}
[data-testid="stMainBlockContainer"] [data-baseweb="input"]{{background:transparent !important;border:0 !important;
  border-bottom:2px solid {p.login_text}55 !important;border-radius:0 !important}}
[data-testid="stMainBlockContainer"] [data-baseweb="input"] input{{color:{p.login_text} !important;background:transparent !important}}
[data-testid="stMainBlockContainer"] [data-testid="stBaseButton-primary"]{{background:{p.login_button} !important;
  border:0 !important;border-radius:0 !important;transform:skewX(-12deg);min-height:2.9rem;
  box-shadow:0 10px 24px rgba(0,0,0,.25)}}
[data-testid="stMainBlockContainer"] [data-testid="stBaseButton-primary"] p{{transform:skewX(12deg);
  color:{p.login_button_text} !important;font-family:{CONDENSED_STACK} !important;font-style:italic;
  text-transform:uppercase;letter-spacing:.14em;font-size:1.05rem}}
[data-testid="stMainBlockContainer"] [data-testid="stBaseButton-tertiary"] p{{color:{p.login_text} !important;
  font-weight:700;text-decoration:underline}}
.ofm-hero{{position:relative;min-height:440px;width:100%;overflow:hidden;border-radius:18px}}
.ofm-hero svg{{position:absolute;inset:0;width:100%;height:100%}}
.ofm-brand{{text-align:right;font-family:{CONDENSED_STACK};font-style:italic;font-weight:800;font-size:3.1rem;line-height:1;
  color:{p.login_text};text-shadow:0 0 18px {p.tri_a}}}
.ofm-brand-sub{{text-align:right;font-family:{CONDENSED_STACK};font-style:italic;letter-spacing:.16em;
  text-transform:uppercase;color:{p.login_text};opacity:.8;margin-bottom:1.4rem}}
.ofm-headline{{font-family:{CONDENSED_STACK};font-style:italic;font-weight:800;font-size:2.2rem;line-height:1.05;
  text-transform:uppercase;color:{p.login_text};margin:.4rem 0 1rem}}
.ofm-login-note{{color:{p.login_text};opacity:.75;font-size:.85rem;text-align:center;margin-top:.6rem}}
@media (max-width:640px){{.ofm-hero{{min-height:180px}}.ofm-brand{{font-size:2.3rem}}.ofm-headline{{font-size:1.6rem}}}}"""


def login_hero_html(theme: str) -> str:
    """Giris sayfasi sol gorseli: egik ucgenler ve hareket izli soyut top. Gercek kisi/fotograf yok."""
    p = PALETTES[normalize_theme(theme)]
    return (
        '<div class="ofm-hero" aria-hidden="true">'
        '<svg viewBox="0 0 400 460" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">'
        f'<defs><linearGradient id="ofm-g1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{p.tri_a}"/>'
        f'<stop offset="1" stop-color="{p.tri_c}"/></linearGradient>'
        f'<linearGradient id="ofm-g2" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="{p.tri_b}"/>'
        f'<stop offset="1" stop-color="{p.tri_c}" stop-opacity=".2"/></linearGradient></defs>'
        '<polygon points="-20,460 90,40 210,460" fill="url(#ofm-g1)" opacity=".92"/>'
        '<polygon points="40,460 150,150 330,460" fill="url(#ofm-g2)" opacity=".9"/>'
        f'<polygon points="120,460 260,230 400,460" fill="{p.tri_b}" opacity=".55"/>'
        f'<polygon points="210,0 400,0 400,300" fill="{p.tri_a}" opacity=".18"/>'
        '<g transform="translate(248 150)">'
        f'<line x1="-150" y1="-10" x2="-62" y2="-10" stroke="{p.login_text}" stroke-width="5" stroke-linecap="round" opacity=".55"/>'
        f'<line x1="-120" y1="16" x2="-60" y2="16" stroke="{p.login_text}" stroke-width="4" stroke-linecap="round" opacity=".4"/>'
        f'<line x1="-96" y1="40" x2="-58" y2="40" stroke="{p.login_text}" stroke-width="3" stroke-linecap="round" opacity=".3"/>'
        '<circle r="54" fill="#ffffff" stroke="#1e293b" stroke-width="4"/>'
        '<polygon points="0,-20 19,-6 12,16 -12,16 -19,-6" fill="#1e293b"/>'
        '<polygon points="0,-54 12,-44 7,-31 -7,-31 -12,-44" fill="#1e293b"/>'
        '<polygon points="51,-17 45,-3 32,-6 30,-20 42,-28" fill="#1e293b"/>'
        '<polygon points="-51,-17 -42,-28 -30,-20 -32,-6 -45,-3" fill="#1e293b"/>'
        '<polygon points="32,44 22,50 13,39 21,27 33,31" fill="#1e293b"/>'
        '<polygon points="-32,44 -33,31 -21,27 -13,39 -22,50" fill="#1e293b"/>'
        "</g>"
        f'<text x="24" y="440" font-size="64" font-style="italic" font-weight="800" fill="{p.login_text}" '
        f'opacity=".16" font-family="Arial Narrow, Arial, sans-serif">{APP_SHORT}</text>'
        "</svg></div>"
    )


def brand_html() -> str:
    return (f'<div class="ofm-brand">{APP_SHORT}</div>'
            f'<div class="ofm-brand-sub">{escape(APP_NAME)}</div>')


def login_headline_html(view: str) -> str:
    text = "Yeni menajer kaydı" if view == "register" else "Çevrimiçi menajer girişi"
    return f'<div class="ofm-headline">{escape(text)}</div>'


def stat_strip_html(items) -> str:
    """Bilgi seridi: [(etiket, deger), ...]. Dar ekranda satir atlar, degerler kesilmez."""
    cells = "".join(
        f'<div class="cell"><div class="k">{escape(str(label))}</div><div class="v">{escape(str(value))}</div></div>'
        for label, value in items
    )
    return f'<div class="ofm-strip">{cells}</div>'


def panel_title_html(text: str) -> str:
    return f'<div class="ofm-panel-title">{escape(text)}</div>'
