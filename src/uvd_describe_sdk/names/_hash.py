"""Hashes and encodings of names: keccak-256, EIP-137 namehash, DNS wire format.

Keccak comes from `pycryptodome`, not from `hashlib`: `hashlib.sha3_256` is the
FIPS-202 SHA-3, whose padding differs from the Keccak Ethereum uses, and it
produces a different digest for the same input. `pycryptodome` was chosen over
`eth-hash` because the describe-net Lambda already ships it (through
`eth-account`, measured 2026-09-24): its marginal weight there is zero.
"""

from __future__ import annotations

import re

from Crypto.Hash import keccak as _keccak

ZERO_ADDRESS = "0x" + "0" * 40


def keccak256(data: bytes) -> bytes:
    return _keccak.new(digest_bits=256, data=data).digest()


def selector(signature: str) -> bytes:
    """The 4-byte selector of a function or error signature."""
    return keccak256(signature.encode("ascii"))[:4]


def labelhash(label: str) -> bytes:
    return keccak256(label.encode("utf-8"))


def namehash(name: str) -> bytes:
    """EIP-137. The name must ALREADY be normalized: hashing is not normalizing.

    Execution Market's `namehash` lower-cased inside the hash
    (`client.py:147-158`, measured 2026-09-24). Lower-casing is not ENSIP-15:
    `ＶＩＴＡＬＩＫ.eth` and `vitalik.eth` normalize to the same name and
    lower-case to different ones. Normalization happens once, before, in
    `_normalize.py`; this function hashes what it is given.
    """
    node = b"\x00" * 32
    if name:
        for label in reversed(name.split(".")):
            node = keccak256(node + labelhash(label))
    return node


def dns_encode(name: str) -> bytes:
    """The DNS wire format ENSIP-10 `resolve(bytes name, bytes data)` takes.

    Raises `ValueError` for a label over 255 bytes: the wire format cannot carry
    it, and a truncated label would name something else.
    """
    out = b""
    for label in name.split("."):
        raw = label.encode("utf-8")
        if not raw or len(raw) > 255:
            raise ValueError("a DNS label must be 1 to 255 bytes")
        out += bytes([len(raw)]) + raw
    return out + b"\x00"


def to_checksum_address(address: str) -> str:
    """EIP-55. Takes `0x` + 40 hex digits in any case."""
    hexpart = address[2:].lower()
    digest = keccak256(hexpart.encode("ascii")).hex()
    return "0x" + "".join(
        ch.upper() if ch.isalpha() and int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(hexpart)
    )


def is_hex_address(value: str) -> bool:
    # A regex and not `int(value, 16)`: `int` accepts underscores and
    # surrounding whitespace, and `0x1_2_…` is not an address.
    return isinstance(value, str) and _HEX_ADDRESS.fullmatch(value) is not None


_HEX_ADDRESS = re.compile(r"0[xX][0-9a-fA-F]{40}")
