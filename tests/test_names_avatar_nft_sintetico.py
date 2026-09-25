"""🔴 SINTÉTICO — el avatar NFT de ENSIP-12, sin grabación, y por qué.

La grabación del único avatar NFT encontrado en vivo (`matoken.eth`,
`eip155:1/erc721:0x3138…168c/9421`, con la metadata en IPFS) se DETUVO el
2026-09-24: `ipfs.io` contestó 429 a la primera prueba y `dweb.link` a la
segunda, y la regla de la grabación es parar al primer 429. No hay fixture real
de esta rama.

Lo que se prueba acá no es un hecho de la cadena sino la REGLA: un avatar que
apunta a un NFT sólo da URL si la dirección del nombre lo posee (`ownerOf` para
ERC-721, `balanceOf > 0` para ERC-1155). Cualquiera puede escribir en su avatar
el NFT más caro del mundo. Las respuestas de abajo son sintéticas y se nombran
así; ningún test de hechos medidos usa este doble.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

import httpx
import pytest

from uvd_describe_sdk.names import _abi
from uvd_describe_sdk.names._avatar import NoAvatar, nft_image
from uvd_describe_sdk.names._hash import selector

from .names_replay import answer_chain_id, drive

DUENO = "0x" + "11" * 20
OTRO = "0x" + "22" * 20
CONTRATO = "0x" + "33" * 20
RPC = {"eip155:1": "https://rpc.test/eip155-1"}


def _responde(tabla: Dict[bytes, bytes], http: Dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        chain_id = answer_chain_id(request)
        if chain_id is not None:
            return chain_id
        if str(request.url) in RPC.values():
            data = bytes.fromhex(json.loads(request.content)["params"][0]["data"][2:])
            salida = tabla[data[:4]]
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": "0x" + salida.hex()}
            )
        return httpx.Response(200, json=http[str(request.url)])

    return httpx.MockTransport(handler)


def _correr(record: str, transport: httpx.MockTransport) -> str:
    url: str = drive(nft_image(record, DUENO, "https://ipfs.io"), transport, rpc=RPC, timeout=5)
    return url


def _data_uri(meta: Dict[str, Any]) -> str:
    return "data:application/json;base64," + base64.b64encode(json.dumps(meta).encode()).decode()


def test_erc721_que_el_nombre_NO_posee_no_da_url() -> None:
    tabla = {
        selector("ownerOf(uint256)"): _abi.encode(["address"], [OTRO]),
    }
    with pytest.raises(NoAvatar, match="not owned"):
        _correr(f"eip155:1/erc721:{CONTRATO}/9421", _responde(tabla, {}))


def test_erc721_que_el_nombre_posee_da_la_imagen_resuelta() -> None:
    tabla = {
        selector("ownerOf(uint256)"): _abi.encode(["address"], [DUENO]),
        selector("tokenURI(uint256)"): _abi.encode(
            ["string"], [_data_uri({"image": "ipfs://QmImagen/9421.png"})]
        ),
    }
    url = _correr(f"eip155:1/erc721:{CONTRATO}/9421", _responde(tabla, {}))
    assert url == "https://ipfs.io/ipfs/QmImagen/9421.png"


def test_erc1155_sin_saldo_no_da_url() -> None:
    tabla = {selector("balanceOf(address,uint256)"): _abi.encode(["uint256"], [0])}
    with pytest.raises(NoAvatar, match="not owned"):
        _correr(f"eip155:1/erc1155:{CONTRATO}/10063", _responde(tabla, {}))


def test_erc1155_sustituye_el_id_en_hex_de_64_y_lee_la_metadata_https() -> None:
    tabla = {
        selector("balanceOf(address,uint256)"): _abi.encode(["uint256"], [1]),
        selector("uri(uint256)"): _abi.encode(["string"], ["https://meta.example/{id}.json"]),
    }
    esperado = "https://meta.example/" + format(10063, "064x") + ".json"
    url = _correr(
        f"eip155:1/erc1155:{CONTRATO}/10063",
        _responde(tabla, {esperado: {"image": "ar://tx-de-la-imagen"}}),
    )
    assert url == "https://arweave.net/tx-de-la-imagen"


def test_una_metadata_en_una_ip_privada_no_se_pide() -> None:
    tabla = {
        selector("ownerOf(uint256)"): _abi.encode(["address"], [DUENO]),
        selector("tokenURI(uint256)"): _abi.encode(["string"], ["https://169.254.169.254/meta"]),
    }
    from uvd_describe_sdk.names._proto import Unavailable

    with pytest.raises(Unavailable, match="IP literal"):
        _correr(f"eip155:1/erc721:{CONTRATO}/1", _responde(tabla, {}))
