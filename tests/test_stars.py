"""Yildiz sistemi (10. Asama) -- saf testler."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stars  # noqa: E402


@pytest.mark.parametrize("rating,expected", [
    (99, "⭐⭐⭐⭐⭐"), (80, "⭐⭐⭐⭐⭐"), (79, "⭐⭐⭐⭐💫"), (75, "⭐⭐⭐⭐💫"), (74, "⭐⭐⭐⭐"),
    (70, "⭐⭐⭐⭐"), (69, "⭐⭐⭐💫"), (60, "⭐⭐⭐"), (45, "⭐💫"), (40, "⭐"), (39, "💫"), (1, "💫"),
])
def test_star_bands_match_the_cm_scale(rating, expected):
    assert stars.stars(rating) == expected


def test_values_glyphs_and_unknown():
    assert stars.star_value(80) == 5.0 and stars.star_value(77) == 4.5 and stars.star_value(12) == 0.5
    assert stars.star_glyphs(77) == "★★★★½" and stars.star_glyphs(80) == "★★★★★"
    assert stars.stars(None) == stars.star_glyphs(None) == stars.UNKNOWN
    assert all(ch not in stars.stars(r) for r in range(1, 100) for ch in "0123456789")   # sayi sizmaz


def test_range_collapses_when_both_ends_share_stars():
    assert stars.star_range(71, 73) == "⭐⭐⭐⭐"
    assert stars.star_range(78, 66) == "⭐⭐⭐💫 – ⭐⭐⭐⭐💫"
    assert stars.star_range(None, 70) == stars.UNKNOWN


def test_threshold_round_trips_with_value():
    for value in (0.5, 1.0, 2.5, 3.0, 4.5, 5.0):
        assert stars.star_value(stars.star_threshold(value)) == value
        assert stars.star_value(stars.star_threshold(value) - 1) < value or value == 0.5
    assert [label for label, _ in stars.FILTER_OPTIONS][0] == "⭐"
