"""
Altyapi Akademisi, yildiz sistemi ve CM temasi uctan uca testleri (10. Asama) -- Streamlit AppTest.

Akademi sekmesi gozlemci tahmini potansiyelle listelenir; "A Takıma Yükselt" ve "U-21'e
Gönder" dugmeleri oyuncuyu veritabaninda gercekten tasir. Kadro ve transfer ekranlarinda
sayisal guc gizlidir (yildiz gosterilir).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    at = _app(seed="4")
    frame = _frame(at, "Potansiyel (gözlemci)")
    _senior, academy = _query(_team_ids)
    assert len(frame) == len(academy) > 0
    assert all(STAR_TEXT.match(v) for v in frame["Güç"]) and all(STAR_TEXT.match(v) for v in frame["Potansiyel (gözlemci)"])
    assert "U-21 kadrosu" in _html(at)
    assert at.button(key="acad_promote_btn") and at.button(key="acad_demote_btn")


def test_promote_and_demote_move_players_in_the_database():
    from models import Player

    _set_user_team(TEAM)
    at = _app(seed="4")
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
    at = _app(seed="4")
    at.selectbox(key="acad_demote").set_value(keeper[0])        # 2 kaleciden biri gonderilemez
    at.run()
    _click(at, "acad_demote_btn")
    assert at.error
    assert _query(lambda db: db.get(Player, keeper[0]).in_academy) is False


def test_squad_and_market_hide_numeric_ratings_behind_stars():
    _set_user_team(TEAM)
    at = _click(_app(seed="4"), "tac_auto")                    # asistan 11'i kurar: tahta dolu
    html = _html(at)
    assert "<th>Güç</th><th>Potansiyel</th>" in html and "<th>OVR</th>" not in html
    assert "★" in html and "cm-p-ovr" in html                  # taktik tahtasi yildizla
    board = html[html.index("cm-p-board"):]
    assert not re.search(r'class="cm-p-ovr"[^>]*>\d', board)    # dairede sayi yok
    squad = _frame(at, "Güç")
    assert "OVR" not in squad.columns and all(STAR_TEXT.match(v) for v in squad["Güç"])

    at.select_slider(key="mkt_stars").set_value("Tümü")
    at.run()
    market = _frame(at, "Güç (tahmin)")
    assert all(STAR_TEXT.match(v) for v in market["Güç (tahmin)"])
    assert "Genel (tahmin)" not in market.columns


def test_ofm_theme_is_injected():
    _set_user_team(TEAM)
    at = _app(seed="4")
    html = _html(at)
    assert "--ofm-bg:#121824" in html and "Barlow" in html          # varsayilan: OFM Dark
