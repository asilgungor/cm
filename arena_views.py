"""
arena_views.py
==============
Devler Arenasi (Champions Cup) sekmesi icin goruntu modelleri (8. Asama).

TournamentManager'in ORM nesnelerini bracket_view'in saf dataclass'larina ve
st.dataframe'e verilecek sozluk satirlarina cevirir. Streamlit bilmez; ayni
fonksiyonlar ileride 2D arayuzde de kullanilabilir.
"""

from __future__ import annotations

from dataclasses import dataclass

from bracket_view import BallView, GroupRowView, PotView, RoundView, SlotView, TieView
from cup_draw import STAGE_LABELS, CupFormat, Stage, slot_title
from models import Fixture, Tournament, TournamentStatus
from tournament_manager import GROUP_LABELS, TournamentManager

TIE_COUNTS = {Stage.R16: 8, Stage.QF: 4, Stage.SF: 2, Stage.FINAL: 1}
KICK_ICONS = {"scored": "⚽", "saved": "🧤", "missed": "❌"}
KICK_WORDS = {"scored": "gol", "saved": "kaleci kurtardı", "missed": "kaçırdı"}
POT_LABELS = {
    CupFormat.KNOCKOUT: ("1. Torba · Seri başları", "2. Torba"),
    CupFormat.GROUPS: ("1. Torba", "2. Torba", "3. Torba", "4. Torba"),
}


# ===========================================================================
# KURA PANOSU
# ===========================================================================

def draw_board(tm: TournamentManager, t: Tournament) -> tuple[list[PotView], list[SlotView], str, str | None]:
    """(torbalar, eslesme/grup kartlari, siradaki adim metni, son cekilen top duyurusu)."""
    fmt = tm.fmt(t)
    session = tm.draw_session(t)
    names = {e.team_id: e.team.name for e in t.entries}
    steps = session.steps if session else []
    last = steps[-1] if steps else None
    drawn = {step.team_id for step in steps}

    pots: list[PotView] = []
    labels = POT_LABELS.get(fmt, ())
    pot_count = max((e.pot for e in t.entries), default=-1) + 1
    for pot_index in range(pot_count):
        members = sorted((e for e in t.entries if e.pot == pot_index), key=lambda e: e.seed_rank)
        opened = [e for e in members if e.team_id in drawn]
        closed = [e for e in members if e.team_id not in drawn]
        balls = [
            BallView(names[e.team_id], "glow" if last is not None and last.team_id == e.team_id else "open")
            for e in opened
        ] + [BallView("?", "closed") for _ in closed]
        label = labels[pot_index] if pot_index < len(labels) else f"{pot_index + 1}. Torba"
        pots.append(PotView(label, balls))

    slots: list[SlotView] = []
    for index, team_ids in enumerate(session.slots() if session else []):
        slots.append(SlotView(
            title=slot_title(fmt, index),
            teams=[names.get(team_id) if team_id is not None else None for team_id in team_ids],
            glow=last is not None and last.slot == index,
        ))

    headline = "Kura tamamlandı" if session is None or session.complete else session.headline()
    announcement = session.describe_step(last) if last is not None else None
    return pots, slots, headline, announcement


@dataclass
class PairReveal:
    """Kura gecesi karti: az once acilan eslesme (ev sahibi / deplasman) ya da grup kurasinda acilan top."""
    title: str
    home: str
    away: str | None               # grup kurasinda ya da eslesmenin ilk topunda None
    detail: str | None = None      # "1. maç 2. hafta · rövanş 3. hafta"


def pair_reveal(tm: TournamentManager, t: Tournament) -> PairReveal | None:
    """Son tiklamada acilan toplar. KNOCKOUT: ilk top ilk macin ev sahibi, ikinci top deplasman."""
    session = tm.draw_session(t)
    last = session.last_step if session else None
    if last is None:
        return None
    fmt = tm.fmt(t)
    names = {e.team_id: e.team.name for e in t.entries}
    title = slot_title(fmt, last.slot)
    first_stage = tm.stages(t)[0]
    if fmt is CupFormat.GROUPS:
        return PairReveal(title, names[last.team_id], None, f"{last.pot + 1}. torbadan · {title}")
    if last.partner_id is None:
        return PairReveal(title, names[last.team_id], None, "rakibi bekleniyor")
    legs = [f"1. maç {tm.matchday_week(t, first_stage, 1)}. hafta"]
    if first_stage is not Stage.FINAL:
        legs.append(f"rövanş {tm.matchday_week(t, first_stage, 2)}. hafta")
    return PairReveal(title, names[last.partner_id], names[last.team_id], " · ".join(legs))


def draw_progress(tm: TournamentManager, t: Tournament) -> tuple[int, int, str]:
    """(tamamlanan, toplam, birim): KNOCKOUT 'eşleşme', GROUPS 'top'."""
    session = tm.draw_session(t)
    if session is None:
        return 0, 0, "eşleşme"
    unit = "eşleşme" if tm.fmt(t) is CupFormat.KNOCKOUT else "top"
    return session.pairs_drawn, session.pair_count, unit


def locked_fixture_rows(tm: TournamentManager, t: Tournament) -> list[dict]:
    """Kesinlesen kuranin ilk tur fiksturu (kilitli): hafta, mac, ev sahibi, deplasman."""
    if t.status is TournamentStatus.DRAW:
        return []
    stage = tm.stages(t)[0]
    rows = []
    for fx in tm.fixtures(t, stage=stage):
        if stage is Stage.GROUP:
            label = f"Grup maçı {fx.leg}"
        else:
            label = "Final" if stage is Stage.FINAL else ("1. maç" if fx.leg == 1 else "Rövanş")
        rows.append({"Hafta": fx.week, "Maç": label, "Ev sahibi": fx.home_team.name,
                     "Deplasman": fx.away_team.name,
                     "Durum": "oynandı" if fx.is_played else "🔒 kilitli"})
    return rows


def pot_remaining_names(tm: TournamentManager, t: Tournament) -> list[tuple[str, list[str]]]:
    """Her torbada henuz cekilmemis takimlar (torbanin icerigi kurada bilinir)."""
    session = tm.draw_session(t)
    labels = POT_LABELS.get(tm.fmt(t), ())
    rows = []
    pot_count = max((e.pot for e in t.entries), default=-1) + 1 if session else 0
    for pot_index in range(pot_count):
        label = labels[pot_index] if pot_index < len(labels) else f"{pot_index + 1}. Torba"
        rows.append((label, [team.name for team in session.remaining(pot_index)]))
    return rows


# ===========================================================================
# TURNUVA AGACI VE GRUPLAR
# ===========================================================================

def _leg_text(fx: Fixture, first_team_id: int) -> str:
    if not fx.is_played:
        return f"{fx.week}. hf"
    if fx.home_team_id == first_team_id:
        text = f"{fx.home_score}-{fx.away_score}"
    else:
        text = f"{fx.away_score}-{fx.home_score}"
    return text + (" uzt." if fx.extra_time else "")


def bracket_rounds(tm: TournamentManager, t: Tournament, user_team_id: int | None) -> list[RoundView]:
    rounds: list[RoundView] = []
    for stage in (s for s in tm.stages(t) if s is not Stage.GROUP):
        by_slot = {tie.slot: tie for tie in tm.ties(t, stage)}
        ties: list[TieView] = []
        for slot in range(TIE_COUNTS[stage]):
            tie = by_slot.get(slot)
            if tie is None:
                ties.append(TieView(home=None, away=None))
                continue
            legs = sorted(tie.fixtures, key=lambda f: f.leg or 0)
            played = [f for f in legs if f.is_played]
            two_legged = stage is not Stage.FINAL
            note = None
            if tie.penalties_first is not None:
                note = f"pen. {tie.penalties_first}-{tie.penalties_second}"
            winner = None
            if tie.winner_team_id is not None:
                winner = "home" if tie.winner_team_id == tie.first_team_id else "away"
            ties.append(TieView(
                home=tie.first_team.name, away=tie.second_team.name,
                legs=[_leg_text(f, tie.first_team_id) for f in legs],
                aggregate=f"{tie.aggregate_first}-{tie.aggregate_second}" if two_legged and played else None,
                note=note, winner=winner, highlight=tie.involves(user_team_id),
            ))
        rounds.append(RoundView(STAGE_LABELS[stage], ties))
    return rounds


def group_views(tm: TournamentManager, t: Tournament, user_team_id: int | None) -> list[tuple[str, list[GroupRowView]]]:
    if tm.fmt(t) is not CupFormat.GROUPS or t.status.value == "DRAW":
        return []
    names = {e.team_id: e.team.name for e in t.entries}
    groups = []
    for index, rows in enumerate(tm.group_rankings(t)):
        groups.append((f"Grup {GROUP_LABELS[index]}", [
            GroupRowView(
                position=pos, team=names[r.team_id], played=r.played, won=r.won, drawn=r.drawn,
                lost=r.lost, goals_for=r.goals_for, goals_against=r.goals_against, points=r.points,
                qualified=pos <= 2, highlight=r.team_id == user_team_id,
            )
            for pos, r in enumerate(rows, start=1)
        ]))
    return groups


# ===========================================================================
# SONUCLAR, KRALLIKLAR, SAKAT / CEZALI
# ===========================================================================

def score_text(fx: Fixture) -> str:
    text = f"{fx.home_score} - {fx.away_score}"
    if fx.extra_time:
        text += " (uzt.)"
    if fx.home_penalties is not None:
        text += f" · pen. {fx.home_penalties}-{fx.away_penalties}"
    return text


def result_rows(tm: TournamentManager, t: Tournament, week: int | None = None) -> list[dict]:
    """Oynanmis kupa maclari (week verilirse o hafta), en yeni once."""
    played = [fx for fx in tm.fixtures(t, week=week) if fx.is_played]
    played.sort(key=lambda fx: (-fx.week, fx.id))
    rows = []
    for fx in played:
        stage = STAGE_LABELS[Stage(fx.stage)]
        leg = "" if fx.stage == Stage.FINAL.value else (f" {fx.leg}. maç" if fx.stage == Stage.GROUP.value
                                                         else (" ilk maç" if fx.leg == 1 else " rövanş"))
        rows.append({"Hafta": fx.week, "Tur": stage + leg, "Ev sahibi": fx.home_team.name,
                     "Skor": score_text(fx), "Deplasman": fx.away_team.name, "id": fx.id})
    return rows


def shootout_lines(fx: Fixture) -> list[str]:
    """Penalti serisini vurus vurus metne cevirir (fixtures.key_events)."""
    lines = []
    for ev in fx.key_events or []:
        if ev.get("type") != "PENALTY_SHOOTOUT":
            continue
        outcome = ev.get("detail") or ""
        lines.append(
            f"{ev.get('kick_number') or len(lines) + 1}. {KICK_ICONS.get(outcome, '•')} "
            f"{ev.get('player') or '?'} ({ev.get('team') or '?'}) — {KICK_WORDS.get(outcome, outcome)} · "
            f"{ev.get('home_penalties', 0)}-{ev.get('away_penalties', 0)}"
        )
    return lines


def player_rows(tm: TournamentManager, t: Tournament, by: str = "goals", limit: int = 10) -> list[dict]:
    return [
        {"#": i, "Oyuncu": r.player.name, "Takım": r.team.name, "Gol": r.goals, "Asist": r.assists,
         "Maç": r.appearances, "Ort. not": r.avg_rating}
        for i, r in enumerate(tm.top_players(t, by=by, limit=limit), start=1)
    ]


UNAVAILABLE_LABELS = {"injury": "🩹 Sakat", "ban": "🟥 Cezalı", "risk": "🟨 Ceza sınırında"}


def unavailable_rows(tm: TournamentManager, t: Tournament, week: int | None = None) -> list[dict]:
    return [
        {"Durum": UNAVAILABLE_LABELS[r.kind], "Oyuncu": r.player.name, "Takım": r.team.name,
         "Mv": r.player.position.value, "Ayrıntı": r.reason}
        for r in tm.unavailable(t, week)
    ]


def participant_rows(tm: TournamentManager, t: Tournament, user_team_id: int | None) -> list[dict]:
    return [
        {"Sıra": e.seed_rank, "Takım": ("► " if e.team_id == user_team_id else "") + e.team.name,
         "Lig": e.league_name, "Katsayı": round(e.coefficient, 1), "Torba": e.pot + 1,
         "Durum": ("Elendi (" + ("Grup" if e.eliminated_stage == "GROUP" else STAGE_LABELS[Stage(e.eliminated_stage)]) + ")")
         if e.eliminated_stage else ("🏆 Şampiyon" if t.champion_team_id == e.team_id else "Turnuvada")}
        for e in sorted(t.entries, key=lambda e: e.seed_rank)
    ]
