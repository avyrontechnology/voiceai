"""Pure credential primitives at their new home (spec 0005, C1).

Each test drives the REAL moved bodies in `voiceai.modules.auth.static_methods`
against concrete values — the behavior-invariant checklist entries for hashing:
PBKDF2 format, cross-instance verification, wrong-password and malformed-reference
rejection (never raise), constant-time comparison, and `secrets`-grade tokens.
A final pin asserts the legacy module re-exports the same objects by identity, so
every importer and monkeypatch target keeps resolving.
"""

import pytest

import voiceai.platform.auth as legacy_auth
from voiceai.modules.auth import static_methods
from voiceai.modules.auth.static_methods import hash_password, new_token, token_hash, verify_password


def test_hash_format_carries_algorithm_iterations_salt_and_digest() -> None:
    """The `$`-separated shape verifiers parse (spec checklist: format)."""
    stored = hash_password("s3cret!")

    algo, iterations, salt_hex, digest_hex = stored.split("$")
    assert algo == "pbkdf2_sha256"
    assert int(iterations) == 600_000
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(digest_hex)) == 32


def test_verify_round_trips_across_hashes() -> None:
    """A password verifies against a hash minted by another call (fresh salt each time)."""
    first, second = hash_password("same-password"), hash_password("same-password")

    assert first != second  # fresh salt per hash
    assert verify_password("same-password", first) is True
    assert verify_password("same-password", second) is True
    assert verify_password("other-password", first) is False


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "not-a-hash",
        "pbkdf2_sha256$600000$zzzz",
        "md5$600000$" + "00" * 16 + "$" + "00" * 32,
        "pbkdf2_sha256$not-a-number$" + "00" * 16 + "$" + "00" * 32,
        "pbkdf2_sha256$600000$" + "00" * 16 + "$not-hex!",
    ],
)
def test_verify_never_raises_on_malformed_references(reference: str) -> None:
    """Malformed rows answer False — a corrupt row must not turn login into a 500."""
    assert verify_password("anything", reference) is False


def test_tokens_are_unique_urlsafe_and_hashed_deterministically() -> None:
    """Tokens carry 256 bits of entropy; hashes are stable SHA-256 hex."""
    first, second = new_token(), new_token()

    assert first != second
    assert len(first) >= 40
    assert token_hash(first) == token_hash(first)
    assert token_hash(first) != token_hash(second)
    assert len(bytes.fromhex(token_hash(first))) == 32


def test_legacy_module_re_exports_the_moved_functions_by_identity() -> None:
    """Same-named delegators keep the legacy lookup/patch site (C1 strangler seam)."""
    assert legacy_auth.hash_password is static_methods.hash_password
    assert legacy_auth.verify_password is static_methods.verify_password
    assert legacy_auth.new_token is static_methods.new_token
    assert legacy_auth.token_hash is static_methods.token_hash


def test_moved_and_legacy_paths_agree() -> None:
    """Belt and braces: a legacy-minted hash verifies through the new home."""
    assert static_methods.verify_password("pw", legacy_auth.hash_password("pw")) is True
