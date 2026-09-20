"""
links_view.py
=============
Faz 14F: "tiklanabilir dunya" (CM 01/02: her isim tiklanir). Tek yardimci: link_table -- st.dataframe(on_select,
selection_mode=["single-row", "single-cell"]). Kulup / lig / ulke sutunundaki HUCREYE tik -> o sayfa; diger hucre ya
da satir -> oyuncu sayfasi (players verildiyse; yoksa satirin ilk kulubu / ligi). Satir -> id eslemesi SUNUCUDA tutulur
({anahtar}__links): istemci siralamasi ozgun satir sirasini dondurdugu icin esleme cizim sirasina gore. Secim
tiklamadan sonra temizlenir (ayni satir yeniden tiklanabilir). Oturum durumu disinda hicbir sey yazmaz.

    link_table(key, frame, players=[id...], clubs={"Kulüp": [id...]}, leagues={"Lig": [id...]},
               nations={"Uyruk": ["Türkiye", ...]}, hint=True, **st.dataframe secenekleri)
    link_target(key)          secili hucrenin hedefi: ("oyuncu", 12) / ("takim", 4) / ("puan-durumu", 2) / ("ulke", "..")
    LINK_SURFACES             bagli tablolarin anahtarlari (14F §3.6: en az 12 yuzey; testler sayar)

Oyuncu sayfasinda ◄ ► tablonun oyuncu sirasiyla gezer (player_view.LIST_KEY). K12: bu modul hicbir sayi uretmez;
tablonun icerigi cagiranin sisli satirlaridir.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence

import pandas as pd
import streamlit as st

import nav_view
from web_common import requires_auth

LINKS_SUFFIX = "__links"
LINK_HINT = "Oyuncu adına tıkla: oyuncu ekranı · kulüp adına tıkla: kulüp sayfası."
CLUB_HINT = "Kulüp adına tıkla: kulüp sayfası."
EMPTY_SELECTION = {"selection": {"rows": [], "columns": [], "cells": []}}
ROW_HEIGHT = 28
MAX_ROWS_SHOWN = 32

# 14F §3.6: bagli yuzeyler (tablo anahtari ya da "_" ile biten anahtar oneki -> yer). Testler bu listeden sayar (>= 12).
LINK_SURFACES: dict[str, str] = {
    "lk_standings": "Lig sayfası · tablo",
    "lk_comp_week": "Lig sayfası · haftanın maçları",
    "lk_comp_goals": "Lig sayfası · gol krallığı",
    "lk_comp_assists": "Lig sayfası · asist krallığı",
    "lk_comp_rating": "Lig sayfası · ortalama not",
    "lk_comp_history": "Lig sayfası · şampiyonlar",
    "lk_comp_season": "Lig sayfası · geçmiş sezon tablosu",
    "lk_fixtures": "Fikstür ve Sonuçlar · kulübün fikstürü",
    "lk_week_results": "Fikstür ve Sonuçlar · haftanın sonuçları",
    "lk_arena_goals": "Devler Arenası · gol krallığı",
    "lk_arena_assists": "Devler Arenası · asist krallığı",
    "lk_arena_out": "Devler Arenası · sakatlar ve cezalılar",
    "lk_arena_results": "Devler Arenası · sonuçlar",
    "lk_arena_teams": "Devler Arenası · katılımcılar",
    "lk_honours": "Haberler ve Tarih · onur listesi",
    "lk_records": "Haberler ve Tarih · rekor transferler",
    "lk_my_transfers": "Haberler ve Tarih · kulübümün transferleri",
    "lk_season_transfers": "Haberler ve Tarih · sezonun transferleri",
    "lk_prev_watch_": "Taktik · maç önü · dikkat edilecek oyuncular",
    "lk_prev_absent_": "Taktik · maç önü · eksikler",
    "lk_scout_xi": "Taktik · rakip gözlem · muhtemel ilk 11",
    "lk_scout_absent": "Taktik · rakip gözlem · eksikler",
    "lk_scout_watch": "Taktik · rakip gözlem · dikkat edilecek oyuncular",
    "lk_planner_": "Taktik · kadro planlayıcı",
    "lk_wages": "Finans · en yüksek maaşlar",
    "lk_concerns": "Kadro · oyuncu memnuniyeti",
    "lk_intake": "Akademi · bu sezonun genç girişi",
    "lk_my_players": "Transfer Merkezi · oyuncularım",
    "lk_payments": "Transfer Merkezi · ödemeler",
    "tc_row_": "Transfer Merkezi · dosya kartları (Oyuncu)",
    "tc_in_": "Transfer Merkezi · gelen teklif kartları (Oyuncu)",
    "home_pl": "Gelen Kutusu · sakatlık / ceza / maaş talebi (oyuncu)",
    "lk_club_squad": "Kulüp sayfası · kadro",
    "lk_club_fixtures": "Kulüp sayfası · fikstür",
    "lk_club_seasons": "Kulüp sayfası · sezon sezon",
    "lk_club_transfers": "Kulüp sayfası · son transferler",
    "lk_find_players": "Bul · oyuncular",
    "lk_find_clubs": "Bul · kulüpler",
    "lk_find_leagues": "Bul · ligler",
    "lk_find_nations": "Bul · ülkeler",
    "lk_find_staff": "Bul · personel",
    "lk_nation_leagues": "Ülke sayfası · ligler",
    "lk_nation_clubs": "Ülke sayfası · kulüpler",
    "lk_nation_players": "Ülke sayfası · en iyi oyuncular",
    "lk_nt_squad": "Milli Takım · milli kadro",
}


def links_key(key: str) -> str:
    return f"{key}{LINKS_SUFFIX}"


def table_height(rows: int) -> int:
    """Yogun listenin yuksekligi: tum satirlar (en fazla MAX_ROWS_SHOWN) kaydirmasiz gorunsun."""
    return ROW_HEIGHT * (min(max(int(rows), 1), MAX_ROWS_SHOWN) + 1) + 3


def _ints(values: Sequence | None) -> list[int | None] | None:
    if values is None:
        return None
    out: list[int | None] = []
    for v in values:
        try:
            out.append(int(v) if v is not None and not isinstance(v, bool) else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def link_table(key: str, frame, *, players: Sequence[int | None] | None = None,
               clubs: Mapping[str, Sequence[int | None]] | None = None,
               leagues: Mapping[str, Sequence[int | None]] | None = None,
               nations: Mapping[str, Sequence[str | None]] | None = None,
               player_cols: Mapping[str, Sequence[int | None]] | None = None, hint: bool = True,
               dense: bool = True, **kwargs) -> None:
    """
    Tiklanabilir CM tablosu (bkz. modul basligi). Esleme listeleri frame satirlariyla AYNI sirada. players: satirin
    oyuncusu (satir / diger hucre tiki); player_cols: bir satirda birden cok oyuncu sutunu (gol krali, sezonun oyuncusu).
    frame bir pandas Styler da olabilir (kendi satirin vurgusu).
    """
    inner = getattr(frame, "data", None)                   # pandas Styler: .data asil tablodur
    rows = len(inner) if isinstance(inner, pd.DataFrame) else len(frame)
    st.session_state[links_key(key)] = {
        "players": _ints(players),
        "player_cols": {str(col): _ints(ids) for col, ids in (player_cols or {}).items()},
        "clubs": {str(col): _ints(ids) for col, ids in (clubs or {}).items()},
        "leagues": {str(col): _ints(ids) for col, ids in (leagues or {}).items()},
        "nations": {str(col): [str(n) if n else None for n in names] for col, names in (nations or {}).items()},
    }
    options = {"hide_index": True, "width": "stretch", **kwargs}
    if dense:
        options.setdefault("row_height", ROW_HEIGHT)
        options.setdefault("height", table_height(rows))
    st.dataframe(frame, key=key, on_select=functools.partial(cb_link, key),
                 selection_mode=["single-row", "single-cell"], **options)
    if hint and rows:
        st.caption(hint_text(players is not None or bool(player_cols), bool(clubs), bool(leagues), bool(nations)))


def hint_text(players: bool, clubs: bool, leagues: bool = False, nations: bool = False) -> str:
    """Tablonun altindaki ipucu: yalnizca gercekten bagli olan adlar."""
    parts = [text for on, text in ((players, "oyuncu adına tıkla: oyuncu ekranı"),
                                   (clubs, "kulüp adına tıkla: kulüp sayfası"),
                                   (leagues, "lige tıkla: lig sayfası"), (nations, "ülkeye tıkla: ülke sayfası")) if on]
    return (" · ".join(parts) + ".").capitalize() if parts else ""


def _selection(state) -> tuple[int | None, str | None]:
    """(satir, sutun adi) -- hucre tiki (sutun ile) ya da satir tiki (sutun None)."""
    if not isinstance(state, dict):
        return None, None
    selection = state.get("selection")
    if not isinstance(selection, dict):
        return None, None
    cells = selection.get("cells")
    if isinstance(cells, list | tuple) and cells:
        cell = cells[0]
        if isinstance(cell, list | tuple) and len(cell) == 2:
            row, col = cell
            if isinstance(row, int) and not isinstance(row, bool):
                return row, str(col)
    rows = selection.get("rows")
    if isinstance(rows, list | tuple) and rows:
        row = rows[0]
        if isinstance(row, int) and not isinstance(row, bool):
            return row, None
    return None, None


def _at(values, row: int):
    if not isinstance(values, list) or not 0 <= row < len(values):
        return None
    return values[row]


def link_target(key: str, state=None) -> tuple[str, int | str] | None:
    """Tablonun secili hucresinin hedef sayfasi (sunucudaki esleme ile); gecersiz / aralik disi -> None."""
    links = st.session_state.get(links_key(key))
    if not isinstance(links, dict):
        return None
    row, col = _selection(st.session_state.get(key) if state is None else state)
    if row is None or row < 0:
        return None
    clubs, leagues, nations = links.get("clubs") or {}, links.get("leagues") or {}, links.get("nations") or {}
    player_cols = links.get("player_cols") or {}
    if col is not None:
        if col in player_cols and _at(player_cols[col], row) is not None:
            return nav_view.PLAYER, _at(player_cols[col], row)
        if col in clubs and _at(clubs[col], row) is not None:
            return nav_view.CLUB_PAGE, _at(clubs[col], row)
        if col in leagues and _at(leagues[col], row) is not None:
            return nav_view.TABLE, _at(leagues[col], row)
        if col in nations and _at(nations[col], row):
            return nav_view.NATION, _at(nations[col], row)
    player = _at(links.get("players"), row)
    if player is not None:
        return nav_view.PLAYER, player
    for mapping, page in ((clubs, nav_view.CLUB_PAGE), (leagues, nav_view.TABLE), (nations, nav_view.NATION),
                          (player_cols, nav_view.PLAYER)):
        for values in mapping.values():
            value = _at(values, row)
            if value is not None and value != "":
                return page, value
    return None


@requires_auth
def cb_link(key: str) -> None:
    """Bagli tabloya tiklandi: hedef sayfa acilir (oyuncu sayfasinda ◄ ► tablonun oyuncu listesiyle)."""
    target = link_target(key)
    st.session_state[key] = {"selection": {"rows": [], "columns": [], "cells": []}}
    if target is None:
        return
    page, value = target
    if page == nav_view.PLAYER:
        links = st.session_state.get(links_key(key)) or {}
        columns = [links.get("players") or [], *(links.get("player_cols") or {}).values()]
        ids = next(([i for i in col if i is not None] for col in columns if value in col), [value])
        st.session_state[nav_view.PROFILE_LIST_KEY] = list(dict.fromkeys(ids))
    section = nav_view.SEC_COMPS if page == nav_view.TABLE else None
    nav_view.goto(page, param=value, section=section)


def split_ids(rows: Sequence[Mapping]) -> tuple[pd.DataFrame, dict[str, list]]:
    """Satir sozluklerindeki gizli id sutunlarini ('_pid', '_tid', ... ; alt cizgiyle baslayan) ayirir:
    (gorunen tablo, {sutun: id listesi}). Id'ler ekrana hic gitmez."""
    rows = list(rows)
    hidden = sorted({k for r in rows for k in r if str(k).startswith("_")})
    ids = {k: [r.get(k) for r in rows] for k in hidden}
    frame = pd.DataFrame([{k: v for k, v in r.items() if not str(k).startswith("_")} for r in rows])
    return frame, ids


def open_buttons(prefix: str, items: Sequence[tuple[str, str, int | str]], *, limit: int = 6) -> None:
    """Satir ici kucuk baglanti dugmeleri (kart / mesaj govdesi): (etiket, sayfa, parametre) -> o sayfa."""
    shown = list(items)[:limit]
    if not shown:
        return
    with st.container(horizontal=True, key=f"{prefix}_links", gap="small"):
        for n, (label, page, value) in enumerate(shown):
            callback = {nav_view.PLAYER: nav_view.cb_open_player, nav_view.CLUB_PAGE: nav_view.cb_open_club,
                        nav_view.TABLE: nav_view.cb_open_league, nav_view.NATION: nav_view.cb_open_nation}.get(page)
            if callback is not None:
                st.button(label, key=f"{prefix}_lk_{n}", on_click=callback, args=(value,))


__all__ = ["LINK_HINT", "LINK_SURFACES", "cb_link", "link_table", "link_target", "links_key", "open_buttons",
           "table_height"]
