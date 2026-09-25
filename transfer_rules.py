"""
transfer_rules.py
=================
Transfer masasi kurallari (13H, Football Manager tarzi yapay zeka kulubu transferleri). SAF modul: veritabani, ORM ve
Streamlit bilmez. Rastgelelik gereken yerde tohumlu random.Random DISARIDAN verilir (transfer_desk crc32 ile turetir);
cm.rng ASLA kullanilmaz. Orkestrasyon ve para hareketi transfer_desk.py'dedir.

    TransferWindow / transfer_window    yaz (sezon basi + sezon arasi) ve kis (sezon ortasi) donemleri; sezon
                                        uzunluguna gore olceklenir (7 haftalik sentetik sezon: yaz 1-2, kis 4)
    AddOn / DealTerms / validate_terms  yapilandirilmis teklif: toplam garantili bedel = pesin + taksit (12/24/36 ay,
                                        ceyreklik); ek odemeler (mac, gol, lig / kupa sampiyonlugu); sonraki satistan
                                        pay (%); takas oyuncusu
    instalment_plan                     taksit plani: tutarlar toplami ertelenen bedele BIREBIR esit, vade haftalari
    hidden_trait                        gizli kisilik (hirs, sakatlik egilimi): FM verisi varsa o, yoksa oyuncu kimliginden
                                        crc32 (kalici, RNG cekmez)
    player_interest                     oyuncunun transfere istekliligi (itibar, lig duzeyi, hirs, rol, mutsuzluk,
                                        sozlesme); prestij kapisi transfers.check_interest ile ayni
    club_stance / rivalry               satici kulubun tutumu: satilik degil / pazarliga acik / listede / fazlalik /
                                        satisa kapali (kadro tabani); hedef ve (gizli) taban bedel, sabir, pesinat
    ValuationContext / package_value    paketin satici gozundeki bugunku degeri (taksit iskontosu, ek odeme olasiligi,
                                        sonraki satis payi, takas oyuncusu, geri alim maddesi)
    seller_response / buyer_response    AI satici / alici karari: kabul / karsi teklif (ne degismeli) / ret / gorusmeyi kes
    ai_bid_terms                        AI kulubunun insan kulubunun oyuncusuna yaptigi yapilandirilmis teklif
    medical_check                       saglik kontrolu: GECTI / RISKLI (menajer karar verir) / KALDI
    scouting                            bilgi yuzdesi (0-100), haftalik gozlem kazanci, bilgiyle olceklenen sis payi

15F CANLI PAZAR (bolum 12, bayrak LIVE_MARKET; kapaliyken bu bolumdeki hicbir sey cagrilmaz):
    window_span / window_index          acik donemin ilk-son haftasi, kacinci haftasindayiz, son gun mu
    window_deal_target / weekly_quota   donem basina dunya capinda hedef AI<->AI transfer sayisi ve haftalik kota
                                        (son hafta DEADLINE_BOOST kat: "son gun")
    market_buyer_weight / weighted_pick maas butcesi buyuk kulup daha cok harcar (agirlikli tohumlu secim)
    market_score / market_wealth        alicinin hedefi ne kadar istedigi: ihtiyac + DERINLIK + yildiz istahi
    elite_wage_floor                    ELIT esigi: dunyanin ortanca maas butcesi x MARKET_ELITE_MEDIANS
    market_squad_gate                   kadro tabani / tavani: 20'nin altina dusuren satis, 30'un ustune cikaran alis yok
    market_spend_cap / market_window_allowance
                                        kulubun bir transferde ve bir DONEMDE harcayabilecegi en yuksek tutar
                                        (kasa asla eksiye dusmez; yazin ocak icin pay ayrilir)
    sell_on_amount                      sonraki satis payi: BRUT satistan ya da KAR uzerinden (sell_on_profit)
    buy_back_open / buy_back_attractive geri alim maddesi hala gecerli mi ve AI satici onu tetikler mi

K12 (menajer her seyi bilmez): bu modulun dondurdugu gizli sayilar (hedef, taban, sabir, ikna) arayuze SAYI olarak
cikmaz; transfer_desk yalnizca etiket ve sisli aralik gosterir.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from transfers import ROLE_RANK, check_interest

# ===========================================================================
# 1) TRANSFER DONEMLERI
# ===========================================================================

SUMMER_SHARE = 0.12            # yaz donemi: sezonun ilk %12'si (en az SUMMER_MIN_WEEKS)
SUMMER_MIN_WEEKS = 2
WINTER_SHARE = 0.10            # kis donemi: sezon ortasindan itibaren sezonun %10'u (en az 1 hafta)
WINTER_MIN_WEEKS = 1

WINDOW_SUMMER, WINDOW_WINTER, WINDOW_OFF_SEASON = "SUMMER", "WINTER", "OFF_SEASON"
WINDOW_LABELS = {WINDOW_SUMMER: "Yaz transfer dönemi", WINDOW_WINTER: "Kış transfer dönemi",
                 WINDOW_OFF_SEASON: "Sezon arası (yaz dönemi)"}


@dataclass(frozen=True)
class TransferWindow:
    open: bool
    name: str | None                 # SUMMER / WINTER / OFF_SEASON / None (kapali)
    label: str                       # Turkce durum cumlesi
    closes_after_week: int | None    # acikken: bu sezonun son acik haftasi (sezon arasinda None)
    next_open_week: int | None       # kapaliyken: bu sezon yeniden acildigi hafta (None: yeni sezonda)
    summer: tuple[int, int]
    winter: tuple[int, int] | None


def window_weeks(season_weeks: int) -> tuple[tuple[int, int], tuple[int, int] | None]:
    """(yaz ilk-son hafta, kis ilk-son hafta ya da None). Kisa sezonda kis yaz ile cakisirsa kis olmaz."""
    s = max(1, int(season_weeks))
    summer_end = min(s, max(SUMMER_MIN_WEEKS, round(s * SUMMER_SHARE)))
    winter_start = max(s // 2 + 1, summer_end + 1)
    if winter_start > s:
        return (1, summer_end), None
    winter_end = min(s, winter_start + max(WINTER_MIN_WEEKS, round(s * WINTER_SHARE)) - 1)
    return (1, summer_end), (winter_start, winter_end)


def transfer_window(week: int, season_weeks: int, season_finished: bool = False) -> TransferWindow:
    """
    week haftasi (oynanacak hafta) icin transfer donemi. Sezon bittiyse (yeni sezon kurulana kadar) sezon arasi = yaz
    donemi acik. Donem disinda anlasma yapilabilir; tamamlanma donem acilinca olur (transfer_desk).
    """
    summer, winter = window_weeks(season_weeks)
    if season_finished:
        return TransferWindow(True, WINDOW_OFF_SEASON, "Sezon arası: yaz transfer dönemi açık.", None, None,
                              summer, winter)
    w = int(week)
    if summer[0] <= w <= summer[1]:
        return TransferWindow(True, WINDOW_SUMMER, f"Yaz transfer dönemi açık ({summer[1]}. haftanın sonunda "
                                                   f"kapanır).", summer[1], None, summer, winter)
    if winter is not None and winter[0] <= w <= winter[1]:
        return TransferWindow(True, WINDOW_WINTER, f"Kış transfer dönemi açık ({winter[1]}. haftanın sonunda "
                                                   f"kapanır).", winter[1], None, summer, winter)
    if winter is not None and w < winter[0]:
        return TransferWindow(False, None, f"Transfer dönemi kapalı; kış dönemi {winter[0]}. haftada açılır. "
                                           f"Anlaşma yapılabilir, transfer dönem açılınca tamamlanır.",
                              None, winter[0], summer, winter)
    return TransferWindow(False, None, "Transfer dönemi kapalı; sezon bitince (yaz dönemi) açılır. Anlaşma "
                                       "yapılabilir, transfer dönem açılınca tamamlanır.", None, None, summer, winter)


# ===========================================================================
# 2) YAPILANDIRILMIS TEKLIF
# ===========================================================================

INSTALMENT_MONTHS = (0, 6, 12, 24, 36)
MONTHS_PER_INSTALMENT = 3          # ceyreklik taksit: 12 ay -> 4 taksit
MAX_SELL_ON_PCT = 50
MAX_ADD_ONS = 4
MAX_FEE = 2_000_000_000
MAX_BUY_BACK_SEASONS = 3           # 15F: geri alim maddesi en fazla bu kadar sezon gecerli olabilir
ADD_ON_APPEARANCES, ADD_ON_GOALS = "APPEARANCES", "GOALS"
ADD_ON_LEAGUE_TITLE, ADD_ON_CUP_TITLE = "LEAGUE_TITLE", "CUP_TITLE"
ADD_ON_KINDS = (ADD_ON_APPEARANCES, ADD_ON_GOALS, ADD_ON_LEAGUE_TITLE, ADD_ON_CUP_TITLE)
ADD_ON_LABELS = {
    ADD_ON_APPEARANCES: "{n} resmi maçtan sonra",
    ADD_ON_GOALS: "{n} golden sonra",
    ADD_ON_LEAGUE_TITLE: "lig şampiyonluğunda",
    ADD_ON_CUP_TITLE: "Devler Arenası şampiyonluğunda",
}
COUNTED_ADD_ONS = (ADD_ON_APPEARANCES, ADD_ON_GOALS)


def money(amount: float) -> str:
    """finance.format_money ile ayni kisa bicim (finance models import ettigi icin burada tekrar)."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.1f}M EUR"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.0f}K EUR"
    return f"{sign}{a:.0f} EUR"


@dataclass(frozen=True)
class AddOn:
    kind: str
    threshold: int = 1                 # mac / gol sayisi (sampiyonlukta 1)
    amount: int = 0

    @property
    def key(self) -> str:
        """Anlasma icinde ek odemenin kalici anahtari (odeme satirinin ref'i bundan uretilir)."""
        return f"{self.kind}:{int(self.threshold)}"

    def describe(self) -> str:
        when = ADD_ON_LABELS.get(self.kind, self.kind).format(n=int(self.threshold))
        return f"{money(self.amount)} ({when})"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "threshold": int(self.threshold), "amount": int(self.amount)}

    @classmethod
    def from_dict(cls, data: Mapping) -> AddOn:
        return cls(kind=str(data.get("kind")), threshold=int(data.get("threshold") or 1),
                   amount=int(data.get("amount") or 0))


@dataclass(frozen=True)
class DealTerms:
    """
    Bonservis paketi. fee: GARANTILI toplam (pesin + taksitler); ek odemeler ve sonraki satis payi haric.
    upfront None: tamami pesin. instalment_months: ertelenen kisim bu surede ceyreklik taksitle odenir.
    15F: sell_on_profit -> pay BRUT satistan degil KARDAN (satis - bu bonservis) alinir; buy_back_fee /
    buy_back_seasons -> saticinin geri alim maddesi (sabit bedel, N sezon gecerli).
    """
    fee: int
    upfront: int | None = None
    instalment_months: int = 0
    add_ons: tuple[AddOn, ...] = ()
    sell_on_pct: int = 0
    exchange_player_id: int | None = None
    sell_on_profit: bool = False               # 15F: kara dayali sonraki satis payi
    buy_back_fee: int | None = None            # 15F: geri alim bedeli (satici kulup icin)
    buy_back_seasons: int = 0                  # 15F: maddenin gecerli oldugu sezon sayisi

    @property
    def upfront_amount(self) -> int:
        return int(self.fee) if self.upfront is None else int(self.upfront)

    @property
    def deferred(self) -> int:
        return max(0, int(self.fee) - self.upfront_amount)

    @property
    def instalments(self) -> int:
        return instalment_count(self.instalment_months) if self.deferred > 0 else 0

    @property
    def upfront_share(self) -> float:
        return self.upfront_amount / self.fee if self.fee > 0 else 1.0

    @property
    def add_ons_total(self) -> int:
        return sum(int(a.amount) for a in self.add_ons)

    def describe(self) -> str:
        parts = [money(self.fee)]
        if self.deferred > 0:
            parts.append(f"{money(self.upfront_amount)} peşin + {money(self.deferred)} {self.instalment_months} ayda "
                         f"{self.instalments} taksit")
        for addon in self.add_ons:
            parts.append(f"+ {addon.describe()}")
        if self.sell_on_pct:
            parts.append(f"sonraki satış {'kârından' if self.sell_on_profit else 'bedelinden'} "
                         f"%{self.sell_on_pct}")
        if self.buy_back_fee:
            parts.append(f"geri alım {money(self.buy_back_fee)} ({self.buy_back_seasons} sezon)")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return {"fee": int(self.fee), "upfront": self.upfront_amount, "instalment_months": int(self.instalment_months),
                "add_ons": [a.to_dict() for a in self.add_ons], "sell_on_pct": int(self.sell_on_pct),
                "exchange_player_id": self.exchange_player_id, "sell_on_profit": bool(self.sell_on_profit),
                "buy_back_fee": self.buy_back_fee, "buy_back_seasons": int(self.buy_back_seasons)}

    @classmethod
    def from_dict(cls, data: Mapping) -> DealTerms:
        back = data.get("buy_back_fee")
        return cls(fee=int(data.get("fee") or 0), upfront=int(data.get("upfront") if data.get("upfront") is not None
                                                             else data.get("fee") or 0),
                   instalment_months=int(data.get("instalment_months") or 0),
                   add_ons=tuple(AddOn.from_dict(a) for a in data.get("add_ons") or ()),
                   sell_on_pct=int(data.get("sell_on_pct") or 0),
                   exchange_player_id=data.get("exchange_player_id"),
                   sell_on_profit=bool(data.get("sell_on_profit")),
                   buy_back_fee=int(back) if back not in (None, "") else None,
                   buy_back_seasons=int(data.get("buy_back_seasons") or 0))


def instalment_count(months: int) -> int:
    return max(0, int(months)) // MONTHS_PER_INSTALMENT


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def normalize_terms(terms: DealTerms) -> DealTerms:
    """
    Pesin None -> tamami; ertelenen yoksa taksit suresi 0; ek odemeler anahtar sirasiyla.
    15F: pay yoksa kar tabani duser; geri alim bedeli yoksa sure 0 (ve tersi).
    """
    upfront = terms.upfront_amount
    months = int(terms.instalment_months) if terms.fee - upfront > 0 else 0
    add_ons = tuple(sorted(terms.add_ons, key=lambda a: (ADD_ON_KINDS.index(a.kind) if a.kind in ADD_ON_KINDS
                                                         else 99, a.threshold)))
    profit = bool(terms.sell_on_profit) and int(terms.sell_on_pct) > 0
    back = int(terms.buy_back_fee) if terms.buy_back_fee else None
    seasons = max(0, int(terms.buy_back_seasons)) if back else 0
    if back and seasons <= 0:
        seasons = 1
    return replace(terms, upfront=upfront, instalment_months=months, add_ons=add_ons, sell_on_profit=profit,
                   buy_back_fee=back, buy_back_seasons=seasons)


def validate_terms(terms: DealTerms, *, allow_exchange: bool = True) -> list[str]:
    """Teklif paketindeki kural ihlalleri (Turkce; bos liste = gecerli)."""
    problems: list[str] = []
    if not _is_int(terms.fee) or terms.fee < 0:
        return ["Bonservis tutarı sıfır ya da pozitif bir tam sayı olmalı."]
    if terms.fee > MAX_FEE:
        problems.append("Bonservis tutarı çok yüksek.")
    upfront = terms.upfront
    deferred = 0
    if upfront is not None and (not _is_int(upfront) or upfront < 0 or upfront > terms.fee):
        problems.append("Peşinat 0 ile toplam bonservis arasında olmalı.")
    elif upfront is not None:
        deferred = terms.fee - upfront
    if terms.instalment_months not in INSTALMENT_MONTHS:
        problems.append("Taksit süresi 0, 6, 12, 24 ya da 36 ay olmalı.")
    elif deferred > 0 and terms.instalment_months == 0:
        problems.append("Peşin ödenmeyen kısım için taksit süresi seç (6, 12, 24 ya da 36 ay).")
    if not _is_int(terms.sell_on_pct) or not 0 <= terms.sell_on_pct <= MAX_SELL_ON_PCT:
        problems.append(f"Sonraki satıştan pay %0-{MAX_SELL_ON_PCT} arasında olmalı.")
    if len(terms.add_ons) > MAX_ADD_ONS:
        problems.append(f"En fazla {MAX_ADD_ONS} ek ödeme maddesi eklenebilir.")
    seen: set[str] = set()
    for addon in terms.add_ons:
        if addon.kind not in ADD_ON_KINDS:
            problems.append("Geçersiz ek ödeme türü.")
            continue
        if not _is_int(addon.amount) or addon.amount <= 0 or addon.amount > MAX_FEE:
            problems.append("Ek ödeme tutarı pozitif olmalı.")
        if addon.kind in COUNTED_ADD_ONS and (not _is_int(addon.threshold) or not 1 <= addon.threshold <= 300):
            problems.append("Maç / gol eşiği 1-300 arasında olmalı.")
        if addon.key in seen:
            problems.append("Aynı ek ödeme maddesi iki kez eklenemez.")
        seen.add(addon.key)
    if terms.exchange_player_id is not None and not allow_exchange:
        problems.append("Bu teklifte takas oyuncusu olamaz.")
    if terms.sell_on_profit and terms.sell_on_pct <= 0:
        problems.append("Kâra dayalı pay için önce sonraki satış payı yüzdesi seç.")
    if terms.buy_back_fee is not None:
        if not _is_int(terms.buy_back_fee) or terms.buy_back_fee <= 0 or terms.buy_back_fee > MAX_FEE:
            problems.append("Geri alım bedeli pozitif olmalı.")
        elif terms.buy_back_fee < terms.fee:
            problems.append("Geri alım bedeli bonservisten düşük olamaz.")
        if not _is_int(terms.buy_back_seasons) or not 1 <= terms.buy_back_seasons <= MAX_BUY_BACK_SEASONS:
            problems.append(f"Geri alım maddesi 1-{MAX_BUY_BACK_SEASONS} sezon geçerli olabilir.")
    elif terms.buy_back_seasons:
        problems.append("Geri alım süresi için önce geri alım bedeli gir.")
    return problems


def instalment_interval(season_weeks: int) -> int:
    """Ceyreklik taksit araligi (hafta): sezonun dortte biri, en az 1."""
    return max(1, round(max(1, int(season_weeks)) / 4))


def instalment_plan(deferred: int, months: int, season_weeks: int, start_career_week: int) -> list[tuple[int, int, int]]:
    """
    [(sira 1.., vade kariyer haftasi, tutar)]. Tutarlar esit (asagi yuvarlanir); kalan kurus SON taksite eklenir:
    toplam ertelenen bedele BIREBIR esittir. 12 ay = 4 taksit, bir sezonluk vade (sezon uzunluguna gore olceklenir).
    """
    deferred, count = int(deferred), instalment_count(months)
    if deferred <= 0 or count <= 0:
        return []
    interval = instalment_interval(season_weeks)
    base = deferred // count
    plan = [(i + 1, int(start_career_week) + interval * (i + 1), base) for i in range(count)]
    seq, due, amount = plan[-1]
    plan[-1] = (seq, due, amount + deferred - base * count)
    return plan


# ===========================================================================
# 3) GIZLI KISILIK
# ===========================================================================

def hidden_trait(kind: str, player_id: int, fm_value=None) -> int:
    """Gizli 1-20 ozellik: FM verisi (1-20) varsa o, yoksa oyuncu kimliginden kalici crc32 (RNG cekmez)."""
    try:
        value = int(fm_value)
    except (TypeError, ValueError):
        value = 0
    if 1 <= value <= 20:
        return value
    return zlib.crc32(f"{kind}|{int(player_id)}".encode()) % 20 + 1


# ===========================================================================
# 4) OYUNCUNUN ISTEKLILIGI
# ===========================================================================

INTEREST_KEEN, INTEREST_INTERESTED, INTEREST_UNDECIDED = "KEEN", "INTERESTED", "UNDECIDED"
INTEREST_RELUCTANT, INTEREST_REFUSES = "RELUCTANT", "REFUSES"
INTEREST_LABELS = {
    INTEREST_KEEN: "Çok istekli", INTEREST_INTERESTED: "İstekli", INTEREST_UNDECIDED: "Kararsız",
    INTEREST_RELUCTANT: "İsteksiz", INTEREST_REFUSES: "Gelmek istemiyor",
}
# (esik, seviye, maas carpani): skor >= esik
INTEREST_BANDS: tuple[tuple[float, str, float], ...] = (
    (15.0, INTEREST_KEEN, 0.94),
    (4.0, INTEREST_INTERESTED, 0.98),
    (-8.0, INTEREST_UNDECIDED, 1.00),
    (-30.0, INTEREST_RELUCTANT, 1.08),
)
INTEREST_REFUSAL = "Bu transfer kariyerim için doğru adım değil."


@dataclass(frozen=True)
class PlayerInterest:
    level: str
    label: str
    score: float
    wage_multiplier: float
    reason: str | None               # gelmek istemiyorsa oyuncunun agzindan neden

    @property
    def refuses(self) -> bool:
        return self.level == INTEREST_REFUSES


def player_interest(*, overall: int, ambition: int, current_rep: int, buyer_rep: int, manager_rep: float,
                    current_league_rep: float, buyer_league_rep: float, current_role, offered_role,
                    concern_level: int = 0, contract_years: int = 3, listed: bool = False,
                    wants_away: bool = False) -> PlayerInterest:
    """
    Oyuncu bu kulube gelmek ister mi? Once prestij kapisi (transfers.check_interest: kulup + menajer beklentisi);
    sonra skor:
        itibar farki x (0.5 + hirs/20)  +  lig duzeyi farki x 0.4  +  rol farki x 8
        + mutsuzluk (kaygi seviyesi x 7)  +  listede 12  +  soz tutulmadi / ayrilmak istiyor 15  +  sozlesmesi bitiyor 6
    Seviye maas talebini olcekler (cok istekli %6 az, isteksiz %8 fazla ister); skor -30 altinda gelmez.
    """
    gate = check_interest(int(overall), int(buyer_rep), float(manager_rep))
    if not gate.interested:
        return PlayerInterest(INTEREST_REFUSES, INTEREST_LABELS[INTEREST_REFUSES], -100.0, 1.0, gate.reason)
    score = (int(buyer_rep) - int(current_rep)) * (0.5 + int(ambition) / 20.0)
    score += (float(buyer_league_rep) - float(current_league_rep)) * 0.4
    score += (ROLE_RANK.get(offered_role, 1) - ROLE_RANK.get(current_role, 1)) * 8.0
    score += max(0, min(3, int(concern_level or 0))) * 7.0
    score += 12.0 if listed else 0.0
    score += 15.0 if wants_away else 0.0
    score += 6.0 if int(contract_years) <= 1 else 0.0
    for threshold, level, multiplier in INTEREST_BANDS:
        if score >= threshold:
            return PlayerInterest(level, INTEREST_LABELS[level], round(score, 1), multiplier, None)
    return PlayerInterest(INTEREST_REFUSES, INTEREST_LABELS[INTEREST_REFUSES], round(score, 1), 1.0,
                          INTEREST_REFUSAL)


# ===========================================================================
# 5) SATICI KULUBUN TUTUMU
# ===========================================================================

STANCE_NOT_FOR_SALE, STANCE_NEGOTIABLE, STANCE_LISTED = "NOT_FOR_SALE", "NEGOTIABLE", "LISTED"
STANCE_SURPLUS, STANCE_UNAVAILABLE = "SURPLUS", "UNAVAILABLE"
STANCE_LABELS = {
    STANCE_NOT_FOR_SALE: "Satılık değil", STANCE_NEGOTIABLE: "Pazarlığa açık", STANCE_LISTED: "Transfer listesinde",
    STANCE_SURPLUS: "Kadro fazlası", STANCE_UNAVAILABLE: "Satışa kapalı",
}
STANCE_PRICE = {STANCE_NOT_FOR_SALE: 1.6, STANCE_NEGOTIABLE: 1.0, STANCE_LISTED: 0.85, STANCE_SURPLUS: 0.78}
STANCE_PATIENCE = {STANCE_NOT_FOR_SALE: 2, STANCE_NEGOTIABLE: 4, STANCE_LISTED: 5, STANCE_SURPLUS: 5}
KEY_PLAYER_IMPORTANCE = 0.8        # bu onem ve >= 2 yil sozlesme: satilik degil
SURPLUS_IMPORTANCE = 0.3
RIVALRY_PRICE = {0: 1.0, 1: 1.08, 2: 1.2}      # ayni lig / derbi primi
RIVALRY_PATIENCE = {0: 0, 1: 0, 2: 1}
POOR_MIN_UPFRONT, RICH_MIN_UPFRONT = 0.6, 0.35
POOR_FLOOR, RICH_FLOOR = 0.9, 0.94             # taban = hedef x bu (kasasi zayif kulup daha esnek)
NOISE_PCT = 0.03                               # anlasmaya ozgu +-%3 sapma (tohumlu, kalici)
SELL_ON_YOUNG_AGE, SELL_ON_WANTED_AGE = 21, 23
SELL_ON_POTENTIAL_GAP = 6


@dataclass(frozen=True)
class ClubStance:
    kind: str
    label: str
    target: int                 # GIZLI: kulubun hedef bedeli (paket degeri)
    minimum: int                # GIZLI: altina inmeyecegi paket degeri
    patience: int               # GIZLI: karsi teklif / kotu teklif hakki
    min_upfront_share: float
    wants_sell_on: int          # karsi teklifte isteyecegi sonraki satis payi (%)
    reason: str | None = None   # satisa kapaliysa neden


def rivalry(seller_name: str, buyer_name: str, same_league: bool) -> int:
    """0 yok, 1 ayni lig rakibi, 2 derbi (ayni sehir: kulup adinin ilk kelimesi ayni)."""
    first = lambda name: (str(name or "").split() or [""])[0].casefold()   # noqa: E731
    if first(seller_name) and first(seller_name) == first(buyer_name):
        return 2
    return 1 if same_league else 0


def deal_noise(deal_id: int, salt: str = "stance") -> float:
    """Anlasmaya ozgu kalici sapma (-NOISE_PCT..+NOISE_PCT); crc32, RNG cekmez."""
    return (zlib.crc32(f"{salt}|{int(deal_id)}".encode()) % 2001 - 1000) / 1000.0 * NOISE_PCT


def _round_to(amount: float, step: int = 10_000) -> int:
    return int(round(amount / step) * step)


def club_stance(*, asking: int, importance: float, contract_years: int, listed: bool, concern_level: int,
                seller_budget: int, rivalry_level: int, age: int, overall: int, potential: int | None,
                noise: float = 0.0, floor_reason: str | None = None) -> ClubStance:
    """
    Satici AI kulubunun oyuncu icin tutumu. asking: transfers.asking_price (deger x kadro onemi x itibar x sozlesme).
        kadro tabani ihlali           -> satisa kapali (hicbir teklif kabul edilmez)
        listede ya da ayrilmak istiyor -> x0.85, sabir 5
        onem < 0.3                    -> kadro fazlasi x0.78
        onem >= 0.8 ve >= 2 yil       -> satilik degil x1.6 ("reddedemeyecegimiz teklif"), sabir 2
        diger                         -> pazarliga acik x1.0, sabir 4
    Rakip kulube (ayni lig x1.08, derbi x1.2 ve sabir -1) pahali satar. Kasasi bedelin altindaki kulup daha cok pesinat
    ister ama tabani daha esnektir.
    """
    young = potential is not None and int(age) <= SELL_ON_WANTED_AGE and int(potential) - int(overall) >= \
        SELL_ON_POTENTIAL_GAP
    wants = (20 if int(age) <= SELL_ON_YOUNG_AGE else 15) if young else 0
    if floor_reason:
        return ClubStance(STANCE_UNAVAILABLE, STANCE_LABELS[STANCE_UNAVAILABLE], 0, 0, 1, 1.0, 0, floor_reason)
    if listed or int(concern_level or 0) >= 3:
        kind = STANCE_LISTED
    elif importance < SURPLUS_IMPORTANCE:
        kind = STANCE_SURPLUS
    elif importance >= KEY_PLAYER_IMPORTANCE and int(contract_years) >= 2:
        kind = STANCE_NOT_FOR_SALE
    else:
        kind = STANCE_NEGOTIABLE
    level = max(0, min(2, int(rivalry_level)))
    target = _round_to(max(0, asking) * STANCE_PRICE[kind] * RIVALRY_PRICE[level] * (1.0 + noise))
    poor = int(seller_budget) < int(asking)
    minimum = _round_to(target * (POOR_FLOOR if poor else RICH_FLOOR))
    patience = max(1, STANCE_PATIENCE[kind] - RIVALRY_PATIENCE[level])
    return ClubStance(kind, STANCE_LABELS[kind], target, minimum, patience,
                      POOR_MIN_UPFRONT if poor else RICH_MIN_UPFRONT, wants)


# ===========================================================================
# 6) PAKET DEGERI
# ===========================================================================

INSTALMENT_DISCOUNT_PER_YEAR = 0.06     # 12 ay taksit ortalama 6 ay gecikme -> %3 iskonto
ADD_ON_TIME_DISCOUNT = 0.9
SELL_ON_RESALE_SHARE = {True: 0.45, False: 0.25}     # genc yetenek mi -> sonraki satis beklentisi
SELL_ON_PROFIT_SHARE = 0.5              # 15F: kara dayali pay brut paya gore bu kadar deger tasir
BUY_BACK_VALUE_SHARE = 0.12             # 15F: geri alim maddesi saticiya oyuncu degerinin bu kadari kadar deger katar
ROLE_APPS_SHARE = {"STAR": 0.85, "FIRST_TEAM": 0.65, "BACKUP": 0.3}
GOALS_PER_APP = {"FWD": 0.45, "MID": 0.15, "DEF": 0.04, "GK": 0.0}
ADD_ON_HORIZON_SEASONS = 3


@dataclass(frozen=True)
class ValuationContext:
    """Paket degerlemesi icin durum (arayuz ya da ORM yok): transfer_desk kurar."""
    league_weeks: int = 6               # sezonluk lig maci
    cup_weeks: int = 0                  # sezonluk olasi kupa maci
    position: str = "MID"
    expected_role: str = "FIRST_TEAM"   # oyuncunun alici kulupteki beklenen rolu
    title_odds: float = 0.1             # alicinin sezonluk lig sampiyonlugu olasiligi
    cup_odds: float = 0.03
    resale_value: int = 0               # sonraki satis payi icin taban (oyuncunun degeri)
    young: bool = False
    exchange_value: int = 0             # takas oyuncusunun KARSI kulup icin degeri (istenmiyorsa 0)
    buyer_risk: float = 1.0             # alicinin odeme guvenilirligi (borclu kulup < 1)


def addon_probability(addon: AddOn, ctx: ValuationContext) -> float:
    """Ek odemenin (ADD_ON_HORIZON_SEASONS sezonda) tetiklenme olasiligi."""
    apps = (ctx.league_weeks + ctx.cup_weeks * 0.5) * ROLE_APPS_SHARE.get(ctx.expected_role, 0.65) \
        * ADD_ON_HORIZON_SEASONS
    if addon.kind == ADD_ON_APPEARANCES:
        return max(0.02, min(0.9, apps / max(1, addon.threshold) * 0.8))
    if addon.kind == ADD_ON_GOALS:
        goals = apps * GOALS_PER_APP.get(ctx.position, 0.15)
        return max(0.0, min(0.85, goals / max(1, addon.threshold) * 0.8))
    if addon.kind == ADD_ON_LEAGUE_TITLE:
        return max(0.0, min(0.9, 1 - (1 - ctx.title_odds) ** ADD_ON_HORIZON_SEASONS))
    if addon.kind == ADD_ON_CUP_TITLE:
        return max(0.0, min(0.6, 1 - (1 - ctx.cup_odds) ** ADD_ON_HORIZON_SEASONS))
    return 0.0


def deferred_factor(months: int, buyer_risk: float = 1.0) -> float:
    """Ertelenen bedelin bugunku degeri: ortalama gecikme (ay/2) x yillik iskonto x odeme riski."""
    return max(0.0, (1.0 - INSTALMENT_DISCOUNT_PER_YEAR * int(months) / 24.0) * float(buyer_risk))


def package_value(terms: DealTerms, ctx: ValuationContext) -> int:
    """
    Paketin satici gozundeki bugunku degeri (EUR). Alici AI icin de maliyetin karsiligi olarak kullanilir.
    15F: kara dayali pay brut payin SELL_ON_PROFIT_SHARE kati kadar deger tasir; geri alim maddesi saticiya
    BUY_BACK_VALUE_SHARE kadar prim ekler (alici icin ayni tutarda maliyet: degerleme simetriktir).
    """
    value = terms.upfront_amount + terms.deferred * deferred_factor(terms.instalment_months, ctx.buyer_risk)
    value += sum(a.amount * addon_probability(a, ctx) * ADD_ON_TIME_DISCOUNT for a in terms.add_ons)
    sell_on = ctx.resale_value * terms.sell_on_pct / 100.0 * SELL_ON_RESALE_SHARE[bool(ctx.young)]
    value += sell_on * (SELL_ON_PROFIT_SHARE if terms.sell_on_profit else 1.0)
    if terms.buy_back_fee:
        value += ctx.resale_value * BUY_BACK_VALUE_SHARE * min(MAX_BUY_BACK_SEASONS,
                                                               max(1, int(terms.buy_back_seasons))) / \
            MAX_BUY_BACK_SEASONS
    if terms.exchange_player_id is not None:
        value += ctx.exchange_value
    return int(round(value))


def _structure_rate(terms: DealTerms, ctx: ValuationContext) -> float:
    """Garantili bedelin 1 EUR'unun paket degerine katkisi (pesin 1, taksitli kisim iskontolu)."""
    share = terms.upfront_share if terms.fee > 0 else 1.0
    return share + (1.0 - share) * deferred_factor(terms.instalment_months, ctx.buyer_risk)


def fee_for_value(terms: DealTerms, target_value: int, ctx: ValuationContext, step: int = 50_000) -> int:
    """Paket degeri target_value olacak garantili bedel (yapi ayni; yukari yuvarlanir)."""
    other = package_value(replace(terms, fee=0, upfront=0), ctx)
    rate = max(0.1, _structure_rate(terms, ctx))
    raw = max(0.0, (int(target_value) - other) / rate)
    return int(math.ceil(raw / step) * step)


def rescale(terms: DealTerms, fee: int) -> DealTerms:
    """Yapiyi (pesin orani, taksit suresi) koruyarak garantili bedeli degistirir."""
    fee = max(0, int(fee))
    if terms.deferred <= 0 or terms.fee <= 0:
        return replace(terms, fee=fee, upfront=fee, instalment_months=0 if terms.deferred <= 0 else
                       terms.instalment_months)
    upfront = _round_to(fee * terms.upfront_share, 10_000)
    return replace(terms, fee=fee, upfront=min(fee, upfront))


# ===========================================================================
# 7) AI SATICI VE ALICI KARARLARI
# ===========================================================================

ACTION_ACCEPT, ACTION_COUNTER, ACTION_REJECT, ACTION_END = "ACCEPT", "COUNTER", "REJECT", "END"
INSULT_RATIO = 0.6                 # taban degerin bu kadarinin altindaki teklif ciddiye alinmaz
CONCESSION_PER_ROUND = 0.25        # kulup her turda hedefinden tabana dogru bu kadar iner
MAX_CONCESSION = 0.8
BUYER_WALK_RATIO = 1.3             # AI alici: istenen, ust sinirinin bu katini asarsa teklifini ceker


@dataclass(frozen=True)
class ClubResponse:
    action: str                             # ACCEPT / COUNTER / REJECT / END
    message: str
    counter: DealTerms | None = None        # COUNTER: kulubun onerdigi paket
    demands: tuple[str, ...] = ()           # ne degismeli (Turkce)
    patience_cost: int = 0


GENEROUS_RATIO = 1.15              # paket hedefin bu katini asiyorsa yapi istekleri (pesinat, pay) aranmaz


def _counter_structure(terms: DealTerms, stance: ClubStance,
                       ctx: ValuationContext) -> tuple[DealTerms, list[str], bool]:
    """(kulubun istedigi yapi, istekler, zorunlu istek var mi). Istenmeyen takas oyuncusu her zaman zorunludur."""
    demands: list[str] = []
    counter = terms
    must = False
    if terms.exchange_player_id is not None and ctx.exchange_value <= 0:
        counter = replace(counter, exchange_player_id=None)
        demands.append("Takas oyuncusunu istemiyoruz; nakit bekliyoruz.")
        must = True
    if counter.fee > 0 and counter.upfront_share + 1e-9 < stance.min_upfront_share:
        upfront = _round_to(counter.fee * stance.min_upfront_share, 10_000)
        counter = replace(counter, upfront=min(counter.fee, max(upfront, counter.upfront_amount)))
        demands.append(f"Bedelin en az %{round(stance.min_upfront_share * 100)}'i peşin ödenmeli.")
    if stance.wants_sell_on and counter.sell_on_pct < stance.wants_sell_on:
        counter = replace(counter, sell_on_pct=stance.wants_sell_on)
        demands.append(f"Sonraki satıştan %{stance.wants_sell_on} pay istiyoruz.")
    return counter, demands, must


def seller_response(rng, terms: DealTerms, stance: ClubStance, ctx: ValuationContext, patience: int,
                    round_no: int) -> ClubResponse:
    """
    AI satici kulubun bonservis teklifine yaniti. rng: anlasma + tura ozgu tohumlu Random (yalnizca kabul bolgesinde
    tek cekim). Paket degeri hedefe ulasirsa kabul; taban ile hedef arasinda olasilikla kabul ya da karsi teklif;
    tabanin %60'inin altinda ret (sabir -2); diger durumda karsi teklif (sabir -1). Sabir biterse gorusmeler kesilir.
    Yapi istekleri (pesinat orani, sonraki satis payi) paket hedefi GENEROUS_RATIO kat astiginda aranmaz; istenmeyen
    takas oyuncusu her zaman karsi teklif sebebidir.
    """
    if stance.kind == STANCE_UNAVAILABLE:
        return ClubResponse(ACTION_END, stance.reason or "Kulüp oyuncuyu satmayı düşünmüyor.", patience_cost=patience)
    structured, demands, must = _counter_structure(terms, stance, ctx)
    pv = package_value(terms, ctx)
    structure_ok = not must and (not demands or pv >= stance.target * GENEROUS_RATIO)
    if structure_ok and pv >= stance.target:
        return ClubResponse(ACTION_ACCEPT, "Kulüp teklifinizi kabul etti.")
    if structure_ok and pv >= stance.minimum and stance.target > stance.minimum:
        chance = 0.35 + 0.65 * (pv - stance.minimum) / (stance.target - stance.minimum)
        if rng.random() < chance:
            return ClubResponse(ACTION_ACCEPT, "Kulüp uzun bir değerlendirmenin ardından teklifinizi kabul etti.")
    insulting = pv < stance.minimum * INSULT_RATIO
    cost = 2 if insulting else 1
    if patience - cost <= 0:
        return ClubResponse(ACTION_END, "Kulüp görüşmeleri kesti: tekliflerinizi ciddi bulmuyor.",
                            patience_cost=cost)
    if insulting:
        return ClubResponse(ACTION_REJECT, "Teklif ciddiye alınmayacak kadar düşük; kulüp yanıt bile vermedi.",
                            demands=tuple(demands), patience_cost=cost)
    concession = min(MAX_CONCESSION, CONCESSION_PER_ROUND * max(1, int(round_no)))
    ask = max(stance.minimum, int(stance.target - (stance.target - stance.minimum) * concession))
    fee = max(structured.fee, fee_for_value(structured, ask, ctx))
    counter = rescale(structured, fee)
    if counter.fee > terms.fee:
        demands.insert(0, f"Bonservis en az {money(counter.fee)} olmalı.")
    if stance.kind == STANCE_NOT_FOR_SALE:
        message = "Oyuncu satılık değil; ancak şu şartlarla düşünebiliriz."
    else:
        message = "Kulüp teklifi yetersiz buldu ve karşı teklif yaptı."
    return ClubResponse(ACTION_COUNTER, message, counter=counter, demands=tuple(demands), patience_cost=cost)


def buyer_response(rng, terms: DealTerms, ctx: ValuationContext, last_value: int, max_value: int, patience: int,
                   round_no: int) -> ClubResponse:
    """
    AI alici kulubun, insan satici menajerin karsi teklifine yaniti. last_value: AI'nin son teklifinin paket degeri,
    max_value: odemeye razi oldugu ust sinir (GIZLI). Istenen <= son teklif: kabul; ust sinira kadar olasilikla kabul
    ya da orta noktadan karsi teklif; ust sinirin %30 fazlasi: teklifini geri ceker.
    """
    pv = package_value(terms, ctx)
    if pv <= last_value:
        return ClubResponse(ACTION_ACCEPT, "Kulüp şartlarınızı kabul etti.")
    if pv <= max_value:
        span = max(1, max_value - last_value)
        chance = 0.3 + 0.7 * (max_value - pv) / span
        if rng.random() < chance:
            return ClubResponse(ACTION_ACCEPT, "Kulüp düşündükten sonra şartlarınızı kabul etti.")
    cost = 2 if pv > max_value * BUYER_WALK_RATIO else 1
    if cost == 2 or patience - cost <= 0:
        return ClubResponse(ACTION_END, "Kulüp talebinizi çok yüksek buldu ve teklifini geri çekti.",
                            patience_cost=cost)
    step = min(MAX_CONCESSION, 0.5 + 0.15 * max(0, int(round_no) - 1))
    offer_value = int(last_value + (min(pv, max_value) - last_value) * step)
    base = replace(terms, sell_on_pct=min(terms.sell_on_pct, 10), add_ons=terms.add_ons[:2],
                   buy_back_fee=None, buy_back_seasons=0)
    fee = fee_for_value(base, offer_value, ctx)
    counter = rescale(base, min(fee, base.fee))
    demands = [f"Bonservis için en fazla {money(counter.fee)} öneriyoruz."]
    if base.sell_on_pct != terms.sell_on_pct:
        demands.append("Sonraki satıştan en fazla %10 pay verebiliriz.")
    if terms.buy_back_fee:
        demands.append("Geri alım maddesini kabul etmiyoruz.")
    return ClubResponse(ACTION_COUNTER, "Kulüp karşı teklif yaptı.", counter=counter, demands=tuple(demands),
                        patience_cost=cost)


# ===========================================================================
# 8) AI KULUBUNUN INSAN OYUNCUSUNA TEKLIFI
# ===========================================================================

AI_BID_LOW, AI_BID_HIGH = 0.85, 1.02            # deger / istenen bedel carpani (ilk teklif)
AI_LISTED_LOW, AI_LISTED_HIGH = 0.9, 1.0
AI_MAX_OVER_BASE = (1.05, 1.25)                 # ust sinir: taban x bu aralik (ihtiyac ve tohum)
AI_ASKING_CAP = 1.6                             # istenen bedel degerin bu katini asarsa taban deger x 1.6
AI_OPEN_BIDS_PER_CLUB = 3


def ai_bid_terms(rng, *, value: int, asking: int | None, budget: int, listed: bool) -> tuple[DealTerms, int] | None:
    """
    AI kulubunun insan kulubundeki oyuncuya ilk teklifi ve odemeye razi oldugu ust sinir (paket degeri).
    Yapi: %50 tamami pesin · %30 %60 pesin + 12 ay · %20 %50 pesin + 24 ay + mac eki. Pesinat kasayi asamaz.
    """
    base = int(value)
    if asking is not None and asking > 0:
        base = min(int(asking), int(value * AI_ASKING_CAP))
    low, high = (AI_LISTED_LOW, AI_LISTED_HIGH) if listed else (AI_BID_LOW, AI_BID_HIGH)
    fee = _round_to(base * rng.uniform(low, high), 50_000)
    cap = int(base * rng.uniform(*AI_MAX_OVER_BASE))
    roll = rng.random()
    if roll < 0.5:
        terms = DealTerms(fee=fee, upfront=fee)
    elif roll < 0.8:
        terms = DealTerms(fee=fee, upfront=_round_to(fee * 0.6), instalment_months=12)
    else:
        terms = DealTerms(fee=fee, upfront=_round_to(fee * 0.5), instalment_months=24,
                          add_ons=(AddOn(ADD_ON_APPEARANCES, 20, _round_to(fee * 0.1, 10_000)),))
    if fee <= 0 or terms.upfront_amount > int(budget):
        return None
    return terms, max(cap, fee)


# ===========================================================================
# 9) SAGLIK KONTROLU
# ===========================================================================

MEDICAL_PASS, MEDICAL_RISK, MEDICAL_FAIL = "PASS", "RISK", "FAIL"
MEDICAL_LABELS = {MEDICAL_PASS: "Sağlık kontrolünden geçti", MEDICAL_RISK: "Sağlık kontrolü: riskli",
                  MEDICAL_FAIL: "Sağlık kontrolünden kaldı"}
MEDICAL_FAIL_WEEKS = 6              # bu kadar ve uzun sakatligi suren oyuncu kontrolden kalir
PRONE_RISK = 16                     # sakatlik egilimi (1-20) bu ve ustu: riskli
RECENT_INJURY_RISK = 3


@dataclass(frozen=True)
class MedicalResult:
    result: str
    label: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"result": self.result, "label": self.label, "notes": list(self.notes)}


def medical_check(*, injured_weeks_left: int, proneness: int, recent_injuries: int, age: int) -> MedicalResult:
    """
    Deterministik saglik kontrolu (RNG yok): uzun sakatlik -> KALDI; kisa sakatlik, sakatliga yatkinlik, son iki
    sezonda cok sakatlik ya da 33+ yas ve orta yatkinlik -> RISKLI (menajer devam edip etmeyecegine karar verir).
    """
    notes: list[str] = []
    weeks = max(0, int(injured_weeks_left))
    if weeks >= MEDICAL_FAIL_WEEKS:
        return MedicalResult(MEDICAL_FAIL, MEDICAL_LABELS[MEDICAL_FAIL],
                             (f"Uzun süreli sakatlık: {weeks} hafta daha oynayamaz.",))
    if weeks > 0:
        notes.append(f"Sakat: {weeks} hafta daha oynayamaz.")
    if int(proneness) >= PRONE_RISK:
        notes.append("Sakatlığa yatkın bir bünyesi var.")
    if int(recent_injuries) >= RECENT_INJURY_RISK:
        notes.append(f"Son dönemde {int(recent_injuries)} kez sakatlandı.")
    if int(age) >= 33 and int(proneness) >= 12:
        notes.append("Yaşı ilerlemiş; toparlanma süreleri uzuyor.")
    if notes:
        return MedicalResult(MEDICAL_RISK, MEDICAL_LABELS[MEDICAL_RISK], tuple(notes))
    return MedicalResult(MEDICAL_PASS, MEDICAL_LABELS[MEDICAL_PASS], ("Sorun bulunmadı.",))


def proneness_label(proneness: int) -> str:
    p = int(proneness)
    if p >= PRONE_RISK:
        return "Sakatlığa yatkın"
    if p >= 11:
        return "Ortalama"
    return "Sağlam bünyeli"


# ===========================================================================
# 10) GOZLEM (BILGI YUZDESI)
# ===========================================================================

KNOWN_THRESHOLD = 25               # teklif / bilgi alma icin gereken bilgi
DETAIL_THRESHOLD = 50              # potansiyel araligi, istekliligi, serbest kalma bedeli
FULL_THRESHOLD = 75                # sakatlik egilimi, sozlesme ayrintisi
SAME_LEAGUE_KNOWLEDGE = 35         # ayni ligde oynayan oyuncular (maclarini izliyorsun)
SCOUT_GAIN_BASE = 15               # gozlem gorevi: haftalik 15 + gozlemci yetenegi (1-20)
NO_SCOUT_GAIN = 12
MAX_KNOWLEDGE = 100
KNOWLEDGE_LABELS = ((FULL_THRESHOLD, "Ayrıntılı rapor"), (DETAIL_THRESHOLD, "İyi biliniyor"),
                    (KNOWN_THRESHOLD, "Az biliniyor"), (0, "Bilinmiyor"))


def weekly_scouting_gain(judging_ability: int | None) -> int:
    """Gozlem gorevindeki oyuncu icin haftalik bilgi artisi."""
    if judging_ability is None:
        return NO_SCOUT_GAIN
    return SCOUT_GAIN_BASE + max(1, min(20, int(judging_ability)))


def knowledge_label(knowledge: int) -> str:
    for threshold, label in KNOWLEDGE_LABELS:
        if int(knowledge) >= threshold:
            return label
    return "Bilinmiyor"


def knowledge_margin(base_margin: int, knowledge: int) -> int | None:
    """
    Gozlemci sis payini bilgiyle olcekler: tam bilgi (100) gozlemcinin payi, esikte (25) payin ~1.45 kati. Esigin
    altinda None (rapor yok). Kendi oyuncumuz icin cagiran 0 verir.
    """
    k = int(knowledge)
    if k < KNOWN_THRESHOLD:
        return None
    if base_margin <= 0:
        return 0
    return int(math.ceil(base_margin * (1.6 - 0.6 * min(k, MAX_KNOWLEDGE) / MAX_KNOWLEDGE)))


# ===========================================================================
# 11) SERBEST KALMA BEDELI VE BASLIK/ODDS YARDIMCILARI
# ===========================================================================

RELEASE_ATTRACTIVE = 1.25          # madde oyuncunun degerinin bu katini asmiyorsa AI kulupleri ilgilenir
RELEASE_BUDGET_SHARE = 0.7         # AI kasasinin en fazla bu kadarini maddeye yatirir
RELEASE_TRIGGER_CHANCE = 0.35      # uygun alici varken haftalik tetiklenme olasiligi


def release_clause_attractive(clause: int, value: int, buyer_budget: int) -> bool:
    return 0 < int(clause) <= int(value) * RELEASE_ATTRACTIVE and int(clause) <= int(buyer_budget) * \
        RELEASE_BUDGET_SHARE


def title_odds(rank_in_league: int, league_size: int) -> float:
    """Kulubun (itibar sirasina gore) sezonluk lig sampiyonlugu olasiligi."""
    if league_size <= 1:
        return 0.9
    return {1: 0.45, 2: 0.25, 3: 0.15}.get(int(rank_in_league), 0.07)


def cup_odds(reputation: int) -> float:
    rep = int(reputation)
    return 0.15 if rep >= 92 else 0.08 if rep >= 88 else 0.03


def patience_label(patience: int) -> str:
    """K12: sabir sayi olarak gosterilmez."""
    p = int(patience)
    if p >= 4:
        return "Kulüp sabırlı"
    if p >= 2:
        return "Kulüp gerginleşiyor"
    return "Son şans: kulüp görüşmeleri kesmek üzere"


def terms_mood(persuasion: float, required: float) -> str:
    """K12: ikna skoru yerine oyuncunun ruh hali."""
    gap = float(persuasion) - float(required)
    if gap >= 0:
        return "Teklife sıcak bakıyor"
    if gap >= -5:
        return "Anlaşmaya yakın"
    if gap >= -15:
        return "Kararsız"
    return "Teklifi yetersiz buluyor"


def sum_amounts(rows: Iterable[tuple[int, int, int]]) -> int:
    return sum(amount for _seq, _due, amount in rows)


# ===========================================================================
# 12) 15F: CANLI PAZAR (AI <-> AI), SONRAKI SATIS PAYININ TABANI VE GERI ALIM
#
# Bu bolum SAF kurallardir: veritabani yok, RNG disaridan verilir. Orkestrasyon transfer_desk.WorldMarket'ta.
# Bayrak LIVE_MARKET kapaliyken transfer_desk bu bolumdeki hicbir fonksiyonu cagirmaz ve dunya 15F oncesiyle
# BIT-BIT aynidir (career_manager._ai_transfer_deals eski yoluyla calisir).
# VARSAYILAN: 15F bayrak acma commit'inden beri ACIK (YENIDEN TEMELLENDIRME 8). Kapali yol silinmedi ve
# korunur: .claude/phase14/kanit/15F_betikler/flag_off.py ile kosuldugunda eski HEAD_PARITY ozetleri gecer.
# ===========================================================================

LIVE_MARKET = True                 # KURAL BAYRAGI: dunya pazari (AI<->AI transfer/kiralik, soylenti, son gun)

WINDOW_DEALS_PER_CLUB = 0.62       # donem basina dunya capinda hedef AI<->AI transfer = AI kulup sayisi x bu
WINDOW_DEALS_MIN = 4               # cok kucuk dunyada bile bu kadar denenir
WINDOW_DEALS_MAX = 120             # kabul bandinin ust ucu asilmaz
DEADLINE_BOOST = 1.7               # donemin SON haftasi ("son gun"): haftalik kota bu kat
MARKET_SQUAD_FLOOR = 20            # AI kulubu A takimi bu sayinin altina dusurecek satisi yapmaz
MARKET_SQUAD_CAP = 30              # A takimi bu sayida (ve ustunde) olan AI kulubu pazardan oyuncu ALMAZ
MARKET_BUDGET_RESERVE = 0.15       # KIS doneminde kasanin bu kadari harcanmaz (kasa asla eksiye dusmez)
MARKET_SUMMER_RESERVE = 0.45       # YAZ doneminde daha cok ayrilir: kulup ocak icin para saklar
MARKET_WINTER_SHARE = 0.6          # kis doneminin hedefi yaz hedefinin bu kadari (ocak daha sakin)
MARKET_WEALTH_POWER = 2.6          # alici agirligi (maas butcesi ^ bu): buyuk kulup harcamanin cogunu yapar
#                                    (3,0 denendi: yaz pencereleri yukseldi ama ocak dustu, 3 sezon toplaminda
#                                     %34,5 -> %33,3; 2,6 pencereler arasinda en tutarli sonucu veriyor)
MARKET_MIN_WEIGHT = 1.0            # agirlik tabani (kucuk kulup de nadiren pazara cikar)
MARKET_CASH_REFERENCE = 20_000_000  # bu kadar harcanabilir kasasi olan kulup tam agirlikta; altinda oransal
MARKET_MAX_IN_PER_WINDOW = 4       # bir kulup bir donemde en fazla bu kadar oyuncu alir (taban)
MARKET_MAX_IN_RICH = 2.0           # zenginlik orani basina ek alis hakki (market_max_in)
MARKET_MAX_OUT_PER_WINDOW = 5      # ... ve satar
MARKET_MIN_TARGET_SCORE = 1.5      # market_score esigi (career_manager.AI_MIN_TARGET_SCORE ile ayni sayi)
MARKET_DEPTH_WEIGHT = 0.6          # ikinci adama gore guclenme, ilk adama gore guclenmenin bu kadari sayilir
MARKET_STAR_OVERALL = 78           # bu gucun ustundeki her puan "yildiz" sayilir
MARKET_STAR_APPETITE = 3.5         # buyuk kulubun yildiz istahi (puan primi)
MARKET_STAR_TOLERANCE = 2          # ihtiyaci olmayan buyuk kulup, ikinci adamindan en cok bu kadar zayif
#                                    yildizi da alir (altinda ilgilenmez): "en iyi kulup de alir" kapisi
MARKET_ELITE_MEDIANS = 3.0         # ELIT kulup = maas butcesi dunyanin ORTANCASININ bu kati (elite_wage_floor).
#                                    Yalniz elit kulup "ihtiyacim yok" kapisini asip yildiz alir. Esik SABIT
#                                    DEGIL, dunyanin kendi ortancasindan hesaplanir: sentetik / kucuk / tek ligli
#                                    dunyada ya da 16A lig piramidinde ortanca bambaska olur; sabit esik ya hic
#                                    kulubu elit saymaz (ocak cokusu geri gelir) ya da yarisini elit sayar
#                                    (buyuk kuluplerin payi seyrelir, 1. sezon yazi %34,7'ye dusmustu). Ikisi de
#                                    SESSIZCE olurdu.
#                                    DUZ DUNYADA ELIT KULUP YOKTUR VE KAPI KAPALI KALIR -- bu DOGRU davranistir,
#                                    kusur degil: kapinin modelledigi sey "o kadar zengin ki ihtiyaci olmayan
#                                    yildizi da alir" kuluptur ve duz bir dunyada boyle bir kulup yoktur. Olctuk
#                                    (kanit/15F/elit_esik.txt): acik veri dunyasinda 6 kulup (%5), sentetik
#                                    dunyada 0 (en buyuk kulup ortancanin yalnizca 2,68 kati). "En buyugun %80'i"
#                                    gibi ikinci bir taban EKLENMEDI: duz dunyada yapay elit uretirdi.
#                                    16A lig piramidi geldiginde yeniden olculecek.
MARKET_WAGE_REFERENCE = 2_500_000   # zenginlik orani: HAFTALIK MAAS BUTCESI / bu
MARKET_WEALTH_CAP = 3.0            # istah bu orandan sonra artmaz
MARKET_BUYER_TRIES = 6             # bir denemede en fazla bu kadar alici aday kulup cekilir
MARKET_TARGET_POOL = 12            # alicinin bakacagi en iyi aday sayisi
MARKET_FILL_PER_WINDOW = 3         # donem acilisinda akademiden en fazla bu kadar oyuncu A takima cikarilir
MARKET_FILL_HEADROOM = 2           # tamamlama hedefi = MARKET_SQUAD_FLOOR + bu. TABANA tamamlamak YETMEZ:
#                                    tam tabandaki (20) kulup SATAMAZ (market_squad_gate satistan SONRA >= 20
#                                    ister), dunya 20'de yiginlasir ve pazar saticisiz kalir. Olctuk: tabana
#                                    tamamlarken 114 kulubun 61'i tam 20'de kaldi, yalnizca 31'i (%27) satabildi
#                                    ve 2. sezonun ocaginda buyuk kuluplerin harcama payi %15'e dustu.
MARKET_LIST_SURPLUS = 0.32         # kadro onemi bu degerin altindaki AI oyuncusu donem acilisinda listelenir
MARKET_LIST_LOAN_AGE = 23          # bu yas ve alti, ilk 11'e giremeyen AI oyuncusu kiralik listesine girer
RUMOURS_PER_WEEK = 3               # menajerin gelen kutusuna haftalik en fazla bu kadar soylenti (15D gurultu siniri)


def window_span(week: int, season_weeks: int, season_finished: bool = False) -> tuple[int, int] | None:
    """Acik transfer doneminin (ilk hafta, son hafta) araligi; donem kapaliysa None. Sezon arasi: yaz araligi."""
    window = transfer_window(week, season_weeks, season_finished)
    if not window.open:
        return None
    if window.name == WINDOW_WINTER and window.winter is not None:
        return window.winter
    return window.summer


def window_index(week: int, span: tuple[int, int], season_finished: bool = False) -> tuple[int, int, bool]:
    """
    (kacinci hafta 1..N, donem uzunlugu N, son hafta mi). Sezon arasinda (season_finished) donem tek haftalik
    sayilir: devir yapilana kadar her hafta "son gun"dur.
    """
    first, last = int(span[0]), int(span[1])
    length = max(1, last - first + 1)
    if season_finished:
        return 1, 1, True
    index = min(length, max(1, int(week) - first + 1))
    return index, length, index >= length


def window_deal_target(ai_clubs: int, winter: bool = False) -> int:
    """
    Donem basina dunya capinda hedeflenen AI<->AI transfer sayisi (kabul bandi: acik veri dunyasinda 40-120).
    KIS donemi yazin MARKET_WINTER_SHARE kadaridir: ocak gercekte de daha sakindir (114 kulup: yaz 71, kis 43).
    """
    raw = round(max(0, int(ai_clubs)) * WINDOW_DEALS_PER_CLUB * (MARKET_WINTER_SHARE if winter else 1.0))
    return int(max(WINDOW_DEALS_MIN, min(WINDOW_DEALS_MAX, raw)))


def weekly_quota(target: int, index: int, length: int, deadline: bool) -> int:
    """
    Bu haftanin transfer kotasi. Donem hedefi haftalara agirlikli bolunur: normal hafta 1, SON hafta
    DEADLINE_BOOST agirlik alir (CM'nin "son gun"u gercekten yogundur). Haftalarin toplami hedefe BIREBIR
    esittir: son hafta kalan her seyi alir.
        hedef 70, 5 hafta, boost 1,7 -> 12 + 12 + 12 + 12 + 22
    """
    target, length = max(0, int(target)), max(1, int(length))
    index = max(1, min(length, int(index)))
    base = int(target / ((length - 1) + DEADLINE_BOOST))
    if index < length:
        return max(0, base)
    return max(0, target - base * (length - 1))


def market_buyer_weight(spend_cap: int, wage_budget: int, reputation: int) -> float:
    """
    Agirlikli alici secimi. Kulubun BUYUKLUGU haftalik maas butcesinden okunur (kasa degil): transfer kasasi
    sezonlar gectikce her kulupte sisiyor ve buyuk kulubu ayirt etmiyor; maas butcesi kararlidir (acik veri
    dunyasinda en buyuk kulup ortancanin ~4 kati). Itibar kucuk bir carpandir. Harcanabilir kasa
    MARKET_CASH_REFERENCE'in altina dustukce agirlik oransal duser (parasi biten kulup pazardan cekilir).
    Kabul: en zengin 5 kulup harcamanin >= %35'i.
    """
    scale = (max(0, int(wage_budget)) / 1_000_000.0) ** MARKET_WEALTH_POWER
    cash = min(1.0, max(0, int(spend_cap)) / MARKET_CASH_REFERENCE)
    return MARKET_MIN_WEIGHT + scale * (0.75 + max(1, min(100, int(reputation))) / 200.0) * cash


def weighted_pick(rng, items: list, weights: list[float]):
    """Tohumlu agirlikli secim (random.choices yerine: ayni cekilis her yerde ayni sonucu versin)."""
    total = sum(max(0.0, w) for w in weights)
    if not items or total <= 0:
        return None
    roll = rng.random() * total
    upto = 0.0
    for item, weight in zip(items, weights, strict=True):
        upto += max(0.0, weight)
        if roll < upto:
            return item
    return items[-1]


def market_max_in(wealth: float) -> int:
    """Kulubun bir donemde alabilecegi en fazla oyuncu: taban + zenginlik primi (buyuk kulup daha cok alir)."""
    return MARKET_MAX_IN_PER_WINDOW + int(round(max(0.0, float(wealth) - 1.0) * MARKET_MAX_IN_RICH))


def elite_wage_floor(wage_budgets) -> float:
    """
    ELIT esigi: dunyanin ORTANCA haftalik maas butcesi x MARKET_ELITE_MEDIANS. Donem acilisinda BIR KEZ
    hesaplanir (kulup basina degil). Bos liste -> sonsuz (hicbir kulup elit degil).
    """
    values = sorted(int(v) for v in wage_budgets if v is not None)
    if not values:
        return float("inf")
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0
    return float(median) * MARKET_ELITE_MEDIANS


def market_wealth(wage_budget: int) -> float:
    """Kulubun buyukluk orani (haftalik maas butcesi / MARKET_WAGE_REFERENCE), MARKET_WEALTH_CAP ile sinirli."""
    return min(MARKET_WEALTH_CAP, max(0.0, int(wage_budget)) / MARKET_WAGE_REFERENCE)


def market_score(*, player_overall: int, player_age: int, best_at_position: int, depth_at_position: int,
                 shortfall: float, wealth: float, elite: bool = False) -> float:
    """
    Dunya pazarinda alicinin bir hedefi ne kadar istedigi (transfers.target_score'un 15F surumu; 0 = ilgilenmez).

    transfers.target_score yalnizca "benim EN IYIMDEN iyi mi" diye sorar; bu yuzden ligin en iyi kulubu HIC
    alis yapmaz (CM'de tam tersi olur). 15F iki sey ekler:
        DERINLIK  ikinci adama gore guclenme de sayilir (MARKET_DEPTH_WEIGHT) -- buyuk kulup rotasyonunu kurar;
        YILDIZ ISTAHI  buyuk kulup (market_wealth: maas butcesi) MARKET_STAR_OVERALL ustundeki her puan icin
        prim verir, boylece harcamanin buyuk kismi zengin kuluplerden gelir (kabul: en zengin 5 kulup >= %35).
        Bu prim IHTIYAC KAPISINI DA ACAR: kadrosunu yamamis buyuk kulup ihtiyaci olmasa bile yildiz almaya
        devam eder (MARKET_STAR_TOLERANCE); aksi halde yazin kadrosunu tamamlayip ocakta pazardan cekiliyor.
    """
    upgrade = int(player_overall) - int(best_at_position)
    depth = int(player_overall) - int(depth_at_position)
    star = max(0, int(player_overall) - MARKET_STAR_OVERALL)
    appetite = min(MARKET_WEALTH_CAP, max(0.0, float(wealth))) * MARKET_STAR_APPETITE * star / 5.0
    if upgrade <= 0 and depth <= 0 and float(shortfall) <= 0:
        # Kadrosunu yamamis buyuk kulup, katiyen ihtiyaci olmasa da YILDIZ almaya devam eder. Bu kapi olmadan
        # buyuk kulup yaz doneminde kadrosunu tamamlayip ocakta pazardan tamamen cekiliyordu (olctuk: en buyuk
        # 5 kulup yazin 22-25, ocakta 3-10 transfer; kasalari 416-654M, kadrolari 22 -- ne para ne kadro engeldi).
        if not elite or appetite <= 0 or depth < -MARKET_STAR_TOLERANCE:
            return 0.0                 # orta sinif kulup ihtiyaci olmayan oyuncuyu ALMAZ
        return appetite
    age_bonus = 2.0 if int(player_age) <= 26 else (-2.0 if int(player_age) >= 32 else 0.0)
    score = max(float(upgrade), depth * MARKET_DEPTH_WEIGHT) + float(shortfall) * 1.5 + age_bonus
    return score + appetite


def market_squad_gate(seller_squad: int, buyer_squad: int) -> str | None:
    """Kadro tabani / tavani ihlali (Turkce) ya da None: satici 20'nin altina inmez, alici 30'a cikmaz."""
    if int(seller_squad) - 1 < MARKET_SQUAD_FLOOR:
        return f"Satıcının A takımı {MARKET_SQUAD_FLOOR} oyuncunun altına düşer."
    if int(buyer_squad) + 1 > MARKET_SQUAD_CAP:
        return f"Alıcının A takımı {MARKET_SQUAD_CAP} oyuncuyu aşar."
    return None


def market_window_allowance(budget_now: int, spent_this_window: int, winter: bool = False) -> int:
    """
    Kulubun bu DONEMDE harcayabilecegi KALAN tutar. Ayrilan pay donem acilisindaki kasadan hesaplanir
    (simdiki kasa + bu donemde harcanan); her teklifte yalnizca kalan kasanin yuzdesi alinsaydi kulup kasayi
    asimptotik olarak tuketir ve ocakta parasiz kalirdi (olctuk: buyuk kuluplerin ocak harcamasi %2,8'e
    iniyordu). Boylece yaz ayirmasi GERCEKTEN ayrilmis olur.
    """
    reserve = MARKET_BUDGET_RESERVE if winter else MARKET_SUMMER_RESERVE
    spent = max(0, int(spent_this_window))
    opening = max(0, int(budget_now)) + spent
    return max(0, int(opening * (1.0 - reserve)) - spent)


def market_spend_cap(budget: int, winter: bool = False) -> int:
    """
    Kulubun bir AI<->AI transferinde harcayabilecegi en yuksek tutar. YAZ doneminde kasanin
    MARKET_SUMMER_RESERVE payi ayrilir (kulup ocak penceresi icin para saklar), KIS doneminde yalnizca
    MARKET_BUDGET_RESERVE. Kasa hicbir zaman eksiye dusmez.
    """
    reserve = MARKET_BUDGET_RESERVE if winter else MARKET_SUMMER_RESERVE
    return max(0, int(int(budget) * (1.0 - reserve)))


def sell_on_amount(pct: int, sale_amount: int, *, profit_basis: bool = False, original_fee: int = 0) -> int:
    """
    Sonraki satis payi tutari. Brut taban: satistan alinan tutarin %payi. KAR tabani (profit_basis): yalnizca
    bonservisi asan kisim paylasilir -- satis bedeli alis bedelinin altindaysa pay SIFIRDIR.
    Taksitli satista `sale_amount` o anda ALINAN tutardir; kar tabaninda alis bedeli de orantili dusulur
    (cagiran `original_fee` olarak o taksitin payina dusen alis bedelini verir).
    """
    pct = max(0, min(MAX_SELL_ON_PCT, int(pct)))
    amount = max(0, int(sale_amount))
    if profit_basis:
        amount = max(0, amount - max(0, int(original_fee)))
    return amount * pct // 100


def buy_back_open(agreed_season: int, current_season: int, seasons: int) -> bool:
    """Geri alim maddesi hala gecerli mi (anlasmanin sezonu dahil, `seasons` sezon boyunca)."""
    if not seasons:
        return False
    return int(current_season) < int(agreed_season) + max(1, int(seasons))


BUY_BACK_TRIGGER_RATIO = 1.35      # geri alim: oyuncunun degeri bedelin bu katini asarsa AI kulup maddeyi kullanir


def buy_back_attractive(fee: int, value: int) -> bool:
    """AI satici kulup geri alim maddesini tetikler mi: oyuncunun degeri bedeli belirgin astiysa."""
    return int(fee) > 0 and int(value) >= int(fee) * BUY_BACK_TRIGGER_RATIO
