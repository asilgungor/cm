"""
transfers.py
============
Transfer pazari ve sozlesme masasi (5. Asama). SAF MANTIK: DB'ye yazmaz.
ORM nesnelerini yalnizca OKUR (duck typing).

Iki asamali akis:

    1) KULUP ASAMASI  -- evaluate_fee()
       Satici kulup, oyuncunun piyasa degeri + kadro onemi + itibar farki
       uzerinden bir istenen bedel belirler; teklif buna gore kabul/red edilir.

    2) SOZLESME MASASI -- ContractNegotiation
       Oyuncu haftalik maas, sozlesme suresi ve kadro rolu talep eder.
       Menajer karsi teklif yapar. Her talebin bir KIRMIZI CIZGISI vardir;
       teklif bunun altina duserse oyuncu masadan kalkar ve transfer iptal olur.
       Sabri da sinirlidir (MAX_ROUNDS): surekli dusuk teklif tukenmeye yol acar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from finance import expected_wage, market_value
from models import Position, SquadRole

# --- Kulup asamasi ---------------------------------------------------------
BASE_ASKING_MARKUP = 1.15          # kulupler piyasa degerinin ustunde ister
IMPORTANCE_MARKUP = 0.45           # yildiz oyuncu icin ek prim
FEE_SHARPNESS = 5.0                # teklif/istenen orani -> kabul olasiligi keskinligi
MIN_CONSIDERED_RATIO = 0.55        # bunun altindaki teklif dogrudan reddedilir
SQUAD_FLOOR = 13                   # kadro bu sayinin altina duserse satis yapilmaz

# --- Sozlesme masasi -------------------------------------------------------
MAX_ROUNDS = 4                     # oyuncunun sabri (karsi teklif hakki)
WAGE_RED_LINE = 0.82               # talebin bu kadarinin altina duserse masadan kalkar
WAGE_HAPPY = 0.98                  # bu orani gecen teklif maas acisindan yeterli
MIN_YEARS, MAX_YEARS = 1, 5
ROLE_RANK = {SquadRole.BACKUP: 0, SquadRole.FIRST_TEAM: 1, SquadRole.STAR: 2}
ROLE_LABELS = {
    SquadRole.STAR: "Yıldız",
    SquadRole.FIRST_TEAM: "As",
    SquadRole.BACKUP: "Yedek",
}


class TransferError(Exception):
    """Transfer kurali ihlali. Mesaji dogrudan kullaniciya gosterilebilir."""


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-_clamp(x, -30, 30)))


# ===========================================================================
# 1) KULUP ASAMASI
# ===========================================================================

def squad_importance(player, squad) -> float:
    """
    Oyuncunun kadrodaki onemi: 0.0 (fazlalik) - 1.0 (vazgecilmez yildiz).
    Kadro ortalamasinin ne kadar uzerinde oldugu + ilk 11'de olup olmadigi.
    """
    ratings = [p.overall_rating for p in squad] or [player.overall_rating]
    avg = sum(ratings) / len(ratings)
    edge = _clamp((player.overall_rating - avg) / 12.0, -0.5, 1.0)
    starter = 0.25 if getattr(player, "is_starter", False) else 0.0
    return _clamp(0.35 + edge * 0.55 + starter, 0.0, 1.0)


def asking_price(player, seller_team, buyer_reputation: int | None = None) -> int:
    """
    Satici kulubun istedigi bonservis (EUR).
    Piyasa degeri x (taban prim + kadro onemi primi) x itibar farki x sozlesme suresi.
    """
    value = player.market_value or market_value(player.overall_rating, player.age, player.position)
    importance = squad_importance(player, seller_team.players)
    markup = BASE_ASKING_MARKUP + IMPORTANCE_MARKUP * importance

    # Zengin/itibarli aliciya daha pahaliya satarlar
    rep_factor = 1.0
    if buyer_reputation is not None:
        rep_factor = _clamp(1.0 + (buyer_reputation - seller_team.reputation) / 220.0, 0.90, 1.22)

    # Sozlesmesi bitmek uzere olan oyuncu ucuzlar
    years = max(0, getattr(player, "contract_years", 3) or 0)
    contract_factor = _clamp(0.62 + 0.13 * years, 0.62, 1.10)

    return int(round(value * markup * rep_factor * contract_factor / 10_000) * 10_000)


@dataclass(frozen=True)
class FeeDecision:
    accepted: bool
    asking: int
    offer: int
    reason: str

    @property
    def ratio(self) -> float:
        return self.offer / self.asking if self.asking else 0.0


def fee_acceptance_probability(offer: int, asking: int) -> float:
    """Teklifin kabul edilme olasiligi. Istenen bedelde ~%50, %30 ustunde ~%95."""
    if asking <= 0:
        return 1.0
    ratio = offer / asking
    if ratio < MIN_CONSIDERED_RATIO:
        return 0.0
    return _clamp(_sigmoid((ratio - 1.0) * FEE_SHARPNESS), 0.0, 0.97)


def evaluate_fee(rng, player, seller_team, offer: int, buyer_reputation: int) -> FeeDecision:
    """1. Asama: satici kulup teklifi degerlendirir."""
    asking = asking_price(player, seller_team, buyer_reputation)

    available = [p for p in seller_team.players if p.id != player.id]
    if len(available) < SQUAD_FLOOR - 1:
        return FeeDecision(False, asking, offer, "Kadro çok daralır, kulüp satışa kapalı.")

    prob = fee_acceptance_probability(offer, asking)
    if prob <= 0.0:
        return FeeDecision(False, asking, offer, "Teklif ciddiye alınmayacak kadar düşük.")
    if rng.random() < prob:
        return FeeDecision(True, asking, offer, "Kulüp bonservis teklifini kabul etti.")
    return FeeDecision(False, asking, offer, "Kulüp teklifi yetersiz buldu.")


# ===========================================================================
# 2) SOZLESME MASASI
# ===========================================================================

@dataclass(frozen=True)
class ContractOffer:
    wage: int                       # haftalik EUR
    years: int
    role: SquadRole

    def describe(self) -> str:
        return f"{self.wage:,.0f} EUR/hafta · {self.years} yıl · {ROLE_LABELS[self.role]}"


class NegotiationStatus(str, Enum):
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    WALKED_AWAY = "WALKED_AWAY"


@dataclass
class NegotiationResponse:
    status: NegotiationStatus
    message: str
    counter: ContractOffer | None = None
    complaints: list[str] = field(default_factory=list)


def suggested_role(player, buyer_team) -> SquadRole:
    """Oyuncunun yeni kulupte kendini nerede gordugu."""
    ratings = sorted((p.overall_rating for p in buyer_team.players), reverse=True)
    if not ratings:
        return SquadRole.FIRST_TEAM
    top5 = sum(ratings[:5]) / min(5, len(ratings))
    avg = sum(ratings) / len(ratings)
    if player.overall_rating >= top5:
        return SquadRole.STAR
    if player.overall_rating >= avg - 1:
        return SquadRole.FIRST_TEAM
    return SquadRole.BACKUP


def demanded_years(rng, player) -> int:
    """Genc oyuncu uzun, yasli oyuncu kisa sozlesme ister."""
    if player.age <= 23:
        base = 5
    elif player.age <= 28:
        base = 4
    elif player.age <= 31:
        base = 3
    else:
        base = 2
    return int(_clamp(base + rng.choice((-1, 0, 0)), MIN_YEARS, MAX_YEARS))


def demanded_wage(player, buyer_team, role: SquadRole) -> int:
    """
    Talep edilen haftalik maas.
    Yeni kulubun itibari mevcut kulubunkinden dusukse "ikna primi" ister.
    """
    base = expected_wage(player.overall_rating, buyer_team.reputation, role)
    current = player.current_wage or 0
    seller_rep = player.team.reputation if player.team is not None else buyer_team.reputation
    step_down = _clamp(1.0 + (seller_rep - buyer_team.reputation) / 160.0, 0.95, 1.30)
    # Hicbir oyuncu mevcut maasinin altina imza atmak istemez
    return int(round(max(base * step_down, current * 1.05) / 100) * 100)


class ContractNegotiation:
    """
    Menajer <-> oyuncu pazarligi. Durum makinesi: her karsi teklif bir tur.

    Kirmizi cizgiler (oyuncu bunlarin altina imza atmaz):
        maas   : talebin %82'si
        sure   : 1 yil (kisa sozlesme kabul edilebilir ama mutsuz eder)
        rol    : talep ettigi rolun bir kademe altina kadar
    """

    def __init__(self, rng, player, buyer_team, fee: int) -> None:
        self.rng = rng
        self.player = player
        self.buyer_team = buyer_team
        self.fee = fee
        self.status = NegotiationStatus.OPEN
        self.rounds_used = 0

        self.role = suggested_role(player, buyer_team)
        self.demand = ContractOffer(
            wage=demanded_wage(player, buyer_team, self.role),
            years=demanded_years(rng, player),
            role=self.role,
        )
        self.min_wage = int(self.demand.wage * WAGE_RED_LINE)
        self.min_role = self._min_role(self.role)
        self.last_offer: ContractOffer | None = None

    @staticmethod
    def _min_role(role: SquadRole) -> SquadRole:
        rank = max(0, ROLE_RANK[role] - 1)
        return next(r for r, v in ROLE_RANK.items() if v == rank)

    @property
    def rounds_left(self) -> int:
        return max(0, MAX_ROUNDS - self.rounds_used)

    @property
    def open(self) -> bool:
        return self.status is NegotiationStatus.OPEN

    # ------------------------------------------------------------------

    def _complaints(self, offer: ContractOffer) -> list[str]:
        out = []
        if offer.wage < self.demand.wage * WAGE_HAPPY:
            gap = self.demand.wage - offer.wage
            out.append(f"Maaş beklentimin {gap:,.0f} EUR altında.")
        if offer.years < self.demand.years:
            out.append(f"{self.demand.years} yıllık güvence istiyorum.")
        if ROLE_RANK[offer.role] < ROLE_RANK[self.demand.role]:
            out.append(f"Bana {ROLE_LABELS[self.demand.role]} rolü sözü verilmeli.")
        return out

    def _satisfaction(self, offer: ContractOffer) -> float:
        """
        0-1 arasi memnuniyet. 0.75 uzeri imza atar.

        Maas puani HAM ORAN degil, kirmizi cizgi ile tatmin noktasi arasina
        gerilmis bir egridir. Ham oran kullanilsaydi kirmizi cizginin (%82)
        hemen ustundeki her teklif dogrudan kabul edilir, pazarlik anlamsiz
        olurdu: %82 -> 0.0, %90 -> 0.5, %98 -> 1.0.
        """
        span = max(WAGE_HAPPY - WAGE_RED_LINE, 0.01)
        ratio = offer.wage / self.demand.wage
        wage_score = _clamp((ratio - WAGE_RED_LINE) / span, 0.0, 1.25)
        years_score = _clamp(offer.years / self.demand.years, 0.4, 1.1)
        role_gap = ROLE_RANK[self.demand.role] - ROLE_RANK[offer.role]
        role_score = {0: 1.0, 1: 0.65}.get(max(0, role_gap), 1.05 if role_gap < 0 else 0.25)
        return _clamp(0.62 * wage_score + 0.18 * years_score + 0.20 * role_score, 0.0, 1.2)

    def respond(self, offer: ContractOffer) -> NegotiationResponse:
        """Menajerin teklifine oyuncunun cevabi."""
        if not self.open:
            raise TransferError("Bu pazarlık kapandı.")

        self.rounds_used += 1
        self.last_offer = offer

        if offer.years < MIN_YEARS or offer.years > MAX_YEARS:
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status, f"Sözleşme süresi {MIN_YEARS}-{MAX_YEARS} yıl arasında olmalı. Görüşme bitti."
            )

        # Kirmizi cizgi ihlali -> masadan kalkar
        if offer.wage < self.min_wage:
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status,
                f"{self.player.name} bu maaşı hakaret saydı ve masadan kalktı "
                f"(kırmızı çizgi: {self.min_wage:,.0f} EUR/hafta).",
            )
        if ROLE_RANK[offer.role] < ROLE_RANK[self.min_role]:
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status,
                f"{self.player.name} kendisine biçilen '{ROLE_LABELS[offer.role]}' rolünü reddetti "
                f"ve görüşmeyi bitirdi.",
            )

        if self._satisfaction(offer) >= 0.75:
            self.status = NegotiationStatus.ACCEPTED
            return NegotiationResponse(
                self.status, f"{self.player.name} anlaşmayı kabul etti! {offer.describe()}"
            )

        complaints = self._complaints(offer)
        if self.rounds_left == 0:
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status,
                f"{self.player.name} görüşmelerin uzamasından bıktı ve teklifi geri çevirdi.",
                complaints=complaints,
            )

        # Oyuncu biraz esner: talebiyle teklif arasinda yaklasir
        counter = ContractOffer(
            wage=int(round(max(offer.wage, (self.demand.wage + offer.wage) / 2) / 100) * 100),
            years=self.demand.years if offer.years < self.demand.years else offer.years,
            role=self.demand.role if ROLE_RANK[offer.role] < ROLE_RANK[self.demand.role] else offer.role,
        )
        self.demand = counter
        self.min_wage = min(self.min_wage, int(counter.wage * WAGE_RED_LINE))
        return NegotiationResponse(
            NegotiationStatus.OPEN,
            f"{self.player.name} karşı teklif sundu: {counter.describe()}",
            counter=counter,
            complaints=complaints,
        )


# ===========================================================================
# 3) AI KULUPLERININ TRANSFER MANTIGI
# ===========================================================================

@dataclass(frozen=True)
class SquadNeed:
    position: Position
    strength: float
    shortfall: float          # lig ortalamasina gore ne kadar geride (0 = sorun yok)


def squad_needs(team, league_average: dict[Position, float]) -> list[SquadNeed]:
    """Takimin mevki bazli zayifliklarini siralar (en acil ilk)."""
    needs: list[SquadNeed] = []
    for position, league_avg in league_average.items():
        group = [p.overall_rating for p in team.players if p.position is position]
        if not group:
            needs.append(SquadNeed(position, 0.0, league_avg))
            continue
        best = sorted(group, reverse=True)[: max(1, len(group) // 2)]
        strength = sum(best) / len(best)
        needs.append(SquadNeed(position, strength, max(0.0, league_avg - strength)))
    return sorted(needs, key=lambda n: -n.shortfall)


def league_position_average(teams, positions=tuple(Position)) -> dict[Position, float]:
    """Ligdeki mevki bazli ortalama guc (en iyi yarisi baz alinir)."""
    out: dict[Position, float] = {}
    for position in positions:
        values = [p.overall_rating for t in teams for p in t.players if p.position is position]
        out[position] = sum(values) / len(values) if values else 0.0
    return out


def target_score(player, team, need: SquadNeed) -> float:
    """
    AI'nin bir hedefi ne kadar istedigi. Mevcut kadrosundan ne kadar iyi +
    yas + mevki ihtiyaci. 0 veya altiysa ilgilenmez.
    """
    group = [p.overall_rating for p in team.players if p.position is player.position]
    current_best = max(group) if group else 0
    upgrade = player.overall_rating - current_best
    if upgrade <= 0 and need.shortfall <= 0:
        return 0.0
    age_bonus = 2.0 if player.age <= 26 else (-2.0 if player.age >= 32 else 0.0)
    return upgrade + need.shortfall * 1.5 + age_bonus


def ai_opening_offer(rng, asking: int, transfer_budget: int) -> int:
    """AI'nin ilk bonservis teklifi: istenen bedelin %85-110'u, butceyle sinirli."""
    offer = int(asking * rng.uniform(0.85, 1.10))
    return int(round(min(offer, transfer_budget) / 10_000) * 10_000)


def ai_contract_offer(rng, negotiation: ContractNegotiation, free_weekly: int) -> ContractOffer:
    """AI dogrudan makul bir sozlesme sunar: talebin %95-105'i, maas alaniyla sinirli."""
    demand = negotiation.demand
    wage = int(demand.wage * rng.uniform(0.95, 1.05))
    wage = int(round(min(wage, max(free_weekly, negotiation.min_wage)) / 100) * 100)
    return ContractOffer(wage=wage, years=demand.years, role=demand.role)
