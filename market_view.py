"""
market_view.py
==============
Teklifler & Mesajlar sekmesi (Faz 12 / 14. Asama, 12B; web_app.TAB_HUB, paylasilan dunyada): gelen / giden
teklifler, kiraliklar, listelerim, mesajlar, dunya panosu, bildirimler; transfer pazarinda insan kulubunun
oyuncusuna teklif paneli. Callback'ler web_common.member_callback ile ACIKCA sarilir.

Durum: iskelet ("Yakinda"); Faz 12 B4 paketinde doldurulacak.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from web_common import show_flash

if TYPE_CHECKING:
    from career_manager import CareerManager
    from models import Team


def hub_tab(db, cm: CareerManager, team: Team | None) -> None:
    """Flash alani: hub."""
    show_flash("hub")
    st.info("Yakında")


def human_offer_panel(db, cm: CareerManager, team: Team, player_id: int) -> None:
    """Transfer pazari: insan menajerin kulubundeki oyuncuya teklif (mkt_kind, mkt_fee, mkt_h_offer ...)."""
    st.info("Yakında")
