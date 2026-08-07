"""Tests for app.security.crypto."""

from __future__ import annotations

import pytest

from app.security.crypto import decrypt_secret, encrypt_secret


class TestRoundtrip:
    def test_encrypt_decrypt_roundtrip(self):
        token = encrypt_secret("sk-test-123")
        assert token != "sk-test-123"
        assert decrypt_secret(token) == "sk-test-123"

    def test_chinese_and_symbols(self):
        plaintext = "密钥-测试🔑 abc123!@#"
        assert decrypt_secret(encrypt_secret(plaintext)) == plaintext

    def test_same_plaintext_different_ciphertext(self):
        """Random nonce → same plaintext never produces the same token."""
        t1 = encrypt_secret("sk-same")
        t2 = encrypt_secret("sk-same")
        assert t1 != t2
        assert decrypt_secret(t1) == decrypt_secret(t2) == "sk-same"


class TestFailureModes:
    def test_empty_plaintext_raises(self):
        with pytest.raises(ValueError):
            encrypt_secret("")

    def test_decrypt_empty_returns_none(self):
        assert decrypt_secret("") is None

    def test_decrypt_garbage_returns_none(self):
        assert decrypt_secret("not-a-valid-token!!") is None

    def test_decrypt_tampered_returns_none(self):
        token = encrypt_secret("sk-original")
        # Flip one character in the base64 payload
        flipped = ("A" if token[0] != "A" else "B") + token[1:]
        assert decrypt_secret(flipped) is None
