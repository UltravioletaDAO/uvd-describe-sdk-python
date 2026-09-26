"""SDK-6: un reverse que no le pudo preguntar a NINGÚN sistema es `rpc_unavailable`.

Fila P1 que la revisión de las rutas de nombres de describe.net le encontró al
resolver ANTES del tag v0.7.0, contra `5ed00228`: con todos los sistemas
salteados por falta de RPC, `reverse()` contestaba `not_found` con
`verified_onchain=False`, y la caché lo guardaba 60 s — el único `not_found`
que quería decir «no sé». Ahora `not_found` sólo sale si al menos un sistema
contestó; si no se pudo preguntar a ninguno, `rpc_unavailable`, que nunca se
cachea.

🔴 El doble es SINTÉTICO (la `Cadena` de la ronda 2).
"""

from __future__ import annotations

import httpx
import pytest

from uvd_describe_sdk.names import NameResolver, _ens

from .names_replay import FAKE_RPC, call_op, failing_transport
from .test_names_ronda2 import Cadena

A = "0x" + "11" * 20  # la dirección por la que se pregunta (EIP-55 no la cambia)
CERO = "0x" + "0" * 40

asincronia = pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])


@asincronia
def test_un_reverse_sin_ningun_RPC_es_rpc_unavailable_y_no_not_found(asincrono: bool) -> None:
    """Los cuatro sistemas salteados por falta de RPC. Antes: `not_found`,
    cacheado 60 s. Mutación DH."""
    resolver = NameResolver(rpc={}, transport=failing_transport())
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert result.tried == () and result.verified_onchain is False
    assert result.normalized is None and result.address == A
    assert "no system could be asked" in (result.detail or "")
    assert "skipped: ens (no RPC for eip155:1)" in (result.detail or "")
    assert resolver.cache is not None and len(resolver.cache) == 0


@asincronia
def test_un_reverse_con_un_sistema_que_contesta_no_hay_nombre_es_not_found(
    asincrono: bool,
) -> None:
    """El otro estado: ENS contestó (ningún resolver para el nombre inverso) y
    Basenames se salteó. Eso SÍ es una respuesta, y se cachea."""
    cadena = Cadena()
    cadena.ok(_ens.REGISTRY, "resolver(bytes32)", ["address"], [CERO])
    transporte = httpx.MockTransport(cadena)
    resolver = NameResolver(
        rpc={"eip155:1": FAKE_RPC["eip155:1"]},
        systems=("ens", "basenames"),
        transport=transporte,
        async_transport=transporte,
    )
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "not_found"
    assert result.tried == ("ens",) and result.verified_onchain is True
    assert result.normalized is None
    assert "no primary name" in (result.detail or "")
    assert "skipped: basenames (no RPC for eip155:8453)" in (result.detail or "")
    assert resolver.cache is not None and len(resolver.cache) == 1


def test_un_reverse_sin_ningun_sistema_de_reverse_habilitado_es_unsupported_system() -> None:
    """Configuración y no caída: lo que `resolve()` contesta para un sistema
    deshabilitado, decidido sin red. Tampoco es `not_found`. Mutación DI."""
    resolver = NameResolver(rpc=FAKE_RPC, systems=("ens-dns",), transport=failing_transport())
    with resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "unsupported_system"
    assert result.tried == () and result.verified_onchain is False
    assert resolver.cache is not None and len(resolver.cache) == 0
