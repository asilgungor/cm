"""
competition_view.py
===================
Faz 14F: lig (yarisma) sayfasi -- CM 01/02 "Competitions" ekrani. Puan Durumu sayfasi (nav_view.TABLE) lig
parametresi alir (?sayfa=puan-durumu&lig=7; menuden gelinirse kendi ligin). Eski web_app.standings_page'in yerine.

SEKMELER (comp_tab):
    Tablo                lig tablosu (kulup adina tik -> kulup sayfasi; kendi satirin sari)
    Hafta hafta          hafta secici (comp_week; varsayilan son oynanan hafta): o haftanin butun maclari
    İstatistikler        gol / asist / ortalama not krallari (not: en az sezon maclarinin %30'u) -- TEK GROUP BY
    Tarih                sezon basina sampiyon / ikinci / gol krali / sezonun oyuncusu (SeasonHonour) ve secilen gecmis
                         sezonun tam tablosu (SeasonStanding; comp_season)

SUNUM + okuma sorgulari; veritabanina YAZMAZ. Sorgu butcesi sekme basina sabit (kulup / oyuncu sayisindan bagimsiz):
tablo 1 + form (kulup basina, career_views.standings_rows), fikstur 1, istatistik 1, tarih 2. Her isim tiklanir
(links_view.link_table). K12: yalnizca oynanmis maclarin herkese acik istatistikleri.

WIDGET / OTURUM ANAHTARLARI: lg_league (lig secici; nav parametresiyle esitlenir), comp_tab, comp_week, comp_season.
"""

from __future__ import annotations

import math

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

import career_views as cv
import links_view as lk
import nav_view
from models import (
    Competition,
    Fixture,
    FixtureStatus,
    League,
    Player,
    PlayerMatchStat,
    SeasonHonour,
    SeasonStanding,
    Team,
)
from web_common import requires_auth, show_flash

LEAGUE_KEY, TAB_KEY, WEEK_KEY, SEASON_KEY = "lg_league", "comp_tab", "comp_week", "comp_season"
TAB_TABLE, TAB_FIXTURES, TAB_STATS, TAB_HISTORY = "Tablo", "Hafta hafta", "İstatistikler", "Tarih"
TABS = (TAB_TABLE, TAB_FIXTURES, TAB_STATS, TAB_HISTORY)
TOP_N = 20
RATING_SHARE = 0.30                    # ortalama not listesi: en az sezon maclarinin %30'u
OWN_ROW_CSS = "color:#ffcc33;font-weight:700"


def league_id_for(cm, team: Team | None, leagues: list[League]) -> int | None:
    """Sayfanin ligi: nav parametresi (gecerli bir lig ise) -> kendi ligin -> ilk lig."""
    ids = [lg.id for lg in leagues]
    param = nav_view.current_param() if st.session_state.get(nav_view.NAV_KEY) == nav_view.TABLE else None
    if isinstance(param, int) and param in ids:
        return param
    if team is not None and team.league_id in ids:
        return team.league_id
    return ids[0] if ids else None


def league_title(db, cm, team: Team | None) -> str | None:
    """CM bandinin yazisi (web_app.screen_title): sayfanin liginin adi."""
    leagues = cm.leagues()
    league_id = league_id_for(cm, team, leagues)
    return next((lg.name for lg in leagues if lg.id == league_id), None)


@requires_auth
def cb_comp_league() -> None:
    """Lig secici degisti: sayfanin parametresi olur (adres ve Geri / Ileri gecmisi o ligi hatirlar)."""
    value = st.session_state.get(LEAGUE_KEY)
    if st.session_state.get(nav_view.NAV_KEY) == nav_view.TABLE and isinstance(value, int):
        nav_view.replace_param(value)


def render_competition(db, cm, team: Team | None) -> None:
    """Lig sayfasi (web_app.PAGE_RENDERERS[TABLE])."""
    show_flash("league")
    leagues = cm.leagues()
    if not leagues:
        st.info("Bu dünyada lig yok.")
        return
    league_id = league_id_for(cm, team, leagues)
    names = {lg.id: lg.name for lg in leagues}
    if st.session_state.get(LEAGUE_KEY) != league_id:
        st.session_state[LEAGUE_KEY] = league_id
    st.selectbox("Lig", list(names), key=LEAGUE_KEY, format_func=lambda i: names.get(i, str(i)),
                 on_change=cb_comp_league, label_visibility="collapsed")
    if st.session_state.get(TAB_KEY) not in TABS:
        st.session_state[TAB_KEY] = TAB_TABLE
    tab = st.segmented_control("Bölüm", list(TABS), key=TAB_KEY, required=True, label_visibility="collapsed",
                               width="stretch")
    own = team.id if team is not None else None
    if tab == TAB_FIXTURES:
        fixtures_tab(db, cm, league_id, own)
    elif tab == TAB_STATS:
        stats_tab(db, cm, league_id)
    elif tab == TAB_HISTORY:
        history_tab(db, cm, league_id, names.get(league_id, ""), own)
    else:
        table_tab(db, cm, league_id, own)


# ---------------------------------------------------------------------------
# Puan durumu
# ---------------------------------------------------------------------------

def _highlight(frame: pd.DataFrame, rows: set[int]):
    """Kendi satirin sari (CM); pandas Styler (Streamlit tablo rengini tasir)."""
    if not rows:
        return frame
    return frame.style.apply(lambda r: [OWN_ROW_CSS if r.name in rows else "" for _ in r], axis=1)


def table_tab(db, cm, league_id: int, own: int | None) -> None:
    table = cm.standings(league_id)
    if not table:
        st.caption("Bu ligde kulüp yok.")
        return
    rows = cv.standings_rows(cm, league_id, own)
    frame = pd.DataFrame([{"#": r["#"], "Kulüp": str(r["Takım"]).removeprefix("► "), "O": r["O"], "G": r["G"],
                           "B": r["B"], "M": r["M"], "A": r["A"], "Y": r["Y"], "Av": r["Av"], "P": r["P"],
                           "Form": r["Form"]} for r in rows])
    mine = {i for i, t in enumerate(table) if t.id == own}
    lk.link_table("lk_standings", _highlight(frame, mine), clubs={"Kulüp": [t.id for t in table]},
                  column_config={"#": st.column_config.NumberColumn("#", width="small"),
                                 "Kulüp": st.column_config.TextColumn("Kulüp", width="medium")})


# ---------------------------------------------------------------------------
# Fikstur ve sonuclar (hafta tarayicisi)
# ---------------------------------------------------------------------------

def league_weeks(db, league_id: int, season: int) -> list[int]:
    return [int(w) for (w,) in db.execute(
        select(Fixture.week).where(Fixture.league_id == league_id, Fixture.season == season,
                                   Fixture.competition == Competition.LEAGUE).distinct().order_by(Fixture.week))]


def week_fixtures(db, league_id: int, season: int, week: int) -> list[tuple]:
    """(ev id, ev adi, skor, dep id, dep adi) -- o haftanin lig maclari, TEK sorgu (iki kulup adi JOIN)."""
    home, away = aliased(Team), aliased(Team)
    rows = db.execute(
        select(Fixture, home.name, away.name).join(home, home.id == Fixture.home_team_id)
        .join(away, away.id == Fixture.away_team_id)
        .where(Fixture.league_id == league_id, Fixture.season == season, Fixture.week == week,
               Fixture.competition == Competition.LEAGUE).order_by(Fixture.id)).all()
    out = []
    for fx, home_name, away_name in rows:
        played = fx.status is FixtureStatus.PLAYED
        score = f"{fx.home_score} - {fx.away_score}" if played else "v"
        out.append((fx.home_team_id, home_name, score, fx.away_team_id, away_name))
    return out


def fixtures_tab(db, cm, league_id: int, own: int | None) -> None:
    weeks = league_weeks(db, league_id, int(cm.season))
    if not weeks:
        st.caption("Bu sezon için fikstür yok.")
        return
    last = cm.last_played_week()
    default = last if last in weeks else weeks[0]
    if st.session_state.get(WEEK_KEY) not in weeks:
        st.session_state[WEEK_KEY] = default
    st.selectbox("Hafta", weeks, key=WEEK_KEY, format_func=lambda w: f"{w}. hafta")
    rows = week_fixtures(db, league_id, int(cm.season), int(st.session_state[WEEK_KEY]))
    if not rows:
        st.caption("Bu hafta maç yok.")
        return
    frame = pd.DataFrame([{"Ev sahibi": h, "Skor": s, "Deplasman": a} for _hid, h, s, _aid, a in rows])
    mine = {i for i, r in enumerate(rows) if own in (r[0], r[3])}
    lk.link_table("lk_comp_week", _highlight(frame, mine),
                  clubs={"Ev sahibi": [r[0] for r in rows], "Deplasman": [r[3] for r in rows]})


# ---------------------------------------------------------------------------
# Istatistikler (tek GROUP BY)
# ---------------------------------------------------------------------------

def season_player_stats(db, league_id: int, season: int) -> list[dict]:
    """Ligin bu sezonki oyuncu istatistikleri TEK sorguda: oyuncu, kulup, mac, gol, asist, ort. not."""
    rows = db.execute(
        select(PlayerMatchStat.player_id, Player.name, PlayerMatchStat.team_id, Team.name,
               func.count(PlayerMatchStat.id), func.coalesce(func.sum(PlayerMatchStat.goals), 0),
               func.coalesce(func.sum(PlayerMatchStat.assists), 0), func.avg(PlayerMatchStat.rating))
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .join(Player, Player.id == PlayerMatchStat.player_id)
        .join(Team, Team.id == PlayerMatchStat.team_id)
        .where(Fixture.league_id == league_id, Fixture.season == season, Fixture.competition == Competition.LEAGUE)
        .group_by(PlayerMatchStat.player_id, Player.name, PlayerMatchStat.team_id, Team.name)).all()
    return [{"pid": int(pid), "name": name, "tid": int(tid), "club": club, "apps": int(apps), "goals": int(goals),
             "assists": int(assists), "rating": float(rating) if rating is not None else None}
            for pid, name, tid, club, apps, goals, assists, rating in rows]


def _leader_table(key: str, rows: list[dict], value: str, label: str, fmt=str) -> None:
    if not rows:
        st.caption("Henüz veri yok.")
        return
    frame = pd.DataFrame([{"#": i, "Oyuncu": r["name"], "Kulüp": r["club"], label: fmt(r[value]), "Maç": r["apps"]}
                          for i, r in enumerate(rows, start=1)])
    lk.link_table(key, frame, players=[r["pid"] for r in rows], clubs={"Kulüp": [r["tid"] for r in rows]},
                  hint=False)


def stats_tab(db, cm, league_id: int) -> None:
    stats = season_player_stats(db, league_id, int(cm.season))
    goals = sorted((r for r in stats if r["goals"] > 0), key=lambda r: (-r["goals"], -r["assists"], r["name"]))
    assists = sorted((r for r in stats if r["assists"] > 0), key=lambda r: (-r["assists"], -r["goals"], r["name"]))
    played = max((r["apps"] for r in stats), default=0)
    need = max(1, math.ceil(played * RATING_SHARE))
    rated = sorted((r for r in stats if r["rating"] is not None and r["apps"] >= need),
                   key=lambda r: (-r["rating"], -r["apps"], r["name"]))
    left, mid, right = st.columns(3, gap="small")
    with left:
        st.markdown("#### Gol krallığı")
        _leader_table("lk_comp_goals", goals[:TOP_N], "goals", "Gol")
    with mid:
        st.markdown("#### Asist krallığı")
        _leader_table("lk_comp_assists", assists[:TOP_N], "assists", "Ast")
    with right:
        st.markdown("#### Ortalama not")
        _leader_table("lk_comp_rating", rated[:TOP_N], "rating", "Ort. not", fmt=lambda v: f"{v:.2f}")
        st.caption(f"En az {need} maç (sezon maçlarının %{int(RATING_SHARE * 100)}'u).")
    st.caption(lk.LINK_HINT)


# ---------------------------------------------------------------------------
# Tarih
# ---------------------------------------------------------------------------

def history_tab(db, cm, league_id: int, league_name: str, own: int | None) -> None:
    honours = list(db.scalars(select(SeasonHonour).where(
        SeasonHonour.kind == "LEAGUE",
        (SeasonHonour.league_id == league_id) | (SeasonHonour.competition_name == league_name))
        .order_by(SeasonHonour.season.desc())))
    st.markdown("#### Şampiyonlar")
    if honours:
        frame = pd.DataFrame([{
            "Sezon": h.season, "Şampiyon": h.champion_name, "İkinci": h.runner_up_name or "—",
            "Gol kralı": f"{h.top_scorer_name} ({h.top_scorer_goals})" if h.top_scorer_name else "—",
            "Sezonun oyuncusu": h.player_of_season_name or "—"} for h in honours])
        lk.link_table("lk_comp_history", frame,
                      clubs={"Şampiyon": [h.champion_team_id for h in honours],
                             "İkinci": [h.runner_up_team_id for h in honours]},
                      player_cols={"Gol kralı": [h.top_scorer_player_id for h in honours],
                                   "Sezonun oyuncusu": [h.player_of_season_id for h in honours]})
    else:
        st.caption("Şampiyonlar listesi ilk sezon bitince dolar.")
    seasons = [int(s) for (s,) in db.execute(select(SeasonStanding.season).where(
        SeasonStanding.league_id == league_id).distinct().order_by(SeasonStanding.season.desc()))]
    if not seasons:
        return
    if st.session_state.get(SEASON_KEY) not in seasons:
        st.session_state[SEASON_KEY] = seasons[0]
    st.markdown("#### Geçmiş sezon tablosu")
    season = st.selectbox("Sezon", seasons, key=SEASON_KEY, format_func=lambda s: f"Sezon {s}")
    rows = list(db.scalars(select(SeasonStanding).where(SeasonStanding.league_id == league_id,
                                                        SeasonStanding.season == season)
                           .order_by(SeasonStanding.position)))
    frame = pd.DataFrame([{"#": r.position, "Kulüp": r.team_name, "P": r.points} for r in rows])
    mine = {i for i, r in enumerate(rows) if r.team_id == own}
    lk.link_table("lk_comp_season", _highlight(frame, mine), clubs={"Kulüp": [r.team_id for r in rows]}, hint=False)


__all__ = ["TABS", "cb_comp_league", "league_id_for", "league_title", "render_competition", "season_player_stats",
           "week_fixtures"]
