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
    * Canli mac (9. Asama): simulate() artik adim adim akisin sonuna kadar kosturulmasidir.
      start() / step() / finished / snapshot() ile mac DAKIKA DAKIKA ilerletilir; adimlar
      arasinda menajer mudahale eder:
        manual_substitution  kulubedeki saglikli oyuncuyla degisiklik (hak ve pencere kurali)
        change_formation     dizilis (MATCH_FORMATIONS, acil durum 5-3-2 dahil); sahadakilerin
                             rolleri yeniden dagitilir, dizilis carpanlari kalan dakikalarda gecerli
        set_instructions     zihniyet + sertlik (instructions.py); guc, kart, sakatlik, yorgunluk
      Mudahaleler rastgele sayi CEKMEZ: mudahale yoksa adim adim oynanan mac simulate() ile
      bit-bit aynidir. Degisiklik penceresi kurali (EngineConfig.sub_windows, orn. 5 hak / 3
      pencere) istege baglidir; devre arasi ve uzatma molalari pencere saymaz.
    * Taktik derinlik (Soccer Manager tarzi; varsayilanlarla motor BIT-BIT eski davranistadir):
        talimatlar        pas stili, tempo, pres, hucum yonu, ofsayt taktigi, kontra atak
                          (instructions.py). Rakipten bagimsiz carpanlar TeamInstructions'ta;
                          rakibe bagli olanlar _matchup_factor / _chance_quality'de
        roller            MatchTeam.roles (team_roles.SetPieceRoles): kaptan (kart riski, geride
                          kalinca savunma dususu, penalti sogukkanliligi), seri penaltida ilk atici
        duran toplar      EngineConfig.set_pieces (None: bir takim atici belirlediyse acik). Pozisyon
                          cekilisinin (ek rastgele sayi CEKMEDEN) bir payi penalti / direkt frikik /
                          korner olur; kalite aticinin ve kafa vuranin becerisiyle belirlenir
        oyun plani        MatchTeam.plan (match_plan.MatchPlan): her oynanan dakikadan sonra kurallar
                          yoklanir; eylemler menajer mudahaleleriyle ayni kod yolundan gecer
                          (hak/pencere/uygunluk). MatchTeam.plans_enabled ile kapatilir
        AI talimatlari    EngineConfig.ai_tactics=True: menajer kontrolunde olmayan (manager_controlled
                          False) ve plani olmayan takimlara instructions.ai_instructions uygulanir
    * Anlatim (13B; EngineConfig.narration / build_up_chains / flow_events / possession_weighted,
      hepsi False iken 13A ile bit-bit ayni; acikken SONUCLAR yine bit-bit ayni):
        veri              cumleler commentary.py bankasinda (canli + gecmis zaman, agirlik, baglam
                          etiketleri); ayri anlatim RNG'si, kova basina tekrar onleyici halka
        meta veri (K5)    MatchEvent.priority / display_probability / dwell_ms / chain_id / report;
                          sans kalitesi `chance_quality` SAKLANIR, asla metne dokulmez (K6)
        kurulus zinciri   atak 1-3 zincirli olay: kurulus halkalari (BUILD_UP) + sut + sonuc;
                          sekil pozisyon cekilisinin artigindan (yeni rastgele sayi yok)
        akis olaylari     CORNER / FOUL / OFFSIDE / ambiyans HER ZAMAN yazilir, akista cogu
                          gizlenir (match_feed, K7); gizlenen olaylarin cumlesi okununca kurulur
        topla oynama      sekans agirlikli (TeamStats.possession_weight, MatchResult.possession_share)
    * Ozellikler motorda (14B; EngineConfig.attribute_model, VARSAYILAN ACIK; kapaliyken 13B ile bit-bit ayni):
        CM 01/02 sayfasinin (cm_attributes, 31 ozellik 1-20; profil ile ayni tohum) 31'i ve gizli sakatlik
        egilimi mevcut cekilislerin olasilik / agirliklarinda okunur (yeni rastgele sayi YOK). Oyuncu carpani
        "tipik sayfadan sapma"dir (tipik oyuncuda tam 1.0): sutor secimi, bitiricilik (yakin / uzak / net sans),
        isabet, asist, hedeflenme ve markaj, kart ve sakatlik kurbani, yorulma, kaleci gucu, duran top; takim
        olcekli uyum, pres, pozisyon hacmi, geri donus, hava savunmasi. Tek kaynak attribute_model.READERS.
    * Taktik etkisi ve karsi hamle (14E; EngineConfig.tactics_v2, VARSAYILAN ACIK; kapaliyken 14B ile bit-bit ayni):
        butce             sut HACMI tum carpanlarla, sans NETLIGI (yogunluk) savunanin YAPISAL gucuyle: otobus
                          rakibin sut sayisini dusurur ama kaliteyi geri vermez. Talimat carpanlari hacme / topa
                          sahip olmaya kendi keskinligiyle yansir (instruction_*_v2)
        kale onu          TeamInstructions.concede_quality rakibin akan oyun sans netligini carpar (K6: gosterilmez)
        karsi hamle       instructions.MATCHUP_EFFECTS: kapali blok x hucum yonu, pres x pas stili x tempo, kontra x
                          (topyekun hucum / onde pres / ustun rakip)
        norm              sahadaki GERCEK rol sayimi (+ bos yuvalar); dizilis tarzi da gercek sekilden
        AI                ai_formation (dizilis) + ai_counter_move (rakibin gorunen talimatina karsi hamle)
    * Not modeli ve gizli ozellikler (15G; EngineConfig.rating_model, VARSAYILAN ACIK; kapaliyken 14E ile
      bit-bit ayni):
        not               PERFORMANSI anlatir: cekilen BELIRLI savunmacinin duellosu, kalecinin kurtarisinin
                          netligi (K6: sayi olarak gosterilmez; yenilen net golde kalecinin sucu azalir) ve
                          atak zincirindeki katki nota girer. Taban + sayilan katkilar + not defteri
        tutarlilik        oyuncu basina "gunun formu": KENDI tohumlu crc32 akisindan (mac RNG'si tuketilmez),
                          beklenen degeri tam 1.0. Dusuk tutarlilik = genis sapma; gucu ve notu birlikte kaydirir
        onemli mac        eleme / final / derbi maclarinda oyuncunun seviyesi kayar (lig macinda carpan tam 1.0).
                          Macin agirligi motorun bildigi gercekten (knockout, tarafsiz saha, MatchTeam.rivals)
                          ya da acikca MatchEngine(..., occasion=...) ile gelir
        mizac             kart agirligi: sogukkanli oyuncu daha az, cabuk parlayan daha cok kart gorur
        Uc gizli ozellik de 1-20'dir ve FM verisi yoksa transfer_rules.hidden_trait ile kimlikten kalici crc32
        ile gelir. K12: hicbiri sayi olarak hicbir olaya, meta veriye ya da arayuze girmez.

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
import math
import random
import sys
import zlib
from bisect import bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import cache
from typing import Any

import attribute_model
import commentary
import fitness
import team_roles
from attribute_model import AttributeModelConfig, RatingModelConfig
from instructions import (
    AI_FORMATION_MAX_CHANGES,
    COUNTER_ATTACK,
    FOCUS_SKILL_RANGE,
    MATCHUP_EFFECTS,
    OFFSIDE_TRAP,
    AttackingFocus,
    Mentality,
    PassingStyle,
    Pressing,
    TeamInstructions,
    Tempo,
    ai_counter_move,
    ai_formation,
    ai_instructions,
)
from match_plan import MatchPlan, PlanRule
from models import LineupStatus, Position
from penalties import (
    PenaltyKick,
    PenaltyTaker,
    ShootoutConfig,
    ShootoutResult,
    ShootoutSide,
    conversion_probability,
    equalize_takers,
    run_shootout,
    save_share,
)
from tactics import FORMATIONS, MATCH_FORMATIONS, formation_name
from team_roles import SetPieceRoles

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
    # --- canli mudahale (9. Asama) ---
    TACTICAL_CHANGE = "TACTICAL_CHANGE"       # dizilis ya da takim talimati degisti (detail: formation / instructions)
    # --- 13B "anlatim": simule edilen akis ile GOSTERILEN akis ayrisir (K7) ---
    # Bu olaylar HER ZAMAN uretilir (istatistikler onlardan turer) ama akista cogunlukla
    # gizlenir (match_feed, display_probability). Uretimi atlamak istatistigi yalancilastirir.
    CORNER = "CORNER"                         # korner kazanildi (sut degil)
    FOUL = "FOUL"                             # kartsiz faul
    OFFSIDE = "OFFSIDE"                       # ofsayt
    BUILD_UP = "BUILD_UP"                     # kurulus zinciri halkasi (detail: win / entry / final / pressure)


# Seri penalti donemine ait olay turleri. Bu olaylar (ve arkalarindan gelen FULL_TIME) oyun
# bittikten sonra, son oyun dakikasina (120 ya da 90) added_time=0 ile yazilir; kronolojik
# sirada tum oyun olaylarindan SONRA gelirler.
SHOOTOUT_EVENTS = frozenset({EventType.SHOOTOUT_START, EventType.PENALTY_SHOOTOUT})


class MatchPhase(str, Enum):
    """Motorun o anki evresi. Canli macta adimlar arasinda hangi mudahalenin serbest oldugunu belirler."""
    NOT_STARTED = "NOT_STARTED"
    FIRST_HALF = "FIRST_HALF"
    HALF_TIME = "HALF_TIME"
    SECOND_HALF = "SECOND_HALF"
    EXTRA_TIME_BREAK = "EXTRA_TIME_BREAK"         # 90+X dudugu ile uzatma baslangici arasi mola
    EXTRA_TIME_FIRST_HALF = "EXTRA_TIME_FIRST_HALF"
    EXTRA_TIME_HALF_TIME = "EXTRA_TIME_HALF_TIME"
    EXTRA_TIME_SECOND_HALF = "EXTRA_TIME_SECOND_HALF"
    PENALTIES = "PENALTIES"
    FINISHED = "FINISHED"


# Molalar: degisiklik penceresi saymaz (IFAB), canli macta varsayilan olarak duraklatilir
BREAK_PHASES = frozenset({MatchPhase.HALF_TIME, MatchPhase.EXTRA_TIME_BREAK, MatchPhase.EXTRA_TIME_HALF_TIME})


class InterventionError(ValueError):
    """Menajer mudahalesi kurallara aykiri (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


def _sentence(text: str) -> str:
    """Olay metni icin: nokta ile biten cumle."""
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


# ---------------------------------------------------------------------------
# 13B / K5: olay basina sunum meta verisi (CM 01/02 `events_eng.cfg` deseni)
# ---------------------------------------------------------------------------
# Bekleme kademeleri (ms). Oynatma katmani (13C) bunlari kullanir; motor yalnizca yazar.
DWELL_ROUTINE = 900      # korner, faul, ofsayt, kurulus, degisiklik
DWELL_MAIN = 1800        # sut, kurtaris, kart, sakatlik, duduk
DWELL_CRUCIAL = 3000     # gol, kirmizi kart, penalti

# `_log` ile yazilan olaylarin tur bazli varsayilan beklemesi (anlatim yolu kendi degerini yazar)
_TYPE_DWELL: dict[str, int] = {
    "GOAL": DWELL_CRUCIAL, "RED_CARD": DWELL_CRUCIAL, "PENALTY_SHOOTOUT": DWELL_CRUCIAL,
    "FULL_TIME": DWELL_CRUCIAL, "SUBSTITUTION": DWELL_ROUTINE, "TACTICAL_CHANGE": DWELL_ROUTINE,
    "CORNER": DWELL_ROUTINE, "FOUL": DWELL_ROUTINE, "OFFSIDE": DWELL_ROUTINE, "BUILD_UP": DWELL_ROUTINE,
}

# Oncelik (0-100): akista yer darsa yuksek olan gosterilir.
PRIORITY_AMBIENT = 12
PRIORITY_LOW = 20
PRIORITY_ROUTINE = 35
PRIORITY_MAIN = 55
PRIORITY_HIGH = 80
PRIORITY_CRUCIAL = 95


@dataclass(slots=True)
class MatchEvent:
    """
    Kronolojik olay kaydi. 2D arayuz bu nesneleri dogrudan okuyabilir.
    13B: mac basina ~90 olay uretildigi icin __slots__ (olusturma ve bellek maliyeti).
    """
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
    # GOAL / SAVE / MISS: None (akan oyun) ya da duran top "penalty" / "free_kick" / "corner".
    # SUBSTITUTION: "manual" (menajer) / "plan" (oyun plani). TACTICAL_CHANGE: "formation" /
    # "instructions" (menajer), "plan" (oyun plani eylemi), "plan_skipped" (plan eylemi uygulanamadi,
    # sebep aciklamada), "ai" (AI talimati).
    detail: str | None = None
    # Seri penalti skoru (bu olaydan sonra) ve atis sirasi. Seri yoksa 0 / None.
    home_penalties: int = 0
    away_penalties: int = 0
    kick_number: int | None = None
    # --- 13B / K5: sunum meta verisi (akis bunlari okur, motor yalnizca yazar) ---
    priority: int = PRIORITY_MAIN            # 0-100
    display_probability: float = 1.0         # akista gorunme olasiligi (K7)
    dwell_ms: int = DWELL_MAIN               # oynatma katmaninin bekleme suresi
    chain_id: int | None = None              # ayni atak pasajindaki olaylar ayni zinciri paylasir
    chain_step: int = 0                      # zincirdeki sira (0 = ilk)
    # K6: sans kalitesi SAKLANIR ama ASLA SAYI OLARAK GOSTERILMEZ. Yalnizca hangi
    # cumlenin secilecegini belirler; arayuz bu alani metne cevirmemelidir.
    chance_quality: float | None = None
    report: str | None = None                # ayni anin gecmis zaman (mac raporu) cumlesi
    tags: frozenset[str] = frozenset()       # anlatim baglami (skor durumu, dakika bandi, bicim)

    @property
    def display_minute(self) -> str:
        return f"{self.minute}+{self.added_time}'" if self.added_time else f"{self.minute}'"


_EVENT_TEXT = MatchEvent.description          # slot tanimlayicisi (LazyEvent dogrudan okur/yazar)


class LazyEvent(MatchEvent):
    """
    13B: cumlesi ILK OKUNDUGUNDA kurulan olay (akista cogunlukla gizlenen turler: siradan
    iskalar ve kurtarislar, kurulus halkalari, korner / faul / ofsayt, ambiyans).

    Neden: mac basina ~90 olay uretiliyor ama bir sezon simulasyonunda bu metinlerin neredeyse
    hicbiri okunmuyor; kimsenin izlemedigi macta cumle kurmak bos is. Metin yalnizca olay
    yukunden ve olaya ozel bir sayidan (`token`) kurulur, tekrar onleyici halka ayni kovadaki
    bir onceki olaydan zincirle gelir: sonuc okunma SIRASINDAN BAGIMSIZDIR ve ayni tohum
    her zaman ayni cumleleri verir (canli mac ve simulate() birebir ayni).
    Nesne her bakimdan bir MatchEvent'tir (isinstance, alanlar, asdict, esitlik, repr).
    """

    __slots__ = ("_spec", "_prev", "_ring")

    def __init__(self, spec: tuple | None = None, prev: LazyEvent | None = None, **fields: Any) -> None:
        self._spec = spec             # (banka anahtari, baglam, isimler, token)
        self._prev = prev             # ayni kovadaki bir onceki tembel olay
        self._ring: tuple[int, ...] | None = None if spec is not None else ()
        fields.setdefault("description", "")
        MatchEvent.__init__(self, **fields)

    @property
    def description(self) -> str:                       # type: ignore[override]
        text = _EVENT_TEXT.__get__(self)
        if text or getattr(self, "_spec", None) is None:
            return text
        pending: list[LazyEvent] = []
        event: LazyEvent | None = self
        while event is not None and event._ring is None:
            pending.append(event)
            event = event._prev
        ring = event._ring if event is not None else ()
        for item in reversed(pending):
            key, tags, names, token = item._spec
            line = commentary.pick(key, tags, token, ring)
            ring = (*ring, id(line))[-commentary.RING_SIZE:]
            item._ring = ring
            _EVENT_TEXT.__set__(item, line.live.format_map(_names_slots(names)))
            item._spec = item._prev = None
        return _EVENT_TEXT.__get__(self)

    @description.setter
    def description(self, value: str) -> None:
        _EVENT_TEXT.__set__(self, value)


_LAZY_NEW = LazyEvent.__new__


def _names_slots(names: tuple[str, str, str, str | None, str | None]) -> commentary.Slots:
    """Tembel olayin yuklu isimleri (takim, rakip, kaleci, oyuncu, savunmaci) -> sablon yuvalari."""
    team, opponent, keeper, player, defender = names
    slots = commentary.Slots(t=team, o=opponent, gk=keeper)
    if player is not None:
        slots["p"] = player
    if defender is not None:
        slots["d"] = defender
    return slots


def _lazy_event(spec: tuple, prev: LazyEvent | None, minute: int, added: int, type_: EventType,
                team: MatchTeam, player: MatchPlayer | None, home_score: int, away_score: int,
                detail: str | None, priority: int, show: float, dwell: int, chain_id: int | None,
                chain_step: int, quality: float | None, tags: frozenset[str]) -> LazyEvent:
    """
    LazyEvent'i dataclass __init__'ini atlayarak kurar (mac basina ~50 kez: kwargs'li 22 alanlik
    kurucu olcululebilir bir maliyetti). HER alan burada atanir; MatchEvent'e alan eklenirse
    buraya da eklenmelidir (tests/test_narration.py alan butunlugunu dogrular).
    """
    ev = _LAZY_NEW(LazyEvent)
    ev._spec = spec
    ev._prev = prev
    ev._ring = None
    _EVENT_TEXT.__set__(ev, "")
    ev.minute = minute
    ev.added_time = added
    ev.type = type_
    ev.team = team.name
    ev.team_id = team.id
    if player is None:
        ev.player = ev.player_id = None
    else:
        ev.player = player.name
        ev.player_id = player.id
    ev.home_score = home_score
    ev.away_score = away_score
    ev.detail = detail
    ev.home_penalties = ev.away_penalties = 0
    ev.kick_number = None
    ev.priority = priority
    ev.display_probability = show
    ev.dwell_ms = dwell
    ev.chain_id = chain_id
    ev.chain_step = chain_step
    ev.chance_quality = quality
    ev.report = None
    ev.tags = tags
    return ev


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
    # FM ozellikleri (1-20; orn. crossing, heading, penalty_taking, leadership). Bos: veri yok,
    # team_roles beceri yardimcilari temel ozelliklerden turetir. Yalnizca taktik derinlik kullanir.
    attributes: dict[str, float] = field(default_factory=dict)

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
    # Mac icinde dizilis degisikligiyle rol degisimleri: (olay indeksi, eski rol, yeni rol).
    # Rol, o indeksteki TACTICAL_CHANGE olayindan itibaren gecerlidir (2D saha gecmisi dogru cizer).
    role_changes: list[tuple[int, Position, Position]] = field(default_factory=list)

    # --- guc onbellegi (13A / S1) ---------------------------------------------------
    # SAF HIZLANDIRMA: hicbir sonucu degistirmez, yalnizca ayni dakika icinde tekrar
    # tekrar hesaplanan degerleri saklar. Motorun CPU'sunun ~%57'si burada geciyordu.
    #   _base_strength : (0.4*overall + 0.6*rating) * condition_factor  -- mac boyunca sabit
    #   _strength_state: [energy, role, attack, midfield, defense]      -- enerji/rol degisince silinir
    #   _fatigue_state : [energy, fatigue_factor]
    # Carpma SIRASI eski formulle birebir aynidir; kayan nokta sonucu bit-bit korunur.
    _base_strength: dict[str, float] = field(default_factory=dict, repr=False, compare=False)
    _strength_state: list = field(default_factory=lambda: [None, None, None, None, None],
                                  repr=False, compare=False)
    _fatigue_state: list = field(default_factory=lambda: [None, 1.0], repr=False, compare=False)
    # Mac boyunca degismeyen turetilmis degerler (aggression, decay_multiplier)
    _const_cache: dict[str, float] = field(default_factory=dict, repr=False, compare=False)

    # --- 14B: ozellik modeli (YALNIZCA EngineConfig.attribute_model acikken dolar) ------
    # sheet: 1-20 CM sayfasi (cm_attributes; profil sayfasiyla ayni tohum). Testler dogrudan enjekte edebilir.
    # _am  : attribute_model.PlayerFactors (karar noktasi carpanlari). K12: ikisi de repr/olay/arayuze girmez.
    # Bayrak kapaliyken sheet bos, _am None kalir ve hicbir sey hesaplanmaz.
    sheet: dict[str, int] = field(default_factory=dict, repr=False, compare=False)
    _am: Any = field(default=None, repr=False, compare=False)

    # --- 15G: gizli ozellikler ve not defteri (YALNIZCA EngineConfig.rating_model acikken dolar) -----
    # _trait     : attribute_model.HiddenTraits (tutarlilik, onemli mac, mizac) -- K12: sayi olarak gosterilmez
    # _day_form  : gunun formu (tutarliliktan; ayri crc32 akisi). 1.0 -> guc carpimina HIC girmez
    # _big_game  : eleme / final / derbi carpani (lig macinda tam 1.0)
    # _duel/_chain/_keeper_credit: not defteri (duello, zincir katkisi, kurtaris / yenilen gol)
    _trait: Any = field(default=None, repr=False, compare=False)
    _day_form: float = field(default=1.0, repr=False, compare=False)
    _big_game: float = field(default=1.0, repr=False, compare=False)
    _duel_credit: float = field(default=0.0, repr=False, compare=False)
    _chain_credit: float = field(default=0.0, repr=False, compare=False)
    _keeper_credit: float = field(default=0.0, repr=False, compare=False)

    def reset_strength_cache(self) -> None:
        """Ozellik / kondisyon degisirse (mac hazirligi) onbellegi bosaltir."""
        self._base_strength.clear()
        self._const_cache.clear()
        self._strength_state[:] = [None, None, None, None, None]
        self._fatigue_state[:] = [None, 1.0]

    def role_at(self, event_index: int) -> Position:
        """Verilen olay indeksinde oynadigi rol (dizilis degisiklikleri geriye sarilarak)."""
        role = self.role or self.position
        for index, old, _new in reversed(self.role_changes):
            if event_index < index:
                role = old
        return role

    @classmethod
    def from_orm(cls, p) -> MatchPlayer:
        condition = fitness.condition_of(p)
        fm = getattr(p, "fm_attributes", None) or {}
        stamina = fm.get("stamina")
        return cls(
            id=p.id, name=p.name, position=p.position, age=p.age,
            overall=p.overall_rating, pace=p.pace, shooting=p.shooting,
            passing=p.passing, defending=p.defending, dribbling=p.dribbling,
            goalkeeping=p.goalkeeping, form=p.form, morale=p.morale,
            condition=condition, energy=float(condition),
            stamina=float(stamina) if stamina is not None else None,
            attributes=dict(fm) if isinstance(fm, dict) else {},
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
        """Enerji 100 -> 1.00, enerji 0 -> 0.75 (fitness.fatigue_factor). Enerji basina onbellekli."""
        state = self._fatigue_state
        if state[0] != self.energy:
            state[0] = self.energy
            state[1] = fitness.fatigue_factor(self.energy)
        return state[1]

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
        """Kart yeme egilimi: mevki + defans gucu + dusuk moral (sinir). Mac boyunca sabit."""
        value = self._const_cache.get("aggression")
        if value is None:
            base = {Position.DEF: 1.4, Position.MID: 1.0, Position.FWD: 0.7, Position.GK: 0.15}
            value = (base[self.position] * (0.7 + 0.3 * self.defending / 100) * (1.25 - 0.5 * self.morale / 100))
            if self._am is not None:
                value *= self._am.card          # 14B: saldirganlik (+), temiz mudahale (-)
            if self._trait is not None:
                value *= self._trait.card       # 15G: gizli mizac (sogukkanli az, cabuk parlayan cok kart)
            self._const_cache["aggression"] = value
        return value

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
        """Toplam yorulma carpani: yas x FM dayaniklilik (veri yoksa sadece yas). Mac boyunca sabit."""
        value = self._const_cache.get("decay")
        if value is None:
            value = self.stamina_multiplier * fitness.stamina_decay_multiplier(self.stamina)
            if self._am is not None:
                value *= self._am.fatigue       # 14B: caliskanligin bedeli
            self._const_cache["decay"] = value
        return value


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
    # --- birinci sinif mac istatistikleri (13A / S2, EngineConfig.match_stats) ---
    corners: int = 0
    fouls: int = 0
    offsides: int = 0
    # --- 13B / D12: DURUST TOPLA OYNAMA ---
    # `possession_minutes` dakikanin sahibini SAYAR; esit takimlarda 90 bagimsiz yazi-tura
    # oldugu icin dagilimi zorunlu olarak dardir (sd ~5.3, %45-64). `possession_weight`
    # ayni sahiplik dizisini SEKANS AGIRLIGI ile toplar: hakimiyetini surduren takimin
    # dakikalari daha agir sayilir (gercek maclarda uzun sahiplik pasajlari boyle olusur).
    # repr=False: eski altin parmak izleri TeamStats.__repr__ uzerinden kurulu.
    possession_weight: float = field(default=0.0, repr=False, compare=False)


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
    # --- canli mac (9. Asama) ---
    instructions: TeamInstructions = field(default_factory=TeamInstructions)
    auto_subs: bool = True                    # False: yorgunluk degisikliklerini menajer yapar (sakatlikta asistan yine sokar)
    sub_windows_used: int = 0                 # oyun sirasinda kullanilan degisiklik penceresi (molalar haric)
    tactical_swaps_used: int = 0              # yorgunluk disi (taktik gerekceli) degisiklik sayisi
    window_key: int | None = field(default=None, repr=False)     # su an acik pencerenin duraklama anahtari
    # --- taktik derinlik (Soccer Manager tarzi); varsayilanlar eski davranistir ---
    roles: SetPieceRoles = field(default_factory=SetPieceRoles)   # kaptan + duran top aticilari
    plan: MatchPlan = field(default_factory=MatchPlan)            # durum bazli oyun plani
    plans_enabled: bool = True                # False: plan kurallari yoklanmaz (canli macta menajer kapatabilir)
    plan_fired: set[int] = field(default_factory=set)            # islenmis (tetiklenmis) kural indeksleri
    manager_controlled: bool = False          # True: EngineConfig.ai_tactics bu takimin talimatina dokunmaz
    # "Gunun formu" (13A / S4): mac basinda bir kez cekilen log-normal performans carpani.
    # Varsayilan 1.0 -- EngineConfig.match_form kapaliyken hic cekilmez.
    match_form: float = 1.0

    # --- kadro onbellegi (13A / S1) -------------------------------------------------
    # on_pitch / outfield_on_pitch / keeper dakikada onlarca kez soruluyordu. Liste her
    # seferinde AYNI siralamayla (self.players sirasi) yeniden kurulur; yalnizca surum
    # degistiginde hesaplanir. Siralamanin korunmasi sart: _weighted_choice bu sirayla ceker.
    _lineup_version: int = field(default=0, repr=False, compare=False)
    _lineup_cache: dict = field(default_factory=dict, repr=False, compare=False)
    # --- 14E: bos yuvalar -------------------------------------------------------------
    # Sahadan cikip yerine kimse girmeyen oyuncunun YUVASI (kirmizi kart, degisikliksiz sakatlik). Gercek rol
    # sayimi (EngineConfig.tactics_v2) = sahadakilerin rolleri + bu yuvalar: 10 kisi kalan takimin normu 11 yuvali
    # kalir (13A D6 kirmizi kart bedeli aynen korunur). Saf muhasebe; sonuca yalnizca bayrak acikken girer.
    open_slots: list[Position] = field(default_factory=list, repr=False, compare=False)
    # --- 15G: derbi rakipleri -----------------------------------------------------------
    # Bu takimin ezeli rakiplerinin kulup id'leri. Kariyer / turnuva katmani doldurur (kanca); bos birakilirsa
    # motor macin agirligini yalnizca eleme / tarafsiz sahadan turetir. Sonuca yalnizca rating_model acikken girer.
    rivals: frozenset[int] = field(default_factory=frozenset, repr=False, compare=False)

    def touch_lineup(self) -> None:
        """Sahadaki oyuncu kumesi ya da rolleri degisti: kadro onbellegini gecersiz kilar."""
        self._lineup_version += 1
        self._lineup_cache.clear()

    def _cached(self, key: str, build):
        cache = self._lineup_cache
        if cache.get("v") != self._lineup_version:
            cache.clear()
            cache["v"] = self._lineup_version
        if key not in cache:
            cache[key] = build()
        return cache[key]

    @property
    def on_pitch(self) -> list[MatchPlayer]:
        return self._cached("on", lambda: [p for p in self.players if p.on_pitch])

    @property
    def captain_on_pitch(self) -> bool:
        """Belirlenmis kaptan sahada mi? (oyundan cikan / atilan / sakatlanan kaptanin etkisi kalmaz)"""
        cid = self.roles.captain_id
        return cid is not None and any(p.id == cid and p.on_pitch for p in self.players)

    def on_pitch_by_id(self, player_id: int | None) -> MatchPlayer | None:
        if player_id is None:
            return None
        return next((p for p in self.players if p.id == player_id and p.on_pitch), None)

    @property
    def outfield_on_pitch(self) -> list[MatchPlayer]:
        return self._cached("out", lambda: [p for p in self.on_pitch if p.role is not Position.GK])

    @property
    def bench(self) -> list[MatchPlayer]:
        # Onbelleklenmez: available_on_bench sakatlik/degisiklik/kadro disi gibi kadro
        # surumune bagli olmayan alanlara da bakar.
        return [p for p in self.players if p.available_on_bench]

    @property
    def keeper(self) -> MatchPlayer | None:
        def _find() -> MatchPlayer | None:
            for p in self.on_pitch:
                if p.role is Position.GK:
                    return p
            return None

        return self._cached("gk", _find)

    @property
    def player_count(self) -> int:
        return len(self.on_pitch)

    def field_player(self, p: MatchPlayer, role: Position, minute: int) -> None:
        p.on_pitch = True
        p.role = role
        p.entered_minute = minute
        p.log_energy(minute)
        slots = self.open_slots
        if slots:                       # giren oyuncu bos yuvayi doldurur (rolu farkliysa yuva onun rolune doner)
            slots.remove(role if role in slots else slots[0])
        self.touch_lineup()

    def remove_player(self, p: MatchPlayer, minute: int) -> None:
        p.log_energy(minute)
        p.on_pitch = False
        p.left_minute = minute
        self.open_slots.append(p.role or p.position)
        self.touch_lineup()

    def reset_open_slots(self) -> None:
        """14E: bos yuvalari dizilisten yeniden kurar (kadro kurulumu / dizilis degisikligi sonrasi)."""
        counts = {Position.GK: 0, Position.DEF: 0, Position.MID: 0, Position.FWD: 0}
        for p in self.on_pitch:
            counts[p.role or p.position] += 1
        d, m, f = self.formation or (4, 4, 2)
        wanted = ((Position.GK, 1), (Position.DEF, d), (Position.MID, m), (Position.FWD, f))
        free = [role for role, n in wanted for _ in range(max(0, n - counts[role]))]
        self.open_slots[:] = free[:max(0, 11 - len(self.on_pitch))]
        self.touch_lineup()

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

    def possession_share(self) -> tuple[int, int] | None:
        """
        13B / D12: ekranda gosterilecek topla oynama yuzdesi (ev, deplasman).
        Sekans agirlikli olcu (`TeamStats.possession_weight`); motor onu yazmadiysa
        (eski yol / el yapimi sonuc) dakika sahipligine duser. Hic veri yoksa None.
        """
        home, away = self.home.stats, self.away.stats
        total = home.possession_weight + away.possession_weight
        if total > 0:
            share = round(100 * home.possession_weight / total)
        else:
            minutes = home.possession_minutes + away.possession_minutes
            if minutes == 0:
                return None
            share = round(100 * home.possession_minutes / minutes)
        return share, 100 - share


# ===========================================================================
# [2] MOTOR
# ===========================================================================

@dataclass
class EngineConfig:
    """Tum ayarlanabilir sabitler tek yerde. Kalibrasyon burada yapilir."""
    formation: tuple[int, int, int] = (4, 4, 2)
    max_subs: int = 5
    subs_reserved_for_emergency: int = 1     # taktik degisiklik bu kadar hakki saklar
    # Degisiklik penceresi (IFAB: 5 hak en fazla 3 duraklamada; devre arasi saymaz). None: sinir yok.
    sub_windows: int | None = None
    extra_time_extra_windows: int = 1        # uzatmalarda acilan ek pencere

    # Form/moral etkisi. Kullanici formulu overall*form*moral'dir; ham haliyle
    # form 45/moral 60 ile form 65/moral 85 arasinda 2x fark cikar ve yetenek
    # anlamsizlasir. Bu yuzden notr noktaya (form 50, moral 70) gore sonumlenir.
    condition_influence: float = 0.25
    condition_clamp: tuple[float, float] = (0.88, 1.12)
    neutral_form: int = 50
    neutral_morale: int = 70

    home_advantage_base: float = 0.05
    home_advantage_per_reputation: float = 0.0006
    home_attack_share: float = 1.20   # ev avantajinin hucuma yansiyan payi (flat_superiority)

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

    # --- duran toplar (team_roles.SetPieceRoles) ---
    # 13A/S2 (D8): ARTIK VARSAYILAN ACIK. Kapaliyken normal bir macta hic korner, hic
    # penalti, hic frikik yoktu. None: yalnizca bir takim atici belirlediyse acilir; False: kapali.
    set_pieces: bool | None = True
    # Pozisyonlarin payi (ek rastgele sayi cekilmez: pozisyon cekilisinin alt dilimi).
    # 13A kalibrasyonu (3000 esit mac, iki takim toplami): 0.32 penalti (%79 gol), 1.65 frikik,
    # 4.4 korner SUTU / mac; duran top golleri tum gollerin ~%27'si (gercek ~%20-25).
    set_piece_penalty_share: float = 0.012
    set_piece_free_kick_share: float = 0.06
    set_piece_corner_share: float = 0.16
    free_kick_quality: float = 0.80             # direkt frikik: duvar + mesafe (sutcu gucu carpani)
    corner_quality: float = 0.84                # korner: kafa vurusu (sutcu gucu carpani)
    set_piece_skill_influence: float = 0.005    # atici becerisi - takim seviyesi (puan) basina kalite
    set_piece_delivery_range: tuple[float, float] = (0.90, 1.10)

    # --- kaptan (sahadayken) ---
    captain_card_factor: float = 0.92           # takimin kart riski
    captain_trailing_relief: float = 0.35       # geride kalinca savunma dususunun silinen payi (0.92 -> ~0.948)
    captain_penalty_composure: float = 2.0      # penalti aticisi yetenegine eklenir (mac ici + seri)

    # --- AI talimatlari (instructions.ai_instructions) ---
    ai_tactics: bool = False                    # True: manager_controlled olmayan, plani olmayan takimlar
    ai_tactics_interval: int = 5                # dakikada bir (ve gol / kirmizi karttan sonra) yeniden degerlendir

    # =======================================================================
    # 13A "motor dogrulugu" bayraklari
    # -----------------------------------------------------------------------
    # Her davranis degisikligi kendi bayraginin arkasindadir. Gelistirme boyunca hepsi
    # False (bugunku davranis) idi; 13A kapanisinda TEK ve ACIK bir adimda acildi ve
    # altin parmak izleri / kariyer parite ozetleri o adimda yeniden temellendirildi.
    # Hepsini False yapmak motoru 12. Asama davranisina birebir geri dondurur
    # (2500 tohum x 2 senaryo + 500 eleme maci ile bit-bit dogrulandi).
    # Eski kalibrasyon sabitleri (base_chance, base_goal, sharpness_*, straight_red_share,
    # cautious_after_yellow, short_handed_penalty, tactical_sub_from_minute,
    # condition_influence, fatigue_base_decay) o yol icin oldugu gibi duruyor.
    # =======================================================================

    # --- S2: korner / faul / ofsayt birinci sinif istatistik (D8, D12) ---
    match_stats: bool = True
    # Pozisyona donmeyen korner ve ofsayt, ATAK cekilisinin artigindan turetilir:
    # yeni rastgele sayi CEKILMEZ (determinizm ve hiz korunur).
    dead_corner_share: float = 0.093        # sansa donmeyen atagin bu dilimi korner
    offside_share: float = 0.035            # ... bu dilimi ofsayt
    foul_share: float = 0.175               # disiplin cekilisinin kartsiz faul dilimi

    # --- S2: disiplin (D6) ---
    discipline_v2: bool = True
    straight_red_share_v2: float = 0.010    # 0.035 -> gercek bant icin
    cautious_after_yellow_v2: float = 0.22  # sari gormus oyuncu cok daha temkinli
    short_handed_penalty_v2: float = 0.80   # 10 kisi ~%25-30 kaybeder (eskiden %13)

    # --- S2: rol gercekciligi (D7) ---
    role_realism: bool = True
    # Sutu kim ceker: takim ICINDE (normalize edilir, takim ustunlugunu BUYUTMEZ) yildiz
    # forvet sirandan forvetten belirgin daha cok sut alir. K3: ustunluk donusumde degil,
    # sut hacminde ve sansin kime dustugunde ifade edilir.
    shooter_sharpness: float = 5.0
    shooter_reference: float = 80.0

    # --- S3: zayif halka (K2, D2) ---
    weak_link: bool = True
    # Hucum zayif halkayi ARAR: savunmaci cekilisinin agirligi gucuyle ters orantilidir.
    # 0 -> hedefleme yok (saf rol agirligi); 2 -> zayif stoper iki katindan fazla sut yer.
    defender_targeting: float = 2.0
    defender_reference: float = 80.0            # "lig ortalamasi" savunmaci (mutlak olcek)
    defender_quality_exponent: float = 0.80     # savunmacinin sans KALITESINE etkisi
    defender_quality_range: tuple[float, float] = (0.65, 1.55)
    finishing_shooting_weight: float = 0.78     # sut->gol adiminda bitiricilik agirligi
    finishing_sharpness: float = 0.60           # bitiricilik / kaleci carpanlarinin keskinligi
    base_goal_v2: float = 0.321                 # weak_link yolunda isabetli sut -> gol tabani
    finishing_reference: float = 78.0           # "lig ortalamasi" bitirici (mutlak olcek)
    keeper_reference: float = 84.0              # "lig ortalamasi" kaleci
    finishing_range: tuple[float, float] = (0.55, 1.85)
    keeper_range: tuple[float, float] = (0.60, 1.60)

    # --- S4: tek keskinlik butcesi + gunun formu (K3, D3, D9) ---
    flat_superiority: bool = True
    possession_sharpness: float = 1.2       # topa sahip olma (tek butcenin parcasi)
    chance_sharpness: float = 6.5           # ustunluk buraya toplanir (sut HACMI)
    accuracy_sharpness: float = 0.35        # isabet adimi: guc farkina neredeyse duyarsiz
    base_on_target_v2: float = 0.375        # isabet tabani (gercek: tum sutlarin %33-42'si)
    base_chance_v2: float = 0.218           # gol/mac 2.7-2.8 icin taban pozisyon orani
    # Ustunlugun sut hacmine yansimasi bu KATLARLA sinirlidir (tempodan bagimsiz):
    # doygunluk buradan gelir, 90+ temposu bundan etkilenmez.
    chance_multiplier_cap: tuple[float, float] = (0.18, 1.85)
    # "Ustunluk sansin TIPINE de yansir": bastiran takim cok ama daha kotu sans uretir,
    # kapanan takim az ama daha net kontra bulur. Blowout'lari kiran asil mekanizma budur.
    chance_density_exponent: float = 3.2
    chance_density_range: tuple[float, float] = (0.42, 1.04)
    # Olu bant: ustunluk bu orani (notr sansin kati) gecene kadar kalite dusmez. Boylece
    # kucuk farklar (gercek ligdeki 1.4-6.9 OVR) hala sonuca yansir, buyuk farklar doyar.
    chance_density_threshold: float = 1.25
    match_form: bool = True
    match_form_sigma: float = 0.08          # gunun formu: log-normal ~%8
    match_form_range: tuple[float, float] = (0.80, 1.25)

    # --- S5: zaman ve ritim (D5, D10) ---
    goal_timing: bool = True
    # Dakikaya gore pozisyon carpani: (dakika ust siniri, carpan). Gercek gol dakikasi
    # egrisi duz degildir; hedef: ilk yari %46-50, son 10 dakika ~%19, 90+ %6-9,
    # ilk 5 dakika ~%3.5. Uzatma dakikalari ayri (daha yuksek) carpan alir.
    tempo_curve: tuple[tuple[int, float], ...] = (
        (5, 0.74), (15, 0.88), (30, 1.01), (45, 1.19), (60, 0.93), (75, 0.97), (80, 1.16), (90, 1.36),
    )
    stoppage_tempo: float = 2.45

    # --- S5: degisiklik zamanlamasi (D5) ---
    sub_timing: bool = True
    tactical_sub_from_minute_v2: int = 45
    sub_window_spread: float = 0.075        # dakikada degisiklik penceresi acma egilimi
    sub_urgency_trailing: float = 1.8       # geride olan takim daha erken/daha cok degisiklik yapar
    tactical_swap_from_minute: int = 62     # yorgun yoksa taktik gerekceli degisiklik
    tired_threshold_v2: float = 70.0        # daha erken yorgunluk esigi: degisiklikler 45-85'e yayilir
    # Taktik degisiklik dizilisin hat sayilarini bozar (3 forvetli sona kalkma): guclu bir
    # hamledir, bu yuzden mac basina sinirlidir. Sinirsizken son yarim saatte iki takim da
    # surekli hucumcu sokup pozisyon sayisini sisiriyordu.
    max_tactical_swaps: int = 1

    # --- S5: yorgunluk / form dengesi (D10) ---
    fatigue_balance: bool = True
    fatigue_base_decay_v2: float = 0.52     # kondisyon tek basina her seyi belirlemesin
    fatigue_late_multiplier: float = 1.45   # ama son bolumde yine de hissedilsin
    condition_influence_v2: float = 0.40    # form/moral taktikle ayni buyukluk mertebesine gelsin
    condition_clamp_v2: tuple[float, float] = (0.84, 1.16)
    fresh_legs_bonus: float = 0.07          # oyuna yeni giren oyuncunun kisa sureli etkisi
    fresh_legs_minutes: int = 15

    # =======================================================================
    # 13B "anlatim" bayraklari
    # -----------------------------------------------------------------------
    # HEPSI False iken motor 13A (467661b) davranisiyla BIT-BIT aynidir: ayni tohum,
    # ayni skor, ayni olay listesi, ayni metin. Acikken SONUC RNG AKISI DEGISMEZ --
    # anlatim ayri bir rastgele akis kullanir ve eski metin cekilislerinin RNG
    # tuketimi `_legacy_text_draw()` ile birebir korunur (K11). Degisen yalnizca
    # (a) olay METINLERI ve (b) olay SAYISI (korner/faul/ofsayt/kurulus artik yaziliyor).
    # =======================================================================

    # --- anlatim verisi (K5, K6, D11): commentary.py bankasi + ayri anlatim RNG'si ---
    narration: bool = True
    narration_salt: int = 0x13B             # anlatim tohumu = crc32(seed | salt)

    # --- kurulus zincirleri (D11): bir atak 1-3 zincirli olay uretir ---
    build_up_chains: bool = True
    # Zincirin sekli, POZISYON cekilisinin artigindan turetilir (yeni rastgele sayi YOK).
    build_up_none_share: float = 0.40       # dogrudan sut (kurulus olayi yok; gol haric)
    build_up_two_share: float = 0.24        # iki halkali zincir (kalan: tek halka)

    # --- gosterim filtresi (K7): olay uretimi degil, AKIS seyreltilir ---
    flow_events: bool = True                # korner / faul / ofsayt / baski olay olarak yazilir
    show_miss: float = 0.42                 # siradan (net olmayan) isabetsiz sutlarin akistaki payi
    show_save: float = 0.92
    show_corner: float = 0.10
    show_foul: float = 0.04
    show_offside: float = 0.30
    show_build_up: float = 0.15             # gole giden zincirler HER ZAMAN gosterilir
    show_pressure: float = 0.34
    # Olu hava tabani: bu kadar dakika ust uste hicbir olay yazilmazsa oyunun akisini
    # anlatan bir ambiyans (BUILD_UP / pressure) satiri yazilir.
    ambient_after: int = 4

    # --- durust topla oynama (D12) ---
    possession_weighted: bool = True
    possession_control_alpha: float = 0.22  # hakimiyet EMA'sinin tepki hizi
    possession_amplitude: float = 1.90      # sekans agirliginin hakimiyete duyarligi
    possession_weight_range: tuple[float, float] = (0.20, 1.80)

    # =======================================================================
    # 14B "ozellikler motorda" (attribute_model.py)
    # -----------------------------------------------------------------------
    # ARTIK VARSAYILAN ACIK (14B §3.7, YENIDEN TEMELLENDIRME 3). Acikken 31 ozelligin 31'i ve gizli
    # sakatlik egilimi mevcut cekilislerin olasilik / agirliklarinda okunur (yeni rastgele sayi YOK);
    # tek dogru kaynak attribute_model.READERS. False yapmak motoru 13B'ye BIT-BIT geri dondurur:
    # MatchPlayer.sheet bos, attributes'a dokunulmaz, hicbir sey hesaplanmaz (kanit:
    # .claude/phase14/kanit/14B_evidence.txt, 2.200 mac).
    # =======================================================================
    attribute_model: bool = True
    attributes: AttributeModelConfig = field(default_factory=AttributeModelConfig)

    # =======================================================================
    # 14E "taktik etkisi ve karsi hamle" (instructions.MATCHUP_EFFECTS, concede_quality, ai_formation)
    # -----------------------------------------------------------------------
    # ARTIK VARSAYILAN ACIK (14E §5, YENIDEN TEMELLENDIRME 4; sahip karari K-S13, VETO: "hayir" denirse bu satiri
    # False yapmak yeter). False iken motor 14B ile BIT-BIT aynidir (kanit: .claude/phase14/kanit/14E_evidence.txt,
    # 2.200 mac). Acikken:
    #   (1) hacim ile kalite AYRI butce: sans yogunlugu (kalite takasi) savunanin YAPISAL gucunden (talimat ve
    #       rakibe bagli carpanlar haric) gelir; otobus rakibin sut hacmini dusurur ama kaliteyi geri vermez.
    #       Talimatin "kale onu" etkisi acik: TeamInstructions.concede_quality rakibin akan oyun sans netligini carpar.
    #   (2) gercek karsi hamleler: MATCHUP_EFFECTS (kapali blok x hucum yonu, pres x pas stili x tempo, kontra).
    #   (3) norm = sahadaki GERCEK rol sayimi (+ bos kalan yuvalar); dizilis tarzi da gercek sekilden. Taktik
    #       degisiklik hakki max_tactical_swaps_v2 (hat sinirli: en cok 3 forvet, en az 1 forvet).
    #   (4) AI dizilis de degistirir ve rakibin GORUNEN talimatina karsi hamle yapar (ai_tactics acikken;
    #       instructions.ai_formation, ai_counter_move).
    # Yeni rastgele sayi CEKILMEZ; yalniz olasilik girdileri degisir.
    # =======================================================================
    tactics_v2: bool = True
    max_tactical_swaps_v2: int = 3
    # Talimat ve karsi hamle carpanlarinin yansima keskinlikleri (yapisal guc kendi keskinligiyle kalir):
    #   sut HACMI (hucum / savunma carpanlari), topa sahip olma (orta saha carpanlari) ve sans NETLIGI (kendi
    #   talimatinin chance_quality'si, dogrusal carpan olarak ust). Talimat tablolari a^2 / (a^2 + b^2) icin kalibre
    #   edildi ve 13A'nin yogunluk takasi bu etkileri geri aliyordu; takas kalkinca keskinlikler 80 v 80 yayilim >=
    #   0.35 puan/mac, tek plan tavani <= +0.25, 70 v 85 savunma plani >= +0.05 ve AI olcutlerine gore secildi
    #   (.claude/phase14/kanit/14E_supurme.txt).
    instruction_sharpness_v2: float = 4.5
    instruction_possession_sharpness_v2: float = 2.4
    instruction_quality_exponent_v2: float = 2.5

    # =======================================================================
    # 15G "not modeli ve gizli ozellikler macta" (attribute_model.RatingModelConfig)
    # -----------------------------------------------------------------------
    # ARTIK VARSAYILAN ACIK (15G, YENIDEN TEMELLENDIRME 7; geri almak icin bu satiri False yapmak YETER).
    # False iken motor 14E ile BIT-BIT aynidir: tek bir gizli ozellik okunmaz, gunun formu cekilmez,
    # not eski formulle hesaplanir (kanit: .claude/phase14/kanit/15G_evidence_rm_off.txt, 2.200 mac). Acikken:
    #   (1) NOT PERFORMANSI ANLATIR: cekilen savunmacinin duellosu, kurtarisin netligi (K6: sayi
    #       gosterilmez) ve atak zincirindeki katki nota girer.
    #   (2) GIZLI OZELLIKLER: tutarlilik (oyuncu basina gunun formu, KENDI crc32 akisindan), onemli mac
    #       (eleme / final / derbi) ve mizac (kart agirligi). Ucu de 1-20, hicbiri sayi olarak gosterilmez;
    #       FM verisi varsa ondan, yoksa transfer_rules.hidden_trait ile kimlikten gelir.
    # Macin RNG'sine yeni cekilis EKLENMEZ: gunun formu ve zincir katkisi ayri tohumlu crc32 akislarindan
    # gelir, geri kalan her sey mevcut olasilik ve agirliklarin carpanidir.
    # =======================================================================
    rating_model: bool = True
    ratings: RatingModelConfig = field(default_factory=RatingModelConfig)


ROLE_WEIGHTS: dict[str, dict[Position, float]] = {
    "attack":   {Position.FWD: 1.00, Position.MID: 0.55, Position.DEF: 0.12, Position.GK: 0.00},
    "midfield": {Position.MID: 1.00, Position.FWD: 0.45, Position.DEF: 0.45, Position.GK: 0.05},
    "defense":  {Position.DEF: 1.00, Position.MID: 0.50, Position.FWD: 0.12, Position.GK: 0.00},
}

# _player_strength onbellek yuvalari: state[2..4]
_KIND_INDEX: dict[str, int] = {"attack": 2, "midfield": 3, "defense": 4}


@cache
def _formation_norm(formation: tuple[int, int, int], kind: str) -> float:
    """Tam kadronun rol agirliklari toplami (saf fonksiyon, onbellekli)."""
    w = ROLE_WEIGHTS[kind]
    d, m, f = formation
    return w[Position.GK] + d * w[Position.DEF] + m * w[Position.MID] + f * w[Position.FWD]


ROLE_FATIGUE: dict[Position, float] = {
    Position.MID: 1.10, Position.FWD: 1.00, Position.DEF: 0.90, Position.GK: 0.30,
}

# Kornerde ceza sahasina kimin girecegi: stoperler ve forvetler, orta saha daha az
AERIAL_ROLE_WEIGHT: dict[Position, float] = {
    Position.DEF: 1.00, Position.FWD: 1.00, Position.MID: 0.55, Position.GK: 0.02,
}

# --- 13A / S2: rol gercekciligi (EngineConfig.role_realism) --------------------------
# Akan oyunda SUTU kim ceker. ROLE_WEIGHTS["attack"] takim gucu icin dogru agirliktir
# ama sutor secimi icin fazla duzdur: 4 orta saha x 0.55 = 2.20, 2 forvet x 1.00 = 2.00
# oldugu icin orta saha forvetten cok gol atiyordu (gercek: FWD ~%50, MID ~%35, DEF ~%13).
SHOOTER_ROLE_WEIGHT: dict[Position, float] = {
    Position.FWD: 1.00, Position.MID: 0.68, Position.DEF: 0.125, Position.GK: 0.00,
}
# Asisti kim yapar. Eskiden rol agirligi HIC yoktu: sahada 4 savunmaci, 2 forvet oldugu
# icin asistlerin %42.5'i savunmadan geliyordu (gercek ~%23 / %45 / %30).
ASSIST_ROLE_WEIGHT: dict[Position, float] = {
    Position.FWD: 0.84, Position.MID: 0.35, Position.DEF: 0.24, Position.GK: 0.02,
}

# --- 13A / S3: zayif halka (EngineConfig.weak_link) ----------------------------------
# Suta karsi hangi savunmacinin cekilecegi. Stoperler cogunlukla, orta saha bazen,
# forvet neredeyse hic. "En iyi dort savunmacinin ortalamasi" yerine BELIRLI bir oyuncu.
DEFENCE_CONTEST_WEIGHT: dict[Position, float] = {
    Position.DEF: 1.00, Position.MID: 0.30, Position.FWD: 0.04, Position.GK: 0.00,
}

# --- 15G: atak zincirinde kimin yer aldigi (EngineConfig.rating_model; yalnizca NOTA girer) ----------
# Topu kazanan, araligi bulan, son pasi hazirlayan. Orta saha en cok, kaleci hic. Sonuca (skor, olay,
# istatistik) HIC etki etmez: yalnizca notun "zincir katkisi" terimini dagitir.
# Agirliklar RatingModelConfig.chain_weight'ten gelir (attribute_model.chain_weights, ayar basina onbellekli).


def _chain_pool(players: Sequence[MatchPlayer],
                weights: dict[Position, float]) -> tuple[list[tuple[MatchPlayer, float]], float]:
    """15G: sahadaki saha oyuncularinin (oyuncu, zincir agirligi) listesi + toplami (kadro onbellegine girer)."""
    pool = [(p, weights[p.role or p.position]) for p in players]
    return pool, sum(w for _, w in pool)

# Dizilis tarzi carpanlari (tam kadro normalizasyonundan SONRA uygulanir).
# 4-3-3 hucumu acar ama savunmayi inceltir; 3-5-2 orta sahayi doldurur,
# kanatlar savunmada acik kalir. 4-4-2 dengeli referans. 5-3-2 (yalnizca mac ici,
# acil durum) savunmayi kalinlastirir, hucum ve orta saha zayiflar.
FORMATION_STYLE: dict[tuple[int, int, int], dict[str, float]] = {
    (4, 4, 2): {"attack": 1.00, "midfield": 1.00, "defense": 1.00},
    (4, 3, 3): {"attack": 1.08, "midfield": 0.96, "defense": 0.94},
    (3, 5, 2): {"attack": 1.03, "midfield": 1.06, "defense": 0.93},
    (5, 3, 2): {"attack": 0.92, "midfield": 0.95, "defense": 1.10},
}

# 14E (EngineConfig.tactics_v2): dizilis tarzi GERCEK sekilden okunur. Tabloda olmayan sekil (orn. taktik
# degisiklikle 3-4-3 ya da 4-5-1) tablodaki dort sekilden turetilen dogrusal egimle: 4-4-2'ye gore (fazla defans
# basina, fazla forvet basina) degisim; [0.85, 1.15] araliginda.
SHAPE_STYLE_SLOPES: dict[str, tuple[float, float]] = {
    "attack": (-0.055, 0.08),
    "midfield": (-0.055, -0.04),
    "defense": (0.085, -0.06),
}
SHAPE_STYLE_RANGE: tuple[float, float] = (0.85, 1.15)


@cache
def _shape_norm(shape: tuple[int, int, int, int], kind: str) -> float:
    """14E: yuva sayimi (GK, DEF, MID, FWD) icin rol agirliklari toplami. GK=1 iken _formation_norm ile BIREBIR."""
    w = ROLE_WEIGHTS[kind]
    gk, d, m, f = shape
    return gk * w[Position.GK] + d * w[Position.DEF] + m * w[Position.MID] + f * w[Position.FWD]


def _shape_style(shape: tuple[int, int, int], kind: str) -> float:
    """14E: gercek sekil (DEF, MID, FWD) icin dizilis tarzi carpani (tablodaki sekillerde FORMATION_STYLE aynen;
    tablo her cagrida okunur, eski yol gibi)."""
    style = FORMATION_STYLE.get(shape)
    if style is not None:
        return style.get(kind, 1.0)
    return _off_table_style(shape, kind)


@cache
def _off_table_style(shape: tuple[int, int, int], kind: str) -> float:
    per_def, per_fwd = SHAPE_STYLE_SLOPES[kind]
    lo, hi = SHAPE_STYLE_RANGE
    return max(lo, min(hi, 1 + per_def * (shape[0] - 4) + per_fwd * (shape[2] - 2)))


_TEMPO_INDEX = {Tempo.SLOW: 0, Tempo.NORMAL: 1, Tempo.FAST: 2}

# 13B: anlatim icin oyuncu secme agirliklari. KOZMETIKTIR (istatistige yazilmaz),
# anlatim RNG'sinden cekilir; sonuc akisina dokunmaz.
_FLAVOUR_ROLE_WEIGHT: dict[str, dict[Position, float]] = {
    "build":  {Position.FWD: 0.55, Position.MID: 1.00, Position.DEF: 0.35, Position.GK: 0.00},
    "attack": {Position.FWD: 1.00, Position.MID: 0.45, Position.DEF: 0.08, Position.GK: 0.00},
    "defend": {Position.FWD: 0.30, Position.MID: 0.90, Position.DEF: 1.00, Position.GK: 0.02},
    "wide":   {Position.FWD: 0.70, Position.MID: 1.00, Position.DEF: 0.45, Position.GK: 0.00},
}


def _cumulative(pairs: tuple[tuple[str, int], ...]) -> tuple[tuple[str, ...], tuple[float, ...]]:
    total = sum(w for _, w in pairs)
    acc, cum = 0.0, []
    for _, w in pairs:
        acc += w / total
        cum.append(acc)
    cum[-1] = 1.0 + 1e-9                     # yuvarlama: random() < 1 her zaman bir kova bulur
    return tuple(name for name, _ in pairs), tuple(cum)


# 13B: sans netligi -> sutun BICIMI (kozmetik; anlatim RNG'si). Net sans cogunlukla karsi
# karsiya / ceza sahasi, zayif sans cogunlukla uzaktan. Duran toplar bicimi kendisi belirler.
_SHOT_FORMS: dict[str, tuple[tuple[str, ...], tuple[float, ...]]] = {
    "big": _cumulative((("oneonone", 34), ("box", 34), ("tap", 16), ("volley", 9), ("header", 7))),
    "good": _cumulative((("box", 44), ("oneonone", 16), ("header", 18), ("volley", 10), ("long", 12))),
    "normal": _cumulative((("box", 40), ("long", 32), ("header", 15), ("volley", 7), ("solo", 6))),
    "far": _cumulative((("long", 62), ("box", 20), ("header", 11), ("solo", 7))),
}

_FLAVOUR_KEYS = {kind: f"flavour:{kind}" for kind in _FLAVOUR_ROLE_WEIGHT}


def _flavour_table(team: MatchTeam, kind: str) -> tuple[list[MatchPlayer], list[float]]:
    """Kadro degisene kadar (MatchTeam._cached) gecerli kumulatif agirlik tablosu."""
    weights = _FLAVOUR_ROLE_WEIGHT[kind]
    pool = [p for p in team.outfield_on_pitch if weights[p.role or p.position] > 0]
    acc, cum = 0.0, []
    for p in pool:
        acc += weights[p.role or p.position]
        cum.append(acc)
    return pool, cum


_BUILD_UP_KEYS = {part: f"buildup.{part}" for part in ("win", "entry", "final", "pressure")}
# gol farki (sinirli -1..3) -> olay anindaki oyun durumu etiketi
_GAME_STATE = {-1: "pullback", 0: "level", 1: "ahead", 2: "extend", 3: "seal"}
_SHOT_KEYS = {"miss": "miss.open", "save": "save.open"}

_SHOT_CONTEXTS: dict[tuple, tuple[frozenset[str], frozenset[str]]] = {}


def _shot_context(quality: str, form: str, minute: str, defended: bool,
                  state: str) -> tuple[frozenset[str], frozenset[str]]:
    """(sut bicimi etiketleri, + oyun durumu). Sinirli sayida birlesim: bir kez kurulur."""
    key = (quality, form, minute, defended, state)
    cached = _SHOT_CONTEXTS.get(key)
    if cached is None:
        base = (quality, form, minute, "defended") if defended else (quality, form, minute)
        cached = _SHOT_CONTEXTS[key] = (frozenset(base), frozenset((*base, state)))
    return cached


# Eksik oyuncuyla dizilis kurulurken slot dusurme sirasi ve hatlarin korunacak asgari sayisi
_DROP_ORDER = (Position.FWD, Position.MID, Position.DEF)
_MIN_LINE = {Position.FWD: 1, Position.MID: 2, Position.DEF: 2}


class _NarrationState:
    """13B anlatim durumu (motor basina bir tane). Sonuc RNG'sine DOKUNMAZ."""

    __slots__ = ("narrator", "chain_id", "control", "quiet", "defender", "quality",
                 "shot_tags", "shot_ctx", "sp_tags", "report", "ctx_key", "ctx_home", "ctx_away",
                 "token", "last_lazy", "assister")

    def __init__(self, narrator: commentary.Narrator, seed: int) -> None:
        self.narrator = narrator
        self.token = seed << 24               # tembel olaylarin olaya ozel sayisi (LazyEvent)
        self.last_lazy: dict[str, LazyEvent] = {}     # kova -> son tembel olay (halka zinciri)
        self.assister: MatchPlayer | None = None     # son akan oyun golunun asistcisi (zincir)
        self.chain_id = 0                     # atak pasaji sayaci (follow-on zinciri)
        self.control = 0.5                    # hakimiyet EMA'si: 1.0 = ev sahibi, 0.0 = deplasman
        self.quiet = 0                        # ust uste olaysiz gecen dakika (olu hava tabani)
        self.defender: MatchPlayer | None = None     # o sutta cekilen savunmaci ({d})
        self.quality = 1.0                    # K6: saklanir, ASLA sayi olarak gosterilmez
        self.shot_tags: frozenset[str] = frozenset()   # sutun bicimi: kalite + bicim + dakika
        self.shot_ctx: frozenset[str] = frozenset()    # + sut ANINDAKI oyun durumu (gol oncesi)
        self.sp_tags: frozenset[str] = frozenset()  # duran top cumlesinin baglami
        self.report = ""                      # son uretilen gecmis zaman rapor cumlesi
        self.ctx_key: tuple[int, int, int] | None = None      # dusuk etkili olay baglami onbellegi
        self.ctx_home: frozenset[str] = frozenset()
        self.ctx_away: frozenset[str] = frozenset()


def _occasion_of(home: MatchTeam, away: MatchTeam, knockout: KnockoutRule | None,
                 neutral_venue: bool) -> str:
    """
    15G: macin agirligi, motorun ZATEN bildigi gercekten. Cagiran katman acikca da verebilir
    (MatchEngine(..., occasion="final")). Lig macinda gizli "onemli mac" ozelligi etkisizdir.
    """
    if knockout is not None:
        return attribute_model.OCCASION_FINAL if neutral_venue else attribute_model.OCCASION_KNOCKOUT
    if away.id in home.rivals or home.id in away.rivals:
        return attribute_model.OCCASION_DERBY
    return attribute_model.OCCASION_LEAGUE


class MatchEngine:
    def __init__(
        self,
        home: MatchTeam,
        away: MatchTeam,
        seed: int | None = None,
        config: EngineConfig | None = None,
        knockout: KnockoutRule | None = None,
        neutral_venue: bool = False,
        *,
        occasion: str | None = None,
        home_plan: MatchPlan | None = None,
        away_plan: MatchPlan | None = None,
        home_roles: SetPieceRoles | None = None,
        away_roles: SetPieceRoles | None = None,
    ) -> None:
        self.cfg = config or EngineConfig()
        # 14B: ozellik modeli ayari (bayrak kapaliyken None: hicbir kanca calismaz)
        self._am: AttributeModelConfig | None = self.cfg.attributes if self.cfg.attribute_model else None
        # 15G: not modeli ayari (bayrak kapaliyken None: tek bir gizli ozellik bile okunmaz)
        self._rm: RatingModelConfig | None = self.cfg.ratings if self.cfg.rating_model else None
        self.rng = random.Random(seed)
        self.seed = seed
        self.home = home
        self.away = away
        self.knockout = knockout
        self.neutral_venue = neutral_venue
        self.occasion = occasion or _occasion_of(home, away, knockout, neutral_venue)
        self.home.is_home, self.away.is_home = True, False
        for team, plan, roles in ((self.home, home_plan, home_roles), (self.away, away_plan, away_roles)):
            if team.formation is None:
                team.formation = self.cfg.formation
            if plan is not None:
                team.plan = plan
                team.plan_fired.clear()
            if roles is not None:
                team.roles = roles
        self._ai_state: dict[int, tuple[int, int, int]] = {}    # takim id -> (gol farki, oyuncu sayisi, rakip sayisi)
        self._ai_shape_changes = [0, 0]                          # 14E: AI dizilis degisikligi (ev, deplasman)
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
        # --- adim adim akis (9. Asama) ---
        self.phase = MatchPhase.NOT_STARTED
        self._runner: Iterator[None] | None = None
        self._result: MatchResult | None = None
        self._tick = 0                 # oynanan dakika sayaci = duraklama (pencere) anahtari
        self._density = 1.0            # o atagin sans yogunlugu -> kalite takasi (13A / S4)
        self._break_tick: int | None = None   # son molanin duraklama anahtari (pencere saymaz)
        # --- 13B anlatim: SONUC RNG'SINDEN AYRI akis (K11). Durum tek bir __slots__ nesnesinde
        # tutulur: motorun ornek ozellik sayisi 30'u asarsa CPython paylasimli anahtar / satir ici
        # deger hizlandirmasini kaybeder ve TUM `self.x` erisimleri yavaslar (olculdu).
        narration_seed = zlib.crc32(f"{seed}|{self.cfg.narration_salt}".encode())
        self._nar = _NarrationState(commentary.Narrator(narration_seed), narration_seed)
        # 15G: zincir katkisi kendi tohumlu akisindan cekilir (sonucun RNG'sine ve anlatima dokunmaz;
        # bayrak kapaliyken hic kurulmaz). Macin agirligi gizli "onemli mac" ozelliginin carpanidir.
        self._chain_rng: random.Random | None = None
        self._occasion_weight = 0.0
        self._chain_w: dict[Position, float] = {}
        if self._rm is not None:
            self._chain_rng = random.Random(zlib.crc32(f"15G|chain|{seed}".encode()))
            self._occasion_weight = attribute_model.occasion_weight(self.occasion)
            self._chain_w = attribute_model.chain_weights(self._rm)
        for team in (home, away):
            self._prepare_team(team)

    # ------------------------------------------------------------------ hazirlik

    def _prepare_team(self, team: MatchTeam) -> None:
        neutral = (self.cfg.neutral_form / 100) * (self.cfg.neutral_morale / 100)
        # D10: kondisyon oyundaki en guclu kaldiracti, form ve moral bilerek +-%12'ye
        # kisilmisti. fatigue_balance acikken ikisi ayni buyukluk mertebesine gelir.
        influence = (self.cfg.condition_influence_v2 if self.cfg.fatigue_balance
                     else self.cfg.condition_influence)
        lo, hi = self.cfg.condition_clamp_v2 if self.cfg.fatigue_balance else self.cfg.condition_clamp
        am = self._am
        rm = self._rm
        weight = self._occasion_weight
        for p in team.players:
            ratio = p.raw_condition / neutral
            p.condition_factor = max(lo, min(hi, 1 + influence * (ratio - 1)))
            # Mac basi enerji = kondisyon; kadro secimi (select_lineup) bunu zaten gorur
            p.energy = float(p.condition)
            p.energy_log.clear()
            if am is not None:
                # 14B: sayfa (profil ile ayni), team_roles icin attributes, dayaniklilik, kanal carpanlari
                attribute_model.prepare_player(p, am)
            else:
                p._am = None                # bayrak kapali: onceki (acik) bir mactan kalan carpan okunmaz
            if rm is not None:
                # 15G: gizli ucluk + gunun formu (oyuncunun KENDI crc32 akisindan; mac RNG'si tuketilmez)
                p._trait = trait = attribute_model.hidden_traits(p, rm)
                p._day_form = attribute_model.day_form(self.seed, p.id, trait.form_sigma, rm)
                p._big_game = attribute_model.big_game_factor(trait.big_match, weight, rm)
            else:
                p._trait = None             # bayrak kapali: onceki bir mactan kalan hicbir sey okunmaz
                p._day_form = p._big_game = 1.0
            p._duel_credit = p._chain_credit = p._keeper_credit = 0.0
            p.reset_strength_cache()        # condition_factor degisti: guc onbellegi bosalir
        team.touch_lineup()
        team.open_slots.clear()
        team.select_lineup()
        team.reset_open_slots()             # 14E: kadro 11'e tamamlanamadiysa bos yuvalar

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

    @property
    def max_windows(self) -> int | None:
        """Oyun sirasinda degisiklik penceresi siniri (uzatmada +1). None: pencere kurali yok."""
        if self.cfg.sub_windows is None:
            return None
        return self.cfg.sub_windows + (self.cfg.extra_time_extra_windows if self.in_extra_time else 0)

    @property
    def in_break(self) -> bool:
        return self.phase in BREAK_PHASES

    @property
    def _free_window(self) -> bool:
        """
        Pencere saymayan duraklama: mola (devre arasi, uzatma molalari) ya da moladan sonra top
        henuz oyuna girmeden (ornegin 46. / 91. dakikanin basinda) yapilan degisiklik.
        """
        return self.in_break or (self._break_tick is not None and self._tick == self._break_tick)

    @property
    def started(self) -> bool:
        return self.phase is not MatchPhase.NOT_STARTED

    @property
    def finished(self) -> bool:
        return self.phase is MatchPhase.FINISHED

    def team_by_id(self, team_id: int) -> MatchTeam:
        for team in (self.home, self.away):
            if team.id == team_id:
                return team
        raise InterventionError(f"Bu maçta #{team_id} numaralı takım yok.")

    # ------------------------------------------------------------------ degisiklik kurallari

    def _window_key(self) -> int:
        """
        Su anki duraklamanin anahtari. Bir dakikanin icindeki degisiklikler (sakatlik, kirmizi kart),
        o dakikanin ardindaki menajer duraklamasi ve sonraki dakikanin basindaki asistan (yorgunluk)
        degisiklikleri arasinda top oyunda degildir: hepsi AYNI duraklama, ayni pencere.
        """
        return self._tick

    def substitution_block(self, team: MatchTeam, reserve_window: bool = False) -> str | None:
        """
        Takim su an degisiklik yapamiyorsa sebep ('değişiklik hakkı kalmadı' ...), yapabiliyorsa None.
        Ayni duraklamada acilmis pencere yeni pencere saymaz; molalar (devre arasi) hic saymaz.
        reserve_window: asistanin yorgunluk degisikligi son pencereyi acil durum icin saklar.
        """
        if team.subs_used >= self.max_subs:
            return "değişiklik hakkı kalmadı"
        windows = self.max_windows
        if windows is None or self._free_window or team.window_key == self._window_key():
            return None
        if team.sub_windows_used >= windows - (1 if reserve_window else 0):
            return "değişiklik penceresi kalmadı"
        return None

    def _register_sub(self, team: MatchTeam) -> None:
        team.subs_used += 1
        team.stats.substitutions += 1
        if self.max_windows is not None and not self._free_window:
            key = self._window_key()
            if team.window_key != key:
                team.window_key = key
                team.sub_windows_used += 1

    # ------------------------------------------------------------------ yardimcilar

    def _log(self, type_: EventType, team: MatchTeam | None, player: MatchPlayer | None, desc: str,
             detail: str | None = None, **meta: Any) -> MatchEvent:
        if "dwell_ms" not in meta:
            meta["dwell_ms"] = _TYPE_DWELL.get(type_.value, DWELL_MAIN)
        ev = MatchEvent(
            minute=self.minute, added_time=self.added, type=type_,
            team=team.name if team else None, player=player.name if player else None,
            description=desc, team_id=team.id if team else None,
            player_id=player.id if player else None,
            home_score=self.home.stats.goals, away_score=self.away.stats.goals,
            detail=detail, **meta,
        )
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------------ 13B anlatim

    def _legacy_text_draw(self, size: int | None) -> None:
        """
        12. Asama metin cekilislerinin RNG TUKETIMINI korur.

        Anlatim ayri bir rastgele akisa tasindi (K11) ama eski kod cumleyi `self.rng`
        ile seciyordu. O cekilisleri kaldirmak SONUC AKISINI kaydirirdi: ayni tohum
        baska bir skor uretirdi. Bu yuzden cekilis aynen yapilir, sonucu atilir --
        boylece 13B'de skorlar, oyuncu istatistikleri ve kartlar bit-bit korunur.
            size=None  -> eski `rng.choices(..., k=1)` (tek `random()`)
            size=n     -> eski `rng.choice(n uzunlugunda dizi)` (ayni `_randbelow`)
        """
        if size is None:
            self.rng.random()
        else:
            self.rng._randbelow(size)          # = rng.choice(n ogeli dizi): ayni tuketim

    def _minute_tag(self) -> str:
        """Dakika bandi etiketi (anlatim baglami)."""
        if self.added > 0:
            return "stoppage"
        minute = self.minute
        if minute > 90:
            return "extra"
        if minute <= 15:
            return "early"
        if minute <= 45:
            return "firsthalf"
        if minute <= 70:
            return "hour"
        if minute <= 85:
            return "late"
        return "closing"

    @staticmethod
    def _quality_tag(quality: float) -> str:
        """K6: kalite SAYI olarak gosterilmez, yalnizca hangi cumlenin secilecegini belirler."""
        if quality >= 1.18:
            return "big"
        if quality >= 0.92:
            return "good"
        if quality >= 0.66:
            return "normal"
        return "far"

    def _shot_form_tag(self, quality_tag: str, kind: str | None) -> str:
        """
        Sutun BICIMI (kafa / uzaktan / karsi karsiya ...). Tamamen KOZMETIKTIR: sonucu
        degistirmez, bu yuzden anlatim RNG'sinden cekilir. Duran topta bicim bellidir,
        boylece "yerden cekilen sut" asla kafa vurusu diye anlatilmaz.
        """
        if kind == "corner":
            return "header"
        if kind in ("penalty", "free_kick"):
            return "box"
        forms, cum = _SHOT_FORMS[quality_tag]
        return forms[bisect_right(cum, self._nar.narrator.rng.random())]

    def _score_state_tag(self, team: MatchTeam) -> str:
        """Golun skor tablosundaki anlami (gol YAZILDIKTAN sonra cagrilir)."""
        mine, theirs = team.stats.goals, self._opponent(team).stats.goals
        if mine == 1 and theirs == 0:
            return "first"
        if mine == theirs:
            return "equalise"
        if mine < theirs:
            return "pullback"
        if mine - theirs == 1:
            return "ahead"
        if mine - theirs == 2:
            return "extend"
        return "seal"

    def _game_state_tag(self, team: MatchTeam) -> str:
        """
        Olay ANINDAKI oyun durumu (gol disi olaylar icin): level / pullback / ahead /
        extend / seal. `_score_state_tag` ise bir golun skor tablosundaki ANLAMIDIR.
        """
        return _GAME_STATE[max(-1, min(3, team.stats.goals - self._opponent(team).stats.goals))]

    def _slots(self, team: MatchTeam | None = None, player: MatchPlayer | None = None,
               keeper: MatchPlayer | None = None, assister: MatchPlayer | None = None,
               defender: MatchPlayer | None = None, **extra: str) -> commentary.Slots:
        slots = commentary.Slots(extra)
        if player is not None:
            slots["p"] = player.name
        if team is not None:
            slots["t"] = team.name
            slots["o"] = self._opponent(team).name
        if keeper is not None:
            slots["gk"] = keeper.name
        elif team is not None:
            slots["gk"] = "kaleci"
        if assister is not None:
            slots["a"] = assister.name
        if defender is not None:
            slots["d"] = defender.name
        return slots

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
        """
        Oyuncunun o turdeki guc katkisi. 13A/S1: (enerji, rol) basina onbellekli.
        Carpim sirasi eski formulle AYNI: ((base*condition_factor) * fatigue * rol * mevki).
        """
        state = p._strength_state
        if state[0] != p.energy or state[1] is not p.role:
            state[0], state[1] = p.energy, p.role
            state[2] = state[3] = state[4] = None
        index = _KIND_INDEX[kind]
        value = state[index]
        if value is None:
            base = p._base_strength.get(kind)
            if base is None:
                rating = {"attack": p.attack_rating, "midfield": p.midfield_rating,
                          "defense": p.defense_rating}[kind]
                base = (0.4 * p.overall + 0.6 * rating) * p.condition_factor
                if p._am is not None:
                    base *= getattr(p._am, kind)      # 14B: hucum / orta saha / savunma kanali
                if p._big_game != 1.0:
                    # 15G: macin agirligi (onemli mac) oyuncunun o macki seviyesidir. Bayrak kapaliyken
                    # tam 1.0 oldugu icin bu carpim HIC yapilmaz: kayan nokta bit-bit korunur.
                    # Gunun formu (tutarlilik) BILEREK burada DEGIL: takim gucu toplamina girseydi 11
                    # oyuncunun gunu takim gucunu oynatir ve +20 farkta surpriz orani bandi asardi
                    # (olculdu). Gunun formu oyuncunun KENDI anlarinda okunur: isabet, bitiricilik,
                    # duello direnci, kaleci.
                    base *= p._big_game
                p._base_strength[kind] = base
            role = p.role or p.position
            penalty = 1.0 if role is p.position else self.cfg.out_of_position_penalty
            value = state[index] = base * p.fatigue_factor * ROLE_WEIGHTS[kind][role] * penalty
        return value

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
            if kind == "attack":
                return self.cfg.trailing_attack_boost
            drop = self.cfg.trailing_defense_drop
            if team.roles.captain_id is not None and team.captain_on_pitch:
                # Kaptan sahada: panik azalir, savunma dususunun bir kismi silinir
                relief = self.cfg.captain_trailing_relief
                if self._am is not None:           # 14B: liderlik kaptan etkisini olcekler
                    relief = min(1.0, relief * self._captain_scale(team))
                drop = 1 - (1 - drop) * (1 - relief)
            return drop
        if diff > 0:
            return self.cfg.leading_attack_drop if kind == "attack" else self.cfg.leading_defense_boost
        return 1.0

    def _formation_norm(self, team: MatchTeam, kind: str) -> float:
        """
        Tam kadro (11) icin rol agirliklari toplami. Takim gucunu 'oyuncu basina'
        olcege indirger; boylece esit iki takimin hucum ve savunma degerleri esit
        cikar (4-4-2'de ham toplamlar 4.68'e 6.24 idi -> savunma hep kazaniyordu).
        """
        if self.cfg.tactics_v2:
            return _shape_norm(self._shape(team), kind)
        return _formation_norm(team.formation, kind)

    def _shape(self, team: MatchTeam) -> tuple[int, int, int, int]:
        """
        14E: yuva sayimi (GK, DEF, MID, FWD) = sahadakilerin GERCEK rolleri + bos yuvalar (MatchTeam.open_slots).
        Taktik degisiklik (orta saha -> forvet) ya da rolu degisen degisiklikten sonra ilan edilen dizilisten ayrilir.
        Kadro degisene kadar gecerli (touch_lineup onbellegi siler).
        """
        cache = team._lineup_cache
        if cache.get("v") == team._lineup_version:
            shape = cache.get("shape")
            if shape is not None:
                return shape

        def build() -> tuple[int, int, int, int]:
            counts = {Position.GK: 0, Position.DEF: 0, Position.MID: 0, Position.FWD: 0}
            for p in team.on_pitch:
                counts[p.role or p.position] += 1
            for role in team.open_slots:
                counts[role] += 1
            return counts[Position.GK], counts[Position.DEF], counts[Position.MID], counts[Position.FWD]

        return team._cached("shape", build)

    def _freshness(self, p: MatchPlayer) -> float:
        """Oyuna yeni giren oyuncunun kisa sureli etkisi (13A/S5, D5: taze bacak is gorsun)."""
        entered = p.entered_minute
        if entered and self.minute - entered <= self.cfg.fresh_legs_minutes:
            return 1.0 + self.cfg.fresh_legs_bonus
        return 1.0

    def _team_strength(self, team: MatchTeam, kind: str, structural: bool = False) -> float:
        if self.cfg.tactics_v2:
            # 14E: talimat ve rakibe bagli carpanlar YAPISAL gucun ustune tek carpan olarak biner (varsayilan
            # talimatta carpan tam 1.0: sonuc bayrak kapali formulle bit-bit ayni).
            base = self._structural_strength(team, kind)
            if structural:
                return base
            return base * (team.instructions.strength_factor(kind) * self._matchup_factor(team, kind))
        # 13B hiz: uretec yerine liste (PEP 709 satir ici) ve `_freshness` satir ici. Ayni carpimlar,
        # ayni sira, ayni `sum()` -> sonuc BIT-BIT ayni (tests: altin tohumlar + kanit betigi).
        strength = self._player_strength
        if self.cfg.fatigue_balance:
            minute, window = self.minute, self.cfg.fresh_legs_minutes
            fresh = 1.0 + self.cfg.fresh_legs_bonus
            total = sum([strength(p, kind) * (fresh if (entered := p.entered_minute)
                                              and minute - entered <= window else 1.0)
                         for p in team.on_pitch])
        else:
            total = sum([strength(p, kind) for p in team.on_pitch])
        total /= max(self._formation_norm(team, kind), 0.01)
        total *= FORMATION_STYLE.get(team.formation, {}).get(kind, 1.0)
        missing = max(0, 11 - team.player_count)
        if missing:
            penalty = (self.cfg.short_handed_penalty_v2 if self.cfg.discipline_v2
                       else self.cfg.short_handed_penalty)
            total *= penalty ** missing
        if team.is_home:
            if kind == "midfield":
                total *= self.home_advantage
            elif kind == "attack" and self.cfg.flat_superiority:
                # D9: ev avantaji yalnizca orta sahadaydi; topa sahip olma keskinligi
                # dustugu icin artik hucuma da bir payi yansir.
                total *= 1 + (self.home_advantage - 1) * self.cfg.home_attack_share
        total *= team.instructions.strength_factor(kind)      # talimatlar (varsayilan tam 1.0)
        total *= self._matchup_factor(team, kind)             # rakibe bagli talimat etkileri (varsayilan 1.0)
        if team.match_form != 1.0:
            total *= team.match_form                          # gunun formu (13A / S4)
        if self._am is not None:
            # 14B: takim uyumu (tum turler) ve rakibin presi (caliskanlik) orta sahayi dusurur
            total *= self._am_team(team).cohesion
            if kind == "midfield":
                total /= self._am_team(self.away if team is self.home else self.home).press
        return total * self._situation_factor(team, kind)

    def _structural_strength(self, team: MatchTeam, kind: str) -> float:
        """
        14E: takim gucu, talimat (strength_factor) ve rakibe bagli (_matchup_factor) carpanlar HARIC. Carpim sirasi
        _team_strength ile ayni; norm ve dizilis tarzi GERCEK sekilden (_shape). Sans yogunlugu (kalite takasi)
        bunu okur: kapanan takim rakibin sut hacmini dusurur ama kalitesini geri vermez.
        """
        strength = self._player_strength
        if self.cfg.fatigue_balance:
            minute, window = self.minute, self.cfg.fresh_legs_minutes
            fresh = 1.0 + self.cfg.fresh_legs_bonus
            total = sum([strength(p, kind) * (fresh if (entered := p.entered_minute)
                                              and minute - entered <= window else 1.0)
                         for p in team.on_pitch])
        else:
            total = sum([strength(p, kind) for p in team.on_pitch])
        shape = self._shape(team)
        total /= max(_shape_norm(shape, kind), 0.01)
        total *= _shape_style(shape[1:], kind)
        missing = max(0, 11 - team.player_count)
        if missing:
            penalty = (self.cfg.short_handed_penalty_v2 if self.cfg.discipline_v2
                       else self.cfg.short_handed_penalty)
            total *= penalty ** missing
        if team.is_home:
            if kind == "midfield":
                total *= self.home_advantage
            elif kind == "attack" and self.cfg.flat_superiority:
                total *= 1 + (self.home_advantage - 1) * self.cfg.home_attack_share
        if team.match_form != 1.0:
            total *= team.match_form
        if self._am is not None:
            total *= self._am_team(team).cohesion
            if kind == "midfield":
                total /= self._am_team(self.away if team is self.home else self.home).press
        return total * self._situation_factor(team, kind)

    # ------------------------------------------------------------------ 14B ozellik modeli

    def _am_team(self, team: MatchTeam) -> attribute_model.TeamFactors:
        """Takim olcekli kanallar (sahadakilerden). Kadro degisene kadar gecerli (MatchTeam._cached; sicak yol
        onbellege dogrudan bakar)."""
        cache = team._lineup_cache
        if cache.get("v") == team._lineup_version:
            factors = cache.get("am")
            if factors is not None:
                return factors
        return team._cached("am", lambda: attribute_model.team_factors(team.on_pitch, self._am))

    def _captain_scale(self, team: MatchTeam) -> float:
        """Sahadaki kaptanin liderlik carpani (kaptan etkilerinin olcegi); kaptan yoksa 1.0."""
        captain = team.on_pitch_by_id(team.roles.captain_id)
        return captain._am.captain if captain is not None and captain._am is not None else 1.0

    def _keeper_strength(self, team: MatchTeam) -> float:
        gk = team.keeper
        if gk is None:
            return 5.0  # bos kale
        penalty = 1.0 if gk.position is Position.GK else self.cfg.out_of_position_penalty
        strength = gk.gk_rating * gk.condition_factor * gk.fatigue_factor * penalty
        if gk._day_form != 1.0 or gk._big_game != 1.0:
            strength *= gk._day_form * gk._big_game      # 15G: kalecinin gunu ve macin agirligi
        return strength

    # ------------------------------------------------------------------ talimat etkilesimleri

    def _matchup_factor(self, team: MatchTeam, kind: str) -> float:
        """
        Rakibe bagli talimat carpani (varsayilan talimatlarda tam 1.0):
            midfield  rakibin presi (tum sahada: orta saha zayiflar), pas stiline gore yansir
            attack    kontra atak: rakip acik oynuyorsa guclenir
            defense   ofsayt taktigi: rakip forvetlerin hizina gore
        """
        inst = team.instructions
        if kind == "midfield":
            press = self._opponent(team).instructions.pressing_effect.opponent_midfield
            if press != 1.0:
                exposure = inst.passing_effect.press_exposure
                if press < 1.0 and self.cfg.tactics_v2:
                    # 14E (c): tum saha pres x tempo. Sabirli (yavas) kisa pas presi bosa cikarir, hizli kisa pas
                    # pres altinda top kaybeder.
                    exposure *= MATCHUP_EFFECTS.press_tempo_exposure[_TEMPO_INDEX[inst.tempo]]
                return 1 + (press - 1) * exposure
            return 1.0
        if kind == "attack":
            factor = self._counter_attack_factor(team) if inst.counter_attack else 1.0
            if (self.cfg.tactics_v2 and inst.attacking_focus is AttackingFocus.FLANKS
                    and self._opponent(team).instructions.concede_quality < 1.0):
                factor *= MATCHUP_EFFECTS.flanks_vs_block_attack      # 14E (b): kapali bloga kanattan
            return factor
        return self._offside_trap_factor(team) if inst.offside_trap else 1.0

    def _counter_attack_factor(self, team: MatchTeam) -> float:
        """Kontra atak hucum carpani: rakip ne kadar acik oynuyorsa o kadar buyuk."""
        c = COUNTER_ATTACK
        opp = self._opponent(team)
        oi = opp.instructions
        bonus = 0.0
        v2 = self.cfg.tactics_v2
        if oi.mentality is Mentality.ALL_OUT_ATTACK:
            bonus += MATCHUP_EFFECTS.counter_vs_all_out if v2 else c.vs_all_out_attack      # 14E (a)
        elif oi.mentality is Mentality.PARK_THE_BUS:
            bonus += c.vs_park_the_bus
        if oi.pressing is Pressing.ALL_OVER:
            bonus += MATCHUP_EFFECTS.counter_vs_high_press if v2 else c.vs_high_press   # 14E (d)
        elif oi.pressing is Pressing.OWN_HALF:
            bonus += c.vs_own_half_press
        if self._is_pressing(opp):
            bonus += c.vs_desperate
        if v2:
            # 14E: zayif takimin kontrasi. Ustun rakip oyunu kurar ve adam yigar: arkasinda alan kalir.
            superiority = self._superiority(team)
            if superiority > 1.0:
                bonus += MATCHUP_EFFECTS.counter_vs_superior * (superiority - 1.0)
        if bonus > 0 and team.instructions.passing_style is PassingStyle.DIRECT:
            bonus *= c.direct_synergy
        lo, hi = c.attack_range
        if v2:
            hi = MATCHUP_EFFECTS.counter_attack_max
        return max(lo, min(hi, 1 + bonus))

    def _superiority(self, team: MatchTeam) -> float:
        """14E: rakibin sahadaki kaba gucu / kendi gucu (overall x form-moral). Iki kadro degisene kadar onbellekli."""
        opp = self._opponent(team)
        key = ("sup", opp._lineup_version)
        cache = team._lineup_cache
        if cache.get("v") == team._lineup_version and key in cache:
            return cache[key]

        def build() -> float:
            own = sum(p.overall * p.condition_factor for p in team.on_pitch)
            return sum(p.overall * p.condition_factor for p in opp.on_pitch) / own if own > 0 else 2.0

        return team._cached(key, build)

    def _concede_factor(self, attacking: MatchTeam, defending: MatchTeam) -> float:
        """
        14E: akan oyun sans NETLIGI carpani (varsayilan talimat ciftinde tam 1.0; K6: asla gosterilmez).
        Savunanin talimati ("kale onu", concede_quality) x hucumcunun kendi talimati (chance_quality ^
        instruction_quality_exponent_v2) x karsi hamleler:
        (b) kapali blok ortaya kapanir: merkezden hucuma ceza buyur, kanattan (orta) hucuma kuculur.
        (c) kisa pas + yavas tempo tum saha presi kirar: onde basan takimin arkasi daha da acik.
        """
        concede = defending.instructions.concede_quality
        ai = attacking.instructions
        m = MATCHUP_EFFECTS
        if concede < 1.0:
            if ai.attacking_focus is AttackingFocus.CENTRE:
                concede = 1.0 - (1.0 - concede) * m.block_centre
            elif ai.attacking_focus is AttackingFocus.FLANKS:
                concede = 1.0 - (1.0 - concede) * m.block_flanks
        if (defending.instructions.pressing is Pressing.ALL_OVER and ai.passing_style is PassingStyle.SHORT
                and ai.tempo is Tempo.SLOW):
            concede *= m.press_bypass_quality
        if ai.counter_attack:
            bonus = self._counter_attack_factor(attacking) - 1.0
            if bonus > 0:
                concede *= 1.0 + bonus * m.counter_clarity     # (a) (d): acik rakibin arkasina kosu, net sans
        gamma = self.cfg.instruction_quality_exponent_v2
        if gamma and ai.chance_quality != 1.0:
            concede *= ai.chance_quality ** gamma       # kendi talimati: sabirli oyun az ama net sans
        return concede

    def _pace_edge(self, defending: MatchTeam) -> float:
        """Rakip forvetlerin hizi - savunmanin hizi (0-100 puan; yorgunluk hizi dusurur)."""
        attacking = self._opponent(defending)
        forwards = [p for p in attacking.outfield_on_pitch if p.role is Position.FWD]
        if not forwards:
            forwards = sorted(attacking.outfield_on_pitch, key=lambda p: (-p.attack_rating, p.id))[:2]
        backs = [p for p in defending.outfield_on_pitch if p.role is Position.DEF] or defending.outfield_on_pitch
        if not forwards or not backs:
            return 0.0

        def speed(players: list[MatchPlayer]) -> float:
            return sum(team_roles.pace_skill(p) * p.fatigue_factor for p in players) / len(players)

        return speed(forwards) - speed(backs)

    def _offside_trap_factor(self, team: MatchTeam) -> float:
        t = OFFSIDE_TRAP
        lo, hi = t.defense_range
        return max(lo, min(hi, t.base_defense - t.pace_influence * self._pace_edge(team)))

    def _focus_skill_factor(self, team: MatchTeam) -> float:
        """Hucum yonu x oyuncu becerisi: ilgili beceri ile takimin ilk 5 saha oyuncusunun genel gucu farki."""
        inst = team.instructions
        influence = inst.focus_effect.attribute_influence
        players = team.outfield_on_pitch
        if not influence or not players:
            return 1.0
        ref = team_roles.top_average((p.overall for p in players), 5)
        if inst.attacking_focus is AttackingFocus.FLANKS:
            skill = (0.5 * team_roles.top_average(map(team_roles.crossing_skill, players), 3)
                     + 0.5 * team_roles.top_average(map(team_roles.aerial_skill, players), 3))
        else:
            skill = (0.5 * team_roles.top_average(map(team_roles.technique_skill, players), 3)
                     + 0.5 * team_roles.top_average(map(team_roles.finishing_skill, players), 2))
        lo, hi = FOCUS_SKILL_RANGE
        return max(lo, min(hi, 1 + influence * (skill - ref)))

    def _chance_quality(self, attacking: MatchTeam, defending: MatchTeam) -> float:
        """Akan oyunda sutcu gucu carpani (varsayilan talimatlarda tam 1.0)."""
        inst = attacking.instructions
        quality = inst.chance_quality
        if inst.focus_effect.attribute_influence:
            quality *= self._focus_skill_factor(attacking)
        if inst.counter_attack:
            bonus = self._counter_attack_factor(attacking) - 1
            if bonus > 0:
                quality *= 1 + bonus * COUNTER_ATTACK.quality_share
        if defending.instructions.offside_trap:
            edge = self._pace_edge(defending)
            if edge > 0:                       # hizli forvet ofsayt cizgisini kirdi: arkaya atilan top
                quality *= min(OFFSIDE_TRAP.through_ball_cap, 1 + OFFSIDE_TRAP.through_ball_quality * edge)
        return quality

    def _card_factor(self, team: MatchTeam) -> float:
        """Takimin kart egilimi carpani: talimatlar (sertlik x pres) x sahadaki kaptan."""
        factor = team.instructions.card_factor
        if team.roles.captain_id is not None and team.captain_on_pitch:
            if self._am is not None:               # 14B: liderlik kaptanin sakinlestirici etkisini olcekler
                factor *= max(0.0, 1 - (1 - self.cfg.captain_card_factor) * self._captain_scale(team))
            else:
                factor *= self.cfg.captain_card_factor
        return factor

    # ------------------------------------------------------------------ ana akis

    def simulate(self) -> MatchResult:
        """Maci sonuna kadar oynatir (canli macta kalan dakikalari da bitirir)."""
        self.run_to_end()
        return self.result()

    def start(self) -> None:
        """Adim adim akisi hazirlar. Ilk step() baslama dudugunu (KICK_OFF) yazar."""
        if self._runner is None:
            self._runner = self._steps()

    def step(self) -> list[MatchEvent]:
        """
        Maci bir adim ilerletir: baslama dudugu, bir oyun dakikasi (uzatma dakikasi dahil) ya da
        bir mola (devre arasi / uzatma oncesi / uzatma arasi). Bu adimda yazilan olaylari dondurur.
        Son adim mac sonu dudugunu, (gerekirse) seri penaltilari ve notlari da isler.
        """
        if self.finished:
            return []
        self.start()
        before = len(self.events)
        assert self._runner is not None
        try:
            next(self._runner)
        except StopIteration:
            if not self.finished:
                raise RuntimeError("Maç akışı yarıda kesildi (motor hatası); maç tamamlanamaz.") from None
        return self.events[before:]

    def run_to_end(self) -> None:
        while not self.finished:
            self.step()

    def result(self) -> MatchResult:
        if self._result is None:
            raise RuntimeError("Maç henüz bitmedi; result() yerine snapshot() kullan.")
        return self._result

    def snapshot(self) -> MatchResult:
        """
        O ANKI durumun MatchResult gorunumu (canli ekran icin). Bitmemis macta notlar
        hesaplanmaz ve macin adami yoktur; olay listesi kopyadir.
        """
        if self._result is not None:
            return self._result
        return self._build_result(man_of_the_match=None, events=list(self.events))

    def _build_result(self, man_of_the_match: MatchPlayer | None, events: list[MatchEvent]) -> MatchResult:
        return MatchResult(
            home=self.home, away=self.away,
            home_score=self.home.stats.goals, away_score=self.away.stats.goals,
            events=events, seed=self.seed,
            first_half_added=self.first_half_added, second_half_added=self.second_half_added,
            man_of_the_match=man_of_the_match,
            extra_time=self.extra_time_played,
            extra_time_first_added=self.extra_time_first_added,
            extra_time_second_added=self.extra_time_second_added,
            shootout=self.shootout, knockout=self.knockout, neutral_venue=self.neutral_venue,
        )

    def _steps(self) -> Iterator[None]:
        """
        Macin kronolojik akisi. Her yield bir adim sonudur; menajer mudahaleleri yield'ler
        arasinda islenir. Rastgele cekis sirasi eski tek parca simulate() ile birebir aynidir.
        """
        self.minute, self.added = 0, 0
        if self.cfg.match_form:
            # D3: "kotu gun" mumkun olmali. Mac basinda takim basina tek log-normal cekilis;
            # dakikalar arasinda degismez, menajer mudahalesi bunu tetiklemez.
            lo, hi = self.cfg.match_form_range
            for team in (self.home, self.away):
                draw = math.exp(self.rng.normalvariate(0.0, self.cfg.match_form_sigma))
                team.match_form = max(lo, min(hi, draw))
        if self.cfg.ai_tactics:
            self._ai_tactics_update(force=True)        # duduk oncesi: olay yazilmaz
        self._log(EventType.KICK_OFF, None, None,
                  f"Hakem düdüğü çaldı! {self.home.name} - {self.away.name} başlıyor.")
        self.phase = MatchPhase.FIRST_HALF
        yield

        for minute in range(1, 46):
            self._play_minute(minute, 0)
            yield
        self.first_half_added = self._compute_added_time(half=1)
        for extra in range(1, self.first_half_added + 1):
            self._play_minute(45, extra)
            yield

        self.minute, self.added = 45, self.first_half_added
        self._log(EventType.HALF_TIME, None, None,
                  f"İlk yarı sona erdi. {self.home.name} {self.home.stats.goals} - "
                  f"{self.away.stats.goals} {self.away.name}")
        self._half_time()
        self.phase = MatchPhase.HALF_TIME
        self._break_tick = self._tick
        yield

        self.phase = MatchPhase.SECOND_HALF
        for minute in range(46, 91):
            self._play_minute(minute, 0)
            yield
        self.second_half_added = self._compute_added_time(half=2)
        for extra in range(1, self.second_half_added + 1):
            self._play_minute(90, extra)
            yield

        self.minute, self.added = 90, self.second_half_added
        if self.knockout is None:
            self._log(EventType.FULL_TIME, None, None,
                      f"Maç bitti! {self.home.name} {self.home.stats.goals} - "
                      f"{self.away.stats.goals} {self.away.name}")
        else:
            yield from self._knockout_steps()

        self._close_minutes()
        self._compute_ratings()
        self._result = self._build_result(man_of_the_match=self._man_of_the_match(), events=self.events)
        self.phase = MatchPhase.FINISHED

    # ------------------------------------------------------------------ eleme: uzatma + penalti

    def _knockout_finish(self) -> None:
        """90+X sonrasi: toplamda esitse uzatma, hala esitse seri penalti; sonra FULL_TIME."""
        for _ in self._knockout_steps():
            pass

    def _knockout_steps(self) -> Iterator[None]:
        rule = self.knockout
        assert rule is not None
        if self._aggregate_level() and rule.extra_time:
            yield from self._extra_time_steps()
        if self._aggregate_level() and rule.penalties:
            self.phase = MatchPhase.PENALTIES
            self._play_shootout()
        self._log_knockout_full_time()

    def _play_extra_time(self) -> None:
        for _ in self._extra_time_steps():
            pass

    def _extra_time_steps(self) -> Iterator[None]:
        self._log(EventType.EXTRA_TIME_START, None, None,
                  f"Normal süre {self.home.stats.goals}-{self.away.stats.goals} bitti"
                  f"{self._aggregate_text()}, uzatmalara gidiliyor!")
        self.in_extra_time = True
        self.extra_time_played = True
        for team in (self.home, self.away):
            for p in team.on_pitch:
                p.energy = min(100.0, p.energy + self.cfg.extra_time_break_recovery)
        self._half_events = 0
        self.phase = MatchPhase.EXTRA_TIME_BREAK
        self._break_tick = self._tick
        yield

        self.phase = MatchPhase.EXTRA_TIME_FIRST_HALF
        for minute in range(91, 106):
            self._play_minute(minute, 0)
            yield
        self.extra_time_first_added = self._compute_added_time(half=3)
        for extra in range(1, self.extra_time_first_added + 1):
            self._play_minute(105, extra)
            yield

        self.minute, self.added = 105, self.extra_time_first_added
        self._log(EventType.EXTRA_TIME_HALF, None, None,
                  f"Uzatmaların ilk yarısı sona erdi. {self._score_text()}{self._aggregate_text()}")
        self._half_events = 0
        self.phase = MatchPhase.EXTRA_TIME_HALF_TIME
        self._break_tick = self._tick
        yield

        self.phase = MatchPhase.EXTRA_TIME_SECOND_HALF
        for minute in range(106, 121):
            self._play_minute(minute, 0)
            yield
        self.extra_time_second_added = self._compute_added_time(half=4)
        for extra in range(1, self.extra_time_second_added + 1):
            self._play_minute(120, extra)
            yield
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

    def _captain_composure(self, team: MatchTeam) -> float:
        """Kaptan sahadaysa penalti aticilarinin yetenegine eklenen sogukkanlilik (yoksa 0)."""
        if team.roles.captain_id is not None and team.captain_on_pitch:
            if self._am is not None:               # 14B: liderlik
                return self.cfg.captain_penalty_composure * self._captain_scale(team)
            return self.cfg.captain_penalty_composure
        return 0.0

    def _shootout_side(self, team: MatchTeam) -> ShootoutSide:
        """
        Seriye yalnizca SU AN sahada olanlar girer; kalede rolu GK olan (acil durum dahil) durur.
        Belirlenmis penalti aticisi sahadaysa ilk atisi o yapar (first_taker_id).
        """
        keeper = team.keeper
        composure = self._captain_composure(team)
        takers = [PenaltyTaker(p.id, p.name, self._penalty_taker_skill(p) + composure) if composure
                  else PenaltyTaker(p.id, p.name, self._penalty_taker_skill(p)) for p in team.on_pitch]
        designated = team.on_pitch_by_id(team.roles.penalty_taker_id)
        return ShootoutSide(
            team_id=team.id, team_name=team.name,
            takers=takers,
            keeper_id=keeper.id if keeper else None,
            keeper_name=keeper.name if keeper else "kaleci",
            keeper_skill=self._penalty_keeper_skill(keeper),
            first_taker_id=designated.id if designated else None,
        )

    def _play_shootout(self) -> None:
        end = self.end_minute
        self.minute, self.added = end, 0
        first = "home" if self.rng.random() < 0.5 else "away"
        home_side, away_side = self._shootout_side(self.home), self._shootout_side(self.away)
        home_takers, away_takers = equalize_takers(
            home_side.takers, away_side.takers, home_side.keeper_id, away_side.keeper_id,
            home_side.first_taker_id, away_side.first_taker_id)
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
            kick_number=kick.number, priority=PRIORITY_CRUCIAL, dwell_ms=DWELL_CRUCIAL,
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
        sub_from = (self.cfg.tactical_sub_from_minute_v2 if self.cfg.sub_timing
                    else self.cfg.tactical_sub_from_minute)
        if minute >= sub_from and added == 0:
            for team in (self.home, self.away):
                self._tactical_substitution(team)      # onceki duraklamada (dakika arasi) yapilir

        self._tick += 1                                # top oyunda: yeni duraklama anahtari
        logged = len(self.events)
        attacking, defending = self._possession()
        attacking.stats.possession_minutes += 1
        cfg = self.cfg
        nar = self._nar                                # hakimiyet EMA'si (bkz. _register_control)
        if attacking is self.home:
            nar.control += cfg.possession_control_alpha * (1.0 - nar.control)
            edge = nar.control - 0.5
        else:
            nar.control -= cfg.possession_control_alpha * nar.control
            edge = 0.5 - nar.control
        if cfg.possession_weighted:
            self._register_control(attacking, edge)
        self._attack(attacking, defending)
        self._discipline(attacking, defending)
        self._injury_check()
        if self.cfg.flow_events:
            # 13B olu hava tabani: sessiz dakikalar birikirse oyunun akisi anlatilir
            self._nar.quiet = 0 if len(self.events) > logged else self._nar.quiet + 1
            if self._nar.quiet >= self.cfg.ambient_after:
                self._log_pressure()
                self._nar.quiet = 0
        self._after_minute()                           # oyun plani + AI talimatlari (rastgele sayi cekmez)

    def _after_minute(self) -> None:
        """Dakika oynandiktan sonraki duraklama: oyun plani kurallari, sonra AI talimatlari."""
        for team in (self.home, self.away):
            if team.plans_enabled and team.plan.rules:
                self._run_plan(team)
        if self.cfg.ai_tactics:
            self._ai_tactics_update()

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
            team.touch_lineup()

    # ------------------------------------------------------------------ yorgunluk

    def _apply_fatigue(self) -> None:
        balance = self.cfg.fatigue_balance
        base_decay = self.cfg.fatigue_base_decay_v2 if balance else self.cfg.fatigue_base_decay
        late = self.cfg.fatigue_late_multiplier if balance else 1.25
        late_phase = self.minute > 75
        for team in (self.home, self.away):
            team_mult = self.cfg.trailing_fatigue_multiplier if self._is_pressing(team) else 1.0
            effort = team.instructions.fatigue_factor          # zihniyet/sertlik: varsayilan tam 1.0
            # 13B hiz: oyuncu basina sabit carpim kadro degisene kadar onbellekte (ayni ifade,
            # ayni sira -> ayni float; enerji guncellemesi birebir eskisi gibi).
            for p, decay in team._cached("fatigue_decay", lambda t=team: [
                    (q, base_decay * ROLE_FATIGUE[q.role or q.position] * q.decay_multiplier)
                    for q in t.on_pitch]):
                if late_phase:
                    decay *= late     # son dakikalarda yorgunluk katlanir
                p.energy = max(0.0, p.energy - decay * team_mult * effort)

    @staticmethod
    def _drain(p: MatchPlayer, amount: float) -> None:
        """Efor kaybi (sut, asist, faul): enerjiden aninda dusulur."""
        p.energy = max(0.0, p.energy - amount)

    def _tactical_substitution(self, team: MatchTeam) -> None:
        if not team.auto_subs:
            return                  # menajer degisiklikleri kendisi yapiyor
        if self.cfg.sub_timing and self.rng.random() >= self._sub_urgency(team):
            # D5: eskiden 60. dakikadan itibaren HER dakika bakiliyordu, bu yuzden
            # degisikliklerin %89.7'si 60-69 arasina yigiliyordu. Artik dakika basina
            # bir egilim var: degisiklikler 45-85 arasina yayilir.
            return
        made = self._one_tactical_substitution(team)
        # Pencere kurali varken her dakika ayri pencere harcamasin: yorgunlarin hepsi ayni duraklamada
        # (kurali olmayan varsayilan macta dakikada tek degisiklik: eski davranis birebir korunur)
        while made and self.max_windows is not None:
            made = self._one_tactical_substitution(team)

    def _sub_urgency(self, team: MatchTeam) -> float:
        """Bu dakikada degisiklik penceresi acma egilimi (13A/S5)."""
        urgency = self.cfg.sub_window_spread
        if self._deficit(team) > 0 and self.minute >= self.cfg.tactical_swap_from_minute:
            urgency *= self.cfg.sub_urgency_trailing
        return urgency

    def _tactical_swap(self, team: MatchTeam) -> tuple[MatchPlayer, Position, str] | None:
        """
        Yorgun kimse yoksa TAKTIK gerekceli degisiklik: (cikan, giren rolu, gerekce).
        Geride kalan takim hucumcu, onde olan takim savunmaci sokar.
        """
        v2 = self.cfg.tactics_v2
        limit = self.cfg.max_tactical_swaps_v2 if v2 else self.cfg.max_tactical_swaps
        if self.minute < self.cfg.tactical_swap_from_minute or team.tactical_swaps_used >= limit:
            return None
        outfield = team.outfield_on_pitch
        if not outfield:
            return None
        deficit = self._deficit(team)
        if v2:
            # 14E: norm artik gercek sekli izler; hak artinca hatlar sinirlanir (sahada en cok 3 forvet, en az 1
            # forvet; forvete oyuncu veren hatta en az 3 oyuncu kalir).
            roles = [p.role or p.position for p in outfield]
            d, m, f = roles.count(Position.DEF), roles.count(Position.MID), roles.count(Position.FWD)
        if deficit > 0:
            pool = [p for p in outfield if (p.role or p.position) is not Position.FWD]
            if v2:
                pool = [] if f >= 3 else [p for p in pool if (m if (p.role or p.position) is Position.MID else d) > 3]
            if pool:
                return min(pool, key=lambda p: (p.attack_rating, p.id)), Position.FWD, "hücum için"
        elif deficit < 0:
            pool = [p for p in outfield if (p.role or p.position) is Position.FWD]
            if v2 and f <= 1:
                pool = []
            if pool:
                return min(pool, key=lambda p: (p.attack_rating, p.id)), Position.MID, "skoru korumak için"
        return None

    def _one_tactical_substitution(self, team: MatchTeam) -> bool:
        if team.subs_used >= self.max_subs - self.cfg.subs_reserved_for_emergency:
            return False
        if self.max_windows is not None and self.substitution_block(team, reserve_window=True):
            return False
        threshold = self.cfg.tired_threshold_v2 if self.cfg.sub_timing else self.cfg.tired_threshold
        tired = [p for p in team.outfield_on_pitch if p.energy < threshold]
        if tired:
            out = min(tired, key=lambda p: p.energy)
            role, reason = out.role, "yoruldu"
        elif self.cfg.sub_timing:
            swap = self._tactical_swap(team)
            if swap is None:
                return False
            out, role, reason = swap
            team.tactical_swaps_used += 1
        else:
            return False
        same_pos = [b for b in team.bench if b.position is role]
        if not same_pos and self.cfg.sub_timing:
            same_pos = [b for b in team.bench if b.position is not Position.GK]
        if not same_pos:
            return False
        sub = max(same_pos, key=lambda p: p.effective_power)
        team.remove_player(out, self.minute)
        out.substituted = True
        team.field_player(sub, role, self.minute)
        self._register_sub(team)
        # Onek ("Degisiklik (takim): ") korunur: degisiklik pencereleri bu onekle ayirt ediliyor.
        if self.cfg.narration:
            key = {"yoruldu": "sub.tired", "hücum için": "sub.attack"}.get(reason, "sub.defend")
            tags = frozenset((self._minute_tag(), self._game_state_tag(team)))
            slots = self._slots(team, sub, pin=sub.name, pout=out.name)
            body = self._nar.narrator.live(key, tags, slots)
        else:
            body = f"{out.name} {reason}, yerine {sub.name} giriyor."
        self._log(EventType.SUBSTITUTION, team, sub, f"Değişiklik ({team.name}): {body}",
                  priority=PRIORITY_ROUTINE, dwell_ms=DWELL_ROUTINE)
        return True

    # ------------------------------------------------------------------ pozisyon

    def _possession(self) -> tuple[MatchTeam, MatchTeam]:
        k = self.cfg.possession_sharpness if self.cfg.flat_superiority else self.cfg.sharpness_team
        if self.cfg.tactics_v2:
            # 14E: talimat ve karsi hamle carpanlari topa sahip olmaya kendi keskinligiyle yansir
            # (instruction_possession_sharpness_v2); varsayilan talimatta carpan 1.0: eski formulle bit-bit ayni.
            home, away = self.home, self.away
            f_home = home.instructions.strength_factor("midfield") * self._matchup_factor(home, "midfield")
            f_away = away.instructions.strength_factor("midfield") * self._matchup_factor(away, "midfield")
            s_home = self._structural_strength(home, "midfield")
            s_away = self._structural_strength(away, "midfield")
            if f_home != 1.0 or f_away != 1.0:
                damp = self.cfg.instruction_possession_sharpness_v2 / k
                s_home *= f_home ** damp
                s_away *= f_away ** damp
            p_home = self._contest(s_home, s_away, k)
        else:
            p_home = self._contest(self._team_strength(self.home, "midfield"),
                                   self._team_strength(self.away, "midfield"), k)
        if self.rng.random() < p_home:
            return self.home, self.away
        return self.away, self.home

    def _register_control(self, attacking: MatchTeam, edge: float) -> None:
        """
        13B / D12 -- DURUST TOPLA OYNAMA.

        "Topla oynama" eskiden dakika sahipligi sayaciydi: 90 bagimsiz yazi-tura, yani
        esit takimlarda dagilim matematiksel olarak dar (sd ~5.3, %45-64). Gercek
        maclarda sahiplik SEKANSLAR halinde gelir; hakimiyeti sureklilesen takim daha
        uzun pasajlar tutar. Burada ayni sahiplik dizisinden bir hakimiyet EMA'si
        turetilir ve dakikanin AGIRLIGI o hakimiyete gore olceklenir.

        YENI RASTGELE SAYI CEKILMEZ: her sey mevcut sahiplik cekilisinin fonksiyonudur.
        Hakimiyet EMA'si (`_play_minute` icinde guncellenir, `edge` sahibin lehine sapmadir)
        ayrica ambiyans anlatiminda topu kimin yonettigini belirler.
        """
        cfg = self.cfg
        lo, hi = cfg.possession_weight_range
        attacking.stats.possession_weight += max(lo, min(hi, 1.0 + cfg.possession_amplitude * edge))

    def _tempo_factor(self) -> float:
        """
        Dakikaya gore oyun temposu (13A/S5). Gercek gol dakikasi egrisi duz degildir:
        ilk 10 dakika ~%7.5, son 10 dakika ~%19, 90+ tum gollerin %6-9'u. Motorun
        dakikalari bagimsiz oldugu icin bu egri dogrudan pozisyon oranina uygulanir.
        """
        if not self.cfg.goal_timing:
            return 1.0
        if self.added > 0:
            return self.cfg.stoppage_tempo
        minute = self.minute
        for edge, factor in self.cfg.tempo_curve:
            if minute <= edge:
                return factor
        return self.cfg.tempo_curve[-1][1]

    def _chance_probability(self, attacking: MatchTeam, defending: MatchTeam,
                            tempo: float) -> tuple[float, float]:
        """
        Dakikada pozisyon uretme olasiligi ve o atagin "sans yogunlugu" carpani.
        13A/S4: ustunlugun TAMAMI buraya (sut HACMINE) toplanir; yogunluk arttikca
        sansin KALITESI duser (bastiran takim cok ama kotu sans, kapanan takim az ama net kontra).
        Yogunluk tempodan ARINDIRILMISTIR: 90+ dakikasinda tempo artar ama kalite dusmez.
        """
        cfg = self.cfg
        if cfg.tactics_v2 and cfg.flat_superiority:
            # 14E (1): hacim TUM carpanlarla, kalite (yogunluk) YAPISAL guc oraniyla. Tek cagride ikisi.
            # Talimat carpanlari hacme kendi keskinligiyle (instruction_sharpness_v2) yansir: tablolar a^2 / (a^2 +
            # b^2) icin kalibre edildi; yogunluk takasi olmadan 6.5 keskinlikte etkileri ~3 kat buyurdu.
            s_attack = self._structural_strength(attacking, "attack")
            s_defense = self._structural_strength(defending, "defense")
            f_attack = attacking.instructions.strength_factor("attack") * self._matchup_factor(attacking, "attack")
            f_defense = defending.instructions.strength_factor("defense") * self._matchup_factor(defending, "defense")
            p_structure = self._contest(s_attack, s_defense, cfg.chance_sharpness)
            if f_attack == 1.0 and f_defense == 1.0:
                p_win = p_structure
            else:
                damp = cfg.instruction_sharpness_v2 / cfg.chance_sharpness
                p_win = self._contest(s_attack * f_attack ** damp, s_defense * f_defense ** damp, cfg.chance_sharpness)
        else:
            attack = self._team_strength(attacking, "attack")
            defense = self._team_strength(defending, "defense")
            if not self.cfg.flat_superiority:
                return self._scaled(self.cfg.base_chance, self._team_contest(attack, defense)), 1.0
            p_win = p_structure = self._contest(attack, defense, cfg.chance_sharpness)
        lo, hi = cfg.chance_multiplier_cap
        mult = max(lo, min(hi, 2 * p_win))
        if self._am is not None:
            # 14B: topsuz oyun + yaraticilik (hucuma katilanlar) pozisyon HACMI; geride iken son bolumde (bastirirken)
            # kararlilik. Hacim dogrudan: guc uzerinden gitseydi yogunluk-kalite takasi etkiyi tersine cevirirdi.
            factors = self._am_team(attacking)
            volume = factors.chance * factors.comeback if self._is_pressing(attacking) else factors.chance
            p_chance = max(0.01, min(0.90, cfg.base_chance_v2 * mult * tempo * volume))
        else:
            p_chance = max(0.01, min(0.90, cfg.base_chance_v2 * mult * tempo))
        lo, hi = cfg.chance_density_range
        # Kalite cezasi GERCEK ustunlugu gorur (hacim tavani kaliteyi affetmez).
        density = max(lo, min(hi, (cfg.chance_density_threshold / max(2 * p_structure, 1e-6))
                              ** cfg.chance_density_exponent))
        return p_chance, density

    def _accuracy_probability(self, shooter_str: float, defender_str: float) -> float:
        """Sut -> isabet. 13A/S4: guc farkina neredeyse duyarsiz (ustunluk hacimde ifade edilir)."""
        if not self.cfg.flat_superiority:
            return self._scaled(self.cfg.base_on_target, self._player_contest(shooter_str, defender_str))
        p_win = self._contest(shooter_str, defender_str, self.cfg.accuracy_sharpness)
        return max(0.10, min(0.80, self.cfg.base_on_target_v2 * 2 * p_win))

    def _dead_ball_residue(self, attacking: MatchTeam, fraction: float) -> None:
        """
        Sansa donmeyen atagin sonu: korner ya da ofsayt.
        `fraction` atak cekilisinin artigidir (U[0,1)); YENI rastgele sayi cekilmez.

        13B / K7: sayaclar ARTIK OLAY da yazar. Olay uretimi asla atlanmaz; akista
        gizleme `display_probability` ile match_feed tarafinda yapilir -- yoksa
        istatistik ile akis birbirine yalan soyler.
        """
        cfg = self.cfg
        if fraction < cfg.dead_corner_share:
            attacking.stats.corners += 1
            if cfg.flow_events:
                self._log_corner(attacking)
        elif fraction < cfg.dead_corner_share + cfg.offside_share:
            attacking.stats.offsides += 1
            if cfg.flow_events:
                self._log_offside(attacking)

    # ---------------------------------------------------------------- dusuk etkili olaylar

    def _flavour_player(self, team: MatchTeam, kind: str) -> MatchPlayer | None:
        """
        Anlatim icin oyuncu secer. TAMAMEN KOZMETIKTIR (istatistik yazilmaz), bu yuzden
        anlatim RNG'sinden cekilir: sonuc akisi etkilenmez.
        """
        pool, cum = team._cached(_FLAVOUR_KEYS[kind], lambda: _flavour_table(team, kind))
        if not pool:
            return None
        return pool[bisect_right(cum, self._nar.narrator.rng.random() * cum[-1])]

    def _lazy(self, key: str, context: frozenset[str], names: tuple, type_: EventType,
              team: MatchTeam, player: MatchPlayer | None, detail: str | None, priority: int,
              show: float, dwell: int, *, home_score: int | None = None, away_score: int | None = None,
              chain_id: int | None = None, chain_step: int = 0,
              quality: float | None = None) -> LazyEvent:
        """Cumlesi okununca kurulacak olay; ayni kovadaki bir onceki olaya zincirlenir (halka)."""
        nar = self._nar
        nar.token += 1
        last = nar.last_lazy
        event = _lazy_event(
            (key, context, names, nar.token), last.get(key), self.minute, self.added, type_, team, player,
            self.home.stats.goals if home_score is None else home_score,
            self.away.stats.goals if away_score is None else away_score,
            detail, priority, show, dwell, chain_id, chain_step, quality, context)
        last[key] = event
        return event

    def _log_shot(self, type_: EventType, attacking: MatchTeam, defending: MatchTeam,
                  shooter: MatchPlayer, keeper: MatchPlayer | None) -> MatchEvent:
        """Akan oyunda isabetsiz / kurtarilan sut. 13B: cumle tembel kurulur (akista cogu gizli)."""
        outcome = "miss" if type_ is EventType.MISS else "save"
        if not self.cfg.narration:
            text = (self._miss_text(shooter, attacking) if outcome == "miss"
                    else self._save_text(shooter, attacking, keeper))
            return self._log(type_, attacking, shooter, text, **self._shot_meta(outcome))
        self._legacy_text_draw(None if outcome == "miss" else 4)    # eski metin cekilisi: akis korunur
        nar = self._nar
        gk = keeper or defending.keeper
        names = (attacking.name, defending.name, gk.name if gk is not None else "kaleci", shooter.name,
                 nar.defender.name if nar.defender is not None else None)
        priority, show, dwell = self._shot_display(outcome, nar.shot_ctx)
        event = self._lazy(_SHOT_KEYS[outcome], nar.shot_ctx, names, type_, attacking, shooter, None,
                           priority, show, dwell, quality=nar.quality)
        self.events.append(event)
        return event

    def _flow_tags(self, team: MatchTeam) -> frozenset[str]:
        """Dusuk etkili olaylarin baglami (dakika bandi + skor durumu); dakika basina bir kez kurulur."""
        nar = self._nar
        key = (self._tick, self.home.stats.goals, self.away.stats.goals)
        if nar.ctx_key != key:
            minute = self._minute_tag()
            diff = key[1] - key[2]
            nar.ctx_key = key
            nar.ctx_home = frozenset((minute, _GAME_STATE[max(-1, min(3, diff))]))
            nar.ctx_away = frozenset((minute, _GAME_STATE[max(-1, min(3, -diff))]))
        return nar.ctx_home if team is self.home else nar.ctx_away

    def _log_flow(self, type_: EventType, team: MatchTeam, player: MatchPlayer | None, key: str,
                  show: float, priority: int, detail: str | None = None) -> None:
        """
        Korner / faul / ofsayt / ambiyans olayi. Mac basina ~30 tane uretildigi icin ince yol:
        `_log` ve `_slots` atlanir, cumle okununca kurulur (LazyEvent). Olay HER ZAMAN yazilir;
        gizleme akisin isidir (K7).
        """
        tags = self._flow_tags(team)
        opponent = self.away if team is self.home else self.home
        keeper = opponent.keeper
        names = (team.name, opponent.name, keeper.name if keeper is not None else "kaleci",
                 player.name if player is not None else None, None)
        self.events.append(self._lazy(key, tags, names, type_, team, player, detail, priority, show,
                                      DWELL_ROUTINE))

    def _log_corner(self, attacking: MatchTeam) -> None:
        self._log_flow(EventType.CORNER, attacking, self._flavour_player(attacking, "wide"),
                       "corner.won", self.cfg.show_corner, PRIORITY_LOW)

    def _log_offside(self, attacking: MatchTeam) -> None:
        self._log_flow(EventType.OFFSIDE, attacking, self._flavour_player(attacking, "attack"),
                       "offside.play", self.cfg.show_offside, PRIORITY_LOW)

    def _log_foul(self, team: MatchTeam) -> None:
        self._log_flow(EventType.FOUL, team, self._flavour_player(team, "defend"),
                       "foul.play", self.cfg.show_foul, PRIORITY_AMBIENT)

    def _log_pressure(self) -> None:
        """
        Olu hava tabani (D12): `ambient_after` dakikadir hicbir olay yazilmadiysa oyunun
        akisini anlatan bir satir. Topu kim yonetiyorsa (hakimiyet EMA'si) onun adina yazilir.
        Rastgele sayi cekmez; yalnizca anlatim RNG'si kullanilir.
        """
        team = self.home if self._nar.control >= 0.5 else self.away
        self._log_flow(EventType.BUILD_UP, team, None, "buildup.pressure",
                       self.cfg.show_pressure, PRIORITY_AMBIENT, detail="pressure")

    # ---------------------------------------------------------------- kurulus zinciri

    def _chain_shape(self, fraction: float) -> tuple[str, ...]:
        """
        Atak pasajinin sekli. POZISYON cekilisinin artigindan turetilir: yeni rastgele
        sayi CEKILMEZ (`_set_piece_kind` ile ayni numara), boylece ayni tohum ayni sonucu
        verir ve hiz kaybi olmaz.
        """
        cfg = self.cfg
        none_share = cfg.build_up_none_share
        if fraction < none_share:
            return ()
        two_share = cfg.build_up_two_share
        one_share = max(0.0, 1.0 - none_share - two_share)
        if fraction < none_share + one_share:
            v = (fraction - none_share) / max(1e-12, one_share)
            return ("win",) if v < 0.30 else (("entry",) if v < 0.65 else ("final",))
        v = (fraction - none_share - one_share) / max(1e-12, two_share)
        return ("win", "final") if v < 0.50 else ("entry", "final")

    def _attach_chain(self, attacking: MatchTeam, shooter: MatchPlayer, shape: tuple[str, ...],
                      outcome: MatchEvent, goal: bool) -> None:
        """
        Sutun ONCESINDEKI 0-2 kurulus halkasini yazar ve sonuc olayinin HEMEN ONUNE yerlestirir
        (hepsi ayni dakikada, ayni adimda uretildigi icin kronoloji bozulmaz). Hepsi ayni
        `chain_id`'yi tasir (CM `follow-on`). Sonuc bilindikten sonra kurulur, cunku:
          * gole giden atagin sebebi HER ZAMAN yazilir ve gosterilir ("gol tek satirla gelmez");
          * siradan bir sutun kurulusu cogunlukla gizlenir (akis sismez).
        Sekil pozisyon cekilisinin artigindan gelir; yeni rastgele sayi cekilmez.
        """
        if goal and not shape:
            shape = ("final",)
        if not shape:
            return
        nar = self._nar
        nar.chain_id += 1
        chain_id = nar.chain_id
        tags = nar.shot_ctx
        opponent = self.away if attacking is self.home else self.home
        home_score, away_score = outcome.home_score, outcome.away_score
        if goal:                                   # kurulus golden ONCE: skor bir eksik
            if attacking is self.home:
                home_score -= 1
            else:
                away_score -= 1
        show = 1.0 if goal else self.cfg.show_build_up
        priority = PRIORITY_HIGH if goal else PRIORITY_ROUTINE
        last = len(shape) - 1
        links: list[MatchEvent] = []
        # Sutcu topu kendisi tasidiysa (bireysel is ya da asistsiz gol) son halka PAS degil
        # TASIMA'dir: "X son pasi verdi" ardindan "Y tek basina bitirdi" celiskisi olmasin.
        carried = "solo" in tags or (goal and nar.assister is None)
        for step, part in enumerate(shape):
            if part == "final" and carried:
                part, player = "entry", shooter
            elif part == "final" and goal:
                player = nar.assister              # son pasi veren, golun asistini yapandir
            else:
                player = self._flavour_player(attacking, "build") or shooter
                if player is shooter and part == "final":      # kendine pas vermesin (kozmetik)
                    player = self._flavour_player(attacking, "build") or shooter
            names = (attacking.name, opponent.name, "kaleci", player.name,
                     nar.defender.name if nar.defender is not None else None)
            links.append(self._lazy(
                _BUILD_UP_KEYS[part], tags, names, EventType.BUILD_UP, attacking, player, part,
                priority, show, DWELL_MAIN if goal and step == last else DWELL_ROUTINE,
                home_score=home_score, away_score=away_score, chain_id=chain_id, chain_step=step))
        outcome.chain_id = chain_id
        outcome.chain_step = len(links)
        at = len(self.events) - 1                  # sonuc olayi her zaman en son yazilandir
        while self.events[at] is not outcome:
            at -= 1
        self.events[at:at] = links

    def _attack(self, attacking: MatchTeam, defending: MatchTeam) -> None:
        p_chance, self._density = self._chance_probability(attacking, defending, self._tempo_factor())
        roll = self.rng.random()
        if roll >= p_chance:
            if self.cfg.match_stats:
                self._dead_ball_residue(attacking, (roll - p_chance) / max(1e-12, 1.0 - p_chance))
            return
        fraction = roll / p_chance
        if self._set_pieces_on():
            # Pozisyon cekilisinin alt dilimi duran top: ek rastgele sayi cekilmez (roll / p_chance ~ U[0,1))
            kind = self._set_piece_kind(fraction)
            if kind is not None:
                self._set_piece(kind, attacking, defending)
                return
            edge = (self.cfg.set_piece_penalty_share + self.cfg.set_piece_free_kick_share
                    + self.cfg.set_piece_corner_share)
            fraction = (fraction - edge) / max(1e-12, 1.0 - edge)

        if self.cfg.tactics_v2:
            # 14E: savunanin talimati akan oyun sansinin NETLIGINI carpar (duran toplar haric; K6: gosterilmez)
            concede = self._concede_factor(attacking, defending)
            if concede != 1.0:
                self._density *= concede
        shooter = self._pick_shooter(attacking)
        if shooter is None:
            return
        shooter.shots += 1
        attacking.stats.shots += 1
        self._drain(shooter, self.cfg.shot_energy_cost)

        defender_str = self._defensive_resistance(defending)
        role_w = max(ROLE_WEIGHTS["attack"][shooter.role or shooter.position], 0.01)
        shooter_str = self._player_strength(shooter, "attack") / role_w
        shooter_str *= self._situation_factor(attacking, "attack")
        quality = self._chance_quality(attacking, defending)      # pas stili / tempo / hucum yonu ...
        if quality != 1.0:
            shooter_str *= quality
        if shooter._day_form != 1.0:
            shooter_str *= shooter._day_form         # 15G: sutorun o gunku isi (tutarlilik)

        # K6: sansin netligi SAKLANIR, sayi olarak asla gosterilmez -- yalnizca dili secer.
        nar = self._nar
        nar.quality = self._density * self._defender_quality(defender_str)
        chained = self.cfg.narration and self.cfg.build_up_chains
        if self.cfg.narration:
            q_tag = self._quality_tag(nar.quality)
            form = self._shot_form_tag(q_tag, None)
            nar.shot_tags, nar.shot_ctx = _shot_context(
                q_tag, form, self._minute_tag(),
                nar.defender is not None and form not in ("oneonone", "tap"),
                self._game_state_tag(attacking))

        p_on_target = self._accuracy_probability(shooter_str, defender_str)
        if self._am is not None:       # 14B: teknik + karar alma (sut secimi)
            p_on_target = max(0.10, min(0.80, p_on_target * shooter._am.accuracy))
        if self.rng.random() >= p_on_target:
            if self._rm is not None:   # 15G: isabetsiz sut de bir duellodur (savunmaci kazandi)
                self._rating_chance(attacking, defending, shooter, nar.defender, nar.quality, "miss", fraction)
            event = self._log_shot(EventType.MISS, attacking, defending, shooter, None)
            if chained:
                self._attach_chain(attacking, shooter, self._chain_shape(fraction), event, goal=False)
            return

        shooter.shots_on_target += 1
        attacking.stats.shots_on_target += 1
        keeper = defending.keeper
        p_goal = self._goal_probability(shooter, shooter_str, defending, quality, defender_str)

        if self.rng.random() < p_goal:
            if self._rm is not None:
                self._rating_chance(attacking, defending, shooter, nar.defender, nar.quality, "goal", fraction)
            self._goal(attacking, shooter)
            if chained:
                self._attach_chain(attacking, shooter, self._chain_shape(fraction), self.events[-1], goal=True)
        else:
            if keeper is not None:
                keeper.saves += 1
            defending.stats.saves += 1
            if self._rm is not None:
                self._rating_chance(attacking, defending, shooter, nar.defender, nar.quality, "save", fraction)
            event = self._log_shot(EventType.SAVE, attacking, defending, shooter, keeper)
            if chained:
                self._attach_chain(attacking, shooter, self._chain_shape(fraction), event, goal=False)

    def _shot_display(self, outcome: str, tags: frozenset[str]) -> tuple[int, float, int]:
        """(oncelik, gosterim olasiligi, bekleme): net sans her zaman, umut sutu nadiren gorunur."""
        if outcome == "goal":
            return PRIORITY_CRUCIAL, 1.0, DWELL_CRUCIAL
        big = "big" in tags
        if outcome == "save":
            return (PRIORITY_HIGH if big else PRIORITY_MAIN, 1.0 if big else self.cfg.show_save,
                    DWELL_ROUTINE if "far" in tags else DWELL_MAIN)
        show = (1.0 if big else 0.85 if "good" in tags else
                self.cfg.show_miss if "normal" in tags else self.cfg.show_miss * 0.8)
        # Sut satiri bir an TUTULUR (ana kademe); yalnizca umut sutlari rutin gecer.
        return (PRIORITY_HIGH if big else PRIORITY_ROUTINE, show,
                DWELL_ROUTINE if "far" in tags else DWELL_MAIN)

    def _shot_meta(self, outcome: str, tags: frozenset[str] | None = None) -> dict[str, Any]:
        """Sut olayinin sunum meta verisi (K5) + saklanan sans kalitesi (K6)."""
        tags = self._nar.shot_ctx if tags is None else tags
        priority, show, dwell = self._shot_display(outcome, tags)
        return dict(priority=priority, display_probability=show, dwell_ms=dwell,
                    chance_quality=self._nar.quality, tags=tags)

    # ---------------------------------------------------------------- sutor, savunma, bitiricilik

    def _pick_shooter(self, attacking: MatchTeam) -> MatchPlayer | None:
        """Akan oyunda sutu kim ceker. 13A/S2: rol agirligi takim gucundekinden ayrilir."""
        if not self.cfg.role_realism:
            return self._weighted_choice(attacking.outfield_on_pitch,
                                         lambda p: self._player_strength(p, "attack"))
        ref, sharp = self.cfg.shooter_reference, self.cfg.shooter_sharpness

        def weight(p: MatchPlayer) -> float:
            role = p.role or p.position
            raw = self._player_strength(p, "attack") / max(ROLE_WEIGHTS["attack"][role], 0.01)
            return SHOOTER_ROLE_WEIGHT[role] * (raw / ref) ** sharp

        if self._am is not None:
            # 14B: topsuz oyun (+ uzaktan sut) sansin kime dustugu; baskin hucumcu daha keskin one cikar
            sharp_am = sharp + self._am.shooter_sharpness_extra

            def weight_am(p: MatchPlayer) -> float:
                role = p.role or p.position
                raw = self._player_strength(p, "attack") / max(ROLE_WEIGHTS["attack"][role], 0.01)
                return SHOOTER_ROLE_WEIGHT[role] * (raw / ref) ** sharp_am * p._am.shooter

            return self._weighted_choice(attacking.outfield_on_pitch, weight_am)
        return self._weighted_choice(attacking.outfield_on_pitch, weight)

    def _defensive_resistance(self, defending: MatchTeam) -> float:
        """
        Sutu kim engellemeye calisiyor.

        Eski model "en iyi dort savunmacinin ortalamasi" idi: bir stoperi 70'ten 45'e
        dusurmek yenilen golu 1.16'dan 1.18'e cikariyordu, yani ZAYIF HALKA yoktu.
        13A/S3: rol agirlikli cekilisle BELIRLI bir savunmaci secilir; kotu savunmaci
        cezalandirilabilir, iyi savunmaci hissedilir.
        """
        situation = self._situation_factor(defending, "defense")
        outfield = defending.outfield_on_pitch
        self._nar.defender = None
        if not self.cfg.weak_link:
            defenders = sorted((self._player_strength(p, "defense") for p in outfield), reverse=True)[:4]
            base = (sum(defenders) / len(defenders)) if defenders else 5.0
            return base * situation
        if not outfield:
            return 5.0 * situation
        if self._am is not None:
            # 14B: pozisyon alma / onsezi / karar alma hedeflenmeyi, markaj sansin netligini belirler
            contested = self._weighted_choice(outfield, self._contest_weight_am)
            if contested is None:
                return 5.0 * situation
            self._nar.defender = contested
            resistance = self._raw_defense(contested) * situation * contested._am.marking
            if contested._day_form != 1.0:
                resistance *= contested._day_form    # 15G: cekilen savunmacinin o gunku isi
            return resistance
        contested = self._weighted_choice(outfield, self._contest_weight)
        if contested is None:
            return 5.0 * situation
        self._nar.defender = contested       # 13B: anlatimda adiyla anilir ({d})
        return self._raw_defense(contested) * situation

    def _raw_defense(self, p: MatchPlayer) -> float:
        """Oyuncunun rol agirligindan ARINDIRILMIS savunma degeri (mutlak olcek)."""
        role = p.role or p.position
        return self._player_strength(p, "defense") / max(ROLE_WEIGHTS["defense"][role], 0.01)

    def _contest_weight(self, p: MatchPlayer) -> float:
        """Suta karsi cekilme agirligi: rol + zayifliga gore hedeflenme (hucum zayif halkayi arar)."""
        weight = DEFENCE_CONTEST_WEIGHT[p.role or p.position]
        if weight <= 0 or not self.cfg.defender_targeting:
            return weight
        # Yalnizca ZAYIF tarafa yuklenilir; iyi savunmacidan "kacilmaz" (kacsaydi iyi
        # savunmacinin katkisi yok olurdu -- o zaten sut HACMINI dusuruyor).
        ratio = max(1.0, self.cfg.defender_reference / max(self._raw_defense(p), 1.0))
        return weight * ratio ** self.cfg.defender_targeting

    def _contest_weight_am(self, p: MatchPlayer) -> float:
        """14B: _contest_weight x oyuncunun 'contest' kanali (pozisyon alma, onsezi, karar alma, guc: iyisi az
        hedeflenir). Ayni ifade satir ici (sicak yol)."""
        weight = DEFENCE_CONTEST_WEIGHT[p.role or p.position]
        if weight <= 0 or not self.cfg.defender_targeting:
            return weight * p._am.contest
        ratio = max(1.0, self.cfg.defender_reference / max(self._raw_defense(p), 1.0))
        return weight * ratio ** self.cfg.defender_targeting * p._am.contest

    def _defender_quality(self, defender_str: float) -> float:
        """Cekilen savunmacinin sans KALITESINE etkisi: zayif stoper daha net pozisyon verir."""
        cfg = self.cfg
        lo, hi = cfg.defender_quality_range
        return max(lo, min(hi, (cfg.defender_reference / max(defender_str, 1.0))
                           ** cfg.defender_quality_exponent))

    def _finishing_power(self, shooter: MatchPlayer, quality: float) -> float:
        """Sut -> gol adiminda sutorun KENDI bitiriciligi (mutlak olcek, rakipten bagimsiz)."""
        w = self.cfg.finishing_shooting_weight
        base = w * shooter.shooting + (1 - w) * shooter.overall
        power = base * shooter.condition_factor * shooter.fatigue_factor * quality
        if shooter._day_form != 1.0 or shooter._big_game != 1.0:
            # 15G: gunun formu ve macin agirligi BITIRICILIGE girer (tutarsiz forvet net sansi harcar).
            # Bayrak kapaliyken ikisi de tam 1.0: carpim HIC yapilmaz, kayan nokta bit-bit korunur.
            power *= shooter._day_form * shooter._big_game
        return power

    def _goal_probability(self, shooter: MatchPlayer, shooter_str: float, defending: MatchTeam,
                          quality: float, defender_str: float) -> float:
        """
        Isabetli sut -> gol. 13A/S3: adim sutorun KENDI bitiriciligine ve kalecinin
        kalitesine baglidir (mutlak olcek); cekilen savunmaci sansin netligini belirler.
        Boylece elit forvet sirandan forvetin ~2-2.5 katini atar, takim ustunlugu ise
        (S4) donusumu degil sut HACMINI buyutur.
        """
        keeper_str = self._keeper_strength(defending)
        if not self.cfg.weak_link:
            return self._scaled(self.cfg.base_goal, self._player_contest(shooter_str, keeper_str))
        cfg = self.cfg
        finish = self._finishing_power(shooter, quality)
        am = self._am
        if am is not None:
            # 14B: sans tipi sonuc yolunda zaten hesaplanan saf degerden (yogunluk x savunmaci kalitesi):
            # bitiricilik yakin, uzaktan sut uzak, fantezi net payda; refleks yakin payda agirlasir.
            clear = self._defender_quality(defender_str) * self._density
            far = attribute_model.far_share(shooter.role or shooter.position, clear, am)
            finish *= attribute_model.finish_factor(shooter._am, far, attribute_model.big_share(clear, am), am)
            keeper = defending.keeper
            if keeper is not None:
                keeper_str *= attribute_model.keeper_factor(keeper._am, far, am)
        lo, hi = cfg.finishing_range
        finish_factor = max(lo, min(hi, (finish / cfg.finishing_reference) ** cfg.finishing_sharpness))
        lo, hi = cfg.keeper_range
        keeper_factor = max(lo, min(hi, (cfg.keeper_reference / max(keeper_str, 1.0))
                                    ** cfg.finishing_sharpness))
        return max(0.02, min(0.80, cfg.base_goal_v2 * finish_factor * keeper_factor
                             * self._defender_quality(defender_str) * self._density))

    def _goal(self, team: MatchTeam, scorer: MatchPlayer, kind: str | None = None,
              assister: MatchPlayer | None = None) -> None:
        """kind None: akan oyun (asist cekilisi). Duran top ('penalty' / 'free_kick' / 'corner'): asist verilir."""
        scorer.goals += 1
        team.stats.goals += 1
        self._half_events += 1

        if kind is not None:
            self._set_piece_goal(team, scorer, kind, assister)
            return

        assister = None
        if self.rng.random() < self.cfg.assist_share:
            candidates = [p for p in team.outfield_on_pitch if p is not scorer]
            if self.cfg.role_realism and self._am is not None:
                # 14B: pas, yaraticilik, orta ve fantezi asistin kime dustugunu belirler
                assister = self._weighted_choice(
                    candidates,
                    lambda p: (p.midfield_rating + p.attack_rating * 0.5)
                    * ASSIST_ROLE_WEIGHT[p.role or p.position] * p._am.assist,
                )
            elif self.cfg.role_realism:
                # D7: asist eskiden rol agirligi OLMADAN cekiliyordu; sahada 4 savunmaci,
                # 2 forvet oldugu icin asistlerin %42.5'i savunmadan geliyordu.
                assister = self._weighted_choice(
                    candidates,
                    lambda p: (p.midfield_rating + p.attack_rating * 0.5)
                    * ASSIST_ROLE_WEIGHT[p.role or p.position],
                )
            else:
                assister = self._weighted_choice(candidates,
                                                 lambda p: p.midfield_rating + p.attack_rating * 0.5)
            if assister:
                assister.assists += 1
                self._drain(assister, self.cfg.assist_energy_cost)

        score = f"{self.home.name} {self.home.stats.goals} - {self.away.stats.goals} {self.away.name}"
        if not self.cfg.narration:
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
            return

        self._legacy_text_draw(5)              # eski flavor cekilisi: RNG akisi korunur
        self._nar.assister = assister
        tags = set(self._nar.shot_tags)
        tags.add(self._score_state_tag(team))
        tags.add("assisted" if assister is not None else "solo")
        frozen = frozenset(tags)
        slots = self._slots(team, scorer, keeper=self._opponent(team).keeper,
                            assister=assister, defender=self._nar.defender)
        live, report = self._nar.narrator.both("goal.open", frozen, slots)
        self._log(EventType.GOAL, team, scorer, f"{live} Skor: {score}",
                  report=report, **self._shot_meta("goal", frozen))

    # ------------------------------------------------------------------ duran toplar

    def _set_pieces_on(self) -> bool:
        flag = self.cfg.set_pieces
        if flag is not None:
            return flag
        return self.home.roles.has_set_piece_takers or self.away.roles.has_set_piece_takers

    def _set_piece_kind(self, fraction: float) -> str | None:
        """Pozisyonun [0, 1) dilimindeki yeri -> 'penalty' / 'free_kick' / 'corner' ya da None (akan oyun)."""
        edge = self.cfg.set_piece_penalty_share
        if fraction < edge:
            return "penalty"
        edge += self.cfg.set_piece_free_kick_share
        if fraction < edge:
            return "free_kick"
        edge += self.cfg.set_piece_corner_share
        if fraction < edge:
            return "corner"
        return None

    def _designated_or_best(self, team: MatchTeam, player_id: int | None,
                            skill: Callable[[MatchPlayer], float]) -> MatchPlayer | None:
        """Belirlenmis atici sahadaysa o; degilse sahadaki en iyi saha oyuncusu (esitlikte kucuk id)."""
        designated = team.on_pitch_by_id(player_id)
        if designated is not None:
            return designated
        pool = team.outfield_on_pitch or team.on_pitch
        return max(pool, key=lambda p: (skill(p), -p.id), default=None)

    def penalty_taker(self, team: MatchTeam) -> MatchPlayer | None:
        """Mac ici penaltiyi kim atar: belirlenmis atici sahadaysa o, yoksa _penalty_taker_skill sirasi."""
        return self._designated_or_best(team, team.roles.penalty_taker_id, self._penalty_taker_skill)

    def _set_shot_context(self, attacking: MatchTeam, defender_str: float | None, kind: str | None) -> None:
        """
        Sutun anlatim baglamini kurar: saklanan sans kalitesi (K6) + bicim etiketi.
        Penalti tanimi geregi net sanstir; korner kafa, frikik ceza sahasi onu demektir.
        """
        if kind == "penalty":
            self._nar.quality = max(1.25, self._density * 1.6)
        elif defender_str is None:
            self._nar.quality = self._density
        else:
            self._nar.quality = self._density * self._defender_quality(defender_str)
        if not self.cfg.narration:
            return
        q_tag = self._quality_tag(self._nar.quality)
        self._nar.shot_tags, self._nar.shot_ctx = _shot_context(
            q_tag, self._shot_form_tag(q_tag, kind), self._minute_tag(), False,
            self._game_state_tag(attacking))

    def _set_piece(self, kind: str, attacking: MatchTeam, defending: MatchTeam) -> None:
        if self.cfg.match_stats:
            # Duran top bir istatistiktir: korner atagin, frikik/penalti savunmanin faulu.
            if kind == "corner":
                attacking.stats.corners += 1
            else:
                defending.stats.fouls += 1
        if kind == "penalty":
            self._penalty_kick(attacking, defending)
            return
        if kind == "free_kick":
            taker = self._designated_or_best(attacking, attacking.roles.free_kick_taker_id,
                                             team_roles.free_kick_skill)
            if taker is None:
                return
            strength = ((0.4 * taker.overall + 0.6 * team_roles.free_kick_skill(taker))
                        * taker.condition_factor * taker.fatigue_factor * self.cfg.free_kick_quality)
            if self._am is not None:   # 14B: duran top (+ teknik) vurusun kalitesi
                strength *= taker._am.free_kick
            self._set_piece_shot(attacking, defending, taker, strength, kind, assister=None)
            return

        taker = self._designated_or_best(attacking, attacking.roles.corner_taker_id, team_roles.corner_skill)
        if taker is None:
            return
        outfield = attacking.outfield_on_pitch
        reference = sum(p.overall for p in outfield) / len(outfield) if outfield else taker.overall
        lo, hi = self.cfg.set_piece_delivery_range
        delivery = max(lo, min(hi, 1 + self.cfg.set_piece_skill_influence
                               * (team_roles.corner_skill(taker) - reference)))
        targets = [p for p in outfield if p is not taker] or [taker]
        header = self._weighted_choice(
            targets,
            lambda p: (team_roles.aerial_skill(p) / 50.0) ** 2 * AERIAL_ROLE_WEIGHT[p.role or p.position],
        )
        if header is None:
            return
        strength = ((0.4 * header.overall + 0.6 * team_roles.aerial_skill(header))
                    * header.condition_factor * header.fatigue_factor * self.cfg.corner_quality * delivery)
        if self._am is not None:
            # 14B: ortanin kalitesi (duran top, orta) ve savunmanin hava hakimiyeti (kafa, ziplama, guc, cesaret)
            strength *= taker._am.delivery / self._am_team(defending).aerial
        self._set_piece_shot(attacking, defending, header, strength, kind,
                             assister=taker if taker is not header else None)

    def _set_piece_goal_probability(self, shooter: MatchPlayer, strength: float,
                                    defending: MatchTeam, defender_str: float, kind: str | None = None) -> float:
        """
        Duran top sutunun gole donmesi. Akan oyundan farki: kalite zaten `strength`
        icinde (frikik/korner carpani), bu yuzden bitiricilik terimi dogrudan onu kullanir.
        """
        keeper_str = self._keeper_strength(defending)
        if not self.cfg.weak_link:
            return self._scaled(self.cfg.base_goal, self._player_contest(strength, keeper_str))
        cfg = self.cfg
        if self._am is not None and defending.keeper is not None:
            # 14B: frikik uzak (elle kontrol), korner kafasi yakin (refleks) sut
            keeper_str *= attribute_model.keeper_factor(defending.keeper._am, 1.0 if kind == "free_kick" else 0.0,
                                                        self._am)
        lo, hi = cfg.finishing_range
        finish_factor = max(lo, min(hi, (strength / cfg.finishing_reference) ** cfg.finishing_sharpness))
        lo, hi = cfg.keeper_range
        keeper_factor = max(lo, min(hi, (cfg.keeper_reference / max(keeper_str, 1.0))
                                    ** cfg.finishing_sharpness))
        return max(0.02, min(0.80, cfg.base_goal_v2 * finish_factor * keeper_factor
                             * self._defender_quality(defender_str) * self._density))

    def _set_piece_shot(self, attacking: MatchTeam, defending: MatchTeam, shooter: MatchPlayer,
                        strength: float, kind: str, assister: MatchPlayer | None) -> None:
        """Frikik ya da korner sutu: akan oyunla ayni isabet / gol yarismalari, kendi metinleri."""
        shooter.shots += 1
        attacking.stats.shots += 1
        self._drain(shooter, self.cfg.shot_energy_cost)
        defender_str = self._defensive_resistance(defending)
        strength *= self._situation_factor(attacking, "attack")
        self._set_shot_context(attacking, defender_str, kind)

        if shooter._day_form != 1.0 or shooter._big_game != 1.0:
            strength *= shooter._day_form * shooter._big_game     # 15G: aticinin gunu ve macin agirligi
        defender = self._nar.defender
        p_on_target = self._accuracy_probability(strength, defender_str)
        if self.rng.random() >= p_on_target:
            if self._rm is not None:       # 15G: duran topta da savunmaci cekilir (zincir yok)
                self._rating_chance(attacking, defending, shooter, defender, self._nar.quality, "miss", None)
            text = self._set_piece_text(kind, "miss", attacking, shooter, assister)
            self._log(EventType.MISS, attacking, shooter, text, detail=kind,
                      report=self._nar.report or None, **self._shot_meta("miss", self._nar.sp_tags))
            return
        shooter.shots_on_target += 1
        attacking.stats.shots_on_target += 1
        keeper = defending.keeper
        p_goal = self._set_piece_goal_probability(shooter, strength, defending, defender_str, kind)
        if self.rng.random() < p_goal:
            if self._rm is not None:
                self._rating_chance(attacking, defending, shooter, defender, self._nar.quality, "goal", None)
            self._goal(attacking, shooter, kind=kind, assister=assister)
            return
        if keeper is not None:
            keeper.saves += 1
        defending.stats.saves += 1
        if self._rm is not None:
            self._rating_chance(attacking, defending, shooter, defender, self._nar.quality, "save", None)
        text = self._set_piece_text(kind, "save", attacking, shooter, assister, keeper)
        self._log(EventType.SAVE, attacking, shooter, text, detail=kind,
                  report=self._nar.report or None, **self._shot_meta("save", self._nar.sp_tags))

    def _penalty_kick(self, attacking: MatchTeam, defending: MatchTeam) -> None:
        """Mac ici penalti: seri penaltiyla ayni olasilik modeli (penalties.conversion_probability)."""
        taker = self.penalty_taker(attacking)
        if taker is None:
            return
        keeper = defending.keeper
        keeper_skill = self._penalty_keeper_skill(keeper)
        skill = self._penalty_taker_skill(taker) + self._captain_composure(attacking)
        taker.shots += 1
        attacking.stats.shots += 1
        self._drain(taker, self.cfg.shot_energy_cost)
        self._set_shot_context(attacking, None, "penalty")
        if self.rng.random() < conversion_probability(skill, keeper_skill, config=self.cfg.shootout):
            taker.shots_on_target += 1
            attacking.stats.shots_on_target += 1
            if self._rm is not None:       # 15G: penaltide cekilen savunmaci yoktur (kaleci tek basina)
                self._rating_chance(attacking, defending, taker, None, self._nar.quality, "goal", None)
            self._goal(attacking, taker, kind="penalty")
            return
        if keeper is not None and self.rng.random() < save_share(keeper_skill, self.cfg.shootout):
            taker.shots_on_target += 1
            attacking.stats.shots_on_target += 1
            keeper.saves += 1
            defending.stats.saves += 1
            if self._rm is not None:
                self._rating_chance(attacking, defending, taker, None, self._nar.quality, "save", None)
            text = self._set_piece_text("penalty", "save", attacking, taker, None, keeper)
            self._log(EventType.SAVE, attacking, taker, text, detail="penalty",
                      report=self._nar.report or None,
                      priority=PRIORITY_HIGH, display_probability=1.0, dwell_ms=DWELL_CRUCIAL,
                      chance_quality=self._nar.quality, tags=self._nar.sp_tags)
            return
        text = self._set_piece_text("penalty", "miss", attacking, taker, None)
        self._log(EventType.MISS, attacking, taker, text, detail="penalty",
                  report=self._nar.report or None,
                  priority=PRIORITY_HIGH, display_probability=1.0, dwell_ms=DWELL_CRUCIAL,
                  chance_quality=self._nar.quality, tags=self._nar.sp_tags)

    def _set_piece_goal(self, team: MatchTeam, scorer: MatchPlayer, kind: str, assister: MatchPlayer | None) -> None:
        if assister is not None and assister is not scorer:
            assister.assists += 1
            self._drain(assister, self.cfg.assist_energy_cost)
        else:
            assister = None
        text = self._set_piece_text(kind, "goal", team, scorer, assister)
        self._log(EventType.GOAL, team, scorer, f"{text} Skor: {self._score_text()}", detail=kind,
                  report=self._nar.report or None, **self._shot_meta("goal", self._nar.sp_tags))

    def _set_piece_text(self, kind: str, outcome: str, team: MatchTeam, shooter: MatchPlayer,
                        assister: MatchPlayer | None, keeper: MatchPlayer | None = None) -> str:
        """
        Duran top cumlesi. 13B: banka satiri; gecmis zaman karsiligi `self._nar.report`
        icinde birakilir (cagiran olay kaydina yazar).
        """
        if self.cfg.narration:
            self._legacy_text_draw(2)          # eski rng.choice(iki secenek): akis korunur
            if outcome == "goal":
                tags = set(self._nar.shot_tags)
                tags.add(self._score_state_tag(team))
            else:
                tags = set(self._nar.shot_ctx)
            tags.add("assisted" if assister is not None else "solo")
            frozen = frozenset(tags)
            slots = self._slots(team, shooter, keeper=keeper, assister=assister)
            live, self._nar.report = self._nar.narrator.both(f"{outcome}.{kind}", frozen, slots)
            self._nar.sp_tags = frozen
            if kind == "penalty":                  # penaltinin nereden geldigi (canli akista baglam)
                live = f"PENALTI! {team.name} penaltı kazandı, topun başında {shooter.name}. {live}"
            return live
        self._nar.report = ""
        self._nar.sp_tags = frozenset()
        who = f"{shooter.name} ({team.name})"
        gk = keeper.name if keeper else "kaleci"
        if kind == "penalty":
            head = f"PENALTI! {team.name} penaltı kazandı, topun başında {shooter.name}."
            texts = {
                "goal": [f"{head} GOOOL! {shooter.name} kaleciyi ters köşeye yatırıyor!",
                         f"{head} GOOOL! {shooter.name} penaltıyı soğukkanlılıkla gole çeviriyor!"],
                "save": [f"{head} {gk} doğru köşeye uzanıp penaltıyı KURTARIYOR!",
                         f"{head} Vuruş zayıf, {gk} penaltıyı çeliyor!"],
                "miss": [f"{head} {shooter.name} topu direğin dışına gönderiyor, penaltı KAÇTI!",
                         f"{head} {shooter.name} üstten auta vuruyor, penaltı KAÇTI!"],
            }
        elif kind == "free_kick":
            texts = {
                "goal": [f"GOOOL! {who} serbest vuruşu barajın üstünden doksana asıyor!",
                         f"GOOOL! {who} frikikten muhteşem bir vuruşla topu ağlara gönderiyor!"],
                "save": [f"{who} serbest vuruşu kaleye çeviriyor, {gk} uçarak kurtarıyor!",
                         f"{who} frikikten sert vurdu, {gk} topu kornere çeliyor."],
                "miss": [f"{who} serbest vuruşu barajdan dönüyor.",
                         f"{who} frikikten şansını deniyor, top üstten auta gidiyor."],
            }
        else:
            by = f"{assister.name}'in kornerinde " if assister else "Korner sonrası "
            texts = {
                "goal": [f"GOOOL! {by}{shooter.name} ({team.name}) yükseliyor ve kafayla topu ağlara gönderiyor!",
                         f"GOOOL! {by}ceza sahası karışıyor, {shooter.name} ({team.name}) topu içeri itiyor!"],
                "save": [f"{by}{shooter.name} ({team.name}) kafayı vuruyor, {gk} gole izin vermiyor!",
                         f"{by}{shooter.name} ({team.name}) yakın direkte kafayı vurdu, {gk} kurtarıyor."],
                "miss": [f"{by}{shooter.name} ({team.name}) kafayı vuruyor, top üstten auta.",
                         f"{by}{shooter.name} ({team.name}) topa yükseliyor ama kafa vuruşu isabetsiz."],
            }
        return self.rng.choice(texts[outcome])

    def _miss_text(self, shooter: MatchPlayer, team: MatchTeam) -> str:
        """12. Asama metni (narration=False). 13B yolu: `_log_shot` + commentary bankasi."""
        texts = [
            f"{shooter.name} ({team.name}) şansını deniyor, top direğin yanından auta gidiyor.",
            f"{shooter.name} ({team.name}) uzaktan vuruyor, top üstten dışarı.",
            f"{shooter.name} ({team.name}) iyi pozisyonda ama vuruşu zayıf, top yandan auta çıkıyor.",
            f"{shooter.name} ({team.name}) direğe vuruyor! Top oyun alanına dönüyor ama defans uzaklaştırıyor.",
        ]
        # Direk nadir olmali (~%8), digerleri siradan kacirma
        return self.rng.choices(texts, weights=[40, 30, 22, 8], k=1)[0]

    def _save_text(self, shooter: MatchPlayer, team: MatchTeam, keeper: MatchPlayer | None) -> str:
        """12. Asama metni (narration=False). 13B yolu: `_log_shot` + commentary bankasi."""
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
            # kadro degisene kadar sabit (oyuncu saldirganligi mac boyunca degismez)
            aggression = t._cached("aggression", lambda: (sum([p.aggression for p in t.on_pitch])
                                                          / max(1, t.player_count)) / 0.85)
            return aggression * self._card_factor(t)     # sert oyun / pres kart riskini katlar, kaptan azaltir

        p_card = self.cfg.base_card * (team_factor(defending) + team_factor(attacking)) / 2
        share = self.cfg.defending_team_card_share
        def_factor, att_factor = self._card_factor(defending), self._card_factor(attacking)
        if def_factor != att_factor:
            # Kart daha sert oynayan takima daha olasi cikar (esit sertlikte payi degistirmez)
            share = share * def_factor / (share * def_factor + (1 - share) * att_factor)

        roll = self.rng.random()
        if roll >= p_card:
            if self.cfg.match_stats:
                # Kartsiz faul: kart cekilisinin artigindan turetilir, yeni sayi cekilmez.
                # Gercek lig ~22 faul/mac; bunlarin yalnizca ~4'u kart.
                limit = p_card + self.cfg.foul_share
                if roll < limit:
                    fraction = (roll - p_card) / max(1e-12, self.cfg.foul_share)
                    fouler = defending if fraction < share else attacking
                    fouler.stats.fouls += 1
                    if self.cfg.flow_events:
                        self._log_foul(fouler)
            return

        team = defending if self.rng.random() < share else attacking
        if self.cfg.match_stats:
            team.stats.fouls += 1
        # Sari gormus oyuncu daha temkinli oynar (ikinci sari enflasyonunu onler)
        cautious = (self.cfg.cautious_after_yellow_v2 if self.cfg.discipline_v2
                    else self.cfg.cautious_after_yellow)
        player = self._weighted_choice(
            team.on_pitch,
            lambda p: p.aggression * (cautious if p.yellow_cards else 1.0),
        )
        if player is None:
            return
        self._half_events += 1
        self._drain(player, self.cfg.foul_energy_cost)

        straight_share = (self.cfg.straight_red_share_v2 if self.cfg.discipline_v2
                          else self.cfg.straight_red_share)
        straight_red = self.rng.random() < straight_share * team.instructions.straight_red_factor
        if self.cfg.discipline_v2 and player.yellow_cards:
            # D6: sari gormus oyuncuya direkt kirmizi verilemez; bir sonraki faulu IKINCI SARIDIR.
            straight_red = False
        if straight_red:
            self._send_off(team, player, second_yellow=False)
            return

        player.yellow_cards += 1
        team.stats.yellow_cards += 1
        if player.yellow_cards >= 2:
            self._send_off(team, player, second_yellow=True)
            return

        if not self.cfg.narration:
            self._log(EventType.YELLOW_CARD, team, player, self.rng.choice([
                f"{player.name} ({team.name}) sert müdahale, hakem sarı kartı gösteriyor.",
                f"{player.name} ({team.name}) geç kalıyor ve rakibini düşürüyor: SARI KART.",
                f"{player.name} ({team.name}) itiraz ediyor, hakem cebine gidiyor: sarı kart.",
            ]))
            return
        self._legacy_text_draw(3)
        tags = frozenset((self._minute_tag(), self._game_state_tag(team)))
        live, report = self._nar.narrator.both("yellow.card", tags, self._slots(team, player))
        self._log(EventType.YELLOW_CARD, team, player, live, report=report,
                  priority=PRIORITY_MAIN, dwell_ms=DWELL_MAIN, tags=tags)

    def _send_off(self, team: MatchTeam, player: MatchPlayer, second_yellow: bool) -> None:
        player.sent_off = True
        team.remove_player(player, self.minute)
        team.stats.red_cards += 1
        was_keeper = player.role is Position.GK

        detail = "second_yellow" if second_yellow else "straight_red"
        if self.cfg.narration:
            tags = frozenset((self._minute_tag(), self._game_state_tag(team)))
            key = "red.second" if second_yellow else "red.straight"
            live, report = self._nar.narrator.both(key, tags, self._slots(team, player))
            self._log(EventType.RED_CARD, team, player,
                      f"{live} {team.name} {team.player_count} kişi kaldı.",
                      detail=detail, report=report, priority=PRIORITY_CRUCIAL,
                      dwell_ms=DWELL_CRUCIAL, tags=tags)
        else:
            reason = "ikinci sarıdan KIRMIZI KART" if second_yellow else "korkunç bir faul, direkt KIRMIZI KART"
            self._log(EventType.RED_CARD, team, player,
                      f"{player.name} ({team.name}) {reason}! {team.name} {team.player_count} kişi kaldı.",
                      detail=detail)
        if was_keeper:
            self._ensure_keeper(team)

    # ------------------------------------------------------------------ sakatlik

    def _injury_check(self) -> None:
        # Sert oynayan takim ikili mucadeleleri sertlestirir: macin (iki taraf icin) sakatlik riski artar
        risk = (self.home.instructions.injury_factor + self.away.instructions.injury_factor) / 2
        if self.rng.random() >= self.cfg.base_injury * risk:
            return
        team = self.rng.choice((self.home, self.away))
        if self._am is not None:       # 14B: gizli sakatlik egilimi + cesaret kurban agirligini belirler
            player = self._weighted_choice(
                team.on_pitch,
                lambda p: (1 + max(0, p.age - 29) * 0.10) * (1 + (100 - p.energy) / 100) * p._am.injury,
            )
        else:
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
        if self.cfg.narration:
            self._legacy_text_draw(3)
            tags = frozenset((self._minute_tag(),))
            live, report = self._nar.narrator.both("injury.play", tags, self._slots(team, player))
            self._log(EventType.INJURY, team, player, live, report=report,
                      priority=PRIORITY_HIGH, dwell_ms=DWELL_MAIN, tags=tags)
        else:
            self._log(EventType.INJURY, team, player, self.rng.choice([
                f"{player.name} ({team.name}) yerde kaldı, sağlık ekibi sahada... Oyuna devam edemiyor!",
                f"{player.name} ({team.name}) ikili mücadelede sakatlandı, sedyeyle oyundan ayrılıyor.",
                f"{player.name} ({team.name}) kas sakatlığı işareti veriyor ve oyunu bırakmak zorunda.",
            ]))
        self._substitute_for(team, player)

    def _substitute_for(self, team: MatchTeam, out: MatchPlayer) -> None:
        """Sakatlanan oyuncunun yerine uygun yedegi sokar."""
        role = out.role or out.position

        blocked = self.substitution_block(team)
        if blocked or not team.bench:
            reason = blocked or "kulübede oyuncu kalmadı"
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
            if outfield and self.substitution_block(team) is None:
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
        self._register_sub(team)
        self._log(EventType.SUBSTITUTION, team, sub, f"Değişiklik ({team.name}): {text}")

    def _ensure_keeper(self, team: MatchTeam) -> None:
        """Kalede kimse yoksa: yedek GK (saha oyuncusu feda) veya acil durum kalecisi."""
        if team.keeper is not None:
            return
        bench_gk = [p for p in team.bench if p.position is Position.GK]
        outfield = team.outfield_on_pitch

        if bench_gk and outfield and self.substitution_block(team) is None:
            victim = min(outfield, key=lambda p: p.effective_power)
            team.remove_player(victim, self.minute)
            victim.substituted = True
            sub = bench_gk[0]
            self._bring_on(team, sub, Position.GK,
                           f"Kaleci için feda: {victim.name} çıkıyor, yedek kaleci {sub.name} giriyor.")
            return

        if outfield:
            emergency = max(outfield, key=lambda p: p.goalkeeping)
            old_role = emergency.role or emergency.position
            emergency.role_changes.append((len(self.events), old_role, Position.GK))
            emergency.role = Position.GK
            if Position.GK in team.open_slots:       # 14E: kale yuvasi doldu, bos kalan onun eski yuvasi
                team.open_slots.remove(Position.GK)
                team.open_slots.append(old_role)
            team.touch_lineup()                      # rol degisti: kadro onbellegi gecersiz
            self._log(EventType.SUBSTITUTION, team, emergency,
                      f"{team.name} kalede yedek kaleci yok! {emergency.name} eldivenleri giyiyor.")

    # ------------------------------------------------------------------ menajer mudahaleleri (9. Asama)

    def _team(self, team: MatchTeam | int) -> MatchTeam:
        if isinstance(team, MatchTeam):
            if team is not self.home and team is not self.away:
                raise InterventionError(f"{team.name} bu maçta oynamıyor.")
            return team
        return self.team_by_id(team)

    def _require_open(self, action: str) -> None:
        if self.finished:
            raise InterventionError(f"Maç bitti; {action} yapılamaz.")
        if self.phase is MatchPhase.PENALTIES:
            raise InterventionError(f"Seri penaltılar sürüyor; {action} yapılamaz.")

    def manual_substitution(self, team: MatchTeam | int, out_id: int, in_id: int,
                            role: Position | None = None) -> MatchEvent:
        """
        Menajerin oyuncu degisikligi. Rastgele sayi cekmez. Kurallar: mac baslamis ve bitmemis
        olmali; cikan sahada, giren kulubede ve oynamaya uygun (sakat/atilmis/oyundan cikmis/kadro
        disi degil); hak ve pencere kalmis olmali (devre arasi pencere saymaz); kaleci yalnizca
        kaleciyle (GK rolunde) degisir. role verilmezse giren oyuncu cikanin gorevini alir.
        """
        return self._substitute_players(self._team(team), out_id, in_id, role, detail="manual",
                                        suffix=" — menajer kararı.")

    def _substitute_players(self, team: MatchTeam, out_id: int, in_id: int, role: Position | None,
                            detail: str, suffix: str) -> MatchEvent:
        """manual_substitution ve oyun plani ortak yolu: kurallar, durum ve olay (rastgele sayi cekmez)."""
        self._require_open("oyuncu değişikliği")
        if not self.started:
            raise InterventionError("Maç başlamadan oyuncu değişikliği yapılamaz; ilk 11'i Kadro & Taktik sekmesinden kur.")
        if self._tick == 0:
            raise InterventionError("Maç yeni başladı; değişiklik için ilk dakikanın oynanmasını bekle.")
        by_id = {p.id: p for p in team.players}
        out, sub = by_id.get(out_id), by_id.get(in_id)
        if out is None:
            raise InterventionError(f"Çıkacak oyuncu (#{out_id}) {team.name} kadrosunda değil.")
        if not out.on_pitch:
            raise InterventionError(f"{out.name} şu an sahada değil.")
        if sub is None:
            raise InterventionError(f"Girecek oyuncu (#{in_id}) {team.name} kadrosunda değil.")
        if sub.on_pitch:
            raise InterventionError(f"{sub.name} zaten sahada.")
        if not sub.available_on_bench:
            if sub.injured or sub.sent_off or sub.played:
                raise InterventionError(f"{sub.name} bu maçta oynadı ve oyundan çıktı; tekrar giremez.")
            raise InterventionError(f"{sub.name} maç kadrosunda değil (kadro dışı).")

        out_role = out.role or out.position
        role = role or out_role
        if out_role is Position.GK and role is not Position.GK:
            raise InterventionError(f"Kaleci {out.name} çıkıyor: giren oyuncu kaleye geçmeli (görev: GK).")
        if role is Position.GK and out_role is not Position.GK and team.keeper is not None:
            raise InterventionError(
                f"Kalede zaten {team.keeper.name} var; kaleciyi değiştirmek için kaleciyi çıkar.")

        blocked = self.substitution_block(team)
        if blocked:
            raise InterventionError(f"{team.name}: {blocked} ({team.subs_used}/{self.max_subs} değişiklik).")
        team.remove_player(out, self.minute)
        out.substituted = True
        team.field_player(sub, role, self.minute)
        self._register_sub(team)

        text = f"Değişiklik ({team.name}): {out.name} çıkıyor, yerine {sub.name} giriyor"
        if role is not sub.position:
            text += f" ({sub.position.value} → {role.value}, mevki dışı)"
        return self._log(EventType.SUBSTITUTION, team, sub, text + suffix, detail=detail)

    def change_formation(self, team: MatchTeam | int, formation: str | tuple[int, int, int]) -> MatchEvent | None:
        """
        Canli dizilis degisikligi (MATCH_FORMATIONS). Sahadaki saha oyunculari yeni hatlara
        dagitilir: once dogal mevkiler, bos kalan slotlara hatta en uygun oyuncu (mevki disi
        cezasiyla). Eksik oyuncuda once forvet, sonra orta saha slotu duser. FORMATION_STYLE
        carpanlari kalan dakikalarda gecerlidir. Degisiklik yoksa None.
        """
        return self._change_formation(self._team(team), formation, prefix=None, detail="formation")

    def _change_formation(self, team: MatchTeam, formation: str | tuple[int, int, int],
                          prefix: str | None, detail: str) -> MatchEvent | None:
        self._require_open("diziliş değişikliği")
        if isinstance(formation, str):
            if formation not in MATCH_FORMATIONS:
                raise InterventionError(
                    f"Bilinmeyen diziliş: {formation}. Seçenekler: {', '.join(MATCH_FORMATIONS)}")
            shape = MATCH_FORMATIONS[formation]
        else:
            shape = tuple(formation)
            if shape not in MATCH_FORMATIONS.values():
                raise InterventionError(f"Bilinmeyen diziliş: {formation_name(shape)}.")
        actual = self._shape(team)[1:] if self.cfg.tactics_v2 else None
        if shape == team.formation and (actual is None or actual == shape):
            return None

        old = team.formation if actual is None else actual      # 14E: metin sahadaki gercek sekli yazar
        team.formation = shape
        index = len(self.events)
        moves = self._reassign_roles(team)
        team.reset_open_slots()          # 14E: eksik kadroda dusurulen yuvalar bos yuva olur
        if not self.started:
            return None                  # baslama oncesi: roller macin basindan gecerli, olay yok
        for p, old_role, new_role in moves:
            p.role_changes.append((index, old_role, new_role))

        head = prefix or f"Taktik değişikliği ({team.name})"
        text = f"{head}: diziliş {formation_name(old)} → {formation_name(shape)}."
        if moves:
            text += " Yeni görevler: " + ", ".join(f"{p.name} {o.value}→{n.value}" for p, o, n in moves) + "."
        off = [p.name for p in team.outfield_on_pitch if p.role is not p.position]
        if off:
            text += " Mevki dışı oynayanlar: " + ", ".join(off) + "."
        return self._log(EventType.TACTICAL_CHANGE, team, None, text, detail=detail)

    def _reassign_roles(self, team: MatchTeam) -> list[tuple[MatchPlayer, Position, Position]]:
        """Sahadaki saha oyuncularini team.formation hatlarina dagitir; (oyuncu, eski, yeni) listesi."""
        outfield = sorted(team.outfield_on_pitch, key=lambda p: p.id)
        slots = dict(zip((Position.DEF, Position.MID, Position.FWD), team.formation, strict=True))
        deficit = sum(slots.values()) - len(outfield)
        while deficit > 0:
            role = next((r for r in _DROP_ORDER if slots[r] > _MIN_LINE[r]), None)
            if role is None:
                role = max(_DROP_ORDER, key=lambda r: slots[r])
                if slots[role] == 0:
                    break
            slots[role] -= 1
            deficit -= 1
        if deficit < 0:
            slots[Position.MID] -= deficit       # kalecisiz kadro: fazlalar orta sahaya

        rating = {
            Position.DEF: lambda p: p.defense_rating,
            Position.MID: lambda p: p.midfield_rating,
            Position.FWD: lambda p: p.attack_rating,
        }
        assigned: dict[int, Position] = {}
        remaining = list(outfield)
        for role in (Position.DEF, Position.MID, Position.FWD):
            natural = sorted((p for p in remaining if p.position is role),
                             key=lambda p, r=role: (p.role is not r, -rating[r](p), p.id))
            chosen = natural[:slots[role]]
            for p in chosen:
                assigned[p.id] = role
            slots[role] -= len(chosen)
            remaining = [p for p in remaining if p.id not in assigned]
        for role in (Position.DEF, Position.MID, Position.FWD):
            for _ in range(slots[role]):
                if not remaining:
                    break
                best = max(remaining, key=lambda p, r=role: (p.role is r, rating[r](p), -p.id))
                assigned[best.id] = role
                remaining.remove(best)

        moves: list[tuple[MatchPlayer, Position, Position]] = []
        for p in outfield:
            new_role = assigned.get(p.id, p.role or p.position)
            old_role = p.role or p.position
            if new_role is not old_role:
                p.role = new_role
                moves.append((p, old_role, new_role))
        if moves:
            team.touch_lineup()                      # roller degisti: kadro onbellegi gecersiz
        return moves

    def set_instructions(self, team: MatchTeam | int, instructions: TeamInstructions) -> MatchEvent | None:
        """
        Takim talimati (zihniyet, sertlik, pas stili, tempo, pres, hucum yonu, ofsayt, kontra).
        Baslama oncesi olay yazmaz. Degisiklik yoksa None. Olay metni yalnizca degisen eksenleri sayar.
        """
        return self._set_instructions(self._team(team), instructions, prefix=None, detail="instructions")

    def _set_instructions(self, team: MatchTeam, instructions: TeamInstructions, prefix: str | None,
                          detail: str) -> MatchEvent | None:
        self._require_open("talimat değişikliği")
        if not isinstance(instructions, TeamInstructions):
            raise InterventionError("Talimat TeamInstructions olmalı.")
        old = team.instructions
        if instructions == old:
            return None
        team.instructions = instructions
        if not self.started:
            return None
        head = prefix or f"Talimat ({team.name})"
        text = f"{head}: " + ", ".join(instructions.changes_from(old)) + "."
        return self._log(EventType.TACTICAL_CHANGE, team, None, text, detail=detail)

    def set_roles(self, team: MatchTeam | int, roles: SetPieceRoles) -> None:
        """Kaptan ve duran top aticilari (mac oncesi ya da canli). Olay yazmaz, rastgele sayi cekmez."""
        team = self._team(team)
        self._require_open("rol değişikliği")
        if not isinstance(roles, SetPieceRoles):
            raise InterventionError("Roller SetPieceRoles olmalı.")
        team.roles = roles

    def set_plan(self, team: MatchTeam | int, plan: MatchPlan) -> None:
        """
        Oyun planini degistirir; islenmis kural kaydi sifirlanir (dakikasi gecmis kurallar kosulu
        saglanirsa bir sonraki duraklamada islenir). Olay yazmaz.
        """
        team = self._team(team)
        self._require_open("oyun planı değişikliği")
        if not isinstance(plan, MatchPlan):
            raise InterventionError("Oyun planı MatchPlan olmalı.")
        team.plan = plan
        team.plan_fired.clear()

    def set_plans_enabled(self, team: MatchTeam | int, enabled: bool) -> None:
        """Oyun planini acar / kapatir (kapaliyken kurallar yoklanmaz; islenmis kural kaydi korunur)."""
        self._team(team).plans_enabled = bool(enabled)

    # ------------------------------------------------------------------ oyun plani

    def _run_plan(self, team: MatchTeam) -> None:
        for index, rule in enumerate(team.plan.rules):
            if index in team.plan_fired or not rule.enabled:
                continue
            if not rule.trigger.matches(self.minute, -self._deficit(team)):
                continue
            team.plan_fired.add(index)
            self._apply_plan_rule(team, index, rule)

    def _apply_plan_rule(self, team: MatchTeam, index: int, rule: PlanRule) -> None:
        """Eylemler sirayla: oyuncu degisikligi, dizilis, talimat. Uygulanamayan eylem aciklamali olayla atlanir."""
        label = f"kural {index + 1}" + (f" «{rule.name}»" if rule.name else "") + f": {rule.trigger.describe()}"
        prefix = f"Oyun planı ({team.name}, {label})"
        action = rule.action

        def skipped(what: str, reason: str) -> None:
            self._log(EventType.TACTICAL_CHANGE, team, None, f"{prefix}: {what} uygulanamadı — {reason}",
                      detail="plan_skipped")

        if action.has_substitution:
            try:
                self._substitute_players(team, action.sub_out_id, action.sub_in_id, None, detail="plan",
                                         suffix=f" — oyun planı ({label}).")
            except InterventionError as exc:
                skipped("oyuncu değişikliği", _sentence(str(exc)))
        if action.formation is not None:
            try:
                if self._change_formation(team, action.formation, prefix=prefix, detail="plan") is None:
                    skipped("diziliş değişikliği", f"takım zaten {formation_name(team.formation)} oynuyor.")
            except InterventionError as exc:
                skipped("diziliş değişikliği", _sentence(str(exc)))
        if action.instructions:
            try:
                wanted = team.instructions.with_changes(action.instructions)
            except ValueError as exc:
                skipped("talimat değişikliği", _sentence(str(exc)))
            else:
                if self._set_instructions(team, wanted, prefix=prefix, detail="plan") is None:
                    skipped("talimat değişikliği", "talimatlar zaten istenen durumda.")

    # ------------------------------------------------------------------ AI talimatlari

    def _strength_ratio(self, team: MatchTeam) -> float:
        """AI icin kaba guc orani: sahadakilerin (yorgunluk haric) gucu, oyuncu sayisiyla."""
        def power(t: MatchTeam) -> float:
            return sum(p.overall * p.condition_factor for p in t.on_pitch)
        opp = power(self._opponent(team))
        return power(team) / opp if opp > 0 else 2.0

    def _ai_tactics_update(self, force: bool = False) -> None:
        """
        manager_controlled olmayan ve aktif oyun plani olmayan takimlarin talimatini ai_instructions ile
        gunceller: duduk oncesi, her ai_tactics_interval dakikada bir ve skor / oyuncu sayisi degisince.
        """
        interval = max(1, self.cfg.ai_tactics_interval)
        for team in (self.home, self.away):
            if team.manager_controlled or (team.plans_enabled and team.plan.rules):
                continue
            state = (-self._deficit(team), team.player_count, self._opponent(team).player_count)
            changed = self._ai_state.get(team.id) != state
            due = self.added == 0 and self.minute % interval == 0
            if not (force or changed or due):
                continue
            self._ai_state[team.id] = state
            ratio = self._strength_ratio(team)
            is_home = team is self.home and not self.neutral_venue
            wanted = ai_instructions(ratio, is_home, state[0], self.minute)
            if self.cfg.tactics_v2:
                wanted = ai_counter_move(wanted, self._opponent(team).instructions)   # 14E: gorunen talimata
            self._set_instructions(team, wanted, prefix=None, detail="ai")
            if self.cfg.tactics_v2 and not force:
                # 14E: AI dizilis de degistirir (en cok AI_FORMATION_MAX_CHANGES kez); metin gorunen gercegi yazar
                side = 0 if team is self.home else 1
                if self._ai_shape_changes[side] < AI_FORMATION_MAX_CHANGES:
                    shape = ai_formation(ratio, is_home, state[0], self.minute, self._shape(team)[1:])
                    if shape is not None and self._change_formation(team, shape, prefix=None, detail="ai"):
                        self._ai_shape_changes[side] += 1


    # ------------------------------------------------------------------ notlar

    # ------------------------------------------------------------------ 15G not defteri

    def _rating_chance(self, attacking: MatchTeam, defending: MatchTeam, shooter: MatchPlayer,
                       defender: MatchPlayer | None, clarity: float, outcome: str,
                       fraction: float | None) -> None:
        """
        15G: bir sansin not defterine islenmesi. Yeni rastgele sayi CEKILMEZ; zincirin sekli pozisyon
        cekilisinin artigindan (fraction), zincirde kimin yer aldigi 15G'nin kendi tohumlu akisindan gelir.
        defender: o sutta GERCEKTEN cekilen savunmaci (penaltide yoktur).
        """
        rm = self._rm
        conceded = outcome == "goal"
        if defender is not None:
            # Cekilen BELIRLI savunmaci: kapattigi net sans degerli, yedigi gol pahali
            defender._duel_credit += attribute_model.duel_value(clarity, conceded, rm)
        keeper = defending.keeper
        if keeper is not None:
            if outcome == "save":
                keeper._keeper_credit += attribute_model.save_value(clarity, rm)
            elif conceded:
                keeper._keeper_credit -= attribute_model.concede_value(clarity, rm)
        if fraction is not None:
            self._rating_chain(attacking, shooter, fraction, conceded)

    def _rating_chain(self, attacking: MatchTeam, shooter: MatchPlayer, fraction: float,
                      goal: bool) -> None:
        """Atak zincirinde yer alanlarin payi. Halka sayisi _chain_shape ile AYNI artiktan gelir."""
        rm = self._rm
        links = len(self._chain_shape(fraction))
        if goal and links == 0:
            links = 1                       # gole giden atagin son pasi her zaman bir halkadir
        if links == 0:
            return
        weights = self._chain_w
        # (oyuncu, agirlik) havuzu kadro / rol degisene kadar gecerli (sicak yol: sutor basina liste kurmak
        # ve toplami yeniden hesaplamak macin %1'ini yiyordu).
        pool, total_all = attacking._cached("chain15g", lambda: _chain_pool(attacking.outfield_on_pitch, weights))
        total = total_all - weights[shooter.role or shooter.position]
        if total <= 0.0:
            return
        value = rm.chain_goal_link if goal else rm.chain_link
        rng = self._chain_rng
        for _ in range(links):
            pick = rng.random() * total
            for p, w in pool:
                if p is shooter:
                    continue
                pick -= w
                if pick <= 0.0:
                    p._chain_credit += value
                    break

    def _compute_ratings(self) -> None:
        for team in (self.home, self.away):
            opp = self._opponent(team)
            won = team.stats.goals > opp.stats.goals
            lost = team.stats.goals < opp.stats.goals
            clean_sheet = opp.stats.goals == 0
            for p in team.players:
                if p.played:
                    p.rating = rating_value(p, won, lost, clean_sheet, self.end_minute, self._rm)

    def _man_of_the_match(self) -> MatchPlayer | None:
        played = [p for t in (self.home, self.away) for p in t.players if p.played]
        return max(played, key=lambda p: (p.rating, p.goals, p.assists), default=None)


# ---------------------------------------------------------------------------- notlar (tek kaynak)
# Motor mac sonunda, canli mac ekrani (match_day_view) her dakikada AYNI fonksiyonu cagirir: boylece
# gosterilen not ile motorun yazdigi not arasinda fark olamaz (parite testli). K12: hicbir ara deger
# (not defteri, gunun formu, gizli ozellik) disari verilmez -- donen tek sey NOTUN KENDISIDIR.


def _rating_legacy(p: MatchPlayer, won: bool, lost: bool, clean_sheet: bool, end_minute: int) -> float:
    """14E notu (EngineConfig.rating_model kapali): sayilan katkilar + sonuc + yorgunluk."""
    r = 6.0
    r += p.goals * 1.0 + p.assists * 0.5 + p.shots_on_target * 0.1 + p.saves * 0.2
    r -= p.yellow_cards * 0.3 + (1.5 if p.sent_off else 0)
    if clean_sheet and p.role in (Position.GK, Position.DEF):
        r += 0.5
    r += 0.3 if won else (-0.3 if lost else 0)
    left = p.left_minute if p.left_minute is not None else end_minute
    played_min = left - (p.entered_minute or 0)
    if played_min < 20:
        r = 6.0 + (r - 6.0) * 0.5
    else:
        # Yorgun oyuncu hata yapar: mac sonu (veya cikis) enerjisine gore ceza
        r -= fitness.fatigue_rating_penalty(p.energy)
    return round(max(1.0, min(10.0, r)), 1)


def _rating_15g(p: MatchPlayer, rm: RatingModelConfig, won: bool, lost: bool, clean_sheet: bool,
                end_minute: int) -> float:
    """15G notu: taban + sayilan katkilar + not defteri (duello, kurtarisin netligi, zincir) + gunun formu."""
    r = rm.base
    r += p.goals * rm.goal + p.assists * rm.assist + p.shots_on_target * rm.shot_on_target
    r += p._duel_credit + p._chain_credit + p._keeper_credit
    r += rm.form_rating_weight * (p._day_form - 1.0)      # gunun formu (tutarlilik) performanstir
    r -= p.yellow_cards * rm.yellow + (rm.red if p.sent_off else 0.0)
    if clean_sheet and p.role in (Position.GK, Position.DEF):
        r += rm.clean_sheet
    r += rm.won if won else (-rm.won if lost else 0.0)
    left = p.left_minute if p.left_minute is not None else end_minute
    played_min = left - (p.entered_minute or 0)
    if played_min < rm.short_spell_minutes:
        r = rm.base + (r - rm.base) * rm.short_spell_share
    else:
        r -= fitness.fatigue_rating_penalty(p.energy)
    lo, hi = rm.range
    return round(max(lo, min(hi, r)), 1)


def rating_value(p: MatchPlayer, won: bool, lost: bool, clean_sheet: bool, end_minute: int,
                 ratings: RatingModelConfig | None = None) -> float:
    """
    Oyuncunun o andaki notu. `ratings` verilmezse oyuncunun KENDI durumundan anlasilir: 15G not modeli
    acik bir macta oynayan oyuncunun gizli ucluSU doludur (_trait), kapali macta None'dir. Boylece ayni
    fonksiyon hem motorda hem canli ekranda, macin hangi bayrakla oynandigini bilmeden dogru calisir.
    """
    if ratings is None and p._trait is not None:
        ratings = EngineConfig().ratings
    if ratings is None:
        return _rating_legacy(p, won, lost, clean_sheet, end_minute)
    return _rating_15g(p, ratings, won, lost, clean_sheet, end_minute)


def live_rating(player: MatchPlayer, team_goals: int, opp_goals: int, now_minute: int) -> float | None:
    """
    Mac SURERKEN gosterilen not (canli mac ekrani). Mac bitiminde (now_minute = bitis dakikasi) motorun
    yazdigi `player.rating` ile BIREBIR aynidir (parite testi, 200 tohum). Oynamamis oyuncu: None.
    """
    if not player.played:
        return None
    return rating_value(player, team_goals > opp_goals, team_goals < opp_goals, opp_goals == 0, now_minute)


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
    fixture, engine = prepare_fixture(db, fixture_id, seed=seed, config=config, current_week=current_week,
                                      knockout=knockout, neutral_venue=neutral_venue,
                                      unavailability=unavailability)
    result = engine.simulate()
    if persist:
        apply_result(fixture, result, update_table=update_table)
    return result


def prepare_fixture(db, fixture_id: int, seed: int | None = None,
                    config: EngineConfig | None = None,
                    current_week: int | None = None,
                    knockout: KnockoutRule | None = None,
                    neutral_venue: bool = False,
                    unavailability: Callable[[Any], str | None] | None = None):
    """
    Fiksturun motorunu kurar ama OYNATMAZ: (fixture, MatchEngine). play_fixture ve canli mac
    (9. Asama) ayni kurulumu kullanir; ayni tohumla mudahalesiz canli mac otomatik macla aynidir.
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
    engine = MatchEngine(home, away, seed=seed, config=config, knockout=knockout, neutral_venue=neutral_venue)
    return fixture, engine


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
    EventType.TACTICAL_CHANGE: "[TAKTİK]",
    EventType.CORNER: "[KORNER]", EventType.FOUL: "[FAUL]",
    EventType.OFFSIDE: "[OFSAYT]", EventType.BUILD_UP: "[ATAK]",
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
    poss_home, poss_away = result.possession_share() or (50, 50)
    rows = [
        ("Gol", h.stats.goals, a.stats.goals),
        ("Şut", h.stats.shots, a.stats.shots),
        ("İsabetli şut", h.stats.shots_on_target, a.stats.shots_on_target),
        ("Kurtarış", h.stats.saves, a.stats.saves),
        ("Topla oynama", f"%{poss_home}", f"%{poss_away}"),
        ("Korner", h.stats.corners, a.stats.corners),
        ("Faul", h.stats.fouls, a.stats.fouls),
        ("Ofsayt", h.stats.offsides, a.stats.offsides),
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
