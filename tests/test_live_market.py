"""
15F canli pazar ve kiralik testleri: saf kurallar (transfer_rules bolum 12, loan_rules 15F) + gercek PostgreSQL
uzerinde dunya pazari, tek oyunculu kiralik, opsiyonlu kiralik, geri alim maddesi, kara dayali sonraki satis payi
ve market_hub geri alinca pay iadesi.

Bayrak: transfer_rules.LIVE_MARKET. 15F bayrak acma commit'inden beri varsayilan ACIKTIR; dunya adimlarini
sinayan testler yine de bayragi acikca kurar (monkeypatch) ki varsayilan degisse de anlamlarini korusunlar.
Bayrak KAPALIYKEN hicbir 15F dunya adimi calismaz (parite) -- bunu test_flag_off_keeps_the_old_ai_window korur.

Her DB testi kendi islemini geri alir. Onerilen: TEST_DB_NAME=fm_db_test_15f.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database  # noqa: E402
import loan_rules  # noqa: E402
import market_hub  # noqa: E402
import transfer_desk  # noqa: E402
import transfer_rules as rules  # noqa: E402
import transfers  # noqa: E402
from career_manager import CareerManager, WeekReport  # noqa: E402
from models import GameMode, Loan, LoanStatus, Player, Position, Team, TransferDeal  # noqa: E402
from transfer_desk import DeskError, LoanCycle, MarketDesk, TransferDesk, WorldMarket  # noqa: E402
from transfer_rules import DealTerms  # noqa: E402

USER = "Istanbul Lions"
OTHER = "Karadeniz Storm"


def _db_available() -> bool:
    try:
        return database.wait_for_db(retries=1, delay=0.5, verbose=False)
    except Exception:
        return False

DB = pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor")


@pytest.fixture
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def live(monkeypatch):
    """15F dunya bayragini bu test icin acar (varsayilan kapali)."""
    monkeypatch.setattr(rules, "LIVE_MARKET", True)
    return True


def _manager(db, team: str = USER) -> CareerManager:
    cm = CareerManager(db, seed=7)
    if cm.season_finished or cm.current_week != 1:
        pytest.skip("Test dünyası sezon başında değil; test veritabanı yeniden kurulmalı")
    cm.set_user_team(cm.find_team(team))
    return cm


def _fringe(team: Team) -> Player:
    """Kadronun ilk 11 disindaki (kiralik icin uygun) bir oyuncusu; kaleci mevki tabanina takilmasin diye haric."""
    seniors = [p for p in team.players if not p.in_academy and p.loan_from_team_id is None]
    if len(seniors) < 13:
        pytest.skip("Kadro kiralık testi için küçük")
    outfield = [p for p in seniors[11:] if p.position is not Position.GK]
    if not outfield:
        pytest.skip("İlk 11 dışında saha oyuncusu yok")
    return outfield[0]                      # ilk 11'in hemen ardindaki oyuncu (kiralik icin en olasi aday)


def _borrower_for(cm: CareerManager, player: Player) -> tuple[Team, int]:
    """Oyuncuyu kiralamayi KABUL EDECEK bir AI kulubu ve maas payi (loan_rules.ai_accepts_loan_in)."""
    humans = cm.human_team_ids()
    clubs = sorted((t for t in cm.teams() if t.id not in humans), key=lambda t: t.id)
    for share in loan_rules.AI_LOAN_OFFER_SHARES:
        for club in clubs:
            ratings = [p.overall_rating for p in club.players if p.position is player.position]
            ok, _reason = loan_rules.ai_accepts_loan_in(int(player.overall_rating),
                                                        sum(ratings) / len(ratings) if ratings else None,
                                                        int(club.free_wage), int(player.current_wage or 0), share)
            if ok:
                return club, share
    pytest.skip("Bu oyuncuyu kiralayacak AI kulübü yok")


def _grow_squad(cm: CareerManager, club: Team, size: int) -> Team:
    """AI kulubunun A takimini akademiden buyutur (kiralik tabani testleri icin)."""
    seniors = [p for p in club.players if not p.in_academy]
    for young in list(club.academy_players):
        if len(seniors) >= size:
            break
        young.in_academy = False
        seniors.append(young)
    cm.db.flush()
    cm.db.expire(club, ["players", "academy_players"])
    if len([p for p in club.players if not p.in_academy]) < size:
        pytest.skip("Test dünyasında kadro büyütülemedi")
    return club


def _big_ai_club(cm: CareerManager) -> Team:
    """Kiraliga verebilecek kadar kalabalik bir AI kulubu (loan_rules.AI_LOAN_OUT_MIN_SQUAD)."""
    humans = cm.human_team_ids()
    clubs = [t for t in cm.teams() if t.id not in humans]
    club = max(clubs, key=lambda t: (len([p for p in t.players if not p.in_academy]), -t.id))
    return _grow_squad(cm, club, loan_rules.AI_LOAN_OUT_MIN_SQUAD + 2)


# ===========================================================================
# 1) SAF KURALLAR: DONEM KOTASI VE ALICI AGIRLIGI
# ===========================================================================


def test_flag_is_on_by_default_after_the_15f_rebaseline():
    # 15F bayrak acma commit'i: canli pazar artik varsayilan davranis.
    # Bayrak kapali davranisin bozulmadigi ayrica dogrulanir:
    # kanit/15F_betikler/flag_off.py eklentisiyle eski HEAD_PARITY ozetleri gecer.
    assert rules.LIVE_MARKET is True


def test_window_span_covers_only_the_open_window():
    assert rules.window_span(1, 38) == (1, 5)
    assert rules.window_span(5, 38) == (1, 5)
    assert rules.window_span(6, 38) is None
    assert rules.window_span(20, 38) == (20, 23)
    assert rules.window_span(24, 38) is None


def test_window_index_marks_the_last_week_as_deadline_day():
    assert rules.window_index(1, (1, 5)) == (1, 5, False)
    assert rules.window_index(5, (1, 5)) == (5, 5, True)
    # sezon arasi: devir yapilana kadar her hafta son gun sayilir
    assert rules.window_index(38, (1, 5), season_finished=True) == (1, 1, True)


def test_window_deal_target_lands_in_the_acceptance_band_for_the_open_world():
    assert 40 <= rules.window_deal_target(114) <= 120
    assert rules.window_deal_target(2) == rules.WINDOW_DEALS_MIN
    assert rules.window_deal_target(10_000) == rules.WINDOW_DEALS_MAX


def test_weekly_quota_boosts_the_deadline_week_but_never_passes_the_target():
    target, length = 70, 5
    quotas = [rules.weekly_quota(target, i, length, i == length) for i in range(1, length + 1)]
    assert quotas[-1] > quotas[0]                       # son gun gercekten yogun
    assert sum(quotas) == target                        # toplam hedefe BIREBIR esit
    assert rules.weekly_quota(target, 1, 1, True) == target      # tek haftalik donem
    assert rules.weekly_quota(0, 1, 1, True) == 0


def test_buyer_weight_grows_with_the_club_size_and_weighted_pick_is_seeded():
    cash = 500_000_000                                  # ikisinin de kasasi dolu: fark KULUP BUYUKLUGUNDEN
    small = rules.market_buyer_weight(cash, 1_000_000, 70)
    big = rules.market_buyer_weight(cash, 5_000_000, 70)
    assert big > small * 10                             # buyuk kulup baskin
    # kasasi biten kulup pazardan cekilir (agirlik oransal duser)
    assert rules.market_buyer_weight(0, 5_000_000, 70) == rules.MARKET_MIN_WEIGHT
    assert rules.market_buyer_weight(rules.MARKET_CASH_REFERENCE // 2, 5_000_000, 70) < big
    items = ["a", "b", "c"]
    weights = [0.0, 0.0, 5.0]
    assert rules.weighted_pick(random.Random(1), items, weights) == "c"     # sifir agirlik hic secilmez
    first = [rules.weighted_pick(random.Random(s), items, [1.0, 2.0, 3.0]) for s in range(20)]
    second = [rules.weighted_pick(random.Random(s), items, [1.0, 2.0, 3.0]) for s in range(20)]
    assert first == second                              # tohumlu: ayni cekilis ayni sonuc
    assert rules.weighted_pick(random.Random(1), [], []) is None
    assert rules.weighted_pick(random.Random(1), items, [0.0, 0.0, 0.0]) is None


def test_squad_gate_protects_the_floor_and_the_cap():
    assert rules.market_squad_gate(25, 25) is None
    assert rules.market_squad_gate(rules.MARKET_SQUAD_FLOOR, 25) is not None       # satici tabana iner
    assert rules.market_squad_gate(25, rules.MARKET_SQUAD_CAP) is not None         # alici tavani asar


def test_wealth_and_slots_scale_with_the_wage_budget():
    assert rules.market_wealth(0) == 0.0
    assert rules.market_wealth(rules.MARKET_WAGE_REFERENCE) == 1.0
    assert rules.market_wealth(10 * rules.MARKET_WAGE_REFERENCE) == rules.MARKET_WEALTH_CAP
    assert rules.market_max_in(0) == rules.MARKET_MAX_IN_PER_WINDOW
    assert rules.market_max_in(1.0) == rules.MARKET_MAX_IN_PER_WINDOW
    assert rules.market_max_in(rules.MARKET_WEALTH_CAP) > rules.MARKET_MAX_IN_PER_WINDOW


def test_market_score_rewards_depth_and_the_star_appetite_of_big_clubs():
    common = dict(player_age=25, best_at_position=85, depth_at_position=70, shortfall=0.0)
    # ilk adamdan zayif ama IKINCI adamdan iyi oyuncu: transfers.target_score 0 verirdi, 15F derinligi sayar
    assert rules.market_score(player_overall=80, wealth=0.0, **common) > 0
    poor = rules.market_score(player_overall=88, wealth=0.2, **common)
    rich = rules.market_score(player_overall=88, wealth=rules.MARKET_WEALTH_CAP, **common)
    assert rich > poor                                  # yildiz istahi kulup buyuklugüyle buyur
    # hicbir yonden guclendirmiyorsa ilgi YOK
    assert rules.market_score(player_overall=60, wealth=rules.MARKET_WEALTH_CAP, **common) == 0.0


def test_big_club_keeps_buying_stars_after_its_squad_is_patched():
    """Kadrosunu yamamis buyuk kulup ocakta pazardan CEKILMEMELI (CM: en iyi kulup de alir)."""
    # 83 güç: ne ilk adamı (88) ne ikinci adamı (84) geçiyor -> "ihtiyacım yok" kapısı
    patched = dict(player_age=27, best_at_position=88, depth_at_position=84, shortfall=0.0,
                   wealth=rules.MARKET_WEALTH_CAP)
    assert rules.market_score(player_overall=83, elite=True, **patched) > 0    # elit kulüp yıldız alır
    # ELİT OLMAYAN kulüp almaz (yoksa orta sınıf da yıldız kovalar, büyük kulüplerin payı seyrelir)
    assert rules.market_score(player_overall=83, elite=False, **patched) == 0.0
    # yıldız olmayan oyuncuya ihtiyaç yokken elit kulüp de ilgilenmez
    assert rules.market_score(player_overall=70, elite=True, **patched) == 0.0
    # ikinci adamından MARKET_STAR_TOLERANCE'tan fazla zayıf yıldıza da ilgi YOK
    weak = 84 - rules.MARKET_STAR_TOLERANCE - 1
    assert rules.market_score(player_overall=weak, elite=True, **patched) == 0.0
    # DERİNLİK artışında (86 > ikinci adam) herkes ilgilenir, kapı yalnızca "ihtiyacım yok" halinde çalışır
    assert rules.market_score(player_overall=86, elite=False, **patched) > 0


def test_elite_threshold_is_a_multiple_of_the_world_median_not_a_constant():
    """Eşik dünyaya gömülü olmamalı: sentetik / küçük / tek ligli dünyada medyan bambaşka olur."""
    small = [1_000_000] * 5 + [9_000_000]
    assert rules.elite_wage_floor(small) == 1_000_000 * rules.MARKET_ELITE_MEDIANS
    # aynı şekil, on kat büyük dünya -> eşik de on kat (kural dünyadan bağımsız)
    assert rules.elite_wage_floor([v * 10 for v in small]) == 10 * rules.elite_wage_floor(small)
    assert rules.elite_wage_floor([]) == float("inf")          # kulüp yoksa kimse elit değil
    assert rules.elite_wage_floor([2_000_000, 4_000_000]) == 3_000_000 * rules.MARKET_ELITE_MEDIANS


def test_spend_cap_keeps_a_reserve_so_the_till_never_goes_negative():
    summer = rules.market_spend_cap(10_000_000)
    winter = rules.market_spend_cap(10_000_000, winter=True)
    assert 0 < summer < winter < 10_000_000      # yazin ocak icin para saklanir
    assert rules.market_spend_cap(0) == 0
    assert rules.market_spend_cap(-5) == 0


def test_window_allowance_really_reserves_money_for_january():
    opening = 500_000_000
    summer = rules.market_window_allowance(opening, 0)
    assert summer == int(opening * (1 - rules.MARKET_SUMMER_RESERVE))
    # yaz payi tukendiginde daha fazla harcanmaz (kasada para kalsa bile)
    assert rules.market_window_allowance(opening - summer, summer) == 0
    # ocakta kalan kasanin cogu harcanabilir
    winter = rules.market_window_allowance(opening - summer, 0, winter=True)
    assert winter > 0 and winter == int((opening - summer) * (1 - rules.MARKET_BUDGET_RESERVE))
    assert rules.market_window_allowance(0, 0) == 0


def test_winter_window_is_quieter_than_summer_but_stays_in_the_band():
    summer = rules.window_deal_target(114)
    winter = rules.window_deal_target(114, winter=True)
    assert winter < summer
    assert 40 <= winter <= 120 and 40 <= summer <= 120


# ===========================================================================
# 2) SAF KURALLAR: SONRAKI SATIS PAYI (KAR TABANI), GERI ALIM, OPSIYONLU KIRALIK
# ===========================================================================


def test_sell_on_amount_gross_and_profit_basis():
    assert rules.sell_on_amount(20, 10_000_000) == 2_000_000
    # kar tabani: yalnizca alis bedelini asan kisim paylasilir
    assert rules.sell_on_amount(20, 10_000_000, profit_basis=True, original_fee=4_000_000) == 1_200_000
    # zararina satista pay YOK
    assert rules.sell_on_amount(20, 3_000_000, profit_basis=True, original_fee=4_000_000) == 0
    assert rules.sell_on_amount(0, 10_000_000) == 0


def test_profit_based_sell_on_is_worth_less_than_gross_and_buy_back_adds_value():
    ctx = rules.ValuationContext(resale_value=20_000_000)
    base = DealTerms(fee=10_000_000, upfront=10_000_000)
    gross = rules.package_value(rules.normalize_terms(DealTerms(fee=10_000_000, upfront=10_000_000,
                                                                sell_on_pct=20)), ctx)
    profit = rules.package_value(rules.normalize_terms(DealTerms(fee=10_000_000, upfront=10_000_000,
                                                                 sell_on_pct=20, sell_on_profit=True)), ctx)
    plain = rules.package_value(base, ctx)
    assert plain < profit < gross
    back = rules.package_value(rules.normalize_terms(
        DealTerms(fee=10_000_000, upfront=10_000_000, buy_back_fee=12_000_000, buy_back_seasons=2)), ctx)
    assert back > plain


def test_terms_round_trip_and_validation_of_the_new_clauses():
    terms = rules.normalize_terms(DealTerms(fee=8_000_000, upfront=8_000_000, sell_on_pct=15,
                                            sell_on_profit=True, buy_back_fee=12_000_000, buy_back_seasons=2))
    again = DealTerms.from_dict(terms.to_dict())
    assert again == terms
    assert "geri alım" in terms.describe() and "kârından" in terms.describe()
    assert rules.validate_terms(terms) == []
    # bedelden dusuk geri alim, gecersiz sure ve paysiz kar tabani yakalanir
    bad = DealTerms(fee=8_000_000, upfront=8_000_000, buy_back_fee=1_000_000, buy_back_seasons=9)
    problems = rules.validate_terms(bad)
    assert any("Geri alım bedeli" in p for p in problems)
    assert any("sezon geçerli" in p for p in problems)
    # normalize: pay yoksa kar tabani duser, bedel yoksa sure duser
    cleaned = rules.normalize_terms(DealTerms(fee=1, upfront=1, sell_on_profit=True, buy_back_seasons=3))
    assert cleaned.sell_on_profit is False and cleaned.buy_back_seasons == 0
    assert rules.normalize_terms(DealTerms(fee=1, upfront=1, buy_back_fee=5)).buy_back_seasons == 1


def test_buy_back_window_and_trigger():
    assert rules.buy_back_open(3, 3, 2) and rules.buy_back_open(3, 4, 2)
    assert not rules.buy_back_open(3, 5, 2)
    assert not rules.buy_back_open(3, 3, 0)
    assert rules.buy_back_attractive(10_000_000, 20_000_000)
    assert not rules.buy_back_attractive(10_000_000, 11_000_000)
    assert not rules.buy_back_attractive(0, 20_000_000)


def test_option_fee_and_exercise_rules():
    optional = loan_rules.option_fee_for(10_000_000)
    mandatory = loan_rules.option_fee_for(10_000_000, mandatory=True)
    assert mandatory < optional                        # yukumluluk daha ucuza alinir
    assert loan_rules.ai_exercises_option(20_000_000, optional, 50_000_000, False)[0]
    assert not loan_rules.ai_exercises_option(10_000_000, optional, 50_000_000, False)[0]
    assert loan_rules.ai_exercises_option(1, mandatory, 50_000_000, True)[0]          # zorunlu: deger onemsiz
    assert not loan_rules.ai_exercises_option(99_000_000, optional, 1_000, False)[0]  # kasa yetmez
    assert not loan_rules.ai_exercises_option(20_000_000, 0, 50_000_000, True)[0]


def test_ai_loan_offer_gates_and_share_ladder():
    rng = random.Random(3)
    common = dict(player_age=22, parent_squad_size=24, borrower_position_avg=60.0,
                  borrower_free_wage=500_000, wage=20_000, market_value=4_000_000)
    assert loan_rules.ai_loan_offer(rng, player_overall=70, player_rank=5, **common) is None     # ilk 11
    assert loan_rules.ai_loan_offer(random.Random(3), player_overall=40, player_rank=15,
                                    **common) is None                                            # guc esigi
    assert loan_rules.ai_loan_offer(random.Random(3), player_overall=70, player_rank=15,
                                    **{**common, "player_age": 34}) is None                      # yas esigi
    assert loan_rules.ai_loan_offer(random.Random(3), player_overall=70, player_rank=15,
                                    **{**common, "parent_squad_size": 16}) is None               # kadro tabani
    offer = loan_rules.ai_loan_offer(random.Random(3), player_overall=70, player_rank=15, **common)
    assert offer is not None and offer.share in loan_rules.AI_LOAN_OFFER_SHARES
    assert offer.weeks in loan_rules.AI_LOAN_OFFER_WEEKS
    # bos maas alani sifirsa AI ancak %0 pay ile alir -> teklif cikmaz
    assert loan_rules.ai_loan_offer(random.Random(3), player_overall=70, player_rank=15,
                                    **{**common, "borrower_free_wage": 0}) is None


def test_loans_enabled_follows_the_world_kind():
    from world_rules import WorldRules

    assert market_hub.loans_enabled(WorldRules.legacy()) is bool(loan_rules.SOLO_LOANS)
    assert market_hub.loans_enabled(WorldRules(shared=True, loans=False)) is False
    assert market_hub.loans_enabled(WorldRules(shared=True, loans=True)) is True


# ===========================================================================
# 3) DUNYA PAZARI (bayrak)
# ===========================================================================


@DB
def test_world_market_runs_only_inside_a_window(db, live):
    cm = _manager(db)
    market = WorldMarket(cm)
    season_weeks = int(cm._projected_season_weeks() or 1)
    closed = next((w for w in range(1, season_weeks + 1)
                   if rules.window_span(w, season_weeks, False) is None), None)
    assert closed is not None
    assert market.run_week(closed) == []                 # donem disinda tek transfer bile yok


@DB
def test_flag_off_keeps_the_old_ai_window(db, monkeypatch):
    cm = _manager(db)
    monkeypatch.setattr(rules, "LIVE_MARKET", False)
    calls: list[str] = []
    monkeypatch.setattr(cm, "_ai_transfer_deals", lambda: calls.append("old") or [])
    cm.run_ai_transfer_window()
    assert calls == ["old"]
    assert cm._live_market_on() is False


@DB
def test_window_opening_lists_surplus_and_young_ai_players(db, live):
    cm = _manager(db)
    market = WorldMarket(cm)
    clubs = market._ai_clubs()
    for team in clubs:                                   # temiz baslangic
        for p in team.players:
            p.transfer_listed = p.loan_listed = False
    db.flush()
    market._open_window(clubs, random.Random(1))
    listed = [p for t in clubs for p in t.players if p.transfer_listed]
    loanable = [p for t in clubs for p in t.players if p.loan_listed]
    assert loanable, "kiralık listesi boş kaldı"
    assert all(int(p.age) <= rules.MARKET_LIST_LOAN_AGE for p in loanable)
    assert all(not p.transfer_listed for p in loanable)  # bir oyuncu iki listede birden olmaz
    assert all(p.team_id not in cm.human_team_ids() for p in listed + loanable)


@DB
def test_window_opening_fills_thin_ai_squads_from_the_academy(db, live):
    """15A/15B kadroları eritiyor; kadrosu tabana dayanan kulüp satamaz ve pazar satıcısız kalır."""
    cm = _manager(db)
    market = WorldMarket(cm)
    club = next(t for t in market._ai_clubs() if t.academy_players)
    before_total = len([p for p in club.players if not p.in_academy]) + len(club.academy_players)
    seniors = len([p for p in club.players if not p.in_academy])
    academy = len(club.academy_players)
    market._fill_squad(club)
    db.flush()
    db.expire(club, ["players", "academy_players"])
    after = len([p for p in club.players if not p.in_academy])
    # hedef TABANIN ÜSTÜ (tam tabandaki kulüp satamaz); dönem başına en fazla MARKET_FILL_PER_WINDOW
    target = rules.MARKET_SQUAD_FLOOR + rules.MARKET_FILL_HEADROOM
    assert target > rules.MARKET_SQUAD_FLOOR      # 20'ye tamamlamak satıcısız bir dünya bırakır
    assert after == seniors + min(rules.MARKET_FILL_PER_WINDOW, target - seniors, academy)
    assert after <= target
    # oyuncu YARATILMAZ: toplam (A takım + akademi) değişmez
    assert len([p for p in club.players if not p.in_academy]) + len(club.academy_players) == before_total
    # tabanın üstündeki kulüpte hiçbir şey yapmaz
    full = next((t for t in market._ai_clubs()
                 if len([p for p in t.players if not p.in_academy]) >= target), None)
    if full is not None:
        count = len([p for p in full.players if not p.in_academy])
        market._fill_squad(full)
        db.flush()
        assert len([p for p in full.players if not p.in_academy]) == count


@DB
def test_market_desk_listings_are_fogged_for_unscouted_players(db, live):
    cm = _manager(db)
    desk = MarketDesk(cm)
    market = WorldMarket(cm)
    market._open_window(market._ai_clubs(), random.Random(2))
    rows = desk.listings(kind="LOAN", limit=30)
    assert rows, "kiralık listesi boş"
    unknown = [r for r in rows if r.knowledge < rules.KNOWN_THRESHOLD]
    for row in unknown:
        assert row.overall_text == "Bilinmiyor" and row.value_text == "Bilinmiyor"
        assert row.wage is None and row.contract_years is None      # K12: gizli sayi cikmaz
    assert all(r.loan_listed for r in rows)


@DB
def test_deadline_story_summarises_the_window(db, live):
    cm = _manager(db)
    market = WorldMarket(cm)
    from models import NewsItem, TransferKind, TransferLog

    db.add(TransferLog(season=cm.season, week=1, player_id=None, player_name="Deneme Oyuncu",
                       from_team_id=None, from_team_name="A Kulubu", to_team_id=None, to_team_name="B Kulubu",
                       fee=25_000_000, wage=10_000, kind=TransferKind.TRANSFER.value))
    db.flush()
    market._deadline_story((1, 5), 5)
    db.flush()
    texts = [n.text for n in db.scalars(select(NewsItem))]
    assert any(t.startswith("Son gün:") for t in texts)
    assert any("Deneme Oyuncu" in t for t in texts)     # en pahali transfer haberde


# ===========================================================================
# 4) TEK OYUNCULU DUNYADA KIRALIK (iki yon)
# ===========================================================================


@DB
def test_solo_world_loan_out_and_back_again(db):
    cm = _manager(db)
    assert not cm.rules.shared                          # eski tek kisilik kariyer
    team = cm.user_team
    desk = MarketDesk(cm)
    player = _fringe(team)
    club, share = _borrower_for(cm, player)
    before = len([p for p in team.players if not p.in_academy])
    view = desk.offer_loan_out(player.id, club.id, weeks=None, share=share)
    db.flush()
    assert view.status == LoanStatus.ACTIVE.value
    db.refresh(player)
    assert player.team_id == club.id and player.loan_from_team_id == team.id
    db.expire(team, ["players", "loaned_out_players"])
    assert len([p for p in team.players if not p.in_academy]) == before - 1
    loan = db.get(Loan, view.id)
    # kiralik suresi dolunca oyuncu ana kulubune doner (tek oyunculu dunyada LoanCycle kosar)
    loan.end_career_week = cm.career_week + 1
    db.flush()
    LoanCycle(cm).run_week(cm.current_week, False)
    db.flush()
    db.refresh(player)
    assert player.team_id == team.id and player.loan_from_team_id is None
    assert db.get(Loan, loan.id).status == LoanStatus.RETURNED.value


@DB
def test_solo_world_loan_in_from_an_ai_club(db):
    cm = _manager(db)
    desk = MarketDesk(cm)
    team = cm.user_team
    source = _big_ai_club(cm)
    target = _fringe(source)
    view = desk.request_loan(target.id, weeks=None, share=100)
    db.flush()
    db.refresh(target)
    assert target.team_id == team.id and target.loan_from_team_id == source.id
    assert view.direction == "IN"
    # kiralik oyuncu satilamaz / yeniden kiralanamaz
    assert cm.transfer_block_reason(target) is not None
    with pytest.raises(DeskError):
        desk.offer_loan_out(target.id, source.id, weeks=None, share=100)


@DB
def test_ai_club_refuses_a_first_team_loan_with_a_turkish_reason(db):
    cm = _manager(db)
    desk = MarketDesk(cm)
    source = _big_ai_club(cm)
    star = [p for p in source.players if not p.in_academy][0]
    with pytest.raises(DeskError) as err:
        desk.request_loan(star.id, weeks=None, share=100)
    assert "ilk" in str(err.value) or "kiralığa vermez" in str(err.value)


@DB
def test_loans_are_returned_at_the_season_rollover_in_a_solo_world(db):
    cm = _manager(db)
    desk = MarketDesk(cm)
    team = cm.user_team
    player = _fringe(team)
    club, share = _borrower_for(cm, player)
    desk.offer_loan_out(player.id, club.id, weeks=None, share=share)
    db.flush()
    cm._return_solo_loans()
    db.flush()
    db.refresh(player)
    assert player.team_id == team.id and player.loan_id is None


# ===========================================================================
# 5) AI KIRALIK TEKLIFLERI (bayrak)
# ===========================================================================


def _make_loan_offer(cm, team) -> TransferDeal:
    """LoanCycle'in yazdigi gibi bir AI kiralik teklifi dosyasi kurar (kural kapilari testte ayri sinaniyor)."""
    cycle = LoanCycle(cm)
    player = _fringe(team)
    club, share = _borrower_for(cm, player)
    offer = loan_rules.LoanOffer(None, share, None, False, "Kulüp kiralık istiyor.")
    cycle._write_offer(team, club, player, offer)
    cm.db.flush()
    return cm.db.scalars(select(TransferDeal).where(TransferDeal.kind == "LOAN")).first()


@DB
def test_ai_loan_offer_reaches_the_manager_and_can_be_accepted(db, live):
    cm = _manager(db)
    team = cm.user_team
    deal = _make_loan_offer(cm, team)
    assert deal is not None and deal.direction == "OUT" and deal.turn == "MANAGER"
    desk = MarketDesk(cm)
    views = desk.loan_offers()
    assert len(views) == 1 and views[0].can_accept and 0 <= views[0].terms.wage_share <= 100
    # 13H listeleri kiralik dosyasini GOSTERMEZ (transfer ekrani karismaz)
    assert all(v.id != deal.id for v in TransferDesk(cm).deals())
    player_id = deal.player_id
    desk.accept_loan_offer(deal.id)
    db.flush()
    player = db.get(Player, player_id)
    assert player.loan_from_team_id == team.id and player.team_id == deal.buyer_team_id
    assert db.get(TransferDeal, deal.id).status == "COMPLETED"


@DB
def test_ai_loan_offer_can_be_rejected(db, live):
    cm = _manager(db)
    deal = _make_loan_offer(cm, cm.user_team)
    desk = MarketDesk(cm)
    view = desk.reject_loan_offer(deal.id, "İhtiyacım var")
    db.flush()
    assert view.status == "REJECTED"
    assert desk.loan_offers() == []


# ===========================================================================
# 6) OPSIYONLU KIRALIK
# ===========================================================================


@DB
def test_mandatory_option_buys_the_player_when_the_loan_ends(db):
    cm = _manager(db)
    desk = MarketDesk(cm)
    team = cm.user_team
    player = _fringe(team)
    club, share = _borrower_for(cm, player)
    club.transfer_budget = 500_000_000
    club.wage_budget = int(club.wage_budget) + 5_000_000
    db.flush()
    fee = loan_rules.option_fee_for(int(player.market_value or 0), True)
    view = desk.offer_loan_out(player.id, club.id, weeks=None, share=share, option_fee=fee,
                               option_mandatory=True)
    db.flush()
    loan = db.get(Loan, view.id)
    assert loan.option_fee == fee and loan.option_mandatory
    loan.end_career_week = cm.career_week + 1
    db.flush()
    LoanCycle(cm).run_week(cm.current_week, False)
    db.flush()
    db.refresh(player)
    assert player.team_id == club.id and player.loan_from_team_id is None      # satin alindi
    assert db.get(Loan, loan.id).option_used is True


@DB
def test_option_fee_below_the_floor_is_refused(db):
    cm = _manager(db)
    desk = MarketDesk(cm)
    player = _fringe(cm.user_team)
    club, share = _borrower_for(cm, player)
    with pytest.raises(DeskError) as err:
        desk.offer_loan_out(player.id, club.id, weeks=None, share=share, option_fee=1)
    assert "satın alma bedelinin en az" in str(err.value)


# ===========================================================================
# 7) KARA DAYALI SONRAKI SATIS PAYI VE PAY IADESI
# ===========================================================================


def _completed_deal(cm, player: Player, seller: Team, buyer: Team, *, fee: int, pct: int,
                    profit: bool) -> TransferDeal:
    """Masada 'sonraki satistan pay' maddesiyle tamamlanmis bir kaynak dosya (yalnizca kayit)."""
    deal = TransferDeal(season=int(cm.season), created_career_week=cm.career_week,
                        updated_career_week=cm.career_week, direction="IN", status="COMPLETED",
                        player_id=player.id, seller_team_id=seller.id, buyer_team_id=buyer.id,
                        human_team_id=buyer.id, fee=fee, upfront=fee, instalment_months=0, add_ons=[],
                        sell_on_pct=pct, sell_on_profit=profit, round=1, patience=0, history=[], contract={},
                        medical={}, completed_career_week=cm.career_week, completed_season=int(cm.season),
                        completed_week=int(cm.current_week))
    cm.db.add(deal)
    cm.db.flush()
    return deal


@DB
def test_profit_based_sell_on_pays_nothing_on_a_loss_and_a_share_of_the_profit(db):
    cm = _manager(db)
    seller = cm.find_team(OTHER)
    buyer = cm.user_team
    buyer.transfer_budget = 50_000_000
    db.flush()
    player = [p for p in buyer.players if not p.in_academy][-1]
    deal = _completed_deal(cm, player, seller, buyer, fee=10_000_000, pct=20, profit=True)
    before = int(seller.transfer_budget)
    # zararina satis: pay yok
    paid = transfer_desk.settle_sell_on(cm, player, buyer, 6_000_000)
    db.flush()
    assert paid == 0 and int(seller.transfer_budget) == before
    deal.sell_on_used_career_week = None
    db.flush()
    # karli satis: yalnizca kardan pay
    paid = transfer_desk.settle_sell_on(cm, player, buyer, 20_000_000)
    db.flush()
    assert paid == (20_000_000 - 10_000_000) * 20 // 100
    assert int(seller.transfer_budget) == before + paid


@DB
def test_gross_sell_on_still_pays_the_whole_share(db):
    cm = _manager(db)
    seller = cm.find_team(OTHER)
    buyer = cm.user_team
    buyer.transfer_budget = 50_000_000
    db.flush()
    player = [p for p in buyer.players if not p.in_academy][-1]
    _completed_deal(cm, player, seller, buyer, fee=10_000_000, pct=20, profit=False)
    paid = transfer_desk.settle_sell_on(cm, player, buyer, 20_000_000)
    assert paid == 20_000_000 * 20 // 100


@DB
def test_reversing_a_sale_refunds_the_sell_on_share_and_frees_the_clause(db):
    cm = _manager(db)
    third = cm.find_team(OTHER)
    seller = cm.user_team
    seller.transfer_budget = 50_000_000
    db.flush()
    player = [p for p in seller.players if not p.in_academy][-1]
    deal = _completed_deal(cm, player, third, seller, fee=10_000_000, pct=20, profit=False)
    third_before = int(third.transfer_budget)
    seller_before = int(seller.transfer_budget)
    paid = transfer_desk.settle_sell_on(cm, player, seller, 20_000_000)
    db.flush()
    assert paid > 0 and deal.sell_on_used_career_week is not None
    refunded = transfer_desk.refund_sell_on(cm, player.id, seller.id, cm.career_week)
    db.flush()
    assert refunded == paid
    assert int(third.transfer_budget) == third_before            # pay geri gitti
    assert int(seller.transfer_budget) == seller_before          # satici parasini geri aldi
    assert deal.sell_on_used_career_week is None                 # madde yeniden kullanilabilir


# ===========================================================================
# 8) GERI ALIM MADDESI
# ===========================================================================


@DB
def test_manager_can_trigger_a_buy_back_clause(db):
    cm = _manager(db)
    team = cm.user_team
    other = cm.find_team(OTHER)
    if len([p for p in other.players if not p.in_academy]) - 1 < transfers.SQUAD_FLOOR:
        pytest.skip("Satıcı kadrosu geri alım testi için küçük")
    player = [p for p in other.players if not p.in_academy and p.position is not Position.GK][-1]
    deal = _completed_deal(cm, player, team, other, fee=5_000_000, pct=0, profit=False)
    deal.buy_back_fee, deal.buy_back_seasons = 6_000_000, 2
    player.transfer_locked_until = None
    team.transfer_budget = 200_000_000
    team.wage_budget = int(team.wage_budget) + 2_000_000
    db.flush()
    desk = MarketDesk(cm)
    rows = desk.buy_back_options()
    assert [r.player_id for r in rows] == [player.id] and rows[0].fee == 6_000_000
    desk.trigger_buy_back(player.id)
    db.flush()
    db.refresh(player)
    assert player.team_id == team.id
    assert deal.buy_back_fee is None                            # madde bir kez kullanilir
    assert desk.buy_back_options() == []


@DB
def test_expired_buy_back_clause_is_not_offered(db):
    cm = _manager(db)
    team = cm.user_team
    other = cm.find_team(OTHER)
    player = [p for p in other.players if not p.in_academy][-1]
    deal = _completed_deal(cm, player, team, other, fee=5_000_000, pct=0, profit=False)
    deal.buy_back_fee, deal.buy_back_seasons = 6_000_000, 1
    deal.completed_season = int(cm.season) - 2
    db.flush()
    assert MarketDesk(cm).buy_back_options() == []
    with pytest.raises(DeskError):
        MarketDesk(cm).trigger_buy_back(player.id)


# ===========================================================================
# 9) HAFTALIK AKIS
# ===========================================================================


@DB
def test_play_week_with_the_flag_on_keeps_the_world_solvent(db, live, caplog):
    cm = _manager(db)
    report = WeekReport(season=cm.season, week=cm.current_week)
    with caplog.at_level("ERROR"):
        cm.run_ai_transfer_window()
        transfer_desk.run_week(cm, cm.current_week, report)
    db.flush()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    assert all(int(t.transfer_budget) >= 0 for t in cm.teams())


@DB
def test_loan_cycle_is_silent_when_loans_are_off(db, monkeypatch):
    cm = _manager(db)
    monkeypatch.setattr(loan_rules, "SOLO_LOANS", False)
    assert transfer_desk.loans_on(cm) is False
    LoanCycle(cm).run_week(cm.current_week, True)        # tek satir bile yazmaz
    db.flush()
    assert db.query(Loan).count() == 0
    with pytest.raises(DeskError):
        MarketDesk(cm).loans()


@DB
def test_tournament_mode_never_runs_the_market(db, live):
    cm = CareerManager(db, seed=7)
    cm.state.game_mode = GameMode.TOURNAMENT
    db.flush()
    assert transfer_desk.live_market_on(cm) is False
    assert transfer_desk.loans_on(cm) is False
    assert cm._live_market_on() is False


@DB
def test_per_instance_override_beats_the_module_flag(db, live):
    """15A contract_cycle / 15B retirement / 15C board gibi: kopya basina self.live_market ezmesi."""
    cm = _manager(db)
    assert cm.live_market is None and cm._live_market_on() is True       # live fixture bayragi acti
    cm.live_market = False
    assert cm._live_market_on() is False and transfer_desk.live_market_on(cm) is False
    calls: list[str] = []
    monkey = getattr(cm, "_ai_transfer_deals")                          # noqa: B009 - eski yolu geri koymak icin
    cm._ai_transfer_deals = lambda: calls.append("old") or []
    try:
        cm.run_ai_transfer_window()
    finally:
        cm._ai_transfer_deals = monkey
    assert calls == ["old"]
    cm.live_market = True                                                # modul bayragi KAPALI olsa da acar
    import transfer_rules as _rules
    old_flag = _rules.LIVE_MARKET
    _rules.LIVE_MARKET = False
    try:
        assert cm._live_market_on() is True
    finally:
        _rules.LIVE_MARKET = old_flag
