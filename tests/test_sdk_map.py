"""El `sdk-map`: qué operación del spec de describe llama cada método público.

`schema/openapi.json` y `schema/sdk.overlay.yaml` están vendoreados de
describe-net (commit y sha256 en `schema/SOURCE`). El overlay oculta con
`x-fern-ignore: true` lo que no es alcance de un SDK REST (web app y protocolos
de agente); cada operación que queda es PÚBLICA y tiene que estar clasificada en
`sdk-map.json`:

    {"mapeadas": {operationId: "metodo"}, "fuera": {operationId: "motivo"}}

`mapeadas` NO se escribe a mano: sale de correr cada método público del cliente
contra un `httpx.MockTransport` que graba `(método HTTP, ruta, query)` y de
resolver lo grabado contra el spec con el overlay aplicado. `fuera` sí es a mano,
porque el motivo es una decisión. La suite falla si:

* una operación pública no está ni en `mapeadas` ni en `fuera` con motivo;
* un método llama a una ruta, un verbo o un parámetro de query que el spec no
  declara;
* aparece un método público que no se conduce acá ni se declara sin red;
* `sdk-map.json` no es el que se mide (regenerarlo: `SDK_MAP_ESCRIBIR=1`).

Los schemas vigilados salen del mapa (las respuestas de las operaciones
mapeadas, con sus `$ref` transitivos), no de una lista escrita a mano.

Los métodos pagos (`wallet_breakdown`, `wallet_history`, `agent`) se conducen con
un 200 al primer intento, que es la rama «el servicio no cobró» de `_paid`: el
grabador ve la misma ruta que vería el 402 y ningún test firma nada.

El overlay se lee SIN PyYAML (no es dependencia del SDK): `_leer_overlay` acepta
la gramática que usa este archivo y rechaza cualquier otra línea, y cada `target`
tiene que ser `$.paths['<ruta>'].<verbo>` y existir en el spec. Un target huérfano
es rojo, no «cero cambios».
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import httpx
import pytest

from uvd_describe_sdk import DescribeClient
from uvd_describe_sdk.client import DescribeNames

from .conftest import BREAKDOWN, HEALTH, LEADERBOARD, WALLET_CON_REPUTACION, json_response
from .names_replay import load

RAIZ = Path(__file__).resolve().parent.parent
SCHEMA = RAIZ / "schema"
SPEC = SCHEMA / "openapi.json"
OVERLAY = SCHEMA / "sdk.overlay.yaml"
SOURCE = SCHEMA / "SOURCE"
MAPA = RAIZ / "sdk-map.json"

VERBOS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")

WALLET = "0x97cd97cfe21799bacbf39d0a53469e5f82f30996"

HISTORIAL: Dict[str, Any] = {
    "wallet": "0x76e9be89a3be6c1bf581a1f4519cb82dca9c57b3",
    "bucket": "week",
    "points": [
        {"period": "2026-08-17T00:00:00Z", "score": 90.166667,
         "review_count": 6, "cumulative_score": 88.921881},
    ],
    "coverage": {"dated_reviews": 6, "undated_reviews": 0},
}

AGENTE: Dict[str, Any] = {
    "network": "base",
    "agent_id": "1197",
    "score": 90.0,
    "review_count": 0,
    "caveats": [],
    "ratings": [],
    "policy_version": "credibility-weight-per-chain@1",
}

#: Métodos públicos que NO hablan con describe. Cada uno se llama igual en
#: `test_los_metodos_sin_red_no_llaman` y tiene que grabar cero requests.
SIN_RED: Dict[str, Callable[[DescribeClient], Any]] = {
    "badge_url": lambda c: c.badge_url(WALLET),
    "close": lambda c: c.close(),
    "is_partner": lambda c: c.is_partner,
    "names": lambda c: c.names,
    "user_agent": lambda c: c.user_agent,
}

#: (método, cómo se llama, qué contesta el doble). Un método puede aparecer más
#: de una vez con argumentos distintos; todas sus llamadas cuentan.
CONDUCTORES: List[Tuple[str, Callable[[DescribeClient], Any], Any]] = [
    ("wallet", lambda c: c.wallet(WALLET), WALLET_CON_REPUTACION),
    ("leaderboard", lambda c: c.leaderboard(), LEADERBOARD),
    ("health", lambda c: c.health(), HEALTH),
    ("wallet_breakdown", lambda c: c.wallet_breakdown(WALLET), BREAKDOWN),
    ("wallet_breakdown", lambda c: c.wallet_breakdown(WALLET, snapshot=True), BREAKDOWN),
    ("wallet_history", lambda c: c.wallet_history(WALLET), HISTORIAL),
    ("agent", lambda c: c.agent("base", "1197"), AGENTE),
    (
        "names.resolve",
        lambda c: c.names.resolve("ultravioletadao.eth"),
        load("resolve_ultravioletadao_eth")["result"],
    ),
    (
        "names.reverse",
        lambda c: c.names.reverse("0xe4dc963c56979E0260fc146b87eE24F18220e545"),
        load("reverse_0xe4dc_ultravioleta")["result"],
    ),
]

_SIN_METODO = "sin método en el SDK Python"

#: Operaciones públicas que el SDK decide no cubrir, con el motivo.
FUERA: Dict[str, str] = {
    "getWalletBadge": "badge_url() arma la URL del SVG y no llama a la red",
    "getCategories": _SIN_METODO,
    "getChainDetail": _SIN_METODO,
    "getFacetStats": _SIN_METODO,
    "getFeed": _SIN_METODO,
    "getIssuerStats": _SIN_METODO,
    "getLeaderboardPage": _SIN_METODO + " (ruta paga)",
    "getManifesto": _SIN_METODO,
    "getPricing": _SIN_METODO,
    "getRaterProfile": _SIN_METODO + " (ruta paga)",
    "getTypeStats": _SIN_METODO,
    "getWalletExists": _SIN_METODO,
    "listChains": _SIN_METODO,
    "search": _SIN_METODO,
}

Llamada = Tuple[str, str, str, Tuple[str, ...]]  # (verbo, host, ruta, claves de query)


# ---------------------------------------------------------------------------
# El overlay: una gramática chica y estricta, no YAML
# ---------------------------------------------------------------------------

_RE_TARGET = re.compile(r"""^\$\.paths\['(?P<ruta>[^']+)'\]\.(?P<verbo>[a-z]+)$""")
_RE_ACCION = re.compile(r'^  - target: "(?P<target>[^"]+)"$')
_RE_UPDATE = re.compile(r"^    update: \{(?P<cuerpo>[^{}]*)\}$")
_RE_CABECERA = re.compile(r"^(?P<clave>overlay|info|actions):(?: (?P<valor>\S+))?$")
_RE_INFO = re.compile(r"^  (?P<clave>title|version): (?P<valor>.+)$")
_RE_PAR = re.compile(r"^(?P<clave>[A-Za-z0-9_-]+): (?P<valor>[A-Za-z0-9_.-]+)$")


def _sin_comentario(linea: str) -> str:
    fuera_de_comillas = True
    for i, ch in enumerate(linea):
        if ch == '"':
            fuera_de_comillas = not fuera_de_comillas
        elif ch == "#" and fuera_de_comillas and (i == 0 or linea[i - 1] == " "):
            return linea[:i].rstrip()
    return linea.rstrip()


def _escalar(valor: str) -> Any:
    return {"true": True, "false": False}.get(valor, valor)


def _leer_overlay(texto: str) -> Dict[str, Any]:
    doc: Dict[str, Any] = {"info": {}, "actions": []}
    pendiente: Optional[str] = None
    for n, cruda in enumerate(texto.splitlines(), 1):
        linea = _sin_comentario(cruda)
        if not linea:
            continue
        if pendiente is not None:
            m = _RE_UPDATE.match(linea)
            if not m:
                raise ValueError(f"línea {n}: se esperaba `update:` tras el target")
            update: Dict[str, Any] = {}
            for par in m["cuerpo"].split(","):
                mp = _RE_PAR.match(par.strip())
                if not mp or mp["clave"] in update:
                    raise ValueError(f"línea {n}: par fuera de la gramática: {par!r}")
                update[mp["clave"]] = _escalar(mp["valor"])
            doc["actions"].append({"target": pendiente, "update": update})
            pendiente = None
            continue
        for rx in (_RE_ACCION, _RE_CABECERA, _RE_INFO):
            m = rx.match(linea)
            if m:
                break
        else:
            raise ValueError(f"línea {n} fuera de la gramática del overlay: {cruda!r}")
        if rx is _RE_ACCION:
            pendiente = m["target"]
        elif rx is _RE_INFO:
            doc["info"][m["clave"]] = m["valor"]
        elif m["valor"] is not None:
            doc[m["clave"]] = m["valor"]
    if pendiente is not None:
        raise ValueError("el overlay termina con un target sin `update:`")
    return doc


def aplicar_overlay(spec: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay 1.0.0 restringido a `update` sobre una operación. Un target que no
    tiene esa forma, o que no apunta a una operación del spec, levanta."""
    if overlay.get("overlay") != "1.0.0":
        raise ValueError(f"overlay {overlay.get('overlay')!r}, se esperaba 1.0.0")
    aplicado = copy.deepcopy(spec)
    huerfanos: List[str] = []
    for accion in overlay["actions"]:
        m = _RE_TARGET.match(accion["target"])
        if not m or m["verbo"] not in VERBOS:
            raise ValueError(f"target fuera de la forma $.paths['…'].<verbo>: {accion['target']}")
        op = aplicado["paths"].get(m["ruta"], {}).get(m["verbo"])
        if not isinstance(op, dict):
            huerfanos.append(accion["target"])
            continue
        op.update(accion["update"])
    if huerfanos:
        raise ValueError(f"targets sin nodo en el spec: {huerfanos}")
    return aplicado


# ---------------------------------------------------------------------------
# El spec: operaciones, prefijos de `servers`, resolución de rutas
# ---------------------------------------------------------------------------


def operaciones(spec: Dict[str, Any]) -> Dict[str, Tuple[str, str, Dict[str, Any]]]:
    """`{operationId: (VERBO, plantilla, operación)}`; un id repetido levanta."""
    ops: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}
    for plantilla, item in spec["paths"].items():
        for verbo, op in item.items():
            if verbo not in VERBOS:
                continue
            oid = op["operationId"]
            if oid in ops:
                raise ValueError(f"operationId repetido: {oid}")
            ops[oid] = (verbo.upper(), plantilla, op)
    return ops


def publicas(spec_aplicado: Dict[str, Any]) -> Set[str]:
    return {
        oid
        for oid, (_, _, op) in operaciones(spec_aplicado).items()
        if op.get("x-fern-ignore") is not True
    }


def _servidores(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """`(host, prefijo)` de cada `servers[].url`, el prefijo más largo primero."""
    pares = []
    for server in spec.get("servers", []):
        partes = urlsplit(server["url"])
        pares.append((partes.netloc, partes.path.rstrip("/")))
    return sorted(pares, key=lambda p: len(p[1]), reverse=True)


def _plantilla_a_regex(plantilla: str) -> re.Pattern[str]:
    trozos = re.split(r"(\{[^/{}]+\})", plantilla)
    return re.compile("".join("[^/]+" if t.startswith("{") else re.escape(t) for t in trozos))


def resolver(spec: Dict[str, Any], llamada: Llamada) -> Optional[str]:
    """El `operationId` de una llamada grabada, o `None` si el spec no la tiene.

    La ruta se mira sin el prefijo de un `servers[].url` del mismo host (el spec
    declara `https://api.describe.net` y `https://api.describe.net/v1`), y cada
    clave de query tiene que ser un parámetro `in: query` de esa operación."""
    verbo, host, ruta, claves = llamada
    ops = operaciones(spec)
    for server_host, prefijo in _servidores(spec):
        if host != server_host or not ruta.startswith(prefijo + "/"):
            continue
        relativa = ruta[len(prefijo):]
        for oid, (v, plantilla, op) in ops.items():
            if v != verbo or not _plantilla_a_regex(plantilla).fullmatch(relativa):
                continue
            declarados = {p["name"] for p in op.get("parameters", []) if p.get("in") == "query"}
            return oid if set(claves) <= declarados else None
    return None


# ---------------------------------------------------------------------------
# El grabador
# ---------------------------------------------------------------------------


def _cliente(respuesta: Any, grabadas: List[Llamada]) -> DescribeClient:
    def handler(request: httpx.Request) -> httpx.Response:
        grabadas.append(
            (
                request.method,
                request.url.host,
                request.url.path,
                tuple(sorted(dict(request.url.params))),
            )
        )
        return json_response(respuesta)

    return DescribeClient(transport=httpx.MockTransport(handler), jitter=0, fail_open=False)


def grabar() -> List[Tuple[str, Llamada]]:
    """Corre cada conductor; cada uno tiene que devolver un resultado (no `None`)
    y grabar al menos una llamada."""
    grabado: List[Tuple[str, Llamada]] = []
    for metodo, llamar, respuesta in CONDUCTORES:
        llamadas: List[Llamada] = []
        with _cliente(respuesta, llamadas) as cliente:
            resultado = llamar(cliente)
        assert resultado is not None, f"{metodo}: el conductor no llegó a un resultado"
        assert llamadas, f"{metodo}: no grabó ninguna llamada"
        grabado.extend((metodo, llamada) for llamada in llamadas)
    return grabado


def medir_mapa(
    spec_aplicado: Dict[str, Any], grabado: List[Tuple[str, Llamada]], fuera: Dict[str, str]
) -> Dict[str, Dict[str, str]]:
    mapeadas: Dict[str, str] = {}
    for metodo, llamada in grabado:
        oid = resolver(spec_aplicado, llamada)
        if oid is None:
            raise AssertionError(f"{metodo} llama a {llamada}, que no está en el spec")
        if mapeadas.setdefault(oid, metodo) != metodo:
            raise AssertionError(f"{oid} lo llaman {mapeadas[oid]} y {metodo}")
    return {"mapeadas": dict(sorted(mapeadas.items())), "fuera": dict(sorted(fuera.items()))}


def sin_clasificar(spec_aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]) -> Set[str]:
    con_motivo = {oid for oid, motivo in mapa["fuera"].items() if motivo.strip()}
    return publicas(spec_aplicado) - set(mapa["mapeadas"]) - con_motivo


def schemas_vigilados(spec: Dict[str, Any], mapa: Dict[str, Dict[str, str]]) -> Set[str]:
    """Los schemas que alcanzan las respuestas 2xx de las operaciones mapeadas,
    siguiendo cada `$ref` hasta el fondo. Un `$ref` que no resuelve levanta."""
    schemas = spec["components"]["schemas"]
    ops = operaciones(spec)
    vistos: Set[str] = set()
    pila: List[Any] = [
        respuesta
        for oid in mapa["mapeadas"]
        for codigo, respuesta in ops[oid][2].get("responses", {}).items()
        if codigo.startswith("2")
    ]
    while pila:
        nodo = pila.pop()
        if isinstance(nodo, dict):
            ref = nodo.get("$ref")
            if isinstance(ref, str):
                nombre = ref.rsplit("/", 1)[-1]
                if not ref.startswith("#/components/schemas/") or nombre not in schemas:
                    raise ValueError(f"$ref que no resuelve: {ref}")
                if nombre not in vistos:
                    vistos.add(nombre)
                    pila.append(schemas[nombre])
            pila.extend(v for k, v in nodo.items() if k != "$ref")
        elif isinstance(nodo, list):
            pila.extend(nodo)
    return vistos


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spec() -> Dict[str, Any]:
    data: Dict[str, Any] = json.loads(SPEC.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def overlay() -> Dict[str, Any]:
    return _leer_overlay(OVERLAY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def aplicado(spec: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    return aplicar_overlay(spec, overlay)


@pytest.fixture(scope="module")
def grabado() -> List[Tuple[str, Llamada]]:
    return grabar()


@pytest.fixture(scope="module")
def mapa(aplicado: Dict[str, Any], grabado: List[Tuple[str, Llamada]]) -> Dict[str, Dict[str, str]]:
    return medir_mapa(aplicado, grabado, FUERA)


# ---------------------------------------------------------------------------
# Lo vendoreado es lo que dice SOURCE
# ---------------------------------------------------------------------------


def test_cada_archivo_vendoreado_tiene_el_sha256_de_source() -> None:
    campos = dict(
        linea.split(": ", 1)
        for linea in SOURCE.read_text(encoding="utf-8").splitlines()
        if linea and not linea.startswith("#")
    )
    assert campos["repo"] == "UltravioletaDAO/describe-net"
    assert re.fullmatch(r"[0-9a-f]{40}", campos["commit"])
    for archivo in (SPEC, OVERLAY):
        _, sha = campos[archivo.name].split(" sha256=")
        assert hashlib.sha256(archivo.read_bytes()).hexdigest() == sha, archivo.name


# ---------------------------------------------------------------------------
# El overlay
# ---------------------------------------------------------------------------


def test_el_overlay_clasifica_cada_operacion(aplicado: Dict[str, Any]) -> None:
    """Oculta o con grupo + método: ninguna operación del spec queda sin marca."""
    sin_marca = [
        oid
        for oid, (_, _, op) in operaciones(aplicado).items()
        if op.get("x-fern-ignore") is not True
        and not (op.get("x-fern-sdk-group-name") and op.get("x-fern-sdk-method-name"))
    ]
    assert sin_marca == []
    assert publicas(aplicado), "el overlay dejó cero operaciones públicas"


def test_un_target_huerfano_es_rojo(spec: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    roto = copy.deepcopy(overlay)
    roto["actions"].append(
        {"target": "$.paths['/no-existe'].get", "update": {"x-fern-ignore": True}}
    )
    with pytest.raises(ValueError, match="targets sin nodo"):
        aplicar_overlay(spec, roto)


def test_una_linea_fuera_de_la_gramatica_es_roja() -> None:
    texto = OVERLAY.read_text(encoding="utf-8") + "  - remove: true\n"
    with pytest.raises(ValueError, match="fuera de la gramática"):
        _leer_overlay(texto)


def test_el_lector_no_pierde_ninguna_accion(overlay: Dict[str, Any]) -> None:
    lineas = OVERLAY.read_text(encoding="utf-8").splitlines()
    assert len(overlay["actions"]) == sum(1 for x in lineas if x.startswith("  - target:"))


# ---------------------------------------------------------------------------
# Cada método público: conducido o declarado sin red
# ---------------------------------------------------------------------------


def _publicos(cls: type) -> Set[str]:
    return {n for n in vars(cls) if not n.startswith("_")}


def test_cada_metodo_publico_se_conduce_o_se_declara_sin_red() -> None:
    conducidos = {m for m, _, _ in CONDUCTORES}
    esperados = _publicos(DescribeClient) | {f"names.{n}" for n in _publicos(DescribeNames)}
    assert conducidos.isdisjoint(SIN_RED)
    assert esperados == conducidos | set(SIN_RED)


def test_los_metodos_sin_red_no_llaman() -> None:
    for metodo, llamar in SIN_RED.items():
        llamadas: List[Llamada] = []
        cliente = _cliente({}, llamadas)
        llamar(cliente)
        cliente.close()
        assert llamadas == [], f"{metodo} está declarado sin red y llamó a {llamadas}"


# ---------------------------------------------------------------------------
# El mapa
# ---------------------------------------------------------------------------


def test_cada_llamada_resuelve_a_una_operacion_publica(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    ocultas = set(operaciones(aplicado)) - publicas(aplicado)
    assert set(mapa["mapeadas"]).isdisjoint(ocultas)


def test_ninguna_operacion_publica_queda_sin_clasificar(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    assert sin_clasificar(aplicado, mapa) == set()
    assert set(mapa["fuera"]).isdisjoint(mapa["mapeadas"])
    assert set(mapa["fuera"]) <= publicas(aplicado), "un `fuera` que no es operación pública"


def test_el_sdk_map_commiteado_es_el_medido(mapa: Dict[str, Dict[str, str]]) -> None:
    medido = json.dumps(mapa, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("SDK_MAP_ESCRIBIR") == "1":
        MAPA.write_text(medido, encoding="utf-8", newline="\n")
    assert MAPA.read_text(encoding="utf-8") == medido, (
        "sdk-map.json no es el medido: regeneralo con SDK_MAP_ESCRIBIR=1"
    )


def test_los_schemas_vigilados_salen_del_mapa(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    vigilados = schemas_vigilados(aplicado, mapa)
    ops = operaciones(aplicado)
    for oid in mapa["mapeadas"]:
        propios = schemas_vigilados(aplicado, {"mapeadas": {oid: ""}, "fuera": {}})
        assert propios, f"{oid} no aporta ningún schema"
        assert propios <= vigilados, oid
    assert schemas_vigilados(aplicado, {"mapeadas": {}, "fuera": mapa["fuera"]}) == set()
    esperados = {
        ref.rsplit("/", 1)[-1]
        for oid in mapa["mapeadas"]
        for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(ops[oid][2]["responses"]["200"]))
    }
    assert esperados <= vigilados


# ---------------------------------------------------------------------------
# Mutaciones: cada una tiene que poner la suite en ROJO
# ---------------------------------------------------------------------------


def test_mutacion_una_operacion_inventada_es_roja(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    mutado = copy.deepcopy(aplicado)
    mutado["paths"]["/inventada/{wallet}"] = {
        "get": {"operationId": "getInventada", "responses": {"200": {"description": "ok"}}}
    }
    assert sin_clasificar(mutado, mapa) == {"getInventada"}


def test_mutacion_una_operacion_oculta_por_el_overlay_no_cuenta(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    mutado = copy.deepcopy(aplicado)
    mutado["paths"]["/inventada"] = {
        "get": {"operationId": "getInventada", "x-fern-ignore": True, "responses": {}}
    }
    assert sin_clasificar(mutado, mapa) == set()


def test_mutacion_un_motivo_vacio_no_clasifica(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    mutado = {"mapeadas": mapa["mapeadas"], "fuera": {**mapa["fuera"], "getPricing": " "}}
    assert sin_clasificar(aplicado, mutado) == {"getPricing"}


def test_mutacion_una_ruta_que_falta_en_el_spec_es_roja(
    aplicado: Dict[str, Any], grabado: List[Tuple[str, Llamada]]
) -> None:
    mutado = copy.deepcopy(aplicado)
    del mutado["paths"]["/health"]
    with pytest.raises(AssertionError, match="health llama a"):
        medir_mapa(mutado, grabado, FUERA)


def test_mutacion_un_verbo_que_el_spec_no_tiene_es_rojo(aplicado: Dict[str, Any]) -> None:
    assert resolver(aplicado, ("GET", "api.describe.net", "/health", ())) == "getHealth"
    assert resolver(aplicado, ("POST", "api.describe.net", "/health", ())) is None


def test_mutacion_un_parametro_de_query_no_declarado_es_rojo(aplicado: Dict[str, Any]) -> None:
    ruta = "/v1/names/resolve"
    assert resolver(aplicado, ("GET", "api.describe.net", ruta, ("name",))) == "resolveName"
    assert resolver(aplicado, ("GET", "api.describe.net", ruta, ("nombre",))) is None


def test_mutacion_sin_el_server_v1_las_rutas_de_names_son_rojas(
    aplicado: Dict[str, Any], grabado: List[Tuple[str, Llamada]]
) -> None:
    mutado = copy.deepcopy(aplicado)
    mutado["servers"] = [s for s in mutado["servers"] if not s["url"].endswith("/v1")]
    with pytest.raises(AssertionError, match="names.resolve llama a"):
        medir_mapa(mutado, grabado, FUERA)


def test_mutacion_un_host_que_no_es_un_server_es_rojo(aplicado: Dict[str, Any]) -> None:
    assert resolver(aplicado, ("GET", "api.example.org", "/health", ())) is None


def test_mutacion_un_ref_que_no_resuelve_es_rojo(
    aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]
) -> None:
    mutado = copy.deepcopy(aplicado)
    del mutado["components"]["schemas"]["Health"]
    with pytest.raises(ValueError, match="no resuelve"):
        schemas_vigilados(mutado, mapa)
