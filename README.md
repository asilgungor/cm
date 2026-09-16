# CM — Futbol Menajerlik Simülasyonu

Football Manager / Championship Manager mekanikleriyle çalışan, arka planda istatistiki bir
simülasyon motoru koşturan menajerlik oyunu. **Python 3.10+ · SQLAlchemy 2 · PostgreSQL 16 (Docker)**

Mimari ilke: **Logic ve View katmanları tamamen ayrık.** Motor (`match_engine.py`) veritabanını ve
arayüzü bilmez; terminal spikeri sadece bir "View"dır ve ileride 2D arayüzle değiştirilecektir.

## Kurulum ve çalıştırma

```bash
docker compose up -d                 # PostgreSQL 16 (host port 5433)
pip install -r requirements.txt
python seed.py                       # şemayı sıfırla + 3 lig / 12 takım / 180 oyuncu / 36 maç
python main.py                       # kariyer modu: takım seç, haftaları oyna, puan durumu
```

Tek maç denemek için: `python match_engine.py` (Galatasaray - Fenerbahçe derbisi).

Faydalı seçenekler:

```bash
python seed.py --verify-only                     # sadece raporla
python match_engine.py --dry-run --seed 42       # DB'ye yazmadan, tekrar üretilebilir
python match_engine.py --home Inter --away Milan --dry-run   # hazırlık maçı
python main.py --team Galatasaray --auto 6 --seed 7          # tam sezonu sormadan oynat
python main.py --new-season                                  # sezon bittiyse yenisini başlat
```

`.env` dosyası `DATABASE_URL` içerir (bkz. `.env.example`). Port 5433, makinede 5432'yi kullanan
başka bir container ile çakışmamak için seçildi.

## Dosyalar

| Dosya | Görev |
|---|---|
| `database.py` | Engine (connection pool), `SessionLocal`, `Base`, `session_scope()` |
| `models.py` | `League`, `Team`, `Player`, `Fixture` ORM modelleri, CHECK/UNIQUE constraint'ler |
| `seed.py` | Deterministik başlangıç verisi; takım güç bantlarından tutarlı oyuncular üretir |
| `match_engine.py` | Maç motoru + DB adaptörü + terminal spikeri |
| `career_manager.py` | Sezon döngüsü: haftayı oynat, form/moral, sakatlık, ceza, gol krallığı, yeni sezon |
| `main.py` | Kariyer CLI'ı (View): hafta, yaklaşan maç, puan durumu, kadro, menü |
| `schedule.py` | Çift devreli fikstür üretimi (saf fonksiyon) |
| `tests/` | Unit + entegrasyon testleri (`pytest`), Monte Carlo kalibrasyon sınırları |

## Maç motoru mekanikleri

- Efektif güç: `overall × form × moral` (nötr noktaya göre sönümlenmiş — form farkı yeteneği ezmesin diye)
- Mevkiye göre alt-özellik ağırlıkları (FWD: shooting/pace, MID: passing/dribbling, DEF: defending, GK: goalkeeping)
- Ev sahibi avantajı (itibarla ölçeklenir), yorgunluk/enerji, taktik değişiklikler
- Sarı/kırmızı kart (ikinci sarı = kırmızı), sakatlık → aynı mevkiden yedek, yedek kaleci kuralları
- Uzatma dakikaları, geride kalan takımın son 20 dakikada bastırması
- Asist, maçın adamı, oyuncu maç notları

## Sezon döngüsü ve kalıcılık

- `game_state`: sezon, mevcut hafta, yönetilen takım (tek satır)
- `player_match_stats`: maç başına oyuncu istatistiği (gol/asist/not) → gol krallığı, geçmiş
- Oyuncu: `injured_until_week` (döneceği hafta), `suspended_matches`, `season_yellow_cards`, `match_rating_history` (son 5 not, JSONB)
- Sakat/cezalı oyuncu ne ilk 11'e ne kulübeye alınır; kadro yetmezse takım eksik oynar
- Maç sonrası: not → form (`±4×(not−6.5)`), not + sonuç → moral; oynamayanın formu 50'ye kayar
- Kırmızı: ikinci sarı 1 maç, direkt kırmızı 1 veya 3 maç; her 4 sarı = 1 maç ceza
- Sakatlık süresi ağırlıklı dağılım (çoğu 1-2 hafta, nadiren 10)
- Sezon bitince yeni sezon: fikstür yeniden, yaş +1, istatistik/ceza/sakatlık sıfırlanır

## Geliştirme

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest            # DB ayaktaysa entegrasyon testleri de koşar
```

## Yol haritası

- [x] Aşama 1 — Veritabanı altyapısı ve seed verisi
- [x] Aşama 2 — İstatistiki maç simülatörü
- [x] Aşama 3 — Sezon döngüsü, kalıcılık ve kariyer CLI'ı
- [ ] Aşama 4 — Transfer/bütçe, taktik seçimi, 2D görsel arayüz
