"""
cm_theme.py
===========
Championship Manager retro temasi (10. Asama). SAF SUNUM: yalnizca CSS/HTML metni uretir;
Streamlit, veritabani ya da oyun kurallarini BILMEZ.

CM 01/02 - CM4 havasi: siyah zemin, koyu yesil baslik seritleri, gri paneller, kabartmali
(bevel) dugmeler, Tahoma/Verdana gibi klasik ekran fontlari, sari vurgu. Streamlit'in kendi
elemanlari data-testid / baseweb seciciyle boyanir; oyunun HTML parcalari (tabela, saha,
kura) kendi renkleriyle kalir ve bu zeminde okunur.

    CM_THEME_CSS          sayfaya BIR KEZ basilan <style> blogu
    login_banner_html()   giris ekrani karsilama paneli (tek satir HTML)
    panel_title_html()    CM tarzi yesil baslik seridi
    stat_strip_html()     satir atlayan kucuk bilgi kutulari (metrik yerine; dar ekranda kesilmez)
"""

from __future__ import annotations

from html import escape

# Palet: .streamlit/config.toml ile ayni degerler (tek yerden okunabilsin diye burada da sabit)
BLACK = "#050805"
PANEL = "#1c211d"
PANEL_LIGHT = "#2b322c"
GREEN_DARK = "#0d3b1e"
GREEN = "#1f6b35"
GREEN_LIGHT = "#3f9a55"
GREY = "#8a938b"
TEXT = "#e6ebe4"
ACCENT = "#f2c230"          # CM sari vurgu (secili sekme, onemli sayilar)
FONT_STACK = 'Tahoma, Verdana, "Trebuchet MS", "DejaVu Sans", sans-serif'

CM_THEME_CSS = f"""
<style>
:root{{--cm-black:{BLACK};--cm-panel:{PANEL};--cm-panel-2:{PANEL_LIGHT};--cm-green-dark:{GREEN_DARK};
  --cm-green:{GREEN};--cm-green-light:{GREEN_LIGHT};--cm-grey:{GREY};--cm-text:{TEXT};--cm-accent:{ACCENT}}}
html,body,[data-testid="stAppViewContainer"],[data-testid="stSidebar"],button,input,textarea,select{{
  font-family:{FONT_STACK} !important}}
[data-testid="stAppViewContainer"]{{background:
  radial-gradient(circle at 50% -20%,rgba(31,107,53,.35),transparent 55%),var(--cm-black);color:var(--cm-text)}}
[data-testid="stHeader"]{{background:linear-gradient(180deg,var(--cm-green-dark),#07160c);
  border-bottom:2px solid var(--cm-green)}}
[data-testid="stSidebar"]{{background:linear-gradient(180deg,#151a16,#0a0d0a);border-right:2px solid var(--cm-green-dark)}}
[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{{background:var(--cm-green-dark);color:var(--cm-accent);
  padding:.3rem .6rem;border:1px solid var(--cm-green);text-transform:uppercase;letter-spacing:.06em;font-size:1rem}}
h1{{color:var(--cm-accent) !important;text-transform:uppercase;letter-spacing:.08em;font-weight:800;
  text-shadow:2px 2px 0 #000}}
h2,h3,h4{{color:var(--cm-text) !important;letter-spacing:.03em}}
h4{{background:linear-gradient(90deg,var(--cm-green-dark),transparent);border-left:4px solid var(--cm-green-light);
  padding:.25rem .6rem;text-transform:uppercase;font-size:.95rem !important}}
.stTabs [data-baseweb="tab-list"]{{gap:2px;background:var(--cm-panel);padding:3px;border:1px solid #000;
  box-shadow:inset 0 0 0 1px var(--cm-panel-2)}}
.stTabs [data-baseweb="tab"]{{background:linear-gradient(180deg,#39423a,#232924);color:var(--cm-text);
  border:1px solid #000;border-radius:0;padding:.35rem .8rem;font-weight:700;font-size:.85rem}}
.stTabs [aria-selected="true"]{{background:linear-gradient(180deg,var(--cm-green-light),var(--cm-green-dark)) !important;
  color:var(--cm-accent) !important}}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{{display:none}}
.stButton button,.stFormSubmitButton button,[data-testid="stBaseButton-secondary"],[data-testid="stBaseButton-primary"]{{
  border-radius:0 !important;font-weight:700;text-transform:uppercase;letter-spacing:.04em;font-size:.82rem;
  border:1px solid #000 !important;box-shadow:inset 1px 1px 0 rgba(255,255,255,.25),inset -1px -1px 0 rgba(0,0,0,.5)}}
[data-testid="stBaseButton-secondary"]{{background:linear-gradient(180deg,#4a534b,#2d342e) !important;color:var(--cm-text) !important}}
[data-testid="stBaseButton-primary"]{{background:linear-gradient(180deg,var(--cm-green-light),var(--cm-green)) !important;
  color:#fff !important}}
.stButton button:disabled{{opacity:.45}}
[data-testid="stExpander"]{{border:1px solid #000;border-radius:0;background:var(--cm-panel)}}
[data-testid="stExpander"] summary{{background:linear-gradient(180deg,#2f3730,#1f2520);font-weight:700}}
[data-testid="stMetric"]{{background:var(--cm-panel);border:1px solid #000;box-shadow:inset 0 0 0 1px var(--cm-panel-2);
  padding:.45rem .7rem}}
[data-testid="stMetricValue"]{{color:var(--cm-accent);font-weight:800}}
[data-testid="stDataFrame"],[data-testid="stTable"]{{border:1px solid #000;box-shadow:0 0 0 1px var(--cm-green-dark)}}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{{border-radius:0 !important;
  background:#0f130f !important;border-color:var(--cm-panel-2) !important}}
[data-testid="stAlert"]{{border-radius:0;border-left-width:4px}}
.cm-board{{border-radius:0 !important;box-shadow:inset 0 0 0 1px var(--cm-green)}}
.cm-login{{max-width:34rem;margin:1.5rem auto .5rem;border:2px solid #000;background:var(--cm-panel);
  box-shadow:0 0 0 2px var(--cm-green-dark),6px 6px 0 rgba(0,0,0,.6)}}
.cm-login .bar{{background:linear-gradient(180deg,var(--cm-green-light),var(--cm-green-dark));color:var(--cm-accent);
  font-weight:800;letter-spacing:.12em;text-transform:uppercase;padding:.45rem .8rem;border-bottom:2px solid #000}}
.cm-login .body{{padding:1rem 1.1rem;color:var(--cm-text)}}
.cm-login .logo{{font-size:2.3rem;font-weight:900;letter-spacing:.14em;color:#fff;text-shadow:3px 3px 0 var(--cm-green-dark)}}
.cm-login .tag{{color:var(--cm-grey);font-size:.85rem;margin-top:.2rem}}
.cm-login .ticker{{margin-top:.8rem;background:#000;color:var(--cm-accent);font-family:"Courier New",monospace;
  padding:.3rem .5rem;font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.cm-panel-title{{background:linear-gradient(180deg,var(--cm-green),var(--cm-green-dark));color:var(--cm-accent);
  font-weight:800;text-transform:uppercase;letter-spacing:.08em;padding:.3rem .7rem;border:1px solid #000;margin:.4rem 0}}
.cm-wonder{{color:var(--cm-accent);font-weight:800}}
.cm-strip{{display:flex;flex-wrap:wrap;gap:4px;margin:.3rem 0 .6rem}}
.cm-strip .cell{{flex:1 1 9rem;min-width:0;background:var(--cm-panel);border:1px solid #000;
  box-shadow:inset 0 0 0 1px var(--cm-panel-2);padding:.35rem .6rem}}
.cm-strip .k{{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--cm-grey)}}
.cm-strip .v{{font-size:1.25rem;font-weight:800;color:var(--cm-accent);white-space:nowrap}}
@media (max-width:640px){{.cm-login{{margin:1rem 0}}.cm-login .logo{{font-size:1.7rem}}}}
</style>
"""


def login_banner_html(season_text: str = "Sezon 2026/27", headline: str = "Menajer girişi") -> str:
    """Giris ekrani karsilama paneli (tek satir HTML; Streamlit markdown'i bolmesin)."""
    return (
        '<div class="cm-login">'
        f'<div class="bar">{escape(headline)}</div>'
        '<div class="body">'
        '<div class="logo">CM ⚽ MANAGER</div>'
        '<div class="tag">Kulübünü seç, akademini kur, sezonları yönet. Her menajerin kariyeri kendine aittir.</div>'
        f'<div class="ticker">» {escape(season_text)} » Transfer dönemi açık » Genç yetenekler akademide bekliyor »</div>'
        "</div></div>"
    )


def stat_strip_html(items) -> str:
    """CM bilgi seridi: [(etiket, deger), ...]. Dar ekranda satir atlar, degerler kesilmez."""
    cells = "".join(
        f'<div class="cell"><div class="k">{escape(str(label))}</div><div class="v">{escape(str(value))}</div></div>'
        for label, value in items
    )
    return f'<div class="cm-strip">{cells}</div>'


def panel_title_html(text: str) -> str:
    return f'<div class="cm-panel-title">{escape(text)}</div>'
