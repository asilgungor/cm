"""
national_rules.py
=================
Milli takim kurallari (Faz 12 / 14. Asama, 12C). SAF modul: veritabani, ORM ve Streamlit bilmez
(oyuncular ordek tipidir; models.Position yerine mevki DEGERI "GK"/"DEF"/"MID"/"FWD" okunur).

    nation_of             -> oyuncunun milli takimi: uyruk, yoksa (sentetik oyuncu) liginin ulkesi
    eligible_nations      -> en az MIN_NATIONAL_PLAYERS oyuncusu olan uluslar
    position_quotas / ai_callups -> AI milli kadro secimi (mevki kotalari, sonra guc)
    validate_callups      -> menajerin kadro cagrisi hatalari
    rank_pct / required_level / job_offer_eligible -> milli takim is teklifi esigi
    contract_until        -> en fazla CONTRACT_MAX_SEASONS sezonluk sozlesme
    expected_stage / sack_decision -> federasyon beklentisi ve gorevden alma
    intl_reputation_delta -> milli mac / tur / turnuva sonucunun menajer taninirligina etkisi

Soccer Manager kurallari: milli takim kulup isiyle BIRLIKTE yurutulur, menajer basina tek milli takim,
sozlesme en fazla 2 sezon, kadro o uyruktan en fazla 30 oyuncu, milli maclarda kondisyon kaybi ve sakatlik yok.

Uyruk normalizasyonu (nation_of):
    * Bilinen uyruklar (FIFA kodu, Ingilizce ve Turkce yazim; buyuk/kucuk harf, aksan ve noktalama
      duyarsiz: club_directory.plain_key) oyunun Turkce ulke adina cevrilir: "TUR", "Turkey",
      "turkiye" -> "Türkiye"; "ENG", "England" -> "İngiltere". Lig ulkeleri (LEAGUES) bu adlarla aynidir.
    * Bilinmeyen uyruk bosluklari sadelestirilmis haliyle kalir ("Cabo  Verde" -> "Cabo Verde").
    * Uyruk bossa (sentetik oyuncular) lig ulkesi ayni kurallarla kullanilir; o da bossa UNKNOWN_NATION
      ("Diğer"). "Diğer" hicbir zaman milli takim olmaz (eligible_nations disarida birakir).

AI kadro (ai_callups): 23 kisilik temel kota GK 3, DEF 8, MID 7, FWD 5; baska limitte en buyuk kalan
yontemiyle olceklenir (30 -> 4/10/9/7, 16 -> 2/6/5/3), en az 1 kaleci. Sakat oyuncu alinmaz, ayni id bir kez.
Her mevkide guc (azalan) -> yas (genc once) -> id. Bir mevkide yeterli oyuncu yoksa bos yerler kalan en guclu
saha oyunculariyla, onlar da bitince kalecilerle doldurulur. Donus: GK, DEF, MID, FWD sirasinda id'ler.

Taninirlik olcegi reputation.py ile aynidir (kulup maci W 0.12 / D 0.02 / L -0.08):
    mac          W ve D, tur agirligi ile carpilir (eleme 1.0, grup 1.25, son 16 / ceyrek 1.5, yari 1.75,
                 final 2.0); maglubiyet agirliksiz -0.08
    tur atlama   "Q" sonucu: Dunya Kupasi'na katilma +0.5, grubu gecme +0.4 (reputation.CUP_ROUND_WON["GROUP"]);
                 eleme turu galibiyeti tur atlamadir: son 16 +0.4, ceyrek +0.7, yari +1.0 (CUP_ROUND_WON)
    final        sampiyon +3.0 (kulup kupasi 2.5, lig 2.0), finalist +0.8 (kulup 0.6)
    32'lik kupayi kazanmak (2G 1B grup) ~ +6.2; Devler Arenasi sampiyonlugu ~ +5.7.

Gorevden alma (sack_decision): beklenen ve ulasilan tur STAGE_ORDER uzerinde karsilastirilir
(QUAL < GROUP < R16 < QF < SF < FINAL < CHAMPION; GROUP = Dunya Kupasi'na katildi).
    hedef tuttu ya da asildi          -> gorevde kalir
    2+ tur geride                     -> gorevden alinir
    1 tur geride ve puan orani < 0.40 -> gorevden alinir, aksi halde uyari ile kalir
points_share: kampanyada (eleme + turnuva) alinabilecek puanin alinan orani, 0..1.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

import reputation
from club_directory import OTHER_COUNTRY, plain_key
from world_cup import (
    FINAL,
    GROUP,
    KNOCKOUT_STAGES,
    QF,
    QUAL,
    R16,
    SF,
    STAGE_ORDER,
    WORLD_CUP_SIZES,
    stage_label,
)

MIN_NATIONAL_PLAYERS = 23
MAX_CALLUPS = 30
MIN_MATCHDAY_SQUAD = 16
CONTRACT_MAX_SEASONS = 2
DEFAULT_CALLUPS = 23

UNKNOWN_NATION = OTHER_COUNTRY

POSITION_ORDER: tuple[str, ...] = ("GK", "DEF", "MID", "FWD")
BASE_QUOTAS: Mapping[str, int] = {"GK": 3, "DEF": 8, "MID": 7, "FWD": 5}     # 23 kisilik kadro
_BASE_TOTAL = sum(BASE_QUOTAS.values())

# Is teklifi: ulkenin itibar sirasi yuzdesi (1/n = en guclu, 1.0 = en zayif) -> gereken menajer seviyesi
JOB_LEVEL_BANDS: tuple[tuple[float, int], ...] = ((0.10, 7), (0.25, 5), (0.50, 3), (1.0, 1))

SACK_POINTS_SHARE = 0.40

QUALIFIED = "Q"
INTL_MATCH_DELTA: Mapping[str, float] = dict(reputation.MATCH_DELTA)
STAGE_WEIGHT: Mapping[str, float] = {QUAL: 1.0, GROUP: 1.25, R16: 1.5, QF: 1.5, SF: 1.75, FINAL: 2.0}
KNOCKOUT_WIN_BONUS: Mapping[str, float] = {stage: reputation.CUP_ROUND_WON[stage] for stage in (R16, QF, SF)}
WORLD_CUP_QUALIFIED = 0.5
GROUP_STAGE_PASSED = reputation.CUP_ROUND_WON["GROUP"]
WORLD_CUP_CHAMPION = 3.0
WORLD_CUP_RUNNER_UP = 0.8
_KNOCKOUT = frozenset({R16, QF, SF, FINAL})

# Oyun adi -> taninan yazimlar (FIFA kodu, Ingilizce, Turkce varyantlar). Anahtarlar plain_key ile eslenir.
_NATION_ALIASES: Mapping[str, tuple[str, ...]] = {
    "Türkiye": ("TUR", "Turkey", "Turkiye", "Türkei"),
    "İngiltere": ("ENG", "England", "Ingiltere"),
    "İspanya": ("ESP", "Spain", "Espana", "Ispanya"),
    "Almanya": ("GER", "DEU", "Germany", "Deutschland"),
    "İtalya": ("ITA", "Italy", "Italia", "Italya"),
    "Fransa": ("FRA", "France"),
    "Portekiz": ("POR", "PRT", "Portugal"),
    "Hollanda": ("NED", "NLD", "HOL", "Netherlands", "Holland", "The Netherlands"),
    "Belçika": ("BEL", "Belgium", "Belcika"),
    "Brezilya": ("BRA", "Brazil", "Brasil"),
    "Arjantin": ("ARG", "Argentina"),
    "Uruguay": ("URU", "URY"),
    "Kolombiya": ("COL", "Colombia"),
    "Şili": ("CHI", "CHL", "Chile", "Sili"),
    "Ekvador": ("ECU", "Ecuador"),
    "Paraguay": ("PAR", "PRY"),
    "Peru": ("PER",),
    "Venezuela": ("VEN",),
    "Meksika": ("MEX", "Mexico"),
    "ABD": ("USA", "United States", "United States of America", "US"),
    "Kanada": ("CAN", "Canada"),
    "Hırvatistan": ("CRO", "HRV", "Croatia", "Hirvatistan"),
    "Sırbistan": ("SRB", "Serbia", "Sirbistan"),
    "İsviçre": ("SUI", "CHE", "Switzerland", "Isvicre"),
    "Avusturya": ("AUT", "Austria"),
    "Danimarka": ("DEN", "DNK", "Denmark"),
    "İsveç": ("SWE", "Sweden", "Isvec"),
    "Norveç": ("NOR", "Norway", "Norvec"),
    "Finlandiya": ("FIN", "Finland"),
    "İzlanda": ("ISL", "Iceland", "Izlanda"),
    "Polonya": ("POL", "Poland"),
    "Çekya": ("CZE", "Czech Republic", "Czechia", "Cekya", "Çek Cumhuriyeti"),
    "Slovakya": ("SVK", "Slovakia"),
    "Slovenya": ("SVN", "Slovenia"),
    "Macaristan": ("HUN", "Hungary"),
    "Romanya": ("ROU", "ROM", "Romania"),
    "Ukrayna": ("UKR", "Ukraine"),
    "Rusya": ("RUS", "Russia"),
    "Yunanistan": ("GRE", "GRC", "Greece"),
    "İskoçya": ("SCO", "Scotland", "Iskocya"),
    "Galler": ("WAL", "Wales"),
    "İrlanda": ("IRL", "Republic of Ireland", "Ireland", "Irlanda"),
    "Kuzey İrlanda": ("NIR", "Northern Ireland", "Kuzey Irlanda"),
    "Bosna-Hersek": ("BIH", "Bosnia and Herzegovina", "Bosnia & Herzegovina", "Bosnia"),
    "Arnavutluk": ("ALB", "Albania"),
    "Kosova": ("KOS", "KVX", "Kosovo"),
    "Kuzey Makedonya": ("MKD", "North Macedonia", "Macedonia"),
    "Karadağ": ("MNE", "Montenegro", "Karadag"),
    "Gürcistan": ("GEO", "Georgia", "Gurcistan"),
    "Senegal": ("SEN",),
    "Fas": ("MAR", "Morocco"),
    "Cezayir": ("ALG", "DZA", "Algeria"),
    "Tunus": ("TUN", "Tunisia"),
    "Mısır": ("EGY", "Egypt", "Misir"),
    "Nijerya": ("NGA", "Nigeria"),
    "Gana": ("GHA", "Ghana"),
    "Fildişi Sahili": ("CIV", "Ivory Coast", "Cote d'Ivoire", "Côte d'Ivoire", "Fildisi Sahili"),
    "Kamerun": ("CMR", "Cameroon"),
    "Mali": ("MLI",),
    "Gine": ("GUI", "GIN", "Guinea"),
    "Japonya": ("JPN", "Japan"),
    "Güney Kore": ("KOR", "South Korea", "Korea Republic", "Guney Kore"),
    "Avustralya": ("AUS", "Australia"),
    "İran": ("IRN", "Iran", "IR Iran"),
    "Suudi Arabistan": ("KSA", "SAU", "Saudi Arabia"),
}
_ALIAS_INDEX: dict[str, str] = {}
for _canonical, _aliases in _NATION_ALIASES.items():
    for _alias in (_canonical, *_aliases):
        _ALIAS_INDEX[plain_key(_alias)] = _canonical


# ===========================================================================
# Uyruk
# ===========================================================================

def _canonical_nation(text: str | None) -> str | None:
    clean = " ".join(str(text or "").split())
    if not clean:
        return None
    return _ALIAS_INDEX.get(plain_key(clean), clean)


def nation_of(player_nationality: str | None, league_country: str) -> str:
    """Oyuncunun milli takimi (bkz. modul aciklamasi). Asla bos donmez."""
    return (
        _canonical_nation(player_nationality)
        or _canonical_nation(league_country)
        or UNKNOWN_NATION
    )


def eligible_nations(counts: Mapping[str, int]) -> list[str]:
    """
    En az MIN_NATIONAL_PLAYERS oyuncusu olan uluslar. Ayni ulusun farkli yazimlari (TUR / Turkey)
    toplanir; "Diğer" ve bos adlar disarida kalir. Sira: oyuncu sayisi (azalan), ad.
    """
    totals: dict[str, int] = {}
    for name, count in counts.items():
        nation = _canonical_nation(name)
        if nation is None or nation == UNKNOWN_NATION:
            continue
        totals[nation] = totals.get(nation, 0) + int(count)
    eligible = [nation for nation, total in totals.items() if total >= MIN_NATIONAL_PLAYERS]
    return sorted(eligible, key=lambda nation: (-totals[nation], plain_key(nation), nation))


# ===========================================================================
# Kadro cagrisi
# ===========================================================================

def position_quotas(limit: int = DEFAULT_CALLUPS) -> dict[str, int]:
    """Mevki kotalari: 23 -> GK 3 / DEF 8 / MID 7 / FWD 5, en buyuk kalan yontemiyle olceklenir (<= 30)."""
    size = max(0, min(int(limit), MAX_CALLUPS))
    quotas = {pos: BASE_QUOTAS[pos] * size // _BASE_TOTAL for pos in POSITION_ORDER}
    by_remainder = sorted(
        POSITION_ORDER,
        key=lambda pos: (-(BASE_QUOTAS[pos] * size % _BASE_TOTAL), POSITION_ORDER.index(pos)),
    )
    for pos in by_remainder[: size - sum(quotas.values())]:
        quotas[pos] += 1
    if size > 0 and quotas["GK"] == 0:
        donor = max(POSITION_ORDER[1:], key=lambda pos: (quotas[pos], -POSITION_ORDER.index(pos)))
        quotas[donor] -= 1
        quotas["GK"] = 1
    return quotas


def _position(player) -> str | None:
    value = getattr(player, "position", None)
    value = getattr(value, "value", value)
    return None if value is None else str(value).strip().upper()


def _is_injured(player, week: int | None) -> bool:
    flag = getattr(player, "injured", None)
    if isinstance(flag, bool):
        if flag:
            return True
    elif callable(flag) and week is not None and flag(week):
        return True
    for attr in ("injury", "injury_weeks"):
        if getattr(player, attr, None):
            return True
    if week is not None:
        check = getattr(player, "is_injured", None)
        if callable(check):
            return bool(check(week))
        until = getattr(player, "injured_until_week", None)
        if until is not None:
            return int(until) > week
    return False


def _strength_key(player) -> tuple:
    age = getattr(player, "age", None)
    return (-int(getattr(player, "overall_rating", 0) or 0), age is None, age or 0, int(player.id))


def ai_callups(players: Sequence, limit: int = DEFAULT_CALLUPS, *, week: int | None = None) -> list[int]:
    """
    AI milli kadrosu (bkz. modul aciklamasi). players: ayni ulustan oyuncular (id, position,
    overall_rating; varsa age ve sakatlik alanlari: injured / injury / injury_weeks, week verilirse
    is_injured(week) ya da injured_until_week). En fazla min(limit, MAX_CALLUPS) id.
    """
    quotas = position_quotas(limit)
    size = sum(quotas.values())
    if size == 0:
        return []
    seen: set[int] = set()
    pool = []
    for player in players:
        pid = int(player.id)
        if pid in seen:
            continue
        seen.add(pid)
        if not _is_injured(player, week):
            pool.append(player)
    ranked = sorted(pool, key=_strength_key)

    chosen: list = []
    for pos in POSITION_ORDER:
        chosen.extend([p for p in ranked if _position(p) == pos][: quotas[pos]])
    chosen_ids = {int(p.id) for p in chosen}
    rest = [p for p in ranked if int(p.id) not in chosen_ids]
    fillers = [p for p in rest if _position(p) != "GK"] + [p for p in rest if _position(p) == "GK"]
    chosen.extend(fillers[: size - len(chosen)])

    def order(player) -> tuple:
        pos = _position(player)
        rank = POSITION_ORDER.index(pos) if pos in POSITION_ORDER else len(POSITION_ORDER)
        return (rank, *_strength_key(player))

    return [int(p.id) for p in sorted(chosen, key=order)]


def validate_callups(player_ids: Sequence[int], eligible_ids: Iterable[int]) -> list[str]:
    """
    Menajerin kadro cagrisi hatalari (bos liste = gecerli): tekrar, baska uyruk, MAX_CALLUPS ustu,
    maç kadrosu icin en az min(MIN_MATCHDAY_SQUAD, uygun oyuncu sayisi) oyuncu.
    """
    ids = [int(pid) for pid in player_ids]
    allowed = {int(pid) for pid in eligible_ids}
    errors: list[str] = []
    if len(set(ids)) != len(ids):
        errors.append("Aynı oyuncu kadroya birden fazla kez çağrıldı.")
    foreign = sorted({pid for pid in ids if pid not in allowed})
    if foreign:
        errors.append(f"Bu ülkenin oyuncusu olmayan id'ler: {foreign}")
    unique = len(set(ids))
    if unique > MAX_CALLUPS:
        errors.append(f"Milli kadroya en fazla {MAX_CALLUPS} oyuncu çağrılabilir ({unique} seçildi).")
    needed = min(MIN_MATCHDAY_SQUAD, len(allowed))
    if unique < needed:
        errors.append(f"Maç kadrosu için en az {needed} oyuncu çağrılmalı ({unique} seçildi).")
    return errors


# ===========================================================================
# Is teklifi ve sozlesme
# ===========================================================================

def _unit(value: float, label: str) -> float:
    number = float(value)
    if math.isnan(number):
        raise ValueError(f"{label} sayı olmalı.")
    return max(0.0, min(1.0, number))


def rank_pct(rank: int, total: int) -> float:
    """Itibar sirasi -> yuzde: 1. / 20 -> 0.05, 20. / 20 -> 1.0."""
    if total < 1 or not 1 <= rank <= total:
        raise ValueError(f"Sıra 1 ile {total} arasında olmalı ({rank} verildi).")
    return rank / total


def required_level(nation_rank_pct: float) -> int:
    """Gereken menajer seviyesi (1-10): ilk %10 -> 7, ilk %25 -> 5, ilk %50 -> 3, diger -> 1."""
    pct = _unit(nation_rank_pct, "Ülke sıralaması yüzdesi")
    for ceiling, level in JOB_LEVEL_BANDS:
        if pct <= ceiling:
            return level
    return JOB_LEVEL_BANDS[-1][1]  # pragma: no cover - pct <= 1.0 her zaman bir banda duser


def job_offer_eligible(manager_level: int, nation_rank_pct: float) -> bool:
    """Menajer seviyesi (1-10, reputation.level) bu ulkeden teklif almaya yetiyor mu?"""
    level = max(1, min(reputation.MAX_LEVEL, int(manager_level)))
    return level >= required_level(nation_rank_pct)


def contract_until(current_season: int, seasons: int = CONTRACT_MAX_SEASONS) -> int:
    """Sozlesme bitis sezonu (dahil): 1..CONTRACT_MAX_SEASONS sezona kirpilir. S3'te 2 sezon -> 4."""
    length = max(1, min(CONTRACT_MAX_SEASONS, int(seasons)))
    return int(current_season) + length - 1


# ===========================================================================
# Beklenti ve gorevden alma
# ===========================================================================

def _stage_code(stage, allowed: Sequence[str] = STAGE_ORDER) -> str:
    code = str(getattr(stage, "value", stage)).strip().upper()
    if code not in allowed:
        raise ValueError(f"Bilinmeyen tur: {stage!r} ({', '.join(allowed)}).")
    return code


def expected_stage(reputation_rank: int, size: int) -> str:
    """
    Federasyon beklentisi. reputation_rank: uygun uluslar arasinda itibar sirasi (1 = en guclu).
        sira > size          -> QUAL (katilmasi beklenmez)
        sira <= size // 16   -> SF   (32'likte ilk 2, 16'likta ilk 1)
        sira <= size // 4    -> ilk eleme turu (1. torba: 32 -> R16, 16 -> QF, 8 -> SF, 4 -> FINAL)
        diger                -> GROUP (turnuvaya katilmak)
    """
    if size not in WORLD_CUP_SIZES:
        raise ValueError(f"Desteklenmeyen Dünya Kupası boyu: {size}.")
    if reputation_rank < 1:
        raise ValueError("Sıra 1'den başlar.")
    if reputation_rank > size:
        return QUAL
    if reputation_rank <= size // 16:
        return SF
    if reputation_rank <= size // 4:
        return KNOCKOUT_STAGES[size][0]
    return GROUP


def sack_decision(expected_stage: str, reached_stage: str, points_share: float) -> tuple[bool, str]:
    """(gorevden alinir mi, Turkce gerekce). Kurallar modul aciklamasinda."""
    expected = _stage_code(expected_stage)
    reached = _stage_code(reached_stage)
    share = _unit(points_share, "Puan oranı")
    percent = round(share * 100)
    shortfall = STAGE_ORDER.index(expected) - STAGE_ORDER.index(reached)
    target = f"hedef {stage_label(expected)}, ulaşılan {stage_label(reached)}"
    if shortfall < 0:
        return False, f"Hedef aşıldı ({target}). Federasyon memnun."
    if shortfall == 0:
        return False, f"Hedef tuttu ({target})."
    if shortfall >= 2:
        return True, f"Federasyon görevine son verdi: beklentinin çok gerisinde kalındı ({target})."
    if share < SACK_POINTS_SHARE:
        return True, (
            f"Federasyon görevine son verdi: hedefin bir tur gerisinde kalındı ({target}) "
            f"ve puan oranı düşük (%{percent})."
        )
    return False, (
        f"Hedefin bir tur gerisinde kalındı ({target}) ama puan oranı (%{percent}) "
        "federasyonun güvenini korudu."
    )


# ===========================================================================
# Taninirlik
# ===========================================================================

def intl_reputation_delta(outcome: str, stage: str | None, won_tournament: bool) -> float:
    """
    Menajer taninirligina etki (reputation.apply ile uygulanir). Cagri protokolu:
        her milli mactan sonra : outcome "W"/"D"/"L", stage mac turu (None = eleme maci),
                                 won_tournament = final kazanildi mi
        grup / eleme gecilince : outcome "Q", stage "QUAL" (Dunya Kupasi'na katildi) ya da "GROUP"
    Eleme turu maci berabere verilemez (uzatma/penalti sonucu W/L); won_tournament yalnizca final
    galibiyetiyle birlikte gecerlidir. Tutarsiz girdi -> ValueError.
    """
    code = str(outcome).strip().upper()
    phase = QUAL if stage is None else _stage_code(stage, tuple(STAGE_WEIGHT))
    if code == QUALIFIED:
        if won_tournament:
            raise ValueError("Tur atlama kaydı turnuva şampiyonluğu olamaz.")
        if phase == QUAL:
            return WORLD_CUP_QUALIFIED
        if phase == GROUP:
            return GROUP_STAGE_PASSED
        raise ValueError("Eleme turlarında tur atlama maç sonucuyla (W) verilir.")
    if code not in INTL_MATCH_DELTA:
        raise ValueError(f"Bilinmeyen sonuç: {outcome!r} (W, D, L ya da Q).")
    if phase in _KNOCKOUT and code == "D":
        raise ValueError("Eleme maçı berabere kaydedilemez; uzatma/penaltı sonucu W ya da L verilmeli.")
    final_won = phase == FINAL and code == "W"
    if bool(won_tournament) != final_won:
        raise ValueError("Turnuva şampiyonluğu yalnızca kazanılan finalle birlikte verilir.")

    base = INTL_MATCH_DELTA[code]
    delta = base if code == "L" else base * STAGE_WEIGHT[phase]
    if code == "W":
        delta += KNOCKOUT_WIN_BONUS.get(phase, 0.0)
    if phase == FINAL:
        delta += WORLD_CUP_CHAMPION if final_won else WORLD_CUP_RUNNER_UP
    return round(delta, 4)

