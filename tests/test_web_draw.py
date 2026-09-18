"""
Kura gecesi uctan uca testleri (11. Asama) -- Streamlit AppTest.

Devler Arenasi sekmesinde kura toplarina tek tek tiklanir: her tiklama TEK top acar; iki tiklama bir
eslesmeyi (ev sahibi + deplasman) tamamlar ve parlayan kartta gosterir; "Kurayı otomatik çek" kalanini tamamlar. Kura
bitince fikstur veritabanina dogrulanmis ve kilitli yazilmis olmali. Her menajerin kurasi kendi
kariyer semasinda cekilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _html,
    _reseed,
    _set_user_team,
)
from tests.test_web_auth import _auth, _clean_accounts, _register  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")


@pytest.fixture(autouse=True)
def fresh_world():
    _clean_accounts()
    _reseed()
    yield
    _clean_accounts()


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _clean_accounts()
    _reseed()


def _cup(schema: str | None = None):
    """(durum, cekilen top, ilk tur fikstur sayisi, butunluk sorunlari) -- verilen kariyer semasinda."""
    import database
    from career_manager import CareerManager

    with database.career_context(schema), database.session_scope() as db:
        tm = CareerManager(db).tournaments
        t = tm.current()
        session = tm.draw_session(t)
        steps = len(session.steps) if session else 0
        fixtures = len(tm.fixtures(t, stage=tm.stages(t)[0]))
        problems = tm.draw_fixture_problems(t) if t.status.value != "DRAW" else []
        return t.status.value, steps, fixtures, problems


def test_each_click_opens_one_ball_two_clicks_pair_up_and_the_final_ball_locks_the_fixtures():
    _set_user_team("Madrid Blancos")
    at = _app(seed="5", page="devler-arenasi")
    assert at.button(key="arena_ball_0") and "Kura gecesi" in _html(at)

    for click in range(1, 17):
        balls = [b.key for b in at.button if b.key.startswith("arena_ball_")]
        assert len(balls) == 9 - (click + 1) // 2                   # acilacak torbada kalan kapali top
        _click(at, "arena_ball_0")
        status, steps, fixtures, _ = _cup()
        assert steps == click                                       # her tiklama tek top
        html = _html(at)
        if click < 16:
            assert status == "DRAW" and fixtures == 0               # kura bitmeden fikstur yazilmaz
            assert "cm-b-reveal" in html
            if click % 2:                                           # ev sahibi acildi, rakibi bekleniyor
                assert "rakibi bekleniyor" in html
            else:                                                   # ikinci top eslesmeyi tamamladi
                assert "rakibi bekleniyor" not in html
                assert "Ev sahibi" in html and "Deplasman" in html and "🆚" in html
            assert any(f"{click // 2}/8 eşleşme" in p.proto.text for p in at.get("progress"))

    status, steps, fixtures, problems = _cup()
    assert (status, steps, fixtures, problems) == ("RUNNING", 16, 16, [])
    assert any("kilitlendi" in s.value for s in at.success)
    assert not [b for b in at.button if b.key.startswith("arena_ball_") or b.key == "arena_draw_all"]
    assert any("kilitli ilk tur fikstürü (16 maç)" in e.label for e in at.expander)
    locked = next(df.value for df in at.dataframe if "Ev sahibi" in df.value.columns)
    assert len(locked) == 16 and set(locked["Durum"]) == {"🔒 kilitli"}

    at.run()                                                        # yeniden cizim: kilitli fikstur degismez
    assert not at.exception and _cup() == ("RUNNING", 16, 16, [])


def test_auto_draw_skips_the_rest_and_keeps_fixture_integrity():
    _set_user_team("Istanbul Lions")
    at = _app(seed="8", page="devler-arenasi")
    _click(at, "arena_ball_0")
    _click(at, "arena_ball_0")
    assert _cup()[1] == 2                                               # iki tiklama: bir eslesme
    _click(at, "arena_draw_all")
    assert any("Kalan 14 top otomatik çekildi" in s.value and "kilitlendi" in s.value for s in at.success)
    assert _cup() == ("RUNNING", 16, 16, [])


def test_every_manager_draws_in_their_own_world():
    first = _register(_app(login=False), "KuraMenajeriA")               # eski kariyeri (public) devralir
    _set_user_team("Istanbul Lions")                                    # Faz 13G: panel kulup secilince acilir
    first.run()
    goto(first, "devler-arenasi")                                       # Faz 13I: menu sayfasi
    _click(first, "arena_ball_0")
    assert _cup()[1] == 1

    second = _register(_app(login=False), "KuraMenajeriB")              # kendi dunyasi kurulur
    schema = _auth(second).career_schema
    assert schema.startswith("career_")
    _click(second, "mode_career")
    import database
    from career_manager import CareerManager

    with database.career_context(schema), database.session_scope() as db:   # Faz 13G: panel kulup secilince
        cm = CareerManager(db)
        cm.set_user_team(cm.find_team("Istanbul Lions"))
    second.run()
    goto(second, "devler-arenasi")
    assert _cup(schema)[1] == 0                                         # A'nin kurasi B'ye sizmaz
    _click(second, "arena_ball_0")
    _click(second, "arena_draw_all")
    assert _cup(schema) == ("RUNNING", 16, 16, [])
    assert _cup()[:3] == ("DRAW", 1, 0)                                 # A'nin kurasi yarida, dokunulmadi
