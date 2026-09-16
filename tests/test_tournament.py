"""
Devler Arenasi (Champions Cup) entegrasyon testleri (8. Asama).

tournament_manager + career_manager + match_engine (uzatma/penalti) birlikte:
    * katilim, torbalar, takvim
    * interaktif kura: her top kaydedilir, kura bitince eslesme ve fikstur bütünlüğü
    * tam turnuva: agac tutarliligi, toplam skor, uzatma/penalti kararlari, sampiyon
    * penalti serisi olay kaydi (kim atti / kim kacirdi)
    * kupa cezalari ligden ayri
    * kariyer modunda senkron takvim (hafta ici kupa + hafta sonu lig), sezon sonu
    * grup formati

Her test kendi transaction'ini rollback eder; test veritabani kirlenmez.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from career_manager import CareerManager  # noqa: E402
from cup_draw import CupFormat, Stage  # noqa: E402
from models import (  # noqa: E402
    Competition,
    Fixture,
    FixtureStatus,
    GameMode,
    Player,
    PlayerMatchStat,
    TournamentStatus,
)
from tournament_manager import CUP_NAME, TournamentError  # noqa: E402


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, seed=1, mode: GameMode | None = None) -> CareerManager:
    cm = CareerManager(db, seed=seed)
    assert cm.current_week == 1 and cm.season == 1, "test dünyası temiz değil"
    if mode is not None:
        cm.set_game_mode(mode)
    return cm


def _play_until_finished(cm: CareerManager, limit: int = 60) -> list:
    reports = []
    for _ in range(limit):
        if cm.season_finished:
            break
        reports.append(cm.play_week())
    assert cm.season_finished, "sezon/turnuva bitmedi"
    return reports


def _league_of(db, team_id: int) -> int:
    from models import Team
    return db.get(Team, team_id).league_id


# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------

def test_ensure_builds_16_team_tournament_with_pots_and_calendar(db):
    cm = _manager(db)
    tm = cm.tournaments
    t = tm.ensure()
    assert t is not None and t.name == CUP_NAME and t.size == 16
    assert t.status is TournamentStatus.DRAW and t.format == CupFormat.KNOCKOUT.value
    assert tm.ensure() is t                                   # idempotent

    entries = sorted(t.entries, key=lambda e: e.seed_rank)
    assert len(entries) == 16 and len({e.team_id for e in entries}) == 16
    assert Counter(e.pot for e in entries) == {0: 8, 1: 8}
    assert min(e.coefficient for e in entries if e.pot == 0) >= max(e.coefficient for e in entries if e.pot == 1)

    # Sezon 1: her ligin en itibarli takimi mutlaka katilir
    for league in cm.leagues():
        best = max(league.teams, key=lambda team: (team.reputation, team.name))
        assert tm.is_participant(t, best.id), best.name

    calendar = tm.calendar(t)
    assert [(md.stage, md.leg) for md in calendar] == [
        (Stage.R16, 1), (Stage.R16, 2), (Stage.QF, 1), (Stage.QF, 2), (Stage.SF, 1), (Stage.SF, 2), (Stage.FINAL, 1)
    ]
    weeks = [md.week for md in calendar]
    assert weeks == sorted(set(weeks)) and weeks[0] == 1
    assert cm.total_weeks() == max(cm.league_weeks(), weeks[-1])


def test_game_mode_can_only_change_at_season_start(db):
    cm = _manager(db)
    assert not cm.mode_chosen and cm.game_mode is GameMode.CAREER
    cm.set_game_mode(GameMode.TOURNAMENT)
    t = cm.tournaments.current()
    assert [md.week for md in cm.tournaments.calendar(t)] == list(range(1, 8))   # her hafta kupa
    cm.set_game_mode(GameMode.CAREER)
    cm.play_week()
    with pytest.raises(ValueError):
        cm.set_game_mode(GameMode.TOURNAMENT)
    with pytest.raises(ValueError):
        cm.reset_game_mode()


def test_tournament_mode_clears_non_participant_user_team(db):
    cm = _manager(db)
    t = cm.tournaments.ensure()
    outsider = next(team for team in cm.teams() if not cm.tournaments.is_participant(t, team.id))
    cm.set_user_team(outsider)
    cm.set_game_mode(GameMode.TOURNAMENT)
    assert cm.state.user_team_id is None


def test_format_change_only_before_first_ball(db):
    cm = _manager(db)
    tm = cm.tournaments
    tm.ensure()
    t = tm.set_format(CupFormat.GROUPS)
    assert Counter(e.pot for e in t.entries) == {0: 4, 1: 4, 2: 4, 3: 4}
    assert tm.calendar(t)[0].stage is Stage.GROUP and len(tm.calendar(t)) == 11
    tm.draw_next()
    with pytest.raises(TournamentError):
        tm.set_format(CupFormat.KNOCKOUT)


# ---------------------------------------------------------------------------
# Kura butunlugu
# ---------------------------------------------------------------------------

def test_interactive_draw_persists_every_ball_and_builds_valid_bracket(db):
    cm = _manager(db, seed=4)
    tm = cm.tournaments
    t = tm.ensure()
    seeded = {e.team_id for e in t.entries if e.pot == 0}

    for number in range(1, 17):
        step = tm.draw_next()
        assert step.number == number
        db.flush()
        db.expire(t)                                            # durum DB'den tekrar okunur
        assert len(tm.draw_session(t).steps) == number
        if number < 16:
            assert t.status is TournamentStatus.DRAW and not tm.fixtures(t)
    assert t.status is TournamentStatus.RUNNING
    with pytest.raises(TournamentError):
        tm.draw_next()

    ties = tm.ties(t, Stage.R16)
    assert [tie.slot for tie in ties] == list(range(8))
    teams = [tie.first_team_id for tie in ties] + [tie.second_team_id for tie in ties]
    assert sorted(teams) == sorted(e.team_id for e in t.entries)              # herkes tam bir kez
    for tie in ties:
        assert tie.second_team_id in seeded and tie.first_team_id not in seeded   # seri basi rovansta evde
        assert _league_of(db, tie.first_team_id) != _league_of(db, tie.second_team_id)
        legs = sorted(tie.fixtures, key=lambda f: f.leg)
        assert [f.leg for f in legs] == [1, 2]
        assert (legs[0].home_team_id, legs[0].away_team_id) == (tie.first_team_id, tie.second_team_id)
        assert (legs[1].home_team_id, legs[1].away_team_id) == (tie.second_team_id, tie.first_team_id)
        assert all(f.competition is Competition.CUP and f.league_id is None for f in legs)
        assert legs[0].week < legs[1].week


def test_play_week_auto_completes_pending_draw(db):
    cm = _manager(db, seed=6, mode=GameMode.TOURNAMENT)
    report = cm.play_week()
    assert any("otomatik" in note for note in report.cup_notes)
    assert len(report.cup_results) == 8 and not report.results        # turnuva modunda lig yok
    assert all(fx.stage == Stage.R16.value and fx.leg == 1 for fx, _ in report.cup_results)


# ---------------------------------------------------------------------------
# Tam turnuva
# ---------------------------------------------------------------------------

def _assert_tie_decisions(tm, t):
    for tie in tm.ties(t):
        legs = sorted(tie.fixtures, key=lambda f: f.leg)
        assert all(f.is_played for f in legs) and tie.decided
        goals_first = sum(f.home_score if f.home_team_id == tie.first_team_id else f.away_score for f in legs)
        goals_second = sum(f.home_score if f.home_team_id == tie.second_team_id else f.away_score for f in legs)
        assert (tie.aggregate_first, tie.aggregate_second) == (goals_first, goals_second)
        deciding = legs[-1]
        assert not any(f.extra_time for f in legs[:-1])               # ilk macta uzatma olmaz
        assert all(f.home_penalties is None for f in legs[:-1])
        if tie.decided_by == "penalties":
            assert goals_first == goals_second and deciding.extra_time
            assert tie.penalties_first != tie.penalties_second
            winner_first = tie.penalties_first > tie.penalties_second
        else:
            assert goals_first != goals_second and deciding.home_penalties is None
            assert deciding.extra_time == (tie.decided_by == "extra_time")
            winner_first = goals_first > goals_second
        assert tie.winner_team_id == (tie.first_team_id if winner_first else tie.second_team_id)
        if deciding.extra_time:
            # uzatmaya ancak normal sure sonunda toplam esitken gidilir
            first_leg_goals = {legs[0].home_team_id: legs[0].home_score, legs[0].away_team_id: legs[0].away_score} \
                if len(legs) == 2 else {tie.first_team_id: 0, tie.second_team_id: 0}
            events = deciding.key_events
            start = next(e for e in events if e["type"] == "EXTRA_TIME_START")
            home_total = start["home_score"] + first_leg_goals[deciding.home_team_id]
            away_total = start["away_score"] + first_leg_goals[deciding.away_team_id]
            assert home_total == away_total


def test_full_knockout_tournament_bracket_integrity(db):
    cm = _manager(db, seed=12, mode=GameMode.TOURNAMENT)
    tm = cm.tournaments
    _play_until_finished(cm)
    t = tm.current()
    assert t.status is TournamentStatus.FINISHED and t.champion_team_id is not None

    expected = {Stage.R16: 8, Stage.QF: 4, Stage.SF: 2, Stage.FINAL: 1}
    for stage, count in expected.items():
        assert len(tm.ties(t, stage)) == count
    stages = list(expected)
    for current, following in zip(stages, stages[1:], strict=False):
        winners = [tie.winner_team_id for tie in tm.ties(t, current)]
        for j, tie in enumerate(tm.ties(t, following)):
            assert {tie.first_team_id, tie.second_team_id} == {winners[2 * j], winners[2 * j + 1]}
    final = tm.ties(t, Stage.FINAL)[0]
    assert len(final.fixtures) == 1 and final.fixtures[0].neutral_venue
    assert final.winner_team_id == t.champion_team_id
    _assert_tie_decisions(tm, t)

    eliminated = [e for e in t.entries if e.eliminated_stage]
    assert len(eliminated) == 15
    assert Counter(e.eliminated_stage for e in eliminated) == {"R16": 8, "QF": 4, "SF": 2, "FINAL": 1}
    assert tm.entries(t)[t.champion_team_id].eliminated_stage is None

    # Turnuva modunda lig oynanmaz; kupa istatistikleri sadece kupa fiksturlerinde
    league_played = db.query(Fixture).filter(Fixture.competition == Competition.LEAGUE,
                                             Fixture.status == FixtureStatus.PLAYED).count()
    assert league_played == 0
    goals = sum(f.home_score + f.away_score for f in tm.fixtures(t))
    stat_goals = sum(r.goals for r in db.query(PlayerMatchStat).join(Fixture).filter(Fixture.tournament_id == t.id))
    assert goals == stat_goals
    top = tm.top_players(t, by="goals")
    assert top and [r.goals for r in top] == sorted((r.goals for r in top), reverse=True)


def test_penalty_shootouts_are_logged_kick_by_kick(db):
    """Penalti serisine giden bir eslesme bulunana kadar farkli tohumlarla Son 16 oynanir."""
    for seed in range(1, 25):
        cm = CareerManager(db, seed=seed)
        cm.set_game_mode(GameMode.TOURNAMENT)
        cm.play_week()
        cm.play_week()                                            # Son 16 rovanslari
        t = cm.tournaments.current()
        shootouts = [f for f in cm.tournaments.fixtures(t, stage=Stage.R16) if f.home_penalties is not None]
        if shootouts:
            break
        db.rollback()
    else:
        pytest.fail("24 tohumda penaltı serisi çıkmadı")

    for fx in shootouts:
        kicks = [e for e in fx.key_events if e["type"] == "PENALTY_SHOOTOUT"]
        assert any(e["type"] == "SHOOTOUT_START" for e in fx.key_events)
        assert fx.extra_time
        assert kicks and all(k["detail"] in {"scored", "saved", "missed"} and k["player"] for k in kicks)
        scored = Counter(k["team_id"] for k in kicks if k["detail"] == "scored")
        assert (scored[fx.home_team_id], scored[fx.away_team_id]) == (fx.home_penalties, fx.away_penalties)
        last = kicks[-1]
        assert (last["home_penalties"], last["away_penalties"]) == (fx.home_penalties, fx.away_penalties)
        for kick in kicks:                                        # atan oyuncu o takimin oyuncusu
            assert db.get(Player, kick["player_id"]).team_id == kick["team_id"]
        counts = Counter(k["team_id"] for k in kicks)
        assert abs(counts[fx.home_team_id] - counts[fx.away_team_id]) <= 1
        tie = fx.tie
        assert tie.decided_by == "penalties"
        winner_pens = max(tie.penalties_first, tie.penalties_second)
        assert (tie.penalties_first if tie.winner_team_id == tie.first_team_id else tie.penalties_second) == winner_pens


# ---------------------------------------------------------------------------
# Cezalar ve senkron takvim
# ---------------------------------------------------------------------------

def test_cup_ban_is_separate_from_league_ban(db):
    cm = _manager(db, seed=9, mode=GameMode.CAREER)
    tm = cm.tournaments
    t = tm.ensure()
    tm.draw_all()
    fx = tm.fixtures(t, week=1)[0]
    team = fx.home_team
    star = max(team.players, key=lambda p: p.overall_rating)
    star.cup_suspended_matches = 1
    db.flush()
    assert not star.is_available(1, Competition.CUP) and star.is_available(1, Competition.LEAGUE)

    report = cm.play_week()
    cup_fx = next(f for f, _ in report.cup_results if f.id == fx.id)
    league_fx = next(f for f, _ in report.results if f.involves(team.id))
    rows = {row.fixture_id for row in db.query(PlayerMatchStat).filter(PlayerMatchStat.player_id == star.id)}
    assert cup_fx.id not in rows                                # kupada oturdu
    assert star.cup_suspended_matches == 0                       # ceza bir kupa maciyla dustu
    assert star.suspended_matches == 0
    assert league_fx.id in rows or star.is_injured(1)            # ligde oynayabilir (sakatlanmadiysa)


def test_career_week_plays_cup_midweek_then_league(db):
    cm = _manager(db, seed=3, mode=GameMode.CAREER)
    report = cm.play_week()
    assert len(report.results) == 12 and len(report.cup_results) == 8
    assert report.cup_label and "Son 16" in report.cup_label
    assert report.played_any

    # Iki mac oynayan (kupa + lig) oyuncunun kupa sonrasi toparlanmasi yarimdir;
    # hafta sonunda kondisyon yine de mac sonu enerjisinden turetilir ve 0-100 icindedir
    for _fx, result in report.cup_results + report.results:
        for side in (result.home, result.away):
            for mp in side.players:
                p = db.get(Player, mp.id)
                assert 0 <= p.condition <= 100


def test_season_waits_for_cup_final_then_next_cup_uses_standings(db):
    cm = _manager(db, seed=21, mode=GameMode.CAREER)
    league_weeks = cm.league_weeks()
    for _ in range(league_weeks):
        cm.play_week()
    assert cm.league_finished
    t = cm.tournaments.current()
    if cm.tournaments.last_week(t) > league_weeks:
        assert not cm.season_finished                          # final henuz oynanmadi
    _play_until_finished(cm)
    assert cm.cup_finished

    champions = {league.id: cm.standings(league.id)[0].id for league in cm.leagues()}
    new_season = cm.start_new_season()
    t2 = cm.tournaments.current()
    assert t2.season == new_season and t2.status is TournamentStatus.DRAW
    for team_id in champions.values():                          # lig sampiyonlari katilir
        assert cm.tournaments.is_participant(t2, team_id)
    assert all(p.cup_suspended_matches == 0 and p.cup_yellow_cards == 0 for p in db.query(Player))


def test_group_format_runs_to_a_champion(db):
    cm = _manager(db, seed=31, mode=GameMode.TOURNAMENT)
    tm = cm.tournaments
    t = tm.set_format(CupFormat.GROUPS)
    tm.draw_all()
    groups = {}
    for e in t.entries:
        groups.setdefault(e.group_index, []).append(e)
    assert sorted(groups) == [0, 1, 2, 3]
    for members in groups.values():
        assert sorted(e.pot for e in members) == [0, 1, 2, 3]
        leagues = [_league_of(db, e.team_id) for e in members]
        assert len(set(leagues)) == 4
    assert len(tm.fixtures(t, stage=Stage.GROUP)) == 48          # 4 grup x 12 mac

    _play_until_finished(cm)
    assert t.status is TournamentStatus.FINISHED
    rankings = tm.group_rankings(t)
    qf = tm.ties(t, Stage.QF)
    a, b, c, d = ([row.team_id for row in rows] for rows in rankings)
    expected = [(b[1], a[0]), (d[1], c[0]), (a[1], b[0]), (c[1], d[0])]
    assert [(tie.first_team_id, tie.second_team_id) for tie in qf] == expected
    for rows in rankings:
        assert all(r.played == 6 for r in rows)
        points = [r.points for r in rows]
        assert points == sorted(points, reverse=True)
    assert Counter(e.eliminated_stage for e in t.entries if e.eliminated_stage)["GROUP"] == 8
    _assert_tie_decisions(tm, t)


def test_find_team_accepts_real_club_names(db):
    cm = _manager(db)
    assert cm.find_team("Galatasaray").name == "Istanbul Lions"
    assert cm.find_team("Real Madrid").name == "Madrid Blancos"
    assert cm.find_team("Kadıköy Canaries").name == "Kadıköy Canaries"
    assert cm.find_team("Olmayan Kulüp FC") is None
