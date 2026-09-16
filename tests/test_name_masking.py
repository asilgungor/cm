"""
Isim maskeleme ara yazilimi testleri (8. Asama): name_masking, club_directory maskeleri,
fm_parser'in maskeli okumasi, seed'in mask_world / validate_world adimlari.

DB'siz testler CM_TEST_NO_DB=1 ile de calisir; en sondaki entegrasyon testi test
veritabanindaki sentetik dunyada hicbir gercek kulup/lig adi olmadigini dogrular.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fm_parser  # noqa: E402
import name_masking  # noqa: E402
import seed  # noqa: E402
from club_directory import (  # noqa: E402
    MASKED_LEAGUES,
    canonical_league,
    known_clubs,
    lookup_club,
    plain_key,
    split_name,
)
from models import Position  # noqa: E402
from name_masking import (  # noqa: E402
    DEFAULT_MASK_LEVEL,
    MASK_LEVELS,
    build_club_mask_map,
    build_player_mask_map,
    find_leaks,
    mask_club_name,
    mask_league_name,
    mask_level_from_env,
    mask_player_name,
    resolve_masked_club,
)

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "fm" / "sample_fm_export.html"

REAL_CLUB_KEYS = {plain_key(n) for c in known_clubs() for n in (c.name, *c.aliases)}
REAL_LEAGUE_NAMES = [*MASKED_LEAGUES, "Super Lig", "La Liga", "English Premier League", "1. Bundesliga",
                     "Serie A TIM", "Ligue 1 Uber Eats"]


# ===========================================================================
# 1) Rehber maskeleri
# ===========================================================================

USER_DICTATED = {
    "Galatasaray": "Istanbul Lions", "Fenerbahçe": "Kadıköy Canaries", "Beşiktaş": "Bosphorus Eagles",
    "Trabzonspor": "Karadeniz Storm", "Başakşehir": "Istanbul Owls", "Manchester City": "Manchester Blue",
}


def test_user_dictated_club_masks_are_exact():
    for real, masked in USER_DICTATED.items():
        assert lookup_club(real).masked == masked


def test_directory_masks_are_unique_nonempty_and_never_real():
    clubs = known_clubs()
    masks = [c.masked for c in clubs]
    assert all(m.strip() for m in masks)
    assert len({plain_key(m) for m in masks}) == len(masks)
    real_cores = {split_name(n)[0] for c in clubs for n in (c.name, *c.aliases)}
    for club in clubs:
        assert plain_key(club.masked) not in REAL_CLUB_KEYS, club.name
        assert split_name(club.masked)[0] not in real_cores, club.name
    assert len(set(MASKED_LEAGUES.values())) == len(MASKED_LEAGUES)


def test_lookup_and_canonical_league_accept_masked_names():
    for club in known_clubs():
        assert lookup_club(club.masked) is club
        assert lookup_club(club.masked.upper()) is club
    assert lookup_club("Kadikoy Canaries").name == "Fenerbahçe"              # aksansiz yazim
    for real, masked in MASKED_LEAGUES.items():
        assert canonical_league(masked) == canonical_league(real)
    assert canonical_league("turkiye elit ligi") == ("Trendyol Süper Lig", "Türkiye")
    assert lookup_club("Istanbul") is None and lookup_club("Lions") is None  # strict eslesme korunur


@pytest.mark.parametrize("query,expected", [
    ("Galatasaray", "Istanbul Lions"), ("galatasaray sk", "Istanbul Lions"), ("Istanbul Lions", "Istanbul Lions"),
    ("Fenerbahce", "Kadıköy Canaries"), ("FC Bayern München", "München Roten"), ("Man Utd", "Manchester Devils"),
    ("PSG", "Paris Rouge-Bleu"), ("Juventus FC", "Torino Bianconeri"), ("Kuzey Yıldızı SK", None), ("", None),
])
def test_resolve_masked_club(query, expected):
    assert resolve_masked_club(query) == expected


def test_mask_club_name_directory_is_idempotent():
    for club in known_clubs():
        for form in (club.name, *club.aliases, club.masked):
            assert mask_club_name(form) == club.masked
    assert mask_club_name(mask_club_name("Galatasaray")) == "Istanbul Lions"


@pytest.mark.parametrize("name", [
    "Kuzey Yıldızı SK", "Paris FC", "Barcelona SC", "Bristol City", "Real Betis", "Club Brugge KV",
    "Hamburger SV", "Göztepe", "Bodø/Glimt", "Ruma", "1907", "FC",
])
def test_unknown_club_masks_differ_and_never_hit_directory(name):
    masked = mask_club_name(name)
    assert masked.strip() and plain_key(masked) != plain_key(name)
    assert mask_club_name(name) == masked                                   # deterministik
    assert lookup_club(masked) is None and not find_leaks([masked])
    assert not any(plain_key(t) in {"fc", "sk", "sv"} for t in masked.split())


def test_club_mask_map_keeps_different_clubs_apart():
    mapping = build_club_mask_map(["Kuzey SK", "Kuzey FK", "Kuzey", "KUZEY SK", "Galatasaray"])
    assert mapping["Kuzey SK"] == mapping["KUZEY SK"]                       # ayni kulup, ayni maske
    assert len({mapping["Kuzey SK"], mapping["Kuzey FK"], mapping["Kuzey"]}) == 3
    assert mapping["Galatasaray"] == "Istanbul Lions"


def test_league_masks():
    for real, masked in MASKED_LEAGUES.items():
        assert mask_league_name(real) == masked and mask_league_name(masked) == masked
    assert mask_league_name("Turkish Super Lig") == "Türkiye Elit Ligi"
    for unknown in ("Eredivisie", "Russian Premier League", "2. Bundesliga", "LaLiga 2", "Ligue 2"):
        masked = mask_league_name(unknown)
        assert masked.strip() and plain_key(masked) != plain_key(unknown)
        assert canonical_league(masked)[1] == "Diğer" and not find_leaks([masked])
        assert mask_league_name(unknown) == masked
    assert mask_league_name("") == "" and mask_league_name("  ") == "  "


# ===========================================================================
# 2) Oyuncu adlari
# ===========================================================================

def test_user_examples():
    assert mask_player_name("Erling Haaland") == "E. Harland"
    assert mask_player_name("Kylian Mbappé") == "K. Mbeppe"


# Cogunlukla kurgusal adlar: farkli bicimler (on ek, tire, tek ad, Turkce/Iskandinav harf, kisa soyad)
NAME_SHAPES = [
    "Egemen Kalaycıoğlu", "Batuhan Öztoprak", "Görkem Çakırtaş", "İlkay Gürsoylu", "Yiğit Bayraktaroğlu",
    "Óscar Monteagudo", "Íñigo Irazoqui", "Unai Echeverría", "Rubén Arrieta", "Lennart Lindemann",
    "Bastian Brückner", "Konstantin Oberhauser", "Jannik Wendlandt", "Sindre Ødegård", "Halvor Sørlie",
    "Åsmund Bækkelund", "Mads Højgaard", "Tomas van der Berg", "Pieter van Dijkstra", "Luca di Marzio",
    "Rafael dos Anjos", "Jean-Baptiste Morel-Lacroix", "Karim Al-Harbi", "Sami bin Nasser", "Yusuf el Amrani",
    "Zé", "Teodoro", "Pelinho", "Ko", "Tae-Hwan Oh", "Jin Xu", "Mateo Li", "Amadou Ba", "Ole Ek",
    "  Deniz    Aksoy  ", "MARKO VUKIC", "Neymarinho Jr.", "O'Brien Kells", "De La Vega", "Ömer Öztürk",
    "Léo Beaumont", "Kasper Hjulmand-Kragh",
]


@pytest.mark.parametrize("level", MASK_LEVELS)
def test_name_shapes_are_never_unchanged_or_empty(level):
    for name in NAME_SHAPES:
        masked = mask_player_name(name, level)
        assert masked.strip(), name
        assert plain_key(masked) != plain_key(name), (name, masked)
        assert masked == mask_player_name(name, level)                    # deterministik
        assert "  " not in masked and masked == masked.strip()


def test_light_mask_shape_rules():
    assert mask_player_name("  Erling   Haaland ") == "E. Harland"          # fazla bosluk
    assert mask_player_name("E. Haaland") == "E. Harland"                   # zaten bas harf
    assert mask_player_name("HAALAND") == "HARLAND"                         # tek ad, buyuk harf

    for name in NAME_SHAPES:
        tokens, masked = name.split(), mask_player_name(name).split()
        if name == "Neymarinho Jr.":
            continue                                                        # ayri testte
        if len(tokens) >= 2 and plain_key(tokens[0]) not in name_masking.PARTICLES:
            initial = name_masking.strip_soft_accents(tokens[0])[0].upper()
            assert masked[0].startswith(initial + "."), (name, masked)     # ilk isim bas harfe iner
        assert masked[-1][0] == tokens[-1][0], (name, masked)              # soyadin ilk harfi korunur


def test_particles_are_kept_unchanged():
    assert mask_player_name("Tomas van der Berg").split()[1:3] == ["van", "der"]
    assert mask_player_name("Luca di Marzio").split()[1] == "di"
    assert mask_player_name("Rafael dos Anjos").split()[1] == "dos"
    assert mask_player_name("Yusuf el Amrani").split()[1] == "el"
    masked = mask_player_name("De La Vega")                                 # on ekle baslayan: bas harf yok
    assert masked.startswith("De La ") and masked != "De La Vega"
    assert mask_player_name("Karim Al-Harbi").split()[1].startswith("Al-")


def test_hyphenated_and_single_names():
    masked = mask_player_name("Jean-Baptiste Morel-Lacroix")
    assert masked.startswith("J.-B. ")
    left, right = masked.split(" ")[1].split("-")
    assert left != "Morel" and left[0] == "M" and right[0] == "L"
    single = mask_player_name("Teodoro")
    assert "." not in single and single[0] == "T" and single != "Teodoro"
    assert mask_player_name("Neymarinho Jr.").endswith(" Jr.")              # sonek korunur, ad degisir
    assert not mask_player_name("Neymarinho Jr.").startswith("N. ")


def test_turkish_and_nordic_letters_survive():
    assert mask_player_name("Egemen Kalaycıoğlu") == "E. Kelaycıoğlu"
    assert "ı" in mask_player_name("Görkem Çakırtaş") and "ş" in mask_player_name("Görkem Çakırtaş")
    assert mask_player_name("İlkay Gürsoylu").startswith("İ. G")
    assert mask_player_name("Sindre Ødegård").split()[1][0] == "Ø"
    assert mask_player_name("Ömer Öztürk").split()[1][0] == "Ö"


def test_short_surnames_and_no_broken_consonant_clusters():
    for name in ("Ko", "Jin Xu", "Mateo Li", "Amadou Ba", "Ole Ek", "Tae-Hwan Oh"):
        masked = mask_player_name(name)
        assert plain_key(masked) != plain_key(name) and len(masked.split()[-1]) >= 2
    rng = random.Random(8)
    letters = "abcdefghijklmnoprstuvyzçğıöşüøå"
    skip = name_masking.PARTICLES | {"jr", "sr", "ii", "iii", "iv"}
    for _ in range(2000):
        word = "".join(rng.choice(letters) for _ in range(rng.randint(2, 9))).capitalize()
        if plain_key(word) in skip:
            continue
        name = f"Test {word}"
        new = mask_player_name(name).split()[-1]
        assert plain_key(new) != plain_key(word)
        # yeni uclu harf ya da uzayan (4+) unsuz kumesi olusmaz
        assert name_masking._acceptable(new.lower(), word.lower()) or new.lower().startswith(word.lower()), (word, new)


def test_strong_level_is_fictional_and_deterministic():
    for name in ("Erling Haaland", "Kylian Mbappé", "Egemen Kalaycıoğlu"):
        strong = mask_player_name(name, "strong")
        assert strong == mask_player_name(name, "strong")
        assert plain_key(strong) not in {plain_key(name), plain_key(mask_player_name(name))}
        assert len(strong.split()) == 2 and not strong.split()[0].endswith(".")
    assert mask_player_name("Egemen Kalaycıoğlu", "strong", nationality="TUR") != \
        mask_player_name("Egemen Kalaycıoğlu", "strong", nationality="ESP")
    assert mask_player_name("Иван Иванов").strip()                          # Latin disi ad -> kurgusal
    with pytest.raises(ValueError):
        mask_player_name("Erling Haaland", "off")


def test_player_mask_map_avoids_collisions_and_original_names():
    mapping = build_player_mask_map(["Lucas Hernández", "Luis Hernández", "LUIS HERNÁNDEZ",
                                     "E. Harland", "Erling Haaland"])
    assert mapping["Luis Hernández"] == mapping["LUIS HERNÁNDEZ"] == "L. Hirnandez"
    assert len({mapping["Lucas Hernández"], mapping["Luis Hernández"]}) == 2
    assert plain_key(mapping["Erling Haaland"]) != "e harland"             # baska oyuncunun ozgun adi
    assert all(plain_key(v) != plain_key(k) for k, v in mapping.items())


def test_mask_level_from_env(monkeypatch):
    monkeypatch.delenv("SEED_NAME_MASKING", raising=False)
    assert mask_level_from_env() == DEFAULT_MASK_LEVEL == "light"
    monkeypatch.setenv("SEED_NAME_MASKING", "STRONG")
    assert mask_level_from_env() == "strong"
    for bad in ("off", "", "hafif"):
        monkeypatch.setenv("SEED_NAME_MASKING", bad)
        assert mask_level_from_env() == "light"


# ===========================================================================
# 3) Sizinti denetimi
# ===========================================================================

def test_find_leaks_exact_semantics():
    names = ["Galatasaray", "Istanbul Lions", "Provence Phocéens", "premier-league", "İngiltere Elit Ligi",
             "Juventus FC", "Juventus Stadium", "Arsenal de Sarandí", "Istanbul Lions FC", "Man Utd", None]
    assert find_leaks(names) == ["Galatasaray", "premier-league", "Juventus FC", "Man Utd"]
    assert find_leaks(REAL_LEAGUE_NAMES[:6]) == REAL_LEAGUE_NAMES[:6]
    assert find_leaks(MASKED_LEAGUES.values()) == []
    assert find_leaks(c.masked for c in known_clubs()) == []


# ===========================================================================
# 4) Parser ve dunya kurulumu
# ===========================================================================

def test_parsing_sample_masks_everything():
    raw = fm_parser.parse_files([SAMPLE], mask_names=False)
    report = fm_parser.parse_files([SAMPLE])
    assert report.masked and report.mask_level == "light" and not raw.masked
    assert report.masked_players == len(report.players) == len(raw.players) == 49
    assert report.masked_clubs == 7 and "maskeli" in report.summary()

    clubs = {p.club for p in report.players}
    assert clubs == {"Istanbul Lions", "Kadıköy Canaries", "Madrid Blancos", "Catalonia Blaugrana",
                     "München Roten", "Ruhr Schwarzgelb", mask_club_name("Kuzey Yıldızı SK")}
    assert find_leaks(clubs) == [] and "Kuzey Yıldızı SK" not in clubs
    raw_names = {plain_key(p.name) for p in raw.players}
    for before, after in zip(raw.players, report.players, strict=True):
        assert plain_key(after.name) not in raw_names, (before.name, after.name)
        assert after.name.split()[0] == before.name[0].upper().replace("Ó", "O").replace("Í", "I") + "."
        assert (after.age, after.position, after.fm_attributes) == (before.age, before.position, before.fm_attributes)

    strong = fm_parser.parse_files([SAMPLE], mask_level="strong")
    assert strong.mask_level == "strong"
    assert {p.name for p in strong.players}.isdisjoint({p.name for p in report.players})


def test_resolve_world_synthetic_has_masked_names_only():
    world = seed.resolve_world(2026, source="synthetic")
    assert (len(world.leagues), len(world.clubs), world.player_count) == (6, 24, 360)
    names = [lg.name for lg in world.leagues] + [c.name for c in world.clubs]
    assert find_leaks(names) == []
    assert {lg.name for lg in world.leagues} == set(MASKED_LEAGUES.values())
    assert {lg.country for lg in world.leagues} == {"Türkiye", "İngiltere", "İspanya", "Almanya", "İtalya", "Fransa"}
    assert all(lookup_club(c.name) is not None and lookup_club(c.name).masked == c.name for c in world.clubs)
    assert world.names_masked and world.mask_summary.renamed_clubs == 0      # zaten maskeli: idempotent
    for club in world.clubs:
        assert len(club.players) == seed.SQUAD_SIZE
        assert all(p.data_source == "synthetic" for p in club.players)


def test_elite_clubs_get_huge_two_line_budgets():
    assert seed.wage_headroom_for(95) > seed.wage_headroom_for(89) > 1
    world = seed.build_synthetic_world(2026)
    elite = [c for c in world.clubs if c.reputation >= seed.ELITE_REPUTATION]
    assert {c.name for c in elite} == {"Manchester Blue", "Merseyside Reds", "Madrid Blancos",
                                       "Catalonia Blaugrana", "München Roten", "Paris Rouge-Bleu"}
    assert min(c.transfer_budget for c in elite) > max(
        c.transfer_budget for c in world.clubs if c.reputation < seed.ELITE_REPUTATION)


@pytest.mark.parametrize("level", MASK_LEVELS)
def test_fm_sample_world_has_no_leaks(level):
    world = seed.resolve_world(2026, source="fm", fm_paths=[SAMPLE], mask_level=level)
    assert find_leaks([lg.name for lg in world.leagues] + [c.name for c in world.clubs]) == []
    assert world.mask_summary.level == level and world.mask_summary.at_ingest
    raw = {plain_key(p.name) for p in fm_parser.parse_files([SAMPLE], mask_names=False).players}
    fm_players = [p for c in world.clubs for p in c.players if p.data_source == "fm"]
    assert len(fm_players) == 42 and not any(plain_key(p.name) in raw for p in fm_players)
    assert lookup_club("München Roten").reputation == next(c for c in world.clubs if c.name == "München Roten").reputation


def test_build_fm_world_masks_even_a_raw_report():
    raw = fm_parser.parse_files([SAMPLE], mask_names=False)
    raw_names = {p.name for p in raw.players}
    world = seed.build_fm_world(raw, rng_seed=1)
    assert world.names_masked and world.mask_summary.renamed_players == 42
    assert {c.name for c in world.clubs} >= {"Istanbul Lions", "München Roten"}
    assert not any(p.name in raw_names for c in world.clubs for p in c.players)
    before = [p.name for c in world.clubs for p in c.players]
    seed.mask_world(world)                                                   # ikinci kez: degisiklik yok
    assert [p.name for c in world.clubs for p in c.players] == before


def test_mask_world_masks_unknown_clubs_and_leagues_without_collisions():
    rng = random.Random(1)
    squad = [seed.generate_player_spec(rng, f"K{i}", Position.GK, 70, (69, 71)) for i in range(2)]
    clubs = [seed.ClubSpec(n, 70, 1_000_000, "4-4-2", list(squad)) for n in ("Kuzey SK", "Kuzey FK", "Galatasaray")]
    world = seed.WorldSpec("synthetic", [seed.LeagueSpec("Premier League", "İngiltere", clubs[:2]),
                                         seed.LeagueSpec("Eredivisie", "Diğer", clubs[2:])])
    assert any("Maskelenmemiş" in p for p in seed.validate_world(world))
    summary = seed.mask_world(world, "light")
    assert summary.renamed_clubs == 3 and summary.renamed_leagues == 2
    assert seed.validate_world(world) == []
    assert len({c.name for c in world.clubs}) == 3
    assert [lg.name for lg in world.leagues][0] == "İngiltere Elit Ligi"


def test_leaking_world_is_rejected_before_db_write():
    rng = random.Random(2)
    squad = [seed.generate_player_spec(rng, f"K{i}", Position.GK, 70, (69, 71)) for i in range(2)]
    world = seed.WorldSpec("fm", [seed.LeagueSpec("Serie A", "İtalya", [
        seed.ClubSpec("Juventus", 80, 1_000_000, "4-4-2", squad)])])
    problems = seed.validate_world(world)
    assert "Maskelenmemiş gerçek isim: Serie A" in problems and "Maskelenmemiş gerçek isim: Juventus" in problems
    with pytest.raises(seed.SeedError, match="Maskelenmemiş"):
        seed.write_world(object(), world, rng_seed=1)                        # DB'ye hic dokunmadan


# ===========================================================================
# 5) Entegrasyon: test veritabani
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")
def test_seeded_database_contains_no_real_club_or_league_names():
    from sqlalchemy import select

    from database import SessionLocal
    from models import League, Team

    with SessionLocal() as db:
        leagues = list(db.scalars(select(League.name)))
        teams = list(db.scalars(select(Team.name)))
    if not teams:
        pytest.skip("Test veritabanı boş")
    assert find_leaks(leagues + teams) == []
    assert len(leagues) == 6 and len(teams) == 24
    assert "Istanbul Lions" in teams and "İngiltere Elit Ligi" in leagues
