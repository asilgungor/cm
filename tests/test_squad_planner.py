"""
Kadro planlayici testleri: squad_planner (saf) + preview_views.build_squad_plan (entegrasyon, rollback).
"""

from __future__ import annotations

import dataclasses
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import squad_planner as sp  # noqa: E402
from squad_planner import PlannerPlayer  # noqa: E402
from stars import star_range, stars  # noqa: E402

STAR_TEXT = re.compile(r"^[⭐💫–\s]+$")
SEASON = 3
_next_id = iter(range(1, 10_000))


def pp(position="DEF", age=25, overall=70, pot=(70, 72), years=3, injured=False, academy=False, name=None):
    pid = next(_next_id)
    return PlannerPlayer(pid, name or f"{position}{pid}", position, age, overall, pot[0], pot[1], years,
                         injured, academy)


def full_squad(formation="4-4-2", **kw) -> list[PlannerPlayer]:
    """Dizilisin onerilen asgarisini tam dolduran, sozlesmesi uzun, genc olmayan kadro."""
    mins = sp.recommended_minimums(formation)
    return [pp(g, **kw) for g in sp.GROUPS for _ in range(mins[g])]


# ===========================================================================
# 1) KURALLAR
# ===========================================================================

def test_recommended_minimums_follow_formations():
    assert sp.recommended_minimums("4-4-2") == {"GK": 2, "DEF": 6, "MID": 6, "FWD": 3}
    assert sp.recommended_minimums("4-3-3") == {"GK": 2, "DEF": 6, "MID": 5, "FWD": 4}
    assert sp.recommended_minimums("3-5-2") == {"GK": 2, "DEF": 5, "MID": 7, "FWD": 3}
    assert sp.recommended_minimums() == {"GK": 2, "DEF": 6, "MID": 7, "FWD": 4}
    assert sp.recommended_minimums("9-0-1") == sp.recommended_minimums("4-4-2")
    assert sp.starters_needed("4-3-3") == {"GK": 1, "DEF": 4, "MID": 3, "FWD": 3}


def test_group_status_thresholds():
    assert sp.group_status("GK", 1, 1, 2) == sp.STATUS_CRITICAL
    assert sp.group_status("GK", 2, 1, 2) == sp.STATUS_OK
    assert [sp.group_status("DEF", n, 4, 6) for n in (3, 4, 5, 6, 8)] == [
        sp.STATUS_CRITICAL, sp.STATUS_THIN, sp.STATUS_THIN, sp.STATUS_OK, sp.STATUS_OK]


def test_contract_helpers():
    assert sp.contract_expiry_season(5, 1) == 5 and sp.contract_expiry_season(5, 3) == 7
    assert sp.contract_expiry_season(5, 0) == 5
    assert [sp.contract_ends_this_season(y) for y in (0, 1, 2)] == [True, True, False]
    assert sp.under_contract_after(2, 1) and not sp.under_contract_after(1, 1)


def test_player_flags():
    assert sp.player_flags(pp(years=1)) == (sp.FLAG_CONTRACT,)
    assert sp.player_flags(pp(age=32)) == (sp.FLAG_AGING,)
    assert sp.player_flags(pp(age=31)) == ()
    assert sp.player_flags(pp(age=21, overall=60, pot=(60, 70))) == (sp.FLAG_GROWING,)   # tahmin orta 65
    assert sp.player_flags(pp(age=22, overall=60, pot=(60, 70))) == ()
    assert sp.player_flags(pp(age=19, overall=60, pot=(60, 64))) == ()                    # fark 2
    assert sp.player_flags(pp(injured=True)) == (sp.FLAG_INJURED,)
    assert sp.player_flags(pp(age=34, years=0, injured=True)) == (sp.FLAG_CONTRACT, sp.FLAG_AGING, sp.FLAG_INJURED)


# ===========================================================================
# 2) PLAN
# ===========================================================================

def test_balanced_squad_has_no_warnings():
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", full_squad())
    assert plan.recommendations == (sp.BALANCED_MESSAGE,)
    assert [g.group for g in plan.groups] == list(sp.GROUPS)
    assert all(g.status == sp.STATUS_OK for g in plan.groups)
    assert plan.squad_size == 17 and plan.formation == "4-4-2" and plan.horizon_seasons == 2
    for g in plan.groups:
        assert [p.season for p in g.projections] == [SEASON + 1, SEASON + 2]
        assert all(p.count == g.count and p.status == sp.STATUS_OK for p in g.projections)


def test_projection_removes_expiring_contracts_and_notes_aging():
    squad = [p for p in full_squad() if p.position != "DEF"] + [
        pp("DEF", years=1, name="Biten A"), pp("DEF", years=1, name="Biten B"), pp("DEF", years=2, name="Iki Yil"),
        pp("DEF", age=31, years=4, name="Otuzbir"), pp("DEF", years=4), pp("DEF", years=4),
    ]
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", squad, horizon_seasons=3)
    defence = plan.group("DEF")
    assert (defence.count, defence.status) == (6, sp.STATUS_OK)
    nxt, second, third = defence.projections
    assert (nxt.season, nxt.count, nxt.status) == (SEASON + 1, 4, sp.STATUS_THIN)
    assert set(nxt.leaving) == {"Biten A", "Biten B"} and nxt.aging == ("Otuzbir",)
    assert (second.count, second.status) == (3, sp.STATUS_CRITICAL) and "Iki Yil" in second.leaving
    assert third.count == 3
    flags = {p.name: p.flags for p in defence.players}
    assert flags["Biten A"] == (sp.FLAG_CONTRACT,) and flags["Otuzbir"] == ()
    expiry = {p.name: p.contract_expiry_season for p in defence.players}
    assert expiry["Biten A"] == SEASON and expiry["Iki Yil"] == SEASON + 1 and expiry["Otuzbir"] == SEASON + 3

    recs = plan.recommendations
    assert any(r.startswith("Gelecek sezon için defans takviyesi gerekli") and "Biten A" in r for r in recs)
    assert recs[-1] == "Sözleşmesi bu sezon bitenler: Biten A, Biten B."
    assert sp.BALANCED_MESSAGE not in recs
    assert [p.name for p in plan.contracts_ending] == ["Biten A", "Biten B"]


def test_critical_keeper_is_the_first_recommendation():
    squad = [p for p in full_squad() if p.position != "GK"] + [pp("GK")]
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", squad)
    assert plan.group("GK").status == sp.STATUS_CRITICAL
    assert plan.recommendations[0].startswith("Kaleci hattı kritik") and "kaleci takviyesi" in plan.recommendations[0]


def test_thin_group_injuries_and_aging_warnings():
    squad = [p for p in full_squad() if p.position != "FWD"] + [
        pp("FWD", age=33, injured=True, name="Yasli Sakat"), pp("FWD", age=34, name="Yasli"),
    ]
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", squad)
    forwards = plan.group("FWD")
    assert (forwards.count, forwards.available, forwards.status) == (2, 1, sp.STATUS_THIN)
    text = " | ".join(plan.recommendations)
    assert "Forvet hattı ince (2/3)" in text and "1 forvet daha önerilir" in text
    assert "yalnızca 1 sağlam oyuncu" in text
    assert "Forvet hattı yaşlanıyor: Yasli, Yasli Sakat 32 yaş ve üzerinde" in text
    flags = {p.name: p.flags for p in forwards.players}
    assert flags == {"Yasli": (sp.FLAG_AGING,), "Yasli Sakat": (sp.FLAG_AGING, sp.FLAG_INJURED)}


def test_academy_prospect_suggested_only_when_plausible():
    squad = [p for p in full_squad() if p.position != "MID"] + [pp("MID", overall=70) for _ in range(5)]
    good = pp("MID", age=18, overall=58, pot=(68, 80), academy=True, name="Umut")
    weak = pp("MID", age=17, overall=45, pot=(48, 55), academy=True, name="Zayif")
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", squad, academy=[weak, good])
    mids = plan.group("MID")
    assert [p.name for p in mids.prospects] == ["Umut", "Zayif"] and mids.count == 5
    assert all(p.in_academy for p in mids.prospects)
    text = " | ".join(plan.recommendations)
    assert "Akademide 1 umut veren orta saha oyuncusu adayı var; Umut" in text
    assert "Zayif" not in text

    no_good = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", squad, academy=[weak])
    assert "Akademide" not in " | ".join(no_good.recommendations)
    healthy = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", full_squad(), academy=[good])
    assert healthy.recommendations == (sp.BALANCED_MESSAGE,)          # ihtiyac yoksa oneri yok


def test_group_stars_horizon_and_squad_limit():
    squad = full_squad() + [pp("MID", overall=40) for _ in range(9)]
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-3-3", squad, horizon_seasons=0, squad_max=25)
    assert plan.horizon_seasons == 1 and all(len(g.projections) == 1 for g in plan.groups)
    assert plan.recommendations[0].startswith("A takım kadrosu 26 oyuncu; sınır 25")
    assert plan.group("MID").stars == stars(70)                         # en iyi 3 orta saha
    empty = sp.plan_squad(1, "Alfa", SEASON, None, [])
    assert empty.formation == "4-4-2" and all(g.stars == "–" and g.status == sp.STATUS_CRITICAL for g in empty.groups)


def test_output_hides_numeric_ratings():
    squad = full_squad() + [pp("FWD", age=19, overall=61, pot=(66, 78))]
    plan = sp.plan_squad(1, "Alfa", SEASON, "4-4-2", squad, academy=[pp("GK", age=17, academy=True)])
    fields = {f.name for f in dataclasses.fields(sp.PlanPlayer)}
    assert not fields & {"overall", "potential_low", "potential_high", "overall_rating"}
    for g in plan.groups:
        for p in g.players + g.prospects:
            assert STAR_TEXT.match(p.stars) and STAR_TEXT.match(p.potential_stars)
    young = next(p for p in plan.group("FWD").players if p.age == 19)
    assert young.potential_stars == star_range(66, 78) and sp.FLAG_GROWING in young.flags


# ===========================================================================
# 3) ENTEGRASYON
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


@contextmanager
def sql_log(db):
    statements: list[str] = []
    engine = db.get_bind()

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@integration
@pytest.mark.integration
def test_build_squad_plan_matches_the_club(db):
    import preview_views as pv
    from career_manager import SENIOR_SQUAD_MAX, CareerManager
    from models import GameState, Player

    cm = CareerManager(db)
    team = cm.find_team("Istanbul Lions")
    assert team is not None
    season = db.get(GameState, 1).season
    week = db.get(GameState, 1).current_week
    seniors = {p.id: p for p in team.players}
    academy = {p.id: p for p in db.scalars(select(Player).where(Player.team_id == team.id,
                                                                Player.in_academy.is_(True)))}
    # Bayraklari kesinlestir: biri sozlesmesi bitiyor + yasli, biri sakat
    veteran, crocked = list(seniors.values())[:2]
    veteran.contract_years, veteran.age = 1, 33
    crocked.injured_until_week = week + 3
    db.flush()

    with sql_log(db) as statements:
        plan = pv.build_squad_plan(db, team.id)
    assert statements and not [s for s in statements
                               if s.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}]
    assert not db.new and not db.dirty and not db.deleted

    assert plan.team_id == team.id and plan.season == season and plan.formation == team.formation
    assert plan.squad_size == len(seniors) and plan.squad_max == SENIOR_SQUAD_MAX
    assert sum(g.count for g in plan.groups) == len(seniors)
    listed = {p.player_id: p for g in plan.groups for p in g.players}
    assert set(listed) == set(seniors)
    assert {p.player_id for g in plan.groups for p in g.prospects} == set(academy)
    for pid, row in listed.items():
        player = seniors[pid]
        assert row.position == player.position.value and row.age == player.age
        assert row.stars == stars(player.overall_rating)
        assert row.potential_stars == star_range(*cm.potential_estimate(team, player))
        assert row.contract_expiry_season == season + max(player.contract_years, 1) - 1
    assert sp.FLAG_CONTRACT in listed[veteran.id].flags and sp.FLAG_AGING in listed[veteran.id].flags
    assert sp.FLAG_INJURED in listed[crocked.id].flags
    assert any(r.startswith("Sözleşmesi bu sezon bitenler") and veteran.name in r for r in plan.recommendations)

    assert all(g.prospects == () for g in pv.build_squad_plan(db, team.id, include_academy=False).groups)
    with pytest.raises(ValueError):
        pv.build_squad_plan(db, 10_000_000)
