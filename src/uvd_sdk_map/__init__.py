"""Cobertura contrato REST <-> SDK, extraída de tests/test_sdk_map.py.

El 2026-10-12 el verificador sólo se podía importar cargando pytest, httpx y
fixtures de describe. Este paquete recibe diccionarios OpenAPI y llamadas ya
capturadas: no importa ningún SDK, no abre archivos y no hace red. Los drivers
quedan en cada consumidor; las reglas de cobertura viven acá una sola vez.

El lector de overlay es una gramática cerrada, NO un parser YAML general.
Los límites y la adopción desde Python/TypeScript están en docs/sdk-map/README.md.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

VERBOS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
Llamada = Tuple[str, str, str, Tuple[str, ...]]  # verbo, host[:puerto], ruta, claves de query

__all__ = [
    "Llamada", "aplicar_overlay", "leer_overlay", "medir_mapa", "operaciones",
    "publicas", "resolver", "schemas_vigilados", "sin_clasificar", "verificar_cobertura",
]

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


def leer_overlay(texto: str) -> Dict[str, Any]:
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


def medir_mapa(
    spec_aplicado: Dict[str, Any], grabado: List[Tuple[str, Llamada]], fuera: Dict[str, str]
) -> Dict[str, Dict[str, str]]:
    mapeadas: Dict[str, str] = {}
    for metodo, llamada in grabado:
        if not isinstance(metodo, str) or not metodo.strip():
            raise ValueError("método grabado sin nombre")
        oid = resolver(spec_aplicado, llamada)
        if oid is None:
            raise AssertionError(f"{metodo} llama a {llamada}, que no está en el spec")
        if mapeadas.setdefault(oid, metodo) != metodo:
            raise AssertionError(f"{oid} lo llaman {mapeadas[oid]} y {metodo}")
    return {"mapeadas": dict(sorted(mapeadas.items())), "fuera": dict(sorted(fuera.items()))}


def sin_clasificar(spec_aplicado: Dict[str, Any], mapa: Dict[str, Dict[str, str]]) -> Set[str]:
    con_motivo = {
        oid for oid, motivo in mapa["fuera"].items()
        if isinstance(motivo, str) and motivo.strip()
    }
    return publicas(spec_aplicado) - set(mapa["mapeadas"]) - con_motivo


def schemas_vigilados(spec: Dict[str, Any], mapa: Dict[str, Dict[str, str]]) -> Set[str]:
    """Los schemas que alcanzan las respuestas 2xx de las operaciones mapeadas,
    siguiendo cada `$ref` hasta el fondo. Un `$ref` que no resuelve levanta."""
    schemas = spec.get("components", {}).get("schemas", {})
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


def verificar_cobertura(
    spec_aplicado: Dict[str, Any], grabado: List[Tuple[str, Llamada]], fuera: Dict[str, str]
) -> Dict[str, Dict[str, str]]:
    """Mide y exige una partición completa de las operaciones públicas.

    `medir_mapa` sirve para inspeccionar cobertura parcial. Este es el gate de
    CI: levanta AssertionError por cobertura y ValueError por entradas inválidas.
    No requiere extensiones Fern cuando el consumidor no usa un overlay.
    """
    mapa = medir_mapa(spec_aplicado, grabado, fuera)
    visibles = publicas(spec_aplicado)
    if not visibles:
        raise AssertionError("el contrato dejó cero operaciones públicas")
    ocultas = set(mapa["mapeadas"]) - visibles
    if ocultas:
        raise AssertionError(f"llamadas a operaciones ocultas: {sorted(ocultas)}")
    ajenas = set(fuera) - visibles
    if ajenas:
        raise AssertionError(f"fuera contiene operaciones no públicas: {sorted(ajenas)}")
    solapadas = set(fuera) & set(mapa["mapeadas"])
    if solapadas:
        raise AssertionError(f"operaciones mapeadas y fuera a la vez: {sorted(solapadas)}")
    faltantes = sin_clasificar(spec_aplicado, mapa)
    if faltantes:
        raise AssertionError(f"operaciones sin clasificar o sin motivo: {sorted(faltantes)}")
    return mapa
