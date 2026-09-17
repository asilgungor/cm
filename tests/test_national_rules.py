"""
Milli takim kural testleri (Faz 12 / 14. Asama, 12C): national_rules.py uyruk normalizasyonu, uygun uluslar,
AI kadro cagrisi, is teklifi esigi, sozlesme, federasyon beklentisi, gorevden alma ve taninirlik.
Saf testler: veritabani ve Streamlit gerektirmez.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import national_rules as nr  # noqa: E402
import reputation  # noqa: E402
from club_directory import LEAGUES  # noqa: E402
from models import Position  # noqa: E402

# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

@dataclass
class FakePlayer:
    id: int
    position: object
    overall_rating: int
    age: int = 25
    injured_until_week: int = 0

    def is_injured(self, week: int) -> bool:
        return self.injured_until_week > week


@dataclass
class BarePlayer:
    """Yalnizca zorunlu alanlar (yas ve sakatlik alani yok)."""
    id: int
    position: str
    overall_rating: int


def squad(counts: dict[str, int], start: int = 1, base: int = 80) -> list[FakePlayer]:
    """Mevki basina oyuncu; her mevkide guc azalan (ilk oyuncu en guclu)."""
    players, next_id = [], start
    for pos, count in counts.items():
        for i in range(count):
            players.append(FakePlayer(next_id, Position(pos), base - i))
            next_id += 1
    return players


def positions_of(ids: list[int], players: list) -> Counter:
    by_id = {p.id: p for p in players}
    return Counter(getattr(by_id[i].position, "value", by_id[i].position) for i in ids)


# ===========================================================================
# Sabitler ve uyruk
# ===========================================================================

def test_constants_follow_soccer_manager_rules():
    assert (nr.MIN_NATIONAL_PLAYERS, nr.MAX_CALLUPS, nr.MIN_MATCHDAY_SQUAD, nr.CONTRACT_MAX_SEASONS) == (23, 30, 16, 2)


@pytest.mark.parametrize(
    ("nationality", "league_country", "expected"),
    [
        ("TUR", "İngiltere", "Türkiye"),
        ("turkey", "İngiltere", "Türkiye"),
        (" Türkiye ", "İngiltere", "Türkiye"),
        ("TURKIYE", "Fransa", "Türkiye"),
        ("ENG", "Türkiye", "İngiltere"),
        ("Germany", "Türkiye", "Almanya"),
        ("Côte d'Ivoire", "Fransa", "Fildişi Sahili"),
        ("CIV", "Fransa", "Fildişi Sahili"),
        ("Cabo   Verde", "Fransa", "Cabo Verde"),       # bilinmeyen: bosluklar sadelesir
        (None, "İspanya", "İspanya"),                   # sentetik oyuncu: lig ulkesi
        ("", "Almanya", "Almanya"),
        ("   ", "İtalya", "İtalya"),
        (None, "Turkey", "Türkiye"),                    # lig ulkesi de normallesir
        (None, "", "Diğer"),
        (None, "Diğer", "Diğer"),
    ],
)
def test_nation_of(nationality, league_country, expected):
    assert nr.nation_of(nationality, league_country) == expected


def test_synthetic_world_league_countries_are_six_nations():
    nations = {nr.nation_of(None, country) for country in LEAGUES.values()}
    assert nations == set(LEAGUES.values())
    assert len(nations) == 6


def test_eligible_nations_threshold_merge_and_order():
    counts = {
        "Türkiye": 12, "TUR": 11,          # ayni ulus: 23
        "Brezilya": 40,
        "Almanya": 22,                     # esigin 1 alti
        "İspanya": 23,
        "Diğer": 90,                       # sozde ulus
        "": 50,
        "Fransa": 40,
    }
    assert nr.eligible_nations(counts) == ["Brezilya", "Fransa", "İspanya", "Türkiye"]
    assert nr.eligible_nations({}) == []
    assert nr.eligible_nations({"İngiltere": nr.MIN_NATIONAL_PLAYERS}) == ["İngiltere"]


# ===========================================================================
# Kadro cagrisi
# ===========================================================================

@pytest.mark.parametrize(
    ("limit", "quotas"),
    [
        (23, {"GK": 3, "DEF": 8, "MID": 7, "FWD": 5}),
        (30, {"GK": 4, "DEF": 10, "MID": 9, "FWD": 7}),
        (16, {"GK": 2, "DEF": 6, "MID": 5, "FWD": 3}),
        (99, {"GK": 4, "DEF": 10, "MID": 9, "FWD": 7}),   # MAX_CALLUPS'a kirpilir
        (1, {"GK": 1, "DEF": 0, "MID": 0, "FWD": 0}),
        (0, {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}),
        (-3, {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}),
    ],
)
def test_position_quotas(limit, quotas):
    assert nr.position_quotas(limit) == quotas


def test_position_quotas_always_sum_to_limit_with_a_keeper():
    for limit in range(1, nr.MAX_CALLUPS + 1):
        quotas = nr.position_quotas(limit)
        assert sum(quotas.values()) == limit
        assert quotas["GK"] >= 1


def test_ai_callups_respects_quotas_and_picks_the_best():
    players = squad({"GK": 5, "DEF": 12, "MID": 12, "FWD": 8})
    picked = nr.ai_callups(players)
    assert len(picked) == 23 == len(set(picked))
    assert positions_of(picked, players) == {"GK": 3, "DEF": 8, "MID": 7, "FWD": 5}
    by_id = {p.id: p for p in players}
    for pos, quota in nr.BASE_QUOTAS.items():
        best = sorted((p for p in players if p.position.value == pos), key=lambda p: -p.overall_rating)[:quota]
        assert {p.id for p in best} <= set(picked)
    ranks = [nr.POSITION_ORDER.index(by_id[i].position.value) for i in picked]
    assert ranks == sorted(ranks), "donus GK, DEF, MID, FWD sirasinda"


def test_ai_callups_limits():
    players = squad({"GK": 6, "DEF": 15, "MID": 15, "FWD": 10})
    assert positions_of(nr.ai_callups(players, 30), players) == {"GK": 4, "DEF": 10, "MID": 9, "FWD": 7}
    assert len(nr.ai_callups(players, 50)) == nr.MAX_CALLUPS
    assert positions_of(nr.ai_callups(players, 16), players) == {"GK": 2, "DEF": 6, "MID": 5, "FWD": 3}
    assert nr.ai_callups(players, 0) == []
    assert nr.ai_callups([], 23) == []


def test_ai_callups_excludes_injured_players():
    players = squad({"GK": 4, "DEF": 10, "MID": 9, "FWD": 6})
    star_def = next(p for p in players if p.position is Position.DEF)
    star_def.injured_until_week = 12
    assert star_def.id in nr.ai_callups(players)                 # hafta verilmezse bilinemez
    assert star_def.id not in nr.ai_callups(players, week=10)    # 12. haftaya kadar sakat
    assert star_def.id in nr.ai_callups(players, week=12)        # dondu

    @dataclass
    class Flagged:
        id: int
        position: str
        overall_rating: int
        injured: bool = False
        injury_weeks: int = 0

    pool = [Flagged(1, "GK", 90, injured=True), Flagged(2, "GK", 80, injury_weeks=2), Flagged(3, "GK", 70)]
    assert nr.ai_callups(pool, 1) == [3]


def test_ai_callups_ignores_duplicates_and_fills_shortfalls():
    players = squad({"GK": 2, "DEF": 3, "MID": 20, "FWD": 2})
    doubled = players + players[:5]
    picked = nr.ai_callups(doubled)
    assert len(picked) == 23 == len(set(picked))
    counts = positions_of(picked, players)
    # kalecisi ve defansi az: tum kaleciler/defanslar/forvetler alinir, bosluk en iyi ortasahalarla dolar
    assert counts == {"GK": 2, "DEF": 3, "MID": 16, "FWD": 2}


def test_ai_callups_uses_keepers_only_when_outfielders_run_out():
    players = squad({"GK": 10, "DEF": 4, "MID": 4, "FWD": 3})
    picked = nr.ai_callups(players)
    assert len(picked) == 21 and positions_of(picked, players)["GK"] == 10
    few = squad({"GK": 8, "DEF": 9, "MID": 8, "FWD": 6})
    assert positions_of(nr.ai_callups(few), few)["GK"] == 3
    # 20 kota yeri dolar; 3 bos yer: once kalan tek forvet (guc 75), sonra en iyi iki yedek kaleci (77, 76)
    short = squad({"GK": 8, "DEF": 6, "MID": 6, "FWD": 6})
    assert positions_of(nr.ai_callups(short), short) == {"GK": 5, "DEF": 6, "MID": 6, "FWD": 6}


def test_ai_callups_tiebreak_younger_then_id_and_bare_players():
    same = [FakePlayer(5, Position.FWD, 70, age=30), FakePlayer(3, Position.FWD, 70, age=22),
            FakePlayer(4, Position.FWD, 70, age=22), FakePlayer(9, Position.GK, 60)]
    assert nr.ai_callups(same, 2) == [9, 3]
    bare = [BarePlayer(i, pos, 60 + i) for i, pos in enumerate(["GK", "DEF", "MID", "FWD"], start=1)]
    assert nr.ai_callups(bare, 4) == [1, 2, 3, 4]


def test_validate_callups():
    eligible = list(range(1, 41))
    assert nr.validate_callups(list(range(1, 24)), eligible) == []
    errors = nr.validate_callups([1, 1, 2, 99] + list(range(3, 40)), eligible)
    assert any("birden fazla" in e for e in errors)
    assert any("[99]" in e for e in errors)
    assert any("en fazla 30" in e for e in errors)
    assert any("en az 16" in e for e in nr.validate_callups(list(range(1, 16)), eligible))
    assert nr.validate_callups([1, 2, 3], [1, 2, 3]) == []      # havuz 16'dan kucukse hepsi yeter


# ===========================================================================
# Is teklifi ve sozlesme
# ===========================================================================

def test_rank_pct_and_required_level_bands():
    assert nr.rank_pct(1, 20) == 0.05
    assert nr.rank_pct(20, 20) == 1.0
    for bad in ((0, 20), (21, 20), (1, 0)):
        with pytest.raises(ValueError):
            nr.rank_pct(*bad)
    assert [nr.required_level(p) for p in (0.0, 0.10, 0.1001, 0.25, 0.26, 0.50, 0.51, 1.0, 5.0)] == [
        7, 7, 5, 5, 3, 3, 1, 1, 1,
    ]
    with pytest.raises(ValueError):
        nr.required_level(float("nan"))


def test_job_offer_eligibility_edges():
    assert nr.job_offer_eligible(7, 0.10)
    assert not nr.job_offer_eligible(6, 0.10)
    assert nr.job_offer_eligible(5, 0.25) and not nr.job_offer_eligible(4, 0.25)
    assert nr.job_offer_eligible(3, 0.5) and not nr.job_offer_eligible(2, 0.5)
    assert nr.job_offer_eligible(1, 0.51)
    assert nr.job_offer_eligible(0, 0.9)                         # seviye 1'e kirpilir
    assert nr.job_offer_eligible(15, 0.0)                        # seviye 10'a kirpilir
    new_manager = reputation.level(reputation.START_REPUTATION).level
    assert nr.job_offer_eligible(new_manager, nr.rank_pct(6, 6))
    assert not nr.job_offer_eligible(new_manager, nr.rank_pct(1, 6))


def test_contract_until_caps_at_two_seasons():
    assert nr.contract_until(3) == 4
    assert nr.contract_until(3, 1) == 3
    assert nr.contract_until(3, 5) == 4
    assert nr.contract_until(3, 0) == 3


# ===========================================================================
# Beklenti ve gorevden alma
# ===========================================================================

@pytest.mark.parametrize(
    ("rank", "size", "stage"),
    [
        (1, 32, "SF"), (2, 32, "SF"), (3, 32, "R16"), (8, 32, "R16"), (9, 32, "GROUP"), (32, 32, "GROUP"),
        (33, 32, "QUAL"),
        (1, 16, "SF"), (2, 16, "QF"), (4, 16, "QF"), (5, 16, "GROUP"),
        (1, 8, "SF"), (2, 8, "SF"), (3, 8, "GROUP"),
        (1, 4, "FINAL"), (2, 4, "GROUP"), (5, 4, "QUAL"),
    ],
)
def test_expected_stage(rank, size, stage):
    assert nr.expected_stage(rank, size) == stage


def test_expected_stage_invalid():
    with pytest.raises(ValueError):
        nr.expected_stage(1, 6)
    with pytest.raises(ValueError):
        nr.expected_stage(0, 32)


def test_sack_decision_rules():
    kept, reason = nr.sack_decision("GROUP", "GROUP", 0.1)
    assert not kept and "Hedef tuttu" in reason
    kept, reason = nr.sack_decision("QUAL", "SF", 0.9)
    assert not kept and "aşıldı" in reason and "Yarı Final" in reason

    sacked, reason = nr.sack_decision("SF", "GROUP", 0.9)
    assert sacked and "Yarı Final" in reason and "Grup Aşaması" in reason

    sacked, reason = nr.sack_decision("GROUP", "QUAL", 0.39)
    assert sacked and "%39" in reason and "Elemeler" in reason
    kept, reason = nr.sack_decision("GROUP", "QUAL", 0.40)
    assert not kept and "%40" in reason
    assert nr.sack_decision("R16", "group", 2.0)[0] is False     # oran 1.0'a kirpilir, kucuk harf kabul
    assert nr.sack_decision("CHAMPION", "FINAL", 0.0)[0] is True


def test_sack_decision_invalid_input():
    with pytest.raises(ValueError):
        nr.sack_decision("LAST8", "GROUP", 0.5)
    with pytest.raises(ValueError):
        nr.sack_decision("GROUP", "GROUP", float("nan"))


# ===========================================================================
# Taninirlik
# ===========================================================================

def test_match_deltas_share_the_club_scale():
    assert nr.intl_reputation_delta("W", None, False) == reputation.MATCH_DELTA["W"]
    assert nr.intl_reputation_delta("W", "QUAL", False) == 0.12
    assert nr.intl_reputation_delta("D", None, False) == 0.02
    assert nr.intl_reputation_delta("W", "GROUP", False) == 0.15
    assert nr.intl_reputation_delta("D", "GROUP", False) == 0.025
    for stage in (None, "GROUP", "R16", "SF"):
        assert nr.intl_reputation_delta("L", stage, False) == reputation.MATCH_DELTA["L"]


def test_stage_progress_and_final_bonuses():
    assert nr.intl_reputation_delta("Q", "QUAL", False) == nr.WORLD_CUP_QUALIFIED == 0.5
    assert nr.intl_reputation_delta("Q", "GROUP", False) == reputation.CUP_ROUND_WON["GROUP"]
    assert nr.intl_reputation_delta("W", "R16", False) == pytest.approx(0.18 + 0.4)
    assert nr.intl_reputation_delta("W", "QF", False) == pytest.approx(0.18 + 0.7)
    assert nr.intl_reputation_delta("W", "SF", False) == pytest.approx(0.21 + 1.0)
    assert nr.intl_reputation_delta("W", "FINAL", True) == pytest.approx(0.24 + 3.0)
    assert nr.intl_reputation_delta("L", "FINAL", False) == pytest.approx(-0.08 + 0.8)
    assert nr.WORLD_CUP_CHAMPION > reputation.CUP_CHAMPION > reputation.SEASON_CHAMPION


def test_world_cup_winning_run_is_big_but_bounded():
    run = [("W", "GROUP"), ("W", "GROUP"), ("D", "GROUP")]
    total = sum(nr.intl_reputation_delta(o, s, False) for o, s in run)
    total += nr.intl_reputation_delta("Q", "GROUP", False)
    total += sum(nr.intl_reputation_delta("W", s, False) for s in ("R16", "QF", "SF"))
    total += nr.intl_reputation_delta("W", "FINAL", True)
    assert 5.5 < total < 7.0
    assert reputation.apply(19.0, total) == reputation.MAX_REPUTATION


@pytest.mark.parametrize(
    ("outcome", "stage", "won"),
    [
        ("D", "QF", False),          # eleme maci berabere kaydedilemez
        ("D", "FINAL", False),
        ("W", "FINAL", False),       # final galibiyeti = sampiyonluk
        ("W", "SF", True),           # sampiyonluk yalnizca finalde
        ("L", "FINAL", True),
        ("Q", "R16", False),         # eleme turunda tur atlama W ile
        ("Q", "QUAL", True),
        ("X", None, False),
        ("W", "CHAMPION", False),
        ("W", "LAST8", False),
    ],
)
def test_inconsistent_reputation_inputs_raise(outcome, stage, won):
    with pytest.raises(ValueError):
        nr.intl_reputation_delta(outcome, stage, won)
