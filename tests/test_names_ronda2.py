"""Ronda 2 del PR #6: las reglas que el refutador encontró SIN atar.

Cada test de acá existe porque una mutación del código dejaba la suite entera en
verde (448 de 448). Las reglas de plata de UNS, Avvy y el reverse no tenían
grabación que las ejercitara: la cadena real no ofrece, a pedido, un UNS que
conteste la dirección cero ni un reverse de Avvy que apunte a otra dirección.

🔴 Por eso los dobles de este archivo son SINTÉTICOS y se nombran así: `Cadena`
contesta por (contrato, selector) lo que el test le dice, y no describe ningún
hecho del mundo. Lo que se prueba es la REGLA del SDK ante esa respuesta. Los
hechos medidos siguen en `test_names_hechos_medidos.py`, contra grabaciones.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List, Tuple

import httpx
import pytest

from uvd_describe_sdk import NameNotVerifiedError, NameResolution, require_onchain_address
from uvd_describe_sdk.names import (
    ICANN_TLDS_MEASURED_AT,
    UNS_ICANN_COLLISIONS,
    InvalidNameError,
    NameResolver,
    _abi,
    _avvy,
    _ens,
    _uns,
    labelhash,
    namehash,
)
from uvd_describe_sdk.names._hash import selector
from uvd_describe_sdk.names._proto import OFFCHAIN_LOOKUP

from .names_replay import FAKE_RPC, Replay, answer_chain_id, failing_transport, load, resolver_for

A = "0x" + "11" * 20  # la dirección por la que se pregunta
B = "0x" + "22" * 20  # otra dirección
R = "0x" + "33" * 20  # un resolver
OTRO = "0x" + "44" * 20
CERO_MINUSCULA = "0x" + "0" * 40
CERO_MAYUSCULA = "0X" + "0" * 40
FUTURO = 4_102_444_800  # 2100-01-01


class Cadena:
    """🔴 SINTÉTICO: un RPC que contesta por (contrato, selector), nada más."""

    def __init__(self) -> None:
        self.rutas: Dict[Tuple[str, bytes], Dict[str, Any]] = {}
        self.http: Dict[str, httpx.Response] = {}
        self.eth_calls: List[Tuple[str, bytes]] = []
        self.pedidas_http: List[str] = []

    def ok(self, to: str, firma: str, tipos: List[str], valores: List[Any]) -> None:
        salida = "0x" + _abi.encode(tipos, valores).hex()
        self.rutas[(to.lower(), selector(firma))] = {"jsonrpc": "2.0", "id": 1, "result": salida}

    def revierte(self, to: str, firma: str, datos: bytes) -> None:
        error = {"code": 3, "message": "execution reverted", "data": "0x" + datos.hex()}
        self.rutas[(to.lower(), selector(firma))] = {"jsonrpc": "2.0", "id": 1, "error": error}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        chain_id = answer_chain_id(request)
        if chain_id is not None:
            return chain_id
        if str(request.url) in FAKE_RPC.values():
            llamada = json.loads(request.content)["params"][0]
            clave = (llamada["to"].lower(), bytes.fromhex(llamada["data"][2:10]))
            self.eth_calls.append(clave)
            assert clave in self.rutas, f"eth_call no previsto por el doble: {clave}"
            return httpx.Response(200, json=self.rutas[clave])
        self.pedidas_http.append(str(request.url))
        assert str(request.url) in self.http, f"HTTP no previsto por el doble: {request.url}"
        return self.http[str(request.url)]

    def resolver(self, **kwargs: Any) -> NameResolver:
        transporte = httpx.MockTransport(self)
        return NameResolver(
            rpc=FAKE_RPC, transport=transporte, async_transport=transporte, **kwargs
        )


# ---------------------------------------------------------------------------
# P1-1 · el timeout duro también en sync (un goteo no estira la llamada)
# ---------------------------------------------------------------------------


async def _goteo(texto: str, pausa: float) -> AsyncIterator[bytes]:
    # Async desde la ronda 4: el modo sync corre sobre el motor async, y un cliente
    # async no puede leer un cuerpo que es un generador sync (httpx lo rechaza).
    # El goteo es el mismo; lo que cambió es por dónde se lee.
    for byte in texto.encode():
        await asyncio.sleep(pausa)
        yield bytes([byte])


def test_sync_un_gateway_CCIP_que_gotea_se_corta_en_el_presupuesto() -> None:
    """La grabación REAL de `jesse.base.eth`, con el gateway de Coinbase
    goteando su respuesta de a un byte cada 20 ms (el goteo es lo sintético).
    Medido por el refutador sobre c9d67c6: presupuesto 1,0 s, 6,36 s."""
    fixture = load("resolve_jesse_base_eth")
    replay = Replay(fixture["exchanges"])

    def goteando(request: httpx.Request) -> httpx.Response:
        respuesta = replay(request)
        if request.url.host == "api.coinbase.com":
            return httpx.Response(respuesta.status_code, content=_goteo(respuesta.text, 0.02))
        return respuesta

    presupuesto = 0.5
    with NameResolver(
        rpc=FAKE_RPC,
        cache=False,
        timeout=presupuesto,
        transport=httpx.MockTransport(goteando),
        clock=lambda: float(fixture["now"]),
    ) as resolver:
        inicio = time.monotonic()
        result = resolver.resolve_sync("jesse.base.eth")
        duro = time.monotonic() - inicio
    assert result.error == "rpc_unavailable"
    assert "budget" in (result.detail or "")
    assert duro < presupuesto + 0.5, f"el presupuesto era {presupuesto} s y tardó {duro:.2f} s"


def test_sync_un_RPC_que_gotea_tambien_se_corta() -> None:
    def goteando(request: httpx.Request) -> httpx.Response:
        chain_id = answer_chain_id(request)
        if chain_id is not None:
            return chain_id
        cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x" + "7f" * 32})
        return httpx.Response(200, content=_goteo(cuerpo, 0.02))

    with NameResolver(
        rpc=FAKE_RPC, cache=False, timeout=0.5, transport=httpx.MockTransport(goteando)
    ) as resolver:
        inicio = time.monotonic()
        result = resolver.resolve_sync("ultravioletadao.eth")
        duro = time.monotonic() - inicio
    assert result.error == "rpc_unavailable"
    assert duro < 1.0


# ---------------------------------------------------------------------------
# P1-2 · las reglas de plata de UNS, Avvy y el reverse
# ---------------------------------------------------------------------------


def _uns_con_eth(cadena: Cadena, valor: str) -> None:
    for chain_reader in (_uns.PROXY_READERS["eip155:137"], _uns.PROXY_READERS["eip155:1"]):
        cadena.ok(
            chain_reader,
            "getData(string[],uint256)",
            ["address", "address", "string[]"],
            [R, OTRO, [valor]],
        )


def _avvy_con_evm(cadena: Cadena, valor: str) -> None:
    cadena.ok(_avvy.POSEIDON, "poseidon(uint256[3])", ["uint256"], [777])
    cadena.ok(_avvy.DOMAIN, "getDomainExpiry(uint256)", ["uint256"], [FUTURO])
    cadena.ok(_avvy.RESOLVER_REGISTRY, "get(uint256,uint256)", ["address", "uint256"], [R, 5])
    cadena.ok(R, "resolveStandard(uint256,uint256,uint256)", ["string"], [valor])


@pytest.mark.parametrize("cero", [CERO_MINUSCULA, CERO_MAYUSCULA])
def test_UNS_que_contesta_la_direccion_cero_es_not_found(cero: str) -> None:
    cadena = Cadena()
    _uns_con_eth(cadena, cero)
    with cadena.resolver() as resolver:
        result = resolver.resolve_sync("alguien.crypto")
    assert result.error == "not_found"
    assert result.address is None


@pytest.mark.parametrize("cero", [CERO_MINUSCULA, CERO_MAYUSCULA])
def test_Avvy_que_contesta_la_direccion_cero_es_not_found(cero: str) -> None:
    cadena = Cadena()
    _avvy_con_evm(cadena, cero)
    with cadena.resolver() as resolver:
        result = resolver.resolve_sync("miniholder.avax")
    assert result.error == "not_found"
    assert result.address is None


def _uns_reverse(cadena: Cadena, reclamado: str) -> None:
    cadena.ok(_uns.PROXY_READERS["eip155:1"], "reverseNameOf(address)", ["string"], [reclamado])


def test_reverse_UNS_cuyo_nombre_apunta_a_otra_direccion_es_reverse_mismatch() -> None:
    cadena = Cadena()
    _uns_reverse(cadena, "brad.crypto")
    _uns_con_eth(cadena, B)  # brad.crypto apunta a B, no a A
    with cadena.resolver(systems=("unstoppable",)) as resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "reverse_mismatch"
    assert result.normalized is None
    assert "brad" not in repr(result)


def test_reverse_UNS_con_nombre_no_normalizado_es_reverse_mismatch() -> None:
    """`Evil.crypto` apunta de vuelta a A — y aun así no se muestra: un nombre
    que no está en su forma normal es cómo entran los parecidos."""
    cadena = Cadena()
    _uns_reverse(cadena, "Evil.crypto")
    _uns_con_eth(cadena, A)
    with cadena.resolver(systems=("unstoppable",)) as resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "reverse_mismatch"
    assert result.normalized is None


def test_reverse_Avvy_cuyo_nombre_apunta_a_otra_direccion_es_reverse_mismatch() -> None:
    cadena = Cadena()
    inverso = "0x" + "55" * 20
    cadena.ok(_avvy.REVERSE_RESOLVER_REGISTRY, "getResolver(uint256)", ["address"], [inverso])
    cadena.ok(inverso, "get(address)", ["uint256", "uint256"], [1, 2])
    senales = _avvy._label_inputs("avax") + _avvy._label_inputs("miniholder")
    cadena.ok(_avvy.RAINBOW_TABLE, "lookup(uint256)", ["uint256[]"], [senales])
    _avvy_con_evm(cadena, B)  # miniholder.avax apunta a B, no a A
    with cadena.resolver(systems=("avvy",)) as resolver:
        result = resolver.reverse_sync(A)
    assert result.error == "reverse_mismatch"
    assert result.normalized is None
    assert "miniholder" not in repr(result)


# ---------------------------------------------------------------------------
# P2-1 · un registro que revierte no escapa como excepción privada
# ---------------------------------------------------------------------------


def _todo_revierte(request: httpx.Request) -> httpx.Response:
    chain_id = answer_chain_id(request)
    if chain_id is not None:
        return chain_id
    error = {"code": 3, "message": "execution reverted"}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error})


@pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])
def test_reverse_con_un_RPC_que_revierte_todo_es_rpc_unavailable(asincrono: bool) -> None:
    transporte = httpx.MockTransport(_todo_revierte)
    resolver = NameResolver(rpc=FAKE_RPC, transport=transporte, async_transport=transporte)
    if asincrono:

        async def correr() -> NameResolution:
            async with resolver as r:
                return await r.reverse(A)

        result = asyncio.run(correr())
    else:
        with resolver:
            result = resolver.reverse_sync(A)
    assert result.error == "rpc_unavailable"
    assert result.system == "ens" and result.tried == ("ens",)
    assert resolver.cache is not None and len(resolver.cache) == 0


# ---------------------------------------------------------------------------
# P2-2 · el RPC se verifica contra su cadena
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])
def test_un_RPC_de_otra_cadena_bajo_la_clave_de_mainnet_es_rpc_unavailable(
    asincrono: bool,
) -> None:
    """Medido por el refutador: el RPC público de Sepolia bajo `eip155:1`
    resolvía `vitalik.eth` con `verified_onchain=True`. Parametrizado en la
    ronda 3: el chequeo del motor ASYNC no tenía test (borrarlo dejaba 476
    verdes)."""
    eth_calls: List[str] = []

    def sepolia(request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        if cuerpo["method"] == "eth_chainId":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0xaa36a7"})
        eth_calls.append(cuerpo["method"])
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x"})

    transporte = httpx.MockTransport(sepolia)
    resolver = NameResolver(rpc=FAKE_RPC, transport=transporte, async_transport=transporte)
    if asincrono:

        async def correr() -> NameResolution:
            async with resolver as r:
                return await r.resolve("ultravioletadao.eth")

        result = asyncio.run(correr())
    else:
        with resolver:
            result = resolver.resolve_sync("ultravioletadao.eth")
    assert result.error == "rpc_unavailable"
    assert result.verified_onchain is False
    assert "eip155:1" in (result.detail or "") and "eip155:11155111" in (result.detail or "")
    assert "rpc.test" not in repr(result)
    assert eth_calls == [], "con la cadena equivocada no se pregunta nada más"
    assert resolver.cache is not None and len(resolver.cache) == 0


def test_el_chequeo_de_cadena_se_hace_una_vez_por_cadena_y_por_resolver() -> None:
    fixture = load("resolve_ultravioletadao_eth")
    replay = Replay(fixture["exchanges"] * 2)
    with resolver_for(fixture, replay) as resolver:
        resolver.resolve_sync("ultravioletadao.eth")
        resolver.resolve_sync("ultravioletadao.eth")
    replay.assert_consumed()
    assert replay.chain_ids == 1


# ---------------------------------------------------------------------------
# P2-3 · los TLD que son a la vez de UNS y de ICANN no se eligen en silencio
# ---------------------------------------------------------------------------


def test_las_cinco_colisiones_medidas() -> None:
    assert UNS_ICANN_COLLISIONS == {"graphics", "gripe", "guide", "shiksha", "travel"}
    assert ICANN_TLDS_MEASURED_AT == "2026-09-24"


@pytest.mark.parametrize("tld", sorted(UNS_ICANN_COLLISIONS))
def test_un_nombre_bajo_una_colision_es_unsupported_system_sin_red(tld: str) -> None:
    with NameResolver(rpc=FAKE_RPC, transport=failing_transport()) as resolver:
        result = resolver.resolve_sync(f"alguien.{tld}")
        assert resolver.detect(f"alguien.{tld}") is None
        assert resolver.normalize(f"Alguien.{tld}") == f"alguien.{tld}"
    assert result.error == "unsupported_system"
    assert result.system is None and result.address is None
    assert "collision" in (result.detail or "")


def test_un_TLD_de_UNS_que_no_colisiona_sigue_siendo_UNS() -> None:
    assert NameResolver(rpc={}).detect("alguien.crypto") == "unstoppable"


# ---------------------------------------------------------------------------
# P2-4 · namehash y labelhash públicos, que NORMALIZAN
# ---------------------------------------------------------------------------


def _ancho(texto: str) -> str:
    return "".join(chr(ord(c) - ord("A") + 0xFF21) if c.isupper() else c for c in texto)


def test_namehash_publico_con_los_vectores_de_EIP137() -> None:
    assert namehash("") == b"\x00" * 32
    assert namehash("eth").hex() == (
        "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    )
    assert namehash("foo.eth").hex() == (
        "de9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f"
    )


def test_namehash_publico_normaliza_antes_de_hashear() -> None:
    """El namehash de EM bajaba a minúsculas; el de ENS normaliza (ENSIP-15)."""
    assert namehash("Foo.ETH") == namehash("foo.eth")
    ancho = _ancho("FOO") + ".eth"
    assert ancho.lower() != "foo.eth"
    assert namehash(ancho) == namehash("foo.eth")
    with pytest.raises(InvalidNameError):
        namehash("a..eth")


def test_labelhash_publico() -> None:
    assert labelhash("eth").hex() == (
        "4f5b812789fc606be1b3b16908db13fc7a9adf7ca72641f84d75b47069d3d7f0"
    )
    assert labelhash(_ancho("ETH")) == labelhash("eth")
    for malo in ("a.b", "", "a b"):
        with pytest.raises(InvalidNameError):
            labelhash(malo)


# ---------------------------------------------------------------------------
# P2-5 · un error JSON-RPC que no es revert, y un OffchainLookup ajeno
# ---------------------------------------------------------------------------


def test_un_error_json_rpc_que_no_es_revert_es_rpc_unavailable_y_no_se_cachea() -> None:
    def limitado(request: httpx.Request) -> httpx.Response:
        chain_id = answer_chain_id(request)
        if chain_id is not None:
            return chain_id
        error = {"code": -32005, "message": "limit exceeded"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": error})

    with NameResolver(rpc=FAKE_RPC, transport=httpx.MockTransport(limitado)) as resolver:
        result = resolver.resolve_sync("ultravioletadao.eth")
    assert result.error == "rpc_unavailable"
    assert resolver.cache is not None and len(resolver.cache) == 0


def _ens_extendido(cadena: Cadena) -> None:
    """Un `.eth` registrado hasta 2100, con un resolver ENSIP-10 en R."""
    cadena.ok(_ens.ETH_REGISTRAR, "nameExpires(uint256)", ["uint256"], [FUTURO])
    cadena.ok(_ens.REGISTRY, "resolver(bytes32)", ["address"], [R])
    cadena.ok(R, "supportsInterface(bytes4)", ["bool"], [True])


def _offchain_lookup(sender: str, urls: List[str]) -> bytes:
    return OFFCHAIN_LOOKUP + _abi.encode(
        ["address", "string[]", "bytes", "bytes4", "bytes"],
        [sender, urls, b"\x01", b"\xaa\xbb\xcc\xdd", b""],
    )


def test_un_OffchainLookup_cuyo_sender_no_es_el_resolver_no_se_sigue() -> None:
    cadena = Cadena()
    _ens_extendido(cadena)
    gateway = "https://gw.example/{sender}/{data}.json"
    cadena.revierte(R, "resolve(bytes,bytes)", _offchain_lookup(OTRO, [gateway]))
    with cadena.resolver() as resolver:
        result = resolver.resolve_sync("alguien.eth")
    assert result.error == "not_found"
    assert "sender" in (result.detail or "")
    assert cadena.pedidas_http == [], "el gateway de un sender ajeno no se consulta"


@pytest.mark.parametrize(("estado", "codigo"), [(404, "not_found"), (403, "rpc_unavailable")])
def test_un_4xx_del_gateway_corta_y_no_se_prueba_el_siguiente(estado: int, codigo: str) -> None:
    """EIP-3668: un 4xx termina la búsqueda. Sólo un 5xx pasa a la URL siguiente."""
    cadena = Cadena()
    _ens_extendido(cadena)
    primero = "https://uno.example/{sender}/{data}.json"
    segundo = "https://dos.example/{sender}/{data}.json"
    cadena.revierte(R, "resolve(bytes,bytes)", _offchain_lookup(R, [primero, segundo]))
    url_uno = primero.replace("{sender}", R.lower()).replace("{data}", "0x01")
    cadena.http[url_uno] = httpx.Response(estado, json={"message": "no"})
    with cadena.resolver() as resolver:
        result = resolver.resolve_sync("alguien.eth")
    assert result.error == codigo
    assert cadena.pedidas_http == [url_uno], "después de un 4xx no se prueba otra URL"


# ---------------------------------------------------------------------------
# P3 · la última puerta antes de un pago re-chequea la dirección cero
# ---------------------------------------------------------------------------


def test_require_onchain_address_rechaza_la_direccion_cero_armada_a_mano() -> None:
    a_mano = NameResolution(
        input="x.eth",
        normalized="x.eth",
        address=CERO_MINUSCULA,
        family="evm",
        system="ens",
        verified_onchain=True,
    )
    with pytest.raises(NameNotVerifiedError) as exc:
        require_onchain_address(a_mano)
    assert exc.value.reason == "not_found"
