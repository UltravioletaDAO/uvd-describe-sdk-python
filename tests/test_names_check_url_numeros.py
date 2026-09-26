"""R5 (ronda 2 del PR 7, P1 [security] por decisión de c0der): `check_url`
rehúsa un host que `getaddrinfo` lee como número, también en HEX, y una IPv6
que lleva adentro una IPv4 que no es global.

Por qué subió a P1: un consumidor que corre en ECS alcanza el endpoint de
credenciales de la tarea en 169.254.170.2, y un gateway CCIP o una metadata de
NFT la elige quien controla un contrato. Medido el 2026-09-26 sobre
`940685ec` (py3.9.24 y 3.13.6): `https://0xa9fea902/`, `https://0x7f000001/`,
`https://127.0.0.0x1/` y `https://0x7f.0x0.0x0.0x1/` PASABAN (la última
etiqueta tiene letras: `x`, `f`), y también `https://[::169.254.170.2]/`
(IPv4-compatible) y `https://[64:ff9b::a9fe:aa02]/` (prefijo NAT64 bien
conocido), que `ipaddress` llama globales.

⚠️ Límite conocido, escrito y NO arreglado acá (decisión de c0der): `check_url`
no resuelve DNS. Un nombre público cuyo DNS contesta una IP privada pasa.
"""

from __future__ import annotations

import pytest

from uvd_describe_sdk.names._proto import Unavailable, check_url

#: Las mismas dos IP en cada forma que `getaddrinfo` o `ipaddress` aceptan:
#: 127.0.0.1 y 169.254.170.2 (credenciales de una tarea de ECS).
FORMAS_PRIVADAS = [
    # hex, entero y por etiqueta — lo que pasaba
    "https://0x7f000001/",
    "https://127.0.0.0x1/",
    "https://0x7f.0x0.0x0.0x1/",
    "https://0xa9fea902/",
    "https://0xA9FEAA02/",
    "https://0xa9.0xfe.0xaa.0x2/",
    # decimal y octal — ya se rehusaban; quedan fijados
    "https://2130706433/",
    "https://2852039170/",
    "https://0177.0000.0000.0001/",
    "https://0251.0376.0251.0002/",
    "https://127.1/",
    # literales
    "https://127.0.0.1/",
    "https://169.254.170.2/",
    # IPv6 que lleva una IPv4 adentro
    "https://[::ffff:127.0.0.1]/",
    "https://[::ffff:7f00:1]/",
    "https://[::ffff:169.254.170.2]/",
    "https://[::ffff:a9fe:aa02]/",
    "https://[::127.0.0.1]/",
    "https://[::169.254.170.2]/",
    "https://[64:ff9b::7f00:1]/",
    "https://[64:ff9b::a9fe:aa02]/",
]


@pytest.mark.parametrize("url", FORMAS_PRIVADAS)
def test_check_url_rehusa_cada_forma_de_una_IP_privada(url: str) -> None:
    """Mutaciones DP (la última etiqueta en hex) y DQ (la IPv4 dentro de una IPv6)."""
    with pytest.raises(Unavailable):
        check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example/",
        "https://api.coinbase.com/api/v1/domain/resolver/resolveDomain/0x01",
        "https://0xproject.example/",  # empieza con 0x, pero la última etiqueta es un TLD
        "https://abc.0xyz/",  # `0xyz` no es hex: `y` y `z`
        "https://8.8.8.8/",
        "https://[2606:4700:4700::1111]/",
        "https://[::ffff:8.8.8.8]/",
    ],
)
def test_check_url_deja_pasar_un_host_publico(url: str) -> None:
    """El otro estado: la guarda no rehúsa lo que es público."""
    check_url(url)
