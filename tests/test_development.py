"""
Gelisim, yaslanma, potansiyel ve genc girisi SAF kurallari (10. Asama).

Veritabani gerektirmez: CM_TEST_NO_DB=1 ile de calisir.
"""

from __future__ import annotations

import random
import statistics
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import development as dev  # noqa: E402
import finance  # noqa: E402
import fitness  # noqa: E402
import name_pools  # noqa: E402
import youth  # noqa: E402
from models import Position  # noqa: E402
from ratings import ENGINE_ATTRIBUTES, POSITION_WEIGHTS, ca_to_overall  # noqa: E402

SEASON = dev.REFERENCE_SEASON_WEEKS


def _grow(age, overall, potential, weeks, minutes=90, rating=6.5, morale=70, coach=None,
          academy=False, facilities=10, season_weeks=SEASON, progress=0.0):
    """Haftalik gelisimi apply_progress ile uygular (yas sabit). (overall, birikim) dondurur."""
    attrs = dict.fromkeys(ENGINE_ATTRIBUTES, overall)
    for _ in range(weeks):
        growth = dev.weekly_growth(age, overall, potential, minutes, rating if minutes else None, morale,
                                   coach, academy, facilities, season_weeks)
        step = dev.apply_progress(Position.MID, overall, potential, attrs, progress, growth)
        overall, potential, attrs, progress = step.overall, step.potential, step.attributes, step.progress
    return overall, progress


def _career(age, overall, potential, seasons, **kw):
    """Sezon sezon (yas ilerler) gelisim; her sezon sonundaki overall listesi."""
    progress, out = 0.0, []
    for _ in range(seasons):
        overall, progress = _grow(age, overall, potential, SEASON, progress=progress, **kw)
        out.append(overall)
        age += 1
    return out


# ===========================================================================
# 1) WONDERKID VE POTANSIYEL
# ===========================================================================

def test_wonderkid_rule_edges():
    assert (dev.WONDERKID_MIN_AGE, dev.WONDERKID_MAX_AGE) == (16, 21)
    assert dev.is_wonderkid(16, 50, 65)
    assert dev.is_wonderkid(21, 50, 65)
    assert not dev.is_wonderkid(15, 50, 65)            # yas alt siniri
    assert not dev.is_wonderkid(22, 50, 80)            # yas ust siniri
    assert not dev.is_wonderkid(18, 50, 64)            # fark 14
    assert dev.is_wonderkid(18, 50, 50 + dev.WONDERKID_MIN_GAP)
    assert not dev.is_wonderkid(18, 50, None)


def test_initial_potential_bounds_and_age_curve():
    rng = random.Random(4)
    for age in range(16, 38):
        for overall in (30, 60, 85, 99):
            pot = dev.initial_potential(rng, age, overall)
            assert overall <= pot <= 99
            if age >= 28:
                assert pot == overall
    young = [dev.initial_potential(rng, 17, 60) - 60 for _ in range(3000)]
    mid = [dev.initial_potential(rng, 23, 60) - 60 for _ in range(3000)]
    assert statistics.mean(young) > statistics.mean(mid) + 6
    high = sum(1 for g in young if g >= 25) / len(young)
    assert 0.0 < high < 0.15                           # nadir yuksek tavan


def test_potential_from_fm_uses_overall_scale():
    assert dev.potential_from_fm(170, 70) == ca_to_overall(170)
    assert dev.potential_from_fm(100, 80) == 80        # PA olcegi gucun altindaysa overall
    assert dev.potential_from_fm(None, 66) == 66
    assert dev.effective_potential(70, None) == 70 and dev.effective_potential(70, 65) == 70


def test_potential_scout_margin_exact_when_excellent():
    assert dev.potential_scout_margin(None) == dev.NO_SCOUT_POTENTIAL_MARGIN
    assert dev.potential_scout_margin(18) == dev.potential_scout_margin(20) == 0
    margins = [dev.potential_scout_margin(r) for r in range(1, 21)]
    assert margins == sorted(margins, reverse=True) and margins[0] > 0


# ===========================================================================
# 2) HAFTALIK GELISIM
# ===========================================================================

def test_growth_never_exceeds_potential():
    for gap in (0, 1, 2, 5, 30):
        g = dev.weekly_growth(17, 60, 60 + gap, 180, 9.5, 100, 20, False, 20, season_weeks=1)
        assert 0 <= g <= gap
    overall, progress = _grow(16, 60, 68, 400, minutes=180, rating=9.0, morale=100, coach=20)
    assert overall == 68 and progress == 0.0
    assert dev.weekly_growth(18, 70, 70, 90, 8.0, 90, 20, False, 20) == 0.0
    assert dev.weekly_growth(18, 70, None, 90, 8.0, 90, 20, False, 20) == 0.0


def test_playing_with_good_ratings_beats_not_playing():
    args = dict(age=18, overall=62, potential=84, morale=70, coach_youth=10, facilities=10)
    unused = dev.weekly_growth(minutes=0, avg_rating=None, in_academy=False, **args)
    starter = dev.weekly_growth(minutes=90, avg_rating=6.5, in_academy=False, **args)
    star = dev.weekly_growth(minutes=90, avg_rating=7.8, in_academy=False, **args)
    poor = dev.weekly_growth(minutes=90, avg_rating=5.0, in_academy=False, **args)
    assert star > starter > poor > unused > 0
    assert dev.playing_factor(180, False) > dev.playing_factor(90, False)     # hafta ici + hafta sonu


def test_better_youth_coach_develops_faster():
    base = dict(age=17, overall=55, potential=80, minutes=0, avg_rating=None, morale=70,
                in_academy=True, facilities=10)
    weak = dev.weekly_growth(coach_youth=2, **base)
    neutral = dev.weekly_growth(coach_youth=10, **base)
    great = dev.weekly_growth(coach_youth=19, **base)
    assert great > neutral > weak
    assert dev.weekly_growth(coach_youth=None, **base) == pytest.approx(neutral)   # antrenor yok: ceza yok


def test_academy_baseline_between_unused_senior_and_starter():
    common = dict(age=17, overall=58, potential=82, morale=70, coach_youth=None, facilities=10)
    unused = dev.weekly_growth(minutes=0, avg_rating=None, in_academy=False, **common)
    academy = dev.weekly_growth(minutes=0, avg_rating=None, in_academy=True, **common)
    starter = dev.weekly_growth(minutes=90, avg_rating=6.0, in_academy=False, **common)
    assert unused < academy < starter
    # tesis yalnizca akademide etkili
    good = dev.weekly_growth(minutes=0, avg_rating=None, in_academy=True, **{**common, "facilities": 20})
    assert good > academy
    senior_good = dev.weekly_growth(minutes=90, avg_rating=6.0, in_academy=False, **{**common, "facilities": 20})
    assert senior_good == pytest.approx(starter)


def test_no_growth_from_thirty():
    for age in (30, 31, 34):
        assert dev.age_growth_factor(age) == 0
        assert dev.weekly_growth(age, 70, 90, 90, 8.0, 90, 20, False, 20) == 0.0
    factors = [dev.age_growth_factor(a) for a in range(16, 31)]
    assert factors == sorted(factors, reverse=True) and factors[0] == 1.0


def test_calibration_short_season():
    # 17 yas wonderkid, her mac oynar: sezonda 3-5 OVR, 22-23 yasinda tavana yaklasir
    starter = _career(17, 62, 85, 7)
    assert 3 <= starter[0] - 62 <= 5
    assert 85 - starter[5] <= 4 and 85 - starter[6] <= 3            # 22 ve 23 yas sonu
    unused = _career(17, 62, 85, 7, minutes=0)
    assert 85 - unused[6] >= 12                                    # oynamayan acikca altinda
    academy = _career(17, 62, 85, 7, minutes=0, academy=True)
    assert unused[6] < academy[6] < starter[6]


def test_season_length_scaling_keeps_per_season_growth():
    short = dev.weekly_growth(18, 60, 80, 90, 6.5, 70, 10, False, 10, season_weeks=7) * 7
    long = dev.weekly_growth(18, 60, 80, 90, 6.5, 70, 10, False, 10, season_weeks=38) * 38
    assert short == pytest.approx(long)
    assert dev.weekly_decline(35, 38) * 38 == pytest.approx(dev.weekly_decline(35, 7) * 7)


# ===========================================================================
# 3) YASLANMA
# ===========================================================================

def test_decline_starts_at_32_and_accelerates():
    assert all(dev.weekly_decline(age) == 0 for age in range(16, 32))
    seasons = [dev.season_decline(age) for age in range(32, 39)]
    assert seasons[0] == pytest.approx(1.0)
    assert all(b > a for a, b in zip(seasons, seasons[1:], strict=False))
    assert dev.weekly_decline(36) > dev.weekly_decline(34) > dev.weekly_decline(32) > 0


def test_decline_over_a_season_matches_curve():
    for age, expected in ((32, 1), (34, 1), (36, 3), (38, 6)):
        overall, progress, potential = 80, 0.0, 80
        attrs = dict.fromkeys(ENGINE_ATTRIBUTES, 80)
        for _ in range(SEASON):
            step = dev.apply_progress(Position.DEF, overall, potential, attrs, progress,
                                      decline=dev.weekly_decline(age, SEASON))
            overall, potential, attrs, progress = step.overall, step.potential, step.attributes, step.progress
        assert 80 - overall == expected, age
        assert potential == overall                         # gerileyen oyuncunun tavani guncel guc


@pytest.mark.parametrize("position", list(Position))
def test_pace_drops_faster_than_other_attributes(position):
    attrs = dict.fromkeys(ENGINE_ATTRIBUTES, 70)
    overall = 70
    for _ in range(6):
        step = dev.apply_progress(position, overall, overall, attrs, 0.0, decline=1.0)
        assert step.change == -1
        overall, attrs = step.overall, step.attributes
    losses = {a: 70 - attrs[a] for a in ENGINE_ATTRIBUTES}
    assert losses["pace"] == max(losses.values())
    assert all(losses["pace"] > loss for a, loss in losses.items() if a != "pace")
    assert losses["dribbling"] >= 1
    weighted = sum(POSITION_WEIGHTS[position][a] * losses[a] for a in ENGINE_ATTRIBUTES)
    assert 6 <= weighted <= 8                               # ~1 overall basina


def test_growth_raises_top_weighted_attributes_and_keeps_bounds():
    attrs = {"pace": 60, "shooting": 60, "passing": 60, "defending": 30, "dribbling": 60, "goalkeeping": 20}
    step = dev.apply_progress(Position.FWD, 60, 70, attrs, 0.95, growth=0.1)
    assert step.change == 1 and step.progress == pytest.approx(0.05)
    gains = {a: step.attributes[a] - attrs[a] for a in ENGINE_ATTRIBUTES}
    assert gains["shooting"] == max(gains.values()) and gains["goalkeeping"] == 0
    # 99'daki ozellik tasmaz, 1'deki inmez
    maxed = dict.fromkeys(ENGINE_ATTRIBUTES, 99)
    assert all(v <= 99 for v in dev.raise_attributes(Position.MID, maxed).values())
    floor = dict.fromkeys(ENGINE_ATTRIBUTES, 1)
    low = dev.apply_progress(Position.MID, 1, 1, floor, 0.0, decline=5.0)
    assert low.overall == 1 and all(v == 1 for v in low.attributes.values())


# ===========================================================================
# 4) KONDISYON VE PIYASA DEGERI
# ===========================================================================

def test_age_recovery_factor_and_condition():
    assert dev.age_recovery_factor(None) == dev.age_recovery_factor(31) == 1.0
    factors = [dev.age_recovery_factor(a) for a in range(31, 42)]
    assert factors == sorted(factors, reverse=True) and factors[1] < 1.0
    assert min(factors) >= dev.RECOVERY_AGE_FLOOR
    assert fitness.recover_condition(56, 10) == fitness.recover_condition(56, 10, age=None) == 89
    assert fitness.recover_condition(56, 10, age=25) == 89
    assert fitness.recover_condition(56, 10, age=34) < 89
    assert fitness.recover_condition(56, 10, 0.5, age=36) < fitness.recover_condition(56, 10, 0.5)


def test_market_value_potential_premium():
    base = finance.market_value(60, 17, Position.MID)
    assert finance.market_value(60, 17, Position.MID, None) == base
    wonderkid = finance.market_value(60, 17, Position.MID, potential=88)
    assert wonderkid > base * 10
    assert finance.market_value(60, 17, Position.MID, potential=75) < wonderkid
    assert finance.market_value(80, 22, Position.FWD, 88) > finance.market_value(80, 22, Position.FWD)
    # 26+ yasta prim yok; potansiyel = guc ise prim yok
    assert finance.market_value(80, 27, Position.DEF, 90) == finance.market_value(80, 27, Position.DEF)
    assert finance.market_value(80, 19, Position.DEF, 80) == finance.market_value(80, 19, Position.DEF)
    # akademi genci sifira yuvarlanmaz
    assert finance.market_value(35, 16, Position.GK, 40) >= finance.MIN_VALUE_WITH_POTENTIAL
    assert finance.academy_wage(35, 50) >= finance.ACADEMY_MIN_WAGE


# ===========================================================================
# 5) GENC GIRISI
# ===========================================================================

@pytest.mark.parametrize(("country", "pool"), [("Türkiye", "Turkiye"), ("İngiltere", "Ingiltere"),
                                               ("İspanya", "Ispanya"), ("Almanya", "Almanya"),
                                               ("Bilinmeyen Ülke", "Ingiltere")])
def test_intake_names_ages_and_attributes(country, pool):
    firsts, lasts = name_pools.YOUTH_NAME_POOLS[pool]
    used: set[str] = set()
    names = []
    for seed in range(40):
        specs = youth.generate_intake(random.Random(seed), country, 12, 80, 4, used)
        assert len(specs) == 4
        for s in specs:
            assert s.age in (16, 17)
            assert youth.OVERALL_MIN <= s.overall <= youth.OVERALL_MAX
            assert s.overall < s.potential <= youth.POTENTIAL_CAP
            assert all(1 <= v <= 99 for v in s.attributes.values())
            first, last = s.name.split(" ", 1)
            assert first in firsts and last in lasts
            names.append(s.name)
    assert len(names) == len(set(names))                     # used_names ile cakisma yok
    assert youth.generate_intake(random.Random(1), country, 12, 80, 0) == []


def test_intake_distribution_wonderkids_minority_and_facilities_shift():
    def batch(facilities, reputation, seeds=600):
        out = []
        for seed in range(seeds):
            out += youth.generate_intake(random.Random(seed), "Türkiye", facilities, reputation, 4)
        return out

    poor, top = batch(2, 60), batch(20, 95)
    assert {s.age for s in poor + top} == {16, 17}
    for specs in (poor, top):
        share = youth.wonderkid_share(specs)
        assert 0.0 < share < 0.35                           # bazen cevher, cogunlukla vasat
        keepers = sum(1 for s in specs if s.position is Position.GK) / len(specs)
        assert 0.03 < keepers < 0.14
    assert youth.wonderkid_share(top) > youth.wonderkid_share(poor)
    assert statistics.mean(s.potential for s in top) > statistics.mean(s.potential for s in poor) + 5
    assert max(s.potential for s in top) >= 80                # gercek cevher cikabiliyor


def test_initial_academy_ages_and_facilities_curve():
    specs = youth.generate_academy(random.Random(9), "Fransa", 10, 80, 200)
    assert {s.age for s in specs} <= {16, 17, 18, 19} and len({s.age for s in specs}) == 4
    assert youth.default_facilities(95) > youth.default_facilities(73) > youth.default_facilities(50)
    rng = random.Random(2)
    assert all(1 <= youth.default_facilities(r, rng) <= 20 for r in range(1, 101))
    assert -1.0 <= youth.quality_index(1, 1) < youth.quality_index(20, 99) <= 1.0


# ===========================================================================
# 6) SEED: kidemli dunya birebir ayni (ayri RNG akisi)
# ===========================================================================

SYNTHETIC_SENIOR_FINGERPRINT = 2396590399      # 10. Asama oncesi build_synthetic_world(2026) ile ayni


def test_synthetic_senior_world_is_unchanged_by_youth_generation():
    import seed

    assert len(name_pools.NAME_POOLS["Ispanya"][0]) == 10     # havuz degisirse adlar kayar
    world = seed.build_synthetic_world(2026)
    rows = [(c.name, c.formation, [(p.name, p.age, p.position.value, p.overall, sorted(p.attributes.items()),
                                    p.form, p.morale, p.contract_years) for p in c.players])
            for c in world.clubs]
    assert zlib.crc32(repr(rows).encode()) == SYNTHETIC_SENIOR_FINGERPRINT
    assert world.player_count == 360 and world.youth_ready
    for club in world.clubs:
        assert 1 <= club.youth_facilities <= 20
        assert youth.INITIAL_ACADEMY_SIZE[0] <= len(club.academy) <= youth.INITIAL_ACADEMY_SIZE[1]
        assert all(p.potential is not None and p.potential >= p.overall for p in club.players + club.academy)
        assert all(p.data_source == "academy" and 16 <= p.age <= 19 for p in club.academy)
    senior_names = {p.name for c in world.clubs for p in c.players}
    academy_names = [p.name for c in world.clubs for p in c.academy]
    assert not senior_names & set(academy_names) and len(academy_names) == len(set(academy_names))
