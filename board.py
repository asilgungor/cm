"""
board.py
========
Yonetim kurulu (Faz 15C): sezon hedefi, haftalik guven, uyarilar, kovulma, sezon basi butce onerisi ve
is piyasasi (bos kulup ilanlari, basvuru, teklif, istifa).

CM 01/02 referansi: "Board Confidence" ekrani, sezon beklentisi, uyari / son uyari, kovulma, "Jobs" ilanlari.
Karsiligi burada: **sonucun bedeli var.** Hedefini tutturan menajer ASLA kovulmaz; hedefin iki kademe altinda
biten menajer buyuk olasilikla kovulur ve kulupsuz kalir; is ilanlarina basvurup yeni kulupte devam eder.

Dosya iki katmanlidir (inbox.py ile ayni desen):
    1-7. bolumler  SAF KURALLAR  -- veritabani, ORM ve Streamlit bilmez; hepsi test edilebilir saf fonksiyon
    8-9. bolumler  BoardRoom (haftalik adim + sezon degerlendirmesi) ve BoardDesk (arayuz API'si)

Sozlesmeler
-----------
* **Kural bayragi** `BOARD` (ya da `CareerManager.board`). Kapaliyken 15C oncesiyle **birebir ayni**: tek satir
  yazilmaz, tek fazladan SQL atilmaz, hicbir kulup / butce / koltuk degismez.
* **Paylasilan dunya (sahip karari K-S4):** kural paylasilan dunyada **varsayilan KAPALI**dir ve yalnizca dunya
  kurali `world_rules.board_confidence` acildiginda calisir. Kisisel kariyerde CM gibi aciktir.
* **Determinizm:** `cm.rng` HIC kullanilmaz. Her zar `crc32("board|...")` tohumlu kendi `random.Random`'i ile
  atilir; mac sonucu yoluna tek cekilis bile eklenmez.
* **Commit ETMEZ** (cagiranin islemine katilir). Haftalik ve devir adimlari `_guarded` ile kendi
  savepoint'indedir: yonetim hatasi haftayi / devri asla bozmaz.
* **Mesaj:** her yonetim olayi gelen kutusuna `inbox.KIND_BOARD` ile yazilir (15D; tur otomatik ONEMLI'dir,
  "suna kadar devam" yonetim mesajinda durur). Hafta raporu nesnesine ve `news_items`'a DOKUNULMAZ (parite).
* **K12:** menajer ic sayilari gormez. Guven 0-100 ve etiketi gosterilir; kovulma zarinin olasiligi, kulubun
  gizli hedef puani ve AI kulup kararlari gosterilmez.

Arayuz (15C-U) icin API ozeti
-----------------------------
    desk = board.BoardDesk(cm)          # ya da cm.board_desk()
    desk.enabled / desk.unemployed
    desk.state()      -> BoardView      (guven, etiket, hedef, sira, uyari, kalan sabir)
    desk.budget()     -> BudgetView     (sezon basi oneri) · desk.accept_budget()
    desk.jobs()       -> [JobAdvertView]        · desk.apply(team_id) -> ApplicationResult
    desk.offers()     -> [JobOfferView]         · desk.accept_offer(id) / desk.decline_offer(id)
    desk.resign()     · desk.history() -> [BoardView]
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from zlib import crc32

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

import finance
import inbox
import reputation
from models import (
    BOARD_OFFER_KINDS,
    BOARD_OFFER_STATUSES,
    BOARD_STATE_STATUSES,
    BOARD_TIERS,
    BoardOffer,
    BoardState,
    Player,
    SeasonStanding,
    Team,
)

log = logging.getLogger(__name__)

# Kural bayragi: kapaliyken yonetim kurulu hic calismaz ve oyun 15C oncesiyle birebir aynidir
# (CareerManager(board=False) tek kayit icin ayni etkiyi verir). Paylasilan dunyada ayrica
# world_rules.board_confidence acik olmalidir (sahip karari K-S4).
BOARD = True


class BoardError(ValueError):
    """Yonetim kurulu kurali ihlali. Mesaji dogrudan menajere gosterilebilir (Turkce)."""


# ===========================================================================
# 1) SEZON HEDEFI — kademeler ve lig kesme noktalari
# ===========================================================================

TITLE, EUROPE, TOP_HALF, MIDTABLE, SURVIVAL, RELEGATION = BOARD_TIERS
TIERS: tuple[str, ...] = BOARD_TIERS                 # en iyi -> en kotu
TARGET_TIERS: tuple[str, ...] = BOARD_TIERS[:-1]     # RELEGATION yalnizca SONUCtur, hedef olmaz

TIER_LABELS: dict[str, str] = {
    TITLE: "Şampiyonluk",
    EUROPE: "Avrupa sırası",
    TOP_HALF: "Üst yarı",
    MIDTABLE: "Orta sıra",
    SURVIVAL: "Ligde kalmak",
    RELEGATION: "Düşme hattı",
}

# Kupa beklentisi (Devler Arenasi). "WIN" = kupayi kaldir; None = beklenti yok.
CUP_WIN = "WIN"
CUP_STAGES: tuple[str, ...] = ("GROUP", "R16", "QF", "SF", "FINAL", CUP_WIN)
CUP_LABELS: dict[str, str] = {
    "GROUP": "Grup aşaması",
    "R16": "Son 16",
    "QF": "Çeyrek final",
    "SF": "Yarı final",
    "FINAL": "Final",
    CUP_WIN: "Kupayı kaldırmak",
}

# Kulup kadar guclu bir rakibi yoksa yonetim SAMPIYONLUK ister; aksi halde en fazla Avrupa sirasi.
TITLE_MARGIN = 0.10                  # 0-1 olceginde ikinciye fark (yalniz acik ara favori)
RELEGATION_SHARE = 0.15              # ligin bu payi dusme hattidir (en az 1 kulup)
EUROPE_SHARE = 0.22


@dataclass(frozen=True)
class LeagueCuts:
    """Ligin kademe sinirlari: bu sira (dahil) o kademeyi saglar."""
    size: int
    europe: int
    half: int
    mid: int
    survival: int

    def position_for(self, tier: str) -> int:
        return {TITLE: 1, EUROPE: self.europe, TOP_HALF: self.half,
                MIDTABLE: self.mid, SURVIVAL: self.survival, RELEGATION: self.size}[tier]


def league_cuts(size: int) -> LeagueCuts:
    """Lig boyuna gore kademe sinirlari (20'lik ligde 4 / 10 / 15 / 17; 6'lik ligde 2 / 3 / 5 / 5)."""
    size = max(2, int(size))
    europe = min(max(2, round(size * EUROPE_SHARE)), max(1, size - 1))
    half = max(europe, size // 2)
    mid = max(half, -(-size * 3 // 4))                       # ceil(0.75 x size)
    survival = max(mid, size - max(1, round(size * RELEGATION_SHARE)))
    return LeagueCuts(size=size, europe=europe, half=half, mid=mid, survival=survival)


def tier_index(tier: str) -> int:
    try:
        return TIERS.index(str(tier))
    except ValueError as exc:
        raise ValueError(f"Bilinmeyen hedef kademesi: {tier!r}.") from exc


def achieved_tier(position: int, size: int) -> str:
    """Sezon sonu sirasinin karsiligi olan kademe (en iyi saglanan)."""
    cuts = league_cuts(size)
    position = max(1, int(position))
    if position <= 1:
        return TITLE
    for tier in (EUROPE, TOP_HALF, MIDTABLE, SURVIVAL):
        if position <= cuts.position_for(tier):
            return tier
    return RELEGATION


def target_tier(strength_rank: int, size: int, margin: float = 0.0) -> str:
    """
    Yonetimin sezon hedefi: kulubun ligdeki GUC sirasina gore (1 = en guclu kadro + itibar).
    margin: birinci kulubun ikinciye farki (0-1). Yalnizca belirgin ustunlukte sampiyonluk istenir --
    CM'de de yonetim ancak acik ara en iyi kadroda "ligi kazan" der.
    """
    cuts = league_cuts(size)
    rank = max(1, int(strength_rank))
    if rank <= 1:
        return TITLE if float(margin) >= TITLE_MARGIN else EUROPE
    for tier in (EUROPE, TOP_HALF, MIDTABLE):
        if rank <= cuts.position_for(tier):
            return tier
    return SURVIVAL


def target_position(tier: str, size: int) -> int:
    """Hedefi saglayan EN KOTU sira (hedefe uzaklik bundan olculur)."""
    return league_cuts(size).position_for(str(tier))


def shortfall(target: str, achieved: str) -> int:
    """Hedefin kac KADEME altinda kalindi (0: tuttu, negatif: asildi, 2+: yonetim sabri biter)."""
    return tier_index(achieved) - tier_index(target)


def season_gap(target: str, position: int, size: int) -> int:
    """
    Yonetimin gordugu kademe farki. Kademe farki, hedeflenen siraya olan SIRA farkindan buyuk olamaz:
    4 kulupluk bir ligde 5 kademe 4 siraya sikistigi icin "ikinci yerine ucuncu" iki kademe sayilirdi.
    Buyuk liglerde (sira farki her zaman kademe farkindan buyuk) deger degismez.
    """
    tiers = shortfall(target, achieved_tier(position, size))
    if tiers <= 0:
        return tiers
    places = max(0, int(position) - target_position(target, size))
    return min(tiers, places)


def target_label(tier: str, size: int) -> str:
    """Menajere gosterilecek hedef metni; Avrupa sirasi ligin boyuna gore yazilir ("İlk 4")."""
    tier = str(tier)
    if tier == EUROPE:
        return f"İlk {league_cuts(size).europe}"
    if tier == TOP_HALF:
        return f"Üst yarı (ilk {league_cuts(size).half})"
    if tier == SURVIVAL:
        return f"Ligde kalmak (en kötü {league_cuts(size).survival}. sıra)"
    return TIER_LABELS.get(tier, tier)


def cup_target(strength_rank: int, size: int, *, in_cup: bool) -> str | None:
    """Kupa beklentisi (kulup turnuvadaysa): en guclu kulupler yari finali, digerleri tur atlamayi hedefler."""
    if not in_cup:
        return None
    rank = max(1, int(strength_rank))
    cuts = league_cuts(size)
    if rank <= 1:
        return "SF"
    if rank <= cuts.europe:
        return "QF"
    if rank <= cuts.half:
        return "R16"
    return "GROUP"


def cup_stage_index(stage: str | None) -> int:
    if stage is None:
        return -1
    code = str(getattr(stage, "value", stage)).strip().upper()
    return CUP_STAGES.index(code) if code in CUP_STAGES else -1


def cup_delta(target: str | None, reached: str | None) -> float:
    """Kupa beklentisinin guvene etkisi: her tur asma +, her tur geride kalma -."""
    if target is None or reached is None:
        return 0.0
    gap = cup_stage_index(reached) - cup_stage_index(target)
    if gap == 0:
        return 0.0
    return round(max(-CUP_MAX_DELTA, min(CUP_MAX_DELTA, gap * CUP_STEP_DELTA)), 2)


CUP_STEP_DELTA = 3.0
CUP_MAX_DELTA = 9.0


# ===========================================================================
# 2) HAFTALIK GUVEN (0-100)
# ===========================================================================

CONFIDENCE_MIN, CONFIDENCE_MAX = 0.0, 100.0
START_CONFIDENCE = 60.0              # goreve baslayan menajerin guveni
WARN_LEVEL = 35.0                    # ilk uyari
FINAL_LEVEL = 22.0                   # son uyari
CRITICAL_LEVEL = 15.0                # kart: iki hafta ust uste bunun altinda -> kovulma
LOW_WEEKS_TO_SACK = 2
GRACE_WEEKS = 6                      # yeni menajer / eski kayit: ilk KARIYER haftalarinda kovulma yok
MIN_SACK_SHARE = 0.12                # sezonun ilk %12'sinde yonetim sabirli (38 haftada 5, 6 haftada 2)
MIN_SACK_WEEK = 2                    # her durumda en az bu hafta beklenir

# Sonuc
RESULT_DELTA: dict[str, float] = {"W": 2.2, "D": 0.3, "L": -2.0}
UPSET_GAP = 8                        # itibar farki (kulup itibari 1-100)
UPSET_WIN_BONUS = 1.5
UPSET_LOSS_PENALTY = -1.5
HEAVY_DEFEAT_PENALTY = -1.0          # 3+ farkli yenilgi
BIG_WIN_BONUS = 0.6                  # 3+ farkli galibiyet
CUP_RESULT_SHARE = 0.6               # kupa macinin agirligi (lig maci = 1.0)

# Hedefe uzaklik
POSITION_STEP = 0.55                 # hedef sirasindan her bir sira sapma
POSITION_MAX = 3.0

# Mali durum
OVERSPEND_PENALTY = -1.2             # maas yuku havuzu asiyor
BROKE_PENALTY = -1.6                 # kasa haftalik maas yukunun BROKE_WEEKS katindan az
BROKE_WEEKS = 4
HEALTHY_BONUS = 0.3                  # kasa saglikli ve havuz asilmiyor

# Yildiz satisi
STAR_GAP = 3                         # kadronun en iyisine bu kadar yakin oyuncu "yildiz"
STAR_SALE_BASE = -5.0
STAR_SALE_MAX = -8.0
STAR_SALE_GOOD_FEE = 1.5             # piyasa degerinin bu kati -> yonetim daha az kizar
STAR_SALE_FEE_RELIEF = 3.0
STAR_SIGNING_BONUS = 2.5             # kadronun en iyisi kadar guclu oyuncu ALMAK

TROPHY_BONUS = 12.0                  # lig ya da kupa sampiyonlugu (sezon sonu)


def clamp_confidence(value: float) -> float:
    return round(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, float(value))), 2)


def apply_confidence(confidence: float, delta: float) -> float:
    return clamp_confidence(float(confidence) + float(delta))


CONFIDENCE_LABELS: tuple[tuple[float, str], ...] = (
    (CRITICAL_LEVEL, "Görevin tehlikede"),
    (FINAL_LEVEL, "Çok düşük"),
    (WARN_LEVEL, "Düşük"),
    (50.0, "Kararsız"),
    (70.0, "Olumlu"),
    (85.0, "Güçlü"),
    (CONFIDENCE_MAX + 1, "Tam destek"),
)


def confidence_label(confidence: float) -> str:
    value = clamp_confidence(confidence)
    for ceiling, text in CONFIDENCE_LABELS:
        if value < ceiling:
            return text
    return CONFIDENCE_LABELS[-1][1]


@dataclass(frozen=True)
class ConfidenceChange:
    """Haftanin guven degisimi ve menajere yazilacak Turkce gerekceler."""
    delta: float
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.delta) or bool(self.reasons)


def match_delta(outcome: str, own_reputation: int, opponent_reputation: int, goal_difference: int,
                *, cup: bool = False) -> float:
    """Tek macin yonetim guvenine etkisi. outcome: 'W' / 'D' / 'L'."""
    code = str(outcome).upper()
    if code not in RESULT_DELTA:
        raise ValueError(f"Bilinmeyen sonuç: {outcome!r} (W / D / L).")
    delta = RESULT_DELTA[code]
    gap = int(opponent_reputation) - int(own_reputation)
    if code == "W":
        if gap >= UPSET_GAP:
            delta += UPSET_WIN_BONUS
        if int(goal_difference) >= 3:
            delta += BIG_WIN_BONUS
    elif code == "L":
        if gap <= -UPSET_GAP:
            delta += UPSET_LOSS_PENALTY
        if int(goal_difference) <= -3:
            delta += HEAVY_DEFEAT_PENALTY
    return round(delta * (CUP_RESULT_SHARE if cup else 1.0), 3)


def position_delta(position: int, target: str, size: int) -> float:
    """Hedef sirasina uzaklik: hedefin ustundeyse +, altindaysa -."""
    wanted = target_position(target, size)
    gap = wanted - max(1, int(position))               # + : hedefin ustunde
    return round(max(-POSITION_MAX, min(POSITION_MAX, gap * POSITION_STEP)), 3)


def finance_delta(transfer_budget: int, wage_bill: int, wage_budget: int) -> float:
    """Mali durumun guvene etkisi: butce asimi ve bosalan kasa yonetimi rahatsiz eder."""
    delta = 0.0
    bill = max(0, int(wage_bill))
    if bill > max(0, int(wage_budget)):
        delta += OVERSPEND_PENALTY
    if bill and int(transfer_budget) < bill * BROKE_WEEKS:
        delta += BROKE_PENALTY
    if delta == 0.0:
        delta = HEALTHY_BONUS
    return round(delta, 3)


def star_sale_delta(rating: int, best_rating: int, fee: int, market_value: int) -> float:
    """Yildiz satmanin guvene etkisi (0: yildiz degil). Iyi para bir miktar telafi eder."""
    rating, best = int(rating), int(best_rating)
    if rating < best - STAR_GAP:
        return 0.0
    closeness = 1.0 - (best - rating) / float(max(1, STAR_GAP))
    delta = STAR_SALE_BASE + (STAR_SALE_MAX - STAR_SALE_BASE) * max(0.0, min(1.0, closeness))
    if market_value > 0 and int(fee) >= market_value * STAR_SALE_GOOD_FEE:
        delta += STAR_SALE_FEE_RELIEF
    return round(max(STAR_SALE_MAX, min(0.0, delta)), 3)


def star_signing_delta(rating: int, best_rating: int) -> float:
    """Kadronun en iyisi kadar guclu oyuncu almak yonetimi memnun eder."""
    return STAR_SIGNING_BONUS if int(rating) >= int(best_rating) else 0.0


def weekly_delta(
    *,
    results: Sequence[tuple[str, int, int, int, bool]] = (),
    position: int | None = None,
    target: str | None = None,
    league_size: int = 0,
    transfer_budget: int | None = None,
    wage_bill: int | None = None,
    wage_budget: int | None = None,
    sales: Sequence[tuple[str, int, int, int, int]] = (),
    signings: Sequence[tuple[str, int, int]] = (),
) -> ConfidenceChange:
    """
    Haftanin toplam guven degisimi.
        results  : (outcome, kendi itibari, rakip itibari, averaj farki, kupa mu)
        sales    : (oyuncu adi, guc, kadronun en iyisi, bonservis, piyasa degeri)
        signings : (oyuncu adi, guc, kadronun en iyisi)
    Donus: toplam degisim + menajere yazilacak gerekceler (Turkce, sirali).
    """
    total = 0.0
    reasons: list[str] = []
    for outcome, own_rep, opp_rep, goal_diff, cup in results:
        value = match_delta(outcome, own_rep, opp_rep, goal_diff, cup=cup)
        total += value
        word = {"W": "Galibiyet", "D": "Beraberlik", "L": "Mağlubiyet"}[str(outcome).upper()]
        reasons.append(f"{word}{' (kupa)' if cup else ''}: {value:+.1f}")
    if position is not None and target is not None and league_size > 0:
        value = position_delta(position, target, league_size)
        total += value
        wanted = target_position(target, league_size)
        reasons.append(f"Sıra {int(position)}. (hedef en kötü {wanted}.): {value:+.1f}")
    if transfer_budget is not None and wage_bill is not None and wage_budget is not None:
        value = finance_delta(transfer_budget, wage_bill, wage_budget)
        total += value
        reasons.append(f"Mali durum: {value:+.1f}")
    for name, rating, best, fee, value_ in sales:
        value = star_sale_delta(rating, best, fee, value_)
        if value:
            total += value
            reasons.append(f"{name} satıldı: {value:+.1f}")
    for name, rating, best in signings:
        value = star_signing_delta(rating, best)
        if value:
            total += value
            reasons.append(f"{name} transfer edildi: {value:+.1f}")
    return ConfidenceChange(round(total, 3), tuple(reasons))


# ===========================================================================
# 3) UYARI VE KOVULMA
# ===========================================================================

WARN_NONE, WARN_FIRST, WARN_FINAL = 0, 1, 2
WARNING_LABELS: dict[int, str] = {
    WARN_NONE: "Uyarı yok",
    WARN_FIRST: "Uyarı aldın",
    WARN_FINAL: "Son uyarı",
}

SACK_MIN_PROBABILITY = 0.70          # kart: hedefin 2+ kademe altinda -> en az %70
SACK_STEP = 0.10                     # her ek kademe
SACK_CONFIDENCE_WEIGHT = 0.20
SACK_ONE_TIER_BASE = 0.20
SACK_ONE_TIER_CONFIDENCE = 0.45
FORM_LOW_SHARE, FORM_HIGH_SHARE = 0.25, 0.70   # form_confidence: puan orani bandi
FIRST_SEASON_MERCY = 0.6             # goreve bu sezon baslayan menajere bir kademe tolerans
SACK_PROBABILITY_MAX = 0.97


def warning_level(confidence: float) -> int:
    """Guvenin karsiligi olan uyari kademesi (yalnizca yukari dogru islenir)."""
    value = clamp_confidence(confidence)
    if value < FINAL_LEVEL:
        return WARN_FINAL
    if value < WARN_LEVEL:
        return WARN_FIRST
    return WARN_NONE


def form_confidence(points: int, played: int, win_points: int = 3) -> float:
    """
    Sonuclardan turetilen yonetim guveni (0-100): haftalik guveni SAKLANMAYAN AI kulupleri icin.
    Puan orani %25 -> 0, %70 -> 100 (ligin kendi icinde: 38 maclik sezonda 1,05 puan/mac dipte, 2,1 zirvede).
    """
    total = max(1, int(played)) * max(1, int(win_points))
    share = max(0, int(points)) / total
    return clamp_confidence(100.0 * (share - FORM_LOW_SHARE) / (FORM_HIGH_SHARE - FORM_LOW_SHARE))


def sack_probability(gap: int, confidence: float, *, first_season: bool = False,
                     relegated: bool = False) -> float:
    """
    Sezon sonu kovulma olasiligi. gap = shortfall(hedef, ulasilan).
        <= 0  hedef tuttu ya da asildi -> 0.0 (KABUL: hedefine ulasan menajer asla kovulmaz)
        == 1  bir kademe geride        -> guvene gore %20-65 (ilk sezonda daha musamahali)
        >= 2  iki+ kademe geride       -> EN AZ %70 (kart), guven dustukce %97'ye kadar
    relegated: kulup DUSME HATTINDA bitti. Futbolda bu tek basina kovulma sebebidir: hedefin bir kademe
    gerisinde kalmis olsa bile iki kademe formulu uygulanir (yani en az %70). Hedefini tutturan menajer
    yine de kovulmaz (gap <= 0 once denetlenir).
    """
    gap = int(gap)
    if gap <= 0:
        return 0.0
    lack = 1.0 - clamp_confidence(confidence) / 100.0
    if gap == 1 and not relegated:
        p = SACK_ONE_TIER_BASE + SACK_ONE_TIER_CONFIDENCE * lack
        if first_season:
            p *= FIRST_SEASON_MERCY
        return round(max(0.0, min(SACK_PROBABILITY_MAX, p)), 4)
    p = SACK_MIN_PROBABILITY + SACK_STEP * max(0, gap - 2) + SACK_CONFIDENCE_WEIGHT * lack
    return round(max(SACK_MIN_PROBABILITY, min(SACK_PROBABILITY_MAX, p)), 4)


def min_sack_week(season_weeks: int) -> int:
    """Sezonun ilk haftalarinda yonetim sabirlidir; esik sezon uzunluguna olceklenir."""
    weeks = max(1, int(season_weeks or 0))
    return max(MIN_SACK_WEEK, round(weeks * MIN_SACK_SHARE))


def sack_now(confidence: float, low_weeks: int, gap: int, *, week: int, weeks_in_charge: int,
             season_weeks: int = 38) -> bool:
    """
    Sezon ICINDE kovulma (kart): guven CRITICAL_LEVEL'in altinda iki hafta ust uste.
    Hedefini SAGLAYAN menajer (gap <= 0) hicbir kosulda kovulmaz; yeni menajere ve sezon basina tolerans var.
    """
    if int(gap) <= 0:
        return False
    if int(week) < min_sack_week(season_weeks) or int(weeks_in_charge) < GRACE_WEEKS:
        return False
    return clamp_confidence(confidence) < CRITICAL_LEVEL and int(low_weeks) >= LOW_WEEKS_TO_SACK


def season_end_confidence(confidence: float, gap: int, *, trophies: int = 0, cup_gap: float = 0.0) -> float:
    """Sezon kapanisinda guven: siralamanin toplu etkisi, kupa beklentisi ve kupalar."""
    value = float(confidence) - SEASON_GAP_WEIGHT * max(0, int(gap)) + SEASON_BEAT_BONUS * max(0, -int(gap))
    value += float(cup_gap) + TROPHY_BONUS * max(0, int(trophies))
    return clamp_confidence(value)


SEASON_GAP_WEIGHT = 14.0
SEASON_BEAT_BONUS = 8.0

# Kovulmanin / istifanin menajer taninirligina bedeli (reputation.apply ile uygulanir)
SACKED_REPUTATION = reputation.SACKED_DELTA
RESIGNED_REPUTATION = reputation.RESIGNED_DELTA


# ===========================================================================
# 4) SEZON BASI BUTCE ONERISI
# ===========================================================================

TIER_BUDGET_BONUS: dict[str, float] = {
    TITLE: 0.30, EUROPE: 0.20, TOP_HALF: 0.12, MIDTABLE: 0.06, SURVIVAL: 0.02,
}
BUDGET_CONFIDENCE_SHARE = 0.20
BUDGET_ROUNDING = 100_000
WAGE_HEADROOM = 1.08                 # yeni havuz en az maas yukunun bu kati
WAGE_ROUNDING = 1_000


@dataclass(frozen=True)
class BudgetPlan:
    """
    Yonetimin sezon basi onerisi (menajer kabul edene kadar uygulanmaz). CM'deki gibi bu bir HEDEF SEVIYEDIR,
    her sezon ust uste EKLENEN para degil: kabul edilince kasa bu seviyeye TAMAMLANIR (zaten ustundeyse
    dokunulmaz), maas havuzu da yukari cekilir. Kasadan ya da havuzdan asla para ALINMAZ.
    """
    transfer_budget: int             # sezonun hedef transfer kasasi (EUR)
    wage_budget: int                 # onerilen HAFTALIK maas havuzu (EUR)

    def grant_over(self, current_budget: int) -> int:
        """Bu oneri kabul edilirse kasaya eklenecek tutar."""
        return max(0, int(self.transfer_budget) - max(0, int(current_budget)))

    @property
    def any(self) -> bool:
        return self.transfer_budget > 0 or self.wage_budget > 0


def budget_proposal(club_reputation: int, confidence: float, target: str, *,
                    transfer_budget: int, wage_bill: int, wage_budget: int) -> BudgetPlan:
    """
    Sezon basi butce onerisi: kulup itibarindan gelen taban x (hedefin hirsi + yonetimin guveni).
    Kasa zaten bu seviyenin ustundeyse oneri onu korur (para alinmaz, ust uste eklenmez).
    """
    base = finance.transfer_budget_for_reputation(max(1, int(club_reputation)))
    share = TIER_BUDGET_BONUS.get(str(target), 0.06) + BUDGET_CONFIDENCE_SHARE * (clamp_confidence(confidence) / 100.0)
    wanted_cash = int(round(base * share / BUDGET_ROUNDING) * BUDGET_ROUNDING)
    wanted_wage = int(round(max(int(wage_budget), int(wage_bill) * WAGE_HEADROOM) / WAGE_ROUNDING) * WAGE_ROUNDING)
    return BudgetPlan(transfer_budget=max(0, max(int(transfer_budget), wanted_cash)),
                      wage_budget=max(int(wage_budget), wanted_wage))


# ===========================================================================
# 5) IS PIYASASI KURALLARI
# ===========================================================================

ADVERT_WEEKS = 8                     # ilan bu kadar kariyer haftasi acik kalir, sonra kulup baskasini bulur
OFFER_WEEKS = 4                      # menajere gelen teklifin gecerlilik suresi
MIN_OPEN_JOBS = 3                    # kulupsuz menajer icin acik tutulan en az ilan sayisi
MAX_PENDING_OFFERS = 3
JOB_TOLERANCE = 3.5                  # kulubun bekledigi taninirlik = AI karsiligi - tolerans
MIN_JOB_REPUTATION = reputation.MIN_REPUTATION
UNEMPLOYED_RELIEF_PER_WEEK = 0.12    # issizlik beklentiyi dusurur (15A'nin "desperation" deseni)
UNEMPLOYED_RELIEF_MAX = 3.0
APPLY_BASE = 0.15
APPLY_GAP_WEIGHT = 0.22
APPLY_MIN, APPLY_MAX = 0.05, 0.90
APPROACH_MIN_CONFIDENCE = 70.0
APPROACH_BASE = 0.35
APPROACH_GAP_WEIGHT = 0.15

OFFER_ADVERT, OFFER_APPROACH = BOARD_OFFER_KINDS
OFFER_PENDING, OFFER_ACCEPTED, OFFER_DECLINED, OFFER_EXPIRED, OFFER_WITHDRAWN = BOARD_OFFER_STATUSES
LIVE_OFFER_STATUSES: tuple[str, ...] = (OFFER_PENDING,)

STATE_ACTIVE, STATE_SACKED, STATE_RESIGNED, STATE_LEFT = BOARD_STATE_STATUSES
STATE_LABELS: dict[str, str] = {
    STATE_ACTIVE: "Görevde",
    STATE_SACKED: "Görevine son verildi",
    STATE_RESIGNED: "İstifa etti",
    STATE_LEFT: "Kulüpten ayrıldı",
}

BUDGET_NONE, BUDGET_PENDING, BUDGET_ACCEPTED = "NONE", "PENDING", "ACCEPTED"


def unemployed_relief(weeks: int) -> float:
    """Kulupsuz gecen her hafta kuluplerin beklentisini bir miktar dusurur (en fazla UNEMPLOYED_RELIEF_MAX)."""
    return round(min(UNEMPLOYED_RELIEF_MAX, max(0, int(weeks)) * UNEMPLOYED_RELIEF_PER_WEEK), 3)


def required_reputation(club_reputation: int, *, unemployed_weeks: int = 0) -> float:
    """Kulubun menajerinden bekledigi taninirlik (1-20)."""
    need = reputation.ai_manager_reputation(int(club_reputation)) - JOB_TOLERANCE
    need -= unemployed_relief(unemployed_weeks)
    return round(max(MIN_JOB_REPUTATION, need), 2)


def job_eligible(manager_reputation: float, club_reputation: int, *, unemployed_weeks: int = 0) -> bool:
    """Menajerin taninirligi bu kulupten teklif almaya yetiyor mu?"""
    return float(manager_reputation) >= required_reputation(club_reputation, unemployed_weeks=unemployed_weeks)


def application_chance(manager_reputation: float, club_reputation: int, *, unemployed_weeks: int = 0) -> float:
    """Basvurunun kabul edilme olasiligi: taninirlik kulubun beklentisini ne kadar asiyorsa o kadar yuksek."""
    need = required_reputation(club_reputation, unemployed_weeks=unemployed_weeks)
    gap = float(manager_reputation) - need
    if gap < 0:
        return 0.0
    return round(max(APPLY_MIN, min(APPLY_MAX, APPLY_BASE + APPLY_GAP_WEIGHT * gap)), 4)


def approach_chance(manager_reputation: float, club_reputation: int, confidence: float, gap: int) -> float:
    """Isini iyi yapan menajere baska kulubun teklif gotturme olasiligi (gap < 0: hedefi asti)."""
    if clamp_confidence(confidence) < APPROACH_MIN_CONFIDENCE and int(gap) >= 0:
        return 0.0
    if not job_eligible(manager_reputation, club_reputation):
        return 0.0
    margin = float(manager_reputation) - required_reputation(club_reputation)
    chance = APPROACH_BASE + APPROACH_GAP_WEIGHT * margin + 0.10 * max(0, -int(gap))
    return round(max(0.0, min(APPLY_MAX, chance)), 4)


# ===========================================================================
# 6) TOHUM (determinizm)
# ===========================================================================

def seed_of(*parts) -> int:
    """crc32 tohumu: Python'un tuzlu hash()'i asla kullanilmaz (yol haritasi §1)."""
    return crc32("|".join(str(p) for p in ("board", *parts)).encode("utf-8"))


def dice(*parts) -> random.Random:
    return random.Random(seed_of(*parts))


def roll(chance: float, *parts) -> bool:
    """Tohumlu zar: ayni girdiyle her zaman ayni sonuc."""
    return dice(*parts).random() < max(0.0, min(1.0, float(chance)))


# ===========================================================================
# 7) GORUNUMLER (arayuz; duz veri)
# ===========================================================================

@dataclass(frozen=True)
class BoardView:
    """Yonetim kurulu ekraninin tek satirlik ozeti."""
    season: int
    team_id: int | None
    team_name: str
    confidence: float
    confidence_label: str
    target: str
    target_label: str
    cup_target: str | None
    cup_target_label: str | None
    position: int | None
    league_size: int
    gap: int                          # + : hedefin altinda
    warning: int
    warning_label: str
    status: str
    status_label: str
    low_weeks: int
    weeks_in_charge: int
    since_week: int
    reasons: tuple[str, ...] = ()

    @property
    def safe(self) -> bool:
        return self.gap <= 0

    @property
    def in_danger(self) -> bool:
        return self.confidence < WARN_LEVEL or self.warning >= WARN_FIRST


@dataclass(frozen=True)
class BudgetView:
    season: int
    team_id: int | None
    transfer_budget: int             # yonetimin hedefledigi sezon kasasi
    transfer_grant: int              # kabul edilirse kasaya eklenecek tutar (0: kasa zaten yeterli)
    wage_budget: int
    status: str
    accepted: bool
    text: str


@dataclass(frozen=True)
class JobAdvertView:
    team_id: int
    team_name: str
    league_id: int | None
    league_name: str
    reputation: int
    squad_size: int
    squad_value: int
    open_since_week: int
    closes_in_weeks: int
    required_reputation: float
    eligible: bool
    applied: bool
    reason: str = ""


@dataclass(frozen=True)
class JobOfferView:
    id: int
    team_id: int
    team_name: str
    league_name: str
    reputation: int
    kind: str
    kind_label: str
    status: str
    season: int
    week: int
    expires_in_weeks: int | None
    text: str


@dataclass(frozen=True)
class ApplicationResult:
    accepted: bool
    text: str
    offer: JobOfferView | None = None


OFFER_KIND_LABELS: dict[str, str] = {
    OFFER_ADVERT: "Başvurun kabul edildi",
    OFFER_APPROACH: "Kulüp seni istiyor",
}


# ===========================================================================
# 8) BoardRoom — haftalik adim ve sezon degerlendirmesi (veritabani katmani)
# ===========================================================================

def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass
class _ClubFacts:
    """Bir kulubun hedef hesabinda kullanilan sayilar (tek toplu sorgudan)."""
    team_id: int
    league_id: int | None
    name: str
    reputation: int
    squad_value: int
    squad_size: int
    best_rating: int


class BoardRoom:
    """
    Yonetim kurulunun orkestrasyonu: haftalik guven, uyari, sezon ici kovulma; sezon devrinde hedef, butce
    onerisi, sezon sonu kovulma, AI kuluplerinin menajer degisimi (is ilanlari) ve is teklifleri.
    `CareerManager` cagirir; RNG yalnizca tohumlu, commit yok, her adim kendi savepoint'inde.
    """

    def __init__(self, cm) -> None:
        self.cm = cm
        self.db = cm.db
        self._facts: dict[int, _ClubFacts] | None = None
        self._ranks: dict[int, tuple[int, int, float]] | None = None    # team -> (sira, lig boyu, fark)
        self._weeks: int | None = None                                  # sezonun lig haftasi sayisi
        self._season_gaps: dict[int | None, int] = {}                   # koltuk -> biten sezonun kademe farki

    # ------------------------------------------------------------------ ortak

    def _guarded(self, label: str, fn, *args):
        """Adim kendi savepoint'inde: yonetim hatasi haftayi / devri bozmaz."""
        self.db.flush()
        try:
            with self.db.begin_nested():
                return fn(*args)
        except (SQLAlchemyError, ValueError, TypeError, AttributeError, KeyError) as exc:
            log.exception("Yönetim kurulu adımı başarısız (%s): %s", label, exc)
            return None

    def _post(self, manager_id: int | None, team_id: int | None, subject: str, body: str,
              *, season: int | None = None, week: int | None = None) -> None:
        """Gelen kutusuna yonetim mesaji (15D). Gelen kutusu kapaliysa sessizce atlanir."""
        cm = self.cm
        if not cm._inbox_on():
            return
        season = cm.season if season is None else int(season)
        week = cm.current_week if week is None else int(week)
        inbox.post(
            self.db, manager_id=manager_id, kind=inbox.KIND_BOARD, subject=subject, body=body,
            team_id=team_id, season=season, week=week, career_week=cm.career_week,
            game_date=inbox.match_date(season, week, start=cm.state.season_start_date),
            ref_type=inbox.REF_TEAM if _is_id(team_id) else None, ref_id=team_id,
        )

    def _manager_id(self, team_id: int) -> int | None:
        return inbox.manager_id_for(self.cm, team_id)

    # ------------------------------------------------------------------ kulup sayilari ve guc sirasi

    def _load_facts(self) -> dict[int, _ClubFacts]:
        if self._facts is not None:
            return self._facts
        rows = self.db.execute(
            select(Player.team_id, func.sum(Player.market_value), func.count(), func.max(Player.overall_rating))
            .where(Player.team_id.isnot(None), Player.in_academy.is_(False))
            .group_by(Player.team_id)
        ).all()
        squads = {int(team_id): (int(value or 0), int(count or 0), int(best or 0))
                  for team_id, value, count, best in rows}
        facts: dict[int, _ClubFacts] = {}
        for team_id, league_id, name, rep in self.db.execute(
            select(Team.id, Team.league_id, Team.name, Team.reputation).order_by(Team.id)
        ).all():
            value, count, best = squads.get(int(team_id), (0, 0, 0))
            facts[int(team_id)] = _ClubFacts(team_id=int(team_id), league_id=league_id, name=name,
                                             reputation=int(rep), squad_value=value, squad_size=count,
                                             best_rating=best)
        self._facts = facts
        return facts

    @staticmethod
    def _strength(fact: _ClubFacts, low: int, high: int) -> float:
        """Kulubun ligindeki guc puani (0-1): itibar %55, kadro degeri %45."""
        span = max(1, high - low)
        value_score = (fact.squad_value - low) / span
        return 0.55 * (fact.reputation / 100.0) + 0.45 * max(0.0, min(1.0, value_score))

    def _load_ranks(self) -> dict[int, tuple[int, int, float]]:
        """Kulup -> (ligindeki guc sirasi, lig boyu, birincinin ikinciye farki)."""
        if self._ranks is not None:
            return self._ranks
        facts = self._load_facts()
        by_league: dict[int, list[_ClubFacts]] = {}
        for fact in facts.values():
            if fact.league_id is not None:
                by_league.setdefault(int(fact.league_id), []).append(fact)
        ranks: dict[int, tuple[int, int, float]] = {}
        for clubs in by_league.values():
            values = [c.squad_value for c in clubs] or [0]
            low, high = min(values), max(values)
            scored = sorted(((self._strength(c, low, high), -c.reputation, c.team_id, c) for c in clubs),
                            key=lambda row: (-row[0], row[1], row[2]))
            margin = round(scored[0][0] - scored[1][0], 4) if len(scored) > 1 else 1.0
            size = len(scored)
            for index, (_score, _rep, team_id, _club) in enumerate(scored, start=1):
                ranks[int(team_id)] = (index, size, margin)
        self._ranks = ranks
        return ranks

    def _target_for(self, team_id: int, *, in_cup: bool = False) -> tuple[str, str | None, int]:
        """(lig hedefi, kupa hedefi, lig boyu)."""
        rank, size, margin = self._load_ranks().get(int(team_id), (1, 1, 0.0))
        league = target_tier(rank, size, margin if rank == 1 else 0.0)
        cup = cup_target(rank, size, in_cup=bool(in_cup))
        return league, cup, size

    # ------------------------------------------------------------------ durum satirlari

    def state_row(self, manager_id: int | None, season: int, team_id: int | None) -> BoardState | None:
        stmt = select(BoardState).where(BoardState.season == int(season), BoardState.team_id == team_id)
        stmt = stmt.where(BoardState.manager_id.is_(None) if manager_id is None
                          else BoardState.manager_id == int(manager_id))
        return self.db.scalars(stmt.order_by(BoardState.id.desc()).limit(1)).first()

    def latest_state(self, manager_id: int | None) -> BoardState | None:
        stmt = select(BoardState)
        stmt = stmt.where(BoardState.manager_id.is_(None) if manager_id is None
                          else BoardState.manager_id == int(manager_id))
        return self.db.scalars(stmt.order_by(BoardState.id.desc()).limit(1)).first()

    def _ensure_state(self, manager_id: int | None, season: int, team: Team, *, week: int,
                      announce: bool = True) -> BoardState:
        """Sezonun yonetim satiri (yoksa hedefi belirler, butce onerisini yazar ve mesaji gonderir)."""
        row = self.state_row(manager_id, season, team.id)
        if row is not None:
            return row
        participants = self._cup_participants(season)
        league, cup, size = self._target_for(team.id, in_cup=team.id in participants)
        confidence = START_CONFIDENCE
        plan = budget_proposal(team.reputation, confidence, league,
                               transfer_budget=team.transfer_budget, wage_bill=team.wage_bill,
                               wage_budget=team.wage_budget)
        row = BoardState(
            manager_id=manager_id, team_id=team.id, season=int(season), target=league, cup_target=cup,
            confidence=confidence, start_confidence=confidence, low_weeks=0, warning=WARN_NONE,
            since_week=int(week), last_week=0, status=STATE_ACTIVE,
            budget_transfer=plan.transfer_budget, budget_wage=plan.wage_budget,
            budget_status=BUDGET_PENDING if plan.any else BUDGET_NONE,
            reasons=[],
        )
        self.db.add(row)
        self.db.flush()
        if announce:
            self._post(manager_id, team.id, f"Sezon hedefi: {target_label(league, size)}",
                       self._target_text(team, league, cup, size, plan), season=season, week=week)
        return row

    def _target_text(self, team: Team, league: str, cup: str | None, size: int, plan: BudgetPlan) -> str:
        money = finance.format_money
        parts = [f"{team.name} yönetim kurulu bu sezon için hedefi belirledi: {target_label(league, size)}."]
        if cup:
            parts.append(f"Devler Arenası beklentisi: {CUP_LABELS.get(cup, cup)}.")
        grant = plan.grant_over(team.transfer_budget)
        if plan.any:
            parts.append(f"Sezon bütçesi önerisi: transfer kasası {money(plan.transfer_budget)}"
                         + (f" (kasaya {money(grant)} eklenir)" if grant > 0 else " (kasan zaten yeterli)")
                         + f", haftalık maaş havuzu {money(plan.wage_budget)}. "
                         "Yönetim Kurulu sayfasından kabul edebilirsin.")
        parts.append("Yönetimin güveni her hafta sonuçlara, hedefe uzaklığa ve mali duruma göre değişir.")
        return " ".join(parts)

    def _cup_participants(self, season: int) -> frozenset[int]:
        try:
            t = self.cm.tournaments.current()
        except (SQLAlchemyError, AttributeError, ValueError):   # pragma: no cover - turnuva kurulmamis dunya
            return frozenset()
        if t is None or int(getattr(t, "season", 0) or 0) != int(season):
            return frozenset()
        try:
            return frozenset(int(e.team_id) for e in t.entries if e.team_id is not None)
        except (AttributeError, TypeError):                     # pragma: no cover
            return frozenset()

    # ------------------------------------------------------------------ haftalik adim

    def run_week(self, week: int, report) -> None:
        """
        Hafta sonunda: her insan kulubunun guveni, uyarilari ve (gerekirse) kovulma. Dunyada hic insan menajer
        yoksa (AI kosusu) TEK SQL bile atilmaz: adim hemen doner.
        """
        cm = self.cm
        if not cm.human_team_ids() and not cm.board_unemployed():
            return
        self._guarded("hafta", self._run_week, week, report)

    def _run_week(self, week: int, report) -> None:
        cm = self.cm
        season = cm.season
        humans = sorted(cm.human_team_ids())
        since = cm.board_since()
        positions = self._positions_for(humans) if humans else {}
        for team_id in humans:
            team = self.db.get(Team, team_id)
            if team is None:
                continue
            self._week_for_club(team, season, week, report, positions.get(team_id), since)
        if cm.board_unemployed():
            self._refresh_vacancies(week)
        self._expire_offers()

    def _positions_for(self, team_ids: Sequence[int]) -> dict[int, tuple[int, int]]:
        """Kulup -> (guncel lig sirasi, lig boyu). Lig basina tek siralama."""
        out: dict[int, tuple[int, int]] = {}
        leagues: dict[int, list[int]] = {}
        for team_id in team_ids:
            team = self.db.get(Team, team_id)
            if team is not None and team.league_id is not None:
                leagues.setdefault(int(team.league_id), []).append(int(team_id))
        for league_id, ids in leagues.items():
            table = self.cm.standings(league_id)
            size = len(table)
            index = {int(t.id): position for position, t in enumerate(table, start=1)}
            for team_id in ids:
                if team_id in index:
                    out[team_id] = (index[team_id], size)
        return out

    def _week_for_club(self, team: Team, season: int, week: int, report, place: tuple[int, int] | None,
                       board_since: int) -> None:
        manager_id = self._manager_id(team.id)
        row = self._ensure_state(manager_id, season, team, week=week)
        if row.status != STATE_ACTIVE or int(row.last_week or 0) >= int(week):
            return
        view = report.view_for(team.id) if report is not None else None
        position, size = place if place else (None, 0)
        change = weekly_delta(
            results=self._results_of(view, team),
            position=position, target=row.target, league_size=size,
            transfer_budget=team.transfer_budget, wage_bill=team.wage_bill, wage_budget=team.wage_budget,
            sales=self._sales_of(view, team), signings=self._signings_of(view, team),
        )
        before = float(row.confidence)
        row.confidence = apply_confidence(before, change.delta)
        row.last_week = int(week)
        row.reasons = list(change.reasons)
        row.low_weeks = int(row.low_weeks or 0) + 1 if row.confidence < CRITICAL_LEVEL else 0
        gap = season_gap(row.target, position, size) if position else 0
        # Eski kayit gecisi: kural bu kayitta yeni calismaya basladiysa menajer uyarilari almamis sayilir
        # (15A'nin contracts_since_cw deseni) -- ilk GRACE_WEEKS hafta kovulma yok.
        weeks_in_charge = min(self._weeks_in_charge(row, week),
                              max(0, self.cm.career_week - int(board_since) + 1))
        level = warning_level(row.confidence)
        if level > int(row.warning or 0) and weeks_in_charge >= GRACE_WEEKS:
            row.warning = level
            self._post(manager_id, team.id,
                       "Yönetimden son uyarı" if level == WARN_FINAL else "Yönetimden uyarı",
                       self._warning_text(team, row, position, size, level, change))
        self.db.flush()
        if sack_now(row.confidence, row.low_weeks, gap, week=week, weeks_in_charge=weeks_in_charge,
                    season_weeks=self._season_weeks()):
            self._sack(team, row, manager_id, season, week,
                       f"Yönetim kurulu {team.name} ile yollarını ayırdı: güven üst üste "
                       f"{row.low_weeks} hafta kritik seviyenin altında kaldı "
                       f"({row.confidence:.0f}/100, {position}. sıra).")

    def _warning_text(self, team: Team, row: BoardState, position: int | None, size: int,
                      level: int, change: ConfidenceChange) -> str:
        head = ("Yönetim kurulu SON UYARI verdi" if level == WARN_FINAL else "Yönetim kurulu uyardı")
        parts = [f"{head}: {team.name} yönetiminin sana güveni {row.confidence:.0f}/100 "
                 f"({confidence_label(row.confidence)})."]
        if position:
            parts.append(f"Sezon hedefi {target_label(row.target, size)}; bugün {position}. sıradasın.")
        if change.reasons:
            parts.append("Bu hafta: " + " · ".join(change.reasons) + ".")
        parts.append("Güven üst üste iki hafta 15'in altına inerse görevine son verilir."
                     if level == WARN_FINAL else "Sonuçlar düzelmezse son uyarı gelir.")
        return " ".join(parts)

    @staticmethod
    def _weeks_in_charge(row: BoardState, week: int) -> int:
        return max(0, int(week) - int(row.since_week or 1) + 1)

    def _season_weeks(self) -> int:
        """Sezonun lig haftasi sayisi (hafta basina TEK okuma; kovulma esigi buna olceklenir)."""
        if self._weeks is None:
            try:
                self._weeks = max(1, int(self.cm.league_weeks()))
            except (SQLAlchemyError, ValueError, TypeError):      # pragma: no cover
                self._weeks = 38
        return self._weeks

    @staticmethod
    def _results_of(view, team: Team) -> list[tuple[str, int, int, int, bool]]:
        out: list[tuple[str, int, int, int, bool]] = []
        if view is None:
            return out
        for result, cup in ((getattr(view, "user_result", None), False),
                            (getattr(view, "user_cup_result", None), True)):
            if result is None:
                continue
            mine, theirs = ((result.home, result.away) if result.home.id == team.id else (result.away, result.home))
            if mine.id != team.id:
                continue
            goals_for, goals_against = mine.stats.goals, theirs.stats.goals
            outcome = "W" if goals_for > goals_against else ("D" if goals_for == goals_against else "L")
            out.append((outcome, int(mine.reputation), int(theirs.reputation), goals_for - goals_against, cup))
        return out

    def _sales_of(self, view, team: Team) -> list[tuple[str, int, int, int, int]]:
        best = self._load_facts().get(int(team.id), None)
        best_rating = best.best_rating if best is not None else 0
        out = []
        for news in getattr(view, "transfers", ()) or ():
            if getattr(news, "from_team_id", None) != team.id:
                continue
            player = self.db.get(Player, getattr(news, "player_id", None))
            if player is None:
                continue
            out.append((player.name, int(player.overall_rating), max(best_rating, int(player.overall_rating)),
                        int(getattr(news, "fee", 0) or 0), int(player.market_value or 0)))
        return out

    def _signings_of(self, view, team: Team) -> list[tuple[str, int, int]]:
        best = self._load_facts().get(int(team.id), None)
        best_rating = best.best_rating if best is not None else 0
        out = []
        for news in getattr(view, "transfers", ()) or ():
            if getattr(news, "to_team_id", None) != team.id:
                continue
            player = self.db.get(Player, getattr(news, "player_id", None))
            if player is not None:
                out.append((player.name, int(player.overall_rating), best_rating))
        return out

    # ------------------------------------------------------------------ kovulma / ayrilma

    def _sack(self, team: Team, row: BoardState, manager_id: int | None, season: int, week: int,
              text: str) -> None:
        row.status = STATE_SACKED
        row.confidence = clamp_confidence(min(row.confidence, CRITICAL_LEVEL))
        self._post(manager_id, team.id, f"{team.name} görevine son verdi", text, season=season, week=week)
        self.cm.board_vacate(team, manager_id, status=STATE_SACKED,
                             reputation_delta=SACKED_REPUTATION)
        self.db.flush()

    # ------------------------------------------------------------------ sezon devri

    def season_review(self, new_season: int) -> None:
        """
        Sezon kapanisi (start_new_season icinden, tablolar sifirlandiktan SONRA; siralama season_standings'ten):
        insan menajerlerin sezon sonu degerlendirmesi ve kovulmasi, AI kuluplerinin menajer degisimi (is ilani),
        basarili menajere gelen teklifler, yeni sezonun hedefi ve butce onerisi.
        """
        self._guarded("sezon", self._season_review, new_season)

    def _season_review(self, new_season: int) -> None:
        cm = self.cm
        season = int(new_season) - 1
        table = self._final_table(season)
        if not table:
            return
        humans = sorted(cm.human_team_ids())
        champions = self._champions(season)
        career_week = cm.career_week
        if humans:
            cm.board_since()
        for team_id in humans:
            team = self.db.get(Team, team_id)
            if team is None:
                continue
            self._close_season_for_manager(team, season, table, champions, career_week)
        self._close_expired_adverts(career_week)                # once eski ilanlar kapanir, sonra yenileri acilir
        self._ai_manager_changes(season, table, humans, career_week)
        self._offer_jobs(new_season)

    def _final_table(self, season: int) -> dict[int, tuple[int, int, int]]:
        """Kulup -> (sezon sonu sirasi, lig boyu, puan) (season_standings; _archive_league yazar)."""
        rows = self.db.execute(
            select(SeasonStanding.team_id, SeasonStanding.league_id, SeasonStanding.position,
                   SeasonStanding.points)
            .where(SeasonStanding.season == int(season))
        ).all()
        sizes: dict[int, int] = {}
        for _team_id, league_id, _position, _points in rows:
            if league_id is not None:
                sizes[int(league_id)] = sizes.get(int(league_id), 0) + 1
        return {int(team_id): (int(position), sizes.get(int(league_id or 0), 0), int(points or 0))
                for team_id, league_id, position, points in rows if team_id is not None}

    @staticmethod
    def _matches_played(size: int) -> int:
        """Cift devreli ligde bir kulubun sezon maci sayisi (form_confidence icin)."""
        return max(1, 2 * (max(2, int(size)) - 1))

    def _champions(self, season: int) -> frozenset[int]:
        from models import SeasonHonour

        rows = self.db.scalars(
            select(SeasonHonour.champion_team_id).where(SeasonHonour.season == int(season))
        ).all()
        return frozenset(int(r) for r in rows if r is not None)

    def _cup_exit(self, team_id: int, season: int) -> str | None:
        """Kulubun bu sezon Devler Arenasi'ndan elendigi tur ("WIN": kupayi kaldirdi)."""
        t = self.cm.tournaments.current()
        if t is None or int(getattr(t, "season", 0) or 0) != int(season):
            return None
        best, won_final = None, False
        for tie in getattr(t, "ties", ()) or ():
            if not getattr(tie, "decided", False):
                continue
            if team_id not in (tie.first_team_id, tie.second_team_id):
                continue
            index = cup_stage_index(tie.stage)
            if index < 0:
                continue
            if best is None or index > cup_stage_index(best):
                best = str(getattr(tie.stage, "value", tie.stage)).upper()
            if str(getattr(tie.stage, "value", tie.stage)).upper() == "FINAL" and tie.winner_team_id == team_id:
                won_final = True
        if won_final:
            return CUP_WIN
        return best

    def _close_season_for_manager(self, team: Team, season: int, table: Mapping[int, tuple[int, int]],
                                  champions: frozenset[int], career_week: int) -> None:
        manager_id = self._manager_id(team.id)
        row = self.state_row(manager_id, season, team.id)
        if row is None or row.status != STATE_ACTIVE:
            return
        position, size, _points = table.get(int(team.id), (None, 0, 0))
        if position is None or size <= 0:
            return
        reached = achieved_tier(position, size)
        gap = season_gap(row.target, position, size)
        trophies = 1 if team.id in champions else 0
        exit_stage = self._cup_exit(team.id, season)
        cup_gap = cup_delta(row.cup_target, exit_stage)
        row.confidence = season_end_confidence(row.confidence, gap, trophies=trophies, cup_gap=cup_gap)
        self._season_gaps[manager_id] = gap
        first_season = int(row.since_week or 1) > 1 or self._is_first_season(manager_id, season)
        chance = sack_probability(gap, row.confidence, first_season=first_season,
                                  relegated=reached == RELEGATION)
        self.db.flush()
        summary = (f"{team.name} {season}. sezonu {position}. sırada bitirdi "
                   f"(hedef: {target_label(row.target, size)}, ulaşılan: {TIER_LABELS[reached]}).")
        if chance > 0 and roll(chance, "sack", season, team.id, manager_id or 0):
            self._sack(team, row, manager_id, season, max(1, self.cm.current_week),
                       summary + f" Yönetim kurulu görevine son verdi (güven {row.confidence:.0f}/100).")
            return
        row.status = STATE_ACTIVE
        self._post(manager_id, team.id, f"Sezon değerlendirmesi: {team.name}",
                   summary + f" Yönetimin güveni: {row.confidence:.0f}/100 ({confidence_label(row.confidence)})."
                   + (" Yönetim hedefin gerisinde kalındığını not etti." if gap > 0 else " Yönetim memnun."),
                   season=season, week=max(1, self.cm.current_week))
        self._open_next_season(manager_id, team, int(season) + 1, row)

    def _is_first_season(self, manager_id: int | None, season: int) -> bool:
        stmt = select(func.count()).select_from(BoardState).where(BoardState.season < int(season))
        stmt = stmt.where(BoardState.manager_id.is_(None) if manager_id is None
                          else BoardState.manager_id == int(manager_id))
        return int(self.db.scalar(stmt) or 0) == 0

    def _open_next_season(self, manager_id: int | None, team: Team, season: int, previous: BoardState) -> None:
        """Yeni sezonun hedefi, devreden guveni ve butce onerisi."""
        self._facts = self._ranks = None                       # devirde kadrolar degisti
        if self.state_row(manager_id, season, team.id) is not None:
            return
        participants = self._cup_participants(season)
        league, cup, size = self._target_for(team.id, in_cup=team.id in participants)
        confidence = clamp_confidence(float(previous.confidence))
        plan = budget_proposal(team.reputation, confidence, league,
                               transfer_budget=team.transfer_budget, wage_bill=team.wage_bill,
                               wage_budget=team.wage_budget)
        self.db.add(BoardState(
            manager_id=manager_id, team_id=team.id, season=int(season), target=league, cup_target=cup,
            confidence=confidence, start_confidence=confidence, low_weeks=0, warning=WARN_NONE,
            since_week=1, last_week=0, status=STATE_ACTIVE,
            budget_transfer=plan.transfer_budget, budget_wage=plan.wage_budget,
            budget_status=BUDGET_PENDING if plan.any else BUDGET_NONE, reasons=[],
        ))
        self.db.flush()
        self._post(manager_id, team.id, f"Sezon hedefi: {target_label(league, size)}",
                   self._target_text(team, league, cup, size, plan), season=season, week=1)

    # ------------------------------------------------------------------ AI kuluplerinin menajer degisimi

    def _ai_manager_changes(self, season: int, table: Mapping[int, tuple[int, int, int]],
                            humans: Sequence[int], career_week: int) -> None:
        """
        AI kuluplerinin (gorunmez) menajerleri de sonuclara gore gider: hedefin altinda biten kulup tohumlu
        zarla menajerini degistirir ve IS ILANI acar (teams.board_vacant_since). Kulup guveni HAFTALIK
        saklanmaz (114 kulup x 38 hafta yazim olurdu): sezon sonu sirasi + PUAN ORANINDAN (form_confidence)
        tureyen tek bir karardir -- ucuz, deterministik ve menajerinkiyle ayni formulu kullanir.
        """
        human_ids = frozenset(int(t) for t in humans)
        for team_id, (position, size, points) in sorted(table.items()):
            if team_id in human_ids or size <= 0:
                continue
            team = self.db.get(Team, team_id)
            if team is None or team.board_vacant_since is not None:
                continue
            target, _cup, _size = self._target_for(team_id)
            reached = achieved_tier(position, size)
            gap = season_gap(target, position, size)
            if gap <= 0:
                continue
            confidence = season_end_confidence(form_confidence(points, self._matches_played(size)), gap)
            chance = sack_probability(gap, confidence, relegated=reached == RELEGATION)
            if chance > 0 and roll(chance, "ai-sack", season, team_id):
                team.board_vacant_since = int(career_week)
        self.db.flush()

    def _close_expired_adverts(self, career_week: int) -> None:
        """Suresi dolan ilanlar: kulup baskasini bulur."""
        rows = self.db.scalars(select(Team).where(Team.board_vacant_since.isnot(None))).all()
        for team in rows:
            if int(career_week) - int(team.board_vacant_since) >= ADVERT_WEEKS:
                team.board_vacant_since = None
        self.db.flush()

    def _refresh_vacancies(self, week: int) -> None:
        """
        Kulupsuz menajer icin piyasa canli tutulur: acik ilan sayisi MIN_OPEN_JOBS'un altina duserse hedefinin
        gerisindeki kuluplerden tohumlu secimle yenileri acilir (yalnizca kulupsuzken calisir: ek SQL yok).
        """
        cm = self.cm
        career_week = cm.career_week
        self._close_expired_adverts(career_week)
        open_count = int(self.db.scalar(
            select(func.count()).select_from(Team).where(Team.board_vacant_since.isnot(None))) or 0)
        if open_count >= MIN_OPEN_JOBS:
            return
        humans = frozenset(cm.human_team_ids())
        candidates: list[tuple[int, int]] = []                  # (gap, team_id)
        for league in cm.leagues():
            table = cm.standings(league.id)
            size = len(table)
            for position, team in enumerate(table, start=1):
                if team.id in humans or team.board_vacant_since is not None:
                    continue
                target, _cup, _size = self._target_for(team.id)
                gap = season_gap(target, position, size)
                if gap >= 1:
                    candidates.append((gap, int(team.id)))
        if not candidates:
            return
        candidates.sort(key=lambda row: (-row[0], row[1]))
        picker = dice("vacancy", cm.season, week)
        pool = candidates[:max(MIN_OPEN_JOBS * 3, MIN_OPEN_JOBS)]
        picker.shuffle(pool)
        for _gap, team_id in pool[:MIN_OPEN_JOBS - open_count]:
            team = self.db.get(Team, team_id)
            if team is not None:
                team.board_vacant_since = int(career_week)
        self.db.flush()

    # ------------------------------------------------------------------ teklifler

    def _offer_jobs(self, new_season: int) -> None:
        """Isini iyi yapan menajere (ya da kulupsuz menajere) bos kuluplerden teklif."""
        cm = self.cm
        seats: list[tuple[int | None, int | None]] = []
        if cm.state.user_team_id is not None or cm.board_unemployed():
            seats.append((None, cm.state.user_team_id))
        for seat in cm.seats.active():
            if not seat.is_primary and _is_id(seat.id) and seat.team_id is not None:
                seats.append((seat.id, seat.team_id))
        for manager_id, team_id in seats:
            self._offer_for_seat(manager_id, team_id, new_season)

    def _offer_for_seat(self, manager_id: int | None, team_id: int | None, season: int) -> None:
        cm = self.cm
        manager_rep = self._reputation_of(manager_id, team_id)
        pending = self._pending_offers(manager_id)
        if len(pending) >= MAX_PENDING_OFFERS:
            return
        state = self.latest_state(manager_id)
        confidence = float(state.confidence) if state is not None else START_CONFIDENCE
        gap = int(self._season_gaps.get(manager_id, 0))        # < 0: hedefini asti -> daha cok kulup ister
        vacancies = self._open_teams()
        offered = {int(o.team_id) for o in pending}
        career_week = cm.career_week
        unemployed_weeks = self._unemployed_weeks(manager_id)
        for team in vacancies:
            if team.id in offered or team.id == team_id:
                continue
            if team_id is not None:
                chance = approach_chance(manager_rep, team.reputation, confidence, gap)
                kind = OFFER_APPROACH
            else:
                chance = application_chance(manager_rep, team.reputation,
                                            unemployed_weeks=unemployed_weeks) * 0.5
                kind = OFFER_APPROACH
            if chance <= 0 or not roll(chance, "approach", season, team.id, manager_id or 0):
                continue
            self._add_offer(manager_id, team, kind, season, career_week)
            offered.add(int(team.id))
            if len(offered) + len(pending) >= MAX_PENDING_OFFERS:
                break

    def _add_offer(self, manager_id: int | None, team: Team, kind: str, season: int,
                   career_week: int) -> BoardOffer:
        offer = BoardOffer(
            manager_id=manager_id, team_id=team.id, season=int(season), week=max(1, self.cm.current_week),
            career_week=int(career_week), expires_career_week=int(career_week) + OFFER_WEEKS,
            kind=kind, status=OFFER_PENDING, reputation=int(team.reputation),
        )
        self.db.add(offer)
        self.db.flush()
        league = self.db.get(Team, team.id)
        league_name = league.league.name if league is not None and league.league is not None else ""
        self._post(manager_id, team.id, f"{team.name} menajerlik teklif ediyor",
                   f"{team.name} ({league_name}, itibar {team.reputation}) boşta ve seni istiyor. "
                   f"Teklif {OFFER_WEEKS} hafta geçerli; Yönetim Kurulu sayfasından kabul edebilirsin.",
                   season=season)
        return offer

    def _pending_offers(self, manager_id: int | None) -> list[BoardOffer]:
        stmt = select(BoardOffer).where(BoardOffer.status == OFFER_PENDING)
        stmt = stmt.where(BoardOffer.manager_id.is_(None) if manager_id is None
                          else BoardOffer.manager_id == int(manager_id))
        return list(self.db.scalars(stmt.order_by(BoardOffer.id)))

    def _expire_offers(self) -> None:
        career_week = self.cm.career_week
        rows = self.db.scalars(select(BoardOffer).where(
            BoardOffer.status == OFFER_PENDING, BoardOffer.expires_career_week < career_week)).all()
        for offer in rows:
            offer.status = OFFER_EXPIRED
        if rows:
            self.db.flush()

    def _open_teams(self) -> list[Team]:
        humans = frozenset(self.cm.human_team_ids())
        rows = self.db.scalars(
            select(Team).where(Team.board_vacant_since.isnot(None)).order_by(Team.reputation.desc(), Team.id)
        ).all()
        return [t for t in rows if t.id not in humans]

    def _reputation_of(self, manager_id: int | None, team_id: int | None) -> float:
        if manager_id is None:
            return float(self.cm.state.manager_reputation)
        seat = self.cm.seats.by_id(int(manager_id))
        return float(seat.reputation) if seat is not None else reputation.START_REPUTATION

    def _unemployed_weeks(self, manager_id: int | None) -> int:
        if manager_id is not None:
            return 0
        since = self.cm.state.board_unemployed_since
        return max(0, self.cm.career_week - int(since)) if since is not None else 0


# ===========================================================================
# 9) BoardDesk — menajer / arayuz API'si
# ===========================================================================

SHARED_OFF_TEXT = ("Bu dünyada yönetim kurulu kuralı kapalı: dünya yöneticisi "
                   "\"Sonuçlara göre kovulma\" kuralını açmadan yönetim ekranı çalışmaz.")
NO_CLUB_TEXT = "Şu anda bir kulübü yönetmiyorsun."
HAS_CLUB_TEXT = "Önce şimdiki kulübünden ayrılmalısın."
OFFER_NOT_FOUND_TEXT = "İş teklifi bulunamadı."
OFFER_CLOSED_TEXT = "Bu teklif artık geçerli değil."
JOB_NOT_OPEN_TEXT = "Bu kulüp menajer aramıyor."
ALREADY_APPLIED_TEXT = "Bu kulübe zaten başvurdun."
NOT_ELIGIBLE_TEXT = "Tanınırlığın bu kulüp için yeterli değil."
NO_BUDGET_TEXT = "Yönetimin bekleyen bir bütçe önerisi yok."


class BoardDesk:
    """
    Menajerin yonetim kurulu masasi (15C-U arayuzu bunu cagirir). Flush eder, COMMIT ETMEZ; reddedilen her adim
    `BoardError` (Turkce) firlatir ve hicbir sey yazmaz. Donusler duz gorunumlerdir (ORM sizmaz).
    """

    def __init__(self, cm) -> None:
        self.cm = cm
        self.db = cm.db
        self.room = BoardRoom(cm)

    # ------------------------------------------------------------------ durum

    @property
    def enabled(self) -> bool:
        return bool(self.cm._board_on())

    def _require(self) -> None:
        if not self.enabled:
            raise BoardError(SHARED_OFF_TEXT if self.cm.rules.shared else "Yönetim kurulu kuralı kapalı.")

    @property
    def manager_id(self) -> int | None:
        return inbox.manager_id_for(self.cm)

    @property
    def team(self) -> Team | None:
        return self.cm.user_team

    @property
    def unemployed(self) -> bool:
        """Kovulmus / istifa etmis (ve henuz kulup bulmamis) menajer."""
        if not self.enabled or self.cm.user_team is not None:
            return False
        return self.cm.state.board_unemployed_since is not None

    def unemployed_weeks(self) -> int:
        since = self.cm.state.board_unemployed_since
        return max(0, self.cm.career_week - int(since)) if since is not None else 0

    def state(self) -> BoardView | None:
        """Yonetim kurulu ekraninin verisi (kulupsuz menajerde son kulubun kapanis satiri)."""
        self._require()
        team = self.cm.user_team
        manager_id = self.manager_id
        row = (self.room.state_row(manager_id, self.cm.season, team.id) if team is not None
               else self.room.latest_state(manager_id))
        if row is None:
            return None
        return self._view(row)

    def history(self, limit: int = 20) -> list[BoardView]:
        """Gecmis sezonlarin yonetim satirlari (en yeni once)."""
        self._require()
        manager_id = self.manager_id
        stmt = select(BoardState)
        stmt = stmt.where(BoardState.manager_id.is_(None) if manager_id is None
                          else BoardState.manager_id == int(manager_id))
        rows = self.db.scalars(stmt.order_by(BoardState.id.desc()).limit(max(1, int(limit))))
        return [self._view(row) for row in rows]

    def _view(self, row: BoardState) -> BoardView:
        team = self.db.get(Team, row.team_id) if row.team_id is not None else None
        position, size = None, 0
        if team is not None and team.league_id is not None and int(row.season) == self.cm.season:
            table = self.cm.standings(team.league_id)
            size = len(table)
            position = next((i for i, t in enumerate(table, start=1) if t.id == team.id), None)
        elif team is not None:
            size = len(team.league.teams) if team.league is not None else 0
        gap = season_gap(row.target, position, size) if position and size else 0
        return BoardView(
            season=int(row.season), team_id=row.team_id, team_name=team.name if team is not None else "—",
            confidence=clamp_confidence(row.confidence), confidence_label=confidence_label(row.confidence),
            target=row.target, target_label=target_label(row.target, size or 20),
            cup_target=row.cup_target,
            cup_target_label=CUP_LABELS.get(row.cup_target) if row.cup_target else None,
            position=position, league_size=size, gap=gap,
            warning=int(row.warning or 0), warning_label=WARNING_LABELS.get(int(row.warning or 0), ""),
            status=row.status, status_label=STATE_LABELS.get(row.status, row.status),
            low_weeks=int(row.low_weeks or 0),
            weeks_in_charge=max(0, self.cm.current_week - int(row.since_week or 1)),
            since_week=int(row.since_week or 1),
            reasons=tuple(str(r) for r in (row.reasons or [])),
        )

    # ------------------------------------------------------------------ butce

    def budget(self) -> BudgetView | None:
        self._require()
        team = self.cm.user_team
        if team is None:
            return None
        row = self.room.state_row(self.manager_id, self.cm.season, team.id)
        if row is None or row.budget_status == BUDGET_NONE:
            return None
        money = finance.format_money
        grant = max(0, int(row.budget_transfer or 0) - int(team.transfer_budget))
        text = (f"Yönetim bu sezon için transfer kasasını {money(row.budget_transfer or 0)} seviyesinde "
                f"ve haftalık maaş havuzunu {money(row.budget_wage or 0)} olarak öneriyor"
                + (f"; kabul edersen kasaya {money(grant)} eklenir." if grant > 0
                   else "; kasan zaten bu seviyenin üstünde."))
        return BudgetView(season=int(row.season), team_id=row.team_id, transfer_budget=int(row.budget_transfer or 0),
                          transfer_grant=grant, wage_budget=int(row.budget_wage or 0),
                          status=row.budget_status, accepted=row.budget_status == BUDGET_ACCEPTED, text=text)

    def accept_budget(self) -> BudgetView:
        """Sezon basi butce onerisini kabul eder: kasa ve maas havuzu guncellenir (kasadan para ALINMAZ)."""
        self._require()
        team = self.cm.user_team
        if team is None:
            raise BoardError(NO_CLUB_TEXT)
        row = self.room.state_row(self.manager_id, self.cm.season, team.id)
        if row is None or row.budget_status != BUDGET_PENDING:
            raise BoardError(NO_BUDGET_TEXT)
        team.transfer_budget = max(int(team.transfer_budget), int(row.budget_transfer or 0))
        team.wage_budget = max(int(team.wage_budget), int(row.budget_wage or 0))
        row.budget_status = BUDGET_ACCEPTED
        self.db.flush()
        view = self.budget()
        assert view is not None
        return view

    # ------------------------------------------------------------------ is piyasasi

    def jobs(self, query: str = "", limit: int = 20) -> list[JobAdvertView]:
        """Acik is ilanlari (menajer arayan kulupler). Kulubu olan menajer de gorebilir (CM'nin "Jobs" ekrani)."""
        self._require()
        manager_rep = float(self.cm.manager_reputation)
        weeks = self.unemployed_weeks()
        applied = {int(o.team_id) for o in self.room._pending_offers(self.manager_id)}
        applied |= self._applied_team_ids()
        career_week = self.cm.career_week
        text = str(query or "").strip().lower()
        facts = self.room._load_facts()
        out: list[JobAdvertView] = []
        for team in self.room._open_teams():
            if text and text not in team.name.lower():
                continue
            fact = facts.get(int(team.id))
            need = required_reputation(team.reputation, unemployed_weeks=weeks)
            eligible = manager_rep >= need
            out.append(JobAdvertView(
                team_id=int(team.id), team_name=team.name, league_id=team.league_id,
                league_name=team.league.name if team.league is not None else "",
                reputation=int(team.reputation),
                squad_size=fact.squad_size if fact else 0, squad_value=fact.squad_value if fact else 0,
                open_since_week=int(team.board_vacant_since or career_week),
                closes_in_weeks=max(0, ADVERT_WEEKS - (career_week - int(team.board_vacant_since or career_week))),
                required_reputation=need, eligible=eligible, applied=int(team.id) in applied,
                reason="" if eligible else NOT_ELIGIBLE_TEXT,
            ))
            if len(out) >= max(1, int(limit)):
                break
        return out

    def _applied_team_ids(self) -> set[int]:
        manager_id = self.manager_id
        stmt = select(BoardOffer.team_id).where(BoardOffer.season == self.cm.season,
                                                BoardOffer.kind == OFFER_ADVERT)
        stmt = stmt.where(BoardOffer.manager_id.is_(None) if manager_id is None
                          else BoardOffer.manager_id == int(manager_id))
        return {int(t) for t in self.db.scalars(stmt) if t is not None}

    def apply(self, team_id: int) -> ApplicationResult:
        """
        Is ilanina basvuru. Kulubun karari taninirliga gore tohumlu zarla verilir; kabul edilirse bekleyen
        bir TEKLIF olusur (accept_offer ile goreve baslanir). Ayni kulube sezon icinde bir kez basvurulur.
        """
        self._require()
        team = self.db.get(Team, int(team_id)) if _is_id(team_id) else None
        if team is None or team.board_vacant_since is None or team.id in self.cm.human_team_ids():
            raise BoardError(JOB_NOT_OPEN_TEXT)
        if int(team.id) in self._applied_team_ids():
            raise BoardError(ALREADY_APPLIED_TEXT)
        manager_id = self.manager_id
        manager_rep = float(self.cm.manager_reputation)
        weeks = self.unemployed_weeks()
        chance = application_chance(manager_rep, team.reputation, unemployed_weeks=weeks)
        season, week = self.cm.season, self.cm.current_week
        if chance <= 0:
            raise BoardError(NOT_ELIGIBLE_TEXT)
        accepted = roll(chance, "apply", season, week, team.id, manager_id or 0)
        career_week = self.cm.career_week
        offer = BoardOffer(
            manager_id=manager_id, team_id=team.id, season=int(season), week=int(week),
            career_week=career_week, expires_career_week=career_week + OFFER_WEEKS,
            kind=OFFER_ADVERT, status=OFFER_PENDING if accepted else OFFER_DECLINED,
            reputation=int(team.reputation),
        )
        self.db.add(offer)
        self.db.flush()
        if accepted:
            text = f"{team.name} başvurunu kabul etti: göreve başlamak için teklifi onayla."
            self.room._post(manager_id, team.id, f"{team.name}: başvurun kabul edildi", text, season=season,
                            week=week)
            return ApplicationResult(True, text, self._offer_view(offer))
        text = f"{team.name} başvurunu değerlendirdi ve başka bir menajerle devam etme kararı aldı."
        self.room._post(manager_id, team.id, f"{team.name}: başvurun reddedildi", text, season=season, week=week)
        return ApplicationResult(False, text)

    def offers(self, *, include_closed: bool = False) -> list[JobOfferView]:
        self._require()
        manager_id = self.manager_id
        stmt = select(BoardOffer)
        stmt = stmt.where(BoardOffer.manager_id.is_(None) if manager_id is None
                          else BoardOffer.manager_id == int(manager_id))
        if not include_closed:
            stmt = stmt.where(BoardOffer.status == OFFER_PENDING)
        rows = self.db.scalars(stmt.order_by(BoardOffer.id.desc()).limit(50))
        return [self._offer_view(row) for row in rows]

    def _offer_view(self, offer: BoardOffer) -> JobOfferView:
        team = self.db.get(Team, offer.team_id)
        expires = (int(offer.expires_career_week) - self.cm.career_week
                   if offer.expires_career_week is not None else None)
        name = team.name if team is not None else "—"
        return JobOfferView(
            id=int(offer.id), team_id=int(offer.team_id), team_name=name,
            league_name=team.league.name if team is not None and team.league is not None else "",
            reputation=int(offer.reputation or 0), kind=offer.kind,
            kind_label=OFFER_KIND_LABELS.get(offer.kind, offer.kind), status=offer.status,
            season=int(offer.season), week=int(offer.week),
            expires_in_weeks=max(0, expires) if expires is not None else None,
            text=f"{name} menajerlik teklif ediyor.",
        )

    def _my_offer(self, offer_id: int) -> BoardOffer:
        manager_id = self.manager_id
        offer = self.db.get(BoardOffer, int(offer_id)) if _is_id(offer_id) else None
        if offer is None or offer.manager_id != manager_id:
            raise BoardError(OFFER_NOT_FOUND_TEXT)
        if offer.status != OFFER_PENDING:
            raise BoardError(OFFER_CLOSED_TEXT)
        if offer.expires_career_week is not None and int(offer.expires_career_week) < self.cm.career_week:
            offer.status = OFFER_EXPIRED
            self.db.flush()
            raise BoardError(OFFER_CLOSED_TEXT)
        return offer

    def accept_offer(self, offer_id: int) -> JobOfferView:
        """Teklifi kabul eder: menajer yeni kulubun basina gecer (varsa eski kulubunden ayrilir)."""
        self._require()
        offer = self._my_offer(offer_id)
        team = self.db.get(Team, offer.team_id)
        if team is None or team.id in self.cm.human_team_ids():
            offer.status = OFFER_WITHDRAWN
            self.db.flush()
            raise BoardError(JOB_NOT_OPEN_TEXT)
        current = self.cm.user_team
        if current is not None:
            self._leave(current, STATE_LEFT, 0.0)
        offer.status = OFFER_ACCEPTED
        self._take_over(team)
        for other in self.room._pending_offers(self.manager_id):
            if other.id != offer.id:
                other.status = OFFER_WITHDRAWN
        self.db.flush()
        return self._offer_view(offer)

    def decline_offer(self, offer_id: int) -> None:
        self._require()
        offer = self._my_offer(offer_id)
        offer.status = OFFER_DECLINED
        self.db.flush()

    def resign(self) -> None:
        """Istifa: menajer kulupsuz kalir, kulup menajer aramaya baslar; taninirlik bir miktar duser."""
        self._require()
        team = self.cm.user_team
        if team is None:
            raise BoardError(NO_CLUB_TEXT)
        manager_id = self.manager_id
        row = self.room.state_row(manager_id, self.cm.season, team.id)
        if row is not None:
            row.status = STATE_RESIGNED
        self.room._post(manager_id, team.id, f"{team.name} görevinden ayrıldın",
                        f"{team.name} yönetimine istifanı sundun. Yeni bir kulüp bulana kadar iş "
                        "ilanlarına başvurabilirsin.")
        self._leave(team, STATE_RESIGNED, RESIGNED_REPUTATION)
        self.db.flush()

    def _leave(self, team: Team, status: str, reputation_delta: float) -> None:
        row = self.room.state_row(self.manager_id, self.cm.season, team.id)
        if row is not None and row.status == STATE_ACTIVE:
            row.status = status
        self.cm.board_vacate(team, self.manager_id, status=status, reputation_delta=reputation_delta)

    def _take_over(self, team: Team) -> None:
        """Yeni kulubun basina gecer: koltuk, ilan, yonetim satiri ve mesaj."""
        cm = self.cm
        cm.board_take_club(team)
        team.board_vacant_since = None
        row = self.room._ensure_state(self.manager_id, cm.season, team, week=cm.current_week, announce=False)
        row.since_week = int(cm.current_week)
        row.status = STATE_ACTIVE
        self.db.flush()
        size = self.room._load_ranks().get(int(team.id), (1, 1, 0.0))[1]
        self.room._post(self.manager_id, team.id, f"{team.name} menajeri oldun",
                        f"{team.name} yönetim kuruluyla anlaştın. Sezon hedefi: "
                        f"{target_label(row.target, size)}. Bol şans!")
