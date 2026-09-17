"""
market_hub.py
=============
Menajerler arasi transfer ve kiralik merkezi (Faz 12 / 14. Asama, 12B). Kontrolcu: commit ETMEZ; web callback'i
SHARED dunya kilidi altinda cagirir ve islemi kendisi kapatir. Hatalar transfers.TransferError alt siniflaridir
(MarketError / FairPlayBlocked / LoanError / OfferVoided). Kurallar SAF modullerde: market_rules (durum makinesi,
teklif dogrulama), loan_rules (kiralik), fair_play (adil oyun). cm.rng'den ASLA cekilmez.

AKIS (insan alici -> insan satici; yapay zekâ kulubune teklif bugunku akisla: CareerManager.offer_fee)
    make_offer   PENDING (tur 0). Oyuncu satiri FOR SHARE: suren bir tamamlama bitene kadar bekler, sonra oyuncunun
                 guncel kulubuyle dogrular. Ayni aliciya ayni oyuncu icin tek acik teklif: uq_transfer_offer_open
                 (IntegrityError -> Turkce MarketError).
    counter      sirasi gelen taraf bedel / takas / kiralik sartlarini degistirir -> COUNTERED (tur + 1, en fazla 4).
    accept       sirasi gelen taraf kabul eder; ADIL OYUN burada degerlendirilir (fair_play.evaluate):
                     BLOCK  -> BLOCKED (SYSTEM), iki koltuga FAIR_PLAY_DELTAS["BLOCKED"], gerekce reason'da
                     REVIEW -> REVIEW (SYSTEM SEND_REVIEW), yoneticilere REVIEW bildirimi
                     ALLOW  -> CONTRACT. Oyuncu basina tek CONTRACT: uq_transfer_offer_contract es zamanli kabulleri
                               cozer (savepoint icinde IntegrityError -> MarketError).
                 BLOCK / REVIEW sonucu HATA DEGIL: yazilir ve OfferView doner (ceza kalici olsun diye; status'a bak).
    open_contract / submit_contract
                 alici oyuncuyla sozlesme masasinda. Masa contract_log'dan DETERMINISTIK kurulur: acilista oyuncu ve
                 alici kulubun o anki degerleri ("open" kaydi) dondurulur, transfers.ContractNegotiation
                 random.Random(market_rules.contract_rng_seed(teklif, alici, oyuncu)) ile yeniden kurulur ve her
                 "bid" kaydi sirayla oynatilir (hafta ilerlese de talepler degismez). Oyuncu reddederse
                 (transfers.check_interest prestij kapisi, market_rules.squad_level_refusal, kirmizi cizgi, sabir)
                 teklif SYSTEM REJECT -> REJECTED.
    complete     SATIR KILITLERI: teklif + ayni oyuncu / takas oyuncusu uzerindeki acik teklifler (artan id) ->
                 oyuncular (artan id) -> kulupler (artan id); hepsi FOR NO KEY UPDATE (FK kontrollerinin KEY SHARE
                 kilitleriyle cakismaz, kilitlenmeye yol acmaz). Her sey kilit altinda YENIDEN dogrulanir (taraflar hala
                 menajerli, oyuncu hala saticida, kasa, kadro tabani, takas oyuncusunun istegi, maas alani, anlasilan
                 sozlesme). Degisiklikler tek savepoint'te: takas oyuncusu sozlesmesiyle saticiya (transfer_log EXCHANGE,
                 CareerManager._record_player_move), ana oyuncu CareerManager.complete_transfer(..., human_deal=True,
                 expected_seller_id=satici) ile aliciya -- PARA TEK YERDE ve BIR KEZ hareket eder. Ayni oyuncu /
                 takas oyuncusu uzerindeki diger acik teklifler ayni islemde VOIDED. Ikinci tamamlama teklif satir
                 kilidinde bekler, COMPLETED gorur ve MarketError alir.
    withdraw     alici (CONTRACT dahil, REVIEW haric) · reject: sirasi gelen taraf · EXPIRE: MarketExtension.on_week
                 (expires_career_week <= sonraki kariyer haftasi) · VOID: oyuncu tasindi / kulup birakildi / menajer
                 degisti (eklenti kancalari, islem aninda dogrulama ve haftalik tarama).

KIRALIK (rules.loans)
    insan <-> insan: teklif (kind LOAN; bedel = kiralik bedeli; takas yok). Oyuncu sozlesmesi yapilmaz: CONTRACT'tan
    sonra alici complete(offer_id, None) ile tamamlar. insan <-> AI: request_ai_loan aninda karar verir
    (loan_rules.ai_accepts_loan_out / ai_accepts_loan_in); kayit icin COMPLETED bir teklif satiri yazilir
    (responded_by_manager_id NULL = AI).
    Baslangic: loans satiri (ACTIVE), oyuncu team_id = kiralayan, loan_from_team_id = ana kulup, loan_id,
    loan_wage_share = kiralayanin maas yuzdesi; transfer_log LOAN. Maas Team.player_wage_bill ile bolunur
    (loaned_out_players; iliskiler expire edilir). Kiralik oyuncu satilamaz / yeniden kiralanamaz / takasta
    verilemez (CareerManager.transfer_block_reason + validate_offer); akademi engeli icin loan_guard_reason.
    Sure: loan_rules.loan_end_week(sezon sonu = ofset + sezon haftasi + 1); en az MIN_LOAN_WEEKS fiili hafta.
    Bitis: on_week (bitis <= sonraki kariyer haftasi), sezon sonu / basi (on_season_end / on_season_start: tum
    aktif kiraliklar doner), erken geri cagirma recall_loan (loan_rules.recall_allowed), AI ana kulup kosullar
    olusunca kendisi geri cagirir. transfer_log LOAN_RETURN; kaygi bildirimi (tek sefer) ana kulubun menajerine.

YONETICI (accounts.world_memberships rolu OWNER / ADMIN; kendi kulubunun anlasmasinda asla)
    review_queue / approve_review (-> CONTRACT, yeniden degerlendirme yok, +2) / deny_review (-> BLOCKED, -15);
    reversible_offers / reversible_transfers(weeks) / reverse_transfer(offer_id, reason): oyuncular (ve takas,
    kiralik) geri doner, sozlesmeleri eski haline gelir; bedel saticidan aliciya iade edilir (PARA KORUNUR: satici
    kasasi yetmezse yalnizca kasadaki kadar), REVERSED, iki koltuga -25, transfer_log REVERSAL. Her yonetici
    islemi world_events (REVIEW / REVERSAL) satiri yazar.

contract_log (JSONB dizi; bu modulun bicimi, her kayitta "cw" = kariyer haftasi):
    {"kind": "event", "action", "side", "fee", "exchange_player_id", "exchange_name", "loan_weeks", "share",
     "note"?, "reason"?}                                     -> OfferView.history
    {"kind": "fairness", "score", "decision", "reasons", "flags", "review_at", "block_at"}
    {"kind": "open", "player": {...}, "buyer": {"rep", "ratings"}, "manager_rep", "refusal"}
    {"kind": "bid", "wage", "years", "role"}                 -> sozlesme masasi yeniden oynatilir
    {"kind": "done", "fee", "wage", "shift", "player": {...}, "exchange": {...}|None, "loan_id", "log_ids",
     "buyer_seat", "seller_seat"}                            -> adil oyun gecmisi ve iade
Koltuklar: created_by_manager_id = alici koltuk, responded_by_manager_id = satici koltuk (yanit veren taraf;
AI kiraliginda NULL). Yonetici islemlerinin yapani world_events.actor_manager_id'dedir.

Bildirimler messaging.notify ile savepoint icinde (bildirim hatasi anlasmayi bozmaz). Eklenti kancalari
(MarketExtension) hafta ilerlemesini asla durdurmaz: her adim kendi savepoint'inde, hata loglanir.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import concerns
import database
import fair_play
import finance
import loan_rules
import market_rules
import messaging
from extensions import CareerExtension
from fair_play import DealFacts, Decision, FairnessVerdict
from market_rules import (
    ADMIN,
    BUYER,
    SELLER,
    SYSTEM,
    OfferAction,
    OfferFacts,
    OfferKind,
    OfferStateError,
    OfferStatus,
)
from messaging import NotificationKind
from models import (
    LineupStatus,
    Loan,
    LoanStatus,
    MembershipStatus,
    Notification,
    Player,
    SquadRole,
    Team,
    TransferLog,
    TransferOffer,
    User,
    World,
    WorldEvent,
    WorldEventKind,
    WorldManager,
    WorldMembership,
    WorldStatus,
)
from seats import MEMBER_STATUSES, Seat, SeatError
from transfers import (
    POSITION_SALE_FLOOR,
    SQUAD_FLOOR,
    ContractNegotiation,
    ContractOffer,
    NegotiationStatus,
    TransferError,
    check_interest,
)

if TYPE_CHECKING:
    from career_manager import CareerManager, TransferNews

log = logging.getLogger(__name__)

# --- sabitler -------------------------------------------------------------------------------------------------
OFFER_REF = "OFFER"
LOAN_REF = "LOAN"
LOAN_RECALL_REF = "LOAN_RECALL"            # kiralik kaygi bildirimi (kiralik basina tek sefer)
LIST_LIMIT = 50
LISTED_LIMIT = 100
REASON_MAX = 300
NOTE_MAX = 200
DEFAULT_ACCOUNT_AGE_DAYS = 3650            # hesabi olmayan (kayitsiz birincil) koltuk yeni sayilmaz
HISTORY_SLACK_WEEKS = 60                   # adil oyun gecmisi: tamamlanma haftasi olusturmadan bu kadar sonra olabilir
ADMIN_ROLES = frozenset({"OWNER", "ADMIN"})
KIND_TRANSFER = "TRANSFER"
KIND_EXCHANGE = "EXCHANGE"
KIND_LOAN = "LOAN"
KIND_LOAN_RETURN = "LOAN_RETURN"
KIND_REVERSAL = "REVERSAL"
TEAM_COLLECTIONS = ("players", "academy_players", "loaned_out_players", "staff")
OPEN_VALUES = tuple(sorted(s.value for s in market_rules.OPEN_STATUSES))
DEAL_VALUES = (OfferStatus.COMPLETED.value, OfferStatus.REVERSED.value)

# --- Turkce mesajlar ----------------------------------------------------------------------------------------------
NO_SEAT_TEXT = "Bu dünyada menajer koltuğun yok."
NO_TEAM_TEXT = "Önce yöneteceğin kulübü seç."
NOT_SHARED_TEXT = "Menajerler arası pazar yalnızca paylaşılan dünyada açılır."
MARKET_OFF_TEXT = "Bu dünyada menajerler arası transfer pazarı kapalı."
LOANS_OFF_TEXT = "Bu dünyada kiralık sistemi kapalı."
OFFER_NOT_FOUND_TEXT = "Teklif bulunamadı."
LOAN_NOT_FOUND_TEXT = "Kiralık bulunamadı."
PLAYER_NOT_FOUND_TEXT = "Oyuncu bulunamadı."
EXCHANGE_NOT_FOUND_TEXT = "Takas oyuncusu bulunamadı."
OWN_PLAYER_TEXT = "{name} zaten senin takımında."
NO_CLUB_PLAYER_TEXT = "{name} bir kulübe bağlı değil."
AI_TRANSFER_TEXT = "{team} yapay zekâ kulübü; teklifini transfer pazarındaki normal akışla yap."
AI_LOAN_TEXT = "{team} yapay zekâ kulübü; kiralık isteğini doğrudan kulübe gönder."
HUMAN_LOAN_TEXT = "{team} bir menajerin kulübü; kiralık teklifini Teklifler panelinden yap."
DUPLICATE_TEXT = "{name} için zaten açık bir teklifin var; o teklifi güncelle ya da geri çek."
CONTRACT_TAKEN_TEXT = ("{name} için başka bir kulüple sözleşme görüşmesi sürüyor; bu teklif şu an kabul "
                       "edilemez.")
BUSY_TEXT = "Teklif bu sırada güncellendi; sayfayı yenileyip tekrar dene."
ADMIN_ONLY_TEXT = "Bu işlemi yalnızca dünya yöneticisi yapabilir."
OWN_DEAL_TEXT = "Kendi kulübünün anlaşmasını yönetici olarak inceleyemez ya da geri alamazsın."
BUYER_ONLY_CONTRACT_TEXT = "Sözleşme masasına yalnızca alıcı kulüp oturur."
LOAN_NO_CONTRACT_TEXT = "Kiralıkta oyuncu sözleşmesi yapılmaz; kiralığı tamamla."
CONTRACT_NOT_OPEN_TEXT = "Önce sözleşme masasını aç."
CONTRACT_NOT_AGREED_TEXT = "Önce oyuncuyla sözleşmede anlaşmalısın."
CONTRACT_MISMATCH_TEXT = "Sözleşme şartları oyuncuyla anlaşılan şartlarla aynı değil."
CONTRACT_MISSING_TEXT = "Sözleşme şartları eksik."
CONTRACT_INVALID_TEXT = "Geçersiz sözleşme teklifi."
AMOUNT_INVALID_TEXT = "Geçersiz tutar."
AMOUNT_NEGATIVE_TEXT = "Teklif negatif olamaz."
SHARE_INVALID_TEXT = "Kiralık maaş payı %0-100 arasında bir tam sayı olmalı."
WEEKS_INVALID_TEXT = "Kiralık süresi tam sayı (hafta) ya da boş (sezon sonu) olmalı."
WAGE_ROOM_TEXT = ("Maaş havuzunda yer yok: {need}/hafta ek alan gerekli. Bütçe kaydırarak tamamla "
                  "(transfer kasasından {cost}).")
SHIFT_BUDGET_TEXT = "Bütçe kaydırma sonrası transfer kasası bedeli karşılamıyor."
SEASON_OVER_LOAN_TEXT = "Sezon bitti; kiralık anlaşmaları yeni sezonda yapılabilir."
LOAN_TOO_SHORT_TEXT = ("Sezonun bitmesine {weeks} hafta var; kiralık en az {min} hafta sürmeli "
                       "(yeni sezonu bekle).")
LOAN_TARGET_TEXT = "Oyuncuyu kiralayacak kulübü seç."
LOAN_KIND_TEXT = "Kiralık isteği için teklif türü kiralık olmalı."
LOAN_EXCHANGE_TEXT = "Kiralık teklifte takas oyuncusu olamaz."
AI_LOAN_FEE_TEXT = "{team} kiralık için bedel ödemez; bedelsiz kiralık öner."
PROTECTED_TEXT = "{team} yönetim koruması altında; {weeks} hafta daha transfer ve kiralık yapmaz."
LOAN_ENDED_TEXT = "Kiralık zaten sona erdi."
NOT_YOUR_PLAYER_TEXT = "{name} senin oyuncun değil."
LISTING_LOAN_TEXT = "Kiralık oyuncu listeye konamaz."
LISTING_ACADEMY_TEXT = "Akademi oyuncuları listeye konamaz."
LISTED_KIND_TEXT = "Liste türü TRANSFER ya da LOAN olmalı."
DIRECTION_TEXT = "Kiralık yönü ALL, IN ya da OUT olmalı."
NO_DEAL_RECORD_TEXT = "Bu teklifin tamamlanma kaydı yok; geri alınamaz."
SELLER_LEFT_TEXT = "Satıcı kulübün menajeri ayrıldı; teklif geçersiz."
BUYER_LEFT_TEXT = "Alıcı kulübün menajeri ayrıldı; teklif geçersiz."
MANAGER_CHANGED_TEXT = "Kulübün menajeri değişti; teklif geçersiz."
CLUB_MISSING_TEXT = "Kulüp bulunamadı; teklif geçersiz."
EXCHANGE_MOVED_TEXT = "Takas oyuncusu artık alıcı kulüpte değil; teklif geçersiz."
EXPIRED_TEXT = "Teklifin süresi doldu."
CLUB_RELEASED_TEXT = "Kulübün menajeri kulüpten ayrıldı; teklif geçersiz."
ACADEMY_LOAN_TEXT = "{name} kiralık oyuncu; akademiye gönderilemez."


class _Unchanged:
    def __repr__(self) -> str:
        return "UNCHANGED"


UNCHANGED: Any = _Unchanged()      # counter: verilmeyen sart aynen kalir (None acik anlam tasir)


class MarketError(TransferError):
    """Menajerler arasi pazar islemi yapilamaz (mesaj Turkce)."""


class FairPlayBlocked(MarketError):
    """
    Adil oyun denetimi anlasmayi engelledi; gerekceler verdict'tedir. accept() engeli HATA olarak firlatmaz (ceza
    yazilmis durumda kalmali, OfferView.status == "BLOCKED"); bu sinif arayuzun engellenmis bir teklifle islem
    denemesini tek tipte gostermesi icin: MarketHub.raise_if_blocked(view).
    """

    def __init__(self, verdict: FairnessVerdict, message: str | None = None) -> None:
        self.verdict = verdict
        super().__init__(message or "Adil oyun denetimi bu anlaşmayı engelledi.")


class LoanError(MarketError):
    """Kiralik islemi yapilamaz."""


class OfferVoided(MarketError):
    """
    Islem sirasinda teklif gecersiz bulundu (oyuncu tasindi, kulup birakildi ...) ve bu islemde VOIDED yazildi.
    Cagiran islemi commit ederse kalici olur; etmezse haftalik tarama yine dusurur.
    """


@dataclass(frozen=True)
class OfferDraft:
    player_id: int
    kind: OfferKind = OfferKind.TRANSFER
    fee: int = 0
    exchange_player_id: int | None = None
    loan_weeks: int | None = None
    loan_wage_share: int = 100
    note: str = ""
    target_team_id: int | None = None      # request_ai_loan: kendi oyuncunu kiralayacak AI kulubu


@dataclass(frozen=True)
class OfferView:
    id: int
    kind: str
    status: str
    status_label: str
    direction: str                    # IN / OUT (yonetici gorunumunde ADMIN)
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
    # --- ekler (B2; varsayilanli) ---
    exchange_player_id: int | None = None
    seller_team_id: int | None = None
    buyer_team_id: int | None = None
    player_position: str = ""
    player_overall: int = 0
    player_value: int = 0
    turn: str | None = None           # BUYER / SELLER / ADMIN / None (market_rules.turn_side)
    contract_opened: bool = False     # sozlesme masasi acildi mi (contract_log "open")
    can_review: bool = False          # yonetici bu teklifi onaylayip reddedebilir mi
    kind_label: str = ""


@dataclass(frozen=True)
class ContractStep:
    status: NegotiationStatus
    message: str
    complaints: tuple[str, ...]
    demand: ContractOffer | None      # OPEN: oyuncunun guncel talebi; ACCEPTED: anlasilan sozlesme
    rounds_left: int
    needs_room: int | None            # ACCEPTED ve maas alani yetmiyor: haftalik eksik (complete(shift_wage_room))


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
    # --- ekler (B2; varsayilanli) ---
    player_id: int | None = None
    parent_team_id: int | None = None
    borrower_team_id: int | None = None
    direction: str = ""               # OUT: kiraliga verdigim / IN: kiraladigim
    start_career_week: int | None = None
    offer_id: int | None = None
    recall_reason: str = ""


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


@dataclass(frozen=True)
class _Deal:
    offer_id: int
    buyer_seat: int
    seller_seat: int
    career_week: int


# ---------------------------------------------------------------------------------------------------------------
# Saf yardimcilar
# ---------------------------------------------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _value(player) -> int:
    if player is None:
        return 0
    return int(player.market_value or finance.market_value(player.overall_rating, player.age, player.position,
                                                           player.potential_rating))


def _ev(value) -> str:
    return str(getattr(value, "value", value))


def _money(amount) -> str:
    return finance.format_money(amount)


def _kind(value, error=MarketError) -> OfferKind:
    try:
        return OfferKind(_ev(value).upper())
    except ValueError:
        raise error("Geçersiz teklif türü.") from None


def _amount(value) -> int:
    if isinstance(value, bool):
        raise MarketError(AMOUNT_INVALID_TEXT)
    if isinstance(value, float) and value != int(value):
        raise MarketError(AMOUNT_INVALID_TEXT)
    try:
        amount = int(value)
    except (TypeError, ValueError):
        raise MarketError(AMOUNT_INVALID_TEXT) from None
    if amount < 0:
        raise MarketError(AMOUNT_NEGATIVE_TEXT)
    return amount


def _share(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise LoanError(SHARE_INVALID_TEXT)
    return value


def _weeks(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LoanError(WEEKS_INVALID_TEXT)
    return value


def _clean_text(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _ban_weeks(player, career_week: int) -> int:
    locked = getattr(player, "transfer_locked_until", None)
    return max(0, int(locked) - int(career_week)) if locked is not None else 0


def _entries(offer, kind: str) -> list[dict]:
    return [e for e in (offer.contract_log or []) if isinstance(e, dict) and e.get("kind") == kind]


def _done_entry(contract_log) -> dict | None:
    found = None
    for entry in contract_log or []:
        if isinstance(entry, dict) and entry.get("kind") == "done":
            found = entry
    return found


def _contract_terms(contract) -> ContractOffer:
    if contract is None:
        raise MarketError(CONTRACT_MISSING_TEXT)
    try:
        wage, years = int(contract.wage), int(contract.years)
        role = SquadRole(_ev(contract.role))
    except (AttributeError, TypeError, ValueError):
        raise MarketError(CONTRACT_INVALID_TEXT) from None
    if wage < 0:
        raise MarketError(CONTRACT_INVALID_TEXT)
    return ContractOffer(wage=wage, years=years, role=role)


def _terms_text(entry: dict) -> str:
    text = _money(int(entry.get("fee") or 0))
    if entry.get("exchange_name"):
        text += f" + takas {entry['exchange_name']}"
    if str(entry.get("offer_kind") or "") == KIND_LOAN:
        weeks = entry.get("loan_weeks")
        text += f", {weeks} hafta" if weeks else ", sezon sonuna kadar"
        text += f", maaşın %{entry.get('share', 100)} payı kiralayanda"
    return text


def loan_guard_reason(player) -> str | None:
    """
    Kiralik oyuncuya yapilamayacak kulup islemleri (akademiye gonderme, satis, yeniden kiralama) icin Turkce neden.
    career_manager.send_to_academy bu kontrolu cagirmali (entegrator degisikligi; bkz. B2 raporu).
    """
    if player is not None and getattr(player, "loan_from_team_id", None) is not None:
        return ACADEMY_LOAN_TEXT.format(name=player.name)
    return None


# ---------------------------------------------------------------------------------------------------------------
# Kontrolcu
# ---------------------------------------------------------------------------------------------------------------

class MarketHub:
    """Oturumdaki menajerin (cm.acting_seat) pazar islemleri. Commit ETMEZ."""

    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm
        self.db = cm.db
        self.seats = cm.seats
        self._role_cache: Any = UNCHANGED

    # ================================================================== temel

    @property
    def rules(self):
        return self.cm.rules

    @property
    def career_week(self) -> int:
        return self.cm.career_week

    def _seat_id(self, seat: Seat | None) -> int | None:
        """Koltugun satir id'si; satiri henuz olmayan birincil koltukta satir kurulur."""
        if seat is None:
            return None
        if seat.id is None and seat.is_primary:
            seat = self.seats.ensure_primary_row(self.cm.state.user_id)
        return seat.id

    def _my_team_id(self) -> int | None:
        team = self.cm.user_team
        return team.id if team is not None else None

    def _require_actor(self) -> tuple[Seat, Team]:
        seat = self.cm.acting_seat
        if seat is None:
            raise MarketError(NO_SEAT_TEXT)
        team = self.cm.user_team
        if team is None:
            raise MarketError(NO_TEAM_TEXT)
        if seat.id is None and seat.is_primary:
            seat = self.seats.ensure_primary_row(self.cm.state.user_id)
        return seat, team

    def _require_enabled(self, kind) -> None:
        rules = self.rules
        if not rules.shared:
            raise MarketError(NOT_SHARED_TEXT)
        if _kind(kind) is OfferKind.LOAN:
            if not rules.loans:
                raise LoanError(LOANS_OFF_TEXT)
        elif not rules.human_market:
            raise MarketError(MARKET_OFF_TEXT)

    def _lock(self, model, ids: Iterable[int | None], *, share: bool = False, skip_locked: bool = False) -> list:
        """
        Satirlari id sirasiyla kilitler ve guncel degerleri okur. share: FOR SHARE (okuma kilidi), aksi FOR NO KEY
        UPDATE. Kulup satirlarinin kadro / personel koleksiyonlari ve oyuncunun kulup iliskisi yenilenir.
        """
        keys = sorted({int(i) for i in ids if i is not None})
        if not keys:
            return []
        self.db.flush()
        pk = model.__mapper__.primary_key[0]
        stmt = (select(model).where(pk.in_(keys)).order_by(pk)
                .with_for_update(read=share, key_share=not share, skip_locked=skip_locked)
                .execution_options(populate_existing=True))
        rows = list(self.db.scalars(stmt))
        for row in rows:
            if model is Team:
                self.db.expire(row, list(TEAM_COLLECTIONS))
            elif model is Player:
                self.db.expire(row, ["team"])
        return rows

    def _expire_teams(self, *teams: Team | None) -> None:
        self.db.flush()
        for team in teams:
            if team is not None:
                self.db.expire(team, list(TEAM_COLLECTIONS))

    def _offer_row(self, offer_id, *, lock: bool = True) -> TransferOffer:
        if not _is_id(offer_id):
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        if lock:
            rows = self._lock(TransferOffer, [offer_id])
            offer = rows[0] if rows else None
        else:
            offer = self.db.get(TransferOffer, offer_id)
        if offer is None:
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        return offer

    def _lock_offer_group(self, offer_id) -> TransferOffer:
        """Teklif + ayni oyuncu / takas oyuncusu uzerindeki acik teklifler, artan id sirasiyla kilitlenir."""
        if not _is_id(offer_id):
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        self.db.flush()
        snap = self.db.execute(select(TransferOffer.player_id, TransferOffer.exchange_player_id)
                               .where(TransferOffer.id == offer_id)).first()
        if snap is None:
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        pids = {snap.player_id, snap.exchange_player_id} - {None}
        wanted = sorted(pids)
        related = set(self.db.scalars(select(TransferOffer.id).where(
            TransferOffer.status.in_(OPEN_VALUES),
            or_(TransferOffer.player_id.in_(wanted), TransferOffer.exchange_player_id.in_(wanted)))))
        rows = self._lock(TransferOffer, related | {offer_id})
        offer = next((o for o in rows if o.id == offer_id), None)
        if offer is None:
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        if {offer.player_id, offer.exchange_player_id} - {None} != pids:
            raise MarketError(BUSY_TEXT)
        return offer

    @staticmethod
    def _side(offer: TransferOffer, team_id: int | None) -> str | None:
        if team_id is None:
            return None
        if offer.buyer_team_id == team_id:
            return BUYER
        if offer.seller_team_id == team_id:
            return SELLER
        return None

    def _party_offer(self, offer_id) -> tuple[TransferOffer, Seat, Team, str]:
        seat, team = self._require_actor()
        offer = self._offer_row(offer_id)
        side = self._side(offer, team.id)
        if side is None:
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        return offer, seat, team, side

    @staticmethod
    def _check_transition(offer: TransferOffer, action: OfferAction, side: str, error=MarketError) -> None:
        try:
            market_rules.next_state(offer.status, action, side, offer.round)
        except OfferStateError as exc:
            raise error(str(exc)) from exc

    def _transition(self, offer: TransferOffer, action: OfferAction, side: str) -> OfferStatus:
        try:
            status, rnd = market_rules.next_state(offer.status, action, side, offer.round)
        except OfferStateError as exc:
            raise MarketError(str(exc)) from exc
        offer.status, offer.round = status.value, rnd
        offer.updated_at = _now()
        return status

    def _log(self, offer: TransferOffer, entry: dict) -> None:
        offer.contract_log = [*(offer.contract_log or []), {"cw": self.career_week, **entry}]

    def _event_entry(self, offer: TransferOffer, action: str, side: str, **extra) -> None:
        exchange = self.db.get(Player, offer.exchange_player_id) if offer.exchange_player_id else None
        self._log(offer, {
            "kind": "event", "action": action, "side": side, "offer_kind": offer.kind, "fee": int(offer.fee or 0),
            "exchange_player_id": offer.exchange_player_id, "exchange_name": exchange.name if exchange else None,
            "loan_weeks": offer.loan_weeks, "share": int(offer.loan_wage_share if offer.loan_wage_share is not None
                                                         else 100),
            **{k: v for k, v in extra.items() if v not in (None, "")},
        })

    def _player_name(self, offer: TransferOffer) -> str:
        player = self.db.get(Player, offer.player_id)
        return player.name if player is not None else "Oyuncu"

    def _team_name(self, team_id: int | None) -> str | None:
        team = self.db.get(Team, team_id) if team_id is not None else None
        return team.name if team is not None else None

    # ================================================================== bildirim / olay

    def _notify(self, seat_ids: Iterable[int | None], kind: NotificationKind, text: str,
                ref_type: str | None = OFFER_REF, ref_id: int | None = None) -> None:
        ids = [i for i in dict.fromkeys(seat_ids) if i is not None]
        if not ids or not text:
            return
        self.db.flush()                               # gercek yazma hatalari burada yuzeye cikar, yutulmaz
        try:
            with self.db.begin_nested():
                for seat_id in ids:
                    messaging.notify(self.db, seat_id, kind, text, ref_type, ref_id)
        except (SQLAlchemyError, ValueError) as exc:
            log.warning("Pazar bildirimi yazılamadı: %s", exc)

    def _notify_parties(self, offer: TransferOffer, kind: NotificationKind, text: str,
                        exclude: int | None = None) -> None:
        seats = [s for s in (offer.created_by_manager_id, offer.responded_by_manager_id) if s != exclude]
        self._notify(seats, kind, text, OFFER_REF, offer.id)

    def _world_id(self) -> int | None:
        schema = database.current_career_schema() or database.LEGACY_CAREER_SCHEMA
        return self.db.scalar(select(World.id).where(World.schema_name == schema))

    def _role(self) -> str | None:
        """Oynatan menajerin dunya rolu (OWNER / ADMIN / MEMBER); kayitsiz eski kariyerde birincil koltuk OWNER."""
        if self._role_cache is not UNCHANGED:
            return self._role_cache
        uid = self.cm.manager_user_id
        if uid is None:
            uid = self.cm.state.user_id
        world_id = self._world_id()
        role = None
        if world_id is not None and uid is not None:
            role = self.db.scalar(
                select(WorldMembership.role).join(World, World.id == WorldMembership.world_id).where(
                    WorldMembership.world_id == world_id, WorldMembership.user_id == uid,
                    WorldMembership.status == MembershipStatus.ACTIVE.value,
                    World.status == WorldStatus.ACTIVE.value))
        elif world_id is None and self.cm.manager_user_id is None:
            role = "OWNER"
        self._role_cache = role
        return role

    def is_admin(self) -> bool:
        return (self._role() or "") in ADMIN_ROLES

    def _admin_seat_ids(self) -> list[int]:
        world_id = self._world_id()
        if world_id is None:
            primary = self.seats.primary()
            return [primary.id] if primary.id is not None else []
        user_ids = self.db.scalars(select(WorldMembership.user_id).where(
            WorldMembership.world_id == world_id, WorldMembership.role.in_(sorted(ADMIN_ROLES)),
            WorldMembership.status == MembershipStatus.ACTIVE.value))
        ids = []
        for uid in user_ids:
            seat = self.seats.resolve(uid)
            if seat is not None and seat.id is not None:
                ids.append(seat.id)
        return ids

    def _world_event(self, kind: WorldEventKind, payload: dict) -> None:
        st = self.cm.state
        seat = self.cm.acting_seat
        self.db.add(WorldEvent(season=int(st.season), week=int(st.current_week), career_week=self.career_week,
                               kind=kind.value, actor_manager_id=seat.id if seat is not None else None,
                               payload=payload))

    def _require_admin(self) -> None:
        if not self.is_admin():
            raise MarketError(ADMIN_ONLY_TEXT)

    def _require_admin_on(self, offer: TransferOffer) -> None:
        self._require_admin()
        team_id = self._my_team_id()
        seat = self.cm.acting_seat
        seat_id = seat.id if seat is not None else None
        if (team_id is not None and team_id in (offer.buyer_team_id, offer.seller_team_id)) or (
                seat_id is not None and seat_id in (offer.created_by_manager_id, offer.responded_by_manager_id)):
            raise MarketError(OWN_DEAL_TEXT)

    def _can_review(self, offer: TransferOffer) -> bool:
        if offer.status != OfferStatus.REVIEW.value:
            return False
        try:
            self._require_admin_on(offer)
        except MarketError:
            return False
        return True

    # ================================================================== dogrulama

    def _senior_counts(self, team_id: int | None) -> tuple[int, dict[str, int]]:
        if team_id is None:
            return 0, {}
        rows = self.db.execute(select(Player.position, func.count()).where(
            Player.team_id == team_id, Player.in_academy.is_(False)).group_by(Player.position)).all()
        by_position = {_ev(position): int(count) for position, count in rows}
        return sum(by_position.values()), by_position

    def _facts(self, *, kind, player: Player, seller_id: int | None, buyer: Team, fee: int,
               exchange: Player | None = None, loan_weeks: int | None = None, share: int = 100,
               humans: frozenset[int] | None = None, wage_room: bool = False) -> OfferFacts:
        humans = humans if humans is not None else self.cm.human_team_ids()
        cw = self.career_week
        kind = _kind(kind)
        self.db.flush()
        seller_total, seller_positions = self._senior_counts(seller_id)
        _buyer_total, buyer_positions = self._senior_counts(buyer.id) if exchange is not None else (0, {})
        position = _ev(player.position)
        ex_position = _ev(exchange.position) if exchange is not None else ""
        return OfferFacts(
            kind=kind, player_id=player.id, player_team_id=player.team_id, seller_team_id=seller_id,
            buyer_team_id=buyer.id, fee=int(fee),
            player_value=_value(player), player_name=player.name, player_position=position,
            player_on_loan=player.loan_from_team_id is not None, player_in_academy=bool(player.in_academy),
            player_ban_weeks=_ban_weeks(player, cw), player_wage=int(player.current_wage or 0),
            exchange_player_id=exchange.id if exchange is not None else None,
            exchange_player_team_id=exchange.team_id if exchange is not None else None,
            exchange_value=_value(exchange), exchange_name=exchange.name if exchange is not None else "",
            exchange_position=ex_position,
            exchange_on_loan=exchange is not None and exchange.loan_from_team_id is not None,
            exchange_in_academy=bool(exchange.in_academy) if exchange is not None else False,
            exchange_ban_weeks=_ban_weeks(exchange, cw) if exchange is not None else 0,
            loan_weeks=loan_weeks, loan_wage_share=int(share),
            buyer_transfer_budget=int(buyer.transfer_budget),
            buyer_free_wage=int(buyer.free_wage) if (kind is OfferKind.LOAN and wage_room) else None,
            buyer_is_human=buyer.id in humans, seller_is_human=seller_id in humans,
            seller_squad_size=seller_total if seller_id is not None else None,
            seller_position_count=seller_positions.get(position, 0) if seller_id is not None else None,
            buyer_position_count=buyer_positions.get(ex_position, 0) if exchange is not None else None,
            squad_floor=SQUAD_FLOOR,
            player_position_floor=POSITION_SALE_FLOOR.get(player.position, 0),
            exchange_position_floor=POSITION_SALE_FLOOR.get(exchange.position, 0) if exchange is not None else 0,
        )

    @staticmethod
    def _require_valid(facts: OfferFacts, extra: Iterable[str | None] = ()) -> None:
        problems = market_rules.validate_offer(facts) + [p for p in extra if p]
        if problems:
            error = LoanError if facts.is_loan else MarketError
            raise error(" ".join(problems[:3]))

    def _exchange_refusal(self, exchange: Player | None, seller: Team | None) -> str | None:
        if exchange is None or seller is None:
            return None
        check = check_interest(exchange.overall_rating, seller.reputation, self.cm.manager_reputation_for(seller))
        if check.interested:
            return None
        return f"{exchange.name} {seller.name} kulübüne gitmek istemiyor: \"{check.reason}\""

    def _season_end_career_week(self) -> int:
        st = self.cm.state
        weeks = max(self.cm.league_weeks(), self.cm.tournaments.projected_last_week())
        return int(st.career_week_offset or 0) + int(weeks) + 1

    def _loan_period(self, weeks: int | None) -> tuple[int, int]:
        start = self.career_week
        if self.cm.season_finished:
            raise LoanError(SEASON_OVER_LOAN_TEXT)
        season_end = self._season_end_career_week()
        end = loan_rules.loan_end_week(start, weeks, season_end)
        if end - start < market_rules.MIN_LOAN_WEEKS:
            raise LoanError(LOAN_TOO_SHORT_TEXT.format(weeks=max(0, season_end - start),
                                                       min=market_rules.MIN_LOAN_WEEKS))
        return start, end

    def _void_reason(self, offer: TransferOffer, player: Player | None = None,
                     exchange: Player | None = None) -> str | None:
        """Teklifi gecersiz kilan durum (Turkce) ya da None. Yalnizca acik teklifler icin anlamli."""
        if offer.seller_team_id is None or offer.buyer_team_id is None:
            return CLUB_MISSING_TEXT
        seller_seat = self.seats.by_team(offer.seller_team_id)
        if seller_seat is None:
            return SELLER_LEFT_TEXT
        buyer_seat = self.seats.by_team(offer.buyer_team_id)
        if buyer_seat is None:
            return BUYER_LEFT_TEXT
        if (offer.responded_by_manager_id is not None and seller_seat.id is not None
                and seller_seat.id != offer.responded_by_manager_id) or (
                offer.created_by_manager_id is not None and buyer_seat.id is not None
                and buyer_seat.id != offer.created_by_manager_id):
            return MANAGER_CHANGED_TEXT
        player = player if player is not None else self.db.get(Player, offer.player_id)
        if player is None:
            return PLAYER_NOT_FOUND_TEXT
        if player.team_id != offer.seller_team_id:
            seller = self._team_name(offer.seller_team_id) or "satıcı"
            return f"{player.name} artık {seller} kulübünde değil; teklif geçersiz."
        if player.loan_from_team_id is not None:
            return f"{player.name} kiralık oyuncu; teklif geçersiz."
        if offer.exchange_player_id is not None:
            exchange = exchange if exchange is not None else self.db.get(Player, offer.exchange_player_id)
            if exchange is None or exchange.team_id != offer.buyer_team_id or exchange.loan_from_team_id is not None:
                return EXCHANGE_MOVED_TEXT
        return None

    def _void(self, offer: TransferOffer, reason: str) -> None:
        self._transition(offer, OfferAction.VOID, SYSTEM)
        offer.reason = reason[:REASON_MAX]
        self._event_entry(offer, OfferAction.VOID.value, SYSTEM, reason=reason)
        self.db.flush()
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"Teklif geçersiz sayıldı ({self._player_name(offer)}): {reason}")

    def _raise_if_void(self, offer: TransferOffer, player: Player | None, exchange: Player | None = None) -> None:
        reason = self._void_reason(offer, player, exchange)
        if reason is None:
            return
        self._void(offer, reason)
        raise OfferVoided(reason)

    def _void_where(self, condition, reason: str, *, skip_locked: bool) -> list[TransferOffer]:
        self.db.flush()
        rows = list(self.db.scalars(
            select(TransferOffer).where(TransferOffer.status.in_(OPEN_VALUES), condition)
            .order_by(TransferOffer.id).with_for_update(key_share=True, skip_locked=skip_locked)
            .execution_options(populate_existing=True)))
        for offer in rows:
            self._void(offer, reason)
        return rows

    def _void_player_offers(self, player_ids: Iterable[int | None], reason_for, *, skip_locked: bool = True) -> None:
        ids = sorted({int(i) for i in player_ids if i is not None})
        for pid in ids:
            player = self.db.get(Player, pid)
            reason = reason_for(player)
            self._void_where(or_(TransferOffer.player_id == pid, TransferOffer.exchange_player_id == pid), reason,
                             skip_locked=skip_locked)

    # ================================================================== adil oyun

    def _account_age_days(self, seat: Seat) -> int:
        if seat.user_id is None:
            return DEFAULT_ACCOUNT_AGE_DAYS
        created = self.db.scalar(select(User.created_at).where(User.id == seat.user_id))
        if created is None:
            return DEFAULT_ACCOUNT_AGE_DAYS
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0, (_now() - created).days)

    def _deal_history(self, exclude_offer_id: int | None = None) -> list[_Deal]:
        cw = self.career_week
        horizon = cw - max(fair_play.PAIR_WINDOW_WEEKS, fair_play.BURST_WINDOW_WEEKS) - HISTORY_SLACK_WEEKS
        rows = self.db.execute(select(
            TransferOffer.id, TransferOffer.created_by_manager_id, TransferOffer.responded_by_manager_id,
            TransferOffer.created_career_week, TransferOffer.contract_log,
        ).where(TransferOffer.status.in_(DEAL_VALUES), TransferOffer.created_by_manager_id.isnot(None),
                TransferOffer.responded_by_manager_id.isnot(None), TransferOffer.created_career_week > horizon))
        deals = []
        for offer_id, buyer_seat, seller_seat, created, contract_log in rows:
            if offer_id == exclude_offer_id:
                continue
            done = _done_entry(contract_log)
            week = int(done.get("cw", created)) if done else int(created)
            deals.append(_Deal(offer_id, int(buyer_seat), int(seller_seat), week))
        return deals

    def _deal_facts(self, *, kind, fee: int, player: Player, exchange: Player | None, share: int | None,
                    weeks: int | None, buyer_seat: Seat, seller_seat: Seat,
                    exclude_offer_id: int | None = None) -> DealFacts:
        cw = self.career_week
        loan = _kind(kind) is OfferKind.LOAN
        buyer_id, seller_id = buyer_seat.id or 0, seller_seat.id or 0
        history = self._deal_history(exclude_offer_id)
        pair = sum(1 for d in history if {d.buyer_seat, d.seller_seat} == {buyer_id, seller_id}
                   and d.career_week > cw - fair_play.PAIR_WINDOW_WEEKS)

        def recent(seat_id: int) -> int:
            return sum(1 for d in history if seat_id in (d.buyer_seat, d.seller_seat)
                       and d.career_week > cw - fair_play.BURST_WINDOW_WEEKS)

        return DealFacts(
            kind=_kind(kind).value, fee=int(fee), player_value=_value(player),
            player_overall=int(player.overall_rating), player_age=int(player.age),
            exchange_value=_value(exchange) if exchange is not None else 0,
            loan_wage_share=int(share if share is not None else 100) if loan else None,
            loan_weeks=weeks if loan else None,
            buyer_seat_id=buyer_id, seller_seat_id=seller_id,
            buyer_account_age_days=self._account_age_days(buyer_seat),
            seller_account_age_days=self._account_age_days(seller_seat),
            buyer_seat_weeks=max(0, cw - int(buyer_seat.joined_career_week)),
            seller_seat_weeks=max(0, cw - int(seller_seat.joined_career_week)),
            pair_deals_recent=pair, buyer_deals_recent=recent(buyer_id), seller_deals_recent=recent(seller_id),
            buyer_fair_play=float(buyer_seat.fair_play), seller_fair_play=float(seller_seat.fair_play),
        )

    def _evaluate_offer(self, offer: TransferOffer, player: Player, exchange: Player | None) -> FairnessVerdict:
        buyer_seat = self.seats.by_id(offer.created_by_manager_id) if offer.created_by_manager_id else None
        seller_seat = self.seats.by_id(offer.responded_by_manager_id) if offer.responded_by_manager_id else None
        if buyer_seat is None or seller_seat is None:
            raise OfferVoided(MANAGER_CHANGED_TEXT)
        facts = self._deal_facts(kind=offer.kind, fee=int(offer.fee), player=player, exchange=exchange,
                                 share=offer.loan_wage_share, weeks=offer.loan_weeks, buyer_seat=buyer_seat,
                                 seller_seat=seller_seat, exclude_offer_id=offer.id)
        return fair_play.evaluate(facts, self.rules.fairness_strictness)

    def _fair_play(self, seat_ids: Iterable[int | None], key: str, offer_id: int | None) -> None:
        delta = fair_play.FAIR_PLAY_DELTAS[key]
        reason = fair_play.FAIR_PLAY_REASONS[key]
        for seat_id in sorted({i for i in seat_ids if i is not None}):     # sabit kilit sirasi (artan id)
            try:
                self.seats.adjust_fair_play(seat_id, delta, reason, offer_id)
            except SeatError as exc:
                log.warning("Adil oyun puanı yazılamadı (koltuk %s): %s", seat_id, exc)

    @staticmethod
    def _verdict(offer: TransferOffer) -> FairnessVerdict | None:
        entries = _entries(offer, "fairness")
        if not entries:
            return None
        e = entries[-1]
        try:
            decision = Decision(e.get("decision"))
        except ValueError:
            return None
        return FairnessVerdict(float(e.get("score") or 0.0), decision, tuple(e.get("reasons") or ()),
                               tuple(e.get("flags") or ()), float(e.get("review_at") or 0.0),
                               float(e.get("block_at") or 0.0))

    @staticmethod
    def raise_if_blocked(view: OfferView) -> None:
        """Arayuz kolayligi: BLOCKED teklif icin FairPlayBlocked (verdict ile)."""
        if view.status == OfferStatus.BLOCKED.value and view.fairness is not None:
            raise FairPlayBlocked(view.fairness, view.reason or None)

    # ================================================================== gorunumler

    def _history(self, offer: TransferOffer) -> tuple[str, ...]:
        lines: list[str] = []
        labels = market_rules.SIDE_LABELS
        for e in offer.contract_log or []:
            if not isinstance(e, dict):
                continue
            prefix = f"{e.get('cw', '?')}. hafta · "
            kind = e.get("kind")
            if kind == "event":
                action, side = e.get("action"), labels.get(e.get("side"), "")
                if action == "OFFER":
                    text = f"{side} teklif etti: {_terms_text(e)}"
                    if e.get("note"):
                        text += f" — Not: {e['note']}"
                elif action == "COUNTER":
                    text = f"{side} karşı teklif yaptı: {_terms_text(e)}"
                elif action == "ACCEPT":
                    text = f"{side} bonservis şartlarını kabul etti"
                elif action == "REJECT":
                    text = f"{side} reddetti" + (f": {e['reason']}" if e.get("reason") else "")
                elif action == "WITHDRAW":
                    text = "Alıcı teklifi geri çekti"
                elif action == "EXPIRE":
                    text = "Teklifin süresi doldu"
                elif action == "VOID":
                    text = f"Geçersiz sayıldı: {e.get('reason', '')}"
                elif action == "BLOCK":
                    text = "Adil oyun denetimi anlaşmayı engelledi"
                elif action == "SEND_REVIEW":
                    text = "Adil oyun: yönetici incelemesine gönderildi"
                elif action == "APPROVE":
                    text = "Yönetici anlaşmayı onayladı"
                elif action == "DENY":
                    text = "Yönetici anlaşmayı reddetti" + (f": {e['reason']}" if e.get("reason") else "")
                elif action == "COMPLETE":
                    text = "Anlaşma tamamlandı"
                elif action == "REVERSE":
                    text = "Yönetici transferi geri aldı" + (f": {e['reason']}" if e.get("reason") else "")
                else:
                    text = str(action)
                lines.append(prefix + text)
            elif kind == "fairness":
                label = fair_play.DECISION_LABELS.get(Decision(e.get("decision", "ALLOW")), "")
                lines.append(prefix + f"Adil oyun: {label} (puan {float(e.get('score') or 0):.0f})")
            elif kind == "open":
                refusal = e.get("refusal")
                lines.append(prefix + (refusal if refusal else "Sözleşme masası açıldı"))
            elif kind == "bid":
                role = transfers_role_label(e.get("role"))
                lines.append(prefix + f"Sözleşme teklifi: {_money(int(e.get('wage') or 0))}/hafta · "
                                      f"{e.get('years')} yıl · {role}")
        return tuple(lines)

    def _view(self, offer: TransferOffer, side: str | None) -> OfferView:
        cw = self.career_week
        status = OfferStatus(offer.status)
        player = self.db.get(Player, offer.player_id)
        exchange = self.db.get(Player, offer.exchange_player_id) if offer.exchange_player_id else None
        allowed = (market_rules.allowed_actions(status, side, offer.round) if side in (BUYER, SELLER)
                   else frozenset())
        is_open = status in market_rules.OPEN_STATUSES
        expires = (max(0, int(offer.expires_career_week) - cw)
                   if is_open and offer.expires_career_week is not None else None)
        try:
            turn = market_rules.turn_side(status, offer.round)
        except OfferStateError:
            turn = None
        can_contract = OfferAction.COMPLETE in allowed
        return OfferView(
            id=offer.id, kind=offer.kind, status=offer.status, status_label=market_rules.STATUS_LABELS[status],
            direction={BUYER: "OUT", SELLER: "IN"}.get(side, "ADMIN"),
            player_id=offer.player_id, player_name=player.name if player is not None else "Oyuncu",
            seller_team=self._team_name(offer.seller_team_id), buyer_team=self._team_name(offer.buyer_team_id),
            fee=int(offer.fee or 0), exchange_player_name=exchange.name if exchange is not None else None,
            loan_weeks=offer.loan_weeks, loan_wage_share=int(offer.loan_wage_share),
            round=int(offer.round or 0), expires_in_weeks=expires,
            can_accept=OfferAction.ACCEPT in allowed, can_reject=OfferAction.REJECT in allowed,
            can_counter=OfferAction.COUNTER in allowed, can_withdraw=OfferAction.WITHDRAW in allowed,
            can_contract=can_contract,
            fairness=self._verdict(offer), reason=offer.reason or "", history=self._history(offer),
            exchange_player_id=offer.exchange_player_id, seller_team_id=offer.seller_team_id,
            buyer_team_id=offer.buyer_team_id,
            player_position=_ev(player.position) if player is not None else "",
            player_overall=int(player.overall_rating) if player is not None else 0,
            player_value=_value(player), turn=turn, contract_opened=bool(_entries(offer, "open")),
            can_review=self._can_review(offer) if side is None else False,
            kind_label=market_rules.KIND_LABELS.get(OfferKind(offer.kind), offer.kind),
        )

    def _box(self, column, side: str) -> list[OfferView]:
        team_id = self._my_team_id()
        if team_id is None:
            return []
        self.db.flush()
        rows = self.db.scalars(
            select(TransferOffer).where(column == team_id)
            .order_by(case((TransferOffer.status.in_(OPEN_VALUES), 0), else_=1), TransferOffer.id.desc())
            .limit(LIST_LIMIT))
        return [self._view(offer, side) for offer in rows]

    def inbox(self) -> list[OfferView]:
        """Kulubume gelen teklifler (satici oldugum): acik olanlar once, sonra en yeni."""
        return self._box(TransferOffer.seller_team_id, SELLER)

    def outbox(self) -> list[OfferView]:
        """Kulubumun yaptigi teklifler (alici oldugum)."""
        return self._box(TransferOffer.buyer_team_id, BUYER)

    def offer(self, offer_id: int) -> OfferView:
        """Tek teklif: tarafi oldugum ya da yoneticisi oldugum dunyadaki teklif."""
        offer = self._offer_row(offer_id, lock=False)
        side = self._side(offer, self._my_team_id())
        if side is None and not self.is_admin():
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        return self._view(offer, side)

    def counts(self) -> InboxCounts:
        team_id = self._my_team_id()
        offers_in = offers_action = 0
        if team_id is not None:
            self.db.flush()
            rows = self.db.execute(select(TransferOffer.seller_team_id, TransferOffer.buyer_team_id,
                                          TransferOffer.status, TransferOffer.round).where(
                TransferOffer.status.in_(OPEN_VALUES),
                or_(TransferOffer.seller_team_id == team_id, TransferOffer.buyer_team_id == team_id))).all()
            for _seller_id, buyer_id, status, rnd in rows:
                side = BUYER if buyer_id == team_id else SELLER
                if side == SELLER:
                    offers_in += 1
                try:
                    if market_rules.turn_side(status, rnd) == side:
                        offers_action += 1
                except OfferStateError:
                    continue
        seat = self.cm.acting_seat
        box = messaging.Messaging(self.db, seat if seat is not None and seat.id is not None else None)
        reviews = 0
        if self.is_admin():
            reviews = int(self.db.scalar(select(func.count()).select_from(TransferOffer).where(
                TransferOffer.status == OfferStatus.REVIEW.value)) or 0)
        return InboxCounts(offers_in=offers_in, offers_action=offers_action, messages=box.unread_messages(),
                           notifications=box.unread_notifications(), reviews=reviews)

    def player_status(self, player_id: int) -> PlayerMarketStatus:
        player = self.db.get(Player, player_id) if _is_id(player_id) else None
        if player is None:
            raise MarketError(PLAYER_NOT_FOUND_TEXT)
        seat = self.seats.by_team(player.team_id) if player.team_id is not None else None
        block = self.cm.transfer_block_reason(player)
        if block is None and player.in_academy:
            block = f"{player.name} akademide; akademi oyuncuları satılık ya da kiralık değil."
        team_id = self._my_team_id()
        self.db.flush()
        mine = None
        if team_id is not None:
            mine = self.db.scalar(select(TransferOffer.id).where(
                TransferOffer.player_id == player.id, TransferOffer.buyer_team_id == team_id,
                TransferOffer.status.in_(OPEN_VALUES)).order_by(TransferOffer.id.desc()).limit(1))
        open_count = int(self.db.scalar(select(func.count()).select_from(TransferOffer).where(
            TransferOffer.player_id == player.id, TransferOffer.status.in_(OPEN_VALUES))) or 0)
        return PlayerMarketStatus(owner_is_human=seat is not None,
                                  seller_seat_name=seat.display_name if seat is not None else None,
                                  on_loan=player.loan_from_team_id is not None, block_reason=block,
                                  my_open_offer_id=mine, open_offers=open_count)

    def exchange_candidates(self, offer_id: int | None = None) -> list[Player]:
        """
        Takas verilebilecek oyuncular: kendi A takimim (kiralik / akademi / transfer yasagi haric). offer_id verilir ve
        ben saticiysam: alicinin oyunculari (karsi teklifte takas istemek icin).
        """
        _seat, team = self._require_actor()
        target, exclude = team.id, None
        if offer_id is not None:
            offer = self._offer_row(offer_id, lock=False)
            side = self._side(offer, team.id)
            if side is None:
                raise MarketError(OFFER_NOT_FOUND_TEXT)
            target = offer.buyer_team_id
            exclude = offer.player_id
        cw = self.career_week
        self.db.flush()
        stmt = (select(Player).where(
            Player.team_id == target, Player.in_academy.is_(False), Player.loan_from_team_id.is_(None),
            or_(Player.transfer_locked_until.is_(None), Player.transfer_locked_until <= cw))
            .order_by(Player.overall_rating.desc(), Player.id))
        if exclude is not None:
            stmt = stmt.where(Player.id != exclude)
        return list(self.db.scalars(stmt))

    def preview_fairness(self, draft: OfferDraft) -> FairnessVerdict:
        """Teklif yapmadan adil oyun onizlemesi (yazma yok)."""
        kind = _kind(draft.kind)
        self._require_enabled(kind)
        seat = self.cm.acting_seat
        team = self.cm.user_team
        if seat is None:
            raise MarketError(NO_SEAT_TEXT)
        if team is None:
            raise MarketError(NO_TEAM_TEXT)
        player = self.db.get(Player, draft.player_id) if _is_id(draft.player_id) else None
        if player is None:
            raise MarketError(PLAYER_NOT_FOUND_TEXT)
        if player.team_id == team.id:
            raise MarketError(OWN_PLAYER_TEXT.format(name=player.name))
        seller_seat = self.seats.by_team(player.team_id) if player.team_id is not None else None
        if seller_seat is None:
            seller_name = self._team_name(player.team_id) or "Kulüp"
            text = AI_LOAN_TEXT if kind is OfferKind.LOAN else AI_TRANSFER_TEXT
            raise MarketError(text.format(team=seller_name))
        exchange = None
        if draft.exchange_player_id is not None:
            exchange = self.db.get(Player, draft.exchange_player_id) if _is_id(draft.exchange_player_id) else None
            if exchange is None:
                raise MarketError(EXCHANGE_NOT_FOUND_TEXT)
        facts = self._deal_facts(kind=kind, fee=_amount(draft.fee), player=player, exchange=exchange,
                                 share=draft.loan_wage_share, weeks=draft.loan_weeks, buyer_seat=seat,
                                 seller_seat=seller_seat)
        return fair_play.evaluate(facts, self.rules.fairness_strictness)

    # ================================================================== teklif akisi

    def make_offer(self, draft: OfferDraft) -> OfferView:
        kind = _kind(draft.kind)
        self._require_enabled(kind)
        seat, team = self._require_actor()
        fee = _amount(draft.fee)
        note = _clean_text(draft.note, NOTE_MAX)
        loan = kind is OfferKind.LOAN
        share = _share(draft.loan_wage_share) if loan else 100
        weeks = _weeks(draft.loan_weeks)
        if loan and draft.exchange_player_id is not None:
            raise LoanError(LOAN_EXCHANGE_TEXT)
        if not _is_id(draft.player_id):
            raise MarketError(PLAYER_NOT_FOUND_TEXT)
        if draft.exchange_player_id is not None and not _is_id(draft.exchange_player_id):
            raise MarketError(EXCHANGE_NOT_FOUND_TEXT)
        locked = {p.id: p for p in self._lock(Player, [draft.player_id, draft.exchange_player_id], share=True)}
        player = locked.get(draft.player_id)
        if player is None:
            raise MarketError(PLAYER_NOT_FOUND_TEXT)
        exchange = locked.get(draft.exchange_player_id) if draft.exchange_player_id is not None else None
        if draft.exchange_player_id is not None and exchange is None:
            raise MarketError(EXCHANGE_NOT_FOUND_TEXT)
        if player.team_id == team.id:
            raise MarketError(OWN_PLAYER_TEXT.format(name=player.name))
        if player.team_id is None:
            raise MarketError(NO_CLUB_PLAYER_TEXT.format(name=player.name))
        seller = self.db.get(Team, player.team_id)
        seller_seat = self.seats.by_team(seller.id)
        if seller_seat is None:
            raise (LoanError if loan else MarketError)(
                (AI_LOAN_TEXT if loan else AI_TRANSFER_TEXT).format(team=seller.name))
        seller_seat_id = self._seat_id(seller_seat)
        facts = self._facts(kind=kind, player=player, seller_id=seller.id, buyer=team, fee=fee, exchange=exchange,
                            loan_weeks=weeks, share=share)
        self._require_valid(facts, [self._exchange_refusal(exchange, seller)])
        if loan:
            self._loan_period(weeks)
        cw = self.career_week
        offer = TransferOffer(
            season=int(self.cm.season), created_career_week=cw,
            expires_career_week=market_rules.expires_at(cw, self.rules), kind=kind.value, player_id=player.id,
            seller_team_id=seller.id, buyer_team_id=team.id, fee=fee,
            exchange_player_id=exchange.id if exchange is not None else None,
            loan_weeks=weeks if loan else None, loan_wage_share=share, status=OfferStatus.PENDING.value, round=0,
            created_by_manager_id=seat.id, responded_by_manager_id=seller_seat_id,
            fairness_flags=[], contract_log=[], transfer_log_ids=[], updated_at=_now(),
        )
        self._log(offer, {"kind": "event", "action": "OFFER", "side": BUYER, "offer_kind": kind.value, "fee": fee,
                          "exchange_player_id": offer.exchange_player_id,
                          "exchange_name": exchange.name if exchange is not None else None,
                          "loan_weeks": offer.loan_weeks, "share": share, **({"note": note} if note else {})})
        try:
            with self.db.begin_nested():
                self.db.add(offer)
                self.db.flush()
        except IntegrityError as exc:
            raise (LoanError if loan else MarketError)(DUPLICATE_TEXT.format(name=player.name)) from exc
        what = "kiralık" if loan else "transfer"
        self._notify([seller_seat_id], NotificationKind.OFFER_IN,
                     f"{team.name}, {player.name} için {what} teklifi yaptı: {_terms_text(offer.contract_log[-1])}.",
                     OFFER_REF, offer.id)
        return self._view(offer, BUYER)

    def counter(
        self,
        offer_id: int,
        *,
        fee: int,
        exchange_player_id: int | None = UNCHANGED,
        loan_weeks: int | None = UNCHANGED,
        loan_wage_share: int | None = None,
    ) -> OfferView:
        """
        Karsi teklif (sirasi gelen taraf). Verilmeyen exchange_player_id / loan_weeks aynen kalir; None acik anlam
        tasir (takas yok / sezon sonuna kadar). loan_wage_share None: aynen kalir.
        """
        offer, seat, team, side = self._party_offer(offer_id)
        kind = _kind(offer.kind)
        loan = kind is OfferKind.LOAN
        self._require_enabled(kind)
        self._check_transition(offer, OfferAction.COUNTER, side)
        new_fee = _amount(fee)
        new_exchange = offer.exchange_player_id if exchange_player_id is UNCHANGED else exchange_player_id
        new_weeks = offer.loan_weeks if loan_weeks is UNCHANGED else _weeks(loan_weeks)
        new_share = int(offer.loan_wage_share) if loan_wage_share is None else _share(loan_wage_share)
        if new_exchange is not None and not _is_id(new_exchange):
            raise MarketError(EXCHANGE_NOT_FOUND_TEXT)
        if loan and new_exchange is not None:
            raise LoanError(LOAN_EXCHANGE_TEXT)
        if not loan:
            new_weeks, new_share = None, 100
        locked = {p.id: p for p in self._lock(Player, [offer.player_id, offer.exchange_player_id, new_exchange],
                                               share=True)}
        player = locked.get(offer.player_id)
        self._raise_if_void(offer, player, locked.get(offer.exchange_player_id))
        exchange = locked.get(new_exchange) if new_exchange is not None else None
        if new_exchange is not None and exchange is None:
            raise MarketError(EXCHANGE_NOT_FOUND_TEXT)
        buyer = self.db.get(Team, offer.buyer_team_id)
        seller = self.db.get(Team, offer.seller_team_id)
        facts = self._facts(kind=kind, player=player, seller_id=seller.id, buyer=buyer, fee=new_fee,
                            exchange=exchange, loan_weeks=new_weeks, share=new_share)
        self._require_valid(facts, [self._exchange_refusal(exchange, seller)])
        if loan:
            self._loan_period(new_weeks)
        self._transition(offer, OfferAction.COUNTER, side)
        offer.fee, offer.exchange_player_id = new_fee, new_exchange
        offer.loan_weeks, offer.loan_wage_share = new_weeks, new_share
        offer.expires_career_week = market_rules.expires_at(self.career_week, self.rules)
        offer.reason = None
        self._event_entry(offer, OfferAction.COUNTER.value, side)
        self.db.flush()
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"{team.name} karşı teklif yaptı ({player.name}): "
                             f"{_terms_text(offer.contract_log[-1])}.", exclude=seat.id)
        return self._view(offer, side)

    def accept(self, offer_id: int) -> OfferView:
        """
        Sirasi gelen taraf bonservis sartlarini kabul eder. Adil oyun burada: BLOCK -> BLOCKED (+ ceza), REVIEW ->
        REVIEW, ALLOW -> CONTRACT. Sonuc her durumda OfferView (status'a bak); kural ihlali MarketError.
        """
        offer, seat, team, side = self._party_offer(offer_id)
        kind = _kind(offer.kind)
        self._require_enabled(kind)
        self._check_transition(offer, OfferAction.ACCEPT, side)
        locked = {p.id: p for p in self._lock(Player, [offer.player_id, offer.exchange_player_id], share=True)}
        player = locked.get(offer.player_id)
        exchange = locked.get(offer.exchange_player_id) if offer.exchange_player_id is not None else None
        self._raise_if_void(offer, player, exchange)
        buyer = self.db.get(Team, offer.buyer_team_id)
        seller = self.db.get(Team, offer.seller_team_id)
        facts = self._facts(kind=kind, player=player, seller_id=seller.id, buyer=buyer, fee=int(offer.fee),
                            exchange=exchange, loan_weeks=offer.loan_weeks, share=int(offer.loan_wage_share))
        self._require_valid(facts, [self._exchange_refusal(exchange, seller)])
        if kind is OfferKind.LOAN:
            self._loan_period(offer.loan_weeks)
        try:
            verdict = self._evaluate_offer(offer, player, exchange)
        except OfferVoided as exc:
            self._void(offer, str(exc))
            raise
        fairness_entry = {"kind": "fairness", "score": verdict.score, "decision": verdict.decision.value,
                          "reasons": list(verdict.reasons), "flags": list(verdict.flags),
                          "review_at": verdict.review_at, "block_at": verdict.block_at}
        parties = (offer.created_by_manager_id, offer.responded_by_manager_id)

        if verdict.decision is Decision.BLOCK:
            self._transition(offer, OfferAction.BLOCK, SYSTEM)
            self._record_fairness(offer, verdict, fairness_entry)
            offer.reason = _clean_text("Adil oyun denetimi engelledi: " + " ".join(verdict.reasons), REASON_MAX)
            self._event_entry(offer, OfferAction.BLOCK.value, SYSTEM)
            self.db.flush()
            self._fair_play(parties, "BLOCKED", offer.id)
            self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                                 f"Anlaşma adil oyun denetiminde engellendi ({player.name}). {offer.reason}")
            return self._view(offer, side)

        if verdict.decision is Decision.REVIEW:
            self._transition(offer, OfferAction.SEND_REVIEW, SYSTEM)
            self._record_fairness(offer, verdict, fairness_entry)
            offer.reason = _clean_text("Yönetici incelemesi: " + " ".join(verdict.reasons), REASON_MAX)
            offer.expires_career_week = market_rules.expires_at(self.career_week, self.rules)
            self._event_entry(offer, OfferAction.ACCEPT.value, side)
            self._event_entry(offer, OfferAction.SEND_REVIEW.value, SYSTEM)
            self.db.flush()
            self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                                 f"Anlaşma yönetici incelemesine gönderildi ({player.name}).")
            admins = [i for i in self._admin_seat_ids() if i not in parties]
            self._notify(admins, NotificationKind.REVIEW,
                         f"İnceleme bekleyen anlaşma: {player.name}, {seller.name} → {buyer.name} "
                         f"({_money(int(offer.fee))}).", OFFER_REF, offer.id)
            return self._view(offer, side)

        try:
            with self.db.begin_nested():
                self._transition(offer, OfferAction.ACCEPT, side)
                self._record_fairness(offer, verdict, fairness_entry)
                offer.expires_career_week = market_rules.expires_at(self.career_week, self.rules)
                offer.reason = None
                self._event_entry(offer, OfferAction.ACCEPT.value, side)
                self.db.flush()
        except IntegrityError as exc:
            raise MarketError(CONTRACT_TAKEN_TEXT.format(name=player.name)) from exc
        next_step = ("kiralığı tamamlayabilir" if kind is OfferKind.LOAN
                     else "oyuncuyla sözleşme masasına oturabilir")
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"{team.name} anlaşmayı kabul etti ({player.name}); alıcı {next_step}.",
                             exclude=seat.id)
        return self._view(offer, side)

    def _record_fairness(self, offer: TransferOffer, verdict: FairnessVerdict, entry: dict) -> None:
        offer.fairness_score = float(verdict.score)
        offer.fairness_flags = list(verdict.flags)
        self._log(offer, entry)

    def reject(self, offer_id: int, reason: str = "") -> OfferView:
        offer, seat, team, side = self._party_offer(offer_id)
        self._check_transition(offer, OfferAction.REJECT, side)
        text = _clean_text(reason, REASON_MAX)
        self._transition(offer, OfferAction.REJECT, side)
        offer.reason = text or None
        self._event_entry(offer, OfferAction.REJECT.value, side, reason=text)
        self.db.flush()
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"{team.name} teklifi reddetti ({self._player_name(offer)})"
                             + (f": {text}" if text else "."), exclude=seat.id)
        return self._view(offer, side)

    def withdraw(self, offer_id: int) -> OfferView:
        offer, seat, team, side = self._party_offer(offer_id)
        self._check_transition(offer, OfferAction.WITHDRAW, side)
        self._transition(offer, OfferAction.WITHDRAW, side)
        self._event_entry(offer, OfferAction.WITHDRAW.value, side)
        self.db.flush()
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"{team.name} teklifini geri çekti ({self._player_name(offer)}).", exclude=seat.id)
        return self._view(offer, side)

    # ================================================================== sozlesme masasi

    def _opening(self, offer: TransferOffer, player: Player, buyer: Team, exchange: Player | None) -> dict:
        self.db.flush()
        ratings = list(self.db.scalars(select(Player.overall_rating).where(
            Player.team_id == buyer.id, Player.in_academy.is_(False),
            Player.id != (exchange.id if exchange is not None else -1))
            .order_by(Player.overall_rating.desc(), Player.id)))
        refusal = market_rules.squad_level_refusal(player.overall_rating, ratings)
        seller = self.db.get(Team, player.team_id)
        return {
            "kind": "open", "cw": self.career_week,
            "player": {"name": player.name, "overall": int(player.overall_rating), "age": int(player.age),
                       "wage": int(player.current_wage or 0),
                       "team_rep": int(seller.reputation) if seller is not None else int(buyer.reputation)},
            "buyer": {"rep": int(buyer.reputation), "ratings": [int(r) for r in ratings]},
            "manager_rep": float(self.cm.manager_reputation_for(buyer)),
            "refusal": (f"{player.name}: \"{refusal}\" — sözleşme masasına oturmadı." if refusal else None),
        }

    @staticmethod
    def _negotiation(offer: TransferOffer, opening: dict) -> ContractNegotiation:
        p, b = opening["player"], opening["buyer"]
        player = SimpleNamespace(id=offer.player_id, name=p["name"], overall_rating=int(p["overall"]),
                                 age=int(p["age"]), current_wage=int(p["wage"]),
                                 team=SimpleNamespace(reputation=int(p["team_rep"])))
        buyer = SimpleNamespace(id=offer.buyer_team_id, reputation=int(b["rep"]),
                                players=[SimpleNamespace(overall_rating=int(r)) for r in b["ratings"]])
        rng = random.Random(market_rules.contract_rng_seed(offer.id, offer.buyer_team_id or 0, offer.player_id))
        negotiation = ContractNegotiation(rng, player, buyer, int(offer.fee),
                                          manager_reputation=float(opening["manager_rep"]))
        if opening.get("refusal") and negotiation.open:
            negotiation.status = NegotiationStatus.WALKED_AWAY
            negotiation.opening_message = opening["refusal"]
        return negotiation

    def _replay(self, offer: TransferOffer):
        """(negotiation, son yanit) ya da (None, None): contract_log'daki masa sirayla yeniden oynatilir."""
        openings = _entries(offer, "open")
        if not openings:
            return None, None
        negotiation = self._negotiation(offer, openings[0])
        last = None
        for entry in _entries(offer, "bid"):
            if not negotiation.open:
                break
            last = negotiation.respond(ContractOffer(wage=int(entry["wage"]), years=int(entry["years"]),
                                                     role=SquadRole(entry["role"])))
        return negotiation, last

    def _needs_room(self, offer: TransferOffer, wage: int) -> int:
        buyer = self.db.get(Team, offer.buyer_team_id)
        freed = 0
        if offer.exchange_player_id is not None:
            exchange = self.db.get(Player, offer.exchange_player_id)
            if exchange is not None and exchange.team_id == buyer.id:
                freed = int(exchange.current_wage or 0)
        return max(0, int(wage) - (int(buyer.free_wage) + freed))

    def _step(self, offer: TransferOffer, negotiation: ContractNegotiation, response) -> ContractStep:
        if not negotiation.open and negotiation.status is NegotiationStatus.WALKED_AWAY and response is None:
            return ContractStep(negotiation.status, negotiation.opening_message or "", (), None, 0, None)
        if response is None:
            message = f"{negotiation.player.name} talebini açıkladı: {negotiation.demand.describe()}"
            complaints: tuple[str, ...] = ()
        else:
            message, complaints = response.message, tuple(response.complaints)
        if negotiation.status is NegotiationStatus.ACCEPTED:
            agreed = negotiation.last_offer
            needs = self._needs_room(offer, agreed.wage)
            return ContractStep(negotiation.status, message, complaints, agreed, negotiation.rounds_left,
                                needs or None)
        demand = negotiation.demand if negotiation.open else None
        return ContractStep(negotiation.status, message, complaints, demand, negotiation.rounds_left, None)

    def _player_walked(self, offer: TransferOffer, message: str) -> None:
        self._transition(offer, OfferAction.REJECT, SYSTEM)
        offer.reason = _clean_text(message, REASON_MAX)
        self._event_entry(offer, OfferAction.REJECT.value, SYSTEM, reason=offer.reason)
        self.db.flush()
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"Transfer iptal ({self._player_name(offer)}): {offer.reason}")

    def _contract_offer(self, offer_id) -> tuple[TransferOffer, Seat, Team]:
        offer, seat, team, side = self._party_offer(offer_id)
        if side != BUYER:
            raise MarketError(BUYER_ONLY_CONTRACT_TEXT)
        if offer.kind == OfferKind.LOAN.value:
            raise LoanError(LOAN_NO_CONTRACT_TEXT)
        self._require_enabled(OfferKind.TRANSFER)
        self._check_transition(offer, OfferAction.COMPLETE, BUYER)
        return offer, seat, team

    def _ensure_opened(self, offer: TransferOffer):
        negotiation, response = self._replay(offer)
        if negotiation is not None:
            return negotiation, response, False
        player = self.db.get(Player, offer.player_id)
        exchange = self.db.get(Player, offer.exchange_player_id) if offer.exchange_player_id else None
        self._raise_if_void(offer, player, exchange)
        buyer = self.db.get(Team, offer.buyer_team_id)
        opening = self._opening(offer, player, buyer, exchange)
        self._log(offer, opening)
        offer.updated_at = _now()
        self.db.flush()
        negotiation = self._negotiation(offer, opening)
        if not negotiation.open:
            self._player_walked(offer, negotiation.opening_message or "")
        return negotiation, None, True

    def open_contract(self, offer_id: int) -> ContractStep:
        """Sozlesme masasi (CONTRACT, alici). Ilk cagri acilis kaydini yazar; sonrakiler kayittan yeniden kurar."""
        offer, _seat, _team = self._contract_offer(offer_id)
        negotiation, response, _opened = self._ensure_opened(offer)
        return self._step(offer, negotiation, response)

    def submit_contract(self, offer_id: int, offer: ContractOffer) -> ContractStep:
        """Oyuncuya sozlesme teklifi: kayda "bid" eklenir; oyuncu masadan kalkarsa teklif REJECTED."""
        contract = _contract_terms(offer)
        row, _seat, _team = self._contract_offer(offer_id)
        negotiation, response, _opened = self._ensure_opened(row)
        if not negotiation.open:
            return self._step(row, negotiation, response)
        player = self.db.get(Player, row.player_id)
        exchange = self.db.get(Player, row.exchange_player_id) if row.exchange_player_id else None
        self._raise_if_void(row, player, exchange)
        response = negotiation.respond(contract)
        self._log(row, {"kind": "bid", "wage": contract.wage, "years": contract.years, "role": contract.role.value})
        row.updated_at = _now()
        self.db.flush()
        if response.status is NegotiationStatus.WALKED_AWAY:
            self._player_walked(row, response.message)
        return self._step(row, negotiation, response)

    # ================================================================== tamamlama

    def _snapshot(self, player: Player | None) -> dict | None:
        if player is None:
            return None
        return {"id": player.id, "team_id": player.team_id, "wage": int(player.current_wage or 0),
                "years": int(player.contract_years), "role": _ev(player.squad_role),
                "locked": player.transfer_locked_until, "last_season": player.last_transfer_season,
                "contract_overall": player.contract_overall}

    def _max_log_id(self) -> int:
        self.db.flush()
        return int(self.db.scalar(select(func.max(TransferLog.id))) or 0)

    def _log_ids_since(self, before: int, player_ids: Iterable[int | None]) -> dict[int, int]:
        self.db.flush()
        ids = [i for i in player_ids if i is not None]
        rows = self.db.execute(select(TransferLog.player_id, TransferLog.id).where(
            TransferLog.id > before, TransferLog.player_id.in_(ids)).order_by(TransferLog.id)).all()
        return {int(pid): int(lid) for pid, lid in rows}

    def complete(self, offer_id: int, contract: ContractOffer | None = None,
                 shift_wage_room: bool = False) -> TransferNews:
        """
        Anlasmayi tamamlar (alici, CONTRACT). TRANSFER: oyuncuyla anlasilan sozlesme (open/submit_contract) gerekir.
        LOAN: contract yok sayilir. shift_wage_room: maas alani yetmiyorsa eksik kadar butce kaydirilir (ayni islem).
        Tum yazmalar tek savepoint'te; hata -> MarketError / LoanError, hicbir sey degismez.
        """
        seat, team = self._require_actor()
        offer = self._lock_offer_group(offer_id)
        side = self._side(offer, team.id)
        if side is None:
            raise MarketError(OFFER_NOT_FOUND_TEXT)
        kind = _kind(offer.kind)
        self._require_enabled(kind)
        self._check_transition(offer, OfferAction.COMPLETE, side, LoanError if kind is OfferKind.LOAN else MarketError)
        players = {p.id: p for p in self._lock(Player, [offer.player_id, offer.exchange_player_id])}
        player = players.get(offer.player_id)
        exchange = players.get(offer.exchange_player_id) if offer.exchange_player_id is not None else None
        self._raise_if_void(offer, player, exchange)
        teams = {t.id: t for t in self._lock(Team, [offer.seller_team_id, offer.buyer_team_id])}
        seller, buyer = teams[offer.seller_team_id], teams[offer.buyer_team_id]
        if kind is OfferKind.LOAN:
            return self._complete_loan(offer, seat, player, seller, buyer, bool(shift_wage_room))

        terms = _contract_terms(contract)
        negotiation, _response = self._replay(offer)
        if negotiation is None:
            raise MarketError(CONTRACT_NOT_OPEN_TEXT)
        if negotiation.status is not NegotiationStatus.ACCEPTED or negotiation.last_offer is None:
            raise MarketError(CONTRACT_NOT_AGREED_TEXT)
        agreed = negotiation.last_offer
        if (agreed.wage, agreed.years, agreed.role) != (terms.wage, terms.years, terms.role):
            raise MarketError(CONTRACT_MISMATCH_TEXT)
        fee = int(offer.fee)
        facts = self._facts(kind=kind, player=player, seller_id=seller.id, buyer=buyer, fee=fee, exchange=exchange)
        self._require_valid(facts, [self._exchange_refusal(exchange, seller)])
        freed = int(exchange.current_wage or 0) if exchange is not None else 0
        needed = int(agreed.wage) - (int(buyer.free_wage) + freed)
        if needed > 0 and not shift_wage_room:
            raise MarketError(WAGE_ROOM_TEXT.format(need=_money(needed),
                                                    cost=_money(finance.weekly_to_transfer(needed))))
        if needed > 0 and int(buyer.transfer_budget) - finance.weekly_to_transfer(needed) < fee:
            raise MarketError(SHIFT_BUDGET_TEXT)

        done = {"kind": "done", "offer_kind": kind.value, "fee": fee, "wage": int(agreed.wage),
                "shift": max(0, needed), "player": self._snapshot(player), "exchange": self._snapshot(exchange),
                "buyer_seat": offer.created_by_manager_id, "seller_seat": offer.responded_by_manager_id,
                "season": int(self.cm.season), "week": int(self.cm.current_week)}
        before = self._max_log_id()
        try:
            with self.db.begin_nested():
                self._transition(offer, OfferAction.COMPLETE, BUYER)
                self.db.flush()
                if needed > 0:
                    self.cm.shift_budget(buyer, needed)
                if exchange is not None:
                    self._move_exchange(exchange, buyer, seller)
                    self._expire_teams(buyer, seller)
                news = self.cm.complete_transfer(buyer, player, fee, agreed, expected_seller_id=seller.id,
                                                 human_deal=True)
                player.transfer_listed = player.loan_listed = False
                logs = self._log_ids_since(before, [player.id, exchange.id if exchange is not None else None])
                done["log_ids"] = {"player": logs.get(player.id),
                                   "exchange": logs.get(exchange.id) if exchange is not None else None}
                offer.transfer_log_ids = [i for i in (done["log_ids"]["player"], done["log_ids"]["exchange"])
                                          if i is not None]
                self._log(offer, done)
                self._event_entry(offer, OfferAction.COMPLETE.value, BUYER)
                self._void_player_offers(
                    [player.id, exchange.id if exchange is not None else None],
                    lambda p: f"{p.name if p else 'Oyuncu'} başka bir anlaşmayla kulüp değiştirdi; teklif geçersiz.")
                self._expire_teams(buyer, seller)
                self.db.flush()
        except (TransferError, finance.BudgetError) as exc:
            if isinstance(exc, MarketError):
                raise
            raise MarketError(str(exc)) from exc
        except IntegrityError as exc:
            raise MarketError(BUSY_TEXT) from exc
        extra = f" + takas {exchange.name}" if exchange is not None else ""
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"Transfer tamamlandı: {player.name}, {seller.name} → {buyer.name} "
                             f"({_money(fee)}{extra}).")
        return news

    def _move_exchange(self, exchange: Player, from_team: Team, to_team: Team) -> TransferNews:
        """Takas oyuncusu SOZLESMESIYLE saticiya gecer (maas / sure / rol degismez); gercek transfer gibi kaydedilir."""
        from career_manager import TransferNews

        exchange.in_academy = False
        exchange.team_id = to_team.id
        exchange.team = to_team
        exchange.last_transfer_season = self.cm.season
        exchange.lineup_status, exchange.lineup_role = LineupStatus.BENCH, None
        exchange.transfer_listed = exchange.loan_listed = False
        news = TransferNews(exchange.name, from_team.name, to_team.name, 0, int(exchange.current_wage or 0),
                            player_id=exchange.id, from_team_id=from_team.id, to_team_id=to_team.id,
                            kind=KIND_EXCHANGE)
        self.cm._record_player_move(exchange, from_team, to_team, news)
        self.db.flush()
        return news

    def _log_move(self, player: Player, from_team: Team | None, to_team: Team | None, *, fee: int, wage: int,
                  kind: str, text: str) -> TransferNews:
        """Kiralik / donus / iade hareketi: transfer_log + (insan kulubuyse) haber + on_player_moved. Yasak baslamaz."""
        from career_manager import TransferNews
        from models import NewsKind

        cm = self.cm
        target = to_team if to_team is not None else from_team
        self.db.add(TransferLog(
            season=int(cm.season), week=max(1, int(cm.current_week)), player_id=player.id, player_name=player.name,
            from_team_id=from_team.id if from_team is not None else None,
            from_team_name=from_team.name if from_team is not None else None,
            to_team_id=target.id if target is not None else None,
            to_team_name=target.name if target is not None else "",
            fee=max(0, int(fee)), wage=max(0, int(wage)), kind=kind,
        ))
        humans = cm.human_team_ids()
        involved = {t.id for t in (from_team, to_team) if t is not None}
        if involved & set(humans) and target is not None:
            cm._add_news(NewsKind.TRANSFER, text, team_id=target.id,
                         other_team_id=from_team.id if from_team is not None else None)
        self.db.flush()
        news = TransferNews(player.name, from_team.name if from_team is not None else "",
                            target.name if target is not None else "", max(0, int(fee)), max(0, int(wage)),
                            player_id=player.id, from_team_id=from_team.id if from_team is not None else None,
                            to_team_id=target.id if target is not None else None, kind=kind)
        if target is not None:
            cm.run_extensions("on_player_moved", player, from_team.id if from_team is not None else None, target.id)
        return news

    # ================================================================== kiralik

    def _complete_loan(self, offer: TransferOffer, seat: Seat, player: Player, parent: Team, borrower: Team,
                       shift_wage_room: bool) -> TransferNews:
        fee, share = int(offer.fee), int(offer.loan_wage_share)
        facts = self._facts(kind=OfferKind.LOAN, player=player, seller_id=parent.id, buyer=borrower, fee=fee,
                            loan_weeks=offer.loan_weeks, share=share)
        self._require_valid(facts)
        start, end = self._loan_period(offer.loan_weeks)
        borrower_pays = loan_rules.wage_split(int(player.current_wage or 0), share)[0]
        needed = borrower_pays - int(borrower.free_wage)
        if needed > 0 and not shift_wage_room:
            raise LoanError(WAGE_ROOM_TEXT.format(need=_money(needed),
                                                  cost=_money(finance.weekly_to_transfer(needed))))
        if needed > 0 and int(borrower.transfer_budget) - finance.weekly_to_transfer(needed) < fee:
            raise LoanError(SHIFT_BUDGET_TEXT)
        done = {"kind": "done", "offer_kind": KIND_LOAN, "fee": fee, "wage": borrower_pays, "shift": max(0, needed),
                "player": self._snapshot(player), "exchange": None, "buyer_seat": offer.created_by_manager_id,
                "seller_seat": offer.responded_by_manager_id, "season": int(self.cm.season),
                "week": int(self.cm.current_week)}
        try:
            with self.db.begin_nested():
                self._transition(offer, OfferAction.COMPLETE, BUYER)
                self.db.flush()
                if needed > 0:
                    self.cm.shift_budget(borrower, needed)
                loan, news, log_id = self._start_loan(offer, player, parent, borrower, start, end, share, fee)
                done["loan_id"], done["log_ids"] = loan.id, {"player": log_id, "exchange": None}
                offer.transfer_log_ids = [log_id] if log_id is not None else []
                self._log(offer, done)
                self._event_entry(offer, OfferAction.COMPLETE.value, BUYER)
                self._void_player_offers([player.id], lambda p: f"{p.name if p else 'Oyuncu'} kiralığa gitti; "
                                                                f"teklif geçersiz.")
                self.db.flush()
        except (TransferError, finance.BudgetError) as exc:
            if isinstance(exc, MarketError):
                raise
            raise LoanError(str(exc)) from exc
        except IntegrityError as exc:
            raise LoanError(BUSY_TEXT) from exc
        self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                             f"Kiralık tamamlandı: {player.name}, {parent.name} → {borrower.name} "
                             f"({self._loan_period_text(start, end)}, maaşın %{share} payı kiralayanda).")
        return news

    @staticmethod
    def _loan_period_text(start: int, end: int) -> str:
        return f"{max(0, end - start)} hafta"

    def _start_loan(self, offer: TransferOffer | None, player: Player, parent: Team, borrower: Team, start: int,
                    end: int, share: int, fee: int) -> tuple[Loan, TransferNews, int | None]:
        """Kiralik baslar: para (bedel) + loans satiri + oyuncu kiralayana. Cagiran savepoint icinde cagirir."""
        if fee:
            if fee > int(borrower.transfer_budget):
                raise LoanError(f"Transfer bütçen yetersiz: {_money(borrower.transfer_budget)} var, "
                                f"{_money(fee)} gerekiyor.")
            borrower.transfer_budget = int(borrower.transfer_budget) - fee
            parent.transfer_budget = int(parent.transfer_budget) + fee
        loan = Loan(offer_id=offer.id if offer is not None else None, player_id=player.id, parent_team_id=parent.id,
                    borrower_team_id=borrower.id, start_career_week=int(start), end_career_week=int(end),
                    wage_share=int(share), status=LoanStatus.ACTIVE.value)
        self.db.add(loan)
        self.db.flush()
        player.team_id = borrower.id
        player.team = borrower
        player.loan_from_team_id, player.loan_id, player.loan_wage_share = parent.id, loan.id, int(share)
        player.in_academy = False
        player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        player.transfer_listed = player.loan_listed = False
        player.minutes_window = []
        player.concern_level = int(concerns.ConcernLevel.NONE)
        player.wage_demand = None
        self.db.flush()
        pays = loan_rules.wage_split(int(player.current_wage or 0), share)[0]
        before = self._max_log_id()
        text = (f"Kiralık: {player.name}, {parent.name} → {borrower.name} ({end - start} hafta, maaşın %{share} payı "
                f"kiralayanda" + (f", bedel {_money(fee)}" if fee else "") + ").")
        news = self._log_move(player, parent, borrower, fee=fee, wage=pays, kind=KIND_LOAN, text=text)
        log_id = self._log_ids_since(before, [player.id]).get(player.id)
        self._expire_teams(parent, borrower)
        return loan, news, log_id

    def _end_loan(self, loan: Loan, status: LoanStatus, *, kind: str = KIND_LOAN_RETURN,
                  notify_text: str | None = None, exclude_seat: int | None = None) -> TransferNews | None:
        """Kiralik biter: oyuncu ana kulube doner (sozlesmesi aynen), loans durumu yazilir. Savepoint cagiranda."""
        cw = self.career_week
        player = self.db.get(Player, loan.player_id)
        parent = self.db.get(Team, loan.parent_team_id) if loan.parent_team_id is not None else None
        borrower = self.db.get(Team, loan.borrower_team_id) if loan.borrower_team_id is not None else None
        loan.status = status.value
        if status is not LoanStatus.RETURNED:
            loan.end_career_week = max(int(loan.start_career_week), cw)
        news = None
        if player is not None and player.loan_id == loan.id:
            current = self.db.get(Team, player.team_id) if player.team_id is not None else borrower
            if parent is not None:
                player.team_id = parent.id
                player.team = parent
            player.loan_from_team_id = player.loan_id = player.loan_wage_share = None
            player.in_academy = False
            player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
            player.transfer_listed = player.loan_listed = False
            player.minutes_window = []
            player.concern_level = int(concerns.ConcernLevel.NONE)
            player.wage_demand = None
            self.db.flush()
            label = {LoanStatus.RECALLED: "geri çağrıldı", LoanStatus.REVERSED: "kiralık geri alındı"}.get(
                status, "kiralık dönüşü")
            text = f"Kiralık ({label}): {player.name}, {current.name if current else '?'} → " \
                   f"{parent.name if parent else '?'}."
            if parent is not None:
                news = self._log_move(player, current, parent, fee=0, wage=int(player.current_wage or 0), kind=kind,
                                      text=text)
        self._expire_teams(parent, borrower)
        if notify_text:
            seat_ids = []
            for team in (parent, borrower):
                seat = self.seats.by_team(team.id) if team is not None else None
                if seat is not None:
                    seat_ids.append(self._seat_id(seat))
            seat_ids = [i for i in seat_ids if i != exclude_seat]
            self._notify(seat_ids, NotificationKind.OFFER_UPDATE, notify_text, LOAN_REF, loan.id)
        return news

    def _loan_view(self, loan: Loan, team_id: int | None) -> LoanView:
        cw = self.career_week
        player = self.db.get(Player, loan.player_id)
        active = loan.status == LoanStatus.ACTIVE.value
        level = concerns.level_of(player.concern_level if player is not None else 0)
        recall_ok, recall_reason = (loan_rules.recall_allowed(player.concern_level if player is not None else 0,
                                                              cw - int(loan.start_career_week))
                                    if active else (False, LOAN_ENDED_TEXT))
        mine_parent = team_id is not None and team_id == loan.parent_team_id
        return LoanView(
            id=loan.id, player_name=player.name if player is not None else "Oyuncu",
            parent_team=self._team_name(loan.parent_team_id), borrower_team=self._team_name(loan.borrower_team_id),
            wage_share=int(loan.wage_share), ends_career_week=loan.end_career_week,
            weeks_left=(max(0, int(loan.end_career_week) - cw) if active and loan.end_career_week is not None
                        else None),
            status=loan.status, concern_label=concerns.LEVEL_LABELS[level],
            can_recall=bool(active and mine_parent and recall_ok),
            player_id=loan.player_id, parent_team_id=loan.parent_team_id, borrower_team_id=loan.borrower_team_id,
            direction="OUT" if mine_parent else ("IN" if team_id == loan.borrower_team_id else ""),
            start_career_week=int(loan.start_career_week), offer_id=loan.offer_id,
            recall_reason="" if recall_ok else recall_reason,
        )

    def loans(self, direction: str = "ALL") -> list[LoanView]:
        """Kulubumun kiraliklari: OUT (kiraliga verdiklerim), IN (kiraladiklarim), ALL. Aktifler once."""
        way = str(direction or "ALL").strip().upper()
        if way not in ("ALL", "IN", "OUT"):
            raise LoanError(DIRECTION_TEXT)
        team_id = self._my_team_id()
        if team_id is None:
            return []
        conditions = {"IN": Loan.borrower_team_id == team_id, "OUT": Loan.parent_team_id == team_id,
                      "ALL": or_(Loan.borrower_team_id == team_id, Loan.parent_team_id == team_id)}
        self.db.flush()
        rows = self.db.scalars(select(Loan).where(conditions[way]).order_by(
            case((Loan.status == LoanStatus.ACTIVE.value, 0), else_=1), Loan.id.desc()).limit(LIST_LIMIT))
        return [self._loan_view(loan, team_id) for loan in rows]

    def request_ai_loan(self, draft: OfferDraft) -> LoanView:
        """
        Insan <-> AI kiralik, aninda karar. Oyuncu AI kulubundeyse: o kulup kiraliga verir mi
        (loan_rules.ai_accepts_loan_out). Oyuncu benimse: draft.target_team_id AI kulubu kiralar mi
        (loan_rules.ai_accepts_loan_in; AI kiralik bedeli odemez). Ret -> LoanError (gerekce Turkce).
        """
        self._require_enabled(OfferKind.LOAN)
        seat, team = self._require_actor()
        if _kind(draft.kind, LoanError) is not OfferKind.LOAN:
            raise LoanError(LOAN_KIND_TEXT)
        if draft.exchange_player_id is not None:
            raise LoanError(LOAN_EXCHANGE_TEXT)
        fee = _amount(draft.fee)
        share = _share(draft.loan_wage_share)
        weeks = _weeks(draft.loan_weeks)
        if not _is_id(draft.player_id):
            raise LoanError(PLAYER_NOT_FOUND_TEXT)
        locked = self._lock(Player, [draft.player_id])
        player = locked[0] if locked else None
        if player is None or player.team_id is None:
            raise LoanError(PLAYER_NOT_FOUND_TEXT)
        outgoing = player.team_id == team.id
        if outgoing:
            if not _is_id(draft.target_team_id):
                raise LoanError(LOAN_TARGET_TEXT)
            parent_id, borrower_id = team.id, draft.target_team_id
        else:
            if draft.target_team_id is not None and draft.target_team_id != player.team_id:
                raise LoanError(PLAYER_NOT_FOUND_TEXT)
            parent_id, borrower_id = player.team_id, team.id
        if parent_id == borrower_id:
            raise LoanError(OWN_PLAYER_TEXT.format(name=player.name))
        teams = {t.id: t for t in self._lock(Team, [parent_id, borrower_id])}
        parent, borrower = teams.get(parent_id), teams.get(borrower_id)
        if parent is None or borrower is None:
            raise LoanError("Kulüp bulunamadı.")
        ai_team = borrower if outgoing else parent
        humans = self.cm.human_team_ids()
        if ai_team.id in humans:
            raise LoanError(HUMAN_LOAN_TEXT.format(team=ai_team.name))
        cw = self.career_week
        if ai_team.ai_protected_until is not None and int(ai_team.ai_protected_until) > cw:
            raise LoanError(PROTECTED_TEXT.format(team=ai_team.name, weeks=int(ai_team.ai_protected_until) - cw))
        facts = self._facts(kind=OfferKind.LOAN, player=player, seller_id=parent.id, buyer=borrower, fee=fee,
                            loan_weeks=weeks, share=share, humans=humans, wage_room=not outgoing)
        self._require_valid(facts)
        start, end = self._loan_period(weeks)
        self.db.flush()
        if outgoing:
            if fee > 0:
                raise LoanError(AI_LOAN_FEE_TEXT.format(team=borrower.name))
            ratings = list(self.db.scalars(select(Player.overall_rating).where(
                Player.team_id == borrower.id, Player.in_academy.is_(False), Player.position == player.position)))
            average = mean(ratings) if ratings else None
            accepted, reason = loan_rules.ai_accepts_loan_in(player.overall_rating, average, int(borrower.free_wage),
                                                             int(player.current_wage or 0), share)
        else:
            seniors = list(self.db.scalars(select(Player.id).where(
                Player.team_id == parent.id, Player.in_academy.is_(False))
                .order_by(Player.overall_rating.desc(), Player.id)))
            rank = seniors.index(player.id) + 1 if player.id in seniors else len(seniors) + 1
            accepted, reason = loan_rules.ai_accepts_loan_out(player.overall_rating, rank, len(seniors), share, weeks)
        if not accepted:
            raise LoanError(f"{ai_team.name}: {reason}")

        offer = TransferOffer(
            season=int(self.cm.season), created_career_week=cw, expires_career_week=None, kind=KIND_LOAN,
            player_id=player.id, seller_team_id=parent.id, buyer_team_id=borrower.id, fee=fee,
            exchange_player_id=None, loan_weeks=weeks, loan_wage_share=share, status=OfferStatus.COMPLETED.value,
            round=0, created_by_manager_id=seat.id, responded_by_manager_id=None, fairness_flags=[],
            contract_log=[], transfer_log_ids=[], updated_at=_now(), reason=_clean_text(reason, REASON_MAX),
        )
        self._log(offer, {"kind": "event", "action": "OFFER", "side": BUYER if not outgoing else SELLER,
                          "offer_kind": KIND_LOAN, "fee": fee, "loan_weeks": weeks, "share": share})
        try:
            with self.db.begin_nested():
                self.db.add(offer)
                self.db.flush()
                loan, _news, log_id = self._start_loan(offer, player, parent, borrower, start, end, share, fee)
                self._log(offer, {"kind": "done", "offer_kind": KIND_LOAN, "fee": fee, "shift": 0,
                                  "wage": loan_rules.wage_split(int(player.current_wage or 0), share)[0],
                                  "player": None, "exchange": None, "loan_id": loan.id,
                                  "log_ids": {"player": log_id, "exchange": None}, "buyer_seat": None,
                                  "seller_seat": None, "ai": True})
                self._event_entry(offer, OfferAction.COMPLETE.value, SYSTEM)
                offer.transfer_log_ids = [log_id] if log_id is not None else []
                self.db.flush()
        except IntegrityError as exc:
            raise LoanError(BUSY_TEXT) from exc
        except (TransferError, finance.BudgetError) as exc:
            if isinstance(exc, MarketError):
                raise
            raise LoanError(str(exc)) from exc
        return self._loan_view(loan, team.id)

    def recall_loan(self, loan_id: int) -> LoanView:
        """Ana kulubun menajeri kiraligi erken bitirir (loan_rules.recall_allowed)."""
        if not self.rules.shared:
            raise LoanError(NOT_SHARED_TEXT)
        seat, team = self._require_actor()
        rows = self._lock(Loan, [loan_id]) if _is_id(loan_id) else []
        loan = rows[0] if rows else None
        if loan is None or loan.parent_team_id != team.id:
            raise LoanError(LOAN_NOT_FOUND_TEXT)
        if loan.status != LoanStatus.ACTIVE.value:
            raise LoanError(LOAN_ENDED_TEXT)
        locked = self._lock(Player, [loan.player_id])
        player = locked[0] if locked else None
        self._lock(Team, [loan.parent_team_id, loan.borrower_team_id])
        allowed, reason = loan_rules.recall_allowed(player.concern_level if player is not None else 0,
                                                    self.career_week - int(loan.start_career_week))
        if not allowed:
            raise LoanError(reason)
        borrower = self._team_name(loan.borrower_team_id)
        with self.db.begin_nested():
            self._end_loan(loan, LoanStatus.RECALLED, exclude_seat=seat.id,
                           notify_text=f"{player.name if player else 'Oyuncu'}, ana kulübü {team.name} tarafından "
                                       f"kiralıktan geri çağrıldı ({borrower}).")
        return self._loan_view(loan, team.id)

    # ================================================================== listeler

    def set_listing(self, player_id: int, transfer_listed: bool | None = None, loan_listed: bool | None = None) -> None:
        """Kendi A takim oyuncumu transfer / kiralik listesine koyar ya da cikarir (None: degismez)."""
        if not self.rules.shared:
            raise MarketError(NOT_SHARED_TEXT)
        if transfer_listed is not None and not self.rules.human_market:
            raise MarketError(MARKET_OFF_TEXT)
        if loan_listed is not None and not self.rules.loans:
            raise LoanError(LOANS_OFF_TEXT)
        _seat, team = self._require_actor()
        rows = self._lock(Player, [player_id]) if _is_id(player_id) else []
        player = rows[0] if rows else None
        if player is None or player.team_id != team.id:
            name = player.name if player is not None else "Oyuncu"
            raise MarketError(NOT_YOUR_PLAYER_TEXT.format(name=name))
        if player.loan_from_team_id is not None and (transfer_listed or loan_listed):
            raise LoanError(LISTING_LOAN_TEXT)
        if player.in_academy and (transfer_listed or loan_listed):
            raise MarketError(LISTING_ACADEMY_TEXT)
        if transfer_listed is not None:
            player.transfer_listed = bool(transfer_listed)
        if loan_listed is not None:
            player.loan_listed = bool(loan_listed)
        self.db.flush()

    def listed_players(self, kind: str, mine: bool = False) -> list[Player]:
        """Menajer kuluplerinin transfer (TRANSFER) ya da kiralik (LOAN) listesi; mine: yalnizca benim kulubum."""
        try:
            listing = OfferKind(_ev(kind).strip().upper())
        except ValueError:
            raise MarketError(LISTED_KIND_TEXT) from None
        column = Player.transfer_listed if listing is OfferKind.TRANSFER else Player.loan_listed
        self.db.flush()
        stmt = select(Player).where(column.is_(True), Player.team_id.isnot(None), Player.in_academy.is_(False),
                                    Player.loan_from_team_id.is_(None))
        if mine:
            team_id = self._my_team_id()
            if team_id is None:
                return []
            stmt = stmt.where(Player.team_id == team_id)
        else:
            humans = sorted(self.cm.human_team_ids())
            if not humans:
                return []
            stmt = stmt.where(Player.team_id.in_(humans))
        return list(self.db.scalars(stmt.order_by(Player.overall_rating.desc(), Player.id).limit(LISTED_LIMIT)))

    # ================================================================== yonetici

    def review_queue(self) -> list[OfferView]:
        self._require_admin()
        self.db.flush()
        rows = self.db.scalars(select(TransferOffer).where(TransferOffer.status == OfferStatus.REVIEW.value)
                               .order_by(TransferOffer.id))
        return [self._view(offer, None) for offer in rows]

    def approve_review(self, offer_id: int) -> OfferView:
        offer = self._offer_row(offer_id)
        self._require_admin_on(offer)
        self._check_transition(offer, OfferAction.APPROVE, ADMIN)
        locked = {p.id: p for p in self._lock(Player, [offer.player_id, offer.exchange_player_id], share=True)}
        player = locked.get(offer.player_id)
        self._raise_if_void(offer, player, locked.get(offer.exchange_player_id))
        try:
            with self.db.begin_nested():
                self._transition(offer, OfferAction.APPROVE, ADMIN)
                offer.expires_career_week = market_rules.expires_at(self.career_week, self.rules)
                offer.reason = None
                self._event_entry(offer, OfferAction.APPROVE.value, ADMIN)
                self.db.flush()
        except IntegrityError as exc:
            raise MarketError(CONTRACT_TAKEN_TEXT.format(name=player.name)) from exc
        self._fair_play((offer.created_by_manager_id, offer.responded_by_manager_id), "APPROVED", offer.id)
        self._world_event(WorldEventKind.REVIEW, {
            "offer_id": offer.id, "decision": "APPROVED", "player_id": offer.player_id,
            "seller_team_id": offer.seller_team_id, "buyer_team_id": offer.buyer_team_id, "fee": int(offer.fee),
            "text": f"Yönetici anlaşmayı onayladı: {player.name}."})
        self.db.flush()
        self._notify_parties(offer, NotificationKind.REVIEW,
                             f"Yönetici anlaşmayı onayladı ({player.name}); alıcı devam edebilir.")
        return self._view(offer, None)

    def deny_review(self, offer_id: int, reason: str) -> OfferView:
        offer = self._offer_row(offer_id)
        self._require_admin_on(offer)
        self._check_transition(offer, OfferAction.DENY, ADMIN)
        text = _clean_text(reason, REASON_MAX) or "Yönetici anlaşmayı adil bulmadı."
        self._transition(offer, OfferAction.DENY, ADMIN)
        offer.reason = text
        self._event_entry(offer, OfferAction.DENY.value, ADMIN, reason=text)
        self.db.flush()
        self._fair_play((offer.created_by_manager_id, offer.responded_by_manager_id), "DENIED", offer.id)
        name = self._player_name(offer)
        self._world_event(WorldEventKind.REVIEW, {
            "offer_id": offer.id, "decision": "DENIED", "reason": text, "player_id": offer.player_id,
            "seller_team_id": offer.seller_team_id, "buyer_team_id": offer.buyer_team_id, "fee": int(offer.fee),
            "text": f"Yönetici anlaşmayı reddetti: {name}."})
        self.db.flush()
        self._notify_parties(offer, NotificationKind.REVIEW, f"Yönetici anlaşmayı reddetti ({name}): {text}")
        return self._view(offer, None)

    def _reversal_problem(self, offer: TransferOffer, done: dict | None) -> str | None:
        if done is None:
            return NO_DEAL_RECORD_TEXT
        if offer.created_by_manager_id is None or offer.responded_by_manager_id is None:
            return "Yalnızca menajerler arası anlaşmalar geri alınabilir."
        player = self.db.get(Player, offer.player_id)
        if offer.kind == KIND_LOAN:
            loan = self.db.get(Loan, done.get("loan_id")) if done.get("loan_id") else None
            if loan is None or loan.status != LoanStatus.ACTIVE.value:
                return "Kiralık sona erdi; geri alınamaz."
            if player is None or player.team_id != offer.buyer_team_id:
                return "Oyuncu artık kiralayan kulüpte değil; geri alınamaz."
            return None
        if player is None or player.team_id != offer.buyer_team_id or player.loan_from_team_id is not None:
            return f"{player.name if player else 'Oyuncu'} artık alıcı kulüpte değil; transfer geri alınamaz."
        if offer.exchange_player_id is not None:
            exchange = self.db.get(Player, offer.exchange_player_id)
            if exchange is None or exchange.team_id != offer.seller_team_id or exchange.loan_from_team_id is not None:
                return "Takas oyuncusu artık satıcı kulüpte değil; transfer geri alınamaz."
        return None

    def reversible_offers(self, weeks: int = 8) -> list[OfferView]:
        """Yonetici: son `weeks` haftada tamamlanan, geri alinabilir menajerler arasi anlasmalar (en yeni once)."""
        self._require_admin()
        cw = self.career_week
        self.db.flush()
        rows = self.db.scalars(select(TransferOffer).where(
            TransferOffer.status == OfferStatus.COMPLETED.value, TransferOffer.created_by_manager_id.isnot(None),
            TransferOffer.responded_by_manager_id.isnot(None)).order_by(TransferOffer.id.desc()))
        views = []
        for offer in rows:
            done = _done_entry(offer.contract_log)
            if done is None or int(done.get("cw", 0)) <= cw - max(0, int(weeks)):
                continue
            if self._reversal_problem(offer, done) is None:
                views.append(self._view(offer, None))
        return views

    def reversible_transfers(self, weeks: int = 8) -> list[TransferLog]:
        """reversible_offers'in ana hareketlerinin transfer_log satirlari; her satirda gecici `offer_id` alani."""
        logs = []
        for view in self.reversible_offers(weeks):
            offer = self.db.get(TransferOffer, view.id)
            done = _done_entry(offer.contract_log) or {}
            log_id = (done.get("log_ids") or {}).get("player")
            row = self.db.get(TransferLog, log_id) if log_id else None
            if row is not None:
                row.offer_id = offer.id                   # gecici (eslenmemis) alan: arayuz adm_reverse_{offer_id}
                logs.append(row)
        return logs

    def reverse_transfer(self, offer_id: int, reason: str) -> list[TransferNews]:
        """
        Yonetici: tamamlanmis menajerler arasi anlasmayi geri alir. Oyuncu (ve takas oyuncusu) eski kulubune eski
        sozlesmesiyle doner; kiralik REVERSED olur; bedel saticidan aliciya iade edilir (satici kasasinda olan
        kadar: para yaratilmaz); REVERSED; iki koltuga -25; transfer_log REVERSAL; world_events REVERSAL.
        """
        offer = self._lock_offer_group(offer_id)
        self._require_admin_on(offer)
        self._check_transition(offer, OfferAction.REVERSE, ADMIN)
        done = _done_entry(offer.contract_log)
        loan = None
        if offer.kind == KIND_LOAN and done is not None and done.get("loan_id"):
            rows = self._lock(Loan, [done["loan_id"]])
            loan = rows[0] if rows else None
        players = {p.id: p for p in self._lock(Player, [offer.player_id, offer.exchange_player_id])}
        teams = {t.id: t for t in self._lock(Team, [offer.seller_team_id, offer.buyer_team_id])}
        problem = self._reversal_problem(offer, done)
        if problem:
            raise MarketError(problem)
        seller, buyer = teams[offer.seller_team_id], teams[offer.buyer_team_id]
        player = players[offer.player_id]
        exchange = players.get(offer.exchange_player_id) if offer.exchange_player_id is not None else None
        text = _clean_text(reason, REASON_MAX) or "Yönetici kararı."
        fee = int(done.get("fee", offer.fee) or 0)
        refund = max(0, min(fee, int(seller.transfer_budget)))
        news: list = []
        with self.db.begin_nested():
            self._transition(offer, OfferAction.REVERSE, ADMIN)
            offer.reason = text
            seller.transfer_budget = int(seller.transfer_budget) - refund
            buyer.transfer_budget = int(buyer.transfer_budget) + refund
            if offer.kind == KIND_LOAN:
                returned = self._end_loan(loan, LoanStatus.REVERSED, kind=KIND_REVERSAL)
                if returned is not None:
                    news.append(returned)
            else:
                news.append(self._restore_player(player, done.get("player") or {}, buyer, seller, refund))
                if exchange is not None:
                    news.append(self._restore_player(exchange, done.get("exchange") or {}, seller, buyer, 0))
            self._log(offer, {"kind": "reversed", "refund": refund, "reason": text})
            self._event_entry(offer, OfferAction.REVERSE.value, ADMIN, reason=text)
            self._expire_teams(seller, buyer)
            self.db.flush()
        self._fair_play((offer.created_by_manager_id, offer.responded_by_manager_id), "REVERSED", offer.id)
        self._world_event(WorldEventKind.REVERSAL, {
            "offer_id": offer.id, "kind": offer.kind, "player_id": offer.player_id,
            "exchange_player_id": offer.exchange_player_id, "seller_team_id": seller.id, "buyer_team_id": buyer.id,
            "fee": fee, "refund": refund, "reason": text,
            "text": f"Yönetici anlaşmayı geri aldı: {player.name}, {buyer.name} → {seller.name}."})
        self.db.flush()
        short = f" (satıcı kasası yetmedi: {_money(fee - refund)} iade edilemedi)" if refund < fee else ""
        self._notify_parties(offer, NotificationKind.REVIEW,
                             f"Yönetici anlaşmayı geri aldı ({player.name}): {text} İade: {_money(refund)}{short}.")
        return news

    def _restore_player(self, player: Player, data: dict, from_team: Team, to_team: Team, fee: int) -> TransferNews:
        player.team_id = to_team.id
        player.team = to_team
        player.in_academy = False
        if data:
            player.current_wage = int(data.get("wage", player.current_wage))
            player.contract_years = int(data.get("years", player.contract_years))
            player.squad_role = SquadRole(data.get("role", _ev(player.squad_role)))
            player.contract_overall = data.get("contract_overall", player.contract_overall)
            player.last_transfer_season = data.get("last_season")
        player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        player.transfer_listed = player.loan_listed = False
        player.minutes_window = []
        player.concern_level = int(concerns.ConcernLevel.NONE)
        player.wage_demand = None
        player.market_value = finance.market_value(player.overall_rating, player.age, player.position,
                                                   player.potential_rating)
        self.db.flush()
        text = f"Transfer geri alındı: {player.name}, {from_team.name} → {to_team.name}."
        news = self._log_move(player, from_team, to_team, fee=fee, wage=int(player.current_wage or 0),
                              kind=KIND_REVERSAL, text=text)
        player.transfer_locked_until = data.get("locked") if data else player.transfer_locked_until
        self.db.flush()
        return news

    # ================================================================== haftalik ve sezonluk (eklenti)

    def _safe(self, label: str, fn, *args) -> None:
        """Eklenti adimi kendi savepoint'inde: hata loglanir, hafta ilerlemesi bozulmaz."""
        self.db.flush()
        try:
            with self.db.begin_nested():
                fn(*args)
        except (SQLAlchemyError, ValueError, TransferError) as exc:
            log.exception("Pazar eklentisi adımı başarısız (%s): %s", label, exc)

    def run_week(self, week: int, report=None) -> None:
        """MarketExtension.on_week: hafta sayaci artmadan once (kariyer haftasi = oynanan hafta)."""
        next_week = self.career_week + 1
        self._safe("expire", self._expire_offers, next_week)
        self._safe("sweep", self._sweep_offers)
        self._safe("loans", self._end_due_loans, next_week)
        self._safe("recalls", self._ai_recalls)
        self._safe("loan_concerns", self._loan_concern_notices)
        self._safe("wage_demands", self._loaned_wage_demands, report)
        self._safe("fair_play", self._fair_play_recovery)

    def _expire_offers(self, next_week: int) -> None:
        self.db.flush()
        rows = list(self.db.scalars(
            select(TransferOffer).where(TransferOffer.status.in_(OPEN_VALUES),
                                        TransferOffer.expires_career_week.isnot(None),
                                        TransferOffer.expires_career_week <= next_week)
            .order_by(TransferOffer.id).with_for_update(key_share=True).execution_options(populate_existing=True)))
        for offer in rows:
            self._transition(offer, OfferAction.EXPIRE, SYSTEM)
            offer.reason = EXPIRED_TEXT
            self._event_entry(offer, OfferAction.EXPIRE.value, SYSTEM)
            self.db.flush()
            self._notify_parties(offer, NotificationKind.OFFER_UPDATE,
                                 f"Teklifin süresi doldu ({self._player_name(offer)}).")

    def _sweep_offers(self) -> None:
        self.db.flush()
        rows = list(self.db.scalars(
            select(TransferOffer).where(TransferOffer.status.in_(OPEN_VALUES)).order_by(TransferOffer.id)
            .with_for_update(key_share=True).execution_options(populate_existing=True)))
        for offer in rows:
            reason = self._void_reason(offer)
            if reason:
                self._void(offer, reason)

    def _end_due_loans(self, next_week: int) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(Loan).where(
            Loan.status == LoanStatus.ACTIVE.value, Loan.end_career_week.isnot(None),
            Loan.end_career_week <= next_week).order_by(Loan.id)))
        for loan in rows:
            name = self._loan_player_name(loan)
            self._end_loan(loan, LoanStatus.RETURNED, notify_text=f"Kiralık süresi doldu: {name} ana kulübüne döndü.")

    def _loan_player_name(self, loan: Loan) -> str:
        player = self.db.get(Player, loan.player_id)
        return player.name if player is not None else "Oyuncu"

    def return_all_loans(self) -> None:
        """Sezon sonu / basi: aktif kiraliklarin hepsi biter (kiralik sezonu asmaz)."""
        self.db.flush()
        for loan in list(self.db.scalars(select(Loan).where(Loan.status == LoanStatus.ACTIVE.value)
                                         .order_by(Loan.id))):
            name = self._loan_player_name(loan)
            self._end_loan(loan, LoanStatus.RETURNED, notify_text=f"Sezon bitti: {name} kiralıktan döndü.")

    def _ai_recalls(self) -> None:
        cw = self.career_week
        humans = self.cm.human_team_ids()
        self.db.flush()
        for loan in list(self.db.scalars(select(Loan).where(Loan.status == LoanStatus.ACTIVE.value)
                                         .order_by(Loan.id))):
            if loan.parent_team_id is None or loan.parent_team_id in humans:
                continue
            player = self.db.get(Player, loan.player_id)
            if player is None:
                continue
            allowed, _reason = loan_rules.recall_allowed(player.concern_level, cw - int(loan.start_career_week))
            if allowed:
                parent = self._team_name(loan.parent_team_id)
                self._end_loan(loan, LoanStatus.RECALLED,
                               notify_text=f"{player.name} süre alamadığı için ana kulübü {parent} tarafından "
                                           f"geri çağrıldı.")

    def _loan_concern_notices(self) -> None:
        cw = self.career_week
        humans = self.cm.human_team_ids()
        self.db.flush()
        for loan in list(self.db.scalars(select(Loan).where(Loan.status == LoanStatus.ACTIVE.value)
                                         .order_by(Loan.id))):
            if loan.parent_team_id not in humans:
                continue
            player = self.db.get(Player, loan.player_id)
            if player is None:
                continue
            allowed, _reason = loan_rules.recall_allowed(player.concern_level, cw - int(loan.start_career_week))
            if not allowed:
                continue
            seat = self.seats.by_team(loan.parent_team_id)
            seat_id = self._seat_id(seat)
            if seat_id is None:
                continue
            exists = self.db.scalar(select(Notification.id).where(
                Notification.manager_id == seat_id, Notification.ref_type == LOAN_RECALL_REF,
                Notification.ref_id == loan.id).limit(1))
            if exists is not None:
                continue
            level = concerns.LEVEL_LABELS[concerns.level_of(player.concern_level)]
            borrower = self._team_name(loan.borrower_team_id)
            self._notify([seat_id], NotificationKind.OFFER_UPDATE,
                         f"Kiralıktaki {player.name} {borrower} kulübünde süre alamıyor ({level}); "
                         f"istersen geri çağırabilirsin.", LOAN_RECALL_REF, loan.id)

    def _loaned_wage_demands(self, report) -> None:
        """Kiralik oyuncu kiralayan kulupten yeni sozlesme istemez (sozlesmesi ana kulubundedir)."""
        self.db.flush()
        rows = list(self.db.scalars(select(Player).where(Player.loan_from_team_id.isnot(None),
                                                         Player.wage_demand.isnot(None))))
        if not rows:
            return
        ids = {p.id for p in rows}
        for p in rows:
            p.wage_demand = None
        if report is not None:
            report.wage_demands = [n for n in report.wage_demands if n.player_id not in ids]
            for club in getattr(report, "clubs", {}).values():
                club.wage_demands = [n for n in club.wage_demands if n.player_id not in ids]
        self.db.flush()

    def _fair_play_recovery(self) -> None:
        self.db.flush()
        rows = list(self.db.execute(select(WorldManager.id, WorldManager.fair_play).where(
            WorldManager.status.in_(MEMBER_STATUSES), WorldManager.fair_play < fair_play.FAIR_PLAY_MAX)
            .order_by(WorldManager.id)))
        for seat_id, score in rows:
            delta = fair_play.weekly_recovery(score)
            if delta > 0:
                self.seats.adjust_fair_play(seat_id, delta, fair_play.FAIR_PLAY_REASONS["RECOVERY"])

    def player_moved(self, player) -> None:
        """Oyuncu kulup degistirdi: uzerindeki (ve takas olarak sunuldugu) acik teklifler VOIDED."""
        if player is None or getattr(player, "id", None) is None:
            return
        name = player.name
        self._void_where(or_(TransferOffer.player_id == player.id, TransferOffer.exchange_player_id == player.id),
                         f"{name} kulüp değiştirdi; teklif geçersiz.", skip_locked=True)

    def club_released(self, team_id: int) -> None:
        """Kulup menajerini kaybetti: kulubun alici / satici oldugu acik teklifler VOIDED, listeler temizlenir."""
        if team_id is None:
            return
        self._void_where(or_(TransferOffer.seller_team_id == team_id, TransferOffer.buyer_team_id == team_id),
                         CLUB_RELEASED_TEXT, skip_locked=True)
        self.db.execute(update(Player).where(Player.team_id == team_id,
                                             or_(Player.transfer_listed.is_(True), Player.loan_listed.is_(True)))
                        .values(transfer_listed=False, loan_listed=False)
                        .execution_options(synchronize_session="fetch"))
        self.db.flush()


def transfers_role_label(role) -> str:
    from transfers import ROLE_LABELS

    try:
        return ROLE_LABELS[SquadRole(_ev(role))]
    except (KeyError, ValueError):
        return str(role)


class MarketExtension(CareerExtension):
    """
    Kariyer eklentisi (extensions.load: rules.shared ve (human_market ya da loans)). Kurucu sorgu atmaz. Kancalar
    hafta ilerlemesini / transferi asla bozmaz: her adim savepoint'te, hata loglanir.
        on_week          teklif suresi, gecersiz teklif taramasi (leave_world dahil), kiralik bitisi, AI geri
                         cagirmasi, kiralik kaygi bildirimi, kiralik oyuncunun maas talebi temizligi, adil oyun
        on_season_end    tum aktif kiraliklar doner (AI akademi yonetiminden ONCE)
        on_season_start  ayni (guvenlik)
        on_player_moved  oyuncu / takas oyuncusu uzerindeki acik teklifler VOIDED
        on_club_released kulubun acik teklifleri VOIDED, listeler temizlenir (kiraliklar surer)
        transfer_block_reason  None (kiralik nedeni CareerManager'da; AI adaylari icin ucuz kalir)
    """

    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm

    def _hub(self) -> MarketHub:
        return MarketHub(self.cm)

    def on_week(self, week: int, report) -> None:
        self._hub().run_week(week, report)

    def on_season_end(self) -> None:
        hub = self._hub()
        hub._safe("season_end_loans", hub.return_all_loans)

    def on_season_start(self, new_season: int) -> None:
        hub = self._hub()
        hub._safe("season_start_loans", hub.return_all_loans)

    def new_season_blocker(self) -> str | None:
        return None

    def on_player_moved(self, player, seller_id: int | None, buyer_id: int) -> None:
        hub = self._hub()
        hub._safe("player_moved", hub.player_moved, player)

    def on_club_released(self, team_id: int) -> None:
        hub = self._hub()
        hub._safe("club_released", hub.club_released, team_id)

    def transfer_block_reason(self, player) -> str | None:
        return None
