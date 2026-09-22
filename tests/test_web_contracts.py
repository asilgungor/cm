"""
Faz 15A-U: Sozlesme ekranlari (Transfer Merkezi > Sözleşmeler, oyuncu profili Sözleşme sekmesi, Gelen Kutusu
uyarilari, Kadro > Sözleşme gorunumu, Bul > kulupsuz) uctan uca -- Streamlit AppTest + gercek PostgreSQL.

    yenileme    sozlesmesi biten oyuncu tabloda -> masa (tc_c_open) -> talebi kabul -> SIGNED, kalan yil artar
    fesih       tazminat ONAY DUGMESINDEN ONCE gorunur; onaysiz dugme pasif; fesihten sonra oyuncu kulupsuz
    serbest     havuzda gorunur (sisli yetenek / deger), masa acilir ve imzalanir; profil Eylem menusunden de
    on sozlesme donem kapaliyken gerekce yazar (dugme pasif)
    haber       Gelen Kutusu'nda "Sözleşmesi bu sezon bitenler" -> Transfer Merkezi > Sözleşmeler
    bayrak      contracts.CONTRACT_CYCLE kapaliyken bolum HIC cizilmez
    K12         ekranda ikna skoru / gizli sayi yok; baska kulubun oyuncusunun yetenegi sisli
Her testten once dunya yeniden kurulur (test_web_app yardimcilari); modul sonunda temiz dunya geri birakilir.
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
    _facts,
    _html,
    _query,
    _reseed,
    _set_user_team,
    _team,
    _texts,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

USER = "Istanbul Lions"
OTHER = "Karadeniz Storm"
SEC = "Sözleşmeler"
TAB_FREE = "Serbest oyuncular"
TAB_PRE = "Ön sözleşme"


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

def _expiring(count: int = 1, *, keeper: bool = False) -> list[tuple[int, str]]:
    """Menajerin en zayif oyuncularini (kulubunu asmayanlar) son sezona cevirir: sozlesmesi bitiyor."""
    from database import session_scope
    from models import Position

    with session_scope() as db:
        team = _team(db, USER)
        pool = [p for p in team.players if not p.in_academy and p.loan_from_team_id is None
                and (keeper or p.position is not Position.GK)]
        picks = sorted(pool, key=lambda p: (p.overall_rating, p.id))[:count]
        for player in picks:
            player.contract_years = 1
        return [(p.id, p.name) for p in picks]


def _release_from(club: str) -> tuple[int, str]:
    """Baska kulupten bir oyuncuyu serbest birakir (havuzu doldurur; 15A kontrolcu API'si)."""
    from career_manager import CareerManager
    from database import session_scope
    from models import Position

    with session_scope() as db:
        cm = CareerManager(db)
        team = _team(db, club)
        player = sorted((p for p in team.players if not p.in_academy and p.position is not Position.GK),
                        key=lambda p: (p.overall_rating, p.id))[0]
        name, pid = player.name, player.id
        cm.release_player(player)
        return pid, name


def _player(db, pid: int):
    from models import Player

    return db.get(Player, pid)


def _talk(db, pid: int):
    from sqlalchemy import select

    from models import ContractTalk

    return db.scalar(select(ContractTalk).where(ContractTalk.player_id == pid)
                     .order_by(ContractTalk.id.desc()).limit(1))


def _frame(at, key: str):
    return next(d for d in at.dataframe if d.key == key).value


def _open_section(at, tab: str | None = None):
    """Bolum secicisinin etiketinde sayac olabilir ("Sözleşmeler (5)"): oturum durumundan sec."""
    at.session_state["tc_section"] = SEC
    if tab is not None:
        at.session_state["tc_csec"] = tab
    at.run()
    assert not at.exception, at.exception
    return at


def _no_hidden_numbers(at) -> None:
    """K12: ikna skoru / gizli sayi ekranda yok (masanin etiketleri gorunur)."""
    text = (_texts(at.markdown) + _texts(at.caption) + _texts(at.info) + _texts(at.warning)
            + _texts(at.success) + _texts(at.error)).casefold()
    for forbidden in ("ikna skoru", "ikna puanı", "gizli", "yenileme skoru", "sabır:"):
        assert forbidden not in text, forbidden


# ---------------------------------------------------------------------------
# 1) Sozlesmelerim: tablo + yenileme masasi
# ---------------------------------------------------------------------------

def test_contracts_section_lists_expiring_players_and_renews_through_the_desk():
    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    (pid, name), = _expiring(1)
    at = _app(page="transfer")
    assert any(o.startswith(SEC) for o in at.radio(key="tc_section").options)     # bayrak acik: bolum menude
    _open_section(at)
    facts = _facts(at)
    assert int(facts.get("Sözleşmesi bitiyor", 0)) >= 1 and "Ön sözleşme dönemi" in facts
    frame = _frame(at, "lk_contracts")
    row = frame[frame["Oyuncu"] == name]
    assert len(row) == 1 and row["Durum"].tolist() == ["Sözleşmesi bitiyor"]
    season = _query(lambda db: __import__("career_manager").CareerManager(db).season)
    assert int(row["Bitiş (sezon)"].tolist()[0]) == season
    assert row["Fesih bedeli (EUR)"].tolist()[0] > 0
    at.selectbox(key="tc_c_pick").set_value(pid)
    at.run()
    _click(at, "tc_c_open")
    assert not [e for e in at.error if "reddet" in e.value], "oyuncu masaya oturmadı (seed değişmiş olabilir)"
    assert "cm-log" in _html(at) and at.number_input(key="tc_c_wage").value > 0
    assert at.number_input(key="tc_c_agent") and at.selectbox(key="tc_c_role")
    _no_hidden_numbers(at)

    _click(at, "tc_c_accept")                                              # talebi oldugu gibi kabul et
    status, years = _query(lambda db: (_talk(db, pid).status, _player(db, pid).contract_years))
    assert status == "SIGNED" and years >= 2, (status, years)
    assert any("SÖZLEŞME TAMAM" in s.value for s in at.success)
    frame = _frame(at, "lk_contracts")
    assert name not in frame["Oyuncu"].tolist() or frame[frame["Oyuncu"] == name]["Durum"].tolist() != \
        ["Sözleşmesi bitiyor"]


# ---------------------------------------------------------------------------
# 2) Fesih: tazminat onaydan once
# ---------------------------------------------------------------------------

def test_termination_shows_the_compensation_before_the_confirm_button():
    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    (pid, name), = _expiring(1)
    at = _app(page="transfer")
    _open_section(at)
    at.selectbox(key="tc_c_pick").set_value(pid)
    at.run()
    facts = _facts(at)
    assert "Tazminat" in facts and "Kalan sözleşme" in facts
    assert at.button(key="tc_c_term").disabled                             # onay kutusu isaretsiz: pasif
    budget_before = _query(lambda db: _team(db, USER).transfer_budget)
    at.checkbox(key="tc_c_term_ok").set_value(True)
    at.run()
    assert not at.button(key="tc_c_term").disabled
    _click(at, "tc_c_term")
    team_id, years = _query(lambda db: (_player(db, pid).team_id, _player(db, pid).contract_years))
    assert team_id is None and years == 0
    budget_after = _query(lambda db: _team(db, USER).transfer_budget)
    assert budget_after < budget_before                                    # tazminat kasadan cikti
    assert any("feshedildi" in s.value and name in s.value for s in at.success)
    kind = _query(lambda db: db.execute(__import__("sqlalchemy").select(
        __import__("models").TransferLog.kind).where(
        __import__("models").TransferLog.player_id == pid)).scalars().all())
    assert "TERMINATED" in kind


# ---------------------------------------------------------------------------
# 3) Serbest oyuncu havuzu ve imza
# ---------------------------------------------------------------------------

def test_free_agent_pool_is_fogged_and_can_be_signed_from_the_desk():
    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=5_000_000)
    pid, name = _release_from(OTHER)
    at = _app(page="transfer")
    _open_section(at, TAB_FREE)
    frame = _frame(at, "lk_free_agents")
    row = frame[frame["Oyuncu"] == name]
    assert len(row) == 1 and row["Önceki kulüp"].tolist() == [OTHER]
    assert row["Bilgi"].tolist()[0] != "%100"                              # K12: kendi oyuncum degil
    ability = row["Mevcut yetenek (tahmin)"].tolist()[0]
    assert not any(ch.isdigit() for ch in ability), ability                # sozcuk olcegi / aralik, sayi degil
    at.selectbox(key="tc_c_fa_pick").set_value(pid)
    at.run()
    _click(at, "tc_c_fa_open")
    assert "cm-log" in _html(at)
    _click(at, "tc_c_accept")
    team_id, status = _query(lambda db: (_player(db, pid).team_id, _talk(db, pid).status))
    assert status == "SIGNED" and team_id == _query(lambda db: _team(db, USER).id), (status, team_id)
    wage = _query(lambda db: _player(db, pid).current_wage)
    assert wage > 0
    _no_hidden_numbers(at)


def test_free_agent_can_be_signed_from_the_player_page_and_found_in_bul():
    import nav_view

    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=5_000_000)
    pid, name = _release_from(OTHER)
    at = _app(page="bul")
    at.text_input(key="fd_query").set_value(name[:6])
    at.checkbox(key="fd_free").set_value(True)
    at.run()
    frame = _frame(at, "lk_find_players")
    assert name in frame["Oyuncu"].tolist() and "Kulüpsüz" in frame["Kulüp"].tolist()

    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.PLAYER, pid
    at.run()
    assert f"{name} (Kulüpsüz)" in _html(at)
    _click(at, "pv_act_free")                                              # Eylem > Sözleşme teklif et
    assert at.session_state[nav_view.NAV_KEY] == nav_view.TRANSFER
    assert at.session_state["tc_section"] == SEC and at.session_state["tc_csec"] == TAB_FREE
    status = _query(lambda db: _talk(db, pid).status)
    assert status == "OPEN"
    assert at.number_input(key="tc_c_wage").value > 0                      # masa acildi


# ---------------------------------------------------------------------------
# 4) On sozlesme: donem kapaliyken gerekce
# ---------------------------------------------------------------------------

def test_pre_contract_tab_explains_the_closed_window():
    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    at = _app(page="transfer")
    _open_section(at, TAB_PRE)
    assert any("Ön sözleşme dönemi" in i.value and "haftada açılır" in i.value for i in at.info)
    buttons = [b for b in at.button if b.key == "tc_c_pre_open"]
    assert not buttons or buttons[0].disabled                              # donem kapali: masa acilmaz


# ---------------------------------------------------------------------------
# 5) Gelen Kutusu uyarisi ve Kadro kisayolu
# ---------------------------------------------------------------------------

def test_inbox_warns_about_expiring_contracts_and_links_to_the_desk():
    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    rows = _expiring(2)
    at = _app(page="ana-sayfa")
    titles = [b.label for b in at.button if (b.key or "").startswith("home_msg_")]
    assert any("Sözleşmesi bu sezon bitenler" in t for t in titles)
    index = next(i for i, t in enumerate(titles) if "Sözleşmesi bu sezon bitenler" in t)
    _click(at, f"home_msg_{index}")
    body = _texts(at.markdown)
    assert all(name in body for _pid, name in rows)
    assert at.button(key="home_go").label == "Sözleşmeler"
    _click(at, "home_go")
    assert at.session_state["nav_page"] == "transfer" and at.session_state["tc_section"] == SEC
    assert at.radio(key="tc_csec").value == "Kadrom"


def test_squad_contract_view_shows_the_talk_column_and_the_renewal_shortcut():
    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    (pid, name), = _expiring(1)
    at = _app(page="transfer")
    _open_section(at)
    at.selectbox(key="tc_c_pick").set_value(pid)
    at.run()
    _click(at, "tc_c_open")                                                # gorusme baslasin
    goto(at, "kadro")
    at.segmented_control(key="sq_view").set_value("Sözleşme")
    at.run()
    frame = _frame(at, "sq_table")
    assert "Görüşme" in frame.columns and "Bitiş (sezon)" in frame.columns
    assert frame[frame["Oyuncu"] == name]["Görüşme"].tolist() == ["Sözleşme yenileme: Görüşülüyor"]
    _click(at, "sq_contracts")
    assert at.session_state["nav_page"] == "transfer" and at.session_state["tc_section"] == SEC


# ---------------------------------------------------------------------------
# 6) Oyuncu profili: Sözleşme sekmesi
# ---------------------------------------------------------------------------

def test_player_profile_contract_tab_shows_expiry_talk_state_and_actions():
    import nav_view

    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    (pid, _name), = _expiring(1)
    at = _app(page="kadro")
    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.PLAYER, pid
    at.run()
    at.segmented_control(key="pv_section").set_value("Sözleşme")
    at.run()
    html = _html(at)                                                       # profil bilgi paneli: cm-pairs
    assert "cm-pairs" in html and "Bitiş (sezon)" in html
    assert any("bu sezon sonunda bitiyor" in w.value for w in at.warning)
    assert at.button(key="pv_act_contract") and at.button(key="pv_act_terminate")
    _click(at, "pv_act_contract")                                          # Eylem > Sözleşmeyi yenile
    assert at.session_state[nav_view.NAV_KEY] == nav_view.TRANSFER
    assert at.session_state["tc_section"] == SEC and at.session_state["tc_c_pick"] == pid
    assert _query(lambda db: _talk(db, pid).status) == "OPEN"
    at.session_state[nav_view.NAV_KEY], at.session_state[nav_view.PARAM_KEY] = nav_view.PLAYER, pid
    at.run()
    at.segmented_control(key="pv_section").set_value("Sözleşme")
    at.run()
    assert any("Sözleşme yenileme: Görüşülüyor" in i.value for i in at.info)
    assert at.button(key="pv_talk_open")


# ---------------------------------------------------------------------------
# 7) Bayrak kapali: bolum hic yok
# ---------------------------------------------------------------------------

def test_section_is_hidden_when_the_contract_cycle_flag_is_off(monkeypatch):
    import contracts

    _set_user_team(USER, transfer_budget=60_000_000, wage_budget=3_000_000)
    _expiring(1)
    monkeypatch.setattr(contracts, "CONTRACT_CYCLE", False)
    at = _app(page="transfer")
    assert SEC not in at.radio(key="tc_section").options
    at = _app(page="ana-sayfa")
    titles = [b.label for b in at.button if (b.key or "").startswith("home_msg_")]
    assert not any("Sözleşmesi bu sezon bitenler" in t for t in titles)
