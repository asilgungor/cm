"""
open_loader.py
==============
Acik veri dunyasi (13F. Asama). `data/open/` altindaki VENDOR dosyalarini okur ve
seed.WorldSpec'e cevirir: GERCEK kulup ve lig adlari, URETILMIS kadrolar.

    data/open/clubs.json     openfootball/clubs      (CC0 1.0)
    data/open/leagues.json   openfootball/football.json + openfootball/europe (CC0 1.0)

Dosyalari `tools/build_open_data.py` uretir (yalnizca gelistirici; internet ister).
BU MODUL YALNIZCA STDLIB KULLANIR ve ag baglantisi kurmaz.

Ne gercek, ne uretilmis?
    gercek (CC0)  : lig adi (Turkce yazimi), kulup adi, kulubun sehri/stadi/kurulus yili,
                    gecmis sezon puan tablolari
    uretilmis     : butun oyuncular (ad, yas, mevki, yetenek, potansiyel, piyasa degeri),
                    kulup itibari, transfer/maas butcesi, dizilis, altyapi
Oyuncu adi ve kisisel veri YOKTUR; oyuncular name_pools havuzlarindan uretilir.
(Istisna, 16G: build_real_world -- ONAYLI, YEREL, TEK KOLTUKLU gercek oyuncu kadrolari; bkz. bolum 5.
 build_open_world bundan hic etkilenmez.)

Itibar (data_licensing.md §3.1):
    z_c  : kulubun lig ici mac basina puan z-skoru, sezonlar yakinliga gore agirliklandirilmis
           (yeni cikan kulup icin z = NEWCOMER_Z sozde gozlemi)
    R_c  = clamp(B(ulke, seviye) + REPUTATION_SLOPE * clip(z_c, ±2.5), 35, 96)
    B tablosu ve egim club_directory.py'deki 38 elle verilmis kulup itibariyla kalibre edildi.
    Belgedeki ilk tahmin egim 6 idi; en kucuk kareler 8.1 verdi ve 8 secildi: 6 ile lig ici
    aralik fazla dar kaliyordu (Premier Lig'in en zayifi 76). Sonuc: 38 kulupte ortalama sapma
    1.5, en buyuk 4 puan (bkz. tests/test_open_world.py).

Kadro gucu (data_licensing.md §3.2):
    merkez = 0.484 * R + 39.97 ,  band = merkez ± 5      (seed.LEAGUE_DATA regresyonu)
    Kadro, sentetik dunyadaki gibi seed.build_rating_targets merdiveniyle uretilir ve
    seed.pad_squad ile mevki asgarileri garantilenir.

Determinizm:
    Her kulubun RNG akisi sha256("ofm-open|<tohum>|<kulup kimligi>") ile tohumlanir; Python'un
    TUZLU hash() fonksiyonu KULLANILMAZ. Koleksiyonlar tuketilmeden once siralanir. Ayni tohum
    + ayni vendor dosyalari = birebir ayni dunya.

Kademeler (16A-0) -- UYARI:
    leagues.json 1. kademelerin yaninda 5 ikinci kademe (en.2, es.2, de.2, it.2, fr.2; `tier: 2`)
    tasir. build_open_world VARSAYILAN OLARAK YALNIZCA 1. KADEMEYI kurar (tiers=(1,)); ligler
    DONGUDEN ONCE suzulur, boylece OpenNameFactory'nin dunya genelindeki SIRALI isim akisi ve
    kulup tohumlari 16A-0 oncesiyle birebir aynidir (tests/test_open_world.py OPEN_WORLD_TIER1_DIGEST).
    tiers=(1, 2) YALNIZCA testlerde ve ileride 16A'da kullanilir; arayuzde ve CLI'da ACILMAZ:
    League.tier, kume dusme/cikma ve kupa katilim kurallari olmadan 2. lig sampiyonu Devler
    Arenasi'na girerdi (cup_draw.qualify her ligin 1.'sini alir).
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import seed as seed_module
from finance import market_value, transfer_budget_for_reputation
from models import Position
from name_pools import YOUTH_NAME_POOLS, pool_key, unique_name

ROOT = Path(__file__).resolve().parent
OPEN_DATA_DIR = ROOT / "data" / "open"

CLUBS_FILE = "clubs.json"
LEAGUES_FILE = "leagues.json"

CLUBS_SCHEMA = "ofm/open-clubs"
LEAGUES_SCHEMA = "ofm/open-leagues"

# --open-sample: her ligin EN ITIBARLI bu kadar kulubuyle kucuk (hizli) bir dunya kurulur.
# Ayri bir ornek dosya YOKTUR: itibarlar tam dunyayla birebir aynidir, yalnizca kulup sayisi azdir.
SAMPLE_CLUBS_PER_LEAGUE = 6

# ClubSpec/LeagueSpec.data_source ve Player.data_source degeri (models.py CHECK'inde tanimli).
OPEN_SOURCE = seed_module.OPEN_SOURCE

# --- itibar egrisi ---------------------------------------------------------
MIN_SEASON_MATCHES = 10        # kulup bu kadar mac oynamadiysa o sezon sayilmaz (yarim sezon)
MIN_SEASON_CLUBS = 8           # bu kadar kulubu olmayan tablodan z cikarilmaz
SEASON_DECAY = 0.85            # en yeni kullanilabilir sezon 1.0, bir oncesi 0.85, ...
NEWCOMER_Z = -1.3              # gecmisi olmayan (yeni cikan) kulup icin sozde gozlem
NEWCOMER_WEIGHT = 0.9
Z_CLIP = 2.5
REPUTATION_SLOPE = 8.0
REPUTATION_MIN, REPUTATION_MAX = 35, 96
DEFAULT_BASE_REPUTATION = 68.0                        # tablosu olmayan ulke/seviye icin

# B(ulke, seviye): editoryal taban, club_directory'deki kulup itibarlariyla kalibre edildi.
BASE_REPUTATION: dict[tuple[str, int], float] = {
    ("tr", 1): 70.0,
    ("en", 1): 84.0,
    ("es", 1): 80.5,
    ("de", 1): 79.5,
    ("it", 1): 79.0,
    ("fr", 1): 77.5,
    # 16A-0: ikinci kademeler. Kalibrasyon: 2. kademe medyan itibari ayni ulkenin 1. kademe
    # medyanindan 8-18 puan asagida (uclarda ortusme serbest; bkz. tests/test_open_world.py).
    # Olculen fark (2026-09-18 verisi): en 14.5, es 13.5, de 13.5, it 15.5, fr 15.0. Italya onerilen
    # 65 ile 17.5'te (sinira yakin) kaliyordu: it.2'nin yalnizca 3 sezon tablosu var, yeni gelen
    # kulup cok, medyan z dusuk; taban 67'ye cekildi.
    ("en", 2): 70.0,
    ("de", 2): 66.0,
    ("es", 2): 66.0,
    ("it", 2): 67.0,
    ("fr", 2): 64.0,
}

DEFAULT_TIERS: tuple[int, ...] = (1,)                 # build_open_world varsayilani: yalnizca 1. kademe

# --- kadro ----------------------------------------------------------------
STRENGTH_SLOPE = 0.484         # merkez = SLOPE * itibar + INTERCEPT
STRENGTH_INTERCEPT = 39.97
STRENGTH_SPREAD = 5            # band = merkez ± SPREAD
STRENGTH_MIN, STRENGTH_MAX = 30, 95

OPEN_SQUAD_COMPOSITION: dict[Position, int] = {
    Position.GK: 3, Position.DEF: 6, Position.MID: 6, Position.FWD: 5,
}
EXTRA_PLAYERS = (0, 2)         # kadroya rastgele eklenen saha oyuncusu sayisi
EXTRA_CYCLE = (Position.DEF, Position.MID, Position.FWD)


class OpenDataError(Exception):
    """Acik veri dosyalari okunamadi ya da tutarsiz. Mesaji dogrudan kullaniciya gosterilebilir."""


# ===========================================================================
# 1) VENDOR DOSYALARI
# ===========================================================================

@dataclass(frozen=True)
class OpenData:
    """Okunmus vendor dosyalari (saf veri)."""
    clubs: dict                             # clubs.json
    leagues: dict                           # leagues.json
    directory: Path

    @property
    def club_index(self) -> dict[str, dict]:
        return {c["id"]: c for c in self.clubs.get("clubs", [])}

    def provenance(self) -> str:
        """Seed ciktisina ve dunya notlarina yazilan kaynak/lisans satiri."""
        return (f"Açık veri: {self.leagues.get('source')} + {self.clubs.get('source')} "
                f"({self.leagues.get('license')}, {self.leagues.get('fetched_at')} tarihinde alındı). "
                f"Kulüp ve lig adları gerçek; tüm oyuncular üretilmiştir.")


def _read_json(path: Path, schema: str) -> dict:
    if not path.is_file():
        raise OpenDataError(
            f"Açık veri dosyası yok: {path}. Önce 'python tools/build_open_data.py' çalıştır."
        )
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpenDataError(f"{path} okunamadı: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != schema:
        raise OpenDataError(f"{path}: beklenen şema '{schema}' değil ({doc.get('schema')!r}).")
    if not doc.get("license") or not doc.get("fetched_at") or not doc.get("source"):
        raise OpenDataError(f"{path}: lisans/kaynak/tarih bilgisi eksik (vendor dosyası bozuk).")
    return doc


def load_open_data(directory: Path | str = OPEN_DATA_DIR) -> OpenData:
    """Vendor dosyalarini okur ve lisans bilgisinin yerinde oldugunu dogrular."""
    directory = Path(directory)
    data = OpenData(
        clubs=_read_json(directory / CLUBS_FILE, CLUBS_SCHEMA),
        leagues=_read_json(directory / LEAGUES_FILE, LEAGUES_SCHEMA),
        directory=directory,
    )
    if not data.leagues.get("leagues"):
        raise OpenDataError(f"{directory / LEAGUES_FILE}: hiç lig yok.")
    return data


# ===========================================================================
# 2) ITIBAR
# ===========================================================================

def _usable_seasons(league: dict) -> list[dict]:
    """Yarim kalmis ya da cok kucuk tablolari eler; en yeniden eskiye siralar."""
    usable = []
    for season in league.get("seasons", []):
        rows = [r for r in season.get("table", []) if r.get("pld", 0) >= MIN_SEASON_MATCHES]
        if len(rows) >= MIN_SEASON_CLUBS:
            usable.append({"season": season.get("season", ""), "table": rows})
    usable.sort(key=lambda s: s["season"], reverse=True)
    return usable


def season_z_scores(rows: list[dict]) -> dict[str, float]:
    """Bir sezon tablosundan kulup basina mac basina puan z-skoru."""
    ppg = {r["id"]: r["pts"] / r["pld"] for r in sorted(rows, key=lambda r: r["id"])}
    mean = statistics.fmean(ppg.values())
    spread = statistics.pstdev(ppg.values()) or 1.0
    return {club: (value - mean) / spread for club, value in ppg.items()}


def league_form_scores(league: dict) -> dict[str, float]:
    """Kulup kimligi -> agirlikli z. Gecmisi olmayan kulup NEWCOMER_Z'ye yaklasir."""
    weighted: dict[str, list[tuple[float, float]]] = {}
    for index, season in enumerate(_usable_seasons(league)):
        weight = SEASON_DECAY ** index
        for club, z in sorted(season_z_scores(season["table"]).items()):
            weighted.setdefault(club, []).append((weight, z))
    scores: dict[str, float] = {}
    for club in sorted({c["id"] for c in league.get("clubs", [])} | set(weighted)):
        observations = weighted.get(club, [])
        total = sum(w for w, _ in observations) + NEWCOMER_WEIGHT
        value = sum(w * z for w, z in observations) + NEWCOMER_WEIGHT * NEWCOMER_Z
        scores[club] = value / total
    return scores


def base_reputation(country_code: str, tier: int) -> float:
    return BASE_REPUTATION.get((country_code, tier), DEFAULT_BASE_REPUTATION)


def reputation_from_form(base: float, z: float) -> int:
    """R = clamp(B + egim * clip(z, ±2.5), 35, 96)."""
    value = base + REPUTATION_SLOPE * max(-Z_CLIP, min(Z_CLIP, z))
    return int(max(REPUTATION_MIN, min(REPUTATION_MAX, round(value))))


def league_reputations(league: dict) -> dict[str, int]:
    """Ligdeki her kulup icin itibar (1-100)."""
    base = base_reputation(league.get("country_code", ""), int(league.get("tier", 1)))
    scores = league_form_scores(league)
    return {club["id"]: reputation_from_form(base, scores.get(club["id"], NEWCOMER_Z))
            for club in league.get("clubs", [])}


def strength_band(reputation: int) -> tuple[int, int]:
    """Itibardan kadro guc bandi (seed.LEAGUE_DATA regresyonu)."""
    centre = STRENGTH_SLOPE * reputation + STRENGTH_INTERCEPT
    low = int(max(STRENGTH_MIN, min(STRENGTH_MAX, round(centre - STRENGTH_SPREAD))))
    high = int(max(low + 1, min(STRENGTH_MAX, round(centre + STRENGTH_SPREAD))))
    return low, high


# ===========================================================================
# 3) DETERMINIZM
# ===========================================================================

def club_seed(world_seed: int, club_id: str) -> int:
    """
    Kulubun RNG tohumu: sha256("ofm-open|<tohum>|<kimlik>"). Python'un tuzlu hash()'i
    surecten surece degistigi icin ASLA kullanilmaz.
    """
    digest = hashlib.sha256(f"ofm-open|{world_seed}|{club_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


class OpenNameFactory(seed_module.NameFactory):
    """
    seed.NameFactory arayuzu (pad_squad `make(country)` cagirir), iki farkla:
        * her kulup icin O KULUBUN RNG akisina gecer (for_club); "kullanilmis adlar" kumesi
          tum dunya icin ORTAKTIR, boylece 2400 oyuncuda ad tekrari olmaz;
        * GENIS isim havuzunu (name_pools.YOUTH_NAME_POOLS, 900-2000 birlesim) kullanir.
          Kidemli havuzlar (NAME_POOLS) Ispanya/Almanya/Fransa icin 10x10'dur; acik veri
          dunyasinda lig basina 400'den fazla oyuncu var, o havuz ilk 100 adda tukenirdi.
          NAME_POOLS'a DOKUNULMAZ: sentetik dunya ayni tohumla birebir ayni kalir.
    """

    def for_club(self, rng: random.Random) -> OpenNameFactory:
        self._rng = rng
        return self

    def make(self, country: str) -> str:
        first_names, last_names = YOUTH_NAME_POOLS[pool_key(country)]
        return unique_name(self._rng, first_names, last_names, self._used)


# ===========================================================================
# 4) DUNYA
# ===========================================================================

def _squad_positions(rng: random.Random) -> list[Position]:
    positions = [pos for pos, count in sorted(OPEN_SQUAD_COMPOSITION.items(), key=lambda kv: kv[0].name)
                 for _ in range(count)]
    for index in range(rng.randint(*EXTRA_PLAYERS)):
        positions.append(EXTRA_CYCLE[index % len(EXTRA_CYCLE)])
    rng.shuffle(positions)
    return positions


def build_club_spec(
    club: dict,
    reputation: int,
    country: str,
    rng: random.Random,
    names: OpenNameFactory,
) -> seed_module.ClubSpec:
    """Tek kulup: gercek ad + itibardan uretilmis kadro."""
    band = strength_band(reputation)
    positions = _squad_positions(rng)
    targets = seed_module.build_rating_targets(rng, band[0], band[1], len(positions))
    players = [
        seed_module.generate_player_spec(rng, names.make(country), position, target, band,
                                         data_source=OPEN_SOURCE)
        for position, target in zip(positions, targets, strict=True)
    ]
    # Mevki asgarileri ve asgari kadro FM yoluyla ayni sekilde garantilenir (normalde 0 ekler).
    players, added = seed_module.pad_squad(players, rng, names, country)
    return seed_module.ClubSpec(
        name=club["name"],
        reputation=reputation,
        transfer_budget=transfer_budget_for_reputation(reputation),
        formation=seed_module._pick_formation(rng),
        players=players,
        academy_added=added,
        data_source=OPEN_SOURCE,
    )


def sample_roster(clubs: list[dict], reputations: dict[str, int], limit: int) -> list[dict]:
    """--open-sample: ligin en itibarli `limit` kulubu (esitlikte kimlige gore, deterministik)."""
    ranked = sorted(clubs, key=lambda c: (-reputations.get(c["id"], 0), c["id"]))
    return ranked[:limit]


def build_open_world(
    rng_seed: int,
    data_dir: Path | str = OPEN_DATA_DIR,
    sample: bool = False,
    data: OpenData | None = None,
    sample_clubs: int = SAMPLE_CLUBS_PER_LEAGUE,
    tiers: Sequence[int] = DEFAULT_TIERS,
) -> seed_module.WorldSpec:
    """
    Vendor dosyalarindan oynanabilir dunya kurar. Veritabanina dokunmaz.
    Kulup ve lig adlari gercek ve `data_source="open"` ile isaretlidir: mask_world onlara
    dokunmaz, sizinti denetimi onlari disarida birakir (bkz. seed._world_names).
    sample=True: her ligden yalnizca en itibarli birkac kulup (hizli deneme; itibarlar aynidir).
    tiers: kurulacak kademeler; varsayilan yalnizca 1. kademe. (1, 2) YALNIZCA testler ve 16A
    icindir (modul basligindaki uyariya bak). Ligler dongudan ONCE suzulur ve (kademe, kod)
    sirasiyla kurulur: 1. kademe kulupleri isim akisini once tuketir, kidemli kadrolari (1,)
    dunyasiyla aynidir.
    """
    data = data or load_open_data(data_dir)
    wanted_tiers = {int(tier) for tier in tiers}
    selected = [lg for lg in data.leagues["leagues"] if int(lg.get("tier", 1)) in wanted_tiers]
    names = OpenNameFactory(random.Random(rng_seed))
    leagues: list[seed_module.LeagueSpec] = []
    notes: list[str] = [data.provenance()]
    if sample:
        notes.append(f"Küçük örnek dünya (--open-sample): her ligde en itibarlı {sample_clubs} kulüp.")

    for league in sorted(selected, key=lambda lg: (int(lg.get("tier", 1)), lg.get("code", ""))):
        country = league.get("country") or ""
        reputations = league_reputations(league)
        roster = list(league.get("clubs", []))
        if sample:
            roster = sample_roster(roster, reputations, sample_clubs)
        clubs: list[seed_module.ClubSpec] = []
        for club in sorted(roster, key=lambda c: c["id"]):
            rng = random.Random(club_seed(rng_seed, club["id"]))
            clubs.append(build_club_spec(club, reputations[club["id"]], country,
                                         rng, names.for_club(rng)))
        if len(clubs) < seed_module.FM_MIN_LEAGUE_CLUBS:
            notes.append(f"{league.get('name')}: yalnızca {len(clubs)} kulüp var, lig kurulamadı.")
            continue
        leagues.append(seed_module.LeagueSpec(
            name=league["name"], country=country, clubs=clubs, data_source=OPEN_SOURCE,
        ))
    if not leagues:
        raise OpenDataError("Açık veriden oynanabilir lig kurulamadı.")

    world = seed_module.WorldSpec(OPEN_SOURCE, leagues, notes)
    seed_module.add_youth_world(world, rng_seed)
    return world


# ===========================================================================
# 5) GERCEK OYUNCU KADROLARI (16G, K-S1) -- YEREL KISISEL VERI, TEK KOLTUK
# ===========================================================================
#
# build_real_world, build_open_world'un AYNI kulup/lig/itibar iskeletine Wikidata kadrolarini (data/local/squads.json,
# tools/build_squads.py) oturtur. build_open_world'a DOKUNULMAZ: varsayilan dunya bit-bit aynidir
# (tests/test_open_world.py OPEN_WORLD_TIER1_DIGEST).
#
#   gercek (Wikidata, CC0)  : ad, dogum tarihi (-> yas), uyruk, mevki, kulup, forma numarasi (PlayerSpec'te; DB'de sutun yok)
#   uretilmis               : TUM yetenekler (kulup itibari bandi + mevki + yas; ayni seed.build_rating_targets merdiveni),
#                             form/moral/sozlesme/potansiyel/deger ve eksik kadro yerleri (data_source="synthetic";
#                             ic etiket, arayuzde gosterilmez)
#   FM yamasi (istege bagli): sahibin KENDI FM26 disa aktarimiyla eslesen oyuncu gercek FM ozelliklerini alir
#                             (data_source="fm"; seed.player_from_record ile, ad/yas Wikidata'dan kalir)
#
# Kadro ici siralama: guc merdiveni (en iyiden en zayifa) oyunculara taninirlik (sitelinks) yuzdeligi ve yas egrisiyle
# dagitilir; uretilmis tamamlama oyunculari merdivenin ALTINA gelir. Bant ve merdiven acik veri dunyasiyla aynidir:
# yildiz sayisi ve kalite dagilimi bugunku uretilmis dunyaya benzer.
#
# Gizlilik: bu dunya ONAY BAYRAGI (OFM_ALLOW_REAL_PLAYERS=1, seed.real_players_allowed) olmadan KURULMAZ ve
# maskeleme seviyesi 'off' olarak kaydedilir (game_state.mask_level): worlds.world_has_real_names paylasilan dunya
# kurulumunu ve paylasilan dunyaya cevirmeyi REDDEDER. Web'den acilan dunya bu yolu hic cagirmaz
# (accounts._build_world real_players gecirmez; seed.NEW_WORLD_SOURCES degismedi).

LOCAL_DIR = ROOT / "data" / "local"
SQUADS_FILE = LOCAL_DIR / "squads.json"
SQUADS_SCHEMA = "ofm/local-squads"

REAL_SOURCE = OPEN_SOURCE          # Wikidata iskeleti oyuncusu (adi gercek, yetenegi uretilmis)
GENERATED_SOURCE = "synthetic"     # kadro tamamlama (ic etiket)
FM_SOURCE = "fm"                   # FM yamasiyla gercek ozellik alan oyuncu

REAL_SQUAD_TARGET = 22             # kadro en az bu sayiya uretilmis oyuncuyla tamamlanir (acik veri dunyasi 20-22)
REAL_SQUAD_MAX = 30                # makullik tavani: gercek + uretilmis en fazla 30 (guven sirasiyla kesilir)
PLAUSIBLE_AGE = 38                 # tavanda esitlik bozucu: bu yasi asan once duser (yas tek basina dislamaz)
# Dusuk guvenli Wikidata oyuncusu (tazelik kaniti yok / baslangic tarihi yok; olculen orneklerde cogu eski oyuncu)
# yalnizca kadro bu sayiya ulasana kadar alinir: bos yeri uretilmis oyuncu yerine gercek ad doldurur, ama kadro
# 30'a dusuk guvenli kayitlarla sisirilmez. Yuksek/orta guvenli oyuncu 30'a kadar alinir.
LOW_CONFIDENCE_UNTIL = REAL_SQUAD_TARGET
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
# Mevki asgarisi (uretilmis oyuncuyla tamamlanir; kadro sinirinda bunlara yer ayrilir). Gercek kadrolar ~3/8/8/5.
REAL_POSITION_MINIMUM: dict[Position, int] = {Position.GK: 3, Position.DEF: 6, Position.MID: 5, Position.FWD: 4}
REAL_MAX_KEEPERS = 4
REAL_MIN_AGE, REAL_MAX_AGE = 16, 40
REAL_FILL_CYCLE = (Position.DEF, Position.MID, Position.FWD, Position.MID)
UNKNOWN_POSITION = Position.MID    # Wikidata'da mevkisi olmayan oyuncu (tahmin; kaleci asla verilmez)
PROMINENCE_WEIGHT, AGE_WEIGHT = 0.75, 0.25
FM_AGE_TOLERANCE = 1               # FM yasi ile Wikidata yasi arasi en fazla fark (oyun ici tarih bilinmez)

_POSITIONS = {p.value: p for p in Position}


@dataclass(frozen=True)
class RealPlayer:
    qid: str
    name: str
    birth_date: date
    nationality: str | None
    position: Position | None
    shirt_number: int | None
    since: str                      # uyelik baslangici ("" = Wikidata'da yok, dusuk guven)
    fresh: int                      # 0-2 (tools/build_squads.freshness)
    sitelinks: int
    confidence: str = "low"         # high / medium / low (tools/build_squads.assemble)


@dataclass(frozen=True)
class SquadData:
    clubs: dict[str, tuple[RealPlayer, ...]]      # acik veri kulup kimligi -> oyuncular
    reference_date: date
    fetched_at: str
    path: Path

    @property
    def player_count(self) -> int:
        return sum(len(players) for players in self.clubs.values())


def _nation(label: str | None) -> str | None:
    """Wikidata'nin Ingilizce ulke adi -> oyunun ulus adi (national_rules takma adlari; bilinmeyen oldugu gibi)."""
    if not label:
        return None
    import national_rules  # gec import: yalnizca gercek kadro yolunda

    return national_rules.nation_of(label, "")


def _confidence(raw: dict) -> str:
    """Kayittaki guven; eski (surum 1) dosyada tazelikten turetilir, baslangicsiz uyelik her zaman dusuk."""
    value = raw.get("confidence")
    if value in CONFIDENCE_RANK:
        return value
    if not raw.get("since"):
        return "low"
    return {2: "high", 1: "medium"}.get(int(raw.get("fresh", 0)), "low")


def load_squads(path: Path | str = SQUADS_FILE) -> SquadData:
    """data/local/squads.json'u okur ve dogrular (OpenDataError). Dosya YEREL kisisel veridir, depoda yoktur."""
    path = Path(path)
    if not path.is_file():
        raise OpenDataError(
            f"Gerçek kadro dosyası yok: {path}. Önce 'python tools/build_squads.py' çalıştır (Wikidata, yerel).")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpenDataError(f"{path} okunamadı: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SQUADS_SCHEMA or not isinstance(doc.get("clubs"), dict):
        raise OpenDataError(f"{path}: beklenen şema '{SQUADS_SCHEMA}' değil.")
    try:
        reference = date.fromisoformat(doc["reference_date"])
        clubs: dict[str, tuple[RealPlayer, ...]] = {}
        for club_id, entry in sorted(doc["clubs"].items()):
            players = []
            for raw in entry.get("players", []):
                players.append(RealPlayer(
                    qid=str(raw["qid"]),
                    name=" ".join(str(raw["name"]).split())[:80],
                    birth_date=date.fromisoformat(raw["birth_date"]),
                    nationality=_nation(raw.get("nationality")),
                    position=_POSITIONS.get(raw.get("position") or ""),
                    shirt_number=raw.get("shirt_number") if isinstance(raw.get("shirt_number"), int) else None,
                    since=str(raw.get("since") or ""),
                    fresh=int(raw.get("fresh", 0)),
                    sitelinks=int(raw.get("sitelinks", 0)),
                    confidence=_confidence(raw),
                ))
            clubs[club_id] = tuple(players)
    except (KeyError, TypeError, ValueError) as exc:
        raise OpenDataError(f"{path}: bozuk kayıt ({exc}).") from exc
    return SquadData(clubs, reference, str(doc.get("fetched_at", "")), path)


def age_on(birth: date, reference: date) -> int:
    return reference.year - birth.year - ((reference.month, reference.day) < (birth.month, birth.day))


# --- FM yamasi: esleme --------------------------------------------------------

_PERSON_FOLD = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ł": "l",
                              "Ł": "l", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "þ": "th", "Þ": "th"})


def person_key(name: str) -> str:
    """Aksan ve noktalama duyarsiz oyuncu adi anahtari ("Martin Ødegaard" == "martin odegaard")."""
    from club_directory import plain_key

    return plain_key((name or "").translate(_PERSON_FOLD))


def club_key_index(data: OpenData, club_ids: set[str]) -> dict[str, set[str]]:
    """Kulup ad anahtari (club_directory.normalize) -> acik veri kulup kimlikleri (ad, takma adlar, kadro adlari)."""
    from club_directory import normalize

    names: dict[str, set[str]] = {}
    for record in data.clubs.get("clubs", []):
        if record["id"] in club_ids:
            names.setdefault(record["id"], set()).update([record["name"], *record.get("aliases", [])])
    for league in data.leagues.get("leagues", []):
        for club in league.get("clubs", []):
            if club["id"] in club_ids:
                names.setdefault(club["id"], set()).add(club["name"])
    index: dict[str, set[str]] = {}
    for club_id, forms in names.items():
        for form in forms:
            key = normalize(form)
            if key:
                index.setdefault(key, set()).add(club_id)
    return index


def resolve_fm_club(text: str | None, index: dict[str, set[str]]) -> str | None:
    """FM'deki kulup adi -> acik veri kulup kimligi; bulunamazsa ya da belirsizse None (tutucu)."""
    from club_directory import lookup_club, normalize

    if not text:
        return None
    hits = index.get(normalize(text), set())
    if not hits:
        info = lookup_club(text)                  # "Man City" -> Manchester City (rehber yazimlari)
        if info is not None:
            for form in (info.name, *info.aliases):
                hits = hits | index.get(normalize(form), set())
    return next(iter(hits)) if len(hits) == 1 else None


def _birth_matches(fm_birth: str | None, birth: date) -> bool | None:
    """FM dogum tarihi (gun-ay yer degistirmis olabilir) Wikidata'yla ayni mi? FM'de yoksa None."""
    if not fm_birth:
        return None
    try:
        year, first, second = (int(part) for part in fm_birth.split("-"))
    except ValueError:
        return None
    return year == birth.year and (first, second) in {(birth.month, birth.day), (birth.day, birth.month)}


def _age_matches(record, player: RealPlayer, reference: date) -> bool:
    by_birth = _birth_matches(getattr(record, "birth_date", None), player.birth_date)
    if by_birth is not None:
        return by_birth
    if record.age is None:
        return False
    return abs(record.age - age_on(player.birth_date, reference)) <= FM_AGE_TOLERANCE


@dataclass
class FMOverlayReport:
    """FM yamasi raporu (gercek adlar icerir: YALNIZCA yerel; data/local/ altina yazilir)."""
    files: list[str] = field(default_factory=list)
    records: int = 0
    matched: list[tuple[str, str, str]] = field(default_factory=list)       # (kulup, qid, ad): Wikidata + FM
    added: list[tuple[str, str]] = field(default_factory=list)              # (kulup, FM adi): yalnizca FM'de olan
    duplicates: list[tuple[str, str]] = field(default_factory=list)         # (kulup, Wikidata adi): FM kaydina yol verdi
    unmatched: list[tuple[str, str, str]] = field(default_factory=list)     # (FM adi, FM kulubu, neden)

    def summary(self) -> str:
        reasons = Counter(reason for *_, reason in self.unmatched)
        detail = ", ".join(f"{reason}: {n}" for reason, n in sorted(reasons.items()))
        return (f"FM dışa aktarımı: {len(self.files)} dosya, {self.records} FM oyuncusu; {len(self.matched)} Wikidata "
                f"oyuncusuyla eşleşti, {len(self.added)} yalnızca FM'den kadroya eklendi (gerçek ad + özellik), "
                f"{len(self.duplicates)} Wikidata kaydı FM kaydına yol verdi, {len(self.unmatched)} yok sayıldı"
                + (f" ({detail})" if detail else "") + ".")

    def to_dict(self) -> dict:
        return {"files": self.files, "records": self.records,
                "matched": [{"club": c, "qid": q, "name": n} for c, q, n in self.matched],
                "added": [{"club": c, "name": n} for c, n in self.added],
                "duplicates": [{"club": c, "name": n} for c, n in self.duplicates],
                "unmatched": [{"name": n, "club": c, "reason": r} for n, c, r in self.unmatched]}


FM_NO_CLUB = "kulüp bu dünyada yok"
FM_NO_PLAYER = "kadroda eşleşen oyuncu yok"          # (surum 1) artik yok sayilmaz: yalnizca-FM oyuncusu olarak eklenir
FM_AMBIGUOUS = "birden fazla aday (belirsiz)"


def match_fm_records(
    records: Sequence,
    squads: SquadData,
    data: OpenData,
    club_ids: set[str],
) -> tuple[dict[str, object], dict[str, list], FMOverlayReport]:
    """
    FM kayitlari -> ({Wikidata qid: FM kaydi}, {kulup: [yalnizca-FM kayitlari]}, rapor).
    ESLEME TUTUCU: kulup ayni olmali, ad aksansiz esit (1. tur) ya da kelime kumesi esit / dogum tarihi birebir + soyad
    (2. tur), yas en fazla FM_AGE_TOLERANCE farkli (FM dogum tarihi varsa o esas). Iki yonlu tekillik: belirsiz aday
    eslesmez ve yok sayilir (ayni kisinin iki kez yazilmamasi icin). ONCELIK FM'de (16G ikinci gecis): kulubu dunyamizda
    olan ama Wikidata'da eslesmeyen FM oyuncusu kadroya YALNIZCA-FM oyuncusu olarak girer. Kulubu dunyamizda olmayan
    FM oyuncusu yok sayilir ve raporlanir.
    """
    report = FMOverlayReport(records=len(records))
    index = club_key_index(data, club_ids)
    by_club: dict[str, list] = {}
    for record in records:
        club_id = resolve_fm_club(record.club, index)
        if club_id is None or club_id not in club_ids:
            report.unmatched.append((record.name, record.club or "", FM_NO_CLUB))
            continue
        by_club.setdefault(club_id, []).append(record)

    matches: dict[str, object] = {}
    fm_only: dict[str, list] = {}
    for club_id in sorted(by_club):
        players = list(squads.clubs.get(club_id, ()))
        pending: list = sorted(by_club[club_id], key=lambda r: (person_key(r.name), r.age or 0, r.line_no))
        ambiguous: set[int] = set()
        for test in (_same_name, _same_tokens_or_birth):
            for i, player in _match_pass(pending, players, set(matches), test, squads.reference_date, ambiguous):
                matches[player.qid] = pending[i]
                report.matched.append((club_id, player.qid, player.name))
                pending[i] = None
        for i, record in enumerate(pending):
            if record is None:
                continue
            if i in ambiguous:
                report.unmatched.append((record.name, record.club or "", FM_AMBIGUOUS))
            else:
                fm_only.setdefault(club_id, []).append(record)
    report.matched.sort()
    report.unmatched.sort()
    return matches, fm_only, report


def _same_name(record, player: RealPlayer) -> bool:
    """1. tur: aksansiz tam ad."""
    return person_key(record.name) == person_key(player.name)


def _same_tokens_or_birth(record, player: RealPlayer) -> bool:
    """2. tur: ayni kelimeler baska sirayla ("Son Heung-min") ya da dogum tarihi birebir + ayni soyad."""
    fm, wd = person_key(record.name).split(), person_key(player.name).split()
    if fm and sorted(fm) == sorted(wd):
        return True
    return _birth_matches(getattr(record, "birth_date", None), player.birth_date) is True and fm[-1:] == wd[-1:]


def _match_pass(pending: list, players: list[RealPlayer], taken: set[str], test, reference: date,
                ambiguous: set[int]) -> list[tuple[int, RealPlayer]]:
    """
    Bir esleme turu: her bekleyen FM kaydi icin aday oyuncular (test + yas/dogum tarihi). Yalnizca TEK adayi olan ve
    o adayi baska hicbir kaydin istemedigi kayitlar eslesir (iki yonlu tekillik); gerisi belirsiz sayilir.
    """
    proposals: dict[int, list[RealPlayer]] = {}
    for i, record in enumerate(pending):
        if record is not None:
            proposals[i] = [p for p in players if p.qid not in taken and test(record, p)
                            and _age_matches(record, p, reference)]
    wanted = Counter(c[0].qid for c in proposals.values() if len(c) == 1)
    out: list[tuple[int, RealPlayer]] = []
    for i, cands in sorted(proposals.items()):
        if len(cands) == 1 and wanted[cands[0].qid] == 1:
            out.append((i, cands[0]))
            ambiguous.discard(i)
        elif cands:
            ambiguous.add(i)
    return out


def fm_age(record, reference: date) -> int:
    """Yalnizca-FM oyuncusunun yasi: DoB varsa referans tarihinde, yoksa FM'deki yas, o da yoksa 25."""
    if getattr(record, "birth_date", None):
        try:
            return age_on(date.fromisoformat(record.birth_date), reference)
        except ValueError:
            pass
    return record.age if record.age is not None else 25


def drop_fm_duplicates(
    roster: Sequence[RealPlayer],
    club_id: str,
    fm_only: dict[str, list],
    reference: date,
) -> tuple[list[RealPlayer], list[RealPlayer]]:
    """
    FM ONCELIKLI: yalnizca-FM kaydiyla ayni kisi olabilecek Wikidata oyunculari kadroya alinmaz (ayni kisi iki kez
    yazilmasin). Ayni kulupte ayni ad (aksansiz) -> yas bakilmaksizin; baska kulubumuzde ayni ad + uyumlu yas.
    Donus: (kalanlar, dusenler).
    """
    same_club = {person_key(r.name) for r in fm_only.get(club_id, ())}
    elsewhere = [(person_key(r.name), fm_age(r, reference)) for cid, recs in fm_only.items() if cid != club_id
                 for r in recs]
    keep, dropped = [], []
    for player in roster:
        key = person_key(player.name)
        age = age_on(player.birth_date, reference)
        if key in same_club or any(k == key and abs(a - age) <= FM_AGE_TOLERANCE for k, a in elsewhere):
            dropped.append(player)
        else:
            keep.append(player)
    return keep, dropped


# --- kulup kurulumu -----------------------------------------------------------

@dataclass
class ClubCoverage:
    club_id: str
    club: str
    league: str
    in_file: int = 0             # squads.json'daki oyuncu
    used: int = 0                # kadroya giren Wikidata oyuncusu (FM eslesmeleri dahil)
    fm: int = 0                  # bunlardan FM ozelligi alan
    fm_only: int = 0             # yalnizca FM disa aktarimindan gelen gercek oyuncu
    fm_duplicates: int = 0       # FM kaydina yol veren Wikidata oyuncusu
    generated: int = 0           # uretilmis tamamlama
    dropped_age: int = 0         # REAL_MIN_AGE-REAL_MAX_AGE disi
    trimmed: int = 0             # kadro tavanini (ya da kaleci sinirini) astigi icin alinmayan
    guessed: int = 0             # Wikidata'da mevkisi yok, UNKNOWN_POSITION (orta saha) sayildi
    confidence: dict[str, int] = field(default_factory=dict)     # kadrodaki Wikidata oyuncularinin guveni
    samples: list[str] = field(default_factory=list)             # rapor icin 3 ornek ad (en taninmis gercekler)

    @property
    def real(self) -> int:
        return self.used + self.fm_only

    @property
    def total(self) -> int:
        return self.real + self.generated


@dataclass
class RealPlayersReport:
    squads_file: str
    fetched_at: str
    reference_date: str
    clubs: list[ClubCoverage] = field(default_factory=list)
    fm: FMOverlayReport | None = None

    @property
    def real(self) -> int:
        return sum(c.real for c in self.clubs)

    @property
    def generated(self) -> int:
        return sum(c.generated for c in self.clubs)

    def by_league(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for c in self.clubs:
            row = out.setdefault(c.league, {"clubs": 0, "in_file": 0, "real": 0, "fm": 0, "generated": 0,
                                            "low": 0})
            row["clubs"] += 1
            row["in_file"] += c.in_file
            row["real"] += c.real
            row["fm"] += c.fm + c.fm_only
            row["generated"] += c.generated
            row["low"] += c.confidence.get("low", 0)
        return out

    def lines(self) -> list[str]:
        out = [f"Gerçek oyuncu kadroları: {self.real} gerçek (Wikidata {self.fetched_at}, referans "
               f"{self.reference_date}) + {self.generated} üretilmiş tamamlama oyuncusu."]
        for league, row in sorted(self.by_league().items()):
            share = 100 * row["real"] / max(1, row["real"] + row["generated"])
            out.append(f"  {league}: {row['clubs']} kulüp, dosyada {row['in_file']}, kadroda {row['real']} gerçek "
                       f"({row['fm']} FM, {row['low']} düşük güven) + {row['generated']} üretilmiş = %{share:.0f} gerçek")
        if self.fm is not None:
            out.append(self.fm.summary())
        return out


def _prominence(players: list[tuple[RealPlayer, int]]) -> dict[str, float]:
    """Kulup ici taninirlik yuzdeligi: en cok sitelink 1.0, en az 0.0 (esitlikte qid)."""
    ranked = sorted(players, key=lambda pa: (-pa[0].sitelinks, int(pa[0].qid[1:] or 0)
                                             if pa[0].qid[1:].isdigit() else 0))
    if len(ranked) == 1:
        return {ranked[0][0].qid: 1.0}
    return {p.qid: 1.0 - i / (len(ranked) - 1) for i, (p, _age) in enumerate(ranked)}


def _age_curve(age: int) -> float:
    """Kadro ici guc sirasi icin yas carpani: 24-30 tepe; genc ve yasli asagi (yetenek degil, yalnizca sira)."""
    if age < 24:
        return max(0.3, 1.0 - (24 - age) * 0.1)
    if age <= 30:
        return 1.0
    return max(0.4, 1.0 - (age - 30) * 0.1)


def _keep_priority(entry: tuple[RealPlayer, int], fm_qids: set[str]) -> tuple:
    """
    Kadro tavaninda kim kalir (16G ikinci gecis): FM eslesmesi > guven (high/medium/low) > en yeni baslangic >
    yas <= PLAUSIBLE_AGE > taninmis > qid. Yas yalnizca esitlik bozucudur, kimseyi tek basina dislamaz.
    """
    player, age = entry
    return (player.qid not in fm_qids, CONFIDENCE_RANK.get(player.confidence, 2),
            "~" if not player.since else _invert(player.since), age > PLAUSIBLE_AGE, -player.sitelinks,
            int(player.qid[1:]) if player.qid[1:].isdigit() else 0)


def _invert(iso: str) -> str:
    """ISO tarihi azalan siraya cevirir (en yeni once): '2025-07-01' -> '7974-92-98'. Bos tarih cagiranda "~" (en sona)."""
    return "".join(chr(ord("9") - ord(ch) + ord("0")) if ch.isdigit() else ch for ch in iso)


def select_real_players(
    players: Sequence[RealPlayer],
    reference: date,
    fm_positions: dict[str, Position],
    reserved: Counter | None = None,
) -> tuple[list[tuple[RealPlayer, int, Position]], int, int]:
    """
    Kulubun Wikidata oyunculari -> [(oyuncu, yas, mevki)], yas disi sayisi, tavan disi sayisi.
    reserved: kadroya ONCEDEN giren yalnizca-FM oyuncularinin mevki sayilari (tavana ve mevkilere sayilir).
    Kadro REAL_SQUAD_MAX'i asmaz ve mevki asgarileri icin yer birakilir; en fazla REAL_MAX_KEEPERS kaleci.
    Dusuk guvenli oyuncu yalnizca kadro LOW_CONFIDENCE_UNTIL'e ulasana kadar alinir.
    Mevkisi bilinmeyen oyuncu orta saha sayilir (UNKNOWN_POSITION; acik mevkiler uretilmis oyuncuyla dolar).
    FM ile eslesen oyuncunun mevkisi FM'den gelir.
    """
    eligible, dropped_age = [], 0
    for player in players:
        age = age_on(player.birth_date, reference)
        if REAL_MIN_AGE <= age <= REAL_MAX_AGE:
            eligible.append((player, age))
        else:
            dropped_age += 1
    fm_qids = set(fm_positions)
    chosen: list[tuple[RealPlayer, int, Position]] = []
    counts: Counter = Counter(reserved or {})
    base = sum(counts.values())
    trimmed = 0
    for player, age in sorted(eligible, key=lambda e: _keep_priority(e, fm_qids)):
        position = fm_positions.get(player.qid) or player.position or UNKNOWN_POSITION
        if position is Position.GK and counts[Position.GK] >= REAL_MAX_KEEPERS:
            trimmed += 1
            continue
        after = counts + Counter({position: 1})
        reserve = sum(max(0, REAL_POSITION_MINIMUM[p] - after[p]) for p in Position)
        low = player.qid not in fm_qids and player.confidence == "low"
        if base + len(chosen) + 1 + reserve > REAL_SQUAD_MAX or (low and base + len(chosen) + 1 > LOW_CONFIDENCE_UNTIL):
            trimmed += 1
            continue
        chosen.append((player, age, position))
        counts[position] += 1
    return chosen, dropped_age, trimmed


def select_fm_only(records: Sequence) -> tuple[list, int]:
    """Yalnizca-FM oyunculari (CA, PA, ad sirasiyla); tavan ve kaleci siniri. Donus: (secilenler, dusenler)."""
    ranked = sorted(records, key=lambda r: (-(r.current_ability or 0), -(r.potential_ability or 0),
                                            person_key(r.name), r.line_no))
    chosen: list = []
    for record in ranked:
        keepers = sum(r.position is Position.GK for r in chosen)
        if len(chosen) >= REAL_SQUAD_MAX or (record.position is Position.GK and keepers >= REAL_MAX_KEEPERS):
            continue
        chosen.append(record)
    return chosen, len(ranked) - len(chosen)


def build_real_club_spec(
    club: dict,
    reputation: int,
    country: str,
    rng: random.Random,
    names: OpenNameFactory,
    roster: Sequence[RealPlayer],
    reference: date,
    fm_matches: dict[str, object],
    fm_rng: random.Random,
    season_year: int,
    league_code: str = "",
    fm_only: Sequence = (),
    fm_duplicates: int = 0,
) -> tuple[seed_module.ClubSpec, ClubCoverage]:
    """
    Tek kulup: gercek ad + gercek kadro + itibardan uretilmis yetenek; eksikler uretilmis oyuncuyla.
    Sira (16G ikinci gecis): 1) yalnizca-FM oyunculari (gercek ad + FM ozellikleri), 2) Wikidata oyunculari (FM ile
    eslesen FM ozelligi alir), 3) uretilmis tamamlama. Wikidata oyunculari ve tamamlama kulup bandinin guc merdiveninden
    yetenek alir; FM oyunculari kendi FM degerlerini tasir.
    """
    band = strength_band(reputation)
    fm_positions = {qid: rec.position for qid, rec in fm_matches.items()
                    if any(p.qid == qid for p in roster)}
    fm_first, fm_cut = select_fm_only(fm_only)
    chosen, dropped_age, trimmed = select_real_players(roster, reference, fm_positions,
                                                       Counter(r.position for r in fm_first))
    coverage = ClubCoverage(club["id"], club["name"], league_code, in_file=len(roster), used=len(chosen),
                            fm_only=len(fm_first), fm_duplicates=fm_duplicates, dropped_age=dropped_age,
                            trimmed=trimmed + fm_cut,
                            guessed=sum(1 for p, _a, _pos in chosen if p.position is None and p.qid not in fm_positions),
                            confidence=dict(Counter(p.confidence for p, _a, _pos in chosen)))

    prominence = _prominence([(p, age) for p, age, _pos in chosen]) if chosen else {}
    ordered = sorted(chosen, key=lambda c: (-(PROMINENCE_WEIGHT * prominence[c[0].qid] + AGE_WEIGHT * _age_curve(c[1])),
                                            int(c[0].qid[1:]) if c[0].qid[1:].isdigit() else 0))
    coverage.samples = [r.name for r in fm_first[:3]] + [p.name for p, _a, _pos in ordered[:max(0, 3 - len(fm_first))]]
    counts = Counter(pos for _p, _a, pos in chosen) + Counter(r.position for r in fm_first)
    fills: list[Position] = []
    for position in sorted(REAL_POSITION_MINIMUM, key=lambda p: p.name):
        fills += [position] * max(0, REAL_POSITION_MINIMUM[position] - counts[position])
    index = 0
    while len(chosen) + len(fm_first) + len(fills) < REAL_SQUAD_TARGET:
        fills.append(REAL_FILL_CYCLE[index % len(REAL_FILL_CYCLE)])
        index += 1
    rng.shuffle(fills)                  # merdivendeki yerleri mevkiye gore dizilmesin (acik veri dunyasi gibi karisik)
    coverage.generated = len(fills)

    players: list[seed_module.PlayerSpec] = []
    for record in fm_first:            # yalnizca-FM: gercek ad + FM ozellikleri (ayri RNG akisi)
        spec = seed_module.player_from_record(record, fm_rng, season_year)
        spec.age = fm_age(record, reference)
        spec.nationality = _nation(record.nationality)
        players.append(spec)
    targets = seed_module.build_rating_targets(rng, band[0], band[1], len(ordered) + len(fills))
    for (player, age, position), target in zip(ordered, targets[:len(ordered)], strict=True):
        spec = seed_module.generate_player_spec(rng, player.name, position, target, band, data_source=REAL_SOURCE)
        spec.age = age
        spec.nationality = player.nationality
        spec.market_value = market_value(spec.overall, age, position)
        spec.shirt_number, spec.wikidata_id = player.shirt_number, player.qid
        record = fm_matches.get(player.qid)
        if record is not None:                  # FM yamasi: gercek FM ozellikleri kazanir (ad/yas Wikidata'dan)
            fm_spec = seed_module.player_from_record(record, fm_rng, season_year)
            fm_spec.name, fm_spec.age = player.name, age
            fm_spec.nationality = player.nationality or _nation(record.nationality)
            fm_spec.shirt_number, fm_spec.wikidata_id = player.shirt_number, player.qid
            spec = fm_spec
            coverage.fm += 1
        players.append(spec)
    for position, target in zip(fills, targets[len(ordered):], strict=True):
        players.append(seed_module.generate_player_spec(rng, names.make(country), position, target, band,
                                                        data_source=GENERATED_SOURCE))
    # FM mevkisi asgariyi bozduysa (nadir) oyun kurallari FM yoluyla ayni sekilde garantilenir.
    players, added = seed_module.pad_squad(players, rng, names, country)
    coverage.generated += added
    spec = seed_module.ClubSpec(
        name=club["name"],
        reputation=reputation,
        transfer_budget=transfer_budget_for_reputation(reputation),
        formation=seed_module._pick_formation(rng),
        players=players,
        academy_added=added,
        data_source=OPEN_SOURCE,
    )
    return spec, coverage


def build_real_world(
    rng_seed: int,
    squads: SquadData,
    data_dir: Path | str = OPEN_DATA_DIR,
    sample: bool = False,
    data: OpenData | None = None,
    sample_clubs: int = SAMPLE_CLUBS_PER_LEAGUE,
    tiers: Sequence[int] = DEFAULT_TIERS,
    fm_records: Sequence | None = None,
    fm_files: Sequence[str] = (),
    season_year: int = seed_module.DEFAULT_SEASON_YEAR,
    min_confidence: str = "low",
) -> seed_module.WorldSpec:
    """
    GERCEK OYUNCULU acik veri dunyasi (16G).
    min_confidence: Wikidata oyuncusu icin en dusuk guven ("low" = hepsi, varsayilan; "medium" / "high" = bayat
    olabilecek dusuk guvenli kayitlar alinmaz, yerlerini FM ya da uretilmis oyuncu doldurur). Veritabanina dokunmaz. ONAY BAYRAGI (OFM_ALLOW_REAL_PLAYERS=1) sart;
    yoksa OpenDataError. Kulup/lig/itibar iskeleti build_open_world ile aynidir; kulup RNG'si de ayni tohumdan
    (club_seed) gelir. Maskeleme seviyesi 'off' olarak isaretlenir: dunya kisiseldir, paylasilan dunya olamaz.
    fm_records: sahibin FM disa aktarim kayitlari (ham adlar; fm_parser.parse_files(mask_level="off")); None = FM yok.
    FM ONCELIKLI: kulubu dunyamizda olan FM oyunculari once gelir; Wikidata eksikleri tamamlar; kalan yer uretilir.
    """
    if not seed_module.real_players_allowed():
        raise OpenDataError(seed_module.REAL_PLAYERS_OPTIN_ERROR)
    if min_confidence not in CONFIDENCE_RANK:
        raise OpenDataError(f"Geçersiz güven eşiği: {min_confidence!r} (low / medium / high).")
    floor = CONFIDENCE_RANK[min_confidence]
    data = data or load_open_data(data_dir)
    wanted_tiers = {int(tier) for tier in tiers}
    selected = [lg for lg in data.leagues["leagues"] if int(lg.get("tier", 1)) in wanted_tiers]

    rosters: list[tuple[dict, dict[str, int], list[dict]]] = []
    for league in sorted(selected, key=lambda lg: (int(lg.get("tier", 1)), lg.get("code", ""))):
        reputations = league_reputations(league)
        roster = list(league.get("clubs", []))
        if sample:
            roster = sample_roster(roster, reputations, sample_clubs)
        rosters.append((league, reputations, sorted(roster, key=lambda c: c["id"])))
    club_ids = {club["id"] for _lg, _rep, roster in rosters for club in roster}

    fm_matches: dict[str, object] = {}
    fm_only: dict[str, list] = {}
    fm_report = None
    if fm_records is not None:
        fm_matches, fm_only, fm_report = match_fm_records(fm_records, squads, data, club_ids)
        fm_report.files = list(fm_files)

    names = OpenNameFactory(random.Random(rng_seed))
    names.reserve(p.name for club_id in sorted(club_ids) for p in squads.clubs.get(club_id, ()))
    names.reserve(r.name for club_id in sorted(fm_only) for r in fm_only[club_id])
    report = RealPlayersReport(str(squads.path), squads.fetched_at, squads.reference_date.isoformat(), fm=fm_report)
    leagues: list[seed_module.LeagueSpec] = []
    notes: list[str] = [
        data.provenance().replace("tüm oyuncular üretilmiştir", "oyuncu kadroları gerçek (Wikidata), yetenekler üretilmiştir"),
        f"GERÇEK OYUNCULAR: kadro iskeleti {squads.path.name} (Wikidata CC0, {squads.fetched_at}); yalnızca bu "
        f"makinedeki tek koltuklu kariyer içindir (kişisel veri).",
    ]
    if sample:
        notes.append(f"Küçük örnek dünya (--open-sample): her ligde en itibarlı {sample_clubs} kulüp.")
    for league, reputations, roster in rosters:
        country = league.get("country") or ""
        clubs: list[seed_module.ClubSpec] = []
        for club in roster:
            rng = random.Random(club_seed(rng_seed, club["id"]))
            fm_rng = random.Random(club_seed(rng_seed, f"fm|{club['id']}"))
            wikidata, duplicates = drop_fm_duplicates(
                [p for p in squads.clubs.get(club["id"], ()) if CONFIDENCE_RANK.get(p.confidence, 2) <= floor],
                club["id"], fm_only, squads.reference_date)
            if fm_report is not None:
                fm_report.duplicates += [(club["id"], p.name) for p in duplicates]
                fm_report.added += [(club["id"], r.name) for r in select_fm_only(fm_only.get(club["id"], ()))[0]]
            spec, coverage = build_real_club_spec(
                club, reputations[club["id"]], country, rng, names.for_club(rng), wikidata,
                squads.reference_date, fm_matches, fm_rng, season_year, league.get("code", ""),
                fm_only=fm_only.get(club["id"], ()), fm_duplicates=len(duplicates))
            clubs.append(spec)
            report.clubs.append(coverage)
        if len(clubs) < seed_module.FM_MIN_LEAGUE_CLUBS:
            notes.append(f"{league.get('name')}: yalnızca {len(clubs)} kulüp var, lig kurulamadı.")
            continue
        leagues.append(seed_module.LeagueSpec(name=league["name"], country=country, clubs=clubs,
                                              data_source=OPEN_SOURCE))
    if not leagues:
        raise OpenDataError("Açık veriden oynanabilir lig kurulamadı.")

    world = seed_module.WorldSpec(OPEN_SOURCE, leagues, notes)
    world.real_players = report
    real = sum(1 for c in world.clubs for p in c.players if p.data_source in (REAL_SOURCE, FM_SOURCE))
    # Maskeleme KAPALI isaretlenir (kimlik eslemesi): game_state.mask_level = 'off' -> worlds.world_has_real_names.
    world.names_masked = True
    world.mask_summary = seed_module.MaskSummary(
        seed_module.MASK_OFF, fm_players=sum(c.fm + c.fm_only for c in report.clubs), open_leagues=len(leagues),
        open_clubs=len(world.clubs), real_players=real)
    seed_module.add_youth_world(world, rng_seed)
    return world
