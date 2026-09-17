"""
Menajerler arasi teklif kurallari testleri (Faz 12 / 14. Asama, 12B): market_rules.py SAF modul, DB gerekmez.

    1) durum makinesi: her (durum, tur, eylem, taraf) birlesimi acik beklenen tabloyla karsilastirilir
    2) yasak hareketlerin Turkce mesajlari, gecersiz girdiler, sira / izinli eylem yardimcilari
    3) validate_offer (kimlik, oyuncu, butce, takas, kiralik, kadro tabani)
    4) teklif suresi, kadro seviyesi reddi, sozlesme tohumu, modul safligi
"""

from __future__ import annotations

import subprocess
import sys
import zlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import market_rules as mr  # noqa: E402
from market_rules import (  # noqa: E402
    OPEN_STATUSES,
    OfferAction,
    OfferFacts,
    OfferKind,
    OfferStateError,
    OfferStatus,
    next_state,
    transition,
    validate_offer,
)
from world_rules import WorldRules  # noqa: E402

B, S, A, Y = "BUYER", "SELLER", "ADMIN", "SYSTEM"
SIDES = (B, S, A, Y)
St, Ac = OfferStatus, OfferAction

# ===========================================================================
# 1) DURUM MAKINESI: ACIK BEKLENEN TABLO
# ===========================================================================

SYSTEM_EXITS_NEGOTIATION = {Ac.EXPIRE: St.EXPIRED, Ac.VOID: St.VOIDED, Ac.BLOCK: St.BLOCKED,
                            Ac.SEND_REVIEW: St.REVIEW}


def _negotiation_rows(status: OfferStatus, rnd: int, mover: str) -> dict:
    rows = {
        (status, rnd, Ac.ACCEPT, mover): (St.CONTRACT, rnd),
        (status, rnd, Ac.REJECT, mover): (St.REJECTED, rnd),
        (status, rnd, Ac.WITHDRAW, B): (St.WITHDRAWN, rnd),
    }
    if rnd < 4:
        rows[(status, rnd, Ac.COUNTER, mover)] = (St.COUNTERED, rnd + 1)
    for action, target in SYSTEM_EXITS_NEGOTIATION.items():
        rows[(status, rnd, action, Y)] = (target, rnd)
    return rows


# (durum, tur, eylem, taraf) -> (yeni durum, yeni tur). Burada olmayan her birlesim OfferStateError olmali.
ALLOWED: dict = {}
ALLOWED.update(_negotiation_rows(St.PENDING, 0, S))
ALLOWED.update(_negotiation_rows(St.COUNTERED, 1, B))
ALLOWED.update(_negotiation_rows(St.COUNTERED, 2, S))
ALLOWED.update(_negotiation_rows(St.COUNTERED, 3, B))
ALLOWED.update(_negotiation_rows(St.COUNTERED, 4, S))
for _rnd in (0, 3):
    ALLOWED.update({
        (St.CONTRACT, _rnd, Ac.COMPLETE, B): (St.COMPLETED, _rnd),
        (St.CONTRACT, _rnd, Ac.WITHDRAW, B): (St.WITHDRAWN, _rnd),
        (St.CONTRACT, _rnd, Ac.REJECT, Y): (St.REJECTED, _rnd),
        (St.CONTRACT, _rnd, Ac.EXPIRE, Y): (St.EXPIRED, _rnd),
        (St.CONTRACT, _rnd, Ac.VOID, Y): (St.VOIDED, _rnd),
        (St.CONTRACT, _rnd, Ac.BLOCK, Y): (St.BLOCKED, _rnd),
        (St.CONTRACT, _rnd, Ac.SEND_REVIEW, Y): (St.REVIEW, _rnd),
        (St.REVIEW, _rnd, Ac.APPROVE, A): (St.CONTRACT, _rnd),
        (St.REVIEW, _rnd, Ac.DENY, A): (St.BLOCKED, _rnd),
        (St.REVIEW, _rnd, Ac.EXPIRE, Y): (St.EXPIRED, _rnd),
        (St.REVIEW, _rnd, Ac.VOID, Y): (St.VOIDED, _rnd),
        (St.COMPLETED, _rnd, Ac.REVERSE, A): (St.REVERSED, _rnd),
    })

CONFIGS = [(St.PENDING, 0), (St.COUNTERED, 1), (St.COUNTERED, 2), (St.COUNTERED, 3), (St.COUNTERED, 4),
           (St.CONTRACT, 0), (St.CONTRACT, 3), (St.REVIEW, 0), (St.REVIEW, 3), (St.COMPLETED, 0), (St.COMPLETED, 3),
           (St.REJECTED, 0), (St.WITHDRAWN, 0), (St.EXPIRED, 0), (St.VOIDED, 0), (St.BLOCKED, 0), (St.REVERSED, 0)]
ALL_CASES = [(status, rnd, action, side) for status, rnd in CONFIGS for action in OfferAction for side in SIDES]


def test_expected_table_covers_every_status():
    assert {status for status, _rnd in CONFIGS} == set(OfferStatus)
    assert {key[:4] for key in ALLOWED} <= set(ALL_CASES)
    assert len(ALL_CASES) == len(CONFIGS) * len(OfferAction) * len(SIDES)


@pytest.mark.parametrize(("status", "rnd", "action", "side"), ALL_CASES,
                         ids=[f"{s.value}-{r}-{a.value}-{d}" for s, r, a, d in ALL_CASES])
def test_transition_table(status, rnd, action, side):
    key = (status, rnd, action, side)
    if key in ALLOWED:
        assert next_state(status, action, side, rnd) == ALLOWED[key]
        assert transition(status, action, side, rnd) is ALLOWED[key][0]
    else:
        with pytest.raises(OfferStateError) as err:
            next_state(status, action, side, rnd)
        message = str(err.value)
        assert message and message.endswith(".") and message[0].isupper()


@pytest.mark.parametrize(("status", "rnd"), CONFIGS)
def test_allowed_actions_match_table(status, rnd):
    for side in SIDES:
        expected = {action for (st, r, action, d) in ALLOWED if (st, r, d) == (status, rnd, side)}
        assert mr.allowed_actions(status, side, rnd) == expected


def test_full_negotiation_path_alternates_turns_until_round_limit():
    status, rnd = St.PENDING, 0
    movers = []
    for _ in range(mr.MAX_COUNTER_ROUNDS):
        mover = mr.turn_side(status, rnd)
        movers.append(mover)
        status, rnd = next_state(status, Ac.COUNTER, mover, rnd)
    assert movers == [S, B, S, B] and (status, rnd) == (St.COUNTERED, 4)
    with pytest.raises(OfferStateError, match="Pazarlık turu sınırı doldu"):
        next_state(status, Ac.COUNTER, S, rnd)
    status, rnd = next_state(status, Ac.ACCEPT, S, rnd)
    assert (status, rnd) == (St.CONTRACT, 4)
    status, rnd = next_state(status, Ac.SEND_REVIEW, Y, rnd)
    status, rnd = next_state(status, Ac.APPROVE, A, rnd)
    assert status is St.CONTRACT
    status, rnd = next_state(status, Ac.COMPLETE, B, rnd)
    assert next_state(status, Ac.REVERSE, A, rnd) == (St.REVERSED, 4)


@pytest.mark.parametrize(("status", "rnd", "action", "side", "message"), [
    (St.PENDING, 0, Ac.WITHDRAW, S, "Satıcı teklifi geri çekemez"),
    (St.COUNTERED, 1, Ac.WITHDRAW, S, "Satıcı teklifi geri çekemez"),
    (St.PENDING, 0, Ac.ACCEPT, B, "Kendi teklifini kabul edemezsin"),
    (St.COUNTERED, 1, Ac.ACCEPT, S, "Kendi teklifini kabul edemezsin"),
    (St.PENDING, 0, Ac.REJECT, B, "Kendi teklifini reddedemezsin; vazgeçtiysen geri çek"),
    (St.COUNTERED, 3, Ac.REJECT, S, "Karşı teklifin alıcının yanıtını bekliyor"),
    (St.COUNTERED, 2, Ac.COUNTER, B, "Sıra karşı tarafta"),
    (St.COUNTERED, 4, Ac.COUNTER, S, r"en fazla 4 karşı teklif"),
    (St.PENDING, 0, Ac.ACCEPT, A, "yalnızca alıcı ya da satıcı kulüp yanıt verebilir"),
    (St.PENDING, 0, Ac.ACCEPT, Y, "yalnızca alıcı ya da satıcı kulüp yanıt verebilir"),
    (St.PENDING, 0, Ac.WITHDRAW, A, "yalnızca alıcı kulüp"),
    (St.PENDING, 0, Ac.EXPIRE, B, "oyun tarafından otomatik"),
    (St.REVIEW, 0, Ac.VOID, A, "oyun tarafından otomatik"),
    (St.PENDING, 0, Ac.COMPLETE, B, "Önce bonservis anlaşması yapılmalı"),
    (St.PENDING, 0, Ac.APPROVE, A, "Yalnızca incelemedeki teklif"),
    (St.CONTRACT, 0, Ac.DENY, A, "Yalnızca incelemedeki teklif"),
    (St.PENDING, 0, Ac.REVERSE, A, "Yalnızca tamamlanmış transfer geri alınabilir"),
    (St.CONTRACT, 0, Ac.ACCEPT, S, "teklif sözleşme masasında"),
    (St.CONTRACT, 0, Ac.COUNTER, B, "teklif sözleşme masasında"),
    (St.CONTRACT, 0, Ac.REJECT, S, "satıcı artık vazgeçemez"),
    (St.CONTRACT, 0, Ac.WITHDRAW, S, "satıcı artık vazgeçemez"),
    (St.CONTRACT, 0, Ac.REJECT, B, "teklifi geri çek"),
    (St.CONTRACT, 0, Ac.COMPLETE, S, "yalnızca alıcı kulüp"),
    (St.REVIEW, 0, Ac.WITHDRAW, B, "yönetici incelemesinde"),
    (St.REVIEW, 0, Ac.APPROVE, B, "yalnızca dünya yöneticisi"),
    (St.REVIEW, 0, Ac.DENY, S, "yalnızca dünya yöneticisi"),
    (St.COMPLETED, 0, Ac.REVERSE, B, "yalnızca dünya yöneticisi"),
    (St.COMPLETED, 0, Ac.VOID, Y, "Transfer tamamlandı; yalnızca dünya yöneticisi geri alabilir"),
    (St.REJECTED, 0, Ac.ACCEPT, S, r"Teklif kapandı \(Reddedildi\)"),
    (St.REVERSED, 0, Ac.REVERSE, A, r"Teklif kapandı \(Geri alındı\)"),
    (St.BLOCKED, 0, Ac.APPROVE, A, r"Teklif kapandı \(Engellendi\)"),
])
def test_forbidden_moves_explain_in_turkish(status, rnd, action, side, message):
    with pytest.raises(OfferStateError, match=message):
        next_state(status, action, side, rnd)


def test_invalid_inputs_raise_state_errors():
    assert issubclass(OfferStateError, ValueError)
    with pytest.raises(OfferStateError, match="Bilinmeyen teklif durumu"):
        transition("DRAFT", Ac.ACCEPT, S)
    with pytest.raises(OfferStateError, match="Bilinmeyen teklif işlemi"):
        transition(St.PENDING, "SIGN", S)
    for side in ("OWNER", "", None, 1):
        with pytest.raises(OfferStateError, match="Geçersiz taraf"):
            transition(St.PENDING, Ac.ACCEPT, side)
    for rnd in (-1, 5, True, 1.0, "1"):
        with pytest.raises(OfferStateError, match="Geçersiz pazarlık turu"):
            transition(St.COUNTERED, Ac.ACCEPT, B, rnd)
    with pytest.raises(OfferStateError, match="turu 0 olmalı"):
        transition(St.PENDING, Ac.ACCEPT, S, 2)
    with pytest.raises(OfferStateError, match="en az 1 olmalı"):
        transition(St.COUNTERED, Ac.ACCEPT, S, 0)


def test_plain_strings_default_rounds_and_turn_helpers():
    # veritabanindan okunan duz metinler ve kucuk harfli taraf
    assert transition("PENDING", "COUNTER", "seller") is St.COUNTERED
    assert next_state("PENDING", "COUNTER", " SELLER ") == (St.COUNTERED, 1)
    # tur verilmezse: PENDING 0, COUNTERED 1 (saticinin ilk karsi teklifi -> sira alicida)
    assert transition(St.COUNTERED, Ac.ACCEPT, B) is St.CONTRACT
    with pytest.raises(OfferStateError):
        transition(St.COUNTERED, Ac.ACCEPT, S)
    assert [mr.proposer_side(r) for r in range(5)] == [B, S, B, S, B]
    assert mr.turn_side(St.PENDING) == S and mr.turn_side("COUNTERED", 2) == S and mr.turn_side(St.COUNTERED, 3) == B
    assert (mr.turn_side(St.CONTRACT), mr.turn_side(St.REVIEW), mr.turn_side(St.COMPLETED)) == (B, A, None)
    assert all(mr.turn_side(s) is None for s in (St.REJECTED, St.WITHDRAWN, St.EXPIRED, St.VOIDED, St.BLOCKED,
                                                 St.REVERSED))
    assert mr.allowed_actions(St.PENDING, B) == {Ac.WITHDRAW}
    assert mr.allowed_actions(St.EXPIRED, A) == frozenset()


def test_open_statuses_labels_and_constants():
    assert OPEN_STATUSES == {St.PENDING, St.COUNTERED, St.CONTRACT, St.REVIEW}
    assert all(mr.is_open(s) == (s in OPEN_STATUSES) for s in OfferStatus) and mr.is_open("REVIEW")
    assert mr.MAX_COUNTER_ROUNDS == 4 and mr.ACTOR_SIDES == SIDES
    for labels, enum_cls in ((mr.STATUS_LABELS, OfferStatus), (mr.ACTION_LABELS, OfferAction),
                             (mr.KIND_LABELS, OfferKind)):
        assert set(labels) == set(enum_cls) and all(isinstance(v, str) and v for v in labels.values())
    assert mr.STATUS_LABELS["COUNTERED"] == "Karşı teklif"        # duz metin anahtar da calisir
    assert mr.KIND_LABELS["LOAN"] == "Kiralık"
    assert set(mr.SIDE_LABELS) == set(SIDES)
    assert set(mr.TRANSITIONS) == set(OfferStatus)
    with pytest.raises(TypeError):
        mr.TRANSITIONS[St.PENDING][Ac.ACCEPT]["BUYER"] = St.COMPLETED     # tablo salt okunur


def test_floor_defaults_match_transfer_rules():
    import transfers
    from models import Position

    assert mr.SQUAD_FLOOR_DEFAULT == transfers.SQUAD_FLOOR
    assert mr.KEEPER_FLOOR_DEFAULT == transfers.POSITION_SALE_FLOOR[Position.GK]


# ===========================================================================
# 2) TEKLIF DOGRULAMA
# ===========================================================================

BASE = OfferFacts(
    kind=OfferKind.TRANSFER, player_id=10, player_team_id=1, seller_team_id=1, buyer_team_id=2, fee=5_000_000,
    player_value=6_000_000, player_name="Ali Kaya", player_position="MID", player_wage=40_000,
    buyer_transfer_budget=20_000_000, seller_squad_size=20, seller_position_count=6,
)
LOAN = replace(BASE, kind=OfferKind.LOAN, fee=0, loan_weeks=10, loan_wage_share=50, buyer_free_wage=100_000)
EXCHANGE = replace(BASE, exchange_player_id=20, exchange_player_team_id=2, exchange_value=2_000_000,
                   exchange_name="Veli Can", exchange_position="DEF")


def test_valid_offers_have_no_problems():
    assert validate_offer(BASE) == []
    assert validate_offer(LOAN) == []
    assert validate_offer(EXCHANGE) == []
    assert validate_offer(replace(BASE, kind="TRANSFER")) == []          # duz metin tur
    assert validate_offer(replace(LOAN, kind="LOAN", seller_is_human=False)) == []   # insan <-> AI kiralik
    assert validate_offer(replace(LOAN, buyer_is_human=False)) == []
    assert validate_offer(replace(BASE, fee=0)) == []                     # bedelsiz (adil oyun ayrica bakar)
    assert validate_offer(replace(BASE, fee=20_000_000)) == []            # butcenin tamami


@pytest.mark.parametrize(("changes", "expected"), [
    ({"player_team_id": None}, ["Ali Kaya bir kulübe bağlı değil."]),
    ({"seller_team_id": None, "fee": -5}, ["Ali Kaya bir kulübe bağlı değil."]),
    ({"buyer_team_id": None}, ["Teklifi yapan kulüp bulunamadı."]),
    ({"player_on_loan": True, "player_team_id": 3, "fee": -1},
     ["Ali Kaya kiralık oyuncu; kiralık dönüşüne kadar satılamaz ya da yeniden kiralanamaz."]),
    ({"player_team_id": 3}, ["Ali Kaya artık bu kulüpte değil; teklif geçersiz."]),
    ({"buyer_team_id": 1}, ["Ali Kaya zaten senin takımında."]),
    ({"player_name": "", "player_team_id": None}, ["Oyuncu bir kulübe bağlı değil."]),
])
def test_identity_problems_are_fatal(changes, expected):
    assert validate_offer(replace(BASE, **changes)) == expected


@pytest.mark.parametrize(("facts", "expected"), [
    (replace(BASE, player_in_academy=True), "Ali Kaya akademide; akademi oyuncuları satılık ya da kiralık değil."),
    (replace(BASE, player_ban_weeks=3), "Ali Kaya yeni transfer; 3 hafta daha satılamaz ya da kiralanamaz."),
    (replace(LOAN, player_ban_weeks=1), "Ali Kaya yeni transfer; 1 hafta daha satılamaz ya da kiralanamaz."),
    (replace(BASE, fee=-1), "Teklif negatif olamaz."),
    (replace(BASE, buyer_transfer_budget=-1_200_000),
     "Transfer kasası ekside (-1.2M EUR); kasa artıya dönene kadar oyuncu alamazsın."),
    (replace(BASE, fee=8_000_000, buyer_transfer_budget=5_000_000),
     "Transfer bütçen yetersiz: 5.0M EUR var, 8.0M EUR gerekiyor."),
    (replace(LOAN, fee=600_000, buyer_transfer_budget=500_000), "Transfer bütçen yetersiz: 500K EUR var, 600K EUR gerekiyor."),
    (replace(BASE, seller_is_human=False), "Yapay zekâ kulübüne teklif normal transfer ekranından yapılır."),
    (replace(BASE, buyer_is_human=False), "Menajerler arası teklifi yalnızca menajerli kulüp yapabilir."),
    (replace(BASE, loan_weeks=10), "Transfer teklifinde kiralık süresi olmaz."),
    (replace(EXCHANGE, exchange_player_id=10), "Takas oyuncusu, istenen oyuncuyla aynı olamaz."),
    (replace(EXCHANGE, exchange_player_team_id=1),
     "Veli Can senin kadronda değil; yalnızca kendi oyuncunu takasta verebilirsin."),
    (replace(EXCHANGE, exchange_player_team_id=None, exchange_name=""),
     "Takas oyuncusu senin kadronda değil; yalnızca kendi oyuncunu takasta verebilirsin."),
    (replace(EXCHANGE, exchange_on_loan=True), "Veli Can kiralık oyuncu; takasta verilemez."),
    (replace(EXCHANGE, exchange_in_academy=True), "Veli Can akademide; akademi oyuncuları takasta verilemez."),
    (replace(EXCHANGE, exchange_ban_weeks=2), "Veli Can yeni transfer; 2 hafta daha satılamaz ya da kiralanamaz."),
    (replace(LOAN, buyer_is_human=False, seller_is_human=False),
     "Kiralık anlaşmasında en az bir taraf menajerli kulüp olmalı."),
    (replace(LOAN, exchange_player_id=20, exchange_player_team_id=2), "Kiralık teklifte takas oyuncusu olamaz."),
    (replace(LOAN, loan_weeks=3), "Kiralık süresi 4-52 hafta arasında olmalı (boş: sezon sonuna kadar)."),
    (replace(LOAN, loan_weeks=53), "Kiralık süresi 4-52 hafta arasında olmalı (boş: sezon sonuna kadar)."),
    (replace(LOAN, loan_wage_share=-1), "Kiralık maaş payı %0-100 arasında olmalı."),
    (replace(LOAN, loan_wage_share=101), "Kiralık maaş payı %0-100 arasında olmalı."),
    (replace(LOAN, player_wage=50_000, buyer_free_wage=24_999),
     "Maaş bütçen yetersiz: kiralık payı haftalık 25K EUR, boş alan 25K EUR."),
    (replace(LOAN, player_wage=50_000, buyer_free_wage=-3_000),
     "Maaş bütçen yetersiz: kiralık payı haftalık 25K EUR, boş alan 0 EUR."),
])
def test_single_rule_violations(facts, expected):
    assert validate_offer(facts) == [expected]


def test_loan_ranges_and_wage_room_edges():
    for weeks in (None, 4, 52):
        assert validate_offer(replace(LOAN, loan_weeks=weeks)) == []
    for share in (0, 100):
        assert validate_offer(replace(LOAN, loan_wage_share=share)) == []
    # kiralayan payi asagi yuvarlanir: 50.001 x %50 -> 25.000
    assert validate_offer(replace(LOAN, player_wage=50_001, buyer_free_wage=25_000)) == []
    assert validate_offer(replace(LOAN, player_wage=50_000, buyer_free_wage=25_000)) == []
    # pay 0 ise eksi maas alani sorun degil; maas alani verilmezse denetlenmez
    assert validate_offer(replace(LOAN, loan_wage_share=0, buyer_free_wage=-50_000)) == []
    assert validate_offer(replace(LOAN, player_wage=10**9, buyer_free_wage=None)) == []
    assert validate_offer(replace(BASE, player_wage=10**9, buyer_free_wage=0)) == []     # transferde maas masada
    assert validate_offer(replace(BASE, buyer_transfer_budget=None, fee=10**12)) == []


@pytest.mark.parametrize(("facts", "expected"), [
    # satici A takimi: islem sonrasi en az squad_floor (13)
    (replace(BASE, seller_squad_size=14), []),
    (replace(BASE, seller_squad_size=13), ["Satıcı kulübün A takım kadrosu 13 oyuncunun altına düşer."]),
    (replace(LOAN, seller_squad_size=13), ["Satıcı kulübün A takım kadrosu 13 oyuncunun altına düşer."]),
    (replace(EXCHANGE, seller_squad_size=13), []),                          # 1'e 1 takas sayiyi korur
    (replace(EXCHANGE, seller_squad_size=11), []),                          # zaten altinda ama azalmiyor
    (replace(BASE, seller_squad_size=15, squad_floor=15), ["Satıcı kulübün A takım kadrosu 15 oyuncunun altına düşer."]),
    (replace(BASE, seller_squad_size=None), []),
    # mevki tabani (kaleci 2)
    (replace(BASE, player_position="GK", seller_position_count=3, player_position_floor=2), []),
    (replace(BASE, player_position="GK", seller_position_count=2, player_position_floor=2),
     ["Satıcı kulüp GK mevkisinde en az 2 oyuncu tutmalı."]),
    (replace(BASE, player_position="GK", seller_position_count=2, player_position_floor=0), []),
    (replace(BASE, player_position="GK", seller_position_count=None, player_position_floor=2), []),
    (replace(EXCHANGE, player_position="GK", exchange_position="GK", seller_position_count=2,
             player_position_floor=2, buyer_position_count=2, exchange_position_floor=2), []),
    (replace(EXCHANGE, player_position="GK", exchange_position="DEF", seller_position_count=2,
             player_position_floor=2), ["Satıcı kulüp GK mevkisinde en az 2 oyuncu tutmalı."]),
    # alicinin takasta verdigi mevki
    (replace(EXCHANGE, exchange_position="GK", buyer_position_count=2, exchange_position_floor=2),
     ["Takastan sonra GK mevkisinde en az 2 oyuncun kalmalı."]),
    (replace(EXCHANGE, exchange_position="GK", buyer_position_count=3, exchange_position_floor=2), []),
    (replace(BASE, exchange_position="GK", buyer_position_count=1, exchange_position_floor=2), []),   # takas yok
])
def test_squad_floors_after_the_deal(facts, expected):
    assert validate_offer(facts) == expected


def test_problems_accumulate_in_stable_order():
    facts = replace(EXCHANGE, player_in_academy=True, player_ban_weeks=2, fee=-1, buyer_transfer_budget=-10,
                    loan_weeks=5, exchange_on_loan=True, exchange_ban_weeks=1, seller_squad_size=13,
                    player_position="GK", exchange_position="GK", seller_position_count=1,
                    player_position_floor=2)
    problems = validate_offer(facts)
    assert problems == [
        "Ali Kaya akademide; akademi oyuncuları satılık ya da kiralık değil.",
        "Ali Kaya yeni transfer; 2 hafta daha satılamaz ya da kiralanamaz.",
        "Teklif negatif olamaz.",
        "Transfer kasası ekside (-10 EUR); kasa artıya dönene kadar oyuncu alamazsın.",
        "Transfer teklifinde kiralık süresi olmaz.",
        "Veli Can kiralık oyuncu; takasta verilemez.",
        "Veli Can yeni transfer; 1 hafta daha satılamaz ya da kiralanamaz.",
    ]
    assert validate_offer(facts) == problems                                # deterministik
    loan = replace(LOAN, loan_weeks=2, loan_wage_share=150, exchange_player_id=5, seller_squad_size=13)
    assert validate_offer(loan) == [
        "Kiralık teklifte takas oyuncusu olamaz.",
        "Kiralık süresi 4-52 hafta arasında olmalı (boş: sezon sonuna kadar).",
        "Kiralık maaş payı %0-100 arasında olmalı.",
    ]


def test_offer_facts_helpers():
    assert not BASE.is_loan and LOAN.is_loan and replace(BASE, kind="loan").is_loan
    assert EXCHANGE.has_exchange and not BASE.has_exchange
    with pytest.raises(AttributeError):
        BASE.fee = 1                                                          # frozen


# ===========================================================================
# 3) SURE, KADRO SEVIYESI, TOHUM
# ===========================================================================

def test_expires_at_uses_world_rules_and_clamps():
    assert mr.expires_at(10, WorldRules()) == 12
    assert mr.expires_at(10, WorldRules(offer_expiry_weeks=5)) == 15
    assert mr.expires_at(0, WorldRules(offer_expiry_weeks=8)) == 8
    assert mr.expires_at(10, None) == 12
    assert mr.expires_at(10, SimpleNamespace(offer_expiry_weeks=20)) == 18
    assert mr.expires_at(10, SimpleNamespace(offer_expiry_weeks=0)) == 11
    assert mr.expires_at(10, SimpleNamespace(offer_expiry_weeks="bozuk")) == 12
    assert not mr.is_expired(12, 11) and mr.is_expired(12, 12) and mr.is_expired(12, 13)
    assert not mr.is_expired(None, 10_000)


def test_squad_level_refusal_edges():
    seventy = [70] * 11
    assert mr.squad_level_refusal(82, seventy) is None                      # fark tam 12: kabul
    refusal = mr.squad_level_refusal(83, seventy)
    assert refusal == "Bu kadro benim seviyemde değil: ilk 11 oyuncunun ortalaması 70, benim gücüm 83."
    assert mr.squad_level_refusal(99, []) is None                           # bos kadro
    # yalnizca en iyi 11 sayilir (yedekler seviyeyi dusurmez), sira onemli degil
    assert mr.squad_level_refusal(82, [40] * 5 + seventy) is None
    assert mr.squad_level_refusal(82, list(reversed([40] * 5 + seventy))) is None
    # 11'den az oyuncu: mevcutlarin ortalamasi
    assert mr.squad_level_refusal(92, [80] * 5) is None
    assert "ilk 5 oyuncunun ortalaması 80" in mr.squad_level_refusal(93, [80] * 5)
    # tam sayi aritmetigi: ortalama 70.545 -> sinir 82.545
    mixed = [71] * 6 + [70] * 5
    assert mr.squad_level_refusal(82, mixed) is None
    assert "ortalaması 71, benim gücüm 83" in mr.squad_level_refusal(83, mixed)
    # margin
    assert mr.squad_level_refusal(70, seventy, margin=0) is None
    assert mr.squad_level_refusal(71, seventy, margin=0) is not None
    assert mr.squad_level_refusal(71, seventy, margin=-5) is not None        # eksi margin 0 sayilir
    assert mr.squad_level_refusal(90, seventy, margin=20) is None
    assert mr.squad_level_refusal(50, seventy) is None                       # zayif oyuncu her zaman gelir
    assert mr.squad_level(mixed) == pytest.approx(776 / 11) and mr.squad_level([]) is None
    assert mr.squad_level([90, 80, 70], top=2) == 85


def test_contract_rng_seed_is_deterministic():
    seed = mr.contract_rng_seed(1, 2, 3)
    assert seed == zlib.crc32(b"contract|1|2|3") == 2718364833
    assert mr.contract_rng_seed(1, 2, 3) == seed
    others = {mr.contract_rng_seed(1, 2, 4), mr.contract_rng_seed(1, 3, 3), mr.contract_rng_seed(2, 2, 3),
              mr.contract_rng_seed(3, 2, 1)}
    assert seed not in others and len(others) == 4
    assert all(0 <= s < 2**32 for s in others)


def test_rule_modules_are_pure():
    code = ("import sys, market_rules, loan_rules, fair_play; "
            "print(sorted(m for m in ('models', 'database', 'sqlalchemy', 'streamlit', 'finance', 'transfers') "
            "if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
