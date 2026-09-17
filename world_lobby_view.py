"""
world_lobby_view.py
===================
Dunya lobisi (Faz 12 / 14. Asama, 12A): Dunyalarim / Dunya olustur / Davet koduyla katil / Acik dunyalar ve
kulup secimi. Kenar cubugundaki "Dunyalar" (sb_worlds) ya da uyeligi dusen oturum buraya yonlenir
(web_common.LOBBY_KEY). Callback'ler requires_auth / member_callback ile ACIKCA sarilir (web_app'in otomatik
sarma dongusu bu modulu gormez; tests/test_world_schema.py her cb_* icin denetler).

Durum: iskelet ("Yakinda"); Faz 12 A4 paketinde doldurulacak.
"""

from __future__ import annotations

import streamlit as st

from web_common import show_flash


def render_lobby() -> None:
    """Lobi sayfasi (flash alani: lobby)."""
    show_flash("lobby")
    st.info("Yakında")


def render_club_offers() -> None:
    """Paylasilan dunyada kulup secimi (co_query, co_only_eligible, co_claim_{team_id})."""
    st.info("Yakında")
