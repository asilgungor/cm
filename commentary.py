"""
commentary.py
=============
Turkce anlatim bankasi (13B / K5). ANLATIM KODDA DEGIL, VERIDE.

Neden ayri dosya
----------------
12. Asama'da her olay turunun 3-5 sabit Python cumlesi vardi: 2000 macta yalnizca 52
farkli cumle cikiyor, tek bir cumle butun satirlarin %12.9'unu tutuyordu. Ustelik
cumleler olayla celisebiliyordu (yerdeki sut "kafayi vuruyor" diye yazilabiliyordu).

Burada her satir bir VERI kaydidir (`Line`):
    live    canli anlatim cumlesi (simdiki zaman)
    report  ayni anin GECMIS ZAMAN karsiligi (mac raporu icin; yalnizca onemli anlarda dolu)
    weight  agirlik
    when    bu satirin gecerli oldugu BAGLAM etiketleri (hepsi saglanmali)

Bagam etiketleri (`when`) uc eksenden gelir:
    skor durumu   first / equalise / ahead / pullback / extend / seal
    dakika bandi  early / firsthalf / hour / late / closing / stoppage / extra
    sans kalitesi big / good / normal / far        (K6: SAYI OLARAK ASLA GOSTERILMEZ)
    vurus bicimi  header / volley / oneonone / tap / long / box / solo / assisted

KURAL: vucut bolumu ya da mesafe SOYLEYEN her satir etiketlenmek ZORUNDADIR.
Etiketsiz satirlar notrdur ("topu aglara gonderiyor"); boylece yerden cekilen sut
asla "kafa vurusu" diye anlatilmaz.

Yer tutucular (yalnizca olay yukunden doldurulur, UYDURMA BILGI YOK):
    {p} oyuncu · {t} takimi · {o} rakip · {gk} kaleci · {a} pas/orta veren · {d} savunmaci
Turkce ek uyumu icin turetilmis yuvalar: {p_gen} {p_dat} {p_abl} {p_acc} {p_loc}
(ayni ekler a_, gk_, d_, t_, o_ icin de vardir). Ornek: "{a_gen} pasiyla".

Calisma anindaki kullanim `Narrator`dir: agirlikli secim + BUCKET BASINA TEKRAR
ONLEYICI HALKA TAMPON. Rastgeleligi motorun sonuc RNG'sinden AYRIDIR (K11): anlatim
tohumu maclarin sonucunu degistiremez.

Yalnizca standart kutuphane kullanir.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from random import Random

__all__ = ["Line", "Narrator", "Slots", "BANK", "REPORT_FALLBACK", "genitive", "dative"]


# ===========================================================================
# [1] TURKCE EK UYUMU (ozel isimlere kesme isaretiyle eklenir)
# ===========================================================================

_BACK = "aıou"
_FRONT = "eiöü"
_ROUND = "ouöü"
_VOWELS = _BACK + _FRONT
_VOICELESS = "pçtksşhf"


def _last_vowel(word: str) -> str:
    for ch in reversed(word.lower()):
        if ch in _VOWELS:
            return ch
    return "e"          # sesli harfi olmayan ad (kisaltma): on-duz varsayilir


def _ends_with_vowel(word: str) -> bool:
    return bool(word) and word.strip()[-1:].lower() in _VOWELS


def _narrow(vowel: str) -> str:
    """Dar unlu uyumu: ı / i / u / ü."""
    if vowel in _BACK:
        return "u" if vowel in _ROUND else "ı"
    return "ü" if vowel in _ROUND else "i"


def _wide(vowel: str) -> str:
    """Genis unlu uyumu: a / e."""
    return "a" if vowel in _BACK else "e"


def genitive(name: str) -> str:
    """Ilgi hali: Ahmet'in, Burak'ın, Onur'un, Gürsel'in, Ali'nin."""
    v = _narrow(_last_vowel(name))
    return f"{name}'n{v}n" if _ends_with_vowel(name) else f"{name}'{v}n"


def dative(name: str) -> str:
    """Yonelme hali: Ahmet'e, Burak'a, Ali'ye."""
    v = _wide(_last_vowel(name))
    return f"{name}'y{v}" if _ends_with_vowel(name) else f"{name}'{v}"


def ablative(name: str) -> str:
    """Ayrilma hali: Ahmet'ten, Burak'tan, Ali'den."""
    v = _wide(_last_vowel(name))
    hard = (not _ends_with_vowel(name)) and name.strip()[-1:].lower() in _VOICELESS
    return f"{name}'{'t' if hard else 'd'}{v}n"


def locative(name: str) -> str:
    """Bulunma hali: Ahmet'te, Burak'ta, Ali'de."""
    v = _wide(_last_vowel(name))
    hard = (not _ends_with_vowel(name)) and name.strip()[-1:].lower() in _VOICELESS
    return f"{name}'{'t' if hard else 'd'}{v}"


def accusative(name: str) -> str:
    """Belirtme hali: Ahmet'i, Burak'ı, Ali'yi."""
    v = _narrow(_last_vowel(name))
    return f"{name}'y{v}" if _ends_with_vowel(name) else f"{name}'{v}"


_CASES = {"gen": genitive, "dat": dative, "abl": ablative, "loc": locative, "acc": accusative}


class Slots(dict):
    """
    Sablon yuvalari. Ek halleri ({p_gen}, {a_dat} ...) ISTENDIGINDE hesaplanir;
    boylece kullanilmayan hicbir sey uretilmez (satir basina maliyet ~0).
    """

    __slots__ = ()

    def __missing__(self, key: str) -> str:
        base, _, case = key.rpartition("_")
        fn = _CASES.get(case)
        if fn is not None and base in self:
            value = fn(self[base])
            self[key] = value
            return value
        raise KeyError(key)


# ===========================================================================
# [2] SATIR KAYDI
# ===========================================================================

@dataclass(frozen=True)
class Line:
    live: str
    report: str = ""
    weight: int = 10
    when: frozenset[str] = frozenset()


def L(live: str, report: str = "", weight: int = 10, when: tuple[str, ...] | str = ()) -> Line:
    if isinstance(when, str):
        when = (when,)
    return Line(live=live, report=report, weight=weight, when=frozenset(when))


# ===========================================================================
# [3] BANKA
# ===========================================================================
# Anahtarlar: "<olay>.<alt tur>". Motor bu anahtarlari dogrudan kullanir.
# Etiketsiz satirlar her baglamda gecerlidir ve VUCUT BOLUMU / MESAFE SOYLEMEZ.

# --------------------------------------------------------------------- GOL
GOAL_OPEN: tuple[Line, ...] = (
    # -- notr (etiketsiz): bicim soylemez --------------------------------
    L("GOOOL! {p} ({t}) topu ağlarla buluşturuyor!", "{p} topu ağlarla buluşturdu.", 9),
    L("GOOOL! {p} ({t}) fileleri havalandırıyor!", "{p} fileleri havalandırdı.", 9),
    L("GOOOL! {p} ({t}) buldu golü!", "Golü {p} buldu.", 8),
    L("GOOOL! {p} ({t}) kaleciye şans tanımıyor!", "{p} kaleciye şans tanımadı.", 8),
    L("GOOOL! {p} ({t}) topu ağların içine yolluyor!", "{p} topu ağların içine yolladı.", 8),
    L("GOOOL! {p} ({t}) beklediği anı yakaladı ve golü attı!", "{p} beklediği anı yakaladı.", 7),
    L("GOOOL! {p} ({t}) soğukkanlı bitiriyor!", "{p} soğukkanlı bitirdi.", 8),
    L("GOOOL! {p} ({t}) savunmayı çaresiz bıraktı!", "{p} savunmayı çaresiz bıraktı.", 7),
    L("GOOOL! {p} ({t}) tek dokunuşla bitirdi işi!", "{p} tek dokunuşla bitirdi.", 7),
    L("GOOOL! Top {p_dat} düştü ve {p} affetmedi!", "Top {p_dat} düştü, affetmedi.", 7),
    L("GOOOL! {p} ({t}) aradığı golü sonunda buldu!", "{p} aradığı golü buldu.", 6),
    L("GOOOL! {p} ({t}) kaleciyi kımıldatmadan bitiriyor!", "{p} kaleciyi kımıldatmadan bitirdi.", 7),
    L("GOOOL! {p} ({t}) kalabalığın arasından fırsatı gördü!", "{p} kalabalığın arasından bitirdi.", 6),
    L("GOOOL! {p} ({t}) bu kez hata yapmadı!", "{p} bu kez hata yapmadı.", 6),
    L("GOOOL! {p} ({t}) bir anda bitirdi pozisyonu!", "{p} pozisyonu bir anda bitirdi.", 6),
    L("GOOOL! {p} ({t}) topu {gk_abl} kaçırdı ve gol!", "{p} topu {gk_abl} kaçırıp bitirdi.", 6),
    L("GOOOL! {p} ({t}) sakin kaldı, doğru yere gönderdi!", "{p} sakin kalıp doğru yere gönderdi.", 6),
    L("GOOOL! {p} ({t}) bu pozisyonda hep golü bulur!", "{p} yine bitirdi.", 5),
    L("GOOOL! {p} ({t}) ağların sesini duyurdu!", "{p} ağların sesini duyurdu.", 5),
    L("GOOOL! {p} ({t}) kaleciyle inatlaşmadı, direkt bitirdi!", "{p} beklemeden bitirdi.", 5),
    L("GOOOL! {p} ({t}) savunmanın hatasını affetmiyor!", "{p} savunma hatasını affetmedi.", 6),
    L("GOOOL! {p} ({t}) doğru yerde, doğru zamanda!", "{p} doğru yerde, doğru zamandaydı.", 6),
    L("GOOOL! {p} ({t}) kaleyi gördü ve hiç düşünmedi!", "{p} hiç düşünmeden bitirdi.", 6),
    L("GOOOL! {p} ({t}) kalenin yolunu buldu!", "{p} kalenin yolunu buldu.", 6),
    # -- ceza sahasi (box) -----------------------------------------------
    L("GOOOL! {p} ({t}) ceza sahasında topu ağlara çeviriyor!",
      "{p} ceza sahasında topu ağlara çevirdi.", 12, "box"),
    L("GOOOL! {p} ({t}) altı pas içinde bitiriyor!", "{p} altı pas içinde bitirdi.", 11, "box"),
    L("GOOOL! {p} ({t}) ceza sahasındaki karambolü lehine çeviriyor!",
      "{p} ceza sahasındaki karamboldan golü çıkardı.", 11, "box"),
    L("GOOOL! {p} ({t}) penaltı noktası civarından vuruyor ve top ağlarda!",
      "{p} penaltı noktası civarından vurup golü attı.", 11, "box"),
    L("GOOOL! {p} ({t}) kale sahasında topa ilk dokunan oluyor!",
      "Kale sahasında topa ilk dokunan {p} oldu.", 10, "box"),
    L("GOOOL! {p} ({t}) yakın mesafeden bitirdi!", "{p} yakın mesafeden bitirdi.", 10, "box"),
    L("GOOOL! {p} ({t}) ceza yayının hemen içinde buldu boşluğu!",
      "{p} ceza yayının içinde boşluğu buldu.", 10, "box"),
    L("GOOOL! {p} ({t}) dönen topu ağlara gönderiyor!", "{p} dönen topu ağlara gönderdi.", 10, "box"),
    L("GOOOL! {p} ({t}) savunmanın arasından sıyrılıp bitiriyor!",
      "{p} savunmanın arasından sıyrılıp bitirdi.", 9, "box"),
    L("GOOOL! {p} ({t}) çaprazdan köşeye nişanlıyor!", "{p} çaprazdan köşeye nişanladı.", 9, "box"),
    L("GOOOL! {p} ({t}) ceza sahasında dönüp vurdu, top ağlarda!",
      "{p} ceza sahasında dönüp vurdu ve golü attı.", 9, "box"),
    # -- uzaktan (long) ---------------------------------------------------
    L("GOOOL! {p} ({t}) uzaktan patlattı, top köşeye gitti!",
      "{p} uzaktan patlattı ve köşeyi buldu.", 12, "long"),
    L("GOOOL! {p} ({t}) ceza sahası dışından muhteşem bir vuruş!",
      "{p} ceza sahası dışından muhteşem vurdu.", 12, "long"),
    L("GOOOL! {p} ({t}) uzaktan denedi ve top ağlara gitti!",
      "{p} uzaktan deneyip golü buldu.", 11, "long"),
    L("GOOOL! {p} ({t}) otuz metreden bombayı patlattı!", "{p} uzak mesafeden bombayı patlattı.", 10, "long"),
    L("GOOOL! {p} ({t}) yerden sert vurdu, top direğin dibinden içeri girdi!",
      "{p} uzaktan yerden vurdu, top direğin dibinden girdi.", 10, "long"),
    L("GOOOL! {p} ({t}) topu yerden seke seke ağlara yolladı!",
      "{p} uzaktan vurdu, top seke seke ağlara gitti.", 9, "long"),
    L("GOOOL! {p} ({t}) kaleciyi uzak mesafeden avladı!", "{p} kaleciyi uzak mesafeden avladı.", 9, "long"),
    L("GOOOL! {p} ({t}) ceza yayının dışından doksana gönderdi!",
      "{p} ceza yayının dışından doksanı buldu.", 10, "long"),
    L("GOOOL! {p} ({t}) uzaktan kavisli bir vuruşla bitirdi!",
      "{p} uzaktan kavisli vuruşla bitirdi.", 9, "long"),
    L("GOOOL! {p} ({t}) kendine güvendi, uzaktan vurdu ve kazandı!",
      "{p} uzaktan vurmayı seçti ve kazandı.", 8, "long"),
    # -- kafa (header) ----------------------------------------------------
    L("GOOOL! {p} ({t}) yükseldi ve kafayla ağlara gönderdi!",
      "{p} kafa vuruşuyla golü attı.", 12, "header"),
    L("GOOOL! {p} ({t}) kafayı vurdu, top köşeye gitti!", "{p} kafayla köşeyi buldu.", 12, "header"),
    L("GOOOL! {p} ({t}) havada rakibinden önce davrandı ve kafayla bitirdi!",
      "{p} havada rakibinden önce davranıp kafayla bitirdi.", 11, "header"),
    L("GOOOL! {p} ({t}) kafayı yere çarptırdı, top ağlarda!",
      "{p} kafayı yere çarptırıp golü attı.", 11, "header"),
    L("GOOOL! {p} ({t}) arka direkte yükselip kafayla tamamladı!",
      "{p} arka direkte kafayla tamamladı.", 10, "header"),
    L("GOOOL! {p} ({t}) kafayla dokundu, top kaleciyi geçti!",
      "{p} kafayla dokunup kaleciyi geçti.", 9, "header"),
    L("GOOOL! {p} ({t}) markajından sıyrıldı ve kafa golünü attı!",
      "{p} markajından sıyrılıp kafa golünü attı.", 10, "header"),
    L("GOOOL! {p} ({t}) yükseklik farkını kullandı, kafayla gol!",
      "{p} yükseklik farkını kullanıp kafayla bitirdi.", 9, "header"),
    # -- karsi karsiya (oneonone) -----------------------------------------
    L("GOOOL! {p} ({t}) kaleciyle karşı karşıya kaldı ve affetmedi!",
      "{p} kaleciyle karşı karşıya kalıp affetmedi.", 13, "oneonone"),
    L("GOOOL! {p} ({t}) {gk_acc} çalımladı ve boş kaleye bıraktı!",
      "{p} kaleciyi çalımlayıp boş kaleye bıraktı.", 12, "oneonone"),
    L("GOOOL! {p} ({t}) kaleciyi ters köşeye yatırdı!", "{p} kaleciyi ters köşeye yatırdı.", 12, "oneonone"),
    L("GOOOL! {p} ({t}) {gk_gen} üzerinden aşırttı!", "{p} kalecinin üzerinden aşırttı.", 11, "oneonone"),
    L("GOOOL! {p} ({t}) savunmanın arkasına sarktı, kaleciyle baş başa bitirdi!",
      "{p} savunmanın arkasına sarkıp kaleciyle baş başa bitirdi.", 11, "oneonone"),
    L("GOOOL! {p} ({t}) soğukkanlılıkla bekledi, {gk_acc} yatırdı ve gönderdi!",
      "{p} soğukkanlılıkla bekleyip kaleciyi yatırdı.", 10, "oneonone"),
    L("GOOOL! {p} ({t}) tek başına kaldı ve hata yapmadı!",
      "{p} kaleciyle baş başa kalıp hata yapmadı.", 10, "oneonone"),
    # -- dokunus / bos kale (tap) -----------------------------------------
    L("GOOOL! {p} ({t}) boş kaleye dokunması yetti!", "{p} boş kaleye dokunup golü attı.", 12, "tap"),
    L("GOOOL! {p} ({t}) çizgi üzerinden topu içeri itti!", "{p} topu çizgi üzerinden içeri itti.", 11, "tap"),
    L("GOOOL! {p} ({t}) kalenin ağzında topu ağlara gönderdi!",
      "{p} kalenin ağzında bitirdi.", 11, "tap"),
    L("GOOOL! {p} ({t}) bir dokunuşla tamamladı!", "{p} bir dokunuşla tamamladı.", 10, "tap"),
    # -- vole (volley) ----------------------------------------------------
    L("GOOOL! {p} ({t}) topu yere indirmeden voleyle patlattı!",
      "{p} voleyle bitirdi.", 12, "volley"),
    L("GOOOL! {p} ({t}) havadaki topa vurdu, ağlar havalandı!",
      "{p} havadaki topa vurup golü attı.", 11, "volley"),
    L("GOOOL! {p} ({t}) düşen topa voleyle nişan aldı!", "{p} düşen topa voleyle nişan aldı.", 10, "volley"),
    # -- bireysel is (solo) -----------------------------------------------
    L("GOOOL! {p} ({t}) topu aldı, iki kişiyi geçti ve bitirdi!",
      "{p} topu alıp iki kişiyi geçtikten sonra bitirdi.", 12, "solo"),
    L("GOOOL! {p} ({t}) tek başına taşıdı ve tamamladı!",
      "{p} topu tek başına taşıyıp tamamladı.", 11, "solo"),
    L("GOOOL! {p} ({t}) savunmanın içinden geçti, kendi golünü kendi yaptı!",
      "{p} savunmanın içinden geçip kendi golünü yaptı.", 11, "solo"),
    # -- asistli (assisted) -----------------------------------------------
    L("GOOOL! {a_gen} pasında {p} ({t}) bitiriyor!", "{a_gen} pasında {p} bitirdi.", 12, "assisted"),
    L("GOOOL! {a} gördü, {p} ({t}) bitirdi!", "{a} gördü, {p} bitirdi.", 12, "assisted"),
    L("GOOOL! {a_gen} ortasında {p} ({t}) hazırdı!",
      "{a_gen} ortasında {p} hazırdı.", 11, "assisted"),
    L("GOOOL! {a} topu {p_dat} bıraktı, gerisi kolaydı!",
      "{a} topu {p_dat} bıraktı, {p} tamamladı.", 11, "assisted"),
    L("GOOOL! {a_gen} arkaya attığı topta {p} ({t}) bitiriyor!",
      "{a_gen} arkaya attığı topta {p} bitirdi.", 11, "assisted"),
    L("GOOOL! {a} ile {p} ({t}) arasındaki oyun savunmayı bitirdi!",
      "{a} ile {p} arasındaki oyun savunmayı çözdü.", 10, "assisted"),
    L("GOOOL! {a_gen} son pası kusursuzdu, {p} ({t}) tamamladı!",
      "{a_gen} son pasını {p} tamamladı.", 11, "assisted"),
    L("GOOOL! {a} kanattan çevirdi, {p} ({t}) ağlara yolladı!",
      "{a} kanattan çevirdi, {p} ağlara yolladı.", 10, "assisted"),
    L("GOOOL! {a_gen} ayağından çıkan top {p_acc} buldu!",
      "{a_gen} pası {p_acc} buldu.", 9, "assisted"),
    # -- skor durumu ------------------------------------------------------
    L("GOOOL! {t} skoru açıyor, {p} sessizliği bozdu!",
      "Skoru açan {p} oldu.", 13, "first"),
    L("GOOOL! İlk gol geldi ve {t} ({p}) öne geçti!", "İlk golü {t} adına {p} buldu.", 12, "first"),
    L("GOOOL! {p} ile {t} maçın ilk golüne imza attı!",
      "Maçın ilk golüne {p} imza attı.", 11, "first"),
    L("GOOOL! {p} eşitliği sağlıyor, {t} maça geri döndü!",
      "{p} eşitliği sağladı ve {t} maça döndü.", 14, "equalise"),
    L("GOOOL! {p} beraberlik golünü attı, her şey yeniden başlıyor!",
      "{p} beraberlik golünü attı.", 13, "equalise"),
    L("GOOOL! {t} boyun eğmedi, {p} skoru eşitledi!",
      "{t} boyun eğmedi; {p} eşitledi.", 12, "equalise"),
    L("GOOOL! {p} ile {t} öne geçiyor!", "{p} ile {t} öne geçti.", 14, "ahead"),
    L("GOOOL! {p} üstünlüğü {t_dat} veriyor!", "{p} üstünlüğü {t_dat} verdi.", 12, "ahead"),
    L("GOOOL! {t} önde artık, golü {p} attı!", "{t} öne geçti; golü {p} attı.", 11, "ahead"),
    L("GOOOL! {p} farkı bire indirdi, {t} umutlandı!",
      "{p} farkı bire indirdi.", 14, "pullback"),
    L("GOOOL! {t} pes etmiyor, {p} maçı yeniden açtı!",
      "{p} golüyle {t} maçı yeniden açtı.", 13, "pullback"),
    L("GOOOL! {p} umut golünü attı!", "{p} umut golünü attı.", 11, "pullback"),
    L("GOOOL! {p} farkı ikiye çıkardı!", "{p} farkı ikiye çıkardı.", 14, "extend"),
    L("GOOOL! {t} nefes aldıran golü buldu, {p} bitirdi!",
      "{t} nefes aldıran golü {p} ile buldu.", 12, "extend"),
    L("GOOOL! {p} ile {t} arayı açıyor!", "{p} ile {t} arayı açtı.", 12, "extend"),
    L("GOOOL! {p} işi bitiriyor, {t} maçı kilitledi!",
      "{p} golüyle {t} maçı kilitledi.", 14, "seal"),
    L("GOOOL! {p} farkı büyüttü, {o} için akşam zor!",
      "{p} farkı büyüttü.", 12, "seal"),
    L("GOOOL! {t} adeta geçit töreni yapıyor, {p} da katıldı!",
      "{t} farkı açmayı sürdürdü; {p} da katkı verdi.", 10, "seal"),
    # -- dakika bandi -----------------------------------------------------
    L("GOOOL! Daha maçın başında {p} ({t}) buldu golü!",
      "Maçın başında {p} golü buldu.", 12, "early"),
    L("GOOOL! Erken vuruş! {p} ({t}) ısınmaya fırsat vermedi!",
      "Erken gol {p_abl} geldi.", 11, "early"),
    L("GOOOL! Devre kapanmadan {p} ({t}) buldu golü!",
      "Devre kapanmadan {p} golü buldu.", 10, "firsthalf"),
    L("GOOOL! Son bölümde {p} ({t}) sahneye çıktı!",
      "Son bölümde {p} sahneye çıktı.", 12, "late"),
    L("GOOOL! Bitime dakikalar kala {p} ({t}) buldu golü!",
      "Bitime dakikalar kala {p} golü buldu.", 12, "closing"),
    L("GOOOL! Son saniyelerde {p} ({t})! Stat yıkılıyor!",
      "Son saniyelerde {p} golü attı.", 14, "stoppage"),
    L("GOOOL! Uzatmada {p} ({t}) hikâyeyi değiştirdi!",
      "Uzatmada {p} hikâyeyi değiştirdi.", 14, "stoppage"),
    L("GOOOL! Uzatma dakikalarında {p} ({t}) buldu golü!",
      "Uzatma dakikalarında {p} golü buldu.", 12, "extra"),
    # -- net / zayif sans -------------------------------------------------
    L("GOOOL! Apaçık pozisyondu ve {p} ({t}) gereğini yaptı!",
      "{p} apaçık pozisyonda gereğini yaptı.", 10, "big"),
    L("GOOOL! Böyle bir pozisyon kaçmaz, {p} ({t}) bitirdi!",
      "{p} net pozisyonu bitirdi.", 9, "big"),
    L("GOOOL! Umut vuruşuydu ama {p} ({t}) haklı çıktı!",
      "{p} umut vuruşuyla golü buldu.", 10, "far"),
    L("GOOOL! Zor açıdan denedi {p} ({t}) ve oldu!",
      "{p} zor açıdan deneyip golü buldu.", 9, "far"),
    L("GOOOL! {p} ({t}) kaleciyi köşede avladı!", "{p} köşeyi bularak attı.", 6),
    L("GOOOL! {p} ({t}) savunmanın uyuduğu anı yakaladı!", "{p} savunmanın uyuduğu anı yakaladı.", 6),
    L("GOOOL! {p} ({t}) sabırlı atağı gole çevirdi!", "{p} sabırlı atağı gole çevirdi.", 6),
    L("GOOOL! {p} ({t}) sert ve isabetli!", "{p} sert ve isabetli vurdu.", 6),
    L("GOOOL! {p} ({t}) boşluğu buldu ve affetmedi!", "{p} boşluğu bulup affetmedi.", 6),
    L("GOOOL! {p} ({t}) bekleneni yaptı, top ağlarda!", "{p} bekleneni yaptı.", 5),
    L("GOOOL! {p} ({t}) arka direkte hazırdı!", "{p} arka direkte hazırdı.", 9, "box"),
    L("GOOOL! {p} ({t}) topu uzak köşeye kıvırdı!", "{p} topu uzak köşeye kıvırdı.", 9, "long"),
)

GOAL_PENALTY: tuple[Line, ...] = (
    L("GOOOL! {p} penaltıyı {gk_acc} ters köşeye yatırarak attı!",
      "{p} penaltıda kaleciyi ters köşeye yatırdı.", 12),
    L("GOOOL! {p} penaltıyı soğukkanlılıkla gole çevirdi!",
      "{p} penaltıyı soğukkanlılıkla kullandı.", 12),
    L("GOOOL! {p} noktadan sert vurdu, {gk} sadece izledi!",
      "{p} penaltıyı sert vurup attı.", 11),
    L("GOOOL! {p} beklemedi, köşeye gönderdi!", "{p} penaltıda beklemeden köşeyi buldu.", 10),
    L("GOOOL! {p} kaleciyi bekletti ve ortadan bıraktı!",
      "{p} penaltıda kaleciyi bekletip ortadan bitirdi.", 9),
    L("GOOOL! {p} penaltıyı üst köşeye çaktı!", "{p} penaltıyı üst köşeye gönderdi.", 10),
    L("GOOOL! {p} sorumluluğu aldı ve noktadan hata yapmadı!",
      "{p} sorumluluğu alıp penaltıyı attı.", 10),
    L("GOOOL! {p} penaltı baskısını kaldırdı, top ağlarda!",
      "{p} penaltı baskısını kaldırdı.", 9),
    L("GOOOL! {p} yerden köşeye nişanladı, {gk} uzandı ama yetişemedi!",
      "{p} yerden köşeye nişanlayıp attı.", 10, "late"),
    L("GOOOL! Bu dakikada bu penaltı altın değerinde, {p} attı!",
      "{p} bu kritik penaltıyı attı.", 11, "closing"),
    L("GOOOL! {p} eşitliği noktadan sağladı!", "{p} penaltıdan eşitliği sağladı.", 12, "equalise"),
    L("GOOOL! {p} penaltıyla {t_acc} öne geçirdi!",
      "{p} penaltıyla {t_acc} öne geçirdi.", 12, "ahead"),
    L("GOOOL! {p} noktadan farkı açtı!", "{p} penaltıyla farkı açtı.", 11, "extend"),
    L("GOOOL! {p} penaltıyla umudu diri tuttu!", "{p} penaltıyla farkı azalttı.", 11, "pullback"),
    L("GOOOL! Maçın ilk golü noktadan geldi, {p} attı!",
      "Maçın ilk golü penaltıdan, {p_abl} geldi.", 11, "first"),
    L("GOOOL! {p} penaltıyla işi bitirdi!", "{p} penaltıyla maçı bitirdi.", 10, "seal"),
)

GOAL_FREE_KICK: tuple[Line, ...] = (
    L("GOOOL! {p} serbest vuruşu barajın üstünden doksana astı!",
      "{p} frikikten doksanı buldu.", 12),
    L("GOOOL! {p} frikikten muhteşem vurdu, top ağlarda!",
      "{p} frikikten muhteşem bir gol attı.", 12),
    L("GOOOL! {p} frikikte topun üzerinden geçti ve kaleciyi çaresiz bıraktı!",
      "{p} frikikte kaleciyi çaresiz bıraktı.", 11),
    L("GOOOL! {p} barajın altından yerden gönderdi!",
      "{p} frikiği barajın altından gönderdi.", 10),
    L("GOOOL! {p} serbest vuruşta kaleciyi yanılttı!",
      "{p} serbest vuruşta kaleciyi yanılttı.", 10),
    L("GOOOL! {p} frikikten direğin dibine nişanladı!",
      "{p} frikiği direğin dibine gönderdi.", 10),
    L("GOOOL! {p} serbest vuruşta topa kavis verdi, kaleci sadece baktı!",
      "{p} kavisli frikikle golü attı.", 11),
    L("GOOOL! {p} frikikten sert vurdu, top {gk_gen} elinden geçip ağlara gitti!",
      "{p} frikikte kalecinin elinden geçen bir gol attı.", 9),
    L("GOOOL! {p} frikikten eşitliği getirdi!", "{p} frikikten eşitliği sağladı.", 11, "equalise"),
    L("GOOOL! {p} frikikle {t_acc} öne geçirdi!", "{p} frikikle {t_acc} öne geçirdi.", 11, "ahead"),
    L("GOOOL! Son dakikalarda frikikten {p}! Muhteşem bir an!",
      "Son dakikalarda {p} frikikten attı.", 12, "closing"),
    L("GOOOL! {p} frikikten farkı açtı!", "{p} frikikten farkı açtı.", 10, "extend"),
    L("GOOOL! Maçı açan gol frikikten, {p_abl} geldi!",
      "Maçı açan gol frikikten, {p_abl} geldi.", 10, "first"),
    L("GOOOL! {p} umut frikiğini gole çevirdi!", "{p} frikikten farkı azalttı.", 10, "pullback"),
)

GOAL_CORNER: tuple[Line, ...] = (
    L("GOOOL! {a_gen} kornerinde {p} ({t}) yükseldi ve kafayla bitirdi!",
      "{a_gen} kornerinde {p} kafayla bitirdi.", 13, "assisted"),
    L("GOOOL! {a} kusursuz bir korner kullandı, {p} ({t}) tamamladı!",
      "{a_gen} kornerini {p} tamamladı.", 12, "assisted"),
    L("GOOOL! {a_gen} korner ortasında {p} ({t}) arka direkte hazırdı!",
      "{a_gen} kornerinde {p} arka direkte hazırdı.", 11, "assisted"),
    L("GOOOL! {a} korneri yakın direğe gönderdi, {p} ({t}) dokundu ve gol!",
      "{a_gen} kornerinde {p} dokunarak bitirdi.", 11, "assisted"),
    L("GOOOL! Korner sonrası ceza sahası karıştı, {p} ({t}) topu içeri itti!",
      "Korner sonrası karambolde {p} bitirdi.", 12),
    L("GOOOL! Kornerde {p} ({t}) markajından kurtuldu, kafayla ağlara!",
      "Kornerde {p} markajından kurtulup kafayla bitirdi.", 12),
    L("GOOOL! Korner ortasında {p} ({t}) en yükseğe çıkan isim oldu!",
      "Kornerde en yükseğe çıkan {p} oldu.", 11),
    L("GOOOL! Korner sonrası dönen topu {p} ({t}) ağlara gönderdi!",
      "Korner sonrası dönen topu {p} değerlendirdi.", 11),
    L("GOOOL! Kornerde savunma uzaklaştıramadı, {p} ({t}) cezalandırdı!",
      "Kornerde savunmanın hatasını {p} cezalandırdı.", 10),
    L("GOOOL! Kornerden gelen top {p_dat} düştü ve iş bitti!",
      "Kornerden gelen topu {p} değerlendirdi.", 10),
    L("GOOOL! Kornerde {p} ({t}) eşitliği getirdi!", "Kornerde {p} eşitliği sağladı.", 11, "equalise"),
    L("GOOOL! Kornerden gelen golle {t} öne geçti, {p} bitirdi!",
      "Kornerden gelen golle {t} öne geçti; golü {p} attı.", 11, "ahead"),
    L("GOOOL! Son dakikaların kornerinde {p} ({t})! İnanılmaz!",
      "Son dakika kornerinde {p} golü attı.", 12, "stoppage"),
    L("GOOOL! Kornerden {p} ({t}) farkı açtı!", "Kornerden {p} farkı açtı.", 10, "extend"),
    L("GOOOL! Kornerde {p} ({t}) umudu yeniden yaktı!",
      "Kornerde {p} farkı azalttı.", 10, "pullback"),
    L("GOOOL! Maçın ilk golü kornerden, {p} ({t}) attı!",
      "Maçın ilk golü kornerden, {p_abl} geldi.", 10, "first"),
)

# --------------------------------------------------------------------- ISKA
MISS_OPEN: tuple[Line, ...] = (
    # -- notr --------------------------------------------------------------
    L("{p} ({t}) denedi ama top auta gitti.", weight=8),
    L("{p} ({t}) isabet ettiremedi.", weight=8),
    L("{p} ({t}) vurdu, top kaleyi bulmadı.", weight=8),
    L("{p} ({t}) vuruşunu kontrol edemedi, top dışarı.", weight=7),
    L("{p} ({t}) topu istediği yere gönderemedi.", weight=7),
    L("{p} ({t}) acele etti, vuruş boşa gitti.", weight=7),
    L("{p} ({t}) fırsatı değerlendiremedi.", weight=7),
    L("{p} ({t}) vurdu ama top kaleden uzaktı.", weight=6),
    L("{p} ({t}) dengesini kuramadı, vuruş auta.", weight=6),
    L("{p} ({t}) bu kez isabet bulamadı.", weight=6),
    L("{p} ({t}) denedi, top taraftarın kucağında.", weight=5),
    L("{p} ({t}) zamanlamayı tutturamadı.", weight=6),
    L("{p} ({t}) topa tam vuramadı.", weight=6),
    L("{p} ({t}) vuruşunda güç vardı ama isabet yoktu.", weight=6),
    L("{p} ({t}) pozisyonu bitiremedi, top oyundan çıktı.", weight=6),
    L("{p} ({t}) kaleyi göremedi, vuruş savunmadan döndü.", weight=6),
    L("{p} ({t}) başını kaldırmadan vurdu, top dışarı.", weight=5),
    # -- ceza sahasi -------------------------------------------------------
    L("{p} ({t}) ceza sahasında buldu topu ama yandan auta vurdu.", weight=11, when="box"),
    L("{p} ({t}) altı pas içinde topa dokundu, top yan ağlarda.", weight=10, when="box"),
    L("{p} ({t}) ceza sahasında dönüp vurdu, top üstten gitti.", weight=10, when="box"),
    L("{p} ({t}) yakın mesafeden isabet ettiremedi!", weight=11, when="box"),
    L("{p} ({t}) ceza noktası civarından vurdu, top direğin yanından çıktı.", weight=10, when="box"),
    L("{p} ({t}) karambolde vurdu ama top savunmaya çarpıp auta gitti.", weight=9, when="box"),
    L("{p} ({t}) çapraz pozisyondan denedi, top yan ağlara gitti.", weight=9, when="box"),
    L("{p} ({t}) ceza sahasında boşluğu buldu ama bitiremedi.", weight=9, when="box"),
    L("{p} ({t}) topu ayağına alamadan vurdu, pozisyon kaçtı.", weight=8, when="box"),
    # -- uzaktan -----------------------------------------------------------
    L("{p} ({t}) uzaktan denedi, top üstten auta gitti.", weight=12, when="long"),
    L("{p} ({t}) ceza sahası dışından vurdu, top kaleyi bulmadı.", weight=11, when="long"),
    L("{p} ({t}) uzaktan şansını zorladı ama top tribüne gitti.", weight=10, when="long"),
    L("{p} ({t}) otuz metreden patlattı, top az farkla dışarı.", weight=10, when="long"),
    L("{p} ({t}) uzaktan kavis denedi, top direğin dibinden auta çıktı.", weight=10, when="long"),
    L("{p} ({t}) yerden uzaktan vurdu, top kaleciden önce çizgiyi geçti.", weight=8, when="long"),
    L("{p} ({t}) uzaktan vurmayı seçti, pozisyon sönüp gitti.", weight=9, when="long"),
    L("{p} ({t}) ceza yayının dışından vurdu, top üst direğin çok üstünden.", weight=9, when="long"),
    # -- kafa ---------------------------------------------------------------
    L("{p} ({t}) kafayı vurdu ama top üstten auta gitti.", weight=12, when="header"),
    L("{p} ({t}) yükseldi, kafa vuruşu isabetsizdi.", weight=11, when="header"),
    L("{p} ({t}) kafayla dokundu, top yandan dışarı.", weight=10, when="header"),
    L("{p} ({t}) arka direkte kafayı vurdu ama topa tam vuramadı.", weight=10, when="header"),
    L("{p} ({t}) havada yükseldi ama kafa vuruşu kaleden uzak kaldı.", weight=9, when="header"),
    L("{p} ({t}) kafa vuruşunu yere çarptıramadı, top havada kaldı ve dışarı çıktı.",
      weight=8, when="header"),
    # -- karsi karsiya -------------------------------------------------------
    L("{p} ({t}) kaleciyle karşı karşıya kaldı ama topu auta gönderdi! Büyük fırsat!",
      weight=13, when="oneonone"),
    L("{p} ({t}) tek başına kalmıştı, bitiremedi!", weight=12, when="oneonone"),
    L("{p} ({t}) {gk_acc} çalımlamak istedi ama topu kaybetti.", weight=11, when="oneonone"),
    L("{p} ({t}) aşırtmayı denedi, top üstten auta gitti!", weight=11, when="oneonone"),
    L("{p} ({t}) karşı karşıya pozisyonda acele etti, top direğin yanından çıktı!",
      weight=11, when="oneonone"),
    # -- dokunus / bos kale --------------------------------------------------
    L("{p} ({t}) neredeyse boş kaleye vuruyordu, top dışarı gitti! İnanılmaz!",
      weight=13, when="tap"),
    L("{p} ({t}) dokunması yeterdi ama topa temas edemedi!", weight=12, when="tap"),
    L("{p} ({t}) çizgi üzerinde topu içeri gönderemedi!", weight=11, when="tap"),
    # -- vole ----------------------------------------------------------------
    L("{p} ({t}) voleyi denedi, top havalanıp tribüne gitti.", weight=11, when="volley"),
    L("{p} ({t}) havadaki topa vurdu ama isabet bulamadı.", weight=10, when="volley"),
    # -- direk / cok yaklasti -------------------------------------------------
    L("{p} ({t}) DİREĞE vurdu! Top oyun alanına döndü ve savunma uzaklaştırdı!",
      "{p} direğe vurdu.", 5),
    L("{p} ({t}) topu üst direğe gönderdi! Az kalsın!", "{p} üst direğe vurdu.", 4),
    L("{p} ({t}) direğin dibinden dışarı! Kaleci hareketsizdi.", weight=5),
    # -- baglam ---------------------------------------------------------------
    L("{p} ({t}) beraberlik fırsatını kaçırdı!", weight=9, when="pullback"),
    L("{p} ({t}) farkı açma şansını harcadı.", weight=8, when="ahead"),
    L("{p} ({t}) son dakikada kaçırdı! Büyük fırsat gitti!", weight=11, when="closing"),
    L("{p} ({t}) uzatma dakikalarında kaçırdı!", weight=10, when="stoppage"),
    L("{p} ({t}) maçın ilk şansında isabet bulamadı.", weight=8, when="early"),
    L("{p} ({t}) devre kapanmadan denedi ama olmadı.", weight=7, when="firsthalf"),
    L("Böyle bir pozisyon kaçmamalıydı! {p} ({t}) affetti!", weight=10, when="big"),
    L("Net fırsattı, {p} ({t}) değerlendiremedi.", weight=9, when="big"),
    L("Umut vuruşuydu, {p} ({t}) zorladı ama olmadı.", weight=9, when="far"),
    L("{p} ({t}) zor açıdan denedi, top kaleyi bulmadı.", weight=8, when="far"),
    L("{d} baskı yaptı, {p} ({t}) rahat vuramadı ve top auta gitti.", weight=9, when="defended"),
    L("{d} son anda müdahale etti, {p_gen} vuruşu bozuldu.", weight=9, when="defended"),
    L("{d} ayağını uzattı, {p_gen} şutu yön değiştirip dışarı çıktı.", weight=8, when="defended"),
    L("{p} ({t}) geride kalan takımına umut olamadı, top dışarı.", weight=8, when="pullback"),
    L("{p} ({t}) üçüncü golü atabilirdi ama isabet yok.", weight=7, when="extend"),
    L("{p} ({t}) acele etmeseydi gol olabilirdi.", weight=6),
    L("{p} ({t}) topu kaleye gönderemedi, rakip savunma rahatladı.", weight=6),
    L("{p} ({t}) vuruş açısını bulamadı.", weight=6),
    L("{p} ({t}) denedi ama top auta yuvarlandı.", weight=6),
    L("{p} ({t}) topun altına girdi, vuruş havaya gitti.", weight=6),
    L("{p} ({t}) arka direğe nişanladı ama top dışarı süzüldü.", weight=9, when="box"),
    L("{p} ({t}) boş kaleyi göremedi, vuruş yandan dışarı.", weight=8, when="big"),
    L("{p} ({t}) uzaktan şansını denedi, top barajdan sekip auta gitti.", weight=8, when="long"),
    L("{p} ({t}) kafayla yön vermek istedi ama top üstten gitti.", weight=9, when="header"),
    L("{p} ({t}) kaleciyi geçti ama açı daraldı, top yan ağda.", weight=9, when="oneonone"),
    L("{p} ({t}) volesini kontrol edemedi, top tribünde.", weight=8, when="volley"),
    L("{p} ({t}) tek başına sürdü ama vuruşu zayıf kaldı.", weight=8, when="solo"),
)

MISS_PENALTY: tuple[Line, ...] = (
    L("{p} penaltıyı direğin dışına gönderdi, KAÇTI!",
      "{p} penaltıyı kaçırdı.", 12),
    L("{p} üstten auta vurdu, penaltı KAÇTI!", "{p} penaltıyı üstten dışarı attı.", 11),
    L("{p} direğe vurdu! Penaltı gitti!", "{p_gen} penaltısı direkten döndü.", 9),
    L("{p} noktadan vuruşunu kaçırdı, büyük fırsat heba oldu!",
      "{p} noktadan kaçırdı.", 10),
    L("{p} kaleciyi yanılttı ama topu da kaleyi de ıskaladı!",
      "{p} penaltıda topu kaleden uzağa gönderdi.", 8),
    L("{p} bu penaltıyı uzun süre unutmayacak, top dışarı!",
      "{p} penaltıyı kaçırdı.", 9),
    L("{p} son dakikada penaltıyı kaçırdı! Ne an!",
      "{p} son dakikada penaltıyı kaçırdı.", 11, "closing"),
    L("{p} eşitlik penaltısını kaçırdı!", "{p} eşitlik penaltısını kaçırdı.", 11, "pullback"),
)

MISS_FREE_KICK: tuple[Line, ...] = (
    L("{p} serbest vuruşu barajdan döndü.", weight=12),
    L("{p} frikikten denedi, top üstten auta gitti.", weight=12),
    L("{p} serbest vuruşta topu barajın üstünden aşırttı ama kale çok uzaktaydı.", weight=10),
    L("{p} frikiği duvara çarptı ve korner oldu.", weight=10),
    L("{p} serbest vuruşu yandan auta gitti.", weight=10),
    L("{p} frikikte kavisi fazla kaçırdı, top dışarı.", weight=9),
    L("{p} frikikte direğin dibine nişanladı ama top az farkla dışarıda kaldı.", weight=8),
    L("{p} frikikten şansını denedi, isabet yoktu.", weight=9),
    L("{p} son dakikanın frikiğinde isabet bulamadı.", weight=9, when="closing"),
    L("{p} frikiği sert vurdu ama top barajın üstünden dışarı gitti.", weight=9),
    L("{p} serbest vuruşta topu kaleye yöneltemedi.", weight=8),
    L("{p} frikikte ortayı denedi, kimse dokunamadı ve top auta çıktı.", weight=8),
)

MISS_CORNER: tuple[Line, ...] = (
    L("{a_gen} kornerinde {p} ({t}) kafayı vurdu, top üstten auta gitti.", weight=12, when="assisted"),
    L("{a} korneri kullandı, {p} ({t}) yükseldi ama isabet yoktu.", weight=11, when="assisted"),
    L("{a_gen} korner ortasında {p} ({t}) topa iyi vuramadı.", weight=10, when="assisted"),
    L("Kornerde {p} ({t}) kafayı vurdu ama top yandan dışarı çıktı.", weight=11),
    L("Korner sonrası {p} ({t}) denedi, top kaleyi bulmadı.", weight=11),
    L("Kornerde {p} ({t}) yükseldi, kafa vuruşu kaleden uzaktı.", weight=10),
    L("Kornerde topa ilk {p} ({t}) dokundu ama top ağların yanından geçti.", weight=10),
    L("Korner ortası savunmadan döndü, {p} ({t}) dönen topu auta gönderdi.", weight=9),
    L("Kornerde ceza sahası karıştı ama {p} ({t}) bitiremedi.", weight=9),
    L("Kornerde {p} ({t}) arka direkte yükseldi ama top dışarı gitti.", weight=9),
    L("Korner sonrası seken topa {p} ({t}) vurdu, isabet yok.", weight=9),
    L("{a_gen} kornerinde {p} ({t}) kafayla yön veremedi.", weight=9, when="assisted"),
    L("Kornerde {p} ({t}) ön direğe koştu ama topa tam dokunamadı.", weight=8),
)

# ------------------------------------------------------------------ KURTARIS
SAVE_OPEN: tuple[Line, ...] = (
    L("{p} ({t}) vurdu, {gk} kurtardı.", weight=8),
    L("{p} ({t}) denedi ama {gk} topu tuttu.", weight=8),
    L("{p} ({t}) kaleyi buldu, {gk} sağlamdı.", weight=8),
    L("{p} ({t}) vuruşunu yaptı, {gk} yerinde duruyordu.", weight=7),
    L("{p} ({t}) şutunu çekti, {gk} topu kontrol etti.", weight=7),
    L("{p} ({t}) isabet buldu ama {gk} geçit vermedi.", weight=8),
    L("{p} ({t}) zorladı, {gk} yine sahnede!", weight=7),
    L("{p} ({t}) vurdu, {gk} refleksiyle çeliyor!", weight=8),
    L("{p} ({t}) kaleye gönderdi, {gk} kornere çeldi.", weight=7),
    L("{p_gen} vuruşunda {gk} son sözü söyledi.", weight=7),
    L("{p} ({t}) denedi, {gk} topu göğsünde tuttu.", weight=6),
    L("{gk} ayakta kaldı, {p_gen} ({t}) şutu geçmedi.", weight=6),
    L("{p} ({t}) vurdu, {gk} iki hamlede topu kontrol etti.", weight=6),
    L("{gk} doğru yerdeydi, {p_gen} vuruşu işe yaramadı.", weight=6),
    # -- ceza sahasi ---------------------------------------------------------
    L("{p} ({t}) ceza sahasında vurdu, {gk} ayaklarıyla kurtardı!", weight=11, when="box"),
    L("{p} ({t}) yakın mesafeden vurdu ama {gk} refleksiyle çıkardı!", weight=11, when="box"),
    L("{p} ({t}) altı pas içinde dokundu, {gk} çizgide durdurdu!", weight=10, when="box"),
    L("{p} ({t}) ceza noktasından vurdu, {gk} köşeye uzanıp çeldi.", weight=10, when="box"),
    L("{p} ({t}) çaprazdan denedi, {gk} yakın direği kapattı.", weight=9, when="box"),
    L("{p} ({t}) dönen topa vurdu, {gk} bir kez daha engelledi!", weight=9, when="box"),
    # -- uzaktan ---------------------------------------------------------------
    L("{p} ({t}) uzaktan patlattı, {gk} havada topu yakaladı!", weight=11, when="long"),
    L("{p} ({t}) ceza sahası dışından vurdu, {gk} yumrukladı.", weight=11, when="long"),
    L("{p} ({t}) uzaktan sert vurdu ama {gk} kornere çeldi!", weight=11, when="long"),
    L("{p} ({t}) otuz metreden denedi, {gk} üst köşeye uçtu!", weight=10, when="long"),
    L("{p} ({t}) uzaktan kavisli vurdu, {gk} son anda parmaklarının ucuyla çeldi!",
      weight=10, when="long"),
    L("{p} ({t}) uzaktan şansını zorladı, {gk} rahat kontrol etti.", weight=9, when="long"),
    # -- kafa --------------------------------------------------------------------
    L("{p} ({t}) kafayı vurdu, {gk} topu üstten kornere çeldi!", weight=12, when="header"),
    L("{p} ({t}) kafayla köşeye gönderdi ama {gk} oradaydı!", weight=11, when="header"),
    L("{p} ({t}) yükseldi, kafa vuruşunu {gk} çizgide çıkardı!", weight=11, when="header"),
    L("{p} ({t}) kafayı yere çarptırdı, {gk} seken topu kontrol etti.", weight=10, when="header"),
    L("{p_gen} kafa vuruşunda {gk} müthiş bir kurtarış yaptı!", weight=10, when="header"),
    # -- karsi karsiya --------------------------------------------------------------
    L("{p} ({t}) kaleciyle karşı karşıya! {gk} ayaklarıyla KURTARIYOR!", weight=13, when="oneonone"),
    L("{p} ({t}) tek başına kaldı ama {gk} üzerine çıkıp kapattı!", weight=12, when="oneonone"),
    L("{p} ({t}) aşırtmayı denedi, {gk} uzanıp topa dokundu!", weight=11, when="oneonone"),
    L("{p} ({t}) karşı karşıya pozisyonda vurdu, {gk} vücuduyla çıkardı!", weight=12, when="oneonone"),
    L("{gk} {p_gen} ({t}) karşısında büyüdü ve pozisyonu kapattı!", weight=11, when="oneonone"),
    # -- vole / dokunus ---------------------------------------------------------------
    L("{p} ({t}) voleyle vurdu, {gk} inanılmaz bir kurtarış yaptı!", weight=11, when="volley"),
    L("{p} ({t}) havadaki topa vurdu, {gk} refleksle çeldi.", weight=10, when="volley"),
    L("{p} ({t}) kalenin ağzında dokundu, {gk} çizgiden çıkardı!", weight=11, when="tap"),
    # -- baglam -------------------------------------------------------------------------
    L("{gk} takımını ayakta tutuyor, {p_gen} ({t}) vuruşu da geçmedi!", weight=9),
    L("{gk} bu maçta ne yaptıysa tuttu; {p} ({t}) bir kez daha aşamadı.", weight=7),
    L("{p} ({t}) beraberlik için vurdu, {gk} izin vermedi!", weight=10, when="pullback"),
    L("{p} ({t}) son dakikada vurdu, {gk} maçı kurtardı!", weight=11, when="closing"),
    L("Uzatmada {p} ({t}) denedi, {gk} yine oradaydı!", weight=10, when="stoppage"),
    L("Net pozisyondu ama {gk} {p_acc} ({t}) durdurdu!", weight=10, when="big"),
    L("{p} ({t}) umut vuruşu yaptı, {gk} sorunsuz topladı.", weight=9, when="far"),
    L("{d} açıyı daralttı, {p_gen} ({t}) vuruşunu {gk} kolay kurtardı.", weight=9, when="defended"),
    L("{d} baskısı altında vurdu {p} ({t}), {gk} rahat kontrol etti.", weight=8, when="defended"),
    L("{p} ({t}) farkı açmak istedi, {gk} geçit vermedi.", weight=8, when="ahead"),
    L("{gk} dizlerinin üstünde topu kucakladı, {p} ({t}) hayal kırıklığında.", weight=6),
    L("{p} ({t}) vurdu, {gk} topu yakın direkten çeldi.", weight=7),
    L("{p} ({t}) kaleyi zorladı, {gk} yere yatarak topladı.", weight=7),
    L("{gk} {p_gen} ({t}) vuruşunu kornere tokatladı.", weight=7),
    L("{p} ({t}) sert vurdu, {gk} topu önünde bıraktı ama savunma tamamladı.", weight=6),
    L("{gk} {p_gen} ({t}) şutunda doğru pozisyon aldı.", weight=6),
    L("{p} ({t}) vurdu, {gk} topu direğin dibinden çıkardı!", weight=8, when="box"),
    L("{p} ({t}) uzaktan denedi, {gk} topu iki elle yakaladı.", weight=8, when="long"),
    L("{p} ({t}) kafayla yere vurdu, {gk} seken topu tuttu.", weight=8, when="header"),
    L("{gk} {p_gen} ({t}) karşısında son ana kadar bekledi ve kazandı!", weight=9, when="oneonone"),
    L("{p} ({t}) solo akınını vuruşla bitirdi, {gk} kurtardı.", weight=8, when="solo"),
    L("{p} ({t}) üçüncüyü arıyordu, {gk} izin vermedi.", weight=7, when="extend"),
)

SAVE_PENALTY: tuple[Line, ...] = (
    L("{gk} doğru köşeye uzandı ve {p_gen} penaltısını KURTARDI!",
      "{gk} {p_gen} penaltısını kurtardı.", 12),
    L("{p_gen} vuruşu zayıftı, {gk} çeldi!", "{gk}, {p_gen} penaltısını çeldi.", 11),
    L("{gk} kalede devleşti, penaltıyı çıkardı!", "{gk}, {p_gen} penaltısını çıkardı.", 11),
    L("{gk} bekledi, {p_gen} ne yapacağını okudu ve kurtardı!",
      "{gk}, {p_gen} penaltısını okuyup kurtardı.", 10),
    L("{gk} ayağıyla çıkardı! {p} inanamıyor!", "{gk}, {p_gen} penaltısını ayağıyla çıkardı.", 9),
    L("{gk} penaltıyı kurtardı ve takımını ayakta tuttu!",
      "{gk}, {p_gen} penaltısını kurtarıp takımını ayakta tuttu.", 10),
    L("{gk} son dakika penaltısını kurtardı! Muhteşem!",
      "{gk}, son dakikada {p_gen} penaltısını kurtardı.", 11, "closing"),
)

SAVE_FREE_KICK: tuple[Line, ...] = (
    L("{p} serbest vuruşu kaleye gönderdi, {gk} uçarak kurtardı!", weight=12),
    L("{p} frikikten sert vurdu, {gk} topu kornere çeldi.", weight=12),
    L("{p} barajın üstünden aşırttı, {gk} üst köşeye uzandı!", weight=11),
    L("{p_gen} frikiğinde {gk} doğru köşeyi seçti.", weight=10),
    L("{p} serbest vuruşu doksana gönderdi ama {gk} oradaydı!", weight=11),
    L("{p} frikikten vurdu, {gk} iki hamlede topladı.", weight=9),
    L("{p_gen} serbest vuruşunu {gk} yumrukla uzaklaştırdı.", weight=9),
)

SAVE_CORNER: tuple[Line, ...] = (
    L("{a_gen} kornerinde {p} ({t}) kafayı vurdu, {gk} gole izin vermedi!",
      weight=12, when="assisted"),
    L("{a} korneri kullandı, {p} ({t}) yükseldi ama {gk} kurtardı!", weight=11, when="assisted"),
    L("{a_gen} korner ortasında {p} ({t}) dokundu, {gk} çizgide çıkardı!", weight=10, when="assisted"),
    L("Kornerde {p} ({t}) kafayı vurdu, {gk} refleksle çeldi!", weight=11),
    L("Korner sonrası {p} ({t}) vurdu, {gk} topu kontrol etti.", weight=11),
    L("Kornerde {p} ({t}) yükseldi, {gk} üstten kornere çeldi.", weight=10),
    L("Kornerde karambol oldu, {gk} topu kucakladı.", weight=9),
    L("Kornerde {p} ({t}) en iyi şansı buldu ama {gk} geçit vermedi!", weight=10),
    L("Kornerde {p} ({t}) yakın mesafeden vurdu, {gk} refleksle karşıladı!", weight=9),
    L("{a_gen} korner ortasına {p} ({t}) kafa vurdu, {gk} topu tuttu.", weight=9, when="assisted"),
    L("Korner sonrası {gk} kalabalığın içinden topu aldı.", weight=8),
)

# ------------------------------------------------------------- KURULUS (ZINCIR)
BUILDUP_WIN: tuple[Line, ...] = (
    L("{p} ({t}) topu orta alanda kazandı.", weight=10),
    L("{p} ({t}) araya girdi ve topu kaptı.", weight=10),
    L("{p} ({t}) pres yaptı, top {t_loc}.", weight=9),
    L("{t} topu kendi yarı sahasında geri kazandı, {p} ayakta.", weight=8),
    L("{p} ({t}) ikili mücadeleden topla çıktı.", weight=9),
    L("{p} ({t}) rakibin pasını kesti.", weight=9),
    L("{p} ({t}) topu çevirdi, {t} yeniden kurulumda.", weight=8),
    L("{t} savunmadan temiz çıktı, top {p_loc}.", weight=8),
    L("{p} ({t}) kalecinin dağıtımıyla oyunu başlattı.", weight=7),
    L("{p} ({t}) taç atışının ardından topu ilerletti.", weight=6),
    L("{t} topu kazandı ve hızlı çıkmak istiyor.", weight=8),
    L("{p} ({t}) rakip yarı sahada topu kazandı! Tehlikeli bölge!", weight=8, when="big"),
    L("{p} ({t}) hatalı pası affetmedi, top {t_loc} kaldı.", weight=7),
    L("{t} kontraya çıkıyor, topu taşıyan {p}.", weight=9, when="oneonone"),
    L("{t} hızla çıkıyor! {p} topu sürüyor!", weight=9, when="solo"),
    L("Geride kalan {t} topu kazandı, {p} öne bakıyor.", weight=8, when="pullback"),
    L("{t} oyunu yavaşlattı, top {p_loc} dolaşıyor.", weight=7, when="ahead"),
    L("{p} ({t}) çizgi boyunca topu ileri taşıyor.", weight=8),
    L("{p} ({t}) rakibi sırtına alıp topu koruyor.", weight=7),
    L("{p} ({t}) ceza sahasına doğru yöneliyor.", weight=7),
    L("Orta sahada mücadele {t_gen} lehine bitti.", weight=7),
    L("{p} ({t}) kısa paslarla oyunu yukarı taşıdı.", weight=8),
    L("{p} ({t}) geniş bir pasla oyunu değiştirdi.", weight=8),
    L("{p} ({t}) topu kesti ve hemen ileriye baktı.", weight=8),
    L("{p} ({t}) topu savunmanın önünde kaptı.", weight=7),
    L("{p} ({t}) rakibin hatalı pasını yakaladı.", weight=7),
    L("{p} ({t}) ikili mücadeleyi kazandı, {t} atağa çıkıyor.", weight=7),
    L("{t} topu geri kazandı; {p} oyunu yönlendiriyor.", weight=7),
    L("{p} ({t}) boşta kalan topa ilk yetişen oldu.", weight=7),
    L("{p} ({t}) araya girdi ve hızla ileri çıktı.", weight=7),
)

BUILDUP_ENTRY: tuple[Line, ...] = (
    L("{p} ({t}) son üçte bire giriyor.", weight=10),
    L("{p} ({t}) kanattan içeri kat etti.", weight=10),
    L("{p} ({t}) rakibini geçti ve ceza sahasına yaklaştı.", weight=10),
    L("{p} ({t}) topu ceza sahasının önüne taşıdı.", weight=9),
    L("{t} savunmanın arkasını zorluyor, top {p_loc}.", weight=9),
    L("{p} ({t}) çizgiye indi, ortaya bakıyor.", weight=9),
    L("{p} ({t}) duvar pasıyla savunmayı kırdı.", weight=9),
    L("{p} ({t}) arkaya sarkıyor, savunma geriliyor.", weight=8),
    L("{p} ({t}) topu ayağında tutarak ceza sahasına yaklaştı.", weight=8),
    L("{t} ceza sahasına giriyor, tehlike büyüyor.", weight=8),
    L("{p} ({t}) hızını kullanarak savunmayı aştı.", weight=8, when="solo"),
    L("{p} ({t}) çalımla bir adam eksiltti.", weight=8, when="solo"),
    L("{p} ({t}) kanatta iki kişiyi geride bıraktı.", weight=7, when="solo"),
    L("{p} ({t}) savunma arasındaki boşluğa koşuyor!", weight=9, when="oneonone"),
    L("{p} ({t}) ofsayt çizgisini doğru okudu ve arkaya sarktı!", weight=9, when="oneonone"),
    L("{p} ({t}) uzun topla buluştu, savunma peşinde.", weight=8),
    L("{d} geride kaldı, {p} ({t}) alanı buldu.", weight=9, when="defended"),
    L("{d} {p_acc} ({t}) yakalayamadı, ceza sahası açık.", weight=8, when="defended"),
    L("{p} ({t}) ceza yayına doğru sürüyor, vuruş arıyor.", weight=9, when="long"),
    L("{p} ({t}) topu dışarı çekti, vuracak alan arıyor.", weight=8, when="long"),
    L("{t} orta yapmak için kanada açıldı.", weight=8, when="header"),
    L("{p} ({t}) kanattan ortaya hazırlanıyor.", weight=8, when="header"),
    L("{t} baskıyı sürdürüyor, top ceza sahasının etrafında dolaşıyor.", weight=8),
    L("{p} ({t}) geri pasla oyunu açtı ve yeniden yükleniyor.", weight=7),
    L("{p} ({t}) ceza sahasının köşesine ulaştı.", weight=7),
    L("{p} ({t}) içeri kat edip boşluk kolluyor.", weight=7),
    L("{p} ({t}) bir çalımla savunmayı açtı.", weight=7),
    L("{t} kısa paslarla ceza sahasına yaklaştı, top {p_loc}.", weight=7),
    L("{p} ({t}) topu savunmanın arasına sürdü.", weight=7),
    L("{p} ({t}) kanatta hız kazandı.", weight=7),
    L("{t} kalabalık hücum ediyor, {p} topla ilerliyor.", weight=7, when="pullback"),
    L("{t} kontra fırsatı buldu, {p} alan yakalıyor.", weight=7, when="ahead"),
)

BUILDUP_FINAL: tuple[Line, ...] = (
    L("{p} ({t}) son pası veriyor!", weight=10),
    L("{p} ({t}) topu ceza sahasına gönderdi!", weight=10),
    L("{p} ({t}) araya sızan arkadaşını gördü!", weight=10),
    L("{p_gen} ({t}) ortası ceza sahasına geliyor!", weight=10),
    L("{p} ({t}) topu ortaya çevirdi!", weight=9),
    L("{p} ({t}) diagonal pasla savunmayı böldü!", weight=9),
    L("{p} ({t}) çizgiden ortaladı!", weight=9),
    L("{p} ({t}) topu geri çekti, vuruş için hazırlık!", weight=9),
    L("{p} ({t}) savunmanın arkasına attı, koşu başladı!", weight=9),
    L("{p} ({t}) ceza sahasında boşta bir arkadaş buldu!", weight=9),
    L("{p} ({t}) kısa pasla ceza sahasında oyunu böldü!", weight=8),
    L("{p} ({t}) topu yerden ortaya gönderdi!", weight=8),
    L("{p} ({t}) yüksek bir orta yaptı, ceza sahasında mücadele var!", weight=9, when="header"),
    L("{p_gen} ({t}) ortası kale sahasına düşüyor!", weight=9, when="header"),
    L("{p} ({t}) arka direğe gönderdi!", weight=8, when="header"),
    L("{p} ({t}) topu savunmanın arkasına yolladı, kaleciyle baş başa kalınıyor!",
      weight=10, when="oneonone"),
    L("{p} ({t}) ara pası buldu! Savunma bitti!", weight=10, when="oneonone"),
    L("{p} ({t}) topu dışarı bıraktı, vuruş geliyor!", weight=9, when="long"),
    L("{p} ({t}) ceza yayına geri çevirdi!", weight=8, when="long"),
    L("{p} ({t}) kendi yolunu açtı!", weight=9, when="solo"),
    L("{p} ({t}) çalımını attı ve vuruş alanına girdi!", weight=9, when="solo"),
    L("{p} ({t}) topu ayağına aldı, alan boş!", weight=8, when="big"),
    L("{t} bu pozisyonu iyi kurdu, {p} son dokunuşu yapıyor!", weight=8, when="big"),
    L("{p} ({t}) telaşlı bir pas verdi ama pozisyon devam ediyor.", weight=7, when="far"),
    L("{p} ({t}) zorlama bir pas denedi, top yine de ileri gitti.", weight=7, when="far"),
    L("{p} ({t}) kafasını kaldırdı ve topu bıraktı!", weight=7),
    L("{p} ({t}) topu tam koşu yoluna gönderdi!", weight=7),
    L("{p} ({t}) gördü ve ara pası verdi!", weight=7),
    L("{p} ({t}) içeri çevirdi, ceza sahası karışıyor!", weight=7),
    L("{p} ({t}) arkadaşını vuruşa hazırladı!", weight=7),
    L("{p} ({t}) topu ayağının içiyle vuruş alanına aktardı!", weight=7),
    L("{p} ({t}) son pasla oyunu bitirmeye hazırlanıyor!", weight=7, when="closing"),
    L("{p} ({t}) geride kalan takımı için son pası verdi!", weight=7, when="pullback"),
)

BUILDUP_PRESSURE: tuple[Line, ...] = (
    L("{t} son dakikalarda oyunu rakip yarı sahaya taşıdı.", weight=10),
    L("{t} topa sahip olmayı sürdürüyor, {o} geri çekildi.", weight=10),
    L("{t} baskısını artırdı, tempo yükseliyor.", weight=10),
    L("Oyun {o_gen} yarı sahasında oynanıyor.", weight=9),
    L("{t} sabırlı kurulumla boşluk arıyor.", weight=9),
    L("{t} kanat değiştirerek savunmayı yormaya çalışıyor.", weight=9),
    L("{o} blok kurdu, {t} çözüm arıyor.", weight=9),
    L("Tempo düştü, {t} topu elinde tutuyor.", weight=8),
    L("{t} üst üste ataklarla baskı kuruyor.", weight=9),
    L("{o} nefes almakta zorlanıyor, {t} yükleniyor.", weight=8),
    L("{t} taraftarını arkasına aldı, baskı sürüyor.", weight=8),
    L("Maçın kontrolü şu an {t_loc}.", weight=8),
    L("{t} geriden çıkmakta zorlanmıyor, oyun sakin akıyor.", weight=7),
    L("{t} oyunu yavaşlatıp skoru korumaya çalışıyor.", weight=9, when="ahead"),
    L("{t} her şeyi öne yığdı, savunmasını boş bıraktı.", weight=10, when="pullback"),
    L("{t} zamanın azaldığını biliyor ve topu hızlı oynuyor.", weight=9, when="closing"),
    L("{t} son çeyrekte ağırlığını koydu.", weight=8, when="late"),
    L("Maçın başında tempo yüksek, {t} yükleniyor.", weight=8, when="early"),
    L("{t} devre kapanmadan bir kez daha yüklendi.", weight=8, when="firsthalf"),
    L("Uzatma dakikalarında {t} her şeyi denedi.", weight=9, when="stoppage"),
    L("{t} farkı korumak için oyunu kesiyor.", weight=8, when="extend"),
    L("İşi bitiren {t} artık rahat oynuyor.", weight=8, when="seal"),
    L("{t} oyunu geniş alanlara yayıyor.", weight=8),
    L("{o} kendi yarı sahasına çekildi, {t} sabırla kuruyor.", weight=8),
    L("{t} topu savunmada dolaştırıyor, açık arıyor.", weight=8),
    L("Orta alanda karşılıklı hamleler, {t} biraz daha istekli.", weight=7),
    L("{t} temposunu yükseltti, {o} baskı altında.", weight=8),
    L("{t} topu ayağında tutuyor, {o} önde pres yapmıyor.", weight=7),
    L("Oyun bir süredir {t} kontrolünde akıyor.", weight=7),
    L("{t} kanatlardan yüklenmeyi deniyor.", weight=7),
)

# ---------------------------------------------------------------- DUSUK ETKILI
CORNER_WON: tuple[Line, ...] = (
    L("{t} korner kazandı.", weight=10),
    L("Savunma topu kornere gönderdi, {t} atışa hazırlanıyor.", weight=10),
    L("{p} ({t}) orta yapmak istedi, savunma kornere çeldi.", weight=10),
    L("{t} lehine korner.", weight=9),
    L("{p_gen} ({t}) şutu savunmadan döndü: korner.", weight=9),
    L("{t} bir korner daha kazandı, baskı sürüyor.", weight=9),
    L("Top savunmaya çarpıp dışarı çıktı, {t} korner kullanacak.", weight=9),
    L("{p} ({t}) çizgiye indi, top kornere gitti.", weight=8),
    L("{gk} topu kornere çeldi.", weight=8),
    L("{t} köşe vuruşu kazandı, ceza sahası doluyor.", weight=8),
    L("{t} kornerle bir şans daha yakaladı.", weight=8),
    L("{p} ({t}) ortasını savunma engelledi, korner.", weight=8),
    L("{t} art arda kornerlerle {o_acc} kendi sahasına hapsetti.", weight=7),
    L("{t} korner kazandı, herkes ceza sahasına yürüyor.", weight=8, when="closing"),
    L("Son dakikalarda {t} lehine korner! Kaleci bile yukarı çıkıyor!",
      weight=9, when="stoppage"),
    L("{t} kornerle son bir şans arıyor.", weight=8, when="pullback"),
    L("{t} korneri uzun oynayıp zaman kazanmak istiyor.", weight=7, when="ahead"),
    L("Maçın ilk korneri {t_gen}.", weight=7, when="early"),
    L("{t} kornerden tehlike yaratmaya çalışıyor.", weight=8),
    L("Ceza sahası kalabalıklaşıyor, {t} korner kullanacak.", weight=8),
    L("{p} ({t}) çizgiye kadar indi, savunma kornere attı.", weight=8),
    L("{t} bir korner daha; ceza sahasında kalabalık var.", weight=8),
    L("{p_gen} ({t}) ortası savunmadan sekti, korner.", weight=8),
    L("{gk} uzanıp topu çizgi dışına çeldi: korner.", weight=7),
    L("{t} korner kullanacak, uzun boylular yukarı çıkıyor.", weight=7),
    L("Savunma riske girmedi, topu kornere gönderdi.", weight=7),
    L("{t} lehine köşe vuruşu; {o} savunması dikkatli.", weight=7),
    L("{p} ({t}) kornere hazırlanıyor.", weight=7),
)

FOUL_EVENTS: tuple[Line, ...] = (
    L("{p} ({t}) faul yaptı, hakem düdüğü çaldı.", weight=10),
    L("{p} ({t}) rakibini düşürdü, oyun duruyor.", weight=10),
    L("Hakem {t_gen} faulünü verdi, serbest vuruş.", weight=10),
    L("{p} ({t}) geç kaldı ve faul yaptı.", weight=10),
    L("{p} ({t}) müdahalesinde topa değil rakibine gitti.", weight=9),
    L("Orta sahada sert bir mücadele, faul {t_gen}.", weight=9),
    L("{p} ({t}) omuz attı, hakem faul verdi.", weight=9),
    L("{p} ({t}) itti ve oyun durdu.", weight=8),
    L("Oyun bir süre durdu, {p} ({t}) faulünde hakem sakin kaldı.", weight=8),
    L("{p} ({t}) formadan çekti, hakem gördü.", weight=8),
    L("{p} ({t}) taktik faulle atağı kesti.", weight=9),
    L("{p} ({t}) arkadan geldi, hakem düdüğü çaldı.", weight=9),
    L("{t} faulü, {o} serbest vuruş kullanacak.", weight=9),
    L("{p} ({t}) elle oynadı, hakem faul verdi.", weight=7),
    L("Hakem avantajı bekledi ama sonra faulü verdi: {p} ({t}).", weight=7),
    L("{p} ({t}) kontrayı faulle durdurdu.", weight=8),
    L("{p_gen} ({t}) müdahalesi sert oldu, oyuncular hakeme yürüyor.", weight=7),
    L("{p} ({t}) ayağını fazla yukarı kaldırdı, faul.", weight=7),
    L("{p} ({t}) rakibine çarptı, hakem oyunu durdurdu.", weight=8),
    L("Tehlikeli bölgenin hemen dışında {t} faulü.", weight=8),
    L("{p} ({t}) son dakikalarda zamana oynamak için faul yaptı.", weight=8, when="closing"),
    L("Maçın ilk faulü {p_abl} ({t}) geldi.", weight=7, when="early"),
    L("Geride kalan {t} sertleşti, {p} faul yaptı.", weight=8, when="pullback"),
    L("Önde olan {t} oyunu kesiyor, {p} faul yaptı.", weight=8, when="ahead"),
    L("{p} ({t}) topun arkasından girdi, hakem durdurdu.", weight=8),
    L("{p} ({t}) rakibinin ayağına bastı, faul.", weight=8),
    L("{p} ({t}) hava topunda kolunu kullandı, faul.", weight=7),
    L("{p} ({t}) rakibini tutarak durdurdu.", weight=8),
    L("{p} ({t}) çelme taktı, hakem faulü gördü.", weight=8),
    L("{p} ({t}) sert bir omuz darbesiyle faul yaptı.", weight=7),
    L("Hakem oyunu durdurdu: {t_gen} oyuncusu {p} faul yaptı.", weight=7),
    L("{p} ({t}) topla birlikte rakibini de biçti.", weight=7),
    L("{p} ({t}) ceza sahası dışında faul yaptı, tehlikeli bölge.", weight=7),
    L("{p} ({t}) yan çizgi kenarında rakibini düşürdü.", weight=7),
    L("{o} lehine serbest vuruş; faul {p_abl} ({t}).", weight=7),
    L("{p} ({t}) atağı bozmak için faul yapmayı seçti.", weight=8),
)

OFFSIDE_EVENTS: tuple[Line, ...] = (
    L("{p} ({t}) ofsayta takıldı.", weight=10),
    L("Bayrak kalktı: {p} ({t}) ofsayt.", weight=10),
    L("{p} ({t}) erken hareket etti, ofsayt.", weight=10),
    L("{t} savunma arkasına attı ama {p} ofsaytta kaldı.", weight=10),
    L("Ofsayt! {p} ({t}) çizgiyi kıl payı geçti.", weight=9),
    L("{o} ofsayt tuzağını çalıştırdı, {p} ({t}) yakalandı.", weight=9),
    L("{p} ({t}) pozisyona girdi ama bayrak havada.", weight=9),
    L("Gol sevinci yarıda kaldı, {p} ({t}) ofsayttaydı.", weight=8),
    L("{p} ({t}) zamanlamayı kaçırdı, ofsayt.", weight=9),
    L("Hakem yardımcısı bayrağı kaldırdı: {t} ofsaytta.", weight=9),
    L("{t} savunma arkasını zorluyor ama yine ofsayt.", weight=8),
    L("Ofsayt kararı {t_acc} durdurdu.", weight=8),
    L("{p} ({t}) koşusunu erken başlattı.", weight=8),
    L("Çok yakın bir ofsayt kararı, {t} itiraz ediyor.", weight=7),
    L("{p} ({t}) arkaya sarktı ama bayrak kalktı.", weight=8),
    L("Son dakikada ofsayt! {t} inanamıyor!", weight=9, when="closing"),
    L("{t} beraberlik peşinde ama ofsayta takıldı.", weight=8, when="pullback"),
    L("{t} kontrada ofsayta düştü.", weight=8, when="ahead"),
    L("Maçın ilk ofsaytı {t_gen}.", weight=7, when="early"),
    L("{p} ({t}) yine ofsayt çizgisinin gerisinde kalamadı.", weight=7),
    L("{p} ({t}) arkaya koştu ama erken çıktı, ofsayt.", weight=8),
    L("{t} derin bir pas denedi, {p} ofsaytta kaldı.", weight=8),
    L("Bayrak havada, {p} ({t}) savunmanın gerisindeydi.", weight=8),
    L("{p} ({t}) çizgiyi zorladı ama yardımcı hakem affetmedi.", weight=7),
    L("{o} savunması aynı anda çıktı: {p} ({t}) ofsayt.", weight=7),
    L("{p} ({t}) pozisyon beklerken ofsayta düştü.", weight=7),
)

# ----------------------------------------------------------------- DISIPLIN
YELLOW_CARDS: tuple[Line, ...] = (
    L("{p} ({t}) sert müdahale etti, hakem sarı kartı gösterdi.",
      "{p} sarı kart gördü.", 10),
    L("{p} ({t}) geç kaldı ve rakibini düşürdü: SARI KART.",
      "{p} geç müdahaleden sarı kart gördü.", 10),
    L("{p} ({t}) itiraz etti, hakemin eli cebine gitti: sarı kart.",
      "{p} itirazdan sarı kart gördü.", 9),
    L("{p} ({t}) kontrayı faulle durdurdu ve sarı kart gördü.",
      "{p} taktik faulden sarı kart gördü.", 10),
    L("{p} ({t}) topa geç gitti, hakem kartını çıkardı.",
      "{p} sarı kart gördü.", 9),
    L("{p} ({t}) formadan çekti, sarı kart.", "{p} formadan çekince sarı kart gördü.", 9),
    L("{p} ({t}) oyunu geciktirdi ve sarı kart gördü.",
      "{p} oyunu geciktirdiği için sarı kart gördü.", 8),
    L("{p} ({t}) ikili mücadelede dirseğini kullandı: sarı kart.",
      "{p} sert mücadeleden sarı kart gördü.", 8),
    L("{p} ({t}) hakemin kararına tepki gösterdi, sarı kart.",
      "{p} tepkisi nedeniyle sarı kart gördü.", 8),
    L("{p} ({t}) arkadan müdahale etti, hakem tereddüt etmedi.",
      "{p} arkadan müdahaleden sarı kart gördü.", 9),
    L("{p} ({t}) kaleciye itiraz etti ve kartını gördü.",
      "{p} sarı kart gördü.", 7),
    L("{p} ({t}) sert girdi, hakem oyuncuyu uyardı ve sarı gösterdi.",
      "{p} sert müdahaleden sarı kart gördü.", 8),
    L("{p} ({t}) atağı kesmek için formadan tuttu: sarı kart.",
      "{p} atağı kestiği için sarı kart gördü.", 8),
    L("{p} ({t}) gereksiz bir faulle sarı kart gördü.",
      "{p} gereksiz bir faulden sarı kart gördü.", 8),
    L("{p} ({t}) zaman geçirmek istedi, hakem sarı kart gösterdi.",
      "{p} zaman geçirdiği için sarı kart gördü.", 8, "closing"),
    L("{p} ({t}) gerginliği tırmandırdı ve sarı kart gördü.",
      "{p} gerginlikte sarı kart gördü.", 7, "late"),
    L("Maçın ilk sarı kartı {p_dat} ({t}) gösterildi.",
      "Maçın ilk sarı kartını {p} gördü.", 7, "early"),
    L("{p} ({t}) baskı altında sinirlendi, sarı kart.",
      "{p} sarı kart gördü.", 7, "pullback"),
    L("{p} ({t}) rakibinin önünü kesti, sarı kart.",
      "{p} önünü kestiği için sarı kart gördü.", 8),
    L("{p} ({t}) kontrolsüz bir müdahale yaptı, hakem sarıyı çıkardı.",
      "{p} kontrolsüz müdahaleden sarı kart gördü.", 8),
    L("{p} ({t}) hızlı kontrayı durdurdu, kartı kaçınılmazdı.",
      "{p} kontrayı durdurduğu için sarı kart gördü.", 8),
    L("{p} ({t}) topu elle oynadı, sarı kart.",
      "{p} elle oynadığı için sarı kart gördü.", 7),
    L("{p} ({t}) üst üste faul yaptı ve sarıyı gördü.",
      "{p} faul serisi sonrası sarı kart gördü.", 7),
    L("{p} ({t}) rakibine omuz attı, hakem sarı gösterdi.",
      "{p} sarı kart gördü.", 7),
    L("{p} ({t}) topu kaçırmamak için geç girdi: sarı kart.",
      "{p} geç müdahaleden sarı kart gördü.", 7),
    L("{p} ({t}) hakemle tartıştı, sarı kart.",
      "{p} tartışma sonrası sarı kart gördü.", 7),
)

RED_STRAIGHT: tuple[Line, ...] = (
    L("{p} ({t}) korkunç bir faul yaptı, DİREKT KIRMIZI KART!",
      "{p} korkunç bir faulle direkt kırmızı kart gördü.", 12),
    L("{p} ({t}) çift ayak girdi! Hakem hiç düşünmeden kırmızı kartı gösterdi!",
      "{p} çift ayak müdahaleden direkt kırmızı kart gördü.", 11),
    L("{p} ({t}) net gol şansını faulle engelledi: KIRMIZI KART!",
      "{p} net gol şansını engellediği için kırmızı kart gördü.", 11),
    L("{p} ({t}) kontrolünü kaybetti ve oyundan atıldı!",
      "{p} kontrolünü kaybedip oyundan atıldı.", 10),
    L("{p} ({t}) rakibine sert bir hareket yaptı, hakem kırmızı kartı çıkardı!",
      "{p} sert hareketi nedeniyle kırmızı kart gördü.", 10),
    L("{p} ({t}) hakeme ağır sözler söyledi ve kırmızı kart gördü!",
      "{p} hakeme itirazdan kırmızı kart gördü.", 8),
    L("Maçın kırılma anı: {p} ({t}) kırmızı kart gördü!",
      "{p_gen} kırmızı kartı maçın kırılma anı oldu.", 9),
)

# NOT: her satir "ikinci sari" ifadesini TASIMAK zorundadir (tests/test_match_engine.py
# ikinci sari kirmizisini metinden dogrular; yapisal isaret `detail="second_yellow"`).
RED_SECOND: tuple[Line, ...] = (
    L("{p} ({t}) ikinci sarıdan KIRMIZI KART gördü!",
      "{p} ikinci sarıdan oyun dışı kaldı.", 12),
    L("{p} ({t}) zaten sarı kartlıydı: ikinci sarı ve kırmızı!",
      "{p} sarı kartlıyken ikinci sarısını gördü.", 11),
    L("{p} ({t}) ikinci sarı kartını gördü ve oyundan çıkarıldı!",
      "{p} ikinci sarı kartıyla atıldı.", 11),
    L("Gereksiz bir faul: {p} ({t}) ikinci sarıdan gidiyor!",
      "{p} gereksiz bir faulle ikinci sarısını gördü.", 10),
    L("{p} ({t}) sarı kartlı oynadığını unuttu, ikinci sarı geldi!",
      "{p} sarı kartlıyken ikinci kartını gördü.", 10),
    L("{p} ({t}) ikinci sarısını gördü, {t} eksik kaldı!",
      "{p} ikinci sarıyla atıldı ve {t} eksik kaldı.", 11),
    L("Hakem {p_dat} ({t}) ikinci sarıyı gösterdi: kırmızı kart!",
      "{p} ikinci sarıdan kırmızı kart gördü.", 10),
    L("{p} ({t}) atağı kesti ama ikinci sarıyı da beraberinde aldı!",
      "{p} atağı keserken ikinci sarısını gördü.", 9),
    L("{p} ({t}) itiraz etti ve ikinci sarıdan oyun dışı kaldı!",
      "{p} itirazdan ikinci sarısını gördü.", 8),
    L("Kritik dakikada {p} ({t}) ikinci sarıyı gördü!",
      "Kritik dakikada {p} ikinci sarıdan atıldı.", 10, "late"),
)

# --------------------------------------------------------------------- DEGISIKLIK
# Yuvalar: {pin} oyuna giren, {pout} cikan. Motor basina "Degisiklik ({t}): " ekler
# (mevcut testler bu onekle degisiklik olaylarini ayirt ediyor) ve yorgunluk
# gerekcesindeki "yoruldu" kelimesi her satirda BULUNMAK zorundadir.
SUB_TIRED: tuple[Line, ...] = (
    L("{pout} yoruldu, yerine {pin} giriyor.", weight=10),
    L("{pout} yoruldu; {pin} sahaya çıkıyor.", weight=10),
    L("{pout} yoruldu, dinlenmeye gidiyor ve {pin} oyuna alınıyor.", weight=10),
    L("{pout} yoruldu, taze bacaklar için {pin} giriyor.", weight=10),
    L("{pout} yoruldu, teknik heyet {pin_acc} sahaya sürüyor.", weight=9),
    L("{pout} yoruldu, temposu düştü; yerini {pin_dat} bırakıyor.", weight=9),
    L("{pout} yoruldu, alkışlarla çıkıyor; {pin} giriyor.", weight=9),
    L("{pout} yoruldu, kenara geliyor ve {pin} oyuna dahil oluyor.", weight=9),
    L("{pout} yoruldu, koşu mesafesi tükendi; {pin} giriyor.", weight=8),
    L("{pout} yoruldu, nefes nefese kalmıştı; {pin} onun yerini alıyor.", weight=8),
    L("{pout} yoruldu, dördüncü hakem tabelayı kaldırdı: {pin} giriyor.", weight=8),
    L("{pout} yoruldu, son bölüm için {pin} sahada.", weight=8),
    L("{pout} yoruldu, kenardan işaret geldi; {pin} oyuna giriyor.", weight=8),
    L("{pout} yoruldu, bacakları ağırlaştı; yerine {pin} giriyor.", weight=8),
    L("{pout} yoruldu, uzun süredir zorlanıyordu; {pin} onu rahatlatıyor.", weight=7),
    L("{pout} yoruldu, son dakikalar için {pin} taze bir soluk.", weight=8, when="late"),
    L("{pout} yoruldu, yerini {pin_dat} bırakırken tribünler alkışlıyor.", weight=7),
    L("{pout} yoruldu; teknik heyet {pin_acc} hazırladı ve değişiklik yapıldı.", weight=7),
    L("{pout} yoruldu, {pin} oyuna giriyor, taze enerji geliyor.", weight=7),
    L("{pout} yoruldu, adımları ağırlaştı; {pin} sahada.", weight=7),
    L("{pout} yoruldu ve kenara çağrıldı; {pin} ısınmasını tamamladı, oyunda.", weight=7),
    L("{pout} yoruldu, tempoya ayak uyduramıyordu; {pin} giriyor.", weight=7),
)

SUB_ATTACK: tuple[Line, ...] = (
    L("{pout} hücum için çıkıyor, {pin} forvet olarak giriyor.", weight=10),
    L("Hücum için hamle: {pout} çıkıyor, {pin} öne ekleniyor.", weight=10),
    L("{t} riske giriyor, hücum için {pin} sahada; {pout} çıkıyor.", weight=10),
    L("Hücum için kart değişti: {pout} yerine {pin}.", weight=9),
    L("{pout} kenara geliyor, hücum için {pin} oyuna giriyor.", weight=9),
    L("Teknik heyet hücum için forvet oyuncusu {pin_acc} sokuyor, {pout} çıkıyor.", weight=9),
    L("{t} her şeyi öne yığıyor: {pout} çıktı, {pin} girdi.", weight=9),
    L("Hücum için son hamle: {pin} giriyor, {pout} çıkıyor.", weight=9, when="closing"),
    L("Geride kalan {t} hücum için {pin_acc} oyuna aldı.", weight=9, when="pullback"),
    L("{pout} yerine {pin}: {t} gol arıyor.", weight=8),
)

SUB_DEFEND: tuple[Line, ...] = (
    L("{pout} çıkıyor, skoru korumak için {pin} giriyor.", weight=10),
    L("Skoru korumak için hamle: {pin} giriyor, {pout} çıkıyor.", weight=10),
    L("{t} kapanıyor; skoru korumak için {pin} sahada.", weight=10),
    L("{pout} kenara geliyor, orta sahayı sağlamlaştırmak için {pin} girdi.", weight=9),
    L("Teknik heyet dengeyi korumak istiyor: {pout} çıktı, {pin} girdi.", weight=9),
    L("{t} öndeyken oyunu kapatıyor, {pin} oyuna alındı.", weight=9),
    L("Son dakikalar için savunma hamlesi: {pin} giriyor.", weight=9, when="closing"),
    L("{t} farkı korumak için {pin_acc} oyuna aldı, {pout} çıktı.", weight=9, when="extend"),
    L("{pout} yerine {pin}: {t} skoru elinde tutmak istiyor.", weight=8),
)

INJURY_EVENTS: tuple[Line, ...] = (
    L("{p} ({t}) yerde kaldı, sağlık ekibi sahada... Oyuna devam edemiyor!",
      "{p} sakatlanarak oyundan çıktı.", 10),
    L("{p} ({t}) ikili mücadelede sakatlandı, sedyeyle oyundan ayrılıyor.",
      "{p} ikili mücadelede sakatlandı.", 10),
    L("{p} ({t}) kas sakatlığı işareti verdi ve oyunu bırakmak zorunda kaldı.",
      "{p} kas sakatlığı nedeniyle oyunu bıraktı.", 10),
    L("{p} ({t}) ayak bileğini tuttu, devam edemiyor.",
      "{p} ayak bileği sakatlığıyla çıktı.", 9),
    L("{p} ({t}) kötü düştü ve oyuna devam edemedi.",
      "{p} sakatlanarak oyundan ayrıldı.", 9),
    L("{p} ({t}) arka adalesini tuttu, çıkmak zorunda.",
      "{p} arka adale sakatlığı yaşadı.", 9),
    L("{p} ({t}) çarpışmanın ardından ayağa kalkamadı.",
      "{p} çarpışma sonrası oyundan çıktı.", 8),
    L("{p} ({t}) dizini tuttu, kötü görünüyor.",
      "{p} diz sakatlığı nedeniyle çıktı.", 8),
)

# ----------------------------------------------------------------- BANKA HARITASI
BANK: dict[str, tuple[Line, ...]] = {
    "goal.open": GOAL_OPEN,
    "goal.penalty": GOAL_PENALTY,
    "goal.free_kick": GOAL_FREE_KICK,
    "goal.corner": GOAL_CORNER,
    "miss.open": MISS_OPEN,
    "miss.penalty": MISS_PENALTY,
    "miss.free_kick": MISS_FREE_KICK,
    "miss.corner": MISS_CORNER,
    "save.open": SAVE_OPEN,
    "save.penalty": SAVE_PENALTY,
    "save.free_kick": SAVE_FREE_KICK,
    "save.corner": SAVE_CORNER,
    "buildup.win": BUILDUP_WIN,
    "buildup.entry": BUILDUP_ENTRY,
    "buildup.final": BUILDUP_FINAL,
    "buildup.pressure": BUILDUP_PRESSURE,
    "corner.won": CORNER_WON,
    "foul.play": FOUL_EVENTS,
    "offside.play": OFFSIDE_EVENTS,
    "yellow.card": YELLOW_CARDS,
    "red.straight": RED_STRAIGHT,
    "red.second": RED_SECOND,
    "injury.play": INJURY_EVENTS,
    "sub.tired": SUB_TIRED,
    "sub.attack": SUB_ATTACK,
    "sub.defend": SUB_DEFEND,
}

# Mac raporunda kullanilacak yedek (report bos birakilmis satirlar icin)
REPORT_FALLBACK: dict[str, str] = {
    "goal.open": "{p} golü attı.",
    "goal.penalty": "{p} penaltıdan attı.",
    "goal.free_kick": "{p} frikikten attı.",
    "goal.corner": "Kornerden gelen golü {p} attı.",
    "miss.open": "{p} pozisyonu değerlendiremedi.",
    "miss.penalty": "{p} penaltıyı kaçırdı.",
    "save.open": "{gk} {p_gen} vuruşunu kurtardı.",
    "save.penalty": "{gk} penaltıyı kurtardı.",
    "yellow.card": "{p} sarı kart gördü.",
    "red.straight": "{p} kırmızı kart gördü.",
    "red.second": "{p} ikinci sarıdan atıldı.",
    "injury.play": "{p} sakatlanarak çıktı.",
}


# ===========================================================================
# [4] SECICI (agirlik + tekrar onleyici halka tampon)
# ===========================================================================

RING_SIZE = 6            # her bucket icin en son kullanilan N satir tekrar edilmez
_DRAW_TRIES = 5          # halkadaki satira denk gelirsek bu kadar alternatif cekilir

# (anahtar, baglam) -> (aday satirlar, kumulatif agirliklar). Modul omru boyunca yasar;
# sablon sayisi sabit oldugu icin sinirlidir ve isinma disinda maliyeti yoktur.
_CANDIDATES: dict[tuple[str, frozenset[str]], tuple[tuple[Line, ...], list[int]]] = {}


def candidates(key: str, tags: frozenset[str]) -> tuple[tuple[Line, ...], list[int]]:
    cached = _CANDIDATES.get((key, tags))
    if cached is not None:
        return cached
    pool = BANK.get(key, ())
    picked = tuple(line for line in pool if line.when <= tags)
    if not picked:                       # hicbir sey uymadi: etiketsiz satirlar
        picked = tuple(line for line in pool if not line.when) or pool
    cum, total = [], 0
    for line in picked:
        total += line.weight
        cum.append(total)
    _CANDIDATES[(key, tags)] = (picked, cum)
    return picked, cum


class Narrator:
    """
    Olay -> cumle. Motorun sonuc RNG'sinden AYRI bir rastgele akis kullanir (K11):
    anlatim secimleri maclarin sonucunu asla degistiremez.
    """

    __slots__ = ("rng", "_ring")

    def __init__(self, seed: int) -> None:
        self.rng = Random(seed)
        self._ring: dict[str, deque[int]] = {}

    def choose(self, key: str, tags: frozenset[str]) -> Line:
        pool, cum = candidates(key, tags)
        if not pool:
            return Line(live="")
        if len(pool) == 1:
            return pool[0]
        ring = self._ring.get(key)
        if ring is None:
            ring = self._ring[key] = deque(maxlen=min(RING_SIZE, len(pool) - 1))
        total = cum[-1]
        random = self.rng.random
        chosen = pool[bisect_right(cum, random() * total)]
        tries = 1
        while id(chosen) in ring and tries < _DRAW_TRIES:   # son kullanilanlardan kacin
            chosen = pool[bisect_right(cum, random() * total)]
            tries += 1
        ring.append(id(chosen))
        return chosen

    def live(self, key: str, tags: frozenset[str], slots: Slots) -> str:
        return self.choose(key, tags).live.format_map(slots)

    def both(self, key: str, tags: frozenset[str], slots: Slots) -> tuple[str, str]:
        """(canli cumle, gecmis zaman rapor cumlesi). Rapor bos ise yedek sablon kullanilir."""
        line = self.choose(key, tags)
        report = line.report or REPORT_FALLBACK.get(key, "")
        return line.live.format_map(slots), (report.format_map(slots) if report else "")


_MASK64 = (1 << 64) - 1


def pick(key: str, tags: frozenset[str], token: int, ring: tuple[int, ...]) -> Line:
    """
    Tembel (lazy) secim: `Narrator.choose` ile ayni agirliklar ve ayni tekrar onleme, ama
    rastgelelik olaya ozel bir sayidan (`token`) turetilir. Boylece bir cumle ancak
    OKUNDUGUNDA kurulabilir ve sonuc okunma sirasindan bagimsizdir. `ring`, ayni kovadaki
    onceki olaylarin sectigi satirlarin kimlikleridir (en yenisi sonda).
    """
    pool, cum = candidates(key, tags)
    if not pool:
        return Line(live="")
    total = cum[-1]
    state = token & _MASK64
    line = pool[0]
    for _ in range(_DRAW_TRIES):
        state = (state * 6364136223846793005 + 1442695040888963407) & _MASK64   # LCG (Knuth)
        line = pool[bisect_right(cum, ((state >> 11) / 9007199254740992.0) * total)]
        if id(line) not in ring:
            break
    return line


def bank_size() -> dict[str, int]:
    """Testler ve ayiklama icin: bucket basina satir sayisi."""
    return {key: len(lines) for key, lines in BANK.items()}


def total_lines() -> int:
    return sum(len(lines) for lines in BANK.values())
