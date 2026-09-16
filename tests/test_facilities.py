"""
Kulup tesisleri ve sponsorluk SAF kural testleri (11. Asama, facilities.py).

Veritabani gerektirmez: maliyet egrileri, sinirlar, saglik merkezi carpani (fitness ile birlikte),
mac gunu geliri, sponsor teklifi uretimi / belirlenimciligi / JSON donusumu ve kurgusal marka adlari.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path
from statistics import mean

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import facilities as fac  # noqa: E402
import fitness  # noqa: E402
import youth  # noqa: E402
from facilities import SponsorOffer  # noqa: E402
from models import Team  # noqa: E402

# Gercek sponsor / sirket adlari: kurgusal markalarda ASLA gecmemeli (buyuk-kucuk harf duyarsiz alt dize)
REAL_BRANDS = (
    "emirates", "etihad", "qatar", "turkish airlines", "thy", "pegasus", "adidas", "nike", "puma",
    "umbro", "jeep", "allianz", "audi", "bmw", "volkswagen", "spotify", "rakuten", "teamviewer",
    "standard chartered", "chevrolet", "vodafone", "turkcell", "türk telekom", "deutsche telekom",
    "t-mobile", "beko", "socar", "red bull", "coca", "pepsi", "heineken", "carlsberg", "samsung",
    "aon", "ing", "santander", "unicredit", "fly better", "visit rwanda", "cazoo", "betway", "bet365",
    "stake", "sorare", "binance", "crypto.com", "gazprom", "evonik", "opel", "hyundai", "kia",
    "garanti", "akbank", "ziraat", "yapı kredi", "papara", "getir", "trendyol", "hepsiburada",
)


# ===========================================================================
# 1) YUKSELTME MALIYETLERI VE SINIRLAR
# ===========================================================================

@pytest.mark.parametrize("kind", fac.FACILITY_KINDS)
def test_upgrade_costs_rise_with_level_and_are_rounded(kind):
    costs = [fac.facility_upgrade_cost(kind, level) for level in range(fac.FACILITY_MIN, fac.FACILITY_MAX)]
    assert len(costs) == 19
    assert all(a < b for a, b in zip(costs, costs[1:], strict=False))
    assert all(c % 10_000 == 0 for c in costs)
    # Kalibrasyon: erken seviye orta kulup kasasinin (~90M) %1'i civari, ust seviye 10M+
    assert 500_000 <= costs[0] <= 1_000_000
    assert 2_000_000 <= fac.facility_upgrade_cost(kind, 10) <= 4_000_000
    assert costs[-1] >= 10_000_000


def test_youth_upgrade_costs_more_than_medical_and_caps_are_enforced():
    for level in range(1, 20):
        assert fac.facility_upgrade_cost("youth", level) > fac.facility_upgrade_cost("medical", level)
    for bad in (0, 20, 21, -3):
        with pytest.raises(ValueError):
            fac.facility_upgrade_cost("youth", bad)
    with pytest.raises(ValueError, match="Bilinmeyen tesis"):
        fac.facility_upgrade_cost("stadium", 5)                  # stadyum koltukla olculur
    with pytest.raises(ValueError, match="Bilinmeyen tesis"):
        fac.facility_upgrade_cost("training", 5)


def test_stadium_expansion_costs_rise_with_size_and_cap_at_max():
    capacities = list(range(fac.STADIUM_MIN, fac.STADIUM_MAX, fac.STADIUM_STEP))
    costs = [fac.stadium_expansion_cost(c) for c in capacities]
    assert all(a < b for a, b in zip(costs, costs[1:], strict=False))
    assert costs[0] == 2_500_000 and fac.stadium_expansion_cost(50_000) == 4_500_000
    # Kismi son adim: 88.000 -> 90.000 (2.000 koltuk) tam adimdan ucuz
    assert fac.next_stadium_capacity(88_000) == fac.STADIUM_MAX
    assert fac.stadium_expansion_cost(88_000) < fac.stadium_expansion_cost(85_000)
    assert fac.next_stadium_capacity(45_000) == 50_000
    with pytest.raises(ValueError, match="en büyük"):
        fac.stadium_expansion_cost(fac.STADIUM_MAX)


# ===========================================================================
# 2) SAGLIK MERKEZI -> KONDISYON TOPARLANMASI
# ===========================================================================

def test_medical_multiplier_is_neutral_at_ten_and_monotonic():
    assert fac.medical_recovery_multiplier(None) == 1.0
    assert fac.medical_recovery_multiplier(10) == 1.0
    assert fac.medical_recovery_multiplier(20) == pytest.approx(1.30)
    assert fac.medical_recovery_multiplier(1) == pytest.approx(0.82)
    values = [fac.medical_recovery_multiplier(level) for level in range(1, 21)]
    assert all(a < b for a, b in zip(values, values[1:], strict=False))
    assert fac.medical_recovery_multiplier(99) == fac.medical_recovery_multiplier(20)    # kirpilir
    assert fac.medical_recovery_multiplier(-5) == fac.medical_recovery_multiplier(1)


def test_recover_condition_default_behaviour_unchanged_without_multiplier():
    for energy in (0, 12.5, 35, 56, 80, 99, 100):
        for physio in (None, 1, 10, 20):
            for share in (1.0, fitness.MIDWEEK_RECOVERY_SHARE):
                for age in (None, 24, 34):
                    base = fitness.recover_condition(energy, physio, share, age=age)
                    assert fitness.recover_condition(energy, physio, share, age=age, medical_multiplier=None) == base
                    assert fitness.recover_condition(energy, physio, share, age=age, medical_multiplier=1.0) == base
    assert fitness.recover_condition(56, 10) == 89                  # dokumante ornek aynen


def test_medical_multiplier_speeds_recovery_but_never_exceeds_100():
    low, high = fac.medical_recovery_multiplier(1), fac.medical_recovery_multiplier(20)
    slow = fitness.recover_condition(40, 10, medical_multiplier=low)
    normal = fitness.recover_condition(40, 10)
    fast = fitness.recover_condition(40, 10, medical_multiplier=high)
    assert slow < normal < fast                                     # 40+60*0.615=77 < 85 < 40+60*0.975=99
    for energy in (0, 30, 70):
        assert fitness.recover_condition(energy, 20, medical_multiplier=high) <= fitness.CONDITION_MAX
        assert fitness.recover_condition(energy, 20, medical_multiplier=5.0) == fitness.CONDITION_MAX
    assert fitness.recover_condition(50, 10, medical_multiplier=0.0) == 50
    # Hafta ici paylasim hala sinirlar: en iyi saglik merkezi bile rotasyon ihtiyacini kaldirmaz
    assert fitness.recover_condition(40, 20, fitness.MIDWEEK_RECOVERY_SHARE, medical_multiplier=high) < 80


# ===========================================================================
# 3) ALTYAPI ETKI ONIZLEMESI
# ===========================================================================

def test_youth_preview_rises_with_level():
    shifts = [fac.youth_potential_shift(level, 85) for level in range(1, 21)]
    assert all(a <= b for a, b in zip(shifts, shifts[1:], strict=False)) and shifts[-1] > shifts[0] + 5
    assert fac.youth_growth_multiplier(1) < fac.youth_growth_multiplier(10) == 1.0 < fac.youth_growth_multiplier(20)


def test_generate_intake_uses_facility_level():
    """youth.generate_intake tesis puanini kullanir: ayni tohum, yuksek tesis -> ortalama potansiyel yukarida."""
    low, high = [], []
    for s in range(60):
        low += [p.potential for p in youth.generate_intake(random.Random(s), "Türkiye", 1, 80, 4)]
        high += [p.potential for p in youth.generate_intake(random.Random(s), "Türkiye", 20, 80, 4)]
    assert mean(high) - mean(low) >= 5
    expected = fac.youth_potential_shift(20, 80) - fac.youth_potential_shift(1, 80)
    assert abs((mean(high) - mean(low)) - expected) < 3.5             # onizleme olculen etkiyle uyumlu


# ===========================================================================
# 4) MAC GUNU GELIRI
# ===========================================================================

def test_gate_income_only_for_home_matches_with_known_capacity():
    assert fac.gate_income(50_000, 85, is_home=False) == 0
    assert fac.gate_income(None, 85) == 0 and fac.gate_income(0, 85) == 0
    income = fac.gate_income(50_000, 85)
    assert income == 1_274_000 and income % 1000 == 0          # 50.000 x ~25.5 EUR


def test_gate_income_grows_with_capacity_until_demand_and_with_reputation():
    demand = fac.stadium_demand(85)
    below = fac.gate_income(demand - 10_000, 85)
    at = fac.gate_income(demand, 85)
    assert below < at == fac.gate_income(demand + 20_000, 85) == fac.gate_income(fac.STADIUM_MAX, 85)
    assert fac.attendance(demand + 20_000, 85) == demand
    for capacity in (15_000, 40_000, 70_000):
        incomes = [fac.gate_income(capacity, rep) for rep in (50, 73, 85, 95)]
        assert all(a <= b for a, b in zip(incomes, incomes[1:], strict=False))
    demands = [fac.stadium_demand(rep) for rep in range(1, 101)]
    assert all(a <= b for a, b in zip(demands, demands[1:], strict=False))
    assert fac.STADIUM_MIN <= demands[0] and demands[-1] <= fac.STADIUM_MAX
    # Kalibrasyon: kucuk / orta / elit kulubun mac basi net geliri
    assert 50_000 <= fac.gate_income(12_000, 50) <= 150_000
    assert 1_000_000 <= fac.gate_income(50_000, 85) <= 1_600_000
    assert 1_800_000 <= fac.gate_income(70_000, 95) <= 2_800_000


# ===========================================================================
# 5) VARSAYILAN TESISLER
# ===========================================================================

def test_default_facilities_are_deterministic_bounded_and_scale_with_reputation():
    for rep in (1, 35, 50, 73, 85, 95, 100):
        a = fac.default_facilities(rep, random.Random(f"x{rep}"))
        b = fac.default_facilities(rep, random.Random(f"x{rep}"))
        assert a == b
        assert fac.STADIUM_MIN <= a.stadium_capacity <= fac.STADIUM_MAX and a.stadium_capacity % 1000 == 0
        assert fac.FACILITY_MIN <= a.medical_facilities <= fac.FACILITY_MAX
    assert fac.default_medical_facilities(80) == 10
    assert fac.default_medical_facilities(50) < fac.default_medical_facilities(80) < fac.default_medical_facilities(95)
    assert fac.default_stadium_capacity(50) < fac.default_stadium_capacity(85) < fac.default_stadium_capacity(95)
    # Varsayilan stat talebin altinda: genisletme anlamli bir karar
    for rep in (60, 73, 85, 95):
        assert fac.default_stadium_capacity(rep) < fac.stadium_demand(rep)


# ===========================================================================
# 6) SPONSORLUK
# ===========================================================================

def test_sponsor_base_scales_with_reputation():
    values = [fac.sponsor_base_weekly(rep) for rep in range(1, 101)]
    assert all(a <= b for a, b in zip(values, values[1:], strict=False))
    assert 20_000 <= fac.sponsor_base_weekly(50) <= 40_000
    assert 300_000 <= fac.sponsor_base_weekly(85) <= 450_000
    assert 500_000 <= fac.sponsor_base_weekly(95) <= 700_000


def test_offers_are_deterministic_and_have_trade_offs():
    offers = fac.generate_sponsor_offers(random.Random(7), 85)
    assert offers == fac.generate_sponsor_offers(random.Random(7), 85)
    assert offers != fac.generate_sponsor_offers(random.Random(8), 85)
    assert len(offers) == 3 and len({o.brand for o in offers}) == 3
    high_weekly, long_term, bonus = offers
    assert high_weekly.weekly == max(o.weekly for o in offers) and high_weekly.seasons == 1
    assert high_weekly.signing_bonus == 0
    assert long_term.seasons == max(o.seasons for o in offers) >= 3
    assert bonus.signing_bonus == max(o.signing_bonus for o in offers) > 0
    assert bonus.weekly == min(o.weekly for o in offers)
    for o in offers:
        assert o.weekly % 1000 == 0 and o.signing_bonus % 10_000 == 0
        assert 1 <= o.seasons <= fac.SPONSOR_MAX_SEASONS
    with pytest.raises(AttributeError):
        high_weekly.weekly = 1                                   # frozen


def test_offer_amounts_scale_with_reputation_and_count_is_respected():
    for s in range(20):
        small = fac.generate_sponsor_offers(random.Random(s), 55)
        big = fac.generate_sponsor_offers(random.Random(s), 95)
        assert all(b.weekly > a.weekly for a, b in zip(small, big, strict=True))
    assert fac.generate_sponsor_offers(random.Random(1), 80, count=0) == []
    five = fac.generate_sponsor_offers(random.Random(1), 80, count=5, exclude_brands={"Veltrano Enerji"})
    assert len(five) == 5 and len({o.brand for o in five}) == 5
    assert "Veltrano Enerji" not in {o.brand for o in five}
    assert five[3].seasons == 1 and five[4].seasons >= 3          # profiller dongusel


def test_offer_profiles_are_balanced_and_bonuses_scale_with_season_length():
    base = fac.sponsor_base_weekly(85)
    for weeks in (7, 20, 38):
        scores: list[list[float]] = [[], [], []]
        best = set()
        for s in range(300):
            offers = fac.generate_sponsor_offers(random.Random(s), 85, season_weeks=weeks)
            best.add(fac.best_offer_index(offers, weeks))
            for i, offer in enumerate(offers):
                scores[i].append(fac.offer_score(offer, weeks) / (base * weeks))
        # Sezon basina deger her dunyada benzer: kisa sezonda imza primi her seyi ezmez
        assert all(1.1 <= mean(profile) <= 1.3 for profile in scores), (weeks, [mean(p) for p in scores])
        assert len(best) == 3                                     # secim gercek bir karar (tek dogru yok)
    short = fac.generate_sponsor_offers(random.Random(4), 85, season_weeks=7)
    long = fac.generate_sponsor_offers(random.Random(4), 85, season_weeks=38)
    assert [(o.brand, o.weekly, o.seasons) for o in short] == [(o.brand, o.weekly, o.seasons) for o in long]
    assert short[0].signing_bonus == long[0].signing_bonus == 0
    assert short[2].signing_bonus < long[2].signing_bonus
    assert fac.best_offer_index([], 20) is None
    offer = SponsorOffer("Kavrix Gıda", 100_000, 2, 500_000)
    assert offer.total_value(10) == 2_500_000
    assert fac.offer_score(offer, 10) == pytest.approx(1_250_000 * (1 + fac.SPONSOR_SECURITY_PREMIUM))


def test_offer_json_round_trip_and_bad_rows_are_skipped():
    offers = fac.generate_sponsor_offers(random.Random(3), 90)
    data = fac.offers_to_json(offers)
    assert all(set(row) == {"brand", "weekly", "seasons", "signing_bonus"} for row in data)
    import json
    assert fac.offers_from_json(json.loads(json.dumps(data))) == offers
    messy = [*data, {"brand": "", "weekly": 1, "seasons": 1}, {"weekly": 5}, "metin", None,
             {"brand": "Ondexa Yapı", "weekly": -1, "seasons": 1, "signing_bonus": 0},
             {"brand": "Ondexa Yapı", "weekly": 1000, "seasons": 9, "signing_bonus": 0}]
    assert fac.offers_from_json(messy) == offers
    assert fac.offers_from_json(None) == [] and fac.offers_from_json({"brand": "x"}) == []
    with pytest.raises(ValueError):
        SponsorOffer.from_dict({"brand": "Nuvaro Turizm", "weekly": 10, "seasons": 0})


def test_brand_names_are_fictional_and_fit_the_column():
    names = {fac.brand_name(random.Random(s)) for s in range(400)}
    names |= {o.brand for s in range(100) for o in fac.generate_sponsor_offers(random.Random(s), 80)}
    names |= {fac.starting_sponsor(random.Random(s), 80).brand for s in range(100)}
    column_length = Team.__table__.c.sponsor_name.type.length
    assert len(names) > 50
    for name in names:
        assert 0 < len(name) <= min(fac.BRAND_MAX_LENGTH, column_length)
        lowered = name.lower()
        assert not any(real in lowered.split() or (" " in real and real in lowered) for real in REAL_BRANDS), name
    for stem in fac.BRAND_STEMS:
        assert not any(real in stem.lower() for real in REAL_BRANDS if len(real) > 3), stem
    # Havuz tukense de benzersiz ad uretilir
    taken = {f"{s} {sec}" for s in fac.BRAND_STEMS for sec in fac.BRAND_SECTORS}
    assert fac.brand_name(random.Random(1), taken) not in taken


def test_starting_sponsor_is_below_market_without_bonus():
    for s in range(30):
        contract = fac.starting_sponsor(random.Random(s), 85)
        assert contract.signing_bonus == 0 and 1 <= contract.seasons <= 3
        assert contract.weekly <= fac.sponsor_base_weekly(85)


def test_contract_helpers():
    assert fac.contract_end_season(3, 1) == 3 and fac.contract_end_season(3, 4) == 6
    assert fac.sponsor_active("Lurivo Enerji", 3, 3) and not fac.sponsor_active("Lurivo Enerji", 2, 3)
    assert not fac.sponsor_active(None, 5, 3) and not fac.sponsor_active("Lurivo Enerji", None, 3)
    assert fac.sponsor_seasons_left(5, 3) == 3 and fac.sponsor_seasons_left(2, 3) == 0
    assert fac.sponsor_seasons_left(None, 3) == 0


# ===========================================================================
# 7) SEMA: yeni sutunlar eski kayitlara eklenebilir
# ===========================================================================

NEW_TEAM_COLUMNS = ("stadium_capacity", "medical_facilities", "sponsor_name", "sponsor_weekly",
                    "sponsor_until_season", "sponsor_offers")


def test_new_team_columns_are_additive_and_safe_for_old_saves():
    additive = {(table, column): ddl for table, column, ddl in database.ADDITIVE_COLUMNS}
    for column in NEW_TEAM_COLUMNS:
        assert column in Team.__table__.c
        ddl = additive[("teams", column)]
        col = Team.__table__.c[column]
        assert col.nullable or "DEFAULT" in ddl, column          # eski satirlar icin guvenli
    checks = {c.name for c in Team.__table__.constraints if c.name}
    assert {"ck_team_stadium_capacity", "ck_team_medical_facilities", "ck_team_sponsor_weekly",
            "ck_team_sponsor_until_season", "ck_team_sponsor_offers"} <= checks
    assert Team.__table__.c.sponsor_name.type.length == 60
