"""
Faz 12 / 14. Asama A4: paylasilan dunya web arayuzu -- Streamlit AppTest (basliksiz) + callback birim testleri.

Gercek web_app.py betigi calisir. Sentetik 'public' dunyasi tests/world_helpers.make_shared_public ile paylasilan
dunyaya cevrilir; IKI AppTest oturumu ayni dunyada iki menajer olarak islem yapar (hazir -> hafta ilerler, yonetici
atar -> atilanin oturumu lobiye duser). Lobi testleri gercek kayit islemlerini (dunya kur, davet koduyla / acik
listeden katil, cevir, ayril) calistirir; kurulan dunya semalari test sonunda silinir.

Kendi veritabaninda calistirin (dunya kurar / yeniden seed eder):
    TEST_DB_NAME=fm_db_test_a4 python -m pytest -q -p no:cacheprovider tests/test_web_worlds.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto, menu  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _click,
    _db_available,
    _query,
    _reseed,
    _set_user_team,
    _texts,
)
from tests.world_helpers import (  # noqa: E402
    add_user,
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

PREFIX = "a4"
OWNER, MEMBER, SPARE = "a4sahip", "a4uye", "a4bos"
OWNER_TEAM, MEMBER_TEAM = "Istanbul Lions", "Kadıköy Canaries"
PASSWORD = "Gizli1234"


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
def shared():
    world = make_shared_public(OWNER, [(MEMBER, MEMBER_TEAM), (SPARE, None)], owner_team=OWNER_TEAM)
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


def _shared_pages(role: str) -> list[str]:
    """Faz 13I: paylasilan dunyada kulubu olan koltugun menusu (Teklifler & Mesajlar; yonetim yalnizca sahip/yonetici)."""
    import nav_view

    return nav_view.pages_for(tournament=False, shared=True, internationals=False, role=role)


def _owner(world, **kwargs):
    return app_as(world.owner_id, OWNER, world.world_id, **kwargs)


def _member(world, **kwargs):
    return app_as(world.user_ids[MEMBER], MEMBER, world.world_id, **kwargs)


def _run(at):
    at.run()
    assert not at.exception, at.exception
    return at


def _keys(elements) -> set[str]:
    return {e.key for e in elements if e.key}


def _club_buttons(at, prefix: str = "co") -> dict[str, tuple[str, bool]]:
    """Faz 13G: ulke -> lig -> kulup seciciyi tum ulkeler icin gezer: {dugme anahtari: (ulke, devre disi mi)}."""
    found: dict[str, tuple[str, bool]] = {}
    for option in list(at.button_group(key=f"{prefix}_country").options):
        country = option.rsplit(" · ", 1)[0].split()[-1]                 # bayrak emojisi ikona gider
        at.button_group(key=f"{prefix}_country").set_value(country)
        _run(at)
        found.update({b.key: (country, b.disabled) for b in at.button if (b.key or "").startswith(f"{prefix}_claim_")})
    return found


def _claim(at, key: str, country: str):
    at.button_group(key="co_country").set_value(country)
    _run(at)
    return _click(at, key)


def _state_week() -> tuple[int, int]:
    from models import GameState

    return _query(lambda db: (lambda s: (s.season, s.current_week))(db.get(GameState, 1)))


def _sql(statement: str, **params):
    import database

    with database.engine.begin() as conn:
        result = conn.execute(text(statement), params)
        return result.all() if result.returns_rows else []


class _LockHolder:
    """Ayri ham baglantida dunya tur kilidi ('public'): hafta ilerlemesi (exclusive) ya da baska callback (shared)."""

    def __init__(self, function: str) -> None:
        self.function = function
        self.started, self.released = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        import database

        with database.engine.connect() as conn:
            conn.execute(text(f"SELECT {self.function}(:ns, hashtext('public'))"), {"ns": database.LOCK_WORLD_TURN})
            self.started.set()
            self.released.wait(60)
            conn.rollback()

    def __enter__(self):
        self.thread.start()
        assert self.started.wait(10)
        return self

    def __exit__(self, *exc):
        self.released.set()
        self.thread.join(10)


# ---------------------------------------------------------------------------
# Kisisel kariyer: oturum baglama, bugunku ekran, lobi, paylasilan dunyaya cevirme
# ---------------------------------------------------------------------------

def test_login_binds_personal_world_keeps_todays_tabs_and_lobby_converts_it():
    import web_app
    import world_lobby_view as lobby
    from tests.test_web_app import _app

    at = _app(login=False)
    _click(at, "auth_to_register")
    at.text_input(key="reg_user").set_value("a4kayit")
    at.text_input(key="reg_pass").set_value(PASSWORD)
    at.text_input(key="reg_pass2").set_value(PASSWORD)
    _click(at, "reg_btn")
    auth = at.session_state["auth"]
    assert (auth.career_schema, auth.world_kind) == ("public", "PERSONAL") and auth.world_id is not None
    assert not at.tabs and "cp_country" in _keys(at.button_group)            # Faz 13G: once kulup (ulke -> lig)
    assert any("Hoş geldin, a4kayit" in s.value for s in at.sidebar.success)
    assert "sb_team" not in _keys(at.selectbox) and "sb_worlds" in _keys(at.button)
    assert not {"wp_ready", "wp_force", "lg_ready"} & _keys(at.button)

    _click(at, "sb_worlds")                                                   # lobi: dunya cizilmez
    assert not at.tabs and at.radio(key="lobby_section").value == lobby.SEC_MINE
    world_id = auth.world_id
    assert f"lobby_enter_{world_id}" in _keys(at.button) and f"lobby_leave_{world_id}" not in _keys(at.button)
    _click(at, "lobby_back")
    assert not at.tabs and "cp_country" in _keys(at.button_group)

    _click(at, "sb_worlds")
    at.text_input(key=f"lconv_name_{world_id}").set_value("A4 Arkadaş Ligi")
    at.selectbox(key=f"lconv_visibility_{world_id}").set_value("INVITE")
    _click(at, f"lobby_convert_{world_id}")
    auth = at.session_state["auth"]
    assert (auth.world_id, auth.world_kind, auth.career_schema) == (world_id, "SHARED", "public")
    assert [t.label for t in at.tabs] == [web_app.TAB_CLUBS, web_app.TAB_HUB, web_app.TAB_ADMIN]   # once kulup sec
    row = _sql("SELECT kind, name, invite_code FROM accounts.worlds WHERE id = :w", w=world_id)[0]
    assert (row.kind, row.name) == ("SHARED", "A4 Arkadaş Ligi") and row.invite_code
    assert any(row.invite_code in s.value for s in at.sidebar.success)


def test_legacy_session_keeps_todays_ui_and_never_draws_a_shared_world_as_primary(shared):
    """Dunyasiz sahte oturum (user 0) bugunku ekrani cizer; paylasilan dunyaya bakiyorsa lobiye duser."""
    from tests.test_web_app import _app

    at = _app()
    assert not at.tabs and at.session_state["world_lobby"] is True               # sahibin kulubu cizilmez
    assert any("paylaşılan bir dünya" in e.value for e in at.error)
    assert not {"tac_auto", "lg_play", "wp_ready"} & _keys(at.button)

    cleanup_shared(shared)                                                        # eski kariyer: bugunku ekran
    import web_app

    at = _app()
    assert "cp_country" in _keys(at.button_group) and "sb_worlds" in _keys(at.button)   # kulupsuz: once kulup
    assert "world_ctx" not in at.session_state and web_app.TAB_CLUBS not in [t.label for t in at.tabs]


# ---------------------------------------------------------------------------
# Lobi: dunya kur, davet koduyla / acik listeden katil, kulup sec, ayril
# ---------------------------------------------------------------------------

def test_lobby_create_world_then_owner_claims_a_club(monkeypatch):
    import web_app
    import world_lobby_view as lobby
    import worlds

    create = worlds.create_world
    monkeypatch.setattr(worlds, "create_world", lambda *a, **k: create(*a, **{**k, "source": "synthetic"}))
    uid = add_user("a4kurucu")
    at = app_as(uid, "a4kurucu", None, world_kind=None, lobby=True)
    assert not at.exception and not at.tabs
    at.radio(key="lobby_section").set_value(lobby.SEC_CREATE)
    _run(at)
    at.text_input(key="wc_name").set_value("A4 <b>Kupa</b>")                      # gecersiz ad: Turkce hata
    _click(at, "wc_create")
    assert any("Dünya adı" in e.value for e in at.error) and at.session_state["auth"].world_id is None

    at.text_input(key="wc_name").set_value("A4 Kupa Dünyası")
    at.number_input(key="wc_max").set_value(4)
    at.toggle(key="wc_auto_advance").set_value(False)
    at.text_input(key="wc_seed").set_value("77")
    _click(at, "wc_create")
    auth = at.session_state["auth"]
    assert auth.world_kind == "SHARED" and auth.career_schema.startswith("world_") and "world_lobby" not in at.session_state
    assert [t.label for t in at.tabs] == [web_app.TAB_CLUBS, web_app.TAB_HUB, web_app.TAB_ADMIN]
    row = _sql("SELECT kind, max_managers, world_seed, owner_user_id FROM accounts.worlds WHERE id = :w",
               w=auth.world_id)[0]
    assert tuple(row) == ("SHARED", 4, 77, uid)
    assert at.button(key="wp_ready").disabled                                     # kulupsuz: hazir olamaz

    key, (country, _disabled) = next(item for item in _club_buttons(at).items() if not item[1][1])
    team_id = int(key.rsplit("_", 1)[1])
    _claim(at, key, country)
    assert not at.tabs and menu(at) == _shared_pages("OWNER")                      # Faz 13I: kulup secilince menu
    team = _sql(f'SELECT user_team_id FROM "{auth.career_schema}".game_state WHERE id = 1')[0].user_team_id
    assert team == team_id and not at.button(key="wp_ready").disabled
    assert any("artık senin" in s.value for s in at.sidebar.success)


def test_lobby_world_type_radio_passes_the_source_to_create_world(monkeypatch):
    """14C: 'Dünya türü' radyosu (wc_source): varsayilan gercek kulupler (open), 'Hızlı kurgusal' -> synthetic."""
    import world_lobby_view as lobby
    import worlds

    seen: list[dict] = []

    def capture(*_args, **kwargs):
        seen.append(kwargs)
        raise worlds.WorldError("yakalandı")                        # dunya kurulmaz; kaynak yeter

    monkeypatch.setattr(worlds, "create_world", capture)
    uid = add_user("a4kaynak")
    at = app_as(uid, "a4kaynak", None, world_kind=None, lobby=True)
    at.radio(key="lobby_section").set_value(lobby.SEC_CREATE)
    _run(at)
    radio = at.radio(key="wc_source")
    assert list(radio.options) == [lobby.WORLD_SOURCE_LABELS["open"], lobby.WORLD_SOURCE_LABELS["synthetic"]]
    assert radio.value == "open" and "114 kulüp" in radio.options[0] and "4 kulüp" in radio.options[1]

    at.text_input(key="wc_name").set_value("A4 Kaynak")
    _click(at, "wc_create")
    radio = at.radio(key="wc_source")
    radio.set_value("synthetic")
    _click(at, "wc_create")
    assert [call["source"] for call in seen] == ["open", "synthetic"]
    assert any("yakalandı" in e.value for e in at.error)
    assert lobby.world_source_choice("fm") == lobby.world_source_choice(None) == "open"


def test_join_by_code_club_offers_by_level_public_join_and_leave(shared):
    import web_app
    import web_common
    import world_lobby_view as lobby
    import worlds

    guest = add_user("a4misafir")
    at = app_as(guest, "a4misafir", None, world_kind=None, lobby=True)
    at.radio(key="lobby_section").set_value(lobby.SEC_CODE)
    _run(at)
    at.text_input(key="wj_code").set_value("YANLIS99")
    _click(at, "wj_join")
    assert [e.value for e in at.error] == [worlds.INVALID_CODE] and at.session_state["auth"].world_id is None

    at.text_input(key="wj_code").set_value("testkod-123")                        # buyuk/kucuk harf ve tire onemsiz
    _click(at, "wj_join")
    auth = at.session_state["auth"]
    assert (auth.world_id, auth.world_kind) == (shared.world_id, "SHARED")
    assert [t.label for t in at.tabs] == [web_app.TAB_CLUBS, web_app.TAB_HUB]     # uye: yonetim sekmesi yok

    eligible = _club_buttons(at)
    assert eligible and not any(disabled for _country, disabled in eligible.values())
    at.toggle(key="co_only_eligible").set_value(False)
    _run(at)
    everything = _club_buttons(at)
    blocked = [k for k, (_country, disabled) in everything.items() if disabled]
    assert blocked and set(eligible) < set(everything)
    assert all(not everything[k][1] for k in eligible)
    at.button_group(key="co_country").set_value(everything[blocked[0]][0])      # kilitli kartin ulkesi
    _run(at)
    assert at.button(key=blocked[0]).disabled and "seviye menajer olmalısın" in at.button(key=blocked[0]).help
    assert any("seviye menajer olmalısın" in str(m.value) for m in at.markdown)   # kartta kilit nedeni
    humans = {row[0] for row in _sql("SELECT team_id FROM public.world_managers WHERE team_id IS NOT NULL "
                                     "UNION SELECT user_team_id FROM public.game_state")}
    assert len(humans) == 2 and not {f"co_claim_{i}" for i in humans} & set(everything)   # insan kulupleri yok

    key = sorted(eligible)[0]
    _claim(at, key, eligible[key][0])
    seat = _sql("SELECT team_id, status FROM public.world_managers WHERE user_id = :u", u=guest)[0]
    assert (seat.team_id, seat.status) == (int(key.rsplit("_", 1)[1]), "ACTIVE")
    assert not at.tabs and menu(at) == _shared_pages("MEMBER")

    # Acik dunyalar listesinden katilim
    _sql("UPDATE accounts.worlds SET visibility = 'PUBLIC' WHERE id = :w", w=shared.world_id)
    walker = add_user("a4gezgin")
    other = app_as(walker, "a4gezgin", None, world_kind=None, lobby=True)
    other.radio(key="lobby_section").set_value(lobby.SEC_PUBLIC)
    _run(other)
    _click(other, f"wb_join_{shared.world_id}")
    assert other.session_state["auth"].world_id == shared.world_id and other.tabs

    # Ayrilma: kulup AI'ya gecer, oturum lobide kalir (uyeliksiz dunya cizilmez)
    _click(at, "sb_worlds")
    assert at.button(key=f"lobby_leave_{shared.world_id}").disabled                # once onay
    at.checkbox(key=f"lobby_leave_ok_{shared.world_id}").check()
    _run(at)
    _click(at, f"lobby_leave_{shared.world_id}")
    member = _sql("SELECT status FROM accounts.world_memberships WHERE world_id = :w AND user_id = :u",
                  w=shared.world_id, u=guest)[0]
    seat = _sql("SELECT team_id, status FROM public.world_managers WHERE user_id = :u", u=guest)[0]
    assert member.status == "LEFT" and (seat.team_id, seat.status) == (None, "LEFT")
    assert at.session_state[web_common.LOBBY_KEY] is True and not at.tabs
    assert f"lobby_enter_{shared.world_id}" not in _keys(at.button)
    at.session_state[web_common.LOBBY_KEY] = False                               # zorla donmeye calissa da
    _run(at)
    assert not at.tabs and at.session_state[web_common.LOBBY_KEY] is True


# ---------------------------------------------------------------------------
# Iki oturum: hazir kontrolu, hafta ilerlemesi, her menajerin kendi raporu
# ---------------------------------------------------------------------------

def test_two_sessions_ready_check_advances_the_week_and_each_sees_own_report(shared):
    from models import ManagerWeekReport

    _sql("UPDATE public.game_state SET manager_reputation = 12.0 WHERE id = 1")   # sahip 12, uye 8 tanınırlık
    owner, member = _owner(shared, page="fikstur"), _member(shared, page="fikstur")   # rapor: Fikstur & Sonuclar
    assert not owner.exception and not member.exception
    assert any("0/2 hazır" in c.value for c in member.sidebar.caption)            # kulupsuz koltuk sayilmaz

    _click(owner, "wp_ready")
    assert _sql("SELECT ready_career_week FROM public.world_managers WHERE is_primary")[0][0] == 1
    assert owner.button(key="wp_ready").label.startswith("↩️") and _state_week() == (1, 1)

    _run(member)
    captions = _texts(member.sidebar.caption)
    assert "1/2 hazır" in captions and f"Bekleniyor: {MEMBER}" in captions

    _click(member, "wp_ready")
    assert _state_week() == (1, 2)
    assert any("1. hafta oynandı" in s.value for s in member.sidebar.success)
    assert _query(lambda db: len(db.scalars(select(ManagerWeekReport)).all())) == 2
    member_report = _texts(member.markdown)
    assert "Menajer tanınırlığı 8.00 →" in member_report and "Menajer tanınırlığı 12.00 →" not in member_report
    assert any(e.label.startswith("Son haftanın raporu · Sezon 1, 1. hafta") for e in member.expander)

    _run(owner)                                                                   # diger oturum DB'den okur
    owner_report = _texts(owner.markdown)
    assert "Menajer tanınırlığı 12.00 →" in owner_report and "Menajer tanınırlığı 8.00 →" not in owner_report
    captions = _texts(owner.sidebar.caption)
    assert "Sezon 1 · Hafta 2" in captions and "0/2 hazır" in captions
    assert owner.button(key="wp_ready").label.startswith("✅")                    # yeni tur temiz


def test_ready_advance_busy_is_retried_on_next_page_load(shared):
    owner, member = _owner(shared), _member(shared)
    _click(owner, "wp_ready")
    with _LockHolder("pg_advisory_xact_lock_shared"):                             # baska menajerin callback'i suruyor
        _click(member, "wp_ready")
        assert _state_week() == (1, 1)
        assert _sql("SELECT ready_career_week FROM public.world_managers WHERE user_id = :u",
                    u=shared.user_ids[MEMBER])[0][0] == 1                        # hazir bayragi yine de kaydedildi
        assert any("başka bir istekle" in i.value for i in member.sidebar.info)
    _run(owner)                                                                   # sayfa yuklemesi: maybe_advance_due
    assert _state_week() == (1, 2)
    assert any("1. hafta oynandı" in i.value for i in owner.sidebar.info)


def test_callbacks_show_busy_message_while_the_week_is_being_played(shared, monkeypatch):
    import database
    import web_common

    owner = _owner(shared, page="kadro")
    before = _query(lambda db: sorted((p.id, p.lineup_status.value) for p in _team(db, OWNER_TEAM).players))
    monkeypatch.setenv(database.WORLD_LOCK_TIMEOUT_ENV, "300")
    with _LockHolder("pg_advisory_xact_lock"):                                    # hafta oynuyor (exclusive)
        _click(owner, "tac_auto")
        assert [w.value for w in owner.sidebar.warning] == [web_common.BUSY_TEXT]
        _click(owner, "wp_ready")
        assert any(w.value == web_common.BUSY_TEXT for w in owner.sidebar.warning)
    after = _query(lambda db: sorted((p.id, p.lineup_status.value) for p in _team(db, OWNER_TEAM).players))
    assert after == before
    assert _sql("SELECT ready_career_week FROM public.world_managers WHERE is_primary")[0][0] is None
    _click(owner, "wp_ready")                                                     # kilit dustu: islem gecer
    assert _sql("SELECT ready_career_week FROM public.world_managers WHERE is_primary")[0][0] == 1


def _team(db, name):
    from models import Team

    return db.scalar(select(Team).where(Team.name == name))


# ---------------------------------------------------------------------------
# Paylasilan dunyada gizlenen / devre disi kontroller
# ---------------------------------------------------------------------------

def test_shared_world_hides_team_selector_seed_week_buttons_live_start_and_member_draw(shared):
    import web_app

    owner = _owner(shared)
    assert not {"sb_team"} & _keys(owner.selectbox) and "career_seed" not in _keys(owner.text_input)
    hidden = {"sb_set_team", "sb_change_mode", "lg_play", "lg_new_season", "arena_play", "live_fixture_start",
              "nav_continue", "home_continue"}
    assert not hidden & _keys(owner.button)
    assert {"wp_ready", "wp_force", "sb_worlds", "sb_logout", "home_ready"} <= _keys(owner.button)
    assert menu(owner) == _shared_pages("OWNER")
    goto(owner, "fikstur")
    assert "lg_ready" in _keys(owner.button) and not hidden & _keys(owner.button)
    goto(owner, "devler-arenasi")
    assert "arena_draw_all" in _keys(owner.button) and not hidden & _keys(owner.button)
    goto(owner, "canli-mac")
    assert any("resmi maçlar hafta ilerlerken" in i.value for i in owner.info)
    assert not hidden & _keys(owner.button)

    member = _member(shared, page="devler-arenasi")
    assert menu(member) == _shared_pages("MEMBER") and "dunya-yonetimi" not in menu(member)
    keys = _keys(member.button)
    assert not {"arena_draw_all", "wp_force", "lg_force", "adm_force"} & keys
    assert not any(k.startswith("arena_ball_") for k in keys)
    assert any(web_app.DRAW_ADMIN_TEXT in i.value for i in member.info)
    assert not [r for r in member.radio if r.key == "arena_format"]
    goto(member, "canli-mac")
    member.radio(key="live_mode").set_value(web_app.LIVE_FRIENDLY)                # hazirlik maci canli kalir
    _run(member)
    assert member.button(key="live_start")


def test_hidden_world_actions_are_refused_server_side(shared, web_state):
    import web_app
    import web_common

    web_state["auth"] = world_auth(shared.user_ids[MEMBER], MEMBER, "public", world_id=shared.world_id,
                                   world_kind="SHARED")
    assert web_app.cb_play_week() is None and web_app.cb_new_season() is None
    assert web_app.cb_draw_all() is None and web_app.cb_set_cup_format() is None
    assert _state_week() == (1, 1)
    assert web_state["flash"]["main"][0] == ("error", web_app.SHARED_WEEK_TEXT)     # Faz 13I: her sayfanin ustu
    assert ("error", web_app.DRAW_ADMIN_TEXT) in web_state["flash"]["arena"]
    import world_admin_view

    assert world_admin_view.cb_admin_kick(shared.seat_ids[SPARE]) is None       # uye yonetici callback'i cagiramaz
    assert web_state["flash"]["admin"] == [("error", web_common.ADMIN_ONLY_TEXT)]
    assert _sql("SELECT status FROM public.world_managers WHERE id = :s", s=shared.seat_ids[SPARE])[0][0] == "ACTIVE"

    web_state["auth"] = world_auth(shared.owner_id, OWNER, "public", world_id=shared.world_id, world_kind="SHARED")
    web_state["sb_team"] = "Madrid Blancos"
    assert web_app.cb_set_team() is None                                          # sahip de panelden secer
    assert web_state["flash"]["sidebar"][-1] == ("error", web_app.SHARED_TEAM_TEXT)
    assert _sql("SELECT t.name FROM public.game_state g JOIN public.teams t ON t.id = g.user_team_id")[0][0] == \
        OWNER_TEAM


def test_removed_member_is_rebound_to_default_world_with_message(web_state, monkeypatch):
    import web_common
    import worlds

    def removed(user_id, world_id):
        raise worlds.NotAMemberError(worlds.REMOVED)

    personal = worlds.WorldContext(world_id=77, schema="career_7", name="Kişisel kariyer", kind="PERSONAL",
                                   role="OWNER", user_id=7, world_seed=None)
    monkeypatch.setattr(worlds, "check_membership", removed)
    monkeypatch.setattr(worlds, "default_world", lambda session: personal)
    web_state.update(auth=world_auth(7, "atilan", "world_5", world_id=5, world_kind="SHARED"), theme="light",
                     career_ready="world_5", tac_rows=[1], neg={"x": 1}, world_ctx=object())
    calls: list = []
    assert web_common.member_callback(lambda: calls.append(1))() is None and calls == []
    auth = web_state["auth"]
    assert (auth.world_id, auth.career_schema, auth.world_kind) == (77, "career_7", "PERSONAL")
    assert web_state["theme"] == "light" and not {"career_ready", "tac_rows", "neg", "world_ctx"} & set(web_state)
    assert web_common.LOBBY_KEY not in web_state
    assert web_state["flash"]["sidebar"] == [("error", f"{web_common.REMOVED_TEXT} «Kişisel kariyer» yüklendi.")]


# ---------------------------------------------------------------------------
# Yonetim sekmesi: atma, kurallar, davet, olaylar
# ---------------------------------------------------------------------------

def test_admin_kick_bounces_kicked_session_and_member_sees_no_admin_tab(shared):
    import web_app
    import web_common
    import world_admin_view as admin

    owner, member = _owner(shared, page="dunya-yonetimi"), _member(shared)
    assert "dunya-yonetimi" not in menu(member) and "dunya-yonetimi" in menu(owner)
    owner.radio(key="adm_section").set_value(admin.SEC_MANAGERS)
    _run(owner)
    kick = f"adm_kick_{shared.seat_ids[MEMBER]}"
    assert owner.button(key=kick).disabled and f"adm_kick_{shared.seat_ids[OWNER]}" not in _keys(owner.button)
    owner.text_input(key="adm_kick_reason").set_value("<script>alert(1)</script> hareketsiz")
    owner.checkbox(key="adm_kick_ok").check()
    _run(owner)
    _click(owner, kick)
    assert any("dünyadan çıkarıldı" in s.value for s in owner.success)
    membership = _sql("SELECT status FROM accounts.world_memberships WHERE world_id = :w AND user_id = :u",
                      w=shared.world_id, u=shared.user_ids[MEMBER])[0]
    seat = _sql("SELECT status, team_id FROM public.world_managers WHERE id = :s", s=shared.seat_ids[MEMBER])[0]
    protected = _sql("SELECT ai_protected_until FROM public.teams WHERE name = :n", n=MEMBER_TEAM)[0][0]
    assert membership.status == "KICKED" and tuple(seat) == ("KICKED", None) and protected is not None

    _run(member)                                                                  # atilan oturum: lobi + mesaj
    assert not member.tabs and member.session_state[web_common.LOBBY_KEY] is True
    assert [e.value for e in member.error] == [web_common.REMOVED_TEXT]
    assert "tac_auto" not in _keys(member.button)

    spare = shared.user_ids[SPARE]                                                # sahip yonetici atar
    _click(owner, f"adm_role_{spare}")
    role = _sql("SELECT role FROM accounts.world_memberships WHERE world_id = :w AND user_id = :u",
                w=shared.world_id, u=spare)[0][0]
    assert role == "ADMIN"
    assert web_app.TAB_ADMIN in [t.label for t in app_as(spare, SPARE, shared.world_id).tabs]

    owner.radio(key="adm_section").set_value(admin.SEC_EVENTS)
    _run(owner)
    events = next(d.value for d in owner.dataframe if "Tür" in d.value.columns)
    assert "Atılma" in set(events["Tür"]) and any("<script>" in text_ for text_ in events["Olay"])   # duz metin


def test_admin_rules_turn_settings_and_invite_rotation(shared):
    import world_admin_view as admin
    from world_rules import WorldRules

    owner = _owner(shared, page="dunya-yonetimi")
    owner.number_input(key="adm_deadline_hours").set_value(48)
    owner.toggle(key="adm_pause_auto").set_value(True)
    _click(owner, "adm_turn_save")
    rules = WorldRules.from_dict(_sql("SELECT world_rules FROM public.game_state")[0][0])
    assert (rules.deadline_hours, rules.auto_advance) == (48, False)

    owner.radio(key="adm_section").set_value(admin.SEC_RULES)
    _run(owner)
    assert not owner.toggle(key="adm_rule_human_market").disabled                 # sezon basi: oyun kurali acik
    owner.number_input(key="adm_rule_max_missed_deadlines").set_value(5)
    owner.toggle(key="adm_rule_human_market").set_value(False)
    _click(owner, "adm_rules_save")
    rules = WorldRules.from_dict(_sql("SELECT world_rules FROM public.game_state")[0][0])
    assert (rules.max_missed_deadlines, rules.human_market) == (5, False)
    assert _sql("SELECT count(*) FROM public.world_events WHERE kind = 'RULES'")[0][0] >= 2

    _sql("UPDATE public.game_state SET current_week = 2 WHERE id = 1")            # sezon basladi: oyun kurallari kilitli
    _run(owner)
    assert owner.toggle(key="adm_rule_human_market").disabled and not owner.number_input(
        key="adm_rule_max_seats").disabled
    assert any("sezon başladıktan sonra kilitli" in i.value for i in owner.info)

    owner.radio(key="adm_section").set_value(admin.SEC_INVITE)
    _run(owner)
    assert "TESTKOD123" in _texts(owner.code)
    _click(owner, "adm_invite_rotate")
    code = _sql("SELECT invite_code FROM accounts.worlds WHERE id = :w", w=shared.world_id)[0][0]
    assert code != "TESTKOD123" and code in _texts(owner.code)


# ---------------------------------------------------------------------------
# Satir kilitleri: transfer tamamlama ve personel alimi yeniden dogrular (eski oturum)
# ---------------------------------------------------------------------------

def test_transfer_completion_rechecks_the_seller_under_row_lock():
    """Faz 13I: transfer masasi tamamlarken oyuncuyu satir kilidi altinda yeniden dogrular (dosya gecersiz olur)."""
    from models import Player, TransferDeal
    from tests.test_web_app import _app, _best_of, _know, _search

    _set_user_team("Manchester Blue", transfer_budget=900_000_000, wage_budget=5_000_000)
    target_id, target_name, value = _query(lambda db: (lambda p: (p.id, p.name, int(p.market_value)))(
        _best_of(db, "Karadeniz Storm")))
    _know("Manchester Blue", target_id)
    at = _app(seed="1", page="transfer")
    _search(at, target_name, target_id)
    _click(at, "mkt_offer")
    at.number_input(key="tc_fee").set_value(int(round(value * 5 / 10_000) * 10_000))     # comert: kabul
    at.slider(key="tc_pct").set_value(100)
    _run(at)
    _click(at, "tc_bid")
    _click(at, "tc_terms_open")
    _click(at, "tc_t_accept")
    if [b for b in at.button if b.key == "tc_med_go"]:
        _click(at, "tc_med_go")
    deal_id = at.session_state["tc_deal"]
    assert _query(lambda db: db.get(TransferDeal, deal_id).status) == "AGREED"
    budget = _query(lambda db: _team(db, "Manchester Blue").transfer_budget)

    vesuvio = _query(lambda db: _team(db, "Vesuvio Azzurri").id)                  # oyuncu bu arada baska kulube gitti
    _sql("UPDATE public.players SET team_id = :t WHERE id = :p", t=vesuvio, p=target_id)
    _click(at, "tc_complete")
    assert any("Karadeniz Storm kulübünde değil" in e.value for e in at.error)
    assert _query(lambda db: (db.get(Player, target_id).team_id, db.get(TransferDeal, deal_id).status)) == \
        (vesuvio, "VOIDED")
    assert _query(lambda db: _team(db, "Manchester Blue").transfer_budget) == budget


def test_staff_hire_rechecks_the_pool_under_row_lock():
    from models import Staff, StaffRole
    from tests.test_web_app import _app

    _set_user_team("Torino Bianconeri")
    physio = _query(lambda db: _team(db, "Torino Bianconeri").staff_by_role(StaffRole.PHYSIO)[0].id)
    at = _app(page="teknik-heyet")
    at.selectbox(key="st_release").set_value(physio)
    _run(at)
    _click(at, "st_release_btn")
    at.selectbox(key="st_hire").set_value(physio)
    _run(at)
    rival = _query(lambda db: _team(db, "Vesuvio Azzurri").id)                    # baska kulup ayni anda aldi
    _sql("UPDATE public.staff SET team_id = :t WHERE id = :s", t=rival, s=physio)
    _click(at, "st_hire_btn")
    assert any("zaten" in e.value and "kadrosunda" in e.value for e in at.error)
    assert _query(lambda db: db.get(Staff, physio).team_id) == rival
