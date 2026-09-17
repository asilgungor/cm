"""
world_cup.py
============
Dunya Kupasi ve eleme gruplari kurallari (Faz 12 / 14. Asama, 12C). SAF modul: veritabani, ORM ve
Streamlit bilmez. Siralama ve fikstur icin cup_draw.rank_group / cup_draw.group_schedule kullanilir
(DrawSession 16 kulube bagli oldugundan kullanilmaz).

    world_cup_size      -> uygun milli takim sayisina gore turnuva boyu: 32 / 16 / 8 / 4, yetmezse 0
    plan                -> gruplar, grup boyu, eleme turlari ve mac gunu sayisi
    rank_by_reputation / seed_pots / group_draw -> itibar torbalari ve deterministik kura
    qualifier_groups    -> eleme gruplari (yalnizca uygun ulus sayisi turnuva boyunu asarsa)
    group_standings / qualified_from_groups -> grup tablolari ve turnuvaya katilanlar
    world_cup_group_fixtures / qualifier_fixtures -> mac gunu -> [(grup, ev, deplasman)]
    knockout_pairs / next_knockout_pairs -> grup siralamalarindan eleme agaci, sonraki tur

Turnuva boyu ve plan (Soccer Manager: 32 takim, 4'erli 8 seri basli grup, ilk iki cikar, tek maclik eleme):
    size  gruplar  eleme turlari        mac gunu (grup 3 + eleme)
     32    8 x 4   R16, QF, SF, FINAL   7
     16    4 x 4   QF, SF, FINAL        6
      8    2 x 4   SF, FINAL            5
      4    1 x 4   FINAL                4   (tek grup; birinci ile ikinci finalde yeniden karsilasir)
    4 takimlik turnuvada da grup asamasi secildi: her boyda ayni kural (4'erli grup, ilk iki cikar) gecerli
    olur ve ulusun 4 maci garanti olur (dogrudan yari final yalnizca 2 mac olurdu).
    Grup maclari tek devre (3 tur), tarafsiz saha; eleme maclari tek mac (berabere biterse uzatma/penalti).

Kura:
    rank_by_reputation  itibar (azalan) -> ad -> id
    seed_pots           siralamayi `groups` genisliginde torbalara boler (torba 1 = seri basilari);
                        yalnizca SON torba eksik olabilir (eleme gruplarinda)
    group_draw          her torba rng ile karistirilir, i. top i. gruba gider: her grupta her torbadan
                        TAM BIR ulus. Eksik son torbanin toplari rng.sample ile secilen gruplara gider
                        (grup boylari en fazla 1 farkli). rng: random.Random ya da tohum (int/str);
                        ayni tohum ayni kura.

Elemeler (qualifier_groups, yalnizca uygun ulus > size):
    * Her uygun ulus eleme oynar. Grup sayisi G = max(1, min(slots, n // 2, yuvarla(n / 4))), yani
      ~4'erli gruplar (boylar en fazla 1 farkli, en az 2). Her grup birincisi kontenjana sigar (G <= slots).
    * Katilim kademelidir: once TUM grup birincileri, sonra en iyi ikinciler, gerekirse en iyi ucuncular ...
      Bir kademe kontenjana sigmiyorsa o kademe mac basina puan, mac basina averaj, mac basina gol,
      itibar ve id ile siralanir (grup boylari farkli olabildigi icin mac basina). Sentetik dunyada
      (6 ulus -> 4'luk kupa) 3'erli 2 grup: 2 birinci + 2 ikinci; 63 ulus -> 32: 16 grup, ilk iki.
    * Fikstur: 4 ve daha kucuk gruplar cift devre (en cok 6 tur), 5+ gruplar tek devre (5 tur) oynar;
      boylece elemeler 3 milli arada 2'ser mac gunune sigar (intl_calendar.matchday_weeks).

Eleme agaci (knockout_pairs): gruplar ikiserli eslenir (A-B, C-D, ...). Ust yari: X1-Y2, alt yari: Y1-X2
(2018 Dunya Kupasi duzeni). Liste agac sirasindadir: sonraki turda eslesme j = (2j) ile (2j+1)'in
galipleri. Ayni gruptan cikan iki ulus farkli yarilara duser, ancak FINALDE karsilasabilir
(tek gruplu 4'luk turnuva haric). Grup birincileri de birbirini en erken ceyrek finalde gorur.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

import cup_draw
from cup_draw import GroupRow

WORLD_CUP_SIZES = (32, 16, 8, 4)
GROUP_SIZE = 4
GROUP_ADVANCE = 2
GROUP_ROUNDS = GROUP_SIZE - 1                 # tek devre
QUALIFIER_GROUP_TARGET = 4
QUALIFIER_DOUBLE_ROUND_MAX = 4                # bu boya kadar eleme gruplari cift devre

# Tur kodlari (international_fixtures.stage ve international_entries.eliminated_stage VARCHAR(8) sigar)
QUAL = "QUAL"                                  # elemeler (fikstur satirinda turnuva turu QUALIFIER, stage GROUP)
GROUP = cup_draw.Stage.GROUP.value
R16 = cup_draw.Stage.R16.value
QF = cup_draw.Stage.QF.value
SF = cup_draw.Stage.SF.value
FINAL = cup_draw.Stage.FINAL.value
CHAMPION = "CHAMPION"                          # yalnizca "ulasilan tur" olarak (sampiyon)

KNOCKOUT_STAGES: dict[int, tuple[str, ...]] = {
    32: (R16, QF, SF, FINAL),
    16: (QF, SF, FINAL),
    8: (SF, FINAL),
    4: (FINAL,),
}
# Ulasilan tur sirasi (national_rules.sack_decision / expected_stage)
STAGE_ORDER: tuple[str, ...] = (QUAL, GROUP, R16, QF, SF, FINAL, CHAMPION)
STAGE_LABELS: dict[str, str] = {
    QUAL: "Elemeler",
    **{stage.value: text for stage, text in cup_draw.STAGE_LABELS.items()},
    CHAMPION: "Şampiyonluk",
}
GROUP_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class NationSeed:
    """Kura girdisi (plan 2.3'te adi gecer, alanlari burada sabitlendi)."""
    nation_id: int
    name: str
    reputation: int


@dataclass(frozen=True)
class WorldCupPlan:
    size: int
    groups: int
    group_size: int
    knockout_stages: tuple[str, ...]
    matchdays: int

    def stage_days(self) -> tuple[tuple[str, int], ...]:
        """Sezon sonu mac gunleri sirayla: (tur, tur ici sira). 32 -> GROUP 1..3, R16, QF, SF, FINAL."""
        days = [(GROUP, rnd) for rnd in range(1, GROUP_ROUNDS + 1)] if self.groups else []
        days.extend((stage, 1) for stage in self.knockout_stages)
        return tuple(days)


# ===========================================================================
# Boy, plan, etiketler
# ===========================================================================

def world_cup_size(eligible: int) -> int:
    """En buyuk 32 / 16 / 8 / 4 <= eligible; 4'ten az uygun ulus -> 0 (Dunya Kupasi yok)."""
    for size in WORLD_CUP_SIZES:
        if eligible >= size:
            return size
    return 0


def plan(size: int) -> WorldCupPlan:
    """Turnuva plani (bkz. modul aciklamasindaki tablo). Desteklenmeyen boy -> ValueError."""
    if size not in KNOCKOUT_STAGES:
        raise ValueError(f"Desteklenmeyen Dünya Kupası boyu: {size} (32, 16, 8 ya da 4 olmalı).")
    stages = KNOCKOUT_STAGES[size]
    return WorldCupPlan(
        size=size,
        groups=size // GROUP_SIZE,
        group_size=GROUP_SIZE,
        knockout_stages=stages,
        matchdays=GROUP_ROUNDS + len(stages),
    )


def stage_label(stage: str) -> str:
    """'R16' -> 'Son 16', 'QUAL' -> 'Elemeler', 'CHAMPION' -> 'Şampiyonluk'. Bilinmeyen kod aynen doner."""
    code = str(getattr(stage, "value", stage)).strip().upper()
    return STAGE_LABELS.get(code, str(stage))


def group_letter(index: int) -> str:
    """0 -> 'A'. 26 ve ustu grup (gercekte olusmaz) sayi ile gosterilir."""
    return GROUP_LETTERS[index] if 0 <= index < len(GROUP_LETTERS) else str(index + 1)


# ===========================================================================
# Torbalar ve kura
# ===========================================================================

def _nation_id(item) -> int:
    """NationSeed, GroupRow (team_id) ya da duz int."""
    for attr in ("nation_id", "team_id"):
        value = getattr(item, attr, None)
        if value is not None:
            return int(value)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"Ulus kimliği okunamadı: {item!r}")
    return item


def _check_unique(ids: Sequence[int]) -> None:
    seen: set[int] = set()
    for nid in ids:
        if nid in seen:
            raise ValueError(f"Aynı ulus birden fazla kez listelendi (id={nid}).")
        seen.add(nid)


def _rng(rng) -> random.Random:
    if isinstance(rng, random.Random):
        return rng
    if isinstance(rng, (int, str)) and not isinstance(rng, bool):
        return random.Random(rng)
    raise ValueError("Kura için random.Random ya da tohum (int/str) gerekir.")


def rank_by_reputation(nations: Sequence[NationSeed]) -> list[NationSeed]:
    """Itibar (azalan), ad (buyuk/kucuk harf duyarsiz), id. Tekrarlayan id -> ValueError."""
    ordered = list(nations)
    _check_unique([n.nation_id for n in ordered])
    return sorted(ordered, key=lambda n: (-int(n.reputation), str(n.name).casefold(), n.nation_id))


def seed_pots(nations: Sequence[NationSeed], groups: int) -> list[list[NationSeed]]:
    """
    Itibara gore torbalar: her torba `groups` ulus (torba 0 = seri basilari). Ulus sayisi `groups`un
    kati degilse yalnizca SON torba eksik kalir. Dunya Kupasi: plan(size).groups ile 4 tam torba.
    """
    if isinstance(groups, bool) or not isinstance(groups, int) or groups < 1:
        raise ValueError("Grup sayısı en az 1 olmalı.")
    ordered = rank_by_reputation(nations)
    if len(ordered) < groups:
        raise ValueError(f"{groups} grup için en az {groups} ulus gerekir ({len(ordered)} var).")
    return [ordered[i:i + groups] for i in range(0, len(ordered), groups)]


def group_draw(rng, pots) -> list[list[int]]:
    """
    Torbalardan gruplar: her grupta her torbadan tam bir ulus (grup listesi torba sirasinda, ilk eleman
    torba 1'den). Grup sayisi = ilk torbanin boyu; yalnizca son torba daha kucuk olabilir.
    pots elemanlari NationSeed ya da ulus id'si olabilir. Donus: grup -> ulus id'leri.
    """
    pot_ids = [[_nation_id(item) for item in pot] for pot in pots]
    if not pot_ids or not pot_ids[0]:
        raise ValueError("Kura için en az bir dolu torba gerekir.")
    width = len(pot_ids[0])
    if any(len(pot) != width for pot in pot_ids[:-1]) or not 0 < len(pot_ids[-1]) <= width:
        raise ValueError("Torbalar eşit büyüklükte olmalı (yalnızca son torba eksik olabilir).")
    _check_unique([nid for pot in pot_ids for nid in pot])
    draw = _rng(rng)
    result: list[list[int]] = [[] for _ in range(width)]
    for pot in pot_ids:
        balls = list(pot)
        draw.shuffle(balls)
        targets = range(width) if len(balls) == width else sorted(draw.sample(range(width), len(balls)))
        for group_index, nation_id in zip(targets, balls, strict=True):
            result[group_index].append(nation_id)
    return result


def world_cup_draw(nations: Sequence[NationSeed], rng) -> list[list[int]]:
    """Dunya Kupasi grup kurasi: len(nations) desteklenen bir boy olmali (32/16/8/4)."""
    tournament = plan(len(nations))
    return group_draw(rng, seed_pots(nations, tournament.groups))


def qualifier_group_count(eligible: int, slots: int) -> int:
    """Eleme grup sayisi: max(1, min(slots, eligible // 2, yuvarla(eligible / 4))); eleme gerekmezse 0."""
    if slots < 1:
        raise ValueError("Kontenjan en az 1 olmalı.")
    if eligible <= slots:
        return 0
    return max(1, min(slots, eligible // 2, (eligible + QUALIFIER_GROUP_TARGET // 2) // QUALIFIER_GROUP_TARGET))


def qualifier_groups(nations: Sequence[NationSeed], slots: int, rng) -> list[list[int]]:
    """
    Eleme gruplari (ulus id'leri, torba sirasinda). Uygun ulus sayisi `slots`u (normalde
    world_cup_size(len(nations))) asmiyorsa bos liste: eleme yok, hepsi dogrudan katilir.
    Katilim kurali qualified_from_groups'tadir (birinciler, en iyi ikinciler, ...).
    """
    count = qualifier_group_count(len(nations), slots)
    if count == 0:
        _check_unique([n.nation_id for n in nations])
        return []
    return group_draw(rng, seed_pots(nations, count))


# ===========================================================================
# Fikstur (cup_draw.group_schedule uzerinden)
# ===========================================================================

def group_fixtures(nation_ids: Sequence[int], double_round: bool) -> list[list[tuple[int, int]]]:
    """Tur -> [(ev, deplasman)]. cup_draw.group_schedule cift devre uretir; tek devrede ilk yarisi alinir."""
    rounds = cup_draw.group_schedule(nation_ids)
    return rounds if double_round else rounds[: len(rounds) // 2]


def _merge_days(per_group: list[list[list[tuple[int, int]]]]) -> list[list[tuple[int, int, int]]]:
    days = max((len(rounds) for rounds in per_group), default=0)
    return [
        [(group_index, home, away)
         for group_index, rounds in enumerate(per_group) if day < len(rounds)
         for home, away in rounds[day]]
        for day in range(days)
    ]


def world_cup_group_fixtures(groups: Sequence[Sequence[int]]) -> list[list[tuple[int, int, int]]]:
    """Dunya Kupasi grup asamasi (tek devre, 4'lu grupta 3 gun): gun -> [(grup, ulus, ulus)]."""
    return _merge_days([group_fixtures(group, double_round=False) for group in groups])


def qualifier_fixtures(groups: Sequence[Sequence[int]]) -> list[list[tuple[int, int, int]]]:
    """Eleme mac gunleri: <= 4'lu gruplar cift devre, daha buyukleri tek devre. gun -> [(grup, ev, dep)]."""
    return _merge_days([
        group_fixtures(group, double_round=len(group) <= QUALIFIER_DOUBLE_ROUND_MAX) for group in groups
    ])


# ===========================================================================
# Tablolar ve katilim (cup_draw.rank_group uzerinden)
# ===========================================================================

def group_standings(
    groups: Sequence[Sequence[int]],
    results: Sequence[tuple[int, int, int, int]],
    reputations: Mapping[int, float] | None = None,
    names: Mapping[int, str] | None = None,
) -> list[list[GroupRow]]:
    """
    Her grubun puan tablosu (en iyi once). results: (ev_id, dep_id, ev_gol, dep_gol) -- tum gruplarin
    maclari tek listede olabilir; baska gruptan mac yok sayilir. Esitlikte itibar katsayi yerine gecer.
    """
    return [cup_draw.rank_group(group, results, coefficients=reputations, names=names) for group in groups]


def group_rankings(standings: Sequence[Sequence[GroupRow]]) -> list[list[int]]:
    """Tablolar -> grup basina siralanmis ulus id'leri (knockout_pairs girdisi)."""
    return [[row.team_id for row in table] for table in standings]


def _tier_key(row, reputations: Mapping[int, float]) -> tuple:
    played = int(getattr(row, "played", 0) or 0)
    goals_for = int(row.goals_for)
    diff = goals_for - int(row.goals_against)
    if played <= 0:
        rates = (Fraction(0), Fraction(0), Fraction(0))
    else:
        rates = (Fraction(int(row.points), played), Fraction(diff, played), Fraction(goals_for, played))
    return (-rates[0], -rates[1], -rates[2], -float(reputations.get(row.team_id, 0)), row.team_id)


def qualified_from_groups(
    standings: Sequence[Sequence[GroupRow]],
    slots: int,
    reputations: Mapping[int, float] | None = None,
) -> list[int]:
    """
    Kademeli katilim: once tum grup birincileri (grup sirasiyla), sonra ikinciler, ucuncular ...
    Kontenjana tam sigmayan kademe mac basina puan / averaj / gol, itibar ve id ile siralanip kesilir.
    standings: siralanmis tablolar (group_standings). Donus tam olarak `slots` ulus id'si.
    """
    tables = [list(table) for table in standings]
    total = sum(len(table) for table in tables)
    if slots < 1 or slots > total:
        raise ValueError(f"Kontenjan 1 ile {total} arasında olmalı ({slots} verildi).")
    _check_unique([row.team_id for table in tables for row in table])
    reps = reputations or {}
    picked: list[int] = []
    for position in range(max(len(table) for table in tables)):
        room = slots - len(picked)
        if room <= 0:
            break
        tier = [table[position] for table in tables if len(table) > position]
        if len(tier) > room:
            tier = sorted(tier, key=lambda row: _tier_key(row, reps))[:room]
        picked.extend(row.team_id for row in tier)
    return picked


# ===========================================================================
# Eleme agaci
# ===========================================================================

def knockout_pairs(group_rankings) -> list[tuple[int, int]]:
    """
    Grup siralamalarindan ilk eleme turu (agac sirasinda). Gruplar ikiserli eslenir; ust yari X1-Y2,
    alt yari Y1-X2. Tek grup -> [(A1, A2)] (4'luk turnuvanin finali). Ilk yazilan grup birincisidir
    (tarafsiz sahada nominal ev sahibi). group_rankings: grup -> siralanmis ulus id'leri ya da GroupRow.
    Grup sayisi 1, 2, 4, 8 ... olmali; her gruptan en az 2 sira gerekir.
    """
    rankings = [[_nation_id(item) for item in ranking] for ranking in group_rankings]
    count = len(rankings)
    if count < 1 or count & (count - 1):
        raise ValueError(f"Eleme ağacı için grup sayısı 1, 2, 4 ya da 8 olmalı ({count} verildi).")
    if any(len(ranking) < GROUP_ADVANCE for ranking in rankings):
        raise ValueError("Her gruptan ilk iki sıra gerekir.")
    if count == 1:
        pairs = [(rankings[0][0], rankings[0][1])]
    else:
        halves = range(0, count, 2)
        top = [(rankings[x][0], rankings[x + 1][1]) for x in halves]
        bottom = [(rankings[x + 1][0], rankings[x][1]) for x in halves]
        pairs = top + bottom
    _check_unique([nid for pair in pairs for nid in pair])
    return pairs


def next_knockout_pairs(winner_ids_in_slot_order: Sequence[int]) -> list[tuple[int, int]]:
    """Sonraki tur: eslesme j = (2j galibi, 2j+1 galibi). Agac sirasi korunur (ust yari once)."""
    winners = [int(w) for w in winner_ids_in_slot_order]
    if len(winners) < 2 or len(winners) % 2:
        raise ValueError("Bir sonraki tur için çift sayıda (en az 2) galip gerekir.")
    _check_unique(winners)
    return [(winners[2 * j], winners[2 * j + 1]) for j in range(len(winners) // 2)]
