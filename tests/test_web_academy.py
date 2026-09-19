"""
Altyapi Akademisi, yildiz sistemi ve CM temasi uctan uca testleri (10. Asama) -- Streamlit AppTest.

Akademi sekmesi gozlemci tahmini potansiyelle listelenir; "A Takıma Yükselt" ve "U-21'e
Gönder" dugmeleri oyuncuyu veritabaninda gercekten tasir. Kadro ve transfer ekranlarinda
sayisal guc gizlidir (yildiz gosterilir).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _html,
    _query,
    _reseed,
    _set_user_team,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"
STAR_TEXT = re.compile(r"^(⭐|💫| – )+$")


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed()


def _team_ids(db):
    from sqlalchemy import select

    from models import Team

    team = db.scalar(select(Team).where(Team.name == TEAM))
    return [p.id for p in team.players], [p.id for p in team.academy_players]


def _frame(at, column: str):
    for df in at.dataframe:
        if column in df.value.columns:
            return df.value
    raise AssertionError(f"{column} sütunlu tablo yok")


def test_academy_tab_lists_prospects_with_stars_only():
    _set_user_team(TEAM)
    at = _app(seed="4", page="akademi")
    frame = _frame(at, "Potansiyel yetenek (gözlemci)")
    _senior, academy = _query(_team_ids)
    assert len(frame) == len(academy) > 0
    assert all(STAR_TEXT.match(v) for v in frame["Mevcut yetenek"]) and all(STAR_TEXT.match(v) for v in frame["Potansiyel yetenek (gözlemci)"])
    assert "U-21 kadrosu" in _html(at)
    assert at.button(key="acad_promote_btn") and at.button(key="acad_demote_btn")


def test_promote_and_demote_move_players_in_the_database():
    from models import Player

    _set_user_team(TEAM)
    at = _app(seed="4", page="akademi")
    senior, academy = _query(_team_ids)

    prospect = academy[0]
    at.selectbox(key="acad_promote").set_value(prospect)
    at.run()
    _click(at, "acad_promote_btn")
    assert any("A takıma yükseltildi" in s.value for s in at.success)
    assert _query(lambda db: db.get(Player, prospect).in_academy) is False
    assert prospect in _query(_team_ids)[0]

    at.selectbox(key="acad_demote").set_value(prospect)
    at.run()
    _click(at, "acad_demote_btn")
    assert any("U-21 akademisine gönderildi" in s.value for s in at.success)
    assert _query(lambda db: db.get(Player, prospect).in_academy) is True
    assert len(_query(_team_ids)[0]) == len(senior)


def test_demotion_rules_are_enforced_with_a_message():
    from sqlalchemy import select

    from models import Player, Position, Team

    _set_user_team(TEAM)
    keeper = _query(lambda db: [p.id for p in db.scalar(select(Team).where(Team.name == TEAM)).players
                                if p.position is Position.GK])
    at = _app(seed="4", page="akademi")
    at.selectbox(key="acad_demote").set_value(keeper[0])        # 2 kaleciden biri gonderilemez
    at.run()
    _click(at, "acad_demote_btn")
    assert at.error
    assert _query(lambda db: db.get(Player, keeper[0]).in_academy) is False


def test_squad_and_market_hide_numeric_ratings_behind_stars():
    _set_user_team(TEAM)
    at = _click(_app(seed="4", page="kadro"), "tac_auto")      # asistan 11'i kurar: tahta dolu
    element = at.get("bidi_component")[0]                         # Faz 13G: surukle-birak tahta
    board = json.loads(element.proto.mixed.json if element.proto.WhichOneof("data") == "mixed" else element.proto.json)
    tokens = [s["player"] for s in board["slots"] if s["player"]] + board["bench"] + board["reserves"]
    assert len(tokens) >= 11 and all("★" in t["stars"] and not re.search(r"\d", t["stars"]) for t in tokens)
    assert not {"overall", "overall_rating", "potential"} & set().union(*map(set, tokens))   # tokende sayi yok
    squad = _frame(at, "Statü")                                    # 14S: CM kadro listesi -- guc / yildiz sutunu yok
    assert not {"Mevcut yetenek", "Potansiyel yetenek", "Güç", "Potansiyel", "OVR"} & set(squad.columns)
    assert all(v in ("Kötü", "Orta", "İyi", "Çok iyi") for v in squad["Moral"])          # moral sozcukle

    goto(at, "transfer")                                         # Faz 13I: Transfer Merkezi › Oyuncu ara
    at.select_slider(key="mkt_stars").set_value("Tümü")
    at.run()
    market = _frame(at, "Mevcut yetenek (tahmin)")
    assert all(STAR_TEXT.match(v) for v in market["Mevcut yetenek (tahmin)"])
    assert "Genel (tahmin)" not in market.columns


def test_ofm_theme_is_injected():
    _set_user_team(TEAM)
    at = _app(seed="4")
    html = _html(at)
    assert "--ofm-bg:#231a2d" in html and "Tahoma" in html          # 14S varsayilan: OFM Klasik (CM 01/02)
