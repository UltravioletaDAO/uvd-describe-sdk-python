"""SDK-6 y la ronda 1 del PR 7: lo que un `reverse()` puede afirmar cuando no le
preguntó a todos.

SDK-6 — fila P1 que la revisión de las rutas de nombres de describe.net le
encontró al resolver ANTES del tag v0.7.0, contra `5ed00228`: con todos los
sistemas salteados por falta de RPC, `reverse()` contestaba `not_found` con
`verified_onchain=False`, y la caché lo guardaba 60 s — el único `not_found` que
quería decir «no sé». Ahora `not_found` sólo sale si al menos un sistema
contestó; si no se pudo preguntar a ninguno, `rpc_unavailable`, que nunca se
cachea.

Ronda 1 (del refutador de describe-net, medido acá con dobles el 2026-09-26):

A. Con ALGÚN sistema salteado y el resto «no hay nombre», el `not_found` salía
   con `verified_onchain=True` (sin RPC de Base: `tried` ens, unstoppable, avvy),
   y un consumidor lo cacheaba como verdad. Sigue siendo `not_found`, pero
   `verified_onchain=False`: un negativo es verificado sólo si se le preguntó a
   todos. Lo mismo para `reverse_mismatch` / `expired` (misma enfermedad).
B. Sin RPC de Ethereum (ENS, Basenames y UNS salteados), el nombre de Avvy salía
   como PRIMARIO — contra el orden estricto del propio SDK. Un sistema salteado
   sigue ocupando su lugar: el de abajo no contesta, y sale `rpc_unavailable`.

🔴 Los dobles son SINTÉTICOS (la `Cadena` de la ronda 2): contestan por
(contrato, selector) y no describen ningún hecho del mundo.
"""

from __future__ import annotations

from typing import Tuple

import httpx
import pytest

from uvd_describe_sdk.names import NameResolver, _avvy, _ens, _uns

from .names_replay import FAKE_RPC, call_op, failing_transport
from .test_names_ronda2 import Cadena, _avvy_con_evm, _uns_con_eth, _uns_reverse

A = "0x" + "11" * 20  # la dirección por la que se pregunta (EIP-55 no la cambia)
B = "0x" + "22" * 20  # otra dirección
CERO = "0x" + "0" * 40
ETH, BASE, POLYGON, AVAX = "eip155:1", "eip155:8453", "eip155:137", "eip155:43114"

asincronia = pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])


def _resolver(cadena: Cadena, cadenas: Tuple[str, ...], **kwargs: object) -> NameResolver:
    transporte = httpx.MockTransport(cadena)
    return NameResolver(
        rpc={c: FAKE_RPC[c] for c in cadenas},
        transport=transporte,
        async_transport=transporte,
        **kwargs,  # type: ignore[arg-type]
    )


def _nadie_tiene_nombre(cadena: Cadena) -> None:
    """Los cuatro sistemas contestan «esta dirección no reclama ningún nombre»."""
    cadena.ok(_ens.REGISTRY, "resolver(bytes32)", ["address"], [CERO])
    cadena.ok(_ens.BASE_REVERSE_REGISTRAR, "nameForAddr(address)", ["string"], [""])
    for chain in (ETH, POLYGON, BASE):
        cadena.ok(_uns.PROXY_READERS[chain], "reverseNameOf(address)", ["string"], [""])
    cadena.ok(_avvy.REVERSE_RESOLVER_REGISTRY, "getResolver(uint256)", ["address"], [CERO])


def _avvy_reclama_miniholder(cadena: Cadena) -> None:
    """A reclama `miniholder.avax` en Avvy, y el nombre apunta de vuelta a A."""
    inverso = "0x" + "55" * 20
    cadena.ok(_avvy.REVERSE_RESOLVER_REGISTRY, "getResolver(uint256)", ["address"], [inverso])
    cadena.ok(inverso, "get(address)", ["uint256", "uint256"], [1, 2])
    senales = _avvy._label_inputs("avax") + _avvy._label_inputs("miniholder")
    cadena.ok(_avvy.RAINBOW_TABLE, "lookup(uint256)", ["uint256[]"], [senales])
    _avvy_con_evm(cadena, A)


# ---------------------------------------------------------------------------
# SDK-6 · ningún sistema preguntado
# ---------------------------------------------------------------------------


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


def test_un_reverse_sin_ningun_sistema_de_reverse_habilitado_es_unsupported_system() -> None:
    """Configuración y no caída: lo que `resolve()` contesta para un sistema
    deshabilitado, decidido sin red. Tampoco es `not_found`. Mutación DI."""
    resolver = NameResolver(rpc=FAKE_RPC, systems=("ens-dns",), transport=failing_transport())
    with resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "unsupported_system"
    assert result.tried == () and result.verified_onchain is False
    assert resolver.cache is not None and len(resolver.cache) == 0


# ---------------------------------------------------------------------------
# A · un negativo con algún sistema salteado no está verificado
# ---------------------------------------------------------------------------


@asincronia
def test_A_not_found_con_un_sistema_salteado_NO_esta_verificado(asincrono: bool) -> None:
    """El caso que midió el refutador: sin RPC de Base, ENS, UNS y Avvy
    contestan «no hay nombre». Antes: `verified_onchain=True`. Mutación DK."""
    cadena = Cadena()
    _nadie_tiene_nombre(cadena)
    resolver = _resolver(cadena, (ETH, POLYGON, AVAX))
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "not_found"
    assert result.verified_onchain is False
    assert result.tried == ("ens", "unstoppable", "avvy")
    # Desde la ronda 2 (R4) también se nombra la cadena de UNS que quedó sin preguntar.
    assert result.detail == (
        "no primary name; skipped: basenames (no RPC for eip155:8453), "
        "unstoppable on eip155:8453 (no RPC)"
    )
    assert resolver.cache is not None and len(resolver.cache) == 1, "es una respuesta: se cachea"


@asincronia
def test_A_not_found_con_todos_preguntados_SI_esta_verificado(asincrono: bool) -> None:
    """El otro estado: los cuatro contestaron."""
    cadena = Cadena()
    _nadie_tiene_nombre(cadena)
    result = call_op(_resolver(cadena, (ETH, BASE, POLYGON, AVAX)), "reverse", [A],
                     asynchronous=asincrono)
    assert result.error == "not_found"
    assert result.verified_onchain is True
    assert result.tried == ("ens", "basenames", "unstoppable", "avvy")
    assert result.detail == "no primary name"


@pytest.mark.parametrize(
    ("cadenas", "verificado"),
    [
        pytest.param((ETH, POLYGON), False, id="avvy-salteado"),
        pytest.param((ETH, POLYGON, AVAX), True, id="todos-preguntados"),
    ],
)
def test_A_un_reverse_mismatch_sigue_la_misma_regla(
    cadenas: Tuple[str, ...], verificado: bool
) -> None:
    """La misma enfermedad en el otro negativo: UNS reclama `brad.crypto`, que
    apunta a OTRA dirección. Con Avvy salteado, un nombre válido de Avvy habría
    cambiado la respuesta. Mutación DL."""
    cadena = Cadena()
    _uns_reverse(cadena, "brad.crypto")
    _uns_con_eth(cadena, B)
    cadena.ok(_avvy.REVERSE_RESOLVER_REGISTRY, "getResolver(uint256)", ["address"], [CERO])
    with _resolver(cadena, cadenas, systems=("unstoppable", "avvy")) as resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "reverse_mismatch"
    assert result.normalized is None
    assert result.verified_onchain is verificado


# ---------------------------------------------------------------------------
# B · un sistema salteado sigue ocupando su lugar en el orden estricto
# ---------------------------------------------------------------------------


@asincronia
def test_B_sin_RPC_de_L1_el_nombre_de_Avvy_NO_sale_como_primario(asincrono: bool) -> None:
    """Medido con este doble antes del arreglo: `miniholder.avax`, `system`
    avvy, `verified_onchain=True`. ENS, Basenames y UNS se saltearon y podían
    tener el primario. Ahora `rpc_unavailable`, sin el nombre. Mutación DM."""
    cadena = Cadena()
    _avvy_reclama_miniholder(cadena)
    resolver = _resolver(cadena, (AVAX,))
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert result.verified_onchain is False
    assert result.normalized is None and "miniholder" not in repr(result)
    assert result.tried == ("avvy",)
    assert "skipped: ens (no RPC for eip155:1)" in (result.detail or "")
    assert resolver.cache is not None and len(resolver.cache) == 0


@asincronia
def test_B_con_los_de_arriba_preguntados_el_nombre_de_Avvy_SI_es_el_primario(
    asincrono: bool,
) -> None:
    """El otro estado: los de arriba contestaron «no hay nombre»."""
    cadena = Cadena()
    _nadie_tiene_nombre(cadena)
    _avvy_reclama_miniholder(cadena)  # pisa el getResolver vacío de Avvy
    result = call_op(_resolver(cadena, (ETH, BASE, POLYGON, AVAX)), "reverse", [A],
                     asynchronous=asincrono)
    assert result.error is None
    assert result.normalized == "miniholder.avax" and result.system == "avvy"
    assert result.verified_onchain is True


def test_B_un_sistema_DESHABILITADO_no_ocupa_lugar() -> None:
    """Deshabilitar es una elección, no una caída: con `systems=("avvy",)` los
    de arriba no están en juego y el nombre de Avvy es la respuesta."""
    cadena = Cadena()
    _avvy_reclama_miniholder(cadena)
    with _resolver(cadena, (AVAX,), systems=("avvy",)) as resolver:
        result = resolver.reverse_sync(A)
    assert result.error is None
    assert result.normalized == "miniholder.avax" and result.verified_onchain is True


# ---------------------------------------------------------------------------
# R4 (ronda 2) · UNS preguntado en ALGUNAS de sus cadenas
# ---------------------------------------------------------------------------
#
# Decisión de c0der: igual que A. Medido por el refutador sobre `e1d311c7`:
# `systems=("unstoppable",)` con sólo el RPC de L1 contestaba `not_found`
# verificado, cacheado, sin decir que Polygon y Base no se preguntaron. Las
# cadenas sin preguntar cuentan como salteadas: se nombran, desverifican un
# negativo, y retienen un nombre hallado DEBAJO de ellas (el orden de UNS es
# L1, Polygon, Base). Mutación DX.

BALD = "alguien.bald"  # un TLD de UNS en Base: su forward lee Base y L1, no Polygon


def _uns_reclama(cadena: Cadena, por_cadena: dict) -> None:
    for chain, nombre in por_cadena.items():
        cadena.ok(_uns.PROXY_READERS[chain], "reverseNameOf(address)", ["string"], [nombre])


def _bald_apunta_a_A(cadena: Cadena) -> None:
    cadena.ok(
        _uns.PROXY_READERS[BASE],
        "getData(string[],uint256)",
        ["address", "address", "string[]"],
        ["0x" + "33" * 20, "0x" + "44" * 20, [A]],
    )


@asincronia
def test_R4_UNS_sin_nombre_con_solo_L1_NO_esta_verificado(asincrono: bool) -> None:
    cadena = Cadena()
    _uns_reclama(cadena, {ETH: ""})
    resolver = _resolver(cadena, (ETH,), systems=("unstoppable",))
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "not_found" and result.verified_onchain is False
    assert result.detail == (
        "no primary name; skipped: unstoppable on eip155:137, eip155:8453 (no RPC)"
    )
    assert resolver.cache is not None and len(resolver.cache) == 1, "sigue siendo una respuesta"


@asincronia
def test_R4_UNS_sin_nombre_en_sus_tres_cadenas_SI_esta_verificado(asincrono: bool) -> None:
    cadena = Cadena()
    _uns_reclama(cadena, {ETH: "", POLYGON: "", BASE: ""})
    resolver = _resolver(cadena, (ETH, POLYGON, BASE), systems=("unstoppable",))
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "not_found" and result.verified_onchain is True
    assert result.detail == "no primary name"


def test_R4_un_nombre_en_L1_se_da_aunque_falte_Polygon() -> None:
    """L1 va primero: lo que diga Polygon ya no cambia la respuesta."""
    cadena = Cadena()
    _uns_reclama(cadena, {ETH: BALD})
    _bald_apunta_a_A(cadena)
    with _resolver(cadena, (ETH, BASE), systems=("unstoppable",)) as resolver:
        result = resolver.reverse_sync(A)
    assert result.error is None
    assert result.normalized == BALD and result.verified_onchain is True


@asincronia
def test_R4_un_nombre_en_Base_con_Polygon_sin_preguntar_NO_se_da(asincrono: bool) -> None:
    """Polygon va antes que Base y no se preguntó: el de Base puede no ser el primario."""
    cadena = Cadena()
    _uns_reclama(cadena, {ETH: "", BASE: BALD})
    _bald_apunta_a_A(cadena)
    resolver = _resolver(cadena, (ETH, BASE), systems=("unstoppable",))
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "rpc_unavailable" and result.verified_onchain is False
    assert result.normalized is None and "alguien" not in repr(result)
    assert "unstoppable on eip155:137 (no RPC)" in (result.detail or "")
