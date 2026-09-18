"""
tactics.py
==========
Kadro ve taktik kurallari (4. Asama). SAF MANTIK: veritabanina yazmaz, ORM
nesnelerini sadece okur (duck typing: id, name, position, overall_rating, form,
morale, is_available(week), unavailability_reason(week); istege bagli condition).

    FORMATIONS        dizilis adi -> (DEF, MID, FWD)  (kayitli, mac oncesi)
    MATCH_FORMATIONS  mac ici dizilisler (FORMATIONS + acil durum 5-3-2)
    selection_power   overall x form x moral x yorgunluk (notr noktada, tam kondisyonda = overall)
    validate_lineup   ilk 11 + kulube kurallari (sayi, mevki, sakat/cezali, kulube limiti,
                      dusuk kondisyon uyarisi)
    pick_best_xi      asistan menajer: en yuksek secim gucune sahip uygun 11 (yorgunlari dinlendirir)
    pick_bench        kalan en iyi oyuncular (en az bir kaleci ile)
    arrange_slots     ilk 11'i dizilis slotlarina yerlestirir (ekran ve duzenleme icin)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import fitness
from models import Position

FORMATIONS: dict[str, tuple[int, int, int]] = {
    "4-4-2": (4, 4, 2),
    "4-3-3": (4, 3, 3),
    "3-5-2": (3, 5, 2),
}
# Mac ici (canli, 9. Asama) dizilisler: kayitli dizilislere ek olarak acil durum 5-3-2.
# 5-3-2 veritabanina YAZILMAZ (teams.formation CHECK kisiti): sema degismez, kariyer kaydi korunur.
MATCH_FORMATIONS: dict[str, tuple[int, int, int]] = {**FORMATIONS, "5-3-2": (5, 3, 2)}
DEFAULT_FORMATION = "4-4-2"


def formation_name(shape: tuple[int, int, int] | None) -> str:
    """(4, 3, 3) -> '4-3-3'. None -> varsayilan dizilis adi."""
    if shape is None:
        return DEFAULT_FORMATION
    return "-".join(str(n) for n in shape)
MAX_BENCH = 7
# form 50 / moral 70 -> secim gucu tam olarak overall'a esit olsun
NEUTRAL_CONDITION = 0.50 * 0.70

ROLE_ORDER = (Position.GK, Position.DEF, Position.MID, Position.FWD)


def formation_tuple(name: str) -> tuple[int, int, int]:
    try:
        return FORMATIONS[name]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen diziliş: {name}. Seçenekler: {', '.join(FORMATIONS)}") from exc


def role_counts(name: str) -> dict[Position, int]:
    d, m, f = formation_tuple(name)
    return {Position.GK: 1, Position.DEF: d, Position.MID: m, Position.FWD: f}


def formation_slots(name: str) -> list[Position]:
    """[GK, DEF, DEF, ..., MID, ..., FWD, ...] -- ekrandaki slot sirasi."""
    slots: list[Position] = []
    for role in ROLE_ORDER:
        slots.extend([role] * role_counts(name)[role])
    return slots


def selection_power(overall: int, form: int, morale: int, condition: int = 100) -> float:
    """
    Kadro secimi icin guc: overall x (form/100) x (moral/100), notr noktaya olceklenmis,
    kondisyonun yorgunluk carpaniyla (100 -> 1.00, 0 -> 0.75) dusurulmus.
    """
    return overall * (form / 100.0) * (morale / 100.0) / NEUTRAL_CONDITION * fitness.fatigue_factor(condition)


def player_power(p) -> float:
    """Oyuncunun secim gucu; kondisyon alani yoksa (test dublorleri) tam kondisyon sayilir."""
    return selection_power(p.overall_rating, p.form, p.morale, fitness.condition_of(p))


@dataclass
class LineupCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_lineup(
    players: Iterable,
    formation: str,
    week: int,
    xi: Mapping[int, Position],
    bench: Iterable[int],
    *,
    allow_incomplete: bool = False,
) -> LineupCheck:
    """
    Menajerin kadro kararini denetler. Hata varsa kadro uygulanmamali.
    Bos ilk 11 hata degil uyaridir: macta asistan otomatik kurar.
    allow_incomplete (Faz 13G, taktik tahtasi taslagi): ilk 11'in 11'den az olmasi ve bir mevkide eksik oyuncu hata
    sayilmaz (fazlasi yine hata); diger TUM kurallar (sakat/cezali, kulube siniri, cakisma) aynen uygulanir.
    Kaydetme her zaman allow_incomplete=False ile yapilir (CareerManager.set_lineup).
    """
    check = LineupCheck()
    by_id = {p.id: p for p in players}
    bench_ids = set(bench)

    if formation not in FORMATIONS:
        check.errors.append(f"Bilinmeyen diziliş: {formation}")
        return check

    unknown = [pid for pid in list(xi) + list(bench_ids) if pid not in by_id]
    if unknown:
        check.errors.append(f"Kadroda olmayan oyuncu id'leri: {unknown}")
        return check

    if not xi:
        check.warnings.append("İlk 11 belirlenmedi; maçta asistan en iyi 11'i kuracak.")
    else:
        if len(xi) > 11 or (len(xi) != 11 and not allow_incomplete):
            check.errors.append(f"İlk 11'de {len(xi)} oyuncu var, 11 olmalı.")
        needs = role_counts(formation)
        for role, n in needs.items():
            have = sum(1 for r in xi.values() if r is role)
            if have > n or (have != n and not allow_incomplete):
                check.errors.append(f"{formation} için {n} {role.value} gerekli, {have} seçildi.")
        for pid, role in xi.items():
            p = by_id[pid]
            reason = p.unavailability_reason(week)
            if reason:
                check.errors.append(f"{p.name} ilk 11'de olamaz: {reason}.")
                continue
            if p.position is not role:
                check.warnings.append(f"{p.name} mevki dışı oynayacak ({p.position.value} → {role.value}).")
            condition = fitness.condition_of(p)
            if condition < fitness.CONDITION_WARN:
                check.warnings.append(f"{p.name} kondisyonu düşük (%{condition}).")

    overlap = bench_ids & set(xi)
    if overlap:
        check.errors.append("Aynı oyuncu hem ilk 11'de hem kulübede: " + ", ".join(by_id[i].name for i in overlap))
    for pid in bench_ids - set(xi):
        reason = by_id[pid].unavailability_reason(week)
        if reason:
            check.errors.append(f"{by_id[pid].name} kulübede olamaz: {reason}.")
    if len(bench_ids) > MAX_BENCH:
        check.errors.append(f"Kulübede en fazla {MAX_BENCH} oyuncu olabilir ({len(bench_ids)} seçildi).")
    return check


def pick_best_xi(players: Iterable, formation: str, week: int) -> dict[int, Position]:
    """
    Asistan menajer: dizilise uygun, sakat/cezali olmayan, secim gucu en yuksek 11.
    Bir mevkide yeterli oyuncu yoksa en iyi kalan saha oyuncusu o slota (mevki disi) konur.
    """
    available = [p for p in players if p.is_available(week)]
    ranked = sorted(available, key=player_power, reverse=True)
    needs = role_counts(formation)
    xi: dict[int, Position] = {}

    for role, n in needs.items():
        for p in [p for p in ranked if p.position is role][:n]:
            xi[p.id] = role

    for role, n in needs.items():
        missing = n - sum(1 for r in xi.values() if r is role)
        if missing <= 0:
            continue
        pool = [p for p in ranked if p.id not in xi and (p.position is not Position.GK or role is Position.GK)]
        for p in pool[:missing]:
            xi[p.id] = role
    return xi


def pick_bench(players: Iterable, xi: Mapping[int, Position], week: int, limit: int = MAX_BENCH) -> list[int]:
    """Ilk 11 disindaki uygun oyunculardan en iyileri; varsa bir kaleci mutlaka kulubede."""
    rest = sorted(
        (p for p in players if p.id not in xi and p.is_available(week)),
        key=player_power, reverse=True,
    )
    keepers = [p for p in rest if p.position is Position.GK][:1]
    others = [p for p in rest if p not in keepers]
    return [p.id for p in (keepers + others)[:limit]]


def arrange_slots(players: Iterable, formation: str, xi: Mapping[int, Position]) -> list[tuple[Position, object | None]]:
    """Ilk 11'i slotlara yerlestirir: [(rol, oyuncu | None), ...] -- dizilis sirasinda."""
    by_id = {p.id: p for p in players}
    buckets: dict[Position, list] = {role: [] for role in ROLE_ORDER}
    for pid, role in xi.items():
        if pid in by_id:
            buckets[role].append(by_id[pid])
    for role in buckets:
        buckets[role].sort(key=player_power, reverse=True)
    return [(role, buckets[role].pop(0) if buckets[role] else None) for role in formation_slots(formation)]
