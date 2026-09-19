"""
tools/build_squads.py
=====================
GELISTIRICI ARACI (16G, sahip karari K-S1 "IKISI BIRDEN"). Oyunun CALISMA ZAMANINDA kullanilmaz.

Acik veri dunyasinin 1. kademe kuluplerinin (data/open/leagues.json, 6 lig / 114 kulup) GUNCEL A TAKIM
KADROLARINI Wikidata'dan (CC0 1.0) alir ve YEREL bir dosyaya yazar:

    data/local/squads.json          (gitignore; DEPOYA GIRMEZ)
    data/local/_cache_squads/       (ham SPARQL yanitlarinin kullanilan alanlari; gitignore)

!! KISISEL VERI !!  Dosya gercek kisilerin adini, dogum tarihini ve uyrugunu tasir (KVKK / GDPR). Yalnizca
sahibin KENDI makinesindeki TEK KOLTUKLU kariyeri icindir (seed.py --real-players, OFM_ALLOW_REAL_PLAYERS=1).
Dosya ve onbellek depoya eklenmez, paylasilmaz, yayimlanmaz; arac `--out` / `--cache` ile depo icinde
data/local/ disina yazmayi REDDEDER (check_output_path).

Oyuncu basina (Wikidata ozelligi):
    ad              rdfs:label  (en > mul > kulubun ulke dili > diger)
    dogum tarihi    P569        (yas = referans tarihinde, varsayilan 2026-07-01)
    uyruk           P1532 "country for sport" > P27 tercihli (preferred) > P27 tek deger > en kucuk QID
    mevki           P413        (P279* ile kaleci / defans / orta saha / forvet; bilinmiyorsa null)
    kulup           P54: GUNCEL kulup = ACIK uyelikler (P582 yok ya da gelecekte) icinde en GEC baslayani (P580);
                    milli / kadin / B-U21-altyapi takimi ve kiralik uyelik secimde yok sayilir (assemble)
    forma numarasi  P1618 (uyelik niteleyicisi; varsa)
    taninirlik      wikibase:sitelinks (kadro ici siralama icin; yetenek DEGIL)
    guven           high / medium / low (P585 istatistik tarihi, P1350 mac sayisi, yeni baslangic; baslangicsiz = low)
Yetenek Wikidata'da YOKTUR: seed.py / open_loader.py yetenekleri bugunku gibi kulup itibari + yas + mevkiden
URETIR; eksik kadrolar uretilmis oyuncularla tamamlanir.

Tuzaklar (phase13/data_licensing.md) ve cozumleri (16G ikinci gecis; yas tek basina HIC kimseyi dislamaz):
    * bitis tarihi olmayan eski uyelikler -> oyuncunun baska takimda DAHA GEC baslamis acik uyeligi varsa o kulupte
      sayilmaz (moved); daha gec baslamis BITMIS donem + hic tazelik kaniti yok -> bayat (left); uzun sure ayni
      kulupte kalan (eski baslangic, bitis yok) korunur. Baslangic >= referans yili - 18, dogum >= referans - 41 yas,
      olum tarihi (P570), is donemi sonu (P2032) ve uyelikten sonra teknik direktorluk (P6087) -> dislanir;
    * baslangicsiz (P580 yok) acik uyelik: yalnizca oyuncunun baska TARIHLI acik uyeligi yoksa, dusuk guvenle;
    * kadin takimi oyunculari erkek kulubu ogesine yazilmis olabilir -> P21 kadin / trans kadin dislanir,
      uyelikte P2094 "kadin futbolu" niteleyicisi olan dislanir;
    * altyapi / U-21 / B takimi AYRI ogelerdir: kadroya yalnizca A takim ogesi (club_colors.json'daki QID) bakar;
      guncel kulup seciminde bu takimlar (team_classes: sinif + etiket deseni) yok sayilir;
    * KIRALIK: P1642 = Q2914547 (loan) olan uyelik sayilmaz. Oyuncu, kiralik olmayan guncel uyeligi hangi
      kulupteyse orada (ana kulubu) listelenir; ana kulubu dunyamizda degilse listede yoktur;
    * P27 cok degerli -> tek deger secimi yukarida.
Kadro tavani (30) ve guven sirasi oyunda uygulanir (open_loader.select_real_players).

Kulup -> Wikidata: data/open/club_colors.json'daki (tools/build_club_colors.py) `wikidata` kimlikleri. Lig bilgisi
openfootball'dan gelir (Wikidata P118 bayat, KULLANILMAZ).

KURALLAR (build_club_colors.py ile ayni, bilerek dar):
    * yalnizca https://query.wikidata.org/sparql (ALLOWED_PREFIXES; host listesi query/www.wikidata.org);
      SI/FM verisi, Transfermarkt, Kaggle, sortitoutsi, kulup siteleri, kazima YOK;
    * yanitlar yalnizca JSON olarak ayristirilir, hicbir sey CALISTIRILMAZ;
    * tanimlayici User-Agent, toplu SPARQL (VALUES bloklari), istekler SERI ve arasinda bekleme,
      429/503'te Retry-After'a uyulur.

Kullanim:
    python tools/build_squads.py                   # onbellekte olmayani indir + data/local/squads.json yaz
    python tools/build_squads.py --offline         # yalnizca onbellekten (eksik girdi = hata, ag YOK)
    python tools/build_squads.py --clubs en/arsenal-fc tr/galatasaray-istanbul   # yalnizca bu kulupler
Guncellemek icin onbellek klasorunu silip cevrimici calistir. `fetched_at` onbellegin son ag alim tarihidir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent
OPEN_DIR = ROOT / "data" / "open"
LOCAL_DIR = ROOT / "data" / "local"                 # .gitignore: data/local/
OUT_FILE = "squads.json"
CACHE_DIR = LOCAL_DIR / "_cache_squads"

SCHEMA = "ofm/local-squads"
LICENSE = "CC0-1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"

SPARQL_URL = "https://query.wikidata.org/sparql"
ALLOWED_PREFIXES = (SPARQL_URL,)
ALLOWED_HOSTS = frozenset({"query.wikidata.org", "www.wikidata.org"})
USER_AGENT = ("OFM-squads/1.0 (https://github.com/asilgungor/cm; tools/build_squads.py; "
              "one-off developer tool, batched queries) python-requests")

SPARQL_PAUSE = 2.0          # sn; istekler arasi (seri)
MAX_RETRIES = 4
CLUB_BATCH = 10             # kadro sorgusu: VALUES blogu basina kulup
PLAYER_BATCH = 200          # oyuncu sorgulari: VALUES blogu basina oyuncu
ITEM_BATCH = 300            # mevki / ulke / takim siniflandirmasi

DEFAULT_SEASON_YEAR = 2026
MIN_AGE, MAX_AGE = 16, 40   # referans tarihindeki yas (DB kisiti 15-45)
START_WINDOW_YEARS = 18     # uyelik baslangici >= referans yili - 18 (2026 -> 2008)
# Tazelik (bkz. freshness): guven sirasi icin; tek basina dislamaz
FRESH_MONTHS = 18           # P585 (istatistik tarihi) bu kadar ay icindeyse uyelik guncel
RECENT_START_YEARS = 2      # bu kadar yil icinde baslamis uyelik guncel sayilir
RETURN_YEARS = 3            # son sonraki donem bu kadar yil icinde bittiyse oyuncu dondu (yil hassasiyeti payi)

LOAN = "Q2914547"                           # P1642 acquisition transaction: loan
HUMAN = "Q5"
FEMALE, TRANS_WOMAN = "Q6581072", "Q1052281"
WOMENS_FOOTBALL = "Q606060"                 # P2094 competition class: women's association football
WOMEN_TEAM_CLASSES = ("Q28140340", "Q51481377", "Q61740358")   # women's a.f. team / club, women's sports team
RESERVE_TEAM_CLASSES = ("Q2412834", "Q134468886")              # reserve team, association football academy
# Etiket deseni (Ingilizce etiket): siniflanmamis B / U-21 / altyapi / kadin takimlari. "Juniors" bilerek YOK
# (Boca Juniors, Argentinos Juniors A takimdir).
TEAM_LABEL_PATTERNS = {
    "women": re.compile(r"\b(women|ladies|f[ée]minin[e]?|femenino|femminile|frauen|kad[ıi]n)\b", re.I),
    "reserve": re.compile(r"( II| III| B|\bU-?(1[5-9]|2[0-3])s?|\bunder[- ](1[5-9]|2[0-3])s?|castilla|atl[eè]tic|"
                         r"next gen|primavera|juvenil|reserves?|youth|academy|development squad)\s*$", re.I),
}
NATIONAL_TEAM = "Q6979593"                  # national association football team (P279* ile tum turevleri)
POSITION_ROOTS = {"GK": "Q201330", "DEF": "Q336286", "MID": "Q193592", "FWD": "Q280658"}
# P279* ile siniflanmayan ama futbol kadrolarinda gorulen mevki ogeleri (2026-09 olcumu)
POSITION_EXTRA = {"Q172964": "GK",          # "goalkeeper" (genel spor ogesi)
                  "Q3446915": "FWD",        # "attacker"
                  "Q1109563": "DEF"}        # "centre half" (bugunku kullanimda stoper)
POSITION_ORDER = ("MID", "DEF", "FWD", "GK")        # esitlik bozma sirasi
LABEL_LANGS = ("en", "mul", "tr", "es", "de", "it", "fr")
COUNTRY_LANG = {"tr": "tr", "en": "en", "es": "es", "de": "de", "it": "it", "fr": "fr"}

NOTICE = (
    "KİŞİSEL VERİ: gerçek futbolcuların adı, doğum tarihi ve uyruğu (Wikidata, CC0 1.0). Yalnızca sahibin "
    "kendi makinesindeki tek koltuklu kariyeri içindir; depoya eklenmez, paylaşılmaz, yayımlanmaz. "
    "Yetenek değeri içermez (oyun üretir). Hiçbir Football Manager / Transfermarkt / Kaggle / sortitoutsi "
    "verisi kullanılmamıştır."
)


class BuildError(Exception):
    """Arac calisamadi."""


# ===========================================================================
# 1) CIKTI YERI (kisisel veri depoya girmez)
# ===========================================================================

def check_output_path(path: Path, root: Path = ROOT, local_dir: Path = LOCAL_DIR) -> Path:
    """
    Kisisel veri yalnizca data/local/ (gitignore) altina ya da DEPO DISINA yazilir. Depo icinde baska bir
    yer (orn. data/open/, depo koku) BuildError verir: gercek adlar yanlislikla commit'lenmesin.
    """
    resolved = Path(path).resolve()
    root_r, local_r = Path(root).resolve(), Path(local_dir).resolve()
    if resolved == root_r or root_r in resolved.parents:
        if not (resolved == local_r or local_r in resolved.parents):
            raise BuildError(f"{path}: kişisel veri depoda yalnızca data/local/ (gitignore) altına yazılabilir.")
    return resolved


# ===========================================================================
# 2) AG + ONBELLEK
# ===========================================================================

def _http(url: str, params: dict, timeout: int) -> tuple[int, str, str | None]:
    """POST (durum, govde, Retry-After). requests varsa onu, yoksa stdlib'i kullanir."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
    try:
        import requests
    except ImportError:
        import urllib.error
        import urllib.request
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=urlencode(params).encode("utf-8"), headers=headers,
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8"), None
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), exc.headers.get("Retry-After")
    response = requests.post(url, data=params, headers=headers, timeout=timeout)
    response.encoding = "utf-8"
    return response.status_code, response.text, response.headers.get("Retry-After")


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if url not in ALLOWED_PREFIXES or parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise BuildError(f"İzin verilmeyen adres: {url}")


class Wikidata:
    """
    SPARQL istemcisi + JSON onbellek (sorgu metninin sha1'i -> satirlar). Onbellekte olan istek aga CIKMAZ.
    offline=True iken onbellekte olmayan istek BuildError verir (sessizce eksik veri uretilmez).
    """

    def __init__(self, cache: Path, offline: bool = False, timeout: int = 90, quiet: bool = False) -> None:
        self.cache = cache
        self.offline = offline
        self.timeout = timeout
        self.quiet = quiet
        self.requests = 0
        self._last = 0.0
        self._used: dict[str, dict] = {}
        path = cache / "sparql.json"
        self._data: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        meta = cache / "meta.json"
        self._meta = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}

    @property
    def fetched_at(self) -> str:
        if self.requests:
            return date.today().isoformat()
        return self._meta.get("fetched_at") or date.today().isoformat()

    def save(self, prune: bool = True) -> None:
        if self.offline:
            return
        check_output_path(self.cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        doc = self._used if prune else self._data
        lines = [f"{json.dumps(k)}: {json.dumps(doc[k], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                 for k in sorted(doc)]
        (self.cache / "sparql.json").write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")
        meta = {"fetched_at": self.fetched_at, "user_agent": USER_AGENT, "endpoints": list(ALLOWED_PREFIXES)}
        (self.cache / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")

    def _fetch(self, query: str) -> dict:
        check_url(SPARQL_URL)
        for attempt in range(MAX_RETRIES + 1):
            wait = SPARQL_PAUSE - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                status, body, retry_after = _http(SPARQL_URL, {"query": query, "format": "json"}, self.timeout)
            except Exception as exc:                          # zaman asimi / baglanti
                status, body, retry_after = 0, str(exc), None
            self._last = time.monotonic()
            self.requests += 1
            if status == 200:
                try:
                    return json.loads(body)                   # yalnizca ayristirilir
                except ValueError as exc:
                    raise BuildError(f"JSON değil: {body[:120]!r}") from exc
            if status in (0, 429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                delay = float(retry_after) if (retry_after or "").isdigit() else 10.0 * (attempt + 1)
                if not self.quiet:
                    print(f"  ~ {status or 'bağlantı'}; {delay:.0f} sn sonra yeniden")
                time.sleep(min(delay, 180.0))
                continue
            raise BuildError(f"SPARQL HTTP {status}: {body[:200]!r}")
        raise BuildError("SPARQL: yeniden denemeler tükendi")

    def sparql(self, query: str) -> list[dict]:
        """SELECT sonucu: [{degisken: deger}] (varlik URI'leri kisa kimlige indirgenir)."""
        query = "\n".join(line.strip() for line in query.strip().splitlines() if line.strip())
        key = hashlib.sha1(query.encode("utf-8")).hexdigest()
        if key in self._data:
            self._used[key] = self._data[key]
            return self._data[key]["rows"]
        if self.offline:
            raise BuildError(f"--offline: önbellekte yok ({query.splitlines()[0][:80]})")
        doc = self._fetch(query)
        rows = []
        for binding in doc.get("results", {}).get("bindings", []):
            row = {}
            for var, cell in binding.items():
                text = cell.get("value", "")
                if cell.get("type") == "uri" and "/entity/" in text:
                    text = text.rsplit("/", 1)[-1]
                row[var] = text
            rows.append(row)
        rows.sort(key=lambda r: json.dumps(r, sort_keys=True))
        entry = {"head": query.splitlines()[0][:120], "rows": rows}
        self._data[key] = self._used[key] = entry
        self.save(prune=False)              # ara kayit: uzun calisma yarida kalirsa indirilen kaybolmaz
        if not self.quiet:
            print(f"  · istek {self.requests}: {len(rows)} satır", flush=True)
        return rows


# ===========================================================================
# 3) KULUPLER
# ===========================================================================

@dataclass(frozen=True)
class ClubRef:
    id: str                     # acik veri kulup kimligi (orn. "en/arsenal-fc")
    name: str
    league: str                 # lig kodu (orn. "en.1")
    tier: int
    country: str                # ulke kodu (tr/en/es/de/it/fr)
    qid: str | None             # Wikidata A takim ogesi (club_colors.json)


def load_clubs(open_dir: Path = OPEN_DIR, tiers: tuple[int, ...] = (1,),
               only: tuple[str, ...] = ()) -> list[ClubRef]:
    try:
        leagues = json.loads((open_dir / "leagues.json").read_text(encoding="utf-8"))
        colors = json.loads((open_dir / "club_colors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError(f"Açık veri okunamadı ({open_dir}): {exc}") from exc
    qids = {cid: (entry or {}).get("wikidata")
            for part in ("clubs", "without_colors") for cid, entry in colors.get(part, {}).items()}
    clubs = []
    for league in leagues.get("leagues", []):
        tier = int(league.get("tier", 1))
        if tier not in tiers:
            continue
        for club in league.get("clubs", []):
            if only and club["id"] not in only:
                continue
            clubs.append(ClubRef(club["id"], club["name"], league["code"], tier,
                                 league.get("country_code", ""), qids.get(club["id"])))
    clubs.sort(key=lambda c: c.id)
    return clubs


# ===========================================================================
# 4) SORGULAR (toplu, VALUES bloklari)
# ===========================================================================

def _chunks(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _values(qids) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def _qid_key(qid: str) -> int:
    return int(qid[1:]) if qid[1:].isdigit() else 0


def squad_rows(client: Wikidata, club_qids: list[str], reference: date) -> list[dict]:
    """A takim ogelerine GUNCEL (bitissiz, baslangicli) uyeligi olan insanlar; kadin / olmus dislanir."""
    min_start = f"{reference.year - START_WINDOW_YEARS}-01-01T00:00:00Z"
    min_birth = f"{reference.year - MAX_AGE - 1}-{reference.month:02d}-{reference.day:02d}T00:00:00Z"
    rows: list[dict] = []
    for chunk in _chunks(sorted(set(club_qids), key=_qid_key), CLUB_BATCH):
        rows += client.sparql(f"""
        SELECT ?club ?player ?st ?start ?dob ?number ?acq ?cls ?pit ?matches ?sl WHERE {{
          VALUES ?club {{ {_values(chunk)} }}
          ?player p:P54 ?st .
          ?st ps:P54 ?club ; pq:P580 ?start .
          FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
          FILTER NOT EXISTS {{ ?st wikibase:rank wikibase:DeprecatedRank }}
          FILTER(?start >= "{min_start}"^^xsd:dateTime)
          ?player wdt:P31 wd:{HUMAN} ; wdt:P569 ?dob .
          FILTER(?dob >= "{min_birth}"^^xsd:dateTime)
          FILTER NOT EXISTS {{ ?player wdt:P21 wd:{FEMALE} }}
          FILTER NOT EXISTS {{ ?player wdt:P21 wd:{TRANS_WOMAN} }}
          FILTER NOT EXISTS {{ ?player wdt:P570 ?died }}
          OPTIONAL {{ ?st pq:P1618 ?number }}
          OPTIONAL {{ ?st pq:P1642 ?acq }}
          OPTIONAL {{ ?st pq:P2094 ?cls }}
          OPTIONAL {{ ?st pq:P585 ?pit }}
          OPTIONAL {{ ?st pq:P1350 ?matches }}
          OPTIONAL {{ ?player wikibase:sitelinks ?sl }}
        }}
        """)
    return rows


def membership_rows(client: Wikidata, players: list[str]) -> list[dict]:
    """
    Adaylarin TUM uyelikleri (bitmis olanlar dahil): 'sonra baska kulube gitti mi?' karari icin
    (bkz. assemble: acik uyelik her zaman, bitmis uyelik yalnizca taze olmayan ifadede sayilir).
    """
    rows: list[dict] = []
    for chunk in _chunks(sorted(set(players), key=_qid_key), PLAYER_BATCH):
        rows += client.sparql(f"""
        SELECT ?player ?team ?start ?end ?acq WHERE {{
          VALUES ?player {{ {_values(chunk)} }}
          ?player p:P54 ?st . ?st ps:P54 ?team .
          FILTER NOT EXISTS {{ ?st wikibase:rank wikibase:DeprecatedRank }}
          OPTIONAL {{ ?st pq:P580 ?start }}
          OPTIONAL {{ ?st pq:P582 ?end }}
          OPTIONAL {{ ?st pq:P1642 ?acq }}
        }}
        """)
    return rows


def extra_squad_rows(client: Wikidata, club_qids: list[str], reference: date) -> list[dict]:
    """
    2. tur (16G ikinci gecis): squad_rows'un kacirdigi GUNCEL uyelikler -- baslangic tarihi (P580) OLMAYAN
    bitissiz uyelik ya da bitis tarihi GELECEKTE olan uyelik (sozlesme sonu P582 diye yazilmis). Oyuncu suzgecleri ayni.
    Baslangicsiz uyelik assemble'da yalnizca oyuncunun baska tarihli acik uyeligi yoksa kabul edilir (dusuk guven).
    """
    min_start = f"{reference.year - START_WINDOW_YEARS}-01-01T00:00:00Z"
    min_birth = f"{reference.year - MAX_AGE - 1}-{reference.month:02d}-{reference.day:02d}T00:00:00Z"
    ref = f"{reference.isoformat()}T00:00:00Z"
    rows: list[dict] = []
    for chunk in _chunks(sorted(set(club_qids), key=_qid_key), CLUB_BATCH):
        rows += client.sparql(f"""
        SELECT ?club ?player ?st ?start ?end ?dob ?number ?acq ?cls ?pit ?matches ?sl WHERE {{
          VALUES ?club {{ {_values(chunk)} }}
          ?player p:P54 ?st .
          ?st ps:P54 ?club .
          FILTER NOT EXISTS {{ ?st wikibase:rank wikibase:DeprecatedRank }}
          OPTIONAL {{ ?st pq:P580 ?start }}
          OPTIONAL {{ ?st pq:P582 ?end }}
          FILTER((!BOUND(?start) && !BOUND(?end))
                 || (BOUND(?end) && ?end > "{ref}"^^xsd:dateTime
                     && (!BOUND(?start) || ?start >= "{min_start}"^^xsd:dateTime)))
          ?player wdt:P31 wd:{HUMAN} ; wdt:P569 ?dob .
          FILTER(?dob >= "{min_birth}"^^xsd:dateTime)
          FILTER NOT EXISTS {{ ?player wdt:P21 wd:{FEMALE} }}
          FILTER NOT EXISTS {{ ?player wdt:P21 wd:{TRANS_WOMAN} }}
          FILTER NOT EXISTS {{ ?player wdt:P570 ?died }}
          OPTIONAL {{ ?st pq:P1618 ?number }}
          OPTIONAL {{ ?st pq:P1642 ?acq }}
          OPTIONAL {{ ?st pq:P2094 ?cls }}
          OPTIONAL {{ ?st pq:P585 ?pit }}
          OPTIONAL {{ ?st pq:P1350 ?matches }}
          OPTIONAL {{ ?player wikibase:sitelinks ?sl }}
        }}
        """)
    return rows


def team_classes(client: Wikidata, teams: list[str]) -> dict[str, str]:
    """
    Guncel kulup seciminde YOK SAYILAN takimlar: takim -> "national" / "women" / "reserve" (U-21, altyapi, B takimi).
    Sinif: P31/P279* (national association football team; women's association football team/club, women's sports
    team; reserve team, association football academy) + Ingilizce etiket deseni (TEAM_LABEL_PATTERNS; orn.
    "Borussia Monchengladbach II" yalnizca "men's association football team" diye tiplenmis).
    """
    out: dict[str, str] = {}
    women = _values(WOMEN_TEAM_CLASSES)
    reserve = _values(RESERVE_TEAM_CLASSES)
    for chunk in _chunks(sorted(set(teams), key=_qid_key), ITEM_BATCH):
        for row in client.sparql(f"""
        SELECT ?team ?label ?national ?women ?reserve WHERE {{
          VALUES ?team {{ {_values(chunk)} }}
          OPTIONAL {{ ?team rdfs:label ?label . FILTER(LANG(?label) = "en") }}
          BIND(EXISTS {{ ?team wdt:P31/wdt:P279* wd:{NATIONAL_TEAM} }} AS ?national)
          BIND((EXISTS {{ VALUES ?w {{ {women} }} ?team wdt:P31/wdt:P279* ?w }}
                || EXISTS {{ ?team wdt:P2094 wd:{WOMENS_FOOTBALL} }}) AS ?women)
          BIND(EXISTS {{ VALUES ?r {{ {reserve} }} ?team wdt:P31/wdt:P279* ?r }} AS ?reserve)
        }}
        """):
            kind = classify_team(row.get("label") or "", row.get("national") == "true",
                                 row.get("women") == "true", row.get("reserve") == "true")
            if kind:
                out[row["team"]] = kind
    return out


def classify_team(label: str, national: bool, women: bool, reserve: bool) -> str | None:
    """Siniflar ve etiket deseni -> "national" / "women" / "reserve" / None (A takim sayilir)."""
    if national:
        return "national"
    if women or TEAM_LABEL_PATTERNS["women"].search(label):
        return "women"
    if reserve or TEAM_LABEL_PATTERNS["reserve"].search(label):
        return "reserve"
    return None


def detail_rows(client: Wikidata, players: list[str]) -> list[dict]:
    """Ad (etiketler), uyruk (P27 + sirasi), spor ulkesi (P1532), mevki (P413), is donemi sonu (P2032),
    teknik direktorluk baslangici (P6087 + P580)."""
    langs = ", ".join(f'"{lang}"' for lang in LABEL_LANGS)
    rows: list[dict] = []
    for chunk in _chunks(sorted(set(players), key=_qid_key), PLAYER_BATCH):
        rows += client.sparql(f"""
        SELECT ?player (GROUP_CONCAT(DISTINCT ?lab; separator="||") AS ?labels)
               (GROUP_CONCAT(DISTINCT ?cit; separator=" ") AS ?cits)
               (GROUP_CONCAT(DISTINCT ?pcit; separator=" ") AS ?preferred)
               (GROUP_CONCAT(DISTINCT ?sport; separator=" ") AS ?sports)
               (GROUP_CONCAT(DISTINCT ?pos; separator=" ") AS ?positions)
               (MAX(YEAR(?wpe)) AS ?workEnd) (MAX(?cstart) AS ?coachFrom) WHERE {{
          VALUES ?player {{ {_values(chunk)} }}
          OPTIONAL {{ ?player rdfs:label ?l . FILTER(LANG(?l) IN ({langs}))
                     BIND(CONCAT(LANG(?l), "=", STR(?l)) AS ?lab) }}
          OPTIONAL {{ ?player p:P27 ?cs . ?cs ps:P27 ?ci ; wikibase:rank ?cr .
                     FILTER(?cr != wikibase:DeprecatedRank)
                     BIND(STRAFTER(STR(?ci), "entity/") AS ?cit)
                     BIND(IF(?cr = wikibase:PreferredRank, ?cit, "") AS ?pcit) }}
          OPTIONAL {{ ?player wdt:P1532 ?si . BIND(STRAFTER(STR(?si), "entity/") AS ?sport) }}
          OPTIONAL {{ ?player wdt:P413 ?pi . BIND(STRAFTER(STR(?pi), "entity/") AS ?pos) }}
          OPTIONAL {{ ?player wdt:P2032 ?wpe }}
          OPTIONAL {{ ?player p:P6087 ?coach . ?coach pq:P580 ?cstart }}
        }} GROUP BY ?player
        """)
    return rows


def position_classes(client: Wikidata, positions: list[str]) -> dict[str, str | None]:
    """Mevki ogesi -> GK/DEF/MID/FWD (P279* kok ogelere gore), bilinmiyorsa None."""
    out: dict[str, str | None] = {}
    roots = POSITION_ROOTS
    for chunk in _chunks(sorted(set(positions), key=_qid_key), ITEM_BATCH):
        for row in client.sparql(f"""
        SELECT ?pos ?gk ?df ?mf ?fw WHERE {{
          VALUES ?pos {{ {_values(chunk)} }}
          BIND(EXISTS {{ ?pos wdt:P279* wd:{roots['GK']} }} AS ?gk)
          BIND(EXISTS {{ ?pos wdt:P279* wd:{roots['DEF']} }} AS ?df)
          BIND(EXISTS {{ ?pos wdt:P279* wd:{roots['MID']} }} AS ?mf)
          BIND(EXISTS {{ ?pos wdt:P279* wd:{roots['FWD']} }} AS ?fw)
        }}
        """):
            hits = [cat for cat, var in (("GK", "gk"), ("DEF", "df"), ("MID", "mf"), ("FWD", "fw"))
                    if row.get(var) == "true"]
            out[row["pos"]] = hits[0] if len(hits) == 1 else None
    for qid in positions:
        if out.get(qid) is None and qid in POSITION_EXTRA:
            out[qid] = POSITION_EXTRA[qid]
    return out


def country_labels(client: Wikidata, qids: list[str]) -> dict[str, str]:
    """Ulke ogesi -> Ingilizce etiket (oyun national_rules takma adlariyla Turkce ada cevirir)."""
    out: dict[str, str] = {}
    for chunk in _chunks(sorted(set(qids), key=_qid_key), ITEM_BATCH):
        for row in client.sparql(f"""
        SELECT ?c ?label WHERE {{
          VALUES ?c {{ {_values(chunk)} }}
          ?c rdfs:label ?label . FILTER(LANG(?label) = "en")
        }}
        """):
            out[row["c"]] = row["label"]
    return out


# ===========================================================================
# 5) DERLEME (SAF: ag yok, testler sahte satirlarla calistirir)
# ===========================================================================

def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def age_on(birth: date, reference: date) -> int:
    return reference.year - birth.year - ((reference.month, reference.day) < (birth.month, birth.day))


def pick_nationality(detail: dict) -> str | None:
    """P1532 > tercihli P27 > tek P27 > en kucuk QID'li P27 (deterministik)."""
    sports = sorted(set((detail.get("sports") or "").split()), key=_qid_key)
    cits = sorted(set((detail.get("cits") or "").split()), key=_qid_key)
    preferred = sorted(set((detail.get("preferred") or "").split()), key=_qid_key)
    if sports:
        common = [s for s in sports if s in cits]
        return (common or sports)[0]
    if len(preferred) == 1:
        return preferred[0]
    return cits[0] if cits else None


def pick_position(qids: list[str], classes: dict[str, str | None]) -> str | None:
    """Oy sayimi: ozgul mevki (stoper, kanat...) 2, kok oge (defans...) 1 puan; esitlikte POSITION_ORDER."""
    votes: Counter[str] = Counter()
    roots = set(POSITION_ROOTS.values())
    for qid in qids:
        cat = classes.get(qid)
        if cat:
            votes[cat] += 1 if qid in roots else 2
    if not votes:
        return None
    best = max(votes.values())
    return next(cat for cat in POSITION_ORDER if votes.get(cat) == best)


def pick_name(labels: str, country: str) -> str | None:
    by_lang: dict[str, str] = {}
    for part in (labels or "").split("||"):
        lang, _, text = part.partition("=")
        text = " ".join(text.split())
        if text and lang not in by_lang:
            by_lang[lang] = text
    for lang in ("en", "mul", COUNTRY_LANG.get(country, "en"), *LABEL_LANGS):
        if by_lang.get(lang):
            return by_lang[lang][:80]
    return None


def _number(text: str | None) -> int | None:
    try:
        value = int(float(text or ""))
    except ValueError:
        return None
    return value if 1 <= value <= 99 else None


def _statements(rows: list[dict]) -> list[dict]:
    """
    Ayni uyelik ifadesinin (?st) OPTIONAL niteleyicilerle cogalan satirlarini tek kayda indirir:
    en buyuk P585 (pit), P1350 var mi (matches), kiralik mi, kadin futbolu sinifi, forma numaralari.
    """
    merged: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: json.dumps(r, sort_keys=True)):
        key = row.get("st") or f"{row.get('player')}|{row.get('start')}"
        entry = merged.setdefault(key, {"start": (row.get("start") or "")[:10], "dob": (row.get("dob") or "")[:10],
                                        "sl": int(float(row.get("sl") or 0)), "numbers": set(), "acqs": set(),
                                        "classes": set(), "pit": "", "matches": False})
        number = _number(row.get("number"))
        if number:
            entry["numbers"].add(number)
        entry["acqs"].add(row.get("acq") or "")
        entry["classes"].add(row.get("cls") or "")
        entry["pit"] = max(entry["pit"], (row.get("pit") or "")[:10])
        entry["matches"] = entry["matches"] or bool(row.get("matches"))
    return sorted(merged.values(), key=lambda e: (e["start"], e["pit"], sorted(e["numbers"])))


def freshness(statement: dict, reference: date) -> int:
    """
    Uyeligin guncel oldugunun Wikidata'daki kaniti (guven sirasi icin; tek basina dislamaz):
        2  son FRESH_MONTHS ayda P585 (istatistik tarihi) ya da son RECENT_START_YEARS yilda baslamis
        1  P1350 (mac sayisi) niteleyicisi var (ifade bakimli)
        0  hicbiri (eski toplu aktarim olabilir) -- baslangic tarihi olmayan uyelik de burada
    """
    if not statement["start"]:
        return 0
    months = (reference.year * 12 + reference.month - 1) - FRESH_MONTHS
    fresh_from = date(months // 12, months % 12 + 1, 1).isoformat()
    recent_from = date(reference.year - RECENT_START_YEARS, reference.month, 1).isoformat()
    if (statement["pit"] and statement["pit"] >= fresh_from) or statement["start"] >= recent_from:
        return 2
    return 1 if statement["matches"] else 0


CONFIDENCE = {2: "high", 1: "medium", 0: "low"}


def assemble(
    clubs: list[ClubRef],
    squads: list[dict],
    memberships: list[dict],
    ignored_teams: dict[str, str] | set[str],
    details: list[dict],
    positions: dict[str, str | None],
    countries: dict[str, str],
    reference: date,
    fetched_at: str,
) -> dict:
    """
    Ham satirlardan squads.json belgesi (SAF). GUNCEL KULUP (16G ikinci gecis): oyuncunun ACIK uyelikleri (P582 yok ya
    da gelecekte) arasinda en GEC baslayani; milli / kadin / B-altyapi takimlari (ignored_teams) ve kiraliklar
    (P1642 = loan) secimde YOK SAYILIR. Uzun sure ayni kulupte kalan (eski baslangic, bitis yok) oyuncu korunur;
    yas tek basina hicbir oyuncuyu dislamaz. Dislama nedenleri sayilir ve belgeye yazilir:
        women     uyelikte P2094 kadin futbolu
        loan      kulupteki guncel uyeligi yalnizca kiralik (oyuncu ana kulubunde sayilir)
        moved     baska bir takimda DAHA GEC baslamis acik uyelik var; iki kulubumuzde birden -> yeni baslangicli
                  olan; baslangicsiz uyelikte: oyuncunun baska TARIHLI acik uyeligi var
        left      (kanit kurali) daha gec baslamis ama BITMIS bir donem var ve kulup ifadesinde hic tazelik kaniti
                  yok (freshness 0): uyelik bayat -- yakinda (RETURN_YEARS) biten donem kiralik donusu sayilir, dislamaz
        age       referans tarihinde MIN_AGE-MAX_AGE disi
        retired   is donemi bitmis (P2032) ya da uyelikten SONRA teknik direktorluge baslamis (P6087)
        no_name   hicbir dilde etiket yok
    Guven: high (freshness 2) / medium (1) / low (0 ya da baslangic tarihi yok). Kadro tavani oyunda (open_loader)
    guvene, sonra en yeni baslangica, sonra yas <= 38'e gore uygulanir.
    """
    by_qid = {c.qid: c for c in clubs if c.qid}
    detail = {row["player"]: row for row in details}
    ignored = set(ignored_teams)
    ref = reference.isoformat()

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in squads:
        if row.get("club") in by_qid and row.get("player"):
            grouped.setdefault((row["club"], row["player"]), []).append(row)

    # oyuncu -> uyelikler: (baslangic, takim, bitis) -- milli / kadin / B takimi ve kiralik haric
    history: dict[str, list[tuple[str, str, str]]] = {}
    for row in memberships:
        if row.get("team") in ignored or row.get("acq") == LOAN:
            continue
        history.setdefault(row["player"], []).append(((row.get("start") or "")[:10], row["team"],
                                                      (row.get("end") or "")[:10]))

    def is_open(end: str) -> bool:
        return not end or end > ref

    excluded: dict[str, Counter[str]] = {c.id: Counter() for c in clubs}
    chosen: dict[str, tuple[str, dict]] = {}          # oyuncu -> (kulup QID, secilen ifade)
    returned_from = date(reference.year - RETURN_YEARS, reference.month, 1).isoformat()
    for (club_qid, player), rows in sorted(grouped.items(), key=lambda kv: (_qid_key(kv[0][1]), kv[0][0])):
        club = by_qid[club_qid]
        statements = _statements(rows)
        if any(WOMENS_FOOTBALL in st["classes"] for st in statements):
            excluded[club.id]["women"] += 1
            continue
        own = [st for st in statements if LOAN not in st["acqs"]]
        if not own:
            excluded[club.id]["loan"] += 1
            continue
        statement = own[-1]                            # en gec baslangicli (baslangicsizler basta siralanir)
        statement["fresh"] = fresh = freshness(statement, reference)
        start = statement["start"]
        elsewhere = [(s, end) for s, t, end in history.get(player, []) if t != club_qid]
        dated_open = [s for s, end in elsewhere if s and is_open(end)]
        if (start and any(s > start for s in dated_open)) or (not start and dated_open):
            excluded[club.id]["moved"] += 1
            continue
        later_closed = sorted((s, end) for s, end in elsewhere if start and s > start and not is_open(end))
        if fresh == 0 and later_closed and later_closed[-1][1] < returned_from:
            excluded[club.id]["left"] += 1
            continue
        birth = _date(statement["dob"])
        age = age_on(birth, reference) if birth else None
        if age is None or not MIN_AGE <= age <= MAX_AGE:
            excluded[club.id]["age"] += 1
            continue
        info = detail.get(player, {})
        work_end = str(info.get("workEnd") or "")
        coach_from = (info.get("coachFrom") or "")[:10]
        if (work_end.isdigit() and int(work_end) <= reference.year) or (coach_from and coach_from > (start or "")):
            excluded[club.id]["retired"] += 1
            continue
        previous = chosen.get(player)
        if previous is not None:                       # iki kulubumuzde birden: daha yeni baslangic kazanir
            if previous[1]["start"] >= start:
                excluded[club.id]["moved"] += 1
                continue
            excluded[by_qid[previous[0]].id]["moved"] += 1
        chosen[player] = (club_qid, statement)

    squads_out: dict[str, dict] = {}
    unknown_pos = unknown_nat = 0
    for club in clubs:
        players = []
        for player, (club_qid, statement) in chosen.items():
            if club_qid != club.qid:
                continue
            info = detail.get(player, {})
            name = pick_name(info.get("labels", ""), club.country)
            if not name:
                excluded[club.id]["no_name"] += 1
                continue
            pos_qids = sorted(set((info.get("positions") or "").split()), key=_qid_key)
            position = pick_position(pos_qids, positions)
            nat_qid = pick_nationality(info)
            unknown_pos += position is None
            unknown_nat += nat_qid is None
            confidence = CONFIDENCE[statement["fresh"]] if statement["start"] else "low"
            players.append({
                "qid": player,
                "name": name,
                "birth_date": statement["dob"],
                "nationality": countries.get(nat_qid) if nat_qid else None,
                "nationality_qid": nat_qid,
                "position": position,
                "positions": pos_qids,
                "shirt_number": min(statement["numbers"]) if statement["numbers"] else None,
                "since": statement["start"],
                "fresh": statement["fresh"],
                "confidence": confidence,
                "sitelinks": statement["sl"],
            })
        players.sort(key=lambda p: (-p["fresh"], p["since"] == "", -p["sitelinks"], _qid_key(p["qid"])))
        squads_out[club.id] = {
            "name": club.name, "league": club.league, "tier": club.tier, "wikidata": club.qid,
            "players": players,
            "excluded": dict(sorted(excluded[club.id].items())),
        }

    by_league: dict[str, list[int]] = {}
    for club in clubs:
        by_league.setdefault(club.league, []).append(len(squads_out[club.id]["players"]))
    totals: Counter[str] = Counter()
    for counter in excluded.values():
        totals.update(counter)
    confidence_counts = Counter(p["confidence"] for s in squads_out.values() for p in s["players"])
    coverage = {
        "clubs": len(clubs),
        "clubs_without_wikidata": sorted(c.id for c in clubs if not c.qid),
        "players": sum(len(s["players"]) for s in squads_out.values()),
        "by_league": {code: {"clubs": len(sizes), "players": sum(sizes), "min": min(sizes), "max": max(sizes),
                             "median": statistics.median(sizes)}
                      for code, sizes in sorted(by_league.items())},
        "excluded": dict(sorted(totals.items())),
        "confidence": {k: confidence_counts[k] for k in ("high", "medium", "low")},
        "position_unknown": unknown_pos,
        "nationality_unknown": unknown_nat,
    }
    return {
        "schema": SCHEMA, "version": 2,
        "license": LICENSE, "license_url": LICENSE_URL,
        "source": "Wikidata", "source_url": "https://www.wikidata.org/",
        "personal_data": True,
        "notice": NOTICE,
        "fetched_at": fetched_at,
        "generator": "tools/build_squads.py",
        "reference_date": reference.isoformat(),
        "rules": {"current_club": "latest-started open membership (no/future P582), national/women/reserve and "
                                  "loan memberships ignored",
                  "min_start_year": reference.year - START_WINDOW_YEARS, "min_age": MIN_AGE, "max_age": MAX_AGE,
                  "fresh_months": FRESH_MONTHS, "recent_start_years": RECENT_START_YEARS,
                  "return_years": RETURN_YEARS, "loan": LOAN, "national_team": NATIONAL_TEAM},
        "coverage": coverage,
        "clubs": squads_out,
    }


def build(client: Wikidata, clubs: list[ClubRef], reference: date, quiet: bool = False) -> dict:
    """
    Sorgu sirasi. Tarihli-bitissiz uyelik sorgusu (squad_rows) ilk gecisle AYNI metindir, onbellekten gelir; ikinci
    gecisin ek adaylari (baslangicsiz / bitisi gelecekte) ayri VALUES bloklarinda sorulur: onbellek bozulmaz.
    Oyuncu listesi her durumda deterministiktir (siralanmis).
    """
    qids = [c.qid for c in clubs if c.qid]
    if not quiet:
        print(f"[kadro] {len(qids)} kulüp için güncel üyelikler sorgulanıyor", flush=True)
    base_rows = squad_rows(client, qids, reference)
    extra_rows = extra_squad_rows(client, qids, reference)
    base = sorted({r["player"] for r in base_rows}, key=_qid_key)
    extra = sorted({r["player"] for r in extra_rows} - set(base), key=_qid_key)
    if not quiet:
        print(f"[kadro] {len(base)} + {len(extra)} aday oyuncu; tüm üyelikleri ve ayrıntıları okunuyor", flush=True)
    memberships = membership_rows(client, base) + membership_rows(client, extra)
    # siniflandirma yalnizca karari etkileyebilecek takimlar icin: acik uyelikler ve pencere icinde baslayanlar
    min_start = f"{reference.year - START_WINDOW_YEARS}-01-01"
    teams = sorted({r["team"] for r in memberships if r.get("team") and
                    (not r.get("end") or r["end"][:10] > reference.isoformat() or (r.get("start") or "") >= min_start)},
                   key=_qid_key)
    ignored = team_classes(client, teams)
    details = detail_rows(client, base) + detail_rows(client, extra)
    pos_qids = sorted({q for row in details for q in (row.get("positions") or "").split()}, key=_qid_key)
    positions = position_classes(client, pos_qids)
    nat_qids = sorted({q for row in details
                       for q in f"{row.get('cits') or ''} {row.get('sports') or ''}".split()}, key=_qid_key)
    countries = country_labels(client, nat_qids)
    return assemble(clubs, base_rows + extra_rows, memberships, ignored, details, positions, countries,
                    reference, client.fetched_at)


def write_json(path: Path, doc: dict) -> int:
    path = check_output_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Wikidata (CC0) güncel A takım kadrolarından YEREL data/local/squads.json üretir "
                    "(kişisel veri; depoya girmez).")
    parser.add_argument("--open-dir", type=Path, default=OPEN_DIR, help="leagues.json / club_colors.json klasörü.")
    parser.add_argument("--out", type=Path, default=LOCAL_DIR / OUT_FILE, help="Çıktı dosyası (data/local/).")
    parser.add_argument("--cache", type=Path, default=CACHE_DIR, help="Önbellek klasörü (data/local/).")
    parser.add_argument("--offline", action="store_true", help="İnternete çıkma, yalnızca önbelleği kullan.")
    parser.add_argument("--season-year", type=int, default=DEFAULT_SEASON_YEAR,
                        help="Referans tarihi <yıl>-07-01 (yaş ve başlangıç penceresi).")
    parser.add_argument("--tiers", default="1", help="Kademeler, virgülle (varsayılan 1).")
    parser.add_argument("--clubs", nargs="*", default=(), help="Yalnızca bu açık veri kulüp kimlikleri.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        out = check_output_path(args.out)
        check_output_path(args.cache)
        tiers = tuple(int(t) for t in str(args.tiers).split(",") if t.strip())
        clubs = load_clubs(args.open_dir, tiers, tuple(args.clubs))
        if not clubs:
            raise BuildError("Seçilen kademe/kulüp bulunamadı.")
    except (BuildError, ValueError) as exc:
        print(f"[kadro] HATA: {exc}")
        return 1
    client = Wikidata(args.cache, offline=args.offline, quiet=args.quiet)
    reference = date(args.season_year, 7, 1)
    try:
        doc = build(client, clubs, reference, quiet=args.quiet)
    except BuildError as exc:
        client.save(prune=False)                    # yarim kalan calismanin indirdikleri kaybolmasin
        print(f"[kadro] HATA: {exc}")
        return 1
    client.save()
    size = write_json(out, doc)
    cov = doc["coverage"]
    print(f"[kadro] {cov['players']} oyuncu / {cov['clubs']} kulüp (dışlanan: {cov['excluded']}); "
          f"mevkisi bilinmeyen {cov['position_unknown']}, uyruğu bilinmeyen {cov['nationality_unknown']}; "
          f"{client.requests} ağ isteği -> {out} ({size / 1024:.0f} KB, {doc['fetched_at']})")
    for code, row in cov["by_league"].items():
        print(f"         {code}: {row['players']} oyuncu, kulüp başına {row['min']}-{row['max']} "
              f"(medyan {row['median']})")
    print("[kadro] UYARI: dosya KİŞİSEL VERİ içerir; depoya ekleme, paylaşma (data/local/ gitignore'dadır).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
