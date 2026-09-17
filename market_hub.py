"""
market_hub.py
=============
Menajerler arasi transfer ve kiralik merkezi (Faz 12 / 14. Asama, 12B). Kontrolcu: commit ETMEZ; web
callback'i SHARED dunya kilidi altinda cagirir. Hatalar transfers.TransferError alt siniflaridir.

    MarketHub        -> gelen/giden teklifler, karsi teklif, kabul, sozlesme masasi (contract_log'dan
                        deterministik yeniden kurulur), tamamlama, listeler, kiraliklar, yonetici incelemesi
                        ve transfer iadesi. Satir kilidi sirasi: teklifler -> oyuncular (artan id) ->
                        kulupler (artan id) -> personel.
    MarketExtension  -> kariyer eklentisi (extensions.CareerExtension): haftalik teklif suresi, kiralik bitisi,
                        adil oyun toparlanmasi, kiralik oyuncu kaygisi bildirimleri.
    AI kulube yapilan teklifler bugunku akisla (CareerManager.offer_fee / open_negotiation) devam eder.
    transfer_log.kind yeni duz metin degerleri: EXCHANGE, LOAN, LOAN_RETURN, REVERSAL.

Durum: iskelet (hata ve veri siniflari tanimli); Faz 12 B2 paketinde doldurulacak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from extensions import CareerExtension
from market_rules import OfferKind
from transfers import TransferError

if TYPE_CHECKING:
    from career_manager import CareerManager, TransferNews
    from fair_play import FairnessVerdict
    from models import Player, TransferLog
    from transfers import ContractOffer, NegotiationStatus

_PACKAGE = "Faz 12: market_hub (B2)"


class MarketError(TransferError):
    """Menajerler arasi pazar islemi yapilamaz (mesaj Turkce)."""


class FairPlayBlocked(MarketError):
    """Adil oyun denetimi anlasmayi engelledi; gerekceler verdict'tedir."""

    def __init__(self, verdict: FairnessVerdict, message: str | None = None) -> None:
        self.verdict = verdict
        super().__init__(message or "Adil oyun denetimi bu anlaşmayı engelledi.")


class LoanError(MarketError):
    """Kiralik islemi yapilamaz."""


@dataclass(frozen=True)
class OfferDraft:
    player_id: int
    kind: OfferKind = OfferKind.TRANSFER
    fee: int = 0
    exchange_player_id: int | None = None
    loan_weeks: int | None = None
    loan_wage_share: int = 100
    note: str = ""


@dataclass(frozen=True)
class OfferView:
    id: int
    kind: str
    status: str
    status_label: str
    direction: str                    # IN / OUT
    player_id: int
    player_name: str
    seller_team: str | None
    buyer_team: str | None
    fee: int
    exchange_player_name: str | None
    loan_weeks: int | None
    loan_wage_share: int
    round: int
    expires_in_weeks: int | None
    can_accept: bool
    can_reject: bool
    can_counter: bool
    can_withdraw: bool
    can_contract: bool
    fairness: FairnessVerdict | None
    reason: str
    history: tuple[str, ...]


@dataclass(frozen=True)
class ContractStep:
    status: NegotiationStatus
    message: str
    complaints: tuple[str, ...]
    demand: ContractOffer | None
    rounds_left: int
    needs_room: int | None


@dataclass(frozen=True)
class LoanView:
    id: int
    player_name: str
    parent_team: str | None
    borrower_team: str | None
    wage_share: int
    ends_career_week: int | None
    weeks_left: int | None
    status: str
    concern_label: str
    can_recall: bool


@dataclass(frozen=True)
class PlayerMarketStatus:
    owner_is_human: bool
    seller_seat_name: str | None
    on_loan: bool
    block_reason: str | None
    my_open_offer_id: int | None
    open_offers: int


@dataclass(frozen=True)
class InboxCounts:
    offers_in: int
    offers_action: int
    messages: int
    notifications: int
    reviews: int


class MarketHub:
    def __init__(self, cm: CareerManager) -> None:
        raise NotImplementedError(_PACKAGE)

    def inbox(self) -> list[OfferView]:
        raise NotImplementedError(_PACKAGE)

    def outbox(self) -> list[OfferView]:
        raise NotImplementedError(_PACKAGE)

    def offer(self, offer_id: int) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def counts(self) -> InboxCounts:
        raise NotImplementedError(_PACKAGE)

    def player_status(self, player_id: int) -> PlayerMarketStatus:
        raise NotImplementedError(_PACKAGE)

    def exchange_candidates(self) -> list[Player]:
        raise NotImplementedError(_PACKAGE)

    def preview_fairness(self, draft: OfferDraft) -> FairnessVerdict:
        raise NotImplementedError(_PACKAGE)

    def make_offer(self, draft: OfferDraft) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def accept(self, offer_id: int) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def reject(self, offer_id: int, reason: str = "") -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def counter(
        self,
        offer_id: int,
        *,
        fee: int,
        exchange_player_id: int | None = None,
        loan_weeks: int | None = None,
        loan_wage_share: int | None = None,
    ) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def withdraw(self, offer_id: int) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def open_contract(self, offer_id: int) -> ContractStep:
        raise NotImplementedError(_PACKAGE)

    def submit_contract(self, offer_id: int, offer: ContractOffer) -> ContractStep:
        raise NotImplementedError(_PACKAGE)

    def complete(self, offer_id: int, contract: ContractOffer, shift_wage_room: bool = False) -> TransferNews:
        raise NotImplementedError(_PACKAGE)

    def set_listing(self, player_id: int, transfer_listed: bool | None = None, loan_listed: bool | None = None) -> None:
        raise NotImplementedError(_PACKAGE)

    def listed_players(self, kind: str) -> list[Player]:
        raise NotImplementedError(_PACKAGE)

    def loans(self, direction: str = "ALL") -> list[LoanView]:
        raise NotImplementedError(_PACKAGE)

    def request_ai_loan(self, draft: OfferDraft) -> LoanView:
        raise NotImplementedError(_PACKAGE)

    def recall_loan(self, loan_id: int) -> LoanView:
        raise NotImplementedError(_PACKAGE)

    def review_queue(self) -> list[OfferView]:
        raise NotImplementedError(_PACKAGE)

    def approve_review(self, offer_id: int) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def deny_review(self, offer_id: int, reason: str) -> OfferView:
        raise NotImplementedError(_PACKAGE)

    def reversible_transfers(self, weeks: int = 8) -> list[TransferLog]:
        raise NotImplementedError(_PACKAGE)

    def reverse_transfer(self, offer_id: int, reason: str) -> list[TransferNews]:
        raise NotImplementedError(_PACKAGE)


class MarketExtension(CareerExtension):
    """Kurucu iskelette NotImplementedError: extensions.load bu eklentiyi paket dolana kadar atlar."""

    def __init__(self, cm: CareerManager) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_week(self, week: int, report) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_season_end(self) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_season_start(self, new_season: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def new_season_blocker(self) -> str | None:
        raise NotImplementedError(_PACKAGE)

    def on_player_moved(self, player, seller_id: int | None, buyer_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def on_club_released(self, team_id: int) -> None:
        raise NotImplementedError(_PACKAGE)

    def transfer_block_reason(self, player) -> str | None:
        raise NotImplementedError(_PACKAGE)
