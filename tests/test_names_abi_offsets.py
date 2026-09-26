"""R1 (ronda 2 del PR 7, P1 [security]): un `string[]` con offsets solapados ya
no es una bomba de memoria.

El dueño de un nombre elige el resolver, y un resolver ENSIP-10 elige el revert
`OffchainLookup`: su `urls: string[]` puede tener N offsets que apuntan todos al
MISMO string de L bytes, y el decodificador materializaba N copias. Medido el
2026-09-26 sobre `940685ec` (py3.9.24 y 3.13.6): 1.024 offsets a un string de
128 KB, un revert de ~160 KB → pico de tracemalloc de **129 MiB**. El
refutador midió 0,95 MB de revert → ≈ 7 GB [cálculo] y 32 s: en un proceso con
poca RAM, un `MemoryError` que escapa o el OOM killer.

Un encoder nunca solapa elementos, así que lo que decodifican no puede sumar
más que los datos que los contienen: pasado eso es `AbiError`, y sale por el
`except _abi.AbiError` que ya existía — «malformed OffchainLookup».

⚠️ Con ese tope el pico bajó a 25 MiB, no a menos de 16: el resto lo ponía el
chequeo de hex del motor (`0x([0-9a-f]{2})*`, ~150 veces la entrada). Encontrado
al medir este mismo test; es la misma clase y se arregló acá. Pico final: 1,5 MiB.

🔴 El doble es SINTÉTICO (la `Cadena` de la ronda 2).
"""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from typing import Any, Callable

import pytest

from uvd_describe_sdk.names import NameResolver, _abi
from uvd_describe_sdk.names._proto import OFFCHAIN_LOOKUP, _is_hex

from .test_names_ronda2 import Cadena, R, _ens_extendido

N_URLS = 1024
LARGO = 128 * 1024


def _w(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _lookup_solapado(n: int, largo: int) -> bytes:
    """`OffchainLookup(R, urls, callData, callback, extraData)` armado a mano: el
    `string[]` tiene `n` offsets, todos al MISMO string de `largo` bytes."""
    texto = b"https://gw.example/" + b"u" * (largo - 19)
    relleno = b"\x00" * ((32 - largo % 32) % 32)
    urls = _w(n) + _w(32 * n) * n + _w(largo) + texto + relleno
    call_data = _w(1) + b"\x01" + b"\x00" * 31
    off_urls = 5 * 32
    off_call = off_urls + len(urls)
    off_extra = off_call + len(call_data)
    cabeza = (
        b"\x00" * 12 + bytes.fromhex(R[2:])
        + _w(off_urls)
        + _w(off_call)
        + b"\xaa\xbb\xcc\xdd" + b"\x00" * 28
        + _w(off_extra)
    )
    return OFFCHAIN_LOOKUP + cabeza + urls + call_data + _w(0)


def _resolver() -> NameResolver:
    cadena = Cadena()
    _ens_extendido(cadena)
    cadena.revierte(R, "resolve(bytes,bytes)", _lookup_solapado(N_URLS, LARGO))
    return cadena.resolver(cache=False)


def _correr(asincrono: bool) -> Callable[[], Any]:
    resolver = _resolver()
    if not asincrono:
        return lambda: resolver.resolve_sync("alguien.eth")

    async def una() -> Any:
        async with resolver as r:
            return await r.resolve("alguien.eth")

    return lambda: asyncio.run(una())


@pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])
def test_offsets_solapados_contestan_rapido_y_sin_bomba_de_memoria(asincrono: bool) -> None:
    """Mutación DR: sin el tope, pico de 129 MiB."""
    correr = _correr(asincrono)
    inicio = time.perf_counter()
    result = correr()
    duro = time.perf_counter() - inicio
    assert result.error == "not_found"
    assert "malformed OffchainLookup" in (result.detail or "")
    assert duro < 0.5, f"tardó {duro:.2f} s"

    correr = _correr(asincrono)
    tracemalloc.start()
    try:
        correr()
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert pico < 16 * 2**20, f"pico de {pico / 2**20:.1f} MiB"


def test_el_chequeo_de_hex_no_cuesta_memoria() -> None:
    """La otra mitad del pico: `0x([0-9a-f]{2})*` guardaba estado por repetición
    y costaba ~150 veces la entrada (24 MiB para el revert de arriba, 145-184 MiB
    para 1 MB). El hex lo elige la cadena o el gateway. Mutación DS."""
    enorme = "0x" + "ab" * 1_000_000
    tracemalloc.start()
    try:
        assert _is_hex(enorme)
        _, pico = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert pico < 2**20, f"pico de {pico / 2**20:.1f} MiB"


@pytest.mark.parametrize(
    ("valor", "es_hex"),
    [("0x", True), ("0xab", True), ("0xAbCd", True), ("0xabc", False), ("0xzz", False),
     ("ab", False), ("0x0g", False), ("", False), ("0X12", False)],
)
def test_el_chequeo_de_hex_no_cambia_de_significado(valor: str, es_hex: bool) -> None:
    assert _is_hex(valor) is es_hex


def test_un_string_array_legitimo_sigue_decodificando() -> None:
    """El otro estado: offsets disjuntos, vacíos y repetidos por valor (no por
    offset) decodifican como siempre."""
    valores = ["https://uno.example/{data}", "", "https://dos.example/{sender}", "é" * 40]
    datos = _abi.encode(["address", "string[]", "bytes"], [R, valores, b"\x01\x02"])
    assert _abi.decode(["address", "string[]", "bytes"], datos) == (R.lower(), valores, b"\x01\x02")
    assert _abi.decode(["bytes[]"], _abi.encode(["bytes[]"], [[b"a" * 64, b"a" * 64]])) == (
        [b"a" * 64, b"a" * 64],
    )
