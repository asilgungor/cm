"""
Mac onu istihbarati ve gozlemci raporu testleri: match_preview (saf) + preview_views (entegrasyon).

Saf testler DB istemez. Entegrasyon testleri tek bir oturumda sentetik dunyada birkac hafta oynatir
(modul kapsamli, sonunda rollback); her test kendi SAVEPOINT'inde calisir ve geri alinir.
Builder'larin veritabanina HIC yazmadigi SQL seviyesinde (INSERT/UPDATE/DELETE yakalanarak) denetlenir.
"""

from __future__ import annotations

import dataclasses
import random
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event, or_, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import match_preview as mp  # noqa: E402
from match_preview import PlayedMatch  # noqa: E402

STAR_TEXT = re.compile(r"^[⭐💫–\s]+$")
FORBIDDEN_FIELDS = {"overall", "overall_rating", "potential", "potential_rating", "potential_low",
                    "potential_high", "current_ability", "potential_ability"}


def _m(fid, week, home, away, hs, as_, comp=mp.LEAGUE, season=1, neutral=False):
    return PlayedMatch(fid, season, week, comp, home, away, hs, as_, neutral,
                       "Lig" if comp == mp.LEAGUE else "Devler Arenası · Son 16 ilk maç")


NAMES = {1: "Alfa", 2: "Beta", 3: "Gama", 4: "Delta"}
SEASON = [
    _m(1, 1, 1, 2, 2, 0),                    # G (ic saha)
    _m(2, 2, 3, 1, 1, 1),                    # B (deplasman)
    _m(100, 3, 1, 4, 0, 1, comp=mp.CUP),     # M (hafta ici kupa: ayni haftanin lig macindan once)
    _m(5, 3, 1, 2, 3, 2),                    # G
    _m(6, 4, 2, 1, 2, 0),                    # M (deplasman)
    _m(7, 5, 1, 3, 0, 0),                    # B
    _m(8, 5, 2, 4, 5, 5),                    # Alfa'yi ilgilendirmiyor
    _m(101, 6, 1, 4, 1, 1, comp=mp.CUP, neutral=True),   # tarafsiz saha: karneye girmez
]


# ===========================================================================
# 1) SAF: form, karne, aralarindaki maclar, egilimler
# ===========================================================================

def test_form_summary_is_chronological_with_midweek_cup_first():
    form = mp.form_summary(SEASON[:-1], 1, NAMES)
    # Kronoloji: G B M(kupa) G M B -> son 5
    assert form.form == "BMGMB"
    assert (form.goals_for, form.goals_against) == (1 + 0 + 3 + 0 + 0, 1 + 1 + 2 + 2 + 0)
    assert [m.fixture_id for m in form.matches] == [2, 100, 5, 6, 7]
    assert form.matches[0].venue == mp.VENUE_AWAY and form.matches[0].opponent_name == "Gama"
    assert form.matches[1].competition_label.startswith("Devler Arenası")
    assert mp.form_summary(SEASON[:-1], 1, NAMES, n=3).form == "GMB"
    assert mp.form_summary([], 1).form == "" and mp.form_summary(SEASON, 99).points_per_match is None


def test_venue_records_exclude_neutral_ground():
    home, away = mp.venue_records(SEASON, 1)
    assert (home.played, home.won, home.drawn, home.lost, home.goals_for, home.goals_against) == (4, 2, 1, 1, 5, 3)
    assert (away.played, away.won, away.drawn, away.lost, away.goals_for, away.goals_against) == (2, 0, 1, 1, 1, 3)
    assert home.points == 7 and home.text == "2G 1B 1M · 5-3"
    assert mp.Record().text == "Maç yok"


def test_head_to_head_totals_and_last_meetings():
    history = SEASON + [_m(300, 2, 2, 1, 1, 1, season=2)]
    h2h = mp.head_to_head(history, 1, 2, NAMES)
    assert (h2h.meetings, h2h.team_a_wins, h2h.draws, h2h.team_b_wins) == (4, 2, 1, 1)
    assert (h2h.team_a_goals, h2h.team_b_goals) == (2 + 3 + 0 + 1, 0 + 2 + 2 + 1)
    assert [m.fixture_id for m in h2h.last_meetings] == [300, 6, 5, 1]          # en yeni once
    assert h2h.last_meetings[0].winner_team_id is None
    assert h2h.last_meetings[1].winner_team_id == 2 and h2h.last_meetings[1].text == "Beta 2 - 0 Alfa"
    assert "Alfa 2 galibiyet" in h2h.summary and "Beta 1 galibiyet" in h2h.summary
    assert len(mp.head_to_head(history, 1, 2, NAMES, n=2).last_meetings) == 2
    none = mp.head_to_head(history, 3, 4, NAMES)
    assert none.meetings == 0 and none.last_meetings == () and "karşılaşmadı" in none.summary


def test_team_tendencies_notes():
    t = mp.team_tendencies(SEASON, 1, yellow_cards=3, red_cards=1)
    assert t.matches == 7 and t.clean_sheets == 2 and t.failed_to_score == 3
    # Tarafsiz sahadaki final de resmi mactir: 7 mac, 7 atilan, 7 yenilen
    assert t.goals_scored_per_match == 1.0 and t.goals_conceded_per_match == 1.0
    assert any("gol yemedi" in n for n in t.notes) and any("kırmızı" in n for n in t.notes)
    prolific = mp.team_tendencies([_m(1, 1, 1, 2, 4, 3), _m(2, 2, 2, 1, 2, 3)], 1)
    assert any("Hücumda üretken" in n for n in prolific.notes)
    assert any("kırılgan" in n for n in prolific.notes)
    assert "3,5" in " ".join(prolific.notes)                                    # Turkce ondalik
    assert mp.team_tendencies([], 1).notes == ("Bu sezon henüz resmi maç oynamadı.",)


def test_players_to_watch_picks_scorer_best_rating_and_contributor():
    line = mp.PlayerSeasonLine
    lines = [
        line(1, "Golcü", "FWD", "⭐⭐⭐", appearances=4, goals=3, assists=0, average_rating=6.9),
        line(2, "Notçu", "MID", "⭐⭐⭐", appearances=4, goals=0, assists=0, average_rating=7.8),
        line(3, "Katkı", "MID", "⭐⭐", appearances=4, goals=1, assists=2, average_rating=6.5),
        line(4, "Tek maç", "DEF", "⭐⭐", appearances=1, goals=0, assists=0, average_rating=9.5),
        line(5, "Sessiz", "DEF", "⭐⭐", appearances=4, goals=0, assists=0, average_rating=6.0),
    ]
    watch = mp.players_to_watch(lines)
    assert [w.player_id for w in watch] == [1, 2, 3]
    assert watch[0].reason == "Takımın golcüsü: 3 gol"
    assert watch[1].reason == "En yüksek ortalama not: 7,8"          # 1 maclik 9.5 sayilmaz
    assert "1 gol, 2 asist" in watch[2].reason
    assert [w.player_id for w in mp.players_to_watch(lines, limit=2)] == [1, 2]
    assert mp.players_to_watch(lines, limit=0) == []

    unplayed = [line(i, f"O{i}", "MID", "⭐", prominence=p) for i, p in ((1, 50), (2, 70), (3, 60), (4, 40))]
    fallback = mp.players_to_watch(unplayed)
    assert [w.player_id for w in fallback] == [2, 3, 1]
    assert all(w.reason == "Kadronun öne çıkan ismi" for w in fallback)


# ===========================================================================
# 2) SAF: kagit ustu yorum
# ===========================================================================

def test_verdict_balanced_home_advantage_and_clear_favourite():
    even = mp.match_verdict("Alfa", "Beta", 70, 70)
    assert even.favorite is None and "dengeli" in even.text

    home_edge = mp.match_verdict("Alfa", "Beta", 70, 70, home_advantage=1.05)
    assert home_edge.favorite == "home" and not home_edge.clear
    assert home_edge.text == "Kağıt üzerinde favori: Alfa (iç saha avantajıyla)"

    away = mp.match_verdict("Alfa", "Beta", 60, 75, home_advantage=1.08)
    assert away.favorite == "away" and away.clear
    assert away.text.startswith("Kağıt üzerinde açık favori: Beta")

    strong_home = mp.match_verdict("Alfa", "Beta", 80, 70, home_advantage=1.08)
    assert strong_home.text == "Kağıt üzerinde açık favori: Alfa"

    form = mp.match_verdict("Alfa", "Beta", 70, 70, home_form="MMMMM", away_form="GGGGG")
    assert form.favorite == "away"

    unknown = mp.match_verdict("Alfa", "Beta", 0, 70)
    assert unknown.favorite is None
    for v in (even, home_edge, away, strong_home, form, unknown):
        assert not re.search(r"\d", v.text)


def test_team_stars_and_strength():
    assert mp.lineup_strength([70, 80]) == 75 and mp.lineup_strength([]) == 0.0
    assert mp.team_stars(75) == "⭐⭐⭐⭐💫" and mp.team_stars(0) == "–"


# ===========================================================================
# 3) SAF: gozlemci isabeti ve tahmin bozma
# ===========================================================================

def test_scout_accuracy_and_confidence_bands():
    values = [mp.scout_accuracy(r) for r in range(1, 21)]
    assert values == sorted(values) and len(set(values)) == 20
    assert mp.scout_accuracy(None) < values[0]
    assert mp.scout_accuracy(None) == mp.NO_SCOUT_ACCURACY and values[-1] == mp.MAX_ACCURACY
    assert mp.confidence_label(mp.scout_accuracy(None)) == "Düşük"
    assert mp.confidence_label(mp.scout_accuracy(5)) == "Düşük"
    assert mp.confidence_label(mp.scout_accuracy(10)) == "Orta"
    assert mp.confidence_label(mp.scout_accuracy(20)) == "Yüksek"
    assert mp.scout_seed(1, 2, 3, 4) == mp.scout_seed(1, 2, 3, 4) != mp.scout_seed(1, 2, 4, 4)


STARTERS = (
    [mp.LineupSlot(1, "GK", "GK")]
    + [mp.LineupSlot(i, "DEF", "DEF") for i in range(2, 6)]
    + [mp.LineupSlot(i, "MID", "MID") for i in range(6, 10)]
    + [mp.LineupSlot(i, "FWD", "FWD") for i in range(10, 12)]
)
BENCH = [mp.Candidate(20, "GK"), mp.Candidate(21, "DEF"), mp.Candidate(22, "MID"),
         mp.Candidate(23, "FWD"), mp.Candidate(24, "FWD")]


def _changed(predicted) -> int:
    return sum(1 for a, b in zip(STARTERS, predicted, strict=True) if a.player_id != b.player_id)


def test_scramble_lineup_rules():
    for seed in range(300):
        perfect = mp.scramble_lineup(random.Random(seed), STARTERS, BENCH, 1.0)
        assert perfect == STARTERS
        blind = mp.scramble_lineup(random.Random(seed), STARTERS, BENCH, 0.0)
        assert [s.role for s in blind] == [s.role for s in STARTERS]           # slotlar korunur
        ids = [s.player_id for s in blind]
        assert len(set(ids)) == 11
        gk = blind[0]
        assert gk.role == "GK" and gk.position == "GK"                          # kaleci yalnizca kaleciyle
        assert all(s.position != "GK" for s in blind[1:])
        assert blind == mp.scramble_lineup(random.Random(seed), STARTERS, BENCH, 0.0)   # deterministik


def test_scramble_lineup_errors_shrink_with_accuracy():
    def average(accuracy):
        return sum(_changed(mp.scramble_lineup(random.Random(s), STARTERS, BENCH, accuracy))
                   for s in range(400)) / 400
    blind, mid, sharp = average(mp.scout_accuracy(None)), average(mp.scout_accuracy(10)), average(mp.scout_accuracy(20))
    assert blind > mid > sharp
    assert sharp < 0.8 and blind > 2.5


def test_predict_formation():
    options = ("4-4-2", "4-3-3", "3-5-2")
    assert all(mp.predict_formation(random.Random(s), "4-3-3", options, 1.0) == "4-3-3" for s in range(200))
    guesses = [mp.predict_formation(random.Random(s), "4-3-3", options, 0.0) for s in range(1000)]
    assert set(guesses) <= set(options)
    miss = sum(g != "4-3-3" for g in guesses) / len(guesses)
    assert 0.35 < miss < 0.65                         # (1 - 0) x FORMATION_MISS_SCALE
    assert mp.predict_formation(random.Random(0), "4-4-2", ("4-4-2",), 0.0) == "4-4-2"


# ===========================================================================
# 4) ENTEGRASYON
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")

PLAY_SEED = 7
WEEKS_PLAYED = 3


@pytest.fixture(scope="module")
def world():
    """Sentetik dunyada WEEKS_PLAYED hafta (lig + kupa) oynanmis tek oturum; modul sonunda rollback."""
    from career_manager import CareerManager
    from database import SessionLocal

    session = SessionLocal()
    try:
        cm = CareerManager(session, seed=PLAY_SEED)
        if cm.season_finished or cm.current_week != 1:
            pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
        for _ in range(WEEKS_PLAYED):
            cm.play_week()
        session.flush()
        yield session, cm
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def db(world):
    session, _cm = world
    nested = session.begin_nested()
    try:
        yield session
    finally:
        if nested.is_active:
            nested.rollback()
        session.expire_all()


@pytest.fixture
def cm(world):
    return world[1]


@contextmanager
def sql_log(db):
    """Blok icinde calisan SQL ifadeleri (yazma denetimi icin)."""
    statements: list[str] = []
    engine = db.get_bind()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def _writes(statements: list[str]) -> list[str]:
    return [s for s in statements
            if s.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"} or "FOR UPDATE" in s.upper()]


def _league_fixtures(cm):
    fixtures = [f for f in cm.fixtures_for_week(cm.current_week) if not f.is_played]
    assert fixtures, "oynanmamış lig maçı yok"
    return fixtures


def _expected_form(db, team_id: int, season: int):
    from models import Competition, Fixture, FixtureStatus

    fixtures = list(db.scalars(select(Fixture).where(
        Fixture.season == season, Fixture.status == FixtureStatus.PLAYED,
        or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id))))
    fixtures.sort(key=lambda f: (f.week, 0 if f.competition is Competition.CUP else 1, f.id))
    letters, gf_total, ga_total = "", 0, 0
    for f in fixtures[-5:]:
        gf, ga = (f.home_score, f.away_score) if f.home_team_id == team_id else (f.away_score, f.home_score)
        letters += "G" if gf > ga else "B" if gf == ga else "M"
        gf_total, ga_total = gf_total + gf, ga_total + ga
    return letters, gf_total, ga_total, len(fixtures)


@integration
@pytest.mark.integration
def test_next_preview_fixture_is_earliest_unplayed(db, cm):
    import preview_views as pv
    from models import Competition, Fixture, FixtureStatus

    for team in cm.teams()[:6]:
        pending = list(db.scalars(select(Fixture).where(
            Fixture.season == cm.season, Fixture.status == FixtureStatus.UNPLAYED,
            or_(Fixture.home_team_id == team.id, Fixture.away_team_id == team.id))))
        pending.sort(key=lambda f: (f.week, 0 if f.competition is Competition.CUP else 1, f.id))
        fx = pv.next_preview_fixture(db, team.id)
        assert fx is not None and fx.id == pending[0].id and fx.involves(team.id)


@integration
@pytest.mark.integration
def test_preview_form_table_and_records_match_fixtures(db, cm):
    import preview_views as pv

    for fx in _league_fixtures(cm)[:4]:
        preview = pv.build_match_preview(db, fx.id, fx.home_team_id)
        assert preview.competition == mp.LEAGUE and preview.competition_label == "Lig"
        assert preview.title == f"Lig · {fx.week}. hafta · {fx.home_team.name} - {fx.away_team.name}"
        assert preview.viewer is preview.home and preview.opponent is preview.away
        for side, team in ((preview.home, fx.home_team), (preview.away, fx.away_team)):
            form, gf, ga, played = _expected_form(db, team.id, cm.season)
            assert side.form == form and len(form) == min(5, played) > 0
            assert (side.form_goals_for, side.form_goals_against) == (gf, ga)
            assert side.league_position == cm.position_of(team) and side.points == team.points
            assert side.league_size == len(cm.standings(team.league_id))
            neutral = sum(1 for m in side.recent_matches if m.venue == mp.VENUE_NEUTRAL)
            assert side.home_record.played + side.away_record.played + neutral <= played
            assert side.tendencies.matches == played
            assert len(side.players_to_watch) <= 3
            assert {w.player_id for w in side.players_to_watch} <= {p.id for p in team.players}
        assert preview.favorite_team_id in (None, fx.home_team_id, fx.away_team_id)
        if preview.favorite_team_id is not None:
            favourite = preview.home if preview.favorite_team_id == fx.home_team_id else preview.away
            assert favourite.name in preview.verdict


@integration
@pytest.mark.integration
def test_head_to_head_counts_match_played_meetings(db, cm):
    import preview_views as pv
    from models import Fixture, FixtureStatus

    met = 0
    for fx in _league_fixtures(cm):
        a, b = fx.home_team_id, fx.away_team_id
        meetings = list(db.scalars(select(Fixture).where(
            Fixture.status == FixtureStatus.PLAYED,
            or_((Fixture.home_team_id == a) & (Fixture.away_team_id == b),
                (Fixture.home_team_id == b) & (Fixture.away_team_id == a)))))
        wins_a = sum(1 for f in meetings if (f.home_score - f.away_score) * (1 if f.home_team_id == a else -1) > 0)
        draws = sum(1 for f in meetings if f.home_score == f.away_score)
        h2h = pv.build_match_preview(db, fx.id, a).head_to_head
        assert (h2h.team_a_id, h2h.team_b_id) == (a, b)
        assert (h2h.meetings, h2h.team_a_wins, h2h.draws, h2h.team_b_wins) == (
            len(meetings), wins_a, draws, len(meetings) - wins_a - draws)
        order = [(m.season, m.week) for m in h2h.last_meetings]
        assert order == sorted(order, reverse=True) and len(order) == min(5, len(meetings))
        met += bool(meetings)
    assert met > 0, "çift devreli ligde 4. haftada rövanşlar başlamalı"


@integration
@pytest.mark.integration
def test_injured_and_suspended_players_are_listed(db, cm):
    import preview_views as pv

    fx = _league_fixtures(cm)[0]
    week = cm.current_week
    opponent = fx.away_team
    healthy = [p for p in opponent.players if p.is_available(week) and p.cup_suspended_matches == 0]
    injured, banned, cup_banned = healthy[0], healthy[1], healthy[2]
    injured.injured_until_week = week + 2
    banned.suspended_matches = 2
    cup_banned.cup_suspended_matches = 1
    db.flush()

    preview = pv.build_match_preview(db, fx.id, fx.home_team_id)
    inj = {a.player_id: a for a in preview.away.injured}
    sus = {a.player_id: a for a in preview.away.suspended}
    assert inj[injured.id].return_week == week + 2 and inj[injured.id].reason == "Sakat"
    assert inj[injured.id].detail == f"{week + 2}. haftada dönüyor"
    assert sus[banned.id].matches_left == 2 and sus[banned.id].reason == "Cezalı"
    assert cup_banned.id not in sus                              # lig macinda kupa cezasi gecmez
    expected_inj = {p.id for p in opponent.players if p.is_injured(week)}
    expected_sus = {p.id for p in opponent.players if p.suspended_matches > 0}
    assert set(inj) == expected_inj and set(sus) == expected_sus

    report = pv.scout_opposition(db, fx.id, fx.home_team_id, rng_seed=3)
    assert {injured.id, banned.id} <= {a.player_id for a in report.absentees}
    assert not {injured.id, banned.id} & {s.player_id for s in report.predicted_xi}


@integration
@pytest.mark.integration
def test_cup_preview_uses_cup_bans_only(db, cm):
    import preview_views as pv
    from models import Competition, Fixture, FixtureStatus

    fx = db.scalar(select(Fixture).where(
        Fixture.season == cm.season, Fixture.competition == Competition.CUP,
        Fixture.status == FixtureStatus.UNPLAYED).order_by(Fixture.week, Fixture.id).limit(1))
    if fx is None:
        pytest.skip("Oynanmamış kupa maçı yok")
    team = fx.home_team
    cup_banned, league_banned = [p for p in team.players if p.is_available(fx.week, Competition.CUP)
                                 and p.suspended_matches == 0][:2]
    cup_banned.cup_suspended_matches = 1
    league_banned.suspended_matches = 1
    db.flush()
    preview = pv.build_match_preview(db, fx.id, fx.away_team_id)
    assert preview.competition == mp.CUP and preview.competition_label.startswith("Devler Arenası")
    sus = {a.player_id: a for a in preview.home.suspended}
    assert cup_banned.id in sus and "(kupa)" in sus[cup_banned.id].detail
    assert league_banned.id not in sus


@integration
@pytest.mark.integration
def test_builders_never_write(db, cm):
    import preview_views as pv

    fx = _league_fixtures(cm)[1]
    db.flush()
    with sql_log(db) as statements:
        pv.next_preview_fixture(db, fx.home_team_id)
        pv.build_match_preview(db, fx.id, fx.home_team_id)
        pv.build_match_preview(db, fx.id, None)
        pv.scout_opposition(db, fx.id, fx.away_team_id, rng_seed=11)
        pv.build_squad_plan(db, fx.home_team_id)
    assert any(s.lstrip().upper().startswith("SELECT") for s in statements)      # dinleyici calisti
    assert _writes(statements) == []
    assert not db.new and not db.dirty and not db.deleted


def _set_scouts(db, cm, judging: int | None) -> None:
    """Tum kuluplerin gozlemcileri: judging None -> kulupten ayrilir (gozlemcisiz)."""
    from models import StaffRole

    for team in cm.teams():
        for member in list(team.staff_by_role(StaffRole.SCOUT)):
            if judging is None:
                member.team = None
            else:
                member.judging_ability = judging
    db.flush()


def _average_overlap(db, cm, seeds=range(5)) -> tuple[float, set[str]]:
    import preview_views as pv
    from match_engine import prepare_fixture

    week = cm.current_week
    total = count = 0
    confidences: set[str] = set()
    for fx in _league_fixtures(cm):
        _, engine = prepare_fixture(db, fx.id, current_week=week)          # motorun gercek ilk 11'i
        for viewer_id, opponent in ((fx.home_team_id, engine.away), (fx.away_team_id, engine.home)):
            actual = {p.id for p in opponent.on_pitch}
            assert len(actual) == 11
            for seed in seeds:
                report = pv.scout_opposition(db, fx.id, viewer_id, rng_seed=seed)
                predicted = {p.player_id for p in report.predicted_xi}
                assert len(predicted) == 11
                total += len(actual & predicted)
                count += 1
                confidences.add(report.confidence)
    return total / count, confidences


@integration
@pytest.mark.integration
def test_strong_scout_predicts_engine_lineup_better_than_no_scout(db, cm):
    import preview_views as pv

    first = _league_fixtures(cm)[0]
    strong_sp = db.begin_nested()
    _set_scouts(db, cm, 20)
    strong, strong_conf = _average_overlap(db, cm)
    strong_sp.rollback()
    db.expire_all()

    blind_sp = db.begin_nested()
    _set_scouts(db, cm, None)
    blind, blind_conf = _average_overlap(db, cm)
    report = pv.scout_opposition(db, first.id, first.home_team_id)
    blind_sp.rollback()
    db.expire_all()

    assert strong_conf == {"Yüksek"} and blind_conf == {"Düşük"}
    assert strong >= 10.0
    assert strong > blind + 1.0, (strong, blind)
    assert report.scout_name is None and any("gözlemci yok" in n for n in report.notes)


@integration
@pytest.mark.integration
def test_scout_report_is_deterministic_and_valid(db, cm):
    import preview_views as pv

    fx = _league_fixtures(cm)[2]
    viewer = fx.home_team_id
    first = pv.scout_opposition(db, fx.id, viewer, rng_seed=42)
    assert first == pv.scout_opposition(db, fx.id, viewer, rng_seed=42)
    assert first.week == cm.current_week and first.opponent_team_id == fx.away_team_id
    assert not first.opponent_is_home and first.disclaimer == mp.SCOUT_DISCLAIMER

    blind_sp = db.begin_nested()
    _set_scouts(db, cm, None)
    reports = [pv.scout_opposition(db, fx.id, viewer, rng_seed=s) for s in range(12)]
    blind_sp.rollback()
    db.expire_all()
    assert len({(r.predicted_formation, tuple(p.player_id for p in r.predicted_xi)) for r in reports}) > 1

    squad = {p.id: p for p in fx.away_team.players}
    for r in reports + [first]:
        ids = [p.player_id for p in r.predicted_xi]
        assert len(set(ids)) == 11 and set(ids) <= set(squad)
        assert all(squad[i].is_available(cm.current_week) for i in ids)
        assert r.predicted_formation in ("4-4-2", "4-3-3", "3-5-2")
        roles = [p.role for p in r.predicted_xi]
        d, m, f = (int(x) for x in r.predicted_formation.split("-"))
        assert roles == ["GK"] + ["DEF"] * d + ["MID"] * m + ["FWD"] * f
    with pytest.raises(ValueError):
        pv.scout_opposition(db, fx.id, next(t.id for t in cm.teams() if not fx.involves(t.id)))


def _texts(obj, name: str = "item") -> list[tuple[str, str]]:
    """Dataclass agacindaki tum (alan, metin) ciftleri; demetteki metinler ust alanin adini alir."""
    out: list[tuple[str, str]] = []
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            if isinstance(value, str):
                out.append((f.name, value))
            else:
                out += _texts(value, f.name)
    elif isinstance(obj, (tuple, list)):
        for item in obj:
            if isinstance(item, str):
                out.append((name, item))
            else:
                out += _texts(item, name)
    return out


def _field_names(obj) -> set[str]:
    names: set[str] = set()
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            names.add(f.name)
            names |= _field_names(getattr(obj, f.name))
    elif isinstance(obj, (tuple, list)):
        for item in obj:
            names |= _field_names(item)
    return names


PROSE_FIELDS = {"verdict", "disclaimer", "confidence", "reason", "detail", "notes", "summary",
                "recommendations", "flags"}


@integration
@pytest.mark.integration
def test_rendered_text_hides_numeric_ratings(db, cm):
    import preview_views as pv

    fx = _league_fixtures(cm)[0]
    preview = pv.build_match_preview(db, fx.id, fx.home_team_id)
    report = pv.scout_opposition(db, fx.id, fx.home_team_id, rng_seed=5)
    plan = pv.build_squad_plan(db, fx.home_team_id)
    ratings = {str(p.overall_rating) for t in (fx.home_team, fx.away_team) for p in t.players}
    ratings |= {str(p.potential_rating) for t in (fx.home_team, fx.away_team) for p in t.players}

    for obj in (preview, report, plan):
        assert not _field_names(obj) & FORBIDDEN_FIELDS
        texts = _texts(obj)
        star_fields = [(k, v) for k, v in texts if k in {"stars", "team_stars", "potential_stars"}]
        assert star_fields and all(STAR_TEXT.match(v) for _k, v in star_fields), star_fields
        prose = [(k, v) for k, v in texts if k in PROSE_FIELDS]
        assert prose
        for key, value in prose:
            # Guc/potansiyel (iki haneli) sizmaz: gol, kart, mac notu ve sayilar tek hanelidir. Sakatlik donus
            # haftasi ("11. haftada dönüyor") iki haneli olabilir ve guc degildir (13. Asama: AI talimatlariyla
            # degisen mac sonuclarinda uzun sakatlik cikti); kontrol disi birakilir
            prose_text = re.sub(r"\d+\. haftada", "N. haftada", value)
            prose_text = re.sub(r"\bilk 11\b", "ilk on bir", prose_text)             # dizilis terimi, guc degil
            # Takimin kart sayisi ("Sert oynuyor: 10 sarı, 1 kırmızı") gorunur bir istatistiktir, guc degil
            # (14B: Agresiflik motorda okununca sert takimlar birkac haftada iki haneli sariya ulasabiliyor)
            prose_text = re.sub(r"\b\d+ sarı, \d+ kırmızı", "N sarı, N kırmızı", prose_text)
            assert not re.search(r"\b\d{2,}\b", prose_text), (key, value)
            assert not set(re.findall(r"\d+", prose_text)) & ratings, (key, value)
    assert not re.search(r"\d", preview.verdict + report.disclaimer + report.confidence)
