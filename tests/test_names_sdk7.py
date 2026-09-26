"""SDK-7 (ronda 1 del PR 7): un `nameExpires` que revierte es un RPC roto, no un
nombre que no existe.

`check_expiry` convertía un `Reverted` de `nameExpires` en `not_found` con
`verified_onchain=True`. El refutador de describe-net lo midió con un RPC propio
que contesta «execution reverted» a TODO: `ultravioletadao.eth` salía
`not_found`, «the registrar did not answer».

Antes de tocarlo se midió si un nombre legítimamente no registrado puede hacer
revertir `nameExpires` (2026-09-26). No puede:

* la grabación REAL de `0xultravioletadao.eth` (mainnet, 2026-09-24,
  `resolve_0xultravioletadao_eth_no_existe`): el registrar contestó `0x00…0`;
* ENS `BaseRegistrarImplementation` (`0x57f1…eA85`, ens-contracts `e2dc52b230`):
  `function nameExpires(uint256 id) … returns (uint256) { return expiries[id]; }`;
* Basenames `BaseRegistrar` (`0x03c4…DD9a`, base-org/basenames `43de03917c`):
  `mapping(uint256 id => uint256 expiry) public nameExpires;` — el getter de un
  mapping público.

Las dos son lecturas de un mapping: sin registro, 0. Un revert ahí sólo lo
produce el RPC, así que es `rpc_unavailable` (que no se cachea).
"""

from __future__ import annotations

import httpx
import pytest

from uvd_describe_sdk.names import NameResolver

from .names_replay import FAKE_RPC, Replay, answer_chain_id, call_op, load, resolver_for

asincronia = pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])


def _todo_revierte(request: httpx.Request) -> httpx.Response:
    chain_id = answer_chain_id(request)
    if chain_id is not None:
        return chain_id
    error = {"code": 3, "message": "execution reverted"}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error})


@asincronia
@pytest.mark.parametrize(
    ("nombre", "cadena"),
    [("ultravioletadao.eth", "eip155:1"), ("jesse.base.eth", "eip155:8453")],
    ids=["ens", "basenames"],
)
def test_un_RPC_que_revierte_nameExpires_es_rpc_unavailable_y_no_not_found(
    nombre: str, cadena: str, asincrono: bool
) -> None:
    """Antes: `not_found`, `verified_onchain=True`, «the registrar did not
    answer». Mutación DN."""
    transporte = httpx.MockTransport(_todo_revierte)
    resolver = NameResolver(rpc=FAKE_RPC, transport=transporte, async_transport=transporte)
    result = call_op(resolver, "resolve", [nombre], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert result.verified_onchain is False and result.address is None
    assert result.detail == f"the {cadena} RPC reverted nameExpires, which cannot revert"
    assert resolver.cache is not None and len(resolver.cache) == 0


def test_un_nombre_no_registrado_de_verdad_sigue_siendo_not_found() -> None:
    """El otro estado, contra la cadena real: el registrar contestó 0."""
    fixture = load("resolve_0xultravioletadao_eth_no_existe")
    replay = Replay(fixture["exchanges"])
    with resolver_for(fixture, replay) as resolver:
        result = resolver.resolve_sync("0xultravioletadao.eth")
    replay.assert_consumed()
    assert result.error == "not_found" and result.verified_onchain is True
    assert result.detail == "0xultravioletadao.eth is not registered"
