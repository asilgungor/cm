"""
Taktik kaliciligi ENTEGRASYON testleri (13. Asama).

Gercek PostgreSQL'e karsi calisir (conftest test veritabani; onerilen: TEST_DB_NAME=fm_db_test_tacpersist).
Her test kendi transaction'ini rollback eder. Eski kayit ve kariyer izolasyonu testleri AYRI kariyer semalarinda
calisir ve semayi sonunda siler.

    * talimat / rol / oyun plani: kayit-okuma, hosgorulu okuma, dogrulama hatalari (Turkce TacticsError)
    * kulupten ayrilan / akademiye giden oyuncu rollerden ve plandan OKURKEN ayiklanir (kayit degismez)
    * kayitli taktikler: ad kurallari, 7 sinir, ayni ad (PostgreSQL lower), uzerine yazma, silme, uygulama
      notlari (satilan, sakat, akademideki, yeni gelen oyuncu) ve asistan modu
    * mac entegrasyonu: otomatik hafta (lig + kupa), canli mac hazirligi ve hazirlik maci kayitli taktikle
      (manager_controlled, plan olaylari); AI kulupleri durum bazli talimat + onerilen rollerle;
      ai_tactics=False eski motor davranisi
    * eski kayit yukseltmesi ve cok kullanicili izolasyon
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from career_manager import (  # noqa: E402
    MAX_TACTIC_PRESETS,
    CareerManager,
    TacticPresetView,
    TacticsError,
)
from instructions import Mentality, PassingStyle, Pressing, TeamInstructions, Tempo  # noqa: E402
from live_match import LiveMatch, SubRule, engine_config_for  # noqa: E402
from match_engine import EventType, MatchResult, prepare_fixture  # noqa: E402
from match_plan import MatchPlan, PlanAction, PlanRule, PlanTrigger, ScoreSituation  # noqa: E402
from models import GameMode, LineupStatus, Player, Position, SquadRole, Team  # noqa: E402
from team_roles import ROLE_FIELDS, SetPieceRoles  # noqa: E402
from transfers import ContractOffer  # noqa: E402


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

TEAM_COLUMNS = ("tactic_instructions", "set_piece_roles", "match_plan")
SET_PIECE_DETAILS = {"penalty", "free_kick", "corner"}


@pytest.fixture
def db():
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _manager(db, seed: int = 31, **kwargs) -> CareerManager:
    cm = CareerManager(db, seed=seed, **kwargs)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    return cm


def _user(cm: CareerManager, name: str = "Istanbul Lions") -> Team:
    team = cm.find_team(name)
    cm.set_user_team(team)
    return team


def _no_ai_market(cm: CareerManager) -> None:
    cm.run_ai_transfer_window = lambda: []


def _move(cm: CareerManager, player: Player, buyer: Team) -> None:
    """Gercek transfer yolu (complete_transfer): bedelsiz, alicinin maas alani acilir."""
    buyer.transfer_budget = max(buyer.transfer_budget, 0)
    buyer.wage_budget = buyer.wage_bill + 5_000_000
    cm.db.flush()
    cm.complete_transfer(buyer, player, 0, ContractOffer(wage=player.current_wage, years=3, role=SquadRole.BACKUP))


def _stored(db, team: Team) -> tuple:
    db.flush()
    return tuple(db.execute(
        select(Team.tactic_instructions, Team.set_piece_roles, Team.match_plan).where(Team.id == team.id)).one())


def _plan_for(team: Team) -> MatchPlan:
    ranked = sorted(team.players, key=lambda p: (-p.overall_rating, p.id))
    forwards = [p for p in ranked if p.position is Position.FWD]
    return MatchPlan((
        PlanRule(PlanTrigger(60, ScoreSituation.LOSING),
                 PlanAction(formation="4-3-3", instructions={"mentality": "ALL_OUT_ATTACK"},
                            sub_out_id=forwards[0].id, sub_in_id=forwards[-1].id), name="Baskı"),
        PlanRule(PlanTrigger(75, ScoreSituation.WINNING, margin=1),
                 PlanAction(instructions={"mentality": "PARK_THE_BUS", "tempo": "SLOW"})),
    ))


def _side(result: MatchResult, team_id: int):
    return result.home if result.home.id == team_id else result.away


def _fingerprint(result: MatchResult) -> tuple:
    return (result.home_score, result.away_score,
            tuple((e.minute, e.added_time, e.type.value, e.team_id, e.player_id, e.detail, e.description)
                  for e in result.events))


# ===========================================================================
# 1) TALIMAT, ROL, OYUN PLANI: kayit ve okuma
# ===========================================================================

def test_instructions_round_trip_and_tolerant_read(db):
    cm = _manager(db)
    team = _user(cm)
    assert cm.team_instructions(team) == TeamInstructions()

    wanted = TeamInstructions(Mentality.ALL_OUT_ATTACK, passing_style=PassingStyle.SHORT, tempo=Tempo.FAST,
                              pressing=Pressing.ALL_OVER, offside_trap=True)
    cm.set_team_instructions(team, wanted)
    db.expire(team)
    assert cm.team_instructions(team) == wanted
    assert _stored(db, team)[0] == wanted.to_dict()

    # Bozuk / eski kayit: gecersiz eksen varsayilana doner, bilinmeyen anahtar yok sayilir
    team.tactic_instructions = {"mentality": "YOK_BOYLE", "tempo": "SLOW", "counter_attack": "evet", "x": 1}
    db.flush()
    assert cm.team_instructions(team) == TeamInstructions(tempo=Tempo.SLOW, counter_attack=True)

    with pytest.raises(TacticsError, match="talimat"):
        cm.set_team_instructions(team, {"mentality": "ALL_OUT_ATTACK"})
    assert cm.team_instructions(team) == TeamInstructions(tempo=Tempo.SLOW, counter_attack=True)


def test_roles_round_trip_suggestion_and_squad_validation(db):
    cm = _manager(db)
    team = _user(cm)
    assert cm.team_roles(team) == SetPieceRoles()

    by_id = {p.id: p for p in team.players}
    suggested = cm.suggest_team_roles(team)
    assert all(getattr(suggested, name) in by_id for name in ROLE_FIELDS)
    assert by_id[suggested.penalty_taker_id].position is not Position.GK

    injured = by_id[suggested.penalty_taker_id]                    # oynayamayan oyuncu onerilmez
    injured.injured_until_week = cm.current_week + 3
    db.flush()
    assert cm.suggest_team_roles(team).penalty_taker_id not in (None, injured.id)
    injured.injured_until_week = 0
    db.flush()

    cm.set_team_roles(team, suggested)
    db.expire(team)
    assert cm.team_roles(team) == suggested
    assert _stored(db, team)[1] == suggested.to_dict()

    academy = cm.academy_players(team)[0]
    stranger = cm.find_team("Kadıköy Canaries").players[0]
    with pytest.raises(TacticsError, match="A takım kadrosunda değil") as exc:
        cm.set_team_roles(team, replace(suggested, captain_id=academy.id, corner_taker_id=stranger.id))
    assert academy.name in str(exc.value) and stranger.name in str(exc.value)
    assert "Kaptan" in str(exc.value) and "Korner atıcısı" in str(exc.value)
    with pytest.raises(TacticsError, match="Roller"):
        cm.set_team_roles(team, suggested.to_dict())
    assert cm.team_roles(team) == suggested                          # hicbir sey yazilmadi


def test_plan_round_trip_validation_and_tolerant_read(db):
    cm = _manager(db)
    team = _user(cm)
    assert cm.team_plan(team) == MatchPlan()

    plan = _plan_for(team)
    cm.set_team_plan(team, plan)
    db.expire(team)
    assert cm.team_plan(team) == plan
    assert _stored(db, team)[2] == plan.to_dict()

    stranger = cm.find_team("Kadıköy Canaries").players[0]
    bad = MatchPlan((PlanRule(PlanTrigger(70), PlanAction(sub_out_id=plan.rules[0].action.sub_out_id,
                                                          sub_in_id=stranger.id)),))
    with pytest.raises(TacticsError, match=r"Oyun planı kaydedilemedi: Kural 1: girecek oyuncu .* kadroda değil"):
        cm.set_team_plan(team, bad)
    with pytest.raises(TacticsError, match="Oyun planı okunamadı"):
        cm.set_team_plan(team, plan.to_dict())
    assert cm.team_plan(team) == plan

    # Bozuk JSONB: okunabilen kurallar korunur, hic okunamazsa bos plan
    raw = plan.to_dict()
    raw["rules"].insert(0, {"trigger": {"minute": 999}, "action": {}})
    raw["rules"].append("bozuk")
    team.match_plan = raw
    db.flush()
    assert cm.team_plan(team) == plan
    team.match_plan = {"rules": "yok"}
    db.flush()
    assert cm.team_plan(team) == MatchPlan()


def test_players_who_leave_are_dropped_from_roles_and_plan_on_read(db):
    cm = _manager(db)
    _no_ai_market(cm)
    team = _user(cm)
    plan = _plan_for(team)
    leaver = db.get(Player, plan.rules[0].action.sub_out_id)
    others = sorted((p for p in team.players if p.id != leaver.id and p.id != plan.rules[0].action.sub_in_id),
                    key=lambda p: p.id)
    captain, corner = others[0], others[1]
    cm.set_team_roles(team, SetPieceRoles(captain_id=captain.id, penalty_taker_id=leaver.id,
                                          free_kick_taker_id=leaver.id, corner_taker_id=corner.id))
    cm.set_team_plan(team, plan)
    before = _stored(db, team)

    _move(cm, leaver, cm.find_team("Manchester Blue"))
    corner.in_academy, corner.lineup_status = True, LineupStatus.OUT
    cm._refresh_squads(team)

    assert cm.team_roles(team) == SetPieceRoles(captain_id=captain.id)
    assert cm.team_plan(team) == MatchPlan(plan.rules[1:])
    assert _stored(db, team) == before                                # okuma kayda yazmaz

    team.set_piece_roles = {"captain_id": 987_654_321, "penalty_taker_id": captain.id}   # silinmis oyuncu
    db.flush()
    assert cm.team_roles(team) == SetPieceRoles(penalty_taker_id=captain.id)
    # Ayni oyuncu yeniden kaydedilemez
    with pytest.raises(TacticsError, match="Penaltı atıcısı"):
        cm.set_team_roles(team, SetPieceRoles(penalty_taker_id=leaver.id))


# ===========================================================================
# 2) KAYITLI TAKTIKLER
# ===========================================================================

def test_presets_names_cap_duplicates_overwrite_order_and_delete(db):
    cm = _manager(db)
    team = _user(cm)
    assert cm.tactic_presets(team) == []
    for bad, message in (("", "boş olamaz"), ("   ", "boş olamaz"), (None, "boş olamaz"), ("x" * 41, "en fazla 40")):
        with pytest.raises(TacticsError, match=message):
            cm.save_tactic_preset(team, bad)

    cm.set_formation(team, "4-3-3")
    cm.auto_lineup(team)
    view = cm.save_tactic_preset(team, "  Hücum Oyunu  ")
    assert isinstance(view, TacticPresetView)
    assert (view.name, view.formation, view.created_season, view.created_week) == ("Hücum Oyunu", "4-3-3", 1, 1)
    assert view.lineup_size == 11 + len(cm.lineup_of(team)[1])
    assert (view.instructions, view.roles, view.plan) == (TeamInstructions(), SetPieceRoles(), MatchPlan())
    assert cm.save_tactic_preset(team, "y" * 40).name == "y" * 40            # tam 40 karakter gecerli

    with pytest.raises(TacticsError, match="zaten var"):
        cm.save_tactic_preset(team, "HÜCUM OYUNU")
    cm.set_formation(team, "3-5-2")
    cm.set_team_instructions(team, TeamInstructions(Mentality.PARK_THE_BUS))
    over = cm.save_tactic_preset(team, "hücum oyunu", overwrite=True)
    assert (over.id, over.name, over.formation) == (view.id, "hücum oyunu", "3-5-2")
    assert over.instructions.mentality is Mentality.PARK_THE_BUS

    # Esitlik PostgreSQL lower() ile (benzersiz indeksle ayni): "İ" -> "i"
    cm.save_tactic_preset(team, "ileri baskı")
    with pytest.raises(TacticsError, match="zaten var"):
        cm.save_tactic_preset(team, "İleri Baskı")

    for name in ("Zeta", "alfa", "Beta", "Delta"):
        cm.save_tactic_preset(team, name)
    presets = cm.tactic_presets(team)
    assert len(presets) == MAX_TACTIC_PRESETS == 7
    assert [p.name for p in presets] == ["alfa", "Beta", "Delta", "hücum oyunu", "ileri baskı", "y" * 40, "Zeta"]
    with pytest.raises(TacticsError, match="En fazla 7 taktik"):
        cm.save_tactic_preset(team, "Sekizinci")
    zeta = next(p for p in presets if p.name == "Zeta")
    assert cm.save_tactic_preset(team, "ZETA", overwrite=True).id == zeta.id      # sinirda uzerine yazma serbest

    other = cm.find_team("Kadıköy Canaries")
    foreign = cm.save_tactic_preset(other, "Zeta")                               # ad kulup icinde tekil
    for action in (cm.delete_tactic_preset, cm.apply_tactic_preset):
        with pytest.raises(TacticsError, match="ait değil"):
            action(team, foreign.id)
    cm.delete_tactic_preset(team, view.id)
    assert len(cm.tactic_presets(team)) == 6
    with pytest.raises(TacticsError, match="bulunamadı"):
        cm.delete_tactic_preset(team, view.id)
    with pytest.raises(TacticsError, match="bulunamadı"):
        cm.apply_tactic_preset(team, view.id)
    assert cm.save_tactic_preset(team, "Sekizinci").name == "Sekizinci"         # yer acildi
    assert [p.name for p in cm.tactic_presets(other)] == ["Zeta"]


def test_apply_preset_restores_tactics_and_explains_skipped_players(db):
    cm = _manager(db)
    _no_ai_market(cm)
    team = _user(cm)
    cm.set_formation(team, "4-3-3")
    cm.auto_lineup(team)
    xi, bench, _ = cm.lineup_of(team)
    by_id = {p.id: p for p in team.players}
    starters = sorted((by_id[pid] for pid in xi), key=lambda p: p.id)
    sold = next(p for p in starters if xi[p.id] is Position.MID)
    injured = next(p for p in starters if xi[p.id] is Position.DEF)
    captain = next(p for p in starters if p.id not in (sold.id, injured.id))
    sub_in = next(by_id[pid] for pid in bench if by_id[pid].position is not Position.GK)
    academy = next(by_id[pid] for pid in reversed(bench) if pid != sub_in.id)

    instructions = TeamInstructions(Mentality.ALL_OUT_ATTACK, tempo=Tempo.FAST)
    plan = MatchPlan((
        PlanRule(PlanTrigger(60), PlanAction(sub_out_id=sold.id, sub_in_id=sub_in.id), name="Taze kan"),
        PlanRule(PlanTrigger(80, ScoreSituation.DRAWING), PlanAction(instructions={"mentality": "ALL_OUT_ATTACK"})),
    ))
    cm.set_team_instructions(team, instructions)
    cm.set_team_roles(team, SetPieceRoles(captain_id=captain.id, penalty_taker_id=sold.id))
    cm.set_team_plan(team, plan)
    preset = cm.save_tactic_preset(team, "Ana Taktik")
    assert preset.lineup_size == 11 + len(bench)

    # Her sey degisir: baska dizilis ve talimat, bos rol/plan, asistan kadrosu; satis, sakatlik, akademi, transfer
    cm.set_formation(team, "3-5-2")
    cm.set_team_instructions(team, TeamInstructions())
    cm.set_team_roles(team, SetPieceRoles())
    cm.set_team_plan(team, MatchPlan())
    cm.clear_lineup(team)
    _move(cm, sold, cm.find_team("Manchester Blue"))
    injured.injured_until_week = cm.current_week + 2
    academy.in_academy, academy.lineup_status = True, LineupStatus.OUT
    cm._refresh_squads(team)
    newcomer = next(p for p in cm.find_team("Merseyside Reds").players if p.position is Position.GK)
    _move(cm, newcomer, team)

    notes = cm.apply_tactic_preset(team, preset.id)
    assert f"{sold.name} artık kulüpte değil; ilk 11'e alınmadı." in notes
    assert f"{injured.name} ilk 11'e alınmadı: sakat, {cm.current_week + 2}. haftada dönüyor." in notes
    assert f"{academy.name} akademide; kulübeye alınmadı." in notes
    assert f"Penaltı atıcısı ({sold.name}) artık A takım kadrosunda değil; rol boş bırakıldı." in notes
    assert f"Oyun planındaki 1. kural «Taze kan» çıkarıldı: {sold.name} artık A takım kadrosunda değil." in notes
    assert f"{newcomer.name} kayıtlı taktikte yoktu; kadro dışı bırakıldı." in notes
    assert sum(note.startswith("Asistan: ") for note in notes) == 2          # bosalan DEF ve MID yeri
    assert len(notes) == 8

    assert team.formation == "4-3-3"
    assert cm.team_instructions(team) == instructions
    assert cm.team_roles(team) == SetPieceRoles(captain_id=captain.id)
    assert cm.team_plan(team) == MatchPlan(plan.rules[1:])
    check = cm.lineup_check(team)
    assert check.ok, check.errors
    new_xi, new_bench, new_out = cm.lineup_of(team)
    assert len(new_xi) == 11 and sold.id not in new_xi and injured.id not in new_xi
    kept = {pid: role for pid, role in xi.items() if pid not in (sold.id, injured.id)}
    assert kept.items() <= new_xi.items()
    assert set(new_bench) <= set(bench) - {academy.id} and newcomer.id in new_out
    assert injured.id in new_out


def test_apply_preset_without_saved_xi_leaves_selection_to_the_assistant(db):
    cm = _manager(db)
    team = _user(cm)
    formation = team.formation
    players = sorted(team.players, key=lambda p: p.id)
    left_out = players[0]
    cm._apply_lineup(team, {}, [p.id for p in players[1:]])
    preset = cm.save_tactic_preset(team, "Asistan")
    assert preset.lineup_size == len(players) - 1

    cm.set_formation(team, next(name for name in ("4-3-3", "3-5-2") if name != formation))
    cm.auto_lineup(team)
    notes = cm.apply_tactic_preset(team, preset.id)
    assert notes == ["Kayıtlı taktikte ilk 11 yok; maçta asistan en iyi 11'i kuracak."]
    xi, bench, out = cm.lineup_of(team)
    assert xi == {} and out == [left_out.id] and len(bench) == len(players) - 1
    assert team.formation == formation


# ===========================================================================
# 3) MAC ENTEGRASYONU
# ===========================================================================

def test_stored_tactics_drive_the_users_league_and_cup_matches_while_ai_clubs_adapt(db):
    cm = _manager(db, seed=41)
    _no_ai_market(cm)
    cm.set_game_mode(GameMode.CAREER)
    t = cm.tournaments.ensure()
    team = next(club for club in cm.teams() if cm.tournaments.is_participant(t, club.id))
    cm.set_user_team(team)
    cm.tournaments.draw_all()

    stored = TeamInstructions(Mentality.PARK_THE_BUS, pressing=Pressing.OWN_HALF)
    captain = cm.suggest_team_roles(team).captain_id
    cm.set_team_instructions(team, stored)
    cm.set_team_roles(team, SetPieceRoles(captain_id=captain))
    cm.set_team_plan(team, MatchPlan((PlanRule(PlanTrigger(1), PlanAction(instructions={"tempo": "FAST"}),
                                               name="Erken"),)))

    report = cm.play_week()
    assert report.user_result is not None and report.user_cup_result is not None
    assert _side(report.user_cup_result, team.id).roles.captain_id == captain
    for result in (report.user_cup_result, report.user_result):
        mine = _side(result, team.id)
        assert mine.manager_controlled and mine.plan_fired == {0}
        plan_events = [e for e in result.events if e.type is EventType.TACTICAL_CHANGE and e.team_id == team.id
                       and e.detail == "plan"]
        assert len(plan_events) == 1 and plan_events[0].minute == 1 and "«Erken»" in plan_events[0].description
        assert not any(e.detail == "ai" and e.team_id == team.id for e in result.events)
        assert mine.instructions == stored.with_changes({"tempo": "FAST"})      # AI talimati dokunmadi
        assert mine.roles.captain_id is not None and mine.roles.has_set_piece_takers   # asistan tamamladi

    results = [r for _, r in report.results + report.cup_results]
    ai_sides = [side for r in results for side in (r.home, r.away) if side.id != team.id]
    assert len(ai_sides) == 2 * len(results) - 2
    assert all(not side.manager_controlled and side.roles.captain_id is not None
               and side.roles.has_set_piece_takers for side in ai_sides)
    ai_ids = {side.id for side in ai_sides}
    assert any(e.detail == "ai" and e.team_id in ai_ids for r in results for e in r.events)
    assert any(e.detail in SET_PIECE_DETAILS for r in results for e in r.events)     # duran toplar acik


def test_live_match_engine_is_prepared_with_the_stored_tactics(db):
    cm = _manager(db, seed=43)
    cm.set_game_mode(GameMode.CAREER)
    t = cm.tournaments.ensure()
    team = next(club for club in cm.teams() if not cm.tournaments.is_participant(t, club.id))
    cm.set_user_team(team)
    stored = TeamInstructions(Mentality.ALL_OUT_ATTACK, passing_style=PassingStyle.DIRECT, counter_attack=True)
    roles = cm.suggest_team_roles(team)
    plan = MatchPlan((PlanRule(PlanTrigger(70, ScoreSituation.LOSING), PlanAction(formation="4-3-3")),))
    cm.set_team_instructions(team, stored)
    cm.set_team_roles(team, roles)
    cm.set_team_plan(team, plan)

    prep = cm.prepare_live_match(config=engine_config_for(SubRule.FIVE_IN_THREE, cm.engine_config))
    engine = prep.engine
    mine = engine.team_by_id(team.id)
    rival = engine.away if mine is engine.home else engine.home
    assert engine.cfg.ai_tactics and engine.cfg.sub_windows == 3              # arayuzun ayari AI'yi kapatmaz
    assert mine.manager_controlled and not rival.manager_controlled
    assert (mine.instructions, mine.roles, mine.plan) == (stored, roles, plan)
    assert rival.roles.captain_id is not None and rival.roles.has_set_piece_takers

    live = LiveMatch.create(engine, prep.managed_team_id, fixture_id=prep.fixture_id, competition="league",
                            season=prep.season, week=prep.week, title=prep.title)
    assert (live.instructions, live.roles, live.plan) == (stored, roles, plan)
    live.play_to_end()
    result = live.result()
    assert not any(e.detail == "ai" and e.team_id == team.id for e in result.events)
    report = cm.save_live_result(prep.fixture_id, result)
    assert report.user_result is result


def test_live_match_without_intervention_equals_the_automatic_match_with_stored_tactics(db):
    cm = _manager(db, seed=59)
    cm.set_game_mode(GameMode.CAREER)
    t = cm.tournaments.ensure()
    team = next(club for club in cm.teams() if not cm.tournaments.is_participant(t, club.id))
    cm.set_user_team(team)
    cm.set_team_instructions(team, TeamInstructions(Mentality.PARK_THE_BUS, counter_attack=True))
    cm.set_team_plan(team, MatchPlan((PlanRule(PlanTrigger(55), PlanAction(instructions={"tempo": "FAST"})),)))
    cm.play_midweek()
    fx, _ = cm.live_fixture()

    auto = cm._prepare_career_fixture(fx, cm.current_week).simulate()
    prep = cm.prepare_live_match()
    live = LiveMatch.create(prep.engine, prep.managed_team_id, fixture_id=prep.fixture_id)
    live.play_to_end()
    assert prep.fixture_id == fx.id and _fingerprint(live.result()) == _fingerprint(auto)


def test_friendly_uses_the_stored_tactics(db):
    cm = _manager(db, seed=47)
    team = _user(cm)
    cm.set_team_plan(team, MatchPlan((PlanRule(PlanTrigger(1), PlanAction(instructions={"pressing": "ALL_OVER"})),)))
    friendly = cm.play_friendly(cm.find_team("Merseyside Reds"))
    home, away = friendly.match.home, friendly.match.away
    assert home.id == team.id and home.manager_controlled and not away.manager_controlled
    assert any(e.detail == "plan" and e.team_id == team.id and e.minute == 1 for e in friendly.match.events)
    assert home.instructions.pressing is Pressing.ALL_OVER
    assert away.roles.has_set_piece_takers


def test_ai_tactics_off_keeps_the_old_engine_behaviour(db):
    cm = _manager(db, seed=53, ai_tactics=False)
    team = _user(cm)
    cm.set_team_instructions(team, TeamInstructions(Mentality.PARK_THE_BUS))
    week = cm.current_week
    fx = next(f for f in cm.fixtures_for_week(week) if not f.involves(team.id))
    engine = cm._prepare_career_fixture(fx, week)
    assert not engine.cfg.ai_tactics and engine.home.roles.is_default and engine.away.roles.is_default
    _, bare = prepare_fixture(db, fx.id, seed=cm.match_seed(fx), current_week=week)
    assert _fingerprint(engine.simulate()) == _fingerprint(bare.simulate())

    own = next(f for f in cm.fixtures_for_week(week) if f.involves(team.id))
    mine = cm._prepare_career_fixture(own, week).team_by_id(team.id)
    assert mine.manager_controlled and mine.instructions.mentality is Mentality.PARK_THE_BUS
    assert mine.roles.is_default                                           # asistan tamamlamasi da kapali


# ===========================================================================
# 4) ESKI KAYIT VE COK KULLANICILI IZOLASYON
# ===========================================================================

OLD_SAVE_SCHEMA = "test_tacpersist_old_save"
ISOLATION_SCHEMA = "test_tacpersist_iso"


def test_old_save_gets_the_tactics_columns_and_presets_table():
    import database
    import seed
    from database import SessionLocal

    with database.career_context(OLD_SAVE_SCHEMA):
        database.drop_career_schema(OLD_SAVE_SCHEMA)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            with database.career_connection() as conn:                        # 13. Asama oncesi kayit
                conn.exec_driver_sql('DROP TABLE "tactic_presets"')
                for column in TEAM_COLUMNS:
                    conn.exec_driver_sql(f'ALTER TABLE "teams" DROP COLUMN "{column}"')
            problems = set(database.schema_problems())
            assert "eksik tablo: tactic_presets" in problems
            assert {f"eksik sütun: teams.{c}" for c in TEAM_COLUMNS} <= problems

            applied = set(database.upgrade_schema())
            assert "tablo eklendi: tactic_presets" in applied
            assert {f"sütun eklendi: teams.{c}" for c in TEAM_COLUMNS} <= applied
            assert database.upgrade_schema() == [] and database.schema_problems() == []
            with database.career_connection() as conn:
                assert conn.scalar(text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = :s AND indexname = 'uq_tactic_preset_team_name'"
                ), {"s": OLD_SAVE_SCHEMA}) == 1

            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=7)
                team = cm.find_team("Istanbul Lions")
                cm.set_user_team(team)
                assert cm.team_instructions(team) == TeamInstructions()               # guvenli varsayilanlar
                assert cm.team_roles(team).is_default and cm.team_plan(team).is_empty
                cm.set_team_instructions(team, TeamInstructions(tempo=Tempo.FAST))
                cm.save_tactic_preset(team, "Eski Plan")
                with pytest.raises(TacticsError, match="zaten var"):
                    cm.save_tactic_preset(team, "ESKI PLAN")
                report = cm.play_week()
                assert report.user_result is not None
                assert _side(report.user_result, team.id).instructions.tempo is Tempo.FAST
                session.commit()
            finally:
                session.close()
        finally:
            database.drop_career_schema(OLD_SAVE_SCHEMA)


def _public_digest(conn) -> tuple:
    teams = conn.scalar(text(
        "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM \"public\".\"teams\" t"))
    presets = conn.scalar(text('SELECT count(*) FROM "public"."tactic_presets"'))
    return teams, presets


def test_tactics_stay_inside_the_career_schema():
    import database
    import seed
    from database import SessionLocal

    with database.engine.connect() as conn:
        public_before = _public_digest(conn)
    with database.career_context(ISOLATION_SCHEMA):
        database.drop_career_schema(ISOLATION_SCHEMA)
        try:
            database.init_db()
            with database.session_scope() as session:
                seed.write_world(session, seed.build_synthetic_world(2026), rng_seed=2026)
            instructions = TeamInstructions(Mentality.ALL_OUT_ATTACK, tempo=Tempo.FAST)
            plan = MatchPlan((PlanRule(PlanTrigger(65, ScoreSituation.DRAWING),
                                       PlanAction(instructions={"mentality": "ALL_OUT_ATTACK"})),))
            session = SessionLocal()
            try:
                cm = CareerManager(session, seed=8)
                team = cm.find_team("Istanbul Lions")
                cm.set_user_team(team)
                roles = cm.suggest_team_roles(team)
                cm.set_team_instructions(team, instructions)
                cm.set_team_roles(team, roles)
                cm.set_team_plan(team, plan)
                cm.save_tactic_preset(team, "İzole Taktik")
                team_id = team.id
                session.commit()
            finally:
                session.close()

            session = SessionLocal()
            try:
                assert session.scalar(text("SELECT current_schema()")) == ISOLATION_SCHEMA
                cm = CareerManager(session, seed=8)
                team = session.get(Team, team_id)
                assert (cm.team_instructions(team), cm.team_roles(team), cm.team_plan(team)) == (instructions, roles, plan)
                assert [p.name for p in cm.tactic_presets(team)] == ["İzole Taktik"]
            finally:
                session.close()
        finally:
            database.drop_career_schema(ISOLATION_SCHEMA)

    with database.engine.connect() as conn:
        assert _public_digest(conn) == public_before
    with SessionLocal() as session:                                         # baglamsiz: public kariyer
        cm = CareerManager(session)
        team = cm.find_team("Istanbul Lions")
        assert cm.team_instructions(team) == TeamInstructions()
        assert cm.team_roles(team).is_default and cm.team_plan(team).is_empty
        assert cm.tactic_presets(team) == []
