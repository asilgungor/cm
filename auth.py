"""
auth.py
=======
Hesap guvenligi (10. Asama). SAF MANTIK: veritabani ve arayuz bilmez, yalnizca standart kutuphane.

Kullanici adi:
    3-32 karakter; ASCII harf, rakam, '_', '.', '-'; harf ya da rakamla baslar.
    Buyuk/kucuk harf gorunum icin korunur, benzersizlik harf buyuklugunden bagimsizdir
    (veritabaninda lower(username) uzerinde benzersiz indeks).

Parola:
    8-128 karakter, en az bir harf ve bir rakam; kullanici adini iceremez; cok yaygin
    parolalar reddedilir. Duz metin ASLA saklanmaz.

Parola ozeti (hashlib.scrypt, bellek-yogun KDF):
    Saklama bicimi kendini tarif eder:  scrypt$<n>$<r>$<p>$<tuz_b64>$<ozet_b64>
    Guncel parametreler N=2^14, r=8, p=5 (16 MiB): OWASP'in scrypt icin verdigi esdeger
    minimumlardan biri (N=2^17,r=8,p=1 ile ayni is yuku, daha az bellek). 16 bayt rastgele
    tuz, 64 bayt cikti. Parola NFKC ile normallestirilir: ayni parola farkli klavye/isletim
    sisteminde (birlesik ya da ayrik aksanli harf) ayni ozeti verir.
    Parametreler ileride artirilirsa needs_rehash eski kayitlari yakalar; basarili giriste
    parola yeni parametrelerle yeniden ozetlenir (accounts.authenticate).

Zamanlama:
    verify_password sabit zamanli karsilastirir (hmac.compare_digest) ve bozuk/bilinmeyen
    kayitta istisna firlatmaz, False doner. DUMMY_HASH ile olmayan kullanici icin de ayni
    maliyette dogrulama yapilir; yanit suresi kullanici adinin var olup olmadigini sizdirmaz.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import unicodedata


class AuthError(ValueError):
    """Kullaniciya gosterilebilir dogrulama hatasi (mesaj Turkce)."""


USERNAME_MIN, USERNAME_MAX = 3, 32
PASSWORD_MIN, PASSWORD_MAX = 8, 128

# --- scrypt parametreleri (guncel) ---------------------------------------------
HASH_SCHEME = "scrypt"
SCRYPT_N = 2 ** 14              # CPU/bellek maliyeti (2'nin kuvveti)
SCRYPT_R = 8                    # blok boyutu
SCRYPT_P = 5                    # paralellik (OpenSSL'de sirali: bellek artmaz, sure artar)
SALT_BYTES = 16
DKLEN = 64
SCRYPT_MAXMEM = 64 * 1024 * 1024

# Saklanan kayittan okunan parametrelerin ust siniri: bozuk/kotu niyetli bir kayit
# sunucuyu dakikalarca mesgul etmesin ya da gigabaytlarca bellek istemesin.
_MAX_LOG2_N = 20
_MAX_R = 64
_MAX_P = 64
_MAX_WORK = 2 ** 24             # n * r * p (guncel ayarin ~25 kati)
_MAX_MEMORY = 256 * 1024 * 1024
_MIN_SALT_BYTES = 8
_MIN_DKLEN, _MAX_DKLEN = 32, 128

# Dogrulamada kabul edilen en uzun parola (PASSWORD_MAX'tan genis: kural sonradan
# siklassa bile eski parolasi olan giris yapabilsin; asiri uzun girdi hic ozetlenmez).
_VERIFY_MAX_CHARS = 1024

_USERNAME_CHARS = re.compile(r"[A-Za-z0-9_.\-]+")
_DIGITS = re.compile(r"[0-9]+")

# Kurallari gecse bile tahmin listelerinin basinda olan parolalar (kucuk harfle)
COMMON_PASSWORDS: frozenset[str] = frozenset({
    "password1", "password12", "password123", "passw0rd", "parola123", "parola1234",
    "sifre123", "sifre1234", "qwerty123", "qwerty1234", "abc12345", "abcd1234",
    "12345678a", "123456789a", "a12345678", "1q2w3e4r", "1q2w3e4r5t", "q1w2e3r4",
    "asdf1234", "admin123", "admin1234", "iloveyou1", "welcome1", "letmein1",
    "football1", "futbol123", "trustno1", "zxcvbnm1", "aa123456", "asd123456",
})


# ---------------------------------------------------------------------------
# Kullanici adi / parola kurallari
# ---------------------------------------------------------------------------

def normalize_username(raw) -> str:
    """Bas/son bosluklari atar. Metin degilse bos dize (dogrulama anlamli hata verir)."""
    return raw.strip() if isinstance(raw, str) else ""


def validate_username(raw) -> str:
    """Kurallara uyan kullanici adini (bosluklari atilmis haliyle) dondurur; aksi AuthError."""
    name = normalize_username(raw)
    if not name:
        raise AuthError("Kullanıcı adı boş olamaz.")
    if not (USERNAME_MIN <= len(name) <= USERNAME_MAX):
        raise AuthError(f"Kullanıcı adı {USERNAME_MIN}-{USERNAME_MAX} karakter olmalı.")
    if not _USERNAME_CHARS.fullmatch(name):
        raise AuthError(
            "Kullanıcı adı yalnızca İngilizce harf, rakam, alt çizgi (_), nokta (.) "
            "ve tire (-) içerebilir."
        )
    if not name[0].isascii() or not name[0].isalnum():
        raise AuthError("Kullanıcı adı harf ya da rakamla başlamalı.")
    return name


def validate_password(password, username=None) -> None:
    """Parola kurallari; ihlalde AuthError (ilk bulunan sorun)."""
    if not isinstance(password, str) or not password:
        raise AuthError("Parola boş olamaz.")
    if len(password) < PASSWORD_MIN:
        raise AuthError(f"Parola en az {PASSWORD_MIN} karakter olmalı.")
    if len(password) > PASSWORD_MAX:
        raise AuthError(f"Parola en fazla {PASSWORD_MAX} karakter olabilir.")
    if not any(ch.isalpha() for ch in password) or not _DIGITS.search(password):
        raise AuthError("Parola en az bir harf ve bir rakam içermeli.")
    folded = password.casefold()
    name = normalize_username(username).casefold()
    if name and name in folded:
        raise AuthError("Parola kullanıcı adını içeremez.")
    if folded in COMMON_PASSWORDS:
        raise AuthError("Bu parola çok yaygın; daha güçlü bir parola seçin.")


# ---------------------------------------------------------------------------
# Ozetleme
# ---------------------------------------------------------------------------

def _password_bytes(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    maxmem = max(SCRYPT_MAXMEM, 2 * 128 * r * (n + p + 2))
    return hashlib.scrypt(_password_bytes(password), salt=salt, n=n, r=r, p=p,
                          dklen=dklen, maxmem=maxmem)


def hash_password(password: str) -> str:
    """Guncel parametrelerle yeni tuzlu ozet: scrypt$n$r$p$tuz$ozet."""
    if not isinstance(password, str):
        raise AuthError("Parola metin olmalı.")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, DKLEN)
    return f"{HASH_SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def _decimal(part: str) -> int:
    if not _DIGITS.fullmatch(part) or (len(part) > 1 and part[0] == "0"):
        raise ValueError("sayi degil")
    return int(part)


def _parse(stored) -> tuple[int, int, int, bytes, bytes] | None:
    """Saklanan ozeti cozer; bicim bozuk ya da parametreler sinir disindaysa None."""
    if not isinstance(stored, str):
        return None
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != HASH_SCHEME:
        return None
    try:
        n, r, p = (_decimal(x) for x in parts[1:4])
        salt = base64.b64decode(parts[4].encode("ascii"), validate=True)
        digest = base64.b64decode(parts[5].encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error):
        return None
    if n < 2 or n & (n - 1) or n.bit_length() - 1 > _MAX_LOG2_N:
        return None
    if not (1 <= r <= _MAX_R and 1 <= p <= _MAX_P) or n * r * p > _MAX_WORK:
        return None
    if 128 * r * n > _MAX_MEMORY:
        return None
    if len(salt) < _MIN_SALT_BYTES or not (_MIN_DKLEN <= len(digest) <= _MAX_DKLEN):
        return None
    return n, r, p, salt, digest


def verify_password(password, stored) -> bool:
    """Parola ozetle eslesiyor mu? Sabit zamanli; hicbir girdide istisna firlatmaz."""
    parsed = _parse(stored)
    if parsed is None or not isinstance(password, str) or len(password) > _VERIFY_MAX_CHARS:
        return False
    n, r, p, salt, expected = parsed
    try:
        actual = _scrypt(password, salt, n, r, p, len(expected))
    except (ValueError, MemoryError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored) -> bool:
    """Kayit guncel parametrelerden zayif (ya da okunamaz) ise True."""
    parsed = _parse(stored)
    if parsed is None:
        return True
    n, r, p, salt, digest = parsed
    return (n < SCRYPT_N or r < SCRYPT_R or p < SCRYPT_P
            or len(salt) < SALT_BYTES or len(digest) < DKLEN)


# Olmayan kullanici icin zaman esitleyici: rastgele, kimsenin bilmedigi bir parolanin ozeti
DUMMY_HASH: str = hash_password(secrets.token_urlsafe(32))
