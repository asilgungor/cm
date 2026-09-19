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

Yildiz sistemi (10. Asama): kadro, pazar ve akademi satirlari sayisal gucu gostermez;
guc ve potansiyel stars.py ile yildiza cevrilir (satirlarda sayilar yalnizca siralama ve
filtre icin tutulur, ekrana yildiz metni gider). Potansiyel her zaman gozlemci tahminidir
(CareerManager.potential_estimate): gercek tavan gizlidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

import fitness
import reputation
import staff as staff_rules
from career_manager import CareerManager
from development import is_wonderkid
from finance import format_money
from models import LineupStatus, Player, Position, Staff, StaffRole, Team
from stars import star_range, stars
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
    potential_low: int | None = None       # gozlemci tahmini (cm verilirse)
    potential_high: int | None = None
    wonderkid: bool = False
    squad_role_key: str = ""               # SquadRole degeri (STAR / FIRST_TEAM / BACKUP): CM statu etiketi icin

    @property
    def low_condition(self) -> bool:
        return self.condition < fitness.CONDITION_WARN

    @property
    def stars(self) -> str:
        return stars(self.overall)

    @property
    def potential_stars(self) -> str:
        return star_range(self.potential_low, self.potential_high)


def _potential(cm: CareerManager | None, team: Team, p: Player) -> tuple[int | None, int | None, bool]:
    """Gozlemci tahmini potansiyel araligi ve (tahmine gore) wonderkid isareti."""
    if cm is None:
        return None, None, False
    low, high = cm.potential_estimate(team, p)
    return low, high, is_wonderkid(p.age, p.overall_rating, (low + high) // 2)


def squad_rows(team: Team, week: int, cm: CareerManager | None = None) -> list[SquadRow]:
    """A takim kadrosu. cm verilirse potansiyel tahmini ve wonderkid isareti de doldurulur."""
    players = sorted(team.players, key=lambda p: (POSITION_ORDER[p.position], -p.overall_rating))
    rows = []
    for p in players:
        condition = int(getattr(p, "condition", 100))
        pot_low, pot_high, wonder = _potential(cm, team, p)
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
            squad_role_key=p.squad_role.value,
            potential_low=pot_low,
            potential_high=pot_high,
            wonderkid=wonder,
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
    """WeekReport -> (tur, metin). tur: 'result' / 'injury' / 'ban' / 'transfer' / 'desk' / 'info' / 'season'."""
    if not report.played_any:
        return [("info", "Oynanacak maç yok — sezon tamamlandı.")]
    lines: list[tuple[str, str]] = [("info", f"Sezon {report.season}, {report.week}. hafta oynandı.")]
    for _fx, r in report.results:
        lines.append(("result", f"{r.home.name} {r.home_score} - {r.away_score} {r.away.name}"))
    lines += [("injury", f"Sakatlık: {n.player_name} ({n.team_name}) — {n.detail}") for n in report.injuries]
    lines += [("ban", f"Ceza: {n.player_name} ({n.team_name}) — {n.detail}") for n in report.suspensions]
    lines += [("transfer", f"Transfer: {n.describe()}") for n in report.transfers]
    lines += [("info", f"Asistan: {note}") for note in report.lineup_notes]
    lines += [("growth", development_line(n)) for n in getattr(report, "development_notes", None) or []]
    lines += [("youth", f"🎓 Akademi: {note}") for note in getattr(report, "academy_notes", None) or []]
    intake = getattr(report, "youth_intake", None) or []
    if intake:
        lines.append(("youth", f"🎓 Genç girişi: akademine {len(intake)} yeni oyuncu katıldı "
                               "(ayrıntılar 🎓 Akademi sayfasında)."))
    if report.finance_note:
        lines.append(("info", report.finance_note))
    lines += [("season", f"🏆 {note}") for note in getattr(report, "honours_notes", None) or []]
    lines += [("info", f"💶 {note}") for note in getattr(report, "prize_notes", None) or []]
    lines += [("concern", f"😟 {n.player_name}: {n.detail}") for n in getattr(report, "concern_notes", None) or []]
    lines += [("concern", f"✍️ Maaş talebi: {n.player_name} — {n.detail} (📋 Kadro sayfasında cevapla)")
              for n in getattr(report, "wage_demands", None) or []]
    # 13H/13I transfer masasi: gelen teklifler, kulup yanitlari, taksit / ek odeme / prim, tamamlanan anlasmalar.
    # Tur 'desk': metin veritabanindan (kulup / oyuncu adlari) -> web_app.week_report_block kacisli yazar.
    lines += [("desk", f"🔄 {note}") for note in getattr(report, "transfer_notes", None) or []]
    cup_label = getattr(report, "cup_label", None)          # rapor nesnesi duck-typed (testler)
    if cup_label:
        lines.append(("info", f"⭐ {cup_label}: {len(report.cup_results)} maç oynandı "
                              f"(ayrıntılar ⭐ Devler Arenası sayfasında)."))
    if getattr(report, "user_cup_result", None) is not None:
        lines.append(("result", "Kupa: " + match_score_text(report.user_cup_result)))
    if report.manager_reputation is not None:
        before, after = report.manager_reputation
        lines.append(("info", f"Menajer tanınırlığı {before:.2f} → {after:.2f} ({reputation.label(after)})"))
    if report.season_finished:
        lines.append(("season", "Sezon tamamlandı!"))
    return lines


def development_line(note) -> str:
    """Gelisim/yaslanma notu, sayisal guc yerine yildizla (yildiz degismediyse ok isaretiyle)."""
    old, new = getattr(note, "old_overall", None), getattr(note, "new_overall", None)
    if old is None or new is None:
        return f"Gelişim: {note.player_name}"
    arrow = "↑" if new > old else "↓"
    change = f"{stars(old)} → {stars(new)}" if stars(old) != stars(new) else f"{stars(new)} {arrow}"
    kind = "Gelişim" if new > old else "Yaşlanma"
    potential = ""
    if getattr(note, "potential_low", None) is not None and new > old:
        potential = f" · potansiyel {star_range(note.potential_low, note.potential_high)}"
    where = " · akademi" if getattr(note, "in_academy", False) else ""
    return f"{kind}: {note.player_name} ({getattr(note, 'age', '?')}) {change}{potential}{where}"


def match_score_text(result) -> str:
    """'A 1 - 1 B (uzt., pen. 4-3)' -- uzatma/penalti bilgisi motor sonucundan."""
    text = f"{result.home.name} {result.home_score} - {result.away_score} {result.away.name}"
    extras = []
    if getattr(result, "extra_time", False):
        extras.append("uzt.")
    if getattr(result, "shootout", None) is not None:
        extras.append(f"pen. {result.home_penalties}-{result.away_penalties}")
    return text + (f" ({', '.join(extras)})" if extras else "")


def cup_report_lines(report) -> list[tuple[str, str]]:
    """Haftanin Devler Arenasi ozeti: sonuclar (uzatma/penalti), tur atlayanlar, sampiyon."""
    if not report.cup_label and not report.cup_notes:
        return []
    lines: list[tuple[str, str]] = [("info", f"⭐ {report.cup_label or 'Devler Arenası'} · {report.week}. hafta")]
    lines += [("result", match_score_text(r)) for _fx, r in report.cup_results]
    lines += [("info", note) for note in report.cup_notes if not note.startswith("🏆")]
    if report.cup_champion is not None:
        lines.append(("season", f"🏆 {report.cup_champion.name} Devler Arenası şampiyonu!"))
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
    def stars_text(self) -> str:
        """Ekranda sayi yerine: gozlemci araligi yildizla (iki uc ayni yildizdaysa tek deger)."""
        return star_range(self.overall_low, self.overall_high)

    @property
    def value_text(self) -> str:
        if self.exact:
            return format_money(self.value_low)
        return f"{format_money(self.value_low)} - {format_money(self.value_high)}"

    def label(self) -> str:
        return f"{self.name} · {self.club} · {self.position} · {self.stars_text}"


def market_rows(cm: CareerManager, buyer: Team, flt: MarketFilter) -> list[MarketRow]:
    """Diger kuluplerin oyunculari; filtre ve siralama gozlemci TAHMINLERI uzerinden."""
    players = cm.db.scalars(
        select(Player).where(Player.team_id.isnot(None), Player.team_id != buyer.id,
                             Player.in_academy.is_(False))          # akademiler satilik degil
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
    """Gozlemci raporu: her ozellik tahmin araligi YILDIZLA (sayi gosterilmez), potansiyel dahil."""
    report = cm.scouted_report(buyer, player)
    labels = (("overall_rating", "Genel"), ("pace", "Hız"), ("shooting", "Şut"), ("passing", "Pas"),
              ("defending", "Defans"), ("dribbling", "Dribling"), ("goalkeeping", "Kalecilik"))
    rows = [{"Özellik": label, "Tahmin": star_range(report[key].low, report[key].high)} for key, label in labels]
    low, high = cm.potential_estimate(buyer, player)
    rows.insert(1, {"Özellik": "Potansiyel yetenek", "Tahmin": star_range(low, high)})
    return rows


def suggested_opening_fee(row: MarketRow) -> int:
    """Teklif kutusunun baslangic degeri: tahmini degerin %120'si, 100K'ya yuvarli."""
    return int(round(row.value_estimate * 1.2 / 100_000) * 100_000)


# ===========================================================================
# 3b) ALTYAPI AKADEMISI (U-21)
# ===========================================================================

ACADEMY_SORTS = ("Potansiyel yetenek (tahmin)", "Mevcut yetenek", "Yaş")


@dataclass
class AcademyFilter:
    positions: set[str] = field(default_factory=set)
    wonderkids_only: bool = False
    sort: str = ACADEMY_SORTS[0]


@dataclass
class AcademyRow:
    id: int
    name: str
    age: int
    position: str
    overall: int
    potential_low: int
    potential_high: int
    wonderkid: bool
    form: int
    morale: int
    unavailable: str | None

    @property
    def potential_estimate(self) -> int:
        return (self.potential_low + self.potential_high) // 2

    @property
    def stars(self) -> str:
        return stars(self.overall)

    @property
    def potential_stars(self) -> str:
        return star_range(self.potential_low, self.potential_high)

    def label(self) -> str:
        badge = "🌟 " if self.wonderkid else ""
        return f"{badge}{self.name} · {self.age} yaş · {self.position} · {self.stars}"

    def to_dict(self) -> dict:
        return {
            "Oyuncu": ("🌟 " if self.wonderkid else "") + self.name,
            "Yaş": self.age,
            "Mv": self.position,
            "Mevcut yetenek": self.stars,
            "Potansiyel yetenek (gözlemci)": self.potential_stars,
            "Durum": "Wonderkid" if self.wonderkid else (self.unavailable or ""),
        }


def _academy_row(cm: CareerManager, team: Team, p: Player, week: int) -> AcademyRow:
    low, high = cm.potential_estimate(team, p)
    return AcademyRow(
        id=p.id, name=p.name, age=p.age, position=p.position.value, overall=p.overall_rating,
        potential_low=low, potential_high=high,
        wonderkid=is_wonderkid(p.age, p.overall_rating, (low + high) // 2),
        form=p.form, morale=p.morale, unavailable=p.unavailability_reason(week),
    )


def academy_rows(cm: CareerManager, team: Team, flt: AcademyFilter | None = None) -> list[AcademyRow]:
    """U-21 akademi oyunculari; filtre ve siralama gozlemci tahmini potansiyel uzerinden."""
    flt = flt or AcademyFilter()
    rows = [_academy_row(cm, team, p, cm.current_week) for p in cm.academy_players(team)]
    rows = [r for r in rows
            if (not flt.positions or r.position in flt.positions) and (not flt.wonderkids_only or r.wonderkid)]
    keys = {
        "Mevcut yetenek": lambda r: (-r.overall, -r.potential_estimate, r.name),
        "Yaş": lambda r: (r.age, -r.potential_estimate, r.name),
    }
    rows.sort(key=keys.get(flt.sort, lambda r: (-r.potential_estimate, -r.overall, r.name)))
    return rows


def demotion_rows(cm: CareerManager, team: Team) -> list[AcademyRow]:
    """U-21'e gonderilebilecek A takim oyunculari (once gencler, sonra formu dusukler)."""
    rows = [_academy_row(cm, team, p, cm.current_week) for p in team.players]
    rows.sort(key=lambda r: (r.age > 21, r.form, r.name))
    return rows


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
