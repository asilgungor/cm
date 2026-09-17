"""
Faz 12 / 14. Asama C3: Milli Takim sekmesi (national_view) -- Streamlit AppTest (basliksiz) + callback birim testleri.

Gercek web_app.py betigi calisir. Sentetik 'public' dunyasi tests/world_helpers.make_shared_public ile milli takimlari
ACIK paylasilan dunyaya cevrilir (sahip Istanbul Lions, uye Madrid Blancos; insan pazari kapali). Ilk AppTest cizimi
(accounts.ensure_career_ready -> ensure_world_setup) 6 milli takimi, kadrolari, elemeleri ve is tekliflerini kurar.
Mac gunleri arka uctan (national_teams) oynatilir, sonuc arayuzde dogrulanir. Kisisel dunya testi eski oturumla
(AuthSession(0, 'test_menajer', 'public')) sezon arasi milli mac gunlerini sekmeden oynatir.

Kendi veritabaninda calistirin (dunyayi yeniden seed eder; modul sonunda conftest'in temiz dunyasi geri kurulur):
    TEST_DB_NAME=fm_db_test_c3 python -m pytest -q -p no:cacheprovider tests/test_web_national.py
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _reseed,
    _set_user_team,
)
from tests.world_helpers import (  # noqa: E402
    app_as,
    cleanup_shared,
    cleanup_users,
    make_shared_public,
    world_auth,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

PREFIX = "c3"
OWNER, MEMBER = "c3sahip", "c3uye"
OWNER_TEAM, MEMBER_TEAM = "Istanbul Lions", "Madrid Blancos"
SEED = 2112


# ---------------------------------------------------------------------------
# Kurulum
# ---------------------------------------------------------------------------

def _intl_rules(**changes):
    from world_rules import WorldRules

    return WorldRules.shared_defaults().with_changes(
        {"internationals": True, "human_market": False, "loans": False, **changes})


@pytest.fixture(scope="module", autouse=True)
def restore_default_world():
    cleanup_users(PREFIX)
    yield
    cleanup_users(PREFIX)
    cleanup_shared(None, reseed=False)
    _reseed(mode=None)                    # conftest ile ayni temiz dunya (mod secilmemis): sonraki moduller icin


@pytest.fixture
def world():
    cleanup_users(PREFIX)
    _reseed()
    shared = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM)], owner_team=OWNER_TEAM, rules=_intl_rules())
    try:
        yield shared
    finally:
        cleanup_shared(shared, reseed=False)


@contextmanager
def _callback_session(monkeypatch, world, username: str):
    """Callback'leri AppTest disinda cagirmak icin duz sozluk session_state (menajerin paylasilan dunya oturumu)."""
    import web_common

    uid = world.owner_id if username == OWNER else world.user_ids[username]
    state: dict = {"auth": world_auth(uid, username, "public", world_id=world.world_id, world_kind="SHARED")}
    with monkeypatch.context() as patch:
        patch.setattr(web_common.st, "session_state", state)
        yield state


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


def _member(world):
    return app_as(world.user_ids[MEMBER], MEMBER, world.world_id)


def _owner(world):
    return app_as(world.owner_id, OWNER, world.world_id)


def _national(world, username: str | None, work):
    """Arka uc kisayolu: menajerin NationalTeams'i (kurulum idempotent) ile islem, commit. username None: eski oturum."""
    import database
    from career_manager import CareerManager
    from national_teams import NationalTeams

    with database.career_context("public"), database.session_scope() as db:
        if username is None:
            cm = CareerManager(db)
        else:
            uid = world.owner_id if username == OWNER else world.user_ids[username]
            cm = CareerManager(db, seed=SEED, manager_user_id=uid)
        nt = NationalTeams(cm)
        nt.ensure_setup()
        return work(nt)


def _offers(world, username: str) -> list[tuple[int, int, str]]:
    """Bekleyen teklifler: (teklif id, ulus id, ulus adi), sekmedeki sirayla."""
    return [tuple(r) for r in _sql(
        "SELECT o.id, n.id, n.name FROM public.national_job_offers o JOIN public.nations n ON n.id = o.nation_id "
        "WHERE o.manager_id = :s AND o.status = 'PENDING' ORDER BY n.reputation DESC, o.id",
        s=world.seat_ids[username])]


def _nation(nation_id: int):
    return _sql("SELECT manager_id, formation, name FROM public.nations WHERE id = :n", n=nation_id)[0]


def _callups(nation_id: int) -> dict[int, tuple[str, str | None]]:
    return {pid: (status, role) for pid, status, role in _sql(
        "SELECT player_id, lineup_status, lineup_role FROM public.national_callups WHERE nation_id = :n AND season = 1",
        n=nation_id)}


def _positions(ids) -> dict[int, str]:
    rows = _sql("SELECT id, position FROM public.players WHERE id = ANY(:ids)", ids=list(ids))
    return {pid: getattr(pos, "value", pos) for pid, pos in rows}


def _finish_club_season() -> None:
    """Lig ve Devler Arenasi bitmis sayilir (milli turnuvalara dokunulmaz); commit."""
    from sqlalchemy import update

    import database
    from career_manager import CareerManager
    from models import Fixture, FixtureStatus, TournamentStatus

    with database.career_context("public"), database.session_scope() as db:
        cm = CareerManager(db)
        db.execute(update(Fixture).where(Fixture.season == cm.season)
                   .values(status=FixtureStatus.PLAYED, home_score=0, away_score=0))
        db.flush()
        db.expire_all()
        cm.tournaments.ensure().status = TournamentStatus.FINISHED
        db.flush()
        assert cm.season_finished


def _play_close_season(nt, days: int | None = None) -> int:
    from career_manager import WeekReport

    played = 0
    while nt.close_season_pending() and (days is None or played < days):
        assert nt.play_close_season_matchday(WeekReport(season=nt.cm.season, week=nt.cm.current_week))
        played += 1
        assert played < 30
    return played


def _tab(at):
    """Milli Takim sekmesinin ogeleri (diger sekmelerin tablolari / metinleri karismasin)."""
    import web_app

    return next(t for t in at.tabs if t.label == web_app.TAB_NATIONAL)


def _html(at) -> str:
    return "\n".join(m.value for m in _tab(at).markdown)


def _frame(at, *columns: str, without: tuple[str, ...] = ()):
    return next(d.value for d in _tab(at).dataframe
                if set(columns) <= set(d.value.columns) and not set(without) & set(d.value.columns))


def _flash_texts(at) -> list[str]:
    tab = _tab(at)
    return [e.value for group in (tab.success, tab.info, tab.warning, tab.error) for e in group]


# ---------------------------------------------------------------------------
# 1) Teklif -> kabul -> kadro cagrisi -> ilk 11 (asistan, elle, sakat oyuncu reddi) -> dizilis
# ---------------------------------------------------------------------------

def test_member_accepts_offer_then_manages_callups_lineup_and_formation(world):
    import national_view as nv
    import web_app

    member = _member(world)
    assert [t.label for t in member.tabs] == web_app.CAREER_TABS + [web_app.TAB_HUB, web_app.TAB_NATIONAL]
    offers = _offers(world, MEMBER)
    assert len(offers) == 3
    keys = _keys(member.button)
    assert {f"nt_{kind}_{oid}" for oid, _n, _name in offers for kind in ("accept", "decline")} <= keys
    assert not {"nt_resign", "nt_lineup_save", "nt_play_matchday"} & keys
    assert member.radio(key="nt_section").options == nv.PUBLIC_SECTIONS
    markdown = _html(member)
    assert all(f"**{name}**" in markdown for _oid, _nid, name in offers)
    assert any("sezonluk sözleşme · 6 hafta daha geçerli" in c.value for c in member.caption)

    declined, accepted = offers[0], offers[1]
    _click(member, f"nt_decline_{declined[0]}")
    assert _sql("SELECT status FROM public.national_job_offers WHERE id = :o", o=declined[0])[0][0] == "DECLINED"
    assert f"nt_decline_{declined[0]}" not in _keys(member.button)
    _click(member, f"nt_accept_{accepted[0]}")
    assert any(accepted[2] in s.value and "menajeri oldun" in s.value for s in member.success)
    nation_id = accepted[1]
    assert _nation(nation_id).manager_id == world.seat_ids[MEMBER]
    keys = _keys(member.button)
    assert not any(k.startswith("nt_accept_") for k in keys)
    assert {"nt_resign", "nt_lineup_save", "nt_auto_lineup", "nt_formation_save"} <= keys
    assert member.radio(key="nt_section").options == nv.JOB_SECTIONS
    assert member.radio(key="nt_section").value == nv.SEC_XI
    assert accepted[2] in _html(member)                                                   # gorev seridi
    assert any("henüz kurulmadı" in i.value for i in member.info)                         # AI kadrosu: ilk 11 yok
    assert member.multiselect(key="nt_xi").value == [] and member.button(key="nt_resign").disabled

    # Kadro: aday tablosu filtreleri, 30 siniri (sunucu), gecerli kayit + uyari
    member.radio(key="nt_section").set_value(nv.SEC_SQUAD)
    _run(member)
    callups = _callups(nation_id)
    assert len(callups) == 23
    picker = member.multiselect(key="nt_callups")
    assert sorted(picker.value) == sorted(callups) and len(picker.options) == 60
    member.selectbox(key="nt_pos").set_value("GK")
    _run(member)
    keepers_table = _frame(member, "Kulüp", "Durum", "Not", without=("Görev",))
    assert len(keepers_table) >= 3 and set(keepers_table["Mv"]) == {"GK"}
    name = keepers_table["Oyuncu"].iloc[-1]
    member.selectbox(key="nt_pos").set_value(nv.POS_ALL)
    member.text_input(key="nt_query").set_value(name.split()[-1].upper())
    _run(member)
    assert name in list(_frame(member, "Kulüp", "Durum", "Not", without=("Görev",))["Oyuncu"])

    candidates = _national(world, MEMBER, lambda nt: [(c.player_id, c.position) for c in nt.candidates()])
    ids = [pid for pid, _pos in candidates]
    member.multiselect(key="nt_callups").set_value(ids[:31])
    _run(member)
    assert any("En fazla 30" in w.value for w in member.warning)
    _click(member, "nt_save_callups")
    assert any("en fazla 30" in e.value for e in member.error)
    assert _callups(nation_id) == callups                                                 # hicbir sey degismedi

    keepers = [pid for pid, pos in candidates if pos == "GK"]
    outfield = [pid for pid, pos in candidates if pos != "GK"]
    chosen = [keepers[0]] + outfield[:24]
    member.multiselect(key="nt_callups").set_value(chosen)
    _click(member, "nt_save_callups")
    assert any("25 oyuncu" in s.value for s in member.success)
    assert any("1 kaleci" in w.value for w in member.warning)
    assert sorted(_callups(nation_id)) == sorted(chosen)
    assert sorted(member.multiselect(key="nt_callups").value) == sorted(chosen)

    # Ilk 11: asistan, taktik tahtasi, elle degisiklik
    member.radio(key="nt_section").set_value(nv.SEC_XI)
    _run(member)
    _click(member, "nt_auto_lineup")
    assert any("Asistan" in s.value for s in member.success)
    callups = _callups(nation_id)
    xi = [pid for pid, (status, _role) in callups.items() if status == "XI"]
    bench = [pid for pid, (status, _role) in callups.items() if status == "BENCH"]
    assert len(xi) == 11 and 0 < len(bench) <= 7
    assert sorted(member.multiselect(key="nt_xi").value) == sorted(xi)
    assert sorted(member.multiselect(key="nt_bench").value) == sorted(bench)
    assert "cm-p-board" in _html(member)

    positions = _positions(callups)
    out_pid = next(pid for pid in xi if positions[pid] != "GK" and callups[pid][1] == positions[pid]
                   and any(positions[b] == positions[pid] for b in bench))
    in_pid = next(b for b in bench if positions[b] == positions[out_pid])
    member.multiselect(key="nt_xi").set_value([pid for pid in xi if pid != out_pid] + [in_pid])
    _run(member)
    assert in_pid not in member.multiselect(key="nt_bench").value                         # ilk 11'e gecen kulubeden duser
    member.multiselect(key="nt_bench").set_value([b for b in bench if b != in_pid] + [out_pid])
    _click(member, "nt_lineup_save")
    assert any("kaydedildi" in s.value for s in member.success), _flash_texts(member)
    after = _callups(nation_id)
    assert after[in_pid] == ("XI", positions[in_pid]) and after[out_pid] == ("BENCH", None)

    # Sakat oyuncu: mevcut ilk 11 denetimi sayfada, kayit reddedilir ve hicbir sey degismez
    _sql("UPDATE public.players SET injured_until_week = 9 WHERE id = :p", p=in_pid)
    _run(member)
    assert any("sakat" in e.value for e in member.error)
    _click(member, "nt_lineup_save")
    assert any("İlk 11 kaydedilmedi" in e.value and "sakat" in e.value for e in member.error)
    assert _callups(nation_id) == after

    # Dizilis
    current = _nation(nation_id).formation
    target = "4-3-3" if current != "4-3-3" else "4-4-2"
    assert member.button(key="nt_formation_save").disabled
    member.selectbox(key="nt_formation").set_value(target)
    _run(member)
    _click(member, "nt_formation_save")
    assert _nation(nation_id).formation == target
    assert member.selectbox(key="nt_formation").value == target
    assert any("dizilişi" in s.value for s in member.success)


# ---------------------------------------------------------------------------
# 2) Fikstur, gruplar, agac ve sampiyon arsivi mac gunlerinden sonra; paylasilan dunyada sezon arasi dugmesi yok
# ---------------------------------------------------------------------------

def test_fixtures_groups_bracket_and_champions_render_after_matchdays(world):
    import national_view as nv
    from career_manager import WeekReport
    from national_teams import NationalExtension

    nation_id, nation_name = _national(world, MEMBER, lambda nt: (lambda v: (v.id, v.name))(
        nt.accept_job(nt.job_offers()[0].id)))
    _national(world, OWNER, lambda nt: NationalExtension(nt.cm).on_week(5, WeekReport(season=1, week=5)))

    member = _member(world)
    member.radio(key="nt_section").set_value(nv.SEC_FIXTURES)
    _run(member)
    frames = [d.value for d in _tab(member).dataframe if {"Aşama", "Penaltı"} <= set(d.value.columns)]
    assert len(frames) == 2
    qualifier, world_cup = frames
    assert len(qualifier) == 12 and "–" not in set(qualifier["Skor"]) and "2. hafta" in set(qualifier["Zaman"])
    assert len(world_cup) == 6 and set(world_cup["Skor"]) == {"–"} and "Sezon arası 1. gün" in set(world_cup["Zaman"])
    assert "12/12 maç oynandı" in _html(member)
    member.toggle(key="nt_fx_mine").set_value(True)
    _run(member)
    mine = [d.value for d in _tab(member).dataframe if {"Aşama", "Penaltı"} <= set(d.value.columns)]
    assert mine and all(nation_name in (row["Ev sahibi"], row["Deplasman"]) for f in mine for _i, row in f.iterrows())
    assert len(mine[0]) == 4                                                               # 3'lu grupta 4 eleme maci

    member.radio(key="nt_section").set_value(nv.SEC_GROUPS)
    _run(member)
    html = _html(member)
    assert html.count('class="cm-b-group"') == 3                                          # 2 eleme grubu + Dunya Kupasi
    assert "Dünya Kupası Elemeleri" in html and "Grup A" in html and nation_name in html and "cm-b-hl" in html

    member.radio(key="nt_section").set_value(nv.SEC_WORLD_CUP)
    _run(member)
    html = _html(member)
    assert "cm-b-bracket" in html and "Final" in html
    assert any("Henüz Dünya Kupası şampiyonu yok" in c.value for c in _tab(member).caption)

    _finish_club_season()
    _run(member)
    assert "nt_play_matchday" not in _keys(member.button)                                 # tur motoru oynatir
    assert any("Dünya Kupası sürüyor" in i.value and "dünya ilerledikçe" in i.value for i in member.info)

    assert _national(world, OWNER, _play_close_season) == 4
    champion = _national(world, OWNER, lambda nt: nt.champion_name())
    _run(member)
    assert any(champion in s.value and "şampiyonu" in s.value for s in member.success)
    assert "cm-b-champ" in _html(member)
    champions = _frame(member, "Sezon", "Şampiyon")
    assert list(champions["Sezon"]) == [1] and list(champions["Şampiyon"]) == [champion]
    assert any(champion in c.value for c in _tab(member).caption)                         # world_cup_status

    member.radio(key="nt_section").set_value(nv.SEC_NATIONS)
    _run(member)
    nations = _frame(member, "Milli takım", "Menajer", "İtibar")
    assert len(nations) == 6 and MEMBER in set(nations["Menajer"]) and list(nations["Sıra"]) == [1, 2, 3, 4, 5, 6]
    assert f"⭐ {nation_name}" in set(nations["Milli takım"])


# ---------------------------------------------------------------------------
# 3) Sunucu tarafi yetki: baska menajerin ulusu yonetilemez, uyruk ve secim denetimi; istifa akisi
# ---------------------------------------------------------------------------

def test_other_managers_cannot_manage_a_nation_and_resign_flow(world, monkeypatch):
    import national_view as nv
    from web_common import md_escape

    view = _national(world, OWNER, lambda nt: nt.accept_job(nt.job_offers()[0].id))
    nation_id = view.id
    owner_seat = world.seat_ids[OWNER]
    before = _callups(nation_id)
    formation = _nation(nation_id).formation
    assert nation_id not in {nid for _oid, nid, _name in _offers(world, MEMBER)}             # diger teklif geri cekildi
    owner_offer = _sql("SELECT id FROM public.national_job_offers WHERE manager_id = :s AND status = 'WITHDRAWN' "
                       "ORDER BY id LIMIT 1", s=owner_seat)[0][0]

    member = _member(world)
    assert member.radio(key="nt_section").options == nv.PUBLIC_SECTIONS
    assert not {"nt_lineup_save", "nt_save_callups", "nt_resign", "nt_auto_lineup"} & _keys(member.button)

    no_job = [("error", md_escape("Milli takım görevin yok."))]
    with _callback_session(monkeypatch, world, MEMBER) as state:
        calls = [
            ("nt_callups", list(before), nv.cb_nt_save_callups, ()),
            ("nt_xi", list(before)[:11], nv.cb_nt_lineup_save, ()),
            ("nt_formation", "3-5-2", nv.cb_nt_formation_save, ()),
            ("nt_resign_ok", True, nv.cb_nt_resign, ()),
            (None, None, nv.cb_nt_auto_lineup, ()),
        ]
        for key, value, callback, args in calls:
            state.pop("flash", None)
            if key is not None:
                state[key] = value
            assert callback(*args) is None
            assert state["flash"]["national"] == no_job, callback.__name__
        state.pop("flash", None)
        nv.cb_nt_accept(owner_offer)                                                        # baskasinin teklifi
        assert state["flash"]["national"][0][0] == "error" and "bulunamad" in state["flash"]["national"][0][1]
    assert _callups(nation_id) == before
    assert _nation(nation_id)[:2] == (owner_seat, formation)
    assert _sql("SELECT status FROM public.national_job_offers WHERE id = :o", o=owner_offer)[0][0] == "WITHDRAWN"

    foreign = _sql("SELECT player_id FROM public.national_callups WHERE nation_id <> :n AND season = 1 "
                   "ORDER BY player_id LIMIT 1", n=nation_id)[0][0]
    with _callback_session(monkeypatch, world, OWNER) as state:
        state["nt_callups"] = list(before)[:20] + [foreign]
        nv.cb_nt_save_callups()
        assert state["flash"]["national"][0][0] == "error" and "oyuncusu olmayan" in state["flash"]["national"][0][1]
        state.pop("flash")
        state["nt_callups"] = ["1; DROP TABLE nations"]
        nv.cb_nt_save_callups()
        assert state["flash"]["national"] == [("error", md_escape(nv.BAD_SELECTION_TEXT))]
        state.pop("flash")
        nv.cb_nt_play_matchday()                                                            # paylasilan dunya: tur motoru
        assert state["flash"]["national"] == [("error", nv.SHARED_MATCHDAY_TEXT)]
        state.pop("flash")
        nv.cb_nt_resign()                                                                   # onay yok
        assert state["flash"]["national"] == [("error", nv.RESIGN_CONFIRM_TEXT)]
    assert _callups(nation_id) == before and _nation(nation_id).manager_id == owner_seat

    owner = _owner(world)
    assert owner.radio(key="nt_section").options == nv.JOB_SECTIONS
    assert owner.button(key="nt_resign").disabled
    owner.checkbox(key="nt_resign_ok").check()
    _run(owner)
    assert not owner.button(key="nt_resign").disabled
    _click(owner, "nt_resign")
    assert _nation(nation_id).manager_id is None
    assert _sql("SELECT nation_id FROM public.world_managers WHERE id = :s", s=owner_seat)[0][0] is None
    assert any("istifa ettin" in i.value and view.name in i.value for i in owner.info)
    assert owner.radio(key="nt_section").options == nv.PUBLIC_SECTIONS
    assert "nt_resign" not in _keys(owner.button)


# ---------------------------------------------------------------------------
# 4) Kacis: HTML / Markdown iceren ulus ve oyuncu adlari duz metin
# ---------------------------------------------------------------------------

def test_nation_and_player_names_with_markup_are_escaped(world, monkeypatch):
    import database
    import national_view as nv
    from models import NationalJobOffer

    view = _national(world, MEMBER, lambda nt: nt.accept_job(nt.job_offers()[0].id))
    _national(world, MEMBER, lambda nt: nt.auto_lineup())
    member = _member(world)                               # kurulum bu oturumda yapildi: ad degisikligi yeni ulus kurmaz
    evil_nation = "<img src=x onerror=alert(1)> Ulus"
    evil_player = "<script>alert('x')</script> **Kalın**"
    starter = next(pid for pid, (status, _role) in _callups(view.id).items() if status == "XI")
    _sql("UPDATE public.nations SET name = :n WHERE id = :i", n=evil_nation, i=view.id)
    _sql("UPDATE public.players SET name = :n WHERE id = :p", n=evil_player, p=starter)

    shown: list[str] = []
    for section in (nv.SEC_XI, nv.SEC_SQUAD, nv.SEC_GROUPS, nv.SEC_NATIONS):
        member.radio(key="nt_section").set_value(section)
        _run(member)
        tab = _tab(member)
        shown += [m.value for m in tab.markdown] + [c.value for c in tab.caption] + _flash_texts(member)
    risky = [t for t in shown if "alert" in t]
    assert risky and all("<img" not in t and "<script" not in t for t in risky)
    assert any("&lt;img src=x" in t for t in risky)                                         # serit, grup tablosu
    assert any("&lt;script&gt;" in t for t in risky)                                        # taktik tahtasi ipucu
    member.radio(key="nt_section").set_value(nv.SEC_XI)
    _run(member)
    assert evil_player in list(_frame(member, "Kulüp", "Durum", "Görev")["Oyuncu"])        # tablo duz metin

    seat = world.seat_ids[MEMBER]
    other = _sql("SELECT id FROM public.nations WHERE id NOT IN (SELECT nation_id FROM public.national_job_offers "
                 "WHERE manager_id = :s) ORDER BY id LIMIT 1", s=seat)[0][0]
    with database.session_scope() as db:
        offer = NationalJobOffer(nation_id=other, manager_id=seat, season=1, status="PENDING", expires_career_week=20)
        db.add(offer)
        db.flush()
        offer_id = offer.id
    with _callback_session(monkeypatch, world, MEMBER) as state:
        nv.cb_nt_accept(offer_id)                                                           # zaten gorevi var
        [(kind, message)] = state["flash"]["national"]
    assert kind == "error" and "menajerisin" in message
    assert "<img" not in message and "&lt;img src=x onerror=alert\\(1\\)&gt;" in message


# ---------------------------------------------------------------------------
# 5) Kisisel dunya: sezon arasi milli mac gunleri sekmeden; yeni sezon engeli ve nedeni
# ---------------------------------------------------------------------------

def test_personal_world_plays_close_season_matchdays_from_the_tab():
    import database
    import web_app
    from career_manager import CareerManager
    from world_rules import WorldRules

    cleanup_users(PREFIX)
    _reseed()
    _set_user_team(OWNER_TEAM)
    with database.session_scope() as db:
        CareerManager(db).state.world_rules = WorldRules(internationals=True).to_dict()

    at = _app()
    assert [t.label for t in at.tabs] == web_app.CAREER_TABS + [web_app.TAB_NATIONAL]
    assert "nt_play_matchday" not in _keys(at.button)
    assert _sql("SELECT count(*) FROM public.nations")[0][0] == 6                        # ilk giriste kuruldu

    _finish_club_season()
    _run(at)
    assert "nt_play_matchday" in _keys(at.button)
    assert any("Dünya Kupası sürüyor" in w.value and "kalan 10 maç günü" in w.value for w in at.warning)
    assert any("Milli Takım" in w.value and "sekmesinden oyna" in w.value for w in at.warning)   # Lig sekmesi nedeni
    _click(at, "lg_new_season")
    assert any("Dünya Kupası sürüyor" in e.value for e in at.error)
    assert _sql("SELECT season FROM public.game_state WHERE id = 1")[0][0] == 1

    assert _national(None, None, lambda nt: _play_close_season(nt, days=8)) == 8          # elemeler + 2 grup gunu
    _click(at, "nt_play_matchday")
    assert any("Milli maç günü oynandı" in s.value for s in at.success)
    assert any("Dünya Kupası" in i.value for i in at.info)                                 # mac gunu notlari
    assert "nt_play_matchday" in _keys(at.button)
    _click(at, "nt_play_matchday")                                                         # final
    assert "nt_play_matchday" not in _keys(at.button)
    assert any("milli maçlar tamamlandı" in s.value and "şampiyonu" in s.value for s in at.success)
    status, champion = _sql("SELECT status, champion_nation_id FROM public.international_tournaments "
                            "WHERE season = 1 AND kind = 'WORLD_CUP'")[0]
    assert status == "FINISHED" and champion is not None
    assert not any("Dünya Kupası sürüyor" in w.value for w in at.warning)

    _click(at, "lg_new_season")
    assert _sql("SELECT season FROM public.game_state WHERE id = 1")[0][0] == 2
