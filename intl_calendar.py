"""
intl_calendar.py
================
Uluslararasi mac takvimi (Faz 12 / 14. Asama, 12C). SAF modul.

    international_weeks -> lig sezonuna esit aralikla yayilmis milli ara haftalari (windows adet)
    Dunya Kupasi sezon sonunda (close-season mac gunleri) oynanir; start_new_season tamamlanana kadar bekler.

Durum: iskelet; Faz 12 C1 paketinde doldurulacak.
"""

from __future__ import annotations

_PACKAGE = "Faz 12: intl_calendar (C1)"


def international_weeks(league_weeks: int, windows: int = 3) -> list[int]:
    raise NotImplementedError(_PACKAGE)
