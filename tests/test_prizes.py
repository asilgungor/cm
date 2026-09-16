"""
Lig TV geliri, lig odul parasi, kupa primleri ve baskan guvencesi testleri (12. Asama).

Saf kurallar (finance.py) DB'siz; entegrasyon testleri gercek PostgreSQL'e karsi calisir ve her test kendi
transaction'ini rollback eder (onerilen: TEST_DB_NAME=fm_db_test_smpkg). 'public' test dunyasi seed ile kurulur
ve kulup ekonomisi KURULMAMISTIR (ensure_club_setup calismamis): orada TV/odul/guvence yoktur (eski davranis).
Testler ensure_club_setup'i rollback edilen oturumda cagirir.
"""

from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean

import pytest
from sqlalchemy import func, select, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facilities as fac  # noqa: E402
import finance  # noqa: E402
from cup_draw import Stage  # noqa: E402

# ===========================================================================
# 1) SAF KURALLAR
# ===========================================================================


def test_tv_money_is_an_equal_share_that_grows_with_league_reputation():
    shares = [finance.tv_money_weekly(rep, 4) for rep in (50, 70, 80, 90, 100)]
    assert shares == sorted(shares) and len(set(shares)) == len(shares)
    assert all(s % 1000 == 0 and s > 0 for s in shares)
    assert finance.tv_money_weekly(85, 0) == 0 and finance.tv_pool_weekly(85, 0) == 0
    # Havuz kulup sayisiyla buyur ama dogrusal degil: kalabalik ligde kulup basi pay biraz kuculur
    assert finance.tv_pool_weekly(85, 20) > finance.tv_pool_weekly(85, 4)
    assert 0.75 * finance.tv_money_weekly(85, 4) < finance.tv_money_weekly(85, 20) < finance.tv_money_weekly(85, 4)
    # Kalibrasyon noktalari (docstring): 4 kulupluk ligde itibar ortalamasi 76 / 85 / 95
    assert 450_000 <= finance.tv_money_weekly(75.75, 4) <= 520_000
    assert 850_000 <= finance.tv_money_weekly(84.75, 4) <= 950_000
    assert 1_550_000 <= finance.tv_money_weekly(95, 4) <= 1_750_000


def test_tv_share_is_comparable_to_average_sponsor_and_gate_income():
    """SM: lig gelirinin yaklasik yarisi TV. Ortalama kulubun haftalik sponsor + (yarim) mac gunu geliriyle kiyasla."""
    for rep in (75, 85, 92):
        capacity = fac.default_stadium_capacity(rep)
        club_income = fac.sponsor_base_weekly(rep) + fac.gate_income(capacity, rep) / 2
        ratio = finance.tv_money_weekly(rep, 4) / club_income
        assert 0.6 <= ratio <= 1.4, (rep, ratio)


def test_league_prize_descends_from_the_champion():
    for size in (4, 18, 20):
        prizes = [finance.league_prize(pos, size, 85) for pos in range(1, size + 1)]
        assert prizes[0] == max(prizes) and prizes == sorted(prizes, reverse=True), size
        assert prizes[0] > prizes[1] > prizes[-1] > 0
        assert all(p % 10_000 == 0 for p in prizes)
        # Sampiyon kulubun sezonluk TV gelirinin 1 kati, sonuncu ~%10'u (cift devre varsayilan lig haftasi)
        season_tv = finance.tv_money_weekly(85, size) * 2 * (size - 1)
        assert abs(prizes[0] - season_tv) <= 10_000
        assert abs(prizes[-1] - 0.10 * season_tv) <= 10_000
    assert finance.league_prize(0, 4, 85) == 0 and finance.league_prize(5, 4, 85) == 0
    assert finance.league_prize(1, 0, 85) == 0
    # Guclu lig ve uzun sezon daha cok oder
    assert finance.league_prize(1, 4, 95) > finance.league_prize(1, 4, 75)
    assert finance.league_prize(1, 4, 85, league_weeks=12) == pytest.approx(
        2 * finance.league_prize(1, 4, 85, league_weeks=6), abs=10_000)


def test_cup_round_prizes_reward_progress():
    for stage in ("GROUP", "R16", "QF", "SF", "FINAL"):
        assert finance.cup_round_prize(stage, True) > finance.cup_round_prize(stage, False) > 0
    assert finance.cup_round_prize(Stage.FINAL, True) == finance.cup_round_prize("FINAL", True)
    assert finance.cup_round_prize("PLAYOFF", True) == 0
    knockout = ("R16", "QF", "SF", "FINAL")
    champion = sum(finance.cup_round_prize(s, True) for s in knockout)
    runner_up = sum(finance.cup_round_prize(s, True) for s in knockout[:-1]) + finance.cup_round_prize("FINAL", False)
    semi = sum(finance.cup_round_prize(s, True) for s in knockout[:2]) + finance.cup_round_prize("SF", False)
    assert champion == 12_000_000 and runner_up == 8_500_000 and semi == 3_750_000
    assert champion > runner_up > semi > finance.cup_round_prize("R16", False)


def test_chairman_top_up_fills_the_gap_to_half_the_league_average():
    assert finance.club_net_worth(10_000_000, 25_000_000) == 35_000_000
    assert finance.chairman_top_up(100_000_000, 150_000_000) == 0
    assert finance.chairman_top_up(75_000_000, 150_000_000) == 0            # tam tabanda
    top_up = finance.chairman_top_up(60_000_000, 150_000_000)
    assert top_up == 15_000_000
    odd = finance.chairman_top_up(60_000_001, 150_000_000)                  # yukari yuvarlanir
    assert odd == 15_000_000 and 60_000_001 + odd >= 75_000_000
    assert finance.chairman_top_up(0, 1_000) == 100_000


def test_economy_does_not_explode_in_a_synthetic_season():
    """
    Yeni akislar sentetik dunyanin baslangic butcelerine gore sinirli kalir (6 lig haftasi):
        olagan sezon (TV + orta sira odulu + ceyrek finalde elenme)  : kucuk kulup <= %30, elit <= %10
        en iyi sezon (TV + lig sampiyonlugu + kupa sampiyonlugu)       : kucuk kulup <= %75, elit <= %20
    """
    import seed

    quarter_final_exit = finance.cup_round_prize("R16", True) + finance.cup_round_prize("QF", False)
    for league in seed.LEAGUE_DATA:
        teams = league["teams"]
        size = len(teams)
        avg = mean(rep for _name, rep, _budget, _band in teams)
        tv_season = finance.tv_money_weekly(avg, size) * 2 * (size - 1)
        for name, _rep, budget, _band in teams:
            typical = tv_season + finance.league_prize((size + 1) // 2, size, avg) + quarter_final_exit
            best = tv_season + finance.league_prize(1, size, avg) + 12_000_000
            elite = budget >= 150_000_000
            assert typical <= (0.10 if elite else 0.30) * budget, (name, typical / budget)
            assert best <= (0.20 if elite else 0.75) * budget, (name, best / budget)


# ===========================================================================
# 2) ENTEGRASYON
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, seed=21, configured=True):
    from career_manager import CareerManager

    cm = CareerManager(db, seed=seed)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    if configured:
        cm.ensure_club_setup()
    cm.run_ai_transfer_window = lambda: []          # bonservis hareketleri kasa hesabini bozmasin
    return cm


def _finish_season_quickly(db, cm) -> None:
    from models import Fixture, FixtureStatus

    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    db.expire_all()
    assert cm.season_finished


@integration
@pytest.mark.integration
def test_weekly_tv_money_reaches_every_configured_league_club(db):
    from career_manager import WeekReport
    from models import Fixture, FixtureStatus

    cm = _manager(db)
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    teams = cm.teams()
    unconfigured = cm.find_team("Karadeniz Storm")
    unconfigured.stadium_capacity = None
    db.flush()
    week = cm.current_week
    before = {t.id: (t.transfer_budget, cm.wage_summary(t).free,
                     t.sponsor_weekly if fac.sponsor_active(t.sponsor_name, t.sponsor_until_season, cm.season) else 0)
              for t in teams}

    assert cm._tv_shares(week) == {}                                   # henuz lig maci oynanmadi
    report = cm.play_week()
    db.flush()
    home = dict(db.execute(select(Fixture.home_team_id, func.count()).where(
        Fixture.season == cm.season, Fixture.week == week, Fixture.status == FixtureStatus.PLAYED,
        Fixture.neutral_venue.is_(False)).group_by(Fixture.home_team_id)).all())
    shares = cm._tv_shares(week)
    for league in cm.leagues():
        expected = finance.tv_money_weekly(mean(t.reputation for t in league.teams), len(league.teams))
        assert {shares[t.id] for t in league.teams} == {expected}      # ligde esit pay
    for team in teams:
        budget, free, sponsor = before[team.id]
        gate = home.get(team.id, 0) * fac.gate_income(team.stadium_capacity, team.reputation)
        tv = shares[team.id] if team.id != unconfigured.id else 0      # kurulmamis kulup TV payi almaz
        # 1. hafta: kupada yalnizca ilk maclar (eslesme kesinlesmez, prim yok); odul/guvence yok
        assert team.transfer_budget == max(0, budget + free + sponsor + gate + tv), team.name
    assert report.tv_income == shares[user.id] > 0
    assert f"TV geliri: {finance.format_money(report.tv_income)}" in report.finance_note
    assert report.finance_note.index("TV geliri") < report.finance_note.index("transfer kasası")

    # Lig maci olmayan hafta TV yok; dogrudan maas adimi da TV odemez
    empty = WeekReport(cm.season, 99)
    cm._pay_weekly_wages(empty, 99)
    assert empty.tv_income == 0 and "TV geliri" not in empty.finance_note


@integration
@pytest.mark.integration
def test_unconfigured_world_keeps_the_old_economy(db):
    cm = _manager(db, configured=False)
    team = cm.find_team("Istanbul Lions")
    cm.set_user_team(team)
    before, surplus = team.transfer_budget, cm.wage_summary(team).free
    report = cm.play_week()
    assert report.tv_income == 0 and team.transfer_budget == before + surplus
    from models import Fixture, FixtureStatus

    db.execute(update(Fixture).where(Fixture.season == cm.season, Fixture.competition == "LEAGUE")
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    db.expire_all()
    budgets = {t.id: t.transfer_budget for t in cm.teams()}
    assert cm._archive_finished_leagues(None) == []                     # arsiv yazilir, odul odenmez
    assert cm._chairman_safety_net() == []
    assert cm.season_payouts == {} and {t.id: t.transfer_budget for t in cm.teams()} == budgets
    assert len(cm.season_honours(1)) == len(cm.leagues())


@integration
@pytest.mark.integration
def test_league_prize_is_paid_exactly_once_before_rollover(db):
    from models import GameMode, SeasonHonour

    cm = _manager(db)
    user = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(user)
    _finish_season_quickly(db, cm)
    league = user.league
    for points, team in zip((9, 7, 4, 1), sorted(league.teams, key=lambda t: t.name), strict=True):
        team.points = points
    db.flush()
    table = cm.standings(league.id)
    strength = mean(t.reputation for t in table)
    before = {t.id: t.transfer_budget for t in cm.teams()}

    notes = cm._archive_finished_leagues(None)
    for position, team in enumerate(table, start=1):
        expected = finance.league_prize(position, len(table), strength, cm.league_weeks())
        assert team.transfer_budget == before[team.id] + expected > before[team.id]
        assert cm.season_payouts[team.id]["league_prize"] == expected
    user_position = next(i for i, t in enumerate(table, start=1) if t.id == user.id)
    assert any(f"Lig ödülü ({league.name}, {user_position}. sıra)" in n for n in notes)
    assert len(cm.leagues()) == db.scalar(select(func.count()).select_from(SeasonHonour).where(
        SeasonHonour.kind == "LEAGUE"))

    # Ikinci arsiv ve yeni sezon tekrar odemez
    paid = {t.id: t.transfer_budget for t in cm.teams()}
    assert cm._archive_finished_leagues(None) == []
    assert {t.id: t.transfer_budget for t in cm.teams()} == paid
    cm.start_new_season()
    assert all(v.get("league_prize", 0) == 0 for v in cm.season_payouts.values())
    assert db.scalar(select(func.count()).select_from(SeasonHonour).where(SeasonHonour.season == 1,
                                                                         SeasonHonour.kind == "LEAGUE")) == 6
    assert cm.game_mode is GameMode.CAREER


@integration
@pytest.mark.integration
def test_league_prize_is_paid_in_the_week_the_league_ends_and_cup_prizes_per_tie(db):
    from models import CupTie, Tournament

    cm = _manager(db, seed=33)
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    paid: list[tuple[str, int, bool, int]] = []
    award = cm._award_cup_prize

    def recording(stage, team_id, won, report):
        amount = award(stage, team_id, won, report)
        paid.append((stage, team_id, won, amount))
        return amount

    cm._award_cup_prize = recording
    league_weeks = cm.league_weeks()
    reports = []
    while not cm.season_finished:
        reports.append(cm.play_week())
    final_league_week = reports[league_weeks - 1]
    assert final_league_week.prize_income >= cm.season_payouts[user.id]["league_prize"] > 0
    assert any(n.startswith("Lig ödülü") for n in final_league_week.prize_notes)
    assert "ödül parası" in final_league_week.finance_note
    assert len(final_league_week.honours_notes) == len(cm.leagues())
    assert all(r.prize_income == 0 or r.prize_notes for r in reports)

    t = db.scalar(select(Tournament).where(Tournament.season == cm.season))
    ties = list(db.scalars(select(CupTie).where(CupTie.tournament_id == t.id)))
    assert ties and all(tie.decided for tie in ties)
    assert len(paid) == 2 * len(ties)                                  # eslesme basina tam iki odeme
    for tie in ties:
        loser = tie.second_team_id if tie.winner_team_id == tie.first_team_id else tie.first_team_id
        assert (tie.stage, tie.winner_team_id, True, finance.cup_round_prize(tie.stage, True)) in paid
        assert (tie.stage, loser, False, finance.cup_round_prize(tie.stage, False)) in paid
    champion_total = sum(a for _s, tid, _w, a in paid if tid == t.champion_team_id)
    assert champion_total == 12_000_000

    # Kupa primleri kullanicinin raporuna da yazilir (katildiysa)
    user_prizes = sum(a for _s, tid, _w, a in paid if tid == user.id)
    reported = sum(r.prize_income for r in reports) - cm.season_payouts[user.id]["league_prize"]
    assert reported == user_prizes


@integration
@pytest.mark.integration
def test_cup_prizes_are_not_paid_in_tournament_mode(db):
    from career_manager import WeekReport
    from models import GameMode

    cm = _manager(db)
    cm.set_game_mode(GameMode.TOURNAMENT)
    team = cm.find_team("Madrid Blancos")
    budget = team.transfer_budget
    assert cm._award_cup_prize("FINAL", team.id, True, WeekReport(1, 1)) == 0
    assert team.transfer_budget == budget
    assert cm._archive_finished_leagues(None) == [] and cm._tv_shares(1) == {}


@integration
@pytest.mark.integration
def test_chairman_tops_up_a_club_far_below_the_league_average(db):
    from models import NewsItem, Player

    cm = _manager(db)
    poor = cm.find_team("Karadeniz Storm")
    rich = cm.find_team("Istanbul Lions")
    cm.set_user_team(poor)
    poor.transfer_budget = 0
    db.execute(update(Player).where(Player.team_id == poor.id).values(overall_rating=40, potential_rating=40))
    db.flush()
    db.expire_all()
    _finish_season_quickly(db, cm)
    cm.start_new_season()
    db.flush()

    payout = cm.season_payouts[poor.id]["chairman"]
    assert payout > 0 and payout % 100_000 == 0
    assert cm.season_payouts.get(rich.id, {}).get("chairman", 0) == 0
    worths = dict(db.execute(select(Player.team_id, func.sum(Player.market_value)).group_by(Player.team_id)).all())
    league = poor.league
    worth = {t.id: t.transfer_budget + int(worths.get(t.id) or 0) for t in league.teams}
    average_before = (sum(worth.values()) - payout) / len(worth)
    assert worth[poor.id] >= 0.5 * average_before > worth[poor.id] - 100_000
    assert any("Başkan kulübe" in n for n in cm.new_season_notes)
    news = db.scalar(select(NewsItem).where(NewsItem.kind == "CHAIRMAN", NewsItem.team_id == poor.id))
    assert news is not None and news.season == cm.season and news.week == 1


@integration
@pytest.mark.integration
def test_buying_is_blocked_while_the_transfer_budget_is_negative(db):
    from transfers import TransferError

    cm = _manager(db)
    buyer = cm.find_team("Istanbul Lions")
    cm.set_user_team(buyer)
    target = cm.find_team("Manchester Blue").players[-1]
    buyer.transfer_budget = -1                                    # CHECK nedeniyle flush edilmez: bellek durumu
    with pytest.raises(TransferError, match="Transfer kasası ekside"):
        cm.offer_fee(buyer, target, 0)
    with pytest.raises(TransferError, match="Transfer kasası ekside"):
        cm.open_negotiation(buyer, target, 0)
    buyer.transfer_budget = 10_000_000
