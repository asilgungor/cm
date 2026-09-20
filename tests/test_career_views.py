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


def _squad_row(**over):
    base = dict(
        id=1, name="Ali", position="FWD", age=25, overall=80, form=50, morale=70, condition=55,
        condition_band="low", status="İlk 11", slot="FWD", unavailable="sakat, 4. haftada dönüyor",
        average_rating=None, weeks_idle=0, contract_years=2, wage=10_000, market_value=1_000_000,
        squad_role="As", squad_role_key="FIRST_TEAM",
    )
    base.update(over)
    return cv.SquadRow(**base)


def test_squad_views_have_ten_plus_columns_and_numeric_money():
    """14G: CM kadro gorunumleri (Genel / Sozlesme / Mac istatistikleri / Kondisyon) >= 10 sutun; para SAYI, sozcuk
    olcegi yildizsiz; kendi oyuncunda yetenek kesin sozcuk."""
    import re

    rows = [_squad_row(id=1, wage=850_000, market_value=850_000, average_rating=7.25, appearances=3, goals=2),
            _squad_row(id=2, name="Veli", wage=12_500_000, market_value=12_500_000, overall=62)]
    assert cv.SQUAD_VIEWS == ("Genel", "Sözleşme", "Maç istatistikleri", "Kondisyon")
    for view in cv.SQUAD_VIEWS:
        out = cv.squad_view_rows(rows, view)
        assert len(out[0]) >= 10, (view, list(out[0]))
        text = " ".join(str(v) for r in out for v in r.values())
        assert not re.search("[⭐\U0001F4AB★]", text)                          # yildiz yok
    contract = cv.squad_view_rows(rows, cv.VIEW_CONTRACT)
    assert isinstance(contract[0]["Maaş/hf (EUR)"], int) and isinstance(contract[0]["Değer (EUR)"], int)
    assert contract[0][cv.ABILITY_LABEL] == "Dünya çapında" and contract[1][cv.ABILITY_LABEL] == "Yeterli"
    general = cv.squad_view_rows(rows, cv.VIEW_GENERAL)
    assert general[0]["Durum"] == "sakat, 4. haftada dönüyor" and general[0]["Ort. not"] == 7.25
    assert general[1]["Ort. not"] == 0.0                     # mac oynamadi: bos hucre yerine 0 (sayisal siralama)
    assert all(v is not None for r in cv.squad_view_rows(rows, cv.VIEW_STATS) for v in r.values())


def test_money_sorts_as_numbers_not_text():
    """14G kabul: "850K" < "12.5M" -- tablo SAYIYLA siralar (metinle siralansa '850K' > '12.5M' olurdu)."""
    import pandas as pd

    rows = [_squad_row(id=1, market_value=12_500_000, wage=12_500_000),
            _squad_row(id=2, market_value=850_000, wage=850_000)]
    frame = pd.DataFrame(cv.squad_view_rows(rows, cv.VIEW_CONTRACT))
    assert pd.api.types.is_numeric_dtype(frame["Değer (EUR)"])
    ordered = frame.sort_values("Değer (EUR)")["Değer (EUR)"].tolist()
    assert ordered == [850_000, 12_500_000]
    assert cv.short_money(ordered[0]) == "850K" and cv.short_money(ordered[1]) == "12.5M"
    assert sorted(["850K", "12.5M"]) == ["12.5M", "850K"]                    # metin sirasi yanlis olurdu
    assert set(cv.MONEY_COLUMNS) <= cv.NUMERIC_COLUMNS


# ===========================================================================
# 3b) TEK SIS MODELI (14G, saf)
# ===========================================================================

def test_fog_thresholds_match_the_attribute_grid():
    """Tek esik takimi: yetenek / deger / izgara ayni bilgi duzeylerinde '?', aralik ve kesin olur."""
    import cm_attributes

    for k in range(0, 101):
        grid = cm_attributes.attribute_display(12, k, "p|x")
        level = cv.fog_level(k)
        rating, money = cv.fog_rating(66, k, (1, "a")), cv.fog_money(850_000, k, (1, "v"))
        if grid == "?":
            assert level == cv.FOG_UNKNOWN and rating is None and money is None, k
        elif grid.isdigit():
            assert level == cv.FOG_EXACT and rating == (66, 66) and money == (850_000, 850_000), k
        else:
            assert level == cv.FOG_RANGE and rating[0] <= 66 <= rating[1] and rating[0] < rating[1], k
            assert money[0] <= 850_000 <= money[1] and money[0] < money[1], k


def test_fog_ranges_contain_the_truth_and_narrow_with_knowledge():
    for value in (35, 50, 66, 79, 95):
        widths = []
        for k in (25, 35, 45, 55, 65, 69):
            low, high = cv.fog_rating(value, k, (value, "a"))
            assert low <= value <= high and 1 <= low and high <= 99
            widths.append(high - low)
        assert widths == sorted(widths, reverse=True)
    assert cv.fog_rating(66, 40, (7, "a")) == cv.fog_rating(66, 40, (7, "a"))          # deterministik


def test_zero_knowledge_shows_question_marks():
    """14G kabul: bilgisi %0 olan oyuncuda deger ve yetenek '?'."""
    from types import SimpleNamespace

    viewer = SimpleNamespace(id=1)
    player = SimpleNamespace(id=9, team_id=2, overall_rating=77, market_value=4_000_000, contract_years=3)
    fog = cv.player_fog(None, viewer, player, 0)
    assert fog.ability_text == "?" and fog.value_text == "?" and fog.potential_text == "?"
    assert fog.contract_text == "?" and fog.level == cv.FOG_UNKNOWN
    known = cv.player_fog(None, viewer, player, 80)
    assert known.ability_text == "Çok iyi" and known.value_text == "4.0M" and known.contract_text == "3 yıl"
    own = cv.player_fog(None, SimpleNamespace(id=2), player, None)
    assert own.knowledge == 100 and own.ability == (77, 77)


def test_ability_words_replace_stars():
    assert cv.ability_word(82) == "Dünya çapında" and cv.ability_word(66) == "İyi" and cv.ability_word(None) == "?"
    assert cv.ability_text((56, 76)) == "Vasat – Çok iyi" and cv.ability_text(None) == "?"
    assert cv.star_text_word("⭐⭐⭐\U0001F4AB") == "İyi" and cv.star_text_word("–") == "?"
    assert cv.star_text_word("⭐⭐ – ⭐⭐⭐⭐") == "Zayıf – Çok iyi"
    assert cv.star_text_word("★★½") == "Vasat"
    assert cv.money_range_text((810_000, 1_200_000)) == "810K – 1.2M"


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
    team = cm.find_team("Istanbul Lions")
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
def test_market_rows_follow_the_single_fog_model(cm, db):
    """14G: pazar tablosu bilgi yuzdesinden (tek sis modeli): ayni lig %35 -> aralik, baska lig %0 -> '?' (sonda,
    alt sinir filtresinde elenir), gozlemci %80 -> kesin."""
    from models import ScoutAssignment

    buyer = cm.find_team("London Gunners")
    rows = cv.market_rows(cm, buyer, cv.MarketFilter(min_estimated_overall=1, limit=5000))
    assert rows and all(r.club != buyer.name for r in rows)
    same = [r for r in rows if r.knowledge == 35]
    unknown = [r for r in rows if r.knowledge == 0]
    assert same and unknown
    assert all(not r.exact and r.overall_high > r.overall_low for r in same)
    assert all(r.ability_text == "?" and r.value_text == "?" and r.contract_text == "?" for r in unknown)
    estimates = [r.overall_estimate for r in rows]
    known = [e for e in estimates if e is not None]
    assert known == sorted(known, reverse=True) and estimates[: len(known)] == known     # bilinmeyenler sonda
    strict = cv.market_rows(cm, buyer, cv.MarketFilter(min_estimated_overall=60, limit=5000))
    assert all(r.overall_estimate is not None and r.overall_estimate >= 60 for r in strict)
    by_position = cv.market_rows(cm, buyer, cv.MarketFilter(positions={"GK"}, min_estimated_overall=1))
    assert by_position and {r.position for r in by_position} == {"GK"}
    target = unknown[0]
    db.add(ScoutAssignment(team_id=buyer.id, player_id=target.id, knowledge=80, status="DONE",
                           assigned_career_week=1, updated_career_week=1))
    db.flush()
    again = {r.id: r for r in cv.market_rows(cm, buyer, cv.MarketFilter(min_estimated_overall=1, limit=5000))}
    assert again[target.id].exact and again[target.id].knowledge == 80 and again[target.id].contract_text != "?"


@integration
@pytest.mark.integration
def test_knowledge_map_matches_the_transfer_desk_rule(cm, db):
    """career_views.knowledge_map (tek sorgu) = TransferDesk.knowledge_of (masanin kurali)."""
    from sqlalchemy import select

    from models import Player
    from transfer_desk import TransferDesk

    buyer = cm.find_team("London Gunners")
    players = list(db.scalars(select(Player).where(Player.in_academy.is_(False)).limit(60)))
    known = cv.knowledge_map(db, buyer, [p.id for p in players])
    desk = TransferDesk(cm)
    assert known == {p.id: desk.knowledge_of(buyer, p) for p in players}


@integration
@pytest.mark.integration
def test_standings_scorers_and_week_lines(cm):
    team = cm.find_team("Istanbul Lions")
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
    team = cm.find_team("Torino Bianconeri")
    rows = cv.staff_rows(team.staff)
    assert len(rows) == len(team.staff) and all("Özellikler" in r for r in rows)
    effects = cv.staff_effects(cm, team)
    assert 0.45 <= effects.injury_multiplier <= 1.4 and 0.6 <= effects.recovery_rate <= 0.9
