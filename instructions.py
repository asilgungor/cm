"""
instructions.py
===============
Takim talimatlari (9. Asama; Soccer Manager tarzi genisletme). SAF MANTIK: veritabani, ORM ya da
Streamlit BILMEZ.

Menajer mac icinde (veya baslama dudugunden once) takimina su eksenlerde emir verir:

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

    Pas stili (PassingStyle)
        SHORT           Kisa Pas: orta saha kontrolu (topa sahip olma) artar, pozisyon sayisi
                        duser ama pozisyonlar daha net; oyuncular daha az yorulur. Rakip tum
                        sahada baski yaparsa pas hatasi riski buyur (baskiya acik)
        MIXED           Notr
        DIRECT          Direkt Oyun: pozisyonlar hizli ve daha sik uretilir, orta saha kontrolu
                        ve pozisyon kalitesi biraz duser; rakip baskisini buyuk olcude atlatir

    Tempo
        SLOW / NORMAL / FAST   Hizli tempo: daha cok atak, daha cok yorgunluk, biraz daha fazla
                               pas hatasi (orta saha kontrolu ve kalite duser). Yavas tersidir.

    Pres (Pressing)
        OWN_HALF        Kendi yari sahasinda: blok kompakt (savunma artar), rakibe topla oynama
                        firsati verilir, enerji ve faul azalir
        MIDFIELD        Notr
        ALL_OVER        Tum sahada: rakibin orta saha gucu duser, kendi yorgunluk belirgin artar,
                        biraz daha fazla faul, savunmanin arkasinda bosluk kalir

    Hucum yonu (AttackingFocus)
        CENTRE          Merkezden: daha az ama daha net pozisyon; kalite, oyuncularin kisa pas /
                        teknik ve bitiricilik becerisiyle (takimin genel seviyesine gore) olceklenir
        MIXED           Notr
        FLANKS          Kanatlardan: daha cok pozisyon ama ortalar daha dusuk kaliteli; kalite
                        orta yapma ve kafa (hava topu) becerisiyle olceklenir

    Ofsayt taktigi (offside_trap)     savunma hatti yavas forvetlere karsi guclenir; hizli
                                      forvetlere karsi zayiflar ve arkaya atilan toplar net
                                      pozisyona doner (hiz: MatchPlayer.pace + FM hizlanma)
    Kontra atak (counter_attack)      topu rakibe birakir (orta saha duser); rakip Cok Ofansif,
                                      tum sahada pres ya da gerideyken bastiriyorsa hucum ve
                                      pozisyon kalitesi artar; otobus ceken rakibe karsi zayiflar

Etkilerin motordaki yeri (match_engine.MatchEngine):
    strength_factor(kind)   _team_strength: hucum / orta saha / savunma takim gucu (rakipten bagimsiz)
    chance_quality          _attack: sutcunun gucu (isabet ve gol olasiligi) -- rakipten bagimsiz kisim
    fatigue_factor          _apply_fatigue: dakikalik enerji kaybi
    card_factor             _discipline: kart olayi olasiligi ve kartin hangi takima cikacagi
    straight_red_factor     _discipline: kartin direkt kirmizi olma payi
    injury_factor           _injury_check: iki takimin ortalamasi macin sakatlik olasiligini olcekler
    Rakibe bagli etkiler (pres x pas stili, kontra atak, ofsayt taktigi, hucum yonunun oyuncu
    becerisiyle etkilesimi) motorda _matchup_factor / _chance_quality ile hesaplanir; katsayilar
    burada (PRESSING_EFFECTS.opponent_midfield, PASSING_EFFECTS.press_exposure, OFFSIDE_TRAP,
    COUNTER_ATTACK, FOCUS_EFFECTS.attribute_influence).

Varsayilan talimat tum carpanlarda TAM 1.0'dir; motor eski davranisla bit-bit aynidir (golden
regresyon testleri). Eski talimatlar (zihniyet/sertlik) varsayilan yeni eksenlerle carpildiginda da
degerleri birebir korunur (x * 1.0 == x).

JSONB saklama: to_dict() / from_dict(). from_dict eksik, bilinmeyen ya da gecersiz anahtarlari
sessizce varsayilana cevirir (eski kayitlar ve ileride eklenecek alanlar kirilmaz).

AI kulupleri: ai_instructions(guc orani, ev sahibi mi, skor farki, dakika) saf fonksiyonu; motorda
EngineConfig.ai_tactics=True iken menajer kontrolunde olmayan takimlara uygulanir.

14E "taktik etkisi ve karsi hamle" (motorda EngineConfig.tactics_v2; bayrak kapaliyken asagidakiler OKUNMAZ):
    concede_quality         zihniyet x pres: RAKIBIN akan oyun sans netligi ("kale onu"). Otobus 0.82, kendi yari
                            0.94, topyekun hucum 1.12, tum saha pres 1.05; varsayilan ve diger eksenler tam 1.0
    MATCHUP_EFFECTS         rakibe bagli karsi hamlelerin TEK tablosu (kalibrasyon tablosu asagida, tablonun ustunde)
    ai_formation            AI mac ici dizilis karari (saf); ai_counter_move: AI rakibin gorunen talimatina karsi hamle
Mevcut tablolarin (MENTALITY_EFFECTS ... COUNTER_ATTACK) degerleri DEGISMEDI: bayrak kapali motor bit-bit aynidir.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any


class Mentality(str, Enum):
    PARK_THE_BUS = "PARK_THE_BUS"
    BALANCED = "BALANCED"
    ALL_OUT_ATTACK = "ALL_OUT_ATTACK"


class Tackling(str, Enum):
    CALM = "CALM"
    NORMAL = "NORMAL"
    HARD = "HARD"


class PassingStyle(str, Enum):
    SHORT = "SHORT"
    MIXED = "MIXED"
    DIRECT = "DIRECT"


class Tempo(str, Enum):
    SLOW = "SLOW"
    NORMAL = "NORMAL"
    FAST = "FAST"


class Pressing(str, Enum):
    OWN_HALF = "OWN_HALF"
    MIDFIELD = "MIDFIELD"
    ALL_OVER = "ALL_OVER"


class AttackingFocus(str, Enum):
    CENTRE = "CENTRE"
    MIXED = "MIXED"
    FLANKS = "FLANKS"


AttackingWidth = AttackingFocus          # Soccer Manager adlandirmasi (ayni enum)


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
PASSING_LABELS: dict[PassingStyle, str] = {
    PassingStyle.SHORT: "Kısa Pas",
    PassingStyle.MIXED: "Karışık",
    PassingStyle.DIRECT: "Direkt Oyun",
}
TEMPO_LABELS: dict[Tempo, str] = {
    Tempo.SLOW: "Yavaş",
    Tempo.NORMAL: "Normal",
    Tempo.FAST: "Hızlı",
}
PRESSING_LABELS: dict[Pressing, str] = {
    Pressing.OWN_HALF: "Kendi Yarı Sahasında",
    Pressing.MIDFIELD: "Orta Sahada",
    Pressing.ALL_OVER: "Tüm Sahada",
}
FOCUS_LABELS: dict[AttackingFocus, str] = {
    AttackingFocus.CENTRE: "Merkezden",
    AttackingFocus.MIXED: "Karışık",
    AttackingFocus.FLANKS: "Kanatlardan",
}
OFFSIDE_TRAP_LABEL = "Ofsayt Taktiği"
COUNTER_ATTACK_LABEL = "Kontra Atak"


@dataclass(frozen=True)
class MentalityEffect:
    attack: float
    midfield: float
    defense: float
    fatigue: float
    # 14E (EngineConfig.tactics_v2): RAKIBIN akan oyun sans NETLIGI carpani ("kale onu"). Kapali blok rakibe
    # az ve kotu sans verir; acik oynayan takim net sans verir. Bayrak kapaliyken okunmaz.
    concede_quality: float = 1.0


@dataclass(frozen=True)
class TacklingEffect:
    defense: float
    card: float
    straight_red: float
    injury: float
    fatigue: float


@dataclass(frozen=True)
class PassingEffect:
    attack: float
    midfield: float
    chance_quality: float
    fatigue: float
    press_exposure: float        # rakip presinin orta saha etkisinin bu takima yansiyan payi (1.0 = aynen)


@dataclass(frozen=True)
class TempoEffect:
    attack: float
    midfield: float
    chance_quality: float
    fatigue: float


@dataclass(frozen=True)
class PressingEffect:
    opponent_midfield: float     # RAKIBIN orta saha gucu carpani
    defense: float
    fatigue: float
    card: float
    concede_quality: float = 1.0  # 14E: rakibin sans netligi (kompakt blok -, onde basan takimin arkasi +)


@dataclass(frozen=True)
class FocusEffect:
    attack: float
    chance_quality: float
    attribute_influence: float   # beceri farki (0-100 puan) basina kalite carpani degisimi


@dataclass(frozen=True)
class OffsideTrapEffect:
    base_defense: float          # esit hizda savunma carpani
    pace_influence: float        # (rakip forvet hizi - kendi defans hizi) puani basina savunma kaybi
    defense_range: tuple[float, float]
    through_ball_quality: float  # hizli forvet farki puani basina rakibin pozisyon kalitesi artisi
    through_ball_cap: float


@dataclass(frozen=True)
class CounterAttackEffect:
    midfield: float              # topu rakibe birakir
    vs_all_out_attack: float     # rakip Cok Ofansif: hucum carpanina eklenir
    vs_high_press: float         # rakip tum sahada pres
    vs_desperate: float          # rakip son bolumde geride ve bastiriyor
    vs_park_the_bus: float       # rakip otobus cekti: kontraya alan yok
    vs_own_half_press: float     # rakip kendi yari sahasinda bekliyor
    direct_synergy: float        # direkt pas stilinde olumlu bonus bu katla buyur
    quality_share: float         # hucum bonusunun pozisyon kalitesine yansiyan payi
    attack_range: tuple[float, float]


# Kalibrasyon: takim gucleri motorda a^2 / (a^2 + b^2) ile olasiliga doner. Cok Ofansif'te
# hucum x1.20 esit rakibe karsi dakikalik pozisyon sansini ~%18 artirir; savunma x0.82 rakibin
# sansini ~%20 artirir. Cok Defansif'te kendi sans ~%24 duser, rakibinki ~%16 duser.
MENTALITY_EFFECTS: dict[Mentality, MentalityEffect] = {
    Mentality.PARK_THE_BUS: MentalityEffect(attack=0.78, midfield=0.94, defense=1.18, fatigue=0.95,
                                            concede_quality=0.82),
    Mentality.BALANCED: MentalityEffect(attack=1.0, midfield=1.0, defense=1.0, fatigue=1.0),
    Mentality.ALL_OUT_ATTACK: MentalityEffect(attack=1.20, midfield=1.04, defense=0.82, fatigue=1.10,
                                              concede_quality=1.12),
}
TACKLING_EFFECTS: dict[Tackling, TacklingEffect] = {
    Tackling.CALM: TacklingEffect(defense=0.95, card=0.55, straight_red=0.8, injury=0.75, fatigue=0.97),
    Tackling.NORMAL: TacklingEffect(defense=1.0, card=1.0, straight_red=1.0, injury=1.0, fatigue=1.0),
    Tackling.HARD: TacklingEffect(defense=1.08, card=2.0, straight_red=1.5, injury=2.0, fatigue=1.05),
}

# Kalibrasyon (esit iki takim 80-80, ev sahibi talimatli, deplasman notr; 1000 macin ortalamasi).
# Topa sahip olma orta saha gucuyle, pozisyon hucum/savunmayla a^2/(a^2+b^2) uzerinden olusur; sut
# isabeti ve gol sutcu gucuyle a^1.5/(a^1.5+b^1.5) uzerinden (kalite x1.04 -> sut basina gol ~%6).
# Notr: ev 12.5 sut / 1.35 gol, deplasman 10.3 sut / 1.09 gol, topa sahip olma %54.9, saha
# oyuncusu mac sonu enerjisi 44.6, kart 1.98; galibiyet %40.2, maglubiyet %28.5.
#                 sut     rakip sut  gol    rakip gol  topa sahip  enerji  kart   G / M
#   Kisa pas      -%10    -%5        -%7    -%8        %57.3       47.2    -%2    %40.3 / %28.0
#   Direkt        +%8     +%3        +%1    +%5        %53.4       42.7    0      %39.9 / %30.6
#   Yavas tempo   -%3     -%4        0      -%5        %56.5       48.9    -%3    %42.5 / %28.0
#   Hizli tempo   +%5     +%4        +%2    +%8        %53.7       39.7    +%1    %40.0 / %30.5
#   Kendi yari    -%2     -%3        -%3    -%4        %53.0       48.9    -%9    %40.9 / %28.8
#   Tum saha pres +%5     -%1        0      +%4        %58.3       35.4    +%20   %39.9 / %29.8
#   Merkezden     -%3     0          +%2    -%2        %54.9       44.7    -%1    %43.5 / %28.3
#   Kanatlardan   +%4     0          -%1    +%1        %55.0       44.6    -%1    %39.9 / %29.0
#   Ofsayt tak.   0       -%4        -%1    -%4        %54.9       44.7    0      %41.5 / %27.4
#   Kontra atak   -%3     +%4        -%3    +%4        %52.9       44.6    +%2    %37.9 / %31.2
# Rakibe bagli: Cok Ofansif rakibe karsi kontra atak golu +%11 (G/M %42.6/%32.2 -> %46.1/%28.8);
# tum sahada pres yapan rakibe karsi direkt oyun G/M %39.0/%30.1 -> %42.2/%29.0, kisa pas %38.8/%30.8.
# Kisa pas: orta saha x1.05, hucum x0.87, kalite x1.04, yorgunluk x0.96; tum sahada prese karsi pres
# etkisi x1.35 yansir (pas hatasi). Direkt: hucum x1.12, orta saha x0.97, kalite x0.97; pres etkisinin
# yalnizca yarisi yansir.
PASSING_EFFECTS: dict[PassingStyle, PassingEffect] = {
    PassingStyle.SHORT: PassingEffect(attack=0.87, midfield=1.05, chance_quality=1.04, fatigue=0.96,
                                      press_exposure=1.35),
    PassingStyle.MIXED: PassingEffect(attack=1.0, midfield=1.0, chance_quality=1.0, fatigue=1.0, press_exposure=1.0),
    PassingStyle.DIRECT: PassingEffect(attack=1.12, midfield=0.97, chance_quality=0.97, fatigue=1.03,
                                       press_exposure=0.5),
}
# Tempo: hizli hucum x1.08 (+%8 pozisyon), orta saha x0.98 (pas hatasi), kalite x0.99, yorgunluk x1.08.
# Yavas: hucum x0.94, orta saha x1.03, kalite x1.02, yorgunluk x0.93.
TEMPO_EFFECTS: dict[Tempo, TempoEffect] = {
    Tempo.SLOW: TempoEffect(attack=0.94, midfield=1.03, chance_quality=1.02, fatigue=0.93),
    Tempo.NORMAL: TempoEffect(attack=1.0, midfield=1.0, chance_quality=1.0, fatigue=1.0),
    Tempo.FAST: TempoEffect(attack=1.08, midfield=0.98, chance_quality=0.99, fatigue=1.08),
}
# Pres: tum sahada rakibin orta sahasi x0.92 (+%3.4 puan topa sahip olma), kendi savunma x0.95 (rakip
# pozisyon sansi +%5), yorgunluk x1.15 (mac sonu ~9 enerji eksik), kart x1.20.
# Kendi yari sahasinda: rakip orta saha x1.05, savunma x1.05, yorgunluk x0.93, kart x0.90.
PRESSING_EFFECTS: dict[Pressing, PressingEffect] = {
    Pressing.OWN_HALF: PressingEffect(opponent_midfield=1.05, defense=1.05, fatigue=0.93, card=0.90,
                                      concede_quality=0.94),
    Pressing.MIDFIELD: PressingEffect(opponent_midfield=1.0, defense=1.0, fatigue=1.0, card=1.0),
    Pressing.ALL_OVER: PressingEffect(opponent_midfield=0.92, defense=0.95, fatigue=1.15, card=1.20,
                                      concede_quality=1.05),
}
# Hucum yonu: kanatlar hucum x1.04 / kalite x0.97, merkez hucum x0.97 / kalite x1.03. Kalite ayrica
# ilgili beceri (0-100) ile takimin ilk 5 saha oyuncusunun genel gucu arasindaki farkla olceklenir:
# puan basina %0.5, [0.93, 1.07] araliginda (motor: _focus_skill_factor).
FOCUS_EFFECTS: dict[AttackingFocus, FocusEffect] = {
    AttackingFocus.CENTRE: FocusEffect(attack=0.97, chance_quality=1.03, attribute_influence=0.005),
    AttackingFocus.MIXED: FocusEffect(attack=1.0, chance_quality=1.0, attribute_influence=0.0),
    AttackingFocus.FLANKS: FocusEffect(attack=1.04, chance_quality=0.97, attribute_influence=0.005),
}
FOCUS_SKILL_RANGE: tuple[float, float] = (0.93, 1.07)
# Ofsayt taktigi: esit hizda savunma x1.03; rakip forvetler 10 puan hizliysa x0.97 ve rakip
# pozisyonlari %5 daha net; 10 puan yavassa x1.09.
OFFSIDE_TRAP = OffsideTrapEffect(base_defense=1.03, pace_influence=0.006, defense_range=(0.90, 1.10),
                                 through_ball_quality=0.005, through_ball_cap=1.08)
# Kontra atak: orta saha x0.96; Cok Ofansif rakibe karsi hucum x1.10 (direkt pasla x1.13), tum sahada
# pres +0.05, bastiran (geride) rakip +0.05, otobus ceken rakip -0.06; bonusun %30'u kaliteye.
COUNTER_ATTACK = CounterAttackEffect(midfield=0.96, vs_all_out_attack=0.10, vs_high_press=0.05, vs_desperate=0.05,
                                     vs_park_the_bus=-0.06, vs_own_half_press=-0.03, direct_synergy=1.3,
                                     quality_share=0.3, attack_range=(0.90, 1.22))


@dataclass(frozen=True)
class MatchupEffects:
    """
    14E karsi hamleler (EngineConfig.tactics_v2; bayrak kapaliyken OKUNMAZ). Rakibe bagli carpanlarin tek tablosu;
    motor _matchup_factor / _chance_quality / _concede_factor. Varsayilan talimat ciftinde her carpan TAM 1.0.
    """
    block_centre: float                 # kapali blok (rakip concede_quality < 1) karsisinda MERKEZ: netlik cezasi xN
    block_flanks: float                 # KANAT: ceza bu kata iner (kalabalik blok ortaya kapanir, orta tek cikis)
    flanks_vs_block_attack: float       # kanat hucumu kapali bloga karsi hucum (hacim) carpani
    press_tempo_exposure: tuple[float, float, float]   # tum saha presin orta saha etkisi x (YAVAS, NORMAL, HIZLI)
    press_bypass_quality: float         # kisa pas + yavas tempo tum saha presi kirar: kendi sans netligi carpani
    counter_vs_all_out: float           # kontra: topyekun hucum eden rakip (COUNTER_ATTACK.vs_all_out_attack yerine)
    counter_vs_high_press: float        # kontra: onde basan rakibin arkasi bos (COUNTER_ATTACK.vs_high_press yerine)
    counter_vs_superior: float          # kontra: rakibin yapisal ustunlugu (guc orani - 1) basina hucum bonusu
    counter_attack_max: float           # kontra hucum carpaninin tavani (COUNTER_ATTACK.attack_range ust siniri yerine)
    counter_clarity: float              # kontra hucum bonusunun sans NETLIGINE yansiyan payi (bos alana kosu: net sans)


# Kalibrasyon (tactics_v2 acik; esli tohum, tohumlarin yarisinda taraflar yer degistirir; .claude/phase14/kanit/
# 14E_supurme.txt). Puan/mac farki, rakip varsayilan talimatli:
#                         80 v 80 (n=3000)    70 v 85 zayif taraf (n=3000)
#   Otobus                -0.171              -0.166
#   Kendi yari            +0.059              -0.008
#   Kendi yari+kontra+dir -0.026              +0.097   (zayif takim plani; 14E oncesi -0.017)
#   Kontra + direkt       -0.063              +0.058
#   Topyekun hucum        +0.049              +0.117   (14E oncesi +0.245)
#   Topyekun + merkez     +0.106 (en iyi)
#   Tum saha pres         +0.017
#   Kisa pas + yavas      -0.082              -0.077
#   Otobus+kisa+yavas+mrk -0.269 (en kotu)    -> talimat uzayi yayilimi 0.375 (14E oncesi 0.313)
# Dongusel ciftler, 80 v 80 kafa kafaya (X puani - Y puani, n=4000): (a) kontra+direkt > topyekun +0.108;
# (b) topyekun+kanat > otobus+kendi yari +0.454; (c) kisa+yavas > tum saha pres +0.380, tum saha pres > kisa+hizli
# +0.464; (d) otobus+kontra > kisa pas+tum saha pres +0.408. Hepsi >= 2.5 SE.
MATCHUP_EFFECTS = MatchupEffects(block_centre=1.5, block_flanks=0.5, flanks_vs_block_attack=1.05,
                                 press_tempo_exposure=(0.0, 1.0, 1.6), press_bypass_quality=1.06,
                                 counter_vs_all_out=0.20, counter_vs_high_press=0.20, counter_vs_superior=0.35,
                                 counter_attack_max=1.30, counter_clarity=0.4)


@dataclass(frozen=True)
class TeamInstructions:
    mentality: Mentality = Mentality.BALANCED
    tackling: Tackling = Tackling.NORMAL
    passing_style: PassingStyle = PassingStyle.MIXED
    tempo: Tempo = Tempo.NORMAL
    pressing: Pressing = Pressing.MIDFIELD
    attacking_focus: AttackingFocus = AttackingFocus.MIXED
    offside_trap: bool = False
    counter_attack: bool = False
    # Onceden hesaplanan carpanlar (motor dakikada birkac kez sorar). Karsilastirmaya girmez.
    _factors: dict[str, float] = field(init=False, repr=False, compare=False, hash=False, default_factory=dict)

    def __post_init__(self) -> None:
        for name, parser in _PARSERS.items():
            value = getattr(self, name)
            parsed = parser(value)
            if parsed is not value:
                object.__setattr__(self, name, parsed)
        for name in _FLAGS:
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise ValueError(f"{FIELD_LABELS[name].capitalize()} açık/kapalı (True/False) olmalı: {value!r}")
        m, t = MENTALITY_EFFECTS[self.mentality], TACKLING_EFFECTS[self.tackling]
        p, tp = PASSING_EFFECTS[self.passing_style], TEMPO_EFFECTS[self.tempo]
        pr, fc = PRESSING_EFFECTS[self.pressing], FOCUS_EFFECTS[self.attacking_focus]
        counter_mid = COUNTER_ATTACK.midfield if self.counter_attack else 1.0
        # Carpim sirasi eski eksenlerle baslar: varsayilan yeni eksenlerde deger birebir korunur
        self._factors.update(
            attack=m.attack * p.attack * tp.attack * fc.attack,
            midfield=m.midfield * p.midfield * tp.midfield * counter_mid,
            defense=m.defense * t.defense * pr.defense,
            fatigue=m.fatigue * t.fatigue * p.fatigue * tp.fatigue * pr.fatigue,
            card=t.card * pr.card,
            quality=p.chance_quality * tp.chance_quality * fc.chance_quality,
            concede=m.concede_quality * pr.concede_quality,
        )

    @property
    def is_default(self) -> bool:
        return self == DEFAULT_INSTRUCTIONS

    @property
    def is_basic(self) -> bool:
        """Yalnizca zihniyet/sertlik degisik mi (yeni eksenler varsayilan)? 9. Asama davranisi."""
        return all(getattr(self, name) == getattr(DEFAULT_INSTRUCTIONS, name) for name in EXTENDED_FIELDS)

    @property
    def _mentality(self) -> MentalityEffect:
        return MENTALITY_EFFECTS[self.mentality]

    @property
    def _tackling(self) -> TacklingEffect:
        return TACKLING_EFFECTS[self.tackling]

    @property
    def passing_effect(self) -> PassingEffect:
        return PASSING_EFFECTS[self.passing_style]

    @property
    def pressing_effect(self) -> PressingEffect:
        return PRESSING_EFFECTS[self.pressing]

    @property
    def focus_effect(self) -> FocusEffect:
        return FOCUS_EFFECTS[self.attacking_focus]

    def strength_factor(self, kind: str) -> float:
        """kind: 'attack' | 'midfield' | 'defense'. Rakipten bagimsiz talimat carpani."""
        if kind in ("attack", "midfield", "defense"):
            return self._factors[kind]
        raise ValueError(f"Bilinmeyen güç türü: {kind}")

    @property
    def fatigue_factor(self) -> float:
        return self._factors["fatigue"]

    @property
    def card_factor(self) -> float:
        return self._factors["card"]

    @property
    def straight_red_factor(self) -> float:
        return self._tackling.straight_red

    @property
    def injury_factor(self) -> float:
        return self._tackling.injury

    @property
    def chance_quality(self) -> float:
        """Pozisyon kalitesi (sutcu gucu) carpani: pas stili x tempo x hucum yonu (beceri etkisi haric)."""
        return self._factors["quality"]

    @property
    def concede_quality(self) -> float:
        """14E: RAKIBIN akan oyun sans netligi carpani (zihniyet x pres; varsayilan tam 1.0). tactics_v2 okur."""
        return self._factors["concede"]

    def describe(self) -> str:
        text = f"Zihniyet: {MENTALITY_LABELS[self.mentality]} · Sertlik: {TACKLING_LABELS[self.tackling]}"
        extras = self.extended_parts()
        return text + "".join(f" · {part}" for part in extras)

    def extended_parts(self, include_defaults: bool = False) -> list[str]:
        """Yeni eksenlerin etiketleri ('Pas: Kısa Pas' ...); varsayilanlar istenmedikce atlanir."""
        d = DEFAULT_INSTRUCTIONS
        parts = []
        if include_defaults or self.passing_style is not d.passing_style:
            parts.append(f"Pas: {PASSING_LABELS[self.passing_style]}")
        if include_defaults or self.tempo is not d.tempo:
            parts.append(f"Tempo: {TEMPO_LABELS[self.tempo]}")
        if include_defaults or self.pressing is not d.pressing:
            parts.append(f"Pres: {PRESSING_LABELS[self.pressing]}")
        if include_defaults or self.attacking_focus is not d.attacking_focus:
            parts.append(f"Hücum yönü: {FOCUS_LABELS[self.attacking_focus]}")
        if include_defaults or self.offside_trap:
            parts.append(f"{OFFSIDE_TRAP_LABEL}: {'Açık' if self.offside_trap else 'Kapalı'}")
        if include_defaults or self.counter_attack:
            parts.append(f"{COUNTER_ATTACK_LABEL}: {'Açık' if self.counter_attack else 'Kapalı'}")
        return parts

    def changes_from(self, old: TeamInstructions) -> list[str]:
        """Olay metni icin yalnizca degisen eksenler: ['zihniyet Dengeli', 'tempo Hızlı', ...]."""
        parts = []
        if self.mentality is not old.mentality:
            parts.append(f"zihniyet {MENTALITY_LABELS[self.mentality]}")
        if self.tackling is not old.tackling:
            parts.append(f"sertlik {TACKLING_LABELS[self.tackling]}")
        if self.passing_style is not old.passing_style:
            parts.append(f"pas stili {PASSING_LABELS[self.passing_style]}")
        if self.tempo is not old.tempo:
            parts.append(f"tempo {TEMPO_LABELS[self.tempo]}")
        if self.pressing is not old.pressing:
            parts.append(f"pres {PRESSING_LABELS[self.pressing]}")
        if self.attacking_focus is not old.attacking_focus:
            parts.append(f"hücum yönü {FOCUS_LABELS[self.attacking_focus]}")
        if self.offside_trap != old.offside_trap:
            parts.append(f"ofsayt taktiği {'açık' if self.offside_trap else 'kapalı'}")
        if self.counter_attack != old.counter_attack:
            parts.append(f"kontra atak {'açık' if self.counter_attack else 'kapalı'}")
        return parts

    # ------------------------------------------------------------------ JSONB

    def to_dict(self) -> dict[str, Any]:
        """{'mentality': 'BALANCED', ..., 'offside_trap': False, 'counter_attack': False}"""
        out: dict[str, Any] = {}
        for name in FIELD_NAMES:
            value = getattr(self, name)
            out[name] = value.value if isinstance(value, Enum) else bool(value)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> TeamInstructions:
        """Hosgorulu: eksik / bilinmeyen / gecersiz anahtar -> o eksenin varsayilani."""
        if not isinstance(data, Mapping):
            return cls()
        kwargs: dict[str, Any] = {}
        for name, parser in _PARSERS.items():
            if name in data and data[name] is not None:
                try:
                    kwargs[name] = parser(data[name])
                except ValueError:
                    pass
        for name in _FLAGS:
            value = data.get(name)
            if isinstance(value, bool):
                kwargs[name] = value
            elif isinstance(value, int) and value in (0, 1):
                kwargs[name] = bool(value)
            elif isinstance(value, str) and value.strip().lower() in _TRUTHY | _FALSY:
                kwargs[name] = value.strip().lower() in _TRUTHY
        return cls(**kwargs)

    def with_changes(self, changes: Mapping[str, Any]) -> TeamInstructions:
        """Kismi degisiklik (anahtarlar alan adlari; degerler enum, deger ya da etiket). Gecersizse ValueError."""
        kwargs = {name: getattr(self, name) for name in FIELD_NAMES}
        kwargs.update(parse_instruction_changes(changes))
        return TeamInstructions(**kwargs)


def _parser(enum_cls: type[Enum], labels: Mapping[Enum, str], what: str):
    def parse(value: Any) -> Any:
        if isinstance(value, enum_cls):
            return value
        if isinstance(value, str):
            for member, label in labels.items():
                if value in (member.value, label):
                    return member
        raise ValueError(f"Bilinmeyen {what}: {value}")
    return parse


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


parse_passing_style = _parser(PassingStyle, PASSING_LABELS, "pas stili")
parse_tempo = _parser(Tempo, TEMPO_LABELS, "tempo")
parse_pressing = _parser(Pressing, PRESSING_LABELS, "pres")
parse_attacking_focus = _parser(AttackingFocus, FOCUS_LABELS, "hücum yönü")

_PARSERS = {
    "mentality": parse_mentality,
    "tackling": parse_tackling,
    "passing_style": parse_passing_style,
    "tempo": parse_tempo,
    "pressing": parse_pressing,
    "attacking_focus": parse_attacking_focus,
}
_FLAGS = ("offside_trap", "counter_attack")
_TRUTHY = frozenset({"true", "1", "yes", "evet", "açık", "acik"})
_FALSY = frozenset({"false", "0", "no", "hayir", "hayır", "kapalı", "kapali"})
FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in fields(TeamInstructions) if f.init)
EXTENDED_FIELDS: tuple[str, ...] = tuple(n for n in FIELD_NAMES if n not in ("mentality", "tackling"))
DEFAULT_INSTRUCTIONS = TeamInstructions()

# Arayuz / plan icin eksen adlarinin Turkce karsiliklari
FIELD_LABELS: dict[str, str] = {
    "mentality": "Zihniyet",
    "tackling": "Sertlik",
    "passing_style": "Pas stili",
    "tempo": "Tempo",
    "pressing": "Pres",
    "attacking_focus": "Hücum yönü",
    "offside_trap": OFFSIDE_TRAP_LABEL,
    "counter_attack": COUNTER_ATTACK_LABEL,
}


def parse_instruction_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Kismi talimat sozlugunu dogrular ve normallestirir (enum / bool). Hata mesajlari Turkce."""
    out: dict[str, Any] = {}
    for name, value in changes.items():
        if name in _PARSERS:
            out[name] = _PARSERS[name](value)
        elif name in _FLAGS:
            if not isinstance(value, bool):
                raise ValueError(f"{FIELD_LABELS[name].capitalize()} açık/kapalı (True/False) olmalı: {value!r}")
            out[name] = value
        else:
            raise ValueError(f"Bilinmeyen talimat alanı: {name}")
    return out


# ===========================================================================
# AI kulupleri icin durum bazli talimat
# ===========================================================================

AI_WEAK_RATIO = 0.90          # ev/deplasman duzeltmeli guc orani bunun altinda: temkinli
AI_UNDERDOG_RATIO = 0.82      # bunun altinda: otobus + kontra
AI_STRONG_RATIO = 1.10        # bunun ustunde: oyunu kur (kisa pas, tum sahada pres)
AI_HOME_EDGE = 0.04           # ev sahibi kendini bu kadar guclu, deplasman bu kadar zayif hisseder


# 14E: AI dizilis degisikligi (EngineConfig.tactics_v2 + ai_tactics). Saf karar tablosu: ai_formation.
AI_CHASE_MINUTE = 75          # bu dakikadan sonra 1 geride: daha hucumcu dizilis
AI_CHASE_MAX_DEFICIT = 1      # bundan fazla geride: dizilise dokunulmaz (talimatla bastirir / mac bitmis)
AI_CHASE_MIN_RATIO = 0.82     # (= AI_UNDERDOG_RATIO) daha zayifsa otobus + kontra dizilisini bozmaz
AI_HOLD_MINUTE = 80           # bu dakikadan sonra 1 onde: 5-3-2
AI_HOLD_MAX_RATIO = 1.10      # (= AI_STRONG_RATIO) daha gucluyse besli savunmaya cekilmez, oyunu kontrol eder
AI_CHASE_FORMATION: tuple[int, int, int] = (4, 3, 3)
AI_HOLD_FORMATION: tuple[int, int, int] = (5, 3, 2)
AI_FORMATION_MAX_CHANGES = 2  # takim basina mac icinde en cok


def ai_formation(team_strength_ratio: float, is_home: bool, score_diff: int, minute: int,
                 current: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
    """
    AI kulubunun mac ici dizilis karari (14E). SAF: ayni girdi -> ayni karar; None = degisiklik yok.

    current  sahadaki gercek dizilis (DEF, MID, FWD); taktik degisikliklerle ilan edilenden ayrilabilir
    75'ten sonra 1 gol geride, 3'ten az forvet: 4-3-3 (4-4-2 / 3-5-2 / 5-3-2 -> 4-3-3); cok zayif taraf
    (oran < AI_CHASE_MIN_RATIO) otobus + kontra dizilisini bozmaz.
    80'den sonra 1 gol onde, 5'ten az defans: 5-3-2; cok guclu taraf (oran > AI_HOLD_MAX_RATIO) gecmez.
    Gerisi None. Esikler maclarin ~%35'inde (85 v 70, iki taraf AI) dizilis degisikligi verecek sekilde secildi
    (brief: 65' / 1-2 gol / 75' -> %79, kabul bandi %20-50).
    """
    shape = tuple(current) if current is not None else None
    if shape is None or len(shape) != 3:
        return None
    ratio = max(0.0, float(team_strength_ratio)) * (1 + AI_HOME_EDGE if is_home else 1 - AI_HOME_EDGE)
    if minute >= AI_CHASE_MINUTE and -AI_CHASE_MAX_DEFICIT <= score_diff <= -1:
        if ratio < AI_CHASE_MIN_RATIO:
            return None                     # otobus + kontra takimi: dizilisini bozmaz, talimatla bastirir
        return AI_CHASE_FORMATION if shape[2] < 3 and shape != AI_CHASE_FORMATION else None
    if minute >= AI_HOLD_MINUTE and score_diff == 1:
        if ratio > AI_HOLD_MAX_RATIO:
            return None                     # guclu taraf oyunu kontrol eder, besli savunmaya cekilmez
        return AI_HOLD_FORMATION if shape[0] < 5 and shape != AI_HOLD_FORMATION else None
    return None


def ai_counter_move(own: TeamInstructions, opponent: TeamInstructions) -> TeamInstructions:
    """
    14E (EngineConfig.tactics_v2): AI rakibin GORUNEN talimatina karsi hamle yapar. SAF; zihniyete dokunmaz, bu
    yuzden iki AI birbirine karsi salinmaz (bir iki adimda durur).

    rakip kapali blok (otobus / kendi yari)  kanattan oyna; dengeliysen onde basip arkani acma; kontra kapat;
                                             otobuse kisa pasla degil ortayla, kendi yarisindaki bloga sabirla
    rakip kontra atakta, sen tum saha pres   presi orta sahaya cek (arkani acma)
    rakip topyekun hucum                     kontra + direkt (topyekun hucumda degilsen)
    rakip tum sahada pres, sen kisa pas      yavas tempo (sabirla presi bosa cikar; topyekun hucumda degilsen)
    """
    changes: dict[str, Any] = {}
    blocked = opponent.concede_quality < 1.0 and own.mentality is not Mentality.PARK_THE_BUS
    if blocked:
        changes["attacking_focus"] = AttackingFocus.FLANKS
        if own.mentality is Mentality.BALANCED:
            if own.pressing is Pressing.ALL_OVER:
                changes["pressing"] = Pressing.MIDFIELD
            if own.passing_style is PassingStyle.SHORT:
                if opponent.mentality is Mentality.PARK_THE_BUS:
                    changes["passing_style"] = PassingStyle.MIXED
                else:
                    changes["tempo"] = Tempo.SLOW
        if own.counter_attack:
            changes["counter_attack"] = False
    if opponent.counter_attack and own.pressing is Pressing.ALL_OVER:
        changes["pressing"] = Pressing.MIDFIELD         # kontraciya karsi onde basip arkani acma
    if not blocked and opponent.mentality is Mentality.ALL_OUT_ATTACK and own.mentality is not Mentality.ALL_OUT_ATTACK:
        changes["counter_attack"] = True
        changes["passing_style"] = PassingStyle.DIRECT
    if (opponent.pressing is Pressing.ALL_OVER and own.mentality is not Mentality.ALL_OUT_ATTACK
            and (changes.get("passing_style") or own.passing_style) is PassingStyle.SHORT):
        changes["tempo"] = Tempo.SLOW
    return own.with_changes(changes) if changes else own


def ai_instructions(team_strength_ratio: float, is_home: bool, score_diff: int, minute: int) -> TeamInstructions:
    """
    AI kulubunun o anki talimati. SAF: rastgelelik yok, ayni girdi -> ayni talimat.

    team_strength_ratio  kendi gucu / rakip gucu (1.0 esit; motor sahadakilerin efektif gucunden hesaplar)
    is_home              ev sahibi mi (ev sahibi biraz daha cesur, deplasman biraz daha temkinli)
    score_diff           kendi gol - rakip gol (eleme macinda toplam skor)
    minute               mac dakikasi (0 = baslama oncesi)

    Mantik: once guce gore temel plan (zayif deplasman -> otobus + kontra; guclu -> kisa pas + pres),
    sonra skor ve dakikaya gore ayar: 60'tan sonra gerideyken tempo ve pres artar, 70'ten sonra
    (fark 2 ya da daha azsa) topyekun hucum; 3+ gol geride mac bitmis sayilir (enerji korunur).
    75'ten sonra 1 farkla onde: otobus + yavas tempo; 2+ farkla onde: dengeli, yavas tempo.
    Beraberlikte 80'den sonra guclu taraf hucuma kalkar.
    """
    ratio = max(0.0, float(team_strength_ratio)) * (1 + AI_HOME_EDGE if is_home else 1 - AI_HOME_EDGE)
    if ratio < AI_UNDERDOG_RATIO:
        base = TeamInstructions(Mentality.PARK_THE_BUS, passing_style=PassingStyle.DIRECT,
                                pressing=Pressing.OWN_HALF, counter_attack=True)
    elif ratio < AI_WEAK_RATIO:
        base = TeamInstructions(pressing=Pressing.OWN_HALF, counter_attack=True)
    elif ratio > AI_STRONG_RATIO:
        base = TeamInstructions(passing_style=PassingStyle.SHORT, pressing=Pressing.ALL_OVER)
    else:
        base = TeamInstructions()

    if score_diff < 0:
        deficit = -score_diff
        if deficit >= 3 and minute >= 60:
            return TeamInstructions(tempo=Tempo.SLOW, pressing=Pressing.OWN_HALF)       # mac bitti: enerji koru
        if minute >= 70:
            return TeamInstructions(Mentality.ALL_OUT_ATTACK, passing_style=PassingStyle.DIRECT, tempo=Tempo.FAST,
                                    pressing=Pressing.ALL_OVER, attacking_focus=AttackingFocus.FLANKS)
        if minute >= 60:
            return base.with_changes({"mentality": Mentality.BALANCED if base.mentality is Mentality.PARK_THE_BUS
                                      else base.mentality, "tempo": Tempo.FAST, "pressing": Pressing.ALL_OVER,
                                      "counter_attack": False})
        return base
    if score_diff > 0:
        if minute >= 75:
            if score_diff == 1:
                return TeamInstructions(Mentality.PARK_THE_BUS, passing_style=PassingStyle.SHORT, tempo=Tempo.SLOW,
                                        pressing=Pressing.OWN_HALF, counter_attack=True)
            return TeamInstructions(passing_style=PassingStyle.SHORT, tempo=Tempo.SLOW)
        if minute >= 60 and score_diff >= 2:
            return base.with_changes({"tempo": Tempo.SLOW, "pressing": Pressing.MIDFIELD
                                      if base.pressing is Pressing.ALL_OVER else base.pressing})
        return base
    if minute >= 80 and ratio >= 1.0:
        return base.with_changes({"mentality": Mentality.ALL_OUT_ATTACK, "tempo": Tempo.FAST,
                                  "counter_attack": False})
    return base
