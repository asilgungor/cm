"""
home_view.py
============
Faz 13I / 14S / 15D-U: Gelen Kutusu (CM 01/02 haber ekrani) + mac masasi. Masada bekleyen isler, sirada ne var,
son ne oldu ve "devam". (Slug "ana-sayfa" 13I'den kalir; menude "Gelen Kutusu (n)" -- n OKUNMAMIS mesaj sayisi.)

15D-U: haber listesi artik KALICI gelen kutusundan gelir (inbox.Inbox; tablo inbox_messages). Sayfa yenilense de
mesajlar durur, okundu / arsiv durumu veritabanindadir, her mesajin tarihi gercek takvim tarihidir
("Cumartesi 2.08.25") ve her mesaj tek tikla kendi sayfasina gider (InboxView.page / page_param). Oturum
durumundaki eski "last_week_lines" yolu KALDIRILDI: hafta raporu da bir mesajtir (inbox.KIND_WEEK_REPORT).

Iki tur satir vardir ve ikisi ayni listede toplanir:
  * KALICI mesajlar  (inbox.InboxView -> message_item): mac sonucu, sakatlik / ceza, transfer, sozlesme, akademi,
                     odul, hafta raporu. Okundu / okunmadi / arsiv burada tutulur.
  * CANLI satirlar   (deal_item / contract_items / kadro durumu / paylasilan dunya sayaclari): "su an masanda
                     bekleyen is". Veritabaninda satiri yoktur; her cizimde yeniden hesaplanir, hep okunmus sayilir
                     ve listenin basinda durur.

    render_home(db, cm, team, *, continue_action, hub_counts, deals, manager_name, contract_rows)
        yonetim satiri : 15C-U -- sezon hedefi, lig sirasi, yonetimin guveni (SOZCUK), uyari, bekleyen is teklifi
                         + "Yönetim kurulu" dugmesi (board_view.board_summary; kural kapaliyken cizilmez).
                         `team` None olabilir (kovulan / istifa eden menajer): mac masasi yerine bu satir ve Devam
        ust serit      : lig sirasi, puan, form, sezon / hafta, transfer butcesi
        sol            : siradaki mac karti (+ Taktik / Canli Mac kisayollari), devam dugmesi (web_app verir), son sonuc
        sag            : CM haber ekrani -- sekmeler Tumu / Mesajlar / Yarismalar / Sakatlik ve Cezalar, tarihli liste
                         ve secilen mesajin govdesi + eylemi
    team_fixtures(db, team_id, season)   kulubun fiksturu (lig + kupa) TEK sorguyla (iki takim adi JOIN)

Widget anahtarlari: home_inbox_tab (sekme), home_msg_{n} (liste satiri: secer + okundu sayar), home_go (secilen
mesajin tek tik eylemi), home_arch (arsivle), home_unread (okunmadi say), home_read_all (tumunu okundu say),
home_clear (okunmuslari arsivle), home_more (daha eski mesajlar), home_prep / home_live (siradaki mac
kisayollari), home_board_go (15C-U: Yonetim sayfasi); devam dugmesi web_app'in verdigi anahtarla
(home_continue / home_new_season / home_live).
Sorgu butcesi: sayfa basina sabit (~10; gelen kutusu iki sorgu: liste + sayaclar), oyuncu / mesaj sayisindan
bagimsiz (N+1 yok).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st
from sqlalchemy import case, or_, select
from sqlalchemy.orm import aliased

import board_view
import continue_view
import inbox
import links_view as lk
import nav_view
from cup_draw import STAGE_LABELS, Stage
from database import session_scope
from finance import format_money
from models import Competition, Fixture, FixtureStatus, GameMode, LineupStatus, Team
from ofm_theme import panel_title_html
from web_common import (
    flash,
    manager,
    md_escape,
    member_callback,
    requires_auth,
    reset_widgets,
    show_flash,
)

log = logging.getLogger(__name__)

AREA = "home"
FORM_ICONS = {"G": "G", "B": "B", "M": "M"}          # 14S: CM gibi harf (emoji yok)
INBOX_LIMIT = 12                    # ilk cizimde gosterilen satir (home_more ile artar)
INBOX_STEP = 12
FETCH_LIMIT = 120                   # veritabanindan okunan en fazla mesaj (sekme basina)


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
            return "Lig"
        try:
            stage = STAGE_LABELS[Stage(self.stage)] if self.stage else ""
        except ValueError:
            stage = str(self.stage)
        leg = f" ({self.leg}. maç)" if self.leg and self.stage != Stage.FINAL.value else ""
        return "Devler Arenası" + (f" · {stage}{leg}" if stage else "")

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
    return "".join(FORM_ICONS.get(letter, letter) for letter in form) if form else "—"


# ===========================================================================
# GELEN KUTUSU
# ===========================================================================

# CM 01/02 sekme satiri: All / Messages / Competitions / Injuries and Bans. "Tümü" bir kategori DEGIL, birlesimdir
# (inbox.ALL_LABEL). Kategori kodlari inbox.py'den gelir; etiketler arayuzun sozlugudur (menudeki "Yarışmalar"la ayni
# sozcuk kullanilir -- inbox.CATEGORY_LABELS orada "Müsabakalar" der).
CAT_ALL = "ALL"
CAT_MESSAGES, CAT_COMPETITIONS, CAT_INJURIES = inbox.CAT_MESSAGE, inbox.CAT_COMPETITION, inbox.CAT_INJURY
INBOX_TABS = (CAT_ALL, CAT_MESSAGES, CAT_COMPETITIONS, CAT_INJURIES)
TAB_LABELS: dict[str, str] = {CAT_ALL: inbox.ALL_LABEL, CAT_MESSAGES: "Mesajlar", CAT_COMPETITIONS: "Yarışmalar",
                              CAT_INJURIES: "Sakatlık ve Cezalar"}
INBOX_TAB_KEY, INBOX_SEL_KEY, INBOX_SHOWN_KEY = "home_inbox_tab", "home_msg", "home_shown"

# Mesajin "tek tik" dugmesinin etiketi (InboxView.page -> dugme). Bilinmeyen sayfa: dugme cizilmez.
PAGE_BUTTONS: dict[str, str] = {
    nav_view.PLAYER: "Oyuncuyu aç", nav_view.CLUB_PAGE: "Kulübü aç", nav_view.TABLE: "Puan Durumu",
    nav_view.FIXTURES: "Fikstür ve Sonuçlar", nav_view.TRANSFER: "Transfer Merkezi",
    nav_view.INBOX: "Teklifler ve Mesajlar", nav_view.ARENA: "Devler Arenası",
    nav_view.NEWS: "Haberler ve Tarih", nav_view.SQUAD: "Kadro",
}


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
    players: tuple[tuple[int, str], ...] = ()      # 14F: govdedeki oyuncular (-> oyuncu sayfasi)
    contract_tab: str | None = None                # 15A: Transfer Merkezi › Sözleşmeler alt bolumu
    # --- 15D: kalici mesaj alanlari (canli satirlarda bos)
    message_id: int | None = None   # inbox_messages.id -- okundu / arsiv bu satirda calisir
    read: bool = True
    important: bool = False
    kind_label: str = ""            # inbox.KIND_LABELS ("Hafta raporu", "Sakatlık", ...)
    date_long: str = ""             # "2 Ağustos 2025 Cumartesi"
    param: int | str | None = None  # hedef sayfanin parametresi (InboxView.page_param)


def plain_text(text: str) -> str:
    """Klasik: bastaki simge / emoji sozcukleri atilir ("🎓 Akademi: ..." -> "Akademi: ...")."""
    words = str(text).split(" ")
    while len(words) > 1 and words[0] and not any(ch.isalnum() for ch in words[0]):
        words.pop(0)
    return " ".join(words)


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
            return item("📥", f"{row.buyer_team} {name} için teklif yaptı", "Teklifi aç", "warning")
        return None
    if row.status == "BIDDING" and row.turn == "MANAGER":
        if row.last_action == "COUNTER":
            return item("↔️", f"{row.seller_team} {name} için karşı teklif yaptı", "Dosyayı aç", "warning")
        return item("✖️", f"{row.seller_team} {name} teklifini geri çevirdi", "Dosyayı aç")
    if row.status == "TERMS":
        return item("✍️", f"{name}: kişisel şartlar seni bekliyor", "Sözleşme masası", "warning")
    if row.status == "MEDICAL":
        return item("🩺", f"{name}: sağlık kontrolü riskli, kararını bekliyor", "Dosyayı aç", "warning")
    if row.status == "AGREED" and row.needs_action:
        return item("✅", f"{name}: her konuda anlaşıldı, transferi tamamla", "Tamamla", "success")
    if row.status == "AGREED":
        return item("⏳", f"{name}: anlaşma tamam, dönem açılınca tamamlanacak", "Dosyayı aç")
    return None


CONTRACT_BUTTON = "Sözleşmeler"
CONTRACT_TAB = "Kadrom"                 # transfer_centre_view.C_MINE (bilinmeyen deger orada Kadrom'a duser)


def contract_items(rows: Sequence, when: str) -> list[InboxItem]:
    """
    15A: sozlesme uyarilari (transfer_desk.ContractDesk.contracts satirlari; bayrak kapaliyken cagiran bos liste verir).
    Imza bekleyen ve ayrilan oyuncular TEK TEK, sozlesmesi bitenler tek bir "Sözleşmesi bu sezon bitenler" haberinde.
    Her haber Transfer Merkezi › Sözleşmeler'e baglanir; govdedeki adlar oyuncu sayfasina.
    """
    import contracts as crules

    items: list[InboxItem] = []
    for row in rows:
        if row.status == crules.ROW_AGREED:
            items.append(InboxItem(f"contract:sign:{row.player_id}", "✍️",
                                   f"{row.name}: sözleşmede anlaşıldı, imza bekliyor", CAT_MESSAGES, when,
                                   (f"Bitiş: sezon {row.expires_season}",
                                    f"Şu anki maaş: {format_money(row.wage)}/hafta"),
                                   CONTRACT_BUTTON, nav_view.TRANSFER, tone="success",
                                   players=((row.player_id, row.name),), contract_tab=CONTRACT_TAB))
        elif row.status == crules.ROW_LEAVING:
            items.append(InboxItem(f"contract:leaving:{row.player_id}", "📄",
                                   f"{row.name} ön sözleşme imzaladı: sezon sonunda ayrılıyor", CAT_MESSAGES, when,
                                   (row.reason or f"Yeni kulübü: {row.other_team or '—'}",
                                    "Artık satılamaz ve sözleşmesi yenilenemez."),
                                   CONTRACT_BUTTON, nav_view.TRANSFER, tone="warning",
                                   players=((row.player_id, row.name),), contract_tab=CONTRACT_TAB))
    expiring = [r for r in rows if r.status in (crules.ROW_EXPIRING, crules.ROW_REFUSED, crules.ROW_TALKS)
                and not r.in_academy]
    if expiring:
        waiting = sum(1 for r in expiring if r.status == crules.ROW_EXPIRING)
        items.append(InboxItem("contract:expiring", "📄",
                               f"Sözleşmesi bu sezon bitenler: {len(expiring)} oyuncu", CAT_MESSAGES, when,
                               tuple(f"{r.name} ({r.position}, {r.age}) — {r.status_label}"
                                     + (f": {r.attitude_label}" if r.attitude_label else "") for r in expiring[:12]),
                               CONTRACT_BUTTON, nav_view.TRANSFER, tone="warning" if waiting else "info",
                               players=tuple((r.player_id, r.name) for r in expiring[:8]), contract_tab=CONTRACT_TAB))
    return items


# ---------------------------------------------------------------------------
# 15D: kalici mesajlar (inbox.InboxView -> liste satiri)
# ---------------------------------------------------------------------------

def message_body(view) -> tuple[str, ...]:
    """Mesajin govde satirlari: yapisal ek (hafta raporunun [[tur, metin], ...] satirlari) varsa o, yoksa govde."""
    lines: list[str] = []
    for pair in view.lines or ():
        if isinstance(pair, list | tuple) and len(pair) >= 2:
            text = plain_text(str(pair[1])).strip()
            if text:
                lines.append(text)
    if not lines:
        lines = [plain_text(line).strip() for line in str(view.body or "").split("\n") if line.strip()]
    return tuple(lines) or (view.subject,)


def message_item(view) -> InboxItem:
    """inbox.InboxView -> gelen kutusu satiri. "Tek tik" hedefi mesajda hazirdir (page / page_param)."""
    target = view.page if view.page in nav_view.PAGES else None
    deal_id = int(view.ref_id) if (view.ref_type == inbox.REF_DEAL and view.ref_id is not None) else None
    return InboxItem(
        uid=f"msg:{view.id}", icon=view.icon, text=view.subject, category=view.category,
        when=inbox.short_date(view.date), body=message_body(view),
        button=PAGE_BUTTONS.get(target or ""), target=target, deal_id=deal_id,
        direction="IN" if deal_id is not None else None,
        tone="warning" if view.important else "info",
        message_id=int(view.id), read=bool(view.read), important=bool(view.important),
        kind_label=view.kind_label, date_long=view.date_long, param=view.page_param)


def message_items(views: Sequence) -> list[InboxItem]:
    return [message_item(view) for view in views]


def today_label(cm) -> str:
    """Canli satirlarin tarihi: oynanacak haftanin gercek mac gunu ("2.08.25")."""
    try:
        return inbox.short_date(cm.game_date())
    except Exception:                          # takvim cizilemezse eski etiket (sayfa asla dusmez)
        return f"S{cm.season} H{cm.current_week}"


def live_items(cm, team: Team | None, deals: Sequence, hub_counts=None,
               contract_rows: Sequence | None = None) -> list[InboxItem]:
    """
    "Su an masanda bekleyen isler" (kalici mesaj DEGIL; her cizimde yeniden hesaplanir): yanit bekleyen transfer
    dosyalari, 15A sozlesme uyarilari, paylasilan dunya sayaclari, sakat / cezali ve maas isteyen oyuncular,
    kondisyonu dusuk ilk 11. Oyuncular iliskiden (tek sorgu); N+1 yok.
    15C-U: kulupsuz menajerde (kovulma / istifa) kadro satirlari hic hesaplanmaz.
    """
    now = today_label(cm)
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
                                   CAT_MESSAGES, now, _deal_body(row), "Dosyayı aç", nav_view.TRANSFER, row.id,
                                   row.direction))
    if hub_counts is not None:
        waiting = int(getattr(hub_counts, "offers_action", 0) or 0)
        unread = int(getattr(hub_counts, "messages", 0) or 0)
        notes = int(getattr(hub_counts, "notifications", 0) or 0)
        if waiting or unread or notes:
            parts = [f"{waiting} teklif yanıt bekliyor" if waiting else "", f"{unread} okunmamış mesaj" if unread else "",
                     f"{notes} okunmamış bildirim" if notes else ""]
            text = " · ".join(p for p in parts if p)
            items.append(InboxItem("hub", "📨", text, CAT_MESSAGES, now, (text,), "Teklifler ve Mesajlar",
                                   nav_view.INBOX, tone="warning" if waiting else "info"))
    items += contract_items(contract_rows or (), now)          # 15A: sozlesme uyarilari (imza, ayrilan, bitenler)
    if team is None:                                           # 15C-U: kulupsuz menajer (kadro yok)
        return items
    week = cm.current_week
    players = list(team.players)
    absent = [(p, p.unavailability_reason(week)) for p in players]
    absent = [(p, reason) for p, reason in absent if reason]
    if absent:
        items.append(InboxItem("squad:absent", "🚑", f"{len(absent)} oyuncu sakat / cezalı", CAT_INJURIES, now,
                               tuple(f"{p.name}: {reason}" for p, reason in absent), "Kadro", nav_view.SQUAD,
                               tone="warning", players=tuple((p.id, p.name) for p, _r in absent)))
    demands = [p for p in players if p.wage_demand]
    if demands:
        items.append(InboxItem("squad:wages", "✍️", f"{len(demands)} oyuncu yeni sözleşme istiyor", CAT_MESSAGES, now,
                               tuple(f"{p.name}: haftalık {format_money(p.wage_demand)} istiyor" for p in demands),
                               "Kadro", nav_view.SQUAD, tone="warning", players=tuple((p.id, p.name) for p in demands)))
    tired = [p for p in players if p.lineup_status is LineupStatus.XI and int(p.condition or 100) < 75]
    if tired:
        items.append(InboxItem("squad:tired", "🔋", "İlk 11'de kondisyonu düşük oyuncular", CAT_MESSAGES, now,
                               tuple(f"{p.name}: kondisyon %{int(p.condition)}" for p in tired), "Kadro",
                               nav_view.SQUAD, players=tuple((p.id, p.name) for p in tired)))
    return items


def inbox_items(cm, team: Team | None, deals: Sequence, hub_counts=None, contract_rows: Sequence | None = None,
                messages: Sequence | None = None) -> list[InboxItem]:
    """Gelen kutusunun satirlari: once bekleyen isler (canli), sonra kalici mesajlar (en yeni ustte)."""
    return live_items(cm, team, deals, hub_counts, contract_rows) + message_items(messages or ())


# ---------------------------------------------------------------------------
# CALLBACK'LER (yalnizca okundu / arsiv yazar; oyun durumuna dokunmaz)
# ---------------------------------------------------------------------------

@requires_auth
def cb_home_go(target: str, deal_id: int | None = None, direction: str | None = None,
               contract_tab: str | None = None, param: int | str | None = None) -> None:
    """
    Gelen kutusunun "tek tik" eylemi: mesajin sayfasina gider (InboxView.page / page_param); transfer dosyasi
    ya da sozlesme bolumu one getirilir. Yalnizca oturum durumu.
    """
    if target == nav_view.TRANSFER and (deal_id is not None or contract_tab):
        import transfer_centre_view

        if contract_tab:
            transfer_centre_view.open_contracts(str(contract_tab))
        else:
            transfer_centre_view.open_file(int(deal_id), str(direction or "IN"))
    if target == nav_view.PLAYER and param is not None:
        nav_view.cb_open_player(param)                      # ◄ ► listesi tek oyuncu (links_view ile ayni yol)
        return
    section = nav_view.SEC_COMPS if target == nav_view.TABLE else None
    nav_view.goto(target, param=param, section=section)


def _box(db):
    """Cizimdeki / callback'teki menajerin kalici gelen kutusu."""
    return manager(db).inbox_for_manager()


@member_callback
def cb_home_select(uid: str, message_id: int | None = None) -> None:
    """Liste satirina tiklandi: satir secilir, kalici mesaj OKUNDU sayilir (CM: haberi acinca okunur)."""
    st.session_state[INBOX_SEL_KEY] = str(uid)
    if message_id is None:
        return
    with session_scope() as db:
        _box(db).mark_read([int(message_id)])


@member_callback
def cb_home_archive(message_id: int) -> None:
    """Mesaji arsivler (satir silinmez: CM'de de haber kaybolmaz)."""
    with session_scope() as db:
        _box(db).archive(int(message_id), True)
    st.session_state.pop(INBOX_SEL_KEY, None)
    flash(AREA, "info", "Mesaj arşivlendi.")


@member_callback
def cb_home_unread(message_id: int) -> None:
    with session_scope() as db:
        _box(db).mark_unread(int(message_id))


@member_callback
def cb_home_read_all(category: str | None = None) -> None:
    with session_scope() as db:
        count = _box(db).mark_read(category=category or None)
    if count:
        flash(AREA, "info", f"{count} mesaj okundu olarak işaretlendi.")


@member_callback
def cb_home_clear(category: str | None = None) -> None:
    """"Temizle": okunmus mesajlari arsivler."""
    with session_scope() as db:
        count = _box(db).archive_read(category=category or None)
    st.session_state.pop(INBOX_SEL_KEY, None)
    flash(AREA, "info", f"{count} okunmuş mesaj arşivlendi." if count else "Arşivlenecek okunmuş mesaj yok.")


@requires_auth
def cb_home_more() -> None:
    ss = st.session_state
    ss[INBOX_SHOWN_KEY] = int(ss.get(INBOX_SHOWN_KEY) or INBOX_LIMIT) + INBOX_STEP


def current_tab() -> str:
    """Secili CM sekmesi (kategori kodu ya da CAT_ALL); bilinmeyen deger "Tümü"ye duser."""
    tab = st.session_state.get(INBOX_TAB_KEY)
    return tab if tab in INBOX_TABS else CAT_ALL


def tab_category(tab: str | None = None) -> str | None:
    """Sekmenin inbox kategorisi ("Tümü" -> None: butun kategoriler)."""
    tab = current_tab() if tab is None else tab
    return None if tab == CAT_ALL else tab


def row_label(item: InboxItem) -> str:
    """CM liste satiri: tarih + konu; okunmamis mesaj KALIN (simge / emoji yok)."""
    text = f"{item.when} · {item.text}" if item.when else item.text
    text = text if len(text) <= 80 else text[:78] + "…"
    return f"**{text}**" if not item.read else text


def inbox_toolbar(counts, unread: int) -> None:
    """Listenin ustu: okunmamis sayisi + "Tümünü okundu say" / "Okunmuşları temizle" (CM'nin haber araclari)."""
    category = tab_category()
    with st.container(horizontal=True, key="home_inbox_tools", vertical_alignment="center"):
        st.caption(f"{unread} okunmamış mesaj" if unread else
                   (f"{counts.total} mesaj" if counts is not None else "Gelen kutusu boş."))
        st.button("Tümünü okundu say", key="home_read_all", on_click=cb_home_read_all, args=(category,),
                  disabled=not unread, help="Bu sekmedeki bütün mesajlar okundu sayılır.")
        st.button("Okunmuşları temizle", key="home_clear", on_click=cb_home_clear, args=(category,),
                  help="Okunmuş mesajları arşivler; hiçbir haber silinmez.")


def inbox_panel(items: list[InboxItem], manager_name: str = "Menajer", counts=None) -> None:
    """
    CM haber ekrani (Gelen Kutusu): sekmeler Tumu / Mesajlar / Yarismalar / Sakatlik ve Cezalar (CM sekme satiri),
    solda tarihli liste, sagda secilen mesajin govdesi ve eylemleri. Simge / emoji yok.
    `counts`: inbox.InboxCounts (sekme basliklarinin gercek toplamlari); yoksa yalnizca cizilen satirlar sayilir.
    """
    if st.session_state.get(INBOX_TAB_KEY) not in INBOX_TABS:
        reset_widgets(INBOX_TAB_KEY)
    stored = getattr(counts, "by_category", None) or {}
    shown_counts = {tab: sum(1 for i in items if tab == CAT_ALL or i.category == tab) for tab in INBOX_TABS}
    live = {tab: sum(1 for i in items if i.message_id is None and (tab == CAT_ALL or i.category == tab))
            for tab in INBOX_TABS}
    totals = {tab: (live[tab] + (sum(stored.values()) if tab == CAT_ALL else int(stored.get(tab, 0))))
              if stored else shown_counts[tab] for tab in INBOX_TABS}
    st.segmented_control("Haberler", list(INBOX_TABS), key=INBOX_TAB_KEY, required=True, default=CAT_ALL,
                         format_func=lambda t: f"{TAB_LABELS[t]} ({totals[t]})" if totals[t] else TAB_LABELS[t],
                         label_visibility="collapsed", width="stretch")
    tab = current_tab()
    unread = int(getattr(counts, "unread", 0) or 0) if tab == CAT_ALL else \
        int((getattr(counts, "unread_by_category", None) or {}).get(tab, 0))
    inbox_toolbar(counts, unread)
    shown = [i for i in items if tab == CAT_ALL or i.category == tab]
    if not shown:
        st.markdown('<div class="cm-empty">Masan temiz: bu bölümde haber yok.</div>', unsafe_allow_html=True)
        return
    limit = max(INBOX_LIMIT, int(st.session_state.get(INBOX_SHOWN_KEY) or INBOX_LIMIT))
    selected = next((i for i in shown if i.uid == st.session_state.get(INBOX_SEL_KEY)), shown[0])
    left, right = st.columns([2, 3], gap="small")
    with left, st.container(key="home_msglist"):
        for n, item in enumerate(shown[:limit]):
            st.button(row_label(item), key=f"home_msg_{n}", on_click=cb_home_select,
                      args=(item.uid, item.message_id), width="stretch",
                      type="primary" if item is selected else "secondary")
        if len(shown) > limit:
            st.button(f"Daha eski haberler (+{min(INBOX_STEP, len(shown) - limit)})", key="home_more",
                      on_click=cb_home_more, width="stretch")
    with right, st.container(key="home_msgbody"):
        message_panel(selected)


def message_panel(selected: InboxItem) -> None:
    """Secilen haberin govdesi (CM: sagdaki okuma alani) ve eylemleri."""
    st.markdown(f"**{md_escape(selected.text)}**")
    head = " · ".join(part for part in (selected.date_long or selected.when, selected.kind_label) if part)
    if head:
        st.caption(head)
    for line in selected.body:
        if line != selected.text:
            st.markdown("- " + md_escape(line))
    if selected.players:                      # 14F: oyuncu adina tik -> oyuncu sayfasi
        lk.open_buttons("home_pl", [(name, nav_view.PLAYER, pid) for pid, name in selected.players], limit=8)
    with st.container(horizontal=True, key="home_msgacts"):
        if selected.button:
            st.button(selected.button, key="home_go", on_click=cb_home_go,
                      args=(selected.target, selected.deal_id, selected.direction, selected.contract_tab,
                            selected.param),
                      type="primary" if selected.tone in ("warning", "success") else "secondary")
        if selected.message_id is not None:
            st.button("Arşivle", key="home_arch", on_click=cb_home_archive, args=(selected.message_id,),
                      help="Haberi listeden kaldırır; kaydı silinmez.")
            if selected.read:
                st.button("Okunmadı say", key="home_unread", on_click=cb_home_unread, args=(selected.message_id,))


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


RESULT_WORDS = {"G": "galibiyet", "B": "beraberlik", "M": "mağlubiyet"}


def last_result_card(last: FixtureLine | None) -> None:
    if last is None:
        st.markdown(_card("Son sonuç", escape("Henüz maç oynanmadı")), unsafe_allow_html=True)
        return
    result = RESULT_WORDS.get(last.result or "", "")
    value = f"{escape(last.home)} <b>{escape(last.score_text)}</b> {escape(last.away)}"
    sub = f"{last.competition_label} · Sezon {last.season}, {last.week}. hafta" + (f" · {result}" if result else "")
    st.markdown(_card("Son sonuç", value, sub), unsafe_allow_html=True)


def read_inbox(cm) -> tuple[list, object | None]:
    """Kalici gelen kutusu: secili sekmenin mesajlari + kategori sayaclari (iki sorgu; hata sayfayi dusurmez)."""
    try:
        box = cm.inbox_for_manager()
        return box.messages(tab_category(), limit=FETCH_LIMIT), box.counts()
    except Exception:                       # gelen kutusu tablosu yoksa / okunamazsa ekran calismaya devam eder
        log.exception("Gelen kutusu okunamadı")
        return [], None


BOARD_BUTTON = "Yönetim kurulu"


def board_strip(cm) -> None:
    """
    15C-U: Gelen Kutusu'nun kisa yonetim satiri -- sezon hedefi, yonetimin guveni (SOZCUK), son uyari ve bekleyen
    is teklifi; tek tik Yönetim sayfasina gider. Kural kapaliyken hicbir sey cizilmez ve tek sorgu atilmaz.
    """
    summary = board_view.board_summary(cm)
    if summary is None:
        return
    if summary.unemployed:
        rows: list[tuple[str, object]] = [("Yönetim kurulu", "Kulüpsüzsün"),
                                          ("Kaç haftadır", f"{summary.unemployed_weeks} hafta"),
                                          ("Bekleyen iş teklifi", summary.offers or "—")]
    else:
        rows = [("Sezon hedefi", summary.target), ("Lig sırası", summary.position),
                ("Yönetimin güveni", summary.confidence_label),
                ("Yönetim uyarısı", summary.warning_label if summary.warning else "—")]
        if summary.offers:
            rows.append(("İş teklifi", summary.offers))
    st.markdown(panel_title_html("Yönetim kurulu"), unsafe_allow_html=True)
    st.markdown(nav_view.facts_html(rows), unsafe_allow_html=True)
    with st.container(horizontal=True, key="home_board", vertical_alignment="center"):
        if summary.unemployed:
            st.caption(f"{summary.offers} iş teklifi yanıt bekliyor." if summary.offers
                       else "Kulüpsüzsün: yeni kulübü iş ilanlarından bulursun.")
        elif summary.warning:
            st.caption("Yönetim kurulu seni uyardı: ayrıntısı gelen kutundaki yönetim mesajında.")
        elif summary.offers:
            st.caption(f"{summary.offers} iş teklifi yanıt bekliyor.")
        st.button(BOARD_BUTTON, key="home_board_go", on_click=board_view.cb_board_open,
                  type="primary" if summary.alert else "secondary",
                  help="Sezon hedefi, yönetimin güveni, bütçe önerisi, iş ilanları ve teklifler.")


def render_home(db, cm, team: Team | None, *, continue_action: Callable[[str], None],
                hub_counts=None, deals: Sequence | None = None, manager_name: str = "Menajer",
                contract_rows: Sequence | None = None) -> None:
    """
    Gelen Kutusu (CM haber ekrani) + mac masasi. continue_action(key): web_app'in devam / hazir dugmesi.
    15C-U: `team` None olabilir (kovulan / istifa eden menajer); o zaman mac masasi yerine yonetim satiri ve
    "Devam" cizilir (hafta kulupsuz de ilerler).
    """
    show_flash(AREA)
    tournament = cm.game_mode is GameMode.TOURNAMENT
    messages, counts = read_inbox(cm)
    inbox_panel(inbox_items(cm, team, deals or (), hub_counts, contract_rows, messages), manager_name, counts)
    board_strip(cm)
    if team is None:                                   # 15C-U: kulupsuz menajer -- mac masasi yok, hafta ilerler
        with st.container(horizontal=True, key="home_actions"):
            continue_action("home")
        continue_view.panel(cm)
        return

    st.markdown(panel_title_html("Maç masası"), unsafe_allow_html=True)
    total = cm.total_weeks()
    strip = []
    if not tournament and team.league_id is not None:
        table = cm.standings(team.league_id)
        position = next((i for i, t in enumerate(table, start=1) if t.id == team.id), None)
        strip += [("Lig sırası", f"{position}. / {len(table)}" if position else "—"), ("Puan", team.points),
                  ("Form", form_text(cm.team_form(team.id)))]
    strip.append(("Sezon · hafta", f"{cm.season} · {min(cm.current_week, total)}/{total}"))
    if not tournament:
        strip.append(("Transfer bütçesi", format_money(team.transfer_budget)))
    st.markdown(nav_view.facts_html(strip), unsafe_allow_html=True)
    upcoming = team_fixtures(db, team.id, cm.season, played=False, limit=1)
    recent = team_fixtures(db, team.id, None, played=True, newest_first=True, limit=1)
    left, right = st.columns(2, gap="small")
    with left:
        next_match_card(cm, team, upcoming[0] if upcoming else None)
    with right:
        last_result_card(recent[0] if recent else None)
    with st.container(horizontal=True, key="home_actions"):
        st.button("Maç önü", key="home_prep", on_click=cb_home_go, args=(nav_view.TACTICS,),
                  disabled=tournament, help="Taktik › Maç önü raporu ve rakip gözlem raporu")
        st.button("Canlı yönet", key="home_live", on_click=cb_home_go, args=(nav_view.MATCH,),
                  help="Haftanın maçını canlı yönet: durdur, değişiklik yap, talimat ver.")
        continue_action("home")
    continue_view.panel(cm)                # 15D: "Şuna kadar devam" (Devam satirinin hemen altinda)


__all__ = ["AREA", "BOARD_BUTTON", "CAT_ALL", "CAT_COMPETITIONS", "CAT_INJURIES", "CAT_MESSAGES", "FixtureLine",
           "InboxItem", "INBOX_TABS", "TAB_LABELS", "board_strip",
           "cb_home_archive", "cb_home_clear", "cb_home_go", "cb_home_more",
           "cb_home_read_all", "cb_home_select", "cb_home_unread", "contract_items", "deal_item", "form_text",
           "inbox_items", "inbox_panel", "live_items", "message_item", "message_items", "read_inbox",
           "render_home", "row_label", "tab_category", "team_fixtures"]
