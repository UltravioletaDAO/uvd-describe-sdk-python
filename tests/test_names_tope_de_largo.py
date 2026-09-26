"""R2 (ronda 2 del PR 7, P1 [security]): un tope de largo ANTES de ENSIP-15.

ENSIP-15 cuesta más que lineal, y el plazo (`wait_for`) no corta CPU dentro de un
paso. El refutador midió con `timeout=1.0`: un nombre primario reclamado de
caracteres combinantes, 150 KB → `reverse()` tardó 5,2 s; 900 KB (cabe en el
tope de 2 MB de hex) → 67,5 s, y en async bloqueando el event loop del
consumidor todo ese tiempo. El nombre lo escribe quien controla la dirección.

`MAX_NAME_BYTES` (1.024, en `_normalize.py`) se chequea en UN helper
(`too_long`) y en tres sitios, siempre ANTES de normalizar:

* nombre reclamado on-chain, en ENS (`_ens.confirm`) → `reverse_mismatch`
  «the reverse record is too long» (mutación DU);
* ídem en UNS y Avvy (`_resolver._reverse_one`) (mutación DV);
* entrada de `resolve()` / `text()` / `avatar()` → `invalid_name` (mutación DT).

El espía de abajo reemplaza `ens_normalize` y FALLA si recibe más que el tope:
así el test prueba que nunca se llegó a normalizar, no sólo el código final.

🔴 Los dobles son SINTÉTICOS (la `Cadena` de la ronda 2).
"""

from __future__ import annotations

from typing import Any, Iterator

import httpx
import pytest

from uvd_describe_sdk.names import NameResolver, _ens, _normalize
from uvd_describe_sdk.names._normalize import MAX_NAME_BYTES

from .names_replay import FAKE_RPC, call_op, failing_transport
from .test_names_ronda2 import Cadena, R, _uns_reverse

A = "0x" + "11" * 20
COMBINANTES = ("a" + chr(0x301)) * 50_000 + ".eth"  # 150.004 bytes: 5,2 s medidos
ASCII_ENORME = "a" * 900_000 + ".eth"  # normaliza rápido; el tope cierra P3-3

asincronia = pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])


@pytest.fixture(autouse=True)
def espia(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`ens_normalize` falla si recibe más que el tope: nadie lo llamó con eso."""
    real = _normalize.ens_normalize

    def vigilado(nombre: str) -> Any:
        largo = len(nombre.encode("utf-8", "surrogatepass"))
        assert largo <= MAX_NAME_BYTES, f"ens_normalize recibió {largo} bytes"
        return real(nombre)

    monkeypatch.setattr(_normalize, "ens_normalize", vigilado)
    yield


def _ens_reclama(nombre: str) -> NameResolver:
    """A reclama `nombre` como primario en L1, con un resolver propio."""
    cadena = Cadena()
    cadena.ok(_ens.REGISTRY, "resolver(bytes32)", ["address"], [R])
    cadena.ok(R, "supportsInterface(bytes4)", ["bool"], [False])
    cadena.ok(R, "name(bytes32)", ["string"], [nombre])
    transporte = httpx.MockTransport(cadena)
    return NameResolver(
        rpc={"eip155:1": FAKE_RPC["eip155:1"]},
        systems=("ens",),
        transport=transporte,
        async_transport=transporte,
    )


@asincronia
@pytest.mark.parametrize("nombre", [COMBINANTES, ASCII_ENORME], ids=["combinantes", "ascii"])
def test_un_nombre_reclamado_enorme_en_ENS_es_reverse_mismatch_sin_normalizar(
    nombre: str, asincrono: bool
) -> None:
    result = call_op(_ens_reclama(nombre), "reverse", [A], asynchronous=asincrono)
    assert result.error == "reverse_mismatch"
    assert result.detail == "the reverse record is too long"
    assert result.normalized is None


@asincronia
def test_un_nombre_reclamado_enorme_en_UNS_es_reverse_mismatch(asincrono: bool) -> None:
    """El otro sitio: UNS y Avvy pasan por `_reverse_one`."""
    cadena = Cadena()
    _uns_reverse(cadena, "a" * 2_000 + ".crypto")
    transporte = httpx.MockTransport(cadena)
    resolver = NameResolver(
        rpc=FAKE_RPC, systems=("unstoppable",), transport=transporte, async_transport=transporte
    )
    result = call_op(resolver, "reverse", [A], asynchronous=asincrono)
    assert result.error == "reverse_mismatch"
    assert result.detail == "the reverse record is too long"


@asincronia
@pytest.mark.parametrize("op", ["resolve", "text", "avatar"])
def test_una_entrada_enorme_es_invalid_name_sin_red_y_sin_normalizar(
    op: str, asincrono: bool
) -> None:
    resolver = NameResolver(rpc=FAKE_RPC, transport=failing_transport())
    args = [COMBINANTES, "url"] if op == "text" else [COMBINANTES]
    result = call_op(resolver, op, args, asynchronous=asincrono)
    assert result.error == "invalid_name"
    assert result.detail == f"a name is at most {MAX_NAME_BYTES} bytes"


def test_el_borde_del_tope() -> None:
    """Hasta `MAX_NAME_BYTES` se normaliza como siempre; un byte más, no."""
    resolver = NameResolver(rpc=FAKE_RPC, transport=failing_transport())
    justo = "a" * (MAX_NAME_BYTES - 4) + ".eth"
    assert resolver.normalize(justo) == justo
    assert resolver.detect("a" + justo) is None
    assert resolver.resolve_sync("a" + justo).error == "invalid_name"
