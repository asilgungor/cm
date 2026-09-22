"""
Faz 15C-U: Yonetim kurulu ekranlari -- Streamlit AppTest (gercek web_app.py).

Kapsam (kart 15C-U):
    ekran       Kulüp bolumunde ve menude "Yönetim": sezon hedefi, lig sirasi, GUVEN CUBUGU + sozcuk,
                haftanin gerekce satirlari, uyari serididi, kariyer karneleri
    K12         guven ciplak sayi olarak YAZILMAZ (58/100 yok); ekranda cubuk + sozcuk vardir
    butce       sezon basi butce onerisi kabul edilince kasa seviyeye tamamlanir, reddedince hicbir sey degismez
    istifa      onay kutusu isaretlenmeden dugme pasif; istifadan sonra menajer kulupsuz, kulup ilan aciyor
    kovulma     cm.user_team None iken uygulama ayakta: kulupsuz menajer ekrani, is ilanlari, teklif kabulu
    kulupsuz    kadro / taktik / transfer / canli mac / kulup sayfalari menude yok, dogrudan acilsa bile duser
    gelen kutusu  yonetim satiri (hedef, guven sozcugu, uyari) + menu rozeti
    kural kapali  board.BOARD = False -> sayfa yok, Gelen Kutusu'nda satir yok, tek sorgu atilmaz

Testler test veritabanina GERCEKTEN yazar; her testten once dunya yeniden kurulur (tests.test_web_app._reseed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import all_pages, goto, menu, page, start  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _facts,
    _html,
    _query,
    _reseed,
    _set_user_team,
    _texts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed()


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _play(weeks: int = 1) -> None:
    """Haftayi dogrudan veritabaninda oynatir (yonetim kurulu adimi da calisir)."""
    from career_manager import CareerManager
    from database import session_scope

    for _ in range(weeks):
        with session_scope() as db:
            CareerManager(db).play_week()


def _cm(fn):
    from career_manager import CareerManager
    from database import session_scope

    with session_scope() as db:
        return fn(CareerManager(db))


def _state():
    """Menajerin bu sezonki yonetim satiri (board_states)."""
    from database import SessionLocal
    from models import BoardState

    with SessionLocal() as db:
        from sqlalchemy import select
        return db.scalars(select(BoardState).order_by(BoardState.id.desc())).first()


def _set_reputation(value: float) -> None:
    _cm(lambda cm: setattr(cm.state, "manager_reputation", float(value)))


def _sack() -> None:
    """Menajeri kovar (15C'nin sezon sonu yolunun ayni kapisi: karne SACKED + board_vacate)."""
    import board

    def go(cm):
        team = cm.user_team
        row = board.BoardRoom(cm).state_row(None, cm.season, team.id)
        if row is not None:
            row.status = board.STATE_SACKED
        cm.board_vacate(team, None, status=board.STATE_SACKED, reputation_delta=board.SACKED_REPUTATION)

    _cm(go)


def _open_a_vacancy(name: str) -> int:
    """Bir AI kulubu menajer arasin (iş ilanı) ve itibarini dusur: basvuru uygunlugu belirli olsun."""
    def go(cm):
        team = cm.find_team(name)
        team.board_vacant_since = cm.career_week
        team.reputation = 50
        return int(team.id)

    return _cm(go)


def _offers(pending_only: bool = True):
    from sqlalchemy import select

    import board
    from database import SessionLocal
    from models import BoardOffer

    with SessionLocal() as db:
        stmt = select(BoardOffer).order_by(BoardOffer.id.desc())
        if pending_only:
            stmt = stmt.where(BoardOffer.status == board.OFFER_PENDING)
        return list(db.scalars(stmt))


def _keys(at) -> set[str]:
    return {b.key for b in at.button if b.key}


# ---------------------------------------------------------------------------
# 1) Ekran: hedef, guven cubugu, gerekceler
# ---------------------------------------------------------------------------

def test_board_screen_shows_target_position_and_a_confidence_bar_with_a_word():
    """CM 01/02: yonetim kurulu ekraninda hedef, sira ve GUVEN CUBUGU + sozcuk; ciplak puan yok (K12)."""
    import board

    _set_user_team(TEAM)
    _play(2)
    at = _app(page="yonetim")
    assert page(at) == "yonetim"
    html = _html(at)
    assert 'class="ofm-conf' in html                       # guven cubugu cizildi
    facts = _facts(at)
    row = _state()
    assert row is not None
    assert facts.get("Sezon hedefi") == board.target_label(row.target, 4)     # 4 kuluplu test ligi: "İlk 2"
    assert facts.get("Lig sırası", "").endswith(". / 4")
    assert facts.get("Hedefe göre") in {"Hedefin üstünde", "Hedefte", "Hedefin bir kademe altında"} or \
        facts["Hedefe göre"].startswith("Hedefin")
    words = {w for _c, w in board.CONFIDENCE_LABELS}
    assert any(f"<b>{word}</b>" in html for word in words)  # cubugun yanindaki SOZCUK
    assert f"{row.confidence:.0f}/100" not in html and f"{row.confidence}" not in html
    for reason in (row.reasons or [])[:2]:                 # 15C'nin Turkce gerekce satirlari oldugu gibi
        assert reason in html


def test_board_screen_is_reachable_from_the_club_section_and_lists_the_career_record():
    import nav_view

    _set_user_team(TEAM)
    _play(1)
    at = _app()
    assert nav_view.BOARD in menu(at)
    _click(at, nav_view.menu_key(nav_view.SEC_CLUB))
    assert nav_view.button_key(nav_view.BOARD) in _keys(at)      # kulup sekme satirinda "Yönetim"
    goto(at, nav_view.BOARD)
    import board_view
    at.radio(key=board_view.SECTION_KEY).set_value(board_view.SEC_HISTORY).run()
    assert not at.exception
    frame = at.dataframe[0].value
    assert list(frame.columns) == ["Sezon", "Kulüp", "Hedef", "Sıra", "Yönetim", "Uyarı", "Durum"]
    assert TEAM in list(frame["Kulüp"])


# ---------------------------------------------------------------------------
# 2) Sezon basi butce onerisi
# ---------------------------------------------------------------------------

def test_budget_proposal_is_accepted_and_tops_the_transfer_budget_up():
    from models import Team

    _set_user_team(TEAM)
    _play(1)
    row = _state()
    assert row is not None and row.budget_status == "PENDING"
    at = _app(page="yonetim")
    assert "bd_budget_ok" in _keys(at) and "bd_budget_no" in _keys(at)
    _click(at, "bd_budget_ok")
    after = _state()
    assert after.budget_status == "ACCEPTED"
    budget = _query(lambda db: db.scalar(
        __import__("sqlalchemy").select(Team.transfer_budget).where(Team.name == TEAM)))
    assert budget >= int(row.budget_transfer)                    # kasa seviyeye tamamlandi (para ALINMADI)
    assert "bd_budget_ok" not in _keys(at)


def test_budget_proposal_can_be_left_alone_and_nothing_changes():
    from models import Team

    _set_user_team(TEAM)
    _play(1)
    before = _query(lambda db: db.scalar(
        __import__("sqlalchemy").select(Team.transfer_budget).where(Team.name == TEAM)))
    at = _app(page="yonetim")
    _click(at, "bd_budget_no")
    assert _state().budget_status == "PENDING"                   # oneri duruyor, hicbir sey yazilmadi
    after = _query(lambda db: db.scalar(
        __import__("sqlalchemy").select(Team.transfer_budget).where(Team.name == TEAM)))
    assert after == before and "bd_budget_ok" not in _keys(at)


# ---------------------------------------------------------------------------
# 3) Istifa (onayli)
# ---------------------------------------------------------------------------

def test_resign_needs_a_confirmation_and_leaves_the_manager_without_a_club():
    import board_view
    from models import Team

    _set_user_team(TEAM)
    _play(1)
    at = _app(page="yonetim")
    assert at.button(key="bd_resign").disabled                   # onay kutusu isaretli degil
    at.checkbox(key=board_view.RESIGN_KEY).check().run()
    assert not at.button(key="bd_resign").disabled
    _click(at, "bd_resign")
    assert _cm(lambda cm: cm.user_team) is None
    vacant = _query(lambda db: db.scalar(
        __import__("sqlalchemy").select(Team.board_vacant_since).where(Team.name == TEAM)))
    assert vacant is not None                                    # kulup menajer ariyor (is ilani)
    assert _cm(lambda cm: cm.board_unemployed()) is True
    assert "Kulüpsüz menajer" in _html(at)                       # ayni cizimde kulupsuz ekrani


# ---------------------------------------------------------------------------
# 4) Kovulma: uygulama ayakta kalir
# ---------------------------------------------------------------------------

def test_a_sacked_manager_sees_the_manager_screen_and_the_job_market():
    import nav_view

    _set_user_team(TEAM)
    _play(1)
    _sack()
    at = _app()
    assert not at.exception
    assert page(at) == nav_view.HOME                             # kulup secicisine DUSMEZ (13G yolu degil)
    pages = menu(at)
    assert nav_view.BOARD in pages
    for slug in (nav_view.SQUAD, nav_view.TACTICS, nav_view.TRANSFER, nav_view.MATCH, nav_view.CLUB,
                 nav_view.FIXTURES, nav_view.ACADEMY, nav_view.STAFF):
        assert slug not in pages                                 # kulup ekranlari menude yok
    goto(at, nav_view.BOARD)
    html = _html(at)
    assert "Kulüpsüz menajer" in html
    facts = _facts(at)
    assert facts.get("Kulüpsüz", "").endswith("hafta") and facts.get("Son kulüp") == TEAM
    assert "Görevine son verildi" == facts.get("Ayrılış")
    goto(at, nav_view.MANAGER)                                   # CM "Menajer" ekrani: tanınırlık + karne
    body = _html(at)
    assert "Menajer tanınırlığı" in body and "Kulüpsüz" in body


def test_a_manager_without_a_club_can_apply_to_a_vacancy():
    import board
    import board_view

    _set_user_team(TEAM)
    _play(1)
    _sack()
    _set_reputation(18.0)
    target = _open_a_vacancy("Kadıköy Canaries")
    at = _app(page="yonetim")
    at.radio(key=board_view.SECTION_KEY).set_value(board_view.SEC_JOBS).run()
    frame = at.dataframe[0].value
    assert list(frame.columns) == ["Kulüp", "Lig", "İtibar", "Kadro", "Kadro değeri", "İlan", "Durum"]
    assert "Kadıköy Canaries" in list(frame["Kulüp"]) and "Uygun" in list(frame["Durum"])
    at.selectbox(key=board_view.JOB_PICK_KEY).set_value(target).run()
    _click(at, "bd_apply")
    rows = [o for o in _offers(pending_only=False) if o.team_id == target]
    assert rows and rows[0].kind == board.OFFER_ADVERT           # basvuru kaydi olustu (kabul ya da ret)


def test_accepting_a_job_offer_puts_the_manager_back_in_charge():
    import board
    import board_view
    import nav_view

    _set_user_team(TEAM)
    _play(1)
    _sack()
    target = _open_a_vacancy("Kadıköy Canaries")
    _make_offer(target)
    at = _app(page="yonetim")
    at.radio(key=board_view.SECTION_KEY).set_value(board_view.SEC_OFFERS).run()
    offer_id = _offers()[0].id
    assert f"bd_offer_ok_{offer_id}" in _keys(at) and f"bd_offer_no_{offer_id}" in _keys(at)
    _click(at, f"bd_offer_ok_{offer_id}")
    assert _cm(lambda cm: cm.user_team.name) == "Kadıköy Canaries"
    assert _cm(lambda cm: cm.board_unemployed()) is False
    assert nav_view.SQUAD in menu(at) and nav_view.MATCH in menu(at)      # normal akisa dondu
    assert _offers()[0].status == board.OFFER_ACCEPTED if _offers() else True


def _make_offer(team_id: int) -> int:
    """Bekleyen bir is teklifi yazar (basvuru zari testte belirsiz olmasin)."""
    import board
    from database import session_scope
    from models import BoardOffer, Team

    with session_scope() as db:
        from career_manager import CareerManager
        cm = CareerManager(db)
        team = db.get(Team, int(team_id))
        offer = BoardOffer(manager_id=None, team_id=team.id, season=cm.season, week=cm.current_week,
                           career_week=cm.career_week, expires_career_week=cm.career_week + board.OFFER_WEEKS,
                           kind=board.OFFER_APPROACH, status=board.OFFER_PENDING, reputation=team.reputation)
        db.add(offer)
        db.flush()
        return int(offer.id)


def test_club_pages_refuse_politely_when_the_manager_has_no_club():
    """Kulupsuz menajer adresten kulup sayfasi acsa bile uygulama DUSMEZ (kadro, taktik, transfer, canli mac)."""
    import web_app

    _set_user_team(TEAM)
    _play(1)
    _sack()
    for slug in ("kadro", "taktik", "transfer", "canli-mac", "kulup", "fikstur", "akademi", "teknik-heyet"):
        at = _app(page=slug)
        assert not at.exception, slug
        assert web_app.NO_CLUB_TEXT in _texts(at.main.info) or page(at) != slug, slug


def test_every_visible_page_renders_without_a_club():
    _set_user_team(TEAM)
    _play(1)
    _sack()
    at = _app()
    for slug in all_pages(at):
        goto(at, slug)
        assert not at.exception, slug


# ---------------------------------------------------------------------------
# 5) Gelen Kutusu satiri ve menu rozeti
# ---------------------------------------------------------------------------

def test_the_inbox_shows_a_compact_board_line_and_a_badge_for_a_waiting_offer():
    import nav_view

    _set_user_team(TEAM)
    _play(1)
    at = _app(page="ana-sayfa")
    facts = _facts(at)
    assert facts.get("Sezon hedefi") and facts.get("Yönetimin güveni")
    assert facts.get("Yönetim uyarısı") == "—"
    assert "home_board_go" in _keys(at)
    before = at.sidebar.button(key="nav_menu_inbox").label
    other = _open_a_vacancy("Kadıköy Canaries")
    _make_offer(other)
    at = _app(page="ana-sayfa")
    assert _facts(at).get("İş teklifi") == "1"
    after = at.sidebar.button(key="nav_menu_inbox").label
    assert after != before and after.endswith(")")                # rozet arttI
    _click(at, "home_board_go")
    assert page(at) == nav_view.BOARD


# ---------------------------------------------------------------------------
# 6) Kural kapali: ekran yok
# ---------------------------------------------------------------------------

def test_the_screen_disappears_when_the_rule_is_off(monkeypatch):
    import board
    import nav_view

    _set_user_team(TEAM)
    _play(1)
    monkeypatch.setattr(board, "BOARD", False)
    at = _app(page="ana-sayfa")
    assert nav_view.BOARD not in menu(at) and "home_board_go" not in _keys(at)
    assert "Yönetimin güveni" not in _facts(at)
    at = _app()
    start(at, nav_view.BOARD)
    at.run()
    assert not at.exception and page(at) != nav_view.BOARD        # adresten de acilmaz


# ---------------------------------------------------------------------------
# 7) Paylasilan dunya kurali (sahip karari K-S4)
# ---------------------------------------------------------------------------

def test_the_world_admin_can_switch_the_board_rule_on():
    import world_admin_view
    from world_rules import FIELD_LABELS, GAMEPLAY_FIELDS, WorldRules

    assert ("board_confidence", "bool") in world_admin_view.RULE_WIDGETS
    assert FIELD_LABELS["board_confidence"] and "board_confidence" in GAMEPLAY_FIELDS
    assert WorldRules.shared_defaults().board_confidence is False      # paylasilan dunyada varsayilan KAPALI


def test_pages_for_only_adds_the_board_page_when_the_rule_is_on():
    import nav_view

    off = nav_view.pages_for(tournament=False, shared=False, internationals=False, role=None)
    on = nav_view.pages_for(tournament=False, shared=False, internationals=False, role=None, board=True)
    assert nav_view.BOARD not in off and on == [*off, nav_view.BOARD]
    assert nav_view.BOARD not in nav_view.pages_for(tournament=True, shared=False, internationals=False,
                                                    role=None, board=True)
    assert nav_view.pages_without_club(on) == [nav_view.HOME, nav_view.TABLE, nav_view.ARENA, nav_view.BOARD]
