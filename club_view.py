"""
club_view.py
============
Faz 14S: "Ülkeler ve Kulüpler" (CM 01/02 "Nations & Clubs") -- asgari surum. Ulke -> lig -> kulup listesi; kulube
tiklayinca yogun kadro listesi (kulup renginde bant), oyuncuya tiklayinca CM profil ekrani (player_view, alan "clubs").
Tam kulup / yarisma sayfalari 14F'nin isidir; bu modul menunun bos kalmamasi icin gezinmeyi kurar.

SUNUM + okuma sorgulari; veritabanina YAZMAZ. Callback'ler yalnizca oturum durumu yazar (requires_auth).
Sorgu butcesi: kulup kartlari TEK sorgu (club_picker_view.career_club_cards), secili kulubun kadrosu TEK sorgu.

K12: baska kulubun kadrosunda guc / potansiyel / maas YOK -- yalnizca mevki, ad, yas, uyruk; ayrintisi profilde
gozlemci sisiyle.

WIDGET / OTURUM ANAHTARLARI:
    nc_country   ulke (st.pills)           nc_league   lig (st.pills; ulkede tek lig varsa kendiliginden)
    nc_clubs     kulup listesi (st.dataframe, satira tik -> kulup)   nc_club   secili kulubun id'si (oturum durumu)
    nc_back      "Kulüp listesine dön"     nc_squad    kulubun kadrosu (satira tik -> profil, pv.AREA_CLUBS)
"""

from __future__ import annotations

import functools

import pandas as pd
import streamlit as st
from sqlalchemy import select

import club_picker_view as cp
import nav_view
import player_view as pv
from models import Player, Team
from web_common import md_escape, requires_auth, reset_widgets

COUNTRY_KEY, LEAGUE_KEY, CLUBS_KEY, CLUB_KEY, SQUAD_KEY = "nc_country", "nc_league", "nc_clubs", "nc_club", "nc_squad"
POSITION_ORDER = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}


def open_club(team_id: int) -> None:
    """Kulup kadrosunu acar (Bul > Kulup ve testler de kullanir)."""
    st.session_state[CLUB_KEY] = int(team_id)


@requires_auth
def cb_nc_row(key: str) -> None:
    """Kulup listesinde satira tiklandi: o kulubun kadrosu acilir (yalnizca oturum durumu)."""
    team_id = pv.row_player(key)                # ayni esleme mantigi: {tablo}__ids satir -> id
    if team_id is not None:
        open_club(team_id)
    st.session_state[key] = {"selection": {"rows": [], "columns": [], "cells": []}}


@requires_auth
def cb_nc_back() -> None:
    reset_widgets(CLUB_KEY)


def club_squad(db, team_id: int) -> list[tuple[int, str, str, int, str]]:
    """(id, ad, mevki, yas, uyruk) -- A takim, TEK sorgu, mevki sirasiyla."""
    rows = db.execute(select(Player.id, Player.name, Player.position, Player.age, Player.nationality)
                      .where(Player.team_id == int(team_id), Player.in_academy.is_(False))).all()
    return sorted(((pid, name, pos.value, int(age), nat or "") for pid, name, pos, age, nat in rows),
                  key=lambda r: (POSITION_ORDER.get(r[2], 9), r[1]))


def render_nations(db, cm, team: Team | None) -> None:
    """Ulkeler ve Kulupler sayfasi (web_app.PAGE_RENDERERS)."""
    club_id = st.session_state.get(CLUB_KEY)
    club = db.get(Team, int(club_id)) if isinstance(club_id, int) else None
    if club is not None:
        _club_squad(db, club)
        return
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
        st.markdown(f"#### {md_escape(league_name)}")
    frame = pd.DataFrame([{"Kulüp": c.name, "Lig": c.league_name, "Stat": cp.capacity_text(c.stadium_capacity)}
                          for c in clubs])
    st.session_state[pv.table_ids_key(CLUBS_KEY)] = [c.team_id for c in clubs]
    st.dataframe(frame, key=CLUBS_KEY, on_select=functools.partial(cb_nc_row, CLUBS_KEY),
                 selection_mode=["single-row", "single-cell"], hide_index=True, width="stretch")
    st.caption("Kulübe tıkla: kadrosu açılır.")


def _club_squad(db, club: Team) -> None:
    """Secili kulubun yogun kadro listesi: kulup renginde bant; satira tik -> CM profil ekrani."""
    league = club.league.name if club.league is not None else ""
    st.markdown(nav_view.band_html(club.name, nav_view.club_band_colors(club.name), sub=league),
                unsafe_allow_html=True)
    rows = club_squad(db, club.id)
    if rows:
        pv.selectable_table(pv.AREA_CLUBS, pd.DataFrame([
            {"Mv": pos, "Oyuncu": name, "Yaş": age, "Uyruk": nat or "—"} for _pid, name, pos, age, nat in rows
        ]), [r[0] for r in rows], key=SQUAD_KEY, row_height=pv.ROW_HEIGHT, height=pv.table_height(len(rows)))
    else:
        st.caption("Kadroda oyuncu yok.")
    st.button("Kulüp listesine dön", key="nc_back", on_click=cb_nc_back)


def _home_country(team: Team | None, cards, countries: list[str]):
    if team is not None:
        own = next((c.country for c in cards if c.team_id == team.id), None)
        if own in countries:
            return own
    return countries[0] if countries else None


def _sync(key: str, options: list, *, default) -> None:
    if st.session_state.get(key) not in options:
        st.session_state[key] = default


__all__ = ["cb_nc_back", "cb_nc_row", "club_squad", "open_club", "render_nations"]
