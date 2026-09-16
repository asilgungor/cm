"""
fitness.py
==========
Dinamik kondisyon kurallari. SAF MANTIK: veritabani ve ORM bilmez.

Zincir:
    players.condition (0-100)  --mac basi-->  MatchPlayer.energy
    energy dakika dakika duser (rol, yas, FM dayaniklilik, efor, bastirma)
    energy -> guc carpani (fatigue_factor): hem mac ici guc hem kadro secimi
    mac sonu enerjisi dusukse hata yapar -> mac notu cezasi (fatigue_rating_penalty)
    dusuk not -> form/moral duser (career_manager'daki mevcut dongu)
    mac sonrasi toparlanma: saglikci ne kadar iyiyse kondisyon o kadar geri gelir
    macta oynamayan (kulube, kadro disi, sakat, cezali) tam dinlenir -> 100

Bantlar (arayuz renkleri icin):
    good >= 80   |   warn 60-79   |   low < 60
Taktik ekrani CONDITION_WARN (70) altindaki ilk 11 oyuncusu icin uyari verir.
"""

from __future__ import annotations

from development import age_recovery_factor

# ===========================================================================
# SINIRLAR VE BANTLAR
# ===========================================================================

CONDITION_MIN = 0
CONDITION_MAX = 100
CONDITION_WARN = 70         # taktik ekrani bu degerin altini uyarir
CONDITION_GOOD = 80         # bu ve ustu: "good"
CONDITION_LOW = 60          # bunun alti: "low"


def clamp_condition(value: float) -> int:
    """Kondisyonu 0-100 tam sayiya indirger."""
    return int(max(CONDITION_MIN, min(CONDITION_MAX, round(value))))


def condition_of(player) -> int:
    """
    Nesnenin kondisyonu (duck typing). Alan yoksa veya henuz flush edilmemis
    ORM nesnesinde None ise tam kondisyon (100) kabul edilir.
    """
    value = getattr(player, "condition", None)
    return CONDITION_MAX if value is None else clamp_condition(value)


def condition_band(condition: float) -> str:
    """'good' (>=80) | 'warn' (60-79) | 'low' (<60)."""
    if condition >= CONDITION_GOOD:
        return "good"
    if condition >= CONDITION_LOW:
        return "warn"
    return "low"


# ===========================================================================
# GUC CARPANI
# ===========================================================================

FATIGUE_FLOOR = 0.75        # enerji 0 iken guc carpani


def fatigue_factor(energy: float) -> float:
    """Enerji 100 -> 1.00, enerji 0 -> 0.75 (dogrusal). Motor ve taktik ayni formulu kullanir."""
    e = max(0.0, min(100.0, float(energy)))
    return FATIGUE_FLOOR + (1.0 - FATIGUE_FLOOR) * e / 100.0


# ===========================================================================
# YORULMA HIZI (FM dayaniklilik)
# ===========================================================================

STAMINA_NEUTRAL_MULTIPLIER = 1.2
STAMINA_STEP = 0.02


def stamina_decay_multiplier(stamina: float | None) -> float:
    """
    FM 'Stamina' (1-20) -> enerji kaybi carpani. Veri yoksa 1.0 (notr).
        stamina  1 -> 1.18   stamina 10 -> 1.00   stamina 20 -> 0.80
    """
    if stamina is None:
        return 1.0
    s = max(1.0, min(20.0, float(stamina)))
    return STAMINA_NEUTRAL_MULTIPLIER - STAMINA_STEP * s


# ===========================================================================
# MAC NOTU CEZASI
# ===========================================================================

# Esik kalibrasyonu: tam kondisyonla 90 dk oynayan 26 yasindaki orta saha maci ~40
# enerjiyle bitirir. Esik 55 olsaydi taze oyuncular da cezalanirdi (ortalama not
# 6.23 -> 5.99; her hafta oynayan ilk 11'de denge notu 5.3-5.8 -> form/moral cokusu).
# Esik 35: taze oyuncu cezasiz (6.23), duzenli oynayanlarda saglikci belirleyici
# (saglikci yok 5.77, rating 10 -> 6.04, rating 20 -> 6.18).
TIRED_RATING_THRESHOLD = 35.0   # mac sonu enerjisi bunun altindaysa hata yapar
TIRED_RATING_PER_POINT = 0.03   # esigin altindaki her enerji puani icin not kaybi


def fatigue_rating_penalty(end_energy: float) -> float:
    """
    Yorgun oyuncu hata yapar: mac notundan dusulecek miktar.
        enerji 35+ -> 0.00   enerji 20 -> 0.45   enerji 0 -> 1.05
    """
    return max(0.0, TIRED_RATING_THRESHOLD - float(end_energy)) * TIRED_RATING_PER_POINT


# ===========================================================================
# MAC SONRASI TOPARLANMA (PHYSIO)
# ===========================================================================

NO_PHYSIO_RECOVERY = 0.60
RECOVERY_PER_PHYSIO_POINT = 0.015


def recovery_rate(physio_rating: int | None) -> float:
    """
    Mac sonrasi eksik kondisyonun geri gelen orani.
    Saglikci yoksa 0.60; rating 1 -> 0.615, rating 10 -> 0.75, rating 20 -> 0.90.
    """
    if physio_rating is None:
        return NO_PHYSIO_RECOVERY
    return max(0.0, min(1.0, NO_PHYSIO_RECOVERY + RECOVERY_PER_PHYSIO_POINT * physio_rating))


# Ayni hafta once kupa (hafta ici) sonra lig (hafta sonu) oynayan takimda iki mac arasi
# sure kisadir: eksik kondisyonun ancak bu kadari geri gelir (rotasyon ihtiyaci dogar).
MIDWEEK_RECOVERY_SHARE = 0.5


def recover_condition(
    end_energy: float, physio_rating: int | None, share: float = 1.0, age: int | None = None
) -> int:
    """
    Mac sonu enerjisinden bir sonraki maca tasinacak kondisyon.
        min(100, round(end + (100 - end) * rate * share * yas_carpani))
    Ornek: enerji 56, saglikci 10 -> 56 + 44 * 0.75 = 89.
    Hafta ici mac (share 0.5): 56 + 44 * 0.75 * 0.5 = 72.5 -> 72.
    Yas (10. Asama): 32 ve ustu daha yavas toparlanir (development.age_recovery_factor;
    34 yas -> 0.85: 56 + 44 * 0.75 * 0.85 = 84). age None -> eski davranis.
    """
    end = max(float(CONDITION_MIN), min(float(CONDITION_MAX), float(end_energy)))
    rate = recovery_rate(physio_rating) * max(0.0, min(1.0, share)) * age_recovery_factor(age)
    return min(CONDITION_MAX, round(end + (CONDITION_MAX - end) * rate))
