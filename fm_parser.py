"""
fm_parser.py
============
Football Manager disa aktarim dosyalarini okur (6. Asama). SAF MANTIK: DB'ye yazmaz.

Desteklenen bicimler (hepsi FM'in "Print Screen / Yazdir" menusunden alinir):
    * HTML  -- "Web Page" ciktisi: <table> satirlari
    * TXT   -- "Text File" ciktisi: | ile ayrilmis sutunlar
    * CSV   -- virgul, noktali virgul veya sekme ayracli (Excel/araclarla kaydedilmis)

Kodlama: UTF-8 / UTF-16 (BOM ile) / Windows-1254 (Turkce) otomatik denenir.

Sutun eslestirme esnektir: "Fin" / "Finishing" / "Bitiricilik" ayni ozellige gider.
Iki FM kisaltmasi belirsizdir ve DEGERLERE bakilarak cozulur:
    "Nat"  -> uyruk ("TUR") ya da Natural Fitness (1-20)
    "Pos"  -> mevki ("D (C)") ya da Positioning (1-20)

Dosyada olmayan hicbir veri UYDURULMAZ: eksik alanlar None kalir; tamamlama
kararlari (orn. eksik ozelligin tahmini) seed/ratings katmanina aittir.

Isim maskeleme (8. Asama): dosya okuyuculari (parse_file / parse_files) varsayilan olarak
oyuncu, kulup ve lig adlarini name_masking ile kurgusal adlara cevirir; gercek ad okuma
sinirini asmaz. Tekrar ayiklama (UID / isim+yas+kulup) maskelemeden ONCE ham adlarla yapilir.
parse_rows dusuk seviyeli yapi tasidir ve ham satirlari oldugu gibi dondurur.
mask_level="off" (kisisel/yerel oyun) adlari HIC degistirmez: rapor ham kalir (masked=False),
yalnizca mask_level alani "off" olarak isaretlenir.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from club_directory import normalize, plain_key
from models import Position
from name_masking import (
    DEFAULT_MASK_LEVEL,
    MASK_OFF,
    build_club_mask_map,
    build_league_mask_map,
    build_player_mask_map,
    normalize_mask_level,
)

GBP_TO_EUR = 1.17
USD_TO_EUR = 0.92
WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12

# ===========================================================================
# 1) SUTUN SOZLUGU
# ===========================================================================

# Kanonik alan -> kabul edilen basliklar (normalize edilmis halleriyle karsilastirilir)
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "player", "player name", "oyuncu", "isim", "ad soyad", "ad"),
    "age": ("age", "yas"),
    "club": ("club", "team", "kulup", "takim"),
    "position": ("position", "positions", "mevki", "pozisyon"),
    "best_position": ("best pos", "best position", "best role", "en iyi mevki"),
    "nationality": ("nation", "nationality", "uyruk", "ulke"),
    # "Based" FM'de kulubun ULKESIDIR, lig degil; genel "ID" de satir numarasi olabilir.
    "league": ("division", "league", "competition", "lig"),
    "uid": ("uid", "unique id"),
    "ca": ("ca", "current ability", "mevcut yetenek"),
    "pa": ("pa", "potential ability", "potansiyel yetenek", "potansiyel"),
    "value": ("transfer value", "value", "market value", "asking price", "deger", "piyasa degeri",
              "transfer degeri"),
    "wage": ("wage", "wages", "salary", "maas"),
    "expires": ("expires", "contract expires", "contract end", "sozlesme bitisi", "sozlesme"),
    # 16G: gercek oyuncu kadrolarina FM yamasi eslemesi icin (open_loader.match_fm_records); FM'de "DoB"
    "birth_date": ("dob", "date of birth", "birth date", "dogum tarihi"),
}

# FM ozellikleri (1-20): kanonik anahtar -> basliklar
ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    # Teknik
    "corners": ("cor", "corners", "korner"),
    "crossing": ("cro", "crossing", "orta"),
    "dribbling": ("dri", "dribbling", "top surme"),
    "finishing": ("fin", "finishing", "bitiricilik"),
    "first_touch": ("fir", "first touch", "ilk dokunus"),
    "free_kicks": ("fre", "free kick taking", "free kicks", "serbest vurus"),
    "heading": ("hea", "heading", "kafa vurusu"),
    "long_shots": ("lon", "long shots", "uzaktan sut"),
    "long_throws": ("l th", "lth", "long throws", "uzun taac"),
    "marking": ("mar", "marking", "markaj"),
    "passing": ("pas", "passing"),
    "penalty_taking": ("pen", "penalty taking", "penalti"),
    "tackling": ("tck", "tackling", "top kapma"),
    "technique": ("tec", "technique", "teknik"),
    # Zihinsel
    "aggression": ("agg", "aggression", "agresiflik"),
    "anticipation": ("ant", "anticipation", "onsezi"),
    "bravery": ("bra", "bravery", "cesaret"),
    "composure": ("cmp", "composure", "sogukkanlilik"),
    "concentration": ("cnt", "concentration", "konsantrasyon"),
    "decisions": ("dec", "decisions", "karar alma"),
    "determination": ("det", "determination", "kararlilik"),
    "flair": ("fla", "flair", "yaraticilik"),
    "leadership": ("ldr", "leadership", "liderlik"),
    "off_the_ball": ("otb", "off the ball", "topsuz alan"),
    "positioning": ("positioning", "pozisyon alma"),
    "teamwork": ("tea", "teamwork", "takim oyunu"),
    "vision": ("vis", "vision", "vizyon"),
    "work_rate": ("wor", "work rate", "calismak"),
    # Fiziksel
    "acceleration": ("acc", "acceleration", "hizlanma"),
    "agility": ("agi", "agility", "ceviklik"),
    "balance": ("bal", "balance", "denge"),
    "jumping_reach": ("jum", "jumping reach", "ziplama"),
    "natural_fitness": ("natural fitness", "dogal kondisyon"),
    "pace": ("pac", "pace", "hiz"),
    "stamina": ("sta", "stamina", "dayaniklilik"),
    "strength": ("str", "strength", "guc"),
    # Kaleci
    "aerial_reach": ("aer", "aerial reach", "hava hakimiyeti"),
    "command_of_area": ("cmd", "command of area", "ceza sahasi hakimiyeti"),
    "communication": ("com", "communication", "iletisim"),
    "eccentricity": ("ecc", "eccentricity", "eksantriklik"),
    "handling": ("han", "handling", "elle kontrol"),
    "kicking": ("kic", "kicking", "degaj"),
    "one_on_ones": ("1v1", "one on ones", "birebir"),
    "punching": ("pun", "punching", "yumruklama"),
    "reflexes": ("ref", "reflexes", "refleksler", "refleks"),
    "rushing_out": ("tro", "rushing out", "rushing out tendency", "kalesini terk"),
    "throwing": ("thr", "throwing", "el ile oyun"),
}

# Degerine bakilarak cozulen belirsiz basliklar: baslik -> (metinse, sayiysa)
AMBIGUOUS_HEADERS: dict[str, tuple[str, str]] = {
    "nat": ("nationality", "attr:natural_fitness"),
    "pos": ("position", "attr:positioning"),
}


def _header_key(text: str) -> str:
    """
    Baslik anahtari. Kulup adi normalizasyonu KULLANILMAZ: o "club"/"fc" gibi kelimeleri
    siler ve "Club" basligi bos anahtara donerdi (bos/sembol hucreler kulup sanilirdi).
    """
    return plain_key(text.replace(".", " "))


def _build_header_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for fld, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if key := _header_key(alias):
                index.setdefault(key, fld)
    for attr, aliases in ATTRIBUTE_ALIASES.items():
        for alias in aliases:
            if key := _header_key(alias):
                index.setdefault(key, f"attr:{attr}")
    return index


_HEADER_INDEX = _build_header_index()


# ===========================================================================
# 2) HUCRE AYRISTIRICILARI
# ===========================================================================

_EMPTY = {"", "-", "--", "—", "n/a", "na", "none", "?"}


def _is_empty(text: str | None) -> bool:
    return text is None or text.strip().casefold() in _EMPTY


def parse_int(text: str | None) -> int | None:
    if _is_empty(text):
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def parse_attribute(text: str | None) -> float | None:
    """'14' -> 14.0 · '12-15' (gozlemci araligi) -> 13.5 · '-' -> None. 1-20 disi -> None."""
    if _is_empty(text):
        return None
    numbers = [float(n) for n in re.findall(r"\d+(?:[.,]\d+)?", text.replace(",", "."))]
    if not numbers:
        return None
    value = sum(numbers[:2]) / min(2, len(numbers))
    return value if 1 <= value <= 20 else None


def _amount(token: str) -> float | None:
    """Tek bir para tutari: '1.5M', '1,5M', '850K', '1.250.000', '12,500'."""
    match = re.search(r"(\d[\d.,\s]*)\s*([kKmMbB])?", token)
    if not match:
        return None
    number, suffix = match.group(1).strip().replace(" ", ""), (match.group(2) or "").lower()
    if suffix:
        separators = number.count(".") + number.count(",")
        if separators > 1:                                     # 1.250.000K -> binlik ayraclar
            number = re.sub(r"[.,](?=\d{3}(?:[.,]|$))", "", number)
        elif separators == 1 and re.search(r"[.,]\d{3}$", number):
            number = re.sub(r"[.,]", "", number)               # 1,250K -> 1250K (binlik ayrac)
        else:
            number = number.replace(",", ".")                  # 1,5M / 1.5M -> ondalik
    else:
        # Ekli olmayan tutarda ayraclar binlik ayraci
        number = re.sub(r"[.,]", "", number)
    try:
        value = float(number)
    except ValueError:
        return None
    return value * {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]


def parse_money(text: str | None) -> int | None:
    """
    Para -> EUR (tam sayi).
        '€45M' -> 45_000_000 · '£1.2M' -> 1_404_000 · '€10M - €20M' -> 15_000_000
        'Not for Sale' / 'Satılık Değil' / '-' -> None
    """
    if _is_empty(text):
        return None
    lowered = plain_key(text)
    if "not for sale" in lowered or "satilik degil" in lowered or "unknown" in lowered:
        return None
    rate = GBP_TO_EUR if "£" in text else USD_TO_EUR if "$" in text else 1.0
    # Aralik ayraci: bosluklu ya da bosluksuz tire ("€10M - €20M", "€10M-€20M") veya "to"
    parts = [p for p in re.split(r"\s*[-–]\s*(?=[€£$]?\s*\d)|\bto\b", text) if re.search(r"\d", p)]
    amounts = [a for a in (_amount(p) for p in parts) if a is not None]
    if not amounts:
        return None
    return int(round(sum(amounts) / len(amounts) * rate))


def parse_wage(text: str | None) -> int | None:
    """
    Maas -> HAFTALIK EUR.
        '€150K p/w' -> 150_000 · '£40K p/m' -> ~10_800 · '€5M p/a' -> ~96_154
    Donem belirtilmemisse FM'in varsayilani olan haftalik kabul edilir.
    """
    amount = parse_money(text)
    if amount is None:
        return None
    lowered = text.casefold()
    if re.search(r"p/?a\b|per annum|/\s*y(ea)?r|/\s*y[iı]l|yearly|y[iı]ll[iı]k", lowered):
        return int(round(amount / WEEKS_PER_YEAR))
    if re.search(r"p/?m\b|per month|/\s*mo|/\s*ay\b|monthly|ayl[iı]k", lowered):
        return int(round(amount * MONTHS_PER_YEAR / WEEKS_PER_YEAR))
    return amount


def parse_expiry_year(text: str | None) -> int | None:
    """'30/6/2027', '2027-06-30', '30.06.2027', '2027' -> 2027."""
    if _is_empty(text):
        return None
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group()) if match else None


def parse_birth_date(text: str | None) -> str | None:
    """
    '20/7/2000 (25 years old)', '20.07.2000', '2000-07-20' -> '2000-07-20'. Gun-ay-yil varsayilir (FM'in Avrupa/Turkce
    bicimi); ay 12'den buyukse ay-gun sirasi kabul edilir. Belirsiz gun/ay (ikisi de <= 12) eslemede iki sirayla da
    denenir (open_loader._birth_matches). Gecersiz tarih None.
    """
    if _is_empty(text):
        return None
    iso = re.search(r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
    else:
        dmy = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})\b", text)
        if not dmy:
            return None
        day, month, year = int(dmy.group(1)), int(dmy.group(2)), int(dmy.group(3))
        if month > 12 >= day:
            day, month = month, day
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


# FM mevki kodu -> motor mevkisi (Ingilizce ve Turkce FM kodlari)
_POSITION_CODES: dict[str, Position] = {
    "GK": Position.GK, "KL": Position.GK,
    "D": Position.DEF, "DC": Position.DEF, "DL": Position.DEF, "DR": Position.DEF,
    "WB": Position.DEF, "WBL": Position.DEF, "WBR": Position.DEF, "SW": Position.DEF,
    "CB": Position.DEF, "LB": Position.DEF, "RB": Position.DEF, "KB": Position.DEF, "L": Position.DEF,
    "DM": Position.MID, "M": Position.MID, "MC": Position.MID, "ML": Position.MID, "MR": Position.MID,
    "AM": Position.MID, "AMC": Position.MID, "AML": Position.MID, "AMR": Position.MID,
    "CM": Position.MID, "LM": Position.MID, "RM": Position.MID, "CAM": Position.MID, "CDM": Position.MID,
    "DOS": Position.MID, "OS": Position.MID, "OOS": Position.MID,
    "ST": Position.FWD, "F": Position.FWD, "FC": Position.FWD, "CF": Position.FWD,
    "LW": Position.FWD, "RW": Position.FWD, "FV": Position.FWD, "SF": Position.FWD,
    # Motorun kendi kodlari (elle hazirlanmis CSV'ler icin)
    "DEF": Position.DEF, "MID": Position.MID, "FWD": Position.FWD,
}


def parse_position(text: str | None) -> Position | None:
    """
    FM mevki metni -> motor mevkisi. Ilk listelenen mevki esas alinir.
    DIKKAT: FM mevkileri geriden one siralar ("D (C), DM"), yani ilk mevki her zaman
    dogal mevki degildir. Disa aktarimda 'Best Pos' sutunu varsa parse_rows onu tercih eder.
        'GK' -> GK · 'D (C)' -> DEF · 'DM, M (C)' -> MID · 'AM (RL), ST (C)' -> MID
        'ST (C)' -> FWD · 'M/AM (C)' -> MID · 'KL' -> GK · 'FV' -> FWD
    """
    if _is_empty(text):
        return None
    first = re.split(r"[,;]", text.strip())[0]
    code = re.split(r"[\s(/]", first.strip().upper())[0]
    return _POSITION_CODES.get(code)


# ===========================================================================
# 3) TABLO OKUYUCULARI
# ===========================================================================

class _TableCollector(HTMLParser):
    """
    HTML icindeki tum <table>'lari satir/hucre listesi olarak toplar.
    Her acik tablo kendi durumunu tasir: hucre icindeki ic ice tablo dis satiri bozmaz.
    Kapanmayan </td>, </tr> ve </table> etiketleri (gecerli HTML'de istege bagli) tolere edilir.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._states: list[dict] = []

    @staticmethod
    def _close_cell(state: dict) -> None:
        if state["cell"] is not None and state["row"] is not None:
            state["row"].append(" ".join("".join(state["cell"]).replace("\xa0", " ").split()))
        state["cell"] = None

    def _close_row(self, state: dict) -> None:
        self._close_cell(state)
        if state["row"] is not None and any(state["row"]):
            state["rows"].append(state["row"])
        state["row"] = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._states.append({"rows": [], "row": None, "cell": None})
            return
        if not self._states:
            return
        state = self._states[-1]
        if tag == "tr":
            self._close_row(state)
            state["row"] = []
        elif tag in ("td", "th"):
            self._close_cell(state)
            if state["row"] is None:
                state["row"] = []
            state["cell"] = []
        elif tag == "br" and state["cell"] is not None:
            state["cell"].append(" ")

    def handle_endtag(self, tag):
        if not self._states:
            return
        state = self._states[-1]
        if tag in ("td", "th"):
            self._close_cell(state)
        elif tag == "tr":
            self._close_row(state)
        elif tag == "table":
            self._close_row(state)
            self.tables.append(self._states.pop()["rows"])

    def handle_data(self, data):
        if self._states and self._states[-1]["cell"] is not None:
            self._states[-1]["cell"].append(data)

    def close(self):
        super().close()
        while self._states:
            state = self._states.pop()
            self._close_row(state)
            self.tables.append(state["rows"])


def strip_rtf(text: str) -> str:
    """
    FM'in .rtf 'Text File' ciktisini duz metne indirger (en iyi cabayla):
    satir sonlari, \\'hh ve \\uN kacislari cozulur, kontrol kelimeleri ve suslu parantezler atilir.
    """
    codepage = re.search(r"\\ansicpg(\d+)", text)
    encoding = f"cp{codepage.group(1)}" if codepage else "cp1252"

    def hex_char(match):
        try:
            return bytes([int(match.group(1), 16)]).decode(encoding, errors="replace")
        except LookupError:
            return bytes([int(match.group(1), 16)]).decode("cp1252", errors="replace")

    # Font/renk tablosu gibi hedef gruplari icerikleriyle birlikte at (metin satirina yapismasinlar)
    group = re.compile(r"\{\\(?:fonttbl|colortbl|stylesheet|info|\*)[^{}]*\}")
    while group.search(text):
        text = group.sub("", text)
    text = re.sub(r"\\par[d]?\b ?", "\n", text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", hex_char, text)
    text = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    return text.replace("{", "").replace("}", "")


def decode_bytes(raw: bytes) -> str:
    """BOM'a bakar; yoksa BOM'suz UTF-16, UTF-8, sonra Turkce Windows kodlamasi (cp1254) denenir."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    sample = raw[:400]
    if len(sample) >= 4 and sample.count(0) >= len(sample) * 0.3:       # BOM'suz UTF-16
        odd_nulls, even_nulls = sample[1::2].count(0), sample[0::2].count(0)
        try:
            return raw.decode("utf-16-le" if odd_nulls > even_nulls else "utf-16-be")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1254", errors="replace")


def detect_format(text: str) -> str:
    head = text[:4000].casefold()
    if "<table" in head or "<html" in head or head.lstrip().startswith("<"):
        return "html"
    # Ayrac satirlari ("-----", "|---|---|") oylamaya katilmaz
    lines = [ln for ln in text.splitlines()[:60] if ln.strip() and not re.fullmatch(r"[\s|\-=+:_]*", ln)]
    if lines and sum(1 for ln in lines if ln.count("|") >= 2) >= len(lines) * 0.5:
        return "txt"
    return "csv"


def _rows_from_html(text: str) -> list[list[str]]:
    collector = _TableCollector()
    collector.feed(text)
    collector.close()
    candidates = [t for t in collector.tables if find_header(t)[0] is not None]
    return max(candidates, key=len) if candidates else []


def _rows_from_txt(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.count("|") < 2 or re.fullmatch(r"[\s|\-=+:]*", line):
            continue
        # Ilk ve son '|' disindaki metin (RTF/konsol kalintisi) hucre sayilmaz
        inner = line[line.index("|") + 1: line.rindex("|")]
        cells = [c.strip() for c in inner.split("|")]
        rows.append(cells)
    return rows


def _rows_from_csv(text: str) -> list[list[str]]:
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect) if any(row)]


def read_rows(path: Path) -> tuple[list[list[str]], str]:
    """Dosyayi okuyup (satirlar, bicim) doner. Ilk satirin baslik olmasi gerekmez."""
    text = decode_bytes(Path(path).read_bytes())
    if text.lstrip().startswith("{\\rtf"):
        text = strip_rtf(text)
    fmt = detect_format(text)
    reader = {"html": _rows_from_html, "txt": _rows_from_txt, "csv": _rows_from_csv}[fmt]
    return reader(text), fmt


# ===========================================================================
# 4) BASLIK TESPITI
# ===========================================================================

def find_header(rows: Sequence[Sequence[str]], scan: int = 15) -> tuple[int | None, list[str | None]]:
    """
    Ilk `scan` satir icinde, isim sutunu iceren ve en az 3 taninan sutunu olan
    satiri baslik kabul eder. Donus: (baslik satir no, sutun -> alan listesi).
    Belirsiz basliklar (Nat/Pos) burada 'ambig:...' olarak isaretlenir.
    """
    for i, row in enumerate(rows[:scan]):
        mapping: list[str | None] = []
        for cell in row:
            key = _header_key(cell)
            if not key:
                mapping.append(None)
            elif key in AMBIGUOUS_HEADERS:
                mapping.append(f"ambig:{key}")
            else:
                mapping.append(_HEADER_INDEX.get(key))
        known = [m for m in mapping if m]
        if "name" in mapping and len(known) >= 3:
            return i, mapping
    return None, []


def _looks_numeric(values: Iterable[str]) -> bool:
    vals = [v for v in values if not _is_empty(v)]
    if not vals:
        return False
    numeric = sum(1 for v in vals if re.fullmatch(r"\d{1,2}(\s*-\s*\d{1,2})?", v.strip()))
    return numeric >= len(vals) * 0.8


def resolve_ambiguous(mapping: list[str | None], data_rows: Sequence[Sequence[str]]) -> list[str | None]:
    """'Nat'/'Pos' basliklarini hucre degerlerine bakarak cozer."""
    resolved = list(mapping)
    for col, target in enumerate(mapping):
        if not target or not target.startswith("ambig:"):
            continue
        text_field, numeric_field = AMBIGUOUS_HEADERS[target.split(":", 1)[1]]
        values = [r[col] for r in data_rows[:30] if col < len(r)]
        choice = numeric_field if _looks_numeric(values) else text_field
        # Ayni alan zaten acik bir baslikla geldiyse (orn. hem 'Position' hem 'Pos') tekrar atama
        resolved[col] = None if choice in resolved else choice
    return resolved


# ===========================================================================
# 5) KAYITLAR
# ===========================================================================

@dataclass
class FMPlayerRecord:
    name: str
    age: int | None
    club: str | None
    position: Position
    positions_raw: str
    nationality: str | None = None
    league: str | None = None
    uid: int | None = None
    current_ability: int | None = None
    potential_ability: int | None = None
    value_eur: int | None = None
    wage_eur_weekly: int | None = None
    contract_expiry_year: int | None = None
    fm_attributes: dict[str, float] = field(default_factory=dict)
    source_file: str = ""
    line_no: int = 0
    birth_date: str | None = None        # 16G: "YYYY-MM-DD" (DoB sutunu varsa; yalnizca FM yamasi eslemesi)

    @property
    def dedupe_key(self) -> tuple:
        if self.uid is not None:
            return ("uid", self.uid)
        return ("name", normalize(self.name), self.age, normalize(self.club or ""))


@dataclass
class ParseReport:
    files: list[tuple[str, str]] = field(default_factory=list)       # (dosya, bicim)
    rows_read: int = 0
    players: list[FMPlayerRecord] = field(default_factory=list)
    skipped: list[tuple[str, int, str]] = field(default_factory=list)  # (dosya, satir, sebep)
    unknown_columns: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    duplicates: int = 0
    masked: bool = False                 # oyuncu/kulup/lig adlari kurgusal adlara cevrildi mi
    mask_level: str | None = None        # uygulanan seviye: "light" / "strong" / "off" ("off" -> masked False)
    masked_players: int = 0              # maskelenen oyuncu kaydi
    masked_clubs: int = 0                # maskelenen farkli kulup adi
    masked_leagues: int = 0              # maskelenen farkli lig metni

    def merge(self, other: ParseReport) -> None:
        # Maskeli ve ham rapor birlesirse sonuc "maskeli" sayilmaz (ham ad kalmis olabilir)
        if not self.files and not self.players:
            self.masked, self.mask_level = other.masked, other.mask_level
        elif self.masked != other.masked or self.mask_level != other.mask_level:
            self.masked, self.mask_level = False, None
        self.files += other.files
        self.rows_read += other.rows_read
        self.players += other.players
        self.skipped += other.skipped
        self.unknown_columns |= other.unknown_columns
        self.warnings += other.warnings
        self.duplicates += other.duplicates
        self.masked_players += other.masked_players
        self.masked_clubs += other.masked_clubs
        self.masked_leagues += other.masked_leagues

    def summary(self) -> str:
        clubs = {p.club for p in self.players if p.club}
        text = (f"{len(self.files)} dosya · {self.rows_read} satır · {len(self.players)} oyuncu · "
                f"{len(clubs)} kulüp · {len(self.skipped)} atlandı · {self.duplicates} tekrar")
        if self.masked:
            text += f" · isimler maskeli ({self.mask_level})"
        return text


def _cell(row: Sequence[str], col: int | None) -> str | None:
    if col is None or col >= len(row):
        return None
    return row[col]


def parse_rows(rows: Sequence[Sequence[str]], source: str = "<bellek>") -> ParseReport:
    """Ham satirlari FM oyuncu kayitlarina cevirir."""
    report = ParseReport()
    header_at, mapping = find_header(rows)
    if header_at is None:
        report.warnings.append(f"{source}: başlık satırı bulunamadı (en az 'Name' + 2 tanınan sütun gerekli).")
        return report

    header = rows[header_at]
    data = rows[header_at + 1:]
    mapping = resolve_ambiguous(mapping, data)
    report.unknown_columns = {header[i] for i, m in enumerate(mapping) if m is None and header[i].strip()}

    # Ayni alana giden birden fazla sutun varsa ILKI gecerlidir
    columns: dict[str, int] = {}
    attr_columns: dict[str, int] = {}
    for i, m in enumerate(mapping):
        if not m:
            continue
        if m.startswith("attr:"):
            attr_columns.setdefault(m.split(":", 1)[1], i)
        else:
            columns.setdefault(m, i)
    if "position" not in columns and "best_position" not in columns:
        report.warnings.append(f"{source}: mevki sütunu yok; oyuncular okunamaz.")
        return report
    if "age" not in columns:
        report.warnings.append(f"{source}: yaş sütunu yok; yaş bilinmiyor olarak işaretlendi.")

    for offset, row in enumerate(data):
        line_no = header_at + 2 + offset
        if list(row) == list(header):                      # FM uzun listelerde basligi tekrarlar
            continue
        report.rows_read += 1

        name = (_cell(row, columns.get("name")) or "").strip()
        if _is_empty(name):
            report.skipped.append((source, line_no, "isim yok"))
            continue
        positions_raw = (_cell(row, columns.get("position")) or "").strip()
        best = (_cell(row, columns.get("best_position")) or "").strip()
        position = parse_position(best) or parse_position(positions_raw)
        positions_raw = positions_raw or best
        if position is None:
            report.skipped.append((source, line_no, f"mevki çözülemedi: '{positions_raw}'"))
            continue
        age = parse_int(_cell(row, columns.get("age"))) if "age" in columns else None
        if "age" in columns and (age is None or not 15 <= age <= 45):      # DB kisiti: 15-45
            report.skipped.append((source, line_no, f"yaş okunamadı: '{_cell(row, columns.get('age'))}'"))
            continue

        club = _cell(row, columns.get("club"))
        league = _cell(row, columns.get("league"))
        nationality = _cell(row, columns.get("nationality"))
        attributes = {
            attr: value
            for attr, col in attr_columns.items()
            if (value := parse_attribute(_cell(row, col))) is not None
        }
        ca = parse_int(_cell(row, columns.get("ca")))
        pa = parse_int(_cell(row, columns.get("pa")))

        report.players.append(FMPlayerRecord(
            name=" ".join(name.split()),
            age=age,
            club=None if _is_empty(club) else club.strip(),
            position=position,
            positions_raw=positions_raw,
            nationality=None if _is_empty(nationality) else nationality.strip(),
            league=None if _is_empty(league) else league.strip(),
            uid=parse_int(_cell(row, columns.get("uid"))),
            current_ability=ca if ca is not None and 1 <= ca <= 200 else None,
            potential_ability=pa if pa is not None and 1 <= pa <= 200 else None,
            value_eur=parse_money(_cell(row, columns.get("value"))),
            wage_eur_weekly=parse_wage(_cell(row, columns.get("wage"))),
            contract_expiry_year=parse_expiry_year(_cell(row, columns.get("expires"))),
            fm_attributes=attributes,
            source_file=source,
            line_no=line_no,
            birth_date=parse_birth_date(_cell(row, columns.get("birth_date"))),
        ))
    return report


def mask_report(report: ParseReport, level: str = DEFAULT_MASK_LEVEL) -> ParseReport:
    """
    Rapordaki oyuncu, kulup ve lig adlarini YERINDE maskeler (name_masking) ve raporu dondurur.
    Farkli gercek adlar ayni maskeye dusmez; ayni kulubun her yazimi ayni maskeyi alir.
    Zaten maskeli rapora tekrar uygulanmaz.
    level="off": hicbir ad degismez; rapor ham kalir (masked=False) ve yalnizca mask_level
    alani "off" olur -- gercek adlarla KISISEL/YEREL oyun icin (bkz. name_masking).
    """
    if report.masked:
        return report
    level = normalize_mask_level(level)
    if level == MASK_OFF:
        report.mask_level = MASK_OFF
        return report
    players = report.players
    player_map = build_player_mask_map([(p.name, p.nationality) for p in players], level)
    club_map = build_club_mask_map((p.club for p in players if p.club), level)
    league_map = build_league_mask_map((p.league for p in players if p.league), level)
    for record in players:
        record.name = player_map[record.name]
        if record.club:
            record.club = club_map[record.club]
        if record.league:
            record.league = league_map[record.league]
    report.masked, report.mask_level = True, level
    report.masked_players = len(players)
    report.masked_clubs = len(set(club_map.values()))
    report.masked_leagues = len(set(league_map.values()))
    return report


def parse_file(
    path: str | Path,
    mask_names: bool = True,
    mask_level: str = DEFAULT_MASK_LEVEL,
) -> ParseReport:
    """Tek dosya. mask_names=False ham adlari dondurur (yalnizca test/teshis icin)."""
    path = Path(path)
    rows, fmt = read_rows(path)
    report = parse_rows(rows, source=path.name)
    report.files.append((path.name, fmt))
    return mask_report(report, mask_level) if mask_names else report


def parse_files(
    paths: Iterable[str | Path],
    mask_names: bool = True,
    mask_level: str = DEFAULT_MASK_LEVEL,
) -> ParseReport:
    """
    Birden fazla disa aktarimi birlestirir; ayni oyuncu (UID ya da isim+yas+kulup) bir kez alinir.
    Tekrar ayiklama HAM adlarla yapilir, ardindan (mask_names=True ise) adlar maskelenir.
    """
    merged = ParseReport()
    for path in paths:
        merged.merge(parse_file(path, mask_names=False))
    seen: set[tuple] = set()
    unique: list[FMPlayerRecord] = []
    for record in merged.players:
        if record.dedupe_key in seen:
            merged.duplicates += 1
            continue
        seen.add(record.dedupe_key)
        unique.append(record)
    merged.players = unique
    return mask_report(merged, mask_level) if mask_names else merged


SUPPORTED_SUFFIXES = (".html", ".htm", ".csv", ".txt", ".tsv", ".rtf")


def discover_files(directory: str | Path, include_samples: bool = False) -> list[Path]:
    """Klasordeki FM disa aktarimlarini bulur. 'sample_' ile baslayanlar varsayilan olarak haric."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and (include_samples or not p.name.lower().startswith("sample_"))
    )
