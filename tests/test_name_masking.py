"""
Isim maskeleme ara yazilimi testleri (8. Asama): name_masking, club_directory maskeleri,
fm_parser'in maskeli okumasi, seed'in mask_world / validate_world adimlari.

DB'siz testler CM_TEST_NO_DB=1 ile de calisir; en sondaki entegrasyon testi test
veritabanindaki sentetik dunyada hicbir gercek kulup/lig adi olmadigini dogrular.
"""

from __future__ import annotations

import dataclasses
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

# Kullanicinin kabul ornekleri (light seviyesi): BIREBIR
ACCEPTANCE = [
    ("Orkun Kökçü", "Orkan Kökçü"),                 # R4 ilk isimde (soyadda duz sesli yok)
    ("Mauro Icardi", "Muro Icardi"),                # R3 kayan sesli au -> u
    ("Hakan Çalhanoğlu", "Hakan Çalhano"),          # R1 -oğlu kisaltma
    ("Kylian Mbappé", "Kylian Mbeppe"),             # R4 a -> e, é -> e
    ("Erling Haaland", "Erling Harland"),           # R2 cift sesli
    ("Victor Osimhen", "Victor Osemen"),            # R4 i -> e, sessiz h
    ("Robert Lewandowski", "Robert Lewandow"),      # R1 -wski kisaltma
]


@pytest.mark.parametrize("original,expected", ACCEPTANCE)
def test_user_acceptance_examples(original, expected):
    assert mask_player_name(original) == expected
    assert mask_player_name(original, "light") == expected                # varsayilan seviye light
    assert build_player_mask_map([original])[original] == expected        # toplu esleme ayni sonucu verir


def test_acceptance_examples_together_keep_their_masks():
    names = [o for o, _ in ACCEPTANCE]
    assert build_player_mask_map(names) == dict(ACCEPTANCE)              # birbirleriyle cakismazlar


# Cogunlukla kurgusal adlar: farkli bicimler (on ek, tire, tek ad, Turkce/Iskandinav harf, kisa soyad)
NAME_SHAPES = [
    "Egemen Kalaycıoğlu", "Batuhan Öztoprak", "Görkem Çakırtaş", "İlkay Gürsoylu", "Yiğit Bayraktaroğlu",
    "Óscar Monteagudo", "Íñigo Irazoqui", "Unai Echeverría", "Rubén Arrieta", "Lennart Lindemann",
    "Bastian Brückner", "Konstantin Oberhauser", "Jannik Wendlandt", "Sindre Ødegård", "Halvor Sørlie",
    "Åsmund Bækkelund", "Mads Højgaard", "Tomas van der Berg", "Pieter van Dijkstra", "Luca di Marzio",
    "Rafael dos Anjos", "Jean-Baptiste Morel-Lacroix", "Karim Al-Harbi", "Sami bin Nasser", "Yusuf el Amrani",
    "Zé", "Teodoro", "Pelinho", "Ko", "Tae-Hwan Oh", "Jin Xu", "Mateo Li", "Amadou Ba", "Ole Ek",
    "  Deniz    Aksoy  ", "MARKO VUKIC", "Neymarinho Jr.", "O'Brien Kells", "De La Vega", "Ömer Öztürk",
    "Léo Beaumont", "Kasper Hjulmand-Kragh", "Çağlar Söyüncü", "Øystein Bråten", "Jens Ærø", "Ng", "Oh",
    "Kang-in Lee", "Luis Alberto Suárez Díaz", "Vinícius Júnior", "Jhon Durán",
]

# Harfin kendisi hic hedef alinmayan ozel harfler (yalnizca R1 kesilen kuyrukla dusebilir)
SPECIAL_LETTERS = set("çğıöşüøåæÇĞÖŞÜØÅÆİ")


def _specials(text: str) -> list[str]:
    return [ch for ch in text if ch in SPECIAL_LETTERS]


def _is_initial_token(token: str) -> bool:
    return "." in token and all(sum(ch.isalpha() for ch in part) == 1
                                for part in token.split("-") if any(ch.isalpha() for ch in part))


def _check_light_shape(name: str, masked: str) -> None:
    """Light maskenin genel sozlesmesi (ozgun ad ve maske uzerinden)."""
    tokens, out = name.split(), masked.split()
    assert masked.strip() and masked == masked.strip() and "  " not in masked, (name, masked)
    assert plain_key(masked) != plain_key(name), (name, masked)
    assert len(out) == len(tokens), (name, masked)
    changed = [(a, b) for a, b in zip(tokens, out, strict=True) if a != b]
    assert len(changed) == 1, (name, masked)                               # TEK kelime degisir
    assert not any(_is_initial_token(t) for t in out if t not in tokens), (name, masked)   # bas harf yok
    before, after = changed[0]
    for part_before, part_after in zip(before.split("-"), after.split("-"), strict=True):
        if part_before == part_after:
            continue
        flat = name_masking._flatten(part_before)
        if len(part_after) < len(flat) - 1 and flat.startswith(part_after):   # R1 kisaltma
            letters = sum(ch.isalpha() for ch in flat)
            cut = len(flat) - len(part_after)
            assert 2 <= cut <= 4 and sum(ch.isalpha() for ch in part_after) >= 5, (name, masked)
            assert letters >= 9 or name_masking._truncate(flat) == part_after, (name, masked)
            assert part_before == before.split("-")[-1], (name, masked)       # yalnizca son soyad parcasi
            assert _specials(part_after) == _specials(flat[:len(part_after)]), (name, masked)
        else:
            assert _specials(part_after) == _specials(part_before), (name, masked)
            if part_after[0] != flat[0]:                                       # yalniz R5 son care
                assert flat[0].lower() in "aeiou" and part_after[1:] == flat[1:], (name, masked)


@pytest.mark.parametrize("level", MASK_LEVELS)
def test_name_shapes_are_never_unchanged_or_empty(level):
    for name in NAME_SHAPES:
        masked = mask_player_name(name, level)
        assert masked.strip(), name
        assert plain_key(masked) != plain_key(name), (name, masked)
        assert masked == mask_player_name(name, level)                    # deterministik
        assert "  " not in masked and masked == masked.strip()


def test_light_mask_shape_rules():
    assert mask_player_name("  Erling   Haaland ") == "Erling Harland"      # fazla bosluk
    assert mask_player_name("E. Haaland") == "E. Harland"                   # ozgun bas harf korunur
    assert mask_player_name("J.-P. Morel") == "J.-P. Marel"
    assert mask_player_name("HAALAND") == "HARLAND"                         # tek ad, buyuk harf
    assert mask_player_name("MARKO VUKIC") == "MARKO VAKIC"
    for name in NAME_SHAPES:
        _check_light_shape(" ".join(name.split()), mask_player_name(name))


@pytest.mark.parametrize("original,expected", [
    ("Rubén Arrieta", "Rubén Arriata"),                  # soyad degisir, ilk isim (aksaniyla) butun kalir
    ("Deniz Aksoy", "Deniz Aksay"),
    ("Paul Pogba", "Paul Pagba"),                        # hece sonu "au" kayan sesli sayilmaz
    ("Paulo Dybala", "Pulo Dybala"),                     # R3 (ilk isim) > R4 (soyad)
    ("Thibaut Courtois", "Thibaut Curtois"),             # R3 soyadda
    ("Toni Kroos", "Toni Kros"),                         # R2 cift sesli teklesir
    ("Mads Højgaard", "Mads Højgard"),
    ("Kang-in Lee", "Kang-in Ley"),                      # sonda "ee" -> "ey"
    ("Jens Ærø", "Jans Ærø"),                            # soyadda duz sesli yok -> ilk isim
    ("Ömer Öztürk", "Ömar Öztürk"),
    ("Ousmane Dembélé", "Ousmane Dambele"),              # kelime basi "ou" dokunulmaz
    ("Luka Modrić", "Luka Madric"),                      # ć duzlesir
    ("Jhon Durán", "Jhon Daran"),                        # degismeyen ilk isim yazimini korur
    ("Ng", "Nge"), ("Oh", "Ah"),                         # R5 son care
])
def test_rule_order_examples(original, expected):
    assert mask_player_name(original) == expected


@pytest.mark.parametrize("original,expected", [
    ("Egemen Kalaycıoğlu", "Egemen Kalaycıo"),
    ("Yiğit Bayraktaroğlu", "Yiğit Bayraktaro"),
    ("Ferdi Kadıoğlu", "Ferdi Kadıo"),                   # 8 harf ama uzun ek (-oğlu), kok 5 harf
    ("Dušan Vlahović", "Dušan Vlahov"),                  # 8 harf, -ović
    ("Zlatan Ibrahimović", "Zlatan Ibrahimov"),
    ("Mathias Rasmussen", "Mathias Rasmus"),             # -ssen
    ("Jude Bellingham", "Jude Belling"),                 # 9+ harf: son hece
    ("Pierre-Emerick Aubameyang", "Pierre-Emerick Aubame"),
    ("Unai Echeverría", "Unai Echever"),                 # kok sonundaki "rr" teklesir
    ("Lennart Lindemann", "Lennart Linde"),
    ("Franz Beckenbauer", "Franz Beckenbau"),            # uzun hece: son sesli + unsuz
    ("Jannik Wendlandt", "Jannik Wendla"),               # uzun hece: yalniz son unsuzler
    ("Mikkel Damsgaard", "Mikkel Damsga"),               # kok cift harfle bitmez
    ("Alex Oxlade-Chamberlain", "Alex Oxlade-Chamber"),  # tireli soyadda yalnizca son parca
    ("Neymarinho Jr.", "Neymari Jr."),                   # sonek korunur
])
def test_truncation_of_long_surnames(original, expected):
    assert mask_player_name(original) == expected


@pytest.mark.parametrize("original", [
    "Christian Eriksen", "Anders Larsson", "Emil Alisson", "Ivan Tadić", "Arda Güler", "Orkun Kökçü",
    "Mauro Icardi", "Pedro Monteagu", "Alexander Arnold",
])
def test_short_surnames_are_never_truncated(original):
    masked = mask_player_name(original)
    before, after = original.split()[-1], masked.split()[-1]
    flat = name_masking._flatten(before)
    assert not (len(after) < len(flat) - 1 and flat.startswith(after)), (original, masked)
    assert len(after) >= len(flat) - 1


def test_long_first_names_are_not_truncated():
    assert mask_player_name("Maximilian Ba") == "Maximilian Be"
    assert mask_player_name("Christopher Nkunku") == "Christopher Nkanku"


def test_particles_are_kept_unchanged():
    assert mask_player_name("Tomas van der Berg") == "Tomas van der Barg"
    assert mask_player_name("Luca di Marzio") == "Luca di Merzio"
    assert mask_player_name("Rafael dos Anjos") == "Rafael dos Anjas"
    assert mask_player_name("Yusuf el Amrani") == "Yusuf el Amreni"
    assert mask_player_name("Virgil van Dijk") == "Virgil van Dejk"
    assert mask_player_name("Kevin De Bruyne") == "Kevin De Brayne"
    assert mask_player_name("De La Vega") == "De La Vaga"                   # on ekle baslayan ad
    assert mask_player_name("Karim Al-Harbi") == "Karim Al-Herbi"           # tireli on ek korunur


def test_hyphenated_single_and_suffixed_names():
    assert mask_player_name("Jean-Baptiste Morel-Lacroix") == "Jean-Baptiste Morel-Lecroix"
    assert mask_player_name("Trent Alexander-Arnold") == "Trent Alexander-Arnald"
    assert mask_player_name("Tae-Hwan Oh") == "Tae-Hwen Oh"                 # "Tee"/"Taa" cift sesli olmaz
    assert mask_player_name("Pepe") == "Pape"
    assert mask_player_name("Teodoro") == "Taodoro"
    assert mask_player_name("Marquinhos") == "Marqui"                       # tek ad da uzunsa kisalir
    assert mask_player_name("Neymar Jr.") == "Naymar Jr."
    assert mask_player_name("Vinícius Júnior") == "Venicius Júnior"          # Júnior sonek
    assert mask_player_name("Luis Alberto Suárez Díaz") == "Luis Alberto Suárez Deaz"


def test_turkish_and_nordic_letters_survive():
    assert mask_player_name("Görkem Çakırtaş") == "Görkem Çekırtaş"
    assert mask_player_name("İlkay Gündoğan") == "İlkay Gündağan"
    assert mask_player_name("Çağlar Söyüncü") == "Çeğlar Söyüncü"
    assert mask_player_name("Barış Alper Yılmaz") == "Barış Alper Yılmez"
    assert mask_player_name("Sindre Ødegård") == "Sindre Ødagård"
    assert mask_player_name("Halvor Sørlie") == "Halvor Sørlia"             # "Sørlee" cift sesli olmaz
    assert mask_player_name("Øystein Bråten") == "Øystein Bråtan"
    assert mask_player_name("Åsmund Bækkelund") == "Åsmund Bække"


def _random_word(rng: random.Random, low: int, high: int) -> str:
    letters = "abcdefghijklmnoprstuvyzçğıöşüøåæéáíóúñ"
    return "".join(rng.choice(letters) for _ in range(rng.randint(low, high))).capitalize()


def test_random_names_follow_the_light_contract():
    rng = random.Random(8)
    for _ in range(3000):
        tokens = [_random_word(rng, 1, 12) for _ in range(rng.choice((1, 2, 2, 3)))]
        if rng.random() < 0.1:
            tokens.insert(len(tokens) - 1, rng.choice(("van", "de", "da", "bin")))
        if rng.random() < 0.1:
            tokens[-1] += "-" + _random_word(rng, 2, 10)
        if rng.random() < 0.05:
            tokens.append("Jr.")
        name = " ".join(tokens)
        if all(plain_key(t) in name_masking.PARTICLES | {"jr"} for t in tokens):
            continue
        if not any(ch.isalpha() for ch in plain_key(name)):                  # Latin harfsiz ("Ø"): strong'a duser
            assert mask_player_name(name) == mask_player_name(name, "strong")
            continue
        masked = mask_player_name(name)
        assert masked == mask_player_name(name)                            # deterministik
        _check_light_shape(name, masked)


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


def test_strong_level_outputs_are_unchanged():
    # 8. Asama'daki strong ciktilari: light kurallari degisti, strong degismedi
    assert mask_player_name("Erling Haaland", "strong") == "Teo Ralson"
    assert mask_player_name("Kylian Mbappé", "strong") == "Viktor Novsky"
    assert mask_player_name("Egemen Kalaycıoğlu", "strong") == "Doruk Uzoğlu"
    assert mask_player_name("Egemen Kalaycıoğlu", "strong", nationality="ESP") != "Doruk Uzoğlu"


def test_player_mask_map_avoids_collisions_and_original_names():
    mapping = build_player_mask_map(["Lucas Hernández", "Lucas Hernandes", "LUCAS HERNÁNDEZ",
                                     "Erling Harland", "Erling Haaland"])
    assert mapping["Lucas Hernández"] == mapping["LUCAS HERNÁNDEZ"]          # ayni ad, ayni maske
    assert mapping["Lucas Hernandes"] == "Lucas Hernan"                     # anahtar sirasinda once gelir
    assert mapping["Lucas Hernández"] not in {"Lucas Hernan", "LUCAS HERNAN"}
    assert plain_key(mapping["Erling Haaland"]) != "erling harland"         # baska oyuncunun ozgun adi
    assert all(plain_key(v) != plain_key(k) for k, v in mapping.items())
    assert len({plain_key(v) for v in mapping.values()}) == 4

    both = build_player_mask_map(["Orkun Kökçü", "Orkan Kökçü"])
    assert both["Orkun Kökçü"] != "Orkan Kökçü" and len(set(both.values())) == 2


def test_player_mask_map_many_colliding_names_are_unique_and_stable():
    rng = random.Random(21)
    firsts = ["Unai", "Unei", "Anai", "Tuna", "Sarp", "Lucas", "Lucos", "Erling"]
    lasts = ["Cabrera", "Cebrera", "Cabrara", "Kalaycıoğlu", "Kalaycıo", "Haaland", "Harland",
             "Hernández", "Hernandes", "Hernan", "Lewandowski", "Lewandow", "Øtegard", "Otegard"]
    names = [f"{f} {s}" for f in firsts for s in lasts]
    names += [f"{_random_word(rng, 2, 6)} {_random_word(rng, 2, 11)}" for _ in range(1500)]
    mapping = build_player_mask_map(names)
    key = name_masking._key
    originals = {key(n) for n in names}
    masks = {key(n): key(m) for n, m in mapping.items()}
    assert len(set(masks.values())) == len(masks)                          # farkli ad -> farkli maske
    assert not set(masks.values()) & originals                             # maske baska ozgun ad olamaz
    assert all(m.strip() for m in mapping.values())
    shuffled = list(names)
    rng.shuffle(shuffled)
    assert build_player_mask_map(shuffled) == mapping                      # girdi sirasindan bagimsiz
    assert build_player_mask_map(names, reserved=["Unai Cebrera"])["Unai Cabrera"] == mapping["Unai Cabrera"]
    reserved = build_player_mask_map(["Deneme Oyuncu"], reserved=["Deneme Oyancu"])
    assert plain_key(reserved["Deneme Oyuncu"]) not in {"deneme oyancu", "deneme oyuncu"}


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
        assert after.name == mask_player_name(before.name)                    # bu ornekte cakisma yok
        _check_light_shape(before.name, after.name)                         # ilk isim bas harfe inmez
        assert (after.age, after.position, after.positions_raw, after.nationality, after.uid,
                after.current_ability, after.potential_ability, after.fm_attributes) == \
            (before.age, before.position, before.positions_raw, before.nationality, before.uid,
             before.current_ability, before.potential_ability, before.fm_attributes)
    masked_names = {p.name for p in report.players}
    assert len({plain_key(n) for n in masked_names}) == len(raw_names)       # farkli oyuncu, farkli maske
    assert {"Egemen Kalaycıo", "Tuna Kalaycıo", "Lennart Linde", "Görkem Çekırtaş", "Unai Echever"} <= masked_names
    assert fm_parser.mask_report(report) is report                          # maskeli rapor tekrar maskelenmez
    assert [p.name for p in report.players] == [p.name for p in fm_parser.parse_files([SAMPLE]).players]

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


def test_unmasked_fm_player_names_are_rejected_before_db_write():
    rng = random.Random(3)
    squad = [seed.generate_player_spec(rng, n, Position.GK, 70, (69, 71), data_source="fm")
             for n in ("Erling Haaland", "Kylian Mbappé")]
    world = seed.WorldSpec("fm", [seed.LeagueSpec("Almanya Elit Ligi", "Almanya", [
        seed.ClubSpec("München Roten", 80, 1_000_000, "4-4-2", squad)])])
    assert "Maskelenmemiş FM oyuncu adı: 2 oyuncu (mask_world uygulanmadı)" in seed.validate_world(world)
    with pytest.raises(seed.SeedError, match="FM oyuncu"):
        seed.write_world(object(), world, rng_seed=1)                        # DB'ye hic dokunmadan
    seed.mask_world(world, "light")
    assert [p.name for p in squad] == ["Erling Harland", "Kylian Mbeppe"]
    assert seed.validate_world(world) == []


def test_fm_world_masking_changes_only_player_names():
    raw = fm_parser.parse_files([SAMPLE], mask_names=False)
    masked = fm_parser.parse_files([SAMPLE])
    record_by_mask = {m.name: r for r, m in zip(raw.players, masked.players, strict=True)}

    from_masked = seed.build_fm_world(masked, rng_seed=5)                  # parser maskeledi (at_ingest)
    from_raw = seed.build_fm_world(fm_parser.parse_files([SAMPLE], mask_names=False), rng_seed=5)
    specs_masked = [p for c in from_masked.clubs for p in (*c.players, *c.academy)]
    specs_raw = [p for c in from_raw.clubs for p in (*c.players, *c.academy)]
    assert [dataclasses.asdict(p) for p in specs_masked] == [dataclasses.asdict(p) for p in specs_raw]

    fm_specs = [p for p in specs_masked if p.data_source == "fm"]
    assert len(fm_specs) == 42
    for spec in fm_specs:
        record = record_by_mask[spec.name]                                   # maskeli ad -> ham kayit
        assert spec.name != record.name
        assert (spec.age, spec.position, spec.nationality, spec.fm_uid, spec.current_ability,
                spec.potential_ability, spec.fm_attributes) == \
            (record.age, record.position, record.nationality, record.uid, record.current_ability,
             record.potential_ability, record.fm_attributes)


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
