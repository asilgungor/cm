"""
cm_attributes.py
================
Championship Manager 01/02 tarzi oyuncu sayfasi: 31 ozellik (1-20) + tercih edilen ayak.
SAF GORUNUM: veritabani, ORM ya da Streamlit BILMEZ; oyuncuya yalnizca ozellik adlariyla (duck typing) bakar
(ORM Player, seed.PlayerSpec, match_engine.MatchPlayer, SimpleNamespace). Hicbir sey yazmaz, global RNG'ye
dokunmaz, sema degistirmez.

Arayuz
------
    ATTRIBUTE_KEYS                      31 anahtar, snake_case Ingilizce, CM sirasi
    ATTRIBUTE_LABELS                    Turkce etiketler
    TECHNICAL_KEYS / MENTAL_KEYS / PHYSICAL_KEYS / GOALKEEPER_KEYS, ATTRIBUTE_GROUPS, GROUP_LABELS
    CHARACTER_KEYS                      kisilik ozellikleri (kararlilik, liderlik, fantezi, ...)
    player_attributes(player, world_seed=None) -> dict[str, int]     kesin 1-20 sayfa
    preferred_foot(player, world_seed=None) -> str                    "Sağ" / "Sol" / "Her iki ayak"
    attribute_display(value, knowledge_pct, player_key) -> str        "15" / "12-15" / "?"
    player_role(player, world_seed=None) -> str                       turetilmis rol kodu (ROLE_LABELS)
    as_fm_attributes(sheet) -> dict[str, int]                         sayfa -> FM anahtarlari (ratings/team_roles)
    expected_attributes(player) -> dict[str, int]                     rol + seviye icin 'tipik' sayfa (14B, motor ici)
    sheet_and_expected(player) -> (tuple, tuple)                      ikisi tek okumayla (motorun mac hazirligi)
    fm_attributes_from_values(values) -> dict[str, int]               as_fm_attributes, deger demetinden (hizli)
    identity_key(player) -> str                                        f"{player.id}|{player.name}" (tohum kimligi)
    world_seed parametresi geriye uyumluluk icin durur ve YOK SAYILIR (14B §3.1, asagida "Kimlik").

Tek dogru kaynak: motorun alti ozelligi
---------------------------------------
Gelisim, deger ve takim gucu pace, shooting, passing, defending, dribbling, goalkeeping (1-99) ve overall_rating
kullanir; bu modul onlari DEGISTIRMEZ. Sayfa bu degerlerden turetilir (asagidaki okuyucular icin bkz. "Hangi oyun
sistemi hangi ozelligi okuyor?"):

    * FM oyunculari (fm_attributes dolu): gercek FM degerleri aynen gecer (FM adlari bizimkilere eslenir:
      vision -> creativity, leadership -> influence, jumping_reach -> jumping, free_kicks/corners -> set_pieces).
      Disa aktarimda olmayan ozellikler asagidaki turetmeyle doldurulur (bilinenler sabit tutularak).
    * Digerleri (acik veri / sentetik / akademi): motor degerleri + mevki + yas + oyuncuya ozel tohumdan
      deterministik turetilir.

Tutarlilik (testle kilitli, tests/test_cm_attributes.py):
    sayfa -> as_fm_attributes -> ratings.derive_engine_attributes her motor ozelligini ENGINE_TOLERANCE (+-3)
    icinde, ratings.compute_overall da overall_rating'i OVERALL_TOLERANCE (+-2) icinde yeniden uretir.
    1-20 olceginin tabani fm_scale(1) = 24'tur: 24'un altindaki motor degeri (orn. kalecinin sutu, saha
    oyuncusunun kaleciligi -- ikisinin de overall agirligi 0) 1 olarak gosterilir ve 24 sayilir; saha oyuncusu
    kaleciligi ratings.OUTFIELD_GK_CEILING (40) ile sinirlidir.

Turetme (FM birimi = (motor - 20) / 3.95, ratings.fm_scale'in tersi)
    1) Kimlik: sha256(f"{player.id}|{player.name}") ozeti (SHAKE-256 ile genisletilir). Python'un tuzlu hash()'i
       ve global random KULLANILMAZ. 14B (§3.1): dunya tohumu KULLANILMAZ -- motorda (MatchPlayer) dunya tohumu
       yoktur; kullanilsaydi profilde gosterilen sayfa ile macta okunan sayfa ayrisirdi (K12 "cift kayit" dersi).
       Ayni dunyada id benzersizdir, farkli dunyalarda adlar farklidir; kimliksiz nesnede (PlayerSpec) id None.
       Kimlikten (yalnizca kimlikten: gelisimle degismez) rol (stoper / bek / on libero / ... / santrfor),
       kanat (sol/sag), oyun tarzi (orn. kanatta "fantezi" / "ortaci" / "hizli") ve ozellik sapmalari cekilir.
    2) On deger: L = overall seviyesi + rol ofseti + tarz bonusu + yas + sapma (+ ilgili motor ozelliginin
       mevki beklentisinden sapmasi). Yas: hizlanma / ceviklik / dayaniklilik gence, karar / onsezi /
       pozisyon alma / takim oyunu / liderlik tecrubeye kayar.
    3) Motorla eslesen 14 ozellik (ENGINE_BACKED_KEYS) ratings.FM_SOURCES agirliklariyla gruplanir ve her grup
       ortalamasi motor degerine oturtulur (grup ici sekil korunur, 1-20 sinirlarinda kalan pay digerlerine
       dagilir), sonra motor degerini tutturan tam sayi kombinasyonu secilir. overall_rating ile agirlikli
       ortalama arasinda fark varsa (kulup bandi kirpmasi, gelisim) agirlikli ozellikler SHIFT_LIMIT kadar,
       gerekirse ENGINE_TOLERANCE'i asmadan biraz daha kaydirilir. Tam sayi aramasi yalnizca hedefe dogru birer
       adimdir; sonuc yine tolerans disinda kalirsa (nadir; orn. 99 tavanli kaleci) bir "kurtarma turu" uyeleri
       iki yone de birer adim dener (bu turdan gecmeyen sayfalar degismez).
    4) Tipik sayfa (expected_attributes, 14B): ayni rol ailesi ve seviye, tarz / sapma / yas egimi YOK (yas =
       TYPICAL_AGE), ayni motor hedeflerine surekli oturtulup yuvarlanir. Arayuzde gosterilmez; attribute_model
       carpanlari "sayfa - tipik sayfa" sapmasindan hesaplar.
    Hiz: 30 kisilik kadro ilk hesapta birkac ms, sonrasi lru_cache (kimlik, mevki, yas, overall, motor degerleri,
    FM verisi anahtarli) ile ~0.3 ms.

Hangi oyun sistemi hangi ozelligi okuyor? (Faz 14B itibariyla; bayrak cevrildi)
    Mac motoru 31 ozelligin 31'ini ve gizli sakatlik egilimini (transfer_rules.hidden_trait) okur
    (match_engine.EngineConfig.attribute_model, VARSAYILAN ACIK; tek dogru kaynak attribute_model.READERS):
        * MatchEngine._prepare_team her oyuncuya bu modulun sayfasini (player_attributes, profil ile AYNI tohum)
          MatchPlayer.sheet olarak verir; uretilmis oyuncuda as_fm_attributes(sheet) MatchPlayer.attributes'a yazilir
          (team_roles: orta, korner, frikik, hava topu, teknik, hiz, kaptanlik CM degerlerini tek yoldan okur) ve
          dayaniklilik MatchPlayer.stamina olur (fitness.stamina_decay_multiplier).
        * Karar noktalarindaki etki "tipik sayfadan sapma" ile olculur (expected_attributes: tipik sayfada carpan
          tam 1.0): sutor secimi, bitiricilik (yakin / uzak / net sans), isabet, asist, hedeflenme ve markaj, kart ve
          sakatlik kurbani, yorulma, kaleci gucu, duran top; takim olcekli uyum, pres, pozisyon hacmi, geri donus,
          hava savunmasi. Tablonun tamami: attribute_model.READERS (ozellik -> kanal, agirlik, supurme olcutu, esik).
    ENGINE_READ_KEYS = ATTRIBUTE_KEYS (31): motorun okudugu ozellikler (attribute_model.ENGINE_READ_KEYS ile ayni,
        testli). FM_READ_KEYS, DISPLAY_ONLY_KEYS ve UNREAD_FOR_GENERATED_KEYS artik BOS: gosterilen her ozellik
        (uretilmis oyuncu dahil) macta bir sey yapar (K12).
    ENGINE_BACKED_KEYS (14): pace, acceleration, finishing, long_shots, passing, creativity, technique, tackling,
        marking, positioning, dribbling, agility, handling, reflexes -- motorun alti ozelligini (grup ortalamasi)
        besleyen ozellikler. TURETME kumesidir (sayfanin grup ortalamasi motor degerine oturtulur), okuma kumesi degil.
    LEGACY_* (bayrak KAPALI, 13B motoru): LEGACY_FM_READ_KEYS (6: stamina, crossing, heading, jumping, set_pieces,
        influence -- yalnizca FM oyuncularinda, team_roles / fitness yoluyla), LEGACY_DISPLAY_ONLY_KEYS (11: aggression,
        anticipation, balance, bravery, decisions, determination, flair, off_the_ball, strength, teamwork, work_rate --
        hicbir sistem okumaz), LEGACY_UNREAD_FOR_GENERATED_KEYS (17 = ikisinin birlesimi). attribute_model.unread_keys
        (False) bunlari dondurur.

Gorunurluk (attribute_display): bilgi %70+ kesin sayi, %25-69 bilgi arttikca daralan aralik (her zaman gercek
degeri icerir, genislik ve kayma (oyuncu, ozellik) basina sabit: yeniden cizimde titremez), %25 alti "?".
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from functools import lru_cache
from itertools import product
from typing import Any

from models import Position
from ratings import (
    ENGINE_ATTRIBUTES,
    FM_SOURCES,
    OUTFIELD_GK_CEILING,
    POSITION_OFFSETS,
    POSITION_WEIGHTS,
    compute_overall,
    fm_scale,
)

# ===========================================================================
# 1) ANAHTARLAR, ETIKETLER, GRUPLAR
# ===========================================================================

ATTRIBUTE_KEYS: tuple[str, ...] = (
    "acceleration", "aggression", "agility", "anticipation", "balance", "bravery", "creativity", "crossing",
    "decisions", "determination", "dribbling", "finishing", "flair", "handling", "heading", "influence",
    "jumping", "long_shots", "marking", "off_the_ball", "pace", "passing", "positioning", "reflexes",
    "set_pieces", "stamina", "strength", "tackling", "teamwork", "technique", "work_rate",
)

ATTRIBUTE_LABELS: dict[str, str] = {
    "acceleration": "Çabukluk", "aggression": "Agresiflik", "agility": "Çeviklik", "anticipation": "Önsezi",
    "balance": "Denge", "bravery": "Cesaret", "creativity": "Yaratıcılık", "crossing": "Orta",
    "decisions": "Karar alma", "determination": "Kararlılık", "dribbling": "Top sürme",
    "finishing": "Bitiricilik", "flair": "Fantezi", "handling": "Elle kontrol", "heading": "Kafa vuruşu",
    "influence": "Liderlik", "jumping": "Zıplama", "long_shots": "Uzaktan şut", "marking": "Markaj",
    "off_the_ball": "Topsuz oyun", "pace": "Hız", "passing": "Pas", "positioning": "Pozisyon alma",
    "reflexes": "Refleks", "set_pieces": "Duran top", "stamina": "Dayanıklılık", "strength": "Güç",
    "tackling": "Top kapma", "teamwork": "Takım oyunu", "technique": "Teknik", "work_rate": "Çalışkanlık",
}

TECHNICAL_KEYS: tuple[str, ...] = (
    "crossing", "dribbling", "finishing", "heading", "long_shots", "marking", "passing", "set_pieces",
    "tackling", "technique",
)
MENTAL_KEYS: tuple[str, ...] = (
    "aggression", "anticipation", "bravery", "creativity", "decisions", "determination", "flair", "influence",
    "off_the_ball", "positioning", "teamwork", "work_rate",
)
PHYSICAL_KEYS: tuple[str, ...] = ("acceleration", "agility", "balance", "jumping", "pace", "stamina", "strength")
GOALKEEPER_KEYS: tuple[str, ...] = ("handling", "reflexes")
CHARACTER_KEYS: tuple[str, ...] = (
    "determination", "influence", "flair", "aggression", "bravery", "work_rate", "teamwork",
)
GROUP_LABELS: dict[str, str] = {
    "technical": "Teknik", "mental": "Zihinsel", "physical": "Fiziksel", "goalkeeping": "Kalecilik",
}
ATTRIBUTE_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("technical", GROUP_LABELS["technical"], TECHNICAL_KEYS),
    ("mental", GROUP_LABELS["mental"], MENTAL_KEYS),
    ("physical", GROUP_LABELS["physical"], PHYSICAL_KEYS),
    ("goalkeeping", GROUP_LABELS["goalkeeping"], GOALKEEPER_KEYS),
)

FOOT_RIGHT, FOOT_LEFT, FOOT_BOTH = "Sağ", "Sol", "Her iki ayak"

# Turetilmis rol (motor okumaz; tercih edilen ayak ve sayfanin sekli buna gore)
ROLE_LABELS: dict[str, str] = {
    "GK": "Kaleci", "CB": "Stoper", "LB": "Sol bek", "RB": "Sağ bek", "DM": "Defansif orta saha",
    "CM": "Merkez orta saha", "AM": "Ofansif orta saha", "LM": "Sol orta saha", "RM": "Sağ orta saha",
    "LW": "Sol kanat", "RW": "Sağ kanat", "ST": "Santrfor",
}

# FM anahtari -> bizim anahtarimiz (ayni adli olanlar dogrudan eslesir)
FM_TO_CM: dict[str, str] = {"vision": "creativity", "leadership": "influence", "jumping_reach": "jumping"}
# Bizim anahtar -> FM verisinde aranacak adlar (ilk bulunan; set_pieces bulunanlarin ortalamasi)
_FM_SOURCES_OF: dict[str, tuple[str, ...]] = {
    "creativity": ("creativity", "vision"),
    "influence": ("influence", "leadership"),
    "jumping": ("jumping", "jumping_reach"),
    "set_pieces": ("set_pieces", "free_kicks", "corners"),
}

# Motor ozelliklerini (ratings.FM_SOURCES uzerinden) besleyen 14 ozellik (TURETME kumesi; bkz. belge basligi)
ENGINE_BACKED_KEYS: frozenset[str] = frozenset(
    FM_TO_CM.get(fm_key, fm_key)
    for sources in FM_SOURCES.values() for fm_key in sources
    if FM_TO_CM.get(fm_key, fm_key) in ATTRIBUTE_KEYS
)
# 14B (bayrak acik, varsayilan): mac motoru 31 ozelligin 31'ini okur (attribute_model.READERS); okunmayan yok.
ENGINE_READ_KEYS: frozenset[str] = frozenset(ATTRIBUTE_KEYS)
FM_READ_KEYS: frozenset[str] = frozenset()
DISPLAY_ONLY_KEYS: frozenset[str] = frozenset()
UNREAD_FOR_GENERATED_KEYS: frozenset[str] = frozenset()
# 13B motoru (EngineConfig.attribute_model=False): yalnizca FM oyuncularinda okunanlar / hic okunmayanlar
LEGACY_FM_READ_KEYS: frozenset[str] = frozenset(
    {"stamina", "crossing", "heading", "jumping", "set_pieces", "influence"}
)
LEGACY_DISPLAY_ONLY_KEYS: frozenset[str] = frozenset(ATTRIBUTE_KEYS) - ENGINE_BACKED_KEYS - LEGACY_FM_READ_KEYS
LEGACY_UNREAD_FOR_GENERATED_KEYS: frozenset[str] = LEGACY_FM_READ_KEYS | LEGACY_DISPLAY_ONLY_KEYS

ENGINE_TOLERANCE = 3          # sayfadan geri turetilen motor ozelligi, motor degerinden en fazla bu kadar sapar
OVERALL_TOLERANCE = 2         # ... ve onun compute_overall'i overall_rating'den
SHIFT_LIMIT = 2.4             # overall farki icin agirlikli ozelliklere uygulanabilecek en buyuk kaydirma

EXACT_KNOWLEDGE = 70          # bu bilgi yuzdesinden itibaren kesin deger
RANGE_KNOWLEDGE = 25          # altinda "?" (transfer_rules.KNOWN_THRESHOLD ile ayni)
MAX_RANGE_WIDTH = 4           # %25 bilgide aralik 5 degeri kapsar ("11-15")

_N = len(ATTRIBUTE_KEYS)
_IDX: dict[str, int] = {key: i for i, key in enumerate(ATTRIBUTE_KEYS)}
_SCALE_FLOOR = fm_scale(1)    # 24: 1-20 olceginin motordaki tabani
_SCALE_TOP = fm_scale(20)     # 99
_OUTFIELD_GK_HI = 5           # saha oyuncusunun elle kontrol / refleks tavani
_HI_KEEPER = (20,) * _N
_HI_OUTFIELD = tuple(_OUTFIELD_GK_HI if key in ("handling", "reflexes") else 20 for key in ATTRIBUTE_KEYS)
_NOT_FIXED = (False,) * _N

# Motor ozelligi -> ((bizim indeks, agirlik), ...): derive_engine_attributes'in yeniden normalize ettigi uyeler
_GROUPS: tuple[tuple[str, tuple[tuple[int, float], ...]], ...] = tuple(
    (attr, tuple((_IDX[FM_TO_CM.get(k, k)], w) for k, w in FM_SOURCES[attr].items()
                 if FM_TO_CM.get(k, k) in _IDX))
    for attr in ENGINE_ATTRIBUTES
)


def _naive_sum(values) -> float:
    """ratings._weighted_fm gibi soldan saga toplama (sum() 3.12+ telafili toplar; son bit farki yuvarlamayi
    degistirebilirdi)."""
    total = 0.0
    for value in values:
        total += value
    return total


_GROUP_WSUM: dict[str, float] = {attr: _naive_sum(w for _, w in members) for attr, members in _GROUPS}
_TECHNIQUE = _IDX["technique"]
_COUPLED = tuple((attr, members) for attr, members in _GROUPS
                 if any(i == _TECHNIQUE for i, _w in members))
_COUPLED_ATTRS = frozenset(attr for attr, _members in _COUPLED)


def _technique_share(attr: str) -> float:
    members = dict(_GROUPS)[attr]
    return next(w for i, w in members if i == _TECHNIQUE) / _GROUP_WSUM[attr]


_TECH_P, _TECH_D = (_technique_share(attr) for attr, _members in _COUPLED)
_TECH_DET = 1.0 - _TECH_P * _TECH_D


# ===========================================================================
# 2) ROLLER, TARZLAR, KISILIK
# ===========================================================================

# Mevki -> ((rol, agirlik), ...). Kanat rollerinde taraf ayrica cekilir.
_ROLE_WEIGHTS: dict[Position, tuple[tuple[str, int], ...]] = {
    Position.GK: (("GK", 100),),
    Position.DEF: (("CB", 58), ("FB", 42)),
    Position.MID: (("DM", 22), ("CM", 30), ("AM", 18), ("WM", 30)),
    Position.FWD: (("ST", 68), ("W", 32)),
}
_SIDED_ROLES = {"FB": ("LB", "RB"), "WM": ("LM", "RM"), "W": ("LW", "RW")}

# Rol ofsetleri (FM birimi, L'ye gore). Motorla eslesen ozelliklerde yalnizca GRUP ICI SEKIL anlamlidir
# (grup ortalamasi motor degerine oturtulur); digerlerinde mutlak seviyedir. Kisilik ozellikleri ayridir.
_ROLE_OFFSETS: dict[str, dict[str, float]] = {
    "GK": {"passing": 1.0, "creativity": -1.5, "technique": 1.0, "long_shots": 0.5,
           "positioning": 6.0, "tackling": -3.0, "marking": -3.0, "agility": 4.5, "dribbling": -3.0,
           "crossing": -8.0, "heading": -7.0, "set_pieces": -7.0, "off_the_ball": -9.0,
           "anticipation": 0.5, "decisions": 0.5, "stamina": -3.0, "strength": -0.5, "jumping": 1.5,
           "balance": -1.0},
    "CB": {"acceleration": -0.8, "finishing": -1.0, "long_shots": 0.5, "passing": 0.8, "creativity": -1.2,
           "technique": -0.3, "marking": 0.6, "positioning": 0.4, "dribbling": -0.5, "agility": -0.3,
           "crossing": -4.0, "heading": 2.2, "set_pieces": -5.0, "off_the_ball": -5.5, "anticipation": 0.5,
           "decisions": -0.3, "stamina": -1.0, "strength": 1.8, "jumping": 1.8, "balance": -0.5},
    "FB": {"acceleration": 0.6, "finishing": -0.8, "long_shots": 0.3, "passing": 0.3, "creativity": -0.6,
           "tackling": 0.6, "positioning": -0.3, "dribbling": 0.3, "agility": 0.3,
           "crossing": 1.0, "heading": -1.5, "set_pieces": -3.0, "off_the_ball": -2.5, "anticipation": -0.3,
           "decisions": -0.8, "stamina": 2.0, "strength": -0.5, "jumping": -0.5, "balance": 0.5},
    "DM": {"acceleration": -0.3, "finishing": -0.8, "long_shots": 0.8, "passing": 0.8, "creativity": -0.8,
           "tackling": 1.2, "positioning": 0.9, "marking": -0.3, "dribbling": -0.3, "agility": -0.3,
           "crossing": -2.5, "heading": 0.3, "set_pieces": -2.5, "off_the_ball": -3.5, "anticipation": 0.8,
           "decisions": 0.5, "stamina": 1.2, "strength": 0.8, "balance": 0.0},
    "CM": {"finishing": -0.3, "long_shots": 0.6, "passing": 0.8, "creativity": 0.2, "technique": 0.2,
           "tackling": 0.5, "marking": -0.8, "crossing": -1.0, "heading": -1.0, "set_pieces": -1.0,
           "off_the_ball": -1.0, "anticipation": 0.3, "decisions": 0.5, "stamina": 1.5, "jumping": -0.8,
           "balance": 0.3},
    "AM": {"acceleration": 0.3, "finishing": 0.3, "long_shots": 0.3, "passing": 0.3, "creativity": 1.5,
           "technique": 0.8, "marking": -0.6, "tackling": -0.9, "positioning": -1.0, "dribbling": 0.5,
           "agility": 0.5, "crossing": -0.5, "heading": -2.5, "set_pieces": 0.5, "off_the_ball": 0.5,
           "anticipation": 0.5, "decisions": 1.2, "stamina": -0.3, "strength": -2.0, "jumping": -2.0,
           "balance": 1.0},
    "WM": {"acceleration": 1.1, "passing": -0.3, "creativity": 0.2, "technique": 0.8, "marking": -1.2,
           "tackling": -0.3, "positioning": -0.5, "dribbling": 1.0, "agility": 0.6,
           "crossing": 2.0, "heading": -3.0, "set_pieces": -0.5, "off_the_ball": 0.3, "anticipation": -0.5,
           "decisions": -1.0, "stamina": 1.2, "strength": -2.0, "jumping": -2.0, "balance": 1.0},
    "W": {"acceleration": 1.1, "passing": -0.3, "creativity": 0.2, "technique": 0.8, "marking": -1.2,
          "tackling": -0.3, "positioning": -0.5, "dribbling": 1.0, "agility": 0.6,
          "crossing": 1.8, "heading": -3.0, "set_pieces": -0.5, "off_the_ball": 0.8, "anticipation": -0.3,
          "decisions": -1.0, "stamina": 0.5, "strength": -2.0, "jumping": -2.0, "balance": 1.0},
    "ST": {"acceleration": 0.5, "finishing": 1.5, "long_shots": -0.8, "passing": -0.5, "creativity": -0.3,
           "technique": 0.5, "marking": -1.5, "tackling": -0.5, "positioning": -0.5,
           "crossing": -2.5, "heading": 1.0, "set_pieces": -2.0, "off_the_ball": 2.0, "anticipation": 1.0,
           "decisions": 0.8, "stamina": -0.5, "strength": 0.5, "jumping": 0.5},
}

# Motor disi yetenek ozelliklerinin genel seviyesi: L (overall) mevkinin anahtar ozelliklerine denk gelir; genel
# ozellikler (onsezi, denge ...) onun biraz altinda durur, yoksa sayfa motorla eslesenlerden yuksek gorunurdu.
_GENERIC_OFFSETS: dict[str, float] = {
    "anticipation": -1.2, "decisions": -1.2, "balance": -1.2, "jumping": -1.0, "strength": -1.0,
    "stamina": -0.8, "crossing": -0.5, "heading": -0.5, "set_pieces": -0.5, "off_the_ball": -0.5,
}

# Rol -> ((tarz, {ozellik: bonus}), ...): ayni motor degerli iki oyuncuyu ayiran bireysellik
_ARCHETYPES: dict[str, tuple[tuple[str, dict[str, float]], ...]] = {
    "GK": (
        ("shot_stopper", {"reflexes": 1.2, "handling": -0.8, "agility": 1.0, "bravery": 1.0}),
        ("commander", {"handling": 1.0, "reflexes": -0.6, "jumping": 1.5, "influence": 2.0, "positioning": 0.8,
                       "strength": 1.0}),
        ("sweeper", {"acceleration": 1.0, "pace": -0.5, "passing": 1.0, "anticipation": 1.2, "decisions": 0.8}),
    ),
    "CB": (
        ("stopper", {"strength": 1.5, "heading": 1.0, "aggression": 2.5, "bravery": 1.5, "tackling": 0.6,
                     "jumping": 1.0, "creativity": -0.8, "balance": -0.5}),
        ("ball_playing", {"passing": 1.0, "creativity": 1.5, "technique": 0.8, "anticipation": 1.0,
                          "decisions": 1.0, "flair": 1.5, "aggression": -1.0}),
        ("cover", {"acceleration": 1.0, "pace": 0.3, "positioning": 1.0, "anticipation": 1.5, "marking": 0.5,
                   "tackling": -0.5, "strength": -0.8}),
    ),
    "FB": (
        ("attacking", {"crossing": 2.0, "stamina": 1.0, "dribbling": 1.0, "off_the_ball": 1.5, "work_rate": 1.0,
                       "marking": -0.6, "flair": 1.0}),
        ("defensive", {"marking": 1.2, "tackling": 0.8, "positioning": 0.8, "strength": 1.0, "crossing": -1.2,
                       "off_the_ball": -1.0, "heading": 0.8}),
        ("athletic", {"acceleration": 1.2, "pace": 0.5, "stamina": 1.5, "agility": 0.6, "technique": -0.6,
                      "work_rate": 1.0}),
    ),
    "DM": (
        ("anchor", {"positioning": 1.5, "marking": 1.0, "strength": 1.0, "teamwork": 1.5, "creativity": -1.0,
                    "flair": -1.5}),
        ("ball_winner", {"tackling": 1.5, "aggression": 2.5, "work_rate": 1.5, "stamina": 1.0, "bravery": 1.2,
                         "positioning": -0.5, "technique": -0.5}),
        ("deep_playmaker", {"passing": 1.5, "creativity": 2.0, "technique": 0.8, "decisions": 1.0,
                            "set_pieces": 1.5, "tackling": -0.8, "flair": 1.0}),
    ),
    "CM": (
        ("box_to_box", {"stamina": 2.0, "work_rate": 2.0, "off_the_ball": 1.2, "strength": 1.0,
                        "creativity": -0.8, "finishing": 0.5}),
        ("playmaker", {"passing": 1.5, "creativity": 2.0, "technique": 1.0, "decisions": 1.0, "flair": 1.5,
                       "set_pieces": 1.0, "tackling": -0.8}),
        ("carrier", {"dribbling": 1.5, "balance": 1.5, "agility": 1.0, "acceleration": 0.8, "long_shots": 1.0,
                     "passing": -0.5}),
    ),
    "AM": (
        ("creator", {"creativity": 2.0, "passing": 1.0, "flair": 2.0, "technique": 0.8, "set_pieces": 1.5,
                     "decisions": 0.8, "finishing": -0.8, "off_the_ball": -0.5}),
        ("shadow_striker", {"off_the_ball": 2.0, "finishing": 1.5, "anticipation": 1.0, "acceleration": 1.0,
                            "creativity": -1.0, "passing": -0.5}),
        ("dribbler", {"dribbling": 2.0, "agility": 1.5, "balance": 1.5, "flair": 1.5, "technique": 0.5,
                      "passing": -0.8, "creativity": -0.5}),
    ),
    "WM": (
        ("flair", {"flair": 4.0, "dribbling": 1.5, "technique": 1.0, "agility": 1.0, "crossing": -1.2,
                   "work_rate": -1.0}),
        ("crosser", {"crossing": 3.0, "set_pieces": 1.5, "work_rate": 1.5, "stamina": 1.0, "flair": -2.0,
                     "dribbling": -0.8, "passing": 0.6}),
        ("speedster", {"acceleration": 1.8, "pace": 0.3, "off_the_ball": 1.2, "technique": -0.8, "stamina": 0.8,
                       "decisions": -0.8}),
    ),
    "W": (
        ("flair", {"flair": 4.0, "dribbling": 1.5, "technique": 1.0, "agility": 1.0, "crossing": -1.2,
                   "work_rate": -1.0}),
        ("crosser", {"crossing": 3.0, "set_pieces": 1.5, "work_rate": 1.0, "stamina": 1.0, "flair": -2.0,
                     "dribbling": -0.8, "finishing": -0.6}),
        ("inside_forward", {"finishing": 1.2, "long_shots": 0.8, "off_the_ball": 1.5, "acceleration": 1.0,
                            "crossing": -1.5, "decisions": 0.5}),
    ),
    "ST": (
        ("poacher", {"finishing": 1.8, "long_shots": -1.5, "off_the_ball": 2.5, "anticipation": 1.5,
                     "acceleration": 1.0, "heading": 0.5, "work_rate": -1.0, "passing": -0.8}),
        ("target_man", {"heading": 3.0, "strength": 3.0, "jumping": 2.5, "bravery": 1.5, "balance": 1.0,
                        "acceleration": -1.5, "agility": -1.5, "dribbling": -1.0, "flair": -1.5}),
        ("complete", {"technique": 1.5, "long_shots": 1.5, "passing": 1.0, "creativity": 1.0, "flair": 1.5,
                      "dribbling": 1.0, "off_the_ball": -0.5}),
    ),
}

# Kisilik: (temel ortalama, beceri katsayisi (L-10 basina), tecrube katsayisi, sapma std, {rol: ofset})
_CHARACTER: dict[str, tuple[float, float, float, float, dict[str, float]]] = {
    "determination": (11.5, 0.25, 0.3, 3.4, {}),
    "influence": (7.5, 0.30, 2.5, 2.8, {"GK": 1.0, "CB": 1.2, "DM": 0.8, "CM": 0.3, "W": -0.8, "WM": -0.8}),
    "flair": (9.0, 0.20, 0.0, 2.8, {"GK": -3.5, "CB": -3.0, "FB": -1.0, "DM": -2.0, "CM": 0.0, "AM": 3.0,
                                    "WM": 2.5, "W": 3.0, "ST": 1.0}),
    "aggression": (10.0, 0.00, 0.3, 3.3, {"GK": -2.0, "CB": 2.5, "FB": 1.0, "DM": 2.5, "CM": 0.5, "AM": -1.5,
                                          "WM": -1.5, "W": -1.5, "ST": 0.5}),
    "bravery": (11.0, 0.20, 0.3, 2.8, {"GK": 1.5, "CB": 2.0, "FB": 0.5, "DM": 1.0, "AM": -1.0, "WM": -1.0,
                                       "W": -1.0, "ST": 1.0}),
    "work_rate": (11.0, 0.25, 0.3, 2.8, {"GK": -1.0, "FB": 1.5, "DM": 2.0, "CM": 2.0, "AM": -1.0, "WM": 1.0,
                                         "W": 0.5, "ST": -1.0}),
    "teamwork": (11.0, 0.25, 1.0, 2.4, {"CB": 0.5, "FB": 0.5, "DM": 1.5, "CM": 1.5, "WM": 0.0, "W": -0.5,
                                        "ST": -1.0}),
}

# Yetenek ozelliklerinin (motor disi) motor sapmasina baglari: ozellik -> ((motor ozelligi, katsayi), ...)
_LINKS: dict[str, tuple[tuple[str, float], ...]] = {
    "crossing": (("passing", 0.5), ("dribbling", 0.2)),
    "set_pieces": (("passing", 0.4), ("shooting", 0.3)),
    "off_the_ball": (("shooting", 0.5), ("pace", 0.2)),
    "anticipation": (("defending", 0.2), ("shooting", 0.1), ("passing", 0.1)),
    "decisions": (("passing", 0.3),),
    "stamina": (("pace", 0.3),),
    "strength": (("defending", 0.2),),
    "balance": (("dribbling", 0.4),),
}
_HEADING_LINKS: dict[Position, tuple[tuple[str, float], ...]] = {
    Position.GK: (),
    Position.DEF: (("defending", 0.4),),
    Position.MID: (("shooting", 0.2), ("defending", 0.2)),
    Position.FWD: (("shooting", 0.4),),
}

_SD_ENGINE, _SD_ABILITY = 0.8, 1.2


def _vector(offsets: Mapping[str, float]) -> tuple[float, ...]:
    return tuple(float(offsets.get(key, 0.0)) for key in ATTRIBUTE_KEYS)


_ROLE_VECTORS = {role: _vector(offsets) for role, offsets in _ROLE_OFFSETS.items()}
_ARCHETYPE_VECTORS = {role: tuple((name, _vector(bonus)) for name, bonus in styles)
                      for role, styles in _ARCHETYPES.items()}

# _priors icin onceden hesaplanmis katsayi vektorleri: x = sabit + seviye*L + tecrube*E + genclik*Y + std*sapma
_EXP_TILT = {"decisions": 1.0, "anticipation": 1.0, "positioning": 0.8}      # zihinsel: tecrubeye kayar
_YOUTH_TILT = {"acceleration": 0.8, "agility": 0.8}                           # fiziksel: gence kayar


def _coefficients() -> tuple[tuple[float, ...], ...]:
    level, exp, youth, sd = [], [], [], []
    for key in ATTRIBUTE_KEYS:
        if key in _CHARACTER:
            _mean, ability, exp_k, char_sd, _by_role = _CHARACTER[key]
            level.append(ability)
            exp.append(exp_k)
            sd.append(char_sd)
        else:
            level.append(1.0)
            exp.append(_EXP_TILT.get(key, 0.0))
            sd.append(_SD_ENGINE if key in ENGINE_BACKED_KEYS else _SD_ABILITY)
        youth.append(_YOUTH_TILT.get(key, 0.0))
    return tuple(level), tuple(exp), tuple(youth), tuple(sd)


_LEVEL_COEF, _EXP_COEF, _YOUTH_COEF, _NOISE_SD = _coefficients()


def _static(family: str, style: int | None) -> tuple[float, ...]:
    """Sabit rol + tarz vektoru. style None: tarzsiz ('tipik' rol oyuncusu, expected_attributes)."""
    role = _ROLE_VECTORS[family]
    bonus = _ARCHETYPE_VECTORS[family][style][1] if style is not None else (0.0,) * _N
    out = []
    for i, key in enumerate(ATTRIBUTE_KEYS):
        if key in _CHARACTER:
            mean, ability, _exp_k, _sd, by_role = _CHARACTER[key]
            out.append(mean - 10.0 * ability + by_role.get(family, 0.0) + bonus[i])
        else:
            out.append(role[i] + bonus[i] + _GENERIC_OFFSETS.get(key, 0.0))
    return tuple(out)


_STATIC = {(family, style): _static(family, style)
           for family, styles in _ARCHETYPES.items() for style in (*range(len(styles)), None)}
_ZERO_NOISE = (0.0,) * _N
TYPICAL_AGE = 26              # expected_attributes: yas egimi (genclik / tecrube) "tipik" oyuncuda notr
_ENGINE_POS = {attr: n for n, attr in enumerate(ENGINE_ATTRIBUTES)}
_LINK_TABLE = {
    position: tuple((_IDX[key], tuple((_ENGINE_POS[attr], k) for attr, k in links))
                    for key, links in (*_LINKS.items(), ("heading", _HEADING_LINKS[position])) if links)
    for position in Position
}
_I_STAMINA, _I_STRENGTH = _IDX["stamina"], _IDX["strength"]


# ===========================================================================
# 3) OYUNCUYU OKUMA (duck typing)
# ===========================================================================

def _number(value: Any, default: float) -> float:
    if type(value) is int:                                      # sicak yol (MatchPlayer / ORM); bool int degil
        return float(value)
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _position_of(player: Any) -> Position:
    raw = getattr(player, "position", None)
    if type(raw) is Position:                                   # sicak yol
        return raw
    raw = getattr(raw, "value", raw)
    try:
        return Position(str(raw).upper())
    except ValueError:
        return Position.MID


def _fm_tuple(fm: Any) -> tuple[tuple[str, float], ...]:
    """FM verisinden yalnizca sayfaya giren (1-20) degerler; lru anahtari icin sirali demet."""
    if not isinstance(fm, Mapping) or not fm:
        return ()
    items = []
    for key, value in fm.items():
        number = _number(value, -1.0)
        if 1.0 <= number <= 20.0 and key in _FM_RELEVANT:
            items.append((str(key), number))
    return tuple(sorted(items))


_FM_RELEVANT = frozenset(ATTRIBUTE_KEYS) | {k for names in _FM_SOURCES_OF.values() for k in names} | {
    "left_foot", "right_foot"}


def identity_key(player: Any) -> str:
    """Oyuncunun kalici kimlik anahtari f"{player.id}|{player.name}" (14B, §3.1): profil sayfasi ile macta motorun
    okudugu sayfa AYNI tohumdan gelir. Dunya tohumu KULLANILMAZ: motor tarafinda (MatchPlayer) dunya tohumu yoktur,
    kullanilsaydi gosterilen sayfa ile oynayan sayfa ayrisirdi (K12 "cift kayit" dersi). Ayni dunyada id benzersizdir,
    farkli dunyalarda adlar farklidir; kimliksiz nesnede (seed.PlayerSpec) id None, ad kullanilir."""
    return f"{getattr(player, 'id', None)}|{getattr(player, 'name', '')}"


def _read(player: Any) -> tuple[str, Position, int, int, tuple[int, ...], tuple[tuple[str, float], ...]]:
    """(kimlik anahtari, mevki, yas, overall, motor ozellikleri, FM verisi) -- ORM, PlayerSpec, MatchPlayer ile
    calisir."""
    key = identity_key(player)
    position = _position_of(player)
    age = int(_number(getattr(player, "age", None), 25.0))
    if getattr(player, "pace", None) is not None:              # ORM Player / MatchPlayer / SimpleNamespace
        engine = tuple(int(_number(getattr(player, a, None), 50.0)) for a in ENGINE_ATTRIBUTES)
        fm = getattr(player, "fm_attributes", None)
        if fm is None:                                          # MatchPlayer: FM verisi 'attributes'ta
            fm = getattr(player, "attributes", None)
    else:                                                       # seed.PlayerSpec: motor ozellikleri 'attributes'ta
        attrs = getattr(player, "attributes", None) or {}
        engine = tuple(int(_number(attrs.get(a), 50.0)) for a in ENGINE_ATTRIBUTES)
        fm = getattr(player, "fm_attributes", None)
    overall = getattr(player, "overall_rating", None)
    if overall is None or isinstance(overall, bool):
        overall = getattr(player, "overall", None)
    if overall is None or isinstance(overall, bool):
        overall = compute_overall(position, dict(zip(ENGINE_ATTRIBUTES, engine, strict=True)))
    return key, position, age, int(_number(overall, 60.0)), engine, _fm_tuple(fm)


# ===========================================================================
# 4) KIMLIK: rol, taraf, tarz, ayak, sapmalar (yalnizca tohum + mevki)
# ===========================================================================

_STREAM_BYTES = 8 + 3 * _N          # 4 secim (2'ser bayt) + 31 sapma (3'er bayt)


def _stream(key: str) -> bytes:
    """Oyuncunun kalici tohum akisi: sha256(f"{player.id}|{player.name}") ozeti SHAKE-256 ile genisletilir.
    Python'un tuzlu hash()'i ve global random KULLANILMAZ: her surecte, her makinede ayni baytlar."""
    digest = hashlib.sha256(key.encode()).digest()
    return hashlib.shake_256(digest).digest(_STREAM_BYTES)


def _uniform(stream: bytes, k: int) -> float:
    """k. secim icin [0, 1) tekduze sayi (2 bayt, 1/65536 cozunurluk)."""
    return (stream[2 * k] * 256 + stream[2 * k + 1]) / 65536.0


def _pick(roll: float, options: tuple[tuple[str, int], ...]) -> str:
    roll *= sum(w for _, w in options)
    for name, weight in options:
        roll -= weight
        if roll < 0:
            return name
    return options[-1][0]


@lru_cache(maxsize=16384)
def _identity(key: str, position: Position) -> tuple[str, str, int, str, tuple[float, ...]]:
    """(rol ailesi, rol kodu, tarz indeksi, ayak, 31 standart sapma). Yalnizca kimlik + mevkiden gelir: motor
    degerleri (gelisim) degisse de rol, tarz, ayak ve sapmalar sabit kalir."""
    stream = _stream(key)
    family = _pick(_uniform(stream, 0), _ROLE_WEIGHTS[position])
    style = int(_uniform(stream, 1) * len(_ARCHETYPES[family]))
    sides = _SIDED_ROLES.get(family)
    code = (sides[0] if _uniform(stream, 2) < 0.5 else sides[1]) if sides else family
    foot_roll = _uniform(stream, 3)
    if code in ("LB", "LM", "LW"):
        foot = FOOT_LEFT if foot_roll < 0.65 else FOOT_BOTH if foot_roll < 0.72 else FOOT_RIGHT
    elif code in ("RB", "RM", "RW"):
        foot = FOOT_LEFT if foot_roll < 0.05 else FOOT_BOTH if foot_roll < 0.12 else FOOT_RIGHT
    else:
        foot = FOOT_LEFT if foot_roll < 0.15 else FOOT_BOTH if foot_roll < 0.20 else FOOT_RIGHT
    # Irwin-Hall(3): uc tekduze baytin toplami ~N(0, 1), [-3, 3] ile sinirli (asiri uc deger yok)
    noise = tuple((stream[j] + stream[j + 1] + stream[j + 2] + 1.5) / 128.0 - 3.0
                  for j in range(8, _STREAM_BYTES, 3))
    return family, code, style, foot, noise


# ===========================================================================
# 5) SAYFA
# ===========================================================================

def _group_value(avg: float, ceiling: int) -> int:
    """derive_engine_attributes'in bu grup icin verecegi motor degeri: ratings.fm_scale (clamp(round(20 +
    avg * 3.95), 1, 99)) satir ici; saha oyuncusu kaleciligi icin ceiling = OUTFIELD_GK_CEILING."""
    value = round(20 + avg * 3.95)
    return 1 if value < 1 else ceiling if value > ceiling else value


def _targets(position: Position, engine: tuple[int, ...], shift: float) -> dict[str, float]:
    """Olcegin temsil edebildigi motor hedefleri (24..99; saha oyuncusu kaleciligi 24..40) + overall kaydirmasi."""
    outfield = position is not Position.GK
    weights = POSITION_WEIGHTS[position]
    targets: dict[str, float] = {}
    for attr, value in zip(ENGINE_ATTRIBUTES, engine, strict=True):
        hi = OUTFIELD_GK_CEILING if (attr == "goalkeeping" and outfield) else _SCALE_TOP
        base = min(max(float(value), _SCALE_FLOOR), hi)
        if weights[attr] > 0:
            base = min(max(base + shift, _SCALE_FLOOR), hi)
        targets[attr] = base
    return targets


def _solve_group(x: list[float], members: tuple[tuple[int, float], ...], wsum: float, goal: float,
                 lo: list[int], hi: list[int], fixed: list[bool]) -> float:
    """Grubun agirlikli ortalamasini goal'e tasir: serbest uyeler ortak kayar, sinira dayanan payi digerlerine
    birakir (grup ici sekil korunur). Uygulanan duzeltmeyi (ilk hata - kalan hata) dondurur: ortak uyeli
    gruplarin Gauss-Seidel dongusu yakinsamayi bununla olcer."""
    total = 0.0
    for i, w in members:
        total += x[i] * w
    first = err = goal - total / wsum
    for _ in range(len(members)):
        if -1e-6 < err < 1e-6:
            return first
        up = err > 0
        free_w = 0.0
        for i, w in members:
            if not fixed[i] and (x[i] < hi[i] if up else x[i] > lo[i]):
                free_w += w
        if free_w == 0.0:
            break
        step = err * wsum / free_w
        clipped = False
        for i, _w in members:
            if not fixed[i] and (x[i] < hi[i] if up else x[i] > lo[i]):
                value = x[i] + step
                if value > hi[i]:
                    value, clipped = hi[i], True
                elif value < lo[i]:
                    value, clipped = lo[i], True
                x[i] = value
        if not clipped:                                         # sinira degmediyse kayma tam oturdu
            return first
        total = 0.0
        for i, w in members:
            total += x[i] * w
        err = goal - total / wsum
    return first - err


def _solve_coupled(x: list[float], goals: Mapping[str, float], lo: list[int], hi: list[int],
                   fixed: list[bool]) -> bool:
    """Pas ve dribling gruplari (teknik ortak) icin kapali cozum: gruplar a ve b kadar ortak kayar, teknik a + b.
        a + t_p * b = e_p      b + t_d * a = e_d      (t: teknigin grup icindeki normalize agirligi)
    Sabit uye yoksa ve hicbir deger sinira tasmiyorsa uygular ve True doner; aksi halde x'e dokunmaz
    (cagiran Gauss-Seidel'e duser)."""
    if fixed is not _NOT_FIXED:
        return False
    (_attr_p, members_p), (_attr_d, members_d) = _COUPLED
    errors = []
    for attr, members in _COUPLED:
        total = 0.0
        for i, w in members:
            total += x[i] * w
        errors.append(goals[attr] - total / _GROUP_WSUM[attr])
    e_p, e_d = errors
    a = (e_p - _TECH_P * e_d) / _TECH_DET
    b = (e_d - _TECH_D * e_p) / _TECH_DET
    moved = {i: a for i, _w in members_p}
    for i, _w in members_d:
        moved[i] = moved.get(i, 0.0) + b
    for i, delta in moved.items():
        if not lo[i] <= x[i] + delta <= hi[i]:
            return False
    for i, delta in moved.items():
        x[i] += delta
    return True


def _fit_continuous(x: list[float], lo: list[int], hi: list[int], fixed: list[bool],
                    targets: Mapping[str, float]) -> list[float]:
    """Grup ortalamalarini motor hedeflerine oturtur (surekli, kopya uzerinde): ortak uyesiz gruplar tek tek,
    pas + dribling (teknik ortak) once kapali cozumle, olmazsa Gauss-Seidel ile."""
    x = list(x)
    goals = {attr: (targets[attr] - 20.0) / 3.95 for attr in ENGINE_ATTRIBUTES}
    for attr, members in _GROUPS:
        if attr not in _COUPLED_ATTRS:
            _solve_group(x, members, _GROUP_WSUM[attr], goals[attr], lo, hi, fixed)
    if not _solve_coupled(x, goals, lo, hi, fixed):
        for _ in range(8):                                      # sinir / sabit uye: Gauss-Seidel
            worst = 0.0
            for attr, members in _COUPLED:
                err = _solve_group(x, members, _GROUP_WSUM[attr], goals[attr], lo, hi, fixed)
                worst = err if err > worst else -err if -err > worst else worst
            if worst < 0.01:                                    # 0.01 FM birimi = 0.04 motor puani
                break
    return x


def _fit(x: list[float], lo: list[int], hi: list[int], fixed: list[bool], targets: Mapping[str, float],
         anchors: Mapping[str, float], outfield: bool, rescue: bool = False) -> tuple[list[int], dict[str, int]]:
    """Grup ortalamalarini hedeflere oturtur (surekli), sonra her grupta motor degerini tutturan tam sayi
    kombinasyonunu secer (hata yayarak yuvarlama; tutmazsa hedefe dogru birer adimlik denemeler). Yalnizca pas ve
    dribling gruplari (teknik ortak) birbirine baglidir; teknik once yuvarlanir, iki grup onu sabit gorur.
    Esitlikte (orn. 90 hedefine 89 ya da 91) oyuncunun gercek motor degerine (anchors) yakin olan secilir.
    Donus: (1-20 sayfa, derive_engine_attributes'in bu sayfadan verecegi motor degerleri)."""
    x = _fit_continuous(x, lo, hi, fixed, targets)
    ints = [int(v + 0.5) for v in x]                            # x zaten [lo, hi] icinde ve pozitif
    derived: dict[str, int] = {}
    for attr, members in _GROUPS:
        target, anchor = targets[attr], anchors[attr]
        wsum = _GROUP_WSUM[attr]
        ceiling = OUTFIELD_GK_CEILING if (outfield and attr == "goalkeeping") else 99
        free = [i for i, _w in members if not fixed[i] and i != _TECHNIQUE]
        carry = 0.0                                             # hata yayma: grup toplami surekli cozume yakin kalsin
        for i, w in members:
            if fixed[i] or i == _TECHNIQUE:
                carry += w * (x[i] - ints[i])
        for i, w in members:
            if not (fixed[i] or i == _TECHNIQUE):
                v = int(x[i] + carry / w + 0.5)
                v = hi[i] if v > hi[i] else lo[i] if v < lo[i] else v
                ints[i] = v
                carry += w * (x[i] - v)
        total = 0.0                                             # ratings._weighted_fm ile ayni toplama sirasi
        for i, w in members:
            total += ints[i] * w
        value = _group_value(total / wsum, ceiling)
        if -0.5 <= value - target <= 0.5:                       # yuvarlama zaten hedefte
            derived[attr] = value
            continue
        step = 1 if value < target else -1                      # once yalnizca hedefe dogru birer adim
        start = tuple(ints[i] for i in free)
        best_key, best = (abs(value - target), abs(value - anchor)), (start, value)
        for two_way in ((False, True) if rescue else (False,)):
            # Kurtarma turu (yalniz _sheet tolerans disinda kalinca, rescue=True): ilk tur hedefi 0.5 icinde
            # tutturamazsa uyeler iki yone birer adim (orn. hiz 88 ancak hiz +1 / cabukluk -1 ile tutuyor; 14B'de
            # kimlik degisince 99 tavanli bir kalecide gorundu).
            if two_way and best_key[0] <= 0.5:
                break
            options = []
            for i, near in zip(free, start, strict=True):
                steps = (near - 1, near, near + 1) if two_way else (near, near + step)
                options.append(tuple(v for v in steps if lo[i] <= v <= hi[i]) or (near,))
            for combo in product(*options):
                for i, v in zip(free, combo, strict=True):
                    ints[i] = v
                total = 0.0
                for i, w in members:
                    total += ints[i] * w
                value = _group_value(total / wsum, ceiling)
                key = (abs(value - target), abs(value - anchor))
                if key < best_key:
                    best_key, best = key, (combo, value)
                    if key[0] <= 0.5:
                        break
        for i, v in zip(free, best[0], strict=True):
            ints[i] = v
        derived[attr] = best[1]
    return ints, derived


def _priors(family: str, style: int | None, noise: tuple[float, ...], position: Position, age: int, overall: int,
            engine: tuple[int, ...]) -> list[float]:
    """On degerler (FM birimi, kirpilmamis): sabit rol+tarz vektoru + seviye + yas + sapma + motor sapmasi.
    style None + sifir sapma + TYPICAL_AGE: rolun ve seviyenin 'tipik' oyuncusu (expected_attributes)."""
    level = (overall - 20.0) / 3.95
    youth = max(-1.0, min(1.0, (27 - age) / 10.0))
    experience = max(-1.0, min(1.25, (age - 24) / 8.0))
    offsets = POSITION_OFFSETS[position]
    dev = [(max(value, _SCALE_FLOOR) - max(overall + offsets[attr], _SCALE_FLOOR)) / 3.95
           for attr, value in zip(ENGINE_ATTRIBUTES, engine, strict=True)]
    x = [st + lc * level + ec * experience + yc * youth + sd * nz
         for st, lc, ec, yc, sd, nz in zip(_STATIC[family, style], _LEVEL_COEF, _EXP_COEF, _YOUTH_COEF,
                                           _NOISE_SD, noise, strict=True)]
    for i, links in _LINK_TABLE[position]:
        for e, k in links:
            x[i] += k * dev[e]
    x[_I_STAMINA] += max(-1.5, min(0.6, (27 - age) / 8.0))
    if age < 21:
        x[_I_STRENGTH] -= 0.35 * (21 - age)
    return x


def _fm_values(fm: tuple[tuple[str, float], ...]) -> dict[int, int]:
    """FM verisi -> {indeks: 1-20}. set_pieces: free_kicks/corners ortalamasi."""
    if not fm:
        return {}
    data = dict(fm)
    values: dict[int, int] = {}
    for key in ATTRIBUTE_KEYS:
        names = _FM_SOURCES_OF.get(key, (key,))
        if key == "set_pieces" and "set_pieces" not in data:
            found = [data[n] for n in names[1:] if n in data]
        else:
            found = next(([data[n]] for n in names if n in data), [])
        if found:
            values[_IDX[key]] = min(20, max(1, int(math.floor(sum(found) / len(found) + 0.5))))
    return values


def _overall_shift(position: Position, engine: tuple[int, ...], overall: int) -> tuple[float, float, dict[str, float]]:
    """(overall farki, ilk kaydirma, kaydirmasiz hedefler): overall_rating ile agirlikli ortalama farki (kulup bandi
    kirpmasi, gelisim) agirlikli ozellikleri kaydirir."""
    weights = POSITION_WEIGHTS[position]
    plain = _targets(position, engine, 0.0)
    gap = overall - sum(weights[a] * plain[a] for a in ENGINE_ATTRIBUTES)
    shift = 0.0 if abs(gap) <= 0.8 else math.copysign(min(SHIFT_LIMIT, abs(gap) - 0.6), gap)
    return gap, shift, plain


@lru_cache(maxsize=16384)
def _sheet(key: str, position: Position, age: int, overall: int, engine: tuple[int, ...],
           fm: tuple[tuple[str, float], ...]) -> tuple[tuple[int, ...], str, str]:
    family, code, style, foot, noise = _identity(key, position)
    outfield = position is not Position.GK
    known = _fm_values(fm)
    lo = [1] * _N
    hi = list(_HI_OUTFIELD if outfield else _HI_KEEPER)
    fixed = _NOT_FIXED
    base = _priors(family, style, noise, position, age, overall, engine)
    if known:
        fixed = [i in known for i in range(_N)]
        for i, value in known.items():
            base[i] = float(value)
            lo[i] = hi[i] = value
    base = [h if v > h else lw if v < lw else v              # on degerler olcegin icinden baslar
            for v, lw, h in zip(base, lo, hi, strict=True)]

    # overall_rating ile agirlikli ortalama farki (kulup bandi kirpmasi, gelisim) agirlikli ozellikleri kaydirir.
    # overall yine disarida kalirsa (orn. kalecilik 99 tavaninda) kaydirma ENGINE_TOLERANCE'i asmadan buyutulur.
    gap, shift, plain = _overall_shift(position, engine, overall)
    best: tuple[tuple[float, float, float], list[int]] | None = None
    for rescue in (False, True):                                # kurtarma turu yalniz tolerans disinda kalinca
        if rescue and best[0][0] == 0 and best[0][1] == 0:
            break
        for extra in (0.0, 0.3, 0.6, 0.9):
            if extra and abs(shift) + extra > ENGINE_TOLERANCE + 0.4:
                break
            trial = shift + math.copysign(extra, gap) if extra else shift
            targets = plain if trial == 0.0 else _targets(position, engine, trial)
            ints, derived = _fit(base, lo, hi, fixed, targets, plain, outfield, rescue)
            attr_err = max(abs(derived[a] - plain[a]) for a in ENGINE_ATTRIBUTES)
            ovr_err = abs(compute_overall(position, derived) - overall)
            key = (max(0.0, attr_err - ENGINE_TOLERANCE), max(0.0, ovr_err - OVERALL_TOLERANCE),
                   attr_err + ovr_err)
            if best is None or key < best[0]:
                best = (key, ints)
            if key[0] == 0 and key[1] == 0:
                break
    ints = best[1]

    feet = dict(fm)
    if "left_foot" in feet and "right_foot" in feet:           # FM "Left Foot / Right Foot" (1-20) varsa
        left, right = feet["left_foot"], feet["right_foot"]
        foot = FOOT_BOTH if min(left, right) >= 15 else FOOT_LEFT if left > right else FOOT_RIGHT
    return tuple(ints), code, foot


def _computed(player: Any, world_seed: int | None) -> tuple[tuple[int, ...], str, str]:
    """world_seed geriye uyumluluk icin imzada kalir ve YOK SAYILIR (14B §3.1: kimlik oyuncunun kendisinden)."""
    key, position, age, overall, engine, fm = _read(player)
    return _sheet(key, position, age, overall, engine, fm)


@lru_cache(maxsize=16384)
def _expected(family: str, position: Position, overall: int, engine: tuple[int, ...]) -> tuple[int, ...]:
    """Rol ailesi + seviye icin 'tipik' sayfa: tarz yok, sapma yok, yas TYPICAL_AGE; motorla eslesen gruplar ayni
    motor hedeflerine (overall kaydirmasi dahil) surekli oturtulur, sonra 1-20'ye yuvarlanir. FM verisi yok sayilir
    (FM oyuncusu kendi farkini tasir)."""
    outfield = position is not Position.GK
    lo = [1] * _N
    hi = list(_HI_OUTFIELD if outfield else _HI_KEEPER)
    base = _priors(family, None, _ZERO_NOISE, position, TYPICAL_AGE, overall, engine)
    base = [h if v > h else lw if v < lw else v for v, lw, h in zip(base, lo, hi, strict=True)]
    _gap, shift, plain = _overall_shift(position, engine, overall)
    targets = plain if shift == 0.0 else _targets(position, engine, shift)
    x = _fit_continuous(base, lo, hi, _NOT_FIXED, targets)
    return tuple(min(h, max(lw, int(v + 0.5))) for v, lw, h in zip(x, lo, hi, strict=True))


# ===========================================================================
# 6) GENEL ARAYUZ
# ===========================================================================

def player_attributes(player: Any, world_seed: int | None = None) -> dict[str, int]:
    """
    Kesin 1-20 CM sayfasi (ATTRIBUTE_KEYS sirasinda, 31 anahtar). FM oyunculari: gercek FM degerleri (FM adlari
    bizimkilere eslenir); digerleri: motor ozellikleri + mevki + yas + sha256(f"{player.id}|{player.name}")
    tohumundan deterministik turetilir. Motor ozelliklerini ENGINE_TOLERANCE, overall'i OVERALL_TOLERANCE icinde
    yeniden uretir. Her cagri yeni bir sozluk dondurur (onbellek paylasilmaz).
    world_seed YOK SAYILIR (geriye uyumluluk): profil sayfasi ile motorun okudugu sayfa (14B) ayni olmali.
    """
    values, _code, _foot = _computed(player, world_seed)
    return dict(zip(ATTRIBUTE_KEYS, values, strict=True))


def sheet_and_expected(player: Any) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(sayfa, tipik sayfa) degerleri ATTRIBUTE_KEYS sirasinda, TEK okumayla (motorun mac hazirligi, 14B)."""
    key, position, age, overall, engine, fm = _read(player)
    values = _sheet(key, position, age, overall, engine, fm)[0]
    return values, _expected(_identity(key, position)[0], position, overall, engine)


def expected_attributes(player: Any) -> dict[str, int]:
    """
    Oyuncunun rol ailesi (kimlikten) ve seviyesi (overall + motor ozellikleri) icin 'tipik' 1-20 sayfa: oyun tarzi,
    kisisel sapma ve yas egimi YOK. attribute_model bunu butce notrlugunun sifir noktasi olarak kullanir (sayfasi
    buna esit oyuncunun her karar noktasindaki carpani tam 1.0). Arayuzde GOSTERILMEZ.
    """
    key, position, _age, overall, engine, _fm = _read(player)
    family = _identity(key, position)[0]
    return dict(zip(ATTRIBUTE_KEYS, _expected(family, position, overall, engine), strict=True))


def preferred_foot(player: Any, world_seed: int | None = None) -> str:
    """"Sağ" / "Sol" / "Her iki ayak". Tohumlu (~%75 sag, ~%20 sol, ~%5 iki ayak); sol bek / sol kanat
    cogunlukla solak. FM verisinde left_foot/right_foot varsa ondan. Motor degerleri degisse de sabittir."""
    return _computed(player, world_seed)[2]


def player_role(player: Any, world_seed: int | None = None) -> str:
    """Turetilmis rol kodu (ROLE_LABELS anahtari: CB, LB, DM, RW, ST ...). Motor OKUMAZ; yalnizca sayfanin
    sekli ve tercih edilen ayak icin. Tohum + mevkiden gelir, gelisimle degismez."""
    return _computed(player, world_seed)[1]


_TO_FM: dict[str, tuple[str, ...]] = {"set_pieces": ("free_kicks", "corners"), "creativity": ("vision",),
                                     "influence": ("leadership",), "jumping": ("jumping_reach",)}


_FM_OUT: tuple[tuple[str, int], ...] = tuple(
    (name, i) for i, key in enumerate(ATTRIBUTE_KEYS) for name in _TO_FM.get(key, (key,)))


def fm_attributes_from_values(values: tuple[int, ...]) -> dict[str, int]:
    """as_fm_attributes'in ATTRIBUTE_KEYS sirasindaki deger demeti icin hizli bicimi (motorun mac hazirligi)."""
    return {name: int(values[i]) for name, i in _FM_OUT}


def as_fm_attributes(sheet: Mapping[str, int]) -> dict[str, int]:
    """Sayfa -> FM anahtarlari (ratings.derive_engine_attributes / team_roles / MatchPlayer.attributes icin)."""
    out: dict[str, int] = {}
    for key, value in sheet.items():
        names = _TO_FM.get(key)
        if names is None:
            out[key] = int(value)
        else:
            for name in names:
                out[name] = int(value)
    return out


def _fog_fraction(player_key: Any) -> float:
    digest = hashlib.sha256(f"cm-fog|{player_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2.0 ** 64


def range_width(knowledge_pct: float) -> int:
    """Aralik genisligi (ust - alt): %25 -> 4, %70'e dogru 1'e iner; %70+ 0 (kesin)."""
    k = float(knowledge_pct)
    if k >= EXACT_KNOWLEDGE:
        return 0
    k = max(float(RANGE_KNOWLEDGE), k)
    span = EXACT_KNOWLEDGE - RANGE_KNOWLEDGE
    return max(1, math.ceil(MAX_RANGE_WIDTH * (EXACT_KNOWLEDGE - k) / span - 1e-9))


def attribute_range(value: int, knowledge_pct: float, player_key: Any) -> tuple[int, int] | None:
    """(alt, ust) ya da None (bilinmiyor). Aralik gercek degeri her zaman icerir, 1-20 icinde kalir ve bilgi
    arttikca ic ice daralir; kayma player_key'e bagli sabit bir kesirdir (yeniden cizimde titremez)."""
    if value is None or knowledge_pct is None or isinstance(knowledge_pct, bool):
        return None
    k = float(knowledge_pct)
    if k < RANGE_KNOWLEDGE:
        return None
    v = min(20, max(1, int(value)))
    width = range_width(k)
    if width == 0:
        return v, v
    low = v - int(_fog_fraction(player_key) * (width + 1))
    low = min(max(low, 1), 20 - width)
    return low, low + width


def attribute_display(value: int, knowledge_pct: float, player_key: Any) -> str:
    """
    Gozlemci sisi: bilgi (0-100, ScoutAssignment.knowledge olcegi) %70+ -> "15"; %25-69 -> "12-15" (bilgi arttikca
    daralir, gercek degeri hep icerir); %25 alti ya da bilinmeyen -> "?".
    player_key (oyuncu, ozellik) ciftini tanimlamalidir: orn. f"{player.id}|{key}" ya da (player.id, key).
    Genislik ve kayma bu anahtardan deterministik gelir; ayni girdi her cizimde ayni metni verir.
    """
    bounds = attribute_range(value, knowledge_pct, player_key)
    if bounds is None:
        return "?"
    low, high = bounds
    return str(low) if low == high else f"{low}-{high}"


def cache_clear() -> None:
    """Testler / olcum icin onbellekleri bosaltir."""
    _identity.cache_clear()
    _sheet.cache_clear()
    _expected.cache_clear()


__all__ = [
    "ATTRIBUTE_GROUPS", "ATTRIBUTE_KEYS", "ATTRIBUTE_LABELS", "CHARACTER_KEYS", "DISPLAY_ONLY_KEYS",
    "ENGINE_BACKED_KEYS", "ENGINE_READ_KEYS", "ENGINE_TOLERANCE", "EXACT_KNOWLEDGE", "FM_READ_KEYS", "FOOT_BOTH",
    "FOOT_LEFT", "LEGACY_DISPLAY_ONLY_KEYS", "LEGACY_FM_READ_KEYS", "LEGACY_UNREAD_FOR_GENERATED_KEYS",
    "FOOT_RIGHT", "GOALKEEPER_KEYS", "GROUP_LABELS", "MENTAL_KEYS", "OVERALL_TOLERANCE", "PHYSICAL_KEYS",
    "RANGE_KNOWLEDGE", "ROLE_LABELS", "TECHNICAL_KEYS", "TYPICAL_AGE", "UNREAD_FOR_GENERATED_KEYS",
    "as_fm_attributes", "attribute_display", "attribute_range", "cache_clear", "expected_attributes",
    "fm_attributes_from_values",
    "identity_key", "player_attributes", "player_role", "preferred_foot", "range_width", "sheet_and_expected",
]
