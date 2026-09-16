"""
staff.py
========
Teknik heyet kurallari ve oyuna etkileri (5. Asama). SAF MANTIK: DB bilmez.

Roller ve 1-20 arasi alt ozellikleri:
    COACH     : attacking, defending, tactical, working_with_youngsters
    SCOUT     : judging_ability, judging_potential
    PHYSIO    : physiotherapy
    ASSISTANT : man_management, determination, tactical_knowledge

Oyuna etkileri:
    PHYSIO    -> sakatlik suresi carpani (iyi saglikci 4 haftayi 2 haftaya indirir)
    SCOUT     -> rakip oyuncularin ozellikleri sisli gosterilir; iyi gozlemci sisi azaltir
    COACH     -> mac sonrasi form degisiminin carpani (hucum/savunma ayri)
    ASSISTANT -> moral degisiminin carpani (adam yonetimi)
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

from models import Position, StaffRole

ATTR_MIN, ATTR_MAX = 1, 20

# Role gore anlamli olan ozellikler (digerleri 1'de kalir)
ROLE_ATTRIBUTES: dict[StaffRole, tuple[str, ...]] = {
    StaffRole.COACH: ("attacking", "defending", "tactical", "working_with_youngsters"),
    StaffRole.SCOUT: ("judging_ability", "judging_potential"),
    StaffRole.PHYSIO: ("physiotherapy",),
    StaffRole.ASSISTANT: ("man_management", "determination", "tactical_knowledge"),
}

ROLE_LABELS: dict[StaffRole, str] = {
    StaffRole.COACH: "Antrenör",
    StaffRole.SCOUT: "Gözlemci",
    StaffRole.PHYSIO: "Sağlıkçı",
    StaffRole.ASSISTANT: "Asistan Menajer",
}

ATTR_LABELS: dict[str, str] = {
    "attacking": "Hücum",
    "defending": "Savunma",
    "tactical": "Taktiksel",
    "working_with_youngsters": "Gençlerle Çalışma",
    "judging_ability": "Yetenek Değerlendirme",
    "judging_potential": "Potansiyel Değerlendirme",
    "physiotherapy": "Tedavi Yeteneği",
    "man_management": "Adam Yönetimi",
    "determination": "Kararlılık",
    "tactical_knowledge": "Taktiksel Bilgi",
}

# Personel haftalik maas tabani (rol bazli), itibar ile olceklenir
ROLE_BASE_WAGE: dict[StaffRole, int] = {
    StaffRole.COACH: 6_000,
    StaffRole.SCOUT: 3_500,
    StaffRole.PHYSIO: 4_000,
    StaffRole.ASSISTANT: 9_000,
}

# Her takimda bulunabilecek rol basina en fazla personel
MAX_PER_ROLE: dict[StaffRole, int] = {
    StaffRole.COACH: 2,
    StaffRole.SCOUT: 2,
    StaffRole.PHYSIO: 1,
    StaffRole.ASSISTANT: 1,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def clamp_attr(value: float) -> int:
    return int(_clamp(round(value), ATTR_MIN, ATTR_MAX))


# ---------------------------------------------------------------------------
# Uretim
# ---------------------------------------------------------------------------

def staff_wage(role: StaffRole, reputation: int) -> int:
    """Personelin haftalik maasi. Itibar 50 referans, 100'e yuvarlanir."""
    factor = _clamp((reputation / 50) ** 1.4, 0.3, 4.0)
    return int(round(ROLE_BASE_WAGE[role] * factor / 100) * 100)


def generate_attributes(rng, role: StaffRole, reputation: int) -> dict[str, int]:
    """
    Itibardan (1-100) alt ozellik uretir. Itibar 50 -> ~10, itibar 90 -> ~17.
    Ilgisiz ozellikler 1'de kalir.
    """
    center = 2 + (reputation / 100) * 16          # 1-18 bandi
    attrs = dict.fromkeys(ATTR_LABELS, ATTR_MIN)
    for name in ROLE_ATTRIBUTES[role]:
        attrs[name] = clamp_attr(rng.gauss(center, 1.8))
    return attrs


def key_attribute(role: StaffRole) -> str:
    """Rolun en belirleyici ozelligi (listelerde tek sayi gostermek icin)."""
    return ROLE_ATTRIBUTES[role][0]


def rating_of(staff, attribute: str) -> int:
    return getattr(staff, attribute, ATTR_MIN) if staff is not None else ATTR_MIN


# ---------------------------------------------------------------------------
# PHYSIO -> sakatlik suresi
# ---------------------------------------------------------------------------

NO_PHYSIO_MULTIPLIER = 1.30


def injury_multiplier(physio_rating: int | None) -> float:
    """
    Sakatlik suresi carpani. Saglikci yoksa 1.30.
        rating  1 -> 1.40   (kotu saglikci sureyi uzatir)
        rating 10 -> 0.95
        rating 20 -> 0.45   (4 hafta -> 2 hafta)
    """
    if physio_rating is None:
        return NO_PHYSIO_MULTIPLIER
    return _clamp(1.45 - 0.05 * physio_rating, 0.45, 1.40)


def apply_injury_multiplier(base_weeks: int, physio_rating: int | None) -> int:
    """Sakatlik suresini saglikciya gore olcekler. En az 1 hafta."""
    return max(1, round(base_weeks * injury_multiplier(physio_rating)))


# ---------------------------------------------------------------------------
# COACH / ASSISTANT -> form ve moral
# ---------------------------------------------------------------------------

ATTACKING_POSITIONS = (Position.FWD, Position.MID)


def coach_attribute_for(position: Position) -> str:
    """Hucum oyuncusuna hucum antrenoru, defansa savunma antrenoru bakar."""
    return "attacking" if position in ATTACKING_POSITIONS else "defending"


def training_multiplier(rating: int | None) -> float:
    """
    Antrenor/asistan carpani. Personel yoksa 1.0 (notr).
        rating  1 -> 0.82   rating 10 -> 1.00   rating 20 -> 1.20
    """
    if rating is None:
        return 1.0
    return _clamp(0.80 + 0.02 * rating, 0.80, 1.20)


def apply_training(delta: int, rating: int | None) -> int:
    """
    Form/moral degisimini personel kalitesine gore olcekler.
    Kazanc carpanla buyur; kayip ayni carpanla YUMUSAR (iyi kadro dususu frenler).
    """
    mult = training_multiplier(rating)
    if delta > 0:
        return int(round(delta * mult))
    if delta < 0:
        return int(round(delta / mult))
    return 0


# ---------------------------------------------------------------------------
# SCOUT -> bilgi sisi
# ---------------------------------------------------------------------------

NO_SCOUT_MARGIN = 12


def scout_margin(judging_ability: int | None) -> int:
    """
    Rakip oyuncu ozelliklerindeki yanilma payi (+-).
    Gozlemci yoksa 12; rating 1 -> 11, rating 10 -> 6, rating 20 -> 1.
    """
    if judging_ability is None:
        return NO_SCOUT_MARGIN
    return int(_clamp(round(12 - 0.55 * judging_ability), 1, NO_SCOUT_MARGIN))


def _stable_offset(seed_parts: tuple, margin: int) -> int:
    """
    Ayni gozlemci + ayni oyuncu + ayni ozellik icin HER ZAMAN ayni sapma.
    (Rapor her acilista degismesin diye hash yerine crc32 kullanilir.)
    """
    if margin <= 0:
        return 0
    key = "|".join(str(p) for p in seed_parts).encode("utf-8")
    return zlib.crc32(key) % (2 * margin + 1) - margin


@dataclass(frozen=True)
class ScoutedValue:
    """Gozlemcinin bir sayi hakkindaki tahmini."""
    low: int
    high: int
    exact: bool

    @property
    def midpoint(self) -> int:
        return (self.low + self.high) // 2

    def __str__(self) -> str:
        return str(self.low) if self.exact else f"{self.low}-{self.high}"


def scouted_value(
    true_value: int,
    margin: int,
    seed_parts: tuple,
    lo: int = 1,
    hi: int = 99,
) -> ScoutedValue:
    """Gercek degeri yanilma payiyla bir araliga cevirir. margin=0 -> kesin."""
    if margin <= 0:
        return ScoutedValue(true_value, true_value, True)
    center = true_value + _stable_offset(seed_parts, margin)
    return ScoutedValue(
        int(_clamp(center - margin, lo, hi)),
        int(_clamp(center + margin, lo, hi)),
        False,
    )


def scouted_money(true_value: int, margin: int, seed_parts: tuple) -> ScoutedValue:
    """Piyasa degeri tahmini: yanilma payi yuzde olarak uygulanir."""
    if margin <= 0:
        return ScoutedValue(true_value, true_value, True)
    pct = margin / 100.0
    offset = _stable_offset(seed_parts, margin) / 100.0
    center = true_value * (1 + offset)
    return ScoutedValue(int(center * (1 - pct)), int(center * (1 + pct)), False)
