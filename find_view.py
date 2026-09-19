"""
find_view.py
============
Faz 14S: "Bul" (CM 01/02 "Find") -- asgari surum. Ad aramasi; sekmeler Oyuncu / Kulup (CM'de Nation / Club /
Non-Player / Player). Oyuncuya tik -> CM profil ekrani (player_view, alan "find"); kulube tik -> Ulkeler ve Kulupler
sayfasinda o kulubun kadrosu. Tam arama (filtreler, personel) 14F'nin isidir.

SUNUM + okuma sorgulari; veritabanina YAZMAZ. Arama en az MIN_QUERY harf ister ve en fazla LIMIT sonuc dondurur
(sayfa basina TEK oyuncu sorgusu; kulup aramasi kulup kartlari uzerinden, TEK sorgu).
K12: sonuclarda guc / potansiyel / deger yok -- yalnizca ad, mevki, yas, kulup.

WIDGET ANAHTARLARI: fd_query (arama kutusu), fd_tab (Oyuncu / Kulup), fd_players / fd_clubs (sonuc tablolari).
"""

from __future__ import annotations

import functools

import pandas as pd
import streamlit as st
from sqlalchemy import select

import club_picker_view as cp
import club_view
import nav_view
import player_view as pv
from club_directory import plain_key
from models import Player, Team
from web_common import requires_auth

QUERY_KEY, TAB_KEY, PLAYERS_KEY, CLUBS_KEY = "fd_query", "fd_tab", "fd_players", "fd_clubs"
TAB_PLAYER, TAB_CLUB = "Oyuncu", "Kulüp"
TABS = (TAB_PLAYER, TAB_CLUB)
MIN_QUERY = 2
LIMIT = 50


def _like(query: str) -> str:
    """ILIKE deseni: % ve _ kacisli (kullanici metni desen olarak yorumlanmaz)."""
    text = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{text}%"


def find_players(db, query: str, limit: int = LIMIT) -> list[tuple[int, str, str, int, str]]:
    """(id, ad, mevki, yas, kulup) -- ada gore (buyuk / kucuk harf duyarsiz) TEK sorgu; akademi oyunculari haric."""
    if len(query.strip()) < MIN_QUERY:
        return []
    rows = db.execute(select(Player.id, Player.name, Player.position, Player.age, Team.name)
                      .outerjoin(Team, Team.id == Player.team_id)
                      .where(Player.name.ilike(_like(query), escape="\\"), Player.in_academy.is_(False))
                      .order_by(Player.name).limit(limit)).all()
    return [(pid, name, pos.value, int(age), club or "Kulüpsüz") for pid, name, pos, age, club in rows]


@requires_auth
def cb_fd_club(key: str) -> None:
    """Kulup sonucuna tiklandi: Ulkeler ve Kulupler'de o kulubun kadrosu (yalnizca oturum durumu)."""
    team_id = pv.row_player(key)
    st.session_state[key] = {"selection": {"rows": [], "columns": [], "cells": []}}
    if team_id is not None:
        club_view.open_club(team_id)
        nav_view.goto(nav_view.NATIONS, section=nav_view.SEC_NATIONS)


def render_find(db, cm, team: Team | None) -> None:
    """Bul sayfasi (web_app.PAGE_RENDERERS)."""
    query = st.text_input("Ara", key=QUERY_KEY, placeholder="Oyuncu ya da kulüp adı (en az 2 harf)",
                          label_visibility="collapsed")
    if st.session_state.get(TAB_KEY) not in TABS:
        st.session_state[TAB_KEY] = TAB_PLAYER
    tab = st.segmented_control("Tür", list(TABS), key=TAB_KEY, required=True, label_visibility="collapsed",
                               width="stretch")
    text = (query or "").strip()
    if len(text) < MIN_QUERY:
        st.caption("Aramak için en az 2 harf yaz. Oyuncuya tıklayınca profili, kulübe tıklayınca kadrosu açılır.")
        return
    if tab == TAB_CLUB:
        key = plain_key(text)
        clubs = [c for c in cp.career_club_cards(db) if key in plain_key(c.name)]
        clubs = sorted(clubs, key=lambda c: plain_key(c.name))[:LIMIT]
        if not clubs:
            st.caption(f"'{text}' ile eşleşen kulüp yok.")
            return
        st.markdown(f"#### '{_md(text)}' ile eşleşen kulüpler ({len(clubs)})")
        st.session_state[pv.table_ids_key(CLUBS_KEY)] = [c.team_id for c in clubs]
        st.dataframe(pd.DataFrame([{"Kulüp": c.name, "Lig": c.league_name, "Ülke": c.country} for c in clubs]),
                     key=CLUBS_KEY, on_select=functools.partial(cb_fd_club, CLUBS_KEY),
                     selection_mode=["single-row", "single-cell"], hide_index=True, width="stretch")
        return
    players = find_players(db, text)
    if not players:
        st.caption(f"'{text}' ile eşleşen oyuncu yok.")
        return
    st.markdown(f"#### '{_md(text)}' ile eşleşen oyuncular ({len(players)}{'+' if len(players) >= LIMIT else ''})")
    pv.selectable_table(pv.AREA_FIND, pd.DataFrame([
        {"Oyuncu": name, "Mv": pos, "Yaş": age, "Kulüp": club} for _pid, name, pos, age, club in players
    ]), [p[0] for p in players], key=PLAYERS_KEY)


def _md(text: str) -> str:
    from web_common import md_escape

    return md_escape(text)


__all__ = ["cb_fd_club", "find_players", "render_find"]
