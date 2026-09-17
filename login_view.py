"""
login_view.py
=============
OFM giris / kayit sayfasi (taktik tahtasi vitrini). Streamlit gorunumu; kurallar ve oturum web_app'tadir.

Duzen: ustte marka + tema secici; solda etiket, slogan ve giris (ya da kayit) karti; sagda hareketli taktik
tahtasi; altta uc adim ve uc ozellik. Callback'ler web_app'tan verilir (PUBLIC_CALLBACKS: cb_login,
cb_register, cb_theme, cb_auth_view) -- bu modul oturum ya da veritabani bilmez.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from ofm_theme import (
    THEME_LABELS,
    brand_html,
    login_headline_html,
    login_hero_html,
    login_intro_html,
    login_steps_html,
)


def render_login(
    *,
    theme: str,
    view: str,
    show_flash: Callable[[], None],
    on_theme: Callable[[], None],
    on_login: Callable[[], None],
    on_register: Callable[[], None],
    on_view: Callable[[str], None],
) -> None:
    brand, switch = st.columns([7, 3], vertical_alignment="center")
    with brand:
        st.markdown(brand_html(), unsafe_allow_html=True)
    with switch:
        st.radio("Tema", list(THEME_LABELS.values()), key="theme_choice", horizontal=True, on_change=on_theme,
                 label_visibility="collapsed")

    left, right = st.columns([5, 7], gap="large")
    with left:
        st.markdown(login_intro_html(), unsafe_allow_html=True)
        with st.container(key="ofm_login_card"):
            st.markdown(login_headline_html(view), unsafe_allow_html=True)
            show_flash()
            if view == "register":
                st.text_input("Kullanıcı adı", key="reg_user", placeholder="ör. asil_gungor (e-posta değil)",
                              help="3-32 karakter: harf, rakam, _ . - (harf ya da rakamla başlamalı). "
                                   "E-posta adresi kullanılamaz: @ işareti kabul edilmez.")
                a, b = st.columns(2)
                a.text_input("Parola", type="password", key="reg_pass",
                             help="En az 8 karakter; en az bir harf ve bir rakam; kullanıcı adını içermemeli.")
                b.text_input("Parola (tekrar)", type="password", key="reg_pass2")
                st.button("Kayıt ol ve başla", key="reg_btn", on_click=on_register, type="primary", width="stretch")
                note, link = st.columns([3, 2], vertical_alignment="center")
                note.markdown('<p class="ofm-login-note">Zaten hesabın var mı?</p>', unsafe_allow_html=True)
                link.button("Giriş yap", key="auth_to_login", on_click=on_view, args=("login",), type="tertiary")
                st.caption("Parolan şifrelenmiş (scrypt) olarak saklanır. İlk kayıt olan menajer mevcut kariyeri "
                           "devralır, sonrakilere yeni bir dünya kurulur.")
            else:
                a, b = st.columns(2)
                a.text_input("Kullanıcı adı", key="login_user")
                b.text_input("Parola", type="password", key="login_pass")
                action, link = st.columns([2, 3], vertical_alignment="center")
                action.button("Giriş yap", key="login_btn", on_click=on_login, type="primary", width="stretch")
                link.button("Yeni misin? Menajer hesabı aç", key="auth_to_register", on_click=on_view,
                            args=("register",), type="tertiary")
    with right:
        st.markdown(login_hero_html(theme), unsafe_allow_html=True)
    st.markdown(login_steps_html(), unsafe_allow_html=True)
