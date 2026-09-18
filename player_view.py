"""
player_view.py
==============
Oyuncu profili (Faz 13E / K14). Bir oyuncuya "🔎 İncele" deyince acilan dort bolumlu inceleme paneli.
Kurallar baska modullerde (career_manager, transfers, development, concerns, ratings); bu modul yalnizca
VERI TOPLAMA + SUNUM yapar ve HICBIR SEYI DEGISTIRMEZ (tek yazdigi yer: ekranin kendi oturum durumu).

Bolumler (pv_section):
    📈 Özellikler & gelişim   ozellikler (gozlemci sisiyle), mevki uygunlugu, potansiyel tahmini,
                              yas/gelisim egrisi, form, moral, kondisyon, kaygi, wonderkid
    📄 Sözleşme & para        maas, sozlesme suresi, piyasa degeri, transfer/kiralik durumu, transfer yasagi,
                              TransferLog gecmisi, "bugun bana kaca mal olur" (transfers.asking_price)
    📊 Maç & sezon            kariyer toplami, sezon sezon tablo (kulup ve kupa ayrimiyla), son maclar,
                              sakatlik gecmisi, milli mac / gol
    ⚖️ Karşılaştırma & rol    ayni mevkideki oyuncularla karsilastirma (kendi kadron kesin, digerleri sisli),
                              kadro rolu, beklenen sure ve oyuncunun memnuniyeti (concerns)

GOZLEMCI SISI (K12 -- menajer her seyi bilmez). Transfer pazarinin kurallari birebir uygulanir:
    * Kendi kulubunun oyuncusu (A takim + akademi): cm.scouted_report margin=0 -> KESIN deger, ama ekranda
      yine SAYI YOK: yildiz (stars.py) + dolu cubuk. Form / moral / kondisyon / kaygi yalnizca burada gosterilir.
    * Baska kulubun oyuncusu: cm.scouted_report(margin>0) -> her ozellik ARALIK; yildiz araligi (star_range) ve
      cubukta "sis bandi" (alt-ust arasi). Form / moral / kondisyon / kadro rolu / memnuniyet GOSTERILMEZ.
    * Potansiyel HER ZAMAN cm.potential_estimate tahminidir (kendi oyuncunda da); gercek potential_rating
      hicbir yerde ekrana gitmez.
    * Motorun 1-99 sayilari, gizli potansiyel, sans kalitesi / xG hicbir yerde YAZILMAZ.
    * Herkese acik olan olgular sisli degildir: mac istatistikleri, kartlar, sakatlik, milli mac sayisi,
      sozlesme suresi ve satici kulubun istedigi bonservis (transfers.asking_price, izleme listesiyle ayni).

MEVKI UYGUNLUGU durustur: motor (match_engine) yalnizca "dogal mevki mi degil mi" bakar ve dogal mevkisi
disinda oynayana sabit bir guc cezasi uygular. Bu yuzden panel motorun okumadigi bir mevki-skoru URETMEZ:
ratings.POSITION_WEIGHTS ile "ozellikleri hangi mevkiye uyuyor" GOZLEMCI NOTU olarak yildizla gosterilir,
motorun asil kurali ayri bir cumleyle yazilir.

GIRIS NOKTALARI (hepsi ayni mekanizma: secici + "🔎 İncele" dugmesi -> panel ayni sekmede yerinde acilir):
    squad      Kadro & Taktik      web_app.squad_tab
    market     Transfer Pazarı     web_app.transfer_tab (hedef oyuncu)
    shortlist  Takip listesi       web_app.shortlist_section
    academy    Altyapı Akademisi   web_app.academy_tab
    hub        Teklifler & Listeler market_view.offers_section / listings_section (satir dugmesi)
    national   Milli Takım kadrosu national_view._squad_section

WIDGET ANAHTARLARI:
    pv_open                oturum durumu: (alan, oyuncu_id) -- panelin nerede ve kimin icin acik oldugu
    pv_section             bolum secici (radio)
    pv_pick_{alan}         secici (selectbox) -- kendi secicisi olmayan alanlarda
    pv_btn_{alan}          "🔎 İncele" dugmesi (secicili alanlar)
    pv_row_{alan}_{id}     satir ici "🔎 İncele" dugmesi (teklif / liste kartlari)
    pv_close               "✖️ Profili kapat"
    pv_cmp                 karsilastirma kapsami (kendi kadrom / lig)
    pv_panel               panel kabi (st.container key; profil CSS'i bununla daraltilir)

CIZIM MALIYETI: giris noktalari SORGU EKLEMEZ (zaten cizilen satirlardan secici uretilir; satir basina sorgu
yoktur). Profil acikken yalnizca SECILI bolumun verisi okunur ve sorgu sayisi mac / mevkidas sayisindan
BAGIMSIZDIR (olculdu): baslik 1-2 (gozlemci personeli), sezon ozeti 2 (tek GROUP BY + kulup adlari tek IN),
son maclar 1 (takma adli JOIN), transfer gecmisi 1, karsilastirma havuzu 1 (oyuncu + kulup adi ayni satirda),
kiralik notu 0-2. Iliski uzerinden satir basina yukleme (N+1) yoktur.

GUVENLIK: callback'ler yalnizca oturum durumunu degistirir (veritabanina yazmaz), bu yuzden web_common.
requires_auth ile sarilir -- paylasilan dunyada profil acmak dunya kilidi almaz ve hafta oynatilirken de
calisir. Kullanici / veritabani metinleri HTML'e html.escape, Streamlit metnine md_escape ile gider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

import concerns as concern_rules
import development
import fitness
import ratings
import transfers
from finance import format_money
from models import (
    Competition,
    Fixture,
    Loan,
    LoanStatus,
    Player,
    PlayerMatchStat,
    Position,
    Team,
    TransferLog,
)
from ofm_theme import panel_title_html, stat_strip_html
from stars import UNKNOWN, star_range, star_value, stars
from web_common import md_escape, money, requires_auth, reset_widgets

# ===========================================================================
# ANAHTARLAR VE SABITLER
# ===========================================================================

PROFILE_KEY = "pv_open"                 # (alan, oyuncu_id)
SECTION_KEY = "pv_section"
COMPARE_KEY = "pv_cmp"
CLOSE_KEY = "pv_close"
PANEL_KEY = "pv_panel"                  # panel kabi (CSS: .st-key-pv_panel)

AREA_SQUAD = "squad"
AREA_MARKET = "market"
AREA_SHORTLIST = "shortlist"
AREA_ACADEMY = "academy"
AREA_HUB = "hub"
AREA_NATIONAL = "national"
AREAS: tuple[str, ...] = (AREA_SQUAD, AREA_MARKET, AREA_SHORTLIST, AREA_ACADEMY, AREA_HUB, AREA_NATIONAL)

SEC_ATTRIBUTES = "📈 Özellikler & gelişim"
SEC_CONTRACT = "📄 Sözleşme & para"
SEC_STATS = "📊 Maç & sezon"
SEC_COMPARE = "⚖️ Karşılaştırma & rol"
SECTIONS: tuple[str, ...] = (SEC_ATTRIBUTES, SEC_CONTRACT, SEC_STATS, SEC_COMPARE)

CMP_SQUAD = "Kendi kadrom"
CMP_LEAGUE = "Ligdeki mevkidaşları"
CMP_OPTIONS: tuple[str, ...] = (CMP_SQUAD, CMP_LEAGUE)

INSPECT_LABEL = "🔎 İncele"
RECENT_MATCHES = 8                      # son maclar tablosunda en fazla satir
LEAGUE_PEERS = 12                       # ligden karsilastirmaya alinan mevkidas sayisi
NO_DATA_TEXT = "Henüz resmi maç oynamadı."
FOG_TEXT = ("Bu oyuncu senin kulübünde değil: özellikler gözlemcinin tahmin aralığıdır; "
            "form, moral, kondisyon ve kulüp içi rolü bilinmez.")

# Ekranda gosterilen ozellikler (career_views.scouted_profile_rows ile ayni sira ve etiketler)
ATTRIBUTE_LABELS: tuple[tuple[str, str], ...] = (
    ("pace", "Hız"),
    ("shooting", "Şut"),
    ("passing", "Pas"),
    ("defending", "Defans"),
    ("dribbling", "Dribling"),
    ("goalkeeping", "Kalecilik"),
)

POSITION_LABELS: dict[str, str] = {"GK": "Kaleci", "DEF": "Defans", "MID": "Orta saha", "FWD": "Forvet"}
COMPETITION_LABELS: dict[str, str] = {Competition.LEAGUE.value: "Lig", Competition.CUP.value: "Kupa"}

# Yas bantlari -- development.py kurallarindan (age_growth_factor / DECLINE_START_AGE) turetilmistir.
AGE_BANDS: tuple[tuple[int, int, str], ...] = (
    (15, 21, "Hızlı gelişim"),
    (22, 25, "Gelişim"),
    (26, 29, "Zirve"),
    (30, 31, "Duraklama"),
    (32, 99, "Gerileme"),
)

# Yildiz degerinin (0.5-5.0) sozle karsiligi. Sayi yerine gecen tek "olcek" budur.
STAR_WORDS: tuple[tuple[float, str], ...] = (
    (5.0, "Dünya çapında"), (4.0, "Çok iyi"), (3.5, "İyi"), (3.0, "Yeterli"),
    (2.5, "Vasat"), (2.0, "Zayıf"), (0.0, "Çok zayıf"),
)

PROFILE_CSS = """
<style>
.pv-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:.5rem .8rem;padding:.6rem .9rem;margin:.2rem 0 .5rem;
  background:var(--ofm-panel);border:1px solid var(--ofm-border);border-left:4px solid var(--ofm-accent);
  border-radius:12px}
.pv-head .nm{font-size:1.35rem;font-weight:800;color:var(--ofm-accent);overflow-wrap:anywhere}
.pv-head .mt{font-size:.9rem;color:var(--ofm-muted);overflow-wrap:anywhere}
.pv-head .tags{display:flex;flex-wrap:wrap;gap:.3rem;flex:1 1 100%}
.pv-attrs{display:grid;grid-template-columns:5.6rem minmax(4rem,1fr) max-content;gap:.34rem .6rem;
  align-items:center;margin:.2rem 0 .5rem}
.pv-attr{display:contents}
.pv-attr .k{font-size:.76rem;text-transform:uppercase;letter-spacing:.05em;color:var(--ofm-muted)}
.pv-attr .track{position:relative;height:10px;border-radius:5px;background:var(--ofm-panel-alt);
  border:1px solid var(--ofm-border);overflow:hidden}
.pv-attr .fill{position:absolute;top:0;bottom:0;background:var(--ofm-primary);border-radius:5px}
.pv-attr .fill.fog{background:var(--ofm-accent);opacity:.6}
.pv-attr .s{font-size:.8rem;color:var(--ofm-text);text-align:right;white-space:nowrap;letter-spacing:-.04em}
.pv-attr.top .k,.pv-attr.top .s{color:var(--ofm-accent);font-weight:700}
.pv-curve{display:flex;flex-wrap:wrap;gap:4px;margin:.2rem 0 .5rem}
.pv-curve .seg{flex:1 1 5.4rem;text-align:center;padding:.3rem .35rem;border-radius:9px;font-size:.74rem;
  background:var(--ofm-panel);border:1px solid var(--ofm-border);color:var(--ofm-muted)}
.pv-curve .seg .y{display:block;font-size:.68rem;opacity:.8}
.pv-curve .seg.now{background:var(--ofm-primary);border-color:var(--ofm-primary);color:var(--ofm-primary-text);
  font-weight:700}
.pv-curve .seg.now .y{opacity:.95}
.st-key-pv_panel .ofm-strip .v{letter-spacing:-.06em}
@media (max-width:640px){
  .pv-attrs{display:flex;flex-direction:column;align-items:stretch;gap:.55rem}
  .pv-attr{display:grid;grid-template-columns:1fr auto;gap:.2rem .5rem;align-items:center}
  .pv-attr .k{grid-column:1;grid-row:1}
  .pv-attr .s{grid-column:2;grid-row:1}
  .pv-attr .track{grid-column:1 / -1;grid-row:2}
  .pv-head .nm{font-size:1.15rem}
  .pv-curve .seg{flex:1 1 4.2rem;font-size:.68rem}
  .st-key-pv_panel .ofm-strip .v{font-size:.95rem}
}
</style>
"""


# ===========================================================================
# 1) VERI: gozlemci sisinden gecmis profil
# ===========================================================================

@dataclass(frozen=True)
class Estimate:
    """Ekrana giden tek bir tahmin: yildiz metni + cubuk bandi. SAYI EKRANA GITMEZ."""
    low: int
    high: int
    exact: bool

    @property
    def text(self) -> str:
        return stars(self.low) if self.exact else star_range(self.low, self.high)

    @property
    def word(self) -> str:
        """Yildizin sozle karsiligi ('İyi', 'Vasat'); sisli tahminde orta noktaya bakar."""
        value = star_value((self.low + self.high) // 2)
        if value is None:
            return UNKNOWN
        return next(word for threshold, word in STAR_WORDS if value >= threshold)

    @property
    def mid(self) -> int:
        return (self.low + self.high) // 2


@dataclass
class ProfileHeader:
    player_id: int
    name: str
    club: str
    position: str
    age: int
    nationality: str | None
    own: bool                                   # izleyen menajerin kulubunun oyuncusu mu
    in_academy: bool
    wonderkid: bool
    tags: list[tuple[str, bool]] = field(default_factory=list)      # (metin, uyari mi)


@dataclass
class SeasonRow:
    season: int
    competition: str
    team_name: str
    appearances: int
    minutes: int
    goals: int
    assists: int
    yellow: int
    red: int
    injuries: int
    rating: float | None


@dataclass
class Profile:
    header: ProfileHeader
    overall: Estimate
    potential: Estimate
    attributes: dict[str, Estimate]
    fit: dict[str, Estimate]                    # mevki kodu -> ozellik uyumu (gozlemci notu)


def _estimate(value) -> Estimate:
    """staff.ScoutedValue -> Estimate."""
    return Estimate(low=int(value.low), high=int(value.high), exact=bool(value.exact))


def position_fit(position: Position, attributes: dict[str, Estimate]) -> dict[str, Estimate]:
    """
    'Özellikleri hangi mevkiye uyuyor' GOZLEMCI NOTU: ratings.POSITION_WEIGHTS agirliklariyla her mevki icin
    bir yildiz (sisli oyuncuda aralik). Motor bu skoru OKUMAZ; motorun kurali dogal mevki / degil ayrimidir
    (ekranda ayrica yazilir). Kaleci mevkisi yalnizca kaleciler icin anlamlidir, digerlerinde gizlenir.
    """
    fit: dict[str, Estimate] = {}
    for pos in Position:
        if pos is Position.GK and position is not Position.GK:
            continue
        if pos is not Position.GK and position is Position.GK:
            continue
        lows = {name: attributes[name].low for name, _label in ATTRIBUTE_LABELS}
        highs = {name: attributes[name].high for name, _label in ATTRIBUTE_LABELS}
        exact = all(attributes[name].exact for name, _label in ATTRIBUTE_LABELS)
        fit[pos.value] = Estimate(
            low=ratings.compute_overall(pos, lows), high=ratings.compute_overall(pos, highs), exact=exact)
    return fit


def build_profile(cm, viewer: Team | None, player: Player) -> Profile:
    """
    Tek oyuncunun sisten gecmis profili. SORGU ACMAZ (cm.scouted_report ve cm.potential_estimate saf hesap;
    yalnizca izleyen kulubun gozlemcisini okur). viewer None ise (kulupsuz menajer) her sey sisli sayilir.
    """
    own = viewer is not None and player.team_id == viewer.id
    report = cm.scouted_report(viewer, player) if viewer is not None else None
    if report is None:                                        # kulupsuz izleyici: gozlemci yok, hepsi bilinmez
        margin = 12
        attrs = {name: Estimate(max(1, player.overall_rating - margin), min(99, player.overall_rating + margin),
                                False) for name, _label in ATTRIBUTE_LABELS}
        overall = Estimate(max(1, player.overall_rating - margin), min(99, player.overall_rating + margin), False)
        potential = Estimate(overall.low, min(99, overall.high + margin), False)
    else:
        attrs = {name: _estimate(report[name]) for name, _label in ATTRIBUTE_LABELS}
        overall = _estimate(report["overall_rating"])
        low, high = cm.potential_estimate(viewer, player)
        potential = Estimate(low, high, low == high)

    club = player.team.name if player.team is not None else "Kulüpsüz"
    header = ProfileHeader(
        player_id=player.id, name=player.name, club=club, position=player.position.value, age=player.age,
        nationality=player.nationality, own=own, in_academy=bool(player.in_academy),
        wonderkid=development.is_wonderkid(player.age, overall.mid, potential.mid),
        tags=profile_tags(cm, player, own),
    )
    return Profile(header=header, overall=overall, potential=potential, attributes=attrs,
                   fit=position_fit(player.position, attrs))


def profile_tags(cm, player: Player, own: bool) -> list[tuple[str, bool]]:
    """
    Herkesin gorebilecegi durum rozetleri: (metin, uyari mi). Uyari (kirmizi) = oynayamaz ya da satin alinamaz
    (sakat / cezali / transfer yasagi); digerleri notr bilgi (akademi, kiralik, listede, maas talebi).
    """
    tags: list[tuple[str, bool]] = []
    reason = player.unavailability_reason(cm.current_week)
    if reason:
        tags.append((reason, True))
    elif player.cup_suspended_matches:
        tags.append((f"kupada cezalı, {player.cup_suspended_matches} maç", True))
    if player.in_academy:
        tags.append(("U-21 akademisi", False))
    if player.loan_from_team_id is not None:
        tags.append(("kiralık oynuyor", False))
    if player.transfer_listed:
        tags.append(("satış listesinde", False))
    if player.loan_listed:
        tags.append(("kiralık listesinde", False))
    banned, ban_reason = cm.transfer_ban_info(player)
    if banned:
        tags.append((ban_reason, True))
    if own and player.wage_demand:
        tags.append(("yeni sözleşme istiyor", False))
    return tags


# ===========================================================================
# 2) VERI: veritabanindan toplu okunan gecmis (sorgular burada, hepsi toplu)
# ===========================================================================

def season_rows(db, player_id: int) -> list[SeasonRow]:
    """
    Sezon x kupa/lig x kulup kirilimli kariyer tablosu. TEK GROUP BY sorgusu + kulup adlari icin TEK IN sorgusu.
    (Satir basina sorgu yoktur; oyuncunun butun maclari bir kerede ozetlenir.)
    """
    stmt = (
        select(
            Fixture.season.label("season"),
            Fixture.competition.label("competition"),
            PlayerMatchStat.team_id.label("team_id"),
            func.count(PlayerMatchStat.id).label("apps"),
            func.coalesce(func.sum(PlayerMatchStat.minutes), 0).label("minutes"),
            func.coalesce(func.sum(PlayerMatchStat.goals), 0).label("goals"),
            func.coalesce(func.sum(PlayerMatchStat.assists), 0).label("assists"),
            func.coalesce(func.sum(PlayerMatchStat.yellow_cards), 0).label("yellow"),
            func.count(PlayerMatchStat.id).filter(PlayerMatchStat.red_card.is_(True)).label("red"),
            func.count(PlayerMatchStat.id).filter(PlayerMatchStat.injured.is_(True)).label("injuries"),
            func.avg(PlayerMatchStat.rating).label("rating"),
        )
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .where(PlayerMatchStat.player_id == player_id)
        .group_by(Fixture.season, Fixture.competition, PlayerMatchStat.team_id)
        .order_by(Fixture.season.desc(), Fixture.competition, PlayerMatchStat.team_id)
    )
    records = db.execute(stmt).all()
    if not records:
        return []
    names = team_names(db, {r.team_id for r in records})
    return [
        SeasonRow(
            season=int(r.season),
            competition=COMPETITION_LABELS.get(getattr(r.competition, "value", r.competition),
                                               str(getattr(r.competition, "value", r.competition))),
            team_name=names.get(r.team_id, "—"),
            appearances=int(r.apps), minutes=int(r.minutes), goals=int(r.goals), assists=int(r.assists),
            yellow=int(r.yellow), red=int(r.red), injuries=int(r.injuries),
            rating=round(float(r.rating), 2) if r.rating is not None else None,
        )
        for r in records
    ]


def team_names(db, team_ids) -> dict[int, str]:
    """Kulup adlari TEK sorguda (satir basina Team nesnesi yuklenmez)."""
    ids = {int(i) for i in team_ids if i is not None}
    if not ids:
        return {}
    return {int(tid): name for tid, name in db.execute(select(Team.id, Team.name).where(Team.id.in_(ids))).all()}


def recent_rows(db, player_id: int, limit: int = RECENT_MATCHES) -> list[dict]:
    """Son maclar: istatistik + fikstur + iki kulup adi TEK sorguda (takma adli JOIN; iliski gezilmez)."""
    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(PlayerMatchStat, Fixture, home.name.label("home_name"), away.name.label("away_name"))
        .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
        .join(home, home.id == Fixture.home_team_id)
        .join(away, away.id == Fixture.away_team_id)
        .where(PlayerMatchStat.player_id == player_id)
        .order_by(Fixture.season.desc(), Fixture.week.desc(), Fixture.id.desc())
        .limit(limit)
    )
    rows: list[dict] = []
    for stat, fx, home_name, away_name in db.execute(stmt).all():
        at_home = stat.team_id == fx.home_team_id
        opponent = away_name if at_home else home_name
        score = f"{fx.home_score}-{fx.away_score}" if fx.is_played else "—"
        cards = "🟥" if stat.red_card else "🟨" * int(stat.yellow_cards or 0)
        rows.append({
            "Sezon": fx.season,
            "Hf": fx.week,
            "Kupa": COMPETITION_LABELS.get(fx.competition.value, fx.competition.value),
            "Rakip": ("" if at_home else "@ ") + opponent,
            "Skor": score,
            "Dk": int(stat.minutes or 0),
            "G": int(stat.goals or 0),
            "A": int(stat.assists or 0),
            "Not": round(float(stat.rating), 1) if stat.rating is not None else None,
            "Kart": cards or "",
            "Durum": "sakatlandı" if stat.injured else "",
        })
    return rows


def transfer_rows(db, player_id: int, player_name: str) -> list[dict]:
    """TransferLog gecmisi TEK sorguda (id silinmisse ada gore de eslesir)."""
    stmt = (
        select(TransferLog)
        .where(or_(TransferLog.player_id == player_id,
                   and_(TransferLog.player_id.is_(None), TransferLog.player_name == player_name)))
        .order_by(TransferLog.season, TransferLog.week, TransferLog.id)
    )
    return [
        {"Sezon": log.season, "Hafta": log.week, "Nereden": log.from_team_name or "Kulüpsüz",
         "Nereye": log.to_team_name, "Bonservis": format_money(log.fee),
         "Maaş": f"{format_money(log.wage)}/hf" if log.wage else "—",
         "Tür": "Kiralık" if log.kind == "LOAN" else "Transfer"}
        for log in db.scalars(stmt)
    ]


def league_peers(db, player: Player) -> list[tuple[Player, str]]:
    """
    Oyuncunun liginde ayni mevkideki oyuncular: oyuncu satiri + kulup adi TEK sorguda (N+1 yok).
    Kulupsuz ya da ligi olmayan oyuncuda bos liste.
    """
    club = player.team
    if club is None or club.league_id is None:
        return []
    stmt = (
        select(Player, Team.name)
        .join(Team, Team.id == Player.team_id)
        .where(Team.league_id == club.league_id, Player.position == player.position,
               Player.in_academy.is_(False))
    )
    return [(p, name) for p, name in db.execute(stmt).all()]


def loan_note(db, cm, player: Player) -> str | None:
    """Aktif kiralamanin ozeti (ana kulup, maas payi, kalan hafta). Kiralik degilse None."""
    if player.loan_from_team_id is None and not player.loan_id:
        return None
    parent = db.get(Team, player.loan_from_team_id) if player.loan_from_team_id else None
    parts = [f"Ana kulüp: {parent.name}" if parent is not None else "Kiralık"]
    if player.loan_wage_share is not None:
        parts.append(f"maaşın %{int(player.loan_wage_share)}'ini kiralayan kulüp ödüyor")
    loan = db.get(Loan, int(player.loan_id)) if player.loan_id else None
    if loan is not None and loan.status == LoanStatus.ACTIVE.value and loan.end_career_week is not None:
        remaining = int(loan.end_career_week) - cm.career_week
        parts.append(f"{max(0, remaining)} hafta kaldı")
    return " · ".join(parts)


# ===========================================================================
# 3) HTML PARCALARI (yalnizca tema degiskenleri; yeni renk tanimlanmaz)
# ===========================================================================

def header_html(header: ProfileHeader) -> str:
    badge = "🌟 " if header.wonderkid else ""
    meta = (f"{POSITION_LABELS.get(header.position, header.position)} ({header.position}) · {header.age} yaş · "
            f"{header.club}")
    if header.nationality:
        meta += f" · {header.nationality}"
    tags = "".join(f'<span class="cm-badge{" bad" if warn else ""}">{escape(text)}</span>'
                   for text, warn in header.tags)
    own_tag = ('<span class="cm-badge xi">kendi oyuncun</span>' if header.own
               else '<span class="cm-badge">gözlemci raporu</span>')
    wonder = '<span class="cm-badge xi">wonderkid</span>' if header.wonderkid else ""
    return (f'<div class="pv-head"><div class="nm">{badge}{escape(header.name)}</div>'
            f'<div class="mt">{escape(meta)}</div>'
            f'<div class="tags">{own_tag}{wonder}{tags}</div></div>')


STAR_CELL_PCT = 10                      # yarim yildizin cubuktaki genisligi (%)


def bar_pct(value: int) -> int:
    """
    Cubuk genisligi YILDIZDAN turetilir (0.5 yildiz = %10 ... 5 yildiz = %100), motorun 1-99 sayisindan degil.
    Boylece cubuk yildizin gosterdiginden bir gram fazla bilgi tasimaz ve motor degeri sayfanin kaynagina da
    sizmaz (K12). Gorsel fark yok: olcek zaten yildizdir.
    """
    star = star_value(value)
    return int(round((star or 0.5) * 20))


def attribute_html(items: list[tuple[str, Estimate]], highlight: set[str] | None = None) -> str:
    """Ozellik cubuklari: kesin degerde dolu cubuk, sisli tahminde alt-ust arasi 'sis bandi'."""
    highlight = highlight or set()
    rows = []
    for label, est in items:
        low, high = bar_pct(min(est.low, est.high)), bar_pct(max(est.low, est.high))
        if est.exact:
            bar = f'<span class="fill" style="left:0;width:{low}%"></span>'
        else:
            # Sis bandi araligin kapsadigi yarim yildiz hucrelerini ORTER: [alt hucrenin basi, ust hucrenin sonu].
            # (Iki uc ayni yildizdaysa tek hucre; 5 yildizda bant sagdan tasmaz.)
            start = max(0, low - STAR_CELL_PCT)
            bar = f'<span class="fill fog" style="left:{start}%;width:{high - start}%"></span>'
        cls = "pv-attr top" if label in highlight else "pv-attr"
        rows.append(f'<div class="{cls}"><div class="k">{escape(label)}</div>'
                    f'<div class="track">{bar}</div>'
                    f'<div class="s">{escape(est.text)} · {escape(est.word)}</div></div>')
    return f'<div class="pv-attrs">{"".join(rows)}</div>'


def age_curve_html(age: int) -> str:
    segments = []
    for low, high, label in AGE_BANDS:
        now = low <= age <= high
        span = f"{low}-{high}" if high < 99 else f"{low}+"
        segments.append(f'<div class="seg{" now" if now else ""}">{escape(label)}'
                        f'<span class="y">{escape(span)} yaş</span></div>')
    return f'<div class="pv-curve">{"".join(segments)}</div>'


# ===========================================================================
# 4) CALLBACK'LER (yalnizca oturum durumu; veritabanina yazmaz -> requires_auth)
# ===========================================================================

@requires_auth
def cb_pv_open(area: str, player_id: int | None = None) -> None:
    """Profili acar. player_id verilmezse alanin kendi secicisinden (pv_pick_{alan}) okunur."""
    if player_id is None:
        player_id = st.session_state.get(pick_key(area))
    if player_id is None:
        return
    st.session_state[PROFILE_KEY] = (area, int(player_id))


@requires_auth
def cb_pv_close() -> None:
    reset_widgets(PROFILE_KEY)


# ===========================================================================
# 5) GIRIS NOKTALARI (tek mekanizma: secici + "🔎 İncele")
# ===========================================================================

def pick_key(area: str) -> str:
    return f"pv_pick_{area}"


def open_profile(area: str, player_id: int) -> None:
    """Testler ve baska gorunumler icin: profili dogrudan acar (callback ile ayni etki)."""
    st.session_state[PROFILE_KEY] = (area, int(player_id))


def opened(area: str) -> int | None:
    """Bu alanda acik profilin oyuncu id'si; acik degilse None."""
    value = st.session_state.get(PROFILE_KEY)
    if not isinstance(value, tuple) or len(value) != 2 or value[0] != area:
        return None
    return int(value[1])


def inspect_button(area: str, player_id: int | None, *, key: str | None = None, label: str = INSPECT_LABEL,
                   container=None, disabled: bool = False, width: str = "stretch", help: str | None = None) -> None:
    """Tek satirlik '🔎 İncele' dugmesi (secicisi zaten olan alanlar ve satir kartlari icin)."""
    target = container if container is not None else st
    target.button(label, key=key or f"pv_btn_{area}", on_click=cb_pv_open, args=(area, player_id),
                  disabled=disabled or player_id is None, width=width,
                  help=help or "Oyuncunun profilini aç: özellikler, sözleşme, istatistik, karşılaştırma.")


def picker(area: str, options: dict[int, str], *, label: str = "Oyuncu", ratio=(3, 1)) -> None:
    """
    Kendi secicisi olmayan alanlar icin: secilebilir oyuncu kutusu + '🔎 İncele'. Satir basina dugme
    uretmez (telefonda da tek satir kalir, her cizimde onlarca widget olusmaz).
    """
    if not options:
        return
    key = pick_key(area)
    if st.session_state.get(key) not in options:
        reset_widgets(key)
    pick, go = st.columns(list(ratio))
    pick.selectbox(label, list(options), key=key, format_func=lambda i: options.get(i, str(i)),
                   label_visibility="collapsed")
    inspect_button(area, st.session_state.get(key, next(iter(options))), key=f"pv_btn_{area}", container=go)


def option_label(name: str, position: str, extra: str = "") -> str:
    """Secici etiketi: ad · mevki · ek bilgi (yildiz / kulup). Duz metin (Streamlit kendi kacisini yapar)."""
    parts = [name, position]
    if extra:
        parts.append(extra)
    return " · ".join(parts)


# ===========================================================================
# 6) PANEL
# ===========================================================================

def profile_panel(db, cm, team: Team | None, area: str) -> None:
    """
    Bu alanda profil acikken paneli cizer; acik degilse HICBIR SEY yapmaz (sorgu da acmaz).
    team: izleyen menajerin kulubu (sis kurallarinin referansi).
    """
    player_id = opened(area)
    if player_id is None:
        return
    player = db.get(Player, player_id)
    if player is None:
        reset_widgets(PROFILE_KEY)
        st.info("Oyuncu artık bu dünyada değil (transfer edilmiş ya da silinmiş olabilir).")
        return
    profile = build_profile(cm, team, player)
    with st.container(border=True, key=PANEL_KEY):
        st.markdown(header_html(profile.header), unsafe_allow_html=True)
        if not profile.header.own:
            st.caption(FOG_TEXT)
        st.markdown(stat_strip_html(_summary_strip(profile)), unsafe_allow_html=True)
        if st.session_state.get(SECTION_KEY) not in SECTIONS:
            reset_widgets(SECTION_KEY)
        section = st.radio("Bölüm", list(SECTIONS), key=SECTION_KEY, horizontal=True,
                           label_visibility="collapsed")
        if section == SEC_ATTRIBUTES:
            _attributes_section(cm, team, player, profile)
        elif section == SEC_CONTRACT:
            _contract_section(db, cm, team, player, profile)
        elif section == SEC_STATS:
            _stats_section(db, player)
        else:
            _compare_section(db, cm, team, player, profile)
        st.button("✖️ Profili kapat", key=CLOSE_KEY, on_click=cb_pv_close)


def _summary_strip(profile: Profile) -> list[tuple[str, str]]:
    items = [("Güç", profile.overall.text), ("Potansiyel (gözlemci)", profile.potential.text),
             ("Yaş", f"{profile.header.age}")]
    items.append(("Mevki", profile.header.position))
    return items


# ---------------------------------------------------------------- 6a) ozellikler

def _attributes_section(cm, team: Team | None, player: Player, profile: Profile) -> None:
    st.markdown(panel_title_html("Özellikler"), unsafe_allow_html=True)
    st.caption("Kesin bilgi dolu çubuk, gözlemci tahmini ise alt–üst arasını gösteren bir bant olarak çizilir. "
               "Sayısal güç bu oyunda hiçbir ekranda gösterilmez; ölçek yıldızdır.")
    best = max(profile.attributes.items(), key=lambda kv: kv[1].mid)[0] if profile.attributes else None
    labels = {name: label for name, label in ATTRIBUTE_LABELS}
    items = [(labels[name], profile.attributes[name]) for name, _label in ATTRIBUTE_LABELS
             if name != "goalkeeping" or player.position is Position.GK]
    st.markdown(attribute_html([("Genel", profile.overall), *items],
                               highlight={labels[best]} if best else set()), unsafe_allow_html=True)

    st.markdown(panel_title_html("Mevki uygunluğu"), unsafe_allow_html=True)
    natural = POSITION_LABELS.get(profile.header.position, profile.header.position)
    st.caption(f"Doğal mevkisi **{natural}**. Motorun kuralı tek cümledir: doğal mevkisi dışında oynatılan "
               "oyuncu sabit bir güç kaybına uğrar; motor mevkiye özel bir puan okumaz. Aşağıdaki satırlar "
               "gözlemci notudur: özelliklerinin hangi mevkiye ne kadar uyduğu.")
    fit_items = [(POSITION_LABELS.get(code, code) + (" (doğal)" if code == profile.header.position else ""), est)
                 for code, est in profile.fit.items()]
    st.markdown(attribute_html(fit_items, highlight={natural + " (doğal)"}), unsafe_allow_html=True)

    st.markdown(panel_title_html("Gelişim"), unsafe_allow_html=True)
    st.markdown(age_curve_html(player.age), unsafe_allow_html=True)
    st.caption(_development_text(profile, player))

    if not profile.header.own:
        st.info("Form, moral ve kondisyon yalnızca kendi kulübünün oyuncuları için görünür.")
        return
    st.markdown(panel_title_html("Günlük durum"), unsafe_allow_html=True)
    condition = int(getattr(player, "condition", 100))
    st.markdown(stat_strip_html([
        ("Form", player.form), ("Moral", player.morale),
        ("Kondisyon", f"%{condition} · {_condition_word(condition)}"),
        ("Son not", player.last_rating if player.last_rating is not None else "—"),
        ("Ortalama not", player.average_rating if player.average_rating is not None else "—"),
        ("Memnuniyet", concern_rules.LEVEL_LABELS[concern_rules.level_of(player.concern_level)]),
    ]), unsafe_allow_html=True)
    history = list(player.match_rating_history or [])
    if history:
        st.caption("Son maç notları: " + " · ".join(f"{value:.1f}" for value in history[-8:]))
    else:
        st.caption(NO_DATA_TEXT)


def _condition_word(condition: int) -> str:
    band = fitness.condition_band(condition)
    return {"good": "dinç", "warn": "yorgun", "low": "bitkin"}.get(band, "—")


def _development_text(profile: Profile, player: Player) -> str:
    """Yasa ve tahmini potansiyele gore duz Turkce degerlendirme (gizli sayi kullanmadan)."""
    band = next(label for low, high, label in AGE_BANDS if low <= player.age <= high)
    gap = profile.potential.mid - profile.overall.mid
    if player.age >= development.DECLINE_START_AGE:
        outlook = "Bu yaştan sonra özellikleri sezon sezon geriler; sözleşme kararlarında bunu hesaba kat."
    elif gap >= 12:
        outlook = "Gözlemciye göre önünde ciddi bir gelişim payı var; düzenli oynarsa hızlı ilerler."
    elif gap >= 5:
        outlook = "Gelişimi sürüyor ama tavanına yaklaşıyor."
    else:
        outlook = "Gözlemciye göre tavanına yakın: buradan sonrası büyük ölçüde form meselesi."
    wonder = " 🌟 Wonderkid: yaşına göre tahmini tavanı çok yüksek." if profile.header.wonderkid else ""
    return f"Yaş bandı: **{band}** ({player.age}). {outlook}{wonder}"


# ---------------------------------------------------------------- 6b) sozlesme

def _contract_section(db, cm, team: Team | None, player: Player, profile: Profile) -> None:
    own = profile.header.own
    value_text = _value_text(cm, team, player)
    wage_text = money(player.current_wage) if own else "Bilinmiyor"
    st.markdown(stat_strip_html([
        ("Haftalık maaş (EUR)", wage_text),
        ("Sözleşme", f"{player.contract_years} yıl" if player.contract_years else "Son sezon"),
        ("Piyasa değeri (EUR)", value_text),
        ("Kadro rolü", transfers.ROLE_LABELS[player.squad_role] if own else "Bilinmiyor"),
    ]), unsafe_allow_html=True)
    if not own:
        st.caption("Maaşı ve kulüp içi rolü başka kulübün defterinde; piyasa değeri gözlemci tahminidir.")

    st.markdown(panel_title_html("Durum"), unsafe_allow_html=True)
    lines: list[str] = []
    note = loan_note(db, cm, player)
    if note:
        lines.append(f"🔁 {md_escape(note)}")
    if player.transfer_listed:
        lines.append("🏷️ Kulübü onu satış listesine koydu.")
    if player.loan_listed:
        lines.append("🔁 Kiralık listesinde.")
    banned, ban_reason = cm.transfer_ban_info(player)
    if banned:
        lines.append(f"⛔ {md_escape(ban_reason)}")
    if own and player.wage_demand:
        lines.append(f"✍️ Yeni sözleşme istiyor: {format_money(player.current_wage)} → "
                     f"**{format_money(player.wage_demand)}**/hafta (Kadro & Taktik sekmesinde cevapla).")
    if not lines:
        lines.append("Özel bir durumu yok: sözleşmesi işliyor, listede değil.")
    for line in lines:
        st.markdown(line)
    st.caption("Serbest kalma bedeli (release clause) bu sürümde modellenmiyor; sözleşmede böyle bir madde yok.")

    if not own:
        st.markdown(panel_title_html("Bana kaça mal olur?"), unsafe_allow_html=True)
        st.markdown(_cost_text(cm, team, player))

    st.markdown(panel_title_html("Transfer geçmişi"), unsafe_allow_html=True)
    rows = transfer_rows(db, player.id, player.name)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("Bu oyuncu bu kariyerde hiç kulüp değiştirmedi.")


def _value_text(cm, team: Team | None, player: Player) -> str:
    """Piyasa degeri (seritte kisa para, birim etikette): kendi oyuncunda kesin, digerlerinde gozlemci araligi."""
    if team is None:
        return "Bilinmiyor"
    value = cm.scouted_report(team, player)["market_value"]
    if value.exact:
        return money(value.low)
    return f"{money(value.low)} – {money(value.high)}"


def _cost_text(cm, team: Team | None, player: Player) -> str:
    """Satici kulubun BUGUN isteyecegi bonservis (izleme listesindeki 'İstenen bonservis' ile ayni kural)."""
    if team is None or player.team is None:
        return "Kulübü yok: bonservis istemez, yalnızca sözleşme masası kurulur."
    banned, ban_reason = cm.transfer_ban_info(player)
    if banned:
        return f"⛔ Şu an satın alınamaz — {md_escape(ban_reason)}."
    if player.in_academy:
        return "🎓 Kulübünün akademisinde: akademi oyuncuları satılık değil."
    asking = transfers.asking_price(player, player.team, team.reputation)
    budget = team.transfer_budget
    verdict = ("bütçen yeter" if budget >= asking else
               f"bütçen {format_money(max(0, asking - budget))} eksik kalır")
    return (f"{md_escape(player.team.name)} bugün yaklaşık **{format_money(asking)}** ister "
            f"(kadro içindeki önemi, sözleşme süresi ve kulübünün sana bakışı hesaba katılmıştır). "
            f"Transfer bütçen {format_money(budget)} — {verdict}. "
            f"Haftalık maaş yükü ayrıca sözleşme masasında belirlenir.")


# ---------------------------------------------------------------- 6c) istatistik

def _stats_section(db, player: Player) -> None:
    rows = season_rows(db, player.id)
    totals = _totals(rows)
    st.markdown(stat_strip_html([
        ("Maç", totals["apps"]), ("Gol", totals["goals"]), ("Asist", totals["assists"]),
        ("Ort. not", totals["rating"] if totals["rating"] is not None else "—"),
        ("Dakika", totals["minutes"]),
        ("Kart", f"{totals['yellow']}🟨 {totals['red']}🟥"),
        ("Milli maç", f"{player.international_caps} ({player.international_goals} gol)"),
    ]), unsafe_allow_html=True)
    if not rows:
        st.info(NO_DATA_TEXT + " Sezon tablosu ilk resmi maçtan sonra dolar.")
        return

    st.markdown(panel_title_html("Sezon sezon"), unsafe_allow_html=True)
    st.dataframe(pd.DataFrame([
        {"Sezon": r.season, "Kulüp": r.team_name, "Kupa/Lig": r.competition, "Maç": r.appearances,
         "Dk": r.minutes, "Gol": r.goals, "Asist": r.assists, "🟨": r.yellow, "🟥": r.red,
         "Sakatlık": r.injuries, "Ort. not": r.rating if r.rating is not None else "—"}
        for r in rows
    ]), hide_index=True, width="stretch")

    injuries = sum(r.injuries for r in rows)
    st.caption(f"Sakatlık geçmişi: {injuries} maçta sakatlanarak çıktı." if injuries
               else "Sakatlık geçmişi: maç içinde hiç sakatlanmadı.")

    st.markdown(panel_title_html("Son maçlar"), unsafe_allow_html=True)
    recent = recent_rows(db, player.id)
    if recent:
        st.dataframe(pd.DataFrame(recent), hide_index=True, width="stretch")
    else:
        st.caption(NO_DATA_TEXT)


def _totals(rows: list[SeasonRow]) -> dict:
    """Kariyer toplami: sezon satirlarindan hesaplanir (ikinci sorgu acilmaz)."""
    apps = sum(r.appearances for r in rows)
    weighted = sum((r.rating or 0) * r.appearances for r in rows if r.rating is not None)
    rated = sum(r.appearances for r in rows if r.rating is not None)
    return {
        "apps": apps,
        "minutes": sum(r.minutes for r in rows),
        "goals": sum(r.goals for r in rows),
        "assists": sum(r.assists for r in rows),
        "yellow": sum(r.yellow for r in rows),
        "red": sum(r.red for r in rows),
        "rating": round(weighted / rated, 2) if rated else None,
    }


# ---------------------------------------------------------------- 6d) karsilastirma ve rol

def _compare_section(db, cm, team: Team | None, player: Player, profile: Profile) -> None:
    st.markdown(panel_title_html("Aynı mevkideki oyuncular"), unsafe_allow_html=True)
    st.caption("Kendi oyuncuların kesin, diğerleri gözlemci tahminiyle sıralanır: liste gizli bir sayıyı "
               "değil, senin bildiğin kadarını gösterir.")
    scope = st.radio("Karşılaştırma", list(CMP_OPTIONS), key=COMPARE_KEY, horizontal=True,
                     label_visibility="collapsed")
    rows = (_squad_compare_rows(cm, team, player) if scope == CMP_SQUAD
            else _league_compare_rows(db, cm, team, player))
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    elif scope == CMP_SQUAD:
        st.info("Kadronda bu mevkide karşılaştırılacak oyuncu yok.")
    else:
        st.info("Bu oyuncunun ligi yok (kulüpsüz) ya da ligde aynı mevkide başka oyuncu bulunamadı.")

    st.markdown(panel_title_html("Kadro rolü ve süre"), unsafe_allow_html=True)
    if not profile.header.own:
        st.info("Kadro rolü ve oynama süresi beklentisi kulüp içi bilgidir: başka kulübün oyuncusu için "
                "gösterilmez.")
        return
    if player.in_academy:
        st.info("Akademi oyuncusu: A takım süre beklentisi işlemez. U-21 maçları gelişimini besler; "
                "A takıma yükseltince kadro rolü ve süre beklentisi başlar.")
        return
    row = next((r for r in cm.player_concerns(team) if r.player_id == player.id), None)
    if row is None:
        st.caption("Bu oyuncu için süre değerlendirmesi yok.")
        return
    st.markdown(stat_strip_html([
        ("Kadro rolü", row.role_label),
        ("Beklediği maç", f"{row.wanted:.1f}"),
        ("Oynadığı", f"{row.played:.1f}"),
        ("Memnuniyet", row.label),
    ]), unsafe_allow_html=True)
    st.markdown(f"**Durum:** {md_escape(row.reason)}")
    if row.overloaded:
        st.warning("Kadro şişkin: bu mevkide beklenti toplamı dağıtılabilir süreyi aşıyor, yedekler daha "
                   "çabuk şikayet eder.")
    if row.wage_demand:
        st.warning(f"✍️ Yeni sözleşme istiyor: {format_money(row.current_wage)} → "
                   f"{format_money(row.wage_demand)}/hafta.")
    st.caption(f"Süre beklentisi kadro rolünden gelir (Yıldız > As > Yedek) ve son "
               f"{concern_rules.CONCERN_WINDOW} resmi maça bakar; kupa maçları yarım sayılır.")


def _squad_compare_rows(cm, team: Team | None, player: Player) -> list[dict]:
    """Kendi kadron: ek SORGU YOK (team.players zaten yuklu)."""
    if team is None:
        return []
    peers = [p for p in team.players if p.position is player.position]
    if player.team_id == team.id and player not in peers:
        peers.append(player)
    rows = []
    for p in sorted(peers, key=lambda x: (-x.overall_rating, x.name)):
        rows.append({
            "Oyuncu": ("▶ " if p.id == player.id else "") + p.name,
            "Yaş": p.age,
            "Güç": stars(p.overall_rating),
            "Potansiyel": star_range(*cm.potential_estimate(team, p)),
            "Form": p.form,
            "Kondisyon": f"%{int(getattr(p, 'condition', 100))}",
            "Rol": transfers.ROLE_LABELS[p.squad_role],
            "Ort. not": p.average_rating if p.average_rating is not None else "—",
        })
    return rows


def _league_compare_rows(db, cm, team: Team | None, player: Player) -> list[dict]:
    """Ligdeki mevkidaslari: TEK sorgu; siralama ve gosterim gozlemci TAHMINI uzerinden (sizinti yok)."""
    pool = league_peers(db, player)
    if not pool:
        return []
    scored: list[tuple[int, Player, str, Estimate]] = []
    for p, club_name in pool:
        est = (_estimate(cm.scouted_report(team, p)["overall_rating"]) if team is not None
               else Estimate(p.overall_rating, p.overall_rating, False))
        scored.append((est.mid, p, club_name, est))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    top = scored[:LEAGUE_PEERS]
    if not any(item[1].id == player.id for item in top):
        me = next((item for item in scored if item[1].id == player.id), None)
        if me is not None:
            top = [*top[: LEAGUE_PEERS - 1], me]
    return [
        {"Oyuncu": ("▶ " if p.id == player.id else "") + p.name, "Kulüp": club_name, "Yaş": p.age,
         "Güç (tahmin)": est.text, "Değerlendirme": est.word,
         "Sözleşme": f"{p.contract_years} yıl"}
        for _mid, p, club_name, est in top
    ]
