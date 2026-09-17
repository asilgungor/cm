"""
fair_play.py
============
Menajerler arasi anlasmalarin adil oyun denetimi (Faz 12 / 14. Asama, 12B). SAF modul: veritabani, Streamlit ve
ORM bilmez; ayni girdi her zaman ayni karari verir (rastgelelik yok).

    Decision         -> ALLOW / REVIEW (yonetici onayi) / BLOCK
    DealFacts        -> anlasmanin anlik goruntusu (kontrolcu veritabanindan doldurur; varsayilan deger YOK: unutulan
                        alan sessizce "temiz" sayilmasin)
    evaluate         -> FairnessVerdict(puan, karar, Turkce gerekceler, sabit makine bayraklari, esikler)
    thresholds       -> (inceleme esigi, engel esigi): denetim seviyesi ve iki tarafin DUSUK adil oyun puani
    FAIR_PLAY_DELTAS -> adil oyun puani degisimleri (engellenen -10, reddedilen inceleme -15, onay +2, iade -25)
    weekly_recovery  -> haftalik toparlanma MIKTARI (SeatStore.adjust_fair_play'e delta olarak verilir)
    apply_fair_play  -> puan + delta, 0-100 araligina kirpilir

Puanlama (evaluate), bayrak -> puan:
    SAME_SEAT          alici ve satici ayni koltuk                                                   +100
    VALUE_UNDERPRICED  TRANSFER: karsilik = bonservis + takas degeri; fark = |karsilik - deger| / deger.
                       fark 0.35'i asarsa (fark - 0.35) x 100 (en fazla 100). Mutlak fark IMBALANCE_MIN_GAP
                       (250K) altindaysa sayilmaz (fazlalik oyuncuyu bedava vermek sorun degil). Deger en az 10K.
    VALUE_OVERPRICED   ayni fark, karsilik degerin USTUNDE: (fark - 0.35) x 50 (en fazla 100). Egim yarimdir cunku
                       kulupler mesru olarak degerin 1.15-1.95 katini ister (transfers.asking_price primleri);
                       asiri fazla odeme yine de para aktarimidir.
    LOAN_FEE_HIGH      LOAN: kiralik bedeli / deger 0.35'i asarsa (oran - 0.35) x 50 (bedel 250K altindaysa sayilmaz)
    REPEATED_PAIR      ayni iki koltuk (yon farketmez) son PAIR_WINDOW_WEEKS (20) haftada ONCEKI her anlasma icin +15
                       (pair_deals_recent bu anlasmayi saymaz: ikinci anlasma +15, ucuncu +30)
    NEW_BUYER          alici hesabi 7 gunden yeni YA DA koltugu 2 haftadan yeni                          +15
    NEW_SELLER         ayni kural satici icin                                                         +15
    BURST              taraflardan biri son BURST_WINDOW_WEEKS (4) haftada bu anlasmayla 3'ten fazla
                       menajerler arasi anlasma yapiyor (buyer/seller_deals_recent ONCEKI anlasmalar)    +10 (bir kez)
    CHEAP_LOAN         LOAN: kiralayanin maas payi %20'nin altinda ve oyuncu degerli (deger >= 5M ya da guc >= 80) +10
    LOW_FAIR_PLAY      bilgi: iki taraftan dusuk adil oyun puani 80'in altinda (puan eklemez, esikler zaten sertlesir)

Esikler (thresholds): taban (inceleme, engel) LOW (45, 75) · MEDIUM (30, 60) · HIGH (20, 45); bilinmeyen seviye
MEDIUM sayilir. Carpan = 0.5 + 0.5 x (dusuk adil oyun / 100): puan 100 -> x1.0, 50 -> x0.75, 0 -> x0.5.
    MEDIUM, adil oyun 90 -> (28.5, 57) · 75 -> (26.25, 52.5) · 50 -> (22.5, 45) · 0 -> (15, 30)
Karar: puan >= engel -> BLOCK; puan >= inceleme -> REVIEW; aksi ALLOW (sinirlar dahil).

Calisilmis ornekler (aksi belirtilmedikce MEDIUM, iki taraf adil oyun 100 -> esikler 30 / 60, eski hesaplar):
    10M degerinde oyuncu  9M       fark 0.10                                   0     -> ALLOW
    10M degerinde oyuncu  5M       fark 0.50  (0.15 x 100)                     15    -> ALLOW
    10M degerinde oyuncu  3M       fark 0.70  (0.35 x 100)                     35    -> REVIEW
    10M degerinde oyuncu  bedava   fark 1.00  (0.65 x 100)                     65    -> BLOCK
    10M degerinde oyuncu 16M       fark 0.60  (0.25 x 50)                      12.5  -> ALLOW (AI istenen bedeli)
    10M degerinde oyuncu 20M       fark 1.00  (0.65 x 50)                      32.5  -> REVIEW
    10M degerinde oyuncu 30M       fark 2.00  (1.65 x 50)                      82.5  -> BLOCK
    10M, 6M + 3M degerinde takas   karsilik 9M                                 0     -> ALLOW
    200K degerinde oyuncu bedava   mutlak fark 250K altinda                    0     -> ALLOW
    10M tam bedel, alici hesabi 3 gunluk, ayni ikilinin 2. anlasmasi  15 + 15   30    -> REVIEW
    20M oyuncu %10 maas payiyla kiralik, eski hesaplar                          10    -> ALLOW
    ayni kiralik, ikilinin 3. anlasmasi, satici adil oyun 50 (esikler 22.5 / 45)  10 + 30 = 40 -> REVIEW
    HIGH (20 / 45): 10M oyuncu 4.5M (fark 0.55 -> 20) -> REVIEW · 2M (fark 0.80 -> 45) -> BLOCK
    LOW (45 / 75): 10M oyuncu bedava (65) -> REVIEW; + yeni satici hesabi (80) -> BLOCK

Kontrolcu notu (B2): BLOCK ve DENY cezasi iki tarafa da yazilir (hediye anlasmasinda satici da ortaktir);
REVERSED cezasi geri alinan anlasmanin iki tarafina. Haftalik toparlanma: puan 100'un altindaysa +1 (engel 10 hafta,
iade 25 hafta surer).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from loan_rules import format_money
from market_rules import OfferKind


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


DECISION_LABELS: Mapping[Decision, str] = MappingProxyType({
    Decision.ALLOW: "Uygun",
    Decision.REVIEW: "Yönetici incelemesi gerekiyor",
    Decision.BLOCK: "Engellendi",
})

# --- Deger dengesizligi -----------------------------------------------------
IMBALANCE_FREE_RATIO = 0.35            # bu orana kadar fark serbest
IMBALANCE_UNDER_POINTS = 100.0         # ucuz satis: serbest oranin ustundeki her 1.0 fark icin puan
IMBALANCE_OVER_POINTS = 50.0           # pahali satis ve kiralik bedeli
IMBALANCE_MAX_POINTS = 100.0
IMBALANCE_MIN_GAP = 250_000            # bundan kucuk mutlak fark sayilmaz (EUR)
VALUE_FLOOR = 10_000                   # sifir degerli oyuncuda bolme hatasi olmasin (finance.MIN_VALUE_WITH_POTENTIAL)

# --- Iliski ve hesap sinyalleri -------------------------------------------------
PAIR_WINDOW_WEEKS = 20
PAIR_POINTS = 15.0
NEW_ACCOUNT_DAYS = 7
NEW_SEAT_WEEKS = 2
NEW_PARTY_POINTS = 15.0
BURST_WINDOW_WEEKS = 4
BURST_MAX_DEALS = 3                    # pencerede bu anlasmayla birlikte bundan fazlasi "yogun"
BURST_POINTS = 10.0
CHEAP_LOAN_SHARE = 20                  # kiralayan payi bunun ALTINDA ise ucuz
LOAN_HIGH_VALUE = 5_000_000
LOAN_HIGH_OVERALL = 80
CHEAP_LOAN_POINTS = 10.0
SAME_SEAT_POINTS = 100.0
LOW_FAIR_PLAY_NOTE = 80.0

# --- Esikler ve adil oyun puani -------------------------------------------------
STRICTNESS_DEFAULT = "MEDIUM"
BASE_THRESHOLDS: Mapping[str, tuple[float, float]] = MappingProxyType({
    "LOW": (45.0, 75.0),
    "MEDIUM": (30.0, 60.0),
    "HIGH": (20.0, 45.0),
})
MIN_THRESHOLD_FACTOR = 0.5             # adil oyun 0'da esikler yariya iner
FAIR_PLAY_MIN, FAIR_PLAY_MAX = 0.0, 100.0
RECOVERY_PER_WEEK = 1.0

FLAG_SAME_SEAT = "SAME_SEAT"
FLAG_UNDERPRICED = "VALUE_UNDERPRICED"
FLAG_OVERPRICED = "VALUE_OVERPRICED"
FLAG_LOAN_FEE_HIGH = "LOAN_FEE_HIGH"
FLAG_REPEATED_PAIR = "REPEATED_PAIR"
FLAG_NEW_BUYER = "NEW_BUYER"
FLAG_NEW_SELLER = "NEW_SELLER"
FLAG_BURST = "BURST"
FLAG_CHEAP_LOAN = "CHEAP_LOAN"
FLAG_LOW_FAIR_PLAY = "LOW_FAIR_PLAY"
FLAGS = (FLAG_SAME_SEAT, FLAG_UNDERPRICED, FLAG_OVERPRICED, FLAG_LOAN_FEE_HIGH, FLAG_REPEATED_PAIR, FLAG_NEW_BUYER,
         FLAG_NEW_SELLER, FLAG_BURST, FLAG_CHEAP_LOAN, FLAG_LOW_FAIR_PLAY)


@dataclass(frozen=True)
class DealFacts:
    kind: str                         # market_rules.OfferKind degeri
    fee: int                          # bonservis ya da kiralik bedeli
    player_value: int                 # Player.market_value (yoksa finance.market_value)
    player_overall: int
    player_age: int                   # bilgi (deger zaten yasi icerir)
    exchange_value: int               # takas oyuncusunun degeri (yoksa 0)
    loan_wage_share: int | None       # kiralayanin maas yuzdesi (transferde None)
    loan_weeks: int | None
    buyer_seat_id: int
    seller_seat_id: int
    buyer_account_age_days: int       # accounts.users.created_at'ten bugune gun
    seller_account_age_days: int
    buyer_seat_weeks: int             # career_week - world_managers.joined_career_week
    seller_seat_weeks: int
    pair_deals_recent: int            # iki koltuk arasi ONCEKI anlasmalar, son PAIR_WINDOW_WEEKS hafta (iki yon)
    buyer_deals_recent: int           # alicinin ONCEKI menajerler arasi anlasmalari, son BURST_WINDOW_WEEKS hafta
    seller_deals_recent: int
    buyer_fair_play: float
    seller_fair_play: float


@dataclass(frozen=True)
class FairnessVerdict:
    score: float
    decision: Decision
    reasons: tuple[str, ...]
    flags: tuple[str, ...]
    review_at: float = 0.0            # uygulanan inceleme esigi
    block_at: float = 0.0             # uygulanan engel esigi

    @property
    def label(self) -> str:
        return DECISION_LABELS[self.decision]


FAIR_PLAY_DELTAS: Mapping[str, float] = MappingProxyType({
    "BLOCKED": -10.0,
    "DENIED": -15.0,
    "APPROVED": 2.0,
    "REVERSED": -25.0,
})
# fair_play_log.reason icin Turkce metinler (RECOVERY: weekly_recovery)
FAIR_PLAY_REASONS: Mapping[str, str] = MappingProxyType({
    "BLOCKED": "Anlaşma adil oyun denetiminde engellendi",
    "DENIED": "Anlaşma yönetici incelemesinde reddedildi",
    "APPROVED": "Anlaşma yönetici incelemesinde onaylandı",
    "REVERSED": "Transfer yönetici tarafından geri alındı",
    "RECOVERY": "Haftalık adil oyun toparlanması",
})


def _strictness(value) -> str:
    level = str(getattr(value, "value", value) or "").strip().upper()
    return level if level in BASE_THRESHOLDS else STRICTNESS_DEFAULT


def _clamp_fair_play(score: float) -> float:
    return max(FAIR_PLAY_MIN, min(FAIR_PLAY_MAX, float(score)))


def thresholds(strictness: str, min_fair_play: float) -> tuple[float, float]:
    """(inceleme esigi, engel esigi). Iki taraftan DUSUK adil oyun puani dustukce esikler dogrusal sertlesir."""
    review, block = BASE_THRESHOLDS[_strictness(strictness)]
    factor = MIN_THRESHOLD_FACTOR + (1.0 - MIN_THRESHOLD_FACTOR) * _clamp_fair_play(min_fair_play) / FAIR_PLAY_MAX
    return round(review * factor, 2), round(block * factor, 2)


def _ratio_points(ratio: float, slope: float) -> float:
    return round(min(IMBALANCE_MAX_POINTS, (ratio - IMBALANCE_FREE_RATIO) * slope), 1)


def evaluate(facts: DealFacts, strictness: str = STRICTNESS_DEFAULT) -> FairnessVerdict:
    """Anlasmanin adil oyun karari. Puanlama ve kalibrasyon modul basliginda."""
    f = facts
    score = 0.0
    reasons: list[str] = []
    flags: list[str] = []

    def add(flag: str, points: float, reason: str) -> None:
        nonlocal score
        score += points
        flags.append(flag)
        reasons.append(reason)

    loan = str(getattr(f.kind, "value", f.kind)).upper() == OfferKind.LOAN.value
    value = max(VALUE_FLOOR, int(f.player_value))

    if f.buyer_seat_id == f.seller_seat_id:
        add(FLAG_SAME_SEAT, SAME_SEAT_POINTS, "Alıcı ve satıcı aynı menajer.")

    # 1) deger dengesizligi
    if loan:
        fee = max(0, int(f.fee))
        ratio = fee / value
        if fee >= IMBALANCE_MIN_GAP and ratio > IMBALANCE_FREE_RATIO:
            add(FLAG_LOAN_FEE_HIGH, _ratio_points(ratio, IMBALANCE_OVER_POINTS),
                f"Kiralık bedeli oyuncu değerine göre çok yüksek: {format_money(fee)} "
                f"(değer {format_money(value)}).")
    else:
        consideration = max(0, int(f.fee)) + max(0, int(f.exchange_value))
        gap = consideration - value
        ratio = abs(gap) / value
        if abs(gap) >= IMBALANCE_MIN_GAP and ratio > IMBALANCE_FREE_RATIO:
            pct = round(ratio * 100)
            if gap < 0:
                add(FLAG_UNDERPRICED, _ratio_points(ratio, IMBALANCE_UNDER_POINTS),
                    f"Bedel oyuncu değerinin çok altında: {format_money(consideration)} karşılığında "
                    f"{format_money(value)} değerinde oyuncu (%{pct} eksik).")
            else:
                add(FLAG_OVERPRICED, _ratio_points(ratio, IMBALANCE_OVER_POINTS),
                    f"Bedel oyuncu değerinin çok üstünde: {format_money(consideration)} karşılığında "
                    f"{format_money(value)} değerinde oyuncu (%{pct} fazla).")

    # 2) ayni ikili tekrar tekrar anlasiyor
    pair = max(0, int(f.pair_deals_recent))
    if pair > 0:
        add(FLAG_REPEATED_PAIR, PAIR_POINTS * pair,
            f"Aynı iki menajer son {PAIR_WINDOW_WEEKS} haftada {pair} kez daha anlaştı.")

    # 3) yeni hesap / yeni koltuk
    for flag, side, days, weeks in (
        (FLAG_NEW_BUYER, "Alıcı", f.buyer_account_age_days, f.buyer_seat_weeks),
        (FLAG_NEW_SELLER, "Satıcı", f.seller_account_age_days, f.seller_seat_weeks),
    ):
        if int(days) < NEW_ACCOUNT_DAYS or int(weeks) < NEW_SEAT_WEEKS:
            add(flag, NEW_PARTY_POINTS,
                f"{side} menajer yeni (hesap {max(0, int(days))} günlük, koltuk {max(0, int(weeks))} haftalık).")

    # 4) kisa surede cok anlasma
    buyer_deals = max(0, int(f.buyer_deals_recent)) + 1
    seller_deals = max(0, int(f.seller_deals_recent)) + 1
    if max(buyer_deals, seller_deals) > BURST_MAX_DEALS:
        add(FLAG_BURST, BURST_POINTS,
            f"Son {BURST_WINDOW_WEEKS} haftada çok sayıda menajerler arası anlaşma "
            f"(bu anlaşmayla alıcı {buyer_deals}, satıcı {seller_deals}).")

    # 5) degerli oyuncunun maasi neredeyse bedava kiralaniyor
    if loan:
        share = 100 if f.loan_wage_share is None else int(f.loan_wage_share)
        valuable = int(f.player_value) >= LOAN_HIGH_VALUE or int(f.player_overall) >= LOAN_HIGH_OVERALL
        if share < CHEAP_LOAN_SHARE and valuable:
            add(FLAG_CHEAP_LOAN, CHEAP_LOAN_POINTS,
                f"Değerli oyuncu çok düşük maaş payıyla kiralanıyor (%{max(0, share)}).")

    low_fair_play = min(_clamp_fair_play(f.buyer_fair_play), _clamp_fair_play(f.seller_fair_play))
    review_at, block_at = thresholds(strictness, low_fair_play)
    if low_fair_play < LOW_FAIR_PLAY_NOTE:
        flags.append(FLAG_LOW_FAIR_PLAY)
        reasons.append(f"Adil oyun puanı düşük ({low_fair_play:.0f}); denetim eşikleri sertleşti.")

    score = round(score, 1)
    if score >= block_at:
        decision = Decision.BLOCK
    elif score >= review_at:
        decision = Decision.REVIEW
    else:
        decision = Decision.ALLOW
    return FairnessVerdict(score, decision, tuple(reasons), tuple(flags), review_at, block_at)


def weekly_recovery(score: float) -> float:
    """Haftalik toparlanma miktari (delta >= 0): 100'un altinda +RECOVERY_PER_WEEK, 100'u asmaz."""
    return round(min(RECOVERY_PER_WEEK, FAIR_PLAY_MAX - _clamp_fair_play(score)), 2)


def apply_fair_play(score: float, delta: float) -> float:
    """Puan + delta, 0-100 araligina kirpilmis (world_managers.fair_play CHECK)."""
    return round(_clamp_fair_play(float(score) + float(delta)), 2)
