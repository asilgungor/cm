"""
club_directory.py
=================
Kulup ve lig rehberi (6. Asama). SAF VERI + isim normalizasyonu.

FM oyuncu listeleri genelde yalnizca kulup adini icerir; hangi lige ait
oldugunu ve kulubun itibarini bu rehberden buluruz. Ayni kulup farkli
yazimlarla gelebilir ("Bayern Münih", "FC Bayern München", "Bayern Munich");
normalize() hepsini tek anahtara indirger.

Itibar degerleri OYUN DENGESI icin verilmis tahminlerdir (1-100), resmi veri degildir.
Rehberde olmayan kulupler, disa aktarimda lig sutunu varsa o lige eklenir ve
itibari kadro gucunden turetilir; lig bilgisi de yoksa seed raporunda listelenir.
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

OTHER_COUNTRY = "Diğer"


@dataclass(frozen=True)
class ClubInfo:
    name: str
    league: str
    reputation: int
    aliases: tuple[str, ...] = ()

    @property
    def country(self) -> str:
        return LEAGUES.get(self.league, OTHER_COUNTRY)


_CLUBS: tuple[ClubInfo, ...] = (
    # --- Turkiye ---
    ClubInfo("Galatasaray", "Trendyol Süper Lig", 80, ("galatasaray sk", "gala", "cimbom")),
    ClubInfo("Fenerbahçe", "Trendyol Süper Lig", 79, ("fenerbahce sk", "fener")),
    ClubInfo("Beşiktaş", "Trendyol Süper Lig", 76, ("besiktas jk", "bjk")),
    ClubInfo("Trabzonspor", "Trendyol Süper Lig", 72, ("trabzon",)),
    ClubInfo("Başakşehir", "Trendyol Süper Lig", 68, ("istanbul basaksehir", "ibfk")),
    # --- Ingiltere ---
    ClubInfo("Manchester City", "Premier League", 94, ("man city", "man. city", "city")),
    ClubInfo("Liverpool", "Premier League", 92, ()),
    ClubInfo("Arsenal", "Premier League", 90, ()),
    ClubInfo("Manchester United", "Premier League", 88, ("man utd", "man united", "manchester utd")),
    ClubInfo("Chelsea", "Premier League", 88, ()),
    ClubInfo("Tottenham Hotspur", "Premier League", 85, ("tottenham", "spurs")),
    ClubInfo("Newcastle United", "Premier League", 83, ("newcastle",)),
    ClubInfo("Aston Villa", "Premier League", 82, ("villa",)),
    # --- Ispanya ---
    ClubInfo("Real Madrid", "LaLiga", 96, ("real madrid cf", "r. madrid")),
    ClubInfo("Barcelona", "LaLiga", 93, ("fc barcelona", "barca")),
    ClubInfo("Atlético Madrid", "LaLiga", 88, ("atletico de madrid", "atl madrid", "atleti")),
    ClubInfo("Real Sociedad", "LaLiga", 80, ()),
    ClubInfo("Sevilla", "LaLiga", 80, ("sevilla fc",)),
    ClubInfo("Villarreal", "LaLiga", 79, ("villarreal cf",)),
    ClubInfo("Athletic Club", "LaLiga", 79, ("athletic bilbao", "bilbao")),
    # --- Almanya ---
    ClubInfo("Bayern München", "Bundesliga", 94,
             ("bayern munih", "bayern munich", "fc bayern munchen", "bayern")),
    ClubInfo("Borussia Dortmund", "Bundesliga", 86, ("dortmund", "bvb")),
    ClubInfo("Bayer Leverkusen", "Bundesliga", 86, ("bayer 04 leverkusen", "leverkusen")),
    ClubInfo("RB Leipzig", "Bundesliga", 83, ("leipzig",)),
    ClubInfo("Eintracht Frankfurt", "Bundesliga", 79, ("frankfurt",)),
    ClubInfo("VfB Stuttgart", "Bundesliga", 79, ("stuttgart",)),
    # --- Italya ---
    ClubInfo("Inter", "Serie A", 88, ("internazionale", "inter milan", "inter milano")),
    ClubInfo("Juventus", "Serie A", 87, ("juve",)),
    ClubInfo("Milan", "Serie A", 86, ("ac milan",)),
    ClubInfo("Napoli", "Serie A", 85, ("ssc napoli",)),
    ClubInfo("Roma", "Serie A", 82, ("as roma",)),
    ClubInfo("Atalanta", "Serie A", 82, ()),
    ClubInfo("Lazio", "Serie A", 80, ("ss lazio",)),
    # --- Fransa ---
    ClubInfo("Paris Saint-Germain", "Ligue 1", 92, ("psg", "paris sg", "paris")),
    ClubInfo("Marseille", "Ligue 1", 81, ("olympique de marseille", "om")),
    ClubInfo("Monaco", "Ligue 1", 81, ("as monaco",)),
    ClubInfo("Lyon", "Ligue 1", 80, ("olympique lyonnais", "ol")),
    ClubInfo("Lille", "Ligue 1", 79, ("losc lille", "losc")),
)

# Kulup adlarinda anlam tasimayan kisaltmalar (tek basina kelime olarak silinir)
_NOISE_TOKENS = frozenset({
    "fc", "cf", "sk", "jk", "fk", "ac", "as", "ss", "ssc", "afc", "sc", "cd", "ud",
    "rcd", "club", "sv", "vfb", "vfl", "tsg", "bv", "ogc", "osc", "rc", "sl", "1",
})

# Lig adi varyasyonlari -> rehberdeki lig adi (normalize edilmis metinde aranir)
_LEAGUE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsuper lig\b|\bturkish super", "Trendyol Süper Lig"),
    (r"\bpremier (league|division)\b|\benglish premier", "Premier League"),
    (r"\bla ?liga\b|\bprimera\b|\bspanish first", "LaLiga"),
    (r"^(?!.*\b2\b).*\bbundesliga\b", "Bundesliga"),
    (r"\bserie a\b", "Serie A"),
    (r"\bligue 1\b", "Ligue 1"),
)


def normalize(text: str) -> str:
    """
    Karsilastirma anahtari: kucuk harf, aksansiz, noktalamasiz, gurultu kisaltmasiz.
        "FC Bayern München"  -> "bayern munchen"
        "Beşiktaş JK"        -> "besiktas"
        "İstanbul Başakşehir"-> "istanbul basaksehir"
    """
    text = (text or "").replace("ı", "i").replace("İ", "i")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [t for t in text.split() if t not in _NOISE_TOKENS]
    return " ".join(tokens)


def _build_index() -> dict[str, ClubInfo]:
    index: dict[str, ClubInfo] = {}
    for club in _CLUBS:
        for alias in (club.name, *club.aliases):
            key = normalize(alias)
            if key and key not in index:
                index[key] = club
    return index


_INDEX = _build_index()


def lookup_club(name: str) -> ClubInfo | None:
    """Kulup adini rehberde arar (yazim farkliliklarina dayanikli)."""
    return _INDEX.get(normalize(name))


def canonical_league(name: str | None) -> tuple[str, str] | None:
    """Disa aktarimdaki lig metnini (lig adi, ulke) ciftine cevirir. Bos ise None."""
    if not name or not name.strip():
        return None
    key = normalize(name)
    for pattern, league in _LEAGUE_PATTERNS:
        if re.search(pattern, key):
            return league, LEAGUES[league]
    return name.strip(), OTHER_COUNTRY


def known_clubs() -> tuple[ClubInfo, ...]:
    return _CLUBS


def reputation_from_strength(top_average_overall: float) -> int:
    """Rehberde olmayan kulup icin kadro gucunden itibar: ilk 11 ort. 80 -> 72, 85 -> 80."""
    return int(max(35, min(90, round(40 + (top_average_overall - 60) * 1.6))))
