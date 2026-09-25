"""
Hafta isleme hizi (Faz 14D) bekcileri.

Gercek PostgreSQL'e karsi (conftest'in kurgusal dunyasi); her test kendi islemini geri alir.
    * SQL bekcisi: bir play_week'in ifade sayisi (satir satir UPDATE / GameState yeniden okumasi geri gelirse kirilir)
    * GameState sabitleme: hafta boyunca game_state tablosuna en fazla 2 SELECT
    * Toplu yazici (CareerManager._bulk_write_dirty, _seat_snapshot icindeki before_flush kancasi):
        (a) izinli sutunlar tablo basina tek UPDATE ... FROM unnest(...) ile yazilir (satirda degismeyen sutun maskeyle
            korunur); flush sonrasi db.dirty bos ve ikinci flush UPDATE uretmez
        (b) veritabanindaki deger ORM degeriyle ayni (JSONB, NULL, enum dahil)
        (c) izinli liste disinda kirli alani olan nesne normal flush'la (satir UPDATE'i) yazilir
Parite: tests/test_multi_seat_career.py HEAD_PARITY testleri (yeniden temellendirilmeden yesil).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from career_manager import CareerManager  # noqa: E402
from models import Fixture, FixtureStatus, GameMode, Player, SquadRole, Team  # noqa: E402


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

USER_TEAM = "Istanbul Lions"

# SQL bekcisi (kurgusal dunya, kariyer modu, kulup ekonomisi kurulu, 1. hafta, tek play_week):
#   14D oncesi (HEAD bd70aa5): 781 ifade (SELECT 365, UPDATE 374, INSERT 20, SAVEPOINT 11, RELEASE 11);
#                              game_state 173 kez okundu.
#   14D sonrasi:              138 ifade (SELECT 75, UPDATE 21, INSERT 20, SAVEPOINT 11, RELEASE 11).
#   15A + 15D sonrasi:        166 ifade (SELECT 89, UPDATE 26, INSERT 25, SAVEPOINT 13, RELEASE 13):
#                              sozlesme dongusu (AI yenileme / serbest kalma) ve gelen kutusu yazimi eklendi.
#   15F (bayrak KAPALI):      ~181 ifade: kiralik yasam dongusunun BOS sorgulari ve savepoint'leri
#                              (loan_rules.SOLO_LOANS bayraktan bagimsiz acik; kiralik yokken satir YAZMAZ).
#   15F (bayrak ACIK):        335 ifade (SELECT 180, UPDATE 77, INSERT 28, SAVEPOINT 25, RELEASE 25).
#                              Bu, olculen EN KOTU hafta: donem ACILIS haftasi (AI listeleri + akademiden kadro
#                              tamamlama + dunya pazari). Donem ICI normal hafta ~300, donem DISI hafta ~167
#                              (yani pazar kapaliyken maliyet yok; kanit/15F/hiz_soz.txt).
# Sinir, o degerin ~1,2 kati (335 x 1,2 = 402 -> 400): kucuk eklemelere yer var; satir satir UPDATE, takim
# basina tembel yukleme ya da GameState yeniden okumasi geri gelirse (yuzlerce ifade) yine kirilir.
MAX_WEEK_STATEMENTS = 400


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@contextmanager
def _statements():
    """Blok icinde veritabanina giden ifadeler (metin)."""
    from database import engine

    seen: list[str] = []

    def _on(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _on)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _on)


def _career(db) -> CareerManager:
    cm = CareerManager(db, seed=7)
    cm.set_game_mode(GameMode.CAREER)
    cm.ensure_club_setup()
    cm.set_user_team(cm.find_team(USER_TEAM))
    db.flush()
    return cm


# ===========================================================================
# SQL bekcisi ve GameState sabitleme
# ===========================================================================

def test_week_statement_budget(db):
    cm = _career(db)
    with _statements() as seen:
        report = cm.play_week()
    assert report.results, "hafta oynanmadi"
    kinds: dict[str, int] = {}
    for s in seen:
        kinds[s.split(" ", 1)[0]] = kinds.get(s.split(" ", 1)[0], 0) + 1
    assert len(seen) <= MAX_WEEK_STATEMENTS, f"play_week {len(seen)} ifade ({kinds}); sinir {MAX_WEEK_STATEMENTS}"


def test_game_state_is_read_at_most_twice_per_week(db):
    cm = _career(db)
    with _statements() as seen:
        cm.play_week()
    reads = [s for s in seen if s.startswith("SELECT") and "FROM game_state" in s]
    assert len(reads) <= 2, f"play_week game_state'i {len(reads)} kez okudu"
    assert cm._pinned_state is None                   # hafta bitince sabitleme kalkar


def test_state_is_pinned_only_inside_snapshot(db):
    cm = _career(db)
    with cm._seat_snapshot():
        pinned = cm.state
        with _statements() as seen:
            for _ in range(50):
                assert cm.state is pinned
                assert cm.season == pinned.season and cm.career_week >= 1
        assert not seen
    assert cm._pinned_state is None


# ===========================================================================
# Toplu yazici
# ===========================================================================

def _players(cm, n: int) -> list[Player]:
    return sorted(cm.find_team(USER_TEAM).players, key=lambda p: p.id)[:n]


def _db_row(db, model, pk: int, *names: str) -> tuple:
    table = model.__table__
    return tuple(db.execute(select(*(table.c[n] for n in names)).where(table.c.id == pk)).one())


def test_bulk_writer_groups_allowed_columns_and_leaves_session_clean(db):
    cm = _career(db)
    players = _players(cm, 4)
    team = cm.find_team(USER_TEAM)
    fixture = db.scalar(select(Fixture).where(Fixture.status == FixtureStatus.UNPLAYED).order_by(Fixture.id))
    db.flush()
    with cm._seat_snapshot(), _statements() as seen:
        for i, p in enumerate(players):
            p.form = (p.form + 7 + i) % 100
            p.morale = (p.morale + 3) % 100
            p.minutes_window = [[90, 90, 1.0, 0]] * (i + 1)                      # JSONB
            p.match_rating_history = [6.5, 7.25, float(i)]
        team.points += 3
        team.played += 1
        team.won += 1
        fixture.home_score, fixture.away_score, fixture.status = 2, 1, FixtureStatus.PLAYED
        db.flush()
        assert not db.dirty                                                      # (a) flush sonrasi temiz
    updates = [s for s in seen if s.startswith("UPDATE")]
    player_updates = [s for s in updates if s.startswith("UPDATE players")]
    assert len(player_updates) == 1 and "FROM unnest(" in player_updates[0], updates
    assert all("FROM unnest(" in s for s in updates), updates
    assert {s.split(" ", 2)[1] for s in updates} == {"players", "teams", "fixtures"}

    with _statements() as again:                                                  # (a) ikinci flush: UPDATE yok
        db.flush()
    assert not [s for s in again if s.startswith("UPDATE")]

    for p in players:                                                            # (b) DB = ORM (JSONB dahil)
        assert _db_row(db, Player, p.id, "form", "morale", "minutes_window", "match_rating_history") == (
            p.form, p.morale, p.minutes_window, p.match_rating_history)
    assert _db_row(db, Team, team.id, "points", "played", "won") == (team.points, team.played, team.won)
    assert _db_row(db, Fixture, fixture.id, "home_score", "away_score", "status") == (2, 1, FixtureStatus.PLAYED)


def test_bulk_writer_handles_nulls_and_mixed_column_sets(db):
    cm = _career(db)
    a, b, c, d = _players(cm, 4)
    a.wage_demand, b.wage_demand, c.wage_demand, d.wage_demand = 5_000, None, 6_000, 7_000   # baslangic: normal flush
    a.concern_level = b.concern_level = c.concern_level = d.concern_level = 0
    c.injured_until_week = d.injured_until_week = 0
    db.flush()
    with cm._seat_snapshot(), _statements() as seen:
        a.wage_demand, b.wage_demand = None, 123_400                        # NULL ve deger ayni sutunda
        a.concern_level = b.concern_level = 2
        c.injured_until_week = 9                                            # farkli sutun kumesi: maskeli sutunlar
        d.wage_demand = None
        db.flush()
    updates = [s for s in seen if s.startswith("UPDATE players")]
    assert len(updates) == 1 and "FROM unnest(" in updates[0], updates     # tablo basina tek ifade
    assert _db_row(db, Player, a.id, "wage_demand", "concern_level", "injured_until_week") == (None, 2, a.injured_until_week)
    assert _db_row(db, Player, b.id, "wage_demand", "concern_level") == (123_400, 2)
    # maske: satirda degismeyen sutun kendi degerini korur
    assert _db_row(db, Player, c.id, "wage_demand", "concern_level", "injured_until_week") == (6_000, 0, 9)
    assert _db_row(db, Player, d.id, "wage_demand", "concern_level", "injured_until_week") == (None, 0, 0)


def test_bulk_writer_falls_back_for_other_dirty_fields(db):
    cm = _career(db)
    plain, other = _players(cm, 2)
    plain.form = (plain.form + 5) % 100
    other.form = (other.form + 5) % 100
    other.squad_role = SquadRole.BACKUP if other.squad_role is not SquadRole.BACKUP else SquadRole.FIRST_TEAM
    with cm._seat_snapshot(), _statements() as seen:
        db.flush()
    updates = [s for s in seen if s.startswith("UPDATE players")]
    bulk = [s for s in updates if "FROM unnest(" in s]
    rowwise = [s for s in updates if "FROM unnest(" not in s]
    assert len(bulk) == 1 and len(rowwise) == 1, updates                  # (c) izinsiz alan: normal flush
    assert "squad_role" in rowwise[0]
    assert not db.dirty
    assert _db_row(db, Player, other.id, "form", "squad_role") == (other.form, other.squad_role)
    assert _db_row(db, Player, plain.id, "form") == (plain.form,)


def test_bulk_writer_is_off_outside_week_transitions(db):
    cm = _career(db)
    (p,) = _players(cm, 1)
    with cm._seat_snapshot():
        assert cm._bulk_listener is not None
    assert cm._bulk_listener is None and cm._pinned_state is None      # dinleyici ve sabitleme kalkti
    p.form = (p.form + 5) % 100
    with _statements() as seen:
        db.flush()
    updates = [s for s in seen if s.startswith("UPDATE players")]
    assert len(updates) == 1 and "FROM unnest(" not in updates[0]          # normal flush
    assert _db_row(db, Player, p.id, "form") == (p.form,)
