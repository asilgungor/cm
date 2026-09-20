"""
Surukle-birak taktik tahtasi (Faz 13G) -- saf kurallar + sunucu dogrulamasi + AppTest.

    1) SAF (veritabani yok): niyet semasi, yerlesim kurma, tasima / yer degistirme kurallari, rol niyeti, bilesen verisi,
       tactics.validate_lineup(allow_incomplete) ve bilesenin JS'inde tehlikeli API olmamasi
    2) ENTEGRASYON (test veritabani, rollback): process_intent -> CareerManager.set_lineup / set_team_roles; red
       yollari (eski surum, kadro baska yoldan degisti, sakat oyuncu, baska kulubun oyuncusu, canli mac kilidi)
    3) AppTest: gercek web_app.py; bilesen tetikleyicisi ve tablo satiri secimi, tarayicinin gonderecegi widget
       durumlariyla (WidgetStates) surulur.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tactics_board as tb  # noqa: E402
from models import Position  # noqa: E402
from tactics import MAX_BENCH, formation_slots, validate_lineup  # noqa: E402
from team_roles import SetPieceRoles  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GK, DEF, MID, FWD = Position.GK, Position.DEF, Position.MID, Position.FWD


# ===========================================================================
# 1) SAF
# ===========================================================================

@dataclass
class FakePlayer:
    id: int
    name: str
    position: Position
    overall_rating: int = 70
    form: int = 50
    morale: int = 70
    condition: int = 100
    injured_until: int | None = None       # oynayabilecegi hafta
    suspended: int = 0

    def unavailability_reason(self, week: int) -> str | None:
        if self.injured_until is not None and self.injured_until > week:
            return f"sakat, {self.injured_until}. haftada dönüyor"
        if self.suspended:
            return f"cezalı, {self.suspended} maç"
        return None

    def is_available(self, week: int) -> bool:
        return self.unavailability_reason(week) is None


def _squad() -> list[FakePlayer]:
    """2 GK (1-2), 7 DEF (3-9), 7 MID (10-16), 5 FWD (17-21); guc id ile azalir."""
    plan = [GK, GK] + [DEF] * 7 + [MID] * 7 + [FWD] * 5
    return [FakePlayer(i + 1, f"Oyuncu {i + 1:02d}", pos, overall_rating=90 - i) for i, pos in enumerate(plan)]


def _lineup(players):
    """Guclu 11 (1 GK, 4 DEF, 4 MID, 2 FWD) + 7 kisilik kulube."""
    by_pos = {pos: [p for p in players if p.position is pos] for pos in (GK, DEF, MID, FWD)}
    xi = {p.id: GK for p in by_pos[GK][:1]}
    xi |= {p.id: DEF for p in by_pos[DEF][:4]}
    xi |= {p.id: MID for p in by_pos[MID][:4]}
    xi |= {p.id: FWD for p in by_pos[FWD][:2]}
    bench = [p.id for p in players if p.id not in xi][:MAX_BENCH]
    return xi, bench


def _board():
    players = _squad()
    xi, bench = _lineup(players)
    layout = tb.layout_from_lineup(players, "4-4-2", xi, bench)
    return players, {p.id: p for p in players}, layout


def _intent(**fields):
    return tb.parse_intent({"n": 1, "rev": 0, **fields})


def test_parse_intent_accepts_the_documented_schema():
    move = _intent(action="move", player=5, to="slot", slot=3)
    assert (move.action, move.player, move.to, move.slot) == ("move", 5, "slot", 3)
    assert _intent(action="move", player=5, to="bench").slot is None
    assert _intent(action="swap", player=5, **{"with": 9}).other == 9
    assert _intent(action="role", player=5, role="corner").role == "corner"
    assert _intent(action="profile", player=5).action == "profile"


@pytest.mark.parametrize("raw", [
    None, "swap", [], {"action": "drop", "player": 1}, {"action": "move", "player": True, "to": "bench"},
    {"action": "move", "player": "5", "to": "bench"}, {"action": "move", "player": 0, "to": "bench"},
    {"action": "move", "player": 5, "to": "tribune"}, {"action": "move", "player": 5, "to": "slot"},
    {"action": "move", "player": 5, "to": "slot", "slot": 11}, {"action": "move", "player": 5, "to": "slot", "slot": -1},
    {"action": "swap", "player": 5}, {"action": "role", "player": 5, "role": "president"},
    {"action": "profile", "player": 5, "n": -3},
])
def test_parse_intent_rejects_anything_outside_the_schema(raw):
    with pytest.raises(tb.IntentError):
        tb.parse_intent(raw)


def test_layout_fills_roles_by_power_and_keeps_the_previous_visual_order():
    players, _by_id, layout = _board()
    assert layout.roles == formation_slots("4-4-2") and layout.complete
    assert layout.slots[:5] == (1, 3, 4, 5, 6)                     # GK + 4 DEF (guc sirasi)
    assert set(layout.bench) == {2, 7, 8, 9, 14, 15, 16}
    swapped = tb.Layout("4-4-2", (1, 6, 4, 5, 3, *layout.slots[5:]), layout.bench)
    again = tb.layout_from_lineup(players, "4-4-2", swapped.xi(), swapped.bench, previous=swapped)
    assert again.slots == swapped.slots                             # hat ici gorsel sira korunur


def test_layout_fits_overflow_players_after_a_formation_change():
    players, _by_id, layout = _board()
    fitted = tb.layout_from_lineup(players, "3-5-2", layout.xi(), layout.bench)
    assert fitted.complete and fitted.formation == "3-5-2"
    assert set(fitted.slots) == set(layout.slots)                   # 4. defans bos kalan orta saha slotuna
    assert tb.layout_signature(fitted) != tb.signature("3-5-2", layout.xi(), layout.bench)


def test_move_swap_and_bench_exchange_rules():
    players, by_id, layout = _board()
    week = 1
    # iki ilk 11 oyuncusu yer degistirir: roller slota gore degisir
    defender, midfielder = layout.slots[1], layout.slots[6]
    res = tb.apply_intent(layout, _intent(action="swap", player=defender, **{"with": midfielder}), by_id, week)
    assert res.layout.xi()[defender] is MID and res.layout.xi()[midfielder] is DEF and res.layout.complete
    # kulubeden slota: ilk 11'e girer, cikan AYNI kulube sirasina
    bench_player = layout.bench[2]
    res = tb.apply_intent(layout, _intent(action="move", player=bench_player, to="slot", slot=1), by_id, week)
    assert res.layout.slots[1] == bench_player and res.layout.bench[2] == layout.slots[1]
    assert len(res.layout.bench) == len(layout.bench)
    # kadro disindan kulubedeki oyuncunun ustune: yer degistirir (kulube sayisi ayni)
    reserve = next(p.id for p in players if p.id not in layout.slots and p.id not in layout.bench)
    res = tb.apply_intent(layout, _intent(action="swap", player=reserve, **{"with": layout.bench[0]}), by_id, week)
    assert res.layout.bench[0] == reserve and layout.bench[0] not in res.layout.bench


def test_moving_out_of_the_xi_leaves_an_incomplete_draft_and_full_bench_is_refused():
    _players, by_id, layout = _board()
    mid = layout.slots[6]
    res = tb.apply_intent(layout, _intent(action="move", player=mid, to="reserves"), by_id, 1)
    assert not res.layout.complete and res.layout.filled == 10 and mid not in res.layout.xi()
    with pytest.raises(tb.IntentError, match="Kulübe dolu"):
        tb.apply_intent(layout, _intent(action="move", player=mid, to="bench"), by_id, 1)
    empty = res.layout.slots.index(None)
    back = tb.apply_intent(res.layout, _intent(action="move", player=mid, to="slot", slot=empty), by_id, 1)
    assert back.layout.complete and back.layout.xi() == layout.xi()


def test_unavailable_players_are_rejected_and_displaced_ones_leave_the_squad():
    players, by_id, layout = _board()
    injured = next(p for p in players if p.id not in layout.slots and p.id not in layout.bench)
    injured.injured_until = 6
    with pytest.raises(tb.IntentError, match=rf"{injured.name} ilk 11'e giremez: sakat"):
        tb.apply_intent(layout, _intent(action="move", player=injured.id, to="slot", slot=2), by_id, 1)
    with pytest.raises(tb.IntentError, match="kulübeye giremez"):
        tb.apply_intent(layout, _intent(action="swap", player=injured.id, **{"with": layout.bench[0]}), by_id, 1)
    # ilk 11'deki oyuncu sonradan cezali oldu: yerine gelen girer, cezali kulubeye degil kadro disina
    by_id[layout.slots[3]].suspended = 1
    res = tb.apply_intent(layout, _intent(action="swap", player=layout.bench[1], **{"with": layout.slots[3]}),
                          by_id, 1)
    assert res.layout.slots[3] == layout.bench[1] and layout.slots[3] not in res.layout.bench
    assert res.notes and "kadro dışına alındı" in res.notes[0]


def test_foreign_players_and_noops():
    _players, by_id, layout = _board()
    with pytest.raises(tb.IntentError, match="A takım kadronda değil"):
        tb.apply_intent(layout, _intent(action="move", player=999, to="slot", slot=0), by_id, 1)
    with pytest.raises(tb.IntentError, match="A takım kadronda değil"):
        tb.apply_intent(layout, _intent(action="swap", player=3, **{"with": 999}), by_id, 1)
    assert not tb.apply_intent(layout, _intent(action="move", player=3, to="slot", slot=1), by_id, 1).changed
    assert not tb.apply_intent(layout, _intent(action="move", player=layout.bench[0], to="bench"), by_id, 1).changed
    with pytest.raises(tb.IntentError):
        tb.apply_intent(layout, _intent(action="profile", player=3), by_id, 1)


def test_role_intent_sets_one_field():
    _players, by_id, _layout = _board()
    roles, text = tb.apply_role(SetPieceRoles(captain_id=1), _intent(action="role", player=4, role="penalty"), by_id)
    assert roles == SetPieceRoles(captain_id=1, penalty_taker_id=4) and "Penaltıları" in text
    with pytest.raises(tb.IntentError):
        tb.apply_role(SetPieceRoles(), _intent(action="role", player=999, role="captain"), by_id)


def test_board_payload_is_json_without_engine_numbers():
    players, _by_id, layout = _board()
    players[0].name = '<img src=x onerror="alert(1)">'
    payload = tb.board_payload(layout, players, 1, SetPieceRoles(captain_id=3), lambda ovr: "★★★",
                               rev=4, locked=False, team_name="Kulüp <b>")
    text = json.dumps(payload, ensure_ascii=False)
    assert payload["rev"] == 4 and len(payload["slots"]) == 11 and payload["max_bench"] == MAX_BENCH
    assert all(0 <= s["x"] <= 100 and 0 <= s["y"] <= 100 for s in payload["slots"])
    token = payload["slots"][0]["player"]
    assert set(token) == {"id", "name", "short", "ini", "pos", "cond", "band", "stars", "out", "badges", "offpos"}
    assert token["name"] == players[0].name                       # ham metin: bilesen textContent ile yazar
    assert "overall" not in text and "potential" not in text
    assert payload["slots"][1]["player"]["badges"] == ["C"]
    assert len(payload["bench"]) == 7 and len(payload["reserves"]) == len(players) - 18 == 3


def test_validate_lineup_allow_incomplete_only_relaxes_the_counts():
    players, _by_id, layout = _board()
    xi = layout.xi()
    xi.pop(layout.slots[10])
    strict = validate_lineup(players, "4-4-2", 1, xi, layout.bench)
    assert any("11 olmalı" in e for e in strict.errors)
    relaxed = validate_lineup(players, "4-4-2", 1, xi, layout.bench, allow_incomplete=True)
    assert relaxed.ok
    players[3].injured_until = 9                                    # diger kurallar aynen
    assert not validate_lineup(players, "4-4-2", 1, xi, layout.bench, allow_incomplete=True).ok
    too_many = {**layout.xi(), 2: GK}
    assert not validate_lineup(players, "4-4-2", 1, too_many, [], allow_incomplete=True).ok


def test_component_script_uses_no_dangerous_apis():
    import re

    source = (ROOT / "web_assets" / "tactics_board.js").read_text(encoding="utf-8")
    js = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", source)             # yorumlar (aciklamada API adlari geciyor)
    css = (ROOT / "web_assets" / "tactics_board.css").read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "fetch(",
                   "XMLHttpRequest", "WebSocket", "import(", "document.write", "setAttribute(\"on"):
        assert banned not in js, banned
    assert "http" not in js and "url(" not in css and "@import" not in css
    assert 'setTriggerValue' in js and 'textContent' in js


# ===========================================================================
# 2) ENTEGRASYON (test veritabani; her test rollback)
# ===========================================================================

def _db_available() -> bool:
    try:
        from database import wait_for_db
        return wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False


integration = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _club(db, name="Istanbul Lions"):
    from career_manager import CareerManager

    cm = CareerManager(db, seed=5)
    team = cm.find_team(name)
    cm.set_user_team(team)
    cm.auto_lineup(team)
    return cm, team


def _raw(state, **fields):
    return {"n": 1, "rev": 0, **fields}


@integration
@pytest.mark.integration
def test_process_intent_saves_complete_moves_and_keeps_drafts(db):
    import tactics_board_view as tbv

    cm, team = _club(db)
    state = tbv.board_state(cm, team, None)
    xi_before, bench_before, _ = cm.lineup_of(team)
    defender, midfielder = state.layout.slots[1], state.layout.slots[6]
    out = tbv.process_intent(cm, team, state, _raw(state, action="swap", player=defender, **{"with": midfielder}), 0)
    assert out.lineup_changed and out.messages[0] == ("success", "Kadro kaydedildi.")
    xi, _bench, _ = cm.lineup_of(team)
    assert xi[defender] is Position.MID and xi[midfielder] is Position.DEF
    assert not tbv.is_draft(out.state)

    # ilk 11'den kadro disina: 10 kisi -> TASLAK, veritabani degismez
    state = out.state
    striker = state.layout.slots[10]
    out = tbv.process_intent(cm, team, state, _raw(state, action="move", player=striker, to="reserves"), 0)
    assert tbv.is_draft(out.state) and out.lineup_changed and not out.state.layout.complete
    assert not [kind for kind, _text in out.messages if kind == "error"]
    assert cm.lineup_of(team)[0] == xi
    # kulubeden bos slota: 11 tamam -> kaydedilir (kulube bir kisi azalir)
    sub = out.state.layout.bench[0]
    out = tbv.process_intent(cm, team, out.state, _raw(out.state, action="move", player=sub, to="slot", slot=10), 0)
    xi, bench, _ = cm.lineup_of(team)
    assert not tbv.is_draft(out.state) and xi[sub] is Position.FWD
    assert striker not in xi and striker not in bench and len(bench) == len(bench_before) - 1


@integration
@pytest.mark.integration
def test_process_intent_rejections_leave_everything_unchanged(db):
    from sqlalchemy import select

    import tactics_board_view as tbv
    from models import Player

    cm, team = _club(db)
    state = tbv.board_state(cm, team, None)
    before = cm.lineup_of(team)
    first, second = state.layout.slots[1], state.layout.slots[2]

    stale = tbv.process_intent(cm, team, state, {"n": 1, "rev": 3, "action": "swap", "player": first,
                                                 "with": second}, rev=4)
    assert stale.messages == [("warning", tbv.STALE_TEXT)] and stale.state is state

    foreign = db.scalar(select(Player.id).where(Player.team_id != team.id, Player.team_id.is_not(None)))
    out = tbv.process_intent(cm, team, state, _raw(state, action="swap", player=first, **{"with": foreign}), 0)
    assert out.messages[0][0] == "error" and "A takım kadronda değil" in out.messages[0][1]

    sub = next(p for p in team.players if p.id == state.layout.bench[0])
    sub.injured_until_week = cm.current_week + 3                     # kulubedeki oyuncu sakatlandi
    db.flush()
    out = tbv.process_intent(cm, team, state, _raw(state, action="swap", player=sub.id, **{"with": first}), 0)
    assert out.messages[0][0] == "error" and f"{sub.name} ilk 11'e giremez: sakat" in out.messages[0][1]

    locked = tbv.process_intent(cm, team, state, _raw(state, action="swap", player=first, **{"with": second}), 0,
                                locked=True)
    assert locked.messages == [("error", tbv.LOCKED_TEXT)]
    assert tbv.process_intent(cm, team, state, _raw(state, action="profile", player=first), 0,
                              locked=True).profile == first
    assert cm.lineup_of(team) == before

    cm.clear_lineup(team)                                           # kadro baska yoldan degisti
    changed = tbv.process_intent(cm, team, state, _raw(state, action="swap", player=first, **{"with": second}), 0)
    assert changed.messages == [("warning", tbv.CHANGED_TEXT)] and changed.state is not state
    assert changed.state.layout.filled == 0


@integration
@pytest.mark.integration
def test_role_intents_use_team_roles_rules(db):
    import tactics_board_view as tbv

    cm, team = _club(db)
    state = tbv.board_state(cm, team, None)
    pid = state.layout.slots[7]
    out = tbv.process_intent(cm, team, state, _raw(state, action="role", player=pid, role="captain"), 0)
    assert out.roles_changed and cm.team_roles(team).captain_id == pid
    out = tbv.process_intent(cm, team, state, _raw(state, action="role", player=pid, role="free_kick"), 0)
    roles = cm.team_roles(team)
    assert (roles.captain_id, roles.free_kick_taker_id) == (pid, pid)


# ===========================================================================
# 3) AppTest
# ===========================================================================

def _component(at):
    element = at.get("bidi_component")[0]
    raw = element.proto.mixed.json if element.proto.WhichOneof("data") == "mixed" else element.proto.json
    return element, json.loads(raw)


def send_intent(at, value: dict):
    """Tarayicinin gonderecegi durum: bilesenin kendi durumu + 'intent' tetikleyicisi (toplayici widget)."""
    from streamlit.proto.WidgetStates_pb2 import WidgetState

    element, _data = _component(at)
    states = at._tree.get_widget_states()
    base = WidgetState(id=element.proto.id, json_value="{}")
    trigger = WidgetState(id=f"$$STREAMLIT_INTERNAL_KEY_{element.proto.id}__events",
                          json_trigger_value=json.dumps({"event": "intent", "value": value}))
    states.widgets.extend([base, trigger])
    at._run(states)
    assert not at.exception, at.exception
    return at


@integration
@pytest.mark.integration
def test_apptest_board_drag_and_menu_intents_update_the_database():
    from tests.test_web_app import _app, _query, _reseed, _set_user_team, _team

    _reseed()
    try:
        _set_user_team("Istanbul Lions")
        at = _app(page="kadro")
        at.button(key="tac_auto").click()
        at.run()
        _element, data = _component(at)
        assert data["formation"] and len(data["slots"]) == 11 and data["rev"] == 0
        assert at.button(key="tac_save")                                 # erisilebilir liste yolu duruyor
        first = data["slots"][1]["player"]["id"]
        mid = data["slots"][6]["player"]["id"]
        send_intent(at, {"n": 1, "rev": data["rev"], "action": "swap", "player": first, "with": mid})
        _element, data = _component(at)
        assert data["rev"] == 1 and data["slots"][6]["player"]["id"] == first
        roles = _query(lambda db: {p.id: (p.lineup_status.value, p.lineup_role.value if p.lineup_role else None)
                                   for p in _team(db, "Istanbul Lions").players})
        assert roles[first] == ("XI", "MID") and roles[mid] == ("XI", "DEF")
        assert any("Kadro kaydedildi" in s.value for s in at.success)

        send_intent(at, {"n": 2, "rev": data["rev"], "action": "role", "player": mid, "role": "captain"})
        _element, data = _component(at)
        assert "C" in data["slots"][1]["player"]["badges"]
        send_intent(at, {"n": 3, "rev": data["rev"], "action": "profile", "player": mid})
        assert at.session_state["pv_open"] == ("squad", mid)
        at.button(key="pv_close").click()                                # 14S: profil CM ekrani; Geri -> kadro
        at.run()

        send_intent(at, {"n": 4, "rev": 0, "action": "swap", "player": first, "with": mid})     # eski surum
        assert any("yenilendi" in w.value for w in at.warning)
        send_intent(at, {"n": 5, "rev": 99, "action": "hack", "player": first})
        assert any("Geçersiz hareket" in e.value for e in at.error)
    finally:
        _reseed(mode=None)          # conftest'in varsayilan dunyasi (mod secilmemis): sonraki moduller ona guvenir
