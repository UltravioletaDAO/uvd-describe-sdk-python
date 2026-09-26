"""R3 (ronda 2 del PR 7, P1 [security]): una señal de la rainbow table de Avvy
fuera de `[0, 2**248)` ya no hace levantar a `reverse()`.

Cada señal empaqueta 31 bytes de una etiqueta, y `_decode_signals` hace
`to_bytes(31, "big")`: con una señal de 2**248 o más, `OverflowError: int too big
to convert` escapaba de `reverse_sync()` y de `await reverse()` (medido el
2026-09-26 sobre `940685ec`, py3.9.24 y 3.13.6). Es la clase de SDK-5: una
entrada que elige la cadena.

Una señal así no es un nombre: `claimed()` contesta `None`, igual que ante una
respuesta que no decodifica (`AbiError`).

🔴 El doble es SINTÉTICO (la `Cadena` de la ronda 2).
"""

from __future__ import annotations

from typing import List

import pytest

from uvd_describe_sdk.names import _avvy

from .names_replay import call_op
from .test_names_ronda2 import Cadena

A = "0x" + "11" * 20
INVERSO = "0x" + "55" * 20


def _avvy_contesta(senales: List[int]) -> Cadena:
    cadena = Cadena()
    cadena.ok(_avvy.REVERSE_RESOLVER_REGISTRY, "getResolver(uint256)", ["address"], [INVERSO])
    cadena.ok(INVERSO, "get(address)", ["uint256", "uint256"], [1, 2])
    cadena.ok(_avvy.RAINBOW_TABLE, "lookup(uint256)", ["uint256[]"], [senales])
    return cadena


@pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "senales", [[1 << 248, 0], [0, 1 << 248], [2**256 - 1, 2**256 - 1]], ids=["248", "par", "max"]
)
def test_una_senal_fuera_de_rango_no_es_un_nombre_y_no_levanta(
    senales: List[int], asincrono: bool
) -> None:
    """Mutación DW."""
    resolver = _avvy_contesta(senales).resolver(systems=("avvy",))
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "not_found"
    assert result.tried == ("avvy",) and result.normalized is None


def test_la_senal_mas_grande_que_cabe_se_sigue_leyendo() -> None:
    """El otro estado: 2**248 - 1 sí son 31 bytes. No es una etiqueta válida de
    Avvy (0xff…), así que llega a `_reverse_one` y es `reverse_mismatch` — pero
    se LEYÓ: el tope no rechaza lo que cabe."""
    resolver = _avvy_contesta([(1 << 248) - 1, 0]).resolver(systems=("avvy",))
    with resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "reverse_mismatch"
    assert result.detail == "the reverse record is not a valid name of its system"
