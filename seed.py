"""
seed.py
=======
Veritabanini sifirlar ve baslangic dunyasini yazar.

Iki veri kaynagi (6. Asama):
    fm        : data/fm/ klasorundeki Football Manager disa aktarimlari (fm_parser.py)
                -> gercek oyuncu adlari, yaslar, kulupler, 1-20 FM ozellikleri
    synthetic : kurgusal 3 lig / 12 takim / 180 oyuncu (testler ve FM verisi yokken)

Varsayilan 'auto': data/fm/ icinde disa aktarim varsa FM, yoksa sentetik.

Calistirma:
    python seed.py                            # auto
    python seed.py --source fm                # data/fm/ zorunlu, yoksa hata
    python seed.py --fm dosya1.html dosya2.csv
    python seed.py --fm-sample                # paketteki KURGUSAL ornekle FM akisini dene
    python seed.py --source synthetic
    python seed.py --verify-only | --hard-reset | --keep | --seed N | --no-fixtures

Akis:
    kaynak -> WorldSpec (saf veri, DB bilmez) -> write_world(db) -> dogrulama raporu
Bu ayrim sayesinde FM donusumu veritabani olmadan test edilebilir.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select, text

import database
import fm_parser
import staff as staff_rules
from club_directory import canonical_league, lookup_club, reputation_from_strength
from database import SessionLocal, engine, session_scope, wait_for_db
from finance import (
    DEFAULT_WAGE_HEADROOM,
    expected_wage,
    market_value,
    transfer_budget_for_reputation,
)
from models import (
    Fixture,
    FixtureStatus,
    GameState,
    League,
    Player,
    Position,
    SquadRole,
    Staff,
    StaffRole,
    Team,
)
from ratings import ENGINE_ATTRIBUTES, POSITION_OFFSETS, POSITION_WEIGHTS, rate_fm_player
from reputation import START_REPUTATION
from schedule import build_round_robin

# Windows konsolunda Turkce karakterler patlamasin diye
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
FM_DATA_DIR = ROOT / "data" / "fm"
DEFAULT_SEASON_YEAR = 2026


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

# Geriye donuk uyumluluk (baska moduller seed uzerinden erisiyordu)
__all__ = ["ATTRIBUTES", "POSITION_WEIGHTS", "POSITION_OFFSETS", "SQUAD_COMPOSITION", "SQUAD_SIZE"]
ATTRIBUTES = ENGINE_ATTRIBUTES


# ===========================================================================
# 2) SENTETIK DUNYA VERISI
# ===========================================================================

# (takim adi, itibar 1-100, butce EUR, guc bandi (min, max))
LEAGUE_DATA: list[dict] = [
    {
        "name": "Trendyol Super Lig",
        "country": "Turkiye",
        "teams": [
            ("Galatasaray",  78, 45_000_000, (73, 83)),
            ("Fenerbahce",   77, 42_000_000, (73, 83)),
            ("Besiktas",     75, 32_000_000, (71, 81)),
            ("Trabzonspor",  73, 25_000_000, (70, 80)),
        ],
    },
    {
        "name": "Premier League",
        "country": "Ingiltere",
        "teams": [
            ("Manchester City",   92, 180_000_000, (80, 90)),
            ("Liverpool",         90, 150_000_000, (78, 89)),
            ("Arsenal",           89, 140_000_000, (78, 89)),
            ("Manchester United", 87, 130_000_000, (76, 87)),
        ],
    },
    {
        "name": "Serie A",
        "country": "Italya",
        "teams": [
            ("Inter",     86, 95_000_000, (77, 87)),
            ("Juventus",  86, 92_000_000, (76, 87)),
            ("Milan",     84, 85_000_000, (76, 86)),
            ("Napoli",    83, 80_000_000, (75, 86)),
        ],
    },
]

# Kurgusal isim havuzlari (sentetik oyuncular ve FM kadro tamamlama icin)
NAME_POOLS: dict[str, tuple[Sequence[str], Sequence[str]]] = {
    "Turkiye": (
        ("Ahmet", "Mehmet", "Mustafa", "Emre", "Burak", "Kerem", "Arda", "Serdar",
         "Hakan", "Volkan", "Caner", "Cengiz", "Okan", "Oguz", "Yusuf", "Baris",
         "Ugur", "Tolga", "Selcuk", "Kaan", "Efe", "Berkay", "Halil", "Ilhan", "Dogan"),
        ("Yilmaz", "Kaya", "Demir", "Sahin", "Celik", "Yildiz", "Yildirim", "Ozturk",
         "Aydin", "Ozdemir", "Arslan", "Dogan", "Kilic", "Aslan", "Cetin", "Kara",
         "Koc", "Kurt", "Ozkan", "Simsek", "Polat", "Tas", "Bulut", "Gunes", "Erdem"),
    ),
    "Ingiltere": (
        ("James", "Harry", "Jack", "Oliver", "Charlie", "George", "Thomas", "Jacob",
         "Alfie", "Lewis", "Callum", "Ryan", "Connor", "Dylan", "Kyle", "Marcus",
         "Nathan", "Aaron", "Reece", "Declan", "Mason", "Ethan", "Jordan", "Liam", "Toby"),
        ("Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies",
         "Robinson", "Wright", "Thompson", "Evans", "Walker", "White", "Roberts",
         "Green", "Hall", "Wood", "Harris", "Clarke", "Baker", "Turner", "Hughes",
         "Edwards", "Mitchell"),
    ),
    "Italya": (
        ("Lorenzo", "Matteo", "Alessandro", "Andrea", "Francesco", "Marco", "Davide",
         "Simone", "Luca", "Federico", "Giuseppe", "Antonio", "Riccardo", "Stefano",
         "Gabriele", "Nicolo", "Emanuele", "Tommaso", "Giacomo", "Daniele", "Pietro",
         "Fabio", "Cristian", "Michele", "Salvatore"),
        ("Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Romano", "Colombo",
         "Ricci", "Marino", "Greco", "Bruno", "Gallo", "Conti", "De Luca", "Mancini",
         "Costa", "Giordano", "Rizzo", "Lombardi", "Moretti", "Barbieri", "Fontana",
         "Santoro", "Mariani", "Rinaldi"),
    ),
    "Ispanya": (
        ("Alvaro", "Diego", "Hugo", "Ivan", "Jorge", "Mario", "Pablo", "Raul", "Sergio", "Victor"),
        ("Alonso", "Blanco", "Castro", "Delgado", "Iglesias", "Molina", "Navarro", "Ortega",
         "Rubio", "Vidal"),
    ),
    "Almanya": (
        ("Ben", "David", "Fabian", "Jonas", "Leon", "Lukas", "Moritz", "Paul", "Tim", "Tobias"),
        ("Bauer", "Fischer", "Hoffmann", "Keller", "Koch", "Richter", "Schmitt", "Wagner",
         "Weber", "Wolf"),
    ),
    "Fransa": (
        ("Antoine", "Baptiste", "Clement", "Hugo", "Julien", "Louis", "Mathis", "Nathan", "Theo", "Yanis"),
        ("Bernard", "Dubois", "Fontaine", "Girard", "Lambert", "Laurent", "Leroy", "Moreau",
         "Petit", "Roux"),
    ),
}

# Lig ulkesi -> isim havuzu anahtari
COUNTRY_POOL = {
    "Türkiye": "Turkiye", "Turkiye": "Turkiye",
    "İngiltere": "Ingiltere", "Ingiltere": "Ingiltere",
    "İtalya": "Italya", "Italya": "Italya",
    "İspanya": "Ispanya", "Almanya": "Almanya", "Fransa": "Fransa",
}

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


@dataclass
class ClubSpec:
    name: str
    reputation: int
    transfer_budget: int
    formation: str
    players: list[PlayerSpec]
    academy_added: int = 0


@dataclass
class LeagueSpec:
    name: str
    country: str
    clubs: list[ClubSpec]


@dataclass
class WorldSpec:
    source: str                             # "synthetic" / "fm"
    leagues: list[LeagueSpec]
    notes: list[str] = field(default_factory=list)
    parse_report: fm_parser.ParseReport | None = None

    @property
    def clubs(self) -> list[ClubSpec]:
        return [c for league in self.leagues for c in league.clubs]

    @property
    def player_count(self) -> int:
        return sum(len(c.players) for c in self.clubs)


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
    """Kurgusal dunya: 3 lig, 12 takim, takim basina tam 15 oyuncu."""
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
    return WorldSpec("synthetic", leagues)


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


def trim_squad(players: list[PlayerSpec], limit: int = FM_MAX_SQUAD) -> list[PlayerSpec]:
    """Kalabalik kadroyu en iyi `limit` oyuncuya indirir; en az 3 kaleci korunur."""
    if len(players) <= limit:
        return players
    ranked = sorted(players, key=lambda p: -p.overall)
    keepers = [p for p in ranked if p.position is Position.GK][:3]
    others = [p for p in ranked if p.position is not Position.GK][: limit - len(keepers)]
    return keepers + others


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


def build_fm_world(
    report: fm_parser.ParseReport,
    rng_seed: int,
    season_year: int = DEFAULT_SEASON_YEAR,
) -> WorldSpec:
    """Parser raporundan oynanabilir dunya kurar. Veritabanina dokunmaz."""
    rng = random.Random(rng_seed)
    names = NameFactory(random.Random(rng_seed + 5))
    names.reserve(p.name for p in report.players)
    formation_rng = random.Random(rng_seed + 1)
    notes: list[str] = []

    grouped: dict[str, dict] = {}
    no_club = 0
    unplaced: Counter[str] = Counter()
    for record in report.players:
        if not record.club:
            no_club += 1
            continue
        info = lookup_club(record.club)
        if info is not None:
            key, league = info.name, (info.league, info.country)
        else:
            league = canonical_league(record.league)
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
        players = trim_squad([player_from_record(r, rng, season_year) for r in entry["records"]])
        players, academy = pad_squad(players, rng, names, country)
        top = sorted((p.overall for p in players), reverse=True)[:11]
        reputation = entry["info"].reputation if entry["info"] else reputation_from_strength(sum(top) / len(top))
        if academy:
            notes.append(f"{club_name}: dosyada {len(players) - academy} oyuncu vardı, "
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

    return WorldSpec("fm", leagues, notes, parse_report=report)


def resolve_world(
    rng_seed: int,
    source: str = "auto",
    fm_paths: Sequence[str | Path] | None = None,
    include_samples: bool = False,
    season_year: int = DEFAULT_SEASON_YEAR,
    fm_dir: Path = FM_DATA_DIR,
) -> WorldSpec:
    """Kaynagi secer ve dunya tanimini dondurur."""
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

    report = fm_parser.parse_files(paths)
    world = build_fm_world(report, rng_seed, season_year)
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
        contract_years=spec.contract_years,
        market_value=spec.market_value if spec.market_value is not None
        else market_value(spec.overall, spec.age, spec.position),
        nationality=spec.nationality,
        data_source=spec.data_source,
        fm_uid=spec.fm_uid,
        current_ability=spec.current_ability,
        potential_ability=spec.potential_ability,
        fm_attributes=spec.fm_attributes,
    )


def write_world(db, world: WorldSpec, rng_seed: int, with_fixtures: bool = True) -> None:
    """Dunya tanimini BOS tablolara yazar. Commit cagirana aittir."""
    staff_rng = random.Random(rng_seed + 2)
    staff_names = StaffNameFactory(staff_rng)
    fixture_rng = random.Random(rng_seed + 3)

    db.add(GameState(id=1, season=1, current_week=1, user_team_id=None,
                     manager_reputation=START_REPUTATION))

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
            )
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
            team.wage_budget = int(round(wage_bill * DEFAULT_WAGE_HEADROOM / 1000) * 1000)
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


def seed(
    rng_seed: int,
    with_fixtures: bool = True,
    source: str = "auto",
    fm_paths: Sequence[str | Path] | None = None,
    include_samples: bool = False,
    season_year: int = DEFAULT_SEASON_YEAR,
) -> WorldSpec:
    """Dunyayi secer ve yazar (tablolarin bos oldugu varsayilir)."""
    world = resolve_world(rng_seed, source, fm_paths, include_samples, season_year)
    with session_scope() as db:
        write_world(db, world, rng_seed, with_fixtures)
    return world


def hard_reset() -> None:
    """Sema tamamen silinip yeniden kurulur. ENUM/tablo kalintisi birakmaz."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    print("[seed] Sema sifirdan olusturuldu (hard reset).")


# ===========================================================================
# 7) DOGRULAMA RAPORU
# ===========================================================================

def verify() -> bool:
    """Veritabanindaki veriyi okuyup ozet rapor basar. Sorun varsa False doner."""
    ok = True
    with SessionLocal() as db:
        league_count = db.scalar(select(func.count()).select_from(League)) or 0
        team_count = db.scalar(select(func.count()).select_from(Team)) or 0
        player_count = db.scalar(select(func.count()).select_from(Player)) or 0
        fixture_count = db.scalar(select(func.count()).select_from(Fixture)) or 0
        staff_count = db.scalar(select(func.count()).select_from(Staff)) or 0
        free_staff = db.scalar(select(func.count()).select_from(Staff).where(Staff.team_id.is_(None))) or 0
        sources = dict(db.execute(select(Player.data_source, func.count()).group_by(Player.data_source)).all())
        fm_world = sources.get("fm", 0) > 0

        print()
        print("=" * 72)
        print(" VERITABANI DOGRULAMA RAPORU")
        print("=" * 72)
        print(f"  Kaynak  : {'FM verisi' if fm_world else 'sentetik'}  "
              f"({', '.join(f'{k}: {v}' for k, v in sorted(sources.items()))})")
        print(f"  Lig     : {league_count}")
        print(f"  Takim   : {team_count}")
        print(f"  Oyuncu  : {player_count}")
        print(f"  Fikstur : {fixture_count}")
        print(f"  Personel: {staff_count} ({free_staff} boşta)")
        state = db.get(GameState, 1)
        if state is not None:
            print(f"  Durum   : sezon {state.season}, hafta {state.current_week}, "
                  f"menajer tanınırlığı {state.manager_reputation:.1f}/20")

        for league in db.scalars(select(League).order_by(League.name)).all():
            print()
            print(f"  {league.name}  ({league.country})")
            print("  " + "-" * 68)
            print(f"  {'Takim':<22}{'Itibar':>7}{'Kadro':>6}{'Ort.':>6}{'Transfer':>10}"
                  f"{'Maas/hf':>10}{'Kull.':>7}   En iyi oyuncu")
            for team in sorted(league.teams, key=lambda t: -t.reputation):
                best = max(team.players, key=lambda p: p.overall_rating)
                usage = f"%{100 * team.wage_bill / team.wage_budget:.0f}" if team.wage_budget else "-"
                print(
                    f"  {team.name:<22}{team.reputation:>7}{len(team.players):>6}{team.squad_rating:>6}"
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
                if fm_world:
                    if len(team.players) < FM_MIN_SQUAD or keepers < 2:
                        print(f"     !! UYARI: {team.name} kadrosu oynanamaz ({len(team.players)} oyuncu, {keepers} GK).")
                        ok = False
                elif len(team.players) != SQUAD_SIZE:
                    print(f"     !! UYARI: {team.name} kadrosunda {len(team.players)} oyuncu var.")
                    ok = False

        if not fm_world:
            print()
            print("  Mevki dagilimi (tum ligler):")
            rows = db.execute(
                select(Player.position, func.count()).group_by(Player.position).order_by(Player.position)
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

def main() -> int:
    parser = argparse.ArgumentParser(description="Veritabanını sıfırla ve dünyayı kur (FM verisi veya sentetik).")
    parser.add_argument("--seed", type=int, default=int(os.getenv("SEED_RANDOM_SEED", "2026")),
                        help="Rastgelelik tohumu (aynı tohum = aynı dünya).")
    parser.add_argument("--source", choices=("auto", "fm", "synthetic"), default="auto",
                        help="auto: data/fm/ doluysa FM, değilse sentetik.")
    parser.add_argument("--fm", nargs="+", metavar="DOSYA", help="Belirli FM dışa aktarım dosyaları.")
    parser.add_argument("--fm-sample", action="store_true",
                        help="data/fm/sample_* kurgusal örnek dosyalarıyla FM akışını dene.")
    parser.add_argument("--season-year", type=int, default=DEFAULT_SEASON_YEAR,
                        help="Sözleşme bitiş yılından kalan süre hesabı için başlangıç yılı.")
    parser.add_argument("--keep", action="store_true", help="Tabloları drop etme.")
    parser.add_argument("--hard-reset", action="store_true", help="'public' şemasını komple silip yeniden kur.")
    parser.add_argument("--no-fixtures", action="store_true", help="Fikstür üretme.")
    parser.add_argument("--verify-only", action="store_true", help="Hiçbir şey yazma, sadece raporla.")
    args = parser.parse_args()

    print(f"[seed] Hedef veritabani: {database.masked_url()}")
    if not wait_for_db():
        print("\n[seed] HATA: Veritabanina baglanilamadi. 'docker compose ps' ile kontrol et.")
        return 1
    if args.verify_only:
        return 0 if verify() else 1

    # Once dunyayi kur (veritabanina dokunmadan): hata varsa mevcut veriyi silmeden cik
    try:
        world = resolve_world(
            args.seed,
            source="fm" if args.fm_sample else args.source,
            fm_paths=args.fm,
            include_samples=args.fm_sample,
            season_year=args.season_year,
        )
    except SeedError as exc:
        print(f"\n[seed] HATA: {exc}")
        print("[seed] Veritabanına dokunulmadı.")
        return 1

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

    if args.hard_reset:
        hard_reset()
        database.init_db()
    elif args.keep:
        database.init_db()
        print("[seed] Tablolar korundu (--keep).")
    else:
        database.reset_db()
        print("[seed] Tablolar silinip yeniden olusturuldu.")

    label = "FM verisi" if world.source == "fm" else "sentetik"
    print(f"[seed] {label} yazılıyor: {len(world.leagues)} lig, {len(world.clubs)} kulüp, "
          f"{world.player_count} oyuncu (tohum={args.seed})...")
    with session_scope() as db:
        write_world(db, world, args.seed, with_fixtures=not args.no_fixtures)
    print("[seed] Yazma tamamlandi.")
    return 0 if verify() else 1


if __name__ == "__main__":
    sys.exit(main())
