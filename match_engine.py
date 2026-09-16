"""
match_engine.py
===============
Istatistiki mac simulasyon motoru (2. Asama).

Katman ayrimi (Logic / View):
    [1] Domain nesneleri : MatchPlayer, MatchTeam, MatchEvent, MatchResult   (saf Python)
    [2] MatchEngine      : 90+ dakikalik simulasyon. Veritabanini BILMEZ.
    [3] DB adaptoru      : Fixture'i yukle -> motoru calistir -> sonucu yaz.
    [4] Sunum (View)     : Terminal spikeri. 2D arayuz sadece bu katmani degistirecek.

Motorun bildigi mekanikler:
    * Efektif guc: overall x form x moral (sonumlenmis, bkz. EngineConfig.condition_influence)
    * Mevkiye gore alt-ozellik agirliklari (FWD: shooting/pace, DEF: defending, GK: goalkeeping)
    * Ev sahibi avantaji (itibar ile olceklenir: buyuk stad = daha sert atmosfer)
    * Yorgunluk: enerji dakika dakika duser, yaslilar daha hizli yorulur, devre arasi toparlanma
    * Dinamik kondisyon (fitness.py): enerji DB'deki kondisyondan baslar; FM dayaniklilik,
      efor (sut/asist/faul) ve bastiran takim yorulmayi hizlandirir; enerji efektif gucu
      ve secim gucunu dusurur; mac sonu yorgunluk notu dusurur; enerji zaman serisi tutulur
    * Taktik degisiklikler: 60'tan sonra yorulan oyuncu degistirilir (1 hak acil durum icin saklanir)
    * Sari/kirmizi kart (ikinci sari = kirmizi), eksik oynama cezasi
    * Sakatlik -> ayni mevkiden yedek; yoksa mevki disi oyuncu (cezali); o da yoksa eksik devam
    * Kaleci sakatlanirsa yedek GK; kaleci kirmizi gorurse saha oyuncusu feda edilip yedek GK girer
    * Yedek GK de yoksa: bir saha oyuncusu eldiven giyer (goalkeeping ~30 -> dogal ceza)
    * Uzatma dakikalari (45+X, 90+X): olay yogunluguna gore
    * Geri dusen takim 70'ten sonra bastirir, onde olan takim kapanir
    * Asist, macin adami (MOTM), oyuncu mac notlari (ileride form guncellemesine girdi olacak)
    * Eleme maci (8. Asama, KnockoutRule): onceki ayaklardan tasinan goller (toplam skor),
      toplamda esitlikte 2x15 dk uzatma (kisa mola toparlanmasi, +1 degisiklik hakki,
      yorgunluk surer) ve seri penaltilar (penalties.py). Tarafsiz sahada (final) ev
      sahibi avantaji yoktur. knockout=None iken motor eski davranisla BIREBIR aynidir
      (ayni tohum -> ayni rastgele cekis sirasi; regresyon testiyle kilitli).

Calistirma:
    python match_engine.py                    # Istanbul Lions - Kadıköy Canaries derbisi, DB'ye yaz
    python match_engine.py --dry-run          # DB'ye yazmadan oynat
    python match_engine.py --fixture-id 7     # belirli bir fikstur
    python match_engine.py --home Inter --away Milan --dry-run   # hazirlik maci
    python match_engine.py --home Inter --away Milan --knockout  # eleme: esitlikte uzatma + penalti
    python match_engine.py --home Inter --away Milan --knockout --carry 1-1 --neutral
    python match_engine.py --seed 42          # tekrar uretilebilir sonuc
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import fitness
from models import LineupStatus, Position
from penalties import (
    PenaltyKick,
    PenaltyTaker,
    ShootoutConfig,
    ShootoutResult,
    ShootoutSide,
    equalize_takers,
    run_shootout,
)
from tactics import FORMATIONS

# Windows konsolunda Turkce karakterler patlamasin diye
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ===========================================================================
# [1] DOMAIN NESNELERI
# ===========================================================================

class EventType(str, Enum):
    KICK_OFF = "KICK_OFF"
    GOAL = "GOAL"
    MISS = "MISS"
    SAVE = "SAVE"
    YELLOW_CARD = "YELLOW_CARD"
    RED_CARD = "RED_CARD"
    INJURY = "INJURY"
    SUBSTITUTION = "SUBSTITUTION"
    HALF_TIME = "HALF_TIME"
    FULL_TIME = "FULL_TIME"
    # --- eleme maclari (8. Asama) ---
    EXTRA_TIME_START = "EXTRA_TIME_START"     # 90+X: normal sure toplamda esit bitti
    EXTRA_TIME_HALF = "EXTRA_TIME_HALF"       # 105+X: uzatmalarin devre arasi
    SHOOTOUT_START = "SHOOTOUT_START"         # seri penaltilar basliyor
    PENALTY_SHOOTOUT = "PENALTY_SHOOTOUT"     # serideki tek atis


# Seri penalti donemine ait olay turleri. Bu olaylar (ve arkalarindan gelen FULL_TIME) oyun
# bittikten sonra, son oyun dakikasina (120 ya da 90) added_time=0 ile yazilir; kronolojik
# sirada tum oyun olaylarindan SONRA gelirler.
SHOOTOUT_EVENTS = frozenset({EventType.SHOOTOUT_START, EventType.PENALTY_SHOOTOUT})


@dataclass
class MatchEvent:
    """Kronolojik olay kaydi. 2D arayuz bu nesneleri dogrudan okuyabilir."""
    minute: int
    added_time: int
    type: EventType
    team: str | None
    player: str | None
    description: str
    team_id: int | None = None
    player_id: int | None = None
    home_score: int = 0
    away_score: int = 0
    # Yapilandirilmis ek bilgi (arayuzler aciklama metnini ayristirmasin diye).
    # RED_CARD: "second_yellow" / "straight_red"; PENALTY_SHOOTOUT: "scored" / "saved" / "missed".
    detail: str | None = None
    # Seri penalti skoru (bu olaydan sonra) ve atis sirasi. Seri yoksa 0 / None.
    home_penalties: int = 0
    away_penalties: int = 0
    kick_number: int | None = None

    @property
    def display_minute(self) -> str:
        return f"{self.minute}+{self.added_time}'" if self.added_time else f"{self.minute}'"


@dataclass
class MatchPlayer:
    """Bir oyuncunun mac icindeki kopyasi. ORM nesnesine bagli degildir."""
    id: int
    name: str
    position: Position
    age: int
    overall: int
    pace: int
    shooting: int
    passing: int
    defending: int
    dribbling: int
    goalkeeping: int
    form: int
    morale: int

    # --- maca girerken fiziksel durum (fitness.py) ---
    condition: int = 100                      # DB kondisyonu; mac basi enerji buradan baslar
    stamina: float | None = None              # FM 'Stamina' 1-20 (yoksa None -> notr)

    # --- mac ici durum ---
    role: Position | None = None          # su an oynadigi mevki (sakatlik sonrasi degisebilir)
    condition_factor: float = 1.0            # form x moral'den turetilir (engine hesaplar; kondisyonla ilgisi yok)
    on_pitch: bool = False
    entered_minute: int | None = None
    left_minute: int | None = None
    energy: float = 100.0
    # Enerji zaman serisi: (motor dakikasi, yuvarlanmis enerji). Ornekler: ilk 11 icin
    # baslama, oyuna giris, sahadayken her 5 normal dakika, oyundan cikis ve mac sonu.
    # Uzatmalar 45/90 dakikasina yazilir; ayni dakikada tek kayit (en son deger) tutulur.
    energy_log: list[tuple[int, int]] = field(default_factory=list)
    yellow_cards: int = 0
    sent_off: bool = False
    injured: bool = False
    substituted: bool = False
    goals: int = 0
    assists: int = 0
    shots: int = 0
    shots_on_target: int = 0
    saves: int = 0
    rating: float = 6.0
    matchday: bool = True                     # False: menajer kadro disi birakti (son care disinda oynamaz)

    @classmethod
    def from_orm(cls, p) -> MatchPlayer:
        condition = fitness.condition_of(p)
        stamina = (getattr(p, "fm_attributes", None) or {}).get("stamina")
        return cls(
            id=p.id, name=p.name, position=p.position, age=p.age,
            overall=p.overall_rating, pace=p.pace, shooting=p.shooting,
            passing=p.passing, defending=p.defending, dribbling=p.dribbling,
            goalkeeping=p.goalkeeping, form=p.form, morale=p.morale,
            condition=condition, energy=float(condition),
            stamina=float(stamina) if stamina is not None else None,
        )

    # --- kullanicinin formulu: overall * (form/100) * (morale/100) ---
    @property
    def raw_condition(self) -> float:
        return (self.form / 100.0) * (self.morale / 100.0)

    @property
    def effective_power(self) -> float:
        """O maclik efektif guc (sonumlenmis form/moral ve anlik enerji ile). Enerji 100'de eski deger."""
        return self.overall * self.condition_factor * self.fatigue_factor

    @property
    def selection_power(self) -> float:
        """Kadro secimi icin: overall x form x moral x yorgunluk, notr noktada (50/70, enerji 100) = overall."""
        return self.overall * self.raw_condition / 0.35 * self.fatigue_factor

    @property
    def fatigue_factor(self) -> float:
        """Enerji 100 -> 1.00, enerji 0 -> 0.75 (fitness.fatigue_factor)."""
        return fitness.fatigue_factor(self.energy)

    def log_energy(self, minute: int) -> None:
        """Enerji zaman serisine ornek ekler; ayni dakikaya ikinci ornek oncekinin yerine gecer."""
        sample = (minute, round(self.energy))
        if self.energy_log and self.energy_log[-1][0] == minute:
            self.energy_log[-1] = sample
        else:
            self.energy_log.append(sample)

    @property
    def played(self) -> bool:
        return self.entered_minute is not None

    @property
    def second_yellow(self) -> bool:
        """Ikinci saridan mi atildi? (ceza: 1 mac; direkt kirmizi: 1-3 mac)"""
        return self.sent_off and self.yellow_cards >= 2

    @property
    def minutes_played(self) -> int:
        """Motor mac sonunda sahadakilerin left_minute'ini gercek bitis dakikasina (90/120) yazar."""
        if not self.played:
            return 0
        left = self.left_minute if self.left_minute is not None else 90
        return max(0, left - (self.entered_minute or 0))

    @property
    def available_on_bench(self) -> bool:
        return not (self.on_pitch or self.sent_off or self.injured or self.substituted
                    or self.played or not self.matchday)

    # --- mevkiye gore alt-ozellik agirliklari ---
    @property
    def attack_rating(self) -> float:
        return 0.50 * self.shooting + 0.30 * self.pace + 0.20 * self.dribbling

    @property
    def midfield_rating(self) -> float:
        return 0.45 * self.passing + 0.30 * self.dribbling + 0.25 * self.pace

    @property
    def defense_rating(self) -> float:
        return 0.70 * self.defending + 0.30 * self.pace

    @property
    def gk_rating(self) -> float:
        return float(self.goalkeeping)

    @property
    def aggression(self) -> float:
        """Kart yeme egilimi: mevki + defans gucu + dusuk moral (sinir)."""
        base = {Position.DEF: 1.4, Position.MID: 1.0, Position.FWD: 0.7, Position.GK: 0.15}
        return base[self.position] * (0.7 + 0.3 * self.defending / 100) * (1.25 - 0.5 * self.morale / 100)

    @property
    def stamina_multiplier(self) -> float:
        """Yorulma hizi carpani. 22-29 yas ideal; 30+ her yil %4 daha hizli yorulur."""
        if self.age >= 30:
            return 1.0 + 0.04 * (self.age - 29)
        if self.age <= 20:
            return 1.05
        return 1.0

    @property
    def decay_multiplier(self) -> float:
        """Toplam yorulma carpani: yas x FM dayaniklilik (veri yoksa sadece yas)."""
        return self.stamina_multiplier * fitness.stamina_decay_multiplier(self.stamina)


@dataclass
class TeamStats:
    goals: int = 0
    shots: int = 0
    shots_on_target: int = 0
    saves: int = 0
    possession_minutes: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    injuries: int = 0
    substitutions: int = 0


@dataclass
class MatchTeam:
    id: int
    name: str
    reputation: int
    players: list[MatchPlayer]
    is_home: bool = False
    formation: tuple[int, int, int] | None = None     # None -> motor varsayilani (EngineConfig)
    stats: TeamStats = field(default_factory=TeamStats)
    subs_used: int = 0
    # Sakat/cezali oldugu icin kadroya HIC alinamayanlar: (oyuncu, sebep). Raporlama icin.
    unavailable: list[tuple[MatchPlayer, str]] = field(default_factory=list)
    # Menajerin ilk 11 tercihi: oyuncu id -> oynayacagi rol. Bos ise tam otomatik secim.
    preferred_xi: dict[int, Position] = field(default_factory=dict)
    # Asistanin kadro kurarken yaptigi mudahaleler (sakat yerine giren, mevki disi...)
    lineup_notes: list[str] = field(default_factory=list)

    @property
    def on_pitch(self) -> list[MatchPlayer]:
        return [p for p in self.players if p.on_pitch]

    @property
    def outfield_on_pitch(self) -> list[MatchPlayer]:
        return [p for p in self.on_pitch if p.role is not Position.GK]

    @property
    def bench(self) -> list[MatchPlayer]:
        return [p for p in self.players if p.available_on_bench]

    @property
    def keeper(self) -> MatchPlayer | None:
        for p in self.on_pitch:
            if p.role is Position.GK:
                return p
        return None

    @property
    def player_count(self) -> int:
        return len(self.on_pitch)

    def field_player(self, p: MatchPlayer, role: Position, minute: int) -> None:
        p.on_pitch = True
        p.role = role
        p.entered_minute = minute
        p.log_energy(minute)

    def remove_player(self, p: MatchPlayer, minute: int) -> None:
        p.log_energy(minute)
        p.on_pitch = False
        p.left_minute = minute

    def select_lineup(self) -> None:
        """
        Ilk 11'i kurar.

        Menajerin tercihi (preferred_xi: oyuncu id -> rol) varsa once o uygulanir;
        eksik veya dizilisle uyumsuz slotlari asistan secim gucune gore tamamlar
        (once ayni mevki, sonra en iyi saha oyuncusu -- mevki disi cezali) ve her
        mudahaleyi lineup_notes'a yazar. Tercih yoksa tamamen otomatik secim.
        Kadro disi (matchday=False) oyuncular yalnizca son care olarak kullanilir.
        """
        gk, d, m, f = 1, *self.formation
        needs = [(Position.GK, gk), (Position.DEF, d), (Position.MID, m), (Position.FWD, f)]
        by_id = {p.id: p for p in self.players}
        ranked = sorted(self.players, key=lambda p: -p.selection_power)
        manual = bool(self.preferred_xi)

        for pid in self.preferred_xi:
            if pid not in by_id:
                self.lineup_notes.append(
                    f"Tercih edilen oyuncu (#{pid}) kadroda yok (sakat/cezalı); yeri asistanca dolduruldu."
                )
        for pos, n in needs:
            wanted = [by_id[pid] for pid, role in self.preferred_xi.items() if role is pos and pid in by_id]
            for p in wanted[:n]:
                self.field_player(p, pos, 0)
            for p in wanted[n:]:
                self.lineup_notes.append(f"{p.name}: dizilişte {pos.value} yeri kalmadı, kulübeye alındı.")

        for pos, n in needs:
            missing = n - sum(1 for p in self.on_pitch if p.role is pos)
            if missing <= 0:
                continue
            same = [p for p in ranked if p.position is pos and not p.on_pitch and p.matchday][:missing]
            for p in same:
                self.field_player(p, pos, 0)
                if manual:
                    self.lineup_notes.append(f"Asistan: {p.name} {pos.value} olarak ilk 11'e alındı.")
            missing -= len(same)
            if missing > 0:
                pool = [p for p in ranked if not p.on_pitch and p.matchday
                        and (p.position is not Position.GK or pos is Position.GK)][:missing]
                for p in pool:
                    self.field_player(p, pos, 0)
                    self.lineup_notes.append(
                        f"Asistan: {p.name} mevki dışı ({p.position.value} → {pos.value}) oynayacak."
                    )
                missing -= len(pool)
            if missing > 0:
                # Son care: menajerin kadro disi biraktiklari; gerekirse yedek kaleci
                # saha oyuncusu olarak sahaya surulur (10 kisi oynamaktan iyidir).
                pool = [p for p in ranked if not p.on_pitch][:missing]
                for p in pool:
                    self.field_player(p, pos, 0)
                    self.lineup_notes.append(f"Asistan: kadro yetmedi, {p.name} kadro dışından çağrıldı.")


@dataclass(frozen=True)
class KnockoutRule:
    """
    Eleme maci kurali. carry: onceki ayaklardan gelen goller, BU macin ev sahibi/deplasmanina
    gore (ornek: ilk mac A 2-0 B; rovanste ev sahibi B ise home_carry=0, away_carry=2).
    Toplam skor esitse extra_time -> 2x15 dk uzatma; hala esitse penalties -> seri penalti.
    """
    home_carry: int = 0
    away_carry: int = 0
    extra_time: bool = True
    penalties: bool = True


@dataclass
class MatchResult:
    home: MatchTeam
    away: MatchTeam
    home_score: int
    away_score: int
    events: list[MatchEvent]
    seed: int | None
    first_half_added: int
    second_half_added: int
    man_of_the_match: MatchPlayer | None
    # --- eleme maclari (8. Asama); varsayilanlar lig maci davranisidir ---
    extra_time: bool = False
    extra_time_first_added: int = 0
    extra_time_second_added: int = 0
    shootout: ShootoutResult | None = None
    knockout: KnockoutRule | None = None
    neutral_venue: bool = False

    @property
    def is_draw(self) -> bool:
        """Bu macin skoru (uzatmalar dahil, penaltilar haric) esit mi?"""
        return self.home_score == self.away_score

    @property
    def winner(self) -> MatchTeam | None:
        """Bu macin kazanani (penaltilar haric). Tur atlayan icin: advancing."""
        if self.is_draw:
            return None
        return self.home if self.home_score > self.away_score else self.away

    @property
    def total_minutes(self) -> int:
        total = 90 + self.first_half_added + self.second_half_added
        if self.extra_time:
            total += 30 + self.extra_time_first_added + self.extra_time_second_added
        return total

    @property
    def end_minute(self) -> int:
        """Oyunun bittigi nominal dakika: uzatma oynandiysa 120, yoksa 90."""
        return 120 if self.extra_time else 90

    @property
    def home_penalties(self) -> int | None:
        return self.shootout.home_score if self.shootout is not None else None

    @property
    def away_penalties(self) -> int | None:
        return self.shootout.away_score if self.shootout is not None else None

    @property
    def home_aggregate(self) -> int:
        return self.home_score + (self.knockout.home_carry if self.knockout else 0)

    @property
    def away_aggregate(self) -> int:
        return self.away_score + (self.knockout.away_carry if self.knockout else 0)

    @property
    def decided_by(self) -> str:
        """'normal' | 'extra_time' | 'penalties'."""
        if self.shootout is not None:
            return "penalties"
        if self.extra_time:
            return "extra_time"
        return "normal"

    @property
    def advancing(self) -> MatchTeam | None:
        """
        Eleme macinda tur atlayan takim: toplam skor (carry + goller), esitse penaltilar.
        Lig macinda (knockout None) ya da toplam esit ve penalti kurali kapaliysa None.
        """
        if self.knockout is None:
            return None
        if self.home_aggregate != self.away_aggregate:
            return self.home if self.home_aggregate > self.away_aggregate else self.away
        if self.shootout is not None:
            return self.home if self.shootout.winner_side == "home" else self.away
        return None

    def scorers(self, team: MatchTeam) -> list[tuple[str, int]]:
        return [(p.name, p.goals) for p in team.players if p.goals > 0]


# ===========================================================================
# [2] MOTOR
# ===========================================================================

@dataclass
class EngineConfig:
    """Tum ayarlanabilir sabitler tek yerde. Kalibrasyon burada yapilir."""
    formation: tuple[int, int, int] = (4, 4, 2)
    max_subs: int = 5
    subs_reserved_for_emergency: int = 1     # taktik degisiklik bu kadar hakki saklar

    # Form/moral etkisi. Kullanici formulu overall*form*moral'dir; ham haliyle
    # form 45/moral 60 ile form 65/moral 85 arasinda 2x fark cikar ve yetenek
    # anlamsizlasir. Bu yuzden notr noktaya (form 50, moral 70) gore sonumlenir.
    condition_influence: float = 0.25
    condition_clamp: tuple[float, float] = (0.88, 1.12)
    neutral_form: int = 50
    neutral_morale: int = 70

    home_advantage_base: float = 0.05
    home_advantage_per_reputation: float = 0.0006

    # Guc farkini olasiliga ceviren keskinlik: a^k / (a^k + b^k).
    # Takim seviyesi (topa sahip olma, pozisyon uretme) ve oyuncu seviyesi
    # (sut-defans, sut-kaleci) ayri tutulur; aksi halde ayni ustunluk dort
    # asamada ust uste carpilip %10'luk fark 3-5x gol farkina donusuyor.
    sharpness_team: float = 2.0
    sharpness_player: float = 1.5
    base_chance: float = 0.24       # dakikada pozisyon uretme (esit guc)
    base_on_target: float = 0.42    # pozisyon -> isabetli sut
    base_goal: float = 0.29         # isabetli sut -> gol
    assist_share: float = 0.72      # gollerin asistli olma orani

    base_card: float = 0.042        # dakikada kart olayi (iki takim toplam)
    straight_red_share: float = 0.035
    defending_team_card_share: float = 0.70
    cautious_after_yellow: float = 0.45     # sari gormus oyuncunun kart agirligi carpani

    base_injury: float = 0.0045     # dakikada sakatlik (iki takim toplam)

    short_handed_penalty: float = 0.95      # eksik oyuncu basina EK carpan (toplam zaten duser)
    out_of_position_penalty: float = 0.85

    fatigue_base_decay: float = 0.60        # dakikada enerji kaybi
    half_time_recovery: float = 6.0
    tired_threshold: float = 60.0
    tactical_sub_from_minute: int = 60

    # Efor: sut atan, asist yapan ve kart goren (faul yapan) oyuncu ek enerji harcar
    shot_energy_cost: float = 1.0
    assist_energy_cost: float = 0.6
    foul_energy_cost: float = 0.8
    trailing_fatigue_multiplier: float = 1.15   # bastiran (geride, umutlu) takim daha hizli yorulur
    energy_log_interval: int = 5                # enerji zaman serisi ornekleme araligi (normal dakika)

    desperation_from_minute: int = 70
    desperation_max_deficit: int = 2        # 3+ gol geride: mac bitmis, kimse riske girmez (blowout frenler)
    trailing_attack_boost: float = 1.12
    trailing_defense_drop: float = 0.92
    leading_attack_drop: float = 0.95
    leading_defense_boost: float = 1.05

    # --- eleme maclari: uzatma (2x15) ve seri penaltilar ---
    extra_time_break_recovery: float = 3.0      # 90' sonrasi kisa mola: sahadakilere enerji
    extra_time_first_added_cap: int = 3         # 105+X
    extra_time_second_added_cap: int = 4        # 120+X
    extra_time_extra_subs: int = 1              # uzatmada acilan ek degisiklik hakki
    # Penalti atisci yetenegi: sut + moral (sogukkanlilik vekili), form ve enerjiyle olceklenir
    penalty_shooting_weight: float = 0.8
    penalty_morale_weight: float = 0.2
    penalty_form_influence: float = 0.10        # form 100 -> +%10, form 0 -> -%10 (notr 50)
    penalty_fatigue_influence: float = 0.5      # yorgunluk carpaninin yarisi yansir
    shootout: ShootoutConfig = field(default_factory=ShootoutConfig)


ROLE_WEIGHTS: dict[str, dict[Position, float]] = {
    "attack":   {Position.FWD: 1.00, Position.MID: 0.55, Position.DEF: 0.12, Position.GK: 0.00},
    "midfield": {Position.MID: 1.00, Position.FWD: 0.45, Position.DEF: 0.45, Position.GK: 0.05},
    "defense":  {Position.DEF: 1.00, Position.MID: 0.50, Position.FWD: 0.12, Position.GK: 0.00},
}

ROLE_FATIGUE: dict[Position, float] = {
    Position.MID: 1.10, Position.FWD: 1.00, Position.DEF: 0.90, Position.GK: 0.30,
}

# Dizilis tarzi carpanlari (tam kadro normalizasyonundan SONRA uygulanir).
# 4-3-3 hucumu acar ama savunmayi inceltir; 3-5-2 orta sahayi doldurur,
# kanatlar savunmada acik kalir. 4-4-2 dengeli referans.
FORMATION_STYLE: dict[tuple[int, int, int], dict[str, float]] = {
    (4, 4, 2): {"attack": 1.00, "midfield": 1.00, "defense": 1.00},
    (4, 3, 3): {"attack": 1.08, "midfield": 0.96, "defense": 0.94},
    (3, 5, 2): {"attack": 1.03, "midfield": 1.06, "defense": 0.93},
}


class MatchEngine:
    def __init__(
        self,
        home: MatchTeam,
        away: MatchTeam,
        seed: int | None = None,
        config: EngineConfig | None = None,
        knockout: KnockoutRule | None = None,
        neutral_venue: bool = False,
    ) -> None:
        self.cfg = config or EngineConfig()
        self.rng = random.Random(seed)
        self.seed = seed
        self.home = home
        self.away = away
        self.knockout = knockout
        self.neutral_venue = neutral_venue
        self.home.is_home, self.away.is_home = True, False
        for team in (self.home, self.away):
            if team.formation is None:
                team.formation = self.cfg.formation
        self.events: list[MatchEvent] = []
        self.minute = 0
        self.added = 0
        self._half_events = 0
        self.first_half_added = 0
        self.second_half_added = 0
        self.in_extra_time = False
        self.extra_time_played = False
        self.extra_time_first_added = 0
        self.extra_time_second_added = 0
        self.shootout: ShootoutResult | None = None
        for team in (home, away):
            self._prepare_team(team)

    # ------------------------------------------------------------------ hazirlik

    def _prepare_team(self, team: MatchTeam) -> None:
        neutral = (self.cfg.neutral_form / 100) * (self.cfg.neutral_morale / 100)
        lo, hi = self.cfg.condition_clamp
        for p in team.players:
            ratio = p.raw_condition / neutral
            p.condition_factor = max(lo, min(hi, 1 + self.cfg.condition_influence * (ratio - 1)))
            # Mac basi enerji = kondisyon; kadro secimi (select_lineup) bunu zaten gorur
            p.energy = float(p.condition)
            p.energy_log.clear()
        team.select_lineup()

    @property
    def home_advantage(self) -> float:
        if self.neutral_venue:
            return 1.0
        return 1 + self.cfg.home_advantage_base + self.cfg.home_advantage_per_reputation * self.home.reputation

    @property
    def max_subs(self) -> int:
        """Degisiklik hakki: uzatmalarda extra_time_extra_subs kadar artar."""
        return self.cfg.max_subs + (self.cfg.extra_time_extra_subs if self.in_extra_time else 0)

    @property
    def end_minute(self) -> int:
        return 120 if self.extra_time_played else 90

    # ------------------------------------------------------------------ yardimcilar

    def _log(self, type_: EventType, team: MatchTeam | None, player: MatchPlayer | None, desc: str,
             detail: str | None = None) -> MatchEvent:
        ev = MatchEvent(
            minute=self.minute, added_time=self.added, type=type_,
            team=team.name if team else None, player=player.name if player else None,
            description=desc, team_id=team.id if team else None,
            player_id=player.id if player else None,
            home_score=self.home.stats.goals, away_score=self.away.stats.goals,
            detail=detail,
        )
        self.events.append(ev)
        return ev

    def _opponent(self, team: MatchTeam) -> MatchTeam:
        return self.away if team is self.home else self.home

    def _carry(self, team: MatchTeam) -> int:
        """Onceki ayaklardan tasinan goller (lig macinda 0)."""
        if self.knockout is None:
            return 0
        return self.knockout.home_carry if team is self.home else self.knockout.away_carry

    def _deficit(self, team: MatchTeam) -> int:
        """
        Takimin gol farki acigi (pozitif = geride). Eleme macinda TOPLAM skora gore;
        lig macinda eski hesapla birebir ayni (carry eklenmez).
        """
        deficit = self._opponent(team).stats.goals - team.stats.goals
        if self.knockout is not None:
            deficit += self._carry(self._opponent(team)) - self._carry(team)
        return deficit

    def _aggregate_level(self) -> bool:
        return self._deficit(self.home) == 0

    def _score_text(self) -> str:
        return f"{self.home.name} {self.home.stats.goals} - {self.away.stats.goals} {self.away.name}"

    def _aggregate_text(self) -> str:
        """Eleme macinda carry varsa ' (toplam 2-2)'; yoksa bos."""
        if self.knockout is None or (self.knockout.home_carry == 0 and self.knockout.away_carry == 0):
            return ""
        return (f" (toplam {self.home.stats.goals + self.knockout.home_carry}-"
                f"{self.away.stats.goals + self.knockout.away_carry})")

    def _contest(self, a: float, b: float, k: float) -> float:
        """a'nin b'yi yenme olasiligi. Esitlikte 0.5."""
        if a <= 0 and b <= 0:
            return 0.5
        a_k, b_k = max(a, 0.0) ** k, max(b, 0.0) ** k
        return a_k / (a_k + b_k)

    def _team_contest(self, a: float, b: float) -> float:
        return self._contest(a, b, self.cfg.sharpness_team)

    def _player_contest(self, a: float, b: float) -> float:
        return self._contest(a, b, self.cfg.sharpness_player)

    @staticmethod
    def _scaled(base: float, p_win: float, lo: float = 0.01, hi: float = 0.75) -> float:
        """Esit gucte 'base' olan olasiligi, ustunluge gore olcekler."""
        return max(lo, min(hi, base * 2 * p_win))

    def _weighted_choice(self, players: Sequence[MatchPlayer], weight: Callable[[MatchPlayer], float]) -> MatchPlayer | None:
        if not players:
            return None
        weights = [max(weight(p), 0.001) for p in players]
        return self.rng.choices(players, weights=weights, k=1)[0]

    def _player_strength(self, p: MatchPlayer, kind: str) -> float:
        rating = {"attack": p.attack_rating, "midfield": p.midfield_rating, "defense": p.defense_rating}[kind]
        base = 0.4 * p.overall + 0.6 * rating
        role = p.role or p.position
        penalty = 1.0 if role is p.position else self.cfg.out_of_position_penalty
        return base * p.condition_factor * p.fatigue_factor * ROLE_WEIGHTS[kind][role] * penalty

    def _is_pressing(self, team: MatchTeam) -> bool:
        """Geride ama umutlu (fark desperation_max_deficit icinde) ve son bolumde: bastiriyor."""
        if self.minute < self.cfg.desperation_from_minute:
            return False
        deficit = self._deficit(team)
        return 0 < deficit <= self.cfg.desperation_max_deficit

    def _situation_factor(self, team: MatchTeam, kind: str) -> float:
        if self.minute < self.cfg.desperation_from_minute or kind == "midfield":
            return 1.0
        diff = -self._deficit(team)
        if diff < 0:
            if -diff > self.cfg.desperation_max_deficit:
                return 1.0
            return self.cfg.trailing_attack_boost if kind == "attack" else self.cfg.trailing_defense_drop
        if diff > 0:
            return self.cfg.leading_attack_drop if kind == "attack" else self.cfg.leading_defense_boost
        return 1.0

    def _formation_norm(self, team: MatchTeam, kind: str) -> float:
        """
        Tam kadro (11) icin rol agirliklari toplami. Takim gucunu 'oyuncu basina'
        olcege indirger; boylece esit iki takimin hucum ve savunma degerleri esit
        cikar (4-4-2'de ham toplamlar 4.68'e 6.24 idi -> savunma hep kazaniyordu).
        """
        w = ROLE_WEIGHTS[kind]
        d, m, f = team.formation
        return w[Position.GK] + d * w[Position.DEF] + m * w[Position.MID] + f * w[Position.FWD]

    def _team_strength(self, team: MatchTeam, kind: str) -> float:
        total = sum(self._player_strength(p, kind) for p in team.on_pitch)
        total /= max(self._formation_norm(team, kind), 0.01)
        total *= FORMATION_STYLE.get(team.formation, {}).get(kind, 1.0)
        missing = max(0, 11 - team.player_count)
        total *= self.cfg.short_handed_penalty ** missing
        if kind == "midfield" and team.is_home:
            total *= self.home_advantage
        return total * self._situation_factor(team, kind)

    def _keeper_strength(self, team: MatchTeam) -> float:
        gk = team.keeper
        if gk is None:
            return 5.0  # bos kale
        penalty = 1.0 if gk.position is Position.GK else self.cfg.out_of_position_penalty
        return gk.gk_rating * gk.condition_factor * gk.fatigue_factor * penalty

    # ------------------------------------------------------------------ ana akis

    def simulate(self) -> MatchResult:
        self.minute, self.added = 0, 0
        self._log(EventType.KICK_OFF, None, None,
                  f"Hakem düdüğü çaldı! {self.home.name} - {self.away.name} başlıyor.")

        for minute in range(1, 46):
            self._play_minute(minute, 0)
        self.first_half_added = self._compute_added_time(half=1)
        for extra in range(1, self.first_half_added + 1):
            self._play_minute(45, extra)

        self.minute, self.added = 45, self.first_half_added
        self._log(EventType.HALF_TIME, None, None,
                  f"İlk yarı sona erdi. {self.home.name} {self.home.stats.goals} - "
                  f"{self.away.stats.goals} {self.away.name}")
        self._half_time()

        for minute in range(46, 91):
            self._play_minute(minute, 0)
        self.second_half_added = self._compute_added_time(half=2)
        for extra in range(1, self.second_half_added + 1):
            self._play_minute(90, extra)

        self.minute, self.added = 90, self.second_half_added
        if self.knockout is None:
            self._log(EventType.FULL_TIME, None, None,
                      f"Maç bitti! {self.home.name} {self.home.stats.goals} - "
                      f"{self.away.stats.goals} {self.away.name}")
        else:
            self._knockout_finish()

        self._close_minutes()
        self._compute_ratings()
        motm = self._man_of_the_match()
        return MatchResult(
            home=self.home, away=self.away,
            home_score=self.home.stats.goals, away_score=self.away.stats.goals,
            events=self.events, seed=self.seed,
            first_half_added=self.first_half_added, second_half_added=self.second_half_added,
            man_of_the_match=motm,
            extra_time=self.extra_time_played,
            extra_time_first_added=self.extra_time_first_added,
            extra_time_second_added=self.extra_time_second_added,
            shootout=self.shootout, knockout=self.knockout, neutral_venue=self.neutral_venue,
        )

    # ------------------------------------------------------------------ eleme: uzatma + penalti

    def _knockout_finish(self) -> None:
        """90+X sonrasi: toplamda esitse uzatma, hala esitse seri penalti; sonra FULL_TIME."""
        rule = self.knockout
        assert rule is not None
        if self._aggregate_level() and rule.extra_time:
            self._play_extra_time()
        if self._aggregate_level() and rule.penalties:
            self._play_shootout()
        self._log_knockout_full_time()

    def _play_extra_time(self) -> None:
        self._log(EventType.EXTRA_TIME_START, None, None,
                  f"Normal süre {self.home.stats.goals}-{self.away.stats.goals} bitti"
                  f"{self._aggregate_text()}, uzatmalara gidiliyor!")
        self.in_extra_time = True
        self.extra_time_played = True
        for team in (self.home, self.away):
            for p in team.on_pitch:
                p.energy = min(100.0, p.energy + self.cfg.extra_time_break_recovery)
        self._half_events = 0

        for minute in range(91, 106):
            self._play_minute(minute, 0)
        self.extra_time_first_added = self._compute_added_time(half=3)
        for extra in range(1, self.extra_time_first_added + 1):
            self._play_minute(105, extra)

        self.minute, self.added = 105, self.extra_time_first_added
        self._log(EventType.EXTRA_TIME_HALF, None, None,
                  f"Uzatmaların ilk yarısı sona erdi. {self._score_text()}{self._aggregate_text()}")
        self._half_events = 0

        for minute in range(106, 121):
            self._play_minute(minute, 0)
        self.extra_time_second_added = self._compute_added_time(half=4)
        for extra in range(1, self.extra_time_second_added + 1):
            self._play_minute(120, extra)
        self.minute, self.added = 120, self.extra_time_second_added

    def _penalty_taker_skill(self, p: MatchPlayer) -> float:
        """Atisci yetenegi (0-100): sut + moral, form ve anlik enerjiyle olceklenir."""
        cfg = self.cfg
        base = cfg.penalty_shooting_weight * p.shooting + cfg.penalty_morale_weight * p.morale
        form = 1 + cfg.penalty_form_influence * (p.form - cfg.neutral_form) / 50
        energy = 1 - cfg.penalty_fatigue_influence * (1 - p.fatigue_factor)
        return base * form * energy

    def _penalty_keeper_skill(self, keeper: MatchPlayer | None) -> float:
        """Kaleci yetenegi: goalkeeping x enerji (acil durum kalecisine mevki disi cezasi)."""
        if keeper is None:
            return 5.0
        energy = 1 - self.cfg.penalty_fatigue_influence * (1 - keeper.fatigue_factor)
        penalty = 1.0 if keeper.position is Position.GK else self.cfg.out_of_position_penalty
        return keeper.goalkeeping * energy * penalty

    def _shootout_side(self, team: MatchTeam) -> ShootoutSide:
        """Seriye yalnizca SU AN sahada olanlar girer; kalede rolu GK olan (acil durum dahil) durur."""
        keeper = team.keeper
        return ShootoutSide(
            team_id=team.id, team_name=team.name,
            takers=[PenaltyTaker(p.id, p.name, self._penalty_taker_skill(p)) for p in team.on_pitch],
            keeper_id=keeper.id if keeper else None,
            keeper_name=keeper.name if keeper else "kaleci",
            keeper_skill=self._penalty_keeper_skill(keeper),
        )

    def _play_shootout(self) -> None:
        end = self.end_minute
        self.minute, self.added = end, 0
        first = "home" if self.rng.random() < 0.5 else "away"
        home_side, away_side = self._shootout_side(self.home), self._shootout_side(self.away)
        home_takers, away_takers = equalize_takers(
            home_side.takers, away_side.takers, home_side.keeper_id, away_side.keeper_id)
        dropped = [t.name for t in home_side.takers if t not in home_takers]
        dropped += [t.name for t in away_side.takers if t not in away_takers]
        home_side.takers, away_side.takers = home_takers, away_takers
        self.shootout = run_shootout(self.rng, home_side, away_side, first=first, config=self.cfg.shootout)

        first_team = self.home if first == "home" else self.away
        period = "Uzatmalarda da" if self.extra_time_played else "Normal sürede"
        text = (f"{period} eşitlik bozulmadı: {self._score_text()}{self._aggregate_text()}. "
                f"Seri penaltı atışları başlıyor! Yazı-turayı kazanan {first_team.name} ilk atışı yapacak.")
        if dropped:
            text += f" Sayıları eşitlemek için seriye katılmayanlar: {', '.join(dropped)}."
        self._log(EventType.SHOOTOUT_START, None, None, text)
        for kick in self.shootout.kicks:
            self._log_kick(kick)

    def _log_kick(self, kick: PenaltyKick) -> None:
        team = self.home if kick.side == "home" else self.away
        tally = f"Seri: {self.home.name} {kick.home_score} - {kick.away_score} {self.away.name}"
        who = f"{kick.player_name} ({team.name})"
        prefix = "Ani ölüm! " if kick.sudden_death else ""
        if kick.outcome == "scored":
            body = self.rng.choice([
                f"{who} kaleciyi ters köşeye yatırıyor, GOL!",
                f"{who} sert ve köşeye vuruyor, top ağlarda!",
                f"{who} soğukkanlı bir vuruşla penaltıyı gole çeviriyor!",
            ])
        elif kick.outcome == "saved":
            body = self.rng.choice([
                f"{who} vuruyor... {kick.keeper_name} doğru köşeye uzanıp KURTARIYOR!",
                f"{who} yerden köşeye vurdu ama {kick.keeper_name} çeliyor!",
            ])
        else:
            body = self.rng.choice([
                f"{who} topu direğin dışına gönderiyor, KAÇIRDI!",
                f"{who} üstten auta vuruyor, KAÇIRDI!",
                f"{who} direğe nişanlıyor, top dışarı çıkıyor!",
            ])
        ev = MatchEvent(
            minute=self.minute, added_time=0, type=EventType.PENALTY_SHOOTOUT,
            team=team.name, player=kick.player_name, description=f"{prefix}{body} {tally}",
            team_id=team.id, player_id=kick.player_id,
            home_score=self.home.stats.goals, away_score=self.away.stats.goals,
            detail=kick.outcome, home_penalties=kick.home_score, away_penalties=kick.away_score,
            kick_number=kick.number,
        )
        self.events.append(ev)

    def _log_knockout_full_time(self) -> None:
        prefix = "Uzatmalar sonunda maç bitti!" if self.extra_time_played else "Maç bitti!"
        text = f"{prefix} {self._score_text()}"
        if self.shootout is not None:
            text += f" (pen. {self.shootout.home_score}-{self.shootout.away_score})"
        text += self._aggregate_text()
        if self.shootout is not None:
            winner = self.home if self.shootout.winner_side == "home" else self.away
        elif not self._aggregate_level():
            winner = self.home if self._deficit(self.home) < 0 else self.away
        else:
            winner = None
        text += f" — {winner.name} tur atlıyor!" if winner else " — Toplamda eşitlik bozulmadı."
        ev = self._log(EventType.FULL_TIME, None, None, text)
        if self.shootout is not None:
            ev.home_penalties, ev.away_penalties = self.shootout.home_score, self.shootout.away_score

    def _play_minute(self, minute: int, added: int) -> None:
        self.minute, self.added = minute, added
        self._apply_fatigue()
        if added == 0 and minute % max(1, self.cfg.energy_log_interval) == 0:
            for team in (self.home, self.away):
                for p in team.on_pitch:
                    p.log_energy(minute)
        if minute >= self.cfg.tactical_sub_from_minute and added == 0:
            for team in (self.home, self.away):
                self._tactical_substitution(team)

        attacking, defending = self._possession()
        attacking.stats.possession_minutes += 1
        self._attack(attacking, defending)
        self._discipline(attacking, defending)
        self._injury_check()

    def _half_time(self) -> None:
        for team in (self.home, self.away):
            for p in team.on_pitch:
                p.energy = min(100.0, p.energy + self.cfg.half_time_recovery)
        self._half_events = 0

    def _compute_added_time(self, half: int) -> int:
        """half 1/2: normal devreler; 3/4: uzatma devreleri (daha kisa, ayri tavan)."""
        if half >= 3:
            base = 0 if half == 3 else 1
            cap = self.cfg.extra_time_first_added_cap if half == 3 else self.cfg.extra_time_second_added_cap
            extra = base + self._half_events // 3 + self.rng.randint(0, 1)
            return max(0, min(extra, cap))
        base = 1 if half == 1 else 2
        extra = base + self._half_events // 3 + self.rng.randint(0, 1)
        return min(extra, 4 if half == 1 else 7)

    def _close_minutes(self) -> None:
        end = self.end_minute
        for team in (self.home, self.away):
            for p in team.players:
                if p.on_pitch:
                    p.log_energy(end)            # mac sonu enerjisi (90+X / 120+X de 90 / 120'ye yazilir)
                    p.left_minute = end
                    p.on_pitch = False

    # ------------------------------------------------------------------ yorgunluk

    def _apply_fatigue(self) -> None:
        for team in (self.home, self.away):
            team_mult = self.cfg.trailing_fatigue_multiplier if self._is_pressing(team) else 1.0
            for p in team.on_pitch:
                decay = self.cfg.fatigue_base_decay * ROLE_FATIGUE[p.role or p.position] * p.decay_multiplier
                if self.minute > 75:
                    decay *= 1.25     # son dakikalarda yorgunluk katlanir
                p.energy = max(0.0, p.energy - decay * team_mult)

    @staticmethod
    def _drain(p: MatchPlayer, amount: float) -> None:
        """Efor kaybi (sut, asist, faul): enerjiden aninda dusulur."""
        p.energy = max(0.0, p.energy - amount)

    def _tactical_substitution(self, team: MatchTeam) -> None:
        if team.subs_used >= self.max_subs - self.cfg.subs_reserved_for_emergency:
            return
        tired = [p for p in team.outfield_on_pitch if p.energy < self.cfg.tired_threshold]
        if not tired:
            return
        out = min(tired, key=lambda p: p.energy)
        same_pos = [b for b in team.bench if b.position is out.role]
        if not same_pos:
            return
        sub = max(same_pos, key=lambda p: p.effective_power)
        team.remove_player(out, self.minute)
        out.substituted = True
        team.field_player(sub, out.role, self.minute)
        team.subs_used += 1
        team.stats.substitutions += 1
        self._log(EventType.SUBSTITUTION, team, sub,
                  f"Değişiklik ({team.name}): {out.name} yoruldu, yerine {sub.name} giriyor.")

    # ------------------------------------------------------------------ pozisyon

    def _possession(self) -> tuple[MatchTeam, MatchTeam]:
        p_home = self._team_contest(self._team_strength(self.home, "midfield"),
                                    self._team_strength(self.away, "midfield"))
        if self.rng.random() < p_home:
            return self.home, self.away
        return self.away, self.home

    def _attack(self, attacking: MatchTeam, defending: MatchTeam) -> None:
        p_chance = self._scaled(
            self.cfg.base_chance,
            self._team_contest(self._team_strength(attacking, "attack"),
                               self._team_strength(defending, "defense")),
        )
        if self.rng.random() >= p_chance:
            return

        shooter = self._weighted_choice(attacking.outfield_on_pitch, lambda p: self._player_strength(p, "attack"))
        if shooter is None:
            return
        shooter.shots += 1
        attacking.stats.shots += 1
        self._drain(shooter, self.cfg.shot_energy_cost)

        defenders = sorted(
            (self._player_strength(p, "defense") for p in defending.outfield_on_pitch), reverse=True
        )[:4]
        defender_str = (sum(defenders) / len(defenders)) if defenders else 5.0
        defender_str *= self._situation_factor(defending, "defense")
        role_w = max(ROLE_WEIGHTS["attack"][shooter.role or shooter.position], 0.01)
        shooter_str = self._player_strength(shooter, "attack") / role_w
        shooter_str *= self._situation_factor(attacking, "attack")

        p_on_target = self._scaled(self.cfg.base_on_target, self._player_contest(shooter_str, defender_str))
        if self.rng.random() >= p_on_target:
            self._log(EventType.MISS, attacking, shooter, self._miss_text(shooter, attacking))
            return

        shooter.shots_on_target += 1
        attacking.stats.shots_on_target += 1
        keeper = defending.keeper
        p_goal = self._scaled(self.cfg.base_goal,
                              self._player_contest(shooter_str, self._keeper_strength(defending)))

        if self.rng.random() < p_goal:
            self._goal(attacking, shooter)
        else:
            if keeper is not None:
                keeper.saves += 1
            defending.stats.saves += 1
            self._log(EventType.SAVE, attacking, shooter, self._save_text(shooter, attacking, keeper))

    def _goal(self, team: MatchTeam, scorer: MatchPlayer) -> None:
        scorer.goals += 1
        team.stats.goals += 1
        self._half_events += 1

        assister: MatchPlayer | None = None
        if self.rng.random() < self.cfg.assist_share:
            candidates = [p for p in team.outfield_on_pitch if p is not scorer]
            assister = self._weighted_choice(candidates, lambda p: p.midfield_rating + p.attack_rating * 0.5)
            if assister:
                assister.assists += 1
                self._drain(assister, self.cfg.assist_energy_cost)

        score = f"{self.home.name} {self.home.stats.goals} - {self.away.stats.goals} {self.away.name}"
        assist_txt = f" {assister.name}'in asistiyle" if assister else ""
        flavor = self.rng.choice([
            "topu ağlarla buluşturuyor",
            "köşeye çok sert vuruyor, kalecinin şansı yok",
            "plase bir vuruşla filelere gönderiyor",
            "kafayı vuruyor ve top ağlarda",
            "ceza sahası içinde karambolde bitiriyor",
        ])
        self._log(EventType.GOAL, team, scorer,
                  f"GOOOL! {scorer.name} ({team.name}){assist_txt} {flavor}! Skor: {score}")

    def _miss_text(self, shooter: MatchPlayer, team: MatchTeam) -> str:
        texts = [
            f"{shooter.name} ({team.name}) şansını deniyor, top direğin yanından auta gidiyor.",
            f"{shooter.name} ({team.name}) uzaktan vuruyor, top üstten dışarı.",
            f"{shooter.name} ({team.name}) iyi pozisyonda ama vuruşu zayıf, top yandan auta çıkıyor.",
            f"{shooter.name} ({team.name}) direğe vuruyor! Top oyun alanına dönüyor ama defans uzaklaştırıyor.",
        ]
        # Direk nadir olmali (~%8), digerleri siradan kacirma
        return self.rng.choices(texts, weights=[40, 30, 22, 8], k=1)[0]

    def _save_text(self, shooter: MatchPlayer, team: MatchTeam, keeper: MatchPlayer | None) -> str:
        gk = keeper.name if keeper else "kaleci"
        return self.rng.choice([
            f"{shooter.name} ({team.name}) vuruyor... {gk} harika bir kurtarışla topu çeliyor!",
            f"{shooter.name} ({team.name}) sert vurdu ama {gk} köşeye uzanarak kurtarıyor.",
            f"{shooter.name} ({team.name}) kaleciyle karşı karşıya! {gk} ayaklarıyla kurtarıyor!",
            f"{shooter.name} ({team.name}) kafayı vuruyor, {gk} topu üstten kornere çeliyor.",
        ])

    # ------------------------------------------------------------------ disiplin

    def _discipline(self, attacking: MatchTeam, defending: MatchTeam) -> None:
        def team_factor(t: MatchTeam) -> float:
            return (sum(p.aggression for p in t.on_pitch) / max(1, t.player_count)) / 0.85

        p_card = self.cfg.base_card * (team_factor(defending) + team_factor(attacking)) / 2
        if self.rng.random() >= p_card:
            return

        team = defending if self.rng.random() < self.cfg.defending_team_card_share else attacking
        # Sari gormus oyuncu daha temkinli oynar (ikinci sari enflasyonunu onler)
        player = self._weighted_choice(
            team.on_pitch,
            lambda p: p.aggression * (self.cfg.cautious_after_yellow if p.yellow_cards else 1.0),
        )
        if player is None:
            return
        self._half_events += 1
        self._drain(player, self.cfg.foul_energy_cost)

        if self.rng.random() < self.cfg.straight_red_share:
            self._send_off(team, player, second_yellow=False)
            return

        player.yellow_cards += 1
        team.stats.yellow_cards += 1
        if player.yellow_cards >= 2:
            self._send_off(team, player, second_yellow=True)
            return

        self._log(EventType.YELLOW_CARD, team, player, self.rng.choice([
            f"{player.name} ({team.name}) sert müdahale, hakem sarı kartı gösteriyor.",
            f"{player.name} ({team.name}) geç kalıyor ve rakibini düşürüyor: SARI KART.",
            f"{player.name} ({team.name}) itiraz ediyor, hakem cebine gidiyor: sarı kart.",
        ]))

    def _send_off(self, team: MatchTeam, player: MatchPlayer, second_yellow: bool) -> None:
        player.sent_off = True
        team.remove_player(player, self.minute)
        team.stats.red_cards += 1
        was_keeper = player.role is Position.GK

        reason = "ikinci sarıdan KIRMIZI KART" if second_yellow else "korkunç bir faul, direkt KIRMIZI KART"
        self._log(EventType.RED_CARD, team, player,
                  f"{player.name} ({team.name}) {reason}! {team.name} {team.player_count} kişi kaldı.",
                  detail="second_yellow" if second_yellow else "straight_red")
        if was_keeper:
            self._ensure_keeper(team)

    # ------------------------------------------------------------------ sakatlik

    def _injury_check(self) -> None:
        if self.rng.random() >= self.cfg.base_injury:
            return
        team = self.rng.choice((self.home, self.away))
        player = self._weighted_choice(
            team.on_pitch,
            lambda p: (1 + max(0, p.age - 29) * 0.10) * (1 + (100 - p.energy) / 100),
        )
        if player is None:
            return
        player.injured = True
        team.stats.injuries += 1
        self._half_events += 1
        team.remove_player(player, self.minute)
        self._log(EventType.INJURY, team, player, self.rng.choice([
            f"{player.name} ({team.name}) yerde kaldı, sağlık ekibi sahada... Oyuna devam edemiyor!",
            f"{player.name} ({team.name}) ikili mücadelede sakatlandı, sedyeyle oyundan ayrılıyor.",
            f"{player.name} ({team.name}) kas sakatlığı işareti veriyor ve oyunu bırakmak zorunda.",
        ]))
        self._substitute_for(team, player)

    def _substitute_for(self, team: MatchTeam, out: MatchPlayer) -> None:
        """Sakatlanan oyuncunun yerine uygun yedegi sokar."""
        role = out.role or out.position

        if team.subs_used >= self.max_subs or not team.bench:
            reason = "değişiklik hakkı kalmadı" if team.subs_used >= self.max_subs else "kulübede oyuncu kalmadı"
            self._log(EventType.SUBSTITUTION, team, None,
                      f"{team.name} {reason}, {team.player_count} kişiyle devam ediyor!")
            if role is Position.GK:
                self._ensure_keeper(team)
            return

        same_pos = [p for p in team.bench if p.position is role]
        if same_pos:
            sub = max(same_pos, key=lambda p: p.effective_power)
            self._bring_on(team, sub, role, f"{out.name} yerine aynı mevkiden {sub.name} giriyor.")
            return

        if role is Position.GK:
            # Yedek kaleci yok: sahadan biri eldiven giyer, kulubeden saha oyuncusu girer
            self._ensure_keeper(team)
            outfield = [p for p in team.bench if p.position is not Position.GK]
            if outfield and team.subs_used < self.max_subs:
                sub = max(outfield, key=lambda p: p.effective_power)
                self._bring_on(team, sub, sub.position, f"{sub.name} kadroyu tamamlamak için giriyor.")
            return

        outfield = [p for p in team.bench if p.position is not Position.GK]
        if outfield:
            sub = max(outfield, key=lambda p: p.effective_power)
            self._bring_on(team, sub, role,
                           f"Aynı mevkide yedek yok! {sub.name} ({sub.position.value}) mevki dışı, "
                           f"{role.value} olarak giriyor.")
        else:
            self._log(EventType.SUBSTITUTION, team, None,
                      f"{team.name} kulübesinde saha oyuncusu yok, {team.player_count} kişiyle devam ediyor!")

    def _bring_on(self, team: MatchTeam, sub: MatchPlayer, role: Position, text: str) -> None:
        team.field_player(sub, role, self.minute)
        team.subs_used += 1
        team.stats.substitutions += 1
        self._log(EventType.SUBSTITUTION, team, sub, f"Değişiklik ({team.name}): {text}")

    def _ensure_keeper(self, team: MatchTeam) -> None:
        """Kalede kimse yoksa: yedek GK (saha oyuncusu feda) veya acil durum kalecisi."""
        if team.keeper is not None:
            return
        bench_gk = [p for p in team.bench if p.position is Position.GK]
        outfield = team.outfield_on_pitch

        if bench_gk and outfield and team.subs_used < self.max_subs:
            victim = min(outfield, key=lambda p: p.effective_power)
            team.remove_player(victim, self.minute)
            victim.substituted = True
            sub = bench_gk[0]
            self._bring_on(team, sub, Position.GK,
                           f"Kaleci için feda: {victim.name} çıkıyor, yedek kaleci {sub.name} giriyor.")
            return

        if outfield:
            emergency = max(outfield, key=lambda p: p.goalkeeping)
            emergency.role = Position.GK
            self._log(EventType.SUBSTITUTION, team, emergency,
                      f"{team.name} kalede yedek kaleci yok! {emergency.name} eldivenleri giyiyor.")

    # ------------------------------------------------------------------ notlar

    def _compute_ratings(self) -> None:
        for team in (self.home, self.away):
            opp = self._opponent(team)
            won = team.stats.goals > opp.stats.goals
            lost = team.stats.goals < opp.stats.goals
            clean_sheet = opp.stats.goals == 0
            for p in team.players:
                if not p.played:
                    continue
                r = 6.0
                r += p.goals * 1.0 + p.assists * 0.5 + p.shots_on_target * 0.1 + p.saves * 0.2
                r -= p.yellow_cards * 0.3 + (1.5 if p.sent_off else 0)
                if clean_sheet and p.role in (Position.GK, Position.DEF):
                    r += 0.5
                r += 0.3 if won else (-0.3 if lost else 0)
                left = p.left_minute if p.left_minute is not None else self.end_minute
                played_min = left - (p.entered_minute or 0)
                if played_min < 20:
                    r = 6.0 + (r - 6.0) * 0.5
                else:
                    # Yorgun oyuncu hata yapar: mac sonu (veya cikis) enerjisine gore ceza
                    r -= fitness.fatigue_rating_penalty(p.energy)
                p.rating = round(max(1.0, min(10.0, r)), 1)

    def _man_of_the_match(self) -> MatchPlayer | None:
        played = [p for t in (self.home, self.away) for p in t.players if p.played]
        return max(played, key=lambda p: (p.rating, p.goals, p.assists), default=None)


# ===========================================================================
# [3] VERITABANI ADAPTORU
# ===========================================================================

class FixtureAlreadyPlayed(Exception):
    pass


def build_match_team(
    team,
    is_home: bool,
    current_week: int | None = None,
    unavailability: Callable[[Any], str | None] | None = None,
) -> MatchTeam:
    """
    ORM Team -> MatchTeam (oyuncular kopyalanir, ORM nesnesi motora girmez).

    current_week verilirse sakat (injured_until_week > hafta) ve cezali
    (suspended_matches > 0) oyuncular kadroya HIC alinmaz: ne ilk 11'e ne
    kulubeye. Menajerin XI/BENCH/OUT kararlari ve takimin dizilisi motora tasinir.

    unavailability verilirse (orn. yalnizca kupada gecerli cezalar) her ORM oyuncu icin
    p.unavailability_reason(current_week) YERINE o cagrilir: sebep metni ya da None.
    """
    available: list[MatchPlayer] = []
    unavailable: list[tuple[MatchPlayer, str]] = []
    preferred: dict[int, Position] = {}
    for p in team.players:
        if unavailability is not None:
            reason = unavailability(p)
        else:
            reason = p.unavailability_reason(current_week) if current_week is not None else None
        mp = MatchPlayer.from_orm(p)
        if reason:
            unavailable.append((mp, reason))
            continue
        status = getattr(p, "lineup_status", None)
        if status is LineupStatus.XI and getattr(p, "lineup_role", None) is not None:
            preferred[p.id] = p.lineup_role
        elif status is LineupStatus.OUT:
            mp.matchday = False
        available.append(mp)
    return MatchTeam(
        id=team.id, name=team.name, reputation=team.reputation,
        players=available, is_home=is_home, unavailable=unavailable,
        formation=FORMATIONS.get(getattr(team, "formation", None)),
        preferred_xi=preferred,
    )


def update_standings(team, goals_for: int, goals_against: int) -> None:
    """Puan tablosu matematigi. 'team' nesnesi ilgili alanlara sahip herhangi bir sey olabilir."""
    team.played += 1
    team.goals_for += goals_for
    team.goals_against += goals_against
    if goals_for > goals_against:
        team.won += 1
        team.points += 3
    elif goals_for == goals_against:
        team.drawn += 1
        team.points += 1
    else:
        team.lost += 1


def apply_result(fixture, result: MatchResult, update_table: bool = True) -> None:
    """
    Sonucu ORM nesnelerine isler. Commit sorumlulugu cagirana aittir.
    update_table=False (kupa maci): lig puan tablosuna dokunulmaz, yalnizca fikstur yazilir.
    Skor uzatmalar dahil, penaltilar harictir (penaltilar result.shootout'ta).
    """
    from models import FixtureStatus

    if update_table:
        update_standings(fixture.home_team, result.home_score, result.away_score)
        update_standings(fixture.away_team, result.away_score, result.home_score)
    fixture.home_score = result.home_score
    fixture.away_score = result.away_score
    fixture.status = FixtureStatus.PLAYED


def play_fixture(db, fixture_id: int, seed: int | None = None,
                 persist: bool = True, config: EngineConfig | None = None,
                 current_week: int | None = None,
                 knockout: KnockoutRule | None = None,
                 neutral_venue: bool = False,
                 update_table: bool = True,
                 unavailability: Callable[[Any], str | None] | None = None) -> MatchResult:
    """
    Fikstur macini oynatir. persist=True ise fikstur (ve update_table ise puan durumu) guncellenir.
    current_week verilirse sakat/cezali oyuncular kadro disi kalir; unavailability verilirse
    bu kontrolun yerine gecer. knockout / neutral_venue MatchEngine'e aynen gecer.
    """
    from models import Fixture, FixtureStatus

    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        raise ValueError(f"Fikstur bulunamadi: id={fixture_id}")
    if fixture.status is FixtureStatus.PLAYED:
        raise FixtureAlreadyPlayed(
            f"Fikstur {fixture_id} zaten oynanmış "
            f"({fixture.home_team.name} {fixture.home_score}-{fixture.away_score} {fixture.away_team.name})."
        )

    week = current_week if current_week is not None else fixture.week
    home = build_match_team(fixture.home_team, True, week, unavailability)
    away = build_match_team(fixture.away_team, False, week, unavailability)
    result = MatchEngine(home, away, seed=seed, config=config,
                         knockout=knockout, neutral_venue=neutral_venue).simulate()

    if persist:
        apply_result(fixture, result, update_table=update_table)
    return result


def simulate_friendly(db, home_name: str, away_name: str,
                      seed: int | None = None, config: EngineConfig | None = None,
                      current_week: int | None = None,
                      knockout: KnockoutRule | None = None,
                      neutral_venue: bool = False) -> MatchResult:
    """Fiksture bagli olmayan hazirlik maci. Hicbir sey yazmaz. knockout: eleme maci provasi."""
    from sqlalchemy import select

    from models import Team

    home = db.scalar(select(Team).where(Team.name == home_name))
    away = db.scalar(select(Team).where(Team.name == away_name))
    if home is None or away is None:
        raise ValueError(f"Takım bulunamadı: {home_name if home is None else away_name}")
    return MatchEngine(build_match_team(home, True, current_week),
                       build_match_team(away, False, current_week),
                       seed=seed, config=config, knockout=knockout,
                       neutral_venue=neutral_venue).simulate()


# ===========================================================================
# [4] SUNUM (View) — terminal spikeri
# ===========================================================================

EVENT_ICONS = {
    EventType.KICK_OFF: "[BAŞLA]", EventType.GOAL: "[GOL]", EventType.MISS: "[ŞUT]",
    EventType.SAVE: "[KURT]", EventType.YELLOW_CARD: "[SARI]", EventType.RED_CARD: "[KIRMIZI]",
    EventType.INJURY: "[SAKAT]", EventType.SUBSTITUTION: "[DEĞ]",
    EventType.HALF_TIME: "[DEVRE]", EventType.FULL_TIME: "[BİTTİ]",
    EventType.EXTRA_TIME_START: "[UZATMA]", EventType.EXTRA_TIME_HALF: "[UZT.DEV]",
    EventType.SHOOTOUT_START: "[SERİ]", EventType.PENALTY_SHOOTOUT: "[PENALTI]",
}

KICK_SYMBOLS = {"scored": "O", "saved": "X", "missed": "X"}


def format_lineup(team: MatchTeam) -> str:
    starters = sorted(
        [p for p in team.players if p.entered_minute == 0],
        key=lambda p: (list(Position).index(p.role or p.position), -p.effective_power),
    )
    bench = [p for p in team.players if p.entered_minute != 0]
    lines = [f"  {team.name}  (itibar {team.reputation}, diziliş {'-'.join(map(str, team.formation))})"]
    for p in starters:
        lines.append(f"    {p.role.value:<4}{p.name:<22} OVR {p.overall:>2}  EFF {p.effective_power:5.1f}"
                     f"  form {p.form} moral {p.morale}")
    lines.append("    Yedekler: " + ", ".join(f"{p.name} ({p.position.value} {p.overall})" for p in bench))
    if team.unavailable:
        lines.append("    Kadro dışı: " + ", ".join(
            f"{p.name} ({p.position.value} {p.overall}, {reason})" for p, reason in team.unavailable))
    if team.lineup_notes:
        lines.append("    Asistan: " + " | ".join(team.lineup_notes))
    return "\n".join(lines)


def format_event(ev: MatchEvent) -> str:
    return f"  {ev.display_minute:>5}  {EVENT_ICONS[ev.type]:<9} {ev.description}"


def format_stats(result: MatchResult) -> str:
    h, a = result.home, result.away
    total_pos = max(1, h.stats.possession_minutes + a.stats.possession_minutes)
    rows = [
        ("Gol", h.stats.goals, a.stats.goals),
        ("Şut", h.stats.shots, a.stats.shots),
        ("İsabetli şut", h.stats.shots_on_target, a.stats.shots_on_target),
        ("Kurtarış", h.stats.saves, a.stats.saves),
        ("Topla oynama", f"%{round(100 * h.stats.possession_minutes / total_pos)}",
         f"%{100 - round(100 * h.stats.possession_minutes / total_pos)}"),
        ("Sarı kart", h.stats.yellow_cards, a.stats.yellow_cards),
        ("Kırmızı kart", h.stats.red_cards, a.stats.red_cards),
        ("Sakatlık", h.stats.injuries, a.stats.injuries),
        ("Değişiklik", h.stats.substitutions, a.stats.substitutions),
    ]
    lines = [f"  {'':<16}{h.name:>20}   {a.name:<20}"]
    for label, hv, av in rows:
        lines.append(f"  {label:<16}{str(hv):>20}   {str(av):<20}")

    lines.append("")
    for team in (h, a):
        scorers = ", ".join(f"{n} ({g})" for n, g in result.scorers(team)) or "-"
        lines.append(f"  Golcüler {team.name}: {scorers}")
    if result.man_of_the_match:
        m = result.man_of_the_match
        lines.append(f"\n  Maçın adamı: {m.name} — not {m.rating} "
                     f"({m.goals} gol, {m.assists} asist, {m.saves} kurtarış)")
    lines.append(f"  Uzatmalar: ilk yarı +{result.first_half_added}, ikinci yarı +{result.second_half_added}"
                 f"  |  toplam {result.total_minutes} dk")
    if result.extra_time:
        lines.append(f"  Uzatma devreleri (2x15): ilk +{result.extra_time_first_added}, "
                     f"ikinci +{result.extra_time_second_added}")
    if result.shootout is not None:
        so = result.shootout
        lines.append(f"  Penaltılar: {h.name} {so.home_score} - {so.away_score} {a.name}"
                     f"{' (ani ölüm)' if so.went_to_sudden_death else ''}")
        for side, team in (("home", h), ("away", a)):
            marks = " ".join(KICK_SYMBOLS.get(k.outcome, "?") for k in so.side_kicks(side))
            lines.append(f"    {team.name:<20} {marks}")
    if result.knockout is not None:
        advancing = result.advancing
        how = {"normal": "normal süre", "extra_time": "uzatmalar", "penalties": "penaltılar"}[result.decided_by]
        agg = ""
        if result.knockout.home_carry or result.knockout.away_carry:
            agg = f"toplam {result.home_aggregate}-{result.away_aggregate}, "
        lines.append(f"  Eleme: {agg}sonuç {how} ile belirlendi → "
                     f"{advancing.name + ' tur atladı' if advancing else 'eşitlik bozulmadı'}")
    if result.neutral_venue:
        lines.append("  Tarafsız saha: ev sahibi avantajı yok")
    return "\n".join(lines)


def print_match_report(result: MatchResult, show_lineups: bool = True) -> None:
    print("=" * 78)
    print(f" {result.home.name}  vs  {result.away.name}" + (f"   (seed={result.seed})" if result.seed is not None else ""))
    print("=" * 78)
    if show_lineups:
        print(format_lineup(result.home))
        print()
        print(format_lineup(result.away))
        print()
    print("-" * 78)
    print(" MAÇ AKIŞI")
    print("-" * 78)
    for ev in result.events:
        print(format_event(ev))
    print()
    print("-" * 78)
    tail = " (uzt.)" if result.extra_time else ""
    if result.shootout is not None:
        tail += f" (pen. {result.shootout.home_score}-{result.shootout.away_score})"
    print(f" SONUÇ: {result.home.name} {result.home_score} - {result.away_score} {result.away.name}{tail}")
    print("-" * 78)
    print(format_stats(result))
    print("=" * 78)


# ===========================================================================
# [5] TEST / CLI
# ===========================================================================

DERBY_TEAMS = ("Istanbul Lions", "Kadıköy Canaries")


def _find_derby_fixture(db):
    """Istanbul Lions - Kadıköy Canaries: once oynanmamis olani, Lions ev sahibi tercihli."""
    from sqlalchemy import select

    from models import Fixture, FixtureStatus, Team

    lions = db.scalar(select(Team).where(Team.name == DERBY_TEAMS[0]))
    canaries = db.scalar(select(Team).where(Team.name == DERBY_TEAMS[1]))
    if lions is None or canaries is None:
        return None, None
    fixtures = db.scalars(
        select(Fixture).where(
            ((Fixture.home_team_id == lions.id) & (Fixture.away_team_id == canaries.id))
            | ((Fixture.home_team_id == canaries.id) & (Fixture.away_team_id == lions.id))
        ).order_by(Fixture.week)
    ).all()
    unplayed = [f for f in fixtures if f.status is FixtureStatus.UNPLAYED]
    unplayed.sort(key=lambda f: (f.home_team_id != lions.id, f.week))
    return (unplayed[0] if unplayed else None), fixtures


def parse_carry(text: str | None) -> tuple[int, int]:
    """'2-1' -> (2, 1). Bos -> (0, 0). Negatif ya da bozuk deger ValueError."""
    if not text:
        return 0, 0
    parts = text.replace(":", "-").split("-")
    if len(parts) != 2:
        raise ValueError(f"Geçersiz toplam skor: {text!r} (örnek: 2-1)")
    home, away = (int(x.strip()) for x in parts)
    if home < 0 or away < 0:
        raise ValueError(f"Geçersiz toplam skor: {text!r}")
    return home, away


def main() -> int:
    from database import session_scope, wait_for_db

    parser = argparse.ArgumentParser(description="Maç simülatörü — test sürüşü")
    parser.add_argument("--fixture-id", type=int, help="Belirli bir fikstür maçı oynat")
    parser.add_argument("--home", help="Hazırlık maçı: ev sahibi takım adı")
    parser.add_argument("--away", help="Hazırlık maçı: deplasman takım adı")
    parser.add_argument("--seed", type=int, default=None, help="Rastgelelik tohumu")
    parser.add_argument("--dry-run", action="store_true", help="Veritabanına yazma")
    parser.add_argument("--no-lineups", action="store_true", help="Kadroları basma")
    parser.add_argument("--knockout", action="store_true",
                        help="Hazırlık maçını eleme maçı olarak oynat: toplamda eşitlikte uzatma, sonra penaltılar")
    parser.add_argument("--carry", default=None,
                        help="Eleme: önceki maçtan taşınan skor (ev-deplasman, örn. 2-1). --knockout gerektirir")
    parser.add_argument("--neutral", action="store_true", help="Tarafsız saha (ev sahibi avantajı yok)")
    parser.add_argument("--no-extra-time", action="store_true", help="Eleme: uzatma oynanmaz, direkt penaltılar")
    args = parser.parse_args()

    knockout = None
    if args.knockout:
        try:
            home_carry, away_carry = parse_carry(args.carry)
        except ValueError as exc:
            print(f"[engine] {exc}")
            return 1
        knockout = KnockoutRule(home_carry=home_carry, away_carry=away_carry,
                                extra_time=not args.no_extra_time)
    elif args.carry or args.no_extra_time:
        print("[engine] --carry / --no-extra-time yalnızca --knockout ile kullanılır.")
        return 1

    if not wait_for_db(retries=3, delay=1.0, verbose=False):
        print("[engine] Veritabanına bağlanılamadı. 'docker compose up -d' çalıştı mı?")
        return 1

    with session_scope() as db:
        if args.home and args.away:
            kind = "Eleme maçı provası" if knockout else "Hazırlık maçı"
            print(f"[engine] {kind}: {args.home} - {args.away} (DB'ye yazılmaz)")
            result = simulate_friendly(db, args.home, args.away, seed=args.seed,
                                       knockout=knockout, neutral_venue=args.neutral)
        elif knockout is not None or args.neutral:
            print("[engine] --knockout / --neutral yalnızca --home ve --away ile (hazırlık maçı) kullanılır.")
            return 1
        else:
            if args.fixture_id is not None:
                from models import Fixture
                fixture = db.get(Fixture, args.fixture_id)
                if fixture is None:
                    print(f"[engine] Fikstür bulunamadı: {args.fixture_id}")
                    return 1
            else:
                fixture, all_derbies = _find_derby_fixture(db)
                if fixture is None:
                    if not all_derbies:
                        print("[engine] Derbi fikstürü yok. Önce 'python seed.py' çalıştır.")
                        return 1
                    played = all_derbies[0]
                    print(f"[engine] Tüm derbiler oynanmış (son: {played.home_team.name} "
                          f"{played.home_score}-{played.away_score} {played.away_team.name}). "
                          f"Hazırlık maçı olarak tekrar oynatılıyor — DB'ye yazılmaz.")
                    result = simulate_friendly(db, played.home_team.name, played.away_team.name, seed=args.seed)
                    print_match_report(result, show_lineups=not args.no_lineups)
                    return 0

            persist = not args.dry_run
            print(f"[engine] Fikstür #{fixture.id} | {fixture.week}. hafta | "
                  f"{fixture.home_team.name} - {fixture.away_team.name} | "
                  f"{'DB güncellenecek' if persist else 'DRY RUN'}")
            try:
                result = play_fixture(db, fixture.id, seed=args.seed, persist=persist)
            except FixtureAlreadyPlayed as exc:
                print(f"[engine] {exc} Yeniden oynatmak için 'python seed.py' ile sıfırla "
                      f"veya --home/--away ile hazırlık maçı yap.")
                return 1

        print_match_report(result, show_lineups=not args.no_lineups)

        if not args.dry_run and not (args.home and args.away):
            h, a = fixture.home_team, fixture.away_team
            print(f"[engine] Puan durumu güncellendi: {h.name} {h.points}p ({h.won}G {h.drawn}B {h.lost}M) | "
                  f"{a.name} {a.points}p ({a.won}G {a.drawn}B {a.lost}M)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
