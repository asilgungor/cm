# OFM — Online Football Manager

Football Manager / Championship Manager / Soccer Manager mekanikleriyle çalışan, tarayıcıdan
oynanan çok kullanıcılı menajerlik oyunu. Arka planda istatistiki bir simülasyon motoru koşar.
**Python 3.10+ · SQLAlchemy 2 · PostgreSQL 16 (Docker) · Streamlit**

Mimari ilke: **Logic ve View katmanları tamamen ayrık.** Motor (`match_engine.py`) veritabanını ve
arayüzü bilmez; web paneli ve terminal spikeri yalnızca birer "View"dır.

## Yerelde adım adım çalıştırma

Gerekenler: **Docker Desktop** (açık ve çalışır durumda), **Python 3.10+** (3.13 ile test edildi), Git.

**1. Kodu indir**

```bash
git clone https://github.com/asilgungor/cm.git
cd cm
```

**2. Veritabanını başlat** (PostgreSQL 16, host portu 5433)

```bash
docker compose up -d
docker compose ps
```

`fm_postgres` satırında `healthy` görünene kadar birkaç saniye bekle.

**3. Ortam dosyasını oluştur** (bağlantı bilgisi; varsayılanlar Docker ayarlarıyla aynıdır)

```bash
cp .env.example .env
```

Windows PowerShell'de: `Copy-Item .env.example .env`

**4. (Önerilir) Sanal ortam ve bağımlılıklar**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell'de etkinleştirme: `.venv\Scripts\Activate.ps1`

**5. Dünyayı kur** (yalnızca ilk kurulumda ya da sıfırdan başlamak istediğinde — mevcut kariyeri siler)

```bash
python seed.py
```

`data/fm/` doluysa FM verisi (oyuncu adları maskelenerek) yüklenir, boşsa kurgusal dünya kurulur.
Paketteki kurgusal FM örneğiyle denemek için: `python seed.py --fm-sample`.

**6. Oyunu aç**

```bash
python -m streamlit run web_app.py
```

Tarayıcıda **http://localhost:8501** açılır.

**7. İlk giriş**

- Giriş sayfasında **"Hemen kayıt ol!"** ile menajer hesabı aç (parola scrypt ile özetlenerek saklanır).
- **İlk kayıt olan menajer** mevcut kariyeri devralır; sonraki her menajere kendi dünyası
  (ayrı PostgreSQL şeması) kurulur — kariyerler birbirine karışmaz.
- **Oyun modunu seç** (Kariyer Modu / Turnuva Modu), kenar çubuğundan kulübünü seç ve
  **Haftayı oyna** ile başla. Tema (⚽ FM Dark / ☀️ FM Light) kenar çubuğundan değişir.

**Durdurmak / yeniden başlatmak**

- Oyunu kapatmak: terminalde `Ctrl+C`.
- Veritabanını durdurmak: `docker compose stop` (veriler `fm_postgres_data` biriminde kalır).
- Ertesi gün devam: `docker compose up -d` ardından `python -m streamlit run web_app.py`.
- Her şeyi silip sıfırlamak: `docker compose down -v` (tüm kariyerler silinir).

**Sık karşılaşılan sorunlar**

| Belirti | Çözüm |
|---|---|
| "Veritabanına bağlanılamadı" | Docker Desktop açık mı? `docker compose up -d` ve `docker compose ps` |
| Port 5433 dolu | `docker-compose.yml` ve `.env` içindeki portu birlikte değiştir |
| Port 8501 dolu | `python -m streamlit run web_app.py --server.port 8502` |
| "eksik sütun" / şema uyarısı | Uygulamayı yeniden başlat (eklenen sütunlar kendiliğinden gelir); olmazsa `python seed.py` |
| Kod güncellendi ama ekran eski | Streamlit'i `Ctrl+C` ile durdurup yeniden başlat |

Eski bir kayıt açıldığında yeni sürümün sütunları kendiliğinden eklenir, `python seed.py` gerekmez
(kariyer silinmez). `main.py` terminal arayüzü hâlâ çalışır ama ikincildir.

Gerçek oyuncu verisi için FM dışa aktarımını `data/fm/` klasörüne koy (bkz. [data/fm/README.md](data/fm/README.md)).

Tek maç denemek için: `python match_engine.py` (Istanbul Lions - Kadıköy Canaries derbisi).

Faydalı seçenekler:

```bash
python seed.py --verify-only                     # sadece raporla
python match_engine.py --dry-run --seed 42       # DB'ye yazmadan, tekrar üretilebilir
python match_engine.py --home "Milano Nerazzurri" --away "Milano Rossoneri" --dry-run   # hazırlık maçı
python match_engine.py --home "Madrid Blancos" --away "München Roten" --knockout        # eleme: uzatma + penaltı
python main.py --team "Istanbul Lions" --auto 7 --seed 7     # tam sezonu sormadan oynat
python main.py --team Galatasaray --formation 4-3-3 --auto-lineup --show-tactics   # gerçek adla da bulunur
python main.py --team "Istanbul Lions" --show-finance --show-staff
python main.py --mode tournament --show-arena                # turnuva modu, Devler Arenası durumu
python main.py --new-season                                  # sezon bittiyse yenisini başlat
```

`.env` dosyası `DATABASE_URL` içerir (bkz. `.env.example`). Port 5433, makinede 5432'yi kullanan
başka bir container ile çakışmamak için seçildi.

**Testleri yerelde koşmak** (oyun veritabanına dokunmaz, ayrı `fm_db_test` kurar):

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

## Sunucu olarak çalıştırma (Windows)

Deneme ve küçük gruplar için uygulama, Windows oturumu açılınca kendiliğinden başlayan bir gözcüyle çalışır
(yönetici yetkisi gerekmez):

```powershell
powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Install   # kur + başlat (Görev Zamanlayıcı: "OFM Server")
powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Status    # durum ve son günlük satırları
powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Restart   # kod güncellemesinden sonra
powershell -ExecutionPolicy Bypass -File ops\ofm_server.ps1 -Action Uninstall # durdur, otomatik başlatmayı kaldır
```

Gözcü (`ops/run_server.py`, `pythonw` ile penceresiz): PostgreSQL erişilemiyorsa Docker Desktop'ı açar ve
`docker compose up -d` çalıştırır; Streamlit'i başlatır, kapanırsa ya da sağlık ucu (`/_stcore/health`) yanıt
vermezse yeniden başlatır; her 5 dakikada `main.py world-tick --all` ile süresi dolan paylaşılan dünyaların
haftasını oynatır. Tek kopya çalışır; günlükler `logs/` altındadır. Dosya izleyici kapalıdır: kod değişikliği
çalışan oturumları yeniden başlatmaz, güncellemeden sonra `-Action Restart` gerekir.

Neden Windows servisi ya da IIS değil: veritabanı Docker Desktop'ta çalışır ve Docker Desktop kullanıcı oturumu
açılınca başlar; oturumdan önce başlayan bir servis veritabanını bulamaz. IIS yalnızca ters vekil (URL Rewrite +
ARR + WebSocket) olurdu ve Python sürecini yine ayrıca yönetmek gerekirdi. İnternete açık kalıcı bir sunucu için
doğru yol Linux üzerinde konteyner + HTTPS sonlandıran ters vekildir (ör. Caddy/nginx).

**Aynı ağdan erişim.** Uygulama tüm arayüzlerde 8501 portunu dinler; Windows Güvenlik Duvarı gelen bağlantıyı
varsayılan olarak engeller. Yerel ağdaki başka cihazlardan denemek için yönetici PowerShell'de:

```powershell
New-NetFirewallRule -DisplayName "OFM 8501 (yerel ag)" -Direction Inbound -Protocol TCP -LocalPort 8501 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

Kural yalnızca **Özel** ağ profilinde geçerlidir (ağın Genel ise Ayarlar → Ağ'dan Özel yapın). Bağlantı HTTP'dir
(şifrelenmez): modeme port yönlendirerek internete açmayın.

## Dosyalar

| Dosya | Görev |
|---|---|
| `database.py` | Engine (connection pool), `SessionLocal`, `Base`, `session_scope()` |
| `models.py` | `League`, `Team`, `Player`, `Fixture`, `Tournament`, `CupTie` ORM modelleri, CHECK/UNIQUE constraint'ler |
| `seed.py` | Deterministik başlangıç verisi; takım güç bantlarından tutarlı oyuncular üretir |
| `match_engine.py` | Maç motoru (dakika dakika adım, canlı müdahale, eleme kuralı: uzatma + penaltı) + DB adaptörü + terminal spikeri |
| `auth.py` | Parola kuralları ve scrypt özetleme (tuzlu, sabit zamanlı doğrulama) (saf) |
| `accounts.py` | Kayıt, giriş, hesap kilidi, kullanıcı başına kariyer şeması kurulumu |
| `development.py` | Potansiyel, wonderkid, haftalık gelişim ve yaşlanma gerilemesi (saf) |
| `youth.py` / `name_pools.py` | Sezonluk genç girişi (ülkeye uygun isimler) ve başlangıç akademileri (saf) |
| `stars.py` | Güç/potansiyel → 5 yıldız (⭐ / 💫) ölçeği (saf) |
| `ofm_theme.py` | OFM temaları (⚽ OFM Dark / ☀️ OFM Light): CSS, giriş sayfası çizimi, kontrast kontrolü (saf sunum) |
| `facilities.py` | Tesisler (altyapı / sağlık merkezi / stadyum), maç günü ve sezonluk bilet geliri, amorti süresi, sponsor teklifleri (saf) |
| `concerns.py` | Oyuncu memnuniyeti: süre beklentisi, şikayet kademeleri, maaş talepleri (saf) |
| `team_roles.py` | Kaptan, penaltı / serbest vuruş / korner atıcıları ve asistan önerisi (saf) |
| `match_plan.py` | Durumsal maç planı: dakika + skor koşullu diziliş / talimat / oyuncu değişikliği kuralları (saf) |
| `match_preview.py` / `squad_planner.py` | Maç önü raporu, rakip gözlem raporu, kadro planlayıcı kuralları (saf) |
| `preview_views.py` | Maç önü raporu, gözlem raporu ve kadro planı için salt okunur veritabanı görünümleri |
| `live_match.py` | Canlı maç kontrolcüsü: durdur/devam, otomatik durma, değişiklik kuralı, müdahale satırları (saf) |
| `instructions.py` | Takım talimatları: zihniyet, sertlik, pas stili, tempo, pres, hücum yönü, ofsayt taktiği, kontra atak; yapay zekâ talimatları (saf) |
| `penalties.py` | Seri penaltı atışları: sıra, erken bitiş, ani ölüm, eşitleme kuralı (saf) |
| `tournament_manager.py` | Devler Arenası kontrolcüsü: katılım, kura, fikstür, eleme, kupa cezaları |
| `cup_draw.py` | Kupa kuralları: torbalar, kısıtlı interaktif kura, takvim, ağaç, grup sıralaması (saf) |
| `bracket_view.py` / `arena_views.py` | Kura panosu, turnuva ağacı ve grup tablosu HTML'i / arena satırları |
| `name_masking.py` | Telifsiz isim ara katmanı: gerçek kulüp/lig/oyuncu adlarını maskeler |
| `career_manager.py` | Sezon döngüsü: haftayı oynat, form/moral, sakatlık, ceza, gol krallığı, yeni sezon |
| `tactics.py` | Diziliş kuralları, kadro doğrulama, asistan menajerin en iyi 11 seçimi |
| `finance.py` | Piyasa değeri/maaş eğrileri, iki kalemli bütçe, 52 haftalık kaydırma kuralları |
| `staff.py` | Teknik heyet alt özellikleri (1-20) ve oyuna etkileri (sağlıkçı/gözlemci/antrenör) |
| `transfers.py` | Bonservis değerlemesi, kulüp kararı, sözleşme masası, ikna formülü, AI hedef seçimi |
| `transfer_rules.py` | 13H transfer masası kuralları: dönemler, yapılandırılmış teklif (peşin/taksit/ek ödeme/sonraki satış payı), kulüp tutumu ve pazarlık, isteklilik, sağlık kontrolü, gözlem bilgisi (saf) |
| `transfer_desk.py` | 13H transfer masası kontrolcüsü (AI kulüpleriyle): gözlem, bilgi alma, teklif/karşı teklif, menajerle kişisel şartlar, sağlık, dönem, para defteri (taksit, ek ödeme, pay), gelen AI teklifleri, serbest kalma bedeli |
| `fm_parser.py` | FM dışa aktarımlarını (HTML / TXT / CSV) okur; sütun eşleme, para/maaş/mevki ayrıştırma |
| `club_directory.py` | Kulüp → lig/itibar/maskeli ad rehberi, yazım farklarına dayanıklı isim eşleme |
| `ratings.py` | FM 1-20 özellikleri ve CA → motor özellikleri (1-99) ve genel güç |
| `reputation.py` | Menajer tanınırlığı (1-20): maç ve sezon sonu kuralları |
| `match_feed.py` | Maç sonucunu canlı akış karelerine çeviren görünüm modeli (arayüzden bağımsız) |
| `web_view.py` / `web_app.py` | Panel HTML parçaları / sekmeli Streamlit menajer paneli |
| `career_views.py` | Panelin veri satırları: kadro, puan durumu, gözlemci sisli pazar, heyet etkileri |
| `pitch.py` | 2D saha: maç olaylarından sahne üretimi ve animasyonlu SVG (saf, kütüphanesiz) |
| `fitness.py` | Dinamik kondisyon kuralları: yorgunluk çarpanı, not cezası, haftalık toparlanma |
| `main.py` | Kariyer CLI'ı (View): hafta, yaklaşan maç, puan durumu, kadro, menü |
| `schedule.py` | Çift devreli fikstür üretimi (saf fonksiyon) |
| `worlds.py` | Dünya kaydı (12. Aşama): kişisel/paylaşılan dünya, oluşturma, davet kodu, açık dünyalar, üyelik ve roller |
| `seats.py` | Menajer koltukları: kulüp talebi, hazır bayrağı, kaçırılan tur, kulüp koruması, adil oyun puanı |
| `world_rules.py` / `turn_rules.py` | Dünya kuralları ve kilit takvimi / tur kararları, kulüp uygunluğu (saf) |
| `world_manager.py` | Tur motoru: Hazırım / süre dolunca / yönetici zorlamasıyla haftayı tek kilitle oynatır |
| `extensions.py` | Kariyer eklenti kancaları (hafta, sezon, oyuncu transferi, kulüp bırakma) |
| `market_rules.py` / `loan_rules.py` / `fair_play.py` | Teklif durum makinesi / kiralık kuralları / adil oyun puanlaması (saf) |
| `market_hub.py` | Menajerler arası pazar: teklif, karşı teklif, takas, kiralık, sözleşme, yönetici incelemesi, geri alma |
| `messaging.py` | Özel mesajlar, dünya panosu, bildirimler, saatlik sınır |
| `intl_calendar.py` / `world_cup.py` / `national_rules.py` | Milli maç takvimi / Dünya Kupası kurası ve eşleşmeleri / uyruk ve kadro kuralları (saf) |
| `national_teams.py` | Milli takımlar: iş teklifleri, kadro çağrısı, eleme maçları, Dünya Kupası |
| `web_common.py` | Web oturum/dünya bağlama, `member_callback` / `admin_callback` güvenlik sarmalayıcıları |
| `world_lobby_view.py` / `world_panel_view.py` / `world_admin_view.py` | Lobi ve kulüp seçimi / kenar çubuğu tur paneli / Dünya Yönetimi sekmesi |
| `market_view.py` / `messages_view.py` / `national_view.py` | Teklifler & Mesajlar / mesaj ve pano / Milli Takım sekmeleri |
| `ops/run_server.py` / `ops/ofm_server.ps1` | Windows sunucu gözcüsü (Streamlit + veritabanı + dünya turu) / kurulum betiği |
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
bütçe kaydırır. Bir oyuncu bir haftada yalnızca bir kez el değiştirebilir. Kulüp kadrosu
çok daralacaksa ya da elinde 2'den az kaleci kalacaksa satışa kapalıdır.

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

**Canlı maç**: motor maçı anında oynatır, `match_feed` olayları
kümülatif skor/istatistikli karelere çevirir, arayüz bunları zamanlayarak oynatır. Skor tabelası,
ilerleme çubuğu, renkli olay akışı, anlık istatistikler; gol ve kırmızı kartta parlayan uyarılar.
Hazırlık maçı veritabanına yazmaz; kariyer modu haftayı kalıcı oynatır.

## Menajer paneli, 2D saha ve dinamik kondisyon

`streamlit run web_app.py` sekmeli bir panel açar (kariyer modunda yedi sekme, Devler Arenası dahil):

| Sekme | İçerik |
|---|---|
| 🏟️ Canlı Maç | 2D saha (oyuncu noktaları, pas zinciri, şut okları, kart/sakatlık işaretleri), skor tabelası, akış, anlık istatistik ve takım kondisyonu. Maç hızı ve animasyon temposu ayarlanır. Modlar: **Maçımı yönet** (haftanın gerçek maçı, canlı müdahale), hazırlık maçı (bir takımı yönet ya da izle), "son maçımı izle". |
| 📋 Kadro & Taktik | Diziliş, asistana kadro kurdurma, taktik tahtası, renkli kondisyon çubukları, tıklanabilir ilk 11 / kulübe tablosu. Sakat/cezalı oyuncu kaydedilemez, düşük kondisyon uyarılır. |
| 💰 Finans | Maaş havuzu kaydırıcısı (52 hafta çarpanıyla anlık önizleme), doluluk çubuğu, bütçe aşımında kırmızı uyarı. |
| 🔄 Transfer Pazarı | Gözlemci sisine sadık arama (filtre ve sıralama tahminler üzerinden), bonservis teklifi, prestij ve rol kısıtlı sözleşme masası, ikna skoru göstergesi. |
| 🏆 Lig | Sonraki haftayı oyna, puan durumu, gol krallığı, haftalık rapor, yeni sezon. |
| 👥 Teknik Heyet | Personel, etkileri (sakatlık süresi, kondisyon toparlanma, gözlemci payı), işe alma/gönderme. |

Veriyi değiştiren her düğme `on_click` callback'i kullanır: işlem sayfa çizilmeden önce çalışır,
böylece hiçbir sekme bayat veri göstermez.

**Dinamik kondisyon** (`players.condition`, 0-100): maçta dakika, yaş, mevki, FM dayanıklılığı ve
efor (şut, asist, faul) ile düşer; düştükçe oyuncunun efektif gücü azalır. Yorgun biten oyuncunun
maç notu düşer, bu da ertesi hafta formunu ve moralini aşağı çeker. Hafta ilerleyince oynamayanlar
%100'e döner, oynayanlar kulübün sağlıkçı kalitesine göre toparlanır. Asistan kadro kurarken
yorgun oyuncuları dinlendirir.

## Devler Arenası (Champions Cup), eleme ve telifsiz dünya

**Oyun modları** (`game_state.game_mode`): ilk girişte seçilir, yalnızca sezon başında (hiç maç
oynanmamışken) değiştirilebilir.

| Mod | Akış |
|---|---|
| `CAREER_MODE` | Her hafta önce o haftanın kupa maçları (hafta içi), sonra lig maçları (hafta sonu). Kupa takvimi lig haftalarına yayılır; sezon lig ve kupa bitince biter. |
| `TOURNAMENT_MODE` | Sadece Devler Arenası: her hafta bir kupa günü. Finans ve transfer yok; "Yeni turnuva" ile yeniden başlar. |

**Katılım ve kura.** 16 takım: her ligin 1.'leri, sonra 2.'leri… (lig gücüne göre sıralı).
İlk sezonda itibar, sonrakilerde bir önceki lig sıralaması kullanılır. Format **direkt eleme**
(Son 16) ya da **4'erli gruplar + eleme**. Kura interaktiftir: her tıklama bir top açar; yeni
eşleşme ya da grup ekranda parlar. Aynı ligden takımlar eşleşmez ya da aynı gruba düşmez.
Her adımda yalnızca kuranın tamamlanmasına izin veren toplar çekilir, böylece kura kilitlenmez.
Kura durumu veritabanına yazılır, sayfa yenilense de kaldığı toptan devam eder;
"Kurayı otomatik çek" kalanını tamamlar.

**Eleme.** Son 16, çeyrek final ve yarı final çift maçlıdır; seri başı rövanşı evinde oynar.
Rövanş sonunda toplam skor eşitse `match_engine` aynı maçı **2×15 dakika uzatmayla** sürdürür
(yorgunluk devam eder, fazladan bir değişiklik hakkı açılır). Eşitlik sürerse **seri penaltı
atışları** oynanır:
- Vuruşlar sırayla atılır; ilk beş atışta kazanan belli olduğu an seri biter, sonra ani ölüme geçilir.
- Kırmızı kart görenler ve oyundan çıkanlar vuruş atamaz; kalabalık taraf oyuncu sayısını eşitler.
- Her vuruş `PENALTY_SHOOTOUT` olayı olarak kaydedilir: atan oyuncu ve sonucu (gol, kurtarış ya da kaçırma).

Final tek maçtır ve tarafsız sahada oynanır.

**Kupa cezaları ligden ayrıdır.** Kupada görülen kırmızı kart yalnızca kupa maçlarına ceza
getirir. Her 3 sarı kart 1 maç cezadır; sarı kart birikimi çeyrek finalden sonra silinir.
Aynı hafta hem kupa hem lig maçı oynayan oyuncunun kondisyonu aradaki kısa sürede yalnızca
yarı yarıya toparlanır, bu da rotasyonu gerekli kılar. Tur atlamak ve kupayı kazanmak menajer
tanınırlığını artırır (şampiyonluk +2.5).

**Telifsiz isim katmanı** (`name_masking.py`). Gerçek kulüp, lig ve oyuncu adları veritabanına
hiç yazılmaz. Adlar FM dışa aktarımı okunurken (`fm_parser`) ve dünya kurulurken (`seed`)
otomatik dönüştürülür:
- Kulüpler: Real Madrid → *Madrid Blancos*, Bayern München → *München Roten*,
  Galatasaray → *Istanbul Lions*.
- Ligler: *İspanya Elit Ligi*.
- Oyuncular: *Erling Haaland* → *Erling Harland* gibi tek harflik değişiklik (ilk isim bütün
  kalır, baş harfe inmez).

Üç seviye vardır — `off`, `light` (**depo varsayılanı**), `strong`:
`--mask-level light|strong` ya da `SEED_NAME_MASKING` ortam değişkeni.
`off` maskelemeyi tamamen kapatır ve **yalnızca kendi lisanslı FM verinle, kendi bilgisayarındaki
kişisel oyun içindir**; kazayla açılmaması için çift onay ister (aşağıya bak).

Seed sırasında sızıntı denetimi yapılır: gerçek bir ad kalmışsa veritabanına dokunulmaz
(`off` seviyesinde gerçek adlar bilerek durduğu için bu kilit uygulanmaz, bunun yerine büyük bir
uyarı basılır). Kurgusal dünya 6 lig × 4 kulüpten oluşur; devlerin iki bütçe kalemi de büyüktür.
Panelde ya da CLI'da gerçek adla arama yapılabilir (`--team Galatasaray` → Istanbul Lions;
maskeleme kapalıyken gerçek adın kendisini bulur).

> Maskeleme hukuki riski azaltır ama hukuki garanti değildir. Hafif harf değişikliği,
> tanınırlığı bilerek korur. FM özellik verisi de maskeli adlarla bile Sports Interactive'in
> lisanslı içeriğidir; dışa aktarımlar yine repoya konmamalı ve paylaşılmamalıdır.

## Canlı maç içi müdahale ve anlık taktik talimatlar

**Maçımı yönet** (🏟️ Canlı Maç) haftanın gerçek maçını canlı oynatır: o hafta kupa maçı varsa önce
o (hafta içi), sonra lig maçı. Kullanıcının maçı hafta içi kupada değilse, lig maçına çıkmadan önce
o haftanın kupa maçları oynanır; böylece rakiplerin kondisyonu günceldir. Motor maçı **dakika
dakika** ilerletir (`MatchEngine.start/step/snapshot`). Müdahale yoksa sonuç, aynı tohumla otomatik
oynatılan maçla bit bit aynıdır (golden regresyon testleri).

| Kontrol | Etki |
|---|---|
| ⏸ DURDUR / ▶ DEVAM | Maç o dakikada durur, kaldığı dakikadan devam eder. Devre arasında, uzatma molalarında ve kendi takımında sakatlık ya da kırmızı kartta kendiliğinden durur (ayarlanabilir). |
| 🔁 Oyuncu değişikliği | Yalnızca maç dururken ya da molada. Kulübedeki sağlıklı oyuncu girer; sakat, atılmış ya da oyundan çıkmış oyuncu giremez. Kaleci ancak kaleciyle değişir. Efektif güç o an güncellenir. |
| Değişiklik kuralı | 5 değişiklik (pencere sınırı yok), **5 değişiklik · en fazla 3 pencere** (IFAB; devre arası pencere saymaz, uzatmada +1 hak ve +1 pencere) ya da klasik **3 değişiklik**. Kural iki takıma da uygulanır. |
| Canlı diziliş | 4-4-2, 4-3-3, 3-5-2 ve maç içi acil durum **5-3-2**. Sahadakiler yeni hatlara dağıtılır (gerekirse mevki dışı cezasıyla), 10 kişiyken önce forvet slotu düşer; diziliş çarpanları kalan dakikalarda geçerlidir. 5-3-2 veritabanına yazılmaz. |
| 🧠 Zihniyet | **Çok Defansif** (hücum ×0.78, savunma ×1.18, daha az koşu) · **Dengeli** · **Çok Ofansif** (hücum ×1.20, savunma ×0.82, yorgunluk ×1.10): şut şansı artar ama savunma açılır. |
| 🧠 Sertlik | **Sakin Kal** (kart ×0.55, sakatlık ×0.75, savunma ×0.95) · Normal · **Sert Oyna** (savunma ×1.08, kart ×2, direkt kırmızı payı ×1.5, maçın sakatlık riski katlanır). Kart daha sert oynayan takıma daha olası çıkar. |
| Asistan | İsteğe bağlı: yorulan oyuncuları asistan değiştirir ya da menajer kendisi yapar. Sakatlıkta asistan her zaman yedek sokar. |

Her müdahale akışa (`SUBSTITUTION` "menajer kararı", `TACTICAL_CHANGE`) ve 2D sahaya yansır.
Diziliş değişikliğinden önceki kareler eski, sonraki kareler yeni dizilişle çizilir. Maç bitince
**💾 Sonucu kaydet** sonucu kariyere işler: skor, puan durumu, istatistik, form/moral/kondisyon,
kupa turu ve tanınırlık otomatik maçtaki kurallarla güncellenir. Canlı oynanan maç yeniden simüle
edilmez; bitmiş canlı maç varken "Sonraki haftayı oyna" da o sonucu kullanır. Kaydedilmemiş
canlı maç varken hafta oynatma, takım ve mod değiştirme, transfer ve teknik heyet işlemleri
kilitlidir. Tohumsuz kariyerde de kullanıcının maçı fikstüre bağlı sabit bir tohum alır: sayfayı
yenileyip maçı baştan "zar atarak" tekrar oynamak, aynı kadro ve talimatlarla aynı maçı verir.
Kura henüz çekilmemiş bir kupa haftasında ekran bunu bildirir; **Maça çık** kurayı otomatik
tamamlar.

## Menajer hesapları, altyapı akademisi ve gelişim

**Hesaplar ve izolasyon.** Kullanıcılar ayrı `accounts` şemasındaki `users` tablosundadır
(`id, username, password_hash, created_at, last_login_at, career_schema`). Parolalar `scrypt`
ile özetlenir (16 bayt rastgele tuz, sabit zamanlı karşılaştırma, düz metin asla saklanmaz).
Her menajerin kariyeri **kendi PostgreSQL şemasındadır**: ilk kullanıcı eski tek kişilik
kariyeri (`public`) devralır, sonrakiler `career_<id>` alır ve `game_state.user_id` kariyeri
sahibine bağlar. Oturum açık olduğu sürece her veritabanı işlemi `SET LOCAL search_path` ile
yalnızca o menajerin şemasına gider. Oyun kodu tablo adlarını nitelemediği için bir menajer
başka bir kariyeri ne okuyabilir ne değiştirebilir. Giriş yapılmadan hiçbir oyun sekmesi çizilmez.
5 hatalı parola hesabı 5 dakika kilitler (sunucu tarafında), tarayıcı oturumu da ayrıca 30 sn bekletir.

**Potansiyel ve wonderkid.** `players.potential_rating` güçle aynı 1-99 ölçeğindedir (FM verisinde
PA'dan türetilir). 16-21 yaşında olup potansiyeli gücünden en az 15 yüksek oyuncu **wonderkid**'dir
(🌟). Gerçek potansiyel gizlidir; ekranda gözlemcinin *Potansiyel Değerlendirme* özelliğine göre
tahmin aralığı gösterilir.

**Gelişim ve yaşlanma** (her hafta, kariyer modunda). Güç artışı: kalan potansiyel farkı × yaş ×
oynama süresi × maç notu × antrenörün *Gençlerle Çalışma* özelliği × moral (akademide tesis kalitesi).
Birikim `development_progress` sütununda tutulur, +1'e ulaşınca güç ve mevkinin önemli özellikleri
kalıcı artar. 17 yaşında 62 güç / 85 potansiyelli bir wonderkid için 7 haftalık sezonlarda:

| Sezon sonu yaşı | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
|---|---|---|---|---|---|---|---|
| Her maç oynarsa | 66 | 71 | 75 | 78 | 81 | 83 | 84 |
| Akademide kalırsa | 64 | 66 | 69 | 71 | 73 | 75 | 76 |
| A takımda oynamazsa | 62 | 63 | 64 | 65 | 66 | 67 | 67 |

32 yaşından sonra gerileme başlar ve hızlanır (sezonda ~1 puan 32'de, ~3.3 puan 36'da); her puan
kaybında önce **hız** (-2) ve top sürme düşer, maç sonrası kondisyon toparlanması da yavaşlar
(32'de ×0.95, 37+ ×0.70). Genç ve yüksek potansiyelli oyuncunun piyasa değeri potansiyeline göre prim alır.

**U-21 akademisi.** `players.in_academy` oyuncuyu A takım kadrosundan ayırır: akademi oyuncuları
maçlara, taktik seçimine ve transfer pazarına girmez (`Team.players` yalnızca A takımını döndürür).
Akademi en fazla 20 kişi, 21 yaş üstü en fazla 3 oyuncu; A takım en fazla 25 kişi. **A Takıma
Yükselt** / **U-21'e Gönder** kuralları: A takımda en az 13 oyuncu ve 2 kaleci kalmalı.
Yeni sezonda yapay zekâ kulüpleri kadrolarını akademiden tamamlar ve en iyi gençlerini yükseltir.

**Genç girişi (Youth Intake).** Her sezon son haftadan bir önceki hafta **her kulübe** 3-4 adet
16-17 yaşında genç katılır. İsimler kulübün ülkesine göre üretilir (Türkiye'de Türkçe isimler);
potansiyel dağılımı altyapı tesislerine (1-20) ve itibara bağlıdır: çoğu ortalama, bazen gerçek
bir cevher (tesis 3 → %5.5 wonderkid, tesis 18 → %22).

**Yıldızlar.** (10. aşamanın CM retro teması 11. aşamada OFM FM Dark / FM Light temalarıyla değiştirildi.)
Kadro, transfer pazarı ve akademide sayısal güç gösterilmez; güç ve potansiyel 5 yıldızla görünür:
80+ ⭐⭐⭐⭐⭐, 75-79 ⭐⭐⭐⭐💫, 70-74 ⭐⭐⭐⭐, … (her bandın üst yarısı 💫 yarım yıldız).

## Kura gecesi, OFM teması, kulüp tesisleri ve sponsorluk (11. Aşama)

**Kura gecesi.** Devler Arenası kurası tek tek topla çekilir: her tıklama tek bir top açar — eleme kurasında
ilk top ilk maçın ev sahibi, ikinci tıklama rakibini açar ve eşleşme kartı parlar; grup kurasında her tık bir
takım yerleştirir. Her top kendi tohumuyla çekildiğinden top top ya da otomatik çekmek aynı kurayı verir. "Kurayı otomatik çek" kalanı
tamamlar. Kura bitince ilk tur fikstürü doğrulanır (`draw_fixture_problems`: her takım tek eşleşmede,
iki ayak ev/deplasman değişir, takvim haftaları doğru, tekrar yok) ve **kilitlenir**; aynı anda iki
tıklama satır kilidiyle (`SELECT … FOR UPDATE`) sıraya girer. Her menajerin kurası kendi kariyer şemasındadır.

**OFM teması ve giriş sayfası.** Uygulamanın resmi adı **Online Football Manager (OFM)** (tarayıcı sekmesi ve
sayfa başlığı). Kenar çubuğundan (girişte sağ üstten) **⚽ OFM Dark** / **☀️ OFM Light** seçilir; seçim `st.session_state.theme` ve `?theme=`
adres parametresinde tutulur (sayfa yenilense de, giriş/çıkışta da kalır). Metin/zemin kontrastı testlerle
WCAG sınırlarında tutulur. Giriş sayfası mor gradyan, eğik üçgenler ve çizim bir top kullanır (fotoğraf yok).

**Maskeli isimlendirme.** FM verisindeki oyuncu adları veritabanına yazılmadan önce hafifçe maskelenir
(`SEED_NAME_MASKING=light`, varsayılan): her isimde tek değişiklik — uzun soyadı kısaltma (Çalhanoğlu →
Çalhano, Lewandowski → Lewandow), çift sesli (Haaland → Harland), "au/ou" (Mauro → Muro) ya da sesli
kayması (Orkun → Orkan, Mbappé → Mbeppe, Osimhen → Osemen). İlk isim bütün kalır: *Erling Haaland* →
*Erling Harland* (baş harfe inmez). Güç, potansiyel, yaş ve 1-20 özellikler
değişmez; gerçek ad hiçbir sütuna yazılmaz (entegrasyon testi tüm metin sütunlarını tarar). Tamamen kurgusal
isimler için `SEED_NAME_MASKING=strong`.

**Maskelemeyi kapatmak (`off`) — kişisel, yerel oyun.** Kendi lisanslı Football Manager oyunundan
aldığın listeyle gerçek kulüp, lig ve oyuncu adlarıyla oynamak istersen:

```bash
$env:OFM_ALLOW_REAL_NAMES = "1"        # 1. onay: gerçek isimlere izin (PowerShell)
python seed.py --source fm --mask-level off   # 2. onay: seviyeyi açıkça seç (mevcut kariyeri siler)
```

Kazayla açılamaz: `SEED_NAME_MASKING=off` tek başına yetmez, bayrak da verilmelidir; izin
değişkeni yoksa seed **veritabanına dokunmadan** hata verir. Geçersiz bir seviye her zaman
`light`'a düşer. Seçilen seviye dünyaya yazılır (`game_state.mask_level`), böylece doğrulama
raporu ve arayüz dünyanın gerçek isimler içerdiğini bilir.

> `off` ile kurulan dünya **kişiseldir ve tek koltukludur**: paylaşılan (çok menajerli) dünyaya
> çevrilemez (`worlds.py` reddeder). Veritabanı dökümü/yedeği, FM dışa aktarımları ve gerçek adlı
> ekran görüntüleri asla repoya eklenemez, yayımlanamaz, paylaşılamaz. Depo varsayılanı `light`
> olarak kalır; `off` yalnızca senin makinendeki (gitignore'daki) `.env` dosyasında açılır.

**Kulüp Yönetimi & Tesisler** sekmesi (bedeller transfer bütçesinden düşer):

| Tesis | Etki | Yükseltme bedeli |
|---|---|---|
| Altyapı (1-20) | Genç girişinin ortalama potansiyeli (~+0.45 / seviye) ve akademi gelişim hızı | 800K × 1.16^(seviye-1) |
| Sağlık merkezi (1-20) | Maç sonrası kondisyon toparlanma hızı: 1 → ×0.82, 10 → ×1.00, 20 → ×1.30 (fizyoterapistle çarpılır); sakatlık süresi aynı çarpana bölünür (4 hafta: 1 → 5, 10 → 4, 20 → 3) | 700K × 1.16^(seviye-1) |
| Stadyum (10.000-90.000) | İç saha maç günü geliri = min(kapasite, taraftar talebi) × bilet getirisi | +5.000 koltuk: 2.5M (10K) … 6.25M (85K) |

Taraftar talebi itibara bağlıdır; talebin üstündeki koltuk gelir getirmez. Bilet geliri her iç saha maçında
transfer bütçesine girer; sekme genişletmenin sezonluk ek bilet gelirini (iç saha lig maçı sayısıyla) ve bedelini
kaç sezonda geri ödeyeceğini gösterir — sonrası her sezon transfer bütçesine net katkıdır. **Sponsorluk:** her sezon başı
itibara göre üç teklif gelir — yüksek haftalık (1 sezon), uzun vade (3-4 sezon + küçük prim), imza primi
(2 sezon + büyük peşin prim). Menajer birini imzalar; yapay zekâ kulüpleri en değerli teklifi kendileri
seçer ve sezon başında bütçelerinin %5'ini aşmayan bir tesis yatırımı yapabilir. Markalar kurgusaldır.

## Soccer Manager incelemesinden gelen özellikler

`soccermanager.com` (SM 2027 ve SM Worlds yardım makaleleri) incelenerek oyunda olmayan ve OFM'ye uyan
mekanikler eklendi:

**🎯 Taktik Merkezi** sekmesi:
- **Takım talimatları:** zihniyet ve sertliğe ek olarak pas stili (kısa / karışık / direkt), tempo, pres
  (kendi yarı sahası / orta saha / tüm saha), hücum yönü (merkez / kanatlar), ofsayt taktiği ve kontra atak.
  Etkiler rakibe bağlıdır: tüm sahada pres rakip orta sahayı boğar ama yorar; kontra atak tam hücuma çıkan
  rakibe karşı güçlenir; ofsayt taktiği rakip forvetlerin hızına bakar; kanatlar orta/kafa gücünü kullanır.
  Varsayılan talimatlarla motor eski sürümle bit bit aynıdır.
- **Kaptan ve duran toplar:** penaltı, serbest vuruş ve korner atıcısı; kaptan sahadayken kart riski azalır
  ve geride kalınan son dakikalarda takım dağılmaz. "Asistan belirlesin" en uygun oyuncuları seçer.
- **Maç planı:** en fazla 5 kural — "60. dakikada gerideysek 4-3-3 + Çok Ofansif, X çıksın Y girsin".
  Kurallar her dakika kontrol edilir, bir kez uygulanır, değişiklik sınırlarına uyar.
- **Kayıtlı taktikler:** en fazla 7 taktik (diziliş, ilk 11 ve kulübe, talimatlar, görevler, plan).
- **Maç önü raporu:** iki takımın sırası, son 5 maç formu, iç saha/deplasman karnesi, aralarındaki
  maçlar, sakat ve cezalılar, dikkat edilecek oyuncular ve kağıt üstü yorum.
- **Rakip gözlem raporu:** tahmini diziliş ve ilk 11; isabeti gözlemcinin *Yetenek Değerlendirme*
  özelliğine bağlıdır (gözlemcisiz ~8.5/11, 20 puanlık gözlemciyle ~10.7/11 doğru oyuncu).
- **Hazırlık maçı:** haftada bir; sakatlık/kart yok, kondisyon düşmez, puan tablosunu etkilemez.
- **Kadro planlayıcı:** mevki gruplarına göre derinlik (Yeterli / İnce / Kritik), sözleşmesi biten ve
  32 yaşını geçecek oyuncularla gelecek iki sezonun projeksiyonu ve takviye önerileri.

**Oyuncu memnuniyeti** (Kadro & Taktik): oyuncular kadro rolüne göre son 8 resmi maçta süre bekler (kupa
yarım sayılır). Oynayan oyuncunun şikayeti ilerlemez; oynamayan önce *Süre bekliyor*, sonra *Şikayetçi*,
en sonunda *Ayrılmak istiyor* olur ve morali haftalık düşer. Gücü 3+ artan ya da piyasanın %60'ının altında
kazanan oyuncu **yeni maaş ister**: kabul maaşı artırır, ret morali düşürür. Şişirilmiş kadroda yedekler
daha çabuk huzursuzlanır (oyuncu istiflemeye karşı).

**Kariyer ekonomisi:** lig gelirinin eşit bölünen **TV payı** (haftalık), sezon sonu **lig sıralama ödülü**
(şampiyon sezonluk TV payı kadar, sonuncu %10'u), Devler Arenası **tur primleri** (şampiyon toplam 12M) ve
**başkan desteği** (kulübün net değeri lig ortalamasının yarısının altına düşerse sezon başı sermaye).

**Transfer koruması:** kulüp değiştiren oyuncu **6 oyun haftası** tekrar satılamaz ve teklif alamaz
(sezon devrinde kesintisiz); yapay zekâ da bu oyuncuları atlar. Transfer kasası ekside teklif yapılamaz.

**⭐ Takip listesi** (Transfer Pazarı) ve **📰 Haberler & Tarih** sekmesi: dünya haber akışı (transferler,
şampiyonluklar, büyük skorlar, sponsor imzaları, akademiden çıkan cevherler), sezon onur listesi (şampiyon,
ikinci, gol kralı, sezonun oyuncusu), kulübün kupaları ve rekor transferler.

**Menajer unvanları:** 1-20 tanınırlık 10 kademeye bölünür (Çaylak → Deneyimli → Profesyonel → Uzman → Elit →
Usta → Efsane → Duayen → Ölümsüz → OFM Efsanesi); kenar çubuğunda rozet ve bir sonraki unvana ilerleme görünür.

Yeni tablolar (`transfer_log`, `season_honours`, `news_items`, `shortlist`, `friendlies`, `tactic_presets`)
ve sütunlar eski kayıtlara girişte kendiliğinden eklenir; hepsi menajerin kendi kariyer şemasındadır.

## Ortak oyun dünyaları (12. Aşama)

**Dünyalar ve lobi.** Her hesabın kişisel kariyeri bir *kişisel dünya*dır ve eskisi gibi çalışır. Kenar
çubuğundaki **🌍 Dünyalar** lobisinden paylaşılan dünya kurulur (ad, görünürlük, koltuk sayısı, en düşük menajer
seviyesi, tur süresi, pazar, milli takımlar, adil oyun sıkılığı), davet koduyla ya da açık dünyalar listesinden
katılınır, kişisel kariyer paylaşılan dünyaya çevrilebilir. Kulüpsüz menajer boştaki kulüplerden seçer;
menajer seviyesi düşükse yalnızca itibarı uygun kulüpler açılır.

**Turlar.** Paylaşılan dünyada hafta düğmesi yerine kenar çubuğunda **Hazırım** paneli vardır: herkes hazır
olunca, süre dolunca (varsayılan 24 saat) ya da sahip/yönetici zorlayınca hafta **bir kez** oynatılır (dünya
kilidi + beklenen hafta kontrolü). Her menajer kendi hafta raporunu görür. Üst üste 3 turu kaçıran menajer 4.
kaçırışta kulübünü kaybeder; kulüp 4 hafta yapay zekâ transferlerine karşı korunur. Oyun kuralları sezonun ilk
maçından sonra kilitlenir. Resmi maçlar paylaşılan dünyada canlı oynanmaz (hafta birlikte simüle edilir);
hazırlık maçları canlı oynanabilir. Sunucu tarafı tetikleyici:

```bash
python main.py world-tick --all     # süresi dolan ya da herkesin hazır olduğu dünyaları ilerletir (cron)
python main.py world-list           # dünyalar: sezon/hafta, hazır sayısı, kalan süre
```

**Menajerler arası pazar.** Başka menajerin oyuncusuna transfer ya da kiralık teklifi yapılır; karşı teklif
(en fazla 4 tur), takas oyuncusu, kiralık süresi ve maaş payı pazarlık edilir. Kabulde **adil oyun denetimi**
çalışır: değerin çok altı/üstü bedel, aynı iki menajer arasında tekrarlanan anlaşmalar, yeni hesaplar ve kısa
sürede çok işlem puanlanır; eşik aşılırsa anlaşma engellenir (iki tarafa adil oyun cezası) ya da yönetici
incelemesine düşer. Onaylanan anlaşma oyuncuyla sözleşme masasına gider; tamamlanınca para ve oyuncular **tek
seferde** el değiştirir (satır kilitleri, kısmi benzersiz indeksler, eşzamanlılık testleri). Yönetici kendi
kulübünün anlaşmasına karışamaz; son 8 haftanın transferlerini geri alabilir. Kiralık oyuncu satılamaz, yeniden
kiralanamaz, akademiye gönderilemez; süresi dolunca ya da sezon sonunda döner, ana kulüp şartlar oluşunca geri
çağırabilir.

**Mesajlaşma.** Menajerler arası özel mesajlar, dünya panosu (yönetici sabitleyebilir) ve bildirimler (tur,
teklif, inceleme, milli takım); saatte 30 mesaj/gönderi sınırı. Kullanıcı metni her yerde kaçırılarak gösterilir.

**Milli takımlar ve Dünya Kupası.** Dünya kurulurken açılırsa uyruklardan milli takımlar oluşur. Menajerlere
milli takım iş teklifleri gelir (menajer başına tek görev); kadro çağrısı (en fazla 30), ilk 11 ve diziliş
yapılır. Eleme maçları lig haftalarının arasına, Dünya Kupası sezon arasına yerleşir; kupa bitmeden yeni sezon
başlamaz. Milli maçlar kulüp oyuncularının kondisyonuna, sakatlığına ve cezasına dokunmaz (yalnızca milli maç ve
gol sayısı yazılır); lig sonuçları milli takımlar açık ya da kapalıyken aynıdır.

## Geliştirme

```bash
pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest            # DB ayaktaysa entegrasyon testleri de koşar
```

Testler oyun veritabanına **dokunmaz**: `tests/conftest.py` ayrı bir `fm_db_test` veritabanı
oluşturur ve her çalıştırmada kurgusal dünyayla doldurur. Sıfırlamadan önce gerçekten bağlanılan
veritabanının adı (`SELECT current_database()`) doğrulanır. `TEST_DB_NAME` ortam değişkeniyle
paralel çalışan test oturumları ayrı veritabanları kullanabilir. Test dünyasını yeniden kuran ya da paylaşılan
dünyaya çeviren modüller bitişte conftest'in varsayılan dünyasını (oyun modu seçilmemiş, sezon başı) geri kurar;
aksi halde sonraki modüller sıraya bağlı düşer.

Projede şema göç (migration) aracı yoktur; bunun yerine eklemeli yükseltme vardır: yeni sütunlar
`database.ADDITIVE_COLUMNS`'a, yeni tablolar modellere eklenir ve menajer giriş yaptığında (ya da `main.py`
açılışında) `upgrade_schema()` bunları mevcut kariyere ekler — kayıt silinmez. Eklemeli olmayan bir
değişiklikte panel ve CLI eski şemayı açıkça bildirir (`eksik sütun: …`); o durumda `python seed.py` gerekir.

## Yol haritası

- [x] Aşama 1 — Veritabanı altyapısı ve seed verisi
- [x] Aşama 2 — İstatistiki maç simülatörü
- [x] Aşama 3 — Sezon döngüsü, kalıcılık ve kariyer CLI'ı
- [x] Aşama 4 — Taktiksel kontrol, form/moral döngüsü ve asistan menajer
- [x] Aşama 5 — İki kalemli finans, bütçe kaydırma, iki aşamalı transfer pazarı, teknik heyet
- [x] Aşama 6 — Gerçek FM verisi, menajer tanınırlığı ve ikna formülü, canlı maç web arayüzü
- [x] Aşama 7 — Dinamik kondisyon, 2D saha görselleştirmesi, web tabanlı kariyer paneli
- [x] Aşama 8 — Devler Arenası (Champions Cup), uzatma/penaltı, interaktif kura, telifsiz isim katmanı
- [x] Aşama 9 — Canlı maç içi müdahale: durdur/devam, oyuncu değişikliği, canlı diziliş, zihniyet ve sertlik talimatları
- [x] Aşama 10 — Menajer hesapları ve kariyer izolasyonu, potansiyel/wonderkid, gelişim ve yaşlanma, U-21 akademisi, genç girişi, CM retro teması ve yıldız sistemi
- [x] Aşama 11 — Kura gecesi, OFM teması (OFM Dark/Light) ve giriş sayfası, maskeli isimlendirme, kulüp tesisleri ve sponsorluk, Soccer Manager paketi (taktik merkezi, maç önü raporu, kadro planlayıcı, oyuncu memnuniyeti, TV/ödül ekonomisi, transfer koruması, haberler ve tarih, takip listesi, hazırlık maçı)
- [x] Aşama 12 — Ortak oyun dünyaları: lobi ve davet kodu, çok menajerli tur motoru (Hazırım / süre / yönetici), menajerler arası transfer ve adil oyun denetimi, kiralık/takas, mesajlaşma, milli takım yönetimi ve Dünya Kupası; kapanışta sağlık merkezinin sakatlık etkisi, stadyum amorti göstergesi ve top top kura
- [x] Aşama 13 — CM 01/02 ve FM12 kıyasıyla yeniden düzenleme: maç motoru doğruluğu (zayıf halka, varyans, duran toplar, tempo), zincirli maç anlatımı ve maç raporu verisi, açık veriyle (CC0) 6 lig ve 114 gerçek kulüp, ülke → lig → kulüp seçimi ve kulüp kilidi, sürükle-bırak taktik tahtası, profesyonel transfer masası (müzakere dosyası, taksit, bonus, serbest kalma bedeli, gözlem, ödeme defteri), CM tarzı sol menü, Ana Sayfa ve Transfer Merkezi, 31 özellikli (1-20) CM 01/02 oyuncu profili ve gözlemci sisi
- [x] Aşama 14 — Maç günü ve oyuncu farkı: CM 01/02 maç ekranı ve olay güdümlü hareketli 2D saha, 31 özelliğin motorda okunması, açık veri dünyası varsayılan, hafta hızı (6,2 → 2,1 sn), taktik etkisi ve karşı hamle, Bul ve tıklanabilir kulüp/lig/ülke sayfaları, yoğun tablolar ve tek sis modeli, oturum sürdürme; ayrıca 'OFM Klasik' (CM 01/02) görünümü, gerçek kulüp renkleri, gerçek oyuncu kadrosu altyapısı ve sözleşme döngüsü (serbest kalma, Bosman, yenileme, fesih)
- [ ] Aşama 15 — Sonuçları olan kariyer: sözleşme döngüsü ve serbest oyuncular, emeklilik, yönetim kurulu ve kovulma, kalıcı gelen kutusu ve takvim, alt mevkiler ve bireysel talimatlar
- [ ] Aşama 16 — Derin dünya: lig piramidi ve küme düşme, kupalar, antrenman programları, gözlem ağı, dünya dengesi
- [ ] Aşama 17 — Bölge modeli ve gerçek 2D saha
