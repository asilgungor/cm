"""
Faz 14F: Bul (CM 01/02 "Find") -- Turkce aksan / buyuk-kucuk harf duyarsiz arama, siralama (tam > on ek > icerir),
sonuca tik -> oyuncu / kulup sayfasi, baska kulubun akademisi gizli, sis (K12), 3.000 satirlik aramada <= 300 ms.

    TEST_DB_NAME=fm_db_test_14fg python -m pytest -q -p no:cacheprovider tests/test_web_find.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, page  # noqa: E402
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


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed(mode=None)


def _rename(team_name: str, index: int, new_name: str) -> int:
    from database import session_scope
    from models import Player

    with session_scope() as db:
        pid = _team(db, team_name).players[index].id
        db.get(Player, pid).name = new_name
        return pid


def _search(at, text: str, tab: str | None = None):
    import find_view

    if page(at) != "bul":
        goto(at, "bul")
    at.text_input(key=find_view.QUERY_KEY).set_value(text)
    at.run()
    if tab is not None:
        at.button_group(key=find_view.TAB_KEY).set_value(tab)
        at.run()
    assert not at.exception, at.exception
    return at


def _frame(at, key: str):
    return next(d for d in at.dataframe if d.key == key).value


# ---------------------------------------------------------------------------
# 1) Saf: eslesme ve siralama
# ---------------------------------------------------------------------------

def test_match_rank_is_accent_and_case_insensitive():
    import find_view as fv
    from club_directory import plain_key

    for query in ("istanbul", "İSTANBUL", "Istanbul", "ıstanbul"):
        assert fv.match_rank(plain_key("İstanbul Başakşehir"), plain_key(query)) == 1, query
    assert fv.match_rank(plain_key("Şükrü Öztürk"), plain_key("sukru")) == 1                     # tam kelime
    assert fv.match_rank(plain_key("Şükrü Öztürk"), plain_key("oztu")) == 2                      # kelime on eki
    assert fv.match_rank(plain_key("Şükrü Öztürk"), plain_key("ztür")) == 3                      # kelime ici
    assert fv.match_rank(plain_key("Göztepe"), plain_key("goztepe")) == 0                        # tam ad
    assert fv.match_rank(plain_key("Göztepe"), plain_key("xyz")) is None
    names = ["Emre Can", "Can Bartu", "Candan Yıldız", "Ercan Tok"]
    assert fv.rank_names(names, "can", str) == ["Can Bartu", "Emre Can", "Candan Yıldız", "Ercan Tok"]


def test_three_thousand_player_search_is_fast(monkeypatch):
    """14F kabul: ~3.000 oyunculuk listede arama <= 300 ms (arama Python'da; havuz tek sorgu)."""
    import find_view as fv
    from club_directory import plain_key

    first = ["Ahmet", "Mehmet", "Şükrü", "İsmail", "Çağlar", "Oğuz", "Ümit", "Gökhan", "Barış", "Emre"]
    last = ["Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Yıldız", "Öztürk", "Aydın", "Arslan", "Doğan"]
    pool = [fv.PlayerHit(i, f"{first[i % 10]} {last[(i // 10) % 10]} {i}", "MID", 20 + i % 15, i % 114,
                         f"Kulüp {i % 114}", 1 + i % 6, "Türkiye", 1 + i % 4, i % 17 == 0,
                         plain_key(f"{first[i % 10]} {last[(i // 10) % 10]} {i}"))
            for i in range(3000)]
    monkeypatch.setattr(fv, "player_pool", lambda db, viewer: pool)
    started = time.perf_counter()
    for query in ("sukru", "OZTURK", "yil", "ismail", "gokhan"):
        hits = fv.find_players(None, query, fv.LIMIT)
        assert hits
    per_search = (time.perf_counter() - started) / 5
    assert per_search <= 0.3, per_search
    assert all("Şükrü" in h.name for h in fv.find_players(None, "sukru", fv.LIMIT))
    assert len(fv.find_players(None, "a", fv.LIMIT)) == 0                                 # en az 2 harf


# ---------------------------------------------------------------------------
# 2) Sayfa: arama, tiklama, akademi gizliligi, sis
# ---------------------------------------------------------------------------

def test_find_page_player_and_club_search_open_pages():
    import find_view
    import nav_view

    _set_user_team(TEAM)
    pid = _rename("Madrid Blancos", 0, "Şükrü Öztürk")
    at = _search(_app(), "sukru")
    frame = _frame(at, find_view.PLAYERS_KEY)
    assert "Şükrü Öztürk" in list(frame["Oyuncu"])
    ids = at.session_state["lk_find_players__links"]["players"]
    select(at, find_view.PLAYERS_KEY, ids.index(pid))
    assert page(at) == nav_view.PLAYER and at.session_state[nav_view.PARAM_KEY] == pid

    at = _search(at, "ŞÜKRÜ")                                                  # buyuk harf + aksan
    assert "Şükrü Öztürk" in list(_frame(at, find_view.PLAYERS_KEY)["Oyuncu"])
    row = at.session_state["lk_find_players__links"]["players"].index(pid)
    club = at.session_state["lk_find_players__links"]["clubs"]["Kulüp"][row]
    select(at, find_view.PLAYERS_KEY, row, "Kulüp")
    assert page(at) == nav_view.CLUB_PAGE and at.session_state[nav_view.PARAM_KEY] == club

    results = []
    for query in ("istanbul", "İSTANBUL", "Istanbul"):
        at = _search(at, query, find_view.TAB_CLUB)
        results.append(list(_frame(at, find_view.CLUBS_KEY)["Kulüp"]))
    assert results[0] == results[1] == results[2] and TEAM in results[0]
    select(at, find_view.CLUBS_KEY, results[0].index(TEAM), "Kulüp")
    assert page(at) == nav_view.CLUB_PAGE
    assert at.session_state[nav_view.PARAM_KEY] == _query(lambda db: _team(db, TEAM).id)


def test_other_clubs_academy_is_never_found_and_fog_is_kept():
    import find_view
    from career_manager import CareerManager
    from database import session_scope

    _set_user_team(TEAM)
    with session_scope() as db:
        cm = CareerManager(db)
        other = next(p for t in cm.teams() if t.name != TEAM for p in cm.academy_players(t))
        other.name = "Zzakademi Başka"
        own = next(p for p in cm.academy_players(cm.find_team(TEAM)))
        own.name = "Zzakademi Kendi"
        rival = cm.find_team("Madrid Blancos").players[0]
        rival.name, rating = "Zzrakip Oyuncu", rival.overall_rating
    at = _search(_app(), "zzakademi")
    names = list(_frame(at, find_view.PLAYERS_KEY)["Oyuncu"])
    assert names == ["Zzakademi Kendi"]                                        # baska kulubun akademisi hic yok
    at = _search(at, "zzrakip")
    frame = _frame(at, find_view.PLAYERS_KEY)
    assert list(frame["Mevcut yetenek"]) == ["?"] and list(frame["Değer (EUR)"]) == ["?"]   # baska lig: bilgi %0
    assert str(rating) not in " ".join(str(v) for v in frame.iloc[0].tolist())


def test_find_lists_nations_and_leagues_without_a_query():
    import find_view
    import nav_view

    _set_user_team(TEAM)
    at = _app(page="bul")
    at.button_group(key=find_view.TAB_KEY).set_value(find_view.TAB_NATION)
    at.run()
    nations = list(_frame(at, find_view.NATIONS_KEY)["Ülke"])
    assert "Türkiye" in nations
    select(at, find_view.NATIONS_KEY, nations.index("Türkiye"), "Ülke")
    assert page(at) == nav_view.NATION and at.session_state[nav_view.PARAM_KEY] == "Türkiye"
    goto(at, "bul")
    at.button_group(key=find_view.TAB_KEY).set_value(find_view.TAB_LEAGUE)
    at.run()
    leagues = _frame(at, find_view.LEAGUES_KEY)
    assert len(leagues) >= 2
    league_id = at.session_state["lk_find_leagues__links"]["leagues"]["Lig"][0]
    select(at, find_view.LEAGUES_KEY, 0, "Lig")
    assert page(at) == nav_view.TABLE and at.session_state[nav_view.PARAM_KEY] == league_id
    assert at.session_state["lg_league"] == league_id
