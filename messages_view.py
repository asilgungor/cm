"""
messages_view.py
================
Mesajlar, dunya panosu ve bildirimler (Faz 12 / 14. Asama, 12B; Teklifler & Mesajlar sekmesinin bolumleri).
Metinler duz metin cizilir (st.text / escape), asla unsafe_allow_html. Callback'ler web_common.member_callback
ile ACIKCA sarilir.

Durum: iskelet ("Yakinda"); Faz 12 B4 paketinde doldurulacak.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    from career_manager import CareerManager


def messages_section(db, cm: CareerManager) -> None:
    st.info("Yakında")


def board_section(db, cm: CareerManager) -> None:
    st.info("Yakında")


def notifications_section(db, cm: CareerManager) -> None:
    st.info("Yakında")
