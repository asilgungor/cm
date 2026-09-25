"""
attribute_model.py
==================
14B "Ozellikler motorda": CM 01/02 sayfasinin (cm_attributes, 31 ozellik 1-20) ve gizli sakatlik egiliminin mac
motorunda OKUNMASI. SAF MANTIK: veritabani, ORM, Streamlit BILMEZ; rastgele sayi CEKMEZ (yalnizca mevcut cekilislerin
olasiliklari ve agirliklari degisir). match_engine yalnizca EngineConfig.attribute_model acikken (14B §3.7'den beri
VARSAYILAN) cagirir; False iken motor 13B ile BIT-BIT aynidir (kanit: .claude/phase14/kanit/14B_evidence.txt).

Model (CM 01/02 dersi: az sayida baskin ozellik, dirsekli doyum, mevki agirligi; hatalari kopyalanmaz)
------------------------------------------------------------------------------------------------------
    * Butce notrlugu: her karar noktasinda (kanal) oyuncunun carpani
          f = 1 + sum_i w_i * (elbowed(s_i) - elbowed(e_i)) / elbowed(e_i)        kanal araligina kirpilir
      s = oyuncunun sayfasi, e = expected_sheet(oyuncu): rol ailesi + seviye icin TIPIK sayfa (tarz, sapma, yas
      egimi yok; motorla eslesen gruplar ayni motor degerine oturtulmus). Sayfasi tipik olan oyuncunun her carpani
      TAM 1.0'dir: takim gucu kalibrasyonu bozulmaz. Tarz sahibi (fantezi kanat, hedef santrfor ...) ve FM oyuncusu
      kendi farkini tasir; bireysellik buradan dogar.
    * Grup ici ayrisma: ayni motor grubundaki ozelliklerin grup ortalamasi motor degerine sabitli (cm_attributes),
      sapmalari toplamda ~0'dir; farkli anlarda okununca biri arti, oteki eksi olur (bitiricilik yakin sansta, uzaktan
      sut uzak sansta; elle kontrol her isabetli sutta, refleks yakin sansta; top kapma / markaj / pozisyon alma ayri
      anlarda).
    * Dirsek: 1-20 olceginde elbow (15) ustu yarim egim. Doyumsuz ozellik YOK ("super kaleci" yok); kanal araliklari
      tek bir ozelligin tasmasini engeller (CM'deki bayt tasmasi / negatif donen bitiricilik hatalari yok).
    * Sut tipi: sans netligi q = yogunluk x cekilen savunmacinin kalitesi (sonuc yolunda ZATEN hesaplanan saf deger;
      anlatim RNG'sine bagli degil). Uzak pay = rol tabani + (1 - q) egimi; net pay = q'nun big_from ustu.
    * Hacim dogrudan: topsuz oyun / yaraticilik (chance) ve kararlilik (comeback, yalniz bastirirken) pozisyon
      OLASILIGINI carpar. Guc uzerinden gitseydi 13A'nin yogunluk-kalite takasi (ustun takim cok ama kotu sans
      uretir) etkiyi tersine cevirirdi: ustun ev sahibinde fazla hucum gucu golu AZALTIR (olculdu).
    * Dayaniklilik MUTLAK okunur (MatchPlayer.stamina -> fitness.stamina_decay_multiplier, FM oyuncusunda oldugu
      gibi); tipik sayfanin dayanikliligi seviyeyle arttigi icin yorulma kanali tipik oyuncuyu tam 1.0'a normalize
      eder (aksi halde 80'lik tipik oyuncu ~%10 yavas yorulur, subs / donusum bantlari kayardi -- olculdu).
    * Takim olcekli kanallar rol agirlikli ORTALAMA ile toplanir (en iyi-k secimi yok: secim yanliligi butceyi bozar).
    * PlayerFactors salt okunur ve lru onbellekte paylasilir (mac hazirligi hizi; bkz. kanit/14B_hiz.txt).
    * Baskin hucumcu (AttributeModelConfig.shooter_sharpness_extra = 4.5): _pick_shooter'in guc keskinligi 5.0 -> 9.5.
      Takim ICINDE normalize: takim gucu degismez, sans takimin en iyi hucumcusunda toplanir. Bu olmadan frikigi artik
      duran top uzmani (AM) attigi icin sezonun gol krali 20.3 -> 17.3'e dusuyordu (kapi bandi 18-34). MUTLAK topsuz
      oyun terimi de denendi (agirlik x elbowed(topsuz oyun) ** k): forvetlerin gol payini %60-64'e cikardigi icin
      (gercek ~%50) secilmedi. Olcumler: .claude/phase14/notlar/14B_teslim.md.

Tek dogru kaynak: READERS (ozellik -> okundugu kanallar, supurme olcutu, yon, esik). CHANNELS kanallarin motorda
nerede carpildigini ve araliklarini tanimlar. Kanal disi okumalar (team_roles yardimcilari MatchPlayer.attributes
uzerinden, fitness MatchPlayer.stamina uzerinden) Reader.indirect'te yazilidir.

K12: sayfa, carpanlar ve esikler HICBIR olay metnine, olay meta verisine ya da arayuz alanina girmez.
"""

from __future__ import annotations

import math
import random
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from operator import attrgetter
from typing import Any

import cm_attributes
import fitness
import transfer_rules
from models import Position

ATTRIBUTE_KEYS = cm_attributes.ATTRIBUTE_KEYS
INJURY_TRAIT = "injury_proneness"          # gizli (sayfada yok): transfer_rules.hidden_trait("injury", ...)
_ROLES = (Position.GK, Position.DEF, Position.MID, Position.FWD)


# ===========================================================================
# 1) AYAR
# ===========================================================================

@dataclass(frozen=True)
class AttributeModelConfig:
    elbow: float = 15.0                     # 1-20: dirsek (ustu yarim egim)
    elbow_slope: float = 0.5
    factor_range: tuple[float, float] = (0.85, 1.15)   # kanal kendi araligini vermezse
    min_expected: float = 3.0               # goreli sapmanin paydasi bundan kucuk olmaz (kalecinin topsuz oyunu ~1)
    # sut tipi: uzak pay = rol tabani + far_slope * (1 - q); net pay = (q - big_from) / big_span
    far_base: tuple[tuple[Position, float], ...] = (
        (Position.FWD, 0.25), (Position.MID, 0.55), (Position.DEF, 0.45), (Position.GK, 0.45))
    far_slope: float = 1.0
    big_from: float = 1.05
    big_span: float = 0.25
    # gizli sakatlik egilimi (1-20): kurban agirligi 1 + injury_slope * (egilim - injury_mean)
    injury_slope: float = 0.05
    injury_mean: float = 10.5
    # kanal araliklari (CHANNELS varsayilanlarinin ustune)
    ranges: tuple[tuple[str, tuple[float, float]], ...] = ()
    # Baskin hucumcu (14B ayari): _pick_shooter'in guc keskinligine (EngineConfig.shooter_sharpness, 5.0) eklenen us.
    # Agirlik takim ICINDE normalize edilir: takim gucunu degistirmez, sansi takimin en iyi hucumcusunda toplar
    # (CM 01/02: baskin forvet takim sutlarinin daha buyuk payini alir). Bkz. kanit/14B_dagilim_acik.txt.
    shooter_sharpness_extra: float = 4.5

    def range_of(self, channel: str) -> tuple[float, float]:
        return _ranges(self)[channel]


@lru_cache(maxsize=8)
def _ranges(cfg: AttributeModelConfig) -> dict[str, tuple[float, float]]:
    """Kanal -> (alt, ust): cfg.ranges > CHANNELS[kanal].range > cfg.factor_range (ayar basina bir kez)."""
    out = {name: ch.range if ch.range is not None else cfg.factor_range for name, ch in CHANNELS.items()}
    out.update(dict(cfg.ranges))
    return out


@dataclass(frozen=True)
class Channel:
    where: str                              # motorda carpildigi yer
    range: tuple[float, float] | None       # None: AttributeModelConfig.factor_range
    team: str | None = None                 # takim olcekli: "mean" / "outfield" / "attack" / "aerial"


CHANNELS: dict[str, Channel] = {
    "attack": Channel("_player_strength('attack') tabani: hucum gucu (takim hucumu + sutor secimi)", None),
    "midfield": Channel("_player_strength('midfield') tabani: topla oynama", None),
    "defense": Channel("_player_strength('defense') tabani: takim savunmasi + zayif halka (_raw_defense)", None),
    "shooter": Channel("_pick_shooter agirligi: sansin kime dustugu (+ baskin hucumcu keskinligi, "
                       "AttributeModelConfig.shooter_sharpness_extra)", (0.70, 1.25)),
    "finish_near": Channel("_goal_probability bitiricilik: yakin sans payi (1 - uzak)", (0.75, 1.20)),
    "finish_far": Channel("_goal_probability bitiricilik: uzak sans payi", (0.75, 1.20)),
    "finish_big": Channel("_goal_probability bitiricilik: net sans payi", (0.75, 1.20)),
    "accuracy": Channel("_attack isabet olasiligi (sutor)", None),
    "free_kick": Channel("_set_piece frikik: vurusun kalitesi (atici gucu)", None),
    "delivery": Channel("_set_piece korner: ortanin kalitesi (kafa vuranin gucu)", None),
    "assist": Channel("_goal asist cekilisi agirligi", (0.70, 1.35)),
    "contest": Channel("_contest_weight: suta karsi cekilme (hedeflenme)", (0.70, 1.35)),
    "marking": Channel("_defensive_resistance: cekilen savunmacinin sans netligine etkisi", (0.80, 1.20)),
    "card": Channel("MatchPlayer.aggression: kart egilimi (kurban agirligi + takim kart orani)", (0.40, 1.80)),
    "injury": Channel("_injury_check kurban agirligi", (0.40, 1.90)),
    "fatigue": Channel("MatchPlayer.decay_multiplier: yorulma hizi", (0.90, 1.12)),
    "keeper": Channel("_goal_probability / _set_piece_goal_probability kaleci gucu (her isabetli sut)", (0.75, 1.20)),
    "keeper_near": Channel("... kaleci gucu, yakin sans payi", (0.75, 1.20)),
    "captain": Channel("kaptan etkileri (kart, geride kalinca savunma, penalti sogukkanliligi) olcegi", (0.25, 1.75)),
    "cohesion": Channel("_team_strength: tum turler (sahadakilerin ortalamasi)", (0.97, 1.03), "mean"),
    "press": Channel("_team_strength('midfield'): rakibin orta sahasi / pres (saha oyunculari ortalamasi)",
                     (0.90, 1.10), "outfield"),
    "chance": Channel("_chance_probability pozisyon hacmi (hucuma katilanlarin rol agirlikli ortalamasi)",
                      (0.93, 1.07), "attack"),
    "comeback": Channel("_chance_probability: geride iken son bolumde (bastirirken) pozisyon hacmi", (0.85, 1.15),
                        "mean"),
    "aerial": Channel("_set_piece korner: savunmanin hava hakimiyeti (kafa gucunu boler)", (0.85, 1.15), "aerial"),
}

# Takim olcekli kanallarda oyuncularin agirligi (rol)
_TEAM_WEIGHTS: dict[str, dict[Position, float]] = {
    "mean": {Position.GK: 1.0, Position.DEF: 1.0, Position.MID: 1.0, Position.FWD: 1.0},
    "outfield": {Position.GK: 0.0, Position.DEF: 1.0, Position.MID: 1.0, Position.FWD: 1.0},
    "attack": {Position.GK: 0.0, Position.DEF: 0.2, Position.MID: 0.8, Position.FWD: 1.0},
    "aerial": {Position.GK: 0.0, Position.DEF: 1.0, Position.MID: 0.4, Position.FWD: 0.3},
}


# ===========================================================================
# 2) OKUYUCU TABLOSU (tek dogru kaynak)
# ===========================================================================

Weight = float | Mapping[Position, float]


@dataclass(frozen=True)
class Read:
    channel: str
    weight: Weight                          # float: dort mevki; Mapping: mevki basina (eksik mevki 0)


@dataclass(frozen=True)
class Reader:
    reads: tuple[Read, ...]
    effect: str                             # oyundaki anlami (Turkce)
    metric: str                             # supurme olcutu (tests.engine_stats.SWEEP_METRICS)
    direction: int                          # +1: ozellik artinca olcut artar, -1: azalir
    threshold: float                        # kanit esigi: goreli (0.15 = %15) ya da absolute ise mutlak
    subject: Position = Position.FWD        # supurmede test oyuncusunun mevkii
    squad: bool = False                     # True: 11 oyuncunun hepsi (takim olcekli ozellik)
    setup: str = ""                         # supurme kurulumu: "captain" / "set_pieces"
    absolute: bool = False
    indirect: tuple[str, ...] = ()          # kanal disi okumalar (team_roles / fitness)


_FWD, _MID, _DEF, _GK = Position.FWD, Position.MID, Position.DEF, Position.GK
_OUT = {_DEF: 1.0, _MID: 1.0, _FWD: 1.0}


def _r(channel: str, weight: Weight) -> Read:
    return Read(channel, weight)


READERS: dict[str, Reader] = {
    # --- hucum -------------------------------------------------------------------------------------------------
    "off_the_ball": Reader(
        (_r("shooter", {_FWD: 0.65, _MID: 0.65, _DEF: 0.45}), _r("chance", 0.10)),
        "Sansin kime dustugu (CM'nin en baskin ozelligi); hucumcularin topsuz oyunu pozisyon hacmini de biraz artirir",
        "shot_share", +1, 0.15),
    "finishing": Reader(
        (_r("finish_near", {_FWD: 0.80, _MID: 0.65, _DEF: 0.45}),),
        "Yakin / iyi sansta gol donusumu", "conversion", +1, 0.12),
    "long_shots": Reader(
        (_r("finish_far", {_FWD: 0.60, _MID: 0.60, _DEF: 0.55}), _r("shooter", {_MID: 0.12, _DEF: 0.10})),
        "Uzak sanstan gol; orta saha / defansin sut payi", "far_conversion", +1, 0.15, subject=_MID,
        indirect=("team_roles.free_kick_skill (long_shots)",)),
    "technique": Reader(
        (_r("accuracy", 0.25), _r("midfield", 0.05), _r("free_kick", 0.08)),
        "Isabet, top kontrolu", "sot_share", +1, 0.05,
        indirect=("team_roles.technique_skill / free_kick_skill (technique)",)),
    "dribbling": Reader(
        (_r("attack", 0.10),),
        "Adam eksiltme: hucum gucu (takim hucumu, sutor secimi)", "shot_share", +1, 0.05),
    "flair": Reader(
        (_r("finish_big", {_FWD: 0.20, _MID: 0.20, _DEF: 0.10}), _r("assist", 0.12)),
        "Buyuk ani bitirme (yalniz net sansta) ve yaratma (asist)", "assists", +1, 0.05, subject=_MID),
    "balance": Reader(
        (_r("attack", 0.05),),
        "Ikili mucadelede ayakta kalma: hucum gucu", "shot_share", +1, 0.05),
    "pace": Reader(
        (_r("attack", 0.07),),
        "Hucum gucu; ofsayt tuzagi / kontrada hiz farki (team_roles.pace_skill motor hizini okur)",
        "shot_share", +1, 0.05),
    "acceleration": Reader(
        (_r("attack", 0.04),),
        "Ilk adim: hucum gucu; kontranin keskinligi (pace_skill: ofsayt tuzagi / kontra)", "shot_share", +1, 0.05,
        indirect=("team_roles.pace_skill (acceleration) -> _pace_edge / _offside_trap_factor",)),
    "crossing": Reader(
        (_r("assist", 0.15), _r("delivery", 0.15)),
        "Orta: asist; korner ortasinin kalitesi; korner aticisi ve kanat hucum odagi (team_roles)", "assists", +1, 0.05, subject=_MID,
        indirect=("team_roles.crossing_skill / corner_skill -> korner aticisi, korner kalitesi, kanat odagi",)),
    "heading": Reader(
        (_r("aerial", 0.35),),
        "Hava topu: korner kafasi (secim + guc); korner savunmasi", "header_goals", +1, 0.05, subject=_DEF,
        indirect=("team_roles.aerial_skill (heading) -> korner kafasi secimi ve gucu",)),
    "jumping": Reader(
        (_r("aerial", 0.25),),
        "Hava topu (ziplama): korner kafasi; korner savunmasi", "header_goals", +1, 0.05, subject=_DEF,
        indirect=("team_roles.aerial_skill (jumping_reach)",)),
    "set_pieces": Reader(
        (_r("free_kick", 0.35), _r("delivery", 0.30)),
        "Duran top kalitesi: frikik vurusu ve korner ortasi; atici secimi (team_roles)", "set_piece_goals", +1, 0.05,
        subject=_MID, setup="set_pieces",
        indirect=("team_roles.free_kick_skill (free_kicks) / corner_skill (corners)",)),
    # --- orta saha ve takim ------------------------------------------------------------------------------------
    "passing": Reader(
        (_r("midfield", 0.12), _r("assist", 0.35)),
        "Topla oynama (orta saha gucu) ve asist", "assists", +1, 0.05, subject=_MID,
        indirect=("team_roles.technique_skill (passing)",)),
    "creativity": Reader(
        (_r("assist", 0.35), _r("chance", 0.06)),
        "Sans yaratma: asist; yaraticilarin pozisyon hacmine katkisi", "assists", +1, 0.05, subject=_MID),
    "decisions": Reader(
        (_r("accuracy", 0.15), _r("contest", {_DEF: -0.10, _MID: -0.10, _FWD: -0.05})),
        "Sut secimi (isabet); savunmada dogru cikis (hedeflenme)", "sot_share", +1, 0.05),
    "work_rate": Reader(
        (_r("press", 0.20), _r("fatigue", 0.12)),
        "Pres: rakibin orta sahasi (saha oyunculari ortalamasi); bedeli daha hizli yorulma", "possession", +1, 0.05,
        subject=_MID, squad=True),
    "teamwork": Reader(
        (_r("cohesion", 0.15),),
        "Takim uyumu: tum takim gucu, dar aralik (+-%3)", "points", +1, 0.05, subject=_MID, squad=True,
        absolute=True),
    "stamina": Reader(
        (),
        "Yorulma hizi (fitness.stamina_decay_multiplier, MatchPlayer.stamina)", "energy75", +1, 5.0, subject=_MID,
        absolute=True, indirect=("fitness.stamina_decay_multiplier (MatchPlayer.stamina)",)),
    "determination": Reader(
        (_r("comeback", 0.30),),
        "Geri donus: geride iken son bolumde (bastirirken) pozisyon hacmi (sahadakilerin ortalamasi)",
        "trailing_goals", +1, 0.05, subject=_MID, squad=True),
    "influence": Reader(
        (_r("captain", 1.2),),
        "Liderlik: kaptanin etkileri (kart riski, geride kalinca panik, penalti sogukkanliligi) olcegi",
        "team_cards", -1, 0.05, subject=_DEF, setup="captain",
        indirect=("team_roles.captaincy_skill (leadership) -> kaptan onerisi",)),
    # --- savunma ve kaleci -------------------------------------------------------------------------------------
    "anticipation": Reader(
        (_r("defense", 0.12), _r("contest", {_DEF: -0.20, _MID: -0.20, _FWD: -0.10})),
        "Savunma gucu; onsezisi iyi savunmaci daha az hedeflenir", "contest_share", -1, 0.05, subject=_DEF),
    "positioning": Reader(
        (_r("contest", {_DEF: -0.35, _MID: -0.35, _FWD: -0.20}), _r("keeper", {_GK: 0.06})),
        "Zayif halka: suta karsi cekilme payi; kalecide kucuk", "contest_share", -1, 0.10, subject=_DEF),
    "marking": Reader(
        (_r("marking", 0.40),),
        "Cekildiginde sansin netligi", "contest_conversion", -1, 0.08, subject=_DEF),
    "tackling": Reader(
        (_r("defense", 0.15), _r("card", {_DEF: -0.30, _MID: -0.30, _FWD: -0.20})),
        "Savunma gucu; temiz mudahale (kart agirligi dusuk)", "player_cards", -1, 0.05, subject=_DEF),
    "strength": Reader(
        (_r("defense", 0.18), _r("contest", {_DEF: -0.10, _MID: -0.08}), _r("aerial", 0.30)),
        "Ikili mucadele (savunma gucu; fiziksel zayif savunmaci daha cok hedeflenir); korner savunmasinda hava",
        "contest_share", -1, 0.05, subject=_DEF),
    "bravery": Reader(
        (_r("aerial", 0.15), _r("injury", 0.30)),
        "Korner savunmasinda hava; sakatlik riski (+)", "injuries", +1, 0.05, subject=_DEF),
    "aggression": Reader(
        (_r("card", 0.60),),
        "Kart egilimi (mevki x saldirganlik x moral)", "player_cards", +1, 0.30, subject=_DEF),
    "handling": Reader(
        (_r("keeper", {_GK: 0.35}),),
        "Isabetli sut -> gol: kaleci gucu", "conceded", -1, 0.08, subject=_GK),
    "reflexes": Reader(
        (_r("keeper", {_GK: 0.15}), _r("keeper_near", {_GK: 0.25})),
        "Kaleci gucu, yakin / net sansta agirlik artar", "near_conceded", -1, 0.08, subject=_GK),
    "agility": Reader(
        (_r("keeper", {_GK: 0.06}), _r("attack", 0.04)),
        "Kaleci gucu (kucuk) ve hucum gucu (kucuk)", "shot_share", +1, 0.05),
    # --- gizli ---------------------------------------------------------------------------------------------------
    INJURY_TRAIT: Reader(
        (),
        "Gizli sakatlik egilimi: _injury_check kurban agirligi (transfer_rules.hidden_trait)", "injuries", +1, 0.40,
        subject=_DEF, indirect=("transfer_rules.hidden_trait('injury', id, fm injury_proneness)",)),
}

ENGINE_READ_KEYS: frozenset[str] = frozenset(READERS) & frozenset(ATTRIBUTE_KEYS)   # bayrak acikken: 31'in 31'i


def unread_keys(attribute_model: bool) -> frozenset[str]:
    """Uretilmis oyuncuda oyunun OKUMADIGI ozellikler: bayrak acikken (varsayilan) bos, kapaliyken (13B motoru)
    cm_attributes.LEGACY_UNREAD_FOR_GENERATED_KEYS (17)."""
    if attribute_model:
        return frozenset(ATTRIBUTE_KEYS) - ENGINE_READ_KEYS
    return cm_attributes.LEGACY_UNREAD_FOR_GENERATED_KEYS


# ===========================================================================
# 3) DIRSEK, SAPMA, CARPAN
# ===========================================================================

def clip(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def elbowed(v: float, cfg: AttributeModelConfig) -> float:
    """1-20 degerin etkin karsiligi: dirsege kadar dogrusal, ustunde elbow_slope egimi (monoton, doyumlu)."""
    return v if v <= cfg.elbow else cfg.elbow + cfg.elbow_slope * (v - cfg.elbow)


def _relative(s: float, e: float, cfg: AttributeModelConfig) -> float:
    base = elbowed(max(float(e), cfg.min_expected), cfg)
    return (elbowed(float(s), cfg) - elbowed(float(e), cfg)) / base


def deviation(sheet: Mapping[str, float], expected: Mapping[str, float],
              keys_weights: Iterable[tuple[str, float]], cfg: AttributeModelConfig) -> float:
    """Agirlikli goreli sapma (dirsekli): sum w * (elbowed(s) - elbowed(e)) / elbowed(e)."""
    total = 0.0
    for key, weight in keys_weights:
        if weight:
            total += weight * _relative(sheet[key], expected[key], cfg)
    return total


def factor(sheet: Mapping[str, float], expected: Mapping[str, float], keys_weights: Iterable[tuple[str, float]],
           cfg: AttributeModelConfig, bounds: tuple[float, float] | None = None) -> float:
    """1 + deviation, bounds (varsayilan cfg.factor_range) icine kirpilmis."""
    lo, hi = bounds if bounds is not None else cfg.factor_range
    return clip(1.0 + deviation(sheet, expected, keys_weights, cfg), lo, hi)


def expected_sheet(player: Any) -> dict[str, int]:
    """Rol + seviye icin tipik (sapmasiz) sayfa: cm_attributes.expected_attributes."""
    return cm_attributes.expected_attributes(player)


def _weight_for(weight: Weight, role: Position) -> float:
    if isinstance(weight, Mapping):
        return float(weight.get(role, 0.0))
    return float(weight)


@lru_cache(maxsize=8)
def _plan(role: Position) -> tuple[tuple[str, tuple[tuple[str, float], ...]], ...]:
    """Mevki -> ((kanal, ((ozellik, agirlik), ...)), ...): yalniz sifir olmayan agirliklar."""
    by_channel: dict[str, list[tuple[str, float]]] = {name: [] for name in CHANNELS}
    for key, reader in READERS.items():
        if key not in ATTRIBUTE_KEYS:
            continue
        for read in reader.reads:
            w = _weight_for(read.weight, role)
            if w:
                by_channel[read.channel].append((key, w))
    return tuple((name, tuple(members)) for name, members in by_channel.items())


# ===========================================================================
# 4) OYUNCU VE TAKIM CARPANLARI
# ===========================================================================

class PlayerFactors:
    """Bir oyuncunun mac boyunca sabit kanal degerleri. Oyuncu kanallari kirpilmis CARPAN; harmanlanan kanallar
    (finish_*, keeper*) ve takim olcekli kanallar ham SAPMA (kirpma kullanim aninda). SALT OKUNUR: ayni sayfa + tipik
    sayfa + mevki + egilim icin tek nesne onbellekte paylasilir (player_factors)."""

    __slots__ = ("attack", "midfield", "defense", "shooter", "accuracy", "free_kick", "delivery", "assist", "contest",
                 "marking", "card", "injury", "fatigue", "captain", "finish_near", "finish_far", "finish_big", "keeper",
                 "keeper_near", "cohesion", "press", "chance", "comeback", "aerial")

    def __init__(self, **values: float) -> None:
        unknown = set(values) - set(self.__slots__)
        if unknown:
            raise TypeError(f"bilinmeyen kanal: {sorted(unknown)}")
        for name in self.__slots__:
            object.__setattr__(self, name, float(values.get(name, 0.0 if name in _RAW_CHANNELS else 1.0)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("PlayerFactors salt okunur (onbellekte paylasilir)")

    def __reduce__(self):                           # pickle / deepcopy (canli mac durumu)
        return (_rebuild_factors, (self.as_dict(),))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PlayerFactors) and self.as_dict() == other.as_dict()

    __hash__ = None                                 # type: ignore[assignment]  # sozluk anahtari degil

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in self.__slots__}


def _rebuild_factors(values: dict[str, float]) -> PlayerFactors:
    return PlayerFactors(**values)


_BLENDED = ("finish_near", "finish_far", "finish_big", "keeper", "keeper_near")
_RAW_CHANNELS = _BLENDED + tuple(name for name, ch in CHANNELS.items() if ch.team)


def player_factors(sheet: Mapping[str, float], expected: Mapping[str, float], role: Position, proneness: float,
                   cfg: AttributeModelConfig) -> PlayerFactors:
    """Sayfa + tipik sayfa + mevki (+ gizli sakatlik egilimi) -> kanal degerleri. Sayfa == tipik ve egilim ortalama
    ise her carpan tam 1.0 (butce notrlugu). Onbellekli: ayni girdiye ayni (paylasilan, salt okunur) nesne."""
    return _factors(tuple(sheet[k] for k in ATTRIBUTE_KEYS), tuple(expected[k] for k in ATTRIBUTE_KEYS), role,
                    proneness, cfg)


_KEY_INDEX = {key: i for i, key in enumerate(ATTRIBUTE_KEYS)}


@lru_cache(maxsize=32)
def _compiled(role: Position, cfg: AttributeModelConfig) -> tuple[tuple[str, bool, float, float,
                                                                    tuple[tuple[int, float], ...]], ...]:
    """(mevki, ayar) -> ((kanal, ham mi, alt, ust, ((ozellik indeksi, agirlik), ...)), ...)."""
    out = []
    for channel, members in _plan(role):
        lo, hi = cfg.range_of(channel)
        out.append((channel, channel in _RAW_CHANNELS, lo, hi, tuple((_KEY_INDEX[k], w) for k, w in members)))
    return tuple(out)


@lru_cache(maxsize=65536)
def _factors(sheet: tuple[float, ...], expected: tuple[float, ...], role: Position, proneness: float,
             cfg: AttributeModelConfig) -> PlayerFactors:
    elbow, slope, floor = cfg.elbow, cfg.elbow_slope, cfg.min_expected
    rel = [0.0] * len(sheet)
    for i, (s_value, e_value) in enumerate(zip(sheet, expected, strict=True)):
        if s_value != e_value:                  # _relative ile ayni ifade (satir ici)
            base = max(float(e_value), floor)
            base = base if base <= elbow else elbow + slope * (base - elbow)
            es = s_value if s_value <= elbow else elbow + slope * (s_value - elbow)
            ee = e_value if e_value <= elbow else elbow + slope * (e_value - elbow)
            rel[i] = (es - ee) / base
    values: dict[str, float] = {}
    for channel, raw, lo, hi, members in _compiled(role, cfg):
        total = 0.0
        for i, w in members:
            total += w * rel[i]
        if channel == "injury":
            total += cfg.injury_slope * (proneness - cfg.injury_mean)
        if raw:
            values[channel] = total
        else:
            value = 1.0 + total
            values[channel] = lo if value < lo else hi if value > hi else value
    # Dayaniklilik MatchPlayer.stamina uzerinden MUTLAK okunur (fitness.stamina_decay_multiplier: 10 -> 1.0); tipik
    # sayfanin dayanikliligi seviyeyle artar (80'lik oyuncuda ~15 -> 0.90). Butce notrlugu: tipik oyuncunun yorulma
    # carpani tam 1.0 olsun diye beklenen dayanikliligin carpaniyla bolunur (farki sayfadaki sapma belirler).
    values["fatigue"] /= fitness.stamina_decay_multiplier(float(expected[_KEY_INDEX["stamina"]]))
    return PlayerFactors(**values)


@lru_cache(maxsize=65536)
def _proneness(player_id: Any, fm_value: Any) -> int:
    """Gizli sakatlik egilimi (transfer_rules.hidden_trait; kalici crc32, RNG cekmez) -- onbellekli."""
    return transfer_rules.hidden_trait("injury", player_id, fm_value)


def prepare_player(p: Any, cfg: AttributeModelConfig) -> None:
    """
    Bayrak acikken mac hazirligi (match_engine._prepare_team, reset_strength_cache'ten ONCE):
        sheet       bossa cm_attributes.player_attributes(p) (profil sayfasiyla ayni tohum; testler enjekte edebilir)
        attributes  uretilmis oyuncuda (bos) as_fm_attributes(sheet): team_roles yardimcilari CM degerlerini tek yoldan
                    okur. FM oyuncusunda gercek FM degerleri korunur, eksikleri sayfadan tamamlanir.
        stamina     bossa sayfanin dayanikliligi (fitness.stamina_decay_multiplier devreye girer)
        _am         kanal degerleri (PlayerFactors; tipik sayfa attributes yazilmadan ONCE, tek okumayla)
    """
    if p.sheet:
        sheet = tuple(p.sheet[k] for k in ATTRIBUTE_KEYS)
        expected = tuple(cm_attributes.expected_attributes(p).values())
    else:
        sheet, expected = cm_attributes.sheet_and_expected(p)
        p.sheet = dict(zip(ATTRIBUTE_KEYS, sheet, strict=True))
    fm = p.attributes
    proneness = _proneness(p.id, fm.get(INJURY_TRAIT) if fm else None)
    fm_sheet = cm_attributes.fm_attributes_from_values(sheet)
    p.attributes = {**fm_sheet, **fm} if fm else fm_sheet
    if p.stamina is None:
        p.stamina = float(p.sheet["stamina"])
    p._am = _factors(sheet, expected, p.position, proneness, cfg)


@dataclass(frozen=True)
class TeamFactors:
    cohesion: float = 1.0
    press: float = 1.0
    chance: float = 1.0
    comeback: float = 1.0
    aerial: float = 1.0


_TEAM_CHANNELS = tuple(name for name, ch in CHANNELS.items() if ch.team)
_team_values = attrgetter(*_TEAM_CHANNELS)
_TEAM_ROLE_WEIGHTS = {role: tuple(_TEAM_WEIGHTS[CHANNELS[name].team][role] for name in _TEAM_CHANNELS)
                      for role in _ROLES}


def team_factors(players: Iterable[Any], cfg: AttributeModelConfig) -> TeamFactors:
    """Sahadaki oyunculardan takim olcekli kanallar: rol agirlikli ortalama sapma, kanal araligina kirpilmis.
    Tipik kadroda hepsi tam 1.0 (en iyi-k secimi yok: secim yanliligi butceyi bozardi)."""
    k = len(_TEAM_CHANNELS)
    sums, weights = [0.0] * k, [0.0] * k
    for p in players:
        role_weights = _TEAM_ROLE_WEIGHTS[p.role or p.position]
        values = _team_values(p._am)
        for i in range(k):
            w = role_weights[i]
            if w:
                sums[i] += w * values[i]
                weights[i] += w
    out = {}
    for i, name in enumerate(_TEAM_CHANNELS):
        lo, hi = cfg.range_of(name)
        out[name] = clip(1.0 + (sums[i] / weights[i] if weights[i] else 0.0), lo, hi)
    return TeamFactors(**out)


# ===========================================================================
# 5) SUT ANI: yakin / uzak / net harmanı
# ===========================================================================

@lru_cache(maxsize=8)
def _far_base(cfg: AttributeModelConfig) -> dict[Position, float]:
    return dict(cfg.far_base)


def far_share(role: Position, clear: float, cfg: AttributeModelConfig) -> float:
    """Sutun uzak sans payi: rol tabani + far_slope * (1 - q), [0, 1]. q = yogunluk x savunmaci kalitesi."""
    return clip(_far_base(cfg)[role] + cfg.far_slope * (1.0 - clear), 0.0, 1.0)


def big_share(clear: float, cfg: AttributeModelConfig) -> float:
    """Net (buyuk) sans payi: q big_from ustunde dogrusal, [0, 1]."""
    return clip((clear - cfg.big_from) / cfg.big_span, 0.0, 1.0)


def finish_factor(pf: PlayerFactors, far: float, big: float, cfg: AttributeModelConfig) -> float:
    """Bitiricilik carpani: yakin payda bitiricilik, uzak payda uzaktan sut, net payda fantezi."""
    lo, hi = cfg.range_of("finish_near")
    return clip(1.0 + (1.0 - far) * pf.finish_near + far * pf.finish_far + big * pf.finish_big, lo, hi)


def keeper_factor(pf: PlayerFactors | None, far: float, cfg: AttributeModelConfig) -> float:
    """Kaleci gucu carpani: elle kontrol (+ cevik, pozisyon) her sutta, refleks yakin payda agirlasir."""
    if pf is None:
        return 1.0
    lo, hi = cfg.range_of("keeper")
    return clip(1.0 + pf.keeper + (1.0 - far) * pf.keeper_near, lo, hi)


# ===========================================================================
# 6) 15G: GIZLI OZELLIKLER VE NOT MODELI
# ===========================================================================
# CM 01/02'nin sayfada GORUNMEYEN uc kisiligi (phase13/cm0102.md) ve performansi yansitan not.
# Hepsi EngineConfig.rating_model bayraginin arkasindadir (15G'den beri VARSAYILAN ACIK, YENIDEN
# TEMELLENDIRME 7); bayrak kapaliyken bu bolum HIC cagrilmaz ve motor 14E ile bit-bit aynidir.
#
#   tutarlilik (consistency)      gunun formu: oyuncu basina, KENDI tohumlu crc32 akisindan cekilir
#                                 (mac RNG'sine dokunulmaz). Dusuk tutarlilik = genis sapma.
#   onemli mac (important_matches) eleme / final / derbi maclarinda oyuncunun seviyesi kayar.
#   mizac (temperament)           kart agirligi: sogukkanli oyuncu daha az kart gorur.
#
# K12: uc deger de 1-20'dir, SAYI olarak hicbir olaya, meta veriye ya da arayuze girmez; yalnizca
# mevcut olasilik ve agirliklari carpar. Deger FM verisi varsa ondan, yoksa transfer_rules.hidden_trait
# ile oyuncu kimliginden kalici crc32 ile gelir (her ozellik kendi "kind" akisi: RNG cekmez).
#
# Not modeli (CM dersi: not PERFORMANSI anlatir, sayilari degil):
#   * cekilen savunmaci: _defensive_resistance'in sectigi BELIRLI oyuncu duellosunu hesabina yazar;
#   * kurtarisin netligi: saklanan sans netligi (K6) kurtarisi ve yenilen golu agirliklandirir;
#   * zincir katkisi: atagin kurulusunda yer alan oyuncular pay alir (sut ve gol ayri agirlikta).
# Netlik (clarity) olcegi _quality_tag ile aynidir: ~0.66 umut sutu, ~0.92-1.18 iyi, >=1.18 net sans.

CONSISTENCY_TRAIT = "consistency"                # FM/CM 'Consistency'   (gizli)
BIG_MATCH_TRAIT = "important_matches"            # FM/CM 'Important Matches' (gizli)
TEMPERAMENT_TRAIT = "temperament"                # FM/CM 'Temperament'   (gizli)
HIDDEN_TRAITS = (CONSISTENCY_TRAIT, BIG_MATCH_TRAIT, TEMPERAMENT_TRAIT)
# transfer_rules.hidden_trait "kind" anahtarlari: her ozellik AYRI crc32 akisi (kind|player_id)
TRAIT_KIND = {CONSISTENCY_TRAIT: "consistency", BIG_MATCH_TRAIT: "big_match",
              TEMPERAMENT_TRAIT: "temperament"}
TRAIT_NEUTRAL = 10.5                             # 1-20 olceginin ortasi
TRAIT_SPAN = 9.5                                 # notr -> uc

# Macin agirligi (oyunun BILDIGI gercekten turetilir; MatchEngine.occasion ile acikca da verilebilir)
OCCASION_LEAGUE = "league"
OCCASION_KNOCKOUT = "knockout"
OCCASION_FINAL = "final"
OCCASION_DERBY = "derby"
OCCASION_WEIGHT: dict[str, float] = {
    OCCASION_LEAGUE: 0.0,        # sirali lig maci: gizli "onemli mac" ozelligi ETKISIZ (carpan tam 1.0)
    OCCASION_DERBY: 0.7,
    OCCASION_KNOCKOUT: 1.0,
    OCCASION_FINAL: 1.4,         # tarafsiz sahada oynanan eleme maci
}


@dataclass(frozen=True)
class RatingModelConfig:
    """15G not modeli ve gizli ozellik ayarlari. Kalibrasyon: .claude/phase14/notlar/15G_teslim.md."""
    # --- gizli ozellik: tutarlilik (gunun formu) ---
    form_sigma: float = 0.068               # NOTR tutarlilikta (10.5) log-normal sapma: tipik oyuncu ~%6.8
    form_spread: float = 1.52               # tutarliliga duyarlilik: 5 -> ~%12.8, 18 -> tabana (%1.0)
    form_spread_floor: float = 0.15         # en tutarli oyuncuda bile kalan sapma payi
    form_range: tuple[float, float] = (0.70, 1.35)
    form_rating_weight: float = 7.80        # gunun formunun NOTA dogrudan yansimasi (kabul: sd orani >= 1.4)

    # --- gizli ozellik: onemli mac ---
    big_game_slope: float = 0.055           # eleme macinda uc ozellikte +-%5.5; final 1.4 kati
    big_game_range: tuple[float, float] = (0.90, 1.10)

    # --- gizli ozellik: mizac (kart agirligi) ---
    temperament_slope: float = 0.35
    temperament_range: tuple[float, float] = (0.70, 1.45)

    # --- not modeli: taban ve klasik terimler ---
    base: float = 6.45
    goal: float = 1.00
    assist: float = 0.50
    shot_on_target: float = 0.10
    yellow: float = 0.30
    red: float = 1.50
    clean_sheet: float = 0.50               # GK / DEF
    won: float = 0.30
    short_spell_minutes: int = 20           # bu surenin altinda oynayanin sapmasi yariya iner
    short_spell_share: float = 0.50
    range: tuple[float, float] = (1.0, 10.0)

    # --- not modeli: kaleci (kurtarisin netligi; sayi olarak gosterilmez) ---
    save: float = 0.155                     # her kurtaris
    save_clarity: float = 0.34              # + netligin 1.0 ustu payi
    save_clarity_cap: float = 0.40
    concede: float = 0.30                   # yenilen gol
    concede_clarity: float = 0.40           # net sansta kalecinin sucu azalir
    concede_floor: float = 0.06

    # --- not modeli: cekilen savunmacinin duellosu ---
    duel_win: float = 0.052                 # cekildi ve gol olmadi
    duel_win_clarity: float = 0.070         # net sansi kapatmak daha degerli
    duel_loss: float = 0.44                 # cekildi ve gol oldu
    duel_loss_relief: float = 0.26          # cok net sansta tek savunmacinin sucu azalir
    duel_clarity_range: tuple[float, float] = (0.45, 1.80)

    # --- not modeli: zincir katkisi ---
    chain_link: float = 0.095               # sutla biten atagin kurulusunda yer almak
    chain_goal_link: float = 0.26           # gole giden zincirde yer almak
    chain_weight: tuple[tuple[Position, float], ...] = (
        (Position.FWD, 0.30), (Position.MID, 0.45), (Position.DEF, 0.25), (Position.GK, 0.0))



@dataclass(frozen=True)
class HiddenTraits:
    """Oyuncunun uc gizli kisiligi (1-20) ve bunlardan turetilen mac carpanlari. Salt okunur."""
    consistency: int
    big_match: int
    temperament: int
    form_sigma: float                        # tutarliliktan turetilen gunun formu sapmasi
    card: float                              # mizactan turetilen kart agirligi


@lru_cache(maxsize=8)
def chain_weights(cfg: RatingModelConfig) -> dict[Position, float]:
    """Zincir katkisinin rol agirliklari (ayar basina bir kez; motor sicak yolda bu sozluge bakar)."""
    return dict(cfg.chain_weight)


def form_sigma(consistency: float, cfg: RatingModelConfig) -> float:
    """Tutarlilik -> gunun formunun sapmasi. 20 dar, 1 genis; notrde tam cfg.form_sigma."""
    scale = 1.0 + cfg.form_spread * (TRAIT_NEUTRAL - consistency) / TRAIT_SPAN
    return cfg.form_sigma * max(cfg.form_spread_floor, scale)


def temperament_card_factor(temperament: float, cfg: RatingModelConfig) -> float:
    """Mizac -> kart agirligi. Sogukkanli (20) az, cabuk parlayan (1) cok kart gorur."""
    lo, hi = cfg.temperament_range
    return clip(1.0 + cfg.temperament_slope * (TRAIT_NEUTRAL - temperament) / TRAIT_SPAN, lo, hi)


@lru_cache(maxsize=65536)
def _hidden_traits(player_id: Any, consistency_fm: Any, big_fm: Any, temper_fm: Any,
                   cfg: RatingModelConfig) -> HiddenTraits:
    consistency = transfer_rules.hidden_trait(TRAIT_KIND[CONSISTENCY_TRAIT], player_id, consistency_fm)
    big_match = transfer_rules.hidden_trait(TRAIT_KIND[BIG_MATCH_TRAIT], player_id, big_fm)
    temperament = transfer_rules.hidden_trait(TRAIT_KIND[TEMPERAMENT_TRAIT], player_id, temper_fm)
    return HiddenTraits(consistency=consistency, big_match=big_match, temperament=temperament,
                        form_sigma=form_sigma(consistency, cfg),
                        card=temperament_card_factor(temperament, cfg))


def hidden_traits(p: Any, cfg: RatingModelConfig) -> HiddenTraits:
    """Oyuncunun gizli ucluSU: FM verisi varsa o, yoksa kimlikten kalici crc32 (RNG cekmez) -- onbellekli."""
    fm = p.attributes
    if not fm:
        return _hidden_traits(p.id, None, None, None, cfg)
    return _hidden_traits(p.id, fm.get(CONSISTENCY_TRAIT), fm.get(BIG_MATCH_TRAIT),
                          fm.get(TEMPERAMENT_TRAIT), cfg)


def day_form(seed: Any, player_id: Any, sigma: float, cfg: RatingModelConfig) -> float:
    """
    Gunun formu: oyuncu basina bir kez, KENDI tohumlu akisindan (crc32(tohum|kimlik) ile tohumlanmis) cekilir.
    Macin RNG'sine dokunulmaz, sonuc yoluna yeni cekilis girmez ve kadro sirasindan bagimsizdir.
    exp(N(0, sigma) - sigma^2/2): beklenen degeri TAM 1.0, yani takim gucu kalibrasyonu bozulmaz.
    (Iki crc32'den Box-Muller de denendi: crc32 GF(2)'de dogrusal oldugu icin ayni uzunluktaki iki anahtarin
    ciktilari sabit bir XOR ile bagli cikiyor; tohumlanmis MT akisi tercih edildi.)
    """
    if sigma <= 0.0:
        return 1.0
    rng = random.Random(zlib.crc32(f"15G|form|{seed}|{player_id}".encode()))
    lo, hi = cfg.form_range
    return clip(math.exp(rng.normalvariate(0.0, sigma) - 0.5 * sigma * sigma), lo, hi)


def big_game_factor(big_match: float, weight: float, cfg: RatingModelConfig) -> float:
    """Onemli mac ozelligi -> oyuncunun o macki seviyesi. Lig macinda (weight 0) TAM 1.0."""
    if weight <= 0.0:
        return 1.0
    lo, hi = cfg.big_game_range
    return clip(1.0 + cfg.big_game_slope * weight * (big_match - TRAIT_NEUTRAL) / TRAIT_SPAN, lo, hi)


def occasion_weight(occasion: str | None) -> float:
    return OCCASION_WEIGHT.get(occasion or OCCASION_LEAGUE, 0.0)


def save_value(clarity: float, cfg: RatingModelConfig) -> float:
    """Kurtarisin degeri: netligi 1.0'in uzerindeki pay kadar agirlasir (sayi olarak gosterilmez)."""
    extra = clarity - 1.0
    if extra <= 0.0:
        return cfg.save
    return cfg.save + min(cfg.save_clarity_cap, cfg.save_clarity * extra)


def concede_value(clarity: float, cfg: RatingModelConfig) -> float:
    """Yenilen golun kaleciye maliyeti: sans ne kadar netse kalecinin sucu o kadar az."""
    return max(cfg.concede_floor, cfg.concede - cfg.concede_clarity * max(0.0, clarity - 1.0))


def duel_value(clarity: float, conceded: bool, cfg: RatingModelConfig) -> float:
    """Cekilen savunmacinin duello hesabi: kapatilan net sans deger, yenilen gol pahali."""
    lo, hi = cfg.duel_clarity_range
    q = clip(clarity, lo, hi)
    if not conceded:
        return cfg.duel_win + cfg.duel_win_clarity * max(0.0, q - 1.0)
    return -max(0.10, cfg.duel_loss - cfg.duel_loss_relief * max(0.0, q - 1.0))


__all__ = [
    "BIG_MATCH_TRAIT", "CHANNELS", "CONSISTENCY_TRAIT", "ENGINE_READ_KEYS", "HIDDEN_TRAITS", "INJURY_TRAIT",
    "OCCASION_DERBY", "OCCASION_FINAL", "OCCASION_KNOCKOUT", "OCCASION_LEAGUE", "OCCASION_WEIGHT",
    "READERS", "TEMPERAMENT_TRAIT", "TRAIT_KIND", "AttributeModelConfig", "Channel", "HiddenTraits",
    "PlayerFactors", "RatingModelConfig", "Read", "Reader", "TeamFactors", "big_game_factor",
    "big_share", "chain_weights", "clip", "concede_value", "day_form", "deviation", "duel_value",
    "elbowed",
    "expected_sheet", "factor", "far_share", "finish_factor", "form_sigma", "hidden_traits",
    "keeper_factor", "occasion_weight", "player_factors", "prepare_player", "save_value",
    "team_factors", "temperament_card_factor", "unread_keys",
]
