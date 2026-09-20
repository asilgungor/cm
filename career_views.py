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

TEK SIS MODELI (14G, K12): "Mevcut yetenek", "Potansiyel yetenek", piyasa degeri ve 1-20 ozellik izgarasi AYNI bilgi
esiklerinden gecer (izleyen kulubun oyuncu hakkindaki bilgisi, 0-100; kendi oyuncun 100):
    bilgi < %25 (FOG_RANGE_FROM)   -> "?"  (yetenek, deger, izgara; transfer_rules.KNOWN_THRESHOLD ile ayni)
    %25-69                          -> aralik (bilgi arttikca ic ice daralir; cm_attributes.range_width ile AYNI genislik)
    %70+ (FOG_EXACT_FROM)          -> kesin
Ayrinti kalemleri kendi esiginde: potansiyel %50 (DETAIL), sozlesme suresi %75 (FULL) -- masanin raporuyla ayni.
Ekrana yildiz degil CM sozcugu gider (ABILITY_WORDS: "Çok zayıf" ... "Dünya çapında"). Bilgi yuzdesi tek sorgulu
knowledge_map ile (TransferDesk.knowledge_of kuraliyla ayni: kendi oyuncun 100, ayni lig en az 35, gozlem kaydi).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from sqlalchemy import or_, select

import cm_attributes
import contracts
import fitness
import reputation
import staff as staff_rules
import transfer_rules
from career_manager import CareerManager
from development import is_wonderkid
from finance import format_money
from models import (
    LineupStatus,
    Player,
    Position,
    ScoutAssignment,
    SquadRole,
    Staff,
    StaffRole,
    Team,
)
from stars import FULL, GLYPH_FULL, GLYPH_HALF, HALF, star_value
from transfers import ROLE_LABELS

STATUS_LABELS = {LineupStatus.XI: "İlk 11", LineupStatus.BENCH: "Kulübe", LineupStatus.OUT: "Kadro dışı"}
STATUS_BY_LABEL = {v: k for k, v in STATUS_LABELS.items()}
POSITION_ORDER = {Position.GK: 0, Position.DEF: 1, Position.MID: 2, Position.FWD: 3}


# ===========================================================================
# 0) ORTAK ETIKETLER (14G: player_view / transfer_centre_view / career_views tek kaynak)
# ===========================================================================

ABILITY_LABEL, POTENTIAL_LABEL = "Mevcut yetenek", "Potansiyel yetenek"
UNKNOWN_TEXT = "?"
# Motorun alti ozelligi (gozlemci raporu, mevki uygunlugu): ekran etiketleri tek yerde
ENGINE_ATTRIBUTE_LABELS: tuple[tuple[str, str], ...] = (
    ("pace", "Hız"), ("shooting", "Şut"), ("passing", "Pas"), ("defending", "Defans"), ("dribbling", "Dribling"),
    ("goalkeeping", "Kalecilik"),
)
POSITION_LABELS: dict[str, str] = {"GK": "Kaleci", "DEF": "Defans", "MID": "Orta saha", "FWD": "Forvet"}
# Yetenegin (1-99 -> yildiz 0.5-5.0) CM sozcugu. Sayi yerine gecen tek olcek budur; sirasi yuksekten dusuge.
ABILITY_WORDS: tuple[tuple[float, str], ...] = (
    (5.0, "Dünya çapında"), (4.0, "Çok iyi"), (3.5, "İyi"), (3.0, "Yeterli"),
    (2.5, "Vasat"), (2.0, "Zayıf"), (0.0, "Çok zayıf"),
)
# Form / moral (1-100) -> CM sozcugu
MOOD_WORDS: tuple[tuple[int, str], ...] = ((75, "Çok iyi"), (55, "İyi"), (35, "Orta"), (0, "Kötü"))
# CM "Squad Status": motorun uc kadro rolu + gencler icin iki gelecek duzeyi (yalnizca gosterim)
SQUAD_STATUS_LABELS: tuple[str, ...] = (ROLE_LABELS[SquadRole.STAR], ROLE_LABELS[SquadRole.FIRST_TEAM],
                                        ROLE_LABELS[SquadRole.BACKUP], "Geleceğin umudu", "İyi bir genç")
YOUNG_STATUS_AGE = 21


def ability_word(rating: float | None) -> str:
    """Yetenek (1-99) -> CM sozcugu ('İyi', 'Vasat'); None -> '?'."""
    value = star_value(rating)
    if value is None:
        return UNKNOWN_TEXT
    return next(word for threshold, word in ABILITY_WORDS if value >= threshold)


def mood_word(value) -> str:
    """Form / moral (1-100) -> CM gibi sozcuk: Kotu / Orta / Iyi / Cok iyi (sayi ekrana gitmez)."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return UNKNOWN_TEXT
    return next(word for low, word in MOOD_WORDS if number >= low)


def squad_status(role, age: int, wonderkid: bool = False) -> str:
    """Kadro rolu -> CM tarzi kulupteki statu etiketi (yalnizca gosterim)."""
    value = str(getattr(role, "value", role))
    if value == "STAR":
        return SQUAD_STATUS_LABELS[0]
    if value == "FIRST_TEAM":
        return SQUAD_STATUS_LABELS[1]
    if int(age) <= YOUNG_STATUS_AGE:
        return SQUAD_STATUS_LABELS[3] if wonderkid else SQUAD_STATUS_LABELS[4]
    return SQUAD_STATUS_LABELS[2]


def _star_count(text: str) -> float | None:
    full = text.count(FULL) + text.count(GLYPH_FULL)
    half = text.count(HALF) + text.count(GLYPH_HALF)
    return None if not full and not half else full + 0.5 * half


def star_text_word(text: str | None) -> str:
    """Baska serit modullerinin urettigi yildiz metni ('⭐⭐⭐💫', '⭐⭐ – ⭐⭐⭐', '★★½') -> CM sozcugu (ekranda yildiz
    yok). Yildizsiz metin (bilinmiyor '–') -> '?'."""
    if not text:
        return UNKNOWN_TEXT
    words = []
    for part in str(text).split(" – "):
        value = _star_count(part)
        if value is None:
            plain = part.strip()
            words.append(plain if plain and plain not in ("–", "-", UNKNOWN_TEXT) else UNKNOWN_TEXT)
            continue
        words.append(next(word for threshold, word in ABILITY_WORDS if value >= threshold))
    words = list(dict.fromkeys(words))
    return words[0] if len(words) == 1 else f"{words[0]} – {words[-1]}"


def star_value_word(value: float | None) -> str:
    """Yildiz degeri (0.5-5.0; milli takim cagri listesi) -> CM sozcugu."""
    if value is None:
        return UNKNOWN_TEXT
    return next(word for threshold, word in ABILITY_WORDS if float(value) >= threshold)


def short_money(amount: float | None) -> str:
    """Tablo hucresi icin kisa para ('850K', '12.5M'); birim sutun basliginda."""
    if amount is None:
        return UNKNOWN_TEXT
    return format_money(amount).removesuffix(" EUR")


# ===========================================================================
# 0b) TEK SIS MODELI (14G): yetenek, deger, izgara ayni esiklerden
# ===========================================================================

FOG_RANGE_FROM = cm_attributes.RANGE_KNOWLEDGE          # 25: altinda "?"
FOG_EXACT_FROM = cm_attributes.EXACT_KNOWLEDGE          # 70: kesin
FOG_DETAIL_FROM = transfer_rules.DETAIL_THRESHOLD       # 50: potansiyel (masanin raporuyla ayni)
FOG_CONTRACT_FROM = transfer_rules.FULL_THRESHOLD       # 75: sozlesme suresi (masanin raporuyla ayni)
FOG_UNKNOWN, FOG_RANGE, FOG_EXACT = "unknown", "range", "exact"
RATING_SCALE = 5                        # 1-20 izgara araligi -> 1-99 yetenek araligi (x5)
MONEY_STEP = 0.10                       # izgaradaki bir adim genislik -> degerin %10'u


def fog_level(knowledge: float | None) -> str:
    """Bilgi yuzdesinin sis duzeyi: izgara (cm_attributes.attribute_display) ile birebir ayni esikler."""
    if knowledge is None or isinstance(knowledge, bool) or float(knowledge) < FOG_RANGE_FROM:
        return FOG_UNKNOWN
    return FOG_EXACT if float(knowledge) >= FOG_EXACT_FROM else FOG_RANGE


def _fog_fraction(key) -> float:
    digest = hashlib.sha256(f"ofm-fog14g|{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2.0 ** 64


def fog_rating(value: int | None, knowledge: float | None, key, *, lo: int = 1, hi: int = 99) -> tuple[int, int] | None:
    """
    1-99 degerin (yetenek / potansiyel) sisli araligi ya da None ("?"). Genislik izgaranin genisligi x5 (bilgi
    %25'te 20 puan, %70'e dogru 5'e iner, %70+ kesin). Aralik gercek degeri HER ZAMAN icerir; kayma key'e bagli
    sabit bir kesir (yeniden cizimde titremez, sorgu yok).
    """
    if value is None or fog_level(knowledge) == FOG_UNKNOWN:
        return None
    v = min(hi, max(lo, int(value)))
    width = cm_attributes.range_width(float(knowledge)) * RATING_SCALE
    if width == 0:
        return v, v
    low = v - int(_fog_fraction(key) * (width + 1))
    low = min(max(low, lo), hi - width)
    return low, low + width


def _nice(amount: float, up: bool) -> int:
    """Para siniri 2 anlamli basamaga (alt sinir asagi, ust sinir yukari): '20.5M – 23.1M' yerine '20M – 24M'."""
    if amount <= 0:
        return 0
    step = 10 ** max(0, int(math.floor(math.log10(amount))) - 1)
    return int((math.ceil if up else math.floor)(amount / step) * step)


def fog_money(value: int | None, knowledge: float | None, key) -> tuple[int, int] | None:
    """Piyasa degerinin sisli araligi (ayni esikler; genislik degerin %10'u x izgara adimi) ya da None ("?")."""
    if value is None or fog_level(knowledge) == FOG_UNKNOWN:
        return None
    v = max(0, int(value))
    steps = cm_attributes.range_width(float(knowledge))
    if steps == 0:
        return v, v
    span = v * MONEY_STEP * steps
    low = v - _fog_fraction(key) * span
    return _nice(max(0.0, low), up=False), _nice(low + span, up=True)


def ability_text(bounds: tuple[int, int] | None) -> str:
    """Sisli yetenek araligi -> CM sozcugu ('İyi' / 'İyi – Çok iyi' / '?')."""
    if bounds is None:
        return UNKNOWN_TEXT
    low, high = ability_word(min(bounds)), ability_word(max(bounds))
    return low if low == high else f"{low} – {high}"


def money_range_text(bounds: tuple[int, int] | None) -> str:
    """Sisli deger araligi -> '850K' / '850K – 1.2M' / '?'."""
    if bounds is None:
        return UNKNOWN_TEXT
    low, high = bounds
    return short_money(low) if low == high else f"{short_money(low)} – {short_money(high)}"


@dataclass(frozen=True)
class PlayerFog:
    """Izleyen kulubun gozunden bir oyuncu (K12): tum ekranlar bunu gosterir, hicbiri gercek sayiyi degil."""
    knowledge: int
    ability: tuple[int, int] | None
    potential: tuple[int, int] | None
    value: tuple[int, int] | None
    contract_years: int | None

    @property
    def level(self) -> str:
        return fog_level(self.knowledge)

    @property
    def ability_text(self) -> str:
        return ability_text(self.ability)

    @property
    def potential_text(self) -> str:
        return ability_text(self.potential)

    @property
    def value_text(self) -> str:
        return money_range_text(self.value)

    @property
    def contract_text(self) -> str:
        return UNKNOWN_TEXT if self.contract_years is None else (
            f"{self.contract_years} yıl" if self.contract_years else "Son sezon")

    @property
    def ability_mid(self) -> int | None:
        return None if self.ability is None else (self.ability[0] + self.ability[1]) // 2

    @property
    def value_mid(self) -> int | None:
        return None if self.value is None else (self.value[0] + self.value[1]) // 2


def player_fog(cm: CareerManager | None, viewer: Team | None, player: Player, knowledge: int | None = None) -> PlayerFog:
    """
    TEK SIS FONKSIYONU. knowledge verilmezse: kendi oyuncun 100, digerleri 0 (cagiran knowledge_map ile verir).
    Potansiyel her zaman gozlemcinin tahminidir (CareerManager.potential_estimate), baska kulupte %50 bilgiyle.
    """
    own = viewer is not None and player.team_id == viewer.id
    k = 100 if own else int(knowledge or 0) if viewer is not None else 0
    ability = fog_rating(player.overall_rating, k, (player.id, "ability"))
    value = fog_money(player.market_value, k, (player.id, "value"))
    potential = None
    if cm is not None and viewer is not None and (own or k >= FOG_DETAIL_FROM):
        low, high = cm.potential_estimate(viewer, player)
        potential = (int(low), int(high))
    contract = int(player.contract_years or 0) if own or k >= FOG_CONTRACT_FROM else None
    return PlayerFog(k, ability, potential, value, contract)


def knowledge_map(db, viewer: Team | None, player_ids: Iterable[int]) -> dict[int, int]:
    """
    Bilgi yuzdesi (0-100) birden cok oyuncu icin IKI sorguda: gozlem kayitlari + oyuncunun kulubu / ligi. Kural
    TransferDesk.knowledge_of ile AYNI (kendi oyuncun 100, ayni ligdeki oyuncu en az %35, gozlemci kaydi); masa
    gerektirmez (turnuva modunda da calisir). Salt okunur.
    """
    ids = sorted({int(i) for i in player_ids if i is not None})
    if not ids:
        return {}
    if viewer is None:
        return dict.fromkeys(ids, 0)
    scouted = dict(db.execute(select(ScoutAssignment.player_id, ScoutAssignment.knowledge).where(
        ScoutAssignment.team_id == viewer.id, ScoutAssignment.player_id.in_(ids))).all())
    clubs = db.execute(select(Player.id, Player.team_id, Team.league_id)
                       .outerjoin(Team, Team.id == Player.team_id).where(Player.id.in_(ids))).all()
    result: dict[int, int] = {}
    for pid, team_id, league_id in clubs:
        if team_id is not None and team_id == viewer.id:
            result[pid] = transfer_rules.MAX_KNOWLEDGE
            continue
        known = int(scouted.get(pid) or 0)
        if league_id is not None and league_id == viewer.league_id:
            known = max(known, transfer_rules.SAME_LEAGUE_KNOWLEDGE)
        if team_id is None:                          # 15A: serbest oyuncunun profili menajerlerde dolasir
            known = max(known, contracts.FREE_AGENT_KNOWLEDGE)
        result[pid] = min(transfer_rules.MAX_KNOWLEDGE, known)
    return result


def fog_rows(cm: CareerManager | None, viewer: Team | None, players: Iterable[Player], *,
             potential: bool = False) -> dict[int, PlayerFog]:
    """Bir liste oyuncunun sisi (bilgi haritasi TEK seferde: iki sorgu). potential=True: potansiyel tahmini de."""
    players = list(players)
    known = knowledge_map(cm.db, viewer, [p.id for p in players]) if (cm is not None and viewer is not None) else {}
    source = cm if potential else None
    return {p.id: player_fog(source, viewer, p, known.get(p.id, 0)) for p in players}


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

    goals: int = 0                         # bu sezon (resmi maclar; season_stats verilirse)
    assists: int = 0
    appearances: int = 0
    minutes: int = 0
    yellow: int = 0
    red: int = 0
    shots: int = 0
    nationality: str | None = None
    contract_expiry_season: int | None = None
    injury_weeks: int = 0
    transfer_listed: bool = False
    loan_listed: bool = False
    wage_demand: int | None = None
    clauses: tuple[str, ...] = ()           # sozlesme maddeleri (serbest kalma, rol sozu, primler)

    @property
    def low_condition(self) -> bool:
        return self.condition < fitness.CONDITION_WARN

    @property
    def stars(self) -> str:
        """14G: CM sozcugu (kendi oyuncun: kesin). Eski ad korunur."""
        return ability_word(self.overall)

    @property
    def potential_stars(self) -> str:
        return ability_text(None if self.potential_low is None else (self.potential_low, self.potential_high))


def _potential(cm: CareerManager | None, team: Team, p: Player) -> tuple[int | None, int | None, bool]:
    """Gozlemci tahmini potansiyel araligi ve (tahmine gore) wonderkid isareti."""
    if cm is None:
        return None, None, False
    low, high = cm.potential_estimate(team, p)
    return low, high, is_wonderkid(p.age, p.overall_rating, (low + high) // 2)


CLAUSE_LABELS: tuple[tuple[str, str], ...] = (
    ("promised_role", "Rol sözü"), ("loyalty_bonus", "Sadakat"), ("appearance_bonus", "Maç primi"),
    ("goal_bonus", "Gol primi"),
)


def contract_clauses(player: Player) -> tuple[str, ...]:
    """15A: sozlesme maddesi etiketleri (kadro "Sözleşme" gorunumu): serbest kalma bedeli ve sozlesme maddeleri."""
    clauses = player.contract_clauses or {}
    labels = ["Serbest kalma"] if player.release_clause else []
    labels += [label for key, label in CLAUSE_LABELS if clauses.get(key)]
    if clauses.get("promise_broken"):
        labels.append("Söz tutulmadı")
    return tuple(labels)


def season_stats(db, team_id: int, season: int) -> dict[int, tuple[int, int, int, int, int, int, int]]:
    """Kulubun bu sezonki resmi mac istatistikleri oyuncu basina TEK GROUP BY sorgusuyla:
    id -> (mac, dakika, gol, asist, sari, kirmizi, sut). Kadro gorunumleri (14G) icin."""
    from sqlalchemy import func

    from models import Fixture, PlayerMatchStat

    rows = db.execute(
        select(PlayerMatchStat.player_id, func.count(PlayerMatchStat.id),
               func.coalesce(func.sum(PlayerMatchStat.minutes), 0), func.coalesce(func.sum(PlayerMatchStat.goals), 0),
               func.coalesce(func.sum(PlayerMatchStat.assists), 0),
               func.coalesce(func.sum(PlayerMatchStat.yellow_cards), 0),
               func.count(PlayerMatchStat.id).filter(PlayerMatchStat.red_card.is_(True)),
               func.coalesce(func.sum(PlayerMatchStat.shots), 0))
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .where(Fixture.season == int(season), PlayerMatchStat.team_id == int(team_id))
        .group_by(PlayerMatchStat.player_id)).all()
    return {int(pid): tuple(int(v or 0) for v in values) for pid, *values in rows}


def squad_rows(team: Team, week: int, cm: CareerManager | None = None, *, stats: bool = False) -> list[SquadRow]:
    """A takim kadrosu. cm verilirse potansiyel tahmini ve wonderkid isareti de doldurulur; stats=True (cm gerekir)
    bu sezonun mac / gol / asist / kart sutunlarini TEK sorguyla ekler (14G kadro gorunumleri)."""
    players = sorted(team.players, key=lambda p: (POSITION_ORDER[p.position], -p.overall_rating))
    season_map = season_stats(cm.db, team.id, cm.season) if stats and cm is not None else {}
    season = int(cm.season) if cm is not None else 0
    rows = []
    for p in players:
        condition = int(getattr(p, "condition", 100))
        pot_low, pot_high, wonder = _potential(cm, team, p)
        apps, minutes, goals, assists, yellow, red, shots = season_map.get(p.id, (0, 0, 0, 0, 0, 0, 0))
        injured_until = int(p.injured_until_week or 0)
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
            goals=goals, assists=assists, appearances=apps, minutes=minutes, yellow=yellow, red=red, shots=shots,
            nationality=p.nationality,
            contract_expiry_season=contracts.expiry_season(season, p.contract_years) if season else None,
            clauses=contract_clauses(p),
            injury_weeks=max(0, injured_until - int(week)) if p.is_injured(week) else 0,
            transfer_listed=bool(p.transfer_listed), loan_listed=bool(p.loan_listed),
            wage_demand=int(p.wage_demand) if p.wage_demand else None,
        ))
    return rows


# 14G: CM kadro "Görünüm" secici -- her gorunum >= 10 sutun; para / sayi sutunlari SAYISAL (tablo sayiyla siralar:
# "850K" < "12.5M"), bicim column_config'te (web_app.squad_table_section).
VIEW_GENERAL, VIEW_CONTRACT, VIEW_STATS, VIEW_FITNESS = "Genel", "Sözleşme", "Maç istatistikleri", "Kondisyon"
SQUAD_VIEWS: tuple[str, ...] = (VIEW_GENERAL, VIEW_CONTRACT, VIEW_STATS, VIEW_FITNESS)
MONEY_COLUMNS = ("Maaş/hf (EUR)", "Değer (EUR)", "Maaş talebi (EUR)")
PERCENT_COLUMNS = ("Kondisyon",)
RATING_COLUMNS = ("Ort. not",)
CONDITION_WORDS = {"good": "Dinç", "warn": "Yorgun", "low": "Bitkin"}
NUMERIC_COLUMNS = frozenset({*MONEY_COLUMNS, *PERCENT_COLUMNS, *RATING_COLUMNS, "Gol/maç", "Sakatlık (hafta)",
                             "Bitiş (sezon)", "Sözleşme (yıl)", "Maç", "Dk", "Gol", "Ast", "Şut", "Sarı", "Kırm.",
                             "Maçsız hafta", "Yaş"})


def _status_text(r: SquadRow) -> str:
    if r.unavailable:
        return r.unavailable
    return r.status + (f" · {r.slot}" if r.slot else "")


def _listing_text(r: SquadRow) -> str:
    return " · ".join(x for x in ("Satılık" if r.transfer_listed else "", "Kiralık" if r.loan_listed else "") if x) \
        or "—"


def squad_view_rows(rows: list[SquadRow], view: str) -> list[dict]:
    """Kadro tablosunun satirlari (gorunume gore). Kendi kadron: yetenek kesin CM sozcugu, potansiyel gozlemci
    tahmini; para ve istatistikler sayi (None -> bos hucre, siralamada sonda)."""
    out = []
    for r in rows:
        base = {"Mv": r.position, "Oyuncu": r.name, "Yaş": r.age}
        rating = round(r.average_rating, 2) if r.average_rating is not None else 0.0   # mac yoksa 0.00 (en altta)
        if view == VIEW_CONTRACT:
            base.update({"Statü": squad_status(r.squad_role_key, r.age, r.wonderkid),
                         "Maaş/hf (EUR)": int(r.wage or 0), "Değer (EUR)": int(r.market_value or 0),
                         "Sözleşme (yıl)": int(r.contract_years or 0), "Bitiş (sezon)": r.contract_expiry_season,
                         "Maaş talebi (EUR)": int(r.wage_demand or 0), "Maddeler": " · ".join(r.clauses) or "—",
                         "Liste": _listing_text(r),
                         ABILITY_LABEL: r.stars, POTENTIAL_LABEL: r.potential_stars})
        elif view == VIEW_STATS:
            base.update({"Maç": r.appearances, "Dk": r.minutes, "Gol": r.goals, "Ast": r.assists, "Şut": r.shots,
                         "Sarı": r.yellow, "Kırm.": r.red, "Ort. not": rating,
                         "Gol/maç": round(r.goals / r.appearances, 2) if r.appearances else 0.0})
        elif view == VIEW_FITNESS:
            base.update({"Kondisyon": int(r.condition), "Kondisyon durumu": CONDITION_WORDS.get(r.condition_band, "—"),
                         "Form": mood_word(r.form), "Moral": mood_word(r.morale), "Durum": _status_text(r),
                         "Sakatlık (hafta)": int(r.injury_weeks or 0), "Maçsız hafta": int(r.weeks_idle or 0),
                         "Not": "Kondisyon düşük" if r.low_condition else ""})
        else:
            base.update({"Uyruk": r.nationality or "—", "Statü": squad_status(r.squad_role_key, r.age, r.wonderkid),
                         "Durum": _status_text(r), ABILITY_LABEL: r.stars, POTENTIAL_LABEL: r.potential_stars,
                         "Kondisyon": int(r.condition), "Moral": mood_word(r.morale), "Form": mood_word(r.form),
                         "Ort. not": rating, "Maç": r.appearances, "Gol": r.goals})
        out.append(base)
    return out


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
    """Gelisim/yaslanma notu, sayisal guc yerine CM sozcuguyle (sozcuk degismediyse ok isaretiyle)."""
    old, new = getattr(note, "old_overall", None), getattr(note, "new_overall", None)
    if old is None or new is None:
        return f"Gelişim: {note.player_name}"
    arrow = "↑" if new > old else "↓"
    before, after = ability_word(old), ability_word(new)
    change = f"{before} → {after}" if before != after else f"{after} {arrow}"
    kind = "Gelişim" if new > old else "Yaşlanma"
    potential = ""
    if getattr(note, "potential_low", None) is not None and new > old:
        potential = f" · potansiyel {ability_text((note.potential_low, note.potential_high))}"
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
    min_estimated_overall: int = 1                         # >1 iken yetenegi bilinmeyen ("?") oyuncu elenir
    max_estimated_value: int | None = None                 # EUR; None = sinirsiz (bilinmeyen deger elenmez)
    limit: int = 40


# Transfer Merkezi "Mevcut yetenek en az" sozcuk olcegi: (etiket, gereken en dusuk tahmini yetenek)
FREE_AGENT_CLUB = "Kulüpsüz"                            # 15A: sozlesmesi biten oyuncu (team_id NULL)
LEVEL_FILTER_ALL = "Tümü"
LEVEL_FILTERS: tuple[tuple[str, int], ...] = (
    (LEVEL_FILTER_ALL, 1), ("Zayıf", 50), ("Vasat", 55), ("Yeterli", 60), ("İyi", 65), ("Çok iyi", 70),
    ("Dünya çapında", 80),
)


@dataclass
class MarketRow:
    id: int
    name: str
    club: str
    position: str
    age: int
    overall_low: int | None                 # None: bilinmiyor (bilgi < %25)
    overall_high: int | None
    value_low: int | None
    value_high: int | None
    exact: bool
    contract_years: int | None              # None: bilinmiyor (bilgi < %75)
    knowledge: int = 0
    club_id: int | None = None

    @property
    def known(self) -> bool:
        return self.overall_low is not None

    @property
    def overall_estimate(self) -> int | None:
        return None if self.overall_low is None else (self.overall_low + self.overall_high) // 2

    @property
    def value_estimate(self) -> int | None:
        return None if self.value_low is None else (self.value_low + self.value_high) // 2

    @property
    def ability_text(self) -> str:
        """Ekranda sayi yerine CM sozcugu (tek sis modeli): 'İyi' / 'İyi – Çok iyi' / '?'."""
        return ability_text(None if self.overall_low is None else (self.overall_low, self.overall_high))

    stars_text = ability_text                                   # eski ad (13I): artik sozcuk

    @property
    def value_text(self) -> str:
        return money_range_text(None if self.value_low is None else (self.value_low, self.value_high))

    @property
    def contract_text(self) -> str:
        if self.contract_years is None:
            return UNKNOWN_TEXT
        return f"{self.contract_years} yıl" if self.contract_years else "Son sezon"

    def label(self) -> str:
        return f"{self.name} · {self.club} · {self.position} · {self.ability_text}"


def market_rows(cm: CareerManager, buyer: Team, flt: MarketFilter,
                knowledge: Mapping[int, int] | None = None) -> list[MarketRow]:
    """
    Diger kuluplerin oyunculari; yetenek / deger / sozlesme TEK SIS MODELINDEN (bilgi yuzdesi), filtre ve siralama
    sisli tahminlerin orta noktasi uzerinden (gercek deger sizmaz). Yetenegi bilinmeyen oyuncular sonda, ada gore.
    Sorgu: oyuncu + kulup TEK sorgu, bilgi haritasi IKI sorgu (knowledge verilmezse).
    """
    records = cm.db.execute(
        select(Player, Team.name, Team.league_id).outerjoin(Team, Team.id == Player.team_id)
        .where(or_(Player.team_id.is_(None), Player.team_id != buyer.id),         # 15A: serbest oyuncular da listede
               Player.in_academy.is_(False))                                      # akademiler satilik degil
    ).all()
    needle = flt.name.strip().casefold()
    picked = [(p, club or FREE_AGENT_CLUB) for p, club, _league in records
              if (not needle or needle in p.name.casefold())
              and (not flt.positions or p.position.value in flt.positions) and p.age <= flt.max_age]
    known = dict(knowledge) if knowledge is not None else knowledge_map(cm.db, buyer, [p.id for p, _c in picked])
    rows: list[MarketRow] = []
    for p, club in picked:
        fog = player_fog(None, buyer, p, known.get(p.id, 0))
        row = MarketRow(
            id=p.id, name=p.name, club=club, position=p.position.value, age=p.age,
            overall_low=fog.ability[0] if fog.ability else None, overall_high=fog.ability[1] if fog.ability else None,
            value_low=fog.value[0] if fog.value else None, value_high=fog.value[1] if fog.value else None,
            exact=fog.level == FOG_EXACT, contract_years=fog.contract_years, knowledge=fog.knowledge,
            club_id=p.team_id,
        )
        if flt.min_estimated_overall > 1 and (row.overall_estimate is None
                                              or row.overall_estimate < flt.min_estimated_overall):
            continue
        if flt.max_estimated_value is not None and row.value_estimate is not None \
                and row.value_estimate > flt.max_estimated_value:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r.overall_estimate is None, -(r.overall_estimate or 0), r.name))
    return rows[: flt.limit]


def scouted_profile_rows(cm: CareerManager, buyer: Team, player: Player, knowledge: int | None = None) -> list[dict]:
    """Gozlemci raporu (tek sis modeli): yetenek / potansiyel CM sozcuguyle, deger araligi; sayi gosterilmez."""
    if knowledge is None:
        knowledge = knowledge_map(cm.db, buyer, [player.id]).get(player.id, 0)
    fog = player_fog(cm, buyer, player, knowledge)
    return [{"Özellik": ABILITY_LABEL, "Tahmin": fog.ability_text},
            {"Özellik": POTENTIAL_LABEL, "Tahmin": fog.potential_text},
            {"Özellik": "Piyasa değeri (EUR)", "Tahmin": fog.value_text}]


def suggested_opening_fee(row: MarketRow) -> int:
    """Teklif kutusunun baslangic degeri: tahmini degerin %120'si, 100K'ya yuvarli (deger bilinmiyorsa 0)."""
    return int(round((row.value_estimate or 0) * 1.2 / 100_000) * 100_000)


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
        """14G: CM sozcugu (kendi akademin: kesin). Eski ad korunur."""
        return ability_word(self.overall)

    @property
    def potential_stars(self) -> str:
        return ability_text((self.potential_low, self.potential_high))

    def label(self) -> str:
        badge = " · geleceğin yıldızı" if self.wonderkid else ""
        return f"{self.name} · {self.age} yaş · {self.position} · {self.stars}{badge}"

    def to_dict(self) -> dict:
        return {
            "Oyuncu": self.name,
            "Yaş": self.age,
            "Mv": self.position,
            ABILITY_LABEL: self.stars,
            f"{POTENTIAL_LABEL} (gözlemci)": self.potential_stars,
            "Durum": WONDERKID_TEXT if self.wonderkid else (self.unavailable or ""),
        }


WONDERKID_TEXT = "Geleceğin yıldızı"
ACADEMY_WONDER_LABEL = "Yalnız geleceğin yıldızları"


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
