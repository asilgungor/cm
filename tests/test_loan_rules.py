"""
Kiralik kurallari testleri (Faz 12 / 14. Asama, 12B): loan_rules.py SAF modul, DB gerekmez.

    maas paylasimi (asagi yuvarlama, %0 / %100, tek sayili maaslar), AI kiraliga verme / alma sinirlari,
    kiralik bitis haftasi, erken geri cagirma kosullari, para bicimi.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import loan_rules as lr  # noqa: E402
from loan_rules import (  # noqa: E402
    ai_accepts_loan_in,
    ai_accepts_loan_out,
    loan_end_week,
    recall_allowed,
    wage_split,
)

# ===========================================================================
# 1) MAAS PAYLASIMI
# ===========================================================================


@pytest.mark.parametrize(("wage", "share", "expected"), [
    (0, 50, (0, 0)),
    (1, 50, (0, 1)),                  # kiralayan payi asagi yuvarlanir, kalan ana kulube
    (1, 100, (1, 0)),
    (99, 1, (0, 99)),
    (100, 1, (1, 99)),
    (999, 50, (499, 500)),
    (1_001, 50, (500, 501)),
    (12_345, 33, (4_073, 8_272)),
    (12_345, 0, (0, 12_345)),
    (12_345, 100, (12_345, 0)),
    (250_000, 150, (250_000, 0)),     # yuzde 0-100'e kirpilir
    (250_000, -5, (0, 250_000)),
    (40_001, 99, (39_600, 401)),
])
def test_wage_split_rounds_borrower_down(wage, share, expected):
    assert wage_split(wage, share) == expected


def test_wage_split_never_creates_or_loses_money():
    for wage in (0, 1, 3, 7, 101, 12_347, 99_999, 1_000_001):
        for share in range(-10, 111, 7):
            borrower, parent = wage_split(wage, share)
            assert borrower + parent == wage and 0 <= borrower <= wage and parent >= 0
    assert wage_split("12345", "33") == (4_073, 8_272)          # sayiya cevrilir


def test_format_money_matches_finance():
    import finance

    for amount in (0, 999, 1_000, 24_999, 25_000, 850_000, 999_999, 1_000_000, 12_500_000, -1_200_000, -10):
        assert lr.format_money(amount) == finance.format_money(amount)


# ===========================================================================
# 2) AI KIRALIGA VERIR MI
# ===========================================================================

def test_loan_out_constants_match_career_rules():
    import career_manager

    assert lr.AI_LOAN_OUT_MIN_SQUAD == career_manager.AI_MIN_SENIOR_SQUAD


@pytest.mark.parametrize(("args", "accepted", "message"), [
    # (guc, sira, kadro, pay, hafta)
    ((68, 18, 24, 50, 10), True, "Kulüp kiralığa onay verdi (maaşın %50 payı kiralayan kulüpte)."),
    ((68, 12, 24, 75, None), True, "onay verdi"),
    ((68, 11, 24, 100, None), False, "Kulüp ilk 11 oyuncusunu kiralığa vermez."),
    ((68, 1, 24, 100, None), False, "ilk 11"),
    ((68, 11, 10, 100, 1), False, "ilk 11"),                 # sira denetimi once gelir
    ((68, 18, 17, 50, None), True, "onay verdi"),             # kiralik sonrasi tam 16
    ((68, 18, 16, 50, None), False, "Kulübün A takım kadrosu 16 oyuncunun altına düşer; kiralık verilmez."),
    ((68, 18, 16, 0, 1), False, "16 oyuncunun altına"),       # kadro denetimi sureden once
    ((68, 18, 24, 50, 6), True, "onay verdi"),
    ((68, 18, 24, 50, 5), False, "Kulüp en az 6 haftalık ya da sezon sonuna kadar kiralık istiyor."),
    ((68, 18, 24, 0, 5), False, "en az 6 haftalık"),          # sure denetimi paydan once
    ((68, 17, 24, 50, None), True, "onay verdi"),             # yedek: %50 yeter
    ((68, 17, 24, 49, None), False, "Kulüp maaş payının en az %50 olmasını istiyor (teklif: %49)."),
    ((68, 16, 24, 75, None), True, "onay verdi"),             # rotasyon: %75
    ((68, 16, 24, 74, None), False, "en az %75 olmasını istiyor (teklif: %74)"),
    ((80, 20, 24, 100, None), True, "onay verdi"),            # degerli oyuncu: %100
    ((80, 20, 24, 99, None), False, "en az %100 olmasını istiyor (teklif: %99)"),
    ((79, 20, 24, 50, None), True, "onay verdi"),
    ((68, 18, 24, 150, None), True, "%100 payı"),             # pay kirpilir
    ((68, 18, 24, -5, None), False, "(teklif: %0)"),
])
def test_ai_accepts_loan_out(args, accepted, message):
    ok, reason = ai_accepts_loan_out(*args)
    assert ok is accepted
    assert message in reason


def test_loan_out_required_share_tiers():
    assert lr.loan_out_required_share(90, 30) == 100
    assert lr.loan_out_required_share(79, 16) == 75
    assert lr.loan_out_required_share(79, 17) == 50


# ===========================================================================
# 3) AI KIRALIK ALIR MI
# ===========================================================================

@pytest.mark.parametrize(("args", "accepted", "message"), [
    # (guc, mevki ortalamasi, bos maas alani, maas, pay)
    ((70, 70.0, 100_000, 30_000, 50), True, "Kulüp kiralık teklifini kabul etti."),
    ((69, 70.0, 100_000, 30_000, 50), False, "Kulübün bu mevkide daha iyi oyuncuları var; kiralık almak istemiyor."),
    ((68, 70.0, 100_000, 30_000, 0), False, "daha iyi oyuncuları var"),
    ((72, 70.0, 100_000, 30_000, 51), False,
     "Kulüp maaşın %51 payı için mevkisinde belirgin bir güçlenme istiyor (en az +3)."),
    ((73, 70.0, 100_000, 30_000, 51), True, "kabul etti"),
    ((69, 70.0, 100_000, 30_000, 100), False, "daha iyi oyuncuları var"),   # guclenme hic yoksa asil neden
    ((73, 70.4, 100_000, 30_000, 100), False, "belirgin bir güçlenme"),      # +2.6 < +3
    ((40, None, 100_000, 30_000, 100), True, "kabul etti"),                  # mevkide oyuncu yok: ihtiyac
    ((74, 70.0, 30_000, 30_000, 100), True, "kabul etti"),                   # pay tam bos alan kadar
    ((74, 70.0, 29_999, 30_000, 100), False,
     "Kulübün maaş bütçesi yetmiyor: haftalık pay 30K EUR, boş alan 30K EUR."),
    ((74, 70.0, 15_000, 30_001, 50), True, "kabul etti"),                    # 15.000,5 asagi yuvarlanir
    ((74, 70.0, 14_999, 30_001, 50), False, "haftalık pay 15K EUR"),
    ((70, 70.0, -50_000, 30_000, 0), True, "kabul etti"),                    # pay 0: maas alani onemsiz
    ((74, 70.0, -50_000, 30_000, 100), False, "boş alan 0 EUR"),
    ((70, 70.0, 100_000, 30_000, 150), False, "%100 payı"),                  # pay kirpilir -> +3 ister
])
def test_ai_accepts_loan_in(args, accepted, message):
    ok, reason = ai_accepts_loan_in(*args)
    assert ok is accepted
    assert message in reason


# ===========================================================================
# 4) BITIS HAFTASI VE GERI CAGIRMA
# ===========================================================================

@pytest.mark.parametrize(("start", "weeks", "season_end", "expected"), [
    (10, 4, 30, 14),
    (10, 1, 30, 11),
    (10, 20, 30, 30),                 # tam sezon sonu
    (10, 21, 30, 30),                 # sezon sonunu asamaz
    (10, None, 30, 30),               # NULL sure: sezon sonu
    (28, 4, 30, 30),
    (30, None, 30, 30),
    (31, 4, 30, 31),                  # sezon sonu gecmis: baslangictan once bitmez
    (31, None, 30, 31),
])
def test_loan_end_week(start, weeks, season_end, expected):
    assert loan_end_week(start, weeks, season_end) == expected


def test_loan_end_week_rejects_empty_duration():
    for weeks in (0, -3):
        with pytest.raises(ValueError, match="en az 1 hafta"):
            loan_end_week(10, weeks, 30)


@pytest.mark.parametrize(("level", "weeks", "allowed", "message"), [
    (2, 4, True, "Oyuncu kiralık kulübünde süre alamıyor; erken geri çağrılabilir."),
    (3, 20, True, "erken geri çağrılabilir"),
    (9, 4, True, "erken geri çağrılabilir"),            # seviye 0-3'e kirpilir
    (1, 20, False, "Oyuncu kiralık kulübünde süre alıyor; yalnızca süre alamayan oyuncu erken geri çağrılabilir."),
    (0, 0, False, "süre alıyor"),                        # kaygi denetimi once
    (None, 10, False, "süre alıyor"),
    (-2, 10, False, "süre alıyor"),
    (2, 3, False, "Kiralık oyuncu en az 4 hafta sonra geri çağrılabilir (3 hafta oldu)."),
    (3, -1, False, "(0 hafta oldu)"),
])
def test_recall_allowed(level, weeks, allowed, message):
    ok, reason = recall_allowed(level, weeks)
    assert ok is allowed
    assert message in reason


def test_recall_threshold_matches_concern_levels():
    import concerns

    assert lr.RECALL_MIN_CONCERN == concerns.ConcernLevel.CONCERNED
    assert recall_allowed(concerns.ConcernLevel.CONCERNED, lr.RECALL_MIN_WEEKS)[0]
    assert not recall_allowed(concerns.ConcernLevel.WATCH, lr.RECALL_MIN_WEEKS)[0]
