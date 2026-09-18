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
Haftalik: run_week(cm, week, report) (CareerManager._run_transfer_desk). Satis hooku: settle_sell_on(cm, ...).
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

import finance
import messaging
import reputation
import staff as staff_rules
import transfer_rules as rules
import transfers
from market_rules import squad_level_refusal
from messaging import NotificationKind
from models import (
    OPEN_DEAL_STATUSES,
    Fixture,
    GameMode,
    HonourKind,
    NewsKind,
    Player,
    PlayerMatchStat,
    Position,
    ScoutAssignment,
    SeasonHonour,
    SquadRole,
    Team,
    TransferDeal,
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


def _terms_of(deal: TransferDeal) -> DealTerms:
    return DealTerms(fee=int(deal.fee), upfront=int(deal.upfront), instalment_months=int(deal.instalment_months),
                     add_ons=tuple(AddOn.from_dict(a) for a in deal.add_ons or ()),
                     sell_on_pct=int(deal.sell_on_pct), exchange_player_id=deal.exchange_player_id)


def _set_terms(deal: TransferDeal, terms: DealTerms) -> None:
    terms = rules.normalize_terms(terms)
    deal.fee, deal.upfront = int(terms.fee), terms.upfront_amount
    deal.instalment_months = int(terms.instalment_months)
    deal.add_ons = [a.to_dict() for a in terms.add_ons]
    deal.sell_on_pct = int(terms.sell_on_pct)
    deal.exchange_player_id = terms.exchange_player_id


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
            self._sell_on_share(source, seller, terms.upfront_amount, f"D{deal.id}#U")
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

    def _sell_on_share(self, source: TransferDeal, payer: Team, base_amount: int, ref: str) -> int:
        """Eski kulube, satistan ALINAN tutarin %payi (benzersiz ref: bir kez). Odenen tutar."""
        amount = int(base_amount) * int(source.sell_on_pct) // 100
        row = self._payment(deal=source, kind="SELL_ON", ref=f"SO{source.id}:{ref}", payer_id=payer.id,
                            payee_id=source.seller_team_id, amount=amount, player_id=source.player_id,
                            due_cw=self.cw, note=f"Sonraki satış payı %{int(source.sell_on_pct)}")
        return self._settle(row) if row is not None else 0

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
                    self._sell_on_share(source, payee, pay, f"{row.ref}:{before}")
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
        stmt = select(TransferDeal).where(TransferDeal.human_team_id == team.id)
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
                         terms.describe())

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
        )

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
        rows = self.db.scalars(stmt.order_by(TransferPayment.due_career_week.asc().nulls_last(), TransferPayment.id)
                               .limit(max(1, min(500, int(limit)))))
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
    cm.db.flush()


def settle_sell_on(cm: CareerManager, player: Player, seller: Team, amount: int) -> int:
    """
    CareerManager.complete_transfer hooku (masa disi satislar: AI penceresi, market_hub, eski akis): oyuncuyu masada
    'sonraki satistan pay' maddesiyle alan kulup onu sattiginda eski kulube pay (bir kez). Odenen tutar.
    """
    desk = TransferDesk(cm)
    source = desk._sell_on_source(player.id, seller.id)
    if source is None:
        return 0
    paid = desk._sell_on_share(source, seller, int(amount), f"SALE{desk.cw}")
    source.sell_on_used_career_week = desk.cw
    desk._log(source, {"kind": "sell_on_used", "amount": paid, "sale": int(amount)})
    return paid


__all__ = [
    "DeskError", "DealView", "FinanceSummary", "KnowledgeView", "PaymentView", "ScoutReportView", "TermsStep",
    "TermsView", "TransferDesk", "WindowView", "run_week", "settle_sell_on", "validate_contract",
    "AddOn", "DealTerms",
]
