"""
Transfer gecmisi, dunya haber akisi ve sezon onurlari arsivi testleri (12. Asama, Soccer Manager
"Game World History / News Feed").

Gercek PostgreSQL'e karsi calisir; her test kendi transaction'ini rollback eder (onerilen:
TEST_DB_NAME=fm_db_test_smpkg). Eski kayit yukseltmesi ve cok kullanicili izolasyon testleri AYRI kariyer
semalarinda calisir ve semayi sonunda siler.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import career_manager as cm_module  # noqa: E402
from career_manager import (  # noqa: E402
    NEWS_AI_TRANSFERS_PER_WEEK,
    CareerManager,
    TransferNews,
    WeekReport,
)
from models import (  # noqa: E402
    Fixture,
    FixtureStatus,
    NewsItem,
    Player,
    Position,
    SeasonHonour,
    TransferLog,
)
from transfers import ContractOffer  # noqa: E402


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

NEW_TABLES = ("transfer_log", "season_honours", "news_items", "shortlist", "friendlies")
NEW_COLUMNS = (("game_state", "career_week_offset"), ("players", "transfer_locked_until"),
               ("players", "minutes_window"), ("players", "concern_level"), ("players", "contract_overall"),
               ("players", "wage_demand"))


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _manager(db, seed=13) -> CareerManager:
    cm = CareerManager(db, seed=seed)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    return cm


def _cheapest_outfield(team) -> Player:
    return min((p for p in team.players if p.position is not Position.GK), key=lambda p: (p.overall_rating, p.id))


def _buy(cm: CareerManager, buyer, player, fee: int) -> TransferNews:
    buyer.transfer_budget = max(buyer.transfer_budget, fee)
    buyer.wage_budget = buyer.wage_bill + player.current_wage + 10_000
    return cm.complete_transfer(buyer, player, fee, ContractOffer(player.current_wage, 3, player.squad_role))


# ===========================================================================
# 1) TRANSFER GECMISI VE HABERLER
# ===========================================================================

def test_user_transfer_is_logged_announced_and_leaves_the_shortlist(db):
    cm = _manager(db)
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    seller = cm.find_team("Milano Nerazzurri")
    player = _cheapest_outfield(seller)
    cm.shortlist_add(player, "sol bek alternatifi")
    assert cm.is_shortlisted(player.id)

    news = _buy(cm, user, player, 7_300_000)
    log = db.scalar(select(TransferLog).where(TransferLog.player_id == player.id))
    assert (log.season, log.week, log.player_name, log.from_team_id, log.from_team_name, log.to_team_id,
            log.to_team_name, log.fee, log.wage, log.kind) == (
        cm.season, cm.current_week, player.name, seller.id, seller.name, user.id, user.name, 7_300_000,
        news.wage, "TRANSFER")
    assert not cm.is_shortlisted(player.id)                              # alinan oyuncu listeden cikar

    item = cm.world_news(limit=1)[0]
    assert item.kind == "TRANSFER" and item.team_id == user.id and item.other_team_id == seller.id
    assert player.name in item.text and seller.name in item.text and "7.3M EUR" in item.text
    assert [n.id for n in cm.world_news(team_id=seller.id)] == [item.id]  # iki taraftan da bulunur

    # Gecmis ve rekorlar: ikinci (daha pahali, AI) transfer
    other_buyer, other_seller = cm.find_team("Madrid Blancos"), cm.find_team("Rhône Gones")
    expensive = _buy(cm, other_buyer, _cheapest_outfield(other_seller), 30_000_000)
    assert expensive.from_team_id == other_seller.id
    assert [t.fee for t in cm.record_transfers(limit=5)] == [30_000_000, 7_300_000]
    assert [t.player_id for t in cm.transfer_history(team=seller)] == [player.id]
    assert len(cm.transfer_history(season=cm.season)) == 2 and cm.transfer_history(season=cm.season + 1) == []
    assert [t.fee for t in cm.transfer_history()] == [30_000_000, 7_300_000]         # yeniden eskiye
    # AI'lar arasi transfer complete_transfer'da haber olmaz (haftalik secim run_ai_transfer_window'da)
    assert len(cm.world_news(limit=10)) == 1


def test_ai_transfer_news_keep_only_the_most_expensive_deals(db, monkeypatch):
    cm = _manager(db)
    user = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(user)
    teams = [t for t in cm.teams() if t.id != user.id]
    deals = [TransferNews(f"Oyuncu {i}", teams[i].name, teams[i + 1].name, fee, 50_000,
                          from_team_id=teams[i].id, to_team_id=teams[i + 1].id)
             for i, fee in enumerate((4_000_000, 25_000_000, 9_000_000, 1_000_000, 13_000_000))]
    monkeypatch.setattr(cm, "_ai_transfer_deals", lambda: list(deals))
    assert cm.run_ai_transfer_window() == deals
    db.flush()
    items = db.scalars(select(NewsItem).where(NewsItem.kind == "TRANSFER").order_by(NewsItem.id)).all()
    assert len(items) == NEWS_AI_TRANSFERS_PER_WEEK == 3
    assert [i.text.split(",")[0] for i in items] == ["Transfer: Oyuncu 1", "Transfer: Oyuncu 4", "Transfer: Oyuncu 2"]


def test_big_results_sponsor_signings_and_wonderkids_become_news(db, monkeypatch):
    cm = _manager(db)
    user = cm.find_team("Bosphorus Eagles")
    cm.set_user_team(user)
    cm.ensure_club_setup()
    fx = cm.fixtures_for_week(1)[0]
    side = SimpleNamespace
    result = SimpleNamespace(home=side(id=fx.home_team_id, name="Ev"), away=side(id=fx.away_team_id, name="Dep"),
                             home_score=1, away_score=5)
    cm._news_big_result(fx, result, 1, cup=False)
    result.away_score = 4
    cm._news_big_result(fx, result, 1, cup=True)                       # 3 fark: haber degil
    db.flush()
    big = db.scalars(select(NewsItem).where(NewsItem.kind == "BIG_RESULT")).all()
    assert len(big) == 1 and big[0].text == "Farklı galibiyet (Lig): Dep 5-1 Ev."
    assert (big[0].team_id, big[0].other_team_id) == (fx.away_team_id, fx.home_team_id)

    offer = cm.sign_sponsor(user, 0)
    cm.sign_sponsor(cm.find_team("Paris Rouge-Bleu"), 1)                 # AI imzasi haber olmaz
    sponsor = db.scalars(select(NewsItem).where(NewsItem.kind == "SPONSOR")).all()
    assert len(sponsor) == 1 and offer.brand in sponsor[0].text and sponsor[0].team_id == user.id

    monkeypatch.setattr(cm_module.development, "is_wonderkid", lambda *args: True)
    report = WeekReport(cm.season, cm.youth_intake_week())
    cm._youth_intake(cm.youth_intake_week(), report)
    kids = db.scalars(select(NewsItem).where(NewsItem.kind == "WONDERKID")).all()
    assert len(kids) == len(report.youth_intake) > 0
    assert all(k.team_id == user.id and "wonderkid" in k.text for k in kids)
    mine = cm.world_news(team_id=user.id, limit=100)
    assert len(cm.world_news(limit=2)) == 2 and len(mine) >= len(kids) + 1
    assert all(user.id in (n.team_id, n.other_team_id) for n in mine)


# ===========================================================================
# 2) SEZON ONURLARI
# ===========================================================================

def test_full_season_archives_honours_once_with_news(db):
    cm = _manager(db, seed=29)
    cm.run_ai_transfer_window = lambda: []
    user = cm.find_team("Istanbul Lions")
    cm.set_user_team(user)
    league_weeks = cm.league_weeks()
    reports = []
    while not cm.season_finished:
        reports.append(cm.play_week())
    leagues = cm.leagues()
    assert len(reports[league_weeks - 1].honours_notes) == len(leagues)
    assert any("Devler Arenası" in n for n in reports[-1].honours_notes)

    honours = cm.season_honours(1)
    assert [h.kind for h in honours] == ["LEAGUE"] * len(leagues) + ["CUP"]
    for league in leagues:
        honour = next(h for h in honours if h.league_id == league.id)
        table = cm.standings(league.id)
        assert (honour.champion_team_id, honour.runner_up_team_id) == (table[0].id, table[1].id)
        assert (honour.champion_name, honour.competition_name) == (table[0].name, league.name)
        scorer = cm.top_scorers(league.id, limit=1)[0]
        assert (honour.top_scorer_name, honour.top_scorer_goals, honour.top_scorer_team) == (
            scorer.player.name, scorer.goals, scorer.team.name)
        assert honour.player_of_season_name and 1.0 <= honour.player_of_season_rating <= 10.0
        apps = db.scalar(select(func.count()).select_from(Fixture).join(
            cm_module.PlayerMatchStat, cm_module.PlayerMatchStat.fixture_id == Fixture.id).where(
            Fixture.league_id == league.id, cm_module.PlayerMatchStat.player_id == honour.player_of_season_id))
        assert apps >= 3                                                    # en az lig haftalarinin yarisi
        expected_position = next(i for i, t in enumerate(table, 1) if t.id == user.id) if user in table else None
        assert honour.user_team_position == expected_position
    cup = honours[-1]
    t = cm.tournaments.current()
    assert cup.champion_team_id == t.champion_team_id and cup.runner_up_team_id not in (None, t.champion_team_id)
    assert cup.top_scorer_goals >= 1 and cup.league_id is None and cup.user_team_position is None

    kinds = [n.kind for n in cm.world_news(limit=500)]
    assert kinds.count("LEAGUE_CHAMPION") == len(leagues) and kinds.count("CUP_CHAMPION") == 1

    champion = cm.db.get(cm_module.Team, cup.champion_team_id)
    club = cm.club_honours(champion)
    assert club.cup_titles == 1 and club.titles[cup.competition_name] == 1
    assert club.total_titles == len(club.honours) and cup in club.honours
    runner = cm.club_honours(cm.db.get(cm_module.Team, cup.runner_up_team_id))
    assert cup in runner.runner_ups and runner.runner_up_finishes >= 1

    # Yeni sezon arsivi tekrar yazmaz; onceki sezon sorgulanabilir kalir
    cm.start_new_season()
    assert db.scalar(select(func.count()).select_from(SeasonHonour)) == len(leagues) + 1
    assert cm.season_honours(cm.season) == [] and len(cm.season_honours()) == len(leagues) + 1


def test_honours_survive_a_quick_season_finish_through_start_new_season(db):
    cm = _manager(db)
    user = cm.find_team("Karadeniz Storm")
    cm.set_user_team(user)
    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    db.expire_all()
    assert cm.season_finished and cm.season_honours() == []
    cm.start_new_season()
    honours = cm.season_honours(1)
    assert len(honours) == len(cm.leagues())                          # kupa hic oynanmadi: kupa arsivi yok
    mine = next(h for h in honours if h.league_id == user.league_id)
    assert mine.user_team_position is not None and mine.top_scorer_name is None
    assert mine.player_of_season_name is None


# ===========================================================================
# 3) ESKI KAYIT VE COK KULLANICILI IZOLASYON
# ===========================================================================

OLD_SAVE_SCHEMA = "test_smpkg_old_save"
ISOLATION_SCHEMA = "test_smpkg_iso"


def test_old_save_gets_the_new_tables_and_columns():
    import database
    import seed
    from database import SessionLocal

    with database.career_context(OLD_SAVE_SCHEMA):
        database.drop_career_schema(OLD_SAVE_SCHEMA)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            with database.career_connection() as conn:                        # 12. Asama oncesi kayit
                for table in NEW_TABLES:
                    conn.exec_driver_sql(f'DROP TABLE "{table}"')
                for table, column in NEW_COLUMNS:
                    conn.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
            problems = set(database.schema_problems())
            assert {f"eksik tablo: {t}" for t in NEW_TABLES} <= problems
            assert {f"eksik sütun: {t}.{c}" for t, c in NEW_COLUMNS} <= problems

            applied = set(database.upgrade_schema())
            assert {f"tablo eklendi: {t}" for t in NEW_TABLES} <= applied
            assert {f"sütun eklendi: {t}.{c}" for t, c in NEW_COLUMNS} <= applied
            assert database.upgrade_schema() == [] and database.schema_problems() == []

            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=5)
                cm.set_user_team(cm.find_team("Istanbul Lions"))
                assert all(p.minutes_window == [] and p.concern_level == 0 for p in cm.user_team.players)
                assert cm.career_week == 1
                report = cm.play_week()
                assert report.played_any and cm.career_week == 2
                session.commit()
            finally:
                session.close()
        finally:
            database.drop_career_schema(OLD_SAVE_SCHEMA)


def _public_digest(conn) -> tuple:
    digests = []
    for table in (*NEW_TABLES, "players", "teams"):
        digests.append(conn.scalar(text(
            f'SELECT md5(coalesce(string_agg(t::text, \'|\' ORDER BY t::text), \'\')) FROM "public"."{table}" t')))
    return tuple(digests)


def test_package_features_stay_inside_the_career_schema():
    import database
    import seed
    from database import SessionLocal

    with database.engine.connect() as conn:
        public_before = _public_digest(conn)
    with database.career_context(ISOLATION_SCHEMA):
        database.drop_career_schema(ISOLATION_SCHEMA)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=4)
                cm.ensure_club_setup()
                user = cm.find_team("Istanbul Lions")
                cm.set_user_team(user)
                seller = cm.find_team("London Gunners")
                target = _cheapest_outfield(seller)
                watched = _cheapest_outfield(cm.find_team("Madrid Blancos"))
                cm.shortlist_add(watched, "izle")
                _buy(cm, user, target, 5_000_000)
                friendly = cm.play_friendly(cm.find_team("Rhône Gones"))
                cm.play_week()
                session.execute(update(Fixture).where(Fixture.season == cm.season, Fixture.competition == "LEAGUE")
                                .values(status=FixtureStatus.PLAYED, home_score=2, away_score=0))
                session.flush()
                session.expire_all()
                cm._archive_finished_leagues(None)
                rows = cm.player_concerns(cm.user_team)
                assert rows and all(r.wanted >= 0 for r in rows)
                target_id, watched_id, user_id = target.id, watched.id, user.id
                session.commit()
            finally:
                session.close()

            session = SessionLocal()
            try:
                assert session.scalar(text("SELECT current_schema()")) == ISOLATION_SCHEMA
                cm = CareerManager(session, seed=4)
                # 13. Asama: AI talimatlariyla degisen mac sonuclari AI transfer penceresinin zarlarini da kaydirir;
                # ayni hafta bir AI transferi olabilir, kullanicinin transferi en yeni kayit olmak zorunda degil
                assert any(row.player_id == target_id and row.to_team_id == user_id for row in cm.transfer_history())
                assert cm.shortlist()[0].player_id == watched_id and cm.is_shortlisted(watched_id)
                assert cm.friendlies()[0].id == friendly.friendly_id
                assert len(cm.season_honours(1)) == len(cm.leagues())
                assert any(n.kind == "TRANSFER" for n in cm.world_news(team_id=user_id))
                moved = session.get(Player, target_id)
                assert moved.team_id == user_id and moved.transfer_locked_until is not None
                assert any(p.minutes_window for p in cm.user_team.players)
            finally:
                session.close()
        finally:
            database.drop_career_schema(ISOLATION_SCHEMA)

    with database.engine.connect() as conn:
        assert _public_digest(conn) == public_before
    from database import SessionLocal as PublicSession
    with PublicSession() as session:                                      # baglamsiz: public kariyer
        for model in (TransferLog, SeasonHonour, NewsItem, cm_module.ShortlistEntry, cm_module.Friendly):
            assert session.scalar(select(func.count()).select_from(model)) == 0, model.__tablename__
