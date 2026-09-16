"""
Kulup Yonetimi & Tesisler sekmesi uctan uca testleri (11. Asama) -- Streamlit AppTest.

Menajer transfer butcesiyle altyapi / saglik merkezi / stadyum yatirimi yapar ve sezonun sponsor
tekliflerinden birini imzalar. Degisiklikler veritabaninda dogrulanir; butce yetmeyince dugmeler
kapanir; her menajerin yatirimi yalnizca kendi kariyer semasina yazilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _reseed,
    _set_user_team,
)
from tests.test_web_auth import _auth, _clean_accounts, _register  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"


@pytest.fixture(autouse=True)
def fresh_world():
    _clean_accounts()
    _reseed()
    yield
    _clean_accounts()


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _clean_accounts()
    _reseed()


def _club(schema: str | None = None, name: str = TEAM) -> dict:
    import database
    from career_manager import CareerManager

    with database.career_context(schema), database.session_scope() as db:
        cm = CareerManager(db)
        team = cm.find_team(name)
        status = cm.facility_status(team)
        status["offers"] = cm.sponsor_offers(team)
        status["sponsor_name"] = team.sponsor_name
        return status


def _tab_labels(at) -> list[str]:
    return [t.label for t in at.tabs]


def test_club_tab_upgrades_youth_medical_and_stadium_from_the_transfer_budget():
    _set_user_team(TEAM)
    at = _app(seed="4")
    assert "🏛️ Kulüp Yönetimi & Tesisler" in _tab_labels(at)
    before = _club()
    assert before["configured"]                                     # giriste ensure_club_setup calisti

    _click(at, "club_up_youth")
    after = _club()
    cost = before["youth"]["upgrade_cost"]
    assert after["youth"]["level"] == before["youth"]["level"] + 1
    assert after["transfer_budget"] == before["transfer_budget"] - cost
    assert any("Altyapı tesisleri yükseltildi" in s.value for s in at.success)

    _click(at, "club_up_medical")
    _click(at, "club_up_stadium")
    final = _club()
    assert final["medical"]["level"] == before["medical"]["level"] + 1
    assert final["stadium"]["capacity"] == before["stadium"]["next_capacity"]
    assert final["transfer_budget"] == (before["transfer_budget"] - cost - after["medical"]["upgrade_cost"]
                                        - after["stadium"]["expansion_cost"])


def test_buttons_are_disabled_when_the_budget_is_short():
    _set_user_team(TEAM, transfer_budget=0)
    at = _app(seed="4")
    for kind in ("youth", "medical", "stadium"):
        assert at.button(key=f"club_up_{kind}").disabled
    status = _club()
    assert status["transfer_budget"] == 0 and not status["youth"]["affordable"]


def test_signing_a_sponsor_offer_replaces_the_contract_and_pays_the_bonus():
    _set_user_team(TEAM)
    at = _app(seed="4")
    before = _club()
    assert len(before["offers"]) == 3 and at.button(key="club_sponsor_0")
    chosen = max(range(3), key=lambda i: before["offers"][i].signing_bonus)
    offer = before["offers"][chosen]

    _click(at, f"club_sponsor_{chosen}")
    after = _club()
    assert after["sponsor_name"] == offer.brand and after["sponsor"]["weekly"] == offer.weekly
    assert after["offers"] == [] and after["transfer_budget"] == before["transfer_budget"] + offer.signing_bonus
    assert any("imzalandı" in s.value for s in at.success)
    assert not [b for b in at.button if b.key.startswith("club_sponsor_")]


def test_each_manager_invests_only_in_their_own_world():
    import database
    from career_manager import CareerManager

    _register(_app(login=False), "TesisMenajeriA")                   # public'i devralir
    second = _register(_app(login=False), "TesisMenajeriB")
    schema = _auth(second).career_schema
    _click(second, "mode_career")
    with database.career_context(schema), database.session_scope() as db:
        cm = CareerManager(db)
        cm.set_user_team(cm.find_team(TEAM))
    second.run()
    public_before, own_before = _club(), _club(schema)

    _click(second, "club_up_medical")
    assert _club(schema)["medical"]["level"] == own_before["medical"]["level"] + 1
    public_after = _club()
    assert public_after["medical"]["level"] == public_before["medical"]["level"]
    assert public_after["transfer_budget"] == public_before["transfer_budget"]
