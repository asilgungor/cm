"""
Duran top ve liderlik rolleri testleri: team_roles.py (SetPieceRoles, beceri yardimcilari,
suggest_roles), penalties.py (belirlenmis ilk atici) ve motordaki karsiliklari (mac ici penalti,
direkt frikik, korner, kaptan etkisi, seri penalti sirasi).

Saf testler: veritabani gerektirmez (sentetik kadrolar).

  CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_team_roles.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import team_roles  # noqa: E402
from match_engine import (  # noqa: E402
    EngineConfig,
    EventType,
    KnockoutRule,
    MatchEngine,
    MatchPlayer,
)
from match_feed import build_timeline  # noqa: E402
from models import Position  # noqa: E402
from penalties import (  # noqa: E402
    PenaltyTaker,
    ShootoutSide,
    equalize_takers,
    kick_order,
    run_shootout,
)
from team_roles import ROLE_FIELDS, SetPieceRoles, suggest_roles  # noqa: E402
from tests.test_live_match import fingerprint, make_player, make_team  # noqa: E402

SET_PIECE_DETAILS = ("penalty", "free_kick", "corner")
QUIET = dict(base_card=0.0, base_injury=0.0)


def engine(seed: int = 1, cfg: EngineConfig | None = None, **kw) -> MatchEngine:
    return MatchEngine(make_team(1, "Ev", 80), make_team(2, "Dep", 80), seed=seed, config=cfg, **kw)


def set_piece_events(events, team_id: int | None = None, detail: str | None = None):
    return [e for e in events if e.detail in SET_PIECE_DETAILS and (team_id is None or e.team_id == team_id)
            and (detail is None or e.detail == detail)]


# ===========================================================================
# 1) SetPieceRoles
# ===========================================================================

def test_roles_defaults_validation_and_helpers():
    roles = SetPieceRoles()
    assert roles.is_default and not roles.has_set_piece_takers
    assert ROLE_FIELDS == ("captain_id", "penalty_taker_id", "free_kick_taker_id", "corner_taker_id")
    assert roles.describe() == "Roller belirlenmedi"
    captain_only = SetPieceRoles(captain_id=5)
    assert not captain_only.is_default and not captain_only.has_set_piece_takers
    full = SetPieceRoles(captain_id=5, penalty_taker_id=9, free_kick_taker_id=9, corner_taker_id=7)
    assert full.has_set_piece_takers
    assert full.describe({5: "Kaptan Ali", 9: "Veli"}) == (
        "Kaptan: Kaptan Ali · Penaltı atıcısı: Veli · Serbest vuruş atıcısı: Veli · Korner atıcısı: #7")
    assert full.without_player(9) == SetPieceRoles(captain_id=5, corner_taker_id=7)
    for bad in ("9", 9.0, True):
        with pytest.raises(ValueError, match="oyuncu numarası"):
            SetPieceRoles(penalty_taker_id=bad)


def test_roles_to_dict_from_dict_roundtrip_and_tolerance():
    full = SetPieceRoles(captain_id=5, penalty_taker_id=9, free_kick_taker_id=None, corner_taker_id=7)
    data = full.to_dict()
    assert data == {"captain_id": 5, "penalty_taker_id": 9, "free_kick_taker_id": None, "corner_taker_id": 7}
    assert SetPieceRoles.from_dict(json.loads(json.dumps(data))) == full
    assert SetPieceRoles.from_dict(None) == SetPieceRoles.from_dict([]) == SetPieceRoles.from_dict({}) == SetPieceRoles()
    messy = {"captain_id": "12", "penalty_taker_id": True, "free_kick_taker_id": "abc", "corner_taker_id": 3.5,
             "vice_captain_id": 4}
    assert SetPieceRoles.from_dict(messy) == SetPieceRoles(captain_id=12)


# ===========================================================================
# 2) Beceri yardimcilari ve suggest_roles
# ===========================================================================

def test_attribute_helper_scales_fm_values_and_falls_back():
    p = make_player(1, Position.FWD, 80)
    assert team_roles.attribute(p, "crossing", 42.0) == 42.0
    p.attributes = {"crossing": 16, "heading": None, "flair": "x", "corners": 25, "leadership": True}
    assert team_roles.attribute(p, "crossing", 42.0) == 80.0
    assert team_roles.attribute(p, "heading", 33.0) == 33.0
    assert team_roles.attribute(p, "flair", 10.0) == 10.0
    assert team_roles.attribute(p, "corners", 0.0) == 100.0                  # 0-100 araligina sikistirilir
    assert team_roles.attribute(p, "leadership", 7.0) == 7.0
    orm = SimpleNamespace(id=3, overall_rating=71, fm_attributes={"penalty_taking": 18}, shooting=50, morale=60)
    assert team_roles.overall_of(orm) == 71.0
    assert team_roles.attribute(orm, "penalty_taking", 0.0) == 90.0
    assert team_roles.penalty_skill(orm) == pytest.approx(0.45 * 90 + 0.35 * 50 + 0.20 * 60)
    fwd = make_player(2, Position.FWD, 80)
    assert team_roles.penalty_skill(fwd) == pytest.approx(0.45 * 84 + 0.35 * 84 + 0.20 * 70)
    assert team_roles.crossing_skill(fwd) == pytest.approx(0.6 * 75 + 0.4 * 80)
    assert team_roles.aerial_skill(fwd) == pytest.approx(0.5 * 84 + 0.5 * 65)
    assert team_roles.top_average([1, 9, 5, 7], 2) == 8.0 and team_roles.top_average([], 3) == 0.0


def _squad() -> list[MatchPlayer]:
    players = [make_player(i, pos, 78, age=age) for i, (pos, age) in enumerate(
        [(Position.GK, 36), (Position.DEF, 31), (Position.DEF, 24), (Position.MID, 27), (Position.MID, 22),
         (Position.FWD, 29), (Position.FWD, 20)], start=1)]
    return players


def test_suggest_roles_uses_base_attributes_without_fm_data():
    squad = _squad()
    roles = suggest_roles(squad)
    assert roles.captain_id == 1                                   # en tecrubeli (kaleci olabilir)
    assert roles.penalty_taker_id == 6                             # en iyi sut (FWD), esitlikte kucuk id
    assert roles.free_kick_taker_id == 6
    assert roles.corner_taker_id == 4                              # pas + top surme: orta saha, kucuk id
    assert suggest_roles(reversed(squad)) == roles                 # siradan bagimsiz
    assert suggest_roles([]) == SetPieceRoles()
    only_keepers = [make_player(1, Position.GK, 70), make_player(2, Position.GK, 75)]
    assert suggest_roles(only_keepers).penalty_taker_id in (1, 2)


def test_suggest_roles_prefers_fm_specialists_and_works_with_orm_objects():
    squad = _squad()
    squad[4].attributes = {"penalty_taking": 20, "composure": 19, "finishing": 17}           # genc MID
    squad[2].attributes = {"free_kicks": 20, "long_shots": 18, "technique": 17, "corners": 19, "crossing": 18}
    for p in squad:
        p.attributes = {**p.attributes, "leadership": 5}
    squad[6].attributes = {"leadership": 20}                                                  # 20 yasinda lider
    roles = suggest_roles(squad)
    assert (roles.penalty_taker_id, roles.free_kick_taker_id, roles.corner_taker_id) == (5, 3, 3)
    assert roles.captain_id == 7                                   # liderlik tecrubeden agir basar
    squad[6].attributes = {"leadership": 9}
    assert suggest_roles(squad).captain_id == 1                    # liderlik farki kucukse en tecrubeli

    orm = [SimpleNamespace(id=10 + i, position=SimpleNamespace(value=pos), age=age, overall_rating=ovr,
                           shooting=sh, passing=pa, dribbling=60, defending=50, pace=60, morale=70, fm_attributes=fm)
           for i, (pos, age, ovr, sh, pa, fm) in enumerate([
               ("GK", 33, 75, 20, 40, {}),
               ("DEF", 34, 72, 40, 55, {"leadership": 19}),
               ("FWD", 25, 80, 85, 60, {"penalty_taking": 8}),
               ("MID", 26, 77, 70, 82, {"penalty_taking": 19, "corners": 17})])]
    roles = suggest_roles(orm)
    assert roles.captain_id == 11
    assert roles.penalty_taker_id == 13 and roles.corner_taker_id == 13
    assert roles.free_kick_taker_id in (12, 13)


def test_match_player_from_orm_copies_fm_attributes():
    orm = SimpleNamespace(id=7, name="A", position=Position.MID, age=25, overall_rating=70, pace=70, shooting=60,
                          passing=75, defending=50, dribbling=70, goalkeeping=20, form=50, morale=70, condition=90,
                          fm_attributes={"stamina": 14, "crossing": 17})
    mp = MatchPlayer.from_orm(orm)
    assert mp.attributes == {"stamina": 14, "crossing": 17} and mp.stamina == 14.0
    orm.fm_attributes["crossing"] = 1
    assert mp.attributes["crossing"] == 17                          # kopya
    orm.fm_attributes = None
    assert MatchPlayer.from_orm(orm).attributes == {}


# ===========================================================================
# 3) Seri penalti: belirlenmis ilk atici
# ===========================================================================

def _takers(skills: dict[int, float]) -> list[PenaltyTaker]:
    return [PenaltyTaker(pid, f"P{pid}", s) for pid, s in skills.items()]


def test_kick_order_and_equalize_with_designated_taker():
    takers = _takers({1: 90, 2: 50, 3: 70, 9: 30})
    assert [t.id for t in kick_order(takers, 9)] == [1, 3, 2, 9]
    assert [t.id for t in kick_order(takers, 9, first_id=2)] == [2, 1, 3, 9]
    assert [t.id for t in kick_order(takers, 9, first_id=9)] == [9, 1, 3, 2]     # kaleci de ilk atabilir
    assert [t.id for t in kick_order(takers, 9, first_id=77)] == [1, 3, 2, 9]    # listede yok: eski sira
    home, away = _takers({1: 90, 2: 40, 3: 70, 9: 30}), _takers({11: 60, 19: 30})
    h, _ = equalize_takers(home, away, 9, 19)
    assert [t.id for t in h] == [1, 9]
    h, _ = equalize_takers(home, away, 9, 19, home_protected_id=2)
    assert [t.id for t in h] == [2, 9]                                            # en zayif ama korunuyor
    h, _ = equalize_takers(_takers({2: 40, 9: 30}), _takers({19: 30}), 9, 19, home_protected_id=2)
    assert [t.id for t in h] == [9]                                               # baska aday yok: mecburen


def test_run_shootout_designated_taker_kicks_first_and_default_is_unchanged():
    def side(first=None):
        return ShootoutSide(1, "Ev", _takers({1: 90, 2: 40, 3: 70, 9: 30}), 9, "GK", 78, first_taker_id=first)

    away = ShootoutSide(2, "Dep", _takers({11: 80, 12: 60, 13: 65, 19: 30}), 19, "GK2", 78)
    plain = run_shootout(random.Random(4), side(), away, first="home")
    same = run_shootout(random.Random(4), side(None), away, first="home")
    assert plain == same and plain.side_kicks("home")[0].player_id == 1
    designated = run_shootout(random.Random(4), side(2), away, first="home")
    assert designated.side_kicks("home")[0].player_id == 2
    assert [k.player_id for k in designated.side_kicks("away")][:2] == [11, 13]


NO_SET_PIECES = EngineConfig(set_pieces=False)   # duran top modeli kapali (13A oncesi akis)


@cache
def shootout_seeds() -> tuple[int, ...]:
    return tuple(s for s in range(60)
                 if engine(s, cfg=NO_SET_PIECES, knockout=KnockoutRule()).simulate().shootout is not None)[:3]


def test_engine_shootout_starts_with_designated_taker_if_on_pitch():
    seeds = shootout_seeds()
    assert len(seeds) == 3
    for seed in seeds:
        plain = engine(seed, cfg=NO_SET_PIECES, knockout=KnockoutRule()).simulate()
        roles = SetPieceRoles(penalty_taker_id=105)                  # stoper; duran top modeli kapali
        r = engine(seed, cfg=EngineConfig(set_pieces=False), knockout=KnockoutRule(), home_roles=roles).simulate()
        assert [e.description for e in r.events if e.type not in (EventType.PENALTY_SHOOTOUT, EventType.FULL_TIME,
                                                                    EventType.SHOOTOUT_START)] == \
            [e.description for e in plain.events if e.type not in (EventType.PENALTY_SHOOTOUT, EventType.FULL_TIME,
                                                                    EventType.SHOOTOUT_START)]
        designated = next(p for p in r.home.players if p.id == 105)
        assert designated.left_minute == r.end_minute and not (designated.sent_off or designated.substituted)
        assert plain.shootout.side_kicks("home")[0].player_id != 105
        assert r.shootout.side_kicks("home")[0].player_id == 105

    # atilan belirlenmis atici: eski siraya donulur
    eng = engine(seeds[0], cfg=EngineConfig(set_pieces=False, **QUIET), knockout=KnockoutRule(),
                 home_roles=SetPieceRoles(penalty_taker_id=105))
    side = eng._shootout_side(eng.home)
    assert side.first_taker_id == 105
    eng.minute = 30
    eng._send_off(eng.home, next(p for p in eng.home.players if p.id == 105), second_yellow=False)
    assert eng._shootout_side(eng.home).first_taker_id is None


# ===========================================================================
# 4) Mac ici duran toplar
# ===========================================================================

AUTO = EngineConfig(set_pieces=None)             # rollere gore ac/kapa (13A oncesi varsayilan)


def test_set_piece_model_activation_rules():
    assert engine(1)._set_pieces_on()                                              # 13A: kosulsuz acik
    assert not engine(1, cfg=AUTO)._set_pieces_on()
    assert not engine(1, cfg=AUTO, home_roles=SetPieceRoles(captain_id=101))._set_pieces_on()
    assert engine(1, cfg=AUTO, away_roles=SetPieceRoles(corner_taker_id=207))._set_pieces_on()
    assert not engine(1, cfg=NO_SET_PIECES, home_roles=SetPieceRoles(penalty_taker_id=112))._set_pieces_on()
    assert engine(1, cfg=EngineConfig(set_pieces=True))._set_pieces_on()
    r = engine(2, cfg=NO_SET_PIECES, home_roles=SetPieceRoles(penalty_taker_id=112)).simulate()
    assert set_piece_events(r.events) == []
    assert fingerprint(r) == fingerprint(engine(2, cfg=NO_SET_PIECES).simulate())


def test_set_pieces_draw_no_extra_random_numbers_before_the_first_set_piece():
    for seed in range(6):
        plain = engine(seed, cfg=NO_SET_PIECES).simulate()
        on = engine(seed, cfg=EngineConfig(set_pieces=True)).simulate()
        first = next((i for i, e in enumerate(on.events) if e.detail in SET_PIECE_DETAILS), None)
        assert first is not None
        assert [e.description for e in on.events[:first]] == [e.description for e in plain.events[:first]]
        assert plain.events[first].detail is None


def test_both_teams_get_set_pieces_when_one_team_designates_takers():
    teams = Counter()
    for seed in range(10):
        r = engine(seed, home_roles=SetPieceRoles(corner_taker_id=107)).simulate()
        teams.update(e.team_id for e in set_piece_events(r.events))
    assert teams[1] >= 8 and teams[2] >= 8, teams


PENALTY_CFG = EngineConfig(set_pieces=True, set_piece_penalty_share=0.35, set_piece_free_kick_share=0.0,
                           set_piece_corner_share=0.0, **QUIET)


def test_designated_penalty_taker_takes_every_penalty_while_on_pitch():
    kicks = Counter()
    for seed in range(12):
        home = make_team(1, "Ev", 80)
        home.auto_subs = False
        r = MatchEngine(home, make_team(2, "Dep", 80), seed=seed, config=PENALTY_CFG,
                        home_roles=SetPieceRoles(penalty_taker_id=105)).simulate()
        for e in set_piece_events(r.events, 1, "penalty"):
            kicks[e.player_id] += 1
            assert e.description.startswith("PENALTI! Ev penaltı kazandı, topun başında P105-DEF.")
        away_takers = {e.player_id for e in set_piece_events(r.events, 2, "penalty")}
        assert away_takers <= {212, 213, 214, 215}                    # belirlenmemis: en iyi (taze) sutcu
        frames = build_timeline(r)
        assert (frames[-1].home.shots, frames[-1].home.on_target, frames[-1].home.goals) == \
            (r.home.stats.shots, r.home.stats.shots_on_target, r.home.stats.goals)
    assert set(kicks) == {105} and kicks[105] >= 20, kicks


def test_penalty_taker_falls_back_when_designated_player_leaves():
    eng = engine(3, cfg=PENALTY_CFG, home_roles=SetPieceRoles(penalty_taker_id=105))
    eng.home.auto_subs = False
    assert eng.penalty_taker(eng.home).id == 105
    for _ in range(20):
        eng.step()
    bench_def = next(p for p in eng.home.bench if p.position is not Position.GK)
    eng.manual_substitution(eng.home, 105, bench_def.id)
    fallback = eng.penalty_taker(eng.home)
    assert fallback.id in (112, 113)        # enerjiye gore en iyi forvet (13A: yorgunluk dengesi)
    best = max(eng.home.outfield_on_pitch, key=lambda p: (eng._penalty_taker_skill(p), -p.id))
    assert fallback is best
    cut = len(eng.events)
    eng.run_to_end()
    later = set_piece_events(eng.events[cut:], 1, "penalty")
    assert later and {e.player_id for e in later} <= {112, 113}      # enerjiye gore en iyi forvet
    assert eng.penalty_taker(make_team(9, "Bos", 80)) is None      # sahada kimse yok


def test_penalty_outcomes_are_consistent_and_labelled():
    tally = Counter()
    for seed in range(30):
        r = engine(seed, cfg=PENALTY_CFG).simulate()
        for e in set_piece_events(r.events, detail="penalty"):
            tally[e.type] += 1
        for team in (r.home, r.away):
            assert team.stats.shots == sum(p.shots for p in team.players)
            assert team.stats.shots_on_target == sum(p.shots_on_target for p in team.players)
            assert team.stats.goals == sum(p.goals for p in team.players)
        labels = {f.event.label for f in build_timeline(r) if f.event.detail == "penalty"}
        assert labels <= {"PENALTI GOLÜ", "PENALTI KURTARIŞ", "PENALTI KAÇTI"}
    total = sum(tally.values())
    assert total > 200
    assert 0.65 < tally[EventType.GOAL] / total < 0.88, tally                    # ~%76 (seri penalti modeli)
    assert tally[EventType.SAVE] > tally[EventType.MISS] > 0, tally


def _captured(eng: MatchEngine, kind: str, monkeypatch) -> tuple[MatchPlayer, float, MatchPlayer | None]:
    seen = []
    monkeypatch.setattr(eng, "_set_piece_shot",
                        lambda att, dfd, shooter, strength, k, assister: seen.append((shooter, strength, assister)))
    eng._set_piece(kind, eng.home, eng.away)
    monkeypatch.undo()
    return seen[-1]


def test_designated_free_kick_taker_drives_set_piece_quality(monkeypatch):
    eng = engine(5, cfg=EngineConfig(set_pieces=True))
    for _ in range(10):
        eng.step()
    specialist = next(p for p in eng.home.on_pitch if p.id == 108)
    specialist.attributes = {"free_kicks": 20, "long_shots": 19, "technique": 18}
    poor = next(p for p in eng.home.on_pitch if p.id == 103)
    poor.attributes = {"free_kicks": 1, "long_shots": 2, "technique": 3}

    eng.home.roles = SetPieceRoles()
    auto_shooter, _, auto_assist = _captured(eng, "free_kick", monkeypatch)
    assert auto_shooter is specialist and auto_assist is None      # belirlenmemis: en iyi frikikci
    eng.home.roles = SetPieceRoles(free_kick_taker_id=108)
    shooter, good, _ = _captured(eng, "free_kick", monkeypatch)
    eng.home.roles = SetPieceRoles(free_kick_taker_id=103)
    shooter2, bad, _ = _captured(eng, "free_kick", monkeypatch)
    assert (shooter, shooter2) == (specialist, poor)
    expected = (0.4 * 80 + 0.6 * team_roles.free_kick_skill(specialist)) * eng.cfg.free_kick_quality
    assert good == pytest.approx(expected * specialist.condition_factor * specialist.fatigue_factor)
    assert good > 1.8 * bad


def test_corner_delivery_quality_depends_on_designated_corner_taker(monkeypatch):
    eng = engine(6, cfg=EngineConfig(set_pieces=True))
    for _ in range(10):
        eng.step()
    header = next(p for p in eng.home.on_pitch if p.id == 104)
    monkeypatch.setattr(eng, "_weighted_choice", lambda players, weight: header)
    crosser = next(p for p in eng.home.on_pitch if p.id == 109)
    crosser.attributes = {"corners": 20, "crossing": 20}
    clumsy = next(p for p in eng.home.on_pitch if p.id == 112)
    clumsy.attributes = {"corners": 2, "crossing": 3}
    eng.home.roles = SetPieceRoles(corner_taker_id=109)
    seen = []
    monkeypatch.setattr(eng, "_set_piece_shot",
                        lambda att, dfd, shooter, strength, k, assister: seen.append((shooter, strength, assister)))
    eng._set_piece("corner", eng.home, eng.away)
    eng.home.roles = SetPieceRoles(corner_taker_id=112)
    eng._set_piece("corner", eng.home, eng.away)
    (s1, good, a1), (s2, bad, a2) = seen
    assert s1 is s2 is header and (a1, a2) == (crosser, clumsy)
    lo, hi = eng.cfg.set_piece_delivery_range
    assert good / bad == pytest.approx(hi / lo)
    base = (0.4 * header.overall + 0.6 * team_roles.aerial_skill(header)) * header.condition_factor * \
        header.fatigue_factor * eng.cfg.corner_quality
    assert good == pytest.approx(base * hi)


def test_set_piece_goals_follow_taker_quality_over_many_matches():
    cfg = EngineConfig(set_pieces=True, set_piece_penalty_share=0.0, set_piece_free_kick_share=0.5,
                       set_piece_corner_share=0.0, **QUIET)

    def goals(taker_id: int, fm: dict) -> tuple[int, int]:
        scored = shots = 0
        for seed in range(40):
            home = make_team(1, "Ev", 80)
            home.auto_subs = False
            next(p for p in home.players if p.id == taker_id).attributes = fm
            r = MatchEngine(home, make_team(2, "Dep", 80), seed=seed, config=cfg,
                            home_roles=SetPieceRoles(free_kick_taker_id=taker_id)).simulate()
            events = set_piece_events(r.events, 1, "free_kick")
            assert {e.player_id for e in events} <= {taker_id}
            scored += sum(1 for e in events if e.type is EventType.GOAL)
            shots += len(events)
        return scored, shots

    good, good_shots = goals(112, {"free_kicks": 20, "long_shots": 20, "technique": 20})
    bad, bad_shots = goals(103, {"free_kicks": 1, "long_shots": 1, "technique": 1})
    assert good_shots > 200 and bad_shots > 200
    assert good / good_shots > 2 * bad / bad_shots, (good, good_shots, bad, bad_shots)


def test_set_piece_model_calibration_and_feed_consistency():
    n = 200
    off = on = 0
    counts = Counter()
    for seed in range(n):
        plain = engine(seed).simulate()
        r = engine(seed, cfg=EngineConfig(set_pieces=True)).simulate()
        off += plain.home_score + plain.away_score
        on += r.home_score + r.away_score
        for e in set_piece_events(r.events):
            counts[e.detail] += 1
            counts[(e.detail, e.type)] += 1
        if seed < 25:
            last = build_timeline(r)[-1]
            for side, team in ((last.home, r.home), (last.away, r.away)):
                assert (side.shots, side.on_target, side.goals) == \
                    (team.stats.shots, team.stats.shots_on_target, team.stats.goals)
            labels = {f.event.label for f in build_timeline(r) if f.event.detail in SET_PIECE_DETAILS}
            assert labels <= {"PENALTI GOLÜ", "PENALTI KURTARIŞ", "PENALTI KAÇTI", "FRİKİK GOLÜ", "FRİKİK",
                              "KORNERDEN GOL", "KORNER"}, labels
            corner_goals = [e for e in r.events if e.detail == "corner" and e.type is EventType.GOAL]
            for e in corner_goals:
                assert "kornerinde" in e.description or "Korner sonrası" in e.description
    assert abs(on / off - 1) < 0.08, (on / n, off / n)
    assert 0.15 <= counts["penalty"] / n <= 0.45, counts
    assert 0.8 <= counts["free_kick"] / n <= 2.2, counts
    assert 2.5 <= counts["corner"] / n <= 5.5, counts
    for kind in ("free_kick", "corner"):
        rate = counts[(kind, EventType.GOAL)] / counts[kind]
        assert 0.03 <= rate <= 0.16, (kind, rate)


# ===========================================================================
# 5) Kaptan
# ===========================================================================

def test_captain_effects_apply_only_while_on_pitch():
    eng = engine(7, cfg=EngineConfig(**QUIET), home_roles=SetPieceRoles(captain_id=105))
    for _ in range(80):
        eng.step()
    team = eng.home
    assert team.captain_on_pitch
    assert eng._card_factor(team) == pytest.approx(eng.cfg.captain_card_factor)
    assert eng._card_factor(eng.away) == 1.0
    assert eng._captain_composure(team) == eng.cfg.captain_penalty_composure and eng._captain_composure(eng.away) == 0
    side = eng._shootout_side(team)
    assert {t.id: t.skill for t in side.takers}[105] == pytest.approx(
        eng._penalty_taker_skill(next(p for p in team.players if p.id == 105)) + eng.cfg.captain_penalty_composure)

    team.stats.goals, eng.away.stats.goals = 0, 1                      # geride, son bolum
    eng.minute = 80
    relieved = eng._situation_factor(team, "defense")
    assert relieved == pytest.approx(1 - (1 - eng.cfg.trailing_defense_drop) * (1 - eng.cfg.captain_trailing_relief))
    assert eng._situation_factor(eng.away, "defense") == eng.cfg.leading_defense_boost
    assert eng._situation_factor(team, "attack") == eng.cfg.trailing_attack_boost

    bench = next(p for p in team.bench if p.position is not Position.GK)
    eng.manual_substitution(team, 105, bench.id)                       # kaptan oyundan cikti
    assert not team.captain_on_pitch
    assert eng._card_factor(team) == 1.0 and eng._captain_composure(team) == 0.0
    assert eng._situation_factor(team, "defense") == eng.cfg.trailing_defense_drop


def test_sent_off_captain_loses_the_effect_and_captain_only_roles_keep_set_pieces_off():
    eng = engine(8, cfg=AUTO, home_roles=SetPieceRoles(captain_id=106))
    for _ in range(30):
        eng.step()
    assert eng.home.captain_on_pitch and not eng._set_pieces_on()
    eng._send_off(eng.home, next(p for p in eng.home.players if p.id == 106), second_yellow=False)
    assert not eng.home.captain_on_pitch and eng._card_factor(eng.home) == 1.0
    eng.run_to_end()
    assert set_piece_events(eng.events) == []


def test_captain_lowers_team_cards_over_many_matches():
    with_captain = without = 0
    for seed in range(250):
        home = make_team(1, "Ev", 80)
        r = MatchEngine(home, make_team(2, "Dep", 80), seed=seed, home_roles=SetPieceRoles(captain_id=101)).simulate()
        with_captain += r.home.stats.yellow_cards + r.home.stats.red_cards
        plain = engine(seed).simulate()
        without += plain.home.stats.yellow_cards + plain.home.stats.red_cards
    assert with_captain < without, (with_captain, without)


def test_roles_can_be_changed_live_without_random_draws():
    eng = engine(9)
    for _ in range(40):
        eng.step()
    state = eng.rng.getstate()
    eng.set_roles(eng.home, SetPieceRoles(captain_id=101, penalty_taker_id=113))
    assert eng.home.roles.penalty_taker_id == 113 and eng.rng.getstate() == state
    with pytest.raises(ValueError):
        eng.set_roles(eng.home, {"captain_id": 1})
    eng.run_to_end()
    with pytest.raises(ValueError, match="Maç bitti"):
        eng.set_roles(eng.home, SetPieceRoles())
