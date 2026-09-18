"""
Faz 12 / 14. Asama B4: menajerler arasi transfer pazari, kiraliklar, mesajlar ve adil oyun yonetimi -- Streamlit
AppTest (basliksiz) + callback birim testleri.

Gercek web_app.py betigi calisir. Sentetik 'public' dunyasi tests/world_helpers.make_shared_public ile paylasilan
dunyaya cevrilir (kurallar: WorldRules.shared_defaults -> menajer pazari ve kiralik acik): sahip (Manchester Blue,
birincil koltuk), uye (Merseyside Reds) ve kulupsuz bir yonetici. Hesaplar / koltuklar eski sayilir (age_managers):
adil oyun karari yalnizca anlasmanin degerine baglidir. IKI (uc) AppTest oturumu ayni dunyada islem yapar ve sonuc
veritabaninda dogrulanir.

Kendi veritabaninda calistirin (dunyayi yeniden seed eder):
    TEST_DB_NAME=fm_db_test_b4 python -m pytest -q -p no:cacheprovider tests/test_web_market.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, menu  # noqa: E402
from tests.nav_helpers import page as current_page  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _click,
    _db_available,
    _query,
    _reseed,
    _set_user_team,
    _texts,
)
from tests.world_helpers import (  # noqa: E402
    age_managers,
    app_as,
    cleanup_shared,
    cleanup_users,
    make_shared_public,
    set_world_role,
    world_auth,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

PREFIX = "b4"
OWNER, MEMBER, ADMIN = "b4sahip", "b4uye", "b4yonetici"
OWNER_TEAM, MEMBER_TEAM, AI_TEAM = "Manchester Blue", "Merseyside Reds", "Karadeniz Storm"


# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_world():
    cleanup_users(PREFIX)
    _reseed()
    yield
    cleanup_users(PREFIX)


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    cleanup_users(PREFIX)
    _reseed()


@pytest.fixture
def market():
    world = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM), (ADMIN, None)], owner_team=OWNER_TEAM)
    age_managers(world)
    set_world_role(world, ADMIN, "ADMIN")
    try:
        yield world
    finally:
        cleanup_shared(world, reseed=False)


@pytest.fixture
def web_state(monkeypatch):
    """Callback'leri AppTest disinda cagirmak icin duz sozluk session_state."""
    import web_common

    state: dict = {}
    monkeypatch.setattr(web_common.st, "session_state", state)
    return state


def _owner(world):
    return app_as(world.owner_id, OWNER, world.world_id, page="transfer")        # Faz 13I: alici pazarda baslar


def _member(world):
    return app_as(world.user_ids[MEMBER], MEMBER, world.world_id, page="mesajlar")   # satici gelen kutusunda


def _shared_pages(role: str) -> list[str]:
    import nav_view

    return nav_view.pages_for(tournament=False, shared=True, internationals=False, role=role)


def _goto(at, slug: str):
    """Faz 13I: kulubu olan koltuk menuden sayfaya gider (zaten oradaysa bir sey yapmaz)."""
    if current_page(at) != slug:
        goto(at, slug)
    return at


def _admin(world):
    return app_as(world.user_ids[ADMIN], ADMIN, world.world_id)


def _run(at):
    at.run()
    assert not at.exception, at.exception
    return at


def _keys(elements) -> set[str]:
    return {e.key for e in elements if e.key}


def _sql(statement: str, **params):
    import database

    with database.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return result.all() if result.returns_rows else []


def _player_id(name: str) -> int:
    return _sql("SELECT id FROM public.players WHERE name = :n", n=name)[0][0]


def _team_id(name: str) -> int:
    return _sql("SELECT id FROM public.teams WHERE name = :n", n=name)[0][0]


def _player(player_id: int):
    return _sql("SELECT team_id, loan_from_team_id FROM public.players WHERE id = :p", p=player_id)[0]


def _budgets() -> tuple[int, int]:
    rows = dict(_sql("SELECT name, transfer_budget FROM public.teams WHERE name IN (:a, :b)", a=OWNER_TEAM,
                     b=MEMBER_TEAM))
    return rows[OWNER_TEAM], rows[MEMBER_TEAM]


def _offer(offer_id: int):
    return _sql("SELECT status, fee, round, kind, loan_weeks, loan_wage_share, reason, buyer_team_id, seller_team_id "
                "FROM public.transfer_offers WHERE id = :o", o=offer_id)[0]


def _offer_ids() -> list[int]:
    return [row[0] for row in _sql("SELECT id FROM public.transfer_offers ORDER BY id")]


def _fair_play(world, name: str) -> float:
    return _sql("SELECT fair_play FROM public.world_managers WHERE id = :s", s=world.seat_ids[name])[0][0]


def _pick_target(at, name: str, player_id: int) -> None:
    """Transfer Merkezi › Oyuncu ara: filtre + hedef oyuncu."""
    _goto(at, "transfer")
    at.select_slider(key="mkt_stars").set_value("Tümü")
    at.text_input(key="mkt_name").set_value(name)
    _run(at)
    at.selectbox(key="mkt_target").set_value(player_id)
    _run(at)


def _market_table(at):
    """Transfer Pazari oyuncu tablosu (DataFrame)."""
    return next(d.value for d in at.dataframe if "Sözleşme" in d.value.columns and "Değer (tahmin)" in d.value.columns)


def _section(at, section: str) -> None:
    _goto(at, "mesajlar")
    at.radio(key="hub_section").set_value(section)
    _run(at)


def _hub(world, username: str, work):
    """Kurulum kisayolu: menajerin MarketHub'i ile dogrudan islem (commit)."""
    import database
    from career_manager import CareerManager
    from market_hub import MarketHub

    uid = world.owner_id if username == OWNER else world.user_ids[username]
    with database.career_context("public"), database.session_scope() as db:
        return work(MarketHub(CareerManager(db, seed=5, manager_user_id=uid)))


# ---------------------------------------------------------------------------
# 1) Tam akis: transfer sekmesinden teklif -> rozet / gelen kutusu -> karsi teklif -> kabul -> sozlesme -> DB
#    + yonetici "Adil oyun" bolumunden geri alma
# ---------------------------------------------------------------------------

def test_offer_counter_accept_contract_completion_and_admin_reversal(market):
    import market_view
    import web_app
    import world_admin_view as admin_view

    target = _player_id("Jack Edwards")
    buyer_id, seller_id = _team_id(OWNER_TEAM), _team_id(MEMBER_TEAM)
    budgets = _budgets()
    logs_before = _sql("SELECT count(*) FROM public.transfer_log")[0][0]

    owner = _owner(market)
    _pick_target(owner, "Jack Edwards", target)
    keys = _keys(owner.button)
    assert "mkt_h_offer" in keys and not {"mkt_offer", "mkt_ai_loan"} & keys     # AI alani yerine menajer paneli
    assert owner.radio(key="mkt_kind").value == "TRANSFER"
    table = _market_table(owner)
    assert list(table["Menajer"]) == [MEMBER] and list(table["Kulüp"]) == [MEMBER_TEAM]
    owner.number_input(key="mkt_fee").set_value(10_000_000)
    owner.text_input(key="mkt_note").set_value("<b>Forvet</b> lazım")
    _run(owner)
    assert any("Adil oyun ön değerlendirmesi: Uygun" in c.value for c in owner.caption)
    assert _offer_ids() == []                                                     # on degerlendirme yazmaz
    _click(owner, "mkt_h_offer")
    [oid] = _offer_ids()
    offer = _offer(oid)
    assert (offer.status, offer.fee, offer.buyer_team_id, offer.seller_team_id) == ("PENDING", 10_000_000, buyer_id,
                                                                                    seller_id)
    assert any("teklifi gönderildi" in s.value for s in owner.success)
    assert owner.button(key="mkt_h_offer").disabled                               # ayni oyuncuya ikinci acik teklif yok

    # Satici: kenar cubugu rozeti + gelen teklif karti (sekme etiketinde sayac yok)
    member = _member(market)
    sidebar = _texts(member.sidebar.caption)
    assert "Yanıt bekleyen teklif: 1" in sidebar and "Okunmamış bildirim: 1" in sidebar
    assert not member.tabs and menu(member) == _shared_pages("MEMBER")
    assert member.button(key="nav_to_mesajlar").label.endswith("(2)")          # menu sayaci: teklif + bildirim
    assert member.radio(key="hub_section").value == market_view.SEC_IN
    keys = _keys(member.button)
    assert {f"off_accept_{oid}", f"off_reject_{oid}", f"off_counter_{oid}"} <= keys and f"off_withdraw_{oid}" not in keys
    notes = [c.value for c in member.caption if "Forvet" in c.value]
    assert notes and all("<b>" not in n and "&lt;b&gt;Forvet" in n for n in notes)   # not duz metin

    member.number_input(key=f"off_counter_fee_{oid}").set_value(12_000_000)
    _click(member, f"off_counter_{oid}")
    offer = _offer(oid)
    assert (offer.status, offer.fee, offer.round) == ("COUNTERED", 12_000_000, 1)
    assert any("Karşı teklif gönderildi" in s.value for s in member.success)
    assert "Yanıt bekleyen teklif: 0" in _texts(member.sidebar.caption)

    # Alici karsi teklifi kabul eder ve sozlesme masasina oturur
    _run(owner)
    assert "Yanıt bekleyen teklif: 1" in _texts(owner.sidebar.caption)
    _section(owner, market_view.SEC_OUT)
    _click(owner, f"off_accept_{oid}")
    assert _offer(oid).status == "CONTRACT"
    assert any("Anlaşma sağlandı" in s.value for s in owner.success)
    _click(owner, f"off_contract_{oid}")
    neg = owner.session_state["hneg"]
    assert neg["offer_id"] == oid and neg["status"] == "OPEN" and neg["demand"]
    assert {"hneg_submit", "hneg_accept", "hneg_leave"} <= _keys(owner.button)
    assert owner.number_input(key="hneg_wage").value == neg["demand"][0]
    _click(owner, "hneg_leave")                                                   # masadan kalk: anlasma acik kalir
    assert "hneg" not in owner.session_state and _offer(oid).status == "CONTRACT"
    _click(owner, f"off_contract_{oid}")                                          # masa kayittan yeniden kurulur
    assert owner.session_state["hneg"]["demand"] == neg["demand"]
    _click(owner, "hneg_submit")                                                  # talep kutulara dolu: imza
    assert any("TRANSFER TAMAM" in s.value for s in owner.success), _texts(owner.error)
    assert "hneg" not in owner.session_state

    assert _player(target).team_id == buyer_id
    assert _budgets() == (budgets[0] - 12_000_000, budgets[1] + 12_000_000)
    assert _offer(oid).status == "COMPLETED"
    logs = _sql("SELECT kind, from_team_id, to_team_id, fee FROM public.transfer_log WHERE player_id = :p", p=target)
    assert [tuple(r) for r in logs] == [("TRANSFER", seller_id, buyer_id, 12_000_000)]
    assert _sql("SELECT count(*) FROM public.transfer_log")[0][0] == logs_before + 1

    # Sahip (yonetici ama taraf) geri alamaz; kulupsuz yonetici geri alir
    _goto(owner, "dunya-yonetimi")
    owner.radio(key="adm_section").set_value(admin_view.SEC_FAIR)
    _run(owner)
    assert owner.button(key=f"adm_reverse_{oid}").disabled
    admin = _admin(market)
    assert [t.label for t in admin.tabs] == [web_app.TAB_CLUBS, web_app.TAB_HUB, web_app.TAB_ADMIN]
    admin.radio(key="adm_section").set_value(admin_view.SEC_FAIR)
    _run(admin)
    assert admin.button(key=f"adm_reverse_{oid}").disabled                        # once onay
    admin.text_input(key="adm_reverse_reason").set_value("Şüpheli <i>anlaşma</i>")
    admin.checkbox(key="adm_reverse_ok").check()
    _run(admin)
    _click(admin, f"adm_reverse_{oid}")
    assert any("Anlaşma geri alındı" in s.value for s in admin.success), _texts(admin.error)
    assert _player(target).team_id == seller_id and _budgets() == budgets
    offer = _offer(oid)
    assert (offer.status, offer.reason) == ("REVERSED", "Şüpheli <i>anlaşma</i>")
    assert (_fair_play(market, OWNER), _fair_play(market, MEMBER)) == (75.0, 75.0)


# ---------------------------------------------------------------------------
# 2) Adil oyun: engellenen anlasmanin Turkce nedeni, inceleme kuyrugu (onay / ret)
# ---------------------------------------------------------------------------

def test_blocked_deal_shows_reason_and_admin_reviews_from_admin_tab(market):
    import market_view
    import world_admin_view as admin_view
    from market_hub import OfferDraft

    star = _player_id("Marcus Jones")                                             # 33.5M degerinde
    owner = _owner(market)
    _pick_target(owner, "Marcus Jones", star)
    owner.number_input(key="mkt_fee").set_value(0)
    _run(owner)
    assert any("ön değerlendirmesi: Engellendi" in c.value for c in owner.caption)
    assert any("değerinin çok altında" in c.value for c in owner.caption)
    _click(owner, "mkt_h_offer")
    [blocked] = _offer_ids()

    member = _member(market)
    _click(member, f"off_accept_{blocked}")
    offer = _offer(blocked)
    assert offer.status == "BLOCKED" and "değerinin çok altında" in offer.reason
    errors = _texts(member.error)
    assert "Anlaşma engellendi" in errors and "değerinin çok altında" in errors
    assert (_fair_play(market, OWNER), _fair_play(market, MEMBER)) == (90.0, 90.0)
    assert _player(star).team_id == _team_id(MEMBER_TEAM)

    # Iki inceleme: biri arayuzden kabul edilir (REVIEW sonucu gosterilir), digeri dogrudan
    first = _hub(market, OWNER, lambda h: h.make_offer(OfferDraft(player_id=_player_id("Connor Davies"),
                                                                  fee=6_000_000)).id)
    second = _hub(market, OWNER, lambda h: h.make_offer(OfferDraft(player_id=_player_id("Harry Green"),
                                                                   fee=5_000_000)).id)
    _run(member)
    _click(member, f"off_accept_{first}")
    assert _offer(first).status == "REVIEW"
    assert any("yönetici incelemesine gönderildi" in w.value for w in member.warning)
    assert _hub(market, MEMBER, lambda h: h.accept(second).status) == "REVIEW"

    _goto(owner, "dunya-yonetimi")
    owner.radio(key="adm_section").set_value(admin_view.SEC_FAIR)                 # sahip taraf: inceleyemez
    _run(owner)
    assert owner.button(key=f"adm_review_approve_{first}").disabled
    admin = _admin(market)
    assert "İnceleme bekleyen anlaşma: 2" in _texts(admin.sidebar.caption)
    admin.radio(key="adm_section").set_value(admin_view.SEC_FAIR)
    _run(admin)
    _click(admin, f"adm_review_approve_{first}")
    assert _offer(first).status == "CONTRACT"
    assert any("Anlaşma onaylandı" in s.value for s in admin.success)
    admin.text_input(key=f"adm_review_reason_{second}").set_value("Değer çok düşük <script>x</script>")
    _click(admin, f"adm_review_deny_{second}")
    offer = _offer(second)
    assert (offer.status, offer.reason) == ("BLOCKED", "Değer çok düşük <script>x</script>")
    assert (_fair_play(market, OWNER), _fair_play(market, MEMBER)) == (77.0, 77.0)   # 90 + 2 - 15
    events = _sql("SELECT payload->>'decision' FROM public.world_events WHERE kind = 'REVIEW' ORDER BY id")
    assert [e[0] for e in events] == ["APPROVED", "DENIED"]

    _run(owner)                                                                   # alici sonucu gorur
    _section(owner, market_view.SEC_OUT)
    assert f"off_contract_{first}" in _keys(owner.button)
    denied = [e.value for e in owner.error if "Değer çok düşük" in e.value]
    assert denied and all("<script>" not in d for d in denied)


# ---------------------------------------------------------------------------
# 3) Kiraliklar: insan <-> insan teklif, AI kulubunden kiralik, AI kulubune kiralik
# ---------------------------------------------------------------------------

def test_loans_between_managers_and_with_ai_clubs(market):
    import market_view

    loanee = _player_id("Ethan Green")
    buyer_id, seller_id, ai_id = _team_id(OWNER_TEAM), _team_id(MEMBER_TEAM), _team_id(AI_TEAM)
    owner = _owner(market)
    _pick_target(owner, "Ethan Green", loanee)
    owner.radio(key="mkt_kind").set_value("LOAN")
    _run(owner)
    assert owner.number_input(key="mkt_fee").value == 0 and "mkt_exchange" not in _keys(owner.selectbox)
    owner.number_input(key="mkt_loan_weeks").set_value(8)
    owner.slider(key="mkt_loan_share").set_value(50)
    _run(owner)
    assert any("ön değerlendirmesi: Uygun" in c.value for c in owner.caption)
    _click(owner, "mkt_h_offer")
    [oid] = _offer_ids()
    offer = _offer(oid)
    assert (offer.kind, offer.status, offer.loan_weeks, offer.loan_wage_share) == ("LOAN", "PENDING", 8, 50)

    member = _member(market)
    _click(member, f"off_accept_{oid}")
    assert _offer(oid).status == "CONTRACT"
    _run(owner)
    _section(owner, market_view.SEC_OUT)
    assert owner.button(key=f"off_contract_{oid}").label.endswith("Kiralığı tamamla")
    _click(owner, f"off_contract_{oid}")
    assert any("Kiralık tamamlandı" in s.value for s in owner.success), _texts(owner.error)
    assert tuple(_player(loanee)) == (buyer_id, seller_id) and _offer(oid).status == "COMPLETED"
    [loan] = _sql("SELECT id, status, wage_share FROM public.loans WHERE player_id = :p", p=loanee)
    assert (loan.status, loan.wage_share) == ("ACTIVE", 50)

    _run(member)                                                                  # ana kulup: geri cagirma henuz yok
    _section(member, market_view.SEC_LOANS)
    assert member.button(key=f"loan_recall_{loan.id}").disabled
    _pick_target(member, "Ethan Green", loanee)                                   # kiraliktaki oyuncuya teklif yok
    assert member.button(key="mkt_h_offer").disabled
    assert any("Kiralık oyuncu" in w.value for w in member.warning)

    # Kendi oyuncunu yapay zekâ kulubune kirala
    wood = _player_id("Marcus Wood")
    _section(owner, market_view.SEC_LOANS)
    owner.selectbox(key="loan_out_player").set_value(wood)
    owner.selectbox(key="loan_out_team").set_value(ai_id)
    owner.slider(key="loan_out_share").set_value(50)
    _click(owner, "loan_out_send")
    assert any("Kiralık onaylandı" in s.value for s in owner.success), _texts(owner.error)
    assert tuple(_player(wood)) == (ai_id, buyer_id)
    [out_loan] = _sql("SELECT id FROM public.loans WHERE player_id = :p", p=wood)
    assert owner.button(key=f"loan_recall_{out_loan.id}").disabled

    # Yapay zekâ kulubunden kiralik iste: once kadro yetmez (Turkce neden), kadro buyuyunce kabul
    kerem = _player_id("Kerem Erdem")
    _pick_target(owner, "Kerem Erdem", kerem)
    keys = _keys(owner.button)
    assert {"mkt_offer", "mkt_ai_loan"} <= keys and "mkt_h_offer" not in keys
    owner.slider(key="mkt_ai_loan_share").set_value(75)
    _click(owner, "mkt_ai_loan")
    assert any("oyuncunun altına düşer" in e.value for e in owner.error)
    assert _player(kerem).team_id == ai_id
    _sql("UPDATE public.players SET in_academy = false WHERE id IN (SELECT id FROM public.players "
         "WHERE team_id = :t AND in_academy ORDER BY id LIMIT 3)", t=ai_id)
    _click(owner, "mkt_ai_loan")
    assert any("Kiralık onaylandı" in s.value for s in owner.success), _texts(owner.error)
    assert tuple(_player(kerem)) == (buyer_id, ai_id)


# ---------------------------------------------------------------------------
# 4) Mesajlar, pano, bildirimler; kullanici metni kacisli
# ---------------------------------------------------------------------------

def test_messages_board_notifications_are_plain_text(market):
    import market_view
    import messaging
    from models import ManagerMessage, WorldPost

    owner = _owner(market)
    _section(owner, market_view.SEC_MESSAGES)
    owner.selectbox(key="msg_to").set_value(market.seat_ids[MEMBER])
    owner.text_input(key="msg_subject").set_value("Takas <b>fikri</b>")
    owner.text_area(key="msg_body").set_value("<script>alert('x')</script>\n[tıkla](http://kotu.example) **kalın**")
    _click(owner, "msg_send")
    assert any("Mesaj gönderildi" in s.value for s in owner.success), _texts(owner.error)
    [message_id] = [r[0] for r in _sql("SELECT id FROM public.manager_messages")]

    member = _member(market)
    assert "Okunmamış mesaj: 1" in _texts(member.sidebar.caption)
    _section(member, market_view.SEC_MESSAGES)
    assert {f"msg_open_{message_id}", f"msg_reply_{message_id}", f"msg_delete_{message_id}"} <= _keys(member.button)
    _click(member, f"msg_open_{message_id}")
    bodies = [m.value for m in member.markdown if "alert" in m.value]
    assert bodies and all("<script>" not in b and "&lt;script&gt;" in b and "\\[tıkla\\]" in b for b in bodies)
    subjects = [m.value for m in member.markdown if "fikri" in m.value]
    assert subjects and all("<b>" not in s for s in subjects)
    assert _query(lambda db: db.get(ManagerMessage, message_id).read_at) is not None
    assert "Okunmamış mesaj: 0" in _texts(member.sidebar.caption)

    _click(member, f"msg_reply_{message_id}")
    assert member.selectbox(key="msg_to").value == market.seat_ids[OWNER]
    assert member.text_input(key="msg_subject").value == "Ynt: Takas <b>fikri</b>"
    member.text_area(key="msg_body").set_value("Olur, konuşalım.")
    _click(member, "msg_send")
    assert _sql("SELECT count(*) FROM public.manager_messages WHERE recipient_manager_id = :s",
                s=market.seat_ids[OWNER])[0][0] == 1
    _click(member, f"msg_delete_{message_id}")
    assert _query(lambda db: db.get(ManagerMessage, message_id).deleted_by_recipient) is True
    assert f"msg_open_{message_id}" not in _keys(member.button)

    # Pano: yazar ve yonetici silebilir, yonetici sabitler; metin kacisli
    _section(member, market_view.SEC_BOARD)
    member.text_area(key="board_body").set_value("<img src=x onerror=alert(1)> Cuma hazırlık maçı?")
    _click(member, "board_post")
    [post_id] = [r[0] for r in _sql("SELECT id FROM public.world_posts")]
    _run(owner)
    _section(owner, market_view.SEC_BOARD)
    posts = [m.value for m in owner.markdown if "onerror" in m.value]
    assert posts and all("<img" not in p for p in posts)
    assert {f"board_delete_{post_id}", f"board_pin_{post_id}"} <= _keys(owner.button)   # sahip = yonetici
    _click(owner, f"board_pin_{post_id}")
    assert _query(lambda db: db.get(WorldPost, post_id).pinned) is True
    _run(member)
    assert f"board_pin_{post_id}" not in _keys(member.button) and f"board_delete_{post_id}" in _keys(member.button)
    _click(owner, f"board_delete_{post_id}")
    assert _query(lambda db: db.get(WorldPost, post_id).kind) == "DELETED"

    # Bildirimler: tek tek ve toplu okundu; kenar cubugu dugmesi yalnizca bildirimleri isaretler
    import database

    with database.session_scope() as db:
        for seat, body in ((market.seat_ids[MEMBER], "<i>Uyarı</i> bir"), (market.seat_ids[MEMBER], "iki"),
                           (market.seat_ids[OWNER], "sahibe")):
            messaging.notify(db, seat, messaging.NotificationKind.KICK_WARNING, body)
    _run(member)
    assert "Okunmamış bildirim: 2" in _texts(member.sidebar.caption)
    _section(member, market_view.SEC_NOTIFICATIONS)
    warned = [m.value for m in member.markdown if "Uyarı" in m.value]
    assert warned and all("<i>" not in w for w in warned)
    first = _sql("SELECT id FROM public.notifications WHERE manager_id = :s ORDER BY id LIMIT 1",
                 s=market.seat_ids[MEMBER])[0][0]
    _click(member, f"ntf_read_{first}")
    assert "Okunmamış bildirim: 1" in _texts(member.sidebar.caption)
    _click(member, "ntf_mark_all")
    assert "Okunmamış bildirim: 0" in _texts(member.sidebar.caption)

    _run(owner)
    sidebar = _texts(owner.sidebar.caption)
    assert "Okunmamış bildirim: 1" in sidebar and "Okunmamış mesaj: 1" in sidebar
    assert "bildirimleri" in owner.button(key="wp_mark_read").label.lower()
    _click(owner, "wp_mark_read")
    sidebar = _texts(owner.sidebar.caption)
    assert "Okunmamış bildirim: 0" in sidebar and "Okunmamış mesaj: 1" in sidebar     # mesaja dokunmaz
    assert _query(lambda db: db.scalar(select(func.count()).select_from(ManagerMessage).where(
        ManagerMessage.read_at.is_(None)))) == 1


# ---------------------------------------------------------------------------
# 5) Eski (kisisel) kariyer: Transfer Pazari bugunku gibi
# ---------------------------------------------------------------------------

def test_personal_career_transfer_tab_is_unchanged():
    import web_app
    from tests.test_web_app import _app

    _set_user_team("Manchester Blue")
    target = _player_id("Jack Edwards")
    at = _app(seed="1", page="transfer")
    assert not at.tabs and menu(at) == web_app.CAREER_PAGES and "mesajlar" not in menu(at)
    _pick_target(at, "Jack Edwards", target)
    keys = _keys(at.button) | _keys(at.radio) | _keys(at.slider) | _keys(at.number_input)
    assert {"mkt_offer", "mkt_shortlist"} <= keys
    assert not {"mkt_h_offer", "mkt_kind", "mkt_ai_loan", "mkt_ai_loan_share", "wp_mark_read", "hub_section"} & keys
    assert list(_market_table(at).columns) == ["Oyuncu", "Kulüp", "Mv", "Yaş", "Bilgi", "Güç (tahmin)",
                                               "Değer (tahmin)", "Sözleşme"]
    assert not [c for c in at.caption if "Adil oyun" in c.value]


# ---------------------------------------------------------------------------
# 6) Sunucu tarafi yetki: widget'tan gelen id'lere guvenilmez
# ---------------------------------------------------------------------------

def test_market_callbacks_authorise_on_the_server(market, web_state):
    import market_view
    import messages_view
    import web_common
    import world_admin_view
    from accounts import AuthSession
    from market_hub import OfferDraft

    oid = _hub(market, OWNER, lambda h: h.make_offer(OfferDraft(player_id=_player_id("Jack Edwards"),
                                                                fee=10_000_000)).id)

    def as_user(name):
        uid = market.owner_id if name == OWNER else market.user_ids[name]
        web_state.clear()
        web_state["auth"] = world_auth(uid, name, "public", world_id=market.world_id, world_kind="SHARED")

    as_user(MEMBER)                                                               # satici geri cekemez
    assert market_view.cb_offer_withdraw(oid) is None
    assert web_state["flash"]["hub"][0][0] == "error" and _offer(oid).status == "PENDING"
    assert world_admin_view.cb_admin_reverse(oid) is None                        # uye yonetici islemi yapamaz
    assert web_state["flash"]["admin"] == [("error", web_common.ADMIN_ONLY_TEXT)]

    as_user(ADMIN)                                                                # taraf olmayan yonetici kabul edemez
    assert market_view.cb_offer_accept(oid) is None
    assert web_state["flash"]["hub"][0][0] == "error" and _offer(oid).status == "PENDING"
    web_state["msg_to"], web_state["msg_subject"], web_state["msg_body"] = market.seat_ids[MEMBER], "Konu", "Govde"
    web_state["msg_offer_id"] = oid                                               # yonetici teklife erisebilir
    assert messages_view.cb_msg_send() is None
    assert _sql("SELECT count(*) FROM public.manager_messages")[0][0] == 1

    as_user(OWNER)                                                                # baskasinin mesajini silemez
    message_id = _sql("SELECT id FROM public.manager_messages")[0][0]
    assert messages_view.cb_msg_delete(message_id) is None
    assert web_state["flash"]["hub"] == [("error", "Mesaj bulunamadı\\.")]
    assert _query(lambda db: db.execute(text("SELECT deleted_by_recipient, deleted_by_sender FROM "
                                             "public.manager_messages")).one()) == (False, False)

    web_state.clear()                                                             # dunyasiz oturum: pazar kapali
    web_state["auth"] = AuthSession(0, "test_menajer", "public")
    assert market_view.cb_offer_accept(oid) is None
    assert web_state["flash"]["hub"] == [("error", market_view.NOT_SHARED_TEXT)]
    assert _offer(oid).status == "PENDING"
