"""
Kariyer paneli gorunum modelleri testleri (7. Asama): career_views, finance onizleme,
web_view panel parcalari. Streamlit gerektirmez; DB testleri rollback ile calisir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import career_views as cv  # noqa: E402
import web_view  # noqa: E402
from finance import (  # noqa: E402
    WEEKS_PER_YEAR,
    plan_budget_shift,
    preview_budget_shift,
    wage_budget_bounds,
)
from models import Position  # noqa: E402

# ===========================================================================
# 1) Finans onizleme (saf)
# ===========================================================================

def test_wage_budget_bounds():
    low, high = wage_budget_bounds(transfer_budget=5_200_000, wage_budget=800_000, committed_weekly=700_000)
    assert (low, high) == (700_000, 900_000)
    # Zaten asim varsa alt sinir mevcut havuzdur (daha asagi inilemez)
    assert wage_budget_bounds(0, 600_000, 700_000) == (600_000, 600_000)


def test_preview_matches_real_shift_everywhere_in_bounds():
    transfer, wage, committed = 5_200_000, 800_000, 700_000
    low, high = wage_budget_bounds(transfer, wage, committed)
    for target in range(low, high + 1, 25_000):
        preview = preview_budget_shift(transfer, wage, target, committed)
        assert preview.valid, (target, preview.message)
        if target == wage:
            assert not preview.changed and preview.new_transfer_budget == transfer
            continue
        assert (preview.new_transfer_budget, preview.new_wage_budget) == plan_budget_shift(
            transfer, wage, target - wage, committed)
        assert preview.transfer_impact == -(target - wage) * WEEKS_PER_YEAR


def test_preview_reports_violations_without_raising():
    over = preview_budget_shift(100_000, 800_000, 900_000, 700_000)
    assert not over.valid and "Transfer bütçesi" in over.message
    under = preview_budget_shift(5_000_000, 800_000, 600_000, 700_000)
    assert not under.valid and "maaş yükünün" in under.message


# ===========================================================================
# 2) Tablo duzenleyicisinden kadro karari (saf)
# ===========================================================================

def test_lineup_from_editor():
    rows = [
        {"id": 1, "Mv": "GK", "Durum": "İlk 11", "Slot": None},
        {"id": 2, "Mv": "MID", "Durum": "İlk 11", "Slot": "FWD"},
        {"id": 3, "Mv": "DEF", "Durum": "Kulübe", "Slot": "DEF"},
        {"id": 4, "Mv": "FWD", "Durum": "Kadro dışı", "Slot": None},
    ]
    xi, bench = cv.lineup_from_editor(rows)
    assert xi == {1: Position.GK, 2: Position.FWD}
    assert bench == [3]


def test_lineup_from_editor_rejects_bad_slot():
    with pytest.raises(ValueError):
        cv.lineup_from_editor([{"id": 1, "Mv": "GK", "Durum": "İlk 11", "Slot": "LIBERO"}])


# ===========================================================================
# 3) HTML parcalari (saf)
# ===========================================================================

def test_condition_bar_colors_and_clamp():
    assert "cm-cond good" in web_view.condition_bar_html(92, "good")
    assert "cm-cond warn" in web_view.condition_bar_html(65, "warn")
    assert "cm-cond low" in web_view.condition_bar_html(40, "low")
    assert "width:100%" in web_view.condition_bar_html(140, "good")
    assert web_view.condition_bar_html(None, None) == "—"


def test_usage_bar_bands():
    assert "cm-usage ok" in web_view.usage_bar_html(70)
    assert "cm-usage tight" in web_view.usage_bar_html(95)
    over = web_view.usage_bar_html(130)
    assert "cm-usage over" in over and "width:100.0%" in over


def test_squad_table_escapes_and_marks_unavailable():
    row = cv.SquadRow(
        id=1, name="<b>Kötü</b>", position="FWD", age=25, overall=80, form=50, morale=70, condition=55,
        condition_band="low", status="İlk 11", slot="FWD", unavailable="sakat, 4. haftada dönüyor",
        average_rating=None, weeks_idle=0, contract_years=2, wage=10_000, market_value=1_000_000,
        squad_role="As",
    )
    html = web_view.squad_table_html([row])
    assert "<b>Kötü</b>" not in html and "&lt;b&gt;" in html
    assert "cm-badge bad" in html and "cm-cond low" in html


def test_negotiation_log_escapes():
    html = web_view.negotiation_log_html([("me", "<script>x</script>"), ("bad", "masadan kalktı")])
    assert "<script>" not in html and 'class="m bad"' in html


# ===========================================================================
# 4) Veritabanli gorunum modelleri
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


@pytest.fixture
def cm(db):
    from career_manager import CareerManager

    manager = CareerManager(db, seed=1)
    if manager.season_finished:
        pytest.skip("Sezon bitmiş")
    return manager


@integration
@pytest.mark.integration
def test_squad_rows_include_condition_and_status(cm):
    team = cm.find_team("Galatasaray")
    team.players[0].condition = 58
    cm.auto_lineup(team)
    rows = cv.squad_rows(team, cm.current_week)
    assert len(rows) == len(team.players)
    assert [r.position for r in rows] == sorted((r.position for r in rows), key=["GK", "DEF", "MID", "FWD"].index)
    tired = next(r for r in rows if r.id == team.players[0].id)
    assert tired.condition == 58 and tired.condition_band == "low" and tired.low_condition
    assert sum(r.status == "İlk 11" for r in rows) == 11
    assert all(r.slot for r in rows if r.status == "İlk 11")


@integration
@pytest.mark.integration
def test_market_rows_respect_scout_fog(cm, db):
    from models import StaffRole

    buyer = cm.find_team("Arsenal")
    scout = buyer.staff_by_role(StaffRole.SCOUT)[0]
    scout.judging_ability = 1                                   # cok sisli
    db.flush()
    rows = cv.market_rows(cm, buyer, cv.MarketFilter(min_estimated_overall=1, limit=500))
    assert rows and all(r.club != buyer.name for r in rows)
    assert all(not r.exact and r.overall_high > r.overall_low for r in rows)
    # Siralama gercek degere degil tahmine gore
    estimates = [r.overall_estimate for r in rows]
    assert estimates == sorted(estimates, reverse=True)
    # Tahmin filtresi: esik tahmin uzerinden uygulanir
    strict = cv.market_rows(cm, buyer, cv.MarketFilter(min_estimated_overall=85, limit=500))
    assert all(r.overall_estimate >= 85 for r in strict)
    by_position = cv.market_rows(cm, buyer, cv.MarketFilter(positions={"GK"}, min_estimated_overall=1))
    assert by_position and {r.position for r in by_position} == {"GK"}


@integration
@pytest.mark.integration
def test_standings_scorers_and_week_lines(cm):
    team = cm.find_team("Galatasaray")
    cm.set_user_team(team)
    cm.run_ai_transfer_window = lambda: []
    report = cm.play_week()
    lines = cv.week_report_lines(report)
    assert lines[0][0] == "info" and any(kind == "result" for kind, _ in lines)
    standings = cv.standings_rows(cm, team.league_id, team.id)
    assert [r["#"] for r in standings] == list(range(1, len(standings) + 1))
    assert sum(r["Takım"].startswith("► ") for r in standings) == 1
    assert all(r["O"] == 1 for r in standings)
    assert cv.result_rows(cm, cm.last_played_week())
    assert all(r["Gol"] >= 1 for r in cv.scorer_rows(cm))


@integration
@pytest.mark.integration
def test_staff_views(cm):
    team = cm.find_team("Juventus")
    rows = cv.staff_rows(team.staff)
    assert len(rows) == len(team.staff) and all("Özellikler" in r for r in rows)
    effects = cv.staff_effects(cm, team)
    assert 0.45 <= effects.injury_multiplier <= 1.4 and 0.6 <= effects.recovery_rate <= 0.9
