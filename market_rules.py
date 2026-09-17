"""
market_rules.py
===============
Menajerler arasi transfer teklifi kurallari (Faz 12 / 14. Asama, 12B). SAF modul: veritabani ve Streamlit
bilmez.

    OfferKind / OfferStatus / OfferAction -> transfer_offers.kind / status degerleri ve eylemler
                             (models.OFFER_KINDS / OFFER_STATUSES ile birebir ayni; test dogrular)
    transition             -> durum makinesi: (durum, eylem, taraf) -> yeni durum ya da OfferStateError
    validate_offer         -> teklif anlik goruntusundeki kural ihlalleri (Turkce)
    expires_at             -> teklifin dusecegi mutlak kariyer haftasi
    squad_level_refusal    -> oyuncu alici kulubun kadro seviyesinin cok altinda/ustundeyse ret nedeni
    contract_rng_seed      -> sozlesme masasinin deterministik tohumu (contract_log'dan yeniden kurulur)

Durum: iskelet (enum ve sabitler tanimli); Faz 12 B1 paketinde doldurulacak.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from world_rules import WorldRules

_PACKAGE = "Faz 12: market_rules (B1)"


class OfferKind(str, Enum):
    TRANSFER = "TRANSFER"
    LOAN = "LOAN"


class OfferStatus(str, Enum):
    PENDING = "PENDING"            # alici teklif etti, satici yaniti bekleniyor
    COUNTERED = "COUNTERED"        # karsi teklif yapildi, diger tarafin yaniti bekleniyor
    CONTRACT = "CONTRACT"          # bonservis kabul, alici oyuncuyla sozlesme masasinda
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    VOIDED = "VOIDED"              # oyuncu tasindi / kulup birakildi / baska teklif tamamlandi
    BLOCKED = "BLOCKED"            # adil oyun denetimi engelledi
    REVIEW = "REVIEW"              # yonetici incelemesinde
    REVERSED = "REVERSED"          # tamamlanmis transfer yonetici tarafindan geri alindi


class OfferAction(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    COUNTER = "COUNTER"
    WITHDRAW = "WITHDRAW"
    COMPLETE = "COMPLETE"
    EXPIRE = "EXPIRE"
    VOID = "VOID"
    BLOCK = "BLOCK"
    SEND_REVIEW = "SEND_REVIEW"
    APPROVE = "APPROVE"
    DENY = "DENY"
    REVERSE = "REVERSE"


ACTOR_SIDES = ("BUYER", "SELLER", "ADMIN", "SYSTEM")

# transfer_offers kismi benzersiz indeksindeki "acik" durumlar (models.OPEN_OFFER_STATUSES)
OPEN_STATUSES: frozenset[OfferStatus] = frozenset(
    {OfferStatus.PENDING, OfferStatus.COUNTERED, OfferStatus.CONTRACT, OfferStatus.REVIEW}
)
MAX_COUNTER_ROUNDS = 4


class OfferStateError(ValueError):
    """Bu durumda bu eylem yapilamaz (mesaj Turkce)."""


@dataclass(frozen=True)
class OfferFacts:
    """Teklif dogrulamasi icin anlik goruntu (yalnizca sayilar ve bayraklar; ORM nesnesi degil)."""
    kind: OfferKind
    player_id: int
    player_team_id: int | None
    seller_team_id: int | None
    buyer_team_id: int | None
    fee: int
    player_value: int
    exchange_player_id: int | None = None
    exchange_player_team_id: int | None = None
    exchange_value: int = 0
    loan_weeks: int | None = None
    loan_wage_share: int = 100
    player_on_loan: bool = False
    player_in_academy: bool = False
    player_transfer_locked: bool = False
    exchange_on_loan: bool = False
    exchange_transfer_locked: bool = False
    buyer_transfer_budget: int = 0
    buyer_is_human: bool = True
    seller_is_human: bool = True
    seller_squad_size: int = 0
    buyer_squad_size: int = 0


def transition(status: OfferStatus, action: OfferAction, actor_side: str) -> OfferStatus:
    raise NotImplementedError(_PACKAGE)


def validate_offer(facts: OfferFacts) -> list[str]:
    raise NotImplementedError(_PACKAGE)


def expires_at(career_week: int, rules: WorldRules) -> int:
    raise NotImplementedError(_PACKAGE)


def squad_level_refusal(player_overall: int, buyer_top_ratings: Sequence[int], margin: int = 12) -> str | None:
    raise NotImplementedError(_PACKAGE)


def contract_rng_seed(offer_id: int, buyer_id: int, player_id: int) -> int:
    raise NotImplementedError(_PACKAGE)
