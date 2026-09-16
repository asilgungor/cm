"""
club_directory.py
=================
Kulup ve lig rehberi (6. Asama). SAF VERI + isim normalizasyonu.

FM oyuncu listeleri genelde yalnizca kulup adini icerir; hangi lige ait
oldugunu ve kulubun itibarini bu rehberden buluruz. Ayni kulup farkli
yazimlarla gelebilir ("Bayern Münih", "FC Bayern München", "Bayern Munich").

Esleme kurallari (7. Asama denetim duzeltmeleri):
    * Kulup adindaki kisaltmalar (FC, SK, AC...) ayri tutulur. "FC" gibi evrensel
      ekler her kulupte yok sayilabilir; digerleri (SC, AS, SSC...) ancak o kulubun
      kendi yazimlarinda geciyorsa kabul edilir. Boylece "Barcelona SC" (Ekvador)
      FC Barcelona'ya, "Paris FC" PSG'ye yapismaz.
    * Lig adlari serbest regex ile degil, bilinen yazimlarin TAM listesiyle eslenir.
      "Russian Premier League", "LaLiga 2", "Austrian Bundesliga" buyuk liglere karismaz;
      "Ligue 1" ve "1. Bundesliga" gibi rakamli adlar korunur.

Itibar degerleri OYUN DENGESI icin verilmis tahminlerdir (1-100), resmi veri degildir.

Isim maskeleme (8. Asama): her kulubun ve ligin kurgusal ama cagristirici bir "maskeli" adi
vardir (Galatasaray -> Istanbul Lions, Premier League -> Ingiltere Elit Ligi). Veritabanina
yalnizca maskeli adlar yazilir (bkz. name_masking.py). lookup_club ve canonical_league maskeli
adlari da tanir; boylece maskeleme idempotent olur ve maskeli girdi ayni kulube/lige cozulur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# lig adi -> ulke
LEAGUES: dict[str, str] = {
    "Trendyol Süper Lig": "Türkiye",
    "Premier League": "İngiltere",
    "LaLiga": "İspanya",
    "Bundesliga": "Almanya",
    "Serie A": "İtalya",
    "Ligue 1": "Fransa",
}

# gercek lig adi -> maskeli (kurgusal) lig adi
MASKED_LEAGUES: dict[str, str] = {
    "Trendyol Süper Lig": "Türkiye Elit Ligi",
    "Premier League": "İngiltere Elit Ligi",
    "LaLiga": "İspanya Elit Ligi",
    "Bundesliga": "Almanya Elit Ligi",
    "Serie A": "İtalya Elit Ligi",
    "Ligue 1": "Fransa Elit Ligi",
}

OTHER_COUNTRY = "Diğer"


@dataclass(frozen=True)
class ClubInfo:
    name: str                       # gercek ad (yalnizca esleme icin; DB'ye yazilmaz)
    league: str                     # gercek lig adi (LEAGUES anahtari)
    reputation: int
    masked: str                     # kurgusal ad: oyunda ve veritabaninda gorunen
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.masked.strip():
            raise ValueError(f"{self.name}: maskeli ad bos olamaz")

    @property
    def country(self) -> str:
        return LEAGUES.get(self.league, OTHER_COUNTRY)

    @property
    def masked_league(self) -> str:
        return MASKED_LEAGUES.get(self.league, self.league)


_CLUBS: tuple[ClubInfo, ...] = (
    # --- Turkiye ---
    ClubInfo("Galatasaray", "Trendyol Süper Lig", 80, "Istanbul Lions", ("galatasaray sk", "gala", "cimbom")),
    ClubInfo("Fenerbahçe", "Trendyol Süper Lig", 79, "Kadıköy Canaries", ("fenerbahce sk", "fener")),
    ClubInfo("Beşiktaş", "Trendyol Süper Lig", 76, "Bosphorus Eagles", ("besiktas jk", "bjk")),
    ClubInfo("Trabzonspor", "Trendyol Süper Lig", 72, "Karadeniz Storm", ("trabzon",)),
    ClubInfo("Başakşehir", "Trendyol Süper Lig", 68, "Istanbul Owls", ("istanbul basaksehir", "istanbul basaksehir fk", "ibfk")),
    # --- Ingiltere ---
    ClubInfo("Manchester City", "Premier League", 94, "Manchester Blue", ("man city", "man. city")),
    ClubInfo("Liverpool", "Premier League", 92, "Merseyside Reds", ()),
    ClubInfo("Arsenal", "Premier League", 90, "London Gunners", ()),
    ClubInfo("Manchester United", "Premier League", 88, "Manchester Devils", ("man utd", "man united", "manchester utd")),
    ClubInfo("Chelsea", "Premier League", 88, "West London Blues", ()),
    ClubInfo("Tottenham Hotspur", "Premier League", 85, "London Lilywhites", ("tottenham", "spurs")),
    ClubInfo("Newcastle United", "Premier League", 83, "Tyneside Magpies", ("newcastle",)),
    ClubInfo("Aston Villa", "Premier League", 82, "Birmingham Claret", ()),
    # --- Ispanya ---
    ClubInfo("Real Madrid", "LaLiga", 96, "Madrid Blancos", ("real madrid cf", "r. madrid")),
    ClubInfo("Barcelona", "LaLiga", 93, "Catalonia Blaugrana", ("fc barcelona", "barca")),
    ClubInfo("Atlético Madrid", "LaLiga", 88, "Madrid Rojiblancos", ("atletico de madrid", "atl madrid", "atleti")),
    ClubInfo("Real Sociedad", "LaLiga", 80, "Donostia Txuri-Urdin", ()),
    ClubInfo("Sevilla", "LaLiga", 80, "Andalusia Reds", ("sevilla fc",)),
    ClubInfo("Villarreal", "LaLiga", 79, "Castellón Submarinos", ("villarreal cf",)),
    ClubInfo("Athletic Club", "LaLiga", 79, "Bizkaia Lions", ("athletic bilbao",)),
    # --- Almanya ---
    ClubInfo("Bayern München", "Bundesliga", 94, "München Roten",
             ("bayern munih", "bayern munich", "fc bayern munchen", "fc bayern", "bayern")),
    ClubInfo("Borussia Dortmund", "Bundesliga", 86, "Ruhr Schwarzgelb", ("bv borussia dortmund", "dortmund", "bvb")),
    ClubInfo("Bayer Leverkusen", "Bundesliga", 86, "Rhein Werkself", ("bayer 04 leverkusen", "leverkusen")),
    ClubInfo("RB Leipzig", "Bundesliga", 83, "Sachsen Bullen", ("leipzig",)),
    ClubInfo("Eintracht Frankfurt", "Bundesliga", 79, "Main Adler", ()),
    ClubInfo("VfB Stuttgart", "Bundesliga", 79, "Schwaben Weiß-Rot", ()),
    # --- Italya ---
    ClubInfo("Inter", "Serie A", 88, "Milano Nerazzurri", ("internazionale", "inter milan", "fc internazionale milano")),
    ClubInfo("Juventus", "Serie A", 87, "Torino Bianconeri", ("juve",)),
    ClubInfo("Milan", "Serie A", 86, "Milano Rossoneri", ("ac milan",)),
    ClubInfo("Napoli", "Serie A", 85, "Vesuvio Azzurri", ("ssc napoli",)),
    ClubInfo("Roma", "Serie A", 82, "Capitale Giallorossi", ("as roma",)),
    ClubInfo("Atalanta", "Serie A", 82, "Bergamo Orobici", ()),
    ClubInfo("Lazio", "Serie A", 80, "Capitale Biancocelesti", ("ss lazio",)),
    # --- Fransa ---
    ClubInfo("Paris Saint-Germain", "Ligue 1", 92, "Paris Rouge-Bleu", ("psg", "paris sg")),
    ClubInfo("Marseille", "Ligue 1", 81, "Provence Phocéens", ("olympique de marseille",)),
    ClubInfo("Monaco", "Ligue 1", 81, "Rocher Monégasques", ("as monaco",)),
    ClubInfo("Lyon", "Ligue 1", 80, "Rhône Gones", ("olympique lyonnais",)),
    ClubInfo("Lille", "Ligue 1", 79, "Flandres Dogues", ("losc lille", "losc")),
)

# Kulup adlarinda kurum tipini belirten kisaltmalar. Esleme icin ayrilir.
_NOISE_TOKENS = frozenset({
    "fc", "cf", "sk", "jk", "fk", "ac", "as", "ss", "ssc", "afc", "sc", "cd", "ud",
    "rcd", "club", "sv", "vfb", "vfl", "tsg", "bv", "ogc", "osc", "rc", "sl",
})
NOISE_TOKENS = _NOISE_TOKENS              # disa acik ad (name_masking kullanir)
# Hangi kulupte olursa olsun anlam degistirmeyen ekler ("Juventus FC" == "Juventus")
_UNIVERSAL_NOISE = frozenset({"fc", "cf", "afc", "club"})

# Lig adi -> bilinen yazimlar (plain_key ile karsilastirilir, TAM esitlik)
_LEAGUE_ALIASES: dict[str, frozenset[str]] = {
    "Trendyol Süper Lig": frozenset({
        "trendyol super lig", "super lig", "turkish super lig", "turkish super league", "turkiye super lig",
    }),
    "Premier League": frozenset({
        "premier league", "english premier league", "english premier division", "barclays premier league",
    }),
    "LaLiga": frozenset({
        "laliga", "la liga", "laliga ea sports", "laliga santander", "spanish first division",
        "spanish la liga", "primera division",
    }),
    "Bundesliga": frozenset({"bundesliga", "german bundesliga", "1 bundesliga"}),
    "Serie A": frozenset({"serie a", "italian serie a", "serie a tim", "serie a enilive"}),
    "Ligue 1": frozenset({
        "ligue 1", "french ligue 1", "ligue 1 mcdonald s", "ligue 1 uber eats", "ligue 1 conforama",
    }),
}


def plain_key(text: str) -> str:
    """Kucuk harf, aksansiz, noktalamasiz; kelimelerin HICBIRI silinmez (rakamlar dahil)."""
    text = (text or "").replace("ı", "i").replace("İ", "i")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def split_name(text: str) -> tuple[str, frozenset[str]]:
    """Kulup adi -> (cekirdek anahtar, ayrilan kisaltmalar). "FC Bayern München" -> ("bayern munchen", {"fc"})."""
    tokens = plain_key(text).split()
    core = [t for t in tokens if t not in _NOISE_TOKENS]
    noise = frozenset(t for t in tokens if t in _NOISE_TOKENS)
    return " ".join(core), noise


def normalize(text: str) -> str:
    """Karsilastirma anahtari: aksansiz, noktalamasiz, kurum kisaltmalari atilmis."""
    return split_name(text)[0]


def _build_index() -> dict[str, list[tuple[ClubInfo, frozenset[str]]]]:
    index: dict[str, list[tuple[ClubInfo, frozenset[str]]]] = {}
    for club in _CLUBS:
        # Maskeli ad da bir yazim sayilir: maskeli girdi ayni kulube cozulur (idempotent maskeleme)
        forms = [split_name(alias) for alias in (club.name, *club.aliases, club.masked)]
        allowed = frozenset().union(*(noise for _core, noise in forms))
        for core, _noise in forms:
            if not core:
                continue
            entries = index.setdefault(core, [])
            if all(existing is not club for existing, _ in entries):
                entries.append((club, allowed))
    return index


_INDEX = _build_index()


def lookup_club(name: str) -> ClubInfo | None:
    """
    Kulup adini rehberde arar. Kisaltmalar yalnizca evrensel ("FC") ya da o kulubun
    kendi yazimlarinda gecen turdense yok sayilir.
    """
    core, noise = split_name(name)
    if not core:
        return None
    for club, allowed in _INDEX.get(core, ()):
        if noise <= (allowed | _UNIVERSAL_NOISE):
            return club
    return None


def canonical_league(name: str | None) -> tuple[str, str] | None:
    """
    Disa aktarimdaki lig metnini (gercek lig adi, ulke) ciftine cevirir. Bos ise None.
    Maskeli lig adi ("Türkiye Elit Ligi") da ayni lige cozulur.
    """
    if not name or not name.strip():
        return None
    key = plain_key(name)
    for league, aliases in _LEAGUE_ALIASES.items():
        if key in aliases or key == plain_key(MASKED_LEAGUES[league]):
            return league, LEAGUES[league]
    return name.strip(), OTHER_COUNTRY


def known_clubs() -> tuple[ClubInfo, ...]:
    return _CLUBS


def real_league_keys() -> dict[str, frozenset[str]]:
    """Gercek lig adi -> bilinen (maskesiz) yazimlarin plain_key kumesi."""
    return {league: aliases | {plain_key(league)} for league, aliases in _LEAGUE_ALIASES.items()}


def reputation_from_strength(top_average_overall: float) -> int:
    """Rehberde olmayan kulup icin kadro gucunden itibar: ilk 11 ort. 80 -> 72, 85 -> 80."""
    return int(max(35, min(90, round(40 + (top_average_overall - 60) * 1.6))))
