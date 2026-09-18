"""
tools/build_open_data.py
========================
GELISTIRICI ARACI (13F. Asama). Oyunun CALISMA ZAMANINDA kullanilmaz.

openfootball (CC0 / kamu malı) depolarindan acik veriyi indirir ve depoya girebilecek
iki dosya uretir:

    data/open/clubs.json    kulup adi + takma adlar, kurulus yili, sehir, stat
    data/open/leagues.json  lig tanimlari, guncel kulup listesi ve gecmis sezon puan tablolari

Kaynaklar (hepsi CC0 1.0 / Public Domain):
    openfootball/clubs         europe/<ulke>/<kod>.clubs.txt   (duz metin)
    openfootball/football.json <sezon>/<kod>.json              (duz JSON)
    openfootball/europe        turkey/<sezon>_tr1.txt          (duz metin; football.json'da
                                                                olmayan Super Lig sezonlari)

KURALLAR (bilerek dar tutuldu):
    * yalnizca yukaridaki depolardan, yalnizca .txt/.json duz metin indirilir (ALLOWED_PREFIXES);
    * indirilen hicbir sey CALISTIRILMAZ, yalnizca ayristirilir;
    * uretilen dosyalara kaynak adresi, lisans ve indirme tarihi yazilir;
    * oyunun calisma zamani stdlib kalir: bu arac `requests` varsa onu, yoksa `urllib`i kullanir
      (requirements.txt degismez).

FM'den, Transfermarkt'tan, FIFA/sofifa setlerinden ya da herhangi bir kazimadan VERI ALINMAZ.
Arma, forma ve gorsel indirilmez.

Kullanim:
    python tools/build_open_data.py                    # indir + data/open/ yaz
    python tools/build_open_data.py --offline          # yalnizca onbellekten uret
    python tools/build_open_data.py --out /tmp/open    # baska klasore yaz

Uretilen dosyalari oyun `open_loader.py` uzerinden okur: `python seed.py --source open`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "open"
CACHE_DIR = Path(tempfile.gettempdir()) / "ofm-open-data"        # depoya girmez

LICENSE = "CC0-1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"

CLUBS_BASE = "https://raw.githubusercontent.com/openfootball/clubs/master"
FJ_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
EUROPE_BASE = "https://raw.githubusercontent.com/openfootball/europe/master"

# Baska hicbir adres indirilmez.
ALLOWED_PREFIXES = (CLUBS_BASE + "/", FJ_BASE + "/", EUROPE_BASE + "/")

# Kulup ana dosyalari: ulke kodu -> openfootball/clubs yolu.
# Monaco kendi ulkesi olarak tutulur ama Ligue 1'de oynar (bkz. LEAGUES[fr.1].club_countries).
CLUB_FILES: dict[str, str] = {
    "tr": "europe/turkey/tr.clubs.txt",
    "en": "europe/england/eng.clubs.txt",
    "es": "europe/spain/es.clubs.txt",
    "de": "europe/germany/de.clubs.txt",
    "it": "europe/italy/it.clubs.txt",
    "fr": "europe/france/fr.clubs.txt",
    "mc": "europe/monaco/mc.clubs.txt",
}

# Oyundaki lig adi TURKCEDIR; kulup adlari kaynaktaki yazimiyla kalir.
LEAGUES: list[dict] = [
    {"code": "tr.1", "name": "Süper Lig", "country": "Türkiye", "country_code": "tr",
     "tier": 1, "club_countries": ["tr"]},
    {"code": "en.1", "name": "Premier Lig", "country": "İngiltere", "country_code": "en",
     "tier": 1, "club_countries": ["en"]},
    {"code": "es.1", "name": "La Liga", "country": "İspanya", "country_code": "es",
     "tier": 1, "club_countries": ["es"]},
    {"code": "de.1", "name": "Bundesliga", "country": "Almanya", "country_code": "de",
     "tier": 1, "club_countries": ["de"]},
    {"code": "it.1", "name": "Serie A", "country": "İtalya", "country_code": "it",
     "tier": 1, "club_countries": ["it"]},
    {"code": "fr.1", "name": "Ligue 1", "country": "Fransa", "country_code": "fr",
     "tier": 1, "club_countries": ["fr", "mc"]},
]

# En yeniden eskiye: kulup listesi (kadro sezonu) bu sirada ILK bulunan tam sezondan alinir.
SEASONS = ["2026-27", "2025-26", "2024-25", "2023-24", "2022-23", "2021-22", "2020-21"]

# football.json'da olmayan sezonlar icin duz metin fikstur dosyalari (openfootball/europe).
EXTRA_TXT: dict[str, dict[str, str]] = {
    "tr.1": {
        "2023-24": f"{EUROPE_BASE}/turkey/2023-24_tr1.txt",
        "2020-21": f"{EUROPE_BASE}/turkey/2020-21_tr1.txt",
    },
}

MIN_TABLE_CLUBS = 8          # daha az kulupten tablo cikarilmaz
MIN_ROSTER_CLUBS = 10        # kadro sezonu icin asgari kulup sayisi

NOTICE_CLUBS = (
    "Kulüp adları, takma adları, kuruluş yılı, şehir ve stat bilgileri openfootball/clubs "
    "deposundan alınmıştır (CC0 1.0, kamu malı). Yalnızca olgusal metin; arma, forma ya da "
    "başka görsel içermez. Hiçbir Football Manager / Transfermarkt / FIFA verisi kullanılmamıştır."
)
NOTICE_LEAGUES = (
    "Lig tanımları, güncel kulüp listeleri ve sezon puan tabloları openfootball/football.json ve "
    "openfootball/europe depolarındaki açık fikstür verisinden hesaplanmıştır (CC0 1.0, kamu malı). "
    "Oyuncu verisi içermez; kadrolar OFM tarafından üretilir."
)


class BuildError(Exception):
    """Arac calisamadi."""


# ===========================================================================
# 1) INDIRME
# ===========================================================================

def _urlopen_text(url: str, timeout: int) -> str:
    # requests varsa kullan, yoksa stdlib (oyunun calisma zamani requests'e bagli degildir)
    try:
        import requests
    except ImportError:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8")
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


class Fetcher:
    """Indirme + onbellek. Yalnizca ALLOWED_PREFIXES altindaki .txt/.json adresleri."""

    def __init__(self, cache: Path, offline: bool = False, timeout: int = 60,
                 quiet: bool = False) -> None:
        self.cache = cache
        self.offline = offline
        self.timeout = timeout
        self.quiet = quiet
        self.urls: list[str] = []
        cache.mkdir(parents=True, exist_ok=True)

    def _cache_name(self, url: str) -> str:
        for prefix in ALLOWED_PREFIXES:
            if url.startswith(prefix):
                return url[len(prefix):].replace("/", "_")
        raise BuildError(f"İzin verilmeyen adres: {url}")

    def get(self, url: str) -> str | None:
        """Metni dondurur; dosya yoksa (404) None. Indirilen icerik ASLA calistirilmaz."""
        if not url.startswith(ALLOWED_PREFIXES):
            raise BuildError(f"İzin verilmeyen adres: {url}")
        if not url.endswith((".txt", ".json")):
            raise BuildError(f"İzin verilmeyen dosya türü: {url}")
        path = self.cache / self._cache_name(url)
        if path.exists():
            self.urls.append(url)
            return path.read_text(encoding="utf-8")
        if self.offline:
            return None
        try:
            text = _urlopen_text(url, self.timeout)
        except Exception as exc:                       # 404 dahil: kaynak yoksa atla
            if not self.quiet:
                print(f"  - yok ({exc.__class__.__name__}): {url}")
            return None
        path.write_text(text, encoding="utf-8")
        self.urls.append(url)
        if not self.quiet:
            print(f"  + {len(text):>7} bayt  {url}")
        return text


# ===========================================================================
# 2) AYRISTIRICILAR
# ===========================================================================

_SPONSOR = re.compile(r"\$\$.*?\$\$")            # "$$Medipol$$ Başakşehir" -> "Başakşehir"
_LANG_TAG = re.compile(r"\s*\[[a-z]{2}\]\s*$")   # "Bayern Munich [en]" -> "Bayern Munich"
_LIST_MARK = re.compile(r"^(?:[ivx]+|\d+)\)\s*", re.IGNORECASE)   # "ii) Bayern München II"
_YEAR = re.compile(r"^\d{4}$")
_REGION = "›"


def _tidy(text: str) -> str:
    text = _SPONSOR.sub("", text or "")
    text = _LANG_TAG.sub("", text)
    return " ".join(text.split())


def parse_clubs_txt(text: str, country_code: str) -> list[dict]:
    """
    openfootball/clubs duz metin bicimi:

        Arsenal FC, 1886, @ Emirates Stadium, London (Highbury)   ## yorum
          | Arsenal | FC Arsenal
          | Arsenal Football Club

    Girintisiz satir yeni kulup, "|" ile baslayan girintili satirlar takma adlardir.
    """
    clubs: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.split("##")[0]
        line = re.sub(r"\s+#(?!#).*$", "", line)
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "=")):
            continue
        if stripped.startswith("|"):
            if current is None:
                continue
            for alias in stripped.lstrip("|").split("|"):
                alias = _tidy(alias)
                if alias and alias not in current["aliases"] and alias != current["name"]:
                    current["aliases"].append(alias)
            continue
        if raw[:1].isspace():                     # girintili ama takma ad degil: atla
            continue
        parts = [_tidy(p) for p in stripped.split(",")]
        name = _LIST_MARK.sub("", parts[0]).strip()
        if not name:
            continue
        founded: int | None = None
        city: str | None = None
        stadium: str | None = None
        for part in parts[1:]:
            if not part:
                continue
            if _YEAR.match(part) and founded is None:
                founded = int(part)
            elif part.startswith("@") and stadium is None:
                stadium = _tidy(part[1:]) or None
            elif city is None:
                city = part.split(_REGION)[0].strip() or None
        current = {"id": club_id(country_code, name), "name": name, "country": country_code,
                   "founded": founded, "city": city, "stadium": stadium, "aliases": []}
        clubs.append(current)
    return clubs


def _team_name(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    value = _tidy(value or "")
    return value or None


def _full_time(score) -> tuple[int, int] | None:
    if isinstance(score, dict):
        score = score.get("ft")
    if not isinstance(score, list) or len(score) < 2:
        return None
    if score[0] is None or score[1] is None:
        return None
    try:
        return int(score[0]), int(score[1])
    except (TypeError, ValueError):
        return None


def parse_matches_json(doc: dict) -> tuple[str, list[tuple[str, str, int, int | None, int | None]]]:
    """football.json -> (lig adi, [(ev, deplasman, oynandi, ev gol, dep gol)])."""
    rows = []
    for match in doc.get("matches", []):
        home, away = _team_name(match.get("team1")), _team_name(match.get("team2"))
        if not home or not away:
            continue
        ft = _full_time(match.get("score"))
        rows.append((home, away, 1 if ft else 0, ft[0] if ft else None, ft[1] if ft else None))
    return _tidy(doc.get("name", "")), rows


_TXT_MATCH = re.compile(
    r"^\s*(?:\d{1,2}[:.]\d{2}\s+)?(?P<home>\S.*?)\s{2,}v\s+(?P<away>\S.*?)\s+(?P<hg>\d+)\s*-\s*(?P<ag>\d+)"
)
_TXT_TITLE = re.compile(r"^=+\s*(?P<name>.+?)\s*=*$")


def parse_matches_txt(text: str) -> tuple[str, list[tuple[str, str, int, int | None, int | None]]]:
    """openfootball duz metin fikstur bicimi ("Ev Takimi   v Deplasman   2-1 (1-0)")."""
    name = ""
    rows = []
    for raw in text.splitlines():
        line = raw.split("#")[0]
        if not line.strip():
            continue
        if not name and line.lstrip().startswith("="):
            hit = _TXT_TITLE.match(line.strip())
            if hit:
                name = _tidy(hit.group("name"))
            continue
        hit = _TXT_MATCH.match(line)
        if not hit:
            continue
        rows.append((_tidy(hit.group("home")), _tidy(hit.group("away")), 1,
                     int(hit.group("hg")), int(hit.group("ag"))))
    return name, rows


# ===========================================================================
# 3) KIMLIK ve TABLO
# ===========================================================================

def ascii_key(text: str) -> str:
    """Aksansiz, noktalamasiz karsilastirma anahtari ("Beşiktaş JK" -> "besiktas jk")."""
    text = (text or "").replace("ı", "i").replace("İ", "i").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def club_id(country_code: str, name: str) -> str:
    """Kararli kulup kimligi: "tr/galatasaray-istanbul". Tohumlamada bu dize kullanilir."""
    return f"{country_code}/{ascii_key(name).replace(' ', '-')}"


def build_table(rows) -> dict[str, dict]:
    """Mac listesinden puan tablosu. Skoru olmayan maclar sayilmaz (yarim sezon sorun degil)."""
    table: dict[str, dict] = {}

    def row(team: str) -> dict:
        return table.setdefault(team, {"pld": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})

    for home, away, played, hg, ag in rows:
        row(home), row(away)
        if not played or hg is None or ag is None:
            continue
        for team, scored, conceded in ((home, hg, ag), (away, ag, hg)):
            entry = table[team]
            entry["pld"] += 1
            entry["gf"] += scored
            entry["ga"] += conceded
            if scored > conceded:
                entry["w"] += 1
                entry["pts"] += 3
            elif scored == conceded:
                entry["d"] += 1
                entry["pts"] += 1
            else:
                entry["l"] += 1
    return table


# ===========================================================================
# 4) DERLEME
# ===========================================================================

def _club_index(clubs_by_country: dict[str, list[dict]]) -> dict[str, dict[str, dict]]:
    """ulke kodu -> {ascii anahtar: kulup}. Ad ve tum takma adlar anahtarlanir."""
    index: dict[str, dict[str, dict]] = {}
    for code, clubs in clubs_by_country.items():
        table: dict[str, dict] = {}
        for club in clubs:
            for form in (club["name"], *club["aliases"]):
                table.setdefault(ascii_key(form), club)
        index[code] = table
    return index


def _resolve(index, country_codes, name: str) -> dict | None:
    key = ascii_key(name)
    for code in country_codes:
        hit = index.get(code, {}).get(key)
        if hit is not None:
            return hit
    return None


def build(fetcher: Fetcher, quiet: bool = False) -> tuple[dict, dict]:
    """Iki vendor dosyasinin icerigini (clubs, leagues) uretir."""
    if not quiet:
        print("[open] Kulüp dosyaları:")
    clubs_by_country: dict[str, list[dict]] = {}
    club_sources: list[str] = []
    for code, rel in CLUB_FILES.items():
        url = f"{CLUBS_BASE}/{rel}"
        text = fetcher.get(url)
        if text is None:
            raise BuildError(f"Kulüp dosyası indirilemedi: {url}")
        clubs_by_country[code] = parse_clubs_txt(text, code)
        club_sources.append(url)
    index = _club_index(clubs_by_country)

    if not quiet:
        print("[open] Lig sezonları:")
    league_sources: list[str] = []
    leagues: list[dict] = []
    used_ids: set[str] = set()
    for spec in LEAGUES:
        code = spec["code"]
        countries = spec["club_countries"]
        seasons: list[dict] = []
        source_name = ""
        for season in SEASONS:
            url = f"{FJ_BASE}/{season}/{code}.json"
            text = fetcher.get(url)
            if text is not None:
                name, rows = parse_matches_json(json.loads(text))
            else:
                url = EXTRA_TXT.get(code, {}).get(season)
                if url is None:
                    continue
                text = fetcher.get(url)
                if text is None:
                    continue
                name, rows = parse_matches_txt(text)
            table = build_table(rows)
            if len(table) < MIN_TABLE_CLUBS:
                continue
            source_name = source_name or name
            entries = []
            for team in sorted(table):
                record = _resolve(index, countries, team)
                entries.append({
                    "id": record["id"] if record else club_id(spec["country_code"], team),
                    "name": team, **table[team],
                })
            entries.sort(key=lambda e: (-e["pts"], -(e["gf"] - e["ga"]), e["name"]))
            seasons.append({"season": season, "source": url, "name": name,
                            "clubs": len(entries), "table": entries})

        roster_season = next((s for s in seasons if len(s["table"]) >= MIN_ROSTER_CLUBS), None)
        if roster_season is None:
            raise BuildError(f"{code}: kadro sezonu bulunamadı.")
        roster = []
        for entry in sorted(roster_season["table"], key=lambda e: e["name"]):
            if entry["id"] in used_ids:
                raise BuildError(f"{code}: kulüp kimliği iki kez geçiyor: {entry['id']}")
            used_ids.add(entry["id"])
            roster.append({"id": entry["id"], "name": entry["name"]})
        leagues.append({
            "code": code, "name": spec["name"], "country": spec["country"],
            "country_code": spec["country_code"], "tier": spec["tier"],
            "source_name": source_name, "roster_season": roster_season["season"],
            "club_countries": countries, "clubs": roster,
            "seasons": [{k: v for k, v in s.items() if k != "name"} for s in seasons],
        })
        league_sources += [s["source"] for s in seasons]
        if not quiet:
            print(f"  {code}: {len(roster)} kulüp ({roster_season['season']}), "
                  f"{len(seasons)} sezon tablosu")

    # clubs.json: yalnizca bir sezon tablosunda gecen kulupler (dosya kucuk ve amaca donuk kalsin)
    wanted = {e["id"] for lg in leagues for s in lg["seasons"] for e in s["table"]}
    clubs = [c for code in CLUB_FILES for c in clubs_by_country[code] if c["id"] in wanted]
    clubs.sort(key=lambda c: c["id"])
    known = {c["id"] for c in clubs}
    for league in leagues:                          # rehberde olmayan kulup icin asgari kayit
        for entry in league["clubs"]:
            if entry["id"] not in known:
                known.add(entry["id"])
                clubs.append({"id": entry["id"], "name": entry["name"],
                              "country": league["country_code"], "founded": None,
                              "city": None, "stadium": None, "aliases": []})
    clubs.sort(key=lambda c: c["id"])

    today = date.today().isoformat()
    clubs_doc = {
        "schema": "ofm/open-clubs", "version": 1,
        "license": LICENSE, "license_url": LICENSE_URL,
        "source": "openfootball/clubs", "source_url": "https://github.com/openfootball/clubs",
        "source_files": sorted(set(club_sources)),
        "fetched_at": today, "generator": "tools/build_open_data.py",
        "notice": NOTICE_CLUBS,
        "clubs": clubs,
    }
    leagues_doc = {
        "schema": "ofm/open-leagues", "version": 1,
        "license": LICENSE, "license_url": LICENSE_URL,
        "source": "openfootball/football.json + openfootball/europe",
        "source_url": "https://github.com/openfootball/football.json",
        "source_files": sorted(set(league_sources)),
        "fetched_at": today, "generator": "tools/build_open_data.py",
        "notice": NOTICE_LEAGUES,
        "leagues": leagues,
    }
    return clubs_doc, leagues_doc


def write_json(path: Path, doc: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="openfootball (CC0) verisinden data/open/*.json üretir (geliştirici aracı).")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="Çıktı klasörü (data/open).")
    parser.add_argument("--cache", type=Path, default=CACHE_DIR, help="İndirme önbelleği.")
    parser.add_argument("--offline", action="store_true", help="İnternete çıkma, önbelleği kullan.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    fetcher = Fetcher(args.cache, offline=args.offline, quiet=args.quiet)
    try:
        clubs_doc, leagues_doc = build(fetcher, quiet=args.quiet)
    except BuildError as exc:
        print(f"[open] HATA: {exc}")
        return 1

    total = 0
    total += write_json(args.out / "clubs.json", clubs_doc)
    total += write_json(args.out / "leagues.json", leagues_doc)

    print(f"[open] {len(clubs_doc['clubs'])} kulüp, {len(leagues_doc['leagues'])} lig yazıldı "
          f"-> {args.out} ({total / 1024:.0f} KB, lisans {LICENSE}, tarih {clubs_doc['fetched_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
