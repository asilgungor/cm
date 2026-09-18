"""
tests/test_engine_distribution.py
=================================
13A DAGILIM KAPISI. Motorun urettigi lig, gercek futbolun bantlarinda mi?

Bantlar `.claude/phase13/benchmarks.md` dosyasindan gelir (Premier Lig / Opta, sezon
dalgalanmasi nedeniyle nokta hedef degil bant). Olcum araci `tests/engine_stats.py`
(teshis asamasindaki `.claude/phase13/scratch/harness.py` buraya tasindi).

13A basladiginda 13 bant saglanmiyordu ve `xfail(strict=True)` ile isaretliydi; motor
duzeldikce her biri "beklenmedik gecis" olarak kirmiziya dondu ve isareti kaldirildi:
    gol/mac, beraberlik payi, sut sayisi, kirmizi kart, penalti sayisi ve donusumu, korner,
    90+ gol payi, ilk yari payi, +20 farkta surpriz orani, +30'da favori doygunlugu,
    +30'da 5+ gollu mac orani, sezonun gol krali.
Bugun listedeki butun bantlar gecerlidir; biri duserse motor gerilemistir.

Kosum suresi ~60 sn (2 surec). `CM_GATE_SCALE=0.2 python -m pytest tests/test_engine_distribution.py`
ile hizli (istatistiksel olarak zayif) bir on kontrol yapilabilir.
"""

from __future__ import annotations

import os
import statistics
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.engine_stats import run, season_top_scorers  # noqa: E402

SCALE = float(os.getenv("CM_GATE_SCALE", "1.0"))


def _n(value: int) -> int:
    return max(50, int(value * SCALE))


N_EQUAL = _n(3000)
N_GAP20 = _n(3000)
N_GAP30 = _n(2000)
N_SEASONS = _n(30)


# ---------------------------------------------------------------------------
# Senaryolar (modul basina bir kez oynatilir)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def equal():
    """Esit iki takim (80 v 80), ev sahibi avantaji acik. Lig ortalamasinin vekili."""
    return run(N_EQUAL)


@pytest.fixture(scope="module")
def gap20():
    """+20 OVR fark. Tohumlarin yarisinda guclu takim deplasmanda: ev avantaji notrlenir."""
    return run(N_GAP20, home_ovr=90, away_ovr=70, swap_from=N_GAP20 // 2)


@pytest.fixture(scope="module")
def gap30():
    """+30 OVR fark (oyunda neredeyse hic olusmaz; egrinin ucunu sinar)."""
    return run(N_GAP30, home_ovr=95, away_ovr=65, swap_from=N_GAP30 // 2)


@pytest.fixture(scope="module")
def top_scorers():
    return season_top_scorers(N_SEASONS)


# ---------------------------------------------------------------------------
# Gol ve sonuc dagilimi
# ---------------------------------------------------------------------------

def test_goals_per_match_in_band(equal):
    assert 2.6 <= equal.goals_per_match <= 3.3, equal.report("EQUAL")


def test_home_win_share_in_band(equal):
    assert 40 <= equal.home_win_pct <= 47, equal.report("EQUAL")


def test_draw_share_in_band(equal):
    assert 21 <= equal.draw_pct <= 27, equal.report("EQUAL")


def test_away_win_share_in_band(equal):
    assert 28 <= equal.away_win_pct <= 36, equal.report("EQUAL")


# ---------------------------------------------------------------------------
# Sut / isabet / donusum
# ---------------------------------------------------------------------------

def test_shots_per_match_in_band(equal):
    assert 24 <= equal.shots_per_match <= 29, equal.report("EQUAL")


def test_shots_on_target_share_in_band(equal):
    assert 33 <= equal.sot_share <= 42, equal.report("EQUAL")


def test_conversion_in_band(equal):
    assert 9.5 <= equal.conversion <= 12.0, equal.report("EQUAL")


# ---------------------------------------------------------------------------
# Disiplin
# ---------------------------------------------------------------------------

def test_red_cards_in_band(equal):
    assert 0.08 <= equal.reds_per_match <= 0.22, equal.report("EQUAL")


def test_yellow_cards_in_band(equal):
    assert 3.5 <= equal.yellows_per_match <= 5.0, equal.report("EQUAL")


# ---------------------------------------------------------------------------
# Duran toplar
# ---------------------------------------------------------------------------

def test_penalties_per_match_in_band(equal):
    assert 0.18 <= equal.pens_per_match <= 0.40, equal.report("EQUAL")


def test_penalty_conversion_in_band(equal):
    assert 76 <= equal.pen_conversion <= 90, equal.report("EQUAL")


def test_corners_per_match_in_band(equal):
    assert 9.5 <= equal.corners_per_match <= 12.0, equal.report("EQUAL")


# ---------------------------------------------------------------------------
# Zaman ve ritim
# ---------------------------------------------------------------------------

def test_stoppage_time_goal_share_in_band(equal):
    assert 6.0 <= equal.goals_90plus_share <= 9.0, equal.report("EQUAL")


def test_first_half_goal_share_in_band(equal):
    assert 46 <= equal.first_half_share <= 50, equal.report("EQUAL")


# ---------------------------------------------------------------------------
# Guc -> sonuc egrisi
# ---------------------------------------------------------------------------

def test_underdog_win_rate_at_20_ovr_gap(gap20):
    assert 8.0 <= gap20.underdog_win_pct <= 12.0, gap20.report("GAP20")


def test_favourite_saturates_at_30_ovr_gap(gap30):
    assert 80.0 <= gap30.favourite_win_pct <= 85.0, gap30.report("GAP30")


def test_blowouts_are_rare_at_30_ovr_gap(gap30):
    assert gap30.five_plus_share <= 12.0, gap30.report("GAP30")


# ---------------------------------------------------------------------------
# Sezon sonuclari
# ---------------------------------------------------------------------------

def test_top_scorer_in_band(top_scorers):
    mean = statistics.mean(top_scorers)
    assert 18 <= mean <= 34, f"gol krali ortalamasi {mean:.1f} (n={len(top_scorers)}): {sorted(top_scorers)}"


# ---------------------------------------------------------------------------
# Raporlama: kapiyi gecen/gecmeyen her olcuyu yazdirir (pytest -s ile gorulur)
# ---------------------------------------------------------------------------

def test_print_distribution_report(equal, gap20, gap30, top_scorers):
    print()
    print(equal.report("EQUAL 80 v 80"))
    print(gap20.report("GAP +20"))
    print(f"GAP20 favori={gap20.favourite_win_pct:.1f}% beraberlik={gap20.gap_draw_pct:.1f}% "
          f"surpriz={gap20.underdog_win_pct:.1f}%")
    print(gap30.report("GAP +30"))
    print(f"GAP30 favori={gap30.favourite_win_pct:.1f}% beraberlik={gap30.gap_draw_pct:.1f}% "
          f"surpriz={gap30.underdog_win_pct:.1f}% 5+gol={gap30.five_plus_share:.1f}%")
    print(f"gol krali (38 mac): ortalama={statistics.mean(top_scorers):.1f} "
          f"medyan={statistics.median(top_scorers)} min={min(top_scorers)} max={max(top_scorers)}")
    assert equal.n == N_EQUAL
