"""
development.py
==============
Potansiyel, oyuncu gelisimi ve yaslanma kurallari (10. Asama). SAF MANTIK: veritabani ve ORM bilmez.

Olcek: potential_rating overall_rating ile AYNI 1-99 olcegindedir (tavan guc). NULL/None
potansiyel "henuz atanmadi" demektir ve overall sayilir (effective_potential).

Wonderkid:
    16-21 yas ve potansiyel - overall >= 15.

Haftalik gelisim (weekly_growth, overall birimi, >= 0):
    GROWTH_RATE x gap^GAP_EXPONENT x yas x oynama x performans x genc antrenoru x moral x tesis
        gap        : potansiyel - overall (kalan yol; yaklastikca yavaslar ama tavana ulasir)
        yas        : 16-21 tam, 22 -> 0.85, 25 -> 0.40, 29 -> 0.04, 30+ sifir (age_growth_factor)
        oynama     : dakikaya bagli. Oynamayan A takim oyuncusu yalnizca antrenman tabani (0.20),
                     akademi oyuncusu U-21 maclari tabani (0.55), 90 dk oynayan 1.00,
                     ayni hafta kupa + lig (180 dk) 1.15 (playing_factor)
        performans : ortalama mac notu 6.0 notr; 7.0 -> 1.20, 5.0 -> 0.80 (oynamadiysa 1.0)
        antrenor   : COACH working_with_youngsters 1-20; 10 notr, 1 -> 0.78, 20 -> 1.25; None cezasiz 1.0
        moral      : 65 notr; 100 -> 1.175, 20 -> 0.775
        tesis      : yalnizca akademi; youth_facilities 1-20, 10 notr, 1 -> 0.82, 20 -> 1.20
    Sonuc hicbir zaman kalan farki (gap) gecmez.

Sezon uzunlugu: oranlar REFERENCE_SEASON_WEEKS (7 hafta: 4 takimli lig + kupa) icin kalibredir.
season_weeks verilirse haftalik oran REFERENCE / season_weeks ile olceklenir; 38 haftalik bir
FM liginde de sezon basina gelisim/gerileme ayni kalir.

Kalibrasyon (7 haftalik sezon, antrenor yok, moral 70, her mac not 6.5):
    17 yas wonderkid 62 (potansiyel 85) her maci oynarsa: 17->66, 18->71, 19->75, 20->78, 21->81,
        22->83, 23->84 (22-23 yasinda tavana yaklasir)
    ayni oyuncu hic oynamazsa: sezonda ~+1, 23 yasinda 67 (potansiyelin acikca altinda)
    akademide (tesis 10): sezonda ~+2-3, 23 yasinda 76; tesis 20 + genc antrenoru 18: 80

Yaslanma (weekly_decline, 32+):
    sezonluk dusus DECLINE_PER_SEASON x DECLINE_GROWTH^(yas - 32), en fazla DECLINE_MAX_PER_SEASON
        32 -> 1.0   33 -> 1.35   34 -> 1.8   35 -> 2.5   36 -> 3.3   37 -> 4.5  (sezon basina OVR)

apply_progress: haftalik gelisim - dusus birikime (development_progress) eklenir. Birikim +1'i
gecince overall 1 artar ve mevkinin EN AGIRLIKLI ozellikleri yukselir; -1'i gecince overall 1
duser ve FIZIKSEL ozellikler once gider (hiz her adimda 2, dribling 1, kalan agirlikli ozelliklerden).
Tum degerler 1-99 araliginda kalir.

Kondisyon (fitness.recover_condition): 32 yas ve ustu mac sonrasi daha yavas toparlanir
(age_recovery_factor).

Emeklilik (Faz 15B, bolum 5; kural bayragi RETIREMENT): yillik tehlike orani = yaslanma egrisi
(season_decline) x guc x sozlesme x kulupsuzluk. Zar tohumludur ve career_manager'da atilir; bu modul
yalnizca olasiligi hesaplar.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass

from models import Position
from ratings import ENGINE_ATTRIBUTES, POSITION_WEIGHTS, ca_to_overall

RATING_MIN, RATING_MAX = 1, 99
_EPS = 1e-6                        # 7 x (1/7) kayan nokta toplami 0.9999999 kalip bir hafta gecikmesin

# ===========================================================================
# 1) WONDERKID VE POTANSIYEL
# ===========================================================================

WONDERKID_MIN_AGE, WONDERKID_MAX_AGE = 16, 21
WONDERKID_MIN_GAP = 15


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def clamp_rating(value: float) -> int:
    return int(_clamp(round(value), RATING_MIN, RATING_MAX))


def effective_potential(overall: int, potential: int | None) -> int:
    """Potansiyel atanmamissa (None) overall; tavan hicbir zaman overall'in altinda sayilmaz."""
    return overall if potential is None else max(overall, potential)


def is_wonderkid(age: int, overall: int, potential: int | None) -> bool:
    """16-21 yas ve potansiyel - overall >= 15."""
    if potential is None or not WONDERKID_MIN_AGE <= age <= WONDERKID_MAX_AGE:
        return False
    return potential - overall >= WONDERKID_MIN_GAP


# Yasa gore ortalama potansiyel payi (overall ustu). 28+ -> 0 (potansiyel = overall).
POTENTIAL_HEADROOM_BY_AGE: dict[int, float] = {
    15: 17.0, 16: 16.0, 17: 14.0, 18: 12.0, 19: 10.0, 20: 8.0, 21: 6.5,
    22: 5.0, 23: 3.5, 24: 2.5, 25: 1.5, 26: 1.0, 27: 0.5,
}
HEADROOM_SPREAD = 0.5              # standart sapma = ortalama x bu oran
HIGH_CEILING_CHANCE = 0.06         # genc oyuncuda nadir yuksek tavan
HIGH_CEILING_BONUS = (8.0, 16.0)


def headroom_mean(age: int) -> float:
    if age < min(POTENTIAL_HEADROOM_BY_AGE):
        return POTENTIAL_HEADROOM_BY_AGE[min(POTENTIAL_HEADROOM_BY_AGE)]
    return POTENTIAL_HEADROOM_BY_AGE.get(age, 0.0)


def initial_potential(rng: random.Random, age: int, overall: int) -> int:
    """
    Kurgusal oyuncuya baslangic potansiyeli (1-99, >= overall).
    Gencin ortalama payi yasla azalir (17 -> ~14, 21 -> ~6.5, 25 -> ~1.5); 21 yas ve altinda
    %6 ihtimalle nadir yuksek tavan (+8..16). 28 yas ve ustu: potansiyel = overall.
    """
    overall = clamp_rating(overall)
    mean = headroom_mean(age)
    if mean <= 0:
        return overall
    headroom = max(0.0, rng.gauss(mean, mean * HEADROOM_SPREAD))
    if age <= WONDERKID_MAX_AGE and rng.random() < HIGH_CEILING_CHANCE:
        headroom += rng.uniform(*HIGH_CEILING_BONUS)
    return int(_clamp(round(overall + headroom), overall, RATING_MAX))


def potential_from_fm(pa_1_200: int | None, overall: int) -> int:
    """FM Potential Ability (1-200) -> potansiyel (overall ile ayni ca_to_overall olcegi), >= overall."""
    overall = clamp_rating(overall)
    if pa_1_200 is None or pa_1_200 <= 0:
        return overall
    return max(overall, ca_to_overall(pa_1_200))


# Gozlemci sisi: SCOUT judging_potential -> potansiyel tahmininin yanilma payi (+-)
NO_SCOUT_POTENTIAL_MARGIN = 12


def potential_scout_margin(judging_potential: int | None) -> int:
    """
    Potansiyel tahmininin yanilma payi. Gozlemci yoksa 12; 1 -> 11, 10 -> 6, 17 -> 1,
    18 ve ustu (mukemmel) -> 0: kesin.
    """
    if judging_potential is None:
        return NO_SCOUT_POTENTIAL_MARGIN
    return int(_clamp(round(12 - 0.65 * judging_potential), 0, NO_SCOUT_POTENTIAL_MARGIN))


# ===========================================================================
# 2) HAFTALIK GELISIM
# ===========================================================================

REFERENCE_SEASON_WEEKS = 7
GROWTH_RATE = 0.16
GAP_EXPONENT = 0.45

GROWTH_AGE_FACTOR: dict[int, float] = {
    22: 0.85, 23: 0.70, 24: 0.55, 25: 0.40, 26: 0.28, 27: 0.18, 28: 0.10, 29: 0.04,
}
PEAK_GROWTH_MAX_AGE = 21
GROWTH_END_AGE = 30                # bu yas ve ustu gelismez

TRAINING_BASELINE = 0.20           # oynamayan A takim oyuncusu (yalnizca antrenman)
ACADEMY_BASELINE = 0.55            # akademi: U-21 maclari (duzenli A takim oyuncusunun altinda)
FULL_MATCH_MINUTES = 90
FULL_MATCH_FACTOR = 1.00
DOUBLE_MATCH_BONUS = 0.15          # ayni hafta kupa + lig (180 dk) -> 1.15

# 15G: 6.00 -> 6.54.
#
# DIKKAT -- bu capa motorun ortalama notu DEGILDIR ve olmasi da gerekmez. Capa, ortalamanin
# BUYUME EGILIMI KADAR ALTIDIR. Eski sistemde de oyleydi:
#
#     durum              capa    gercek ortalama   fark (capa ortalamanin altinda)   performance_factor
#     14E (eski)         6.00    6.203             0.203                            1.0406
#     15G capa 6.72      6.72    6.707            -0.013                            0.9969   <- egilim SIFIRLANIR
#     15G capa 6.54      6.54    6.707             0.167                            1.0401   <- egilim KORUNUR
#
# Yani oyunda basindan beri yapisal bir buyume egilimi var ve 15B'nin emeklilik / yeni nesil denge ayari
# TAM OLARAK ona dayaniyor. Capayi ortalamaya esitlemek ("notr = motorun ortalamasi") ilkeli gorunur ama
# o egilimi sessizce sifirlar, yani 15B'nin altindaki zemini ceker: olculdu, dunya 12. sezona kadar
# cokuyor ve sonra asiri toparlaniyordu (en kotu 10 sezonluk pencere 2.86 > bant 2).
# Bu yama YENIDEN CAPALAMA yamasidir, denge degistirme yamasi degil: 6.54 oyunun hep sahip oldugu egilimi korur.
#
# KARISTIRMA: career_manager.NEUTRAL_RATING (form capasi) motorun ortalamasina ESITTIR ve esit olmalidir --
# baska turlu form her mac tek yone suruklenir ve butun kadro tabanda ya da tavanda sikisir. Ikisi farkli
# isler yapar: form capasi bir DENGE noktasi, gelisim capasi bir SEVIYE ayaridir. Bkz. career_manager.py.
NEUTRAL_MATCH_RATING = 6.54
# 15G: 0.20 -> 0.24, TAM TELAFI (yeniden capalama; davranis degisikligi DEGIL).
# Esli olcum (kanit/15G/egim_probe.txt; ayni tohum, 114 kuluplu gercek dunya, 3 sezon, iki kol ayrisimiyor:
# ortalama guc 72.85 / 72.70): sezonluk oyuncu ortalamalarinin sapmasi bayrak KAPALIYKEN 0.333, ACIKKEN
# 0.277 -> oran 1.20. Egim 0.20'de birakilsaydi iyi ve kotu sezon arasindaki gelisim farki 15G oncesine gore
# ~%20 daralirdi; 15B'nin emeklilik / yeni nesil ayari o farkla yapilmisti. 0.20 x 1.20 = 0.24 zinciri
# 15G oncesiyle AYNI yerde tutar. (Sentetik tekduze kadrolarda olculen 1.5 kat gecersizdi; oradaki tek
# degisken gurultuydu. Seviye etkisi ihmal edilebilir: 1 + 0.24 x (6.707 - 6.72) = 0.9969.)
# Daralmanin sebebi YAPISALDIR: eski not gollere yasliyordu ve goller forvetlerde toplandigi icin oyunculari
# birbirinden uzaga saviyordu; 15G notu krediyi duellolara, zincire ve kurtarislara dagitiyor.
# ACIK SORU (Faz 15 kapanisi): sikisan dagilim daha ADIL bir dagilim olabilir; "gelisim farkinin dogru
# genisligi nedir" sorusu bu pakette cevaplanmadi. Bkz. notlar/15g_c_yamasi.md §1.1b.
PERFORMANCE_PER_POINT = 0.24
PERFORMANCE_BOUNDS = (0.70, 1.35)

COACH_NEUTRAL = 10
COACH_PER_POINT = 0.025
MORALE_NEUTRAL = 65
MORALE_PER_POINT = 0.005
MORALE_BOUNDS = (0.75, 1.20)
FACILITIES_NEUTRAL = 10
FACILITIES_PER_POINT = 0.02


def season_scale(season_weeks: int | None) -> float:
    """Haftalik oranin sezon uzunluguna gore carpani (referans 7 hafta)."""
    if not season_weeks or season_weeks <= 0:
        return 1.0
    return REFERENCE_SEASON_WEEKS / season_weeks


def age_growth_factor(age: int) -> float:
    """16-21 -> 1.0; 22 -> 0.85 ... 29 -> 0.04; 30+ -> 0."""
    if age <= PEAK_GROWTH_MAX_AGE:
        return 1.0
    if age >= GROWTH_END_AGE:
        return 0.0
    return GROWTH_AGE_FACTOR.get(age, 0.0)


def playing_factor(minutes: int | None, in_academy: bool) -> float:
    """
    Dakikaya gore oynama carpani.
        A takim: 0 dk -> 0.20 (antrenman tabani), 45 dk -> 0.60, 90 dk -> 1.00, 180 dk -> 1.15
        akademi: en az 0.55 (U-21 maclari)
    """
    m = max(0, minutes or 0)
    if m <= FULL_MATCH_MINUTES:
        senior = TRAINING_BASELINE + (FULL_MATCH_FACTOR - TRAINING_BASELINE) * m / FULL_MATCH_MINUTES
    else:
        extra = min(m - FULL_MATCH_MINUTES, FULL_MATCH_MINUTES) / FULL_MATCH_MINUTES
        senior = FULL_MATCH_FACTOR + DOUBLE_MATCH_BONUS * extra
    return max(senior, ACADEMY_BASELINE) if in_academy else senior


def performance_factor(avg_rating: float | None) -> float:
    """Ortalama mac notu: 6.0 notr, 7.0 -> 1.20, 5.0 -> 0.80 (0.70-1.35). Oynamadiysa 1.0."""
    if avg_rating is None:
        return 1.0
    return _clamp(1.0 + (avg_rating - NEUTRAL_MATCH_RATING) * PERFORMANCE_PER_POINT, *PERFORMANCE_BOUNDS)


def coach_youth_factor(working_with_youngsters: int | None) -> float:
    """Genc antrenoru (1-20): 1 -> 0.775, 10 -> 1.0, 20 -> 1.25. Antrenor yoksa ceza yok (1.0)."""
    if working_with_youngsters is None:
        return 1.0
    rating = _clamp(working_with_youngsters, 1, 20)
    return 1.0 + (rating - COACH_NEUTRAL) * COACH_PER_POINT


def morale_factor(morale: int | None) -> float:
    """Moral (0-100): 65 notr, 100 -> 1.175, 20 -> 0.775."""
    if morale is None:
        return 1.0
    return _clamp(1.0 + (morale - MORALE_NEUTRAL) * MORALE_PER_POINT, *MORALE_BOUNDS)


def facilities_factor(facilities: int | None) -> float:
    """Altyapi tesisi (1-20): 1 -> 0.82, 10 -> 1.0, 20 -> 1.20. Bilinmiyorsa notr."""
    if facilities is None:
        return 1.0
    return 1.0 + (_clamp(facilities, 1, 20) - FACILITIES_NEUTRAL) * FACILITIES_PER_POINT


def weekly_growth(
    age: int,
    overall: int,
    potential: int | None,
    minutes: int | None,
    avg_rating: float | None,
    morale: int | None,
    coach_youth: int | None,
    in_academy: bool,
    facilities: int | None,
    season_weeks: int | None = None,
) -> float:
    """Bu haftanin gelisimi (overall birimi, >= 0). Kalan farki (potansiyel - overall) asmaz."""
    gap = effective_potential(overall, potential) - overall
    age_factor = age_growth_factor(age)
    if gap <= 0 or age_factor <= 0:
        return 0.0
    growth = (
        GROWTH_RATE * gap ** GAP_EXPONENT * age_factor
        * playing_factor(minutes, in_academy)
        * performance_factor(avg_rating)
        * coach_youth_factor(coach_youth)
        * morale_factor(morale)
        * (facilities_factor(facilities) if in_academy else 1.0)
        * season_scale(season_weeks)
    )
    return max(0.0, min(float(gap), growth))


# ===========================================================================
# 3) YASLANMA
# ===========================================================================

DECLINE_START_AGE = 32
DECLINE_PER_SEASON = 1.0
DECLINE_GROWTH = 1.35
DECLINE_MAX_PER_SEASON = 8.0


def season_decline(age: int) -> float:
    """Sezon basina overall dususu: 32 -> 1.0, 34 -> 1.82, 36 -> 3.32, en fazla 8."""
    if age < DECLINE_START_AGE:
        return 0.0
    return min(DECLINE_MAX_PER_SEASON, DECLINE_PER_SEASON * DECLINE_GROWTH ** (age - DECLINE_START_AGE))


def weekly_decline(age: int, season_weeks: int | None = None) -> float:
    """Haftalik yaslanma dususu (overall birimi, >= 0). 32 yas altinda 0, yasla hizlanir."""
    return season_decline(age) / REFERENCE_SEASON_WEEKS * season_scale(season_weeks)


RECOVERY_AGE_STEP = 0.05
RECOVERY_AGE_FLOOR = 0.70


def age_recovery_factor(age: int | None) -> float:
    """Mac sonrasi toparlanma carpani: 32 alti 1.0; 32 -> 0.95, 34 -> 0.85, 37+ -> 0.70."""
    if age is None or age < DECLINE_START_AGE:
        return 1.0
    return max(RECOVERY_AGE_FLOOR, 1.0 - RECOVERY_AGE_STEP * (age - DECLINE_START_AGE + 1))


# ===========================================================================
# 4) BIRIKIMI GUC VE OZELLIKLERE CEVIRME
# ===========================================================================

# Gerilemede once giden fiziksel ozellikler: (ozellik, adim basina dusus)
PHYSICAL_DECLINE: tuple[tuple[str, int], ...] = (("pace", 2), ("dribbling", 1))
MAX_ATTRIBUTE_STEP = 2


def _weight_order(position: Position) -> list[str]:
    weights = POSITION_WEIGHTS[position]
    return sorted((a for a in ENGINE_ATTRIBUTES if weights[a] > 0), key=lambda a: (-weights[a], a))


def raise_attributes(position: Position, attributes: Mapping[str, int], balanced: bool = False) -> dict[str, int]:
    """
    Overall +1 icin ozellikler.
        balanced=False (15B oncesi): mevkinin EN AGIRLIKLI ozelliginden baslanir, agirlikli toplam ~1 artana
            kadar her birine +2 (tasmiyorsa) ya da +1 eklenir. Ornek FWD: sut +2, hiz +1.
        balanced=True (Faz 15B): mevkinin agirlik VEREN her ozelligi +1 alir. Agirliklarin toplami 1 oldugu
            icin agirlikli kazanc yine tam +1'dir, ama oyuncunun PROFILI korunur.
    99'daki ozellik atlanir (kalan kazanc yeri olan ozelliklere dagitilir); agirliksiz ozellik (saha
    oyuncusunun kaleciligi) degismez.

    NEDEN (CM dersi 1: "doyumsuz, sinirsiz buyuyen ozellik"): eski kural her +1 gucu TEK ozellige yiginca
    uzun kariyerlerde profil bozuluyordu -- stoperin savunmasi +2/puan, kalecinin kalecilik ozelligi ise
    agirligi 0,7 oldugu icin yalnizca +1/puan artiyor. 20 sezonluk kosuda saha oyuncularinin anahtar
    ozellikleri ~+2,5 sisip kaleciler geride kaliyor ve gol/mac bandi yukari kaciyordu (olculdu; 15B teslim
    notu §1.5). Dengeli kural bu farki kapatir.
    """
    weights = POSITION_WEIGHTS[position]
    attrs = {a: int(attributes[a]) for a in ENGINE_ATTRIBUTES}
    gained = 0.0
    order = _weight_order(position)
    if balanced:
        for attr in order:
            if attrs[attr] < RATING_MAX:
                attrs[attr] += 1
                gained += weights[attr]
    for attr in order:                       # klasik kural + dengeli kuralda 99'a dayanan ozelliklerin telafisi
        if gained >= 1.0 - 1e-9:
            break
        room = RATING_MAX - attrs[attr]
        if room <= 0:
            continue
        step = MAX_ATTRIBUTE_STEP if gained + MAX_ATTRIBUTE_STEP * weights[attr] <= 1.0 + 1e-9 else 1
        step = min(step, room)
        attrs[attr] += step
        gained += step * weights[attr]
    return attrs


def lower_attributes(position: Position, attributes: Mapping[str, int]) -> dict[str, int]:
    """
    Overall -1 icin ozellikler: once fiziksel dusus (hiz -2, dribling -1), agirlikli toplam
    ~1 azalana kadar mevkinin en agirlikli diger ozelliklerinden -1. 1'deki ozellik atlanir.
    """
    weights = POSITION_WEIGHTS[position]
    attrs = {a: int(attributes[a]) for a in ENGINE_ATTRIBUTES}
    lost = 0.0
    for attr, amount in PHYSICAL_DECLINE:
        drop = min(amount, attrs[attr] - RATING_MIN)
        attrs[attr] -= drop
        lost += drop * weights[attr]
    physical = {a for a, _ in PHYSICAL_DECLINE}
    for attr in _weight_order(position):
        if lost >= 1.0 - 1e-9:
            break
        if attr in physical or attrs[attr] <= RATING_MIN:
            continue
        attrs[attr] -= 1
        lost += weights[attr]
    return attrs


@dataclass(frozen=True)
class DevelopmentStep:
    """apply_progress sonucu: yeni guc, ozellikler, birikim ve potansiyel."""
    overall: int
    potential: int
    attributes: dict[str, int]
    progress: float
    change: int                        # overall'in net degisimi (+/-)


def apply_progress(
    position: Position,
    overall: int,
    potential: int | None,
    attributes: Mapping[str, int],
    progress: float,
    growth: float = 0.0,
    decline: float = 0.0,
    balanced: bool = False,
) -> DevelopmentStep:
    """
    Birikime bu haftanin gelisimini ekler, gerilemesini cikarir; tam sayiyi gecen kismi
    overall'a ve ozelliklere yansitir.
        * overall potansiyeli (ve 99'u) asamaz; tavandaki oyuncunun artan birikimi silinir
        * gerileyen (32+) oyuncunun potansiyeli yeni overall'a iner (tavan artik gecmiste kaldi)
        * overall 1'in altina inmez
        * balanced (Faz 15B kural bayragi): yukselirken profil korunur (raise_attributes)
    """
    overall = clamp_rating(overall)
    ceiling = min(RATING_MAX, effective_potential(overall, potential))
    attrs = {a: int(attributes[a]) for a in ENGINE_ATTRIBUTES}
    start = overall
    total = float(progress or 0.0) + max(0.0, growth) - max(0.0, decline)

    while total >= 1.0 - _EPS and overall < ceiling:
        overall += 1
        total -= 1.0
        attrs = raise_attributes(position, attrs, balanced)
    if overall >= ceiling and total > 0:
        total = 0.0
    if abs(total) < _EPS:
        total = 0.0
    declined = False
    while total <= -1.0 + _EPS and overall > RATING_MIN:
        overall -= 1
        total += 1.0
        attrs = lower_attributes(position, attrs)
        declined = True
    if overall <= RATING_MIN and total < 0:
        total = 0.0

    new_potential = overall if declined else max(ceiling, overall)
    return DevelopmentStep(overall, new_potential, attrs, total, overall - start)


# ===========================================================================
# 5) EMEKLILIK (Faz 15B)
# ===========================================================================
#
# KURAL BAYRAGI
#     RETIREMENT  modul sabiti (CareerManager.retirement ile kopya basina ezilebilir). False iken oyun 15B
#                 oncesiyle BIREBIR aynidir: sezon devrinde emeklilik adimi hic calismaz ve genc girisi eski
#                 sabit YOUTH_INTAKE_SIZE ile uretilir (tek fazladan sorgu bile yok).
#
# TASARIM
#     Emeklilik yillik bir TEHLIKE (hazard) orani olarak modellenir ve tohumlu zarla cekilir (career_manager
#     crc32 ile turetir; cm.rng'ye dokunulmaz). Yas terimi YASLANMA EGRISININ KENDISIDIR (season_decline):
#     boylece "gerileme" ile "emeklilik" tek kaynaktan gelir, ikinci bir yas tablosu tutulmaz.
#         taban = RETIRE_RATE x season_decline(yas) ** RETIRE_DECLINE_EXPONENT
#         32 -> 0.06   33 -> 0.10   34 -> 0.16   35 -> 0.25   36 -> 0.41   37 -> 0.66   38 -> 0.98 -> tavan
#         30-31 gerileme baslamadan once kucuk bir taban (RETIRE_EARLY_HAZARD), RETIRE_FORCED_AGE (41) zorunlu.
#     Carpanlar:
#         guc        : iyi oyuncu daha uzun oynar. 58 notr; 78 -> x0.64, 38 -> x1.36 (RETIRE_QUALITY_BOUNDS)
#         sozlesme   : bitmis sozlesme x1.45, 2+ yil sozlesme x0.70 (kulup istiyor)
#         kulupsuz   : x1.80; ayrica 26 yas ustu kulupsuz oyuncuya isizlik tabani
#                      RETIRE_CLUBLESS_FLOOR x (1 + kulupsuz sezon): havuz sonsuza kadar buyumez
#     Sonuc RETIRE_MAX ile sinirlidir (zorunlu yas haric).
#
# Kalibrasyon (carpansiz, 30 yasindan itibaren): medyan emeklilik yasi ~35, 38 yasindan sonra cok az oyuncu
# kalir; guclu oyuncu (x0.64) 38-39'a kadar oynar, zayif oyuncu (x1.36) 33-34'te biter. Oranlar 20 sezonluk
# acik veri kosusunda nufus / ortalama yas / ortalama guc bandina gore secildi (bkz. 15B teslim notu).

RETIREMENT = True                   # Faz 15B kural bayragi

RETIRE_MIN_AGE = 30                 # bu yasin altinda (kulupsuz kurali disinda) emeklilik yok
RETIRE_FORCED_AGE = 41              # bu yas ve ustu kesin emekli
RETIRE_RATE = 0.06                  # 32 yasindaki taban (season_decline == 1.0)
RETIRE_DECLINE_EXPONENT = 1.6
RETIRE_EARLY_HAZARD: dict[int, float] = {30: 0.015, 31: 0.035}
RETIRE_MAX = 0.98

RETIRE_QUALITY_PIVOT = 58           # bu gucteki oyuncu notr
RETIRE_QUALITY_PER_POINT = 0.018
RETIRE_QUALITY_BOUNDS = (0.45, 1.70)

RETIRE_NO_CONTRACT = 1.45           # contract_years <= 0
RETIRE_LONG_CONTRACT_YEARS = 2
RETIRE_LONG_CONTRACT = 0.70

RETIRE_CLUBLESS = 1.80
RETIRE_CLUBLESS_MIN_AGE = 26        # bu yastan itibaren isizlik tabani isler
RETIRE_CLUBLESS_FLOOR = 0.10        # kulupsuz gecen her sezon bu kadar ekler
WEEKS_PER_YEAR = 52


def retirement_base(age: int) -> float:
    """Yasa gore yillik emeklilik tabani (0-1). 30 alti 0; 32+ yaslanma egrisinden; 41+ kesin."""
    age = int(age)
    if age >= RETIRE_FORCED_AGE:
        return 1.0
    if age < RETIRE_MIN_AGE:
        return 0.0
    if age < DECLINE_START_AGE:
        return RETIRE_EARLY_HAZARD.get(age, 0.0)
    return min(1.0, RETIRE_RATE * season_decline(age) ** RETIRE_DECLINE_EXPONENT)


def retirement_quality_factor(overall: int) -> float:
    """Guce gore carpan: 58 notr, 78 -> 0.64, 38 -> 1.36 (0.45-1.70)."""
    factor = 1.0 - (int(overall) - RETIRE_QUALITY_PIVOT) * RETIRE_QUALITY_PER_POINT
    return _clamp(factor, *RETIRE_QUALITY_BOUNDS)


def retirement_contract_factor(contract_years: int | None, clubless: bool = False) -> float:
    """Sozlesme durumu carpani: bitmis 1.45, 2+ yil 0.70, arasi 1.0. Kulupsuz oyuncu 'bitmis' sayilir."""
    years = 0 if clubless else int(contract_years or 0)
    if years <= 0:
        return RETIRE_NO_CONTRACT
    if years >= RETIRE_LONG_CONTRACT_YEARS:
        return RETIRE_LONG_CONTRACT
    return 1.0


def retirement_chance(
    age: int,
    overall: int,
    contract_years: int | None = 1,
    clubless: bool = False,
    weeks_clubless: int = 0,
) -> float:
    """
    Bu sezon sonunda emekli olma olasiligi (0-1). Girdiler: yas (gerileme egrisi), guc, sozlesme ve kulupsuzluk.
    Kulupsuz oyuncunun 26 yasindan itibaren isizlik tabani vardir (her kulupsuz sezon tabani bir kat artirir).
    """
    age = int(age)
    if age >= RETIRE_FORCED_AGE:
        return 1.0
    chance = (
        retirement_base(age)
        * retirement_quality_factor(overall)
        * retirement_contract_factor(contract_years, clubless)
        * (RETIRE_CLUBLESS if clubless else 1.0)
    )
    if clubless and age >= RETIRE_CLUBLESS_MIN_AGE:
        seasons = max(0.0, float(weeks_clubless or 0)) / WEEKS_PER_YEAR
        chance = max(chance, RETIRE_CLUBLESS_FLOOR * (1.0 + seasons))
    return _clamp(chance, 0.0, RETIRE_MAX)


def retirement_reason(age: int, overall: int, clubless: bool = False) -> str:
    """Emeklilik haberindeki kisa gerekce (Turkce)."""
    if clubless:
        return "kulüp bulamadı"
    if age >= RETIRE_FORCED_AGE - 2:
        return "yaş"
    if overall >= RETIRE_QUALITY_PIVOT + 12:
        return "zirvedeyken bıraktı"
    return "yaş ve düşen form"
