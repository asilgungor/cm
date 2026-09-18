"""
home_view.py
============
Faz 13I: Ana Sayfa (CM 01/02'deki menajer masasi). Sirada ne var, son ne oldu, masada bekleyen isler ve "devam".
SUNUM + kucuk okuma sorgulari; veritabanina yazmaz. Callback'leri yalnizca sayfa degistirir (nav_view.goto) ya da
Transfer Merkezi'nde dosya acar (transfer_centre_view.open_file) -> requires_auth.

    render_home(db, cm, team, *, continue_action, report_lines, report_when, hub_counts, deals, manager_name)
        ust serit      : lig sirasi, puan, form, sezon / hafta, transfer butcesi
        sol            : siradaki mac karti (+ Taktik / Canli Mac kisayollari), devam dugmesi (web_app verir), son sonuc
        sag            : CM haber ekrani -- baslik menajerin adiyla, sekmeler Tumu / Mesajlar / Musabakalar / Sakatlik &
                         Cezalar, tarihli liste (S1 H3) ve secilen mesajin govdesi + eylemi. Kaynaklar: yanit bekleyen
                         transfer dosyalari (gelen teklif, karsi teklif, sozlesme, saglik, tamamlama), suresi dolan
                         dosyalar, okunmamis mesaj / bildirim (paylasilan dunya), sakat / cezali oyuncular, maas talepleri,
                         kondisyonu dusuk ilk 11, son haftanin raporu (sonuclar, sakatlik / ceza, masa notlari)
    team_fixtures(db, team_id, season)   kulubun fiksturu (lig + kupa) TEK sorguyla (iki takim adi JOIN)

Widget anahtarlari: home_inbox_tab (sekme), home_msg_{n} (liste satiri: secer), home_go (secilen mesajin eylemi),
home_prep / home_live (siradaki mac kisayollari); devam dugmesi web_app'in verdigi anahtarla (home_continue /
home_new_season / home_live).
Sorgu butcesi: sayfa basina sabit (~8), oyuncu / dosya sayisindan bagimsiz (N+1 yok).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st
from sqlalchemy import case, or_, select
from sqlalchemy.orm import aliased

import nav_view
import reputation
from cup_draw import STAGE_LABELS, Stage
from finance import format_money
from models import Competition, Fixture, FixtureStatus, GameMode, LineupStatus, Team
from ofm_theme import panel_title_html, stat_strip_html
from web_common import md_escape, requires_auth, reset_widgets, show_flash

AREA = "home"
FORM_ICONS = {"G": "🟩", "B": "🟨", "M": "🟥"}
INBOX_LIMIT = 8


# ===========================================================================
# FIKSTUR SATIRLARI (tek sorgu)
# ===========================================================================

@dataclass(frozen=True)
class FixtureLine:
    id: int
    season: int
    week: int
    cup: bool
    stage: str | None
    leg: int | None
    home: str
    away: str
    home_id: int
    away_id: int
    at_home: bool
    played: bool
    home_score: int | None
    away_score: int | None
    home_pens: int | None
    away_pens: int | None
    extra_time: bool

    @property
    def opponent(self) -> str:
        return self.away if self.at_home else self.home

    @property
    def opponent_id(self) -> int:
        return self.away_id if self.at_home else self.home_id

    @property
    def competition_label(self) -> str:
        if not self.cup:
            return "🏆 Lig"
        try:
            stage = STAGE_LABELS[Stage(self.stage)] if self.stage else ""
        except ValueError:
            stage = str(self.stage)
        leg = f" ({self.leg}. maç)" if self.leg and self.stage != Stage.FINAL.value else ""
        return "⭐ Devler Arenası" + (f" · {stage}{leg}" if stage else "")

    @property
    def score_text(self) -> str:
        if not self.played:
            return "—"
        text = f"{self.home_score} - {self.away_score}"
        if self.extra_time:
            text += " (uzt.)"
        if self.home_pens is not None and self.away_pens is not None:
            text += f" (pen. {self.home_pens}-{self.away_pens})"
        return text

    @property
    def result(self) -> str | None:
        """G / B / M (kendi takimina gore; penaltilar kazanani belirler)."""
        if not self.played or self.home_score is None or self.away_score is None:
            return None
        mine, theirs = ((self.home_score, self.away_score) if self.at_home else (self.away_score, self.home_score))
        if mine == theirs and self.home_pens is not None and self.away_pens is not None:
            mine, theirs = ((self.home_pens, self.away_pens) if self.at_home else (self.away_pens, self.home_pens))
        return "G" if mine > theirs else "M" if mine < theirs else "B"


def team_fixtures(db, team_id: int, season: int | None = None, *, played: bool | None = None,
                  newest_first: bool = False, limit: int | None = None) -> list[FixtureLine]:
    """Kulubun maclari (lig + kupa), hafta sirasiyla (ayni haftada kupa hafta ici: once). TEK sorgu."""
    home_t, away_t = aliased(Team), aliased(Team)
    cup_first = case((Fixture.competition == Competition.CUP, 0), else_=1)
    stmt = (select(Fixture, home_t.name, away_t.name)
            .join(home_t, home_t.id == Fixture.home_team_id)
            .join(away_t, away_t.id == Fixture.away_team_id)
            .where(or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id)))
    if season is not None:
        stmt = stmt.where(Fixture.season == season)
    if played is not None:
        stmt = stmt.where(Fixture.status == (FixtureStatus.PLAYED if played else FixtureStatus.UNPLAYED))
    if newest_first:
        stmt = stmt.order_by(Fixture.season.desc(), Fixture.week.desc(), cup_first.desc(), Fixture.id.desc())
    else:
        stmt = stmt.order_by(Fixture.season, Fixture.week, cup_first, Fixture.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    lines = []
    for fx, home, away in db.execute(stmt).all():
        lines.append(FixtureLine(
            id=fx.id, season=int(fx.season), week=int(fx.week), cup=fx.competition is Competition.CUP,
            stage=fx.stage, leg=fx.leg, home=home, away=away, home_id=fx.home_team_id, away_id=fx.away_team_id,
            at_home=fx.home_team_id == team_id,
            played=fx.status is FixtureStatus.PLAYED, home_score=fx.home_score, away_score=fx.away_score,
            home_pens=fx.home_penalties, away_pens=fx.away_penalties, extra_time=bool(fx.extra_time)))
    return lines


def form_text(form: str) -> str:
    return " ".join(FORM_ICONS.get(letter, letter) for letter in form) if form else "—"


# ===========================================================================
# GELEN KUTUSU
# ===========================================================================

CAT_ALL, CAT_MESSAGES, CAT_COMPETITIONS, CAT_INJURIES = "Tümü", "Mesajlar", "Müsabakalar", "Sakatlık & Cezalar"
INBOX_TABS = (CAT_ALL, CAT_MESSAGES, CAT_COMPETITIONS, CAT_INJURIES)
INBOX_TAB_KEY, INBOX_SEL_KEY = "home_inbox_tab", "home_msg"


@dataclass(frozen=True)
class InboxItem:
    """CM haber kutusu satiri: tarih + baslik (liste), govde (secilince), eylem (sayfa / dosya)."""
    uid: str
    icon: str
    text: str                       # baslik (duz metin; cizimde kacisli)
    category: str = CAT_MESSAGES
    when: str = ""
    body: tuple[str, ...] = ()      # secilen mesajin govdesi (duz metin satirlari)
    button: str | None = None
    target: str | None = None       # nav_view slug
    deal_id: int | None = None
    direction: str | None = None
    tone: str = "info"              # info / warning / error / success


def _deal_body(row) -> tuple[str, ...]:
    lines = [f"Aşama: {row.status_label}"]
    if row.terms_text:
        lines.append(f"Paket: {row.terms_text}")
    if row.mood:
        lines.append(row.mood)
    if row.expires_in_weeks is not None:
        lines.append("Bu hafta düşer." if row.expires_in_weeks == 0 else f"{row.expires_in_weeks} hafta içinde düşer.")
    if row.reason:
        lines.append(row.reason)
    return tuple(lines)


def deal_item(row, when: str = "") -> InboxItem | None:
    """transfer_desk.DealSummary -> gelen kutusu satiri (yalnizca menajerin isi varsa)."""
    name = row.player_name
    body = _deal_body(row)

    def item(icon, text, button, tone="info"):
        return InboxItem(f"deal:{row.id}", icon, text, CAT_MESSAGES, when, body, button, nav_view.TRANSFER, row.id,
                         row.direction, tone)

    if row.direction == "OUT":
        if row.can_accept_offer:
            return item("📥", f"{row.buyer_team} {name} için teklif yaptı", "📥 Teklifi aç", "warning")
        return None
    if row.status == "BIDDING" and row.turn == "MANAGER":
        if row.last_action == "COUNTER":
            return item("↔️", f"{row.seller_team} {name} için karşı teklif yaptı", "📂 Dosyayı aç", "warning")
        return item("✖️", f"{row.seller_team} {name} teklifini geri çevirdi", "📂 Dosyayı aç")
    if row.status == "TERMS":
        return item("✍️", f"{name}: kişisel şartlar seni bekliyor", "✍️ Sözleşme masası", "warning")
    if row.status == "MEDICAL":
        return item("🩺", f"{name}: sağlık kontrolü riskli, kararını bekliyor", "📂 Dosyayı aç", "warning")
    if row.status == "AGREED" and row.needs_action:
        return item("✅", f"{name}: her konuda anlaşıldı, transferi tamamla", "✅ Tamamla", "success")
    if row.status == "AGREED":
        return item("⏳", f"{name}: anlaşma tamam, dönem açılınca tamamlanacak", "📂 Dosyayı aç")
    return None


def report_items(lines: Sequence[tuple[str, str]], when: str) -> list[InboxItem]:
    """Hafta raporu -> haber kutusu: sonuclar (Musabakalar), sakatlik / ceza (Sakatlik & Cezalar), masa notlari
    (Mesajlar). Metinler career_views.week_report_lines ciktisidir."""
    results = [text for kind, text in lines if kind in ("result", "info", "season")]
    items: list[InboxItem] = []
    if results:
        items.append(InboxItem("report:week", "📅", results[0], CAT_COMPETITIONS, when, tuple(results[1:]) or
                               (results[0],), "📅 Fikstür & Sonuçlar", nav_view.FIXTURES))
    for i, (kind, text) in enumerate(lines):
        if kind in ("injury", "ban"):
            items.append(InboxItem(f"report:{kind}:{i}", "🚑" if kind == "injury" else "🟥", text, CAT_INJURIES,
                                   when, (text,)))
        elif kind == "desk":
            items.append(InboxItem(f"report:desk:{i}", "🔄", text.removeprefix("🔄 "), CAT_MESSAGES, when, (text,),
                                   "🔄 Transfer Merkezi", nav_view.TRANSFER))
        elif kind in ("concern", "youth", "growth"):
            items.append(InboxItem(f"report:{kind}:{i}", "📋", text, CAT_MESSAGES, when, (text,), "📋 Kadro",
                                   nav_view.SQUAD))
    return items


def inbox_items(cm, team: Team, deals: Sequence, hub_counts=None,
                report_lines: Sequence[tuple[str, str]] | None = None, report_when: str = "") -> list[InboxItem]:
    """
    Ana sayfanin haber kutusu (CM: tarihli liste + secilen mesajin govdesi). Sira: yanit bekleyen transfer isleri,
    menajer mesajlari, kadro durumu, son haftanin raporu. Oyuncular iliskiden (tek sorgu); N+1 yok.
    """
    now = f"S{cm.season} H{cm.current_week}"
    items: list[InboxItem] = []
    seen: set[int] = set()
    for row in deals:
        item = deal_item(row, now)
        if item is not None:
            items.append(item)
            seen.add(row.id)
    for row in deals:
        if row.id not in seen and row.expires_in_weeks is not None and row.expires_in_weeks <= 1:
            items.append(InboxItem(f"deal:{row.id}", "⌛", f"{row.player_name} dosyası "
                                   + ("bu hafta" if row.expires_in_weeks == 0 else "gelecek hafta") + " düşüyor",
                                   CAT_MESSAGES, now, _deal_body(row), "📂 Dosyayı aç", nav_view.TRANSFER, row.id,
                                   row.direction))
    if hub_counts is not None:
        waiting = int(getattr(hub_counts, "offers_action", 0) or 0)
        unread = int(getattr(hub_counts, "messages", 0) or 0)
        notes = int(getattr(hub_counts, "notifications", 0) or 0)
        if waiting or unread or notes:
            parts = [f"{waiting} teklif yanıt bekliyor" if waiting else "", f"{unread} okunmamış mesaj" if unread else "",
                     f"{notes} okunmamış bildirim" if notes else ""]
            text = " · ".join(p for p in parts if p)
            items.append(InboxItem("hub", "📨", text, CAT_MESSAGES, now, (text,), "📨 Teklifler & Mesajlar",
                                   nav_view.INBOX, tone="warning" if waiting else "info"))
    week = cm.current_week
    players = list(team.players)
    absent = [(p.name, p.unavailability_reason(week)) for p in players]
    absent = [(name, reason) for name, reason in absent if reason]
    if absent:
        items.append(InboxItem("squad:absent", "🚑", f"{len(absent)} oyuncu sakat / cezalı", CAT_INJURIES, now,
                               tuple(f"{name}: {reason}" for name, reason in absent), "📋 Kadro", nav_view.SQUAD,
                               tone="warning"))
    demands = [p for p in players if p.wage_demand]
    if demands:
        items.append(InboxItem("squad:wages", "✍️", f"{len(demands)} oyuncu yeni sözleşme istiyor", CAT_MESSAGES, now,
                               tuple(f"{p.name}: haftalık {format_money(p.wage_demand)} istiyor" for p in demands),
                               "📋 Kadro", nav_view.SQUAD, tone="warning"))
    tired = [p for p in players if p.lineup_status is LineupStatus.XI and int(p.condition or 100) < 75]
    if tired:
        items.append(InboxItem("squad:tired", "🔋", "İlk 11'de kondisyonu düşük oyuncular", CAT_MESSAGES, now,
                               tuple(f"{p.name}: kondisyon %{int(p.condition)}" for p in tired), "📋 Kadro",
                               nav_view.SQUAD))
    items += report_items(report_lines or (), report_when)
    return items


@requires_auth
def cb_home_go(target: str, deal_id: int | None = None, direction: str | None = None) -> None:
    """Gelen kutusu kisayolu: sayfaya gider; transfer dosyasiysa dosya acilir (yalnizca oturum durumu)."""
    if deal_id is not None and target == nav_view.TRANSFER:
        import transfer_centre_view

        transfer_centre_view.open_file(int(deal_id), str(direction or "IN"))
    nav_view.goto(target)


@requires_auth
def cb_home_select(uid: str) -> None:
    st.session_state[INBOX_SEL_KEY] = str(uid)


def inbox_panel(items: list[InboxItem], manager_name: str) -> None:
    """CM haber ekrani: baslik menajerin adiyla, sekmeler, tarihli liste, secilen mesajin govdesi ve eylemi."""
    st.markdown(nav_view.name_title_html(f"{manager_name} · Haberler"), unsafe_allow_html=True)
    if st.session_state.get(INBOX_TAB_KEY) not in INBOX_TABS:
        reset_widgets(INBOX_TAB_KEY)
    counts = {tab: sum(1 for i in items if tab == CAT_ALL or i.category == tab) for tab in INBOX_TABS}
    st.segmented_control("Haberler", list(INBOX_TABS), key=INBOX_TAB_KEY, required=True, default=CAT_ALL,
                         format_func=lambda t: f"{t} ({counts[t]})" if counts[t] else t,
                         label_visibility="collapsed", width="stretch")
    tab = st.session_state.get(INBOX_TAB_KEY) or CAT_ALL
    shown = [i for i in items if tab == CAT_ALL or i.category == tab]
    if not shown:
        st.success("Masan temiz: bu bölümde haber yok.")
        return
    selected = next((i for i in shown if i.uid == st.session_state.get(INBOX_SEL_KEY)), shown[0])
    with st.container(key="home_msglist"):
        for n, item in enumerate(shown[:INBOX_LIMIT]):
            label = f"{item.when} · {item.icon} {item.text}"
            st.button(label if len(label) <= 90 else label[:88] + "…", key=f"home_msg_{n}", on_click=cb_home_select,
                      args=(item.uid,), width="stretch", type="primary" if item is selected else "secondary")
    if len(shown) > INBOX_LIMIT:
        st.caption(f"+{len(shown) - INBOX_LIMIT} haber daha: ilgili sayfalarda.")
    with st.container(border=True, key="home_msgbody"):
        st.markdown(f"**{selected.icon} {md_escape(selected.text)}**")
        if selected.when:
            st.caption(selected.when)
        for line in selected.body:
            if line != selected.text:
                st.markdown("- " + md_escape(line))
        if selected.button:
            st.button(selected.button, key="home_go", on_click=cb_home_go,
                      args=(selected.target, selected.deal_id, selected.direction),
                      type="primary" if selected.tone in ("warning", "success") else "secondary")


# ===========================================================================
# SAYFA
# ===========================================================================

def _card(title: str, value: str, sub: str = "") -> str:
    return (f'<div class="ofm-card"><div class="k">{escape(title)}</div><div class="v">{value}</div>'
            + (f'<div class="s">{escape(sub)}</div>' if sub else "") + "</div>")


def next_match_card(cm, team: Team, nxt: FixtureLine | None) -> None:
    if nxt is None:
        st.markdown(_card("Sıradaki maç", escape("Bu sezon oynanacak maç kalmadı"),
                          "Sezon bitince yeni sezonu başlat." if cm.season_finished else ""), unsafe_allow_html=True)
        return
    venue = "İç saha" if nxt.at_home else "Deplasman"
    value = f"{escape(nxt.home)} <b>–</b> {escape(nxt.away)}"
    form = cm.team_form(nxt.opponent_id) if not nxt.cup else ""
    sub = f"{nxt.competition_label} · {nxt.week}. hafta · {venue}" + (f" · rakip formu {form}" if form else "")
    st.markdown(_card("Sıradaki maç", value, sub), unsafe_allow_html=True)



def last_result_card(last: FixtureLine | None) -> None:
    if last is None:
        st.markdown(_card("Son sonuç", escape("Henüz maç oynanmadı")), unsafe_allow_html=True)
        return
    icon = FORM_ICONS.get(last.result or "", "")
    value = f"{escape(last.home)} <b>{escape(last.score_text)}</b> {escape(last.away)}"
    st.markdown(_card("Son sonuç", value, f"{icon} {last.competition_label} · Sezon {last.season}, {last.week}. hafta"),
                unsafe_allow_html=True)


def render_home(db, cm, team: Team, *, continue_action: Callable[[str], None],
                report_lines: Sequence[tuple[str, str]] | None = None, report_when: str = "",
                hub_counts=None, deals: Sequence | None = None, manager_name: str = "Menajer") -> None:
    """Ana Sayfa. continue_action(key): web_app'in devam / hazir dugmesi (anahtar on eki alir)."""
    show_flash(AREA)
    tournament = cm.game_mode is GameMode.TOURNAMENT
    total = cm.total_weeks()
    strip = []
    if not tournament and team.league_id is not None:
        table = cm.standings(team.league_id)
        position = next((i for i, t in enumerate(table, start=1) if t.id == team.id), None)
        strip += [("Lig sırası", f"{position}. / {len(table)}" if position else "—"), ("Puan", team.points),
                  ("Form", form_text(cm.team_form(team.id)))]
    strip.append(("Sezon · Hafta", f"{cm.season} · {min(cm.current_week, total)}/{total}"))
    if not tournament:
        strip.append(("Transfer bütçesi", format_money(team.transfer_budget)))
    lvl = reputation.level(cm.manager_reputation)
    strip.append(("Menajer", f"{reputation.badge(lvl)} {lvl.title}"))
    st.markdown(stat_strip_html(strip), unsafe_allow_html=True)

    upcoming = team_fixtures(db, team.id, cm.season, played=False, limit=1)
    recent = team_fixtures(db, team.id, None, played=True, newest_first=True, limit=1)
    left, right = st.columns([2, 3], gap="large")
    with left:
        st.markdown(panel_title_html("Maç masası"), unsafe_allow_html=True)
        next_match_card(cm, team, upcoming[0] if upcoming else None)
        a, b = st.columns(2)
        a.button("🎯 Maç önü", key="home_prep", on_click=cb_home_go, args=(nav_view.TACTICS,),
                 width="stretch", disabled=tournament, help="Taktik › Maç önü raporu ve rakip gözlem raporu")
        b.button("🏟️ Canlı yönet", key="home_live", on_click=cb_home_go, args=(nav_view.MATCH,), width="stretch",
                 help="Haftanın maçını canlı yönet: durdur, değişiklik yap, talimat ver.")
        continue_action("home")
        last_result_card(recent[0] if recent else None)
    with right:
        inbox_panel(inbox_items(cm, team, deals or (), hub_counts, report_lines, report_when), manager_name)


__all__ = ["AREA", "FixtureLine", "InboxItem", "cb_home_go", "cb_home_select", "deal_item", "form_text",
           "inbox_items", "inbox_panel", "render_home", "report_items", "team_fixtures"]
