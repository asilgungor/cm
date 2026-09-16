"""
Hesap guvenligi (auth.py, 10. Asama) testleri. SAF: veritabani gerektirmez.

    CM_TEST_NO_DB=1 python -m pytest -q -p no:cacheprovider tests/test_auth.py
"""

from __future__ import annotations

import base64
import hashlib
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
from auth import (  # noqa: E402
    DUMMY_HASH,
    PASSWORD_MAX,
    PASSWORD_MIN,
    USERNAME_MAX,
    USERNAME_MIN,
    AuthError,
    hash_password,
    needs_rehash,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)

PASSWORD = "Gizli.Parola42"


def _manual_hash(password: str, n: int, r: int, p: int, salt: bytes = b"s" * 16, dklen: int = 64) -> str:
    """auth.py'den bagimsiz uretilen kayit: bicimin birlikte calisabilirligini de dogrular."""
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen,
                            maxmem=128 * 1024 * 1024)
    return "$".join(["scrypt", str(n), str(r), str(p),
                     base64.b64encode(salt).decode(), base64.b64encode(digest).decode()])


# ---------------------------------------------------------------------------
# Ozet bicimi ve dogrulama
# ---------------------------------------------------------------------------

def test_hash_format_is_self_describing():
    stored = hash_password(PASSWORD)
    parts = stored.split("$")
    assert len(parts) == 6 and parts[0] == "scrypt"
    assert (int(parts[1]), int(parts[2]), int(parts[3])) == (auth.SCRYPT_N, auth.SCRYPT_R, auth.SCRYPT_P)
    assert auth.SCRYPT_N >= 2 ** 14 and auth.SCRYPT_R >= 8
    assert len(base64.b64decode(parts[4], validate=True)) == 16
    assert len(base64.b64decode(parts[5], validate=True)) == 64
    assert len(stored) <= 255                     # users.password_hash String(255)


def test_same_password_gets_random_salts():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert first.split("$")[4] != second.split("$")[4]
    assert verify_password(PASSWORD, first) and verify_password(PASSWORD, second)


def test_verify_true_and_false():
    stored = hash_password(PASSWORD)
    assert verify_password(PASSWORD, stored) is True
    for wrong in ("Gizli.Parola43", "gizli.parola42", PASSWORD + " ", ""):
        assert verify_password(wrong, stored) is False
    for not_text in (None, 42, PASSWORD.encode()):
        assert verify_password(not_text, stored) is False


def test_verify_detects_tampered_digest_and_salt():
    stored = hash_password(PASSWORD)
    parts = stored.split("$")
    digest = bytearray(base64.b64decode(parts[5]))
    digest[0] ^= 1
    tampered = "$".join(parts[:5] + [base64.b64encode(bytes(digest)).decode()])
    assert verify_password(PASSWORD, tampered) is False
    other_salt = "$".join(parts[:4] + [base64.b64encode(b"x" * 16).decode(), parts[5]])
    assert verify_password(PASSWORD, other_salt) is False


def test_unicode_password_is_normalized():
    composed = "Şifre.Güçlü42"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    assert verify_password(decomposed, hash_password(composed))


def test_manual_scrypt_record_interoperates():
    stored = _manual_hash(PASSWORD, 2 ** 14, 8, 5)
    assert verify_password(PASSWORD, stored) and not verify_password("baska1234", stored)


@pytest.mark.parametrize("stored", [
    None,
    "",
    123,
    b"scrypt$16384$8$5$AAAA$AAAA",
    "duzmetinparola",
    "scrypt",
    "scrypt$",
    "scrypt$16384$8$5$c2FsdHNhbHQ=",                                  # eksik parca
    "bcrypt$16384$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 88,          # bilinmeyen sema
    "scrypt$16384$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 88 + "$x",   # fazla parca
    "scrypt$16383$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",   # n 2'nin kuvveti degil
    "scrypt$016384$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",  # basta sifir
    "scrypt$-16384$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",
    "scrypt$１６３８４$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",  # tam genislik rakam
    "scrypt$1073741824$8$1$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",  # DoS: 2^30
    "scrypt$16384$0$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",
    "scrypt$16384$8$0$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",
    "scrypt$16384$8$5$!!!!$" + "A" * 86 + "==",                        # bozuk base64
    "scrypt$16384$8$5$c2FsdA==$" + "A" * 86 + "==",                    # kisa tuz
    "scrypt$16384$8$5$c2FsdHNhbHRzYWx0c2FsdA==$QUFBQQ==",             # kisa ozet
    "scrypt$16384$8$5$şşşş$" + "A" * 86 + "==",                        # ASCII disi
    "scrypt$abc$8$5$c2FsdHNhbHRzYWx0c2FsdA==$" + "A" * 86 + "==",
])
def test_malformed_hashes_never_raise(stored):
    assert verify_password(PASSWORD, stored) is False
    assert needs_rehash(stored) is True


def test_overlong_password_is_rejected_without_error():
    stored = hash_password(PASSWORD)
    assert verify_password("a1" * 5000, stored) is False


def test_needs_rehash_only_for_weaker_parameters():
    assert needs_rehash(hash_password(PASSWORD)) is False
    weak_n = _manual_hash(PASSWORD, 2 ** 12, 8, 5)
    weak_p = _manual_hash(PASSWORD, 2 ** 14, 8, 1)          # eski N=2^14,r=8,p=1 kayitlari
    short_salt = _manual_hash(PASSWORD, 2 ** 14, 8, 5, salt=b"k" * 8)
    short_digest = _manual_hash(PASSWORD, 2 ** 14, 8, 5, dklen=32)
    for stored in (weak_n, weak_p, short_salt, short_digest):
        assert verify_password(PASSWORD, stored), "eski kayit yine de dogrulanabilmeli"
        assert needs_rehash(stored) is True
    stronger = _manual_hash(PASSWORD, 2 ** 15, 8, 5)
    assert verify_password(PASSWORD, stronger) and needs_rehash(stronger) is False


def test_no_plaintext_in_hash():
    secret = "CokGizliParola2026"
    stored = hash_password(secret)
    assert secret not in stored and secret.lower() not in stored.lower()
    assert base64.b64encode(secret.encode()).decode().rstrip("=") not in stored
    assert secret.encode().hex() not in stored


def test_dummy_hash_is_current_and_unguessable():
    assert DUMMY_HASH.startswith("scrypt$") and needs_rehash(DUMMY_HASH) is False
    for guess in ("", PASSWORD):
        assert verify_password(guess, DUMMY_HASH) is False


# ---------------------------------------------------------------------------
# Kullanici adi
# ---------------------------------------------------------------------------

def test_auth_error_is_value_error():
    assert issubclass(AuthError, ValueError)
    assert (USERNAME_MIN, USERNAME_MAX, PASSWORD_MIN, PASSWORD_MAX) == (3, 32, 8, 128)


def test_normalize_username():
    assert normalize_username("  Menajer_1  ") == "Menajer_1"
    assert normalize_username("\tabc\n") == "abc"
    assert normalize_username(None) == "" and normalize_username(42) == ""


@pytest.mark.parametrize("raw, expected", [
    ("Menajer", "Menajer"),
    ("  asil.tek-2026  ", "asil.tek-2026"),
    ("123abc", "123abc"),
    ("abc", "abc"),
    ("a" * 32, "a" * 32),
    ("a_b.c-d", "a_b.c-d"),
])
def test_valid_usernames(raw, expected):
    assert validate_username(raw) == expected


@pytest.mark.parametrize("raw, message", [
    ("", "Kullanıcı adı boş olamaz."),
    ("   ", "Kullanıcı adı boş olamaz."),
    (None, "Kullanıcı adı boş olamaz."),
    ("ab", "Kullanıcı adı 3-32 karakter olmalı."),
    ("a" * 33, "Kullanıcı adı 3-32 karakter olmalı."),
    ("ali veli", "yalnızca İngilizce harf, rakam"),
    ("şükrü", "yalnızca İngilizce harf, rakam"),
    ("menajer@site", "yalnızca İngilizce harf, rakam"),
    ("x';DROP TABLE users--", "yalnızca İngilizce harf, rakam"),
    ("_menajer", "Kullanıcı adı harf ya da rakamla başlamalı."),
    (".menajer", "Kullanıcı adı harf ya da rakamla başlamalı."),
    ("-menajer", "Kullanıcı adı harf ya da rakamla başlamalı."),
])
def test_invalid_usernames(raw, message):
    with pytest.raises(AuthError) as err:
        validate_username(raw)
    assert message in str(err.value)


# ---------------------------------------------------------------------------
# Parola
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("password, username", [
    ("abcdefg1", None),                       # tam alt sinir
    ("a1" * 64, None),                        # tam ust sinir (128)
    ("Şampiyon2026", "menajer"),              # Turkce harf de harftir
    (PASSWORD, "Menajer"),
    ("Kaleci.99x", ""),
])
def test_valid_passwords(password, username):
    assert validate_password(password, username) is None


@pytest.mark.parametrize("password, username, message", [
    ("", None, "Parola boş olamaz."),
    (None, None, "Parola boş olamaz."),
    ("Kisa1", None, "Parola en az 8 karakter olmalı."),
    ("a1" * 64 + "x", None, "Parola en fazla 128 karakter olabilir."),
    ("sadeceharfler", None, "Parola en az bir harf ve bir rakam içermeli."),
    ("1234567890", None, "Parola en az bir harf ve bir rakam içermeli."),
    ("........", None, "Parola en az bir harf ve bir rakam içermeli."),
    ("menajer123", "Menajer", "Parola kullanıcı adını içeremez."),
    ("xxMENAJERxx9", "  menajer ", "Parola kullanıcı adını içeremez."),
    ("Password123", None, "Bu parola çok yaygın; daha güçlü bir parola seçin."),
])
def test_invalid_passwords(password, username, message):
    with pytest.raises(AuthError) as err:
        validate_password(password, username)
    assert str(err.value) == message
