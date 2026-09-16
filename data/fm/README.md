# FM veri klasörü

`python seed.py` bu klasördeki Football Manager dışa aktarımlarını okur ve oyun dünyasını
**kendi oyuncu listenden** kurar. Klasörde dışa aktarım yoksa kurgusal (sentetik) dünyaya düşer.

> Dışa aktarım dosyaları `.gitignore` ile repodan hariç tutulur. FM veritabanı Sports
> Interactive'in lisanslı içeriğidir; kişisel kullanım için kendi oyunundan alınır, repoya konmaz.

## İsim maskeleme (telif güvenliği)

Dosya okunduğu anda tüm gerçek isimler kurgusal ama çağrıştırıcı adlara çevrilir;
**veritabanına hiçbir gerçek kulüp, lig ya da oyuncu adı yazılmaz** (`name_masking.py`).

| Tür | Örnek | Kural |
|---|---|---|
| Rehberdeki kulüp | Galatasaray → **Istanbul Lions**, Manchester City → **Manchester Blue** | `club_directory.py` içindeki sabit maske; tüm yazımlar (`Galatasaray SK`, `Man City`) aynı maskeye gider |
| Rehberde olmayan kulüp | Kuzey Yıldızı SK → **Kuzey Yıldısı** | FC/SK gibi ekler atılır, en ayırt edici kelimede tek harflik değişiklik |
| Lig | Premier League → **İngiltere Elit Ligi** | Bilinmeyen lig: ayırt edici kelimede hafif değişiklik |
| Oyuncu (`light`, varsayılan) | Erling Haaland → **E. Harland**, Kylian Mbappé → **K. Mbeppe** | Ad baş harfe iner, soyadında hafif fonetik değişiklik; `van`, `de` gibi ekler ve Türkçe/İskandinav harfler korunur |
| Oyuncu (`strong`) | → tamamen kurgusal ad | Özgün addan (sha256) deterministik, uyruğa göre isim havuzu |

- Aynı dosya her çalıştırmada aynı maskeleri üretir; farklı iki gerçek isim aynı maskeye düşmez.
- Seviye: `python seed.py --mask-level strong` ya da `SEED_NAME_MASKING=strong` ortam değişkeni.
- Maskelenmemiş gerçek bir kulüp/lig adı dünyada kalırsa seed **veritabanına dokunmadan** durur;
  doğrulama raporu da (`--verify-only`) aynı denetimi yapar.
- Oyunda gerçek adla arama yapılabilir: "Galatasaray" yazmak "Istanbul Lions"u bulur
  (`name_masking.resolve_masked_club`).

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

## Örnek dosya

`sample_fm_export.html` **kurgusal** bir örnektir: kulüp adları gerçek (maskeleme girdisi olarak),
oyuncu isimleri ve tüm değerler uydurmadır. Seed sonrası veritabanında kulüpler maskeli adlarıyla
(Istanbul Lions, Madrid Blancos, München Roten...) görünür. FM akışını denemek için:

```bash
python seed.py --fm-sample
```
