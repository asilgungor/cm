# FM veri klasörü

`python seed.py` bu klasördeki Football Manager dışa aktarımlarını okur ve oyun dünyasını
**gerçek oyuncu listenden** kurar. Klasörde dışa aktarım yoksa kurgusal (sentetik) dünyaya düşer.

> Dışa aktarım dosyaları `.gitignore` ile repodan hariç tutulur. FM veritabanı Sports
> Interactive'in lisanslı içeriğidir; kişisel kullanım için kendi oyunundan alınır, repoya konmaz.

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
(Süper Lig, Premier League, LaLiga, Bundesliga, Serie A, Ligue 1 kulüpleri; `Bayern Münih` /
`FC Bayern München` gibi yazım farkları eşlenir). Rehberde olmayan kulüp, dosyada `Division`
sütunu varsa o lige eklenir; yoksa atlanır ve seed raporunda listelenir.

Eşleme bilinçli olarak sıkıdır: "Barcelona SC" FC Barcelona'ya, "Paris FC" PSG'ye yapışmaz;
"Russian Premier League", "LaLiga 2", "Austrian Bundesliga" gibi ligler büyük liglerle
birleşmez, kendi adlarıyla ayrı lig olur. Dünya veritabanına yazılmadan önce doğrulanır
(tekrarlanan lig/kulüp adı, kaleci eksikliği, geçersiz yaş); sorun varsa mevcut kayıt silinmez.

## Kadro tamamlama

Her kulübün oynanabilir olması için en az 16 oyuncu ve mevki başına asgari sayı
(2 GK, 4 DEF, 4 MID, 3 FWD) gerekir. Eksik kalan kadrolar kulübün seviyesinin altında
**altyapı oyuncularıyla** (`data_source = academy`) tamamlanır. En fazla 26 oyuncu alınır.

## Örnek dosya

`sample_fm_export.html` **kurgusal** bir örnektir: kulüp adları gerçek, oyuncu isimleri ve tüm
değerler uydurmadır. FM akışını denemek için:

```bash
python seed.py --fm-sample
```
