"""
Milli takimlar ve Dunya Kupasi testleri (Faz 12 / 14. Asama, C2: national_teams.py). Gercek PostgreSQL'e karsi
calisir; kendi veritabaninda calistirin: TEST_DB_NAME=fm_db_test_c2.

Modul basinda temiz sentetik dunya kurulur ve tests/world_helpers.make_shared_public ile milli takimlari ACIK
paylasilan dunyaya cevrilir (sahip = birincil koltuk + kulubu olan bir menajer). Testler kendi islemlerini geri
alir. Acik / kapali parite testi dunyayi iki kez yeniden kurar; modul sonunda cleanup_shared dunyayi temizler.
"""

from __future__ import annotations

import gc
import sys
from pathlib import Path

import pytest
from sqlalchemy import DateTime, func, select, text, update

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import extensions  # noqa: E402
import national_rules  # noqa: E402
from bracket_view import (  # noqa: E402
    GroupRowView,
    RoundView,
    TieView,
    bracket_html,
    group_tables_html,
)
from career_manager import CareerManager, SeasonNotFinished, WeekReport  # noqa: E402
from career_views import cup_report_lines  # noqa: E402
from match_engine import build_match_team  # noqa: E402
from models import (  # noqa: E402
    Fixture,
    FixtureStatus,
    GameMode,
    InternationalEntry,
    InternationalFixture,
    InternationalTournament,
    League,
    LineupStatus,
    Nation,
    NationalCallup,
    NationalJobOffer,
    NewsItem,
    Notification,
    Player,
    PlayerMatchStat,
    Position,
    Team,
    TournamentStatus,
    WorldEvent,
    WorldManager,
)
from national_teams import (  # noqa: E402
    DISABLED_TEXT,
    NationalExtension,
    NationalPlayer,
    NationalSquad,
    NationalTeamError,
    NationalTeams,
)
from tactics import FORMATIONS  # noqa: E402
from world_rules import WorldRules  # noqa: E402


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

OWNER, MEMBER = "c2_sahip", "c2_uye"
OWNER_TEAM, MEMBER_TEAM = "Istanbul Lions", "Madrid Blancos"
SEED = 2112
LEAGUE_COUNTRIES = {"Türkiye", "İngiltere", "İtalya", "İspanya", "Almanya", "Fransa"}
CAPS = ("international_caps", "international_goals")


def _intl_rules(**changes) -> WorldRules:
    """Paylasilan dunya + milli takimlar (insan pazari kapali: yalnizca NationalExtension yuklenir)."""
    return WorldRules.shared_defaults().with_changes(
        {"internationals": True, "human_market": False, "loans": False, **changes})


def _fresh_world(mode: GameMode | None = GameMode.CAREER) -> None:
    import seed

    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")
    if mode is not None:
        with database.session_scope() as db:
            CareerManager(db).set_game_mode(mode)


def _drop_test_users() -> None:
    database.init_accounts()
    with database.engine.begin() as conn:
        conn.execute(text('DELETE FROM "accounts".users WHERE username = ANY(:names)'), {"names": [OWNER, MEMBER]})


@pytest.fixture(scope="module")
def shared():
    from tests.world_helpers import cleanup_shared, make_shared_public

    cleanup_shared(None, reseed=False)
    _drop_test_users()
    _fresh_world()
    world = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM)], owner_team=OWNER_TEAM, rules=_intl_rules())
    try:
        yield world
    finally:
        cleanup_shared(world, reseed=False)
        _fresh_world(mode=None)          # conftest ile ayni temiz dunya (mod secilmemis): sonraki moduller sezon basinda


@pytest.fixture
def db(shared):
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ===========================================================================
# Yardimcilar
# ===========================================================================

def _managers(db, shared) -> tuple[CareerManager, CareerManager]:
    owner = CareerManager(db, seed=SEED)
    member = CareerManager(db, seed=SEED, manager_user_id=shared.user_ids[MEMBER])
    if owner.season != 1 or owner.current_week != 1 or owner.season_finished:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    return owner, member


def _setup(db, shared, owner_reputation: float | None = None):
    owner, member = _managers(db, shared)
    if owner_reputation is not None:
        owner.state.manager_reputation = owner_reputation
        db.flush()
    national = NationalTeams(owner)
    notes = national.ensure_setup()
    return owner, member, national, NationalTeams(member), notes


def _tournament(db, season: int, kind: str) -> InternationalTournament | None:
    return db.scalar(select(InternationalTournament).where(InternationalTournament.season == season,
                                                            InternationalTournament.kind == kind))


def _fixtures(db, t: InternationalTournament) -> list[InternationalFixture]:
    return db.scalars(select(InternationalFixture).where(InternationalFixture.tournament_id == t.id)
                      .order_by(InternationalFixture.id)).all()


def _seat_row(db, shared, username: str) -> WorldManager:
    return db.get(WorldManager, shared.seat_ids[username])


def _finish_club_season(db, cm: CareerManager) -> None:
    """Lig ve Devler Arenasi bitmis sayilir (milli turnuvalara dokunulmaz)."""
    db.execute(update(Fixture).where(Fixture.season == cm.season)
               .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
    db.flush()
    db.expire_all()
    cm.tournaments.ensure().status = TournamentStatus.FINISHED
    db.flush()
    assert cm.season_finished


def _table_rows(db, name: str, exclude: tuple[str, ...] = ()) -> list[tuple]:
    table = database.Base.metadata.tables[name]
    cols = [c for c in table.columns if not isinstance(c.type, DateTime) and c.name not in exclude]
    return [tuple(row) for row in db.execute(select(*cols).order_by(*table.primary_key.columns)).all()]


def _notifications(db, seat_id: int) -> list[str]:
    return db.scalars(select(Notification.text).where(Notification.manager_id == seat_id)
                      .order_by(Notification.id)).all()


def _nation_of_player(db, player_id: int) -> str:
    nationality, country = db.execute(
        select(Player.nationality, League.country).join(Team, Team.id == Player.team_id)
        .join(League, League.id == Team.league_id).where(Player.id == player_id)).one()
    return national_rules.nation_of(nationality, country)


def _play_out_close_season(db, cm: CareerManager, national: NationalTeams) -> list[WeekReport]:
    reports = []
    while national.close_season_pending():
        report = WeekReport(season=cm.season, week=cm.current_week)
        assert national.play_close_season_matchday(report)
        reports.append(report)
        assert len(reports) < 30
    return reports


# ===========================================================================
# 1) Kurulum
# ===========================================================================

def test_ensure_setup_builds_nations_qualifiers_and_offers_idempotently(db, shared):
    owner, _member, national, _member_national, notes = _setup(db, shared)
    assert national.enabled() and notes[0].startswith("6 milli takım kuruldu")
    assert any(isinstance(ext, NationalExtension) for ext in extensions.load(owner))

    nations = db.scalars(select(Nation).order_by(Nation.id)).all()
    assert {n.name for n in nations} == LEAGUE_COUNTRIES                  # sentetik: uyruk yok -> lig ulkesi
    for nation in nations:
        assert 1 <= nation.reputation <= 100 and nation.formation in FORMATIONS
        assert nation.ai_managed and nation.manager_id is None and len(nation.code) == 3
    turkey = next(n for n in nations if n.name == "Türkiye")
    assert turkey.code == "TUR"
    top = sorted(db.scalars(
        select(Player.overall_rating).join(Team, Team.id == Player.team_id).join(League, League.id == Team.league_id)
        .where(League.country == "Türkiye", Player.in_academy.is_(False))), reverse=True)[:23]
    assert turkey.reputation == round(sum(top) / 23)

    qualifier, wc = _tournament(db, 1, "QUALIFIER"), _tournament(db, 1, "WORLD_CUP")
    assert qualifier.status == "RUNNING" and wc.status == "DRAW"
    assert [(d["stage"], d["round"], d["size"]) for d in wc.calendar] == [
        ("GROUP", 1, 4), ("GROUP", 2, 4), ("GROUP", 3, 4), ("FINAL", 1, 4)]
    entries = db.scalars(select(InternationalEntry).where(InternationalEntry.tournament_id == qualifier.id)).all()
    assert sorted(sum(1 for e in entries if e.group_index == g) for g in (0, 1)) == [3, 3]
    fixtures = _fixtures(db, qualifier)
    assert len(fixtures) == 12 and all(fx.status == "unplayed" and not fx.neutral for fx in fixtures)
    assert sorted({(fx.leg, fx.week) for fx in fixtures}) == [(1, 2), (2, 2), (3, 4), (4, 4), (5, 5), (6, 5)]
    pairs = {}
    for fx in fixtures:
        pairs.setdefault(frozenset((fx.home_nation_id, fx.away_nation_id)), []).append((fx.home_nation_id, fx.away_nation_id))
    assert len(pairs) == 6 and all(len(v) == 2 and v[0] == v[1][::-1] for v in pairs.values())   # cift devre

    views = national.nations()
    assert [v.rank for v in views] == [1, 2, 3, 4, 5, 6] and all(v.squad_size == 23 for v in views)
    assert all(v.stage_label.startswith("Elemeler · Grup") and v.qualified is None for v in views)
    assert "elemeleri sürüyor" in national.world_cup_status()

    counts = {model: db.scalar(select(func.count()).select_from(model))
              for model in (Nation, NationalCallup, InternationalTournament, InternationalEntry, InternationalFixture,
                            NationalJobOffer, Notification)}
    assert counts[NationalJobOffer] == 6                                   # 2 menajer x 3 teklif
    assert national.ensure_setup() == []
    assert NationalTeams(owner).ensure_setup() == []
    assert counts == {model: db.scalar(select(func.count()).select_from(model)) for model in counts}


def test_disabled_rules_leave_the_world_untouched(db, shared):
    owner, _member = _managers(db, shared)
    owner.state.world_rules = _intl_rules(internationals=False).to_dict()
    db.flush()
    national = NationalTeams(owner)
    assert not national.enabled() and national.ensure_setup() == []
    assert not any(isinstance(ext, NationalExtension) for ext in extensions.load(owner))
    NationalExtension(owner).on_week(2, WeekReport(season=1, week=2))
    assert db.scalar(select(func.count()).select_from(Nation)) == 0
    assert national.nations() == [] and national.job_offers() == [] and national.my_nation() is None
    assert national.world_cup_status() == DISABLED_TEXT and national.fixtures() == [] and national.bracket() == []
    assert not national.close_season_pending() and national.new_season_blocker() is None
    with pytest.raises(NationalTeamError, match="kapalı"):
        national.accept_job(1)


def test_worlds_without_four_eligible_nations_have_no_world_cup(db, shared, monkeypatch):
    monkeypatch.setattr(national_rules, "MIN_NATIONAL_PLAYERS", 61)        # her ulkenin 60 A takim oyuncusu var
    owner, _member = _managers(db, shared)
    national = NationalTeams(owner)
    assert national.ensure_setup() == []
    assert db.scalar(select(func.count()).select_from(InternationalTournament)) == 0
    status = national.world_cup_status()
    assert status.startswith("Dünya Kupası yok") and "0 ülkenin 61" in status
    _finish_club_season(db, owner)
    assert not national.close_season_pending() and national.new_season_blocker() is None
    assert owner.start_new_season() == 2


# ===========================================================================
# 2) Kadro cagrisi ve ilk 11
# ===========================================================================

def test_ai_callups_are_nationality_correct_and_manager_callups_are_validated(db, shared):
    owner, member = _managers(db, shared)
    best_turk = db.scalar(
        select(Player).join(Team, Team.id == Player.team_id).join(League, League.id == Team.league_id)
        .where(League.country == "Türkiye", Player.in_academy.is_(False))
        .order_by(Player.overall_rating.desc(), Player.id).limit(1))
    best_turk.injured_until_week = 9                                       # sakat: AI kadrosuna alinmaz
    db.flush()
    national = NationalTeams(owner)
    national.ensure_setup()

    rows = db.execute(select(NationalCallup.player_id, Nation.name)
                      .join(Nation, Nation.id == NationalCallup.nation_id)).all()
    per_nation: dict[str, list[int]] = {}
    for player_id, nation_name in rows:
        per_nation.setdefault(nation_name, []).append(player_id)
        assert _nation_of_player(db, player_id) == nation_name
    assert set(per_nation) == LEAGUE_COUNTRIES
    assert all(len(ids) == national_rules.DEFAULT_CALLUPS <= national_rules.MAX_CALLUPS for ids in per_nation.values())
    assert len({pid for pid, _ in rows}) == len(rows) and best_turk.id not in per_nation["Türkiye"]
    for ids in per_nation.values():
        keepers = db.scalar(select(func.count()).select_from(Player).where(Player.id.in_(ids),
                                                                           Player.position == Position.GK))
        assert keepers == 3

    member_national = NationalTeams(member)
    offer = member_national.job_offers()[0]
    member_national.accept_job(offer.id)
    candidates = member_national.candidates()
    assert len(candidates) == 60 and all(_nation_of_player(db, c.player_id) == offer.nation_name for c in candidates)
    assert sum(1 for c in candidates if c.status != "NONE") == 23
    assert candidates[0].status != "NONE" and candidates[-1].status == "NONE"   # cagrilmislar once
    keepers = [c for c in candidates if c.position == "GK"]
    assert member_national.candidates(position="GK") == [c for c in candidates if c.position == "GK"]
    assert member_national.candidates(query=candidates[5].name.split()[-1].upper())
    ids = [c.player_id for c in candidates]
    foreign = next(pid for name, pids in per_nation.items() if name != offer.nation_name for pid in pids)

    before = [(r.player_id, r.status) for r in member_national.squad()]
    with pytest.raises(NationalTeamError, match="en fazla 30"):
        member_national.set_callups(ids[:31])
    with pytest.raises(NationalTeamError, match="oyuncusu olmayan"):
        member_national.set_callups(ids[:20] + [foreign])
    with pytest.raises(NationalTeamError, match="en az 16"):
        member_national.set_callups(ids[:10])
    with pytest.raises(NationalTeamError, match="birden fazla"):
        member_national.set_callups(ids[:20] + ids[:1])
    assert [(r.player_id, r.status) for r in member_national.squad()] == before

    outfield = [c.player_id for c in candidates if c.position != "GK"]
    chosen = [keepers[0].player_id] + outfield[:24]
    warnings = member_national.set_callups(chosen)
    assert any("1 kaleci" in w for w in warnings)
    squad = member_national.squad()
    assert sorted(r.player_id for r in squad) == sorted(chosen) and all(r.status == "BENCH" for r in squad)
    assert national.squad(offer.nation_id) == squad                          # herkes okuyabilir


def test_national_lineup_uses_national_availability_only(db, shared):
    _owner, _member, _national, member_national, _notes = _setup(db, shared)
    offer = member_national.job_offers()[0]
    member_national.accept_job(offer.id)
    nation = db.get(Nation, offer.nation_id)
    formation = nation.formation

    squad = member_national.squad()
    check = member_national.set_lineup({squad[0].player_id: Position.GK}, [])
    assert not check.ok and all(r.status == "BENCH" for r in member_national.squad())

    member_national.auto_lineup()
    squad = member_national.squad()
    xi = {r.player_id: Position(r.role) for r in squad if r.status == "XI"}
    bench = [r.player_id for r in squad if r.status == "BENCH"]
    assert len(xi) == 11 and 0 < len(bench) <= 7 and len(squad) == 23
    assert [r.status for r in squad] == sorted((r.status for r in squad), key=["XI", "BENCH", "OUT"].index)

    starter = db.get(Player, next(iter(xi)))
    starter.suspended_matches, starter.cup_suspended_matches = 3, 2          # kulup cezasi milli macta gecmez
    db.flush()
    assert member_national.set_lineup(xi, bench).ok
    starter.injured_until_week = 9                                          # kulup sakatligi gecer
    db.flush()
    check = member_national.set_lineup(xi, bench)
    assert not check.ok and any("sakat" in e for e in check.errors)
    assert next(r for r in member_national.squad() if r.player_id == starter.id).available is False

    check = member_national.set_formation("4-3-3")
    assert db.get(Nation, nation.id).formation == "4-3-3" and isinstance(check.errors, list)
    with pytest.raises(NationalTeamError, match="Bilinmeyen diziliş"):
        member_national.set_formation("5-3-2")

    row = db.execute(select(*[getattr(Player, f) for f in ("id", "name", "position", "age", "overall_rating",
                                                           "pace", "shooting", "passing", "defending", "dribbling",
                                                           "goalkeeping", "form", "morale", "condition",
                                                           "fm_attributes", "injured_until_week", "team_id")])
                     .where(Player.id == starter.id)).one()
    proxy = NationalPlayer(row, LineupStatus.XI, Position.MID)
    with pytest.raises(AttributeError):
        proxy.suspended_matches = 0                                         # kulup satirina yazilamaz
    assert proxy.unavailability_reason(1).startswith("sakat") and not proxy.is_available(1)
    team = build_match_team(NationalSquad(nation.id, nation.name, nation.reputation, "4-4-2", [proxy]), True, 1,
                            lambda p: None)
    assert team.id == nation.id and team.preferred_xi == {starter.id: Position.MID}

    # Milli ceza: ulusun son milli macinda kirmizi kart -> bir sonraki milli macta oynayamaz (kulup satiri degil)
    starter.injured_until_week = 0
    member_national.set_formation(formation)
    red = next(pid for pid in xi if pid != starter.id)
    qualifier = _tournament(db, 1, "QUALIFIER")
    opponent = next(n.id for n in db.scalars(select(Nation).order_by(Nation.id)) if n.id != nation.id)
    played = InternationalFixture(tournament_id=qualifier.id, season=1, stage="GROUP", leg=9, group_index=0, week=2,
                                  home_nation_id=nation.id, away_nation_id=opponent, status="played", home_score=0,
                                  away_score=1, key_events=[{"type": "RED_CARD", "team_id": nation.id,
                                                             "player_id": red}])
    db.add(played)
    db.flush()
    row = next(r for r in member_national.squad() if r.player_id == red)
    assert not row.available and "cezalı" in row.reason and db.get(Player, red).suspended_matches == 0
    check = member_national.set_lineup(xi, bench)
    assert not check.ok and any("cezalı" in e for e in check.errors)
    served = InternationalFixture(tournament_id=qualifier.id, season=1, stage="GROUP", leg=10, group_index=0, week=4,
                                  home_nation_id=opponent, away_nation_id=nation.id, status="played", home_score=1,
                                  away_score=1, key_events=[])
    db.add(served)
    db.flush()
    assert next(r for r in member_national.squad() if r.player_id == red).available   # ceza bir macta cekildi
    assert member_national.set_lineup(xi, bench).ok


# ===========================================================================
# 3) Is teklifleri
# ===========================================================================

def test_one_national_job_per_manager_accept_decline_and_resign(db, shared):
    owner, _member, national, member_national, _notes = _setup(db, shared)
    member_offers, owner_offers = member_national.job_offers(), national.job_offers()
    assert len(member_offers) == len(owner_offers) == 3
    assert all(o.seasons == 2 and o.expires_in_weeks == 6 for o in member_offers)
    ranks = {v.id: v.rank for v in national.nations()}
    assert sorted(ranks[o.nation_id] for o in member_offers) == [4, 5, 6]     # seviye 1: alt yari
    member_seat = _seat_row(db, shared, MEMBER)
    assert sum("menajerlik teklif etti" in t for t in _notifications(db, member_seat.id)) == 3

    first = member_offers[0]
    view = member_national.accept_job(first.id)
    assert (view.id, view.manager_name, view.ai_managed, view.contract_until_season) == (first.nation_id, MEMBER,
                                                                                        False, 2)
    assert member_national.my_nation() == view and member_national.job_offers() == []
    assert (member_seat.nation_id, member_seat.national_until_season) == (first.nation_id, 2)
    event = db.scalars(select(WorldEvent).where(WorldEvent.kind == "NATIONAL").order_by(WorldEvent.id.desc())).first()
    assert event.payload["action"] == "ACCEPT" and event.actor_manager_id == member_seat.id

    same = next(o for o in owner_offers if o.nation_id == first.nation_id)
    with pytest.raises(NationalTeamError, match="artık geçerli değil"):
        national.accept_job(same.id)
    owner_seat = _seat_row(db, shared, OWNER)
    assert any("geri çekti" in t for t in _notifications(db, owner_seat.id))
    other = next(o for o in owner_offers if o.nation_id != first.nation_id)
    with pytest.raises(NationalTeamError, match="bulunamadı"):
        member_national.accept_job(other.id)                                # baskasinin teklifi

    extra = NationalJobOffer(nation_id=other.nation_id, manager_id=member_seat.id, season=1, status="PENDING",
                             expires_career_week=20)
    db.add(extra)
    db.flush()
    with pytest.raises(NationalTeamError, match="tek milli takımı"):
        member_national.accept_job(extra.id)                                # menajer basina tek gorev

    national.decline_job(other.id)
    assert db.get(NationalJobOffer, other.id).status == "DECLINED"
    national._offer_jobs()
    assert other.nation_id not in {o.nation_id for o in national.job_offers()}   # ayni sezon yeniden gelmez

    stale = next(o for o in national.job_offers())
    db.get(NationalJobOffer, stale.id).expires_career_week = owner.career_week - 1
    db.flush()
    with pytest.raises(NationalTeamError, match="süresi doldu"):
        national.accept_job(stale.id)

    member_national.resign()
    nation = db.get(Nation, first.nation_id)
    assert nation.manager_id is None and nation.ai_managed and nation.contract_until_season is None
    assert member_seat.nation_id is None and member_national.my_nation() is None
    with pytest.raises(NationalTeamError, match="görevin yok"):
        member_national.resign()
    spectator = NationalTeams(CareerManager(db, manager_user_id=987_654_321))
    assert spectator.job_offers() == [] and spectator.my_nation() is None
    with pytest.raises(NationalTeamError, match="koltuğun olmalı"):
        spectator.accept_job(first.id)


# ===========================================================================
# 4) Mac gunu: sonuclar, tablolar ve kulup satirlari
# ===========================================================================

def test_qualifier_matchday_stores_results_and_changes_only_caps_and_goals(db, shared):
    owner, _member, _national, member_national, _notes = _setup(db, shared)
    offer = member_national.job_offers()[0]
    member_national.accept_job(offer.id)
    member_national.auto_lineup()
    squad = member_national.squad()
    injured_id = next(r.player_id for r in squad if r.status == "XI" and r.position != "GK")
    suspended_id = next(r.player_id for r in squad if r.status == "XI" and r.player_id != injured_id)
    tired_id = next(r.player_id for r in squad if r.status == "BENCH")
    owner.state.current_week = 2
    db.get(Player, injured_id).injured_until_week = 6
    suspended = db.get(Player, suspended_id)
    suspended.suspended_matches, suspended.cup_suspended_matches, suspended.season_yellow_cards = 2, 1, 7
    db.get(Player, tired_id).condition = 41
    db.flush()

    players_before = _table_rows(db, "players", CAPS)
    teams_before, stats_before = _table_rows(db, "teams"), db.scalar(select(func.count()).select_from(PlayerMatchStat))
    report = WeekReport(season=1, week=2, results=[])
    NationalExtension(owner).on_week(2, report)
    db.flush()

    qualifier = _tournament(db, 1, "QUALIFIER")
    fixtures = _fixtures(db, qualifier)
    played = [fx for fx in fixtures if fx.status == "played"]
    assert len(played) == 4 and {fx.week for fx in played} == {2} and {fx.leg for fx in played} == {1, 2}
    assert all(fx.key_events and fx.key_events[-1]["type"] == "FULL_TIME" for fx in played)
    assert all(fx.home_penalties is None and not fx.extra_time for fx in played)
    entries = db.scalars(select(InternationalEntry).where(InternationalEntry.tournament_id == qualifier.id)).all()
    assert sum(e.played for e in entries) == 8
    assert sum(e.goals_for for e in entries) == sum(fx.home_score + fx.away_score for fx in played)
    assert all(e.points == 3 * e.won + e.drawn for e in entries)

    # Kulup satirlari: milli mac ve gol disinda HICBIR alan degismedi (kondisyon, sakatlik, ceza, form, moral...)
    assert _table_rows(db, "players", CAPS) == players_before
    assert _table_rows(db, "teams") == teams_before
    assert db.scalar(select(func.count()).select_from(PlayerMatchStat)) == stats_before
    caps = dict(db.execute(select(Player.id, Player.international_caps).where(Player.international_caps > 0)).all())
    goals = db.scalar(select(func.sum(Player.international_goals)))
    assert 4 * 2 * 11 <= sum(caps.values()) <= 4 * 2 * 16 and max(caps.values()) <= 2
    assert goals == sum(fx.home_score + fx.away_score for fx in played)
    member_played = [fx for fx in played if offer.nation_id in (fx.home_nation_id, fx.away_nation_id)]
    assert injured_id not in caps                                           # sakat oynamaz
    if member_played:
        assert caps.get(suspended_id, 0) == len(member_played)              # kulup cezasi milli macta yok
        assert any("Tanınırlık" in t for t in _notifications(db, _seat_row(db, shared, MEMBER).id))

    assert [n.split(":")[0] for n in report.honours_notes] == ["Dünya Kupası Elemeleri · 1. maç günü",
                                                                "Dünya Kupası Elemeleri · 2. maç günü"]
    assert "(2/6 maç günü oynandı)" in member_national.world_cup_status()
    views = member_national.fixtures("QUALIFIER")
    assert len(views) == 12 and sum(v.score is not None for v in views) == 4 and views[0].week_label == "2. hafta"

    NationalExtension(owner).on_week(2, WeekReport(season=1, week=2))      # ayni hafta tekrar: oynanmis gun yok
    assert sum(1 for fx in _fixtures(db, qualifier) if fx.status == "played") == 4


# ===========================================================================
# 5) Sezon arasi: Dunya Kupasi, yeni sezon engeli, sampiyon arsivi
# ===========================================================================

def test_world_cup_blocks_new_season_until_finished_and_archives_champion(db, shared):
    import world_manager

    owner, _member, national, _member_national, _notes = _setup(db, shared)
    _finish_club_season(db, owner)
    assert national.close_season_pending() and world_manager._close_season_hook(owner) is not None
    with pytest.raises(SeasonNotFinished, match="Dünya Kupası sürüyor") as blocked:
        owner.start_new_season()
    assert "kalan 10 maç günü" in str(blocked.value) and owner.season == 1

    reports = _play_out_close_season(db, owner, national)
    assert len(reports) == 6 + 4                                             # 6 yetisme eleme gunu + 4 Dunya Kupasi
    assert all(cup_report_lines(r) for r in reports)
    assert cup_report_lines(reports[6])[0][1].startswith("⭐ Dünya Kupası · Grup Aşaması 1. maç")
    qualifier, wc = _tournament(db, 1, "QUALIFIER"), _tournament(db, 1, "WORLD_CUP")
    assert qualifier.status == "FINISHED" and wc.status == "FINISHED" and wc.champion_nation_id is not None
    q_entries = db.scalars(select(InternationalEntry).where(InternationalEntry.tournament_id == qualifier.id)).all()
    assert sorted(e.eliminated_stage or "-" for e in q_entries) == ["-", "-", "-", "-", "QUAL", "QUAL"]
    wc_fixtures = _fixtures(db, wc)
    assert [fx.stage for fx in wc_fixtures] == ["GROUP"] * 6 + ["FINAL"] and all(fx.neutral for fx in wc_fixtures)
    final = wc_fixtures[-1]
    winner = final.home_nation_id if (final.home_penalties, final.home_score) > (final.away_penalties, final.away_score) \
        else final.away_nation_id
    assert winner == wc.champion_nation_id and final.close_season_day == 4
    wc_entries = db.scalars(select(InternationalEntry).where(InternationalEntry.tournament_id == wc.id)).all()
    assert sorted(e.eliminated_stage or "-" for e in wc_entries) == ["-", "FINAL", "GROUP", "GROUP"]
    champion = db.get(Nation, wc.champion_nation_id).name
    assert national.champions() == [(1, champion)] and national.champion_name() == champion
    assert national.world_cup_status() == f"Dünya Kupası şampiyonu: {champion}"
    assert db.scalar(select(func.count()).select_from(NewsItem).where(NewsItem.kind == "WORLD_CUP")) == 1
    assert db.scalars(select(WorldEvent.payload).where(WorldEvent.kind == "NATIONAL")
                      .order_by(WorldEvent.id)).all()[-1]["action"] == "CHAMPION"
    assert not national.close_season_pending() and national.new_season_blocker() is None
    assert world_manager._close_season_hook(owner) is None
    assert national.play_close_season_matchday(WeekReport(season=1, week=1)) is False

    assert owner.start_new_season() == 2
    team_id = owner.state.user_team_id
    assert any(n.startswith("Milli takım teklifi:") for n in owner.new_season_notes)
    assert owner.new_season_notes == owner.new_season_notes_by_team[team_id]
    assert len(national.job_offers()) == 3 and all(o.expires_in_weeks == 6 for o in national.job_offers())
    assert db.scalar(select(func.count()).select_from(NationalJobOffer)
                     .where(NationalJobOffer.season == 1, NationalJobOffer.status == "PENDING")) == 0
    assert _tournament(db, 2, "QUALIFIER") is not None and _tournament(db, 2, "WORLD_CUP").status == "DRAW"
    counts = db.execute(select(NationalCallup.nation_id, func.count()).where(NationalCallup.season == 2)
                        .group_by(NationalCallup.nation_id)).all()
    assert len(counts) == 6 and {c for _nid, c in counts} == {23}
    assert NationalTeams(owner).champions() == [(1, champion)]              # arsiv yeni sezonda da duruyor


def test_campaign_review_sacks_underperformers_and_contracts_expire(db, shared):
    owner, _member, national, member_national, _notes = _setup(db, shared, owner_reputation=16.0)
    views = {v.rank: v for v in national.nations()}
    top, weakest = views[1], views[6]
    national.accept_job(next(o.id for o in national.job_offers() if o.nation_id == top.id))
    member_national.accept_job(next(o.id for o in member_national.job_offers() if o.nation_id == weakest.id))
    member_seat, owner_seat = _seat_row(db, shared, MEMBER), _seat_row(db, shared, OWNER)
    db.get(Nation, weakest.id).contract_until_season = 1                    # sozlesme bu sezon sonunda biter
    member_seat.national_until_season = 1

    _finish_club_season(db, owner)
    qualifier = _tournament(db, 1, "QUALIFIER")
    entries = {e.nation_id: e for e in db.scalars(select(InternationalEntry)
                                                   .where(InternationalEntry.tournament_id == qualifier.id))}
    from match_engine import update_standings

    for fx in _fixtures(db, qualifier):                                      # en guclu ulus tum eleme maclarini kaybeder
        if top.id in (fx.home_nation_id, fx.away_nation_id):
            fx.home_score, fx.away_score = (0, 3) if fx.home_nation_id == top.id else (3, 0)
        else:
            fx.home_score, fx.away_score = 1, 1
        fx.status = "played"
        update_standings(entries[fx.home_nation_id], fx.home_score, fx.away_score)
        update_standings(entries[fx.away_nation_id], fx.away_score, fx.home_score)
    db.flush()
    _play_out_close_season(db, owner, national)
    assert entries[top.id].eliminated_stage == "QUAL"

    assert owner.start_new_season() == 2
    top_nation, weak_nation = db.get(Nation, top.id), db.get(Nation, weakest.id)
    assert top_nation.manager_id is None and top_nation.ai_managed and owner_seat.nation_id is None
    assert any("görevine son verdi" in t for t in _notifications(db, owner_seat.id))
    assert weak_nation.manager_id is None and member_seat.nation_id is None and member_seat.national_until_season is None
    member_texts = _notifications(db, member_seat.id)
    assert any("sözleşmen sona erdi" in t for t in member_texts)
    assert not any("görevine son verdi" in t for t in member_texts)          # beklentisi elemelerdi: gorevden alinmaz
    actions = {(e.payload["action"], e.payload["nation_id"]) for e in db.scalars(
        select(WorldEvent).where(WorldEvent.kind == "NATIONAL"))}
    assert {("SACKED", top.id), ("CONTRACT_END", weakest.id)} <= actions
    assert top.id not in {o.nation_id for o in national.job_offers()}        # gorevden alindigi ulustan teklif yok
    assert national.job_offers() and all(o.nation_id != top.id for o in national.job_offers())
    assert member_national.job_offers()                                     # sozlesmesi biten yeniden aday


# ===========================================================================
# 6) Gorunumler: gruplar ve agac
# ===========================================================================

def test_group_tables_bracket_and_fixture_views_have_ui_shapes(db, shared):
    owner, _member, national, member_national, _notes = _setup(db, shared)
    offer = member_national.job_offers()[0]
    member_national.accept_job(offer.id)

    tables = member_national.group_tables("QUALIFIER")
    assert [title for title, _rows in tables] == ["Grup A", "Grup B"] and all(len(rows) == 3 for _t, rows in tables)
    fields = set(GroupRowView.__dataclass_fields__)
    for _title, rows in tables:
        assert all(set(row) == fields for row in rows) and [r["position"] for r in rows] == [1, 2, 3]
    assert sum(r["highlight"] for _t, rows in tables for r in rows) == 1
    assert sum(r["qualified"] for _t, rows in tables for r in rows) == 4
    html = group_tables_html([(title, [GroupRowView(**row) for row in rows]) for title, rows in tables])
    assert "Grup A" in html and offer.nation_name in html
    assert national.group_tables("WORLD_CUP") == [] and national.group_tables("BOGUS") == []

    rounds = national.bracket()
    assert rounds == [RoundView("Final", [TieView(home=None, away=None)])]
    assert "?" in bracket_html(rounds) or "cm-b-tbd" in bracket_html(rounds)

    _finish_club_season(db, owner)
    reports = []
    while national.close_season_pending():
        report = WeekReport(season=1, week=owner.current_week)
        national.play_close_season_matchday(report)
        reports.append(report)
        wc = _tournament(db, 1, "WORLD_CUP")
        if wc.status == "RUNNING" and not any(fx.stage == "FINAL" for fx in _fixtures(db, wc)):
            wc_tables = national.group_tables("WORLD_CUP")
            assert len(wc_tables) == 1 and len(wc_tables[0][1]) == 4
            assert sum(r["qualified"] for r in wc_tables[0][1]) == 2
    rounds = national.bracket()
    assert len(rounds) == 1 and rounds[0].label == "Final" and len(rounds[0].ties) == 1
    tie = rounds[0].ties[0]
    champion = national.champion_name()
    assert champion in (tie.home, tie.away) and tie.winner in ("home", "away")
    assert (tie.home if tie.winner == "home" else tie.away) == champion and len(tie.legs) == 1
    assert champion in bracket_html(rounds, champion)

    wc_views = national.fixtures("WORLD_CUP")
    assert [v.stage_label for v in wc_views[:2]] == ["Grup A · 1. maç"] * 2 and wc_views[-1].stage_label == "Final"
    assert wc_views[-1].week_label == "Sezon arası 4. gün" and all(v.score for v in wc_views)
    assert len(national.fixtures()) == 12 + 7
    nation_views = {v.name: v for v in national.nations()}
    assert nation_views[champion].stage_label == "Dünya Kupası şampiyonu" and nation_views[champion].qualified
    assert sum(v.qualified is True for v in nation_views.values()) == 4
    assert sum(v.stage_label == "Elemelerde elendi" for v in nation_views.values()) == 2


# ===========================================================================
# 7) Dunya ilerlemesi (world_manager.try_advance): sezon arasi milli mac gunleri. Paylasilan dunyaya COMMIT eder;
#    bu yuzden islemi geri alinan testlerden sonra, dunyayi yeniden kuran parite testinden once calisir.
# ===========================================================================

def test_world_advance_plays_close_season_matchdays_before_the_new_season(shared):
    import world_manager as wm
    from models import GameState, ManagerWeekReport
    from turn_rules import AdvanceTrigger
    from worlds import WorldContext

    ctx = WorldContext(world_id=shared.world_id, schema="public", name="Test Dünyası", kind="SHARED", role="OWNER",
                       user_id=shared.owner_id, world_seed=None)

    def read(fn):
        with database.career_context("public"), database.session_scope() as session:
            return fn(session)

    with database.career_context("public"), database.session_scope() as session:
        state = session.get(GameState, 1)
        state.world_rules = _intl_rules(max_missed_deadlines=20).to_dict()   # zorlanan turlarda kulup kaybedilmesin
    kinds, phases = [], []
    for _ in range(40):
        expected = read(lambda s: (s.get(GameState, 1).season, s.get(GameState, 1).current_week))
        phases.append(read(lambda s: wm.WorldController(s, ctx).turn_status().phase))
        result = wm.try_advance(ctx, AdvanceTrigger.FORCED, expected=expected)
        assert result.advanced, result.message
        kinds.append(result.kind)
        if result.kind == "NEW_SEASON":
            break
    close = [i for i, kind in enumerate(kinds) if kind == "CLOSE_SEASON_MATCHDAY"]
    assert len(close) == 4 and kinds[-1] == "NEW_SEASON" and set(kinds[:close[0]]) == {"WEEK"}
    assert [phases[i] for i in close] == ["CLOSE_SEASON"] * 4 and phases[-1] == "SEASON_END"

    def check(session):
        wc = _tournament(session, 1, "WORLD_CUP")
        assert wc.status == "FINISHED" and wc.champion_nation_id is not None
        assert session.scalar(select(func.sum(Player.international_caps))) > 0
        rows = session.scalars(select(ManagerWeekReport).where(ManagerWeekReport.midweek.is_(True))
                               .order_by(ManagerWeekReport.id)).all()
        assert rows and any(line[1].startswith("⭐ Dünya Kupası") for row in rows for line in row.cup_lines)
        season_two = session.get(GameState, 1)
        assert (season_two.season, season_two.current_week) == (2, 1)
        assert _tournament(session, 2, "WORLD_CUP") is not None

    read(check)


# ===========================================================================
# 8) RNG / lig paritesi: milli maclar acik ya da kapali, ayni tohumla ayni lig
# ===========================================================================

PARITY_SEED = 9031
PARITY_TABLES = ("leagues", "teams", "players", "staff", "fixtures", "player_match_stats", "tournaments",
                 "tournament_entries", "cup_ties", "transfer_log", "season_honours", "season_standings",
                 "friendlies", "shortlist")


def _parity_run(internationals: bool) -> dict:
    """Temiz dunyada tam sezon + sezon arasi + yeni sezonun ilk haftasi; islem GERI ALINIR. gc kapali (A2 paritesi)."""
    from seats import SeatStore

    _fresh_world(mode=None)
    db = database.SessionLocal()
    gc.collect()
    gc.disable()
    try:
        cm = CareerManager(db, seed=PARITY_SEED)
        cm.state.world_rules = WorldRules(internationals=internationals).to_dict()
        cm.set_game_mode(GameMode.CAREER)
        cm.ensure_youth_setup()
        cm.ensure_club_setup()
        cm.set_user_team(cm.find_team(OWNER_TEAM))
        SeatStore(db).ensure_primary_row(None, "Sahip")
        national = NationalTeams(cm)
        if internationals:
            assert national.ensure_setup()
            national.accept_job(national.job_offers()[0].id)            # birincil menajer milli takimi da yonetir
        weeks = 0
        while not cm.season_finished:
            cm.play_week()
            weeks += 1
            assert weeks < 30
        close_days = len(_play_out_close_season(db, cm, national))
        cm.start_new_season()
        cm.play_week()
        db.flush()
        tables = {name: _table_rows(db, name, CAPS if name == "players" else ()) for name in PARITY_TABLES}
        return {
            "tables": tables,
            "rng": cm.rng.getstate(),
            "weeks": weeks,
            "close_days": close_days,
            "intl_played": db.scalar(select(func.count()).select_from(InternationalFixture)
                                     .where(InternationalFixture.status == "played")),
            "caps": db.scalar(select(func.coalesce(func.sum(Player.international_caps), 0))),
        }
    finally:
        gc.enable()
        db.rollback()
        db.close()


def test_league_results_are_identical_with_internationals_on_and_off():
    try:
        on = _parity_run(internationals=True)
        off = _parity_run(internationals=False)
    finally:
        _fresh_world(mode=None)                                             # conftest ile ayni temiz dunya
    assert on["intl_played"] >= 12 + 7 and on["caps"] > 0 and on["close_days"] == 4
    assert off["intl_played"] == 0 and off["caps"] == 0 and off["close_days"] == 0
    assert on["weeks"] == off["weeks"]
    assert on["rng"] == off["rng"]                                          # cm.rng milli maclardan etkilenmedi
    for name in PARITY_TABLES:
        assert on["tables"][name] == off["tables"][name], name
