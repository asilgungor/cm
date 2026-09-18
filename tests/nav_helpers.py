"""
Faz 13I: web testleri icin menu yardimcilari. Ust sekmeler (st.tabs) kalkti; her cizimde yalnizca secili sayfa cizilir.

    start(at, "kadro")      ilk at.run()'dan ONCE: oturum o sayfada baslar (nav_page)
    goto(at, "Kadro")       kenar cubugu menusunden sayfaya gider (nav_to_{slug} tiklanir, sayfa yeniden cizilir)
    menu(at)                menudeki sayfalar (slug, sirasiyla)
    page(at)                secili sayfa (slug)
Sayfa adi slug ("kadro"), etiket ("📋 Kadro", "Kadro") ya da takma ad ("finans", "lig") olabilir (nav_view.resolve).
"""

from __future__ import annotations


def _slug(page: str) -> str:
    import nav_view

    slug = nav_view.resolve(page)
    assert slug is not None, f"bilinmeyen sayfa: {page}"
    return slug


def start(at, page: str):
    import nav_view

    at.session_state[nav_view.NAV_KEY] = _slug(page)
    return at


def goto(at, page: str):
    import nav_view

    slug = _slug(page)
    at.button(key=nav_view.button_key(slug)).click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[nav_view.NAV_KEY] == slug
    return at


def menu(at) -> list[str]:
    import nav_view

    prefix = nav_view.BUTTON_PREFIX
    return [b.key[len(prefix):] for b in at.sidebar.button if (b.key or "").startswith(prefix)]


def page(at) -> str | None:
    import nav_view

    return at.session_state[nav_view.NAV_KEY] if nav_view.NAV_KEY in at.session_state else None
