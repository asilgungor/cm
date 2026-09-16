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


def market_value(overall: int, age: int, position: Position) -> int:
    """Oyuncunun piyasa degeri (EUR). 10.000'e yuvarlanir."""
    base = VALUE_BASE_AMOUNT * VALUE_GROWTH ** (overall - VALUE_BASE_OVERALL)
    raw = base * age_value_factor(age) * POSITION_VALUE_FACTOR[position]
    return int(round(raw / 10_000) * 10_000)


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
