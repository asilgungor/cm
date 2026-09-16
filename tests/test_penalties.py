"""
Seri penalti testleri (8. Asama): penalties.py SAF mantik. Veritabani gerektirmez.

Ozellik tabanli: yuzlerce tohumda IFAB kurallari (ABAB sirasi, erken bitis, ani olum,
atisci rotasyonu, esitlemek icin azaltma) ve kalibrasyon (gol orani, kaleci etkisi).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from penalties import (  # noqa: E402
    PenaltyTaker,
    ShootoutConfig,
    ShootoutResult,
    ShootoutSide,
    conversion_probability,
    equalize_takers,
    kick_order,
    regulation_decided,
    run_shootout,
    save_share,
)

SEEDS = range(600)


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def make_side(team_id: int, n: int = 11, *, rng: random.Random | None = None, taker_skill: float = 68.0,
              keeper_skill: float = 78.0, spread: float = 12.0) -> ShootoutSide:
    """n oyunculu taraf; son oyuncu kaleci (atis yetenegi dusuk)."""
    rng = rng or random.Random(team_id)
    takers = [
        PenaltyTaker(id=team_id * 100 + i, name=f"T{team_id}-{i}",
                     skill=taker_skill + rng.uniform(-spread, spread))
        for i in range(n - 1)
    ]
    keeper = PenaltyTaker(id=team_id * 100 + 99, name=f"GK{team_id}", skill=40.0)
    takers.append(keeper)
    rng.shuffle(takers)
    return ShootoutSide(team_id=team_id, team_name=f"Takım {team_id}", takers=takers,
                        keeper_id=keeper.id, keeper_name=keeper.name, keeper_skill=keeper_skill)


def shootout(seed: int, home_n: int = 11, away_n: int = 11, config: ShootoutConfig | None = None,
             first: str | None = None, **kw) -> tuple[ShootoutResult, ShootoutSide, ShootoutSide]:
    side_rng = random.Random(seed * 7 + 1)
    home = make_side(1, home_n, rng=side_rng, **kw)
    away = make_side(2, away_n, rng=side_rng, **kw)
    return run_shootout(random.Random(seed), home, away, first=first, config=config), home, away


def regulation_kicks(result: ShootoutResult):
    return [k for k in result.kicks if not k.sudden_death]


def sudden_kicks(result: ShootoutResult):
    return [k for k in result.kicks if k.sudden_death]


# ---------------------------------------------------------------------------
# Sira ve bitis kurallari
# ---------------------------------------------------------------------------

def test_kicks_strictly_alternate_starting_with_first_side():
    firsts = set()
    for seed in SEEDS:
        result, _, _ = shootout(seed)
        firsts.add(result.first_side)
        second = "away" if result.first_side == "home" else "home"
        for i, kick in enumerate(result.kicks):
            assert kick.side == (result.first_side if i % 2 == 0 else second), (seed, i)
            assert kick.number == i + 1
            assert kick.round == i // 2 + 1
        assert result.kicks[0].side == result.first_side
    assert firsts == {"home", "away"}          # yazi-tura iki tarafa da duser


def test_first_side_can_be_forced():
    for seed in range(50):
        for first in ("home", "away"):
            result, _, _ = shootout(seed, first=first)
            assert result.first_side == first and result.kicks[0].side == first
    with pytest.raises(ValueError):
        shootout(1, first="middle")


def _decided(h: int, a: int, ht: int, at: int, n: int = 5) -> bool:
    """Testin kendi (bagimsiz) hesabi: kalan tum atislar atilsa bile yetisilemiyor mu?"""
    return h + (n - ht) < a or a + (n - at) < h


def test_regulation_ends_exactly_when_mathematically_decided():
    early = full = 0
    for seed in SEEDS:
        result, _, _ = shootout(seed)
        reg = regulation_kicks(result)
        assert 1 <= len(reg) <= 10
        h = a = ht = at = 0
        states = []
        for kick in reg:
            if kick.side == "home":
                ht += 1
                h += kick.scored
            else:
                at += 1
                a += kick.scored
            assert (kick.home_score, kick.away_score) == (h, a)
            states.append(_decided(h, a, ht, at))
        # son atistan once hic karar anı yok (fazladan atis yok)
        assert not any(states[:-1]), (seed, states)
        if sudden_kicks(result):
            # ani olume gidildiyse: 5'er atis tamam, skor esit, karar yok (eksik atis yok)
            assert len(reg) == 10 and h == a and not states[-1]
            full += 1
        else:
            assert states[-1], seed           # seri tam karar aninda bitti
            early += len(reg) < 10
        assert regulation_decided(h, a, ht, at) == states[-1]
    assert early > 50 and full > 50           # iki senaryo da bolca uretildi


def test_regulation_decided_helper_examples():
    assert regulation_decided(3, 0, 3, 3)          # 3-0, deplasmanin 2 atisi kaldi
    assert not regulation_decided(3, 0, 3, 2)      # deplasmanin 3 atisi var: 3'e yetisebilir
    assert not regulation_decided(0, 2, 3, 2)      # ev en fazla 2 -> henuz esitleyebilir
    assert regulation_decided(4, 2, 5, 4)          # deplasman en fazla 3
    assert not regulation_decided(4, 4, 5, 5)      # esit: ani olum


def test_sudden_death_rounds_are_complete_and_stop_at_first_difference():
    seen = 0
    for seed in SEEDS:
        result, _, _ = shootout(seed)
        sd = sudden_kicks(result)
        if not sd:
            continue
        seen += 1
        assert len(sd) % 2 == 0                         # her tur iki atis
        rounds = [sd[i:i + 2] for i in range(0, len(sd), 2)]
        for pair in rounds[:-1]:
            assert pair[0].round == pair[1].round
            assert pair[0].scored == pair[1].scored     # tur esit bitti -> devam
        last = rounds[-1]
        assert last[0].scored != last[1].scored          # ilk fark -> seri bitti
        winner_kick = last[0] if last[0].scored else last[1]
        assert winner_kick.side == result.winner_side
        assert all(k.round > 5 for k in sd)
    assert seen > 50


def test_winner_has_higher_score_and_tallies_are_consistent():
    for seed in SEEDS:
        result, home, away = shootout(seed)
        assert result.home_score != result.away_score
        winner = result.home_score if result.winner_side == "home" else result.away_score
        loser = result.away_score if result.winner_side == "home" else result.home_score
        assert winner > loser and result.loser_side != result.winner_side
        last = result.kicks[-1]
        assert (last.home_score, last.away_score) == (result.home_score, result.away_score)
        assert result.home_score == sum(k.scored for k in result.side_kicks("home"))
        assert result.away_score == sum(k.scored for k in result.side_kicks("away"))
        for kick in result.kicks:
            assert kick.outcome in {"scored", "saved", "missed"}
            assert kick.team_id == (home.team_id if kick.side == "home" else away.team_id)
            assert kick.keeper_name == (away.keeper_name if kick.side == "home" else home.keeper_name)
        assert result.scoreline == f"{result.home_score}-{result.away_score}"


def test_deterministic_by_seed():
    for seed in range(40):
        a, _, _ = shootout(seed)
        b, _, _ = shootout(seed)
        assert a == b
    assert len({tuple(k.outcome for k in shootout(s)[0].kicks) for s in range(40)}) > 20


# ---------------------------------------------------------------------------
# Atisci sirasi ve rotasyon
# ---------------------------------------------------------------------------

def test_kick_order_best_first_keeper_last():
    side = make_side(1, 11, rng=random.Random(3))
    order = kick_order(side.takers, side.keeper_id)
    assert order[-1].id == side.keeper_id
    skills = [t.skill for t in order[:-1]]
    assert skills == sorted(skills, reverse=True)
    assert {t.id for t in order} == {t.id for t in side.takers}


def test_everyone_kicks_before_anyone_kicks_twice_and_cycles_repeat():
    # Herkes hep gol atar -> seri ani olumde uzar, tavan devreye girer: rotasyon birkac tur doner
    cfg = ShootoutConfig(min_conversion=1.0, max_conversion=1.0, max_sudden_death_rounds=30)
    for seed in range(20):
        result, home, away = shootout(seed, config=cfg)
        for side_name, side in (("home", home), ("away", away)):
            takers = [k.player_id for k in result.side_kicks(side_name)]
            n = len(side.takers)
            assert len(takers) > 2 * n                                  # en az iki tam dongu
            assert len(set(takers[:n])) == n                            # ilk dongu: herkes bir kez
            assert all(takers[i] == takers[i % n] for i in range(len(takers)))   # ayni sira
            expected = [t.id for t in kick_order(side.takers, side.keeper_id)]
            assert takers[:n] == expected
            assert takers[n - 1] == side.keeper_id                      # kaleci en sonda


def test_rotation_cycle_with_natural_long_shootouts():
    """Gercekci olasiliklarla uzun serilerde de kimse erken ikinci kez atmaz."""
    checked = 0
    for seed in range(1500):
        result, home, away = shootout(seed, home_n=7, away_n=7)
        if len(result.kicks) <= 14:
            continue
        checked += 1
        for side_name in ("home", "away"):
            takers = [k.player_id for k in result.side_kicks(side_name)]
            assert len(set(takers[:7])) == 7
            assert all(takers[i] == takers[i % 7] for i in range(len(takers)))
    assert checked > 0


def test_infinite_loop_guard_breaks_tie_deterministically():
    cfg = ShootoutConfig(min_conversion=1.0, max_conversion=1.0, max_sudden_death_rounds=3)
    result, _, _ = shootout(5, config=cfg)
    assert result.rounds == 5 + 3 + 1
    assert abs(result.home_score - result.away_score) == 1
    last_two = result.kicks[-2:]
    assert last_two[0].round == last_two[1].round and last_two[0].scored != last_two[1].scored
    assert run_shootout(random.Random(5), *shootout(5, config=cfg)[1:], config=cfg) == result


# ---------------------------------------------------------------------------
# Esitlemek icin azaltma (reduce to equate)
# ---------------------------------------------------------------------------

def test_equalize_takers_drops_weakest_non_keepers():
    home = [PenaltyTaker(i, f"H{i}", s) for i, s in enumerate([80, 50, 70, 30, 60, 90, 55, 65, 75, 85, 20])]
    keeper_id = 10                                                   # kaleci en zayif (20) ama cikmaz
    away = [PenaltyTaker(100 + i, f"A{i}", 60) for i in range(9)]
    h, a = equalize_takers(home, away, keeper_id, 108)
    assert len(h) == len(a) == 9
    assert a == away                                                 # az olan taraf degismez
    assert keeper_id in {t.id for t in h}
    assert {t.id for t in home} - {t.id for t in h} == {3, 1}        # 30 ve 50 cikti
    assert [t.id for t in h] == [t.id for t in home if t.id not in {1, 3}]   # sira korunur

    same_h, same_a = equalize_takers(home, home, keeper_id, keeper_id)
    assert same_h == home and same_a == home

    tiny = [PenaltyTaker(1, "GK", 10)]
    h2, a2 = equalize_takers(tiny, away, 1, 108)
    assert h2 == tiny and len(a2) == 1 and a2[0].id == 108            # rakipte yalnizca kaleci kalir


def test_equalize_ties_are_deterministic():
    home = [PenaltyTaker(i, f"H{i}", 60) for i in range(11)]
    away = [PenaltyTaker(100 + i, f"A{i}", 60) for i in range(10)]
    h1, _ = equalize_takers(home, away, 0, 100)
    h2, _ = equalize_takers(list(home), list(away), 0, 100)
    assert h1 == h2 and len(h1) == 10 and h1[0].id == 0


def test_short_handed_side_forces_reduction_in_shootout():
    for seed in range(100):
        result, home, away = shootout(seed, home_n=11, away_n=9)
        home_ids = {k.player_id for k in result.side_kicks("home")}
        allowed, _ = equalize_takers(home.takers, away.takers, home.keeper_id, away.keeper_id)
        assert home_ids <= {t.id for t in allowed}
        dropped = {t.id for t in home.takers} - {t.id for t in allowed}
        assert len(dropped) == 2 and not (home_ids & dropped)
        assert home.keeper_id not in dropped


def test_empty_side_is_rejected():
    empty = ShootoutSide(9, "Boş", [], None, "kaleci", 50.0)
    with pytest.raises(ValueError):
        run_shootout(random.Random(1), empty, make_side(2))


# ---------------------------------------------------------------------------
# Olasilik modeli ve kalibrasyon
# ---------------------------------------------------------------------------

def test_probability_model_bounds_and_pressure():
    cfg = ShootoutConfig()
    neutral = conversion_probability(cfg.taker_reference, cfg.keeper_reference, False, cfg)
    assert neutral == pytest.approx(cfg.base_conversion)
    assert conversion_probability(cfg.taker_reference, cfg.keeper_reference, True, cfg) == pytest.approx(
        cfg.base_conversion - cfg.sudden_death_pressure)
    assert conversion_probability(200, 0, False, cfg) == cfg.max_conversion
    assert conversion_probability(0, 200, False, cfg) == cfg.min_conversion
    assert conversion_probability(80, 70) > conversion_probability(60, 70)
    assert conversion_probability(70, 90) < conversion_probability(70, 60)
    lo, hi = cfg.save_share_range
    assert lo <= save_share(0) < save_share(70) < save_share(90) <= hi


def _rates(keeper_skill: float, n: int = 400) -> tuple[float, float, float]:
    kicks = scored = saved = 0
    lengths = []
    for seed in range(n):
        result, _, _ = shootout(seed, keeper_skill=keeper_skill)
        kicks += len(result.kicks)
        scored += sum(k.scored for k in result.kicks)
        saved += sum(k.outcome == "saved" for k in result.kicks)
        lengths.append(len(result.kicks))
    return scored / kicks, saved / kicks, sum(lengths) / n


def test_realistic_conversion_rate():
    conversion, saved, avg_kicks = _rates(keeper_skill=78.0)
    assert 0.70 <= conversion <= 0.82, conversion
    assert 0.10 <= saved <= 0.22, saved
    assert 8.5 <= avg_kicks <= 11.5, avg_kicks


def test_stronger_keepers_save_more():
    weak_conv, weak_saved, _ = _rates(keeper_skill=60.0, n=300)
    strong_conv, strong_saved, _ = _rates(keeper_skill=92.0, n=300)
    assert strong_conv < weak_conv - 0.05
    assert strong_saved > weak_saved + 0.04
