"""
Menajer paneli uctan uca testleri (7. Asama) -- Streamlit AppTest (basliksiz).

Gercek web_app.py betigini calistirir; kenar cubugu ve sekmelerdeki widget'lari
anahtarlariyla (key) bulup tiklar, sonucu hem ekranda hem VERITABANINDA dogrular.

Bu testler test veritabanina GERCEKTEN yazar (callback'ler kendi transaction'larini
commit eder). Bu yuzden her testten once dunya yeniden kurulur ve modul sonunda
temiz dunya geri birakilir; diger test dosyalari etkilenmez.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "web_app.py")


def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _reseed(mode: str | None = "CAREER_MODE") -> None:
    """Temiz dunya. mode verilirse ilk giris mod ekrani gecilmis sayilir (8. Asama)."""
    import database
    import seed
    from career_manager import CareerManager
    from models import GameMode

    database.reset_db()
    seed.seed(rng_seed=2026, source="synthetic")
    if mode is not None:
        with database.session_scope() as db:
            CareerManager(db).set_game_mode(GameMode(mode))


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed()


def _set_user_team(name: str, **team_fields):
    from career_manager import CareerManager
    from database import session_scope

    with session_scope() as db:
        cm = CareerManager(db)
        team = cm.find_team(name)
        cm.set_user_team(team)
        for field, value in team_fields.items():
            setattr(team, field, value)
        return team.id


def _query(fn):
    from database import SessionLocal

    with SessionLocal() as db:
        return fn(db)


def _team(db, name):
    from sqlalchemy import select

    from models import Team
    return db.scalar(select(Team).where(Team.name == name))


def _app(seed: str | None = None, login: bool = True):
    at = AppTest.from_file(APP, default_timeout=90)
    if login:
        _login(at)
    if seed is not None:
        at.session_state["career_seed"] = seed
    at.run()
    assert not at.exception, at.exception
    return at


def _login(at) -> None:
    """Giris kapisini (10. Asama) test kullanicisiyla gecer: eski tek kisilik kariyer ('public')."""
    from accounts import AuthSession

    at.session_state["auth"] = AuthSession(user_id=0, username="test_menajer", career_schema="public")


def _career_tab_count() -> int:
    import web_app
    return len(web_app.CAREER_TABS)


def _texts(elements) -> str:
    return "\n".join(str(e.value) for e in elements)


def _html(at) -> str:
    return _texts(at.markdown)


def _click(at, key: str):
    at.button(key=key).click()
    at.run()
    assert not at.exception, at.exception
    return at


# ---------------------------------------------------------------------------
# Iskelet ve takim secimi
# ---------------------------------------------------------------------------

def test_dashboard_has_seven_career_tabs_and_prompts_for_team():
    at = _app()
    assert at.title[0].value.endswith("OFM · ONLINE FOOTBALL MANAGER")
    assert len(at.tabs) == _career_tab_count()
    import web_app
    assert [t.label for t in at.tabs] == web_app.CAREER_TABS
    assert web_app.CAREER_TABS[1:] == ["📋 Kadro & Taktik", "🎯 Taktik Merkezi", "🎓 Altyapı Akademisi (U-21)",
                                       "💰 Finans", "🏛️ Kulüp Yönetimi & Tesisler", "🔄 Transfer Pazarı", "🏆 Lig",
                                       "📰 Haberler & Tarih", "⭐ Devler Arenası", "👥 Teknik Heyet"]
    # Devler Arenasi disindaki yonetim sekmeleri + Canli Mac'in varsayilan "Maçımı yönet" modu takim ister
    assert sum("takımını seç" in i.value for i in at.info) == _career_tab_count() - 1
    assert at.radio(key="live_mode").value == "Maçımı yönet"


def test_select_team_from_sidebar_persists():
    at = _app()
    at.selectbox(key="sb_team").set_value("Kadıköy Canaries")
    _click(at, "sb_set_team")
    assert _query(lambda db: __import__("career_manager").CareerManager(db).user_team.name) == "Kadıköy Canaries"
    assert not any("takımını seç" in i.value for i in at.info)
    assert "Menajer tanınırlığı" in _texts(at.sidebar.caption)


# ---------------------------------------------------------------------------
# Kadro & Taktik
# ---------------------------------------------------------------------------

def test_assistant_lineup_board_and_condition_bars():
    _set_user_team("Istanbul Lions")
    at = _app()
    _click(at, "tac_auto")
    xi = _query(lambda db: [p for p in _team(db, "Istanbul Lions").players if p.lineup_status.value == "XI"])
    assert len(xi) == 11
    assert any("Asistan 11 kişilik" in s.value for s in at.success)
    html = _html(at)
    assert "<svg" in html                                    # taktik tahtasi
    assert "cm-squad" in html and "cm-cond" in html          # kadro tablosu + kondisyon cubuklari


def test_tired_starter_triggers_warning_on_save():
    from models import Position

    _set_user_team("Istanbul Lions")
    at = _app()
    _click(at, "tac_auto")

    def tire_a_starter(db):
        team = _team(db, "Istanbul Lions")
        starter = next(p for p in team.players if p.lineup_status.value == "XI" and p.position is not Position.GK)
        starter.condition = 52
        db.commit()
        return starter.name

    tired_name = _query(tire_a_starter)
    at.run()
    assert any("kondisyonu düşük" in w.value and tired_name in w.value for w in at.warning)
    assert "cm-cond low" in _html(at)


def test_saving_injured_player_in_xi_is_rejected():
    _set_user_team("Bosphorus Eagles")
    at = _app()
    _click(at, "tac_auto")

    def injure_starter(db):
        team = _team(db, "Bosphorus Eagles")
        starter = next(p for p in team.players if p.lineup_status.value == "XI")
        starter.injured_until_week = 5
        db.commit()
        rows = [{"id": p.id, "Mv": p.position.value,
                 "Durum": {"XI": "İlk 11", "BENCH": "Kulübe", "OUT": "Kadro dışı"}[p.lineup_status.value],
                 "Slot": p.lineup_role.value if p.lineup_role else None} for p in team.players]
        return starter.name, rows, {p.id: p.lineup_status.value for p in team.players}

    name, rows, before = _query(injure_starter)
    at.run()
    at.session_state["tac_rows"] = rows
    _click(at, "tac_save")
    assert any("kaydedilmedi" in e.value and name in e.value and "sakat" in e.value for e in at.error)
    after = _query(lambda db: {p.id: p.lineup_status.value for p in _team(db, "Bosphorus Eagles").players})
    assert after == before


def test_formation_change_applies_immediately():
    _set_user_team("Milano Nerazzurri")
    at = _app()
    at.selectbox(key="tac_formation").set_value("3-5-2")
    at.run()
    assert not at.exception
    assert _query(lambda db: _team(db, "Milano Nerazzurri").formation) == "3-5-2"


# ---------------------------------------------------------------------------
# Finans
# ---------------------------------------------------------------------------

def test_budget_slider_preview_and_apply_uses_52_weeks():
    _set_user_team("Istanbul Lions")
    t0, w0 = _query(lambda db: (_team(db, "Istanbul Lions").transfer_budget, _team(db, "Istanbul Lions").wage_budget))
    at = _app()
    at.slider(key="fin_target").set_value(w0 + 10_000)
    at.run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Transfer bütçesine etkisi (EUR)"] == "-520K"
    _click(at, "fin_apply")
    t1, w1 = _query(lambda db: (_team(db, "Istanbul Lions").transfer_budget, _team(db, "Istanbul Lions").wage_budget))
    assert (t1, w1) == (t0 - 520_000, w0 + 10_000)
    assert any("aktarıldı" in s.value for s in at.success)


def test_overspending_shows_red_warning():
    def bill(db):
        return _team(db, "Karadeniz Storm").wage_bill

    wage_bill = _query(bill)
    _set_user_team("Karadeniz Storm", wage_budget=wage_bill - 25_000)
    at = _app()
    assert any("aşılıyor" in e.value for e in at.error)
    assert "cm-usage over" in _html(at)


# ---------------------------------------------------------------------------
# Transfer pazari ve sozlesme masasi
# ---------------------------------------------------------------------------

def _best_of(db, club):
    return max(_team(db, club).players, key=lambda p: p.overall_rating)


def test_offer_negotiate_and_sign_player():
    _set_user_team("Manchester Blue")
    target_id, target_name = _query(lambda db: (lambda p: (p.id, p.name))(_best_of(db, "Karadeniz Storm")))
    at = _app(seed="1")
    at.select_slider(key="mkt_stars").set_value("Tümü")
    at.text_input(key="mkt_name").set_value(target_name)
    at.run()
    at.selectbox(key="mkt_target").set_value(target_id)
    at.run()
    at.number_input(key="mkt_fee").set_value(100_000_000)
    _click(at, "mkt_offer")

    neg = at.session_state["neg"]
    assert neg["negotiation"].open, neg["log"]
    assert any("Sözleşme masası açıldı" in s.value for s in at.success)
    assert "cm-log" in _html(at)
    _click(at, "neg_accept")

    assert _query(lambda db: db.get(__import__("models").Player, target_id).team.name) == "Manchester Blue"
    assert any("TRANSFER TAMAM" in s.value for s in at.success)
    assert "neg" not in at.session_state


def test_star_refuses_small_club_even_after_fee_accepted():
    _set_user_team("Karadeniz Storm", transfer_budget=900_000_000)
    star_id, star_name = _query(lambda db: (lambda p: (p.id, p.name))(_best_of(db, "Manchester Blue")))
    at = _app(seed="1")
    at.select_slider(key="mkt_stars").set_value("Tümü")
    at.text_input(key="mkt_name").set_value(star_name)
    at.run()
    at.selectbox(key="mkt_target").set_value(star_id)
    at.run()
    at.number_input(key="mkt_fee").set_value(400_000_000)
    _click(at, "mkt_offer")
    assert any("masasına oturmadı" in e.value for e in at.error)
    assert not at.session_state["neg"]["negotiation"].open
    assert _query(lambda db: db.get(__import__("models").Player, star_id).team.name) == "Manchester Blue"
    _click(at, "neg_leave")
    assert "neg" not in at.session_state


def test_offer_above_budget_is_blocked():
    _set_user_team("Karadeniz Storm", transfer_budget=1_000)
    target_id = _query(lambda db: _best_of(db, "Vesuvio Azzurri").id)
    at = _app(seed="1")
    at.select_slider(key="mkt_stars").set_value("Tümü")
    at.run()
    at.selectbox(key="mkt_target").set_value(target_id)
    at.run()
    at.number_input(key="mkt_fee").set_value(50_000_000)
    _click(at, "mkt_offer")
    assert any("Transfer bütçen yetersiz" in e.value for e in at.error)


# ---------------------------------------------------------------------------
# Lig ve canli mac
# ---------------------------------------------------------------------------

def test_play_week_then_watch_own_match_on_2d_pitch():
    _set_user_team("Istanbul Lions")
    at = _app(seed="7")
    _click(at, "lg_play")
    assert _query(lambda db: db.get(__import__("models").GameState, 1).current_week) == 2
    assert any("1. hafta oynandı" in s.value for s in at.success)
    assert at.session_state["last_user_result"] is not None

    at.radio(key="live_mode").set_value("Son maçımı izle")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_start")
    html = _html(at)
    # Yer tutucu her karede ustune yazilir: son durumda son sahne (mac sonu) gorunur.
    # Olay basina sahne uretimi tests/test_pitch.py'de dogrulanir.
    assert 'viewBox="-4 -10 113 86"' in html and 'data-frame="' in html
    assert "MAÇ SONU" in html and "Kondisyon" in html


def test_friendly_live_match_with_and_without_pitch():
    at = _app()
    at.radio(key="live_mode").set_value("Hazırlık maçı")
    at.run()
    at.select_slider(key="live_speed").set_value("Anında")
    at.selectbox(key="live_home").set_value("Merseyside Reds")
    at.selectbox(key="live_away").set_value("London Gunners")
    at.text_input(key="live_seed").set_value("3")
    at.radio(key="live_side").set_value("Sadece izle")        # mudahalesiz izleme (canli yonetim: test_web_live)
    at.run()
    _click(at, "live_start")
    html = _html(at)
    assert 'viewBox="-4 -10 113 86"' in html and "Merseyside Reds" in html and "MAÇ SONU" in html
    assert any("kaydedilmez" in c.value for c in at.caption)

    at.toggle(key="live_pitch").set_value(False)
    at.run()
    _click(at, "live_start")
    assert 'viewBox="-4 -10 113 86"' not in _html(at) and "MAÇ SONU" in _html(at)


def test_friendly_same_team_rejected_and_no_db_write():
    from sqlalchemy import func, select

    from models import Fixture, FixtureStatus

    at = _app()
    at.radio(key="live_mode").set_value("Hazırlık maçı")
    at.run()
    at.select_slider(key="live_speed").set_value("Anında")
    at.selectbox(key="live_home").set_value("Milano Rossoneri")
    at.selectbox(key="live_away").set_value("Milano Rossoneri")
    at.run()
    _click(at, "live_start")
    assert any("kendisiyle" in e.value for e in at.error)
    played = _query(lambda db: db.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.status == FixtureStatus.PLAYED)))
    assert played == 0


# ---------------------------------------------------------------------------
# Teknik heyet
# ---------------------------------------------------------------------------

def test_release_and_hire_staff():
    from models import StaffRole

    _set_user_team("Torino Bianconeri")

    def physio_id(db):
        return _team(db, "Torino Bianconeri").staff_by_role(StaffRole.PHYSIO)[0].id

    released = _query(physio_id)
    at = _app()
    at.selectbox(key="st_release").set_value(released)
    at.run()
    _click(at, "st_release_btn")
    assert _query(lambda db: db.get(__import__("models").Staff, released).team_id) is None
    assert any("gönderildi" in s.value for s in at.success)

    at.selectbox(key="st_hire").set_value(released)
    at.run()
    _click(at, "st_hire_btn")
    assert _query(lambda db: db.get(__import__("models").Staff, released).team.name) == "Torino Bianconeri"
