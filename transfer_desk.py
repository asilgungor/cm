"""
transfer_desk.py
================
Transfer masasi (13H): insan kulubu <-> yapay zeka kulubu transferlerinin Football Manager tarzi akisi. Kontrolcu:
FLUSH eder, COMMIT ETMEZ (web callback'i islemi kapatir). Kurallar SAF transfer_rules.py'de; sozlesme masasi
transfers.ContractNegotiation(agent=True). Rastgelelik anlasma kimligi + turdan crc32 ile turetilir: cm.rng'den
ASLA cekilmez (eski kariyerde mac / AI transfer sonuclari degismez). Insan <-> insan transferleri market_hub'dadir;
masa menajer kulubundeki oyuncuya teklif kabul etmez (market_hub metniyle yonlendirir).

AKIS (IN: menajer AI kulubunden alir)
    scout(oyuncu)       gozlem gorevi: bilgi (0-100) her hafta artar (transfer_rules.weekly_scouting_gain). Ayni lig
                        oyunculari 35 ile bilinir. Teklif ve bilgi alma icin en az KNOWN_THRESHOLD (25) gerekir.
    scout_report        bilgiyle olceklenen sisli rapor (K12): ozellik / deger araligi; 50+ potansiyel, isteklilik,
                        serbest kalma bedeli; 75+ sakatlik egilimi ve sozlesme
    enquire             ENQUIRY dosyasi: kulubun tutumu (satilik degil / pazarliga acik / listede / fazlalik / kapali)
                        ve sisli fiyat araligi ("12-15M civari"); oyuncunun istekliligi (bilgi 50+ ise)
    make_bid            yapilandirilmis teklif (DealTerms): pesin + taksit, ek odemeler, sonraki satis payi, takas.
                        Kulup ayni hafta en fazla RESPONSES_PER_WEEK kez aninda yanit verir; fazlasi sirada bekler ve
                        haftalik adimda yanitlanir. Yanit: ACCEPT (-> TERMS) / COUNTER (ne degismeli; sira menajerde,
                        COUNTER_VALID_WEEKS gecerli) / REJECT (sabir -2) / END (gorusmeler kesildi: REJECTED ve
                        TALKS_COOLDOWN_WEEKS hafta yeni dosya acilamaz)
    accept_counter      kulubun karsi teklifi kabul -> TERMS
    open_terms / submit_terms
                        oyuncu ve menajeriyle kisisel sartlar: maas, sure, rol sozu, imza primi, sadakat primi,
                        menajer ucreti, serbest kalma bedeli, mac / gol primi. Masa history'deki "terms_open" anlik
                        goruntusu ve "terms_bid" kayitlarindan deterministik yeniden kurulur. Kabul -> saglik kontrolu
    saglik              PASS -> AGREED; RISK -> MEDICAL (confirm_medical: devam / vazgec); FAIL -> COLLAPSED
    complete            AGREED + transfer donemi acik: para ve oyuncu TEK savepoint'te el degistirir. Donem kapaliysa
                        anlasma bekler; donem acildigi haftanin basinda (onceki haftanin sonunda) kendiliginden tamamlanir
    withdraw            menajer tamamlanmadan once vazgecer

AKIS (OUT: AI kulubu menajerin oyuncusunu ister)
    haftalik            donem aciksa AI kulupleri teklif yapar (listedeki / ayrilmak isteyen oyuncuya daha sik);
                        istenen bedel (set_asking_price) tabani belirler. Serbest kalma bedeli olan oyuncu icin uygun
                        AI kulubu bedeli oderse satis REDDEDILEMEZ.
    accept_offer / reject_offer / counter_offer
                        menajer kabul / ret / karsi teklif (AI alici: transfer_rules.buyer_response, gizli ust sinir).
                        Bonservis anlasmasindan sonra AI kulubu oyuncuyla kendi sozlesmesini yapar (oyuncu reddedebilir)
                        ve donem aciksa transfer hemen tamamlanir.
    set_listing / set_asking_price   transfer / kiralik listesi ve istenen bedel (her dunyada)

DURUMLAR  ENQUIRY -> BIDDING -> TERMS -> (MEDICAL) -> AGREED -> COMPLETED; kapanislar REJECTED, COLLAPSED, WITHDRAWN,
          EXPIRED, VOIDED (oyuncu baska yoldan kulup degistirdi / kulup menajerini kaybetti). turn: MANAGER / CLUB.

PARA (tek dogru kaynak: transfer_payments; her satirin ref'i benzersiz -- ayni odeme iki kez yapilamaz)
    tamamlama   pesinat CareerManager.complete_transfer ile (log_fee = toplam garantili bedel); imza primi ve menajer
                ucreti aliciyi terk eder (payee NULL); taksitler SCHEDULED (vade: ceyrek sezon araliklari); sadakat
                primi her sezon basi (due_season). Taksit tutarlari toplami ertelenen bedele BIREBIR esittir.
    haftalik    vadesi gelen taksit / sadakat / prim odenir. Kasa eksiye DUSMEZ: yetmeyen kisim OVERDUE kalir ve her
                hafta yeniden denenir; borcu olan kulup masada yeni teklif yapamaz (acik kural). Ek odemeler
                (mac, gol, lig / kupa sampiyonlugu) tetiklenince bir kez odenir; oyuncu kulupten ayrilirsa duser.
    sonraki satis payi  masada alinan oyuncuyu alici kulup SATTIGINDA (hangi yoldan olursa olsun: masa, AI penceresi,
                market_hub, eski akis) eski kulube %pay odenir -- bir kez (sell_on_used_career_week). Masa satislarinda
                pay alinan her odemeden orantili (pesinat, taksit, ek odeme); tam pesin satislarda tek seferde.
    kilitler    tamamlama: anlasma satiri (FOR UPDATE) -> oyuncular (id sirasi) -> kulupler (id sirasi). Ikinci
                tamamlama anlasma kilidinde bekler, COMPLETED gorur ve DeskError alir; para bir kez hareket eder.

K12: arayuze gizli sayi cikmaz (kulubun hedef / taban bedeli, sabir, ikna skoru, AI ust siniri): etiket ve sisli
aralik gosterilir. Gorunumler (DealView, TermsStep, ...) duz degerlerdir; arayuz ORM bilmeden cizer.

KONTROLCU API'si (TransferDesk(cm)): window, knowledge, scout, scout_report, enquire, make_bid, accept_counter,
withdraw, open_terms, submit_terms, confirm_medical, complete, trigger_release_clause, incoming, outgoing, deals,
deal, accept_offer, reject_offer, counter_offer, set_listing, set_asking_price, payments, finance_summary.
13I salt okunur arayuz yardimcilari (yazmaz): summaries (tek sorgulu liste satiri), action_count (menu sayaci),
open_deal_for, knowledge_map (arama tablosu, iki sorgu), terms_table (sozlesme masasi + konusma gecmisi).
Haftalik: run_week(cm, week, report) (CareerManager._run_transfer_desk). Satis hooku: settle_sell_on(cm, ...).

15A SOZLESME DONGUSU (kurallar contracts.py; bayrak contracts.CONTRACT_CYCLE, kapaliyken hicbiri calismaz)
    ContractCycle(cm)   dunya capinda: AI yenileme kararlari (ContractTalk RENEWAL SIGNED / DECLINED / REFUSED), on
                        sozlesme donemi (AI -> AI ve AI -> insan kulubunun oyuncusu; PRE_CONTRACT AGREED), serbest oyuncu
                        imzalari (ihtiyac + firsat), insan kuluplerine uyarilar; sezon devri: geciken kararlar, on
                        sozlesmelerin uygulanmasi (transfer_log BOSMAN), kadro guvencesi, serbest birakma (RELEASED), AI
                        hazirlik donemi imzalari ve akademiden tamamlama. Haftalik: run_contract_week(cm, week, report).
    ContractDesk(cm)    menajerin API'si: contract_window, contracts (Sozlesmeler ekrani), open_renewal, free_agents,
                        open_free_agent, pre_contract_targets, open_pre_contract, submit, sign, withdraw, talk, talks,
                        terms_log, termination_quote, terminate. Gorusme masasi transfers.ContractNegotiation(agent=True)
                        (history'deki "terms_open" anlik goruntusu + "terms_bid" tekliflerinden deterministik).

15F CANLI PAZAR VE KIRALIK (kurallar transfer_rules bolum 12 + loan_rules 15F; bayrak transfer_rules.LIVE_MARKET)
    WorldMarket(cm)     dunya pazari (AI <-> AI): YALNIZ transfer doneminde, LIGLER ARASI, ihtiyaca gore; donem
                        basina rules.window_deal_target kadar transfer (son hafta "son gun" kotasi), donem
                        acilisinda AI listeleri, AI <-> AI kiraliklar, geri alim maddeleri, soylentiler ve son gun
                        haberi. career_manager.run_ai_transfer_window bayrak ACIKKEN buna, kapaliyken eski
                        _ai_transfer_deals'e gider (kapaliyken dunya 15F oncesiyle BIT-BIT ayni).
    LoanCycle(cm)       haftalik kiralik dongusu (run_week icinden): satin alma opsiyonlari (her dunyada), AI
                        kiralik teklifleri (bayrak), ve TEK OYUNCULU dunyada kiralik bitisi / geri cagirma /
                        kaygi (paylasilan dunyada bunlari market_hub.MarketExtension kosar).
    MarketDesk(cm)      menajerin 15F API'si: listings, world_moves, loans, request_loan, offer_loan_out,
                        loan_offers / accept_loan_offer / reject_loan_offer, recall, exercise_option,
                        buy_back_options / trigger_buy_back.
    refund_sell_on      market_hub bir satisi geri aldiginda odenmis sonraki satis payinin iadesi.
    Yeni maddeler: kara dayali sonraki satis payi (DealTerms.sell_on_profit), geri alim (buy_back_fee /
    buy_back_seasons), opsiyonlu kiralik (loans.option_fee / option_mandatory).
    Kiralik dosyasi transfer_deals.kind = LOAN'dir; 13H listeleri (deals / summaries / action_count sayaci disinda)
    yalnizca kind = TRANSFER dosyalarini gosterir.
"""

from __future__ import annotations

import logging
import random
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

import contracts
import finance
import inbox
import loan_rules
import market_rules
import messaging
import reputation
import staff as staff_rules
import transfer_rules as rules
import transfers
from market_rules import squad_level_refusal
from messaging import NotificationKind
from models import (
    OPEN_DEAL_STATUSES,
    ContractTalk,
    Fixture,
    GameMode,
    HonourKind,
    League,
    LineupStatus,
    Loan,
    LoanStatus,
    NewsKind,
    Player,
    PlayerMatchStat,
    Position,
    ScoutAssignment,
    SeasonHonour,
    SquadRole,
    Team,
    TransferDeal,
    TransferKind,
    TransferLog,
    TransferPayment,
)
from transfer_rules import AddOn, DealTerms
from transfers import ContractNegotiation, ContractOffer, NegotiationStatus, TransferError

if TYPE_CHECKING:
    from career_manager import CareerManager, TransferNews

log = logging.getLogger(__name__)

# --- sabitler ---------------------------------------------------------------------------------------------------
IN, OUT = "IN", "OUT"
MANAGER, CLUB = "MANAGER", "CLUB"
ENQUIRY, BIDDING, TERMS, MEDICAL, AGREED = "ENQUIRY", "BIDDING", "TERMS", "MEDICAL", "AGREED"
COMPLETED, REJECTED, COLLAPSED, WITHDRAWN, EXPIRED, VOIDED = (
    "COMPLETED", "REJECTED", "COLLAPSED", "WITHDRAWN", "EXPIRED", "VOIDED")
OPEN = frozenset(OPEN_DEAL_STATUSES)
STATUS_LABELS = {
    ENQUIRY: "Bilgi alındı", BIDDING: "Pazarlık", TERMS: "Kişisel şartlar", MEDICAL: "Sağlık kontrolü",
    AGREED: "Anlaşma tamam", COMPLETED: "Tamamlandı", REJECTED: "Reddedildi", COLLAPSED: "Çöktü",
    WITHDRAWN: "Geri çekildi", EXPIRED: "Süresi doldu", VOIDED: "Geçersiz",
}
PAID, SCHEDULED, OVERDUE, CANCELLED = "PAID", "SCHEDULED", "OVERDUE", "CANCELLED"
PAYMENT_LABELS = {
    "UPFRONT": "Peşinat", "INSTALMENT": "Taksit", "ADD_ON": "Ek ödeme", "SELL_ON": "Sonraki satış payı",
    "SIGNING": "İmza primi", "AGENT": "Menajer ücreti", "LOYALTY": "Sadakat primi", "BONUS": "Maç / gol primi",
    "RELEASE": "Serbest kalma bedeli",
}
PAYMENT_STATUS_LABELS = {PAID: "Ödendi", SCHEDULED: "Planlandı", OVERDUE: "Gecikmede", CANCELLED: "İptal"}

RESPONSES_PER_WEEK = 2             # kulup ayni dosyada haftada en fazla bu kadar aninda yanit verir
COUNTER_VALID_WEEKS = 2            # sira menajerdeyken (karsi teklif / AI teklifi) gecerlilik
ENQUIRY_VALID_WEEKS = 4            # teklif yapilmayan bilgi dosyasi kapanir
TERMS_VALID_WEEKS = 3              # bonservis anlasmasindan sonra kisisel sartlar / saglik icin sure
TALKS_COOLDOWN_WEEKS = 4           # kulup gorusmeleri kesince yeni dosya acilamaz
AGREED_MAX_FAILURES = 3            # donem acikken tamamlanamayan anlasma bu kadar denemeden sonra coker
AI_BID_BASE_CHANCE = 0.12          # donem acikken haftalik: kulube (listede oyuncu yoksa) AI teklifi gelme olasiligi
AI_BID_LISTED_CHANCE = 0.55        # listede / ayrilmak isteyen oyuncu varsa
AI_BID_POOL = 8                    # listede kimse yoksa teklif en iyi bu kadar oyuncudan birine
AI_BID_TRIES = 3                   # alici bulunamazsa havuzdan en fazla bu kadar oyuncu denenir
AI_BUYER_MIN_BUDGET_SHARE = 0.3    # alici AI kasasi en az oyuncu degerinin bu kadari
OUT_PATIENCE = 3                   # AI alicinin karsi teklif sabri
PROMISE_BROKEN_MORALE = -10
MIN_RELEASE_CLAUSE = 100_000
DEAL_REF = "DEAL"
NOTE_MAX = 300

# --- Turkce mesajlar ----------------------------------------------------------------------------------------------
NO_TEAM_TEXT = "Önce yöneteceğin kulübü seç."
TOURNAMENT_TEXT = "Turnuva modunda transfer yapılmaz."
PLAYER_NOT_FOUND_TEXT = "Oyuncu bulunamadı."
DEAL_NOT_FOUND_TEXT = "Transfer dosyası bulunamadı."
OWN_PLAYER_TEXT = "{name} zaten senin takımında."
NO_CLUB_TEXT = "{name} bir kulübe bağlı değil."
ACADEMY_TEXT = "{name} {team} akademisinde; akademi oyuncuları satılık değil."
HUMAN_SELLER_TEXT = "{team} bir menajerin kulübü; {name} için teklifini Teklifler panelinden yap."
UNKNOWN_TEXT = ("{name} hakkında yeterli bilgin yok (%{k}). Önce gözlemci gönder; teklif için en az "
                "%{need} bilgi gerekir.")
DEBT_TEXT = "Gecikmiş transfer ödemelerin var ({amount}); borç kapanmadan yeni teklif yapamazsın."
BUDGET_TEXT = "Transfer bütçen yetersiz: {have} var, {need} gerekiyor."
TALKS_BLOCKED_TEXT = "{team} görüşmeleri kesti; {weeks} hafta boyunca yeni teklif kabul etmiyor."
CLUB_THINKING_TEXT = "Kulüp önceki teklifini değerlendiriyor; yanıt bekleniyor."
NOT_YOUR_TURN_TEXT = "Sıra karşı tarafta; yanıtı bekle."
NO_COUNTER_TEXT = "Kabul edilecek bir karşı teklif yok."
FEE_AGREED_TEXT = "Bonservis anlaşması zaten yapıldı."
CLOSED_TEXT = "Dosya kapandı ({status})."
TERMS_STAGE_TEXT = "Kişisel şartlar yalnızca bonservis anlaşmasından sonra görüşülür."
TERMS_NOT_OPEN_TEXT = "Önce sözleşme masasını aç."
NOT_AGREED_TEXT = "Transfer için önce tüm şartlarda anlaşılmalı."
WINDOW_CLOSED_TEXT = "Transfer dönemi kapalı; anlaşma dönem açılınca kendiliğinden tamamlanacak."
WAGE_ROOM_TEXT = ("Maaş havuzunda yer yok: haftalık {need} ek alan gerekli. Bütçe kaydırarak tamamla "
                  "(transfer kasasından {cost}).")
SQUAD_FULL_TEXT = "A takım kadron dolu (en fazla {limit} oyuncu); önce birini gönder."
EXCHANGE_TEXT = "Takas oyuncusu senin A takım oyuncun olmalı (kiralık, akademi ve yeni transfer olamaz)."
CONTRACT_INVALID_TEXT = "Geçersiz sözleşme teklifi."
MEDICAL_STAGE_TEXT = "Bekleyen bir sağlık kontrolü kararı yok."
OUT_ONLY_TEXT = "Bu işlem yalnızca kulübüne gelen tekliflerde yapılır."
IN_ONLY_TEXT = "Bu işlem yalnızca senin yaptığın transfer dosyalarında yapılır."
NOT_YOUR_PLAYER_TEXT = "{name} senin oyuncun değil."
SELLER_FLOOR_TEXT = "Kadron {floor} oyuncunun altına düşer; bu satış yapılamaz."
KEEPER_FLOOR_TEXT = "Elinde en az {floor} kaleci kalmalı; bu satış yapılamaz."
MOVED_TEXT = "{name} artık {team} kulübünde değil; dosya geçersiz."
RELEASE_NONE_TEXT = "{name} için serbest kalma bedeli yok."


class DeskError(TransferError):
    """Transfer masasi islemi yapilamaz (mesaj Turkce, dogrudan gosterilir)."""


# ---------------------------------------------------------------------------------------------------------------
# Gorunumler (arayuz icin duz degerler)
# ---------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowView:
    open: bool
    name: str | None
    label: str
    closes_after_week: int | None
    next_open_week: int | None
    summer: tuple[int, int]
    winter: tuple[int, int] | None


@dataclass(frozen=True)
class KnowledgeView:
    player_id: int
    knowledge: int                      # 0-100
    label: str
    assigned: bool                      # gozlemci gorevde
    weekly_gain: int                    # gorevdeyse haftalik artis
    can_bid: bool                       # KNOWN_THRESHOLD asildi


@dataclass(frozen=True)
class ScoutReportView:
    player_id: int
    name: str
    team: str | None
    age: int
    position: str
    knowledge: int
    knowledge_label: str
    known: bool                         # rapor var mi (bilgi esigi)
    overall: staff_rules.ScoutedValue | None
    attributes: dict                    # ozellik -> ScoutedValue
    value: staff_rules.ScoutedValue | None
    potential: tuple[int, int] | None   # bilgi 50+
    interest_label: str                 # bilgi 50+; aksi "Bilinmiyor"
    release_clause: int | None          # bilgi 50+ ve varsa
    injury_label: str | None            # bilgi 75+
    contract_text: str | None           # bilgi 75+
    margin: int | None


@dataclass(frozen=True)
class TermsView:
    fee: int
    upfront: int
    deferred: int
    instalment_months: int
    instalments: int
    instalment_amount: int
    add_ons: tuple[str, ...]
    add_ons_total: int
    sell_on_pct: int
    exchange_player_id: int | None
    exchange_player_name: str | None
    text: str
    add_on_items: tuple[AddOn, ...] = ()      # 13I: teklif kurucunun varsayilanlari (masadaki paket)


@dataclass(frozen=True)
class DealView:
    id: int
    direction: str                      # IN (aliyorum) / OUT (satiyorum)
    status: str
    status_label: str
    turn: str | None                    # MANAGER / CLUB / None
    player_id: int
    player_name: str
    position: str
    age: int
    seller_team_id: int | None
    seller_team: str | None
    buyer_team_id: int | None
    buyer_team: str | None
    terms: TermsView | None
    stance_label: str | None            # IN: kulubun tutumu
    club_message: str                   # son kulup / oyuncu mesaji
    demands: tuple[str, ...]            # kulubun istedigi degisiklikler
    mood: str | None                    # K12: sabir etiketi (sayi degil)
    interest_label: str | None
    value_text: str                     # IN: sisli deger; OUT: kesin deger
    expires_in_weeks: int | None
    window: WindowView
    completes_text: str | None          # AGREED: ne zaman tamamlanir
    contract: ContractOffer | None
    medical_label: str | None
    medical_notes: tuple[str, ...]
    add_on_progress: tuple[str, ...]    # tamamlanmis anlasmada ek odeme durumu
    history: tuple[str, ...]
    can_bid: bool
    can_accept_counter: bool
    can_withdraw: bool
    can_open_terms: bool
    can_confirm_medical: bool
    can_complete: bool
    can_accept_offer: bool
    can_reject_offer: bool
    can_counter_offer: bool
    reason: str = ""
    price_hint: tuple[int, int] | None = None   # IN: kulubun soyledigi SISLI fiyat araligi (bilgi alma)


@dataclass(frozen=True)
class DealSummary:
    """
    13I liste satiri (Transfer Merkezi listeleri, ana sayfa, menu sayaci): TEK sorguyla (oyuncu + iki kulup adi JOIN)
    kurulur; tam DealView yalnizca acilan dosya icin. K12: sabir sayisi yok, yalnizca etiket.
    """
    id: int
    direction: str
    status: str
    status_label: str
    turn: str | None
    last_action: str | None
    player_id: int
    player_name: str
    position: str
    age: int
    seller_team: str | None
    buyer_team: str | None
    terms_text: str | None
    fee: int
    upfront: int
    instalment_months: int
    sell_on_pct: int
    mood: str | None
    expires_in_weeks: int | None
    needs_action: bool                  # sira menajerde (ya da AGREED + donem acik: tamamlanabilir)
    can_accept_offer: bool
    can_reject_offer: bool
    can_counter_offer: bool
    reason: str = ""


@dataclass(frozen=True)
class TermsTable:
    """Sozlesme masasinin salt okunur goruntusu (13I): son adim + konusma gecmisi (negotiation_log_html girdisi)."""
    step: TermsStep
    log: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TermsStep:
    deal_id: int
    status: NegotiationStatus
    deal_status: str
    message: str
    complaints: tuple[str, ...]
    demand: ContractOffer | None        # OPEN: oyuncunun guncel talebi; ACCEPTED: anlasilan sozlesme
    rounds_left: int
    mood: str                           # K12: ikna skoru yerine ruh hali
    needs_room: int | None              # ACCEPTED ve maas alani yetmiyor: haftalik eksik
    medical_label: str | None = None
    medical_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaymentView:
    id: int
    deal_id: int | None
    kind: str
    kind_label: str
    direction: str                      # PAY (kulubum oduyor) / RECEIVE (kulubum aliyor)
    counterparty: str | None
    player_name: str | None
    amount: int
    paid_amount: int
    due_in_weeks: int | None
    due_season: int | None
    status: str
    status_label: str
    note: str


@dataclass(frozen=True)
class FinanceSummary:
    payable_scheduled: int              # gelecekte odenecek (taksit, sadakat)
    receivable_scheduled: int           # gelecekte alinacak
    overdue_payable: int
    overdue_receivable: int
    next_payments: tuple[PaymentView, ...]


# ---------------------------------------------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _ev(value) -> str:
    return str(getattr(value, "value", value))


def _money(amount) -> str:
    return finance.format_money(amount)


def _entries(deal: TransferDeal, kind: str) -> list[dict]:
    return [e for e in (deal.history or []) if isinstance(e, dict) and e.get("kind") == kind]


def _last(deal: TransferDeal, kind: str) -> dict | None:
    found = _entries(deal, kind)
    return found[-1] if found else None


def _hint_of(enquiry: dict | None) -> tuple[int, int] | None:
    """Bilgi alma kaydindaki SISLI fiyat araligi (kulubun menajere soyledigi; gizli hedef degil)."""
    hint = (enquiry or {}).get("hint")
    try:
        return (int(hint[0]), int(hint[1])) if hint else None
    except (TypeError, ValueError, IndexError):
        return None


def _terms_of(deal: TransferDeal) -> DealTerms:
    return DealTerms(fee=int(deal.fee), upfront=int(deal.upfront), instalment_months=int(deal.instalment_months),
                     add_ons=tuple(AddOn.from_dict(a) for a in deal.add_ons or ()),
                     sell_on_pct=int(deal.sell_on_pct), exchange_player_id=deal.exchange_player_id,
                     sell_on_profit=bool(getattr(deal, "sell_on_profit", False)),
                     buy_back_fee=getattr(deal, "buy_back_fee", None),
                     buy_back_seasons=int(getattr(deal, "buy_back_seasons", 0) or 0))


def _set_terms(deal: TransferDeal, terms: DealTerms) -> None:
    terms = rules.normalize_terms(terms)
    deal.fee, deal.upfront = int(terms.fee), terms.upfront_amount
    deal.instalment_months = int(terms.instalment_months)
    deal.add_ons = [a.to_dict() for a in terms.add_ons]
    deal.sell_on_pct = int(terms.sell_on_pct)
    deal.exchange_player_id = terms.exchange_player_id
    deal.sell_on_profit = bool(terms.sell_on_profit)                     # 15F
    deal.buy_back_fee = int(terms.buy_back_fee) if terms.buy_back_fee else None
    deal.buy_back_seasons = int(terms.buy_back_seasons)


def _contract_of(deal: TransferDeal) -> ContractOffer | None:
    try:
        return ContractOffer.from_dict(deal.contract) if deal.contract else None
    except (KeyError, TypeError, ValueError):
        return None


def _rng(*parts) -> random.Random:
    return random.Random(zlib.crc32("|".join(str(p) for p in ("desk", *parts)).encode()))


def _clean(text, limit: int = NOTE_MAX) -> str:
    return " ".join(str(text or "").split())[:limit]


def validate_contract(offer) -> ContractOffer:
    """Arayuzden gelen sozlesme teklifini dogrular (Turkce DeskError)."""
    try:
        role = SquadRole(_ev(offer.role))
        values = [int(offer.wage), int(offer.years), int(getattr(offer, "signing_fee", 0) or 0),
                  int(getattr(offer, "loyalty_bonus", 0) or 0), int(getattr(offer, "agent_fee", 0) or 0),
                  int(getattr(offer, "appearance_bonus", 0) or 0), int(getattr(offer, "goal_bonus", 0) or 0)]
        clause = getattr(offer, "release_clause", None)
        clause = None if clause is None else int(clause)
    except (AttributeError, TypeError, ValueError):
        raise DeskError(CONTRACT_INVALID_TEXT) from None
    wage, years, signing, loyalty, agent, apps, goals = values
    if min(wage, signing, loyalty, agent, apps, goals) < 0:
        raise DeskError("Sözleşme tutarları negatif olamaz.")
    if not transfers.MIN_YEARS <= years <= transfers.MAX_YEARS:
        raise DeskError(f"Sözleşme süresi {transfers.MIN_YEARS}-{transfers.MAX_YEARS} yıl olmalı.")
    if clause is not None and clause < MIN_RELEASE_CLAUSE:
        raise DeskError(f"Serbest kalma bedeli en az {_money(MIN_RELEASE_CLAUSE)} olmalı (ya da boş bırak).")
    return ContractOffer(wage=wage, years=years, role=role, signing_fee=signing, loyalty_bonus=loyalty,
                         agent_fee=agent, release_clause=clause, appearance_bonus=apps, goal_bonus=goals)


# ---------------------------------------------------------------------------------------------------------------
# Kontrolcu
# ---------------------------------------------------------------------------------------------------------------

class TransferDesk:
    """Oturumdaki menajerin (cm.user_team) AI kulupleriyle transfer masasi. Commit ETMEZ."""

    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm
        self.db = cm.db
        self._report = None
        self._humans: frozenset[int] | None = None
        self._open_override: bool | None = None      # haftalik adim: SONRAKI haftanin donemi

    # ================================================================== temel

    @property
    def cw(self) -> int:
        return int(self.cm.career_week)

    def _humans_now(self) -> frozenset[int]:
        return self._humans if self._humans is not None else self.cm.human_team_ids()

    def _team(self) -> Team:
        if self.cm.game_mode is GameMode.TOURNAMENT:
            raise DeskError(TOURNAMENT_TEXT)
        team = self.cm.user_team
        if team is None:
            raise DeskError(NO_TEAM_TEXT)
        return team

    def _player(self, player_id) -> Player:
        player = self.db.get(Player, player_id) if _is_id(player_id) else None
        if player is None:
            raise DeskError(PLAYER_NOT_FOUND_TEXT)
        return player

    def _season_weeks(self) -> int:
        return max(1, int(self.cm._projected_season_weeks() or 1))

    def _window(self, week: int | None = None, finished: bool | None = None) -> rules.TransferWindow:
        week = self.cm.current_week if week is None else week
        finished = self.cm.season_finished if finished is None else finished
        return rules.transfer_window(week, self._season_weeks(), bool(finished))

    def _is_open(self) -> bool:
        """Tamamlama icin gecerli donem: UI'da bu hafta, haftalik adimda sonraki hafta (hafta sinirinda tamamlanir)."""
        return self._open_override if self._open_override is not None else self._window().open

    def window(self) -> WindowView:
        """Bu haftanin transfer donemi (arayuz ust seridi)."""
        w = self._window()
        return WindowView(w.open, w.name, w.label, w.closes_after_week, w.next_open_week, w.summer, w.winter)

    def _log(self, deal: TransferDeal, entry: dict) -> None:
        deal.history = [*(deal.history or []), {"cw": self.cw, **entry}]
        deal.updated_career_week = self.cw
        deal.updated_at = _now()

    def _lock_deal(self, deal_id) -> TransferDeal:
        if not _is_id(deal_id):
            raise DeskError(DEAL_NOT_FOUND_TEXT)
        self.db.flush()
        deal = self.db.scalar(select(TransferDeal).where(TransferDeal.id == deal_id).with_for_update()
                              .execution_options(populate_existing=True))
        if deal is None:
            raise DeskError(DEAL_NOT_FOUND_TEXT)
        return deal

    def _my_deal(self, deal_id, direction: str | None = None) -> tuple[TransferDeal, Team]:
        team = self._team()
        deal = self._lock_deal(deal_id)
        if deal.human_team_id != team.id:
            raise DeskError(DEAL_NOT_FOUND_TEXT)
        if direction is not None and deal.direction != direction:
            raise DeskError(IN_ONLY_TEXT if direction == IN else OUT_ONLY_TEXT)
        return deal, team

    def _require_open(self, deal: TransferDeal) -> None:
        if deal.status not in OPEN:
            raise DeskError(CLOSED_TEXT.format(status=STATUS_LABELS.get(deal.status, deal.status)))

    def _set_status(self, deal: TransferDeal, status: str, reason: str | None = None, turn: str | None = None) -> None:
        deal.status = status
        deal.turn = turn
        if reason is not None:
            deal.reason = _clean(reason)
        if status not in OPEN:
            deal.expires_career_week = None
            deal.response_due_week = None
        self._log(deal, {"kind": "status", "status": status, "reason": _clean(reason) if reason else None})

    def _team_name(self, team_id: int | None) -> str | None:
        team = self.db.get(Team, team_id) if team_id is not None else None
        return team.name if team is not None else None

    def _safe(self, label: str, fn, *args) -> None:
        """Haftalik adim kendi savepoint'inde: hata loglanir, hafta ilerlemesi bozulmaz."""
        self.db.flush()
        try:
            with self.db.begin_nested():
                fn(*args)
        except (SQLAlchemyError, ValueError, TransferError, finance.BudgetError) as exc:
            log.exception("Transfer masası adımı başarısız (%s): %s", label, exc)

    # ================================================================== bildirim / haber / rapor

    def _seat_id(self, team_id: int | None) -> int | None:
        seat = self.cm.seats.by_team(team_id) if team_id is not None else None
        return seat.id if seat is not None and seat.id is not None else None

    def _notify(self, deal: TransferDeal | None, team_id: int | None, text: str,
                kind: NotificationKind = NotificationKind.OFFER_UPDATE) -> None:
        """Insan kulubunun koltuguna bildirim (koltuk satiri yoksa yalnizca masa kaydi) + haftalik rapor notu."""
        if team_id is None or not text:
            return
        if self._report is not None and team_id in self._humans_now():
            self.cm._sink(self._report, team_id).transfer_notes.append(_clean(text))
        seat_id = self._seat_id(team_id)
        if seat_id is None:
            return
        self.db.flush()
        try:
            with self.db.begin_nested():
                messaging.notify(self.db, seat_id, kind, text, DEAL_REF, deal.id if deal is not None else None)
        except (SQLAlchemyError, ValueError) as exc:
            log.warning("Transfer masası bildirimi yazılamadı: %s", exc)

    def _rumour(self, text: str, team_id: int | None, other_id: int | None) -> None:
        self.cm._add_news(NewsKind.RUMOUR, text, team_id=team_id, other_team_id=other_id)

    # ================================================================== gozlem

    def _assignment(self, team_id: int, player_id: int) -> ScoutAssignment | None:
        self.db.flush()
        return self.db.scalar(select(ScoutAssignment).where(ScoutAssignment.team_id == team_id,
                                                            ScoutAssignment.player_id == player_id))

    def knowledge_of(self, team: Team, player: Player) -> int:
        """Kulubun oyuncu hakkindaki bilgisi (0-100): kendi oyuncusu 100, ayni lig en az 35, gozlem kaydi."""
        if player.team_id == team.id:
            return rules.MAX_KNOWLEDGE
        row = self._assignment(team.id, player.id)
        known = int(row.knowledge) if row is not None else 0
        club = self.db.get(Team, player.team_id) if player.team_id is not None else None
        if club is not None and club.league_id == team.league_id:
            known = max(known, rules.SAME_LEAGUE_KNOWLEDGE)
        if player.team_id is None:                        # 15A: serbest oyuncunun profili menajerlerde dolasir
            known = max(known, contracts.FREE_AGENT_KNOWLEDGE)
        return min(rules.MAX_KNOWLEDGE, known)

    def _gain(self, team: Team) -> int:
        return rules.weekly_scouting_gain(self.cm.scout_rating(team))

    def knowledge(self, player_id: int) -> KnowledgeView:
        team = self._team()
        player = self._player(player_id)
        row = self._assignment(team.id, player.id)
        k = self.knowledge_of(team, player)
        return KnowledgeView(player.id, k, rules.knowledge_label(k),
                             row is not None and row.status == "ASSIGNED", self._gain(team),
                             k >= rules.KNOWN_THRESHOLD)

    def scout(self, player_id: int) -> KnowledgeView:
        """Gozlemci gorevi: bilgi her hafta artar (en fazla 100). Zaten tam biliniyorsa bir sey yapilmaz."""
        team = self._team()
        player = self._player(player_id)
        if player.team_id == team.id:
            raise DeskError(OWN_PLAYER_TEXT.format(name=player.name))
        known = self.knowledge_of(team, player)
        row = self._assignment(team.id, player.id)
        if known < rules.MAX_KNOWLEDGE:
            if row is None:
                try:
                    with self.db.begin_nested():
                        row = ScoutAssignment(team_id=team.id, player_id=player.id, knowledge=known, status="ASSIGNED",
                                              assigned_career_week=self.cw, updated_career_week=self.cw)
                        self.db.add(row)
                        self.db.flush()
                except IntegrityError:
                    row = self._assignment(team.id, player.id)
            if row is not None and row.status != "ASSIGNED":
                row.status = "ASSIGNED"
                row.knowledge = max(int(row.knowledge), known)
                row.updated_career_week = self.cw
            self.db.flush()
        return self.knowledge(player.id)

    def scout_report(self, player_id: int) -> ScoutReportView:
        """Bilgi yuzdesiyle olceklenen sisli rapor (K12: kesin sayi yalnizca kendi oyuncunda)."""
        team = self._team()
        player = self._player(player_id)
        k = self.knowledge_of(team, player)
        own = player.team_id == team.id
        margin = 0 if own else rules.knowledge_margin(self.cm.scout_margin(team), k)
        club = self._team_name(player.team_id)
        if margin is None:
            return ScoutReportView(player.id, player.name, club, int(player.age), _ev(player.position), k,
                                   rules.knowledge_label(k), False, None, {}, None, None, "Bilinmiyor", None, None,
                                   None, None)
        seed = (self.cm.scout_rating(team) or 0, player.id)
        attrs = {name: staff_rules.scouted_value(getattr(player, name), margin, (*seed, name))
                 for name in ("pace", "shooting", "passing", "defending", "dribbling", "goalkeeping")}
        overall = staff_rules.scouted_value(player.overall_rating, margin, (*seed, "overall_rating"))
        value = staff_rules.scouted_money(int(player.market_value), margin, (*seed, "value"))
        detail = k >= rules.DETAIL_THRESHOLD
        full = k >= rules.FULL_THRESHOLD
        interest = self._interest(player, team).label if detail and not own else ("—" if own else "Bilinmiyor")
        prone = rules.hidden_trait("injury", player.id, (player.fm_attributes or {}).get("injury_proneness"))
        contract = None
        if full:
            wage_band = staff_rules.scouted_money(int(player.current_wage or 0), 0 if own else 10,
                                                  (*seed, "wage"))
            contract = (f"Sözleşmesi {int(player.contract_years)} yıl · maaş {_money(wage_band.low)}"
                        + ("" if wage_band.exact else f"-{_money(wage_band.high)}") + "/hafta")
        return ScoutReportView(
            player.id, player.name, club, int(player.age), _ev(player.position), k, rules.knowledge_label(k), True,
            overall, attrs, value, self.cm.potential_estimate(team, player) if detail else None, interest,
            player.release_clause if detail else None, rules.proneness_label(prone) if full else None, contract,
            margin)

    # ================================================================== isteklilik ve tutum

    def _league_rep(self, team: Team | None) -> float:
        if team is None:
            return 50.0
        teams = team.league.teams if team.league is not None else [team]
        return sum(t.reputation for t in teams) / max(1, len(teams))

    def _interest(self, player: Player, buyer: Team) -> rules.PlayerInterest:
        current = self.db.get(Team, player.team_id) if player.team_id is not None else None
        clauses = player.contract_clauses or {}
        ambition = rules.hidden_trait("ambition", player.id, (player.fm_attributes or {}).get("ambition"))
        return rules.player_interest(
            overall=player.overall_rating, ambition=ambition,
            current_rep=current.reputation if current is not None else buyer.reputation,
            buyer_rep=buyer.reputation, manager_rep=self.cm.manager_reputation_for(buyer),
            current_league_rep=self._league_rep(current), buyer_league_rep=self._league_rep(buyer),
            current_role=player.squad_role, offered_role=transfers.suggested_role(player, buyer),
            concern_level=int(player.concern_level or 0), contract_years=int(player.contract_years or 0),
            listed=bool(player.transfer_listed), wants_away=bool(clauses.get("wants_away")))

    def _floor_reason(self, player: Player, seller: Team) -> str | None:
        """Satici kulubun kadro / kaleci tabani (transfers.evaluate_fee ile ayni anlam)."""
        others = [p for p in seller.players if p.id != player.id]
        if len(others) < transfers.SQUAD_FLOOR - 1:
            return "Kadro çok daralır, kulüp satışa kapalı."
        floor = transfers.POSITION_SALE_FLOOR.get(player.position)
        if floor is not None and sum(1 for p in others if p.position is player.position) < floor:
            return f"Kulüp {player.position.value} mevkisinde yedeksiz kalır, satışa kapalı."
        return None

    def _stance(self, deal: TransferDeal, player: Player, seller: Team, buyer: Team) -> rules.ClubStance:
        asking = transfers.asking_price(player, seller, buyer.reputation)
        return rules.club_stance(
            asking=asking, importance=transfers.squad_importance(player, seller.players),
            contract_years=int(player.contract_years or 0), listed=bool(player.transfer_listed),
            concern_level=int(player.concern_level or 0), seller_budget=int(seller.transfer_budget),
            rivalry_level=rules.rivalry(seller.name, buyer.name, seller.league_id == buyer.league_id),
            age=int(player.age), overall=int(player.overall_rating), potential=player.potential_rating,
            noise=rules.deal_noise(deal.id), floor_reason=self._floor_reason(player, seller))

    def _exchange_value(self, exchange: Player | None, receiver: Team) -> int:
        """Takas oyuncusunun alan kulup icin degeri: mevkisindeki en iyiden iyiyse %90, ortalamanin ustunde %60."""
        if exchange is None:
            return 0
        check = transfers.check_interest(exchange.overall_rating, receiver.reputation,
                                         self.cm.manager_reputation_for(receiver))
        if not check.interested:
            return 0
        group = [p.overall_rating for p in receiver.players if p.position is exchange.position]
        value = int(exchange.market_value or 0)
        if not group or exchange.overall_rating >= max(group):
            return int(value * 0.9)
        if exchange.overall_rating >= sum(group) / len(group):
            return int(value * 0.6)
        return 0

    def _ctx(self, player: Player, seller: Team, buyer: Team, exchange: Player | None = None) -> rules.ValuationContext:
        league = sorted(buyer.league.teams if buyer.league is not None else [buyer],
                        key=lambda t: (-t.reputation, t.id))
        rank = next((i for i, t in enumerate(league, 1) if t.id == buyer.id), len(league))
        young = int(player.age) <= rules.SELL_ON_WANTED_AGE and (player.potential_rating or 0) - \
            player.overall_rating >= rules.SELL_ON_POTENTIAL_GAP
        return rules.ValuationContext(
            league_weeks=int(self.cm.league_weeks() or 6), cup_weeks=3, position=_ev(player.position),
            expected_role=transfers.suggested_role(player, buyer).value,
            title_odds=rules.title_odds(rank, len(league)), cup_odds=rules.cup_odds(buyer.reputation),
            resale_value=int(player.market_value or 0), young=young,
            exchange_value=self._exchange_value(exchange, seller),
            buyer_risk=0.85 if self._overdue_amount(buyer.id) > 0 else 1.0)

    # ================================================================== borc

    def _overdue_amount(self, team_id: int | None) -> int:
        if team_id is None:
            return 0
        self.db.flush()
        return int(self.db.scalar(select(func.coalesce(func.sum(TransferPayment.amount - TransferPayment.paid_amount),
                                                       0)).where(TransferPayment.payer_team_id == team_id,
                                                                 TransferPayment.status == OVERDUE)) or 0)

    # ================================================================== IN: bilgi alma ve teklif

    def _check_target(self, team: Team, player: Player) -> Team:
        """Menajerin AI kulubundeki oyuncuya teklif yapabilmesi. Satici kulubu dondurur."""
        if player.team_id == team.id:
            raise DeskError(OWN_PLAYER_TEXT.format(name=player.name))
        seller = self.db.get(Team, player.team_id) if player.team_id is not None else None
        if seller is None:
            raise DeskError(NO_CLUB_TEXT.format(name=player.name))
        if player.in_academy:
            raise DeskError(ACADEMY_TEXT.format(name=player.name, team=seller.name))
        if seller.id in self._humans_now():
            raise DeskError(HUMAN_SELLER_TEXT.format(team=seller.name, name=player.name))
        reason = self.cm.transfer_block_reason(player)
        if reason:
            raise DeskError(f"{player.name} için teklif yapılamaz. {reason}.")
        k = self.knowledge_of(team, player)
        if k < rules.KNOWN_THRESHOLD:
            raise DeskError(UNKNOWN_TEXT.format(name=player.name, k=k, need=rules.KNOWN_THRESHOLD))
        blocked = self.db.scalar(select(func.max(TransferDeal.talks_blocked_until)).where(
            TransferDeal.player_id == player.id, TransferDeal.buyer_team_id == team.id))
        if blocked is not None and int(blocked) > self.cw:
            raise DeskError(TALKS_BLOCKED_TEXT.format(team=seller.name, weeks=int(blocked) - self.cw))
        return seller

    def _open_deal(self, player_id: int, buyer_id: int) -> TransferDeal | None:
        self.db.flush()
        return self.db.scalar(select(TransferDeal).where(
            TransferDeal.player_id == player_id, TransferDeal.buyer_team_id == buyer_id,
            TransferDeal.status.in_(sorted(OPEN))).with_for_update().execution_options(populate_existing=True))

    def _new_deal(self, *, direction: str, player: Player, seller: Team, buyer: Team, human: Team,
                  status: str, patience: int) -> TransferDeal:
        deal = TransferDeal(season=int(self.cm.season), created_career_week=self.cw, updated_career_week=self.cw,
                            direction=direction, status=status, turn=MANAGER, player_id=player.id,
                            seller_team_id=seller.id, buyer_team_id=buyer.id, human_team_id=human.id, fee=0,
                            upfront=0, instalment_months=0, add_ons=[], sell_on_pct=0, round=0,
                            patience=int(patience), history=[], contract={}, medical={}, updated_at=_now())
        try:
            with self.db.begin_nested():
                self.db.add(deal)
                self.db.flush()
        except IntegrityError:
            existing = self._open_deal(player.id, buyer.id)
            if existing is None:
                raise DeskError("Dosya bu sırada güncellendi; tekrar dene.") from None
            return existing
        return deal

    def _in_deal(self, team: Team, player: Player, seller: Team) -> TransferDeal:
        deal = self._open_deal(player.id, team.id)
        if deal is not None:
            return deal
        deal = self._new_deal(direction=IN, player=player, seller=seller, buyer=team, human=team, status=ENQUIRY,
                              patience=1)
        deal.patience = self._stance(deal, player, seller, team).patience
        deal.expires_career_week = self.cw + ENQUIRY_VALID_WEEKS
        return deal

    def enquire(self, player_id: int) -> DealView:
        """Kulubun tutumunu ve (sisli) fiyat beklentisini sorar; dosya ENQUIRY olarak acilir ya da tazelenir."""
        team = self._team()
        player = self._player(player_id)
        seller = self._check_target(team, player)
        deal = self._in_deal(team, player, seller)
        stance = self._stance(deal, player, seller, team)
        k = self.knowledge_of(team, player)
        hint = None
        if stance.kind not in (rules.STANCE_UNAVAILABLE, rules.STANCE_NOT_FOR_SALE):
            margin = max(4, rules.knowledge_margin(self.cm.scout_margin(team), k) or 12)
            band = staff_rules.scouted_money(stance.target, margin, ("enquiry", deal.id, k // 25))
            hint = [int(round(band.low / 100_000) * 100_000), int(round(band.high / 100_000) * 100_000)]
        message = self._stance_message(stance, seller, player, hint)
        interest = self._interest(player, team).label if k >= rules.DETAIL_THRESHOLD else "Bilinmiyor"
        self._log(deal, {"kind": "enquiry", "stance": stance.kind, "hint": hint, "interest": interest,
                         "message": message})
        self.db.flush()
        return self._view(deal)

    @staticmethod
    def _stance_message(stance: rules.ClubStance, seller: Team, player: Player, hint: list | None) -> str:
        if stance.kind == rules.STANCE_UNAVAILABLE:
            return f"{seller.name}: {stance.reason}"
        if stance.kind == rules.STANCE_NOT_FOR_SALE:
            return (f"{seller.name}: \"{player.name} satılık değil. Reddedemeyeceğimiz bir teklif gelirse "
                    f"değerlendiririz.\"")
        band = f"{_money(hint[0])}-{_money(hint[1])}" if hint else "makul"
        if stance.kind == rules.STANCE_LISTED:
            return f"{seller.name}: \"{player.name} ayrılabilir; {band} civarı bir teklif yeterli olabilir.\""
        if stance.kind == rules.STANCE_SURPLUS:
            return f"{seller.name}: \"{player.name} planlarımızda yok; {band} civarına bırakırız.\""
        return f"{seller.name}: \"{player.name} için {band} civarında bir teklifi değerlendiririz.\""

    def _validate_exchange(self, team: Team, seller: Team, terms: DealTerms) -> Player | None:
        if terms.exchange_player_id is None:
            return None
        exchange = self.db.get(Player, terms.exchange_player_id) if _is_id(terms.exchange_player_id) else None
        if exchange is None or exchange.team_id != team.id or exchange.in_academy or \
                exchange.loan_from_team_id is not None or self.cm.transfer_block_reason(exchange):
            raise DeskError(EXCHANGE_TEXT)
        check = transfers.check_interest(exchange.overall_rating, seller.reputation,
                                         self.cm.manager_reputation_for(seller))
        if not check.interested:
            raise DeskError(f"{exchange.name} {seller.name} kulübüne gitmek istemiyor: \"{check.reason}\"")
        others = [p for p in team.players if p.id != exchange.id]
        if len(others) < transfers.SQUAD_FLOOR - 1:
            raise DeskError(SELLER_FLOOR_TEXT.format(floor=transfers.SQUAD_FLOOR))
        return exchange

    def _clean_terms(self, terms: DealTerms, *, allow_exchange: bool) -> DealTerms:
        if not isinstance(terms, DealTerms):
            raise DeskError("Geçersiz teklif.")
        problems = rules.validate_terms(terms, allow_exchange=allow_exchange)
        if problems:
            raise DeskError(" ".join(problems[:3]))
        return rules.normalize_terms(terms)

    def make_bid(self, player_id: int, terms: DealTerms) -> DealView:
        """
        Yapilandirilmis bonservis teklifi. Pesinat kasadan karsilanabilmeli; gecikmis borcu olan kulup teklif yapamaz.
        Kulup bu hafta RESPONSES_PER_WEEK kez yanit verdiyse teklif sirada bekler (haftalik adimda yanitlanir).
        """
        team = self._team()
        player = self._player(player_id)
        seller = self._check_target(team, player)
        terms = self._clean_terms(terms, allow_exchange=True)
        self._validate_exchange(team, seller, terms)
        overdue = self._overdue_amount(team.id)
        if overdue > 0:
            raise DeskError(DEBT_TEXT.format(amount=_money(overdue)))
        if terms.upfront_amount > int(team.transfer_budget):
            raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(terms.upfront_amount)))
        deal = self._in_deal(team, player, seller)
        if deal.status not in (ENQUIRY, BIDDING):
            raise DeskError(FEE_AGREED_TEXT)
        if deal.turn == CLUB:
            raise DeskError(CLUB_THINKING_TEXT)
        first = deal.round == 0
        _set_terms(deal, terms)
        deal.status, deal.turn, deal.last_action = BIDDING, CLUB, "BID"
        deal.round = int(deal.round) + 1
        deal.reason = None
        self._log(deal, {"kind": "bid", "side": MANAGER, "terms": terms.to_dict()})
        if first:
            self._rumour(f"Transfer söylentisi: {team.name}, {player.name} için {seller.name} kulübüne resmi teklif "
                         f"yaptı.", team.id, seller.id)
        if self._responses_this_week(deal) < RESPONSES_PER_WEEK:
            self._club_answer(deal)
        else:
            deal.response_due_week = self.cw + 1
            deal.expires_career_week = None
            self._log(deal, {"kind": "queued", "message": "Kulüp teklifi değerlendiriyor; yanıt gelecek hafta."})
        self.db.flush()
        return self._view(deal)

    def _responses_this_week(self, deal: TransferDeal) -> int:
        return sum(1 for e in _entries(deal, "response") if e.get("cw") == self.cw and e.get("side") == CLUB)

    def _club_answer(self, deal: TransferDeal) -> None:
        """Siradaki kulup yaniti (IN: AI satici; OUT: AI alici). Anlasma kilitli olmali."""
        if deal.direction == IN:
            self._seller_answer(deal)
        else:
            self._buyer_answer(deal)

    def _seller_answer(self, deal: TransferDeal) -> None:
        player = self.db.get(Player, deal.player_id)
        seller = self.db.get(Team, deal.seller_team_id)
        buyer = self.db.get(Team, deal.buyer_team_id)
        if self._void_if_invalid(deal, player):
            return
        terms = _terms_of(deal)
        exchange = self.db.get(Player, terms.exchange_player_id) if terms.exchange_player_id else None
        stance = self._stance(deal, player, seller, buyer)
        ctx = self._ctx(player, seller, buyer, exchange)
        response = rules.seller_response(_rng("seller", deal.id, deal.round), terms, stance, ctx,
                                         int(deal.patience), int(deal.round))
        self._apply_response(deal, response, player, seller, buyer)

    def _apply_response(self, deal: TransferDeal, response: rules.ClubResponse, player: Player, seller: Team,
                        buyer: Team) -> None:
        deal.patience = max(0, int(deal.patience) - int(response.patience_cost))
        deal.response_due_week = None
        entry = {"kind": "response", "side": CLUB, "action": response.action, "message": response.message,
                 "demands": list(response.demands),
                 "terms": response.counter.to_dict() if response.counter is not None else None}
        self._log(deal, entry)
        club = seller if deal.direction == IN else buyer
        human_id = deal.human_team_id
        if response.action == rules.ACTION_ACCEPT:
            deal.last_action = "ACCEPT"
            deal.reason = None
            if deal.direction == IN:
                deal.status, deal.turn = TERMS, MANAGER
                deal.expires_career_week = self.cw + TERMS_VALID_WEEKS
                self._rumour(f"Transfer: {buyer.name} ile {seller.name}, {player.name} için bonservis konusunda "
                             f"anlaştı; oyuncuyla sözleşme görüşmeleri başlıyor.", buyer.id, seller.id)
                self._notify(deal, human_id, f"{seller.name} {player.name} için teklifini kabul etti "
                                             f"({_terms_of(deal).describe()}). Sözleşme masasını aç.")
            else:
                self._fee_agreed_out(deal, player, seller, buyer)
            return
        if response.action == rules.ACTION_COUNTER and response.counter is not None:
            _set_terms(deal, response.counter)
            deal.turn, deal.last_action = MANAGER, "COUNTER"
            deal.expires_career_week = self.cw + COUNTER_VALID_WEEKS
            deal.reason = _clean(response.message)
            self._notify(deal, human_id, f"{club.name} karşı teklif yaptı ({player.name}): "
                                         f"{_terms_of(deal).describe()}.")
            return
        if response.action == rules.ACTION_REJECT:
            deal.turn, deal.last_action = MANAGER, "REJECT"
            deal.expires_career_week = self.cw + COUNTER_VALID_WEEKS
            deal.reason = _clean(response.message)
            self._notify(deal, human_id, f"{club.name}: {response.message} ({player.name})")
            return
        deal.last_action = "END"
        deal.talks_blocked_until = self.cw + TALKS_COOLDOWN_WEEKS if deal.direction == IN else None
        self._set_status(deal, REJECTED, response.message)
        self._notify(deal, human_id, f"{club.name}: {response.message} ({player.name})")

    def accept_counter(self, deal_id: int) -> DealView:
        """Kulubun karsi teklifini kabul: bonservis anlasmasi -> kisisel sartlar (TERMS)."""
        deal, team = self._my_deal(deal_id, IN)
        self._require_open(deal)
        if deal.status != BIDDING or deal.turn != MANAGER or deal.last_action != "COUNTER":
            raise DeskError(NO_COUNTER_TEXT)
        player = self.db.get(Player, deal.player_id)
        if self._void_if_invalid(deal, player):
            raise DeskError(deal.reason or DEAL_NOT_FOUND_TEXT)
        seller = self.db.get(Team, deal.seller_team_id)
        terms = _terms_of(deal)
        self._validate_exchange(team, seller, terms)
        if terms.upfront_amount > int(team.transfer_budget):
            raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(terms.upfront_amount)))
        self._log(deal, {"kind": "response", "side": MANAGER, "action": "ACCEPT", "message": "Karşı teklif kabul",
                         "demands": [], "terms": terms.to_dict()})
        deal.status, deal.turn, deal.last_action = TERMS, MANAGER, "ACCEPT"
        deal.expires_career_week = self.cw + TERMS_VALID_WEEKS
        deal.reason = None
        self._rumour(f"Transfer: {team.name} ile {seller.name}, {player.name} için bonservis konusunda anlaştı.",
                     team.id, seller.id)
        self.db.flush()
        return self._view(deal)

    def withdraw(self, deal_id: int) -> DealView:
        """Menajer dosyadan cekilir (tamamlanmadan once; para hareket etmez)."""
        deal, _team = self._my_deal(deal_id, IN)
        self._require_open(deal)
        self._set_status(deal, WITHDRAWN, "Menajer görüşmelerden çekildi.")
        self.db.flush()
        return self._view(deal)

    # ================================================================== IN: kisisel sartlar

    def _terms_snapshot(self, deal: TransferDeal, player: Player, buyer: Team) -> dict:
        self.db.flush()
        ratings = list(self.db.scalars(select(Player.overall_rating).where(
            Player.team_id == buyer.id, Player.in_academy.is_(False),
            Player.id != (deal.exchange_player_id or -1)).order_by(Player.overall_rating.desc(), Player.id)))
        seller = self.db.get(Team, player.team_id) if player.team_id is not None else None
        interest = self._interest(player, buyer)
        refusal = None
        if interest.refuses:
            refusal = f"{player.name}: \"{interest.reason}\" — sözleşme masasına oturmadı."
        else:
            level = squad_level_refusal(player.overall_rating, ratings)
            if level:
                refusal = f"{player.name}: \"{level}\" — sözleşme masasına oturmadı."
        return {
            "kind": "terms_open",
            "player": {"name": player.name, "overall": int(player.overall_rating), "age": int(player.age),
                       "wage": int(player.current_wage or 0), "position": _ev(player.position),
                       "value": int(player.market_value or 0),
                       "team_rep": int(seller.reputation) if seller is not None else int(buyer.reputation)},
            "buyer": {"rep": int(buyer.reputation), "ratings": [int(r) for r in ratings]},
            "manager_rep": float(self.cm.manager_reputation_for(buyer)), "fee": int(deal.fee),
            "multiplier": float(interest.wage_multiplier), "interest": interest.label, "refusal": refusal,
        }

    @staticmethod
    def _negotiation_from(deal: TransferDeal, opening: dict) -> ContractNegotiation:
        p, b = opening["player"], opening["buyer"]
        player = SimpleNamespace(id=deal.player_id, name=p["name"], overall_rating=int(p["overall"]),
                                 age=int(p["age"]), current_wage=int(p["wage"]), position=Position(p["position"]),
                                 market_value=int(p["value"]), team=SimpleNamespace(reputation=int(p["team_rep"])))
        buyer = SimpleNamespace(id=deal.buyer_team_id, reputation=int(b["rep"]),
                                players=[SimpleNamespace(overall_rating=int(r)) for r in b["ratings"]])
        rng = random.Random(zlib.crc32(f"desk-terms|{deal.id}|{deal.buyer_team_id}|{deal.player_id}".encode()))
        negotiation = ContractNegotiation(rng, player, buyer, int(opening["fee"]),
                                          manager_reputation=float(opening["manager_rep"]), agent=True,
                                          demand_multiplier=float(opening.get("multiplier", 1.0)))
        if opening.get("refusal") and negotiation.open:
            negotiation.status = NegotiationStatus.WALKED_AWAY
            negotiation.opening_message = opening["refusal"]
        return negotiation

    def _replay(self, deal: TransferDeal):
        """(negotiation, son yanit) ya da (None, None): masa history'den sirayla yeniden oynatilir."""
        history = deal.history or []
        start = max((i for i, e in enumerate(history) if isinstance(e, dict) and e.get("kind") == "terms_open"),
                    default=None)
        if start is None:
            return None, None
        negotiation = self._negotiation_from(deal, history[start])
        last = None
        for entry in history[start + 1:]:
            if not isinstance(entry, dict) or entry.get("kind") != "terms_bid":
                continue
            if not negotiation.open:
                break
            last = negotiation.respond(ContractOffer.from_dict(entry["offer"]))
        return negotiation, last

    def _step(self, deal: TransferDeal, negotiation: ContractNegotiation, response) -> TermsStep:
        medical = deal.medical or {}
        med_label, med_notes = medical.get("label"), tuple(medical.get("notes") or ())
        if response is None and not negotiation.open:
            return TermsStep(deal.id, negotiation.status, deal.status, negotiation.opening_message or "", (), None, 0,
                             "Görüşmeyi reddetti", None, med_label, med_notes)
        if response is None:
            message = f"{negotiation.player.name} ve menajeri taleplerini açıkladı: {negotiation.demand.describe()}"
            complaints: tuple[str, ...] = ()
        else:
            message, complaints = response.message, tuple(response.complaints)
        if negotiation.status is NegotiationStatus.ACCEPTED:
            agreed = negotiation.last_offer
            buyer = self.db.get(Team, deal.buyer_team_id)
            freed = 0
            if deal.exchange_player_id is not None:
                exchange = self.db.get(Player, deal.exchange_player_id)
                freed = int(exchange.current_wage or 0) if exchange is not None and exchange.team_id == buyer.id \
                    else 0
            needs = max(0, int(agreed.wage) - (int(buyer.free_wage) + freed))
            return TermsStep(deal.id, negotiation.status, deal.status, message, complaints, agreed,
                             negotiation.rounds_left, "Anlaştı", needs or None, med_label, med_notes)
        demand = negotiation.demand if negotiation.open else None
        mood = rules.terms_mood(negotiation.persuasion(negotiation.last_offer), negotiation.required_persuasion) \
            if negotiation.open and negotiation.last_offer is not None else \
            ("Görüşmeyi bitirdi" if not negotiation.open else "Talebini açıkladı")
        return TermsStep(deal.id, negotiation.status, deal.status, message, complaints, demand,
                         negotiation.rounds_left, mood, None, med_label, med_notes)

    def _terms_deal(self, deal_id) -> tuple[TransferDeal, Team, Player]:
        deal, team = self._my_deal(deal_id, IN)
        self._require_open(deal)
        if deal.status != TERMS:
            raise DeskError(TERMS_STAGE_TEXT)
        player = self.db.get(Player, deal.player_id)
        if self._void_if_invalid(deal, player):
            raise DeskError(deal.reason or DEAL_NOT_FOUND_TEXT)
        return deal, team, player

    def open_terms(self, deal_id: int) -> TermsStep:
        """Sozlesme masasi (TERMS). Ilk cagri anlik goruntuyu yazar; sonrakiler history'den yeniden kurar."""
        deal, team, player = self._terms_deal(deal_id)
        negotiation, response = self._replay(deal)
        if negotiation is None:
            self._log(deal, self._terms_snapshot(deal, player, team))
            negotiation, response = self._replay(deal)
            if not negotiation.open:
                self._set_status(deal, COLLAPSED, negotiation.opening_message)
                self._notify(deal, team.id, f"Transfer çöktü: {negotiation.opening_message}")
        self.db.flush()
        return self._step(deal, negotiation, response)

    def submit_terms(self, deal_id: int, offer: ContractOffer) -> TermsStep:
        """Oyuncuya / menajerine sozlesme teklifi. Kabulde saglik kontrolu hemen yapilir."""
        contract = validate_contract(offer)
        deal, team, player = self._terms_deal(deal_id)
        negotiation, response = self._replay(deal)
        if negotiation is None:
            raise DeskError(TERMS_NOT_OPEN_TEXT)
        if not negotiation.open:
            return self._step(deal, negotiation, response)
        response = negotiation.respond(contract)
        self._log(deal, {"kind": "terms_bid", "offer": contract.to_dict()})
        if response.status is NegotiationStatus.WALKED_AWAY:
            self._set_status(deal, COLLAPSED, response.message)
            self._notify(deal, team.id, f"Transfer çöktü ({player.name}): {response.message}")
        elif response.status is NegotiationStatus.ACCEPTED:
            deal.contract = contract.to_dict()
            self._run_medical(deal, player, team)
        self.db.flush()
        return self._step(deal, negotiation, response)

    # ================================================================== IN: saglik kontrolu

    def _recent_injuries(self, player: Player) -> int:
        self.db.flush()
        return int(self.db.scalar(select(func.count()).select_from(PlayerMatchStat).join(
            Fixture, Fixture.id == PlayerMatchStat.fixture_id).where(
            PlayerMatchStat.player_id == player.id, PlayerMatchStat.injured.is_(True),
            Fixture.season >= int(self.cm.season) - 1)) or 0)

    def _run_medical(self, deal: TransferDeal, player: Player, team: Team) -> None:
        prone = rules.hidden_trait("injury", player.id, (player.fm_attributes or {}).get("injury_proneness"))
        result = rules.medical_check(injured_weeks_left=max(0, int(player.injured_until_week or 0) -
                                                                int(self.cm.current_week)),
                                     proneness=prone, recent_injuries=self._recent_injuries(player),
                                     age=int(player.age))
        deal.medical = {**result.to_dict(), "cw": self.cw}
        self._log(deal, {"kind": "medical", "result": result.result, "notes": list(result.notes)})
        if result.result == rules.MEDICAL_FAIL:
            self._set_status(deal, COLLAPSED, f"{player.name} sağlık kontrolünden kaldı: " + " ".join(result.notes))
            self._notify(deal, team.id, f"Transfer çöktü: {player.name} sağlık kontrolünden kaldı.")
        elif result.result == rules.MEDICAL_RISK:
            deal.status, deal.turn = MEDICAL, MANAGER
            deal.expires_career_week = self.cw + TERMS_VALID_WEEKS
            self._notify(deal, team.id, f"{player.name} sağlık kontrolü riskli: " + " ".join(result.notes)
                         + " Devam edip etmeyeceğine karar ver.")
        else:
            self._agreed(deal, player, team)

    def _agreed(self, deal: TransferDeal, player: Player, team: Team) -> None:
        deal.status, deal.turn = AGREED, None
        deal.expires_career_week = None
        deal.reason = None
        self._log(deal, {"kind": "status", "status": AGREED, "reason": None})
        if not self._is_open():
            self._notify(deal, team.id, f"{player.name} ile her konuda anlaşıldı; transfer dönem açılınca "
                                        f"tamamlanacak.")

    def confirm_medical(self, deal_id: int, proceed: bool) -> DealView:
        """Riskli saglik kontrolunden sonra menajer karari: devam (AGREED) ya da vazgec (WITHDRAWN)."""
        deal, team = self._my_deal(deal_id, IN)
        self._require_open(deal)
        if deal.status != MEDICAL:
            raise DeskError(MEDICAL_STAGE_TEXT)
        player = self.db.get(Player, deal.player_id)
        if self._void_if_invalid(deal, player):
            raise DeskError(deal.reason or DEAL_NOT_FOUND_TEXT)
        if proceed:
            self._agreed(deal, player, team)
        else:
            self._set_status(deal, WITHDRAWN, "Sağlık raporu sonrası menajer transferden vazgeçti.")
        self.db.flush()
        return self._view(deal)

    # ================================================================== tamamlama

    def complete(self, deal_id: int, shift_wage_room: bool = False) -> DealView:
        """
        AGREED dosyayi tamamlar (transfer donemi acik olmali; kapaliysa DeskError ve anlasma donem acilinca
        kendiliginden tamamlanir). shift_wage_room: maas alani yetmiyorsa eksik kadar butce kaydirilir (ayni islem).
        """
        deal, _team = self._my_deal(deal_id, IN)
        if deal.status == COMPLETED:
            raise DeskError("Transfer zaten tamamlandı.")
        self._require_open(deal)
        if deal.status != AGREED:
            raise DeskError(NOT_AGREED_TEXT)
        if not self._window().open:
            raise DeskError(WINDOW_CLOSED_TEXT)
        self._complete_in(deal, bool(shift_wage_room))
        self.db.flush()
        return self._view(deal)

    def _void_if_invalid(self, deal: TransferDeal, player: Player | None) -> bool:
        """Oyuncu satici kulupte degilse / kiraliga gittiyse / insan kulubu kulubunu kaybettiyse dosya VOIDED."""
        reason = None
        if player is None:
            reason = PLAYER_NOT_FOUND_TEXT
        elif player.team_id != deal.seller_team_id:
            reason = MOVED_TEXT.format(name=player.name, team=self._team_name(deal.seller_team_id) or "satıcı")
        elif player.loan_from_team_id is not None:
            reason = f"{player.name} kiralık oyuncu; dosya geçersiz."
        elif deal.human_team_id is None or deal.human_team_id not in self._humans_now():
            reason = "Kulübün menajeri değişti; dosya geçersiz."
        elif deal.exchange_player_id is not None and deal.status in OPEN:
            exchange = self.db.get(Player, deal.exchange_player_id)
            if exchange is None or exchange.team_id != deal.buyer_team_id:
                reason = "Takas oyuncusu artık kulüpte değil; dosya geçersiz."
        if reason is None:
            return False
        self._set_status(deal, VOIDED, reason)
        self._notify(deal, deal.human_team_id, reason)
        return True

    def _complete_in(self, deal: TransferDeal, shift_wage_room: bool) -> TransferNews:
        from career_manager import SENIOR_SQUAD_MAX
        from market_hub import MarketHub

        cm = self.cm
        players = {p.id: p for p in cm.lock_rows(Player, [deal.player_id, deal.exchange_player_id])}
        teams = {t.id: t for t in cm.lock_rows(Team, [deal.seller_team_id, deal.buyer_team_id])}
        player = players.get(deal.player_id)
        if self._void_if_invalid(deal, player):
            raise DeskError(deal.reason)
        seller, buyer = teams[deal.seller_team_id], teams[deal.buyer_team_id]
        reason = cm.transfer_block_reason(player)
        if reason:
            raise DeskError(f"{player.name} transfer edilemez. {reason}.")
        floor = self._floor_reason(player, seller)
        exchange = players.get(deal.exchange_player_id) if deal.exchange_player_id else None
        if floor and exchange is None:
            raise DeskError(f"{seller.name}: {floor}")
        contract = _contract_of(deal)
        if contract is None:
            raise DeskError(NOT_AGREED_TEXT)
        terms = _terms_of(deal)
        seniors = len(buyer.players) + 1 - (1 if exchange is not None else 0)
        if seniors > SENIOR_SQUAD_MAX:
            raise DeskError(SQUAD_FULL_TEXT.format(limit=SENIOR_SQUAD_MAX))
        freed = int(exchange.current_wage or 0) if exchange is not None else 0
        need = int(contract.wage) - (int(buyer.free_wage) + freed)
        if need > 0 and not shift_wage_room:
            raise DeskError(WAGE_ROOM_TEXT.format(need=_money(need), cost=_money(finance.weekly_to_transfer(need))))
        shift_cost = finance.weekly_to_transfer(need) if need > 0 else 0
        cost = terms.upfront_amount + int(contract.signing_fee) + int(contract.agent_fee)
        if cost + shift_cost > int(buyer.transfer_budget):
            raise DeskError(BUDGET_TEXT.format(have=_money(buyer.transfer_budget), need=_money(cost + shift_cost)))
        try:
            with self.db.begin_nested():
                if need > 0:
                    cm.shift_budget(buyer, need)
                if exchange is not None:
                    MarketHub(cm)._move_exchange(exchange, buyer, seller)
                    self._expire(buyer, seller)
                news = cm.complete_transfer(buyer, player, terms.upfront_amount, contract,
                                            expected_seller_id=seller.id, log_fee=terms.fee, settle_sell_on=False)
                self._record_completion(deal, player, seller, buyer, terms, contract, news)
                clauses = {"deal_id": deal.id, "signed_season": int(cm.season), "promise_week": self.cw,
                           "promised_role": contract.role.value}
                if contract.appearance_bonus:
                    clauses["appearance_bonus"] = int(contract.appearance_bonus)
                if contract.goal_bonus:
                    clauses["goal_bonus"] = int(contract.goal_bonus)
                if contract.loyalty_bonus:
                    clauses["loyalty_bonus"] = int(contract.loyalty_bonus)
                player.contract_clauses = clauses
                player.release_clause = contract.release_clause
                self._expire(buyer, seller)
                self.db.flush()
        except (TransferError, finance.BudgetError) as exc:
            if isinstance(exc, DeskError):
                raise
            raise DeskError(str(exc)) from exc
        except IntegrityError as exc:
            raise DeskError("Transfer bu sırada güncellendi; tekrar dene.") from exc
        self._notify(deal, buyer.id, f"Transfer tamamlandı: {player.name}, {seller.name} → {buyer.name} "
                                     f"({terms.describe()}).")
        return news

    def _expire(self, *teams: Team) -> None:
        self.db.flush()
        for team in teams:
            if team is not None:
                self.db.expire(team, ["players", "academy_players", "loaned_out_players"])

    def _payment(self, *, deal: TransferDeal | None, kind: str, ref: str, payer_id: int | None,
                 payee_id: int | None, amount: int, player_id: int | None, due_cw: int | None = None,
                 due_season: int | None = None, note: str = "") -> TransferPayment | None:
        """Odeme satiri (benzersiz ref). Ayni ref zaten varsa None (odeme zaten kayitli: ikinci kez YAPILMAZ)."""
        if amount <= 0:
            return None
        row = TransferPayment(deal_id=deal.id if deal is not None else None, player_id=player_id, kind=kind, ref=ref,
                              payer_team_id=payer_id, payee_team_id=payee_id, amount=int(amount), paid_amount=0,
                              due_career_week=due_cw, due_season=due_season, status=SCHEDULED,
                              created_career_week=self.cw, note=_clean(note, 160) or None)
        try:
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
        except IntegrityError:
            return None
        return row

    def _record_completion(self, deal: TransferDeal, player: Player, seller: Team, buyer: Team, terms: DealTerms,
                           contract: ContractOffer, news) -> None:
        """Tamamlanan anlasmanin defteri: pesinat (zaten odendi), prim / menajer ucreti, taksit ve sadakat plani."""
        cm = self.cm
        source = self._sell_on_source(player.id, seller.id, exclude_deal=deal.id)
        upfront = self._payment(deal=deal, kind="UPFRONT", ref=f"D{deal.id}#U", payer_id=buyer.id,
                                payee_id=seller.id, amount=terms.upfront_amount, player_id=player.id,
                                due_cw=self.cw, note=f"{player.name} peşinat")
        if upfront is not None:                       # para complete_transfer ile hareket etti: yalnizca kayit
            upfront.paid_amount, upfront.status, upfront.paid_career_week = upfront.amount, PAID, self.cw
        if source is not None:
            self._sell_on_share(source, seller, terms.upfront_amount, f"D{deal.id}#U",
                                self._share_of_cost(source, terms.upfront_amount, terms.fee))
            source.sell_on_used_career_week = self.cw
        if deal.direction == IN:
            for kind, amount, label in (("SIGNING", contract.signing_fee, "imza primi"),
                                        ("AGENT", contract.agent_fee, "menajer ücreti")):
                row = self._payment(deal=deal, kind=kind, ref=f"D{deal.id}#{kind}", payer_id=buyer.id,
                                    payee_id=None, amount=int(amount), player_id=player.id, due_cw=self.cw,
                                    note=f"{player.name} {label}")
                if row is not None:
                    self._settle(row)
            for k in range(1, int(contract.years) + 1):
                self._payment(deal=deal, kind="LOYALTY", ref=f"D{deal.id}#L{k}", payer_id=buyer.id, payee_id=None,
                              amount=int(contract.loyalty_bonus), player_id=player.id,
                              due_season=int(cm.season) + k, note=f"{player.name} sadakat primi ({k}. sezon)")
        for seq, due, amount in rules.instalment_plan(terms.deferred, terms.instalment_months, self._season_weeks(),
                                                      self.cw):
            self._payment(deal=deal, kind="INSTALMENT", ref=f"D{deal.id}#I{seq}", payer_id=buyer.id,
                          payee_id=seller.id, amount=amount, player_id=player.id, due_cw=due,
                          note=f"{player.name} {seq}. taksit")
        self.db.flush()
        log_id = self.db.scalar(select(func.max(TransferLog.id)).where(TransferLog.player_id == player.id))
        finished = bool(cm.season_finished)
        deal.status, deal.turn = COMPLETED, None
        deal.completed_career_week, deal.completed_season = self.cw, int(cm.season)
        deal.completed_week = int(cm.current_week)
        deal.transfer_log_id = int(log_id) if log_id is not None else None
        deal.expires_career_week = deal.response_due_week = None
        self._log(deal, {"kind": "done", "season": int(cm.season), "week": int(cm.current_week),
                         "title_from_season": int(cm.season) + (1 if finished else 0),
                         "upfront": terms.upfront_amount, "fee": terms.fee, "log_id": deal.transfer_log_id,
                         "sell_on_from": ({"deal": source.id, "pct": int(source.sell_on_pct),
                                           "club": source.seller_team_id} if source is not None else None)})
        self._void_other_deals(player, deal.id, f"{player.name} başka bir anlaşmayla kulüp değiştirdi.")

    def _void_other_deals(self, player: Player, keep_id: int, reason: str) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(TransferDeal).where(
            TransferDeal.player_id == player.id, TransferDeal.id != keep_id,
            TransferDeal.status.in_(sorted(OPEN))).order_by(TransferDeal.id).with_for_update(skip_locked=True)))
        for other in rows:
            self._set_status(other, VOIDED, reason)
            self._notify(other, other.human_team_id, reason)

    # ================================================================== sonraki satis payi

    def _sell_on_source(self, player_id: int, seller_id: int, exclude_deal: int | None = None) -> TransferDeal | None:
        """Oyuncuyu satan kulubun onu 'sonraki satistan pay' maddesiyle aldigi, henuz kullanilmamis anlasma."""
        self.db.flush()
        stmt = (select(TransferDeal).where(
            TransferDeal.player_id == player_id, TransferDeal.buyer_team_id == seller_id,
            TransferDeal.status == COMPLETED, TransferDeal.sell_on_pct > 0,
            TransferDeal.sell_on_used_career_week.is_(None))
            .order_by(TransferDeal.id.desc()).limit(1).with_for_update().execution_options(populate_existing=True))
        if exclude_deal is not None:
            stmt = stmt.where(TransferDeal.id != exclude_deal)
        return self.db.scalar(stmt)

    def _sell_on_share(self, source: TransferDeal, payer: Team, base_amount: int, ref: str,
                       original_fee: int = 0) -> int:
        """
        Eski kulube, satistan ALINAN tutarin %payi (benzersiz ref: bir kez). Odenen tutar.
        15F: madde KARA dayaliysa (source.sell_on_profit) yalnizca alis bedelini asan kisim paylasilir;
        `original_fee` bu odemenin payina dusen ALIS bedelidir (taksitli satista orantili). Zarardaki satista
        pay 0'dir: satir yazilmaz.
        """
        profit = bool(getattr(source, "sell_on_profit", False))
        amount = rules.sell_on_amount(int(source.sell_on_pct), int(base_amount), profit_basis=profit,
                                      original_fee=int(original_fee))
        note = f"Sonraki satış {'kârından' if profit else 'bedelinden'} pay %{int(source.sell_on_pct)}"
        row = self._payment(deal=source, kind="SELL_ON", ref=f"SO{source.id}:{ref}", payer_id=payer.id,
                            payee_id=source.seller_team_id, amount=amount, player_id=source.player_id,
                            due_cw=self.cw, note=note)
        return self._settle(row) if row is not None else 0

    @staticmethod
    def _share_of_cost(source: TransferDeal, paid: int, sale_total: int) -> int:
        """Bu odemenin payina dusen ALIS bedeli (kar tabanli pay icin); brut tabanda 0."""
        if not getattr(source, "sell_on_profit", False):
            return 0
        total = max(1, int(sale_total))
        return int(source.fee) * max(0, int(paid)) // total

    # ================================================================== para defteri

    def _settle(self, row: TransferPayment) -> int:
        """
        Odemeyi (kalanini) yapar: odeyen kasadaki kadar oder (kasa eksiye DUSMEZ); kalan OVERDUE kalir, sonraki
        haftalarda yeniden denenir. Masa satislarinda alinan taksit / ek odemeden eski kulubun payi hemen odenir.
        """
        remaining = int(row.amount) - int(row.paid_amount)
        if remaining <= 0 or row.status == CANCELLED:
            return 0
        payer = self.db.get(Team, row.payer_team_id) if row.payer_team_id is not None else None
        payee = self.db.get(Team, row.payee_team_id) if row.payee_team_id is not None else None
        if payer is None:                             # odeyen kulup yok (silindi): para yoktan yaratilmaz
            row.status = CANCELLED
            self.db.flush()
            return 0
        pay = max(0, min(remaining, int(payer.transfer_budget)))
        before = int(row.paid_amount)
        if pay > 0:
            payer.transfer_budget = int(payer.transfer_budget) - pay
            if payee is not None:
                payee.transfer_budget = int(payee.transfer_budget) + pay
            row.paid_amount = before + pay
            row.paid_career_week = self.cw
        row.status = PAID if int(row.paid_amount) >= int(row.amount) else OVERDUE
        self.db.flush()
        if pay > 0 and payee is not None and row.kind in ("INSTALMENT", "ADD_ON") and row.deal_id is not None:
            deal = self.db.get(TransferDeal, row.deal_id)
            done = _last(deal, "done") if deal is not None else None
            source_info = (done or {}).get("sell_on_from")
            if source_info:
                source = self.db.get(TransferDeal, int(source_info["deal"]))
                if source is not None:
                    # 15F: kar tabanli payda alis bedeli yalnizca GARANTILI bedelin payina dusen kisimdan
                    # dusulur; ek odemeler (ADD_ON) saf kardir, tamami paylasilir.
                    cost = self._share_of_cost(source, pay, int(deal.fee)) if row.kind == "INSTALMENT" else 0
                    self._sell_on_share(source, payee, pay, f"{row.ref}:{before}", cost)
        return pay

    def _process_payments(self) -> None:
        """Vadesi gelen taksit / sadakat primleri ve gecikmis odemeler (en eski once)."""
        cm = self.cm
        self.db.flush()
        rows = list(self.db.scalars(select(TransferPayment).where(
            TransferPayment.status.in_((SCHEDULED, OVERDUE)),
            or_(TransferPayment.due_career_week <= self.cw, TransferPayment.due_season <= int(cm.season)))
            .order_by(TransferPayment.id).with_for_update(skip_locked=True).execution_options(populate_existing=True)))
        for row in rows:
            if row.kind == "LOYALTY" and row.status == SCHEDULED:
                player = self.db.get(Player, row.player_id) if row.player_id is not None else None
                if player is None or player.team_id != row.payer_team_id:
                    row.status = CANCELLED
                    continue
            before = int(row.paid_amount)
            paid = self._settle(row)
            human = row.payer_team_id if row.payer_team_id in self._humans_now() else (
                row.payee_team_id if row.payee_team_id in self._humans_now() else None)
            if human is None:
                continue
            label = PAYMENT_LABELS.get(row.kind, row.kind)
            if paid > 0:
                verb = "ödendi" if human == row.payer_team_id else "kasaya girdi"
                self._note(human, f"{label} {verb}: {_money(paid)} ({row.note or ''})")
            if row.status == OVERDUE and human == row.payer_team_id and (paid > 0 or before == 0):
                self._note(human, f"{label} ödenemedi, gecikmede: {_money(int(row.amount) - int(row.paid_amount))}")

    def _note(self, team_id: int | None, text: str) -> None:
        if self._report is not None and team_id in self._humans_now():
            self.cm._sink(self._report, team_id).transfer_notes.append(_clean(text))

    # ================================================================== ek odemeler, primler, soz

    def _counted(self, deal: TransferDeal, kind: str) -> int:
        done = _last(deal, "done") or {}
        s0, w0 = int(done.get("season", deal.completed_season or 1)), int(done.get("week", deal.completed_week or 1))
        self.db.flush()
        column = func.count() if kind == rules.ADD_ON_APPEARANCES else func.coalesce(func.sum(PlayerMatchStat.goals), 0)
        stmt = select(column).select_from(PlayerMatchStat).join(Fixture, Fixture.id == PlayerMatchStat.fixture_id) \
            .where(PlayerMatchStat.player_id == deal.player_id, PlayerMatchStat.team_id == deal.buyer_team_id,
                   or_(Fixture.season > s0, (Fixture.season == s0) & (Fixture.week >= w0)))
        if kind == rules.ADD_ON_APPEARANCES:
            stmt = stmt.where(PlayerMatchStat.minutes > 0)
        return int(self.db.scalar(stmt) or 0)

    def _titles(self, deal: TransferDeal, kind: str) -> int:
        done = _last(deal, "done") or {}
        since = int(done.get("title_from_season", deal.completed_season or 1))
        honour = HonourKind.LEAGUE.value if kind == rules.ADD_ON_LEAGUE_TITLE else HonourKind.CUP.value
        self.db.flush()
        return int(self.db.scalar(select(func.count()).select_from(SeasonHonour).where(
            SeasonHonour.kind == honour, SeasonHonour.champion_team_id == deal.buyer_team_id,
            SeasonHonour.season >= since)) or 0)

    def _addon_progress(self, deal: TransferDeal, addon: AddOn) -> int:
        if addon.kind in rules.COUNTED_ADD_ONS:
            return self._counted(deal, addon.kind)
        return self._titles(deal, addon.kind)

    def _check_add_ons(self) -> None:
        """Tamamlanan anlasmalarin ek odemeleri: tetiklenen bir kez odenir (ref D{id}#A{anahtar})."""
        self.db.flush()
        deals = list(self.db.scalars(select(TransferDeal).where(
            TransferDeal.status == COMPLETED, func.jsonb_array_length(TransferDeal.add_ons) > 0)
            .order_by(TransferDeal.id)))
        for deal in deals:
            player = self.db.get(Player, deal.player_id)
            for raw in deal.add_ons or []:
                addon = AddOn.from_dict(raw)
                ref = f"D{deal.id}#A{addon.key}"
                if self.db.scalar(select(TransferPayment.id).where(TransferPayment.ref == ref)) is not None:
                    continue
                if player is None or player.team_id != deal.buyer_team_id:
                    lapsed = {e.get("key") for e in _entries(deal, "addon_lapsed")}
                    if addon.key not in lapsed:
                        self._log(deal, {"kind": "addon_lapsed", "key": addon.key})
                    continue
                if self._addon_progress(deal, addon) < int(addon.threshold):
                    continue
                row = self._payment(deal=deal, kind="ADD_ON", ref=ref, payer_id=deal.buyer_team_id,
                                    payee_id=deal.seller_team_id, amount=int(addon.amount), player_id=deal.player_id,
                                    due_cw=self.cw, note=f"{player.name}: {addon.describe()}")
                if row is None:
                    continue
                paid = self._settle(row)
                text = f"Ek ödeme tetiklendi ({player.name}): {addon.describe()}; ödenen {_money(paid)}."
                self._log(deal, {"kind": "addon_paid", "key": addon.key, "paid": paid})
                for team_id in (deal.buyer_team_id, deal.seller_team_id):
                    if team_id in self._humans_now():
                        self._notify(deal, team_id, text)

    def _pay_match_bonuses(self, week: int) -> None:
        """Masada imzalanan sozlesmelerdeki mac / gol primleri: bu haftanin resmi maclari (ref B{oyuncu}:{sezon}:{hafta})."""
        season = int(self.cm.season)
        self.db.flush()
        players = list(self.db.scalars(select(Player).where(
            or_(Player.contract_clauses.has_key("appearance_bonus"),
                Player.contract_clauses.has_key("goal_bonus")),
            Player.team_id.isnot(None)).order_by(Player.id)))
        for p in players:
            clauses = p.contract_clauses or {}
            apps, goals = self.db.execute(select(
                func.count().filter(PlayerMatchStat.minutes > 0), func.coalesce(func.sum(PlayerMatchStat.goals), 0))
                .select_from(PlayerMatchStat).join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
                .where(PlayerMatchStat.player_id == p.id, PlayerMatchStat.team_id == p.team_id,
                       Fixture.season == season, Fixture.week == int(week))).one()
            amount = int(apps or 0) * int(clauses.get("appearance_bonus") or 0) + \
                int(goals or 0) * int(clauses.get("goal_bonus") or 0)
            row = self._payment(deal=None, kind="BONUS", ref=f"B{p.id}:{season}:{int(week)}", payer_id=p.team_id,
                                payee_id=None, amount=amount, player_id=p.id, due_cw=self.cw,
                                note=f"{p.name}: {int(apps or 0)} maç, {int(goals or 0)} gol primi")
            if row is not None:
                paid = self._settle(row)
                self._note(p.team_id, f"Prim ödendi ({p.name}): {_money(paid)}")

    def _check_promises(self) -> None:
        """Rol sozu verilen oyuncu 'Ayrilmak istiyor' seviyesine gelirse soz tutulmamis sayilir (bir kez)."""
        humans = sorted(self._humans_now())
        if not humans:
            return
        self.db.flush()
        rows = list(self.db.scalars(select(Player).where(
            Player.team_id.in_(humans), Player.contract_clauses.has_key("promised_role"),
            Player.concern_level >= 3).order_by(Player.id)))
        for p in rows:
            clauses = dict(p.contract_clauses or {})
            if clauses.get("promise_broken") or clauses.get("promised_role") == SquadRole.BACKUP.value:
                continue
            clauses["promise_broken"] = self.cw
            clauses["wants_away"] = True
            p.contract_clauses = clauses
            p.morale = max(0, int(p.morale) + PROMISE_BROKEN_MORALE)
            role = transfers.ROLE_LABELS.get(SquadRole(clauses["promised_role"]), clauses["promised_role"])
            self._notify(None, p.team_id, f"{p.name}: \"Bana {role} rolü sözü verilmişti, tutulmadı.\" Oyuncu "
                                          f"ayrılmak istiyor; gelen tekliflere açık.")

    # ================================================================== haftalik: dosyalar

    def _sweep(self) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(TransferDeal).where(TransferDeal.status.in_(sorted(OPEN)))
                                    .order_by(TransferDeal.id).with_for_update(skip_locked=True)
                                    .execution_options(populate_existing=True)))
        for deal in rows:
            self._void_if_invalid(deal, self.db.get(Player, deal.player_id))

    def _expire_due(self, next_week: int) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(TransferDeal).where(
            TransferDeal.status.in_(sorted(OPEN - {AGREED})), TransferDeal.expires_career_week.isnot(None),
            TransferDeal.expires_career_week <= next_week).order_by(TransferDeal.id)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True)))
        for deal in rows:
            player = self.db.get(Player, deal.player_id)
            name = player.name if player is not None else "Oyuncu"
            if deal.direction == OUT and deal.status == BIDDING:
                text = f"{self._team_name(deal.buyer_team_id)} teklifinin süresi doldu ({name})."
            else:
                text = f"Dosyanın süresi doldu ({name}): {STATUS_LABELS.get(deal.status, deal.status)} aşamasında " \
                       f"yanıt verilmedi."
            self._set_status(deal, EXPIRED, text)
            self._notify(deal, deal.human_team_id, text)

    def _queued_responses(self, next_week: int) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(TransferDeal).where(
            TransferDeal.status == BIDDING, TransferDeal.turn == CLUB, TransferDeal.response_due_week.isnot(None),
            TransferDeal.response_due_week <= next_week).order_by(TransferDeal.id)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True)))
        for deal in rows:
            self._club_answer(deal)

    def _scouting_progress(self) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(ScoutAssignment).where(ScoutAssignment.status == "ASSIGNED")
                                    .order_by(ScoutAssignment.id)))
        gains: dict[int, int] = {}
        for row in rows:
            team = self.db.get(Team, row.team_id)
            if team is None:
                continue
            if team.id not in gains:
                gains[team.id] = self._gain(team)
            row.knowledge = min(rules.MAX_KNOWLEDGE, int(row.knowledge) + gains[team.id])
            row.updated_career_week = self.cw
            if row.knowledge >= rules.MAX_KNOWLEDGE:
                row.status = "DONE"
                player = self.db.get(Player, row.player_id)
                if player is not None:
                    self._notify(None, team.id, f"Gözlem raporu hazır: {player.name} hakkında ayrıntılı bilgi var.")

    def _complete_waiting(self) -> None:
        """Donem acilinca (ya da aciksa) bekleyen AGREED anlasmalar tamamlanir; basarisizlik notla bildirilir."""
        self.db.flush()
        ids = list(self.db.scalars(select(TransferDeal.id).where(TransferDeal.status == AGREED)
                                   .order_by(TransferDeal.id)))
        for deal_id in ids:
            deal = self._lock_deal(deal_id)
            if deal.status != AGREED or self._void_if_invalid(deal, self.db.get(Player, deal.player_id)):
                continue
            try:
                with self.db.begin_nested():
                    if deal.direction == IN:
                        self._complete_in(deal, True)
                    else:
                        self._complete_out(deal)
            except DeskError as exc:
                failures = len(_entries(deal, "completion_failed")) + 1
                self._log(deal, {"kind": "completion_failed", "reason": _clean(str(exc))})
                if failures >= AGREED_MAX_FAILURES:
                    self._set_status(deal, COLLAPSED, f"Transfer tamamlanamadı: {exc}")
                    self._notify(deal, deal.human_team_id, f"Transfer çöktü: {exc}")
                else:
                    self._notify(deal, deal.human_team_id, f"Transfer tamamlanamadı: {exc}")

    # ================================================================== OUT: gelen teklifler

    def _ai_buyer_for(self, player: Player, seller: Team, rng: random.Random, exclude: set[int],
                      min_budget: int) -> Team | None:
        humans, protected = self._humans_now(), self.cm._protected_team_ids()
        self.db.flush()
        debtors = set(self.db.scalars(select(TransferPayment.payer_team_id).where(
            TransferPayment.status == OVERDUE, TransferPayment.payer_team_id.isnot(None)).distinct()))
        scored: list[tuple[float, int, Team]] = []
        averages_cache: dict[int, dict] = {}
        for team in self.cm.teams():
            if team.id in humans or team.id in protected or team.id == seller.id or team.id in exclude                     or team.id in debtors:
                continue
            if int(team.transfer_budget) < min_budget:
                continue
            if not transfers.check_interest(player.overall_rating, team.reputation,
                                            reputation.ai_manager_reputation(team.reputation)).interested:
                continue
            if team.league_id not in averages_cache:
                averages_cache[team.league_id] = transfers.league_position_average(team.league.teams)
            needs = {n.position: n for n in transfers.squad_needs(team, averages_cache[team.league_id])}
            need = needs.get(player.position)
            if need is None:
                continue
            score = transfers.target_score(player, team, need)
            if score <= 0:
                continue
            scored.append((score, team.id, team))
        if not scored:
            return None
        scored.sort(key=lambda s: (-s[0], s[1]))
        return scored[rng.randrange(min(3, len(scored)))][2]

    def _eligible_out(self, p: Player) -> bool:
        return (not p.in_academy and p.loan_from_team_id is None and self.cm.transfer_block_reason(p) is None)

    def _ai_incoming_bids(self, next_open: bool) -> None:
        """Donem aciksa AI kulupleri insan kuluplerinin oyuncularina teklif yapar (tohum: hafta + kulup)."""
        if not next_open:
            return
        for team_id in sorted(self._humans_now()):
            team = self.db.get(Team, team_id)
            if team is None:
                continue
            self.db.flush()
            open_rows = list(self.db.execute(select(TransferDeal.player_id, TransferDeal.buyer_team_id).where(
                TransferDeal.human_team_id == team.id, TransferDeal.direction == OUT,
                TransferDeal.status.in_(sorted(OPEN)))))
            if len(open_rows) >= rules.AI_OPEN_BIDS_PER_CLUB:
                continue
            rng = _rng("ai-bid", self.cw, team.id)
            candidates = [p for p in team.players if self._eligible_out(p)]
            if len(candidates) <= transfers.SQUAD_FLOOR:
                continue
            listed = [p for p in candidates if p.transfer_listed or (p.contract_clauses or {}).get("wants_away")]
            chance = AI_BID_LISTED_CHANCE if listed else AI_BID_BASE_CHANCE
            if rng.random() >= chance:
                continue
            pool = listed or sorted(candidates, key=lambda p: (-p.overall_rating, p.id))[:AI_BID_POOL]
            target = buyer = None
            for candidate in rng.sample(pool, min(AI_BID_TRIES, len(pool))):
                if self._floor_reason(candidate, team):
                    continue
                busy = {buyer_id for pid, buyer_id in open_rows if pid == candidate.id}
                min_budget = int(int(candidate.market_value or 0) * AI_BUYER_MIN_BUDGET_SHARE)
                buyer = self._ai_buyer_for(candidate, team, rng, busy, min_budget)
                if buyer is not None:
                    target = candidate
                    break
            if target is None or buyer is None:
                continue
            value = int(target.market_value or 0)
            bid = rules.ai_bid_terms(rng, value=value, asking=target.asking_price, budget=int(buyer.transfer_budget),
                                     listed=bool(target.transfer_listed))
            if bid is None:
                continue
            terms, cap = bid
            deal = self._new_deal(direction=OUT, player=target, seller=team, buyer=buyer, human=team,
                                  status=BIDDING, patience=OUT_PATIENCE)
            if deal.status != BIDDING or deal.round:
                continue
            _set_terms(deal, terms)
            deal.turn, deal.last_action, deal.round = MANAGER, "BID", 1
            deal.expires_career_week = self.cw + 1 + COUNTER_VALID_WEEKS
            ctx = self._ctx(target, team, buyer)
            self._log(deal, {"kind": "bid", "side": CLUB, "terms": terms.to_dict()})
            self._log(deal, {"kind": "ai_limit", "max": int(cap), "last": rules.package_value(terms, ctx)})
            self._notify(deal, team.id, f"{buyer.name}, {target.name} için teklif yaptı: {terms.describe()}.",
                         NotificationKind.OFFER_IN)

    def _release_clause_triggers(self, next_open: bool) -> None:
        """Serbest kalma bedeli olan insan oyuncusu: uygun AI kulubu bedeli oderse satis reddedilemez."""
        if not next_open:
            return
        humans = sorted(self._humans_now())
        if not humans:
            return
        self.db.flush()
        rows = list(self.db.scalars(select(Player).where(
            Player.release_clause.isnot(None), Player.team_id.in_(humans), Player.in_academy.is_(False),
            Player.loan_from_team_id.is_(None)).order_by(Player.id)))
        for p in rows:
            if self.cm.transfer_block_reason(p):
                continue
            seller = self.db.get(Team, p.team_id)
            if self._floor_reason(p, seller):
                continue
            clause = int(p.release_clause)
            rng = _rng("release", self.cw, p.id)
            value = int(p.market_value or 0)
            buyer = self._ai_buyer_for(p, seller, rng, set(), clause)
            if buyer is None or not rules.release_clause_attractive(clause, value, int(buyer.transfer_budget)):
                continue
            if rng.random() >= rules.RELEASE_TRIGGER_CHANCE:
                continue
            self._trigger_release(p, seller, buyer, clause)

    def _trigger_release(self, player: Player, seller: Team, buyer: Team, clause: int) -> TransferDeal | None:
        existing = self._open_deal(player.id, buyer.id)
        if existing is not None:
            self._set_status(existing, VOIDED, "Serbest kalma bedeli ödendi.")
            self.db.flush()
        deal = self._new_deal(direction=OUT, player=player, seller=seller, buyer=buyer, human=seller, status=TERMS,
                              patience=0)
        _set_terms(deal, DealTerms(fee=clause, upfront=clause))
        deal.turn, deal.last_action, deal.round = None, "RELEASE", 1
        self._log(deal, {"kind": "bid", "side": CLUB, "terms": _terms_of(deal).to_dict(), "release": True})
        self._rumour(f"Transfer: {buyer.name}, {player.name} için {_money(clause)} serbest kalma bedelini ödedi.",
                     buyer.id, seller.id)
        self._notify(deal, seller.id, f"{buyer.name}, {player.name} için serbest kalma bedelini ({_money(clause)}) "
                                      f"ödedi; kulüp bu satışı reddedemez.", NotificationKind.OFFER_IN)
        self._fee_agreed_out(deal, player, seller, buyer)
        return deal

    def trigger_release_clause(self, player_id: int) -> DealView:
        """AI kulubundeki oyuncunun serbest kalma bedelini ode (kulup reddedemez; oyuncu yine de ikna edilmeli)."""
        team = self._team()
        player = self._player(player_id)
        seller = self._check_target(team, player)
        if player.release_clause is None:
            raise DeskError(RELEASE_NONE_TEXT.format(name=player.name))
        clause = int(player.release_clause)
        overdue = self._overdue_amount(team.id)
        if overdue > 0:
            raise DeskError(DEBT_TEXT.format(amount=_money(overdue)))
        if clause > int(team.transfer_budget):
            raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(clause)))
        deal = self._in_deal(team, player, seller)
        if deal.status not in (ENQUIRY, BIDDING) or deal.turn == CLUB:
            raise DeskError(FEE_AGREED_TEXT if deal.status not in (ENQUIRY, BIDDING) else CLUB_THINKING_TEXT)
        _set_terms(deal, DealTerms(fee=clause, upfront=clause))
        deal.round = int(deal.round) + 1
        self._log(deal, {"kind": "bid", "side": MANAGER, "terms": _terms_of(deal).to_dict(), "release": True})
        deal.status, deal.turn, deal.last_action = TERMS, MANAGER, "RELEASE"
        deal.expires_career_week = self.cw + TERMS_VALID_WEEKS
        self._rumour(f"Transfer: {team.name}, {player.name} için serbest kalma bedelini ödemeye hazır.",
                     team.id, seller.id)
        self.db.flush()
        return self._view(deal)

    def _my_out(self, deal_id) -> tuple[TransferDeal, Team, Player]:
        deal, team = self._my_deal(deal_id, OUT)
        self._require_open(deal)
        player = self.db.get(Player, deal.player_id)
        if self._void_if_invalid(deal, player):
            raise DeskError(deal.reason or DEAL_NOT_FOUND_TEXT)
        return deal, team, player

    def _seller_floor(self, team: Team, player: Player) -> None:
        others = [p for p in team.players if p.id != player.id]
        if len(others) < transfers.SQUAD_FLOOR:
            raise DeskError(SELLER_FLOOR_TEXT.format(floor=transfers.SQUAD_FLOOR))
        floor = transfers.POSITION_SALE_FLOOR.get(player.position)
        if floor is not None and sum(1 for p in others if p.position is player.position) < floor:
            raise DeskError(KEEPER_FLOOR_TEXT.format(floor=floor))

    def accept_offer(self, deal_id: int) -> DealView:
        """Gelen AI teklifini kabul: AI oyuncuyla sozlesme yapar; donem aciksa transfer hemen tamamlanir."""
        deal, team, player = self._my_out(deal_id)
        if deal.status != BIDDING or deal.turn != MANAGER:
            raise DeskError(NOT_YOUR_TURN_TEXT)
        self._seller_floor(team, player)
        buyer = self.db.get(Team, deal.buyer_team_id)
        self._log(deal, {"kind": "response", "side": MANAGER, "action": "ACCEPT", "message": "Teklif kabul",
                         "demands": [], "terms": _terms_of(deal).to_dict()})
        deal.last_action = "ACCEPT"
        self._fee_agreed_out(deal, player, team, buyer)
        self.db.flush()
        return self._view(deal)

    def reject_offer(self, deal_id: int, reason: str = "") -> DealView:
        deal, _team, player = self._my_out(deal_id)
        if deal.status != BIDDING or deal.turn != MANAGER:
            raise DeskError(NOT_YOUR_TURN_TEXT)
        self._set_status(deal, REJECTED, _clean(reason) or f"{player.name} için teklif reddedildi.")
        self.db.flush()
        return self._view(deal)

    def counter_offer(self, deal_id: int, terms: DealTerms) -> DealView:
        """Gelen AI teklifine karsi teklif (takas yok). AI alici aninda (haftalik sinir icinde) ya da gelecek hafta."""
        deal, team, player = self._my_out(deal_id)
        if deal.status != BIDDING or deal.turn != MANAGER:
            raise DeskError(NOT_YOUR_TURN_TEXT)
        terms = self._clean_terms(terms, allow_exchange=False)
        self._seller_floor(team, player)
        _set_terms(deal, terms)
        deal.turn, deal.last_action = CLUB, "COUNTER"
        deal.round = int(deal.round) + 1
        self._log(deal, {"kind": "bid", "side": MANAGER, "terms": terms.to_dict()})
        if self._responses_this_week(deal) < RESPONSES_PER_WEEK:
            self._buyer_answer(deal)
        else:
            deal.response_due_week = self.cw + 1
            deal.expires_career_week = None
            self._log(deal, {"kind": "queued", "message": "Kulüp karşı teklifi değerlendiriyor; yanıt gelecek hafta."})
        self.db.flush()
        return self._view(deal)

    def _buyer_answer(self, deal: TransferDeal) -> None:
        player = self.db.get(Player, deal.player_id)
        seller = self.db.get(Team, deal.seller_team_id)
        buyer = self.db.get(Team, deal.buyer_team_id)
        if self._void_if_invalid(deal, player):
            return
        limit = _last(deal, "ai_limit") or {}
        ctx = self._ctx(player, seller, buyer)
        max_value = min(int(limit.get("max") or 0), int(buyer.transfer_budget) * 2)
        last_value = int(limit.get("last") or 0)
        response = rules.buyer_response(_rng("buyer", deal.id, deal.round), _terms_of(deal), ctx, last_value,
                                        max_value, int(deal.patience), int(deal.round))
        if response.action == rules.ACTION_COUNTER and response.counter is not None:
            self._log(deal, {"kind": "ai_limit", "max": int(limit.get("max") or 0),
                             "last": rules.package_value(response.counter, ctx)})
        if _terms_of(deal).upfront_amount > int(buyer.transfer_budget) and response.action == rules.ACTION_ACCEPT:
            response = rules.ClubResponse(rules.ACTION_END, "Kulübün kasası peşinatı karşılamıyor; teklif geri "
                                                            "çekildi.", patience_cost=1)
        self._apply_response(deal, response, player, seller, buyer)

    def _fee_agreed_out(self, deal: TransferDeal, player: Player, seller: Team, buyer: Team) -> None:
        """OUT bonservis anlasmasi: AI kulubu oyuncuyla sozlesme yapar; donem aciksa hemen tamamlanir."""
        ok, message, contract = self._ai_sign(deal, player, buyer)
        if not ok:
            self._set_status(deal, COLLAPSED, message)
            self._notify(deal, seller.id, f"Transfer çöktü: {message}")
            return
        deal.contract = contract.to_dict()
        deal.status, deal.turn, deal.expires_career_week = AGREED, None, None
        self._log(deal, {"kind": "ai_terms", "contract": contract.to_dict()})
        if self._is_open():
            try:
                with self.db.begin_nested():
                    self._complete_out(deal)
            except DeskError as exc:
                self._log(deal, {"kind": "completion_failed", "reason": _clean(str(exc))})
                self._notify(deal, seller.id, f"Transfer tamamlanamadı: {exc}")
        else:
            self._notify(deal, seller.id, f"{player.name} için {buyer.name} ile anlaşıldı; transfer dönem açılınca "
                                          f"tamamlanacak.")

    def _ai_sign(self, deal: TransferDeal, player: Player, buyer: Team) -> tuple[bool, str, ContractOffer | None]:
        rng = _rng("ai-terms", deal.id, player.id, buyer.id)
        negotiation = ContractNegotiation(rng, player, buyer, int(deal.fee),
                                          manager_reputation=self.cm.manager_reputation_for(buyer))
        if not negotiation.open:
            return False, f"{player.name}, {buyer.name} kulübüne gitmek istemedi: {negotiation.opening_message}", None
        offer = transfers.ai_contract_offer(rng, negotiation, max(int(buyer.free_wage), negotiation.demand.wage))
        response = negotiation.respond(offer)
        if response.status is not NegotiationStatus.ACCEPTED:
            return False, f"{player.name}, {buyer.name} ile sözleşmede anlaşamadı.", None
        return True, response.message, offer

    def _complete_out(self, deal: TransferDeal) -> TransferNews:
        cm = self.cm
        players = {p.id: p for p in cm.lock_rows(Player, [deal.player_id])}
        teams = {t.id: t for t in cm.lock_rows(Team, [deal.seller_team_id, deal.buyer_team_id])}
        player = players.get(deal.player_id)
        if self._void_if_invalid(deal, player):
            raise DeskError(deal.reason)
        seller, buyer = teams[deal.seller_team_id], teams[deal.buyer_team_id]
        reason = cm.transfer_block_reason(player)
        if reason:
            raise DeskError(f"{player.name} transfer edilemez. {reason}.")
        self._seller_floor(seller, player)
        contract = _contract_of(deal)
        if contract is None:
            raise DeskError(NOT_AGREED_TEXT)
        terms = _terms_of(deal)
        if contract.wage > int(buyer.free_wage):
            shift = finance.auto_shift_for_wage(int(buyer.transfer_budget), int(buyer.wage_budget),
                                                int(buyer.free_wage), int(contract.wage), terms.upfront_amount)
            if shift > 0:
                try:
                    cm.shift_budget(buyer, shift)
                except finance.BudgetError:
                    pass
        if contract.wage > int(buyer.free_wage):
            raise DeskError(f"{buyer.name} maaş bütçesini ayarlayamadı.")
        if terms.upfront_amount > int(buyer.transfer_budget):
            raise DeskError(f"{buyer.name} peşinatı ödeyemedi.")
        try:
            with self.db.begin_nested():
                news = cm.complete_transfer(buyer, player, terms.upfront_amount, contract,
                                            expected_seller_id=seller.id, log_fee=terms.fee, settle_sell_on=False)
                self._record_completion(deal, player, seller, buyer, terms, contract, news)
                player.transfer_listed = player.loan_listed = False
                self._expire(buyer, seller)
                self.db.flush()
        except (TransferError, finance.BudgetError) as exc:
            if isinstance(exc, DeskError):
                raise
            raise DeskError(str(exc)) from exc
        except IntegrityError as exc:
            raise DeskError("Transfer bu sırada güncellendi; tekrar dene.") from exc
        self._notify(deal, seller.id, f"Transfer tamamlandı: {player.name}, {seller.name} → {buyer.name} "
                                      f"({terms.describe()}).")
        return news

    # ================================================================== listeler ve istenen bedel

    def _own_player(self, player_id) -> tuple[Team, Player]:
        team = self._team()
        rows = self.cm.lock_rows(Player, [player_id]) if _is_id(player_id) else []
        player = rows[0] if rows else None
        if player is None or player.team_id != team.id:
            raise DeskError(NOT_YOUR_PLAYER_TEXT.format(name=player.name if player is not None else "Oyuncu"))
        return team, player

    def set_listing(self, player_id: int, transfer: bool | None = None, loan: bool | None = None) -> None:
        """Kendi A takim oyuncumu transfer / kiralik listesine koyar ya da cikarir (None: degismez). Her dunyada."""
        _team, player = self._own_player(player_id)
        if (transfer or loan) and (player.in_academy or player.loan_from_team_id is not None):
            raise DeskError("Akademi ve kiralık oyuncuları listeye konamaz.")
        if transfer is not None:
            player.transfer_listed = bool(transfer)
        if loan is not None:
            player.loan_listed = bool(loan)
        self.db.flush()

    def set_asking_price(self, player_id: int, amount: int | None) -> None:
        """AI kuluplerine ilan edilen istenen bedel (None: kaldir). AI teklifleri bu tabana gore gelir."""
        _team, player = self._own_player(player_id)
        if amount is not None:
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0 or amount > rules.MAX_FEE:
                raise DeskError("İstenen bedel sıfır ya da pozitif bir tam sayı olmalı.")
        player.asking_price = amount
        self.db.flush()

    # ================================================================== gorunumler

    def deal(self, deal_id: int) -> DealView:
        team = self._team()
        deal = self.db.get(TransferDeal, deal_id) if _is_id(deal_id) else None
        if deal is None or deal.human_team_id != team.id:
            raise DeskError(DEAL_NOT_FOUND_TEXT)
        return self._view(deal)

    def deals(self, direction: str | None = None, open_only: bool = False, limit: int = 50) -> list[DealView]:
        """Kulubumun dosyalari: acik olanlar once, sonra en yeni."""
        team = self.cm.user_team
        if team is None:
            return []
        self.db.flush()
        stmt = select(TransferDeal).where(TransferDeal.human_team_id == team.id,
                                          TransferDeal.kind == TRANSFER_KIND)     # 15F: kiralik dosyalari LoanDesk'te
        if direction in (IN, OUT):
            stmt = stmt.where(TransferDeal.direction == direction)
        if open_only:
            stmt = stmt.where(TransferDeal.status.in_(sorted(OPEN)))
        rows = self.db.scalars(stmt.order_by(TransferDeal.status.in_(sorted(OPEN)).desc(), TransferDeal.id.desc())
                               .limit(max(1, min(200, int(limit)))))
        return [self._view(d) for d in rows]

    def incoming(self, open_only: bool = True) -> list[DealView]:
        """Oyuncularima gelen AI teklifleri."""
        return self.deals(OUT, open_only)

    def outgoing(self, open_only: bool = True) -> list[DealView]:
        """AI kuluplerine yaptigim teklifler."""
        return self.deals(IN, open_only)

    def _history_lines(self, deal: TransferDeal) -> tuple[str, ...]:
        lines: list[str] = []
        for e in deal.history or []:
            if not isinstance(e, dict):
                continue
            prefix = f"{e.get('cw', '?')}. hafta · "
            kind = e.get("kind")
            if kind == "enquiry":
                lines.append(prefix + (e.get("message") or "Bilgi alındı"))
            elif kind == "bid":
                who = "Teklifin" if e.get("side") == MANAGER else "Kulübün teklifi"
                if e.get("release"):
                    who = "Serbest kalma bedeli"
                lines.append(prefix + f"{who}: {DealTerms.from_dict(e.get('terms') or {}).describe()}")
            elif kind == "response":
                text = e.get("message") or e.get("action")
                if e.get("demands"):
                    text += " — " + " ".join(e["demands"])
                lines.append(prefix + str(text))
            elif kind == "queued":
                lines.append(prefix + str(e.get("message")))
            elif kind == "terms_open":
                lines.append(prefix + (e.get("refusal") or "Sözleşme masası açıldı"))
            elif kind == "terms_bid":
                lines.append(prefix + f"Sözleşme teklifi: {ContractOffer.from_dict(e['offer']).describe()}")
            elif kind == "medical":
                lines.append(prefix + rules.MEDICAL_LABELS.get(e.get("result"), "Sağlık kontrolü")
                             + (": " + " ".join(e.get("notes") or ()) if e.get("notes") else ""))
            elif kind == "ai_terms":
                lines.append(prefix + "Alıcı kulüp oyuncuyla sözleşmede anlaştı")
            elif kind == "status":
                label = STATUS_LABELS.get(e.get("status"), e.get("status"))
                lines.append(prefix + label + (f": {e['reason']}" if e.get("reason") else ""))
            elif kind == "done":
                lines.append(prefix + "Transfer tamamlandı")
            elif kind == "addon_paid":
                lines.append(prefix + f"Ek ödeme ödendi ({e.get('key')})")
            elif kind == "addon_lapsed":
                lines.append(prefix + f"Ek ödeme düştü: oyuncu kulüpten ayrıldı ({e.get('key')})")
            elif kind == "completion_failed":
                lines.append(prefix + f"Tamamlanamadı: {e.get('reason')}")
        return tuple(lines)

    def _terms_view(self, deal: TransferDeal) -> TermsView | None:
        if deal.round == 0 and deal.status == ENQUIRY:
            return None
        terms = _terms_of(deal)
        plan = rules.instalment_plan(terms.deferred, terms.instalment_months, self._season_weeks(), self.cw)
        exchange = self.db.get(Player, terms.exchange_player_id) if terms.exchange_player_id else None
        return TermsView(terms.fee, terms.upfront_amount, terms.deferred, terms.instalment_months, len(plan),
                         plan[0][2] if plan else 0, tuple(a.describe() for a in terms.add_ons), terms.add_ons_total,
                         terms.sell_on_pct, terms.exchange_player_id, exchange.name if exchange else None,
                         terms.describe(), tuple(terms.add_ons))

    def _view(self, deal: TransferDeal) -> DealView:
        team = self.cm.user_team
        player = self.db.get(Player, deal.player_id)
        is_open = deal.status in OPEN
        mine = team is not None and team.id == deal.human_team_id
        window = self.window()
        last_response = _last(deal, "response")
        enquiry = _last(deal, "enquiry")
        message = (deal.reason or (last_response or {}).get("message") or (enquiry or {}).get("message") or "")
        demands = tuple((last_response or {}).get("demands") or ()) if deal.last_action in ("COUNTER", "REJECT") \
            else ()
        if deal.direction == IN and player is not None and team is not None and player.team_id != team.id:
            k = self.knowledge_of(team, player)
            margin = rules.knowledge_margin(self.cm.scout_margin(team), k)
            band = staff_rules.scouted_money(int(player.market_value), margin,
                                             (self.cm.scout_rating(team) or 0, player.id, "value")) \
                if margin is not None else None
            value_text = "Bilinmiyor" if band is None else (
                _money(band.low) if band.exact else f"{_money(band.low)}-{_money(band.high)}")
        else:
            value_text = _money(int(player.market_value)) if player is not None else "—"
        stance_label = rules.STANCE_LABELS.get((enquiry or {}).get("stance")) if deal.direction == IN else None
        medical = deal.medical or {}
        completes = None
        if deal.status == AGREED:
            completes = "Transfer dönemi açık: tamamlayabilirsin." if window.open else window.label
        progress: tuple[str, ...] = ()
        if deal.status == COMPLETED and deal.add_ons:
            items = []
            for raw in deal.add_ons:
                addon = AddOn.from_dict(raw)
                paid = self.db.scalar(select(TransferPayment.status).where(
                    TransferPayment.ref == f"D{deal.id}#A{addon.key}"))
                if paid is not None:
                    items.append(f"{addon.describe()}: ödendi")
                elif addon.kind in rules.COUNTED_ADD_ONS:
                    items.append(f"{addon.describe()}: {self._addon_progress(deal, addon)}/{addon.threshold}")
                else:
                    items.append(f"{addon.describe()}: bekleniyor")
            progress = tuple(items)
        manager_turn = is_open and mine and deal.turn == MANAGER
        return DealView(
            id=deal.id, direction=deal.direction, status=deal.status,
            status_label=STATUS_LABELS.get(deal.status, deal.status), turn=deal.turn if is_open else None,
            player_id=deal.player_id, player_name=player.name if player is not None else "Oyuncu",
            position=_ev(player.position) if player is not None else "", age=int(player.age) if player else 0,
            seller_team_id=deal.seller_team_id, seller_team=self._team_name(deal.seller_team_id),
            buyer_team_id=deal.buyer_team_id, buyer_team=self._team_name(deal.buyer_team_id),
            terms=self._terms_view(deal), stance_label=stance_label, club_message=message, demands=demands,
            mood=rules.patience_label(int(deal.patience)) if is_open and deal.status == BIDDING else None,
            interest_label=(enquiry or {}).get("interest") if deal.direction == IN else None,
            value_text=value_text,
            expires_in_weeks=(max(0, int(deal.expires_career_week) - self.cw)
                              if is_open and deal.expires_career_week is not None else None),
            window=window, completes_text=completes, contract=_contract_of(deal),
            medical_label=medical.get("label"), medical_notes=tuple(medical.get("notes") or ()),
            add_on_progress=progress, history=self._history_lines(deal),
            can_bid=bool(mine and deal.direction == IN and deal.status in (ENQUIRY, BIDDING) and deal.turn != CLUB),
            can_accept_counter=bool(manager_turn and deal.direction == IN and deal.status == BIDDING
                                    and deal.last_action == "COUNTER"),
            can_withdraw=bool(mine and is_open and deal.direction == IN),
            can_open_terms=bool(mine and deal.direction == IN and deal.status == TERMS),
            can_confirm_medical=bool(mine and deal.direction == IN and deal.status == MEDICAL),
            can_complete=bool(mine and deal.direction == IN and deal.status == AGREED and window.open),
            can_accept_offer=bool(manager_turn and deal.direction == OUT and deal.status == BIDDING),
            can_reject_offer=bool(manager_turn and deal.direction == OUT and deal.status == BIDDING),
            can_counter_offer=bool(manager_turn and deal.direction == OUT and deal.status == BIDDING
                                   and int(deal.patience) > 0),
            reason=deal.reason or "",
            price_hint=_hint_of(enquiry) if deal.direction == IN else None,
        )

    # ================================================================== 13I: salt okunur listeler (tek sorgu)

    def _summary_rows(self, team_id: int, direction: str | None, open_only: bool, limit: int, ids=None):
        from sqlalchemy.orm import aliased

        seller, buyer = aliased(Team), aliased(Team)
        self.db.flush()
        stmt = (select(TransferDeal, Player.name, Player.position, Player.age, seller.name, buyer.name)
                .outerjoin(Player, Player.id == TransferDeal.player_id)
                .outerjoin(seller, seller.id == TransferDeal.seller_team_id)
                .outerjoin(buyer, buyer.id == TransferDeal.buyer_team_id)
                .where(TransferDeal.human_team_id == team_id, TransferDeal.kind == TRANSFER_KIND))
        if direction in (IN, OUT):
            stmt = stmt.where(TransferDeal.direction == direction)
        if open_only:
            stmt = stmt.where(TransferDeal.status.in_(sorted(OPEN)))
        if ids is not None:
            stmt = stmt.where(TransferDeal.id.in_(sorted(ids) or [-1]))
        stmt = stmt.order_by(TransferDeal.status.in_(sorted(OPEN)).desc(), TransferDeal.id.desc())
        return self.db.execute(stmt.limit(max(1, min(200, int(limit))))).all()

    def summaries(self, direction: str | None = None, open_only: bool = False, limit: int = 50) -> list[DealSummary]:
        """
        Kulubumun dosyalari liste satiri olarak (acik olanlar once, sonra en yeni). TEK sorgu: oyuncu ve iki kulup
        adi JOIN ile gelir; donem bilgisi bir kez okunur. Tam dosya icin deal(id).
        """
        team = self.cm.user_team
        if team is None or self.cm.game_mode is GameMode.TOURNAMENT:
            return []
        rows = self._summary_rows(team.id, direction, open_only, limit)
        window_open = self._window().open if rows else False
        return [self._summary(deal, name, position, age, seller, buyer, window_open)
                for deal, name, position, age, seller, buyer in rows]

    def _summary(self, deal: TransferDeal, name, position, age, seller, buyer, window_open: bool) -> DealSummary:
        is_open = deal.status in OPEN
        manager_turn = is_open and deal.turn == MANAGER
        out_bid = manager_turn and deal.direction == OUT and deal.status == BIDDING
        terms = None
        if not (deal.round == 0 and deal.status == ENQUIRY):
            terms = _terms_of(deal).describe()
        completable = deal.status == AGREED and deal.direction == IN and window_open
        return DealSummary(
            id=deal.id, direction=deal.direction, status=deal.status,
            status_label=STATUS_LABELS.get(deal.status, deal.status), turn=deal.turn if is_open else None,
            last_action=deal.last_action, player_id=deal.player_id, player_name=name or "Oyuncu",
            position=_ev(position) if position is not None else "", age=int(age or 0), seller_team=seller,
            buyer_team=buyer, terms_text=terms, fee=int(deal.fee or 0), upfront=int(deal.upfront or 0),
            instalment_months=int(deal.instalment_months or 0), sell_on_pct=int(deal.sell_on_pct or 0),
            mood=rules.patience_label(int(deal.patience)) if is_open and deal.status == BIDDING else None,
            expires_in_weeks=(max(0, int(deal.expires_career_week) - self.cw)
                              if is_open and deal.expires_career_week is not None else None),
            needs_action=bool((manager_turn and deal.status != ENQUIRY) or completable), can_accept_offer=bool(out_bid),
            can_reject_offer=bool(out_bid), can_counter_offer=bool(out_bid and int(deal.patience) > 0),
            reason=deal.reason or "")

    def action_count(self) -> int:
        """
        Menu sayaci: sira menajerde olan acik dosyalar (menajerin kendi actigi, henuz teklif yapmadigi bilgi alma
        dosyasi haric) + donem acikken tamamlanabilir anlasmalar. En fazla 2 sorgu.
        """
        team = self.cm.user_team
        if team is None or self.cm.game_mode is GameMode.TOURNAMENT:
            return 0
        self.db.flush()
        turn = int(self.db.scalar(select(func.count()).select_from(TransferDeal).where(
            TransferDeal.human_team_id == team.id, TransferDeal.status.in_(sorted(OPEN - {ENQUIRY})),
            TransferDeal.turn == MANAGER)) or 0)          # 15F: kiralik teklifleri de sayaca girer
        agreed = int(self.db.scalar(select(func.count()).select_from(TransferDeal).where(
            TransferDeal.human_team_id == team.id, TransferDeal.direction == IN,
            TransferDeal.kind == TRANSFER_KIND, TransferDeal.status == AGREED)) or 0)
        return turn + (agreed if agreed and self._window().open else 0)

    def open_deal_for(self, player_id: int) -> int | None:
        """Bu oyuncu icin acik IN dosyam (varsa id). Salt okuma."""
        team = self.cm.user_team
        if team is None or not _is_id(player_id):
            return None
        self.db.flush()
        return self.db.scalar(select(TransferDeal.id).where(
            TransferDeal.player_id == player_id, TransferDeal.buyer_team_id == team.id,
            TransferDeal.human_team_id == team.id, TransferDeal.kind == TRANSFER_KIND,
            TransferDeal.status.in_(sorted(OPEN))).limit(1))

    def knowledge_map(self, player_ids) -> dict[int, int]:
        """
        Birden cok oyuncu icin bilgi yuzdesi (knowledge_of ile ayni kural) -- IKI sorgu: gozlem kayitlari + oyuncunun
        kulubunun ligi. Transfer Merkezi arama tablosunun 'Bilgi' sutunu.
        """
        team = self._team()
        ids = sorted({int(i) for i in player_ids if _is_id(i)})
        if not ids:
            return {}
        self.db.flush()
        scouted = dict(self.db.execute(select(ScoutAssignment.player_id, ScoutAssignment.knowledge).where(
            ScoutAssignment.team_id == team.id, ScoutAssignment.player_id.in_(ids))).all())
        clubs = self.db.execute(select(Player.id, Player.team_id, Team.league_id)
                                .outerjoin(Team, Team.id == Player.team_id).where(Player.id.in_(ids))).all()
        result: dict[int, int] = {}
        for pid, team_id, league_id in clubs:
            if team_id == team.id:
                result[pid] = rules.MAX_KNOWLEDGE
                continue
            known = int(scouted.get(pid) or 0)
            if league_id is not None and league_id == team.league_id:
                known = max(known, rules.SAME_LEAGUE_KNOWLEDGE)
            result[pid] = min(rules.MAX_KNOWLEDGE, known)
        return result

    def terms_table(self, deal_id: int) -> TermsTable | None:
        """
        Sozlesme masasinin SALT OKUNUR goruntusu: masa hic acilmadiysa None. History'deki anlik goruntu ve tekliflerden
        deterministik yeniden kurulur; her teklif ve yanit gecmise yazilir. Hicbir sey yazmaz (cizim icin guvenli).
        """
        team = self._team()
        deal = self.db.get(TransferDeal, deal_id) if _is_id(deal_id) else None
        if deal is None or deal.human_team_id != team.id or deal.direction != IN:
            raise DeskError(DEAL_NOT_FOUND_TEXT)
        history = deal.history or []
        start = max((i for i, e in enumerate(history) if isinstance(e, dict) and e.get("kind") == "terms_open"),
                    default=None)
        if start is None:
            return None
        negotiation = self._negotiation_from(deal, history[start])
        log: list[tuple[str, str]] = []
        if negotiation.open:
            log.append(("him", f"{negotiation.player.name} ve menajeri taleplerini açıkladı: "
                               f"{negotiation.demand.describe()}"))
        else:
            log.append(("bad", negotiation.opening_message or "Oyuncu görüşmeyi reddetti."))
        last = None
        for entry in history[start + 1:]:
            if not isinstance(entry, dict) or entry.get("kind") != "terms_bid":
                continue
            if not negotiation.open:
                break
            offer = ContractOffer.from_dict(entry["offer"])
            log.append(("me", f"Teklif: {offer.describe()}"))
            last = negotiation.respond(offer)
            log.append(("bad" if last.status is NegotiationStatus.WALKED_AWAY else "him", last.message))
            log.extend(("him", f"· {c}") for c in last.complaints)
        return TermsTable(self._step(deal, negotiation, last), tuple(log))

    def _payment_view(self, row: TransferPayment, team_id: int) -> PaymentView:
        paying = row.payer_team_id == team_id
        other = row.payee_team_id if paying else row.payer_team_id
        player = self.db.get(Player, row.player_id) if row.player_id is not None else None
        due = (int(row.due_career_week) - self.cw) if row.due_career_week is not None else None
        counterparty = self._team_name(other) if other is not None else (
            "Oyuncu / menajeri" if paying else None)
        return PaymentView(row.id, row.deal_id, row.kind, PAYMENT_LABELS.get(row.kind, row.kind),
                           "PAY" if paying else "RECEIVE", counterparty, player.name if player else None,
                           int(row.amount), int(row.paid_amount), due, row.due_season, row.status,
                           PAYMENT_STATUS_LABELS.get(row.status, row.status), row.note or "")

    def payments(self, scope: str = "OPEN", limit: int = 100) -> list[PaymentView]:
        """Kulubumun transfer odemeleri. scope: OPEN (planli + gecikmis), ALL, PAY, RECEIVE."""
        team = self.cm.user_team
        if team is None:
            return []
        self.db.flush()
        stmt = select(TransferPayment).where(or_(TransferPayment.payer_team_id == team.id,
                                                 TransferPayment.payee_team_id == team.id))
        scope = str(scope or "OPEN").upper()
        if scope == "OPEN":
            stmt = stmt.where(TransferPayment.status.in_((SCHEDULED, OVERDUE)))
        elif scope == "PAY":
            stmt = stmt.where(TransferPayment.payer_team_id == team.id)
        elif scope == "RECEIVE":
            stmt = stmt.where(TransferPayment.payee_team_id == team.id)
        rows = list(self.db.scalars(stmt.order_by(TransferPayment.due_career_week.asc().nulls_last(),
                                                  TransferPayment.id).limit(max(1, min(500, int(limit))))))
        # 13I: oyuncu ve kulup adlari satir basina sorgu atmasin (N+1 yok): kimlik haritasi iki IN sorgusuyla dolar
        player_ids = sorted({r.player_id for r in rows if r.player_id is not None})
        team_ids = sorted({t for r in rows for t in (r.payer_team_id, r.payee_team_id) if t is not None})
        if player_ids:
            list(self.db.scalars(select(Player).where(Player.id.in_(player_ids))))
        if team_ids:
            list(self.db.scalars(select(Team).where(Team.id.in_(team_ids))))
        return [self._payment_view(r, team.id) for r in rows]

    def finance_summary(self) -> FinanceSummary:
        team = self.cm.user_team
        if team is None:
            return FinanceSummary(0, 0, 0, 0, ())
        rows = self.payments("OPEN", limit=500)
        pay = [r for r in rows if r.direction == "PAY"]
        rec = [r for r in rows if r.direction == "RECEIVE"]
        remaining = lambda items, status: sum(r.amount - r.paid_amount for r in items if r.status == status)  # noqa: E731
        upcoming = tuple(sorted((r for r in rows if r.status == SCHEDULED and r.due_in_weeks is not None),
                                key=lambda r: (r.due_in_weeks, r.id))[:5])
        return FinanceSummary(remaining(pay, SCHEDULED), remaining(rec, SCHEDULED), remaining(pay, OVERDUE),
                              remaining(rec, OVERDUE), upcoming)


# ---------------------------------------------------------------------------------------------------------------
# Modul fonksiyonlari: haftalik adim ve satis hooku
# ---------------------------------------------------------------------------------------------------------------

def run_week(cm: CareerManager, week: int, report=None) -> None:
    """
    CareerManager._play_week'ten (maaslar ve AI penceresinden sonra, hafta sayaci artmadan once). Turnuva modunda
    calismaz. Sira: gecersiz dosyalar -> sure dolanlar -> sirada bekleyen kulup yanitlari -> gozlem -> odemeler ->
    ek odemeler -> primler -> soz kontrolu -> donem acilinca bekleyen anlasmalar -> serbest kalma bedelleri -> AI
    teklifleri. Her adim kendi savepoint'inde; cm.rng'den cekilmez.
    """
    if cm.game_mode is GameMode.TOURNAMENT:
        return
    desk = TransferDesk(cm)
    desk._report = report
    desk._humans = cm.human_team_ids()
    next_week = desk.cw + 1
    next_open = desk._window(int(week) + 1, cm.season_finished).open
    desk._open_override = next_open
    desk._safe("sweep", desk._sweep)
    desk._safe("expire", desk._expire_due, next_week)
    desk._safe("responses", desk._queued_responses, next_week)
    desk._safe("scouting", desk._scouting_progress)
    desk._safe("payments", desk._process_payments)
    desk._safe("add_ons", desk._check_add_ons)
    desk._safe("bonuses", desk._pay_match_bonuses, week)
    desk._safe("promises", desk._check_promises)
    if next_open:
        desk._safe("window_completions", desk._complete_waiting)
    desk._safe("release_clauses", desk._release_clause_triggers, next_open)
    desk._safe("ai_bids", desk._ai_incoming_bids, next_open)
    LoanCycle(cm, report).run_week(int(week), next_open)      # 15F: opsiyonlar, AI kiralik teklifleri, dongu
    cm.db.flush()


def settle_sell_on(cm: CareerManager, player: Player, seller: Team, amount: int) -> int:
    """
    CareerManager.complete_transfer hooku (masa disi satislar: AI penceresi, market_hub, eski akis): oyuncuyu masada
    'sonraki satistan pay' maddesiyle alan kulup onu sattiginda eski kulube pay (bir kez). Odenen tutar.
    15F: madde kara dayaliysa alis bedelinin TAMAMI dusulur (bu satista bedelin tamami bir kerede alinir).
    """
    desk = TransferDesk(cm)
    source = desk._sell_on_source(player.id, seller.id)
    if source is None:
        return 0
    paid = desk._sell_on_share(source, seller, int(amount), f"SALE{desk.cw}",
                               int(source.fee) if getattr(source, "sell_on_profit", False) else 0)
    source.sell_on_used_career_week = desk.cw
    desk._log(source, {"kind": "sell_on_used", "amount": paid, "sale": int(amount)})
    return paid


def refund_sell_on(cm: CareerManager, player_id: int, seller_team_id: int, sale_career_week: int) -> int:
    """
    15F (faz14-devir §5): yonetici bir `market_hub` satisini GERI ALDIGINDA o satista odenmis 'sonraki satis
    payi' de iade edilir ve madde yeniden kullanilabilir olur (sell_on_used_career_week temizlenir).
    Para YARATILMAZ: pay alan kulubun kasasinda ne kadar varsa o kadar geri gider, kalan ilgili satirda
    'iade edilemedi' notuyla kalir. Iade edilen toplam tutar doner. Cagiran savepoint icinde cagirir.
    """
    desk = TransferDesk(cm)
    db = cm.db
    db.flush()
    rows = list(db.scalars(select(TransferPayment).where(
        TransferPayment.kind == "SELL_ON", TransferPayment.player_id == int(player_id),
        TransferPayment.payer_team_id == int(seller_team_id),
        TransferPayment.created_career_week == int(sale_career_week),
        TransferPayment.status != CANCELLED).order_by(TransferPayment.id).with_for_update()))
    refunded = 0
    for row in rows:
        paid = int(row.paid_amount)
        payer = db.get(Team, row.payer_team_id) if row.payer_team_id is not None else None
        payee = db.get(Team, row.payee_team_id) if row.payee_team_id is not None else None
        give = paid if payee is None else max(0, min(paid, int(payee.transfer_budget)))
        if give > 0 and payee is not None:
            payee.transfer_budget = int(payee.transfer_budget) - give
        if give > 0 and payer is not None:
            payer.transfer_budget = int(payer.transfer_budget) + give
        row.paid_amount, row.status = 0, CANCELLED
        short = "" if give >= paid else f" ({_money(paid - give)} iade edilemedi)"
        row.note = _clean(f"{row.note or 'Sonraki satış payı'} — satış geri alındı, iade {_money(give)}{short}", 160)
        refunded += give
        source = db.get(TransferDeal, row.deal_id) if row.deal_id is not None else None
        if source is not None:
            source.sell_on_used_career_week = None
            desk._log(source, {"kind": "sell_on_refund", "amount": give, "payment": row.id})
    db.flush()
    return refunded


# ===============================================================================================================
# 15A: SOZLESME DONGUSU (kurallar contracts.py). ContractCycle: dunya capinda AI adimlari ve sezon devri;
# ContractDesk: menajerin yenileme / serbest oyuncu / on sozlesme / fesih API'si. cm.rng'den CEKILMEZ: her karar
# kendi crc32 tohumundan (sezon, hafta, oyuncu, kulup). Bayrak kapaliyken (contracts.CONTRACT_CYCLE) hicbiri calismaz.
# ===============================================================================================================

CONTRACT_REF = "CONTRACT"
PRE_CONTRACT_APPROACH_CHANCE = 0.30   # on sozlesme doneminde AI kulubunun haftalik aday tarama olasiligi
PRE_CONTRACTS_PER_WEEK = 4            # dunya capinda haftalik AI on sozlesme siniri
PRE_CONTRACTS_PER_CLUB = 2            # AI kulubu sezonda en fazla bu kadar on sozlesme imzalar
PRE_CONTRACT_TRIES = 3
FREE_AGENT_SIGNINGS_PER_WEEK = 16     # dunya capinda haftalik AI serbest oyuncu imzasi siniri
FREE_AGENT_SIZE_CHANCE = 0.5          # kadro hedefin altinda: kulubun haftalik havuza bakma olasiligi
FREE_AGENT_UPGRADE_CHANCE = 0.08      # kadro tamam: firsat taramasi (belirgin guc artisi)
FREE_AGENT_TRIES = 3
PRESEASON_SIGNINGS_PER_CLUB = 4       # sezon devrinde AI kulubu havuzdan en fazla bu kadar imza
CONTRACT_NEWS_PER_WEEK = 3            # haftalik AI yenileme ve serbest imza haberleri (en degerliler)
RELEASE_NEWS_TOP = 3                  # devirde serbest kalan AI oyunculari: en degerli bu kadari ayri haber
EMERGENCY_WAGE_SHARE = 0.6            # kalecisiz kalan AI kulubunun acil imzasi: beklenen maasin bu kadari, 1 yil

CYCLE_OFF_TEXT = "Sözleşme döngüsü bu kariyerde kapalı."
TALK_NOT_FOUND_TEXT = "Sözleşme görüşmesi bulunamadı."
TALK_CLOSED_TEXT = "Görüşme kapandı ({status})."
LEAVING_TEXT = "{name} ön sözleşme imzaladı: sezon sonunda {team} kulübüne katılacak."
COOLDOWN_TEXT = "{name} şu an görüşmek istemiyor; {weeks} hafta sonra yeniden dene."
NOT_EXPIRING_TEXT = "{name} için ön sözleşme yapılamaz: sözleşmesi bu sezon bitmiyor."
WINDOW_TEXT = "Ön sözleşme dönemi {week}. haftada açılır (sezonun ikinci yarısı)."
SHORTER_TEXT = "Yeni sözleşme mevcut sözleşmeden ({years} yıl) kısa olamaz."
NOT_FREE_TEXT = "{name} serbest oyuncu değil."


@dataclass(frozen=True)
class ContractWindowView:
    """Sozlesme takvimi (arayuz seridi): on sozlesme donemi acik mi, hangi hafta acilir."""
    pre_contract_open: bool
    opens_week: int
    season_weeks: int
    label: str


@dataclass(frozen=True)
class ContractRow:
    """Sozlesmeler ekrani satiri (kendi oyuncum: sayilar kesin)."""
    player_id: int
    name: str
    age: int
    position: str
    in_academy: bool
    wage: int
    contract_years: int
    expires_season: int
    expiring: bool
    status: str                          # contracts.ROW_* kodu
    status_label: str
    talk_id: int | None
    other_team: str | None               # LEAVING: gidecegi kulup
    wage_demand: int | None
    attitude_label: str | None           # yenilemeye bakisi (K12: etiket)
    can_renew: bool
    can_terminate: bool
    termination_cost: int | None
    reason: str = ""


@dataclass(frozen=True)
class FreeAgentRow:
    """Serbest oyuncu havuzu satiri (K12: guc ve deger bilgi yuzdesine gore sisli)."""
    player_id: int
    name: str
    age: int
    position: str
    previous_club: str | None
    weeks_free: int
    knowledge: int
    knowledge_label: str
    overall: staff_rules.ScoutedValue | None
    value: staff_rules.ScoutedValue | None
    talk_id: int | None
    can_approach: bool
    reason: str = ""


@dataclass(frozen=True)
class PreContractRow:
    """On sozlesme adayi (AI kulubunde sozlesmesi biten oyuncu; K12: sisli)."""
    player_id: int
    name: str
    age: int
    position: str
    team_id: int
    team: str
    knowledge: int
    knowledge_label: str
    overall: staff_rules.ScoutedValue | None
    value: staff_rules.ScoutedValue | None
    talk_id: int | None
    can_approach: bool
    reason: str = ""


@dataclass(frozen=True)
class ContractStep:
    """Sozlesme masasinin adimi (transfer masasi TermsStep'in esi; arayuz ayni bileseni kullanabilir)."""
    talk_id: int
    kind: str
    status: NegotiationStatus            # OPEN / ACCEPTED / WALKED_AWAY
    talk_status: str                     # contracts: OPEN / AGREED / SIGNED / COLLAPSED ...
    message: str
    complaints: tuple[str, ...]
    demand: ContractOffer | None         # OPEN: oyuncunun guncel talebi; kabul: anlasilan sozlesme
    rounds_left: int
    mood: str                            # K12: ikna skoru yerine ruh hali
    needs_room: int | None               # AGREED ama maas alani yetmiyor: haftalik eksik (sign(shift_wage_room=True))
    cost_now: int                        # imzada kasadan cikacak: imza primi + menajer ucreti
    player_id: int
    player_name: str


@dataclass(frozen=True)
class ContractTalkView:
    id: int
    kind: str
    kind_label: str
    status: str
    status_label: str
    direction: str                       # IN (oyuncu kulubume) / OUT (oyuncum baska kulube) / OWN (yenileme)
    player_id: int
    player_name: str
    team_id: int | None
    team: str | None
    from_team_id: int | None
    from_team: str | None
    contract: ContractOffer | None
    effective_season: int | None
    reason: str
    expires_in_weeks: int | None
    history: tuple[str, ...]
    can_submit: bool
    can_sign: bool
    can_withdraw: bool


@dataclass(frozen=True)
class TerminationQuote:
    player_id: int
    name: str
    wage: int
    contract_years: int
    remaining_weeks: float
    compensation: int
    budget: int
    can_terminate: bool
    reason: str
    done: bool = False


def _talk_contract(talk: ContractTalk) -> ContractOffer | None:
    try:
        return ContractOffer.from_dict(talk.contract) if talk.contract else None
    except (KeyError, TypeError, ValueError):
        return None


def _talk_negotiation(talk: ContractTalk, opening: dict) -> ContractNegotiation:
    """Masa (menajer, agent=True) history'deki anlik goruntuden deterministik kurulur (transfer_desk ile ayni bicim)."""
    p, b = opening["player"], opening["buyer"]
    player = SimpleNamespace(id=talk.player_id, name=p["name"], overall_rating=int(p["overall"]), age=int(p["age"]),
                             current_wage=int(p["wage"]), position=Position(p["position"]),
                             market_value=int(p["value"]), team=SimpleNamespace(reputation=int(p["team_rep"])))
    buyer = SimpleNamespace(id=talk.team_id, reputation=int(b["rep"]),
                            players=[SimpleNamespace(overall_rating=int(r)) for r in b["ratings"]])
    rng = random.Random(zlib.crc32(f"contract-terms|{talk.id}|{talk.team_id}|{talk.player_id}".encode()))
    negotiation = ContractNegotiation(rng, player, buyer, int(opening.get("fee", 0)),
                                      manager_reputation=float(opening["manager_rep"]), agent=True,
                                      demand_multiplier=float(opening.get("multiplier", 1.0)),
                                      free_signing=opening.get("free_signing"))
    if opening.get("refusal") and negotiation.open:
        negotiation.status = NegotiationStatus.WALKED_AWAY
        negotiation.opening_message = opening["refusal"]
    return negotiation


def _talk_replay(talk: ContractTalk):
    """(negotiation, son yanit) ya da (None, None)."""
    history = talk.history or []
    start = max((i for i, e in enumerate(history) if isinstance(e, dict) and e.get("kind") == "terms_open"),
                default=None)
    if start is None:
        return None, None
    negotiation = _talk_negotiation(talk, history[start])
    last = None
    for entry in history[start + 1:]:
        if not isinstance(entry, dict) or entry.get("kind") != "terms_bid":
            continue
        if not negotiation.open:
            break
        last = negotiation.respond(ContractOffer.from_dict(entry["offer"]))
    return negotiation, last


class _ContractBase:
    """ContractCycle / ContractDesk ortak yardimcilari (kayit, bildirim, odeme, yenileme uygulamasi)."""

    def __init__(self, cm: CareerManager, report=None) -> None:
        self.cm = cm
        self.db = cm.db
        self.desk = TransferDesk(cm)
        self.desk._report = report
        self.report = report

    @property
    def cw(self) -> int:
        return int(self.cm.career_week)

    def _season_weeks(self) -> int:
        return max(1, int(self.cm._projected_season_weeks() or 1))

    def _humans(self) -> frozenset[int]:
        return self.desk._humans_now()

    def _guarded(self, label: str, fn, *args) -> bool:
        """Adim kendi savepoint'inde: hata loglanir, hafta / devir bozulmaz (eski davranis: adim hic olmamis gibi)."""
        self.db.flush()
        try:
            with self.db.begin_nested():
                fn(*args)
            return True
        except (SQLAlchemyError, ValueError, TransferError, finance.BudgetError) as exc:
            log.exception("Sözleşme döngüsü adımı başarısız (%s): %s", label, exc)
            self.cm._contract_holds = None
            return False

    def _new_talk(self, *, kind: str, status: str, player: Player, team: Team | None, from_team_id: int | None,
                  human_team_id: int | None, contract: ContractOffer | None = None, reason: str | None = None,
                  effective_season: int | None = None, expires: int | None = None,
                  history: list | None = None, season: int | None = None) -> ContractTalk:
        talk = ContractTalk(
            season=int(self.cm.season if season is None else season), kind=kind, status=status, player_id=player.id,
            team_id=team.id if team is not None else None, from_team_id=from_team_id, human_team_id=human_team_id,
            created_career_week=self.cw, updated_career_week=self.cw, expires_career_week=expires,
            effective_season=effective_season, reason=_clean(reason) or None, history=list(history or []),
            contract=contract.to_dict() if contract is not None else {}, updated_at=_now())
        self.db.add(talk)
        return talk

    def _set_talk(self, talk: ContractTalk, status: str, reason: str | None = None) -> None:
        talk.status = status
        talk.updated_career_week = self.cw
        talk.updated_at = _now()
        if reason is not None:
            talk.reason = _clean(reason) or None
        if status not in contracts.LIVE_TALK_STATUSES:
            talk.expires_career_week = None
        talk.history = [*(talk.history or []), {"cw": self.cw, "kind": "status", "status": status,
                                                  "reason": _clean(reason) if reason else None}]

    def _note(self, team_id: int | None, text: str) -> None:
        """Insan kulubunun hafta raporu (transfer_notes) + koltuk bildirimi (satiri varsa)."""
        if team_id is None or not text or team_id not in self._humans():
            return
        if self.report is not None:
            self.cm._sink(self.report, team_id).transfer_notes.append(_clean(text))
        seat_id = self.desk._seat_id(team_id)
        if seat_id is None:
            return
        self.db.flush()
        try:
            with self.db.begin_nested():
                messaging.notify(self.db, seat_id, NotificationKind.OFFER_UPDATE, text, CONTRACT_REF, None)
        except (SQLAlchemyError, ValueError) as exc:
            log.warning("Sözleşme bildirimi yazılamadı: %s", exc)

    def _renewal_proxy(self, team: Team, player: Player, teammates: list[int]):
        """Yenileme masasinin oyuncu / kulup vekilleri (veteranin maas tabani dusuk; kulup kendisi, oyuncu haric)."""
        wage = contracts.renewal_wage_basis(int(player.current_wage or 0), int(player.age), int(player.overall_rating),
                                            player.contract_overall)
        proxy = SimpleNamespace(id=player.id, name=player.name, overall_rating=int(player.overall_rating),
                                age=int(player.age), current_wage=wage, position=player.position,
                                market_value=int(player.market_value or 0),
                                team=SimpleNamespace(reputation=int(team.reputation)))
        buyer = SimpleNamespace(id=team.id, reputation=int(team.reputation),
                                players=[SimpleNamespace(overall_rating=int(r)) for r in teammates])
        return proxy, buyer

    def _attitude(self, team: Team, player: Player) -> contracts.RenewalAttitude:
        clauses = player.contract_clauses or {}
        ambition = rules.hidden_trait("ambition", player.id, (player.fm_attributes or {}).get("ambition"))
        return contracts.renewal_attitude(
            overall=int(player.overall_rating), club_reputation=int(team.reputation),
            manager_reputation=float(self.cm.manager_reputation_for(team)), concern_level=int(player.concern_level or 0),
            wants_away=bool(clauses.get("wants_away")), ambition=ambition)

    def _apply_renewal(self, player: Player, offer: ContractOffer) -> None:
        import concerns

        player.current_wage = int(offer.wage)
        player.contract_years = contracts.renewal_contract_years(int(offer.years))
        player.squad_role = offer.role
        player.contract_overall = player.overall_rating
        if player.wage_demand is not None:
            player.wage_demand = None
        player.morale = max(0, min(100, int(player.morale) + concerns.WAGE_ACCEPT_MORALE))

    def _pay_now(self, talk: ContractTalk, team: Team, player: Player, contract: ContractOffer) -> None:
        """Imza primi + menajer ucreti (odeme defteri; kasa eksiye DUSMEZ) ve sadakat primi plani."""
        for kind, amount, label in (("SIGNING", contract.signing_fee, "imza primi"),
                                    ("AGENT", contract.agent_fee, "menajer ücreti")):
            row = self.desk._payment(deal=None, kind=kind, ref=f"K{talk.id}#{kind}", payer_id=team.id, payee_id=None,
                                     amount=int(amount), player_id=player.id, due_cw=self.cw,
                                     note=f"{player.name} {label}")
            if row is not None:
                self.desk._settle(row)
        for k in range(1, int(contract.years) + 1):
            self.desk._payment(deal=None, kind="LOYALTY", ref=f"K{talk.id}#L{k}", payer_id=team.id, payee_id=None,
                               amount=int(contract.loyalty_bonus), player_id=player.id,
                               due_season=int(self.cm.season) + k, note=f"{player.name} sadakat primi ({k}. sezon)")

    def _clauses(self, talk: ContractTalk, contract: ContractOffer) -> dict:
        clauses = {"talk_id": talk.id, "signed_season": int(self.cm.season), "promise_week": self.cw,
                   "promised_role": contract.role.value}
        if contract.appearance_bonus:
            clauses["appearance_bonus"] = int(contract.appearance_bonus)
        if contract.goal_bonus:
            clauses["goal_bonus"] = int(contract.goal_bonus)
        if contract.loyalty_bonus:
            clauses["loyalty_bonus"] = int(contract.loyalty_bonus)
        return clauses


class ContractCycle(_ContractBase):
    """
    Dunya capinda sozlesme dongusu (AI kulupleri + sezon devri). Kontrolcu: flush eder, commit ETMEZ.
    Haftalik (run_contract_week): AI yenileme kararlari -> uyarilar -> on sozlesme donemi -> serbest oyuncu -> sure dolan
    gorusmeler. Devir (CareerManager._start_new_season): before_rollover -> [yas / sozlesme dususu] -> after_decrement
    -> [akademi yonetimi] -> preseason.
    """

    def __init__(self, cm: CareerManager, report=None) -> None:
        super().__init__(cm, report)
        self.desk._humans = cm.human_team_ids()
        self.humans = self.desk._humans
        self.protected = cm._protected_team_ids()
        self.season = int(cm.season)
        self.season_weeks = self._season_weeks()
        self.start = contracts.pre_contract_start(self.season_weeks)
        self._teams: dict[int, Team] | None = None
        self._squads: dict[int, list[Player]] = {}
        self._shapes: dict[tuple, contracts.SquadShape] = {}
        self._norm = contracts.SQUAD_MIN                  # AI kulup kadrolarinin medyani (_load)
        self.decided: set[int] = set()          # bu sezon yenileme karari verilmis (AI) / yenilenmis oyuncular
        self.let_go: set[int] = set()           # AI kulubu yenilemedi ya da oyuncu reddetti (on sozlesmeye acik)
        self.pre: dict[int, tuple[int | None, int | None]] = {}      # canli on sozlesme: oyuncu -> (alici, kulubu)
        self.live: set[int] = set()             # insan masasinda canli gorusmesi olan oyuncular
        self.pre_count: dict[int, int] = {}     # alici -> bu sezon imzaladigi on sozlesme

    # ------------------------------------------------------------------ durum
    def _load(self, *relations) -> dict[int, Team]:
        if self._teams is None or relations:
            teams = self.cm._load_teams(Team.players, *relations)
            self._teams = {t.id: t for t in teams}
            self._squads = {t.id: list(t.players) for t in teams}
            self._shapes = {}
            ai_sizes = sorted(len(v) for k, v in self._squads.items() if k not in self.humans)
            self._norm = ai_sizes[len(ai_sizes) // 2] if ai_sizes else contracts.SQUAD_MIN
        return self._teams

    def _norm_from_db(self) -> None:
        """_load'suz AI kadro medyani (tek sorgu; devirde tam okuma gerekmediginde)."""
        self.db.flush()
        rows = self.db.execute(select(Player.team_id, func.count()).where(
            Player.team_id.isnot(None), Player.in_academy.is_(False)).group_by(Player.team_id)).all()
        sizes = sorted(int(n) for team_id, n in rows if team_id not in self.humans)
        self._norm = sizes[len(sizes) // 2] if sizes else contracts.SQUAD_MIN

    def _floor(self) -> int:
        """AI kadro tabani: SQUAD_MIN, ama dunyanin olagan kadrosunu (AI medyani) asmaz (kucuk sentetik dunya: 15)."""
        return min(contracts.SQUAD_MIN, max(contracts.SAFETY_SQUAD - 1, self._norm))

    def _safety(self) -> int:
        """Devir kadro guvencesi / insan kulubu tabani: SAFETY_SQUAD, kucuk dunyada olagan kadronun 3 alti (en az 12)."""
        return min(contracts.SAFETY_SQUAD, max(12, self._norm - 3))

    def _target(self) -> int:
        """AI serbest oyuncu hedefi: SQUAD_TARGET, ama olagan kadronun en fazla 1 ustu."""
        return min(contracts.SQUAD_TARGET, max(self._floor(), self._norm + 1))

    def _read_talks(self) -> None:
        self.db.flush()
        rows = self.db.execute(select(
            ContractTalk.player_id, ContractTalk.kind, ContractTalk.status, ContractTalk.team_id,
            ContractTalk.from_team_id, ContractTalk.season).where(or_(
                ContractTalk.season == self.season, ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES)))).all()
        self.decided, self.let_go, self.pre, self.live, self.pre_count = set(), set(), {}, set(), {}
        for pid, kind, status, team_id, from_id, season in rows:
            if kind == contracts.KIND_PRE_CONTRACT and status in (contracts.AGREED, contracts.SIGNED):
                if season == self.season and team_id is not None:
                    self.pre_count[team_id] = self.pre_count.get(team_id, 0) + 1
                if status == contracts.AGREED:
                    self.pre[pid] = (team_id, from_id)
                continue
            if status in contracts.LIVE_TALK_STATUSES:
                self.live.add(pid)
            if kind == contracts.KIND_RENEWAL and season == self.season and \
                    status in (contracts.SIGNED, contracts.DECLINED, contracts.REFUSED):
                self.decided.add(pid)
                if status != contracts.SIGNED:
                    self.let_go.add(pid)

    def _shape(self, team_id: int, *, min_years: int = 2, current: bool = False) -> contracts.SquadShape:
        """
        Kulubun kadro ozeti. current: bugunku A takim (kiralik dahil). Aksi GELECEK SEZON: en az min_years yil
        sozlesmesi olanlar (ayrilacaklar ve kiralik gelenler haric) + on sozlesmeyle gelecekler.
        """
        key = (team_id, min_years, current)
        if key not in self._shapes:
            players = self._squads.get(team_id, [])
            if current:
                pairs = [(p.position, p.overall_rating) for p in players]
            else:
                pairs = [(p.position, p.overall_rating) for p in players
                         if int(p.contract_years or 0) >= min_years and p.id not in self.pre
                         and p.loan_from_team_id is None]
                for pid, (buyer_id, _from) in self.pre.items():
                    if buyer_id == team_id:
                        incoming = self.db.get(Player, pid)
                        if incoming is not None:
                            pairs.append((incoming.position, incoming.overall_rating))
            self._shapes[key] = contracts.SquadShape.of(pairs)
        return self._shapes[key]

    def _changed(self, *team_ids) -> None:
        for key in [k for k in self._shapes if k[0] in team_ids]:
            del self._shapes[key]

    # ------------------------------------------------------------------ haftalik
    def run_week(self, week: int) -> list:
        st = self.cm.state
        if st.contracts_since_cw is None:
            st.contracts_since_cw = self.cw
        signed: list = []
        if not self._guarded("contracts", self._week_body, int(week), signed):
            return []
        return signed

    def _week_body(self, week: int, signed: list) -> None:
        self._load()
        self._read_talks()
        self._ai_renewals(week, catch_up=week >= self.start - 1)
        self._notices(week)
        if not self.cm.season_finished and week >= self.start:
            self._ai_pre_contracts(week)
        signed += self._ai_free_agents(week)
        self._fallbacks(None, None, humans=False)          # havuz bosken AI kadrosu SQUAD_MIN alti: akademiden
        self._expire_talks()
        self.db.flush()

    def _notices(self, week: int) -> None:
        humans = sorted(self.humans)
        if not humans:
            return
        first = week == 1 or self.cm.state.contracts_since_cw == self.cw
        if not first and week != self.start:
            return
        for team_id in humans:
            ending = sorted((p for p in self._squads.get(team_id, []) if contracts.expiring(p.contract_years)
                             and p.loan_from_team_id is None), key=lambda p: (-p.overall_rating, p.id))
            if not ending:
                continue
            names = ", ".join(p.name for p in ending[:8]) + (f" ve {len(ending) - 8} oyuncu daha" if len(ending) > 8
                                                              else "")
            if week == self.start:
                text = (f"Ön sözleşme dönemi açıldı: sözleşmesi biten {len(ending)} oyuncun ({names}) artık başka "
                        f"kulüplerle bedelsiz anlaşabilir. Yenilemek için Sözleşmeler ekranı.")
            else:
                text = (f"Sözleşmesi bu sezon bitenler: {names}. Yenilemezsen sezon sonunda serbest kalırlar; "
                        f"{self.start}. haftadan itibaren başka kulüplerle ön sözleşme imzalayabilirler.")
            self._note(team_id, text)

    # ---- AI yenileme kararlari
    def _ai_renewals(self, week: int, *, catch_up: bool = False) -> None:
        """
        AI kulubu sozlesmesi biten oyuncularina kulup basina TEK haftada (contracts.renewal_decision_week; sonra gelenler
        hemen) karar verir: once en istenen (cekirdek kadroya gore skor), her karar gelecek sezon kadrosunu gunceller
        -- zorunlu yenilemeler (kaleci / kadro tabani) en iyilerine duser.
        """
        due: dict[int, list[Player]] = {}
        for team_id, players in self._squads.items():
            if team_id in self.humans:
                continue
            if not catch_up and contracts.renewal_decision_week(team_id, self.season, self.season_weeks) > week:
                continue
            for p in players:
                if not contracts.expiring(p.contract_years) or p.loan_from_team_id is not None:
                    continue
                if p.id in self.decided or p.id in self.pre or p.id in self.live:
                    continue
                due.setdefault(team_id, []).append(p)
        renewed: list[tuple[Player, Team, ContractOffer]] = []
        for team_id in sorted(due):
            team = self._teams[team_id]
            core = self._shape(team_id)
            expected = self._expected_pairs(team_id)
            ranked = sorted(due[team_id], key=lambda p: (-contracts.club_renewal_score(
                age=int(p.age), overall=int(p.overall_rating), potential=p.potential_rating, position=p.position,
                shape=_without(expected, p), core=core).score, p.id))
            for p in ranked:
                status, offer, reason = self._decide_renewal(team, p, expected)
                self._new_talk(kind=contracts.KIND_RENEWAL, status=status, player=p, team=team,
                               from_team_id=team.id, human_team_id=None, contract=offer, reason=reason)
                self.decided.add(p.id)
                if status == contracts.SIGNED:
                    renewed.append((p, team, offer))
                    self._changed(team.id)
                else:
                    self.let_go.add(p.id)
                    if (p.position, int(p.overall_rating)) in expected:
                        expected.remove((p.position, int(p.overall_rating)))
        for p, team, offer in sorted(renewed, key=lambda r: (-int(r[0].market_value or 0), r[0].id))[
                :CONTRACT_NEWS_PER_WEEK]:
            self.cm._add_news(NewsKind.CONTRACT, f"{team.name}, {p.name} ile sözleşmesini {offer.years} yıl uzattı "
                                                 f"({_money(offer.wage)}/hafta).", team_id=team.id)

    def _expected_pairs(self, team_id: int) -> list[tuple[Position, int]]:
        """Beklenen gelecek sezon kadrosu: bugunku A takim eksi birakilan / reddeden / ayrilacak / kiralik gelenler,
        arti on sozlesmeyle gelecekler ((mevki, guc) ciftleri)."""
        pairs = [(p.position, int(p.overall_rating)) for p in self._squads.get(team_id, [])
                 if p.id not in self.let_go and p.id not in self.pre and p.loan_from_team_id is None]
        for pid, (buyer_id, _from) in self.pre.items():
            if buyer_id == team_id:
                incoming = self.db.get(Player, pid)
                if incoming is not None:
                    pairs.append((incoming.position, int(incoming.overall_rating)))
        return pairs

    def _decide_renewal(self, team: Team, p: Player,
                        expected: list[tuple[Position, int]]) -> tuple[str, ContractOffer | None, str]:
        score = contracts.club_renewal_score(age=int(p.age), overall=int(p.overall_rating),
                                             potential=p.potential_rating, position=p.position,
                                             shape=_without(expected, p), core=self._shape(team.id))
        rng = _rng("contract-renew", self.season, p.id)
        if not (score.must or rng.random() < score.probability):
            return contracts.DECLINED, None, f"{team.name} sözleşmeyi yenilemedi ({score.reason})."
        attitude = self._attitude(team, p)
        if attitude.refuses:
            return contracts.REFUSED, None, f"{p.name}: \"{attitude.reason}\""
        teammates = sorted((q.overall_rating for q in self._squads.get(team.id, []) if q.id != p.id), reverse=True)
        proxy, buyer = self._renewal_proxy(team, p, teammates)
        negotiation = ContractNegotiation(rng, proxy, buyer, 0, manager_reputation=self.cm.manager_reputation_for(team),
                                          demand_multiplier=attitude.wage_multiplier)
        if not negotiation.open:
            return contracts.REFUSED, None, f"{p.name}: \"{negotiation.interest.reason}\""
        room = int(team.free_wage) + int(p.current_wage or 0)
        offer = transfers.ai_contract_offer(rng, negotiation, max(room, negotiation.demand.wage))
        if negotiation.persuasion(offer) < negotiation.required_persuasion:
            return contracts.REFUSED, None, f"{p.name} yeni sözleşme şartlarını beğenmedi."
        raise_by = int(offer.wage) - int(p.current_wage or 0)
        if raise_by > int(team.free_wage):
            shift = finance.auto_shift_for_wage(int(team.transfer_budget), int(team.wage_budget), int(team.free_wage),
                                                raise_by)
            if shift > 0:
                try:
                    self.cm.shift_budget(team, shift)
                except finance.BudgetError:
                    pass
            if raise_by > int(team.free_wage):
                return contracts.DECLINED, None, f"{team.name} maaş talebini karşılayamadı."
        response = negotiation.respond(offer)
        if response.status is not NegotiationStatus.ACCEPTED:
            return contracts.REFUSED, None, f"{p.name} yeni sözleşmeyi kabul etmedi."
        self._apply_renewal(p, offer)
        return contracts.SIGNED, offer, f"{p.name} sözleşme yeniledi ({score.reason})."

    # ---- on sozlesme (AI alici)
    def _bosman_pool(self) -> list[Player]:
        pool = []
        for team_id, players in self._squads.items():
            if team_id in self.protected:
                continue
            human = team_id in self.humans
            for p in players:
                if not contracts.expiring(p.contract_years) or p.loan_from_team_id is not None or p.id in self.pre:
                    continue
                if human and p.id in self.live:
                    continue
                if not human and p.id not in self.let_go:
                    continue
                pool.append(p)
        pool.sort(key=lambda p: p.id)
        return pool

    def _ai_pre_contracts(self, week: int) -> None:
        pool = self._bosman_pool()
        if not pool:
            return
        rng = _rng("pre-contract-buyers", self.season, week)
        buyers = [t for _tid, t in sorted(self._teams.items())
                  if t.id not in self.humans and t.id not in self.protected]
        rng.shuffle(buyers)
        made = 0
        for buyer in buyers:
            if made >= PRE_CONTRACTS_PER_WEEK or not pool:
                break
            if rng.random() >= PRE_CONTRACT_APPROACH_CHANCE:
                continue
            if self.pre_count.get(buyer.id, 0) >= PRE_CONTRACTS_PER_CLUB:
                continue
            shape = self._shape(buyer.id)
            if shape.size >= contracts.SQUAD_MAX:
                continue
            scored = []
            for p in pool:
                if p.team_id == buyer.id or int(p.age) > contracts.PRE_CONTRACT_MAX_AGE:
                    continue
                if self._staying(p.team_id) - 1 < self._safety():
                    continue                    # kulubunun kadrosunu guvence tabaninin altina dusurmez
                fit = contracts.target_fit(overall=p.overall_rating, age=p.age, position=p.position, shape=shape)
                if fit < contracts.PRE_CONTRACT_MIN_FIT:
                    continue
                if p.position is Position.GK and self._shape(p.team_id).counts.get(Position.GK, 0) < \
                        contracts.MIN_KEEPERS:
                    continue                    # kulubunu kalecisiz birakmaz (dunya dengesi)
                scored.append((fit, p.id, p))
            scored.sort(key=lambda s: (-s[0], s[1]))
            for _fit, _pid, p in scored[:PRE_CONTRACT_TRIES]:
                if self._try_pre_contract(buyer, p, rng) is not None:
                    made += 1
                    pool.remove(p)
                    break

    def _staying(self, team_id: int) -> int:
        """Kulubun bugunku A takimi eksi on sozlesmeyle ayrilacaklar."""
        leaving = sum(1 for _pid, (_buyer, source) in self.pre.items() if source == team_id)
        return len(self._squads.get(team_id, [])) - leaving

    def _try_pre_contract(self, buyer: Team, p: Player, rng: random.Random) -> ContractTalk | None:
        source = self._teams.get(p.team_id)
        if source is None:
            return None
        interest = self.desk._interest(p, buyer)
        if interest.refuses:
            return None
        negotiation = ContractNegotiation(rng, p, buyer, 0, manager_reputation=self.cm.manager_reputation_for(buyer),
                                          demand_multiplier=interest.wage_multiplier)
        if not negotiation.open:
            return None
        offer = transfers.ai_contract_offer(rng, negotiation, max(int(buyer.free_wage), negotiation.demand.wage))
        if negotiation.persuasion(offer) < negotiation.required_persuasion:
            return None
        if int(offer.wage) > int(buyer.free_wage) + finance.max_shiftable_to_wages(int(buyer.transfer_budget)):
            return None
        if negotiation.respond(offer).status is not NegotiationStatus.ACCEPTED:
            return None
        human = source.id if source.id in self.humans else None
        try:
            with self.db.begin_nested():
                talk = self._new_talk(kind=contracts.KIND_PRE_CONTRACT, status=contracts.AGREED, player=p, team=buyer,
                                      from_team_id=source.id, human_team_id=human, contract=offer,
                                      effective_season=self.season + 1,
                                      reason=f"{p.name} sezon sonunda {buyer.name} kulübüne katılacak.")
                self.db.flush()
        except IntegrityError:
            return None
        self.pre[p.id] = (buyer.id, source.id)
        self.pre_count[buyer.id] = self.pre_count.get(buyer.id, 0) + 1
        self._changed(buyer.id, source.id)
        self.cm._contract_holds = None
        self.cm._add_news(NewsKind.CONTRACT, f"Ön sözleşme: {p.name} sezon sonunda {source.name} kulübünden "
                                             f"{buyer.name} kulübüne bedelsiz geçecek.", team_id=buyer.id,
                          other_team_id=source.id)
        if human is not None:
            self._note(human, f"{p.name}, {buyer.name} ile ön sözleşme imzaladı: sezon sonunda bedelsiz ayrılacak "
                              f"({_money(offer.wage)}/hafta, {offer.years} yıl).")
        return talk

    # ---- serbest oyuncular (AI)
    def _free_agent_pool(self) -> list[Player]:
        self.db.flush()
        return list(self.db.scalars(select(Player).where(Player.team_id.is_(None)).order_by(Player.id)))

    def _ai_clubs(self) -> list[Team]:
        return [t for _tid, t in sorted(self._teams.items()) if t.id not in self.humans and t.id not in self.protected]

    def _ai_free_agents(self, week: int) -> list:
        pool = self._free_agent_pool()
        if not pool:
            return []
        rng = _rng("free-agent-clubs", self.season, week)
        clubs = self._ai_clubs()
        rng.shuffle(clubs)
        clubs.sort(key=lambda t: 0 if self._shape(t.id, current=True).urgent_positions() else 1)
        signed = []
        for club in clubs:
            if len(signed) >= FREE_AGENT_SIGNINGS_PER_WEEK or not pool:
                break
            shape = self._shape(club.id, current=True)
            if shape.urgent_positions():
                mode = "urgent"
            elif shape.size < self._floor():
                mode = "size"
            elif shape.size < self._target():
                mode = "size" if rng.random() < FREE_AGENT_SIZE_CHANCE else None
            elif shape.size < contracts.SQUAD_MAX and rng.random() < FREE_AGENT_UPGRADE_CHANCE:
                mode = "upgrade"
            else:
                mode = None
            if mode is None:
                continue
            news = self._sign_best(club, shape, pool, mode, rng)
            if news is not None:
                signed.append(news)
        self._free_agent_news(signed)
        return signed

    def _free_agent_news(self, signed: list) -> None:
        for n in sorted(signed, key=lambda n: (-int(n.wage), n.player_id or 0))[:CONTRACT_NEWS_PER_WEEK]:
            if n.to_team_id in self.humans:
                continue                                    # insan kulubunun imzasi zaten haber oldu
            self.cm._add_news(NewsKind.TRANSFER, self.cm._transfer_news_text(n), team_id=n.to_team_id)

    def _sign_best(self, club: Team, shape: contracts.SquadShape, pool: list[Player], mode: str,
                   rng: random.Random, *, season: int | None = None, week: int | None = None):
        positions = set(shape.urgent_positions()) if mode == "urgent" else set(Position)
        threshold = {"urgent": contracts.FREE_AGENT_MIN_FIT_URGENT, "size": contracts.FREE_AGENT_MIN_FIT_SIZE,
                     "upgrade": contracts.FREE_AGENT_MIN_FIT_UPGRADE}[mode]
        scored = []
        for p in pool:
            if p.position not in positions:
                continue
            fit = contracts.target_fit(overall=p.overall_rating, age=p.age, position=p.position, shape=shape)
            if mode == "upgrade":
                weakest = shape.weakest(p.position)
                if weakest is None or int(p.overall_rating) - weakest < contracts.FREE_AGENT_MIN_FIT_UPGRADE:
                    continue
            elif fit < threshold:
                continue
            scored.append((fit, p.id, p))
        scored.sort(key=lambda s: (-s[0], s[1]))
        for _fit, _pid, p in scored[:FREE_AGENT_TRIES]:
            offer = self._free_agent_offer(club, p, rng)
            if offer is None:
                continue
            news = self._sign(club, p, offer, pool, season=season, week=week)
            if news is not None:
                return news
        return None

    def _free_agent_offer(self, club: Team, p: Player, rng: random.Random) -> ContractOffer | None:
        weeks = contracts.weeks_free(p.free_agent_since, self.cw)
        proxy = SimpleNamespace(id=p.id, name=p.name, overall_rating=contracts.expectation_overall(p.overall_rating, weeks),
                                age=int(p.age), current_wage=0, position=p.position,
                                market_value=int(p.market_value or 0), team=None)
        negotiation = ContractNegotiation(rng, proxy, club, 0, manager_reputation=self.cm.manager_reputation_for(club),
                                          demand_multiplier=contracts.free_agent_wage_factor(weeks))
        if not negotiation.open:
            return None
        offer = transfers.ai_contract_offer(rng, negotiation, max(int(club.free_wage), negotiation.demand.wage))
        if negotiation.persuasion(offer) < negotiation.required_persuasion:
            return None
        if not self._make_room(club, int(offer.wage)):
            return None
        if negotiation.respond(offer).status is not NegotiationStatus.ACCEPTED:
            return None
        return offer

    def _make_room(self, club: Team, wage: int) -> bool:
        """AI butce kaydirmasi (CareerManager.shift_budget kurali; toplu adimda flush etmez). Yer acildi mi?"""
        if wage <= int(club.free_wage):
            return True
        shift = finance.auto_shift_for_wage(int(club.transfer_budget), int(club.wage_budget), int(club.free_wage), wage)
        if shift > 0:
            try:
                club.transfer_budget, club.wage_budget = finance.plan_budget_shift(
                    int(club.transfer_budget), int(club.wage_budget), shift, int(club.wage_bill))
            except finance.BudgetError:
                return False
        return wage <= int(club.free_wage)

    def _sign(self, club: Team, p: Player, offer: ContractOffer, pool: list[Player], *, season: int | None = None,
              week: int | None = None):
        try:                                            # dogrulama degisiklikten ONCE: savepoint gerekmez
            news = self.cm.sign_free_agent(club, p, offer, season=season, week=week, flush=False)
        except TransferError as exc:
            log.warning("Serbest oyuncu imzası yapılamadı (%s): %s", p.id, exc)
            return None
        if p in pool:
            pool.remove(p)
        self._squads.setdefault(club.id, []).append(p)
        self._changed(club.id)
        return news

    # ---- gorusme suresi / gecerliligi
    def _expire_talks(self) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(ContractTalk).where(
            ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES), ContractTalk.human_team_id.isnot(None))
            .order_by(ContractTalk.id)))
        next_cw = self.cw + 1
        for talk in rows:
            player = self.db.get(Player, talk.player_id)
            reason = _talk_invalid_reason(talk, player)
            if reason:
                self._set_talk(talk, contracts.VOIDED, reason)
                self._note(talk.human_team_id, reason)
                continue
            if talk.kind == contracts.KIND_PRE_CONTRACT and talk.status == contracts.AGREED:
                continue
            if talk.expires_career_week is not None and int(talk.expires_career_week) <= next_cw:
                text = f"Sözleşme görüşmesinin süresi doldu ({player.name if player else 'Oyuncu'})."
                self._set_talk(talk, contracts.EXPIRED, text)
                self._note(talk.human_team_id, text)

    # ------------------------------------------------------------------ sezon devri
    def before_rollover(self) -> None:
        """Yas / sozlesme dususunden ONCE: AI'nin karar vermedigi (gec gelen, eski kayit) oyuncular icin kararlar."""
        def body() -> None:
            self._read_talks()
            self.db.flush()
            expiring = set(self.db.scalars(select(Player.id).where(
                Player.team_id.isnot(None), Player.team_id.notin_(sorted(self.humans) or [-1]),
                Player.in_academy.is_(False), Player.contract_years <= 1, Player.loan_from_team_id.is_(None))))
            if not expiring - self.decided - set(self.pre) - self.live:
                return                                  # olagan sezon: kararlarin hepsi verilmis (tam okuma yok)
            self._load(Team.academy_players, Team.loaned_out_players, Team.staff)
            self._ai_renewals(10 ** 6, catch_up=True)
            self.db.flush()
        self._guarded("contracts_before_rollover", body)

    def after_decrement(self, new_season: int, notes_by_team: dict[int, list[str]]) -> list[str]:
        """
        Dususten sonra (yeni sezonun basi): on sozlesmeler uygulanir (BOSMAN), kadro guvencesi (SAFETY_SQUAD / 2
        kaleci: en iyi bitenler 1 yil uzar), kalan suresi biten A takim oyunculari serbest kalir (RELEASED), canli
        gorusmeler kapanir. Insan kulubunun oyunculari ancak donem uyarisini almis bir sezonda serbest kalir.
        Donus: odak kulubun notlari.
        """
        focus = self.cm._acting_team_id()
        notes: list[str] = []

        def note(team_id: int | None, text: str) -> None:
            if team_id is None or team_id not in self.humans:
                return
            notes_by_team.setdefault(team_id, []).append(text)
            if team_id == focus:
                notes.append(text)

        def body() -> None:
            st = self.cm.state
            first = st.contracts_since_cw is None       # dongu bu devirde ilk kez: menajer uyarilmadi
            if first:
                st.contracts_since_cw = self.cw
            warned = not first and int(st.contracts_since_cw) <= int(st.career_week_offset or 0) + self.start
            self._norm_from_db()
            self._read_talks()
            self._execute_pre_contracts(new_season, note)
            self._release_expired(new_season, warned, note)
            self._close_old_talks()
            self.db.flush()
        if not self._guarded("contracts_rollover", body):
            notes.clear()
        self.cm._contract_holds = None
        return notes

    def _execute_pre_contracts(self, new_season: int, note) -> None:
        rows = list(self.db.scalars(select(ContractTalk).where(
            ContractTalk.kind == contracts.KIND_PRE_CONTRACT, ContractTalk.status == contracts.AGREED)
            .order_by(ContractTalk.id)))
        moved: list[tuple[Player, Team, Team]] = []
        # toplu okuma + guclu referans (kimlik haritasi zayif: tek tek get SELECT atardi)
        players = {p.id: p for p in self.db.scalars(select(Player).where(
            Player.id.in_(sorted({t.player_id for t in rows}) or [-1])))}
        teams = {t.id: t for t in self.db.scalars(select(Team).where(Team.id.in_(
            sorted({i for t in rows for i in (t.team_id, t.from_team_id) if i is not None}) or [-1])))}
        for talk in rows:
            player = players.get(talk.player_id)
            buyer = teams.get(talk.team_id)
            source = teams.get(talk.from_team_id)
            contract = _talk_contract(talk)
            if player is None or buyer is None or source is None or contract is None or \
                    player.team_id != source.id:
                self._set_talk(talk, contracts.VOIDED, "Ön sözleşme uygulanamadı: oyuncu artık kulübünde değil.")
                continue
            if buyer.id in self.humans:                            # AI kulubunun maas acigi hazirlik doneminde kapanir
                self._make_room(buyer, int(contract.wage))
            try:                                                  # dogrulama degisiklikten ONCE (savepoint yok)
                self.cm.sign_free_agent(buyer, player, contract, years=int(contract.years), from_team=source,
                                        kind=TransferKind.BOSMAN.value, season=new_season, week=1, flush=False)
            except TransferError as exc:
                log.warning("Ön sözleşme uygulanamadı (%s): %s", talk.id, exc)
                continue
            if buyer.id in self.humans:
                player.contract_clauses = self._clauses(talk, contract)
                self._pay_now(talk, buyer, player, contract)
            self._set_talk(talk, contracts.SIGNED)
            moved.append((player, source, buyer))
            for team, other, verb in ((buyer, source, "katıldı"), (source, buyer, "ayrıldı")):
                note(team.id, f"Ön sözleşme: {player.name} bedelsiz {verb} ({other.name}; "
                              f"{_money(contract.wage)}/hafta, {contract.years} yıl).")
        for player, source, buyer in sorted(moved, key=lambda m: (-int(m[0].market_value or 0), m[0].id))[:5]:
            if buyer.id in self.humans or source.id in self.humans:
                continue                                        # insan kulubu: _record_player_move haberi yazdi
            self.cm._add_news(NewsKind.TRANSFER, f"Transfer: {player.name}, sözleşmesi biten oyuncu olarak "
                                                 f"{source.name} kulübünden {buyer.name} kulübüne bedelsiz katıldı.",
                              team_id=buyer.id, other_team_id=source.id, week=1, season=new_season)
        self._squads = {}
        self._teams = None

    def _release_expired(self, new_season: int, warned: bool, note) -> None:
        self.db.flush()
        expired = list(self.db.scalars(select(Player).where(
            Player.team_id.isnot(None), Player.in_academy.is_(False), Player.contract_years <= 0,
            Player.loan_from_team_id.is_(None)).order_by(Player.team_id, Player.id)))
        if not expired:
            return
        counts: dict[int, list[int]] = {}
        for team_id, position, n in self.db.execute(select(Player.team_id, Player.position, func.count()).where(
                Player.team_id.isnot(None), Player.in_academy.is_(False), Player.contract_years >= 1)
                .group_by(Player.team_id, Player.position)):
            row = counts.setdefault(team_id, [0, 0])
            row[0] += int(n)
            if position is Position.GK:
                row[1] += int(n)
        by_team: dict[int, list[Player]] = {}
        for p in expired:
            by_team.setdefault(p.team_id, []).append(p)
        released: list[tuple[Player, Team]] = []
        safety = self._safety()
        teams = {t.id: t for t in self.db.scalars(select(Team).where(Team.id.in_(sorted(by_team))))}
        for team_id in sorted(by_team):
            team = teams.get(team_id)
            human = team_id in self.humans
            if team is None or (human and not warned):
                continue
            size, keepers = counts.get(team_id, [0, 0])
            ranked = sorted(by_team[team_id], key=lambda p: (-int(p.overall_rating), p.id))
            extend: list[Player] = []
            for p in (q for q in ranked if q.position is Position.GK):
                if keepers + sum(1 for q in extend if q.position is Position.GK) >= contracts.MIN_KEEPERS:
                    break
                extend.append(p)
            for p in ranked:
                if size + len(extend) >= safety:
                    break
                if p not in extend:
                    extend.append(p)
            for p in extend:
                p.contract_years = 1
                note(team_id, f"Kadro güvencesi: {p.name} ile sözleşme 1 yıl uzatıldı (kadro {safety} "
                              f"oyuncunun ya da {contracts.MIN_KEEPERS} kalecinin altına düşecekti).")
            leaving = [p for p in ranked if p not in extend]
            for p in leaving:
                self.cm.release_player(p, TransferKind.RELEASED.value, season=new_season, week=1, news=human,
                                       flush=False)
                released.append((p, team))
            if leaving:
                names = ", ".join(p.name for p in leaving[:10]) + (" ..." if len(leaving) > 10 else "")
                note(team_id, f"Sözleşmesi biten {len(leaving)} oyuncu serbest kaldı: {names}.")
        self.db.flush()                                         # toplu yazim; sonra iliskiler tazelenir
        for p, team in released:
            self.db.expire(p, ["team"])
            self.db.expire(team, ["players", "academy_players"])
        ai = [(p, t) for p, t in released if t.id not in self.humans]
        if ai:
            top = sorted(ai, key=lambda pt: (-int(pt[0].market_value or 0), pt[0].id))[:RELEASE_NEWS_TOP]
            self.cm._add_news(NewsKind.CONTRACT, f"Sözleşmesi biten {len(released)} oyuncu serbest kaldı; en "
                                                 f"değerlileri: " + ", ".join(f"{p.name} ({t.name})" for p, t in top)
                              + ".", week=1, season=new_season)

    def _close_old_talks(self) -> None:
        self.db.flush()
        rows = list(self.db.scalars(select(ContractTalk).where(
            ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES),
            or_(ContractTalk.kind != contracts.KIND_PRE_CONTRACT, ContractTalk.status == contracts.OPEN))
            .order_by(ContractTalk.id)))
        for talk in rows:
            self._set_talk(talk, contracts.EXPIRED, "Sezon bitti; görüşme kapandı.")

    def preseason(self, new_season: int, notes_by_team: dict[int, list[str]]) -> list[str]:
        """
        Akademi yonetiminden sonra: AI kulupleri havuzdan kadrosunu tamamlar (once mevki asgarisi, sonra SQUAD_TARGET;
        kulup basina en fazla PRESEASON_SIGNINGS_PER_CLUB), hala SQUAD_MIN alti -> akademiden yukseltme, kalecisi 2'nin
        altinda -> akademiden kaleci ya da acil imza. Insan kulubu imzalamaz (menajer karar verir); yalnizca A takimi
        SAFETY_SQUAD'in altina dustuyse asistan akademiden yukseltir (not). Donus: odak kulubun notlari.
        """
        focus = self.cm._acting_team_id()
        notes: list[str] = []

        def note(team_id: int | None, text: str) -> None:
            if team_id is None or team_id not in self.humans:
                return
            notes_by_team.setdefault(team_id, []).append(text)
            if team_id == focus:
                notes.append(text)

        def body() -> None:
            pool = self._free_agent_pool()
            self._load(Team.academy_players, Team.loaned_out_players, Team.staff)   # maas alani: tek seferde
            for club in self._ai_clubs():               # on sozlesmelerle gelen maas acigi butce kaydirmayla kapanir
                self._make_room(club, 0)
            self._read_talks()
            self._preseason_signings(new_season, pool)
            self._fallbacks(new_season, note)
            self.db.flush()
        if not self._guarded("contracts_preseason", body):
            notes.clear()
        return notes

    def _preseason_signings(self, new_season: int, pool: list[Player]) -> None:
        if not pool:
            return
        rng = _rng("preseason-clubs", new_season)
        clubs = self._ai_clubs()
        rng.shuffle(clubs)
        clubs.sort(key=lambda t: (0 if self._shape(t.id, current=True).urgent_positions() else 1,
                                  self._shape(t.id, current=True).size))
        signed = []
        for club in clubs:
            for _ in range(PRESEASON_SIGNINGS_PER_CLUB):
                if not pool:
                    break
                shape = self._shape(club.id, current=True)
                if shape.urgent_positions():
                    mode = "urgent"
                elif shape.size < self._target():
                    mode = "size"
                else:
                    break
                news = self._sign_best(club, shape, pool, mode, rng, season=new_season, week=1)
                if news is None:
                    break
                signed.append(news)
        for n in sorted(signed, key=lambda n: (-int(n.wage), n.player_id or 0))[:CONTRACT_NEWS_PER_WEEK]:
            self.cm._add_news(NewsKind.TRANSFER, self.cm._transfer_news_text(n), team_id=n.to_team_id, week=1,
                              season=new_season)

    def _fallbacks(self, new_season: int | None, note, *, humans: bool = True) -> None:
        """
        Kadro tabani: AI kulubu SQUAD_MIN (insan: SAFETY_SQUAD) ya da 2 kalecinin altindaysa akademiden en gucluler
        yukselir; AI kulubunde kaleci hala eksikse havuzdan acil kaleci. new_season None: sezon ici (haftalik adim).
        """
        from career_manager import SENIOR_SQUAD_MAX

        for _tid, club in sorted(self._teams.items()):
            human = club.id in self.humans
            if (club.id in self.protected and not human) or (human and not humans):
                continue
            floor = self._safety() if human else self._floor()
            shape = self._shape(club.id, current=True)
            if shape.size >= floor and shape.counts.get(Position.GK, 0) >= contracts.MIN_KEEPERS:
                continue
            academy = sorted(self.cm.academy_players(club), key=lambda p: (-int(p.overall_rating), p.id))
            keepers = shape.counts.get(Position.GK, 0)
            size = shape.size
            promoted = []
            for p in [a for a in academy if a.position is Position.GK]:
                if keepers >= contracts.MIN_KEEPERS or size >= SENIOR_SQUAD_MAX:
                    break
                promoted.append(p)
                keepers += 1
                size += 1
            for p in academy:
                if size >= floor or size >= SENIOR_SQUAD_MAX:
                    break
                if p not in promoted:
                    promoted.append(p)
                    size += 1
            for p in promoted:
                p.in_academy = False
                p.lineup_status, p.lineup_role = LineupStatus.BENCH, None
            if promoted:
                self.cm._refresh_squads(club)
                self._squads[club.id] = list(club.players)
                self._changed(club.id)
                if note is not None:
                    note(club.id, f"A takım {floor} oyuncunun altına düştü; asistan akademiden yükseltti: "
                                  + ", ".join(p.name for p in promoted) + ".")
            if keepers < contracts.MIN_KEEPERS and not human:
                self._emergency_keeper(club, new_season)

    def _emergency_keeper(self, club: Team, new_season: int | None) -> None:
        pool = [p for p in self._free_agent_pool() if p.position is Position.GK]
        if not pool:
            log.warning("Kalecisiz kulüp (%s): havuzda kaleci yok", club.name)
            return
        pool.sort(key=lambda p: (-int(p.overall_rating), p.id))
        p = pool[0]
        wage = int(round(finance.expected_wage(int(p.overall_rating), int(club.reputation), SquadRole.BACKUP)
                         * EMERGENCY_WAGE_SHARE / 100) * 100)
        self._make_room(club, wage)
        self._sign(club, p, ContractOffer(wage=wage, years=1, role=SquadRole.BACKUP), pool, season=new_season,
                   week=1 if new_season is not None else None)


def _without(pairs: list[tuple[Position, int]], player: Player) -> contracts.SquadShape:
    """(mevki, guc) listesinden oyuncunun bir kaydi cikarilmis kadro ozeti (listede yoksa aynen)."""
    out = list(pairs)
    key = (player.position, int(player.overall_rating))
    if key in out:
        out.remove(key)
    return contracts.SquadShape.of(out)


def _talk_invalid_reason(talk: ContractTalk, player: Player | None) -> str | None:
    """Canli gorusme hala gecerli mi? (oyuncu kulup degistirdi / imzaladi)."""
    if player is None:
        return PLAYER_NOT_FOUND_TEXT
    if talk.kind == contracts.KIND_RENEWAL and player.team_id != talk.team_id:
        return f"{player.name} artık kulübünde değil; yenileme görüşmesi geçersiz."
    if talk.kind == contracts.KIND_FREE_AGENT and talk.status in contracts.LIVE_TALK_STATUSES \
            and player.team_id is not None:
        return f"{player.name} başka bir kulüple anlaştı; görüşme geçersiz."
    if talk.kind == contracts.KIND_PRE_CONTRACT and player.team_id != talk.from_team_id:
        return f"{player.name} kulüp değiştirdi; ön sözleşme geçersiz."
    return None


class ContractDesk(_ContractBase):
    """
    Menajerin sozlesme masasi (cm.user_team). Commit ETMEZ; hatalar DeskError (Turkce). Donusler duz gorunumler.
        contract_window()                         on sozlesme takvimi
        contracts(expiring_only=False)            Sozlesmeler ekrani (A takim + akademi): durum, bitis sezonu, maas,
                                                  yenileme / fesih eylemleri, fesih bedeli
        open_renewal(pid) -> ContractStep         kendi oyuncunla yenileme masasi (oyuncu reddedebilir)
        free_agents(query, position, limit)       serbest oyuncu havuzu (sisli)
        open_free_agent(pid) -> ContractStep      serbest oyuncuyla masa
        pre_contract_targets(query, position, limit)
                                                  on sozlesme adaylari: AI kulubunde sozlesmesi biten (donem acikken)
        open_pre_contract(pid) -> ContractStep    on sozlesme masasi (kulup engelleyemez; oyuncu reddedebilir)
        submit(talk_id, ContractOffer, shift_wage_room=False) -> ContractStep
                                                  teklif; kabulde yenileme / serbest imza HEMEN imzalanir (maas alani
                                                  yetmezse AGREED kalir: needs_room), on sozlesme AGREED (devirde katilir)
        sign(talk_id, shift_wage_room=False)      imza bekleyen (AGREED) yenileme / serbest imza
        withdraw(talk_id), talk(talk_id), talks(open_only=True), terms_log(talk_id)
        termination_quote(pid), terminate(pid)    fesih: kalan maasin contracts.TERMINATION_SHARE'i tazminat
    """

    def _team(self) -> Team:
        team = self.desk._team()
        if not self.cm._contract_cycle_on():
            raise DeskError(CYCLE_OFF_TEXT)
        return team

    def _season_weeks_now(self) -> int:
        return self._season_weeks()

    # ------------------------------------------------------------------ takvim ve listeler
    def contract_window(self) -> ContractWindowView:
        sw = self._season_weeks()
        start = contracts.pre_contract_start(sw)
        opened = contracts.pre_contract_open(self.cm.current_week, sw, self.cm.season_finished)
        label = ("Ön sözleşme dönemi açık: sözleşmesi biten oyuncular başka kulüplerle bedelsiz anlaşabilir."
                 if opened else f"Ön sözleşme dönemi {start}. haftada açılır.")
        return ContractWindowView(opened, start, sw, label)

    def _talk_maps(self, team: Team) -> tuple[dict, dict, dict]:
        """(canli gorusmeler: oyuncu -> talk, ayrilanlar: oyuncu -> alici adi, son biten yenileme: oyuncu -> talk)."""
        self.db.flush()
        rows = list(self.db.scalars(select(ContractTalk).where(
            or_(ContractTalk.team_id == team.id, ContractTalk.from_team_id == team.id),
            or_(ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES), ContractTalk.season == int(self.cm.season)))
            .order_by(ContractTalk.id)))
        live, leaving, ended = {}, {}, {}
        for talk in rows:
            if talk.kind == contracts.KIND_PRE_CONTRACT and talk.status == contracts.AGREED and \
                    talk.from_team_id == team.id:
                leaving[talk.player_id] = self.desk._team_name(talk.team_id) or "başka kulüp"
            elif talk.status in contracts.LIVE_TALK_STATUSES and talk.team_id == team.id:
                live[talk.player_id] = talk
            elif talk.kind == contracts.KIND_RENEWAL and talk.team_id == team.id and \
                    talk.status in (contracts.COLLAPSED, contracts.REFUSED):
                ended[talk.player_id] = talk
        return live, leaving, ended

    def _cooldown(self, talk: ContractTalk | None) -> int:
        if talk is None:
            return 0
        return max(0, int(talk.updated_career_week) + contracts.TALK_RETRY_WEEKS - self.cw)

    def contracts(self, expiring_only: bool = False) -> list[ContractRow]:
        team = self._team()
        self.db.flush()
        players = list(self.db.scalars(select(Player).where(Player.team_id == team.id)
                                       .order_by(Player.in_academy, Player.overall_rating.desc(), Player.id)))
        live, leaving, ended = self._talk_maps(team)
        sw, week, finished = self._season_weeks(), int(self.cm.current_week), bool(self.cm.season_finished)
        season = int(self.cm.season)
        rows: list[ContractRow] = []
        for p in players:
            ending = contracts.expiring(p.contract_years)
            if expiring_only and not ending:
                continue
            talk = live.get(p.id)
            if p.id in leaving:
                status = contracts.ROW_LEAVING
            elif talk is not None:
                status = contracts.ROW_AGREED if talk.status == contracts.AGREED else contracts.ROW_TALKS
            elif ending and p.id in ended and self._cooldown(ended[p.id]) > 0:
                status = contracts.ROW_REFUSED
            elif ending:
                status = contracts.ROW_EXPIRING
            else:
                status = contracts.ROW_UNDER_CONTRACT
            loaned = p.loan_from_team_id is not None
            attitude = None if loaned or p.id in leaving else self._attitude(team, p)
            reason = ""
            if loaned:
                reason = "Kiralık oyuncu: sözleşmesi ana kulübünde."
            elif p.id in leaving:
                reason = LEAVING_TEXT.format(name=p.name, team=leaving[p.id])
            elif status == contracts.ROW_REFUSED:
                reason = COOLDOWN_TEXT.format(name=p.name, weeks=self._cooldown(ended[p.id]))
            can_renew = not loaned and p.id not in leaving and status != contracts.ROW_REFUSED
            cost = None if loaned or p.id in leaving else contracts.termination_compensation(
                int(p.current_wage or 0), int(p.contract_years or 0), week, sw, finished)
            rows.append(ContractRow(
                player_id=p.id, name=p.name, age=int(p.age), position=_ev(p.position), in_academy=bool(p.in_academy),
                wage=int(p.current_wage or 0), contract_years=int(p.contract_years or 0),
                expires_season=contracts.expiry_season(season, p.contract_years), expiring=ending, status=status,
                status_label=contracts.ROW_LABELS[status], talk_id=talk.id if talk is not None else None,
                other_team=leaving.get(p.id), wage_demand=int(p.wage_demand) if p.wage_demand is not None else None,
                attitude_label=attitude.label if attitude is not None else None, can_renew=can_renew,
                can_terminate=cost is not None, termination_cost=cost, reason=reason))
        return rows

    def _fog(self, team: Team, player: Player) -> tuple[int, staff_rules.ScoutedValue | None,
                                                         staff_rules.ScoutedValue | None]:
        k = self.desk.knowledge_of(team, player)
        margin = rules.knowledge_margin(self.cm.scout_margin(team), k)
        if margin is None:
            return k, None, None
        seed = (self.cm.scout_rating(team) or 0, player.id)
        return (k, staff_rules.scouted_value(player.overall_rating, margin, (*seed, "overall_rating")),
                staff_rules.scouted_money(int(player.market_value or 0), margin, (*seed, "value")))

    def free_agents(self, query: str = "", position: str | None = None, limit: int = 50) -> list[FreeAgentRow]:
        """Serbest oyuncu havuzu: en degerliler once (sisli guc / deger), onceki kulubu ve issizlik suresi."""
        team = self._team()
        self.db.flush()
        stmt = select(Player).where(Player.team_id.is_(None))
        if query and query.strip():
            stmt = stmt.where(Player.name.ilike(f"%{query.strip()}%"))
        if position:
            stmt = stmt.where(Player.position == Position(_ev(position)))
        players = list(self.db.scalars(stmt.order_by(Player.market_value.desc(), Player.id)
                                       .limit(max(1, min(200, int(limit))))))
        if not players:
            return []
        ids = [p.id for p in players]
        last = dict(self.db.execute(select(TransferLog.player_id, func.max(TransferLog.id)).where(
            TransferLog.player_id.in_(ids), TransferLog.to_team_id.is_(None)).group_by(TransferLog.player_id)).all())
        clubs = dict(self.db.execute(select(TransferLog.id, TransferLog.from_team_name).where(
            TransferLog.id.in_(list(last.values()) or [-1]))).all())
        live, _leaving, _ended = self._talk_maps(team)
        seniors = len(team.players)
        from career_manager import SENIOR_SQUAD_MAX
        rows = []
        for p in players:
            k, overall, value = self._fog(team, p)
            talk = live.get(p.id)
            full = seniors >= SENIOR_SQUAD_MAX
            rows.append(FreeAgentRow(
                player_id=p.id, name=p.name, age=int(p.age), position=_ev(p.position),
                previous_club=clubs.get(last.get(p.id)), weeks_free=contracts.weeks_free(p.free_agent_since, self.cw),
                knowledge=k, knowledge_label=rules.knowledge_label(k), overall=overall, value=value,
                talk_id=talk.id if talk is not None else None, can_approach=not full,
                reason=SQUAD_FULL_TEXT.format(limit=SENIOR_SQUAD_MAX) if full else ""))
        return rows

    def pre_contract_targets(self, query: str = "", position: str | None = None,
                             limit: int = 50) -> list[PreContractRow]:
        """On sozlesme adaylari: AI kuluplerinde sozlesmesi bu sezon biten A takim oyunculari (sisli)."""
        team = self._team()
        self.db.flush()
        humans = sorted(self._humans())
        holds = self.cm._contract_hold_map()
        stmt = (select(Player).where(Player.team_id.isnot(None), Player.team_id.notin_(humans or [-1]),
                                     Player.in_academy.is_(False), Player.contract_years <= 1,
                                     Player.loan_from_team_id.is_(None)))
        if query and query.strip():
            stmt = stmt.where(Player.name.ilike(f"%{query.strip()}%"))
        if position:
            stmt = stmt.where(Player.position == Position(_ev(position)))
        players = [p for p in self.db.scalars(stmt.order_by(Player.market_value.desc(), Player.id)
                                              .limit(max(1, min(200, int(limit)) * 2))) if p.id not in holds]
        window = self.contract_window()
        live, _leaving, _ended = self._talk_maps(team)
        rows = []
        for p in players[:max(1, min(200, int(limit)))]:
            k, overall, value = self._fog(team, p)
            talk = live.get(p.id)
            reason = ""
            if not window.pre_contract_open:
                reason = WINDOW_TEXT.format(week=window.opens_week)
            elif k < rules.KNOWN_THRESHOLD:
                reason = UNKNOWN_TEXT.format(name=p.name, k=k, need=rules.KNOWN_THRESHOLD)
            rows.append(PreContractRow(
                player_id=p.id, name=p.name, age=int(p.age), position=_ev(p.position), team_id=int(p.team_id),
                team=self.desk._team_name(p.team_id) or "", knowledge=k, knowledge_label=rules.knowledge_label(k),
                overall=overall, value=value, talk_id=talk.id if talk is not None else None,
                can_approach=not reason, reason=reason))
        return rows

    # ------------------------------------------------------------------ masa acma
    def _live_talk(self, player_id: int, team_id: int) -> ContractTalk | None:
        self.db.flush()
        return self.db.scalar(select(ContractTalk).where(
            ContractTalk.player_id == player_id, ContractTalk.team_id == team_id,
            ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES)).with_for_update()
            .execution_options(populate_existing=True))

    def _last_ended(self, player_id: int, team_id: int, kind: str) -> ContractTalk | None:
        return self.db.scalar(select(ContractTalk).where(
            ContractTalk.player_id == player_id, ContractTalk.team_id == team_id, ContractTalk.kind == kind,
            ContractTalk.status.in_((contracts.COLLAPSED, contracts.REFUSED)))
            .order_by(ContractTalk.id.desc()).limit(1))

    def _open(self, *, kind: str, player: Player, team: Team, from_team_id: int | None, snapshot: dict) -> ContractStep:
        existing = self._live_talk(player.id, team.id)
        if existing is not None:
            if existing.kind != kind:
                raise DeskError(f"{player.name} ile açık bir {contracts.KIND_LABELS[existing.kind].lower()} "
                                f"görüşmesi var.")
            negotiation, last = _talk_replay(existing)
            return self._step(existing, negotiation, last)
        wait = self._cooldown(self._last_ended(player.id, team.id, kind))
        if wait > 0:
            raise DeskError(COOLDOWN_TEXT.format(name=player.name, weeks=wait))
        try:
            with self.db.begin_nested():
                talk = self._new_talk(kind=kind, status=contracts.OPEN, player=player, team=team,
                                      from_team_id=from_team_id, human_team_id=team.id,
                                      expires=self.cw + contracts.TALK_VALID_WEEKS,
                                      history=[{"cw": self.cw, **snapshot}])
                self.db.flush()
        except IntegrityError:
            raise DeskError("Görüşme bu sırada güncellendi; tekrar dene.") from None
        negotiation, last = _talk_replay(talk)
        if not negotiation.open:
            self._set_talk(talk, contracts.COLLAPSED, negotiation.opening_message)
        self.db.flush()
        return self._step(talk, negotiation, last)

    def _snapshot(self, *, player_view: dict, buyer: Team, ratings: list[int], multiplier: float,
                  free_signing: bool, interest: str, refusal: str | None) -> dict:
        return {"kind": "terms_open", "player": player_view, "buyer": {"rep": int(buyer.reputation),
                                                                       "ratings": [int(r) for r in ratings]},
                "manager_rep": float(self.cm.manager_reputation_for(buyer)), "fee": 0, "multiplier": float(multiplier),
                "free_signing": bool(free_signing), "interest": interest, "refusal": refusal}

    def _ratings(self, team: Team, exclude: int | None = None) -> list[int]:
        self.db.flush()
        return [int(r) for r in self.db.scalars(select(Player.overall_rating).where(
            Player.team_id == team.id, Player.in_academy.is_(False), Player.id != (exclude or -1))
            .order_by(Player.overall_rating.desc(), Player.id))]

    def _own(self, team: Team, player_id) -> Player:
        player = self.desk._player(player_id)
        if player.team_id != team.id:
            raise DeskError(NOT_YOUR_PLAYER_TEXT.format(name=player.name))
        return player

    def _leaving_to(self, player: Player) -> str | None:
        return self.cm._contract_hold_map().get(player.id)

    def open_renewal(self, player_id: int) -> ContractStep:
        """Kendi oyuncunla yeni sozlesme masasi. Oyuncu ayrilmak istiyorsa / kulubu astiysa masaya oturmaz."""
        team = self._team()
        player = self._own(team, player_id)
        if player.loan_from_team_id is not None:
            raise DeskError(f"{player.name} kiralık oyuncu; sözleşmesi ana kulübünde.")
        hold = self._leaving_to(player)
        if hold:
            raise DeskError(f"{player.name}: {hold}.")
        attitude = self._attitude(team, player)
        ratings = self._ratings(team, exclude=player.id)
        proxy, _buyer = self._renewal_proxy(team, player, ratings)
        view = {"name": player.name, "overall": int(player.overall_rating), "age": int(player.age),
                "wage": int(proxy.current_wage), "position": _ev(player.position),
                "value": int(player.market_value or 0), "team_rep": int(team.reputation)}
        refusal = f"{player.name}: \"{attitude.reason}\"" if attitude.refuses else None
        return self._open(kind=contracts.KIND_RENEWAL, player=player, team=team, from_team_id=team.id,
                          snapshot=self._snapshot(player_view=view, buyer=team, ratings=ratings,
                                                  multiplier=attitude.wage_multiplier, free_signing=False,
                                                  interest=attitude.label, refusal=refusal))

    def open_free_agent(self, player_id: int) -> ContractStep:
        """Serbest oyuncuyla masa: issizlik suresi beklentisini (prestij, rol, maas) dusurur. Bonservis yok."""
        from career_manager import SENIOR_SQUAD_MAX

        team = self._team()
        player = self.desk._player(player_id)
        if player.team_id is not None:
            raise DeskError(NOT_FREE_TEXT.format(name=player.name))
        if len(team.players) >= SENIOR_SQUAD_MAX:
            raise DeskError(SQUAD_FULL_TEXT.format(limit=SENIOR_SQUAD_MAX))
        weeks = contracts.weeks_free(player.free_agent_since, self.cw)
        expect = contracts.expectation_overall(player.overall_rating, weeks)
        ratings = self._ratings(team)
        refusal = None
        level = squad_level_refusal(expect, ratings)
        if level:
            refusal = f"{player.name}: \"{level}\" — sözleşme masasına oturmadı."
        view = {"name": player.name, "overall": int(expect), "age": int(player.age), "wage": 0,
                "position": _ev(player.position), "value": int(player.market_value or 0),
                "team_rep": int(team.reputation)}
        return self._open(kind=contracts.KIND_FREE_AGENT, player=player, team=team, from_team_id=None,
                          snapshot=self._snapshot(player_view=view, buyer=team, ratings=ratings,
                                                  multiplier=contracts.free_agent_wage_factor(weeks),
                                                  free_signing=True, interest="Serbest oyuncu", refusal=refusal))

    def open_pre_contract(self, player_id: int) -> ContractStep:
        """
        On sozlesme masasi: AI kulubunde sozlesmesi bu sezon biten oyuncu, donem acikken (sezonun ikinci yarisi).
        Kulup engelleyemez; oyuncunun istekliligi (transfer_rules.player_interest; sozlesmesi bitiyor +) ve kadro
        seviyesi. Anlasma sezon devrinde uygulanir.
        """
        team = self._team()
        player = self.desk._player(player_id)
        if player.team_id == team.id:
            raise DeskError(OWN_PLAYER_TEXT.format(name=player.name))
        seller = self.db.get(Team, player.team_id) if player.team_id is not None else None
        if seller is None:
            raise DeskError(NO_CLUB_TEXT.format(name=player.name))
        if seller.id in self._humans():
            raise DeskError(HUMAN_SELLER_TEXT.format(team=seller.name, name=player.name))
        if player.in_academy:
            raise DeskError(ACADEMY_TEXT.format(name=player.name, team=seller.name))
        if player.loan_from_team_id is not None:
            raise DeskError(f"{player.name} kiralık oyuncu; ön sözleşme yapılamaz.")
        if not contracts.expiring(player.contract_years):
            raise DeskError(NOT_EXPIRING_TEXT.format(name=player.name))
        window = self.contract_window()
        if not window.pre_contract_open:
            raise DeskError(WINDOW_TEXT.format(week=window.opens_week))
        hold = self._leaving_to(player)
        if hold:
            raise DeskError(f"{player.name}: {hold}.")
        k = self.desk.knowledge_of(team, player)
        if k < rules.KNOWN_THRESHOLD:
            raise DeskError(UNKNOWN_TEXT.format(name=player.name, k=k, need=rules.KNOWN_THRESHOLD))
        interest = self.desk._interest(player, team)
        ratings = self._ratings(team)
        refusal = None
        if interest.refuses:
            refusal = f"{player.name}: \"{interest.reason}\" — sözleşme masasına oturmadı."
        else:
            level = squad_level_refusal(player.overall_rating, ratings)
            if level:
                refusal = f"{player.name}: \"{level}\" — sözleşme masasına oturmadı."
        view = {"name": player.name, "overall": int(player.overall_rating), "age": int(player.age),
                "wage": int(player.current_wage or 0), "position": _ev(player.position),
                "value": int(player.market_value or 0), "team_rep": int(seller.reputation)}
        return self._open(kind=contracts.KIND_PRE_CONTRACT, player=player, team=team, from_team_id=seller.id,
                          snapshot=self._snapshot(player_view=view, buyer=team, ratings=ratings,
                                                  multiplier=interest.wage_multiplier, free_signing=True,
                                                  interest=interest.label, refusal=refusal))

    # ------------------------------------------------------------------ teklif ve imza
    def _lock_talk(self, talk_id) -> tuple[ContractTalk, Team]:
        team = self._team()
        if not _is_id(talk_id):
            raise DeskError(TALK_NOT_FOUND_TEXT)
        self.db.flush()
        talk = self.db.scalar(select(ContractTalk).where(ContractTalk.id == talk_id).with_for_update()
                              .execution_options(populate_existing=True))
        if talk is None or talk.human_team_id != team.id or talk.team_id != team.id:
            raise DeskError(TALK_NOT_FOUND_TEXT)
        return talk, team

    def _check_valid(self, talk: ContractTalk) -> Player:
        player = self.db.get(Player, talk.player_id)
        reason = _talk_invalid_reason(talk, player)
        if reason:
            self._set_talk(talk, contracts.VOIDED, reason)
            self.db.flush()
            raise DeskError(reason)
        return player

    def submit(self, talk_id: int, offer: ContractOffer, shift_wage_room: bool = False) -> ContractStep:
        """Oyuncuya / menajerine teklif. Kabulde yenileme ve serbest imza hemen imzalanir; on sozlesme AGREED olur."""
        contract = validate_contract(offer)
        talk, team = self._lock_talk(talk_id)
        if talk.status != contracts.OPEN:
            raise DeskError(TALK_CLOSED_TEXT.format(status=contracts.STATUS_LABELS.get(talk.status, talk.status)))
        player = self._check_valid(talk)
        negotiation, last = _talk_replay(talk)
        if negotiation is None:
            raise DeskError(TERMS_NOT_OPEN_TEXT)
        if not negotiation.open:
            return self._step(talk, negotiation, last)
        if talk.kind == contracts.KIND_RENEWAL and \
                contracts.renewal_contract_years(contract.years) < int(player.contract_years or 0):
            raise DeskError(SHORTER_TEXT.format(years=int(player.contract_years or 0)))
        if talk.kind != contracts.KIND_PRE_CONTRACT:
            cost = int(contract.signing_fee) + int(contract.agent_fee)
            if cost > int(team.transfer_budget):
                raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(cost)))
        response = negotiation.respond(contract)
        talk.history = [*(talk.history or []), {"cw": self.cw, "kind": "terms_bid", "offer": contract.to_dict()}]
        talk.updated_career_week = self.cw
        talk.updated_at = _now()
        if response.status is NegotiationStatus.WALKED_AWAY:
            self._set_talk(talk, contracts.COLLAPSED, response.message)
        elif response.status is NegotiationStatus.ACCEPTED:
            talk.contract = contract.to_dict()
            talk.status = contracts.AGREED
            if talk.kind == contracts.KIND_PRE_CONTRACT:
                talk.expires_career_week = None
                talk.effective_season = int(self.cm.season) + 1
                seller = self.db.get(Team, talk.from_team_id)
                self.cm._contract_holds = None
                self.desk._rumour(f"Ön sözleşme: {player.name} sezon sonunda {seller.name} kulübünden {team.name} "
                                  f"kulübüne bedelsiz geçecek.", team.id, seller.id)
            else:
                talk.expires_career_week = self.cw + contracts.TALK_VALID_WEEKS
                self._try_sign(talk, team, player, shift_wage_room, strict=False)
        self.db.flush()
        return self._step(talk, negotiation, response)

    def sign(self, talk_id: int, shift_wage_room: bool = False) -> ContractStep:
        """Anlasilmis (AGREED) yenileme / serbest imzayi tamamlar (maas alani yetmezse shift_wage_room)."""
        talk, team = self._lock_talk(talk_id)
        if talk.status != contracts.AGREED or talk.kind == contracts.KIND_PRE_CONTRACT:
            raise DeskError(NOT_AGREED_TEXT if talk.status != contracts.AGREED else
                            "Ön sözleşme sezon sonunda kendiliğinden uygulanır.")
        player = self._check_valid(talk)
        self._try_sign(talk, team, player, shift_wage_room, strict=True)
        self.db.flush()
        negotiation, last = _talk_replay(talk)
        return self._step(talk, negotiation, last)

    def _needs(self, talk: ContractTalk, team: Team, player: Player, contract: ContractOffer) -> int:
        current = int(player.current_wage or 0) if talk.kind == contracts.KIND_RENEWAL else 0
        return max(0, int(contract.wage) - current - int(team.free_wage))

    def _try_sign(self, talk: ContractTalk, team: Team, player: Player, shift: bool, *, strict: bool) -> None:
        from career_manager import SENIOR_SQUAD_MAX

        contract = _talk_contract(talk)
        if contract is None:
            raise DeskError(NOT_AGREED_TEXT)
        if talk.kind == contracts.KIND_FREE_AGENT and len(team.players) >= SENIOR_SQUAD_MAX:
            if strict:
                raise DeskError(SQUAD_FULL_TEXT.format(limit=SENIOR_SQUAD_MAX))
            return
        need = self._needs(talk, team, player, contract)
        if need > 0 and not shift:
            if strict:
                raise DeskError(WAGE_ROOM_TEXT.format(need=_money(need), cost=_money(finance.weekly_to_transfer(need))))
            return
        cost = int(contract.signing_fee) + int(contract.agent_fee) + (finance.weekly_to_transfer(need) if need else 0)
        if cost > int(team.transfer_budget):
            if strict:
                raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(cost)))
            return
        try:
            with self.db.begin_nested():
                if need > 0:
                    self.cm.shift_budget(team, need)
                if talk.kind == contracts.KIND_RENEWAL:
                    self._apply_renewal(player, contract)
                    player.release_clause = contract.release_clause
                    text = (f"{player.name} ile sözleşme yenilendi: {contract.years} yıl "
                            f"({_money(contract.wage)}/hafta).")
                    self.cm._add_news(NewsKind.CONTRACT, f"{team.name}, {player.name} ile sözleşmesini "
                                                         f"{contract.years} yıl uzattı.", team_id=team.id)
                else:
                    self.cm.sign_free_agent(team, player, contract)
                    text = f"{player.name} serbest oyuncu olarak imzaladı ({_money(contract.wage)}/hafta)."
                player.contract_clauses = self._clauses(talk, contract)
                self._pay_now(talk, team, player, contract)
                self._set_talk(talk, contracts.SIGNED)
                self.db.flush()
        except (TransferError, finance.BudgetError) as exc:
            raise DeskError(str(exc)) from exc
        self.desk._expire(team)
        self._note(team.id, text)

    def withdraw(self, talk_id: int) -> ContractTalkView:
        """Acik ya da imza bekleyen gorusmeden cekilir (anlasilmis on sozlesme baglayicidir)."""
        talk, _team = self._lock_talk(talk_id)
        if talk.status not in contracts.LIVE_TALK_STATUSES:
            raise DeskError(TALK_CLOSED_TEXT.format(status=contracts.STATUS_LABELS.get(talk.status, talk.status)))
        if talk.kind == contracts.KIND_PRE_CONTRACT and talk.status == contracts.AGREED:
            raise DeskError("Ön sözleşme imzalandı; geri çekilemez.")
        self._set_talk(talk, contracts.WITHDRAWN, "Menajer görüşmeden çekildi.")
        self.db.flush()
        return self._view(talk)

    # ------------------------------------------------------------------ gorunumler
    def _step(self, talk: ContractTalk, negotiation: ContractNegotiation | None, response) -> ContractStep:
        player = self.db.get(Player, talk.player_id)
        name = player.name if player is not None else "Oyuncu"
        agreed = _talk_contract(talk)
        cost = int(agreed.signing_fee) + int(agreed.agent_fee) if agreed is not None else 0
        if negotiation is None:
            return ContractStep(talk.id, talk.kind, NegotiationStatus.WALKED_AWAY, talk.status, talk.reason or "", (),
                                None, 0, "Görüşme yok", None, cost, talk.player_id, name)
        if response is None and not negotiation.open:
            return ContractStep(talk.id, talk.kind, negotiation.status, talk.status,
                                negotiation.opening_message or talk.reason or "", (), None, 0, "Görüşmeyi reddetti",
                                None, 0, talk.player_id, name)
        if response is None:
            message = f"{name} ve menajeri taleplerini açıkladı: {negotiation.demand.describe()}"
            complaints: tuple[str, ...] = ()
        else:
            message, complaints = response.message, tuple(response.complaints)
        if negotiation.status is NegotiationStatus.ACCEPTED:
            needs = None
            if talk.status == contracts.AGREED and talk.kind != contracts.KIND_PRE_CONTRACT and agreed is not None \
                    and player is not None:
                team = self.db.get(Team, talk.team_id)
                needs = self._needs(talk, team, player, agreed) or None if team is not None else None
            return ContractStep(talk.id, talk.kind, negotiation.status, talk.status, message, complaints,
                                agreed or negotiation.last_offer, negotiation.rounds_left, "Anlaştı", needs, cost,
                                talk.player_id, name)
        demand = negotiation.demand if negotiation.open else None
        mood = rules.terms_mood(negotiation.persuasion(negotiation.last_offer), negotiation.required_persuasion) \
            if negotiation.open and negotiation.last_offer is not None else \
            ("Görüşmeyi bitirdi" if not negotiation.open else "Talebini açıkladı")
        demand_cost = int(demand.signing_fee) + int(demand.agent_fee) if demand is not None else 0
        return ContractStep(talk.id, talk.kind, negotiation.status, talk.status, message, complaints, demand,
                            negotiation.rounds_left, mood, None, demand_cost, talk.player_id, name)

    def _history_lines(self, talk: ContractTalk) -> tuple[str, ...]:
        lines = []
        for e in talk.history or []:
            if not isinstance(e, dict):
                continue
            prefix = f"{e.get('cw', '?')}. hafta · "
            kind = e.get("kind")
            if kind == "terms_open":
                lines.append(prefix + (e.get("refusal") or "Sözleşme masası açıldı"))
            elif kind == "terms_bid":
                lines.append(prefix + f"Teklif: {ContractOffer.from_dict(e['offer']).describe()}")
            elif kind == "status":
                label = contracts.STATUS_LABELS.get(e.get("status"), e.get("status"))
                lines.append(prefix + label + (f": {e['reason']}" if e.get("reason") else ""))
        return tuple(lines)

    def _view(self, talk: ContractTalk) -> ContractTalkView:
        team = self.cm.user_team
        player = self.db.get(Player, talk.player_id)
        mine = team is not None and talk.human_team_id == team.id
        live = talk.status in contracts.LIVE_TALK_STATUSES
        if talk.kind == contracts.KIND_RENEWAL:
            direction = "OWN"
        else:
            direction = "IN" if team is not None and talk.team_id == team.id else "OUT"
        return ContractTalkView(
            id=talk.id, kind=talk.kind, kind_label=contracts.KIND_LABELS.get(talk.kind, talk.kind),
            status=talk.status, status_label=contracts.STATUS_LABELS.get(talk.status, talk.status),
            direction=direction, player_id=talk.player_id, player_name=player.name if player is not None else "Oyuncu",
            team_id=talk.team_id, team=self.desk._team_name(talk.team_id), from_team_id=talk.from_team_id,
            from_team=self.desk._team_name(talk.from_team_id), contract=_talk_contract(talk),
            effective_season=talk.effective_season, reason=talk.reason or "",
            expires_in_weeks=(max(0, int(talk.expires_career_week) - self.cw)
                              if live and talk.expires_career_week is not None else None),
            history=self._history_lines(talk),
            can_submit=bool(mine and direction != "OUT" and talk.status == contracts.OPEN),
            can_sign=bool(mine and direction != "OUT" and talk.status == contracts.AGREED
                          and talk.kind != contracts.KIND_PRE_CONTRACT),
            can_withdraw=bool(mine and direction != "OUT" and live and not (
                talk.kind == contracts.KIND_PRE_CONTRACT and talk.status == contracts.AGREED)))

    def talk(self, talk_id: int) -> ContractTalkView:
        team = self._team()
        talk = self.db.get(ContractTalk, talk_id) if _is_id(talk_id) else None
        if talk is None or talk.human_team_id != team.id:
            raise DeskError(TALK_NOT_FOUND_TEXT)
        return self._view(talk)

    def talks(self, open_only: bool = True, limit: int = 50) -> list[ContractTalkView]:
        """Kulubumun gorusmeleri (giden on sozlesmeler dahil): canli olanlar once, sonra en yeni."""
        team = self._team()
        self.db.flush()
        stmt = select(ContractTalk).where(ContractTalk.human_team_id == team.id)
        if open_only:
            stmt = stmt.where(ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES))
        rows = self.db.scalars(stmt.order_by(ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES).desc(),
                                             ContractTalk.id.desc()).limit(max(1, min(200, int(limit)))))
        return [self._view(t) for t in rows]

    def terms_log(self, talk_id: int) -> tuple[ContractStep, tuple[tuple[str, str], ...]]:
        """Masanin salt okunur goruntusu + konusma gecmisi (me / him / bad); hicbir sey yazmaz."""
        team = self._team()
        talk = self.db.get(ContractTalk, talk_id) if _is_id(talk_id) else None
        if talk is None or talk.human_team_id != team.id or talk.team_id != team.id:
            raise DeskError(TALK_NOT_FOUND_TEXT)
        history = talk.history or []
        start = max((i for i, e in enumerate(history) if isinstance(e, dict) and e.get("kind") == "terms_open"),
                    default=None)
        if start is None:
            return self._step(talk, None, None), ()
        negotiation = _talk_negotiation(talk, history[start])
        log_lines: list[tuple[str, str]] = []
        if negotiation.open:
            log_lines.append(("him", f"{negotiation.player.name} ve menajeri taleplerini açıkladı: "
                                     f"{negotiation.demand.describe()}"))
        else:
            log_lines.append(("bad", negotiation.opening_message or "Oyuncu görüşmeyi reddetti."))
        last = None
        for entry in history[start + 1:]:
            if not isinstance(entry, dict) or entry.get("kind") != "terms_bid" or not negotiation.open:
                continue
            offer = ContractOffer.from_dict(entry["offer"])
            log_lines.append(("me", f"Teklif: {offer.describe()}"))
            last = negotiation.respond(offer)
            log_lines.append(("bad" if last.status is NegotiationStatus.WALKED_AWAY else "him", last.message))
            log_lines.extend(("him", f"· {c}") for c in last.complaints)
        return self._step(talk, negotiation, last), tuple(log_lines)

    # ------------------------------------------------------------------ fesih
    def termination_quote(self, player_id: int) -> TerminationQuote:
        team = self._team()
        player = self._own(team, player_id)
        wage, years = int(player.current_wage or 0), int(player.contract_years or 0)
        sw, week, finished = self._season_weeks(), int(self.cm.current_week), bool(self.cm.season_finished)
        weeks = contracts.remaining_contract_weeks(years, week, sw, finished)
        cost = contracts.termination_compensation(wage, years, week, sw, finished)
        reason = ""
        if player.loan_from_team_id is not None:
            reason = f"{player.name} kiralık oyuncu; sözleşmesi ana kulübünde."
        elif self._leaving_to(player):
            reason = f"{player.name}: {self._leaving_to(player)}."
        elif not player.in_academy:
            others = [p for p in team.players if p.id != player.id]
            if len(others) < transfers.SQUAD_FLOOR:
                reason = SELLER_FLOOR_TEXT.format(floor=transfers.SQUAD_FLOOR)
            elif player.position is Position.GK and sum(1 for p in others if p.position is Position.GK) < \
                    contracts.MIN_KEEPERS:
                reason = KEEPER_FLOOR_TEXT.format(floor=contracts.MIN_KEEPERS)
        if not reason and cost > int(team.transfer_budget):
            reason = BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(cost))
        return TerminationQuote(player.id, player.name, wage, years, round(weeks, 1), cost, int(team.transfer_budget),
                                not reason, reason)

    def terminate(self, player_id: int) -> TerminationQuote:
        """Sozlesmeyi feshet: tazminat kasadan, oyuncu serbest kalir (transfer_log TERMINATED + haber)."""
        team = self._team()
        rows = self.cm.lock_rows(Player, [player_id]) if _is_id(player_id) else []
        if not rows:
            raise DeskError(PLAYER_NOT_FOUND_TEXT)
        quote = self.termination_quote(player_id)
        if not quote.can_terminate:
            raise DeskError(quote.reason)
        player = rows[0]
        try:
            with self.db.begin_nested():
                team.transfer_budget = int(team.transfer_budget) - int(quote.compensation)
                self.db.flush()
                for talk in self.db.scalars(select(ContractTalk).where(
                        ContractTalk.player_id == player.id,
                        ContractTalk.status.in_(contracts.LIVE_TALK_STATUSES)).order_by(ContractTalk.id)):
                    self._set_talk(talk, contracts.VOIDED, f"{player.name} ile sözleşme feshedildi.")
                self.cm.release_player(player, TransferKind.TERMINATED.value, news=True)
        except (TransferError, IntegrityError) as exc:
            raise DeskError(str(exc)) from exc
        self._note(team.id, f"{player.name} ile sözleşme feshedildi; tazminat {_money(quote.compensation)}.")
        self.db.flush()
        return TerminationQuote(quote.player_id, quote.name, quote.wage, quote.contract_years, quote.remaining_weeks,
                                quote.compensation, int(team.transfer_budget), False, "", True)


def run_contract_week(cm: CareerManager, week: int, report=None) -> list:
    """
    CareerManager._run_contract_week (bayrak acik, kariyer modu; masa adimindan sonra). AI yenileme kararlari, uyarilar,
    on sozlesme donemi, serbest oyuncu imzalari, gorusme sureleri. Donus: AI serbest oyuncu imzalari (TransferNews;
    haftalik rapora eklenir). Tek savepoint: hata loglanir, hafta ilerler.
    """
    if cm.game_mode is GameMode.TOURNAMENT:
        return []
    return ContractCycle(cm, report).run_week(int(week))


__all__ = [
    "DeskError", "DealSummary", "DealView", "FinanceSummary", "KnowledgeView", "PaymentView", "ScoutReportView",
    "TermsStep", "TermsTable", "TermsView", "TransferDesk", "WindowView", "run_week", "settle_sell_on",
    "validate_contract",
    "AddOn", "DealTerms",
    # 15A sozlesme dongusu
    "ContractCycle", "ContractDesk", "ContractRow", "ContractStep", "ContractTalkView", "ContractWindowView",
    "FreeAgentRow", "PreContractRow", "TerminationQuote", "run_contract_week",
]


# ===============================================================================================================
# 15F: CANLI PAZAR VE KIRALIK
#
# Kurallar SAF modullerde: transfer_rules bolum 12 (donem kotasi, agirlikli alici, kadro kapilari, sonraki satis
# payinin tabani, geri alim) ve loan_rules 15F bolumu (AI kiralik teklifi, opsiyonlu kiralik).
#
#   WorldMarket   dunya pazari (AI <-> AI): YALNIZ transfer doneminde, LIGLER ARASI, ihtiyaca gore; donem basina
#                 dunya capinda rules.window_deal_target kadar transfer, son hafta DEADLINE_BOOST kat ("son gun").
#                 Donem acilisinda AI kulupleri fazlalik / genc oyuncularini transfer ve kiralik listesine koyar
#                 (menajerin gordugu listeler), her hafta AI <-> AI kiraliklar ve geri alim maddeleri islenir,
#                 soylentiler ve son gun haberi yazilir. Bayrak rules.LIVE_MARKET.
#   LoanCycle     haftalik kiralik yasam dongusu: satin alma opsiyonlari (her dunyada), AI kuluplerinin menajerin
#                 oyuncusuna kiralik teklifi (bayrak), ve TEK OYUNCULU dunyada kiralik bitisi / AI geri cagirmasi /
#                 kaygi bildirimleri (paylasilan dunyada bunlari market_hub.MarketExtension kosar).
#   LoanDesk      menajerin kiralik API'si: listeler, kirala / kiraliga ver, gelen AI kiralik teklifleri,
#                 geri cagirma, opsiyon bilgisi. Kiralik dosyasi transfer_deals (kind = LOAN).
#
# Determinizm: cm.rng'den ASLA cekilmez; her adim kendi crc32 tohumundan (_rng). Para: kasa asla eksiye dusmez
# (rules.market_spend_cap + CareerManager.complete_transfer kontrolleri).
# ===============================================================================================================

LOAN_KIND, TRANSFER_KIND = "LOAN", "TRANSFER"
MARKET_MIN_FEE = 50_000                # bu tutarin altinda AI <-> AI transferi denenmez
MARKET_NEEDS_PER_TRY = 3               # bir denemede alicinin bakacagi mevki sayisi (en acil ihtiyactan baslar)
MARKET_TOP_PICKS = 3                   # her mevkide en yuksek puanli bu kadar aday arasindan secilir
MARKET_PICK_TRIES = 2                  # ... ve en fazla bu kadari denenir (pazarlik cogu zaman tutmaz)
MARKET_FAIL_DECAY = 0.7                # alamayan kulubun bu haftaki agirligi bu kadar duser
MARKET_RUMOUR_VALUE = 8_000_000        # bu degerin ustundeki oyuncu icin soylenti yazilir (her kulupte)
LOAN_OFFER_CHANCE = 0.8                # donem acikken haftalik: menajerin kulubune AI kiralik teklifi olasiligi
LOAN_OFFER_TRIES = 4                   # teklif icin en fazla bu kadar oyuncu denenir
LOAN_OFFER_CLUB_TRIES = 6              # her oyuncu icin en fazla bu kadar kiralayan aday kulup
LOAN_OFFER_VALID_WEEKS = 2             # menajer bu kadar hafta icinde yanit vermezse teklif duser
LOAN_OFFERS_OPEN_MAX = 3               # bir kulupte ayni anda en fazla bu kadar acik AI kiralik teklifi
AI_LOAN_PAIRS_PER_WEEK = 6             # AI <-> AI kiralik denemesi (haftalik, dunya capinda)
LOAN_LIST_LIMIT = 60

LOANS_OFF_TEXT = "Bu kariyerde kiralık sistemi kapalı."
LOAN_WINDOW_TEXT = "Kiralık yalnızca transfer dönemi açıkken yapılır."
LOAN_NOT_FOUND_TEXT = "Kiralık teklifi bulunamadı."
LOAN_OWN_TEXT = "{name} zaten senin takımında."
LOAN_ON_LOAN_TEXT = "{name} şu an kiralık; yeniden kiralanamaz."
LOAN_ACADEMY_TEXT = "{name} akademi oyuncusu; kiralık işlemleri A takım oyuncuları içindir."
LOAN_TARGET_TEXT = "Oyuncuyu kiralayacak kulübü seç."
LOAN_HUMAN_TEXT = "{team} bir menajerin kulübü; kiralık teklifini Teklifler panelinden yap."
LOAN_SQUAD_TEXT = "A takım kadron {floor} oyuncunun altına düşer."
LOAN_FULL_TEXT = "A takım kadron dolu (en fazla {limit} oyuncu)."
LOAN_WAGE_TEXT = "Maaş havuzunda yer yok: haftalık {need} gerekli, {have} boş."
BUY_BACK_NONE_TEXT = "{name} için geri alım maddesi yok ya da süresi doldu."
BUY_BACK_SQUAD_TEXT = "{team} kadrosu {floor} oyuncunun altına düşer; geri alım şu an yapılamaz."


@dataclass(frozen=True)
class ListingRow:
    """15F: AI kuluplerinin transfer / kiralik listesindeki oyuncu (K12: guc ve deger SISLI)."""
    player_id: int
    name: str
    position: str
    age: int
    team_id: int | None
    team_name: str | None
    league_name: str | None
    knowledge: int
    knowledge_label: str
    overall_text: str                 # "Bilinmiyor" ya da sisli aralik
    value_text: str
    asking_text: str | None           # istenen bedel (biliniyorsa)
    wage: int | None                  # 75+ bilgi: haftalik maas
    contract_years: int | None
    transfer_listed: bool
    loan_listed: bool


@dataclass(frozen=True)
class WorldMoveRow:
    """15F: dunyada tamamlanan transfer / kiralik (menajerin gordugu 'AI transfer listesi')."""
    season: int
    week: int
    player_id: int | None
    player_name: str
    from_team: str | None
    to_team: str | None
    fee: int
    fee_text: str
    kind: str
    kind_label: str


@dataclass(frozen=True)
class LoanTermsView:
    weeks: int | None
    weeks_text: str
    wage_share: int
    borrower_weekly: int
    parent_weekly: int
    fee: int
    option_fee: int | None
    option_mandatory: bool
    text: str


@dataclass(frozen=True)
class LoanOfferView:
    """15F: AI kulubunun menajerin oyuncusu icin kiralik teklifi (transfer_deals, kind = LOAN)."""
    deal_id: int
    status: str
    status_label: str
    player_id: int
    player_name: str
    position: str
    age: int
    club_id: int | None
    club_name: str | None
    terms: LoanTermsView
    message: str
    expires_in_weeks: int | None
    can_accept: bool
    can_reject: bool


@dataclass(frozen=True)
class BuyBackRow:
    """15F: kulubumun geri alim maddesi tasidigi, baska kulupte oynayan oyuncu."""
    player_id: int
    name: str
    position: str
    age: int
    team_id: int | None
    team_name: str | None
    fee: int
    fee_text: str
    until_season: int
    value_text: str
    affordable: bool
    reason: str


def position_floor_ok(team: Team, player: Player) -> bool:
    """
    Oyuncu kulupten ayrilirsa mevki tabani (transfers.POSITION_SALE_FLOOR: en az 2 kaleci) korunur mu?
    transfers.evaluate_fee bonservisli satista bunu zaten yapar; KIRALIKTA (AI <-> AI ve menajer) burada yapilir.
    """
    floor = transfers.POSITION_SALE_FLOOR.get(player.position)
    if floor is None:
        return True
    return sum(1 for p in team.players if p.id != player.id and p.position is player.position) >= floor


def live_market_on(cm) -> bool:
    """
    15F dunya pazari bayragi: kapaliyken WorldMarket hic kurulmaz (eski AI penceresi calisir).
    Tek dogru kaynak CareerManager._live_market_on (kopya basina self.live_market ezmesi oradadir).
    """
    return bool(cm._live_market_on())


def loans_on(cm) -> bool:
    """Kiralik acik mi (paylasilan dunyada kural, tek oyunculuda loan_rules.SOLO_LOANS)."""
    import market_hub

    return cm.game_mode is not GameMode.TOURNAMENT and market_hub.loans_enabled(cm.rules)


def _market_extension_active(cm) -> bool:
    """Kiralik yasam dongusunu market_hub.MarketExtension kosuyor mu (extensions.EXTENSIONS ile ayni kosul)?"""
    r = cm.rules
    return bool(getattr(r, "shared", False) and (getattr(r, "human_market", False) or getattr(r, "loans", False)))


def _loan_terms_view(player: Player, weeks: int | None, share: int, fee: int = 0,
                     option_fee: int | None = None, option_mandatory: bool = False) -> LoanTermsView:
    borrower, parent = loan_rules.wage_split(int(player.current_wage or 0), int(share))
    span = "sezon sonuna kadar" if weeks is None else f"{int(weeks)} hafta"
    text = f"{span} · maaşın %{int(share)} payı kiralayan kulüpte ({_money(borrower)}/hafta)"
    if fee:
        text += f" · kiralık bedeli {_money(fee)}"
    if option_fee:
        text += f" · {'satın alma yükümlülüğü' if option_mandatory else 'satın alma opsiyonu'} {_money(option_fee)}"
    return LoanTermsView(weeks=weeks, weeks_text=span, wage_share=int(share), borrower_weekly=borrower,
                         parent_weekly=parent, fee=int(fee), option_fee=option_fee,
                         option_mandatory=bool(option_mandatory), text=text)


class WorldMarket:
    """
    15F dunya pazari (AI <-> AI). Haftalik giris: run_week(week) -> tamamlanan transferlerin TransferNews listesi
    (career_manager.run_ai_transfer_window haber secimini eskisi gibi yapar).

    Sira: (1) donemin ILK haftasinda AI kulupleri fazlalik / genc oyuncularini listeler · (2) bu haftanin kotasi
    kadar AI <-> AI transfer (agirlikli alici secimi: kasasi buyuk kulup daha cok harcar) · (3) AI <-> AI kiralik
    · (4) geri alim maddeleri · (5) son haftada "son gun" haberi. Her adim kendi savepoint'inde: pazar hatasi
    haftayi bozmaz. cm.rng'den CEKILMEZ.
    """

    def __init__(self, cm: CareerManager, report=None) -> None:
        self.cm = cm
        self.db = cm.db
        self._report = report
        self.humans = cm.human_team_ids()
        self.protected = cm._protected_team_ids()
        self.season = int(cm.season)
        self.cw = int(cm.career_week)
        self._averages: dict[int, dict] = {}
        self._moved: set[int] = set()
        self._in: dict[int, int] = {}
        self._out: dict[int, int] = {}
        self._spend: dict[int, int] = {}        # bu donemde kulup basina harcanan (donem payi icin)
        self._elite_floor = float("inf")        # ELIT maas butcesi esigi (rules.elite_wage_floor)
        self._rumours = 0
        self._human_leagues: set | None = None
        self._winter = False                    # kis donemi: daha sakin hedef, kasanin daha buyuk kismi harcanir

    # ------------------------------------------------------------------ haftalik giris

    def run_week(self, week: int) -> list:
        cm = self.cm
        season_weeks = max(1, int(cm._projected_season_weeks() or 1))
        window = rules.transfer_window(int(week), season_weeks, False)   # sezon arasinda pazar yok (15A'nin isi)
        span = rules.window_span(int(week), season_weeks, False)
        if span is None:
            return []
        self._winter = window.name == rules.WINDOW_WINTER
        index, length, deadline = rules.window_index(int(week), span, False)
        clubs = self._ai_clubs()
        if len(clubs) < 4:
            return []
        # ELIT esigi donem icinde BIR KEZ, dunyanin kendi ortancasindan (kulup basina degil)
        self._elite_floor = rules.elite_wage_floor(t.wage_budget for t in clubs)
        if index == 1:
            self._safe("listings", self._open_window, clubs, _rng("mk-list", self.season, week))
        self._read_counts(span, int(week))
        target = rules.window_deal_target(len(clubs), winter=self._winter)
        quota = rules.weekly_quota(target, index, length, deadline)
        news = self._transfers(clubs, quota, _rng("mk-deal", self.season, week))
        self._safe("loans", self._ai_loans, clubs, _rng("mk-loan", self.season, week))
        self._safe("buy_backs", self._buy_backs, _rng("mk-back", self.season, week))
        if deadline:
            self._safe("deadline", self._deadline_story, span, int(week))
        return news

    def _safe(self, label: str, fn, *args):
        self.db.flush()
        try:
            with self.db.begin_nested():
                return fn(*args)
        except (SQLAlchemyError, ValueError, TransferError) as exc:
            log.exception("Dunya pazari adimi basarisiz (%s): %s", label, exc)
            return None

    # ------------------------------------------------------------------ dunya verisi

    def _ai_clubs(self) -> list[Team]:
        """Pazara giren kulupler: insan ve koruma altindaki kulupler disarida (kadrolari tek sorguda yuklenir)."""
        teams = self.cm._load_teams(Team.players, Team.academy_players)
        return [t for t in teams if t.id not in self.humans and t.id not in self.protected]

    def _league_average(self, league_id) -> dict:
        key = int(league_id) if league_id is not None else 0
        if key not in self._averages:
            league = self.db.get(League, key) if league_id is not None else None
            self._averages[key] = transfers.league_position_average(league.teams) if league is not None else {}
        return self._averages[key]

    def _read_counts(self, span: tuple[int, int], week: int) -> None:
        """Bu donemde kulup basina kac alis / satis oldu (transfer_log'dan; ayri durum tutulmaz)."""
        self.db.flush()
        rows = self.db.execute(select(TransferLog.from_team_id, TransferLog.to_team_id, TransferLog.fee).where(
            TransferLog.season == self.season, TransferLog.week >= int(span[0]), TransferLog.week <= int(week),
            TransferLog.kind == TransferKind.TRANSFER.value)).all()
        self._in, self._out, self._spend = {}, {}, {}
        for from_id, to_id, fee in rows:
            if to_id is not None:
                self._in[int(to_id)] = self._in.get(int(to_id), 0) + 1
                self._spend[int(to_id)] = self._spend.get(int(to_id), 0) + int(fee or 0)
            if from_id is not None:
                self._out[int(from_id)] = self._out.get(int(from_id), 0) + 1

    def _debtors(self) -> set[int]:
        self.db.flush()
        return set(self.db.scalars(select(TransferPayment.payer_team_id).where(
            TransferPayment.status == OVERDUE, TransferPayment.payer_team_id.isnot(None)).distinct()))

    # ------------------------------------------------------------------ 1) donem acilisi: listeler

    def _open_window(self, clubs: list[Team], rng: random.Random) -> None:
        """
        Donemin ilk haftasinda AI kulupleri kadrolarini gozden gecirir: fazlalik oyuncular transfer listesine,
        ilk 11'e giremeyen gencler kiralik listesine girer. Menajerin gordugu "AI transfer / kiralik listeleri"
        budur (TransferDesk.market_listings). Degeri degismeyen satir YAZILMAZ.
        """
        for team in clubs:
            self._fill_squad(team)
            seniors = [p for p in team.players if p.loan_from_team_id is None]
            if len(seniors) < 2:
                continue
            can_sell = len(seniors) - 1 >= rules.MARKET_SQUAD_FLOOR
            for rank, p in enumerate(seniors, start=1):
                importance = transfers.squad_importance(p, seniors)
                listed = bool(can_sell and rank > loan_rules.AI_LOAN_OUT_PROTECTED_RANK
                              and importance < rules.MARKET_LIST_SURPLUS)
                loanable = bool(not listed and rank >= loan_rules.AI_LOAN_OFFER_MIN_RANK
                                and int(p.age) <= rules.MARKET_LIST_LOAN_AGE)
                if bool(p.transfer_listed) != listed:
                    p.transfer_listed = listed
                if bool(p.loan_listed) != loanable:
                    p.loan_listed = loanable
        self.db.flush()

    def _fill_squad(self, team: Team) -> None:
        """
        Donem acilisinda A takimi ince kalan AI kulubu akademisinden en hazir oyuncularla tamamlar.
        15A / 15B (sozlesme bitisi, emeklilik) dunyanin kadrolarini her sezon eritiyor; kadrosu tabana dayanan
        kulup SATAMAZ (market_squad_gate satistan SONRA >= MARKET_SQUAD_FLOOR ister) ve pazar satici bulamaz.
        Bu yuzden hedef TABANIN BIRAZ USTUDUR (MARKET_SQUAD_FLOOR + MARKET_FILL_HEADROOM): tam tabana
        tamamlamak dunyayi 20'de yiginlastirir ve satici birakmaz. Oyuncu YARATILMAZ, yalnizca A takima cikarilir.
        """
        seniors = [p for p in team.players if not p.in_academy]
        target = rules.MARKET_SQUAD_FLOOR + rules.MARKET_FILL_HEADROOM
        missing = min(rules.MARKET_FILL_PER_WINDOW, target - len(seniors))
        if missing <= 0:
            return
        ready = sorted((p for p in team.academy_players if p.loan_from_team_id is None),
                       key=lambda p: (-int(p.overall_rating), p.id))[:missing]
        for player in ready:
            player.in_academy = False
            player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        if ready:
            self.db.flush()
            self.db.expire(team, ["players", "academy_players"])

    # ------------------------------------------------------------------ 2) AI <-> AI transferler

    @staticmethod
    def _pools(clubs: list[Team]) -> dict:
        """Mevki -> [(oyuncu, satici kulup)] en iyiden zayifa. Kulup nesneleri zaten yuklu: ek sorgu yok."""
        pools: dict = {}
        for team in clubs:
            for p in team.players:
                if p.in_academy or p.loan_from_team_id is not None:
                    continue
                pools.setdefault(p.position, []).append((p, team))
        for rows in pools.values():
            rows.sort(key=lambda row: (-int(row[0].overall_rating), row[0].id))
        return pools

    def _buyers(self, clubs: list[Team]) -> tuple[list[Team], list[float]]:
        debtors = self._debtors()
        buyers: list[Team] = []
        weights: list[float] = []
        for team in clubs:
            cap = self._cap(team)
            if team.id in debtors or cap < MARKET_MIN_FEE:
                continue
            if len(team.players) >= rules.MARKET_SQUAD_CAP:
                continue
            if self._in.get(team.id, 0) >= self._max_in(team):
                continue
            buyers.append(team)
            weights.append(rules.market_buyer_weight(cap, int(team.wage_budget), int(team.reputation)))
        return buyers, weights

    def _cap(self, team: Team) -> int:
        """
        Kulubun su an harcayabilecegi en yuksek tutar: tek transfer tavani ile DONEM payinin kalani.
        Donem payi bu donemde zaten harcanani dusler (rules.market_window_allowance).
        """
        budget = int(team.transfer_budget)
        return min(rules.market_spend_cap(budget, winter=self._winter),
                   rules.market_window_allowance(budget, self._spend.get(team.id, 0), winter=self._winter))

    @staticmethod
    def _max_in(team: Team) -> int:
        """Kulubun bu donemdeki alis hakki (rules.market_max_in: buyuk kulup daha cok alir)."""
        return rules.market_max_in(rules.market_wealth(int(team.wage_budget)))

    def _transfers(self, clubs: list[Team], quota: int, rng: random.Random) -> list:
        if quota <= 0:
            return []
        pools = self._pools(clubs)
        buyers, weights = self._buyers(clubs)
        index_of = {team.id: i for i, team in enumerate(buyers)}
        news: list = []
        attempts, limit = 0, max(1, quota) * rules.MARKET_BUYER_TRIES
        while len(news) < quota and attempts < limit:
            attempts += 1
            buyer = rules.weighted_pick(rng, buyers, weights)
            if buyer is None:
                break
            slot = index_of[buyer.id]
            deal = self._attempt(buyer, pools, rng)
            if deal is None:
                weights[slot] = weights[slot] * MARKET_FAIL_DECAY if weights[slot] > 0.05 else 0.0
                continue
            news.append(deal)
            self._in[buyer.id] = self._in.get(buyer.id, 0) + 1
            cap = self._cap(buyer)
            if self._in[buyer.id] >= self._max_in(buyer) or cap < MARKET_MIN_FEE or \
                    len(buyer.players) >= rules.MARKET_SQUAD_CAP:
                weights[slot] = 0.0
            else:
                weights[slot] = rules.market_buyer_weight(cap, int(buyer.wage_budget), int(buyer.reputation))
        return news

    def _attempt(self, buyer: Team, pools: dict, rng: random.Random):
        cap = self._cap(buyer)
        wealth = rules.market_wealth(int(buyer.wage_budget))
        elite = float(int(buyer.wage_budget)) >= self._elite_floor
        manager_rep = reputation.ai_manager_reputation(int(buyer.reputation))
        depth = self._depth(buyer)
        for need in transfers.squad_needs(buyer, self._league_average(buyer.league_id))[:MARKET_NEEDS_PER_TRY]:
            best, second = depth.get(need.position, (0, 0))
            candidates = self._candidates(buyer, pools.get(need.position) or (), need, cap, wealth, best,
                                          second, elite)
            if not candidates:
                continue
            candidates.sort(key=lambda row: (-row[0], row[1].id))
            picks = rng.sample(candidates[:MARKET_TOP_PICKS], min(MARKET_PICK_TRIES, len(candidates),
                                                                  MARKET_TOP_PICKS))
            for _score, target, seller, asking in picks:
                deal = self._complete(buyer, seller, target, asking, rng, manager_rep)
                if deal is not None:
                    return deal
                self._rumour(buyer, seller, target)
        return None

    @staticmethod
    def _depth(buyer: Team) -> dict:
        """Mevki -> (en iyi guc, ikinci guc). Derinlik puani (rules.market_score) bunu kullanir."""
        out: dict = {}
        for p in buyer.players:
            best, second = out.get(p.position, (0, 0))
            value = int(p.overall_rating)
            if value > best:
                best, second = value, best
            elif value > second:
                second = value
            out[p.position] = (best, second)
        return out

    def _candidates(self, buyer: Team, pool, need, cap: int, wealth: float, best: int, second: int,
                    elite: bool) -> list:
        """Alicinin bu mevkide bakacagi adaylar: kasa, kadro kapilari, transfer yasagi ve rules.market_score."""
        out: list = []
        for p, seller in pool:
            if len(out) >= rules.MARKET_TARGET_POOL:
                break
            value = int(p.market_value or 0)
            if seller.id == buyer.id or p.id in self._moved or not 0 < value <= cap:
                continue
            if int(p.last_transfer_season or 0) == self.season:
                continue
            if self._out.get(seller.id, 0) >= rules.MARKET_MAX_OUT_PER_WINDOW:
                continue
            if rules.market_squad_gate(len(seller.players), len(buyer.players)):
                continue
            if self.cm._transfer_block_reason(p, self.cw) is not None:
                continue
            score = rules.market_score(player_overall=int(p.overall_rating), player_age=int(p.age),
                                       best_at_position=best, depth_at_position=second,
                                       shortfall=float(need.shortfall), wealth=wealth, elite=elite)
            if score < rules.MARKET_MIN_TARGET_SCORE:
                continue
            asking = transfers.asking_price(p, seller, int(buyer.reputation))
            if not MARKET_MIN_FEE <= asking <= cap:      # istenen bedel kasayi asiyorsa aday bile olmaz
                continue
            out.append((score, p, seller, asking))
        return out

    def _complete(self, buyer: Team, seller: Team, target: Player, asking: int, rng: random.Random,
                  manager_rep: float):
        """Bir AI <-> AI transferini bastan sona dener; olmadiysa None (hicbir sey yazilmaz)."""
        cm = self.cm
        cap = self._cap(buyer)
        if not MARKET_MIN_FEE <= asking <= cap:          # aday secildikten sonra kasa degismis olabilir
            return None
        fee = transfers.ai_opening_offer(rng, asking, cap)
        if fee < MARKET_MIN_FEE:
            return None
        if not transfers.evaluate_fee(rng, target, seller, fee, int(buyer.reputation)).accepted:
            return None
        negotiation = ContractNegotiation(rng, target, buyer, fee, manager_reputation=manager_rep)
        if not negotiation.open:
            return None
        offer = transfers.ai_contract_offer(rng, negotiation, max(buyer.free_wage, negotiation.demand.wage))
        if negotiation.persuasion(offer) < negotiation.required_persuasion:
            return None
        if negotiation.respond(offer).status is not NegotiationStatus.ACCEPTED:
            return None
        try:
            with self.db.begin_nested():
                if offer.wage > buyer.free_wage:
                    shift = finance.auto_shift_for_wage(int(buyer.transfer_budget), int(buyer.wage_budget),
                                                        int(buyer.free_wage), int(offer.wage), fee)
                    if shift <= 0:
                        raise TransferError("Maas havuzu yetersiz.")
                    cm.shift_budget(buyer, shift)
                    if offer.wage > buyer.free_wage or fee > int(buyer.transfer_budget):
                        raise TransferError("Maas havuzu yetersiz.")
                news = cm.complete_transfer(buyer, target, fee, offer)
        except (TransferError, finance.BudgetError, SQLAlchemyError):
            return None
        self._moved.add(target.id)
        self._out[seller.id] = self._out.get(seller.id, 0) + 1
        self._spend[buyer.id] = self._spend.get(buyer.id, 0) + int(fee)
        self.db.expire(seller, ["players"])
        self.db.expire(buyer, ["players"])
        return news

    # ------------------------------------------------------------------ 3) soylentiler ve son gun

    def _inbox_targets(self) -> list[tuple[int | None, int]]:
        """(gelen kutusu sahibi, kulup) ciftleri: her insan menajer icin bir satir. Gelen kutusu kapaliysa bos."""
        if not self.cm._inbox_on():
            return []
        return [(inbox.manager_id_for(self.cm, team_id), team_id) for team_id in sorted(self.humans)]

    def _post(self, kind: str, subject: str, body: str, *, ref_type=None, ref_id=None,
              important: bool = False) -> None:
        cm = self.cm
        season, week = int(cm.season), int(cm.current_week)
        for manager_id, team_id in self._inbox_targets():
            inbox.post(self.db, manager_id=manager_id, kind=kind, subject=subject, body=body, team_id=team_id,
                       season=season, week=week, career_week=self.cw,
                       game_date=inbox.match_date(season, week, start=cm.state.season_start_date),
                       ref_type=ref_type, ref_id=ref_id, important=important)

    def _rumour(self, buyer: Team, seller: Team, target: Player) -> None:
        """
        AI dosyasindan soylenti: kulup X, oyuncu Y ile ilgileniyor. Gurultu siniri (15D): haftada en fazla
        rules.RUMOURS_PER_WEEK. Yalnizca menajeri ilgilendiren isimler: degeri MARKET_RUMOUR_VALUE ustundeki
        oyuncular ve menajerin ligindeki kulupler.
        """
        if self._rumours >= rules.RUMOURS_PER_WEEK:
            return
        value = int(target.market_value or 0)
        if self._human_leagues is None:
            self._human_leagues = {t.league_id for t in (self.db.get(Team, i) for i in self.humans)
                                   if t is not None}
        interesting = value >= MARKET_RUMOUR_VALUE or buyer.league_id in self._human_leagues or \
            seller.league_id in self._human_leagues
        if not interesting:
            return
        self._rumours += 1
        text = (f"Söylenti: {buyer.name}, {seller.name} kulübünden {target.name} ({_ev(target.position)}, "
                f"{int(target.age)}) ile ilgileniyor (değeri {_money(value)}).")
        self.cm._add_news(NewsKind.RUMOUR, text, team_id=buyer.id, other_team_id=seller.id)
        self._post(inbox.KIND_NEWS, f"Söylenti: {target.name} için {buyer.name}", text,
                   ref_type=inbox.REF_PLAYER, ref_id=target.id)

    def _deadline_story(self, span: tuple[int, int], week: int) -> None:
        """
        "Son gun": donem kapanirken dunyanin ozeti (kac transfer, en pahalisi) ve menajerin kendi bilancosu.
        Haber akisina ve (acikken) gelen kutusuna tek mesaj yazilir.
        """
        self.db.flush()
        rows = list(self.db.execute(select(
            TransferLog.player_name, TransferLog.from_team_name, TransferLog.to_team_name, TransferLog.fee,
            TransferLog.from_team_id, TransferLog.to_team_id).where(
            TransferLog.season == self.season, TransferLog.week >= int(span[0]), TransferLog.week <= int(week),
            TransferLog.kind == TransferKind.TRANSFER.value).order_by(TransferLog.fee.desc())).all())
        if not rows:
            return
        spend = sum(int(r[3] or 0) for r in rows)
        top = rows[0]
        headline = (f"Son gün: transfer dönemi kapandı. Dünyada {len(rows)} transfer, toplam harcama "
                    f"{_money(spend)}. En pahalısı {top[0]} ({top[1]} → {top[2]}, {_money(int(top[3] or 0))}).")
        self.cm._add_news(NewsKind.RUMOUR, headline, team_id=top[5], other_team_id=top[4])
        lines = [["info", f"{r[0]}: {r[1]} → {r[2]} ({_money(int(r[3] or 0))})"] for r in rows[:10]]
        for manager_id, team_id in self._inbox_targets():
            mine_in = [r for r in rows if r[5] == team_id]
            mine_out = [r for r in rows if r[4] == team_id]
            body = headline
            if mine_in or mine_out:
                body += (f" Kulübün bu dönemde {len(mine_in)} transfer yaptı, {len(mine_out)} oyuncu gönderdi.")
            inbox.post(self.db, manager_id=manager_id, kind=inbox.KIND_NEWS, subject="Son gün: dönem kapandı",
                       body=body, team_id=team_id, season=self.season, week=int(self.cm.current_week),
                       career_week=self.cw, lines=lines, important=True,
                       game_date=inbox.match_date(self.season, int(self.cm.current_week),
                                                  start=self.cm.state.season_start_date))

    # ------------------------------------------------------------------ 4) AI <-> AI kiraliklar

    def _ai_loans(self, clubs: list[Team], rng: random.Random) -> None:
        """
        AI kulubu fazlalik / genc oyuncusunu baska bir AI kulubune kiraliga verir (ligler arasi). Kararlar
        loan_rules.ai_accepts_loan_out / ai_accepts_loan_in; kiralik bedeli yoktur.
        """
        if not loans_on(self.cm):
            return
        import market_hub

        hub = market_hub.MarketHub(self.cm)
        parents = [t for t in clubs if len(t.players) > loan_rules.AI_LOAN_OUT_MIN_SQUAD]
        if len(parents) < 2:
            return
        season_end = hub._season_end_career_week()
        done = 0
        for _ in range(AI_LOAN_PAIRS_PER_WEEK * 3):
            if done >= AI_LOAN_PAIRS_PER_WEEK:
                break
            parent = parents[rng.randrange(len(parents))]
            seniors = [p for p in parent.players if p.loan_from_team_id is None]
            pool = [(rank, p) for rank, p in enumerate(seniors, start=1)
                    if p.loan_listed and p.id not in self._moved and position_floor_ok(parent, p)
                    and self.cm._transfer_block_reason(p, self.cw) is None]
            if not pool:
                continue
            rank, player = pool[rng.randrange(len(pool))]
            share = loan_rules.loan_out_required_share(int(player.overall_rating), rank)
            ok, _reason = loan_rules.ai_accepts_loan_out(int(player.overall_rating), rank, len(seniors), share, None)
            if not ok:
                continue
            borrower = self._loan_borrower(clubs, parent, player, share, rng)
            if borrower is None:
                continue
            end = loan_rules.loan_end_week(self.cw, None, season_end)
            if end - self.cw < market_rules.MIN_LOAN_WEEKS:
                return                                    # sezon sonuna cok az kaldi: bu hafta kiralik yok
            try:
                with self.db.begin_nested():
                    hub._start_loan(None, player, parent, borrower, self.cw, end, share, 0)
            except (TransferError, SQLAlchemyError, ValueError):
                continue
            self._moved.add(player.id)
            self.db.expire(parent, ["players", "loaned_out_players"])
            self.db.expire(borrower, ["players"])
            done += 1

    def _loan_borrower(self, clubs: list[Team], parent: Team, player: Player, share: int,
                       rng: random.Random) -> Team | None:
        """Oyuncuyu kiralayacak AI kulubu: mevkisinde guclenme ve bos maas alani (loan_rules.ai_accepts_loan_in)."""
        wage = int(player.current_wage or 0)
        picks = [t for t in clubs if t.id != parent.id and len(t.players) < rules.MARKET_SQUAD_CAP]
        if not picks:
            return None
        for _ in range(4):
            club = picks[rng.randrange(len(picks))]
            ratings = [p.overall_rating for p in club.players if p.position is player.position]
            average = sum(ratings) / len(ratings) if ratings else None
            ok, _reason = loan_rules.ai_accepts_loan_in(int(player.overall_rating), average, int(club.free_wage),
                                                        wage, share)
            if ok:
                return club
        return None

    # ------------------------------------------------------------------ 5) geri alim maddeleri

    def _buy_backs(self, rng: random.Random) -> None:
        """
        AI satici kulup, geri alim maddesi tasidigi oyuncunun degeri bedeli belirgin astiysa maddeyi kullanir
        (rules.buy_back_attractive). Maddeyi tasiyan kulup INSANSA hicbir sey yapilmaz: menajer kendisi karar
        verir (TransferDesk.buy_back_options / trigger_buy_back).
        """
        self.db.flush()
        rows = list(self.db.scalars(select(TransferDeal).where(
            TransferDeal.status == COMPLETED, TransferDeal.buy_back_fee.isnot(None),
            TransferDeal.buy_back_seasons > 0, TransferDeal.completed_season.isnot(None),
            TransferDeal.completed_season > self.season - rules.MAX_BUY_BACK_SEASONS)
            .order_by(TransferDeal.id)))
        for deal in rows:
            club = self.db.get(Team, deal.seller_team_id) if deal.seller_team_id is not None else None
            if club is None or club.id in self.humans or club.id in self.protected:
                continue
            player = self.db.get(Player, deal.player_id)
            if player is None or player.team_id != deal.buyer_team_id or player.team_id in self.humans:
                continue
            if not rules.buy_back_open(int(deal.completed_season), self.season, int(deal.buy_back_seasons)):
                continue
            fee = int(deal.buy_back_fee)
            if not rules.buy_back_attractive(fee, int(player.market_value or 0)):
                continue
            if fee > rules.market_spend_cap(int(club.transfer_budget)) or rng.random() >= 0.5:
                continue
            if self._exercise_buy_back(deal, club, player, rng) is not None:
                self._moved.add(player.id)

    def _exercise_buy_back(self, deal: TransferDeal, club: Team, player: Player, rng: random.Random):
        """Geri alim maddesini uygular: sabit bedel, oyuncunun sozlesmesi yenilenir. Basarisizsa None."""
        cm = self.cm
        seller = self.db.get(Team, player.team_id) if player.team_id is not None else None
        if seller is None or rules.market_squad_gate(len(seller.players), len(club.players)):
            return None
        fee = int(deal.buy_back_fee)
        manager_rep = reputation.ai_manager_reputation(int(club.reputation))
        negotiation = ContractNegotiation(rng, player, club, fee, manager_reputation=manager_rep)
        if not negotiation.open:
            return None
        offer = transfers.ai_contract_offer(rng, negotiation, max(club.free_wage, negotiation.demand.wage))
        try:
            with self.db.begin_nested():
                if offer.wage > club.free_wage:
                    shift = finance.auto_shift_for_wage(int(club.transfer_budget), int(club.wage_budget),
                                                        int(club.free_wage), int(offer.wage), fee)
                    if shift <= 0:
                        raise TransferError("Maas havuzu yetersiz.")
                    cm.shift_budget(club, shift)
                    if offer.wage > club.free_wage or fee > int(club.transfer_budget):
                        raise TransferError("Maas havuzu yetersiz.")
                news = cm.complete_transfer(club, player, fee, offer)
                deal.buy_back_fee, deal.buy_back_seasons = None, 0
                self._log_deal(deal, {"kind": "buy_back_used", "fee": fee, "club": club.id})
        except (TransferError, finance.BudgetError, SQLAlchemyError):
            return None
        self.cm._add_news(NewsKind.RUMOUR, f"Geri alım maddesi: {club.name}, {player.name} oyuncusunu "
                                           f"{_money(fee)} karşılığında geri aldı.",
                          team_id=club.id, other_team_id=seller.id)
        self.db.expire(seller, ["players"])
        self.db.expire(club, ["players"])
        return news

    def _log_deal(self, deal: TransferDeal, entry: dict) -> None:
        deal.history = [*(deal.history or []), {"cw": self.cw, **entry}]
        deal.updated_career_week = self.cw


class LoanCycle:
    """
    15F haftalik kiralik dongusu (transfer_desk.run_week icinden; her adim kendi savepoint'inde).

    1) satin alma opsiyonlari  bu hafta biten opsiyonlu kiraliklar: AI kiralayan loan_rules.ai_exercises_option
                               ile karar verir (ZORUNLU opsiyon her dunyada uygulanir). HER dunyada calisir.
    2) AI kiralik teklifleri   donem aciksa AI kulupleri menajerin yedek oyuncularini kiralamak ister
                               (transfer_deals kind = LOAN, direction = OUT). Bayrak rules.LIVE_MARKET.
    3) yasam dongusu           TEK OYUNCULU dunyada kiralik bitisi / AI geri cagirmasi / kaygi bildirimleri /
                               kiralik oyuncunun maas talebi temizligi. Paylasilan dunyada bunlari
                               market_hub.MarketExtension kosar; cift calismasin diye burada atlanir.
    """

    def __init__(self, cm: CareerManager, report=None) -> None:
        self.cm = cm
        self.db = cm.db
        self.desk = TransferDesk(cm)
        self.desk._report = report
        self.desk._humans = cm.human_team_ids()
        self._report = report

    @property
    def cw(self) -> int:
        return int(self.cm.career_week)

    def _hub(self):
        import market_hub

        return market_hub.MarketHub(self.cm)

    def run_week(self, week: int, next_open: bool) -> None:
        if not loans_on(self.cm):
            return
        next_week = self.cw + 1
        self.desk._safe("loan_options", self._options, next_week)
        if live_market_on(self.cm) and (next_open or self.desk._window().open):
            self.desk._safe("loan_offers", self._ai_loan_offers, week)
        if _market_extension_active(self.cm):
            return                                    # paylasilan dunya: dongu MarketExtension'da
        hub = self._hub()
        self.desk._safe("loan_end", hub._end_due_loans, next_week)
        self.desk._safe("loan_recalls", hub._ai_recalls)
        self.desk._safe("loan_concerns", hub._loan_concern_notices)
        self.desk._safe("loan_wages", hub._loaned_wage_demands, self._report)

    # ------------------------------------------------------------------ 1) satin alma opsiyonu

    def _options(self, next_week: int) -> None:
        """
        Suresi dolan opsiyonlu kiraliklar: kiralayan kulup satin alma hakkini / yukumlulugunu kullanir mi?
        Kiralik once normal bicimde biter (oyuncu ana kulubune doner), sonra transfer tamamlanir: para ve
        transfer kaydi tek yerden (CareerManager.complete_transfer) gecer, sonraki satis payi da isler.
        """
        self.db.flush()
        rows = list(self.db.scalars(select(Loan).where(
            Loan.status == LoanStatus.ACTIVE.value, Loan.option_fee.isnot(None), Loan.option_used.is_(False),
            Loan.end_career_week.isnot(None), Loan.end_career_week <= int(next_week)).order_by(Loan.id)))
        if not rows:
            return
        humans = self.cm.human_team_ids()
        hub = self._hub()
        for loan in rows:
            player = self.db.get(Player, loan.player_id)
            borrower = self.db.get(Team, loan.borrower_team_id) if loan.borrower_team_id is not None else None
            parent = self.db.get(Team, loan.parent_team_id) if loan.parent_team_id is not None else None
            if player is None or borrower is None or parent is None or player.loan_id != loan.id:
                continue
            fee, mandatory = int(loan.option_fee), bool(loan.option_mandatory)
            human_borrower = borrower.id in humans
            if human_borrower and not mandatory:
                self._option_notice(loan, player, borrower, fee)      # menajer kullanmadi: madde duser
                continue
            take, reason = loan_rules.ai_exercises_option(int(player.market_value or 0), fee,
                                                          int(borrower.transfer_budget), mandatory)
            if not take:
                self._option_notice(loan, player, borrower, fee, reason)
                continue
            self._exercise(hub, loan, player, parent, borrower, fee, reason)

    def _option_notice(self, loan: Loan, player: Player, borrower: Team, fee: int, reason: str = "") -> None:
        loan.option_fee, loan.option_mandatory = None, False
        humans = self.cm.human_team_ids()
        for team_id in (loan.parent_team_id, loan.borrower_team_id):
            if team_id in humans:
                self.desk._notify(None, team_id, f"Kiralık opsiyonu kullanılmadı: {player.name} "
                                                 f"({_money(fee)}). {reason}".strip())

    def _exercise(self, hub, loan: Loan, player: Player, parent: Team, borrower: Team, fee: int,
                  reason: str) -> None:
        """Opsiyon kullanilir: kiralik biter, oyuncu bedel karsiligi kiralayan kulube satilir."""
        cm = self.cm
        rng = _rng("option", cm.season, self.cw, loan.id)
        manager_rep = cm.manager_reputation_for(borrower)
        try:
            with self.db.begin_nested():
                loan.option_used = True
                hub._end_loan(loan, LoanStatus.RETURNED)
                self.db.flush()
                negotiation = ContractNegotiation(rng, player, borrower, fee, manager_reputation=manager_rep)
                offer = transfers.ai_contract_offer(rng, negotiation, max(borrower.free_wage,
                                                                          negotiation.demand.wage))
                if offer.wage > borrower.free_wage:
                    shift = finance.auto_shift_for_wage(int(borrower.transfer_budget), int(borrower.wage_budget),
                                                        int(borrower.free_wage), int(offer.wage), fee)
                    if shift <= 0:
                        raise TransferError("Maas havuzu yetersiz.")
                    cm.shift_budget(borrower, shift)
                cm.complete_transfer(borrower, player, fee, offer)
        except (TransferError, finance.BudgetError, SQLAlchemyError) as exc:
            log.info("Kiralık opsiyonu uygulanamadı (%s): %s", loan.id, exc)
            return
        text = f"Kiralık opsiyonu: {borrower.name}, {player.name} oyuncusunu {_money(fee)} karşılığında aldı."
        cm._add_news(NewsKind.TRANSFER, text, team_id=borrower.id, other_team_id=parent.id)
        humans = cm.human_team_ids()
        for team_id in (parent.id, borrower.id):
            if team_id in humans:
                self.desk._notify(None, team_id, f"{text} {reason}".strip())

    # ------------------------------------------------------------------ 2) AI kiralik teklifleri

    def _ai_loan_offers(self, week: int) -> None:
        """Donem aciksa AI kulupleri menajerin ilk 11 disindaki oyuncularini kiralamak ister (tohum: hafta+kulup)."""
        humans = sorted(self.cm.human_team_ids())
        if not humans:
            return
        clubs = None
        for team_id in humans:
            team = self.db.get(Team, team_id)
            if team is None:
                continue
            rng = _rng("loan-offer", self.cm.season, int(week), team_id)
            if rng.random() >= LOAN_OFFER_CHANCE:
                continue
            self.db.flush()
            open_count = int(self.db.scalar(select(func.count()).select_from(TransferDeal).where(
                TransferDeal.human_team_id == team.id, TransferDeal.kind == LOAN_KIND,
                TransferDeal.status.in_(sorted(OPEN)))) or 0)
            if open_count >= LOAN_OFFERS_OPEN_MAX:
                continue
            if clubs is None:
                clubs = [t for t in self.cm._load_teams(Team.players)
                         if t.id not in self.cm.human_team_ids() and t.id not in self.cm._protected_team_ids()]
            self._offer_for(team, clubs, rng)

    def _offer_for(self, team: Team, clubs: list[Team], rng: random.Random) -> None:
        """
        Menajerin ilk 11 disindaki bir oyuncusu icin kiralik teklifi arar. Kiralayan aday, oyuncunun o mevkide
        GUCLENDIRDIGI kuluplerden secilir (aksi halde loan_rules.ai_accepts_loan_in zaten reddeder).
        """
        seniors = [p for p in team.players if p.loan_from_team_id is None and not p.in_academy]
        pool = [(rank, p) for rank, p in enumerate(seniors, start=1)
                if rank >= loan_rules.AI_LOAN_OFFER_MIN_RANK and position_floor_ok(team, p)
                and self.cm.transfer_block_reason(p) is None]
        if not pool or not clubs:
            return
        self.db.flush()                                   # acik dosyalar TEK sorguda (oyuncu, alici) ciftleri
        busy = set(self.db.execute(select(TransferDeal.player_id, TransferDeal.buyer_team_id).where(
            TransferDeal.player_id.in_(sorted(p.id for _r, p in pool)),
            TransferDeal.status.in_(sorted(OPEN)))).all())
        for _ in range(LOAN_OFFER_TRIES):
            rank, player = pool[rng.randrange(len(pool))]
            options = []
            for club in clubs:
                if club.id == team.id or len(club.players) >= rules.MARKET_SQUAD_CAP:
                    continue
                ratings = [p.overall_rating for p in club.players if p.position is player.position]
                average = sum(ratings) / len(ratings) if ratings else None
                if average is not None and average > int(player.overall_rating):
                    continue                              # oyuncu bu kulubu guclendirmiyor
                options.append((club, average))
            rng.shuffle(options)
            for club, average in options[:LOAN_OFFER_CLUB_TRIES]:
                if (player.id, club.id) in busy:
                    continue
                offer = loan_rules.ai_loan_offer(
                    rng, player_overall=int(player.overall_rating), player_age=int(player.age), player_rank=rank,
                    parent_squad_size=len(seniors), borrower_position_avg=average,
                    borrower_free_wage=int(club.free_wage), wage=int(player.current_wage or 0),
                    market_value=int(player.market_value or 0))
                if offer is None:
                    continue
                self._write_offer(team, club, player, offer)
                return

    def _write_offer(self, team: Team, club: Team, player: Player, offer) -> None:
        deal = self.desk._new_deal(direction=OUT, player=player, seller=team, buyer=club, human=team,
                                   status=BIDDING, patience=0)
        if deal.round or deal.status != BIDDING or deal.direction != OUT:
            return                                        # ayni oyuncu/kulup icin zaten acik bir dosya vardi
        deal.kind, deal.round, deal.turn, deal.last_action = LOAN_KIND, 1, MANAGER, "BID"
        deal.loan_weeks = offer.weeks
        deal.loan_wage_share = int(offer.share)
        deal.option_fee = int(offer.option_fee) if offer.option_fee else None
        deal.option_mandatory = bool(offer.option_mandatory)
        deal.expires_career_week = self.cw + 1 + LOAN_OFFER_VALID_WEEKS
        self.desk._log(deal, {"kind": "loan_offer", "side": CLUB, "terms": offer.to_dict()})
        text = f"{club.name}, {player.name} için kiralık teklifi yaptı: {offer.describe()}."
        self.desk._notify(deal, team.id, text, NotificationKind.OFFER_IN)
        if self.cm._inbox_on():
            season, week = int(self.cm.season), int(self.cm.current_week)
            inbox.post(self.db, manager_id=inbox.manager_id_for(self.cm, team.id),
                       kind=inbox.KIND_TRANSFER_OFFER, subject=f"Kiralık teklifi: {player.name}", body=text,
                       team_id=team.id, season=season, week=week, career_week=self.cw,
                       game_date=inbox.match_date(season, week, start=self.cm.state.season_start_date),
                       ref_type=inbox.REF_DEAL, ref_id=deal.id, important=True)


class MarketDesk:
    """
    15F menajer API'si (15F-U arayuzu bunu kullanir). FLUSH eder, COMMIT ETMEZ; hatalar Turkce DeskError.
    Donusler duz gorunum nesneleridir (ORM sizmaz), sayilar K12'ye gore SISLIDIR.

        window()                     bu haftanin transfer donemi (arayuz seridi)
        listings(kind, ...)          AI kuluplerinin transfer / kiralik listeleri (sisli guc ve deger)
        world_moves(limit)           dunyada tamamlanan son transfer ve kiraliklar ("AI transfer listesi")
        loans(direction)             kulubumun kiraliklari (market_hub.LoanView)
        request_loan(...)            AI kulubunden oyuncu kirala (aninda karar: loan_rules.ai_accepts_loan_out)
        offer_loan_out(...)          oyuncumu AI kulubune kiraliga ver (loan_rules.ai_accepts_loan_in)
        loan_offers() / accept_loan_offer(id) / reject_loan_offer(id, sebep)
                                     AI kuluplerinin kiralik teklifleri (transfer_deals, kind = LOAN)
        recall(loan_id)              erken geri cagirma (loan_rules.recall_allowed)
        exercise_option(loan_id)     kiraladigim oyuncuyu opsiyon bedeliyle satin al
        buy_back_options() / trigger_buy_back(player_id)
                                     kulubumun tasidigi geri alim maddeleri (sabit bedel, N sezon)
    """

    def __init__(self, cm: CareerManager) -> None:
        self.cm = cm
        self.db = cm.db
        self.desk = TransferDesk(cm)

    # ------------------------------------------------------------------ temel

    @property
    def cw(self) -> int:
        return int(self.cm.career_week)

    def _team(self) -> Team:
        return self.desk._team()

    def _hub(self):
        import market_hub

        return market_hub.MarketHub(self.cm)

    def _require_loans(self) -> None:
        if not loans_on(self.cm):
            raise DeskError(LOANS_OFF_TEXT)

    def window(self) -> WindowView:
        """Bu haftanin transfer donemi (arayuz ust seridi; TransferDesk.window ile ayni)."""
        return self.desk.window()

    def _require_window(self) -> None:
        if not self.desk._window().open:
            raise DeskError(LOAN_WINDOW_TEXT)

    def _ai_club(self, team_id) -> Team:
        club = self.db.get(Team, team_id) if _is_id(team_id) else None
        if club is None:
            raise DeskError(LOAN_TARGET_TEXT)
        if club.id in self.cm.human_team_ids():
            raise DeskError(LOAN_HUMAN_TEXT.format(team=club.name))
        if club.ai_protected_until is not None and int(club.ai_protected_until) > self.cw:
            raise DeskError(f"{club.name} yönetim koruması altında; şu an kiralık yapmıyor.")
        return club

    @staticmethod
    def _seniors(team: Team) -> list[Player]:
        return [p for p in team.players if not p.in_academy]

    @staticmethod
    def _squad_max() -> int:
        from career_manager import SENIOR_SQUAD_MAX

        return SENIOR_SQUAD_MAX

    # ------------------------------------------------------------------ listeler (K12: sisli)

    def listings(self, kind: str = TRANSFER_KIND, query: str = "", position: str | None = None,
                 limit: int = 50) -> list[ListingRow]:
        """
        AI kuluplerinin transfer (kind = TRANSFER) ya da kiralik (LOAN) listesindeki oyuncular. Guc ve deger
        gozlem sisinin arkasindadir (K12): bilgi %25'in altindaysa "Bilinmiyor" yazilir, aksi halde aralik.
        """
        team = self._team()
        column = Player.loan_listed if str(kind).upper() == LOAN_KIND else Player.transfer_listed
        humans = self.cm.human_team_ids()
        self.db.flush()
        stmt = (select(Player, Team.name, League.name)                 # kulup ve lig adi ayni sorguda
                .outerjoin(Team, Team.id == Player.team_id)
                .outerjoin(League, League.id == Team.league_id)
                .where(column.is_(True), Player.team_id.isnot(None), Player.in_academy.is_(False),
                       Player.loan_from_team_id.is_(None), Player.team_id.notin_(sorted(humans) or [-1])))
        if query and query.strip():
            stmt = stmt.where(Player.name.ilike(f"%{query.strip()}%"))
        if position:
            stmt = stmt.where(Player.position == Position(_ev(position)))
        found = list(self.db.execute(stmt.order_by(Player.market_value.desc(), Player.id)
                                     .limit(max(1, min(200, int(limit))))).all())
        knowledge = self.desk.knowledge_map([p.id for p, _t, _l in found])   # iki sorgu (K12 sisi)
        base_margin, scout = self.cm.scout_margin(team), self.cm.scout_rating(team) or 0
        rows: list[ListingRow] = []
        for p, club_name, league_name in found:
            k = int(knowledge.get(p.id, 0))
            margin = rules.knowledge_margin(base_margin, k)
            if margin is None:
                overall_text = value_text = "Bilinmiyor"
            else:
                seed = (scout, p.id)
                overall_text = str(staff_rules.scouted_value(int(p.overall_rating), margin,
                                                             (*seed, "overall_rating")))
                band = staff_rules.scouted_money(int(p.market_value or 0), margin, (*seed, "value"))
                value_text = _money(band.low) if band.exact else f"{_money(band.low)} - {_money(band.high)}"
            detailed = k >= rules.FULL_THRESHOLD
            rows.append(ListingRow(
                player_id=p.id, name=p.name, position=_ev(p.position), age=int(p.age),
                team_id=p.team_id, team_name=club_name, league_name=league_name,
                knowledge=k, knowledge_label=rules.knowledge_label(k), overall_text=overall_text,
                value_text=value_text,
                asking_text=_money(int(p.asking_price)) if p.asking_price and k >= rules.DETAIL_THRESHOLD else None,
                wage=int(p.current_wage or 0) if detailed else None,
                contract_years=int(p.contract_years or 0) if detailed else None,
                transfer_listed=bool(p.transfer_listed), loan_listed=bool(p.loan_listed)))
        return rows

    def world_moves(self, limit: int = 20, kinds: tuple[str, ...] = ()) -> list[WorldMoveRow]:
        """Dunyada tamamlanan son transferler / kiraliklar (transfer_log; en yeni once)."""
        import market_hub

        labels = {TransferKind.TRANSFER.value: "Transfer", TransferKind.FREE_AGENT.value: "Serbest imza",
                  TransferKind.BOSMAN.value: "Bosman", market_hub.KIND_LOAN: "Kiralık",
                  market_hub.KIND_LOAN_RETURN: "Kiralık dönüşü"}
        wanted = tuple(kinds) or (TransferKind.TRANSFER.value, market_hub.KIND_LOAN)
        self.db.flush()
        rows = list(self.db.scalars(select(TransferLog).where(TransferLog.kind.in_(wanted))
                                    .order_by(TransferLog.id.desc()).limit(max(1, min(200, int(limit))))))
        return [WorldMoveRow(season=int(r.season), week=int(r.week), player_id=r.player_id,
                             player_name=r.player_name, from_team=r.from_team_name, to_team=r.to_team_name,
                             fee=int(r.fee or 0), fee_text=_money(int(r.fee or 0)), kind=r.kind,
                             kind_label=labels.get(r.kind, r.kind)) for r in rows]

    # ------------------------------------------------------------------ kiralik: listeler ve islemler

    def loans(self, direction: str = "ALL"):
        """Kulubumun kiraliklari (market_hub.LoanView; IN kiraladiklarim, OUT kiraliga verdiklerim)."""
        self._require_loans()
        return self._hub().loans(direction)

    def _check_borrow_room(self, team: Team, player: Player, share: int) -> None:
        limit = self._squad_max()
        if len(self._seniors(team)) >= limit:
            raise DeskError(LOAN_FULL_TEXT.format(limit=limit))
        need = loan_rules.wage_split(int(player.current_wage or 0), share)[0]
        if need > int(team.free_wage):
            raise DeskError(LOAN_WAGE_TEXT.format(need=_money(need), have=_money(max(0, int(team.free_wage)))))

    def _period(self, weeks: int | None) -> tuple[int, int]:
        import market_hub

        try:
            start, end = self._hub()._loan_period(weeks)
        except market_hub.LoanError as exc:              # Turkce metin aynen korunur
            raise DeskError(str(exc)) from exc
        if end - start < market_rules.MIN_LOAN_WEEKS:
            raise DeskError(f"Kiralık en az {market_rules.MIN_LOAN_WEEKS} hafta sürmeli.")
        return start, end

    def request_loan(self, player_id: int, weeks: int | None = None, share: int = 100, *,
                     option_fee: int | None = None, option_mandatory: bool = False):
        """
        AI kulubunden oyuncu kirala. Karar ANINDA verilir (loan_rules.ai_accepts_loan_out): kulup ilk 11'ini
        vermez, kadro tabanini korur, en az 6 haftalik (ya da sezon sonuna kadar) kiralik ve yeterli maas payi
        ister. Opsiyon istenirse bedel loan_rules.option_fee_for tabaninin altinda olamaz.
        """
        self._require_loans()
        self._require_window()
        team = self._team()
        player = self.desk._player(player_id)
        if player.team_id == team.id:
            raise DeskError(LOAN_OWN_TEXT.format(name=player.name))
        parent = self._ai_club(player.team_id)
        if player.in_academy:
            raise DeskError(LOAN_ACADEMY_TEXT.format(name=player.name))
        if player.loan_from_team_id is not None:
            raise DeskError(LOAN_ON_LOAN_TEXT.format(name=player.name))
        blocked = self.cm.transfer_block_reason(player)
        if blocked:
            raise DeskError(f"{player.name} kiralanamaz. {blocked}.")
        share = max(0, min(100, int(share)))
        self._check_borrow_room(team, player, share)
        seniors = self._seniors(parent)
        rank = next((i for i, p in enumerate(seniors, start=1) if p.id == player.id), len(seniors) + 1)
        fee = self._option_check(player, option_fee, option_mandatory)
        ok, reason = loan_rules.ai_accepts_loan_out(int(player.overall_rating), rank, len(seniors), share, weeks)
        if not ok:
            raise DeskError(f"{parent.name}: {reason}")
        if not position_floor_ok(parent, player):
            raise DeskError(f"{parent.name} {_ev(player.position)} mevkisinde yedeksiz kalır; kiralık vermez.")
        start, end = self._period(weeks)
        loan = self._start(parent, team, player, start, end, share, 0, fee, option_mandatory)
        self.desk._notify(None, team.id, f"Kiralık: {player.name}, {parent.name} kulübünden kiralandı "
                                         f"({end - start} hafta, maaşın %{share} payı sende).")
        return self._hub()._loan_view(loan, team.id)

    def offer_loan_out(self, player_id: int, team_id: int, weeks: int | None = None, share: int = 100, *,
                       option_fee: int | None = None, option_mandatory: bool = False):
        """Oyuncumu bir AI kulubune kiraliga ver (loan_rules.ai_accepts_loan_in: mevkisinde guclenme + maas alani)."""
        self._require_loans()
        self._require_window()
        team = self._team()
        player = self.desk._player(player_id)
        if player.team_id != team.id:
            raise DeskError(NOT_YOUR_PLAYER_TEXT.format(name=player.name))
        club = self._ai_club(team_id)
        self._validate_out(team, player)
        share = max(0, min(100, int(share)))
        fee = self._option_check(player, option_fee, option_mandatory)
        ratings = [p.overall_rating for p in club.players if p.position is player.position]
        ok, reason = loan_rules.ai_accepts_loan_in(int(player.overall_rating),
                                                   sum(ratings) / len(ratings) if ratings else None,
                                                   int(club.free_wage), int(player.current_wage or 0), share)
        if not ok:
            raise DeskError(f"{club.name}: {reason}")
        start, end = self._period(weeks)
        loan = self._start(team, club, player, start, end, share, 0, fee, option_mandatory)
        self.desk._notify(None, team.id, f"Kiralık: {player.name}, {club.name} kulübüne kiralık gitti "
                                         f"({end - start} hafta, maaşın %{share} payı kiralayanda).")
        return self._hub()._loan_view(loan, team.id)

    def _validate_out(self, team: Team, player: Player) -> None:
        if player.in_academy:
            raise DeskError(LOAN_ACADEMY_TEXT.format(name=player.name))
        if player.loan_from_team_id is not None:
            raise DeskError(LOAN_ON_LOAN_TEXT.format(name=player.name))
        blocked = self.cm.transfer_block_reason(player)
        if blocked:
            raise DeskError(f"{player.name} kiralığa verilemez. {blocked}.")
        if len(self._seniors(team)) - 1 < transfers.SQUAD_FLOOR:
            raise DeskError(LOAN_SQUAD_TEXT.format(floor=transfers.SQUAD_FLOOR))
        self.desk._seller_floor(team, player)

    def _option_check(self, player: Player, option_fee: int | None, mandatory: bool) -> int | None:
        """Opsiyon bedeli adil mi? Taban loan_rules.option_fee_for (piyasa degeri x pay)."""
        if option_fee is None:
            return None
        if isinstance(option_fee, bool) or not isinstance(option_fee, int) or option_fee <= 0:
            raise DeskError("Satın alma bedeli pozitif bir tam sayı olmalı.")
        floor = loan_rules.option_fee_for(int(player.market_value or 0), bool(mandatory))
        if option_fee < floor:
            raise DeskError(f"Kulüp satın alma bedelinin en az {_money(floor)} olmasını istiyor.")
        return int(option_fee)

    def _start(self, parent: Team, borrower: Team, player: Player, start: int, end: int, share: int, fee: int,
               option_fee: int | None, option_mandatory: bool, deal: TransferDeal | None = None):
        """Kiralik satirini kurar (market_hub._start_loan: oyuncu, maas paylasimi, transfer_log tek yerde)."""
        import market_hub

        hub = self._hub()
        try:
            with self.db.begin_nested():
                loan, _news, _log_id = hub._start_loan(None, player, parent, borrower, start, end, share, fee)
                loan.fee = int(fee)
                loan.option_fee = int(option_fee) if option_fee else None
                loan.option_mandatory = bool(option_mandatory) and bool(option_fee)
                loan.deal_id = deal.id if deal is not None else None
                self.db.flush()
        except market_hub.LoanError as exc:
            raise DeskError(str(exc)) from exc
        except (TransferError, finance.BudgetError) as exc:
            raise DeskError(str(exc)) from exc
        return loan

    def recall(self, loan_id: int):
        """Kiraliga verdigim oyuncuyu erken geri cagir (loan_rules.recall_allowed: sure alamiyorsa)."""
        self._require_loans()
        import market_hub

        try:
            return self._hub().recall_loan(loan_id)
        except market_hub.LoanError as exc:
            raise DeskError(str(exc)) from exc

    def exercise_option(self, loan_id: int):
        """Kiraladigim oyuncuyu opsiyon bedeliyle satin al (kiralik biter, transfer tamamlanir)."""
        self._require_loans()
        team = self._team()
        loan = self.db.get(Loan, loan_id) if _is_id(loan_id) else None
        if loan is None or loan.borrower_team_id != team.id or loan.status != LoanStatus.ACTIVE.value:
            raise DeskError("Kiralık bulunamadı.")
        if not loan.option_fee or loan.option_used:
            raise DeskError("Bu kiralıkta satın alma opsiyonu yok.")
        player = self.db.get(Player, loan.player_id)
        parent = self.db.get(Team, loan.parent_team_id) if loan.parent_team_id is not None else None
        if player is None or parent is None or player.loan_id != loan.id:
            raise DeskError("Kiralık bulunamadı.")
        fee = int(loan.option_fee)
        if fee > int(team.transfer_budget):
            raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(fee)))
        LoanCycle(self.cm)._exercise(self._hub(), loan, player, parent, team, fee, "")
        self.db.flush()
        if not loan.option_used:            # savepoint geri alindi: kasa / maas alani yetmedi
            raise DeskError("Satın alma tamamlanamadı (maaş havuzu ya da kasa yetersiz).")
        return self._hub()._loan_view(loan, team.id)

    # ------------------------------------------------------------------ AI kiralik teklifleri

    def _loan_view(self, deal: TransferDeal, player: Player | None = None) -> LoanOfferView:
        player = player or self.db.get(Player, deal.player_id)
        club = self.db.get(Team, deal.buyer_team_id) if deal.buyer_team_id is not None else None
        is_open = deal.status in OPEN
        last = _last(deal, "loan_offer") or {}
        terms = _loan_terms_view(player, deal.loan_weeks, int(deal.loan_wage_share or 100), 0,
                                 deal.option_fee, bool(deal.option_mandatory))
        return LoanOfferView(
            deal_id=deal.id, status=deal.status, status_label=STATUS_LABELS.get(deal.status, deal.status),
            player_id=deal.player_id, player_name=player.name if player is not None else "Oyuncu",
            position=_ev(player.position) if player is not None else "",
            age=int(player.age) if player is not None else 0,
            club_id=deal.buyer_team_id, club_name=club.name if club is not None else None, terms=terms,
            message=deal.reason or (f"{club.name if club else 'Kulüp'} kiralık teklifi yaptı: {terms.text}."
                                    if last else ""),
            expires_in_weeks=(max(0, int(deal.expires_career_week) - self.cw)
                              if is_open and deal.expires_career_week is not None else None),
            can_accept=bool(is_open and deal.turn == MANAGER), can_reject=bool(is_open and deal.turn == MANAGER))

    def loan_offers(self, open_only: bool = True, limit: int = 50) -> list[LoanOfferView]:
        """AI kuluplerinin oyuncularim icin kiralik teklifleri (en yenisi once)."""
        team = self.cm.user_team
        if team is None or self.cm.game_mode is GameMode.TOURNAMENT:
            return []
        self.db.flush()
        stmt = select(TransferDeal).where(TransferDeal.human_team_id == team.id,
                                          TransferDeal.kind == LOAN_KIND, TransferDeal.direction == OUT)
        if open_only:
            stmt = stmt.where(TransferDeal.status.in_(sorted(OPEN)))
        rows = self.db.scalars(stmt.order_by(TransferDeal.status.in_(sorted(OPEN)).desc(),
                                             TransferDeal.id.desc()).limit(max(1, min(200, int(limit)))))
        return [self._loan_view(d) for d in rows]

    def _my_loan_deal(self, deal_id) -> tuple[TransferDeal, Team, Player]:
        team = self._team()
        deal = self.desk._lock_deal(deal_id)
        if deal.human_team_id != team.id or deal.kind != LOAN_KIND or deal.direction != OUT:
            raise DeskError(LOAN_NOT_FOUND_TEXT)
        if deal.status not in OPEN or deal.turn != MANAGER:
            raise DeskError(CLOSED_TEXT.format(status=STATUS_LABELS.get(deal.status, deal.status)))
        player = self.db.get(Player, deal.player_id)
        if player is None or player.team_id != team.id:
            raise DeskError(MOVED_TEXT.format(name=player.name if player else "Oyuncu", team=team.name))
        return deal, team, player

    def accept_loan_offer(self, deal_id: int) -> LoanOfferView:
        """AI kiralik teklifini kabul et: kiralik hemen baslar (sartlar teklifte yazili)."""
        self._require_loans()
        deal, team, player = self._my_loan_deal(deal_id)
        club = self._ai_club(deal.buyer_team_id)
        self._validate_out(team, player)
        share = int(deal.loan_wage_share or 100)
        ratings = [p.overall_rating for p in club.players if p.position is player.position]
        ok, reason = loan_rules.ai_accepts_loan_in(int(player.overall_rating),
                                                   sum(ratings) / len(ratings) if ratings else None,
                                                   int(club.free_wage), int(player.current_wage or 0), share)
        if not ok:                                    # sartlar degisti: teklif duser
            self.desk._set_status(deal, VOIDED, f"{club.name}: {reason}")
            self.db.flush()
            raise DeskError(f"{club.name}: {reason}")
        start, end = self._period(deal.loan_weeks)
        self._start(team, club, player, start, end, share, 0, deal.option_fee, bool(deal.option_mandatory), deal)
        self.desk._log(deal, {"kind": "loan_done", "side": MANAGER, "weeks": deal.loan_weeks, "share": share})
        self.desk._set_status(deal, COMPLETED, f"{player.name} {club.name} kulübüne kiralık gitti.")
        deal.completed_career_week, deal.completed_season = self.cw, int(self.cm.season)
        deal.completed_week = int(self.cm.current_week)
        self.db.flush()
        return self._loan_view(deal, player)

    def reject_loan_offer(self, deal_id: int, reason: str = "") -> LoanOfferView:
        self._require_loans()
        deal, _team, player = self._my_loan_deal(deal_id)
        self.desk._set_status(deal, REJECTED, _clean(reason) or f"{player.name} için kiralık teklifi reddedildi.")
        self.db.flush()
        return self._loan_view(deal, player)

    # ------------------------------------------------------------------ geri alim maddesi

    def _buy_back_deals(self, team: Team) -> list[TransferDeal]:
        self.db.flush()
        return list(self.db.scalars(select(TransferDeal).where(
            TransferDeal.seller_team_id == team.id, TransferDeal.status == COMPLETED,
            TransferDeal.buy_back_fee.isnot(None), TransferDeal.buy_back_seasons > 0,
            TransferDeal.completed_season.isnot(None)).order_by(TransferDeal.id.desc())))

    def buy_back_options(self) -> list[BuyBackRow]:
        """Kulubumun tasidigi, hala gecerli geri alim maddeleri (sabit bedel; oyuncu hala aldigi kulupte)."""
        team = self.cm.user_team
        if team is None or self.cm.game_mode is GameMode.TOURNAMENT:
            return []
        rows: list[BuyBackRow] = []
        for deal in self._buy_back_deals(team):
            if not rules.buy_back_open(int(deal.completed_season), int(self.cm.season), int(deal.buy_back_seasons)):
                continue
            player = self.db.get(Player, deal.player_id)
            if player is None or player.team_id != deal.buyer_team_id or player.loan_from_team_id is not None:
                continue
            club = self.db.get(Team, player.team_id)
            fee = int(deal.buy_back_fee)
            reason = ""
            if fee > int(team.transfer_budget):
                reason = BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(fee))
            elif len(self._seniors(team)) >= self._squad_max():
                reason = SQUAD_FULL_TEXT.format(limit=self._squad_max())
            rows.append(BuyBackRow(
                player_id=player.id, name=player.name, position=_ev(player.position), age=int(player.age),
                team_id=player.team_id, team_name=club.name if club is not None else None, fee=fee,
                fee_text=_money(fee),
                until_season=int(deal.completed_season) + int(deal.buy_back_seasons) - 1,
                value_text=_money(int(player.market_value or 0)), affordable=not reason, reason=reason))
        return rows

    def trigger_buy_back(self, player_id: int):
        """
        Geri alim maddesini kullan: sabit bedel odenir, oyuncu kulubume doner (kulup REDDEDEMEZ). Oyuncunun
        yeni sozlesmesi icin kisisel sartlar transfer masasinda degil, dogrudan AI sozlesmesi gibi kurulur:
        madde bunu garanti eder. Donem acik olmali.
        """
        self._require_window()
        team = self._team()
        player = self.desk._player(player_id)
        deal = next((d for d in self._buy_back_deals(team) if d.player_id == player.id
                     and rules.buy_back_open(int(d.completed_season), int(self.cm.season),
                                             int(d.buy_back_seasons))), None)
        if deal is None or player.team_id == team.id or player.team_id != deal.buyer_team_id:
            raise DeskError(BUY_BACK_NONE_TEXT.format(name=player.name))
        seller = self.db.get(Team, player.team_id)
        if seller is None:
            raise DeskError(BUY_BACK_NONE_TEXT.format(name=player.name))
        if seller.id in self.cm.human_team_ids():
            raise DeskError(HUMAN_SELLER_TEXT.format(team=seller.name, name=player.name))
        fee = int(deal.buy_back_fee)
        if fee > int(team.transfer_budget):
            raise DeskError(BUDGET_TEXT.format(have=_money(team.transfer_budget), need=_money(fee)))
        if len(self._seniors(team)) >= self._squad_max():
            raise DeskError(SQUAD_FULL_TEXT.format(limit=self._squad_max()))
        if len(self._seniors(seller)) - 1 < transfers.SQUAD_FLOOR:
            raise DeskError(BUY_BACK_SQUAD_TEXT.format(team=seller.name, floor=transfers.SQUAD_FLOOR))
        rng = _rng("buyback", self.cm.season, self.cw, deal.id)
        negotiation = ContractNegotiation(rng, player, team, fee,
                                          manager_reputation=self.cm.manager_reputation_for(team))
        offer = transfers.ai_contract_offer(rng, negotiation, max(team.free_wage, negotiation.demand.wage))
        need = int(offer.wage) - int(team.free_wage)
        if need > 0:
            shift_cost = finance.weekly_to_transfer(need)
            if fee + shift_cost > int(team.transfer_budget):
                raise DeskError(WAGE_ROOM_TEXT.format(need=_money(need), cost=_money(shift_cost)))
        try:
            with self.db.begin_nested():
                if need > 0:
                    self.cm.shift_budget(team, need)
                news = self.cm.complete_transfer(team, player, fee, offer)
                deal.buy_back_fee, deal.buy_back_seasons = None, 0
                self.desk._log(deal, {"kind": "buy_back_used", "fee": fee, "club": team.id})
        except (TransferError, finance.BudgetError) as exc:
            raise DeskError(str(exc)) from exc
        self.desk._notify(None, team.id, f"Geri alım maddesi kullanıldı: {player.name}, {seller.name} "
                                         f"kulübünden {_money(fee)} karşılığında döndü.")
        return news
