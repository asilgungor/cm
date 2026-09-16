"""
Canli kariyer maci entegrasyon testleri (9. Asama).

career_manager + tournament_manager + match_engine + live_match birlikte:
    * mudahalesiz canli lig maci == ayni tohumla otomatik mac (tum hafta birebir)
    * canli oyuncu degisikligi: dakika, oyuncu satiri, form/moral/kondisyon kaliciligi
    * kariyer haftasi: once hafta ici kupa (canli) -> play_midweek, sonra lig (canli) -> play_week
    * kullanici kupada degilken lig hazirligi bekleyen hafta ici maclarini once oynatir
    * turnuva modu: yalnizca kupa; rovansta eleme kurali dogrulamasi
    * gecersiz canli sonuc: LiveMatchError ve hicbir sey yazilmaz

Her test kendi transaction'ini rollback eder; test veritabani kirlenmez.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitness  # noqa: E402
import staff as staff_rules  # noqa: E402
from career_manager import (  # noqa: E402
    CareerManager,
    LiveMatchError,
    clamp,
    form_delta,
    morale_delta,
    outcome_for,
)
from cup_draw import Stage  # noqa: E402
from live_match import LiveMatch  # noqa: E402
from match_engine import EventType, MatchResult, prepare_fixture  # noqa: E402
from models import (  # noqa: E402
    Competition,
    Fixture,
    FixtureStatus,
    GameMode,
    Player,
    PlayerMatchStat,
    Position,
    StaffRole,
    Team,
    TournamentStatus,
)


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


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _manager(db, seed: int, mode: GameMode = GameMode.CAREER) -> CareerManager:
    cm = CareerManager(db, seed=seed)
    assert cm.current_week == 1 and cm.season == 1, "test dünyası temiz değil"
    cm.set_game_mode(mode)
    return cm


def _cup_team(cm: CareerManager, participant: bool) -> Team:
    t = cm.tournaments.ensure()
    return next(team for team in cm.teams() if cm.tournaments.is_participant(t, team.id) is participant)


def _fingerprint(r: MatchResult) -> tuple:
    """Macin tam izi: olaylar, oyuncu durumlari (enerji dahil), istatistikler, macin adami."""
    events = tuple(
        (e.minute, e.added_time, e.type.value, e.team_id, e.player_id, e.home_score, e.away_score,
         e.detail, e.description)
        for e in r.events
    )
    players = tuple(
        (p.id, p.entered_minute, p.left_minute, p.rating, p.energy, tuple(p.energy_log), p.goals,
         p.assists, p.shots, p.saves, p.yellow_cards, p.sent_off, p.injured, p.substituted)
        for team in (r.home, r.away) for p in team.players
    )
    motm = r.man_of_the_match.id if r.man_of_the_match else None
    return (r.home_score, r.away_score, events, players, r.home.stats, r.away.stats, motm,
            r.extra_time, r.home_penalties, r.away_penalties)


def _signature(cm: CareerManager) -> tuple:
    """Kalici dunya ozeti: hata sonrasi 'hicbir sey degismedi' kontrolu icin."""
    from sqlalchemy import func, select

    db = cm.db
    db.flush()
    played = db.scalar(select(func.count()).select_from(Fixture).where(Fixture.status == FixtureStatus.PLAYED))
    stats = db.scalar(select(func.count()).select_from(PlayerMatchStat))
    players = db.execute(select(
        func.sum(Player.form), func.sum(Player.morale), func.sum(Player.condition),
        func.sum(Player.suspended_matches), func.sum(Player.cup_suspended_matches),
        func.sum(Player.injured_until_week), func.sum(Player.weeks_since_match),
    )).one()
    teams = db.execute(select(func.sum(Team.points), func.sum(Team.played), func.sum(Team.transfer_budget))).one()
    return (cm.season, cm.current_week, cm.manager_reputation, played, stats, tuple(players), tuple(teams))


def _stable_squads(cm: CareerManager) -> None:
    """
    Team.players yalnizca overall'a gore sirali: esit overall'li oyuncularin sirasi DB'nin dondurdugu
    fiziksel siraya bagli (SAVEPOINT geri sarimindan sonra degisebilir) ve motorun secimleri liste
    sirasina duyarli. Karsilastirmali testlerde iki yol ayni sirayla baslasin diye sira bellekte
    (SQL uretmeden) id ile kesinlestirilir. Oturumun kimlik haritasi zayif referanslidir: takimlar
    cop toplayiciyla atilip DB sirasiyla yeniden yuklenmesin diye yoneticiye baglanip tutulur.
    """
    teams = cm.teams()
    for team in teams:
        team.players.sort(key=lambda p: (-p.overall_rating, p.id))
    cm._test_pinned_teams = teams


def _automatic_week_then_rewind(cm: CareerManager) -> tuple[dict, CareerManager]:
    """
    Haftayi otomatik oynatir (SAVEPOINT icinde), izleri toplar ve dunyayi geri sarar. Tam rollback
    kullanilmaz: kurada uretilen kupa fiksturlerinin id'si (ve mac tohumu) degisirdi. Donen yeni
    yonetici ayni rastgele durumla (cm.rng) baslar.
    """
    db = cm.db
    db.flush()
    _stable_squads(cm)
    rng_state = cm.rng.getstate()
    savepoint = db.begin_nested()
    auto = cm.play_week()
    user_id = cm.state.user_team_id
    data = {
        "prints": {fx.id: _fingerprint(r) for fx, r in auto.results + auto.cup_results},
        "user_fixture": next((fx.id for fx, r in auto.results if r is auto.user_result), None),
        "user_cup_fixture": next((fx.id for fx, r in auto.cup_results if r is auto.user_cup_result), None),
        "table": {t.id: (t.points, t.played, t.goals_for, t.goals_against) for t in cm.teams()},
        "squad": {p.id: (p.form, p.morale, p.condition, p.injured_until_week)
                  for p in db.get(Team, user_id).players},
    }
    savepoint.rollback()
    fresh = CareerManager(db, seed=cm.seed)
    fresh.rng.setstate(rng_state)
    _stable_squads(fresh)
    assert fresh.current_week == 1 and not any(f.is_played for f in fresh.fixtures_for_week(1))
    return data, fresh


def _play_live(prep, **kwargs) -> tuple[LiveMatch, MatchResult]:
    live = LiveMatch.create(
        prep.engine, prep.managed_team_id, fixture_id=prep.fixture_id,
        competition=prep.competition.value.lower(), season=prep.season, week=prep.week,
        title=prep.title, **kwargs,
    )
    live.play_to_end()
    return live, live.result()


# ---------------------------------------------------------------------------
# Lig: mudahalesiz canli mac == otomatik mac
# ---------------------------------------------------------------------------

def test_match_seed_formula(db):
    cm = _manager(db, seed=7)
    fx = cm.fixtures_for_week(1)[0]
    assert cm.match_seed(fx) == 7 * 10_000 + fx.id
    assert CareerManager(db).match_seed(fx) is None


def test_unseeded_career_fixes_the_users_match_seed_against_refresh_rerolls(db):
    """Tohumsuz kariyerde kullanicinin maci sabit tohumlu: sayfa yenileyip canli maci bastan zar atamaz."""
    cm = _manager(db, seed=7)
    fx = cm.fixtures_for_week(1)[0]
    other = next(f for f in cm.fixtures_for_week(1)
                 if fx.home_team_id not in (f.home_team_id, f.away_team_id)
                 and fx.away_team_id not in (f.home_team_id, f.away_team_id))
    cm.set_user_team(db.get(Team, fx.home_team_id))
    unseeded = CareerManager(db)
    seed = unseeded.match_seed(fx)
    assert isinstance(seed, int) and seed == CareerManager(db).match_seed(fx)
    assert unseeded.match_seed(other) is None                    # diger maclar rastgele kalir

    first = CareerManager(db).prepare_live_match()
    second = CareerManager(db).prepare_live_match()               # "sayfa yenilendi": yeni hazirlik
    assert first.fixture_id == second.fixture_id
    first.engine.run_to_end()
    second.engine.run_to_end()
    assert _fingerprint(first.engine.result()) == _fingerprint(second.engine.result())


def test_live_league_match_without_intervention_equals_automatic_week(db):
    cm = _manager(db, seed=7)
    team = _cup_team(cm, participant=False)
    team_id = team.id
    cm.set_user_team(team)
    cm.tournaments.draw_all()
    auto, cm = _automatic_week_then_rewind(cm)
    auto_prints, user_fx_id = auto["prints"], auto["user_fixture"]

    fx, competition = cm.live_fixture()
    assert competition is Competition.LEAGUE and fx.id == user_fx_id

    prep = cm.prepare_live_match()
    assert prep.fixture_id == user_fx_id and prep.competition is Competition.LEAGUE
    assert prep.managed_team_id == team_id and (prep.season, prep.week) == (1, 1)
    assert prep.title == f"Lig · 1. hafta · {fx.home_team.name} - {fx.away_team.name}"
    # Kullanici kupada degil: bekleyen hafta ici maclari once oynandi, hafta ilerlemedi
    assert prep.midweek_report is not None and prep.midweek_report.midweek_only
    assert cm.current_week == 1 and not fx.is_played

    live, result = _play_live(prep)
    assert result.events[-1].type is EventType.FULL_TIME
    assert _fingerprint(result) == auto_prints[user_fx_id]

    report = cm.save_live_result(prep.fixture_id, result)
    assert not report.midweek_only and report.user_result is result
    assert cm.current_week == 2
    assert fx.status is FixtureStatus.PLAYED and (fx.home_score, fx.away_score) == (result.home_score, result.away_score)
    assert all(f.is_played for f in cm.fixtures_for_week(1))
    assert len(report.results) == 12 and not report.cup_results      # kupa hafta ici oynanmisti
    assert report.lineup_notes == list((result.home if result.home.id == team_id else result.away).lineup_notes)

    # Tum hafta (kupa + lig) ve kalicilik otomatik yolla birebir ayni
    live_prints = {f.id: _fingerprint(r) for f, r in prep.midweek_report.cup_results + report.results}
    assert live_prints == auto_prints
    assert {t.id: (t.points, t.played, t.goals_for, t.goals_against) for t in cm.teams()} == auto["table"]
    assert {p.id: (p.form, p.morale, p.condition, p.injured_until_week) for p in team.players} == auto["squad"]


# ---------------------------------------------------------------------------
# Canli oyuncu degisikligi kaliciligi
# ---------------------------------------------------------------------------

def test_live_manual_substitution_is_persisted(db):
    from sqlalchemy import select

    cm = _manager(db, seed=5)
    _stable_squads(cm)
    team = _cup_team(cm, participant=False)
    cm.set_user_team(team)
    prep = cm.prepare_live_match()
    engine = prep.engine

    live = LiveMatch.create(engine, prep.managed_team_id, auto_subs=False, fixture_id=prep.fixture_id,
                            competition="league", season=prep.season, week=prep.week, title=prep.title)
    live.run(max_steps=36)
    live.pause()
    assert not live.finished and live.can_substitute_now
    mine = live.managed_team
    out = next(p for p in sorted(mine.on_pitch, key=lambda p: p.id) if p.role is not Position.GK)
    sub = next(p for p in sorted(mine.bench, key=lambda p: p.id) if p.position is not Position.GK)
    sub_minute = engine.minute
    event = live.substitute(out.id, sub.id)
    assert event.type is EventType.SUBSTITUTION and event.detail == "manual"
    live.play_to_end()
    result = live.result()

    before = {pid: (db.get(Player, pid).form, db.get(Player, pid).morale) for pid in (out.id, sub.id)}
    report = cm.save_live_result(prep.fixture_id, result)
    assert report.user_result is result and cm.current_week == 2

    rows = {r.player_id: r for r in db.scalars(
        select(PlayerMatchStat).where(PlayerMatchStat.fixture_id == prep.fixture_id))}
    assert sub.entered_minute == sub_minute and out.left_minute == sub_minute
    assert not (sub.injured or sub.sent_off or sub.substituted), "tohum: giren oyuncu maci bitirmeli"
    assert rows[sub.id].minutes == result.end_minute - sub_minute == sub.minutes_played
    assert rows[out.id].minutes == sub_minute - out.entered_minute == out.minutes_played
    assert out.entered_minute == 0

    goals_for = mine.stats.goals
    goals_against = (result.away if mine is result.home else result.home).stats.goals
    outcome = outcome_for(goals_for, goals_against)
    physio = cm._staff_rating(team, StaffRole.PHYSIO, "physiotherapy")
    assistant = cm._staff_rating(team, StaffRole.ASSISTANT, "man_management")
    for mp in (out, sub):
        p = db.get(Player, mp.id)
        coach = cm._staff_rating(team, StaffRole.COACH, staff_rules.coach_attribute_for(p.position))
        form0, morale0 = before[mp.id]
        assert rows[mp.id].rating == mp.rating and p.match_rating_history[-1] == mp.rating
        assert p.form == clamp(form0 + staff_rules.apply_training(form_delta(mp.rating, outcome), coach))
        assert p.morale == clamp(morale0 + staff_rules.apply_training(morale_delta(mp.rating, outcome), assistant))
        assert p.condition == fitness.recover_condition(mp.energy, physio, age=mp.age)   # kupada degil: tam toparlanma
        assert p.weeks_since_match == 0


# ---------------------------------------------------------------------------
# Kariyer haftasi: hafta ici kupa (canli) + hafta sonu lig (canli)
# ---------------------------------------------------------------------------

def test_career_week_live_cup_then_live_league_matches_automatic(db):
    cm = _manager(db, seed=13)
    team = _cup_team(cm, participant=True)
    team_id = team.id
    cm.set_user_team(team)
    cm.tournaments.draw_all()
    auto, cm = _automatic_week_then_rewind(cm)
    auto_prints = auto["prints"]

    cup_fx, competition = cm.live_fixture()
    assert competition is Competition.CUP and cup_fx.involves(team_id) and cup_fx.week == 1
    assert cup_fx.id == auto["user_cup_fixture"]
    league_fx = next(f for f in cm.fixtures_for_week(1) if f.involves(team_id))

    prep = cm.prepare_live_match()
    assert prep.competition is Competition.CUP and prep.fixture_id == cup_fx.id
    assert prep.midweek_report is None and prep.engine.knockout is None      # Son 16 ilk mac
    assert prep.title == f"Devler Arenası · Son 16 ilk maç · {cup_fx.home_team.name} - {cup_fx.away_team.name}"
    _, cup_result = _play_live(prep)

    midweek = cm.save_live_result(prep.fixture_id, cup_result)
    assert midweek.midweek_only and cm.current_week == 1
    assert not midweek.results and len(midweek.cup_results) == 8
    assert midweek.user_cup_result is cup_result and midweek.cup_label
    assert cup_fx.is_played and (cup_fx.home_score, cup_fx.away_score) == (cup_result.home_score, cup_result.away_score)
    assert cup_fx.key_events and cup_fx.key_events[-1]["type"] == "FULL_TIME"
    assert not league_fx.is_played
    # Ayni hafta lig maci var: kupada oynayanlar yarim toparlanir
    physio = cm.physio_rating(team)
    mine = cup_result.home if cup_result.home.id == team_id else cup_result.away
    played = [mp for mp in mine.players if mp.played]
    assert len(played) >= 11
    for mp in played:
        assert db.get(Player, mp.id).condition == fitness.recover_condition(
            mp.energy, physio, fitness.MIDWEEK_RECOVERY_SHARE, age=mp.age)        # 10. Asama: 32+ yavas

    # Sirada lig maci; hazirlik hafta icini tekrar oynatmaz
    assert cm.live_fixture() == (league_fx, Competition.LEAGUE)
    signature = _signature(cm)
    prep2 = cm.prepare_live_match()
    assert _signature(cm) == signature
    assert prep2.competition is Competition.LEAGUE and prep2.fixture_id == league_fx.id
    assert prep2.midweek_report is None
    _, league_result = _play_live(prep2)

    report = cm.save_live_result(prep2.fixture_id, league_result)
    assert not report.midweek_only and cm.current_week == 2
    assert report.user_result is league_result and len(report.results) == 12 and not report.cup_results
    assert league_fx.is_played

    live_prints = {f.id: _fingerprint(r) for f, r in midweek.cup_results + report.results}
    assert live_prints == auto_prints
    assert {t.id: (t.points, t.played, t.goals_for, t.goals_against) for t in cm.teams()} == auto["table"]
    assert {p.id: (p.form, p.morale, p.condition, p.injured_until_week) for p in team.players} == auto["squad"]

    # 2. hafta: rovans (eleme kurali tasinan gollerle)
    cup_fx2, competition2 = cm.live_fixture()
    assert competition2 is Competition.CUP and cup_fx2.stage == Stage.R16.value and cup_fx2.leg == 2


def test_prepare_completes_pending_draw_for_user_cup_match(db):
    cm = _manager(db, seed=23)
    t = cm.tournaments.current()
    team = _cup_team(cm, participant=True)
    cm.set_user_team(team)
    assert t.status is TournamentStatus.DRAW
    # Kura cekilmeden kupa fiksturu yok: salt sorgu lig macini gosterir; arayuz bunu
    # live_cup_draw_pending ile ayirt edip kupa macini (kura bekliyor) anlatir
    assert cm.live_fixture()[1] is Competition.LEAGUE
    assert cm.live_cup_draw_pending()

    prep = cm.prepare_live_match()
    assert not cm.live_cup_draw_pending()
    assert t.status is TournamentStatus.RUNNING
    assert prep.competition is Competition.CUP and prep.midweek_report is None
    assert not any(f.is_played for f in cm.tournaments.fixtures(t))
    assert db.get(Fixture, prep.fixture_id).involves(team.id)


def test_league_prep_plays_pending_midweek_when_user_not_in_cup(db):
    cm = _manager(db, seed=29)
    team = _cup_team(cm, participant=False)
    cm.set_user_team(team)
    t = cm.tournaments.current()
    assert t.status is TournamentStatus.DRAW

    prep = cm.prepare_live_match()
    mid = prep.midweek_report
    assert prep.competition is Competition.LEAGUE
    assert mid is not None and mid.midweek_only and mid.week == 1 and cm.current_week == 1
    assert len(mid.cup_results) == 8 and not mid.results and mid.user_cup_result is None
    assert any("otomatik" in note for note in mid.cup_notes)              # kura hafta icinde cekildi
    assert all(f.is_played for f in cm.tournaments.fixtures(t, week=1))
    assert not any(f.is_played for f in cm.fixtures_for_week(1))
    # Hafta ici maclarin oyuncu satirlari yazildi (lig macinin motoru bu durumdan kuruldu)
    cup_ids = [fx.id for fx, _ in mid.cup_results]
    rows = db.query(PlayerMatchStat).filter(PlayerMatchStat.fixture_id.in_(cup_ids)).count()
    assert rows == sum(1 for _, r in mid.cup_results for side in (r.home, r.away) for mp in side.players if mp.played)

    again = cm.prepare_live_match()                                       # hafta ici tekrar oynanmaz
    assert again.midweek_report is None and again.fixture_id == prep.fixture_id
    _, result = _play_live(again)
    report = cm.save_live_result(again.fixture_id, result)
    assert cm.current_week == 2 and report.user_result is result and not report.cup_results


# ---------------------------------------------------------------------------
# Turnuva modu
# ---------------------------------------------------------------------------

def test_tournament_mode_offers_only_cup_and_checks_knockout_rule(db):
    cm = _manager(db, seed=17, mode=GameMode.TOURNAMENT)
    tm = cm.tournaments
    team = _cup_team(cm, participant=True)
    cm.set_user_team(team)
    league_fx = next(f for f in cm.fixtures_for_week(1) if f.involves(team.id))

    prep = cm.prepare_live_match()
    assert prep.competition is Competition.CUP and prep.midweek_report is None
    _, result = _play_live(prep)
    report = cm.save_live_result(prep.fixture_id, result)
    assert not report.midweek_only and cm.current_week == 2
    assert not report.results and len(report.cup_results) == 8 and report.user_cup_result is result
    assert not league_fx.is_played

    # Rovans: motor eleme kuralini (tasinan goller) fiksturden alir
    fx2, competition = cm.live_fixture()
    assert competition is Competition.CUP and fx2.leg == 2
    prep2 = cm.prepare_live_match()
    rule = tm.knockout_rule(fx2)
    assert prep2.fixture_id == fx2.id and rule is not None and prep2.engine.knockout == rule
    assert prep2.title.startswith("Devler Arenası · Son 16 rövanş · ")

    # Eleme kurali olmadan kurulmus motorun sonucu reddedilir
    _, bare = prepare_fixture(db, fx2.id, seed=cm.match_seed(fx2), current_week=2)
    bare_result = bare.simulate()
    signature = _signature(cm)
    with pytest.raises(LiveMatchError, match="kupa kurallarıyla"):
        cm.save_live_result(fx2.id, bare_result)
    assert _signature(cm) == signature

    _, result2 = _play_live(prep2)
    report2 = cm.save_live_result(fx2.id, result2)
    assert cm.current_week == 3 and report2.user_cup_result is result2
    tie = fx2.tie
    assert tie.decided and team.id in (tie.first_team_id, tie.second_team_id)
    assert fx2.extra_time == result2.extra_time
    advancing = result2.advancing
    assert advancing is not None and tie.winner_team_id == advancing.id
    assert all(not f.is_played for f in cm.fixtures_for_week(1) + cm.fixtures_for_week(2))


# ---------------------------------------------------------------------------
# Hatalar: LiveMatchError ve hicbir sey yazilmaz
# ---------------------------------------------------------------------------

def test_invalid_live_results_raise_and_change_nothing(db):
    cm = _manager(db, seed=19)
    team = _cup_team(cm, participant=False)
    cm.set_user_team(team)
    prep = cm.prepare_live_match()
    engine = prep.engine
    for _ in range(20):
        engine.step()
    signature = _signature(cm)

    with pytest.raises(LiveMatchError, match="bitmedi"):
        cm.save_live_result(prep.fixture_id, engine.snapshot())
    assert _signature(cm) == signature
    engine.run_to_end()
    result = engine.result()

    other = next(f for f in cm.fixtures_for_week(1) if not f.involves(team.id))
    _, other_engine = prepare_fixture(db, other.id, seed=1, current_week=1)
    other_result = other_engine.simulate()
    with pytest.raises(LiveMatchError, match="ait değil"):
        cm.save_live_result(prep.fixture_id, other_result)

    with pytest.raises(LiveMatchError, match="bulunamadı"):
        cm.save_live_result(10**9, result)

    next_fx = next(f for f in cm.fixtures_for_week(2) if f.involves(team.id))
    _, next_engine = prepare_fixture(db, next_fx.id, seed=1, current_week=2)
    next_result = next_engine.simulate()
    with pytest.raises(LiveMatchError, match="bu haftanın maçı değil"):
        cm.save_live_result(next_fx.id, next_result)

    with pytest.raises(LiveMatchError, match="lig maçı"):
        cm.play_midweek({prep.fixture_id: result})

    assert _signature(cm) == signature

    # Gecerli girdi + gecersiz girdi: gecerli olan da islenmez
    with pytest.raises(LiveMatchError):
        cm.play_week({prep.fixture_id: result, next_fx.id: next_result})
    assert _signature(cm) == signature
    assert not db.get(Fixture, prep.fixture_id).is_played

    # Mac otomatik oynandiktan sonra eski canli sonuc kaydedilemez
    cm.play_week()
    assert cm.current_week == 2
    with pytest.raises(LiveMatchError, match="zaten oynanmış"):
        cm.save_live_result(prep.fixture_id, result)


def test_league_result_before_midweek_is_rejected(db):
    cm = _manager(db, seed=31)
    team = _cup_team(cm, participant=False)
    cm.set_user_team(team)
    fx, _ = cm.live_fixture()
    # prepare_live_match atlanip motor hafta ici maclardan once kurulmus
    _, engine = prepare_fixture(db, fx.id, seed=cm.match_seed(fx), current_week=1)
    stale = engine.simulate()
    signature = _signature(cm)
    with pytest.raises(LiveMatchError, match="henüz oynanmadı"):
        cm.save_live_result(fx.id, stale)
    assert _signature(cm) == signature
    assert cm.tournaments.current().status is TournamentStatus.DRAW


def test_prepare_errors_without_team_or_after_season(db):
    from sqlalchemy import update

    cm = _manager(db, seed=37)
    assert cm.live_fixture() is None
    with pytest.raises(LiveMatchError, match="takımı seç"):
        cm.prepare_live_match()

    team = _cup_team(cm, participant=False)
    cm.set_user_team(team)
    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    cm.tournaments.current().status = TournamentStatus.FINISHED
    db.flush()
    db.expire_all()
    assert cm.season_finished
    signature = _signature(cm)
    with pytest.raises(LiveMatchError, match="Sezon bitti"):
        cm.prepare_live_match()
    assert _signature(cm) == signature
