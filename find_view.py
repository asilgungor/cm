"""
find_view.py
============
Faz 14S / 14F: "Bul" (CM 01/02 "Find"). Tek arama kutusu (fd_query) + sekmeler (fd_tab):
    Oyuncu    suzgecler: mevki, yas araligi, lig, "sozlesmesi bitiyor", "transfer listesinde"; oyuncuya tik -> oyuncu
              sayfasi, kulube tik -> kulup sayfasi, uyruga tik -> ulke sayfasi
    Kulüp     kulube tik -> kulup sayfasi, lige tik -> lig sayfasi, ulkeye tik -> ulke sayfasi
    Lig       lige tik -> lig sayfasi
    Ülke      ulkeye tik -> ulke sayfasi (arama bossa hepsi)
    Personel  teknik heyet (ad, rol, kulup); kulube tik -> kulup sayfasi

ESLESME: Turkce aksan ve buyuk / kucuk harf duyarsiz (club_directory.plain_key: İ/ı/i, ş/s, ğ/g, ç/c, ö/o, ü/u), on ek
ve kelime ici. SIRALAMA: tam ad > tam kelime > on ek (ad ya da bir kelimesi) > icerir, sonra Turkce alfabetik
(player_view.tr_sort_key).
En cok LIMIT sonuc; "Daha fazla" (fd_more) sonraki LIMIT.

VERI: oyuncular TEK sorguda (id, ad, mevki, yas, kulup id / adi / ligi, uyruk, sozlesme, liste bayragi; BASKA kulubun
akademisi hic gelmez, kendi akademin gelir); arama Python'da (~3.000 satir). Sonuclarin sisi (yetenek / deger) TEK SIS
MODELINDEN (career_views.player_fog; bilgi haritasi iki sorgu, yalnizca gosterilen satirlar icin). "Sözleşmesi bitiyor"
suzgeci yalnizca BILINEN sozlesmelerde (kendi oyuncun ya da %75 bilgi; K12).
SUNUM + okuma sorgulari; veritabanina YAZMAZ.

WIDGET ANAHTARLARI: fd_query, fd_tab, fd_pos, fd_age, fd_league, fd_expiring, fd_listed, fd_more; tablolar
lk_find_players / lk_find_clubs / lk_find_leagues / lk_find_nations / lk_find_staff (links_view).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st
from sqlalchemy import select

import career_views as cv
import club_picker_view as cp
import links_view as lk
import player_view as pv
import staff as staff_rules
from club_directory import plain_key
from models import League, Player, Staff, Team
from web_common import md_escape, requires_auth

QUERY_KEY, TAB_KEY = "fd_query", "fd_tab"
PLAYERS_KEY, CLUBS_KEY, LEAGUES_KEY, NATIONS_KEY, STAFF_KEY = (
    "lk_find_players", "lk_find_clubs", "lk_find_leagues", "lk_find_nations", "lk_find_staff")
POS_KEY, AGE_KEY, LEAGUE_KEY, EXPIRING_KEY, LISTED_KEY, MORE_KEY = (
    "fd_pos", "fd_age", "fd_league", "fd_expiring", "fd_listed", "fd_more")
TAB_PLAYER, TAB_CLUB, TAB_LEAGUE, TAB_NATION, TAB_STAFF = "Oyuncu", "Kulüp", "Lig", "Ülke", "Personel"
TABS = (TAB_PLAYER, TAB_CLUB, TAB_LEAGUE, TAB_NATION, TAB_STAFF)
POSITIONS = ["GK", "DEF", "MID", "FWD"]
MIN_QUERY = 2
LIMIT = 100
ANY_LEAGUE = 0
AGE_RANGE = (15, 45)


@dataclass(frozen=True)
class PlayerHit:
    id: int
    name: str
    position: str
    age: int
    team_id: int | None
    club: str
    league_id: int | None
    nationality: str
    contract_years: int
    transfer_listed: bool
    key: str                                    # plain_key(ad)


def match_rank(key: str, needle: str) -> int | None:
    """Esleme derecesi: 0 tam ad, 1 bir kelimesi tam, 2 on ek (ad ya da bir kelimesi), 3 icerir; eslesmezse None.
    Iki taraf plain_key (Turkce aksan ve buyuk / kucuk harf duyarsiz)."""
    if not needle or needle not in key:
        return None
    if key == needle:
        return 0
    words = key.split()
    if needle in words:
        return 1
    if key.startswith(needle) or any(word.startswith(needle) for word in words):
        return 2
    return 3


def rank_names(items, needle: str, name_of) -> list:
    """(derece, Turkce alfabetik ad) sirasiyla eslesenler."""
    ranked = []
    for item in items:
        name = name_of(item)
        rank = match_rank(plain_key(name), needle)
        if rank is not None:
            ranked.append((rank, pv.tr_sort_key(name), item))
    ranked.sort(key=lambda t: (t[0], t[1]))
    return [item for _r, _k, item in ranked]


def player_pool(db, viewer: Team | None) -> list[PlayerHit]:
    """Aranabilir oyuncular TEK sorguda: A takimlar + kulupsuzler + KENDI akademin (baska kulubun akademisi yok)."""
    stmt = (select(Player.id, Player.name, Player.position, Player.age, Player.team_id, Team.name, Team.league_id,
                   Player.nationality, Player.contract_years, Player.transfer_listed, Player.in_academy)
            .outerjoin(Team, Team.id == Player.team_id))
    own = viewer.id if viewer is not None else None
    hits = []
    for pid, name, pos, age, tid, club, league_id, nat, years, listed, academy in db.execute(stmt).all():
        if academy and (own is None or tid != own):
            continue
        hits.append(PlayerHit(int(pid), name, pos.value, int(age), tid, club or "Kulüpsüz", league_id, nat or "",
                              int(years or 0), bool(listed), plain_key(name)))
    return hits


def find_players(db, query: str, limit: int = LIMIT, *, viewer: Team | None = None, cm=None,
                 positions: set[str] | None = None, ages: tuple[int, int] | None = None, league_id: int | None = None,
                 expiring: bool = False, listed: bool = False) -> list[PlayerHit]:
    """Oyuncu aramasi (bkz. modul basligi). expiring: yalnizca BILINEN son sezon sozlesmeleri (K12)."""
    needle = plain_key(query or "")
    if len(needle) < MIN_QUERY:
        return []
    pool = [h for h in player_pool(db, viewer)
            if (not positions or h.position in positions) and (ages is None or ages[0] <= h.age <= ages[1])
            and (not league_id or h.league_id == league_id) and (not listed or h.transfer_listed)]
    ranked = rank_names(pool, needle, lambda h: h.name)
    if expiring:
        known = cv.knowledge_map(db, viewer, [h.id for h in ranked]) if viewer is not None else {}
        ranked = [h for h in ranked if h.contract_years <= 1 and (
            (viewer is not None and h.team_id == viewer.id) or known.get(h.id, 0) >= cv.FOG_CONTRACT_FROM)]
    return ranked[:limit]


@requires_auth
def cb_fd_more() -> None:
    st.session_state[MORE_KEY] = int(st.session_state.get(MORE_KEY) or 1) + 1


@requires_auth
def cb_fd_reset_more() -> None:
    st.session_state[MORE_KEY] = 1


def render_find(db, cm, team: Team | None) -> None:
    """Bul sayfasi (web_app.PAGE_RENDERERS)."""
    query = st.text_input("Ara", key=QUERY_KEY, placeholder="Oyuncu, kulüp, lig, ülke ya da personel adı",
                          label_visibility="collapsed", on_change=cb_fd_reset_more)
    if st.session_state.get(TAB_KEY) not in TABS:
        st.session_state[TAB_KEY] = TAB_PLAYER
    tab = st.segmented_control("Tür", list(TABS), key=TAB_KEY, required=True, label_visibility="collapsed",
                               width="stretch", on_change=cb_fd_reset_more)
    text = (query or "").strip()
    if tab == TAB_NATION:
        _nations(db, text)
    elif tab == TAB_LEAGUE:
        _leagues(db, text)
    elif tab == TAB_CLUB:
        _clubs(db, text)
    elif tab == TAB_STAFF:
        _staff(db, text)
    else:
        _players(db, cm, team, text)


def _short(text: str) -> bool:
    if len(plain_key(text)) >= MIN_QUERY:
        return False
    st.markdown('<div class="cm-empty">Aramak için en az 2 harf yaz. Aksan ve büyük / küçük harf önemsiz '
                '(ş = s, ı = i).</div>', unsafe_allow_html=True)
    return True


def _players(db, cm, team: Team | None, text: str) -> None:
    leagues = {lg.id: lg.name for lg in cm.leagues()} if cm is not None else {}
    with st.expander("Süzgeçler", expanded=bool(st.session_state.get(POS_KEY) or st.session_state.get(LISTED_KEY)
                                                 or st.session_state.get(EXPIRING_KEY))):
        c1, c2, c3 = st.columns([2, 2, 2])
        positions = c1.multiselect("Mevki", POSITIONS, key=POS_KEY, placeholder="Tümü")
        ages = c2.slider("Yaş", AGE_RANGE[0], AGE_RANGE[1], value=AGE_RANGE, key=AGE_KEY)
        options = [ANY_LEAGUE, *leagues]
        if st.session_state.get(LEAGUE_KEY) not in options:
            st.session_state[LEAGUE_KEY] = ANY_LEAGUE
        league = c3.selectbox("Lig", options, key=LEAGUE_KEY,
                              format_func=lambda i: "Tüm ligler" if i == ANY_LEAGUE else leagues.get(i, str(i)))
        d1, d2 = st.columns(2)
        expiring = d1.checkbox("Sözleşmesi bitiyor (bilinen)", key=EXPIRING_KEY,
                               help="Kendi oyuncun ya da gözlemcinin %75 bildiği oyuncular: son sezonu.")
        listed = d2.checkbox("Transfer listesinde", key=LISTED_KEY)
    if _short(text):
        return
    pages = max(1, int(st.session_state.get(MORE_KEY) or 1))
    hits = find_players(db, text, LIMIT * pages + 1, viewer=team, cm=cm, positions=set(positions),
                        ages=tuple(ages), league_id=league or None, expiring=expiring, listed=listed)
    more = len(hits) > LIMIT * pages
    hits = hits[: LIMIT * pages]
    if not hits:
        st.markdown(f'<div class="cm-empty">"{md_escape(text)}" ile eşleşen oyuncu yok.</div>',
                    unsafe_allow_html=True)
        return
    st.markdown(f"#### Oyuncular ({len(hits)}{'+' if more else ''})")
    players = {p.id: p for p in db.scalars(select(Player).where(Player.id.in_([h.id for h in hits])))}
    fogs = cv.fog_rows(cm, team, [players[h.id] for h in hits if h.id in players])
    frame = pd.DataFrame([{
        "Oyuncu": h.name, "Mv": h.position, "Yaş": h.age, "Kulüp": h.club, "Uyruk": h.nationality or "—",
        cv.ABILITY_LABEL: fogs[h.id].ability_text if h.id in fogs else cv.UNKNOWN_TEXT,
        "Değer (EUR)": fogs[h.id].value_text if h.id in fogs else cv.UNKNOWN_TEXT,
    } for h in hits])
    lk.link_table(PLAYERS_KEY, frame, players=[h.id for h in hits], clubs={"Kulüp": [h.team_id for h in hits]},
                  nations={"Uyruk": [h.nationality or None for h in hits]})
    if more:
        st.button(f"Daha fazla ({LIMIT} sonuç daha)", key="fd_more_btn", on_click=cb_fd_more)


def _clubs(db, text: str) -> None:
    if _short(text):
        return
    cards = rank_names(cp.career_club_cards(db), plain_key(text), lambda c: c.name)[:LIMIT]
    if not cards:
        st.markdown(f'<div class="cm-empty">"{md_escape(text)}" ile eşleşen kulüp yok.</div>', unsafe_allow_html=True)
        return
    st.markdown(f"#### Kulüpler ({len(cards)})")
    from club_view import reputation_label

    lk.link_table(CLUBS_KEY, pd.DataFrame([{"Kulüp": c.name, "Lig": c.league_name, "Ülke": c.country,
                                            "İtibar": reputation_label(c.reputation)} for c in cards]),
                  clubs={"Kulüp": [c.team_id for c in cards]}, leagues={"Lig": [c.league_id for c in cards]},
                  nations={"Ülke": [c.country for c in cards]})


def _leagues(db, text: str) -> None:
    leagues = list(db.scalars(select(League).order_by(League.id)))
    needle = plain_key(text)
    shown = rank_names(leagues, needle, lambda lg: lg.name) if needle else leagues
    if not shown:
        st.markdown(f'<div class="cm-empty">"{md_escape(text)}" ile eşleşen lig yok.</div>', unsafe_allow_html=True)
        return
    counts = {}
    for card in cp.career_club_cards(db):
        counts[card.league_id] = counts.get(card.league_id, 0) + 1
    st.markdown(f"#### Ligler ({len(shown)})")
    lk.link_table(LEAGUES_KEY, pd.DataFrame([{"Lig": lg.name, "Ülke": lg.country, "Kulüp": counts.get(lg.id, 0)}
                                             for lg in shown]),
                  leagues={"Lig": [lg.id for lg in shown]}, nations={"Ülke": [lg.country for lg in shown]})


def _nations(db, text: str) -> None:
    countries = sorted({c for (c,) in db.execute(select(League.country).distinct()) if c}
                       | {n for (n,) in db.execute(select(Player.nationality).distinct()) if n},
                       key=pv.tr_sort_key)
    needle = plain_key(text)
    shown = rank_names(countries, needle, str) if needle else countries
    if not shown:
        st.markdown(f'<div class="cm-empty">"{md_escape(text)}" ile eşleşen ülke yok.</div>', unsafe_allow_html=True)
        return
    st.markdown(f"#### Ülkeler ({len(shown)})")
    lk.link_table(NATIONS_KEY, pd.DataFrame([{"Ülke": name} for name in shown]), nations={"Ülke": shown})


def _staff(db, text: str) -> None:
    if _short(text):
        return
    rows = db.execute(select(Staff.id, Staff.name, Staff.role, Staff.team_id, Team.name)
                      .outerjoin(Team, Team.id == Staff.team_id)).all()
    hits = rank_names(rows, plain_key(text), lambda r: r[1])[:LIMIT]
    if not hits:
        st.markdown(f'<div class="cm-empty">"{md_escape(text)}" ile eşleşen personel yok.</div>',
                    unsafe_allow_html=True)
        return
    st.markdown(f"#### Personel ({len(hits)})")
    lk.link_table(STAFF_KEY, pd.DataFrame([{"Ad": name, "Rol": staff_rules.ROLE_LABELS.get(role, str(role)),
                                            "Kulüp": club or "Boşta"} for _sid, name, role, _tid, club in hits]),
                  clubs={"Kulüp": [tid for _sid, _n, _r, tid, _c in hits]})


__all__ = ["TABS", "cb_fd_more", "cb_fd_reset_more", "find_players", "match_rank", "player_pool", "rank_names",
           "render_find"]
