"""
squad_planner.py
================
Kadro planlayici (Soccer Manager 27 tarzi). SAF MANTIK: veritabani ve Streamlit bilmez; girdi
PlannerPlayer satirlaridir (preview_views.build_squad_plan ORM'den doldurur, salt okunur).

Gizli guc kurali (stars.py): ciktida sayisal guc ya da potansiyel yoktur. Guc yildizla, potansiyel
gozlemci TAHMININ yildiz araligiyla (CareerManager.potential_estimate) gosterilir. Girdideki sayilar
yalnizca siralama ve bayraklar icindir.

Mevki gruplari models.Position ile ayni: GK, DEF, MID, FWD.

Onerilen asgari sayi = dizilisin ilk 11 ihtiyaci + yedek payi (BACKUP_COVER: GK 1, DEF 2, MID 2, FWD 1):
    4-4-2 -> GK 2 · DEF 6 · MID 6 · FWD 3   (17)
    4-3-3 -> GK 2 · DEF 6 · MID 5 · FWD 4   (17)
    3-5-2 -> GK 2 · DEF 5 · MID 7 · FWD 3   (17)
    dizilis bilinmiyorsa (esnek kadro) her mevkinin en zor dizilisi: GK 2 · DEF 6 · MID 7 · FWD 4 (19)
Durum:
    Kritik  : sayi < ilk 11 ihtiyaci (kalecide < 2: A takimda en az 2 kaleci kurali)
    İnce    : sayi < onerilen
    Yeterli : aksi

Sozlesme: contract_years = bu sezon dahil kalan yil. 1 (ya da 0: suresi dolmus) -> bu sezon sonunda
biter ("Sözleşmesi bitiyor"); bitis sezonu = sezon + max(yil, 1) - 1. k sezon sonrasi projeksiyonda
yalnizca contract_years > k olanlar kalir; o sezon 32+ olacaklar "yaslanan" olarak not edilir
(development.DECLINE_START_AGE: gerileme baslar).

Bayraklar: Sözleşmesi bitiyor · Yaşlanıyor (32+) · Gelişiyor (21 ve alti, TAHMINI potansiyel orta noktasi
gucunden en az GROWING_MIN_GAP fazla) · Sakat.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from development import DECLINE_START_AGE, WONDERKID_MAX_AGE
from stars import UNKNOWN, star_range, stars
from tactics import DEFAULT_FORMATION, FORMATIONS

GROUPS: tuple[str, ...] = ("GK", "DEF", "MID", "FWD")
GROUP_LABELS: dict[str, str] = {"GK": "Kaleci", "DEF": "Defans", "MID": "Orta saha", "FWD": "Forvet"}
REINFORCE_NOUNS: dict[str, str] = {"GK": "kaleci", "DEF": "defans", "MID": "orta saha", "FWD": "forvet"}
PLAYER_NOUNS: dict[str, str] = {
    "GK": "kaleci", "DEF": "defans oyuncusu", "MID": "orta saha oyuncusu", "FWD": "forvet",
}
BACKUP_COVER: dict[str, int] = {"GK": 1, "DEF": 2, "MID": 2, "FWD": 1}
MIN_KEEPERS = 2

STATUS_OK = "Yeterli"
STATUS_THIN = "İnce"
STATUS_CRITICAL = "Kritik"
STATUS_RANK: dict[str, int] = {STATUS_OK: 0, STATUS_THIN: 1, STATUS_CRITICAL: 2}

FLAG_CONTRACT = "Sözleşmesi bitiyor"
FLAG_AGING = "Yaşlanıyor"
FLAG_GROWING = "Gelişiyor"
FLAG_INJURED = "Sakat"

AGING_AGE = DECLINE_START_AGE           # 32
GROWING_MAX_AGE = WONDERKID_MAX_AGE     # 21
GROWING_MIN_GAP = 3
DEFAULT_HORIZON = 2
BALANCED_MESSAGE = "Kadro dengeli görünüyor; acil takviye gerekmiyor."


# ===========================================================================
# 1) KURALLAR
# ===========================================================================

def starters_needed(formation: str | None) -> dict[str, int]:
    """Dizilisin mevki basina ilk 11 ihtiyaci. Bilinmeyen dizilis -> varsayilan (4-4-2)."""
    d, m, f = FORMATIONS.get(formation or DEFAULT_FORMATION, FORMATIONS[DEFAULT_FORMATION])
    return {"GK": 1, "DEF": d, "MID": m, "FWD": f}


def recommended_minimums(formation: str | None = None) -> dict[str, int]:
    """Onerilen asgari sayi. formation None: tum dizilislerin en zoru (esnek kadro)."""
    if formation is None:
        shapes = [starters_needed(name) for name in FORMATIONS]
        return {g: max(s[g] for s in shapes) + BACKUP_COVER[g] for g in GROUPS}
    needed = starters_needed(formation)
    return {g: needed[g] + BACKUP_COVER[g] for g in GROUPS}


def critical_minimum(group: str, starters: int) -> int:
    return max(starters, MIN_KEEPERS) if group == "GK" else starters


def group_status(group: str, count: int, starters: int, recommended: int) -> str:
    if count < critical_minimum(group, starters):
        return STATUS_CRITICAL
    if count < recommended:
        return STATUS_THIN
    return STATUS_OK


def contract_expiry_season(season: int, contract_years: int) -> int:
    """Sozlesmenin son sezonu (0 = suresi dolmus: bu sezon)."""
    return season + max(contract_years, 1) - 1


def contract_ends_this_season(contract_years: int) -> bool:
    return contract_years <= 1


def under_contract_after(contract_years: int, seasons_ahead: int) -> bool:
    """seasons_ahead sezon sonra hala sozlesmeli mi?"""
    return contract_years > seasons_ahead


# ===========================================================================
# 2) VERI
# ===========================================================================

@dataclass(frozen=True)
class PlannerPlayer:
    """Girdi satiri. overall ve potansiyel araligi yalnizca siralama/bayrak icin (ekrana cikmaz)."""
    player_id: int
    name: str
    position: str                       # GK / DEF / MID / FWD
    age: int
    overall: int
    potential_low: int                  # gozlemci tahmini (kendi oyuncunda da sisli)
    potential_high: int
    contract_years: int
    injured: bool = False
    in_academy: bool = False

    @property
    def potential_estimate(self) -> int:
        return (self.potential_low + self.potential_high) // 2


@dataclass(frozen=True)
class PlanPlayer:
    player_id: int
    name: str
    position: str
    age: int
    stars: str
    potential_stars: str
    contract_expiry_season: int
    flags: tuple[str, ...]
    in_academy: bool = False


@dataclass(frozen=True)
class SeasonProjection:
    season: int
    seasons_ahead: int
    count: int                          # sozlesmesi suren oyuncu
    leaving: tuple[str, ...]            # o sezona kadar sozlesmesi biten
    aging: tuple[str, ...]              # kalanlardan o sezon 32+ olacaklar
    status: str


@dataclass(frozen=True)
class PositionGroupPlan:
    group: str
    label: str
    players: tuple[PlanPlayer, ...]
    prospects: tuple[PlanPlayer, ...]   # U-21 akademi adaylari (derinlige sayilmaz)
    count: int
    available: int                      # sakat olmayan
    starters: int                       # dizilisin ilk 11 ihtiyaci
    recommended: int
    status: str                         # Yeterli / İnce / Kritik
    stars: str                          # mevkinin en iyi 'starters' oyuncusunun ortalamasi
    projections: tuple[SeasonProjection, ...]

    @property
    def next_season(self) -> SeasonProjection | None:
        return self.projections[0] if self.projections else None


@dataclass(frozen=True)
class SquadPlan:
    team_id: int
    team_name: str
    season: int
    formation: str
    horizon_seasons: int
    squad_size: int
    squad_max: int | None
    groups: tuple[PositionGroupPlan, ...]
    recommendations: tuple[str, ...]

    def group(self, key: str) -> PositionGroupPlan:
        return next(g for g in self.groups if g.group == key)

    @property
    def contracts_ending(self) -> tuple[PlanPlayer, ...]:
        return tuple(p for g in self.groups for p in g.players if FLAG_CONTRACT in p.flags)


# ===========================================================================
# 3) PLAN
# ===========================================================================

def player_flags(p: PlannerPlayer) -> tuple[str, ...]:
    flags: list[str] = []
    if contract_ends_this_season(p.contract_years):
        flags.append(FLAG_CONTRACT)
    if p.age >= AGING_AGE:
        flags.append(FLAG_AGING)
    if p.age <= GROWING_MAX_AGE and p.potential_estimate - p.overall >= GROWING_MIN_GAP:
        flags.append(FLAG_GROWING)
    if p.injured:
        flags.append(FLAG_INJURED)
    return tuple(flags)


def plan_player(p: PlannerPlayer, season: int) -> PlanPlayer:
    return PlanPlayer(
        player_id=p.player_id, name=p.name, position=p.position, age=p.age,
        stars=stars(p.overall), potential_stars=star_range(p.potential_low, p.potential_high),
        contract_expiry_season=contract_expiry_season(season, p.contract_years),
        flags=player_flags(p), in_academy=p.in_academy,
    )


def _names(players: Sequence[PlannerPlayer]) -> str:
    return ", ".join(p.name for p in players)


def _projections(
    group: str, players: Sequence[PlannerPlayer], season: int, horizon: int, starters: int, recommended: int,
) -> tuple[SeasonProjection, ...]:
    rows = []
    for ahead in range(1, horizon + 1):
        staying = [p for p in players if under_contract_after(p.contract_years, ahead)]
        leaving = [p for p in players if not under_contract_after(p.contract_years, ahead)]
        rows.append(SeasonProjection(
            season=season + ahead, seasons_ahead=ahead, count=len(staying),
            leaving=tuple(p.name for p in leaving),
            aging=tuple(p.name for p in staying if p.age + ahead >= AGING_AGE),
            status=group_status(group, len(staying), starters, recommended),
        ))
    return tuple(rows)


def _group_recommendations(
    g: PositionGroupPlan, players: Sequence[PlannerPlayer], prospects: Sequence[PlannerPlayer],
) -> list[tuple[int, str]]:
    """(onem, metin) listesi. Onem: 3 acil, 2 gerekli, 1 planla."""
    out: list[tuple[int, str]] = []
    label, reinforce, noun = g.label, REINFORCE_NOUNS[g.group], PLAYER_NOUNS[g.group]
    critical = critical_minimum(g.group, g.starters)

    if g.status == STATUS_CRITICAL:
        out.append((3, f"{label} hattı kritik: {g.count} oyuncu var, bu diziliş için en az {critical} "
                       f"gerekli. Acilen {reinforce} takviyesi yap."))
    elif g.status == STATUS_THIN:
        out.append((2, f"{label} hattı ince ({g.count}/{g.recommended}): rotasyon ve sakatlıklar için "
                       f"{g.recommended - g.count} {noun} daha önerilir."))
    if g.status != STATUS_CRITICAL and g.available < g.starters:
        out.append((2, f"{label} hattında sakatlıklar nedeniyle yalnızca {g.available} sağlam oyuncu var; "
                       f"ilk 11 için {g.starters} gerekli."))

    nxt = g.next_season
    if nxt is not None and nxt.leaving and nxt.status != STATUS_OK:
        out.append((2, f"Gelecek sezon için {reinforce} takviyesi gerekli: {len(nxt.leaving)} oyuncunun "
                       f"sözleşmesi bitiyor ({', '.join(nxt.leaving)}); hat {g.count} oyuncudan "
                       f"{nxt.count} oyuncuya düşüyor, önerilen {g.recommended}."))
    if len(g.projections) > 1:
        last = g.projections[-1]
        if last.status != STATUS_OK and STATUS_RANK[last.status] > STATUS_RANK[nxt.status]:
            out.append((1, f"{last.season}. sezona kadar {label.lower()} hattında {len(last.leaving)} "
                           f"oyuncunun sözleşmesi bitiyor; uzun vadeli {reinforce} planı yap."))

    aging = [p for p in players if p.age >= AGING_AGE]
    if aging and len(aging) * 3 >= len(players):
        out.append((1, f"{label} hattı yaşlanıyor: {_names(aging)} {AGING_AGE} yaş ve üzerinde; "
                       f"performans düşüşü beklenir, genç bir {noun} planla."))

    needs_players = g.status != STATUS_OK or (nxt is not None and nxt.status != STATUS_OK)
    plausible = plausible_prospects(players, prospects)
    if needs_players and plausible:
        best = plausible[0]
        out.append((1, f"Akademide {len(plausible)} umut veren {noun} adayı var; {best.name} "
                       f"(potansiyel {star_range(best.potential_low, best.potential_high)}) "
                       f"A takıma yükseltilebilir."))
    return out


def plausible_prospects(players: Sequence[PlannerPlayer], prospects: Sequence[PlannerPlayer]) -> list[PlannerPlayer]:
    """
    A takima aday akademi oyunculari: TAHMINI potansiyeli mevkinin en zayif A takim oyuncusuna yetisen
    (mevkide kimse yoksa hepsi). En yuksek tahmini potansiyel once.
    """
    floor = min((p.overall for p in players), default=None)
    fit = [p for p in prospects if floor is None or p.potential_estimate >= floor]
    return sorted(fit, key=lambda p: (-p.potential_estimate, -p.overall, p.player_id))


def plan_squad(
    team_id: int,
    team_name: str,
    season: int,
    formation: str | None,
    seniors: Sequence[PlannerPlayer],
    academy: Sequence[PlannerPlayer] = (),
    horizon_seasons: int = DEFAULT_HORIZON,
    squad_max: int | None = None,
) -> SquadPlan:
    """A takimi mevki gruplarina ayirir; derinlik, durum, sezon projeksiyonu ve Turkce oneriler uretir."""
    horizon = max(1, int(horizon_seasons))
    formation_name = formation if formation in FORMATIONS else DEFAULT_FORMATION
    needed = starters_needed(formation_name)
    minimums = recommended_minimums(formation_name)

    groups: list[PositionGroupPlan] = []
    urgent: list[tuple[int, int, str]] = []
    for order, key in enumerate(GROUPS):
        players = sorted((p for p in seniors if p.position == key), key=lambda p: (-p.overall, p.name))
        prospects = sorted((p for p in academy if p.position == key),
                           key=lambda p: (-p.potential_estimate, -p.overall, p.name))
        top = players[: needed[key]]
        plan = PositionGroupPlan(
            group=key, label=GROUP_LABELS[key],
            players=tuple(plan_player(p, season) for p in players),
            prospects=tuple(plan_player(p, season) for p in prospects),
            count=len(players), available=sum(1 for p in players if not p.injured),
            starters=needed[key], recommended=minimums[key],
            status=group_status(key, len(players), needed[key], minimums[key]),
            stars=stars(sum(p.overall for p in top) / len(top)) if top else UNKNOWN,
            projections=_projections(key, players, season, horizon, needed[key], minimums[key]),
        )
        groups.append(plan)
        urgent += [(weight, order, text) for weight, text in _group_recommendations(plan, players, prospects)]

    urgent.sort(key=lambda row: (-row[0], row[1]))
    recommendations = [text for _w, _o, text in urgent]
    if squad_max is not None and len(seniors) > squad_max:
        recommendations.insert(0, f"A takım kadrosu {len(seniors)} oyuncu; sınır {squad_max}. "
                                  f"Yeni oyuncu almadan önce kadroyu daralt.")
    if not recommendations:
        recommendations.append(BALANCED_MESSAGE)
    ending = [p for p in sorted(seniors, key=lambda p: p.name) if contract_ends_this_season(p.contract_years)]
    if ending:
        recommendations.append(f"Sözleşmesi bu sezon bitenler: {_names(ending)}.")

    return SquadPlan(
        team_id=team_id, team_name=team_name, season=season, formation=formation_name,
        horizon_seasons=horizon, squad_size=len(seniors), squad_max=squad_max,
        groups=tuple(groups), recommendations=tuple(recommendations),
    )
