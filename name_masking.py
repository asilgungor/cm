"""
name_masking.py
===============
Isim maskeleme / donusum ara yazilimi (8. Asama). SAF MANTIK: veritabani bilmez.

Amac: ham veri okunurken ya da dunya kurulurken gercek kulup, lig ve oyuncu adlari
kurgusal ama cagristirici adlara cevrilir; veritabanina HICBIR gercek ad ulasmaz.

Kulupler
    * Rehberdeki kulup (gercek ad, yazim farki ya da zaten maskeli ad) -> rehberdeki maskeli ad
      ("Galatasaray", "Galatasaray SK", "Istanbul Lions" -> "Istanbul Lions"). Idempotent.
    * Rehberde olmayan kulup -> kural tabanli maske: kurum kisaltmalari (FC, SK...) atilir,
      en ayirt edici (en uzun) kelimeye tek harflik fonetik degisiklik uygulanir.
Ligler
    * Bilinen lig (gercek ad, yazim farki, maskeli ad) -> "<Ulke> Elit Ligi".
    * Bilinmeyen lig -> genel kelimeler (League, Liga...) korunur, ayirt edici kelime degisir.
Oyuncular
    * light : ada TEK, sistematik ve gercekci bir degisiklik uygulanir; ad taninir kalir ama
              ozgun yazim veritabanina ulasmaz. Ilk isim bas harfe INMEZ (butun kalir).
              Kural sirasi (ilk uygulanabilen secilir, ayrinti: bolum 2):
                R1 uzun soyad kisaltma : "Hakan Çalhanoğlu" -> "Hakan Çalhano",
                                         "Robert Lewandowski" -> "Robert Lewandow"
                R2 cift sesli          : "Erling Haaland" -> "Erling Harland"
                R3 kayan sesli (au/ou) : "Mauro Icardi" -> "Muro Icardi"
                R4 sesli kaydirma      : "Kylian Mbappé" -> "Kylian Mbeppe",
                                         "Victor Osimhen" -> "Victor Osemen", "Orkun Kökçü" -> "Orkan Kökçü"
                R5 son care            : bas sesli kaydirma / harf eki
              Soyad (son ana kelime) her kuralda ilk isimden once denenir. Soyad on ekleri
              (van, de...), sonekler (Jr.) ve Turkce/Iskandinav harfler (ç ğ ı ö ş ü ø å æ) hedef
              alinmaz; degisen kelimede yumusak aksanlar duzlesir (é -> e).
    * strong: ozgun addan hashlib ozetiyle secilen tamamen kurgusal ad (uyruga gore havuz).

Determinizm: Python'un tuzlanan hash() fonksiyonu KULLANILMAZ; kurallar sirali, "strong"
seviyesi sha256 ozetine dayanir. Ayni girdi her calistirmada ayni maskeyi verir.

Toplu eslemeler (build_*_mask_map) farkli ozgun adlarin ayni maskeye dusmesini engeller:
cakisma olursa ozgun adin plain_key sirasina gore deterministik bir varyant secilir. Maske,
veri kumesindeki baska bir ozgun adla da ayni olamaz.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections.abc import Iterable, Iterator
from functools import lru_cache

from club_directory import (
    MASKED_LEAGUES,
    NOISE_TOKENS,
    OTHER_COUNTRY,
    canonical_league,
    known_clubs,
    lookup_club,
    plain_key,
    real_league_keys,
    split_name,
)

MASK_LEVELS = ("light", "strong")
DEFAULT_MASK_LEVEL = "light"
MASK_LEVEL_ENV = "SEED_NAME_MASKING"

_MAX_VARIANTS = 64
_ROMAN = ("II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")


def normalize_mask_level(level: str | None) -> str:
    """Gecersiz ya da bos seviye -> varsayilan ('light'). 'off' diye bir seviye yoktur."""
    key = (level or "").strip().casefold()
    return key if key in MASK_LEVELS else DEFAULT_MASK_LEVEL


def mask_level_from_env() -> str:
    """SEED_NAME_MASKING ortam degiskeni; gecersizse 'light'."""
    return normalize_mask_level(os.getenv(MASK_LEVEL_ENV))


def _check_level(level: str) -> str:
    if level not in MASK_LEVELS:
        raise ValueError(f"Geçersiz maskeleme seviyesi: {level!r} (light/strong)")
    return level


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _key(text: str) -> str:
    """plain_key; Latin harfi olmayan adlarda (Kiril vb.) bos kalmasin diye casefold'a duser."""
    return plain_key(text) or _clean(text).casefold()


# ===========================================================================
# 1) KELIME DUZEYINDE HAFIF DEGISIKLIK (kulup ve lig kural maskeleri)
# ===========================================================================

# Soyad on ekleri: oldugu gibi korunur ("van Dijk" -> "van Dejk")
PARTICLES = frozenset({
    "van", "de", "da", "di", "dos", "del", "della", "von", "der", "ter", "ten", "el", "al", "bin",
    "ben", "le", "la",
})
# Kusak/soy sonekleri: oldugu gibi korunur ("Vinícius Júnior" -> "Venicius Júnior")
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "junior", "filho"})

# Kaldirilan "yumusak" aksanlar: yeniden adlandirmada okunusu sadelestirir (é -> e).
# Turkce (ç ğ ş ö ü), Iskandinav (å) ve tilde (ñ, ã) isaretleri korunur.
_SOFT_MARKS = frozenset({chr(0x300), chr(0x301), chr(0x302)})   # grave, acute, circumflex
_EXTRA_VOWELS = frozenset("ıøæåœ")

_DIGRAPHS = (("sch", "sh"), ("ij", "y"), ("ph", "f"), ("th", "t"), ("ck", "k"), ("dt", "t"))
_VOWEL_SHIFTS = (("a", "e"), ("o", "u"), ("i", "y"), ("u", "o"), ("e", "i"))
_CONSONANT_SWAPS = (("z", "s"), ("w", "v"), ("v", "w"), ("k", "c"), ("c", "k"), ("q", "k"),
                    ("x", "s"), ("s", "z"))
_NO_NEW_DOUBLE = frozenset("iyuwjhqxv")


def strip_soft_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(ch for ch in decomposed if ch not in _SOFT_MARKS))


def _is_vowel(ch: str) -> bool:
    if not ch or not ch.isalpha():
        return False
    base = unicodedata.normalize("NFD", ch)[0].lower()
    return base in "aeiouy" or ch.lower() in _EXTRA_VOWELS


def _is_consonant(ch: str) -> bool:
    return ch.isalpha() and not _is_vowel(ch)


def _max_consonant_run(text: str) -> int:
    best = run = 0
    for ch in text:
        run = run + 1 if _is_consonant(ch) else 0
        best = max(best, run)
    return best


def _acceptable(new: str, old: str) -> bool:
    """'Belirgin bozuk' sonuclari eler: uclu harf, yeni 'yy'/'ii' gibi ciftler, uzun unsuz kumesi."""
    for i in range(len(new) - 2):
        if new[i].isalpha() and new[i] == new[i + 1] == new[i + 2]:
            return False
    if any(ch * 2 in new and ch * 2 not in old for ch in _NO_NEW_DOUBLE):
        return False
    return _max_consonant_run(new) <= max(_max_consonant_run(old), 3)


def _lower_aligned(word: str) -> str:
    """Harf harf kucuk harf; uzunlugu degisen harfler ('İ') oldugu gibi kalir (indeksler hizali)."""
    return "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in word)


def _apply(word: str, start: int, end: int, replacement: str) -> str:
    segment = [ch for ch in word[start:end] if ch.isalpha()]
    if segment and all(ch.isupper() for ch in segment):
        replacement = replacement.upper()
    return word[:start] + replacement + word[end:]


def _edits(lw: str, first: int) -> list[tuple[int, int, str]]:
    """Sirali aday degisiklikler (baslangic, bitis, yeni metin). Ilk harf (first) hic degismez."""
    n = len(lw)
    edits: list[tuple[int, int, str]] = []

    # 1) Cift sesli: "Haaland" -> "Harland" (ardindan tek unsuz + sesli), sonda "Lee" -> "Ley",
    #    aksi halde teklesir ("Kroos" -> "Kros", "Ødegaard" -> "Ødegard")
    for i in range(first, n - 1):
        v = lw[i]
        if v in "aeiou" and lw[i + 1] == v:
            nxt = lw[i + 2] if i + 2 < n else ""
            after = lw[i + 3] if i + 3 < n else ""
            if nxt and nxt != "r" and _is_consonant(nxt) and _is_vowel(after):
                edits.append((i + 1, i + 2, "r"))
            elif i + 2 == n:
                edits.append((i + 1, i + 2, "y"))
            else:
                edits.append((i + 1, i + 2, ""))

    # 2) Fonetik harf ciftleri: "Dijk" -> "Dyk", "Stephan" -> "Stefan"
    for src, dst in _DIGRAPHS:
        start = lw.find(src, first + 1)
        while start != -1:
            edits.append((start, start + len(src), dst))
            start = lw.find(src, start + 1)

    # 3) Sesli kaydirma: "Mbappe" -> "Mbeppe"; komsu harfle yeni cift olusturmaz
    for src, dst in _VOWEL_SHIFTS:
        for i in range(first + 1, n):
            if lw[i] == src and (lw[i - 1] != dst) and (i + 1 >= n or lw[i + 1] != dst):
                edits.append((i, i + 1, dst))

    # 4) Cift unsuz teklesir: "Müller" -> "Müler"
    for i in range(first, n - 1):
        if _is_consonant(lw[i]) and lw[i] == lw[i + 1]:
            edits.append((i + 1, i + 2, ""))

    # 5) Unsuz degisimi (en az 4 harfli kelimelerde): "Öztürk" -> "Östürk"
    if sum(ch.isalpha() for ch in lw) >= 4:
        for src, dst in _CONSONANT_SWAPS:
            for i in range(first + 1, n):
                if lw[i] == src and lw[i - 1] != dst and (i + 1 >= n or lw[i + 1] != dst):
                    edits.append((i, i + 1, dst))

    # 6) Son care: son harften sonra ek ("Oh" -> "Ohe", "Ng" -> "Nge")
    last = max(i for i, ch in enumerate(lw) if ch.isalpha())
    edits.append((last + 1, last + 1, "n" if _is_vowel(lw[last]) else "e"))
    return edits


def _candidates(word: str) -> list[str]:
    lw = _lower_aligned(word)
    first = next((i for i, ch in enumerate(word) if ch.isalpha()), None)
    if first is None:
        return []
    old_key = _key(word)
    seen: set[str] = set()
    out: list[str] = []
    for start, end, replacement in _edits(lw, first):
        new_lw = lw[:start] + replacement + lw[end:]
        if not _acceptable(new_lw, lw):
            continue
        new = _apply(word, start, end, replacement)
        key = _key(new)
        if key == old_key or new in seen:
            continue
        seen.add(new)
        out.append(new)
    if not out:                                   # guvenlik: ek her zaman farkli bir sonuc verir
        out.append(word + ("n" if _is_vowel(lw[-1]) else "e"))
    return out


def tweak_word(word: str, variant: int = 0, strip_soft: bool = False) -> str:
    """
    Tek kelimeye deterministik, hafif harf degisikligi. Ilk harf korunur; sonuc plain_key
    olarak girdiden her zaman farklidir. variant > 0 siradaki aday degisikligi secer.
    Harf icermeyen kelime oldugu gibi doner.
    """
    if strip_soft:
        word = strip_soft_accents(word)
    candidates = _candidates(word)
    if not candidates:
        return word
    if variant < len(candidates):
        return candidates[variant]
    return tweak_word(candidates[variant % len(candidates)], variant // len(candidates) - 1)


# ===========================================================================
# 2) OYUNCU ADLARI
# ===========================================================================
#
# light seviyesi: ada TEK, sistematik ve gercekci bir degisiklik uygulanir.
#
# Kelime sirasi (her kuralda ayni): soyad = son ana kelime (tireli ise sondan basa parcalari),
# sonra ara adlar (sagdan sola), en son ilk isim (bastan sona parcalari). Ana kelime: harf iceren,
# on ek (van, de...), sonek (Jr.) ya da bas harf ("E.") olmayan kelime. Tek ana kelime varsa
# ("Pepe", "Neymar Jr.") o kelime soyad sayilir.
#
# Kural sirasi: kurallar ONCELIK sirasiyla denenir, her kural once soyadda sonra ilk isimde.
# Ilk uygulanabilen degisiklik maskedir (varyant 0); sonrakiler cakisma varyantlaridir.
#   R1 Kisaltma (yalnizca son soyad parcasi): uzun ek (-oğlu, -wski/-wska, -ović/-ević,
#      -ssen/-sson; kok en az 5 harf kalir) belirli harf sayisi kadar, ya da 9+ harfli soyadda
#      son hece (2-4 harf) atilir. "Çalhanoğlu" -> "Çalhano", "Lewandowski" -> "Lewandow",
#      "Bellingham" -> "Belling", "Aubameyang" -> "Aubame".
#   R2 Cift sesli: "aa" + unsuz + sesli -> "ar" ("Haaland" -> "Harland"); sonda "ee" -> "ey"
#      ("Lee" -> "Ley"); diger ciftler teklesir ("Kroos" -> "Kros", "Højgaard" -> "Højgard").
#   R3 Kayan sesli: kelime basinda olmayan "au"/"ou" + unsuz + sesli -> "u"
#      ("Mauro" -> "Muro", "Courtois" -> "Curtois"). "Paul" gibi hece sonu kayanlar dokunulmaz.
#   R4 Sesli kaydirma: ilk harften sonraki ilk DUZ sesli (a e i o u) en yakin sese kayar:
#      a->e, e->a, i->e, o->a, u->a ("Mbappé" -> "Mbeppe", "Orkun" -> "Orkan"). Komsu harfle
#      ayni cift sesli olusursa siradaki sesli denenir; "gue/qui" icindeki sessiz u atlanir.
#      Varyantlar: sonraki sesliler, ardindan ikinci yakin ses (a->o, e->i, i->a, o->u, u->o).
#   R5 Son care: ilk harf sesliyse kaydirilir ("Oh" -> "Ah"), en son harf eki ("Ng" -> "Nge").
#
# Sadelestirme: DEGISEN kelimede yumusak aksanlar atilir (é -> e, ć -> c) ve digraf kurmayan
# sessiz h dusurulur (j/m + h + sesli: "Osimhen" -> "Osimen" -> R4 -> "Osemen"). Bu yazim
# duzeltmesi tek basina maske sayilmaz. Degismeyen kelimeler oldugu gibi kalir (aksanlar dahil).
# Turkce/Iskandinav harfleri (ç ğ ı ö ş ü ø å æ) ve diger isaretli harfler hic hedef alinmaz;
# yalnizca R1 kesilen kuyrukla birlikte dusurebilir.
#
# Istisna tablosu YOKTUR: yukaridaki kurallar kabul orneklerinin hepsini dogal olarak uretir.

_SILENT_H_AFTER = frozenset("jm")                  # "Osimhen", "Jhon": okunmayan h
_PLAIN_VOWELS = frozenset("aeiou")
_NEAREST_VOWEL = {"a": "e", "e": "a", "i": "e", "o": "a", "u": "a"}
_SECOND_VOWEL = {"a": "o", "e": "i", "i": "a", "o": "u", "u": "o"}
_LONG_SURNAME_LETTERS = 9
_MIN_STEM_LETTERS = 5
# (ek, atilan harf sayisi): ek sonrasi kok okunur kalsin diye ekin tamami atilmaz.
# Sadelestirilmis kelimeye bakilir: "-ović" once "-ovic" olur (ć -> c); caron (č) korunur.
_LONG_ENDINGS = (
    ("oğlu", 3), ("oglu", 3), ("wski", 3), ("wska", 3),
    ("ovic", 2), ("evic", 2), ("ovič", 2), ("evič", 2),
    ("ssen", 3), ("sson", 3),
)
# Son hecenin basi olabilecek unsuz kumeleri (uzundan kisaya)
_ONSET_CLUSTERS = ("sch", "ch", "sh", "th", "ph", "kh", "zh", "wh", "nh", "lh")


def _is_particle(token: str) -> bool:
    return plain_key(token) in PARTICLES


def _is_suffix(token: str) -> bool:
    return plain_key(token) in _SUFFIXES


def _has_letter(token: str) -> bool:
    return any(ch.isalpha() for ch in token)


def _is_initial(token: str) -> bool:
    """'E.', 'J.-P.': ozgun veride zaten bas harf olan kelime (maskelenmez, oldugu gibi kalir)."""
    parts = [part for part in token.split("-") if _has_letter(part)]
    return "." in token and bool(parts) and all(_letter_count(part) == 1 for part in parts)


def _first_letter(word: str) -> int | None:
    return next((i for i, ch in enumerate(word) if ch.isalpha()), None)


def _last_letter(word: str) -> int | None:
    return next((i for i in range(len(word) - 1, -1, -1) if word[i].isalpha()), None)


def _vowel_flags(lw: str) -> list[bool]:
    """Harf harf sesli mi? 'y' sesli komsusu yoksa sesli ("Kylian"), varsa unsuzdur ("Lozoya")."""
    base = [ch != "y" and _is_vowel(ch) for ch in lw]
    flags = list(base)
    for i, ch in enumerate(lw):
        if ch == "y":
            flags[i] = not (i > 0 and base[i - 1]) and not (i + 1 < len(lw) and base[i + 1])
    return flags


def _flatten(word: str) -> str:
    """Degisecek kelimenin yazimini sadelestirir: yumusak aksanlar ve sessiz h atilir."""
    word = strip_soft_accents(word)
    out = []
    for i, ch in enumerate(word):
        if (ch in "hH" and 0 < i < len(word) - 1 and word[i - 1].lower() in _SILENT_H_AFTER
                and _is_vowel(word[i + 1])):
            continue
        out.append(ch)
    return "".join(out)


def _truncate(word: str) -> str | None:
    """R1: uzun ek ya da 9+ harfli kelimede son hece (2-4 harf) atilir. Uygulanamazsa None."""
    end = _last_letter(word)
    if end is None:
        return None
    end += 1
    lw = _lower_aligned(word)
    letters = _letter_count(word)
    stem: int | None = None
    for ending, cut in _LONG_ENDINGS:
        if lw[:end].endswith(ending) and letters - cut >= _MIN_STEM_LETTERS:
            stem = end - cut
            break
    if stem is None:
        if letters < _LONG_SURNAME_LETTERS:
            return None
        flags = _vowel_flags(lw)

        def consonant(k: int) -> bool:
            return lw[k].isalpha() and not flags[k]

        coda = end
        while coda > 0 and consonant(coda - 1):
            coda -= 1
        nucleus = coda
        while nucleus > 0 and flags[nucleus - 1]:
            nucleus -= 1
        if nucleus == coda:
            return None
        onset = nucleus
        for cluster in _ONSET_CLUSTERS:
            start = nucleus - len(cluster)
            if start > 0 and lw[start:nucleus] == cluster:
                onset = start
                break
        else:
            if nucleus > 0 and consonant(nucleus - 1):
                onset = nucleus - 1
        size = end - onset
        if 2 <= size <= 4:
            stem = onset
            # "Echeverria" -> "Echeverr" -> "Echever": kokun sonundaki cift unsuz teklesir
            if size < 4 and stem >= 2 and lw[stem - 1] == lw[stem - 2] and consonant(stem - 1):
                stem -= 1
        elif size > 4:
            # Hece cok uzun: once yalniz son unsuzler ("Wendlandt" -> "Wendla"), olmazsa son sesli
            # ile birlikte ("Beckenbauer" -> "Beckenbau"). Kok cift harfle bitmez ("Damsgaa" degil
            # "Damsga").
            options = [k for k in (coda, coda - 1) if 2 <= end - k <= 4]
            clean = [k for k in options if not (k >= 2 and lw[k - 1] == lw[k - 2])]
            stem = (clean or options or [end - 2])[0]
        else:
            stem = end - 2
    if _letter_count(word[:stem]) < _MIN_STEM_LETTERS:
        return None
    return word[:stem] + word[end:]


def _double_vowel(word: str) -> str | None:
    """R2: ilk cift sesli ("Haaland" -> "Harland", "Lee" -> "Ley", "Kroos" -> "Kros")."""
    first = _first_letter(word)
    if first is None:
        return None
    lw = _lower_aligned(word)
    n = len(lw)
    for i in range(first, n - 1):
        v = lw[i]
        if v not in _PLAIN_VOWELS or lw[i + 1] != v:
            continue
        nxt = lw[i + 2] if i + 2 < n else ""
        after = lw[i + 3] if i + 3 < n else ""
        if v == "a" and nxt and nxt != "r" and _is_consonant(nxt) and _is_vowel(after):
            return _apply(word, i + 1, i + 2, "r")
        if v == "e" and i + 2 == n:
            return _apply(word, i + 1, i + 2, "y")
        return word[:i + 1] + word[i + 2:]
    return None


def _diphthong(word: str) -> str | None:
    """R3: kelime basinda olmayan au/ou + unsuz + sesli -> u ("Mauro" -> "Muro")."""
    first = _first_letter(word)
    if first is None:
        return None
    lw = _lower_aligned(word)
    flags = _vowel_flags(lw)
    n = len(lw)
    for i in range(first + 1, n - 1):
        if lw[i] in "ao" and lw[i + 1] == "u":
            j = i + 2
            while j < n and lw[j].isalpha() and not flags[j]:
                j += 1
            if j > i + 2 and j < n and flags[j]:
                return word[:i] + word[i + 1:]
    return None


def _vowel_shifts(word: str, table: dict[str, str], initial: bool = False) -> list[str]:
    """R4 (initial=True ise R5): duz seslilerin en yakin sese kaymasi; ilk uygun sesli once."""
    first = _first_letter(word)
    if first is None:
        return []
    lw = _lower_aligned(word)
    n = len(lw)
    positions = [first] if initial else range(first + 1, n)
    out = []
    for i in positions:
        ch = lw[i]
        if ch not in table:
            continue
        if ch == "u" and i > 0 and lw[i - 1] in "qg" and i + 1 < n and lw[i + 1] in "ei":
            continue                                     # "Rodriguez", "Irazoqui": sessiz u
        new = table[ch]
        if (i > 0 and lw[i - 1] == new) or (i + 1 < n and lw[i + 1] == new):
            continue                                     # yeni "ee"/"aa" olusmaz
        out.append(_apply(word, i, i + 1, new))
    return out


def _append_letter(word: str) -> str | None:
    """R5 son care: sesliyle bitiyorsa 'n', unsuzle bitiyorsa 'e' eklenir ("Ng" -> "Nge")."""
    last = _last_letter(word)
    if last is None:
        return None
    letter = "n" if _is_vowel(word[last]) else "e"
    if word[:last + 1].isupper() and _letter_count(word) > 1:
        letter = letter.upper()
    return word[:last + 1] + letter + word[last + 1:]


def _word_slots(tokens: list[str]) -> list[tuple[int, int, bool]]:
    """Kural deneme sirasiyla (kelime, tire parcasi, son soyad parcasi mi) listesi."""
    mains = [i for i, t in enumerate(tokens)
             if _has_letter(t) and not _is_particle(t) and not _is_suffix(t) and not _is_initial(t)]
    if not mains:                                        # yalnizca on ek/sonek/bas harf
        lettered = [i for i, t in enumerate(tokens) if _has_letter(t)]
        if not lettered:
            return []
        mains = [lettered[-1]]
    surname = mains[-1]
    given = mains[0] if len(mains) > 1 else None
    order = [surname, *reversed(mains[1:-1])] + ([given] if given is not None else [])
    slots: list[tuple[int, int, bool]] = []
    for i in order:
        parts = tokens[i].split("-")
        usable = [j for j, part in enumerate(parts)
                  if _has_letter(part) and not (len(parts) > 1 and _is_particle(part))]
        usable = usable or [j for j, part in enumerate(parts) if _has_letter(part)]
        last = usable[-1]
        if i != given:
            usable.reverse()                             # soyad: sondan basa ("Morel-Lacroix")
        slots.extend((i, j, i == surname and j == last) for j in usable)
    return slots


def _slot_edits(tokens: list[str], slots: list[tuple[int, int, bool]]) -> Iterator[tuple[int, int, str | None]]:
    """Oncelik sirasiyla (kelime, parca, yeni parca) adaylari: R1 > R2 > R3 > R4 > R5."""
    flat = {(i, j): _flatten(tokens[i].split("-")[j]) for i, j, _ in slots}
    for i, j, last in slots:
        if last:
            yield i, j, _truncate(flat[i, j])
    for rule in (_double_vowel, _diphthong):
        for i, j, _ in slots:
            yield i, j, rule(flat[i, j])
    for table in (_NEAREST_VOWEL, _SECOND_VOWEL):
        for i, j, _ in slots:
            for new in _vowel_shifts(flat[i, j], table):
                yield i, j, new
    for i, j, _ in slots:
        for new in _vowel_shifts(flat[i, j], _NEAREST_VOWEL, initial=True):
            yield i, j, new
    for i, j, _ in slots:
        yield i, j, _append_letter(flat[i, j])


@lru_cache(maxsize=16384)
def _light_candidates(clean: str) -> tuple[str, ...]:
    """Tek degisiklikli maskeler, oncelik sirasiyla; hepsi plain_key olarak ozgun addan farkli."""
    tokens = clean.split(" ")
    seen = {_key(clean)}
    out: list[str] = []
    for i, j, new in _slot_edits(tokens, _word_slots(tokens)):
        if not new:
            continue
        parts = tokens[i].split("-")
        parts[j] = new
        candidate = " ".join([*tokens[:i], "-".join(parts), *tokens[i + 1:]])
        key = _key(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return tuple(out)


@lru_cache(maxsize=4096)
def _light_sequence(clean: str) -> tuple[str, ...]:
    """
    Cakisma varyantlari: once tek degisiklikli adaylar, yetmezse bunlara ikinci degisiklik
    (genislik oncelikli). Ozgun ada geri donen birlesimler elenir. En fazla _MAX_VARIANTS.
    """
    original = _key(clean)
    seen = {original}
    out: list[str] = []
    frontier = [clean]
    while frontier and len(out) < _MAX_VARIANTS:
        nxt: list[str] = []
        for name in frontier:
            for candidate in _light_candidates(name):
                key = _key(candidate)
                if key in seen:
                    continue
                seen.add(key)
                out.append(candidate)
                nxt.append(candidate)
                if len(out) >= _MAX_VARIANTS:
                    return tuple(out)
        frontier = nxt
    return tuple(out)


def _mask_player_light(clean: str, variant: int) -> str | None:
    """variant 0: kural sirasindaki ilk degisiklik. Aday kalmadiysa None (strong'a duser)."""
    first = _light_candidates(clean)
    if variant < len(first):
        return first[variant]
    sequence = _light_sequence(clean)
    if not sequence:
        return None
    return sequence[variant % len(sequence)]        # tukenince tekrar eder; _unique roma rakamina gecer


# Tamamen kurgusal ad havuzlari: (adlar, soyad on hecesi, soyad son hecesi)
_STRONG_POOLS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "tr": (
        ("Aras", "Batu", "Berk", "Cenk", "Çağan", "Deniz", "Doruk", "Ege", "Emir", "Eren", "Görkem",
         "İlker", "Kaan", "Koray", "Mert", "Oğuz", "Onur", "Sarp", "Taylan", "Tuna", "Ulaş", "Yağız"),
        ("Ak", "Alp", "Bal", "Boz", "Demir", "Er", "Gök", "Kara", "Kor", "Öz", "Sarı", "Tok", "Tunç",
         "Uz", "Yıldız", "Ilgaz"),
        ("alp", "can", "dağ", "gil", "han", "kan", "oğlu", "soy", "taş", "tekin", "yurt", "el", "ay"),
    ),
    "es": (
        ("Álvaro", "Bruno", "Darío", "Esteban", "Gonzalo", "Iker", "Joaquín", "Lisandro", "Mateo",
         "Nicolás", "Rodrigo", "Santiago", "Tomás", "Valentín", "Xabier"),
        ("Alba", "Bel", "Cas", "Del", "Esc", "Fer", "Gal", "Ibar", "Lar", "Mend", "Oliv", "Sal", "Zub"),
        ("ares", "ena", "era", "ido", "ino", "oza", "uri", "ueta", "ano", "illa"),
    ),
    "de": (
        ("Anton", "Bastian", "Dominik", "Emil", "Finn", "Hannes", "Jonas", "Lennart", "Malte",
         "Niklas", "Ole", "Quirin", "Rasmus", "Tilo", "Veit"),
        ("Adel", "Berg", "Eich", "Fal", "Grün", "Hass", "Kron", "Lind", "Mahl", "Rein", "Stein", "Wald"),
        ("bach", "berger", "brink", "dorf", "feld", "hart", "hof", "inger", "mann", "rath", "stedt"),
    ),
    "en": (
        ("Alfie", "Brandon", "Callum", "Dexter", "Elliot", "Finley", "Harvey", "Jasper", "Kieran",
         "Morgan", "Rhys", "Tristan", "Wesley", "Owen"),
        ("Ash", "Black", "Bram", "Cald", "Craw", "Dun", "Fair", "Hart", "Kel", "North", "Red", "Whit"),
        ("bridge", "by", "croft", "field", "ford", "ham", "ley", "more", "ridge", "ton", "wood"),
    ),
    "it": (
        ("Alessio", "Brando", "Cesare", "Dario", "Elia", "Filippo", "Gianluca", "Ivano", "Mattia",
         "Nicola", "Ottavio", "Raffaele", "Tiziano", "Valerio"),
        ("Bel", "Cal", "Cor", "Fal", "Gal", "Lom", "Mar", "Pal", "Ros", "San", "Ser", "Tor", "Ven"),
        ("aldi", "ari", "elli", "etti", "ini", "one", "otti", "ucci", "ano", "esi"),
    ),
    "fr": (
        ("Aurélien", "Bastien", "Cédric", "Dorian", "Florian", "Gaël", "Loïc", "Mathéo", "Noé",
         "Rémi", "Tanguy", "Yohan"),
        ("Beau", "Bel", "Char", "Dal", "Fon", "Gar", "Lam", "Mar", "Mont", "Roch", "Val", "Ver"),
        ("ard", "ault", "eau", "elle", "et", "ier", "in", "ois", "on", "ot"),
    ),
    "pt": (
        ("Caio", "Davi", "Enzo", "Fabrício", "Gustavo", "Heitor", "Igor", "Lucca", "Murilo",
         "Otávio", "Renan", "Thiago", "Vitor"),
        ("Alm", "Bar", "Cor", "Fer", "Gou", "Lim", "Mac", "Mor", "Pin", "Ros", "Sil", "Tav"),
        ("ado", "eira", "ello", "inho", "oso", "uto", "ares", "anha"),
    ),
    "nordic": (
        ("Anders", "Björn", "Einar", "Fredrik", "Halvor", "Ivar", "Kasper", "Leif", "Mads", "Oskar",
         "Rune", "Sindre"),
        ("Berg", "Dahl", "Eng", "Fjell", "Holm", "Lund", "Nord", "Sand", "Sol", "Strand", "Vik"),
        ("by", "dal", "gaard", "heim", "lund", "qvist", "rud", "sen", "strøm", "vik"),
    ),
    "neutral": (
        ("Adrian", "Bojan", "Dario", "Elian", "Goran", "Ilian", "Kristo", "Luka", "Marin", "Nikola",
         "Petar", "Radu", "Stefan", "Teo", "Viktor"),
        ("Adan", "Bor", "Cal", "Dar", "Kor", "Mor", "Nov", "Ral", "Tel", "Var", "Zor"),
        ("ano", "ek", "el", "ic", "in", "ov", "sky", "son", "escu", "is"),
    ),
}

_REGION_BY_NATION: dict[str, str] = {}
for _region, _keys in {
    "tr": ("tur", "turkiye", "turkey"),
    "es": ("esp", "ispanya", "spain", "arg", "uru", "col", "mex", "chi", "per", "ecu", "par", "ven"),
    "de": ("ger", "almanya", "germany", "aut", "sui"),
    "en": ("eng", "ingiltere", "england", "sco", "wal", "nir", "irl", "usa", "aus", "can"),
    "it": ("ita", "italya", "italy"),
    "fr": ("fra", "fransa", "france", "bel"),
    "pt": ("por", "portekiz", "portugal", "bra", "brezilya", "brazil"),
    "nordic": ("nor", "den", "swe", "isl", "fin", "norvec", "danimarka", "isvec"),
}.items():
    for _k in _keys:
        _REGION_BY_NATION[_k] = _region


def _region(nationality: str | None, name: str) -> str:
    region = _REGION_BY_NATION.get(plain_key(nationality or ""))
    if region:
        return region
    lowered = name.casefold()
    if any(ch in lowered for ch in "ğşı"):
        return "tr"
    if any(ch in lowered for ch in "øæå"):
        return "nordic"
    if "ñ" in lowered:
        return "es"
    if any(ch in lowered for ch in "ãõ"):
        return "pt"
    if "ß" in lowered:
        return "de"
    return "neutral"


def _pick(pool: tuple[str, ...], digest: bytes, offset: int) -> str:
    return pool[int.from_bytes(digest[offset:offset + 4], "big") % len(pool)]


def _mask_player_strong(clean: str, nationality: str | None, variant: int) -> str:
    firsts, heads, tails = _STRONG_POOLS[_region(nationality, clean)]
    digest = hashlib.sha256(f"cm-name-mask|{_key(clean)}|{variant}".encode()).digest()
    head = _pick(heads, digest, 4)
    tail = _pick(tails, digest, 8)
    if head.casefold().endswith(tail[0]) or head.casefold() == tail:
        tail = tails[(tails.index(tail) + 1) % len(tails)]
    return f"{_pick(firsts, digest, 0)} {head}{tail}"


def _has_latin_letter(text: str) -> bool:
    return any(ch.isalpha() for ch in plain_key(text))


def _mask_player(clean: str, level: str, nationality: str | None, variant: int) -> str:
    latin = _has_latin_letter(clean)
    if level == "light" and latin:
        masked = _mask_player_light(clean, variant)
        if masked and masked.strip() and _key(masked) != _key(clean):
            return masked
    # strong seviye; ya da hafif degisiklik uygulanamayan ad (Latin harfsiz, "123")
    light = (_mask_player_light(clean, 0) or "") if latin else ""
    bump = variant
    masked = _mask_player_strong(clean, nationality, bump)
    while _key(masked) in (_key(clean), _key(light)):
        bump += 1
        masked = _mask_player_strong(clean, nationality, bump)
    return masked


def mask_player_name(full_name: str, level: str = DEFAULT_MASK_LEVEL, nationality: str | None = None) -> str:
    """
    Oyuncu adini maskeler. Sonuc bos olmaz ve plain_key olarak ozgun addan farklidir.
        light : tek kural tabanli degisiklik, ilk isim butun kalir (bkz. bolum 2 basi):
                "Erling Haaland" -> "Erling Harland", "Kylian Mbappé" -> "Kylian Mbeppe",
                "Hakan Çalhanoğlu" -> "Hakan Çalhano", "Mauro Icardi" -> "Muro Icardi"
        strong: ozgun addan (sha256) turetilen tamamen kurgusal ad; uyruk havuzu secer.
    Ayni girdi her calistirmada ayni sonucu verir (tuzlu hash() kullanilmaz).
    """
    level = _check_level(level)
    return _mask_player(_clean(full_name), level, nationality, 0)


def build_player_mask_map(
    entries: Iterable[str | tuple[str, str | None]],
    level: str = DEFAULT_MASK_LEVEL,
    reserved: Iterable[str] = (),
) -> dict[str, str]:
    """
    Toplu oyuncu maskesi: {ozgun yazim: maske}. Girdi ad ya da (ad, uyruk) olabilir.
    Farkli ozgun adlar ayni maskeye dusmez; maske baska bir ozgun adla (ya da `reserved`
    icindeki adlarla) cakismaz. Ayni ad (farkli yazim/buyuk-kucuk harf) ayni maskeyi alir.
    """
    level = _check_level(level)
    spellings: dict[str, set[str]] = {}
    nations: dict[str, str] = {}
    for entry in entries:
        name, nationality = (entry, None) if isinstance(entry, str) else entry
        if name is None:
            continue
        key = _key(name)
        spellings.setdefault(key, set()).add(name)
        if nationality and (key not in nations or nationality < nations[key]):
            nations[key] = nationality

    used = {k for k in (_key(r) for r in reserved) if k} | set(spellings)
    result: dict[str, str] = {}
    for key in sorted(spellings):
        base = _representative(spellings[key])
        masked = _unique(lambda v, b=base, k=key: _mask_player(b, level, nations.get(k), v), used)
        used.add(_key(masked))
        for original in spellings[key]:
            result[original] = masked
    return result


def _representative(spellings: set[str]) -> str:
    """Ayni anahtarli yazimlardan maskelenecek olan: tamamen BUYUK harf olmayan, sonra alfabetik ilk."""
    return _clean(min(spellings, key=lambda s: (s.isupper(), s)))


def _unique(make, used: set[str], extra_keys=None) -> str:
    """Kullanilmamis ilk varyanti secer; hepsi doluysa roma rakamiyla ayristirir."""
    masked = make(0)
    for variant in range(_MAX_VARIANTS):
        masked = make(variant)
        keys = {_key(masked)} | (extra_keys(masked) if extra_keys else set())
        if not keys & used:
            return masked
    for numeral in _ROMAN:
        candidate = f"{masked} {numeral}"
        if _key(candidate) not in used:
            return candidate
    raise ValueError(f"Benzersiz maske üretilemedi: {masked}")      # pratikte olmaz


# ===========================================================================
# 3) KULUPLER
# ===========================================================================

_UNNAMED_CLUB = "İsimsiz Kulüp"
_UNNAMED_LEAGUE_SUFFIX = "Ligi"


def _club_names(club) -> tuple[str, ...]:
    return (club.name, *club.aliases)


# Gercek (maskesiz) rehber yazimlari: sizinti denetimi bunlarla TAM esitlik arar
_REAL_CLUB_KEYS = frozenset(plain_key(n) for c in known_clubs() for n in _club_names(c))
_REAL_LEAGUE_KEYS = frozenset(k for keys in real_league_keys().values() for k in keys)
_MASKED_LEAGUE_KEYS = frozenset(plain_key(m) for m in MASKED_LEAGUES.values())
# Kural tabanli maskelerin asla dusmemesi gereken anahtarlar (gercek + maskeli rehber adlari)
_DIRECTORY_RESERVED = frozenset(
    {plain_key(n) for c in known_clubs() for n in (*_club_names(c), c.masked)}
    | {split_name(n)[0] for c in known_clubs() for n in (*_club_names(c), c.masked)}
) - {""}


def _club_core_keys(name: str) -> set[str]:
    core = split_name(name)[0]
    return {core} if core else set()


def _roman_suffix(variant: int) -> str:
    return "" if variant == 0 else f" {_ROMAN[(variant - 1) % len(_ROMAN)]}"


def _letter_count(token: str) -> int:
    return sum(ch.isalpha() for ch in token)


def _rule_club_mask(name: str, variant: int) -> str:
    """Kurum kisaltmalari atilir; en uzun (en ayirt edici) kelime hafifce degisir."""
    tokens = name.split(" ")
    core = [t for t in tokens if plain_key(t) not in NOISE_TOKENS] or tokens
    lettered = [i for i, t in enumerate(core) if _has_letter(t)]
    if not lettered:                                   # "1907" gibi harfsiz ad
        return f"{' '.join(core)} Kulübü{_roman_suffix(variant)}"
    target = max(lettered, key=lambda i: (_letter_count(core[i]), -i))
    parts = core[target].split("-")
    longest = max((i for i, part in enumerate(parts) if _has_letter(part)),
                  key=lambda i: (_letter_count(parts[i]), -i))
    parts[longest] = tweak_word(parts[longest], variant)
    core[target] = "-".join(parts)
    return " ".join(core)


def build_club_mask_map(names: Iterable[str], reserved: Iterable[str] = ()) -> dict[str, str]:
    """
    Toplu kulup maskesi: {ozgun yazim: maske}. Rehber kulubu -> rehberdeki maskeli ad;
    bilinmeyen kulup -> kural tabanli maske. Iki farkli kulup ayni maskeye dusmez ve hicbir
    maske rehberdeki bir kulubun (gercek ya da maskeli) adina esit olmaz.
    """
    result: dict[str, str] = {}
    unknown: dict[str, set[str]] = {}
    for name in names:
        if name is None:
            continue
        if not name.strip():
            result[name] = _UNNAMED_CLUB
            continue
        info = lookup_club(name)
        if info is not None:
            result[name] = info.masked
        else:
            unknown.setdefault(_key(name), set()).add(name)

    used = set(_DIRECTORY_RESERVED) | set(unknown) | {k for k in (_key(r) for r in reserved) if k}
    # Bilinmeyen kulubun ozgun cekirdek adi da ("Kuzey SK" -> "kuzey") baska bir maske olamaz
    used |= {core for spellings in unknown.values() for n in spellings for core in _club_core_keys(n)}
    for key in sorted(unknown):
        base = _representative(unknown[key])
        masked = _unique(lambda v, b=base: _rule_club_mask(b, v), used, _club_core_keys)
        used.add(_key(masked))
        used |= _club_core_keys(masked)
        for original in unknown[key]:
            result[original] = masked
    return result


def mask_club_name(name: str) -> str:
    """
    Kulup adini maskeler. Rehber kulubu (her yazimi ya da zaten maskeli adi) -> maskeli ad;
    bilinmeyen kulup -> kural tabanli maske ("Kuzey Yıldızı SK" -> "Kuzey Yıldısı").
    """
    return build_club_mask_map([name])[name]


def resolve_masked_club(query: str) -> str | None:
    """Gercek ad / yazim farki / maskeli ad -> rehberdeki maskeli ad. Bilinmiyorsa None."""
    info = lookup_club(query or "")
    return info.masked if info is not None else None


# ===========================================================================
# 4) LIGLER
# ===========================================================================

_GENERIC_LEAGUE_WORDS = frozenset({
    "league", "liga", "lig", "ligi", "ligue", "division", "divisie", "serie", "premier", "super",
    "first", "second", "third", "primera", "segunda", "national", "pro", "championship", "cup",
    "a", "b", "1", "2", "3", "de", "of", "the", "la",
})


def _rule_league_mask(name: str, variant: int) -> str:
    tokens = name.split(" ")
    lettered = [i for i, t in enumerate(tokens) if _has_letter(t)]
    if not lettered:
        return f"{name} {_UNNAMED_LEAGUE_SUFFIX}{_roman_suffix(variant)}"
    distinctive = [i for i in lettered
                   if plain_key(tokens[i]) not in _GENERIC_LEAGUE_WORDS and _letter_count(tokens[i]) >= 4]
    target = max(distinctive or lettered, key=lambda i: (_letter_count(tokens[i]), -i))
    out = list(tokens)
    out[target] = tweak_word(tokens[target], variant)
    return " ".join(out)


def _known_league_mask(name: str) -> str | None:
    resolved = canonical_league(name)
    if resolved is None or resolved[1] == OTHER_COUNTRY:
        return None
    return MASKED_LEAGUES[resolved[0]]


def build_league_mask_map(names: Iterable[str | None], reserved: Iterable[str] = ()) -> dict[str, str]:
    """Toplu lig maskesi: bilinen lig -> '<Ulke> Elit Ligi'; bilinmeyen -> kural tabanli. Bos kalir."""
    result: dict[str, str] = {}
    unknown: dict[str, set[str]] = {}
    for name in names:
        if name is None:
            continue
        if not name.strip():
            result[name] = name
            continue
        known = _known_league_mask(name)
        if known is not None:
            result[name] = known
        else:
            unknown.setdefault(_key(name), set()).add(name)

    used = set(_REAL_LEAGUE_KEYS | _MASKED_LEAGUE_KEYS) | set(unknown)
    used |= {k for k in (_key(r) for r in reserved) if k}
    for key in sorted(unknown):
        base = _representative(unknown[key])
        masked = _unique(lambda v, b=base: _rule_league_mask(b, v), used)
        used.add(_key(masked))
        for original in unknown[key]:
            result[original] = masked
    return result


def mask_league_name(name: str) -> str:
    """Bilinen lig (gercek/yazim farki/maskeli) -> maskeli lig adi; bilinmeyen -> hafif degisiklik."""
    if not name or not name.strip():
        return name
    return build_league_mask_map([name])[name]


# ===========================================================================
# 5) SIZINTI DENETIMI
# ===========================================================================

def is_real_name(name: str | None) -> bool:
    """
    Ad, rehberdeki GERCEK bir kulup/lig adinin ya da yaziminin kendisi mi?
    TAM esitlik: plain_key birebir ayni ya da kurum kisaltmalari ayiklandiginda gercek bir
    kulube cozuluyor ("Juventus FC"). Alt dize aranmaz: "Provence Phocéens" sizinti degildir.
    """
    key = plain_key(name or "")
    if not key:
        return False
    if key in _REAL_CLUB_KEYS or key in _REAL_LEAGUE_KEYS:
        return True
    info = lookup_club(name)
    return info is not None and split_name(name)[0] != split_name(info.masked)[0]


def find_leaks(names: Iterable[str | None]) -> list[str]:
    """Verilen adlardan maskelenmemis gercek kulup/lig adi olanlari (sirayla) dondurur."""
    return [name for name in names if name is not None and is_real_name(name)]
