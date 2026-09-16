"""
concerns.py
===========
Oyuncu kaygilari: oynama suresi ve maas (12. Asama, Soccer Manager "concerns" yeniden tasarimi).
SAF MANTIK: veritabani ve ORM bilmez; career_manager.py uygular.

Oynama suresi penceresi (players.minutes_window, en fazla CONCERN_WINDOW resmi mac, en yeni sonda):
    [oynadigi_dk, beklenen_maclik_dk, beklenti_payi, hazirlik_dk]
        lig maci      : [dakika, 90, pay, 0]         (uzatma dakikalari 90'la sinirlanir)
        kupa maci     : [dakika x 0.5, 45, pay, 0]   (SM: kupa maclari toplam mac hakkinin bir PAYI kadar sayilir)
        hazirlik maci : son olayin hazirlik_dk alanina dakika x 0.5 eklenir (yeni olay acmaz, beklenen sure yok)
    Sakat/cezali oyuncu icin olay yazilmaz (kaygi o maclar icin durur).
    Beklenti payi MAC ANINDA yazilir: sezon ortasinda gucu artan oyuncunun beklentisi pencere boyunca
    yavasca yukselir (ucurum yok).

Beklenti payi (expected_share):
    sozlesme rolu   : Yildiz 0.75 · As 0.55 · Yedek 0.20
    kadro konumu    : mevkisinde ilk POSITION_SLOTS icindeyse en az 0.45 (gucu artan yedek sure bekler)
    kaleci          : mevkisinde ilk sirada degilse ya da macta gucu kendisine yakin (en fazla GK_PEER_TOLERANCE
                      geride) baska bir kaleci oynadiysa en fazla GK_BACKUP_SHARE: kalede tek kisi oynar; ancak
                      belirgin sekilde daha zayif kaleciye kulubede birakilan kaleci sure bekler

Seviye (target_level), oran = oynanan / istenen (maclik):
    >= 0.80 yok · >= 0.55 "Süre bekliyor" · >= 0.30 "Şikayetçi" · alti "Ayrılmak istiyor"
    istenen < MIN_WANTED_MATCHES ise degerlendirme yapilmaz (az veri / dusuk beklenti).
    Firsat kaygisi: A takimin toplam beklentisi mac basina dagitilabilir sureyi (11 maclik) asiyorsa kadro
    sisirilmistir; Yedek rolundeki oyuncularin esikleri OPPORTUNITY_SHIFT kadar yukselir (daha erken kaygilanir).

Haftalik gecis (next_level): seviye hedefe haftada EN FAZLA bir kademe yaklasir (kademeli). Oyuncu son RESMI
macta oynadiysa kaygi DURAKLAR: yukselmez, moral cezasi yazilmaz (dusmesi serbest).

Moral (level_morale_effect): haftalik 0 / -1 / -2 / -4; moral seviyenin tabanina (- / 60 / 45 / 30) inince
durur: kaygi morali kademeli dusurur, sifira cokertmez (secim gucu -> kulube -> daha fazla kaygi dongusu sinirli).
Eski "uzun sure oynamayan -1" cezasi (career_manager.idle_morale_penalty) kariyer modunda bunun YERINE gecer.

Maas talebi (wage_demand_amount):
    maasi belirlendiginden beri gucu >= WAGE_DEMAND_OVERALL_RISE artan ya da maasi yeni seviyesinin beklenen
    maasinin WAGE_DEMAND_UNDERPAID_RATIO altinda kalan oyuncu yeni sozlesme ister:
        talep = max(beklenen maas, mevcut maas x 1.10), 100'e yuvarlanir
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from finance import expected_wage
from models import Position, SquadRole

CONCERN_WINDOW = 8                     # penceredeki en fazla olay
MATCH_MINUTES = 90
CUP_MATCH_WEIGHT = 0.5
FRIENDLY_MINUTES_WEIGHT = 0.5
MIN_WANTED_MATCHES = 1.5               # istenen bunun altindaysa kaygi hedefi yok

ROLE_SHARE: dict[SquadRole, float] = {
    SquadRole.STAR: 0.75, SquadRole.FIRST_TEAM: 0.55, SquadRole.BACKUP: 0.20,
}
POSITION_SLOTS: dict[Position, int] = {Position.GK: 1, Position.DEF: 4, Position.MID: 4, Position.FWD: 2}
STANDING_SHARE = 0.45                  # mevkisinde ilk POSITION_SLOTS icindeki oyuncunun asgari beklentisi
GK_BACKUP_SHARE = 0.10                 # ikinci kalecinin beklentisi (pencerede degerlendirme esiginin altinda)
GK_PEER_TOLERANCE = 3                  # oynayan kaleci en fazla bu kadar zayifsa kulubedeki kaleci sure beklemez
SQUAD_MATCH_CAPACITY = 11.0            # mac basina dagitilabilir sure (maclik): 11 x 90 dk

# Oran esikleri (NONE, WATCH, CONCERNED): oran >= esik -> o seviye
LEVEL_THRESHOLDS: tuple[float, float, float] = (0.80, 0.55, 0.30)
OPPORTUNITY_SHIFT = 0.15               # sisirilmis kadroda Yedek rolu esik kaymasi

LEVEL_MORALE: tuple[int, int, int, int] = (0, -1, -2, -4)
LEVEL_MORALE_FLOOR: tuple[int, int, int, int] = (0, 60, 45, 30)

WAGE_DEMAND_OVERALL_RISE = 3
WAGE_DEMAND_UNDERPAID_RATIO = 0.60
WAGE_DEMAND_MIN_RAISE = 1.10
WAGE_ACCEPT_MORALE = 4
WAGE_REFUSE_MORALE = -8
WAGE_PENDING_MORALE = -1               # talep bekledigi her hafta (talep edildigi hafta haric)
WAGE_ACCEPT_MIN_YEARS = 2              # yeni sozlesme en az bu kadar yil


class ConcernLevel(int, enum.Enum):
    NONE = 0
    WATCH = 1
    CONCERNED = 2
    ANGRY = 3


LEVEL_LABELS: dict[ConcernLevel, str] = {
    ConcernLevel.NONE: "Mutlu",
    ConcernLevel.WATCH: "Süre bekliyor",
    ConcernLevel.CONCERNED: "Şikayetçi",
    ConcernLevel.ANGRY: "Ayrılmak istiyor",
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def level_of(value: int | ConcernLevel | None) -> ConcernLevel:
    """DB degeri (0-3) -> ConcernLevel; gecersiz/None -> NONE."""
    try:
        return ConcernLevel(int(_clamp(int(value or 0), 0, 3)))
    except (TypeError, ValueError):
        return ConcernLevel.NONE


# ===========================================================================
# 1) BEKLENTI
# ===========================================================================

def expected_share(role: SquadRole, position: Position, position_rank: int, peer_keeper_played: bool = False) -> float:
    """
    Oyuncunun mac suresinden bekledigi pay (0-1). position_rank: mevkisindeki guc sirasi (1 = en iyi).
    Sozlesme rolu ile kadro konumunun buyugu. Kaleci: ikinci siradaysa ya da macta gucu kendisine yakin baska bir
    kaleci oynadiysa (peer_keeper_played; bkz. keeper_peer_played) en fazla GK_BACKUP_SHARE.
    """
    share = ROLE_SHARE.get(role, ROLE_SHARE[SquadRole.FIRST_TEAM])
    if position_rank <= POSITION_SLOTS.get(position, 0):
        share = max(share, STANDING_SHARE)
    if position is Position.GK and (position_rank > POSITION_SLOTS[Position.GK] or peer_keeper_played):
        share = min(share, GK_BACKUP_SHARE)
    return round(share, 3)


def keeper_peer_played(own_overall: int, played_keeper_overalls: Iterable[int]) -> bool:
    """Kulubedeki kalecinin yerine gucu en fazla GK_PEER_TOLERANCE geride (ya da daha iyi) bir kaleci oynadi mi?"""
    return any(other >= own_overall - GK_PEER_TOLERANCE for other in played_keeper_overalls)


def position_ranks(players: Iterable[tuple[int, Position, int]]) -> dict[int, int]:
    """(oyuncu id, mevki, guc) -> mevkisindeki sira (1 tabanli; esit gucte id kucuk olan once)."""
    by_position: dict[Position, list[tuple[int, int]]] = {}
    for pid, position, overall in players:
        by_position.setdefault(position, []).append((pid, overall))
    ranks: dict[int, int] = {}
    for group in by_position.values():
        for rank, (pid, _overall) in enumerate(sorted(group, key=lambda x: (-x[1], x[0])), start=1):
            ranks[pid] = rank
    return ranks


def squad_overloaded(shares: Iterable[float], capacity: float = SQUAD_MATCH_CAPACITY) -> bool:
    """A takimin toplam beklentisi (maclik) mac basina dagitilabilir sureyi asiyor mu? (kadro sisirme)"""
    return sum(shares) > capacity


# ===========================================================================
# 2) PENCERE
# ===========================================================================

def match_entry(minutes: int, share: float, cup: bool = False) -> list[float]:
    """Resmi mac olayi. Uzatmali macta dakika 90 ile sinirlanir; kupa maci CUP_MATCH_WEIGHT agirliginda."""
    weight = CUP_MATCH_WEIGHT if cup else 1.0
    played = min(max(0, int(minutes)), MATCH_MINUTES)
    return [round(played * weight, 1), round(MATCH_MINUTES * weight, 1), round(float(share), 3)]


def add_friendly_minutes(window, minutes: int, share: float) -> list[list[float]]:
    """
    Hazirlik maci: oynanan dakika yarim agirlikla SON olayin 4. alanina (hazirlik dakikasi) eklenir; beklenen sure
    eklenmez ve pencerede yeni olay acilmaz (hazirlik maclari resmi maclari pencereden itmez, son resmi mactaki
    "oynadi" bilgisini degistirmez). Pencere bossa [0, 0, pay, dk] olayi acilir. YENI liste dondurur.
    """
    bonus = round(min(max(0, int(minutes)), MATCH_MINUTES) * FRIENDLY_MINUTES_WEIGHT, 1)
    rows = [list(e) for e in _entries(window)]
    if not rows:
        return [[0.0, 0.0, round(float(share), 3), bonus]]
    rows[-1][3] = round(rows[-1][3] + bonus, 1)
    return rows


def _entries(window) -> list[tuple[float, float, float, float]]:
    """JSONB penceresi -> dogrulanmis olaylar (oynanan, beklenen, pay, hazirlik dk); bozuk girdiler atlanir."""
    out: list[tuple[float, float, float, float]] = []
    if not isinstance(window, list):
        return out
    for item in window:
        if not isinstance(item, Sequence) or isinstance(item, str) or len(item) < 3:
            continue
        try:
            played, available, share = float(item[0]), float(item[1]), float(item[2])
            friendly = float(item[3]) if len(item) > 3 else 0.0
        except (TypeError, ValueError):
            continue
        out.append((max(0.0, played), max(0.0, available), _clamp(share, 0.0, 1.0), max(0.0, friendly)))
    return out


def push_entry(window, entry: Sequence[float], size: int = CONCERN_WINDOW) -> list[list[float]]:
    """Pencereye resmi mac olayi ekler, en fazla `size` olay kalir (YENI liste: JSONB degisikligi algilansin)."""
    rows = [list(e) for e in _entries(window)]
    rows.append([float(x) for x in entry[:3]] + [0.0])
    return rows[-size:]


@dataclass(frozen=True)
class PlayingTime:
    wanted_matches: float        # beklenen oynama (maclik)
    played_matches: float        # oynadigi (maclik; hazirlik maclari yarim agirlikla)
    active: bool                 # son resmi macta oynadi mi (kaygi duraklar)

    @property
    def ratio(self) -> float | None:
        return None if self.wanted_matches <= 0 else self.played_matches / self.wanted_matches


def playing_time(window) -> PlayingTime:
    """Penceredeki istenen / oynanan sure ve son resmi macta oynayip oynamadigi."""
    entries = _entries(window)
    wanted = sum(available * share for _played, available, share, _friendly in entries) / MATCH_MINUTES
    played = sum(p + friendly for p, _available, _share, friendly in entries) / MATCH_MINUTES
    last_official = next((e for e in reversed(entries) if e[1] > 0), None)
    active = last_official is not None and last_official[0] > 0
    return PlayingTime(round(wanted, 1), round(played, 1), active)


# ===========================================================================
# 3) SEVIYE
# ===========================================================================

def target_level(time: PlayingTime, role: SquadRole, overloaded: bool = False) -> ConcernLevel:
    """Oynama suresine gore olmasi gereken seviye (haftalik gecis kademeli: next_level)."""
    if time.wanted_matches < MIN_WANTED_MATCHES:
        return ConcernLevel.NONE
    shift = OPPORTUNITY_SHIFT if overloaded and role is SquadRole.BACKUP else 0.0
    ratio = time.played_matches / time.wanted_matches
    none_at, watch_at, concerned_at = (t + shift for t in LEVEL_THRESHOLDS)
    if ratio >= none_at:
        return ConcernLevel.NONE
    if ratio >= watch_at:
        return ConcernLevel.WATCH
    if ratio >= concerned_at:
        return ConcernLevel.CONCERNED
    return ConcernLevel.ANGRY


def next_level(current: int | ConcernLevel, target: ConcernLevel, active: bool) -> ConcernLevel:
    """Hedefe haftada en fazla bir kademe. Aktif oynayan oyuncunun kaygisi yukselmez (duraklar)."""
    now = level_of(current)
    if target > now:
        return now if active else ConcernLevel(now + 1)
    if target < now:
        return ConcernLevel(now - 1)
    return now


def level_morale_effect(level: int | ConcernLevel, active: bool = False, morale: int | None = None) -> int:
    """
    Seviyenin haftalik moral etkisi; aktif oynayan oyuncuda durur (0). morale verilirse seviyenin tabanina
    (LEVEL_MORALE_FLOOR) kadar dusurur, tabandaki ya da altindaki morale dokunmaz.
    """
    lv = level_of(level)
    effect = 0 if active else LEVEL_MORALE[lv]
    if effect == 0 or morale is None:
        return effect
    floor = LEVEL_MORALE_FLOOR[lv]
    if morale <= floor:
        return 0
    return max(effect, floor - int(morale))


def reason_text(level: int | ConcernLevel, time: PlayingTime, role: SquadRole, role_label: str,
                overloaded: bool = False) -> str:
    """Oyuncu satirindaki kisa Turkce gerekce."""
    lv = level_of(level)
    if lv is ConcernLevel.NONE:
        if time.wanted_matches < MIN_WANTED_MATCHES:
            return "Oynama süresinden memnun."
        return f"Oynama süresinden memnun ({time.played_matches:g}/{time.wanted_matches:g} maç)."
    text = (f"{role_label} olarak {time.wanted_matches:g} maçlık süre bekliyor, "
            f"{time.played_matches:g} maçlık oynadı.")
    if overloaded and role is SquadRole.BACKUP:
        text += " Kadro çok kalabalık, forma şansı göremiyor."
    if time.active:
        text += " Son maçta oynadı, kaygısı şimdilik duruldu."
    return text


# ===========================================================================
# 4) MAAS TALEBI
# ===========================================================================

def wage_demand_amount(overall: int, contract_overall: int | None, current_wage: int, team_reputation: int,
                       role: SquadRole) -> int | None:
    """
    Yeni sozlesme talebi (haftalik EUR) ya da None. contract_overall None: maasin belirlendigi guc bilinmiyor.
    Dusuk maas kurali, sozlesmeden beri gucu artmamis oyuncuda (contract_overall == overall) tekrar calismaz:
    reddedilen talep her hafta yinelenmez, oyuncu ancak gucu yeniden artinca ister.
    """
    target = expected_wage(overall, team_reputation, role)
    rose = contract_overall is not None and overall - contract_overall >= WAGE_DEMAND_OVERALL_RISE
    underpaid = (current_wage < target * WAGE_DEMAND_UNDERPAID_RATIO
                 and (contract_overall is None or overall > contract_overall))
    if not (rose or underpaid):
        return None
    demand = int(round(max(target, current_wage * WAGE_DEMAND_MIN_RAISE) / 100) * 100)
    return demand if demand > current_wage else None


# ===========================================================================
# 5) RAPOR SATIRI
# ===========================================================================

@dataclass(frozen=True)
class ConcernRow:
    """player_concerns() satiri: arayuzun oyuncu tablosu icin duz degerler."""
    player_id: int
    name: str
    position: str                # "GK" / "DEF" / "MID" / "FWD"
    role: str                    # SquadRole degeri ("STAR" / "FIRST_TEAM" / "BACKUP")
    role_label: str              # "Yıldız" / "As" / "Yedek"
    level: str                   # ConcernLevel adi ("NONE" / "WATCH" / "CONCERNED" / "ANGRY")
    level_value: int             # 0-3
    label: str                   # "Mutlu" / "Süre bekliyor" / "Şikayetçi" / "Ayrılmak istiyor"
    wanted: float                # beklenen oynama (maclik)
    played: float                # oynadigi (maclik)
    active: bool                 # son resmi macta oynadi (kaygi duraklar)
    overloaded: bool             # kadro sisirilmis (firsat kaygisi)
    reason: str
    wage_demand: int | None      # bekleyen yeni sozlesme talebi (haftalik EUR)
    current_wage: int


def summary_counts(rows: Iterable[ConcernRow]) -> Mapping[str, int]:
    """Seviye adi -> oyuncu sayisi (arayuz rozeti icin)."""
    counts = {level.name: 0 for level in ConcernLevel}
    for row in rows:
        counts[row.level] = counts.get(row.level, 0) + 1
    return counts
