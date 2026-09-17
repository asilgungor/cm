"""
world_rules.py
==============
Dunya kurallari (Faz 12 / 14. Asama, 12A). SAF modul: veritabani ve Streamlit bilmez.

GameState.world_rules (JSONB) WorldRules.to_dict ciktisini saklar; bos {} = WorldRules.legacy():
tek menajer, canli mac acik, insan pazari / kiralik / milli takimlar / hafta suresi kapali (bugunku oyun).

    WorldRules.legacy()          -> eski tek kisilik kariyer (varsayilan alanlar)
    WorldRules.shared_defaults() -> yeni paylasilan dunya icin onerilen kurallar
    WorldRules.from_dict(data)   -> HOSGORULU okuma: bilinmeyen anahtar yok sayilir, bozuk / aralik disi deger
                                    o alanin varsayilanina doner; asla hata firlatmaz
    WorldRules.to_dict()         -> JSON'a yazilabilir sozluk (tum alanlar)
    WorldRules.validate()        -> kural ihlalleri (Turkce metinler; bos liste = gecerli)
    WorldRules.with_changes(...) -> SIKI guncelleme (yonetici formu): bilinmeyen anahtar / gecersiz deger RulesError
    WorldRules.editable_changes  -> yeni kurallara gecis engelleri (Turkce; bos liste = izinli):
                                    OYUN kurallari (GAMEPLAY_FIELDS: canli mac, galibiyet puani, pazar, kiralik,
                                    milli takimlar, Dunya Kupasi sikligi) yalnizca sezon basinda (ilk mactan once)
                                    degisir; tur / yonetim ayarlari (hafta suresi, hazir kontrolu, koltuk, koruma,
                                    teklif suresi, adil oyun) her zaman. Paylasilan dunya kisisel kariyere donmez.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

STRICTNESS_LEVELS = ("LOW", "MEDIUM", "HIGH")

# Alan -> (en az, en cok). from_dict aralik disini varsayilana cevirir, validate raporlar.
INT_BOUNDS: Mapping[str, tuple[int, int]] = {
    "max_seats": (1, 64),
    "deadline_hours": (1, 168),
    "max_missed_deadlines": (1, 20),
    "protection_weeks": (0, 52),
    "win_points": (2, 3),
    "offer_expiry_weeks": (1, 8),
    "world_cup_every_seasons": (1, 4),
}

FIELD_LABELS: Mapping[str, str] = {
    "shared": "Paylaşılan dünya",
    "max_seats": "En fazla menajer",
    "live_matches": "Canlı maç",
    "auto_advance": "Süre dolunca hafta otomatik ilerler",
    "deadline_hours": "Hafta süresi (saat)",
    "ready_check": "Herkes hazır olunca hafta ilerler",
    "max_missed_deadlines": "Kulübü kaybetmeden kaçırılabilecek hafta",
    "protection_weeks": "Bırakılan kulübün AI koruma süresi (hafta)",
    "club_offers_by_level": "Kulüp seçenekleri menajer seviyesine göre",
    "win_points": "Galibiyet puanı",
    "human_market": "Menajerler arası transfer pazarı",
    "loans": "Kiralık oyuncu",
    "offer_expiry_weeks": "Teklif geçerlilik süresi (hafta)",
    "fairness_strictness": "Adil oyun denetimi",
    "internationals": "Milli takımlar",
    "world_cup_every_seasons": "Dünya Kupası sıklığı (sezon)",
}

# Mac sonuclarini / sezon yapisini etkileyen kurallar: sezon basladiktan sonra kilitli
GAMEPLAY_FIELDS: tuple[str, ...] = (
    "live_matches", "win_points", "human_market", "loans", "internationals", "world_cup_every_seasons",
)
# Tur ve yonetim ayarlari: her zaman degisebilir (shared yalnizca acilabilir, bkz. editable_changes)
SETTINGS_FIELDS: tuple[str, ...] = (
    "max_seats", "auto_advance", "deadline_hours", "ready_check", "max_missed_deadlines", "protection_weeks",
    "club_offers_by_level", "offer_expiry_weeks", "fairness_strictness",
)


class RulesError(ValueError):
    """Gecersiz ya da su an degistirilemeyen dunya kurali (mesaj Turkce)."""


@dataclass(frozen=True)
class WorldRules:
    shared: bool = False
    max_seats: int = 1
    live_matches: bool = True
    auto_advance: bool = False
    deadline_hours: int = 24
    ready_check: bool = True
    max_missed_deadlines: int = 3
    protection_weeks: int = 4
    club_offers_by_level: bool = False
    win_points: int = 3
    human_market: bool = False
    loans: bool = False
    offer_expiry_weeks: int = 2
    fairness_strictness: str = "MEDIUM"        # LOW / MEDIUM / HIGH
    internationals: bool = False
    world_cup_every_seasons: int = 1

    @classmethod
    def legacy(cls) -> WorldRules:
        """Eski tek kisilik kariyer: GameState.world_rules bos ({})."""
        return cls()

    @classmethod
    def shared_defaults(cls) -> WorldRules:
        """
        Yeni paylasilan dunya: 8 koltuk; herkes hazir olunca ya da 24 saat dolunca hafta ilerler (hazir kontrolu
        ve otomatik ilerleme acik); ust uste 3 turu kaciran menajer 4. kacirista kulubunu kaybeder, kulup 4 hafta
        AI'ya karsi korunur; kulup secenekleri menajer seviyesine gore; insan pazari ve kiralik acik (teklif 2 hafta
        gecerli, orta adil oyun denetimi); milli takimlar kapali (dunya kurulurken acilabilir).
        live_matches acik kalir: CareerManager.live_allowed yine de yalnizca tek aktif koltukta canli izin verir.
        """
        return cls(
            shared=True,
            max_seats=8,
            live_matches=True,
            auto_advance=True,
            deadline_hours=24,
            ready_check=True,
            max_missed_deadlines=3,
            protection_weeks=4,
            club_offers_by_level=True,
            win_points=3,
            human_market=True,
            loans=True,
            offer_expiry_weeks=2,
            fairness_strictness="MEDIUM",
            internationals=False,
            world_cup_every_seasons=1,
        )

    @classmethod
    def from_dict(cls, data: Any) -> WorldRules:
        """Hosgorulu: sozluk degilse eski kurallar; her alan ayri ayri okunur, bozuk alan varsayilana doner."""
        if not isinstance(data, Mapping):
            return cls.legacy()
        defaults = cls()
        values: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            value = _coerce(f.name, data[f.name], getattr(defaults, f.name))
            if value is not None:
                values[f.name] = value
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> list[str]:
        """Kural ihlalleri (bos liste = gecerli). Tur hatalari da raporlanir (dogrudan kurulan nesneler icin)."""
        problems: list[str] = []
        defaults = WorldRules()
        for f in fields(self):
            value, default = getattr(self, f.name), getattr(defaults, f.name)
            label = FIELD_LABELS.get(f.name, f.name)
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    problems.append(f"{label}: açık/kapalı olmalı.")
            elif isinstance(default, int):
                low, high = INT_BOUNDS[f.name]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    problems.append(f"{label}: {low}-{high} arasında olmalı.")
        if self.fairness_strictness not in STRICTNESS_LEVELS:
            problems.append(f"{FIELD_LABELS['fairness_strictness']}: düşük, orta ya da yüksek olmalı.")
        if problems:
            return problems
        if not self.shared and self.max_seats != 1:
            problems.append("Kişisel kariyerde tek menajer olur; birden çok koltuk için dünyayı paylaşıma aç.")
        if self.shared and self.max_seats < 2:
            problems.append("Paylaşılan dünyada en az 2 menajer koltuğu olmalı.")
        if self.shared and not (self.ready_check or self.auto_advance):
            problems.append("Hafta ilerlemesi için hazır kontrolü ya da süre dolunca otomatik ilerleme açık olmalı.")
        if self.human_market and not self.shared:
            problems.append("Menajerler arası pazar yalnızca paylaşılan dünyada açılır.")
        if self.loans and not self.shared:
            problems.append("Kiralık oyuncu sistemi yalnızca paylaşılan dünyada açılır.")
        return problems

    def with_changes(self, changes: Mapping[str, Any]) -> WorldRules:
        """
        Yonetici formundan gelen degisiklikler (SIKI: from_dict gibi sessizce varsayilana donmez).
        Bilinmeyen kural ya da tipe/araliga uymayan deger -> RulesError. Kural butunlugu (validate) ve sezon kilidi
        (editable_changes) ayrica denetlenir.
        """
        if not isinstance(changes, Mapping):
            raise RulesError("Kural değişiklikleri okunamadı.")
        known = {f.name for f in fields(self)}
        values: dict[str, Any] = {}
        for name, raw in changes.items():
            if name not in known:
                raise RulesError(f"Bilinmeyen dünya kuralı: {name}")
            value = _coerce(name, raw, getattr(WorldRules(), name))
            if value is None:
                label = FIELD_LABELS.get(name, name)
                if name in INT_BOUNDS:
                    low, high = INT_BOUNDS[name]
                    raise RulesError(f"{label}: {low}-{high} arasında olmalı.")
                raise RulesError(f"{label}: geçersiz değer.")
            values[name] = value
        return replace(self, **values)

    def changed_fields(self, new: WorldRules) -> dict[str, tuple[Any, Any]]:
        """Degisen alanlar: ad -> (eski, yeni) (denetim kaydi icin)."""
        return {f.name: (getattr(self, f.name), getattr(new, f.name))
                for f in fields(self) if getattr(self, f.name) != getattr(new, f.name)}

    def editable_changes(self, new: WorldRules, season_started: bool) -> list[str]:
        """
        self -> new gecisinin engelleri (Turkce; bos liste = izinli). season_started: bu sezon mac oynandi ya da
        hafta 1'i gecti (CareerManager.can_change_mode'un tersi). Oyun kurallari (GAMEPLAY_FIELDS) yalnizca sezon
        basinda; tur ve yonetim ayarlari (SETTINGS_FIELDS) her zaman degisir. Paylasilan dunya kisisel kariyere
        cevrilemez (koltuklar ve pazar verisi yetim kalirdi); kisisel -> paylasilan donusumu sezon basinda.
        """
        problems: list[str] = []
        if self.shared and not new.shared:
            problems.append("Paylaşılan dünya kişisel kariyere geri çevrilemez.")
        if not season_started:
            return problems
        if not self.shared and new.shared:
            problems.append(f"{FIELD_LABELS['shared']}: yalnızca sezon başında (ilk maçtan önce) açılabilir.")
        for name in GAMEPLAY_FIELDS:
            if getattr(self, name) != getattr(new, name):
                problems.append(f"{FIELD_LABELS[name]}: oyun kuralı; yalnızca sezon başında "
                                "(ilk maçtan önce) değiştirilebilir.")
        return problems


def _coerce(name: str, raw: Any, default: Any) -> Any:
    """Tek alan: dogru tipe cevrilebilir ve aralikta ise deger, aksi halde None (varsayilan kalir)."""
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, int) and raw in (0, 1):
            return bool(raw)
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false", "1", "0"):
            return raw.strip().lower() in ("true", "1")
        return None
    if isinstance(default, int):
        if isinstance(raw, bool):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if isinstance(raw, float) and raw != value:
            return None
        low, high = INT_BOUNDS[name]
        return value if low <= value <= high else None
    if name == "fairness_strictness":
        value = str(raw).strip().upper() if isinstance(raw, str) else None
        return value if value in STRICTNESS_LEVELS else None
    return None
