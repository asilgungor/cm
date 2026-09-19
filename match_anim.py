"""
match_anim.py
=============
Faz 14T -- CANLI MAC 2D CANLANDIRMA (K-S17, K8'in revizyonu). SAF MANTIK: Streamlit, veritabani ve tarayici BILMEZ.

    frame_script(result, frames, i, prev=None)   gosterilen kare -> tarayiciya giden kucuk "betik" (JSON uyumlu dict)
    component_payload(script, mode, factor, ...) bilesenin (web_assets/match_pitch.js) veri paketi
    standalone_html(result, ...)                 sahibe gosterilecek tek dosyalik ornek (motorla oynanmis gercek mac)

OLAY GUDUMLU CANLANDIRMA
    Motor top ya da oyuncu konumu TUTMAZ (bolge modeli 17A'da gelir). Bu modul her gosterilen karenin olayini ve ayni
    atak zincirindeki (chain_id) gizli kurulus halkalarini sirayla "vurus"lara (beat) cevirir; her vurus GERCEK olaya
    ve olaydaki GERCEK oyunculara baglidir:
        BUILD_UP win/entry/final/pressure  topu kazanan / tasiyan / son pasi veren oyuncu (zincirin sonraki halkasina pas)
        GOAL / SAVE / MISS                 sutcu (olayin oyuncusu), savunan takimin O ANKI kalecisi (_on_pitch)
        detail "corner"                    korneri kullanan (motorun kurali: belirlenmis atici sahadaysa o, yoksa sahadaki
                                           en iyi korner becerisi -- MatchEngine._designated_or_best ile ayni secim) +
                                           kafayi vuran sutcu; CORNER (kazanildi) olayinda orta savunmadan doner
        detail "free_kick" / "penalty"     atici olayin oyuncusu; baraj / bos ceza sahasi
        FOUL / kart / sakatlik             olayin oyuncusu (fauli yapan / kart goren / sakatlanan); faule ugrayan motor
                                           kaydinda YOK -> en yakin rakip temsili olarak secilir, vurgulanmaz, adlandirilmaz
        OFFSIDE                            ofsayta dusen oyuncu, yan cizgide bayrak
        SUBSTITUTION                       giren (olayin oyuncusu) ve cikan (_tracks: ayni olayda sahadan ayrilan)
        KICK_OFF / devre / mac sonu        sekil sifirlanir; devre arasinda yon degisir (tek seferlik gecis)
    Konum ve yollar TEMSILIDIR ama olayla ASLA celismez: ISKA kaleye girmez (auta ya da ustten gider, direkten doner),
    KURTARIS gercek kalecide biter, GOL gercek sutcunun ayagindan aga gider; top hic bir zaman yanlis takimda gorunmez
    (sahiplik degisimi hep "serbest top" ya da tackle ile olur). Olaylar arasinda iki takim dizilisinde, topun kimde
    oldugu ve evreye gore kayar (hucum eden one cikar, savunan geriler); tarayici ayrica hafif bir "nefes" hareketi ekler.

    Sut bicimi (kafa / uzaktan / karsi karsiya / yakin / vole) ve iska / kurtaris turu (direk, ust direk, ustten, baraj,
    kornere celme) YALNIZCA olayin kendi cumlesinden okunur: motorun sakladigi sans kalitesi (chance_quality) ve
    kalite etiketleri (big / good / normal / far) OKUNMAZ (K12: olaylarin soylediginden fazlasi gosterilmez).

DETERMINIZM
    Butun sunum rastgeleligi random.Random(zlib.crc32("tohum|ev id|dep id|olay indeksi|tuz")) ureticilerinden gelir.
    Motor RNG'sine dokunulmaz, Python hash() kullanilmaz: ayni mac her surecte ayni betikleri verir (test).

ZAMAN
    Betik zamanlari ms'dir ve "Normal" hizda karenin dwell_ms'inin FIT (%92) kadarina sigdirilir. Tarayici zamani hiz
    carpaniyla (Yavaş 1,4 / Hızlı 0,5) olcekler; "Anında" ve geri sarmada son poza atlar, DURDUR'da donar.

BETIK SEMASI (v1; sayilar yuvarlanir, metin yalnizca ad / etiket)
    v, f (olay indeksi), pf (onceki gosterilen olay, yoksa -1), T (toplam ms), dw (dwell), dir (1: ev saga hucum),
    snap (1: baslangic pozuna atla), pl [[id, taraf 0/1, kisa ad, kaleci 0/1, kondisyon kovasi 0-3, sari 0-2,
    durum 0 / 1 giren / 2 cikan]], p0 / p1 (baslangic / bitis konumlari, duz [x, y, ...]), mv [[i, t0, t1, x, y(, cx, cy)]],
    b0 / b1 [x, y, sahip], bl [[t0, t1, x, y, h, sahip, profil 0 yay / 1 yukselen]], fx [[t0, sure, kod, i, x, y]],
    hl [vurgulu oyuncu indeksleri], k (kare turu), cap (alt yazi), min, ph, sc, pen.
    Kondisyon yalnizca KOVA (yesil / sari / kirmizi) olarak gider; guc, not, ozellik, olasilik YOK.
"""

from __future__ import annotations

import json
import math
import random
import zlib
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

import match_feed
import pitch
import team_roles
from match_engine import EventType, MatchResult
from match_feed import Frame, build_timeline
from models import Position

# ===========================================================================
# [1] SABITLER
# ===========================================================================

SCRIPT_VERSION = 1
L, W = pitch.PITCH_LENGTH, pitch.PITCH_WIDTH
CX, CY = L / 2, W / 2
POST_LOW, POST_HIGH = pitch.GOAL_TOP, pitch.GOAL_BOTTOM          # 30.34 / 37.66 (kale agzi y araligi)
NET = pitch.GOAL_DEPTH                                           # ag derinligi (kale cizgisinin arkasi)
CROSSBAR = 2.44
SPOT_D = L - pitch.PENALTY_SPOT                                  # hucum cercevesinde penalti noktasi derinligi
BOX_D = L - pitch.PENALTY_DEPTH                                  # ceza sahasi cizgisi (88.5)
BOX_LO, BOX_HI = (W - pitch.PENALTY_WIDTH) / 2, (W + pitch.PENALTY_WIDTH) / 2
WALL_DIST = 9.15
FIT = 0.92                     # animasyon karenin bekleme suresinin en cok bu kadarini kullanir
SEP = 3.0                      # dizilis hedeflerinde iki oyuncu arasi en az mesafe
BENCH_Y = W + 1.6              # yedek kulubesi / tunel (ust yan cizginin disi, orta cizgi hizasi)

SIDES = ("home", "away")
SIDE_CODE = {"home": 0, "away": 1}
ENERGY_CODE = {"none": 0, "green": 1, "yellow": 2, "red": 3}
SHOT_TYPES = frozenset({EventType.GOAL, EventType.SAVE, EventType.MISS})
WHISTLES = frozenset({EventType.HALF_TIME, EventType.FULL_TIME, EventType.EXTRA_TIME_START, EventType.EXTRA_TIME_HALF})
NEUTRAL: frozenset[EventType] = frozenset()   # (14T) artik her olayin bitis durumu yalnizca kendisine bagli

PERSISTENT_FX = frozenset({"yc", "rc", "inj", "fall"})      # kare sonunda da gorunur kalanlar (kart, sakatlik)
RESPOT = 2                     # top ucus profili: 0 yay, 1 yukselen (ustten), 2 yeniden yerlestirme (oyun disi: gorunmez)
# Efekt kodlari (tarayici bunlari simgeye cevirir)
FX_CODES = ("wh", "yc", "rc", "inj", "fl", "net", "sub_in", "sub_out", "dive", "fall", "cel", "tac", "hdr",
            "post", "save", "duel", "wall")

# ---------------------------------------------------------------------------
# Metin ipuclari: YALNIZCA olayin kendi cumlesi (K12 -- kalite etiketi okunmaz)
# ---------------------------------------------------------------------------
_T_DRIBBLE = ("çalım", "rakibini geçti", "eksiltti", "geride bıraktı", "savunmayı aştı", "yakalayamadı",
              "geride kaldı", "iki kişiyi geçti", "içinden geçti", "savunmayı açtı", "savunmayı kırdı")
_T_DUEL = ("ikili mücadele", "mücadele")
_T_INTERCEPT = ("pas", "araya gir", "kesti", "kaptı", "yakaladı")
_T_LONG = ("uzaktan", "metreden", "ceza sahası dışından", "ceza yayı", "uzun mesafe")
_T_CLOSE = ("altı pas", "yakın mesafe", "çizgi üzerinde", "boş kale", "dokunması yeterdi", "karambol", "çizgide")
_T_ONE = ("karşı karşıya", "tek başına", "üzerine çıkıp", "aşırt", "kaleciyi geçti", "kaleciyi çalım")
_T_HEADER = ("kafa",)
_T_VOLLEY = ("vole",)
_T_CROSS = ("orta", "kafa", "havadan", "kanattan")
_T_BAR = ("üst direğe", "üst direkten", "üst direk")
_T_POST = ("direğe vurdu", "direkten", "direğe çarp", "direğe gönderdi", "direkten döndü")
_T_OVER = ("üstten", "üstünden", "tribün", "havaya", "havalan", "taraftarın kucağında", "havada kaldı")
_T_WALL = ("baraj", "duvara")
_T_BLOCK = ("savunmadan döndü", "savunmaya çarp", "yön değiştir", "ayağını uzattı")
_T_PARRY = ("kornere", "çeld", "çeli", "yumrukla", "tokatla", "çıkardı", "parmak", "dokundu", "uzaklaştır")
_T_GK_BLOCK = ("ayaklarıyla", "vücuduyla", "üzerine çıkıp", "kapattı")
_T_DISSENT = ("itiraz", "tepki", "geciktir")


def _norm(text: str | None) -> str:
    """Turkce kucuk harf (I -> ı, İ -> i) -- casefold birlesik nokta uretmesin."""
    return (text or "").replace("İ", "i").replace("I", "ı").lower()


def _has(text: str, words: Sequence[str]) -> bool:
    return any(w in text for w in words)


def shot_form(description: str | None, detail: str | None = None) -> str:
    """Sutun bicimi -- olayin cumlesinden (motorun kalite etiketinden DEGIL)."""
    if detail == "penalty":
        return "penalty"
    if detail == "free_kick":
        return "free_kick"
    text = _norm(description)
    if detail == "corner":
        return "header" if _has(text, _T_HEADER) or "seken" not in text else "box"
    if _has(text, _T_HEADER):
        return "header"
    if _has(text, _T_ONE):
        return "oneonone"
    if _has(text, _T_CLOSE):
        return "close"
    if _has(text, _T_LONG):
        return "long"
    if _has(text, _T_VOLLEY):
        return "volley"
    if _has(text, _T_DRIBBLE):
        return "solo"
    return "box"


def miss_kind(description: str | None) -> str:
    """ISKA turu: 'bar' (ust direk) / 'post' (direk) / 'wall' (baraj) / 'block' (savunma) / 'over' (ustten) / 'wide'."""
    text = _norm(description)
    if _has(text, _T_BAR):
        return "bar"
    if _has(text, _T_POST) and "yanından" not in text and "dibinden" not in text and "dışına" not in text:
        return "post"
    if _has(text, _T_WALL):
        return "wall"
    if _has(text, _T_BLOCK):
        return "block"
    if _has(text, _T_OVER) or "üst" in text:
        return "over"
    return "wide"


def save_kind(description: str | None) -> str:
    """KURTARIS turu: 'block' (kaleci cikip kapatir) / 'parry' (celer, top oyundan cikar) / 'catch' (tutar)."""
    text = _norm(description)
    if _has(text, _T_GK_BLOCK):
        return "block"
    if _has(text, _T_PARRY):
        return "parry"
    return "catch"


# ===========================================================================
# [2] MAC BAGLAMI (izler, yonler, tohum) -- mac basina bir kez kurulur
# ===========================================================================

@dataclass(frozen=True)
class Actor:
    pid: int
    side: str
    name: str
    gk: bool
    role: Position
    energy: int
    yellow: int
    status: int = 0             # 0 sahada / 1 giren / 2 cikan (kare sonunda sahadan ayrilir)


class MatchCtx:
    """Bir MatchResult icin saf baglam: kim sahada (pitch._tracks), her olayin evresi ve yonu, sunum tohumu."""

    def __init__(self, result: MatchResult):
        self.result = result
        self.events = result.events
        self.tracks = pitch._tracks(result)
        self.frames = build_timeline(result, include_hidden=True)        # olay basina bir kare (indeks hizali)
        self.seed_key = f"{result.seed}|{result.home.id}|{result.away.id}"
        self.teams = {"home": result.home, "away": result.away}
        self.names = {p.id: p.name for team in (result.home, result.away) for p in team.players}
        self._rows: dict[tuple[str, int], list] = {}
        self._rest: dict[int, tuple] = {}
        self._yellow = self._yellow_counts()

    # --- temel sorgular ---
    def rng(self, index: int, salt: str) -> random.Random:
        return random.Random(zlib.crc32(f"{self.seed_key}|{index}|{salt}".encode()))

    def rows(self, side: str, index: int):
        key = (side, index)
        if key not in self._rows:
            self._rows[key] = pitch._on_pitch(self.tracks, side, max(index, 0))
        return self._rows[key]

    def home_right(self, index: int) -> bool:
        if index < 0 or not self.frames:
            return True
        return pitch.home_attacks_right(self.frames[min(index, len(self.frames) - 1)])

    def right(self, side: str, index: int) -> bool:
        """side takimi bu olayda saga mi hucum ediyor? Seri penaltilarda iki takim da ayni (sag) kaleye atar."""
        if 0 <= index < len(self.frames) and self.frames[index].phase == match_feed.PHASE_SHOOTOUT:
            return pitch.SHOOTOUT_GOAL_RIGHT
        home = self.home_right(index)
        return home if side == "home" else not home

    def side_of(self, index: int) -> str | None:
        return self.frames[index].event.side if 0 <= index < len(self.frames) else None

    def _yellow_counts(self) -> list[dict[int, int]]:
        out, seen = [], {}
        for ev in self.events:
            if ev.type is EventType.YELLOW_CARD and ev.player_id is not None:
                seen = {**seen, ev.player_id: seen.get(ev.player_id, 0) + 1}
            out.append(seen)
        return out

    def yellows_before(self, index: int) -> dict[int, int]:
        return self._yellow[index - 1] if 0 < index <= len(self._yellow) else {}

    def minute(self, index: int) -> int:
        return self.events[index].minute if 0 <= index < len(self.events) else 0

    def chain(self, index: int) -> list[int]:
        """index'teki olayin zincirindeki (chain_id) olaylar, index DAHIL, kronolojik."""
        ev = self.events[index]
        cid = ev.chain_id
        if cid is None:
            return [index]
        out = [index]
        j = index - 1
        while j >= 0 and self.events[j].chain_id == cid:
            out.insert(0, j)
            j -= 1
        return out

    def chain_next(self, index: int) -> int | None:
        cid = self.events[index].chain_id
        j = index + 1
        if cid is not None and j < len(self.events) and self.events[j].chain_id == cid:
            return j
        return None

    def kicking_side(self, index: int) -> str:
        """Santrayi kim yapar (motor kaydetmez; temsili): 1. yari / 1. uzatma ev, digerleri deplasman."""
        phase = self.frames[index].phase if 0 <= index < len(self.frames) else match_feed.PHASE_FIRST_HALF
        return "away" if phase in (match_feed.PHASE_SECOND_HALF, match_feed.PHASE_ET_SECOND) else "home"


# ===========================================================================
# [3] ZAMAN CIZELGESI KURUCU
# ===========================================================================

def _clampx(x: float) -> float:
    return max(0.6, min(L - 0.6, x))


def _clampy(y: float) -> float:
    return max(0.6, min(W - 0.6, y))


def _ease(u: float) -> float:
    return u * u * (3 - 2 * u)


class Timeline:
    """
    Oyuncu hareketleri, top ucuslari ve efektler (ms). Hareket MUTLAK hedeftir; baslangici bir onceki hareketin
    bittigi yer (ilk hareket: baslangic pozu). Ayni oyuncuya cakisan yeni hareket eskisini o anda keser. Top ucusu
    bir onceki ucusun bittigi yerden (ya da sahibinin o anki konumundan) baslar. Tarayici ayni kuralla oynatir.
    """

    def __init__(self, start: dict[int, tuple[float, float]], ball: tuple[float, float, int]):
        self.start = dict(start)
        self.segs: dict[int, list[list]] = {i: [] for i in start}
        self.ball0 = ball
        self.ball: list[list] = []
        self.fx: list[list] = []
        self.hl: set[int] = set()
        self.keys: list[tuple[float, float]] = []      # "asil an" araliklari: sikistirmada hizini korur

    def key(self, t0: float, t1: float) -> None:
        if t1 > t0:
            self.keys.append((max(0.0, t0), t1))

    # --- oyuncular ---
    def _seg_start(self, i: int, k: int) -> tuple[float, float]:
        if k == 0:
            return self.start[i]
        s = self.segs[i][k - 1]
        return s[2], s[3]

    @staticmethod
    def _interp(a, b, ctrl, u):
        if ctrl is None:
            return a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
        v = 1 - u
        return (v * v * a[0] + 2 * v * u * ctrl[0] + u * u * b[0],
                v * v * a[1] + 2 * v * u * ctrl[1] + u * u * b[1])

    def pos_at(self, i: int, t: float) -> tuple[float, float]:
        x, y = self.start[i]
        for k, (t0, t1, tx, ty, ctrl) in enumerate(self.segs[i]):
            if t <= t0:
                return x, y
            if t >= t1:
                x, y = tx, ty
                continue
            return self._interp(self._seg_start(i, k), (tx, ty), ctrl, _ease((t - t0) / (t1 - t0)))
        return x, y

    def end_pos(self, i: int) -> tuple[float, float]:
        segs = self.segs[i]
        return (segs[-1][2], segs[-1][3]) if segs else self.start[i]

    def move(self, i: int, x: float, y: float, t0: float, t1: float, ctrl=None) -> None:
        x, y = _clampx(x), _clampy(y)
        segs = self.segs[i]
        while segs and segs[-1][1] > t0:
            last = segs[-1]
            if last[0] >= t0:
                segs.pop()
                continue
            px, py = self.pos_at(i, t0)
            segs[-1] = [last[0], t0, px, py, None]
            break
        if ctrl is not None:
            ctrl = (_clampx(ctrl[0]), _clampy(ctrl[1]))
        segs.append([t0, max(t1, t0 + 1), x, y, ctrl])

    # --- top ---
    def ball_state(self) -> tuple[float, float, int, float]:
        """(x, y, sahip, son ucusun bitisi). Sahip varsa konum sahibin son hedefidir."""
        if self.ball:
            t0, t1, x, y, h, holder, prof = self.ball[-1]
            return x, y, holder, t1
        x, y, holder = self.ball0
        return x, y, holder, 0.0

    def holder(self) -> int:
        return self.ball_state()[2]

    def ball_at(self, t: float) -> tuple[float, float]:
        """Topun t anindaki (yer) konumu -- ucus baslangici icin."""
        x, y, holder = self.ball0
        for t0, t1, bx, by, _h, hold, _p in self.ball:
            if t < t0:
                break
            if t >= t1:
                x, y, holder = bx, by, hold
                continue
            u = (t - t0) / (t1 - t0)
            sx, sy = self._ball_from(t0)
            return sx + (bx - sx) * u, sy + (by - sy) * u
        if holder >= 0:
            return self.pos_at(holder, t)
        return x, y

    def _ball_from(self, t0: float) -> tuple[float, float]:
        x, y, holder = self.ball0
        for s0, _s1, bx, by, _h, hold, _p in self.ball:
            if s0 >= t0:
                break
            x, y, holder = bx, by, hold
        return self.pos_at(holder, t0) if holder >= 0 else (x, y)

    def fly(self, x: float, y: float, t0: float, t1: float, h: float = 0.0, holder: int = -1,
            prof: int = 0, clamp: bool = True) -> None:
        last_end = self.ball[-1][1] if self.ball else 0.0
        t0 = max(t0, last_end)
        t1 = max(t1, t0 + 1)
        if clamp:
            x, y = _clampx(x), _clampy(y)
        self.ball.append([t0, t1, x, y, h, holder, prof])

    def pass_to(self, j: int, t0: float, dur: float, h: float = 0.0) -> float:
        """Topu j'ye gonderir (hedef: j'nin varis anindaki konumu). Donus: varis ani."""
        last_end = self.ball[-1][1] if self.ball else 0.0
        t0 = max(t0, last_end)
        tx, ty = self.pos_at(j, t0 + dur)
        self.fly(tx, ty, t0, t0 + dur, h, j)
        return t0 + dur

    # --- efektler ---
    def effect(self, code: str, t0: float, dur: float, i: int = -1, x: float | None = None,
               y: float | None = None) -> None:
        if x is None or y is None:
            x, y = self.pos_at(i, t0) if i >= 0 else (CX, CY)
        self.fx.append([t0, dur, code, i, x, y])

    def last_time(self) -> float:
        times = [s[1] for segs in self.segs.values() for s in segs]
        times += [b[1] for b in self.ball]
        times += [f[0] + f[1] for f in self.fx]
        return max(times, default=0.0)


# ===========================================================================
# [4] SAHNE: bir karenin (vurus dizisinin) koreografisi
# ===========================================================================

class Scene:
    """Tek karenin koreografisi. Hucum cercevesi: (derinlik, yanal) -> saha, olayin takiminin yonune gore."""

    def __init__(self, ctx: MatchCtx, index: int, actors: list[Actor]):
        self.ctx = ctx
        self.index = index
        self.actors = actors
        self.by_pid = {a.pid: i for i, a in enumerate(actors)}
        self.tl: Timeline | None = None
        self.t = 0.0
        self.tight = False          # dar butce (rutin kare): kurulum ve sonrasi kisa, yalnizca asil an

    # --- cerceve donusumu ---
    def P(self, side: str, d: float, lat: float, index: int | None = None) -> tuple[float, float]:
        """side takiminin hucum cercevesinde (kendi kalesinden derinlik, yanal) -> saha koordinati."""
        if self.ctx.right(side, self.index if index is None else index):
            return d, lat
        return L - d, W - lat

    def D(self, side: str, x: float, y: float) -> tuple[float, float]:
        """Saha -> side takiminin hucum cercevesi."""
        if self.ctx.right(side, self.index):
            return x, y
        return L - x, W - y

    @staticmethod
    def other(side: str) -> str:
        return "away" if side == "home" else "home"

    def idx(self, pid: int | None) -> int | None:
        return self.by_pid.get(pid) if pid is not None else None

    def side_actors(self, side: str, outfield: bool = False, leaving: bool = False) -> list[int]:
        return [i for i, a in enumerate(self.actors) if a.side == side and (not outfield or not a.gk)
                and (leaving or a.status != 2)]

    def keeper(self, side: str) -> int | None:
        return next((i for i, a in enumerate(self.actors) if a.side == side and a.gk and a.status != 2), None)

    def nearest(self, pool: Sequence[int], x: float, y: float, t: float, exclude: Sequence[int] = ()) -> int | None:
        best, best_d = None, 1e9
        for i in pool:
            if i in exclude:
                continue
            px, py = self.tl.pos_at(i, t)
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_d:
                best, best_d = i, d
        return best

    # --- dizilis ---
    def formation(self, index: int, poss: str | None, ball_d: float, ball_l: float, style: str = "play",
                  rng: random.Random | None = None, leaving: bool = False) -> dict[int, tuple[float, float]]:
        """
        Iki takimin dizilis hedefleri (saha koordinati). poss: topa sahip taraf (None = olu top); ball_d / ball_l topun
        SAHIP tarafin hucum cercevesindeki derinligi / yanali. Hat derinligi rol + topun yerine gore kayar.
        """
        rng = rng or self.ctx.rng(index, f"shape|{style}")
        out: dict[int, tuple[float, float]] = {}
        for side in SIDES:
            members = self.side_actors(side, leaving=leaving)
            has_ball = poss == side
            if poss is None:
                bd, bl = 52.5, 34.0
            elif has_ball:
                bd, bl = ball_d, ball_l
            else:
                bd, bl = L - ball_d, W - ball_l                      # rakibin topu: kendi cercevemde
            lines: dict[Position, list[int]] = {}
            for i in members:
                lines.setdefault(self.actors[i].role if not self.actors[i].gk else Position.GK, []).append(i)
            for role, group in lines.items():
                n = len(group)
                depth = self._line_depth(role, has_ball, poss is None, bd, style)
                margin = 6.5 if has_ball else 11.0
                if style == "kickoff":
                    margin = 8.0
                for k, i in enumerate(group):
                    lat = margin + (W - 2 * margin) * (k + 0.5) / n if role is not Position.GK else 34.0
                    pull = 0.12 if has_ball else 0.32
                    if style not in ("kickoff", "break"):
                        lat += (bl - lat) * pull
                    d = depth + rng.uniform(-1.4, 1.4) if role is not Position.GK else depth
                    lat += rng.uniform(-1.2, 1.2) if role is not Position.GK else (bl - 34.0) * 0.15
                    if style == "kickoff":
                        d = min(d, CX - 1.2)
                    out[i] = self.P(side, d, lat, index)
        self._separate(out)
        return out

    @staticmethod
    def _line_depth(role: Position, has_ball: bool, dead: bool, bd: float, style: str) -> float:
        if style == "kickoff":
            return {Position.GK: 4.0, Position.DEF: 17.0, Position.MID: 33.0, Position.FWD: 46.0}[role]
        if style == "break":
            return {Position.GK: 6.0, Position.DEF: 22.0, Position.MID: 36.0, Position.FWD: 46.0}[role]
        if dead:
            return {Position.GK: 5.0, Position.DEF: 24.0, Position.MID: 40.0, Position.FWD: 52.0}[role]
        if has_ball:
            de = max(14.0, min(55.0, bd - 30.0))
            table = {Position.DEF: de, Position.MID: max(26.0, min(76.0, bd - 10.0)),
                     Position.FWD: max(42.0, min(95.0, bd + 7.0)), Position.GK: max(4.0, min(20.0, de - 20.0))}
            return table[role]
        de = max(8.0, min(38.0, bd * 0.55))
        table = {Position.DEF: de, Position.MID: max(19.0, min(55.0, de + 13.0)),
                 Position.FWD: max(32.0, min(66.0, de + 28.0)), Position.GK: max(2.5, min(9.0, de * 0.25))}
        return table[role]

    @staticmethod
    def _separate(points: dict[int, tuple[float, float]], fixed: Sequence[int] = (), rounds: int = 5) -> None:
        keys = sorted(points)
        for _ in range(rounds):
            moved = False
            for a_i, a in enumerate(keys):
                for b in keys[a_i + 1:]:
                    ax, ay = points[a]
                    bx, by = points[b]
                    dx, dy = bx - ax, by - ay
                    dist = math.hypot(dx, dy)
                    if dist >= SEP:
                        continue
                    if dist < 1e-6:
                        dx, dy, dist = 0.0, 1.0, 1.0
                    push = (SEP - dist) / 2
                    ux, uy = dx / dist, dy / dist
                    if a not in fixed:
                        points[a] = (_clampx(ax - ux * push), _clampy(ay - uy * push))
                    if b not in fixed:
                        points[b] = (_clampx(bx + ux * push), _clampy(by + uy * push))
                    moved = True
            if not moved:
                return

    def shift(self, targets: dict[int, tuple[float, float]], t0: float, t1: float, skip: Sequence[int] = ()) -> None:
        for i, (x, y) in targets.items():
            if i not in skip:
                self.tl.move(i, x, y, t0, t1)

    # --- top sahipligi ---
    def gain(self, side: str, target: int, t0: float) -> float:
        """
        Topu `side` takiminin `target` oyuncusuna getirir. Top hic bir zaman yanlis takimda gorunmez: rakipteyse
        once serbest top / mucadele, kalecideyse kaleci uzun oynar ve target ikinci topu kazanir. Donus: bitis ani.
        """
        tl = self.tl
        holder = tl.holder()
        t0 = max(t0, tl.ball_state()[3])
        if holder == target:
            return t0
        if holder >= 0 and self.actors[holder].side == side:
            return tl.pass_to(target, t0, 380, h=0.0)
        if holder >= 0 and self.actors[holder].gk:
            # kaleci uzun oynar (kendi takimina), target mucadeleyi kazanir
            mates = [i for i in self.side_actors(self.actors[holder].side, outfield=True)]
            tx, ty = tl.pos_at(target, t0 + 500)
            receiver = self.nearest(mates, tx, ty, t0 + 500)
            if receiver is not None:
                t0 = tl.pass_to(receiver, t0, 520, h=7.0)
                rx, ry = tl.pos_at(receiver, t0)
                tl.move(target, rx + 0.8, ry, t0 - 300, t0 + 60)
                tl.effect("duel", t0, 350, receiver)
                return tl.pass_to(target, t0 + 60, 140)
            return tl.pass_to(target, t0, 600, h=6.0)
        if holder >= 0:
            # rakip oyuncu: target ustune gider, mucadeleyi kazanir
            hx, hy = tl.pos_at(holder, t0 + 280)
            tl.move(target, hx, hy + 0.9, t0, t0 + 280)
            tl.effect("duel", t0 + 200, 350, holder)
            return tl.pass_to(target, t0 + 280, 140)
        # serbest top
        return tl.pass_to(target, t0, 360, h=1.0)

    def attacker_outfield(self, side: str, t: float, prefer: Sequence[Position] = (Position.FWD, Position.MID),
                          exclude: Sequence[int] = ()) -> int | None:
        pool = [i for i in self.side_actors(side, outfield=True) if i not in exclude]
        for role in prefer:
            group = [i for i in pool if self.actors[i].role is role]
            if group:
                return max(group, key=lambda i: (self.D(side, *self.tl.pos_at(i, t))[0], -self.actors[i].pid))
        return pool[0] if pool else None


# ===========================================================================
# [5] VURUSLAR (olay turune gore koreografi)
# ===========================================================================

class Choreographer(Scene):
    """Scene + vurus fonksiyonlari. Her vurus TUM oyunculara mutlak hedef verir: karenin son pozu yalnizca son olaya
    baglidir (onceki karenin nereden basladigindan bagimsiz) -> ardisik kareler dikissiz baglanir."""

    def beat(self, e: int, last: bool) -> None:
        ev = self.ctx.events[e]
        et = ev.type
        if et is EventType.KICK_OFF:
            self.b_kickoff(e)
        elif et in WHISTLES:
            self.b_break(e)
        elif et is EventType.BUILD_UP:
            self.b_build(e)
        elif et in SHOT_TYPES:
            detail = ev.detail
            if detail == "corner":
                self.b_corner(e, shot=True)
            elif detail == "free_kick":
                self.b_free_kick(e)
            elif detail == "penalty":
                self.b_penalty(e)
            else:
                self.b_shot(e)
        elif et is EventType.CORNER:
            self.b_corner(e, shot=False)
        elif et is EventType.FOUL:
            self.b_foul(e, card=None)
        elif et is EventType.YELLOW_CARD:
            self.b_foul(e, card="yc")
        elif et is EventType.RED_CARD:
            self.b_foul(e, card="rc")
        elif et is EventType.OFFSIDE:
            self.b_offside(e)
        elif et is EventType.INJURY:
            self.b_injury(e)
        elif et is EventType.SUBSTITUTION:
            self.b_sub(e)
        elif et is EventType.TACTICAL_CHANGE:
            self.b_tactic(e)
        elif et is EventType.SHOOTOUT_START:
            self.b_shootout_setup(e)
        elif et is EventType.PENALTY_SHOOTOUT:
            self.b_shootout_kick(e)
        else:                                                   # bilinmeyen tur: yalnizca sekil (ileriye uyumlu)
            self.b_tactic(e)

    # ------------------------------------------------------------------ yardimcilar
    def _player(self, e: int) -> int | None:
        ev = self.ctx.events[e]
        i = self.idx(ev.player_id)
        if i is None and ev.player:
            i = next((k for k, a in enumerate(self.actors) if a.pid is not None and
                      self._full_name(k) == ev.player), None)
        return i

    def _full_name(self, i: int) -> str | None:
        return self.ctx.names.get(self.actors[i].pid)

    def named(self, e: int, side: str, exclude: Sequence[int] = ()) -> int | None:
        """Olay cumlesinde adi gecen `side` oyuncusu (orn. calimla gecilen savunmaci); yoksa None."""
        text = self.ctx.events[e].description or ""
        best = None
        for i in self.side_actors(side, leaving=True):
            if i in exclude:
                continue
            name = self._full_name(i)
            if not name:
                continue
            at = text.find(name)
            while at >= 0:
                end = at + len(name)
                if end >= len(text) or not text[end].isalnum():
                    if best is None or len(name) > len(self._full_name(best) or ""):
                        best = i
                    break
                at = text.find(name, at + 1)
        return best

    def setup(self, e: int, poss: str | None, ball_d: float, ball_l: float, t0: float, t1: float,
              style: str = "play", skip: Sequence[int] = ()) -> dict[int, tuple[float, float]]:
        targets = self.formation(e, poss, ball_d, ball_l, style)
        self.shift(targets, t0, t1, skip)
        return targets

    def highlight(self, *indices: int | None) -> None:
        for i in indices:
            if i is not None:
                self.tl.hl.add(i)

    # ------------------------------------------------------------------ santra / duduk
    def b_kickoff(self, e: int, restart: bool = False, side: str | None = None) -> None:
        tl, t = self.tl, self.t
        side = side if side in SIDES else self.ctx.kicking_side(e)
        targets = self.formation(e, None, 52.5, 34.0, "kickoff")
        taker = self.attacker_outfield(side, t, (Position.FWD, Position.MID))
        back = None
        if taker is not None:
            targets[taker] = self.P(side, CX - 0.6, CY)
            mids = [i for i in self.side_actors(side, outfield=True) if self.actors[i].role is Position.MID]
            back = self.nearest(mids, *self.P(side, CX - 12, CY + 4), t) if mids else None
        self.shift(targets, t, t + 500)
        tl.fly(CX, CY, t, t + 400, 0.0, -1, RESPOT)
        if not restart:
            tl.effect("wh", t + 150, 700, x=CX, y=CY - 5)
        if taker is not None:
            tl.fly(*tl.pos_at(taker, t + 520), t + 420, t + 520, 0.0, taker)
            if back is not None:
                tl.pass_to(back, t + 700, 320)
                tl.key(t + 650, t + 1020)
        self.t = t + (1100 if back is not None else 700)

    def b_break(self, e: int) -> None:
        tl, t = self.tl, self.t
        tl.effect("wh", t, 900, x=CX, y=CY - 5)
        tl.key(t, t + 500)
        targets = self.formation(e, None, 52.5, 34.0, "break")
        self.shift(targets, t + 150, t + 1300)
        tl.fly(CX, CY, t + 150, t + 700, 0.0, -1, RESPOT)
        self.t = t + 1300

    # ------------------------------------------------------------------ kurulus zinciri
    def b_build(self, e: int) -> None:
        ev = self.ctx.events[e]
        side = self.ctx.side_of(e) or "home"
        part = ev.detail or "entry"
        rng = self.ctx.rng(e, "build")
        tl, t = self.tl, self.t
        text = _norm(ev.description)
        p = self._player(e)
        if part == "pressure" or p is None:
            self._circulate(e, side, rng)
            return
        self.highlight(p)
        if part == "win":
            ball_d = rng.uniform(34, 50)
            ball_l = rng.uniform(14, 54)
            self.setup(e, side, ball_d, ball_l, t, t + 700, skip=(p,))
            spot = self.P(side, ball_d, ball_l)
            holder = tl.holder()
            if holder >= 0 and self.actors[holder].side != side and not self.actors[holder].gk:
                intercept = _has(text, _T_INTERCEPT) and not _has(text, _T_DUEL)
                if intercept:
                    # rakip pas denedi, p araya girdi (top rakipten serbest topa, oradan p'ye)
                    tl.move(p, spot[0], spot[1], t, t + 420)
                    tl.effect("duel", t + 360, 300, p)
                    tl.fly(spot[0], spot[1], t + 120, t + 420, 0.0, p)
                else:
                    hx, hy = tl.pos_at(holder, t + 300)
                    tl.move(p, hx + 0.8, hy + 0.6, t, t + 300)
                    tl.effect("duel", t + 240, 380, holder)
                    tl.fly(hx + 0.8, hy + 0.6, t + 300, t + 420, 0.0, p)
                    tl.move(holder, hx - 1.5, hy - 1.0, t + 300, t + 600)
                    tl.move(p, spot[0], spot[1], t + 420, t + 760)
            else:
                tl.move(p, spot[0], spot[1], t, t + 420)
                self.gain(side, p, t + 60)
            self.t = max(t + 760, tl.ball_state()[3])
            tl.key(t + 180, t + 560)
            return
        if part == "entry":
            ball_d = rng.uniform(72, 84)
            ball_l = rng.choice((rng.uniform(8, 18), rng.uniform(26, 42), rng.uniform(50, 60)))
            start_d, start_l = ball_d - rng.uniform(14, 20), ball_l + rng.uniform(-6, 6)
            self.setup(e, side, ball_d, ball_l, t, t + 900, skip=(p,))
            sx, sy = self.P(side, start_d, start_l)
            tl.move(p, sx, sy, t, t + 280)
            t1 = self.gain(side, p, t + 60)
            t1 = max(t1, t + 280)
            ex, ey = self.P(side, ball_d, ball_l)
            dribble = _has(text, _T_DRIBBLE)
            if dribble:
                d = self.named(e, self.other(side)) or self.nearest(
                    self.side_actors(self.other(side), outfield=True), (sx + ex) / 2, (sy + ey) / 2, t1)
                if d is not None:
                    mx, my = (sx + ex) / 2, (sy + ey) / 2
                    tl.move(d, mx, my, t1 - 100, t1 + 260)
                    # p rakibin etrafindan kavis cizer
                    nx, ny = -(ey - sy), (ex - sx)
                    norm = math.hypot(nx, ny) or 1.0
                    bend = 5.5 * (1 if rng.random() < 0.5 else -1)
                    ctrl = (mx + nx / norm * bend, my + ny / norm * bend)
                    tl.move(p, ex, ey, t1, t1 + 700, ctrl)
                    tl.effect("fall", t1 + 330, 450, d)
                    lx, ly = self.D(side, mx, my)
                    tl.move(d, *self.P(side, lx - 2.5, ly + (1.5 if bend > 0 else -1.5)), t1 + 330, t1 + 820)
                    self.highlight(d)
                    tl.key(t1, t1 + 820)
                    self.t = t1 + 820
                    return
            tl.move(p, ex, ey, t1, t1 + 620)
            tl.key(t1, t1 + 400)
            self.t = t1 + 620
            return
        # final: son pas zincirin bir sonraki oyuncusuna (sutcu)
        ball_d = rng.uniform(76, 86)
        ball_l = rng.uniform(10, 58)
        nxt = self.ctx.chain_next(e)
        receiver = self._player(nxt) if nxt is not None else None
        if receiver is not None and self.actors[receiver].side != side:
            receiver = None
        if receiver is None or receiver == p:
            receiver = self.attacker_outfield(side, t, (Position.FWD, Position.MID), exclude=(p,))
        skip = (p,) if receiver is None else (p, receiver)
        self.setup(e, side, ball_d, ball_l, t, t + 800, skip=skip)
        px, py = self.P(side, ball_d, ball_l)
        tl.move(p, px, py, t, t + 300)
        t1 = max(self.gain(side, p, t + 40), t + 300)
        if receiver is None:
            self.t = t1 + 200
            return
        cross = _has(text, _T_CROSS) and "pas" not in text
        rd = rng.uniform(90, 97) if cross else rng.uniform(86, 93)
        rl = 34 + rng.uniform(-7, 7)
        rx, ry = self.P(side, rd, rl)
        tl.move(receiver, rx, ry, t, t1 + 420)
        tl.pass_to(receiver, t1 + 40, 380, h=4.0 if cross else 0.0)
        tl.key(t1 - 60, t1 + 420)
        self.t = t1 + 440

    def _circulate(self, e: int, side: str, rng: random.Random) -> None:
        tl, t = self.tl, self.t
        ball_d = rng.uniform(55, 70)
        ball_l = rng.uniform(16, 52)
        self.setup(e, side, ball_d, ball_l, t, t + 1300)
        mids = [i for i in self.side_actors(side, outfield=True) if self.actors[i].role in (Position.MID, Position.DEF)]
        if not mids:
            self.t = t + 600
            return
        targets = self.formation(e, side, ball_d, ball_l)
        aim = self.P(side, ball_d - 8, ball_l)
        first = min(mids, key=lambda i: (math.dist(targets[i], aim), self.actors[i].pid))
        t1 = self.gain(side, first, t + 60)
        order = sorted(mids, key=lambda i: self.actors[i].pid)
        chosen = [first]
        for _ in range(2):
            options = [i for i in order if i != chosen[-1]]
            chosen.append(options[rng.randrange(len(options))] if options else chosen[-1])
        for j in chosen[1:]:
            start = t1 + 120
            t1 = tl.pass_to(j, start, 360)
            tl.key(start, t1)
        self.t = t1 + 100

    # ------------------------------------------------------------------ sut
    def _shot_spot(self, form: str, rng: random.Random) -> tuple[float, float]:
        if form == "long":
            return rng.uniform(74, 82), 34 + rng.uniform(-13, 13)
        if form == "oneonone":
            return rng.uniform(92, 96), 34 + rng.uniform(-6, 6)
        if form == "close":
            return rng.uniform(98, 101), 34 + rng.uniform(-5, 5)
        if form == "header":
            return rng.uniform(94, 99), 34 + rng.uniform(-6, 6)
        if form == "volley":
            return rng.uniform(89, 94), 34 + rng.uniform(-8, 8)
        if form == "penalty":
            return SPOT_D, CY
        return rng.uniform(87, 93), 34 + rng.uniform(-10, 10)

    def b_shot(self, e: int) -> None:
        ev = self.ctx.events[e]
        side = self.ctx.side_of(e) or "home"
        rng = self.ctx.rng(e, "shot")
        tl, t = self.tl, self.t
        text = ev.description
        form = shot_form(text, ev.detail)
        s = self._player(e)
        if s is None:
            s = self.attacker_outfield(side, t)
        if s is None:
            self.t = t + 300
            return
        self.highlight(s)
        sd, sl = self._shot_spot(form, rng)
        self.setup(e, side, sd, sl, t, t + 900, skip=(s,))
        sx, sy = self.P(side, sd, sl)
        if form == "header" or form == "volley":
            holder = tl.holder()
            crosser = holder if holder >= 0 and self.actors[holder].side == side and holder != s else None
            if crosser is None:
                crosser = self.nearest([i for i in self.side_actors(side, outfield=True) if i != s],
                                       *self.P(side, 90, 8 if sl < 34 else 60), t)
            if crosser is not None:
                cx_, cy_ = self.P(side, rng.uniform(86, 94), 7 if sl < 34 else 61)
                tl.move(crosser, cx_, cy_, t, t + 380)
                t1 = max(self.gain(side, crosser, t), t + 380)
                tl.move(s, sx, sy, t, t1 + 420)
                t1 = tl.pass_to(s, t1 + 40, 420, h=4.5 if form == "header" else 2.0)
                tl.key(t1 - 420, t1)
                if form == "header":
                    tl.effect("hdr", t1 - 80, 300, s)
                self._strike(e, side, s, t1, form, rng)
                return
        if form == "oneonone":
            start = self.P(side, sd - rng.uniform(12, 16), sl + rng.uniform(-4, 4))
            tl.move(s, *start, t, t + 260)
            t1 = max(self.gain(side, s, t), t + 260)
            tl.move(s, sx, sy, t1, t1 + 520)
            tl.key(t1 + 200, t1 + 520)
            self._press(e, side, s, t1 + 200)
            self._strike(e, side, s, t1 + 520, form, rng)
            return
        if form == "solo":
            start = self.P(side, sd - rng.uniform(10, 14), sl + rng.uniform(-8, 8))
            tl.move(s, *start, t, t + 260)
            t1 = max(self.gain(side, s, t), t + 260)
            d = self.named(e, self.other(side)) or self.nearest(
                self.side_actors(self.other(side), outfield=True), (start[0] + sx) / 2, (start[1] + sy) / 2, t1)
            if d is not None:
                mx, my = (start[0] + sx) / 2, (start[1] + sy) / 2
                tl.move(d, mx, my, t1 - 120, t1 + 240)
                ctrl = (mx + (sy - start[1]) * 0.35, my - (sx - start[0]) * 0.35)
                tl.move(s, sx, sy, t1, t1 + 560, ctrl)
                tl.effect("fall", t1 + 280, 420, d)
                self.highlight(d)
                tl.key(t1, t1 + 560)
            else:
                tl.move(s, sx, sy, t1, t1 + 560)
            self._strike(e, side, s, t1 + 560, form, rng)
            return
        # yerden sut: top sutcuye gelir (pas / kazanma), kisa bir tasima, sut
        near = self.P(side, sd - rng.uniform(3, 6), sl + rng.uniform(-3, 3))
        if self.tight:
            tl.move(s, sx, sy, t, t + 340)
            t1 = max(self.gain(side, s, t + 20), t + 340)
            self._press(e, side, s, t1 - 250)
            self._strike(e, side, s, t1, form, rng)
            return
        tl.move(s, *near, t, t + 360)
        t1 = max(self.gain(side, s, t + 40), t + 360)
        tl.move(s, sx, sy, t1, t1 + 300)
        self._press(e, side, s, t1)
        self._strike(e, side, s, t1 + 300, form, rng)

    def _press(self, e: int, side: str, s: int, t: float) -> None:
        """Cumlede adi gecen savunmaci (orn. '{d} son anda müdahale etti') sutcunun ustune gelir; yoksa en yakin."""
        tl = self.tl
        defenders = self.side_actors(self.other(side), outfield=True)
        named = self.named(e, self.other(side))
        sx, sy = tl.pos_at(s, t + 250)
        d = named or self.nearest(defenders, sx, sy, t)
        if d is None:
            return
        ld, ll = self.D(side, sx, sy)
        tl.move(d, *self.P(side, ld + 1.8, ll + (1.2 if ll < 34 else -1.2)), t, t + 280)
        if named is not None:
            self.highlight(named)

    def _strike(self, e: int, side: str, s: int, t: float, form: str, rng: random.Random,
                wall: Sequence[int] = ()) -> None:
        """Sut ani ve sonucu: GOL aga, KURTARIS kalecide, ISKA kale disinda (auta / ustten / direkten / baraj)."""
        ev = self.ctx.events[e]
        tl = self.tl
        rng = self.ctx.rng(e, "strike")          # sonuc geometrisi onceki cekilislerden bagimsiz (bitis pozu sabit)
        k = self.keeper(self.other(side))
        if k is not None:
            self.highlight(k)
        sx, sy = tl.pos_at(s, t)
        sd, sl = self.D(side, sx, sy)
        dist = math.hypot(L - sd, CY - sl)
        fly = max(260.0, min(330.0 if self.tight else 560.0, 150.0 + dist * 11.0))
        if tl.holder() != s:                                     # top sutcude degilse (guvenlik) once gelsin
            t = self.gain(side, s, t)
        # kaleci acıyı kapatir
        if k is not None:
            gl = 34 + (sl - 34) * 0.18
            tl.move(k, *self.P(side, L - 1.4, gl), t - 250, t)
        hit = t + fly
        tl.key(t - 140, hit + 260)
        et = ev.type
        if et is EventType.GOAL:
            sign = rng.choice((-1.0, 1.0))
            ty = 34 + sign * rng.uniform(1.2, 3.0)
            tx = L + rng.uniform(0.7, 1.5)
            h = 3.2 if form == "free_kick" else (rng.uniform(0.0, 1.8) if form != "header" else 0.6)
            tl.fly(*self.P(side, tx, ty), t, hit, h, -1, clamp=False)
            if k is not None:
                wrong = rng.random() < 0.6
                ky = 34 - sign * rng.uniform(1.0, 2.2) if wrong else ty - sign * rng.uniform(1.7, 2.4)
                tl.move(k, *self.P(side, L - 0.9, ky), hit - fly * 0.55, hit + 60)
                tl.effect("dive", hit - fly * 0.55, 700, k)
            gx, gy = self.P(side, L + NET / 2, 34)
            tl.effect("net", hit, 800, x=gx, y=gy)
            self._celebrate(e, side, s, hit + 150, rng)
            return
        if et is EventType.SAVE:
            kind = save_kind(ev.description)
            sign = rng.choice((-1.0, 1.0))
            ty = 34 + sign * rng.uniform(0.4, 2.8)
            if k is None:
                tl.fly(*self.P(side, L + 1.5, 34 + sign * 6), t, hit, 1.0, -1, clamp=False)
                self.t = hit + 200
                return
            if kind == "block":
                bx, by = self.P(side, sd + 2.2, sl + (34 - sl) * 0.1)
                tl.move(k, bx, by, t - 320, t + fly * 0.6)
                tl.fly(bx, by, t, t + fly * 0.6, 0.0, -1)
                tl.effect("save", t + fly * 0.6, 500, k)
                rb = self.P(side, sd + rng.uniform(-6, -2), sl + rng.uniform(-9, 9))
                tl.fly(*rb, t + fly * 0.6 + 10, t + fly * 0.6 + 420, 0.6, -1)
                self._clear(side, t + fly * 0.6 + 420)
                return
            kx, ky = self.P(side, L - 0.8, ty)
            tl.move(k, kx, ky, hit - fly * 0.6, hit)
            tl.effect("dive", hit - fly * 0.6, 700, k)
            if kind == "parry":
                tl.fly(kx, ky, t, hit, rng.uniform(0.3, 2.0), -1)
                tl.effect("save", hit, 450, k)
                out = self.P(side, L + rng.uniform(0.8, 2.4), 34 + sign * rng.uniform(5.5, 9.5))
                tl.fly(*out, hit + 10, hit + 380, 1.2, -1, clamp=False)
                self.t = hit + 450
                self._shape_after(e, self.other(side), 12.0, hit + 60, hit + (300 if self.tight else 500))
                return
            tl.fly(kx, ky, t, hit, 3.0 if form == "free_kick" else rng.uniform(0.2, 1.8), k)
            tl.effect("save", hit, 450, k)
            self.t = hit + 350
            self._shape_after(e, self.other(side), 10.0, hit, hit + (380 if self.tight else 700))
            return
        # ISKA
        kind = miss_kind(ev.description)
        if kind == "wall" and wall:
            wx, wy = tl.pos_at(wall[len(wall) // 2], t)
            tl.fly(wx, wy, t, t + 260, 1.6, -1)
            tl.effect("wall", t + 260, 400, x=wx, y=wy)
            rb = self.P(side, sd + rng.uniform(-8, -4), sl + rng.uniform(-10, 10))
            tl.fly(*rb, t + 270, t + 620, 1.5, -1)
            self._clear(side, t + 620)
            return
        if kind == "block" or (kind == "wall" and not wall):
            d = self.named(e, self.other(side)) or self.nearest(self.side_actors(self.other(side), outfield=True),
                                                                *self.P(side, sd + 3, sl), t)
            if d is not None:
                bx, by = self.P(side, sd + 2.5, sl + (34 - sl) * 0.12)
                tl.move(d, bx, by, t - 260, t + 120)
                tl.fly(bx, by, t, t + 150, 0.3, -1)
                self.highlight(d)
                out = self.P(side, L + rng.uniform(1.0, 3.0), 34 + (1 if sl >= 34 else -1) * rng.uniform(6, 14))
                tl.fly(*out, t + 160, t + 520, 1.0, -1, clamp=False)
                self._goal_kick(side, t + 560, rng)
                return
        if kind in ("post", "bar"):
            if kind == "post":
                py = POST_LOW if (sl < 34) == (rng.random() < 0.7) else POST_HIGH
                end = self.P(side, L - 0.25, py + (0.25 if py == POST_LOW else -0.25))
                prof, h = 0, 0.8
            else:
                end = self.P(side, L - 0.3, 34 + rng.uniform(-2.5, 2.5))
                prof, h = 1, CROSSBAR
            tl.fly(*end, t, hit, h, -1, prof)
            tl.effect("post", hit, 450, x=end[0], y=end[1])
            if k is not None:
                tl.move(k, *self.P(side, L - 1.0, 34 + (sl - 34) * 0.2), t, hit)
            rb = self.P(side, rng.uniform(92, 99), 34 + rng.uniform(-12, 12))
            tl.fly(*rb, hit + 10, hit + 420, 1.0, -1)
            self._clear(side, hit + 420)
            return
        if kind == "over":
            cy_ = 34 + rng.uniform(-5.5, 5.5)
            ex = L + rng.uniform(4.0, 6.5)
            u_line = max(0.05, (L - sd) / max(ex - sd, 1e-6))
            ey = sl + (cy_ - sl) / u_line
            ey = max(-2.5, min(W + 2.5, ey))
            h = max(rng.uniform(5.5, 7.5), 3.1 / u_line)
            tl.fly(*self.P(side, ex, ey), t, hit, h, -1, 1, clamp=False)
            self._goal_kick(side, hit + 250, rng)
            return
        # auta (direklerin disi): cizgiyi direk disindan gecer, yan aga en fazla dokunur
        sign = 1.0 if (sl >= 34) == (rng.random() < 0.65) else -1.0
        cy_ = 34 + sign * rng.uniform(POST_HIGH - 34 + 1.0, POST_HIGH - 34 + 6.5)
        ex = L + rng.uniform(1.5, 3.5)
        slope = (cy_ - sl) / max(L - sd, 1.0)
        ey = cy_ + slope * (ex - L)
        if abs(ey - 34) < POST_HIGH - 34 + 0.5:                   # iceri kivrilan top yan aga carpar, aga girmez
            ey = 34 + sign * (POST_HIGH - 34 + 0.5)
            ex = L + min(ex - L, NET)
        tl.fly(*self.P(side, ex, ey), t, hit, rng.uniform(0.2, 2.2), -1, 0, clamp=False)
        if k is not None:
            tl.move(k, *self.P(side, L - 1.0, 34 + sign * 1.4), t, hit)
        self._goal_kick(side, hit + 250, rng)

    def _goal_kick(self, att_side: str, t: float, rng: random.Random) -> None:
        """Iska sonrasi: kale vurusu -- top savunan kalecide (ya da en geri savunmacida)."""
        tl = self.tl
        dside = self.other(att_side)
        k = self.keeper(dside)
        spot = self.P(dside, 5.5, 34 + self.ctx.rng(self.index, "goalkick").choice((-6.0, 6.0)))
        span = 300 if self.tight else 450
        if k is not None:
            tl.move(k, *spot, t, t + span)
            tl.fly(*spot, t + 60, t + span, 0.0, k, RESPOT)
        self._shape_after(self.index, dside, 6.0, t, t + (380 if self.tight else 700))
        self.t = t + span + 50

    def _clear(self, att_side: str, t: float) -> None:
        """Serbest top: savunan takimin en yakin oyuncusu toplar."""
        tl = self.tl
        dside = self.other(att_side)
        bx, by = tl.ball_at(t)
        d = self.nearest(self.side_actors(dside, outfield=True), bx, by, t)
        if d is None:
            self.t = t + 200
            return
        tl.move(d, bx, by, t - 150, t + 200)
        tl.fly(bx, by, t + 190, t + 260, 0.0, d)
        ld, ll = self.D(dside, bx, by)
        self._shape_after(self.index, dside, max(ld, 8.0), t, t + (380 if self.tight else 700), skip=(d,))
        self.t = t + 300

    def _shape_after(self, e: int, poss: str, ball_d: float, t0: float, t1: float, skip: Sequence[int] = ()) -> None:
        tl = self.tl
        holder = tl.holder()
        skip = tuple(skip) + ((holder,) if holder >= 0 else ())
        k_att = self.keeper(self.other(poss))
        targets = self.formation(e, poss, ball_d, 34.0, "play", self.ctx.rng(e, f"after|{poss}"))
        self.shift(targets, t0, t1, skip=skip + ((k_att,) if k_att is not None else ()))
        if k_att is not None:
            tl.move(k_att, *targets[k_att], t1, t1 + 400)

    def _celebrate(self, e: int, side: str, s: int, t: float, rng: random.Random) -> None:
        tl = self.tl
        rng = self.ctx.rng(e, "celebrate")
        sx, sy = tl.pos_at(s, t)
        ld, ll = self.D(side, sx, sy)
        corner = self.P(side, 99.0, 5.0 if ll < 34 else 63.0)
        tl.move(s, *corner, t, t + 900)
        tl.effect("cel", t + 300, 1400, s)
        tl.key(t, t + 500)
        mates = sorted((i for i in self.side_actors(side, outfield=True) if i != s),
                       key=lambda i: (math.dist(tl.pos_at(i, t), (sx, sy)), self.actors[i].pid))
        for n, i in enumerate(mates[:4]):
            ang = n * 1.4 + rng.uniform(-0.3, 0.3)
            tl.move(i, corner[0] - math.cos(ang) * 2.4, corner[1] + math.sin(ang) * 2.4 * (1 if ll < 34 else -1),
                    t + 150, t + 1000 + n * 90)
        for i in mates[4:]:
            px, py = tl.pos_at(i, t)
            tl.move(i, px + (corner[0] - px) * 0.3, py + (corner[1] - py) * 0.2, t + 200, t + 1300)
        for i in self.side_actors(self.other(side), outfield=True):
            px, py = tl.pos_at(i, t)
            od, ol = self.D(self.other(side), px, py)
            tl.move(i, *self.P(self.other(side), max(od - 5, 6), ol), t + 250, t + 1300)
        self.t = t + 1300

    # ------------------------------------------------------------------ duran toplar
    def _corner_taker(self, e: int, side: str, shooter: int | None) -> int | None:
        """Motorun korner aticisi kurali (MatchEngine._designated_or_best): belirlenmis atici sahadaysa o, degilse
        sahadaki saha oyuncularindan en yuksek korner becerisi (esitlikte kucuk id). Cumlede atici adiyla geciyorsa o."""
        text = _norm(self.ctx.events[e].description)
        takes = _has(text, ("hazırlan", "korneri kullan", "kornerinde", "korner ortası", "kornerini"))
        named = self.named(e, side, exclude=(shooter,) if shooter is not None else ()) if takes else None
        if named is not None and not self.actors[named].gk:
            return named
        team = self.ctx.teams[side]
        rows = self.ctx.rows(side, e)
        designated = team.roles.corner_taker_id
        if designated is not None and any(tr.player.id == designated for tr, _ in rows):
            return self.idx(designated)
        pool = [tr.player for tr, role in rows if role is not Position.GK] or [tr.player for tr, _ in rows]
        best = max(pool, key=lambda p: (team_roles.corner_skill(p), -p.id), default=None)
        return self.idx(best.id) if best is not None else None

    def b_corner(self, e: int, shot: bool) -> None:
        side = self.ctx.side_of(e) or "home"
        dside = self.other(side)
        rng = self.ctx.rng(e, "corner")
        tl, t = self.tl, self.t
        s = self._player(e) if shot else None
        taker = self._corner_taker(e, side, s)
        if taker is None:
            self.t = t + 300
            return
        if s == taker:
            s = self.attacker_outfield(side, t, (Position.DEF, Position.FWD), exclude=(taker,))
        self.highlight(taker, s if shot else None)
        top = rng.random() < 0.5
        flag_l = 0.4 if top else W - 0.4
        flag = self.P(side, L - 0.4, flag_l)
        # kurulum: ceza sahasi kalabaligi (hucumcular ceza sahasinda, savunma adam adama, iki hucumcu geride)
        att = [i for i in self.side_actors(side, outfield=True) if i != taker]
        att.sort(key=lambda i: ({Position.DEF: 0, Position.FWD: 1, Position.MID: 2}[self.actors[i].role],
                                self.actors[i].pid))
        box_att = att[:6]
        if s is not None and s not in box_att:
            box_att = [s] + box_att[:5]
        stay = [i for i in att if i not in box_att]
        targets: dict[int, tuple[float, float]] = {}
        for n, i in enumerate(box_att):
            targets[i] = self.P(side, rng.uniform(94, 101), 25 + 18 * (n + 0.5) / len(box_att) + rng.uniform(-1, 1))
        for n, i in enumerate(stay):
            targets[i] = self.P(side, 58 + 10 * (n % 2), 18 + 32 * (n + 0.5) / max(len(stay), 1))
        defenders = self.side_actors(dside, outfield=True)
        markers = sorted(defenders, key=lambda i: ({Position.DEF: 0, Position.MID: 1, Position.FWD: 2}
                                                    [self.actors[i].role], self.actors[i].pid))
        for n, i in enumerate(markers):
            if n < len(box_att):
                ax, ay = targets[box_att[n]]
                ad, al = self.D(side, ax, ay)
                targets[i] = self.P(side, ad + 1.3, al + (0.9 if al < 34 else -0.9))
            elif n < len(box_att) + 2:
                targets[i] = self.P(side, 99.5, (POST_LOW - 1.0) if n % 2 else (POST_HIGH + 1.0))
            else:
                targets[i] = self.P(side, rng.uniform(80, 86), 34 + rng.uniform(-12, 12))
        k = self.keeper(dside)
        if k is not None:
            targets[k] = self.P(side, L - 1.0, 34 + (-1.2 if top else 1.2))
        k_att = self.keeper(side)
        if k_att is not None:
            targets[k_att] = self.P(side, 30.0, 34.0)
        self._separate(targets, fixed=(k,) if k is not None else ())
        walk = 480 if self.tight else 700
        self.shift(targets, t, t + walk + 200, skip=(taker,))
        tl.move(taker, *flag, t, t + walk)
        # top bayrakta: once takimda degilse serbest top olarak bayrağa gelir
        holder = tl.holder()
        if holder >= 0 and self.actors[holder].side == side and holder != taker:
            tl.fly(*flag, t + 150, t + walk, 2.0, taker)
        else:
            tl.fly(*flag, t + 80, t + walk, 0.0, -1, RESPOT)
            tl.fly(*flag, t + walk, t + walk + 20, 0.0, taker)
        tl.effect("fl", t + 60, walk + 250, x=flag[0], y=flag[1])
        t1 = t + walk + (120 if self.tight else 300)
        if shot and s is not None:
            sd, sl = self._shot_spot("header", rng)
            sx, sy = self.P(side, sd, sl)
            tl.move(s, sx, sy, t1 - 200, t1 + 480)
            tl.pass_to(s, t1, 480, h=5.0)
            tl.key(t1 - 100, t1 + 480)
            tl.effect("hdr", t1 + 400, 300, s)
            self._strike(e, side, s, t1 + 480, "header", rng)
            return
        # korner kazanildi (sut yok): orta savunmadan doner, savunan takim toplar
        land = self.P(side, rng.uniform(95, 100), 34 + rng.uniform(-5, 5))
        head = self.nearest([i for i in defenders], land[0], land[1], t1)
        if head is None:
            tl.fly(*land, t1, t1 + 480, 5.0, -1)
            self.t = t1 + 520
            return
        tl.move(head, *land, t1 - 150, t1 + 470)
        tl.pass_to(head, t1, 480, h=5.0)
        tl.key(t1 - 100, t1 + 600)
        tl.effect("hdr", t1 + 400, 300, head)
        away = self.P(side, rng.uniform(70, 80), 34 + rng.uniform(-18, 18))
        tl.fly(*away, t1 + 500, t1 + 900, 5.0, -1)
        self._clear(side, t1 + 900)

    def b_free_kick(self, e: int) -> None:
        side = self.ctx.side_of(e) or "home"
        dside = self.other(side)
        rng = self.ctx.rng(e, "fk")
        tl, t = self.tl, self.t
        s = self._player(e)
        if s is None:
            self.b_shot(e)
            return
        self.highlight(s)
        bd, bl = rng.uniform(76, 86), 34 + rng.uniform(-14, 14)
        ball = self.P(side, bd, bl)
        tl.effect("wh", t, 600, x=ball[0], y=ball[1])
        targets = self.formation(e, side, bd, bl, "play")
        # baraj: topla kale ortasi arasindaki dogruda 9,15 m, dik yonde 1 m aralik
        gx, gy = L, 34.0
        dx, dy = gx - bd, gy - bl
        norm = math.hypot(dx, dy) or 1.0
        ux, uy = dx / norm, dy / norm
        wall_n = 4 if norm < 24 else 3
        defenders = sorted(self.side_actors(dside, outfield=True),
                           key=lambda i: (math.dist(targets[i], ball), self.actors[i].pid))
        wall = defenders[:wall_n]
        for n, i in enumerate(wall):
            off = (n - (wall_n - 1) / 2) * 1.05
            wd, wl = bd + ux * WALL_DIST - uy * off, bl + uy * WALL_DIST + ux * off
            targets[i] = self.P(side, wd, wl)
        # hucumcular ceza sahasina, bos savunmacilar onlari tutar
        runners = sorted((i for i in self.side_actors(side, outfield=True) if i != s),
                         key=lambda i: ({Position.FWD: 0, Position.DEF: 1, Position.MID: 2}[self.actors[i].role],
                                        self.actors[i].pid))[:5]
        free = [i for i in defenders if i not in wall]
        for n, i in enumerate(runners):
            rd, rl = rng.uniform(93, 99), 26 + 16 * (n + 0.5) / len(runners)
            targets[i] = self.P(side, rd, rl)
            if n < len(free):
                targets[free[n]] = self.P(side, rd + 1.2, rl + (0.8 if rl < 34 else -0.8))
        k = self.keeper(dside)
        if k is not None:
            targets[k] = self.P(side, L - 1.0, 34 + (1.5 if bl < 34 else -1.5))
        self._separate(targets, fixed=tuple(wall) + ((k,) if k is not None else ()))
        stand = self.P(side, bd - ux * 2.2, bl - uy * 2.2)
        self.shift(targets, t, t + 1000, skip=(s,))
        tl.move(s, *stand, t, t + 700)
        holder = tl.holder()
        if holder >= 0 and self.actors[holder].side == side:
            tl.fly(*ball, t + 150, t + 650, 0.0, -1, RESPOT)
        else:
            tl.fly(*ball, t + 150, t + 650, 0.0, -1, RESPOT)
        tl.move(s, *ball, t + 1050, t + 1300)
        tl.fly(*ball, t + 1290, t + 1300, 0.0, s)
        tl.key(t + 1050, t + 1310)
        self._strike(e, side, s, t + 1310, "free_kick", rng, wall=wall)

    def b_penalty(self, e: int) -> None:
        side = self.ctx.side_of(e) or "home"
        dside = self.other(side)
        rng = self.ctx.rng(e, "pen")
        tl, t = self.tl, self.t
        s = self._player(e)
        if s is None:
            self.b_shot(e)
            return
        self.highlight(s)
        spot = self.P(side, SPOT_D, CY)
        tl.effect("wh", t, 700, x=spot[0], y=spot[1])
        targets = self.formation(e, side, 84.0, 34.0, "play")
        # ceza sahasi bosalir: herkes ceza sahasi / yay disina
        others = sorted((i for i in targets if i != s and not self.actors[i].gk), key=lambda i: self.actors[i].pid)
        for n, i in enumerate(others):
            px, py = targets[i]
            d, lat = self.D(side, px, py)
            if d > BOX_D - 1.5:
                lane = n % 2
                targets[i] = self.P(side, BOX_D - 2.5 - 2.2 * (n % 3), 16 + 36 * ((n * 0.37 + lane * 0.5) % 1.0))
        k = self.keeper(dside)
        if k is not None:
            targets[k] = self.P(side, L - 0.6, 34)
        k_att = self.keeper(side)
        if k_att is not None:
            targets[k_att] = self.P(side, 30, 34)
        self._separate(targets, fixed=(k,) if k is not None else ())
        self.shift(targets, t, t + 1000, skip=(s,))
        stand = self.P(side, SPOT_D - 2.6, CY + 0.8)
        tl.move(s, *stand, t, t + 800)
        tl.fly(*spot, t + 200, t + 700, 0.0, -1, RESPOT)
        tl.move(s, *self.P(side, SPOT_D - 0.6, CY + 0.2), t + 1050, t + 1300)
        tl.fly(*self.P(side, SPOT_D - 0.6, CY + 0.2), t + 1290, t + 1300, 0.0, s)
        tl.key(t + 1000, t + 1310)
        self._spot_kick(e, side, s, t + 1310, rng)

    def _spot_kick(self, e: int, side: str, s: int, t: float, rng: random.Random, shootout: bool = False) -> None:
        """Penalti vurusu (mac ici ya da seri): GOL kosede, KURTARIS kalecide, ISKA kale disinda."""
        ev = self.ctx.events[e]
        tl = self.tl
        k = self.keeper(self.other(side))
        if k is not None:
            self.highlight(k)
        if shootout:
            outcome = {"scored": EventType.GOAL, "saved": EventType.SAVE}.get(ev.detail or "", EventType.MISS)
        else:
            outcome = ev.type
        sign = rng.choice((-1.0, 1.0))
        hit = t + 330
        tl.key(t - 120, hit + 300)
        if outcome is EventType.GOAL:
            ty = 34 + sign * rng.uniform(1.4, 3.1)
            tl.fly(*self.P(side, L + rng.uniform(0.8, 1.5), ty), t, hit, rng.uniform(0.0, 1.5), -1, clamp=False)
            if k is not None:
                wrong = rng.random() < 0.7
                ky = 34 - sign * rng.uniform(1.4, 2.4) if wrong else ty - sign * rng.uniform(1.8, 2.4)
                tl.move(k, *self.P(side, L - 0.8, ky), t - 40, hit + 40)
                tl.effect("dive", t - 40, 700, k)
            gx, gy = self.P(side, L + NET / 2, 34)
            tl.effect("net", hit, 800, x=gx, y=gy)
            if shootout:
                tl.effect("cel", hit + 100, 800, s)
                self.t = hit + 900
            else:
                self._celebrate(e, side, s, hit + 150, rng)
            return
        if outcome is EventType.SAVE and k is not None:
            ty = 34 + sign * rng.uniform(1.0, 2.6)
            kx, ky = self.P(side, L - 0.8, ty)
            tl.move(k, kx, ky, t - 40, hit)
            tl.effect("dive", t - 40, 700, k)
            tl.effect("save", hit, 450, k)
            if save_kind(ev.description) == "catch" or shootout:
                tl.fly(kx, ky, t, hit, 0.5, k)
                self.t = hit + 500
                if not shootout:
                    self._shape_after(e, self.other(side), 10.0, hit, hit + 700)
                return
            tl.fly(kx, ky, t, hit, 0.5, -1)
            tl.fly(*self.P(side, L + 1.5, 34 + sign * 7.5), hit + 10, hit + 380, 1.0, -1, clamp=False)
            self.t = hit + 450
            return
        # iska: dis direk / ustten / direk
        kind = miss_kind(ev.description)
        if kind in ("post", "bar"):
            end = (self.P(side, L - 0.25, POST_LOW + 0.25 if sign < 0 else POST_HIGH - 0.25) if kind == "post"
                   else self.P(side, L - 0.3, 34 + sign * 1.5))
            tl.fly(*end, t, hit, 0.8 if kind == "post" else CROSSBAR, -1, 1 if kind == "bar" else 0)
            tl.effect("post", hit, 450, x=end[0], y=end[1])
            tl.fly(*self.P(side, rng.uniform(96, 100), 34 + sign * rng.uniform(4, 9)), hit + 10, hit + 400, 1.0, -1)
        elif kind == "over":
            ex = L + 5.0
            tl.fly(*self.P(side, ex, 34 + sign * rng.uniform(0.5, 2.5)), t, hit + 150, 6.5, -1, 1, clamp=False)
        else:
            tl.fly(*self.P(side, L + 2.5, 34 + sign * rng.uniform(4.9, 7.5)), t, hit, 0.6, -1, clamp=False)
        if k is not None:
            tl.move(k, *self.P(side, L - 0.8, 34 - sign * 1.8), t - 40, hit)
            tl.effect("dive", t - 40, 700, k)
        self.t = hit + 450
        if not shootout:
            self._goal_kick(side, hit + 300, rng)

    # ------------------------------------------------------------------ faul / kart / ofsayt / sakatlik
    def b_foul(self, e: int, card: str | None) -> None:
        ev = self.ctx.events[e]
        side = self.ctx.side_of(e) or "home"                     # fauli yapan / karti goren takim
        victim_side = self.other(side)
        rng = self.ctx.rng(e, "foul")
        tl, t = self.tl, self.t
        f = self._player(e)
        text = _norm(ev.description)
        if f is None:
            self.setup(e, victim_side, 50, 34, t, t + 700)
            tl.effect("wh", t + 300, 600, x=CX, y=CY)
            self.t = t + 900
            return
        self.highlight(f)
        role = self.actors[f].role
        depth_by_role = {Position.DEF: (60, 78), Position.MID: (42, 62), Position.FWD: (22, 42), Position.GK: (86, 94)}
        lo, hi = depth_by_role.get(role, (40, 60))
        vd = rng.uniform(lo, hi)
        vl = rng.uniform(8, 60)
        dissent = card is not None and _has(text, _T_DISSENT)
        targets = self.setup(e, victim_side, vd, vl, t, t + 800, skip=(f,))
        spot = self.P(victim_side, vd, vl)
        pool = self.side_actors(victim_side, outfield=True)
        # faule ugrayan motor kaydinda yok: dizilis hedefinde faul yerine en yakin rakip (temsili, vurgulanmaz)
        victim = min(pool, key=lambda i: (math.dist(targets[i], spot), self.actors[i].pid)) if pool else None
        if dissent:
            # itiraz / zaman gecirme: carpisma yok, oyuncu hakeme (orta noktaya dogru) yurur, kart; top olu, rakipte
            fx_, fy_ = targets.get(f, tl.pos_at(f, t))
            tl.move(f, fx_ + (CX - fx_) * 0.15, fy_ + (CY - fy_) * 0.15, t, t + 600)
            tl.effect("wh", t + 200, 500, i=f)
            if victim is not None:
                tl.fly(*targets[victim], t + 100, t + 500, 0.0, victim, RESPOT)
            self._card(f, card, t + 600, ev)
            return
        if victim is None:
            tl.move(f, *spot, t, t + 500)
            tl.effect("wh", t + 450, 600, x=spot[0], y=spot[1])
            self.t = t + 800
            return
        # kurban topla ilerler, faul yapan araya girer: carpisma, dusme, duduk
        pre = self.P(victim_side, vd - 5, vl + rng.uniform(-3, 3))
        tl.move(victim, *pre, t, t + 250)
        self.gain(victim_side, victim, t)
        t1 = max(t + (120 if self.tight else 260), tl.ball_state()[3])
        tl.move(victim, *spot, t1, t1 + 420)
        tl.move(f, spot[0] + (0.9 if rng.random() < 0.5 else -0.9), spot[1] + 0.8, t1 + 60, t1 + 420)
        tl.effect("duel", t1 + 330, 300, victim)
        tl.effect("fall", t1 + 400, 900, victim)
        tl.key(t1 + 200, t1 + 760)
        tl.effect("wh", t1 + 450, 650, x=spot[0], y=spot[1] - 3)
        self.t = t1 + 700
        if card is not None:
            self._card(f, card, t1 + 650, ev)

    def _card(self, f: int, card: str, t: float, ev) -> None:
        tl = self.tl
        tl.key(t, t + 550)
        if card == "rc" and ev.detail == "second_yellow":
            tl.effect("yc", t, 600, f)
            tl.effect("rc", t + 600, 900, f)
            t += 600
        else:
            tl.effect(card, t, 900, f)
        if card == "rc":
            fx_, fy_ = tl.pos_at(f, t)
            tl.move(f, CX + (fx_ - CX) * 0.3, BENCH_Y - 1.0, t + 500, t + 1500)
            self.t = t + 1500
        else:
            self.t = t + 900

    def b_offside(self, e: int) -> None:
        side = self.ctx.side_of(e) or "home"
        dside = self.other(side)
        rng = self.ctx.rng(e, "off")
        tl, t = self.tl, self.t
        a = self._player(e)
        bd, bl = rng.uniform(58, 68), rng.uniform(14, 54)
        targets = self.setup(e, side, bd, bl, t, t + 800, skip=(a,) if a is not None else ())
        if a is None:
            self.t = t + 800
            return
        self.highlight(a)
        line = [self.D(side, *targets[i])[0] for i in self.side_actors(dside, outfield=True)
                if self.actors[i].role is Position.DEF] or [80.0]
        last_line = max(line)
        ad, al = last_line + rng.uniform(1.2, 2.2), rng.uniform(20, 48)
        tl.move(a, *self.P(side, ad - 3, al), t, t + 300)
        tl.move(a, *self.P(side, ad + 6, al), t + 450, t + 900)
        passer = self.attacker_outfield(side, t, (Position.MID, Position.DEF), exclude=(a,))
        if passer is not None:
            tl.move(passer, *self.P(side, bd, bl), t, t + 300)
            t1 = max(self.gain(side, passer, t), t + 300)
            tl.pass_to(a, t1 + 80, 480)
        tl.key(t + 400, t + 1000)
        flag_x = self.P(side, ad, 0)[0]
        tl.effect("fl", t + 700, 900, x=flag_x, y=W + 0.8 if rng.random() < 0.5 else -0.8)
        tl.effect("wh", t + 800, 600, x=flag_x, y=34)
        # serbest vurus savunana
        end = tl.ball_state()[3]
        self._clear(side, max(end, t + 950) + 150)

    def b_injury(self, e: int) -> None:
        ev = self.ctx.events[e]
        side = self.ctx.side_of(e) or "home"
        rng = self.ctx.rng(e, "inj")
        tl, t = self.tl, self.t
        p = self._player(e)
        targets = self.setup(e, None, 52.5, 34.0, t, t + 900, skip=(p,) if p is not None else ())
        if p is None:
            self.t = t + 800
            return
        self.highlight(p)
        px, py = targets.get(p, tl.pos_at(p, t))
        if _has(_norm(ev.description), _T_DUEL):
            opp = self.nearest(self.side_actors(self.other(side), outfield=True), px, py, t)
            if opp is not None:
                tl.move(opp, px + 1.0, py + 0.7, t + 100, t + 450)
                tl.effect("duel", t + 380, 300, p)
        tl.move(p, px, py, t, t + 450)
        tl.key(t + 300, t + 900)
        tl.effect("fall", t + 450, 2400, p)
        tl.effect("inj", t + 600, 2200, p)
        tl.effect("wh", t + 500, 500, x=px, y=py - 3)
        out = (px + rng.uniform(-4, 4), -0.8 if py < 34 else W + 0.8)
        tl.fly(*out, t + 300, t + 700, 1.2, -1, clamp=False)
        self.t = t + 1100

    def b_sub(self, e: int) -> None:
        side = self.ctx.side_of(e) or "home"
        tl, t = self.tl, self.t
        incoming = self._player(e)
        outgoing = next((i for i, a in enumerate(self.actors) if a.status == 2 and a.side == side), None)
        board = (CX + (-3.0 if side == "home" else 3.0), BENCH_Y)
        targets = self.formation(e, None, 52.5, 34.0, "play", self.ctx.rng(e, "sub"))
        # degisiklik oyun durmusken yapilir: top taca (kulube tarafi, orta cizgi yakini), olu top
        tl.fly(CX + (-6.0 if side == "home" else 6.0), W - 0.3, t, t + 500, 0.4, -1)
        skip = tuple(i for i in (incoming, outgoing) if i is not None)
        self.shift(targets, t, t + 1300, skip=skip)
        if outgoing is not None:
            self.highlight(outgoing)
            tl.effect("sub_out", t, 1300, outgoing)
            tl.move(outgoing, *board, t, t + 900)
        tl.key(t + 600, t + 1100)
        if incoming is not None:
            self.highlight(incoming)
            tl.effect("sub_in", t + 700, 1100, incoming)
            if incoming in targets:
                tl.move(incoming, *targets[incoming], t + 800, t + 1500)
        self.t = t + 1500

    def b_tactic(self, e: int) -> None:
        side = self.ctx.side_of(e)
        tl, t = self.tl, self.t
        # taktik degisikligi (kenardan bagiris / plan): takim yeni sekline gecer; top olu (taca), temsili
        targets = self.formation(e, None, 52.5, 34.0, "play", self.ctx.rng(e, "tac"))
        self.shift(targets, t, t + 1100)
        tl.fly(CX + (-6.0 if side == "home" else 6.0), W - 0.3, t, t + 500, 0.4, -1)
        tl.key(t + 100, t + 500)
        if side in SIDES:
            tl.effect("tac", t + 100, 1200, x=CX + (-7.0 if side == "home" else 7.0), y=BENCH_Y - 0.4)
        self.t = t + 1100

    # ------------------------------------------------------------------ seri penaltilar
    def _shootout_pose(self, e: int, kicking: str, taker: int | None) -> dict[int, tuple[float, float]]:
        """pitch._shootout_scene ile ayni duzen: tek kale (sag), savunan kaleci cizgide, atan takimin kalecisi ceza
        sahasi kosesinde, digerleri orta yuvarlakta iki sira."""
        right = pitch.SHOOTOUT_GOAL_RIGHT
        out: dict[int, tuple[float, float]] = {}
        defending = self.other(kicking)
        for side in SIDES:
            line = [i for i in self.side_actors(side) if not self.actors[i].gk and i != taker]
            line.sort(key=lambda i: self.actors[i].pid)
            lat = CY - pitch.SHOOTOUT_ROW_OFFSET if side == "home" else CY + pitch.SHOOTOUT_ROW_OFFSET
            for n, i in enumerate(line):
                out[i] = (CX + (n - (len(line) - 1) / 2) * pitch.SHOOTOUT_ROW_SPACING, lat)
            k = self.keeper(side)
            if k is not None:
                if side == defending:
                    out[k] = (pitch.SHOOTOUT_KEEPER_X, CY) if right else (L - pitch.SHOOTOUT_KEEPER_X, CY)
                else:
                    out[k] = ((L - pitch.PENALTY_DEPTH, (W - pitch.PENALTY_WIDTH) / 2) if right
                              else (pitch.PENALTY_DEPTH, (W - pitch.PENALTY_WIDTH) / 2))
        return out

    def _shootout_first(self) -> str:
        first = getattr(getattr(self.ctx.result, "shootout", None), "first_side", None)
        return first if first in SIDES else "home"

    def b_shootout_setup(self, e: int) -> None:
        tl, t = self.tl, self.t
        pose = self._shootout_pose(e, self._shootout_first(), None)
        self.shift(pose, t, t + 1300)
        spot = pitch.SHOOTOUT_SPOT
        tl.effect("wh", t, 800, x=CX, y=CY - 6)
        tl.fly(*spot, t + 200, t + 900, 0.0, -1, RESPOT)
        self.t = t + 1300

    def b_shootout_kick(self, e: int) -> None:
        kicking = self.ctx.side_of(e) or self._shootout_first()
        rng = self.ctx.rng(e, "so")
        tl, t = self.tl, self.t
        taker = self._player(e)
        pose = self._shootout_pose(e, kicking, taker)
        self.shift(pose, t, t + 700, skip=(taker,) if taker is not None else ())
        spot = pitch.SHOOTOUT_SPOT
        tl.fly(*spot, t, t + 300, 0.0, -1, RESPOT)
        if taker is None:
            self.t = t + 700
            return
        self.highlight(taker)
        tl.move(taker, spot[0] - 2.4, spot[1] + 0.7, t, t + 700)
        tl.move(taker, spot[0] - 0.6, spot[1] + 0.2, t + 800, t + 1000)
        tl.fly(spot[0] - 0.6, spot[1] + 0.2, t + 990, t + 1000, 0.0, taker)
        self._spot_kick(e, kicking, taker, t + 1010, rng, shootout=True)


# ===========================================================================
# [6] KARE BETIGI
# ===========================================================================

def _actors_for(ctx: MatchCtx, index: int) -> list[Actor]:
    """Karenin oyunculari: o olaydaki saha + (degisiklikte) cikan oyuncu. Sira: ev (hat sirasi), deplasman, cikan."""
    ev = ctx.events[index]
    yellows = ctx.yellows_before(index)
    minute = ctx.minute(index)
    actors: list[Actor] = []
    seen: set[int] = set()
    for side in SIDES:
        for track, role in ctx.rows(side, index):
            p = track.player
            status = 1 if (ev.type is EventType.SUBSTITUTION and ev.player_id == p.id) else 0
            if track.exit == index + 1 and p.sent_off:
                status = 2
            actors.append(Actor(p.id, side, pitch.short_name(p.name, 11), role is Position.GK, role,
                                ENERGY_CODE[pitch.energy_level(pitch.energy_at(p, minute))], min(yellows.get(p.id, 0), 2),
                                status))
            seen.add(p.id)
    if ev.type is EventType.SUBSTITUTION and index > 0:
        side = ctx.side_of(index)
        for track, role in ctx.rows(side, index - 1) if side in SIDES else []:
            p = track.player
            if p.id not in seen and track.exit == index:
                actors.append(Actor(p.id, side, pitch.short_name(p.name, 11), role is Position.GK, role,
                                    ENERGY_CODE[pitch.energy_level(pitch.energy_at(p, minute))],
                                    min(yellows.get(p.id, 0), 2), 2))
    return actors


def _beats(ctx: MatchCtx, index: int, prev: int | None) -> list[int]:
    chain = ctx.chain(index)
    if prev is not None:
        chain = [j for j in chain if j > prev] or [index]
    return chain


TIGHT_BUDGET_MS = 1200         # bu butcenin altindaki karelerde kurulum / sonrasi kisa tutulur


def _run(ctx: MatchCtx, index: int, beats: list[int], start: dict[int, tuple[float, float]],
         ball: tuple[float, float, int], actors: list[Actor], restart: str | None,
         tight: bool = False) -> Choreographer:
    ch = Choreographer(ctx, index, actors)
    ch.tl = Timeline(start, ball)
    ch.tight = tight
    if restart in SIDES:                     # gol sonrasi: golu yiyen takim santra yapar
        ch.b_kickoff(beats[0], restart=True, side=restart)
        ch.t -= 250
    for n, e in enumerate(beats):
        ch.beat(e, last=(n == len(beats) - 1))
    return ch


def _nominal_start(ctx: MatchCtx, index: int, actors: list[Actor]) -> tuple[dict[int, tuple[float, float]],
                                                                            tuple[float, float, int]]:
    """Baglamsiz baslangic: olayin evresine gore notr dizilis, top ortada / olayin takiminda."""
    sc = Scene(ctx, index, actors)
    side = ctx.side_of(index)
    pose = sc.formation(index, side if side in SIDES else None, 50.0, 34.0, "play", ctx.rng(index, "nominal"),
                        leaving=True)
    return pose, (CX, CY, -1)


def rest_state(ctx: MatchCtx, index: int) -> tuple[dict[int, tuple[float, float]], tuple[float, float, int | None]]:
    """
    index'teki olayin koreografisi bittiginde: {oyuncu id: konum}, (top x, y, sahibin id'si | None). Yalnizca o olaya
    baglidir (degisiklik / taktik gibi olu top olaylarinda top bir onceki olayin yerinde kalir). Onbellekli.
    """
    if index in ctx._rest:
        return ctx._rest[index]
    if index < 0:
        actors = _actors_for(ctx, 0)
        ch = Choreographer(ctx, 0, actors)
        ch.tl = Timeline(_nominal_start(ctx, 0, actors)[0], (CX, CY, -1))
        ch.b_kickoff(0, restart=True)
        return _end_state(ch)
    actors = _actors_for(ctx, index)
    pose, ball = _nominal_start(ctx, index, actors)
    if ctx.events[index].type in NEUTRAL and index > 0:
        prev_pose, prev_ball = rest_state(ctx, index - 1)
        bx, by, hp = prev_ball
        by_pid = {a.pid: i for i, a in enumerate(actors)}
        holder = by_pid.get(hp, -1) if hp is not None else -1
        ball = (bx, by, holder)
        for i, a in enumerate(actors):
            if a.pid in prev_pose:
                pose[i] = prev_pose[a.pid]
    tight = (ctx.events[index].dwell_ms or 1800) * FIT < TIGHT_BUDGET_MS      # karedekiyle ayni koreografi
    ch = _run(ctx, index, [index], pose, ball, actors, None, tight=tight)
    state = _end_state(ch)
    ctx._rest[index] = state
    return state


def _end_state(ch: Choreographer) -> tuple[dict[int, tuple[float, float]], tuple[float, float, int | None]]:
    tl = ch.tl
    pose = {a.pid: tl.end_pos(i) for i, a in enumerate(ch.actors) if a.status != 2}
    x, y, holder, _ = tl.ball_state()
    if holder >= 0:
        x, y = tl.end_pos(holder)
        hp = ch.actors[holder].pid if ch.actors[holder].status != 2 else None
        if hp is None:
            holder = -1
    else:
        hp = None
    return pose, (x, y, hp)


def _r(v: float) -> float:
    return round(float(v), 1)


FILL_RATE_MIN = 0.28           # kurulum / sonrasi en fazla bu kadar hizlanir (1 / 0,28 ~ 3,6 kat)
KEY_RATE_MIN = 0.55            # asil anlar bundan cok sikisacaksa zincirin eski gizli halkalari birakilir


def time_warp(keys: list[tuple[float, float]], raw_T: float, budget: float):
    """
    Parca parca dogrusal zaman egrisi: [0, raw_T] -> [0, <= budget]. Asil an araliklari (keys) b, geri kalan a hiziyla
    akar (a <= b <= 1). Once asil anlar korunur (b = 1, a >= FILL_RATE_MIN); sigmazsa a = FILL_RATE_MIN ve b duser;
    b de a'nin altina inerse duz olcek. Donus: (egri, asil anlarin hizi b).
    """
    merged: list[list[float]] = []
    for t0, t1 in sorted((max(0.0, a), min(raw_T, b)) for a, b in keys):
        if t1 <= t0:
            continue
        if merged and t0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], t1)
        else:
            merged.append([t0, t1])
    key_len = sum(b - a for a, b in merged)
    fill_len = raw_T - key_len
    if raw_T <= budget:
        a = b = 1.0
    else:
        a = (budget - key_len) / fill_len if fill_len > 1e-9 else 0.0
        b = 1.0
        if a < FILL_RATE_MIN:
            a = FILL_RATE_MIN if fill_len > 1e-9 else 0.0
            b = (budget - a * fill_len) / key_len if key_len > 1e-9 else a
            if b < a:
                a = b = budget / raw_T
        a = min(a, 1.0)
    points = [(0.0, 0.0)]
    cursor = 0.0
    for t0, t1 in merged:
        cursor += (t0 - points[-1][0]) * a
        points.append((t0, cursor))
        cursor += (t1 - t0) * b
        points.append((t1, cursor))
    if points[-1][0] < raw_T:
        cursor += (raw_T - points[-1][0]) * a
        points.append((raw_T, cursor))
    xs = [p[0] for p in points]

    def warp(t: float) -> float:
        if t <= 0:
            return 0.0
        if t >= raw_T:
            return points[-1][1] + (t - raw_T) * a
        k = bisect_right(xs, t) - 1
        (x0, y0), (x1, y1) = points[k], points[min(k + 1, len(points) - 1)]
        return y0 if x1 <= x0 else y0 + (t - x0) * (y1 - y0) / (x1 - x0)

    return warp, b


def frame_script(result: MatchResult, frames: list[Frame], i: int, prev: int | None = None,
                 ctx: MatchCtx | None = None) -> dict:
    """
    frames[i] karesinin betigi. prev: bir onceki GOSTERILEN karenin frames icindeki konumu (surekli oynatmada);
    None -> baglamsiz (ilk kare, geri sarma, atlama). Donus JSON uyumlu dict (sema: modul basligi).
    """
    ctx = ctx or MatchCtx(result)
    frame = frames[i]
    index = frame.index
    prev_index = frames[prev].index if prev is not None and 0 <= prev < len(frames) else None
    if prev_index is not None and prev_index >= index:
        prev_index = None
    beats = _beats(ctx, index, prev_index)
    actors = _actors_for(ctx, index)
    by_pid = {a.pid: n for n, a in enumerate(actors)}

    # baslangic: bir onceki gosterilen olayin bitis pozu (yoksa zincirden onceki olayin)
    anchor = prev_index if prev_index is not None else beats[0] - 1
    snap = prev_index is None
    anchor_ev = ctx.events[anchor] if anchor >= 0 else None
    pose0, ball0 = rest_state(ctx, anchor)
    if anchor >= 0 and ctx.home_right(anchor) != ctx.home_right(index):
        # devre / uzatma degisimi: yon degisti -> yeni yonde santra dizilisi (tarayici tek seferde gecer)
        snap = True
        tmp = Choreographer(ctx, index, actors)
        tmp.tl = Timeline(_nominal_start(ctx, index, actors)[0], (CX, CY, -1))
        tmp.b_kickoff(index, restart=True)
        pose0, ball0 = _end_state(tmp)
        anchor_ev = None
    start: dict[int, tuple[float, float]] = {}
    fallback = _nominal_start(ctx, index, actors)[0]
    for n, a in enumerate(actors):
        if a.status == 1:
            start[n] = (CX + (-3.0 if a.side == "home" else 3.0), BENCH_Y)
        else:
            start[n] = pose0.get(a.pid, fallback[n])
    bx, by, hp = ball0
    holder = by_pid.get(hp, -1) if hp is not None else -1
    restart = None
    if anchor_ev is not None and anchor_ev.type is EventType.GOAL and ctx.events[index].type not in WHISTLES:
        scorer = ctx.side_of(anchor)
        restart = Scene.other(scorer) if scorer in SIDES else None
    # zaman: Normal hizda dwell_ms * FIT'e sigdir. Asil anlar (sut, pas, carpisma, kart) hizini korur; kurulum ve
    # sonrasi sikisir. Hala sigmiyorsa zincirin en eski GIZLI halkalari birakilir (son pas + sonuc kalir).
    dwell = int(frame.dwell_ms or 1800)
    budget = dwell * FIT
    while True:
        ch = _run(ctx, index, beats, start, (bx, by, holder), actors, restart, tight=budget < TIGHT_BUDGET_MS)
        tl = ch.tl
        raw_T = max(tl.last_time(), 1.0)
        warp, rate = time_warp(tl.keys, raw_T, budget)
        if rate >= KEY_RATE_MIN or len(beats) <= 1 or beats[0] == index:
            break
        beats = beats[1:]

    def ts(v: float) -> int:
        return int(round(warp(v)))

    mv = []
    for n in range(len(actors)):
        for t0, t1, x, y, ctrl in tl.segs[n]:
            row = [n, ts(t0), max(ts(t1), ts(t0) + 1), _r(x), _r(y)]
            if ctrl is not None:
                row += [_r(ctrl[0]), _r(ctrl[1])]
            mv.append(row)
    bl = [[ts(t0), max(ts(t1), ts(t0) + 1), _r(x), _r(y), _r(h), holder_, prof]
          for t0, t1, x, y, h, holder_, prof in tl.ball]
    total = min(int(budget), max(ts(raw_T), 1))
    fx = []
    for t0, dur, code, n, x, y in tl.fx:
        start_ms = min(ts(t0), total - 1)
        end_ms = ts(t0 + dur) if code in PERSISTENT_FX else min(ts(t0 + dur), total)
        fx.append([start_ms, max(end_ms - start_ms, 1), code, n, _r(x), _r(y)])
    end_ball = tl.ball_state()
    ex, ey = (tl.end_pos(end_ball[2]) if end_ball[2] >= 0 else (end_ball[0], end_ball[1]))
    ev = frame.event
    return {
        "v": SCRIPT_VERSION,
        "f": index,
        "pf": prev_index if prev_index is not None else -1,
        "T": total,
        "dw": dwell,
        "dir": 1 if ctx.home_right(index) else 0,
        "snap": 1 if snap else 0,
        "k": ev.type,
        "pl": [[a.pid, SIDE_CODE[a.side], a.name, 1 if a.gk else 0, a.energy, a.yellow, a.status] for a in actors],
        "p0": [_r(c) for n in range(len(actors)) for c in tl.start[n]],
        "p1": [_r(c) for n in range(len(actors)) for c in tl.end_pos(n)],
        "mv": mv,
        "b0": [_r(bx), _r(by), holder],
        "bl": bl,
        "b1": [_r(ex), _r(ey), end_ball[2]],
        "fx": fx,
        "hl": sorted(tl.hl),
        "cap": pitch._caption(frame),
        "min": frame.display_minute,
        "ph": frame.phase,
        "sc": [int(frame.home_score), int(frame.away_score)],
        "pen": ([int(frame.home_penalties), int(frame.away_penalties)]
                if frame.home_penalties is not None and frame.away_penalties is not None else None),
    }


def script_bytes(script: dict) -> int:
    return len(json.dumps(script, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# ===========================================================================
# [7] BILESEN VERISI VE TEK DOSYALIK ORNEK
# ===========================================================================

MODES = ("play", "pause", "hold", "jump")
# Kaleci formasi: iki takimin formasindan ve cimden ayrisan ilk aday (FM 2D'deki gibi kaleci ayri renkte)
KEEPER_KITS = ("#fdd835", "#26c6da", "#ab47bc", "#ff7043", "#ec407a", "#212121", "#8d6e63")   # beyaz yok: top beyaz
PITCH_GREEN = "#2e7d32"


def _rgb(color: str) -> tuple[int, int, int]:
    if not pitch._HEX_COLOR.match(str(color)):
        return (128, 128, 128)
    return tuple(int(color[k:k + 2], 16) for k in (1, 3, 5))


def _color_gap(a: str, b: str) -> float:
    return math.dist(_rgb(a), _rgb(b))


def keeper_colors(home_bg: str, away_bg: str) -> tuple[str, str]:
    """(ev kalecisi, deplasman kalecisi): iki formadan, cimden ve birbirinden ayrisan renkler (deterministik)."""
    avoid = [home_bg, away_bg, PITCH_GREEN]
    picked: list[str] = []
    for _ in range(2):
        best = max(KEEPER_KITS, key=lambda c: (min(_color_gap(c, x) for x in avoid + picked) if c not in picked
                                              else -1.0, -KEEPER_KITS.index(c)))
        picked.append(best)
    return picked[0], picked[1]


def component_payload(script: dict | None, mode: str, factor: float, colors, names: tuple[str, str],
                      token: str = "") -> dict:
    """
    web_assets/match_pitch.js veri paketi. mode: 'play' (surekli oynat), 'pause' (menajer durdurdu: dondur; yeni karede
    son poz), 'hold' (otomatik duraklama: yeni kareyi oynat, son pozda don), 'jump' (son poza atla: Anında / geri sarma).
    colors: ((ev zemin, ev yazi), (dep zemin, dep yazi)).
    """
    (hb, hf), (ab, af) = colors
    hk, ak = keeper_colors(hb, ab)
    return {
        "s": script,
        "m": mode if mode in MODES else "play",
        "k": max(0.0, float(factor)),
        "tok": token or (f"{script['f']}|{script['pf']}" if script else ""),
        "c": [[hb, hf, hk], [ab, af, ak]],
        "tn": [names[0], names[1]],
    }


def all_scripts(result: MatchResult, frames: list[Frame]) -> list[dict]:
    """Tum gorunur kareler, surekli oynatma sirasiyla (her kare bir oncekinden devam eder)."""
    ctx = MatchCtx(result)
    return [frame_script(result, frames, i, i - 1 if i > 0 else None, ctx) for i in range(len(frames))]


def standalone_html(result: MatchResult, frames: list[Frame], colors, js: str, css: str,
                    title: str = "OFM · Canlı maç 2D örneği") -> str:
    """
    Sahibe gosterilecek TEK DOSYALIK ornek: ayni bilesen JS'i (export default -> yerel fonksiyon), gercek motor macinin
    tum gorunur kareleri ve yorum satirlari. Ag istegi yok.
    """
    scripts = all_scripts(result, frames)
    rows = [{"m": f.display_minute, "l": f.event.label, "d": f.event.description, "t": f.event.type,
             "s": f.event.side or "", "dw": f.dwell_ms} for f in frames]
    data = {"scripts": scripts, "rows": rows, "names": [result.home.name, result.away.name],
            "colors": [list(colors[0]), list(colors[1])],
            "keepers": list(keeper_colors(colors[0][0], colors[1][0]))}
    # JSON, <script type="application/json"> icinde: < ve > kacirilir (ad / aciklama HTML olarak ayrismaz)
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
    module = js.replace("export default function", "const ofmPitch = function", 1)
    return _DEMO_TEMPLATE.format(title=escape(title), css=css, pitch_html=PITCH_HTML, data=blob, module=module,
                                 home=escape(result.home.name), away=escape(result.away.name),
                                 score=f"{result.home_score} - {result.away_score}")


PITCH_HTML = '<div class="mp-root" aria-live="off"></div>'

_DEMO_TEMPLATE = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;background:#0b0f18;color:#e8ecf4;font:14px/1.4 Tahoma,Verdana,"Segoe UI",sans-serif}}
.wrap{{max-width:980px;margin:0 auto;padding:12px 16px 32px}}
h1{{font-size:18px;margin:4px 0 2px;color:#ffd60a}}
.sub{{color:#aeb6c8;font-size:12px;margin-bottom:10px}}
.band{{display:flex;align-items:center;gap:10px;background:#141a2a;border:1px solid #2b3550;border-radius:10px;
  padding:8px 12px;margin-bottom:8px;flex-wrap:wrap}}
.band .min{{font-size:26px;font-weight:800;min-width:64px}}
.band .sc{{font-size:22px;font-weight:800;margin-left:auto}}
.banner{{background:#1b2338;border:1px solid #2b3550;border-radius:10px;padding:10px 12px;min-height:44px;margin-bottom:8px}}
.banner b{{color:#ffd60a;margin-right:8px}}
.ctl{{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}}
.ctl button,.ctl select{{background:#24305a;color:#fff;border:1px solid #3d4b80;border-radius:6px;padding:6px 12px;
  font:inherit;cursor:pointer}}
.ctl input[type=range]{{flex:1 1 200px}}
.log{{max-height:260px;overflow:auto;font-size:12px;color:#aeb6c8;border-top:1px solid #2b3550;margin-top:8px;padding-top:6px}}
.log div{{padding:2px 0}}.log div.on{{color:#fff;font-weight:700}}
{css}
</style></head><body><div class="wrap">
<h1>Canlı maç — 2D canlandırma örneği (14T)</h1>
<div class="sub">Gerçek motor maçı: {home} {score} {away}. Her hareket motorun bir olayına ve o olaydaki gerçek
oyunculara bağlıdır; konumlar temsilîdir (motor konum tutmaz). Kaçan şut kaleye girmez, kurtarış gerçek kalecide biter.</div>
<div class="band"><span class="min" id="min">0'</span><span id="ph"></span><span class="sc" id="sc">0 - 0</span></div>
<div class="banner" id="banner"><b></b><span></span></div>
<div id="pitch">{pitch_html}</div>
<div class="ctl"><button id="play">⏸ Durdur</button><select id="speed"><option value="1.4">Yavaş</option>
<option value="1" selected>Normal</option><option value="0.5">Hızlı</option><option value="0">Anında</option></select>
<input type="range" id="seek" min="0" value="0"><span id="pos"></span></div>
<div class="log" id="log"></div>
</div>
<script id="ofm-data" type="application/json">{data}</script>
<script type="module">
{module}
const DATA = JSON.parse(document.getElementById("ofm-data").textContent);
const host = document.getElementById("pitch");
let i = 0, playing = true, timer = null, lastShown = -1;
const $ = (id) => document.getElementById(id);
$("seek").max = String(DATA.scripts.length - 1);
function logRows(){{ const log = $("log"); log.textContent = "";
  for (let k = Math.max(0, i - 40); k <= i; k++) {{ const r = DATA.rows[k]; const d = document.createElement("div");
    d.textContent = r.m + "  " + r.l + " — " + r.d; if (k === i) d.className = "on"; log.prepend(d); }} }}
function show(mode){{
  const s = DATA.scripts[i], r = DATA.rows[i];
  $("min").textContent = s.min; $("ph").textContent = s.ph; $("sc").textContent = s.sc[0] + " - " + s.sc[1]
    + (s.pen ? "  (pen. " + s.pen[0] + "-" + s.pen[1] + ")" : "");
  $("banner").querySelector("b").textContent = r.m + " " + r.l; $("banner").querySelector("span").textContent = r.d;
  $("seek").value = String(i); $("pos").textContent = (i + 1) + " / " + DATA.scripts.length; logRows();
  const k = Number($("speed").value);
  ofmPitch({{ parentElement: host, setTriggerValue(){{}}, data: {{ s, m: mode, k: k || 1,
    tok: s.f + "|" + s.pf + "|" + mode, c: [[DATA.colors[0][0], DATA.colors[0][1], DATA.keepers[0]],
    [DATA.colors[1][0], DATA.colors[1][1], DATA.keepers[1]]], tn: DATA.names }} }});
  lastShown = i;
}}
function schedule(){{ clearTimeout(timer); if (!playing || i >= DATA.scripts.length - 1) return;
  const k = Number($("speed").value); const dw = DATA.rows[i].dw * (k || 0.05);
  timer = setTimeout(() => {{ i += 1; show(k === 0 ? "jump" : "play"); schedule(); }}, Math.max(60, dw)); }}
$("play").onclick = () => {{ playing = !playing; $("play").textContent = playing ? "⏸ Durdur" : "▶ Devam";
  if (playing) {{ show("play"); schedule(); }} else {{ clearTimeout(timer); show("pause"); }} }};
$("seek").oninput = () => {{ i = Number($("seek").value); show("jump"); schedule(); }};
$("speed").onchange = () => schedule();
window.ofmDemo = {{ show(k, mode) {{ i = k; playing = false; clearTimeout(timer); show(mode || "play"); }},
  frames: DATA.scripts.length, rows: DATA.rows }};
show("play"); schedule();
</script></body></html>
"""
