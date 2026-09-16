"""
Devler Arenasi web arayuzu uctan uca testleri (8. Asama) -- Streamlit AppTest.

Ilk giris mod secimi, interaktif kura (top top), otomatik kura, format degisimi,
turnuva agaci, kupa haftasinin oynatilmasi, sampiyon ve penalti ayrintilari.
Her testten once dunya yeniden kurulur (test_web_app ile ayni yaklasim).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    APP,
    AppTest,
    _click,
    _db_available,
    _html,
    _query,
    _reseed,
    _set_user_team,
    _texts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]


# Varsayilan: kariyer modu secilmis temiz dunya. Bazi testler farkli baslangic ister.
WORLD_MODES = {
    "test_first_entry_offers_two_modes_and_career_opens_seven_tabs": None,
    "test_tournament_mode_limits_tabs_and_team_list_to_participants": None,
    "test_playing_cup_week_updates_bracket_tables_and_live_match": "TOURNAMENT_MODE",
    "test_full_tournament_in_browser_crowns_champion": "TOURNAMENT_MODE",
}


@pytest.fixture(autouse=True)
def fresh_world(request):
    _reseed(mode=WORLD_MODES.get(request.node.name, "CAREER_MODE"))
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed(mode=None)


def _run(seed: str | None = None):
    at = AppTest.from_file(APP, default_timeout=120)
    if seed is not None:
        at.session_state["career_seed"] = seed
    at.run()
    assert not at.exception, at.exception
    return at


def _tournament(db):
    from career_manager import CareerManager
    return CareerManager(db).tournaments.current()


def _draw_steps(db) -> int:
    t = _tournament(db)
    return len(t.draw_state.get("steps", [])) if t and t.draw_state else 0


# ---------------------------------------------------------------------------
# Ilk giris: oyun modu
# ---------------------------------------------------------------------------

def test_first_entry_offers_two_modes_and_career_opens_seven_tabs():
    at = _run()
    assert len(at.tabs) == 0
    assert "Oyun modunu seç" in _texts(at.markdown)
    assert at.button(key="mode_career") and at.button(key="mode_tournament")

    _click(at, "mode_career")
    assert _query(lambda db: db.get(__import__("models").GameState, 1).game_mode.value) == "CAREER_MODE"
    assert [t.label for t in at.tabs][-2:] == ["⭐ Devler Arenası", "👥 Teknik Heyet"]
    assert len(at.tabs) == 7


def test_tournament_mode_limits_tabs_and_team_list_to_participants():
    at = _run()
    _click(at, "mode_tournament")
    assert [t.label for t in at.tabs] == ["🏟️ Canlı Maç", "⭐ Devler Arenası", "📋 Kadro & Taktik", "👥 Teknik Heyet"]

    participants = _query(lambda db: sorted(e.team.name for e in _tournament(db).entries))
    assert len(participants) == 16
    assert sorted(at.selectbox(key="sb_team").options) == participants
    assert "Turnuva Modu" in _texts(at.sidebar.caption)


def test_mode_change_button_locks_after_first_week():
    _set_user_team("Istanbul Lions")
    at = _run(seed="2")
    assert not at.button(key="sb_change_mode").disabled
    _click(at, "arena_draw_all")
    _click(at, "arena_play")
    assert at.button(key="sb_change_mode").disabled


# ---------------------------------------------------------------------------
# Interaktif kura
# ---------------------------------------------------------------------------

def test_ball_by_ball_draw_glows_persists_and_completes():
    _set_user_team("Madrid Blancos")
    at = _run(seed="5")
    html = _html(at)
    assert "cm-b-" in html and "Kura çekimi" in _texts(at.markdown)
    assert at.button(key="arena_ball_0")

    _click(at, "arena_ball_0")                                   # 2. torbadan ilk top
    assert _query(_draw_steps) == 1
    assert "cm-b-glow" in _html(at)

    _click(at, "arena_ball_0")                                   # rakibi: eslesme tamamlandi
    assert _query(_draw_steps) == 2
    first_pair = _query(lambda db: _tournament(db).draw_state["steps"][:2])
    names = _query(lambda db: {e.team_id: e.team.name for e in _tournament(db).entries})
    html = _html(at)
    assert "cm-b-slot cm-b-glow" in html
    for step in first_pair:
        assert names[step["team_id"]] in html

    # Yeni oturum (sayfa yenileme) kaldigi yerden devam eder
    at = _run(seed="5")
    assert _query(_draw_steps) == 2
    _click(at, "arena_draw_all")
    assert any("Kura tamamlandı" in s.value for s in at.success)
    status, ties = _query(lambda db: (_tournament(db).status.value, len(_tournament(db).ties)))
    assert (status, ties) == ("RUNNING", 8)
    html = _html(at)
    assert all(name in html for name in names.values())          # agacta 16 takim
    assert "arena_draw_all" not in [b.key for b in at.button]    # kura bitti: kura dugmeleri yok


def test_format_can_switch_to_groups_only_before_first_ball():
    _set_user_team("München Roten")
    at = _run(seed="3")
    at.radio(key="arena_format").set_value("groups")
    at.run()
    assert not at.exception
    assert _query(lambda db: _tournament(db).format) == "groups"
    assert any("Gruplar" in s.value for s in at.success)

    _click(at, "arena_ball_0")
    assert not [r for r in at.radio if r.key == "arena_format"]  # kura basladi: format kilitli


# ---------------------------------------------------------------------------
# Kupa haftasi, agac, krallik, canli izleme, sampiyon
# ---------------------------------------------------------------------------

def test_playing_cup_week_updates_bracket_tables_and_live_match():
    _set_user_team("Manchester Blue")
    at = _run(seed="11")
    _click(at, "arena_draw_all")
    _click(at, "arena_play")

    assert any("1. hafta oynandı" in s.value for s in at.success)
    assert at.session_state["last_user_cup_result"] is not None
    assert at.session_state["last_user_result"] is None           # turnuva modunda lig maci yok
    texts = _texts(at.markdown)
    assert "Turnuva ağacı" in texts and "Gol krallığı" in texts and "Sakatlar ve cezalılar" in texts
    played = _query(lambda db: sum(1 for f in _tournament(db).fixtures if f.is_played))
    assert played == 8

    at.radio(key="live_mode").set_value("Son maçımı izle")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_start")
    html = _html(at)
    assert "MAÇ SONU" in html and 'viewBox="-4 -10 113 86"' in html


def test_full_tournament_in_browser_crowns_champion():
    _set_user_team("Paris Rouge-Bleu")
    at = _run(seed="17")
    _click(at, "arena_draw_all")
    for _ in range(7):
        if at.button(key="arena_play").disabled:
            break
        _click(at, "arena_play")
    assert at.button(key="arena_play").disabled

    champion = _query(lambda db: _tournament(db).champion.name)
    html = _html(at)
    assert "cm-b-banner" in html and champion in html
    assert any("şampiyonu" in s.value for s in at.success) or "Devler Arenası Şampiyonu" in html

    shootouts = _query(lambda db: [f.id for f in _tournament(db).fixtures if f.home_penalties is not None])
    labels = [e.label for e in at.expander]
    assert len([lbl for lbl in labels if lbl.startswith("🥅 Penaltılar")]) == min(len(shootouts), 6)
    assert at.button(key="arena_new_season")
    _click(at, "arena_new_season")
    assert _query(lambda db: (_tournament(db).season, _tournament(db).status.value)) == (2, "DRAW")


def test_friendly_knockout_toggle_plays_without_errors():
    at = _run()
    at.select_slider(key="live_speed").set_value("Anında")
    at.selectbox(key="live_home").set_value("Madrid Blancos")
    at.selectbox(key="live_away").set_value("Catalonia Blaugrana")
    at.toggle(key="live_knockout").set_value(True)
    at.text_input(key="live_seed").set_value("4")
    at.run()
    _click(at, "live_start")
    assert "MAÇ SONU" in _html(at)
    assert any("Eleme kuralları" in c.value for c in at.caption)
