"""
Taktik Merkezi sekmesi uctan uca testleri -- Streamlit AppTest.

Mac onu raporu (form, aralarindaki maclar, eksikler, yorum), rakip gozlem raporu (tahmini diziliş ve
ilk 11, gozlemci guvenilirligi) ve kadro planlayici (mevki derinligi, sozlesme/yas projeksiyonu)
salt okunur cizilir; veritabanina hicbir sey yazilmaz. Kenar cubugunda menajer unvani gorunur.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import menu  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _html,
    _reseed,
    _set_user_team,
    _texts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed()


def _section(at, label: str):
    at.radio(key="prep_section").set_value(label)
    at.run()
    assert not at.exception, at.exception
    return at


def _world_fingerprint():
    from sqlalchemy import func, select

    from database import session_scope
    from models import Fixture, GameState, Player

    with session_scope() as db:
        return (db.get(GameState, 1).current_week, db.scalar(select(func.sum(Player.morale))),
                db.scalar(select(func.count()).select_from(Fixture).where(Fixture.home_score.is_not(None))))


def test_match_preview_scout_report_and_squad_planner_render_read_only():
    import web_app

    team_id = _set_user_team(TEAM)
    at = _app(seed="4", page="taktik")
    assert "taktik" in menu(at) and at.session_state["nav_page"] == "taktik"
    before = _world_fingerprint()

    html = _html(at)                                                   # varsayilan: mac onu raporu
    assert "Aralarındaki maçlar" in html and "Son maçlar" in _texts(at.markdown)
    verdict = _expected_preview(team_id)[3]
    assert verdict and any(verdict in i.value for i in at.info)

    _section(at, web_app.PREP_SCOUT)
    assert "Gözlem raporu" in _html(at)
    xi = next(df.value for df in at.dataframe if "Görev" in df.value.columns)
    assert len(xi) == 11 and "Mevcut yetenek" in xi.columns and not {"overall_rating", "Overall"} & set(xi.columns)
    assert any("tahmin" in c.value.lower() for c in at.caption)

    _section(at, web_app.PREP_PLANNER)
    assert any("Sözleşme bitişi" in df.value.columns for df in at.dataframe)
    assert any(label in e.label for e in at.expander for label in ("Kaleci", "Defans", "Orta saha", "Forvet"))
    assert _world_fingerprint() == before                             # salt okunur


def _expected_preview(team_id: int):
    import preview_views
    from database import session_scope

    with session_scope() as db:
        fixture = preview_views.next_preview_fixture(db, team_id)
        preview = preview_views.build_match_preview(db, fixture.id, team_id)
        return fixture.id, preview.title, f"Hafta {preview.week}", preview.verdict


def test_preview_follows_the_next_fixture_after_a_played_week():
    from html import escape

    team_id = _set_user_team(TEAM)
    at = _app(seed="4", page="taktik")
    first_id, first_title, first_week, _ = _expected_preview(team_id)
    assert escape(first_title) in _html(at) and any(first_week in c.value for c in at.caption)

    _click(at, "nav_continue")                                        # Faz 13I: menudeki Devam, sayfa degismez
    second_id, second_title, second_week, _ = _expected_preview(team_id)
    assert second_id != first_id
    assert escape(second_title) in _html(at) and any(second_week in c.value for c in at.caption)


def test_sidebar_shows_manager_title_and_level():
    _set_user_team(TEAM)
    at = _app(seed="4")
    captions = _texts(at.sidebar.caption)
    assert "Menajer tanınırlığı" in captions and "seviye" in captions and "/10" in captions
