"""SDK-5: una URL elegida on-chain ya no hace LEVANTAR al resolver.

Fila P1 que la revisión de las rutas de nombres de describe.net le encontró al
resolver ANTES del tag v0.7.0, contra `5ed00228`: una URL que elige la cadena
(gateway CCIP, metadata del NFT, redirect, registro de avatar) hacía levantar
al resolver en vez de contestar — el `ValueError` de `urlsplit` en `check_url`,
el de `urljoin` en un redirect y el `httpx.InvalidURL` del motor. El mismo
barrido encontró dos más en ese camino: el `RecursionError` de `json.loads` (un
cuerpo de 1.000+ `[` anidados, que no es un `ValueError`) y el `ValueError` de
`int()` con más de 4.300 dígitos en el token id de un avatar NFT. Medidos los
cinco en py3.9.24, 3.12.12 y 3.13.6 el 2026-09-25. describe.net lo tapaba con
su propia guarda; cada consumidor nuevo lo heredaba como un 500 que elige el
dueño del nombre.

Un test por clase concreta, y cada una se atrapa por su clase — nunca `except
Exception`, que taparía también un bug del propio SDK (el último test lo ata).

🔴 Los dobles son SINTÉTICOS (la `Cadena` de la ronda 2): contestan por
(contrato, selector) lo que el test les dice y no describen ningún hecho del
mundo. Ningún fixture grabado se toca ni se regraba.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, List, Tuple

import httpx
import pytest

from uvd_describe_sdk.names import _abi, _ens
from uvd_describe_sdk.names._avatar import nft_reference

from .names_replay import call_op
from .test_names_ronda2 import FUTURO, Cadena, R, _ens_extendido, _offchain_lookup

A = "0x" + "11" * 20  # la dirección del nombre (sólo dígitos: EIP-55 no la cambia)
NFT = "0x" + "44" * 20  # un contrato ERC-721
#: El `callback` que pone `_offchain_lookup` de la ronda 2.
CALLBACK = b"\xaa\xbb\xcc\xdd"
#: `json.loads` levanta `RecursionError` con esto (medido en las tres versiones);
#: 200 KB, lejos del tope de 2 MB del motor.
ANIDADO = b"[" * 200_000
REGISTRO_NFT = f"eip155:1/erc721:{NFT}/7"

asincronia = pytest.mark.parametrize("asincrono", [False, True], ids=["sync", "async"])


def _con_gateways(urls: List[str]) -> Cadena:
    """`alguien.eth` con un resolver ENSIP-10 cuyo `resolve()` pide CCIP-Read."""
    cadena = Cadena()
    _ens_extendido(cadena)
    cadena.revierte(R, "resolve(bytes,bytes)", _offchain_lookup(R, urls))
    return cadena


# ---------------------------------------------------------------------------
# Las tres reproducciones de la revisión: un gateway CCIP malformado
# ---------------------------------------------------------------------------


@asincronia
@pytest.mark.parametrize(
    ("gateway", "motivo"),
    [
        # `urlsplit`: corchete sin cerrar → ValueError («Invalid IPv6 URL»). Mutación DA.
        pytest.param("https://[x/{data}", "does not parse", id="urlsplit-corchete-abierto"),
        # `urlsplit`: host entre corchetes que no es una IP → ValueError. Mutación DA.
        pytest.param("https://[zzz]/{data}", "does not parse", id="urlsplit-host-no-IP"),
        # `urlsplit` lo acepta y httpx no: carácter de control → InvalidURL. Mutación DB.
        pytest.param("https://gw.example/\x01{data}", "InvalidURL", id="httpx-InvalidURL"),
    ],
)
def test_un_gateway_CCIP_malformado_es_rpc_unavailable_y_no_una_excepcion(
    gateway: str, motivo: str, asincrono: bool
) -> None:
    cadena = _con_gateways([gateway])
    resolver = cadena.resolver()
    result = call_op(resolver, "resolve", ["alguien.eth"], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert result.address is None and result.verified_onchain is False
    assert result.tried == ("ens",)
    assert motivo in (result.detail or "")
    assert cadena.pedidas_http == [], "una URL que no parsea no se pide"
    assert resolver.cache is not None
    assert len(resolver.cache) == 0, "«no pude preguntar» no se cachea"


def test_un_gateway_malformado_se_saltea_y_el_siguiente_contesta() -> None:
    """Se rehúsa como una URL prohibida (http://, IP privada): se pasa a la
    siguiente, no se corta la búsqueda. La respuesta vuelve on-chain por el
    callback, como cualquier CCIP-Read."""
    bueno = "https://gw.example/{data}"
    cadena = _con_gateways(["https://[x/{data}", bueno])
    url_buena = bueno.replace("{data}", "0x01")
    cadena.http[url_buena] = httpx.Response(200, json={"data": "0x" + b"firmado".hex()})
    respuesta = _abi.encode(["bytes"], [_abi.encode(["address"], [A])])
    cadena.rutas[(R.lower(), CALLBACK)] = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": "0x" + respuesta.hex(),
    }
    with cadena.resolver() as resolver:
        result = resolver.resolve_sync("alguien.eth")
    assert result.error is None
    assert result.address == A and result.verified_onchain is True
    assert cadena.pedidas_http == [url_buena]


# ---------------------------------------------------------------------------
# Un redirect del gateway a una URL que no parsea
# ---------------------------------------------------------------------------


@asincronia
@pytest.mark.parametrize(
    ("location", "motivo"),
    [
        # httpx lo acepta y `urljoin` no → ValueError. EL que escapaba. Mutación DC.
        pytest.param("https://[x/", "redirected to a URL that does not parse", id="urljoin"),
        # Estos dos httpx ya los rechaza al armar la request siguiente, con un
        # `RemoteProtocolError` que el motor atrapa (medido): quedan como guarda.
        pytest.param("//[zzz]/a", "RemoteProtocolError", id="httpx-host-no-IP"),
        pytest.param("https://gw.example/\x01", "RemoteProtocolError", id="httpx-control"),
    ],
)
def test_un_redirect_a_una_URL_que_no_parsea_es_rpc_unavailable(
    location: str, motivo: str, asincrono: bool
) -> None:
    gateway = "https://gw.example/{data}"
    cadena = _con_gateways([gateway])
    url = gateway.replace("{data}", "0x01")
    cadena.http[url] = httpx.Response(302, headers={"location": location})
    result = call_op(cadena.resolver(), "resolve", ["alguien.eth"], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert motivo in (result.detail or "")
    assert cadena.pedidas_http == [url], "el destino del redirect no se pide"


# ---------------------------------------------------------------------------
# Un cuerpo de gateway que `json.loads` no puede leer
# ---------------------------------------------------------------------------


@asincronia
def test_un_gateway_que_contesta_JSON_anidado_sin_fin_es_rpc_unavailable(asincrono: bool) -> None:
    """`RecursionError` no es un `ValueError`: escapaba del resolver. Mutación DD."""
    gateway = "https://gw.example/{data}"
    cadena = _con_gateways([gateway])
    cadena.http[gateway.replace("{data}", "0x01")] = httpx.Response(200, content=ANIDADO)
    result = call_op(cadena.resolver(), "resolve", ["alguien.eth"], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert "without hex `data`" in (result.detail or "")


# ---------------------------------------------------------------------------
# El avatar: la metadata del NFT y el registro mismo
# ---------------------------------------------------------------------------


def _con_avatar(registro: str) -> Cadena:
    """`alguien.eth` → A, con resolver propio (no ENSIP-10) y `avatar` = `registro`."""
    cadena = Cadena()
    cadena.ok(_ens.ETH_REGISTRAR, "nameExpires(uint256)", ["uint256"], [FUTURO])
    cadena.ok(_ens.REGISTRY, "resolver(bytes32)", ["address"], [R])
    cadena.ok(R, "supportsInterface(bytes4)", ["bool"], [False])
    cadena.ok(R, "text(bytes32,string)", ["string"], [registro])
    cadena.ok(R, "addr(bytes32)", ["address"], [A])
    return cadena


def _nft_de_A(cadena: Cadena, token_uri: str) -> None:
    cadena.ok(NFT, "ownerOf(uint256)", ["address"], [A])
    cadena.ok(NFT, "tokenURI(uint256)", ["string"], [token_uri])


@asincronia
def test_una_metadata_de_NFT_en_una_URL_que_no_parsea_es_rpc_unavailable(
    asincrono: bool,
) -> None:
    """El `tokenURI` lo escribe quien desplegó el contrato: pasa por `check_url`
    como un gateway (mutación DA, el otro sitio que la llama)."""
    cadena = _con_avatar(REGISTRO_NFT)
    _nft_de_A(cadena, "https://[x/1.json")
    result = call_op(cadena.resolver(), "avatar", ["alguien.eth"], asynchronous=asincrono)
    assert result.error == "rpc_unavailable"
    assert result.value is None and result.raw_value == REGISTRO_NFT
    assert "does not parse" in (result.detail or "")
    assert cadena.pedidas_http == []


def test_una_metadata_https_con_JSON_anidado_sin_fin_no_da_URL() -> None:
    """Igual que una metadata que no es JSON: sin URL, con el motivo. Mutación DE."""
    cadena = _con_avatar(REGISTRO_NFT)
    _nft_de_A(cadena, "https://meta.example/1.json")
    cadena.http["https://meta.example/1.json"] = httpx.Response(200, content=ANIDADO)
    with cadena.resolver() as resolver:
        result = resolver.avatar_sync("alguien.eth")
    assert result.error is None and result.value is None
    assert result.detail == "the NFT metadata is not JSON"
    assert result.raw_value == REGISTRO_NFT


def test_una_metadata_data_URI_con_JSON_anidado_sin_fin_no_da_URL() -> None:
    """La misma lectura, en la rama `data:` (sin red). Mutación DF."""
    cadena = _con_avatar(REGISTRO_NFT)
    _nft_de_A(cadena, "data:application/json," + "[" * 200_000)
    with cadena.resolver() as resolver:
        result = resolver.avatar_sync("alguien.eth")
    assert result.error is None and result.value is None
    assert result.detail == "the NFT metadata (a data: URI) is not JSON"
    assert cadena.pedidas_http == []


def test_un_avatar_NFT_con_un_token_id_de_5000_digitos_no_da_URL_ni_excepcion() -> None:
    """`int()` rehúsa más de 4.300 dígitos con `ValueError`; el registro lo
    escribe el dueño del nombre. Un token id que no es un uint256 no es una
    referencia ENSIP-12, y ni se pregunta al contrato. Mutación DG."""
    registro = f"eip155:1/erc721:{NFT}/" + "9" * 5000
    cadena = _con_avatar(registro)
    with cadena.resolver() as resolver:
        result = resolver.avatar_sync("alguien.eth")
    assert result.error is None and result.value is None
    assert result.detail == "the avatar record is not an ENSIP-12 URI"
    assert result.raw_value == registro
    assert all(to != NFT.lower() for to, _ in cadena.eth_calls)


def test_la_referencia_NFT_acepta_hasta_el_uint256_maximo_y_ni_uno_mas() -> None:
    maximo = (1 << 256) - 1
    assert nft_reference(f"eip155:1/erc721:{NFT}/{maximo}") == ("eip155:1", "erc721", NFT, maximo)
    assert nft_reference(f"eip155:1/erc721:{NFT}/{maximo + 1}") is None
    # Los ceros a la izquierda no cuentan como dígitos: es el 7.
    cero_7 = f"eip155:1/erc721:{NFT}/" + "0" * 5000 + "7"
    assert nft_reference(cero_7) == ("eip155:1", "erc721", NFT, 7)
    assert nft_reference(f"eip155:{'1' * 5000}/erc721:{NFT}/7") is None


# ---------------------------------------------------------------------------
# Lo que el encargo prohíbe: un `except Exception` que tape errores del SDK
# ---------------------------------------------------------------------------

NAMES = Path(__file__).resolve().parents[1] / "src" / "uvd_describe_sdk" / "names"

#: El único que atrapa todo, y no tapa nada: guarda la excepción del hilo
#: propio para re-levantarla en el hilo de quien llamó (`_proto.run_blocking`).
PERMITIDOS = {("_proto.py", "worker")}


class _AtrapaTodo(ast.NodeVisitor):
    """Anota (archivo, función más interna) de cada `except:`, `except
    Exception` o `except BaseException`, también dentro de una tupla."""

    def __init__(self, archivo: str) -> None:
        self.archivo = archivo
        self.funciones = ["<módulo>"]
        self.encontrados: List[Tuple[str, str]] = []

    def _funcion(self, nodo: Any) -> None:
        self.funciones.append(nodo.name)
        self.generic_visit(nodo)
        self.funciones.pop()

    def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:
        self._funcion(nodo)

    def visit_AsyncFunctionDef(self, nodo: ast.AsyncFunctionDef) -> None:
        self._funcion(nodo)

    def visit_ExceptHandler(self, nodo: ast.ExceptHandler) -> None:
        tipos = nodo.type.elts if isinstance(nodo.type, ast.Tuple) else [nodo.type]
        amplios = {"Exception", "BaseException"}
        if any(t is None or (isinstance(t, ast.Name) and t.id in amplios) for t in tipos):
            self.encontrados.append((self.archivo, self.funciones[-1]))
        self.generic_visit(nodo)


def test_ningun_modulo_de_names_atrapa_Exception_salvo_el_que_re_levanta() -> None:
    """«Nunca un `except Exception` que tape errores del propio SDK; se atrapan
    las clases concretas.» Mutación DJ."""
    encontrados: List[Tuple[str, str]] = []
    for archivo in sorted(NAMES.glob("*.py")):
        buscador = _AtrapaTodo(archivo.name)
        buscador.visit(ast.parse(archivo.read_text(encoding="utf-8")))
        encontrados.extend(buscador.encontrados)
    assert set(encontrados) == PERMITIDOS, encontrados
