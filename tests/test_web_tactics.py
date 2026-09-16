"""
Taktik Merkezi: talimatlar, kaptan/duran toplar, mac plani ve kayitli taktikler -- Streamlit AppTest.

Secimler ekrandaki widget'larla yapilir, kaydedilen degerler CareerManager uzerinden veritabanindan
okunur. Canli kariyer macinin kayitli talimatlarla basladigi da dogrulanir.
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


def _read(fn):
    from career_manager import CareerManager
    from database import session_scope

    with session_scope() as db:
        cm = CareerManager(db)
        return fn(cm, cm.find_team(TEAM))


def _section(at, label: str):
    at.radio(key="prep_section").set_value(label)
    at.run()
    assert not at.exception, at.exception
    return at


def _open(section: str):
    import web_app

    _set_user_team(TEAM)
    at = _app(seed="4")
    return _section(at, getattr(web_app, section))


def test_team_instructions_are_saved_from_the_tactics_centre():
    from instructions import (
        PASSING_LABELS,
        PRESSING_LABELS,
        TEMPO_LABELS,
        PassingStyle,
        Pressing,
        Tempo,
    )

    at = _open("PREP_ORDERS")
    at.selectbox(key="ord_passing_style").set_value(PASSING_LABELS[PassingStyle.SHORT])
    at.selectbox(key="ord_pressing").set_value(PRESSING_LABELS[Pressing.ALL_OVER])
    at.selectbox(key="ord_tempo").set_value(TEMPO_LABELS[Tempo.FAST])
    at.toggle(key="ord_counter_attack").set_value(True)
    _click(at, "ord_save")

    saved = _read(lambda cm, team: cm.team_instructions(team))
    assert (saved.passing_style, saved.pressing, saved.tempo, saved.counter_attack) == \
        (PassingStyle.SHORT, Pressing.ALL_OVER, Tempo.FAST, True)
    assert any("Takım talimatları kaydedildi" in s.value for s in at.success)
    at.run()                                                          # yeniden cizim kayitli degeri gosterir
    assert at.selectbox(key="ord_passing_style").value == PASSING_LABELS[PassingStyle.SHORT]


def test_assistant_and_manual_set_piece_roles():
    at = _open("PREP_ORDERS")
    _click(at, "role_suggest")
    suggested = _read(lambda cm, team: cm.team_roles(team))
    assert suggested.captain_id is not None and suggested.penalty_taker_id is not None

    squad = _read(lambda cm, team: sorted(p.id for p in team.players))
    other = next(pid for pid in squad if pid != suggested.penalty_taker_id)
    at.selectbox(key="role_penalty_taker_id").set_value(other)
    _click(at, "role_save")
    assert _read(lambda cm, team: cm.team_roles(team)).penalty_taker_id == other


def test_match_plan_rules_can_be_added_toggled_and_deleted():
    from instructions import MENTALITY_LABELS, Mentality
    from match_plan import SITUATION_LABELS, ScoreSituation

    at = _open("PREP_PLAN")
    _click(at, "plan_add")                                            # bos kural reddedilir
    assert any("bir şeyi değiştirmeli" in e.value for e in at.error)
    assert _read(lambda cm, team: len(cm.team_plan(team))) == 0

    at.text_input(key="pl_name").set_value("Skor peşinde")
    at.number_input(key="pl_minute").set_value(60)
    at.selectbox(key="pl_situation").set_value(SITUATION_LABELS[ScoreSituation.LOSING])
    at.selectbox(key="pl_mentality").set_value(MENTALITY_LABELS[Mentality.ALL_OUT_ATTACK])
    at.selectbox(key="pl_formation").set_value("4-3-3")
    _click(at, "plan_add")
    plan = _read(lambda cm, team: cm.team_plan(team))
    rule = plan.rules[0]
    assert len(plan) == 1 and rule.name == "Skor peşinde" and rule.trigger.minute == 60
    assert rule.trigger.situation is ScoreSituation.LOSING and rule.action.formation == "4-3-3"

    _click(at, "plan_toggle_0")
    assert _read(lambda cm, team: cm.team_plan(team).rules[0].enabled) is False
    _click(at, "plan_del_0")
    assert _read(lambda cm, team: cm.team_plan(team).is_empty)


def test_tactic_preset_save_apply_and_delete():
    import web_app
    from instructions import MENTALITY_LABELS, Mentality, TeamInstructions

    at = _open("PREP_ORDERS")
    at.selectbox(key="ord_mentality").set_value(MENTALITY_LABELS[Mentality.PARK_THE_BUS])
    _click(at, "ord_save")
    _section(at, web_app.PREP_PRESETS)
    at.text_input(key="preset_name").set_value("Deplasman kalesi")
    _click(at, "preset_save")
    presets = _read(lambda cm, team: [(t.id, t.name, t.instructions.mentality) for t in cm.tactic_presets(team)])
    assert [(name, mentality) for _, name, mentality in presets] == [("Deplasman kalesi", Mentality.PARK_THE_BUS)]

    _read(lambda cm, team: cm.set_team_instructions(team, TeamInstructions()))   # menajer baska taktige gecti
    at.run()
    _click(at, "preset_apply")
    assert _read(lambda cm, team: cm.team_instructions(team).mentality) is Mentality.PARK_THE_BUS
    assert any("Taktik uygulandı" in s.value for s in at.success)

    _click(at, "preset_delete")
    assert _read(lambda cm, team: cm.tactic_presets(team)) == []


def test_live_career_match_starts_with_the_saved_instructions():
    from instructions import PASSING_LABELS, PassingStyle, TeamInstructions

    _set_user_team(TEAM)
    _read(lambda cm, team: cm.set_team_instructions(team, TeamInstructions(passing_style=PassingStyle.DIRECT)))
    at = _app(seed="7")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_fixture_start")
    live = at.session_state["live"]
    assert live.instructions.passing_style is PassingStyle.DIRECT, PASSING_LABELS
