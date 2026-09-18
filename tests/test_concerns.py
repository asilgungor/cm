"""
Oyuncu kaygilari testleri (12. Asama, Soccer Manager "concerns": oynama suresi ve maas).

Saf kurallar (concerns.py) DB'siz; entegrasyon testleri gercek PostgreSQL'e karsi calisir ve her test kendi
transaction'ini rollback eder (onerilen: TEST_DB_NAME=fm_db_test_smpkg).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import concerns as cn  # noqa: E402
import finance  # noqa: E402
from concerns import ConcernLevel  # noqa: E402
from models import Position, SquadRole  # noqa: E402

# ===========================================================================
# 1) SAF KURALLAR
# ===========================================================================


def test_expected_share_combines_role_standing_and_keeper_rules():
    assert cn.expected_share(SquadRole.STAR, Position.MID, 1) == 0.75
    assert cn.expected_share(SquadRole.FIRST_TEAM, Position.MID, 6) == 0.55
    assert cn.expected_share(SquadRole.BACKUP, Position.MID, 7) == 0.20
    assert cn.expected_share(SquadRole.BACKUP, Position.DEF, 4) == cn.STANDING_SHARE     # gucu artan yedek
    assert cn.expected_share(SquadRole.BACKUP, Position.FWD, 3) == 0.20                   # 2 forvet slotu
    # Ikinci kaleci (sirasi ya da macta yakin gucte baska kaleci oynadi) sure beklemez
    assert cn.expected_share(SquadRole.STAR, Position.GK, 2) == cn.GK_BACKUP_SHARE
    assert cn.expected_share(SquadRole.STAR, Position.GK, 1, peer_keeper_played=True) == cn.GK_BACKUP_SHARE
    assert cn.expected_share(SquadRole.FIRST_TEAM, Position.GK, 1) == 0.55
    assert cn.keeper_peer_played(85, [82]) and cn.keeper_peer_played(85, [90])
    assert not cn.keeper_peer_played(85, [81]) and not cn.keeper_peer_played(85, [])


def test_position_ranks_and_overloaded_squads():
    ranks = cn.position_ranks([(1, Position.GK, 80), (2, Position.GK, 84), (3, Position.MID, 70), (4, Position.MID, 70)])
    assert ranks == {2: 1, 1: 2, 3: 1, 4: 2}
    synthetic = [0.75] * 3 + [0.55] * 8 + [0.20] * 4                      # 15 kisilik sentetik kadro
    hoarding = [0.75] * 5 + [0.55] * 12 + [0.20] * 8
    assert not cn.squad_overloaded(synthetic) and cn.squad_overloaded(hoarding)


def test_window_entries_weights_and_size():
    assert cn.match_entry(90, 0.55) == [90.0, 90.0, 0.55]
    assert cn.match_entry(120, 0.55) == [90.0, 90.0, 0.55]                # uzatma 90 ile sinirli
    assert cn.match_entry(60, 0.75, cup=True) == [30.0, 45.0, 0.75]       # kupa maci yarim agirlik
    window = [[90, 90, 0.5], "bozuk", [1, 2], None]
    pushed = cn.push_entry(window, cn.match_entry(0, 0.5))
    assert pushed == [[90.0, 90.0, 0.5, 0.0], [0.0, 90.0, 0.5, 0.0]] and pushed is not window
    for _ in range(20):
        pushed = cn.push_entry(pushed, cn.match_entry(45, 0.5))
    assert len(pushed) == cn.CONCERN_WINDOW
    # Hazirlik maci yeni olay acmaz: son olaya yarim agirlikli dakika eklenir
    assert cn.add_friendly_minutes([], 90, 0.2) == [[0.0, 0.0, 0.2, 45.0]]
    with_friendly = cn.add_friendly_minutes(pushed, 120, 0.5)
    assert len(with_friendly) == cn.CONCERN_WINDOW and with_friendly[-1] == [45.0, 90.0, 0.5, 45.0]
    assert with_friendly[:-1] == pushed[:-1] and pushed[-1] == [45.0, 90.0, 0.5, 0.0]


def test_playing_time_and_activity():
    window = [cn.match_entry(90, 0.55) for _ in range(4)] + [cn.match_entry(0, 0.55) for _ in range(4)]
    time = cn.playing_time(window)
    assert (time.wanted_matches, time.played_matches, time.active) == (4.4, 4.0, False)
    assert time.ratio == pytest.approx(4.0 / 4.4)
    # Hazirlik maci dakika ekler ama "son resmi mac"i degistirmez (kaygiyi duraklatma istismari yok)
    with_friendly = cn.add_friendly_minutes(window, 90, 0.55)
    assert cn.playing_time(with_friendly).active is False
    assert cn.playing_time(with_friendly).played_matches == 4.5
    assert cn.playing_time(with_friendly).wanted_matches == 4.4
    assert cn.playing_time(cn.push_entry(window, cn.match_entry(10, 0.55))).active is True
    assert cn.playing_time([]) == cn.PlayingTime(0.0, 0.0, False) and cn.playing_time(None).ratio is None


def test_target_level_thresholds_and_opportunity_shift():
    def level(played, wanted=5.0, role=SquadRole.FIRST_TEAM, overloaded=False):
        return cn.target_level(cn.PlayingTime(wanted, played, False), role, overloaded)

    assert [level(p) for p in (5.0, 4.0, 3.9, 2.75, 2.7, 1.5, 1.4, 0)] == [
        ConcernLevel.NONE, ConcernLevel.NONE, ConcernLevel.WATCH, ConcernLevel.WATCH, ConcernLevel.CONCERNED,
        ConcernLevel.CONCERNED, ConcernLevel.ANGRY, ConcernLevel.ANGRY]
    assert level(0, wanted=1.4) is ConcernLevel.NONE                         # az beklenti: degerlendirme yok
    # Sisirilmis kadroda yedekler daha erken kaygilanir; diger roller etkilenmez
    assert level(4.0, role=SquadRole.BACKUP) is ConcernLevel.NONE
    assert level(4.0, role=SquadRole.BACKUP, overloaded=True) is ConcernLevel.WATCH
    assert level(4.0, role=SquadRole.FIRST_TEAM, overloaded=True) is ConcernLevel.NONE


def test_levels_move_one_step_per_week_and_pause_while_playing():
    assert cn.next_level(0, ConcernLevel.ANGRY, active=False) is ConcernLevel.WATCH
    assert cn.next_level(ConcernLevel.WATCH, ConcernLevel.ANGRY, active=False) is ConcernLevel.CONCERNED
    assert cn.next_level(ConcernLevel.WATCH, ConcernLevel.ANGRY, active=True) is ConcernLevel.WATCH
    assert cn.next_level(ConcernLevel.ANGRY, ConcernLevel.NONE, active=True) is ConcernLevel.CONCERNED
    assert cn.next_level(ConcernLevel.ANGRY, ConcernLevel.NONE, active=False) is ConcernLevel.CONCERNED
    assert cn.next_level(2, ConcernLevel.CONCERNED, active=False) is ConcernLevel.CONCERNED
    assert cn.level_of(None) is ConcernLevel.NONE and cn.level_of(7) is ConcernLevel.ANGRY


def test_morale_effect_is_paused_and_stops_at_the_level_floor():
    assert [cn.level_morale_effect(lv) for lv in ConcernLevel] == [0, -1, -2, -4]
    assert cn.level_morale_effect(ConcernLevel.ANGRY, active=True, morale=80) == 0
    assert cn.level_morale_effect(ConcernLevel.ANGRY, morale=80) == -4
    assert cn.level_morale_effect(ConcernLevel.ANGRY, morale=32) == -2          # taban 30
    assert cn.level_morale_effect(ConcernLevel.ANGRY, morale=30) == 0
    assert cn.level_morale_effect(ConcernLevel.WATCH, morale=60) == 0
    assert cn.level_morale_effect(ConcernLevel.CONCERNED, morale=20) == 0


def test_rising_player_expectation_changes_gradually():
    """Sezon ortasinda yedekten ilk 11 seviyesine cikan oyuncunun beklentisi pencere boyunca kademeli yukselir."""
    window = [cn.match_entry(0, cn.expected_share(SquadRole.BACKUP, Position.MID, 6)) for _ in range(cn.CONCERN_WINDOW)]
    wanted = [cn.playing_time(window).wanted_matches]
    for _ in range(cn.CONCERN_WINDOW):
        window = cn.push_entry(window, cn.match_entry(0, cn.expected_share(SquadRole.BACKUP, Position.MID, 2)))
        wanted.append(cn.playing_time(window).wanted_matches)
    steps = [b - a for a, b in zip(wanted, wanted[1:], strict=False)]
    assert wanted[0] == 1.6 and wanted[-1] == 3.6
    assert all(0 < step <= 0.3 for step in steps), steps                   # ucurum yok


def test_wage_demand_rules():
    rep = 85
    fair = finance.expected_wage(80, rep, SquadRole.FIRST_TEAM)
    assert cn.wage_demand_amount(80, 80, fair, rep, SquadRole.FIRST_TEAM) is None
    assert cn.wage_demand_amount(80, 78, fair, rep, SquadRole.FIRST_TEAM) is None           # +2: yeterli degil
    risen = cn.wage_demand_amount(83, 80, fair, rep, SquadRole.FIRST_TEAM)
    assert risen == finance.expected_wage(83, rep, SquadRole.FIRST_TEAM) > fair
    # Guc artisina ragmen piyasa maasi zaten dusukse en az %10 zam ister
    rich = finance.expected_wage(90, rep, SquadRole.FIRST_TEAM)
    assert cn.wage_demand_amount(83, 80, rich, rep, SquadRole.FIRST_TEAM) == int(round(rich * 1.10 / 100) * 100)
    # Cok dusuk maas: sozlesme gucu bilinmiyorsa ister; reddedildikten sonra (guc degismedi) tekrar istemez
    cheap = int(fair * 0.5)
    assert cn.wage_demand_amount(80, None, cheap, rep, SquadRole.FIRST_TEAM) == fair
    assert cn.wage_demand_amount(80, 80, cheap, rep, SquadRole.FIRST_TEAM) is None
    assert cn.wage_demand_amount(81, 80, cheap, rep, SquadRole.FIRST_TEAM) is not None


# ===========================================================================
# 2) ENTEGRASYON
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


def _manager(db, seed=17):
    from career_manager import CareerManager

    cm = CareerManager(db, seed=seed)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.run_ai_transfer_window = lambda: []
    return cm


def _outfield(team, strongest=False):
    pool = [p for p in team.players if p.position is not Position.GK]
    return (max if strongest else min)(pool, key=lambda p: (p.overall_rating, p.id))


@integration
@pytest.mark.integration
def test_match_minutes_feed_the_window_and_replace_the_idle_morale_penalty(db):
    from career_manager import clamp, morale_delta, outcome_for
    from models import LineupStatus, Player

    cm = _manager(db)
    team = cm.find_team("Karadeniz Storm")                   # kupada degil: haftada tek resmi mac
    cm.set_user_team(team)
    xi = cm.auto_lineup(team)
    idle = next(p for p in sorted(team.players, key=lambda p: p.id) if p.id not in xi and p.position is not Position.GK)
    idle.lineup_status, idle.weeks_since_match = LineupStatus.OUT, 5
    injured = next(p for p in sorted(team.players, key=lambda p: -p.id) if p.id not in xi and p.id != idle.id)
    injured.injured_until_week = 5
    db.flush()
    morale0 = idle.morale

    report = cm.play_week()
    result = report.user_result
    mine, theirs = (result.home, result.away) if result.home.id == team.id else (result.away, result.home)
    mp = next(p for p in mine.players if p.id == idle.id)
    if mp.played:
        pytest.skip("kadro dışı oyuncu son çare olarak oynadı")
    db.flush()
    idle = db.get(Player, idle.id)
    # Eski davranista 6. hafta oynamayan -1 ek moral cezasi alirdi; kariyer modunda kaygilar bunun yerine gecer
    assert idle.morale == clamp(morale0 + morale_delta(None, outcome_for(mine.stats.goals, theirs.stats.goals)))
    ranks = cn.position_ranks((p.id, p.position, p.overall_rating) for p in team.players)
    expected = cn.match_entry(0, cn.expected_share(idle.squad_role, idle.position, ranks[idle.id]))
    assert idle.minutes_window == [expected + [0.0]]
    assert db.get(Player, injured.id).minutes_window == []                # sakat: kaygi durur
    for p in mine.players:
        if p.played:
            entry = db.get(Player, p.id).minutes_window[-1]
            assert entry[:2] == [float(min(p.minutes_played, 90)), 90.0]


@integration
@pytest.mark.integration
def test_tournament_mode_keeps_the_old_idle_rule_and_writes_no_window(db):
    from models import GameMode, Player

    cm = _manager(db)
    cm.set_game_mode(GameMode.TOURNAMENT)
    reports = [cm.play_week() for _ in range(2)]
    assert any(r.cup_results for r in reports)
    db.flush()
    assert all(p.minutes_window == [] and p.concern_level == 0 for p in db.query(Player).all())


@integration
@pytest.mark.integration
def test_weekly_concern_escalates_step_by_step_with_morale_and_notes(db):
    from career_manager import WeekReport

    cm = _manager(db)
    team = cm.find_team("Istanbul Lions")
    cm.set_user_team(team)
    player = _outfield(team, strongest=True)
    player.squad_role = cn.SquadRole.FIRST_TEAM
    player.minutes_window = [cn.match_entry(0, 0.55) for _ in range(cn.CONCERN_WINDOW)]
    player.morale, player.concern_level = 90, 0
    db.flush()

    levels, morales = [], []
    for week in range(1, 5):
        report = WeekReport(cm.season, week)
        cm._weekly_concerns(week, report)
        levels.append(player.concern_level)
        morales.append(player.morale)
        if week < 4:
            note = next(n for n in report.concern_notes if n.player_id == player.id)
            assert note.detail.startswith(cn.LEVEL_LABELS[ConcernLevel(week)])
            assert "4.4 maçlık süre bekliyor, 0 maçlık oynadı" in note.detail
        else:
            assert all(n.player_id != player.id for n in report.concern_notes)     # zaten en ust seviye
    assert levels == [1, 2, 3, 3]
    assert morales == [89, 87, 83, 79]

    # Son resmi macta oynadi: kaygi duraklar (hedef hala en ust seviye ama moral cezasi yok)
    player.minutes_window = cn.push_entry(player.minutes_window, cn.match_entry(90, 0.55))
    cm._weekly_concerns(5, WeekReport(cm.season, 5))
    assert (player.concern_level, player.morale) == (3, 79)
    # Duzenli oynamaya baslayinca haftada bir kademe iner
    player.minutes_window = [cn.match_entry(90, 0.55) + [0.0] for _ in range(cn.CONCERN_WINDOW)]
    cm._weekly_concerns(6, WeekReport(cm.season, 6))
    assert (player.concern_level, player.morale) == (2, 79)

    rows = {r.player_id: r for r in cm.player_concerns(team)}
    row = rows[player.id]
    assert (row.level, row.level_value, row.label, row.role, row.role_label) == (
        "CONCERNED", 2, "Şikayetçi", "FIRST_TEAM", "Önemli ilk 11 oyuncusu")
    assert (row.wanted, row.played, row.active, row.wage_demand) == (4.4, 8.0, True, None)
    assert "duruldu" in row.reason
    assert list(rows)[0] == player.id                                        # en kaygili once
    assert len(rows) == len(team.players)


@integration
@pytest.mark.integration
def test_user_wage_demand_can_be_accepted_or_refused(db):
    from career_manager import ConcernError, WeekReport

    cm = _manager(db)
    team = cm.find_team("Kadıköy Canaries")
    cm.set_user_team(team)
    star, other = _outfield(team, strongest=True), _outfield(team)
    for p in (star, other):
        p.contract_overall = p.overall_rating - 4
    db.flush()

    report = WeekReport(cm.season, 1)
    cm._weekly_concerns(1, report)
    demands = {n.player_id: n for n in report.wage_demands}
    assert star.id in demands and other.id in demands and "yeni sözleşme istiyor" in demands[star.id].detail
    expected = cn.wage_demand_amount(star.overall_rating, star.contract_overall, star.current_wage,
                                     team.reputation, star.squad_role)
    assert star.wage_demand == expected > star.current_wage
    assert cm.player_concerns(team)[0].wage_demand is not None                 # bekleyen talep one cikar

    # Bekleyen talep her hafta moral dusurur, yeni talep uretmez
    morale = star.morale
    again = WeekReport(cm.season, 2)
    cm._weekly_concerns(2, again)
    assert star.morale == morale - 1 and again.wage_demands == []

    # Maas havuzu yetmiyor: hata, hicbir sey degismez
    team.wage_budget = team.wage_bill
    db.flush()
    wage, demand = star.current_wage, star.wage_demand
    with pytest.raises(ConcernError, match="Maaş havuzunda yer yok"):
        cm.respond_wage_demand(star, accept=True)
    assert (star.current_wage, star.wage_demand) == (wage, demand)

    team.wage_budget = team.wage_bill + 1_000_000
    db.flush()
    morale, years = star.morale, star.contract_years
    message = cm.respond_wage_demand(star, accept=True)
    assert star.current_wage == demand and star.wage_demand is None and "yeni sözleşmeyi imzaladı" in message
    assert star.contract_overall == star.overall_rating and star.contract_years == max(years, 2)
    assert star.morale == min(100, morale + cn.WAGE_ACCEPT_MORALE)

    level, morale = other.concern_level, other.morale
    message = cm.respond_wage_demand(other, accept=False)
    assert "reddedildi" in message and other.wage_demand is None
    assert other.concern_level == min(3, level + 1) and other.morale == max(0, morale + cn.WAGE_REFUSE_MORALE)
    assert other.contract_overall == other.overall_rating
    later = WeekReport(cm.season, 3)
    cm._weekly_concerns(3, later)
    assert later.wage_demands == []                                            # reddedilen talep yinelenmez

    with pytest.raises(ConcernError, match="talep etmiyor"):
        cm.respond_wage_demand(other, accept=True)
    with pytest.raises(ConcernError, match="senin oyuncun değil"):
        cm.respond_wage_demand(_outfield(cm.find_team("Paris Rouge-Bleu")), accept=True)


@integration
@pytest.mark.integration
def test_ai_clubs_resolve_wage_demands_immediately(db):
    from career_manager import WeekReport

    cm = _manager(db)
    cm.set_user_team(cm.find_team("Istanbul Lions"))
    rich, poor = cm.find_team("Madrid Blancos"), cm.find_team("Karadeniz Storm")
    happy, unhappy = _outfield(rich, strongest=True), _outfield(poor, strongest=True)
    for p in (happy, unhappy):
        p.contract_overall = p.overall_rating - 5
    rich.wage_budget, rich.transfer_budget = rich.wage_bill + 5_000_000, 10 ** 9
    poor.wage_budget, poor.transfer_budget = poor.wage_bill, 0                  # zam icin kaynak yok
    db.flush()
    wage, level, morale = unhappy.current_wage, unhappy.concern_level, unhappy.morale
    demand = cn.wage_demand_amount(happy.overall_rating, happy.contract_overall, happy.current_wage,
                                   rich.reputation, happy.squad_role)
    assert demand is not None

    report = WeekReport(cm.season, 1)
    cm._weekly_concerns(1, report)
    assert report.wage_demands == []                                           # AI talebi bekletmez
    assert happy.wage_demand is None and happy.current_wage == demand
    assert happy.contract_overall == happy.overall_rating
    assert unhappy.wage_demand is None and unhappy.current_wage == wage
    assert unhappy.concern_level == min(3, level + 1) and unhappy.morale < morale


@integration
@pytest.mark.integration
def test_hoarded_squad_flags_opportunity_concerns(db):
    cm = _manager(db)
    team = cm.find_team("Manchester Blue")
    cm.set_user_team(team)
    assert not any(r.overloaded for r in cm.player_concerns(team))
    for kid in cm.academy_players(team):                         # kadroyu sisir: tum gencler A takima
        cm.promote_to_senior(team, kid)
    for p in cm._senior_players(team):
        p.squad_role = cn.SquadRole.STAR
    db.flush()
    rows = cm.player_concerns(team)
    assert len(rows) > 15 and all(r.overloaded for r in rows)
    backup = next(r for r in rows if r.position != "GK")
    assert backup.role_label == "Vazgeçilmez" and "kalabalık" not in backup.reason       # yalnizca Yedek rolune yazilir
