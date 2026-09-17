"""
Faz 12 / 14. Asama temel paketi (WP0) testleri: paylasilan dunya semasi, eski kayit yukseltmesi, kayipsiz
hazirlik maci kisiti gevsetmesi, dunya tur kilidi (advisory lock), web oturum dekoratorleri, eklenti yukleyici
ve web_app'in eski adlari / kosullu dunya sekmeleri.

Kilit testleri gercek PostgreSQL baglantilariyla ve thread'lerle calisir; semalar gecicidir ve silinir. Web
dunya testi (AppTest) sentetik 'public' dunyasini degistirir ve sonunda yeniden kurar -- web testleriyle AYNI
grupta, sirayla calistirin.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import database  # noqa: E402
import models  # noqa: E402
from world_rules import WorldRules  # noqa: E402


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")

ACCOUNT_TABLES = ("worlds", "world_memberships", "manager_profiles")
WORLD_TABLES_12A = ("world_managers", "manager_week_reports", "manager_shortlist", "season_standings", "world_events")
WORLD_TABLES_12B = ("transfer_offers", "loans", "manager_messages", "world_posts", "notifications", "fair_play_log")
WORLD_TABLES_12C = ("nations", "national_callups", "international_tournaments", "international_entries",
                    "international_fixtures", "national_job_offers")
WORLD_TABLES = WORLD_TABLES_12A + WORLD_TABLES_12B + WORLD_TABLES_12C
PHASE12_COLUMNS = (
    ("game_state", "world_rules"), ("game_state", "turn_opened_at"), ("game_state", "turn_deadline_at"),
    ("game_state", "last_advance_at"), ("game_state", "last_advance_trigger"), ("game_state", "last_advance_by"),
    ("teams", "ai_protected_until"),
    ("players", "loan_id"), ("players", "loan_from_team_id"), ("players", "loan_wage_share"),
    ("players", "transfer_listed"), ("players", "loan_listed"),
    ("players", "international_caps"), ("players", "international_goals"),
)
LOCK_SCHEMA = "test_wp0_lock"
OLD_SAVE_SCHEMA = "test_wp0_old_save"
RELAX_SCHEMA = "test_wp0_relax"


# ===========================================================================
# 1) MODELLER (saf)
# ===========================================================================

def test_models_define_every_phase12_table_constraint_and_additive_column():
    tables = database.Base.metadata.tables
    for name in ACCOUNT_TABLES:
        assert f"accounts.{name}" in tables, name
    for name in WORLD_TABLES:
        assert name in tables and tables[name].schema is None, name

    additive = {(t, c): ddl for t, c, ddl in database.ADDITIVE_COLUMNS}
    for table, column in PHASE12_COLUMNS:
        col = tables[table].c[column]
        assert (table, column) in additive, (table, column)
        assert col.nullable or "DEFAULT" in additive[(table, column)], (table, column)   # eski satirlar icin guvenli

    manager_checks = {c.name for c in tables["world_managers"].constraints if c.name}
    assert {"ck_world_manager_user", "ck_world_manager_primary_fields"} <= manager_checks
    partial = {i.name: i for i in tables["world_managers"].indexes}
    assert all(partial[n].unique and partial[n].dialect_options["postgresql"]["where"] is not None
               for n in ("uq_world_manager_primary", "uq_world_manager_user", "uq_world_manager_team"))
    offers = {i.name for i in tables["transfer_offers"].indexes if i.unique}
    assert {"uq_transfer_offer_open", "uq_transfer_offer_contract"} <= offers

    friendly = tables["friendlies"]
    assert "uq_friendly_week" not in {c.name for c in friendly.constraints}
    home_week = {i.name: i for i in friendly.indexes}["uq_friendly_home_week"]
    assert home_week.unique and [c.name for c in home_week.columns] == ["season", "week", "home_team_id"]
    assert ("friendlies", "uq_friendly_week", "uq_friendly_home_week") in database.RELAXED_CONSTRAINTS
    assert isinstance(database.SCHEMA_VERSION, int) and database.SCHEMA_VERSION > 0
    assert database.LOCK_WORLD_TURN == 10_003


def test_plain_text_kinds_match_rule_enums():
    import fair_play
    import market_rules

    assert {s.value for s in market_rules.OfferStatus} == set(models.OFFER_STATUSES)
    assert {k.value for k in market_rules.OfferKind} == set(models.OFFER_KINDS)
    assert {s.value for s in market_rules.OPEN_STATUSES} == set(models.OPEN_OFFER_STATUSES)
    assert dict(fair_play.FAIR_PLAY_DELTAS) == {"BLOCKED": -10.0, "DENIED": -15.0, "APPROVED": 2.0,
                                                "REVERSED": -25.0}


def test_stub_modules_import_and_expose_contracts():
    stubs = ("world_rules", "turn_rules", "seats", "worlds", "world_manager", "market_rules", "loan_rules",
             "fair_play", "market_hub", "messaging", "intl_calendar", "world_cup", "national_rules",
             "national_teams", "extensions", "web_common", "world_lobby_view", "world_panel_view",
             "world_admin_view", "market_view", "messages_view", "national_view")
    for name in stubs:
        importlib.import_module(name)
    import market_hub
    import national_rules
    import seats
    import world_manager
    import worlds
    from transfers import TransferError

    assert issubclass(market_hub.FairPlayBlocked, TransferError) and issubclass(world_manager.ClubUnavailableError,
                                                                                  worlds.WorldError)
    assert (national_rules.MIN_NATIONAL_PLAYERS, national_rules.MAX_CALLUPS) == (23, 30)
    # seats (Faz 12 A2) uygulandi: kurucu sorgu atmaz, hatalar ValueError
    assert seats.SeatStore(None).db is None and issubclass(seats.SeatError, ValueError)
    # worlds (Faz 12 A1) uygulandi: sozlesme hatalari WorldError (ValueError) alt siniflari
    assert all(issubclass(cls, worlds.WorldError) and issubclass(cls, ValueError) for cls in (
        worlds.WorldNotFound, worlds.WorldPermissionError, worlds.WorldFullError, worlds.NotAMemberError,
        worlds.LevelTooLowError))


def test_world_rules_legacy_is_empty_dict_and_round_trips():
    legacy = WorldRules.from_dict({})
    assert legacy == WorldRules.legacy() and legacy.validate() == []
    assert not (legacy.shared or legacy.human_market or legacy.internationals or legacy.auto_advance)
    assert legacy.max_seats == 1 and legacy.live_matches
    shared = WorldRules.shared_defaults()
    assert shared.validate() == [] and WorldRules.from_dict(shared.to_dict()) == shared
    tolerant = WorldRules.from_dict({"shared": True, "max_seats": "abc", "win_points": 99, "unknown": 1})
    assert tolerant.shared and tolerant.max_seats == 1 and tolerant.win_points == 3
    assert WorldRules.from_dict("bozuk") == WorldRules.legacy()


def test_loan_wage_split_never_loses_money():
    from loan_rules import wage_split

    for wage in (0, 1, 999, 12_345, 250_000):
        for share in (0, 1, 33, 50, 99, 100, 150, -5):
            borrower, parent = wage_split(wage, share)
            assert borrower + parent == wage and borrower >= 0 and parent >= 0


# ===========================================================================
# 2) EKLENTI YUKLEYICI (saf)
# ===========================================================================

def test_extension_loader_is_empty_for_legacy_rules_and_stub_modules(monkeypatch):
    import extensions

    legacy_cm = SimpleNamespace(state=SimpleNamespace(world_rules={}))
    assert extensions.load(legacy_cm) == []
    everything = WorldRules(shared=True, max_seats=4, human_market=True, loans=True, internationals=True)
    loaded = [type(e).__name__ for e in extensions.load(SimpleNamespace(rules=everything))]
    assert "MarketExtension" in loaded                                      # B2 doldu; milli takim C2 ile gelir
    assert set(loaded) <= {"MarketExtension", "NationalExtension"}

    class FakeMarket:
        def __init__(self, cm):
            self.cm = cm

    monkeypatch.setitem(sys.modules, "market_hub", SimpleNamespace(MarketExtension=FakeMarket))
    cm = SimpleNamespace(state=SimpleNamespace(world_rules=everything.to_dict()))
    loaded = extensions.load(cm)
    assert len(loaded) == 1 and isinstance(loaded[0], FakeMarket) and loaded[0].cm is cm
    assert extensions.load(SimpleNamespace(rules=WorldRules(internationals=True))) == []   # pazar kapali


# ===========================================================================
# 3) WEB: adlar, sekmeler, callback kapilari
# ===========================================================================

def test_web_app_keeps_tested_names_and_world_tabs_are_conditional():
    import web_app

    for name in ("manager", "session_scope", "requires_auth", "session_career_schema", "NoCareerSession",
                 "PUBLIC_CALLBACKS", "flash", "show_flash", "reset_widgets", "parse_seed", "money",
                 "live_fixture_pending", "get_script_run_ctx", "login_screen"):
        assert hasattr(web_app, name), name
    assert web_app.CAREER_TABS == [web_app.TAB_LIVE, web_app.TAB_SQUAD, web_app.TAB_PREP, web_app.TAB_ACADEMY,
                                   web_app.TAB_FINANCE, web_app.TAB_CLUB, web_app.TAB_MARKET, web_app.TAB_LEAGUE,
                                   web_app.TAB_WORLD, web_app.TAB_ARENA, web_app.TAB_STAFF]
    for tab in web_app.WORLD_TABS:
        assert tab not in web_app.CAREER_TABS and tab not in web_app.TOURNAMENT_TABS

    names = web_app.world_tab_names
    assert names(WorldRules.legacy(), False, None) == []
    assert names(WorldRules.shared_defaults(), True, "MEMBER") == [web_app.TAB_HUB]
    assert names(WorldRules.shared_defaults(), True, "ADMIN") == [web_app.TAB_HUB, web_app.TAB_ADMIN]
    intl = WorldRules(shared=True, max_seats=4, internationals=True)
    assert names(intl, True, "OWNER") == [web_app.TAB_HUB, web_app.TAB_NATIONAL, web_app.TAB_ADMIN]


def test_every_callback_in_web_app_and_view_modules_requires_auth():
    import web_app

    callbacks = {name: getattr(web_app, name) for name in dir(web_app) if name.startswith("cb_")}
    assert len(callbacks) > 20
    for name, fn in callbacks.items():
        assert getattr(fn, "requires_auth", False) is (name not in web_app.PUBLIC_CALLBACKS), name

    view_modules = sorted({p.stem for pattern in ("*_view.py", "*_views.py") for p in ROOT.glob(pattern)})
    assert {"world_lobby_view", "world_panel_view", "world_admin_view", "market_view", "messages_view",
            "national_view"} <= set(view_modules)
    for module_name in view_modules:
        module = importlib.import_module(module_name)
        for name in dir(module):
            fn = getattr(module, name)
            if name.startswith("cb_") and callable(fn) and getattr(fn, "__module__", None) == module_name:
                assert getattr(fn, "requires_auth", False) is True, f"{module_name}.{name}"


@pytest.fixture()
def web_state(monkeypatch):
    import web_common

    state: dict = {}
    monkeypatch.setattr(web_common.st, "session_state", state)
    return state


def _membership(role: str):
    import worlds

    return worlds.MembershipInfo(world_id=5, user_id=7, username="x", role=role, status="ACTIVE",
                                 joined_at=None, last_seen_at=None, team_name=None)


def test_member_callback_legacy_session_needs_no_world_and_takes_no_lock(web_state, monkeypatch):
    import web_common
    import worlds
    from accounts import AuthSession

    def never(*_args, **_kwargs):
        raise AssertionError("eski oturumda uyelik sorgulanmamali")

    monkeypatch.setattr(worlds, "check_membership", never)
    monkeypatch.setattr(database, "world_lock", never)
    calls: list = []

    @web_common.member_callback
    def cb_demo(value):
        calls.append(value)
        return value

    assert cb_demo.requires_auth is True and web_common.admin_callback(cb_demo).requires_auth is True
    assert cb_demo(1) is None and calls == []                          # oturum yok
    web_state["auth"] = AuthSession(user_id=0, username="test_menajer", career_schema="public")
    assert cb_demo(2) == 2 and calls == [2]
    assert web_common.admin_callback(lambda: "ok")() == "ok"            # kendi kariyerinin sahibi


def test_member_callback_removed_member_is_sent_to_lobby(web_state, monkeypatch):
    import web_common
    import worlds
    from tests.world_helpers import world_auth

    def removed(user_id, world_id):
        raise worlds.NotAMemberError("uye degil")

    def no_default_world(session):                 # Faz 12 A4: once varsayilan dunya denenir; yoksa lobi
        raise worlds.NotAMemberError("uye degil")

    monkeypatch.setattr(worlds, "check_membership", removed)
    monkeypatch.setattr(worlds, "default_world", no_default_world)
    calls: list = []
    web_state["auth"] = world_auth(7, "atilan", "public", world_id=5, world_kind="SHARED")
    web_state["career_ready"] = "public"
    assert web_common.member_callback(lambda: calls.append(1))() is None
    assert calls == [] and web_state[web_common.LOBBY_KEY] is True and "career_ready" not in web_state
    assert web_state["flash"]["lobby"] == [("error", web_common.REMOVED_TEXT)]

    monkeypatch.setattr(worlds, "check_membership", lambda user_id, world_id: _membership("MEMBER"))
    web_state["auth"] = world_auth(7, "uye", "public", world_id=5, world_kind="PERSONAL")
    assert web_common.admin_callback(lambda: calls.append(2))() is None and calls == []
    assert web_state["flash"]["admin"] == [("error", web_common.ADMIN_ONLY_TEXT)]
    assert web_common.member_callback(lambda: calls.append(3))() is None and calls == [3]   # kisisel: kilit yok


# ===========================================================================
# 4) DUNYA TUR KILIDI (PostgreSQL advisory lock)
# ===========================================================================

def _own_world_locks(db_or_conn) -> list[tuple[str, bool]]:
    return [tuple(r) for r in db_or_conn.execute(text(
        "SELECT mode, granted FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
        "AND classid = :ns"), {"ns": database.LOCK_WORLD_TURN}).all()]


class _Holder:
    """Ayri ham baglantida islem seviyesi kilit tutar (release() ile rollback)."""

    def __init__(self, schema: str, function: str):
        self.started, self.released, self.error = threading.Event(), threading.Event(), None
        self.thread = threading.Thread(target=self._run, args=(schema, function), daemon=True)

    def _run(self, schema: str, function: str) -> None:
        try:
            with database.engine.connect() as conn:
                conn.execute(text(f"SELECT {function}(:ns, hashtext(:k))"), {"ns": database.LOCK_WORLD_TURN, "k": schema})
                self.started.set()
                self.released.wait(30)
                conn.rollback()
        except Exception as exc:                  # pragma: no cover - testte gorunsun
            self.error = exc
            self.started.set()

    def __enter__(self):
        self.thread.start()
        assert self.started.wait(10) and self.error is None, self.error
        return self

    def __exit__(self, *exc):
        self.released.set()
        self.thread.join(10)


def _locked_query(schema: str, mode: str, timeout_ms: int | None = None) -> list[tuple[str, bool]]:
    with database.career_context(schema), database.world_lock(schema, mode, timeout_ms), \
            database.session_scope() as db:
        return _own_world_locks(db)


@integration
@pytest.mark.integration
def test_shared_locks_proceed_together_and_are_released_at_commit():
    with _Holder(LOCK_SCHEMA, "pg_advisory_xact_lock_shared"):
        started = time.monotonic()
        assert _locked_query(LOCK_SCHEMA, "shared", 5_000) == [("ShareLock", True)]
        assert time.monotonic() - started < 3
        with database.career_context(LOCK_SCHEMA), database.world_lock(LOCK_SCHEMA, "shared", 1234), \
                database.session_scope() as db:
            assert db.scalar(text("SHOW lock_timeout")) == "0"          # yalnizca kilit beklemesi sinirlandi
    assert _locked_query(LOCK_SCHEMA, "try_exclusive") == [("ExclusiveLock", True)]   # hepsi commit'te dustu
    with database.career_context(LOCK_SCHEMA), database.session_scope() as db:
        assert _own_world_locks(db) == []                                # blok disinda kilit yok


@integration
@pytest.mark.integration
def test_exclusive_waits_behind_shared_and_try_exclusive_is_busy():
    outcome: dict = {}

    def advance() -> None:
        try:
            outcome["locks"] = _locked_query(LOCK_SCHEMA, "exclusive", 15_000)
        except Exception as exc:                  # pragma: no cover
            outcome["error"] = exc
        outcome["at"] = time.monotonic()

    with _Holder(LOCK_SCHEMA, "pg_advisory_xact_lock_shared") as holder:
        worker = threading.Thread(target=advance, daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        waiting = 0
        with database.engine.connect() as monitor:
            while waiting == 0 and time.monotonic() < deadline:
                waiting = monitor.execute(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = :ns AND NOT granted"
                ), {"ns": database.LOCK_WORLD_TURN}).scalar()
                monitor.rollback()
                time.sleep(0.05)
        assert waiting == 1 and "at" not in outcome                      # exclusive, shared'in arkasinda bekliyor

        with pytest.raises(database.WorldBusyError):
            _locked_query(LOCK_SCHEMA, "try_exclusive")
        released_at = time.monotonic()
        holder.released.set()
        worker.join(15)
    assert outcome.get("locks") == [("ExclusiveLock", True)] and outcome["at"] >= released_at


@integration
@pytest.mark.integration
def test_lock_timeout_surfaces_as_55P03_and_env_override(monkeypatch):
    with _Holder(LOCK_SCHEMA, "pg_advisory_xact_lock"):
        started = time.monotonic()
        with pytest.raises(OperationalError) as excinfo:
            _locked_query(LOCK_SCHEMA, "shared", 200)
        assert excinfo.value.orig.pgcode == database.LOCK_TIMEOUT_PGCODE == "55P03"
        assert time.monotonic() - started < 5

        monkeypatch.setenv(database.WORLD_LOCK_TIMEOUT_ENV, "150")
        assert database.world_lock_timeout_ms("shared") == 150 and database.world_lock_timeout_ms("exclusive") == 150
        with pytest.raises(OperationalError) as excinfo:
            _locked_query(LOCK_SCHEMA, "exclusive")
        assert excinfo.value.orig.pgcode == "55P03"
        # DDL baglantisi (career_connection) da ayni kilide tabidir
        with database.career_context(LOCK_SCHEMA), database.world_lock(LOCK_SCHEMA, "try_exclusive"):
            with pytest.raises(database.WorldBusyError), database.career_connection():
                pass
    monkeypatch.delenv(database.WORLD_LOCK_TIMEOUT_ENV)
    assert database.world_lock_timeout_ms("shared") == database.SHARED_LOCK_TIMEOUT_MS


@integration
@pytest.mark.integration
def test_accounts_scopes_never_take_the_world_lock_even_for_public():
    import accounts

    with _Holder(LOCK_SCHEMA, "pg_advisory_xact_lock"):                  # dunya mesgul: hesap islemi beklemez
        with database.career_context(LOCK_SCHEMA), database.world_lock(LOCK_SCHEMA, "exclusive", 200):
            with accounts._accounts_scope() as db:
                assert _own_world_locks(db) == []
                assert db.scalar(text("SELECT count(*) FROM accounts.users")) >= 0

    with database.world_lock("public", "exclusive"):                    # cozucu yok -> etkin kariyer 'public'
        with database.session_scope() as db:
            assert _own_world_locks(db) == [("ExclusiveLock", True)]
        with accounts._accounts_scope() as db:                           # 'public'e sabitlenen yeni baglam
            assert _own_world_locks(db) == []

    with pytest.raises(ValueError, match="etkin kariyer"):
        with database.world_lock(LOCK_SCHEMA, "shared"):                 # career_context disarida acilmali
            pass
    with pytest.raises(ValueError):
        with database.career_context(LOCK_SCHEMA), database.world_lock(LOCK_SCHEMA, "upgrade"):
            pass


@integration
@pytest.mark.integration
def test_member_callback_takes_shared_lock_and_reports_busy_world(web_state, monkeypatch):
    import web_common
    import worlds
    from tests.world_helpers import world_auth

    monkeypatch.setattr(worlds, "check_membership", lambda user_id, world_id: _membership("MEMBER"))
    web_state["auth"] = world_auth(7, "uye", LOCK_SCHEMA, world_id=5, world_kind="SHARED")
    seen: list = []

    @web_common.member_callback
    def cb_mutate():
        with database.session_scope() as db:
            seen.append(_own_world_locks(db))

    with database.career_context(LOCK_SCHEMA):                           # web'de sema cozucuden gelir
        cb_mutate()
        assert seen == [[("ShareLock", True)]]
        monkeypatch.setenv(database.WORLD_LOCK_TIMEOUT_ENV, "200")
        with _Holder(LOCK_SCHEMA, "pg_advisory_xact_lock"):
            assert cb_mutate() is None
    assert seen == [[("ShareLock", True)]]                                # mesgulken govde yazmadi
    assert web_state["flash"][web_common.BUSY_FLASH_AREA] == [("warning", web_common.BUSY_TEXT)]


# ===========================================================================
# 5) ESKI KAYIT YUKSELTMESI VE KAYIPSIZ GEVSETME
# ===========================================================================

def _simulate_pre_phase12(conn) -> None:
    for table in reversed(WORLD_TABLES):
        conn.exec_driver_sql(f'DROP TABLE "{table}" CASCADE')
    for table, column in PHASE12_COLUMNS:
        conn.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
    conn.exec_driver_sql('DROP INDEX "uq_friendly_home_week"')
    conn.exec_driver_sql('ALTER TABLE "friendlies" ADD CONSTRAINT "uq_friendly_week" UNIQUE (season, week)')


def _friendly_state(conn, schema: str) -> tuple[bool, bool]:
    old = conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = :s AND c.conname = 'uq_friendly_week')"), {"s": schema})
    new = conn.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = :s AND indexname = 'uq_friendly_home_week')"),
        {"s": schema})
    return bool(old), bool(new)


def _friendly(season: int, week: int, home, away, score: int = 1) -> models.Friendly:
    return models.Friendly(season=season, week=week, home_team_id=home.id, home_team_name=home.name,
                           away_team_id=away.id, away_team_name=away.name, home_score=score, away_score=0)


@integration
@pytest.mark.integration
def test_old_save_upgrades_twice_in_scratch_schema_and_keeps_its_data():
    import seed
    from career_manager import CareerManager

    schema = OLD_SAVE_SCHEMA
    with database.career_context(schema):
        database.drop_career_schema(schema)
        try:
            database.init_db()
            with database.session_scope() as db:
                seed.write_world(db, seed.build_synthetic_world(2026), rng_seed=2026)
                home, away = db.scalars(select(models.Team).order_by(models.Team.id).limit(2)).all()
                db.add(_friendly(1, 1, home, away, score=3))
            with database.career_connection() as conn:
                bills_before = conn.execute(text(
                    "SELECT team_id, sum(current_wage)::bigint FROM players WHERE team_id IS NOT NULL "
                    "GROUP BY team_id ORDER BY team_id")).all()
                _simulate_pre_phase12(conn)
                assert _friendly_state(conn, schema) == (True, False)

            problems = set(database.schema_problems())
            assert {f"eksik tablo: {t}" for t in WORLD_TABLES} <= problems
            assert {f"eksik sütun: {t}.{c}" for t, c in PHASE12_COLUMNS} <= problems

            applied = database.upgrade_schema()
            assert {f"tablo eklendi: {t}" for t in WORLD_TABLES} <= set(applied)
            assert {f"sütun eklendi: {t}.{c}" for t, c in PHASE12_COLUMNS} <= set(applied)
            assert {"indeks eklendi: friendlies.uq_friendly_home_week", "indeks eklendi: players.ix_player_loan_from",
                    "kısıt gevşetildi: friendlies.uq_friendly_week → uq_friendly_home_week"} <= set(applied)
            assert database.upgrade_schema() == [] and database.schema_problems() == []

            with database.career_connection() as conn:
                assert _friendly_state(conn, schema) == (False, True)
                assert conn.execute(text("SELECT season, week, home_score FROM friendlies")).all() == [(1, 1, 3)]
                assert conn.scalar(text("SELECT world_rules FROM game_state WHERE id = 1")) in ({}, None)

            session = database.SessionLocal()
            try:
                cm = CareerManager(session, seed=5)
                assert WorldRules.from_dict(cm.state.world_rules) == WorldRules.legacy()
                teams = session.scalars(select(models.Team).order_by(models.Team.id)).all()
                bills = {t.id: sum(p.current_wage for p in t.players) + sum(p.current_wage for p in t.academy_players)
                         for t in teams}
                assert {t.id: t.player_wage_bill for t in teams} == bills == dict(bills_before)
                cm.set_user_team(cm.find_team("Istanbul Lions"))
                assert cm.play_week().played_any                            # yukseltilmis kayit oynanir
                session.commit()
            finally:
                session.close()
        finally:
            database.drop_career_schema(schema)


@integration
@pytest.mark.integration
def test_friendly_relax_is_idempotent_and_allows_one_friendly_per_club_per_week():
    schema = RELAX_SCHEMA
    with database.career_context(schema):
        database.drop_career_schema(schema)
        try:
            database.init_db()
            with database.session_scope() as db:
                league = models.League(name="Gevşetme Ligi", country="Test")
                db.add(league)
                db.flush()
                a, b, c = (models.Team(league_id=league.id, name=n) for n in ("A Kulübü", "B Kulübü", "C Kulübü"))
                db.add_all([a, b, c])
                db.flush()
                db.add(_friendly(1, 1, a, b, score=2))
            with database.career_connection() as conn:
                conn.exec_driver_sql('DROP INDEX "uq_friendly_home_week"')
                conn.exec_driver_sql('ALTER TABLE "friendlies" ADD CONSTRAINT "uq_friendly_week" UNIQUE (season, week)')

            first = database.upgrade_schema()
            assert first == ["indeks eklendi: friendlies.uq_friendly_home_week",
                             "kısıt gevşetildi: friendlies.uq_friendly_week → uq_friendly_home_week"]
            assert database.upgrade_schema() == [] and database.upgrade_schema() == []

            with database.session_scope() as db:
                a, b, c = db.scalars(select(models.Team).order_by(models.Team.id)).all()
                assert [(f.home_team_name, f.home_score) for f in db.scalars(select(models.Friendly))] == \
                    [("A Kulübü", 2)]
                db.add(_friendly(1, 1, c, b))                              # ayni hafta, baska ev sahibi kulup: olur
                db.flush()
                with pytest.raises(IntegrityError, match="uq_friendly_home_week"), db.begin_nested():
                    db.add(_friendly(1, 1, a, c))                          # ayni kulup ayni hafta: olmaz
                    db.flush()
            with database.career_connection() as conn:
                assert conn.scalar(text("SELECT count(*) FROM friendlies")) == 2
        finally:
            database.drop_career_schema(schema)


# ===========================================================================
# 6) KIRALIK MAAS PAYLASIMI VE PAYLASILAN DUNYA YARDIMCISI (test veritabani, geri alinir / yeniden kurulur)
# ===========================================================================

@integration
@pytest.mark.integration
def test_loan_split_moves_wage_share_between_clubs_and_total_is_kept():
    session = database.SessionLocal()
    try:
        parent, borrower = session.scalars(select(models.Team).order_by(models.Team.id).limit(2)).all()
        before = (parent.player_wage_bill, borrower.player_wage_bill)
        player = max(parent.players, key=lambda p: p.current_wage)
        wage = player.current_wage
        player.team_id, player.loan_from_team_id, player.loan_wage_share = borrower.id, parent.id, 40
        session.flush()
        session.expire_all()
        parent, borrower = session.get(models.Team, parent.id), session.get(models.Team, borrower.id)
        assert borrower.player_wage_bill == before[1] + wage * 40 // 100
        assert parent.player_wage_bill == before[0] - wage * 40 // 100
        assert [p.id for p in parent.loaned_out_players] == [player.id]
    finally:
        session.rollback()
        session.close()


@integration
@pytest.mark.integration
def test_shared_world_helper_and_seat_constraints():
    from tests.world_helpers import cleanup_shared, make_shared_public

    world = None
    try:
        world = make_shared_public("SahipMenajer", ["UyeBir", ("UyeIki", "Istanbul Lions")],
                                   owner_team="Kadıköy Canaries")
        with database.session_scope() as db:
            seats = {s.display_name: s for s in db.scalars(select(models.WorldManager))}
            assert seats["SahipMenajer"].is_primary and seats["SahipMenajer"].team_id is None
            assert seats["UyeIki"].team.name == "Istanbul Lions" and seats["UyeBir"].team_id is None
            state = db.get(models.GameState, 1)
            assert state.user_id == world.owner_id and WorldRules.from_dict(state.world_rules).shared
            team_id = seats["UyeIki"].team_id
            for bad, constraint in (
                (models.WorldManager(user_id=None, display_name="Ikinci birincil", is_primary=True),
                 "uq_world_manager_primary"),
                (models.WorldManager(user_id=None, display_name="Kimliksiz", is_primary=False),
                 "ck_world_manager_user"),
                (models.WorldManager(user_id=world.owner_id, display_name="Cift", is_primary=False),
                 "uq_world_manager_user"),
            ):
                with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
                    db.add(bad)
                    db.flush()
            with pytest.raises(IntegrityError, match="uq_world_manager_team"), db.begin_nested():
                seats["UyeBir"].team_id = team_id                                  # ayni kulube iki koltuk olmaz
                db.flush()
            with pytest.raises(IntegrityError, match="ck_world_manager_primary_fields"), db.begin_nested():
                seats["SahipMenajer"].reputation = 9.0                             # birincilin tanınırlığı GameState'te
                db.flush()
        with database.engine.connect() as conn:
            assert conn.execute(text(
                'SELECT role FROM "accounts".world_memberships WHERE world_id = :w ORDER BY role'),
                {"w": world.world_id}).scalars().all() == ["MEMBER", "MEMBER", "OWNER"]
    finally:
        cleanup_shared(world)


@integration
@pytest.mark.integration
def test_shared_world_session_renders_world_slots():
    """Paylasilan dunya oturumu: eski sekmeler + Teklifler ve Yonetim yuvalari, kenar cubugunda takim secici yok."""
    pytest.importorskip("streamlit.testing.v1")
    import web_app
    import web_common
    from tests.world_helpers import app_as, cleanup_shared, make_shared_public

    world = None
    try:
        world = make_shared_public("DunyaSahibi", ["DunyaUyesi"], owner_team="Istanbul Lions")
        owner = app_as(world.owner_id, "DunyaSahibi", world.world_id)
        assert not owner.exception, owner.exception
        labels = [t.label for t in owner.tabs]
        assert labels == web_app.CAREER_TABS + [web_app.TAB_HUB, web_app.TAB_ADMIN]
        assert not [s for s in owner.selectbox if s.key == "sb_team"] and owner.button(key="sb_logout")
        # Faz 12 A4: kenar cubugu dunya paneli ve yonetim sekmesi dolu; pazar sekmesi (12B) hala iskelet
        assert owner.button(key="wp_ready") and owner.button(key="wp_force") and owner.button(key="adm_force")
        assert sum("Yakında" in i.value for i in owner.info) >= 1

        # Kulubu olmayan uye: kulup secimi sekmesi (+ Teklifler), yonetim sekmesi yok
        member = app_as(world.user_ids["DunyaUyesi"], "DunyaUyesi", world.world_id)
        assert not member.exception, member.exception
        assert [t.label for t in member.tabs] == [web_app.TAB_CLUBS, web_app.TAB_HUB]
        assert member.button(key="wp_ready").disabled and not [b for b in member.button if b.key == "wp_force"]

        member.session_state[web_common.LOBBY_KEY] = True                    # lobi yolu: dunya cizilmez
        member.run()
        assert not member.exception and not member.tabs and member.button(key="sb_logout")
    finally:
        cleanup_shared(world)


@integration
@pytest.mark.integration
def test_open_session_after_code_update_is_upgraded_not_told_to_reseed():
    """Oturum eski surumde hazirlandiysa (career_ready dolu) eksik sema once kayipsiz yukseltilir; seed onerilmez."""
    pytest.importorskip("streamlit.testing.v1")
    from tests.test_web_app import _app, _reseed

    _reseed()                                                               # mod secilmis temiz dunya
    try:
        at = _app()
        assert at.session_state["career_ready"] == "public" and at.tabs
        with database.engine.begin() as conn:                              # uygulama guncellendi: yeni sema eksik
            conn.exec_driver_sql('ALTER TABLE "teams" DROP COLUMN "ai_protected_until"')
            conn.exec_driver_sql('DROP TABLE "world_events"')
        at.run()
        assert not at.exception, at.exception
        assert not [e for e in at.error if "seed.py" in e.value] and at.tabs
        assert database.schema_problems() == []
    finally:
        _reseed()
