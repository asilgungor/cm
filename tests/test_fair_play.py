"""
Adil oyun denetimi testleri (Faz 12 / 14. Asama, 12B): fair_play.py SAF modul, DB gerekmez.

    1) her puan etkeni tek basina (sinirlariyla) ve birlikte
    2) esikler: denetim seviyesi ve dusuk adil oyun puani
    3) ALLOW / REVIEW / BLOCK sinirlari, modul basligindaki calisilmis ornekler
    4) adil oyun puani degisimleri ve haftalik toparlanma
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fair_play as fp  # noqa: E402
from fair_play import DealFacts, Decision, evaluate, thresholds  # noqa: E402
from market_rules import OfferKind  # noqa: E402
from world_rules import STRICTNESS_LEVELS, WorldRules  # noqa: E402

M = 1_000_000

# Temiz anlasma: 10M degerinde oyuncu tam bedelle, eski hesaplar, tekrar yok
CLEAN = DealFacts(
    kind="TRANSFER", fee=10 * M, player_value=10 * M, player_overall=76, player_age=25, exchange_value=0,
    loan_wage_share=None, loan_weeks=None, buyer_seat_id=1, seller_seat_id=2,
    buyer_account_age_days=400, seller_account_age_days=400, buyer_seat_weeks=40, seller_seat_weeks=40,
    pair_deals_recent=0, buyer_deals_recent=0, seller_deals_recent=0, buyer_fair_play=100.0, seller_fair_play=100.0,
)
LOAN = replace(CLEAN, kind="LOAN", fee=0, player_value=20 * M, loan_wage_share=100, loan_weeks=10)


def score_flags(facts: DealFacts, strictness: str = "MEDIUM") -> tuple[float, tuple[str, ...]]:
    verdict = evaluate(facts, strictness)
    return verdict.score, verdict.flags


def test_clean_deal_is_allowed_without_reasons():
    for facts in (CLEAN, LOAN):
        verdict = evaluate(facts)
        assert verdict == fp.FairnessVerdict(0.0, Decision.ALLOW, (), (), 30.0, 60.0)
        assert verdict.label == "Uygun"


# ===========================================================================
# 1) ETKENLER TEK BASINA
# ===========================================================================

@pytest.mark.parametrize(("changes", "score", "flags"), [
    # ucuz satis: (fark - 0.35) x 100
    ({"fee": 9 * M}, 0.0, ()),
    ({"fee": 6_500_000}, 0.0, ()),                                   # fark tam 0.35: serbest
    ({"fee": 6_490_000}, 0.1, ("VALUE_UNDERPRICED",)),
    ({"fee": 5 * M}, 15.0, ("VALUE_UNDERPRICED",)),
    ({"fee": 0}, 65.0, ("VALUE_UNDERPRICED",)),
    ({"fee": -5 * M}, 65.0, ("VALUE_UNDERPRICED",)),                 # eksi bedel 0 sayilir
    # pahali satis: (fark - 0.35) x 50, en fazla 100
    ({"fee": 13_500_000}, 0.0, ()),
    ({"fee": 16 * M}, 12.5, ("VALUE_OVERPRICED",)),
    ({"fee": 20 * M}, 32.5, ("VALUE_OVERPRICED",)),
    ({"fee": 30 * M}, 82.5, ("VALUE_OVERPRICED",)),
    ({"fee": 50 * M}, 100.0, ("VALUE_OVERPRICED",)),
    # takas degeri karsiliga eklenir
    ({"fee": 6 * M, "exchange_value": 3 * M}, 0.0, ()),
    ({"fee": 0, "exchange_value": 10 * M}, 0.0, ()),
    ({"fee": 0, "exchange_value": 5 * M}, 15.0, ("VALUE_UNDERPRICED",)),
    ({"fee": 10 * M, "exchange_value": 10 * M}, 32.5, ("VALUE_OVERPRICED",)),
    # mutlak fark 250K altinda sayilmaz; deger tabani 10K
    ({"fee": 0, "player_value": 200_000}, 0.0, ()),
    ({"fee": 260_000, "player_value": 500_000}, 0.0, ()),
    ({"fee": 250_000, "player_value": 500_000}, 15.0, ("VALUE_UNDERPRICED",)),
    ({"fee": 0, "player_value": 0}, 0.0, ()),
    ({"fee": M, "player_value": 0}, 100.0, ("VALUE_OVERPRICED",)),
    # ayni ikili: onceki her anlasma +15
    ({"pair_deals_recent": 1}, 15.0, ("REPEATED_PAIR",)),
    ({"pair_deals_recent": 3}, 45.0, ("REPEATED_PAIR",)),
    ({"pair_deals_recent": -2}, 0.0, ()),
    # yeni hesap (<7 gun) ya da yeni koltuk (<2 hafta): taraf basina bir kez +15
    ({"buyer_account_age_days": 6}, 15.0, ("NEW_BUYER",)),
    ({"buyer_account_age_days": 7}, 0.0, ()),
    ({"buyer_seat_weeks": 1}, 15.0, ("NEW_BUYER",)),
    ({"buyer_seat_weeks": 2}, 0.0, ()),
    ({"buyer_account_age_days": 0, "buyer_seat_weeks": 0}, 15.0, ("NEW_BUYER",)),
    ({"seller_account_age_days": 6}, 15.0, ("NEW_SELLER",)),
    ({"seller_seat_weeks": 1}, 15.0, ("NEW_SELLER",)),
    ({"buyer_seat_weeks": 0, "seller_account_age_days": 1}, 30.0, ("NEW_BUYER", "NEW_SELLER")),
    # 4 haftada bu anlasmayla 3'ten fazla: bir kez +10
    ({"buyer_deals_recent": 2}, 0.0, ()),
    ({"buyer_deals_recent": 3}, 10.0, ("BURST",)),
    ({"seller_deals_recent": 3}, 10.0, ("BURST",)),
    ({"buyer_deals_recent": 5, "seller_deals_recent": 9}, 10.0, ("BURST",)),
    # ayni koltuk
    ({"seller_seat_id": 1}, 100.0, ("SAME_SEAT",)),
])
def test_transfer_factors_in_isolation(changes, score, flags):
    assert score_flags(replace(CLEAN, **changes)) == (score, flags)


@pytest.mark.parametrize(("changes", "score", "flags"), [
    # kiralikta deger dengesizligi yok; yuksek kiralik bedeli (bedel / deger - 0.35) x 50
    ({"fee": 0, "player_value": 50 * M}, 0.0, ()),
    ({"fee": 7 * M}, 0.0, ()),                                        # 20M icin oran 0.35
    ({"fee": 10 * M}, 7.5, ("LOAN_FEE_HIGH",)),
    ({"fee": 240_000, "player_value": 10_000}, 0.0, ()),              # bedel 250K altinda
    # degerli oyuncu (>= 5M ya da guc >= 80) %20 altinda maas payiyla +10
    ({"loan_wage_share": 19}, 10.0, ("CHEAP_LOAN",)),
    ({"loan_wage_share": 0}, 10.0, ("CHEAP_LOAN",)),
    ({"loan_wage_share": 20}, 0.0, ()),
    ({"loan_wage_share": None}, 0.0, ()),                             # None: tam maas
    ({"loan_wage_share": 10, "player_value": 4_990_000, "player_overall": 79}, 0.0, ()),
    ({"loan_wage_share": 10, "player_value": 5 * M, "player_overall": 60}, 10.0, ("CHEAP_LOAN",)),
    ({"loan_wage_share": 10, "player_value": M, "player_overall": 80}, 10.0, ("CHEAP_LOAN",)),
    ({"kind": OfferKind.LOAN, "loan_wage_share": 5}, 10.0, ("CHEAP_LOAN",)),
    ({"kind": "loan", "loan_wage_share": 5}, 10.0, ("CHEAP_LOAN",)),
])
def test_loan_factors_in_isolation(changes, score, flags):
    assert score_flags(replace(LOAN, **changes)) == (score, flags)


def test_transfer_ignores_loan_only_factors():
    assert score_flags(replace(CLEAN, loan_wage_share=0, player_value=10 * M)) == (0.0, ())
    assert score_flags(replace(CLEAN, kind=OfferKind.TRANSFER, fee=10 * M)) == (0.0, ())


def test_reasons_are_turkish_and_aligned_with_flags():
    facts = replace(LOAN, fee=15 * M, loan_wage_share=10, pair_deals_recent=2, buyer_account_age_days=3,
                    seller_seat_weeks=1, buyer_deals_recent=3, seller_seat_id=2, seller_fair_play=60)
    verdict = evaluate(facts)
    assert verdict.flags == ("LOAN_FEE_HIGH", "REPEATED_PAIR", "NEW_BUYER", "NEW_SELLER", "BURST", "CHEAP_LOAN",
                             "LOW_FAIR_PLAY")
    assert len(verdict.reasons) == len(verdict.flags) and set(verdict.flags) <= set(fp.FLAGS)
    assert verdict.reasons == (
        "Kiralık bedeli oyuncu değerine göre çok yüksek: 15.0M EUR (değer 20.0M EUR).",
        "Aynı iki menajer son 20 haftada 2 kez daha anlaştı.",
        "Alıcı menajer yeni (hesap 3 günlük, koltuk 40 haftalık).",
        "Satıcı menajer yeni (hesap 400 günlük, koltuk 1 haftalık).",
        "Son 4 haftada çok sayıda menajerler arası anlaşma (bu anlaşmayla alıcı 4, satıcı 1).",
        "Değerli oyuncu çok düşük maaş payıyla kiralanıyor (%10).",
        "Adil oyun puanı düşük (60); denetim eşikleri sertleşti.",
    )
    # 20 + 30 + 15 + 15 + 10 + 10 = 100; esikler adil oyun 60 -> (24, 48)
    assert (verdict.score, verdict.decision, verdict.review_at, verdict.block_at) == (100.0, Decision.BLOCK, 24.0, 48.0)
    assert evaluate(facts) == verdict                                     # deterministik
    under = evaluate(replace(CLEAN, fee=3 * M))
    assert under.reasons == ("Bedel oyuncu değerinin çok altında: 3.0M EUR karşılığında 10.0M EUR değerinde oyuncu "
                             "(%70 eksik).",)
    over = evaluate(replace(CLEAN, fee=20 * M))
    assert over.reasons == ("Bedel oyuncu değerinin çok üstünde: 20.0M EUR karşılığında 10.0M EUR değerinde oyuncu "
                            "(%100 fazla).",)
    assert evaluate(replace(CLEAN, seller_seat_id=1)).reasons == ("Alıcı ve satıcı aynı menajer.",)


def test_low_fair_play_note_adds_no_points():
    verdict = evaluate(replace(CLEAN, buyer_fair_play=79.9))
    assert verdict.score == 0.0 and verdict.flags == ("LOW_FAIR_PLAY",) and verdict.decision is Decision.ALLOW
    assert evaluate(replace(CLEAN, seller_fair_play=80.0)).flags == ()


# ===========================================================================
# 2) ESIKLER
# ===========================================================================

def test_thresholds_per_strictness():
    assert thresholds("LOW", 100) == (45.0, 75.0)
    assert thresholds("MEDIUM", 100) == (30.0, 60.0)
    assert thresholds("HIGH", 100) == (20.0, 45.0)
    assert set(STRICTNESS_LEVELS) == set(fp.BASE_THRESHOLDS)
    assert thresholds(WorldRules().fairness_strictness, 100) == (30.0, 60.0)
    # tolerans: kucuk harf ve bilinmeyen seviye (MEDIUM)
    assert thresholds("high", 100) == (20.0, 45.0) and thresholds(" low ", 100) == (45.0, 75.0)
    assert thresholds("EXTREME", 100) == thresholds(None, 100) == (30.0, 60.0)


@pytest.mark.parametrize(("strictness", "fair_play", "expected"), [
    ("MEDIUM", 90, (28.5, 57.0)),
    ("MEDIUM", 75, (26.25, 52.5)),
    ("MEDIUM", 50, (22.5, 45.0)),
    ("MEDIUM", 0, (15.0, 30.0)),
    ("LOW", 50, (33.75, 56.25)),
    ("LOW", 0, (22.5, 37.5)),
    ("HIGH", 50, (15.0, 33.75)),
    ("HIGH", 0, (10.0, 22.5)),
    ("MEDIUM", 150, (30.0, 60.0)),                  # 0-100'e kirpilir
    ("MEDIUM", -20, (15.0, 30.0)),
])
def test_thresholds_tighten_with_fair_play(strictness, fair_play, expected):
    assert thresholds(strictness, fair_play) == expected


def test_thresholds_are_monotonic_and_ordered():
    for level in STRICTNESS_LEVELS:
        previous = None
        for score in range(0, 101, 5):
            review, block = thresholds(level, score)
            assert 0 < review < block
            if previous is not None:
                assert review >= previous[0] and block >= previous[1]
            previous = (review, block)
    for score in range(0, 101, 10):
        assert thresholds("HIGH", score) < thresholds("MEDIUM", score) < thresholds("LOW", score)


def test_lower_fair_play_side_sets_thresholds():
    buyer_low = evaluate(replace(CLEAN, buyer_fair_play=40, seller_fair_play=100))
    seller_low = evaluate(replace(CLEAN, buyer_fair_play=100, seller_fair_play=40))
    assert (buyer_low.review_at, buyer_low.block_at) == (seller_low.review_at, seller_low.block_at) == (21.0, 42.0)


# ===========================================================================
# 3) KARAR SINIRLARI VE CALISILMIS ORNEKLER
# ===========================================================================

@pytest.mark.parametrize(("strictness", "changes", "score", "decision"), [
    ("MEDIUM", {"fee": 3_510_000}, 29.9, Decision.ALLOW),
    ("MEDIUM", {"fee": 3_500_000}, 30.0, Decision.REVIEW),
    ("MEDIUM", {"pair_deals_recent": 2}, 30.0, Decision.REVIEW),
    ("MEDIUM", {"fee": 510_000}, 59.9, Decision.REVIEW),
    ("MEDIUM", {"fee": 500_000}, 60.0, Decision.BLOCK),
    ("MEDIUM", {"pair_deals_recent": 4}, 60.0, Decision.BLOCK),
    ("MEDIUM", {"pair_deals_recent": 1, "buyer_deals_recent": 3}, 25.0, Decision.ALLOW),
    ("LOW", {"fee": 2_000_000}, 45.0, Decision.REVIEW),
    ("LOW", {"fee": 2_010_000}, 44.9, Decision.ALLOW),
    ("LOW", {"pair_deals_recent": 5}, 75.0, Decision.BLOCK),
    ("HIGH", {"fee": 4_500_000}, 20.0, Decision.REVIEW),
    ("HIGH", {"fee": 4_510_000}, 19.9, Decision.ALLOW),
    ("HIGH", {"pair_deals_recent": 3}, 45.0, Decision.BLOCK),
    # dusuk adil oyun esikleri kaydirir (MEDIUM, 50 -> 22.5 / 45)
    ("MEDIUM", {"pair_deals_recent": 1, "seller_fair_play": 50}, 15.0, Decision.ALLOW),
    ("MEDIUM", {"fee": 7_750_000, "pair_deals_recent": 1, "seller_fair_play": 50}, 15.0, Decision.ALLOW),
    ("MEDIUM", {"pair_deals_recent": 2, "seller_fair_play": 50}, 30.0, Decision.REVIEW),
    ("MEDIUM", {"pair_deals_recent": 3, "buyer_fair_play": 50}, 45.0, Decision.BLOCK),
    ("MEDIUM", {"pair_deals_recent": 1, "buyer_fair_play": 0}, 15.0, Decision.REVIEW),
])
def test_decision_boundaries(strictness, changes, score, decision):
    verdict = evaluate(replace(CLEAN, **changes), strictness)
    assert (verdict.score, verdict.decision) == (score, decision)


@pytest.mark.parametrize(("strictness", "facts", "score", "decision"), [
    ("MEDIUM", replace(CLEAN, fee=9 * M), 0.0, Decision.ALLOW),
    ("MEDIUM", replace(CLEAN, fee=5 * M), 15.0, Decision.ALLOW),
    ("MEDIUM", replace(CLEAN, fee=3 * M), 35.0, Decision.REVIEW),
    ("MEDIUM", replace(CLEAN, fee=0), 65.0, Decision.BLOCK),
    ("MEDIUM", replace(CLEAN, fee=16 * M), 12.5, Decision.ALLOW),
    ("MEDIUM", replace(CLEAN, fee=20 * M), 32.5, Decision.REVIEW),
    ("MEDIUM", replace(CLEAN, fee=30 * M), 82.5, Decision.BLOCK),
    ("MEDIUM", replace(CLEAN, fee=6 * M, exchange_value=3 * M), 0.0, Decision.ALLOW),
    ("MEDIUM", replace(CLEAN, fee=0, player_value=200_000), 0.0, Decision.ALLOW),
    ("MEDIUM", replace(CLEAN, buyer_account_age_days=3, pair_deals_recent=1), 30.0, Decision.REVIEW),
    ("MEDIUM", replace(LOAN, loan_wage_share=10), 10.0, Decision.ALLOW),
    ("MEDIUM", replace(LOAN, loan_wage_share=10, pair_deals_recent=2, seller_fair_play=50), 40.0, Decision.REVIEW),
    ("HIGH", replace(CLEAN, fee=4_500_000), 20.0, Decision.REVIEW),
    ("HIGH", replace(CLEAN, fee=2 * M), 45.0, Decision.BLOCK),
    ("LOW", replace(CLEAN, fee=0), 65.0, Decision.REVIEW),
    ("LOW", replace(CLEAN, fee=0, seller_seat_weeks=1), 80.0, Decision.BLOCK),
])
def test_docstring_worked_examples(strictness, facts, score, decision):
    verdict = evaluate(facts, strictness)
    assert (verdict.score, verdict.decision) == (score, decision)


def test_same_inputs_same_verdict_across_strictness_levels():
    facts = replace(CLEAN, fee=4 * M, pair_deals_recent=1)                 # 25 + 15 = 40
    verdicts = {level: evaluate(facts, level) for level in STRICTNESS_LEVELS}
    assert {v.score for v in verdicts.values()} == {40.0}
    assert [verdicts[level].decision for level in ("LOW", "MEDIUM", "HIGH")] == [
        Decision.ALLOW, Decision.REVIEW, Decision.REVIEW]
    assert evaluate(facts) == verdicts["MEDIUM"]                          # varsayilan MEDIUM
    assert {d: fp.DECISION_LABELS[d] for d in Decision} == {
        Decision.ALLOW: "Uygun", Decision.REVIEW: "Yönetici incelemesi gerekiyor", Decision.BLOCK: "Engellendi"}


# ===========================================================================
# 4) ADIL OYUN PUANI
# ===========================================================================

def test_fair_play_deltas_and_reasons():
    assert dict(fp.FAIR_PLAY_DELTAS) == {"BLOCKED": -10.0, "DENIED": -15.0, "APPROVED": 2.0, "REVERSED": -25.0}
    assert set(fp.FAIR_PLAY_REASONS) == set(fp.FAIR_PLAY_DELTAS) | {"RECOVERY"}
    assert all(0 < len(text) <= 120 for text in fp.FAIR_PLAY_REASONS.values())      # fair_play_log.reason
    with pytest.raises(TypeError):
        fp.FAIR_PLAY_DELTAS["BLOCKED"] = 0.0


@pytest.mark.parametrize(("score", "expected"), [
    (100.0, 0.0), (120.0, 0.0), (99.5, 0.5), (99.0, 1.0), (75.0, 1.0), (0.0, 1.0), (-5.0, 1.0),
])
def test_weekly_recovery(score, expected):
    assert fp.weekly_recovery(score) == expected


def test_apply_fair_play_and_recovery_timeline():
    assert fp.apply_fair_play(95, 10) == 100.0
    assert fp.apply_fair_play(5, fp.FAIR_PLAY_DELTAS["BLOCKED"]) == 0.0
    assert fp.apply_fair_play(90, fp.FAIR_PLAY_DELTAS["REVERSED"]) == 65.0
    assert fp.apply_fair_play(99.5, fp.FAIR_PLAY_DELTAS["APPROVED"]) == 100.0
    score, weeks = fp.apply_fair_play(100, fp.FAIR_PLAY_DELTAS["REVERSED"]), 0
    while fp.weekly_recovery(score) > 0:
        score, weeks = fp.apply_fair_play(score, fp.weekly_recovery(score)), weeks + 1
    assert (score, weeks) == (100.0, 25)                                  # iade cezasi 25 haftada kapanir
