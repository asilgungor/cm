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

    IKNA (6. Asama)
       Ikna_Skoru = Takim_Itibari x 0.4 + Menajer_Taninirligi x 0.3 + Maas_Carpani x 0.3
       Uc bilesen de 0-100'e normalize edilir (itibar zaten 1-100; tanınırlık 1-20 -> x5;
       maas carpani kirmizi cizgi %82 -> 0, tatmin %98 -> 100). Normalize edilmeseydi
       takim itibari tek basina skoru belirlerdi (40 puana karsi 6 ve 0.4 puan).

       Oyuncunun kariyer beklentisi overall'a baglidir. Kulup + menajer prestiji bu
       beklentinin altindaysa oyuncu bonservis odenmis olsa bile masaya HIC oturmaz.
       Prestij beklentiyi ne kadar asarsa oyuncu o kadar dusuk maasa ikna olur.

    MENAJER (AJAN) MASASI (13H, transfer_desk.py) -- eski akis BIREBIR ayni kalir:
       ContractOffer ek maddeleri (varsayilan 0 / None): imza primi, sadakat primi (sezonluk), menajer ucreti,
       serbest kalma bedeli, mac basi / gol primi. ContractNegotiation(agent=True) iken:
         * oyuncu paketin HAFTALIK DEGERINE bakar: maas + (imza primi + sadakat x yil) / (yil x 52)
           + primlerin beklenen haftaligi + serbest kalma maddesinin guvencesi (extras_weekly_value)
         * talep imza primi ve menajer ucreti de icerir (agent_demands); menajer ucreti talebin
           AGENT_RED_LINE'inin altina duserse menajer masayi dagitir, AGENT_HAPPY'nin altinda kabul yok
         * demand_multiplier: oyuncunun istekliligi (transfer_rules.player_interest) maas talebini olcekler
       agent=False (varsayilan) iken paket degeri = maas: kirmizi cizgi, karsi teklif ve ikna eskisiyle ayni.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

from finance import expected_wage, market_value
from models import Position, SquadRole

# --- Kulup asamasi ---------------------------------------------------------
BASE_ASKING_MARKUP = 1.15          # kulupler piyasa degerinin ustunde ister
IMPORTANCE_MARKUP = 0.45           # yildiz oyuncu icin ek prim
FEE_SHARPNESS = 5.0                # teklif/istenen orani -> kabul olasiligi keskinligi
MIN_CONSIDERED_RATIO = 0.55        # bunun altindaki teklif dogrudan reddedilir
SQUAD_FLOOR = 13                   # kadro bu sayinin altina duserse satis yapilmaz
# Mevki tabani: satistan sonra bu mevkide bu kadar oyuncu kalmayacaksa kulup satmaz
# (AI penceresi bir kulubu kalecisiz birakabiliyordu).
POSITION_SALE_FLOOR: dict[Position, int] = {Position.GK: 2}

# --- Sozlesme masasi -------------------------------------------------------
MAX_ROUNDS = 4                     # oyuncunun sabri (karsi teklif hakki)
WAGE_RED_LINE = 0.82               # talebin bu kadarinin altina duserse masadan kalkar
WAGE_HAPPY = 0.98                  # bu orani gecen teklif maas acisindan yeterli
MIN_YEARS, MAX_YEARS = 1, 5
ROLE_RANK = {SquadRole.BACKUP: 0, SquadRole.FIRST_TEAM: 1, SquadRole.STAR: 2}

# --- Ikna (6. Asama) -------------------------------------------------------
TEAM_WEIGHT, MANAGER_WEIGHT, WAGE_WEIGHT = 0.4, 0.3, 0.3
DEFAULT_MANAGER_REPUTATION = 10.0  # menajer bilgisi verilmezse (1-20)
WAGE_SCORE_AT_GATE = 85.0          # prestij tam beklenti sinirindaysa gereken maas puani
ROLE_CONFLICT_WAGE_SPIKE = 1.25    # beklenenden bir alt rol teklifinde maas talebi carpani
# --- Menajer (ajan) masasi (13H) --------------------------------------------
SIGNING_FEE_WEEKS = 6              # imza primi talebi: haftalik maasin bu kati (bonservissizde x3)
AGENT_FEE_SHARE = 0.05             # menajer ucreti talebi: bonservisin %5'i ...
AGENT_FEE_MIN_WEEKS = 4            # ... ama en az bu kadar haftalik maas
AGENT_RED_LINE = 0.5               # menajer ucreti talebin bu kadarinin altinda -> menajer masadan kalkar
AGENT_HAPPY = 0.9                  # talebin bu kadari: menajer razi
EXPECTED_APPS_PER_WEEK = {SquadRole.STAR: 0.9, SquadRole.FIRST_TEAM: 0.7, SquadRole.BACKUP: 0.3}
EXPECTED_GOALS_PER_APP = {Position.FWD: 0.45, Position.MID: 0.15, Position.DEF: 0.04, Position.GK: 0.0}
BONUS_VALUE_SHARE = 0.6            # oyuncu primlerin beklenen degerinin bu kadarini maas gibi sayar
RELEASE_CLAUSE_COMFORT = 0.03      # makul serbest kalma maddesi paketin haftalik degerine %3 ekler
RELEASE_CLAUSE_COMFORT_VALUE = 2.0  # madde piyasa degerinin bu katini asmiyorsa "makul"
CLUB_GOALS_MESSAGE = "Kulübün hedefleri benimle uyuşmuyor."
MANAGER_MESSAGE = "Bu menajerle çalışmak istemiyorum."
# Faz 13I: CM 01/02 "kulupteki statu" etiketleri (yalnizca metin; motorun uc rolu ve sure beklentisi AYNI).
# CM'de rotasyon ve yedek iki ayri duzeydir; bizde tek sure beklentisi (BACKUP) oldugu icin tek etiket. Gencler icin
# CM'nin iki gelecek duzeyi (Gelecegin umudu / Iyi bir genc) player_view.squad_status'ta gosterilir.
ROLE_LABELS = {
    SquadRole.STAR: "Vazgeçilmez",
    SquadRole.FIRST_TEAM: "Önemli ilk 11 oyuncusu",
    SquadRole.BACKUP: "Rotasyon / yedek",
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
    floor = POSITION_SALE_FLOOR.get(player.position)
    if floor is not None and sum(1 for p in available if p.position is player.position) < floor:
        return FeeDecision(False, asking, offer,
                           f"Kulüp {player.position.value} mevkisinde yedeksiz kalır, satışa kapalı.")

    prob = fee_acceptance_probability(offer, asking)
    if prob <= 0.0:
        return FeeDecision(False, asking, offer, "Teklif ciddiye alınmayacak kadar düşük.")
    if rng.random() < prob:
        return FeeDecision(True, asking, offer, "Kulüp bonservis teklifini kabul etti.")
    return FeeDecision(False, asking, offer, "Kulüp teklifi yetersiz buldu.")


# ===========================================================================
# 2) SOZLESME MASASI
# ===========================================================================

def manager_score(manager_reputation: float) -> float:
    """Menajer tanınırlığı (1-20) -> 0-100."""
    return _clamp(manager_reputation, 1.0, 20.0) * 5.0


def wage_offer_score(offer_wage: int, demand_wage: int) -> float:
    """
    Onerilen maas carpani -> 0-100. Ham oran degil, kirmizi cizgi ile tatmin noktasi
    arasina gerilmis egri: %82 -> 0, %90 -> 50, %98 ve ustu -> 100.
    """
    if demand_wage <= 0:
        return 100.0
    span = max(WAGE_HAPPY - WAGE_RED_LINE, 0.01)
    return _clamp((offer_wage / demand_wage - WAGE_RED_LINE) / span, 0.0, 1.0) * 100.0


def persuasion_score(team_reputation: int, manager_reputation: float, wage_score: float) -> float:
    """Ikna_Skoru = Takim x 0.4 + Menajer x 0.3 + Maas x 0.3 (hepsi 0-100 olcekte)."""
    return (TEAM_WEIGHT * team_reputation
            + MANAGER_WEIGHT * manager_score(manager_reputation)
            + WAGE_WEIGHT * wage_score)


def club_expectation(overall: int) -> float:
    """Oyuncunun kulup itibari beklentisi (1-100): OVR 90 -> 80, 80 -> 64, 70 -> 48."""
    return _clamp(1.6 * overall - 64, 30, 95)


def manager_expectation(overall: int) -> float:
    """Oyuncunun menajer tanınırlığı beklentisi (1-20): OVR 90 -> 12, 80 -> 7, 70 -> 2."""
    return _clamp(0.5 * overall - 33, 1, 16)


@dataclass(frozen=True)
class InterestCheck:
    """Oyuncunun masaya oturup oturmayacagi. Maastan bagimsizdir: para yildizi satin almaz."""
    interested: bool
    prestige: float               # 0.4 x takim + 0.3 x menajer
    required_prestige: float      # ayni formulle oyuncunun beklentisi
    reason: str | None

    @property
    def required_persuasion(self) -> float:
        """Imza icin gereken ikna skoru (prestij tam sinirdaysa maas puani 85 olmali)."""
        return self.required_prestige + WAGE_WEIGHT * WAGE_SCORE_AT_GATE

    @property
    def surplus(self) -> float:
        return self.prestige - self.required_prestige


def check_interest(overall: int, team_reputation: int, manager_reputation: float) -> InterestCheck:
    """
    Kulup + menajer prestiji oyuncunun beklentisini karsiliyor mu?
    Karsilamiyorsa en buyuk eksik hangisiyse onu soyler:
        kulup  -> "Kulübün hedefleri benimle uyuşmuyor."
        menajer-> "Bu menajerle çalışmak istemiyorum."
    """
    club_exp = club_expectation(overall)
    manager_exp = manager_score(manager_expectation(overall))
    prestige = TEAM_WEIGHT * team_reputation + MANAGER_WEIGHT * manager_score(manager_reputation)
    required = TEAM_WEIGHT * club_exp + MANAGER_WEIGHT * manager_exp
    if prestige >= required:
        return InterestCheck(True, prestige, required, None)
    # Acıklar formuldeki agirliklariyla karsilastirilir (kulup %40, menajer %30)
    club_gap = TEAM_WEIGHT * (club_exp - team_reputation)
    manager_gap = MANAGER_WEIGHT * (manager_exp - manager_score(manager_reputation))
    reason = CLUB_GOALS_MESSAGE if club_gap >= manager_gap else MANAGER_MESSAGE
    return InterestCheck(False, prestige, required, reason)


@dataclass(frozen=True)
class ContractOffer:
    wage: int                       # haftalik EUR
    years: int
    role: SquadRole
    # --- 13H ek maddeler (varsayilanli: eski konumsal / anahtar kullanim ve esitlik degismez) ---
    signing_fee: int = 0            # imza primi (tek sefer, kulup kasasindan oyuncuya)
    loyalty_bonus: int = 0          # sadakat primi: sozlesme suresince HER SEZON basinda
    agent_fee: int = 0              # menajer (ajan) ucreti (tek sefer)
    release_clause: int | None = None   # serbest kalma bedeli: bu bedeli oduyen kulube satis reddedilemez
    appearance_bonus: int = 0       # resmi mac basina prim
    goal_bonus: int = 0             # gol basina prim

    @property
    def has_extras(self) -> bool:
        return bool(self.signing_fee or self.loyalty_bonus or self.agent_fee or self.appearance_bonus
                    or self.goal_bonus or self.release_clause is not None)

    def extras_text(self) -> str:
        parts = []
        if self.signing_fee:
            parts.append(f"imza primi {self.signing_fee:,.0f}")
        if self.loyalty_bonus:
            parts.append(f"sadakat primi {self.loyalty_bonus:,.0f}/sezon")
        if self.agent_fee:
            parts.append(f"menajer ücreti {self.agent_fee:,.0f}")
        if self.appearance_bonus:
            parts.append(f"maç primi {self.appearance_bonus:,.0f}")
        if self.goal_bonus:
            parts.append(f"gol primi {self.goal_bonus:,.0f}")
        if self.release_clause is not None:
            parts.append(f"serbest kalma bedeli {self.release_clause:,.0f}")
        return " · ".join(parts)

    def describe(self) -> str:
        text = f"{self.wage:,.0f} EUR/hafta · {self.years} yıl · {ROLE_LABELS[self.role]}"
        return f"{text} · {self.extras_text()}" if self.has_extras else text

    def to_dict(self) -> dict:
        return {"wage": int(self.wage), "years": int(self.years), "role": self.role.value,
                "signing_fee": int(self.signing_fee), "loyalty_bonus": int(self.loyalty_bonus),
                "agent_fee": int(self.agent_fee),
                "release_clause": None if self.release_clause is None else int(self.release_clause),
                "appearance_bonus": int(self.appearance_bonus), "goal_bonus": int(self.goal_bonus)}

    @classmethod
    def from_dict(cls, data: dict) -> ContractOffer:
        clause = data.get("release_clause")
        return cls(wage=int(data["wage"]), years=int(data["years"]), role=SquadRole(data["role"]),
                   signing_fee=int(data.get("signing_fee") or 0), loyalty_bonus=int(data.get("loyalty_bonus") or 0),
                   agent_fee=int(data.get("agent_fee") or 0),
                   release_clause=None if clause is None else int(clause),
                   appearance_bonus=int(data.get("appearance_bonus") or 0),
                   goal_bonus=int(data.get("goal_bonus") or 0))


def extras_weekly_value(offer: ContractOffer, position: Position | None = None, market_value: int = 0) -> float:
    """
    Ek maddelerin oyuncu gozundeki HAFTALIK degeri (menajer masasi). Menajer ucreti oyuncuya gitmez: sayilmaz.
        imza primi + sadakat primi x yil  -> sozlesme suresine yayilir
        mac / gol primi                   -> rolun beklenen mac sayisi x prim x BONUS_VALUE_SHARE
        serbest kalma maddesi             -> makulse (<= deger x 2) maasin %3'u kadar guvence
    """
    years = max(1, int(offer.years))
    lump = (int(offer.signing_fee) + int(offer.loyalty_bonus) * years) / (years * 52)
    apps = EXPECTED_APPS_PER_WEEK.get(offer.role, 0.7)
    goals = apps * EXPECTED_GOALS_PER_APP.get(position, 0.15) if position is not None else apps * 0.15
    bonus = (int(offer.appearance_bonus) * apps + int(offer.goal_bonus) * goals) * BONUS_VALUE_SHARE
    comfort = 0.0
    if offer.release_clause is not None and market_value > 0 \
            and offer.release_clause <= market_value * RELEASE_CLAUSE_COMFORT_VALUE:
        comfort = int(offer.wage) * RELEASE_CLAUSE_COMFORT
    return lump + bonus + comfort


def agent_demands(wage: int, fee: int) -> tuple[int, int]:
    """(imza primi, menajer ucreti) talebi. Bonservissiz (serbest) imzada imza primi 3 kat istenir."""
    signing = int(wage) * SIGNING_FEE_WEEKS * (3 if int(fee) <= 0 else 1)
    agent = max(int(fee) * AGENT_FEE_SHARE, int(wage) * AGENT_FEE_MIN_WEEKS)
    return int(round(signing / 1000) * 1000), int(round(agent / 1000) * 1000)


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
        prestij: kulup + menajer beklentinin altindaysa masaya hic oturmaz
        maas   : talebin %82'si
        sure   : talebin 1 yil altina kadar
        rol    : talep ettigi rolun bir kademe altina kadar -- ama o zaman maas talebi %25 firlar

    13H menajer masasi (agent=True): maas yerine PAKETIN haftalik degeri (_value: maas + extras_weekly_value)
    kirmizi cizgi / ikna / karsi teklifte kullanilir; talep imza primi ve menajer ucreti icerir; menajer ucreti
    talebin AGENT_RED_LINE'i altindaysa menajer masayi dagitir. demand_multiplier: istekli oyuncu daha az ister.
    agent=False iken (_value == maas) davranis 13H oncesiyle BIREBIR aynidir (RNG cekimi dahil).
    """

    def __init__(
        self,
        rng,
        player,
        buyer_team,
        fee: int,
        manager_reputation: float = DEFAULT_MANAGER_REPUTATION,
        *,
        agent: bool = False,
        demand_multiplier: float = 1.0,
    ) -> None:
        self.rng = rng
        self.player = player
        self.buyer_team = buyer_team
        self.fee = fee
        self.manager_reputation = manager_reputation
        self.status = NegotiationStatus.OPEN
        self.rounds_used = 0
        self.opening_message: str | None = None
        self.agent = bool(agent)
        self._position = getattr(player, "position", None)
        self._market_value = int(getattr(player, "market_value", 0) or 0)

        self.role = suggested_role(player, buyer_team)
        wage = demanded_wage(player, buyer_team, self.role)
        if demand_multiplier != 1.0:
            wage = int(round(wage * float(demand_multiplier) / 100) * 100)
        years = demanded_years(rng, player)
        if self.agent:
            signing, agent_fee = agent_demands(wage, fee)
            self.demand = ContractOffer(wage=wage, years=years, role=self.role, signing_fee=signing,
                                        agent_fee=agent_fee)
        else:
            self.demand = ContractOffer(wage=wage, years=years, role=self.role)
        self.min_wage = int(self._value(self.demand) * WAGE_RED_LINE)
        self.min_role = self._min_role(self.role)
        # Talep hatlari: istenen rol ve (onerilirse) bir alt rol. Her hattin kendi talebi
        # ve kirmizi cizgisi vardir; alt rol hatti acilinca asil roldeki sartlar DEGISMEZ.
        self._tracks: dict[SquadRole, ContractOffer] = {self.role: self.demand}
        self._min_wages: dict[SquadRole, int] = {self.role: self.min_wage}
        self.last_offer: ContractOffer | None = None

        # Prestij kapisi: bonservis odenmis olsa bile oyuncu masaya oturmayabilir
        self.interest = check_interest(player.overall_rating, buyer_team.reputation, manager_reputation)
        if not self.interest.interested:
            self.status = NegotiationStatus.WALKED_AWAY
            self.opening_message = (
                f"{player.name}: \"{self.interest.reason}\" — sözleşme masasına oturmadı."
            )

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

    def _value(self, offer: ContractOffer):
        """Teklifin oyuncu gozundeki haftalik degeri: eski masada maasin kendisi, menajer masasinda paket."""
        if not self.agent:
            return offer.wage
        return offer.wage + extras_weekly_value(offer, self._position, self._market_value)

    def _agent_short(self, offer: ContractOffer, ratio: float) -> bool:
        return self.agent and offer.agent_fee < self.demand.agent_fee * ratio

    def _complaints(self, offer: ContractOffer) -> list[str]:
        out = []
        if self._value(offer) < self._value(self.demand) * WAGE_HAPPY:
            gap = self._value(self.demand) - self._value(offer)
            if self.agent:
                out.append(f"Paketin haftalık değeri beklentimin {gap:,.0f} EUR altında.")
            else:
                out.append(f"Maaş beklentimin {gap:,.0f} EUR altında.")
        if offer.years < self.demand.years:
            out.append(f"{self.demand.years} yıllık güvence istiyorum.")
        if ROLE_RANK[offer.role] < ROLE_RANK[self.demand.role]:
            out.append(f"Bana {ROLE_LABELS[self.demand.role]} rolü sözü verilmeli.")
        if self._agent_short(offer, AGENT_HAPPY):
            out.append(f"Menajer ücreti düşük: menajeri {self.demand.agent_fee:,.0f} EUR istiyor.")
        return out

    def persuasion(self, offer: ContractOffer) -> float:
        """Bu teklifin ikna skoru (0-100)."""
        return persuasion_score(
            self.buyer_team.reputation,
            self.manager_reputation,
            wage_offer_score(self._value(offer), self._value(self.demand)),
        )

    @property
    def required_persuasion(self) -> float:
        return self.interest.required_persuasion

    def _accepts(self, offer: ContractOffer) -> bool:
        return (
            self.persuasion(offer) >= self.required_persuasion
            and offer.years >= self.demand.years - 1
            and ROLE_RANK[offer.role] >= ROLE_RANK[self.demand.role]
            and not self._agent_short(offer, AGENT_HAPPY)
        )

    def _track_for(self, role: SquadRole) -> SquadRole:
        """Teklif hangi talep hattina ait: istenen rol (ya da ustu) veya bir alt rol."""
        return self.role if ROLE_RANK[role] >= ROLE_RANK[self.role] else role

    def respond(self, offer: ContractOffer) -> NegotiationResponse:
        """
        Menajerin teklifine oyuncunun cevabi.

        Rol celiskisi: bir alt rol ilk kez onerildiginde oyuncu o rol icin maas talebini
        %25 artirir ve bunu bildirir (bu tur hakaret sayilmaz). Asil role geri donulurse
        asil talep ve kirmizi cizgi aynen gecerlidir.
        """
        if not self.open:
            raise TransferError("Bu pazarlık kapandı.")

        self.rounds_used += 1
        self.last_offer = offer

        if offer.years < MIN_YEARS or offer.years > MAX_YEARS:
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status, f"Sözleşme süresi {MIN_YEARS}-{MAX_YEARS} yıl arasında olmalı. Görüşme bitti."
            )
        if ROLE_RANK[offer.role] < ROLE_RANK[self.min_role]:
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status,
                f"{self.player.name} kendisine biçilen '{ROLE_LABELS[offer.role]}' rolünü reddetti "
                f"ve görüşmeyi bitirdi.",
            )

        key = self._track_for(offer.role)
        spike_note: str | None = None
        if key not in self._tracks:
            base = self._tracks[self.role]
            spiked = int(round(base.wage * ROLE_CONFLICT_WAGE_SPIKE / 100) * 100)
            track = replace(base, wage=spiked, role=key) if self.agent else \
                ContractOffer(wage=spiked, years=base.years, role=key)
            self._tracks[key] = track
            self._min_wages[key] = int(self._value(track) * WAGE_RED_LINE)
            spike_note = (
                f"{self.player.name}: \"{ROLE_LABELS[self.role]} olmayacaksam bunun karşılığını isterim.\" "
                f"Maaş beklentisi {spiked:,.0f} EUR/hafta'ya fırladı."
            )
        self.demand, self.min_wage = self._tracks[key], self._min_wages[key]

        # Menajer ucreti hakaret duzeyinde -> menajer masayi dagitir (yalnizca menajer masasi)
        if self._agent_short(offer, AGENT_RED_LINE):
            self.status = NegotiationStatus.WALKED_AWAY
            return NegotiationResponse(
                self.status,
                f"{self.player.name} adına menajeri teklif edilen ücreti hakaret saydı ve görüşmeyi bitirdi "
                f"(istenen {self.demand.agent_fee:,.0f} EUR).",
            )

        # Kirmizi cizgi ihlali -> masadan kalkar (rol celiskisinin ilk aninda degil: once sartini soyler)
        if self._value(offer) < self.min_wage and spike_note is None:
            self.status = NegotiationStatus.WALKED_AWAY
            what = "paketi" if self.agent else "maaşı"
            return NegotiationResponse(
                self.status,
                f"{self.player.name} bu {what} hakaret saydı ve masadan kalktı "
                f"(kırmızı çizgi: {self.min_wage:,.0f} EUR/hafta).",
            )

        if self._value(offer) >= self.min_wage and self._accepts(offer):
            self.status = NegotiationStatus.ACCEPTED
            message = f"{self.player.name} anlaşmayı kabul etti! {offer.describe()}"
            return NegotiationResponse(self.status, message if spike_note is None else f"{spike_note} {message}")

        complaints = self._complaints(offer)
        if spike_note is not None:
            complaints.insert(0, f"Rol çelişkisi: {ROLE_LABELS[self.role]} → {ROLE_LABELS[key]} "
                                 f"(maaş talebi ×{ROLE_CONFLICT_WAGE_SPIKE:.2f})")
        if self.rounds_left == 0:
            self.status = NegotiationStatus.WALKED_AWAY
            reason = "rol tartışmasından sonra" if spike_note else "görüşmelerin uzamasından bıktı ve"
            return NegotiationResponse(
                self.status,
                f"{self.player.name} {reason} teklifi geri çevirdi.",
                complaints=complaints,
            )
        if spike_note is not None:
            return NegotiationResponse(NegotiationStatus.OPEN, spike_note, counter=self.demand, complaints=complaints)

        # Oyuncu biraz esner: talebiyle teklif arasinda yaklasir (menajer masasinda paket degeri uzerinden)
        years = self.demand.years if offer.years < self.demand.years else offer.years
        role = self.demand.role if ROLE_RANK[offer.role] < ROLE_RANK[self.demand.role] else offer.role
        if self.agent:
            credit = self._value(offer) - offer.wage
            wage = max(offer.wage, (self._value(self.demand) + self._value(offer)) / 2 - credit)
            agent_fee = offer.agent_fee
            if self._agent_short(offer, AGENT_HAPPY):
                agent_fee = int(round((self.demand.agent_fee + offer.agent_fee) / 2 / 1000) * 1000)
            counter = replace(offer, wage=int(round(wage / 100) * 100), years=years, role=role,
                              agent_fee=max(offer.agent_fee, agent_fee))
        else:
            counter = ContractOffer(
                wage=int(round(max(offer.wage, (self.demand.wage + offer.wage) / 2) / 100) * 100),
                years=years,
                role=role,
            )
        self._tracks[key] = counter
        self._min_wages[key] = min(self._min_wages[key], int(self._value(counter) * WAGE_RED_LINE))
        self.demand, self.min_wage = counter, self._min_wages[key]
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
    """AI dogrudan makul bir sozlesme sunar: talebin %98-105'i (tatmin noktasi), maas alaniyla sinirli."""
    demand = negotiation.demand
    wage = int(demand.wage * rng.uniform(0.98, 1.05))
    wage = int(round(min(wage, max(free_weekly, negotiation.min_wage)) / 100) * 100)
    return ContractOffer(wage=wage, years=demand.years, role=demand.role)
