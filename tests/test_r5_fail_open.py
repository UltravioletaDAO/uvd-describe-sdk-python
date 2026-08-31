"""R5 — el fail-open: qué tapa, qué NO tapa, y por qué la línea está donde está.

Lo que Saul pidió (2026-08-28, literal): *«pon un fallback si es que describe
está caído»*.

Lo que este archivo defiende es la mitad que NO pidió y que sin ella el fallback
miente: que ese `None` **nunca salga callado**. Un fail-open silencioso convierte
«describe está caído» en «esta wallet no tiene reputación» — la misma confusión
que R1 existe para impedir, ahora fabricada por el mecanismo que la iba a evitar.

════════════════════════════════════════════════════════════════════════════
R5 CORREGIDA — 2026-08-30. LA LÍNEA ES SI HUBO DINERO DE POR MEDIO
════════════════════════════════════════════════════════════════════════════
    GRATIS  wallet() · leaderboard() · health()  → `None` observado. Nunca `[]`.
    PAGAS   wallet_breakdown() · agent()         → LEVANTAN, incluso con
                                                    `fail_open=True` explícito.

⚠️ **Hasta hoy este archivo afirmaba lo contrario y se deja escrito**: tenía un
`test_leaderboard_y_health_no_son_nullables` que exigía que las dos gratis
levantaran, siguiendo la tabla de tipos del contrato v0.1 «al pie de la letra»,
y su docstring decía que *«una lista vacía devuelta por un fallo se lee como el
índice está vacío»*. Esa observación era CORRECTA y sobrevivió: por eso la regla
corregida dice explícitamente **nunca `[]`** — `None` no es una lista vacía. Lo
que se cayó fue la conclusión: el criterio no era «operativa vs. de perfil» sino
si hubo un pago de por medio. El gemelo TypeScript leyó la otra mitad del mismo
contrato y terminó haciendo fail-open en las rutas PAGAS, devolviendo `null`
tras un timeout posterior al settlement — plata gastada sin que el llamador se
entere. Los dos SDK implementan ahora la regla de arriba, igual.

════════════════════════════════════════════════════════════════════════════
🔴 VERIFICACIÓN DISCRIMINANTE — los dos ejes
════════════════════════════════════════════════════════════════════════════
Un test que sólo afirme `rep is None` estaría verde igual con el bug: también
daría `None` si el SDK devolviera `None` para una wallet sin calificar. Por eso
cada test de acá afirma **las dos mitades** —que hubo `None` *y* que hubo
observación— y el de contraste exige que el caso legítimo devuelva un OBJETO con
**cero** observaciones.

Y la regla corregida tiene dos bordes, así que hay un test por borde:

  * `test_las_rutas_pagas_no_hacen_fail_open_ni_con_fail_open_true` se pone rojo
    si alguien mete `wallet_breakdown()` / `agent()` adentro del fail-open.
  * `test_las_dos_gratis_hacen_fail_open_y_lo_reportan` se pone rojo si alguien
    las saca a `leaderboard()` / `health()`.

Los dos se verificaron inyectando su bug (2026-08-30). Las salidas están en el
reporte de esa sesión.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

import httpx
import pytest

from uvd_describe_sdk import (
    DescribeClient,
    DescribeError,
    DescribeHTTPError,
    DescribeTimeout,
)

from .conftest import CHALLENGE_402, WALLET_REGISTRADA_SIN_CALIFICAR, json_response


def _boom_timeout(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("cold start")


def _boom_dns(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("dns")


def _boom_500(_request: httpx.Request) -> httpx.Response:
    return json_response({"detail": "boom"}, status=500)


class _PayerMinimo:
    """Un payer que devuelve un token cualquiera. **No toca una clave.**

    Está acá y no importado de `test_r6` para que este archivo se lea solo: lo
    único que necesita es que el baile del 402 llegue hasta el segundo request,
    que es donde vive el borde que se está probando.
    """

    def create_authorization(self, *_args: Any, **_kwargs: Any) -> str:
        return "BASE64-DE-MENTIRA"


def _paga_y_despues_falla(fallo: Any) -> Any:
    """402 en el primer request; `fallo` en el segundo (el ya pagado)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return fallo(request)

    return handler


# ---------------------------------------------------------------------------
# El fail-open devuelve None Y observa. Las dos mitades, siempre.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,kind",
    [
        (_boom_timeout, "timeout"),
        (_boom_dns, "unreachable"),
        (_boom_500, "http_error"),
    ],
)
def test_fail_open_devuelve_none_y_lo_reporta(make_client, handler, kind) -> None:
    visto: list[DescribeError] = []
    with make_client(handler, fail_open=True, on_error=visto.append) as c:
        rep = c.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    assert rep is None
    # La mitad que hace la diferencia: alguien se enteró, y sabe de QUÉ tipo fue.
    assert len(visto) == 1
    assert visto[0].kind == kind
    assert isinstance(visto[0], DescribeError)
    # `wallet()` es GRATIS: no se firmó nada, así que la excepción tragada no
    # puede venir marcada como posterior a un pago.
    assert visto[0].payment_sent is False


def test_sin_on_error_igual_loguea_en_warning(make_client, caplog) -> None:
    """No existe el modo silencioso. El default no es «no hacer nada»: es loguear.

    Sin esto, un consumidor que no pase `on_error` tendría un fail-open mudo — y
    los logs son donde se investiga el incidente.
    """
    with caplog.at_level(logging.WARNING, logger="uvd_describe_sdk"):
        with make_client(_boom_500, fail_open=True) as c:
            rep = c.wallet("0xdead")

    assert rep is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    mensaje = " ".join(r.getMessage() for r in caplog.records)
    # El mensaje tiene que decir explícitamente que None no es «sin reputación»:
    # es lo que va a leer el que abra el log a las 3 AM.
    assert "no reputation" in mensaje


def test_el_caso_legitimo_no_produce_ninguna_observacion(make_client) -> None:
    """🔴 EL TEST DISCRIMINANTE.

    Montamos el estado BUENO —una wallet real, registrada, sin calificaciones— y
    exigimos lo contrario en las dos mitades: **objeto**, no `None`; y **cero**
    observaciones. Si alguien implementara «sin ratings ⇒ devolvé None», los
    tests de arriba seguirían verdes y este se pondría rojo. Es la única razón
    por la que este archivo prueba algo.
    """
    visto: list[DescribeError] = []
    with make_client(
        lambda _r: json_response(WALLET_REGISTRADA_SIN_CALIFICAR),
        fail_open=True,
        on_error=visto.append,
    ) as c:
        rep = c.wallet("0x00000000000000000000000000000000000000aa")

    assert rep is not None
    assert rep.global_score is None
    assert visto == []


def test_fail_open_false_levanta_en_vez_de_tragar(make_client) -> None:
    visto: list[DescribeError] = []
    with make_client(_boom_timeout, fail_open=False, on_error=visto.append) as c:
        with pytest.raises(DescribeTimeout):
            c.wallet("0xdead")
    # Y si levanta, NO se observa: observar sería reportar dos veces el mismo
    # hecho a quien ya lo va a ver en su `except`.
    assert visto == []


def test_un_on_error_roto_no_tumba_al_llamador(make_client, caplog) -> None:
    """El observador es de quien llama y puede explotar. Que su bug convierta
    una degradación prevista en un crash sería peor que no tener observador."""

    def observador_roto(_exc: DescribeError) -> None:
        raise RuntimeError("el logger del consumidor se rompió")

    with caplog.at_level(logging.ERROR, logger="uvd_describe_sdk"):
        with make_client(_boom_500, fail_open=True, on_error=observador_roto) as c:
            rep = c.wallet("0xdead")

    assert rep is None
    assert any("on_error" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 🔴 BORDE 1 — las GRATIS sí. Rojo si alguien saca a leaderboard/health.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metodo", ["leaderboard", "health"])
@pytest.mark.parametrize(
    "handler,kind",
    [
        (_boom_timeout, "timeout"),
        (_boom_dns, "unreachable"),
        (_boom_500, "http_error"),
    ],
)
def test_las_dos_gratis_hacen_fail_open_y_lo_reportan(
    make_client, metodo, handler, kind
) -> None:
    """🔴 DISCRIMINANTE del borde gratis.

    `leaderboard()` y `health()` son **gratis**, y un fallo ruidoso ahí obliga a
    cada consumidor a escribir su propio `try/except` para algo que el SDK ya
    sabe hacer — que es justo la duplicación que este SDK viene a borrar. Se
    degradan igual que `wallet()`: `None`, y siempre observado.

    Si alguien las devuelve a levantar, este test se pone rojo con
    `DID NOT RETURN None` (levanta la excepción y ni llega al assert).
    """
    visto: list[DescribeError] = []
    with make_client(handler, fail_open=True, on_error=visto.append) as c:
        resultado = getattr(c, metodo)()

    assert resultado is None
    assert len(visto) == 1 and visto[0].kind == kind
    # Una ruta GRATIS jamás marca un pago: no se firmó nada.
    assert visto[0].payment_sent is False and visto[0].payment is None


def test_un_fallo_de_leaderboard_NUNCA_devuelve_una_lista_vacia(make_client) -> None:
    """🔴 La mitad del razonamiento viejo que SOBREVIVIÓ, ahora como test.

    Antes este archivo usaba «una lista vacía se lee como el índice está vacío»
    para justificar que `leaderboard()` levantara. La observación era correcta y
    el contrato corregido la atendió por el otro lado: se degrada, pero a `None`
    y **nunca** a `[]`. `[]` afirma que el índice está vacío —una afirmación
    falsa sobre el mundo—; `None` dice «no pude preguntar».

    Este test se pone rojo con un `return []` en el `except`, que es la forma
    más natural de escribir mal este fail-open.
    """
    with make_client(_boom_500, fail_open=True) as c:
        filas = c.leaderboard()

    assert filas is None
    assert filas != [], "un fallo NUNCA puede volver como lista vacía"


def test_las_gratis_con_fail_open_apagado_siguen_levantando(make_client) -> None:
    """El escape hatch sigue existiendo y sigue siendo explícito."""
    with make_client(_boom_500, fail_open=False) as c:
        with pytest.raises(DescribeHTTPError):
            c.leaderboard()
        with pytest.raises(DescribeHTTPError):
            c.health()


# ---------------------------------------------------------------------------
# 🔴 BORDE 2 — las PAGAS no. Rojo si alguien las mete en el fail-open.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fallo",
    [_boom_timeout, _boom_dns, _boom_500],
    ids=["timeout", "unreachable", "http_error"],
)
def test_las_rutas_pagas_no_hacen_fail_open_ni_con_fail_open_true(
    make_client, fallo
) -> None:
    """🔴 EL TEST QUE PROTEGE LA PLATA. Discriminante del borde pago.

    El fallo ocurre en el **segundo** request: el 402 ya se contestó, el sobre
    ya se firmó y se despachó. El USDC pudo haberse movido.

    Y `fail_open=True` está pasado EXPLÍCITAMENTE, que es el punto: no es una
    preferencia del llamador sino una propiedad del método. Un flag de
    disponibilidad no puede comprar el derecho a tragar un recibo. Si alguien
    mete estas dos rutas en el fail-open —«por simetría con las gratis»— este
    test se pone rojo con `DID NOT RAISE`.

    El contraste con `test_las_dos_gratis_...` es lo que hace que los dos
    prueben algo: el mismo transporte roto, el mismo `fail_open=True`, y
    resultados deliberadamente opuestos.
    """
    visto: list[DescribeError] = []
    with make_client(
        _paga_y_despues_falla(fallo),
        fail_open=True,
        payer=_PayerMinimo(),
        on_error=visto.append,
    ) as c:
        with pytest.raises(DescribeError) as exc:
            c.wallet_breakdown("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
        with pytest.raises(DescribeError):
            c.agent("base", "9001")

    # Y no se observó: observar sería reportar dos veces el mismo hecho a quien
    # ya lo va a ver en su `except`. Lo que se le debe acá es la EXCEPCIÓN.
    assert visto == []
    # Y la excepción dice que la credencial ya había salido — sin eso, quien la
    # reciba no sabe si le toca reconciliar.
    assert exc.value.payment_sent is True


def test_la_tabla_de_nullabilidad_del_contrato_esta_en_las_firmas(make_client) -> None:
    """La regla corregida, leída de las anotaciones y no de la prosa.

    Es el complemento barato de los dos discriminantes de arriba: aquellos
    prueban el COMPORTAMIENTO, este prueba que la firma publicada no mienta
    sobre él. Un `-> Optional[Breakdown]` sería la primera línea del bug.
    """
    nullable = {"wallet", "leaderboard", "health"}
    nunca_nullable = {"wallet_breakdown", "agent"}

    for nombre in nullable:
        anotacion = str(inspect.signature(getattr(DescribeClient, nombre)).return_annotation)
        assert anotacion.startswith("Optional["), f"{nombre} -> {anotacion}"

    for nombre in nunca_nullable:
        anotacion = str(inspect.signature(getattr(DescribeClient, nombre)).return_annotation)
        assert "Optional" not in anotacion and "None" not in anotacion, (
            f"{nombre} -> {anotacion}: una ruta PAGA nullable es una credencial "
            "gastada sin recibo. Ver la cabecera de client.py."
        )


def test_badge_url_no_toca_la_red_ni_con_el_indice_caido(make_client) -> None:
    """El badge es string puro: sigue funcionando con todo roto.

    Es la superficie que Saul puso primero (el copy-paste tipo like button) y no
    puede depender de que el proceso que la genera alcance la API.
    """
    with make_client(_boom_500) as c:
        url = c.badge_url("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
    assert url.endswith("/badge/0x97cd97cfe21799bacbf39d0a53469e5f82f30996.svg")
