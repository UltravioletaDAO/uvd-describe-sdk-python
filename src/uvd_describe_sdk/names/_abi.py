"""The smallest ABI codec that the name resolvers need, and nothing more.

Why not `eth-abi`: measured 2026-09-24 on the describe-net Lambda (Linux
x86_64 wheels, py3.12), `web3>=7,<8` adds 14 packages and 18 MB unpacked on top
of what that Lambda already ships. The resolvers here encode and decode a dozen
fixed signatures; a general codec is not what they need.

Supported types: `address`, `bool`, `uint256`, `bytes32`, `bytes4`, `bytes`,
`string`, and one level of `T[]` / `T[3]` over them. Decoding checks every
offset and length against the buffer and raises `AbiError` instead of reading
past it: the bytes come from an RPC and, through CCIP-Read, from a gateway.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Tuple


class AbiError(ValueError):
    """The bytes are not a valid ABI encoding of the expected types."""


def _word(value: int) -> bytes:
    return value.to_bytes(32, "big")


def _is_dynamic(typ: str) -> bool:
    if typ in ("bytes", "string"):
        return True
    if typ.endswith("[]"):
        return True
    if typ.endswith("]"):
        return _is_dynamic(typ[: typ.rindex("[")])
    return False


def _encode_static(typ: str, value: Any) -> bytes:
    if typ == "address":
        raw = bytes.fromhex(str(value)[2:]) if isinstance(value, str) else bytes(value)
        if len(raw) != 20:
            raise AbiError(f"address must be 20 bytes, got {len(raw)}")
        return b"\x00" * 12 + raw
    if typ == "bool":
        return _word(1 if value else 0)
    if typ == "uint256":
        if not isinstance(value, int) or value < 0 or value >= 1 << 256:
            raise AbiError("uint256 out of range")
        return _word(value)
    if typ in ("bytes32", "bytes4"):
        size = 32 if typ == "bytes32" else 4
        raw = bytes(value)
        if len(raw) != size:
            raise AbiError(f"{typ} must be {size} bytes")
        return raw.ljust(32, b"\x00")
    raise AbiError(f"unsupported static type {typ}")


def _encode_one(typ: str, value: Any) -> bytes:
    """Encode ONE value as it goes in the tail (dynamic) or the head (static)."""
    if typ in ("bytes", "string"):
        raw = value.encode("utf-8") if typ == "string" else bytes(value)
        pad = (32 - len(raw) % 32) % 32
        return _word(len(raw)) + raw + b"\x00" * pad
    if typ.endswith("]"):
        inner = typ[: typ.rindex("[")]
        items = list(value)
        body = encode([inner] * len(items), items)
        return (_word(len(items)) + body) if typ.endswith("[]") else body
    return _encode_static(typ, value)


def encode(types: Sequence[str], values: Sequence[Any]) -> bytes:
    """`abi.encode(values...)` for the supported types."""
    if len(types) != len(values):
        raise AbiError("types and values differ in length")
    head_size = 0
    for typ in types:
        if _is_dynamic(typ):
            head_size += 32
        elif typ.endswith("]"):
            head_size += 32 * int(typ[typ.rindex("[") + 1 : -1])
        else:
            head_size += 32
    head = b""
    tail = b""
    for typ, value in zip(types, values):
        if _is_dynamic(typ):
            head += _word(head_size + len(tail))
            tail += _encode_one(typ, value)
        else:
            head += _encode_one(typ, value)
    return head + tail


def _read_word(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 32 > len(data):
        raise AbiError("read past the end of the data")
    return int.from_bytes(data[offset : offset + 32], "big")


def _decode_one(typ: str, data: bytes, offset: int) -> Any:
    """Decode the value whose HEAD slot is at `offset` inside `data`."""
    if _is_dynamic(typ):
        start = _read_word(data, offset)
        return _decode_at(typ, data, start)
    return _decode_at(typ, data, offset)


def _decode_at(typ: str, data: bytes, start: int) -> Any:
    if typ in ("bytes", "string"):
        length = _read_word(data, start)
        begin = start + 32
        if length > len(data) - begin:
            raise AbiError("dynamic length runs past the end of the data")
        raw = data[begin : begin + length]
        if typ == "bytes":
            return raw
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AbiError("string is not UTF-8") from exc
    if typ.endswith("[]"):
        inner = typ[:-2]
        count = _read_word(data, start)
        if count > (len(data) - start) // 32:
            raise AbiError("array length runs past the end of the data")
        return list(decode([inner] * count, data[start + 32 :]))
    if typ.endswith("]"):
        inner = typ[: typ.rindex("[")]
        if _is_dynamic(inner):
            raise AbiError(f"fixed arrays of dynamic types are not supported: {typ}")
        count = int(typ[typ.rindex("[") + 1 : -1])
        return [_decode_at(inner, data, start + 32 * i) for i in range(count)]
    word = _read_word(data, start)
    if typ == "address":
        if word >> 160:
            raise AbiError("address word has dirty high bits")
        return "0x" + data[start + 12 : start + 32].hex()
    if typ == "bool":
        if word > 1:
            raise AbiError("bool word is neither 0 nor 1")
        return word == 1
    if typ == "uint256":
        return word
    if typ == "bytes32":
        return data[start : start + 32]
    if typ == "bytes4":
        return data[start : start + 4]
    raise AbiError(f"unsupported type {typ}")


def decode(types: Sequence[str], data: bytes) -> Tuple[Any, ...]:
    """`abi.decode(data, (types...))` for the supported types."""
    out: List[Any] = []
    offset = 0
    for typ in types:
        out.append(_decode_one(typ, data, offset))
        if not _is_dynamic(typ) and typ.endswith("]"):
            offset += 32 * int(typ[typ.rindex("[") + 1 : -1])
        else:
            offset += 32
    return tuple(out)
