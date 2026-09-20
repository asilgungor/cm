"""
Faz 14F: kulup sayfasi (?sayfa=takim&id=..) -- kulup renginde bant, sekmeler Kadro / Fikstur / Genel Bilgi / Tarih.
Kendi kulubunde sozlesme yili ve kesin deger; baska kulupte sozlesme "?" ve TEK SIS MODELI (K12: motorun 1-99 sayisi
hicbir hucrede yok). Tarih SeasonStanding'i gosterir; fikstur rakibi tiklanir.

    TEST_DB_NAME=fm_db_test_14fg python -m pytest -q -p no:cacheprovider tests/test_web_club_page.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import page  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _db_available,
    _query,
    _reseed,
    _set_user_team,
    _team,
)
from tests.test_web_links import select  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"
RIVAL = "Madrid Blancos"


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed(mode=None)


def _open_club(at, team_id: int):
    import nav_view

    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.CLUB_PAGE, team_id
    at.run()
    assert not at.exception, at.exception
    return at


def _tab(at, label: str):
    at.button_group(key="club_tab").set_value(label)
    at.run()
    assert not at.exception, at.exception
    return at


def _frame(at, key: str):
    return next(d for d in at.dataframe if d.key == key).value


def _html(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def test_own_club_shows_contract_years_and_other_clubs_show_question_marks():
    import club_view

    _set_user_team(TEAM)
    own_id = _query(lambda db: _team(db, TEAM).id)
    rival_id = _query(lambda db: _team(db, RIVAL).id)
    ratings = _query(lambda db: {str(p.overall_rating) for p in _team(db, RIVAL).players})
    at = _open_club(_app(), own_id)
    assert list(at.button_group(key="club_tab").options) == list(club_view.CLUB_TABS)
    assert f'<span class="t">{TEAM}</span>' in _html(at)                     # kulup renginde bant
    own = _frame(at, "lk_club_squad")
    assert all(str(v).endswith("yıl") or v == "Son sezon" for v in own["Sözleşme"])
    assert own["Değer (EUR)"].dtype.kind in "iu"                                 # kendi kulubu: sayisal (sirali)

    at = _open_club(at, rival_id)
    rival = _frame(at, "lk_club_squad")
    assert set(rival["Sözleşme"]) == {"?"}                                       # baska lig: bilgi %0
    assert set(rival["Mevcut yetenek"]) == {"?"} and set(rival["Değer (EUR)"]) == {"?"}
    cells = {str(v) for col in rival.columns if col not in ("Yaş",) for v in rival[col]}
    assert not cells & ratings                                                   # motorun 1-99 sayisi yok
    info = _html(_tab(at, "Genel Bilgi"))
    assert "Transfer bütçesi" not in info and "İtibar" in info                   # K12: baska kulubun defteri kapali
    assert "Transfer bütçesi" in _html(_tab(_open_club(at, own_id), "Genel Bilgi"))


def test_history_tab_shows_past_season_standings():
    from database import session_scope
    from models import SeasonStanding, Team

    _set_user_team(TEAM)
    with session_scope() as db:
        team = db.query(Team).filter(Team.name == RIVAL).one()
        db.add(SeasonStanding(season=1, league_id=team.league_id, team_id=team.id, team_name=team.name,
                              position=2, points=11))
        rival_id = team.id
    at = _tab(_open_club(_app(), rival_id), "Tarih")
    seasons = _frame(at, "lk_club_seasons")
    assert list(seasons["Sezon"]) == [1] and list(seasons["Sıra"]) == ["2."] and list(seasons["Puan"]) == [11]


def test_fixture_opponent_cell_opens_the_rival_club_page():
    import nav_view

    _set_user_team(TEAM)
    own_id = _query(lambda db: _team(db, TEAM).id)
    at = _tab(_open_club(_app(), own_id), "Fikstür")
    fixtures = _frame(at, "lk_club_fixtures")
    assert len(fixtures) >= 1
    opponent = at.session_state["lk_club_fixtures__links"]["clubs"]["Rakip"][0]
    select(at, "lk_club_fixtures", 0, "Rakip")
    assert page(at) == nav_view.CLUB_PAGE and at.session_state[nav_view.PARAM_KEY] == opponent != own_id
    assert _query(lambda db: db.get(__import__("models").Team, opponent).name) in _html(at)


def test_nations_and_clubs_list_opens_the_club_page():
    import club_view
    import nav_view
    import player_view as pv

    _set_user_team(TEAM)
    at = _app(page="ulkeler")
    ids = at.session_state[pv.table_ids_key(club_view.CLUBS_KEY)]
    select(at, club_view.CLUBS_KEY, 0)
    assert page(at) == nav_view.CLUB_PAGE and at.session_state[nav_view.PARAM_KEY] == ids[0]
    _open_club(at, 999_999)
    assert "Kulüp bulunamadı" in _html(at) and at.button(key="club_nf_find")
