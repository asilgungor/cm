"""
career_views.py
===============
Kariyer paneli gorunum modelleri (7. Asama).

Web arayuzu (web_app.py) veritabanindan ORM nesneleri degil, bu modulun urettigi
DUZ veri satirlarini gosterir. CLI'daki render_* fonksiyonlarinin veri tarafi
buraya tasindi; boylece ileride baska bir arayuz (2D istemci, API) ayni satirlari
kullanabilir ve bu donusumler Streamlit olmadan test edilir.

Transfer pazari gozlemci SISINE SADIK kalir: filtreleme ve siralama gercek
degerlerle degil, gozlemcinin tahmin araliklarinin orta noktasiyla yapilir.
Aksi halde "OVR'ye gore sirala" gizli bilgiyi sizdirirdi.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

import fitness
import reputation
import staff as staff_rules
from career_manager import CareerManager
from finance import format_money
from models import LineupStatus, Player, Position, Staff, StaffRole, Team
from transfers import ROLE_LABELS

STATUS_LABELS = {LineupStatus.XI: "İlk 11", LineupStatus.BENCH: "Kulübe", LineupStatus.OUT: "Kadro dışı"}
STATUS_BY_LABEL = {v: k for k, v in STATUS_LABELS.items()}
POSITION_ORDER = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}


# ===========================================================================
# 1) KADRO
# ===========================================================================

@dataclass
class SquadRow:
    id: int
    name: str
    position: str
    age: int
    overall: int
    form: int
    morale: int
    condition: int
    condition_band: str
    status: str                        # "İlk 11" / "Kulübe" / "Kadro dışı"
    slot: str | None                   # ilk 11'de oynayacagi rol
    unavailable: str | None            # "sakat, 5. haftada dönüyor" / "cezalı, 1 maç"
    average_rating: float | None
    weeks_idle: int
    contract_years: int
    wage: int
    market_value: int
    squad_role: str

    @property
    def low_condition(self) -> bool:
        return self.condition < fitness.CONDITION_WARN


def squad_rows(team: Team, week: int) -> list[SquadRow]:
    players = sorted(team.players, key=lambda p: (POSITION_ORDER[p.position], -p.overall_rating))
    rows = []
    for p in players:
        condition = int(getattr(p, "condition", 100))
        rows.append(SquadRow(
            id=p.id,
            name=p.name,
            position=p.position.value,
            age=p.age,
            overall=p.overall_rating,
            form=p.form,
            morale=p.morale,
            condition=condition,
            condition_band=fitness.condition_band(condition),
            status=STATUS_LABELS[p.lineup_status],
            slot=p.lineup_role.value if p.lineup_status is LineupStatus.XI and p.lineup_role else None,
            unavailable=p.unavailability_reason(week),
            average_rating=p.average_rating,
            weeks_idle=p.weeks_since_match,
            contract_years=p.contract_years,
            wage=p.current_wage,
            market_value=p.market_value,
            squad_role=ROLE_LABELS[p.squad_role],
        ))
    return rows


def lineup_from_editor(rows: list[dict]) -> tuple[dict[int, Position], list[int]]:
    """
    Tablo duzenleyicisinden gelen satirlari (id, Durum, Slot) kadro kararina cevirir.
    Ilk 11 isaretli ama slotu bos oyuncu kendi mevkisinde oynar.
    """
    xi: dict[int, Position] = {}
    bench: list[int] = []
    for row in rows:
        status = STATUS_BY_LABEL.get(row.get("Durum"), LineupStatus.OUT)
        if status is LineupStatus.XI:
            slot = row.get("Slot") or row.get("Mv")
            xi[int(row["id"])] = Position(slot)
        elif status is LineupStatus.BENCH:
            bench.append(int(row["id"]))
    return xi, bench


def tired_starters(rows: list[SquadRow]) -> list[SquadRow]:
    return [r for r in rows if r.status == "İlk 11" and r.low_condition]


# ===========================================================================
# 2) LIG
# ===========================================================================

def standings_rows(cm: CareerManager, league_id: int, highlight_id: int | None = None) -> list[dict]:
    return [
        {
            "#": i,
            "Takım": ("► " if t.id == highlight_id else "") + t.name,
            "O": t.played, "G": t.won, "B": t.drawn, "M": t.lost,
            "A": t.goals_for, "Y": t.goals_against, "Av": t.goal_difference, "P": t.points,
            "Form": cm.team_form(t.id) or "-",
        }
        for i, t in enumerate(cm.standings(league_id), start=1)
    ]


def scorer_rows(cm: CareerManager, league_id: int | None = None) -> list[dict]:
    return [
        {"#": i, "Oyuncu": r.player.name, "Takım": r.team.name, "Gol": r.goals,
         "Asist": r.assists, "Maç": r.appearances, "Ort. not": r.avg_rating}
        for i, r in enumerate(cm.top_scorers(league_id), start=1)
    ]


def result_rows(cm: CareerManager, week: int | None) -> list[dict]:
    if week is None:
        return []
    return [
        {"Lig": fx.league.name, "Ev sahibi": fx.home_team.name,
         "Skor": f"{fx.home_score} - {fx.away_score}", "Deplasman": fx.away_team.name}
        for fx in cm.results_for_week(week)
    ]


def week_report_lines(report) -> list[tuple[str, str]]:
    """WeekReport -> (tur, metin). tur: 'result' / 'injury' / 'ban' / 'transfer' / 'info' / 'season'."""
    if not report.played_any:
        return [("info", "Oynanacak maç yok — sezon tamamlandı.")]
    lines: list[tuple[str, str]] = [("info", f"Sezon {report.season}, {report.week}. hafta oynandı.")]
    for _fx, r in report.results:
        lines.append(("result", f"{r.home.name} {r.home_score} - {r.away_score} {r.away.name}"))
    lines += [("injury", f"Sakatlık: {n.player_name} ({n.team_name}) — {n.detail}") for n in report.injuries]
    lines += [("ban", f"Ceza: {n.player_name} ({n.team_name}) — {n.detail}") for n in report.suspensions]
    lines += [("transfer", f"Transfer: {n.describe()}") for n in report.transfers]
    lines += [("info", f"Asistan: {note}") for note in report.lineup_notes]
    if report.finance_note:
        lines.append(("info", report.finance_note))
    if report.manager_reputation is not None:
        before, after = report.manager_reputation
        lines.append(("info", f"Menajer tanınırlığı {before:.2f} → {after:.2f} ({reputation.label(after)})"))
    if report.season_finished:
        lines.append(("season", "Sezon tamamlandı!"))
    return lines


# ===========================================================================
# 3) TRANSFER PAZARI (gozlemci sisine sadik)
# ===========================================================================

@dataclass
class MarketFilter:
    name: str = ""
    positions: set[str] = field(default_factory=set)       # {"GK", "FWD"}; bos = hepsi
    max_age: int = 45
    min_estimated_overall: int = 1
    max_estimated_value: int | None = None                 # EUR; None = sinirsiz
    limit: int = 40


@dataclass
class MarketRow:
    id: int
    name: str
    club: str
    position: str
    age: int
    overall_low: int
    overall_high: int
    value_low: int
    value_high: int
    exact: bool
    contract_years: int

    @property
    def overall_estimate(self) -> int:
        return (self.overall_low + self.overall_high) // 2

    @property
    def value_estimate(self) -> int:
        return (self.value_low + self.value_high) // 2

    @property
    def overall_text(self) -> str:
        return str(self.overall_low) if self.exact else f"{self.overall_low}-{self.overall_high}"

    @property
    def value_text(self) -> str:
        if self.exact:
            return format_money(self.value_low)
        return f"{format_money(self.value_low)} - {format_money(self.value_high)}"

    def label(self) -> str:
        return f"{self.name} · {self.club} · {self.position} · OVR ~{self.overall_text}"


def market_rows(cm: CareerManager, buyer: Team, flt: MarketFilter) -> list[MarketRow]:
    """Diger kuluplerin oyunculari; filtre ve siralama gozlemci TAHMINLERI uzerinden."""
    players = cm.db.scalars(
        select(Player).where(Player.team_id.isnot(None), Player.team_id != buyer.id)
    ).all()
    needle = flt.name.strip().casefold()
    rows: list[MarketRow] = []
    for p in players:
        if needle and needle not in p.name.casefold():
            continue
        if flt.positions and p.position.value not in flt.positions:
            continue
        if p.age > flt.max_age:
            continue
        report = cm.scouted_report(buyer, p)
        ovr, value = report["overall_rating"], report["market_value"]
        row = MarketRow(
            id=p.id, name=p.name, club=p.team.name, position=p.position.value, age=p.age,
            overall_low=ovr.low, overall_high=ovr.high, value_low=value.low, value_high=value.high,
            exact=ovr.exact, contract_years=p.contract_years,
        )
        if row.overall_estimate < flt.min_estimated_overall:
            continue
        if flt.max_estimated_value is not None and row.value_estimate > flt.max_estimated_value:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (-r.overall_estimate, r.name))
    return rows[: flt.limit]


def scouted_profile_rows(cm: CareerManager, buyer: Team, player: Player) -> list[dict]:
    report = cm.scouted_report(buyer, player)
    labels = (("overall_rating", "Genel"), ("pace", "Hız"), ("shooting", "Şut"), ("passing", "Pas"),
              ("defending", "Defans"), ("dribbling", "Dribling"), ("goalkeeping", "Kalecilik"))
    return [{"Özellik": label, "Tahmin": str(report[key])} for key, label in labels]


def suggested_opening_fee(row: MarketRow) -> int:
    """Teklif kutusunun baslangic degeri: tahmini degerin %120'si, 100K'ya yuvarli."""
    return int(round(row.value_estimate * 1.2 / 100_000) * 100_000)


# ===========================================================================
# 4) TEKNIK HEYET
# ===========================================================================

def staff_rows(members: list[Staff]) -> list[dict]:
    return [
        {
            "id": m.id,
            "Rol": staff_rules.ROLE_LABELS[m.role],
            "İsim": m.name,
            "İtibar": m.reputation,
            "Maaş/hf": format_money(m.wage),
            "Özellikler": "  ".join(
                f"{staff_rules.ATTR_LABELS[a]} {getattr(m, a)}" for a in staff_rules.ROLE_ATTRIBUTES[m.role]
            ),
        }
        for m in members
    ]


@dataclass
class StaffEffects:
    injury_multiplier: float
    four_week_injury: int
    scout_margin: int
    attack_training: float
    defense_training: float
    morale_training: float
    recovery_rate: float


def staff_effects(cm: CareerManager, team: Team) -> StaffEffects:
    physio = cm.physio_rating(team)
    return StaffEffects(
        injury_multiplier=staff_rules.injury_multiplier(physio),
        four_week_injury=staff_rules.apply_injury_multiplier(4, physio),
        scout_margin=cm.scout_margin(team),
        attack_training=staff_rules.training_multiplier(cm._staff_rating(team, StaffRole.COACH, "attacking")),
        defense_training=staff_rules.training_multiplier(cm._staff_rating(team, StaffRole.COACH, "defending")),
        morale_training=staff_rules.training_multiplier(cm._staff_rating(team, StaffRole.ASSISTANT, "man_management")),
        recovery_rate=fitness.recovery_rate(physio),
    )
