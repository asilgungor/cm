"""
world_panel_view.py
===================
Paylasilan dunya kenar cubugu paneli (Faz 12 / 14. Asama, 12A): dunya adi, sezon/hafta, sure sayaci,
"3/5 hazir" ve bekleyenler, hazir / zorla ilerlet dugmeleri, bildirim rozeti, "Dunyalar" (sb_worlds).
Paylasilan dunyada takim secicinin (sb_team / sb_set_team / sb_change_mode) ve kariyer tohumunun yerini alir.

Durum: iskelet ("Yakinda"); Faz 12 A4 paketinde doldurulacak.
"""

from __future__ import annotations

import streamlit as st

from web_common import show_flash


def sidebar_panel() -> None:
    """Kenar cubugu (st.sidebar icinde cagrilir; flash alani: world)."""
    show_flash("world")
    st.info("Yakında")
