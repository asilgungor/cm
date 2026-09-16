"""
facilities.py
=============
Kulup tesisleri ve sponsorluk ekonomisi (11. Asama). SAF MANTIK: veritabani ve ORM bilmez.

Tesisler (teams tablosu):
    youth_facilities   1-20   altyapi: genc girisinin potansiyeli (youth.quality_index) ve akademi
                              gelisim hizi (development.facilities_factor)
    medical_facilities 1-20   saglik merkezi: mac sonrasi kondisyon toparlanma ORANINI carpar
                              (saglikci puaniyla BIRLIKTE, fitness.recover_condition). 10 notr.
    stadium_capacity   koltuk ic saha mac gunu geliri (gate_income): seyirci = min(kapasite, talep)

Yukseltme maliyeti (EUR, transfer kasasindan duser):
    altyapi  seviye L -> L+1: 800K x 1.16^(L-1)   (1->2 800K, 10->11 3.0M, 15->16 6.4M, 19->20 11.6M)
    saglik   seviye L -> L+1: 700K x 1.16^(L-1)   (1->2 700K, 10->11 2.7M, 15->16 5.6M, 19->20 10.1M)
    stadyum  +5.000 koltuk  : koltuk basina 400 + 10 x (kapasite / 1000) EUR
                              (10K -> 2.5M, 50K -> 4.5M, 85K -> 6.25M)
    Kalibrasyon (sentetik dunya): orta kulup (itibar 85, kasa ~90M, altyapi ~14) bir sonraki altyapi
    seviyesi icin ~%6.5, stadyum icin ~%5 oder; kucuk kulup (itibar 73, kasa 25M) ~%14.

Mac gunu geliri (ic saha maci basina, net, EUR):
    talep(itibar)  = 10.000 + 80.000 x x^1.6          x = (itibar - 40) / 60, 0-1 araligina kirpilir
    getiri(itibar) = 6 + 30 x x^1.5  EUR/seyirci      (bilet + mac gunu harcamasi, isletme gideri sonrasi)
    itibar 50 / 12K koltuk -> ~96K · itibar 73 / 33K -> ~601K · itibar 85 / 50K -> ~1.28M · itibar 95 / 70K -> ~2.26M
    Talep kapasitenin altindaysa genisletme gelir getirmez (gercek bir karar).

Sponsorluk:
    temel haftalik(itibar) = 15.000 + 700.000 x x^2.2  (itibar 50 -> 29K, 73 -> 203K, 85 -> 387K, 95 -> 593K)
    Teklif profilleri (count=3 -> her profilden bir teklif, bu sirayla):
        yuksek haftalik : temel x 1.15-1.25, 1 sezon, imza primi yok
        uzun vade       : temel x 1.00-1.08, 3-4 sezon, prim 0.15-0.30 sezonluk temel bedel
        imza primi      : temel x 0.78-0.88, 2 sezon, prim 0.50-0.70 sezonluk temel bedel
    Primler SEZON UZUNLUGUYLA olceklenir (season_weeks; sentetik dunya ~7, FM ~38 hafta): sabit haftalik prim
    kisa sezonda her seyi ezerdi ve kulupler her sezon sozlesme bozup prim toplardi. Boylece uc profilin sezon
    basina degeri (offer_score) her dunyada birbirine yakindir; secim nakit (prim) / gelir / guvence dengesidir.
    Sozlesme sponsor_until_season (dahil) sonunda biter. Markalar KURGUSALDIR (uydurma kokler).
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import development
import youth

# ===========================================================================
# SINIRLAR
# ===========================================================================

FACILITY_MIN = 1
FACILITY_MAX = 20
FACILITY_KINDS: tuple[str, ...] = ("youth", "medical")
UPGRADE_KINDS: tuple[str, ...] = ("youth", "medical", "stadium")
FACILITY_LABELS: dict[str, str] = {
    "youth": "Altyapı tesisleri",
    "medical": "Sağlık merkezi",
    "stadium": "Stadyum",
}

STADIUM_MIN = 10_000
STADIUM_MAX = 90_000
STADIUM_STEP = 5_000


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _reputation_share(reputation: int | None) -> float:
    """Itibar -> 0..1 olcegi: 40 ve alti 0, 100 -> 1."""
    rep = 70.0 if reputation is None else float(reputation)
    return _clamp((rep - 40.0) / 60.0, 0.0, 1.0)


def _check_kind(kind: str, allowed: Sequence[str] = FACILITY_KINDS) -> str:
    if kind not in allowed:
        raise ValueError(f"Bilinmeyen tesis türü: {kind!r} (seçenekler: {', '.join(allowed)})")
    return kind


# ===========================================================================
# YUKSELTME MALIYETLERI
# ===========================================================================

UPGRADE_BASE_COST: dict[str, int] = {"youth": 800_000, "medical": 700_000}
UPGRADE_COST_GROWTH = 1.16
STADIUM_SEAT_COST_BASE = 400            # EUR / yeni koltuk
STADIUM_SEAT_COST_PER_THOUSAND = 10     # mevcut her 1.000 koltuk icin koltuk basina ek EUR


def facility_upgrade_cost(kind: str, level: int) -> int:
    """
    `level` seviyesinden bir ust seviyeye cikmanin maliyeti (EUR, 10.000'e yuvarlanir).
    kind: "youth" | "medical". Seviye 1-19 olmali (20 zaten en ust: ValueError).
    """
    _check_kind(kind)
    if not FACILITY_MIN <= int(level) < FACILITY_MAX:
        raise ValueError(f"Tesis seviyesi {FACILITY_MIN}-{FACILITY_MAX - 1} arasında olmalı: {level}")
    raw = UPGRADE_BASE_COST[kind] * UPGRADE_COST_GROWTH ** (int(level) - FACILITY_MIN)
    return int(round(raw / 10_000) * 10_000)


def next_stadium_capacity(capacity: int) -> int:
    """Bir genisletme sonrasi kapasite (en fazla STADIUM_MAX)."""
    return int(min(STADIUM_MAX, max(int(capacity), STADIUM_MIN) + STADIUM_STEP))


def stadium_expansion_cost(capacity: int) -> int:
    """
    Mevcut kapasiteden bir genisletmenin (en fazla STADIUM_STEP koltuk) maliyeti, EUR (10.000'e yuvarlanir).
    Buyuk statta koltuk basina maliyet artar. Kapasite zaten STADIUM_MAX ise ValueError.
    """
    capacity = int(capacity)
    if capacity >= STADIUM_MAX:
        raise ValueError(f"Stadyum zaten en büyük kapasitede ({STADIUM_MAX:,} koltuk).")
    added = next_stadium_capacity(capacity) - max(capacity, 0)
    per_seat = STADIUM_SEAT_COST_BASE + STADIUM_SEAT_COST_PER_THOUSAND * max(capacity, 0) / 1000
    return int(round(added * per_seat / 10_000) * 10_000)


# ===========================================================================
# ETKILER
# ===========================================================================

MEDICAL_NEUTRAL_LEVEL = 10
MEDICAL_BONUS_PER_LEVEL = 0.03          # 10 ustu her seviye: 20 -> 1.30
MEDICAL_PENALTY_PER_LEVEL = 0.02        # 10 alti her seviye: 1 -> 0.82


def medical_recovery_multiplier(level: int | None) -> float:
    """
    Saglik merkezinin toparlanma orani carpani. None (kurulmamis kulup) -> 1.0 (eski davranis).
        seviye 1 -> 0.82 · 5 -> 0.90 · 10 -> 1.00 · 15 -> 1.15 · 20 -> 1.30
    """
    if level is None:
        return 1.0
    lv = int(_clamp(int(level), FACILITY_MIN, FACILITY_MAX))
    if lv >= MEDICAL_NEUTRAL_LEVEL:
        return round(1.0 + MEDICAL_BONUS_PER_LEVEL * (lv - MEDICAL_NEUTRAL_LEVEL), 4)
    return round(1.0 - MEDICAL_PENALTY_PER_LEVEL * (MEDICAL_NEUTRAL_LEVEL - lv), 4)


# Genc girisinde kalite endeksi q'nun (youth.quality_index) beklenen potansiyel etkisi:
# guc ortalamasi OVERALL_QUALITY*q + kalan pay HEADROOM_QUALITY*q + cevher ihtimali GEM_CHANCE_QUALITY*q x ort. bonus
_YOUTH_POTENTIAL_PER_QUALITY = (
    youth.OVERALL_QUALITY + youth.HEADROOM_QUALITY + youth.GEM_CHANCE_QUALITY * sum(youth.GEM_BONUS) / 2
)


def youth_potential_shift(level: int | None, reputation: int | None) -> float:
    """
    Genc girisinde beklenen ortalama potansiyel kaymasi (puan), notr kulube (tesis 10.5, itibar 70) gore.
    Tesis bir seviye artinca ~+0.45 puan. Yaklasik (kirpmalar haric) beklenen degerdir.
    """
    return round(_YOUTH_POTENTIAL_PER_QUALITY * youth.quality_index(level, reputation), 1)


def youth_growth_multiplier(level: int | None) -> float:
    """Akademi oyuncularinin haftalik gelisim carpani (development.facilities_factor): 1 -> 0.82, 20 -> 1.20."""
    return round(development.facilities_factor(level), 4)


# ===========================================================================
# MAC GUNU GELIRI
# ===========================================================================

DEMAND_BASE = 10_000
DEMAND_RANGE = 80_000
DEMAND_EXPONENT = 1.6
YIELD_BASE = 6.0                # EUR / seyirci (net)
YIELD_RANGE = 30.0
YIELD_EXPONENT = 1.5


def stadium_demand(reputation: int | None) -> int:
    """Bir ic saha macina gelmek isteyen seyirci (100'e yuvarlanir): itibar 50 -> ~14.5K, 85 -> ~60.5K, 95 -> ~79.6K."""
    raw = DEMAND_BASE + DEMAND_RANGE * _reputation_share(reputation) ** DEMAND_EXPONENT
    return int(round(raw / 100) * 100)


def ticket_yield(reputation: int | None) -> float:
    """Seyirci basina net mac gunu geliri (EUR): itibar 50 -> ~8, 85 -> ~25.5, 95 -> ~32.3."""
    return YIELD_BASE + YIELD_RANGE * _reputation_share(reputation) ** YIELD_EXPONENT


def attendance(capacity: int | None, reputation: int | None) -> int:
    """Seyirci: talep kapasiteyi asarsa stat dolar."""
    if not capacity or capacity <= 0:
        return 0
    return int(min(int(capacity), stadium_demand(reputation)))


def gate_income(capacity: int | None, reputation: int | None, is_home: bool = True) -> int:
    """
    Tek bir ic saha macinin net mac gunu geliri (EUR, 1.000'e yuvarlanir). Deplasman, tarafsiz saha
    (is_home=False) ya da kapasitesi bilinmeyen (kurulmamis) kulup -> 0.
    """
    if not is_home:
        return 0
    fans = attendance(capacity, reputation)
    return int(round(fans * ticket_yield(reputation) / 1000) * 1000)


# ===========================================================================
# VARSAYILAN TESISLER (eski kayit / yeni dunya doldurma)
# ===========================================================================

DEFAULT_CAPACITY_FILL = 0.82        # varsayilan stat talebin ~%82'si: genisletme anlamli
DEFAULT_CAPACITY_JITTER = 0.10
DEFAULT_MEDICAL_PIVOT = 80          # bu itibarda varsayilan saglik merkezi notr (10)
DEFAULT_MEDICAL_PER_REPUTATION = 0.3


@dataclass(frozen=True)
class ClubFacilities:
    """Doldurma icin varsayilan tesisler (altyapi youth.default_facilities'tedir)."""
    stadium_capacity: int
    medical_facilities: int


def default_stadium_capacity(reputation: int | None, rng: random.Random | None = None) -> int:
    """Talebin ~%72-92'si, 1.000'e yuvarlanir, STADIUM_MIN-STADIUM_MAX."""
    jitter = rng.uniform(-DEFAULT_CAPACITY_JITTER, DEFAULT_CAPACITY_JITTER) if rng is not None else 0.0
    raw = stadium_demand(reputation) * (DEFAULT_CAPACITY_FILL + jitter)
    return int(_clamp(round(raw / 1000) * 1000, STADIUM_MIN, STADIUM_MAX))


def default_medical_facilities(reputation: int | None, rng: random.Random | None = None) -> int:
    """Itibar 80 -> 10 (notr), 95 -> ~14, 73 -> ~8, 50 -> 1; rng verilirse -2..+2 sapma."""
    rep = 70.0 if reputation is None else float(reputation)
    base = MEDICAL_NEUTRAL_LEVEL + (rep - DEFAULT_MEDICAL_PIVOT) * DEFAULT_MEDICAL_PER_REPUTATION
    jitter = rng.randint(-2, 2) if rng is not None else 0
    return int(_clamp(round(base + jitter), FACILITY_MIN, FACILITY_MAX))


def default_facilities(reputation: int | None, rng: random.Random | None = None) -> ClubFacilities:
    """Kulubun itibarindan stadyum kapasitesi ve saglik merkezi seviyesi (rng: once stat, sonra saglik)."""
    capacity = default_stadium_capacity(reputation, rng)
    medical = default_medical_facilities(reputation, rng)
    return ClubFacilities(stadium_capacity=capacity, medical_facilities=medical)


# ===========================================================================
# SPONSORLUK
# ===========================================================================

SPONSOR_BASE = 15_000
SPONSOR_RANGE = 700_000
SPONSOR_EXPONENT = 2.2
SPONSOR_MAX_SEASONS = 4
SPONSOR_SECURITY_PREMIUM = 0.04     # AI degerlendirmesi: fazladan her sezon guvencesi %4 deger
DEFAULT_SEASON_WEEKS = 38           # sezon uzunlugu bilinmiyorsa (degerlendirme icin)

# (profil, haftalik carpan araligi, sezon araligi, prim = temel haftalik x sezon haftasi x bu aralik)
OFFER_PROFILES: tuple[tuple[str, tuple[float, float], tuple[int, int], tuple[float, float]], ...] = (
    ("weekly", (1.15, 1.25), (1, 1), (0.0, 0.0)),
    ("long", (1.00, 1.08), (3, 4), (0.15, 0.30)),
    ("bonus", (0.78, 0.88), (2, 2), (0.50, 0.70)),
)
STARTING_WEEKLY_FACTOR = (0.85, 1.0)    # baslangic sozlesmesi piyasanin biraz altinda
STARTING_SEASONS = (1, 3)

# Uydurma marka kokleri (gercek sirket adi DEGIL) + Turkce sektor adlari
BRAND_STEMS: tuple[str, ...] = (
    "Veltrano", "Kavrix", "Solmera", "Brenvik", "Tarsuna", "Quentoro", "Mirovia", "Zelvora",
    "Ondexa", "Lurivo", "Parvela", "Cindora", "Vostrel", "Ambrena", "Korvani", "Istrella",
    "Nerevo", "Galdera", "Pironta", "Sevanta", "Dravena", "Heliorax", "Talvessa", "Rudeva",
    "Orvanta", "Marvolta", "Crestiva", "Lobrenta", "Bexora", "Nuvaro", "Yamora", "Frenzola",
)
BRAND_SECTORS: tuple[str, ...] = (
    "Enerji", "Sigorta", "İletişim", "Havayolları", "Otomotiv", "Gıda", "Lojistik",
    "Yatırım", "Teknoloji", "İçecek", "Yapı", "Turizm",
)
BRAND_MAX_LENGTH = 60


@dataclass(frozen=True)
class SponsorOffer:
    """Sponsor teklifi / sozlesmesi. weekly: haftalik EUR; seasons: bu sezon dahil sure; signing_bonus: imzada kasaya."""
    brand: str
    weekly: int
    seasons: int
    signing_bonus: int

    def total_value(self, season_weeks: int = DEFAULT_SEASON_WEEKS) -> int:
        """Sozlesmenin toplam degeri: haftalik x sezon haftasi x sezon + imza primi."""
        return int(self.weekly * max(1, season_weeks) * self.seasons + self.signing_bonus)

    def to_dict(self) -> dict:
        return {"brand": self.brand, "weekly": int(self.weekly), "seasons": int(self.seasons),
                "signing_bonus": int(self.signing_bonus)}

    @classmethod
    def from_dict(cls, data: Mapping) -> SponsorOffer:
        brand = str(data["brand"]).strip()
        weekly, seasons, bonus = int(data["weekly"]), int(data["seasons"]), int(data.get("signing_bonus", 0))
        if not brand or weekly < 0 or bonus < 0 or not 1 <= seasons <= SPONSOR_MAX_SEASONS:
            raise ValueError(f"Geçersiz sponsor teklifi: {dict(data)!r}")
        return cls(brand=brand[:BRAND_MAX_LENGTH], weekly=weekly, seasons=seasons, signing_bonus=bonus)


def sponsor_base_weekly(reputation: int | None) -> int:
    """Itibara gore piyasa haftalik sponsor bedeli (EUR, 1.000'e yuvarlanir)."""
    raw = SPONSOR_BASE + SPONSOR_RANGE * _reputation_share(reputation) ** SPONSOR_EXPONENT
    return int(round(raw / 1000) * 1000)


def brand_name(rng: random.Random, exclude: Iterable[str] = ()) -> str:
    """Kurgusal marka adi ('Veltrano Enerji'); exclude'dakilerle cakismaz (havuz tukenirse numara eklenir)."""
    taken = set(exclude)
    for _ in range(40):
        name = f"{rng.choice(BRAND_STEMS)} {rng.choice(BRAND_SECTORS)}"
        if name not in taken:
            return name
    base = f"{rng.choice(BRAND_STEMS)} {rng.choice(BRAND_SECTORS)}"
    suffix = 2
    while f"{base} {suffix}" in taken:
        suffix += 1
    return f"{base} {suffix}"


def generate_sponsor_offers(
    rng: random.Random,
    reputation: int | None,
    count: int = 3,
    exclude_brands: Iterable[str] = (),
    season_weeks: int = DEFAULT_SEASON_WEEKS,
) -> list[SponsorOffer]:
    """
    `count` teklif, OFFER_PROFILES sirasiyla dongusel (yuksek haftalik, uzun vade, imza primi, ...).
    Tutarlar itibarla, imza primleri ayrica sezon uzunluguyla (season_weeks) buyur; ayni rng durumu -> ayni
    teklifler. Markalar liste icinde (ve exclude_brands ile) cakismaz.
    """
    base = sponsor_base_weekly(reputation)
    weeks = max(1, int(season_weeks))
    used = set(exclude_brands)
    offers: list[SponsorOffer] = []
    for i in range(max(0, int(count))):
        _profile, weekly_range, seasons_range, bonus_share = OFFER_PROFILES[i % len(OFFER_PROFILES)]
        brand = brand_name(rng, used)
        used.add(brand)
        weekly = int(round(base * rng.uniform(*weekly_range) / 1000) * 1000)
        seasons = rng.randint(*seasons_range)
        bonus = int(round(base * weeks * rng.uniform(*bonus_share) / 10_000) * 10_000) if bonus_share[1] > 0 else 0
        offers.append(SponsorOffer(brand=brand, weekly=weekly, seasons=seasons, signing_bonus=bonus))
    return offers


def starting_sponsor(rng: random.Random, reputation: int | None) -> SponsorOffer:
    """Doldurma icin baslangic sozlesmesi: piyasanin biraz altinda, 1-3 sezon, imza primi yok."""
    base = sponsor_base_weekly(reputation)
    brand = brand_name(rng)
    weekly = int(round(base * rng.uniform(*STARTING_WEEKLY_FACTOR) / 1000) * 1000)
    return SponsorOffer(brand=brand, weekly=weekly, seasons=rng.randint(*STARTING_SEASONS), signing_bonus=0)


def offer_score(offer: SponsorOffer, season_weeks: int = DEFAULT_SEASON_WEEKS) -> float:
    """Sezon basina deger (+ uzun sozlesme guvencesi). AI en yuksek puanli teklifi imzalar."""
    per_season = offer.total_value(season_weeks) / max(1, offer.seasons)
    return per_season * (1.0 + SPONSOR_SECURITY_PREMIUM * (offer.seasons - 1))


def best_offer_index(offers: Sequence[SponsorOffer], season_weeks: int = DEFAULT_SEASON_WEEKS) -> int | None:
    """En iyi degerli teklifin indeksi (esitlikte ilk); bos liste -> None."""
    if not offers:
        return None
    return max(range(len(offers)), key=lambda i: (offer_score(offers[i], season_weeks), -i))


def offers_to_json(offers: Iterable[SponsorOffer]) -> list[dict]:
    """JSONB'ye yazilacak liste."""
    return [offer.to_dict() for offer in offers]


def offers_from_json(data) -> list[SponsorOffer]:
    """JSONB listesinden teklifler; bozuk/eksik girdiler atlanir (liste degilse bos)."""
    if not isinstance(data, list):
        return []
    offers: list[SponsorOffer] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        try:
            offers.append(SponsorOffer.from_dict(item))
        except (KeyError, TypeError, ValueError):
            continue
    return offers


def contract_end_season(season: int, seasons: int) -> int:
    """Bu sezon imzalanan `seasons` sezonluk sozlesmenin son sezonu (dahil)."""
    return int(season) + max(1, int(seasons)) - 1


def sponsor_active(sponsor_name: str | None, until_season: int | None, season: int) -> bool:
    """Sozlesme bu sezon gecerli mi? (ad var ve bitis sezonu henuz gecmedi)"""
    return bool(sponsor_name) and until_season is not None and int(until_season) >= int(season)


def sponsor_seasons_left(until_season: int | None, season: int) -> int:
    """Bu sezon dahil kalan sezon sayisi (bitmis/yok -> 0)."""
    if until_season is None:
        return 0
    return max(0, int(until_season) - int(season) + 1)
