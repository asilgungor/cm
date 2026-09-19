# FM veri klasörü

`python seed.py` bu klasördeki Football Manager dışa aktarımlarını okur ve oyun dünyasını
**kendi oyuncu listenden** kurar. Klasörde dışa aktarım yoksa kurgusal (sentetik) dünyaya düşer.

> Dışa aktarım dosyaları `.gitignore` ile repodan hariç tutulur. FM veritabanı Sports
> Interactive'in lisanslı içeriğidir; kişisel kullanım için kendi oyunundan alınır, repoya konmaz.

## İsim maskeleme (telif güvenliği)

Dosya okunduğu anda tüm gerçek isimler kurgusal ama çağrıştırıcı adlara çevrilir;
**veritabanına hiçbir gerçek kulüp, lig ya da oyuncu adı yazılmaz** (`name_masking.py`).

Üç seviye vardır (`MASK_LEVELS`), **depo varsayılanı `light`**:

| Seviye | Ne yapar | Kime göre |
|---|---|---|
| `light` (varsayılan) | Her isimde TEK, sistematik değişiklik; isim tanınır kalır | Paylaşılabilir, telif riski düşük |
| `strong` | Tamamen kurgusal ad (özgün addan sha256 ile, uyruk havuzuna göre) | Paylaşılabilir, en güvenli |
| `off` | **Hiçbir ad değişmez** — kimlik eşlemesi | **Yalnızca kişisel, yerel oyun** |

| Tür | Örnek (`light`) | Kural |
|---|---|---|
| Rehberdeki kulüp | Galatasaray → **Istanbul Lions**, Manchester City → **Manchester Blue** | `club_directory.py` içindeki sabit maske; tüm yazımlar (`Galatasaray SK`, `Man City`) aynı maskeye gider |
| Rehberde olmayan kulüp | Kuzey Yıldızı SK → **Kuzey Yıldısı** | FC/SK gibi ekler atılır, en ayırt edici kelimede tek harflik değişiklik |
| Lig | Premier League → **İngiltere Elit Ligi** | Bilinmeyen lig: ayırt edici kelimede hafif değişiklik |
| Oyuncu (`light`, varsayılan) | Erling Haaland → **Erling Harland**, Kylian Mbappé → **Kylian Mbeppe**, Hakan Çalhanoğlu → **Hakan Çalhano** | İlk isim **bütün kalır** (baş harfe inmez), tek değişiklik genelde soyadındadır; `van`, `de` gibi ekler ve Türkçe/İskandinav harfler korunur |
| Oyuncu (`strong`) | → tamamen kurgusal ad | Özgün addan (sha256) deterministik, uyruğa göre isim havuzu |

- Aynı dosya her çalıştırmada aynı maskeleri üretir; farklı iki gerçek isim aynı maskeye düşmez.
- Seviye: `python seed.py --mask-level strong` ya da `SEED_NAME_MASKING=strong` ortam değişkeni.
- Maskelenmemiş gerçek bir kulüp/lig adı dünyada kalırsa seed **veritabanına dokunmadan** durur;
  doğrulama raporu da (`--verify-only`) aynı denetimi yapar.
- Oyunda gerçek adla arama yapılabilir: "Galatasaray" yazmak "Istanbul Lions"u bulur
  (`name_masking.resolve_masked_club`).

### `off`: kendi FM verinle gerçek isimlerle oynamak (kişisel/yerel)

Kendi lisanslı FM oyunundan aldığın listeyle, **kendi bilgisayarında**, gerçek kulüp, lig ve
oyuncu adlarıyla oynamak istersen maskelemeyi tamamen kapatabilirsin. Kazayla açılmasın diye
**iki ayrı onay** gerekir:

```bash
# PowerShell (tek seferlik)
$env:OFM_ALLOW_REAL_NAMES = "1"
python seed.py --source fm --mask-level off
```

- `SEED_NAME_MASKING=off` **tek başına yetmez**: `--mask-level off` bayrağı da verilmelidir.
- `OFM_ALLOW_REAL_NAMES=1` yoksa seed hata verir ve **veritabanına dokunmaz**.
- Seviye dünyanın kaydına yazılır (`game_state.mask_level`); `--verify-only` bayraksız çağrılsa
  bile dünyanın kendi seviyesine göre rapor verir ve gerçek adları hata saymaz.
- Dünya kurulurken ve doğrulama raporunda büyük bir uyarı basılır.

**`off` ile kurulan dünya kişiseldir. Asla yapılmaması gerekenler:**

- FM dışa aktarımını (`data/fm/` içindeki dosyaları) repoya eklemek — `.gitignore` bunu engeller,
  `--force` ile zorlanmaz.
- Bu veritabanının dökümünü/yedeğini (`pg_dump`), gerçek adlı ekran görüntülerini ya da gerçek
  adlarla doldurulmuş bir yapıyı paylaşmak, yayımlamak, bir sunucuya koymak.
- Bu dünyayı paylaşılan (çok menajerli) dünyaya çevirmek: `worlds.py` buna izin vermez, dünya
  **tek koltukta** kalır. Başka menajerlerin girdiği bir sunucuda gerçek isim + doğum yılı +
  özellik verisi tutmak lisans ve kişisel veri açısından kabul edilemez.

Paylaşılacak bir dünya kuracaksan maskeli kur: `python seed.py --mask-level light` (ya da `strong`).
**Depo varsayılanı `light`'tir ve öyle kalır**; `off` yalnızca senin makinendeki `.env` dosyasında
(gitignore'da) açılabilir.

## Dışa aktarım nasıl alınır

1. FM'de oyuncu arama / gözlemci ekranını aç, ilgilendiğin ligleri ya da kulüpleri filtrele.
2. Görünümü (view) özelleştir ve aşağıdaki sütunları ekle.
3. Ekranı yazdır: **Ctrl+P → Web Page** (veya **Text File**). Oluşan `.html` / `.txt` / `.rtf` dosyasını bu klasöre koy.
4. Birden çok dosya koyabilirsin; aynı oyuncu (UID ya da isim + yaş + kulüp) bir kez alınır.

CSV de desteklenir (virgül, noktalı virgül ya da sekme ayraçlı; UTF-8, UTF-16 veya Windows-1254).

## Sütunlar

| Gerekli | Önerilen | Özellikler (1-20) |
|---|---|---|
| Name, Position, Club | Age, Nat, CA, PA, Transfer Value, Wage, Expires, UID, Division | Acc, Pac, Fin, Lon, Cmp, Pas, Vis, Tec, Tck, Mar, Pos, Dri, Agi, Han, Ref, 1v1, Aer … |

- İngilizce kısaltmalar, uzun adlar ve Türkçe FM başlıkları tanınır (`Fin` / `Finishing` / `Bitiricilik`).
- `Nat` ve `Pos` belirsizdir; değerlere bakılarak uyruk/mevki ya da Natural Fitness/Positioning olarak çözülür.
- Gözlemci aralıkları (`12-15`) ortalamaya çevrilir; `-` boş sayılır.
- Para: `€45M`, `£1.2M`, `€10M - €20M`, `Not for Sale`. Maaş: `p/w`, `p/m`, `p/a` haftalığa çevrilir.
- Mevki: oyuncunun ilk (doğal) mevkisi alınır. Türkçe kodlar (KL, D, DOS, OS, OOS, FV) de tanınır.

## Kulüp → lig eşlemesi

FM oyuncu listeleri genelde lig içermez. Lig, `club_directory.py` rehberinden bulunur
(Türkiye, İngiltere, İspanya, Almanya, İtalya ve Fransa'nın üst lig kulüpleri; `Bayern Münih` /
`FC Bayern München` gibi yazım farkları ve maskeli adlar eşlenir). Rehberde olmayan kulüp, dosyada
`Division` sütunu varsa o lige eklenir; yoksa atlanır ve seed raporunda (maskeli adıyla) listelenir.

Eşleme bilinçli olarak sıkıdır: "Barcelona SC" FC Barcelona'ya, "Paris FC" PSG'ye yapışmaz;
"Russian Premier League", "LaLiga 2", "Austrian Bundesliga" gibi ligler büyük liglerle
birleşmez, kendi adlarıyla ayrı lig olur. Dünya veritabanına yazılmadan önce doğrulanır
(tekrarlanan lig/kulüp adı, kaleci eksikliği, geçersiz yaş); sorun varsa mevcut kayıt silinmez.

## Kadro tamamlama

Her kulübün oynanabilir olması için en az 16 oyuncu ve mevki başına asgari sayı
(2 GK, 4 DEF, 4 MID, 3 FWD) gerekir. Eksik kalan kadrolar kulübün seviyesinin altında
**altyapı oyuncularıyla** (`data_source = academy`) tamamlanır. En fazla 26 oyuncu alınır.

## Gerçek kadrolar ve FM dışa aktarımı (16G, yalnızca kendi kariyerin)

`python seed.py --real-players` açık veri dünyasını (6 lig, 114 kulüp) **gerçek oyuncu kadrolarıyla** kurar:
kadro iskeleti (ad, yaş, uyruk, mevki, kulüp) Wikidata'dan gelir (`tools/build_squads.py` →
`data/local/squads.json`), yetenekler oyun tarafından üretilir. Bu klasöre **kendi FM26 oyunundan** aldığın
dışa aktarımları koyarsan **FM önceliklidir**: bir kulübün FM oyuncuları (gerçek ad + gerçek 1-20 özellikler,
CA/PA, değer, maaş, sözleşme) kadroya önce girer, Wikidata yalnızca eksik oyuncuları ekler, kalan yer (en fazla 30
kişilik kadroya kadar) üretilmiş oyuncuyla dolar. Wikidata oyuncusuyla eşleşen FM satırı o oyuncuya gerçek özellikleri
verir. Kulübü bu dünyada olmayan (başka lig) ya da iki Wikidata adayı arasında belirsiz kalan FM satırı yok sayılır ve
raporda listelenir.

> Yalnızca kendi bilgisayarındaki **tek koltuklu** kariyer içindir. İki ayrı onay gerekir: `--real-players`
> ve `OFM_ALLOW_REAL_PLAYERS=1`. Dünya paylaşılan dünyaya çevrilemez; paylaşılan dünya şemasına yazılamaz.
> `data/fm/` ve `data/local/` içeriği depoya girmez (`.gitignore`), paylaşılmaz.

### Dışa aktarım (öneri; menü adları FM sürümüne ve dil ayarına göre değişebilir)

1. FM26'da kariyerini aç. **Oyuncu Arama** (Player Search) ya da bir kulübün **Kadro** ekranına git.
   Altı ligin tamamı için oyuncu aramada lig filtresi (Süper Lig, Premier League, LaLiga, Bundesliga, Serie A,
   Ligue 1) kullanmak en hızlısıdır; tek tek kulüp kadrosu da olur.
2. Görünümü özelleştir ve şu sütunları ekle:
   - **Zorunlu:** `Name`, `Position`, `Club`
   - **Eşleşme için önerilen:** `Age` ve **`DoB`** (doğum tarihi; varsa eşleşme yaşa değil tarihe bakar)
   - **Değer için:** `CA`, `PA`, `Nat`, `Transfer Value`, `Wage`, `Expires`
   - **Özellikler (1-20):** teknik, zihinsel, fiziksel ve kaleci özelliklerinin hepsi (`Fin`, `Pas`, `Tck`,
     `Pac`, `Acc`, `Han`, `Ref` …). Dışa aktarımda olmayan özellik oyunda tahminle doldurulur.
3. Listeyi yazdır: **Ctrl+P → Web Page** (ya da Text File). Oluşan `.html` / `.txt` dosyasını bu klasöre koy.
   Birden çok dosya olabilir; aynı oyuncu (UID ya da isim + yaş + kulüp) bir kez alınır.
4. Adları `sample_` ile başlamasın (o önek kurgusal örnek dosyalara ayrılmıştır ve atlanır).

### Eşleşme kuralları (tutucu)

- **Kulüp aynı olmalı:** FM'deki kulüp adı açık veri kulübüne (ad, takma ad ya da `club_directory.py`
  yazımları; "Man City" gibi) tek anlamlı olarak bağlanmalı. Bağlanamayan kulüp: `kulüp bu dünyada yok`.
- **Ad:** aksan ve noktalama duyarsız tam eşitlik ("Odegaard" = "Ødegaard"); ikinci turda kelime sırası farkı
  ("Heung-min Son" / "Son Heung-min") ya da **doğum tarihi birebir + aynı soyad**.
- **Yaş:** FM'deki yaş Wikidata yaşından en fazla 1 farklı olabilir (FM'in oyun içi tarihi bilinmediği için).
  `DoB` sütunu varsa yaş yerine tarih karşılaştırılır (gün/ay sırası iki yönlü denenir).
- **Tekillik:** iki aday varsa ya da iki FM satırı aynı oyuncuyu isterse eşleşme yapılmaz (`belirsiz`) ve FM satırı
  yok sayılır (aynı kişi iki kez yazılmasın).
- Eşleşen oyuncunun **adı ve yaşı Wikidata'dan** kalır; mevkisi, özellikleri, CA/PA ve sözleşme bilgisi FM'den gelir.
- Wikidata'da eşleşmeyen FM satırı **yalnızca-FM oyuncusu** olarak kadroya girer (ad, yaş, uyruk da FM'den). Aynı
  kulüpte aynı adlı Wikidata kaydı (ya da başka kulüpte aynı ad + uyumlu yaş) aynı kişi sayılır ve alınmaz.
- Kadro tavanı 30: önce FM oyuncuları (en yüksek CA; en fazla 4 kaleci), sonra Wikidata (güven sırasıyla).

### Çalıştırma

```powershell
python tools/build_squads.py                 # bir kez: Wikidata kadroları -> data/local/squads.json
$env:OFM_ALLOW_REAL_PLAYERS = "1"
python seed.py --real-players --career-schema <kendi_kariyer_şeman>
```

- `<kendi_kariyer_şeman>`: hesabının kariyer şeması (`accounts.users.career_schema`; `public` ya da `career_<id>`).
  Paylaşılan dünya şeması (`world_<id>`) reddedilir.
- `--fm dosya1.html dosya2.html`: yalnızca bu dosyaları yama olarak kullan; `--no-fm-overlay`: yamasız kur.
- Seed çıktısı eşleşen / yalnızca-FM / yok sayılan sayısını ve ilk yok sayılanları yazar; tam liste
  `data/local/fm_overlay_report.json` (gerçek adlar içerir, yereldir).
- **Önce yedek al:** seed hedef şemadaki kariyeri silip dünyayı yeniden kurar (mevcut kariyer ilerlemesi gider).

## Örnek dosya

`sample_fm_export.html` **kurgusal** bir örnektir: kulüp adları gerçek (maskeleme girdisi olarak),
oyuncu isimleri ve tüm değerler uydurmadır. Seed sonrası veritabanında kulüpler maskeli adlarıyla
(Istanbul Lions, Madrid Blancos, München Roten...) görünür. FM akışını denemek için:

```bash
python seed.py --fm-sample
```
