"""
inbox.py
========
Kalici gelen kutusu, oyun takvimi ve "suna kadar devam" (Faz 15 / 15D). CM 01/02'nin haber ekrani burada
uretilir: tarihli, kategorili, okundu / arsiv durumlu, sayfa yenilendiginde KAYBOLMAYAN mesajlar.

Uc is yapar:

1) **Takvim.** Oyunun haftasi gercek bir tarihe cevrilir: lig mac gunu CUMARTESI, hafta ici kupa CARSAMBA.
   Sezonun ilk cumartesi `game_state.season_start_date`'te sabitlenebilir; bos ise varsayilan takvim kullanilir
   (BASE_SEASON_YEAR + sezon - 1, agustosun ilk cumartesi). Arayuzun tarih cubugu: `date_bar(cm)`.

2) **Gelen kutusu.** `InboxWriter` hafta raporunu ve sezon devrini menajer basina mesajlara cevirir
   (`inbox_messages`); `Inbox` arayuzun okuma / okundu / arsiv API'sidir. Uretici tek yerdedir: hafta akisina
   (career_manager) dokunmadan mesaj eklemek / cikarmak icin bu dosya yeter.

3) **"Suna kadar devam".** `continue_until(cm, target)`: sonraki maca / transfer donemi acilisina / sezon
   sonuna kadar haftalari isler; onemli mesajda (teklif, sakatlik, yonetim) DURUR.

Kurallar
--------
* **Commit ETMEZ** (cagiranin islemine katilir). Yazmalar `InboxWriter._guarded` ile kendi savepoint'indedir:
  gelen kutusu hatasi haftayi asla bozmaz.
* **Determinizm:** hicbir yerde RNG yoktur. Mesajlar yalnizca hafta raporundan ve veritabanindan turetilir;
  mac sonucu yoluna tek cekilis bile eklenmez.
* **Parite:** modul yalnizca EKLER. `INBOX` bayragi kapaliyken (ya da `CareerManager(inbox=False)`) tek SQL bile
  atilmaz ve oyun 15D oncesiyle birebir aynidir. Hafta raporu nesnesi (WeekReport) ve `career_views`in urettigi
  satirlar DEGISMEZ: gelen kutusu onlarin YANINDA durur.
* **Guvenlik (XSS):** konu ve govde DUZ METIN saklanir ve oldugu gibi doner; bu modul HTML uretmez ve kacis
  YAPMAZ (messaging.py ile ayni kural). Arayuz her metni escape ederek cizer.
* **Sahiplik:** `inbox_messages.manager_id` NULL = **birincil koltuk** (eski tek menajer; world_managers satiri
  olmayabilir). Diger koltuklar kendi satir id'leriyle yazar. Ayrim `shortlist` / `manager_shortlist` ile aynidir.

Arayuz (15D-U) icin API
-----------------------
    # takvim
    default_season_start(season) / season_start(season, stored) / match_date(season, week, midweek=...)
    format_date(d) -> "Cumartesi 2.08.25" · long_date(d) -> "2 Ağustos 2025 Cumartesi"
    date_bar(cm) -> DateBar(season, week, date, short, long, midweek, kickoff)

    # okuma
    Inbox.for_manager(cm) ya da Inbox(db, manager_id=..., team_id=...)
        messages(category=None, *, unread_only=False, include_archived=False, kinds=None,
                 limit=50, before_id=None) -> list[InboxView]
        message(message_id) -> InboxView | None
        counts() -> InboxCounts(total, unread, by_category, unread_by_category)
        unread_total() -> int
        mark_read(ids=None, *, category=None) -> int
        mark_unread(message_id) -> bool
        archive(message_id, archived=True) -> bool
        archive_read(category=None) -> int
        latest_week_report() -> InboxView | None

    # sabitler
    CATEGORIES / CATEGORY_LABELS / CATEGORY_ORDER   (CM sekmeleri; "Tumu" = hepsi)
    KINDS / KIND_LABELS / KIND_CATEGORY / KIND_ICONS
    REF_TYPES / LINK_PAGES                          (ref_type -> nav_view slug: mesajdan tek tik)

    # yazma (motor ve sonraki paketler: 15C yonetim, 15E medya ...)
    post(db, *, manager_id, kind, subject, body="", team_id=None, season=..., week=..., ...)
    InboxWriter(cm).record_week(report) / record_season(new_season, notes_by_team)

    # suna kadar devam
    TARGETS / TARGET_LABELS / STOP_* / ContinueResult / continue_until(cm, target, ...)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

import transfer_rules
from models import INBOX_CATEGORIES, INBOX_KINDS, INBOX_REF_TYPES, InboxMessage, Team

log = logging.getLogger(__name__)

# Kural bayragi: kapaliyken gelen kutusu hic yazilmaz ve oyun 15D oncesiyle birebir aynidir
# (CareerManager(inbox=False) tek kayit icin ayni etkiyi verir).
INBOX = True

SUBJECT_MAX = 160                 # inbox_messages.subject
BODY_MAX = 2000                   # inbox_messages.body
REF_TYPE_MAX = 16
ELLIPSIS = "…"


# ===========================================================================
# 1) TAKVIM — hafta -> gercek tarih
# ===========================================================================

# Sezon 1 = 2025-26. Sezonun ilk lig mac gunu, agustosun ilk CUMARTESI'sidir.
BASE_SEASON_YEAR = 2025
LEAGUE_WEEKDAY = 5                # Cumartesi (date.weekday(): Pazartesi 0)
CUP_WEEKDAY = 2                   # Carsamba (ayni haftanin lig gununden 3 gun once)
SEASON_START_MONTH = 8

DAY_NAMES = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
MONTH_NAMES = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
               "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
LEAGUE_KICKOFF, CUP_KICKOFF = "19:00", "21:45"


def default_season_start(season: int) -> date:
    """Sezonun varsayilan ilk lig mac gunu: (BASE_SEASON_YEAR + sezon - 1) agustosunun ilk cumartesi."""
    year = BASE_SEASON_YEAR + max(1, int(season or 1)) - 1
    first = date(year, SEASON_START_MONTH, 1)
    return first + timedelta(days=(LEAGUE_WEEKDAY - first.weekday()) % 7)


def season_start(season: int, stored: date | None = None) -> date:
    """Sezonun ilk lig mac gunu: kayitta sabitlenmisse o, yoksa varsayilan takvim."""
    if isinstance(stored, datetime):
        stored = stored.date()
    return stored if isinstance(stored, date) else default_season_start(season)


def match_date(season: int, week: int, *, midweek: bool = False, start: date | None = None) -> date:
    """
    Sezonun `week`. haftasinin mac gunu. Lig: cumartesi (sezon basi + 7 x (hafta-1)).
    Hafta ici kupa: AYNI haftanin carsambasi (lig gununden 3 gun once) -- oyun da kupayi ligden once oynatir.
    """
    base = season_start(season, start) + timedelta(days=7 * (max(1, int(week or 1)) - 1))
    return base - timedelta(days=(LEAGUE_WEEKDAY - CUP_WEEKDAY)) if midweek else base


def week_dates(season: int, week: int, start: date | None = None) -> tuple[date, date]:
    """(hafta ici kupa carsambasi, lig cumartesi)."""
    return (match_date(season, week, midweek=True, start=start),
            match_date(season, week, midweek=False, start=start))


def day_name(moment: date) -> str:
    return DAY_NAMES[moment.weekday()]


def format_date(moment: date | None) -> str:
    """CM tarih cubugu bicimi: "Cumartesi 2.08.25"."""
    if moment is None:
        return ""
    return f"{day_name(moment)} {moment.day}.{moment.month:02d}.{moment.year % 100:02d}"


def short_date(moment: date | None) -> str:
    """Liste sutunu: "2.08.25" (gun adi olmadan)."""
    if moment is None:
        return ""
    return f"{moment.day}.{moment.month:02d}.{moment.year % 100:02d}"


def long_date(moment: date | None) -> str:
    """Mesaj basligi: "2 Ağustos 2025 Cumartesi"."""
    if moment is None:
        return ""
    return f"{moment.day} {MONTH_NAMES[moment.month - 1]} {moment.year} {day_name(moment)}"


@dataclass(frozen=True)
class DateBar:
    """Arayuzun tarih cubugu (CM: "Wednesday 7.11.01"). Oynanacak haftanin mac gunu."""
    season: int
    week: int
    date: date
    short: str
    long: str
    midweek: bool                  # sirada hafta ici kupa gunu var mi
    kickoff: str
    label: str                     # "Cumartesi 2.08.25 · Sezon 1, 1. hafta"


def date_bar(cm, *, midweek: bool | None = None) -> DateBar:
    """
    Oynanacak haftanin tarih cubugu. midweek None: bu hafta oynanmamis hafta ici kupa maci varsa carsamba.
    Yalnizca okur (state ve kupa takvimi); hicbir sey yazmaz.
    """
    season, week = int(cm.season), int(cm.current_week)
    if midweek is None:
        midweek = _midweek_pending(cm, week)
    when = match_date(season, week, midweek=bool(midweek), start=_stored_start(cm))
    return DateBar(
        season=season, week=week, date=when, short=format_date(when), long=long_date(when),
        midweek=bool(midweek), kickoff=CUP_KICKOFF if midweek else LEAGUE_KICKOFF,
        label=f"{format_date(when)} · Sezon {season}, {week}. hafta",
    )


def _stored_start(cm) -> date | None:
    state = getattr(cm, "state", None)
    return getattr(state, "season_start_date", None) if state is not None else None


def _midweek_pending(cm, week: int) -> bool:
    """Bu haftanin Devler Arenasi mac gunu henuz oynanmadi mi (kura beklese de sayilir)."""
    try:
        cup = cm.tournaments
        current = cup.current()
        return current is not None and bool(cup.matchday_due(current, week))
    except Exception:                     # takvim cizimi asla sayfayi dusurmez (kupa kurulmamis olabilir)
        return False


# ===========================================================================
# 2) KATEGORI, TUR VE BAGLANTI SABITLERI
# ===========================================================================

CAT_MESSAGE, CAT_COMPETITION, CAT_INJURY = "MESSAGE", "COMPETITION", "INJURY"
CATEGORIES: tuple[str, ...] = INBOX_CATEGORIES
CATEGORY_ORDER: tuple[str, ...] = (CAT_MESSAGE, CAT_COMPETITION, CAT_INJURY)
# CM 01/02: All / Messages / Competitions / Injuries and Bans ("Tumu" kategori degil, hepsinin birlesimidir)
CATEGORY_LABELS: dict[str, str] = {
    CAT_MESSAGE: "Mesajlar",
    CAT_COMPETITION: "Müsabakalar",
    CAT_INJURY: "Sakatlık ve Cezalar",
}
ALL_LABEL = "Tümü"

KIND_WEEK_REPORT, KIND_MATCH_RESULT, KIND_MATCH_REPORT = "WEEK_REPORT", "MATCH_RESULT", "MATCH_REPORT"
KIND_INJURY, KIND_BAN = "INJURY", "BAN"
KIND_TRANSFER, KIND_TRANSFER_OFFER, KIND_SCOUT_REPORT = "TRANSFER", "TRANSFER_OFFER", "SCOUT_REPORT"
KIND_CONTRACT, KIND_CONTRACT_EXPIRING, KIND_BOARD = "CONTRACT", "CONTRACT_EXPIRING", "BOARD"
KIND_AWARD, KIND_SQUAD, KIND_FINANCE = "AWARD", "SQUAD", "FINANCE"
KIND_YOUTH, KIND_SEASON, KIND_NEWS = "YOUTH", "SEASON", "NEWS"
KINDS: tuple[str, ...] = INBOX_KINDS

KIND_CATEGORY: dict[str, str] = {
    KIND_WEEK_REPORT: CAT_COMPETITION,
    KIND_MATCH_RESULT: CAT_COMPETITION,
    KIND_MATCH_REPORT: CAT_COMPETITION,
    KIND_AWARD: CAT_COMPETITION,
    KIND_SEASON: CAT_COMPETITION,
    KIND_NEWS: CAT_COMPETITION,
    KIND_INJURY: CAT_INJURY,
    KIND_BAN: CAT_INJURY,
    KIND_TRANSFER: CAT_MESSAGE,
    KIND_TRANSFER_OFFER: CAT_MESSAGE,
    KIND_SCOUT_REPORT: CAT_MESSAGE,
    KIND_CONTRACT: CAT_MESSAGE,
    KIND_CONTRACT_EXPIRING: CAT_MESSAGE,
    KIND_BOARD: CAT_MESSAGE,
    KIND_SQUAD: CAT_MESSAGE,
    KIND_FINANCE: CAT_MESSAGE,
    KIND_YOUTH: CAT_MESSAGE,
}

KIND_LABELS: dict[str, str] = {
    KIND_WEEK_REPORT: "Hafta raporu",
    KIND_MATCH_RESULT: "Maç sonucu",
    KIND_MATCH_REPORT: "Maç raporu",
    KIND_INJURY: "Sakatlık",
    KIND_BAN: "Ceza",
    KIND_TRANSFER: "Transfer",
    KIND_TRANSFER_OFFER: "Transfer teklifi",
    KIND_SCOUT_REPORT: "Gözlemci raporu",
    KIND_CONTRACT: "Sözleşme",
    KIND_CONTRACT_EXPIRING: "Sözleşme bitiyor",
    KIND_BOARD: "Yönetim",
    KIND_AWARD: "Ödül",
    KIND_SQUAD: "Kadro",
    KIND_FINANCE: "Finans",
    KIND_YOUTH: "Akademi",
    KIND_SEASON: "Sezon",
    KIND_NEWS: "Haber",
}

# Liste satirinin basindaki isaret (CM'de kucuk simge sutunu). Arayuz isterse kendi setini kullanir.
KIND_ICONS: dict[str, str] = {
    KIND_WEEK_REPORT: "📋", KIND_MATCH_RESULT: "⚽", KIND_MATCH_REPORT: "📝", KIND_INJURY: "🩹",
    KIND_BAN: "🟥", KIND_TRANSFER: "🔄", KIND_TRANSFER_OFFER: "💰", KIND_SCOUT_REPORT: "🔍",
    KIND_CONTRACT: "✍️", KIND_CONTRACT_EXPIRING: "⏳", KIND_BOARD: "🏛️", KIND_AWARD: "🏆",
    KIND_SQUAD: "📋", KIND_FINANCE: "💶", KIND_YOUTH: "🎓", KIND_SEASON: "📅", KIND_NEWS: "📰",
}

# Onemli sayilan turler: "suna kadar devam" bunlarda durur (kart: teklif, sakatlik, yonetim).
IMPORTANT_KINDS: frozenset[str] = frozenset({
    KIND_INJURY, KIND_BAN, KIND_TRANSFER_OFFER, KIND_BOARD, KIND_CONTRACT_EXPIRING,
})
# Transfer masasi / sozlesme notlari DUZ METINDIR (13H transfer_desk, 15A ContractCycle uretir; ikisi de
# C seridinin baska dosyalarinda). Notun turu asagidaki kelimelerle siniflanir -- heuristiktir: yeni bir not
# metni eklenirse listeye de eklenir (classify_desk_note testte sabitlenir).
CONTRACT_MARKERS: tuple[str, ...] = ("sözleşme", "yenile", "serbest kal", "fesh", "bosman", "tazminat")
EXPIRY_MARKERS: tuple[str, ...] = ("bitiyor", "bitenler", "yenilemezsen", "serbest kal", "ön sözleşme")
OFFER_MARKERS: tuple[str, ...] = ("teklif", "pazarlık", "görüşme", "bonservis", "serbest kalma bedeli",
                                  "taksit", "prim")


def classify_desk_note(text: str) -> str:
    """
    Masa notunun gelen kutusu turu. Sozlesme notu > teklif notu > duz transfer notu. "Sozlesmesi bitiyor" ve
    "teklif" turleri ONEMLI'dir: "suna kadar devam" onlarda durur.
    """
    low = str(text or "").lower()
    if any(marker in low for marker in CONTRACT_MARKERS):
        return KIND_CONTRACT_EXPIRING if any(m in low for m in EXPIRY_MARKERS) else KIND_CONTRACT
    if any(marker in low for marker in OFFER_MARKERS):
        return KIND_TRANSFER_OFFER
    return KIND_TRANSFER

REF_PLAYER, REF_TEAM, REF_FIXTURE, REF_DEAL, REF_TALK = "PLAYER", "TEAM", "FIXTURE", "DEAL", "TALK"
REF_OFFER, REF_LEAGUE, REF_TOURNAMENT, REF_NEWS, REF_REPORT = "OFFER", "LEAGUE", "TOURNAMENT", "NEWS", "REPORT"
REF_TYPES: tuple[str, ...] = INBOX_REF_TYPES

# Mesajdan TEK TIKLA acilacak sayfa: ref_type -> nav_view slug'i (nav_view U seridinde; burada yalnizca
# metin sabiti tutulur, bagimlilik yok). ref_id o sayfanin parametresidir (oyuncu / kulup / lig id).
LINK_PAGES: dict[str, str] = {
    REF_PLAYER: "oyuncu",             # nav_view.PLAYER      ?sayfa=oyuncu&id=<ref_id>
    REF_TEAM: "takim",                # nav_view.CLUB_PAGE   ?sayfa=takim&id=<ref_id>
    REF_FIXTURE: "fikstur",           # nav_view.FIXTURES
    REF_DEAL: "transfer",             # nav_view.TRANSFER    (dosya: transfer_centre_view.open_file(ref_id))
    REF_TALK: "transfer",             # nav_view.TRANSFER    (sozlesme masasi)
    REF_OFFER: "mesajlar",            # nav_view.INBOX       (Teklifler ve Mesajlar)
    REF_LEAGUE: "puan-durumu",        # nav_view.TABLE       ?sayfa=puan-durumu&lig=<ref_id>
    REF_TOURNAMENT: "devler-arenasi",  # nav_view.ARENA
    REF_NEWS: "haberler",             # nav_view.NEWS
    REF_REPORT: "fikstur",            # nav_view.FIXTURES    (hafta raporu)
}
# ref_id'si sayfa parametresi olan hedefler (digerlerinde ref_id yalnizca baglamdir)
PARAM_REFS: frozenset[str] = frozenset({REF_PLAYER, REF_TEAM, REF_LEAGUE})

# Gurultu tavani (kart: 38 haftalik sezonda menajer basina 150-400 mesaj). Hafta basina ust sinirlar:
MAX_DESK_MESSAGES = 6             # transfer masasi / sozlesme notu
MAX_CONCERN_MESSAGES = 3          # kaygi + maas talebi
MAX_INJURY_MESSAGES = 6           # kendi kulubunun sakatlik + cezalari
MAX_RIVAL_INJURY = 1              # ayni ligdeki rakiplerin sakatlik / cezalari (CM'nin "Injuries and Bans" sekmesi
#                                   rakipleri de gosterir: "Taffarel banned for one match ... Lille")
MAX_TRANSFER_MESSAGES = 4         # kulubun kendi transferleri
MAX_WEEK_MESSAGES = 24            # bir menajer icin tek haftada yazilacak mutlak tavan


# ===========================================================================
# 3) GORUNUM MODELLERI
# ===========================================================================

@dataclass(frozen=True)
class InboxView:
    """Arayuze giden tek mesaj. Metinler DUZ METIN (arayuz escape eder)."""
    id: int
    manager_id: int | None
    team_id: int | None
    category: str
    category_label: str
    kind: str
    kind_label: str
    icon: str
    season: int
    week: int
    career_week: int
    date: date | None
    date_label: str               # "Cumartesi 2.08.25"
    date_long: str                # "2 Ağustos 2025 Cumartesi"
    subject: str
    body: str
    lines: list                   # yapisal ek (hafta raporu satirlari: [[tur, metin], ...])
    ref_type: str | None
    ref_id: int | None
    page: str | None              # nav_view slug'i (LINK_PAGES) -- "tek tik" hedefi
    page_param: int | None        # o sayfanin parametresi (yoksa None)
    important: bool
    read: bool
    archived: bool
    created_at: datetime | None

    @property
    def link(self) -> tuple[str, int | None] | None:
        """(sayfa slug'i, parametre) ya da None (baglantisiz mesaj)."""
        return None if self.page is None else (self.page, self.page_param)


@dataclass(frozen=True)
class InboxCounts:
    total: int
    unread: int
    by_category: dict[str, int]
    unread_by_category: dict[str, int]


# ===========================================================================
# 4) YARDIMCILAR
# ===========================================================================

def utc_now() -> datetime:
    """Modul saati (testler monkeypatch ile degistirir)."""
    return datetime.now(timezone.utc)


def _clean(text) -> str:
    """Duz metin: CRLF -> LF, gorunmez denetim karakterleri silinir, bas / son bosluk kirpilir."""
    raw = "" if text is None else str(text)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in raw if c == "\n" or c == "\t" or (c >= " " and c != "\x7f")).strip()


def _line(text) -> str:
    """Tek satir duz metin (konu)."""
    raw = "" if text is None else str(text)
    return " ".join(raw.replace("\x00", " ").split())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + ELLIPSIS


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def category_of(kind: str) -> str:
    return KIND_CATEGORY.get(str(kind), CAT_MESSAGE)


def is_important(kind: str) -> bool:
    return str(kind) in IMPORTANT_KINDS


def view_of(row: InboxMessage) -> InboxView:
    """ORM satiri -> InboxView (arayuzun tek tipi)."""
    when = _as_date(row.game_date)
    kind, category = str(row.kind), str(row.category)
    page = LINK_PAGES.get(str(row.ref_type)) if row.ref_type else None
    param = int(row.ref_id) if (row.ref_id is not None and str(row.ref_type) in PARAM_REFS) else None
    return InboxView(
        id=int(row.id), manager_id=row.manager_id, team_id=row.team_id,
        category=category, category_label=CATEGORY_LABELS.get(category, category),
        kind=kind, kind_label=KIND_LABELS.get(kind, kind), icon=KIND_ICONS.get(kind, "•"),
        season=int(row.season), week=int(row.week), career_week=int(row.career_week or 1),
        date=when, date_label=format_date(when), date_long=long_date(when),
        subject=str(row.subject), body=str(row.body or ""), lines=list(row.lines or []),
        ref_type=row.ref_type, ref_id=row.ref_id, page=page, page_param=param,
        important=bool(row.important), read=row.read_at is not None, archived=bool(row.archived),
        created_at=_as_utc(row.created_at),
    )


# ===========================================================================
# 5) YAZMA
# ===========================================================================

def post(
    db,
    *,
    manager_id: int | None,
    kind: str,
    subject: str,
    body: str = "",
    team_id: int | None = None,
    season: int = 1,
    week: int = 1,
    career_week: int = 1,
    game_date: date | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    important: bool | None = None,
    lines: Sequence | None = None,
    category: str | None = None,
    flush: bool = False,
) -> InboxMessage | None:
    """
    Gelen kutusuna tek mesaj yazar (commit ETMEZ). Sonraki paketler (15C yonetim, 15E medya, 16F gozlem) bu
    fonksiyonu cagirir. Bos konu ya da bilinmeyen tur -> None (mesaj yazilmaz, hata firlatilmaz: gelen kutusu
    asla oyunu durdurmaz). manager_id None = birincil koltuk.
    """
    kind = str(kind)
    if kind not in KIND_CATEGORY:
        log.warning("Bilinmeyen gelen kutusu türü: %s", kind)
        return None
    subject = _clip(_line(subject), SUBJECT_MAX)
    if not subject:
        return None
    category = str(category or category_of(kind))
    if category not in CATEGORIES:
        category = CAT_MESSAGE
    if ref_type is not None:
        ref_type = _line(ref_type).upper()[:REF_TYPE_MAX] or None
    row = InboxMessage(
        manager_id=manager_id if _is_id(manager_id) else None,
        team_id=team_id if _is_id(team_id) else None,
        category=category, kind=kind,
        season=max(1, int(season or 1)), week=max(1, int(week or 1)), career_week=max(1, int(career_week or 1)),
        game_date=_as_date(game_date),
        subject=subject, body=_clip(_clean(body), BODY_MAX),
        ref_type=ref_type, ref_id=int(ref_id) if _is_id(ref_id) else None,
        important=bool(is_important(kind) if important is None else important),
        lines=[list(item) for item in (lines or [])],
        created_at=utc_now(),
    )
    db.add(row)
    if flush:
        db.flush()
    return row


# ===========================================================================
# 6) OKUMA (arayuz)
# ===========================================================================

LIST_LIMIT_DEFAULT, LIST_LIMIT_MAX = 50, 500


class Inbox:
    """
    Bir menajerin gelen kutusu. manager_id None = birincil koltuk (eski tek menajer). Commit ETMEZ.
    Yazmaz -- yalnizca okundu / arsiv durumunu gunceller.
    """

    def __init__(self, db, manager_id: int | None = None, team_id: int | None = None) -> None:
        self.db = db
        self.manager_id = manager_id if _is_id(manager_id) else None
        self.team_id = team_id if _is_id(team_id) else None

    @classmethod
    def for_manager(cls, cm) -> Inbox:
        """Oynatan menajerin gelen kutusu (birincil koltuk -> manager_id None)."""
        return cls(cm.db, manager_id=manager_id_for(cm), team_id=cm._acting_team_id())

    # ------------------------------------------------------------------ sorgular

    def _owned(self, stmt):
        return stmt.where(InboxMessage.manager_id.is_(None) if self.manager_id is None
                          else InboxMessage.manager_id == self.manager_id)

    def messages(self, category: str | None = None, *, unread_only: bool = False,
                 include_archived: bool = False, kinds: Iterable[str] | None = None,
                 important_only: bool = False, limit: int = LIST_LIMIT_DEFAULT,
                 before_id: int | None = None) -> list[InboxView]:
        """En yeni ustte. category None / "" -> CM'nin "Tümü" sekmesi. before_id: sayfalama (daha eskiler)."""
        stmt = self._owned(select(InboxMessage))
        if category:
            stmt = stmt.where(InboxMessage.category == str(category))
        if not include_archived:
            stmt = stmt.where(InboxMessage.archived.is_(False))
        if unread_only:
            stmt = stmt.where(InboxMessage.read_at.is_(None))
        if important_only:
            stmt = stmt.where(InboxMessage.important.is_(True))
        if kinds is not None:
            wanted = [str(k) for k in kinds]
            if not wanted:
                return []
            stmt = stmt.where(InboxMessage.kind.in_(wanted))
        if _is_id(before_id):
            stmt = stmt.where(InboxMessage.id < int(before_id))
        stmt = stmt.order_by(InboxMessage.id.desc()).limit(max(1, min(LIST_LIMIT_MAX, int(limit or 1))))
        self.db.flush()
        return [view_of(row) for row in self.db.scalars(stmt)]

    def message(self, message_id: int) -> InboxView | None:
        row = self._row(message_id)
        return view_of(row) if row is not None else None

    def _row(self, message_id: int) -> InboxMessage | None:
        if not _is_id(message_id):
            return None
        row = self.db.get(InboxMessage, int(message_id))
        if row is None:
            return None
        return row if (row.manager_id or None) == self.manager_id else None       # baskasinin mesaji gorunmez

    def counts(self, *, include_archived: bool = False) -> InboxCounts:
        """Kategori basina toplam ve okunmamis sayilar (kenar cubugu rozeti + sekme basliklari): TEK sorgu."""
        stmt = self._owned(select(
            InboxMessage.category, func.count(),
            func.count().filter(InboxMessage.read_at.is_(None)),
        ))
        if not include_archived:
            stmt = stmt.where(InboxMessage.archived.is_(False))
        self.db.flush()
        rows = self.db.execute(stmt.group_by(InboxMessage.category)).all()
        by_category = {c: 0 for c in CATEGORY_ORDER}
        unread_by_category = {c: 0 for c in CATEGORY_ORDER}
        for category, total, unread in rows:
            by_category[str(category)] = int(total or 0)
            unread_by_category[str(category)] = int(unread or 0)
        return InboxCounts(total=sum(by_category.values()), unread=sum(unread_by_category.values()),
                           by_category=by_category, unread_by_category=unread_by_category)

    def unread_total(self) -> int:
        return self.counts().unread

    def latest_week_report(self) -> InboxView | None:
        """Son hafta raporu mesaji (arsivlenmis olsa da): sayfa yenilense de rapor burada."""
        rows = self.messages(kinds=(KIND_WEEK_REPORT,), include_archived=True, limit=1)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ durum

    def mark_read(self, ids: Iterable[int] | int | None = None, *, category: str | None = None,
                  read: bool = True) -> int:
        """ids None: (kategorideki) tum okunmamislar. Isaretlenen satir sayisi."""
        stmt = self._owned(update(InboxMessage))
        if read:
            stmt = stmt.where(InboxMessage.read_at.is_(None))
        if category:
            stmt = stmt.where(InboxMessage.category == str(category))
        if ids is not None:
            wanted = [int(i) for i in ([ids] if _is_id(ids) else ids) if _is_id(i)]
            if not wanted:
                return 0
            stmt = stmt.where(InboxMessage.id.in_(sorted(set(wanted))))
        result = self.db.execute(
            stmt.values(read_at=utc_now() if read else None).execution_options(synchronize_session="fetch"))
        return int(result.rowcount or 0)

    def mark_unread(self, message_id: int) -> bool:
        row = self._row(message_id)
        if row is None:
            return False
        row.read_at = None
        self.db.flush()
        return True

    def archive(self, message_id: int, archived: bool = True) -> bool:
        """Mesaji arsivler / arsivden cikarir (satir silinmez: CM'de de haber kaybolmaz)."""
        row = self._row(message_id)
        if row is None:
            return False
        row.archived = bool(archived)
        if archived and row.read_at is None:
            row.read_at = utc_now()
        self.db.flush()
        return True

    def archive_read(self, category: str | None = None) -> int:
        """Okunmus mesajlari topluca arsivler ("Temizle" dugmesi)."""
        stmt = self._owned(update(InboxMessage)).where(InboxMessage.archived.is_(False),
                                                       InboxMessage.read_at.isnot(None))
        if category:
            stmt = stmt.where(InboxMessage.category == str(category))
        result = self.db.execute(stmt.values(archived=True).execution_options(synchronize_session="fetch"))
        return int(result.rowcount or 0)


def manager_id_for(cm, team_id: int | None = None) -> int | None:
    """
    Gelen kutusu sahibi: birincil koltuk -> None, diger koltuk -> world_managers.id.
    team_id verilirse o kulubun koltugu (hafta raporu tum insan kulupleri icin yazar).
    """
    if team_id is None:
        seat = cm.acting_seat                        # property
        if seat is None or seat.is_primary:
            return None
        return seat.id if _is_id(seat.id) else None
    if team_id == cm.state.user_team_id:
        return None                          # birincil koltugun kulubu
    seat = cm.seats.by_team(team_id)
    return seat.id if seat is not None and _is_id(seat.id) else None


# ===========================================================================
# 7) URETICI — hafta raporu -> mesajlar
# ===========================================================================

def _assistant_note(note: object) -> str:
    """career_manager bazi dizilis notlarini zaten "Asistan: " onekiyle yaziyor; onek iki kez cikmasin."""
    text = str(note).strip()
    return text[len("Asistan: "):].strip() if text.startswith("Asistan: ") else text


def _score_text(result) -> str:
    """"A 1 - 1 B (uzt., pen. 4-3)" (career_views.match_score_text ile ayni bicim; bagimlilik yok)."""
    text = f"{result.home.name} {result.home_score} - {result.away_score} {result.away.name}"
    extras = []
    if getattr(result, "extra_time", False):
        extras.append("uzt.")
    if getattr(result, "shootout", None) is not None:
        extras.append(f"pen. {result.home_penalties}-{result.away_penalties}")
    return text + (f" ({', '.join(extras)})" if extras else "")


def _outcome_word(result, team_id: int | None) -> str:
    if team_id is None:
        return "Sonuç"
    mine, theirs = ((result.home_score, result.away_score) if result.home.id == team_id
                    else (result.away_score, result.home_score))
    return "Galibiyet" if mine > theirs else ("Beraberlik" if mine == theirs else "Mağlubiyet")


def _scorer_text(result) -> str:
    parts = []
    for side in (result.home, result.away):
        names = [f"{p.name} ({p.goals})" if p.goals > 1 else p.name
                 for p in side.players if getattr(p, "goals", 0) > 0]
        if names:
            parts.append(f"{side.name}: " + ", ".join(names))
    return " · ".join(parts)


class InboxWriter:
    """
    Hafta raporunu ve sezon devrini menajer basina gelen kutusu mesajlarina cevirir. CareerManager hafta
    sonunda bir kez cagirir. RNG KULLANMAZ; her adim kendi savepoint'indedir (hata haftayi bozmaz).
    """

    def __init__(self, cm) -> None:
        self.cm = cm
        self.db = cm.db
        self._league_names: dict[int, frozenset[str]] = {}       # lig -> kulup adlari (hafta basina tek sorgu)

    # ------------------------------------------------------------------ ortak

    @property
    def _start(self) -> date | None:
        return _stored_start(self.cm)

    def _guarded(self, label: str, fn, *args) -> bool:
        """Adim kendi savepoint'inde: gelen kutusu hatasi hafta / devir islemini bozmaz."""
        self.db.flush()
        try:
            with self.db.begin_nested():
                fn(*args)
            return True
        except (SQLAlchemyError, ValueError, TypeError, AttributeError) as exc:
            log.exception("Gelen kutusu adımı başarısız (%s): %s", label, exc)
            return False

    def _league_club_names(self, team: Team) -> frozenset[str]:
        """Kulubun ligindeki kulup adlari (hafta basina lig basina TEK sorgu; PlayerNote'ta kulup id yok)."""
        league_id = team.league_id
        if league_id is None:
            return frozenset()
        names = self._league_names.get(league_id)
        if names is None:
            names = frozenset(self.db.scalars(select(Team.name).where(Team.league_id == league_id)))
            self._league_names[league_id] = names
        return names

    def _human_clubs(self) -> list[tuple[int, Team]]:
        """(kulup id, Team) -- insan menajerlerin kulupleri, id sirasinda (deterministik)."""
        ids = sorted(i for i in self.cm.human_team_ids() if _is_id(i))
        if not ids:
            return []
        rows = {t.id: t for t in self.db.scalars(select(Team).where(Team.id.in_(ids)))}
        return [(i, rows[i]) for i in ids if i in rows]

    # ------------------------------------------------------------------ hafta

    def record_week(self, report) -> int:
        """Haftanin (ya da yalnizca hafta ici kupanin) mesajlarini yazar. Yazilan mesaj sayisini dondurur."""
        if report is None or not INBOX:
            return 0
        written = 0
        for team_id, team in self._human_clubs():
            box = _Basket(manager_id=manager_id_for(self.cm, team_id), team_id=team_id)
            if self._guarded("week", self._write_week, report, team, box):
                written += box.written
        return written

    def _write_week(self, report, team: Team, box: _Basket) -> None:
        self._club_week(report, team, box)
        self._commit_basket(box)

    def _commit_basket(self, box: _Basket) -> None:
        """
        Sepetteki mesajlari tavana kirpip yazar. Tavan asilirsa once KIRPILABILIR (onemsiz, sabitlenmemis)
        mesajlar dusurulur; hafta raporu ve onemli mesajlar her zaman yazilir. Yazim sirasi eklenme sirasidir.
        """
        rows = list(enumerate(box.rows))
        if len(rows) > MAX_WEEK_MESSAGES:
            ranked = [r for r in rows if r[1][0]] + [r for r in rows if not r[1][0]]
            rows = sorted(ranked[:MAX_WEEK_MESSAGES], key=lambda pair: pair[0])
        for _index, (_pinned, values) in rows:
            post(self.db, manager_id=box.manager_id, team_id=box.team_id, **values)
        box.written = len(rows)

    def _club_week(self, report, team: Team, box: _Basket) -> None:
        view = report.view_for(team.id)
        season, week = int(report.season), int(report.week)
        cw = int(self.cm.career_week)
        midweek_only = bool(getattr(report, "midweek_only", False))
        league_day, cup_day = (match_date(season, week, start=self._start),
                               match_date(season, week, midweek=True, start=self._start))
        add = lambda **kw: box.add(season=season, week=week, career_week=cw, **kw)   # noqa: E731

        # --- 1) kulubun maclari (lig cumartesi, hafta ici kupa carsamba)
        if view.user_cup_result is not None:
            add(kind=KIND_MATCH_RESULT, game_date=cup_day,
                **self._match_fields(view.user_cup_result, team, report, view, cup=True, week=week))
        if view.user_result is not None:
            add(kind=KIND_MATCH_RESULT, game_date=league_day,
                **self._match_fields(view.user_result, team, report, view, cup=False, week=week))

        # --- 2) sakatlik ve cezalar (kulubun oyunculari): CM'de ayri sekme, her biri ayri mesaj
        hurt = [n for n in report.injuries if n.team_name == team.name][:MAX_INJURY_MESSAGES]
        for note in hurt:
            add(kind=KIND_INJURY, game_date=league_day, subject=f"Sakatlık: {note.player_name}",
                body=f"{note.player_name} sakatlandı — {note.detail}. Kadro sayfasından yerine bakabilirsin.",
                ref_type=REF_PLAYER, ref_id=note.player_id, important=True)
        banned = [n for n in report.suspensions if n.team_name == team.name][:MAX_INJURY_MESSAGES]
        for note in banned:
            add(kind=KIND_BAN, game_date=league_day, subject=f"Ceza: {note.player_name}",
                body=f"{note.player_name} cezalı — {note.detail}.",
                ref_type=REF_PLAYER, ref_id=note.player_id, important=True)
        # ayni ligdeki rakipler (CM'nin "Injuries and Bans" sekmesi rakipleri de yazar); ONEMLI DEGIL: "devam" durmaz
        rivals = self._league_club_names(team) - {team.name}
        for kind, notes in ((KIND_INJURY, report.injuries), (KIND_BAN, report.suspensions)):
            word = "sakatlandı" if kind == KIND_INJURY else "ceza aldı"
            for note in [n for n in notes if n.team_name in rivals][:MAX_RIVAL_INJURY]:
                add(kind=kind, game_date=league_day,
                    subject=f"Ligde {'sakatlık' if kind == KIND_INJURY else 'ceza'}: "
                            f"{note.player_name} ({note.team_name})",
                    body=f"{note.player_name} ({note.team_name}) {word} — {note.detail}.",
                    ref_type=REF_PLAYER, ref_id=note.player_id, important=False)

        # --- 3) transfer masasi ve sozlesme notlari (13H / 15A ureticileri)
        for note in list(view.transfer_notes)[:MAX_DESK_MESSAGES]:
            text = _clean(note)
            if not text:
                continue
            kind = classify_desk_note(text)
            add(kind=kind, game_date=league_day, subject=_clip(text, 90), body=text,
                ref_type=REF_TALK if kind in (KIND_CONTRACT, KIND_CONTRACT_EXPIRING) else REF_DEAL)

        # --- 4) kulubun tamamlanan transferleri
        moves = [n for n in report.transfers if team.id in (n.from_team_id, n.to_team_id)][:MAX_TRANSFER_MESSAGES]
        for news in moves:
            joined = news.to_team_id == team.id
            add(kind=KIND_TRANSFER, game_date=league_day,
                subject=("Kadroya katıldı: " if joined else "Kulüpten ayrıldı: ") + str(news.player_name),
                body=news.describe(), ref_type=REF_PLAYER, ref_id=news.player_id)

        # --- 5) oyuncu kaygilari ve maas talepleri (sozlesme uyarisi: CM "contract expiring")
        concerns = list(view.concern_notes)[:MAX_CONCERN_MESSAGES]
        for note in concerns:
            add(kind=KIND_SQUAD, game_date=league_day, subject=f"{note.player_name} memnun değil",
                body=f"{note.player_name}: {note.detail}", ref_type=REF_PLAYER, ref_id=note.player_id)
        for note in list(view.wage_demands)[:MAX_CONCERN_MESSAGES]:
            add(kind=KIND_CONTRACT_EXPIRING, game_date=league_day,
                subject=f"Yeni sözleşme isteği: {note.player_name}",
                body=f"{note.player_name} — {note.detail}. Kadro sayfasından yanıtla.",
                ref_type=REF_PLAYER, ref_id=note.player_id, important=True)

        # --- 6) akademi ve genc girisi (tek ozet)
        intake = list(view.youth_intake)
        if intake or view.academy_notes:
            body = "; ".join([f"{n.player_name} ({n.detail})" for n in intake[:6]]
                             + [str(n) for n in list(view.academy_notes)[:4]])
            add(kind=KIND_YOUTH, game_date=league_day,
                subject=f"Akademi: {len(intake)} yeni oyuncu" if intake else "Akademi raporu", body=body)

        # --- 7) sezon onurlari / oduller (dunya capinda; menajerin kulubu ilgilendirmese de CM'de haberdir)
        for note in list(report.honours_notes)[:4]:
            add(kind=KIND_AWARD, game_date=league_day, subject=_clip(str(note), 90), body=str(note),
                ref_type=REF_NEWS)

        # --- 8) HAFTA RAPORU (eskiden st.session_state["last_week_lines"]): kalici, tek mesaj
        add(pin=True, kind=KIND_WEEK_REPORT, game_date=cup_day if midweek_only else league_day,
            subject=self._report_subject(view, season, week, midweek_only),
            body=self._report_body(view, report, team, midweek_only),
            lines=self._report_lines(view, report, team, midweek_only),
            ref_type=REF_REPORT, ref_id=None)

    # ------------------------------------------------------------------ mesaj govdeleri

    def _match_fields(self, result, team: Team, report, view, *, cup: bool, week: int) -> dict:
        where = "Devler Arenası" if cup else "Lig"
        outcome = _outcome_word(result, team.id)
        body_parts = [f"{where} · {week}. hafta · {_score_text(result)}", outcome + "."]
        scorers = _scorer_text(result)
        if scorers:
            body_parts.append("Goller — " + scorers)
        best = getattr(result, "man_of_the_match", None)
        if best is not None:
            body_parts.append(f"Maçın adamı: {best.name} ({best.rating:.1f}).")
        notes = list(view.lineup_notes or [])
        if notes:
            body_parts.append("Asistan: " + "; ".join(_assistant_note(n) for n in notes[:4]))
        fixture_id = None
        for fx, res in list(report.results) + list(report.cup_results):
            if res is result:
                fixture_id = fx.id
                break
        return {
            "subject": f"{where} · {week}. hafta: {_score_text(result)}",
            "body": "\n".join(body_parts),
            "ref_type": REF_FIXTURE if fixture_id is not None else REF_TEAM,
            "ref_id": fixture_id if fixture_id is not None else team.id,
        }

    @staticmethod
    def _report_subject(view, season: int, week: int, midweek_only: bool) -> str:
        if midweek_only:
            return f"Hafta içi raporu · Sezon {season}, {week}. hafta"
        if not view.played_any:
            return f"Sezon {season}, {week}. hafta — oynanacak maç yok"
        return f"Hafta raporu · Sezon {season}, {week}. hafta"

    def _report_lines(self, view, report, team: Team, midweek_only: bool) -> list[list[str]]:
        """
        Raporun yapisal satirlari: [[tur, metin], ...] -- manager_week_reports.lines ile AYNI bicim
        (career_views.week_report_lines'in urettigi turler: result / injury / ban / transfer / desk / info /
        season / concern / youth). Arayuz bunlari dogrudan cizebilir.
        """
        lines: list[list[str]] = []
        if not view.played_any:
            return [["info", "Oynanacak maç yok — sezon tamamlandı."]]
        head = "Hafta içi kupa maçları oynandı." if midweek_only \
            else f"Sezon {report.season}, {report.week}. hafta oynandı."
        lines.append(["info", head])
        if view.user_result is not None:
            lines.append(["result", _score_text(view.user_result)])
        if view.user_cup_result is not None:
            lines.append(["result", "Kupa: " + _score_text(view.user_cup_result)])
        lines += [["injury", f"Sakatlık: {n.player_name} ({n.team_name}) — {n.detail}"] for n in report.injuries]
        lines += [["ban", f"Ceza: {n.player_name} ({n.team_name}) — {n.detail}"] for n in report.suspensions]
        lines += [["transfer", f"Transfer: {n.describe()}"] for n in report.transfers[:10]]
        lines += [["info", f"Asistan: {_assistant_note(note)}"] for note in view.lineup_notes]
        lines += [["desk", str(note)] for note in view.transfer_notes]
        lines += [["concern", f"{n.player_name}: {n.detail}"] for n in view.concern_notes]
        if view.finance_note:
            lines.append(["info", str(view.finance_note)])
        lines += [["info", str(note)] for note in view.prize_notes]
        lines += [["season", str(note)] for note in report.honours_notes]
        if view.manager_reputation is not None:
            before, after = view.manager_reputation
            lines.append(["info", f"Menajer tanınırlığı {before:.2f} → {after:.2f}"])
        if report.season_finished:
            lines.append(["season", "Sezon tamamlandı!"])
        return lines

    def _report_body(self, view, report, team: Team, midweek_only: bool) -> str:
        played = len(report.results) + len(report.cup_results)
        parts = [f"{played} maç oynandı." if played else "Oynanacak maç yok — sezon tamamlandı."]
        if view.user_result is not None:
            parts.append(_score_text(view.user_result))
        if view.user_cup_result is not None:
            parts.append("Kupa: " + _score_text(view.user_cup_result))
        if view.finance_note:
            parts.append(str(view.finance_note))
        for note in list(view.prize_notes)[:3]:
            parts.append(str(note))
        if report.season_finished:
            parts.append("Sezon tamamlandı.")
        return "\n".join(parts)

    # ------------------------------------------------------------------ sezon devri

    def record_season(self, new_season: int, notes_by_team: Mapping[int, Sequence[str]] | None = None) -> int:
        """Sezon devri mesajlari: yeni sezon duyurusu + kulubun devir notlari (sozlesme, akademi, sponsor)."""
        if not INBOX:
            return 0
        written = 0
        for team_id, team in self._human_clubs():
            notes = [str(n) for n in ((notes_by_team or {}).get(team_id) or [])]
            box = _Basket(manager_id=manager_id_for(self.cm, team_id), team_id=team_id)
            if self._guarded("season", self._write_season, int(new_season), team, notes, box):
                written += box.written
        return written

    def _write_season(self, new_season: int, team: Team, notes: list[str], box: _Basket) -> None:
        self._club_season(new_season, team, notes, box)
        self._commit_basket(box)

    def _club_season(self, new_season: int, team: Team, notes: list[str], box: _Basket) -> None:
        when = match_date(new_season, 1, start=self._start)
        cw = int(self.cm.career_week)
        box.add(pin=True, season=new_season, week=1, career_week=cw, kind=KIND_SEASON, game_date=when,
                subject=f"Sezon {new_season} başlıyor",
                body=f"{team.name} için {new_season}. sezon {long_date(when)} günü başlıyor. "
                     f"Kadronu ve taktiğini gözden geçir.",
                lines=[["info", n] for n in notes[:30]], ref_type=REF_TEAM, ref_id=team.id)
        for note in notes[:MAX_DESK_MESSAGES]:
            text = _clean(note)
            if text:
                box.add(season=new_season, week=1, career_week=cw, kind=KIND_CONTRACT, game_date=when,
                        subject=_clip(text, 90), body=text, ref_type=REF_TEAM, ref_id=team.id)


@dataclass
class _Basket:
    """Bir menajerin bu haftaki mesaj sepeti (tavan kirpmasi _commit_basket'te)."""
    manager_id: int | None
    team_id: int | None
    rows: list[tuple[bool, dict]] = field(default_factory=list)   # (kirpilamaz, post() degerleri)
    written: int = 0

    def add(self, *, pin: bool = False, **values) -> None:
        values.setdefault("important", is_important(str(values.get("kind"))))
        self.rows.append((bool(pin) or bool(values["important"]), values))


# ===========================================================================
# 8) "SUNA KADAR DEVAM"
# ===========================================================================

TARGET_NEXT_MATCH, TARGET_WINDOW, TARGET_SEASON_END, TARGET_WEEKS = "NEXT_MATCH", "WINDOW", "SEASON_END", "WEEKS"
TARGETS: tuple[str, ...] = (TARGET_NEXT_MATCH, TARGET_WINDOW, TARGET_SEASON_END, TARGET_WEEKS)
TARGET_LABELS: dict[str, str] = {
    TARGET_NEXT_MATCH: "Sonraki maça kadar",
    TARGET_WINDOW: "Transfer dönemi açılana kadar",
    TARGET_SEASON_END: "Sezon sonuna kadar",
    TARGET_WEEKS: "Belirli hafta sayısı",
}

STOP_TARGET, STOP_IMPORTANT, STOP_SEASON_END = "TARGET", "IMPORTANT", "SEASON_END"
STOP_LIMIT, STOP_BLOCKED, STOP_ERROR = "LIMIT", "BLOCKED", "ERROR"
STOP_LABELS: dict[str, str] = {
    STOP_TARGET: "Hedefe ulaşıldı",
    STOP_IMPORTANT: "Önemli bir gelişme var",
    STOP_SEASON_END: "Sezon bitti",
    STOP_LIMIT: "Hafta sınırına ulaşıldı",
    STOP_BLOCKED: "Devam edilemedi",
    STOP_ERROR: "Hafta işlenemedi",
}

DEFAULT_MAX_WEEKS = 60            # bir cagrida en fazla islenecek hafta (sonsuz dongu kalkani)
SHARED_TEXT = "Paylaşılan dünyada hafta tur motoruyla ilerler: 'şuna kadar devam' kullanılamaz."
NO_TEAM_TEXT = "Devam etmek için bir kulübün olmalı."
LIVE_TEXT = "Canlı maçın sürüyor; önce bitir."


@dataclass(frozen=True)
class ContinueResult:
    """`continue_until` sonucu. messages: durusa yol acan (ya da yolda biriken) ONEMLI mesajlar."""
    target: str
    weeks: int                      # islenen hafta sayisi
    stopped_by: str                 # STOP_*
    reason: str                     # Turkce aciklama (arayuz dogrudan gosterir)
    season: int
    week: int
    messages: list[InboxView] = field(default_factory=list)
    seconds: float = 0.0
    reports: list = field(default_factory=list)      # islenen haftalarin WeekReport nesneleri (son hafta en sonda)

    @property
    def stopped_early(self) -> bool:
        return self.stopped_by in (STOP_IMPORTANT, STOP_LIMIT, STOP_BLOCKED, STOP_ERROR)


def _has_fixture_this_week(cm, team_id: int | None) -> bool:
    """Menajerin kulubunun bu hafta (oynanmamis) lig ya da kupa maci var mi."""
    if team_id is None:
        return False
    week = int(cm.current_week)
    for fixture in (cm.next_fixture(team_id), cm.next_cup_fixture(team_id)):
        if fixture is not None and int(fixture.week) == week:
            return True
    return False


def _window_open(cm) -> bool:
    return transfer_rules.transfer_window(cm.current_week, cm.league_weeks(), cm.season_finished).open


def _target_reached(cm, target: str, team_id: int | None) -> bool:
    if cm.season_finished:
        return True
    if target == TARGET_NEXT_MATCH:
        return _has_fixture_this_week(cm, team_id)
    if target == TARGET_WINDOW:
        return _window_open(cm)
    return False                         # SEASON_END / WEEKS: dongu kosullari asagida


def continue_until(cm, target: str = TARGET_NEXT_MATCH, *, weeks: int | None = None,
                   max_weeks: int = DEFAULT_MAX_WEEKS, stop_on_important: bool = True,
                   keep_reports: bool = False) -> ContinueResult:
    """
    Hedefe kadar haftalari isler (CM'nin "devam" dugmesi). Paylasilan dunyada calismaz (hafta tur motoruyla
    ilerler). Onemli mesajda (teklif, sakatlik, yonetim) durur: kullanici mudahale edebilsin.

        TARGET_NEXT_MATCH  menajerin kulubunun maci olan haftaya gelince durur (o hafta OYNANMAZ)
        TARGET_WINDOW      transfer donemi acilinca durur
        TARGET_SEASON_END  sezon bitene kadar
        TARGET_WEEKS       `weeks` hafta (en fazla max_weeks)

    Her hafta `cm.play_week()` cagrilir; gelen kutusu mesajlari CareerManager tarafindan yazilir. Donus:
    ContinueResult (islenen hafta, durus nedeni, biriken onemli mesajlar, sure).
    """
    started = time.perf_counter()
    target = str(target if target in TARGETS else TARGET_NEXT_MATCH)
    team_id = cm._acting_team_id()
    box = Inbox.for_manager(cm)
    seen_id = _last_message_id(box)
    done: list = []

    def result(stopped_by: str, reason: str, important: list[InboxView]) -> ContinueResult:
        return ContinueResult(target=target, weeks=len(done), stopped_by=stopped_by, reason=reason,
                              season=int(cm.season), week=int(cm.current_week), messages=important,
                              seconds=time.perf_counter() - started,
                              reports=done if keep_reports else [])

    if getattr(cm.rules, "shared", False):
        return result(STOP_BLOCKED, SHARED_TEXT, [])
    if team_id is None:
        return result(STOP_BLOCKED, NO_TEAM_TEXT, [])
    if cm.season_finished:
        return result(STOP_SEASON_END, "Sezon tamamlandı; yeni sezonu başlat.", [])

    limit = max(0, int(DEFAULT_MAX_WEEKS if max_weeks is None else max_weeks))
    if target == TARGET_WEEKS:
        limit = min(limit, max(0, int(weeks or 0)))
    if target != TARGET_WEEKS and _target_reached(cm, target, team_id):
        return result(STOP_TARGET, _reached_text(cm, target), [])

    while len(done) < limit:
        try:
            report = cm.play_week()
        except Exception as exc:                          # hafta islenemedi: nedeni kullaniciya dondur
            log.exception("'Şuna kadar devam' haftası işlenemedi: %s", exc)
            return result(STOP_ERROR, f"{LIVE_TEXT if 'canlı' in str(exc).lower() else exc}", [])
        done.append(report)
        fresh = _new_important(box, seen_id)
        seen_id = _last_message_id(box, default=seen_id)
        if cm.season_finished:
            return result(STOP_SEASON_END, "Sezon tamamlandı.", fresh)
        if stop_on_important and fresh:
            return result(STOP_IMPORTANT, _important_text(fresh), fresh)
        if target != TARGET_WEEKS and _target_reached(cm, target, team_id):
            return result(STOP_TARGET, _reached_text(cm, target), fresh)
    if target == TARGET_WEEKS:
        return result(STOP_TARGET, f"{len(done)} hafta işlendi.", [])
    return result(STOP_LIMIT, f"{len(done)} hafta işlendi; hedefe ulaşılmadı.", [])


def _reached_text(cm, target: str) -> str:
    if target == TARGET_NEXT_MATCH:
        return f"Sıradaki maç {cm.current_week}. haftada."
    if target == TARGET_WINDOW:
        return transfer_rules.transfer_window(cm.current_week, cm.league_weeks(), cm.season_finished).label
    return "Hedefe ulaşıldı."


def _important_text(messages: Sequence[InboxView]) -> str:
    first = messages[0]
    extra = f" (+{len(messages) - 1} gelişme)" if len(messages) > 1 else ""
    return f"{first.kind_label}: {first.subject}{extra}"


def _last_message_id(box: Inbox, default: int = 0) -> int:
    box.db.flush()
    stmt = box._owned(select(func.max(InboxMessage.id)))
    return int(box.db.scalar(stmt) or default)


def _new_important(box: Inbox, after_id: int) -> list[InboxView]:
    box.db.flush()
    stmt = box._owned(select(InboxMessage)).where(InboxMessage.id > int(after_id),
                                                  InboxMessage.important.is_(True))
    return [view_of(row) for row in box.db.scalars(stmt.order_by(InboxMessage.id))]


__all__ = [
    "ALL_LABEL", "BASE_SEASON_YEAR", "CATEGORIES", "CATEGORY_LABELS", "CATEGORY_ORDER", "CAT_COMPETITION",
    "CAT_INJURY", "CAT_MESSAGE", "DateBar", "DEFAULT_MAX_WEEKS", "ContinueResult", "INBOX", "IMPORTANT_KINDS",
    "Inbox", "InboxCounts", "InboxView", "InboxWriter", "KINDS", "KIND_CATEGORY", "KIND_ICONS", "KIND_LABELS",
    "LINK_PAGES", "PARAM_REFS", "REF_TYPES", "STOP_BLOCKED", "STOP_ERROR", "STOP_IMPORTANT", "STOP_LABELS",
    "STOP_LIMIT", "STOP_SEASON_END", "STOP_TARGET", "TARGETS", "TARGET_LABELS", "TARGET_NEXT_MATCH",
    "TARGET_SEASON_END", "TARGET_WEEKS", "TARGET_WINDOW", "category_of", "continue_until", "date_bar",
    "day_name", "default_season_start", "format_date", "is_important", "long_date", "manager_id_for",
    "classify_desk_note", "match_date", "post", "season_start", "short_date", "view_of", "week_dates",
]
