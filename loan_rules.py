"""
loan_rules.py
=============
Kiralik oyuncu kurallari (Faz 12 / 14. Asama, 12B). SAF modul: veritabani, Streamlit ve models bilmez
(models.Team.player_wage_bill wage_split'i kullanir; bu dosya models'i import ETMEMELI -- dongu olur).

    wage_split            -> haftalik maasin kiralayan / ana kulup paylari (toplam her zaman maasa esit)
    ai_accepts_loan_out   -> AI kulubu oyuncusunu insan kulubune kiraliga verir mi?
    ai_accepts_loan_in    -> AI kulubu insan kulubunden oyuncu kiralar mi?
    loan_end_week         -> kiralamanin bitecegi mutlak kariyer haftasi (NULL hafta: sezon sonu)
    recall_allowed        -> ana kulup oyuncuyu erken geri cagirabilir mi (oynama suresi kaygisi)?
    format_money          -> finance.format_money ile ayni kisa para bicimi (finance models import ettigi icin
                             burada; market_rules ve fair_play de bunu kullanir)

Maas paylasimi (wage_split): kiralayanin payi = maas x yuzde // 100 (ASAGI yuvarlanir), kalan ana kulube yazilir.
    12.345 EUR, %33 -> kiralayan 4.073, ana kulup 8.272 · %0 -> 0 / tamami · %100 -> tamami / 0

Sure (loan_end_week): kiralik baslangic haftasi S, sure N hafta -> bitis S + N; sezon sonunu (season_end) asamaz,
NULL sure = sezon sonu. Oyuncu S .. bitis-1 haftalarinda kiralayanda oynar; kariyer haftasi bitis haftasina
ulasinca (haftalik eklenti) ana kulubune doner. Bitis hicbir zaman baslangictan once olmaz (loans CHECK).

AI kiraliga VERIR mi (ai_accepts_loan_out), kontrol sirasi ve kalibrasyon:
    1) oyuncu ana kulupte guc sirasinda ilk AI_LOAN_OUT_PROTECTED_RANK (11) icindeyse: ilk 11 kiralanmaz
    2) kiralik sonrasi A takim AI_LOAN_OUT_MIN_SQUAD (16; career_manager.AI_MIN_SENIOR_SQUAD) altina dusecekse
    3) sure AI_LOAN_OUT_MIN_WEEKS (6) haftadan kisaysa (NULL = sezon sonu her zaman uygun)
    4) kiralayanin maas payi gereken payin altindaysa. Gereken pay:
           guc >= AI_LOAN_OUT_VALUABLE_OVERALL (80)          -> %100 (degerli oyuncunun maasini AI odemez)
           guc sirasi <= AI_LOAN_OUT_ROTATION_RANK (16)      -> %75  (rotasyon oyuncusu)
           diger (yedek, fazlalik)                           -> %50
    Ornekler: 24 kisilik kadroda 18. sira, 68 guc, %50, 10 hafta -> kabul · 14. sira %60 -> ret (%75 ister)
              9. sira -> ret (ilk 11) · 16 kisilik kadro -> ret (15'e duser) · 18. sira, 4 hafta -> ret

AI kiralik ALIR mi (ai_accepts_loan_in), kontrol sirasi ve kalibrasyon:
    borrower_position_avg: AI kulubunun o mevkideki oyuncularinin guc ortalamasi (None: o mevkide oyuncusu yok)
    1) guc farki (oyuncu - ortalama) gereken farkin altindaysa ret. Gereken fark:
           maas payi <= AI_LOAN_IN_CHEAP_SHARE (%50)  -> AI_LOAN_IN_MIN_EDGE (0): en az mevki ortalamasi
           maas payi  > %50                           -> AI_LOAN_IN_FULL_WAGE_EDGE (+3): belirgin guclenme
       mevkide oyuncusu yoksa (None) her oyuncu ihtiyactir
    2) kiralayanin haftalik payi (wage_split) bos maas alanini asiyorsa ret (pay 0 ise denetlenmez)
    Ornekler: ortalama 70; 71 guc %50 -> kabul · 71 guc %80 -> ret (+3 ister) · 74 guc %100, pay 30K, bos 50K
              -> kabul · ayni oyuncu bos alan 20K -> ret · 68 guc %0 -> ret (mevkide daha iyileri var)

Erken geri cagirma (recall_allowed), kontrol sirasi:
    1) oyuncunun kaygi seviyesi RECALL_MIN_CONCERN (2 = concerns.ConcernLevel.CONCERNED "Şikayetçi") altindaysa ret
    2) oyuncu RECALL_MIN_WEEKS (4) haftadan az suredir kiraliktaysa ret (kaygi penceresi dolmadan cagrilmaz)
"""

from __future__ import annotations

# --- AI kiraliga verir mi -----------------------------------------------------
AI_LOAN_OUT_PROTECTED_RANK = 11        # ana kulupte guc sirasi bu ve ustu (1 = en iyi): kiralanmaz
AI_LOAN_OUT_MIN_SQUAD = 16             # kiralik sonrasi A takimda en az (career_manager.AI_MIN_SENIOR_SQUAD)
AI_LOAN_OUT_MIN_WEEKS = 6              # daha kisa kiralik istenmez (NULL: sezon sonu uygun)
AI_LOAN_OUT_ROTATION_RANK = 16         # 12-16. sira rotasyon oyuncusu
AI_LOAN_OUT_VALUABLE_OVERALL = 80      # bu gucte oyuncunun maasini AI hic odemez
AI_LOAN_OUT_BASE_SHARE = 50            # kiralayanin asgari maas payi (%)
AI_LOAN_OUT_ROTATION_SHARE = 75
AI_LOAN_OUT_VALUABLE_SHARE = 100

# --- AI kiralik alir mi -------------------------------------------------------
AI_LOAN_IN_MIN_EDGE = 0                # oyuncu en az mevki ortalamasi kadar iyi olmali
AI_LOAN_IN_CHEAP_SHARE = 50            # bu paya kadar maas odemek "ucuz" sayilir
AI_LOAN_IN_FULL_WAGE_EDGE = 3          # daha yuksek payda ek guc farki

# --- Erken geri cagirma ------------------------------------------------------
RECALL_MIN_CONCERN = 2                 # concerns.ConcernLevel.CONCERNED
RECALL_MIN_WEEKS = 4


def format_money(amount: float) -> str:
    """Insan okuyacak kisa para bicimi: 12.5M EUR, 850K EUR, 900 EUR (finance.format_money ile birebir)."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.1f}M EUR"
    if a >= 1_000:
        return f"{sign}{a / 1_000:.0f}K EUR"
    return f"{sign}{a:.0f} EUR"


def wage_split(wage: int, borrower_share_pct: int) -> tuple[int, int]:
    """
    (kiralayanin odedigi, ana kulubun odedigi) haftalik maas. Kiralayan payi asagi yuvarlanir, kalan ana
    kulube yazilir: iki pay toplami maasa birebir esittir (kulupler arasi para kaybolmaz / yaratilmaz).
    Yuzde 0-100 araligina kirpilir.
    """
    wage = int(wage)
    share = max(0, min(100, int(borrower_share_pct)))
    borrower = wage * share // 100
    return borrower, wage - borrower


def loan_out_required_share(player_overall: int, player_rank_in_parent: int) -> int:
    """AI ana kulubunun kiralayandan istedigi asgari maas payi (%)."""
    if int(player_overall) >= AI_LOAN_OUT_VALUABLE_OVERALL:
        return AI_LOAN_OUT_VALUABLE_SHARE
    if int(player_rank_in_parent) <= AI_LOAN_OUT_ROTATION_RANK:
        return AI_LOAN_OUT_ROTATION_SHARE
    return AI_LOAN_OUT_BASE_SHARE


def ai_accepts_loan_out(
    player_overall: int,
    player_rank_in_parent: int,
    parent_squad_size: int,
    wage_share: int,
    weeks: int | None,
) -> tuple[bool, str]:
    """
    AI ana kulubu oyuncusunu insan kulubune kiraliga verir mi? (kabul, Turkce gerekce).
    player_rank_in_parent: A takimdaki guc sirasi (1 = en iyi); parent_squad_size: kiralik ONCESI A takim sayisi;
    wage_share: kiralayanin odeyecegi maas yuzdesi; weeks: sure (None = sezon sonu). Sira modul basliginda.
    """
    if int(player_rank_in_parent) <= AI_LOAN_OUT_PROTECTED_RANK:
        return False, f"Kulüp ilk {AI_LOAN_OUT_PROTECTED_RANK} oyuncusunu kiralığa vermez."
    if int(parent_squad_size) - 1 < AI_LOAN_OUT_MIN_SQUAD:
        return False, f"Kulübün A takım kadrosu {AI_LOAN_OUT_MIN_SQUAD} oyuncunun altına düşer; kiralık verilmez."
    if weeks is not None and int(weeks) < AI_LOAN_OUT_MIN_WEEKS:
        return False, (f"Kulüp en az {AI_LOAN_OUT_MIN_WEEKS} haftalık ya da sezon sonuna kadar "
                       f"kiralık istiyor.")
    share = max(0, min(100, int(wage_share)))
    required = loan_out_required_share(player_overall, player_rank_in_parent)
    if share < required:
        return False, f"Kulüp maaş payının en az %{required} olmasını istiyor (teklif: %{share})."
    return True, f"Kulüp kiralığa onay verdi (maaşın %{share} payı kiralayan kulüpte)."


def ai_accepts_loan_in(
    player_overall: int,
    borrower_position_avg: float | None,
    borrower_free_wage: int,
    wage: int,
    share: int,
) -> tuple[bool, str]:
    """
    AI kulubu insan kulubunun oyuncusunu kiralar mi? (kabul, Turkce gerekce).
    borrower_position_avg: AI kulubunun o mevkideki guc ortalamasi (None: mevkide oyuncu yok);
    borrower_free_wage: AI kulubunun bos haftalik maas alani; wage: oyuncunun haftalik maasi; share: AI'nin
    odeyecegi yuzde. Sira modul basliginda.
    """
    pct = max(0, min(100, int(share)))
    if borrower_position_avg is not None:
        required_edge = AI_LOAN_IN_MIN_EDGE
        if pct > AI_LOAN_IN_CHEAP_SHARE:
            required_edge += AI_LOAN_IN_FULL_WAGE_EDGE
        edge = float(player_overall) - float(borrower_position_avg)
        if edge < required_edge:
            if required_edge > AI_LOAN_IN_MIN_EDGE and edge >= AI_LOAN_IN_MIN_EDGE:
                return False, (f"Kulüp maaşın %{pct} payı için mevkisinde belirgin bir güçlenme istiyor "
                               f"(en az +{required_edge}).")
            return False, "Kulübün bu mevkide daha iyi oyuncuları var; kiralık almak istemiyor."
    borrower_pays, _parent_pays = wage_split(wage, pct)
    if borrower_pays > 0 and borrower_pays > int(borrower_free_wage):
        return False, (f"Kulübün maaş bütçesi yetmiyor: haftalık pay {format_money(borrower_pays)}, "
                       f"boş alan {format_money(max(0, int(borrower_free_wage)))}.")
    return True, "Kulüp kiralık teklifini kabul etti."


def loan_end_week(start_career_week: int, weeks: int | None, season_end_career_week: int) -> int:
    """
    Kiralamanin bitecegi mutlak kariyer haftasi: baslangic + sure, sezon sonuyla sinirli; None sure = sezon sonu.
    Bitis baslangictan once olmaz (sezon sonu gecmisse baslangic haftasi doner). Sure 1'den kucukse ValueError.
    """
    start, season_end = int(start_career_week), int(season_end_career_week)
    if weeks is None:
        end = season_end
    else:
        if int(weeks) < 1:
            raise ValueError("Kiralık süresi en az 1 hafta olmalı.")
        end = min(start + int(weeks), season_end)
    return max(start, end)


def recall_allowed(concern_level: int | None, weeks_on_loan: int) -> tuple[bool, str]:
    """
    Ana kulup kiralik oyuncuyu erken geri cagirabilir mi? (izin, Turkce gerekce).
    concern_level: oyuncunun oynama suresi kaygisi (concerns.ConcernLevel degeri 0-3; None = 0).
    """
    level = max(0, min(3, int(concern_level or 0)))
    weeks = max(0, int(weeks_on_loan))
    if level < RECALL_MIN_CONCERN:
        return False, "Oyuncu kiralık kulübünde süre alıyor; yalnızca süre alamayan oyuncu erken geri çağrılabilir."
    if weeks < RECALL_MIN_WEEKS:
        return False, (f"Kiralık oyuncu en az {RECALL_MIN_WEEKS} hafta sonra geri çağrılabilir "
                       f"({weeks} hafta oldu).")
    return True, "Oyuncu kiralık kulübünde süre alamıyor; erken geri çağrılabilir."
