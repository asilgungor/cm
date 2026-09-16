"""
seed.py
=======
Veritabanini sifirlar ve baslangic (seed) verisini yazar.

Uretilenler:
    3 lig  ->  12 takim  ->  180 oyuncu  ->  36 fikstur maci

Calistirma:
    python seed.py                 # tablolari drop+create eder ve doldurur
    python seed.py --keep          # tablolari silmeden ustune yazmayi dener
    python seed.py --seed 1234     # farkli bir rastgelelik tohumu
    python seed.py --no-fixtures   # fikstur uretme
    python seed.py --verify-only   # hicbir sey yazmadan mevcut veriyi raporla
    python seed.py --hard-reset    # sema tamamen silinip yeniden kurulur

Tasarim notu:
    Oyuncu yetenekleri rastgele "uydurulmaz"; her takimin bir GUC BANDI
    (orn. Man City 80-90) vardir. 15 oyuncu bu bandin tepesinden tabanina
    dogru dagitilir, sonra mevkiye gore yetenek profili uygulanir ve
    overall_rating bu yeteneklerden AGIRLIKLI ORTALAMA ile geri hesaplanir.
    Boylece "overall 85 ama tum yetenekleri 60" gibi tutarsiz oyuncu olusmaz.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections.abc import Sequence

from sqlalchemy import func, select, text

import database
from database import SessionLocal, engine, session_scope, wait_for_db
from models import Fixture, FixtureStatus, League, Player, Position, Team

# Windows konsolunda Turkce karakterler patlamasin diye
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ===========================================================================
# 1) KADRO YAPISI
# ===========================================================================

# Toplam 15. 2 kaleci: sakatlik/kart mekanigi icin yedek GK sart (kullanici karari, 2. Asama).
SQUAD_COMPOSITION: dict[Position, int] = {
    Position.GK: 2,
    Position.DEF: 4,
    Position.MID: 5,
    Position.FWD: 4,
}

SQUAD_SIZE = sum(SQUAD_COMPOSITION.values())


# ===========================================================================
# 2) YETENEK MODELI
# ===========================================================================

ATTRIBUTES = ("pace", "shooting", "passing", "defending", "dribbling", "goalkeeping")

# overall_rating = bu agirliklarla hesaplanan agirlikli ortalama.
# Her mevki icin agirliklarin toplami 1.0'dir.
POSITION_WEIGHTS: dict[Position, dict[str, float]] = {
    Position.GK:  {"goalkeeping": 0.70, "defending": 0.12, "passing": 0.10,
                   "pace": 0.05, "dribbling": 0.03, "shooting": 0.00},
    Position.DEF: {"defending": 0.50, "pace": 0.18, "passing": 0.15,
                   "dribbling": 0.10, "shooting": 0.07, "goalkeeping": 0.00},
    Position.MID: {"passing": 0.35, "dribbling": 0.22, "defending": 0.18,
                   "pace": 0.13, "shooting": 0.12, "goalkeeping": 0.00},
    Position.FWD: {"shooting": 0.40, "pace": 0.25, "dribbling": 0.22,
                   "passing": 0.10, "defending": 0.03, "goalkeeping": 0.00},
}

# Mevkiye gore yetenek sapmalari. Agirlikli toplamlari ~0 olacak sekilde
# ayarlandi; bu sayede uretilen overall, hedeflenen guce yakin cikar.
POSITION_OFFSETS: dict[Position, dict[str, int]] = {
    Position.GK:  {"goalkeeping": +8, "defending": -20, "passing": -10,
                   "pace": -18, "dribbling": -25, "shooting": -35},
    Position.DEF: {"defending": +6, "pace": +1, "passing": -3,
                   "dribbling": -7, "shooting": -18, "goalkeeping": -45},
    Position.MID: {"passing": +4, "dribbling": +2, "defending": -3,
                   "pace": 0, "shooting": -4, "goalkeeping": -45},
    Position.FWD: {"shooting": +4, "pace": +2, "dribbling": +2,
                   "passing": -8, "defending": -25, "goalkeeping": -45},
}


# ===========================================================================
# 3) DUNYA VERISI
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

# Oyuncu isimleri kurgusaldir: gercek futbolcu isimleri kullanilmaz,
# boylece veri eskimez ve isim/telif sorunu olmaz.
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
}


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

    def make(self, country: str) -> str:
        first_names, last_names = NAME_POOLS[country]
        for _ in range(400):
            name = f"{self._rng.choice(first_names)} {self._rng.choice(last_names)}"
            if name not in self._used:
                self._used.add(name)
                return name
        # Havuz tukendiyse ayirt edici bir son ek ekle
        name = f"{self._rng.choice(first_names)} {self._rng.choice(last_names)} Jr."
        self._used.add(name)
        return name


def build_rating_targets(rng: random.Random, low: int, high: int, count: int) -> list[int]:
    """
    Kadronun guc merdiveni: en iyi oyuncudan en zayifina dogru bandi tarar.
    Yildiz oyuncu, ilk 11 ve yedekler arasinda dogal bir fark olusur.
    """
    if count == 1:
        return [high]
    span = high - low
    targets = []
    for i in range(count):
        base = high - span * (i / (count - 1))
        targets.append(_clamp(base + rng.uniform(-1.2, 1.2), low, high))
    return targets


def generate_player(
    rng: random.Random,
    name: str,
    position: Position,
    target_rating: int,
    band: tuple[int, int],
) -> Player:
    """Hedeflenen guce ve mevkiye uygun, tutarli yetenekleri olan oyuncu uretir."""
    offsets = POSITION_OFFSETS[position]
    weights = POSITION_WEIGHTS[position]

    raw: dict[str, int] = {}
    for attr in ATTRIBUTES:
        value = target_rating + offsets[attr] + rng.randint(-3, 3)
        if attr == "goalkeeping" and position is not Position.GK:
            # Saha oyuncularinin kalecilik degeri dusuk ve dar bir bantta kalir
            raw[attr] = _clamp(value, 5, 35)
        else:
            raw[attr] = _clamp(value, 20, 99)

    overall = sum(weights[attr] * raw[attr] for attr in ATTRIBUTES)
    overall = _clamp(overall, band[0], band[1])

    # Yas: 17-37 arasi, tepe noktasi 26 (gercekci bir kadro yas dagilimi)
    age = _clamp(rng.triangular(17, 37, 26), 17, 37)

    return Player(
        name=name,
        age=age,
        position=position,
        overall_rating=overall,
        pace=raw["pace"],
        shooting=raw["shooting"],
        passing=raw["passing"],
        defending=raw["defending"],
        dribbling=raw["dribbling"],
        goalkeeping=raw["goalkeeping"],
        form=rng.randint(45, 65),
        morale=rng.randint(60, 85),
    )


def generate_squad(
    rng: random.Random,
    names: NameFactory,
    country: str,
    band: tuple[int, int],
) -> list[Player]:
    """Bir takimin 15 kisilik kadrosunu uretir."""
    positions: list[Position] = []
    for position, count in SQUAD_COMPOSITION.items():
        positions.extend([position] * count)

    # Guc merdivenini mevkilere karistirarak dagit; yoksa en iyi oyuncular
    # hep ayni mevkide toplanirdi.
    rng.shuffle(positions)

    targets = build_rating_targets(rng, band[0], band[1], SQUAD_SIZE)

    return [
        generate_player(rng, names.make(country), position, target, band)
        for position, target in zip(positions, targets, strict=True)
    ]


def build_round_robin(team_ids: Sequence[int]) -> list[list[tuple[int, int]]]:
    """
    Cift devreli lig fiksturu (circle / Berger yontemi).

    4 takim -> 3 hafta ilk devre + 3 hafta ikinci devre = 6 hafta, 12 mac.
    Ikinci devrede ev sahipligi ters cevrilir.
    """
    teams = list(team_ids)
    if len(teams) % 2:
        teams.append(-1)  # bay (bos) takim

    half = len(teams) // 2
    rounds: list[list[tuple[int, int]]] = []

    for week in range(len(teams) - 1):
        pairs: list[tuple[int, int]] = []
        for i in range(half):
            home, away = teams[i], teams[len(teams) - 1 - i]
            if home == -1 or away == -1:
                continue
            # Ev/deplasman dengesi icin haftalik siralamayi degistir
            pairs.append((home, away) if week % 2 == 0 else (away, home))
        rounds.append(pairs)
        # Ilk takim sabit, digerleri saat yonunde doner
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]

    second_leg = [[(away, home) for home, away in week] for week in rounds]
    return rounds + second_leg


# ===========================================================================
# 5) SEED AKISI
# ===========================================================================

def seed(rng_seed: int, with_fixtures: bool = True) -> None:
    rng = random.Random(rng_seed)
    names = NameFactory(rng)

    with session_scope() as db:
        for league_row in LEAGUE_DATA:
            league = League(name=league_row["name"], country=league_row["country"])
            db.add(league)

            for team_name, reputation, budget, band in league_row["teams"]:
                team = Team(
                    league=league,
                    name=team_name,
                    budget=budget,
                    reputation=reputation,
                )
                team.players = generate_squad(rng, names, league_row["country"], band)
                db.add(team)

            # Takim id'lerinin atanmasi icin flush (commit degil)
            db.flush()

            if with_fixtures:
                team_ids = [t.id for t in league.teams]
                rng.shuffle(team_ids)
                for week_index, pairs in enumerate(build_round_robin(team_ids), start=1):
                    for home_id, away_id in pairs:
                        db.add(
                            Fixture(
                                league_id=league.id,
                                home_team_id=home_id,
                                away_team_id=away_id,
                                week=week_index,
                                status=FixtureStatus.UNPLAYED,
                            )
                        )


def hard_reset() -> None:
    """Sema tamamen silinip yeniden kurulur. Artik ENUM/tablo kalintisi birakmaz."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    print("[seed] Sema sifirdan olusturuldu (hard reset).")


# ===========================================================================
# 6) DOGRULAMA RAPORU
# ===========================================================================

def verify() -> bool:
    """Veritabanindaki veriyi okuyup ozet rapor basar. Sorun varsa False doner."""
    ok = True
    with SessionLocal() as db:
        league_count = db.scalar(select(func.count()).select_from(League)) or 0
        team_count = db.scalar(select(func.count()).select_from(Team)) or 0
        player_count = db.scalar(select(func.count()).select_from(Player)) or 0
        fixture_count = db.scalar(select(func.count()).select_from(Fixture)) or 0

        print()
        print("=" * 68)
        print(" VERITABANI DOGRULAMA RAPORU")
        print("=" * 68)
        print(f"  Lig     : {league_count}")
        print(f"  Takim   : {team_count}")
        print(f"  Oyuncu  : {player_count}")
        print(f"  Fikstur : {fixture_count}")

        if team_count and player_count % team_count != 0:
            print("  !! UYARI: Oyuncu sayisi takim basina esit dagilmamis.")
            ok = False

        leagues = db.scalars(select(League).order_by(League.name)).all()
        for league in leagues:
            print()
            print(f"  {league.name}  ({league.country})")
            print("  " + "-" * 64)
            print(f"  {'Takim':<20}{'Itibar':>7}{'Kadro Ort.':>12}{'Butce':>14}   En iyi oyuncu")
            for team in sorted(league.teams, key=lambda t: -t.reputation):
                best = max(team.players, key=lambda p: p.overall_rating)
                budget_m = f"{team.budget / 1_000_000:.0f}M EUR"
                print(
                    f"  {team.name:<20}{team.reputation:>7}"
                    f"{team.squad_rating:>12}{budget_m:>14}   "
                    f"{best.name} ({best.position.value} {best.overall_rating})"
                )
                if len(team.players) != SQUAD_SIZE:
                    print(f"     !! UYARI: {team.name} kadrosunda {len(team.players)} oyuncu var.")
                    ok = False

        # Mevki dagilimi kontrolu
        print()
        print("  Mevki dagilimi (tum ligler):")
        rows = db.execute(
            select(Player.position, func.count())
            .group_by(Player.position)
            .order_by(Player.position)
        ).all()
        for position, count in rows:
            expected = SQUAD_COMPOSITION[position] * team_count
            flag = "" if count == expected else f"  !! beklenen {expected}"
            print(f"    {position.value:<5}{count:>5}{flag}")
            if count != expected:
                ok = False

        # Fikstur ornegi
        if fixture_count:
            print()
            print("  Ornek fikstur (1. hafta):")
            first_week = db.scalars(
                select(Fixture).where(Fixture.week == 1).order_by(Fixture.league_id)
            ).all()
            for fx in first_week:
                print(f"    [{fx.league.country:<10}] {fx.home_team.name} - {fx.away_team.name}")

        print()
        print("=" * 68)
        print(" SONUC:", "BASARILI" if ok else "KONTROL GEREKIYOR")
        print("=" * 68)
    return ok


# ===========================================================================
# 7) CLI
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="FM veritabanini sifirla ve doldur.")
    parser.add_argument("--seed", type=int,
                        default=int(os.getenv("SEED_RANDOM_SEED", "2026")),
                        help="Rastgelelik tohumu (ayni tohum = ayni kadrolar).")
    parser.add_argument("--keep", action="store_true",
                        help="Tablolari drop etme, sadece eksikleri olustur.")
    parser.add_argument("--hard-reset", action="store_true",
                        help="'public' semasini komple silip yeniden kur.")
    parser.add_argument("--no-fixtures", action="store_true",
                        help="Fikstur uretme.")
    parser.add_argument("--verify-only", action="store_true",
                        help="Hicbir sey yazma, sadece mevcut veriyi raporla.")
    args = parser.parse_args()

    print(f"[seed] Hedef veritabani: {database.masked_url()}")

    if not wait_for_db():
        print()
        print("[seed] HATA: Veritabanina baglanilamadi.")
        print("       'docker compose ps' ile container'in 'healthy' oldugunu kontrol et.")
        return 1

    if args.verify_only:
        return 0 if verify() else 1

    if args.hard_reset:
        hard_reset()
        database.init_db()
    elif args.keep:
        database.init_db()
        print("[seed] Tablolar korundu (--keep).")
    else:
        database.reset_db()
        print("[seed] Tablolar silinip yeniden olusturuldu.")

    print(f"[seed] Veri uretiliyor (tohum={args.seed})...")
    seed(rng_seed=args.seed, with_fixtures=not args.no_fixtures)
    print("[seed] Yazma tamamlandi.")

    return 0 if verify() else 1


if __name__ == "__main__":
    sys.exit(main())
