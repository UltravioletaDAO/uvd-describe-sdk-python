"""① JITTER — dispersar el rebaño antes de cada GET. Aporte de KarmaKadabra.

Medición de **KarmaKadabra** (`#agents`, **2026-08-30**), textual:

    «27 agentes despiertan al MISMO tiempo por EventBridge y pegan simultáneo
    contra su límite de rps COMPARTIDO con los otros consumidores. Sin jitter,
    un enjambre es un DDoS educado.»

Su código es `random.uniform(0, 0.4)` antes de cada lectura
(`karmakadabra/lib/reputation_scan.py:120-123`). Acá se absorbe con ese mismo
0,4 y **prendido por default**: el trade-off completo está en la cabecera de
`client.py`, y el que lo rompe el empate es quién paga el costo de equivocarse
— el del default apagado lo pagan MeshRelay y Execution Market, que comparten
el límite y no eligieron nada.

════════════════════════════════════════════════════════════════════════════
🔴 QUÉ HACE DISCRIMINANTES A ESTOS TESTS
════════════════════════════════════════════════════════════════════════════
Un test que sólo afirmara «durmió» estaría verde con las tres versiones malas
del feature: la que duerme de más (en el tramo pagado), la que duerme siempre lo
mismo (RNG global sembrado) y la que no se puede apagar. Por eso cada test acá
mide **cuántas veces** durmió y **cuánto**, y hay uno por cada una de esas tres.

El seam es `client.time`, reemplazado entero por un doble que ANOTA en vez de
dormir: la suite no se puede permitir dormir de verdad, y menos 0,2 s por GET.
"""

from __future__ import annotations

import inspect
import random
from typing import Any, List

import httpx
import pytest

from uvd_describe_sdk import DEFAULT_JITTER_S, DescribeClient
from uvd_describe_sdk import client as client_mod

from .conftest import CHALLENGE_402, HEALTH, WALLET_CON_REPUTACION, json_response


class _RelojFalso:
    """Un doble de `time` que anota los sueños en vez de dormirlos."""

    def __init__(self) -> None:
        self.dormido: List[float] = []

    def sleep(self, segundos: float) -> None:
        self.dormido.append(segundos)


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> _RelojFalso:
    falso = _RelojFalso()
    monkeypatch.setattr(client_mod, "time", falso)
    return falso


class _PayerMinimo:
    """Devuelve un token cualquiera. **No toca una clave.**"""

    def create_authorization(self, *_args: Any, **_kwargs: Any) -> str:
        return "BASE64-DE-MENTIRA"


# ---------------------------------------------------------------------------
# El default: prendido, y en el rango que KK midió
# ---------------------------------------------------------------------------


def test_el_default_es_04_y_viene_prendido() -> None:
    """El contrato con el gemelo TypeScript, leído de la firma y no de la prosa.

    Los dos SDK tienen que dispersar lo MISMO: si uno viene apagado, la flota
    que use ese lenguaje sigue siendo el enjambre que el aporte vino a arreglar,
    y el bug reaparece a mitad del ecosistema. 0,4 s es el valor que KK mide y
    corre en producción con 27 agentes — se hereda, no se reinventa.
    """
    assert DEFAULT_JITTER_S == 0.4
    firma = inspect.signature(DescribeClient.__init__)
    assert firma.parameters["jitter"].default == DEFAULT_JITTER_S


def test_duerme_antes_de_cada_get_y_dentro_del_rango(make_client, reloj) -> None:
    with make_client(lambda _r: json_response(WALLET_CON_REPUTACION)) as c:
        c.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
        c.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
        c.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert len(reloj.dormido) == 3, "una dispersión por request, ni más ni menos"
    assert all(0.0 < s <= DEFAULT_JITTER_S for s in reloj.dormido)


def test_las_tres_rutas_gratis_dispersan(make_client, reloj) -> None:
    """No sólo `wallet()`: el enjambre de KK pega contra el índice entero."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return json_response(HEALTH)
        if request.url.path == "/leaderboard":
            return json_response([])
        return json_response(WALLET_CON_REPUTACION)

    with make_client(handler) as c:
        c.wallet("0xdead")
        c.leaderboard()
        c.health()

    assert len(reloj.dormido) == 3


# ---------------------------------------------------------------------------
# 🔴 DISCRIMINANTE 1 — el opt-out existe y es de verdad
# ---------------------------------------------------------------------------


def test_jitter_cero_no_duerme_ni_una_vez(make_client, reloj) -> None:
    """🔴 Rojo si el jitter no se puede apagar.

    Es la mitad que hace legítimo el default prendido: un default sólo es
    defendible si salirse cuesta una línea explícita. Sin este test, «prendido
    por default» podría degenerar en «prendido y punto», que es la sorpresa que
    el argumento en contra nombraba.
    """
    with make_client(lambda _r: json_response(WALLET_CON_REPUTACION), jitter=0) as c:
        c.wallet("0xdead")
        c.wallet("0xdead")

    assert reloj.dormido == []


# ---------------------------------------------------------------------------
# 🔴 DISCRIMINANTE 2 — el tramo POSTERIOR A LA FIRMA no lleva jitter
# ---------------------------------------------------------------------------


def test_el_tramo_posterior_a_la_firma_no_lleva_jitter(make_client, reloj) -> None:
    """🔴 EL QUE PROTEGE LA VENTANA DE SETTLEMENT.

    El baile del 402 son DOS requests. La dispersión le corresponde sólo a la
    primera: para cuando la segunda sale, el rebaño ya se dispersó y lo único
    que agregaría dormir es quemar ventana de settlement
    (`maxTimeoutSeconds: 120` en el challenge) con la autorización EIP-3009 ya
    firmada en la mano. Jitter y backoff no son lo mismo, y este test es dónde
    esa distinción deja de ser prosa.

    Se pone rojo con `2 == 1` en cuanto alguien saque el `disperse=False` del
    segundo `_request`, que es la forma más natural de escribir mal esto
    («total, es la misma función»).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response({"wallet": "0xdead", "final_score": 80.0})

    with make_client(handler, payer=_PayerMinimo()) as c:
        c.wallet_breakdown("0xdead")

    assert len(reloj.dormido) == 1, (
        "el segundo request del 402 va DESPUÉS de firmar: dormir ahí quema "
        "ventana de settlement y no dispersa a nadie"
    )


# ---------------------------------------------------------------------------
# 🔴 DISCRIMINANTE 3 — una flota sembrada igual NO duerme igual
# ---------------------------------------------------------------------------


def test_una_semilla_global_no_pone_a_la_flota_en_lockstep() -> None:
    """🔴 EL BUG SUTIL: dispersión cero con el jitter «funcionando».

    27 procesos corren la misma imagen. Basta con que esa imagen llame a
    `random.seed(0)` —para hacer reproducible cualquier otra cosa— y, si el
    jitter usara el RNG **global**, los 27 dormirían exactamente lo mismo: el
    enjambre vuelve a pegar simultáneo, con el feature instalado y en verde.

    Acá se simulan dos procesos de esa flota sembrando el global idéntico. Con
    `_RNG` propio (sembrado del SO) las dos secuencias difieren. Se pone rojo
    con `random.uniform`, que es exactamente lo que hace KK — es la única cosa
    de su implementación que este SDK cambia, y por eso lleva su test.
    """
    random.seed(0)
    proceso_a = [client_mod._jitter_seconds(0.4) for _ in range(5)]
    random.seed(0)
    proceso_b = [client_mod._jitter_seconds(0.4) for _ in range(5)]

    assert proceso_a != proceso_b, (
        "dos agentes de la misma imagen con la misma semilla global durmieron "
        "lo mismo: eso es dispersión CERO, el bug que el jitter viene a evitar"
    )


def test_la_aleatoriedad_no_es_criptografica() -> None:
    """Es dispersión, no un secreto. `secrets` acá sería cargo cult: más lento
    y sin comprar nada. Se afirma el tipo para que el día que alguien lo
    «endurezca» tenga que borrar esta línea y leer por qué está."""
    assert isinstance(client_mod._RNG, random.Random)


# ---------------------------------------------------------------------------
# El contraste: sin jitter configurado el rango sigue siendo el rango
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("jitter", [0, -1.0])
def test_valores_no_positivos_desactivan_en_vez_de_explotar(jitter: float) -> None:
    """Un `jitter` negativo por error no puede convertirse en un `sleep` que
    levante: se lee como apagado. Es la regla de la casa —basura ⇒ default, no
    excepción— aplicada al único parámetro nuevo."""
    assert client_mod._jitter_seconds(jitter) == 0.0
