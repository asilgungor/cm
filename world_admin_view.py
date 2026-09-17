"""
world_admin_view.py
===================
Dunya yonetimi sekmesi (Faz 12 / 14. Asama, 12A; web_app.TAB_ADMIN, yalnizca OWNER / ADMIN): menajerler,
hafta, kurallar, adil oyun incelemesi, davet ve olay kaydi. Callback'ler web_common.admin_callback ile ACIKCA
sarilir.

Durum: iskelet ("Yakinda"); Faz 12 A4 paketinde doldurulacak.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from web_common import show_flash

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Team


def admin_tab(db, cm: CareerManager, team: Team | None) -> None:
    """Flash alani: admin."""
    show_flash("admin")
    st.info("Yakında")
