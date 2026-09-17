"""
market_rules.py
===============
Menajerler arasi transfer teklifi kurallari (Faz 12 / 14. Asama, 12B). SAF modul: veritabani, Streamlit ve ORM
bilmez (yalnizca loan_rules ve world_rules gibi saf modulleri kullanir).

    OfferKind / OfferStatus / OfferAction -> transfer_offers.kind / status degerleri ve eylemler
                             (models.OFFER_KINDS / OFFER_STATUSES ile birebir ayni; test dogrular)
    STATUS_LABELS / KIND_LABELS / ACTION_LABELS / SIDE_LABELS -> Turkce etiketler (duz metin anahtarla da okunur)
    TRANSITIONS            -> acik durum tablosu: durum -> eylem -> taraf -> yeni durum
    next_state / transition-> (durum, eylem, taraf[, tur]) -> yeni durum (ve tur) ya da OfferStateError
    allowed_actions        -> tarafin bu durumda yapabilecegi eylemler (arayuz dugmeleri)
    turn_side              -> sira kimde (BUYER / SELLER / ADMIN / None)
    OfferFacts / validate_offer -> teklif anlik goruntusundeki kural ihlalleri (Turkce)
    expires_at / is_expired-> teklifin dusecegi mutlak kariyer haftasi
    squad_level_refusal    -> oyuncu alici kulubun kadro seviyesinin cok ustundeyse ret nedeni (SM kurali)
    contract_rng_seed      -> sozlesme masasinin deterministik tohumu (contract_log'dan yeniden kurulur)

Taraflar: BUYER (alici kulubun menajeri), SELLER (oyuncunun sahibi kulubun menajeri), ADMIN (dunya yoneticisi),
SYSTEM (oyun: hafta ilerlemesi, adil oyun denetimi, kulup birakma).

Durum makinesi (TRANSITIONS; TURN = sirasi gelen taraf, alici ya da satici):
    PENDING / COUNTERED  ACCEPT      TURN   -> CONTRACT     (bonservis anlasmasi; alici oyuncuyla masaya oturur)
                         REJECT      TURN   -> REJECTED
                         COUNTER     TURN   -> COUNTERED    (tur + 1; en fazla MAX_COUNTER_ROUNDS karsi teklif)
                         WITHDRAW    BUYER  -> WITHDRAWN    (alici sirasi olmasa da cekebilir)
                         EXPIRE      SYSTEM -> EXPIRED
                         VOID        SYSTEM -> VOIDED       (oyuncu tasindi / kulup birakildi / baska teklif bitti)
                         BLOCK       SYSTEM -> BLOCKED      (adil oyun: engel)
                         SEND_REVIEW SYSTEM -> REVIEW       (adil oyun: yonetici incelemesi)
    CONTRACT             COMPLETE    BUYER  -> COMPLETED
                         WITHDRAW    BUYER  -> WITHDRAWN    (alici masadan kalkti)
                         REJECT      SYSTEM -> REJECTED     (oyuncu masadan kalkti / sozlesmeyi reddetti)
                         EXPIRE / VOID / BLOCK / SEND_REVIEW  SYSTEM (yukaridaki gibi)
    REVIEW               APPROVE     ADMIN  -> CONTRACT
                         DENY        ADMIN  -> BLOCKED
                         EXPIRE      SYSTEM -> EXPIRED
                         VOID        SYSTEM -> VOIDED
    COMPLETED            REVERSE     ADMIN  -> REVERSED
    REJECTED / WITHDRAWN / EXPIRED / VOIDED / BLOCKED / REVERSED: kapali, hicbir eylem yok.

Sira ve tur: tur (transfer_offers.round) yapilan karsi teklif sayisidir. PENDING her zaman tur 0 (alici teklif etti,
satici yanitlar); COUNTERED tur 1..MAX_COUNTER_ROUNDS. Teklifi sunan taraf tur cift ise BUYER, tek ise SELLER;
sira diger taraftadir. Sunan taraf kendi teklifini kabul edemez, reddedemez, ustune karsi teklif yapamaz. Satici
hicbir zaman geri cekemez (reddeder); bonservis kabul edildikten (CONTRACT) sonra satici vazgecemez. Incelemedeki
teklif geri cekilemez (adil oyun cezasindan kacilmasin).

Tarafi belirleme (B2): oturumdaki koltugun kulubu teklifin alici kulubu ise BUYER, satici kulubu ise SELLER;
yonetici eylemleri (APPROVE / DENY / REVERSE) ADMIN ile cagrilir ve yonetici kendi kulubunun teklifini
incelememelidir (kontrolcu denetler).

validate_offer denetimleri (hepsi Turkce; bos liste = gecerli):
    kimlik (olumcul, diger denetimler yapilmaz): oyuncunun kulubu var, alici var, oyuncu kiralikta degil, satici
        hala oyuncunun kulubu, alici satici degil
    oyuncu: akademide degil, transfer yasagi yok
    bedel >= 0; alici kasasi eksi degil ve bedeli karsiliyor (butce verildiyse)
    TRANSFER: iki taraf da menajerli; kiralik suresi yok. Takas oyuncusu: istenen oyuncu degil, alicinin kadrosunda,
        kiralik / akademi / transfer yasagi yok
    LOAN: en az bir taraf menajerli; takas yok; sure None ya da MIN_LOAN_WEEKS..MAX_LOAN_WEEKS; maas payi 0..100;
        kiralayanin payi bos maas alanini asmaz (alan verildiyse)
    kadro tabani (transfers.SQUAD_FLOOR / POSITION_SALE_FLOOR, career_manager.send_to_academy ile ayni anlam: islem
        SONRASI A takimda en az squad_floor oyuncu ve mevkide en az taban kalir; islem sayiyi azaltmiyorsa
        denetlenmez): satici A takim sayisi, saticinin oyuncu mevkisi, alicinin takas oyuncusu mevkisi. 1'e 1 takasta
        alicinin toplam sayisi degismez. (AI akisi transfers.evaluate_fee bir oyuncu daha gevsek davranir.)

Kadro seviyesi (squad_level_refusal, Soccer Manager): alicinin en iyi SQUAD_LEVEL_TOP (11) oyuncusunun guc
ortalamasi oyuncunun gucunden margin'den (12) FAZLA gerideyse oyuncu gelmek istemez. Esit fark kabul edilir.
    ilk 11 ortalamasi 70: 82 guc -> gelir · 83 guc -> "Bu kadro benim seviyemde değil ..."
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING

from loan_rules import format_money, wage_split
from world_rules import INT_BOUNDS

if TYPE_CHECKING:
    from world_rules import WorldRules


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


BUYER, SELLER, ADMIN, SYSTEM = "BUYER", "SELLER", "ADMIN", "SYSTEM"
ACTOR_SIDES = (BUYER, SELLER, ADMIN, SYSTEM)
TURN = "TURN"                      # tabloda: sirasi gelen taraf (alici ya da satici)

# transfer_offers kismi benzersiz indeksindeki "acik" durumlar (models.OPEN_OFFER_STATUSES)
OPEN_STATUSES: frozenset[OfferStatus] = frozenset(
    {OfferStatus.PENDING, OfferStatus.COUNTERED, OfferStatus.CONTRACT, OfferStatus.REVIEW}
)
NEGOTIATION_STATUSES: frozenset[OfferStatus] = frozenset({OfferStatus.PENDING, OfferStatus.COUNTERED})
MAX_COUNTER_ROUNDS = 4

MIN_LOAN_WEEKS = 4                 # daha kisa kiralik yok (tek maclik "yildiz kiralama" olmasin)
MAX_LOAN_WEEKS = 52                # pratikte loan_end_week sezon sonuyla sinirlar
SQUAD_FLOOR_DEFAULT = 13           # transfers.SQUAD_FLOOR (test esitligi dogrular)
KEEPER_FLOOR_DEFAULT = 2           # transfers.POSITION_SALE_FLOOR[GK]
SQUAD_LEVEL_TOP = 11               # kadro seviyesi: en iyi 11 oyuncunun ortalamasi
SQUAD_LEVEL_MARGIN = 12

STATUS_LABELS: Mapping[OfferStatus, str] = MappingProxyType({
    OfferStatus.PENDING: "Yanıt bekliyor",
    OfferStatus.COUNTERED: "Karşı teklif",
    OfferStatus.CONTRACT: "Sözleşme masasında",
    OfferStatus.COMPLETED: "Tamamlandı",
    OfferStatus.REJECTED: "Reddedildi",
    OfferStatus.WITHDRAWN: "Geri çekildi",
    OfferStatus.EXPIRED: "Süresi doldu",
    OfferStatus.VOIDED: "Geçersiz",
    OfferStatus.BLOCKED: "Engellendi",
    OfferStatus.REVIEW: "İncelemede",
    OfferStatus.REVERSED: "Geri alındı",
})
KIND_LABELS: Mapping[OfferKind, str] = MappingProxyType({
    OfferKind.TRANSFER: "Transfer",
    OfferKind.LOAN: "Kiralık",
})
ACTION_LABELS: Mapping[OfferAction, str] = MappingProxyType({
    OfferAction.ACCEPT: "Kabul et",
    OfferAction.REJECT: "Reddet",
    OfferAction.COUNTER: "Karşı teklif yap",
    OfferAction.WITHDRAW: "Geri çek",
    OfferAction.COMPLETE: "Transferi tamamla",
    OfferAction.EXPIRE: "Süre doldu",
    OfferAction.VOID: "Geçersiz say",
    OfferAction.BLOCK: "Engelle",
    OfferAction.SEND_REVIEW: "İncelemeye gönder",
    OfferAction.APPROVE: "Onayla",
    OfferAction.DENY: "İncelemede reddet",
    OfferAction.REVERSE: "Geri al",
})
SIDE_LABELS: Mapping[str, str] = MappingProxyType({
    BUYER: "Alıcı", SELLER: "Satıcı", ADMIN: "Yönetici", SYSTEM: "Sistem",
})


class OfferStateError(ValueError):
    """Bu durumda bu eylem yapilamaz (mesaj Turkce)."""


# ===========================================================================
# 1) DURUM MAKINESI
# ===========================================================================

def _freeze(table: dict) -> Mapping:
    return MappingProxyType({
        status: MappingProxyType({action: MappingProxyType(dict(sides)) for action, sides in actions.items()})
        for status, actions in table.items()
    })


_S, _A = OfferStatus, OfferAction
_NEGOTIATION_MOVES = {
    _A.ACCEPT: {TURN: _S.CONTRACT},
    _A.REJECT: {TURN: _S.REJECTED},
    _A.COUNTER: {TURN: _S.COUNTERED},
    _A.WITHDRAW: {BUYER: _S.WITHDRAWN},
    _A.EXPIRE: {SYSTEM: _S.EXPIRED},
    _A.VOID: {SYSTEM: _S.VOIDED},
    _A.BLOCK: {SYSTEM: _S.BLOCKED},
    _A.SEND_REVIEW: {SYSTEM: _S.REVIEW},
}

TRANSITIONS: Mapping[OfferStatus, Mapping[OfferAction, Mapping[str, OfferStatus]]] = _freeze({
    _S.PENDING: _NEGOTIATION_MOVES,
    _S.COUNTERED: _NEGOTIATION_MOVES,
    _S.CONTRACT: {
        _A.COMPLETE: {BUYER: _S.COMPLETED},
        _A.WITHDRAW: {BUYER: _S.WITHDRAWN},
        _A.REJECT: {SYSTEM: _S.REJECTED},
        _A.EXPIRE: {SYSTEM: _S.EXPIRED},
        _A.VOID: {SYSTEM: _S.VOIDED},
        _A.BLOCK: {SYSTEM: _S.BLOCKED},
        _A.SEND_REVIEW: {SYSTEM: _S.REVIEW},
    },
    _S.REVIEW: {
        _A.APPROVE: {ADMIN: _S.CONTRACT},
        _A.DENY: {ADMIN: _S.BLOCKED},
        _A.EXPIRE: {SYSTEM: _S.EXPIRED},
        _A.VOID: {SYSTEM: _S.VOIDED},
    },
    _S.COMPLETED: {_A.REVERSE: {ADMIN: _S.REVERSED}},
    _S.REJECTED: {},
    _S.WITHDRAWN: {},
    _S.EXPIRED: {},
    _S.VOIDED: {},
    _S.BLOCKED: {},
    _S.REVERSED: {},
})


def _status(value) -> OfferStatus:
    try:
        return OfferStatus(getattr(value, "value", value))
    except ValueError:
        raise OfferStateError(f"Bilinmeyen teklif durumu: {value}.") from None


def _action(value) -> OfferAction:
    try:
        return OfferAction(getattr(value, "value", value))
    except ValueError:
        raise OfferStateError(f"Bilinmeyen teklif işlemi: {value}.") from None


def _side(value) -> str:
    side = str(value).strip().upper() if isinstance(value, str) else None
    if side not in ACTOR_SIDES:
        raise OfferStateError(f"Geçersiz taraf: {value}.")
    return side


def _round(status: OfferStatus, value: int | None) -> int:
    """Tur: None ise durumun ilk gecerli turu (PENDING 0, COUNTERED 1 = saticinin ilk karsi teklifi)."""
    if value is None:
        return 1 if status is OfferStatus.COUNTERED else 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNTER_ROUNDS:
        raise OfferStateError("Geçersiz pazarlık turu.")
    if status is OfferStatus.PENDING and value != 0:
        raise OfferStateError("Yanıt bekleyen ilk teklifin pazarlık turu 0 olmalı.")
    if status is OfferStatus.COUNTERED and value < 1:
        raise OfferStateError("Karşı teklifin pazarlık turu en az 1 olmalı.")
    return value


def proposer_side(round: int) -> str:
    """Bu turdaki teklifi sunan taraf: cift tur alici (ilk teklif dahil), tek tur satici."""
    return BUYER if int(round) % 2 == 0 else SELLER


def turn_side(status: OfferStatus | str, round: int | None = None) -> str | None:
    """
    Sira kimde: PENDING / COUNTERED -> teklifi sunmayan taraf; CONTRACT -> BUYER (sozlesmeyi tamamlar);
    REVIEW -> ADMIN; kapali ya da tamamlanmis teklif -> None.
    """
    st = _status(status)
    if st in NEGOTIATION_STATUSES:
        return SELLER if proposer_side(_round(st, round)) == BUYER else BUYER
    if st is OfferStatus.CONTRACT:
        return BUYER
    if st is OfferStatus.REVIEW:
        return ADMIN
    return None


def _missing_action_message(status: OfferStatus, action: OfferAction) -> str:
    if not TRANSITIONS[status]:
        return f"Teklif kapandı ({STATUS_LABELS[status]}); işlem yapılamaz."
    if status is OfferStatus.COMPLETED:
        return "Transfer tamamlandı; yalnızca dünya yöneticisi geri alabilir."
    if action is OfferAction.REVERSE:
        return "Yalnızca tamamlanmış transfer geri alınabilir."
    if action in (OfferAction.APPROVE, OfferAction.DENY):
        return "Yalnızca incelemedeki teklif onaylanır ya da reddedilir."
    if status is OfferStatus.REVIEW:
        return "Teklif yönetici incelemesinde; karar bekleniyor."
    if status is OfferStatus.CONTRACT:
        return "Bonservis anlaşması yapıldı; teklif sözleşme masasında."
    if action is OfferAction.COMPLETE:
        return "Önce bonservis anlaşması yapılmalı."
    return f"{STATUS_LABELS[status]} durumundaki teklifte bu işlem yapılamaz ({ACTION_LABELS[action]})."


def _side_message(status: OfferStatus, action: OfferAction, side: str, sides: Mapping[str, OfferStatus]) -> str:
    if side == SELLER and status is OfferStatus.CONTRACT and action in (OfferAction.REJECT, OfferAction.WITHDRAW):
        return "Bonservis anlaşması yapıldı; satıcı artık vazgeçemez."
    if side == SELLER and action is OfferAction.WITHDRAW:
        return "Satıcı teklifi geri çekemez; reddedebilirsin."
    if side == BUYER and status is OfferStatus.CONTRACT and action is OfferAction.REJECT:
        return "Sözleşme masasından kalkmak için teklifi geri çek."
    if TURN in sides:
        return "Teklife yalnızca alıcı ya da satıcı kulüp yanıt verebilir."
    if ADMIN in sides:
        return "Bu işlemi yalnızca dünya yöneticisi yapabilir."
    if SYSTEM in sides:
        return "Bu işlem oyun tarafından otomatik yapılır."
    return "Bu işlemi yalnızca alıcı kulüp yapabilir."


def _turn_message(action: OfferAction, side: str) -> str:
    if action is OfferAction.ACCEPT:
        return "Kendi teklifini kabul edemezsin; karşı tarafın yanıtını bekle."
    if action is OfferAction.REJECT:
        if side == BUYER:
            return "Kendi teklifini reddedemezsin; vazgeçtiysen geri çek."
        return "Karşı teklifin alıcının yanıtını bekliyor."
    return "Sıra karşı tarafta; yeni karşı teklif için yanıtını bekle."


def next_state(
    status: OfferStatus | str,
    action: OfferAction | str,
    actor_side: str,
    round: int | None = None,
) -> tuple[OfferStatus, int]:
    """
    (yeni durum, yeni tur). COUNTER turu bir artirir, diger eylemler turu korur. Izin verilmeyen hareket
    OfferStateError (Turkce). round None: durumun ilk gecerli turu (bkz. _round).
    """
    st, act, side = _status(status), _action(action), _side(actor_side)
    rnd = _round(st, round)
    sides = TRANSITIONS[st].get(act)
    if sides is None:
        raise OfferStateError(_missing_action_message(st, act))
    if side in sides:
        target = sides[side]
    elif TURN in sides and side in (BUYER, SELLER):
        if side != turn_side(st, rnd):
            raise OfferStateError(_turn_message(act, side))
        target = sides[TURN]
    else:
        raise OfferStateError(_side_message(st, act, side, sides))
    if act is OfferAction.COUNTER:
        if rnd >= MAX_COUNTER_ROUNDS:
            raise OfferStateError(
                f"Pazarlık turu sınırı doldu (en fazla {MAX_COUNTER_ROUNDS} karşı teklif); kabul et ya da reddet."
            )
        return target, rnd + 1
    return target, rnd


def transition(
    status: OfferStatus | str,
    action: OfferAction | str,
    actor_side: str,
    round: int | None = None,
) -> OfferStatus:
    """Durum makinesi: yeni durum ya da OfferStateError. Tur da gerekiyorsa next_state."""
    return next_state(status, action, actor_side, round)[0]


def allowed_actions(status: OfferStatus | str, actor_side: str, round: int | None = None) -> frozenset[OfferAction]:
    """Tarafin bu durumda (ve turda) yapabilecegi eylemler: OfferView.can_* bayraklari icin."""
    allowed = set()
    for action in OfferAction:
        try:
            next_state(status, action, actor_side, round)
        except OfferStateError:
            continue
        allowed.add(action)
    return frozenset(allowed)


def is_open(status: OfferStatus | str) -> bool:
    return _status(status) in OPEN_STATUSES


# ===========================================================================
# 2) TEKLIF DOGRULAMA
# ===========================================================================

@dataclass(frozen=True)
class OfferFacts:
    """
    Teklif dogrulamasi icin anlik goruntu (yalnizca sayilar ve bayraklar; ORM nesnesi degil). None verilen
    butce / maas alani / kadro sayilari denetlenmez (ornegin tamamlama oncesi yeniden dogrulamada hepsi verilir).
    """
    kind: OfferKind
    player_id: int
    player_team_id: int | None          # oyuncunun su an kayitli oldugu kulup (Player.team_id)
    seller_team_id: int | None          # teklifteki satici (ana) kulup
    buyer_team_id: int | None           # alici / kiralayan kulup
    fee: int                            # bonservis ya da kiralik bedeli (EUR)
    player_value: int = 0
    player_name: str = ""
    player_position: str = ""           # "GK" / "DEF" / "MID" / "FWD"
    player_on_loan: bool = False        # Player.loan_from_team_id dolu
    player_in_academy: bool = False
    player_ban_weeks: int = 0           # transfer yasaginin kalan haftasi (0: yasak yok)
    player_wage: int = 0                # haftalik maas (kiralik payi denetimi)
    exchange_player_id: int | None = None
    exchange_player_team_id: int | None = None
    exchange_value: int = 0
    exchange_name: str = ""
    exchange_position: str = ""
    exchange_on_loan: bool = False
    exchange_in_academy: bool = False
    exchange_ban_weeks: int = 0
    loan_weeks: int | None = None       # None: sezon sonuna kadar
    loan_wage_share: int = 100          # kiralayanin odedigi maas yuzdesi
    buyer_transfer_budget: int | None = None
    buyer_free_wage: int | None = None  # kiralayanin bos haftalik maas alani
    buyer_is_human: bool = True
    seller_is_human: bool = True
    seller_squad_size: int | None = None        # saticinin A takim sayisi (islem ONCESI, oyuncu dahil)
    seller_position_count: int | None = None    # saticida player_position mevkisindeki A takim sayisi (oyuncu dahil)
    buyer_position_count: int | None = None     # alicida exchange_position mevkisindeki A takim sayisi (takas dahil)
    squad_floor: int = SQUAD_FLOOR_DEFAULT      # transfers.SQUAD_FLOOR
    player_position_floor: int = 0              # transfers.POSITION_SALE_FLOOR.get(oyuncu mevkisi, 0)
    exchange_position_floor: int = 0            # transfers.POSITION_SALE_FLOOR.get(takas mevkisi, 0)

    @property
    def is_loan(self) -> bool:
        return str(getattr(self.kind, "value", self.kind)).upper() == OfferKind.LOAN.value

    @property
    def has_exchange(self) -> bool:
        return self.exchange_player_id is not None


def _ban_text(name: str, weeks: int) -> str:
    return f"{name} yeni transfer; {weeks} hafta daha satılamaz ya da kiralanamaz."


def _identity_problems(f: OfferFacts, name: str) -> list[str]:
    if f.player_team_id is None or f.seller_team_id is None:
        return [f"{name} bir kulübe bağlı değil."]
    if f.buyer_team_id is None:
        return ["Teklifi yapan kulüp bulunamadı."]
    if f.player_on_loan:
        return [f"{name} kiralık oyuncu; kiralık dönüşüne kadar satılamaz ya da yeniden kiralanamaz."]
    if f.player_team_id != f.seller_team_id:
        return [f"{name} artık bu kulüpte değil; teklif geçersiz."]
    if f.buyer_team_id == f.seller_team_id:
        return [f"{name} zaten senin takımında."]
    return []


def _exchange_problems(f: OfferFacts) -> list[str]:
    ex = f.exchange_name or "Takas oyuncusu"
    if f.exchange_player_id == f.player_id:
        return ["Takas oyuncusu, istenen oyuncuyla aynı olamaz."]
    if f.exchange_player_team_id != f.buyer_team_id:
        return [f"{ex} senin kadronda değil; yalnızca kendi oyuncunu takasta verebilirsin."]
    problems = []
    if f.exchange_on_loan:
        problems.append(f"{ex} kiralık oyuncu; takasta verilemez.")
    if f.exchange_in_academy:
        problems.append(f"{ex} akademide; akademi oyuncuları takasta verilemez.")
    if f.exchange_ban_weeks > 0:
        problems.append(_ban_text(ex, f.exchange_ban_weeks))
    return problems


def _loan_problems(f: OfferFacts) -> list[str]:
    problems = []
    if f.has_exchange:
        problems.append("Kiralık teklifte takas oyuncusu olamaz.")
    if f.loan_weeks is not None and not MIN_LOAN_WEEKS <= f.loan_weeks <= MAX_LOAN_WEEKS:
        problems.append(f"Kiralık süresi {MIN_LOAN_WEEKS}-{MAX_LOAN_WEEKS} hafta arasında olmalı "
                        f"(boş: sezon sonuna kadar).")
    if not 0 <= f.loan_wage_share <= 100:
        problems.append("Kiralık maaş payı %0-100 arasında olmalı.")
    elif f.buyer_free_wage is not None:
        pays = wage_split(f.player_wage, f.loan_wage_share)[0]
        if pays > 0 and pays > f.buyer_free_wage:
            problems.append(f"Maaş bütçen yetersiz: kiralık payı haftalık {format_money(pays)}, "
                            f"boş alan {format_money(max(0, f.buyer_free_wage))}.")
    return problems


def _floor_problems(f: OfferFacts) -> list[str]:
    problems = []
    same_position = f.has_exchange and f.exchange_position == f.player_position
    if f.seller_squad_size is not None:
        after = f.seller_squad_size - 1 + (1 if f.has_exchange else 0)
        if after < f.seller_squad_size and after < f.squad_floor:
            problems.append(f"Satıcı kulübün A takım kadrosu {f.squad_floor} oyuncunun altına düşer.")
    if f.player_position_floor > 0 and f.seller_position_count is not None:
        after = f.seller_position_count - 1 + (1 if same_position else 0)
        if after < f.seller_position_count and after < f.player_position_floor:
            problems.append(f"Satıcı kulüp {f.player_position} mevkisinde en az {f.player_position_floor} "
                            f"oyuncu tutmalı.")
    if f.has_exchange and f.exchange_position_floor > 0 and f.buyer_position_count is not None:
        after = f.buyer_position_count - 1 + (1 if same_position else 0)
        if after < f.buyer_position_count and after < f.exchange_position_floor:
            problems.append(f"Takastan sonra {f.exchange_position} mevkisinde en az {f.exchange_position_floor} "
                            f"oyuncun kalmalı.")
    return problems


def validate_offer(facts: OfferFacts) -> list[str]:
    """Teklifin kural ihlalleri (Turkce, sirali; bos liste = gecerli). Denetimler modul basliginda."""
    f = facts
    name = f.player_name or "Oyuncu"
    problems = _identity_problems(f, name)
    if problems:
        return problems

    if f.player_in_academy:
        problems.append(f"{name} akademide; akademi oyuncuları satılık ya da kiralık değil.")
    if f.player_ban_weeks > 0:
        problems.append(_ban_text(name, f.player_ban_weeks))

    if f.fee < 0:
        problems.append("Teklif negatif olamaz.")
    if f.buyer_transfer_budget is not None:
        if f.buyer_transfer_budget < 0:
            problems.append(f"Transfer kasası ekside ({format_money(f.buyer_transfer_budget)}); kasa artıya "
                            f"dönene kadar oyuncu alamazsın.")
        elif f.fee > f.buyer_transfer_budget:
            problems.append(f"Transfer bütçen yetersiz: {format_money(f.buyer_transfer_budget)} var, "
                            f"{format_money(f.fee)} gerekiyor.")

    if f.is_loan:
        if not (f.buyer_is_human or f.seller_is_human):
            problems.append("Kiralık anlaşmasında en az bir taraf menajerli kulüp olmalı.")
        problems.extend(_loan_problems(f))
    else:
        if not f.seller_is_human:
            problems.append("Yapay zekâ kulübüne teklif normal transfer ekranından yapılır.")
        if not f.buyer_is_human:
            problems.append("Menajerler arası teklifi yalnızca menajerli kulüp yapabilir.")
        if f.loan_weeks is not None:
            problems.append("Transfer teklifinde kiralık süresi olmaz.")
        if f.has_exchange:
            problems.extend(_exchange_problems(f))

    problems.extend(_floor_problems(f))
    return problems


# ===========================================================================
# 3) SURE, KADRO SEVIYESI, TOHUM
# ===========================================================================

DEFAULT_OFFER_EXPIRY_WEEKS = 2     # WorldRules.offer_expiry_weeks varsayilani


def expires_at(career_week: int, rules: WorldRules | None) -> int:
    """
    Teklifin dusecegi mutlak kariyer haftasi: su anki hafta + rules.offer_expiry_weeks (1-8 araligina kirpilir).
    Teklif bu haftaya ulasildiginda (career_week >= expires) SYSTEM EXPIRE ile duser. Kontrolcu her karsi teklifte
    ve kabulde (CONTRACT) suresi yeniden baslatabilir.
    """
    low, high = INT_BOUNDS["offer_expiry_weeks"]
    weeks = getattr(rules, "offer_expiry_weeks", DEFAULT_OFFER_EXPIRY_WEEKS)
    try:
        weeks = int(weeks)
    except (TypeError, ValueError):
        weeks = DEFAULT_OFFER_EXPIRY_WEEKS
    return int(career_week) + max(low, min(high, weeks))


def is_expired(expires_career_week: int | None, career_week: int) -> bool:
    """Suresi doldu mu? (None: suresiz)."""
    return expires_career_week is not None and int(career_week) >= int(expires_career_week)


def squad_level(ratings: Sequence[int], top: int = SQUAD_LEVEL_TOP) -> float | None:
    """En iyi `top` oyuncunun guc ortalamasi (bos kadro: None)."""
    best = sorted((int(r) for r in ratings), reverse=True)[: max(1, int(top))]
    return sum(best) / len(best) if best else None


def squad_level_refusal(
    player_overall: int,
    buyer_top_ratings: Sequence[int],
    margin: int = SQUAD_LEVEL_MARGIN,
) -> str | None:
    """
    Oyuncu alici kulubun kadro seviyesinin cok ustundeyse oyuncunun agzindan Turkce ret nedeni, aksi None.
    Seviye: en iyi SQUAD_LEVEL_TOP oyuncunun ortalamasi; fark margin'i ASARSA ret (esit fark kabul). Bos kadro: None.
    Karsilastirma tam sayi aritmetigiyle yapilir (ortalama yuvarlama hatasi yok).
    """
    best = sorted((int(r) for r in buyer_top_ratings), reverse=True)[:SQUAD_LEVEL_TOP]
    if not best:
        return None
    overall, gap = int(player_overall), max(0, int(margin))
    if overall * len(best) - sum(best) <= gap * len(best):
        return None
    level = sum(best) / len(best)
    return (f"Bu kadro benim seviyemde değil: ilk {len(best)} oyuncunun ortalaması {level:.0f}, "
            f"benim gücüm {overall}.")


def contract_rng_seed(offer_id: int, buyer_id: int, player_id: int) -> int:
    """Sozlesme masasinin tohumu: ayni teklif + alici + oyuncu -> ayni tohum (crc32: surumler arasi sabit)."""
    return zlib.crc32(f"contract|{int(offer_id)}|{int(buyer_id)}|{int(player_id)}".encode())
