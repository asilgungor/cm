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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import seed as seed_module
from finance import transfer_budget_for_reputation
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
