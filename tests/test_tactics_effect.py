"""
tests/test_tactics_effect.py
============================
14E "taktik etkisi ve karsi hamle" (EngineConfig.tactics_v2). Veritabani ve Streamlit gerektirmez.

    1) Norm      taktik degisiklikten sonra norm ve dizilis tarzi sahadaki GERCEK sekilden; 10 kisi kalan takim 11
                 yuvali kalir (13A D6 kirmizi kart bedeli korunur)
    2) Notr      varsayilan talimatta concede_quality ve karsi hamle carpanlari TAM 1.0; yapisal guc = tam guc;
                 rol degisikligi olmayan maclarda bayrak acik / kapali BIT-BIT ayni
    3) Butce     otobus rakibin sut HACMINI dusurur, sans yogunlugu (kalite) yapisal gucten gelir
    4) AI        ai_formation karar tablosu; motor AI dizilisi degistirir, menajer takimina dokunmaz
    5) Duman     n = 800 (kabul esiklerinin yarisi): zayif takim savunma plani ve bir dongusel cift.
                 Tam olcum: .claude/phase14/kanit/14E_supurme.txt (n >= 3.000 / kol)
    6) Determinizm  bayrak kapaliyken 0-9 tohumlari 14E oncesi altin listeyle ayni

  CM_TEST_NO_DB=1 python -m pytest -q tests/test_tactics_effect.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instructions import (  # noqa: E402
    AI_FORMATION_MAX_CHANGES,
    MATCHUP_EFFECTS,
    MENTALITY_EFFECTS,
    PRESSING_EFFECTS,
    AttackingFocus,
    Mentality,
    PassingStyle,
    Pressing,
    TeamInstructions,
    Tempo,
    ai_formation,
)
from match_engine import (  # noqa: E402
    FORMATION_STYLE,
    EngineConfig,
    EventType,
    MatchEngine,
    _formation_norm,
    _shape_norm,
    _shape_style,
)
from models import Position  # noqa: E402
from tests.engine_stats import duel, make_team  # noqa: E402

V2 = EngineConfig(tactics_v2=True)
OFF = EngineConfig(tactics_v2=False)
KINDS = ("attack", "midfield", "defense")
PARK_COUNTER = TeamInstructions(Mentality.PARK_THE_BUS, passing_style=PassingStyle.DIRECT, pressing=Pressing.OWN_HALF,
                                counter_attack=True)


def _engine(cfg: EngineConfig, home=None, away=None, seed: int = 1) -> MatchEngine:
    return MatchEngine(home or make_team(1, "Ev", 80), away or make_team(2, "Dep", 78), seed=seed, config=cfg)


# ===========================================================================
# 1) Norm: gercek rol sayimi
# ===========================================================================

def _swapped_442(cfg: EngineConfig) -> MatchEngine:
    """4-4-2; 60. dakikada bir orta saha cikar, kulubeden forvet FORVET olarak girer (taktik degisiklik)."""
    eng = _engine(cfg)
    team = eng.home
    mid = next(p for p in reversed(team.on_pitch) if p.role is Position.MID)
    fwd = next(p for p in team.bench if p.position is Position.FWD)
    team.remove_player(mid, 60)
    mid.substituted = True
    team.field_player(fwd, Position.FWD, 60)
    eng.minute = 80                          # taze bacak etkisi (15 dk) bitti
    return eng


def _lined_up_433(cfg: EngineConfig, xi: dict[int, Position]) -> MatchEngine:
    home = make_team(1, "Ev", 80)
    home.formation = (4, 3, 3)
    home.preferred_xi = dict(xi)
    eng = _engine(cfg, home=home)
    eng.minute = 80
    return eng


def test_norm_after_midfielder_to_forward_swap_equals_433_lineup():
    swapped = _swapped_442(V2)
    xi = {p.id: p.role for p in swapped.home.on_pitch}
    assert sorted(r.value for r in xi.values()).count("FWD") == 3
    lined = _lined_up_433(V2, xi)
    assert {p.id for p in lined.home.on_pitch} == set(xi)
    assert swapped._shape(swapped.home) == lined._shape(lined.home) == (1, 4, 3, 3)
    for kind in KINDS:
        assert swapped._team_strength(swapped.home, kind) == pytest.approx(
            lined._team_strength(lined.home, kind), rel=0, abs=1e-9)


def test_flag_off_keeps_the_old_formation_norm_bug():
    """Bayrak kapaliyken eski davranis (ilan edilen 4-4-2 normu) aynen durur: hucum sisiyor."""
    swapped = _swapped_442(OFF)
    lined = _lined_up_433(OFF, {p.id: p.role for p in swapped.home.on_pitch})
    assert swapped._team_strength(swapped.home, "attack") > lined._team_strength(lined.home, "attack") * 1.01


def test_ten_men_keep_an_eleven_slot_norm():
    """Kirmizi kart: bos yuva sayilir; 10 kisi kalan takimin gucu bayrak acik / kapali ayni (D6 bedeli korunur)."""
    values = []
    for cfg in (OFF, V2):
        eng = _engine(cfg)
        eng.start()
        eng.step()
        defender = next(p for p in eng.home.on_pitch if p.role is Position.DEF)
        eng._send_off(eng.home, defender, second_yellow=False)
        values.append([eng._team_strength(eng.home, k) for k in KINDS])
        if cfg.tactics_v2:
            assert eng.home.player_count == 10
            assert eng._shape(eng.home) == (1, 4, 4, 2)
            assert eng.home.open_slots == [Position.DEF]
    assert values[0] == values[1]


def test_shape_norm_and_style_match_the_formation_tables():
    for shape, style in FORMATION_STYLE.items():
        for kind in KINDS:
            assert _shape_norm((1, *shape), kind) == _formation_norm(shape, kind)
            assert _shape_style(shape, kind) == style[kind]
    # tabloda olmayan sekil: dogrusal egim, sinirli
    assert _shape_style((3, 4, 3), "attack") > FORMATION_STYLE[(4, 4, 2)]["attack"]
    assert _shape_style((3, 4, 3), "defense") < FORMATION_STYLE[(4, 4, 2)]["defense"]
    assert 0.85 <= _shape_style((1, 4, 5), "defense") <= 1.15


def test_v2_tactical_swaps_respect_line_limits():
    eng = _engine(V2)
    team = eng.home
    eng.minute = 70
    eng.away.stats.goals = 1                 # ev sahibi geride: hucumcu degisiklik
    swap = eng._tactical_swap(team)
    assert swap is not None and swap[1] is Position.FWD
    out, role, _ = swap
    team.remove_player(out, 70)
    team.field_player(next(p for p in team.bench if p.position is Position.FWD), role, 70)
    team.tactical_swaps_used += 1
    assert eng._shape(team)[3] == 3
    assert eng._tactical_swap(team) is None   # 3 forvet: dorduncu yok
    eng.away.stats.goals, team.stats.goals = 0, 1   # onde: forvet -> orta saha, en az 1 forvet kalir
    for _ in range(2):
        out, role, _ = eng._tactical_swap(team)
        team.remove_player(out, 75)
        team.field_player(next(p for p in team.bench if p.position is not Position.GK), role, 75)
        team.tactical_swaps_used += 1
    assert eng._tactical_swap(team) is None   # hak bitti (3)
    assert team.tactical_swaps_used == EngineConfig().max_tactical_swaps_v2


# ===========================================================================
# 2) Notr: varsayilan talimatta her sey tam 1.0
# ===========================================================================

def test_default_instructions_are_exactly_neutral():
    inst = TeamInstructions()
    assert inst.concede_quality == 1.0
    assert MENTALITY_EFFECTS[Mentality.BALANCED].concede_quality == 1.0
    assert PRESSING_EFFECTS[Pressing.MIDFIELD].concede_quality == 1.0
    eng = _engine(V2)
    for _ in range(40):
        eng.step()
    for team in (eng.home, eng.away):
        other = eng._opponent(team)
        assert eng._concede_factor(team, other) == 1.0
        for kind in KINDS:
            assert eng._matchup_factor(team, kind) == 1.0
            assert eng._team_strength(team, kind) == eng._team_strength(team, kind, structural=True)


def test_concede_quality_table():
    assert TeamInstructions(Mentality.PARK_THE_BUS).concede_quality < 1.0
    assert TeamInstructions(pressing=Pressing.OWN_HALF).concede_quality < 1.0
    assert TeamInstructions(Mentality.ALL_OUT_ATTACK).concede_quality > 1.0
    assert TeamInstructions(pressing=Pressing.ALL_OVER).concede_quality > 1.0
    assert TeamInstructions(offside_trap=True).concede_quality == 1.0
    both = TeamInstructions(Mentality.PARK_THE_BUS, pressing=Pressing.OWN_HALF)
    assert both.concede_quality == pytest.approx(MENTALITY_EFFECTS[Mentality.PARK_THE_BUS].concede_quality
                                                 * PRESSING_EFFECTS[Pressing.OWN_HALF].concede_quality)


def _outcome(r) -> tuple:
    return (r.home_score, r.away_score, len(r.events),
            tuple((e.minute, e.added_time, e.type, e.player_id, e.detail, e.description) for e in r.events),
            tuple((p.id, p.goals, p.shots, p.rating, p.energy) for t in (r.home, r.away) for p in t.players))


@pytest.mark.parametrize("home_ovr,away_ovr", [(80, 80), (70, 85)])
def test_flag_on_equals_off_for_default_instructions_without_role_changes(home_ovr, away_ovr):
    """Varsayilan talimat, taktik degisiklik yok: bayrak acik / kapali ozetler (olaylar dahil) BIREBIR ayni."""
    no_swaps = dict(max_tactical_swaps=0, max_tactical_swaps_v2=0)
    for seed in range(60):
        runs = [MatchEngine(make_team(1, "Ev", home_ovr), make_team(2, "Dep", away_ovr), seed=seed,
                            config=EngineConfig(tactics_v2=flag, **no_swaps)).simulate() for flag in (False, True)]
        assert _outcome(runs[0]) == _outcome(runs[1]), seed


# ===========================================================================
# 3) Hacim ile kalite ayri butce
# ===========================================================================

def _chance(cfg: EngineConfig, defending_inst: TeamInstructions) -> tuple[float, float]:
    eng = _engine(cfg, make_team(1, "Ev", 85), make_team(2, "Dep", 70))
    eng.away.instructions = defending_inst
    eng.away.manager_controlled = True
    eng.minute = 30
    return eng._chance_probability(eng.home, eng.away, 1.0)


def test_parked_bus_cuts_volume_without_giving_back_quality():
    base_p, base_density = _chance(V2, TeamInstructions())
    bus_p, bus_density = _chance(V2, TeamInstructions(Mentality.PARK_THE_BUS))
    assert bus_p < base_p * 0.92                     # ustun rakibin hacmi duser (yuksek p'de doygun)
    assert bus_density == base_density               # yapisal: talimat kaliteyi geri vermez
    # bayrak kapaliyken eski tek butce: otobus rakibe daha NET sans verir
    _, off_base = _chance(OFF, TeamInstructions())
    _, off_bus = _chance(OFF, TeamInstructions(Mentality.PARK_THE_BUS))
    assert off_bus > off_base


def test_block_penalty_depends_on_attacking_focus():
    eng = _engine(V2)
    eng.away.instructions = TeamInstructions(Mentality.PARK_THE_BUS)
    values = {}
    for focus in AttackingFocus:
        eng.home.instructions = TeamInstructions(attacking_focus=focus)
        values[focus] = eng._concede_factor(eng.home, eng.away)
    assert values[AttackingFocus.CENTRE] < values[AttackingFocus.MIXED] < values[AttackingFocus.FLANKS] < 1.0
    assert values[AttackingFocus.MIXED] == MENTALITY_EFFECTS[Mentality.PARK_THE_BUS].concede_quality


def test_press_interacts_with_tempo():
    eng = _engine(V2)
    eng.away.instructions = TeamInstructions(pressing=Pressing.ALL_OVER)
    mids = {}
    for tempo in Tempo:
        eng.home.instructions = TeamInstructions(passing_style=PassingStyle.SHORT, tempo=tempo)
        mids[tempo] = eng._matchup_factor(eng.home, "midfield")
    assert mids[Tempo.FAST] < mids[Tempo.NORMAL] < mids[Tempo.SLOW]
    assert MATCHUP_EFFECTS.press_tempo_exposure[1] == 1.0
    eng.home.instructions = TeamInstructions(passing_style=PassingStyle.SHORT, tempo=Tempo.SLOW)
    assert eng._concede_factor(eng.home, eng.away) > TeamInstructions(pressing=Pressing.ALL_OVER).concede_quality


def test_counter_attack_gains_against_a_superior_opponent_only():
    counter = TeamInstructions(counter_attack=True)
    factors = []
    for away_ovr in (80, 90):
        eng = _engine(V2, make_team(1, "Ev", 80), make_team(2, "Dep", away_ovr))
        eng.home.instructions = counter
        factors.append(eng._counter_attack_factor(eng.home))
    assert factors[0] == 1.0 < factors[1]


# ===========================================================================
# 4) AI dizilis degisikligi
# ===========================================================================

@pytest.mark.parametrize("ratio,home,diff,minute,current,expected", [
    (1.0, True, -1, 76, (4, 4, 2), (4, 3, 3)),
    (1.0, False, -1, 76, (3, 5, 2), (4, 3, 3)),
    (1.3, False, -1, 88, (5, 3, 2), (4, 3, 3)),
    (1.0, True, -1, 74, (4, 4, 2), None),          # erken
    (1.0, True, -2, 80, (4, 4, 2), None),          # 2 geride: talimatla bastirir
    (1.0, True, -1, 80, (4, 3, 3), None),          # zaten 3 forvet
    (1.0, True, -1, 80, (3, 4, 3), None),
    (0.75, False, -1, 80, (4, 4, 2), None),        # otobus + kontra takimi dizilisini bozmaz
    (1.0, True, 1, 81, (4, 4, 2), (5, 3, 2)),
    (1.0, False, 1, 81, (4, 3, 3), (5, 3, 2)),
    (0.75, False, 1, 81, (4, 4, 2), (5, 3, 2)),
    (1.0, True, 1, 79, (4, 4, 2), None),
    (1.0, True, 2, 85, (4, 4, 2), None),
    (1.0, True, 1, 85, (5, 3, 2), None),
    (1.3, True, 1, 85, (4, 4, 2), None),           # cok guclu taraf besli savunmaya cekilmez
    (1.0, True, 0, 88, (4, 4, 2), None),
])
def test_ai_formation_decision_table(ratio, home, diff, minute, current, expected):
    assert ai_formation(ratio, home, diff, minute, current) == expected
    assert ai_formation(ratio, home, diff, minute, None) is None


def test_ai_changes_formation_but_never_for_managed_teams():
    cfg = EngineConfig(tactics_v2=True, ai_tactics=True)
    seen, managed_changes = 0, 0
    for seed in range(40):
        home, away = make_team(1, "Ev", 78), make_team(2, "Dep", 80)
        away.manager_controlled = True
        r = MatchEngine(home, away, seed=seed, config=cfg).simulate()
        ai = [e for e in r.events if e.type is EventType.TACTICAL_CHANGE and e.detail == "ai" and "diziliş" in e.description]
        assert len(ai) <= AI_FORMATION_MAX_CHANGES
        managed_changes += sum(1 for e in ai if e.team_id == 2)
        seen += bool(ai)
        for e in ai:
            assert "→" in e.description and "%" not in e.description     # K12: gorunen gercek, sayi yok
    assert managed_changes == 0 and seen > 0


def test_ai_formation_needs_the_flag():
    cfg = EngineConfig(tactics_v2=False, ai_tactics=True)
    for seed in range(20):
        r = MatchEngine(make_team(1, "Ev", 85), make_team(2, "Dep", 70), seed=seed, config=cfg).simulate()
        assert not any(e.type is EventType.TACTICAL_CHANGE and "diziliş" in e.description for e in r.events)


def test_v2_match_replays_identically():
    cfg = EngineConfig(tactics_v2=True, ai_tactics=True)
    for seed in range(5):
        runs = []
        for _ in range(2):
            home, away = make_team(1, "Ev", 80), make_team(2, "Dep", 76)
            home.instructions, home.manager_controlled = PARK_COUNTER, True
            runs.append(_outcome(MatchEngine(home, away, seed=seed, config=cfg).simulate()))
        assert runs[0] == runs[1]


# ===========================================================================
# 5) Duman olcumleri (esli tohum, n = 800; tam olcum kanit dosyasinda)
# ===========================================================================

SMOKE_N = 800


OWN_COUNTER_DIRECT = TeamInstructions(passing_style=PassingStyle.DIRECT, pressing=Pressing.OWN_HALF, counter_attack=True)


def test_smoke_underdog_defensive_plan_beats_default():
    """Kabul 1'in yarisi: 70 v 85, kendi yari + kontra + direkt varsayilani >= +0.025 puan/mac yener (esli tohum)."""
    base = duel(SMOKE_N, 70, 85, subj_inst=TeamInstructions(), opp_inst=TeamInstructions(), cfg=V2)
    plan = duel(SMOKE_N, 70, 85, subj_inst=OWN_COUNTER_DIRECT, opp_inst=TeamInstructions(), cfg=V2)
    diff, se = plan.minus(base)
    assert diff >= 0.025, (diff, se)


def test_smoke_cyclic_pair_bus_counter_beats_possession_press():
    """Kabul 3'un yarisi, cift (d): otobus + kontra, kisa pas + tum saha presi kafa kafaya >= +0.015 yener."""
    r = duel(SMOKE_N, 80, 80, subj_inst=TeamInstructions(Mentality.PARK_THE_BUS, counter_attack=True),
             opp_inst=TeamInstructions(passing_style=PassingStyle.SHORT, pressing=Pressing.ALL_OVER), cfg=V2)
    diff, se = r.head_to_head()
    assert diff >= 0.015, (diff, se)


# ===========================================================================
# 6) Determinizm: bayrak kapali = 14E oncesi; bayrak varsayilan acik (K-S13)
# ===========================================================================

def test_tactics_v2_is_on_by_default():
    """14E §5 (YENIDEN TEMELLENDIRME 4): varsayilan acik; False yapmak motoru 14B'ye bit-bit geri dondurur."""
    assert EngineConfig().tactics_v2 is True
    assert EngineConfig().max_tactical_swaps_v2 == 3


def test_flag_off_seeds_match_pre_14e_golden():
    from tests.test_engine_golden_seeds import GOLDEN_PRE_14E, fingerprint, simulate

    for seed, home_goals, away_goals, n_events, digest in GOLDEN_PRE_14E:
        r = simulate(seed, replace(EngineConfig(), tactics_v2=False))
        assert (r.home_score, r.away_score, len(r.events), fingerprint(r)) == (
            home_goals, away_goals, n_events, digest)
