"""
Oyun plani testleri: match_plan.py (PlanTrigger / PlanAction / PlanRule / MatchPlan, dogrulama,
JSONB) ve motor entegrasyonu (MatchEngine._run_plan, LiveMatch.set_plans_enabled).

Saf testler: veritabani gerektirmez (sentetik kadrolar). Kanitlanan sey:

    1) Tetik      dakika ve skor durumu (gol farki: en az / tam) dogruluk tablosu
    2) Dogrulama  Turkce hata mesajlari, en fazla 5 kural, to_dict / from_dict (kati ve hosgorulu)
    3) Motor      kural dogru dakikada, yalnizca dogru skor durumunda ve bir kez tetiklenir; eylemler
                  menajer mudahalesiyle ayni kurallardan gecer (3/5 hak, pencere, sahada olmayan /
                  atilmis oyuncu); uygulanamayan eylem aciklamali olayla atlanir; rastgele sayi cekilmez
    4) Canli mac  menajer mudahalesi kalan kurallari iptal etmez; plans_enabled ile kapatilir

  CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_match_plan.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instructions import Mentality, Pressing, TeamInstructions, Tempo  # noqa: E402
from live_match import LiveMatch, SubRule, engine_config_for  # noqa: E402
from match_engine import EngineConfig, EventType, KnockoutRule, MatchEngine, MatchTeam  # noqa: E402
from match_feed import build_timeline  # noqa: E402
from match_plan import (  # noqa: E402
    MAX_PLAN_RULES,
    SITUATION_LABELS,
    MatchPlan,
    PlanAction,
    PlanError,
    PlanRule,
    PlanTrigger,
    ScoreSituation,
    describe_instruction_changes,
)
from models import Position  # noqa: E402
from tests.test_live_match import big_team, fingerprint, make_team  # noqa: E402

QUIET = EngineConfig(base_card=0.0, base_injury=0.0)
W, D, L, A = ScoreSituation.WINNING, ScoreSituation.DRAWING, ScoreSituation.LOSING, ScoreSituation.ANY


def rule(minute: int, situation: ScoreSituation = A, margin: int | None = None, exact: bool = False,
         **action) -> PlanRule:
    return PlanRule(PlanTrigger(minute, situation, margin, exact), PlanAction(**action))


def plan_events(events, team_id: int = 1):
    return [e for e in events if e.team_id == team_id and e.detail in ("plan", "plan_skipped")]


# ===========================================================================
# 1) Tetik
# ===========================================================================

@pytest.mark.parametrize("trigger,minute,diff,expected", [
    (PlanTrigger(60), 59, 0, False),
    (PlanTrigger(60), 60, -3, True),
    (PlanTrigger(60), 95, 2, True),
    (PlanTrigger(60, D), 70, 0, True),
    (PlanTrigger(60, D), 70, 1, False),
    (PlanTrigger(60, W), 70, 1, True),
    (PlanTrigger(60, W), 70, 0, False),
    (PlanTrigger(60, W), 70, -1, False),
    (PlanTrigger(60, L), 70, -1, True),
    (PlanTrigger(60, L), 70, 1, False),
    (PlanTrigger(60, L, 2), 70, -1, False),
    (PlanTrigger(60, L, 2), 70, -2, True),
    (PlanTrigger(60, L, 2), 70, -4, True),
    (PlanTrigger(60, L, 2, True), 70, -3, False),
    (PlanTrigger(60, W, 1, True), 70, 1, True),
    (PlanTrigger(60, W, 1, True), 70, 2, False),
    (PlanTrigger(1, "LOSING"), 1, -1, True),
    (PlanTrigger(1, "Öndeyken"), 1, 1, True),
])
def test_trigger_truth_table(trigger, minute, diff, expected):
    assert trigger.errors() == []
    assert trigger.matches(minute, diff) is expected


def test_trigger_describe_and_labels():
    assert PlanTrigger(60).describe() == "60. dk sonrası"
    assert PlanTrigger(70, D).describe() == "70. dk sonrası, beraberken"
    assert PlanTrigger(75, W).describe() == "75. dk sonrası, öndeyken"
    assert PlanTrigger(80, L, 2).describe() == "80. dk sonrası, en az 2 farkla gerideyken"
    assert PlanTrigger(85, W, 1, True).describe() == "85. dk sonrası, tam 1 farkla öndeyken"
    assert SITUATION_LABELS == {A: "Her durumda", W: "Öndeyken", D: "Beraberken", L: "Gerideyken"}
    assert describe_instruction_changes({"mentality": "ALL_OUT_ATTACK", "offside_trap": False}) == \
        "zihniyet Çok Ofansif (Topyekûn Hücum), ofsayt taktiği kapalı"
    assert describe_instruction_changes({"tempo": "TURBO"}) == "talimat (geçersiz)"


# ===========================================================================
# 2) Dogrulama ve JSONB
# ===========================================================================

def test_valid_plan_construction_and_normalisation():
    plan = MatchPlan([
        rule(60, L, formation="4-3-3", instructions={"mentality": "Çok Ofansif (Topyekûn Hücum)", "tempo": "FAST"},
             sub_out_id=110, sub_in_id=114),
        PlanRule(PlanTrigger(75, W, 1), PlanAction(instructions={"pressing": Pressing.OWN_HALF}), name="Koru"),
        rule(80, formation=(5, 3, 2)),
    ])
    assert isinstance(plan.rules, tuple) and len(plan) == 3 and not plan.is_empty
    first = plan.rules[0].action
    assert first.instructions == {"mentality": Mentality.ALL_OUT_ATTACK, "tempo": Tempo.FAST}
    assert plan.rules[2].action.formation == "5-3-2"
    assert plan.describe({110: "Ali", 114: "Veli"}) == [
        "1. 60. dk sonrası, gerideyken → Ali çıkar, Veli girer; diziliş 4-3-3; "
        "zihniyet Çok Ofansif (Topyekûn Hücum), tempo Hızlı",
        "2. Koru: 75. dk sonrası, en az 1 farkla öndeyken → pres Kendi Yarı Sahasında",
        "3. 80. dk sonrası → diziliş 5-3-2",
    ]
    assert MatchPlan().is_empty and MatchPlan().errors() == []
    assert plan.squad_errors([110, 114]) == []
    assert plan.squad_errors([110]) == ["Kural 1: girecek oyuncu (#114) kadroda değil."]


@pytest.mark.parametrize("bad_rule,message", [
    (rule(0, instructions={"tempo": "FAST"}), "dakika 1 ile 120 arasında bir tam sayı olmalı (0 verildi)."),
    (rule(121, instructions={"tempo": "FAST"}), "dakika 1 ile 120 arasında"),
    (PlanRule(PlanTrigger("60"), PlanAction(formation="4-3-3")), "tam sayı olmalı ('60' verildi)"),
    (rule(60, D, 1, formation="4-3-3"), "gol farkı yalnızca 'Öndeyken' ya da 'Gerideyken'"),
    (rule(60, A, 2, formation="4-3-3"), "gol farkı yalnızca"),
    (rule(60, W, 0, formation="4-3-3"), "gol farkı en az 1 olan bir tam sayı olmalı"),
    (rule(60, W, None, True, formation="4-3-3"), "'tam fark' seçeneği için gol farkı belirtilmeli"),
    (PlanRule(PlanTrigger(60, "KAZANIRKEN"), PlanAction(formation="4-3-3")), "bilinmeyen skor durumu"),
    (rule(60), "en az bir eylem (diziliş, talimat ya da oyuncu değişikliği) seçilmeli"),
    (rule(60, formation="4-2-4"), "bilinmeyen diziliş: 4-2-4"),
    (rule(60, instructions={"gegenpress": True}), "bilinmeyen talimat alanı: gegenpress"),
    (rule(60, instructions={"tempo": "TURBO"}), "bilinmeyen tempo: TURBO"),
    (rule(60, instructions={"counter_attack": "evet"}), "kontra atak açık/kapalı"),
    (rule(60, sub_out_id=5), "çıkan ve giren oyuncu birlikte seçilmeli"),
    (rule(60, sub_out_id=5, sub_in_id=5), "çıkan ve giren oyuncu aynı olamaz"),
    (rule(60, sub_out_id="5", sub_in_id=6), "oyuncu numaraları tam sayı olmalı"),
    (PlanRule(PlanTrigger(60), PlanAction(formation="4-3-3"), enabled="yes"), "kural açık/kapalı"),
])
def test_invalid_rules_raise_turkish_errors(bad_rule, message):
    with pytest.raises(PlanError) as info:
        MatchPlan([rule(10, formation="4-3-3"), bad_rule])
    assert info.value.errors and all(err.startswith("Kural 2: ") and err.endswith(".") for err in info.value.errors)
    assert any(message in err for err in info.value.errors), info.value.errors
    assert isinstance(info.value, ValueError) and message in str(info.value)


def test_rule_count_limit():
    rules = [rule(10 + i, formation="4-3-3") for i in range(MAX_PLAN_RULES)]
    assert len(MatchPlan(rules)) == MAX_PLAN_RULES
    with pytest.raises(PlanError, match=r"Oyun planında en fazla 5 kural olabilir \(6 verildi\)\."):
        MatchPlan([*rules, rule(90, formation="4-4-2")])
    with pytest.raises(PlanError, match="Kural 3: kural okunamadı."):
        MatchPlan([rules[0], rules[1], "kural"])


def test_to_dict_from_dict_roundtrip_json():
    plan = MatchPlan([
        PlanRule(PlanTrigger(60, L, 2, True), PlanAction("4-3-3", {"mentality": "ALL_OUT_ATTACK",
                                                                   "offside_trap": True}, 110, 114),
                 name="Hücum", enabled=False),
        rule(88, W, instructions={"tempo": "SLOW"}),
    ])
    data = plan.to_dict()
    assert data == {"rules": [
        {"name": "Hücum", "enabled": False,
         "trigger": {"minute": 60, "situation": "LOSING", "margin": 2, "exact_margin": True},
         "action": {"formation": "4-3-3", "instructions": {"mentality": "ALL_OUT_ATTACK", "offside_trap": True},
                    "sub_out_id": 110, "sub_in_id": 114}},
        {"name": "", "enabled": True,
         "trigger": {"minute": 88, "situation": "WINNING", "margin": None, "exact_margin": False},
         "action": {"formation": None, "instructions": {"tempo": "SLOW"}, "sub_out_id": None, "sub_in_id": None}},
    ]}
    assert MatchPlan.from_dict(json.loads(json.dumps(data))) == plan
    assert MatchPlan.from_dict(None) == MatchPlan() == MatchPlan.from_dict({})


def test_from_dict_missing_keys_strict_and_lenient():
    minimal = {"rules": [{"trigger": {"minute": 70}, "action": {"formation": "5-3-2"}}]}
    plan = MatchPlan.from_dict(minimal)
    assert plan.rules[0] == PlanRule(PlanTrigger(70), PlanAction(formation="5-3-2"), name="", enabled=True)
    broken = {"rules": [{"trigger": {"minute": 70}, "action": {"formation": "5-3-2"}},
                        {"trigger": {"minute": 500}, "action": {"formation": "5-3-2"}},
                        "not a rule",
                        {"trigger": {"minute": 80, "situation": "LOSING"}, "action": {}},
                        *({"trigger": {"minute": 60 + i}, "action": {"instructions": {"tempo": "FAST"}}}
                          for i in range(6))], "extra": 1}
    with pytest.raises(PlanError, match="Kural 2: dakika 1 ile 120"):
        MatchPlan.from_dict(broken)
    lenient = MatchPlan.from_dict(broken, strict=False)
    assert len(lenient) == MAX_PLAN_RULES
    assert [r.trigger.minute for r in lenient.rules] == [70, 60, 61, 62, 63]
    for junk in ("x", {"rules": "x"}, [1]):
        assert MatchPlan.from_dict(junk, strict=False) == MatchPlan()
        with pytest.raises(PlanError):
            MatchPlan.from_dict(junk)


# ===========================================================================
# 3) Motor entegrasyonu
# ===========================================================================

def new_engine(seed: int, plan: MatchPlan | None = None, cfg: EngineConfig | None = None, home: MatchTeam | None = None,
               **kw) -> MatchEngine:
    return MatchEngine(home or big_team(1, "Ev"), make_team(2, "Dep", 80), seed=seed, config=cfg, home_plan=plan, **kw)


def test_rule_fires_once_at_the_right_minute_only_in_the_right_score_situation():
    plan = MatchPlan([rule(60, L, instructions={"tempo": "FAST"})])
    fired = not_fired = 0
    for seed in range(40):
        eng = new_engine(seed, plan)
        was_fired = False
        while not eng.finished:
            events = eng.step()
            mine = plan_events(events)
            losing = eng.home.stats.goals < eng.away.stats.goals
            in_play = eng.phase.name in ("FIRST_HALF", "SECOND_HALF") and eng.minute >= 1
            if not was_fired and in_play and eng.minute >= 60 and losing and not eng.finished:
                assert len(mine) == 1, (seed, eng.minute)
                ev = mine[0]
                assert (ev.type, ev.detail, ev.minute) == (EventType.TACTICAL_CHANGE, "plan", eng.minute)
                assert ev.description == "Oyun planı (Ev, kural 1: 60. dk sonrası, gerideyken): tempo Hızlı."
                assert ev.home_score < ev.away_score
                assert eng.home.instructions == TeamInstructions(tempo=Tempo.FAST)
                was_fired = True
            else:
                assert mine == [], (seed, eng.minute, [e.description for e in mine])
        assert (0 in eng.home.plan_fired) is was_fired
        fired += was_fired
        not_fired += not was_fired
        if not was_fired:
            assert eng.home.instructions.is_default
            assert fingerprint(eng.result()) == fingerprint(new_engine(seed).simulate())
    assert fired >= 5 and not_fired >= 5, (fired, not_fired)


def test_plan_draws_no_random_numbers_and_prefix_matches_unplanned_match():
    plan = MatchPlan([rule(30, formation="4-3-3", instructions={"pressing": "ALL_OVER"}, sub_out_id=110,
                          sub_in_id=154)])
    for seed in (1, 2, 3):
        eng = new_engine(seed, plan)
        reference = new_engine(seed).simulate()
        planned = eng.simulate()
        cut = next(i for i, e in enumerate(planned.events) if e.detail in ("plan", "plan_skipped"))
        assert [e.description for e in planned.events[:cut]] == [e.description for e in reference.events[:cut]]
        assert fingerprint(new_engine(seed, plan).simulate()) == fingerprint(planned)
    eng = new_engine(4, plan, cfg=QUIET)
    while eng.minute < 29 or eng.phase.name != "FIRST_HALF":
        eng.step()
    state = eng.rng.getstate()
    eng.step()                                                          # 30. dakika + plan
    probe = new_engine(4, cfg=QUIET)
    while probe.minute < 29 or probe.phase.name != "FIRST_HALF":
        probe.step()
    probe.step()
    assert eng.rng.getstate() == probe.rng.getstate() != state


def test_actions_apply_in_order_substitution_formation_instructions():
    plan = MatchPlan([PlanRule(PlanTrigger(30), PlanAction(formation="4-3-3", instructions={"mentality": "ALL_OUT_ATTACK"},
                                                           sub_out_id=110, sub_in_id=156), name="Baskı")])
    result = new_engine(5, plan, cfg=QUIET).simulate()
    events = plan_events(result.events)
    assert [e.type for e in events] == [EventType.SUBSTITUTION, EventType.TACTICAL_CHANGE, EventType.TACTICAL_CHANGE]
    assert all(e.minute == 30 and e.detail == "plan" for e in events)
    sub, formation, inst = events
    assert sub.description == ("Değişiklik (Ev): P110-MID çıkıyor, yerine P156-FWD giriyor (FWD → MID, mevki dışı) "
                               "— oyun planı (kural 1 «Baskı»: 30. dk sonrası).")
    assert formation.description.startswith("Oyun planı (Ev, kural 1 «Baskı»: 30. dk sonrası): diziliş 4-4-2 → 4-3-3.")
    assert "P156-FWD MID→FWD" in formation.description                    # giren oyuncu yeni hatlara dagitildi
    assert inst.description == "Oyun planı (Ev, kural 1 «Baskı»: 30. dk sonrası): zihniyet Çok Ofansif (Topyekûn Hücum)."
    home = result.home
    assert home.formation == (4, 3, 3) and home.instructions.mentality is Mentality.ALL_OUT_ATTACK
    entered = next(p for p in home.players if p.id == 156)
    assert entered.entered_minute == 30 and home.subs_used >= 1
    frames = build_timeline(result)
    labels = [f.event.label for f in frames if f.event.detail == "plan"]
    assert labels == ["DEĞİŞİKLİK (PLAN)", "OYUN PLANI", "OYUN PLANI"]
    assert frames[-1].home.subs == home.stats.substitutions


def test_plan_respects_classic_three_substitution_limit():
    home = big_team(1, "Ev")
    home.auto_subs = False
    plan = MatchPlan([rule(20, sub_out_id=103, sub_in_id=151), rule(30, sub_out_id=104, sub_in_id=152),
                      rule(40, sub_out_id=107, sub_in_id=154), rule(50, sub_out_id=108, sub_in_id=155)])
    cfg = engine_config_for(SubRule.CLASSIC_THREE, QUIET)
    result = new_engine(6, plan, cfg=cfg, home=home).simulate()
    events = plan_events(result.events)
    assert [(e.minute, e.type, e.detail) for e in events] == [
        (20, EventType.SUBSTITUTION, "plan"), (30, EventType.SUBSTITUTION, "plan"),
        (40, EventType.SUBSTITUTION, "plan"), (50, EventType.TACTICAL_CHANGE, "plan_skipped")]
    assert events[-1].description == ("Oyun planı (Ev, kural 4: 50. dk sonrası): oyuncu değişikliği uygulanamadı — "
                                      "Ev: değişiklik hakkı kalmadı (3/3 değişiklik).")
    assert result.home.subs_used == 3 and result.home.stats.substitutions == 3
    by_id = {p.id: p for p in result.home.players}
    assert by_id[108].left_minute == 90 and not by_id[155].played


def test_plan_respects_five_in_three_windows_and_shares_a_window_within_one_rule_minute():
    home = big_team(1, "Ev")
    home.auto_subs = False
    plan = MatchPlan([rule(20, sub_out_id=103, sub_in_id=151), rule(20, sub_out_id=104, sub_in_id=152),
                      rule(35, sub_out_id=107, sub_in_id=154), rule(50, sub_out_id=108, sub_in_id=155),
                      rule(65, sub_out_id=112, sub_in_id=156)])
    eng = new_engine(7, plan, cfg=engine_config_for(SubRule.FIVE_IN_THREE, QUIET), home=home)
    result = eng.simulate()
    events = plan_events(result.events)
    assert [e.detail for e in events] == ["plan", "plan", "plan", "plan", "plan_skipped"]
    assert "değişiklik penceresi kalmadı (4/5 değişiklik)" in events[-1].description
    assert (result.home.subs_used, result.home.sub_windows_used) == (4, 3)


def test_invalid_substitutions_are_skipped_with_an_explanation():
    home = big_team(1, "Ev")
    home.auto_subs = False
    plan = MatchPlan([rule(40, sub_out_id=103, sub_in_id=151),          # 103 atildi
                      rule(41, sub_out_id=104, sub_in_id=9999),         # kadroda yok
                      rule(42, sub_out_id=104, sub_in_id=152),
                      rule(43, sub_out_id=105, sub_in_id=152),          # 152 zaten sahada
                      rule(44, sub_out_id=101, sub_in_id=153)])         # kaleci cikar, yedek DEF kaleye (izinli)
    eng = new_engine(8, plan, cfg=QUIET, home=home)
    while eng.minute < 30 or eng.phase.name != "FIRST_HALF":
        eng.step()
    eng._send_off(eng.home, next(p for p in eng.home.players if p.id == 103), second_yellow=False)
    result = eng.simulate()
    events = plan_events(result.events)
    texts = [(e.minute, e.detail, e.description.split(" — ", 1)[-1]) for e in events]
    assert texts[0] == (40, "plan_skipped", "P103-DEF şu an sahada değil.")
    assert texts[1] == (41, "plan_skipped", "Girecek oyuncu (#9999) Ev kadrosunda değil.")
    assert texts[2][:2] == (42, "plan")
    assert texts[3] == (43, "plan_skipped", "P152-DEF zaten sahada.")
    assert texts[4][:2] == (44, "plan") and "(DEF → GK, mevki dışı)" in events[4].description
    by_id = {p.id: p for p in result.home.players}
    assert by_id[153].role is Position.GK and by_id[153].entered_minute == 44 and by_id[101].left_minute == 44
    assert result.home.subs_used == 2 and by_id[103].sent_off


def test_formation_already_set_and_instructions_already_set_are_reported():
    plan = MatchPlan([rule(10, formation="4-4-2"), rule(11, instructions={"mentality": "BALANCED"})])
    events = plan_events(new_engine(9, plan).simulate().events)
    assert [e.description for e in events] == [
        "Oyun planı (Ev, kural 1: 10. dk sonrası): diziliş değişikliği uygulanamadı — takım zaten 4-4-2 oynuyor.",
        "Oyun planı (Ev, kural 2: 11. dk sonrası): talimat değişikliği uygulanamadı — talimatlar zaten istenen durumda.",
    ]
    assert all(e.detail == "plan_skipped" and e.type is EventType.TACTICAL_CHANGE for e in events)


def test_disabled_rules_and_plans_enabled_flag():
    plan = MatchPlan([PlanRule(PlanTrigger(10), PlanAction(formation="5-3-2"), enabled=False),
                      rule(20, formation="4-3-3")])
    result = new_engine(10, plan).simulate()
    assert [e.minute for e in plan_events(result.events)] == [20]

    eng = new_engine(10, plan)
    eng.set_plans_enabled(eng.home, False)
    assert fingerprint(eng.simulate()) == fingerprint(new_engine(10).simulate())

    # mac icinde kapatip acmak: kapaliyken islenmez, acilinca dakikasi gecmis kural hemen islenir
    eng = new_engine(10, plan)
    eng.set_plans_enabled(eng.home, False)
    while eng.minute < 35 or eng.phase.name != "FIRST_HALF":
        eng.step()
    assert plan_events(eng.events) == []
    eng.set_plans_enabled(1, True)
    events = eng.step()
    assert [e.minute for e in plan_events(events)] == [36]
    assert eng.home.formation == (4, 3, 3)


def test_set_plan_mid_match_resets_fired_rules_and_validates():
    plan = MatchPlan([rule(5, instructions={"tempo": "FAST"})])
    eng = new_engine(11, plan)
    for _ in range(12):
        eng.step()
    assert eng.home.plan_fired == {0}
    eng.set_plan(eng.home, MatchPlan([rule(5, instructions={"tempo": "SLOW"})]))
    assert eng.home.plan_fired == set()
    events = eng.step()
    assert plan_events(events)[0].description.endswith("tempo Yavaş.")
    with pytest.raises(ValueError, match="MatchPlan"):
        eng.set_plan(eng.home, {"rules": []})
    eng.run_to_end()
    with pytest.raises(ValueError, match="Maç bitti"):
        eng.set_plan(eng.home, MatchPlan())


def test_knockout_aggregate_score_and_extra_time_minutes():
    plan = MatchPlan([rule(1, L, instructions={"mentality": "ALL_OUT_ATTACK"})])
    eng = MatchEngine(big_team(1, "Ev"), make_team(2, "Dep", 80), seed=12,
                      knockout=KnockoutRule(home_carry=0, away_carry=2), home_plan=plan)
    events = [e for _ in range(2) for e in eng.step()]
    fired = plan_events(events)
    assert len(fired) == 1 and fired[0].minute == 1 and (fired[0].home_score, fired[0].away_score) == (0, 0)

    cfg = EngineConfig(base_chance=0.0, base_card=0.0, base_injury=0.0)       # 0-0: uzatma
    extra = MatchPlan([rule(100, D, formation="5-3-2"), rule(95, W, formation="3-5-2")])
    for seed in range(10):
        result = MatchEngine(big_team(1, "Ev"), make_team(2, "Dep", 80), seed=seed, config=cfg,
                             knockout=KnockoutRule(), home_plan=extra).simulate()
        if not result.extra_time:
            continue
        events = plan_events(result.events)
        if result.home_score == result.away_score:
            assert [e.minute for e in events] == [100]
            return
    raise AssertionError("uzatmaya giden ve beraber biten maç bulunamadı")


def test_plans_for_both_teams_via_matchteam_fields():
    home, away = big_team(1, "Ev"), big_team(2, "Dep")
    home.plan = MatchPlan([rule(15, formation="3-5-2")])
    away.plan = MatchPlan([rule(25, instructions={"pressing": "ALL_OVER"})])
    result = MatchEngine(home, away, seed=13).simulate()
    assert [e.minute for e in plan_events(result.events, 1)] == [15]
    assert [e.minute for e in plan_events(result.events, 2)] == [25]
    assert result.away.instructions.pressing is Pressing.ALL_OVER and result.home.formation == (3, 5, 2)
    from pitch import build_scenes

    scenes = build_scenes(result, build_timeline(result))
    assert len(scenes) == len(result.events)


# ===========================================================================
# 4) Canli mac
# ===========================================================================

def test_manual_intervention_keeps_remaining_rules_and_partial_instructions():
    plan = MatchPlan([rule(20, instructions={"pressing": "ALL_OVER"}), rule(60, instructions={"tempo": "FAST"})])
    live = LiveMatch.create(new_engine(14), 1, plan=plan, pause_at_breaks=False, pause_on_key_events=False)
    assert live.plan == plan and live.plans_enabled
    while live.engine.minute < 30:
        live.tick()
    live.pause()
    assert live.managed_team.instructions.pressing is Pressing.ALL_OVER
    live.set_instructions("PARK_THE_BUS", "CALM")                          # menajer mudahalesi
    live.resume()
    live.play_to_end()
    result = live.result()
    assert [e.minute for e in plan_events(result.events)] == [20, 60]
    assert result.home.instructions == TeamInstructions(Mentality.PARK_THE_BUS, "CALM", pressing=Pressing.ALL_OVER,
                                                        tempo=Tempo.FAST)
    rows = live.plan_status()
    assert [r["İşlendi"] for r in rows] == [True, True] and rows[1]["Açıklama"] == "60. dk sonrası → tempo Hızlı"


def test_live_toggle_plans_and_replace_plan_and_roles():
    from team_roles import SetPieceRoles

    plan = MatchPlan([rule(50, formation="5-3-2")])
    live = LiveMatch.create(new_engine(15), 1, plan=plan, plans_enabled=False, roles=SetPieceRoles(captain_id=101))
    assert not live.plans_enabled and live.roles.captain_id == 101
    live.play_to_end()
    assert plan_events(live.result().events) == []

    live = LiveMatch.create(new_engine(15), 1, plan=plan, pause_at_breaks=False, pause_on_key_events=False)
    while live.engine.minute < 45:
        live.tick()
    live.set_plans_enabled(False)
    while live.engine.minute < 55 or live.engine.phase.name != "SECOND_HALF":
        live.tick()
    assert plan_events(live.engine.events) == [] and not live.plans_enabled
    live.set_plan(MatchPlan([rule(56, formation="3-5-2")]))
    live.set_plans_enabled(True)
    live.play_to_end()
    assert [(e.minute, e.detail) for e in plan_events(live.result().events)] == [(56, "plan")]
    assert live.result().home.formation == (3, 5, 2)
    watch = LiveMatch.create(new_engine(16), None)
    assert watch.plan == MatchPlan() and not watch.plans_enabled
    with pytest.raises(ValueError, match="yönettiğin"):
        watch.set_plan(plan)
    with pytest.raises(ValueError, match="yönettiğin"):
        watch.set_plans_enabled(True)


def test_goalkeeper_role_is_kept_by_plan_substitution_default():
    home = big_team(1, "Ev")
    plan = MatchPlan([rule(30, sub_out_id=101, sub_in_id=102)])
    result = new_engine(17, plan, cfg=QUIET, home=home).simulate()
    ev = plan_events(result.events)[0]
    assert ev.type is EventType.SUBSTITUTION and "mevki dışı" not in ev.description
    backup = next(p for p in result.home.players if p.id == 102)
    assert backup.role is Position.GK and backup.entered_minute == 30
