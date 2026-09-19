"""
club_colors.py
==============
Kulup renkleri (14S): CM 01/02 gorunumundeki tam genislik baslik bandi icin (zemin, yazi) cifti.
SAF MODUL: veritabani yok, Streamlit yok, ag yok; yalnizca stdlib.

Veri: data/open/club_colors.json  (tools/build_club_colors.py uretir; Wikidata P6364 "official
color" + P465 "sRGB color hex triplet", CC0 1.0). Wikidata rengi olmayan kulupler icin elle
derlenmis yedek (data/open/club_colors_curated.json, kayitta `source: "curated"`) aracin icinde
birlestirilir; ONCELIK Wikidata'dadir. Bu modul yalnizca birlesik dosyayi okur; kaynak ayrimi
yapmaz. Dosya bir kez okunur ve onbellekte tutulur.

Arama (colors_for):
    Takim adi VERITABANINDAKI yaziminla verilir. Acik veri dunyasinda bu ad leagues.json kadro adidir
    ("Galatasaray", "Fenerbahçe"; bkz. open_loader.build_open_world). Karsilastirma aksansiz,
    noktalamasiz ve buyuk/kucuk harf duyarsiz, ama TAM eslesmedir: "BESIKTAS" == "Beşiktaş",
    "Galatasaray" != "Galatasaray Kadın".
    Anahtarlar iki oncelikte: (1) oyundaki adlar + clubs.json resmi adi, (2) takma adlar. Takma ad
    ancak birinci oncelikte kimse tarafindan alinmamissa kullanilir; ayni oncelikte iki farkli kulube
    cikan anahtar BELIRSIZDIR ve hic kullanilmaz.
    Bilinmeyen ad (sentetik dunya, maskeli adlar "Istanbul Lions", renk verisi olmayan kulup) -> None;
    cagiran kendi varsayilan renklerine duser.

Band kurali (band_colors), WCAG 2 kontrasti ile:
    1) Renkler tekillestirilir; ilk renk birincildir (Wikidata'daki sira).
    2) Birincil ACIK bir renkse (uzerinde beyaz yazi okunmuyor: kontrast(beyaz) < 3.0 -- beyaz, sari,
       gok mavisi) ve listede koyu bir renk varsa zemin o ILK koyu renk olur, birincil yazi adayi olur.
       CM 01/02 bandi gibi: koyu dolgu, acik harf.
           Fenerbahçe  sari + lacivert -> lacivert zemin, sari yazi
           Galatasaray sari + kirmizi  -> kirmizi zemin, sari yazi
           Beşiktaş    beyaz + siyah   -> siyah zemin, beyaz yazi
       Aksi halde zemin = birincil, yazi adayi = ikinci renk.
    3) Yazi = aday, zemine karsi kontrasti >= 3.0 ise (WCAG buyuk metin esigi); degilse beyaz ya da
       koyu (#111111), hangisi daha kontrastliysa.
    Yalniz beyaz -> beyaz zemin + koyu yazi; yalniz siyah -> siyah zemin + beyaz yazi; beyaz + siyah
    (hangi sirada olursa olsun) -> siyah zemin + beyaz yazi.

Renkler "#RRGGBB" (buyuk harf) dondurulur.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "open" / "club_colors.json"
SCHEMA = "ofm/open-club-colors"

WHITE = "#FFFFFF"
NEAR_BLACK = "#111111"                   # match_day_view.INK_DARK ile ayni ton
MIN_TEXT_CONTRAST = 3.0                  # WCAG 2: buyuk/kalin metin esigi
LIGHT_LIMIT = 3.0                        # kontrast(beyaz, renk) bunun altindaysa renk "acik"tir

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")


# ===========================================================================
# 1) RENK MATEMATIGI (WCAG 2)
# ===========================================================================

def normalize_hex(value: str) -> str | None:
    """'ff0000' / '#FF0000' -> '#FF0000'; gecersizse None."""
    hit = _HEX.match((value or "").strip()) if isinstance(value, str) else None
    return f"#{hit.group(1).upper()}" if hit else None


def relative_luminance(color: str) -> float:
    """WCAG 2 bagil parlaklik (0 siyah .. 1 beyaz)."""
    code = normalize_hex(color)
    if code is None:
        raise ValueError(f"Geçersiz renk: {color!r}")
    channels = []
    for index in (1, 3, 5):
        c = int(code[index:index + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 kontrast orani (1..21), sira onemsiz."""
    la, lb = relative_luminance(a), relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def is_light(color: str) -> bool:
    """Uzerinde beyaz yazi okunmayan (acik) renk mi?"""
    return contrast_ratio(WHITE, color) < LIGHT_LIMIT


def ink_for(background: str) -> str:
    """Beyaz ya da koyu: zemine karsi hangisi daha kontrastliysa."""
    return WHITE if contrast_ratio(WHITE, background) >= contrast_ratio(NEAR_BLACK, background) else NEAR_BLACK


def band_colors(colors: Sequence[str]) -> tuple[str, str] | None:
    """Kulubun resmi renk listesinden baslik bandi (zemin, yazi). Bos/gecersiz liste -> None."""
    palette = list(dict.fromkeys(code for code in (normalize_hex(c) for c in colors or ()) if code))
    if not palette:
        return None
    primary, rest = palette[0], palette[1:]
    dark = next((c for c in rest if not is_light(c)), None) if is_light(primary) else None
    if dark is not None:
        background, candidate = dark, primary
    else:
        background, candidate = primary, (rest[0] if rest else None)
    if candidate is not None and contrast_ratio(candidate, background) >= MIN_TEXT_CONTRAST:
        return background, candidate
    return background, ink_for(background)


# ===========================================================================
# 2) AD ANAHTARI ve VERI
# ===========================================================================

def name_key(text: str) -> str:
    """Aksansiz, noktalamasiz karsilastirma anahtari ("Beşiktaş JK" -> "besiktas jk")."""
    text = (text or "").replace("ı", "i").replace("İ", "i").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())


def load_document(path: Path | str = DATA_FILE) -> dict:
    """club_colors.json'u okur; yoksa ya da bozuksa bos belge (oyun renksiz devam eder)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("clubs"), dict):
        return {}
    return doc


def build_index(doc: dict) -> dict[str, tuple[str, str]]:
    """
    Ad anahtari -> (zemin, yazi). Oncelik ve belirsizlik kurali modul basliginda.
    Renksiz kuluplerin adlari da (without_colors) anahtar sahibidir: "Bodrum" gibi bir ad, rengi
    bilinmiyor diye takma adi "Bodrum" olan baska bir kulubun rengini almaz.
    """
    entries: dict[str, dict] = {}
    for section in ("without_colors", "clubs"):
        for club_id, entry in (doc.get(section) or {}).items():
            if isinstance(entry, dict):
                entries[club_id] = entry
    bands = {cid: band_colors(entry.get("colors") or ()) for cid, entry in entries.items()}
    index: dict[str, tuple[str, str]] = {}
    taken: set[str] = set()                              # ust oncelikte gorulen her anahtar
    for tier in ("names", "aliases"):
        owners: dict[str, set[str]] = {}
        for club_id, entry in sorted(entries.items()):
            forms = list(entry.get(tier) or ())
            if tier == "names":
                forms.append(entry.get("name") or "")
            for form in forms:
                key = name_key(form) if isinstance(form, str) else ""
                if key and key not in taken:
                    owners.setdefault(key, set()).add(club_id)
        for key, clubs in owners.items():
            taken.add(key)
            if len(clubs) == 1:
                band = bands[next(iter(clubs))]
                if band is not None:
                    index[key] = band
    return index


@lru_cache(maxsize=4)
def _index_for(path: str) -> dict[str, tuple[str, str]]:
    return build_index(load_document(path))


def colors_for(team_name: str) -> tuple[str, str] | None:
    """
    Baslik bandi renkleri: (zemin, yazi), ikisi de "#RRGGBB". Bilinmeyen takim -> None.
    team_name veritabanindaki takim adidir (acik veri dunyasinda leagues.json kadro adi).
    """
    if not isinstance(team_name, str):
        return None
    key = name_key(team_name)
    return _index_for(str(DATA_FILE)).get(key) if key else None


def clear_cache() -> None:
    """Veri dosyasi yeniden uretildiyse (gelistirici) onbellegi bosaltir."""
    _index_for.cache_clear()
