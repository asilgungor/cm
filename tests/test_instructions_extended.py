"""
Genisletilmis takim talimatlari testleri (Soccer Manager tarzi taktik derinlik): instructions.py
(pas stili, tempo, pres, hucum yonu, ofsayt taktigi, kontra atak, JSONB, ai_instructions) ve
motordaki karsiliklari.

Saf testler: veritabani ve Streamlit gerektirmez (sentetik kadrolar). Kanitlanan sey:

    1) Geriye uyumluluk  varsayilan nesnelerle (talimat / rol / plan) motor BIT-BIT eski davranista;
                         degisiklik ONCESI motordan dondurulmus parmak izleri
    2) API               enumlar, Turkce etiketler, carpim kurallari, dogrulama, to_dict / from_dict
    3) Aninda etki       rakibe bagli carpanlar (pres x pas stili, kontra, ofsayt, hucum yonu x beceri)
    4) Istatistik        sabit tohumlu yuzlerce macta yonlu etkiler (sut, topa sahip olma, enerji, kart)
    5) AI                ai_instructions saf kurallari ve EngineConfig.ai_tactics

  CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_instructions_extended.py
"""

from __future__ import annotations

import itertools
import json
import random
import sys
from collections import Counter
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instructions import (  # noqa: E402
    COUNTER_ATTACK,
    FIELD_NAMES,
    FOCUS_EFFECTS,
    FOCUS_LABELS,
    MENTALITY_EFFECTS,
    OFFSIDE_TRAP,
    PASSING_EFFECTS,
    PASSING_LABELS,
    PRESSING_EFFECTS,
    PRESSING_LABELS,
    TACKLING_EFFECTS,
    TEMPO_EFFECTS,
    TEMPO_LABELS,
    AttackingFocus,
    AttackingWidth,
    Mentality,
    PassingStyle,
    Pressing,
    Tackling,
    TeamInstructions,
    Tempo,
    ai_instructions,
    parse_attacking_focus,
    parse_instruction_changes,
    parse_passing_style,
    parse_pressing,
    parse_tempo,
)
from live_match import LiveMatch, SubRule, engine_config_for  # noqa: E402
from match_engine import EngineConfig, EventType, KnockoutRule, MatchEngine, MatchTeam  # noqa: E402
from match_plan import MatchPlan  # noqa: E402
from models import Position  # noqa: E402
from team_roles import SetPieceRoles  # noqa: E402
from tests.test_live_match import (  # noqa: E402
    GOLDEN_SAMPLE,
    _scripted_match,
    fingerprint,
    make_team,
)

KINDS = ("attack", "midfield", "defense")


def engine(seed: int = 1, cfg: EngineConfig | None = None, **kw) -> MatchEngine:
    return MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed, config=cfg, **kw)


def stepped(seed: int = 1, steps: int = 40, cfg: EngineConfig | None = None) -> MatchEngine:
    eng = engine(seed, cfg)
    for _ in range(steps):
        eng.step()
    return eng


# ===========================================================================
# 1) Geriye uyumluluk: varsayilanlarla bit-bit ayni motor
# ===========================================================================

# 9. Asama ONCESI motordan alinmis parmak izleri: tests/test_live_match._scripted_match,
# eleme maclari (uzatma + seri penalti) ve zihniyet / sertlik talimatli maclar
# (EngineConfig(sub_windows=3), deplasman PARK_THE_BUS + HARD).
# YENIDEN TEMELLENDIRME (13A "motor dogrulugu"): EngineConfig'teki on 13A bayragi TEK adimda
# acildi (gerekce ve kanit tests/test_extra_time.py GOLDEN notunda). Bayraklarin hepsi False
# iken motor 5.500 macta 13A oncesiyle BIT-BIT ayni kaliyor, yani asagidaki fark yalnizca
# kasitli kalibrasyon degisikligidir.
# YENIDEN TEMELLENDIRME 2 (13B "anlatim"): yalnizca olay listesi ve metin degisti (korner / faul /
# ofsayt / kurulus olaylari yaziliyor, cumleler commentary.py bankasindan). Bu dokuz senaryonun
# SONUC ozeti (skor, oyuncu istatistikleri, notlar, enerji serileri, takim sayaclari, penaltilar)
# 13A kopyasiyla birebir ayni; 13A kopyasi eski parmak izlerini aynen uretiyor
# (.claude/phase13/scratch/b13/regen_instructions.py base|new).
PRE_CHANGE_SCRIPTED = {2: "f2f159173bf15089", 5: "f687e5eb603d0119", 13: "fa101bdc3b943ea1"}
# Tohumlar 13A ile yenilendi: eski 6/10/19 artik normal surede bitiyor (uzatma/seri gerekiyor).
PRE_CHANGE_KNOCKOUT = {21: "4718a94939f22b78", 26: "017b360a9d5f2919", 32: "4beb7851cb0c4513"}
PRE_CHANGE_INSTRUCTIONS = [
    (Mentality.ALL_OUT_ATTACK, Tackling.HARD, 0, "f8741dcc3982bf42"),
    (Mentality.PARK_THE_BUS, Tackling.CALM, 1, "5d847afb5e7882e3"),
    (Mentality.BALANCED, Tackling.HARD, 2, "bbc9405eeb5dc763"),
]


@pytest.mark.parametrize("seed,digest", sorted(PRE_CHANGE_SCRIPTED.items()))
def test_scripted_live_interventions_match_pre_change_engine(seed, digest):
    assert fingerprint(_scripted_match(seed)) == digest


@pytest.mark.parametrize("seed,digest", sorted(PRE_CHANGE_KNOCKOUT.items()))
def test_knockout_with_extra_time_and_shootout_match_pre_change_engine(seed, digest):
    plain = engine(seed, knockout=KnockoutRule()).simulate()
    assert plain.decided_by in ("penalties", "extra_time")
    assert fingerprint(plain) == digest
    explicit = engine(seed, cfg=EngineConfig(ai_tactics=False), knockout=KnockoutRule(),
                      home_plan=MatchPlan(), away_plan=MatchPlan(), home_roles=SetPieceRoles(),
                      away_roles=SetPieceRoles()).simulate()
    assert fingerprint(explicit) == digest


@pytest.mark.parametrize("mentality,tackling,seed,digest", PRE_CHANGE_INSTRUCTIONS)
def test_basic_instructions_match_pre_change_engine(mentality, tackling, seed, digest):
    eng = engine(seed, cfg=EngineConfig(sub_windows=3))
    home = TeamInstructions.from_dict({"mentality": mentality.value, "tackling": tackling.value})
    assert home == TeamInstructions(mentality, tackling) and home.is_basic
    eng.set_instructions(eng.home, home)
    eng.set_instructions(eng.away, TeamInstructions(Mentality.PARK_THE_BUS, Tackling.HARD))
    assert fingerprint(eng.simulate()) == digest


@pytest.mark.parametrize("seed,home_ovr,away_ovr,home_goals,away_goals,n_events,digest", GOLDEN_SAMPLE)
def test_explicit_default_objects_everywhere_are_bit_identical(seed, home_ovr, away_ovr, home_goals, away_goals,
                                                                n_events, digest):
    """Varsayilan talimat / rol / plan nesneleri motor kurucusu, MatchTeam alani ve LiveMatch uzerinden."""
    home, away = make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr)
    home.roles, home.plan, away.instructions = SetPieceRoles(), MatchPlan(), TeamInstructions.from_dict({})
    eng = MatchEngine(home, away, seed=seed, config=engine_config_for(SubRule.STANDARD),
                      home_plan=MatchPlan.from_dict({"rules": []}), away_roles=SetPieceRoles.from_dict({}))
    live = LiveMatch.create(eng, 1, instructions=TeamInstructions.from_dict(None), plan=MatchPlan(),
                            roles=SetPieceRoles(), plans_enabled=True)
    assert eng.set_instructions(eng.away, TeamInstructions(**TeamInstructions().to_dict())) is None
    live.play_to_end()
    result = live.result()
    assert (result.home_score, result.away_score, len(result.events)) == (home_goals, away_goals, n_events)
    assert fingerprint(result) == digest
    # 13A: duran toplar artik varsayilan acik, "plan" / "ai" detaylari hala olusmamali
    assert not any(e.detail in ("plan", "plan_skipped", "ai") for e in result.events)


def test_default_config_flags_leave_instruction_surface_neutral():
    # 13A: duran toplar artik varsayilan ACIK (set_pieces=True); "rollere gore ac" davranisi
    # set_pieces=None ile hala mevcut. AI talimatlari hala varsayilan kapali.
    cfg = EngineConfig()
    assert cfg.set_pieces is True and cfg.ai_tactics is False
    assert not engine(3, cfg=EngineConfig(set_pieces=None))._set_pieces_on()
    eng = engine(3)
    assert eng._set_pieces_on()
    for team in (eng.home, eng.away):
        assert team.roles.is_default and team.plan.is_empty and team.plans_enabled
        assert team.instructions.is_default and not team.manager_controlled
    for _ in range(30):
        eng.step()
    assert eng._chance_quality(eng.home, eng.away) == 1.0
    assert all(eng._matchup_factor(t, k) == 1.0 for t in (eng.home, eng.away) for k in KINDS)
    assert eng._card_factor(eng.home) == 1.0


# ===========================================================================
# 2) API: enumlar, etiketler, carpim kurallari, dogrulama, JSONB
# ===========================================================================

def test_enums_values_and_turkish_labels():
    assert [p.value for p in PassingStyle] == ["SHORT", "MIXED", "DIRECT"]
    assert [t.value for t in Tempo] == ["SLOW", "NORMAL", "FAST"]
    assert [p.value for p in Pressing] == ["OWN_HALF", "MIDFIELD", "ALL_OVER"]
    assert [f.value for f in AttackingFocus] == ["CENTRE", "MIXED", "FLANKS"]
    assert AttackingWidth is AttackingFocus
    assert PASSING_LABELS == {PassingStyle.SHORT: "Kısa Pas", PassingStyle.MIXED: "Karışık",
                              PassingStyle.DIRECT: "Direkt Oyun"}
    assert TEMPO_LABELS == {Tempo.SLOW: "Yavaş", Tempo.NORMAL: "Normal", Tempo.FAST: "Hızlı"}
    assert PRESSING_LABELS == {Pressing.OWN_HALF: "Kendi Yarı Sahasında", Pressing.MIDFIELD: "Orta Sahada",
                               Pressing.ALL_OVER: "Tüm Sahada"}
    assert FOCUS_LABELS == {AttackingFocus.CENTRE: "Merkezden", AttackingFocus.MIXED: "Karışık",
                            AttackingFocus.FLANKS: "Kanatlardan"}
    assert FIELD_NAMES == ("mentality", "tackling", "passing_style", "tempo", "pressing", "attacking_focus",
                           "offside_trap", "counter_attack")


def test_neutral_options_are_exactly_one():
    for table in (PASSING_EFFECTS[PassingStyle.MIXED], TEMPO_EFFECTS[Tempo.NORMAL],
                  PRESSING_EFFECTS[Pressing.MIDFIELD]):
        assert all(v == 1.0 for v in vars(table).values())
    assert FOCUS_EFFECTS[AttackingFocus.MIXED].attack == FOCUS_EFFECTS[AttackingFocus.MIXED].chance_quality == 1.0
    assert FOCUS_EFFECTS[AttackingFocus.MIXED].attribute_influence == 0.0
    inst = TeamInstructions()
    assert inst.is_default and inst.is_basic
    assert [inst.strength_factor(k) for k in KINDS] == [1.0, 1.0, 1.0]
    assert (inst.fatigue_factor, inst.card_factor, inst.straight_red_factor, inst.injury_factor,
            inst.chance_quality) == (1.0, 1.0, 1.0, 1.0, 1.0)
    assert not TeamInstructions(offside_trap=True).is_default
    assert not TeamInstructions(tempo=Tempo.FAST).is_basic and TeamInstructions(Mentality.PARK_THE_BUS).is_basic


@pytest.mark.parametrize("passing,tempo,pressing,focus,counter", [
    (PassingStyle.SHORT, Tempo.FAST, Pressing.ALL_OVER, AttackingFocus.FLANKS, True),
    (PassingStyle.DIRECT, Tempo.SLOW, Pressing.OWN_HALF, AttackingFocus.CENTRE, False),
    (PassingStyle.MIXED, Tempo.NORMAL, Pressing.MIDFIELD, AttackingFocus.MIXED, True),
])
@pytest.mark.parametrize("mentality,tackling", [(Mentality.ALL_OUT_ATTACK, Tackling.HARD),
                                                (Mentality.PARK_THE_BUS, Tackling.CALM)])
def test_factor_products(mentality, tackling, passing, tempo, pressing, focus, counter):
    inst = TeamInstructions(mentality, tackling, passing, tempo, pressing, focus, counter_attack=counter)
    m, t = MENTALITY_EFFECTS[mentality], TACKLING_EFFECTS[tackling]
    p, tp, pr, fc = PASSING_EFFECTS[passing], TEMPO_EFFECTS[tempo], PRESSING_EFFECTS[pressing], FOCUS_EFFECTS[focus]
    counter_mid = COUNTER_ATTACK.midfield if counter else 1.0
    assert inst.strength_factor("attack") == pytest.approx(m.attack * p.attack * tp.attack * fc.attack, rel=1e-15)
    assert inst.strength_factor("midfield") == pytest.approx(m.midfield * p.midfield * tp.midfield * counter_mid,
                                                             rel=1e-15)
    assert inst.strength_factor("defense") == pytest.approx(m.defense * t.defense * pr.defense, rel=1e-15)
    assert inst.fatigue_factor == pytest.approx(m.fatigue * t.fatigue * p.fatigue * tp.fatigue * pr.fatigue,
                                                rel=1e-15)
    assert inst.card_factor == pytest.approx(t.card * pr.card, rel=1e-15)
    assert inst.chance_quality == pytest.approx(p.chance_quality * tp.chance_quality * fc.chance_quality, rel=1e-15)
    assert (inst.straight_red_factor, inst.injury_factor) == (t.straight_red, t.injury)


def test_basic_instructions_keep_exact_legacy_factors():
    for m, t in itertools.product(Mentality, Tackling):
        inst = TeamInstructions(m, t)
        me, te = MENTALITY_EFFECTS[m], TACKLING_EFFECTS[t]
        assert (inst.strength_factor("attack"), inst.strength_factor("midfield")) == (me.attack, me.midfield)
        assert inst.strength_factor("defense") == me.defense * te.defense
        assert inst.fatigue_factor == me.fatigue * te.fatigue and inst.card_factor == te.card


def test_coercion_parsing_and_validation():
    inst = TeamInstructions("ALL_OUT_ATTACK", "Sert Oyna", "Kısa Pas", "FAST", "Tüm Sahada", "Kanatlardan")
    assert inst == TeamInstructions(Mentality.ALL_OUT_ATTACK, Tackling.HARD, PassingStyle.SHORT, Tempo.FAST,
                                    Pressing.ALL_OVER, AttackingFocus.FLANKS)
    assert inst.passing_style is PassingStyle.SHORT and hash(inst) == hash(replace(inst))
    assert parse_passing_style("Direkt Oyun") is PassingStyle.DIRECT and parse_tempo(Tempo.SLOW) is Tempo.SLOW
    assert parse_pressing("OWN_HALF") is Pressing.OWN_HALF and parse_attacking_focus("Merkezden") is AttackingFocus.CENTRE
    for bad in (lambda: TeamInstructions(passing_style="Tiki-taka"), lambda: TeamInstructions(tempo=3),
                lambda: TeamInstructions(offside_trap="evet"), lambda: TeamInstructions(counter_attack=1)):
        with pytest.raises(ValueError):
            bad()
    with pytest.raises(ValueError, match="Bilinmeyen talimat alanı: gegenpress"):
        parse_instruction_changes({"gegenpress": True})
    with pytest.raises(ValueError, match="Bilinmeyen pres"):
        parse_instruction_changes({"pressing": "HIGH"})
    with pytest.raises(ValueError, match="Ofsayt taktiği açık/kapalı"):
        parse_instruction_changes({"offside_trap": "yes"})
    with pytest.raises(ValueError, match="Bilinmeyen güç türü"):
        inst.strength_factor("goalkeeping")


def test_to_dict_from_dict_roundtrip_for_every_combination():
    combos = itertools.product(Mentality, Tackling, PassingStyle, Tempo, Pressing, AttackingFocus,
                               (False, True), (False, True))
    count = 0
    for values in combos:
        inst = TeamInstructions(*values)
        data = inst.to_dict()
        assert list(data) == list(FIELD_NAMES)
        assert TeamInstructions.from_dict(json.loads(json.dumps(data))) == inst
        count += 1
    assert count == 3 ** 6 * 4
    assert TeamInstructions().to_dict() == {
        "mentality": "BALANCED", "tackling": "NORMAL", "passing_style": "MIXED", "tempo": "NORMAL",
        "pressing": "MIDFIELD", "attacking_focus": "MIXED", "offside_trap": False, "counter_attack": False,
    }


def test_from_dict_is_tolerant():
    assert TeamInstructions.from_dict(None) == TeamInstructions()
    assert TeamInstructions.from_dict([1, 2]) == TeamInstructions()
    assert TeamInstructions.from_dict({"unknown": 1, "future_axis": "X"}) == TeamInstructions()
    data = {"mentality": "NOPE", "tackling": None, "passing_style": "Kısa Pas", "tempo": 7, "pressing": "ALL_OVER",
            "attacking_focus": "", "offside_trap": "true", "counter_attack": 0, "extra": {"a": 1}}
    assert TeamInstructions.from_dict(data) == TeamInstructions(passing_style=PassingStyle.SHORT,
                                                                pressing=Pressing.ALL_OVER, offside_trap=True)
    assert TeamInstructions.from_dict({"offside_trap": "belki", "counter_attack": 2}) == TeamInstructions()
    assert TeamInstructions.from_dict({"counter_attack": "Açık", "offside_trap": 1}) == \
        TeamInstructions(counter_attack=True, offside_trap=True)
    legacy = {"mentality": "PARK_THE_BUS", "tackling": "CALM"}                   # 9. Asama kaydi
    assert TeamInstructions.from_dict(legacy) == TeamInstructions(Mentality.PARK_THE_BUS, Tackling.CALM)


def test_with_changes_describe_and_change_text():
    base = TeamInstructions(Mentality.PARK_THE_BUS)
    new = base.with_changes({"tempo": "Hızlı", "counter_attack": True})
    assert (new.mentality, new.tempo, new.counter_attack) == (Mentality.PARK_THE_BUS, Tempo.FAST, True)
    assert base.with_changes({}) == base
    assert new.changes_from(base) == ["tempo Hızlı", "kontra atak açık"]
    everything = TeamInstructions(Mentality.ALL_OUT_ATTACK, Tackling.HARD, PassingStyle.DIRECT, Tempo.SLOW,
                                  Pressing.OWN_HALF, AttackingFocus.CENTRE, True, True)
    assert everything.changes_from(TeamInstructions()) == [
        "zihniyet Çok Ofansif (Topyekûn Hücum)", "sertlik Sert Oyna", "pas stili Direkt Oyun", "tempo Yavaş",
        "pres Kendi Yarı Sahasında", "hücum yönü Merkezden", "ofsayt taktiği açık", "kontra atak açık"]
    assert TeamInstructions().changes_from(everything)[-2:] == ["ofsayt taktiği kapalı", "kontra atak kapalı"]
    assert TeamInstructions(Mentality.BALANCED, Tackling.HARD).describe() == "Zihniyet: Dengeli · Sertlik: Sert Oyna"
    assert new.describe() == ("Zihniyet: Çok Defansif (Otobüsü Çek) · Sertlik: Normal · Tempo: Hızlı · "
                              "Kontra Atak: Açık")
    assert len(TeamInstructions().extended_parts(include_defaults=True)) == 6


def test_set_instructions_event_lists_only_changed_axes():
    eng = stepped(17, 12)
    ev = eng.set_instructions(eng.home, TeamInstructions(passing_style=PassingStyle.SHORT, tempo=Tempo.FAST))
    assert (ev.type, ev.detail, ev.team_id) == (EventType.TACTICAL_CHANGE, "instructions", 1)
    assert ev.description == "Talimat (Ev): pas stili Kısa Pas, tempo Hızlı."
    ev2 = eng.set_instructions(eng.home, replace(eng.home.instructions, offside_trap=True))
    assert ev2.description == "Talimat (Ev): ofsayt taktiği açık."
    assert eng.set_instructions(eng.home, TeamInstructions.from_dict(eng.home.instructions.to_dict())) is None


def test_live_partial_instructions_keep_other_axes_and_text():
    live = LiveMatch.create(engine(4), 1, instructions=TeamInstructions(pressing=Pressing.ALL_OVER))
    assert live.managed_team.manager_controlled and not live.opponent_team.manager_controlled
    assert live.instructions_text() == "Dengeli · Normal · Pres: Tüm Sahada"
    live.run(max_steps=11)
    ev = live.set_instructions("ALL_OUT_ATTACK", "HARD")                       # eski imza
    assert ev.description == "Talimat (Ev): zihniyet Çok Ofansif (Topyekûn Hücum), sertlik Sert Oyna."
    assert live.instructions.pressing is Pressing.ALL_OVER                     # korunur
    ev2 = live.set_instructions(tempo="Hızlı", counter_attack=True)
    assert ev2.description == "Talimat (Ev): tempo Hızlı, kontra atak açık."
    assert live.instructions == TeamInstructions(Mentality.ALL_OUT_ATTACK, Tackling.HARD, pressing=Pressing.ALL_OVER,
                                                 tempo=Tempo.FAST, counter_attack=True)
    assert live.set_team_instructions(TeamInstructions()).description.startswith("Talimat (Ev): zihniyet Dengeli")
    assert live.history[-1].endswith("kontra atak kapalı.")
    with pytest.raises(ValueError):
        live.set_instructions(pressing="Gegenpress")


# ===========================================================================
# 3) Rakibe bagli carpanlar (deterministik)
# ===========================================================================

def test_pressing_weakens_opponent_midfield_scaled_by_passing_style():
    # match_form (13A/S4) takim basina bir kez cekilir; bu test iki takimi dogrudan
    # karsilastirdigi icin "gunun formu" kapatilir.
    eng = stepped(21, 30, cfg=EngineConfig(match_form=False))
    base = eng._team_strength(eng.away, "midfield")
    press = PRESSING_EFFECTS[Pressing.ALL_OVER].opponent_midfield
    eng.set_instructions(eng.home, TeamInstructions(pressing=Pressing.ALL_OVER))
    assert eng._team_strength(eng.away, "midfield") == pytest.approx(base * press, rel=1e-12)
    for style in PassingStyle:
        eng.set_instructions(eng.away, TeamInstructions(passing_style=style))
        exposure = PASSING_EFFECTS[style].press_exposure
        expected = base * PASSING_EFFECTS[style].midfield * (1 + (press - 1) * exposure)
        assert eng._team_strength(eng.away, "midfield") == pytest.approx(expected, rel=1e-12)
    eng.set_instructions(eng.home, TeamInstructions(pressing=Pressing.OWN_HALF))
    eng.set_instructions(eng.away, TeamInstructions())
    assert eng._team_strength(eng.away, "midfield") == pytest.approx(base * 1.05, rel=1e-12)
    # kendi takiminin gucu yalnizca kendi talimatindan etkilenir
    assert eng._team_strength(eng.home, "defense") > eng._team_strength(eng.away, "defense")


@pytest.mark.parametrize("opponent,passing,expected", [
    (TeamInstructions(), PassingStyle.MIXED, 1.0),
    (TeamInstructions(Mentality.ALL_OUT_ATTACK), PassingStyle.MIXED, 1.10),
    (TeamInstructions(Mentality.ALL_OUT_ATTACK), PassingStyle.DIRECT, 1.13),
    (TeamInstructions(Mentality.ALL_OUT_ATTACK, pressing=Pressing.ALL_OVER), PassingStyle.MIXED, 1.15),
    (TeamInstructions(Mentality.PARK_THE_BUS), PassingStyle.MIXED, 0.94),
    (TeamInstructions(Mentality.PARK_THE_BUS, pressing=Pressing.OWN_HALF), PassingStyle.DIRECT, 0.91),
])
def test_counter_attack_depends_on_how_open_the_opponent_plays(opponent, passing, expected):
    eng = stepped(22, 30)
    eng.set_instructions(eng.away, opponent)
    eng.set_instructions(eng.home, TeamInstructions(passing_style=passing))
    plain_attack = eng._team_strength(eng.home, "attack")
    plain_mid = eng._team_strength(eng.home, "midfield")
    plain_quality = eng._chance_quality(eng.home, eng.away)
    eng.set_instructions(eng.home, TeamInstructions(passing_style=passing, counter_attack=True))
    assert eng._counter_attack_factor(eng.home) == pytest.approx(expected, rel=1e-12)
    assert eng._team_strength(eng.home, "attack") == pytest.approx(plain_attack * expected, rel=1e-12)
    assert eng._team_strength(eng.home, "midfield") == pytest.approx(plain_mid * COUNTER_ATTACK.midfield, rel=1e-12)
    quality_bonus = 1 + max(0.0, expected - 1) * COUNTER_ATTACK.quality_share
    assert eng._chance_quality(eng.home, eng.away) == pytest.approx(plain_quality * quality_bonus, rel=1e-12)


def test_counter_attack_gains_when_the_opponent_is_desperate_late():
    eng = stepped(23, 75)
    eng.home.stats.goals, eng.away.stats.goals = eng.away.stats.goals + 1, eng.away.stats.goals
    eng.minute = 80
    assert eng._is_pressing(eng.away)
    eng.set_instructions(eng.home, TeamInstructions(counter_attack=True))
    assert eng._counter_attack_factor(eng.home) == pytest.approx(1 + COUNTER_ATTACK.vs_desperate, rel=1e-12)


@pytest.mark.parametrize("forward_pace,expected_defense,through_ball", [
    (80, OFFSIDE_TRAP.base_defense, 1.0),                  # esit hiz
    (95, OFFSIDE_TRAP.base_defense - OFFSIDE_TRAP.pace_influence * 15, 1 + OFFSIDE_TRAP.through_ball_quality * 15),
    (100, 0.91, OFFSIDE_TRAP.through_ball_cap),            # tavan: arkaya atilan top en fazla %8 netlesir
    (60, 1.10, 1.0),                                       # yavas forvetler: savunma tavanda
])
def test_offside_trap_vs_forward_pace(forward_pace, expected_defense, through_ball):
    eng = stepped(24, 5, cfg=EngineConfig(base_card=0.0, base_injury=0.0))
    for p in eng.away.players:
        if p.position is Position.FWD:
            p.pace = forward_pace
    for p in eng.away.on_pitch + eng.home.on_pitch:
        p.energy = 100.0
    base_def = eng._team_strength(eng.home, "defense")
    eng.set_instructions(eng.home, TeamInstructions(offside_trap=True))
    assert eng._offside_trap_factor(eng.home) == pytest.approx(min(1.10, max(0.90, expected_defense)), abs=1e-9)
    assert eng._team_strength(eng.home, "defense") == pytest.approx(
        base_def * eng._offside_trap_factor(eng.home), rel=1e-12)
    assert eng._chance_quality(eng.away, eng.home) == pytest.approx(through_ball, abs=1e-9)
    # FM hizlanma verisi hizi etkiler; yorgun forvet yavaslar
    fwd = [p for p in eng.away.on_pitch if p.role is Position.FWD]
    edge = eng._pace_edge(eng.home)
    for p in fwd:
        p.energy = 0.0
    assert eng._pace_edge(eng.home) < edge
    for p in fwd:
        p.energy, p.attributes = 100.0, {"acceleration": 20}
    assert eng._pace_edge(eng.home) > edge or forward_pace == 100


def test_attacking_focus_quality_scales_with_player_skills():
    eng = stepped(25, 20)
    team = eng.home
    for focus in AttackingFocus:
        eng.set_instructions(team, TeamInstructions(attacking_focus=focus))
        assert eng._team_strength(team, "attack") > 0
    eng.set_instructions(team, TeamInstructions(attacking_focus=AttackingFocus.FLANKS))
    for p in team.players:
        p.attributes = {"crossing": 19, "heading": 19, "jumping_reach": 19}
    good = eng._chance_quality(team, eng.away)
    for p in team.players:
        p.attributes = {"crossing": 6, "heading": 6, "jumping_reach": 6}
    poor = eng._chance_quality(team, eng.away)
    flanks = FOCUS_EFFECTS[AttackingFocus.FLANKS].chance_quality
    assert good == pytest.approx(flanks * 1.07) and poor == pytest.approx(flanks * 0.93)
    # merkez: kisa pas / teknik + bitiricilik; kanat becerisi merkezi etkilemez
    eng.set_instructions(team, TeamInstructions(attacking_focus=AttackingFocus.CENTRE))
    centre_poor_crossers = eng._chance_quality(team, eng.away)
    for p in team.players:
        p.attributes = {"crossing": 19, "heading": 19, "technique": 19, "passing": 19, "finishing": 19}
    assert eng._chance_quality(team, eng.away) > centre_poor_crossers
    eng.set_instructions(team, TeamInstructions(attacking_focus=AttackingFocus.MIXED))
    assert eng._chance_quality(team, eng.away) == 1.0


def test_pressing_card_factor_reaches_discipline_share():
    eng = stepped(26, 10)
    eng.set_instructions(eng.home, TeamInstructions(pressing=Pressing.ALL_OVER))
    assert eng._card_factor(eng.home) == PRESSING_EFFECTS[Pressing.ALL_OVER].card
    eng.set_instructions(eng.home, TeamInstructions(tackling=Tackling.HARD, pressing=Pressing.OWN_HALF))
    assert eng._card_factor(eng.home) == pytest.approx(2.0 * 0.90)


def test_extended_instructions_never_draw_random_numbers_and_replay_identically():
    inst = TeamInstructions(Mentality.ALL_OUT_ATTACK, passing_style=PassingStyle.DIRECT, tempo=Tempo.FAST,
                            pressing=Pressing.ALL_OVER, attacking_focus=AttackingFocus.FLANKS, offside_trap=True,
                            counter_attack=True)
    eng = stepped(27, 50)
    state = eng.rng.getstate()
    eng.set_instructions(eng.home, inst)
    eng._chance_quality(eng.home, eng.away)
    [eng._team_strength(t, k) for t in (eng.home, eng.away) for k in KINDS]
    assert eng.rng.getstate() == state

    def play() -> str:
        e = engine(27)
        e.set_instructions(e.home, inst)
        e.set_instructions(e.away, TeamInstructions(pressing=Pressing.OWN_HALF, offside_trap=True))
        return fingerprint(e.simulate())

    assert play() == play() != fingerprint(engine(27).simulate())


# ===========================================================================
# 4) Istatistik (sabit tohumlar -> deterministik)
# ===========================================================================

STAT_SEEDS = range(220)
DEFAULT = TeamInstructions()


@cache
def season(home: TeamInstructions, away: TeamInstructions = DEFAULT) -> dict[str, float]:
    tot: Counter[str] = Counter()
    energy: list[float] = []
    for seed in STAT_SEEDS:
        eng = engine(seed)
        eng.set_instructions(eng.home, home)
        eng.set_instructions(eng.away, away)
        r = eng.simulate()
        for side, team in (("home", r.home), ("away", r.away)):
            tot[f"{side}_shots"] += team.stats.shots
            tot[f"{side}_goals"] += team.stats.goals
            tot[f"{side}_poss"] += team.stats.possession_minutes
            tot[f"{side}_cards"] += team.stats.yellow_cards + team.stats.red_cards
        energy += [p.energy for p in r.home.players
                   if p.entered_minute == 0 and p.left_minute == 90 and p.role is not Position.GK]
    out = {k: v / len(STAT_SEEDS) for k, v in tot.items()}
    out["home_poss_share"] = tot["home_poss"] / (tot["home_poss"] + tot["away_poss"])
    out["home_energy"] = sum(energy) / len(energy)
    return out


def test_all_over_pressing_tires_own_team_and_lowers_opponent_midfield_share():
    base, press = season(DEFAULT), season(TeamInstructions(pressing=Pressing.ALL_OVER))
    msg = f"notr {base}, tum sahada {press}"
    assert press["home_energy"] < base["home_energy"] - 6, msg
    assert press["home_poss_share"] > base["home_poss_share"] + 0.02, msg          # rakibin orta sahasi zayif
    assert press["home_cards"] > base["home_cards"] * 1.08, msg                    # biraz daha fazla faul
    own = season(TeamInstructions(pressing=Pressing.OWN_HALF))
    assert own["home_energy"] > base["home_energy"] + 2.5 and own["home_poss_share"] < base["home_poss_share"], msg


def test_direct_passing_creates_more_shots_with_less_possession():
    base, direct = season(DEFAULT), season(TeamInstructions(passing_style=PassingStyle.DIRECT))
    msg = f"notr {base}, direkt {direct}"
    assert direct["home_shots"] > base["home_shots"] * 1.03, msg
    assert direct["home_poss_share"] < base["home_poss_share"] - 0.008, msg
    assert direct["home_energy"] < base["home_energy"], msg


def test_short_passing_keeps_the_ball_fewer_shots_less_fatigue():
    base, short = season(DEFAULT), season(TeamInstructions(passing_style=PassingStyle.SHORT))
    msg = f"notr {base}, kisa pas {short}"
    assert short["home_poss_share"] > base["home_poss_share"] + 0.015, msg
    assert short["home_shots"] < base["home_shots"] * 0.95, msg
    assert short["away_shots"] < base["away_shots"], msg
    assert short["home_energy"] > base["home_energy"] + 1.5, msg


def test_fast_tempo_more_attacks_more_fatigue_slow_tempo_opposite():
    base = season(DEFAULT)
    fast, slow = season(TeamInstructions(tempo=Tempo.FAST)), season(TeamInstructions(tempo=Tempo.SLOW))
    msg = f"notr {base}, hizli {fast}, yavas {slow}"
    assert fast["home_shots"] > base["home_shots"] * 1.02 and fast["home_energy"] < base["home_energy"] - 3, msg
    assert fast["home_poss_share"] < base["home_poss_share"], msg                 # pas hatalari
    assert slow["home_shots"] < base["home_shots"] and slow["home_energy"] > base["home_energy"] + 3, msg


def test_flanks_create_more_shots_than_centre():
    centre = season(TeamInstructions(attacking_focus=AttackingFocus.CENTRE))
    flanks = season(TeamInstructions(attacking_focus=AttackingFocus.FLANKS))
    assert flanks["home_shots"] > centre["home_shots"] * 1.04, (centre, flanks)


def test_extended_instruction_matches_stay_realistic():
    wild = TeamInstructions(Mentality.ALL_OUT_ATTACK, passing_style=PassingStyle.DIRECT, tempo=Tempo.FAST,
                            pressing=Pressing.ALL_OVER, attacking_focus=AttackingFocus.FLANKS, counter_attack=True)
    stats = season(wild, TeamInstructions(Mentality.PARK_THE_BUS, pressing=Pressing.OWN_HALF, offside_trap=True))
    goals = stats["home_goals"] + stats["away_goals"]
    shots = stats["home_shots"] + stats["away_shots"]
    assert 1.8 <= goals <= 4.2 and 16 <= shots <= 36, stats


# ===========================================================================
# 5) AI talimatlari
# ===========================================================================

def test_ai_instructions_pure_rules():
    early_weak_away = ai_instructions(0.80, False, 0, 0)
    assert early_weak_away.mentality is Mentality.PARK_THE_BUS and early_weak_away.counter_attack
    assert early_weak_away.pressing is Pressing.OWN_HALF
    assert ai_instructions(0.80, False, 0, 0) == early_weak_away                 # saf / deterministik
    slightly_weak = ai_instructions(0.90, False, 0, 10)
    assert slightly_weak.mentality is Mentality.BALANCED and slightly_weak.counter_attack
    assert ai_instructions(0.90, True, 0, 10).is_default                         # evde ayni fark: notr
    strong_home = ai_instructions(1.15, True, 0, 5)
    assert strong_home.passing_style is PassingStyle.SHORT and strong_home.pressing is Pressing.ALL_OVER
    assert ai_instructions(1.0, True, 0, 30).is_default

    trailing_late = ai_instructions(0.8, False, -1, 72)
    assert trailing_late.mentality is Mentality.ALL_OUT_ATTACK and trailing_late.tempo is Tempo.FAST
    assert not trailing_late.counter_attack
    chasing = ai_instructions(0.8, False, -1, 62)
    assert chasing.mentality is Mentality.BALANCED and chasing.tempo is Tempo.FAST
    assert ai_instructions(1.0, True, -1, 30).is_default                         # erken: plan degismez
    lost_cause = ai_instructions(1.0, True, -3, 80)
    assert lost_cause.mentality is not Mentality.ALL_OUT_ATTACK and lost_cause.tempo is Tempo.SLOW

    protect = ai_instructions(1.3, True, 1, 78)
    assert protect.mentality is Mentality.PARK_THE_BUS and protect.tempo is Tempo.SLOW
    comfortable = ai_instructions(1.0, True, 2, 80)
    assert comfortable.mentality is Mentality.BALANCED and comfortable.tempo is Tempo.SLOW
    assert ai_instructions(1.2, True, 0, 85).mentality is Mentality.ALL_OUT_ATTACK     # beraberlik, guclu taraf
    assert ai_instructions(0.8, False, 0, 85).mentality is Mentality.PARK_THE_BUS       # zayif taraf puana razi
    for ratio, home, diff, minute in itertools.product((0.5, 0.85, 1.0, 1.4), (True, False), (-3, -1, 0, 1, 2),
                                                       (0, 30, 60, 65, 75, 89, 105)):
        assert isinstance(ai_instructions(ratio, home, diff, minute), TeamInstructions)


def _ai_match(seed: int, **kw) -> tuple[MatchEngine, list]:
    eng = MatchEngine(make_team(1, "Ev", 86), make_team(2, "Dep", 72), seed=seed,
                      config=EngineConfig(ai_tactics=True), **kw)
    return eng, eng.simulate().events


def test_ai_tactics_adjust_clubs_but_not_managed_or_planned_teams():
    changed = 0
    for seed in range(12):
        eng = MatchEngine(make_team(1, "Ev", 86), make_team(2, "Dep", 72), seed=seed, config=EngineConfig(ai_tactics=True))
        eng.start()
        eng.step()                                                               # duduk: AI talimati olay yazmadan
        assert eng.events[0].type is EventType.KICK_OFF and len(eng.events) == 1
        assert eng.away.instructions.mentality is Mentality.PARK_THE_BUS and eng.away.instructions.counter_attack
        assert eng.home.instructions.pressing is Pressing.ALL_OVER
        eng.run_to_end()
        ai_events = [e for e in eng.events if e.detail == "ai"]
        changed += len(ai_events)
        assert all(e.type is EventType.TACTICAL_CHANGE and e.description.startswith("Talimat (") for e in ai_events)
    assert changed >= 6

    # yonetilen takim: AI dokunmaz
    eng = MatchEngine(make_team(1, "Ev", 86), make_team(2, "Dep", 72), seed=3, config=EngineConfig(ai_tactics=True))
    live = LiveMatch.create(eng, 2, instructions=TeamInstructions(tempo=Tempo.SLOW))
    live.play_to_end()
    assert eng.away.instructions == TeamInstructions(tempo=Tempo.SLOW)
    assert not any(e.detail == "ai" and e.team_id == 2 for e in eng.events)
    assert eng.home.instructions != TeamInstructions() or any(e.detail == "ai" and e.team_id == 1 for e in eng.events)

    # oyun plani olan takim: AI dokunmaz
    from match_plan import PlanAction, PlanRule, PlanTrigger

    plan = MatchPlan((PlanRule(PlanTrigger(89), PlanAction(instructions={"tempo": "SLOW"})),))
    eng2, events = _ai_match(4, away_plan=plan)
    assert not any(e.detail == "ai" and e.team_id == 2 for e in events)


def test_ai_tactics_off_by_default_and_deterministic_when_on():
    assert fingerprint(engine(8).simulate()) == fingerprint(engine(8, cfg=EngineConfig(ai_tactics=False)).simulate())
    a = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 74), seed=8, config=EngineConfig(ai_tactics=True))
    b = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 74), seed=8, config=EngineConfig(ai_tactics=True))
    assert fingerprint(a.simulate()) == fingerprint(b.simulate())


def test_matchteam_direct_fields_and_random_replay_through_live_controller():
    home = make_team(1, "Ev", 80)
    home.instructions = TeamInstructions(passing_style=PassingStyle.SHORT)
    assert isinstance(home, MatchTeam)
    eng = MatchEngine(home, make_team(2, "Dep", 80), seed=31)
    live = LiveMatch.create(eng, 1)
    rng = random.Random(5)
    while not live.finished:
        live.run(max_steps=rng.randint(1, 9))
        if live.paused:
            live.resume()
    reference = MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=31)
    reference.set_instructions(reference.home, TeamInstructions(passing_style=PassingStyle.SHORT))
    assert fingerprint(live.result()) == fingerprint(reference.simulate())
