"""OFM temalari (OFM Dark / OFM Light) -- saf testler: okunabilirlik, CSS butunlugu, giris gorseli.

Faz 13: temanin iki katmani da burada kilitlenir --
  * her token cifti WCAG AA gecmeli (contrast_pairs),
  * .streamlit/config.toml paletleri ofm_theme.PALETTES ile birebir ayni olmali (Streamlit'in kendi
    cizdigi widget'lar ve canvas tablolar o dosyadan renk alir),
  * CSS Streamlit 1.64 secicilerini kullanmali (1.64 BaseWeb kullanmaz: data-baseweb secicileri bosa duser).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ofm_theme as ot  # noqa: E402

THEMES = (ot.THEME_DARK, ot.THEME_LIGHT)
CONFIG_TOML = ROOT / ".streamlit" / "config.toml"


def test_requested_palette_colors():
    dark, light = ot.PALETTES[ot.THEME_DARK], ot.PALETTES[ot.THEME_LIGHT]
    assert (dark.bg, dark.panel, dark.accent, dark.primary) == ("#121824", "#1e2538", "#FFCD00", "#635BFF")
    assert (light.bg, light.panel, light.text, light.primary, light.accent) == \
        ("#f4f6f9", "#ffffff", "#1e293b", "#10b981", "#0369a1")       # vurgu: beyaz panelde 4.5:1 gecen koyu mavi


@pytest.mark.parametrize("theme", THEMES)
def test_every_rendered_token_pair_passes_wcag_aa(theme):
    """
    Ekranda yan yana cizilen HER renk cifti AA gecer (normal metin 4.5:1, buyuk metin / arayuz 3:1).
    Yeni bir cift ofm_theme.contrast_pairs'e eklenmeden kullanilirsa bu test onu yakalamaz; bu yuzden
    asagidaki test CSS'te gecen her tokenin en az bir ciftte denetlendigini ayrica dogrular.
    """
    failures = [
        f"{name}: {fg} / {bg} = {ot.contrast_ratio(fg, bg):.2f} (gereken {need})"
        for name, fg, bg, need in ot.contrast_pairs(theme)
        if ot.contrast_ratio(fg, bg) < need
    ]
    assert not failures, f"{theme}: okunmayan renk cifti -> " + "; ".join(failures)


@pytest.mark.parametrize("theme", THEMES)
def test_contrast_pairs_cover_every_colour_token_we_paint(theme):
    """Paletteki her renk en az bir kontrast ciftinde gecmeli (unutulan token sessizce okunmaz kalmasin)."""
    p = ot.PALETTES[theme]
    checked = {value.lower() for _, fg, bg, _ in ot.contrast_pairs(theme) for value in (fg, bg)}
    skip = {"border", "primary", "tri_a", "tri_b", "tri_c"}           # cizgi / saha rengi: metin tasimaz
    missing = sorted(field for field in p.__dataclass_fields__
                     if field not in skip and getattr(p, field).lower() not in checked)
    assert not missing, f"{theme}: kontrast denetimi disinda kalan token(lar): {missing}"


@pytest.mark.parametrize("theme", THEMES)
def test_text_stays_readable_in_both_themes(theme):
    p = ot.PALETTES[theme]
    assert ot.contrast_ratio(p.text, p.bg) >= 7                       # govde metni
    assert ot.contrast_ratio(p.text, p.panel) >= 7
    assert ot.contrast_ratio(p.text, p.input_bg) >= 7                 # giris alani (Faz 13 hatasi: beyaz/beyaz)
    assert ot.contrast_ratio(p.muted, p.panel) >= 4.5                 # aciklamalar
    assert ot.contrast_ratio(p.muted, p.input_bg) >= 4.5              # yer tutucu
    assert ot.contrast_ratio(p.primary_text, p.primary) >= 4.5        # dugme yazisi
    assert ot.contrast_ratio(p.accent, p.panel) >= 4.5                # panel basligi: normal boy, kalin
    for hero in (p.hero_from, p.hero_via, p.hero_to):
        assert ot.contrast_ratio(p.login_text, hero) >= 4.5           # giris sayfasi metni
    assert ot.contrast_ratio(p.login_button_text, p.login_button) >= 7


def test_contrast_ratio_reference_values():
    assert ot.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert ot.contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0)


@pytest.mark.parametrize("theme", THEMES)
def test_streamlit_config_palette_matches_ofm_palette(theme):
    """
    Streamlit widget iclerini ve canvas ile cizilen tablolari config.toml'daki paletle boyar; orasi
    OFM paletinden sapiyorsa uygulama ici secim "her pikseli" kontrol edemez (Faz 13 hatasinin kokeni).
    """
    config = tomllib.loads(CONFIG_TOML.read_text(encoding="utf-8"))
    section = config["theme"][theme]
    expected = ot.streamlit_theme_options(theme)
    assert section.get("sidebar") == expected["sidebar"]
    assert {k: v for k, v in section.items() if k != "sidebar"} == \
        {k: v for k, v in expected.items() if k != "sidebar"}


def test_streamlit_config_defines_both_themes_and_no_forced_base():
    config = tomllib.loads(CONFIG_TOML.read_text(encoding="utf-8"))
    assert set(config["theme"]) >= {"dark", "light"}
    assert "base" not in config["theme"]          # tek tema zorlanirsa uygulama ici secim calismaz


@pytest.mark.parametrize("theme", THEMES)
def test_theme_sync_script_pins_streamlit_to_the_chosen_theme(theme):
    """localStorage'daki Streamlit tema anahtarini uygulamanin secimine yazar; yenileme istege bagli."""
    name = ot.STREAMLIT_THEME_NAMES[theme]
    quiet, reloading = ot.theme_sync_script(theme), ot.theme_sync_script(theme, reload=True)
    for script in (quiet, reloading):
        assert script.startswith("<script>") and script.endswith("</script>")
        assert f'var want="{name}";' in script
        assert 'stActiveTheme-"+window.location.pathname+"-v2' in script
        assert "localStorage.setItem" in script and "try{" in script
        assert "\n" not in script                                  # tek satir: Streamlit HTML'i bolmesin
    assert "location.reload()" in reloading
    assert "if(!false){return;}" in quiet                          # oturum aciksa sayfa yenilenmez
    assert "sessionStorage" in reloading                           # yenileme dongusu nobetcisi
    assert ot.theme_sync_script("<script>") == ot.theme_sync_script(ot.DEFAULT_THEME)


@pytest.mark.parametrize("theme", THEMES)
def test_css_targets_streamlit_164_selectors_not_dead_baseweb_ones(theme):
    """
    Streamlit 1.64 BaseWeb kullanmaz; [data-baseweb="..."] secicileri hicbir ogeyle eslesmez.
    Widget'lar data-testid ve ARIA rolleriyle hedeflenir -- kapsam burada kilitlenir.
    """
    for login in (False, True):
        assert "data-baseweb" not in ot.theme_css(theme, login=login)
    css = ot.theme_css(theme)
    for selector in (
        '[data-testid="stSelectbox"] div[role="group"]',            # acilir liste (bildirilen hata)
        '[data-testid="stMultiSelect"] div[role="group"]',
        '[data-testid="stTextInputRootElement"]',
        '[data-testid="stTextAreaRootElement"]',
        '[data-testid="stNumberInputContainer"]',
        '[data-testid="stDateInput"] div[role="group"]',
        '[data-testid="stTextInputField"]',
        '[data-testid="stMultiSelectTagsContainer"]',
        '[data-testid="stTab"]',
        '[data-testid="stExpander"]',
        '[data-testid="stMetric"]',
        '[data-testid="stDataFrame"]',
        '[data-testid="stProgressBarTrack"]',
        '[data-testid="stSliderThumbValue"]',
        '[data-testid="stFileUploaderDropzone"]',
        '[data-testid="stTableStyledTable"]',
        '[data-testid="stToolbar"]',
        '[data-testid="stSidebarContent"]',
        '[data-testid^="stBaseButton-primary"]',
        '[data-testid^="stBaseButton-secondary"]',
        '[data-testid="stBaseButton-tertiary"]',
        '[role="listbox"]',                                         # acilir liste penceresi (portal)
        '[role="option"]',
        '[role="tooltip"]',
        "::placeholder",                                            # yer tutucu okunur kalmali
        "button:disabled",
    ):
        assert selector in css, f"{theme}: CSS {selector} secicisini kapsamiyor"


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
    assert ot.normalize_theme("☀️ OFM Light") == "light" and ot.normalize_theme("light") == "light"
    assert ot.normalize_theme("☀️ FM Light") == "light" and ot.normalize_theme("⚽ FM Dark") == "dark"   # eski etiket
    assert ot.normalize_theme(None) == ot.normalize_theme("<script>") == "dark"
    assert ot.theme_css("<script>") == ot.theme_css("dark")


@pytest.mark.parametrize("theme", THEMES)
def test_login_hero_is_a_drawing_not_a_photo(theme):
    html = ot.login_hero_html(theme)
    assert "<svg" in html and "<img" not in html and "http" not in html.replace("http://www.w3.org/2000/svg", "")
    assert "\n" not in html and 'aria-hidden="true"' in html


def test_official_brand_title_theme_labels_and_skin_colours():
    assert ot.BRAND_TITLE == "Online Football Manager (OFM)"
    assert ot.THEME_LABELS == {"dark": "⚽ OFM Dark", "light": "☀️ OFM Light"}
    dark, light = ot.PALETTES["dark"], ot.PALETTES["light"]
    assert (dark.bg, dark.panel, dark.text, dark.accent) == ("#121824", "#1e2538", "#ffffff", "#FFCD00")
    assert (light.bg, light.panel, light.text) == ("#f4f6f9", "#ffffff", "#1e293b")


def test_brand_and_headlines():
    assert "OFM" in ot.brand_html() and "Online Football Manager" in ot.brand_html()
    assert 'lang="en"' in ot.brand_html()                                  # marka Ingilizce buyuk harfle
    assert "lang', 'tr'" in ot.LANG_SCRIPT and "<script>" in ot.LANG_SCRIPT    # Turkce buyuk harf: GİRİŞ
    assert "giriş" in ot.login_headline_html("login").lower()
    assert "kaydı" in ot.login_headline_html("register")
    assert "&lt;b&gt;" in ot.panel_title_html("<b>") and "&lt;x&gt;" in ot.stat_strip_html([("<x>", 1)])
    assert not re.search(r"cm-green|Tahoma", ot.theme_css("dark"))


@pytest.mark.parametrize("theme", THEMES)
def test_login_board_is_animated_but_respects_reduced_motion(theme):
    board, css = ot.login_hero_html(theme), ot.theme_css(theme, login=True)
    assert board.count('class="ofm-p"') + board.count('class="ofm-p d"') == 10 and "ofm-ball" in board and board.count("ofm-run") == 3   # 10 saha oyuncusu
    assert "MAÇ PLANI" in board and board.count("<span>") == 2                                      # donen plan karti
    assert "@keyframes ofm-ball" in css and "prefers-reduced-motion:reduce" in css


def test_login_intro_and_steps():
    intro = ot.login_intro_html()
    assert "Taktiği sen kur" in intro and "ÜCRETSİZ" in intro
    steps = ot.login_steps_html()
    assert steps.count('class="ofm-step"') == 6 and "Hesap aç" in steps and "<script" not in steps
    assert ot.contrast_ratio(ot.PALETTES[ot.THEME_LIGHT].login_button_text, ot.PALETTES[ot.THEME_LIGHT].login_button) >= 7
