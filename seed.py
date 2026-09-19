"""
seed.py
=======
Veritabanini sifirlar ve baslangic dunyasini yazar.

Veri kaynaklari:
    fm        : data/fm/ klasorundeki Football Manager disa aktarimlari (fm_parser.py)
                -> oyuncu adlari, yaslar, kulupler, 1-20 FM ozellikleri
    synthetic : kurgusal 6 lig / 24 takim / 360 oyuncu (VARSAYILAN; testler ve FM verisi yokken)
    open      : data/open/ acik verisi (13F. Asama, openfootball / CC0, bkz. open_loader.py)
                -> GERCEK kulup ve lig adlari (Galatasaray, Real Madrid CF, Süper Lig...),
                   kadrolarin tamami URETILMIS. Maskeleme bu adlara dokunmaz (data_source="open").
                   Istege baglidir: varsayilan dunya sentetik kalir.

Isim maskeleme (8. Asama): veritabanina HICBIR gercek kulup/lig/oyuncu adi yazilmaz.
    * FM verisi dosya okunurken maskelenir (fm_parser.parse_files -> name_masking).
    * resolve_world'un son adimi mask_world'dur (iki kaynak icin de; maskeli veride idempotent).
    * validate_world maskelenmemis gercek kulup/lig adi bulursa dunya DB'ye dokunmadan reddedilir;
      write_world ayni denetimi son emniyet kilidi olarak tekrarlar.
    Seviye: --mask-level off|light|strong (varsayilan SEED_NAME_MASKING ortam degiskeni, yoksa light).

    MASKELEME KAPALI (off, 13. Asama): kullanicinin KENDI lisansli FM disa aktarimiyla, gercek
    kulup/lig/oyuncu adlariyla yerel oynamasi icindir. KAZAYLA ACILAMAZ -- CIFT onay gerekir:
    seviyenin acikca verilmesi (--mask-level off; SEED_NAME_MASKING tek basina yetmez, bkz.
    default_mask_level) ve OFM_ALLOW_REAL_NAMES=1 (name_masking.real_names_allowed). Bu seviyede:
        * hicbir ad donusturulmez (kimlik eslemesi), kulupler rehberdeki gercek adlariyla gruplanir,
        * "maskelenmemis gercek isim" kilidi UYGULANMAZ (o kilit maskeli dunyalari korur),
        * seed raporunda ve --verify-only ciktisinda MASK_OFF_WARNING basilir,
        * secilen seviye game_state.mask_level'a yazilir: dogrulama raporu ve arayuz dunyanin
          gercek isim tasidigini bilir, worlds.py bu dunyanin paylasilan dunyaya cevrilmesini reddeder.
      Diger dogrulamalar (tekrarlanan lig/kulup adi, kaleci sayisi, yas araligi) aynen calisir.
      Boyle bir dunya KISISELDIR ve TEK KOLTUKLUDUR: veritabani dokumu/yedegi, FM disa aktarimlari ve
      gercek adli bir yapi paylasilamaz, yayimlanamaz, depoya eklenemez. Depo varsayilani "light".

Varsayilan 'auto': data/fm/ icinde disa aktarim varsa FM, yoksa sentetik.
    (Bu CLI ve seed() varsayilanidir. Web'den acilan YENI kariyer ve paylasilan dunya kaynagini
    new_world_source() secer: OFM_NEW_WORLD_SOURCE > acik veri > sentetik. 16A-0 / K-S11: data/fm
    disa aktarimi web'den acilan dunyada KENDILIGINDEN secilmez; FM tabanli yeni kariyer yalnizca
    OFM_NEW_WORLD_SOURCE=fm ya da bu CLI (--source fm) ile acilir.)

Calistirma:
    python seed.py                            # auto
    python seed.py --source fm                # data/fm/ zorunlu, yoksa hata
    python seed.py --fm dosya1.html dosya2.csv
    python seed.py --fm-sample                # paketteki KURGUSAL ornekle FM akisini dene
    python seed.py --source synthetic
    python seed.py --source open              # ACIK VERI: gercek kulup/lig adlari (data/open/, CC0)
    python seed.py --open-sample              # kucuk acik veri kumesiyle dene (lig basina 6 kulup)
    python seed.py --verify-only | --hard-reset | --keep | --seed N | --no-fixtures
    python seed.py --mask-level strong        # FM oyuncularina tamamen kurgusal adlar
    OFM_ALLOW_REAL_NAMES=1 python seed.py --source fm --mask-level off
                                              # KISISEL/YEREL: kendi FM verinin GERCEK adlari (cift onay)
    OFM_ALLOW_REAL_PLAYERS=1 python seed.py --real-players --career-schema <sema>
                                              # 16G KISISEL/YEREL: GERCEK OYUNCU KADROLARI (Wikidata, data/local/
                                              # squads.json) + data/fm'de disa aktarim varsa FM yamasi (cift onay;
                                              # yalnizca tek koltuklu kariyer, bkz. "Gercek oyuncular" asagida)

Gercek oyuncular (16G, sahip karari K-S1 "IKISI BIRDEN"):
    Acik veri dunyasinin kulup/lig iskeletine Wikidata'nin guncel A takim kadrolari oturur (ad, yas, uyruk, mevki;
    yetenekler URETILIR, eksik kadro uretilmis oyuncuyla tamamlanir). Sahibin KENDI FM26 disa aktarimi ONCELIKLIDIR:
    bir kulubun FM oyunculari (gercek ad + ozellik) kadroya once girer, Wikidata eksikleri ekler, Wikidata oyuncusuyla
    eslesen FM satiri ona gercek ozellikleri verir (open_loader.build_real_world). Kisisel veridir; CIFT onay gerekir:
    --real-players (acik secim; ortam degiskeni TEK BASINA acmaz) ve OFM_ALLOW_REAL_PLAYERS=1 (OFM_ALLOW_REAL_NAMES'ten
    AYRI bayrak). Dunya maskeleme 'off' olarak kaydedilir (game_state.mask_level): worlds.world_has_real_names onu
    paylasilan dunyaya cevirmez ve paylasilan dunya kurulumunda reddeder; CLI paylasilan dunya semasina yazmayi
    reddeder (worlds.refuse_real_players_target). Web'den acilan dunya bu yolu HIC kullanmaz (accounts._build_world
    real_players gecirmez, NEW_WORLD_SOURCES degismedi). Kadro verisi depoya girmez (data/local/, gitignore).

Akis:
    kaynak -> WorldSpec (saf veri, DB bilmez) -> write_world(db) -> dogrulama raporu
Bu ayrim sayesinde FM donusumu veritabani olmadan test edilebilir.

Altyapi ve potansiyel (10. Asama) -- add_youth_world:
    * Her oyuncuya potansiyel: FM oyuncusu potential_ability'den (development.potential_from_fm),
      kurgusal oyuncu yasina gore (development.initial_potential). Piyasa degeri potansiyel primiyle.
    * Her kulube altyapi tesisi (youth.default_facilities) ve 4-6 kisilik baslangic akademisi
      (youth.generate_academy, kulubun ulkesine uygun adlar).
    * AYRI RNG akisi (tohum + YOUTH_SEED_OFFSET) ve kidemli dunya uretildikten SONRA calisir: ayni tohumla
      kidemli oyuncularin adlari, yetenekleri ve siralari birebir aynidir. Akademi oyunculari tum
      kidemli oyuncular yazildiktan sonra yazilir (kidemli oyuncu id'leri kaymaz).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select, text

import database
import development
import fm_parser
import staff as staff_rules
import youth
from club_directory import (
    MASKED_LEAGUES,
    OTHER_COUNTRY,
    canonical_league,
    lookup_club,
    reputation_from_strength,
)
from database import SessionLocal, engine, session_scope, wait_for_db
from finance import (
    DEFAULT_WAGE_HEADROOM,
    academy_wage,
    expected_wage,
    market_value,
    transfer_budget_for_reputation,
)
from fitness import CONDITION_MAX
from models import (
    Fixture,
    FixtureStatus,
    GameState,
    League,
    LineupStatus,
    Player,
    Position,
    SquadRole,
    Staff,
    StaffRole,
    Team,
)
from name_masking import (
    DEFAULT_MASK_LEVEL,
    MASK_LEVEL_ENV,
    MASK_LEVELS,
    MASK_OFF,
    REAL_NAMES_ENV,
    build_club_mask_map,
    build_league_mask_map,
    build_player_mask_map,
    find_leaks,
    mask_level_from_env,
    normalize_mask_level,
    real_names_allowed,
)
from name_pools import COUNTRY_POOL, NAME_POOLS  # seed.NAME_POOLS eskisi gibi erisilebilir
from ratings import ENGINE_ATTRIBUTES, POSITION_OFFSETS, POSITION_WEIGHTS, rate_fm_player
from reputation import START_REPUTATION
from schedule import build_round_robin

# Windows konsolunda Turkce karakterler patlamasin diye
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
FM_DATA_DIR = ROOT / "data" / "fm"
DEFAULT_SEASON_YEAR = 2026

# 'off' seviyesi CIFT onay ister; onaylardan biri eksikse dunya kurulmaz (kaza korumasi).
REAL_NAMES_OPTIN_ERROR = (
    f"İsim maskeleme kapatılamadı: 'off' seviyesi ÇİFT onay ister — (1) seviyeyi açıkça seç "
    f"(--mask-level off; {MASK_LEVEL_ENV} ortam değişkeni TEK BAŞINA yetmez) ve (2) gerçek "
    f"isimlere izin ver: {REAL_NAMES_ENV}=1. Bu koruma, lisanslı FM verisinin kazayla "
    f"maskesiz yazılmasını engeller; dünya kurulmadı, veritabanına dokunulmadı."
)

# Maskeleme kapaliyken (--mask-level off) seed raporunda ve dogrulama raporunda basilir.
MASK_OFF_WARNING = (
    "!! MASKELEME KAPALI: bu dünya, SENİN kendi lisanslı Football Manager verinden gelen "
    "GERÇEK kulüp, lig ve oyuncu adlarını içerir.\n"
    "   Yalnızca kendi bilgisayarındaki kişisel oyunun içindir. Bu veritabanının dökümünü/yedeğini, "
    "FM dışa aktarımlarını ya da gerçek adlı bir yapıyı ASLA depoya ekleme (git commit), yayımlama "
    "veya paylaşma; FM veritabanı Sports Interactive'in lisanslı içeriğidir.\n"
    "   Paylaşılacak bir dünya kuracaksan maskelemeyi aç: --mask-level light (varsayılan) ya da strong."
)

# 16G (K-S1): gercek oyuncu kadrolari. CIFT onay: --real-players (acik secim) + OFM_ALLOW_REAL_PLAYERS=1.
# OFM_ALLOW_REAL_NAMES'ten AYRI bir bayraktir: FM ad izni gercek kisi kadrolarina izin sayilmaz.
REAL_PLAYERS_ENV = "OFM_ALLOW_REAL_PLAYERS"
_TRUTHY = frozenset({"1", "true", "yes", "on", "evet"})
REAL_PLAYERS_OPTIN_ERROR = (
    f"Gerçek oyuncu kadroları kurulamadı: bu dünya gerçek kişilerin adını, doğum tarihini ve uyruğunu içerir "
    f"(kişisel veri) ve AÇIK onay ister — --real-players ile birlikte {REAL_PLAYERS_ENV}=1 ver. Yalnızca kendi "
    f"makinendeki tek koltuklu kariyer içindir; dünya kurulmadı, veritabanına dokunulmadı."
)
REAL_PLAYERS_SOURCE_ERROR = (
    "Gerçek oyuncu kadroları yalnızca açık veri dünyasına kurulur (--source open ya da auto); "
    "maskeleme seviyesi verilecekse 'off' olmalıdır. Dünya kurulmadı, veritabanına dokunulmadı."
)
REAL_PLAYERS_WARNING = (
    "!! GERÇEK OYUNCULAR: bu dünya gerçek futbolcuların adını, doğum tarihinden yaşını ve uyruğunu içerir "
    "(Wikidata, CC0; kişisel veri). FM yaması varsa eşleşen oyuncuların özellikleri SENİN FM dışa aktarımından gelir.\n"
    "   Yalnızca kendi bilgisayarındaki TEK KOLTUKLU kişisel kariyer içindir: paylaşılan dünyaya çevrilemez, "
    "başka menajerlere açılamaz. Veritabanı dökümünü, data/local/ ve data/fm/ içeriğini ASLA depoya ekleme, "
    "yayımlama ya da paylaşma."
)


def real_players_allowed() -> bool:
    """OFM_ALLOW_REAL_PLAYERS ile gercek oyuncu kadrolarina izin verilmis mi? ('1', 'true', 'yes', 'on', 'evet')"""
    return (os.getenv(REAL_PLAYERS_ENV) or "").strip().casefold() in _TRUTHY


class SeedError(Exception):
    """Dunya kurulamadi. Mesaji dogrudan kullaniciya gosterilebilir."""


# ===========================================================================
# 1) KADRO YAPISI
# ===========================================================================

# Sentetik dunya: tam 15 oyuncu. 2 kaleci: sakatlik/kart mekanigi icin yedek GK sart.
SQUAD_COMPOSITION: dict[Position, int] = {
    Position.GK: 2,
    Position.DEF: 4,
    Position.MID: 5,
    Position.FWD: 4,
}
SQUAD_SIZE = sum(SQUAD_COMPOSITION.values())

# FM dunyasi: kulup buyuklugu degisken; oynanabilirlik icin asgari/azami sinirlar
FM_MIN_SQUAD = 16
FM_MAX_SQUAD = 26
FM_MIN_PER_POSITION: dict[Position, int] = {
    Position.GK: 2, Position.DEF: 4, Position.MID: 4, Position.FWD: 3,
}
FM_MIN_LEAGUE_CLUBS = 2
ACADEMY_GAP = 12             # altyapi oyuncusu kulup ortalamasinin bu kadar altinda

# 13F: koken (ClubSpec/LeagueSpec.data_source). "open" isaretli lig ve kulup adlari CC0 acik
# veriden gelir (openfootball): mask_world onlara DOKUNMAZ ve sizinti denetimine (find_leaks)
# hic verilmezler. Diger her ad icin kilit bugunku kadar katidir (bkz. _world_names).
OPEN_SOURCE = "open"

# 14C: web'den acilan YENI kariyer / paylasilan dunya kaynagi (bkz. new_world_source). CLI'nin ve
# seed()'in varsayilani 'auto' DEGISMEZ. OFM_NEW_WORLD_SOURCE=synthetic bugunku kucuk kurgusal
# dunyayi birebir geri getirir (veto yolu); testler de bunu kullanir (tests/conftest.py).
# 16A-0 (K-S11): 'fm' yalnizca sahibin ACIK secimiyle (OFM_NEW_WORLD_SOURCE=fm) doner.
NEW_WORLD_SOURCE_ENV = "OFM_NEW_WORLD_SOURCE"
NEW_WORLD_SOURCES = (OPEN_SOURCE, "synthetic", "auto", "fm")

# Altyapi (10. Asama): ayri RNG akisi ve baslangic akademisi buyuklugu
YOUTH_SEED_OFFSET = 11
INITIAL_ACADEMY_SIZE = youth.INITIAL_ACADEMY_SIZE

# Geriye donuk uyumluluk (baska moduller seed uzerinden erisiyordu)
__all__ = ["ATTRIBUTES", "POSITION_WEIGHTS", "POSITION_OFFSETS", "SQUAD_COMPOSITION", "SQUAD_SIZE"]
ATTRIBUTES = ENGINE_ATTRIBUTES


# ===========================================================================
# 2) SENTETIK DUNYA VERISI
# ===========================================================================

# (takim adi, itibar 1-100, butce EUR, guc bandi (min, max))
# Takim ve lig adlari club_directory'deki MASKELI adlardir (gercek adlar DB'ye yazilmaz).
LEAGUE_DATA: list[dict] = [
    {
        "name": "Türkiye Elit Ligi",
        "country": "Türkiye",
        "teams": [
            ("Istanbul Lions",    78, 45_000_000, (73, 83)),
            ("Kadıköy Canaries",  77, 42_000_000, (73, 83)),
            ("Bosphorus Eagles",  75, 32_000_000, (71, 81)),
            ("Karadeniz Storm",   73, 25_000_000, (70, 80)),
        ],
    },
    {
        "name": "İngiltere Elit Ligi",
        "country": "İngiltere",
        "teams": [
            ("Manchester Blue",   92, 180_000_000, (80, 90)),
            ("Merseyside Reds",   90, 150_000_000, (78, 89)),
            ("London Gunners",    89, 140_000_000, (78, 89)),
            ("Manchester Devils", 87, 130_000_000, (76, 87)),
        ],
    },
    {
        "name": "İtalya Elit Ligi",
        "country": "İtalya",
        "teams": [
            ("Milano Nerazzurri", 86, 95_000_000, (77, 87)),
            ("Torino Bianconeri", 86, 92_000_000, (76, 87)),
            ("Milano Rossoneri",  84, 85_000_000, (76, 86)),
            ("Vesuvio Azzurri",   83, 80_000_000, (75, 86)),
        ],
    },
    {
        "name": "İspanya Elit Ligi",
        "country": "İspanya",
        "teams": [
            ("Madrid Blancos",      95, 200_000_000, (81, 91)),
            ("Catalonia Blaugrana", 92, 170_000_000, (79, 90)),
            ("Madrid Rojiblancos",  87, 110_000_000, (77, 87)),
            ("Bizkaia Lions",       79, 60_000_000, (74, 84)),
        ],
    },
    {
        "name": "Almanya Elit Ligi",
        "country": "Almanya",
        "teams": [
            ("München Roten",    94, 190_000_000, (80, 91)),
            ("Ruhr Schwarzgelb", 86, 100_000_000, (76, 86)),
            ("Rhein Werkself",   86, 95_000_000, (76, 86)),
            ("Sachsen Bullen",   83, 80_000_000, (75, 85)),
        ],
    },
    {
        "name": "Fransa Elit Ligi",
        "country": "Fransa",
        "teams": [
            ("Paris Rouge-Bleu",   92, 175_000_000, (79, 89)),
            ("Provence Phocéens",  81, 55_000_000, (74, 84)),
            ("Rocher Monégasques", 81, 55_000_000, (73, 84)),
            ("Rhône Gones",        80, 50_000_000, (73, 83)),
        ],
    },
]

# "Devasa cift kalemli butce": itibari ELITE_REPUTATION ve ustu kulupler maas havuzunda da
# cok daha genis pay birakir (transfer butceleri zaten en buyuk dilimde).
ELITE_REPUTATION = 90
ELITE_WAGE_HEADROOM = 1.45


def wage_headroom_for(reputation: int) -> float:
    """Maas butcesi / baslangic maas yuku orani. Elit kulup -> ELITE_WAGE_HEADROOM."""
    return ELITE_WAGE_HEADROOM if reputation >= ELITE_REPUTATION else DEFAULT_WAGE_HEADROOM

# Kurgusal isim havuzlari (NAME_POOLS / COUNTRY_POOL) 10. Asama'da name_pools.py'ye tasindi;
# buradan yeniden disa aktarilir (seed.NAME_POOLS eskisi gibi calisir).

STAFF_FIRST_NAMES = (
    "Andre", "Bernd", "Carlo", "Diego", "Emilio", "Fabien", "Gustav", "Henrik",
    "Igor", "Janos", "Klaus", "Lucien", "Marcel", "Nikola", "Osvaldo", "Patrik",
    "Rafael", "Stefan", "Tomas", "Viktor", "Ahmet", "Cem", "Levent", "Orhan", "Sinan",
)
STAFF_LAST_NAMES = (
    "Adler", "Bauer", "Conti", "Dubois", "Engel", "Ferrer", "Grimaldi", "Haas",
    "Ivanov", "Janssen", "Kovac", "Laurent", "Moreau", "Novak", "Olsen", "Peeters",
    "Quintana", "Richter", "Sokolov", "Toth", "Ergin", "Kaplan", "Sezer", "Uzun", "Varol",
)

TEAM_STAFF_PLAN: dict[StaffRole, int] = {
    StaffRole.ASSISTANT: 1,
    StaffRole.COACH: 2,
    StaffRole.SCOUT: 1,
    StaffRole.PHYSIO: 1,
}
FREE_AGENT_STAFF_PLAN: dict[StaffRole, int] = {
    StaffRole.ASSISTANT: 3,
    StaffRole.COACH: 5,
    StaffRole.SCOUT: 4,
    StaffRole.PHYSIO: 4,
}

FORMATION_CHOICES = (("4-4-2", 50), ("4-3-3", 30), ("3-5-2", 20))


# ===========================================================================
# 3) DUNYA TANIMI (saf veri)
# ===========================================================================

@dataclass
class PlayerSpec:
    name: str
    age: int
    position: Position
    overall: int
    attributes: dict[str, int]
    form: int
    morale: int
    contract_years: int
    market_value: int | None = None        # None -> finance egrisi
    current_wage: int | None = None        # None -> kulup itibari + kadro rolune gore egri
    nationality: str | None = None
    data_source: str = "synthetic"
    fm_uid: int | None = None
    current_ability: int | None = None
    potential_ability: int | None = None
    fm_attributes: dict[str, float] = field(default_factory=dict)
    potential: int | None = None           # 10. Asama: tavan guc (1-99), add_youth_world doldurur
    shirt_number: int | None = None        # 16G: Wikidata P1618 (yalnizca bellekte; DB'de sutun yok)
    wikidata_id: str | None = None         # 16G: gercek oyuncunun Wikidata kimligi (yalnizca bellekte)


@dataclass
class ClubSpec:
    name: str
    reputation: int
    transfer_budget: int
    formation: str
    players: list[PlayerSpec]
    academy_added: int = 0                 # FM kadro tamamlama (A takim) oyuncu sayisi
    youth_facilities: int | None = None    # 10. Asama: altyapi tesisi 1-20
    academy: list[PlayerSpec] = field(default_factory=list)   # U-21 akademi (A takim disi)
    data_source: str = "synthetic"         # 13F: "open" ise ad ACIK VERIDEN gelir (bkz. OPEN_SOURCE)


@dataclass
class LeagueSpec:
    name: str
    country: str
    clubs: list[ClubSpec]
    data_source: str = "synthetic"         # 13F: "open" ise ad ACIK VERIDEN gelir


@dataclass
class MaskSummary:
    """mask_world sonucu: seed ciktisinda gosterilir."""
    level: str
    leagues: int = 0                        # dunyadaki lig sayisi (hepsi maskeli adla)
    clubs: int = 0
    fm_players: int = 0                     # maskeli adla yazilacak FM oyuncusu
    renamed_leagues: int = 0                # bu adimda adi degisen
    renamed_clubs: int = 0
    renamed_players: int = 0
    at_ingest: bool = False                 # FM adlari dosya okunurken maskelenmisti
    open_leagues: int = 0                   # 13F: acik veriden gelen (maskelenmeyen) lig sayisi
    open_clubs: int = 0                     # 13F: acik veriden gelen (maskelenmeyen) kulup sayisi
    real_players: int = 0                   # 16G: gercek (Wikidata) oyuncu sayisi -- yalnizca 'off' dunyada

    @property
    def off(self) -> bool:
        return self.level == MASK_OFF

    def _open_text(self) -> str:
        if not (self.open_leagues or self.open_clubs):
            return ""
        return (f" Ayrıca {self.open_leagues} lig ve {self.open_clubs} kulüp açık veriden (CC0) "
                f"gerçek adıyla geliyor; bunlar maskelenmez.")

    def text(self) -> str:
        if self.off and self.real_players:
            return (f"İsim maskeleme KAPALI (off): {self.real_players} GERÇEK oyuncu (Wikidata; {self.fm_players} "
                    f"tanesi FM yamalı), {self.open_leagues} lig ve {self.open_clubs} kulüp gerçek adıyla yazılıyor."
                    f"\n{REAL_PLAYERS_WARNING}")
        if self.off:
            return (f"İsim maskeleme KAPALI (off): {self.leagues} lig, {self.clubs} kulüp ve "
                    f"{self.fm_players} FM oyuncusu GERÇEK adıyla yazılıyor.\n{MASK_OFF_WARNING}")
        if (self.open_leagues or self.open_clubs) and not (self.leagues or self.clubs or self.fm_players):
            return (f"İsim maskeleme ({self.level}): maskelenecek ad yok — {self.open_leagues} lig ve "
                    f"{self.open_clubs} kulüp açık veriden (CC0) gerçek adıyla geliyor, oyuncular üretilmiş.")
        where = " (FM adları dosya okunurken maskelendi)" if self.at_ingest else ""
        return (f"İsim maskeleme ({self.level}){where}: {self.leagues} lig, {self.clubs} kulüp, "
                f"{self.fm_players} FM oyuncusu kurgusal adla; bu adımda {self.renamed_leagues} lig, "
                f"{self.renamed_clubs} kulüp, {self.renamed_players} oyuncu adı dönüştürüldü."
                + self._open_text())


@dataclass
class WorldSpec:
    source: str                             # "synthetic" / "fm" / "open" (13F: acik veri)
    leagues: list[LeagueSpec]
    notes: list[str] = field(default_factory=list)
    parse_report: fm_parser.ParseReport | None = None
    names_masked: bool = False              # mask_world uygulandi mi ('off' seviyesinde kimlik eslemesi)
    mask_summary: MaskSummary | None = None  # seviye burada tutulur (mask_summary.off -> maskeleme kapali)
    youth_ready: bool = False               # add_youth_world uygulandi mi
    real_players: object | None = None      # 16G: open_loader.RealPlayersReport (gercek oyuncu dunyasi)

    @property
    def clubs(self) -> list[ClubSpec]:
        return [c for league in self.leagues for c in league.clubs]

    @property
    def player_count(self) -> int:
        """A takim oyunculari (akademi haric)."""
        return sum(len(c.players) for c in self.clubs)

    @property
    def academy_count(self) -> int:
        return sum(len(c.academy) for c in self.clubs)


# ===========================================================================
# 4) URETICI FONKSIYONLAR
# ===========================================================================

def _clamp(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


class NameFactory:
    """Ulke bazli, tekrar etmeyen oyuncu ismi uretir."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._used: set[str] = set()

    def reserve(self, names) -> None:
        """Gercek oyuncu adlarini kullanilmis say (altyapi isimleri cakismasin)."""
        self._used.update(names)

    def make(self, country: str) -> str:
        first_names, last_names = NAME_POOLS[COUNTRY_POOL.get(country, country)] \
            if COUNTRY_POOL.get(country, country) in NAME_POOLS else NAME_POOLS["Ingiltere"]
        for _ in range(400):
            name = f"{self._rng.choice(first_names)} {self._rng.choice(last_names)}"
            if name not in self._used:
                self._used.add(name)
                return name
        name = f"{self._rng.choice(first_names)} {self._rng.choice(last_names)} Jr."
        self._used.add(name)
        return name


def build_rating_targets(rng: random.Random, low: int, high: int, count: int) -> list[int]:
    """Kadronun guc merdiveni: en iyi oyuncudan en zayifina dogru bandi tarar."""
    if count == 1:
        return [high]
    span = high - low
    return [
        _clamp(high - span * (i / (count - 1)) + rng.uniform(-1.2, 1.2), low, high)
        for i in range(count)
    ]


def generate_player_spec(
    rng: random.Random,
    name: str,
    position: Position,
    target_rating: int,
    band: tuple[int, int],
    data_source: str = "synthetic",
) -> PlayerSpec:
    """Hedeflenen guce ve mevkiye uygun, tutarli yetenekleri olan kurgusal oyuncu."""
    offsets = POSITION_OFFSETS[position]
    weights = POSITION_WEIGHTS[position]

    raw: dict[str, int] = {}
    for attr in ENGINE_ATTRIBUTES:
        value = target_rating + offsets[attr] + rng.randint(-3, 3)
        if attr == "goalkeeping" and position is not Position.GK:
            raw[attr] = _clamp(value, 5, 35)
        else:
            raw[attr] = _clamp(value, 20, 99)

    overall = _clamp(sum(weights[a] * raw[a] for a in ENGINE_ATTRIBUTES), band[0], band[1])
    age = _clamp(rng.triangular(17, 37, 26), 17, 37)
    if data_source == "academy":
        age = _clamp(rng.triangular(17, 22, 18), 17, 22)
    contract_years = rng.randint(1, 5)
    return PlayerSpec(
        name=name,
        age=age,
        position=position,
        overall=overall,
        attributes=raw,
        form=rng.randint(45, 65),
        morale=rng.randint(60, 85),
        contract_years=contract_years,
        market_value=market_value(overall, age, position),
        data_source=data_source,
    )


def build_synthetic_world(rng_seed: int) -> WorldSpec:
    """Kurgusal dunya: 6 lig, 24 takim, takim basina tam 15 oyuncu (maskeli kulup/lig adlari)."""
    rng = random.Random(rng_seed)
    names = NameFactory(rng)
    formation_rng = random.Random(rng_seed + 1)
    leagues: list[LeagueSpec] = []

    for league_row in LEAGUE_DATA:
        clubs: list[ClubSpec] = []
        for team_name, reputation, budget, band in league_row["teams"]:
            positions = [pos for pos, n in SQUAD_COMPOSITION.items() for _ in range(n)]
            rng.shuffle(positions)
            targets = build_rating_targets(rng, band[0], band[1], SQUAD_SIZE)
            players = [
                generate_player_spec(rng, names.make(league_row["country"]), pos, target, band)
                for pos, target in zip(positions, targets, strict=True)
            ]
            clubs.append(ClubSpec(
                name=team_name,
                reputation=reputation,
                transfer_budget=budget,
                formation=_pick_formation(formation_rng),
                players=players,
            ))
        leagues.append(LeagueSpec(league_row["name"], league_row["country"], clubs))
    world = WorldSpec("synthetic", leagues)
    add_youth_world(world, rng_seed)
    return world


def academy_player_spec(spec: youth.YouthSpec) -> PlayerSpec:
    """youth.YouthSpec -> akademiye yazilacak PlayerSpec (potansiyel primli piyasa degeriyle)."""
    return PlayerSpec(
        name=spec.name,
        age=spec.age,
        position=spec.position,
        overall=spec.overall,
        attributes=dict(spec.attributes),
        form=spec.form,
        morale=spec.morale,
        contract_years=spec.contract_years,
        market_value=market_value(spec.overall, spec.age, spec.position, spec.potential),
        data_source="academy",
        potential=spec.potential,
    )


def add_youth_world(world: WorldSpec, rng_seed: int) -> WorldSpec:
    """
    Dunyaya potansiyel, altyapi tesisi ve baslangic akademisi ekler (YERINDE, idempotent).
    Ayri RNG akisi kullanir; kidemli oyuncularin ad/yetenek/sira uretimine dokunmaz.
        * FM oyuncusu: potansiyel potential_ability'den; dosyadaki piyasa degeri korunur
        * kurgusal oyuncu (sentetik / kadro tamamlama): yasa gore potansiyel, primli piyasa degeri
    """
    if world.youth_ready:
        return world
    rng = random.Random(rng_seed + YOUTH_SEED_OFFSET)
    used_names = {p.name for c in world.clubs for p in c.players}
    for league in world.leagues:
        for club in league.clubs:
            for spec in club.players:
                if spec.potential is None:
                    if spec.data_source == "fm" and spec.potential_ability:
                        spec.potential = development.potential_from_fm(spec.potential_ability, spec.overall)
                    else:
                        spec.potential = development.initial_potential(rng, spec.age, spec.overall)
                if spec.data_source != "fm" or spec.market_value is None:
                    spec.market_value = market_value(spec.overall, spec.age, spec.position, spec.potential)
            if club.youth_facilities is None:
                club.youth_facilities = youth.default_facilities(club.reputation, rng)
            if not club.academy:
                count = rng.randint(*INITIAL_ACADEMY_SIZE)
                club.academy = [
                    academy_player_spec(y)
                    for y in youth.generate_academy(rng, league.country, club.youth_facilities,
                                                    club.reputation, count, used_names)
                ]
    world.youth_ready = True
    return world


def _pick_formation(rng: random.Random) -> str:
    options, weights = zip(*FORMATION_CHOICES, strict=True)
    return rng.choices(options, weights=weights, k=1)[0]


# ===========================================================================
# 5) FM DUNYASI
# ===========================================================================

def player_from_record(record: fm_parser.FMPlayerRecord, rng: random.Random, season_year: int) -> PlayerSpec:
    """FM kaydi -> oyuncu tanimi. Dosyada olmayan alanlar tahmin/egriyle doldurulur."""
    overall, attrs, _estimated = rate_fm_player(
        record.position, record.fm_attributes, record.current_ability
    )
    if record.contract_expiry_year is not None:
        contract_years = _clamp(record.contract_expiry_year - season_year, 0, 6)
    else:
        contract_years = rng.randint(1, 4)
    wage = record.wage_eur_weekly
    return PlayerSpec(
        name=record.name,
        age=record.age if record.age is not None else 25,
        position=record.position,
        overall=overall,
        attributes=attrs,
        form=rng.randint(48, 62),
        morale=rng.randint(62, 80),
        contract_years=contract_years,
        market_value=record.value_eur,
        current_wage=int(round(wage / 100) * 100) if wage else None,
        nationality=record.nationality,
        data_source="fm",
        fm_uid=record.uid,
        current_ability=record.current_ability,
        potential_ability=record.potential_ability,
        fm_attributes=dict(record.fm_attributes),
    )


MAX_KEEPERS = 3


def trim_squad(players: list[PlayerSpec], limit: int = FM_MAX_SQUAD) -> list[PlayerSpec]:
    """
    Kalabalik kadroyu `limit` oyuncuya indirir. Once her mevkinin asgari sayisi o mevkinin
    en iyilerinden korunur (kaleci 3'e kadar), kalan yerler genel guce gore dolar.
    Boylece zayif ama tek defans hattina sahip bir kulubun gercek defanslari atilip
    yerine uydurma altyapi oyunculari eklenmez.
    """
    if len(players) <= limit:
        return players
    ranked = sorted(players, key=lambda p: -p.overall)
    keep: list[PlayerSpec] = []
    for position, minimum in FM_MIN_PER_POSITION.items():
        quota = MAX_KEEPERS if position is Position.GK else minimum
        keep += [p for p in ranked if p.position is position][:quota]
    chosen = {id(p) for p in keep}
    for p in ranked:
        if len(keep) >= limit:
            break
        if id(p) in chosen:
            continue
        if p.position is Position.GK and sum(k.position is Position.GK for k in keep) >= MAX_KEEPERS:
            continue
        keep.append(p)
        chosen.add(id(p))
    return keep


def masking_off(world: WorldSpec) -> bool:
    """
    Bu dunya maskeleme KAPALI (off) kurulmus mu? Yalnizca mask_world calistiysa bilinir;
    seviyesi bilinmeyen (elle kurulmus) dunya maskeli sayilir, yani sizinti kilidi calisir.
    """
    return world.mask_summary is not None and world.mask_summary.off


def validate_world(world: WorldSpec) -> list[str]:
    """
    Veritabanina yazmadan ONCE yakalanmasi gereken sorunlar (yazma yarida patlamasin).
    Maskeleme kapaliysa (off) gercek adlar bilerek durdugu icin isim sizintisi denetimi
    ATLANIR; diger butun denetimler (tekrar, kaleci, yas) aynen uygulanir.
    Acik veri kokenli (data_source="open") lig/kulup adlari denetime hic girmez (_world_names).
    """
    problems: list[str] = []
    if not masking_off(world):
        for leak in find_leaks(_world_names(world)):
            problems.append(f"Maskelenmemiş gerçek isim: {leak}")
        unmasked = _unmasked_fm_players(world)
        if unmasked:
            problems.append(f"Maskelenmemiş FM oyuncu adı: {unmasked} oyuncu (mask_world uygulanmadı)")
    league_names = [lg.name for lg in world.leagues]
    for name in {n for n in league_names if league_names.count(n) > 1}:
        problems.append(f"Aynı adla birden fazla lig: {name}")
    club_names = [c.name for c in world.clubs]
    for name in {n for n in club_names if club_names.count(n) > 1}:
        problems.append(f"Aynı adla birden fazla kulüp: {name}")
    for club in world.clubs:
        keepers = sum(p.position is Position.GK for p in club.players)
        if keepers < 2:
            problems.append(f"{club.name}: {keepers} kaleci (en az 2 gerekli)")
        for p in (*club.players, *club.academy):
            if not 15 <= p.age <= 45:
                problems.append(f"{club.name}: {p.name} yaşı {p.age} (15-45 olmalı)")
    return problems


def pad_squad(
    players: list[PlayerSpec],
    rng: random.Random,
    names: NameFactory,
    country: str,
) -> tuple[list[PlayerSpec], int]:
    """Mevki asgarilerini ve FM_MIN_SQUAD'i altyapi oyunculariyla tamamlar."""
    padded = list(players)
    average = sum(p.overall for p in padded) / len(padded) if padded else 65
    level = _clamp(average - ACADEMY_GAP, 45, 80)
    band = (max(40, level - 4), level + 3)

    def add(position: Position) -> None:
        padded.append(generate_player_spec(
            rng, names.make(country), position, rng.randint(*band), band, data_source="academy"
        ))

    for position, minimum in FM_MIN_PER_POSITION.items():
        while sum(1 for p in padded if p.position is position) < minimum:
            add(position)
    cycle = [Position.DEF, Position.MID, Position.FWD, Position.MID]
    i = 0
    while len(padded) < FM_MIN_SQUAD:
        add(cycle[i % len(cycle)])
        i += 1
    return padded, len(padded) - len(players)


def _masked_league(league: tuple[str, str] | None) -> tuple[str, str] | None:
    """canonical_league sonucu -> (maskeli lig adi, ulke). Bilinmeyen lig metni oldugu gibi kalir."""
    if league is None or league[1] == OTHER_COUNTRY:
        return league
    return MASKED_LEAGUES.get(league[0], league[0]), league[1]


def default_mask_level() -> str:
    """
    Seviye verilmediginde kullanilan varsayilan: SEED_NAME_MASKING ortam degiskeni.
    ASLA 'off' donmez -- ortam degiskeni tek basina maskelemeyi kapatamaz; kapatmak icin seviyenin
    acikca secilmesi (CLI: --mask-level off) ve OFM_ALLOW_REAL_NAMES=1 gerekir.
    """
    level = mask_level_from_env()
    return DEFAULT_MASK_LEVEL if level == MASK_OFF else level


def _mask_level_for(report: fm_parser.ParseReport | None, level: str | None = None) -> str:
    """
    Gecerli seviye: ACIKCA verilen > parser raporundaki (maskeliyse) > default_mask_level().
    'off' yalnizca acikca secildiginde VE OFM_ALLOW_REAL_NAMES izniyle gecerlidir; izin yoksa
    SeedError (dunya kurulmaz). Boylece maskeleme kazayla kapanamaz.
    """
    at_ingest = report is not None and report.masked
    resolved = normalize_mask_level(level or (report.mask_level if at_ingest else None)
                                    or default_mask_level())
    if resolved == MASK_OFF and not real_names_allowed():
        raise SeedError(REAL_NAMES_OPTIN_ERROR)
    return resolved


def build_fm_world(
    report: fm_parser.ParseReport,
    rng_seed: int,
    season_year: int = DEFAULT_SEASON_YEAR,
    mask_level: str | None = None,
) -> WorldSpec:
    """
    Parser raporundan oynanabilir dunya kurar. Veritabanina dokunmaz.
    Rehber kulupleri maskeli adlariyla gruplanir (itibar rehberden); sonda mask_world uygulanir,
    yani ham (mask_names=False) rapordan bile maskeli dunya cikar.
    Maskeleme kapaliysa (off) gruplama rehberdeki GERCEK ad ve GERCEK lig adiyla yapilir:
    ayni kulubun farkli yazimlari ("FC Bayern München" / "Bayern Munich") yine tek kulup olur.
    """
    rng = random.Random(rng_seed)
    names = NameFactory(random.Random(rng_seed + 5))
    names.reserve(p.name for p in report.players)
    formation_rng = random.Random(rng_seed + 1)
    notes: list[str] = []

    off = _mask_level_for(report, mask_level) == MASK_OFF
    grouped: dict[str, dict] = {}
    no_club = 0
    unplaced: Counter[str] = Counter()
    for record in report.players:
        if not record.club:
            no_club += 1
            continue
        info = lookup_club(record.club)
        if info is not None:
            key = info.name if off else info.masked
            league = ((info.league if off else info.masked_league), info.country)
        else:
            league = canonical_league(record.league)
            if not off:
                league = _masked_league(league)
            if league is None:
                unplaced[record.club] += 1
                continue
            key = record.club.strip()
        entry = grouped.setdefault(key, {"info": info, "league": league, "records": []})
        entry["records"].append(record)

    if no_club:
        notes.append(f"Kulübü olmayan {no_club} oyuncu (serbest) atlandı.")
    if unplaced:
        listed = ", ".join(f"{club} ({n})" for club, n in unplaced.most_common(8))
        notes.append(
            f"Rehberde olmayan ve lig bilgisi taşımayan {len(unplaced)} kulüp atlandı: {listed}. "
            f"Dışa aktarıma 'Division' sütunu ekle ya da club_directory.py'ye tanımla."
        )

    by_league: dict[tuple[str, str], list[ClubSpec]] = {}
    for club_name in sorted(grouped):
        entry = grouped[club_name]
        league_name, country = entry["league"]
        records = entry["records"]
        players = trim_squad([player_from_record(r, rng, season_year) for r in records])
        if len(players) < len(records):
            notes.append(f"{club_name}: dosyada {len(records)} oyuncu vardı, mevki dengesi korunarak "
                         f"en iyi {len(players)} oyuncu alındı.")
        players, academy = pad_squad(players, rng, names, country)
        top = sorted((p.overall for p in players), reverse=True)[:11]
        reputation = entry["info"].reputation if entry["info"] else reputation_from_strength(sum(top) / len(top))
        if academy:
            notes.append(f"{club_name}: dosyada {len(records)} oyuncu vardı, "
                         f"{academy} altyapı oyuncusuyla {len(players)}'e tamamlandı.")
        by_league.setdefault((league_name, country), []).append(ClubSpec(
            name=club_name,
            reputation=reputation,
            transfer_budget=transfer_budget_for_reputation(reputation),
            formation=_pick_formation(formation_rng),
            players=players,
            academy_added=academy,
        ))

    leagues: list[LeagueSpec] = []
    for (league_name, country), clubs in sorted(by_league.items()):
        if len(clubs) < FM_MIN_LEAGUE_CLUBS:
            notes.append(f"{league_name}: yalnızca {len(clubs)} kulüp var, lig kurulamadı "
                         f"(en az {FM_MIN_LEAGUE_CLUBS}): {', '.join(c.name for c in clubs)}.")
            continue
        leagues.append(LeagueSpec(league_name, country, clubs))

    world = WorldSpec("fm", leagues, notes, parse_report=report)
    mask_world(world, mask_level)
    add_youth_world(world, rng_seed)          # maskeli kidemli adlardan SONRA: akademi adlari cakismaz
    return world


def _maskable_leagues(world: WorldSpec) -> list[LeagueSpec]:
    return [lg for lg in world.leagues if lg.data_source != OPEN_SOURCE]


def _maskable_clubs(world: WorldSpec) -> list[ClubSpec]:
    return [c for c in world.clubs if c.data_source != OPEN_SOURCE]


def _world_names(world: WorldSpec) -> list[str]:
    """
    Sizinti denetimine (find_leaks) verilecek adlar. Acik veri kokenli (data_source="open")
    lig ve kulup adlari HARIC tutulur: onlar CC0 acik veriden gelen gercek adlardir, bilerek
    maskelenmezler. find_leaks semantigi degismez; yalnizca hangi adlarin ona verildigi degisir.
    """
    return ([lg.name for lg in _maskable_leagues(world)]
            + [c.name for c in _maskable_clubs(world)])


def _unmasked_fm_players(world: WorldSpec) -> int:
    """
    Maskelenmemis olabilecek FM oyuncusu sayisi. FM adlari ya parser raporunda (at_ingest) ya da
    mask_world'de maskelenir; ikisi de olmadiysa ozgun ad veritabanina gidebilir -> 0 olmali.
    """
    if world.names_masked:
        return 0
    report = world.parse_report
    if report is not None and report.masked:
        return 0
    return sum(p.data_source == "fm" for c in world.clubs for p in (*c.players, *c.academy))


def mask_world(world: WorldSpec, level: str | None = None) -> MaskSummary:
    """
    Dunyadaki kulup, lig ve FM oyuncu adlarini YERINDE maskeler ve ozet dondurur.
    Idempotent: rehber adlari zaten maskeliyse degismez; dunya ya da parser raporu onceden
    maskelendiyse kural tabanli maskeler (bilinmeyen kulup/lig, oyuncu) tekrar uygulanmaz.
    Sentetik ve altyapi oyunculari kurgusal havuzlardan geldigi icin maskelenmez.
    Seviye: verilen > parser raporundaki > SEED_NAME_MASKING > light.
    'off' seviyesinde hicbir ad degistirilmez (kimlik eslemesi); dunya yine "islendi" sayilir,
    boylece seviyeyi tasiyan ozet (mask_summary) dogrulama adimlarina ulasir.
    """
    if world.names_masked and world.mask_summary is not None:
        return world.mask_summary
    report = world.parse_report
    at_ingest = report is not None and report.masked
    level = _mask_level_for(report, level)
    fm_players = [p for c in world.clubs for p in c.players if p.data_source == "fm"]
    # Acik veri kokenli lig/kulup adlari maskelenmez ve ozette de sayilmaz (bkz. OPEN_SOURCE).
    maskable_leagues = _maskable_leagues(world)
    maskable_clubs = _maskable_clubs(world)
    summary = MaskSummary(level, leagues=len(maskable_leagues), clubs=len(maskable_clubs),
                          fm_players=len(fm_players), at_ingest=at_ingest,
                          open_leagues=len(world.leagues) - len(maskable_leagues),
                          open_clubs=len(world.clubs) - len(maskable_clubs))
    if level == MASK_OFF:                     # maskeleme kapali: adlar oldugu gibi kalir
        world.names_masked, world.mask_summary = True, summary
        return summary

    # Rehber kulubu/bilinen lig her zaman maskeli ada normalize edilir; bilinmeyenler yalnizca hamsa
    club_map = build_club_mask_map((c.name for c in maskable_clubs), level)
    for club in maskable_clubs:
        new = club_map[club.name] if not at_ingest or lookup_club(club.name) else club.name
        if new != club.name:
            club.name = new
            summary.renamed_clubs += 1

    league_map = build_league_mask_map((lg.name for lg in maskable_leagues), level)
    for league in maskable_leagues:
        known = canonical_league(league.name)[1] != OTHER_COUNTRY
        new = league_map[league.name] if not at_ingest or known else league.name
        if new != league.name:
            league.name = new
            summary.renamed_leagues += 1

    if fm_players and not at_ingest:
        others = [p.name for c in world.clubs for p in c.players if p.data_source != "fm"]
        player_map = build_player_mask_map(
            [(p.name, p.nationality) for p in fm_players], level, reserved=others
        )
        for player in fm_players:
            player.name = player_map[player.name]
        summary.renamed_players = len(fm_players)

    world.names_masked, world.mask_summary = True, summary
    return summary


def new_world_source(open_dir: Path | None = None) -> str:
    """
    Web'den acilan YENI kariyer / paylasilan dunya icin kaynak (CLI varsayilani 'auto' DEGISMEZ):
      1) OFM_NEW_WORLD_SOURCE gecerliyse (NEW_WORLD_SOURCES) o -- sahibin kendi makinesindeki ACIK secimi;
      2) data/open okunabiliyorsa 'open' (6 lig, 114 gercek kulup adi, CC0);
      3) yoksa 'synthetic'.
    16A-0 (sahip vetosu K-S11): data/fm'deki FM disa aktarimi ARTIK KENDILIGINDEN SECILMEZ (14C'de 2. adimdi).
    data/fm sahibin kisisel FM disa aktarimidir; onunla kurulan bir PAYLASILAN dunya SI verisini
    (players.fm_attributes) baska menajerlere acar ve data_licensing.md bunu yasaklar ("dunyayi baska
    menajerlerle paylasmak -- asla"). FM tabanli yeni kariyer yalnizca iki yoldan acilir:
    OFM_NEW_WORLD_SOURCE=fm (sahibin kendi makinesi, kendi kariyeri) ya da CLI `python seed.py --source fm`
    (DATABASE_URL'in gosterdigi kendi veritabani). seed()/CLI varsayilani 'auto' DEGISMEZ.
    Ortam degiskeni cagri aninda okunur (import aninda degil). open_dir: None -> open_loader.OPEN_DATA_DIR.
    """
    wanted = (os.getenv(NEW_WORLD_SOURCE_ENV) or "").strip().lower()
    if wanted in NEW_WORLD_SOURCES:
        return wanted
    import open_loader  # gec import: open_loader seed'i import eder

    try:
        open_loader.load_open_data(open_loader.OPEN_DATA_DIR if open_dir is None else open_dir)
    except open_loader.OpenDataError:
        return "synthetic"
    return OPEN_SOURCE


def resolve_world(
    rng_seed: int,
    source: str = "auto",
    fm_paths: Sequence[str | Path] | None = None,
    include_samples: bool = False,
    season_year: int = DEFAULT_SEASON_YEAR,
    fm_dir: Path = FM_DATA_DIR,
    mask_level: str | None = None,
    open_sample: bool = False,
    real_players: bool = False,
    squads_path: Path | None = None,
    fm_overlay: bool = True,
    min_confidence: str = "low",
) -> WorldSpec:
    """
    Kaynagi secer, dunya tanimini kurar, SON ADIM olarak isimleri maskeler (mask_world) ve
    dogrular. Tutarsizlik ya da maskelenmemis gercek isim varsa SeedError: veritabanina
    henuz dokunulmamistir. mask_level="off" yalnizca OFM_ALLOW_REAL_NAMES izniyle gecerlidir.
    source="open" acik veri dunyasini kurar (data/open/, CC0): lig/kulup adlari gercektir,
    maskelenmez; oyuncular yine uretilir.
    real_players=True (16G): acik veri dunyasina GERCEK oyuncu kadrolari (data/local/squads.json) + FM yamasi
    (fm_overlay; fm_paths ya da data/fm'deki disa aktarimlar). OFM_ALLOW_REAL_PLAYERS=1 sart, yoksa SeedError.
    Varsayilan (False) yol bu parametrelerden hic etkilenmez.
    """
    if real_players:
        world = _build_real_world(rng_seed, source, fm_paths, include_samples, season_year, fm_dir, mask_level,
                                  open_sample, squads_path, fm_overlay, min_confidence)
        level = MASK_OFF
    else:
        level = _mask_level_for(None, mask_level)
        world = _build_world(rng_seed, source, fm_paths, include_samples, season_year, fm_dir, level,
                             open_sample)
    mask_world(world, level)
    problems = validate_world(world)
    if problems:
        label = {"fm": "FM dünyası", OPEN_SOURCE: "Açık veri dünyası"}.get(world.source, "Dünya")
        raise SeedError(f"{label} tutarsız, veritabanına dokunulmadı: " + "; ".join(problems[:10]))
    return world


def _build_real_world(
    rng_seed: int,
    source: str,
    fm_paths: Sequence[str | Path] | None,
    include_samples: bool,
    season_year: int,
    fm_dir: Path,
    mask_level: str | None,
    open_sample: bool,
    squads_path: Path | None,
    fm_overlay: bool,
    min_confidence: str = "low",
) -> WorldSpec:
    """
    16G: gercek oyunculu acik veri dunyasi. Onay (OFM_ALLOW_REAL_PLAYERS) ve arguman denetimi dunya kurulmadan
    ONCE yapilir. FM yamasi: fm_paths verilmisse onlar, yoksa data/fm'deki disa aktarimlar (sample_* haric;
    include_samples ile dahil). Adlar ham okunur (mask_level="off"): eslesme icin gerekir; DB'ye FM adi YAZILMAZ,
    oyuncunun adi Wikidata'dan kalir.
    """
    if not real_players_allowed():
        raise SeedError(REAL_PLAYERS_OPTIN_ERROR)
    if source not in (OPEN_SOURCE, "auto") or (mask_level is not None and str(mask_level).strip().lower() != MASK_OFF):
        raise SeedError(REAL_PLAYERS_SOURCE_ERROR)
    import open_loader  # gec import: open_loader seed'i import eder

    records = files = None
    notes: list[str] = []
    if fm_overlay:
        paths = [Path(p) for p in fm_paths] if fm_paths else fm_parser.discover_files(fm_dir, include_samples)
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise SeedError(f"Dosya bulunamadı: {', '.join(missing)}")
        if paths:
            report = fm_parser.parse_files(paths, mask_names=True, mask_level=MASK_OFF)
            records, files = report.players, [p.name for p in paths]
            notes += [f"FM yaması uyarısı: {w}" for w in report.warnings]
        else:
            notes.append(f"FM yaması yok: {fm_dir} içinde dışa aktarım bulunamadı (bkz. data/fm/README.md).")
    try:
        squads = open_loader.load_squads(squads_path or open_loader.SQUADS_FILE)
        world = open_loader.build_real_world(rng_seed, squads, sample=open_sample, fm_records=records,
                                             fm_files=files or (), season_year=season_year,
                                             min_confidence=min_confidence)
    except open_loader.OpenDataError as exc:
        raise SeedError(str(exc)) from exc
    world.notes += notes
    return world


def _build_world(
    rng_seed: int,
    source: str,
    fm_paths: Sequence[str | Path] | None,
    include_samples: bool,
    season_year: int,
    fm_dir: Path,
    mask_level: str,
    open_sample: bool = False,
) -> WorldSpec:
    if source == OPEN_SOURCE:
        # gec import: sentetik/FM yolu open_loader'a bagli degil (open_loader seed'i import eder)
        import open_loader
        try:
            return open_loader.build_open_world(rng_seed, sample=open_sample)
        except open_loader.OpenDataError as exc:
            raise SeedError(str(exc)) from exc
    if source == "synthetic" and not fm_paths:
        return build_synthetic_world(rng_seed)

    paths = [Path(p) for p in fm_paths] if fm_paths else fm_parser.discover_files(fm_dir, include_samples)
    if not paths:
        if source == "fm" or include_samples:
            raise SeedError(f"{fm_dir} içinde FM dışa aktarımı bulunamadı (.html/.csv/.txt).")
        world = build_synthetic_world(rng_seed)
        world.notes.append(
            f"{fm_dir} içinde FM dışa aktarımı yok; kurgusal dünya kuruldu. "
            f"Gerçek veri için dışa aktarımını oraya koy (bkz. data/fm/README.md)."
        )
        return world

    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise SeedError(f"Dosya bulunamadı: {', '.join(missing)}")

    report = fm_parser.parse_files(paths, mask_names=True, mask_level=mask_level)
    world = build_fm_world(report, rng_seed, season_year, mask_level)
    if not world.leagues:
        details = "; ".join(report.warnings + world.notes) or report.summary()
        raise SeedError(f"FM verisinden oynanabilir lig kurulamadı. {details}")
    return world


# ===========================================================================
# 6) VERITABANINA YAZMA
# ===========================================================================

class StaffNameFactory:
    """Tekrar etmeyen teknik heyet ismi uretir."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._used: set[str] = set()

    def make(self) -> str:
        for _ in range(500):
            name = f"{self._rng.choice(STAFF_FIRST_NAMES)} {self._rng.choice(STAFF_LAST_NAMES)}"
            if name not in self._used:
                self._used.add(name)
                return name
        name = f"{self._rng.choice(STAFF_FIRST_NAMES)} {self._rng.choice(STAFF_LAST_NAMES)} II"
        self._used.add(name)
        return name


def generate_staff(rng: random.Random, names: StaffNameFactory, role: StaffRole, reputation: int) -> Staff:
    """Itibardan alt ozellikleri ve maasi turetilmis personel uretir."""
    reputation = _clamp(reputation, 1, 100)
    attrs = staff_rules.generate_attributes(rng, role, reputation)
    return Staff(
        name=names.make(),
        role=role,
        reputation=reputation,
        wage=staff_rules.staff_wage(role, reputation),
        **attrs,
    )


def _player_orm(spec: PlayerSpec) -> Player:
    a = spec.attributes
    return Player(
        name=spec.name,
        age=spec.age,
        position=spec.position,
        overall_rating=spec.overall,
        pace=a["pace"], shooting=a["shooting"], passing=a["passing"],
        defending=a["defending"], dribbling=a["dribbling"], goalkeeping=a["goalkeeping"],
        form=spec.form,
        morale=spec.morale,
        condition=CONDITION_MAX,              # yeni dunya: herkes tam kondisyonla baslar
        contract_years=spec.contract_years,
        market_value=spec.market_value if spec.market_value is not None
        else market_value(spec.overall, spec.age, spec.position, spec.potential),
        nationality=spec.nationality,
        data_source=spec.data_source,
        fm_uid=spec.fm_uid,
        current_ability=spec.current_ability,
        potential_ability=spec.potential_ability,
        fm_attributes=spec.fm_attributes,
        potential_rating=spec.potential,
    )


def write_world(db, world: WorldSpec, rng_seed: int, with_fixtures: bool = True) -> None:
    """
    Dunya tanimini BOS tablolara yazar. Commit cagirana aittir.
    Son emniyet kilidi: maskelenmemis gercek kulup/lig adi varsa hicbir sey yazilmaz.
    Maskeleme kapali (off) kurulmus dunyada bu kilit bilerek uygulanmaz (bkz. masking_off).
    """
    if not masking_off(world):
        leaks = find_leaks(_world_names(world))
        if leaks:
            raise SeedError(f"Maskelenmemiş gerçek isim veritabanına yazılamaz: {', '.join(leaks[:10])}")
        if _unmasked_fm_players(world):            # oyuncu adlari: yalnizca maskeli ad yazilir
            raise SeedError("Maskelenmemiş FM oyuncu adları veritabanına yazılamaz (önce mask_world).")
    add_youth_world(world, rng_seed)          # elle kurulmus WorldSpec icin (builder'lar zaten ekler)
    staff_rng = random.Random(rng_seed + 2)
    staff_names = StaffNameFactory(staff_rng)
    fixture_rng = random.Random(rng_seed + 3)
    written: list[tuple[Team, ClubSpec]] = []

    # Dunyanin isim maskeleme seviyesi kayda yazilir: arayuz uyarisi ve paylasilan dunya
    # denetimi (worlds.py) bunu okur. Seviyesi bilinmeyen (elle kurulmus) dunya 'light' sayilir.
    level = world.mask_summary.level if world.mask_summary is not None else DEFAULT_MASK_LEVEL
    db.add(GameState(id=1, season=1, current_week=1, user_team_id=None,
                     manager_reputation=START_REPUTATION, academy_seeded=True, mask_level=level))

    for role, count in FREE_AGENT_STAFF_PLAN.items():
        for _ in range(count):
            db.add(generate_staff(staff_rng, staff_names, role, _clamp(staff_rng.gauss(58, 16), 20, 95)))

    for league_spec in world.leagues:
        league = League(name=league_spec.name, country=league_spec.country)
        db.add(league)

        for club in league_spec.clubs:
            team = Team(
                league=league,
                name=club.name,
                transfer_budget=club.transfer_budget,
                reputation=club.reputation,
                formation=club.formation,
                youth_facilities=club.youth_facilities,
            )
            written.append((team, club))
            specs = sorted(club.players, key=lambda p: -p.overall)
            team.players = []
            for rank, spec in enumerate(specs):
                player = _player_orm(spec)
                player.squad_role = (SquadRole.STAR if rank < 3
                                     else SquadRole.FIRST_TEAM if rank < 11
                                     else SquadRole.BACKUP)
                player.current_wage = spec.current_wage or expected_wage(
                    spec.overall, club.reputation, player.squad_role
                )
                team.players.append(player)

            team.staff = [
                generate_staff(staff_rng, staff_names, role,
                               _clamp(staff_rng.gauss(club.reputation - 6, 7), 20, 95))
                for role, count in TEAM_STAFF_PLAN.items()
                for _ in range(count)
            ]
            wage_bill = sum(p.current_wage for p in team.players) + sum(s.wage for s in team.staff)
            team.wage_budget = int(round(wage_bill * wage_headroom_for(club.reputation) / 1000) * 1000)
            db.add(team)

        db.flush()

        if with_fixtures:
            team_ids = [t.id for t in league.teams]
            fixture_rng.shuffle(team_ids)
            for week_index, pairs in enumerate(build_round_robin(team_ids), start=1):
                for home_id, away_id in pairs:
                    db.add(Fixture(
                        season=1, league_id=league.id,
                        home_team_id=home_id, away_team_id=away_id,
                        week=week_index, status=FixtureStatus.UNPLAYED,
                    ))

    # Akademiler EN SONDA: kidemli oyuncu id'leri akademisiz dunyayla ayni kalir.
    # team_id ile yazilir (team.players koleksiyonuna eklenmez: o koleksiyon yalnizca A takimdir).
    for team, club in written:
        for spec in club.academy:
            player = _player_orm(spec)
            player.team_id = team.id
            player.in_academy = True
            player.squad_role = SquadRole.BACKUP
            player.lineup_status = LineupStatus.OUT
            player.current_wage = spec.current_wage or academy_wage(spec.overall, club.reputation)
            db.add(player)
    db.flush()


def seed(
    rng_seed: int,
    with_fixtures: bool = True,
    source: str = "auto",
    fm_paths: Sequence[str | Path] | None = None,
    include_samples: bool = False,
    season_year: int = DEFAULT_SEASON_YEAR,
    mask_level: str | None = None,
    open_sample: bool = False,
    real_players: bool = False,
    squads_path: Path | None = None,
    fm_overlay: bool = True,
    min_confidence: str = "low",
) -> WorldSpec:
    """Dunyayi secer ve yazar (tablolarin bos oldugu varsayilir). real_players: bkz. resolve_world (16G)."""
    world = resolve_world(rng_seed, source, fm_paths, include_samples, season_year,
                          mask_level=mask_level, open_sample=open_sample, real_players=real_players,
                          squads_path=squads_path, fm_overlay=fm_overlay, min_confidence=min_confidence)
    with session_scope() as db:
        write_world(db, world, rng_seed, with_fixtures)
    return world


def hard_reset() -> None:
    """
    AKTIF kariyerin semasi (database.current_career_schema(), yoksa 'public') tamamen silinip
    yeniden kurulur. ENUM/tablo kalintisi birakmaz; diger kullanicilarin kariyer semalarina ve
    'accounts' semasina dokunmaz.
    """
    schema = database.current_career_schema() or database.LEGACY_CAREER_SCHEMA
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    print(f"[seed] '{schema}' şeması sıfırdan oluşturuldu (hard reset).")


# ===========================================================================
# 7) DOGRULAMA RAPORU
# ===========================================================================

def verify(mask_level: str | None = None) -> bool:
    """
    Veritabanindaki veriyi okuyup ozet rapor basar. Sorun varsa False doner.
    Seviye: verilen > game_state.mask_level (dunyanin kuruldugu seviye) > light. 'off' ise gercek
    kulup/lig adlari BEKLENEN durumdur: isim sizintisi hata degil, uyaridir (MASK_OFF_WARNING).
    Boylece --verify-only, hicbir bayrak verilmese de dunyanin kendi seviyesiyle rapor verir.
    """
    ok = True
    with SessionLocal() as db:
        league_count = db.scalar(select(func.count()).select_from(League)) or 0
        team_count = db.scalar(select(func.count()).select_from(Team)) or 0
        player_count = db.scalar(select(func.count()).select_from(Player)
                                 .where(Player.in_academy.is_(False))) or 0
        academy_count = db.scalar(select(func.count()).select_from(Player)
                                  .where(Player.in_academy.is_(True))) or 0
        no_potential = db.scalar(select(func.count()).select_from(Player)
                                 .where(Player.potential_rating.is_(None))) or 0
        fixture_count = db.scalar(select(func.count()).select_from(Fixture)) or 0
        staff_count = db.scalar(select(func.count()).select_from(Staff)) or 0
        free_staff = db.scalar(select(func.count()).select_from(Staff).where(Staff.team_id.is_(None))) or 0
        sources = dict(db.execute(select(Player.data_source, func.count()).group_by(Player.data_source)).all())
        fm_world = sources.get("fm", 0) > 0
        open_world = sources.get(OPEN_SOURCE, 0) > 0
        variable_squads = fm_world or open_world        # kadro buyuklugu kulupten kulube degisir

        print()
        print("=" * 72)
        print(" VERITABANI DOGRULAMA RAPORU")
        print("=" * 72)
        print(f"  Kaynak  : {'FM verisi' if fm_world else 'açık veri (CC0)' if open_world else 'sentetik'}  "
              f"({', '.join(f'{k}: {v}' for k, v in sorted(sources.items()))})")
        print(f"  Lig     : {league_count}")
        print(f"  Takim   : {team_count}")
        print(f"  Oyuncu  : {player_count} A takım + {academy_count} akademi (U-21)")
        if no_potential:
            print(f"  Potansiyel: !! {no_potential} oyuncuda potansiyel yok (CareerManager.ensure_youth_setup)")
        print(f"  Fikstur : {fixture_count}")
        print(f"  Personel: {staff_count} ({free_staff} boşta)")
        state = db.get(GameState, 1)
        if state is not None:
            print(f"  Durum   : sezon {state.season}, hafta {state.current_week}, "
                  f"menajer tanınırlığı {state.manager_reputation:.1f}/20")
        stored_level = state.mask_level if state is not None else None
        off = normalize_mask_level(mask_level or stored_level or DEFAULT_MASK_LEVEL) == MASK_OFF
        names = list(db.scalars(select(League.name))) + list(db.scalars(select(Team.name)))
        leaks = find_leaks(names)
        if off and open_world:
            # 16G: acik veri + 'off' yalnizca gercek oyuncu kadrolariyla kurulur (open_loader.build_real_world)
            print(f"  İsimler : GERÇEK OYUNCULAR (off) — {sources.get(OPEN_SOURCE, 0)} Wikidata oyuncusu, "
                  f"{sources.get('fm', 0)} FM yamalı, {sources.get('synthetic', 0)} üretilmiş tamamlama.")
            print("  " + REAL_PLAYERS_WARNING.replace("\n", "\n  "))
        elif off:
            print(f"  İsimler : MASKELEME KAPALI (off) — {len(names)} lig/kulüp adı gerçek verinden "
                  f"({len(leaks)} tanesi rehberdeki gerçek adla birebir aynı).")
            print("  " + MASK_OFF_WARNING.replace("\n", "\n  "))
        elif open_world:
            print(f"  İsimler : açık veri (CC0) — {len(names)} lig/kulüp adı gerçek ve maskesiz; "
                  f"oyuncu adları üretilmiştir, kişisel veri yoktur.")
        elif leaks:
            print(f"  İsimler : !! UYARI: maskelenmemiş gerçek isim: {', '.join(leaks[:10])}")
            ok = False
        else:
            print(f"  İsimler : maskeli ({len(names)} lig/kulüp adında gerçek isim yok)")

        for league in db.scalars(select(League).order_by(League.name)).all():
            print()
            print(f"  {league.name}  ({league.country})")
            print("  " + "-" * 70)
            print(f"  {'Takim':<24}{'Itibar':>7}{'Kadro':>6}{'Akad.':>6}{'Ort.':>6}{'Transfer':>10}"
                  f"{'Maas/hf':>10}{'Kull.':>7}   En iyi oyuncu")
            for team in sorted(league.teams, key=lambda t: -t.reputation):
                best = max(team.players, key=lambda p: p.overall_rating)
                usage = f"%{100 * team.wage_bill / team.wage_budget:.0f}" if team.wage_budget else "-"
                print(
                    f"  {team.name:<24}{team.reputation:>7}{len(team.players):>6}"
                    f"{len(team.academy_players):>6}{team.squad_rating:>6}"
                    f"{team.transfer_budget / 1_000_000:>9.0f}M{team.wage_budget / 1000:>9.0f}K{usage:>7}   "
                    f"{best.name} ({best.position.value} {best.overall_rating})"
                )
                if team.free_wage < 0:
                    print(f"     !! UYARI: {team.name} maaş bütçesini aşıyor.")
                    ok = False
                if len(team.staff) != sum(TEAM_STAFF_PLAN.values()):
                    print(f"     !! UYARI: {team.name} teknik heyeti eksik ({len(team.staff)}).")
                    ok = False
                keepers = sum(1 for p in team.players if p.position is Position.GK)
                if variable_squads:
                    if len(team.players) < FM_MIN_SQUAD or keepers < 2:
                        print(f"     !! UYARI: {team.name} kadrosu oynanamaz ({len(team.players)} oyuncu, {keepers} GK).")
                        ok = False
                elif len(team.players) != SQUAD_SIZE:
                    print(f"     !! UYARI: {team.name} kadrosunda {len(team.players)} oyuncu var.")
                    ok = False

        if not variable_squads:
            print()
            print("  Mevki dagilimi (tum ligler):")
            rows = db.execute(
                select(Player.position, func.count()).where(Player.in_academy.is_(False))
                .group_by(Player.position).order_by(Player.position)
            ).all()
            for position, count in rows:
                expected = SQUAD_COMPOSITION[position] * team_count
                flag = "" if count == expected else f"  !! beklenen {expected}"
                print(f"    {position.value:<5}{count:>5}{flag}")
                if count != expected:
                    ok = False

        if fixture_count:
            print()
            print("  Ornek fikstur (1. hafta):")
            for fx in db.scalars(select(Fixture).where(Fixture.week == 1).order_by(Fixture.league_id)).all():
                print(f"    [{fx.league.country:<10}] {fx.home_team.name} - {fx.away_team.name}")

        print()
        print("=" * 72)
        print(" SONUC:", "BASARILI" if ok else "KONTROL GEREKIYOR")
        print("=" * 72)
    return ok


# ===========================================================================
# 8) CLI
# ===========================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    """CLI secenekleri (ayri islev: seviye cozumlemesi testten dogrulanabilsin)."""
    parser = argparse.ArgumentParser(description="Veritabanını sıfırla ve dünyayı kur (FM verisi veya sentetik).")
    parser.add_argument("--seed", type=int, default=int(os.getenv("SEED_RANDOM_SEED", "2026")),
                        help="Rastgelelik tohumu (aynı tohum = aynı dünya).")
    parser.add_argument("--source", choices=("auto", "fm", "synthetic", OPEN_SOURCE), default="auto",
                        help="auto: data/fm/ doluysa FM, değilse sentetik. "
                             "open: data/open/ açık verisi (CC0) — gerçek kulüp ve lig adları, "
                             "üretilmiş kadrolar.")
    parser.add_argument("--fm", nargs="+", metavar="DOSYA", help="Belirli FM dışa aktarım dosyaları.")
    parser.add_argument("--fm-sample", action="store_true",
                        help="data/fm/sample_* kurgusal örnek dosyalarıyla FM akışını dene.")
    parser.add_argument("--open-sample", action="store_true",
                        help="Açık veri akışını küçük bir dünyayla dene: her ligin yalnızca "
                             "en itibarlı birkaç kulübü (itibarlar tam dünyayla aynı).")
    parser.add_argument("--season-year", type=int, default=DEFAULT_SEASON_YEAR,
                        help="Sözleşme bitiş yılından kalan süre hesabı için başlangıç yılı.")
    parser.add_argument("--keep", action="store_true", help="Tabloları drop etme.")
    parser.add_argument("--hard-reset", action="store_true",
                        help="Aktif kariyer şemasını (varsayılan 'public') komple silip yeniden kur.")
    parser.add_argument("--no-fixtures", action="store_true", help="Fikstür üretme.")
    parser.add_argument("--verify-only", action="store_true", help="Hiçbir şey yazma, sadece raporla.")
    parser.add_argument("--mask-level", choices=MASK_LEVELS, default=None,
                        help="İsim maskeleme: light ('Erling Harland', 'Hakan Çalhano'), strong (tamamen kurgusal) "
                             f"ya da off (KİŞİSEL/YEREL: kendi lisanslı FM verinin gerçek adları; ayrıca "
                             f"{REAL_NAMES_ENV}=1 gerekir, bu dünya paylaşılamaz/yayımlanamaz). "
                             f"Verilmezse: {MASK_LEVEL_ENV} ortam değişkeni (off hariç), yoksa light. "
                             "--verify-only ile verilmezse dünyanın kurulduğu seviye kullanılır.")
    # 16G: gercek oyuncu kadrolari (kisisel/yerel, tek koltuk)
    parser.add_argument("--real-players", action="store_true",
                        help="GERÇEK OYUNCU KADROLARI (KİŞİSEL/YEREL): açık veri dünyasına data/local/squads.json "
                             "(tools/build_squads.py, Wikidata) kadroları + data/fm'de dışa aktarım varsa FM yaması. "
                             f"Ayrıca {REAL_PLAYERS_ENV}=1 gerekir; yalnızca tek koltuklu kariyer, paylaşılamaz.")
    parser.add_argument("--squads", type=Path, default=None, metavar="DOSYA",
                        help="--real-players kadro dosyası (varsayılan data/local/squads.json).")
    parser.add_argument("--no-fm-overlay", action="store_true",
                        help="--real-players ile FM yamasını kapat (yalnızca Wikidata iskeleti + üretilmiş yetenek).")
    parser.add_argument("--min-confidence", choices=("low", "medium", "high"), default="low",
                        help="--real-players: Wikidata oyuncusu için en düşük güven. low (varsayılan) en çok gerçek "
                             "adı verir ama eski/bayat üyelikler de girer; medium/high daha az ama güncel oyuncu.")
    parser.add_argument("--career-schema", default=None, metavar="ŞEMA",
                        help="Dünyayı bu kariyer şemasına kur (ör. sahibin 'career_<id>' şeması; accounts.users."
                             "career_schema). Verilmezse 'public'.")
    return parser


FM_OVERLAY_REPORT = ROOT / "data" / "local" / "fm_overlay_report.json"


def _real_players_target_problem() -> str | None:
    """16G: hedef sema paylasilan dunyaysa (ya da oyle kayitliysa) gercek oyuncu dunyasi YAZILMAZ."""
    import worlds  # gec import: hesap/dunya kaydi yalnizca bu denetimde gerekir

    schema = database.current_career_schema() or database.LEGACY_CAREER_SCHEMA
    try:
        worlds.refuse_real_players_target(schema)
    except worlds.WorldError as exc:
        return str(exc)
    return None


def _print_real_players(world: WorldSpec) -> None:
    report = world.real_players
    for line in report.lines():
        print(f"[seed] {line}")
    if report.fm is not None:
        for name, club, reason in report.fm.unmatched[:15]:
            print(f"[seed]   FM eşleşmedi: {name} ({club}) — {reason}")
        if len(report.fm.unmatched) > 15:
            print(f"[seed]   ... ve {len(report.fm.unmatched) - 15} FM oyuncusu daha (tam liste: {FM_OVERLAY_REPORT})")
        FM_OVERLAY_REPORT.parent.mkdir(parents=True, exist_ok=True)          # data/local/: gitignore
        FM_OVERLAY_REPORT.write_text(json.dumps(report.fm.to_dict(), ensure_ascii=False, indent=1) + "\n",
                                     encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.career_schema:
        return _run(args)
    try:
        schema = database.valid_schema_name(args.career_schema)
    except ValueError as exc:
        print(f"[seed] HATA: {exc}")
        return 1
    with database.career_context(schema):
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    print(f"[seed] Hedef veritabani: {database.masked_url()}")
    if args.career_schema:
        print(f"[seed] Hedef kariyer şeması: {database.current_career_schema()}")
    if not wait_for_db():
        print("\n[seed] HATA: Veritabanina baglanilamadi. 'docker compose ps' ile kontrol et.")
        return 1
    if args.verify_only:
        return 0 if verify(args.mask_level) else 1

    # Once dunyayi kur (veritabanina dokunmadan): hata varsa mevcut veriyi silmeden cik
    try:
        source = args.source
        if args.fm_sample:
            source = "fm"
        elif args.open_sample:
            source = OPEN_SOURCE
        world = resolve_world(
            args.seed,
            source=source,
            fm_paths=args.fm,
            include_samples=args.fm_sample,
            season_year=args.season_year,
            mask_level=args.mask_level,
            open_sample=args.open_sample,
            real_players=args.real_players,
            squads_path=args.squads,
            fm_overlay=not args.no_fm_overlay,
            min_confidence=args.min_confidence,
        )
    except SeedError as exc:
        print(f"\n[seed] HATA: {exc}")
        print("[seed] Veritabanına dokunulmadı.")
        return 1
    if world.real_players is not None:
        problem = _real_players_target_problem()
        if problem:
            print(f"\n[seed] HATA: {problem}")
            print("[seed] Veritabanına dokunulmadı.")
            return 1
        _print_real_players(world)

    if world.parse_report is not None:
        report = world.parse_report
        print(f"[seed] FM verisi okundu: {report.summary()}")
        for name, fmt in report.files:
            print(f"         - {name} ({fmt})")
        if report.unknown_columns:
            print(f"[seed] Tanınmayan sütunlar (yok sayıldı): {', '.join(sorted(report.unknown_columns))}")
        for warning in report.warnings:
            print(f"[seed] UYARI: {warning}")
        for file, line, reason in report.skipped[:10]:
            print(f"[seed] Atlandı: {file}:{line} — {reason}")
        if len(report.skipped) > 10:
            print(f"[seed] ... ve {len(report.skipped) - 10} satır daha atlandı.")
    for note in world.notes:
        print(f"[seed] Not: {note}")
    if world.mask_summary is not None:
        print(f"[seed] {world.mask_summary.text()}")

    if args.hard_reset:
        hard_reset()
        database.init_db()
    elif args.keep:
        database.init_db()
        print("[seed] Tablolar korundu (--keep).")
    else:
        database.reset_db()
        print("[seed] Tablolar silinip yeniden olusturuldu.")

    label = {"fm": "FM verisi", OPEN_SOURCE: "açık veri"}.get(world.source, "sentetik")
    print(f"[seed] {label} yazılıyor: {len(world.leagues)} lig, {len(world.clubs)} kulüp, "
          f"{world.player_count} oyuncu + {world.academy_count} akademi oyuncusu (tohum={args.seed})...")
    with session_scope() as db:
        write_world(db, world, args.seed, with_fixtures=not args.no_fixtures)
    print("[seed] Yazma tamamlandi.")
    applied = world.mask_summary.level if world.mask_summary is not None else args.mask_level
    return 0 if verify(applied) else 1


if __name__ == "__main__":
    sys.exit(main())
