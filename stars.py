"""
stars.py
========
Yildiz sistemi (10. Asama). SAF MANTIK: veritabani ve Streamlit bilmez.

Kadro, transfer pazari ve akademi ekranlarinda sayisal guc (1-99) GIZLENIR; guc ve
potansiyel 5 yildiz uzerinden gosterilir (CM tarzi). Olcek, 10 puanlik bantlar ve
bandin ust yarisi icin yarim yildiz:

    80+     ⭐⭐⭐⭐⭐        5
    75-79   ⭐⭐⭐⭐💫       4.5
    70-74   ⭐⭐⭐⭐         4
    65-69   ⭐⭐⭐💫        3.5
    60-64   ⭐⭐⭐          3
    ...
    35-39   💫             0.5   (en az yarim yildiz)

stars()        emoji metni (tablolar, secim kutulari)
star_glyphs()  SVG'de guvenle cizilen ★ / ½ metni (taktik tahtasi)
star_range()   gozlemci sisi: tahmin araligi iki uc ayni yildizdaysa tek deger
star_threshold() filtre: "en az N yildiz" -> gerekli en dusuk guc
"""

from __future__ import annotations

import math

FULL = "⭐"
HALF = "💫"
GLYPH_FULL = "★"
GLYPH_HALF = "½"
UNKNOWN = "–"

MAX_STARS = 5.0
MIN_STARS = 0.5
_BASE = 30          # 40 -> 1 yildiz, 80 -> 5 yildiz
_STEP = 10


def star_value(rating: float | None) -> float | None:
    """Guc (1-99) -> 0.5 adimli yildiz (0.5-5.0). None -> None."""
    if rating is None:
        return None
    halves = math.floor((float(rating) - _BASE) / (_STEP / 2))
    return max(MIN_STARS, min(MAX_STARS, halves / 2))


def _parts(value: float) -> tuple[int, bool]:
    full = int(value)
    return full, value - full >= 0.5


def stars(rating: float | None) -> str:
    """Emoji yildizlar: 77 -> '⭐⭐⭐⭐💫'."""
    value = star_value(rating)
    if value is None:
        return UNKNOWN
    full, half = _parts(value)
    return FULL * full + (HALF if half else "")


def star_glyphs(rating: float | None) -> str:
    """SVG metni icin: 77 -> '★★★★½' (emoji yazi tipi gerektirmez)."""
    value = star_value(rating)
    if value is None:
        return UNKNOWN
    full, half = _parts(value)
    return GLYPH_FULL * full + (GLYPH_HALF if half else "")


def star_range(low: float | None, high: float | None) -> str:
    """Tahmin araligi: iki uc ayni yildizdaysa tek deger, degilse 'alt – ust'."""
    if low is None or high is None:
        return UNKNOWN
    lo, hi = stars(min(low, high)), stars(max(low, high))
    return lo if lo == hi else f"{lo} – {hi}"


def star_threshold(min_stars: float) -> int:
    """'En az N yildiz' icin gereken en dusuk guc: 3 -> 60, 4.5 -> 75."""
    value = max(MIN_STARS, min(MAX_STARS, float(min_stars)))
    return int(_BASE + value * _STEP)


FILTER_OPTIONS: tuple[tuple[str, float], ...] = tuple(
    (stars(star_threshold(v)), v) for v in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
)
