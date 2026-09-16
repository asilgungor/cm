"""
preview_views.py
================
Mac onu istihbarati, rakip gozlem raporu ve kadro planlayici gorunum modelleri (salt okunur).

career_views.py gibi: veritabanindan ORM nesneleri okunur, arayuze match_preview.py / squad_planner.py
dataclass'lari (duz veri) doner. Kurallar o saf modullerdedir; burada yalnizca sorgu ve donusum var.

SALT OKUNUR: hicbir fonksiyon session'a nesne eklemez, alan degistirmez, flush/commit cagirmaz.
(SessionLocal autoflush=False: sorgular cagiranin bekleyen degisikliklerini de yazmaz.) GameState
yoksa olusturulmaz (CareerManager.state bunu yapardi); fiksturun sezonu/haftasi kullanilir.

    next_preview_fixture   kullanicinin siradaki oynanmamis maci (lig ya da kupa; ayni haftada kupa once)
    build_match_preview    iki takimin sirasi/puani, son 5 mac formu, ic saha/deplasman karnesi,
                           aralarindaki maclar, sakat/cezalilar, izlenecek oyuncular, takim yildizi,
                           kagit ustu yorum
    scout_opposition       rakibin tahmini dizilisi ve ilk 11'i (motorun kadro secimiyle AYNI mantik:
                           match_engine.build_match_team + MatchTeam.select_lineup), izleyen kulubun en iyi
                           gozlemcisine (judging_ability) gore bozulur; tohum fikstur + oyun haftasi
    build_squad_plan       A takim (+ akademi adaylari) mevki gruplari, derinlik, sozlesme ve yas projeksiyonu

Gozlemci sisi: rakip oyuncunun yildizi transfer pazariyla ayni tahmin araligidir (CareerManager.scouted_report
ile ayni tohum parcalari: gozlemci puani, oyuncu id, 'overall_rating'). Kendi oyuncularin kesin yildizla,
potansiyel her zaman gozlemci tahmini (CareerManager.potential_estimate).
"""

from __future__ import annotations

import random

from sqlalchemy import and_, case, func, or_, select

import match_preview as mp
import squad_planner as sp
import staff as staff_rules
from career_manager import SENIOR_SQUAD_MAX, CareerManager, standings_key
from cup_draw import STAGE_LABELS, Stage
from match_engine import EngineConfig, MatchTeam, build_match_team
from models import (
    Competition,
    Fixture,
    FixtureStatus,
    GameState,
    Player,
    PlayerMatchStat,
    Position,
    StaffRole,
    Team,
)
from stars import star_range
from tactics import FORMATIONS, ROLE_ORDER, formation_name
from tournament_manager import CUP_SHORT_NAME

LEAGUE_LABEL = "Lig"
_ROLE_INDEX = {role: i for i, role in enumerate(ROLE_ORDER)}


# ===========================================================================
# 0) ORTAK YARDIMCILAR (salt okunur)
# ===========================================================================

def _state(db) -> GameState | None:
    return db.get(GameState, 1)


def _availability_week(db, fx: Fixture) -> int:
    """Sakatlik kontrolu haftasi: fiksturun haftasi (gecikmis macta oyun haftasi)."""
    st = _state(db)
    if st is not None and st.season == fx.season:
        return max(fx.week, st.current_week)
    return fx.week


def _game_week(db, fx: Fixture) -> int:
    """Gozlemci raporunun tohum haftasi: oyun haftasi (yoksa fikstur haftasi)."""
    st = _state(db)
    return st.current_week if st is not None and st.season == fx.season else fx.week


def competition_label(fx: Fixture) -> str:
    """'Lig' / 'Devler Arenası · Son 16 ilk maç' / 'Devler Arenası · Final'."""
    if fx.competition is not Competition.CUP:
        return LEAGUE_LABEL
    try:
        stage = Stage(fx.stage)
    except ValueError:
        return CUP_SHORT_NAME
    part = STAGE_LABELS[stage]
    if stage is Stage.GROUP and fx.leg:
        part = f"{part} {fx.leg}. maç"
    elif stage is not Stage.FINAL and fx.leg:
        part = f"{part} {'ilk maç' if fx.leg == 1 else 'rövanş'}"
    return f"{CUP_SHORT_NAME} · {part}"


def _played_match(fx: Fixture) -> mp.PlayedMatch:
    return mp.PlayedMatch(
        fixture_id=fx.id, season=fx.season, week=fx.week,
        competition=mp.CUP if fx.competition is Competition.CUP else mp.LEAGUE,
        home_team_id=fx.home_team_id, away_team_id=fx.away_team_id,
        home_score=fx.home_score, away_score=fx.away_score,
        neutral_venue=bool(fx.neutral_venue), label=competition_label(fx),
    )


def _season_matches(db, season: int, team_ids: list[int]) -> list[mp.PlayedMatch]:
    fixtures = db.scalars(
        select(Fixture).where(
            Fixture.season == season, Fixture.status == FixtureStatus.PLAYED,
            or_(Fixture.home_team_id.in_(team_ids), Fixture.away_team_id.in_(team_ids)),
        ).order_by(Fixture.week, Fixture.id)
    )
    return [_played_match(fx) for fx in fixtures]


def _meetings(db, team_a: int, team_b: int) -> list[mp.PlayedMatch]:
    """Iki takim arasindaki tum sezonlarin oynanmis maclari (lig + kupa)."""
    fixtures = db.scalars(
        select(Fixture).where(
            Fixture.status == FixtureStatus.PLAYED,
            or_(and_(Fixture.home_team_id == team_a, Fixture.away_team_id == team_b),
                and_(Fixture.home_team_id == team_b, Fixture.away_team_id == team_a)),
        ).order_by(Fixture.season, Fixture.week, Fixture.id)
    )
    return [_played_match(fx) for fx in fixtures]


def _team_names(db, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(Team.id, Team.name).where(Team.id.in_(ids))).all())


class _Fog:
    """Izleyen kulubun gozunden yildiz: kendi oyuncusu kesin, digerleri gozlemci araligi."""

    def __init__(self, viewer: Team | None) -> None:
        self.viewer_id = viewer.id if viewer is not None else None
        best = viewer.best_staff(StaffRole.SCOUT, "judging_ability") if viewer is not None else None
        self.scout = best
        self.rating = best.judging_ability if best is not None else None
        self.margin = staff_rules.scout_margin(self.rating)

    def range(self, p: Player) -> tuple[int, int]:
        if p.team_id is not None and p.team_id == self.viewer_id:
            return p.overall_rating, p.overall_rating
        value = staff_rules.scouted_value(p.overall_rating, self.margin, (self.rating or 0, p.id, "overall_rating"))
        return value.low, value.high

    def stars(self, p: Player) -> str:
        return star_range(*self.range(p))

    def estimate(self, p: Player) -> int:
        low, high = self.range(p)
        return (low + high) // 2


def _unavailability(competition: Competition, week: int):
    """Kupada yalnizca kupa cezalari gecerli (tournament_manager.prepare_cup_engine ile ayni)."""
    if competition is not Competition.CUP:
        return None

    def cup_reason(player, w=week):
        return player.unavailability_reason(w, Competition.CUP)

    return cup_reason


def expected_lineup(team: Team, is_home: bool, week: int, competition: Competition,
                    formation: str | None = None) -> MatchTeam:
    """
    Motorun bu mac icin kuracagi ilk 11 (OYNATMAZ, ORM'e yazmaz): prepare_fixture ile ayni kadro
    kurulumu. formation verilirse (gozlemcinin yanlis dizilis tahmini) o dizilisle kurulur.
    """
    mt = build_match_team(team, is_home, week, _unavailability(competition, week))
    if formation in FORMATIONS:
        mt.formation = FORMATIONS[formation]
    elif mt.formation is None:
        mt.formation = EngineConfig().formation
    mt.select_lineup()
    return mt


def _home_advantage(fx: Fixture, home: Team) -> float:
    """Motorun ic saha carpani (MatchEngine.home_advantage ile ayni formul, varsayilan ayar)."""
    if fx.neutral_venue:
        return 1.0
    cfg = EngineConfig()
    return 1 + cfg.home_advantage_base + cfg.home_advantage_per_reputation * home.reputation


def _position_key(p: Player) -> tuple:
    return _ROLE_INDEX.get(p.position, 9), p.name


def _absentees(team: Team, week: int, competition: Competition, fog: _Fog
               ) -> tuple[tuple[mp.AbsentPlayer, ...], tuple[mp.AbsentPlayer, ...]]:
    injured: list[mp.AbsentPlayer] = []
    suspended: list[mp.AbsentPlayer] = []
    cup = competition is Competition.CUP
    for p in sorted(team.players, key=_position_key):
        if p.is_injured(week):
            injured.append(mp.AbsentPlayer(
                player_id=p.id, name=p.name, position=p.position.value, stars=fog.stars(p),
                reason="Sakat", detail=f"{p.injured_until_week}. haftada dönüyor",
                return_week=p.injured_until_week,
            ))
        matches = p.cup_suspended_matches if cup else p.suspended_matches
        if matches > 0:
            suspended.append(mp.AbsentPlayer(
                player_id=p.id, name=p.name, position=p.position.value, stars=fog.stars(p),
                reason="Cezalı", detail=f"{matches} maç ceza" + (" (kupa)" if cup else ""),
                matches_left=matches,
            ))
    return tuple(injured), tuple(suspended)


def _season_lines(db, team: Team, season: int, week: int, competition: Competition, fog: _Fog
                  ) -> list[mp.PlayerSeasonLine]:
    """Takimin su anki A takim oyuncularinin bu sezon BU takimda (lig + kupa) istatistikleri."""
    rows = db.execute(
        select(PlayerMatchStat.player_id, func.count(PlayerMatchStat.id), func.sum(PlayerMatchStat.goals),
               func.sum(PlayerMatchStat.assists), func.avg(PlayerMatchStat.rating))
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .where(Fixture.season == season, PlayerMatchStat.team_id == team.id)
        .group_by(PlayerMatchStat.player_id)
    ).all()
    stats = {pid: (int(n), int(g or 0), int(a or 0), round(float(r), 2) if r is not None else None)
             for pid, n, g, a, r in rows}
    lines = []
    for p in team.players:
        apps, goals, assists, avg = stats.get(p.id, (0, 0, 0, None))
        lines.append(mp.PlayerSeasonLine(
            player_id=p.id, name=p.name, position=p.position.value, stars=fog.stars(p),
            appearances=apps, goals=goals, assists=assists, average_rating=avg,
            prominence=fog.estimate(p), available=p.is_available(week, competition),
        ))
    return lines


def _cards(db, team_id: int, season: int) -> tuple[int, int]:
    yellow, red = db.execute(
        select(func.coalesce(func.sum(PlayerMatchStat.yellow_cards), 0),
               func.coalesce(func.sum(case((PlayerMatchStat.red_card.is_(True), 1), else_=0)), 0))
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .where(Fixture.season == season, PlayerMatchStat.team_id == team_id)
    ).one()
    return int(yellow), int(red)


def _league_standing(db, team: Team) -> tuple[str | None, int | None, int]:
    """(lig adi, sira, takim sayisi). Siralama career_manager.standings_key ile ayni."""
    teams = list(db.scalars(select(Team).where(Team.league_id == team.league_id)))
    table = sorted(teams, key=standings_key)
    position = next((i for i, t in enumerate(table, start=1) if t.id == team.id), None)
    return (team.league.name if team.league is not None else None), position, len(table)


def _team_formation(team: Team) -> str:
    return team.formation if team.formation in FORMATIONS else formation_name(EngineConfig().formation)


# ===========================================================================
# 1) MAC ONU
# ===========================================================================

def next_preview_fixture(db, team_id: int) -> Fixture | None:
    """Takimin bu sezon oynanmamis ilk maci: once hafta, ayni haftada kupa (hafta ici) ligden once."""
    st = _state(db)
    stmt = (
        select(Fixture)
        .where(
            Fixture.status == FixtureStatus.UNPLAYED,
            or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
        )
        .order_by(Fixture.season, Fixture.week,
                  case((Fixture.competition == Competition.CUP, 0), else_=1), Fixture.id)
        .limit(1)
    )
    if st is not None:
        stmt = stmt.where(Fixture.season == st.season)
    return db.scalar(stmt)


def _team_preview(db, fx: Fixture, team: Team, is_home: bool, viewer_id: int | None,
                  matches: list[mp.PlayedMatch], names: dict[int, str], week: int, fog: _Fog
                  ) -> tuple[mp.TeamPreview, float, str]:
    league_name, position, size = _league_standing(db, team)
    form = mp.form_summary(matches, team.id, names)
    home_record, away_record = mp.venue_records(matches, team.id)
    injured, suspended = _absentees(team, week, fx.competition, fog)
    lineup = expected_lineup(team, is_home, week, fx.competition)
    strength = mp.lineup_strength(p.overall for p in lineup.on_pitch)
    yellow, red = _cards(db, team.id, fx.season)
    preview = mp.TeamPreview(
        team_id=team.id, name=team.name, is_home=is_home, is_viewer=team.id == viewer_id,
        league_name=league_name, league_position=position, league_size=size,
        points=team.points, played=team.played, formation=_team_formation(team),
        team_stars=mp.team_stars(strength),
        form=form.form, form_goals_for=form.goals_for, form_goals_against=form.goals_against,
        recent_matches=form.matches, home_record=home_record, away_record=away_record,
        injured=injured, suspended=suspended,
        players_to_watch=tuple(mp.players_to_watch(
            _season_lines(db, team, fx.season, week, fx.competition, fog))),
        tendencies=mp.team_tendencies(matches, team.id, yellow, red),
    )
    return preview, strength, form.form


def build_match_preview(db, fixture_id: int, viewer_team_id: int | None) -> mp.MatchPreview:
    """
    Mac onu raporu (salt okunur). viewer_team_id: raporu okuyan kulup (gozlemci sisi onun gozlemcisiyle);
    maca dahil olmasa da calisir (iki takim da sisli gorunur).
    """
    fx = db.get(Fixture, fixture_id)
    if fx is None:
        raise ValueError(f"Fikstür bulunamadı (#{fixture_id}).")
    home, away = fx.home_team, fx.away_team
    viewer = db.get(Team, viewer_team_id) if viewer_team_id is not None else None
    fog = _Fog(viewer)
    week = _availability_week(db, fx)

    matches = _season_matches(db, fx.season, [home.id, away.id])
    meetings = _meetings(db, home.id, away.id)
    ids = {home.id, away.id} | {t for m in matches for t in (m.home_team_id, m.away_team_id)}
    names = _team_names(db, ids)

    home_view, home_strength, home_form = _team_preview(db, fx, home, True, viewer_team_id, matches, names, week, fog)
    away_view, away_strength, away_form = _team_preview(db, fx, away, False, viewer_team_id, matches, names, week, fog)
    verdict = mp.match_verdict(home.name, away.name, home_strength, away_strength,
                               _home_advantage(fx, home), home_form, away_form)
    favorite = {"home": home.id, "away": away.id}.get(verdict.favorite)
    label = competition_label(fx)
    return mp.MatchPreview(
        fixture_id=fx.id, season=fx.season, week=fx.week,
        competition=mp.CUP if fx.competition is Competition.CUP else mp.LEAGUE,
        competition_label=label, title=f"{label} · {fx.week}. hafta · {home.name} - {away.name}",
        neutral_venue=bool(fx.neutral_venue), viewer_team_id=viewer_team_id,
        home=home_view, away=away_view,
        head_to_head=mp.head_to_head(meetings, home.id, away.id, names),
        verdict=verdict.text, favorite_team_id=favorite,
    )


# ===========================================================================
# 2) GOZLEMCI RAPORU
# ===========================================================================

def scout_opposition(db, fixture_id: int, viewer_team_id: int, rng_seed: int = 0) -> mp.ScoutReport:
    """
    Rakibin tahmini dizilisi ve ilk 11'i. Dogru tahmin = motorun kadro secimi (expected_lineup); izleyenin
    en iyi gozlemcisine (judging_ability) gore dizilis yanlis tahmin edilebilir ve oyuncular makul
    alternatiflerle yer degistirir (match_preview.scout_accuracy). Ayni tohum + fikstur + oyun haftasi
    + izleyen -> ayni rapor. Rakip oyuncular yalnizca gozlemci sisli yildizla gosterilir.
    """
    fx = db.get(Fixture, fixture_id)
    if fx is None:
        raise ValueError(f"Fikstür bulunamadı (#{fixture_id}).")
    if not fx.involves(viewer_team_id):
        raise ValueError("Gözlemci raporu yalnızca takımının oynayacağı maç için hazırlanır.")
    viewer = db.get(Team, viewer_team_id)
    opponent_is_home = fx.away_team_id == viewer_team_id
    opponent = fx.home_team if opponent_is_home else fx.away_team
    week = _availability_week(db, fx)
    game_week = _game_week(db, fx)

    fog = _Fog(viewer)
    accuracy = mp.scout_accuracy(fog.rating)
    confidence = mp.confidence_label(accuracy)
    rng = random.Random(mp.scout_seed(rng_seed, fx.id, game_week, viewer_team_id))

    formation = mp.predict_formation(rng, _team_formation(opponent), tuple(FORMATIONS), accuracy)
    lineup = expected_lineup(opponent, opponent_is_home, week, fx.competition, formation)
    starters = sorted(lineup.on_pitch, key=lambda p: (_ROLE_INDEX[p.role], -p.selection_power, p.id))
    alternatives = sorted((p for p in lineup.players if not p.on_pitch and p.matchday),
                          key=lambda p: (-p.selection_power, p.id))
    predicted = mp.scramble_lineup(
        rng,
        [mp.LineupSlot(p.id, p.position.value, p.role.value) for p in starters],
        [mp.Candidate(p.id, p.position.value) for p in alternatives],
        accuracy,
    )

    by_id = {p.id: p for p in opponent.players}
    xi = tuple(
        mp.ScoutedPlayer(
            player_id=slot.player_id, name=by_id[slot.player_id].name, position=slot.position,
            role=slot.role, age=by_id[slot.player_id].age, stars=fog.stars(by_id[slot.player_id]),
        )
        for slot in sorted(predicted, key=lambda s: _ROLE_INDEX[Position(s.role)])
    )
    injured, suspended = _absentees(opponent, week, fx.competition, fog)
    absentees = injured + tuple(a for a in suspended if a.player_id not in {i.player_id for i in injured})
    matches = _season_matches(db, fx.season, [opponent.id])
    yellow, red = _cards(db, opponent.id, fx.season)
    return mp.ScoutReport(
        fixture_id=fx.id, week=game_week, viewer_team_id=viewer_team_id,
        opponent_team_id=opponent.id, opponent_name=opponent.name, opponent_is_home=opponent_is_home,
        scout_name=fog.scout.name if fog.scout is not None else None,
        accuracy=accuracy, confidence=confidence, predicted_formation=formation, predicted_xi=xi,
        absentees=absentees,
        players_to_watch=tuple(mp.players_to_watch(
            _season_lines(db, opponent, fx.season, week, fx.competition, fog))),
        tendencies=mp.team_tendencies(matches, opponent.id, yellow, red),
        notes=mp.scout_notes(fog.scout is not None, confidence, len(absentees), formation),
    )


# ===========================================================================
# 3) KADRO PLANLAYICI
# ===========================================================================

def build_squad_plan(db, team_id: int, horizon_seasons: int = sp.DEFAULT_HORIZON,
                     include_academy: bool = True) -> sp.SquadPlan:
    """
    Kulubun kadro plani (salt okunur): A takim mevki gruplari + (istege bagli) akademi adaylari.
    Potansiyel kulubun gozlemcisinin tahminidir (CareerManager.potential_estimate; gercek tavan gizli).
    """
    team = db.get(Team, team_id)
    if team is None:
        raise ValueError(f"Takım bulunamadı (#{team_id}).")
    st = _state(db)
    season = st.season if st is not None else 1
    week = st.current_week if st is not None else 1
    cm = CareerManager(db)          # yalnizca potential_estimate (yazmaz, state'e dokunmaz)

    def row(p: Player) -> sp.PlannerPlayer:
        low, high = cm.potential_estimate(team, p)
        return sp.PlannerPlayer(
            player_id=p.id, name=p.name, position=p.position.value, age=p.age, overall=p.overall_rating,
            potential_low=low, potential_high=high, contract_years=p.contract_years,
            injured=p.is_injured(week), in_academy=p.in_academy,
        )

    seniors = [row(p) for p in team.players]
    academy: list[sp.PlannerPlayer] = []
    if include_academy:
        academy = [row(p) for p in db.scalars(
            select(Player).where(Player.team_id == team.id, Player.in_academy.is_(True)).order_by(Player.id)
        )]
    return sp.plan_squad(team.id, team.name, season, team.formation, seniors, academy,
                         horizon_seasons, squad_max=SENIOR_SQUAD_MAX)
