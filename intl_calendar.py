"""
intl_calendar.py
================
Uluslararasi mac takvimi (Faz 12 / 14. Asama, 12C). SAF modul: veritabani, ORM ve Streamlit bilmez.

    international_weeks  -> lig sezonuna esit aralikla yayilmis milli ara haftalari (windows adet)
    matchday_weeks       -> eleme mac gunlerini (1..n) milli ara haftalarina dagitir
    is_world_cup_season  -> Dunya Kupasi bu sezonun sonunda mi? (WorldRules.world_cup_every_seasons)

Kurallar:
    * Milli ara haftasi, lig haftalariyla AYNI haftadadir (arena ara haftasi gibi): o hafta lig maci da oynanir,
      milli maclar ek mac gunudur. Milli maclarda kondisyon kaybi ve sakatlik yoktur (national_teams C2).
    * Ilk ve son lig haftasi hic milli ara olmaz (sezon acilisi ve sampiyonluk haftasi korunur).
      Adaylar 2 .. league_weeks-1 haftalaridir (C = league_weeks - 2 aday).
    * Pencere sayisi n = min(windows, C); C <= 0 ise (2 haftalik ya da daha kisa sezon) milli ara YOKTUR.
      Kucuk sezonlar hata vermez, yalnizca daha az pencere alir: 6 haftalik sentetik sezon -> [2, 4, 5],
      7 hafta -> [2, 4, 6], 4 hafta -> [2, 3], 3 hafta -> [2].
    * Yayilim: adaylar n esit dilime bolunur, her dilimin ortasi alinir:
          hafta_k = 2 + floor((2k + 1) * C / (2n))      (k = 0 .. n-1)
      C / n >= 1 oldugundan haftalar kesin artandir (tekrar yok) ve hepsi [2, league_weeks-1] icindedir.
      38 haftalik lig -> [8, 20, 32].
    * Dunya Kupasi sezon SONUNDA (close season) ardisik mac gunlerinde oynanir; lig haftasi kullanmaz
      (gun plani: world_cup.plan(size).stage_days()).
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_WINDOWS = 3


def international_weeks(league_weeks: int, windows: int = DEFAULT_WINDOWS) -> list[int]:
    """
    Milli ara haftalari (1-tabanli lig haftalari, artan). Ilk ve son lig haftasindan kacinir; sezon
    windows + 2 haftadan kisaysa daha az pencere dondurur (bkz. modul aciklamasi). Deterministiktir.
    """
    if isinstance(league_weeks, bool) or not isinstance(league_weeks, int):
        raise ValueError("Lig hafta sayısı tam sayı olmalı.")
    if isinstance(windows, bool) or not isinstance(windows, int):
        raise ValueError("Milli ara sayısı tam sayı olmalı.")
    if windows < 0:
        raise ValueError("Milli ara sayısı negatif olamaz.")
    candidates = league_weeks - 2
    count = min(windows, candidates)
    if count <= 0:
        return []
    return [2 + (2 * k + 1) * candidates // (2 * count) for k in range(count)]


def matchday_weeks(matchdays: int, weeks: Sequence[int]) -> list[int]:
    """
    Mac gunu k (0-tabanli) -> oynanacagi milli ara haftasi. Mac gunleri pencerelere esit dagitilir,
    sira korunur (azalmayan): pencere_k = floor((2k + 1) * W / (2M)).
        6 mac gunu, 3 pencere -> her pencerede 2 (cift mac haftasi)
        3 mac gunu, 3 pencere -> her pencerede 1
        2 mac gunu, 3 pencere -> ilk ve son pencere
    Pencere yoksa ve mac gunu varsa ValueError (eleme oynanamaz; cagiran elemeyi atlar).
    """
    if isinstance(matchdays, bool) or not isinstance(matchdays, int) or matchdays < 0:
        raise ValueError("Maç günü sayısı negatif olmayan bir tam sayı olmalı.")
    ordered = sorted(set(int(w) for w in weeks))
    if matchdays == 0:
        return []
    if not ordered:
        raise ValueError("Milli ara haftası yok: eleme maçları bu sezona yerleştirilemez.")
    count = len(ordered)
    return [ordered[(2 * k + 1) * count // (2 * matchdays)] for k in range(matchdays)]


def is_world_cup_season(season: int, every_seasons: int = 1) -> bool:
    """
    Dunya Kupasi bu sezonun sonunda mi? every_seasons = 1 -> her sezon; 2 -> 2., 4., 6. sezon ...
    Elemeler Dunya Kupasi sezonunun milli aralarinda oynanir.
    """
    if season < 1:
        raise ValueError("Sezon 1'den başlar.")
    if every_seasons < 1:
        raise ValueError("Dünya Kupası sıklığı en az 1 sezon olmalı.")
    return season % every_seasons == 0
