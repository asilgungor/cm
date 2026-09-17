"""
national_view.py
================
Milli Takim sekmesi (Faz 12 / 14. Asama, 12C; web_app.TAB_NATIONAL, milli takimlar acik dunyada): is
teklifleri, kadro cagrisi ve ilk 11, fikstur ve gruplar, Dunya Kupasi agaci. Callback'ler
web_common.member_callback ile ACIKCA sarilir.

Durum: iskelet ("Yakinda"); Faz 12 C3 paketinde doldurulacak.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from web_common import show_flash

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Team


def national_tab(db, cm: CareerManager, team: Team | None) -> None:
    """Flash alani: national."""
    show_flash("national")
    st.info("Yakında")
