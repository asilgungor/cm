"""
Dunya Kupasi kural testleri (Faz 12 / 14. Asama, 12C): world_cup.py turnuva boyu, plan, itibar torbalari,
deterministik grup kurasi, eleme gruplari ve kademeli katilim, fikstur, eleme agaci.
Saf testler: veritabani ve Streamlit gerektirmez.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cup_draw  # noqa: E402
import world_cup as wc  # noqa: E402
from cup_draw import GroupRow  # noqa: E402
from world_cup import NationSeed  # noqa: E402

# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def nations(count: int, start: int = 1) -> list[NationSeed]:
    """id 1 en yuksek itibarli: itibar sirasi = id sirasi."""
    return [NationSeed(nation_id=start + i, name=f"Ulus {start + i:03d}", reputation=100 - i) for i in range(count)]


def simulate_results(groups, fixtures_fn, seed: int) -> list[tuple[int, int, int, int]]:
    rng = random.Random(seed)
    results = []
    for day in fixtures_fn(groups):
        for _group, home, away in day:
            results.append((home, away, rng.randint(0, 3), rng.randint(0, 3)))
    return results


def row(team_id: int, played: int, points: int, gf: int = 0, ga: int = 0) -> GroupRow:
    won, drawn = divmod(points, 3)
    return GroupRow(team_id=team_id, played=played, won=won, drawn=drawn, lost=played - won - drawn,
                    goals_for=gf, goals_against=ga, points=points)


# ===========================================================================
# Boy ve plan
# ===========================================================================

@pytest.mark.parametrize(
    ("eligible", "size"),
    [(0, 0), (3, 0), (4, 4), (6, 4), (7, 4), (8, 8), (15, 8), (16, 16), (20, 16), (31, 16),
     (32, 32), (40, 32), (100, 32)],
)
def test_world_cup_size_mapping(eligible, size):
    assert wc.world_cup_size(eligible) == size


@pytest.mark.parametrize(
    ("size", "groups", "stages", "matchdays"),
    [
        (32, 8, ("R16", "QF", "SF", "FINAL"), 7),
        (16, 4, ("QF", "SF", "FINAL"), 6),
        (8, 2, ("SF", "FINAL"), 5),
        (4, 1, ("FINAL",), 4),
    ],
)
def test_plan_shapes(size, groups, stages, matchdays):
    plan = wc.plan(size)
    assert (plan.size, plan.groups, plan.group_size, plan.knockout_stages, plan.matchdays) == (
        size, groups, 4, stages, matchdays,
    )
    assert plan.groups * plan.group_size == size
    # ilk ikiler eleme agacini tam doldurur: 2 * gruplar = 2 ^ eleme turu sayisi
    assert plan.groups * wc.GROUP_ADVANCE == 2 ** len(plan.knockout_stages)
    days = plan.stage_days()
    assert len(days) == plan.matchdays
    assert days[:3] == (("GROUP", 1), ("GROUP", 2), ("GROUP", 3))
    assert tuple(stage for stage, _ in days[3:]) == stages
    assert all(stage in wc.STAGE_ORDER for stage in stages)


@pytest.mark.parametrize("size", [0, 2, 6, 12, 64])
def test_plan_rejects_unsupported_sizes(size):
    with pytest.raises(ValueError):
        wc.plan(size)


def test_stage_labels_and_letters():
    assert wc.stage_label("R16") == "Son 16"
    assert wc.stage_label(cup_draw.Stage.FINAL) == "Final"
    assert wc.stage_label("qual") == "Elemeler"
    assert wc.stage_label("CHAMPION") == "Şampiyonluk"
    assert wc.stage_label("XYZ") == "XYZ"
    assert [wc.group_letter(i) for i in range(3)] == ["A", "B", "C"]
    assert wc.group_letter(26) == "27"
    assert all(len(code) <= 8 for code in wc.STAGE_ORDER), "VARCHAR(8) sutunlarina sigmali"


# ===========================================================================
# Torbalar ve kura
# ===========================================================================

def test_seed_pots_by_reputation_with_name_and_id_tiebreak():
    pool = nations(32)
    random.Random(5).shuffle(pool)
    pots = wc.seed_pots(pool, 8)
    assert len(pots) == 4 and all(len(pot) == 8 for pot in pots)
    assert [n.nation_id for n in pots[0]] == list(range(1, 9))
    assert [n.nation_id for pot in pots for n in pot] == list(range(1, 33))

    tied = [NationSeed(3, "beta", 70), NationSeed(1, "Alfa", 70), NationSeed(2, "alfa", 70), NationSeed(9, "Zeta", 90)]
    assert [n.nation_id for n in wc.rank_by_reputation(tied)] == [9, 1, 2, 3]


def test_seed_pots_last_pot_may_be_partial_and_bad_input_raises():
    pots = wc.seed_pots(nations(10), 3)
    assert [len(pot) for pot in pots] == [3, 3, 3, 1]
    with pytest.raises(ValueError):
        wc.seed_pots(nations(2), 3)
    with pytest.raises(ValueError):
        wc.seed_pots(nations(4), 0)
    with pytest.raises(ValueError):
        wc.seed_pots(nations(4) + nations(1), 2)  # tekrar eden id


def test_group_draw_one_nation_per_pot_per_group():
    pots = wc.seed_pots(nations(32), 8)
    groups = wc.group_draw(123, pots)
    assert len(groups) == 8
    for group in groups:
        assert len(group) == 4
        for pot_index, nation_id in enumerate(group):
            assert nation_id in {n.nation_id for n in pots[pot_index]}
    assert sorted(nid for group in groups for nid in group) == list(range(1, 33))


def test_group_draw_is_deterministic_for_a_seed_and_differs_for_another():
    pots = wc.seed_pots(nations(32), 8)
    first = wc.group_draw(2026, pots)
    assert wc.group_draw(2026, pots) == first
    assert wc.group_draw(random.Random(2026), pots) == first
    assert wc.group_draw("dunya|2026", pots) == wc.group_draw("dunya|2026", pots)
    assert wc.group_draw(2027, pots) != first
    # NationSeed yerine id'ler de kabul edilir
    id_pots = [[n.nation_id for n in pot] for pot in pots]
    assert wc.group_draw(2026, id_pots) == first


def test_group_draw_is_not_biased_to_group_a():
    pots = wc.seed_pots(nations(32), 8)
    homes = {next(i for i, g in enumerate(wc.group_draw(seed, pots)) if 1 in g) for seed in range(80)}
    assert homes == set(range(8))


def test_group_draw_partial_last_pot_and_invalid_pots():
    pots = wc.seed_pots(nations(11), 3)                      # 3 / 3 / 3 / 2
    groups = wc.group_draw(7, pots)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [3, 4, 4]
    assert sorted(nid for g in groups for nid in g) == list(range(1, 12))
    for group in groups:
        for pot_index, nation_id in enumerate(group):
            assert nation_id in {n.nation_id for n in pots[pot_index]}
    with pytest.raises(ValueError):
        wc.group_draw(1, [[1, 2, 3], [4], [5, 6, 7]])        # eksik torba ortada
    with pytest.raises(ValueError):
        wc.group_draw(1, [[1, 2], [3, 1]])                   # tekrar
    with pytest.raises(ValueError):
        wc.group_draw(1, [])
    with pytest.raises(ValueError):
        wc.group_draw(None, [[1, 2], [3, 4]])                # rng yok


def test_world_cup_draw_uses_plan_groups():
    groups = wc.world_cup_draw(nations(16), 99)
    assert len(groups) == 4 and all(len(g) == 4 for g in groups)
    assert {g[0] for g in groups} == {1, 2, 3, 4}               # seri basilari farkli gruplarda
    with pytest.raises(ValueError):
        wc.world_cup_draw(nations(12), 99)


# ===========================================================================
# Elemeler
# ===========================================================================

def test_no_qualifiers_when_everyone_fits():
    for count in (4, 8, 16, 32):
        assert wc.qualifier_groups(nations(count), wc.world_cup_size(count), 1) == []
    assert wc.qualifier_groups(nations(3), 4, 1) == []
    assert wc.qualifier_group_count(32, 32) == 0
    with pytest.raises(ValueError):
        wc.qualifier_groups(nations(6), 0, 1)


def test_synthetic_world_qualifiers_two_groups_of_three():
    pool = nations(6)                    # sentetik dunya: 6 lig ulkesi -> 4'luk kupa
    size = wc.world_cup_size(len(pool))
    groups = wc.qualifier_groups(pool, size, 11)
    assert size == 4
    assert sorted(len(g) for g in groups) == [3, 3]
    assert {g[0] for g in groups} == {1, 2}, "iki seri basi ayni grupta olamaz"
    assert wc.qualifier_groups(pool, size, 11) == groups
    days = wc.qualifier_fixtures(groups)
    assert len(days) == 6                # 3'lu grup cift devre: 6 mac gunu (3 milli arada 2'ser)
    standings = wc.group_standings(groups, simulate_results(groups, wc.qualifier_fixtures, 4))
    qualified = wc.qualified_from_groups(standings, size)
    assert len(qualified) == 4
    assert set(qualified) == {t[i].team_id for t in standings for i in range(2)}, "birinciler + ikinciler"


@pytest.mark.parametrize("count", [5, 6, 7, 9, 10, 12, 17, 20, 31, 33, 40, 63, 64, 65, 100])
def test_qualifiers_produce_exactly_size_qualifiers(count):
    pool = nations(count)
    size = wc.world_cup_size(count)
    groups = wc.qualifier_groups(pool, size, count)
    assert groups, "uygun ulus turnuva boyunu asiyor: eleme olmali"
    sizes = [len(g) for g in groups]
    assert min(sizes) >= 2 and max(sizes) - min(sizes) <= 1
    assert len(groups) <= size
    assert sorted(nid for g in groups for nid in g) == list(range(1, count + 1))
    days = wc.qualifier_fixtures(groups)
    assert len(days) <= 6, "elemeler 3 milli arada 2'ser mac gunune sigmali"
    reps = {n.nation_id: n.reputation for n in pool}
    standings = wc.group_standings(groups, simulate_results(groups, wc.qualifier_fixtures, count), reps)
    qualified = wc.qualified_from_groups(standings, size, reps)
    assert len(qualified) == size == len(set(qualified))
    winners = {table[0].team_id for table in standings}
    assert winners <= set(qualified), "tum grup birincileri katilir"


def test_qualified_tier_uses_points_per_match_across_unequal_groups():
    standings = [
        [row(1, 6, 15), row(2, 6, 9, 8, 5), row(3, 6, 6), row(4, 6, 3)],
        [row(5, 6, 13), row(6, 6, 10, 7, 6), row(7, 6, 4), row(8, 6, 1)],
        [row(9, 4, 10), row(10, 4, 7, 5, 4), row(11, 4, 0)],   # 3'lu grup: ikinci 7/4 = 1.75 puan/mac
    ]
    # 3 birinci + en iyi 2 ikinci: 10 (1.75) ve 6 (1.67); 2 (1.50) disarida
    assert wc.qualified_from_groups(standings, 5) == [1, 5, 9, 10, 6]
    assert wc.qualified_from_groups(standings, 3) == [1, 5, 9]
    assert len(wc.qualified_from_groups(standings, 11)) == 11
    with pytest.raises(ValueError):
        wc.qualified_from_groups(standings, 12)
    with pytest.raises(ValueError):
        wc.qualified_from_groups(standings, 0)


def test_qualified_tier_tiebreak_goal_difference_then_reputation():
    standings = [
        [row(1, 4, 12), row(2, 4, 6, 6, 4)],
        [row(3, 4, 12), row(4, 4, 6, 7, 5)],
        [row(5, 4, 12), row(6, 4, 6, 9, 6)],
    ]
    assert wc.qualified_from_groups(standings, 4) == [1, 3, 5, 6]          # averaj +3
    level = [[row(1, 4, 12), row(2, 4, 6, 5, 5)], [row(3, 4, 12), row(4, 4, 6, 5, 5)]]
    assert wc.qualified_from_groups(level, 3, {2: 60, 4: 80}) == [1, 3, 4]  # itibar
    assert wc.qualified_from_groups(level, 3) == [1, 3, 2]                  # id


# ===========================================================================
# Fikstur ve tablolar
# ===========================================================================

def test_world_cup_group_fixtures_single_round_robin():
    groups = [[1, 2, 3, 4], [5, 6, 7, 8]]
    days = wc.world_cup_group_fixtures(groups)
    assert len(days) == 3
    for group_index, group in enumerate(groups):
        meetings = Counter(frozenset((h, a)) for day in days for g, h, a in day if g == group_index)
        assert set(meetings) == {frozenset(p) for p in combinations(group, 2)}
        assert set(meetings.values()) == {1}
    for day in days:
        playing = [nid for _g, h, a in day for nid in (h, a)]
        assert len(playing) == len(set(playing)) == 8


def test_qualifier_fixtures_double_round_up_to_four_single_above():
    groups = [[1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12]]
    days = wc.qualifier_fixtures(groups)
    assert len(days) == 6
    by_group = {g: [(h, a) for day in days for gg, h, a in day if gg == g] for g in range(3)}
    assert Counter(frozenset(m) for m in by_group[0]) == {frozenset(p): 2 for p in combinations(groups[0], 2)}
    assert set(by_group[0]) == {(a, h) for h, a in by_group[0]}, "cift devrede ev/deplasman ters"
    assert Counter(frozenset(m) for m in by_group[1]) == {frozenset(p): 1 for p in combinations(groups[1], 2)}
    assert Counter(frozenset(m) for m in by_group[2]) == {frozenset(p): 2 for p in combinations(groups[2], 2)}
    for day in days:
        playing = [nid for _g, h, a in day for nid in (h, a)]
        assert len(playing) == len(set(playing))


def test_group_standings_wrap_rank_group_with_reputation_tiebreak():
    groups = [[1, 2, 3, 4]]
    results = [(1, 2, 0, 0), (3, 4, 0, 0), (1, 3, 0, 0), (2, 4, 0, 0), (1, 4, 0, 0), (2, 3, 0, 0),
               (99, 1, 5, 0)]                               # grup disi mac yok sayilir
    table = wc.group_standings(groups, results, reputations={1: 50, 2: 90, 3: 70, 4: 60})[0]
    assert [r.team_id for r in table] == [2, 3, 4, 1]
    assert all(r.points == 3 and r.played == 3 for r in table)
    assert wc.group_rankings([table]) == [[2, 3, 4, 1]]


# ===========================================================================
# Eleme agaci
# ===========================================================================

def _meeting_round(pairs: list[tuple[int, int]], a: int, b: int) -> int:
    """Agac sirasinda iki ulusun en erken karsilasabilecegi tur (1 = ilk eleme turu)."""
    slot = {nid: 2 * i + side for i, pair in enumerate(pairs) for side, nid in enumerate(pair)}
    return (slot[a] ^ slot[b]).bit_length()


def test_knockout_pairs_round_of_16_crossing():
    rankings = [[10 * g + 1, 10 * g + 2, 10 * g + 3, 10 * g + 4] for g in range(8)]   # A=1x, B=2x ...
    pairs = wc.knockout_pairs(rankings)
    assert pairs == [
        (1, 12), (21, 32), (41, 52), (61, 72),     # ust yari: A1-B2, C1-D2, E1-F2, G1-H2
        (11, 2), (31, 22), (51, 42), (71, 62),     # alt yari: B1-A2, D1-C2, F1-E2, H1-G2
    ]


@pytest.mark.parametrize("group_count", [2, 4, 8])
def test_group_mates_cannot_meet_before_the_final(group_count):
    rankings = [[100 * g + 1, 100 * g + 2] for g in range(group_count)]
    pairs = wc.knockout_pairs(rankings)
    final_round = (2 * group_count - 1).bit_length()
    assert len(pairs) == group_count
    for first, second in rankings:
        assert _meeting_round(pairs, first, second) == final_round
    winners = [ranking[0] for ranking in rankings]
    for a, b in combinations(winners, 2):
        assert _meeting_round(pairs, a, b) >= 2, "grup birincileri ilk turda karsilasmaz"


def test_knockout_pairs_match_arena_quarter_final_tree():
    rankings = [[1, 2], [3, 4], [5, 6], [7, 8]]
    ours = wc.knockout_pairs(rankings)
    arena = cup_draw.group_qualifier_pairs(rankings)
    assert [frozenset(p) for p in ours] == [frozenset(p) for p in arena]
    assert all(pair[0] in {1, 3, 5, 7} for pair in ours), "grup birincisi once yazilir"


def test_knockout_pairs_single_group_final_and_rows_input():
    assert wc.knockout_pairs([[4, 9, 2, 7]]) == [(4, 9)]
    table = [GroupRow(team_id=5), GroupRow(team_id=6)]
    assert wc.knockout_pairs([table, [GroupRow(team_id=7), GroupRow(team_id=8)]]) == [(5, 8), (7, 6)]


def test_knockout_pairs_invalid_input():
    with pytest.raises(ValueError):
        wc.knockout_pairs([[1, 2], [3, 4], [5, 6]])       # 3 grup
    with pytest.raises(ValueError):
        wc.knockout_pairs([])
    with pytest.raises(ValueError):
        wc.knockout_pairs([[1, 2], [3]])                  # ikinci yok
    with pytest.raises(ValueError):
        wc.knockout_pairs([[1, 2], [2, 3]])               # tekrar


def test_next_knockout_pairs_keeps_tree_order():
    assert wc.next_knockout_pairs([1, 21, 41, 61, 11, 31, 51, 71]) == [(1, 21), (41, 61), (11, 31), (51, 71)]
    assert wc.next_knockout_pairs([3, 4]) == [(3, 4)]
    for bad in ([1], [1, 2, 3], [1, 1]):
        with pytest.raises(ValueError):
            wc.next_knockout_pairs(bad)


# ===========================================================================
# Uctan uca: 40 ulus -> elemeler -> 32'lik kupa -> sampiyon (deterministik)
# ===========================================================================

def _run_tournament(seed: int) -> tuple[list[int], int]:
    pool = nations(40)
    reps = {n.nation_id: n.reputation for n in pool}
    size = wc.world_cup_size(len(pool))
    q_groups = wc.qualifier_groups(pool, size, seed)
    q_tables = wc.group_standings(q_groups, simulate_results(q_groups, wc.qualifier_fixtures, seed), reps)
    qualified = set(wc.qualified_from_groups(q_tables, size, reps))
    entrants = [n for n in pool if n.nation_id in qualified]
    groups = wc.world_cup_draw(entrants, f"wc|{seed}")
    tables = wc.group_standings(groups, simulate_results(groups, wc.world_cup_group_fixtures, seed + 1), reps)
    pairs = wc.knockout_pairs(wc.group_rankings(tables))
    rng = random.Random(seed)
    stages = list(wc.plan(size).knockout_stages)
    for stage in stages:
        winners = [pair[rng.randint(0, 1)] for pair in pairs]
        if stage == wc.FINAL:
            return sorted(qualified), winners[0]
        pairs = wc.next_knockout_pairs(winners)
    raise AssertionError("final oynanmadi")  # pragma: no cover


def test_end_to_end_tournament_is_deterministic():
    qualified, champion = _run_tournament(8)
    assert len(qualified) == 32
    assert champion in qualified
    assert _run_tournament(8) == (qualified, champion)
