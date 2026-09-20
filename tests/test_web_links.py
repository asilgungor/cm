"""
Faz 14F: tiklanabilir dunya -- bagli tablolar (links_view.link_table), parametreli sayfalar (oyuncu / kulup / ulke),
adres (?sayfa=oyuncu&id=..) ve Geri / Ileri gecmisi. Gercek web_app.py AppTest ile; tablo tiklamasi tarayicinin
gonderecegi secim JSON'uyla surulur (AppTest'te dataframe secimi yok).

    TEST_DB_NAME=fm_db_test_14fg python -m pytest -q -p no:cacheprovider tests/test_web_links.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, page  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    APP,
    _app,
    _click,
    _db_available,
    _login,
    _query,
    _reseed,
    _set_user_team,
    _team,
)

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


def select(at, key: str, row: int, column: str | None = None):
    """Tarayicidaki tek tik: satir (column None) ya da hucre ([satir, sutun]) secimi."""
    from streamlit.proto.WidgetStates_pb2 import WidgetState

    frame = next(d for d in at.dataframe if d.key == key)
    selection = ({"rows": [], "columns": [], "cells": [[row, column]]} if column is not None
                 else {"rows": [row], "columns": [], "cells": []})
    states = at._tree.get_widget_states()
    states.widgets.append(WidgetState(id=frame.proto.id, string_value=json.dumps({"selection": selection})))
    at._run(states)
    assert not at.exception, at.exception
    return at


def _param(at):
    import nav_view

    return at.session_state[nav_view.PARAM_KEY] if nav_view.PARAM_KEY in at.session_state else None


def _url(at, key: str):
    value = at.query_params.get(key)
    return value[0] if isinstance(value, list) and value else value


def _keys_now(at) -> set[str]:
    return {d.key for d in at.dataframe if d.key}


def _play_week():
    from career_manager import CareerManager
    from database import session_scope

    with session_scope() as db:
        cm = CareerManager(db)
        cm.run_ai_transfer_window = lambda: []
        cm.play_week()


# ---------------------------------------------------------------------------
# 1) En az 12 bagli yuzey: her biri gercekten cizilir, tiklamasi dogru sayfaya gider
# ---------------------------------------------------------------------------

def test_at_least_twelve_linked_surfaces_render_and_route():
    import links_view as lk
    import nav_view

    assert len(lk.LINK_SURFACES) >= 12
    _set_user_team(TEAM)
    _play_week()                                                  # istatistik / sonuc tablolari dolsun
    at = _app(page="puan-durumu")
    seen: set[str] = set()

    def collect():
        for key in _keys_now(at):
            if any(key == s or (s.endswith("_") and key.startswith(s)) for s in lk.LINK_SURFACES):
                seen.add(key)

    collect()                                                     # Lig sayfasi: tablo
    for tab in ("Hafta hafta", "İstatistikler", "Tarih"):
        at.button_group(key="comp_tab").set_value(tab)
        at.run()
        assert not at.exception, at.exception
        collect()
    for slug in ("fikstur", "kulup", "taktik", "bul", "haberler"):
        goto(at, slug)
        collect()
    goto(at, "taktik")
    at.radio(key="prep_section").set_value("Rakip gözlem raporu")
    at.run()
    collect()
    at.radio(key="prep_section").set_value("Kadro planlayıcı")
    at.run()
    collect()
    goto(at, "haberler")
    at.radio(key="world_section").set_value("Transfer kayıtları")
    at.run()
    collect()
    goto(at, "transfer")
    at.radio(key="tc_section").set_value("Oyuncularım")
    at.run()
    collect()
    goto(at, "bul")
    at.text_input(key="fd_query").set_value("a")
    at.run()
    at.text_input(key="fd_query").set_value("an")
    at.run()
    collect()
    for tab in ("Kulüp", "Lig", "Ülke"):
        at.button_group(key="fd_tab").set_value(tab)
        at.run()
        collect()
    rival = _query(lambda db: _team(db, RIVAL).id)
    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.CLUB_PAGE, rival
    at.run()
    collect()
    at.button_group(key="club_tab").set_value("Fikstür")
    at.run()
    collect()
    assert len(seen) >= 12, sorted(seen)

    # Tiklama yonlendirmesi: lig tablosunda kulup hucresi -> kulup sayfasi; gol kralligi satiri -> oyuncu sayfasi
    goto(at, "puan-durumu")
    at.button_group(key="comp_tab").set_value("Tablo")
    at.run()
    clubs = at.session_state[lk.links_key("lk_standings")]["clubs"]["Kulüp"]
    select(at, "lk_standings", 2, "Kulüp")
    assert page(at) == nav_view.CLUB_PAGE and _param(at) == clubs[2]
    assert at.session_state["lk_standings"]["selection"]["cells"] == []            # ayni hucre yeniden tiklanir
    goto(at, "puan-durumu")
    at.button_group(key="comp_tab").set_value("İstatistikler")
    at.run()
    scorer = at.session_state[lk.links_key("lk_comp_goals")]["players"][0]
    select(at, "lk_comp_goals", 0)
    assert page(at) == nav_view.PLAYER and _param(at) == scorer
    assert "pv-sheet" in "\n".join(str(m.value) for m in at.markdown)


# ---------------------------------------------------------------------------
# 2) Geri / Ileri parametreli sayfalari gezer; adres parametreyi tasir
# ---------------------------------------------------------------------------

def test_back_and_forward_walk_player_club_player():
    import nav_view

    _set_user_team(TEAM)
    rival_id = _query(lambda db: _team(db, RIVAL).id)
    pid = _query(lambda db: _team(db, RIVAL).players[0].id)
    other = _query(lambda db: _team(db, RIVAL).players[1].id)
    at = _app(page="kadro")
    # oyuncu -> (Eylem) kulup sayfasi -> kadrodan baska oyuncu
    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.PLAYER, pid
    at.run()
    assert page(at) == nav_view.PLAYER and _url(at, "sayfa") == "oyuncu" and _url(at, "id") == str(pid)
    _click(at, "pv_go_club")
    assert page(at) == nav_view.CLUB_PAGE and _param(at) == rival_id and _url(at, "id") == str(rival_id)
    ids = at.session_state["lk_club_squad__links"]["players"]
    select(at, "lk_club_squad", ids.index(other), "Oyuncu")
    assert page(at) == nav_view.PLAYER and _param(at) == other
    history = at.session_state[nav_view.HISTORY_KEY]
    assert history[-3:] == [f"oyuncu|{pid}", f"takim|{rival_id}", f"oyuncu|{other}"]
    _click(at, "nav_foot_back")
    assert page(at) == nav_view.CLUB_PAGE and _param(at) == rival_id
    _click(at, "nav_foot_back")
    assert page(at) == nav_view.PLAYER and _param(at) == pid and _url(at, "id") == str(pid)
    _click(at, "nav_foot_fwd")
    assert page(at) == nav_view.CLUB_PAGE and _param(at) == rival_id
    _click(at, "nav_menu_club")                                      # menu: parametresiz sayfa, adres temizlenir
    assert page(at) == nav_view.SQUAD and _param(at) is None and _url(at, "id") is None


def test_player_page_opens_from_the_url_and_bad_ids_are_harmless():
    from streamlit.testing.v1 import AppTest

    import nav_view

    _set_user_team(TEAM)
    pid = _query(lambda db: _team(db, RIVAL).players[0].id)
    name = _query(lambda db: _team(db, RIVAL).players[0].name)

    def fresh(query: dict):
        at = AppTest.from_file(APP, default_timeout=90)
        _login(at)
        for key, value in query.items():
            at.query_params[key] = value
        at.run()
        assert not at.exception, at.exception
        return at

    at = fresh({"sayfa": "oyuncu", "id": str(pid)})
    text = "\n".join(str(m.value) for m in at.markdown)
    assert page(at) == nav_view.PLAYER and _param(at) == pid and f"{name} ({RIVAL})" in text and "pv-sheet" in text
    for bad in ("999999", "abc", "-4", "0"):
        at = fresh({"sayfa": "oyuncu", "id": bad})
        text = "\n".join(str(m.value) for m in at.markdown)
        if bad == "999999":
            assert page(at) == nav_view.PLAYER and "Oyuncu bulunamadı" in text     # gecerli bicim, olmayan kayit
            assert at.button(key="pv_nf_find")
        else:
            assert page(at) == nav_view.FIND                                         # gecersiz parametre: Bul
    at = fresh({"sayfa": "takim", "id": "999999"})
    assert "Kulüp bulunamadı" in "\n".join(str(m.value) for m in at.markdown)
    at = fresh({"sayfa": "ulke", "ad": "Türkiye"})
    assert page(at) == nav_view.NATION and _param(at) == "Türkiye"
    assert "lk_nation_clubs" in _keys_now(at)
    assert _query(lambda db: _team(db, TEAM).name) in list(
        next(d for d in at.dataframe if d.key == "lk_nation_clubs").value["Kulüp"])


def test_other_clubs_academy_player_is_hidden_on_the_player_page():
    import nav_view
    from career_manager import CareerManager
    from database import session_scope

    _set_user_team(TEAM)
    with session_scope() as db:
        cm = CareerManager(db)
        other = next(p.id for t in cm.teams() if t.name != TEAM for p in cm.academy_players(t))
        own = next(p.id for p in cm.academy_players(cm.find_team(TEAM)))
    at = _app(page="kadro")
    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.PLAYER, other
    at.run()
    assert "Oyuncu bulunamadı" in "\n".join(str(m.value) for m in at.markdown)
    at.session_state[nav_view.PARAM_KEY] = own
    at.run()
    assert "pv-sheet" in "\n".join(str(m.value) for m in at.markdown)


# ---------------------------------------------------------------------------
# 3) Saf: hedef cozumu sunucudaki eslemeyle; gecersiz secim yok sayilir
# ---------------------------------------------------------------------------

def test_link_target_uses_the_server_mapping(monkeypatch):
    import accounts
    import links_view as lk
    import nav_view
    import web_common

    state: dict = {"auth": accounts.AuthSession(user_id=0, username="x", career_schema="public")}
    monkeypatch.setattr(web_common.st, "session_state", state)
    state[lk.links_key("t")] = {"players": [11, 22], "player_cols": {"Gol kralı": [5, None]},
                                "clubs": {"Kulüp": [7, 8]}, "leagues": {"Lig": [3, 3]}, "nations": {"Uyruk": ["X", None]}}

    def target(selection):
        return lk.link_target("t", {"selection": selection})

    assert target({"rows": [1], "columns": [], "cells": []}) == (nav_view.PLAYER, 22)
    assert target({"rows": [], "columns": [], "cells": [[0, "Kulüp"]]}) == (nav_view.CLUB_PAGE, 7)
    assert target({"rows": [], "columns": [], "cells": [[1, "Lig"]]}) == (nav_view.TABLE, 3)
    assert target({"rows": [], "columns": [], "cells": [[0, "Uyruk"]]}) == (nav_view.NATION, "X")
    assert target({"rows": [], "columns": [], "cells": [[0, "Gol kralı"]]}) == (nav_view.PLAYER, 5)
    assert target({"rows": [], "columns": [], "cells": [[1, "Oyuncu"]]}) == (nav_view.PLAYER, 22)
    for bad in ({"rows": [5]}, {"rows": [-1]}, {"rows": [True]}, {"rows": ["1"]}, {"cells": [[9, "Kulüp"]]}, {}):
        assert target({"rows": [], "columns": [], "cells": [], **bad}) is None, bad
    state["t"] = {"selection": {"rows": [], "columns": [], "cells": [[1, "Kulüp"]]}}
    lk.cb_link("t")
    assert state[nav_view.NAV_KEY] == nav_view.CLUB_PAGE and state[nav_view.PARAM_KEY] == 8
    assert state["t"]["selection"]["cells"] == []
    del state["auth"]                                                     # oturum yoksa hicbir sey yapmaz
    state["t"] = {"selection": {"rows": [0], "columns": [], "cells": []}}
    lk.cb_link("t")
    assert state[nav_view.NAV_KEY] == nav_view.CLUB_PAGE
    assert lk.cb_link.requires_auth is True and nav_view.cb_open_player.requires_auth is True
