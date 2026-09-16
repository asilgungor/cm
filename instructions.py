"""
instructions.py
===============
Takim talimatlari (9. Asama). SAF MANTIK: veritabani, ORM ya da Streamlit BILMEZ.

Menajer mac icinde (veya baslama dudugunden once) takimina iki eksende emir verir:

    Zihniyet (Mentality)
        PARK_THE_BUS    Cok Defansif: pozisyon uretimi belirgin duser, savunma saglamlasir,
                        oyuncular daha az kosar
        BALANCED        Dengeli: motorun notr davranisi (tum carpanlar 1.0)
        ALL_OUT_ATTACK  Cok Ofansif: pozisyon (sut) sansi artar AMA savunma zaafiyeti dogar
                        (rakibin pozisyon sansi da artar) ve oyuncular daha hizli yorulur

    Sertlik (Tackling)
        CALM            Sakin Kal: kart ve sakatlik riski duser, savunma biraz yumusar
        NORMAL          Motorun notr davranisi
        HARD            Sert Oyna: savunma gucu artar; kart olasiligi ~2 kat, direkt kirmizi
                        payi 1.5 kat, macin sakatlik riski (iki takim icin) katlanir

Etkilerin motordaki yeri (match_engine.MatchEngine):
    strength_factor(kind)   _team_strength: hucum / orta saha / savunma takim gucu
    fatigue_factor          _apply_fatigue: dakikalik enerji kaybi
    card_factor             _discipline: kart olayi olasiligi ve kartin hangi takima cikacagi
    straight_red_factor     _discipline: kartin direkt kirmizi olma payi
    injury_factor           _injury_check: iki takimin ortalamasi macin sakatlik olasiligini olcekler

Varsayilan talimat (BALANCED + NORMAL) tum carpanlarda TAM 1.0'dir; motor eski davranisla
bit-bit aynidir (golden regresyon testleri).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mentality(str, Enum):
    PARK_THE_BUS = "PARK_THE_BUS"
    BALANCED = "BALANCED"
    ALL_OUT_ATTACK = "ALL_OUT_ATTACK"


class Tackling(str, Enum):
    CALM = "CALM"
    NORMAL = "NORMAL"
    HARD = "HARD"


MENTALITY_LABELS: dict[Mentality, str] = {
    Mentality.PARK_THE_BUS: "Çok Defansif (Otobüsü Çek)",
    Mentality.BALANCED: "Dengeli",
    Mentality.ALL_OUT_ATTACK: "Çok Ofansif (Topyekûn Hücum)",
}
TACKLING_LABELS: dict[Tackling, str] = {
    Tackling.CALM: "Sakin Kal",
    Tackling.NORMAL: "Normal",
    Tackling.HARD: "Sert Oyna",
}


@dataclass(frozen=True)
class MentalityEffect:
    attack: float
    midfield: float
    defense: float
    fatigue: float


@dataclass(frozen=True)
class TacklingEffect:
    defense: float
    card: float
    straight_red: float
    injury: float
    fatigue: float


# Kalibrasyon: takim gucleri motorda a^2 / (a^2 + b^2) ile olasiliga doner. Cok Ofansif'te
# hucum x1.20 esit rakibe karsi dakikalik pozisyon sansini ~%18 artirir; savunma x0.82 rakibin
# sansini ~%20 artirir. Cok Defansif'te kendi sans ~%24 duser, rakibinki ~%16 duser.
MENTALITY_EFFECTS: dict[Mentality, MentalityEffect] = {
    Mentality.PARK_THE_BUS: MentalityEffect(attack=0.78, midfield=0.94, defense=1.18, fatigue=0.95),
    Mentality.BALANCED: MentalityEffect(attack=1.0, midfield=1.0, defense=1.0, fatigue=1.0),
    Mentality.ALL_OUT_ATTACK: MentalityEffect(attack=1.20, midfield=1.04, defense=0.82, fatigue=1.10),
}
TACKLING_EFFECTS: dict[Tackling, TacklingEffect] = {
    Tackling.CALM: TacklingEffect(defense=0.95, card=0.55, straight_red=0.8, injury=0.75, fatigue=0.97),
    Tackling.NORMAL: TacklingEffect(defense=1.0, card=1.0, straight_red=1.0, injury=1.0, fatigue=1.0),
    Tackling.HARD: TacklingEffect(defense=1.08, card=2.0, straight_red=1.5, injury=2.0, fatigue=1.05),
}


@dataclass(frozen=True)
class TeamInstructions:
    mentality: Mentality = Mentality.BALANCED
    tackling: Tackling = Tackling.NORMAL

    @property
    def is_default(self) -> bool:
        return self.mentality is Mentality.BALANCED and self.tackling is Tackling.NORMAL

    @property
    def _mentality(self) -> MentalityEffect:
        return MENTALITY_EFFECTS[self.mentality]

    @property
    def _tackling(self) -> TacklingEffect:
        return TACKLING_EFFECTS[self.tackling]

    def strength_factor(self, kind: str) -> float:
        """kind: 'attack' | 'midfield' | 'defense'. Sertlik yalnizca savunmayi etkiler."""
        if kind == "attack":
            return self._mentality.attack
        if kind == "midfield":
            return self._mentality.midfield
        if kind == "defense":
            return self._mentality.defense * self._tackling.defense
        raise ValueError(f"Bilinmeyen güç türü: {kind}")

    @property
    def fatigue_factor(self) -> float:
        return self._mentality.fatigue * self._tackling.fatigue

    @property
    def card_factor(self) -> float:
        return self._tackling.card

    @property
    def straight_red_factor(self) -> float:
        return self._tackling.straight_red

    @property
    def injury_factor(self) -> float:
        return self._tackling.injury

    def describe(self) -> str:
        return f"Zihniyet: {MENTALITY_LABELS[self.mentality]} · Sertlik: {TACKLING_LABELS[self.tackling]}"


def parse_mentality(value: Mentality | str) -> Mentality:
    """Enum, deger ('ALL_OUT_ATTACK') ya da etiket ('Dengeli') kabul eder."""
    if isinstance(value, Mentality):
        return value
    for m, label in MENTALITY_LABELS.items():
        if value in (m.value, label):
            return m
    raise ValueError(f"Bilinmeyen zihniyet: {value}")


def parse_tackling(value: Tackling | str) -> Tackling:
    if isinstance(value, Tackling):
        return value
    for t, label in TACKLING_LABELS.items():
        if value in (t.value, label):
            return t
    raise ValueError(f"Bilinmeyen sertlik: {value}")
