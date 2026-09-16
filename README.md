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
python match_engine.py               # Galatasaray - Fenerbahçe derbisini oynat, DB'yi güncelle
```

Faydalı seçenekler:

```bash
python seed.py --verify-only                     # sadece raporla
python match_engine.py --dry-run --seed 42       # DB'ye yazmadan, tekrar üretilebilir
python match_engine.py --home Inter --away Milan --dry-run   # hazırlık maçı
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
| `tests/` | Unit + entegrasyon testleri (`pytest`), Monte Carlo kalibrasyon sınırları |

## Maç motoru mekanikleri

- Efektif güç: `overall × form × moral` (nötr noktaya göre sönümlenmiş — form farkı yeteneği ezmesin diye)
- Mevkiye göre alt-özellik ağırlıkları (FWD: shooting/pace, MID: passing/dribbling, DEF: defending, GK: goalkeeping)
- Ev sahibi avantajı (itibarla ölçeklenir), yorgunluk/enerji, taktik değişiklikler
- Sarı/kırmızı kart (ikinci sarı = kırmızı), sakatlık → aynı mevkiden yedek, yedek kaleci kuralları
- Uzatma dakikaları, geride kalan takımın son 20 dakikada bastırması
- Asist, maçın adamı, oyuncu maç notları

## Geliştirme

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest            # DB ayaktaysa entegrasyon testleri de koşar
```

## Yol haritası

- [x] Aşama 1 — Veritabanı altyapısı ve seed verisi
- [x] Aşama 2 — İstatistiki maç simülatörü
- [ ] Aşama 3 — Sezon döngüsü: hafta ilerletme, form/moral güncelleme, sakatlık/ceza kalıcılığı
- [ ] Aşama 4 — 2D görsel arayüz
