"""
career_manager.py
=================
Sezon dongusu kontrolcusu (3. Asama).

Sorumluluklar:
    * Mevcut haftadaki TUM liglerin maclarini match_engine ile oynatir
    * Mac sonrasi kaliciligi yazar: oyuncu mac istatistikleri, not gecmisi,
      form/moral guncellemesi, sakatlik suresi, kart cezasi, sari birikimi
    * Haftalik maaslari oder ve butceleri gunceller (5. Asama)
    * Teknik heyet etkilerini uygular: saglikci -> sakatlik suresi ve kondisyon toparlanmasi,
      antrenor -> form, asistan -> moral, gozlemci -> bilgi sisi
    * Dinamik kondisyon (fitness.py): oynayanin mac sonu enerjisi saglikciya gore
      toparlanip kaydedilir; oynamayan tam dinlenir (100)
    * Transfer pazarini yurutur: bonservis teklifi, sozlesme masasi, AI kulupleri
    * Ceza sayaclarini hafta sonunda azaltir, haftayi ilerletir
    * Puan durumu / gol kralligi / sonraki mac / takim formu sorgulari
    * Sezon bitince yeni sezon kurar (fikstur, yas, istatistik sifirlama)
    * Oyun modu (8. Asama): CAREER_MODE'da her hafta once o haftanin Devler Arenasi maclari
      (hafta ici), sonra lig maclari (hafta sonu) oynanir; TOURNAMENT_MODE'da sadece kupa.
      Kupa orkestrasyonu tournament_manager.py'dedir.
    * Canli mac (9. Asama): kullanicinin bu haftaki gercek maci (lig ya da kupa) canli oynanir.
        live_fixture        siradaki maci: once hafta ici kupa, sonra lig (turnuva modunda sadece kupa)
        prepare_live_match  motoru otomatik yolla AYNI parametrelerle kurar (tohum, ayar, hafta,
                            kupada eleme kurali / tarafsiz saha / kupa cezalari). Lig macindan once
                            bu haftanin kupa maclari hala bekliyorsa once play_midweek oynatilir ki
                            hafta ici toparlanma ve sakatliklar lig kadrosuna yansisin.
        play_midweek        yalnizca bu haftanin kupa mac gunu (hafta ilerlemez)
        play_week / play_midweek(live_results)
                            canli maclarin bitmis sonuclari simulasyon yerine islenir; kalicilik
                            (tablo, oyuncu satirlari, form/moral/kondisyon, cezalar, tanınırlık)
                            otomatik yolla birebir aynidir. Sonuclar HICBIR SEY yazilmadan dogrulanir.
        save_live_result    arayuz kisayolu: kupa maci + bekleyen lig maci -> play_midweek, aksi play_week
      Mudahalesiz canli mac, ayni tohumla otomatik oynanan macla bit-bit aynidir.
    * Gelisim ve altyapi (10. Asama; kurallar development.py / youth.py):
        ensure_youth_setup  eski kayit/yeni dunya icin idempotent doldurma: potansiyel, tesis, akademiler
        play_week           (yalnizca kariyer modu) maclardan sonra TUM oyuncular (A takim + akademi) icin
                            haftalik gelisim/yaslanma: bu haftanin lig VE kupa dakikalari/notlari (hafta ici
                            play_midweek ile oynanmis olsa bile), genc antrenoru, moral, tesis. 32+ gerileme.
                            youth_intake_week() haftasinda sezonda bir kez TUM kuluplere genc girisi (ayri,
                            tohumdan turetilmis RNG: cm.rng dizisi bozulmaz), akademi kapasitesi uygulanir.
        promote_to_senior / send_to_academy
                            kadro kurallari (A takim en fazla 25, en az SQUAD_FLOOR ve 2 kaleci; akademi 20,
                            21 yas ustu en fazla 3). Ihlalde AcademyError (Turkce mesaj).
        potential_estimate  gozlemcinin (judging_potential) sisli potansiyel araligi; gercek tavan gizlidir
        start_new_season    akademi de yaslanir; AI kulupleri akademisini yonetir (yukseltme/serbest birakma);
                            kullanicinin kulubunde hicbir sey otomatik tasinmaz, yalnizca new_season_notes.
      Akademi oyunculari A takim mantigina (mac, kadro, transfer hedefi, cezalar) girmez: Team.players
      yalnizca A takimdir; dogrudan Player sorgulari in_academy ile suzulur.
    * Kulup tesisleri ve sponsorluk (11. Asama; kurallar facilities.py):
        ensure_club_setup   eski kayit/yeni dunya icin idempotent doldurma: stadyum kapasitesi ve saglik merkezi
                            (kulube ozgu sabit tohum + itibar), hic sozlesmesi olmamis her kulube baslangic
                            sponsoru + bekleyen teklifler. NULL tesis = kurulmamis kulup: etkisiz (eski davranis).
        facility_status     seviyeler, sonraki maliyetler ve etki onizlemesi (arayuz icin duz dict)
        upgrade_facility    youth / medical / stadium; bedel transfer kasasindan. Ihlalde FacilityError.
        sponsor_offers / sign_sponsor
                            bekleyen teklifler; imza mevcut sozlesmenin yerine gecer, imza primi kasaya
        play_week           (kariyer modu, maas adimi) her kulube gecerli sponsor bedeli + bu hafta oynanan
                            her ic saha lig/kupa maci icin mac gunu geliri (tarafsiz saha haric)
        _post_match         kondisyon toparlanmasi saglikci x saglik merkezi carpani
        start_new_season    biten sozlesmeler sona erer; her kulube taze teklif: AI en degerlisini (belirgin
                            fark varsa) imzalar ve butcesinin kucuk bir payiyla tek tesis yatirimi yapar;
                            kullanicinin teklifleri imzalayana kadar bekler (new_season_notes).
    * Kariyer paketi (12. Asama; Soccer Manager'dan esinli). Para akislari yalnizca KARIYER modunda ve kurulmus
      (ensure_club_setup ile stadyum kapasitesi atanmis) kuluplerde: kurulmamis dunya eski davranisla aynidir.
        _pay_weekly_wages   lig maci oynanan haftada ligin her kulubune ESIT TV payi (finance.tv_money_weekly)
        lig odulu           lig bittigi hafta (en gec start_new_season'da, tek sefer) siraya gore odul; ayni anda
                            season_honours arsivi (sampiyon, ikinci, gol krali, sezonun oyuncusu)
        kupa primleri       eslesme / grup bittiginde (tournament_manager kancasi) tur primi; final -> kupa arsivi
        baskan guvencesi    sezon basi net degeri (kasa + kadro degeri) lig ortalamasinin %50'sinin altindaki
                            kulube fark kadar para
        transfer yasagi     kulup degistiren oyuncu TRANSFER_BAN_WEEKS hafta satilamaz / teklif alamaz
                            (mutlak kariyer haftasi: sezon devrinde kesintisiz); transfer_ban_info
        transfer_log / news_items
                            her transfer kaydedilir; haber akisi: kullaniciyi ilgilendiren transferler, haftanin en
                            pahali AI transferleri, sampiyonlar, buyuk skorlar, sponsor imzasi, wonderkid, baskan
        kaygilar            (concerns.py) oynama suresi penceresi mac sonrasi yazilir, haftalik seviye / moral /
                            maas talebi; eski "uzun sure oynamayan" moral cezasinin yerini alir (kariyer modunda)
        shortlist / friendlies
                            izleme listesi; kullanicinin haftada bir hazirlik maci (sakatlik ve kart yok,
                            kondisyon dusmez, tablo ve istatistik etkilenmez)
    * Taktik kaliciligi (13. Asama; kurallar instructions.py / team_roles.py / match_plan.py):
        team_instructions / set_team_instructions   kayitli takim talimati (teams.tactic_instructions)
        team_roles / suggest_team_roles / set_team_roles
                            kaptan + duran top aticilari (teams.set_piece_roles); okurken A takimdan ayrilan
                            oyuncular dusurulur (yazilmaz), kayitta kadro disi oyuncu TacticsError
        team_plan / set_team_plan
                            durum bazli oyun plani (teams.match_plan); okurken degisiklik oyuncusu ayrilmis
                            kurallar dusurulur, kayitta gecersiz kural / kadroda olmayan oyuncu TacticsError
        tactic_presets / save_tactic_preset / apply_tactic_preset / delete_tactic_preset
                            kulup basina en fazla MAX_TACTIC_PRESETS adli taktik (dizilis, talimat, roller, plan,
                            kadro); uygulamada ayrilan / akademideki / oynayamayan oyuncular notla atlanir
        mac entegrasyonu    kullanicinin kulubunun TUM kariyer maclari (lig, kupa, canli, hazirlik) kayitli
                            talimat, rol ve planla oynanir (manager_controlled: AI talimati dokunmaz). ai_tactics
                            (varsayilan acik): AI kulupleri durum bazli talimat (EngineConfig.ai_tactics) ve ilk
                            11'den onerilen kaptan / aticilarla oynar -> duran toplar iki taraf icin de acik;
                            kullanicinin bos (ya da mac kadrosunda olmayan) rolleri asistanca tamamlanir.
    * Paylasilan dunya (Faz 12 / 14. Asama, A2; koltuklar seats.py, kurallar world_rules.py, eklentiler extensions.py):
        CareerManager(db, ..., manager_user_id)
                            None ya da dunya sahibi -> birincil koltuk (eski tek menajer: GameState.user_team_id /
                            manager_reputation); baska kullanici -> world_managers koltugu; koltugu yoksa izleyici
                            (user_team None). acting_seat, seats (SeatStore), rules (WorldRules; bos = eski kurallar)
        human_team_ids()    insan kulupleri (birincil + aktif koltuklar). play_week / play_midweek / start_new_season
                            boyunca sabitlenir. Eski kariyerde {GameState.user_team_id} (ya da bos): sonuclar birebir
        kullaniciya donuk her adim (mac tohumu, kayitli taktik, rapor notlari, tanınırlık, maas talebi bekletme,
                            sponsor teklifi bekletme, lig/kupa odul notlari, baskan guvencesi, akademi uyarilari,
                            haber) TUM insan kulupleri icin; AI transfer penceresi insan ve korumadaki
                            (teams.ai_protected_until) kuluplerle islem yapmaz (RNG cekme sirasi ayni)
        WeekReport.clubs    odak disi insan kulupleri icin ClubWeekReport; view_for(team_id) o kulubun gorunumu.
                            Odak (focus_team_id) = oynatan koltugun kulubu: eski kariyerde ust alanlar aynen dolar
        live_allowed        canli resmi mac yalnizca kural aciksa ve dunyada tek menajer varken
        transfer_block_reason
                            yeni transfer yasagi + kiralik + kulup korumasi + eklenti nedenleri
        insan -> insan      menajer kulubundeki oyuncuya AI akisiyla teklif yapilamaz (Teklifler paneli)
        season_standings    lig arsivlenirken tum kuluplerin sirasi yazilir
        eklenti kancalari   on_week (AI penceresinden sonra), on_season_end / new_season_blocker /
                            on_season_start, on_player_moved, transfer_block_reason (extensions.load; eski kariyerde
                            hic eklenti yok). Eklentiler cm.rng'den cekmez.
        ensure_world_setup  birincil koltuk satirini idempotent kurar (game_state satir kilidiyle)
    * Sozlesme dongusu (Faz 15A; kurallar contracts.py, orkestrasyon transfer_desk.ContractCycle / ContractDesk).
      Kural bayragi contracts.CONTRACT_CYCLE (kopya basina self.contract_cycle ile ezilir); KAPALIYKEN bu adimlarin
      hicbiri calismaz ve oyun 15A oncesiyle birebir aynidir:
        play_week           AI transfer penceresi ve masadan sonra _run_contract_week: AI yenileme kararlari, on sozlesme
                            donemi (sezonun ikinci yarisi, iki yon), serbest oyuncu imzalari, insan kuluplerine uyarilar
        start_new_season    yas / sozlesme dususunden ONCE AI'nin geciken kararlari; sonra on sozlesmeler uygulanir,
                            kalan suresi biten A takim oyunculari serbest kalir (release_player: transfer_log RELEASED),
                            akademi yonetiminden sonra AI kulupleri serbest oyuncu havuzundan kadrosunu tamamlar
        release_player / sign_free_agent
                            kulupsuz birakma (team_id NULL; Team.players delete-orphan oldugu icin iliski ATANMAZ) ve
                            kulupsuz oyuncuyla imza / on sozlesmeyle katilim (transfer_log FREE_AGENT / BOSMAN)
        transfer_block_reason  on sozlesme imzalamis oyuncu sezon sonuna kadar satilamaz
    * Kalici gelen kutusu ve takvim (Faz 15D; kurallar ve uretici inbox.py). Kural bayragi inbox.INBOX (kopya
      basina self.inbox ile ezilir); KAPALIYKEN tek satir bile yazilmaz ve oyun 15D oncesiyle birebir aynidir:
        play_week / play_midweek / save_live_result
                            hafta bittikten sonra _record_inbox_week: her insan kulubunun gelen kutusuna mac sonucu,
                            sakatlik / ceza, transfer masasi notlari, kaygi / maas talebi, akademi ve HAFTA RAPORU
                            (eskiden yalnizca st.session_state'teydi) yazilir
        start_new_season    _record_inbox_season: sezon duyurusu + kulubun devir notlari
        date_bar / game_date  oyun haftasinin GERCEK tarihi (lig cumartesi, hafta ici kupa carsamba)
        inbox_for_manager   oynatan menajerin gelen kutusu (okuma / okundu / arsiv)
        continue_until      "suna kadar devam": sonraki mac / transfer donemi / sezon sonu; onemli mesajda durur
      Uretici hafta raporu NESNESINI ve career_views satirlarini DEGISTIRMEZ: mesajlar onlarin yanina yazilir.
    * Emeklilik ve yeni jenerasyon (Faz 15B; kurallar development.retirement_chance / youth.replacement_intake).
      Kural bayragi development.RETIREMENT (kopya basina self.retirement ile ezilir); KAPALIYKEN adim hic calismaz
      ve oyun 15B oncesiyle birebir aynidir:
        start_new_season    yas / sozlesme dususunden SONRA, sozlesme dongusunden (serbest birakma) ONCE
                            _retire_players: tohumlu (crc32) emeklilik. Oyuncu satiri SILINIR; transfer_log RETIRED
                            satiri adiyla kalir, haber (NewsKind.RETIREMENT) ve gelen kutusu mesaji yazilir.
                            Kadro guvencesi: hicbir kulup 13 oyuncunun / 2 kalecinin altina emeklilikle dusmez
        _youth_intake       genc girisi sayisi dunyanin nufus hedefine (game_state.population_target) ve bu sezon
                            beklenen emeklilik sayisina gore olceklenir (youth.replacement_intake; kulup basina
                            0-8); uretim dunyanin guc capasina kaydirilir (game_state.strength_target ->
                            youth.anchor_shift): yeni jenerasyonun ortalama potansiyeli dunyanin seviyesidir.
                            Mevki karisimi birakmasi beklenen kusagin dagilimiyla harmanlanir
                            (youth.replacement_positions): kaleci hatti yaslanmaz
    * Kulup secimi (Faz 13G): choose_club (web yolu) kariyer modunda kulubu KILITLER (club_locked; eski kayitlar
      dahil), turnuva modunda ilk mactan sonra kilitler ve yalnizca katilimcilari kabul eder; kariyer + kulup
      secilmisken oyun modu degismez (career_mode_locked). set_user_team kilitsiz alt seviye yazimdir (CLI, testler).

Katman: LOGIC. Terminale hicbir sey basmaz; main.py (View) sonuclari formatlar.
COMMIT ETMEZ -- cagiran taraf session_scope() ile islem sinirini belirler.
Testler ayni nedenle rollback ile temiz kalir.

Sakatlik semantigi:
    injured_until_week = oyuncunun tekrar oynayabilecegi hafta.
    W. haftada sakatlanip 'n' hafta yatan oyuncu W+1..W+n haftalarini kacirir,
    W+n+1'de doner -> injured_until_week = W + n + 1.

Ceza semantigi:
    suspended_matches > 0 iken kadroya alinmaz. Hafta sonunda, o hafta cezali
    olarak OTURAN oyuncularin sayaci 1 azalir (ayni hafta kirmizi gorenlerinki degil).
"""

from __future__ import annotations

import math
import random
import zlib
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from statistics import mean

from sqlalchemy import Numeric, and_, cast, delete, desc, event, func, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import instance_state, set_committed_value

import concerns
import contracts
import development
import extensions
import facilities
import finance
import fitness
import inbox
import reputation
import staff as staff_rules
import team_roles
import transfers
import youth
from club_directory import plain_key
from cup_draw import STAGE_LABELS, Stage, cup_size_for
from instructions import TeamInstructions
from match_engine import (
    EngineConfig,
    EventType,
    MatchEngine,
    MatchResult,
    MatchTeam,
    apply_result,
    build_match_team,
    prepare_fixture,
)
from match_plan import MatchPlan, PlanRule
from models import (
    RATING_HISTORY_SIZE,
    RELEASED_TEAM_NAME,
    RETIRED_TEAM_NAME,
    Competition,
    ContractTalk,
    Fixture,
    FixtureStatus,
    Friendly,
    GameMode,
    GameState,
    HonourKind,
    League,
    LineupStatus,
    ManagerShortlistEntry,
    NewsItem,
    NewsKind,
    Player,
    PlayerMatchStat,
    Position,
    SeasonHonour,
    SeasonStanding,
    ShortlistEntry,
    SquadRole,
    Staff,
    StaffRole,
    TacticPreset,
    Team,
    Tournament,
    TournamentStatus,
    TransferKind,
    TransferLog,
)
from name_masking import MASK_OFF, resolve_masked_club
from ratings import ENGINE_ATTRIBUTES
from schedule import build_round_robin
from seats import Seat, SeatError, SeatStore
from tactics import (
    FORMATIONS,
    MAX_BENCH,
    ROLE_ORDER,
    LineupCheck,
    pick_bench,
    pick_best_xi,
    player_power,
    role_counts,
    validate_lineup,
)
from team_roles import ROLE_FIELDS, ROLE_LABELS, SetPieceRoles
from tournament_manager import (
    CUP_SHORT_NAME,
    CUP_YELLOW_BAN_EVERY,
    TournamentManager,
    matchday_label,
)
from transfers import ContractOffer, TransferError
from world_rules import WorldRules

# ===========================================================================
# 1) SAF KURALLAR (DB bilmez, birim testi kolay)
# ===========================================================================

NEUTRAL_RATING = 6.5            # bu notun ustu iyi, alti kotu performans
MAX_FORM_SWING = 12             # tek macta form en fazla bu kadar degisir
MAX_MORALE_SWING = 12
BENCH_FORM_DRIFT = 2            # oynamayan oyuncunun formu 50'ye dogru kayar (1. hafta)
MAX_IDLE_DRIFT = 6              # ritim kaybi haftalar gectikce buyur, bu kadarla sinirli
IDLE_MORALE_AFTER_WEEKS = 3     # bu kadar hafta oynamayan mutsuzlasir
GOOD_RATING = 7.0               # bu ve ustu: iyi mac
BAD_RATING = 6.0                # bunun alti: kotu mac
RESULT_MORALE = {"W": 3, "D": 0, "L": -3}
RESULT_FORM = {"W": 1, "D": 0, "L": -1}
YELLOW_BAN_EVERY = 4            # her 4 sari kart = 1 mac ceza
STRAIGHT_RED_LONG_BAN_CHANCE = 0.4   # direkt kirmizida %40 ihtimalle 3 mac (siddet), aksi 1 mac
# (hafta, agirlik): cogu sakatlik kisa, nadiren sezonu bitiren
INJURY_TABLE: list[tuple[int, int]] = [(1, 45), (2, 25), (3, 15), (4, 5), (6, 5), (10, 5)]

# --- AI transfer pazari ---
AI_TRANSFER_CHANCE = 0.30       # bir AI kulubun o hafta pazara cikma olasiligi
AI_MAX_DEALS_PER_WEEK = 2       # tum ligler toplaminda haftalik tamamlanan transfer siniri
AI_MIN_TARGET_SCORE = 1.5       # bu puanin altindaki hedefe teklif yapilmaz

# --- Altyapi akademisi (10. Asama) ---
SENIOR_SQUAD_MAX = 25           # A takim kadrosu en fazla
ACADEMY_CAPACITY = 20           # akademi (U-21) en fazla
ACADEMY_MAX_AGE = 21            # bu yasin ustu akademide "yas ustu" sayilir
ACADEMY_OVERAGE_SLOTS = 3       # akademide 22+ yas icin kontenjan
YOUTH_INTAKE_SIZE = (3, 4)      # sezonluk genc girisi (kulup basina)
MIN_SENIOR_KEEPERS = transfers.POSITION_SALE_FLOOR[Position.GK]     # A takimda en az 2 kaleci
AI_MIN_SENIOR_SQUAD = 16        # AI kulubu A takimi bunun altindaysa akademiden yukseltir
AI_OVERAGE_PROMOTE_MARGIN = 3   # AI: yas ustu fazlasi mevkisinin en zayifindan en fazla bu kadar geride ise yukselir

# --- Emeklilik ve yeni jenerasyon (Faz 15B) ---
RETIREMENT_SQUAD_FLOOR = transfers.SQUAD_FLOOR      # 13: emeklilikle A takimi bu sayinin altina dusen kulup olmaz
RETIREMENT_NEWS_PER_SEASON = 5                      # AI kuluplerinden haber akisina cikan en degerli emekli sayisi
RETIREMENT_INBOX_LINES = 12                         # gelen kutusu mesajindaki en fazla oyuncu satiri

# --- Tesisler ve sponsorluk (11. Asama) ---
AI_FACILITY_BUDGET_SHARE = 0.05     # AI sezon basi en fazla bir tesis yatirimi: bedel kasanin bu payini asmaz
AI_SPONSOR_SWITCH_MARGIN = 0.15     # AI gecerli sozlesmesini ancak bu kadar daha degerli teklif icin birakir

# --- Kariyer paketi (12. Asama) ---
TRANSFER_BAN_WEEKS = 6              # kulup degistiren oyuncu bu kadar oyun haftasi satilamaz / teklif alamaz
NEWS_AI_TRANSFERS_PER_WEEK = 3      # haber akisina haftada en fazla bu kadar AI transferi (en pahalilar)
BIG_RESULT_MARGIN = 4               # bu ve ustu gol farki haber olur
POTS_MIN_APPEARANCE_SHARE = 0.5     # sezonun oyuncusu: ligde lig haftalarinin, kupada mac gunlerinin en az bu payi
NEWS_TEXT_MAX = 300

# --- Taktik kaliciligi (13. Asama) ---
MAX_TACTIC_PRESETS = 7              # kulup basina kayitli taktik
TACTIC_PRESET_NAME_MAX = 40         # taktik adi en fazla (tactic_presets.name String(40))

# --- Hafta isleme hizi (Faz 14D) ---
# Hafta / sezon donusumu boyunca (_seat_snapshot) her flush'in BASINDA oturumdaki kirli Player / Team / Fixture
# nesneleri, yalnizca bu sutunlari degismisse satir satir UPDATE yerine tablo basina tek
# "UPDATE ... FROM unnest(...)" ile yazilir (_bulk_write_dirty). Listede olmayan bir alani (iliski, FK, ad ...) kirli
# olan nesneye dokunulmaz: normal flush yazar. Yazim ani ayni flush'tir (nesne yasam dongusu, okuma sirasi ve
# veritabani durumu HEAD ile ayni); yalnizca ifade bicimi degisir. Birincil anahtar, yabanci anahtar ve benzersiz
# kisitli sutunlar listeye girmez.
BULK_WRITE_COLUMNS: dict[type, frozenset[str]] = {
    Player: frozenset({
        # _post_match (lig + kupa)
        "form", "morale", "condition", "weeks_since_match", "minutes_window", "match_rating_history",
        "injured_until_week", "suspended_matches", "season_yellow_cards", "cup_suspended_matches", "cup_yellow_cards",
        # _weekly_development
        "overall_rating", *ENGINE_ATTRIBUTES, "potential_rating", "market_value", "contract_overall",
        "development_progress",
        # _weekly_concerns / maas talepleri
        "concern_level", "wage_demand", "current_wage", "contract_years",
        # 15F: donem acilisinda AI transfer / kiralik listeleri (satir satir UPDATE yerine tek toplu yazim)
        "transfer_listed", "loan_listed",
        # start_new_season
        "age",
    }),
    Team: frozenset({
        "points", "played", "won", "drawn", "lost", "goals_for", "goals_against",    # puan tablosu
        "transfer_budget", "wage_budget",                                            # maaslar, butce kaydirma
    }),
    Fixture: frozenset({
        "home_score", "away_score", "status", "extra_time", "home_penalties", "away_penalties", "key_events",
    }),
}
BULK_WRITE_CHUNK = 5000             # tek ifadedeki en fazla satir
_BULK_COLUMN_CACHE: dict[type, dict[str, object]] = {}
_BULK_STATEMENT_CACHE: dict[tuple, tuple] = {}


def _bulk_update_statement(table, cols: list, dialect, masked: tuple[bool, ...] | None = None) -> tuple:
    """
    Faz 14D: 'UPDATE tablo AS t SET c = v.c, ... FROM unnest(CAST(:ids AS INTEGER[]), CAST(:p0 AS <tip>[]), ...)
    AS v(id, c, ...) WHERE t.id = v.id' ve sutunlarin baglama islemcileri (JSONB -> json metni, enum -> deger; ORM
    flush'inin kullandigi ayni islemciler). Her sutun TEK dizi parametresi: ifade metni sutun kumesine gore sabittir
    (derleme onbellekte), satir sayisi ifade boyutunu buyutmez. Tip donusumu hedef sutunun tipine acik CAST'tir.
    masked[i]: sutun her satirda degismiyorsa ek bir BOOLEAN[] maske (:m<i>) gelir ve degismeyen satirda sutun kendi
    degerini korur (c = CASE WHEN v._m<i> THEN v.c ELSE t.c END): tablonun tum satirlari tek ifadede yazilir.
    """
    masked = masked or (False,) * len(cols)
    key = (table.name, tuple(c.name for c in cols), masked, dialect.name)
    cached = _BULK_STATEMENT_CACHE.get(key)
    if cached is None:
        quote = dialect.identifier_preparer.quote
        sets, arrays, aliases = [], ["CAST(:ids AS INTEGER[])"], ["id"]
        for i, (col, mask) in enumerate(zip(cols, masked, strict=True)):
            name = quote(col.name)
            arrays.append(f"CAST(:p{i} AS {col.type.compile(dialect=dialect)}[])")
            aliases.append(name)
            if mask:
                arrays.append(f"CAST(:m{i} AS BOOLEAN[])")
                aliases.append(f"_m{i}")
                sets.append(f"{name} = CASE WHEN v._m{i} THEN v.{name} ELSE t.{name} END")
            else:
                sets.append(f"{name} = v.{name}")
        sql = (f"UPDATE {dialect.identifier_preparer.format_table(table)} AS t SET {', '.join(sets)} "
               f"FROM unnest({', '.join(arrays)}) AS v({', '.join(aliases)}) WHERE t.id = v.id")
        cached = (sql_text(sql), [c.type.bind_processor(dialect) for c in cols])
        _BULK_STATEMENT_CACHE[key] = cached
    return cached


def avg_match_rating():
    """
    Mac notu ortalamasi NUMERIC uzerinden. float8 toplami satir okuma sirasina bagli (son hanelerde) degisir;
    fiziksel sira ise veritabani gecmisine (olu satirlar, autovacuum) gore oynar. Esit notlu iki oyuncudan
    sezonun oyuncusu ya da gelisim esigi boylece rastgele secilebiliyordu. NUMERIC toplam tam ve siradan bagimsiz.
    """
    return func.avg(cast(PlayerMatchStat.rating, Numeric))


def clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return int(max(lo, min(hi, round(value))))


def _seats(count: int) -> str:
    """Koltuk sayisi Turkce binlik ayraciyla: 45000 -> '45.000'."""
    return f"{int(count):,}".replace(",", ".")


def form_delta(rating: float, outcome: str | None = None) -> int:
    """
    Mac notuna gore form degisimi (+ kazanan takima kucuk bonus).
    8.5 -> +8, 7.0 -> +2, 6.5 -> 0, 6.0 -> -2, 5.0 -> -6.
    """
    bonus = RESULT_FORM[outcome] if outcome else 0
    return clamp((rating - NEUTRAL_RATING) * 4 + bonus, -MAX_FORM_SWING, MAX_FORM_SWING)


def morale_delta(rating: float | None, outcome: str) -> int:
    """
    Moral degisimi = kisisel performans + takim sonucu.
        not >= 7.0 : +3 ve ustu (ne kadar iyi, o kadar fazla)
        not <  6.0 : -4 ve alti  -> galibiyette bile net dusus
        arasi      : sadece sonuc etkisi (G +3, B 0, M -3)
    rating None ise oyuncu oynamamistir; sadece sonuc etkisinin yarisini alir.
    """
    result = RESULT_MORALE[outcome]
    if rating is None:
        return round(result / 2)
    if rating >= GOOD_RATING:
        perf = 3 + round((rating - GOOD_RATING) * 2)
    elif rating < BAD_RATING:
        perf = -4 - round((BAD_RATING - rating) * 2)
    else:
        perf = 0
    return clamp(perf + result, -MAX_MORALE_SWING, MAX_MORALE_SWING)


def bench_form_drift(form: int, weeks_idle: int = 1) -> int:
    """
    Oynamayan oyuncunun formu notre (50) dogru kayar: mac ritmi kaybi.
    Kademeli: 1. hafta 2, 2. hafta 3, 3. hafta 4 ... en fazla MAX_IDLE_DRIFT.
    """
    step = min(BENCH_FORM_DRIFT + max(0, weeks_idle - 1), MAX_IDLE_DRIFT)
    if form > 50:
        return -min(step, form - 50)
    if form < 50:
        return min(step, 50 - form)
    return 0


def idle_morale_penalty(weeks_idle: int) -> int:
    """
    Uzun sure oynamayan oyuncu mutsuzlasir. 12. Asama: yalnizca turnuva modunda uygulanir; kariyer modunda
    oynama suresi kaygilari (concerns.py, _weekly_concerns) bunun yerine gecer (cift sayim yok).
    """
    return -1 if weeks_idle >= IDLE_MORALE_AFTER_WEEKS else 0


def injury_weeks(rng: random.Random) -> int:
    """Sakatlik suresi (hafta), INJURY_TABLE agirliklariyla."""
    weeks, weights = zip(*INJURY_TABLE, strict=True)
    return rng.choices(weeks, weights=weights, k=1)[0]


def suspension_length(rng: random.Random, second_yellow: bool) -> int:
    """Ikinci sari: 1 mac. Direkt kirmizi: 1 mac (son adam) veya 3 mac (siddet)."""
    if second_yellow:
        return 1
    return 3 if rng.random() < STRAIGHT_RED_LONG_BAN_CHANCE else 1


def outcome_for(goals_for: int, goals_against: int) -> str:
    if goals_for > goals_against:
        return "W"
    if goals_for == goals_against:
        return "D"
    return "L"


def standings_key(team: Team) -> tuple:
    """Puan, averaj, atilan gol, isim. Siralama icin (buyukten kucuge)."""
    return (-team.points, -team.goal_difference, -team.goals_for, team.name)


# ===========================================================================
# 2) RAPOR NESNELERI (View bunlari formatlar)
# ===========================================================================

@dataclass
class PlayerNote:
    player_id: int
    player_name: str
    team_name: str
    detail: str


@dataclass
class DevelopmentNote(PlayerNote):
    """Kullanicinin oyuncusunun haftalik guc degisimi (gelisim ya da yaslanma)."""
    age: int = 0
    old_overall: int = 0
    new_overall: int = 0
    potential_low: int | None = None         # gozlemci tahmini (yaslanmada None)
    potential_high: int | None = None
    in_academy: bool = False


@dataclass
class YouthIntakeNote(PlayerNote):
    """Kullanicinin akademisine katilan genc (potansiyel gozlemci tahminidir)."""
    age: int = 0
    position: str = ""
    overall: int = 0
    potential_low: int = 0
    potential_high: int = 0
    wonderkid: bool = False                  # tahmini potansiyel ortasina gore


@dataclass
class ClubWeekReport:
    """
    Bir insan kulubunun haftalik rapor alanlari (Faz 12). Alan adlari WeekReport'un kullaniciya donuk alanlariyla
    AYNIDIR: odak kulup WeekReport'un ust alanlarina, diger insan kulupleri WeekReport.clubs'a yazilir.
    """
    user_result: MatchResult | None = None
    lineup_notes: list[str] = field(default_factory=list)
    finance_note: str | None = None
    manager_reputation: tuple[float, float] | None = None
    season_reputation_delta: float | None = None
    user_cup_result: MatchResult | None = None
    development_notes: list[PlayerNote] = field(default_factory=list)
    youth_intake: list[PlayerNote] = field(default_factory=list)
    academy_notes: list[str] = field(default_factory=list)
    sponsor_income: int = 0
    gate_income: int = 0
    tv_income: int = 0
    prize_income: int = 0
    prize_notes: list[str] = field(default_factory=list)
    concern_notes: list[PlayerNote] = field(default_factory=list)
    wage_demands: list[PlayerNote] = field(default_factory=list)
    transfer_notes: list[str] = field(default_factory=list)      # 13H transfer masasi (gelen teklif, taksit ...)


CLUB_REPORT_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(ClubWeekReport))


@dataclass
class WeekReport:
    season: int
    week: int
    results: list[tuple[Fixture, MatchResult]] = field(default_factory=list)
    injuries: list[PlayerNote] = field(default_factory=list)
    suspensions: list[PlayerNote] = field(default_factory=list)
    user_result: MatchResult | None = None
    season_finished: bool = False
    lineup_notes: list[str] = field(default_factory=list)   # kullanicinin takimi icin asistan notlari
    transfers: list[TransferNews] = field(default_factory=list)
    finance_note: str | None = None                        # kullanicinin takimi icin maas ozeti
    # Menajer tanınırlığı (once, sonra). Kullanici takimi oynamadiysa None.
    manager_reputation: tuple[float, float] | None = None
    season_reputation_delta: float | None = None            # sezon bu hafta bittiyse
    # --- Devler Arenasi (8. Asama) ---
    cup_label: str | None = None                            # "Devler Arenası · Son 16 ilk maç"
    cup_results: list[tuple[Fixture, MatchResult]] = field(default_factory=list)
    cup_notes: list[str] = field(default_factory=list)      # tur atlayanlar, kura, sampiyon
    user_cup_result: MatchResult | None = None
    cup_champion: Team | None = None
    # --- Canli mac (9. Asama) ---
    midweek_only: bool = False          # play_midweek: yalnizca hafta ici kupa, hafta ilerlemedi
    # --- Gelisim ve altyapi (10. Asama) ---
    development_notes: list[PlayerNote] = field(default_factory=list)   # DevelopmentNote: kullanicinin oyunculari
    youth_intake: list[PlayerNote] = field(default_factory=list)        # YouthIntakeNote: kullanicinin yeni gencleri
    youth_intake_total: int = 0                                         # bu hafta tum kuluplere gelen genc sayisi
    academy_notes: list[str] = field(default_factory=list)              # kullanicinin akademisi: kapasite vb.
    # --- Tesisler ve sponsorluk (11. Asama): kullanicinin kulubunun bu haftaki gelirleri (EUR) ---
    sponsor_income: int = 0
    gate_income: int = 0
    # --- Kariyer paketi (12. Asama): kullanicinin kulubu ---
    tv_income: int = 0                                                  # bu haftanin lig TV payi (EUR)
    prize_income: int = 0                                               # bu hafta kasaya giren lig/kupa odulleri
    prize_notes: list[str] = field(default_factory=list)                # "Lig ödülü (2. sıra): 4.3M EUR" vb.
    honours_notes: list[str] = field(default_factory=list)              # bu hafta arsivlenen sampiyonluklar (tum dunya)
    concern_notes: list[PlayerNote] = field(default_factory=list)       # kaygisi artan oyuncular
    wage_demands: list[PlayerNote] = field(default_factory=list)        # bu hafta yeni sozlesme isteyenler
    # --- 13H transfer masasi: gelen AI teklifleri, kulup yanitlari, taksit / ek odeme, tamamlanan anlasmalar
    transfer_notes: list[str] = field(default_factory=list)
    # --- Faz 12: paylasilan dunya. Ust alanlar odak kulubundur (eski kariyer: kullanicinin kulubu); diger insan
    # kuluplerinin ayni alanlari clubs'ta (takim id -> ClubWeekReport). None: CareerManager kullanicinin kulubunu
    # odak sayar (dogrudan kurulan rapor).
    clubs: dict[int, ClubWeekReport] = field(default_factory=dict)
    focus_team_id: int | None = None

    @property
    def played_any(self) -> bool:
        return bool(self.results or self.cup_results)

    def view_for(self, team_id: int | None) -> WeekReport:
        """
        Kulubun gorunumu: dunya alanlari (sonuclar, sakatliklar, kupa, arsiv ...) aynen, kullaniciya donuk alanlar o
        kulubun (yoksa bos). Kopya doner (career_views.week_report_lines degismeden kullanilir); clubs bos.
        """
        if team_id is not None and team_id == self.focus_team_id:
            source = self
        else:
            source = self.clubs.get(team_id) if team_id is not None else None
            source = source if source is not None else ClubWeekReport()
        values = {}
        for name in CLUB_REPORT_FIELDS:
            value = getattr(source, name)
            values[name] = list(value) if isinstance(value, list) else value
        return replace(self, clubs={}, focus_team_id=team_id, **values)


@dataclass
class LivePreparation:
    """
    Canli oynanacak kariyer maci (prepare_live_match). Yalnizca id ve duz degerler tutar:
    arayuz veritabani oturumunu kapattiktan sonra da guvenle saklanabilir. Motor ORM bilmez.
    midweek_report: lig macindan once bekleyen hafta ici kupa maclari oynatildiysa onun raporu.
    """
    fixture_id: int
    competition: Competition
    engine: MatchEngine
    season: int
    week: int
    title: str                          # "Lig · 3. hafta · A - B" / "Devler Arenası · Final · A - B"
    managed_team_id: int
    midweek_report: WeekReport | None = None


@dataclass
class TransferNews:
    player_name: str
    from_team: str
    to_team: str
    fee: int
    wage: int
    # 12. Asama (varsayilanli: eski konumsal kullanim bozulmaz)
    player_id: int | None = None
    from_team_id: int | None = None
    to_team_id: int | None = None
    kind: str = TransferKind.TRANSFER.value

    def describe(self) -> str:
        return (f"{self.player_name}: {self.from_team} → {self.to_team} "
                f"({finance.format_money(self.fee)}, {finance.format_money(self.wage)}/hafta)")


@dataclass
class ScorerRow:
    player: Player
    team: Team
    goals: int
    assists: int
    appearances: int
    avg_rating: float


@dataclass
class ClubHonours:
    """club_honours(): kulubun kupalari. titles: yarisma adi -> sampiyonluk sayisi."""
    team_id: int
    team_name: str
    titles: dict[str, int]
    league_titles: int
    cup_titles: int
    runner_up_finishes: int
    honours: list[SeasonHonour]              # kazanilan sampiyonluklar (yeniden eskiye)
    runner_ups: list[SeasonHonour]           # ikincilikler (yeniden eskiye)

    @property
    def total_titles(self) -> int:
        return self.league_titles + self.cup_titles


@dataclass
class ShortlistRow:
    """shortlist() satiri. asking_price: kullanicinin kulubune istenen bonservis (satilik degilse None)."""
    player: Player
    player_id: int
    name: str
    age: int
    position: str
    overall: int
    team_id: int | None
    team_name: str | None                    # None: kulupsuz
    market_value: int
    asking_price: int | None
    in_academy: bool
    transfer_banned: bool
    ban_reason: str
    note: str | None
    added_season: int
    added_week: int


@dataclass
class FriendlyResult:
    """play_friendly() sonucu. match: motorun tam sonucu (arayuz mac akisini gosterebilir)."""
    friendly_id: int
    season: int
    week: int
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str
    home_score: int
    away_score: int
    goals: list[dict]
    match: MatchResult

    @property
    def score(self) -> str:
        return f"{self.home_score}-{self.away_score}"


# ===========================================================================
# 3) KONTROLCU
# ===========================================================================

class SeasonNotFinished(Exception):
    pass


class LiveMatchError(ValueError):
    """Canli kariyer maci hazirlanamadi / kaydedilemedi (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


LIVE_SHARED_REFUSAL = ("Paylaşılan dünyada resmi maçlar hafta ilerlerken birlikte oynanır; "
                       "maçını sonra izleyebilirsin.")


class AcademyError(ValueError):
    """Akademi / A takim kadro kurali ihlali (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


class FacilityError(ValueError):
    """Tesis yatirimi / sponsor imzasi yapilamadi (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


class ConcernError(ValueError):
    """Maas talebine cevap verilemedi (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


class ShortlistError(ValueError):
    """Izleme listesi islemi yapilamadi (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


class FriendlyError(ValueError):
    """Hazirlik maci oynanamadi (mesaj Turkce, dogrudan kullaniciya gosterilir)."""


class TacticsError(ValueError):
    """Taktik (talimat, rol, oyun plani, kayitli taktik) kaydedilemedi / uygulanamadi (mesaj Turkce, gosterilir)."""


class ClubChoiceError(ValueError):
    """Kulup secimi reddedildi: kilitli kariyer, turnuva disi kulup, paylasilan dunya (mesaj Turkce, gosterilir)."""


CLUB_LOCKED_TEXT = ("{club} bu kariyerde senin kulübün: kariyer modunda seçilen kulüp değiştirilemez "
                    "(istifa ve iş başvurusu ileride gelecek).")
CLUB_SHARED_TEXT = "Paylaşılan dünyada kulübünü dünya panelinden seçersin."
MODE_LOCKED_TEXT = "Kulübünü seçtin: kariyer modu kilitli, oyun modu değiştirilemez."
# 15C: gorevden alinan / istifa eden menajer kulubu serbestce secemez; is ilanina basvurup teklif bekler.
BOARD_UNEMPLOYED_TEXT = ("Şu anda kulüpsüzsün: yeni kulübü 🏛️ Yönetim Kurulu sayfasındaki iş ilanlarına "
                         "başvurarak bulursun.")


@dataclass(frozen=True)
class TacticPresetView:
    """
    Kayitli taktigin salt okunur gorunumu (tactic_presets). Oyuncu id'leri kaydedildigi gibidir: kulupten
    ayrilanlar ancak apply_tactic_preset'te atlanir. lineup_size: kaydedilen ilk 11 + kulube oyuncu sayisi.
    """
    id: int
    name: str
    formation: str
    instructions: TeamInstructions
    roles: SetPieceRoles
    plan: MatchPlan
    lineup_size: int
    created_season: int
    created_week: int


class CareerManager:
    def __init__(
        self,
        db,
        seed: int | None = None,
        engine_config: EngineConfig | None = None,
        ai_tactics: bool = True,
        *,
        manager_user_id: int | None = None,
    ) -> None:
        self.db = db
        self.seed = seed
        self.rng = random.Random(seed)
        self.engine_config = engine_config
        # 13. Asama: kariyer maclarinda AI kulupleri durum bazli talimat (EngineConfig.ai_tactics) ve onerilen
        # kaptan / duran top aticilari kullanir; kullanicinin bos rolleri asistanca tamamlanir. False: eski motor
        # davranisi (yalnizca kullanicinin kayitli taktigi uygulanir).
        self.ai_tactics = ai_tactics
        # start_new_season: kullanicinin akademisi icin notlar (otomatik tasima yok)
        self.new_season_notes: list[str] = []
        # 12. Asama: bu yoneticinin odedigi sezon sonu odemeleri, takim id -> {"league_prize", "chairman"}
        # (start_new_season basinda sifirlanir; arayuz ve testler icin salt bilgi)
        self.season_payouts: dict[int, dict[str, int]] = {}
        # Faz 12: oynatan menajer (None = birincil koltuk / sistem), koltuklar, insan kulupleri anlik goruntusu
        self.manager_user_id = manager_user_id
        self.seats = SeatStore(db)
        # start_new_season: insan kulubu -> notlar (new_season_notes odak kulubun listesidir)
        self.new_season_notes_by_team: dict[int, list[str]] = {}
        self._human_ids: frozenset[int] | None = None           # play_week / play_midweek / start_new_season boyunca
        self._protected_ids: frozenset[int] | None = None       # ayni sure: koruma altindaki kulupler
        self._extension_list: list | None = None
        self._actor: tuple[str, int | None] | None = None       # ("primary", None) / ("seat", id) / ("none", None)
        # Faz 14D: hafta / sezon donusumu boyunca GameState'e guclu referans (state her erisimde SELECT atmasin) ve
        # toplu yazicinin before_flush dinleyicisi (_seat_snapshot kurar / kaldirir)
        self._pinned_state: GameState | None = None
        self._bulk_listener = None
        self._week_teams: list[Team] = []      # Faz 14D: bu haftanin lig takimlari (toplu okuma; hafta boyunca tutulur)
        # Faz 15A: sozlesme dongusu bayragi (None: contracts.CONTRACT_CYCLE) ve on sozlesme tutmalari onbellegi
        # (oyuncu id -> transfer engeli metni; dongu kapaliyken hic okunmaz)
        self.contract_cycle: bool | None = None
        self._contract_holds: dict[int, str] | None = None
        # Faz 15D: kalici gelen kutusu bayragi (None: inbox.INBOX). False -> hafta / devir sonunda tek SQL bile
        # atilmaz, oyun 15D oncesiyle birebir aynidir.
        self.inbox: bool | None = None
        # Faz 15C: yonetim kurulu bayragi (None: board.BOARD). False -> guven, uyari, kovulma ve is piyasasi hic
        # calismaz; oyun 15C oncesiyle birebir aynidir. Paylasilan dunyada ayrica world_rules.board_confidence
        # acik olmalidir (sahip karari K-S4; kapaliyken bayrak acik olsa da calismaz).
        self.board: bool | None = None
        # Faz 15B: emeklilik ve yeni jenerasyon bayragi (None: development.RETIREMENT). False -> sezon devrinde
        # emeklilik adimi hic calismaz ve genc girisi eski sabit YOUTH_INTAKE_SIZE ile uretilir: oyun 15B
        # oncesiyle birebir aynidir.
        self.retirement: bool | None = None

    # ------------------------------------------------------------------ durum

    @property
    def state(self) -> GameState:
        pinned = self._pinned_state
        if pinned is not None:
            insp = sa_inspect(pinned)
            if insp.session is self.db and not insp.deleted and not insp.detached:
                return pinned
        st = self.db.get(GameState, 1)
        if st is None:
            st = GameState(id=1, season=1, current_week=1)
            self.db.add(st)
            self.db.flush()
        return st

    @property
    def season(self) -> int:
        return self.state.season

    @property
    def current_week(self) -> int:
        return self.state.current_week

    @property
    def user_team(self) -> Team | None:
        """Oynatan menajerin kulubu: birincil koltukta GameState, diger koltukta koltuk satiri (izleyici: None)."""
        if self._is_primary_actor():
            return self.state.user_team
        team_id = self._acting_team_id()
        return self.db.get(Team, team_id) if team_id is not None else None

    @property
    def manager_reputation(self) -> float:
        """Oynatan menajerin tanınırlığı (birincil: GameState; izleyici: baslangic degeri)."""
        if self._is_primary_actor():
            return self.state.manager_reputation
        seat = self.acting_seat
        return seat.reputation if seat is not None else reputation.START_REPUTATION

    @property
    def career_week(self) -> int:
        """Mutlak kariyer haftasi: onceki sezonlarin haftalari + bu sezonun haftasi (sezon devrinde kesintisiz)."""
        st = self.state
        return int(st.career_week_offset or 0) + int(st.current_week)

    # ------------------------------------------------------------------ koltuklar ve dunya kurallari (Faz 12)

    @property
    def rules(self) -> WorldRules:
        """Dunya kurallari (GameState.world_rules; bos {} = eski tek kisilik kariyer)."""
        return WorldRules.from_dict(self.state.world_rules)

    def _resolve_actor(self) -> tuple[str, int | None]:
        if self._actor is None:
            uid = self.manager_user_id
            st = self.state
            if uid is None or (st.user_id is not None and st.user_id == uid):
                self._actor = ("primary", None)
            else:
                seat = self.seats.resolve(uid)
                if seat is None:
                    self._actor = ("none", None)
                elif seat.is_primary:
                    self._actor = ("primary", None)
                else:
                    self._actor = ("seat", seat.id)
        return self._actor

    def _is_primary_actor(self) -> bool:
        return self._resolve_actor()[0] == "primary"

    @property
    def acting_seat(self) -> Seat | None:
        """Oynatan menajerin koltugu (guncel anlik goruntu). Koltugu olmayan kullanici (izleyici): None."""
        kind, seat_id = self._resolve_actor()
        if kind == "primary":
            return self.seats.primary()
        return self.seats.by_id(seat_id) if kind == "seat" else None

    def _acting_team_id(self) -> int | None:
        """Oynatan menajerin kulubu (birincil: GameState.user_team_id; ACTIVE olmayan koltuk ya da izleyici: None)."""
        kind, seat_id = self._resolve_actor()
        if kind == "primary":
            return self.state.user_team_id
        seat = self.seats.by_id(seat_id) if kind == "seat" else None
        return seat.team_id if seat is not None and seat.active else None

    def refresh_seats(self) -> None:
        """Koltuk degisikliginden sonra (kulup alma / birakma, katilim) onbellekleri yeniler."""
        self._actor = None
        self._extension_list = None
        if self._human_ids is not None:
            self._human_ids = self.seats.human_team_ids()
            self._protected_ids = None
            self._protected_ids = self._protected_team_ids()

    def human_team_ids(self) -> frozenset[int]:
        """
        Insan menajerlerin kulupleri (birincil + ACTIVE koltuklar). Eski kariyerde {GameState.user_team_id} (takim
        secilmemisse bos). play_week / play_midweek / start_new_season boyunca bir kez okunur.
        """
        if self._human_ids is not None:
            return self._human_ids
        return self.seats.human_team_ids()

    @contextmanager
    def _seat_snapshot(self) -> Iterator[None]:
        """
        Hafta / sezon donusumu boyunca insan kulupleri ve eklentiler sabit (ic ice cagri disaridakini kullanir).
        Faz 14D: ayni sure GameState sabitlenir (state tek okuma) ve flush'lar toplu yaziciyla calisir.
        """
        outer = self._human_ids is None
        try:
            if outer:
                # Faz 14D: once yalnizca okunur (yoksa OLUSTURULMAZ: olusturma ani HEAD'deki gibi state'te kalir);
                # asagidaki koltuk / koruma okumalari ayni nesneyi kimlik haritasindan alir
                self._pinned_state = self.db.get(GameState, 1)
                self._extension_list = None
                self._human_ids = self.seats.human_team_ids()
                self._protected_ids = self._protected_team_ids()
                self._pinned_state = self.state
                self._start_bulk_writes()
            yield
        finally:
            if outer:
                self._stop_bulk_writes()
                self._pinned_state = None
                self._week_teams = []
                self._contract_holds = None
                self._human_ids = None
                self._protected_ids = None

    # ------------------------------------------------------------------ toplu yazim (Faz 14D)

    def _start_bulk_writes(self) -> None:
        """_seat_snapshot girisi: oturumun her flush'i once _bulk_write_dirty'yi calistirir."""
        if self._bulk_listener is None and isinstance(self.db, Session):
            listener = self._before_flush
            event.listen(self.db, "before_flush", listener)
            self._bulk_listener = listener

    def _stop_bulk_writes(self) -> None:
        listener, self._bulk_listener = self._bulk_listener, None
        if listener is not None:
            event.remove(self.db, "before_flush", listener)

    def _before_flush(self, session, _flush_context, instances) -> None:
        # flush(objects) yalnizca verilen nesneleri yazar: digerlerine dokunulmaz
        if session is self.db and instances is None:
            self._bulk_write_dirty()

    @staticmethod
    def _bulk_columns(model: type) -> dict[str, object]:
        """Modelin toplu yazilabilir ozellikleri -> tablo sutunu (yalnizca duz, PK / FK olmayan sutunlar)."""
        cache = _BULK_COLUMN_CACHE
        if model not in cache:
            mapper = sa_inspect(model)
            table = model.__table__
            columns = {}
            for key in BULK_WRITE_COLUMNS.get(model, ()):
                col = mapper.columns.get(key)
                if col is not None and col.table is table and not col.primary_key and not col.foreign_keys:
                    columns[key] = col
            cache[model] = columns
        return cache[model]

    def _bulk_write_dirty(self) -> int:
        """
        Oturumdaki kirli Player / Team / Fixture nesnelerinden yalnizca BULK_WRITE_COLUMNS sutunlari degismis olanlari
        yazar: tablo basina (BULK_WRITE_CHUNK satirda bolunerek) birincil anahtar sirasiyla TEK
        "UPDATE tablo SET ... FROM unnest(...) WHERE tablo.id = v.id" ifadesi. Yalnizca degisen sutunlar yazilir
        (satirda degismeyen sutun maskeyle kendi degerini korur); degisiklik karsilastirmasi flush'inkiyle ayni
        (impl.is_equal). Ardindan her yazilan ozellik ORM'de 'kaydedilmis deger' olur (set_committed_value): flush ayni
        degeri tekrar yazmaz; nesnenin kendisi flush'ta yine islenir (yasam dongusu ve olaylar normal flush'la ayni).
        Donus: yazilan satir sayisi.
        """
        by_table: dict[str, list[tuple[int, object, frozenset[str]]]] = {}
        models: dict[str, type] = {}
        for obj in self.db.dirty:
            model = type(obj)
            columns = self._bulk_columns(model) if model in BULK_WRITE_COLUMNS else None
            if not columns:
                continue
            state = instance_state(obj)
            committed = state.committed_state
            if not committed or state.key is None or state.deleted or not committed.keys() <= columns.keys():
                continue
            if state.mapper._is_orphan(state):
                continue
            dict_ = state.dict
            changed: list[str] | None = []
            for key, old in committed.items():
                if key not in dict_:
                    changed = None
                    break
                if state.manager[key].impl.is_equal(dict_[key], old) is not True:
                    changed.append(key)
            if not changed:
                continue
            name = model.__tablename__
            models[name] = model
            by_table.setdefault(name, []).append((state.key[1][0], obj, frozenset(changed)))

        written = 0
        dialect = self.db.get_bind().dialect
        for name, rows in sorted(by_table.items()):
            columns = self._bulk_columns(models[name])
            rows.sort(key=lambda row: row[0])
            for start in range(0, len(rows), BULK_WRITE_CHUNK):
                chunk = rows[start:start + BULK_WRITE_CHUNK]
                keys = sorted(frozenset().union(*(changed for _pk, _obj, changed in chunk)))
                masked = tuple(any(key not in changed for _pk, _obj, changed in chunk) for key in keys)
                stmt, processors = _bulk_update_statement(
                    models[name].__table__, [columns[key] for key in keys], dialect, masked)
                params: dict[str, list] = {"ids": [pk for pk, _obj, _changed in chunk]}
                dicts = [instance_state(obj).dict for _pk, obj, _changed in chunk]
                for i, (key, proc, mask) in enumerate(zip(keys, processors, masked, strict=True)):
                    flags = [key in changed for _pk, _obj, changed in chunk]
                    params[f"p{i}"] = [
                        (d[key] if proc is None else proc(d[key])) if flag else None
                        for d, flag in zip(dicts, flags, strict=True)
                    ]
                    if mask:
                        params[f"m{i}"] = flags
                self.db.execute(stmt, params)
                for _pk, obj, changed in chunk:
                    dict_ = instance_state(obj).dict
                    for key in changed:
                        set_committed_value(obj, key, dict_[key])
                written += len(chunk)
        return written

    def manager_reputation_for(self, team: Team) -> float:
        """Insan kulubu icin menajerinin gercek tanınırlığı; AI kulupleri icin itibardan turetilen."""
        if team.id == self.state.user_team_id:
            return self.state.manager_reputation
        if team.id in self.human_team_ids():
            rep = self.seats.reputation_for_team(team.id)
            if rep is not None:
                return rep
        return reputation.ai_manager_reputation(team.reputation)

    def live_allowed(self) -> bool:
        """
        Canli resmi mac: kural acik ve dunyada tek menajer (birincil). Paylasilan dunyada ikinci uye (kulubu alinmis
        olsa da) katildiginda resmi maclar hafta ilerlerken birlikte oynanir; hazirlik maclari etkilenmez.
        """
        return self.rules.live_matches and self.seats.member_count() <= 1

    def lock_rows(self, model, ids: Iterable[int]) -> list:
        """
        Satirlari id sirasiyla FOR UPDATE kilitler ve guncel degerleri okur (populate_existing). Once flush edilir:
        oturumdaki yazilmamis degisiklikler kaybolmaz. Kilit sirasi: teklifler -> oyuncular -> kulupler -> personel.
        """
        keys = sorted({int(i) for i in ids if i is not None})
        if not keys:
            return []
        self.db.flush()
        pk = model.__mapper__.primary_key[0]
        return list(self.db.scalars(
            select(model).where(pk.in_(keys)).order_by(pk).with_for_update()
            .execution_options(populate_existing=True)
        ))

    def ensure_world_setup(self) -> list[str]:
        """
        Eski kayitlar ve yeni dunyalar icin IDEMPOTENT: birincil koltuk satiri yoksa kurulur (sahibin kullanici
        adiyla). Es zamanli iki giris game_state satir kilidiyle sirali calisir. Yapilanlar (Turkce) listesi.
        """
        self.db.flush()
        st = self.db.get(GameState, 1, with_for_update=True, populate_existing=True) or self.state
        existed = self.seats.primary().id is not None
        self.seats.ensure_primary_row(st.user_id, None)        # varsa yalnizca sahipsiz satiri sahibine baglar
        self.refresh_seats()
        notes = [] if existed else ["menajer koltuğu kaydedildi"]
        if self.rules.internationals:                          # Faz 12C: milli takimlar ve is teklifleri ilk giriste
            import national_teams
            notes += national_teams.NationalTeams(self).ensure_setup()
        return notes

    def _extensions(self) -> list:
        """Dunya kurallarinin actigi eklentiler (eski kariyer: bos). Hafta / sezon donusumu basinda yeniden yuklenir."""
        if self._extension_list is None:
            self._extension_list = extensions.load(self)
        return self._extension_list

    def run_extensions(self, hook: str, *args) -> None:
        """Eklenti kancasini sirayla cagirir (orn. dunya paneli: run_extensions("on_club_released", team_id))."""
        for extension in self._extensions():
            getattr(extension, hook)(*args)

    def _report_focus(self, report: WeekReport | None) -> int | None:
        if report is not None and report.focus_team_id is not None:
            return report.focus_team_id
        return self._acting_team_id()

    def _sink(self, report: WeekReport, team_id: int) -> WeekReport | ClubWeekReport:
        """Insan kulubunun rapor alanlari: odak kulup -> raporun ust alanlari, digerleri -> report.clubs."""
        if team_id == self._report_focus(report):
            return report
        return report.clubs.setdefault(team_id, ClubWeekReport())

    # ------------------------------------------------------------------ oyun modu

    @property
    def tournaments(self) -> TournamentManager:
        if getattr(self, "_tournaments", None) is None:
            self._tournaments = TournamentManager(self)
        return self._tournaments

    @property
    def mode_chosen(self) -> bool:
        return self.state.game_mode is not None

    @property
    def game_mode(self) -> GameMode:
        """Secilmemisse kariyer modu gibi davranilir (CLI ve eski kayitlar icin)."""
        return self.state.game_mode or GameMode.CAREER

    def can_change_mode(self) -> bool:
        """
        Mod yalnizca sezon basinda, hicbir mac (lig/kupa) oynanmamisken degisebilir. Faz 12: paylasilan dunya
        kariyer modunda sabittir (mod secilmemisse bir kez kariyer secilebilir).
        """
        if self.state.game_mode is not None and self.rules.shared:
            return False
        played = self.db.scalar(
            select(func.count()).select_from(Fixture).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.PLAYED
            )
        )
        return self.current_week == 1 and not played

    def set_game_mode(self, mode: GameMode) -> None:
        """
        Modu kaydeder, sezonun turnuvasini hazirlar ve kupa takvimini moda gore kurar
        (kariyer: lig haftalarina yayilir, turnuva: 1. haftadan itibaren her hafta).
        Turnuva modunda kullanicinin takimi katilimci degilse takim secimi sifirlanir.
        Faz 12: paylasilan dunyada yalnizca kariyer modu (ValueError).
        """
        st = self.state
        if st.game_mode is mode:
            return
        if mode is not GameMode.CAREER and self.rules.shared:
            raise ValueError("Paylaşılan dünyalar yalnızca kariyer modunda oynanır.")
        if self.career_mode_locked():
            raise ValueError(MODE_LOCKED_TEXT)
        if not self.can_change_mode():
            raise ValueError("Sezon başladıktan sonra oyun modu değiştirilemez.")
        st.game_mode = mode
        t = self.tournaments.ensure()
        if t is not None:
            self.tournaments.refresh_calendar(t)
        if mode is GameMode.TOURNAMENT and not self.tournaments.is_participant(t, st.user_team_id):
            st.user_team_id = None
            st.user_team = None
        self.db.flush()

    def reset_game_mode(self) -> None:
        """Mod secim ekranina don (sadece sezon basinda; paylasilan dunyada hic)."""
        if self.rules.shared:
            raise ValueError("Paylaşılan dünyalar yalnızca kariyer modunda oynanır.")
        if self.career_mode_locked():
            raise ValueError(MODE_LOCKED_TEXT)
        if not self.can_change_mode():
            raise ValueError("Sezon başladıktan sonra oyun modu değiştirilemez.")
        self.state.game_mode = None
        self.db.flush()

    # ------------------------------------------------------------------ kulup secimi (Faz 13G)

    def career_mode_locked(self) -> bool:
        """
        Kariyer modu (acikca secilmis) + kulup secilmis: mod degisikligi kapali. Aksi halde kariyer -> turnuva ->
        kariyer donusuyle kilitli kulup degistirilebilirdi (turnuva modu katilimci olmayan kulubu siler).
        """
        st = self.state
        return st.game_mode is GameMode.CAREER and st.user_team_id is not None and not self.rules.shared

    def club_locked(self) -> bool:
        """
        Menajer kulubunu degistirebilir mi? (True = kilitli)
            kariyer modu   kulup bir kez secilince kilitli (eski kayitlar dahil; istifa / is basvurusu gelecek is)
            turnuva modu   turnuva basladiktan sonra (ilk mac oynandiysa) kilitli; oncesinde katilimcilar arasinda serbest
            paylasilan     kulup yalnizca dunya panelinden alinir (bu yoldan hic degismez)
            mod secilmemis kilitsiz (CLI / testler; web once mod ekranini gosterir)
        """
        if self.rules.shared or not self._is_primary_actor():
            return True
        st = self.state
        if st.user_team_id is None:
            return False
        if st.game_mode is GameMode.TOURNAMENT:
            return not self.can_change_mode()
        return st.game_mode is GameMode.CAREER

    def choose_club(self, team: Team) -> list[str]:
        """
        Menajerin (web) kulup secimi; kurallar club_locked'ta. Turnuva modunda yalnizca bu sezonun katilimcilari.
        Kulubun kayitli ilk 11'i gecersizse (yeni dunyada kulubede 20 oyuncu gibi) asistan kadroyu kurar: menajer
        hatasiz bir kadroyla baslar. Reddedilirse ClubChoiceError (hicbir sey yazilmaz). Donus: kullanici notlari.
        """
        if self.rules.shared or not self._is_primary_actor():
            raise ClubChoiceError(CLUB_SHARED_TEXT)
        st = self.state
        if st.game_mode is None:
            raise ClubChoiceError("Önce oyun modunu seç.")
        if st.user_team_id == team.id:
            return []
        if self.board_unemployed():                  # 15C: kovulan / istifa eden menajer is piyasasindan doner
            raise ClubChoiceError(BOARD_UNEMPLOYED_TEXT)
        if self.club_locked():
            current = self.db.get(Team, st.user_team_id)
            raise ClubChoiceError(CLUB_LOCKED_TEXT.format(club=current.name if current is not None else "Kulübün"))
        if st.game_mode is GameMode.TOURNAMENT:
            t = self.tournaments.current()
            if t is not None and not self.tournaments.is_participant(t, team.id):
                raise ClubChoiceError(f"{team.name} bu sezon Devler Arenası'nda yok: turnuva modunda yalnızca "
                                      "katılımcı kulüpler yönetilir.")
        self.set_user_team(team)
        xi, _bench, _out = self.lineup_of(team)
        if not xi or not self.lineup_check(team).ok:
            self.auto_lineup(team)
            return ["Asistan ilk 11'i ve kulübeyi kurdu; 📋 Kadro sayfasında değiştirebilirsin."]
        return []

    def set_user_team(self, team: Team) -> None:
        """
        Birincil koltugun (eski tek menajer) kulubu. Faz 12: baska bir koltugun kulubu secilemez (SeatError);
        birincil olmayan koltuk kulubunu dunya panelinden alir (worlds.WorldPermissionError).
        """
        if not self._is_primary_actor():
            from worlds import WorldPermissionError

            raise WorldPermissionError("Paylaşılan dünyada kulübünü dünya panelinden seçersin.")
        if team.id != self.state.user_team_id and team.id in self.seats.human_team_ids():
            raise SeatError(f"{team.name} başka bir menajerin kulübü.")
        self.state.user_team_id = team.id
        self.state.user_team = team
        self.db.flush()
        if self._human_ids is not None:
            self._human_ids = self.seats.human_team_ids()

    def find_team(self, name: str) -> Team | None:
        """
        Takimi adiyla bulur: once birebir, sonra harf buyuklugu ve aksandan bagimsiz,
        en son rehber uzerinden ("Galatasaray SK" -> maskeli dunyada "Istanbul Lions",
        maskeleme kapali dunyada gercek ad "Galatasaray").
        (Postgres lower() ile Python lower() "İ" harfinde ayrisir; karsilastirma Python'da yapilir.)
        """
        name = (name or "").strip()
        exact = self.db.scalar(select(Team).where(Team.name == name))
        if exact is not None:
            return exact
        teams = list(self.db.scalars(select(Team)))
        key = plain_key(name)
        found = next((t for t in teams if plain_key(t.name) == key), None)
        if found is not None:
            return found
        for candidate in (resolve_masked_club(name), resolve_masked_club(name, MASK_OFF)):
            if candidate is None:
                continue
            candidate_key = plain_key(candidate)
            found = next((t for t in teams if plain_key(t.name) == candidate_key), None)
            if found is not None:
                return found
        return None

    # ------------------------------------------------------------------ sorgular

    def leagues(self) -> list[League]:
        return list(self.db.scalars(select(League).order_by(League.id)))

    def teams(self) -> list[Team]:
        return list(self.db.scalars(select(Team).order_by(Team.league_id, Team.name)))

    def _load_teams(self, *relations, team_ids: Iterable[int] | None = None) -> list[Team]:
        """
        Faz 14D (toplu okuma): takimlar (sira teams() ile ayni: lig, ad) ve istenen koleksiyonlari, takim basina tembel
        SELECT yerine iliski basina TEK IN sorgusuyla. Yalnizca henuz yuklenmemis koleksiyonlar okunur; yuklu olanlar
        EZILMEZ (populate_existing yok): bellekteki kadro sirasi ve icerigi tembel yuklemeyle ayni kalir.
        """
        stmt = select(Team).order_by(Team.league_id, Team.name)
        if team_ids is not None:
            ids = sorted({int(i) for i in team_ids})
            if not ids:
                return []
            stmt = stmt.where(Team.id.in_(ids))
        teams = list(self.db.scalars(stmt))
        for rel in relations:
            missing = sorted({t.id for t in teams if rel.key in sa_inspect(t).unloaded})
            if missing:
                self.db.scalars(select(Team).where(Team.id.in_(missing)).options(selectinload(rel))).all()
        return teams

    def _players_in_order(self, id_query) -> list[Player]:
        """
        Faz 14D (toplu okuma): id sorgusunun sirasiyla oyuncu nesneleri; ayni kosullu select(Player) sorgusunun
        dondurecegi nesnelerin aynisi. Kimlik haritasinda tum sutunlari yuklu olanlarin satiri yeniden okunup cozulmez
        (varlik sorgusu da onlari yenilemez: populate_existing yok); haritada olmayan, suresi dolmus (expired) ya da
        eksik sutunlu olanlar tek IN sorgusuyla yuklenir (varlik sorgusunun yapacagi gibi eksik alanlari doldurulur).
        """
        ids = list(self.db.scalars(id_query))
        identity_map = self.db.identity_map
        mapper = sa_inspect(Player)
        column_keys = {attr.key for attr in mapper.column_attrs}
        found: dict[int, Player] = {}
        missing: list[int] = []
        for pid in ids:
            obj = identity_map.get(mapper.identity_key_from_primary_key((pid,)))
            state = instance_state(obj) if obj is not None else None
            if state is None or state.expired or state.expired_attributes or not column_keys <= state.dict.keys():
                missing.append(pid)
            else:
                found[pid] = obj
        if missing:
            for obj in self.db.scalars(select(Player).where(Player.id.in_(missing))):
                found[obj.id] = obj
        return [found[pid] for pid in ids if pid in found]

    def fixtures_for_week(self, week: int | None = None, league_id: int | None = None) -> list[Fixture]:
        week = self.current_week if week is None else week
        stmt = (
            select(Fixture)
            .where(Fixture.season == self.season, Fixture.week == week,
                   Fixture.competition == Competition.LEAGUE)
            .order_by(Fixture.league_id, Fixture.id)
        )
        if league_id is not None:
            stmt = stmt.where(Fixture.league_id == league_id)
        return list(self.db.scalars(stmt))

    def league_weeks(self) -> int:
        """Bu sezonun lig fiksturundeki son hafta."""
        return self.db.scalar(
            select(func.max(Fixture.week)).where(
                Fixture.season == self.season, Fixture.competition == Competition.LEAGUE
            )
        ) or 0

    def total_weeks(self) -> int:
        """Sezonun son haftasi: kariyerde lig ve kupanin en gec biteni, turnuva modunda kupa."""
        cup_weeks = self.tournaments.last_week(self.tournaments.current())
        if self.game_mode is GameMode.TOURNAMENT:
            return cup_weeks
        return max(self.league_weeks(), cup_weeks)

    def next_fixture(self, team_id: int) -> Fixture | None:
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.UNPLAYED,
                Fixture.competition == Competition.LEAGUE,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(Fixture.week)
            .limit(1)
        )
        return self.db.scalar(stmt)

    def next_cup_fixture(self, team_id: int) -> Fixture | None:
        """Takimin bu sezon oynanmamis ilk kupa maci (kura bitmediyse yok)."""
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.UNPLAYED,
                Fixture.competition == Competition.CUP,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(Fixture.week, Fixture.id)
            .limit(1)
        )
        return self.db.scalar(stmt)

    def standings(self, league_id: int) -> list[Team]:
        """Bellekteki (henuz flush edilmemis olabilecek) degerlerle siralar."""
        teams = list(self.db.scalars(select(Team).where(Team.league_id == league_id)))
        return sorted(teams, key=standings_key)

    def position_of(self, team: Team) -> int:
        table = self.standings(team.league_id)
        return next(i for i, t in enumerate(table, start=1) if t.id == team.id)

    def last_played_week(self) -> int | None:
        return self.db.scalar(
            select(func.max(Fixture.week)).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.PLAYED,
                Fixture.competition == Competition.LEAGUE,
            )
        )

    def results_for_week(self, week: int) -> list[Fixture]:
        return [f for f in self.fixtures_for_week(week) if f.is_played]

    def team_form(self, team_id: int, n: int = 5) -> str:
        """Son n macin sonucu, kronolojik: 'G' galibiyet, 'B' beraberlik, 'M' maglubiyet."""
        stmt = (
            select(Fixture)
            .where(
                Fixture.season == self.season,
                Fixture.status == FixtureStatus.PLAYED,
                Fixture.competition == Competition.LEAGUE,
                (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
            )
            .order_by(desc(Fixture.week))
            .limit(n)
        )
        letters = []
        for fx in self.db.scalars(stmt):
            gf, ga = (fx.home_score, fx.away_score) if fx.home_team_id == team_id else (fx.away_score, fx.home_score)
            letters.append({"W": "G", "D": "B", "L": "M"}[outcome_for(gf, ga)])
        return "".join(reversed(letters))

    def top_scorers(self, league_id: int | None = None, limit: int = 10) -> list[ScorerRow]:
        self.db.flush()
        goals = func.sum(PlayerMatchStat.goals)
        assists = func.sum(PlayerMatchStat.assists)
        stmt = (
            select(Player, Team, goals, assists, func.count(PlayerMatchStat.id), avg_match_rating())
            .join(PlayerMatchStat, PlayerMatchStat.player_id == Player.id)
            .join(Team, Team.id == PlayerMatchStat.team_id)
            .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
            .where(Fixture.season == self.season, Fixture.competition == Competition.LEAGUE)
            .group_by(Player.id, Team.id)
            .having(goals > 0)
            .order_by(desc(goals), desc(assists), Player.name, Player.id)
            .limit(limit)
        )
        if league_id is not None:
            stmt = stmt.where(Team.league_id == league_id)
        return [
            ScorerRow(player=p, team=t, goals=int(g), assists=int(a), appearances=int(n), avg_rating=round(float(r), 2))
            for p, t, g, a, n, r in self.db.execute(stmt)
        ]

    @property
    def cup_finished(self) -> bool:
        t = self.tournaments.current()
        return t is None or t.status is TournamentStatus.FINISHED

    @property
    def league_finished(self) -> bool:
        remaining = self.db.scalar(
            select(func.count()).select_from(Fixture).where(
                Fixture.season == self.season, Fixture.status == FixtureStatus.UNPLAYED,
                Fixture.competition == Competition.LEAGUE,
            )
        )
        return remaining == 0

    @property
    def season_finished(self) -> bool:
        """Kariyer: lig ve kupa bitti. Turnuva modu: kupa bitti."""
        if self.game_mode is GameMode.TOURNAMENT:
            return self.cup_finished
        return self.league_finished and self.cup_finished

    def champion(self, league_id: int) -> Team | None:
        if not self.season_finished:
            return None
        table = self.standings(league_id)
        return table[0] if table else None

    # ------------------------------------------------------------------ ana dongu

    def match_seed(self, fx: Fixture) -> int | None:
        """
        Fiksturun mac tohumu (lig ve kupa, otomatik ve canli ayni): tekrar uretilebilirlik.
        Tohumsuz kariyerde diger maclar rastgeledir, ama KULLANICININ maci fikstur ve sezona bagli
        sabit bir tohum alir: canli maci sayfayi yenileyip (ya da otomatik oynatip) bastan zar
        atarak tekrarlamak ayni kadro ve talimatlarla ayni maci verir.
        Faz 12: insan kulubu iceren her mac; iki insan kulubu karsilasirsa anahtar ev sahibidir.
        """
        if self.seed is not None:
            return self.seed * 10_000 + fx.id
        humans = self.human_team_ids()
        if fx.home_team_id in humans:
            key = fx.home_team_id
        elif fx.away_team_id in humans:
            key = fx.away_team_id
        else:
            return None
        return zlib.crc32(f"{self.season}|{fx.id}|{key}".encode())

    def _pending_league_fixtures(self, week: int) -> list[Fixture]:
        """Bu haftanin oynanmamis lig maclari (turnuva modunda lig yok)."""
        if self.game_mode is GameMode.TOURNAMENT:
            return []
        return [f for f in self.fixtures_for_week(week) if not f.is_played]

    @staticmethod
    def _team_ids(fixtures: Iterable[Fixture]) -> set[int]:
        return {team_id for f in fixtures for team_id in (f.home_team_id, f.away_team_id)}

    def play_week(self, live_results: Mapping[int, MatchResult] | None = None) -> WeekReport:
        """
        Mevcut haftayi oynatir ve ilerletir.
            1) Devler Arenasi maci varsa (hafta ici) -- kura bitmemisse otomatik cekilir;
               play_midweek ile zaten oynandiysa bu adim bos gecer
            2) Tum liglerin maclari (hafta sonu) -- turnuva modunda yok
            3) Maaslar ve AI transfer penceresi -- turnuva modunda yok
            4) Faz 12: eklentilerin on_week kancasi (hafta sayaci artmadan once)
        live_results: fikstur id -> canli oynanmis BITMIS mac sonucu (9. Asama). Bu fiksturler simule
        edilmez; kalicilik otomatik yolla aynidir. Gecersiz girdi -> LiveMatchError, hicbir sey yazilmaz.
        Faz 12: her insan kulubunun sonucu / asistan notlari o kulubun rapor alanlarina (WeekReport.view_for).
        """
        with self._seat_snapshot():
            report = self._play_week(live_results)
            self._record_inbox_week(report)                # 15D: kalici gelen kutusu (bayrak kapaliyken hic)
            return report

    def _play_week(self, live_results: Mapping[int, MatchResult] | None) -> WeekReport:
        live = self._checked_live_results(live_results, allow_league=True)
        week = self.current_week
        report = WeekReport(season=self.season, week=week, focus_team_id=self._acting_team_id())
        tournament_mode = self.game_mode is GameMode.TOURNAMENT
        cup = self.tournaments
        t = cup.ensure()

        fixtures = self._pending_league_fixtures(week)
        cup_due = cup.matchday_due(t, week)
        if not fixtures and not cup_due:
            report.season_finished = self.season_finished
            return report

        # Faz 14D: bu haftanin lig takimlari A takimlari ve teknik heyetleriyle tek seferde (mac, gelisim, maas).
        # Hafta boyunca tutulur; HEAD'de de fikstur nesneleri bu takimlari ilk macta yukleyip hafta sonuna kadar
        # tutar (kadro koleksiyonu aradaki kupa gununde degismez: sira ve icerik ayni).
        self._week_teams = self._load_teams(Team.players, Team.staff, team_ids=self._team_ids(fixtures))
        humans = self.human_team_ids()
        if cup_due:
            cup.play_matchday(week, report, self._team_ids(fixtures), live)

        # Bu hafta cezali olarak oturanlar: mac sonrasi sayaclari 1 azalacak (akademidekiler cezasini A takimda ceker)
        suspended_before = set(
            self.db.scalars(select(Player.id).where(Player.suspended_matches > 0,
                                                    Player.in_academy.is_(False)))
        ) if fixtures else set()

        for fx in fixtures:
            result = live.pop(fx.id, None)
            if result is None:
                result = self._prepare_career_fixture(fx, week).simulate()
            apply_result(fx, result, update_table=True)         # canli mac: ayni kalicilik
            self._post_match(fx, result, week, report)
            for side in (result.home, result.away):
                if side.id in humans:
                    sink = self._sink(report, side.id)
                    sink.user_result = result
                    sink.lineup_notes = list(side.lineup_notes)
            report.results.append((fx, result))

        self._require_consumed(live)
        self._decrement_suspensions(suspended_before)
        if not tournament_mode:
            # 12. Asama: bu hafta biten lig(ler) arsivlenir ve odulu odenir (tek sefer; finans notuna yansir)
            self._archive_finished_leagues(report)
            # Gelisim/yaslanma ve genc girisi: turnuva modunda yok. Kendi RNG'leri var (cm.rng'ye dokunmaz).
            self._weekly_development(week, report)
            self._youth_intake(week, report)
            self._weekly_concerns(week, report)          # 12. Asama: oynama suresi kaygilari ve maas talepleri
            self._pay_weekly_wages(report, week)
            report.transfers = self.run_ai_transfer_window()
            self._run_transfer_desk(week, report)        # 13H: taksit, ek odeme, kulup yanitlari, AI teklifleri ...
            if self._contract_cycle_on():
                self._run_contract_week(week, report)    # 15A: yenileme, on sozlesme, serbest oyuncu (bayrak)
            if self._board_on():
                self._run_board_week(week, report)       # 15C: yonetim guveni, uyari, kovulma (bayrak)
        self.run_extensions("on_week", week, report)     # Faz 12: insan pazari, milli takimlar (eski kariyer: yok)
        self.state.current_week = week + 1
        self.db.flush()
        report.season_finished = self.season_finished
        self._update_manager_reputation(report)
        return report

    def play_midweek(self, live_results: Mapping[int, MatchResult] | None = None) -> WeekReport:
        """
        Yalnizca bu haftanin Devler Arenasi mac gununu oynatir (hafta ici; kura bekliyorsa cekilir).
        Lig maci, maas, transfer ve hafta ilerletme YOK: hafta play_week ile tamamlanir, onun kupa
        adimi o zaman bos gecer. Ayni hafta lig maci olan takimlar play_week'teki gibi hesaplanir
        (hafta ici yarim toparlanma). live_results yalnizca bu haftanin kupa fiksturleri olabilir.
        """
        with self._seat_snapshot():
            live = self._checked_live_results(live_results, allow_league=False)
            week = self.current_week
            report = WeekReport(season=self.season, week=week, midweek_only=True,
                                focus_team_id=self._acting_team_id())
            cup = self.tournaments
            t = cup.ensure()
            if cup.matchday_due(t, week):
                league_team_ids = self._team_ids(self._pending_league_fixtures(week))
                cup.play_matchday(week, report, league_team_ids, live)
            self._require_consumed(live)
            report.season_finished = self.season_finished
            self._record_inbox_week(report)                # 15D: hafta ici kupa gunu de gelen kutusuna girer
            return report

    # ------------------------------------------------------------------ 15D: kalici gelen kutusu ve takvim

    def _inbox_on(self) -> bool:
        """Kural bayragi (inbox.INBOX ya da self.inbox). Kapaliyken gelen kutusuna tek satir bile yazilmaz."""
        return inbox.INBOX if self.inbox is None else bool(self.inbox)

    def _record_inbox_week(self, report: WeekReport | None) -> None:
        """
        15D: haftanin mesajlari (mac sonucu, sakatlik / ceza, transfer masasi, hafta raporu) her insan kulubunun
        gelen kutusuna yazilir. RNG kullanmaz; hata kendi savepoint'inde kalir (hafta bozulmaz).
        """
        if report is not None and self._inbox_on():
            inbox.InboxWriter(self).record_week(report)

    def _record_inbox_season(self, new_season: int) -> None:
        """15D: sezon devri duyurusu ve kulup basina devir notlari."""
        if self._inbox_on():
            inbox.InboxWriter(self).record_season(new_season, self.new_season_notes_by_team)

    def date_bar(self, *, midweek: bool | None = None) -> inbox.DateBar:
        """Arayuzun tarih cubugu: oynanacak haftanin gercek tarihi (lig cumartesi, hafta ici kupa carsamba)."""
        return inbox.date_bar(self, midweek=midweek)

    def game_date(self, week: int | None = None, *, midweek: bool = False):
        """Verilen haftanin (varsayilan: oynanacak hafta) gercek takvim tarihi."""
        return inbox.match_date(self.season, self.current_week if week is None else int(week),
                                midweek=midweek, start=self.state.season_start_date)

    def inbox_for_manager(self) -> inbox.Inbox:
        """Oynatan menajerin gelen kutusu (okuma / okundu / arsiv)."""
        return inbox.Inbox.for_manager(self)

    def continue_until(self, target: str = inbox.TARGET_NEXT_MATCH, **options) -> inbox.ContinueResult:
        """
        "Suna kadar devam": sonraki maca / transfer donemi acilisina / sezon sonuna kadar haftalari isler,
        onemli gelismede durur (inbox.continue_until).
        """
        return inbox.continue_until(self, target, **options)

    # ------------------------------------------------------------------ 15C: yonetim kurulu

    def _board_on(self) -> bool:
        """
        Yonetim kurulu kurali acik mi? Kisisel kariyerde modul bayragi (board.BOARD ya da self.board) yeter;
        PAYLASILAN dunyada ayrica dunya kurali gerekir (sahip karari K-S4: varsayilan kapali, sahibi acar).
        Kapaliyken 15C hic calismaz ve oyun 15C oncesiyle birebir aynidir.
        """
        import board

        if not (board.BOARD if self.board is None else bool(self.board)):
            return False
        rules = self.rules
        return bool(rules.board_confidence) if rules.shared else True

    def board_since(self) -> int:
        """
        Kuralin bu kayitta ilk calistigi mutlak kariyer haftasi (eski kayit gecisi: uyari almamis menajer ilk
        board.GRACE_WEEKS hafta kovulmaz). Ilk cagrida yazilir.
        """
        st = self.state
        if st.board_since_cw is None:
            st.board_since_cw = self.career_week
            self.db.flush()
        return int(st.board_since_cw)

    def _run_board_week(self, week: int, report: WeekReport) -> None:
        """15C: haftalik yonetim guveni, uyarilar, sezon ici kovulma ve is piyasasi (bayrak kapaliyken hic)."""
        import board

        board.BoardRoom(self).run_week(week, report)

    def _run_board_season(self, new_season: int) -> None:
        """15C: sezon kapanisi -- hedef karnesi, kovulma, AI kuluplerinin menajer degisimi, yeni sezon hedefi."""
        import board

        board.BoardRoom(self).season_review(new_season)

    def board_desk(self):
        """Menajerin yonetim kurulu masasi (arayuz API'si; board.BoardDesk)."""
        import board

        return board.BoardDesk(self)

    def board_vacate(self, team: Team, manager_id: int | None, *, status: str,
                     reputation_delta: float = 0.0) -> None:
        """
        15C: menajer kulubunden ayrilir (kovulma / istifa / baska kulube gecis). Kulup IS ILANI acar, menajer
        kulupsuz kalir. Yalnizca board.BoardRoom / BoardDesk cagirir (kural bayragi acikken).
        """
        if reputation_delta:
            try:
                self.seats.apply_reputation(team.id, float(reputation_delta))
            except (SeatError, ValueError):                       # pragma: no cover - koltugu olmayan kayit
                pass                                              # taninirlik guncellenemedi: ayrilma yine de olur
        st = self.state
        if manager_id is None:
            if st.user_team_id == team.id:
                st.user_team_id = None
                st.user_team = None
            st.board_unemployed_since = self.career_week
        else:
            seat = self.seats.by_id(int(manager_id))
            if seat is not None:
                self.seats.assign_team(seat, None)
        team.board_vacant_since = self.career_week
        self.db.flush()
        self.refresh_seats()

    def board_take_club(self, team: Team) -> list[str]:
        """15C: menajer yeni kulubun basina gecer (is teklifi kabul edildi). Donus: kullanici notlari."""
        st = self.state
        st.user_team_id = team.id
        st.user_team = team
        st.board_unemployed_since = None
        team.board_vacant_since = None
        self.db.flush()
        self.refresh_seats()
        xi, _bench, _out = self.lineup_of(team)
        if not xi or not self.lineup_check(team).ok:
            self.auto_lineup(team)
            return ["Asistan ilk 11'i ve kulübeyi kurdu; 📋 Kadro sayfasında değiştirebilirsin."]
        return []

    def board_unemployed(self) -> bool:
        """15C: menajer kovuldu / istifa etti ve henuz yeni kulup bulmadi mi?"""
        return (self._board_on() and self._acting_team_id() is None
                and self.state.board_unemployed_since is not None)

    # ------------------------------------------------------------------ canli mac (9. Asama)

    def live_fixture(self) -> tuple[Fixture, Competition] | None:
        """
        Kullanicinin bu haftaki siradaki oynanmamis maci: once hafta ici kupa, sonra lig
        (turnuva modunda yalnizca kupa). Takim yoksa ya da bu hafta maci kalmadiysa None.
        Salt sorgu: kura henuz cekilmediyse kupa fiksturu yoktur (prepare_live_match kurayi
        otomatik haftadaki gibi tamamlar ve kupa macini sunar). Faz 12: oynatan koltugun kulubu.
        """
        user_id = self._acting_team_id()
        if user_id is None:
            return None
        week = self.current_week
        cup = self.tournaments
        t = cup.current()
        if cup.matchday_due(t, week):
            fx = next((f for f in cup.fixtures(t, week=week)
                       if f.involves(user_id) and not f.is_played), None)
            if fx is not None:
                return fx, Competition.CUP
        if self.game_mode is GameMode.CAREER:
            fx = next((f for f in self.fixtures_for_week(week)
                       if f.involves(user_id) and not f.is_played), None)
            if fx is not None:
                return fx, Competition.LEAGUE
        return None

    def live_cup_draw_pending(self) -> bool:
        """
        Kullanicinin bu hafta kupa maci var ama kura henuz cekilmedi mi? (live_fixture bu durumda
        kupa fiksturunu goremez; prepare_live_match kurayi tamamlayip kupa macini sunar.)
        """
        user_id = self._acting_team_id()
        if user_id is None:
            return False
        cup = self.tournaments
        t = cup.current()
        return (cup.matchday_due(t, self.current_week) and t.status is TournamentStatus.DRAW
                and cup.is_participant(t, user_id))

    def prepare_live_match(self, config: EngineConfig | None = None) -> LivePreparation:
        """
        Kullanicinin bu haftaki siradaki macini canli oynatmak icin motoru kurar (OYNATMAZ).
        Parametreler otomatik yolla aynidir: tohum match_seed, ayar (config yoksa kariyer ayari),
        mevcut hafta; kupada eleme kurali, tarafsiz saha ve kupa cezalari (prepare_cup_engine).
        Lig maci icin bu haftanin kupa maclari hala bekliyorsa ONCE play_midweek oynatilir
        (raporu midweek_report). Bunun disinda yazilan tek sey, otomatik haftanin da ilk isi olan
        turnuva kurulumu (ensure) ve kullanicinin kupa maci kuraya bagliysa kuranin tamamlanmasidir
        (kura tohumludur: otomatik haftayla ayni eslesmeler).
        13. Asama: motor kullanicinin kayitli talimati, rolleri ve oyun planiyla (manager_controlled) ve
        rakibin AI talimat/rolleriyle kurulu gelir; LiveMatch.create(instructions=None, roles=None,
        plan=None) bunlari korur.
        Faz 12: canli resmi mac yalnizca live_allowed() iken (paylasilan dunyada maclar hafta ilerlerken oynanir).
        """
        if not self.live_allowed():
            raise LiveMatchError(LIVE_SHARED_REFUSAL)
        user_id = self._acting_team_id()
        if user_id is None:
            raise LiveMatchError("Canlı maç için önce yöneteceğin takımı seç.")
        if self.season_finished:
            raise LiveMatchError("Sezon bitti; canlı oynanacak maç yok. Yeni sezonu başlat.")

        week = self.current_week
        cup = self.tournaments
        t = cup.ensure()
        if (cup.matchday_due(t, week) and t.status is TournamentStatus.DRAW
                and cup.is_participant(t, user_id)):
            cup.draw_all()

        pending = self.live_fixture()
        if pending is None:
            raise LiveMatchError(
                "Bu hafta oynayacağın maç kalmadı; haftayı tamamlamak için sonraki haftayı oyna."
            )
        fx, competition = pending
        home, away = fx.home_team.name, fx.away_team.name

        if competition is Competition.CUP:
            engine = cup.prepare_cup_engine(fx, week, config)
            title = f"{CUP_SHORT_NAME} · {matchday_label(cup.matchday_for_week(t, week))} · {home} - {away}"
            midweek_report = None
        else:
            # Hafta ici kupa maclari lig macindan once: toparlanma/sakatlik/ceza kadroya yansisin
            midweek_report = self.play_midweek() if cup.matchday_pending(t, week) else None
            engine = self._prepare_career_fixture(fx, week, config)
            title = f"Lig · {week}. hafta · {home} - {away}"

        return LivePreparation(
            fixture_id=fx.id, competition=competition, engine=engine, season=self.season,
            week=week, title=title, managed_team_id=user_id, midweek_report=midweek_report,
        )

    def save_live_result(self, fixture_id: int, result: MatchResult) -> WeekReport:
        """
        Arayuz kisayolu: canli oynanan maci kaydeder. Kariyer modunda kupa maci icin bu hafta
        oynanmamis lig maci varsa yalnizca hafta ici oynatilir (play_midweek; sonra lig maci
        canli oynanabilir), aksi halde hafta tamamlanir (play_week). Gecersizse LiveMatchError.
        """
        fx = self.db.get(Fixture, fixture_id)
        live = {fixture_id: result}
        if (fx is not None and fx.competition is Competition.CUP
                and self.game_mode is GameMode.CAREER
                and self._pending_league_fixtures(self.current_week)):
            return self.play_midweek(live)
        return self.play_week(live)

    def _checked_live_results(
        self, live_results: Mapping[int, MatchResult] | None, allow_league: bool
    ) -> dict[int, MatchResult]:
        """
        Canli sonuclari HICBIR SEY yazilmadan dogrular (yarim islenmis hafta olmasin); kopyasini
        dondurur, play_* kullandikca cikarir. Kurallar: fikstur var, oynanmamis, bu sezonun bu
        haftasi; sonuc ayni ev/deplasman takimlarina ait ve bitmis (son olay FULL_TIME); kupada
        mac gunu bu hafta, eleme kurali ve tarafsiz saha fiksturle ayni; lig maci icin mod kariyer,
        eleme kurali yok ve bu haftanin kupa maclari oynanmis (lig motoru hafta ici sonrasi
        kurulmus olmali). Her girdi mutlaka islenecek bir fiksture karsilik gelir.
        Faz 12: canli sonuc yalnizca live_allowed() iken kabul edilir.
        """
        if not live_results:
            return {}
        if not self.live_allowed():
            raise LiveMatchError(LIVE_SHARED_REFUSAL)
        season, week = self.season, self.current_week
        cup = self.tournaments
        t = cup.current()
        checked: dict[int, MatchResult] = {}
        for fixture_id, result in live_results.items():
            fx = self.db.get(Fixture, fixture_id)
            if fx is None:
                raise LiveMatchError(f"Canlı maç kaydedilemedi: fikstür bulunamadı (#{fixture_id}).")
            label = f"{fx.home_team.name} - {fx.away_team.name}"
            if fx.is_played:
                raise LiveMatchError(f"{label} maçı zaten oynanmış; canlı sonuç kaydedilemez.")
            if fx.season != season or fx.week != week:
                raise LiveMatchError(
                    f"{label} bu haftanın maçı değil ({fx.season}. sezon {fx.week}. hafta; "
                    f"şu an {season}. sezon {week}. hafta). Canlı maçı yeniden hazırla."
                )
            if not isinstance(result, MatchResult):
                raise LiveMatchError(f"{label} için geçerli bir maç sonucu verilmedi.")
            if (result.home.id, result.away.id) != (fx.home_team_id, fx.away_team_id):
                raise LiveMatchError(
                    f"Canlı maç sonucu ({result.home.name} - {result.away.name}) {label} fikstürüne ait değil."
                )
            if not result.events or result.events[-1].type != EventType.FULL_TIME:
                raise LiveMatchError(f"{label} maçı henüz bitmedi; önce maçı sonuna kadar oynat.")

            if fx.competition is Competition.CUP:
                if not cup.matchday_due(t, week) or fx.tournament_id != t.id:
                    raise LiveMatchError(f"{label} kupa maçı bu hafta oynanmıyor.")
                if (result.knockout != cup.knockout_rule(fx)
                        or bool(result.neutral_venue) != bool(fx.neutral_venue)):
                    raise LiveMatchError(
                        f"{label}: canlı maç kupa kurallarıyla (toplam skor, uzatma/penaltı, "
                        f"tarafsız saha) kurulmamış; maçı yeniden hazırla."
                    )
            else:
                if not allow_league:
                    raise LiveMatchError(
                        f"{label} bir lig maçı; hafta içinde yalnızca Devler Arenası maçları oynanır."
                    )
                if self.game_mode is GameMode.TOURNAMENT:
                    raise LiveMatchError(f"Turnuva modunda lig maçı oynanmaz ({label}).")
                if result.knockout is not None or result.neutral_venue:
                    raise LiveMatchError(f"{label} bir lig maçı; eleme kuralıyla oynanan sonuç kaydedilemez.")
                if self._midweek_blocks_league(t, week):
                    raise LiveMatchError(
                        f"{label} kaydedilemez: bu haftanın Devler Arenası maçları henüz oynanmadı. "
                        f"Lig maçını hafta içi maçlarından sonra yeniden hazırla."
                    )
            checked[fixture_id] = result
        return checked

    def _midweek_blocks_league(self, t, week: int) -> bool:
        """
        Lig macinin canli sonucu, bu haftanin kupa maclari oynanmadan kaydedilemez: motoru hafta ici
        toparlanma/sakatliklardan once kurulmus olur. Turnuva henuz hic kurulmadiysa (play_week
        kuracak) ama dunya bir turnuvaya yetiyorsa da beklemede sayilir.
        """
        if t is None:
            return cup_size_for(sum(len(league.teams) for league in self.leagues())) > 0
        return self.tournaments.matchday_pending(t, week)

    @staticmethod
    def _require_consumed(live: Mapping[int, MatchResult]) -> None:
        """Dogrulama her girdinin islenecegini garanti eder; yine de kalan olursa hafta kaydedilmez."""
        if live:
            ids = ", ".join(f"#{fixture_id}" for fixture_id in sorted(live))
            raise LiveMatchError(f"Canlı maç sonucu işlenemedi (fikstür {ids}); hafta kaydedilmedi.")

    def _update_manager_reputation(self, report: WeekReport) -> None:
        """
        Kullanicinin lig maci ve (sezon bittiyse) lig sirasi tanınırlığı degistirir.
        Kupa maci ve tur atlama etkileri kupa oynanirken zaten islenmistir; report.manager_reputation
        haftanin ilk degerinden son degerine tum degisimi gosterir. Faz 12: her insan kulubunun menajeri icin
        (kendi rapor alanlarina).
        """
        humans = self.human_team_ids()
        if not humans:
            return
        for team_id in sorted(humans):
            sink = self._sink(report, team_id)
            if sink.user_result is not None:
                self._apply_match_reputation(sink.user_result, report, team_id)

            if report.season_finished and self.game_mode is not GameMode.TOURNAMENT:
                team = self.db.get(Team, team_id)
                table = self.standings(team.league_id)
                position = next(i for i, t in enumerate(table, start=1) if t.id == team.id)
                delta = reputation.season_delta(position, len(table))
                sink.season_reputation_delta = delta
                self._apply_reputation_delta(delta, report, team_id)
        self.db.flush()

    def _apply_reputation_delta(self, delta: float, report: WeekReport, team_id: int | None = None) -> None:
        """
        Tanınırlığa degisim uygular; raporda (hafta basi, guncel) ciftini tutar. team_id: insan kulubu (None: raporun
        odak kulubu). Birincil koltukta GameState, digerlerinde koltuk satiri (seats.SeatStore.apply_reputation).
        """
        if team_id is None:
            team_id = self._report_focus(report)
        if team_id is None or team_id not in self.human_team_ids():
            return
        sink = self._sink(report, team_id)
        before_value, after = self.seats.apply_reputation(team_id, delta)
        before = sink.manager_reputation[0] if sink.manager_reputation else before_value
        sink.manager_reputation = (before, after)

    def _apply_match_reputation(self, result: MatchResult, report: WeekReport, team_id: int | None = None) -> None:
        """Insan kulubunun oynadigi tek macin (lig ya da kupa) tanınırlık etkisi (team_id None: odak kulup)."""
        if team_id is None:
            team_id = self._report_focus(report)
        if team_id is None or team_id not in (result.home.id, result.away.id) or team_id not in self.human_team_ids():
            return
        mine, theirs = (result.home, result.away) if result.home.id == team_id else (result.away, result.home)
        goal_diff = mine.stats.goals - theirs.stats.goals
        outcome = outcome_for(mine.stats.goals, theirs.stats.goals)
        self._apply_reputation_delta(
            reputation.match_delta(outcome, mine.reputation, theirs.reputation, goal_diff), report, team_id
        )

    def _post_match(
        self,
        fx: Fixture,
        result: MatchResult,
        week: int,
        report: WeekReport,
        competition: Competition = Competition.LEAGUE,
        midweek_team_ids: Iterable[int] = (),
    ) -> None:
        """
        Mac sonrasi kalicilik. competition=CUP ise kartlar kupa cezasina yazilir.
        midweek_team_ids: ayni hafta lig maci da olan takimlar; onlarin kupa macinda oynayanlari
        yarim toparlanir (MIDWEEK_RECOVERY_SHARE) ve oynamayanlara ritim kaybi yazilmaz
        (haftanin tek "oynamadi" kaydi lig macinda dusulur).
        """
        outcomes = {
            result.home.id: outcome_for(result.home_score, result.away_score),
            result.away.id: outcome_for(result.away_score, result.home_score),
        }
        midweek_ids = set(midweek_team_ids)
        cup = competition is Competition.CUP
        # 12. Asama: kariyer modunda oynama suresi penceresi yazilir ve kaygilar eski "uzun sure oynamayan"
        # moral cezasinin yerini alir (turnuva modu eski davranis)
        track_concerns = self._concerns_active()
        self._news_big_result(fx, result, week, cup)
        for team in (result.home, result.away):
            outcome = outcomes[team.id]
            midweek = cup and team.id in midweek_ids
            share = fitness.MIDWEEK_RECOVERY_SHARE if midweek else 1.0
            orm_team = self.db.get(Team, team.id)
            assistant = self._staff_rating(orm_team, StaffRole.ASSISTANT, "man_management")
            physio = self._staff_rating(orm_team, StaffRole.PHYSIO, "physiotherapy")
            # Saglik merkezi saglikcinin toparlanma oranini carpar (kurulmamis kulup: 1.0)
            medical = facilities.medical_recovery_multiplier(
                orm_team.medical_facilities if orm_team is not None else None
            )
            ranks = concerns.position_ranks(
                [(mp.id, mp.position, mp.overall) for mp in team.players]
                + [(mp.id, mp.position, mp.overall) for mp, _reason in team.unavailable]
            ) if track_concerns else {}
            keepers_played = [mp.overall for mp in team.players if mp.played and mp.position is Position.GK]

            for mp in team.players:
                p = self.db.get(Player, mp.id)
                if p is None:
                    continue

                if track_concerns:
                    peer_keeper = (p.position is Position.GK and not mp.played
                                   and concerns.keeper_peer_played(mp.overall, keepers_played))
                    expectation = concerns.expected_share(p.squad_role, p.position, ranks.get(p.id, 99), peer_keeper)
                    p.minutes_window = concerns.push_entry(
                        p.minutes_window, concerns.match_entry(mp.minutes_played, expectation, cup))

                if mp.played:
                    self.db.add(PlayerMatchStat(
                        fixture_id=fx.id, player_id=p.id, team_id=team.id,
                        minutes=mp.minutes_played, goals=mp.goals, assists=mp.assists,
                        shots=mp.shots, shots_on_target=mp.shots_on_target, saves=mp.saves,
                        yellow_cards=mp.yellow_cards, red_card=mp.sent_off, injured=mp.injured,
                        rating=mp.rating,
                    ))
                    history = list(p.match_rating_history or [])
                    p.match_rating_history = (history + [mp.rating])[-RATING_HISTORY_SIZE:]
                    coach = self._staff_rating(
                        orm_team, StaffRole.COACH, staff_rules.coach_attribute_for(p.position)
                    )
                    p.form = clamp(p.form + staff_rules.apply_training(
                        form_delta(mp.rating, outcome), coach))
                    p.morale = clamp(p.morale + staff_rules.apply_training(
                        morale_delta(mp.rating, outcome), assistant))
                    p.weeks_since_match = 0
                    # Mac sonu enerjisi bir sonraki maca kadar saglikcinin ve saglik merkezinin kalitesine gore toparlanir
                    p.condition = fitness.recover_condition(
                        mp.energy, physio, share, age=p.age, medical_multiplier=medical
                    )
                else:
                    if not midweek:
                        p.weeks_since_match += 1
                        p.form = clamp(p.form + bench_form_drift(p.form, p.weeks_since_match))
                        idle = 0 if track_concerns else idle_morale_penalty(p.weeks_since_match)
                        p.morale = clamp(p.morale + morale_delta(None, outcome) + idle)
                    p.condition = fitness.CONDITION_MAX          # oynamadi: tam dinlendi

                if mp.injured:
                    base_weeks = injury_weeks(self.rng)
                    # Saglikcinin tedavi yetenegi sureyi kisaltir (veya uzatir); saglik merkezi ayrica olcekler
                    treated = staff_rules.apply_injury_multiplier(base_weeks, physio)
                    weeks = facilities.medical_injury_weeks(
                        treated, orm_team.medical_facilities if orm_team is not None else None
                    )
                    p.injured_until_week = week + weeks + 1
                    detail = f"{weeks} hafta, {p.injured_until_week}. haftada dönüyor"
                    if physio is not None and treated != base_weeks:
                        detail += f" (sağlıkçı {base_weeks}→{treated} hf)"
                    if weeks != treated:
                        detail += f" (sağlık merkezi {treated}→{weeks} hf)"
                    report.injuries.append(PlayerNote(p.id, p.name, team.name, detail))

                if mp.sent_off:
                    matches = suspension_length(self.rng, mp.second_yellow)
                    reason = "ikinci sarı" if mp.second_yellow else "direkt kırmızı"
                    if cup:
                        p.cup_suspended_matches += matches
                        reason += ", kupa"
                    else:
                        p.suspended_matches += matches
                    report.suspensions.append(PlayerNote(
                        p.id, p.name, team.name, f"{matches} maç ({reason})",
                    ))
                elif mp.yellow_cards and cup:
                    before = p.cup_yellow_cards
                    p.cup_yellow_cards = before + mp.yellow_cards
                    bans = p.cup_yellow_cards // CUP_YELLOW_BAN_EVERY - before // CUP_YELLOW_BAN_EVERY
                    if bans:
                        p.cup_suspended_matches += bans
                        report.suspensions.append(PlayerNote(
                            p.id, p.name, team.name,
                            f"{bans} maç (kupada {p.cup_yellow_cards}. sarı kart)",
                        ))
                elif mp.yellow_cards:
                    before = p.season_yellow_cards
                    p.season_yellow_cards = before + mp.yellow_cards
                    bans = p.season_yellow_cards // YELLOW_BAN_EVERY - before // YELLOW_BAN_EVERY
                    if bans:
                        p.suspended_matches += bans
                        report.suspensions.append(PlayerNote(
                            p.id, p.name, team.name,
                            f"{bans} maç ({p.season_yellow_cards}. sarı kart)",
                        ))

            # Sakat/cezali oyuncular da takimin sonucundan etkilenir (yarim etki)
            for mp, _reason in team.unavailable:
                p = self.db.get(Player, mp.id)
                if p is not None:
                    if not midweek:
                        p.weeks_since_match += 1                   # sakatken de ritim kaybi
                        p.form = clamp(p.form + bench_form_drift(p.form, p.weeks_since_match))
                        p.morale = clamp(p.morale + morale_delta(None, outcome))
                    p.condition = fitness.CONDITION_MAX              # macta yoktu: tam dinlendi

    def _decrement_suspensions(self, player_ids: set[int]) -> None:
        for pid in player_ids:
            p = self.db.get(Player, pid)
            if p is not None and p.suspended_matches > 0:
                p.suspended_matches -= 1

    # ------------------------------------------------------------------ teknik heyet

    def _staff_rating(self, team: Team | None, role: StaffRole, attribute: str) -> int | None:
        """Takimdaki en iyi personelin ilgili ozelligi. Personel yoksa None."""
        if team is None:
            return None
        best = team.best_staff(role, attribute)
        return getattr(best, attribute) if best is not None else None

    def physio_rating(self, team: Team) -> int | None:
        return self._staff_rating(team, StaffRole.PHYSIO, "physiotherapy")

    def scout_rating(self, team: Team) -> int | None:
        return self._staff_rating(team, StaffRole.SCOUT, "judging_ability")

    def scout_margin(self, team: Team) -> int:
        return staff_rules.scout_margin(self.scout_rating(team))

    def free_agent_staff(self, role: StaffRole | None = None) -> list[Staff]:
        stmt = select(Staff).where(Staff.team_id.is_(None))
        if role is not None:
            stmt = stmt.where(Staff.role == role)
        return list(self.db.scalars(stmt.order_by(desc(Staff.reputation))))

    def hire_staff(self, team: Team, member: Staff) -> None:
        """Bostaki personeli ise alir. Maas havuzu yetmezse TransferError."""
        if member.employed:
            raise TransferError(f"{member.name} zaten {member.team.name} kadrosunda.")
        limit = staff_rules.MAX_PER_ROLE[member.role]
        if len(team.staff_by_role(member.role)) >= limit:
            raise TransferError(
                f"{staff_rules.ROLE_LABELS[member.role]} kadrosu dolu (en fazla {limit}). "
                f"Önce birini gönder."
            )
        if member.wage > team.free_wage:
            raise TransferError(
                f"Maaş havuzunda yer yok: {finance.format_money(member.wage)}/hafta gerekli, "
                f"{finance.format_money(team.free_wage)}/hafta boş."
            )
        member.team_id = team.id
        member.team = team
        self.db.flush()

    def release_staff(self, team: Team, member: Staff) -> None:
        """Personeli gonderir; bostaki havuza doner."""
        if member.team_id != team.id:
            raise TransferError(f"{member.name} bu kulübün personeli değil.")
        member.team_id = None
        member.team = None
        self.db.flush()

    # ------------------------------------------------------------------ finans

    def wage_summary(self, team: Team) -> finance.WageSummary:
        return finance.wage_summary(team.player_wage_bill, team.staff_wage_bill, team.wage_budget)

    def shift_budget(self, team: Team, weekly_delta: int) -> tuple[int, int]:
        """
        Butce kaydirma. weekly_delta > 0: maas havuzunu buyut (transferden 52x duser).
        Kural ihlalinde finance.BudgetError firlatir, hicbir sey degismez.
        """
        new_transfer, new_wage = finance.plan_budget_shift(
            team.transfer_budget, team.wage_budget, weekly_delta, team.wage_bill
        )
        team.transfer_budget, team.wage_budget = new_transfer, new_wage
        self.db.flush()
        return new_transfer, new_wage

    def _pay_weekly_wages(self, report: WeekReport, week: int | None = None) -> None:
        """
        Haftalik maaslar havuzdan oder; havuzla gercek yuk arasindaki fark
        transfer kasasina yansir (artan birikir, asim kasadan duser).
        11. Asama: ayni adimda kulup gelirleri de kasaya girer: gecerli sponsor sozlesmesinin haftalik bedeli
        ve bu hafta (hafta ici dahil) oynanmis her IC SAHA lig/kupa maci icin mac gunu geliri. Tarafsiz saha
        (final) gelir getirmez; kapasitesi olmayan (kurulmamis) kulup mac gunu geliri almaz.
        12. Asama: bu hafta lig maci oynanan her ligin kurulmus kuluplerine esit TV payi (finance.tv_money_weekly).
        """
        week = self.current_week if week is None else week
        season = self.season
        humans = self.human_team_ids()
        home_matches = self._home_matches_played(week)
        tv_shares = self._tv_shares(week)
        # Faz 14D: maas yuku icin tum koleksiyonlar tek seferde (ilk tembel erisimle ayni an: icerik ayni)
        for team in self._load_teams(Team.players, Team.academy_players, Team.loaned_out_players, Team.staff):
            summary = self.wage_summary(team)
            sponsor = (team.sponsor_weekly
                       if facilities.sponsor_active(team.sponsor_name, team.sponsor_until_season, season) else 0)
            matches = home_matches.get(team.id, 0)
            gate = matches * facilities.gate_income(team.stadium_capacity, team.reputation) if matches else 0
            tv = tv_shares.get(team.id, 0) if self._economy_ready(team) else 0
            team.transfer_budget = max(0, team.transfer_budget + summary.free + sponsor + gate + tv)
            if team.id in humans:                        # Faz 12: her insan kulubu kendi finans notunu alir
                sink = self._sink(report, team.id)
                sink.sponsor_income, sink.gate_income, sink.tv_income = sponsor, gate, tv
                verb = "kasaya eklendi" if summary.free >= 0 else "kasadan düşüldü"
                parts = [
                    f"Maaşlar ödendi: {finance.format_money(summary.total)}/hafta "
                    f"(havuz {finance.format_money(team.wage_budget)}, %{summary.usage_pct:.0f} dolu)",
                    f"{finance.format_money(abs(summary.free))} {verb}",
                ]
                if sponsor:
                    parts.append(f"sponsor geliri ({team.sponsor_name}): {finance.format_money(sponsor)}")
                elif team.sponsor_until_season is not None or team.sponsor_offers:
                    parts.append("sponsor geliri yok" + (" (bekleyen teklifleri değerlendir)"
                                                         if team.sponsor_offers else ""))
                if gate:
                    parts.append(f"bilet geliri ({matches} iç saha maçı): {finance.format_money(gate)}")
                if tv:
                    parts.append(f"TV geliri: {finance.format_money(tv)}")
                if sink.prize_income:
                    parts.append(f"ödül parası: {finance.format_money(sink.prize_income)}")
                parts.append(f"transfer kasası: {finance.format_money(team.transfer_budget)}")
                sink.finance_note = " · ".join(parts)
        self.db.flush()

    @staticmethod
    def _economy_ready(team: Team | None) -> bool:
        """
        Kulup ekonomisi kurulmus mu? (ensure_club_setup stadyum kapasitesini atamis.) Kurulmamis kulup TV payi,
        lig/kupa odulu ve baskan guvencesi almaz: eski kayit / test dunyasi eski davranisla aynidir.
        """
        return team is not None and team.stadium_capacity is not None

    def _tv_shares(self, week: int) -> dict[int, int]:
        """Bu hafta lig maci oynanmis liglerin kulupleri -> haftalik TV payi (ligde esit; lig itibar ortalamasiyla)."""
        if self.game_mode is GameMode.TOURNAMENT:
            return {}
        self.db.flush()
        league_ids = set(self.db.scalars(
            select(Fixture.league_id).where(
                Fixture.season == self.season, Fixture.week == week, Fixture.competition == Competition.LEAGUE,
                Fixture.status == FixtureStatus.PLAYED,
            ).distinct()
        ))
        shares: dict[int, int] = {}
        for league in self.leagues():
            if league.id not in league_ids or not league.teams:
                continue
            share = finance.tv_money_weekly(mean(t.reputation for t in league.teams), len(league.teams))
            shares.update({t.id: share for t in league.teams})
        return shares

    def _home_matches_played(self, week: int) -> dict[int, int]:
        """Bu sezonun bu haftasinda (lig + kupa, hafta ici dahil) oynanmis ic saha maclari: takim id -> sayi."""
        self.db.flush()
        rows = self.db.execute(
            select(Fixture.home_team_id, func.count())
            .where(Fixture.season == self.season, Fixture.week == week,
                   Fixture.status == FixtureStatus.PLAYED, Fixture.neutral_venue.is_(False))
            .group_by(Fixture.home_team_id)
        )
        return {team_id: int(count) for team_id, count in rows}

    # ------------------------------------------------------------------ transfer pazari

    def transfer_targets(self, buyer: Team, query: str = "", limit: int = 20) -> list[Player]:
        """Baska kuluplerin A takim oyunculari (isim filtresiyle), guce gore sirali. Akademiler satilik degil."""
        stmt = (
            select(Player)
            .where(Player.team_id.isnot(None), Player.team_id != buyer.id, Player.in_academy.is_(False))
            .order_by(desc(Player.overall_rating))
            .limit(limit)
        )
        if query.strip():
            stmt = stmt.where(Player.name.ilike(f"%{query.strip()}%"))
        return list(self.db.scalars(stmt))

    def scouted_report(self, buyer: Team, player: Player) -> dict:
        """
        Oyuncunun gozlemci suzgecinden gecmis profili.
        Kendi oyuncumuzsa kesin, degilse gozlemcinin yanilma payiyla aralik.
        """
        margin = 0 if player.team_id == buyer.id else self.scout_margin(buyer)
        seed = (self.scout_rating(buyer) or 0, player.id)
        attrs = ("overall_rating", "pace", "shooting", "passing",
                 "defending", "dribbling", "goalkeeping")
        report = {
            name: staff_rules.scouted_value(getattr(player, name), margin, (*seed, name))
            for name in attrs
        }
        report["market_value"] = staff_rules.scouted_money(
            player.market_value, margin, (*seed, "value")
        )
        report["margin"] = margin
        return report

    def offer_fee(self, buyer: Team, player: Player, fee: int) -> transfers.FeeDecision:
        """1. Asama: satici kulube bonservis teklifi. Faz 12: menajer kulubundeki oyuncuya bu akisla teklif olmaz."""
        if player.team_id == buyer.id:
            raise TransferError("Bu oyuncu zaten senin takımında.")
        if player.team is None:
            raise TransferError("Oyuncunun kulübü yok.")
        if player.in_academy:
            raise TransferError(f"{player.name} {player.team.name} akademisinde; akademi oyuncuları satılık değil.")
        self._check_human_seller(buyer, player)
        if fee < 0:
            raise TransferError("Teklif negatif olamaz.")
        self._check_buyer_budget(buyer)
        self._check_transfer_ban(player)
        if not finance.can_afford_transfer(buyer.transfer_budget, fee):
            raise TransferError(
                f"Transfer bütçen yetersiz: {finance.format_money(buyer.transfer_budget)} var, "
                f"{finance.format_money(fee)} gerekiyor."
            )
        return transfers.evaluate_fee(self.rng, player, player.team, fee, buyer.reputation)

    def open_negotiation(self, buyer: Team, player: Player, fee: int) -> transfers.ContractNegotiation:
        """
        2. Asama: sozlesme masasini acar (kulup onayindan SONRA cagrilir).
        Kulup + menajer prestiji yetmezse donen pazarlik zaten kapalidir
        (negotiation.open False, negotiation.opening_message sebebi soyler).
        12. Asama: transfer yasagindaki oyuncu ya da eksi kasa -> TransferError.
        Faz 12: insan alicinin menajer kulubundeki oyuncusu -> TransferError (Teklifler paneli).
        """
        self._check_human_seller(buyer, player)
        self._check_buyer_budget(buyer)
        self._check_transfer_ban(player)
        return transfers.ContractNegotiation(
            self.rng, player, buyer, fee, manager_reputation=self.manager_reputation_for(buyer)
        )

    # ---- transfer yasagi (12. Asama)

    def transfer_ban_info(self, player: Player) -> tuple[bool, str]:
        """
        (yasakli mi, Turkce sebep). Kulup degistiren oyuncu TRANSFER_BAN_WEEKS oyun haftasi satilamaz ve teklif
        alamaz; sayac mutlak kariyer haftasidir (sezon devrinde kesintisiz). Yasak yoksa (False, "").
        """
        return self._transfer_ban(player, None)

    def _transfer_ban(self, player: Player, career_week: int | None) -> tuple[bool, str]:
        """transfer_ban_info govdesi; career_week verilirse (AI aday taramasi, Faz 14D) yeniden okunmaz."""
        locked = getattr(player, "transfer_locked_until", None)
        if locked is None:
            return False, ""
        remaining = int(locked) - (self.career_week if career_week is None else career_week)
        if remaining <= 0:
            return False, ""
        return True, f"Yeni transfer: {remaining} hafta daha satılamaz"

    def _protected_team_ids(self) -> frozenset[int]:
        """
        Faz 12: menajeri birakilan ve koruma suresi (teams.ai_protected_until, mutlak kariyer haftasi) dolmamis
        kulupler. Yalnizca id okunur (ORM nesnesi / iliski yuklenmez: eski kariyerde oturum durumu HEAD ile ayni
        kalir). Hafta / sezon donusumu boyunca bir kez okunur.
        """
        if self._protected_ids is not None:
            return self._protected_ids
        return frozenset(self.db.scalars(select(Team.id).where(
            Team.ai_protected_until.isnot(None), Team.ai_protected_until > self.career_week)))

    def transfer_block_reason(self, player: Player) -> str | None:
        """
        Oyuncunun transferini engelleyen neden (Turkce) ya da None. Sira: yeni transfer yasagi, kiralik oyuncu,
        kulubun koruma suresi (teams.ai_protected_until), eklentilerin nedenleri. RNG kullanmaz; eski kariyerde
        oyuncunun kulup iliskisine dokunmaz.
        """
        return self._transfer_block_reason(player, None)

    def _transfer_block_reason(self, player: Player, career_week: int | None) -> str | None:
        """transfer_block_reason govdesi; career_week verilirse (AI aday taramasi, Faz 14D) yeniden okunmaz."""
        banned, reason = self._transfer_ban(player, career_week)
        if banned:
            return reason
        if getattr(player, "loan_from_team_id", None) is not None:
            return "Kiralık oyuncu: kiralık dönemi bitmeden satılamaz"
        holds = self._contract_hold_map()
        if holds and player.id in holds:                    # 15A: on sozlesme imzalamis (bayrak kapaliyken bos)
            return holds[player.id]
        if player.team_id is not None and player.team_id in self._protected_team_ids():
            team = self.db.get(Team, player.team_id)
            weeks = int(team.ai_protected_until) - (self.career_week if career_week is None else career_week)
            return f"{team.name} yönetim koruması altında: {weeks} hafta daha transfer yapılamaz"
        for extension in self._extensions():
            reason = extension.transfer_block_reason(player)
            if reason:
                return reason
        return None

    def _check_transfer_ban(self, player: Player) -> None:
        reason = self.transfer_block_reason(player)
        if reason:
            raise TransferError(f"{player.name} için teklif yapılamaz. {reason}.")

    def _check_human_seller(self, buyer: Team, player: Player) -> None:
        """Faz 12: insan menajerin kulubundeki oyuncuya baska bir insan menajer AI akisiyla teklif yapamaz."""
        seller_id = player.team_id
        if seller_id is None or seller_id == buyer.id:
            return
        humans = self.human_team_ids()
        if buyer.id in humans and seller_id in humans:
            raise TransferError(
                f"{player.team.name} bir menajerin kulübü; {player.name} için teklifini Teklifler panelinden yap."
            )

    @staticmethod
    def _check_buyer_budget(buyer: Team) -> None:
        """Kasa eksideyken (baskan guvencesi / gelirler artiya cevirene kadar) oyuncu alinamaz."""
        if buyer.transfer_budget < 0:
            raise TransferError(
                f"Transfer kasası ekside ({finance.format_money(buyer.transfer_budget)}); kasa artıya dönene "
                f"kadar oyuncu alamazsın."
            )

    def complete_transfer(
        self, buyer: Team, player: Player, fee: int, offer: ContractOffer,
        expected_seller_id: int | None = None, *, human_deal: bool = False,
        log_fee: int | None = None, settle_sell_on: bool = True,
    ) -> TransferNews:
        """
        Anlasma tamam: oyuncu takim degistirir, butceler guncellenir.
        Maas havuzu yetmiyorsa TransferError (cagiran once butce kaydirmali).
        Faz 12: expected_seller_id verilirse oyuncu hala o kulupte olmali (satir kilidi altinda yeniden dogrulama);
        human_deal=True yalnizca menajerler arasi pazar (market_hub) icindir: satici menajerin onayi orada alinmistir,
        aksi halde insan -> insan transferi TransferError.
        13H (transfer masasi): fee bu anda el degistiren paradir (masada pesinat); log_fee verilirse transfer kaydi ve
        haber toplam garantili bedeli yazar. Yeni sozlesme: serbest kalma bedeli ve masa maddeleri sifirlanir.
        settle_sell_on: oyuncu daha once masada "sonraki satistan pay" maddesiyle alindiysa pay burada (bir kez) eski
        kulube odenir (transfer_desk.settle_sell_on); masa kendi taksitli satislarinda False verip payi kendisi oder.
        """
        seller = player.team
        if seller is None:
            raise TransferError("Oyuncunun kulübü yok.")
        if expected_seller_id is not None and seller.id != expected_seller_id:
            raise TransferError(f"{player.name} artık bu kulübün oyuncusu değil; teklif geçersiz.")
        if not human_deal:
            self._check_human_seller(buyer, player)
        self._check_buyer_budget(buyer)
        self._check_transfer_ban(player)
        if fee > buyer.transfer_budget:
            raise TransferError("Transfer bütçesi yetersiz.")

        wage_delta = offer.wage - 0        # gelen oyuncu havuza tamamen yeni yuk ekler
        if wage_delta > buyer.free_wage:
            raise TransferError(
                f"Maaş havuzunda yer yok: {finance.format_money(offer.wage)}/hafta gerekli, "
                f"{finance.format_money(buyer.free_wage)}/hafta boş. Bütçe kaydırman gerekiyor."
            )

        buyer.transfer_budget -= fee
        seller.transfer_budget += fee

        was_academy = player.in_academy
        player.in_academy = False                       # satin alinan oyuncu A takima katilir
        player.team_id = buyer.id
        player.team = buyer
        player.current_wage = offer.wage
        player.contract_years = offer.years
        player.squad_role = offer.role
        player.last_transfer_season = self.season
        player.lineup_status = LineupStatus.BENCH
        player.lineup_role = None
        player.market_value = finance.market_value(
            player.overall_rating, player.age, player.position, player.potential_rating
        )
        if player.release_clause is not None:               # 13H: yeni sozlesme, eski maddeler duser
            player.release_clause = None
        if player.contract_clauses:
            player.contract_clauses = {}
        if player.free_agent_since is not None:              # 15A (kulubu olan oyuncuda hep bos)
            player.free_agent_since = None
        news = TransferNews(player.name, seller.name, buyer.name, fee if log_fee is None else int(log_fee),
                            offer.wage, player_id=player.id, from_team_id=seller.id, to_team_id=buyer.id)
        self._record_player_move(player, seller, buyer, news)
        if settle_sell_on and fee > 0:
            import transfer_desk
            transfer_desk.settle_sell_on(self, player, seller, int(fee))
        self.db.flush()
        if was_academy:
            for team in (seller, buyer):
                self.db.expire(team, ["players", "academy_players"])
        return news

    def _record_player_move(self, player: Player, seller: Team | None, buyer: Team, news: TransferNews, *,
                            season: int | None = None, week: int | None = None) -> None:
        """
        Kulup degistiren oyuncu (transfer ya da kulupsuz imza; 12. Asama): transfer yasagi baslar, kaygi penceresi
        ve maas talebi sifirlanir (yeni kulup, yeni sozlesme), transfer_log'a yazilir. Kullanicinin kulubunu
        ilgilendiren transfer hemen haber olur (AI transferleri haftalik secilir: run_ai_transfer_window);
        kullanicinin aldigi oyuncu izleme listesinden cikar.
        Faz 12: insan kuluplerinin hepsi icin; alan koltugun izleme listesi; eklentilerin on_player_moved kancasi.
        15A: season / week verilirse kayit ve haber o tarihe yazilir (sezon devrindeki katilimlar yeni sezonun 1. haftasi).
        """
        player.transfer_locked_until = self.career_week + TRANSFER_BAN_WEEKS
        player.minutes_window = []
        player.concern_level = int(concerns.ConcernLevel.NONE)
        player.wage_demand = None
        player.contract_overall = player.overall_rating
        if player.asking_price is not None:                  # 13H: eski kulubun istedigi bedel yeni kulupte gecmez
            player.asking_price = None
        seller_id = seller.id if seller is not None else None
        self.db.add(TransferLog(
            season=self.season if season is None else int(season),
            week=self.current_week if week is None else int(week), player_id=player.id, player_name=player.name,
            from_team_id=seller_id, from_team_name=seller.name if seller is not None else None,
            to_team_id=buyer.id, to_team_name=buyer.name, fee=int(news.fee), wage=int(news.wage),
            kind=news.kind,
        ))
        humans = self.human_team_ids()
        if buyer.id in humans or (seller_id is not None and seller_id in humans):
            self._add_news(NewsKind.TRANSFER, self._transfer_news_text(news), team_id=buyer.id,
                           other_team_id=seller_id, week=week, season=season)
        if buyer.id in humans:
            if buyer.id == self.state.user_team_id:
                entry = self.db.get(ShortlistEntry, player.id)
                if entry is not None:
                    self.db.delete(entry)
            else:
                seat = self.seats.by_team(buyer.id)
                entry = self._seat_shortlist_entry(seat.id, player.id) if seat is not None and seat.id else None
                if entry is not None:
                    self.db.delete(entry)
        self.run_extensions("on_player_moved", player, seller_id, buyer.id)

    @staticmethod
    def _transfer_news_text(news: TransferNews) -> str:
        if news.kind == TransferKind.BOSMAN.value:            # 15A
            return (f"Transfer: {news.player_name}, sözleşmesi biten oyuncu olarak {news.from_team} kulübünden "
                    f"{news.to_team} kulübüne bedelsiz katıldı ({finance.format_money(news.wage)}/hafta).")
        if news.kind == TransferKind.FREE_AGENT.value or not news.from_team:
            return (f"Transfer: {news.player_name} serbest oyuncu olarak {news.to_team} ile anlaştı "
                    f"({finance.format_money(news.wage)}/hafta).")
        return (f"Transfer: {news.player_name}, {news.from_team} → {news.to_team} "
                f"({finance.format_money(news.fee)}, {finance.format_money(news.wage)}/hafta).")

    # ------------------------------------------------------------------ sozlesme dongusu (Faz 15A)

    def _contract_cycle_on(self) -> bool:
        """Kural bayragi (contracts.CONTRACT_CYCLE ya da self.contract_cycle) ve kariyer modu. Turnuvada dongu yok."""
        flag = contracts.CONTRACT_CYCLE if self.contract_cycle is None else bool(self.contract_cycle)
        return flag and self.game_mode is not GameMode.TOURNAMENT

    def _contract_hold_map(self) -> dict[int, str]:
        """
        On sozlesme imzalamis oyuncular -> transfer engeli metni. Dongu kapaliyken BOS (sorgu yok: eski davranis).
        Hafta / sezon donusumu boyunca onbellekte; yeni on sozlesmede transfer_desk sifirlar.
        """
        if self._contract_holds is None:
            flag = contracts.CONTRACT_CYCLE if self.contract_cycle is None else bool(self.contract_cycle)
            if not flag:
                return {}
            self.db.flush()
            rows = self.db.execute(
                select(ContractTalk.player_id, Team.name).join(Team, Team.id == ContractTalk.team_id)
                .where(ContractTalk.kind == contracts.KIND_PRE_CONTRACT, ContractTalk.status == contracts.AGREED))
            self._contract_holds = {int(pid): f"Ön sözleşme imzaladı: sezon sonunda {name} kulübüne katılacak"
                                    for pid, name in rows}
        return self._contract_holds

    def _run_contract_week(self, week: int, report: WeekReport) -> None:
        """
        15A haftalik adim (transfer_desk.run_contract_week): AI yenileme kararlari, on sozlesme donemi, serbest oyuncu
        imzalari, insan kuluplerine uyarilar. cm.rng'den CEKMEZ; her adim kendi savepoint'inde.
        """
        import transfer_desk
        report.transfers += transfer_desk.run_contract_week(self, week, report)

    def release_player(self, player: Player, kind: str = TransferKind.RELEASED.value, *, season: int | None = None,
                       week: int | None = None, news: bool | None = None, flush: bool = True) -> TransferLog:
        """
        15A: oyuncu kulupsuz kalir (sozlesmesi bitti / feshedildi). team_id NULL yazilir -- iliski (player.team)
        ATANMAZ: Team.players delete-orphan oldugundan koleksiyondan cikarmak satiri silerdi. Maas, sozlesme,
        listeler ve maddeler sifirlanir, free_agent_since = bu mutlak kariyer haftasi. transfer_log (kind, to_team_id
        NULL, to_team_name RELEASED_TEAM_NAME, wage = son maas), insan kulubunun oyuncusuysa haber (news=True her
        durumda), eklentilerin on_player_moved kancasi (alici None). Donus: transfer_log satiri.
        flush=False (toplu serbest birakma): yazim ve player.team / kadro koleksiyonlarinin expire'i cagirana kalir.
        """
        team = self.db.get(Team, player.team_id) if player.team_id is not None else None
        if team is None:
            raise TransferError(f"{player.name} zaten kulüpsüz.")
        last_wage = int(player.current_wage or 0)
        player.team_id = None
        player.in_academy = False
        player.free_agent_since = self.career_week
        player.contract_years = 0
        player.current_wage = 0
        player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        player.transfer_listed = player.loan_listed = False
        player.asking_price = None
        player.release_clause = None
        player.contract_clauses = {}
        player.wage_demand = None
        player.concern_level = int(concerns.ConcernLevel.NONE)
        player.minutes_window = []
        player.transfer_locked_until = None
        log = TransferLog(
            season=self.season if season is None else int(season),
            week=self.current_week if week is None else int(week), player_id=player.id, player_name=player.name,
            from_team_id=team.id, from_team_name=team.name, to_team_id=None, to_team_name=RELEASED_TEAM_NAME,
            fee=0, wage=last_wage, kind=kind,
        )
        self.db.add(log)
        if flush:
            self.db.flush()
            self.db.expire(player, ["team"])
            self.db.expire(team, ["players", "academy_players"])
        if news if news is not None else team.id in self.human_team_ids():
            verb = "sözleşmesi feshedildi" if kind == TransferKind.TERMINATED.value else "sözleşmesi bitti"
            self._add_news(NewsKind.CONTRACT, f"{player.name} ({team.name}) {verb}; serbest oyuncu.",
                           team_id=team.id, week=week, season=season)
        self.run_extensions("on_player_moved", player, team.id, None)
        return log

    def sign_free_agent(self, buyer: Team, player: Player, offer: ContractOffer, *, years: int | None = None,
                        from_team: Team | None = None, kind: str = TransferKind.FREE_AGENT.value,
                        season: int | None = None, week: int | None = None, flush: bool = True) -> TransferNews:
        """
        15A: kulupsuz oyuncuyla imza (kind FREE_AGENT) ya da on sozlesmeyle bedelsiz katilim (kind BOSMAN, from_team
        oyuncunun eski kulubu). Bonservis yok; maas / sure / rol teklifinden (years verilirse sozlesme yili odur),
        serbest kalma maddesi tekliften, masa maddeleri sifirlanir. complete_transfer gibi A takima katilir, piyasa
        degeri yenilenir, _record_player_move (yasak, kayit, haber, izleme listesi, eklentiler). Para hareketi YOK:
        imza primi / menajer ucreti cagiranin (transfer_desk) isidir. season / week: kayit tarihi (devirde yeni sezon).
        flush=False: toplu imza (AI hazirlik donemi); yazim cagiranin flush'ina kalir.
        """
        if from_team is None and player.team_id is not None:
            raise TransferError(f"{player.name} kulüpsüz değil.")
        if from_team is not None and player.team_id != from_team.id:
            raise TransferError(f"{player.name} artık {from_team.name} oyuncusu değil.")
        player.in_academy = False
        player.team_id = buyer.id
        player.team = buyer
        player.current_wage = int(offer.wage)
        player.contract_years = int(offer.years if years is None else years)
        player.squad_role = offer.role
        player.last_transfer_season = self.season if season is None else int(season)
        player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        player.market_value = finance.market_value(
            player.overall_rating, player.age, player.position, player.potential_rating
        )
        player.release_clause = offer.release_clause
        player.contract_clauses = {}
        player.free_agent_since = None
        player.transfer_listed = player.loan_listed = False
        news = TransferNews(player.name, from_team.name if from_team is not None else "Serbest", buyer.name, 0,
                            int(offer.wage), player_id=player.id,
                            from_team_id=from_team.id if from_team is not None else None, to_team_id=buyer.id,
                            kind=kind)
        self._record_player_move(player, from_team, buyer, news, season=season, week=week)
        if flush:
            self.db.flush()
        return news

    # ------------------------------------------------------------------ AI transfer pazari

    def _run_transfer_desk(self, week: int, report: WeekReport) -> None:
        """
        13H transfer masasinin haftalik adimlari (transfer_desk.run_week): sirada bekleyen kulup yanitlari, sure dolan
        teklifler, gozlem, taksit / ek odeme / prim odemeleri, soz kontrolu, donem acilinca bekleyen anlasmalarin
        tamamlanmasi, serbest kalma bedelleri ve AI kuluplerinin gelen teklifleri. cm.rng'den CEKMEZ; her adim kendi
        savepoint'inde (hata haftayi bozmaz). Masa hic kullanilmamis eski kariyerde oyun sonucu degismez.
        """
        import transfer_desk
        transfer_desk.run_week(self, week, report)

    def _return_solo_loans(self) -> None:
        """
        15F: sezon devrinde aktif kiraliklarin hepsi biter. PAYLASILAN dunyada bunu market_hub.MarketExtension
        yapar (on_season_end); TEK OYUNCULU dunyada eklenti yuklenmedigi icin burada yapilir. Kiralik yoksa tek
        satir bile yazilmaz (eski kayit davranisi degismez); hata devri bozmaz.
        """
        import transfer_desk

        if self.game_mode is GameMode.TOURNAMENT or transfer_desk._market_extension_active(self):
            return
        if not transfer_desk.loans_on(self):
            return
        import market_hub

        hub = market_hub.MarketHub(self)
        hub._safe("season_end_loans", hub.return_all_loans)

    def _live_market_on(self) -> bool:
        """15F kural bayragi (transfer_rules.LIVE_MARKET). Kapaliyken AI penceresi 15F oncesiyle bit-bit ayni."""
        import transfer_rules

        return bool(transfer_rules.LIVE_MARKET) and self.game_mode is not GameMode.TOURNAMENT

    def run_ai_transfer_window(self) -> list[TransferNews]:
        """
        AI kulupleri kendi butce ve kadro ihtiyaclarina gore teklif yapar.
        Maas alani yetmezse arka planda butce kaydirir.
        12. Asama: haftanin en pahali NEWS_AI_TRANSFERS_PER_WEEK transferi haber akisina yazilir (her transfer
        zaten transfer_log'dadir).
        Faz 12: insan kulupleri ve koruma suresindeki kulupler pencereye ne alici ne satici olarak girer.
        15F (bayrak ACIK): pencere yerine transfer_desk.WorldMarket calisir -- transferler YALNIZ donem icinde,
        LIGLER ARASI ve ihtiyaca gore olur, ayrica AI <-> AI kiraliklar, geri alim maddeleri, soylentiler ve
        "son gun" haberi yazilir. Haber secimi (en pahali uc transfer) iki yolda da aynidir.
        """
        if self._live_market_on():
            import transfer_desk
            deals = transfer_desk.WorldMarket(self).run_week(self.current_week)
        else:
            deals = self._ai_transfer_deals()
        ranked = sorted(deals, key=lambda n: -n.fee)[:NEWS_AI_TRANSFERS_PER_WEEK]
        humans = self.human_team_ids()
        for deal in ranked:
            if deal.to_team_id in humans or deal.from_team_id in humans:
                continue                                   # kullaniciyi ilgilendiren transfer zaten haber oldu
            self._add_news(NewsKind.TRANSFER, self._transfer_news_text(deal), team_id=deal.to_team_id,
                           other_team_id=deal.from_team_id)
        return deals

    def _ai_transfer_deals(self) -> list[TransferNews]:
        """AI transfer penceresi: tamamlanan transferler (haber secimi run_ai_transfer_window'da)."""
        news: list[TransferNews] = []
        humans = self.human_team_ids()
        protected = self._protected_team_ids()
        # Bir transfer penceresinde ayni oyuncu birden fazla kez el degistiremez
        # ve bir kulup hem alip hem satamaz (aksi halde Inter->Milan->Inter gibi
        # atlikarinca olusuyordu).
        moved_players: set[int] = set()
        busy_teams: set[int] = set()

        for league in self.leagues():
            averages = transfers.league_position_average(league.teams)
            # Faz 12: insan kulupleri ve korumadaki kulupler alici olmaz (eski kariyerde yalnizca kullanici disarida;
            # liste ve karistirma sirasi ayni kalir)
            buyers = [t for t in league.teams if t.id not in humans and t.id not in protected]
            self.rng.shuffle(buyers)

            for buyer in buyers:
                if len(news) >= AI_MAX_DEALS_PER_WEEK:
                    return news
                if buyer.id in busy_teams:
                    continue
                if self.rng.random() >= AI_TRANSFER_CHANCE:
                    continue
                deal = self._ai_attempt_transfer(
                    buyer, league, averages, moved_players, busy_teams
                )
                if deal is not None:
                    news.append(deal)
        return news

    def _ai_attempt_transfer(
        self,
        buyer: Team,
        league: League,
        averages,
        moved_players: set[int],
        busy_teams: set[int],
    ) -> TransferNews | None:
        needs = transfers.squad_needs(buyer, averages)
        if not needs:
            return None
        need = needs[0]

        # Insan menajerlerin oyunculari AI tarafindan onaysiz satin alinamaz; korumadaki kulup de satmaz; bu sezon
        # zaten transfer edilmis oyuncu da tekrar el degistirmez.
        humans, protected = self.human_team_ids(), self._protected_team_ids()
        # Faz 14D: aday basina tekrar okunmayan girdiler (tarama boyunca degismez; sira ve suzme ayni)
        season, week, career_week = self.season, self.current_week, self.career_week
        candidates = [
            p for t in league.teams
            if t.id != buyer.id and t.id not in humans and t.id not in busy_teams and t.id not in protected
            for p in t.players
            if p.position is need.position
            and p.id not in moved_players
            and p.last_transfer_season != season
            and p.is_available(week)
            and self._transfer_block_reason(p, career_week) is None   # 12. Asama yasak + Faz 12 kiralik / eklenti
        ]
        scored = [(transfers.target_score(p, buyer, need), p) for p in candidates]
        scored = [(s, p) for s, p in scored if s >= AI_MIN_TARGET_SCORE]
        if not scored:
            return None
        scored.sort(key=lambda sp: -sp[0])
        target = scored[0][1]

        asking = transfers.asking_price(target, target.team, buyer.reputation)
        if asking > buyer.transfer_budget:
            return None
        fee = transfers.ai_opening_offer(self.rng, asking, buyer.transfer_budget)

        decision = transfers.evaluate_fee(self.rng, target, target.team, fee, buyer.reputation)
        if not decision.accepted:
            return None

        negotiation = transfers.ContractNegotiation(
            self.rng, target, buyer, fee, manager_reputation=self.manager_reputation_for(buyer)
        )
        if not negotiation.open:          # oyuncu bu kulube gelmek istemiyor
            return None

        # Once imzalanacak teklifi belirle; imza cikmayacaksa butceyi bosuna kaydirma
        offer = transfers.ai_contract_offer(
            self.rng, negotiation, max(buyer.free_wage, negotiation.demand.wage)
        )
        if negotiation.persuasion(offer) < negotiation.required_persuasion:
            return None

        # Maas alani yetmiyorsa butce kaydir (bonservisi ayirarak)
        need_weekly = offer.wage
        if need_weekly > buyer.free_wage:
            shift = finance.auto_shift_for_wage(
                buyer.transfer_budget, buyer.wage_budget, buyer.free_wage, need_weekly, fee
            )
            if shift <= 0:
                return None
            try:
                self.shift_budget(buyer, shift)
            except finance.BudgetError:
                return None
            if need_weekly > buyer.free_wage:
                return None

        response = negotiation.respond(offer)
        if response.status is not transfers.NegotiationStatus.ACCEPTED:
            return None

        seller_id = target.team_id
        try:
            deal = self.complete_transfer(buyer, target, fee, offer)
        except TransferError:
            return None
        moved_players.add(target.id)
        busy_teams.update({buyer.id, seller_id})
        return deal

    # ------------------------------------------------------------------ altyapi ve gelisim (10. Asama)

    def _youth_rng(self, kind: str, team_id: int) -> random.Random:
        """
        Genc girisi / akademi RNG'si: cm.rng dizisinden BAGIMSIZ (mac ve transfer sonuclari degismez).
        Tohumlu kariyerde tohum + sezon + takimdan turetilir; tohumsuzda rastgele.
        """
        if self.seed is None:
            return random.Random()
        return random.Random(zlib.crc32(f"{kind}|{self.seed}|{self.season}|{team_id}".encode()))

    @staticmethod
    def _default_youth_facilities(team: Team) -> int:
        """Eski kayit: altyapi tesisi itibardan + kulube ozgu sabit tohumla sapma (facility_status da kullanir)."""
        rng = random.Random(zlib.crc32(f"facilities|{team.id}".encode()))
        return youth.default_facilities(team.reputation, rng)

    @staticmethod
    def _backfill_potential(p: Player) -> int:
        """Eski kayit: FM oyuncusunda potential_ability, digerlerinde oyuncuya ozgu sabit tohumla yas egrisi."""
        if p.potential_ability:
            return development.potential_from_fm(p.potential_ability, p.overall_rating)
        rng = random.Random(zlib.crc32(f"potential|{p.id}".encode()))
        return development.initial_potential(rng, p.age, p.overall_rating)

    def _academy_player(self, team: Team, spec: youth.YouthSpec) -> Player:
        """youth.YouthSpec -> akademi oyuncusu (A takim koleksiyonuna eklenmez; team_id ile baglanir)."""
        return Player(
            team_id=team.id, name=spec.name, age=spec.age, position=spec.position,
            overall_rating=spec.overall, **{a: spec.attributes[a] for a in ENGINE_ATTRIBUTES},
            form=spec.form, morale=spec.morale, condition=fitness.CONDITION_MAX,
            contract_years=spec.contract_years,
            market_value=finance.market_value(spec.overall, spec.age, spec.position, spec.potential),
            current_wage=finance.academy_wage(spec.overall, team.reputation),
            squad_role=SquadRole.BACKUP, lineup_status=LineupStatus.OUT, lineup_role=None,
            data_source="academy", potential_rating=spec.potential, in_academy=True,
            development_progress=0.0, match_rating_history=[], fm_attributes={},
        )

    def ensure_youth_setup(self) -> list[str]:
        """
        Eski kayitlar ve yeni dunyalar icin IDEMPOTENT doldurma (kariyer silinmez):
            * potential_rating NULL olan her oyuncu: FM'de potential_ability, aksi halde oyuncuya ozgu
              sabit tohumla (id) yas egrisi
            * youth_facilities NULL olan her kulup: itibardan + kulube ozgu sabit sapma
            * game_state.academy_seeded degilse: akademisi olmayan her kulube 4-6 kisilik baslangic akademisi
        Yapilanlari anlatan Turkce mesajlar dondurur (bir sey yapilmadiysa bos liste).
        """
        messages: list[str] = []
        self.db.flush()
        # Es zamanli iki giris ayni kariyeri ayni anda doldurmasin (akademiler iki kez kurulurdu):
        # game_state satiri islem sonuna kadar kilitlenir, bekleyen islem guncel satiri okur.
        st = self.db.get(GameState, 1, with_for_update=True, populate_existing=True) or self.state

        missing = list(self.db.scalars(
            select(Player).where(Player.potential_rating.is_(None)).order_by(Player.id)
        ))
        for p in missing:
            p.potential_rating = self._backfill_potential(p)
        if missing:
            messages.append(f"{len(missing)} oyuncuya potansiyel atandı.")

        no_facilities = list(self.db.scalars(
            select(Team).where(Team.youth_facilities.is_(None)).order_by(Team.id)
        ))
        for team in no_facilities:
            team.youth_facilities = self._default_youth_facilities(team)
        if no_facilities:
            messages.append(f"{len(no_facilities)} kulübe altyapı tesisi puanı verildi.")

        if not st.academy_seeded:
            self.db.flush()
            used_names = set(self.db.scalars(select(Player.name)))
            clubs = created = 0
            for team in self.teams():
                if self._academy_size(team) > 0:
                    continue
                rng = self._youth_rng("academy", team.id)
                count = rng.randint(*youth.INITIAL_ACADEMY_SIZE)
                specs = youth.generate_academy(rng, team.league.country, team.youth_facilities,
                                               team.reputation, count, used_names)
                self.db.add_all(self._academy_player(team, spec) for spec in specs)
                clubs += 1
                created += len(specs)
                self.db.flush()
                self.db.expire(team, ["academy_players"])
            st.academy_seeded = True
            if created:
                messages.append(f"{clubs} kulübe toplam {created} oyunculuk başlangıç akademisi (U-21) kuruldu.")
        self.db.flush()
        return messages

    def academy_players(self, team: Team) -> list[Player]:
        """Kulubun U-21 akademisi: potansiyel (azalan), guc, id sirasiyla. Veritabanindan taze okunur."""
        self.db.flush()
        return list(self.db.scalars(
            select(Player)
            .where(Player.team_id == team.id, Player.in_academy.is_(True))
            .order_by(Player.potential_rating.desc().nulls_last(), Player.overall_rating.desc(), Player.id)
        ))

    def _senior_players(self, team: Team) -> list[Player]:
        self.db.flush()
        return list(self.db.scalars(
            select(Player)
            .where(Player.team_id == team.id, Player.in_academy.is_(False))
            .order_by(Player.overall_rating.desc(), Player.id)
        ))

    def _academy_size(self, team: Team) -> int:
        return self.db.scalar(select(func.count()).select_from(Player).where(
            Player.team_id == team.id, Player.in_academy.is_(True))) or 0

    def youth_intake_week(self) -> int:
        """
        Genc girisinin yapildigi hafta: sezonun son haftasindan bir onceki (en az 1). Sezonun ilk haftasi
        oynanmadan (turnuva henuz kurulmamisken) de ayni degeri verir: kupa takvimi varsayilan formatla
        ongorulur (tournaments.projected_last_week, yan etkisiz).
        """
        return max(1, self._projected_season_weeks() - 1)

    def _projected_season_weeks(self) -> int:
        """total_weeks gibi; turnuva henuz kurulmadiysa kupa takvimi ongorulur."""
        cup_weeks = self.tournaments.projected_last_week()
        if self.game_mode is GameMode.TOURNAMENT:
            return cup_weeks
        return max(self.league_weeks(), cup_weeks)

    def _refresh_squads(self, team: Team) -> None:
        self.db.flush()
        self.db.expire(team, ["players", "academy_players"])

    @staticmethod
    def _check_owner(team: Team, player: Player) -> None:
        if player.team_id != team.id:
            raise AcademyError(f"{player.name} {team.name} oyuncusu değil.")

    def promote_to_senior(self, team: Team, player: Player) -> None:
        """Akademi oyuncusunu A takima yukseltir (kulube). A takim en fazla SENIOR_SQUAD_MAX; aksi AcademyError."""
        self._check_owner(team, player)
        if not player.in_academy:
            raise AcademyError(f"{player.name} zaten A takım kadrosunda.")
        seniors = self._senior_players(team)
        if len(seniors) >= SENIOR_SQUAD_MAX:
            raise AcademyError(
                f"A takım kadrosu dolu (en fazla {SENIOR_SQUAD_MAX} oyuncu). "
                f"{player.name} için önce bir oyuncuyu akademiye gönder ya da sat."
            )
        player.in_academy = False
        player.lineup_status, player.lineup_role = LineupStatus.BENCH, None
        self._refresh_squads(team)

    def send_to_academy(self, team: Team, player: Player) -> None:
        """
        A takim oyuncusunu U-21 akademisine gonderir (kadro disi, ilk 11'den cikar).
        Kurallar: A takimda en az transfers.SQUAD_FLOOR oyuncu ve MIN_SENIOR_KEEPERS kaleci kalir;
        akademi en fazla ACADEMY_CAPACITY; 21 yas ustu icin ACADEMY_OVERAGE_SLOTS kontenjan. Aksi AcademyError.
        """
        self._check_owner(team, player)
        if player.loan_from_team_id is not None:            # Faz 12 B2: kiralik oyuncu kulubunun akademisine gidemez
            raise AcademyError(f"{player.name} kiralık oyuncu; akademiye gönderilemez.")
        if player.in_academy:
            raise AcademyError(f"{player.name} zaten akademide.")
        remaining = [p for p in self._senior_players(team) if p.id != player.id]
        if len(remaining) < transfers.SQUAD_FLOOR:
            raise AcademyError(
                f"A takım kadrosu {transfers.SQUAD_FLOOR} oyuncunun altına düşemez; "
                f"{player.name} akademiye gönderilemez."
            )
        if (player.position is Position.GK
                and sum(1 for p in remaining if p.position is Position.GK) < MIN_SENIOR_KEEPERS):
            raise AcademyError(
                f"A takımda en az {MIN_SENIOR_KEEPERS} kaleci kalmalı; {player.name} akademiye gönderilemez."
            )
        academy = self.academy_players(team)
        if len(academy) >= ACADEMY_CAPACITY:
            raise AcademyError(f"Akademi dolu (en fazla {ACADEMY_CAPACITY} oyuncu).")
        if (player.age > ACADEMY_MAX_AGE
                and sum(1 for p in academy if p.age > ACADEMY_MAX_AGE) >= ACADEMY_OVERAGE_SLOTS):
            raise AcademyError(
                f"Akademide {ACADEMY_MAX_AGE} yaş üstü kontenjanı dolu (en fazla {ACADEMY_OVERAGE_SLOTS} oyuncu); "
                f"{player.name} ({player.age}) akademiye gönderilemez."
            )
        player.in_academy = True
        player.lineup_status, player.lineup_role = LineupStatus.OUT, None
        self._refresh_squads(team)

    def potential_scout_rating(self, team: Team) -> int | None:
        return self._staff_rating(team, StaffRole.SCOUT, "judging_potential")

    def potential_estimate(self, team: Team, player: Player) -> tuple[int, int]:
        """
        Izleyen kulubun gozlemcisine (judging_potential) gore potansiyel araligi (dusuk, yuksek).
        Kendi oyunculari icin de sislidir (tavan kesin bilinemez); gozlemci 18+ ise kesin.
        Ayni gozlemci + ayni oyuncu icin her zaman ayni aralik; gercek deger her zaman araliktadir.
        Kendi oyuncusunda alt sinir oyuncunun (kesin bilinen) gucunun altina inmez.
        """
        judging = self.potential_scout_rating(team)
        margin = development.potential_scout_margin(judging)
        true_potential = development.effective_potential(player.overall_rating, player.potential_rating)
        value = staff_rules.scouted_value(true_potential, margin, (judging or 0, player.id, "potential"))
        low, high = value.low, value.high
        if player.team_id == team.id:
            low = max(low, player.overall_rating)
            high = max(high, low)
        return low, high

    def academy_warnings(self, team: Team) -> list[str]:
        """Kullanicinin akademisi icin guncel uyarilar (yas ustu fazlasi, A takima hazir gencler, kadro siniri)."""
        academy = self.academy_players(team)
        seniors = self._senior_players(team)
        notes: list[str] = []
        overage = [p for p in academy if p.age > ACADEMY_MAX_AGE]
        if len(overage) > ACADEMY_OVERAGE_SLOTS:
            names = ", ".join(f"{p.name} ({p.age})" for p in overage)
            notes.append(
                f"Akademide {ACADEMY_MAX_AGE} yaş üstü {len(overage)} oyuncu var, kontenjan {ACADEMY_OVERAGE_SLOTS}: "
                f"{names}. Fazlasını A takıma yükselt."
            )
        for p in academy:
            group = [s.overall_rating for s in seniors if s.position is p.position]
            if group and p.overall_rating >= min(group):
                notes.append(f"{p.name} ({p.age}, {p.position.value}) A takıma hazır görünüyor: "
                             f"mevkisindeki en zayıf oyuncudan geri değil.")
        if len(seniors) > SENIOR_SQUAD_MAX:
            notes.append(f"A takım kadrosu {len(seniors)} oyuncu; sınır {SENIOR_SQUAD_MAX}. "
                         f"Yeni oyuncu yükseltmek için kadroyu daralt.")
        return notes

    # ---- haftalik gelisim

    def _week_minutes(self, week: int) -> dict[int, tuple[int, float | None]]:
        """Bu haftanin (lig + kupa, hafta ici dahil) oyuncu basina toplam dakika ve ortalama not."""
        self.db.flush()
        rows = self.db.execute(
            select(PlayerMatchStat.player_id, func.sum(PlayerMatchStat.minutes), avg_match_rating())
            .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
            .where(Fixture.season == self.season, Fixture.week == week)
            .group_by(PlayerMatchStat.player_id)
        )
        return {pid: (int(minutes or 0), float(avg) if avg is not None else None) for pid, minutes, avg in rows}

    def _weekly_development(self, week: int, report: WeekReport) -> None:
        """
        Haftalik gelisim ve yaslanma (kariyer modu). Deterministik: RNG kullanmaz.
        15B (bayrak): guc artisi mevkinin agirlik verdigi TUM ozelliklere +1 olarak dagilir (dengeli gelisim);
        bayrak kapaliyken eski kural (en agirlikli ozellige +2) aynen isler.
        Yalnizca degisebilecek oyuncular okunur: 32+ (gerileme) ya da 30 alti ve potansiyeli gucunden yuksek.
        Guc degisirse ozellikler, potansiyel (gerilemede) ve piyasa degeri guncellenir; kullanicinin
        oyunculari icin DevelopmentNote yazilir.
        """
        if self.game_mode is GameMode.TOURNAMENT:
            return
        played = self._week_minutes(week)
        season_weeks = self._projected_season_weeks()
        # 15B: gelisim oyuncunun profilini korur (tek ozellik doyumsuz buyumez). Bayrak kapaliyken eski kural.
        balanced = self._retirement_on()
        teams = {t.id: t for t in self._load_teams(Team.staff)}      # Faz 14D: heyetler tek seferde
        coaches = {tid: self._staff_rating(t, StaffRole.COACH, "working_with_youngsters") for tid, t in teams.items()}
        humans = self.human_team_ids()
        candidates = self._players_in_order(
            select(Player.id)
            .where(
                Player.team_id.isnot(None),
                or_(
                    Player.age >= development.DECLINE_START_AGE,
                    and_(Player.age < development.GROWTH_END_AGE,
                         func.coalesce(Player.potential_rating, Player.overall_rating) > Player.overall_rating),
                ),
            )
            .order_by(Player.id)
        )
        progress_only: dict[int, tuple[Player, float]] = {}
        for p in candidates:
            team = teams.get(p.team_id)
            minutes, avg_rating = played.get(p.id, (0, None))
            growth = development.weekly_growth(
                p.age, p.overall_rating, p.potential_rating, minutes, avg_rating, p.morale,
                coaches.get(p.team_id), p.in_academy,
                team.youth_facilities if team is not None else None, season_weeks,
            )
            decline = development.weekly_decline(p.age, season_weeks)
            if growth <= 0 and decline <= 0:
                continue
            before = p.overall_rating
            step = development.apply_progress(
                p.position, p.overall_rating, p.potential_rating,
                {a: getattr(p, a) for a in ENGINE_ATTRIBUTES}, p.development_progress, growth, decline,
                balanced,
            )
            if step.change == 0:
                progress_only[p.id] = (p, step.progress)
                continue
            if p.contract_overall is None:
                p.contract_overall = before          # 12. Asama: maas talebi icin sozlesme anindaki guc bilinmiyordu
            p.development_progress = step.progress
            p.overall_rating = step.overall
            for attr, value in step.attributes.items():
                setattr(p, attr, value)
            p.potential_rating = step.potential
            p.market_value = finance.market_value(p.overall_rating, p.age, p.position, p.potential_rating)
            if p.team_id in humans and team is not None:        # Faz 12: her insan kulubu kendi notlarini alir
                self._sink(report, p.team_id).development_notes.append(self._development_note(team, p, before))
        self._write_progress(progress_only)
        # Akademi oyunculari A takim maci oynamaz: A takimdan tasinan yorgunluk hafta icinde tamamen gecer
        self.db.execute(
            update(Player)
            .where(Player.in_academy.is_(True), Player.condition < fitness.CONDITION_MAX)
            .values(condition=fitness.CONDITION_MAX)
        )
        self.db.flush()

    def _write_progress(self, rows: Mapping[int, tuple[Player, float]]) -> None:
        """
        Yalnizca birikimi degisen oyuncular (haftada yuzlerce satir) TEK UPDATE ... FROM unnest(...) ile yazilir;
        ORM nesnesine 'kaydedilmis deger' olarak islenir (tekrar flush edilmez). Satir satir UPDATE
        haftayi ~%30 yavaslatiyordu.
        """
        if not rows:
            return
        # Faz 14D: VALUES yerine sutun basina tek dizi parametresi (ifade derlemesi onbellekte; sonuc ayni)
        table = Player.__table__
        stmt, _processors = _bulk_update_statement(table, [table.c.development_progress], self.db.get_bind().dialect)
        self.db.execute(stmt, {"ids": list(rows), "p0": [progress for _p, progress in rows.values()]})
        for player, progress in rows.values():
            set_committed_value(player, "development_progress", progress)

    def _development_note(self, team: Team, p: Player, before: int) -> DevelopmentNote:
        detail = f"({p.age}) {before} → {p.overall_rating}"
        low = high = None
        if p.overall_rating < before:
            detail += " · yaşlanma"
        else:
            low, high = self.potential_estimate(team, p)
            detail += f" · potansiyel {low if low == high else f'{low}-{high}'}"
        if p.in_academy:
            detail += " · akademi"
        return DevelopmentNote(
            p.id, p.name, team.name, detail, age=p.age, old_overall=before, new_overall=p.overall_rating,
            potential_low=low, potential_high=high, in_academy=p.in_academy,
        )

    # ---- genc girisi

    def _youth_intake(self, week: int, report: WeekReport) -> None:
        """
        Sezonda bir kez, youth_intake_week() haftasinda (kacirildiysa sonraki ilk oynanan haftada) TUM
        kuluplere YOUTH_INTAKE_SIZE genc. Kulup/sezon/tohumdan turetilmis ayri RNG. Ardindan akademi
        kapasitesi uygulanir. Kullanicinin kulubu icin YouthIntakeNote ve kapasite notlari rapora yazilir.
        Faz 15B (bayrak acik): sayi sabit degil, dunyanin nufus acigina gore olceklenir (_replacement_plan:
        kulup basina 0-8; fazla bir genc alacak kulupler sezona gore doner) ve uretim dunyanin guc capasina
        kaydirilir (strength_anchor -> youth.anchor_shift). Uretim kurallari, kalan pay ve isim havuzlari
        AYNIDIR; bayrak kapaliyken eski sabit aralik, ayni RNG cekilisi ve kaydirmasiz uretim kullanilir.
        """
        st = self.state
        if self.game_mode is GameMode.TOURNAMENT or st.last_youth_intake_season == self.season:
            return
        if week < self.youth_intake_week():
            return
        self.db.flush()
        used_names = set(self.db.scalars(select(Player.name)))
        humans = self.human_team_ids()
        teams = self.teams()
        # 15B: bayrak acikken sezonluk sayi dunyanin nufus acigina gore olceklenir (uretim kurallari ayni).
        plan = self._replacement_plan(len(teams)) if (teams and self._retirement_on()) else None
        shift = youth.anchor_shift(self.strength_anchor()) if plan is not None else 0.0
        total = 0
        for index, team in enumerate(teams):
            rng = self._youth_rng("intake", team.id)
            if plan is None:
                count = rng.randint(*YOUTH_INTAKE_SIZE)
            else:
                count = plan[0] + (1 if (index + self.season) % len(teams) < plan[1] else 0)
            facilities = team.youth_facilities or youth.default_facilities(team.reputation)
            specs = youth.generate_intake(rng, team.league.country, facilities, team.reputation, count,
                                          used_names, shift, plan[2] if plan is not None else None)
            newcomers = [self._academy_player(team, spec) for spec in specs]
            self.db.add_all(newcomers)
            total += len(newcomers)
            self.db.flush()
            sink = self._sink(report, team.id) if team.id in humans else None      # Faz 12: tum insan kulupleri
            released = self._enforce_academy_capacity(team, sink)
            if sink is not None:
                sink.youth_intake = [self._intake_note(team, p) for p in newcomers if p.id not in released]
                for note in sink.youth_intake:
                    if note.wonderkid:                   # 12. Asama: haber akisi
                        self._add_news(NewsKind.WONDERKID, f"{team.name} akademisine wonderkid katıldı: "
                                       f"{note.player_name} ({note.detail}).", team_id=team.id, week=week)
        st.last_youth_intake_season = self.season
        report.youth_intake_total = total
        self.db.flush()

    def _intake_note(self, team: Team, p: Player) -> YouthIntakeNote:
        low, high = self.potential_estimate(team, p)
        wonderkid = development.is_wonderkid(p.age, p.overall_rating, (low + high) // 2)
        pot = str(low) if low == high else f"{low}-{high}"
        detail = f"{p.age} yaş · {p.position.value} · güç {p.overall_rating} · potansiyel {pot}"
        if wonderkid:
            detail += " · wonderkid"
        return YouthIntakeNote(
            p.id, p.name, team.name, detail, age=p.age, position=p.position.value,
            overall=p.overall_rating, potential_low=low, potential_high=high, wonderkid=wonderkid,
        )

    def _enforce_academy_capacity(self, team: Team, report: WeekReport | ClubWeekReport | None = None) -> set[int]:
        """Akademi ACADEMY_CAPACITY'yi asarsa en dusuk potansiyelliler kulupten ayrilir (silinir)."""
        academy = self.academy_players(team)
        excess = len(academy) - ACADEMY_CAPACITY
        if excess <= 0:
            return set()
        ranked = sorted(academy, key=lambda p: (
            development.effective_potential(p.overall_rating, p.potential_rating), p.overall_rating, -p.age, p.id,
        ))
        released = ranked[:excess]
        names = ", ".join(f"{p.name} ({p.age})" for p in released)
        ids = {p.id for p in released}
        for p in released:
            self.db.delete(p)
        self._refresh_squads(team)
        if report is not None:
            report.academy_notes.append(
                f"Akademi kapasitesi ({ACADEMY_CAPACITY}) aşıldı; en düşük potansiyelli {len(released)} "
                f"oyuncu kulüpten ayrıldı: {names}."
            )
        return ids

    # ---- emeklilik ve yeni jenerasyon (Faz 15B)

    def _retirement_on(self) -> bool:
        """Kural bayragi (development.RETIREMENT ya da self.retirement) ve kariyer modu. Turnuvada emeklilik yok."""
        flag = development.RETIREMENT if self.retirement is None else bool(self.retirement)
        return flag and self.game_mode is not GameMode.TOURNAMENT

    def population_target(self) -> int:
        """
        Dunyanin nufus hedefi: kural bu kayitta ILK calistiginda o anki oyuncu sayisi yazilir (game_state).
        Yeni jenerasyon bu hedefe gore olceklenir; hedefin ustune cikmis eski bir kayit kucultulmez, oldugu
        yerde dengelenir (hedef bir kez yazilir).
        """
        st = self.state
        if st.population_target is None:
            st.population_target = int(self.db.scalar(select(func.count()).select_from(Player)) or 0)
            self.db.flush()
        return int(st.population_target)

    def strength_anchor(self) -> int:
        """
        Dunyanin guc capasi: kural ILK calistiginda tum oyuncularin ortalama gucu yazilir (game_state).
        Uretim seviyesi bu capaya kaydirilir (youth.anchor_shift = capa - youth.ANCHOR_REFERENCE): acik veri
        dunyasinda A takim oyunculari 70-85 gucundeyken uretim formulu 35-60 verir; capasiz her yeni nesil
        dunyayi puan puan asagi ceker (CM dersi: uzun kayitta ozellik kaymasi). Kaydirma sonrasi yeni
        jenerasyonun ortalama potansiyeli capanin biraz ustunde olur (~+8; kalibrasyon 20 sezonluk kosuyla
        yapildi). Capa SABITTIR: geri besleme yapmaz, bu yuzden dunya kendi ortalamasinin etrafinda salinir,
        suruklenmez.
        """
        st = self.state
        if st.strength_target is None:
            mean = self.db.scalar(select(func.avg(Player.overall_rating)))
            st.strength_target = development.clamp_rating(float(mean) if mean is not None
                                                          else youth.ANCHOR_REFERENCE)
            self.db.flush()
        return int(st.strength_target)

    def _retirement_rng(self, player_id: int, season: int) -> random.Random:
        """Emeklilik zari: cm.rng dizisinden BAGIMSIZ (mac ve transfer sonuclari degismez), tohumlu kayitta sabit."""
        if self.seed is None:
            return random.Random()
        return random.Random(zlib.crc32(f"retire|{self.seed}|{int(season)}|{int(player_id)}".encode()))

    @staticmethod
    def _clubless_weeks(free_agent_since: int | None, career_week: int) -> int:
        return max(0, int(career_week) - int(free_agent_since)) if free_agent_since is not None else 0

    def _retirement_candidates_stmt(self, age_column):
        """Emeklilik adayi sorgusu: yasi geleni ya da kulupsuz ve 26+ olan (kiralik oyuncu haric)."""
        return (
            Player.loan_from_team_id.is_(None),
            or_(age_column >= development.RETIRE_MIN_AGE,
                and_(Player.team_id.is_(None), age_column >= development.RETIRE_CLUBLESS_MIN_AGE)),
        )

    def _expected_retirements(self) -> tuple[int, dict[Position, float]]:
        """
        Bu sezonun devrinde emekli olmasi BEKLENEN oyuncu sayisi ve MEVKI dagilimi (olasiliklarin toplami;
        devirde yas 1 artar, sozlesme 1 azalir). Genc girisi bu sayiyi onceden doldurur (nufus emeklilik
        dalgasinin onunden kapanir) ve dagilimi mevki karisimini belirler (kaleci hattinin yaslanmamasi icin).
        """
        next_age = Player.age + 1
        rows = self.db.execute(
            select(Player.age, Player.overall_rating, Player.contract_years, Player.team_id,
                   Player.free_agent_since, Player.position)
            .where(*self._retirement_candidates_stmt(next_age))
        ).all()
        career_week = self.career_week
        by_position: dict[Position, float] = {}
        total = 0.0
        for age, overall, years, team_id, since, position in rows:
            clubless = team_id is None
            chance = development.retirement_chance(
                int(age) + 1, int(overall), max(0, int(years or 0) - 1), clubless,
                self._clubless_weeks(since, career_week),
            )
            total += chance
            by_position[position] = by_position.get(position, 0.0) + chance
        return int(round(total)), by_position

    def _retirement_guard(self, drawn: list[Player]) -> list[Player]:
        """
        Kadro guvencesi: emeklilik hicbir kulubu RETIREMENT_SQUAD_FLOOR oyuncunun ya da MIN_SENIOR_KEEPERS
        kalecinin altina dusuremez (15A'nin devir guvencesi bundan SONRA calisir). Once en guclu emekli gider
        (vakti geldi), taban zorlanirsa en zayiflar bir sezon daha kadroda kalir. Kulupsuz ve akademi
        oyunculari A takim tabanini etkilemez.
        """
        if not drawn:
            return []
        counts = {
            int(team_id): (int(squad or 0), int(keepers or 0))
            for team_id, squad, keepers in self.db.execute(
                select(Player.team_id, func.count(),
                       func.count().filter(Player.position == Position.GK))
                .where(Player.team_id.isnot(None), Player.in_academy.is_(False))
                .group_by(Player.team_id)
            )
        }
        by_team: dict[int | None, list[Player]] = {}
        for p in drawn:
            by_team.setdefault(p.team_id, []).append(p)
        allowed: list[Player] = []
        for team_id, players in by_team.items():
            if team_id is None:
                allowed += players                      # kulupsuz oyuncu hicbir kadroyu bosaltmaz
                continue
            squad, keepers = counts.get(int(team_id), (0, 0))
            for p in sorted(players, key=lambda x: (-x.overall_rating, x.id)):
                if p.in_academy:
                    allowed.append(p)
                    continue
                if squad - 1 < RETIREMENT_SQUAD_FLOOR:
                    break
                if p.position is Position.GK and keepers - 1 < MIN_SENIOR_KEEPERS:
                    continue
                allowed.append(p)
                squad -= 1
                keepers -= 1 if p.position is Position.GK else 0
        return sorted(allowed, key=lambda p: p.id)

    def _retire_players(self, new_season: int) -> list[str]:
        """
        Faz 15B: sezon devrinde emeklilik (yas ARTTIKTAN sonra, 15A'nin serbest birakmasindan ONCE: suresi biten
        veteran "serbest kaldi" degil "futbolu birakti" olur). Zar tohumlu ve cm.rng'den bagimsizdir.
        Emekli olan oyuncunun SATIRI SILINIR (akademi kapasitesindeki gibi); kaydi transfer_log RETIRED satirinda
        adiyla kalir. Insan kulubunun her emeklisi + AI kuluplerinin en degerli birkaci haber olur; insan
        kuluplerine gelen kutusu mesaji yazilir. Donus: menajerin devir notlari.
        """
        if not self._retirement_on():
            return []
        self.db.flush()
        self.population_target()                     # ilk calismada dunyanin nufus hedefi yazilir
        candidates = self._players_in_order(
            select(Player.id).where(*self._retirement_candidates_stmt(Player.age)).order_by(Player.id)
        )
        career_week = self.career_week
        drawn: list[Player] = []
        for p in candidates:
            clubless = p.team_id is None
            chance = development.retirement_chance(
                p.age, p.overall_rating, p.contract_years, clubless,
                self._clubless_weeks(p.free_agent_since, career_week),
            )
            if chance > 0 and self._retirement_rng(p.id, new_season).random() < chance:
                drawn.append(p)
        going = self._retirement_guard(drawn)
        if not going:
            return []

        humans = self.human_team_ids()
        teams = {t.id: t for t in self.teams()}
        by_human: dict[int, list[tuple[int, str]]] = {}        # kulup -> (guc, satir) -- SILMEDEN once hazirlanir
        world: list[tuple[int, str]] = []                      # (piyasa degeri, haber metni) -- AI kulupleri
        for p in going:
            team = teams.get(p.team_id) if p.team_id is not None else None
            reason = development.retirement_reason(p.age, p.overall_rating, team is None)
            self.db.add(TransferLog(
                season=new_season, week=1, player_id=None, player_name=p.name,
                from_team_id=team.id if team is not None else None,
                from_team_name=team.name if team is not None else None,
                to_team_id=None, to_team_name=RETIRED_TEAM_NAME,
                fee=0, wage=int(p.current_wage or 0), kind=TransferKind.RETIRED.value,
            ))
            where = team.name if team is not None else RELEASED_TEAM_NAME
            text = f"{p.name} ({p.age}, {where}) futbolu bıraktı: {reason}."
            if team is not None and team.id in humans:
                by_human.setdefault(team.id, []).append((p.overall_rating, self._retirement_line(p)))
                self._add_news(NewsKind.RETIREMENT, text, team_id=team.id, week=1, season=new_season)
            else:
                world.append((int(p.market_value or 0), text))
        for _value, text in sorted(world, key=lambda row: -row[0])[:RETIREMENT_NEWS_PER_SEASON]:
            self._add_news(NewsKind.RETIREMENT, text, week=1, season=new_season)
        touched = {p.team_id for p in going if p.team_id is not None}
        for p in going:
            # Eklentiler (paylasilan dunya): acik teklifler / kiralik dosyalari kapansin -- satir silinmeden ONCE
            self.run_extensions("on_player_moved", p, p.team_id, None)
        # Faz 14D deseni: satir satir ORM silme (200 emeklide ~1 dk) yerine TEK ifade; cocuk satirlarini
        # veritabani CASCADE'i siler. Once kadro koleksiyonlari suresi doldurulur (silinen nesneye referans
        # kalmasin: Team.players delete-orphan), sonra nesneler oturumdan cikarilir.
        self.db.flush()
        for team_id in sorted(touched):
            team = teams.get(team_id)
            if team is not None:
                self.db.expire(team, ["players", "academy_players"])
        going_ids = [p.id for p in going]
        for p in going:
            self.db.expunge(p)
        self.db.execute(delete(Player).where(Player.id.in_(going_ids)))
        self.db.flush()
        for team_id in sorted(by_human):
            team = teams.get(team_id)
            if team is not None:
                self._post_retirement_inbox(team, by_human[team_id], new_season)
        focus = self._acting_team_id()
        return [self._retirement_note(teams[focus], by_human[focus])] if focus in by_human else []

    @staticmethod
    def _retirement_line(p: Player) -> str:
        return f"{p.name} ({p.age}) · {p.position.value} · güç {p.overall_rating}"

    @staticmethod
    def _retirement_note(team: Team, rows: list[tuple[int, str]]) -> str:
        names = ", ".join(line for _overall, line in sorted(rows, key=lambda row: -row[0]))
        return f"{team.name}: {len(rows)} oyuncu futbolu bıraktı — {names}."

    def _post_retirement_inbox(self, team: Team, rows: list[tuple[int, str]], new_season: int) -> None:
        """15B emeklilik mesaji (15D gelen kutusu; kapaliyken sessizce atlanir). inbox.py'ye DOKUNULMAZ."""
        if not self._inbox_on() or not rows:
            return
        ranked = [line for _overall, line in sorted(rows, key=lambda row: -row[0])]
        inbox.post(
            self.db, manager_id=inbox.manager_id_for(self, team.id), kind=inbox.KIND_SQUAD,
            subject=f"{len(rows)} oyuncu futbolu bıraktı",
            body=f"{team.name} kadrosundan {len(rows)} oyuncu {new_season}. sezon öncesinde kariyerini "
                 f"noktaladı. Kadro derinliğini gözden geçir.",
            team_id=team.id, season=new_season, week=1, career_week=self.career_week,
            game_date=inbox.match_date(new_season, 1, start=self.state.season_start_date),
            lines=[["info", line] for line in ranked[:RETIREMENT_INBOX_LINES]],
            ref_type=inbox.REF_TEAM, ref_id=team.id,
        )

    def _replacement_plan(self, clubs: int) -> tuple[int, int, tuple[tuple[Position, int], ...]]:
        """
        15B: bu sezonun genc girisi olcegi -- (kulup basina taban, fazladan bir genc alacak kulup sayisi,
        mevki agirliklari). Sayi youth.replacement_deficit ile YUMUSATILIR (kusak dalgasi geri donmesin);
        mevki karisimi birakmasi beklenen kusagin dagilimiyla harmanlanir (youth.replacement_positions).
        """
        total = int(self.db.scalar(select(func.count()).select_from(Player)) or 0)
        expected, by_position = self._expected_retirements()
        deficit = youth.replacement_deficit(self.population_target(), total, expected)
        per_club, extra = youth.replacement_intake(deficit, clubs)
        return per_club, extra, youth.replacement_positions(by_position)

    # ---- sezon basi akademi yonetimi

    def _season_academy_management(self, notes_by_team: dict[int, list[str]] | None = None) -> list[str]:
        """
        AI kulupleri akademisini yonetir; kullanicinin kulubu icin yalnizca uyari notlari dondurulur.
        Faz 12: tum insan kulupleri dokunulmaz; notlari notes_by_team'e (verilirse), odak kulubunki donus degerine.
        """
        humans = self.human_team_ids()
        focus = self._acting_team_id()
        notes: list[str] = []
        for team in self.teams():
            if team.id in humans:
                team_notes = self.academy_warnings(team)
                if team.id == focus:
                    notes += team_notes
                if notes_by_team is not None:
                    notes_by_team.setdefault(team.id, []).extend(team_notes)
            else:
                self._ai_manage_academy(team)
        return notes

    def _ai_manage_academy(self, team: Team) -> None:
        """
        AI kulubu (sezon basi):
            1) A takim AI_MIN_SENIOR_SQUAD'in altindaysa en guclu gencler yukselir
            2) mevkisindeki en zayif A takim oyuncusundan iyi olan (ya da mevkide kimse yoksa) yukselir
            3) 21 yas ustu kontenjani (en yuksek potansiyelliler kalir) asan: yer varsa ve mevkisinin en
               zayifindan AI_OVERAGE_PROMOTE_MARGIN'den fazla geride degilse yukselir, aksi serbest birakilir
        A takim hicbir adimda SENIOR_SQUAD_MAX'i asmaz.
        """
        academy = self.academy_players(team)
        if not academy:
            return
        seniors = self._senior_players(team)
        established = list(seniors)          # olcu: bu cagrida yukselenler mevki tabanini dusurmesin

        def weakest(position: Position) -> int | None:
            group = [s.overall_rating for s in established if s.position is position]
            return min(group) if group else None

        def promote(p: Player) -> None:
            p.in_academy = False
            p.lineup_status, p.lineup_role = LineupStatus.BENCH, None
            seniors.append(p)
            academy.remove(p)

        def strongest_first(players: list[Player]) -> list[Player]:
            return sorted(players, key=lambda p: (
                -p.overall_rating, -development.effective_potential(p.overall_rating, p.potential_rating), p.id,
            ))

        for p in strongest_first(academy):
            if len(seniors) >= min(AI_MIN_SENIOR_SQUAD, SENIOR_SQUAD_MAX):
                break
            promote(p)
        for p in strongest_first(academy):
            if len(seniors) >= SENIOR_SQUAD_MAX:
                break
            floor = weakest(p.position)
            if floor is None or p.overall_rating > floor:
                promote(p)
        overage = sorted(
            (p for p in academy if p.age > ACADEMY_MAX_AGE),
            key=lambda p: (-development.effective_potential(p.overall_rating, p.potential_rating),
                           -p.overall_rating, p.id),
        )
        for p in overage[ACADEMY_OVERAGE_SLOTS:]:
            floor = weakest(p.position)
            if len(seniors) < SENIOR_SQUAD_MAX and (floor is None or p.overall_rating >= floor - AI_OVERAGE_PROMOTE_MARGIN):
                promote(p)
            else:
                academy.remove(p)
                self.db.delete(p)
        self._refresh_squads(team)

    # ------------------------------------------------------------------ tesisler ve sponsorluk (11. Asama)

    def _club_rng(self, kind: str, team_id: int, season: int) -> random.Random:
        """Sponsor teklifleri RNG'si: cm.rng dizisinden BAGIMSIZ; tohumlu kariyerde tohum + sezon + kulupten."""
        if self.seed is None:
            return random.Random()
        return random.Random(zlib.crc32(f"{kind}|{self.seed}|{season}|{team_id}".encode()))

    @staticmethod
    def _default_club_facilities(team: Team) -> facilities.ClubFacilities:
        """Eski kayit: stadyum ve saglik merkezi itibardan + kulube ozgu sabit tohum (kariyer tohumundan bagimsiz)."""
        rng = random.Random(zlib.crc32(f"club-facilities|{team.id}".encode()))
        return facilities.default_facilities(team.reputation, rng)

    def _fill_team_facilities(self, team: Team) -> tuple[bool, bool, bool]:
        """Kulubun NULL tesislerini varsayilanla doldurur. Donus: (altyapi, stadyum, saglik) dolduruldu mu."""
        youth_set = team.youth_facilities is None
        if youth_set:
            team.youth_facilities = self._default_youth_facilities(team)
        capacity_set, medical_set = team.stadium_capacity is None, team.medical_facilities is None
        if capacity_set or medical_set:
            defaults = self._default_club_facilities(team)
            if capacity_set:
                team.stadium_capacity = defaults.stadium_capacity
            if medical_set:
                team.medical_facilities = defaults.medical_facilities
        return youth_set, capacity_set, medical_set

    @staticmethod
    def _never_sponsored(team: Team) -> bool:
        """Hic sponsor sozlesmesi olmamis (kurulmamis) kulup. Sozlesmesi bitenin bitis sezonu durur."""
        return team.sponsor_name is None and team.sponsor_until_season is None

    @staticmethod
    def _apply_sponsor(team: Team, offer: facilities.SponsorOffer, season: int) -> None:
        team.sponsor_name = offer.brand
        team.sponsor_weekly = offer.weekly
        team.sponsor_until_season = facilities.contract_end_season(season, offer.seasons)

    def ensure_club_setup(self) -> list[str]:
        """
        Eski kayitlar ve yeni dunyalar icin IDEMPOTENT doldurma (kariyer silinmez):
            * stadium_capacity / medical_facilities NULL olan her kulup: itibardan + kulube ozgu sabit tohumla
            * hic sponsor sozlesmesi olmamis her kulup (AI ve kullanici): baslangic sozlesmesi (piyasanin biraz
              altinda, imza primi odenmez) ve bekleyen 3 teklif. Kullanici hangi kulubu secerse secsin teklifleri
              hazirdir; imzadan sonra (teklifler bosalir) tekrar giriste yeni teklif URETILMEZ (prim istismari yok),
              yenileri sezon basinda gelir.
        Yapilanlari anlatan Turkce mesajlar dondurur (bir sey yapilmadiysa bos liste).
        """
        messages: list[str] = []
        self.db.flush()
        # Es zamanli iki giris ayni kariyeri ayni anda doldurmasin: game_state satiri islem sonuna kadar kilitli
        st = self.db.get(GameState, 1, with_for_update=True, populate_existing=True) or self.state
        teams = list(self.db.scalars(
            select(Team).order_by(Team.id).execution_options(populate_existing=True)
        ))
        capacities = medicals = sponsored = 0
        season_weeks = max(1, self._projected_season_weeks())
        for team in teams:
            if team.stadium_capacity is None or team.medical_facilities is None:
                defaults = self._default_club_facilities(team)
                if team.stadium_capacity is None:
                    team.stadium_capacity = defaults.stadium_capacity
                    capacities += 1
                if team.medical_facilities is None:
                    team.medical_facilities = defaults.medical_facilities
                    medicals += 1
            if self._never_sponsored(team):
                rng = random.Random(zlib.crc32(f"sponsor|{team.id}".encode()))
                contract = facilities.starting_sponsor(rng, team.reputation)
                self._apply_sponsor(team, contract, st.season)
                if not team.sponsor_offers:
                    team.sponsor_offers = facilities.offers_to_json(facilities.generate_sponsor_offers(
                        rng, team.reputation, exclude_brands={contract.brand}, season_weeks=season_weeks))
                sponsored += 1
        if capacities:
            messages.append(f"{capacities} kulübe stadyum kapasitesi atandı.")
        if medicals:
            messages.append(f"{medicals} kulübe sağlık merkezi seviyesi verildi.")
        if sponsored:
            messages.append(f"{sponsored} kulübe başlangıç sponsor sözleşmesi ve sponsor teklifleri verildi.")
        self.db.flush()
        return messages

    def _league_home_matches_per_season(self, team: Team) -> int:
        """Bu sezon kulubun ic saha lig maci sayisi (fikstur yoksa ligdeki rakip sayisi: cift devreli lig)."""
        count = self.db.scalar(
            select(func.count()).select_from(Fixture)
            .where(Fixture.season == self.season, Fixture.home_team_id == team.id,
                   Fixture.competition == Competition.LEAGUE)
        )
        if count:
            return int(count)
        clubs = self.db.scalar(select(func.count()).select_from(Team).where(Team.league_id == team.league_id))
        return max(0, int(clubs or 0) - 1)

    def facility_status(self, team: Team) -> dict:
        """
        Kulubun tesis ve sponsor durumu (arayuz icin duz dict; yalnizca okur). Kurulmamis (NULL) tesis icin
        ensure_club_setup'in yazacagi varsayilan gosterilir ("configured": False).
            {
              "team_id", "team_name", "season", "transfer_budget", "configured": bool,
              "youth":   {"kind", "label", "level", "max_level", "at_max", "next_level", "upgrade_cost",
                          "affordable", "potential_shift_now", "potential_shift_next",
                          "growth_multiplier_now", "growth_multiplier_next"},
              "medical": {"kind", "label", "level", "max_level", "at_max", "next_level", "upgrade_cost",
                          "affordable", "recovery_multiplier_now", "recovery_multiplier_next"},
              "stadium": {"kind", "label", "capacity", "min_capacity", "max_capacity", "step", "at_max",
                          "next_capacity", "expansion_cost", "affordable", "demand", "attendance_now",
                          "attendance_next", "gate_income_now", "gate_income_next", "home_matches_per_season",
                          "season_gate_now", "season_gate_next", "payback_seasons"},
              "sponsor": {"name", "weekly", "until_season", "active", "seasons_left", "pending_offers"},
            }
        *_next / upgrade_cost / next_* degerleri en ust seviyede None. gate_income_*: IC SAHA MACI BASINA net EUR.
        season_gate_*: sezonluk ic saha LIG maci geliri (kupa maclari ek gelirdir); payback_seasons: genisletme
        bedelinin ek bilet geliriyle kac sezonda transfer kasasina geri dondugu (ek gelir yoksa None).
        potential_shift_*: genc girisinde beklenen ortalama potansiyel kaymasi (notr kulube gore, puan).
        """
        season, budget = self.season, team.transfer_budget
        youth_level = team.youth_facilities or self._default_youth_facilities(team)
        defaults = (self._default_club_facilities(team)
                    if team.stadium_capacity is None or team.medical_facilities is None else None)
        medical_level = team.medical_facilities or defaults.medical_facilities
        capacity = team.stadium_capacity or defaults.stadium_capacity

        def level_block(kind: str, level: int) -> dict:
            at_max = level >= facilities.FACILITY_MAX
            cost = None if at_max else facilities.facility_upgrade_cost(kind, level)
            return {
                "kind": kind, "label": facilities.FACILITY_LABELS[kind], "level": level,
                "max_level": facilities.FACILITY_MAX, "at_max": at_max,
                "next_level": None if at_max else level + 1, "upgrade_cost": cost,
                "affordable": cost is not None and cost <= budget,
            }

        youth_block = level_block("youth", youth_level)
        nxt = youth_block["next_level"]
        youth_block.update(
            potential_shift_now=facilities.youth_potential_shift(youth_level, team.reputation),
            potential_shift_next=None if nxt is None else facilities.youth_potential_shift(nxt, team.reputation),
            growth_multiplier_now=facilities.youth_growth_multiplier(youth_level),
            growth_multiplier_next=None if nxt is None else facilities.youth_growth_multiplier(nxt),
        )
        medical_block = level_block("medical", medical_level)
        nxt = medical_block["next_level"]
        medical_block.update(
            recovery_multiplier_now=facilities.medical_recovery_multiplier(medical_level),
            recovery_multiplier_next=None if nxt is None else facilities.medical_recovery_multiplier(nxt),
        )
        stadium_at_max = capacity >= facilities.STADIUM_MAX
        next_capacity = None if stadium_at_max else facilities.next_stadium_capacity(capacity)
        expansion_cost = None if stadium_at_max else facilities.stadium_expansion_cost(capacity)
        stadium_block = {
            "kind": "stadium", "label": facilities.FACILITY_LABELS["stadium"], "capacity": capacity,
            "min_capacity": facilities.STADIUM_MIN, "max_capacity": facilities.STADIUM_MAX,
            "step": facilities.STADIUM_STEP, "at_max": stadium_at_max, "next_capacity": next_capacity,
            "expansion_cost": expansion_cost,
            "affordable": expansion_cost is not None and expansion_cost <= budget,
            "demand": facilities.stadium_demand(team.reputation),
            "attendance_now": facilities.attendance(capacity, team.reputation),
            "attendance_next": None if next_capacity is None else facilities.attendance(next_capacity, team.reputation),
            "gate_income_now": facilities.gate_income(capacity, team.reputation),
            "gate_income_next": None if next_capacity is None else facilities.gate_income(next_capacity, team.reputation),
        }
        home_matches = self._league_home_matches_per_season(team)
        stadium_block.update(
            home_matches_per_season=home_matches,
            season_gate_now=facilities.season_gate_income(capacity, team.reputation, home_matches),
            season_gate_next=None if next_capacity is None
            else facilities.season_gate_income(next_capacity, team.reputation, home_matches),
            payback_seasons=facilities.expansion_payback_seasons(
                expansion_cost, stadium_block["gate_income_now"], stadium_block["gate_income_next"], home_matches
            ),
        )
        active = facilities.sponsor_active(team.sponsor_name, team.sponsor_until_season, season)
        sponsor_block = {
            "name": team.sponsor_name if active else None,
            "weekly": team.sponsor_weekly if active else 0,
            "until_season": team.sponsor_until_season if active else None,
            "active": active,
            "seasons_left": facilities.sponsor_seasons_left(team.sponsor_until_season, season) if active else 0,
            "pending_offers": len(self.sponsor_offers(team)),
        }
        return {
            "team_id": team.id, "team_name": team.name, "season": season, "transfer_budget": budget,
            "configured": None not in (team.youth_facilities, team.medical_facilities, team.stadium_capacity),
            "youth": youth_block, "medical": medical_block, "stadium": stadium_block, "sponsor": sponsor_block,
        }

    def upgrade_facility(self, team: Team, kind: str) -> int:
        """
        Tesis yatirimi: "youth" / "medical" bir seviye, "stadium" +STADIUM_STEP koltuk (en fazla STADIUM_MAX).
        Bedel transfer kasasindan duser; odenen bedeli dondurur. Kurulmamis tesisler once varsayilanla doldurulur.
        En ust seviye/kapasite, yetersiz kasa ya da bilinmeyen tur -> FacilityError, hicbir sey degismez.
        """
        if kind not in facilities.UPGRADE_KINDS:
            raise FacilityError(
                f"Bilinmeyen tesis: {kind!r}. Seçenekler: altyapı (youth), sağlık merkezi (medical), stadyum (stadium)."
            )
        label = facilities.FACILITY_LABELS[kind]
        youth_level = team.youth_facilities or self._default_youth_facilities(team)
        defaults = (self._default_club_facilities(team)
                    if team.stadium_capacity is None or team.medical_facilities is None else None)
        if kind == "stadium":
            capacity = team.stadium_capacity or defaults.stadium_capacity
            if capacity >= facilities.STADIUM_MAX:
                raise FacilityError(f"{label} zaten en büyük kapasitede ({_seats(facilities.STADIUM_MAX)} koltuk).")
            cost = facilities.stadium_expansion_cost(capacity)
            what = f"{label} genişletmesi ({_seats(capacity)} → {_seats(facilities.next_stadium_capacity(capacity))} koltuk)"
        else:
            level = youth_level if kind == "youth" else (team.medical_facilities or defaults.medical_facilities)
            if level >= facilities.FACILITY_MAX:
                raise FacilityError(f"{label} zaten en üst seviyede ({facilities.FACILITY_MAX}).")
            cost = facilities.facility_upgrade_cost(kind, level)
            what = f"{label} {level} → {level + 1}"
        if cost > team.transfer_budget:
            raise FacilityError(
                f"Transfer bütçesi yetersiz: {what} için {finance.format_money(cost)} gerekli, "
                f"kasada {finance.format_money(team.transfer_budget)} var."
            )
        self._fill_team_facilities(team)
        team.transfer_budget -= cost
        if kind == "stadium":
            team.stadium_capacity = facilities.next_stadium_capacity(team.stadium_capacity)
        elif kind == "youth":
            team.youth_facilities += 1
        else:
            team.medical_facilities += 1
        self.db.flush()
        return cost

    def sponsor_offers(self, team: Team) -> list[facilities.SponsorOffer]:
        """Kulubun bekleyen sponsor teklifleri (imzalanana ya da sezon basinda yenilenene kadar)."""
        return facilities.offers_from_json(team.sponsor_offers)

    def sign_sponsor(self, team: Team, index: int) -> facilities.SponsorOffer:
        """
        Bekleyen tekliflerden `index`'inciyi (0 tabanli) imzalar: mevcut sozlesmenin yerine gecer (bu sezon dahil
        `seasons` sezon; sezon bitmis ve yenisi baslamamissa gelecek sezondan itibaren), imza primi transfer
        kasasina eklenir, teklifler temizlenir. Teklif yoksa ya da indeks gecersizse FacilityError, hicbir sey degismez.
        """
        offers = self.sponsor_offers(team)
        if not offers:
            raise FacilityError("Bekleyen sponsor teklifi yok. Yeni teklifler sezon başında gelir.")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(offers):
            raise FacilityError(f"Geçersiz sponsor teklifi seçimi; {len(offers)} teklif arasından birini seç.")
        offer = offers[index]
        # Sezon bittikten sonra (yeni sezon baslamadan) imzalanan sozlesme gelecek sezondan sayilir: yoksa 1 sezonluk
        # anlasma sezon devrinde hemen biterdi
        self._apply_sponsor(team, offer, self.season + 1 if self.season_finished else self.season)
        team.transfer_budget += offer.signing_bonus
        team.sponsor_offers = []
        if team.id in self.human_team_ids():             # 12. Asama: haber akisi (Faz 12: her insan kulubu)
            bonus = f", imza primi {finance.format_money(offer.signing_bonus)}" if offer.signing_bonus else ""
            self._add_news(NewsKind.SPONSOR, f"{team.name}, {offer.brand} ile {offer.seasons} sezonluk sponsorluk "
                           f"anlaşması imzaladı ({finance.format_money(offer.weekly)}/hafta{bonus}).", team_id=team.id)
        self.db.flush()
        return offer

    def _season_club_economy(self, new_season: int, notes_by_team: dict[int, list[str]] | None = None) -> list[str]:
        """
        Sezon basi (kariyer modu; start_new_season, sezon numarasi artmadan once cagrilir):
            1) biten sozlesmeler sona erer (ad ve bedel silinir, bitis sezonu kalir)
            2) her kulube taze teklifler: kullanicininkiler imzalanana kadar bekler (notlar dondurulur);
               AI en degerli teklifi, sozlesmesi yoksa ya da mevcut sozlesmesinden belirgin degerliyse imzalar
            3) AI kulupleri butcesinin kucuk bir payiyla en fazla bir tesis yatirimi yapar
        Hic kurulmamis kulup (ensure_club_setup calismamis) atlanir: eski davranis korunur.
        Faz 12: tum insan kuluplerinin teklifleri bekler; notlari notes_by_team'e, odak kulubunki donus degerine.
        """
        humans = self.human_team_ids()
        focus = self._acting_team_id()
        season_weeks = max(1, self._projected_season_weeks())
        notes: list[str] = []
        for team in self.teams():
            if self._never_sponsored(team):
                continue
            expired = None
            if team.sponsor_name is not None and not facilities.sponsor_active(
                    team.sponsor_name, team.sponsor_until_season, new_season):
                expired = team.sponsor_name
                team.sponsor_name, team.sponsor_weekly = None, 0
            rng = self._club_rng("sponsor-offers", team.id, new_season)
            offers = facilities.generate_sponsor_offers(
                rng, team.reputation, exclude_brands={team.sponsor_name} if team.sponsor_name else (),
                season_weeks=season_weeks)
            if team.id in humans:
                team.sponsor_offers = facilities.offers_to_json(offers)
                team_notes = self._sponsor_season_notes(team, expired, offers, new_season)
                if team.id == focus:
                    notes += team_notes
                if notes_by_team is not None:
                    notes_by_team.setdefault(team.id, []).extend(team_notes)
                continue
            team.sponsor_offers = []
            best = offers[facilities.best_offer_index(offers, season_weeks)]
            if team.sponsor_name is None or facilities.offer_score(best, season_weeks) > (
                    team.sponsor_weekly * season_weeks * (1.0 + AI_SPONSOR_SWITCH_MARGIN)):
                self._apply_sponsor(team, best, new_season)
                team.transfer_budget += best.signing_bonus
            self._ai_invest_in_facilities(team)
        self.db.flush()
        return notes

    @staticmethod
    def _sponsor_season_notes(team: Team, expired: str | None, offers: list[facilities.SponsorOffer],
                              new_season: int) -> list[str]:
        notes: list[str] = []
        money = finance.format_money
        if expired:
            notes.append(f"Sponsor sözleşmen sona erdi ({expired}); yeni sponsor imzalayana kadar sponsor geliri yok.")
        elif team.sponsor_name:
            left = facilities.sponsor_seasons_left(team.sponsor_until_season, new_season)
            notes.append(f"Mevcut sponsor: {team.sponsor_name}, {money(team.sponsor_weekly)}/hafta, "
                         f"bu sezon dahil {left} sezon.")
        listed = "; ".join(
            f"{o.brand} {money(o.weekly)}/hafta, {o.seasons} sezon"
            + (f", imza primi {money(o.signing_bonus)}" if o.signing_bonus else "")
            for o in offers
        )
        notes.append(f"{len(offers)} yeni sponsor teklifi var: {listed}. İmzalanan teklif mevcut sözleşmenin yerini alır.")
        return notes

    def _ai_invest_in_facilities(self, team: Team) -> int:
        """
        AI kulubu (sezon basi) en fazla bir tesis yatirimi yapar: bedeli kasanin AI_FACILITY_BUDGET_SHARE payini
        asmayan en ucuz secenek. Stadyum ancak talep yeni kapasiteyi dolduracaksa aday olur. Kurulmamis kulup
        yatirim yapmaz. Deterministik (RNG yok). Odenen bedel (yoksa 0).
        """
        if None in (team.youth_facilities, team.medical_facilities, team.stadium_capacity):
            return 0
        candidates: list[tuple[int, str]] = []
        for kind, level in (("youth", team.youth_facilities), ("medical", team.medical_facilities)):
            if level < facilities.FACILITY_MAX:
                candidates.append((facilities.facility_upgrade_cost(kind, level), kind))
        capacity = team.stadium_capacity
        if (capacity < facilities.STADIUM_MAX
                and facilities.stadium_demand(team.reputation) >= facilities.next_stadium_capacity(capacity)):
            candidates.append((facilities.stadium_expansion_cost(capacity), "stadium"))
        limit = team.transfer_budget * AI_FACILITY_BUDGET_SHARE
        affordable = sorted(c for c in candidates if c[0] <= limit)
        if not affordable:
            return 0
        return self.upgrade_facility(team, affordable[0][1])

    # ------------------------------------------------------------------ kariyer paketi (12. Asama)

    def _concerns_active(self) -> bool:
        """Oynama suresi kaygilari yalnizca kariyer modunda (turnuva modu eski davranis)."""
        return self.game_mode is not GameMode.TOURNAMENT

    def _note_payout(self, team_id: int, key: str, amount: int) -> None:
        row = self.season_payouts.setdefault(team_id, {"league_prize": 0, "chairman": 0})
        row[key] = row.get(key, 0) + int(amount)

    # ---- haber akisi

    def _add_news(self, kind: NewsKind, text: str, team_id: int | None = None, other_team_id: int | None = None,
                  week: int | None = None, season: int | None = None) -> NewsItem:
        item = NewsItem(
            season=self.season if season is None else season,
            week=max(1, self.current_week if week is None else int(week)),
            kind=kind.value, text=text[:NEWS_TEXT_MAX], team_id=team_id, other_team_id=other_team_id,
        )
        self.db.add(item)
        return item

    def _news_big_result(self, fx: Fixture, result: MatchResult, week: int, cup: bool) -> None:
        """BIG_RESULT_MARGIN ve ustu gol farkli lig/kupa maci haber olur."""
        if abs(result.home_score - result.away_score) < BIG_RESULT_MARGIN:
            return
        home_won = result.home_score > result.away_score
        winner, loser = (result.home, result.away) if home_won else (result.away, result.home)
        high, low = max(result.home_score, result.away_score), min(result.home_score, result.away_score)
        where = CUP_SHORT_NAME if cup else "Lig"
        self._add_news(NewsKind.BIG_RESULT, f"Farklı galibiyet ({where}): {winner.name} {high}-{low} {loser.name}.",
                       team_id=winner.id, other_team_id=loser.id, week=week, season=fx.season)

    def world_news(self, limit: int = 30, team_id: int | None = None) -> list[NewsItem]:
        """Haber akisi, yeniden eskiye. team_id verilirse o kulubu (iki taraftan biri olarak) ilgilendirenler."""
        self.db.flush()
        stmt = select(NewsItem).order_by(NewsItem.id.desc()).limit(max(0, int(limit)))
        if team_id is not None:
            stmt = stmt.where(or_(NewsItem.team_id == team_id, NewsItem.other_team_id == team_id))
        return list(self.db.scalars(stmt))

    # ---- transfer gecmisi

    def record_transfers(self, limit: int = 10) -> list[TransferLog]:
        """Kariyerin en pahali transferleri (bonservis azalan; esitlikte once yapilan)."""
        self.db.flush()
        return list(self.db.scalars(
            select(TransferLog).order_by(TransferLog.fee.desc(), TransferLog.id).limit(max(0, int(limit)))
        ))

    def transfer_history(self, team: Team | None = None, season: int | None = None,
                         limit: int = 50) -> list[TransferLog]:
        """Transfer kaydi, yeniden eskiye. team: gelen ya da giden transferleri; season: yalnizca o sezon."""
        self.db.flush()
        stmt = select(TransferLog).order_by(TransferLog.id.desc()).limit(max(0, int(limit)))
        if team is not None:
            stmt = stmt.where(or_(TransferLog.from_team_id == team.id, TransferLog.to_team_id == team.id))
        if season is not None:
            stmt = stmt.where(TransferLog.season == season)
        return list(self.db.scalars(stmt))

    # ---- sezon arsivi: onurlar ve oduller

    def season_honours(self, season: int | None = None) -> list[SeasonHonour]:
        """Sezon arsivi (tum sezonlar ya da biri): sezon azalan, once ligler sonra kupa, yarisma adi."""
        self.db.flush()
        stmt = select(SeasonHonour)
        if season is not None:
            stmt = stmt.where(SeasonHonour.season == season)
        rows = list(self.db.scalars(stmt))
        return sorted(rows, key=lambda h: (-h.season, h.kind != HonourKind.LEAGUE.value, h.competition_name))

    def club_honours(self, team: Team) -> ClubHonours:
        """Kulubun sampiyonluklari (yarisma basina sayi + liste) ve ikincilikleri."""
        self.db.flush()
        rows = list(self.db.scalars(
            select(SeasonHonour)
            .where(or_(SeasonHonour.champion_team_id == team.id, SeasonHonour.runner_up_team_id == team.id))
            .order_by(SeasonHonour.season.desc(), SeasonHonour.id)
        ))
        won = [h for h in rows if h.champion_team_id == team.id]
        runner_ups = [h for h in rows if h.runner_up_team_id == team.id]
        titles: dict[str, int] = {}
        for h in won:
            titles[h.competition_name] = titles.get(h.competition_name, 0) + 1
        return ClubHonours(
            team_id=team.id, team_name=team.name, titles=titles,
            league_titles=sum(1 for h in won if h.kind == HonourKind.LEAGUE.value),
            cup_titles=sum(1 for h in won if h.kind == HonourKind.CUP.value),
            runner_up_finishes=len(runner_ups), honours=won, runner_ups=runner_ups,
        )

    def _honour_exists(self, season: int, kind: HonourKind, name: str) -> bool:
        return self.db.scalar(select(SeasonHonour.id).where(
            SeasonHonour.season == season, SeasonHonour.kind == kind.value, SeasonHonour.competition_name == name,
        )) is not None

    def _player_of_season(self, *conditions, min_apps: int) -> tuple | None:
        """(oyuncu id, ad, takim adi, ortalama not) -- en az min_apps mac, en yuksek ortalama; yoksa None."""
        apps = func.count(PlayerMatchStat.id)
        avg = avg_match_rating()
        return self.db.execute(
            select(Player.id, Player.name, Team.name, avg)
            .join(PlayerMatchStat, PlayerMatchStat.player_id == Player.id)
            .join(Team, Team.id == PlayerMatchStat.team_id)
            .join(Fixture, Fixture.id == PlayerMatchStat.fixture_id)
            .where(*conditions)
            .group_by(Player.id, Player.name, Team.id, Team.name)
            .having(apps >= max(1, int(min_apps)))
            .order_by(desc(avg), desc(apps), desc(func.sum(PlayerMatchStat.goals)), Player.name, Player.id)
            .limit(1)
        ).first()

    def _archive_finished_leagues(self, report: WeekReport | None,
                                  notes_by_team: dict[int, list[str]] | None = None) -> list[str]:
        """
        Bu sezon tum lig maclari oynanmis ve henuz arsivlenmemis her lig icin _archive_league (kariyer modu).
        play_week (lig bittigi hafta) ve start_new_season (tablolar sifirlanmadan once) cagirir; arsiv satiri
        (uq_season_honour) sayesinde odul TEK SEFER odenir. Kullaniciya donuk notlari dondurur.
        Faz 12: tum insan kuluplerinin notlari notes_by_team'e (verilirse); donus degeri odak kulubun notlari.
        """
        if self.game_mode is GameMode.TOURNAMENT:
            return []
        self.db.flush()
        season = self.season
        rows = self.db.execute(
            select(Fixture.league_id, func.max(Fixture.week),
                   func.count().filter(Fixture.status == FixtureStatus.UNPLAYED))
            .where(Fixture.season == season, Fixture.competition == Competition.LEAGUE)
            .group_by(Fixture.league_id)
            .order_by(Fixture.league_id)
        ).all()
        notes: list[str] = []
        for league_id, weeks, unplayed in rows:
            if unplayed:
                continue
            league = self.db.get(League, league_id)
            if league is None or not league.teams or self._honour_exists(season, HonourKind.LEAGUE, league.name):
                continue
            notes += self._archive_league(league, int(weeks or 0), report, notes_by_team)
        return notes

    def _archive_league(self, league: League, league_weeks: int, report: WeekReport | None,
                        notes_by_team: dict[int, list[str]] | None = None) -> list[str]:
        """
        Ligin sezon arsivi + siraya gore lig odulu (kurulmus kuluplere). Kullanici notlarini dondurur.
        Faz 12: tum kuluplerin sirasi season_standings'e yazilir; odul notlari her insan kulubunun rapor alanlarina
        ve notes_by_team'e; donus degeri odak kulubun notlari. season_honours.user_team_position birincil koltugun.
        """
        season = self.season
        week = report.week if report is not None else max(1, self.current_week - 1)
        table = self.standings(league.id)
        champion, runner = table[0], (table[1] if len(table) > 1 else None)
        scorer = next(iter(self.top_scorers(league.id, limit=1)), None)
        pots = self._player_of_season(
            Fixture.season == season, Fixture.league_id == league.id, Fixture.competition == Competition.LEAGUE,
            min_apps=math.ceil(POTS_MIN_APPEARANCE_SHARE * max(1, league_weeks)),
        )
        user_id = self.state.user_team_id
        user_position = next((i for i, t in enumerate(table, start=1) if t.id == user_id), None)
        self.db.add(SeasonHonour(
            season=season, kind=HonourKind.LEAGUE.value, league_id=league.id, competition_name=league.name,
            champion_team_id=champion.id, champion_name=champion.name,
            runner_up_team_id=runner.id if runner else None, runner_up_name=runner.name if runner else None,
            top_scorer_player_id=scorer.player.id if scorer else None,
            top_scorer_name=scorer.player.name if scorer else None,
            top_scorer_team=scorer.team.name if scorer else None,
            top_scorer_goals=scorer.goals if scorer else None,
            player_of_season_id=pots[0] if pots else None, player_of_season_name=pots[1] if pots else None,
            player_of_season_team=pots[2] if pots else None,
            player_of_season_rating=round(float(pots[3]), 2) if pots else None,
            user_team_position=user_position,
        ))
        self.db.flush()                                  # uq_season_honour: es zamanli ikinci arsiv burada durur
        # Faz 12: sezon sonu siralamasi (tum kulupler; menajer seviyesi / kulup teklifleri icin)
        self.db.add_all(
            SeasonStanding(season=season, league_id=league.id, team_id=team.id, team_name=team.name,
                           position=position, points=int(team.points))
            for position, team in enumerate(table, start=1)
        )

        money = finance.format_money
        humans = self.human_team_ids()
        team_notes: dict[int, list[str]] = {}
        strength = mean(t.reputation for t in table)
        for position, team in enumerate(table, start=1):
            if not self._economy_ready(team):
                continue
            prize = finance.league_prize(position, len(table), strength, league_weeks or None)
            if prize <= 0:
                continue
            team.transfer_budget += prize
            self._note_payout(team.id, "league_prize", prize)
            if team.id in humans:
                text = f"Lig ödülü ({league.name}, {position}. sıra): {money(prize)} kasaya eklendi."
                team_notes.setdefault(team.id, []).append(text)
                if report is not None:
                    sink = self._sink(report, team.id)
                    sink.prize_income += prize
                    sink.prize_notes.append(text)

        honour_text = f"{league.name} şampiyonu: {champion.name} ({champion.points} puan)."
        if report is not None:
            report.honours_notes.append(honour_text)
        if champion.id in humans:
            team_notes.setdefault(champion.id, []).insert(0, f"Tebrikler, {league.name} şampiyonu oldun!")
        runner_text = f", ikinci {runner.name}" if runner else ""
        self._add_news(NewsKind.LEAGUE_CHAMPION,
                       f"{champion.name} {season}. sezonun {league.name} şampiyonu ({champion.points} puan{runner_text}).",
                       team_id=champion.id, other_team_id=runner.id if runner else None, week=week, season=season)
        self.db.flush()
        if notes_by_team is not None:
            for team_id, notes in team_notes.items():
                notes_by_team.setdefault(team_id, []).extend(notes)
        return list(team_notes.get(self._report_focus(report), []))

    def _archive_cup(self, t: Tournament | None, report: WeekReport | None) -> SeasonHonour | None:
        """Biten Devler Arenasi'nin arsivi (final oynandiginda; en gec yeni sezon kurulmadan). Tek sefer."""
        if t is None or t.status is not TournamentStatus.FINISHED or t.champion_team_id is None:
            return None
        self.db.flush()
        if self._honour_exists(t.season, HonourKind.CUP, t.name):
            return None
        champion = self.db.get(Team, t.champion_team_id)
        final = next((tie for tie in t.ties if tie.stage == Stage.FINAL.value and tie.decided), None)
        runner_id = None
        if final is not None:
            runner_id = final.second_team_id if final.winner_team_id == final.first_team_id else final.first_team_id
        runner = self.db.get(Team, runner_id) if runner_id is not None else None
        scorer = next(iter(self.tournaments.top_players(t, "goals", 1)), None)
        pots = self._player_of_season(
            Fixture.tournament_id == t.id,
            min_apps=math.ceil(POTS_MIN_APPEARANCE_SHARE * max(1, len(t.calendar or []))),
        )
        honour = SeasonHonour(
            season=t.season, kind=HonourKind.CUP.value, league_id=None, competition_name=t.name,
            champion_team_id=champion.id, champion_name=champion.name,
            runner_up_team_id=runner.id if runner else None, runner_up_name=runner.name if runner else None,
            top_scorer_player_id=scorer.player.id if scorer else None,
            top_scorer_name=scorer.player.name if scorer else None,
            top_scorer_team=scorer.team.name if scorer else None,
            top_scorer_goals=scorer.goals if scorer else None,
            player_of_season_id=pots[0] if pots else None, player_of_season_name=pots[1] if pots else None,
            player_of_season_team=pots[2] if pots else None,
            player_of_season_rating=round(float(pots[3]), 2) if pots else None,
            user_team_position=None,
        )
        self.db.add(honour)
        self.db.flush()
        week = report.week if report is not None else max(1, self.current_week - 1)
        if report is not None:
            report.honours_notes.append(f"{t.name} şampiyonu: {champion.name}.")
        runner_text = f"; finalde {runner.name} ile karşılaştı" if runner else ""
        self._add_news(NewsKind.CUP_CHAMPION, f"{champion.name} {t.season}. sezonun {t.name} şampiyonu{runner_text}.",
                       team_id=champion.id, other_team_id=runner.id if runner else None, week=week, season=t.season)
        return honour

    def _award_cup_prize(self, stage: str, team_id: int, won: bool, report: WeekReport | None) -> int:
        """
        Devler Arenasi tur primi (tournament_manager kancasi: eslesme ya da grup asamasi bittiginde takim basina
        bir kez). Yalnizca kariyer modu ve kurulmus kulup. Odenen tutar. Faz 12: not her insan kulubunun raporuna.
        """
        if self.game_mode is GameMode.TOURNAMENT:
            return 0
        team = self.db.get(Team, team_id)
        if not self._economy_ready(team):
            return 0
        prize = finance.cup_round_prize(stage, won)
        if prize <= 0:
            return 0
        team.transfer_budget += prize
        if report is not None and team_id in self.human_team_ids():
            try:
                label = STAGE_LABELS[Stage(stage)]
            except ValueError:
                label = str(stage)
            if stage == Stage.FINAL.value:
                what = "şampiyonluk primi" if won else "finalist primi"
            else:
                what = f"{label} {'tur atlama' if won else 'katılım'} primi"
            text = f"{CUP_SHORT_NAME} {what}: {finance.format_money(prize)} kasaya eklendi."
            sink = self._sink(report, team_id)
            sink.prize_income += prize
            sink.prize_notes.append(text)
        return prize

    def _chairman_safety_net(self, notes_by_team: dict[int, list[str]] | None = None) -> list[str]:
        """
        Baskan guvencesi (sezon basi, kariyer modu): net degeri (kasa + tum oyuncularin piyasa degeri) liginin
        ortalamasinin finance.CHAIRMAN_FLOOR_SHARE payinin altindaki kurulmus kulube fark kasaya konur.
        Kullanicinin kulubu icin not ve haber. Faz 12: tum insan kulupleri (notes_by_team); donus odak kulubun.
        """
        self.db.flush()
        worths = dict(self.db.execute(
            select(Player.team_id, func.sum(Player.market_value))
            .where(Player.team_id.isnot(None)).group_by(Player.team_id)
        ).all())
        humans = self.human_team_ids()
        focus = self._acting_team_id()
        money = finance.format_money
        notes: list[str] = []
        for league in self.leagues():
            teams = list(league.teams)
            if len(teams) < 2:
                continue
            worth = {t.id: finance.club_net_worth(t.transfer_budget, int(worths.get(t.id) or 0)) for t in teams}
            average = mean(worth.values())
            for team in teams:
                if not self._economy_ready(team):
                    continue
                top_up = finance.chairman_top_up(worth[team.id], average)
                if top_up <= 0:
                    continue
                team.transfer_budget += top_up
                self._note_payout(team.id, "chairman", top_up)
                if team.id in humans:
                    text = (f"Başkan kulübe {money(top_up)} kaynak aktardı: kulübün net değeri ({money(worth[team.id])}) "
                            f"lig ortalamasının yarısının ({money(average * finance.CHAIRMAN_FLOOR_SHARE)}) altındaydı.")
                    if team.id == focus:
                        notes.append(text)
                    if notes_by_team is not None:
                        notes_by_team.setdefault(team.id, []).append(text)
                    self._add_news(NewsKind.CHAIRMAN, f"{team.name} başkanı kulübe {money(top_up)} kaynak aktardı.",
                                   team_id=team.id, week=1, season=self.season + 1)
        self.db.flush()
        return notes

    # ---- oyuncu kaygilari ve maas talepleri

    @staticmethod
    def _squad_expectations(squad: list[Player]) -> tuple[dict[int, float], bool]:
        """A takim: oyuncu id -> bugunku beklenti payi, kadro sisirilmis mi."""
        ranks = concerns.position_ranks((p.id, p.position, p.overall_rating) for p in squad)
        shares = {p.id: concerns.expected_share(p.squad_role, p.position, ranks[p.id]) for p in squad}
        return shares, concerns.squad_overloaded(shares.values())

    def _weekly_concerns(self, week: int, report: WeekReport) -> None:
        """
        Haftalik kaygi adimi (kariyer modu, TUM kulupler, yalnizca A takim): seviye hedefe bir kademe yaklasir
        (aktif oynayanda yukselmez), seviyenin moral etkisi yazilir, maas talebi dogar. Kullanicinin kulubunde
        talep bekler (respond_wage_demand; bekledigi her hafta -1 moral), AI kulubu hemen karar verir.
        Kullanicinin oyuncularinda kaygisi artanlar ve yeni talepler rapora yazilir.
        """
        if not self._concerns_active():
            return
        self.db.flush()
        humans = self.human_team_ids()
        teams = {t.id: t for t in self.teams()}
        squads: dict[int, list[Player]] = {}
        for p in self._players_in_order(select(Player.id).where(Player.team_id.isnot(None),
                                                                Player.in_academy.is_(False))
                                        .order_by(Player.team_id, Player.id)):
            squads.setdefault(p.team_id, []).append(p)
        money = finance.format_money
        for team_id, squad in squads.items():
            team = teams.get(team_id)
            if team is None:
                continue
            _shares, overloaded = self._squad_expectations(squad)
            mine = team_id in humans                     # Faz 12: her insan kulubunde talepler menajeri bekler
            sink = self._sink(report, team_id) if mine else None
            for p in squad:
                time = concerns.playing_time(p.minutes_window)
                old = concerns.level_of(p.concern_level)
                new = concerns.next_level(old, concerns.target_level(time, p.squad_role, overloaded), time.active)
                if new != old:
                    p.concern_level = int(new)
                morale = concerns.level_morale_effect(new, time.active, p.morale)
                if p.wage_demand is not None:
                    if mine:
                        morale += concerns.WAGE_PENDING_MORALE
                    else:
                        self._ai_resolve_wage_demand(team, p, int(p.wage_demand))
                elif p.loan_from_team_id is None:                  # kiralik oyuncunun maasini ana kulup de oder
                    demand = concerns.wage_demand_amount(p.overall_rating, p.contract_overall, p.current_wage,
                                                         team.reputation, p.squad_role)
                    if demand is not None and mine:
                        p.wage_demand = demand
                        sink.wage_demands.append(PlayerNote(
                            p.id, p.name, team.name,
                            f"yeni sözleşme istiyor: {money(demand)}/hafta (şu an {money(p.current_wage)}/hafta)",
                        ))
                    elif demand is not None:
                        self._ai_resolve_wage_demand(team, p, demand)
                if morale:
                    p.morale = clamp(p.morale + morale)
                if mine and new > old:
                    role_label = transfers.ROLE_LABELS[p.squad_role]
                    sink.concern_notes.append(PlayerNote(
                        p.id, p.name, team.name,
                        f"{concerns.LEVEL_LABELS[new]}: "
                        f"{concerns.reason_text(new, time, p.squad_role, role_label, overloaded)}",
                    ))
        self.db.flush()

    @staticmethod
    def _accept_wage_demand(p: Player, demand: int) -> None:
        p.current_wage = int(demand)
        p.contract_overall = p.overall_rating
        p.contract_years = max(p.contract_years, concerns.WAGE_ACCEPT_MIN_YEARS)
        p.wage_demand = None
        p.morale = clamp(p.morale + concerns.WAGE_ACCEPT_MORALE)

    @staticmethod
    def _refuse_wage_demand(p: Player) -> None:
        p.wage_demand = None
        p.contract_overall = p.overall_rating            # guc yeniden artana kadar yeni talep yok
        p.morale = clamp(p.morale + concerns.WAGE_REFUSE_MORALE)
        p.concern_level = int(min(concerns.ConcernLevel.ANGRY, concerns.level_of(p.concern_level) + 1))

    def _ai_resolve_wage_demand(self, team: Team, p: Player, demand: int) -> bool:
        """AI kulubu talebi karsilayabiliyorsa (gerekirse butce kaydirarak) kabul eder, aksi reddeder."""
        raise_by = int(demand) - p.current_wage
        if raise_by > team.free_wage:
            shift = finance.auto_shift_for_wage(team.transfer_budget, team.wage_budget, team.free_wage, raise_by)
            if shift > 0:
                try:
                    self.shift_budget(team, shift)
                except finance.BudgetError:
                    pass
        if raise_by <= team.free_wage:
            self._accept_wage_demand(p, demand)
            return True
        self._refuse_wage_demand(p)
        return False

    def respond_wage_demand(self, player: Player, accept: bool) -> str:
        """
        Kullanicinin oyuncusunun bekleyen maas talebine cevap. Kabul: maas talebe cikar (maas havuzunda yer yoksa
        ConcernError, hicbir sey degismez), sozlesme en az WAGE_ACCEPT_MIN_YEARS yil, moral artar. Red: moral
        duser ve kaygi bir kademe artar. Turkce sonuc mesaji dondurur.
        """
        team = self.user_team
        if team is None:
            raise ConcernError("Önce yöneteceğin takımı seç.")
        if player is None or player.team_id != team.id:
            raise ConcernError(f"{getattr(player, 'name', 'Oyuncu')} senin oyuncun değil.")
        if player.wage_demand is None:
            raise ConcernError(f"{player.name} yeni sözleşme talep etmiyor.")
        if player.loan_from_team_id is not None:
            raise ConcernError(f"{player.name} kiralık oyuncu; maaşı ana kulübüyle konuşulur.")
        money = finance.format_money
        demand = int(player.wage_demand)
        if accept:
            raise_by = demand - player.current_wage
            if raise_by > team.free_wage:
                raise ConcernError(
                    f"Maaş havuzunda yer yok: {player.name} için {money(raise_by)}/hafta ek alan gerekli, "
                    f"{money(max(0, team.free_wage))}/hafta boş. Önce bütçe kaydır."
                )
            self._accept_wage_demand(player, demand)
            self.db.flush()
            return (f"{player.name} yeni sözleşmeyi imzaladı: {money(demand)}/hafta, "
                    f"{player.contract_years} yıl.")
        self._refuse_wage_demand(player)
        self.db.flush()
        label = concerns.LEVEL_LABELS[concerns.level_of(player.concern_level)]
        return f"{player.name} maaş talebi reddedildi; morali düştü ve kaygısı arttı ({label})."

    def player_concerns(self, team: Team) -> list[concerns.ConcernRow]:
        """A takim oyunculari icin kaygi satirlari: seviye (azalan), bekleyen talep once, isim."""
        squad = self._senior_players(team)
        shares, overloaded = self._squad_expectations(squad)
        rows: list[concerns.ConcernRow] = []
        for p in squad:
            time = concerns.playing_time(p.minutes_window)
            level = concerns.level_of(p.concern_level)
            role_label = transfers.ROLE_LABELS[p.squad_role]
            rows.append(concerns.ConcernRow(
                player_id=p.id, name=p.name, position=p.position.value, role=p.squad_role.value,
                role_label=role_label, level=level.name, level_value=int(level),
                label=concerns.LEVEL_LABELS[level], wanted=time.wanted_matches, played=time.played_matches,
                active=time.active, overloaded=overloaded,
                reason=concerns.reason_text(level, time, p.squad_role, role_label, overloaded),
                wage_demand=int(p.wage_demand) if p.wage_demand is not None else None,
                current_wage=int(p.current_wage),
            ))
        rows.sort(key=lambda r: (-r.level_value, r.wage_demand is None, r.name))
        return rows

    # ---- izleme listesi

    def _shortlist_owner(self) -> Team:
        team = self.user_team
        if team is None:
            raise ShortlistError("İzleme listesi için önce yöneteceğin takımı seç.")
        return team

    def _shortlist_seat_id(self) -> int | None:
        """
        Faz 12: izleme listesinin sahibi. Birincil koltuk eski shortlist tablosunu kullanir (None); diger koltuklar
        manager_shortlist'i (koltuk id). Koltugu olmayan izleyici icin ShortlistError.
        """
        kind, seat_id = self._resolve_actor()
        if kind == "primary":
            return None
        if kind == "seat" and seat_id is not None:
            return seat_id
        raise ShortlistError("İzleme listesi için önce yöneteceğin takımı seç.")

    def _seat_shortlist_entry(self, seat_id: int, player_id: int) -> ManagerShortlistEntry | None:
        return self.db.scalar(select(ManagerShortlistEntry).where(
            ManagerShortlistEntry.manager_id == seat_id, ManagerShortlistEntry.player_id == player_id))

    def _shortlist_entry(self, player_id: int) -> ShortlistEntry | ManagerShortlistEntry | None:
        seat_id = self._shortlist_seat_id()
        if seat_id is None:
            return self.db.get(ShortlistEntry, player_id)
        return self._seat_shortlist_entry(seat_id, player_id)

    def shortlist_add(self, player: Player, note: str | None = None) -> ShortlistEntry | ManagerShortlistEntry:
        """
        Oyuncuyu izleme listesine ekler; zaten listedeyse notu gunceller (not verilmediyse dokunmaz).
        Kendi oyuncun, gecersiz oyuncu ya da 120 karakteri asan not -> ShortlistError.
        Faz 12: birincil olmayan koltugun listesi manager_shortlist'te (koltuga ozel).
        """
        team = self._shortlist_owner()
        if getattr(player, "id", None) is None or self.db.get(Player, player.id) is None:
            raise ShortlistError("Oyuncu bulunamadı.")
        if player.team_id == team.id:
            raise ShortlistError(f"{player.name} zaten senin oyuncun; izleme listesine eklenemez.")
        text_note = note.strip() if isinstance(note, str) else None
        if text_note and len(text_note) > 120:
            raise ShortlistError("Not en fazla 120 karakter olabilir.")
        seat_id = self._shortlist_seat_id()
        entry = self._shortlist_entry(player.id)
        if entry is None:
            if seat_id is None:
                entry = ShortlistEntry(player_id=player.id, added_season=self.season, added_week=self.current_week,
                                       note=text_note or None)
            else:
                entry = ManagerShortlistEntry(manager_id=seat_id, player_id=player.id, added_season=self.season,
                                              added_week=self.current_week, note=text_note or None)
            self.db.add(entry)
        elif note is not None:
            entry.note = text_note or None
        self.db.flush()
        return entry

    def shortlist_remove(self, player_id: int) -> bool:
        """Oyuncuyu listeden cikarir. Listede degilse (ya da koltugu olmayan izleyicide) False."""
        if self._resolve_actor()[0] == "none":
            return False
        entry = self._shortlist_entry(player_id)
        if entry is None:
            return False
        self.db.delete(entry)
        self.db.flush()
        return True

    def is_shortlisted(self, player_id: int) -> bool:
        self.db.flush()
        if self._resolve_actor()[0] == "none":
            return False
        return self._shortlist_entry(player_id) is not None

    def shortlist(self) -> list[ShortlistRow]:
        """Izleme listesi (eklenme sirasi): kulup, deger, kullanicinin kulubune istenen bonservis, transfer yasagi."""
        self.db.flush()
        if self._resolve_actor()[0] == "none":
            return []                                    # koltugu olmayan izleyicinin listesi yok
        user = self.user_team
        seat_id = self._shortlist_seat_id()
        if seat_id is None:
            entries = list(self.db.scalars(
                select(ShortlistEntry).order_by(ShortlistEntry.added_season, ShortlistEntry.added_week,
                                                ShortlistEntry.player_id)
            ))
        else:
            entries = list(self.db.scalars(
                select(ManagerShortlistEntry).where(ManagerShortlistEntry.manager_id == seat_id)
                .order_by(ManagerShortlistEntry.added_season, ManagerShortlistEntry.added_week,
                          ManagerShortlistEntry.player_id)
            ))
        rows: list[ShortlistRow] = []
        for entry in entries:
            p = entry.player
            club = p.team
            for_sale = club is not None and not p.in_academy and (user is None or club.id != user.id)
            reason = self.transfer_block_reason(p)
            rows.append(ShortlistRow(
                player=p, player_id=p.id, name=p.name, age=p.age, position=p.position.value,
                overall=p.overall_rating, team_id=club.id if club else None, team_name=club.name if club else None,
                market_value=int(p.market_value),
                asking_price=transfers.asking_price(p, club, user.reputation if user else None) if for_sale else None,
                in_academy=p.in_academy, transfer_banned=reason is not None, ban_reason=reason or "",
                note=entry.note, added_season=entry.added_season, added_week=entry.added_week,
            ))
        return rows

    # ---- hazirlik maclari

    def friendly_opponents(self, team: Team | None = None) -> list[Team]:
        """Hazirlik maci rakipleri: diger tum kulupler (itibar azalan, ad)."""
        team = team if team is not None else self.user_team
        if team is None:
            return []
        return sorted((t for t in self.teams() if t.id != team.id), key=lambda t: (-t.reputation, t.name))

    def friendlies(self, season: int | None = None, team_id: int | None = None) -> list[Friendly]:
        """
        Oynanmis hazirlik maclari, yeniden eskiye (season verilirse yalnizca o sezon). Faz 12: team_id verilirse
        yalnizca o kulubun (ev ya da deplasman) maclari.
        """
        self.db.flush()
        stmt = select(Friendly).order_by(Friendly.season.desc(), Friendly.week.desc(), Friendly.id.desc())
        if season is not None:
            stmt = stmt.where(Friendly.season == season)
        if team_id is not None:
            stmt = stmt.where(or_(Friendly.home_team_id == team_id, Friendly.away_team_id == team_id))
        return list(self.db.scalars(stmt))

    def _friendly_seed(self, season: int, week: int, home_id: int, away_id: int) -> int:
        base = self.seed if self.seed is not None else "free"
        return zlib.crc32(f"friendly|{base}|{season}|{week}|{home_id}|{away_id}".encode())

    def play_friendly(self, opponent_team: Team) -> FriendlyResult:
        """
        Kullanicinin kulubu (ev sahibi) ile hazirlik maci; haftada en fazla bir (play_week'ten once). Soccer Manager
        kurallari: sakatlik ve kart YOK, kondisyon dusmez, sakat/cezali oyuncular oynayabilir. Puan tablosu,
        istatistik, form, moral ve itibar etkilenmez; sonuc friendlies tablosuna yazilir, oynayanlar kaygi
        penceresine yarim agirlikli dakika alir. Hata -> FriendlyError (hicbir sey yazilmaz).
        Faz 12: oynatan koltugun kulubu; haftada tek mac KULUP basinadir (ev ya da deplasman): rakip bu hafta
        hazirlik maci oynadiysa da reddedilir. Insan kulubu rakip de kayitli taktigiyle oynar.
        """
        user = self.user_team
        if user is None:
            raise FriendlyError("Hazırlık maçı için önce yöneteceğin takımı seç.")
        if self.game_mode is GameMode.TOURNAMENT:
            raise FriendlyError("Hazırlık maçları yalnızca kariyer modunda oynanır.")
        if self.season_finished:
            raise FriendlyError("Sezon bitti; hazırlık maçı için yeni sezonu başlat.")
        opponent_id = getattr(opponent_team, "id", None)
        opponent = self.db.get(Team, opponent_id) if isinstance(opponent_id, int) else None
        if opponent is None:
            raise FriendlyError("Geçersiz rakip: kulüp bulunamadı.")
        if opponent.id == user.id:
            raise FriendlyError("Takımın kendisiyle hazırlık maçı yapamaz; başka bir rakip seç.")
        season, week = self.season, self.current_week
        self.db.flush()
        clubs = (user.id, opponent.id)
        this_week = list(self.db.scalars(
            select(Friendly)
            .where(Friendly.season == season, Friendly.week == week,
                   or_(Friendly.home_team_id.in_(clubs), Friendly.away_team_id.in_(clubs)))
            .order_by(Friendly.id)
        ))
        played = next((f for f in this_week if user.id in (f.home_team_id, f.away_team_id)), None)
        if played is not None:
            raise FriendlyError(
                f"Bu hafta zaten hazırlık maçı oynadın ({played.home_team_name} {played.home_score}-"
                f"{played.away_score} {played.away_team_name}). Haftada en fazla bir hazırlık maçı oynanır."
            )
        if this_week:
            raise FriendlyError(
                f"{opponent.name} bu hafta zaten bir hazırlık maçı oynadı; haftada en fazla bir hazırlık maçı "
                f"oynanır. Başka bir rakip seç."
            )

        def everyone_available(_player) -> None:
            return None

        config = replace(self._career_config() or EngineConfig(), base_injury=0.0, base_card=0.0)
        home = build_match_team(user, True, week, everyone_available)
        away = build_match_team(opponent, False, week, everyone_available)
        engine = MatchEngine(home, away, seed=self._friendly_seed(season, week, user.id, opponent.id),
                             config=config)
        result = self._apply_match_tactics(engine).simulate()          # 13. Asama: kayitli taktik
        goals = [
            {"minute": ev.minute, "added": ev.added_time, "team_id": ev.team_id, "team": ev.team,
             "player_id": ev.player_id, "player": ev.player, "text": ev.description}
            for ev in result.events if ev.type == EventType.GOAL
        ]
        friendly = Friendly(
            season=season, week=week, home_team_id=user.id, home_team_name=user.name,
            away_team_id=opponent.id, away_team_name=opponent.name,
            home_score=result.home_score, away_score=result.away_score, events=goals,
        )
        self.db.add(friendly)
        for orm_team, match_team in ((user, result.home), (opponent, result.away)):
            shares, _overloaded = self._squad_expectations(list(orm_team.players))
            for mp in match_team.players:
                if not mp.played:
                    continue
                p = self.db.get(Player, mp.id)
                if p is not None:
                    p.minutes_window = concerns.add_friendly_minutes(
                        p.minutes_window, mp.minutes_played, shares.get(p.id, 0.0))
        self.db.flush()
        return FriendlyResult(
            friendly_id=friendly.id, season=season, week=week, home_team_id=user.id, home_team_name=user.name,
            away_team_id=opponent.id, away_team_name=opponent.name, home_score=result.home_score,
            away_score=result.away_score, goals=goals, match=result,
        )

    # ------------------------------------------------------------------ kadro & taktik

    def lineup_of(self, team: Team) -> tuple[dict[int, Position], list[int], list[int]]:
        """(ilk 11: id -> rol, kulube id'leri, kadro disi id'leri)"""
        xi = {p.id: p.lineup_role for p in team.players
              if p.lineup_status is LineupStatus.XI and p.lineup_role is not None}
        bench = [p.id for p in team.players if p.lineup_status is LineupStatus.BENCH]
        out = [p.id for p in team.players if p.lineup_status is LineupStatus.OUT]
        return xi, bench, out

    def lineup_check(self, team: Team) -> LineupCheck:
        xi, bench, _ = self.lineup_of(team)
        return validate_lineup(team.players, team.formation, self.current_week, xi, bench)

    def set_formation(self, team: Team, name: str) -> LineupCheck:
        if name not in FORMATIONS:
            raise ValueError(f"Bilinmeyen diziliş: {name}. Seçenekler: {', '.join(FORMATIONS)}")
        team.formation = name
        self.db.flush()
        return self.lineup_check(team)

    def set_lineup(self, team: Team, xi: Mapping[int, Position], bench: Iterable[int]) -> LineupCheck:
        """Menajer karari. Hata varsa HICBIR sey degismez; sonuc dondurulur."""
        check = validate_lineup(team.players, team.formation, self.current_week, xi, bench)
        if check.ok:
            self._apply_lineup(team, xi, bench)
        return check

    def auto_lineup(self, team: Team) -> dict[int, Position]:
        """Asistan menajer: en yuksek efektif guce sahip uygun 11 + kulube."""
        week = self.current_week
        xi = pick_best_xi(team.players, team.formation, week)
        bench = pick_bench(team.players, xi, week)
        self._apply_lineup(team, xi, bench)
        return xi

    def clear_lineup(self, team: Team) -> None:
        self._apply_lineup(team, {}, [p.id for p in team.players])

    def _apply_lineup(self, team: Team, xi: Mapping[int, Position], bench: Iterable[int]) -> None:
        bench_ids = set(bench)
        for p in team.players:
            if p.id in xi:
                p.lineup_status, p.lineup_role = LineupStatus.XI, xi[p.id]
            elif p.id in bench_ids:
                p.lineup_status, p.lineup_role = LineupStatus.BENCH, None
            else:
                p.lineup_status, p.lineup_role = LineupStatus.OUT, None
        self.db.flush()

    # ------------------------------------------------------------------ kayitli taktik (13. Asama)

    def _senior_ids(self, team: Team) -> set[int]:
        """A takim oyuncu id'leri (akademi haric); veritabanindan taze okunur."""
        self.db.flush()
        return set(self.db.scalars(
            select(Player.id).where(Player.team_id == team.id, Player.in_academy.is_(False))))

    def _player_label(self, player_id: int) -> str:
        player = self.db.get(Player, player_id)
        return player.name if player is not None else f"#{player_id} numaralı oyuncu"

    @staticmethod
    def _roles_in_squad(roles: SetPieceRoles, squad_ids: set[int]) -> tuple[SetPieceRoles, list[tuple[str, int]]]:
        """Kadroda olmayan oyunculari rollerden cikarir: (temiz roller, [(rol alani, oyuncu id), ...])."""
        dropped = [(name, getattr(roles, name)) for name in ROLE_FIELDS
                   if getattr(roles, name) is not None and getattr(roles, name) not in squad_ids]
        if dropped:
            roles = replace(roles, **{name: None for name, _ in dropped})
        return roles, dropped

    @staticmethod
    def _read_plan(data) -> MatchPlan:
        """JSONB'den hosgorulu okuma: okunamayan kurallar atlanir, hic okunamazsa bos plan."""
        try:
            return MatchPlan.from_dict(data, strict=False)
        except (ValueError, TypeError):
            return MatchPlan()

    @staticmethod
    def _plan_in_squad(plan: MatchPlan, squad_ids: set[int]) -> tuple[MatchPlan, list[tuple[int, PlanRule, list[int]]]]:
        """Oyuncu degisikligindeki oyuncusu kadroda olmayan kurallari cikarir: (plan, [(sira, kural, id'ler)])."""
        kept: list[PlanRule] = []
        dropped: list[tuple[int, PlanRule, list[int]]] = []
        for index, rule in enumerate(plan.rules, start=1):
            missing = [pid for pid in (rule.action.sub_out_id, rule.action.sub_in_id)
                       if pid is not None and pid not in squad_ids]
            if missing:
                dropped.append((index, rule, missing))
            else:
                kept.append(rule)
        return (MatchPlan(tuple(kept)) if dropped else plan), dropped

    def team_instructions(self, team: Team) -> TeamInstructions:
        """Kulubun kayitli takim talimati; kayit yoksa (ya da bozuksa) varsayilan talimat."""
        return TeamInstructions.from_dict(team.tactic_instructions)

    def set_team_instructions(self, team: Team, instructions: TeamInstructions) -> None:
        if not isinstance(instructions, TeamInstructions):
            raise TacticsError("Takım talimatı okunamadı; kaydedilmedi.")
        team.tactic_instructions = instructions.to_dict()
        self.db.flush()

    def team_roles(self, team: Team) -> SetPieceRoles:
        """Kayitli kaptan ve duran top aticilari. A takim kadrosunda olmayan oyuncular dusurulur (yazilmaz)."""
        roles = SetPieceRoles.from_dict(team.set_piece_roles)
        if roles.is_default:
            return roles
        return self._roles_in_squad(roles, self._senior_ids(team))[0]

    def suggest_team_roles(self, team: Team) -> SetPieceRoles:
        """Asistan onerisi: A takimin bu hafta oynayabilen oyunculari arasindan (kimse yoksa tum A takim)."""
        seniors = self._senior_players(team)
        week = self.current_week
        available = [p for p in seniors if p.is_available(week)]
        return team_roles.suggest_roles(available or seniors)

    def set_team_roles(self, team: Team, roles: SetPieceRoles) -> None:
        """Rolleri kaydeder. Oyuncu kulubun A takim kadrosunda degilse TacticsError (hicbir sey yazilmaz)."""
        if not isinstance(roles, SetPieceRoles):
            raise TacticsError("Roller okunamadı; kaydedilmedi.")
        _, dropped = self._roles_in_squad(roles, self._senior_ids(team))
        if dropped:
            raise TacticsError(" ".join(
                f"{ROLE_LABELS[name]} olarak seçilen oyuncu ({self._player_label(pid)}) "
                f"{team.name} A takım kadrosunda değil." for name, pid in dropped))
        team.set_piece_roles = roles.to_dict()
        self.db.flush()

    def team_plan(self, team: Team) -> MatchPlan:
        """
        Kayitli oyun plani (hosgorulu okuma; kayit yoksa bos). Oyuncu degisikligindeki oyuncusu artik A takim
        kadrosunda olmayan kurallar dusurulur (yazilmaz).
        """
        plan = self._read_plan(team.match_plan)
        if plan.is_empty:
            return plan
        return self._plan_in_squad(plan, self._senior_ids(team))[0]

    def set_team_plan(self, team: Team, plan: MatchPlan) -> None:
        """Oyun planini kaydeder. Gecersiz kural ya da kadroda olmayan oyuncu -> TacticsError (yazilmaz)."""
        if not isinstance(plan, MatchPlan):
            raise TacticsError("Oyun planı okunamadı; kaydedilmedi.")
        errors = plan.errors() + plan.squad_errors(self._senior_ids(team))
        if errors:
            raise TacticsError("Oyun planı kaydedilemedi: " + " ".join(errors))
        team.match_plan = plan.to_dict()
        self.db.flush()

    # ---- kayitli taktikler (tactic_presets)

    def _preset_rows(self, team: Team) -> list[TacticPreset]:
        self.db.flush()
        return list(self.db.scalars(
            select(TacticPreset).where(TacticPreset.team_id == team.id)
            .order_by(func.lower(TacticPreset.name), TacticPreset.id)))

    @staticmethod
    def _snapshot_xi(lineup) -> list[tuple[int, Position]]:
        rows = lineup.get("xi") if isinstance(lineup, Mapping) else None
        out: list[tuple[int, Position]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            pid = row.get("player_id")
            try:
                role = Position(row.get("role"))
            except ValueError:
                continue
            if isinstance(pid, int) and not isinstance(pid, bool):
                out.append((pid, role))
        return out

    @staticmethod
    def _snapshot_ids(lineup, key: str) -> list[int]:
        rows = lineup.get(key) if isinstance(lineup, Mapping) else None
        return [pid for pid in (rows if isinstance(rows, list) else [])
                if isinstance(pid, int) and not isinstance(pid, bool)]

    def _preset_view(self, preset: TacticPreset) -> TacticPresetView:
        lineup = preset.lineup
        return TacticPresetView(
            id=preset.id, name=preset.name, formation=preset.formation,
            instructions=TeamInstructions.from_dict(preset.instructions),
            roles=SetPieceRoles.from_dict(preset.roles),
            plan=self._read_plan(preset.plan),
            lineup_size=len(self._snapshot_xi(lineup)) + len(self._snapshot_ids(lineup, "bench")),
            created_season=preset.created_season, created_week=preset.created_week,
        )

    def _owned_preset(self, team: Team, preset_id: int) -> TacticPreset:
        preset = (self.db.get(TacticPreset, preset_id)
                  if isinstance(preset_id, int) and not isinstance(preset_id, bool) else None)
        if preset is None:
            raise TacticsError("Kayıtlı taktik bulunamadı; silinmiş olabilir.")
        if preset.team_id != team.id:
            raise TacticsError(f"Bu kayıtlı taktik {team.name} kulübüne ait değil.")
        return preset

    def tactic_presets(self, team: Team) -> list[TacticPresetView]:
        """Kulubun kayitli taktikleri: ada gore (harf buyuklugunden bagimsiz), esitlikte kayit sirasiyla."""
        return [self._preset_view(p) for p in self._preset_rows(team)]

    def save_tactic_preset(self, team: Team, name: str, overwrite: bool = False) -> TacticPresetView:
        """
        Mevcut taktigi adiyla kaydeder: dizilis, kayitli talimat, roller ve oyun plani (kadroda olmayan
        oyuncular ayiklanmis) ve kadro (ilk 11 + kulube + kadro disi). Ad 1-40 karakter, kulup icinde
        harf buyuklugunden bagimsiz tekil; ayni ad varsa overwrite=True o kaydi gunceller (tarih de yenilenir).
        Kulup basina en fazla MAX_TACTIC_PRESETS. Ihlalde TacticsError (hicbir sey yazilmaz).
        """
        clean = name.strip() if isinstance(name, str) else ""
        if not clean:
            raise TacticsError("Taktik adı boş olamaz.")
        if len(clean) > TACTIC_PRESET_NAME_MAX:
            raise TacticsError(f"Taktik adı en fazla {TACTIC_PRESET_NAME_MAX} karakter olabilir "
                               f"({len(clean)} karakter yazıldı).")
        self.db.flush()
        # Esitlik PostgreSQL lower() ile: benzersiz indeksle ayni kural ("İ" / "i" Python'da farkli dusebilir)
        same = self.db.scalar(select(TacticPreset).where(
            TacticPreset.team_id == team.id, func.lower(TacticPreset.name) == func.lower(clean)))
        if same is not None and not overwrite:
            raise TacticsError(f"“{same.name}” adında kayıtlı bir taktik zaten var. Üzerine yazmayı onayla "
                               f"ya da başka bir ad seç.")
        if same is None:
            count = self.db.scalar(select(func.count()).select_from(TacticPreset)
                                   .where(TacticPreset.team_id == team.id)) or 0
            if count >= MAX_TACTIC_PRESETS:
                raise TacticsError(f"En fazla {MAX_TACTIC_PRESETS} taktik kaydedebilirsin; yeni taktik için "
                                   f"önce birini sil ya da mevcut bir taktiğin üzerine yaz.")

        xi, bench, out = self.lineup_of(team)
        snapshot = {
            "name": clean,
            "formation": team.formation if team.formation in FORMATIONS else "4-4-2",
            "instructions": self.team_instructions(team).to_dict(),
            "roles": self.team_roles(team).to_dict(),
            "plan": self.team_plan(team).to_dict(),
            "lineup": {
                "xi": [{"player_id": pid, "role": role.value}
                       for pid, role in sorted(xi.items(), key=lambda item: (ROLE_ORDER.index(item[1]), item[0]))],
                "bench": list(bench),
                "out": list(out),
            },
            "created_season": self.season,
            "created_week": self.current_week,
        }
        try:
            with self.db.begin_nested():             # es zamanli kayit: benzersiz indeks ihlali Turkce hata olur
                preset = same if same is not None else TacticPreset(team_id=team.id)
                for key, value in snapshot.items():
                    setattr(preset, key, value)
                if same is None:
                    self.db.add(preset)
                self.db.flush()
        except IntegrityError:
            raise TacticsError(f"“{clean}” adında kayıtlı bir taktik zaten var; sayfayı yenileyip tekrar dene.") from None
        return self._preset_view(preset)

    def delete_tactic_preset(self, team: Team, preset_id: int) -> None:
        preset = self._owned_preset(team, preset_id)
        self.db.delete(preset)
        self.db.flush()

    def apply_tactic_preset(self, team: Team, preset_id: int) -> list[str]:
        """
        Kayitli taktigi uygular ve Turkce notlar dondurur. Sira: dizilis, talimat, roller (A takimda olmayan
        oyuncu dusurulur), oyun plani (degisiklik oyuncusu kadrodan ayrilmis kural cikarilir), kadro.
        Kadro: kulupten ayrilan / akademideki / bu hafta oynayamayan (sakat, cezali) oyuncular atlanir; ilk 11'de
        bosalan yerleri asistan (ayni mevki, yoksa mevki disi en iyi oyuncu) doldurur; kayitli taktikte olmayan
        yeni oyuncular kadro disi kalir. Kadro yine de gecersizse asistan en iyi 11'i kurar. Ilk 11'i
        kaydedilmemis (asistan modu) taktikte kadro disi kararlari korunur, digerleri kulubededir.
        Baska kulubun taktigi -> TacticsError.
        """
        preset = self._owned_preset(team, preset_id)
        notes: list[str] = []
        if preset.formation in FORMATIONS:
            team.formation = preset.formation
        else:
            notes.append(f"Kayıtlı diziliş ({preset.formation}) geçersiz; mevcut diziliş ({team.formation}) korundu.")
        team.tactic_instructions = TeamInstructions.from_dict(preset.instructions).to_dict()

        squad_ids = self._senior_ids(team)
        roles, dropped_roles = self._roles_in_squad(SetPieceRoles.from_dict(preset.roles), squad_ids)
        for field_name, pid in dropped_roles:
            notes.append(f"{ROLE_LABELS[field_name]} ({self._player_label(pid)}) artık A takım kadrosunda değil; "
                         f"rol boş bırakıldı.")
        team.set_piece_roles = roles.to_dict()

        raw_rules = preset.plan.get("rules") if isinstance(preset.plan, Mapping) else None
        plan = self._read_plan(preset.plan)
        unreadable = len(raw_rules) - len(plan.rules) if isinstance(raw_rules, list) else 0
        if unreadable > 0:
            notes.append(f"Oyun planındaki {unreadable} kural okunamadı ve atlandı.")
        plan, dropped_rules = self._plan_in_squad(plan, squad_ids)
        for index, rule, missing in dropped_rules:
            label = f"{index}. kural" + (f" «{rule.name}»" if rule.name else "")
            who = ", ".join(self._player_label(pid) for pid in missing)
            notes.append(f"Oyun planındaki {label} çıkarıldı: {who} artık A takım kadrosunda değil.")
        team.match_plan = plan.to_dict()
        self.db.flush()

        self._apply_preset_lineup(team, preset.lineup, notes)
        return notes

    def _apply_preset_lineup(self, team: Team, lineup, notes: list[str]) -> None:
        week = self.current_week
        self._refresh_squads(team)
        seniors = {p.id: p for p in team.players}
        xi_raw = self._snapshot_xi(lineup)
        bench_raw = self._snapshot_ids(lineup, "bench")
        out_raw = set(self._snapshot_ids(lineup, "out"))

        if not xi_raw:
            # Asistan modu: ilk 11 kaydedilmemis. Kadro disi kararlari korunur, kalanlar kulubede.
            bench = [pid for pid in seniors if pid not in out_raw]
            self._apply_lineup(team, {}, bench)
            notes.append("Kayıtlı taktikte ilk 11 yok; maçta asistan en iyi 11'i kuracak.")
            return

        def usable(pid: int, where: str) -> bool:
            player = seniors.get(pid)
            if player is None:
                other = self.db.get(Player, pid)
                if other is None:
                    notes.append(f"#{pid} numaralı oyuncu artık kulüpte değil; {where} alınmadı.")
                elif other.team_id == team.id and other.in_academy:
                    notes.append(f"{other.name} akademide; {where} alınmadı.")
                else:
                    notes.append(f"{other.name} artık kulüpte değil; {where} alınmadı.")
                return False
            reason = player.unavailability_reason(week)
            if reason:
                notes.append(f"{player.name} {where} alınmadı: {reason}.")
                return False
            return True

        xi: dict[int, Position] = {}
        for pid, role in xi_raw:
            if pid not in xi and usable(pid, "ilk 11'e"):
                xi[pid] = role
        bench: list[int] = []
        for pid in bench_raw:
            if pid not in xi and pid not in bench and usable(pid, "kulübeye"):
                bench.append(pid)
        mentioned = {pid for pid, _ in xi_raw} | set(bench_raw) | out_raw

        # Dizilise gore fazla oyuncu (bozuk kayit) kulubeye; bosalan yerleri asistan doldurur
        needs = role_counts(team.formation)
        for role in ROLE_ORDER:
            extra = [pid for pid, r in xi.items() if r is role][needs[role]:]
            for pid in extra:
                del xi[pid]
                notes.append(f"{seniors[pid].name}: dizilişte {role.value} yeri kalmadı, kulübeye alındı.")
                bench.append(pid)
        # Aday sirasi: once mac kadrosu (kulube + yeni gelenler), sonra kadro disi birakilanlar
        pool = sorted((p for p in team.players if p.id not in xi and p.is_available(week)),
                      key=lambda p: (p.id in out_raw, -player_power(p), p.id))
        for role in ROLE_ORDER:
            missing = needs[role] - sum(1 for r in xi.values() if r is role)
            for _ in range(max(0, missing)):
                pick = (next((p for p in pool if p.id not in xi and p.position is role), None)
                        or next((p for p in pool if p.id not in xi
                                 and (p.position is not Position.GK or role is Position.GK)), None))
                if pick is None:
                    break
                xi[pick.id] = role
                if pick.id in bench:
                    bench.remove(pick.id)
                if pick.position is role:
                    notes.append(f"Asistan: {pick.name} {role.value} olarak ilk 11'e alındı.")
                else:
                    notes.append(f"Asistan: {pick.name} mevki dışı ({pick.position.value} → {role.value}) "
                                 f"ilk 11'e alındı.")
        for p in team.players:
            if p.id not in mentioned and p.id not in xi:
                notes.append(f"{p.name} kayıtlı taktikte yoktu; kadro dışı bırakıldı.")
        if len(bench) > MAX_BENCH:
            cut = bench[MAX_BENCH:]
            bench = bench[:MAX_BENCH]
            notes.append(f"Kulübe {MAX_BENCH} oyuncuyla sınırlı; "
                         f"{', '.join(seniors[pid].name for pid in cut)} kadro dışı bırakıldı.")

        check = validate_lineup(team.players, team.formation, week, xi, bench)
        if check.ok:
            self._apply_lineup(team, xi, bench)
        else:
            self.auto_lineup(team)
            notes.append(f"Kayıtlı kadro uygulanamadı ({check.errors[0]}); asistan en iyi 11'i kurdu.")

    # ---- mac entegrasyonu

    def _career_config(self, config: EngineConfig | None = None) -> EngineConfig | None:
        """Kariyer macinin motor ayari: verilen (yoksa kariyer) ayari; ai_tactics acik kariyerde AI talimatlari acik."""
        cfg = config if config is not None else self.engine_config
        if self.ai_tactics and (cfg is None or not cfg.ai_tactics):
            cfg = replace(cfg if cfg is not None else EngineConfig(), ai_tactics=True)
        return cfg

    def _prepare_career_fixture(self, fx: Fixture, week: int, config: EngineConfig | None = None,
                                **kwargs) -> MatchEngine:
        """
        Kariyer fiksturunun motoru (OYNATMAZ): lig ve kupa, otomatik ve canli ayni kurulum. kwargs
        (knockout, neutral_venue, unavailability) prepare_fixture'a aynen gecer.
        """
        _, engine = prepare_fixture(self.db, fx.id, seed=self.match_seed(fx), config=self._career_config(config),
                                    current_week=week, **kwargs)
        return self._apply_match_tactics(engine)

    def _apply_match_tactics(self, engine: MatchEngine) -> MatchEngine:
        """
        Duduk oncesi taktik (rastgele sayi cekmez). Kullanicinin kulubu: kayitli talimat, roller ve oyun plani;
        manager_controlled (AI talimati dokunmaz). AI kulupleri (ai_tactics acikken): ilk 11'den onerilen
        kaptan ve duran top aticilari; talimatlarini motor maç boyunca durum bazli yonetir.
        """
        humans = self.human_team_ids()                   # Faz 12: her insan kulubu kendi kayitli taktigiyle
        for side in (engine.home, engine.away):
            if side.id in humans:
                team = self.db.get(Team, side.id)
                side.manager_controlled = True
                engine.set_instructions(side, self.team_instructions(team))
                engine.set_roles(side, self._match_roles(team, side))
                engine.set_plan(side, self.team_plan(team))
            elif self.ai_tactics:
                engine.set_roles(side, team_roles.suggest_roles(side.on_pitch))
        return engine

    def _match_roles(self, team: Team, side: MatchTeam) -> SetPieceRoles:
        """
        Kullanicinin mac rolleri: kayitli roller; ai_tactics acikken belirlenmemis ya da mac kadrosunda
        olmayan (sakat, cezali, kadro disi) rolu asistan ilk 11'den tamamlar (kaptan yoksa yardimci kaptan gibi).
        """
        stored = self.team_roles(team)
        if not self.ai_tactics:
            return stored
        matchday = {p.id for p in side.players if p.matchday}
        suggested = team_roles.suggest_roles(side.on_pitch)
        return SetPieceRoles(**{
            name: (getattr(stored, name) if getattr(stored, name) in matchday else getattr(suggested, name))
            for name in ROLE_FIELDS
        })

    # ------------------------------------------------------------------ yeni sezon

    def start_new_season(self) -> int:
        """
        Sezon bittiyse: takim istatistikleri sifirlanir, fikstur yeniden uretilir,
        oyuncular (akademi dahil) bir yas alir, sakatlik/ceza/sari/not gecmisi temizlenir, kondisyon 100'e doner.
        Form ve moral tasinir (yeni sezona 'ruh hali' ile girilir). Piyasa degeri potansiyel primiyle.
        Kariyer modunda AI kulupleri akademilerini yonetir (_ai_manage_academy); kullanicinin kulubu icin
        yalnizca new_season_notes doldurulur (hicbir oyuncu otomatik tasinmaz).
        11. Asama (kariyer modu): biten sponsor sozlesmeleri sona erer, her kulube taze teklif gelir; AI imzalar
        ve tesis yatirimi yapar, kullanicinin teklifleri bekler (_season_club_economy, notlar new_season_notes'ta).
        12. Asama: tablolar sifirlanmadan ONCE biten sezon arsivlenir (lig onurlari ve odulleri, lig bittigi hafta
        yazilmadiysa; kupa onurlari) -- her yarisma icin tek sefer. Kariyer modunda sezon sonunda baskan guvencesi
        (_chairman_safety_net). Mutlak kariyer haftasi (career_week_offset) kesintisiz devam eder.
        Faz 12: eklentinin new_season_blocker nedeni -> SeasonNotFinished; on_season_end arsiv ve sifirlamalardan
        once, on_season_start yeni turnuva kurulduktan sonra. Tum insan kuluplerinin notlari
        new_season_notes_by_team'de (new_season_notes odak kulubun listesi).
        15A (bayrak, kariyer modu; transfer_desk.ContractCycle): yas / sozlesme dususunden ONCE AI'nin geciken
        yenileme kararlari; dususten sonra on sozlesmeler uygulanir, kadro guvencesi, suresi biten A takim
        oyunculari serbest kalir; akademi yonetiminden sonra AI kulupleri serbest oyuncu havuzundan kadrosunu
        tamamlar. Notlar (insan kulupleri) listelerin sonuna eklenir.
        15B (bayrak, kariyer modu): yas dususunden sonra ve 15A'nin serbest birakmasindan ONCE emeklilik
        (_retire_players): tohumlu zar, oyuncu satiri silinir, transfer_log RETIRED + haber + gelen kutusu.
        """
        if not self.season_finished:
            raise SeasonNotFinished("Sezon henüz bitmedi; oynanmamış maçlar var.")
        with self._seat_snapshot():
            new_season = self._start_new_season()
            self._record_inbox_season(new_season)          # 15D: sezon devri gelen kutusuna girer
            return new_season

    def _start_new_season(self) -> int:
        for extension in self._extensions():
            blocker = extension.new_season_blocker()
            if blocker:
                raise SeasonNotFinished(blocker)
        self.run_extensions("on_season_end")
        self._return_solo_loans()                  # 15F: tek oyunculu dunyada kiraliklar sezon sonunda biter

        st = self.state
        new_season = st.season + 1
        tournament_mode = self.game_mode is GameMode.TOURNAMENT
        previous_cup = self.tournaments.current()
        self.season_payouts = {}
        archive_notes: list[str] = []
        archive_by_team: dict[int, list[str]] = {}
        if not tournament_mode:
            archive_notes += self._archive_finished_leagues(None, archive_by_team)
        if previous_cup is not None and previous_cup.status is TournamentStatus.FINISHED:
            self._archive_cup(previous_cup, None)
        # Kupa katilimi: kariyerde biten sezonun lig siralamasi (sifirlamadan ONCE okunur)
        cup_tables = self.tournaments.qualification_tables(by_standings=not tournament_mode)

        for team in self.teams():
            team.reset_season_stats()

        cycle = None
        if not tournament_mode and self._contract_cycle_on():       # 15A: bayrak kapaliyken hic kurulmaz
            import transfer_desk
            cycle = transfer_desk.ContractCycle(self)
            cycle.before_rollover()

        for league in self.leagues():
            team_ids = [t.id for t in league.teams]
            self.rng.shuffle(team_ids)
            for week_index, pairs in enumerate(build_round_robin(team_ids), start=1):
                for home_id, away_id in pairs:
                    self.db.add(Fixture(
                        season=new_season, league_id=league.id,
                        home_team_id=home_id, away_team_id=away_id,
                        week=week_index, status=FixtureStatus.UNPLAYED,
                    ))

        for p in self.db.scalars(select(Player)):
            p.injured_until_week = 0
            p.suspended_matches = 0
            p.season_yellow_cards = 0
            p.cup_suspended_matches = 0
            p.cup_yellow_cards = 0
            p.weeks_since_match = 0
            p.match_rating_history = []
            p.condition = fitness.CONDITION_MAX          # sezon arasi tam dinlenme
            if tournament_mode:
                continue                                 # yeni turnuva: yas ve sozlesme ilerlemez
            p.age = min(45, p.age + 1)
            # Sozlesme bir yil erir, piyasa degeri yeni yasa gore guncellenir
            p.contract_years = max(0, p.contract_years - 1)
            p.market_value = finance.market_value(p.overall_rating, p.age, p.position, p.potential_rating)

        self.new_season_notes = []
        self.new_season_notes_by_team = {}
        if not tournament_mode:
            by_team = self.new_season_notes_by_team
            self.db.flush()
            # 15B: emeklilik serbest birakmadan ONCE (suresi biten veteran "serbest" degil "emekli" olur).
            # Notlar new_season_notes'a girer; gelen kutusu mesajini _retire_players kendisi yazar (15D
            # record_season notlari SOZLESME turuyle yazdigi icin ikinci kez eklenmez).
            retirement_notes = self._retire_players(new_season)
            contract_notes = cycle.after_decrement(new_season, by_team) if cycle is not None else []
            self.new_season_notes = self._season_academy_management(by_team)
            self.new_season_notes += retirement_notes
            if cycle is not None:
                contract_notes += cycle.preseason(new_season, by_team)
            # 11. Asama: sponsor sozlesmeleri/teklifleri ve AI tesis yatirimlari (sezon numarasi artmadan)
            self.new_season_notes += self._season_club_economy(new_season, by_team)
            # 12. Asama: lig odulu notlari (odul bu cagrida odendiyse) ve baskan guvencesi
            self.new_season_notes += archive_notes
            for team_id, notes in archive_by_team.items():
                by_team.setdefault(team_id, []).extend(notes)
            self.new_season_notes += self._chairman_safety_net(by_team)
            self.new_season_notes += contract_notes
            # 15C: yonetim kurulunun sezon karnesi. Notlari GELEN KUTUSUNA yazar (new_season_notes'a ve
            # news_items'a DOKUNMAZ): hafta / devir ozetleri bayrak acikken de degismez.
            if self._board_on():
                self._run_board_season(new_season)

        st.career_week_offset = int(st.career_week_offset or 0) + max(0, st.current_week - 1)
        st.season = new_season
        st.current_week = 1
        self.db.flush()
        fmt = self.tournaments.fmt(previous_cup) if previous_cup is not None else None
        self.tournaments.create(new_season, cup_tables, fmt)
        self.db.flush()
        self.run_extensions("on_season_start", new_season)
        return new_season
