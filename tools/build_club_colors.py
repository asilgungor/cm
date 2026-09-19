"""
tools/build_club_colors.py
==========================
GELISTIRICI ARACI (14S). Oyunun CALISMA ZAMANINDA kullanilmaz.

Acik veri kuluplerinin (data/open/clubs.json) RESMI RENKLERINI Wikidata'dan (CC0 1.0) alir ve

    data/open/club_colors.json
        clubs:          {kulup kimligi: {name, wikidata, colors ["#RRGGBB"...], source, license,
                         + denetim alanlari: wikidata_label, match, color_names, [colors_from],
                         + arama adlari: names (oyundaki/DB adlari + resmi ad), aliases}}
        without_colors: {kulup kimligi: {name, wikidata|null, reason, names, aliases}}
                        (renksiz kuluplerin adlari da yazilir: club_colors.py onlari baska kulube vermez)

dosyasini uretir. Dosyayi oyun `club_colors.py` uzerinden okur (baslik bandi: CM 01/02 gorunumu).

Kaynak (yalnizca Wikidata, CC0 1.0):
    P6364  "official color"               kulubun resmi renkleri (renk ogeleri, SIRALI)
    P465   "sRGB color hex triplet"       renk ogesinin onaltilik kodu ("000080")
    P31/P279*  Q476028                    "association football club" tur denetimi
    (P462 "color" bilerek KULLANILMAZ: eski/tutarsiz; orn. ChievoVerona icin beyaz-mavi.)

Esleme (ayrinti: `rank`, `choose`):
    1) clubs.json'da Wikidata kimligi YOK; ileride `wikidata` alani gelirse ya da OVERRIDES'a elle
       dogrulanmis kimlik yazilirsa o kullanilir (yine tur/ulke denetiminden gecer). Aksi halde ad
       ile aranir.
    2) ARAMA: www.wikidata.org wbsearchentities, kulubun ulke dilinde; terimler once oyundaki ad
       (leagues.json kadro/tablo adlari) ve clubs.json adi, bulunamazsa takma adlar.
    3) DOGRULAMA: aday ogeler toplu SPARQL (VALUES bloklari) ile denetlenir; aday ancak
       - futbol kulubuyse (P31/P279* Q476028) ya da futbol oynayan spor kulubuyse
         (P31/P279* Q847017 + P641 Q2736; orn. FC Erzgebirge Aue),
       - kadin/milli/plaj futbolu takimi DEGILSE,
       - ulkesi (P17) kulubun ulkesiyse (ya da P17 hic yoksa, dusuk oncelikle) kabul edilir.
    4) SECIM (puan): tam ad 3 / takma ad 2 / onek 1, +2 kurulus yili (P571 == clubs.json founded),
       +1 dogrudan futbol kulubu turu, -2 taslak oge (< 3 site baglantisi). Puan < 2 ise esleme YOK
       (yanlis renk, renksizden kotudur). Esitlikte ulke, faal olma, site baglantisi. Ayni Wikidata
       ogesi iki kulube cikarsa zayif esleme dusurulur.
    5) RENK: ogenin P6364 ifadeleri SIRASIYLA (wbgetclaims; SPARQL sirayi korumaz). Kaldirilmis
       (deprecated) ve bitis tarihi (P582) olan ifadeler atilir; tercihli (preferred) ifade varsa
       yalnizca onlar alinir. Futbol ogesinde renk yoksa bagli oldugu spor kulubunun (P361/P749;
       orn. Fenerbahce SK) renkleri kullanilir (`colors_from`).

Elle derlenmis yedek (data/open/club_colors_curated.json, `source: "curated"`):
    ONCELIK Wikidata'dadir. Yedek YALNIZCA Wikidata rengi olmayan kulube uygulanir; Wikidata rengi
    olan kulupteki yedek girdisi yok sayilir ve `curated_ignored` basligina yazilir. Yedek dosya
    elle tutulur (1. kademede Wikidata'nin kapsamadigi kulupler; yalnizca yaygin bilinen forma/arma
    renkleri, emin olunmayan kulup `omitted` altinda); arac onu yalnizca okur, ag gerekmez.

KURALLAR (bilerek dar tutuldu):
    * yalnizca query.wikidata.org/sparql ve www.wikidata.org/w/api.php (ALLOWED_PREFIXES);
      baska hicbir kaynak (SI/FM verisi, Transfermarkt, Kaggle, sortitoutsi, kulup siteleri) YOK;
    * yanitlar yalnizca JSON olarak ayristirilir, hicbir sey CALISTIRILMAZ;
    * tanimlayici User-Agent, toplu SPARQL (VALUES), istekler arasinda bekleme, 429/503'te
      Retry-After'a uyulur; istekler SERIDIR (paralel istek yok);
    * uretilen dosyaya kaynak, lisans ve alim tarihi yazilir.

Onbellek (tekrar uretilebilirlik): data/open/_cache_colors/ altinda asama basina bir JSON
(search.json, sparql.json, claims.json, meta.json). Ham yanitin YALNIZCA kullanilan alanlari tutulur,
satir basina bir girdi; toplam ~400 KB, depoya girebilir. Basarili calismada yalnizca kullanilan
girdiler yazilir (eskiyenler temizlenir).

Kullanim:
    python tools/build_club_colors.py              # onbellekte olmayani indir + yaz
    python tools/build_club_colors.py --offline    # yalnizca onbellekten (eksik girdi = hata)
    python tools/build_club_colors.py --out /tmp   # baska klasore yaz
Guncellemek icin onbellek klasorunu silip cevrimici calistir. `fetched_at`, onbellegin son ag alimi
tarihidir (meta.json): --offline uretim birebir ayni dosyayi verir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent
OPEN_DIR = ROOT / "data" / "open"
OUT_FILE = "club_colors.json"
CURATED_FILE = "club_colors_curated.json"   # elle derlenmis yedek (Wikidata rengi yoksa)
CACHE_DIR = OPEN_DIR / "_cache_colors"

SCHEMA = "ofm/open-club-colors"
CURATED_SCHEMA = "ofm/open-club-colors-curated"
CURATED_SOURCE = "curated"
LICENSE = "CC0-1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ENTRY_SOURCE, ENTRY_LICENSE = "wikidata", "CC0"

SPARQL_URL = "https://query.wikidata.org/sparql"
API_URL = "https://www.wikidata.org/w/api.php"
# Baska hicbir adres istenmez (ag korumasi, build_open_data.py ile ayni yaklasim).
ALLOWED_PREFIXES = (SPARQL_URL, API_URL)
ALLOWED_HOSTS = frozenset({"query.wikidata.org", "www.wikidata.org"})

USER_AGENT = ("OFM-club-colors/1.0 (https://github.com/asilgungor/cm; tools/build_club_colors.py; "
              "one-off developer tool) python-requests")

API_PAUSE = 0.35            # sn; www.wikidata.org API istekleri arasi
SPARQL_PAUSE = 1.5          # sn; query.wikidata.org istekleri arasi
MAX_RETRIES = 4
SPARQL_BATCH = 200          # VALUES blogu basina oge
SEARCH_LIMIT = 20

FOOTBALL_CLUB = "Q476028"                    # association football club
EXCLUDED_TYPES = ("Q51481377",               # women's association football club
                  "Q135408445",              # men's national association football team
                  "Q116953048")              # beach soccer club
WOMENS_FOOTBALL = "Q606060"                  # P2094 (competition class): women's association football
DEFUNCT_CLUB = "Q94579592"                   # defunct association football club
SPORTS_CLUB = "Q847017"                      # sports club: renk yedegi ebeveyni + cok bransli kulup
FOOTBALL_SPORT = "Q2736"                     # P641 (sport): association football

# clubs.json ulke kodu -> (arama dili, kabul edilen P17 degerleri)
COUNTRIES: dict[str, tuple[str, frozenset[str]]] = {
    "tr": ("tr", frozenset({"Q43"})),
    "en": ("en", frozenset({"Q145", "Q21", "Q25"})),            # BK, Ingiltere, Galler (Swansea, Cardiff)
    "es": ("es", frozenset({"Q29", "Q228"})),                   # Ispanya, Andorra (FC Andorra, La Liga 2)
    "de": ("de", frozenset({"Q183"})),
    "it": ("it", frozenset({"Q38"})),
    "fr": ("fr", frozenset({"Q142"})),
    "mc": ("fr", frozenset({"Q235", "Q142"})),                  # AS Monaco
}

# Elle dogrulanmis kimlikler (kulup kimligi -> Wikidata QID). Aramadan ONCE kullanilir ama yine de
# tur/ulke denetiminden gecer. clubs.json kendi kimlik alanini tasimaz; bu tablo bos baslar.
OVERRIDES: dict[str, str] = {}

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_QID = re.compile(r"^Q\d+$")

NOTICE = (
    "Kulüp resmi renkleri Wikidata'dan alınmıştır (P6364 'official color', renk kodu P465 "
    "'sRGB color hex triplet'; CC0 1.0, kamu malı). Yalnızca olgusal renk kodu; arma, logo ya da "
    "forma görseli içermez. Hiçbir Football Manager / Transfermarkt / Kaggle / sortitoutsi verisi "
    "ve kulüp sitesi kullanılmamıştır."
)


class BuildError(Exception):
    """Arac calisamadi."""


# ===========================================================================
# 1) AD ANAHTARI
# ===========================================================================

def ascii_key(text: str) -> str:
    """Aksansiz, noktalamasiz karsilastirma anahtari (build_open_data.ascii_key ile ayni)."""
    text = (text or "").replace("ı", "i").replace("İ", "i").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def _unique(forms) -> list[str]:
    """Anahtara gore tekillestirir, ilk yazimi ve sirayi korur."""
    seen: set[str] = set()
    out: list[str] = []
    for form in forms:
        form = " ".join((form or "").split())
        key = ascii_key(form)
        if key and key not in seen:
            seen.add(key)
            out.append(form)
    return out


# ===========================================================================
# 2) AG + ONBELLEK
# ===========================================================================

def _http(method: str, url: str, params: dict, timeout: int) -> tuple[int, str, str | None]:
    """(durum, govde, Retry-After). requests varsa onu, yoksa stdlib'i kullanir."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        import requests
    except ImportError:
        import urllib.error
        import urllib.request
        data = None
        full = url
        if method == "POST":
            data = urlencode(params).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            full = f"{url}?{urlencode(params)}"
        request = urllib.request.Request(full, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8"), None
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), exc.headers.get("Retry-After")
    if method == "POST":
        response = requests.post(url, data=params, headers=headers, timeout=timeout)
    else:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.encoding = "utf-8"
    return response.status_code, response.text, response.headers.get("Retry-After")


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if url not in ALLOWED_PREFIXES or parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise BuildError(f"İzin verilmeyen adres: {url}")


class Wikidata:
    """
    Wikidata istemcisi + asama basina JSON onbellek. Onbellekte olan istek aga CIKMAZ.
    offline=True iken onbellekte olmayan istek BuildError verir (sessizce eksik veri uretilmez).
    """

    STORES = ("search", "sparql", "claims")

    def __init__(self, cache: Path, offline: bool = False, timeout: int = 90,
                 quiet: bool = False) -> None:
        self.cache = cache
        self.offline = offline
        self.timeout = timeout
        self.quiet = quiet
        self.requests = 0
        self._last: dict[str, float] = {}
        self._data: dict[str, dict] = {}
        self._used: dict[str, dict] = {name: {} for name in self.STORES}
        for name in (*self.STORES, "meta"):
            path = cache / f"{name}.json"
            self._data[name] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    @property
    def fetched_at(self) -> str:
        """Son ag alimi tarihi (onbellekten uretimde degismez: cevrimdisi cikti birebir aynidir)."""
        if self.requests:
            return date.today().isoformat()
        return self._data["meta"].get("fetched_at") or date.today().isoformat()

    # --- onbellek ---------------------------------------------------------
    def _cached(self, store: str, key: str):
        if key in self._data[store]:
            value = self._data[store][key]
            self._used[store][key] = value
            return True, value
        if self.offline:
            raise BuildError(f"--offline: önbellekte yok ({store}: {key[:80]})")
        return False, None

    def _remember(self, store: str, key: str, value) -> None:
        self._data[store][key] = value
        self._used[store][key] = value

    def save(self, prune: bool = True) -> None:
        """
        prune=True (basarili calisma): yalnizca bu calismada kullanilan girdiler yazilir, onbellek
        kucuk ve kararli kalir. prune=False (hata): eldeki her sey korunur.
        """
        if self.offline:
            return
        self.cache.mkdir(parents=True, exist_ok=True)
        docs = {name: (self._used if prune else self._data)[name] for name in self.STORES}
        docs["meta"] = {"fetched_at": self.fetched_at, "user_agent": USER_AGENT,
                        "endpoints": list(ALLOWED_PREFIXES)}
        for name, doc in docs.items():
            lines = [f"{json.dumps(key, ensure_ascii=False)}: "
                     f"{json.dumps(doc[key], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                     for key in sorted(doc)]
            text = "{\n" + ",\n".join(lines) + "\n}\n"          # satir basina bir girdi: kucuk, diff dostu
            (self.cache / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")

    # --- ag -----------------------------------------------------------------
    def _fetch_json(self, method: str, url: str, params: dict, pause: float) -> dict:
        _check_url(url)
        for attempt in range(MAX_RETRIES + 1):
            wait = pause - (time.monotonic() - self._last.get(url, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                status, body, retry_after = _http(method, url, params, self.timeout)
            except Exception as exc:                          # zaman asimi / baglanti
                status, body, retry_after = 0, str(exc), None
            self._last[url] = time.monotonic()
            self.requests += 1
            if status == 200:
                try:
                    doc = json.loads(body)                    # yalnizca ayristirilir
                except ValueError as exc:
                    raise BuildError(f"JSON değil ({url}): {body[:120]!r}") from exc
                error = doc.get("error") if isinstance(doc, dict) else None
                if error:
                    raise BuildError(f"Wikidata API hatası: {error}")
                return doc
            if status in (0, 429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                delay = float(retry_after) if (retry_after or "").isdigit() else 5.0 * (attempt + 1)
                if not self.quiet:
                    print(f"  ~ {status or 'bağlantı'}; {delay:.0f} sn sonra yeniden ({url})")
                time.sleep(min(delay, 120.0))
                continue
            raise BuildError(f"{url}: HTTP {status}: {body[:200]!r}")
        raise BuildError(f"{url}: yeniden denemeler tükendi")

    # --- istekler -------------------------------------------------------------
    def search(self, term: str, language: str) -> list[dict]:
        """
        wbsearchentities (etiket/takma ad ONEK aramasi): [{id, label, match_type, match_text}].
        Onbellekte satir basina [id, esleme turu, eslesen metin, etiket ("" = eslesen metinle ayni)].
        """
        key = f"{language}|{term}"
        hit, rows = self._cached("search", key)
        if not hit:
            doc = self._fetch_json("GET", API_URL, {
                "action": "wbsearchentities", "search": term, "language": language, "uselang": language,
                "type": "item", "limit": SEARCH_LIMIT, "format": "json",
            }, API_PAUSE)
            rows = []
            for item in doc.get("search", []):
                match = item.get("match") or {}
                text, label = match.get("text") or "", item.get("label") or ""
                rows.append([item.get("id"), match.get("type") or "", text, "" if label == text else label])
            self._remember("search", key, rows)
        return [{"id": qid, "match_type": kind, "match_text": text, "label": label or text}
                for qid, kind, text, label in rows]

    def sparql(self, query: str) -> list[dict]:
        """SELECT sonucu: [{degisken: deger}] (URI'ler kisa kimlige indirgenir)."""
        query = "\n".join(line.strip() for line in query.strip().splitlines() if line.strip())
        key = hashlib.sha1(query.encode("utf-8")).hexdigest()
        hit, value = self._cached("sparql", key)
        if hit:
            return value["rows"]
        doc = self._fetch_json("POST", SPARQL_URL, {"query": query, "format": "json"}, SPARQL_PAUSE)
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
        self._remember("sparql", key, {"head": query.splitlines()[0][:120], "rows": rows})
        return rows

    def claims(self, qid: str, prop: str = "P6364") -> list[dict]:
        """wbgetclaims: ifadeler KAYITLI SIRAYLA [{value, rank, qualifiers: {P: [deger]}}]."""
        if not _QID.match(qid):
            raise BuildError(f"Geçersiz öğe: {qid}")
        key = f"{qid}|{prop}"
        hit, value = self._cached("claims", key)
        if hit:
            return value
        doc = self._fetch_json("GET", API_URL, {
            "action": "wbgetclaims", "entity": qid, "property": prop, "format": "json",
        }, API_PAUSE)
        rows = []
        for claim in doc.get("claims", {}).get(prop, []):
            snak = claim.get("mainsnak") or {}
            datavalue = (snak.get("datavalue") or {}).get("value")
            qualifiers = {}
            for pid, snaks in sorted((claim.get("qualifiers") or {}).items()):
                values = []
                for q in snaks:
                    raw = (q.get("datavalue") or {}).get("value")
                    if isinstance(raw, dict):
                        raw = raw.get("id") or raw.get("time") or json.dumps(raw, sort_keys=True)
                    values.append(raw if raw is not None else q.get("snaktype"))
                qualifiers[pid] = values
            rows.append({
                "value": datavalue.get("id") if isinstance(datavalue, dict) else None,
                "rank": claim.get("rank", "normal"),
                "qualifiers": qualifiers,
            })
        self._remember("claims", key, rows)
        return rows


# ===========================================================================
# 3) KULUPLER
# ===========================================================================

@dataclass
class Club:
    id: str
    name: str                                   # clubs.json adi
    country: str
    founded: int | None
    game_names: list[str]                       # leagues.json kadro + tablo adlari (DB'deki ad)
    aliases: list[str]
    wikidata: str | None = None                 # clubs.json'da kimlik alani varsa (su an yok)
    terms: list[str] = field(default_factory=list)          # 1. tur arama terimleri
    alias_terms: list[str] = field(default_factory=list)    # 2. tur

    @property
    def names(self) -> list[str]:
        """club_colors.py'nin 1. oncelikli anahtarlari: oyundaki adlar (DB'deki yazim) + resmi ad."""
        return _unique([*self.game_names, self.name])

    @property
    def other_names(self) -> list[str]:
        """2. oncelik: takma adlar (club_colors.py baska kulubun adiyla cakisani kullanmaz)."""
        taken = {ascii_key(n) for n in self.names}
        return [a for a in _unique(self.aliases) if ascii_key(a) not in taken]


def load_clubs(open_dir: Path) -> list[Club]:
    try:
        clubs_doc = json.loads((open_dir / "clubs.json").read_text(encoding="utf-8"))
        leagues_doc = json.loads((open_dir / "leagues.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError(f"Açık veri okunamadı ({open_dir}): {exc}") from exc
    roster: dict[str, list[str]] = {}
    tables: dict[str, list[str]] = {}
    for league in leagues_doc.get("leagues", []):
        for entry in league.get("clubs", []):
            roster.setdefault(entry["id"], []).append(entry["name"])
        for season in league.get("seasons", []):
            for entry in season.get("table", []):
                tables.setdefault(entry["id"], []).append(entry["name"])
    clubs = []
    for raw in clubs_doc.get("clubs", []):
        if raw.get("country") not in COUNTRIES:
            raise BuildError(f"Bilinmeyen ülke kodu: {raw.get('country')} ({raw.get('id')})")
        game = _unique([*roster.get(raw["id"], []), *tables.get(raw["id"], [])])
        club = Club(id=raw["id"], name=raw["name"], country=raw["country"],
                    founded=raw.get("founded"), game_names=game, aliases=list(raw.get("aliases", [])),
                    wikidata=raw.get("wikidata") or None)
        club.terms = _unique([*game, club.name])
        primary = {ascii_key(t) for t in club.terms}
        # takma adlar: kisaltmalar ("Galat. Istanbul") aramada ise yaramaz
        club.alias_terms = [a for a in _unique(club.aliases)
                            if "." not in a and len(ascii_key(a)) >= 4 and ascii_key(a) not in primary][:5]
        clubs.append(club)
    clubs.sort(key=lambda c: c.id)
    return clubs


# ===========================================================================
# 4) DOGRULAMA (toplu SPARQL)
# ===========================================================================

@dataclass
class Facts:
    qid: str
    football: bool              # P31/P279* association football club
    sports_football: bool       # P31/P279* sports club + P641 association football (orn. FC Erzgebirge Aue)
    excluded: bool
    defunct: bool
    countries: frozenset[str]
    sitelinks: int
    inception: int | None
    parents: tuple[str, ...]
    colors: int


def _values(qids) -> str:
    return " ".join(f"wd:{q}" for q in qids)


def _chunks(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def verify(client: Wikidata, qids, known: dict[str, Facts]) -> None:
    """Adaylarin tur/ulke/yil/renk bilgisini `known` sozlugune ekler (VALUES bloklariyla)."""
    todo = sorted({q for q in qids if q not in known and _QID.match(q or "")},
                  key=lambda q: int(q[1:]))
    excluded = _values(EXCLUDED_TYPES)
    for chunk in _chunks(todo, SPARQL_BATCH):
        query = f"""
        SELECT ?item (SAMPLE(?sl) AS ?sitelinks) (SAMPLE(?fb) AS ?football) (SAMPLE(?sf) AS ?sportsFootball)
               (SAMPLE(?ex) AS ?excluded) (SAMPLE(?df) AS ?defunct) (MIN(YEAR(?inc)) AS ?inception)
               (GROUP_CONCAT(DISTINCT STRAFTER(STR(?country), "entity/"); separator=" ") AS ?countries)
               (GROUP_CONCAT(DISTINCT STRAFTER(STR(?parent), "entity/"); separator=" ") AS ?parents)
               (COUNT(DISTINCT ?st) AS ?colors)
        WHERE {{
          VALUES ?item {{ {_values(chunk)} }}
          OPTIONAL {{ ?item wikibase:sitelinks ?sl }}
          BIND(EXISTS {{ ?item wdt:P31/wdt:P279* wd:{FOOTBALL_CLUB} }} AS ?fb)
          BIND(EXISTS {{ ?item wdt:P31/wdt:P279* wd:{SPORTS_CLUB} . ?item wdt:P641 wd:{FOOTBALL_SPORT} }} AS ?sf)
          FILTER(?fb || ?sf)
          BIND((EXISTS {{ VALUES ?bad {{ {excluded} }} ?item wdt:P31/wdt:P279* ?bad }}
                || EXISTS {{ ?item wdt:P2094 wd:{WOMENS_FOOTBALL} }}) AS ?ex)
          BIND((EXISTS {{ ?item wdt:P576 ?end }}
                || EXISTS {{ ?item wdt:P31 wd:{DEFUNCT_CLUB} }}) AS ?df)
          OPTIONAL {{ ?item wdt:P17 ?country }}
          OPTIONAL {{ ?item wdt:P571 ?inc }}
          OPTIONAL {{ ?item wdt:P361|wdt:P749 ?parent . ?parent wdt:P31/wdt:P279* wd:{SPORTS_CLUB} }}
          OPTIONAL {{ ?item p:P6364 ?st . ?st wikibase:rank ?rk . FILTER(?rk != wikibase:DeprecatedRank) }}
        }} GROUP BY ?item
        """
        for row in client.sparql(query):
            qid = row.get("item", "")
            inception = row.get("inception")
            known[qid] = Facts(
                qid=qid,
                football=row.get("football") == "true",
                sports_football=row.get("sportsFootball") == "true",
                excluded=row.get("excluded") == "true",
                defunct=row.get("defunct") == "true",
                countries=frozenset((row.get("countries") or "").split()),
                sitelinks=int(row.get("sitelinks") or 0),
                inception=int(float(inception)) if inception else None,
                parents=tuple(sorted((row.get("parents") or "").split(), key=lambda q: int(q[1:]))),
                colors=int(row.get("colors") or 0),
            )


# ===========================================================================
# 5) ESLEME
# ===========================================================================

EXACT_PRIMARY, EXACT_ALIAS, PREFIX = 3, 2, 1
MATCH_NAMES = {EXACT_PRIMARY: "ad", EXACT_ALIAS: "takma-ad", PREFIX: "önek", 9: "elle"}


@dataclass
class Candidate:
    qid: str
    label: str
    match: int                  # EXACT_PRIMARY / EXACT_ALIAS / PREFIX
    term: str


def collect(client: Wikidata, club: Club, terms: list[str], match_exact: int,
            into: dict[str, Candidate]) -> None:
    """Arama sonuclarindan aday toplar; ayni oge icin en iyi esleme turu saklanir."""
    language = COUNTRIES[club.country][0]
    for term in terms:
        key = ascii_key(term)
        for row in client.search(term, language):
            qid = row.get("id") or ""
            if not _QID.match(qid):
                continue
            exact = ascii_key(row.get("match_text", "")) == key and row.get("match_type") in ("label", "alias")
            kind = match_exact if exact else PREFIX
            old = into.get(qid)
            if old is None or kind > old.match:
                into[qid] = Candidate(qid, row.get("label", ""), kind, term)


MIN_SITELINKS = 3           # daha azi: taslak/kopya oge (orn. 2020 toplu aktarimi "men's team" ogeleri)
MIN_SCORE = 2               # kabul esigi


def rank(club: Club, cand: Candidate, facts: Facts | None) -> tuple | None:
    """
    Siralama anahtari; None = aday reddedildi (futbol kulubu degil, dislanan tur, yanlis ulke).
    puan = ad (tam 3 / takma ad 2 / onek 1) + 2 (kurulus yili tutuyor) + 1 (dogrudan futbol kulubu
           turu) - 2 (taslak oge: site baglantisi < 3). Esitlikte ulke, faal olma, site baglantisi.
    """
    if facts is None or not (facts.football or facts.sports_football) or facts.excluded:
        return None
    allowed = COUNTRIES[club.country][1]
    if facts.countries and not facts.countries & allowed:
        return None
    score = cand.match
    if club.founded and facts.inception == club.founded:
        score += 2
    if facts.football:
        score += 1
    if facts.sitelinks < MIN_SITELINKS:
        score -= 2
    country_ok = 1 if facts.countries & allowed else 0
    return (score, country_ok, 0 if facts.defunct else 1, facts.sitelinks)


def choose(club: Club, cands: dict[str, Candidate], known: dict[str, Facts]) -> Candidate | None:
    """En iyi aday; puani MIN_SCORE'un altindaysa esleme yok (yanlis renk, renksizden kotudur)."""
    scored = []
    for cand in cands.values():
        key = rank(club, cand, known.get(cand.qid))
        if key is not None:
            scored.append((key, -int(cand.qid[1:]), cand))
    if not scored:
        return None
    key, _, best = max(scored, key=lambda s: (s[0], s[1]))
    return best if key[0] >= MIN_SCORE else None


def match_clubs(client: Wikidata, clubs: list[Club], quiet: bool = False
                ) -> tuple[dict[str, Candidate], dict[str, Facts], dict[str, str]]:
    known: dict[str, Facts] = {}
    pool: dict[str, dict[str, Candidate]] = {c.id: {} for c in clubs}
    notes: dict[str, str] = {}

    # 0) var olan kimlikler: clubs.json "wikidata" alani ya da elle dogrulanmis OVERRIDES
    fixed = {c.id: (c.wikidata or OVERRIDES.get(c.id)) for c in clubs}
    for club in clubs:
        qid = fixed[club.id]
        if qid:
            pool[club.id][qid] = Candidate(qid, "", 9, "kimlik")

    # 1) oyun adi + resmi ad
    if not quiet:
        print(f"[renk] 1. tur arama: {sum(len(c.terms) for c in clubs)} terim")
    for club in clubs:
        if not fixed[club.id]:
            collect(client, club, club.terms, EXACT_PRIMARY, pool[club.id])
    verify(client, [q for p in pool.values() for q in p], known)

    # 2) bulunamayanlar icin takma adlar
    missing = []
    for club in clubs:
        best = choose(club, pool[club.id], known)
        if best is None or best.match == PREFIX:
            missing.append(club)
    if not quiet:
        print(f"[renk] 2. tur (takma ad): {len(missing)} kulüp")
    for club in missing:
        collect(client, club, club.alias_terms, EXACT_ALIAS, pool[club.id])
    verify(client, [q for c in missing for q in pool[c.id]], known)

    chosen: dict[str, Candidate] = {}
    for club in clubs:
        best = choose(club, pool[club.id], known)
        if best is None:
            notes[club.id] = "uygun Wikidata öğesi bulunamadı"
        else:
            chosen[club.id] = best

    # 3) ayni oge iki kulube: zayif esleme duser
    by_qid: dict[str, list[str]] = {}
    for club_id, cand in chosen.items():
        by_qid.setdefault(cand.qid, []).append(club_id)
    index = {c.id: c for c in clubs}
    for qid, owners in by_qid.items():
        if len(owners) < 2:
            continue
        owners.sort(key=lambda cid: (rank(index[cid], chosen[cid], known[qid]), cid), reverse=True)
        top, *rest = owners
        if rank(index[top], chosen[top], known[qid])[:1] == rank(index[rest[0]], chosen[rest[0]], known[qid])[:1]:
            rest = owners                                   # esit: ikisi de guvenilmez
        for cid in rest:
            chosen.pop(cid, None)
            notes[cid] = f"{qid} başka kulüple çakıştı ({', '.join(o for o in owners if o != cid)})"
    return chosen, known, notes


# ===========================================================================
# 6) RENKLER
# ===========================================================================

def current_colors(statements: list[dict]) -> list[str]:
    """Gecerli renk ogeleri, kayitli sirayla (bkz. modul basligi, adim 5)."""
    live = [s for s in statements
            if s.get("value") and s.get("rank") != "deprecated" and "P582" not in s.get("qualifiers", {})]
    if any(s["rank"] == "preferred" for s in live):
        live = [s for s in live if s["rank"] == "preferred"]
    ordinals = [s["qualifiers"].get("P1545", [None])[0] for s in live]
    if live and all(o is not None and str(o).isdigit() for o in ordinals):
        live = [s for _, s in sorted(zip((int(o) for o in ordinals), live, strict=True),
                                     key=lambda pair: pair[0])]
    return list(dict.fromkeys(s["value"] for s in live))


def color_hexes(client: Wikidata, qids) -> dict[str, tuple[str, str]]:
    """Renk ogesi -> ("#RRGGBB", ingilizce etiket). Birden cok P465 varsa en kucugu (kararli)."""
    out: dict[str, tuple[str, str]] = {}
    todo = sorted(set(qids), key=lambda q: int(q[1:]))
    for chunk in _chunks(todo, SPARQL_BATCH):
        query = f"""
        SELECT ?c (MIN(?hex) AS ?code) (SAMPLE(?lb) AS ?label) WHERE {{
          VALUES ?c {{ {_values(chunk)} }}
          OPTIONAL {{ ?c wdt:P465 ?hex }}
          OPTIONAL {{ ?c rdfs:label ?lb . FILTER(LANG(?lb) = "en") }}
        }} GROUP BY ?c
        """
        for row in client.sparql(query):
            hit = _HEX.match((row.get("code") or "").strip())
            if hit:
                out[row["c"]] = (f"#{hit.group(1).upper()}", row.get("label", ""))
    return out


# ===========================================================================
# 7) DERLEME
# ===========================================================================

def build(client: Wikidata, open_dir: Path = OPEN_DIR, quiet: bool = False) -> dict:
    clubs = load_clubs(open_dir)
    chosen, known, notes = match_clubs(client, clubs, quiet=quiet)

    if not quiet:
        print(f"[renk] {len(chosen)} kulüp eşleşti; renk ifadeleri okunuyor")
    color_items: dict[str, list[str]] = {}          # kulup -> renk ogeleri
    color_from: dict[str, str] = {}
    for club in clubs:
        cand = chosen.get(club.id)
        if cand is None:
            continue
        facts = known[cand.qid]
        sources = ([cand.qid] if facts.colors else []) + list(facts.parents)
        for source in sources:
            items = current_colors(client.claims(source))
            if items:
                color_items[club.id] = items
                color_from[club.id] = source
                break
    hexes = color_hexes(client, [q for items in color_items.values() for q in items])

    entries: dict[str, dict] = {}
    without: dict[str, dict] = {}
    missing_hex: set[str] = set()
    for club in clubs:
        cand = chosen.get(club.id)
        pairs: dict[str, str] = {}                  # "#RRGGBB" -> renk adi (sira korunur)
        for qid in color_items.get(club.id, []):
            if qid in hexes:
                pairs.setdefault(*hexes[qid])
            else:
                missing_hex.add(qid)
        if cand is None or not pairs:
            # renksiz kulup de adlariyla yazilir: club_colors.py bu adlari baska kulube vermez
            without[club.id] = {
                "name": club.name,
                "wikidata": cand.qid if cand else None,
                "reason": ("Wikidata öğesinde resmi renk (P6364) yok" if cand
                           else notes.get(club.id, "uygun Wikidata öğesi bulunamadı")),
                "names": club.names,
                "aliases": club.other_names,
            }
            continue
        entry = {
            "name": club.name,
            "wikidata": cand.qid,
            "colors": list(pairs),
            "source": ENTRY_SOURCE,
            "license": ENTRY_LICENSE,
            "wikidata_label": cand.label,
            "match": MATCH_NAMES[cand.match],
            "color_names": list(pairs.values()),
        }
        if color_from[club.id] != cand.qid:
            entry["colors_from"] = color_from[club.id]
        entry["names"] = club.names
        entry["aliases"] = club.other_names
        entries[club.id] = entry
    if missing_hex and not quiet:
        print(f"[renk] P465 kodu olmayan renk öğeleri atlandı: {', '.join(sorted(missing_hex))}")

    curated = load_curated(open_dir, {c.id: c for c in clubs})
    ignored = apply_curated(entries, without, curated)
    if ignored and not quiet:
        print(f"[renk] elle derleme yok sayıldı (Wikidata rengi var): {', '.join(ignored)}")
    wikidata_count = sum(1 for e in entries.values() if e["source"] == ENTRY_SOURCE)

    return {
        "schema": SCHEMA, "version": 1,
        "license": LICENSE, "license_url": LICENSE_URL,
        "source": "Wikidata", "source_url": "https://www.wikidata.org/",
        "properties": {"official_color": "P6364", "hex": "P465", "football_club": FOOTBALL_CLUB},
        "fetched_at": client.fetched_at,
        "generator": "tools/build_club_colors.py",
        "notice": NOTICE,
        "coverage": {"clubs": len(clubs), "matched": len(chosen), "with_colors": len(entries),
                     "wikidata": wikidata_count, "curated": len(entries) - wikidata_count},
        "curated_file": f"data/open/{CURATED_FILE}",
        "curated_ignored": ignored,
        "clubs": dict(sorted(entries.items())),
        "without_colors": without,
    }


# ===========================================================================
# 8) ELLE DERLENMIS YEDEK (club_colors_curated.json)
# ===========================================================================

def load_curated(open_dir: Path, clubs: dict[str, Club]) -> dict[str, dict]:
    """
    Elle derlenmis renkler: {kulup kimligi: {name, colors, source: "curated", note}}. Dosya yoksa bos.
    Ag KULLANMAZ (--offline uretim etkilenmez). Bilinmeyen kimlik, ad uyusmazligi ya da gecersiz
    renk BuildError verir: yanlis kulube renk yazilmaz.
    """
    path = open_dir / CURATED_FILE
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError(f"{path} okunamadı: {exc}") from exc
    if doc.get("schema") != CURATED_SCHEMA or not isinstance(doc.get("clubs"), dict):
        raise BuildError(f"{path}: beklenen şema '{CURATED_SCHEMA}' değil.")
    out: dict[str, dict] = {}
    for club_id, entry in doc["clubs"].items():
        club = clubs.get(club_id)
        if club is None:
            raise BuildError(f"{path}: bilinmeyen kulüp kimliği {club_id}")
        if entry.get("name") != club.name:
            raise BuildError(f"{path}: {club_id} adı '{entry.get('name')}', clubs.json'da '{club.name}'")
        if entry.get("source") != CURATED_SOURCE or not entry.get("note"):
            raise BuildError(f"{path}: {club_id} source='curated' ve note taşımalı")
        colors = []
        for code in entry.get("colors") or ():
            hit = _HEX.match(code or "") if isinstance(code, str) else None
            if not hit:
                raise BuildError(f"{path}: {club_id} geçersiz renk {code!r}")
            colors.append(f"#{hit.group(1).upper()}")
        if not colors or len(set(colors)) != len(colors):
            raise BuildError(f"{path}: {club_id} renk listesi boş ya da tekrarlı")
        out[club_id] = {"colors": colors, "color_names": list(entry.get("color_names") or ()),
                        "note": entry["note"]}
    return out


def apply_curated(entries: dict[str, dict], without: dict[str, dict],
                  curated: dict[str, dict]) -> list[str]:
    """
    ONCELIK: Wikidata once. Elle derleme YALNIZCA Wikidata rengi olmayan (without_colors) kulube
    uygulanir; Wikidata rengi olan kulupte yok sayilir ve kimligi dondurulur (curated_ignored).
    """
    ignored = []
    for club_id, item in sorted(curated.items()):
        if club_id in entries:
            ignored.append(club_id)
            continue
        base = without.pop(club_id, None)
        if base is None:
            continue
        entry = {
            "name": base["name"],
            "wikidata": base["wikidata"],
            "colors": item["colors"],
            "source": CURATED_SOURCE,
            "license": ENTRY_LICENSE,
            "note": item["note"],
            "color_names": item["color_names"],
            "names": base["names"],
            "aliases": base["aliases"],
        }
        entries[club_id] = entry
    return ignored


def write_json(path: Path, doc: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(text.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):      # Windows konsolunda (cp1252) Turkce patlamasin
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Wikidata (CC0) resmi kulüp renklerinden data/open/club_colors.json üretir.")
    parser.add_argument("--open-dir", type=Path, default=OPEN_DIR, help="clubs.json/leagues.json klasörü.")
    parser.add_argument("--out", type=Path, default=OPEN_DIR, help="Çıktı klasörü (data/open).")
    parser.add_argument("--cache", type=Path, default=CACHE_DIR, help="Önbellek klasörü.")
    parser.add_argument("--offline", action="store_true", help="İnternete çıkma, önbelleği kullan.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    client = Wikidata(args.cache, offline=args.offline, quiet=args.quiet)
    try:
        doc = build(client, args.open_dir, quiet=args.quiet)
    except BuildError as exc:
        client.save(prune=False)                    # yarim kalan calismanin indirdikleri kaybolmasin
        print(f"[renk] HATA: {exc}")
        return 1
    client.save()
    out = args.out / OUT_FILE
    size = write_json(out, doc)
    cov = doc["coverage"]
    print(f"[renk] {cov['with_colors']}/{cov['clubs']} kulüp renkli (Wikidata {cov['wikidata']}, "
          f"elle {cov['curated']}; eşleşen {cov['matched']}, eşleşmeyen {cov['clubs'] - cov['matched']}); "
          f"{client.requests} ağ isteği -> {out} ({size / 1024:.0f} KB, {LICENSE}, {doc['fetched_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
