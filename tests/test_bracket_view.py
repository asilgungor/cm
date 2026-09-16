"""
Devler Arenasi gorunum testleri (8. Asama): bracket_view.py HTML parcalari.
Saf testler: veritabani ve Streamlit gerektirmez.
"""

from __future__ import annotations

import re
import sys
from html import escape
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bracket_view as bv  # noqa: E402
import cup_draw as cd  # noqa: E402
from bracket_view import (  # noqa: E402
    BallView,
    GroupRowView,
    PotView,
    RoundView,
    SlotView,
    TieView,
)

EVIL = '<script>alert("x")</script>'
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.roots = 0
        self.stray_text = ""
        self.errors: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if not self.stack:
            self.roots += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"beklenmeyen </{tag}> (yigin: {self.stack})")
            return
        self.stack.pop()

    def handle_data(self, data):
        if not self.stack:
            self.stray_text += data


def assert_fragment(html: str) -> _Balance:
    """Tek kok elemanli, dengeli, tek satirlik HTML parcasi; tasma sarmalayicisi icinde."""
    parser = _Balance()
    parser.feed(html)
    parser.close()
    assert not parser.errors, parser.errors
    assert parser.stack == [], f"kapanmayan etiketler: {parser.stack}"
    assert parser.roots == 1 and not parser.stray_text.strip()
    assert "\n" not in html, "Streamlit markdown'i icin tek satir olmali"
    assert html.startswith('<div class="cm-b-scroll">')
    assert "script" not in parser.tags
    return parser


def classes_of(html: str) -> list[str]:
    return [c for attr in re.findall(r'class="([^"]*)"', html) for c in attr.split()]


def sample_rounds() -> list[RoundView]:
    r16 = [TieView(f"Ev {i}", f"Dep {i}", legs=["1-0", "0-1"], aggregate="1-1", note="pen. 5-4",
                   winner="home") for i in range(8)]
    r16[0] = TieView(EVIL, "Bayern & Co", legs=["1-0", "1-2 uzt."], aggregate="2-2", note="pen. 4-3",
                     winner="home", highlight=True)
    qf = [TieView(EVIL, "Ev 2", legs=["2-0"], winner=None), TieView(None, "Ev 4"),
          TieView(None, None), TieView(None, None)]
    sf = [TieView(None, None), TieView(None, None)]
    final = [TieView(None, None)]
    return [RoundView("Son 16", r16), RoundView("Çeyrek Final", qf), RoundView("Yarı Final", sf),
            RoundView("Final", final)]


# ---------------------------------------------------------------------------
# Eleme agaci
# ---------------------------------------------------------------------------

def test_bracket_escapes_names_and_marks_winner_and_highlight():
    html = bv.bracket_html(sample_rounds())
    assert_fragment(html)
    assert EVIL not in html and escape(EVIL) in html
    assert "Bayern &amp; Co" in html
    for i in range(1, 8):
        assert f"Ev {i}" in html and f"Dep {i}" in html
    for label in ("Son 16", "Çeyrek Final", "Yarı Final", "Final"):
        assert escape(label) in html
    assert "1. maç 1-0" in html and "2. maç 1-2 uzt." in html
    assert "Toplam 2-2" in html and "pen. 4-3" in html
    cls = classes_of(html)
    assert cls.count("cm-b-win") == 8 and cls.count("cm-b-lose") == 8
    assert cls.count("cm-b-hl") == 1
    # kazanan satiri: vurgulu eslesmede ilk takim
    first_tie = re.search(r'<div class="cm-b-tie cm-b-hl">(.*?)</div></div>', html).group(1)
    assert first_tie.startswith('<div class="cm-b-team cm-b-win"><span class="cm-b-name">&lt;script&gt;')


def test_bracket_placeholders_for_undecided_teams():
    html = bv.bracket_html(sample_rounds())
    # QF: 1 + 2 + 2, SF: 4, Final: 2 -> 11 bos yer
    assert classes_of(html).count("cm-b-tbd") == 11
    assert html.count('<span class="cm-b-name cm-b-tbd">?</span>') == 11
    assert "cm-b-champ" not in html and "cm-b-to-champ" not in html


def test_bracket_connectors_and_rounds_structure():
    html = bv.bracket_html(sample_rounds())
    cls = classes_of(html)
    assert cls.count("cm-b-round") == 4
    assert cls.count("cm-b-in") == 3                      # ilk tur disindaki turlar soldan baglanir
    assert cls.count("cm-b-link") == 4 + 2 + 1            # ikili kutular saga baglanir, final baglanmaz
    assert cls.count("cm-b-tie") == 15
    # tek sayida eslesme: son kutu baglantisiz
    odd = bv.bracket_html([RoundView("A", [TieView("a", "b")] * 3), RoundView("B", [TieView("c", "d")] * 2)])
    assert_fragment(odd)
    assert classes_of(odd).count("cm-b-link") == 1 and classes_of(odd).count("cm-b-pair") == 2


def test_bracket_champion_card():
    html = bv.bracket_html(sample_rounds(), champion=EVIL)
    assert_fragment(html)
    cls = classes_of(html)
    assert cls.count("cm-b-champ") == 1 and "cm-b-to-champ" in cls
    assert "Şampiyon" in html and "🏆" in html
    assert EVIL not in html and html.count(escape(EVIL)) == 3
    only = bv.bracket_html([], champion="Kupa Sahibi")
    assert_fragment(only)
    assert "Kupa Sahibi" in only


def test_bracket_empty():
    html = bv.bracket_html([])
    assert_fragment(html)
    assert "Henüz" in html


# ---------------------------------------------------------------------------
# Kura tahtasi
# ---------------------------------------------------------------------------

def board_html(**kwargs) -> str:
    pots = [
        PotView("1. Torba", [BallView("GİZLİ-TOP", "closed"), BallView(EVIL, "open"), BallView("Real & Co", "glow")]),
        PotView("2. Torba", [BallView("başka gizli", "closed"), BallView("Porto", "open"),
                             BallView("bilinmeyen", "weird")]),
    ]
    slots = [
        SlotView("Eşleşme 1", ["Porto", "Real & Co"]),
        SlotView("Eşleşme 2", [EVIL, None], glow=True),
        SlotView("Grup B", [None, None, None, None]),
    ]
    return bv.draw_board_html(pots, slots, **kwargs)


def test_draw_board_glow_only_on_glow_slot_and_ball():
    html = board_html(headline="2. torba: Son 16 rakibi çekiliyor", last_step_text=f"{EVIL} → Eşleşme 2")
    assert_fragment(html)
    cls = classes_of(html)
    assert cls.count("cm-b-glow") == 2
    glow_ball = re.findall(r'<span class="cm-b-ball cm-b-glow">([^<]*)</span>', html)
    assert glow_ball == ["Real &amp; Co"]
    glow_slot = re.findall(r'<div class="cm-b-slot cm-b-glow"><div class="cm-b-slot-title">([^<]*)</div>', html)
    assert glow_slot == ["Eşleşme 2"]
    assert cls.count("cm-b-slot") == 3 and cls.count("cm-b-pot") == 2


def test_draw_board_balls_names_placeholders_and_texts():
    html = board_html(headline=f"Başlık {EVIL}", last_step_text="Son top: Porto")
    assert EVIL not in html and escape(EVIL) in html
    assert "GİZLİ-TOP" not in html and "başka gizli" not in html      # kapali topun etiketi gorunmez
    assert "bilinmeyen" not in html                                    # bilinmeyen durum -> kapali
    cls = classes_of(html)
    assert cls.count("cm-b-closed") == 3 and cls.count("cm-b-open") == 2
    assert html.count('<span class="cm-b-name cm-b-tbd">?</span>') == 5
    assert "cm-b-headline" in cls and "cm-b-announce" in cls
    assert "Son top: Porto" in html and "1. Torba" in html
    plain = board_html()
    assert_fragment(plain)
    assert "cm-b-headline" not in plain and "cm-b-announce" not in plain


def test_draw_board_from_live_session():
    """Entegrasyon ornegi: DrawSession durumundan gorunum modeli kurulur."""
    teams = [cd.CupTeam(i, f"Kulüp {i}", f"L{i % 5}", 100.0 - i) for i in range(1, 17)]
    for fmt in cd.CupFormat:
        session = cd.DrawSession(fmt, cd.make_pots(teams, fmt), seed="ui")
        for _ in range(5):
            session.draw_next()
        last = session.last_step
        drawn = {s.team_id for s in session.steps}
        pots = [
            PotView(f"{p + 1}. Torba", [
                BallView(t.name, "glow" if t.id == last.team_id else "open" if t.id in drawn else "closed")
                for t in pot
            ])
            for p, pot in enumerate(session.pots)
        ]
        slots = [
            SlotView(cd.slot_title(fmt, i), [session.team(x).name if x else None for x in slot], glow=i == last.slot)
            for i, slot in enumerate(session.slots())
        ]
        html = bv.draw_board_html(pots, slots, session.headline(), session.describe_step(last))
        assert_fragment(html)
        assert classes_of(html).count("cm-b-glow") == 2
        assert escape(session.headline()) in html


# ---------------------------------------------------------------------------
# Grup tablolari ve afis
# ---------------------------------------------------------------------------

def test_group_tables_mark_qualified_and_highlight():
    rows_a = [
        GroupRowView(1, EVIL, 6, 4, 1, 1, 10, 4, 13, qualified=True),
        GroupRowView(2, "Benim Takımım", 6, 3, 1, 2, 7, 7, 10, qualified=True, highlight=True),
        GroupRowView(3, "Üçüncü", 6, 2, 1, 3, 5, 7, 7),
        GroupRowView(4, "Dördüncü", 6, 1, 1, 4, 3, 7, 4),
    ]
    rows_b = [GroupRowView(i, f"B{i}", 0, 0, 0, 0, 0, 0, 0) for i in range(1, 5)]
    html = bv.group_tables_html([("Grup A", rows_a), ("Grup <B>", rows_b)])
    assert_fragment(html)
    assert EVIL not in html and escape(EVIL) in html and "Grup &lt;B&gt;" in html
    cls = classes_of(html)
    assert cls.count("cm-b-q") == 2 and cls.count("cm-b-hl") == 1 and cls.count("cm-b-table") == 2
    assert '<tr class="cm-b-q cm-b-hl">' in html
    assert "<td>+6</td>" in html and "<td>0</td>" in html and "<td>-4</td>" in html
    assert '<td class="cm-b-pts">13</td>' in html
    for head in ("Takım", "O", "G", "B", "M", "Av", "P"):
        assert f">{escape(head)}</th>" in html
    body_rows = re.findall(r"<tr[^>]*><td>", html)
    assert len(body_rows) == 8
    empty = bv.group_tables_html([])
    assert_fragment(empty)


def test_champion_banner():
    html = bv.champion_banner_html(EVIL, subtitle="2026/27 · Finalde 2-1 <üstünlük>")
    assert_fragment(html)
    assert EVIL not in html and escape(EVIL) in html
    assert "&lt;üstünlük&gt;" in html and "🏆" in html
    plain = bv.champion_banner_html("Galatasaray")
    assert_fragment(plain)
    assert "cm-b-banner-sub" not in plain


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

def test_css_block_prefix_theme_and_overflow():
    css = bv.BRACKET_CSS
    assert css.startswith("<style>") and css.endswith("</style>") and css.count("<style>") == 1
    assert "\n\n" not in css                                   # bos satir markdown blogunu bozar
    body = css[len("<style>"):-len("</style>")]
    selectors = set(re.findall(r"\.([A-Za-z][\w-]*)", body))
    assert selectors and all(s.startswith("cm-b-") for s in selectors), selectors
    assert ".cm-b-scroll{max-width:100%;overflow-x:auto" in css
    assert "@keyframes cm-b-pulse" in css and ".cm-b-glow{animation:cm-b-pulse" in css
    assert "prefers-reduced-motion" in css
    # tema: sabit siyah metin / beyaz zemin yok
    lowered = css.lower().replace(" ", "")
    for bad in ("color:#000", "color:black", "background:#fff", "background:white", "background:#ffffff"):
        assert bad not in lowered
    # her gorunum sayfa kaydirmasini engelleyen sarmalayiciyla baslar
    for html in (bv.bracket_html(sample_rounds(), "X"), board_html(), bv.group_tables_html([]),
                 bv.champion_banner_html("X")):
        assert html.startswith('<div class="cm-b-scroll">')
        assert all(c.startswith("cm-b-") for c in classes_of(html))
