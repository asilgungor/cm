"""
name_pools.py
=============
Ulke bazli kurgusal isim havuzlari (10. Asama'da seed.py'den ayrildi). SAF VERI: DB bilmez.

    NAME_POOLS / COUNTRY_POOL : sentetik dunya ve FM kadro tamamlama (seed.NameFactory).
                                DOKUNMA: havuz uzunlugu degisirse ayni tohumla uretilen
                                kidemli oyuncu adlari da degisir (dunya birebir ayni kalmali).
    YOUTH_NAME_POOLS          : altyapi/genc girisi (youth.py). NAME_POOLS + ek adlar; kucuk
                                havuzlu ulkelerde (Ispanya/Almanya/Fransa: 10x10) her sezon gelen
                                genclerle ad cakismasi hizla artmasin diye genisletildi.

Ulke adi lig ulkesidir ("Türkiye", "İngiltere", ...). Havuzu olmayan ulke Ingiltere havuzunu kullanir.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

NAME_POOLS: dict[str, tuple[Sequence[str], Sequence[str]]] = {
    "Turkiye": (
        ("Ahmet", "Mehmet", "Mustafa", "Emre", "Burak", "Kerem", "Arda", "Serdar",
         "Hakan", "Volkan", "Caner", "Cengiz", "Okan", "Oguz", "Yusuf", "Baris",
         "Ugur", "Tolga", "Selcuk", "Kaan", "Efe", "Berkay", "Halil", "Ilhan", "Dogan"),
        ("Yilmaz", "Kaya", "Demir", "Sahin", "Celik", "Yildiz", "Yildirim", "Ozturk",
         "Aydin", "Ozdemir", "Arslan", "Dogan", "Kilic", "Aslan", "Cetin", "Kara",
         "Koc", "Kurt", "Ozkan", "Simsek", "Polat", "Tas", "Bulut", "Gunes", "Erdem"),
    ),
    "Ingiltere": (
        ("James", "Harry", "Jack", "Oliver", "Charlie", "George", "Thomas", "Jacob",
         "Alfie", "Lewis", "Callum", "Ryan", "Connor", "Dylan", "Kyle", "Marcus",
         "Nathan", "Aaron", "Reece", "Declan", "Mason", "Ethan", "Jordan", "Liam", "Toby"),
        ("Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies",
         "Robinson", "Wright", "Thompson", "Evans", "Walker", "White", "Roberts",
         "Green", "Hall", "Wood", "Harris", "Clarke", "Baker", "Turner", "Hughes",
         "Edwards", "Mitchell"),
    ),
    "Italya": (
        ("Lorenzo", "Matteo", "Alessandro", "Andrea", "Francesco", "Marco", "Davide",
         "Simone", "Luca", "Federico", "Giuseppe", "Antonio", "Riccardo", "Stefano",
         "Gabriele", "Nicolo", "Emanuele", "Tommaso", "Giacomo", "Daniele", "Pietro",
         "Fabio", "Cristian", "Michele", "Salvatore"),
        ("Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Romano", "Colombo",
         "Ricci", "Marino", "Greco", "Bruno", "Gallo", "Conti", "De Luca", "Mancini",
         "Costa", "Giordano", "Rizzo", "Lombardi", "Moretti", "Barbieri", "Fontana",
         "Santoro", "Mariani", "Rinaldi"),
    ),
    "Ispanya": (
        ("Alvaro", "Diego", "Hugo", "Ivan", "Jorge", "Mario", "Pablo", "Raul", "Sergio", "Victor"),
        ("Alonso", "Blanco", "Castro", "Delgado", "Iglesias", "Molina", "Navarro", "Ortega",
         "Rubio", "Vidal"),
    ),
    "Almanya": (
        ("Ben", "David", "Fabian", "Jonas", "Leon", "Lukas", "Moritz", "Paul", "Tim", "Tobias"),
        ("Bauer", "Fischer", "Hoffmann", "Keller", "Koch", "Richter", "Schmitt", "Wagner",
         "Weber", "Wolf"),
    ),
    "Fransa": (
        ("Antoine", "Baptiste", "Clement", "Hugo", "Julien", "Louis", "Mathis", "Nathan", "Theo", "Yanis"),
        ("Bernard", "Dubois", "Fontaine", "Girard", "Lambert", "Laurent", "Leroy", "Moreau",
         "Petit", "Roux"),
    ),
}

DEFAULT_POOL = "Ingiltere"

# Lig ulkesi -> isim havuzu anahtari
COUNTRY_POOL = {
    "Türkiye": "Turkiye", "Turkiye": "Turkiye",
    "İngiltere": "Ingiltere", "Ingiltere": "Ingiltere",
    "İtalya": "Italya", "Italya": "Italya",
    "İspanya": "Ispanya", "Almanya": "Almanya", "Fransa": "Fransa",
}

# Genc girisi icin ek adlar (NAME_POOLS'a eklenir; kidemli dunya uretimini etkilemez)
_YOUTH_EXTRA: dict[str, tuple[Sequence[str], Sequence[str]]] = {
    "Turkiye": (
        ("Alp", "Anil", "Atakan", "Batuhan", "Cem", "Deniz", "Enes", "Eren", "Furkan", "Goktug",
         "Ismail", "Mert", "Onur", "Ozan", "Semih", "Sinan", "Taylan", "Umut", "Yigit", "Zeki"),
        ("Acar", "Akin", "Altun", "Avci", "Bayram", "Cakir", "Duman", "Eren", "Gok", "Guler",
         "Isik", "Karaca", "Kocak", "Oral", "Ozer", "Sari", "Tekin", "Turan", "Uysal", "Yavuz"),
    ),
    "Ingiltere": (
        ("Adam", "Ben", "Billy", "Cameron", "Danny", "Elliot", "Finley", "Freddie", "Harvey", "Jamie",
         "Joe", "Kieran", "Leo", "Luke", "Max", "Morgan", "Oscar", "Sam", "Tyler", "Zach"),
        ("Allen", "Bennett", "Carter", "Collins", "Cooper", "Foster", "Gray", "Hill", "Jackson",
         "King", "Lee", "Marshall", "Morris", "Parker", "Phillips", "Price", "Scott", "Shaw",
         "Stevens", "Ward"),
    ),
    "Italya": (
        ("Alberto", "Christian", "Diego", "Edoardo", "Elia", "Filippo", "Giorgio", "Leonardo",
         "Manuel", "Mattia", "Nicola", "Paolo", "Raffaele", "Samuele", "Vincenzo"),
        ("Amato", "Caruso", "Cattaneo", "D'Angelo", "Fabbri", "Ferrara", "Galli", "Leone",
         "Longo", "Martini", "Messina", "Palumbo", "Parisi", "Sala", "Villa"),
    ),
    "Ispanya": (
        ("Adrian", "Alejandro", "Carlos", "Daniel", "David", "Fernando", "Gonzalo", "Javier",
         "Jesus", "Juan", "Luis", "Manuel", "Marcos", "Miguel", "Nicolas", "Oscar", "Ruben",
         "Samuel", "Unai", "Xavier"),
        ("Cano", "Cortes", "Diaz", "Dominguez", "Fernandez", "Garcia", "Gil", "Gomez", "Herrera",
         "Lopez", "Marin", "Martinez", "Moreno", "Munoz", "Perez", "Ramos", "Romero", "Ruiz",
         "Sanchez", "Torres"),
    ),
    "Almanya": (
        ("Alexander", "Christian", "Dominik", "Elias", "Felix", "Finn", "Florian", "Jan", "Jannik",
         "Julian", "Kai", "Luca", "Marco", "Max", "Niklas", "Noah", "Philipp", "Simon", "Sebastian",
         "Yannick"),
        ("Becker", "Braun", "Hartmann", "Herrmann", "Kaiser", "Klein", "Kramer", "Krause", "Lange",
         "Lehmann", "Meyer", "Muller", "Neumann", "Schneider", "Schulz", "Schwarz", "Vogel",
         "Werner", "Winkler", "Zimmermann"),
    ),
    "Fransa": (
        ("Adrien", "Alexis", "Arthur", "Benjamin", "Dylan", "Enzo", "Gabriel", "Jules", "Kylian",
         "Lucas", "Maxime", "Nolan", "Noah", "Paul", "Quentin", "Rayan", "Romain", "Sacha",
         "Tom", "Victor"),
        ("Andre", "Bonnet", "Chevalier", "David", "Durand", "Fournier", "Garnier", "Gauthier",
         "Lefebvre", "Martin", "Mercier", "Michel", "Morel", "Muller", "Perrin", "Richard",
         "Robert", "Rousseau", "Simon", "Thomas"),
    ),
}

YOUTH_NAME_POOLS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    key: (
        tuple(dict.fromkeys((*firsts, *_YOUTH_EXTRA.get(key, ((), ()))[0]))),
        tuple(dict.fromkeys((*lasts, *_YOUTH_EXTRA.get(key, ((), ()))[1]))),
    )
    for key, (firsts, lasts) in NAME_POOLS.items()
}


def pool_key(country: str) -> str:
    """Lig ulkesi -> havuz anahtari. Havuzu olmayan ulke Ingiltere havuzunu kullanir."""
    key = COUNTRY_POOL.get(country, country)
    return key if key in NAME_POOLS else DEFAULT_POOL


def unique_name(
    rng: random.Random,
    first_names: Sequence[str],
    last_names: Sequence[str],
    used: set[str],
    attempts: int = 400,
) -> str:
    """Kullanilmamis 'Ad Soyad' uretir ve used kumesine ekler. Havuz tukenirse bas harf eklenir."""
    for _ in range(attempts):
        name = f"{rng.choice(first_names)} {rng.choice(last_names)}"
        if name not in used:
            used.add(name)
            return name
    for _ in range(attempts):
        name = f"{rng.choice(first_names)} {rng.choice('ABCDEFGHIJKLMNOPRSTUVYZ')}. {rng.choice(last_names)}"
        if name not in used:
            used.add(name)
            return name
    name = f"{rng.choice(first_names)} {rng.choice(last_names)} Jr."
    used.add(name)
    return name


def youth_name(rng: random.Random, country: str, used: set[str]) -> str:
    """Kulubun ulkesine uygun, tekrar etmeyen genc oyuncu adi (genisletilmis havuz)."""
    first_names, last_names = YOUTH_NAME_POOLS[pool_key(country)]
    return unique_name(rng, first_names, last_names, used)
