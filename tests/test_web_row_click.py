"""
Oyuncu tablosunda satira tek tik -> profil (Faz 13G). Gercek web_app.py AppTest ile calisir; tablo secimi,
tarayicinin gonderecegi widget durumuyla (st.dataframe secim JSON'u) surulur -- AppTest'in kendi arayuzunde
dataframe secimi yoktur. Callback (player_view.cb_pv_row) ayrica sahte oturum durumuyla dogrudan denenir.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    _app,
    _db_available,
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


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed(mode=None)                  # conftest'in varsayilan dunyasi (mod secilmemis)


def select_row(at, key: str, row: int):
    """Tarayicidaki tek tikin karsiligi: tablonun secim durumu {'selection': {'rows': [row]}} olarak gelir."""
    from streamlit.proto.WidgetStates_pb2 import WidgetState

    frame = next(d for d in at.dataframe if d.key == key)
    states = at._tree.get_widget_states()
    states.widgets.append(WidgetState(id=frame.proto.id, string_value=json.dumps(
        {"selection": {"rows": [row], "columns": [], "cells": []}})))
    at._run(states)
    assert not at.exception, at.exception
    return at


def _table(at, key: str):
    return next(d for d in at.dataframe if d.key == key).value


def test_clicking_a_squad_row_opens_that_players_profile_and_clears_the_selection():
    import player_view as pv

    _set_user_team(TEAM)
    at = _app()
    ids = at.session_state[pv.table_ids_key("sq_table")]
    names = _table(at, "sq_table")["Oyuncu"].tolist()
    assert len(ids) == len(names) == _query(lambda db: len(_team(db, TEAM).players))
    select_row(at, "sq_table", 3)
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_SQUAD, ids[3])
    assert at.session_state["sq_table"]["selection"]["rows"] == []        # ayni satir yeniden tiklanabilir
    panel = "\n".join(str(m.value) for m in at.markdown if 'class="pv-head' in str(m.value))
    assert names[3].replace("🌟 ", "") in panel and "kendi oyuncun" in panel
    select_row(at, "sq_table", 0)                                        # baska satir: profil degisir
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_SQUAD, ids[0])
    assert any(pv.ROW_HINT in c.value for c in at.caption)
    assert at.selectbox(key="pv_pick_squad")                             # secici ikincil yol olarak duruyor


def test_market_row_click_targets_the_player_and_opens_the_full_profile():
    import player_view as pv

    _set_user_team(TEAM)
    at = _app()
    ids = at.session_state[pv.table_ids_key("mkt_table")]
    select_row(at, "mkt_table", 2)
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_MARKET, ids[2])
    assert at.selectbox(key="mkt_target").value == ids[2]                # teklif paneli ayni oyuncuya gecti
    assert any("Gözlemci raporu" in str(m.value) for m in at.markdown)
    assert not any('class="pv-head' in str(m.value) and "kendi oyuncun" in str(m.value) for m in at.markdown)


def test_academy_and_shortlist_rows_open_profiles():
    import player_view as pv
    from career_manager import CareerManager
    from database import session_scope
    from models import Player

    _set_user_team(TEAM)
    with session_scope() as db:
        cm = CareerManager(db)
        target = next(p for p in cm.find_team("Madrid Blancos").players)
        cm.shortlist_add(target)
        target_id = target.id
        academy = [p.id for p in cm.academy_players(cm.find_team(TEAM))]
    assert academy, "sentetik dünyada akademi oyuncusu olmalı"
    at = _app()
    academy_rows = at.session_state[pv.table_ids_key("acad_table")]
    assert set(academy_rows) == set(academy)
    select_row(at, "acad_table", 0)
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_ACADEMY, academy_rows[0])

    assert at.session_state[pv.table_ids_key("sl_table")] == [target_id]
    select_row(at, "sl_table", 0)
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_SHORTLIST, target_id)
    assert at.session_state["sl_pick"] == target_id                      # "Listeden çıkar" ayni oyuncuyu hedefler
    name = _query(lambda db: db.get(Player, target_id).name)
    panel = "\n".join(str(m.value) for m in at.markdown if 'class="pv-head' in str(m.value))
    assert name in panel and "kendi oyuncun" not in panel               # baska kulup: gozlemci sisi


def test_row_callback_validates_the_row_index_on_the_server(monkeypatch):
    import accounts
    import player_view as pv
    import web_common

    state: dict = {"auth": accounts.AuthSession(user_id=0, username="x", career_schema="public"),
                   pv.table_ids_key("t"): [11, 22, 33]}
    monkeypatch.setattr(web_common.st, "session_state", state)
    for rows in ([5], [-1], [True], ["1"], []):
        state["t"] = {"selection": {"rows": rows, "columns": [], "cells": []}}
        pv.cb_pv_row(pv.AREA_SQUAD, "t")
        assert pv.PROFILE_KEY not in state
    state["t"] = {"selection": {"rows": [1], "columns": [], "cells": []}}
    pv.cb_pv_row(pv.AREA_MARKET, "t", "mkt_target")
    assert state[pv.PROFILE_KEY] == (pv.AREA_MARKET, 22) and state["mkt_target"] == 22
    assert state["t"]["selection"]["rows"] == [] and state[pv.SCROLL_KEY] == 1
    del state["auth"]                                                    # oturum yoksa hicbir sey yapmaz
    state["t"] = {"selection": {"rows": [2], "columns": [], "cells": []}}
    pv.cb_pv_row(pv.AREA_SQUAD, "t")
    assert state[pv.PROFILE_KEY] == (pv.AREA_MARKET, 22)
