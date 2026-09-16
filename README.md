# CM — Futbol Menajerlik Simülasyonu

Football Manager / Championship Manager mekanikleriyle çalışan, arka planda istatistiki bir
simülasyon motoru koşturan menajerlik oyunu. **Python 3.10+ · SQLAlchemy 2 · PostgreSQL 16 (Docker)**

Mimari ilke: **Logic ve View katmanları tamamen ayrık.** Motor (`match_engine.py`) veritabanını ve
arayüzü bilmez; terminal spikeri sadece bir "View"dır ve ileride 2D arayüzle değiştirilecektir.

## Kurulum ve çalıştırma

```bash
docker compose up -d                 # PostgreSQL 16 (host port 5433)
pip install -r requirements.txt
python seed.py                       # data/fm/ doluysa FM verisi, değilse kurgusal dünya
python main.py                       # kariyer modu: takım seç, haftaları oyna, puan durumu
streamlit run web_app.py             # canlı maç ekranı (tarayıcıda)
```

Gerçek oyuncu verisi için FM dışa aktarımını `data/fm/` klasörüne koy (bkz. [data/fm/README.md](data/fm/README.md)).
Denemek için paketteki **kurgusal** örnek: `python seed.py --fm-sample`.

Tek maç denemek için: `python match_engine.py` (Galatasaray - Fenerbahçe derbisi).

Faydalı seçenekler:

```bash
python seed.py --verify-only                     # sadece raporla
python match_engine.py --dry-run --seed 42       # DB'ye yazmadan, tekrar üretilebilir
python match_engine.py --home Inter --away Milan --dry-run   # hazırlık maçı
python main.py --team Galatasaray --auto 6 --seed 7          # tam sezonu sormadan oynat
python main.py --team Galatasaray --formation 4-3-3 --auto-lineup --show-tactics
python main.py --team Galatasaray --show-finance --show-staff
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
| `tactics.py` | Diziliş kuralları, kadro doğrulama, asistan menajerin en iyi 11 seçimi |
| `finance.py` | Piyasa değeri/maaş eğrileri, iki kalemli bütçe, 52 haftalık kaydırma kuralları |
| `staff.py` | Teknik heyet alt özellikleri (1-20) ve oyuna etkileri (sağlıkçı/gözlemci/antrenör) |
| `transfers.py` | Bonservis değerlemesi, kulüp kararı, sözleşme masası, ikna formülü, AI hedef seçimi |
| `fm_parser.py` | FM dışa aktarımlarını (HTML / TXT / CSV) okur; sütun eşleme, para/maaş/mevki ayrıştırma |
| `club_directory.py` | Kulüp → lig/itibar rehberi, yazım farklarına dayanıklı isim eşleme |
| `ratings.py` | FM 1-20 özellikleri ve CA → motor özellikleri (1-99) ve genel güç |
| `reputation.py` | Menajer tanınırlığı (1-20): maç ve sezon sonu kuralları |
| `match_feed.py` | Maç sonucunu canlı akış karelerine çeviren görünüm modeli (arayüzden bağımsız) |
| `web_view.py` / `web_app.py` | Canlı maç ekranının HTML parçaları / Streamlit uygulaması |
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

## Taktik ve gelişim döngüsü

- Diziliş: **4-4-2 / 4-3-3 / 3-5-2**. Seçim maç motorundaki hücum-savunma dengesini değiştirir
  (4-3-3: hücum ×1.08, savunma ×0.94 · 3-5-2: orta saha ×1.06, savunma ×0.93)
- İlk 11, yedek kulübesi ve kadro dışı `players.lineup_status` / `lineup_role` ile kalıcı
- Sakat/cezalı oyuncu kadro ekranında seçilemez; seçilmeye çalışılırsa değişiklik **uygulanmaz**
- "Asistana bırak": `overall × form × moral` en yüksek uygun 11'i dizer; eksik slotu maç anında da
  asistan tamamlar ve gerekçesini maç raporuna yazar
- Form/moral döngüsü: not ≥ 7.0 → form ve moral yükselir; not < 6.0 → moral düşer (galibiyette bile);
  kazanan takıma küçük form bonusu. Oynamayanın formu kademeli olarak 50'ye kayar
  (1. hafta 2, 2. hafta 3, ... en çok 6) ve 3+ haftadır oynamayanın morali de düşer

## Finans, transfer ve teknik heyet

**İki kalemli bütçe.** `teams.transfer_budget` (bonservis kasası) ve `teams.wage_budget`
(haftalık maaş havuzu) ayrıdır ve **52 hafta** çarpanıyla birbirine dönüşür — haftalık 10K
maaş alanı açmak transfer bütçesinden 520K götürür. Havuz mevcut maaş yükünün altına
inemez, kasa eksiye düşemez. Her hafta `wage_budget − wage_bill` farkı transfer kasasına
yansır: artan birikir, bütçe aşımı kasadan düşer.

**İki aşamalı transfer.** Önce satıcı kulüp bonservis teklifini değerlendirir (piyasa değeri,
kadro önemi, sözleşme süresi, itibar farkı → olasılık). Kabul ederse **sözleşme masası**
açılır: oyuncu haftalık maaş, süre ve kadro rolü (Yıldız/As/Yedek) talep eder; menajer tur
tur pazarlık eder. Kırmızı çizginin altına düşen teklif ya da hakaret sayılan rol masayı
dağıtır. AI kulüpler de kendi bütçeleriyle pazara çıkar, maaş alanı yetmezse arka planda
bütçe kaydırır. Bir oyuncu bir haftada yalnızca bir kez el değiştirebilir.

**Teknik heyet** (`staff` tablosu, 1-20 arası alt özellikler; boştaki personel havuzu):

| Rol | Özellikler | Oyuna etkisi |
|---|---|---|
| Antrenör | Hücum, Savunma, Taktiksel, Gençlerle Çalışma | Maç sonu form değişiminin çarpanı (hücum/savunma ayrı) |
| Gözlemci | Yetenek/Potansiyel Değerlendirme | Rakip oyuncuların OVR ve özellikleri yanılma payıyla aralık olarak görünür |
| Sağlıkçı | Tedavi Yeteneği | Sakatlık süresi çarpanı — 20 puanlı sağlıkçı 4 haftayı 2 haftaya indirir |
| Asistan | Adam Yönetimi, Kararlılık, Taktiksel Bilgi | Moral değişiminin çarpanı |

Personel maaşları da aynı haftalık havuzdan ödenir; menüden işe alınıp gönderilebilir.

## Gerçek FM verisi, menajer tanınırlığı ve canlı maç

**FM verisi.** `fm_parser.py` FM'in Print Screen çıktılarını (Web Page HTML, Text File, CSV)
UTF-8 / UTF-16 / Windows-1254 kodlamalarıyla okur. `Fin`/`Finishing`/`Bitiricilik` gibi başlıklar
aynı özelliğe gider; `Nat` ve `Pos` gibi belirsiz kısaltmalar değerlere bakılarak çözülür. Kulüpler
rehberden lige yerleştirilir (`Bayern Münih` = `FC Bayern München`), eksik kadrolar altyapı
oyuncularıyla oynanabilir hale getirilir, ham FM özellikleri `players.fm_attributes` (JSONB)
sütununda saklanır. Dışa aktarım dosyaları repoya girmez.

**İkna formülü.** `İkna = Takım itibarı × 0.4 + Menajer tanınırlığı × 0.3 + Maaş çarpanı × 0.3`.
Üç bileşen de 0-100'e normalize edilir (normalize edilmeseydi takım itibarı tek başına skoru
belirlerdi). Oyuncunun beklentisi overall'a bağlıdır: kulüp + menajer prestiji yetmezse oyuncu
bonservis ödenmiş olsa bile *"Kulübün hedefleri benimle uyuşmuyor"* ya da *"Bu menajerle
çalışmak istemiyorum"* diyerek masaya oturmaz. Prestij beklentiyi aştıkça oyuncu daha düşük
maaşa ikna olur. Beklenenden düşük kadro rolü önerilirse maaş talebi %25 fırlar.

**Menajer tanınırlığı** (`game_state.manager_reputation`, 1-20, başlangıç 8): galibiyet artırır,
güçlü rakibi yenmek bonus verir, ağır yenilgi düşürür; sezon sonunda şampiyonluk +2.
AI kulüplerinin menajer tanınırlığı kulüp itibarından türetilir.

**Canlı maç** (`streamlit run web_app.py`): motor maçı anında oynatır, `match_feed` olayları
kümülatif skor/istatistikli karelere çevirir, arayüz bunları zamanlayarak oynatır. Skor tabelası,
ilerleme çubuğu, renkli olay akışı, anlık istatistikler; gol ve kırmızı kartta parlayan uyarılar.
Hazırlık maçı veritabanına yazmaz; kariyer modu haftayı kalıcı oynatır.

## Geliştirme

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest            # DB ayaktaysa entegrasyon testleri de koşar
```

Testler oyun veritabanına **dokunmaz**: `tests/conftest.py` ayrı bir `fm_db_test` veritabanı
oluşturur ve her çalıştırmada kurgusal dünyayla doldurur. Kariyer kaydın ve FM verin güvende.

## Yol haritası

- [x] Aşama 1 — Veritabanı altyapısı ve seed verisi
- [x] Aşama 2 — İstatistiki maç simülatörü
- [x] Aşama 3 — Sezon döngüsü, kalıcılık ve kariyer CLI'ı
- [x] Aşama 4 — Taktiksel kontrol, form/moral döngüsü ve asistan menajer
- [x] Aşama 5 — İki kalemli finans, bütçe kaydırma, iki aşamalı transfer pazarı, teknik heyet
- [x] Aşama 6 — Gerçek FM verisi, menajer tanınırlığı ve ikna formülü, canlı maç web arayüzü
- [ ] Aşama 7 — 2D saha görselleştirmesi, arayüzden kariyer yönetimi
