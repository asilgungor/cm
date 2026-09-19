"""
Faz 13I / 14S: web testleri icin menu yardimcilari. Ust sekmeler (st.tabs) yok; her cizimde yalnizca secili sayfa cizilir.
14S'ten beri kenar cubugu CM kisa menusudur (nav_menu_{bolum}); bolumun sayfalari ekranin sekme satirindadir
(nav_to_{slug}), Gelen Kutusu'nun alt sayfalari alt eylem dugmelerindedir (nav_act_{slug}).

    start(at, "kadro")      ilk at.run()'dan ONCE: oturum o sayfada baslar (nav_page)
    goto(at, "Kadro")       sayfaya gider: sekme dugmesi gorunuyorsa ona, yoksa once menudeki bolume, sonra sekmeye /
                            alt eyleme tiklar (sayfa yeniden cizilir)
    menu(at)                bu cizimde gorunen OYUN sayfalari (nav_view.pages_for sozlesmesi; kabuk ekranlari haric)
    all_pages(at)           bu cizimde gorunen tum sayfalar (oyun + CM kabuk ekranlari: Menajer, Ulkeler, Bul, ...)
    sections(at)            kenar cubugu menusundeki bolumler (nav_menu_{bolum}, sirasiyla)
    page(at)                secili sayfa (slug)
Sayfa adi slug ("kadro"), etiket ("Kadro", 13I'nin "📋 Kadro"su) ya da takma ad ("finans", "lig") olabilir.
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


def _keys(at) -> set[str]:
    return {b.key for b in at.button if b.key}


def _click(at, key: str) -> None:
    at.button(key=key).click()
    at.run()
    assert not at.exception, at.exception


def goto(at, page: str):
    import nav_view

    slug = _slug(page)
    if nav_view.button_key(slug) not in _keys(at):
        section = nav_view.primary_section(slug).key
        _click(at, nav_view.menu_key(section))
    keys = _keys(at)
    if at.session_state[nav_view.NAV_KEY] != slug:
        if nav_view.button_key(slug) in keys:
            _click(at, nav_view.button_key(slug))
        else:
            _click(at, f"nav_act_{slug}")
    assert at.session_state[nav_view.NAV_KEY] == slug
    return at


def all_pages(at) -> list[str]:
    import nav_view

    return list(at.session_state[nav_view.PAGES_KEY]) if nav_view.PAGES_KEY in at.session_state else []


def menu(at) -> list[str]:
    import nav_view

    return [slug for slug in all_pages(at) if slug not in nav_view.SHELL_PAGES]


def sections(at) -> list[str]:
    import nav_view

    prefix = nav_view.MENU_PREFIX
    return [b.key[len(prefix):] for b in at.sidebar.button if (b.key or "").startswith(prefix)]


def page(at) -> str | None:
    import nav_view

    return at.session_state[nav_view.NAV_KEY] if nav_view.NAV_KEY in at.session_state else None
