"""
finance.py
==========
Finansal kurallar (5. Asama). SAF MANTIK: veritabanina yazmaz, ORM bilmez.

Iki kalemli butce:
    transfer_budget -> bonservis icin toplam para (EUR)
    wage_budget     -> HAFTALIK toplam maas havuzu (EUR/hafta)

Bu ikisi 52 hafta carpaniyla birbirine donusur:
    Haftalik 10.000 EUR maas alani acmak, transfer butcesinden 520.000 EUR gotururur.

Haftalik maas akisi (career_manager her hafta uygular):
    fark = wage_budget - wage_bill
    transfer_budget += fark
Yani havuzdan artan para kasaya yazilir, havuzu asan maas kasadan cikar.
Boylece "maaslar haftalik olarak wage_budget havuzundan duser" kurali
transfer butcesiyle tutarli sekilde baglanir ve butce asimi cezalandirilir.
"""

from __future__ import annotations

from dataclasses import dataclass

from models import Position, SquadRole

WEEKS_PER_YEAR = 52

# --- Piyasa degeri egrisi ---------------------------------------------------
# 70 OVR referans; her +1 OVR degeri %19.6 buyutur (70 -> 1.2M, 85 -> ~11M, 90 -> ~40M)
VALUE_BASE_OVERALL = 70
VALUE_BASE_AMOUNT = 1_200_000
VALUE_GROWTH = 1.196

# --- Maas egrisi ------------------------------------------------------------
# 70 OVR referans; her +1 OVR maasi %13.5 buyutur (70 -> 20k/hf, 85 -> ~132k/hf)
WAGE_BASE_OVERALL = 70
WAGE_BASE_AMOUNT = 20_000
WAGE_GROWTH = 1.135
WAGE_REPUTATION_PIVOT = 85          # bu itibardaki kulup "referans" maas oder

POSITION_VALUE_FACTOR: dict[Position, float] = {
    Position.GK: 0.75, Position.DEF: 0.90, Position.MID: 1.00, Position.FWD: 1.15,
}

ROLE_WAGE_PREMIUM: dict[SquadRole, float] = {
    SquadRole.STAR: 1.30, SquadRole.FIRST_TEAM: 1.00, SquadRole.BACKUP: 0.72,
}

# Kadro icin ayrilan haftalik havuzda tutulan pay (teknik heyet dahil)
DEFAULT_WAGE_HEADROOM = 1.18


class BudgetError(Exception):
    """Butce kurali ihlali. Mesaji dogrudan kullaniciya gosterilebilir."""


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Degerleme
# ---------------------------------------------------------------------------

def age_value_factor(age: int) -> float:
    """Yas egrisi: 21-26 zirve, 30 sonrasi hizli dusus."""
    if age <= 19:
        return 1.05
    if age <= 23:
        return 1.15
    if age <= 26:
        return 1.00
    if age <= 29:
        return 0.82
    if age <= 31:
        return 0.60
    if age <= 33:
        return 0.40
    return 0.22


# --- Potansiyel primi (10. Asama) -------------------------------------------
# Genc oyuncunun degeri, potansiyele dogru kaydirilmis "etkin guc" ile hesaplanir:
#     etkin = overall + pay(yas) x (potansiyel - overall)
# 17 yas 60 -> 88: etkin 76.8 -> ~0.2M yerine ~4.3M. 26+ yasta prim yok.
POTENTIAL_VALUE_SHARE: tuple[tuple[int, float], ...] = ((19, 0.60), (21, 0.50), (23, 0.30), (25, 0.10))
MIN_VALUE_WITH_POTENTIAL = 10_000     # akademi gencinin degeri sifira yuvarlanmasin


def potential_value_share(age: int) -> float:
    """Potansiyel farkinin degere yansiyan payi: <=19 0.60, 20-21 0.50, 22-23 0.30, 24-25 0.10, 26+ 0."""
    for max_age, share in POTENTIAL_VALUE_SHARE:
        if age <= max_age:
            return share
    return 0.0


def market_value(overall: int, age: int, position: Position, potential: int | None = None) -> int:
    """
    Oyuncunun piyasa degeri (EUR). 10.000'e yuvarlanir.
    potential verilirse (10. Asama) genc ve yuksek potansiyelli oyuncuya prim eklenir ve deger en az
    MIN_VALUE_WITH_POTENTIAL olur; None ise eski egri birebir aynidir.
    """
    effective = float(overall)
    if potential is not None and potential > overall:
        effective += potential_value_share(age) * (potential - overall)
    base = VALUE_BASE_AMOUNT * VALUE_GROWTH ** (effective - VALUE_BASE_OVERALL)
    raw = base * age_value_factor(age) * POSITION_VALUE_FACTOR[position]
    value = int(round(raw / 10_000) * 10_000)
    return max(MIN_VALUE_WITH_POTENTIAL, value) if potential is not None else value


def transfer_budget_for_reputation(reputation: int) -> int:
    """
    Itibardan baslangic transfer butcesi (FM dunyasi icin; sentetik dunyayla ayni egri):
        itibar 78 -> ~45M · 92 -> ~180M · 96 -> ~270M. 100.000'e yuvarlanir.
    """
    raw = 7_600_000 * 1.104 ** (reputation - 60)
    return int(round(raw / 100_000) * 100_000)


def reputation_wage_factor(team_reputation: int) -> float:
    """Buyuk kulup daha cok oder; kucuk kulubun maas tavani dusuktur."""
    return _clamp((team_reputation / WAGE_REPUTATION_PIVOT) ** 1.5, 0.45, 1.60)


def expected_wage(
    overall: int,
    team_reputation: int,
    role: SquadRole = SquadRole.FIRST_TEAM,
) -> int:
    """Oyuncunun bu kulupte bekledigi HAFTALIK maas (EUR). 100'e yuvarlanir."""
    base = WAGE_BASE_AMOUNT * WAGE_GROWTH ** (overall - WAGE_BASE_OVERALL)
    raw = base * reputation_wage_factor(team_reputation) * ROLE_WAGE_PREMIUM[role]
    return int(round(raw / 100) * 100)


ACADEMY_MIN_WAGE = 100


def academy_wage(overall: int, team_reputation: int) -> int:
    """
    Akademi (U-21) oyuncusunun haftalik genc sozlesmesi: yedek rolu egrisi, en az 100 EUR.
    Akademi maaslari A takim maas havuzuna yazilmaz (Team.player_wage_bill yalnizca A takim);
    oyuncu A takima yukselince maasi havuza girer.
    """
    return max(ACADEMY_MIN_WAGE, expected_wage(overall, team_reputation, SquadRole.BACKUP))


# ---------------------------------------------------------------------------
# Butce hesaplari
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WageSummary:
    """Bir takimin haftalik maas tablosu."""
    player_wages: int
    staff_wages: int
    wage_budget: int

    @property
    def total(self) -> int:
        return self.player_wages + self.staff_wages

    @property
    def free(self) -> int:
        """Havuzda kalan haftalik alan (negatifse butce asimi)."""
        return self.wage_budget - self.total

    @property
    def usage_pct(self) -> float:
        return 100.0 * self.total / self.wage_budget if self.wage_budget else 0.0

    @property
    def overspending(self) -> bool:
        return self.free < 0


def wage_summary(player_wages: int, staff_wages: int, wage_budget: int) -> WageSummary:
    return WageSummary(player_wages, staff_wages, wage_budget)


def weekly_to_transfer(weekly_amount: int) -> int:
    """Haftalik maas alani <-> bonservis parasi donusumu (yillik yuk)."""
    return weekly_amount * WEEKS_PER_YEAR


def plan_budget_shift(
    transfer_budget: int,
    wage_budget: int,
    weekly_delta: int,
    committed_weekly: int,
) -> tuple[int, int]:
    """
    Butce kaydirmayi PLANLAR (uygulamaz): haftalik maas havuzunu weekly_delta
    kadar degistirir, karsiligini transfer butcesinden alir/ekler.

        weekly_delta > 0 : maas havuzu buyur, transfer butcesi 52x azalir
        weekly_delta < 0 : maas havuzu kucultulur, transfer butcesi 52x artar

    committed_weekly: halihazirda odenen maaslar (oyuncu + personel).
    Havuz bunun altina indirilemez, transfer butcesi eksiye dusemez.

    Donus: (yeni_transfer_budget, yeni_wage_budget). Kural ihlalinde BudgetError.
    """
    if weekly_delta == 0:
        raise BudgetError("Sıfır tutarlı kaydırma yapılamaz.")

    cost = weekly_to_transfer(weekly_delta)
    new_transfer = transfer_budget - cost
    new_wage = wage_budget + weekly_delta

    if new_transfer < 0:
        short = -new_transfer
        max_weekly = transfer_budget // WEEKS_PER_YEAR
        raise BudgetError(
            f"Transfer bütçesi {short:,.0f} EUR yetersiz. "
            f"En fazla haftalık {max_weekly:,.0f} EUR aktarabilirsin."
        )
    if new_wage < 0:
        raise BudgetError("Maaş havuzu negatif olamaz.")
    if new_wage < committed_weekly:
        raise BudgetError(
            f"Havuz mevcut maaş yükünün ({committed_weekly:,.0f} EUR/hafta) altına inemez. "
            f"En fazla haftalık {wage_budget - committed_weekly:,.0f} EUR geri çekebilirsin."
        )
    return new_transfer, new_wage


def max_shiftable_to_wages(transfer_budget: int) -> int:
    """Transfer butcesinden maas havuzuna aktarilabilecek en yuksek haftalik tutar."""
    return max(0, transfer_budget // WEEKS_PER_YEAR)


def max_shiftable_to_transfer(wage_budget: int, committed_weekly: int) -> int:
    """Maas havuzundan geri cekilebilecek en yuksek haftalik tutar."""
    return max(0, wage_budget - committed_weekly)


def wage_budget_bounds(transfer_budget: int, wage_budget: int, committed_weekly: int) -> tuple[int, int]:
    """
    Kaydirici (slider) icin gecerli haftalik maas havuzu araligi.
        alt sinir: mevcut maas yuku (zaten asim varsa mevcut havuz; daha asagi inilemez)
        ust sinir: mevcut havuz + transfer butcesinin haftaliga cevrilebilen kismi
    """
    low = min(wage_budget, committed_weekly)
    high = wage_budget + max_shiftable_to_wages(transfer_budget)
    return low, high


@dataclass(frozen=True)
class ShiftPreview:
    """Butce kaydirmanin UYGULANMADAN onizlemesi (arayuz anlik gosterir)."""
    weekly_delta: int
    transfer_impact: int              # transfer butcesine etkisi (negatif = harcanir)
    new_transfer_budget: int
    new_wage_budget: int
    valid: bool
    message: str | None = None

    @property
    def changed(self) -> bool:
        return self.weekly_delta != 0


def preview_budget_shift(
    transfer_budget: int,
    wage_budget: int,
    target_wage_budget: int,
    committed_weekly: int,
) -> ShiftPreview:
    """Hedef haftalik havuza gecmenin sonucunu hesaplar; kural ihlalini hata firlatmadan bildirir."""
    delta = target_wage_budget - wage_budget
    impact = -weekly_to_transfer(delta)
    if delta == 0:
        return ShiftPreview(0, 0, transfer_budget, wage_budget, True)
    try:
        new_transfer, new_wage = plan_budget_shift(transfer_budget, wage_budget, delta, committed_weekly)
    except BudgetError as exc:
        return ShiftPreview(delta, impact, transfer_budget + impact, target_wage_budget, False, str(exc))
    return ShiftPreview(delta, impact, new_transfer, new_wage, True)


def can_afford_transfer(transfer_budget: int, fee: int) -> bool:
    return fee <= transfer_budget


def can_afford_wage(free_weekly: int, wage: int) -> bool:
    return wage <= free_weekly


def auto_shift_for_wage(
    transfer_budget: int,
    wage_budget: int,
    free_weekly: int,
    needed_weekly: int,
    reserve_fee: int = 0,
) -> int:
    """
    AI kuluplerinin kullandigi otomatik butce kaydirma.

    Maas alani yetmiyorsa acigi kapatacak haftalik tutari hesaplar; ancak
    reserve_fee kadar bonservis parasini dokunulmaz birakir.
    Donus: kaydirilacak haftalik tutar (0 = kaydirma yok/mumkun degil).
    """
    shortfall = needed_weekly - free_weekly
    if shortfall <= 0:
        return 0
    spare = max(0, transfer_budget - reserve_fee)
    affordable = spare // WEEKS_PER_YEAR
    return int(min(shortfall, affordable))


def format_money(amount: float) -> str:
    """Insan okuyacak kisa para bicimi: 12.5M EUR, 850K EUR, 900 EUR."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.1f}M EUR"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.0f}K EUR"
    return f"{sign}{a:.0f} EUR"


# ===========================================================================
# 12. Asama: lig yayin geliri (TV), lig odul parasi, kupa primleri, baskan guvencesi
# ===========================================================================
# Kalibrasyon (sentetik dunya: 4 takimli ligler, 6 lig haftasi + 7 haftalik kupa; bkz. career_manager):
#   Soccer Manager'daki gibi lig gelirinin yaklasik yarisi TV payidir: bir kulubun haftalik TV payi, lig
#   ortalamasindaki bir kulubun haftalik sponsor + mac gunu gelirine (facilities.py) yakindir.
#       lig itibar ortalamasi 76 -> ~490K/hafta · 85 -> ~910K · 89.5 -> ~1.22M · 95 -> ~1.65M (4 kulup)
#   TV havuzu kulup sayisiyla dogrusal DEGIL (kulup^0.9) buyur: kalabalik ligde pay biraz kuculur
#   (20 kulup: 4 kulupluk ligdeki payin ~%85'i).
#   Lig odulu, kulubun sezonluk TV gelirine (pay x lig haftasi) oranla: sampiyon 1.0x, sonuncu 0.10x, arasi
#   (sira payi)^1.5 egrisiyle azalir. 4 kulupluk ligde 1.00 / 0.59 / 0.27 / 0.10.
#   Kupa primleri sabittir (kupa takvimi her dunyada ayni uzunlukta): eleme turunu gecen / elenen.
#       16 takim eleme: sampiyon toplam 12M, finalist 8.5M, yari finalist 3.75M, ceyrek finalist 1.75M, son 16 0.5M

TV_BASE_WEEKLY = 50_000
TV_RANGE_WEEKLY = 2_400_000
TV_EXPONENT = 3.0
TV_POOL_CLUB_EXPONENT = 0.9          # lig havuzu = kulup basi deger x kulup^0.9

PRIZE_CHAMPION_SHARE = 1.0           # sezonluk TV gelirinin katsayisi
PRIZE_LAST_SHARE = 0.10
PRIZE_CURVE = 1.5

# asama -> (turu gecen / grubundan cikan, elenen) EUR
CUP_ROUND_PRIZES: dict[str, tuple[int, int]] = {
    "GROUP": (1_500_000, 750_000),
    "R16": (1_000_000, 500_000),
    "QF": (1_500_000, 750_000),
    "SF": (2_500_000, 1_250_000),
    "FINAL": (7_000_000, 3_500_000),
}

CHAIRMAN_FLOOR_SHARE = 0.50          # net deger lig ortalamasinin bu payinin altindaysa baskan tamamlar
CHAIRMAN_ROUNDING = 100_000


def _league_reputation_share(league_reputation: float | None) -> float:
    """Lig itibar ortalamasi -> 0..1 (40 ve alti 0, 100 -> 1); facilities.py ile ayni olcek."""
    rep = 70.0 if league_reputation is None else float(league_reputation)
    return _clamp((rep - 40.0) / 60.0, 0.0, 1.0)


def tv_pool_weekly(league_reputation: float | None, clubs: int) -> int:
    """Ligin haftalik toplam TV havuzu (EUR). clubs < 1 -> 0."""
    clubs = int(clubs)
    if clubs < 1:
        return 0
    per_club = TV_BASE_WEEKLY + TV_RANGE_WEEKLY * _league_reputation_share(league_reputation) ** TV_EXPONENT
    return int(per_club * clubs ** TV_POOL_CLUB_EXPONENT)


def tv_money_weekly(league_reputation: float | None, clubs: int) -> int:
    """
    Bir kulubun haftalik TV payi (EUR, 1.000'e yuvarlanir): havuz ligdeki kulupler arasinda ESIT bolunur,
    kulubun kendi itibari etkisizdir. league_reputation: lig itibar ortalamasi (1-100).
    """
    clubs = int(clubs)
    if clubs < 1:
        return 0
    return int(round(tv_pool_weekly(league_reputation, clubs) / clubs / 1000) * 1000)


def league_prize(position: int, league_size: int, league_strength: float | None,
                 league_weeks: int | None = None) -> int:
    """
    Sezon sonu lig odulu (EUR, 10.000'e yuvarlanir). position 1 = sampiyon (en buyuk), sonra azalir.
    league_strength: lig itibar ortalamasi. league_weeks: lig haftasi (None: cift devre 2 x (kulup - 1)).
    Gecersiz sira (1..league_size disi) -> 0.
    """
    size, position = int(league_size), int(position)
    if size < 1 or not 1 <= position <= size:
        return 0
    weeks = int(league_weeks) if league_weeks else max(1, 2 * (size - 1))
    season_tv = tv_money_weekly(league_strength, size) * max(1, weeks)
    if size == 1:
        share = PRIZE_CHAMPION_SHARE
    else:
        rank = (size - position) / (size - 1)            # sampiyon 1.0, sonuncu 0.0
        share = PRIZE_LAST_SHARE + (PRIZE_CHAMPION_SHARE - PRIZE_LAST_SHARE) * rank ** PRIZE_CURVE
    return int(round(season_tv * share / 10_000) * 10_000)


def cup_round_prize(stage: str, won: bool) -> int:
    """
    Devler Arenasi turu primi (EUR): eslesmesi (ya da grubu) biten her takima bir kez odenir.
    stage: "GROUP" / "R16" / "QF" / "SF" / "FINAL" (cup_draw.Stage degeri); won: turu gecti / kupayi kaldirdi.
    Bilinmeyen asama -> 0.
    """
    key = getattr(stage, "value", stage)
    prizes = CUP_ROUND_PRIZES.get(str(key))
    if prizes is None:
        return 0
    return prizes[0] if won else prizes[1]


def club_net_worth(transfer_budget: int, squad_value: int) -> int:
    """Kulubun net degeri: transfer kasasi + kadro piyasa degeri (akademi dahil)."""
    return int(transfer_budget) + int(squad_value)


def chairman_top_up(net_worth: int, league_average_net_worth: float,
                    floor_share: float = CHAIRMAN_FLOOR_SHARE) -> int:
    """
    Baskan guvencesi (sezon basi): net deger lig ortalamasinin floor_share payinin altindaysa aradaki fark kasaya
    konur (CHAIRMAN_ROUNDING'e yukari yuvarlanir). Taban ustundeyse 0.
    """
    floor = float(league_average_net_worth) * floor_share
    gap = floor - int(net_worth)
    if gap <= 0:
        return 0
    return int(-(-gap // CHAIRMAN_ROUNDING) * CHAIRMAN_ROUNDING)
