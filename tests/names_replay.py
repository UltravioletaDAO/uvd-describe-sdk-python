"""El doble de la cadena para los tests de nombres: REPRODUCE lo grabado, nada más.

Cada archivo de `tests/fixtures/names/` lo escribió
`scripts/grabar_fixtures_names.py` contra RPC y gateways reales el 2026-09-24:
cada `eth_call` y cada request CCIP, con su respuesta, en el orden en que el
resolver los pidió. Este reproductor:

* contesta la request N con el intercambio N, y **falla si la request no es la
  grabada** (otra cadena, otro contrato, otro calldata, otra URL): un resolver
  que cambió de camino no puede pasar con una respuesta ajena;
* y `assert_consumed()` exige que se hayan usado TODOS — «cada doble afirma que
  se usó». Un test que pasa sin haber pedido lo grabado no probó lo grabado.

Las URLs de RPC de la grabación no se guardan (pueden llevar llave); acá cada
cadena tiene una URL de mentira bajo `rpc.test`, que no resuelve en ninguna red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from uvd_describe_sdk.names import NameResolver

FIXTURES = Path(__file__).parent / "fixtures" / "names"

#: Una URL de mentira por cadena. `.test` es un TLD reservado (RFC 2606).
FAKE_RPC: Dict[str, str] = {
    chain: f"https://rpc.test/{chain.replace(':', '-')}"
    for chain in ("eip155:1", "eip155:8453", "eip155:137", "eip155:43114")
}
_CHAIN_OF = {url: chain for chain, url in FAKE_RPC.items()}


def answer_chain_id(request: httpx.Request) -> Optional[httpx.Response]:
    """🔴 SINTÉTICO: el `eth_chainId` que el motor pide antes del primer
    `eth_call` de cada cadena (ronda 2 del PR #6).

    Las fixtures se grabaron antes de ese chequeo y el script de grabación no lo
    graba: se contesta desde la clave CAIP-2 de la URL de mentira, que es
    exactamente lo que un RPC honesto de esa cadena diría. `None` si la request
    no es un `eth_chainId` a una URL de `FAKE_RPC`.
    """
    chain = _CHAIN_OF.get(str(request.url))
    if chain is None or not request.content:
        return None
    if json.loads(request.content).get("method") != "eth_chainId":
        return None
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": 1, "result": hex(int(chain.split(":")[1]))}
    )


def load(name: str) -> Dict[str, Any]:
    data: Dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


def all_fixture_names() -> List[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


class Replay:
    """Handler de `httpx.MockTransport` que reproduce UNA grabación, en orden."""

    def __init__(self, exchanges: List[Dict[str, Any]]) -> None:
        self.exchanges = exchanges
        self.used = 0
        #: Cuántos `eth_chainId` contestó (sintéticos: ver `answer_chain_id`).
        self.chain_ids = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        chain_id = answer_chain_id(request)
        if chain_id is not None:
            self.chain_ids += 1
            return chain_id
        position = self.used + 1
        assert self.used < len(self.exchanges), (
            f"la request #{position} no está en la grabación: {request.method} {request.url}"
        )
        grabado = self.exchanges[self.used]
        self.used += 1
        if grabado["kind"] == "rpc":
            chain = _CHAIN_OF.get(str(request.url))
            call = json.loads(request.content)["params"][0]
            pedido = (chain, call["to"], call["data"])
            esperado = (grabado["chain"], grabado["to"], grabado["data"])
            assert pedido == esperado, (
                f"request #{position} distinta de la grabada:\n"
                f"  pedida:  {pedido}\n  grabada: {esperado}"
            )
            return httpx.Response(grabado["status"], json=grabado["response"])
        body = request.content.decode() if request.content else None
        pedido_http = (request.method, str(request.url), body)
        esperado_http = (grabado["method"], grabado["url"], grabado["body"])
        assert pedido_http == esperado_http, (
            f"request #{position} distinta de la grabada:\n  pedida:  {pedido_http}\n"
            f"  grabada: {esperado_http}"
        )
        return httpx.Response(
            grabado["status"],
            headers=grabado.get("headers") or {},
            content=grabado["response_text"].encode("utf-8"),
        )

    def assert_consumed(self) -> None:
        assert self.used == len(self.exchanges), (
            f"se usaron {self.used} de {len(self.exchanges)} intercambios grabados: "
            "el resolver dejó de pedir algo que en vivo sí pidió"
        )


def resolver_for(
    fixture: Dict[str, Any], replay: Replay, *, cache: Any = False, **overrides: Any
) -> NameResolver:
    """Un `NameResolver` configurado como en la grabación, contra el reproductor."""
    options: Dict[str, Any] = dict(fixture.get("options") or {})
    if "systems" in options:
        options["systems"] = tuple(options["systems"])
    options.update(overrides)
    transport = httpx.MockTransport(replay)
    return NameResolver(
        rpc={chain: FAKE_RPC[chain] for chain in fixture["chains"]},
        cache=cache,
        transport=transport,
        async_transport=transport,
        clock=lambda: float(fixture["now"]),
        **options,
    )


def call_op(resolver: NameResolver, op: str, args: List[str], *, asynchronous: bool) -> Any:
    """Corre la operación grabada, en su variante sync o async."""
    if not asynchronous:
        return getattr(resolver, f"{op}_sync")(*args)
    import asyncio

    async def _run() -> Any:
        try:
            return await getattr(resolver, op)(*args)
        finally:
            await resolver.aclose()

    return asyncio.run(_run())


def failing_transport(error: Optional[Exception] = None) -> httpx.MockTransport:
    """Un transporte que revienta si alguien lo usa: prueba que NO hubo red."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise error or AssertionError(f"no debía salir ninguna request: {request.url}")

    return httpx.MockTransport(_handler)
