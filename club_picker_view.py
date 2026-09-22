"""
club_picker_view.py
===================
Kulup secimi: ULKE -> LIG -> KULUP (Faz 13G). Kariyerin ilk adimi budur: kulubu olmayan menajer (kisisel kariyer /
turnuva modu) sekmeleri, kenar cubugu araclarini ve transfer pazarini gormeden once bu sayfayi gorur
(web_app.club_select_page). Paylasilan dunyada kulupsuz koltugun kulup teklifleri de ayni seciciyle cizilir
(world_lobby_view.render_club_offers; uygunluk / koruma kurallari world_manager.WorldController.club_offers'ta).

    ClubCard                   kartin verisi (ulke ligden turetilir: leagues.country)
    career_club_cards(db, ids) kisisel kariyer / turnuva: TEK sorgu (kulup + lig + kadro ortalamasi GROUP BY)
    cards_from_offers(offers)  paylasilan dunya: WorldController.club_offers satirlari -> kart
    countries / leagues_of / clubs_in / search      saf gruplama ve arama (testler dogrudan cagirir)
    render_picker(...)         ulke hapi -> lig hapi -> kulup kartlari (+ istege bagli arama)
    render_career_picker(db, cm)  kisisel kariyer / turnuva sayfasi (cb_choose_club)

WIDGET ANAHTARLARI (prefix: kariyer "cp", paylasilan dunya "co"):
    {prefix}_country   ulke (st.pills)          {prefix}_league   lig (st.pills; ulkede tek lig varsa kendiliginden)
    {prefix}_query     arama (paylasilan dunyada eski ad: co_query)
    cp_pick_{team_id}  kariyer: "Bu kulübü yönet"   co_claim_{team_id}  paylasilan dunya: "Yönet" (degismedi)

KILIT (Faz 13G): kulup secildikten sonra kariyer modunda kulup degismez (CareerManager.choose_club / club_locked);
turnuva modunda ilk maca kadar katilimcilar arasinda secilebilir. Callback sunucuda yeniden dogrular: istemciden gelen
tek sey dugmenin argumani olan kulup id'sidir; kulup her zaman oturumun kariyerinde (manager(db)) aranir.

GUVENLIK: kulup / lig / ulke adlari HTML'e html.escape ile gider. Callback member_callback ile sarilir (oturum +
dunya uyeligi + paylasilan dunyada SHARED kilit).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from html import escape

import streamlit as st
from sqlalchemy import func, select

from career_manager import CareerManager, ClubChoiceError
from club_directory import plain_key
from database import session_scope
from finance import format_money
from models import GameMode, League, Player, Team
from stars import UNKNOWN, star_glyphs
from web_common import (
    flash,
    live_fixture_pending,
    manager,
    md_escape,
    member_callback,
    reset_widgets,
    show_flash,
)

FLASH_AREA = "clubs"
WELCOME_AREA = "main"                     # web_app.main basligin altinda gosterir (telefonda kenar cubugu kapali)
SCROLL_TOP_KEY = "scroll_top"             # kulup secildi: bir sonraki cizim sayfanin basina kayar
CAREER_PREFIX = "cp"
SHARED_PREFIX = "co"
SEARCH_MAX = 40
GRID_COLUMNS = 3

# Bayrak emojileri (Windows'ta harf ciftine dusebilir; ad her zaman yaninda yazilir)
COUNTRY_FLAGS: dict[str, str] = {
    "Türkiye": "🇹🇷",
    "İngiltere": "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "İspanya": "🇪🇸",
    "Almanya": "🇩🇪",
    "İtalya": "🇮🇹",
    "Fransa": "🇫🇷",
    "Hollanda": "🇳🇱",
    "Portekiz": "🇵🇹",
    "İskoçya": "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "Belçika": "🇧🇪",
}
HOME_COUNTRY = "Türkiye"                  # listede ilk sirada

PICKER_CSS = """
<style>
.ofm-club-card{display:flex;flex-direction:column;gap:.25rem;min-width:0}
.ofm-club-card .cc-name{font-weight:700;font-size:1.05rem;line-height:1.25;color:var(--ofm-text,inherit);
  overflow-wrap:break-word;hyphens:auto}
.ofm-club-card .cc-stars{color:var(--ofm-accent,#f5b400);font-size:.95rem;letter-spacing:.05em}
.ofm-club-card .cc-league{color:var(--ofm-muted,#8a94a6);font-size:.8rem;overflow-wrap:break-word}
.ofm-club-card .cc-stats{margin:.25rem 0 0;padding:.35rem .5rem;border-radius:8px;
  background:var(--ofm-panel-alt,rgba(128,128,128,.08));border:1px solid var(--ofm-border,rgba(128,128,128,.25))}
.ofm-club-card .cc-stats div{display:flex;flex-wrap:wrap;justify-content:space-between;column-gap:.5rem;
  padding:.1rem 0}
.ofm-club-card .cc-stats dt{font-size:.75rem;color:var(--ofm-muted,#8a94a6);margin:0;text-transform:none}
.ofm-club-card .cc-stats dd{margin:0 0 0 auto;font-weight:700;font-size:.85rem;text-align:right}
.ofm-club-card .cc-note{font-size:.8rem;color:var(--ofm-muted,#8a94a6)}
.ofm-club-card .cc-note.lock{color:#e5534b}
.st-key-cp_country button[role="radio"],.st-key-cp_league button[role="radio"],
.st-key-co_country button[role="radio"],.st-key-co_league button[role="radio"]{
  background:var(--ofm-panel) !important;color:var(--ofm-text) !important;border-color:var(--ofm-border) !important}
.st-key-cp_country button[aria-checked="true"],.st-key-cp_league button[aria-checked="true"],
.st-key-co_country button[aria-checked="true"],.st-key-co_league button[aria-checked="true"]{
  background:var(--ofm-primary) !important;color:var(--ofm-primary-text) !important;
  border-color:var(--ofm-primary) !important}
.ofm-pick-step{font-weight:700;margin:.6rem 0 .1rem;color:var(--ofm-text,inherit)}
.ofm-pick-step .n{display:inline-block;min-width:1.5rem;height:1.5rem;line-height:1.5rem;text-align:center;
  border-radius:50%;background:var(--ofm-primary,#635BFF);color:var(--ofm-primary-text,#fff);margin-right:.4rem;
  font-size:.85rem}
</style>
"""


# ===========================================================================
# VERI
# ===========================================================================

@dataclass(frozen=True)
class ClubCard:
    team_id: int
    name: str
    league_id: int | None
    league_name: str
    country: str
    reputation: int
    stadium_capacity: int | None
    transfer_budget: int
    squad_rating: float | None            # A takim ortalamasi: ekranda YALNIZCA yildiz (sayi yazilmaz)
    eligible: bool = True
    reason: str = ""
    protected: bool = False


def career_club_cards(db, only_ids: Iterable[int] | None = None) -> list[ClubCard]:
    """Kisisel kariyer / turnuva modu: tum kulupler (only_ids verilirse yalnizca onlar). Tek sorgu."""
    squad = (select(Player.team_id.label("team_id"), func.avg(Player.overall_rating).label("rating"))
             .where(Player.team_id.is_not(None), Player.in_academy.is_(False))
             .group_by(Player.team_id).subquery())
    query = (select(Team.id, Team.name, Team.league_id, League.name, League.country, Team.reputation,
                    Team.stadium_capacity, Team.transfer_budget, squad.c.rating)
             .join(League, League.id == Team.league_id)
             .outerjoin(squad, squad.c.team_id == Team.id))
    if only_ids is not None:
        ids = sorted({int(i) for i in only_ids})
        if not ids:
            return []
        query = query.where(Team.id.in_(ids))
    return [ClubCard(team_id=tid, name=name, league_id=lid, league_name=lname, country=country or "",
                     reputation=int(rep), stadium_capacity=cap, transfer_budget=int(budget or 0),
                     squad_rating=float(rating) if rating is not None else None)
            for tid, name, lid, lname, country, rep, cap, budget, rating in db.execute(query).all()]


def cards_from_offers(offers: Iterable) -> list[ClubCard]:
    """Paylasilan dunya: world_manager.ClubOffer -> ClubCard (uygunluk ve koruma notlariyla)."""
    return [ClubCard(team_id=o.team_id, name=o.team_name, league_id=getattr(o, "league_id", None),
                     league_name=o.league_name, country=getattr(o, "country", "") or "", reputation=o.reputation,
                     stadium_capacity=getattr(o, "stadium_capacity", None), transfer_budget=o.transfer_budget,
                     squad_rating=o.squad_rating or None, eligible=o.eligible, reason=o.reason,
                     protected=o.protected)
            for o in offers]


def flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country, "🏳️")


def countries(cards: Sequence[ClubCard]) -> list[str]:
    """Ulkeler: once ev sahibi ulke (Turkiye), sonra aksansiz alfabetik."""
    names = {c.country for c in cards if c.country}
    return sorted(names, key=lambda n: (n != HOME_COUNTRY, plain_key(n)))


def leagues_of(cards: Sequence[ClubCard], country: str | None) -> list[tuple[int | None, str, int]]:
    """Ulkenin ligleri: (lig id, ad, kulup sayisi); en guclu lig (en yuksek itibarli kulup) once."""
    groups: dict[tuple[int | None, str], list[ClubCard]] = {}
    for c in cards:
        if c.country == country:
            groups.setdefault((c.league_id, c.league_name), []).append(c)
    ordered = sorted(groups.items(), key=lambda kv: (-max(c.reputation for c in kv[1]), plain_key(kv[0][1])))
    return [(lid, name, len(members)) for (lid, name), members in ordered]


def clubs_in(cards: Sequence[ClubCard], league_id: int | None) -> list[ClubCard]:
    return sort_cards([c for c in cards if c.league_id == league_id])


def sort_cards(cards: Iterable[ClubCard]) -> list[ClubCard]:
    """Uygun olanlar once, sonra itibar (yuksekten), sonra ad."""
    return sorted(cards, key=lambda c: (not c.eligible, -c.reputation, plain_key(c.name)))


def search(cards: Sequence[ClubCard], query: str) -> list[ClubCard]:
    """Kulup, lig ya da ulke adinda aksansiz arama (Galatasaray / super lig / ingiltere)."""
    key = plain_key(query or "")
    if not key:
        return []
    return sort_cards(c for c in cards
                      if key in plain_key(c.name) or key in plain_key(c.league_name) or key in plain_key(c.country))


def capacity_text(capacity: int | None) -> str:
    return "—" if not capacity else f"{int(capacity):,}".replace(",", ".") + " koltuk"


def card_html(card: ClubCard) -> str:
    stars = star_glyphs(card.squad_rating) if card.squad_rating else UNKNOWN
    notes = []
    if card.protected:
        notes.append('<div class="cc-note">🛡️ Yapay zekâ transferlerine karşı korumada</div>')
    if not card.eligible and card.reason:
        notes.append(f'<div class="cc-note lock">🔒 {escape(card.reason)}</div>')
    return (
        '<div class="ofm-club-card">'
        f'<div class="cc-name">{escape(card.name)}</div>'
        f'<div class="cc-stars" title="Kadro gücü (yıldız)">{escape(stars)}</div>'
        f'<div class="cc-league">{escape(flag(card.country))} {escape(card.league_name)} · {escape(card.country)}</div>'
        '<dl class="cc-stats">'
        f'<div><dt>İtibar</dt><dd>{int(card.reputation)}</dd></div>'
        f'<div><dt>Stadyum</dt><dd>{escape(capacity_text(card.stadium_capacity))}</dd></div>'
        f'<div><dt>Transfer bütçesi</dt><dd>{escape(format_money(card.transfer_budget))}</dd></div>'
        '</dl>'
        f'{"".join(notes)}</div>'
    )


# ===========================================================================
# CIZIM
# ===========================================================================

def _step(number: int, text: str) -> None:
    st.markdown(f'<div class="ofm-pick-step"><span class="n">{number}</span>{escape(text)}</div>',
                unsafe_allow_html=True)


def _sync_choice(key: str, options: list, *, auto_single: bool = False) -> None:
    """Kayitli secim artik seceneklerde yoksa silinir; tek secenek varsa (auto_single) o secilir."""
    value = st.session_state.get(key)
    if value is not None and value not in options:
        reset_widgets(key)
        value = None
    if value is None and auto_single and len(options) == 1:
        st.session_state[key] = options[0]


def render_picker(cards: Sequence[ClubCard], *, prefix: str, button_key: Callable[[ClubCard], str],
                  on_choose: Callable, button_label: str = "Bu kulübü yönet", query_key: str | None = None,
                  empty_text: str = "Seçebileceğin kulüp yok.", disabled: bool = False) -> None:
    """
    Ulke -> lig -> kulup. cards bos degilse en az bir adim her zaman gorunur. query_key verilirse (arama kutusunun
    anahtari; cagiran taraf kutuyu kendisi cizer) doluyken sonuclar ulke/lig seciminden bagimsiz listelenir.
    Dugmeler on_choose(team_id) callback'ini args ile cagirir; anahtar button_key(card).
    """
    if not cards:
        st.info(empty_text)
        return
    country_key, league_key = f"{prefix}_country", f"{prefix}_league"
    options = countries(cards)
    _sync_choice(country_key, options)
    counts = {name: sum(1 for c in cards if c.country == name) for name in options}
    _step(1, "Ülkeni seç")
    country = st.pills("Ülke", options, key=country_key, label_visibility="collapsed",
                       format_func=lambda n: f"{flag(n)} {n} · {counts.get(n, 0)}")
    query = str(st.session_state.get(query_key) or "").strip() if query_key else ""
    if query:
        found = search(cards, query)
        st.caption(f"Arama «{query}»: {len(found)} kulüp" + (f" (ilk {SEARCH_MAX})" if len(found) > SEARCH_MAX else "")
                   + ". Aramayı silince ülke → lig adımlarına dönersin.")
        _grid(found[:SEARCH_MAX], button_key, on_choose, button_label, disabled)
        if not found:
            st.info("Aramana uyan kulüp yok.")
        return
    if country is None:
        st.info("Önce ülkeyi seç: ligler ve kulüpler ülkeye göre listelenir.")
        return
    leagues = leagues_of(cards, country)
    league_ids = [lid for lid, _, _ in leagues]
    names = {lid: f"{name} · {n} kulüp" for lid, name, n in leagues}
    _sync_choice(league_key, league_ids, auto_single=True)
    _step(2, f"{flag(country)} {country}: ligini seç")
    league = st.pills("Lig", league_ids, key=league_key, label_visibility="collapsed",
                      format_func=lambda lid: names.get(lid, str(lid)))
    if league is None:
        st.info("Bir lig seç: kulüpleri itibar sırasıyla göreceksin.")
        return
    clubs = clubs_in(cards, league)
    _step(3, "Kulübünü seç")
    _grid(clubs, button_key, on_choose, button_label, disabled)


def _grid(clubs: Sequence[ClubCard], button_key, on_choose, button_label: str, disabled: bool) -> None:
    for start in range(0, len(clubs), GRID_COLUMNS):
        cols = st.columns(GRID_COLUMNS)
        for col, card in zip(cols, clubs[start:start + GRID_COLUMNS], strict=False):
            with col, st.container(border=True):
                st.markdown(card_html(card), unsafe_allow_html=True)
                st.button(button_label, key=button_key(card), on_click=on_choose, args=(card.team_id,),
                          type="primary", width="stretch", disabled=disabled or not card.eligible,
                          help=None if card.eligible else (card.reason or None))


# ===========================================================================
# KISISEL KARIYER / TURNUVA SAYFASI
# ===========================================================================

def picker_team_ids(cm: CareerManager) -> list[int] | None:
    """Turnuva modunda yalnizca bu sezonun Devler Arenasi katilimcilari; kariyerde tum kulupler (None)."""
    if cm.game_mode is not GameMode.TOURNAMENT:
        return None
    t = cm.tournaments.current()
    if t is None:
        return None
    return [team.id for team in cm.tournaments.participants(t)]


def render_career_picker(db, cm: CareerManager) -> None:
    """Kulubu olmayan menajerin ilk sayfasi (kisisel kariyer / turnuva modu)."""
    tournament = cm.game_mode is GameMode.TOURNAMENT
    st.markdown("## 🏟️ Kulübünü seç")
    show_flash(FLASH_AREA)
    st.caption("Turnuva modu: Devler Arenası'na katılan kulüplerden birini seç. Turnuva başlayınca kulüp değişmez."
                if tournament else
                "Kariyerin ilk adımı: önce ülke, sonra lig, sonra kulüp. Seçtiğin kulüp bu kariyer boyunca "
                "senin kulübün olur (kariyer modunda kulüp değiştirilemez).")
    cards = career_club_cards(db, picker_team_ids(cm))
    st.text_input("Kulüp ara", key=f"{CAREER_PREFIX}_query", max_chars=40, placeholder="🔎 …ya da kulüp / lig ara",
                  label_visibility="collapsed")
    render_picker(cards, prefix=CAREER_PREFIX, button_key=lambda c: f"cp_pick_{c.team_id}",
                  on_choose=cb_choose_club, query_key=f"{CAREER_PREFIX}_query",
                  disabled=live_fixture_pending())


# Kulup degisince eski kulubun ekran durumu (kadro editoru, pazar hedefi, sozlesme masasi, son rapor) atilir
TEAM_WIDGETS = ("neg", "tac_editor", "tac_rows", "fin_target", "mkt_target", "mkt_fee",
                "last_user_result", "last_user_cup_result", "tac_formation", "sb_team", "pv_open",
                f"{CAREER_PREFIX}_query", f"{CAREER_PREFIX}_country", f"{CAREER_PREFIX}_league")


@member_callback
def cb_choose_club(team_id: int) -> None:
    """Kulup secimi (kisisel kariyer / turnuva). Kurallar CareerManager.choose_club'da; reddedilirse flash."""
    if live_fixture_pending():
        flash(FLASH_AREA, "error", "Kaydedilmemiş canlı maç varken kulüp seçilemez.")
        return
    try:
        with session_scope() as db:
            cm = manager(db)
            team = db.get(Team, int(team_id))
            if team is None:
                raise ClubChoiceError("Kulüp bulunamadı.")
            notes = cm.choose_club(team)
            name = team.name
    except ClubChoiceError as exc:
        flash(FLASH_AREA, "error", str(exc))
        return
    reset_widgets(*TEAM_WIDGETS)
    flash(WELCOME_AREA, "success", f"🏟️ Hoş geldin! Artık **{md_escape(name)}** menajerisin. "
                                   + " ".join(md_escape(n) for n in notes))
    st.session_state[SCROLL_TOP_KEY] = True
