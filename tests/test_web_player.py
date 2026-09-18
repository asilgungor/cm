"""
Faz 13E: oyuncu profili (player_view) -- Streamlit AppTest (basliksiz) + saf yardimci testleri.

Gercek web_app.py betigi calisir. Her giris noktasindan (kadro, transfer pazari, takip listesi, akademi,
Teklifler & Listeler, milli takim kadrosu) ayni panel acilir; panelin gosterdigi bilgi GOZLEMCI SISINE
uyar: kendi oyuncunda kesin yildiz + gunluk durum, baska kulubun oyuncusunda aralik ve kapali kulup ici bilgi.
Motorun 1-99 sayilari ve gizli potansiyel HICBIR cizimde yazilmaz (K12). Faz 13I (CM 01/02 duzeni): 1-20 ozellik
sayfasi "CM gibi + gozlemci" kuraliyla -- kendi oyuncun ve %70+ bilinen oyuncu kesin, kismen bilinen aralik, bilinmeyen "?".

Kendi veritabaninda calistirin (dunyayi yeniden seed eder):
    TEST_DB_NAME=fm_db_test_13e python -m pytest -q -p no:cacheprovider tests/test_web_player.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.nav_helpers import goto  # noqa: E402
from tests.test_web_app import (  # noqa: E402
    _app,
    _click,
    _db_available,
    _know,
    _query,
    _reseed,
    _set_user_team,
)
from tests.world_helpers import (  # noqa: E402
    age_managers,
    app_as,
    cleanup_shared,
    cleanup_users,
    make_shared_public,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL erişilemiyor"),
]

pytest.importorskip("streamlit.testing.v1")

TEAM = "Istanbul Lions"
RIVAL = "Madrid Blancos"
PREFIX = "p13e"
OWNER, MEMBER = "p13esahip", "p13euye"
HUB_TEAM, HUB_MEMBER_TEAM = "Manchester Blue", "Merseyside Reds"

TAG = re.compile(r"<[^>]+>")
STAR_TEXT = re.compile(r"^(⭐|💫|–|\s|-)+$")
SHEET_CELL = re.compile(r'<span class="k">([^<]*)</span><span class="v">([^<]*)</span>')
EXTRA_CELLS = frozenset({"Tercih ettiği ayak", "Form", "Moral", "Kondisyon"})


@pytest.fixture(autouse=True)
def fresh_world():
    cleanup_users(PREFIX)
    _reseed()
    yield
    cleanup_users(PREFIX)


@pytest.fixture(scope="module", autouse=True)
def clean_after_module():
    yield
    cleanup_users(PREFIX)
    _reseed()


# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------

def _players(team_name: str, *, academy: bool = False) -> list[tuple]:
    """(id, ad, mevki, guc, potansiyel, yas) -- ORM nesnesi disari sizmaz."""
    from sqlalchemy import select

    from models import Team

    def work(db):
        team = db.scalar(select(Team).where(Team.name == team_name))
        return [(p.id, p.name, p.position.value, p.overall_rating, p.potential_rating, p.age)
                for p in (team.academy_players if academy else team.players)]

    return _query(work)


def _set_fields(player_id: int, **fields) -> None:
    from database import session_scope
    from models import Player

    with session_scope() as db:
        player = db.get(Player, player_id)
        for key, value in fields.items():
            setattr(player, key, value)


def _panel_blocks(at) -> str:
    """Yalnizca profil panelinin kendi HTML'i (pv-head / pv-attrs / pv-curve); tema CSS'i ve diger sekmeler degil."""
    return "\n".join(m.value for m in at.markdown if 'class="pv-' in str(m.value))


def _panel_text(at) -> str:
    return TAG.sub(" ", _panel_blocks(at))


def _all_text(at) -> str:
    parts = [str(m.value) for m in at.markdown]
    parts += [str(c.value) for c in at.caption]
    parts += [str(e.value) for group in (at.info, at.success, at.warning, at.error) for e in group]
    return TAG.sub(" ", "\n".join(parts))


def _sheet(at) -> dict[str, str]:
    """Profil sekmesindeki CM ozellik izgarasi: etiket -> ekrandaki metin ("15" / "12-15" / "?")."""
    import html

    blocks = "\n".join(m.value for m in at.markdown if 'class="pv-sheet' in str(m.value))
    cells = {html.unescape(k): html.unescape(v) for k, v in SHEET_CELL.findall(blocks)}
    assert len(cells) == 31 + len(EXTRA_CELLS), f"CM izgarasi eksik: {len(cells)} hucre"
    return cells


def _frame(at, *columns: str):
    """Verilen sutunlari tasiyan ilk tablo."""
    for df in at.dataframe:
        if set(columns) <= set(df.value.columns):
            return df.value
    raise AssertionError(f"{columns} sütunlu tablo yok")


def _has_frame(at, *columns: str) -> bool:
    return any(set(columns) <= set(df.value.columns) for df in at.dataframe)


def _section(at, section: str):
    at.button_group(key="pv_section").set_value(section)
    at.run()
    assert not at.exception, at.exception
    return at


def _open_squad(at, player_id: int):
    if at.session_state["nav_page"] != "kadro":                   # Faz 13I: menu sayfasi
        goto(at, "kadro")
    at.selectbox(key="pv_pick_squad").set_value(player_id)
    at.run()
    assert not at.exception, at.exception
    return _click(at, "pv_btn_squad")


def _open_market(at, player_id: int, name: str):
    """Pazar listesi 40 satirla sinirli: once isimle daralt, sonra hedefi sec ve incele."""
    if at.session_state["nav_page"] != "transfer":
        goto(at, "transfer")
    at.text_input(key="mkt_name").set_value(name)
    at.run()
    assert not at.exception, at.exception
    at.selectbox(key="mkt_target").set_value(player_id)
    at.run()
    assert not at.exception, at.exception
    return _click(at, "pv_btn_market")


def _contains_number(text: str, value) -> bool:
    return bool(re.search(rf"(?<!\d){int(value)}(?!\d)", text))


# ---------------------------------------------------------------------------
# 1) Giris noktalari: kadro
# ---------------------------------------------------------------------------

def test_squad_entry_point_opens_the_profile_with_cm_tabs():
    import player_view as pv

    _set_user_team(TEAM)
    at = _app(page="kadro")
    assert at.selectbox(key="pv_pick_squad")                      # secici her zaman var
    assert "pv_section" not in {g.key for g in at.button_group}     # panel kapaliyken sekme satiri yok

    pid, name, position, _ovr, _pot, age = _players(TEAM)[0]
    at = _open_squad(at, pid)
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_SQUAD, pid)
    assert list(at.button_group(key="pv_section").options) == list(pv.SECTIONS)
    assert pv.SECTIONS == ("Profil", "Sakatlık & Cezalar", "Sözleşme", "Transfer", "Geçmiş")
    blocks = _panel_blocks(at)
    assert "pv-head" in blocks and name in blocks
    assert f"{age} yaş" in blocks and f'class="no">{position}' in blocks and f"({TEAM})" in blocks
    assert "kendi oyuncun" in blocks
    assert at.button(key="pv_close") and at.button(key="pv_prev") and at.button(key="pv_next")


def test_close_button_clears_the_panel():
    import player_view as pv

    _set_user_team(TEAM)
    at = _open_squad(_app(), _players(TEAM)[0][0])
    at = _click(at, "pv_close")
    assert pv.PROFILE_KEY not in at.session_state
    assert "pv-head" not in _panel_blocks(at)


# ---------------------------------------------------------------------------
# 2) Kendi oyuncun vs sisli oyuncu
# ---------------------------------------------------------------------------

def test_own_player_shows_daily_state_and_exact_stars():
    import player_view as pv

    _set_user_team(TEAM)
    pid = _players(TEAM)[0][0]
    _set_fields(pid, form=64, morale=71, condition=88)
    at = _open_squad(_app(), pid)
    panel = _panel_text(at)
    assert "gözlemci raporu" not in panel                          # kendi oyuncun: sis rozeti yok
    assert "kendi oyuncun" in panel and "⭐" in panel
    cells = _sheet(at)
    assert cells["Kondisyon"] == "%88" and cells["Form"] == "64" and cells["Moral"] == "71"   # izgaranin son hucreleri
    assert all(v.isdigit() for k, v in cells.items() if k not in EXTRA_CELLS)                  # kendi oyuncun: kesin 1-20
    text = _all_text(at)
    assert pv.FOG_TEXT not in text
    text = _all_text(_section(at, pv.SEC_CONTRACT))
    assert "Memnuniyet" in text and "Mutlu" in text                # kaygi seviyesi (concerns)
    # Kendi oyuncunda tahminler KESIN: her ozellik tek yildiz degeri (aralik degil), yine de sayi yok
    profile = _build(pid)
    assert profile.overall.exact and all(e.exact for e in profile.attributes.values())
    assert profile.overall.text.startswith("⭐") and "–" not in profile.overall.text


def test_other_club_player_is_fogged_and_hides_club_internals():
    import player_view as pv

    _set_user_team(TEAM)
    pid, name, _pos, _ovr, _pot, _age = _players(RIVAL)[0]
    at = _open_market(_app(), pid, name)
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_MARKET, pid)
    blocks = _panel_blocks(at)
    assert name in blocks and "gözlemci raporu" in blocks and "kendi oyuncun" not in blocks
    text = _all_text(at)
    assert pv.FOG_TEXT in text
    cells = _sheet(at)
    assert {cells["Form"], cells["Moral"], cells["Kondisyon"]} == {"?"}       # gunluk durum yalnizca kendi oyuncunda
    profile = _build(pid)
    assert not profile.overall.exact and all(not e.exact for e in profile.attributes.values())

    # Sozlesme bolumu: maas ve kadro rolu kapali, piyasa degeri aralik; Transfer: "bana kaca mal olur" acik
    at = _section(at, pv.SEC_CONTRACT)
    text = _all_text(at)
    assert "Bilinmiyor" in text and "başka kulübün defterinde" in text
    text = _all_text(_section(at, pv.SEC_TRANSFER))
    assert "Bana kaça mal olur?" in text and "kulübe sor" in text               # K12: kulubun fiyati yazilmaz


def test_attribute_sheet_follows_scout_knowledge_like_cm():
    """CM gibi + gozlemci: %25 alti "?", %25-69 aralik (gercek degeri icerir), %70+ kesin 1-20; gizli potansiyel
    ve gunluk durum bilgi ne olursa olsun yazilmaz."""
    import cm_attributes

    _set_user_team(TEAM)
    pid, name, _pos, _ovr, _pot, _age = _players(RIVAL)[0]
    _set_fields(pid, potential_rating=97)
    _know(TEAM, pid, 50)
    at = _open_market(_app(), pid, name)
    cells = _sheet(at)
    ranges = {k: v for k, v in cells.items() if k not in EXTRA_CELLS}
    assert all(re.fullmatch(r"\d{1,2}-\d{1,2}", v) for v in ranges.values()), ranges   # %50: hep aralik
    assert cells["Tercih ettiği ayak"] != "?" and cells["Form"] == "?"            # ayak gozlemle bilinir
    assert "bilgi %50" in _panel_text(at)

    from sqlalchemy import update

    from database import session_scope
    from models import Player, ScoutAssignment

    with session_scope() as db:
        db.execute(update(ScoutAssignment).where(ScoutAssignment.player_id == pid).values(knowledge=75))
        exact = cm_attributes.player_attributes(db.get(Player, pid))
    at.run()
    assert not at.exception, at.exception
    cells = _sheet(at)
    labels = {cm_attributes.ATTRIBUTE_LABELS[k]: v for k, v in exact.items()}
    assert {k: v for k, v in cells.items() if k not in EXTRA_CELLS} == {k: str(v) for k, v in labels.items()}
    assert not _contains_number(_panel_text(at), 97)                               # potansiyel yine tahmin
    for label, text in ranges.items():                                            # %50 araligi kesin degeri icerir
        low, high = (int(n) for n in text.split("-"))
        assert low <= labels[label] <= high, (label, text, labels[label])


def test_own_and_foreign_potential_is_always_an_estimate():
    """Gercek potential_rating hicbir kullanicida ekrana gitmez: kendi oyuncunda da tahmin gosterilir."""
    _set_user_team(TEAM)
    pid, _name, _pos, _ovr, _pot, _age = _players(TEAM)[0]
    _set_fields(pid, overall_rating=70, potential_rating=93)
    profile = _build(pid)
    assert profile.potential.text and not _contains_number(profile.potential.text, 93)
    assert profile.potential.low >= 70                             # kendi oyuncunda alt sinir gucun altina inmez


# ---------------------------------------------------------------------------
# 3) Yasak sayilar (K12)
# ---------------------------------------------------------------------------

FORBIDDEN_SETUP = dict(overall_rating=83, potential_rating=97, pace=81, shooting=84, passing=79,
                       defending=38, dribbling=86, goalkeeping=23, age=27, form=50, morale=70, condition=100)


def test_profile_never_prints_engine_numbers_for_a_scouted_player():
    import player_view as pv

    _set_user_team(TEAM)
    pid, name, _pos, _ovr, _pot, _age = _players(RIVAL)[0]
    _set_fields(pid, **FORBIDDEN_SETUP)
    at = _open_market(_app(), pid, name)

    panel = _panel_text(at)
    assert "⭐" in panel                                            # olcek yildiz
    assert set(v for k, v in _sheet(at).items() if k not in EXTRA_CELLS) == {"?"}   # bilgi %0: hic sayi yok
    for field in ("overall_rating", "potential_rating", "pace", "shooting", "passing",
                  "defending", "dribbling", "goalkeeping"):
        assert not _contains_number(panel, FORBIDDEN_SETUP[field]), f"{field} sayısı ekrana sızdı: {panel}"

    # Cizimin tamaminda sans kalitesi / xG yok
    everything = _all_text(at)
    assert "xG" not in everything and "şans kalitesi" not in everything.lower()

    # Panelin ham HTML'i de sizdirmaz: cubuk genisligi yildizdan turetilir
    raw = _panel_blocks(at)
    assert not _contains_number(TAG.sub(" ", raw), FORBIDDEN_SETUP["overall_rating"])
    assert pv.bar_pct(83) == 100 and pv.bar_pct(60) == 60


def test_all_sections_of_a_scouted_player_stay_silent_about_hidden_potential():
    import player_view as pv

    _set_user_team(TEAM)
    pid, name, _pos, _ovr, _pot, _age = _players(RIVAL)[0]
    _set_fields(pid, **FORBIDDEN_SETUP)
    at = _open_market(_app(), pid, name)
    for section in pv.SECTIONS:
        at = _section(at, section)
        assert not _contains_number(_panel_text(at), 97), f"{section}: gizli potansiyel sızdı"


# ---------------------------------------------------------------------------
# 4) Bos durumlar, sakat / cezali, kiralik
# ---------------------------------------------------------------------------

def test_player_without_matches_shows_empty_states():
    import player_view as pv

    _set_user_team(TEAM)
    at = _open_squad(_app(), _players(TEAM)[0][0])
    assert pv.NO_DATA_TEXT in _all_text(at)                         # Profil: bu sezonun matrisi bos
    at = _section(at, pv.SEC_STATS)
    text = _all_text(at)
    assert pv.NO_DATA_TEXT in text
    assert not _has_frame(at, "Sezon", "Kupa/Lig", "Ort. not")      # sezon tablosu daha yok

    at = _section(at, pv.SEC_TRANSFER)
    assert "hiç kulüp değiştirmedi" in _all_text(at)


def test_injured_and_suspended_players_are_tagged():
    _set_user_team(TEAM)
    squad = _players(TEAM)
    _set_fields(squad[0][0], injured_until_week=6)
    _set_fields(squad[1][0], suspended_matches=2, season_yellow_cards=4)

    at = _open_squad(_app(), squad[0][0])
    assert "sakat, 6. haftada dönüyor" in _panel_text(at)
    assert 'cm-badge bad">sakat, 6. haftada dönüyor' in _panel_blocks(at)     # oynayamaz: uyari rozeti
    at = _open_squad(at, squad[1][0])
    assert 'cm-badge bad">cezalı, 2 maç' in _panel_blocks(at)


def test_loaned_player_shows_parent_club_and_wage_share():
    import player_view as pv

    _set_user_team(TEAM)
    from sqlalchemy import select

    from models import Team

    parent_id = _query(lambda db: db.scalar(select(Team).where(Team.name == RIVAL)).id)
    pid = _players(TEAM)[0][0]
    _set_fields(pid, loan_from_team_id=parent_id, loan_wage_share=60)

    at = _open_squad(_app(), pid)
    assert "kiralık oynuyor" in _panel_text(at)
    at = _section(at, pv.SEC_TRANSFER)
    text = _all_text(at)
    assert f"Ana kulüp: {RIVAL}" in text and "%60" in text


def test_transfer_listed_and_ban_states_reach_the_contract_section():
    import player_view as pv

    _set_user_team(TEAM)
    pid = _players(TEAM)[0][0]
    _set_fields(pid, transfer_listed=True, wage_demand=90_000, release_clause=45_000_000)
    at = _section(_open_squad(_app(), pid), pv.SEC_CONTRACT)
    text = _all_text(at)
    assert "Yeni sözleşme istiyor" in text and "Serbest kalma bedeli" in text and "45.0M" in text   # 13H: modellendi
    text = _all_text(_section(at, pv.SEC_TRANSFER))
    assert "satış listesine koydu" in text


# ---------------------------------------------------------------------------
# 5) Mac ve sezon istatistikleri
# ---------------------------------------------------------------------------

def test_season_table_and_recent_matches_after_a_played_week():
    import player_view as pv

    _set_user_team(TEAM)
    at = _app(page="kadro")
    _click(at, "tac_auto")
    _click(at, "nav_continue")                                      # Faz 13I: menudeki Devam
    played = _query(_first_scorer)
    assert played is not None, "hafta oynandi ama oyuncu istatistigi yazilmadi"

    at = _open_squad(at, played)
    matrix = _frame(at, "Yarışma", "Maç", "Gol", "İsabetli şut", "Ort. not")      # Profil: yarisma matrisi
    assert "Lig" in list(matrix["Yarışma"]) and not {"Pas", "Top kapma", "Dribling"} & set(matrix.columns)
    at = _section(at, pv.SEC_STATS)
    seasons = _frame(at, "Sezon", "Kupa/Lig", "Maç", "Gol", "Ort. not")
    assert len(seasons) >= 1 and int(seasons["Maç"].iloc[0]) >= 1
    recent = _frame(at, "Rakip", "Skor", "Dk")
    assert len(recent) >= 1
    assert "Sakatlık geçmişi" in _all_text(at)


def _first_scorer(db):
    """Hafta oynandiktan sonra kullanicinin kulubunden istatistigi olan bir oyuncu."""
    from sqlalchemy import select

    from models import Player, PlayerMatchStat, Team

    team = db.scalar(select(Team).where(Team.name == TEAM))
    row = db.scalar(select(PlayerMatchStat).join(Player, Player.id == PlayerMatchStat.player_id)
                    .where(Player.team_id == team.id).limit(1))
    return row.player_id if row is not None else None


# ---------------------------------------------------------------------------
# 6) Karsilastirma ve kadro rolu
# ---------------------------------------------------------------------------

def test_comparison_lists_same_position_players_and_squad_role():
    import player_view as pv

    _set_user_team(TEAM)
    pid, name, position, _ovr, _pot, _age = next(p for p in _players(TEAM) if p[2] == "DEF")
    at = _section(_open_squad(_app(), pid), pv.SEC_COMPARE)
    frame = _frame(at, "Oyuncu", "Güç", "Rol")
    assert any(str(cell).startswith("▶ ") and name in str(cell) for cell in frame["Oyuncu"])
    assert all(STAR_TEXT.match(str(v)) for v in frame["Güç"])       # sayisal guc yok
    text = _all_text(_section(at, pv.SEC_CONTRACT))
    assert "Kulüpteki statü" in text and "Beklediği maç" in text
    assert any(label in text for label in pv.SQUAD_STATUS_LABELS)     # CM statu etiketleri
    at = _section(at, pv.SEC_COMPARE)

    # Lig kapsami: mevkidaslari sisli yildizla, kendi oyuncun isaretli
    at.radio(key="pv_cmp").set_value(pv.CMP_LEAGUE)
    at.run()
    assert not at.exception, at.exception
    league = _frame(at, "Oyuncu", "Kulüp", "Güç (tahmin)")
    assert len(league) >= 2 and all(STAR_TEXT.match(str(v)) for v in league["Güç (tahmin)"])
    assert any(str(cell).startswith("▶ ") for cell in league["Oyuncu"])


def test_comparison_of_a_foreign_player_hides_club_role():
    import player_view as pv

    _set_user_team(TEAM)
    pid, name, _pos, _ovr, _pot, _age = _players(RIVAL)[0]
    at = _section(_open_market(_app(), pid, name), pv.SEC_CONTRACT)
    text = _all_text(at)
    assert "başka kulübün defterinde" in text
    assert "Beklediği maç" not in text and "Kulüpteki statü" not in _panel_text(at)


# ---------------------------------------------------------------------------
# 7) Diger giris noktalari: takip listesi, akademi
# ---------------------------------------------------------------------------

def test_shortlist_entry_point_opens_the_profile():
    import player_view as pv

    _set_user_team(TEAM)
    pid, name, _pos, _ovr, _pot, _age = _players(RIVAL)[0]
    _shortlist(pid)
    at = _app(page="transfer")                                    # Faz 13I: takip listesi Transfer Merkezi'nde
    at = _click(at, "pv_btn_shortlist")
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_SHORTLIST, pid)
    assert name in _panel_blocks(at) and "gözlemci raporu" in _panel_blocks(at)


def _shortlist(player_id: int) -> None:
    from career_manager import CareerManager
    from database import session_scope
    from models import Player

    with session_scope() as db:
        CareerManager(db).shortlist_add(db.get(Player, player_id))


def test_academy_entry_point_opens_the_profile():
    import player_view as pv

    _set_user_team(TEAM)
    academy = _players(TEAM, academy=True)
    assert academy, "seed akademisi bos"
    pid, name, _pos, _ovr, _pot, _age = academy[0]
    at = _app(page="akademi")
    at.selectbox(key="pv_pick_academy").set_value(pid)
    at.run()
    at = _click(at, "pv_btn_academy")
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_ACADEMY, pid)
    assert name in _panel_blocks(at) and "kendi oyuncun" in _panel_blocks(at)
    at = _section(at, pv.SEC_CONTRACT)
    assert "Akademi oyuncusu" in _all_text(at)                      # A takim sure beklentisi islemez


# ---------------------------------------------------------------------------
# 8) Paylasilan dunya: Teklifler & Listeler ve milli takim kadrosu
# ---------------------------------------------------------------------------

@pytest.fixture
def hub_world():
    world = make_shared_public(OWNER, [(MEMBER, HUB_MEMBER_TEAM)], owner_team=HUB_TEAM)
    age_managers(world)
    try:
        yield world
    finally:
        cleanup_shared(world, reseed=False)


def test_hub_listings_entry_point_opens_the_profile(hub_world):
    import market_view
    import player_view as pv

    at = app_as(hub_world.owner_id, OWNER, hub_world.world_id, page="mesajlar")
    assert not at.exception, at.exception
    at.radio(key="hub_section").set_value(market_view.SEC_LISTS)
    at.run()
    assert not at.exception, at.exception

    pid, name, _pos, _ovr, _pot, _age = _players(HUB_TEAM)[0]
    at = _click(at, f"pv_row_hub_p{pid}")
    assert at.session_state[pv.PROFILE_KEY] == (pv.AREA_HUB, pid)
    blocks = _panel_blocks(at)
    assert name in blocks and "kendi oyuncun" in blocks


def test_national_squad_entry_point_opens_the_profile():
    import national_view as nv
    import player_view as pv
    from world_rules import WorldRules

    rules = WorldRules.shared_defaults().with_changes(
        {"internationals": True, "human_market": False, "loans": False})
    world = make_shared_public(OWNER, [(MEMBER, HUB_MEMBER_TEAM)], owner_team=HUB_TEAM, rules=rules)
    try:
        at = app_as(world.user_ids[MEMBER], MEMBER, world.world_id, page="milli-takim")
        assert not at.exception, at.exception
        offer = next(k for k in {b.key for b in at.button if b.key} if k.startswith("nt_accept_"))
        at = _click(at, offer)
        at.radio(key="nt_section").set_value(nv.SEC_SQUAD)
        at.run()
        assert not at.exception, at.exception

        assert at.selectbox(key="pv_pick_national")               # aday tablosunun altindaki secici
        at = _click(at, "pv_btn_national")
        assert at.session_state[pv.PROFILE_KEY][0] == pv.AREA_NATIONAL
        assert "pv-head" in _panel_blocks(at)
    finally:
        cleanup_shared(world, reseed=False)


# ---------------------------------------------------------------------------
# 9) Saf yardimcilar (Streamlit'siz)
# ---------------------------------------------------------------------------

def _build(player_id: int):
    """Tek oyuncunun profil nesnesi (kullanicinin kulubu izleyici)."""
    import player_view as pv
    from career_manager import CareerManager
    from database import session_scope
    from models import Player

    with session_scope() as db:
        cm = CareerManager(db)
        return pv.build_profile(cm, cm.user_team, db.get(Player, player_id))


def test_bar_width_comes_from_stars_not_from_the_engine_value():
    import player_view as pv

    assert pv.bar_pct(40) == 20 and pv.bar_pct(80) == 100
    assert pv.bar_pct(81) == pv.bar_pct(95)                          # 80+ hepsi 5 yildiz: cubuk da ayni
    assert pv.bar_pct(1) == 10                                       # en az yarim yildiz


def test_position_fit_is_offered_only_for_plausible_positions():
    import player_view as pv
    from models import Position

    attrs = {name: pv.Estimate(60, 60, True) for name, _label in pv.ATTRIBUTE_LABELS}
    assert set(pv.position_fit(Position.GK, attrs)) == {"GK"}
    assert set(pv.position_fit(Position.MID, attrs)) == {"DEF", "MID", "FWD"}
    fogged = {name: pv.Estimate(50, 70, False) for name, _label in pv.ATTRIBUTE_LABELS}
    assert all(not est.exact for est in pv.position_fit(Position.FWD, fogged).values())


def test_estimate_text_is_always_stars():
    import player_view as pv

    exact = pv.Estimate(75, 75, True)
    fogged = pv.Estimate(55, 75, False)
    assert exact.text == "⭐⭐⭐⭐💫" and "–" in fogged.text
    assert exact.word == "Çok iyi" and pv.Estimate(85, 85, True).word == "Dünya çapında"


def test_open_and_close_helpers_track_one_area_at_a_time(monkeypatch):
    import player_view as pv

    state: dict = {}
    monkeypatch.setattr(pv.st, "session_state", state)
    assert pv.opened(pv.AREA_SQUAD) is None
    pv.open_profile(pv.AREA_SQUAD, 7)
    assert pv.opened(pv.AREA_SQUAD) == 7 and pv.opened(pv.AREA_MARKET) is None
    state["auth"] = object()
    pv.cb_pv_close()
    assert pv.opened(pv.AREA_SQUAD) is None


def test_callbacks_require_a_session():
    import player_view as pv

    assert pv.cb_pv_open.requires_auth is True
    assert pv.cb_pv_close.requires_auth is True


def test_hostile_player_and_club_names_are_escaped():
    """Oyuncu / kulup adi disaridan (FM dosyasi) gelebilir: panel HTML'i ve Markdown'i kacisli yazar."""
    import player_view as pv

    _set_user_team(TEAM)
    squad = _players(TEAM)
    pid = squad[0][0]
    _set_fields(pid, name="<b>Kötü</b> *Ad* [x](y)")
    at = _open_squad(_app(), pid)
    blocks = _panel_blocks(at)
    assert "&lt;b&gt;Kötü&lt;/b&gt;" in blocks and "<b>Kötü</b>" not in blocks
    header = pv.header_html(pv.ProfileHeader(
        player_id=1, name="<script>x</script>", club="<i>K</i>", position="MID", age=20, nationality="<u>",
        own=False, in_academy=False, wonderkid=False, tags=[("<b>t</b>", True)]))
    assert "<script>" not in header and "<i>K</i>" not in header and "<b>t</b>" not in header


def test_squad_status_labels_follow_cm_levels_without_changing_rules():
    import player_view as pv
    from models import SquadRole

    assert pv.squad_status(SquadRole.STAR, 29) == "Vazgeçilmez"
    assert pv.squad_status(SquadRole.FIRST_TEAM, 19) == "Önemli ilk 11 oyuncusu"
    assert pv.squad_status(SquadRole.BACKUP, 27) == "Rotasyon / yedek"
    assert pv.squad_status(SquadRole.BACKUP, 19, wonderkid=True) == "Geleceğin umudu"
    assert pv.squad_status(SquadRole.BACKUP, 21) == "İyi bir genç"
    assert set(pv.ROLE_PROMISE_LABELS) == set(SquadRole)                # sozlesme masasi: motorun uc rolu


def test_sheet_columns_use_the_module_groups_and_escape_text():
    from types import SimpleNamespace

    import cm_attributes
    import player_view as pv

    player = SimpleNamespace(id=7, position=__import__("models").Position.MID, age=24, overall_rating=70,
                             pace=70, shooting=65, passing=75, defending=55, dribbling=72, goalkeeping=20,
                             fm_attributes=None, form=60, morale=70, condition=90)
    cells = {k: pv.SheetCell(k, cm_attributes.ATTRIBUTE_LABELS[k], "12") for k in cm_attributes.ATTRIBUTE_KEYS}
    extras = pv.extra_cells(player, own=True, knowledge=100)
    columns = pv.sheet_columns(cells, extras)
    assert len(columns) == pv.SHEET_COLUMNS == 3
    labels = [[label for label, _cells in col] for col in columns]
    groups = [label for _k, label, _keys in cm_attributes.ATTRIBUTE_GROUPS]
    assert labels == [[groups[0]], [groups[1]], [*groups[2:], pv.STATE_GROUP_LABEL]]
    shown = [c.key for col in columns for _label, group in col for c in group]
    assert sorted(shown) == sorted([*cm_attributes.ATTRIBUTE_KEYS, "foot", "form", "morale", "condition"])
    tech = [c.label for c in columns[0][0][1]]
    assert tech == sorted(tech, key=pv.tr_sort_key)                        # grup icinde Turkce alfabetik
    html = pv.sheet_html([[("<b>G</b>", [pv.SheetCell("x", "<i>k</i>", "<s>")])]])
    assert "<b>G</b>" not in html and "<i>k</i>" not in html and "&lt;s&gt;" in html
    assert pv.SheetCell("x", "k", "16-19").band == 4 and pv.SheetCell("x", "k", "9-12").band == 2
    assert pv.SheetCell("x", "k", "?").band == 0
