"""
fair_play.py
============
Menajerler arasi anlasmalarin adil oyun denetimi (Faz 12 / 14. Asama, 12B). SAF modul.

Puanlama (evaluate): deger dengesizligi |bonservis + takas - deger| / deger 0.35 ustunde (ucuz VE pahali satis);
ayni ikili son 20 kariyer haftasinda ilk anlasmadan sonra anlasma basina +15; yeni hesap (<7 gun) ya da yeni
koltuk (<2 hafta) taraf basina +15; 4 haftada 3'ten fazla insan-insan anlasmasi +10; yuksek degerli oyuncuda
%20'nin altinda kiralik maas payi +10. Esikler (thresholds) iki tarafin DUSUK adil oyun puanina gore sertlesir.

    Decision        -> ALLOW / REVIEW (yonetici onayi) / BLOCK
    FAIR_PLAY_DELTAS-> adil oyun puani degisimleri (engellenen -10, reddedilen inceleme -15, onay +2, iade -25)
    weekly_recovery -> haftalik toparlanma

Durum: iskelet (enum, veri siniflari ve sabitler tanimli); Faz 12 B1 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

_PACKAGE = "Faz 12: fair_play (B1)"


class Decision(str, Enum):
    ALLOW = "ALLOW"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class DealFacts:
    kind: str                         # market_rules.OfferKind degeri
    fee: int
    player_value: int
    player_overall: int
    player_age: int
    exchange_value: int
    loan_wage_share: int | None
    loan_weeks: int | None
    buyer_seat_id: int
    seller_seat_id: int
    buyer_account_age_days: int
    seller_account_age_days: int
    buyer_seat_weeks: int
    seller_seat_weeks: int
    pair_deals_recent: int
    buyer_deals_recent: int
    seller_deals_recent: int
    buyer_fair_play: float
    seller_fair_play: float


@dataclass(frozen=True)
class FairnessVerdict:
    score: float
    decision: Decision
    reasons: tuple[str, ...]
    flags: tuple[str, ...]


FAIR_PLAY_DELTAS: Mapping[str, float] = MappingProxyType({
    "BLOCKED": -10.0,
    "DENIED": -15.0,
    "APPROVED": 2.0,
    "REVERSED": -25.0,
})


def evaluate(facts: DealFacts, strictness: str = "MEDIUM") -> FairnessVerdict:
    raise NotImplementedError(_PACKAGE)


def thresholds(strictness: str, min_fair_play: float) -> tuple[float, float]:
    """(inceleme esigi, engel esigi)."""
    raise NotImplementedError(_PACKAGE)


def weekly_recovery(score: float) -> float:
    raise NotImplementedError(_PACKAGE)
