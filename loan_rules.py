"""
loan_rules.py
=============
Kiralik oyuncu kurallari (Faz 12 / 14. Asama, 12B). SAF modul: veritabani, Streamlit ve models bilmez
(models.Team.player_wage_bill wage_split'i kullanir; bu dosya models'i import ETMEMELI -- dongu olur).

    wage_split            -> haftalik maasin kiralayan / ana kulup paylari (toplam her zaman maasa esit)
    ai_accepts_loan_out   -> AI kulubu oyuncusunu kiraliga verir mi?
    ai_accepts_loan_in    -> AI kulubu insan kulubunden oyuncu kiralar mi?
    loan_end_week         -> kiralamanin bitecegi mutlak kariyer haftasi (NULL hafta: sezon sonu)
    recall_allowed        -> ana kulup oyuncuyu erken geri cagirabilir mi (oynama suresi kaygisi)?

Durum: wage_split tanimli (models bagimli); digerleri Faz 12 B1 paketinde doldurulacak.
"""

from __future__ import annotations

_PACKAGE = "Faz 12: loan_rules (B1)"


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


def ai_accepts_loan_out(player_overall, player_rank_in_parent, parent_squad_size, wage_share, weeks) -> tuple[bool, str]:
    raise NotImplementedError(_PACKAGE)


def ai_accepts_loan_in(player_overall, borrower_position_avg, borrower_free_wage, wage, share) -> tuple[bool, str]:
    raise NotImplementedError(_PACKAGE)


def loan_end_week(start_career_week: int, weeks: int | None, season_end_career_week: int) -> int:
    raise NotImplementedError(_PACKAGE)


def recall_allowed(concern_level: int, weeks_on_loan: int) -> tuple[bool, str]:
    raise NotImplementedError(_PACKAGE)
