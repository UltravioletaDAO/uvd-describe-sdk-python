"""El respaldo contesta cuando el índice NO PUDO, y jamás cuando el índice DIJO.

POR QUÉ EXISTE ESTE ENGANCHE. `fail_open=True` devuelve `None` cuando el índice no
contesta, y ese `None` es honesto: dice *"no pude preguntar"*. Pero para un consumidor
cuyo **gate** depende de la reputación —a quién le compro, a quién le asigno trabajo— ese
`None` y *"no tiene reputación"* terminan en la misma rama del código, y **un gate sin
datos no se abstiene: aprueba a cualquiera**. Ese consumidor necesita una SEGUNDA fuente,
no un valor nulo mejor explicado.

Lo aportó KarmaKadabra, que ya operaba así en producción: con el índice caído lee ERC-8004
directo de su facilitador sobre las 9 EVM de escrow. El SDK no trae esa fuente —sería una
dependencia que la mayoría no quiere— sino el enganche para conectar la que cada uno tenga.

LA LÍNEA QUE NO SE CRUZA, y es la mitad importante de este archivo: **un 404 no dispara el
respaldo.** Un 404 es una RESPUESTA ("no tengo ese sujeto"), no un fallo. Consultar una
segunda fuente para contradecirla convertiría el respaldo en una forma de buscar el número
que a uno le gusta más — y eso no es resiliencia, es *shopping* de datos.
"""
from __future__ import annotations

import httpx
import pytest

from uvd_describe_sdk import DescribeClient
from uvd_describe_sdk.models import WalletReputation

_W = "0x914c38fC1AEc27912B90609cA82383630d64B5e4"


def _respaldo(direccion: str) -> WalletReputation:
    return WalletReputation(wallet=direccion, identity_count=1, total_reviews=3,
                            global_score=71.5)


def _cliente(handler, **kw) -> DescribeClient:
    return DescribeClient(transport=httpx.MockTransport(handler), jitter=0.0, **kw)


def test_el_respaldo_contesta_cuando_el_indice_se_cae():
    """El caso que motiva todo: el índice no contesta y el gate igual tiene un dato."""
    c = _cliente(lambda req: httpx.Response(503, json={"error": "down"}),
                 fallback_reader=_respaldo)
    r = c.wallet(_W)
    assert r is not None, "con respaldo conectado, un 503 no puede devolver None"
    assert r.global_score == 71.5


def test_el_respaldo_se_marca_y_no_se_hace_pasar_por_el_indice():
    """Sin la marca, un consumidor publicaría como canónico un número que
    describe.net no firmó — la misma enfermedad que R1 persigue con `None` vs `0`."""
    c = _cliente(lambda req: httpx.Response(503), fallback_reader=_respaldo)
    r = c.wallet(_W)
    assert r is not None and r.source == "fallback"


def test_un_404_NO_dispara_el_respaldo():
    """La línea que no se cruza: un 404 es una respuesta, no un fallo."""
    llamado = []

    def espia(direccion: str):
        llamado.append(direccion)
        return _respaldo(direccion)

    c = _cliente(lambda req: httpx.Response(404, json={"error": "not found"}),
                 fallback_reader=espia)
    assert c.wallet(_W) is None
    assert llamado == [], (
        "el respaldo se consultó ante un 404 — eso es contradecir una respuesta, "
        "no cubrir una caída"
    )


def test_un_respaldo_que_revienta_no_tumba_la_lectura():
    """Un plan B roto no puede dejar al consumidor PEOR que sin plan B."""
    def explota(direccion: str):
        raise RuntimeError("el facilitador tampoco contesta")

    c = _cliente(lambda req: httpx.Response(503), fallback_reader=explota)
    assert c.wallet(_W) is None          # cae al camino de siempre, no propaga


def test_sin_respaldo_el_comportamiento_no_cambia_en_un_byte():
    """Discriminante del contrato: quien no conecta un respaldo no nota que existe."""
    c = _cliente(lambda req: httpx.Response(503))
    assert c.wallet(_W) is None


def test_el_respaldo_tambien_sirve_con_fail_open_apagado():
    """Si el respaldo trajo un dato no hay nada sobre lo que fallar.

    Negarse a devolverlo con `fail_open=False` sería castigar al que SÍ contestó: la
    bandera dice qué hacer cuando no hay respuesta, y acá sí la hay.
    """
    c = _cliente(lambda req: httpx.Response(503),
                 fail_open=False, fallback_reader=_respaldo)
    r = c.wallet(_W)
    assert r is not None and r.global_score == 71.5


def test_con_fail_open_apagado_y_respaldo_vacio_sigue_levantando():
    """Y si el respaldo tampoco tiene nada, la excepción vuelve: no se traga el fallo."""
    from uvd_describe_sdk import DescribeError
    c = _cliente(lambda req: httpx.Response(503),
                 fail_open=False, fallback_reader=lambda d: None)
    with pytest.raises(DescribeError):
        c.wallet(_W)
