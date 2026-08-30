"""R7 (timeout), la atribución por User-Agent, el passthrough y `badge_url`.

Lo que queda del contrato después de R1–R6, más las dos reglas de
`F0-describe-sdk.md` que no tienen número en el contrato núcleo pero sí tienen
medición detrás: passthrough de campos desconocidos y atribución por UA.
"""

from __future__ import annotations

import httpx
import pytest

from uvd_describe_sdk import (
    DEFAULT_PAY_NETWORK,
    DEFAULT_TIMEOUT_S,
    DescribeClient,
    __version__,
    badge_img_tag,
    badge_url,
    default_user_agent,
)

from .conftest import (
    HEALTH,
    LEADERBOARD,
    WALLET_CON_REPUTACION,
    json_response,
)

# ---------------------------------------------------------------------------
# R7 — el timeout, y su razón
# ---------------------------------------------------------------------------


def test_el_default_son_30_segundos() -> None:
    """15,2 s de cold start medido; 29 s de techo del API Gateway; y distinto de
    los 45 s del facilitator «so the two clocks never race» (INC-2026-08-19)."""
    assert DEFAULT_TIMEOUT_S == 30.0


def test_el_timeout_llega_al_cliente_http(make_client) -> None:
    """No alcanza con que la constante sea 30: hay que ver que se USE."""
    c = make_client(lambda _r: json_response(HEALTH), timeout=7.5)
    assert c._http.timeout.read == 7.5
    c.close()

    d = make_client(lambda _r: json_response(HEALTH))
    assert d._http.timeout.read == DEFAULT_TIMEOUT_S
    d.close()


def test_la_red_de_pago_por_defecto_es_base() -> None:
    """Primera de `supportedChains` en el challenge vivo (2026-08-30)."""
    assert DEFAULT_PAY_NETWORK == "base"


# ---------------------------------------------------------------------------
# Atribución — el UA que el proveedor loguea
# ---------------------------------------------------------------------------


def test_el_user_agent_lleva_la_version_y_el_producto() -> None:
    """MeshRelay lo dejó escrito: «a User-Agent that lies about the version is
    worse than none». Acá la versión sale de `version.__version__`, que es de
    donde `pyproject.toml` también la lee — una sola definición."""
    assert default_user_agent() == f"uvd-describe-sdk-py/{__version__}"
    assert default_user_agent("karmakadabra") == (
        f"uvd-describe-sdk-py/{__version__} (+karmakadabra)"
    )


def test_el_producto_se_sanea_antes_de_entrar_al_header() -> None:
    """Un `\\n` en el UA es header splitting, y el valor sale de config ajena."""
    ua = default_user_agent("mala\r\nX-Inyectado: 1")
    assert "\n" not in ua and "\r" not in ua
    assert "X-Inyectado" not in ua or ":" not in ua.split("(+")[1]


def test_el_ua_viaja_en_cada_request(make_client) -> None:
    """El rate limit son 20 rps COMPARTIDOS sin bucket por partner: un request
    anónimo contra un límite compartido es free-riding."""
    vistos = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request.headers.get("User-Agent"))
        return json_response(HEALTH)

    with make_client(handler, product="meshrelay") as c:
        c.health()
        c.health()

    assert vistos == [c.user_agent] * 2
    assert "(+meshrelay)" in vistos[0]


def test_un_user_agent_explicito_gana() -> None:
    c = DescribeClient(
        user_agent="mi-cosa/9.9",
        transport=httpx.MockTransport(lambda _r: json_response(HEALTH)),
    )
    assert c.user_agent == "mi-cosa/9.9"
    c.close()


# ---------------------------------------------------------------------------
# Passthrough — se tipa lo conocido y se CONSERVA lo desconocido
# ---------------------------------------------------------------------------


def test_un_campo_que_el_sdk_no_tipa_sigue_llegando(make_client) -> None:
    """El precedente es del propio servicio: `Facet.direction` viajaba y FastAPI
    lo descartaba en silencio por no estar declarado — 200, forma correcta, dato
    ausente. Un SDK que tipe estricto y tire el resto reproduce ese bug del lado
    del cliente, y encima en verde."""
    payload = dict(WALLET_CON_REPUTACION, campo_del_futuro={"algo": 1})

    with make_client(lambda _r: json_response(payload)) as c:
        rep = c.wallet(payload["wallet"])

    assert rep is not None
    assert rep.raw["campo_del_futuro"] == {"algo": 1}


def test_distinct_raters_del_top_level_llega_tipado(make_client) -> None:
    """🔴 El caso medido: este campo viaja VIVO en `api.describe.net` (2026-08-30)
    y **no existe** en `types.py` de Execution Market, la implementación de
    referencia. Sin tiparlo ni conservarlo, quien migre de EM pierde un dato que
    la API ya sirve."""
    with make_client(lambda _r: json_response(WALLET_CON_REPUTACION)) as c:
        rep = c.wallet(WALLET_CON_REPUTACION["wallet"])
    assert rep is not None
    assert rep.distinct_raters == 7548
    assert rep.raw["distinct_raters"] == 7548


def test_la_wallet_viaja_verbatim_sin_lowercase(make_client) -> None:
    """🔴 Un id Solana es base58 **case-SENSITIVE**: bajarlo a minúsculas nombra
    otra clave, en silencio y con un 200. Lo declara el schema del servicio."""
    vistos = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(str(request.url))
        return json_response(dict(WALLET_CON_REPUTACION, wallet="83AzQwErTy"))

    with make_client(handler) as c:
        c.wallet("83AzQwErTy")

    assert "83AzQwErTy" in vistos[0]
    assert "83azqwerty" not in vistos[0]


# ---------------------------------------------------------------------------
# health() y leaderboard()
# ---------------------------------------------------------------------------


def test_health_expone_los_parametros_calibrables_sin_copiarlos(make_client) -> None:
    """El servicio publica `reading_policy` y `confidence_thresholds` en /health
    **justamente** para que ningún consumidor los vuelva a tipear. Este SDK los
    expone como dicts crudos y no como constantes suyas: una constante local
    sería una copia que se pudre."""
    with make_client(lambda _r: json_response(HEALTH)) as c:
        h = c.health()

    # `health()` es nullable desde la R5 corregida (2026-08-30): `None` significa
    # «no hubo respuesta». Acá el handler contesta, así que tiene que ser objeto.
    assert h is not None
    assert h.status == "ok"
    assert h.reading_policy["min_raters"] == 3
    assert h.reading_policy["campaign_per_rater"] == 20
    assert h.confidence_thresholds["high"] == 6
    # las políticas están versionadas POR SEPARADO
    assert h.policy_version == "equal-weight-per-chain@2"
    assert h.ordering_policy != h.policy_version
    assert h.chain("arbitrum") is not None
    assert h.chain("una-cadena-que-no-indexan") is None  # parcial ≠ error


def test_leaderboard_conserva_el_shrunk_score_que_manda_en_el_orden(make_client) -> None:
    """El ranking **no ordena por promedio**: ordena por la media bayesiana.
    `shrunk_score` viaja para que el orden se pueda recomputar a mano."""
    with make_client(lambda _r: json_response(LEADERBOARD)) as c:
        filas = c.leaderboard()

    assert filas is not None  # nullable desde la R5 corregida; acá SÍ contestó
    assert filas[0].shrunk_score == 99.982977
    assert filas[0].final_score == 100.0
    assert filas[0].shrunk_score != filas[0].final_score


def test_leaderboard_no_se_llama_con_parametros() -> None:
    """La ruta gratis contesta 422 a cualquier query param (medido 2026-08-30).
    Por eso el método no tiene argumentos: una firma con `limit=` sería un 422
    garantizado escrito en la API del SDK."""
    import inspect

    firma = inspect.signature(DescribeClient.leaderboard)
    assert list(firma.parameters) == ["self"]


def test_la_policy_version_se_inyecta_en_cada_fila(make_client) -> None:
    """R2: la ruta devuelve un array pelado sin política adentro, así que el
    SDK la baja a cada fila. Un ranking sin su política es un rumor ordenado."""
    from uvd_describe_sdk.models import parse_leaderboard

    filas = parse_leaderboard(LEADERBOARD, policy_version="equal-weight-per-chain@2")
    assert all(f.policy_version == "equal-weight-per-chain@2" for f in filas)


# ---------------------------------------------------------------------------
# badge — la superficie copy-paste
# ---------------------------------------------------------------------------


def test_badge_url_no_hace_una_sola_request() -> None:
    """Es string puro: se puede llamar sin conexión, en un render, en un loop.
    Un transporte que explota lo demuestra."""

    def explota(_r: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("badge_url tocó la red")

    c = DescribeClient(transport=httpx.MockTransport(explota))
    assert c.badge_url("0xabc") == "https://api.describe.net/badge/0xabc.svg"
    c.close()


def test_badge_url_escapa_lo_que_viene_de_afuera() -> None:
    """El valor termina en un `<img src=...>`. Sin escapar la barra, una wallet
    inventada con `/` apuntaría el `<img>` a otra ruta del índice."""
    url = badge_url("../../admin")
    assert "/badge/..%2F..%2Fadmin.svg" in url
    assert url.count("/badge/") == 1


def test_badge_url_respeta_un_base_url_propio() -> None:
    assert badge_url("0xabc", base_url="http://localhost:8088/") == (
        "http://localhost:8088/badge/0xabc.svg"
    )


def test_el_img_tag_lleva_alt() -> None:
    """Un badge sin texto alternativo es un número que un lector de pantalla no
    puede leer — y el número es todo el contenido."""
    tag = badge_img_tag("0xabc")
    assert tag.startswith("<img src=")
    assert 'alt="' in tag and 'alt=""' not in tag
    assert 'loading="lazy"' in tag


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------


def test_el_context_manager_cierra_el_cliente_http() -> None:
    with DescribeClient(
        transport=httpx.MockTransport(lambda _r: json_response(HEALTH))
    ) as c:
        c.health()
    assert c._http.is_closed


def test_el_base_url_se_normaliza() -> None:
    """Una barra final de más produciría `//health`, que en CloudFront es otra
    cache key y en algunos routers un 404."""
    vistos = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(str(request.url))
        return json_response(HEALTH)

    with DescribeClient(
        base_url="https://api.describe.net/", transport=httpx.MockTransport(handler)
    ) as c:
        c.health()
    assert vistos == ["https://api.describe.net/health"]


@pytest.mark.parametrize("metodo", ["wallet", "leaderboard", "health", "badge_url"])
def test_los_metodos_del_contrato_existen(metodo) -> None:
    assert callable(getattr(DescribeClient, metodo))


def test_los_seis_metodos_del_contrato_nucleo() -> None:
    """La tabla del contrato v0.1, con el naming idiomático de Python."""
    for m in (
        "wallet",
        "wallet_breakdown",
        "agent",
        "leaderboard",
        "health",
        "badge_url",
    ):
        assert callable(getattr(DescribeClient, m)), m
