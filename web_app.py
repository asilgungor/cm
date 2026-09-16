"""
web_app.py
==========
Canli mac ekrani (6. Asama) -- Streamlit.

    streamlit run web_app.py

Mimari:
    match_engine  -> maci ANINDA oynatir (MatchResult)
    match_feed    -> olaylari kumulatif skor/istatistikli karelere cevirir (saf)
    web_view      -> kareleri HTML/CSS'e cevirir (saf, isimler escape edilir)
    web_app       -> kareleri zamanlayip ekrana basar (bu dosya)

Iki mod:
    Hazirlik maci : iki takim sec, izle. Veritabanina HICBIR SEY yazilmaz.
    Kariyer       : tum liglerde haftayi oynatir (kalici), senin macini canli izletir.
"""

from __future__ import annotations

import time

import streamlit as st
from sqlalchemy import select

from career_manager import CareerManager
from database import session_scope, wait_for_db
from match_engine import simulate_friendly
from match_feed import build_timeline, summarize
from models import Team
from web_view import CSS, banner_html, feed_html, scoreboard_html, stats_html, summary_lines

SPEEDS = {"Yavaş": 1.2, "Normal": 0.55, "Hızlı": 0.2, "Anında": 0.0}


def load_teams() -> list[str]:
    with session_scope() as db:
        return [t.name for t in db.scalars(select(Team).order_by(Team.league_id, Team.name))]


def run_friendly(home: str, away: str, seed: int | None):
    with session_scope() as db:
        return simulate_friendly(db, home, away, seed=seed)


def run_career_week(seed: int | None):
    """Haftayi oynatir; (rapor ozeti, kullanicinin mac sonucu) doner."""
    with session_scope() as db:
        cm = CareerManager(db, seed=seed)
        if cm.user_team is None:
            return None, "Önce kenar çubuğundan takımını seç."
        if cm.season_finished:
            return None, "Sezon bitti. CLI'dan yeni sezonu başlat: python main.py --new-season"
        report = cm.play_week()
        notes = [f"Sezon {report.season}, {report.week}. hafta oynandı ({len(report.results)} maç)."]
        if report.manager_reputation:
            before, after = report.manager_reputation
            notes.append(f"Menajer tanınırlığı: {before:.2f} → {after:.2f}")
        notes += [f"Transfer: {n.describe()}" for n in report.transfers]
        notes += [f"Sakatlık: {n.player_name} ({n.team_name}) — {n.detail}" for n in report.injuries]
        return report.user_result, notes


def play_live(result, delay: float) -> None:
    """Kareleri sirayla basar: tabela, uyari, olay akisi, istatistik."""
    frames = build_timeline(result)
    home, away = result.home.name, result.away.name

    board = st.empty()
    banner = st.empty()
    progress = st.progress(0.0, text="Başlama düdüğü bekleniyor")
    feed_col, stats_col = st.columns([3, 2], gap="large")
    with feed_col:
        st.markdown("#### Maç akışı")
        feed = st.empty()
    with stats_col:
        st.markdown("#### İstatistikler")
        stats = st.empty()

    total = max(1, result.total_minutes)
    flash, flash_left = None, 0
    for i, frame in enumerate(frames):
        if frame.event.highlight in {"goal", "red"}:
            flash, flash_left = frame.event.highlight, 1
        board.markdown(scoreboard_html(home, away, frame, flash if flash_left else None), unsafe_allow_html=True)
        flash_left = max(0, flash_left - 1)
        if frame.event.highlight in {"goal", "red"}:
            banner.markdown(banner_html(frame), unsafe_allow_html=True)
        elif frame.event.highlight not in {"injury"}:
            banner.empty()
        progress.progress(min(1.0, frame.elapsed / total), text=f"{frame.display_minute} · {frame.phase}")
        feed.markdown(feed_html(frames[: i + 1]), unsafe_allow_html=True)
        stats.markdown(stats_html(home, away, frame.home, frame.away), unsafe_allow_html=True)
        if delay:
            time.sleep(delay * frame.pacing)

    summary = summarize(result, frames)
    stats.markdown(
        stats_html(home, away, summary.home_stats, summary.away_stats,
                   (summary.possession_home, summary.possession_away)),
        unsafe_allow_html=True,
    )
    st.divider()
    st.markdown("### Maç sonu")
    for line in summary_lines(summary):
        st.markdown(line)


def main() -> None:
    st.set_page_config(page_title="CM Canlı Maç", page_icon="⚽", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    st.title("⚽ CM — Canlı Maç")

    if not wait_for_db(retries=2, delay=0.5, verbose=False):
        st.error("Veritabanına bağlanılamadı. `docker compose up -d` çalışıyor mu?")
        st.stop()

    teams = load_teams()
    if len(teams) < 2:
        st.warning("Veritabanında takım yok. Önce `python seed.py` çalıştır.")
        st.stop()

    with st.sidebar:
        st.header("Ayarlar")
        mode = st.radio("Mod", ["Hazırlık maçı", "Kariyer: haftayı oyna"],
                        help="Hazırlık maçı veritabanına yazmaz. Kariyer modu haftayı kalıcı olarak oynatır.")
        speed = st.select_slider("Hız", options=list(SPEEDS), value="Normal")
        seed_text = st.text_input("Tohum (boş = rastgele)", value="")
        seed = int(seed_text) if seed_text.strip().lstrip("-").isdigit() else None

        if mode == "Hazırlık maçı":
            home = st.selectbox("Ev sahibi", teams, index=0)
            away = st.selectbox("Deplasman", teams, index=1 if len(teams) > 1 else 0)
        else:
            with session_scope() as db:
                cm = CareerManager(db)
                current = cm.user_team.name if cm.user_team else None
                week, season = cm.current_week, cm.season
                rep = cm.manager_reputation
            chosen = st.selectbox("Takımın", teams, index=teams.index(current) if current in teams else 0)
            if chosen != current and st.button("Takımı ayarla"):
                with session_scope() as db:
                    cm = CareerManager(db)
                    cm.set_user_team(cm.find_team(chosen))
                st.rerun()
            st.caption(f"Sezon {season} · Hafta {week} · Menajer tanınırlığı {rep:.1f}/20")

        start = st.button("▶ Maçı başlat", type="primary", use_container_width=True)

    if not start:
        st.markdown(scoreboard_html(teams[0] if mode == "Hazırlık maçı" else "Ev", teams[1] if mode == "Hazırlık maçı" else "Deplasman", None),
                    unsafe_allow_html=True)
        st.info("Kenar çubuğundan ayarları seç ve **Maçı başlat**'a bas.")
        return

    if mode == "Hazırlık maçı":
        if home == away:
            st.error("Bir takım kendisiyle oynayamaz.")
            return
        result = run_friendly(home, away, seed)
        st.caption("Hazırlık maçı — veritabanına kaydedilmez.")
        play_live(result, SPEEDS[speed])
        return

    result, notes = run_career_week(seed)
    if result is None and isinstance(notes, str):
        st.warning(notes)
        return
    for note in notes:
        st.caption(note)
    if result is None:
        st.info("Bu hafta takımının maçı yok.")
        return
    play_live(result, SPEEDS[speed])


if __name__ == "__main__":
    main()
