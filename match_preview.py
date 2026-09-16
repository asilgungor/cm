"""
match_preview.py
================
Mac onu istihbarati ve rakip gozlem raporu (Soccer Manager tarzi). SAF MANTIK: veritabani ve
Streamlit bilmez. Girdiler duz veri satirlaridir (PlayedMatch, PlayerSeasonLine, LineupSlot ...);
veritabanindan okuma ve ORM -> satir donusumu preview_views.py'dedir (salt okunur).

Gizli guc kurali (stars.py): sayisal guc (1-99) hicbir cikti alaninda yoktur. Oyuncular yildizla
gosterilir; rakip oyuncularin yildizi gozlemci sisinden (star_range) gecer. Mac notu, gol, asist ve
kart gibi herkese acik istatistikler sayi olarak kalir.

Mac onu (MatchPreview):
    form_summary       son 5 resmi mac (lig + kupa, bu sezon, kronolojik): 'G' galibiyet,
                       'B' beraberlik, 'M' maglubiyet + atilan/yenilen gol. Ayni haftada kupa
                       (hafta ici) ligden once oynanir. Skor uzatmalar dahil, penaltilar haric.
    venue_records      bu sezon ic saha / deplasman karnesi (tarafsiz saha iki tarafa da sayilmaz)
    head_to_head       tum sezonlarda oynanmis karsilasmalar: toplam G/B/M ve son 5 mac (en yeni once)
    players_to_watch   golcu + en yuksek ortalama not + skora katki (en fazla 3); sezon istatistigi
                       yoksa kadronun one cikanlari (rakipte gozlemci tahmini sirasi)
    team_tendencies    mac basi atilan/yenilen gol, gol yemeden biten mac, kart egilimi
    match_verdict      kagit ustu yorum: beklenen ilk 11 gucu x ic saha avantaji x son 5 mac formu.
                       Metinde sayi yoktur ("Kağıt üzerinde favori: X").

Gozlemci raporu (ScoutReport):
    scout_accuracy     en iyi gozlemcinin judging_ability'si -> isabet
                           gozlemci yok 0.35 · 1 -> 0.43 · 8 -> 0.62 · 10 -> 0.675 · 15 -> 0.81 · 20 -> 0.95
    confidence_label   isabet >= 0.80 Yüksek, >= 0.60 Orta, aksi Düşük
    predict_formation  (1 - isabet) x FORMATION_MISS_SCALE ihtimalle dizilis yanlis tahmin edilir
    scramble_lineup    her tahmini ilk 11 oyuncusu (1 - isabet) x SWAP_SCALE ihtimalle makul bir
                       alternatifle yer degistirir: once ayni mevki, yoksa herhangi bir saha oyuncusu;
                       kaleci yalnizca kaleciyle. Alternatif secimi gucluye yatkindir (sira: secim gucu).
    Zar sirasi sabittir (dizilis 2 zar, oyuncu basina 2 zar, sonuc ne olursa olsun): ayni tohumda daha
    iyi gozlemcinin hata denemeleri kotu gozlemcininkilerin alt kumesidir. Tohum (scout_seed): verilen
    tohum + fikstur + oyun haftasi + izleyen takim; sayfa yenilense de rapor degismez, hafta ilerleyince
    gozlemci yeni rapor yazar.
"""

from __future__ import annotations

import random
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from stars import UNKNOWN, stars

LEAGUE = "LEAGUE"
CUP = "CUP"
GK = "GK"

WIN, DRAW, LOSS = "G", "B", "M"
FORM_POINTS = {WIN: 3, DRAW: 1, LOSS: 0}
FORM_LENGTH = 5
H2H_LENGTH = 5
WATCH_LIMIT = 3

VENUE_HOME = "İç saha"
VENUE_AWAY = "Deplasman"
VENUE_NEUTRAL = "Tarafsız saha"

# --- Kagit ustu yorum ---
BALANCED_EDGE = 0.03            # guc orani farki bunun altinda: dengeli
CLEAR_FAVORITE_EDGE = 0.10      # bu ve ustu: acik favori
FORM_NEUTRAL_PPG = 1.5          # son 5 macta mac basi puan notr noktasi
FORM_WEIGHT = 0.01              # notrden her 1 puan sapma gucu %1 kaydirir

# --- Egilim notlari ---
TENDENCY_MIN_MATCHES = 2
PROLIFIC_GOALS = 2.0
BLUNT_GOALS = 0.8
TIGHT_DEFENCE = 0.8
LEAKY_DEFENCE = 2.0
CARD_HEAVY_YELLOWS = 2.5        # mac basi sari kart

# --- Gozlemci ---
NO_SCOUT_ACCURACY = 0.35
SCOUT_ACCURACY_BASE = 0.40
SCOUT_ACCURACY_PER_POINT = 0.0275
MAX_ACCURACY = 0.95
SWAP_SCALE = 0.70
FORMATION_MISS_SCALE = 0.50
CONFIDENCE_BANDS: tuple[tuple[float, str], ...] = ((0.80, "Yüksek"), (0.60, "Orta"))
LOW_CONFIDENCE = "Düşük"
SCOUT_DISCLAIMER = (
    "Bu rapor gözlemcinin tahminidir; rakibin gerçek kadrosu ve dizilişi maç günü farklı olabilir."
)


def _decimal(value: float) -> str:
    """Turkce ondalik: 1.5 -> '1,5'."""
    return f"{value:.1f}".replace(".", ",")


# ===========================================================================
# 1) MAC SONUCLARI: form, ic saha / deplasman, aralarindaki maclar
# ===========================================================================

@dataclass(frozen=True)
class PlayedMatch:
    """Oynanmis mac (lig ya da kupa). label: 'Lig' / 'Devler Arenası · Son 16 ilk maç'."""
    fixture_id: int
    season: int
    week: int
    competition: str
    home_team_id: int
    away_team_id: int
    home_score: int
    away_score: int
    neutral_venue: bool = False
    label: str = "Lig"

    def involves(self, team_id: int) -> bool:
        return team_id in (self.home_team_id, self.away_team_id)

    def score_for(self, team_id: int) -> tuple[int, int]:
        """(atilan, yenilen) -- takimin bakisiyla."""
        if team_id == self.home_team_id:
            return self.home_score, self.away_score
        return self.away_score, self.home_score

    def opponent_of(self, team_id: int) -> int:
        return self.away_team_id if team_id == self.home_team_id else self.home_team_id

    def venue_for(self, team_id: int) -> str:
        if self.neutral_venue:
            return VENUE_NEUTRAL
        return VENUE_HOME if team_id == self.home_team_id else VENUE_AWAY

    @property
    def chronology(self) -> tuple[int, int, int, int]:
        """Sezon, hafta, (kupa hafta ici: once), fikstur id."""
        return self.season, self.week, 0 if self.competition == CUP else 1, self.fixture_id


def outcome_letter(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return WIN
    if goals_for == goals_against:
        return DRAW
    return LOSS


def matches_of(matches: Iterable[PlayedMatch], team_id: int) -> list[PlayedMatch]:
    """Takimin maclari, kronolojik (en eski once)."""
    return sorted((m for m in matches if m.involves(team_id)), key=lambda m: m.chronology)


@dataclass(frozen=True)
class RecentMatch:
    fixture_id: int
    season: int
    week: int
    competition_label: str
    opponent_id: int
    opponent_name: str
    venue: str
    goals_for: int
    goals_against: int
    result: str                         # G / B / M

    @property
    def score_text(self) -> str:
        return f"{self.goals_for}-{self.goals_against}"


@dataclass(frozen=True)
class FormSummary:
    form: str                           # 'GGBMG' (kronolojik, en yeni sonda)
    goals_for: int
    goals_against: int
    matches: tuple[RecentMatch, ...]

    @property
    def points_per_match(self) -> float | None:
        if not self.form:
            return None
        return sum(FORM_POINTS[c] for c in self.form) / len(self.form)


def form_summary(
    matches: Iterable[PlayedMatch],
    team_id: int,
    names: Mapping[int, str] | None = None,
    n: int = FORM_LENGTH,
) -> FormSummary:
    """Son n mac: form harfleri ve bu maclardaki atilan/yenilen gol."""
    names = names or {}
    recent = matches_of(matches, team_id)[-n:] if n > 0 else []
    rows = []
    for m in recent:
        gf, ga = m.score_for(team_id)
        opponent = m.opponent_of(team_id)
        rows.append(RecentMatch(
            fixture_id=m.fixture_id, season=m.season, week=m.week, competition_label=m.label,
            opponent_id=opponent, opponent_name=names.get(opponent, "?"), venue=m.venue_for(team_id),
            goals_for=gf, goals_against=ga, result=outcome_letter(gf, ga),
        ))
    return FormSummary(
        form="".join(r.result for r in rows),
        goals_for=sum(r.goals_for for r in rows),
        goals_against=sum(r.goals_against for r in rows),
        matches=tuple(rows),
    )


@dataclass(frozen=True)
class Record:
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0

    @property
    def points(self) -> int:
        return 3 * self.won + self.drawn

    @property
    def text(self) -> str:
        if not self.played:
            return "Maç yok"
        return f"{self.won}G {self.drawn}B {self.lost}M · {self.goals_for}-{self.goals_against}"


def record_of(scores: Iterable[tuple[int, int]]) -> Record:
    played = won = drawn = lost = gf_total = ga_total = 0
    for gf, ga in scores:
        played += 1
        gf_total += gf
        ga_total += ga
        letter = outcome_letter(gf, ga)
        won += letter == WIN
        drawn += letter == DRAW
        lost += letter == LOSS
    return Record(played, won, drawn, lost, gf_total, ga_total)


def venue_records(matches: Iterable[PlayedMatch], team_id: int) -> tuple[Record, Record]:
    """(ic saha, deplasman) karnesi. Tarafsiz sahadaki maclar ikisine de girmez."""
    own = [m for m in matches_of(matches, team_id) if not m.neutral_venue]
    home = record_of(m.score_for(team_id) for m in own if m.home_team_id == team_id)
    away = record_of(m.score_for(team_id) for m in own if m.away_team_id == team_id)
    return home, away


@dataclass(frozen=True)
class H2HMeeting:
    fixture_id: int
    season: int
    week: int
    competition_label: str
    home_team_id: int
    home_name: str
    away_team_id: int
    away_name: str
    home_score: int
    away_score: int

    @property
    def winner_team_id(self) -> int | None:
        if self.home_score == self.away_score:
            return None
        return self.home_team_id if self.home_score > self.away_score else self.away_team_id

    @property
    def text(self) -> str:
        return f"{self.home_name} {self.home_score} - {self.away_score} {self.away_name}"


@dataclass(frozen=True)
class HeadToHead:
    """team_a / team_b bakisiyla aralarindaki tum oynanmis maclar; son maclar en yeni once."""
    team_a_id: int
    team_b_id: int
    meetings: int
    team_a_wins: int
    draws: int
    team_b_wins: int
    team_a_goals: int
    team_b_goals: int
    last_meetings: tuple[H2HMeeting, ...]
    summary: str


def head_to_head(
    matches: Iterable[PlayedMatch],
    team_a_id: int,
    team_b_id: int,
    names: Mapping[int, str] | None = None,
    n: int = H2H_LENGTH,
) -> HeadToHead:
    names = names or {}
    pair = sorted(
        (m for m in matches
         if team_a_id != team_b_id and m.involves(team_a_id) and m.involves(team_b_id)),
        key=lambda m: m.chronology,
    )
    a = record_of(m.score_for(team_a_id) for m in pair)
    last = tuple(
        H2HMeeting(
            fixture_id=m.fixture_id, season=m.season, week=m.week, competition_label=m.label,
            home_team_id=m.home_team_id, home_name=names.get(m.home_team_id, "?"),
            away_team_id=m.away_team_id, away_name=names.get(m.away_team_id, "?"),
            home_score=m.home_score, away_score=m.away_score,
        )
        for m in reversed(pair[-n:] if n > 0 else [])
    )
    name_a, name_b = names.get(team_a_id, "?"), names.get(team_b_id, "?")
    if not pair:
        summary = "İki takım daha önce karşılaşmadı."
    else:
        summary = (f"{a.played} karşılaşma: {name_a} {a.won} galibiyet, {a.drawn} beraberlik, "
                   f"{name_b} {a.lost} galibiyet.")
    return HeadToHead(
        team_a_id=team_a_id, team_b_id=team_b_id, meetings=a.played,
        team_a_wins=a.won, draws=a.drawn, team_b_wins=a.lost,
        team_a_goals=a.goals_for, team_b_goals=a.goals_against,
        last_meetings=last, summary=summary,
    )


# ===========================================================================
# 2) TAKIM EGILIMLERI
# ===========================================================================

@dataclass(frozen=True)
class TeamTendencies:
    matches: int
    goals_scored_per_match: float
    goals_conceded_per_match: float
    clean_sheets: int
    failed_to_score: int
    yellow_cards: int
    red_cards: int
    notes: tuple[str, ...]

    @property
    def yellow_cards_per_match(self) -> float:
        return round(self.yellow_cards / self.matches, 2) if self.matches else 0.0


def team_tendencies(
    matches: Iterable[PlayedMatch],
    team_id: int,
    yellow_cards: int = 0,
    red_cards: int = 0,
) -> TeamTendencies:
    """Bu sezonun maclarindan egilimler. Kartlar oyuncu mac istatistiklerinden verilir."""
    own = matches_of(matches, team_id)
    played = len(own)
    scores = [m.score_for(team_id) for m in own]
    scored = sum(gf for gf, _ in scores)
    conceded = sum(ga for _, ga in scores)
    clean = sum(1 for _, ga in scores if ga == 0)
    blank = sum(1 for gf, _ in scores if gf == 0)
    per_scored = round(scored / played, 2) if played else 0.0
    per_conceded = round(conceded / played, 2) if played else 0.0

    notes: list[str] = []
    if not played:
        notes.append("Bu sezon henüz resmi maç oynamadı.")
    elif played >= TENDENCY_MIN_MATCHES:
        if per_scored >= PROLIFIC_GOALS:
            notes.append(f"Hücumda üretken: maç başına {_decimal(per_scored)} gol atıyor.")
        elif per_scored <= BLUNT_GOALS:
            notes.append(f"Gol bulmakta zorlanıyor: maç başına {_decimal(per_scored)} gol.")
        if per_conceded <= TIGHT_DEFENCE:
            notes.append(f"Savunması sağlam: maç başına {_decimal(per_conceded)} gol yiyor.")
        elif per_conceded >= LEAKY_DEFENCE:
            notes.append(f"Savunması kırılgan: maç başına {_decimal(per_conceded)} gol yiyor.")
        if clean:
            notes.append(f"{played} maçın {clean} tanesinde gol yemedi.")
        if yellow_cards / played >= CARD_HEAVY_YELLOWS or red_cards:
            notes.append(f"Sert oynuyor: {yellow_cards} sarı, {red_cards} kırmızı kart gördü.")
    if played and not notes:
        notes.append("Belirgin bir eğilim görünmüyor.")
    return TeamTendencies(
        matches=played, goals_scored_per_match=per_scored, goals_conceded_per_match=per_conceded,
        clean_sheets=clean, failed_to_score=blank, yellow_cards=yellow_cards, red_cards=red_cards,
        notes=tuple(notes),
    )


# ===========================================================================
# 3) OYUNCULAR: izlenecekler, eksikler
# ===========================================================================

@dataclass(frozen=True)
class PlayerSeasonLine:
    """Oyuncunun bu sezonki istatistigi. stars: izleyene gore (rakipte gozlemci sisli) yildiz."""
    player_id: int
    name: str
    position: str
    stars: str
    appearances: int = 0
    goals: int = 0
    assists: int = 0
    average_rating: float | None = None
    prominence: float = 0.0             # istatistik yoksa siralama (rakipte gozlemci tahmini)
    available: bool = True


@dataclass(frozen=True)
class WatchPlayer:
    player_id: int
    name: str
    position: str
    stars: str
    goals: int
    assists: int
    appearances: int
    average_rating: float | None
    reason: str
    available: bool


def players_to_watch(lines: Iterable[PlayerSeasonLine], limit: int = WATCH_LIMIT) -> list[WatchPlayer]:
    """
    Dikkat edilmesi gerekenler (en fazla limit):
        1) golcu (en cok gol)            2) en yuksek ortalama not (en az liderin yarisi kadar mac)
        3) skora katki (gol + asist/2)   4) istikrarli not
    Bu sezon kimse oynamadiysa prominence sirasiyla kadronun one cikanlari.
    """
    lines = list(lines)
    if limit <= 0 or not lines:
        return []
    picked: dict[int, str] = {}
    played = [line for line in lines if line.appearances > 0]
    if played:
        scorers = sorted((line for line in played if line.goals > 0),
                         key=lambda x: (-x.goals, -x.assists, -(x.average_rating or 0.0), x.name))
        if scorers:
            picked[scorers[0].player_id] = f"Takımın golcüsü: {scorers[0].goals} gol"
        min_apps = max(1, max(line.appearances for line in played) // 2)
        raters = sorted((line for line in played
                         if line.appearances >= min_apps and line.average_rating is not None),
                        key=lambda x: (-x.average_rating, -x.goals, x.name))
        best = next((x for x in raters if x.player_id not in picked), None)
        if best is not None and len(picked) < limit:
            picked[best.player_id] = f"En yüksek ortalama not: {_decimal(best.average_rating)}"
        contributors = sorted((line for line in played if line.goals + line.assists > 0),
                              key=lambda x: (-(x.goals + 0.5 * x.assists), -(x.average_rating or 0.0), x.name))
        for x in contributors:
            if len(picked) >= limit:
                break
            picked.setdefault(x.player_id, f"Skora katkı: {x.goals} gol, {x.assists} asist")
        for x in raters:
            if len(picked) >= limit:
                break
            picked.setdefault(x.player_id, f"İstikrarlı: ortalama not {_decimal(x.average_rating)}")
    else:
        for x in sorted(lines, key=lambda x: (-x.prominence, x.name)):
            if len(picked) >= limit:
                break
            picked[x.player_id] = "Kadronun öne çıkan ismi"
    by_id = {line.player_id: line for line in lines}
    return [
        WatchPlayer(
            player_id=pid, name=by_id[pid].name, position=by_id[pid].position, stars=by_id[pid].stars,
            goals=by_id[pid].goals, assists=by_id[pid].assists, appearances=by_id[pid].appearances,
            average_rating=by_id[pid].average_rating, reason=reason, available=by_id[pid].available,
        )
        for pid, reason in list(picked.items())[:limit]
    ]


@dataclass(frozen=True)
class AbsentPlayer:
    """Sakat ya da cezali oyuncu. reason: 'Sakat' / 'Cezalı'."""
    player_id: int
    name: str
    position: str
    stars: str
    reason: str
    detail: str                         # '5. haftada dönüyor' / '1 maç ceza'
    return_week: int | None = None      # sakatlikta tekrar oynayabilecegi hafta
    matches_left: int | None = None     # cezada kalan mac


# ===========================================================================
# 4) KAGIT USTU YORUM
# ===========================================================================

def lineup_strength(overalls: Iterable[float]) -> float:
    """Beklenen ilk 11'in ortalama gucu (bos -> 0). Ekrana yalnizca yildizi gider."""
    values = list(overalls)
    return sum(values) / len(values) if values else 0.0


def team_stars(strength: float) -> str:
    return stars(strength) if strength > 0 else UNKNOWN


def form_factor(form: str) -> float:
    """Son maclarin mac basi puani notrden (1.5) sapma basina FORM_WEIGHT."""
    if not form:
        return 1.0
    ppg = sum(FORM_POINTS.get(c, 0) for c in form) / len(form)
    return 1.0 + FORM_WEIGHT * (ppg - FORM_NEUTRAL_PPG)


@dataclass(frozen=True)
class Verdict:
    text: str
    favorite: str | None                # "home" / "away" / None (dengeli)
    clear: bool                         # acik favori mi


def match_verdict(
    home_name: str,
    away_name: str,
    home_strength: float,
    away_strength: float,
    home_advantage: float = 1.0,
    home_form: str = "",
    away_form: str = "",
) -> Verdict:
    """
    Kagit ustu yorum. home_advantage: motorun ic saha carpani (tarafsiz sahada 1.0).
    Metin sayi icermez; ev sahibi yalnizca ic saha avantajiyla one geciyorsa bu belirtilir.
    """
    if home_strength <= 0 or away_strength <= 0:
        return Verdict("Kağıt üzerinde değerlendirme için yeterli bilgi yok.", None, False)
    home_base = home_strength * form_factor(home_form)
    away_score = away_strength * form_factor(away_form)
    ratio = home_base * max(home_advantage, 0.0) / away_score
    edge = max(ratio, 1.0 / ratio) - 1.0 if ratio > 0 else float("inf")
    if edge < BALANCED_EDGE:
        return Verdict("Kağıt üzerinde dengeli bir maç: iki takım da kazanabilir.", None, False)
    favorite = "home" if ratio > 1.0 else "away"
    clear = edge >= CLEAR_FAVORITE_EDGE
    name = home_name if favorite == "home" else away_name
    text = f"Kağıt üzerinde {'açık favori' if clear else 'favori'}: {name}"
    if home_advantage > 1.0:
        if favorite == "home" and home_base / away_score < 1.0 + BALANCED_EDGE:
            text += " (iç saha avantajıyla)"
        elif favorite == "away":
            text += " (deplasmanda olmasına rağmen)"
    return Verdict(text, favorite, clear)


# ===========================================================================
# 5) GOZLEMCI RAPORU
# ===========================================================================

def scout_accuracy(judging_ability: int | None) -> float:
    """En iyi gozlemcinin yetenek degerlendirmesi (1-20) -> isabet (0.35-0.95)."""
    if judging_ability is None:
        return NO_SCOUT_ACCURACY
    value = SCOUT_ACCURACY_BASE + SCOUT_ACCURACY_PER_POINT * judging_ability
    return round(max(SCOUT_ACCURACY_BASE, min(MAX_ACCURACY, value)), 4)


def confidence_label(accuracy: float) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if accuracy >= threshold:
            return label
    return LOW_CONFIDENCE


def swap_chance(accuracy: float) -> float:
    return max(0.0, 1.0 - accuracy) * SWAP_SCALE


def formation_miss_chance(accuracy: float) -> float:
    return max(0.0, 1.0 - accuracy) * FORMATION_MISS_SCALE


def scout_seed(rng_seed: int, fixture_id: int, week: int, viewer_team_id: int) -> int:
    """Rapor tohumu: ayni tohum + fikstur + hafta + izleyen -> ayni rapor (crc32: surumler arasi sabit)."""
    return zlib.crc32(f"scout|{rng_seed}|{fixture_id}|{week}|{viewer_team_id}".encode())


@dataclass(frozen=True)
class LineupSlot:
    player_id: int
    position: str                       # oyuncunun dogal mevkisi
    role: str                           # oynayacagi slot


@dataclass(frozen=True)
class Candidate:
    """Ilk 11 disindaki uygun oyuncu (alternatif). Liste secim gucu sirasiyla verilir."""
    player_id: int
    position: str


def predict_formation(
    rng: random.Random, actual: str, options: Sequence[str], accuracy: float
) -> str:
    """Gercek dizilis ya da (isabete gore) baska bir secenek. Her zaman 2 zar atar."""
    miss_draw, pick_draw = rng.random(), rng.random()
    others = [o for o in options if o != actual]
    if others and miss_draw < formation_miss_chance(accuracy):
        return others[int(pick_draw * len(others))]
    return actual


def scramble_lineup(
    rng: random.Random,
    starters: Sequence[LineupSlot],
    alternatives: Sequence[Candidate],
    accuracy: float,
) -> list[LineupSlot]:
    """
    Beklenen ilk 11'i gozlemci isabetine gore bozar. Her slot icin 2 zar (sonuctan bagimsiz).
    Alternatif: once slotun mevkisinden, yoksa herhangi bir saha oyuncusu; kaleci yalnizca kaleci.
    Secim gucluye yatkindir (pick^2): 'makul' alternatifler, rastgele yedek degil.
    """
    chance = swap_chance(accuracy)
    pool = list(alternatives)
    predicted: list[LineupSlot] = []
    for slot in starters:
        swap_draw, pick_draw = rng.random(), rng.random()
        if swap_draw < chance:
            options = [c for c in pool if c.position == slot.role]
            if not options and slot.role != GK:
                options = [c for c in pool if c.position != GK]
            if options:
                alt = options[min(len(options) - 1, int(pick_draw * pick_draw * len(options)))]
                pool.remove(alt)
                predicted.append(LineupSlot(alt.player_id, alt.position, slot.role))
                continue
        predicted.append(slot)
    return predicted


@dataclass(frozen=True)
class ScoutedPlayer:
    player_id: int
    name: str
    position: str                       # dogal mevki
    role: str                           # tahmin edilen slot
    age: int
    stars: str                          # gozlemci sisiyle yildiz (araligi)

    @property
    def out_of_position(self) -> bool:
        return self.position != self.role


def scout_notes(has_scout: bool, confidence: str, absentees: int, formation: str) -> tuple[str, ...]:
    notes: list[str] = []
    if not has_scout:
        notes.append("Kulübünde gözlemci yok: tahminler düşük güvenilirlikte. "
                     "Teknik heyete bir gözlemci eklemeyi düşün.")
    elif confidence == LOW_CONFIDENCE:
        notes.append("Gözlemcinin değerlendirmesi sınırlı: ilk 11 ve diziliş tahmini yanıltıcı olabilir.")
    notes.append(f"Beklenen diziliş: {formation}.")
    if absentees:
        notes.append(f"Rakipte {absentees} oyuncu sakatlık ya da ceza nedeniyle forma giyemeyecek.")
    return tuple(notes)


@dataclass(frozen=True)
class ScoutReport:
    fixture_id: int
    week: int                           # raporun yazildigi oyun haftasi (tohum)
    viewer_team_id: int
    opponent_team_id: int
    opponent_name: str
    opponent_is_home: bool
    scout_name: str | None
    accuracy: float                     # 0-1 (gozlemci isabeti; oyuncu gucu degil)
    confidence: str                     # Düşük / Orta / Yüksek
    predicted_formation: str
    predicted_xi: tuple[ScoutedPlayer, ...]
    absentees: tuple[AbsentPlayer, ...]
    players_to_watch: tuple[WatchPlayer, ...]
    tendencies: TeamTendencies
    notes: tuple[str, ...]
    disclaimer: str = SCOUT_DISCLAIMER


# ===========================================================================
# 6) MAC ONU
# ===========================================================================

@dataclass(frozen=True)
class TeamPreview:
    team_id: int
    name: str
    is_home: bool
    is_viewer: bool
    league_name: str | None
    league_position: int | None
    league_size: int
    points: int
    played: int
    formation: str
    team_stars: str                     # beklenen ilk 11'in yildizi
    form: str                           # son 5 mac 'GBM', kronolojik
    form_goals_for: int
    form_goals_against: int
    recent_matches: tuple[RecentMatch, ...]
    home_record: Record
    away_record: Record
    injured: tuple[AbsentPlayer, ...]
    suspended: tuple[AbsentPlayer, ...]
    players_to_watch: tuple[WatchPlayer, ...]
    tendencies: TeamTendencies


@dataclass(frozen=True)
class MatchPreview:
    fixture_id: int
    season: int
    week: int
    competition: str                    # LEAGUE / CUP
    competition_label: str
    title: str
    neutral_venue: bool
    viewer_team_id: int | None
    home: TeamPreview
    away: TeamPreview
    head_to_head: HeadToHead            # team_a = ev sahibi
    verdict: str
    favorite_team_id: int | None

    @property
    def viewer(self) -> TeamPreview | None:
        return next((t for t in (self.home, self.away) if t.is_viewer), None)

    @property
    def opponent(self) -> TeamPreview | None:
        if self.viewer is None:
            return None
        return self.away if self.home.is_viewer else self.home
