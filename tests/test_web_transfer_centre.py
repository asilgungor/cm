"""
Faz 13I: Transfer Merkezi (transfer_centre_view) uctan uca -- Streamlit AppTest + gercek PostgreSQL.

    alis   gozlemci gonder -> haftalar ilerler (bilgi artar) -> kulube sor (mkt_offer) -> dusuk yapilandirilmis
           teklif (pesin + 12 ay taksit) -> KARSI TEKLIF -> karsi teklifi kabul -> kisisel sartlar (menajer masasi)
           -> saglik -> donem kapali: "donem acilinca tamamlanacak" -> donem acilinca kendiliginden tamamlanir (hafta
           raporunda masa notu); odeme defterinde 4 taksit planli. Ayni lig + donem acik: tamamla dugmesi.
    satis  AI kulubu teklifleri: kabul (transfer + alacak taksitleri), ret, karsi teklif (asiri istek: alici ceker)
    K12    ekranda kulubun hedef / taban bedeli, sabir sayisi, ikna skoru ve AI ust siniri SAYI olarak yok
Her testten once dunya yeniden kurulur (test_web_app yardimcilari); modul sonunda temiz dunya geri birakilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, menu  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _html,
    _query,
    _reseed,
    _search,
    _set_user_team,
    _team,
    _texts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

USER = "Istanbul Lions"
FOREIGN = "Rhône Gones"                  # baska lig: once gozlem gerekir
SEC_FILES, SEC_INCOMING, SEC_PAYMENTS = "Dosyalarım", "Gelen teklifler", "Ödemeler"


@pytest.fixture(autouse=True)
def fresh_world():
    _reseed()
    yield


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    _reseed()


def _deal(db, deal_id: int):
    from models import TransferDeal
    return db.get(TransferDeal, deal_id)


def _in_deal_id(db, player_id: int) -> int | None:
    from sqlalchemy import select

    from models import TransferDeal
    return db.scalar(select(TransferDeal.id).where(TransferDeal.player_id == player_id,
                                                   TransferDeal.buyer_team_id == _team(db, USER).id))


def _stance_target(deal_id: int) -> int:
    """Test icin kulubun GIZLI hedef bedeli (arayuz bunu hic gostermez)."""
    import database
    from career_manager import CareerManager
    from models import Player, Team
    from transfer_desk import TransferDesk

    with database.SessionLocal() as db:
        cm = CareerManager(db)
        desk = TransferDesk(cm)
        deal = _deal(db, deal_id)
        return desk._stance(deal, db.get(Player, deal.player_id), db.get(Team, deal.seller_team_id),
                            db.get(Team, deal.buyer_team_id)).target


def _foreign_target(db) -> tuple[int, str]:
    from models import Position

    team = _team(db, FOREIGN)
    player = sorted((p for p in team.players if p.position in (Position.MID, Position.DEF) and p.age > 23),
                    key=lambda p: (p.overall_rating, p.id))[0]
    return player.id, player.name


def _no_hidden_numbers(at, target: int) -> None:
    """
    K12: gizli hedef / taban bedel kesin sayi olarak, ikna skoru ve sabir sayisi hic gecmez. (Kulubun soyledigi SISLI
    fiyat araligi gorunur; 0.1M'lik yuvarlamada bir ucu hedefle ayni gorunebilir, bu yuzden kesin tutar aranir.)
    """
    text = _texts(at.markdown) + _texts(at.caption) + _texts(at.info) + _texts(at.warning)
    for exact in (str(target), f"{target:,}", f"{target:,}".replace(",", ".")):
        assert exact not in text
    lowered = text.casefold()
    assert "ikna skoru" not in lowered and "hedef bedel" not in lowered and "taban" not in lowered
    assert "sabır:" not in lowered and "kalan sabır" not in lowered


def test_full_purchase_scout_bid_counter_terms_medical_complete_and_ledger():
    _set_user_team(USER, transfer_budget=300_000_000, wage_budget=3_000_000)
    pid, name = _query(_foreign_target)
    at = _app(seed="3", page="transfer")
    _search(at, name, pid)
    table = next(d for d in at.dataframe if d.key == "mkt_table").value
    assert table.loc[table["Oyuncu"] == name, "Bilgi"].tolist() == ["%0"]
    assert at.button(key="mkt_offer").disabled                              # bilgi yok: once gozlemci
    _click(at, "tc_scout")
    assert any("Gözlemci görevlendirildi" in s.value for s in at.success)

    for week in (1, 2):                                                      # gozlemci haftada ~%24 bilgi toplar
        _click(at, "nav_continue")
        assert any(f"{week}. hafta oynandı" in s.value for s in at.success)
    _search(at, name, pid)
    table = next(d for d in at.dataframe if d.key == "mkt_table").value
    assert int(table.loc[table["Oyuncu"] == name, "Bilgi"].tolist()[0].lstrip("%")) >= 25
    assert not at.button(key="mkt_offer").disabled
    _click(at, "mkt_offer")                                                  # kulube sor: dosya acilir
    deal_id = _query(lambda db: _in_deal_id(db, pid))
    assert at.session_state["tc_deal"] == deal_id and at.radio(key="tc_section").value == SEC_FILES
    assert any("Kulübün tavrı" in m.value for m in at.markdown)
    target = _stance_target(deal_id)

    fee = int(round(target * 0.75 / 10_000) * 10_000)
    at.number_input(key="tc_fee").set_value(fee)
    at.slider(key="tc_pct").set_value(60)
    at.selectbox(key="tc_months").set_value(12)
    at.run()
    assert any("12 ayda 4 taksit" in c.value for c in at.caption)           # teklif onizlemesi
    _click(at, "tc_bid")
    deal = _query(lambda db: (lambda d: (d.status, d.last_action, d.turn))(_deal(db, deal_id)))
    assert deal == ("BIDDING", "COUNTER", "MANAGER"), deal
    assert at.button(key="tc_accept_counter") and any("karşı teklif" in w.value for w in at.warning)
    _no_hidden_numbers(at, target)

    _click(at, "tc_accept_counter")
    assert _query(lambda db: _deal(db, deal_id).status) == "TERMS"
    _click(at, "tc_terms_open")
    assert "cm-log" in _html(at) and at.number_input(key="tc_t_wage").value > 0
    assert at.button(key="tc_t_submit") and at.number_input(key="tc_t_agent")
    _no_hidden_numbers(at, target)
    _click(at, "tc_t_accept")                                                # oyuncunun talebi aynen
    status = _query(lambda db: _deal(db, deal_id).status)
    if status == "MEDICAL":
        _click(at, "tc_med_go")
        status = _query(lambda db: _deal(db, deal_id).status)
    assert status == "AGREED", status
    # 3. hafta: yaz donemi kapandi -> tamamla dugmesi yok, dosya "donem acilinca tamamlanacak" der
    assert not [b for b in at.button if b.key == "tc_complete"]
    assert any("dönem açılınca kendiliğinden tamamlanır" in i.value for i in at.info)
    for _week in range(8):                                                     # kis donemi acilinca kendiliginden
        _click(at, "nav_continue")
        if _query(lambda db: _deal(db, deal_id).status) == "COMPLETED":
            break
    assert _query(lambda db: (_deal(db, deal_id).status, db.get(__import__("models").Player, pid).team.name)) == \
        ("COMPLETED", USER)
    goto(at, "fikstur")                                                        # hafta raporunda masa notu
    # 15D-U: hafta raporu kalici gelen kutusundan gelir (baslik: "Hafta raporu · Sezon 1, N. hafta · Cumartesi ...")
    assert any(e.label.startswith("Hafta raporu · Sezon 1,") and "Cumartesi" in e.label for e in at.expander)
    assert any("tamamlandı" in m.value for m in at.markdown)
    goto(at, "transfer")

    at.radio(key="tc_section").set_value(SEC_PAYMENTS)
    at.run()
    ledger = at.dataframe[0].value
    instalments = ledger[ledger["Tür"] == "Taksit"]
    assert len(instalments) == 4 and set(instalments["Durum"]) == {"Planlandı"}
    assert set(instalments["Yön"]) == {"Ödeme"} and set(instalments["Karşı taraf"]) == {FOREIGN}


def test_same_league_generous_bid_completes_immediately_in_open_window():
    """Ayni lig (%35 bilgi) + yaz donemi acik: kabul -> kisisel sartlar -> tamamla dugmesi (maas alani kaydirma)."""
    _set_user_team(USER, transfer_budget=300_000_000, wage_budget=3_000_000)
    pid, name = _query(lambda db: (lambda p: (p.id, p.name))(sorted(
        (p for p in _team(db, "Karadeniz Storm").players if p.position.value in ("MID", "DEF") and p.age > 23),
        key=lambda p: (p.overall_rating, p.id))[0]))
    at = _app(seed="4", page="transfer")
    _search(at, name, pid)
    _click(at, "mkt_offer")
    deal_id = _query(lambda db: _in_deal_id(db, pid))
    target = _stance_target(deal_id)
    at.number_input(key="tc_fee").set_value(int(round(target * 2 / 10_000) * 10_000))
    at.slider(key="tc_pct").set_value(100)
    at.run()
    _click(at, "tc_bid")
    assert _query(lambda db: _deal(db, deal_id).status) == "TERMS"
    _click(at, "tc_terms_open")
    at.number_input(key="tc_t_sign").set_value(at.number_input(key="tc_t_sign").value + 50_000)
    at.run()
    _click(at, "tc_t_submit")                                                  # talepten iyi paket: kabul
    if _query(lambda db: _deal(db, deal_id).status) == "MEDICAL":
        _click(at, "tc_med_go")
    assert _query(lambda db: _deal(db, deal_id).status) == "AGREED"
    _click(at, "tc_complete")
    assert any("TRANSFER TAMAM" in s.value for s in at.success)
    assert _query(lambda db: db.get(__import__("models").Player, pid).team.name) == USER
    assert "tc_complete" not in {b.key for b in at.button}


def _ai_bids(count: int = 3) -> list[int]:
    """Oyuncularima yapay zekâ kulubu teklifleri (haftalik rastlantiyi beklemeden, masanin kendi yapisiyla)."""
    import database
    import transfer_desk
    import transfer_rules as rules
    from career_manager import CareerManager
    from models import Position
    from transfer_rules import DealTerms

    with database.session_scope() as db:
        cm = CareerManager(db)
        desk = transfer_desk.TransferDesk(cm)
        user = cm.user_team
        buyers = [cm.find_team(name) for name in ("Manchester Blue", "Madrid Blancos", "München Roten")][:count]
        players = sorted((p for p in user.players if p.position is not Position.GK),
                         key=lambda p: (p.overall_rating, p.id))[:count]
        ids = []
        for player, buyer in zip(players, buyers, strict=True):
            buyer.transfer_budget += 200_000_000
            buyer.wage_budget += 2_000_000
            fee = int(round(int(player.market_value) / 10_000) * 10_000)
            terms = DealTerms(fee=fee, upfront=fee // 2, instalment_months=12)
            deal = desk._new_deal(direction="OUT", player=player, seller=user, buyer=buyer, human=user,
                                  status="BIDDING", patience=3)
            transfer_desk._set_terms(deal, terms)
            deal.turn, deal.round, deal.last_action = "MANAGER", 1, "BID"
            deal.expires_career_week = cm.career_week + 3
            desk._log(deal, {"kind": "bid", "side": "CLUB", "terms": terms.to_dict()})
            desk._log(deal, {"kind": "ai_limit", "max": int(fee * 1.2),
                             "last": rules.package_value(terms, desk._ctx(player, user, buyer))})
            ids.append(deal.id)
        return ids


def test_incoming_offers_accept_reject_counter_and_receivables():
    _set_user_team(USER)
    accept_id, reject_id, counter_id = _ai_bids()
    at = _app(seed="3")
    assert at.button(key="nav_menu_inbox").label.endswith("(3)")              # 14S: Gelen Kutusu (n) sayaci
    offer = next(b.key for b in at.button if (b.key or "").startswith("home_msg_") and "teklif yaptı" in b.label)
    _click(at, offer)                                                        # ana sayfa haberleri: mesaji sec
    assert any("teklif yaptı" in m.value for m in at.main.markdown)          # secilen mesajin govdesi
    _click(at, "home_go")                                                    # mesajin eylemi -> Gelen teklifler
    assert at.session_state["nav_page"] == "transfer" and at.radio(key="tc_section").value == SEC_INCOMING
    assert {f"tc_in_accept_{i}" for i in (accept_id, reject_id, counter_id)} <= {b.key for b in at.button}

    player_id = _query(lambda db: _deal(db, accept_id).player_id)
    _click(at, f"tc_in_accept_{accept_id}")
    status = _query(lambda db: _deal(db, accept_id).status)
    assert status in ("COMPLETED", "COLLAPSED"), status
    if status == "COMPLETED":
        assert any("TRANSFER TAMAM" in s.value for s in at.success)
        assert _query(lambda db: db.get(__import__("models").Player, player_id).team.name) == "Manchester Blue"
    at.text_input(key=f"tc_in_reason_{reject_id}").set_value("Satılık değil")
    _click(at, f"tc_in_reject_{reject_id}")
    assert _query(lambda db: (_deal(db, reject_id).status, _deal(db, reject_id).reason)) == ("REJECTED", "Satılık değil")

    fee = _query(lambda db: int(_deal(db, counter_id).fee))
    at.number_input(key=f"tc_in_fee_{counter_id}").set_value(fee * 3)        # ust sinirin cok ustu: alici ceker
    _click(at, f"tc_in_counter_{counter_id}")
    assert _query(lambda db: _deal(db, counter_id).status) == "REJECTED"
    assert any("geri çekti" in e.value for e in at.error)
    assert not at.button(key="nav_to_transfer").label.endswith(")")          # yanit bekleyen kalmadi

    if status == "COMPLETED":                                                # alacak taksitleri defterde
        at.radio(key="tc_section").set_value(SEC_PAYMENTS)
        at.run()
        ledger = at.dataframe[0].value
        receivable = ledger[(ledger["Tür"] == "Taksit") & (ledger["Yön"] == "Tahsilat")]
        assert len(receivable) == 4 and set(receivable["Karşı taraf"]) == {"Manchester Blue"}


def test_my_players_listing_and_asking_price():
    _set_user_team(USER)
    pid = _query(lambda db: sorted(_team(db, USER).players, key=lambda p: (p.overall_rating, p.id))[0].id)
    at = _app(page="transfer")
    at.radio(key="tc_section").set_value("Oyuncularım")
    at.run()
    at.selectbox(key="tc_my_pick").set_value(pid)
    at.run()
    _click(at, "tc_list_transfer")
    at.number_input(key="tc_ask").set_value(12_500_000)
    _click(at, "tc_ask_save")
    player = _query(lambda db: (lambda p: (p.transfer_listed, p.asking_price))(db.get(__import__("models").Player, pid)))
    assert player == (True, 12_500_000)
    assert at.button(key="tc_list_transfer").label == "Transfer listesinden çıkar"      # 14FG: emoji yok
    _click(at, "tc_ask_clear")
    assert _query(lambda db: db.get(__import__("models").Player, pid).asking_price) is None


def test_transfer_centre_is_not_in_tournament_mode_menu():
    import nav_view

    _reseed(mode="TOURNAMENT_MODE")
    participants = _query(lambda db: sorted(e.team.name for e in
                                            __import__("career_manager").CareerManager(db).tournaments.current().entries))
    _set_user_team(participants[0])
    at = _app()
    assert menu(at) == list(nav_view.TOURNAMENT_PAGES) and "transfer" not in menu(at)
    goto(at, "Devler Arenası")
    assert at.button(key="arena_draw_all")
