"""Las piezas sin red del resolver: detección, guard de URLs, ABI, avatar, Avvy.

Ninguna de estas necesita una grabación: son funciones puras. Las que dependen
de un hecho de la cadena (el hash de `avax`) se atan a la grabación que lo leyó.
"""

from __future__ import annotations

import pytest

from uvd_describe_sdk.names import UNS_TLDS_MEASURED_AT, NameResolver, _abi, _avvy
from uvd_describe_sdk.names._avatar import nft_reference, plain_url
from uvd_describe_sdk.names._hash import is_hex_address, namehash, to_checksum_address
from uvd_describe_sdk.names._normalize import UNS_L2_BY_TLD
from uvd_describe_sdk.names._proto import BASE, POLYGON, Unavailable, check_url

from .names_replay import load

# ---------------------------------------------------------------------------
# Detección por sufijo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "system"),
    [
        ("jesse.base.eth", "basenames"),  # antes que `.eth`: es sufijo suyo
        ("base.eth", "ens"),
        ("sub.ultravioletadao.eth", "ens"),
        ("gregskril.com", "ens-dns"),
        ("brad.crypto", "unstoppable"),
        ("x.chomp", "unstoppable"),  # TLD de UNS registrado en Base
        ("bonfida.sol", "sns"),
        ("miniholder.avax", "avvy"),
        ("vitalik", None),
        ("0xe4dc963c56979E0260fc146b87eE24F18220e545", None),
    ],
)
def test_la_deteccion_por_sufijo(name: str, system: object) -> None:
    assert NameResolver(rpc={}).detect(name) == system


def test_la_tabla_de_uns_tiene_fecha_y_cada_tld_su_L2() -> None:
    assert UNS_TLDS_MEASURED_AT == "2026-09-24"
    assert UNS_L2_BY_TLD["crypto"] == POLYGON
    assert UNS_L2_BY_TLD["chomp"] == BASE
    # Los de Solana/Sonic se DETECTAN pero no tienen lector EVM: no son una L2.
    assert "wif" not in UNS_L2_BY_TLD


# ---------------------------------------------------------------------------
# El guard de URLs (CCIP-Read y metadata de NFT)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.coinbase.com/x",  # sin TLS
        "https://localhost/x",
        "https://metadata.localhost/x",
        "https://127.0.0.1/x",
        "https://10.0.0.8/x",
        "https://169.254.169.254/latest/meta-data/",  # metadata de instancia
        "https://[::1]/x",
        "https://2130706433/x",  # 127.0.0.1 disfrazado de número
        "https://127.1/x",
        "https://user:pass@example.com/x",
        "ftp://example.com/x",
    ],
)
def test_el_guard_rechaza_lo_que_un_resolver_malicioso_podria_apuntar(url: str) -> None:
    with pytest.raises(Unavailable):
        check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.coinbase.com/api/v1/domain/resolver/resolveDomain/0xabc/0x12",
        "https://dnssec-oracle.ens.domains/",
        "https://8.8.8.8/x",
    ],
)
def test_el_guard_deja_pasar_gateways_publicos(url: str) -> None:
    check_url(url)


# ---------------------------------------------------------------------------
# ABI: lo que viene de un RPC o de un gateway no puede hacer leer fuera del buffer
# ---------------------------------------------------------------------------


def test_abi_ida_y_vuelta() -> None:
    datos = _abi.encode(
        ["string[]", "uint256", "bytes", "address"],
        [["crypto.ETH.address", "x"], 7, b"\x01\x02", "0x" + "ab" * 20],
    )
    assert _abi.decode(["string[]", "uint256", "bytes", "address"], datos) == (
        ["crypto.ETH.address", "x"],
        7,
        b"\x01\x02",
        "0x" + "ab" * 20,
    )


@pytest.mark.parametrize(
    "datos",
    [
        b"",
        b"\x00" * 31,
        (32).to_bytes(32, "big") + (10**9).to_bytes(32, "big"),  # largo absurdo
        (10**9).to_bytes(32, "big"),  # offset fuera del buffer
    ],
)
def test_abi_mal_formado_es_AbiError_y_no_otra_cosa(datos: bytes) -> None:
    with pytest.raises(_abi.AbiError):
        _abi.decode(["string"], datos)


def test_una_direccion_con_bits_altos_sucios_no_se_acepta() -> None:
    with pytest.raises(_abi.AbiError):
        _abi.decode(["address"], b"\x01" + b"\x00" * 31)


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------


def test_namehash_de_los_vectores_de_EIP137() -> None:
    assert namehash("") == b"\x00" * 32
    assert (
        namehash("eth").hex() == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    )
    assert (
        namehash("foo.eth").hex()
        == "de9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f"
    )


def test_checksum_eip55() -> None:
    assert to_checksum_address("0xe4dc963c56979e0260fc146b87ee24f18220e545") == (
        "0xe4dc963c56979E0260fc146b87eE24F18220e545"
    )
    assert not is_hex_address("0x" + "1_" * 20)
    assert not is_hex_address(" 0x" + "1" * 40)


# ---------------------------------------------------------------------------
# Avatar ENSIP-12
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("record", "url"),
    [
        ("https://euc.li/vitalik.eth", "https://euc.li/vitalik.eth"),
        ("ipfs://QmX/1.png", "https://ipfs.io/ipfs/QmX/1.png"),
        ("ipfs://ipfs/QmX", "https://ipfs.io/ipfs/QmX"),
        ("ipns://app.eth", "https://ipfs.io/ipns/app.eth"),
        ("ar://abc", "https://arweave.net/abc"),
        ("data:image/png;base64,AA==", "data:image/png;base64,AA=="),
        ("eip155:1/erc721:0x31385d3520bced94f77aae104b406994d8f2168c/9421", None),
        ("javascript:alert(1)", None),
    ],
)
def test_avatar_uri_a_url(record: str, url: object) -> None:
    assert plain_url(record, "https://ipfs.io") == url


def test_la_referencia_nft_grabada_de_matoken_se_entiende() -> None:
    """El registro REAL de `matoken.eth` (leído el 2026-09-24)."""
    assert nft_reference("eip155:1/erc721:0x31385d3520bced94f77aae104b406994d8f2168c/9421") == (
        "eip155:1",
        "erc721",
        "0x31385d3520bced94f77aae104b406994d8f2168c",
        9421,
    )
    assert nft_reference("eip155:1/erc20:0x31385d3520bced94f77aae104b406994d8f2168c/1") is None


# ---------------------------------------------------------------------------
# Avvy: el hash de `avax` pre-cacheado es el que da el contrato
# ---------------------------------------------------------------------------


def test_el_hash_precacheado_de_avax_es_el_del_contrato_poseidon() -> None:
    """Los dos clientes oficiales pre-cachean este número; se leyó del contrato
    `Poseidon` en Avalanche el 2026-09-24 (`poseidon_tld_avax.json`)."""
    fixture = load("poseidon_tld_avax")
    assert int(fixture["result"]["poseidon"]) == _avvy.AVAX_TLD_HASH
    grabada = fixture["exchanges"][0]
    assert grabada["to"].lower() == _avvy.POSEIDON.lower()


def test_la_decodificacion_de_senales_de_avvy_es_la_inversa_del_empaque() -> None:
    for label in ("miniholder", "a" * 31, "b" * 40, "x-1"):
        entradas = _avvy._label_inputs(label)
        assert _avvy._decode_signals(entradas) == label
