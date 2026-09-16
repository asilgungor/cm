"""
bracket_view.py
===============
Devler Arenasi ekran parcalari (8. Asama): eleme agaci, interaktif kura tahtasi, grup
tablolari ve sampiyon afisi. SAF FONKSIYONLAR: HTML metni uretir; Streamlit, veritabani
ya da cup_draw kurallarini BILMEZ (web_app.py gorunum modellerini doldurur).

    BRACKET_CSS            Sayfaya BIR KEZ basilan <style> blogu
    bracket_html           turlar soldan saga, baglanti cizgileri, galip/vurgu, kupa karti
    draw_board_html        torbalar ve toplar, eslesme/grup kartlari, parlayan son top
    group_tables_html      grup puan tablolari (cikan takimlar isaretli)
    champion_banner_html   sampiyon afisi

Tema: renkler currentColor, rgba ve CSS degiskenleriyle verilir; acik ve koyu Streamlit
temasinda okunur. Genis icerik .cm-b-scroll (overflow-x:auto; max-width:100%) icindedir,
sayfada yatay kaydirma olusmaz.

Streamlit markdown'i bos satir ve girintili satirlari HTML blogu disinda yorumlayabilir;
bu yuzden her fonksiyon TEK SATIRLIK, tek kok elemanli bir HTML parcasi dondurur.

GUVENLIK: takim adlari dis kaynakli FM dosyalarindan gelebilir; HTML'e yazilan her metin
html.escape() ile kacirilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

PLACEHOLDER = "?"

BRACKET_CSS = """<style>
.cm-b-scroll{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;
--cm-b-line:rgba(128,128,128,.45);--cm-b-card:rgba(128,128,128,.08);--cm-b-border:rgba(128,128,128,.3);
--cm-b-gold:#f2b705;--cm-b-gold-soft:rgba(242,183,5,.16);--cm-b-hl:#1e88e5;--cm-b-win:#43a047;
--cm-b-gap:30px;color:inherit;font-variant-numeric:tabular-nums;margin:.25rem 0 .6rem}
.cm-b-bracket{display:flex;align-items:stretch;gap:var(--cm-b-gap);min-width:max-content;padding:.4rem .2rem .8rem}
.cm-b-round{display:flex;flex-direction:column;width:200px;flex:0 0 auto}
.cm-b-round-title{text-align:center;font-weight:700;font-size:.82rem;letter-spacing:.4px;text-transform:uppercase;
opacity:.75;padding:.15rem 0 .5rem}
.cm-b-col{display:flex;flex-direction:column;flex:1}
.cm-b-pair{flex:1;display:flex;flex-direction:column;position:relative}
.cm-b-cell{flex:1;display:flex;align-items:center;position:relative;padding:5px 0}
.cm-b-cell > .cm-b-tie{width:100%}
.cm-b-link::after{content:"";position:absolute;top:25%;bottom:25%;right:calc(var(--cm-b-gap) / -2);
width:calc(var(--cm-b-gap) / 2);border:2px solid var(--cm-b-line);border-left:none;border-radius:0 6px 6px 0}
.cm-b-in .cm-b-cell::before{content:"";position:absolute;top:50%;left:calc(var(--cm-b-gap) / -2);
width:calc(var(--cm-b-gap) / 2);border-top:2px solid var(--cm-b-line)}
.cm-b-to-champ .cm-b-cell::after{content:"";position:absolute;top:50%;right:calc(var(--cm-b-gap) * -1);
width:var(--cm-b-gap);border-top:2px solid var(--cm-b-gold)}
.cm-b-tie{border:1px solid var(--cm-b-border);border-radius:10px;background:var(--cm-b-card);overflow:hidden;
font-size:.88rem}
.cm-b-tie.cm-b-hl{box-shadow:0 0 0 2px var(--cm-b-hl);border-color:var(--cm-b-hl)}
.cm-b-team{display:flex;align-items:center;justify-content:space-between;gap:.4rem;padding:.3rem .55rem;
border-left:3px solid transparent}
.cm-b-team + .cm-b-team{border-top:1px solid var(--cm-b-border)}
.cm-b-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cm-b-team.cm-b-win{font-weight:700;border-left-color:var(--cm-b-win);background:rgba(67,160,71,.12)}
.cm-b-team.cm-b-lose{opacity:.62}
.cm-b-mark{color:var(--cm-b-win);font-weight:800}
.cm-b-tbd{opacity:.5;font-style:italic}
.cm-b-meta{display:flex;flex-wrap:wrap;gap:.2rem .5rem;padding:.22rem .55rem;font-size:.74rem;
border-top:1px dashed var(--cm-b-border);opacity:.85}
.cm-b-agg{font-weight:700}
.cm-b-note{font-weight:700;padding:0 .35rem;border-radius:999px;background:var(--cm-b-gold-soft);
box-shadow:inset 0 0 0 1px rgba(242,183,5,.55)}
.cm-b-champ-round{justify-content:center}
.cm-b-champ{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.2rem;
min-width:170px;padding:1rem .8rem;border-radius:14px;border:2px solid var(--cm-b-gold);
background:linear-gradient(160deg,rgba(242,183,5,.28),rgba(242,183,5,.06));text-align:center;
box-shadow:0 6px 22px rgba(242,183,5,.22)}
.cm-b-trophy{font-size:2.3rem;line-height:1}
.cm-b-champ-label{font-size:.72rem;font-weight:700;letter-spacing:.8px;text-transform:uppercase;opacity:.8}
.cm-b-champ-name{font-size:1.05rem;font-weight:800;overflow-wrap:anywhere}
.cm-b-empty{padding:.8rem;opacity:.7;font-style:italic}
.cm-b-draw{display:flex;flex-direction:column;gap:.8rem;padding:.2rem}
.cm-b-headline{font-size:1.1rem;font-weight:800;padding:.55rem .8rem;border-radius:10px;
background:var(--cm-b-gold-soft);border-left:4px solid var(--cm-b-gold)}
.cm-b-announce{padding:.5rem .8rem;border-radius:10px;border:1px solid var(--cm-b-border);background:var(--cm-b-card);
font-weight:600;animation:cm-b-fade .6s ease-out 1}
.cm-b-pots{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:.6rem}
.cm-b-pot{border:1px solid var(--cm-b-border);border-radius:14px;padding:.5rem .6rem .65rem;
background:radial-gradient(ellipse at 50% 0%,rgba(128,128,128,.14),rgba(128,128,128,.03))}
.cm-b-pot-label{font-weight:700;font-size:.85rem;margin-bottom:.4rem;opacity:.85}
.cm-b-balls{display:flex;flex-wrap:wrap;gap:.35rem}
.cm-b-ball{display:inline-flex;align-items:center;justify-content:center;min-height:30px;font-size:.78rem;
border-radius:999px;border:1px solid var(--cm-b-border);max-width:100%}
.cm-b-ball.cm-b-closed{width:30px;height:30px;padding:0;
background:radial-gradient(circle at 32% 30%,rgba(255,255,255,.95),rgba(200,200,200,.75) 45%,rgba(120,120,120,.8))}
.cm-b-ball.cm-b-open{padding:.15rem .6rem;background:var(--cm-b-card);overflow-wrap:anywhere}
.cm-b-ball.cm-b-glow{padding:.15rem .6rem;font-weight:800;background:var(--cm-b-gold-soft);border-color:var(--cm-b-gold)}
.cm-b-slots{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:.5rem}
.cm-b-slot{border:1px solid var(--cm-b-border);border-radius:10px;background:var(--cm-b-card);overflow:hidden}
.cm-b-slot-title{font-size:.76rem;font-weight:700;letter-spacing:.3px;text-transform:uppercase;opacity:.75;
padding:.3rem .55rem;border-bottom:1px solid var(--cm-b-border)}
.cm-b-slot-team{padding:.25rem .55rem;font-size:.86rem;overflow-wrap:anywhere}
.cm-b-slot-team + .cm-b-slot-team{border-top:1px dashed var(--cm-b-border)}
.cm-b-slot.cm-b-glow{border-color:var(--cm-b-gold)}
.cm-b-glow{animation:cm-b-pulse 1.6s ease-in-out infinite}
@keyframes cm-b-pulse{0%,100%{box-shadow:0 0 0 0 rgba(242,183,5,.15)}50%{box-shadow:0 0 16px 4px rgba(242,183,5,.75)}}
@keyframes cm-b-fade{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.cm-b-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:.7rem}
.cm-b-group{border:1px solid var(--cm-b-border);border-radius:12px;background:var(--cm-b-card);overflow:hidden}
.cm-b-group-title{font-weight:800;padding:.4rem .7rem;border-bottom:1px solid var(--cm-b-border)}
.cm-b-table{width:100%;border-collapse:collapse;font-size:.84rem}
.cm-b-table th{font-weight:600;opacity:.7;padding:.25rem .3rem;text-align:center;border-bottom:1px solid var(--cm-b-border)}
.cm-b-table td{padding:.28rem .3rem;text-align:center;border-bottom:1px solid rgba(128,128,128,.14)}
.cm-b-table th.cm-b-tcol,.cm-b-table td.cm-b-tcol{text-align:left;width:100%;overflow-wrap:anywhere}
.cm-b-table td.cm-b-pts{font-weight:800}
.cm-b-table tr.cm-b-q td:first-child{box-shadow:inset 3px 0 0 var(--cm-b-win)}
.cm-b-table tr.cm-b-hl td{background:rgba(30,136,229,.14);font-weight:700}
.cm-b-banner{display:flex;align-items:center;gap:1rem;padding:1rem 1.2rem;border-radius:16px;
border:2px solid var(--cm-b-gold);background:linear-gradient(135deg,rgba(242,183,5,.3),rgba(242,183,5,.05));
box-shadow:0 8px 26px rgba(242,183,5,.2)}
.cm-b-banner .cm-b-trophy{font-size:3rem}
.cm-b-banner-text{display:flex;flex-direction:column;gap:.1rem;min-width:0}
.cm-b-banner-name{font-size:1.5rem;font-weight:800;overflow-wrap:anywhere}
.cm-b-banner-sub{opacity:.8}
@media (prefers-reduced-motion:reduce){.cm-b-scroll *{animation:none!important}}
</style>"""


# ===========================================================================
# [1] ELEME AGACI
# ===========================================================================

@dataclass
class TieView:
    home: str | None                  # None -> "?" (henuz belli degil)
    away: str | None
    legs: list[str] = field(default_factory=list)   # ornegin ["1-0", "1-2 uzt."]
    aggregate: str | None = None      # ornegin "2-2"
    note: str | None = None           # ornegin "pen. 4-3"
    winner: str | None = None         # "home" | "away" | None
    highlight: bool = False           # kullanicinin takimi bu eslesmede


@dataclass
class RoundView:
    label: str
    ties: list[TieView]


def _name(value: str | None) -> str:
    if value is None or not str(value).strip():
        return f'<span class="cm-b-name cm-b-tbd">{PLACEHOLDER}</span>'
    return f'<span class="cm-b-name">{escape(str(value))}</span>'


def _team_row(name: str | None, side: str, winner: str | None) -> str:
    classes = ["cm-b-team"]
    mark = ""
    if winner in ("home", "away"):
        if winner == side:
            classes.append("cm-b-win")
            mark = '<span class="cm-b-mark">✓</span>'
        else:
            classes.append("cm-b-lose")
    return f'<div class="{" ".join(classes)}">{_name(name)}{mark}</div>'


def _tie_html(tie: TieView) -> str:
    classes = "cm-b-tie cm-b-hl" if tie.highlight else "cm-b-tie"
    meta: list[str] = []
    if len(tie.legs) == 1:
        meta.append(f'<span class="cm-b-leg">{escape(tie.legs[0])}</span>')
    else:
        meta.extend(
            f'<span class="cm-b-leg">{i}. maç {escape(leg)}</span>' for i, leg in enumerate(tie.legs, 1)
        )
    if tie.aggregate:
        meta.append(f'<span class="cm-b-agg">Toplam {escape(tie.aggregate)}</span>')
    if tie.note:
        meta.append(f'<span class="cm-b-note">{escape(tie.note)}</span>')
    meta_html = f'<div class="cm-b-meta">{"".join(meta)}</div>' if meta else ""
    return (
        f'<div class="{classes}">'
        f"{_team_row(tie.home, 'home', tie.winner)}{_team_row(tie.away, 'away', tie.winner)}"
        f"{meta_html}</div>"
    )


def _champion_card(name: str) -> str:
    return (
        '<div class="cm-b-champ"><div class="cm-b-trophy">🏆</div>'
        '<div class="cm-b-champ-label">Şampiyon</div>'
        f'<div class="cm-b-champ-name">{escape(name)}</div></div>'
    )


def bracket_html(rounds: list[RoundView], champion: str | None = None) -> str:
    """
    Eleme agaci: turlar soldan saga. Bir sonraki turu olan turlarda eslesmeler ikiserli
    'pair' kutularina konur ve saga dogru kose cizgisiyle baglanir; sonraki turun her
    eslesmesi soldan kisa bir cizgi alir. Sampiyon verilirse kupa karti en saga eklenir.
    """
    if not rounds and not champion:
        return '<div class="cm-b-scroll"><div class="cm-b-empty">Henüz eşleşme yok.</div></div>'

    columns: list[str] = []
    last = len(rounds) - 1
    for index, rnd in enumerate(rounds):
        classes = ["cm-b-round"]
        if index > 0:
            classes.append("cm-b-in")
        if index == last and champion:
            classes.append("cm-b-to-champ")
        linked = index < last
        groups: list[str] = []
        ties = list(rnd.ties)
        if linked:
            for start in range(0, len(ties), 2):
                chunk = ties[start:start + 2]
                cls = "cm-b-pair cm-b-link" if len(chunk) == 2 else "cm-b-pair"
                cells = "".join(f'<div class="cm-b-cell">{_tie_html(t)}</div>' for t in chunk)
                groups.append(f'<div class="{cls}">{cells}</div>')
        else:
            groups.extend(f'<div class="cm-b-cell">{_tie_html(t)}</div>' for t in ties)
        columns.append(
            f'<div class="{" ".join(classes)}">'
            f'<div class="cm-b-round-title">{escape(rnd.label)}</div>'
            f'<div class="cm-b-col">{"".join(groups)}</div></div>'
        )
    if champion:
        columns.append(
            '<div class="cm-b-round cm-b-champ-round">'
            '<div class="cm-b-round-title">Kupa</div>'
            f'<div class="cm-b-col cm-b-champ-round">{_champion_card(champion)}</div></div>'
        )
    return f'<div class="cm-b-scroll"><div class="cm-b-bracket">{"".join(columns)}</div></div>'


# ===========================================================================
# [2] KURA TAHTASI
# ===========================================================================

BALL_STATES = ("closed", "open", "glow")


@dataclass
class BallView:
    label: str
    state: str      # "closed" (etiket gosterilmez), "open", "glow" (az once cekildi)


@dataclass
class PotView:
    label: str
    balls: list[BallView]


@dataclass
class SlotView:
    title: str                        # "Eşleşme 3" ya da "Grup B"
    teams: list[str | None]           # None -> "?"
    glow: bool = False


def _ball_html(ball: BallView) -> str:
    state = ball.state if ball.state in BALL_STATES else "closed"   # bilinmeyen durum -> kapali
    if state == "closed":
        return '<span class="cm-b-ball cm-b-closed" aria-label="kapalı top"></span>'
    return f'<span class="cm-b-ball cm-b-{"glow" if state == "glow" else "open"}">{escape(ball.label)}</span>'


def _slot_html(slot: SlotView) -> str:
    classes = "cm-b-slot cm-b-glow" if slot.glow else "cm-b-slot"
    teams = "".join(f'<div class="cm-b-slot-team">{_name(team)}</div>' for team in slot.teams)
    return f'<div class="{classes}"><div class="cm-b-slot-title">{escape(slot.title)}</div>{teams}</div>'


def draw_board_html(
    pots: list[PotView],
    slots: list[SlotView],
    headline: str | None = None,
    last_step_text: str | None = None,
) -> str:
    """Torbalar + toplar, eslesme/grup kartlari; glow=True kart ve 'glow' top nabiz gibi parlar."""
    parts: list[str] = []
    if headline:
        parts.append(f'<div class="cm-b-headline">{escape(headline)}</div>')
    if last_step_text:
        parts.append(f'<div class="cm-b-announce" role="status">{escape(last_step_text)}</div>')
    pot_html = "".join(
        f'<div class="cm-b-pot"><div class="cm-b-pot-label">{escape(pot.label)}</div>'
        f'<div class="cm-b-balls">{"".join(_ball_html(b) for b in pot.balls)}</div></div>'
        for pot in pots
    )
    parts.append(f'<div class="cm-b-pots">{pot_html}</div>')
    parts.append(f'<div class="cm-b-slots">{"".join(_slot_html(s) for s in slots)}</div>')
    return f'<div class="cm-b-scroll"><div class="cm-b-draw">{"".join(parts)}</div></div>'


# ===========================================================================
# [3] GRUP TABLOLARI VE AFIS
# ===========================================================================

@dataclass
class GroupRowView:
    position: int
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int
    qualified: bool = False
    highlight: bool = False


_GROUP_HEAD = (
    '<thead><tr><th>#</th><th class="cm-b-tcol">Takım</th><th title="Oynanan">O</th>'
    '<th title="Galibiyet">G</th><th title="Beraberlik">B</th><th title="Mağlubiyet">M</th>'
    '<th title="Atılan">A</th><th title="Yenilen">Y</th><th title="Averaj">Av</th>'
    '<th title="Puan">P</th></tr></thead>'
)


def _row_html(row: GroupRowView) -> str:
    classes = [c for c, on in (("cm-b-q", row.qualified), ("cm-b-hl", row.highlight)) if on]
    attr = f' class="{" ".join(classes)}"' if classes else ""
    diff = int(row.goals_for) - int(row.goals_against)
    cells = [
        str(int(row.position)),
        None,
        *(str(int(v)) for v in (row.played, row.won, row.drawn, row.lost, row.goals_for, row.goals_against)),
        f"{diff:+d}" if diff else "0",
    ]
    tds = "".join(
        f'<td class="cm-b-tcol">{escape(row.team)}</td>' if value is None else f"<td>{value}</td>"
        for value in cells
    )
    return f'<tr{attr}>{tds}<td class="cm-b-pts">{int(row.points)}</td></tr>'


def group_tables_html(groups: list[tuple[str, list[GroupRowView]]]) -> str:
    """Her grup icin puan tablosu; ust tura cikanlar (qualified) yesil seritle isaretlenir."""
    if not groups:
        return '<div class="cm-b-scroll"><div class="cm-b-empty">Grup kurası henüz çekilmedi.</div></div>'
    cards = "".join(
        f'<div class="cm-b-group"><div class="cm-b-group-title">{escape(title)}</div>'
        f'<table class="cm-b-table">{_GROUP_HEAD}<tbody>{"".join(_row_html(r) for r in rows)}</tbody>'
        "</table></div>"
        for title, rows in groups
    )
    return f'<div class="cm-b-scroll"><div class="cm-b-groups">{cards}</div></div>'


def champion_banner_html(team_name: str, subtitle: str | None = None) -> str:
    sub = f'<div class="cm-b-banner-sub">{escape(subtitle)}</div>' if subtitle else ""
    return (
        '<div class="cm-b-scroll"><div class="cm-b-banner"><div class="cm-b-trophy">🏆</div>'
        '<div class="cm-b-banner-text"><div class="cm-b-champ-label">Devler Arenası Şampiyonu</div>'
        f'<div class="cm-b-banner-name">{escape(team_name)}</div>{sub}</div></div></div>'
    )

