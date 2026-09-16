"""
cup_draw.py
===========
Devler Arenasi (Champions Cup) turnuva kurallari (8. Asama). SAF MANTIK: yalnizca
standart kutuphane (veritabani, ORM ya da Streamlit BILMEZ).

    cup_size_for / qualify      katilimci sayisi ve lig tablolarindan kademeli katilim
    formats_for / stages_for    desteklenen formatlar (eleme / gruplar) ve turlar
    make_pots                   katsayiya gore torbalar
    build_calendar              mac gunleri (lig sezonuna yayilmis ya da ardisik)
    DrawSession                 interaktif kura: top top cekilir, JSON'a yazilip devam ettirilebilir
    next_round_pairs            eleme agacinda bir sonraki turun eslesmeleri
    group_qualifier_pairs       grup siralamalarindan ceyrek final agaci
    group_schedule / rank_group grup fiksturu ve UEFA benzeri averaj kurallariyla puan tablosu

Kura kurallari:
    KNOCKOUT  Toplar sirayla: once seri basi olmayan torbadan (1) bir top eslesme k'yi acar,
              sonra seri basi torbasindan (0) bir top eslesme k'yi tamamlar (k = 0, 1, ...
              agac sirasiyla). Koruma aciksa ayni ligden iki takim eslesemez.
    GROUPS    Torbalar sirayla cekilir; her top, o torbadan takimi olmayan, ayni ligden takimi
              olmayan VE kuranin tamamlanabilmesine izin veren ILK gruba (A -> D) gider
              (UEFA "kura bilgisayari" mantigi).

Kilitlenme yok: her adimda top, gecerli bir tam kuraya izin veren toplar arasindan esit
olasilikla secilir (lig bazli, onbellekli geri izleme). Baslangicta hic gecerli tam kura
yoksa (ornegin 16'lik elemede ayni ligden 9 takim) lig korumasi kapatilir ve
relaxed_note ile aciklanir. Not (Konig kenar boyama / Hall): BOS kurada koruma ancak ve
ancak her lig en fazla size/2 (eleme) ya da 4 (gruplar) takimla temsil ediliyorsa
uygulanabilir; yarim kalmis kurada ise bu yetmez, bu yuzden arama her adimda yapilir.

Determinizm: n. adim random.Random(f"{seed}:{n}") ile cekilir. Bu yuzden
DrawSession.from_state(to_state()) yarida kalan kurayi AYNEN surdurur ve top top cekmek
draw_all ile ayni sonucu verir.

Kura gecesi (11. Asama): draw_pair() menajerin tek tiklamasidir. KNOCKOUT'ta eslesmeyi
tamamlayana kadar top acar (iki top: ev sahibi + deplasman), GROUPS'ta tek top. Adimlar
draw_next ile birebir aynidir: cift cift, top top ya da otomatik cekmek ayni kurayi verir.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from functools import lru_cache

from schedule import build_round_robin

# ===========================================================================
# [1] SABITLER
# ===========================================================================

CUP_SIZE = 16
GROUP_COUNT = 4
GROUP_ROUNDS = 6
GROUP_LETTERS = "ABCD"
STATE_VERSION = 1


class CupFormat(str, Enum):
    KNOCKOUT = "knockout"
    GROUPS = "groups"


class Stage(str, Enum):
    GROUP = "GROUP"
    R16 = "R16"
    QF = "QF"
    SF = "SF"
    FINAL = "FINAL"


STAGE_LABELS: dict[Stage, str] = {
    Stage.GROUP: "Grup Aşaması",
    Stage.R16: "Son 16",
    Stage.QF: "Çeyrek Final",
    Stage.SF: "Yarı Final",
    Stage.FINAL: "Final",
}

FORMAT_LABELS: dict[CupFormat, str] = {
    CupFormat.KNOCKOUT: "Eleme usulü",
    CupFormat.GROUPS: "Gruplar + eleme",
}

# Final tek mac, tarafsiz sahada
TWO_LEGGED: frozenset[Stage] = frozenset({Stage.R16, Stage.QF, Stage.SF})


@dataclass(frozen=True)
class CupTeam:
    id: int
    name: str
    league: str          # ulke/lig korumasi anahtari
    coefficient: float


# ===========================================================================
# [2] KATILIM, FORMAT, TORBALAR
# ===========================================================================

def cup_size_for(team_count: int) -> int:
    """Katilimci sayisi: 16 (>= 16 takim), 8 (>= 8 takim), aksi halde 0 (turnuva yok)."""
    if team_count >= 16:
        return 16
    if team_count >= 8:
        return 8
    return 0


def _check_unique(teams: Sequence[CupTeam]) -> None:
    seen: set[int] = set()
    for team in teams:
        if team.id in seen:
            raise ValueError(f"Aynı takım birden fazla kez listelendi (id={team.id}).")
        seen.add(team.id)


def qualify(league_tables: Sequence[Sequence[CupTeam]], slots: int = CUP_SIZE) -> list[CupTeam]:
    """
    Kademeli katilim. Her ic dizi bir lig, final siralamasina gore (en iyi once).
    Kademe 1 = her ligin 1.'si, kademe 2 = her ligin 2.'si, ... Bir kademe icinde ligler
    guce gore siralanir: ligin ilk 4 takiminin ortalama katsayisi (azalan), esitlikte lig adi.
    `slots` takima ulasilinca durulur. Yeterli takim yoksa ValueError.
    """
    if slots <= 0:
        raise ValueError("Katılımcı sayısı pozitif olmalı.")
    leagues = [list(table) for table in league_tables if len(table) > 0]
    _check_unique([team for table in leagues for team in table])
    total = sum(len(table) for table in leagues)
    if total < slots:
        raise ValueError(f"Turnuva için {slots} takım gerekiyor, liglerde yalnızca {total} takım var.")

    def strength(item: tuple[int, list[CupTeam]]) -> tuple[float, str, int]:
        index, table = item
        top = table[:4]
        mean = sum(t.coefficient for t in top) / len(top)
        return (-mean, table[0].league, index)

    ordered = [table for _, table in sorted(enumerate(leagues), key=strength)]
    picked: list[CupTeam] = []
    depth = max(len(table) for table in ordered)
    for tier in range(depth):
        for table in ordered:
            if tier < len(table):
                picked.append(table[tier])
                if len(picked) == slots:
                    return picked
    return picked  # pragma: no cover - total >= slots oldugu icin buraya gelinmez


def formats_for(size: int) -> list[CupFormat]:
    """16 -> [KNOCKOUT, GROUPS]; 8 -> [KNOCKOUT]; desteklenmeyen boyut -> []."""
    if size == 16:
        return [CupFormat.KNOCKOUT, CupFormat.GROUPS]
    if size == 8:
        return [CupFormat.KNOCKOUT]
    return []


def stages_for(fmt: CupFormat, size: int) -> list[Stage]:
    """KNOCKOUT 16: R16,QF,SF,FINAL | KNOCKOUT 8: QF,SF,FINAL | GROUPS 16: GROUP,QF,SF,FINAL."""
    fmt = CupFormat(fmt)
    if fmt not in formats_for(size):
        raise ValueError(f"Desteklenmeyen turnuva: {fmt.value} / {size} takım.")
    if fmt is CupFormat.GROUPS:
        return [Stage.GROUP, Stage.QF, Stage.SF, Stage.FINAL]
    if size == 16:
        return [Stage.R16, Stage.QF, Stage.SF, Stage.FINAL]
    return [Stage.QF, Stage.SF, Stage.FINAL]


def _coefficient_order(teams: Sequence[CupTeam]) -> list[CupTeam]:
    return sorted(teams, key=lambda t: (-t.coefficient, t.name, t.id))


def make_pots(teams: Sequence[CupTeam], fmt: CupFormat) -> list[list[CupTeam]]:
    """
    Katsayiya gore (azalan, esitlikte ad) torbalar.
    KNOCKOUT: size/2'lik 2 torba (torba 0 = seri basilari). GROUPS (yalnizca 16): 4'lu 4 torba.
    """
    fmt = CupFormat(fmt)
    size = len(teams)
    stages_for(fmt, size)  # desteklenmeyen kombinasyonda ValueError
    _check_unique(teams)
    ordered = _coefficient_order(teams)
    pot_count = 2 if fmt is CupFormat.KNOCKOUT else GROUP_COUNT
    per_pot = size // pot_count
    return [ordered[i * per_pot:(i + 1) * per_pot] for i in range(pot_count)]


def slot_title(fmt: CupFormat, index: int) -> str:
    """Ekran basligi: KNOCKOUT -> 'Eşleşme 3', GROUPS -> 'Grup C' (index 0-tabanli)."""
    if CupFormat(fmt) is CupFormat.GROUPS:
        return f"Grup {GROUP_LETTERS[index]}"
    return f"Eşleşme {index + 1}"


# ===========================================================================
# [3] TAKVIM
# ===========================================================================

@dataclass(frozen=True)
class Matchday:
    number: int      # 1-tabanli mac gunu
    stage: Stage
    leg: int         # iki macli turlarda 1/2, finalde 1, grup asamasinda tur 1..6
    week: int


def build_calendar(fmt: CupFormat, size: int, league_weeks: int, tournament_only: bool) -> list[Matchday]:
    """
    Mac gunleri sirayla: grup turlari 1..6 / R16 1,2 / QF 1,2 / SF 1,2 / FINAL.
    tournament_only (ya da lig haftasi mac gunu sayisindan azsa): hafta 1..n.
    Aksi halde lig sezonuna yayilir, final son lig haftasinda oynanir:
        week_k = ceil((k + 1) * league_weeks / n)   (k 0-tabanli; kesin artan)
    """
    plan: list[tuple[Stage, int]] = []
    for stage in stages_for(fmt, size):
        if stage is Stage.GROUP:
            plan.extend((stage, rnd) for rnd in range(1, GROUP_ROUNDS + 1))
        elif stage in TWO_LEGGED:
            plan.extend([(stage, 1), (stage, 2)])
        else:
            plan.append((stage, 1))
    n = len(plan)
    spread = not tournament_only and league_weeks >= n
    days: list[Matchday] = []
    for k, (stage, leg) in enumerate(plan):
        week = -(-(k + 1) * league_weeks // n) if spread else k + 1
        days.append(Matchday(number=k + 1, stage=stage, leg=leg, week=week))
    return days


# ===========================================================================
# [4] KURA: UYGULANABILIRLIK (lig bazli, onbellekli)
# ===========================================================================

def _canon(sets: Sequence[tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(tuple(sorted(s)) for s in sets))


@lru_cache(maxsize=200_000)
def _knockout_completable(unseeded: tuple[str, ...], seeded: tuple[str, ...]) -> bool:
    """
    Kalan seri basi olmayan ve seri basi toplari (yalnizca lig adlari, sirali) ayni ligden
    eslesme olmadan mukemmel eslestirilebilir mi? (geri izleme + onbellek)
    """
    if not unseeded:
        return True
    head, rest = unseeded[0], unseeded[1:]
    tried: set[str] = set()
    for i, league in enumerate(seeded):
        if league == head or league in tried:
            continue
        tried.add(league)
        if _knockout_completable(rest, seeded[:i] + seeded[i + 1:]):
            return True
    return False


@lru_cache(maxsize=200_000)
def _groups_completable(
    pending: tuple[str, ...],
    open_sets: tuple[tuple[str, ...], ...],
    closed_sets: tuple[tuple[str, ...], ...],
    future: tuple[tuple[str, ...], ...],
) -> bool:
    """
    pending    : cekilmekte olan torbanin kalan toplari (lig adlari, sirali)
    open_sets  : bu torbadan henuz takimi olmayan gruplarin lig kumeleri
    closed_sets: bu torbadan takimi olan gruplarin lig kumeleri
    future     : sonraki torbalarin lig adlari
    Gruplar yalnizca lig kumeleriyle ayirt edilir; bu yuzden kanonik anahtarla onbelleklenir.
    """
    if not pending:
        if not future:
            return True
        return _groups_completable(future[0], _canon(open_sets + closed_sets), (), future[1:])
    head, rest = pending[0], pending[1:]
    tried: set[tuple[str, ...]] = set()
    for i, group in enumerate(open_sets):
        if head in group or group in tried:
            continue
        tried.add(group)
        new_open = _canon(open_sets[:i] + open_sets[i + 1:])
        new_closed = _canon(closed_sets + (group + (head,),))
        if _groups_completable(rest, new_open, new_closed, future):
            return True
    return False


# ===========================================================================
# [5] KURA OTURUMU
# ===========================================================================

@dataclass(frozen=True)
class DrawStep:
    number: int              # 1-tabanli
    team_id: int
    team_name: str
    pot: int                 # 0-tabanli torba
    slot: int                # KNOCKOUT: agac sirasinda eslesme 0..size/2-1; GROUPS: grup 0..3
    partner_id: int | None   # KNOCKOUT: eslesmedeki rakip (eslesmenin ilk topunda None)


class DrawComplete(Exception):
    """Tum toplar cekildikten sonra draw_next cagrildi."""


class DrawSession:
    """
    Interaktif kura. Durum yalnizca (format, torbalar, seed, koruma, cekilen adimlar)
    ile tanimlanir; yerlesimler adimlardan yeniden kurulur.
    """

    def __init__(
        self,
        fmt: CupFormat,
        pots: list[list[CupTeam]],
        seed: int | str,
        protect_leagues: bool = True,
    ) -> None:
        self._fmt = CupFormat(fmt)
        self._pots: list[list[CupTeam]] = [list(pot) for pot in pots]
        self._seed = seed
        self._protect_requested = bool(protect_leagues)
        self._validate_pots()
        self._teams: dict[int, CupTeam] = {t.id: t for pot in self._pots for t in pot}
        self._pot_of: dict[int, int] = {t.id: p for p, pot in enumerate(self._pots) for t in pot}
        self._steps: list[DrawStep] = []
        self._drawn: set[int] = set()
        width = 2 if self._fmt is CupFormat.KNOCKOUT else GROUP_COUNT
        self._slots: list[list[int | None]] = [[None] * width for _ in range(self._slot_count)]
        self._protected = False
        self._relaxed_note: str | None = None
        if self._protect_requested:
            if self._completable():
                self._protected = True
            else:
                self._relax()

    # ----------------------------------------------------------------- yapi
    @property
    def _slot_count(self) -> int:
        return len(self._pots[0]) if self._fmt is CupFormat.KNOCKOUT else GROUP_COUNT

    def _validate_pots(self) -> None:
        pots = self._pots
        if self._fmt is CupFormat.KNOCKOUT:
            if len(pots) != 2 or len(pots[0]) != len(pots[1]) or len(pots[0]) not in (4, 8):
                raise ValueError("Eleme kurası için eşit büyüklükte iki torba (4+4 ya da 8+8) gerekir.")
        elif len(pots) != GROUP_COUNT or any(len(pot) != GROUP_COUNT for pot in pots):
            raise ValueError("Grup kurası için 4'er takımlık 4 torba gerekir.")
        _check_unique([t for pot in pots for t in pot])

    def _relax(self) -> None:
        self._protected = False
        limit = len(self._pots[0]) if self._fmt is CupFormat.KNOCKOUT else GROUP_COUNT
        counts: dict[str, int] = {}
        for pot in self._pots:
            for t in pot:
                counts[t.league] = counts.get(t.league, 0) + 1
        crowded = sorted((lg for lg, n in counts.items() if n > limit), key=lambda lg: (-counts[lg], lg))
        detail = (
            ", ".join(f"{lg}: {counts[lg]} takım" for lg in crowded) + f" (en fazla {limit} olabilir)"
            if crowded
            else "kalan toplar kurala uygun yerleştirilemiyor"
        )
        self._relaxed_note = (
            f"Aynı ülke koruması kaldırıldı — {detail}. Kura korumasız çekiliyor."
        )

    # ----------------------------------------------------------- ozellikler
    @property
    def fmt(self) -> CupFormat:
        return self._fmt

    @property
    def size(self) -> int:
        return sum(len(pot) for pot in self._pots)

    @property
    def seed(self) -> int | str:
        return self._seed

    @property
    def pots(self) -> list[list[CupTeam]]:
        return [list(pot) for pot in self._pots]

    @property
    def first_stage(self) -> Stage:
        return stages_for(self._fmt, self.size)[0]

    @property
    def steps(self) -> list[DrawStep]:
        return list(self._steps)

    @property
    def total_steps(self) -> int:
        return self.size

    @property
    def complete(self) -> bool:
        return len(self._steps) >= self.total_steps

    @property
    def protected(self) -> bool:
        """Lig korumasi fiilen uygulaniyor mu?"""
        return self._protected

    @property
    def relaxed_note(self) -> str | None:
        return self._relaxed_note

    @property
    def current_pot(self) -> int | None:
        """Siradaki topun cekilecegi torba (0-tabanli); kura bittiyse None."""
        if self.complete:
            return None
        done = len(self._steps)
        if self._fmt is CupFormat.KNOCKOUT:
            return 1 if done % 2 == 0 else 0
        return done // GROUP_COUNT

    @property
    def last_step(self) -> DrawStep | None:
        return self._steps[-1] if self._steps else None

    def team(self, team_id: int) -> CupTeam:
        return self._teams[team_id]

    def remaining(self, pot: int) -> list[CupTeam]:
        """Torbada hala duran toplar (torba sirasiyla)."""
        return [t for t in self._pots[pot] if t.id not in self._drawn]

    # ------------------------------------------------------- uygulanabilirlik
    def _league(self, team_id: int) -> str:
        return self._teams[team_id].league

    def _completable(self) -> bool:
        """Mevcut yerlesimden lig korumasina uyan tam bir kura var mi?"""
        if self._fmt is CupFormat.KNOCKOUT:
            unseeded = [t.league for t in self.remaining(1)]
            unseeded += [
                self._league(tie[0]) for tie in self._slots if tie[0] is not None and tie[1] is None
            ]
            seeded = [t.league for t in self.remaining(0)]
            return _knockout_completable(tuple(sorted(unseeded)), tuple(sorted(seeded)))

        pot = self.current_pot
        if pot is None:
            return True
        open_sets: list[tuple[str, ...]] = []
        closed_sets: list[tuple[str, ...]] = []
        for group in self._slots:
            leagues = tuple(self._league(tid) for tid in group if tid is not None)
            (open_sets if group[pot] is None else closed_sets).append(leagues)
        pending = tuple(sorted(t.league for t in self.remaining(pot)))
        future = tuple(
            tuple(sorted(t.league for t in self._pots[p])) for p in range(pot + 1, GROUP_COUNT)
        )
        return _groups_completable(pending, _canon(open_sets), _canon(closed_sets), future)

    def _place(self, team: CupTeam, pot: int, slot: int) -> DrawStep:
        partner: int | None = None
        if self._fmt is CupFormat.KNOCKOUT:
            position = 0 if pot == 1 else 1
            if position == 1:
                partner = self._slots[slot][0]
        else:
            position = pot
        self._slots[slot][position] = team.id
        self._drawn.add(team.id)
        step = DrawStep(
            number=len(self._steps) + 1,
            team_id=team.id,
            team_name=team.name,
            pot=pot,
            slot=slot,
            partner_id=partner,
        )
        self._steps.append(step)
        return step

    def _unplace(self) -> None:
        step = self._steps.pop()
        self._drawn.discard(step.team_id)
        if self._fmt is CupFormat.KNOCKOUT:
            self._slots[step.slot][0 if step.pot == 1 else 1] = None
        else:
            self._slots[step.slot][step.pot] = None

    def _target_slot(self, team: CupTeam, pot: int, protected: bool) -> int | None:
        """Top cekilirse gidecegi yer; kuralara/tamamlanabilirlige uymuyorsa None."""
        if self._fmt is CupFormat.KNOCKOUT:
            tie = len(self._steps) // 2
            if protected and pot == 0:
                if self._league(self._slots[tie][0]) == team.league:
                    return None
            return tie if self._fits(team, pot, tie, protected) else None

        for group_index, group in enumerate(self._slots):
            if group[pot] is not None:
                continue
            if protected and any(
                tid is not None and self._league(tid) == team.league for tid in group
            ):
                continue
            if self._fits(team, pot, group_index, protected):
                return group_index
        return None

    def _fits(self, team: CupTeam, pot: int, slot: int, protected: bool) -> bool:
        if not protected:
            return True
        self._place(team, pot, slot)
        try:
            return self._completable()
        finally:
            self._unplace()

    def _candidates(self, pot: int, protected: bool) -> list[tuple[CupTeam, int]]:
        found: list[tuple[CupTeam, int]] = []
        for team in self.remaining(pot):
            slot = self._target_slot(team, pot, protected)
            if slot is not None:
                found.append((team, slot))
        return found

    # ---------------------------------------------------------------- cekis
    def draw_next(self) -> DrawStep:
        """Tek top acar. Kura bittiyse DrawComplete."""
        pot = self.current_pot
        if pot is None:
            raise DrawComplete("Kura tamamlandı.")
        candidates = self._candidates(pot, self._protected)
        if not candidates:
            # Yalnizca bozuk/elle degistirilmis bir durumdan devam edilirse: kilitlenme yerine gevset
            self._relax()
            candidates = self._candidates(pot, False)
        rng = random.Random(f"{self._seed}:{len(self._steps) + 1}")
        team, slot = rng.choice(candidates)
        return self._place(team, pot, slot)

    def draw_pair(self) -> list[DrawStep]:
        """
        Kura gecesi tiklamasi. KNOCKOUT: eslesmeyi TAMAMLAYAN kadar top acar -- normalde iki top
        (seri basi olmayan torbadan ilk macin ev sahibi, sonra seri basi torbasindan rakibi);
        yarim kalmis bir eslesme varsa yalnizca rakibi. GROUPS: eslesme olmadigindan tek top.
        Kura bittiyse DrawComplete.
        """
        drawn = [self.draw_next()]
        if self._fmt is CupFormat.KNOCKOUT:
            while drawn[-1].partner_id is None and not self.complete:
                drawn.append(self.draw_next())
        return drawn

    @property
    def pair_count(self) -> int:
        """Kura gecesinde gereken tiklama sayisi: KNOCKOUT eslesme, GROUPS top sayisi."""
        return self._slot_count if self._fmt is CupFormat.KNOCKOUT else self.total_steps

    @property
    def pairs_drawn(self) -> int:
        """Tamamlanan eslesme (KNOCKOUT) ya da cekilen top (GROUPS) sayisi."""
        if self._fmt is CupFormat.KNOCKOUT:
            return sum(1 for step in self._steps if step.partner_id is not None)
        return len(self._steps)

    def draw_all(self) -> list[DrawStep]:
        """Kalan tum toplari ceker; bu cagrida cekilen adimlari dondurur."""
        drawn: list[DrawStep] = []
        while not self.complete:
            drawn.append(self.draw_next())
        return drawn

    # --------------------------------------------------------------- sonuc
    def pairs(self) -> list[tuple[int, int]]:
        """
        KNOCKOUT, tamamlanmis kura: agac sirasinda (ilk mac ev sahibi, rovans ev sahibi).
        Seri basi rovansi evinde oynar.
        """
        if self._fmt is not CupFormat.KNOCKOUT:
            raise ValueError("pairs() yalnızca eleme kurasında kullanılır.")
        if not self.complete:
            raise ValueError("Kura henüz tamamlanmadı.")
        return [(tie[0], tie[1]) for tie in self._slots]

    def groups(self) -> list[list[int]]:
        """GROUPS, tamamlanmis kura: A..D gruplari, torba sirasiyla takim id'leri."""
        if self._fmt is not CupFormat.GROUPS:
            raise ValueError("groups() yalnızca grup kurasında kullanılır.")
        if not self.complete:
            raise ValueError("Kura henüz tamamlanmadı.")
        return [list(group) for group in self._slots]

    def slots(self) -> list[list[int | None]]:
        """Kismi gorunum: KNOCKOUT [seri basi olmayan, seri basi] / GROUPS torba basina 4 yer."""
        return [list(slot) for slot in self._slots]

    # ------------------------------------------------------------ metinler
    def headline(self) -> str:
        """Siradaki islemin ekran metni."""
        pot = self.current_pot
        if pot is None:
            return "Kura tamamlandı"
        if self._fmt is CupFormat.KNOCKOUT:
            stage = STAGE_LABELS[self.first_stage]
            tie = len(self._steps) // 2
            if pot == 1:
                return f"2. torba: {slot_title(self._fmt, tie)} için takım çekiliyor"
            opponent = self._teams[self._slots[tie][0]].name
            return f"1. torba: {stage} rakibi çekiliyor ({slot_title(self._fmt, tie)} · {opponent})"
        left = len(self.remaining(pot))
        return f"{pot + 1}. torba: gruplara yerleşecek takım çekiliyor ({left} top kaldı)"

    def describe_step(self, step: DrawStep) -> str:
        """Duyuru metni, ornegin 'Eşleşme 3: Porto – Real Madrid' ya da 'Ajax → Grup B'."""
        title = slot_title(self._fmt, step.slot)
        if self._fmt is CupFormat.GROUPS:
            return f"{step.team_name} → {title} ({step.pot + 1}. torba)"
        if step.partner_id is None:
            return f"{title}: {step.team_name} – ?"
        return f"{title}: {self._teams[step.partner_id].name} – {step.team_name}"

    # ------------------------------------------------------------ kalicilik
    def to_state(self) -> dict:
        """JSON'a yazilabilir durum (yalnizca int/str/float/bool/list/dict/None)."""
        return {
            "version": STATE_VERSION,
            "format": self._fmt.value,
            "seed": self._seed,
            "protect_leagues": self._protect_requested,
            "protected": self._protected,
            "relaxed_note": self._relaxed_note,
            "pots": [
                [
                    {"id": t.id, "name": t.name, "league": t.league, "coefficient": float(t.coefficient)}
                    for t in pot
                ]
                for pot in self._pots
            ],
            "steps": [asdict(step) for step in self._steps],
        }

    @classmethod
    def from_state(cls, state: Mapping) -> DrawSession:
        """to_state ciktisindan oturumu kurar; adimlar dogrulanarak yeniden uygulanir."""
        if state.get("version") != STATE_VERSION:
            raise ValueError(f"Desteklenmeyen kura durumu sürümü: {state.get('version')!r}")
        pots = [
            [
                CupTeam(
                    id=int(t["id"]), name=str(t["name"]), league=str(t["league"]),
                    coefficient=float(t["coefficient"]),
                )
                for t in pot
            ]
            for pot in state["pots"]
        ]
        session = cls(CupFormat(state["format"]), pots, state["seed"], bool(state["protect_leagues"]))
        session._protected = bool(state.get("protected", session._protected))
        session._relaxed_note = state.get("relaxed_note", session._relaxed_note)
        for raw in state.get("steps", []):
            session._replay(raw)
        return session

    def _replay(self, raw: Mapping) -> None:
        pot = self.current_pot
        team_id = int(raw["team_id"])
        slot = int(raw["slot"])
        if (
            pot is None
            or int(raw["pot"]) != pot
            or team_id not in self._teams
            or team_id in self._drawn
            or self._pot_of[team_id] != pot
            or int(raw["number"]) != len(self._steps) + 1
            or not 0 <= slot < self._slot_count
        ):
            raise ValueError(f"Kura durumu bozuk (adım {raw.get('number')!r}).")
        if self._fmt is CupFormat.KNOCKOUT:
            if slot != len(self._steps) // 2:
                raise ValueError(f"Kura durumu bozuk: eşleşme sırası (adım {raw['number']}).")
        elif self._slots[slot][pot] is not None:
            raise ValueError(f"Kura durumu bozuk: grup dolu (adım {raw['number']}).")
        step = self._place(self._teams[team_id], pot, slot)
        if step.partner_id != raw.get("partner_id"):
            raise ValueError(f"Kura durumu bozuk: rakip uyuşmuyor (adım {raw['number']}).")


# ===========================================================================
# [6] TUR ILERLEMESI
# ===========================================================================

def next_round_pairs(winner_ids_in_slot_order: Sequence[int]) -> list[tuple[int, int]]:
    """
    Bir sonraki turun eslesme j'si = (2j) ve (2j+1) eslesmelerinin galipleri:
    (ilk mac ev sahibi = 2j+1'in galibi, rovans ev sahibi = 2j'nin galibi).
    Finalde sira yalnizca 'ilk/ikinci yazilan' anlamina gelir.
    """
    winners = list(winner_ids_in_slot_order)
    if len(winners) < 2 or len(winners) % 2:
        raise ValueError("Bir sonraki tur için çift sayıda (en az 2) galip gerekir.")
    if len(set(winners)) != len(winners):
        raise ValueError("Galip listesinde tekrar eden takım var.")
    return [(winners[2 * j + 1], winners[2 * j]) for j in range(len(winners) // 2)]


def group_qualifier_pairs(group_rankings: Sequence[Sequence[int]]) -> list[tuple[int, int]]:
    """
    A..D grup siralamalarindan ceyrek final agaci: A1-B2, C1-D2, B1-A2, D1-C2.
    Ikinci ilk maci evinde, grup birincisi rovansi evinde oynar. A1 ile B1 (ve ayni gruptan
    cikan iki takim) ancak finalde karsilasabilir.
    """
    if len(group_rankings) != GROUP_COUNT or any(len(g) < 2 for g in group_rankings):
        raise ValueError("Çeyrek final için 4 grubun ilk iki sırası gerekir.")
    a, b, c, d = (list(g) for g in group_rankings)
    pairs = [(b[1], a[0]), (d[1], c[0]), (a[1], b[0]), (c[1], d[0])]
    ids = [tid for pair in pairs for tid in pair]
    if len(set(ids)) != len(ids):
        raise ValueError("Grup sıralamalarında tekrar eden takım var.")
    return pairs


# ===========================================================================
# [7] GRUP FIKSTURU VE PUAN TABLOSU
# ===========================================================================

def group_schedule(team_ids: Sequence[int]) -> list[list[tuple[int, int]]]:
    """
    Cift devreli grup fiksturu: 4 takim -> 6 tur, her takim her turda bir kez oynar,
    herkes birbiriyle iki kez (ev/deplasman ters) karsilasir. Tur -> [(ev, dep), ...]
    """
    ids = list(team_ids)
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise ValueError("Grup fikstürü için en az 2 farklı takım gerekir.")
    return build_round_robin(ids)


@dataclass
class GroupRow:
    team_id: int
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    points: int = 0

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def _mini_table(
    team_ids: Sequence[int], results: Sequence[tuple[int, int, int, int]]
) -> dict[int, GroupRow]:
    rows = {tid: GroupRow(team_id=tid) for tid in team_ids}
    for home, away, hg, ag in results:
        if home not in rows or away not in rows:
            continue
        for tid, gf, ga in ((home, hg, ag), (away, ag, hg)):
            row = rows[tid]
            row.played += 1
            row.goals_for += gf
            row.goals_against += ga
            if gf > ga:
                row.won += 1
                row.points += 3
            elif gf == ga:
                row.drawn += 1
                row.points += 1
            else:
                row.lost += 1
    return rows


def _buckets(ids: Sequence[int], key) -> list[list[int]]:
    """Anahtar degerine gore (azalan) esit gruplar."""
    ordered = sorted(ids, key=key, reverse=True)
    out: list[list[int]] = []
    for tid in ordered:
        if out and key(out[-1][0]) == key(tid):
            out[-1].append(tid)
        else:
            out.append([tid])
    return out


def rank_group(
    team_ids: Sequence[int],
    results: Sequence[tuple[int, int, int, int]],
    coefficients: Mapping[int, float] | None = None,
    names: Mapping[int, str] | None = None,
) -> list[GroupRow]:
    """
    Puan tablosu (en iyi once). results: (ev_id, dep_id, ev_gol, dep_gol); grup disi takim
    iceren maclar yok sayilir. Siralama (UEFA benzeri):
        1) puan
        2) esit takimlar arasindaki maclarda puan, averaj, atilan gol
           (hala esit kalan alt kume icin 2. madde yalnizca o takimlarla yeniden uygulanir)
        3) genel averaj, 4) genel atilan gol, 5) katsayi (yuksek), 6) ad, id
    """
    ids = list(dict.fromkeys(team_ids))
    members = set(ids)
    valid = [r for r in results if r[0] in members and r[1] in members and r[0] != r[1]]
    table = _mini_table(ids, valid)
    coefs = coefficients or {}
    labels = names or {}

    def fallback(tid: int) -> tuple[int, int, float, str, int]:
        row = table[tid]
        return (-row.goal_difference, -row.goals_for, -float(coefs.get(tid, 0.0)),
                str(labels.get(tid, "")).casefold(), tid)

    def resolve(tied: list[int]) -> list[int]:
        if len(tied) == 1:
            return tied
        subset = set(tied)
        h2h = _mini_table(tied, [r for r in valid if r[0] in subset and r[1] in subset])
        buckets = _buckets(
            tied, key=lambda t: (h2h[t].points, h2h[t].goal_difference, h2h[t].goals_for)
        )
        if len(buckets) == 1:
            return sorted(tied, key=fallback)
        return [tid for bucket in buckets for tid in resolve(bucket)]

    ordered: list[int] = []
    for bucket in _buckets(ids, key=lambda t: table[t].points):
        ordered.extend(resolve(bucket))
    return [table[tid] for tid in ordered]


# ===========================================================================
# [8] YARDIMCI
# ===========================================================================

def protection_feasible(fmt: CupFormat, pots: Sequence[Sequence[CupTeam]]) -> bool:
    """Bu torbalarla lig korumasina uyan tam bir kura var mi? (kura oncesi uyari icin)"""
    return DrawSession(fmt, [list(p) for p in pots], seed=0, protect_leagues=True).protected
