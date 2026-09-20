"""
contracts.py
============
Sozlesme dongusu (Faz 15A) -- SAF kurallar: veritabani, ORM ve Streamlit bilmez. Rastgelelik gereken yerde tohumlu
random.Random DISARIDAN verilir (transfer_desk crc32 ile turetir); cm.rng ASLA kullanilmaz. Orkestrasyon
transfer_desk.py'de (ContractCycle: AI kulupleri + sezon devri, ContractDesk: menajer API'si); sezon kancalari
career_manager.py'de.

KURAL BAYRAGI
    CONTRACT_CYCLE  modul sabiti (CareerManager.contract_cycle ile kopya basina ezilebilir). False iken oyun 15A
                    oncesiyle BIREBIR aynidir: haftalik adim ve sezon devri adimlari hic calismaz, hicbir sorgu atilmaz.
                    Turnuva modunda dongu yoktur (sozlesme zaten ilerlemez).

SOZLESME ANLAMI (squad_planner ile ayni)
    contract_years = bu sezon DAHIL kalan yil. 1 (ya da eski kayitta 0) -> bu sezon sonunda biter; bitis sezonu =
    sezon + max(yil, 1) - 1. Sezon devrinde yil bir azalir; 0'a dusen ve yenilenmemis A takim oyuncusu SERBEST kalir.
    Yenileme N yil  -> bu sezondan SONRA N sezon (contract_years = N + 1, en fazla MAX_CONTRACT_YEARS)
    Serbest imza N  -> transferle ayni: bu sezon dahil N sezon (contract_years = N)
    On sozlesme N   -> yeni sezonda katilir, o sezondan itibaren N sezon (devirde contract_years = N)

TAKVIM (sezon uzunluguna olcekli; season_weeks = CareerManager._projected_season_weeks)
    yenileme kararlari   AI kulubu sozlesmesi biten oyunculari icin 1 .. (on sozlesme baslangici - 1) haftalari
                         arasinda TEK haftada karar verir (kulup basina crc32 haftasi: haberler sezona yayilir); en
                         istenen once, her karar gelecek sezon kadrosunu gunceller
    on sozlesme donemi   sezonun ikinci yarisi (pre_contract_start: season_weeks // 2 + 1) -- CM'nin "son 6 ay" /
                         AB 2001 (Bosman) kurali. Sozlesmesi biten oyuncu baska kulupe BEDELSIZ katilmak icin anlasir;
                         kulubu engelleyemez. Iki yon: AI -> AI, AI -> insan kulubunun oyuncusu, insan -> AI oyuncusu.
    sezon devri          on sozlesmeler uygulanir, kalan suresi biten A takim oyunculari serbest kalir (transfer_log
                         RELEASED + haber), AI kulupleri serbest oyuncu havuzundan kadrosunu tamamlar.

AI KULUBU (yenileme)
    kulup tarafi   club_renewal_score: BEKLENEN gelecek sezon kadrosuna gore guc, ilk 11 olmasi, yas, genc
                   potansiyeli, mevki derinligi. Zorunlu (sozlesmeli cekirdek kadroya gore): kalecisi 2'nin altina
                   duser ya da kadro RENEWAL_FORCE_SQUAD'in altina iner. p = sigmoid(RENEWAL_SLOPE x skor); zar
                   tohumlu.
    oyuncu tarafi  renewal_attitude: kulubu asmis (transfers.check_interest prestij kapisi), ayrilmak istiyor
                   (kaygi 3 / soz tutulmadi) -> reddeder; mutsuzluk ve hirs maas talebini buyutur. Veteranin maas
                   tabani duser (renewal_wage_basis).
SERBEST OYUNCU
    Isizlik haftasi arttikca beklenti duser: desperation_points (prestij kapisinda "daha zayif oyuncu gibi" davranir),
    free_agent_wage_factor (maas talebi carpani). AI: kadrosu SQUAD_TARGET'in altinda ya da mevkisi asgarinin
    altinda olan kulup ihtiyacina gore, kalan kulupler firsat oldugunda (belirgin guc artisi) imzalar.
FESIH
    termination_compensation: kalan maasin TERMINATION_SHARE kadari (kalan hafta: bu sezonun kalan kismi 52 haftalik
    yila olceklenir + sonraki sezonlar x 52).
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Mapping
from dataclasses import dataclass

from models import Position
from transfers import check_interest

# ===========================================================================
# 0) BAYRAK
# ===========================================================================

CONTRACT_CYCLE = True               # Faz 15A kural bayragi (kabul olcumleri yesil: varsayilan ACIK)

# ===========================================================================
# 1) TAKVIM VE SURELER
# ===========================================================================

MAX_CONTRACT_YEARS = 6              # players.contract_years CHECK 0-6
WEEKS_PER_YEAR = 52


def pre_contract_start(season_weeks: int) -> int:
    """On sozlesme (Bosman) doneminin ilk haftasi: sezonun ikinci yarisi (7 haftalik sezonda 4, 38'de 20)."""
    return max(2, int(season_weeks) // 2 + 1)


def pre_contract_open(week: int, season_weeks: int, season_finished: bool = False) -> bool:
    """Bu hafta on sozlesme yapilabilir mi? Sezon bittiyse (devre kadar) acik."""
    return bool(season_finished) or int(week) >= pre_contract_start(season_weeks)


def renewal_decision_week(team_id: int, season: int, season_weeks: int) -> int:
    """
    AI kulubunun sozlesmesi biten oyunculari icin yenileme kararlarini verdigi hafta (1 .. on sozlesme baslangici - 1;
    kulup + sezondan kalici crc32: haberler sezona yayilir). Bu haftadan sonra kulube gelen biten oyuncu hemen karara
    girer.
    """
    last = max(1, pre_contract_start(season_weeks) - 1)
    return 1 + zlib.crc32(f"renew-week|{int(season)}|{int(team_id)}".encode()) % last


def expiring(contract_years: int | None) -> bool:
    """Sozlesmesi bu sezon sonunda biter mi (1; eski kayitta suresi dolmus 0)?"""
    return int(contract_years or 0) <= 1


def expiry_season(season: int, contract_years: int | None) -> int:
    """Sozlesmenin bittigi sezon (squad_planner.contract_expiry_season ile ayni)."""
    return int(season) + max(int(contract_years or 0), 1) - 1


def renewal_contract_years(years: int) -> int:
    """N yillik yenileme: bu sezondan sonra N sezon -> contract_years = N + 1 (en fazla MAX_CONTRACT_YEARS)."""
    return max(2, min(MAX_CONTRACT_YEARS, int(years) + 1))


# ===========================================================================
# 2) KADRO YAPISI (AI hedefleri)
# ===========================================================================

# Kadro esikleri acik veri / FM dunyasi icindir; kucuk dunyada (sentetik: 15 kisilik kadro) ContractCycle AI kadrolarinin
# medyanina olcekler (_floor / _target / _safety): kurgusal test dunyasi kadro yapisini korur.
SQUAD_MIN = 20                      # AI A takimi bunun altina dusmesin (yenileme ve serbest oyuncu ihtiyaci)
SQUAD_TARGET = 22                   # AI serbest oyuncu alimi bu buyuklugu hedefler
SQUAD_MAX = 25                      # career_manager.SENIOR_SQUAD_MAX ile ayni: AI bunun ustune imza atmaz
SAFETY_SQUAD = 16                   # devirde (insan dahil) kadro bunun altina duserse en iyi bitenler 1 yil uzar
MIN_KEEPERS = 2
RENEWAL_FORCE_SQUAD = 16            # gelecek sezon SOZLESMELI kadro bunun altindaysa kulup yenilemek zorunda
POSITION_MINIMUMS: dict[Position, int] = {Position.GK: 2, Position.DEF: 5, Position.MID: 5, Position.FWD: 3}
POSITION_TARGETS: dict[Position, int] = {Position.GK: 3, Position.DEF: 7, Position.MID: 7, Position.FWD: 5}
STARTERS: dict[Position, int] = {Position.GK: 1, Position.DEF: 4, Position.MID: 4, Position.FWD: 2}


@dataclass(frozen=True)
class SquadShape:
    """Bir kulubun (gelecek sezon) kadro ozeti: mevki basina oyuncu ve guc listeleri (azalan)."""
    size: int
    counts: Mapping[Position, int]
    ratings: Mapping[Position, tuple[int, ...]]
    average: float

    @classmethod
    def of(cls, players) -> SquadShape:
        """players: (position, overall) ciftleri."""
        ratings: dict[Position, list[int]] = {pos: [] for pos in Position}
        for position, overall in players:
            ratings[position].append(int(overall))
        sorted_ratings = {pos: tuple(sorted(r, reverse=True)) for pos, r in ratings.items()}
        allr = [r for group in sorted_ratings.values() for r in group]
        return cls(len(allr), {pos: len(r) for pos, r in sorted_ratings.items()}, sorted_ratings,
                   sum(allr) / len(allr) if allr else 0.0)

    def weakest(self, position: Position) -> int | None:
        group = self.ratings.get(position) or ()
        return group[-1] if group else None

    def rank_of(self, position: Position, overall: int) -> int:
        """Bu guc mevkisinde kacinci olurdu (0 = en iyi)."""
        return sum(1 for r in self.ratings.get(position) or () if r > int(overall))

    def shortage(self, position: Position) -> int:
        """Mevki asgarisine eksik oyuncu sayisi (0: yeterli)."""
        return max(0, POSITION_MINIMUMS[position] - self.counts.get(position, 0))

    def urgent_positions(self) -> list[Position]:
        """Asgarinin altindaki mevkiler (en buyuk eksik once; esitlikte GK, DEF, MID, FWD)."""
        order = list(Position)
        short = [p for p in order if self.shortage(p) > 0]
        return sorted(short, key=lambda p: (-self.shortage(p), order.index(p)))


# ===========================================================================
# 3) AI YENILEME KARARI (kulup tarafi)
# ===========================================================================

RENEWAL_BIAS = 0.6
RENEWAL_SLOPE = 1.2
RENEWAL_REL_WEIGHT = 0.25           # beklenen kadro ortalamasina gore guc farki (OVR puani basina)
RENEWAL_STARTER = 1.0               # mevkisinin ilk 11'inde
RENEWAL_YOUTH = 1.2                 # 23 ve alti, potansiyeli gucunden en az 4 fazla
RENEWAL_THIN = 1.5                  # mevki beklenen kadroda asgarinin altinda
RENEWAL_DEEP = -0.8                 # mevki beklenen kadroda hedefin 1 fazlasi ya da daha kalabalik
RENEWAL_BIG_SQUAD = -0.6            # beklenen kadro SQUAD_TARGET + 2 ve ustu


def age_renewal_factor(age: int) -> float:
    a = int(age)
    if a <= 24:
        return 0.6
    if a <= 29:
        return 0.3
    if a <= 31:
        return -0.3
    return {32: -0.9, 33: -1.6, 34: -2.4}.get(a, -3.5)


@dataclass(frozen=True)
class RenewalScore:
    must: bool                      # zorunlu: kaleci / kadro tabani
    score: float
    probability: float
    reason: str                     # Turkce kisa gerekce (haber / gecmis)


def club_renewal_score(*, age: int, overall: int, potential: int | None, position: Position,
                       shape: SquadShape, core: SquadShape | None = None) -> RenewalScore:
    """
    AI kulubunun sozlesmesi biten oyuncusunu yenileme istegi.
        shape  BEKLENEN gelecek sezon kadrosu (bu oyuncu HARIC): bugunku A takim eksi kulubun birakmaya karar verdigi /
               reddeden / on sozlesmeyle ayrilacak oyuncular arti gelecekler. Guc, ilk 11 ve mevki derinligi buna gore.
        core   yalnizca SOZLESMELI gelecek sezon kadrosu (en az 2 yil + yenilenenler + gelecekler; bu oyuncu haric).
               Zorunlu yenileme: kalecisi MIN_KEEPERS'in altina ya da kadrosu RENEWAL_FORCE_SQUAD'in altina iner
               (34 yas ustu haric). Verilmezse shape.
    p = sigmoid(RENEWAL_SLOPE x skor); zar cagiranin tohumlu RNG'si.
    """
    position = Position(position)
    core = shape if core is None else core
    if position is Position.GK and core.counts.get(Position.GK, 0) < MIN_KEEPERS:
        return RenewalScore(True, 99.0, 1.0, "kaleci ihtiyacı")
    if core.size < RENEWAL_FORCE_SQUAD and int(age) <= 34:
        return RenewalScore(True, 99.0, 1.0, "kadro ihtiyacı")
    starter = shape.rank_of(position, overall) < STARTERS[position]
    score = RENEWAL_BIAS + RENEWAL_REL_WEIGHT * (int(overall) - shape.average)
    if starter:
        score += RENEWAL_STARTER
    score += age_renewal_factor(age)
    if int(age) <= 23 and (potential or 0) - int(overall) >= 4:
        score += RENEWAL_YOUTH
    count = shape.counts.get(position, 0)
    if count < POSITION_MINIMUMS[position]:
        score += RENEWAL_THIN
    elif count >= POSITION_TARGETS[position] + 1:
        score += RENEWAL_DEEP
    if shape.size >= SQUAD_TARGET + 2:
        score += RENEWAL_BIG_SQUAD
    probability = 1.0 / (1.0 + math.exp(-RENEWAL_SLOPE * max(-30.0, min(30.0, score))))
    reason = "ilk 11 oyuncusu" if starter else ("yaşı ilerledi" if int(age) >= 32 else "kadro planı")
    return RenewalScore(False, round(score, 3), probability, reason)


# ===========================================================================
# 4) OYUNCUNUN YENILEMEYE BAKISI
# ===========================================================================

REFUSE_WANTS_AWAY = "Kulüpten ayrılmak istiyorum; yeni sözleşme konuşmayacağım."
REFUSE_OUTGROWN = "Kariyerimde bir sonraki adımı atmak istiyorum."
ATTITUDE_LABELS = {"KEEN": "Kalmak istiyor", "OPEN": "Görüşmeye açık", "DEMANDING": "Zor ikna olur",
                   "REFUSES": "Yenilemek istemiyor"}


@dataclass(frozen=True)
class RenewalAttitude:
    refuses: bool
    level: str                      # KEEN / OPEN / DEMANDING / REFUSES
    label: str
    wage_multiplier: float
    reason: str | None


def renewal_attitude(*, overall: int, club_reputation: int, manager_reputation: float, concern_level: int = 0,
                     wants_away: bool = False, ambition: int = 10) -> RenewalAttitude:
    """
    Oyuncu kendi kulubuyle yeni sozlesme konusur mu? Ayrilmak istiyorsa (kaygi 3 ya da soz tutulmadi) ya da kulubu
    asmissa (transfers.check_interest prestij kapisi: kulup + menajer beklentisinin altinda) reddeder. Mutsuzluk ve
    hirs (gizli 1-20) maas talebini buyutur.
    """
    if wants_away or int(concern_level or 0) >= 3:
        return RenewalAttitude(True, "REFUSES", ATTITUDE_LABELS["REFUSES"], 1.0, REFUSE_WANTS_AWAY)
    gate = check_interest(int(overall), int(club_reputation), float(manager_reputation))
    if not gate.interested:
        return RenewalAttitude(True, "REFUSES", ATTITUDE_LABELS["REFUSES"], 1.0, REFUSE_OUTGROWN)
    multiplier = {0: 1.0, 1: 1.04, 2: 1.10}.get(int(concern_level or 0), 1.10)
    if int(ambition) >= 15:
        multiplier += 0.04
    if gate.surplus >= 12 and int(concern_level or 0) == 0:
        level = "KEEN"
        multiplier -= 0.03
    elif multiplier >= 1.08:
        level = "DEMANDING"
    else:
        level = "OPEN"
    return RenewalAttitude(False, level, ATTITUDE_LABELS[level], round(multiplier, 3), None)


def renewal_wage_basis(current_wage: int, age: int, overall: int, contract_overall: int | None) -> int:
    """
    Yenileme talebinde 'mevcut maas' tabani (transfers.demanded_wage mevcut maasin %5 ustunu ister): veteran ve gucu
    sozlesme anindan belirgin dusen oyuncu maas indirimini kabul eder.
    """
    factor = 1.0
    if int(age) >= 33:
        factor = 0.80
    elif int(age) >= 31:
        factor = 0.92
    if contract_overall is not None and int(contract_overall) - int(overall) >= 3:
        factor *= 0.90
    return int(round(int(current_wage or 0) * factor / 100) * 100)


# ===========================================================================
# 5) SERBEST OYUNCU
# ===========================================================================

FREE_AGENT_KNOWLEDGE = 25           # serbest oyuncularin profili menajerlerde dolasir: teklif esigi kadar bilinir
DESPERATION_WEEKS_PER_POINT = 2     # her 2 hafta issizlik beklentiyi 1 OVR puani dusurur ...
DESPERATION_MAX = 12                # ... en fazla 12
WAGE_DROP_PER_WEEK = 0.02
WAGE_FLOOR = 0.60


def weeks_free(since_career_week: int | None, now_career_week: int) -> int:
    if since_career_week is None:
        return 0
    return max(0, int(now_career_week) - int(since_career_week))


def desperation_points(weeks: int) -> int:
    """Issizlik: oyuncunun kulup beklentisi (prestij kapisi, rol ve maas) bu kadar OVR puani daha zayif gibi."""
    return min(DESPERATION_MAX, max(0, int(weeks)) // DESPERATION_WEEKS_PER_POINT)


def free_agent_wage_factor(weeks: int) -> float:
    return max(WAGE_FLOOR, 1.0 - WAGE_DROP_PER_WEEK * max(0, int(weeks)))


def expectation_overall(overall: int, weeks: int) -> int:
    """Serbest oyuncunun beklentilerinin olculdugu guc (gercek guc DEGISMEZ)."""
    return max(1, int(overall) - desperation_points(weeks))


# ===========================================================================
# 6) AI HEDEF SECIMI (on sozlesme ve serbest oyuncu)
# ===========================================================================

PRE_CONTRACT_MAX_AGE = 31
PRE_CONTRACT_MIN_FIT = 2.0
FREE_AGENT_MIN_FIT_URGENT = -99.0   # asgari altinda: kim olursa
FREE_AGENT_MIN_FIT_SIZE = -4.0      # kadro kucuk: biraz zayif derinlik oyuncusu da olur
FREE_AGENT_MIN_FIT_UPGRADE = 4.0    # firsat: mevkisinin en zayifindan en az bu kadar iyi


def age_fit_penalty(age: int) -> float:
    a = int(age)
    if a <= 26:
        return -1.0                 # genc: bonus
    if a <= 30:
        return 0.0
    return float(a - 30) * 1.2


def target_fit(*, overall: int, age: int, position: Position, shape: SquadShape) -> float:
    """
    Kulubun (gelecek sezon kadrosu shape) bu oyuncuyu ne kadar istedigi: mevkisindeki en zayiftan farki, mevki eksigi
    (asgari altinda +6, hedef altinda +2), kadro kucuklugu (+1.5) ve yas.
    """
    position = Position(position)
    weakest = shape.weakest(position)
    gap = float(int(overall) - weakest) if weakest is not None else 8.0
    fit = gap - age_fit_penalty(age)
    count = shape.counts.get(position, 0)
    if count < POSITION_MINIMUMS[position]:
        fit += 6.0
    elif count < POSITION_TARGETS[position]:
        fit += 2.0
    elif count >= POSITION_TARGETS[position] + 1:
        fit -= 3.0
    if shape.size < SQUAD_TARGET:
        fit += 1.5
    return round(fit, 3)


# ===========================================================================
# 7) FESIH
# ===========================================================================

TERMINATION_SHARE = 0.5             # fesih tazminati: kalan maasin bu kadari (karsilikli fesih)


def remaining_contract_weeks(contract_years: int, week: int, season_weeks: int, season_finished: bool) -> float:
    """Kalan sozlesme suresi (52 haftalik yil biriminde): bu sezonun kalan kismi + sonraki sezonlar."""
    sw = max(1, int(season_weeks))
    this_season = 0.0 if season_finished else WEEKS_PER_YEAR * max(0, sw - int(week) + 1) / sw
    return this_season + WEEKS_PER_YEAR * max(0, int(contract_years or 0) - 1)


def termination_compensation(wage: int, contract_years: int, week: int, season_weeks: int,
                             season_finished: bool = False, share: float = TERMINATION_SHARE) -> int:
    """Fesih tazminati (EUR, 1.000'e yuvarlanir): kalan maasin share kadari."""
    weeks = remaining_contract_weeks(contract_years, week, season_weeks, season_finished)
    return int(round(float(share) * int(wage or 0) * weeks / 1000) * 1000)


# ===========================================================================
# 8) DOSYA TURLERI, DURUMLAR VE ETIKETLER
# ===========================================================================

KIND_RENEWAL, KIND_FREE_AGENT, KIND_PRE_CONTRACT = "RENEWAL", "FREE_AGENT", "PRE_CONTRACT"
TALK_KINDS = (KIND_RENEWAL, KIND_FREE_AGENT, KIND_PRE_CONTRACT)
OPEN, AGREED, SIGNED = "OPEN", "AGREED", "SIGNED"
REFUSED, DECLINED, COLLAPSED, WITHDRAWN, EXPIRED, VOIDED = (
    "REFUSED", "DECLINED", "COLLAPSED", "WITHDRAWN", "EXPIRED", "VOIDED")
TALK_STATUSES = (OPEN, AGREED, SIGNED, REFUSED, DECLINED, COLLAPSED, WITHDRAWN, EXPIRED, VOIDED)
LIVE_TALK_STATUSES = (OPEN, AGREED)
KIND_LABELS = {KIND_RENEWAL: "Sözleşme yenileme", KIND_FREE_AGENT: "Serbest oyuncu", KIND_PRE_CONTRACT: "Ön sözleşme"}
STATUS_LABELS = {
    OPEN: "Görüşülüyor", AGREED: "Anlaşıldı", SIGNED: "İmzalandı", REFUSED: "Oyuncu reddetti",
    DECLINED: "Kulüp yenilemedi", COLLAPSED: "Görüşme bitti", WITHDRAWN: "Geri çekildi", EXPIRED: "Süresi doldu",
    VOIDED: "Geçersiz",
}

# ContractDesk.contracts satir durumlari (arayuz etiketi)
ROW_UNDER_CONTRACT, ROW_EXPIRING, ROW_TALKS, ROW_AGREED = "UNDER_CONTRACT", "EXPIRING", "TALKS", "AGREED"
ROW_REFUSED, ROW_LEAVING = "REFUSED", "LEAVING"
ROW_LABELS = {
    ROW_UNDER_CONTRACT: "Sözleşmeli", ROW_EXPIRING: "Sözleşmesi bitiyor", ROW_TALKS: "Yenileme görüşmesi",
    ROW_AGREED: "Anlaşıldı, imza bekliyor", ROW_REFUSED: "Yenilemeyi reddetti", ROW_LEAVING: "Ön sözleşme imzaladı",
}
TALK_RETRY_WEEKS = 4                # biten yenileme / serbest oyuncu gorusmesinden sonra yeni gorusme bekleme suresi
TALK_VALID_WEEKS = 3                # acik ya da imza bekleyen gorusme bu kadar hafta sonra duser


__all__ = [
    "CONTRACT_CYCLE", "RenewalAttitude", "RenewalScore", "SquadShape", "club_renewal_score", "desperation_points",
    "expectation_overall", "expiring", "expiry_season", "free_agent_wage_factor", "pre_contract_open",
    "pre_contract_start", "remaining_contract_weeks", "renewal_attitude", "renewal_contract_years",
    "renewal_decision_week", "renewal_wage_basis", "target_fit", "termination_compensation", "weeks_free",
]
