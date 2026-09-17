"""
Uluslararasi takvim testleri (Faz 12 / 14. Asama, 12C): intl_calendar.py milli ara haftalari,
eleme mac gunlerinin pencerelere dagitimi ve Dunya Kupasi sezonu. Saf testler: veritabani gerektirmez.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import intl_calendar as ic  # noqa: E402
from schedule import weeks_in_season  # noqa: E402

# ===========================================================================
# international_weeks
# ===========================================================================

def test_full_season_windows_are_evenly_spread():
    assert ic.international_weeks(38) == [8, 20, 32]
    assert ic.international_weeks(38, windows=3) == ic.international_weeks(38)
    assert ic.international_weeks(34, windows=2) == [10, 26]


def test_synthetic_six_and_seven_week_seasons_get_distinct_middle_weeks():
    # sentetik dunya: 4 kulupluk lig -> cift devre 6 hafta
    assert weeks_in_season(4) == 6
    assert ic.international_weeks(6) == [2, 4, 5]
    assert ic.international_weeks(7) == [2, 4, 6]


@pytest.mark.parametrize(
    ("league_weeks", "expected"),
    [(0, []), (1, []), (2, []), (3, [2]), (4, [2, 3]), (5, [2, 3, 4])],
)
def test_tiny_seasons_get_fewer_windows(league_weeks, expected):
    assert ic.international_weeks(league_weeks) == expected


def test_window_invariants_for_all_season_lengths():
    for league_weeks in range(0, 61):
        for windows in range(0, 7):
            weeks = ic.international_weeks(league_weeks, windows)
            case = (league_weeks, windows, weeks)
            assert len(weeks) == max(0, min(windows, league_weeks - 2)), case
            assert weeks == sorted(set(weeks)), f"haftalar kesin artan olmali: {case}"
            assert all(2 <= w <= league_weeks - 1 for w in weeks), f"ilk/son hafta milli ara olamaz: {case}"
            # esit yayilim: ardisik aralar floor(C/n) ya da ceil(C/n) (C = aday hafta sayisi)
            if len(weeks) >= 2:
                candidates = league_weeks - 2
                low, high = candidates // len(weeks), -(-candidates // len(weeks))
                assert all(low <= b - a <= high for a, b in zip(weeks, weeks[1:], strict=False)), case
            assert ic.international_weeks(league_weeks, windows) == weeks, case  # deterministik


def test_invalid_window_arguments_raise():
    with pytest.raises(ValueError):
        ic.international_weeks(38, -1)
    with pytest.raises(ValueError):
        ic.international_weeks(38.0, 3)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ic.international_weeks(38, True)  # type: ignore[arg-type]


# ===========================================================================
# matchday_weeks
# ===========================================================================

def test_six_qualifier_matchdays_become_double_headers():
    assert ic.matchday_weeks(6, [8, 20, 32]) == [8, 8, 20, 20, 32, 32]
    assert ic.matchday_weeks(6, ic.international_weeks(6)) == [2, 2, 4, 4, 5, 5]


def test_fewer_matchdays_than_windows_spread_to_the_ends():
    assert ic.matchday_weeks(3, [8, 20, 32]) == [8, 20, 32]
    assert ic.matchday_weeks(2, [8, 20, 32]) == [8, 32]
    assert ic.matchday_weeks(1, [8, 20, 32]) == [20]
    assert ic.matchday_weeks(0, [8, 20, 32]) == []


def test_matchday_distribution_is_balanced_and_ordered():
    for matchdays in range(1, 13):
        for windows in range(1, 6):
            weeks = [3 * (i + 1) for i in range(windows)]
            placed = ic.matchday_weeks(matchdays, list(reversed(weeks)))  # giris sirasi onemsiz
            case = (matchdays, windows, placed)
            assert len(placed) == matchdays, case
            assert placed == sorted(placed), case
            assert set(placed) <= set(weeks), case
            load = Counter(placed)
            if matchdays >= windows:
                assert set(load) == set(weeks), f"her pencere kullanilmali: {case}"
                assert max(load.values()) - min(load.values()) <= 1, case


def test_matchdays_without_windows_raise():
    with pytest.raises(ValueError):
        ic.matchday_weeks(6, [])
    assert ic.matchday_weeks(0, []) == []
    with pytest.raises(ValueError):
        ic.matchday_weeks(-1, [8])


# ===========================================================================
# is_world_cup_season
# ===========================================================================

def test_world_cup_frequency():
    assert all(ic.is_world_cup_season(season) for season in range(1, 6))
    assert [s for s in range(1, 9) if ic.is_world_cup_season(s, 2)] == [2, 4, 6, 8]
    assert [s for s in range(1, 9) if ic.is_world_cup_season(s, 4)] == [4, 8]
    with pytest.raises(ValueError):
        ic.is_world_cup_season(0)
    with pytest.raises(ValueError):
        ic.is_world_cup_season(1, 0)
