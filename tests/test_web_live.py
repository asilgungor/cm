"""
Canli mac ici mudahale uctan uca testleri (9. Asama) -- Streamlit AppTest.

Gercek web_app.py calisir. Maci ekrandaki DURDUR / DEVAM / Sonucu gör dugmeleri, Talimat
Paneli ve degisiklik paneli widget'lariyla yonetir; sonra mudahalenin MOTORA
(session_state["live"].engine) ve kariyer macinda VERITABANINA yansidigini dogrular.
Hiz "Anında": mac bir sonraki duraklamaya (devre arasi, kritik olay) kadar aninda akar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _html,
    _query,
    _reseed,
    _set_user_team,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")


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

def _live(at):
    return at.session_state["live"]


def _has_live(at) -> bool:
    try:
        return at.session_state["live"] is not None
    except KeyError:
        return False


def _set(at, kind: str, key: str, value):
    getattr(at, kind)(key=key).set_value(value)
    at.run()
    assert not at.exception, at.exception
    return at


def _start_friendly(at, side: str = "Ev sahibi", rule: str | None = None, seed: str = "3"):
    if not [r for r in at.radio if r.key == "live_mode"]:     # Faz 13G: kulupsuz kariyer once kulup secimini acar
        _set_user_team("Istanbul Lions")
        at.run()
    _set(at, "radio", "live_mode", "Hazırlık maçı")
    at.select_slider(key="live_speed").set_value("Anında")
    at.selectbox(key="live_home").set_value("Merseyside Reds")
    at.selectbox(key="live_away").set_value("London Gunners")
    at.text_input(key="live_seed").set_value(seed)
    at.radio(key="live_side").set_value(side)
    at.run()
    if rule is not None:
        _set(at, "radio", "live_rule", rule)
    return _click(at, "live_start")


def _finish_resuming(at, limit: int = 12):
    """Kritik olaylarda duran maci DEVAM ile sonuna kadar goturur."""
    for _ in range(limit):
        live = _live(at)
        if live.finished:
            return at
        assert live.paused
        _click(at, "live_resume")
    raise AssertionError("maç bitmedi")


def _played_fixtures() -> int:
    from sqlalchemy import func, select

    from models import Fixture, FixtureStatus

    return _query(lambda db: db.scalar(
        select(func.count()).select_from(Fixture).where(Fixture.status == FixtureStatus.PLAYED)))


# ---------------------------------------------------------------------------
# Hazirlik maci: durdur, degistir, dizilis, talimat
# ---------------------------------------------------------------------------

def test_friendly_pauses_at_half_time_and_interventions_reach_engine():
    from match_engine import EventType, MatchPhase
    from models import Position

    at = _start_friendly(_app())
    live = _live(at)
    eng = live.engine
    team = live.managed_team
    assert team is eng.home and team.name == "Merseyside Reds"
    assert live.paused and eng.phase is MatchPhase.HALF_TIME
    assert any("Devre arası" in i.value for i in at.info)
    assert "Talimat Paneli" in _html(at) and "Oyuncu Değişikliği" in _html(at)

    # --- oyuncu degisikligi (devre arasi pencere saymaz)
    sub = next(p for p in team.bench if p.position is not Position.GK)
    out = next((p for p in team.outfield_on_pitch if p.role is sub.position), team.outfield_on_pitch[0])
    out_role = out.role
    power_before = eng._team_strength(team, "midfield")
    at.selectbox(key="live_sub_out").set_value(out.id)
    at.selectbox(key="live_sub_in").set_value(sub.id)
    at.run()
    _click(at, "live_sub_confirm")
    assert any("menajer kararı" in s.value for s in at.success)
    assert at.radio(key="live_mode").disabled                        # canli mac surerken mod kilitli
    assert out.substituted and not out.on_pitch and out.left_minute == 45
    assert sub.on_pitch and sub.entered_minute == 45 and sub.role is out_role     # cikanin gorevi
    assert team.subs_used == 1 and team.player_count == 11
    assert eng._team_strength(team, "midfield") != power_before       # efektif guc aninda degisti
    manual = [e for e in eng.events if e.type is EventType.SUBSTITUTION and e.detail == "manual"]
    assert len(manual) == 1 and out.name in manual[0].description

    # --- canli dizilis: acil durum 5-3-2
    _set(at, "selectbox", "live_formation", "5-3-2")
    _click(at, "live_formation_apply")
    assert team.formation == (5, 3, 2)
    roles = [p.role for p in team.outfield_on_pitch]
    assert (roles.count(Position.DEF), roles.count(Position.MID), roles.count(Position.FWD)) == (5, 3, 2)
    assert any("5-3-2" in i.value for i in at.info)

    # --- Talimat Paneli: zihniyet ve sertlik
    _set(at, "radio", "live_mentality", "Çok Ofansif (Topyekûn Hücum)")
    _set(at, "radio", "live_tackling", "Sert Oyna")
    assert team.instructions.mentality.value == "ALL_OUT_ATTACK"
    assert team.instructions.tackling.value == "HARD"
    tactic = [e for e in eng.events if e.type is EventType.TACTICAL_CHANGE]
    assert [e.detail for e in tactic] == ["formation", "instructions", "instructions"]

    # --- devam: mac kaldigi dakikadan surer ve biter; hazirlik maci DB'ye yazilmaz
    kick_events = len(eng.events)
    _click(at, "live_resume")
    _finish_resuming(at)
    assert len(eng.events) > kick_events and eng.finished
    assert sub.minutes_played == eng.end_minute - 45 and out.minutes_played == 45
    html = _html(at)
    assert "MAÇ SONU" in html and 'viewBox="-4 -10 113 86"' in html
    assert any("Menajer müdahaleleri" in e.label for e in at.expander)
    assert _played_fixtures() == 0

    _click(at, "live_close")
    assert not _has_live(at)
    assert not at.radio(key="live_mode").disabled


def test_classic_three_sub_rule_blocks_fourth_substitution():
    from models import Position

    at = _start_friendly(_app(), rule="3 değişiklik (klasik)")
    live = _live(at)
    team = live.managed_team
    assert live.engine.max_subs == 3 and live.paused
    for _ in range(3):
        out = next(p for p in team.on_pitch if p.role is not Position.GK)
        sub = next(p for p in team.bench if p.position is not Position.GK)
        at.selectbox(key="live_sub_out").set_value(out.id)
        at.selectbox(key="live_sub_in").set_value(sub.id)
        at.selectbox(key="live_sub_role").set_value(out.role.value)
        at.run()
        _click(at, "live_sub_confirm")
    assert team.subs_used == 3
    assert any("değişiklik hakkı kalmadı" in w.value for w in at.warning)
    assert not [b for b in at.button if b.key == "live_sub_confirm"]      # onay dugmesi yok
    assert any("3/3 değişiklik" in c.value for c in at.caption)


def test_watch_only_friendly_has_no_intervention_panel():
    at = _start_friendly(_app(), side="Sadece izle")
    assert not _has_live(at)
    html = _html(at)
    assert "MAÇ SONU" in html and "Talimat Paneli" not in html


def test_pause_and_finish_buttons_drive_the_live_match():
    at = _start_friendly(_app())
    live = _live(at)
    assert live.paused
    _click(at, "live_resume")                 # DEVAM: bir sonraki duraklamaya kadar akar
    live = _live(at)
    if not live.finished:
        assert live.paused
    at2 = _start_friendly(_app(), seed="11")
    _click(at2, "live_finish")                # Sonucu gör: duraklamadan bitirir
    assert _live(at2).finished and "MAÇ SONU" in _html(at2)


# ---------------------------------------------------------------------------
# Kariyer: haftanin gercek maci canli
# ---------------------------------------------------------------------------

def test_manage_real_cup_then_league_match_live_and_save_completes_week():
    from models import Competition, Fixture, FixtureStatus, GameState

    _set_user_team("Istanbul Lions")
    at = _app(seed="7")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    assert at.button(key="live_fixture_start")

    # 1) hafta ici Devler Arenasi maci
    _click(at, "live_fixture_start")
    live = _live(at)
    assert live.is_fixture and live.competition == "cup" and live.paused       # devre arasi
    assert at.button(key="lg_play").disabled and at.button(key="arena_play").disabled
    assert not [b for b in at.button if b.key == "live_close"]                 # kaydetmeden kapanmaz
    cup_fixture_id = live.fixture_id
    _set(at, "radio", "live_mentality", "Çok Defansif (Otobüsü Çek)")
    _click(at, "live_finish")
    assert _live(at).finished
    _click(at, "live_save")
    assert any("Devler Arenası maçın kaydedildi" in s.value for s in at.success)
    cup_fx = _query(lambda db: (db.get(Fixture, cup_fixture_id).status, db.get(GameState, 1).current_week))
    assert cup_fx == (FixtureStatus.PLAYED, 1)                                  # hafta henuz bitmedi
    result = live.result()
    assert _query(lambda db: (db.get(Fixture, cup_fixture_id).home_score,
                              db.get(Fixture, cup_fixture_id).away_score)) == (result.home_score, result.away_score)

    # 2) ayni hafta lig maci
    _click(at, "live_close")
    assert at.button(key="live_fixture_start")
    _click(at, "live_fixture_start")
    live = _live(at)
    assert live.competition == "league" and live.fixture_id != cup_fixture_id
    _click(at, "live_finish")
    _click(at, "live_save")
    assert any("1. hafta tamamlandı" in s.value for s in at.success)
    assert _query(lambda db: db.get(GameState, 1).current_week) == 2
    assert _query(lambda db: db.get(Fixture, live.fixture_id).competition) is Competition.LEAGUE
    assert at.session_state["last_user_result"] is live.result()
    assert at.session_state["last_user_cup_result"] is not None


def test_play_week_button_uses_finished_live_result():
    from models import Fixture, GameState

    _set_user_team("Istanbul Lions")
    at = _app(seed="5")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_fixture_start")
    live = _live(at)
    _click(at, "live_finish")
    assert not at.button(key="lg_play").disabled          # bitti: hafta oynatilabilir, sonuc korunur
    _click(at, "lg_play")
    result = live.result()
    score = _query(lambda db: (db.get(Fixture, live.fixture_id).home_score, db.get(Fixture, live.fixture_id).away_score))
    assert score == (result.home_score, result.away_score)
    assert _query(lambda db: db.get(GameState, 1).current_week) == 2
    assert _live(at).saved


def test_unsaved_live_fixture_locks_market_staff_and_team_change():
    _set_user_team("Istanbul Lions")
    at = _app(seed="7")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_fixture_start")
    at.run()                                                   # kilitler bir sonraki cizimde gorunur
    infos = " ".join(i.value for i in at.info)
    assert "transfer işlemleri maç kaydedilene kadar kapalı" in infos
    assert "teknik heyet değişiklikleri maç kaydedilene kadar kapalı" in infos
    assert not [b for b in at.button if b.key == "sb_set_team"]           # Faz 13G: kariyerde kulup kilitli
    assert at.button(key="sb_change_mode").disabled
    assert not [b for b in at.button if b.key == "mkt_offer"]

    _click(at, "live_finish")
    _click(at, "live_save")
    assert _live(at).saved
    assert "transfer işlemleri maç kaydedilene kadar kapalı" not in " ".join(i.value for i in at.info)


def test_stale_live_fixture_warns_hides_save_and_can_be_closed():
    from career_manager import CareerManager
    from database import session_scope
    from models import Fixture

    _set_user_team("Istanbul Lions")
    at = _app(seed="7")
    at.select_slider(key="live_speed").set_value("Anında")
    at.run()
    _click(at, "live_fixture_start")
    live = _live(at)
    assert not [b for b in at.button if b.key == "live_close"]

    with session_scope() as db:                                # baska bir sekmede hafta oynandi
        CareerManager(db, seed=7).play_week()
    assert _query(lambda db: db.get(Fixture, live.fixture_id).is_played)

    _click(at, "live_finish")
    assert any("artık geçerli değil" in w.value for w in at.warning)
    assert not [b for b in at.button if b.key == "live_save"]
    _click(at, "live_close")
    assert not _has_live(at)
