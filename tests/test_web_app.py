"""
Menajer paneli uctan uca testleri (7. Asama) -- Streamlit AppTest (basliksiz).

Gercek web_app.py betigini calistirir; kenar cubugu menusu ve sayfalardaki widget'lari
anahtarlariyla (key) bulup tiklar, sonucu hem ekranda hem VERITABANINDA dogrular. Faz 13I: ust sekme yok, her
cizimde yalnizca secili sayfa cizilir -- testler sayfaya tests/nav_helpers.py ile gider (start / goto).

Bu testler test veritabanina GERCEKTEN yazar (callback'ler kendi transaction'larini
commit eder). Bu yuzden her testten once dunya yeniden kurulur ve modul sonunda
temiz dunya geri birakilir; diger test dosyalari etkilenmez.
"""

from __future__ import annotations

import json
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

from tests.nav_helpers import goto, menu, start  # noqa: E402

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


def _app(seed: str | None = None, login: bool = True, page: str | None = None):
    at = AppTest.from_file(APP, default_timeout=90)
    if login:
        _login(at)
    if seed is not None:
        at.session_state["career_seed"] = seed
    if page is not None:
        start(at, page)                                  # Faz 13I: oturum bu sayfada baslar
    at.run()
    assert not at.exception, at.exception
    return at


def _login(at) -> None:
    """Giris kapisini (10. Asama) test kullanicisiyla gecer: eski tek kisilik kariyer ('public')."""
    from accounts import AuthSession

    at.session_state["auth"] = AuthSession(user_id=0, username="test_menajer", career_schema="public")


def _career_tab_count() -> int:
    import web_app
    return len(web_app.CAREER_PAGES)


def _texts(elements) -> str:
    return "\n".join(str(e.value) for e in elements)


def _html(at) -> str:
    return _texts(at.markdown)


def _facts(at) -> dict[str, str]:
    """CM bilgi satirlari (nav_view.facts_html): baslik -> deger, sayfadaki butun satirlar."""
    import html
    import re

    out: dict[str, str] = {}
    for m in at.markdown:
        text = str(m.value)
        if "cm-facts" not in text:
            continue
        heads = [html.unescape(h) for h in re.findall(r"<th[^>]*>([^<]*)</th>", text)]
        cells = [html.unescape(c) for c in re.findall(r"<td[^>]*>([^<]*)</td>", text)]
        out.update(zip(heads, cells, strict=False))
    return out


def _click(at, key: str):
    at.button(key=key).click()
    at.run()
    assert not at.exception, at.exception
    return at


# ---------------------------------------------------------------------------
# Iskelet ve takim secimi
# ---------------------------------------------------------------------------

def test_club_selection_is_the_first_step_country_league_club():
    """Faz 13G: kulubu olmayan kariyerde ILK ekran ulke -> lig -> kulup; sekme / kenar cubugu araci yok."""
    import club_picker_view as cp
    import web_app

    at = _app()
    assert not at.title                                                   # 14S: dev sayfa basligi yok (CM bandi)
    assert len(at.tabs) == 0 and "Kulübünü seç" in _html(at)
    assert not [s for s in at.selectbox if s.key == "sb_team"] and not [b for b in at.button if b.key == "sb_set_team"]
    assert {"sb_logout", "sb_worlds", "sb_change_mode"} <= {b.key for b in at.button}
    assert not [b for b in at.button if (b.key or "").startswith("cp_pick_")]             # once ulke
    country = at.button_group(key="cp_country")
    assert country.options[0].endswith("Türkiye · 4") and len(country.options) == 6
    country.set_value("Türkiye")
    at.run()
    assert at.button_group(key="cp_league").value is not None                             # tek lig: kendiliginden
    picks = {b.key for b in at.button if (b.key or "").startswith("cp_pick_")}
    turkish = _query(lambda db: {f"cp_pick_{t.id}" for t in _team(db, "Istanbul Lions").league.teams})
    assert picks == turkish                                                              # yalnizca o ligin kulupleri
    team_id = _query(lambda db: _team(db, "Kadıköy Canaries").id)
    _click(at, f"cp_pick_{team_id}")
    assert _query(lambda db: __import__("career_manager").CareerManager(db).user_team.name) == "Kadıköy Canaries"
    # Faz 13I: ayni cizimde CM tarzi menu (sekme yok) ve Ana Sayfa
    assert not at.tabs and menu(at) == web_app.CAREER_PAGES
    assert web_app.CAREER_PAGES == ["ana-sayfa", "haberler", "kadro", "taktik", "canli-mac", "akademi",
                                    "teknik-heyet", "fikstur", "puan-durumu", "devler-arenasi", "transfer", "kulup"]
    assert at.session_state["nav_page"] == "ana-sayfa" and at.button(key="nav_menu_inbox").proto.type == "primary"
    assert any("Kadıköy Canaries" in s.value and "menajerisin" in s.value for s in at.success)
    assert cp.SCROLL_TOP_KEY not in at.session_state                                    # tek seferlik kaydirma
    assert "Menajer tanınırlığı" in _texts(at.sidebar.caption)
    assert at.button(key="nav_menu_club").label == "Kadıköy Canaries"                    # CM menusu: kulubun adi
    assert any("kariyer boyunca" in c.value for c in at.sidebar.caption)
    assert not [s for s in at.selectbox if s.key == "sb_team"]                           # KILIT: secici yok
    assert at.button(key="sb_change_mode").disabled                                      # kariyer modu kilitli
    assert at.button(key="nav_continue") and at.button(key="home_continue")              # devam: menude + ana sayfa
    goto(at, "Canlı Maç")
    assert at.radio(key="live_mode").value == "Maçımı yönet"
    xi = _query(lambda db: [p for p in _team(db, "Kadıköy Canaries").players if p.lineup_status.value == "XI"])
    assert len(xi) == 11                                                                 # asistan kadroyu kurdu


def test_club_search_skips_the_country_step_and_lock_is_enforced_server_side(monkeypatch):
    import accounts
    import web_app
    import web_common

    at = _app()
    at.text_input(key="cp_query").set_value("canaries")
    at.run()
    team_id = _query(lambda db: _team(db, "Kadıköy Canaries").id)
    assert [b.key for b in at.button if (b.key or "").startswith("cp_pick_")] == [f"cp_pick_{team_id}"]
    _click(at, f"cp_pick_{team_id}")

    # Kilit: istemci eski bir secici degeri ya da baska bir kulup id'si gonderse bile kulup degismez
    state: dict = {"auth": accounts.AuthSession(user_id=0, username="test_menajer", career_schema="public"),
                   "sb_team": "Istanbul Lions"}
    monkeypatch.setattr(web_common.st, "session_state", state)
    assert web_app.cb_set_team() is None
    other = _query(lambda db: _team(db, "Istanbul Lions").id)
    assert web_app.club_picker_view.cb_choose_club(other) is None
    errors = [text for kind, text in state["flash"]["sidebar"] + state["flash"]["clubs"] if kind == "error"]
    assert len(errors) == 2 and all("değiştirilemez" in text for text in errors)
    assert _query(lambda db: __import__("career_manager").CareerManager(db).user_team.name) == "Kadıköy Canaries"


# ---------------------------------------------------------------------------
# Kadro & Taktik
# ---------------------------------------------------------------------------

def test_assistant_lineup_board_and_condition_bars():
    _set_user_team("Istanbul Lions")
    at = _app(page="kadro")
    _click(at, "tac_auto")
    xi = _query(lambda db: [p for p in _team(db, "Istanbul Lions").players if p.lineup_status.value == "XI"])
    assert len(xi) == 11
    assert any("Asistan 11 kişilik" in s.value for s in at.success)
    board = at.get("bidi_component")                         # Faz 13G: surukle-birak taktik tahtasi
    assert len(board) == 1 and board[0].proto.component_name == "ofm_tactics_board"
    table = next(d for d in at.dataframe if d.key == "sq_table").value       # kadro tablosu (satira tik: profil)
    squad = _query(lambda db: len(_team(db, "Istanbul Lions").players))
    assert {"Oyuncu", "Kondisyon", "Durum"} <= set(table.columns) and len(table) == squad
    assert (table["Durum"].str.startswith("İlk 11")).sum() == 11


def test_tired_starter_triggers_warning_on_save():
    from models import Position

    _set_user_team("Istanbul Lions")
    at = _app(page="kadro")
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
    at.button_group(key="sq_view").set_value("Kondisyon")             # 14G: kadro gorunumu (Not sutunu burada)
    at.run()
    table = next(d for d in at.dataframe if d.key == "sq_table").value
    assert table.loc[table["Oyuncu"].str.endswith(tired_name), "Not"].tolist() == ["Kondisyon düşük"]


def test_saving_injured_player_in_xi_is_rejected():
    _set_user_team("Bosphorus Eagles")
    at = _app(page="kadro")
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
    at = _app(page="kadro")
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
    at = _app(page="kulup")                              # Kulüp & Finans: ilk bolum butce
    at.slider(key="fin_target").set_value(w0 + 10_000)
    at.run()
    metrics = _facts(at)                                  # 14FG: st.metric yerine CM bilgi satiri
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
    at = _app(page="finans")
    assert any("aşılıyor" in e.value for e in at.error)
    assert "cm-usage over" in _html(at)


# ---------------------------------------------------------------------------
# Transfer Merkezi (Faz 13I): eski "Bonservis teklifi" dugmesi transfer masasina yonlendirir
# (tam akis: tests/test_web_transfer_centre.py)
# ---------------------------------------------------------------------------

def _best_of(db, club):
    return max(_team(db, club).players, key=lambda p: p.overall_rating)


def _know(team_name: str, player_id: int, knowledge: int = 100) -> None:
    """Gozlem bilgisi (13H): baska ligdeki oyuncuya teklif icin kulubun bilgisi gerekir."""
    from database import session_scope
    from models import ScoutAssignment

    with session_scope() as db:
        db.add(ScoutAssignment(team_id=_team(db, team_name).id, player_id=player_id, knowledge=knowledge,
                               status="DONE", assigned_career_week=1, updated_career_week=1))


def _search(at, name: str, target_id: int) -> None:
    at.select_slider(key="mkt_level").set_value("Tümü")
    at.text_input(key="mkt_name").set_value(name)
    at.run()
    at.selectbox(key="mkt_target").set_value(target_id)
    at.run()
    assert not at.exception, at.exception


def _open_deal(db, buyer: str, player_id: int):
    from sqlalchemy import select

    from models import TransferDeal
    return db.scalar(select(TransferDeal).where(TransferDeal.player_id == player_id,
                                                TransferDeal.buyer_team_id == _team(db, buyer).id))


def test_market_offer_button_opens_the_transfer_desk_file():
    """mkt_offer artik tek atislik teklif yapmaz: kulube sorar (enquire) ve Dosyalarim'da dosyayi acar."""
    _set_user_team("Istanbul Lions")
    target_id, name = _query(lambda db: (lambda p: (p.id, p.name))(_best_of(db, "Karadeniz Storm")))
    at = _app(seed="1", page="transfer")
    _search(at, name, target_id)
    assert "%35" in next(d for d in at.dataframe if d.key == "mkt_table").value["Bilgi"].tolist()   # ayni lig
    _click(at, "mkt_offer")
    deal = _query(lambda db: (lambda d: (d.id, d.status))(_open_deal(db, "Istanbul Lions", target_id)))
    assert deal[1] == "ENQUIRY" and at.session_state["tc_deal"] == deal[0]
    assert at.radio(key="tc_section").value == "Dosyalarım"
    assert at.button(key="tc_bid") and at.number_input(key="tc_fee").value > 0
    assert "neg" not in at.session_state                                      # eski oturum masasi yok
    assert _query(lambda db: db.get(__import__("models").Player, target_id).team.name) == "Karadeniz Storm"


def test_star_refuses_small_club_even_after_fee_accepted():
    _set_user_team("Karadeniz Storm", transfer_budget=900_000_000)
    star_id, star_name = _query(lambda db: (lambda p: (p.id, p.name))(_best_of(db, "Manchester Blue")))
    _know("Karadeniz Storm", star_id)
    at = _app(seed="1", page="transfer")
    _search(at, star_name, star_id)
    _click(at, "mkt_offer")
    at.number_input(key="tc_fee").set_value(700_000_000)
    at.slider(key="tc_pct").set_value(100)
    at.run()
    _click(at, "tc_bid")
    assert _query(lambda db: _open_deal(db, "Karadeniz Storm", star_id).status) == "TERMS"
    _click(at, "tc_terms_open")
    assert any("masasına oturmadı" in e.value for e in at.error)
    assert _query(lambda db: _open_deal(db, "Karadeniz Storm", star_id).status) == "COLLAPSED"
    assert _query(lambda db: db.get(__import__("models").Player, star_id).team.name) == "Manchester Blue"


def test_offer_above_budget_is_blocked():
    _set_user_team("Karadeniz Storm", transfer_budget=1_000)
    target_id, name = _query(lambda db: (lambda p: (p.id, p.name))(_best_of(db, "Vesuvio Azzurri")))
    at = _app(seed="1", page="transfer")
    _search(at, name, target_id)
    assert at.button(key="mkt_offer").disabled                                # bilgi %0: once gozlemci
    _know("Karadeniz Storm", target_id, 60)
    at.run()
    _click(at, "mkt_offer")
    at.number_input(key="tc_fee").set_value(50_000_000)
    at.run()
    assert any("bütçeni aşıyor" in w.value for w in at.warning)
    _click(at, "tc_bid")
    assert any("Transfer bütçen yetersiz" in e.value for e in at.error)
    assert _query(lambda db: _open_deal(db, "Karadeniz Storm", target_id).status) == "ENQUIRY"


# ---------------------------------------------------------------------------
# Lig ve canli mac
# ---------------------------------------------------------------------------

def test_play_week_then_watch_own_match_on_2d_pitch():
    _set_user_team("Istanbul Lions")
    at = _app(seed="7", page="fikstur")
    _click(at, "lg_play")
    assert _query(lambda db: db.get(__import__("models").GameState, 1).current_week) == 2
    assert any("1. hafta oynandı" in s.value for s in at.success)
    assert at.session_state["last_user_result"] is not None

    goto(at, "canli-mac")
    at.radio(key="live_mode").set_value("Son maçımı izle")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_start")
    html = _html(at)
    # 14T: saha canli 2D bilesen; Anında -> son karenin (mac sonu) betigi, son pozunda
    pitch_data = _pitch_data(at)
    assert pitch_data is not None and pitch_data["s"]["k"] == "FULL_TIME" and pitch_data["m"] == "jump"
    assert "MAÇ SONU" in html and "Kondisyon" in html


def _pitch_data(at):
    """14T: canli 2D saha bileseninin (md_pitch) veri paketi; yoksa None. Zamanlayici bileseni betik tasimaz."""
    for element in at.get("bidi_component"):
        raw = element.proto.mixed.json if element.proto.WhichOneof("data") == "mixed" else element.proto.json
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("s"), dict):
            return data
    return None


def test_friendly_live_match_with_and_without_pitch():
    _set_user_team("Istanbul Lions")                # Faz 13G: panel (Canli Mac sayfasi) kulup secilince acilir
    at = _app(page="canli-mac")
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
    # 14T: saha artik canli 2D bilesen (st.components.v2, md_pitch); betik gosterilen kareyi tasir
    assert _pitch_data(at) is not None and "Merseyside Reds" in html and "MAÇ SONU" in html
    assert any("kaydedilmez" in c.value for c in at.caption)

    at.toggle(key="live_pitch").set_value(False)
    at.run()
    _click(at, "live_start")
    assert _pitch_data(at) is None
    assert 'viewBox="-4 -10 113 86"' not in _html(at) and "MAÇ SONU" in _html(at)


def test_friendly_same_team_rejected_and_no_db_write():
    from sqlalchemy import func, select

    from models import Fixture, FixtureStatus

    _set_user_team("Istanbul Lions")                # Faz 13G: panel kulup secilince acilir
    at = _app(page="canli-mac")
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
    at = _app(page="teknik-heyet")
    at.selectbox(key="st_release").set_value(released)
    at.run()
    _click(at, "st_release_btn")
    assert _query(lambda db: db.get(__import__("models").Staff, released).team_id) is None
    assert any("gönderildi" in s.value for s in at.success)

    at.selectbox(key="st_hire").set_value(released)
    at.run()
    _click(at, "st_hire_btn")
    assert _query(lambda db: db.get(__import__("models").Staff, released).team.name) == "Torino Bianconeri"
