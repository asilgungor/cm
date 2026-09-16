"""OFM temalari (FM Dark / FM Light) -- saf testler: okunabilirlik, CSS butunlugu, giris gorseli."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ofm_theme as ot  # noqa: E402

THEMES = (ot.THEME_DARK, ot.THEME_LIGHT)


def test_requested_palette_colors():
    dark, light = ot.PALETTES[ot.THEME_DARK], ot.PALETTES[ot.THEME_LIGHT]
    assert (dark.bg, dark.panel, dark.accent, dark.primary) == ("#121824", "#1e2538", "#FFCD00", "#635BFF")
    assert (light.bg, light.panel, light.text, light.primary, light.accent) == \
        ("#f4f6f9", "#ffffff", "#1e293b", "#10b981", "#0284c7")


@pytest.mark.parametrize("theme", THEMES)
def test_text_stays_readable_in_both_themes(theme):
    p = ot.PALETTES[theme]
    assert ot.contrast_ratio(p.text, p.bg) >= 7                       # govde metni
    assert ot.contrast_ratio(p.text, p.panel) >= 7
    assert ot.contrast_ratio(p.muted, p.panel) >= 4.5                 # aciklamalar
    assert ot.contrast_ratio(p.primary_text, p.primary) >= 4.5        # dugme yazisi
    assert ot.contrast_ratio(p.accent, p.panel) >= 3                  # buyuk/kalin vurgu
    for hero in (p.hero_from, p.hero_via, p.hero_to):
        assert ot.contrast_ratio(p.login_text, hero) >= 4.5           # giris sayfasi metni
    assert ot.contrast_ratio(p.login_button_text, p.login_button) >= 7


def test_contrast_ratio_reference_values():
    assert ot.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert ot.contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)


@pytest.mark.parametrize("theme", THEMES)
def test_theme_css_is_one_block_without_blank_lines(theme):
    for login in (False, True):
        css = ot.theme_css(theme, login=login)
        assert css.startswith("<style>") and css.endswith("</style>") and css.count("<style>") == 1
        assert "\n\n" not in css
        assert f"--ofm-bg:{ot.PALETTES[theme].bg}" in css
        assert ("ofm-hero" in css) is login                           # giris stilleri yalnizca giris sayfasinda
    assert "st-key-arena_ball_" in ot.theme_css(theme) and ".cm-p-wrap" in ot.theme_css(theme)


def test_themes_differ_and_unknown_names_fall_back_to_dark():
    assert ot.theme_css("dark") != ot.theme_css("light")
    assert ot.normalize_theme("☀️ FM Light") == "light" and ot.normalize_theme("light") == "light"
    assert ot.normalize_theme(None) == ot.normalize_theme("<script>") == "dark"
    assert ot.theme_css("<script>") == ot.theme_css("dark")


@pytest.mark.parametrize("theme", THEMES)
def test_login_hero_is_a_drawing_not_a_photo(theme):
    html = ot.login_hero_html(theme)
    assert "<svg" in html and "<img" not in html and "http" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "\n" not in html and 'aria-hidden="true"' in html


def test_brand_and_headlines():
    assert "OFM" in ot.brand_html() and "Online Football Manager" in ot.brand_html()
    assert "giriş" in ot.login_headline_html("login").lower()
    assert "kaydı" in ot.login_headline_html("register")
    assert "&lt;b&gt;" in ot.panel_title_html("<b>") and "&lt;x&gt;" in ot.stat_strip_html([("<x>", 1)])
    assert not re.search(r"cm-green|Tahoma", ot.theme_css("dark"))
