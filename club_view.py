"""
club_view.py
============
Faz 14S / 14F: "Ülkeler ve Kulüpler" (CM 01/02 "Nations & Clubs"), KULUP SAYFASI ve ULKE SAYFASI.

    render_nations   Ulkeler ve Kulupler (menu): ulke -> lig -> kulup listesi; kulube tik -> kulup sayfasi
    render_club      kulup sayfasi (?sayfa=takim&id=..): kulup renginde bant + sekmeler (club_tab)
                       Kadro        yogun liste (oyuncuya tik -> oyuncu sayfasi, uyruga tik -> ulke sayfasi)
                       Fikstür      bu sezonun maclari (rakibe tik -> rakibin kulup sayfasi)
                       Genel Bilgi  lig (-> lig sayfasi), ulke, sira / puan / form, stat, itibar ETIKETI; kendi
                                    kulubunde butceler ve tesis duzeyleri (K12: baska kulubun defteri kapali)
                       Tarih        sampiyonluklar, sezon sezon lig sirasi (SeasonStanding), son transferler, rekorlar
    render_nation    ulke sayfasi (?sayfa=ulke&ad=..): ligleri, kulupleri, o uyruktan en iyi 20 oyuncu (sisli)

SUNUM + okuma sorgulari; veritabanina YAZMAZ. Callback'ler yalnizca oturum durumu yazar (requires_auth).
Sorgu butcesi: kulup kartlari TEK sorgu (club_picker_view.career_club_cards); kulup kadrosu TEK sorgu + bilgi haritasi
IKI sorgu; fikstur TEK sorgu (home_view.team_fixtures); tarih sekmesi sabit (4).

K12 (14G tek sis modeli, career_views.player_fog): baska kulubun oyuncusunda yetenek / deger bilgi esiginden (%25 alti
"?"), sozlesme %75 bilgiyle; motorun 1-99 sayilari hicbir hucrede yok. Kendi kulubunde kesin.

WIDGET / OTURUM ANAHTARLARI:
    nc_country / nc_league / nc_clubs   Ulkeler ve Kulupler (ulke, lig, kulup listesi)
    club_tab                            kulup sayfasi sekmesi
    lk_club_squad / lk_club_fixtures    kulup sayfasi tablolari (links_view)
    lk_nation_clubs / lk_nation_players / lk_nation_leagues   ulke sayfasi tablolari
"""

from __future__ import annotations

import functools

import pandas as pd
import streamlit as st
from sqlalchemy import or_, select

import career_views as cv
import club_picker_view as cp
import home_view
import links_view as lk
import nav_view
import player_view as pv
from finance import format_money
from models import League, Player, SeasonStanding, Team, TransferLog
from web_common import requires_auth

COUNTRY_KEY, LEAGUE_KEY, CLUBS_KEY, CLUB_KEY, SQUAD_KEY = "nc_country", "nc_league", "nc_clubs", "nc_club", "nc_squad"
CLUB_TAB_KEY = "club_tab"
TAB_SQUAD, TAB_FIXTURES, TAB_INFO, TAB_HISTORY = "Kadro", "Fikstür", "Genel Bilgi", "Tarih"
CLUB_TABS = (TAB_SQUAD, TAB_FIXTURES, TAB_INFO, TAB_HISTORY)
POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
NATION_TOP = 20
# Kulubun itibari (1-100) -> CM etiketi (sayi yazilmaz)
CLUB_REPUTATION_LABELS: tuple[tuple[int, str], ...] = (
    (85, "Dünya çapında"), (75, "Kıta çapında"), (65, "Ulusal"), (50, "Bölgesel"), (0, "Yerel"))
NOT_FOUND_TEXT = "Kulüp bulunamadı: bu dünyada yok."


def reputation_label(reputation: int | None) -> str:
    value = int(reputation or 0)
    return next(text for low, text in CLUB_REPUTATION_LABELS if value >= low)


def open_club(team_id: int) -> None:
    """Kulup sayfasini acar (Bul, Ulkeler ve Kulupler ve testler)."""
    nav_view.goto(nav_view.CLUB_PAGE, param=int(team_id))


@requires_auth
def cb_nc_row(key: str) -> None:
    """Kulup listesinde satira tiklandi: o kulubun sayfasi acilir (yalnizca oturum durumu)."""
    team_id = pv.row_player(key)                # ayni esleme mantigi: {tablo}__ids satir -> id
    st.session_state[key] = {"selection": {"rows": [], "columns": [], "cells": []}}
    if team_id is not None:
        open_club(team_id)


@requires_auth
def cb_nc_back() -> None:
    """Eski (14S) 'Kulüp listesine dön': Ulkeler ve Kulupler'e."""
    nav_view.goto(nav_view.NATIONS, section=nav_view.SEC_NATIONS)


def club_squad(db, team_id: int) -> list[tuple[int, str, str, int, str]]:
    """(id, ad, mevki, yas, uyruk) -- A takim, TEK sorgu, mevki sirasiyla."""
    rows = db.execute(select(Player.id, Player.name, Player.position, Player.age, Player.nationality)
                      .where(Player.team_id == int(team_id), Player.in_academy.is_(False))).all()
    return sorted(((pid, name, pos.value, int(age), nat or "") for pid, name, pos, age, nat in rows),
                  key=lambda r: (POSITION_ORDER.get(r[2], 9), r[1]))


# ===========================================================================
# ULKELER VE KULUPLER (menu)
# ===========================================================================

def render_nations(db, cm, team: Team | None) -> None:
    """Ulkeler ve Kulupler sayfasi (web_app.PAGE_RENDERERS)."""
    cards = cp.career_club_cards(db)
    if not cards:
        st.info("Bu dünyada kulüp yok.")
        return
    countries = cp.countries(cards)
    _sync(COUNTRY_KEY, countries, default=_home_country(team, cards, countries))
    country = st.pills("Ülke", countries, key=COUNTRY_KEY, label_visibility="collapsed")
    leagues = cp.leagues_of(cards, country)
    league_ids = [lid for lid, _name, _n in leagues]
    names = {lid: f"{name} · {n}" for lid, name, n in leagues}
    _sync(LEAGUE_KEY, league_ids, default=league_ids[0] if league_ids else None)
    if len(league_ids) > 1:
        st.pills("Lig", league_ids, key=LEAGUE_KEY, format_func=lambda i: names.get(i, str(i)),
                 label_visibility="collapsed")
    league_id = st.session_state.get(LEAGUE_KEY)
    clubs = cp.clubs_in(cards, league_id)
    if not clubs:
        st.caption("Bu ülkede kulüp yok.")
        return
    league_name = next((name for lid, name, _n in leagues if lid == league_id), "")
    if league_name:
        st.markdown(f"#### {cv_escape(league_name)}")
    frame = pd.DataFrame([{"Kulüp": c.name, "Lig": c.league_name, "İtibar": reputation_label(c.reputation),
                           "Stat": cp.capacity_text(c.stadium_capacity)} for c in clubs])
    st.session_state[pv.table_ids_key(CLUBS_KEY)] = [c.team_id for c in clubs]
    st.dataframe(frame, key=CLUBS_KEY, on_select=functools.partial(cb_nc_row, CLUBS_KEY),
                 selection_mode=["single-row", "single-cell"], hide_index=True, width="stretch",
                 row_height=lk.ROW_HEIGHT, height=lk.table_height(len(clubs)))
    st.caption("Kulübe tıkla: kulüp sayfası açılır.")
    with st.container(horizontal=True, key="nc_links"):
        if country:
            st.button(f"{country} · ülke sayfası", key="nc_nation", on_click=nav_view.cb_open_nation,
                      args=(country,))
        if league_id is not None:
            st.button(f"{league_name} · lig sayfası" if league_name else "Lig sayfası", key="nc_league_page",
                      on_click=nav_view.cb_open_league, args=(int(league_id),))


def cv_escape(text: str) -> str:
    from web_common import md_escape

    return md_escape(text)


def _home_country(team: Team | None, cards, countries: list[str]):
    if team is not None:
        own = next((c.country for c in cards if c.team_id == team.id), None)
        if own in countries:
            return own
    return countries[0] if countries else None


def _sync(key: str, options: list, *, default) -> None:
    if st.session_state.get(key) not in options:
        st.session_state[key] = default


# ===========================================================================
# KULUP SAYFASI
# ===========================================================================

def render_club(db, cm, team: Team | None) -> None:
    """Kulup sayfasi (web_app.PAGE_RENDERERS[CLUB_PAGE]); parametre: kulup id."""
    param = nav_view.current_param()
    club = db.get(Team, int(param)) if isinstance(param, int) else None
    if club is None:
        st.markdown(nav_view.band_html("Kulüp"), unsafe_allow_html=True)
        st.markdown(f'<div class="cm-empty">{NOT_FOUND_TEXT}</div>', unsafe_allow_html=True)
        st.button("Bul", key="club_nf_find", on_click=nav_view.cb_nav, args=(nav_view.FIND, nav_view.SEC_FIND))
        return
    league = club.league
    sub = " · ".join(x for x in (league.name if league else "", league.country if league else "") if x)
    st.markdown(nav_view.band_html(club.name, nav_view.club_band_colors(club.name), sub=sub), unsafe_allow_html=True)
    if st.session_state.get(CLUB_TAB_KEY) not in CLUB_TABS:
        st.session_state[CLUB_TAB_KEY] = TAB_SQUAD
    tab = st.segmented_control("Bölüm", list(CLUB_TABS), key=CLUB_TAB_KEY, required=True,
                               label_visibility="collapsed", width="stretch")
    own = team is not None and club.id == team.id
    if tab == TAB_FIXTURES:
        club_fixtures(db, cm, club)
    elif tab == TAB_INFO:
        club_info(db, cm, club, own)
    elif tab == TAB_HISTORY:
        club_history(db, cm, club)
    else:
        club_squad_tab(db, cm, team, club, own)


def squad_frame(cm, viewer: Team | None, players: list[Player], own: bool) -> tuple[pd.DataFrame, dict]:
    """Kulup kadrosu tablosu (TEK SIS MODELI): kendi kulubunde kesin sozcuk / sayisal deger / sozlesme yili;
    baska kulupte bilgi esiginden (? / aralik), sozlesme %75 bilgiyle. (tablo, column_config)."""
    fogs = cv.fog_rows(cm, viewer, players)
    rows = []
    for p in players:
        fog = fogs[p.id]
        row = {"Mv": p.position.value, "Oyuncu": p.name, "Yaş": p.age, "Uyruk": p.nationality or "—",
               cv.ABILITY_LABEL: fog.ability_text}
        if own:
            row["Değer (EUR)"] = int(p.market_value or 0)
            row["Maaş/hf (EUR)"] = int(p.current_wage or 0)
        else:
            row["Değer (EUR)"] = fog.value_text
        row["Sözleşme"] = fog.contract_text
        rows.append(row)
    config = {}
    if own:
        config = {"Değer (EUR)": st.column_config.NumberColumn("Değer (EUR)", format="compact"),
                  "Maaş/hf (EUR)": st.column_config.NumberColumn("Maaş/hf (EUR)", format="compact")}
    return pd.DataFrame(rows), config


def club_squad_tab(db, cm, team: Team | None, club: Team, own: bool) -> None:
    players = sorted(db.scalars(select(Player).where(Player.team_id == club.id, Player.in_academy.is_(False))),
                     key=lambda p: (POSITION_ORDER.get(p.position.value, 9), p.name))
    if not players:
        st.caption("Kadroda oyuncu yok.")
        return
    frame, config = squad_frame(cm, team, players, own)
    lk.link_table("lk_club_squad", frame, players=[p.id for p in players],
                  nations={"Uyruk": [p.nationality for p in players]}, column_config=config)
    if not own:
        st.caption("Başka kulübün oyuncuları: yetenek ve değer gözlemcinin bilgisi kadar (%25 altında \"?\"), "
                   "sözleşme %75 bilgiyle görünür.")


def club_fixtures(db, cm, club: Team) -> None:
    fixtures = home_view.team_fixtures(db, club.id, int(cm.season))
    if not fixtures:
        st.caption("Bu sezon için fikstür yok.")
        return
    frame = pd.DataFrame([{"Hf": f.week, "Turnuva": f.competition_label, "Rakip": f.opponent,
                           "Yer": "İç saha" if f.at_home else "Deplasman", "Skor": f.score_text,
                           "Sonuç": f.result or "—"} for f in fixtures])
    lk.link_table("lk_club_fixtures", frame, clubs={"Rakip": [f.opponent_id for f in fixtures]})


def club_info(db, cm, club: Team, own: bool) -> None:
    league = club.league
    rows: list[tuple[str, object]] = [("Kulüp", club.name)]
    if league is not None:
        rows.append(("Lig", league.name))
        rows.append(("Ülke", league.country))
        table = cm.standings(league.id)
        position = next((i for i, t in enumerate(table, start=1) if t.id == club.id), None)
        if position:
            rows.append(("Lig sırası", f"{position}. / {len(table)}"))
        rows.append(("Puan", club.points))
    rows.append(("Form (son 5)", cm.team_form(club.id) or "—"))
    rows.append(("İtibar", reputation_label(club.reputation)))
    rows.append(("Stat", cp.capacity_text(club.stadium_capacity)))
    if own:
        rows += [("Transfer bütçesi (EUR)", cv.short_money(club.transfer_budget)),
                 ("Haftalık maaş havuzu (EUR)", cv.short_money(club.wage_budget)),
                 ("Altyapı tesisleri", f"{club.youth_facilities or '—'}/20"),
                 ("Sağlık merkezi", f"{club.medical_facilities or '—'}/20")]
        if club.sponsor_name:
            rows.append(("Sponsor", f"{club.sponsor_name} · {format_money(club.sponsor_weekly)}/hf"))
    st.markdown(nav_view.pairs_html(rows), unsafe_allow_html=True)
    if not own:
        st.caption("Başka kulübün bütçesi ve tesisleri kendi defterinde: yalnızca herkese açık bilgi gösterilir.")
    with st.container(horizontal=True, key="club_info_links"):
        if league is not None:
            st.button("Lig sayfası", key="club_go_league", on_click=nav_view.cb_open_league, args=(int(league.id),))
            st.button("Ülke sayfası", key="club_go_nation", on_click=nav_view.cb_open_nation,
                      args=(str(league.country),))


def club_history(db, cm, club: Team) -> None:
    honours = cm.club_honours(club)
    st.markdown(nav_view.pairs_html([("Lig şampiyonluğu", honours.league_titles), ("Kupa", honours.cup_titles),
                                     ("İkincilik", honours.runner_up_finishes)]), unsafe_allow_html=True)
    seasons = list(db.execute(select(SeasonStanding.season, League.name, SeasonStanding.position,
                                     SeasonStanding.points, SeasonStanding.league_id)
                              .outerjoin(League, League.id == SeasonStanding.league_id)
                              .where(SeasonStanding.team_id == club.id)
                              .order_by(SeasonStanding.season.desc())).all())
    st.markdown("#### Sezon sezon")
    if seasons:
        frame = pd.DataFrame([{"Sezon": s, "Lig": name or "—", "Sıra": f"{pos}.", "Puan": pts}
                              for s, name, pos, pts, _lid in seasons])
        lk.link_table("lk_club_seasons", frame, leagues={"Lig": [lid for *_x, lid in seasons]}, hint=False)
    else:
        st.caption("Geçmiş sezon yok: lig sırası ilk sezon bitince yazılır.")
    logs = list(db.scalars(select(TransferLog).where(
        or_(TransferLog.from_team_id == club.id, TransferLog.to_team_id == club.id))
        .order_by(TransferLog.id.desc()).limit(20)))
    st.markdown("#### Son transferler")
    if logs:
        transfer_table("lk_club_transfers", logs)
        incoming = [t for t in logs if t.to_team_id == club.id and t.fee]
        outgoing = [t for t in logs if t.from_team_id == club.id and t.fee]
        records = []
        if incoming:
            top = max(incoming, key=lambda t: t.fee)
            records.append(("Rekor gelen", f"{top.player_name} · {format_money(top.fee)}"))
        if outgoing:
            top = max(outgoing, key=lambda t: t.fee)
            records.append(("Rekor giden", f"{top.player_name} · {format_money(top.fee)}"))
        if records:
            st.markdown(nav_view.pairs_html(records), unsafe_allow_html=True)
    else:
        st.caption("Bu kariyerde kulübün transfer kaydı yok.")


def transfer_table(key: str, logs) -> None:
    """Transfer kaydi tablosu (TransferLog): oyuncu ve iki kulup tiklanir. Para sayisal (sirali)."""
    logs = list(logs)
    frame = pd.DataFrame([{"Sezon/Hafta": f"S{t.season} H{t.week}", "Oyuncu": t.player_name,
                           "Nereden": t.from_team_name or "Kulüpsüz", "Nereye": t.to_team_name,
                           "Bonservis (EUR)": int(t.fee or 0), "Maaş/hf (EUR)": int(t.wage or 0),
                           "Tür": "Kiralık" if t.kind == "LOAN" else "Transfer"} for t in logs])
    lk.link_table(key, frame, players=[t.player_id for t in logs],
                  clubs={"Nereden": [t.from_team_id for t in logs], "Nereye": [t.to_team_id for t in logs]},
                  column_config={"Bonservis (EUR)": st.column_config.NumberColumn("Bonservis (EUR)", format="compact"),
                                 "Maaş/hf (EUR)": st.column_config.NumberColumn("Maaş/hf (EUR)", format="compact")})


# ===========================================================================
# ULKE SAYFASI
# ===========================================================================

def render_nation(db, cm, team: Team | None) -> None:
    """Ulke sayfasi (web_app.PAGE_RENDERERS[NATION]); parametre: ulke adi (League.country / Player.nationality)."""
    name = nav_view.current_param()
    name = str(name) if name else ""
    st.markdown(nav_view.band_html(name or "Ülke"), unsafe_allow_html=True)
    cards = [c for c in cp.career_club_cards(db) if c.country == name]
    leagues = cp.leagues_of(cards, name)
    if leagues:
        st.markdown("#### Ligler")
        frame = pd.DataFrame([{"Lig": lname, "Kulüp sayısı": n} for _lid, lname, n in leagues])
        lk.link_table("lk_nation_leagues", frame, leagues={"Lig": [lid for lid, _n, _c in leagues]}, hint=False)
        st.markdown("#### Kulüpler")
        clubs = cp.sort_cards(cards)
        lk.link_table("lk_nation_clubs", pd.DataFrame([
            {"Kulüp": c.name, "Lig": c.league_name, "İtibar": reputation_label(c.reputation),
             "Stat": cp.capacity_text(c.stadium_capacity)} for c in clubs]),
            clubs={"Kulüp": [c.team_id for c in clubs]}, leagues={"Lig": [c.league_id for c in clubs]})
    players = list(db.scalars(select(Player).where(Player.nationality == name, Player.in_academy.is_(False))))
    if players:
        fogs = cv.fog_rows(cm, team, players)
        ranked = sorted(players, key=lambda p: (fogs[p.id].ability_mid is None, -(fogs[p.id].ability_mid or 0),
                                                p.name))[:NATION_TOP]
        clubs = cv_team_names(db, {p.team_id for p in ranked})
        st.markdown(f"#### {name} uyruklu en iyi oyuncular")
        lk.link_table("lk_nation_players", pd.DataFrame([
            {"Oyuncu": p.name, "Mv": p.position.value, "Yaş": p.age, "Kulüp": clubs.get(p.team_id, "Kulüpsüz"),
             cv.ABILITY_LABEL: fogs[p.id].ability_text} for p in ranked]),
            players=[p.id for p in ranked], clubs={"Kulüp": [p.team_id for p in ranked]})
        st.caption("Sıralama gözlemcinin bildiği kadarıyla: yeteneği bilinmeyen (\"?\") oyuncular sonda.")
    elif not leagues:
        st.markdown('<div class="cm-empty">Bu ülkede lig, kulüp ya da oyuncu yok.</div>', unsafe_allow_html=True)
    if team is not None and cm.rules.internationals:
        st.button("Milli Takım", key="nation_nt", on_click=nav_view.cb_nav, args=(nav_view.NATIONAL, nav_view.SEC_COMPS))


def cv_team_names(db, ids) -> dict[int, str]:
    return pv.team_names(db, ids)


__all__ = ["CLUB_TABS", "cb_nc_back", "cb_nc_row", "club_squad", "open_club", "render_club", "render_nation",
           "render_nations", "reputation_label", "squad_frame", "transfer_table"]
