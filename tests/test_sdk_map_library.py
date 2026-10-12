"""Contrato SINTÉTICO ajeno a describe: el paquete no depende de sus fixtures.

Estos tests fijan las guardas que antes sólo tenían ejemplos de describe y
los gates que un consumidor externo debe usar sin copiar asserts de la suite.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from uvd_sdk_map import (
    aplicar_overlay,
    leer_overlay,
    medir_mapa,
    operaciones,
    resolver,
    schemas_vigilados,
    verificar_cobertura,
)


@pytest.fixture
def contrato() -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "servers": [{"url": "https://catalog.example:8443/api/v2"}],
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "readItem",
                    "parameters": [{"in": "query", "name": "lang"}],
                    "responses": {"200": {"$ref": "#/components/schemas/Item"}},
                }
            },
            "/status": {"get": {"operationId": "readStatus", "responses": {}}},
        },
        "components": {
            "schemas": {
                "Item": {"properties": {"child": {"$ref": "#/components/schemas/Child"}}},
                "Child": {"allOf": [{"$ref": "#/components/schemas/Item"}]},
                "Unused": {},
            }
        },
    }


CALL = ("GET", "catalog.example:8443", "/api/v2/items/0Xab", ("lang",))
TRACE = [("items.read", CALL)]
OUTSIDE = {"readStatus": "no ofrece estado; decisión del SDK"}


def test_contrato_ajeno_y_grabacion_JSON_sin_overlay(contrato: Dict[str, Any]) -> None:
    original = copy.deepcopy(contrato)
    # Los arrays de un grabador TypeScript entran por json.loads sin adaptador.
    recorded = json.loads(json.dumps(TRACE))
    mapa = verificar_cobertura(contrato, recorded, OUTSIDE)
    assert mapa == {"mapeadas": {"readItem": "items.read"}, "fuera": OUTSIDE}
    assert schemas_vigilados(contrato, mapa) == {"Item", "Child"}
    assert contrato == original
    assert verificar_cobertura(contrato, TRACE * 2, OUTSIDE) == mapa


@pytest.mark.parametrize("path", ["0xAB", "0Xab", "café", "A", "a"])
def test_segmentos_opacos_sin_normalizar(contrato: Dict[str, Any], path: str) -> None:
    assert resolver(contrato, ("GET", CALL[1], "/api/v2/items/" + path, ())) == "readItem"


@pytest.mark.parametrize("call", [
    ("POST", CALL[1], CALL[2], CALL[3]),
    ("get", CALL[1], CALL[2], CALL[3]),
    (" GET ", CALL[1], CALL[2], CALL[3]),
    ("GET", "catalog.example", CALL[2], CALL[3]),
    ("GET", "other.example:8443", CALL[2], CALL[3]),
    ("GET", CALL[1], "/api/v20/items/1", ()),
    ("GET", CALL[1], "/bad/v2/items/1", ()),
    ("GET", CALL[1], "/api/v2/Items/1", ()),
    ("GET", CALL[1], "/api/v2/items/", ()),
    ("GET", CALL[1], "/api/v2/items/a/b", ()),
    ("GET", CALL[1], CALL[2], ("Lang",)),
    ("GET", CALL[1], CALL[2], ("lang", "extra")),
])
def test_una_llamada_fuera_del_contrato_no_se_mapea(
    contrato: Dict[str, Any], call: Any
) -> None:
    with pytest.raises(AssertionError, match="no está en el spec"):
        verificar_cobertura(contrato, TRACE + [("items.read", call)], OUTSIDE)


@pytest.mark.parametrize("reason", ["", " ", "\t\n", "\u2003", None, False])
def test_fuera_exige_motivo_textual_no_vacio(contrato: Dict[str, Any], reason: Any) -> None:
    with pytest.raises(AssertionError, match="sin clasificar o sin motivo.*readStatus"):
        verificar_cobertura(contrato, TRACE, {"readStatus": reason})


def test_una_operacion_nueva_no_pasa(contrato: Dict[str, Any]) -> None:
    contrato["paths"]["/new"] = {"post": {"operationId": "newOperation"}}
    with pytest.raises(AssertionError, match="sin clasificar.*newOperation"):
        verificar_cobertura(contrato, TRACE, OUTSIDE)


def test_mapeadas_y_fuera_son_disjuntas(contrato: Dict[str, Any]) -> None:
    with pytest.raises(AssertionError, match="mapeadas y fuera a la vez"):
        verificar_cobertura(contrato, TRACE, {**OUTSIDE, "readItem": "no se usa"})


@pytest.mark.parametrize("oid", ["inventada", "readItem"])
def test_fuera_no_admite_ids_ajenos_u_ocultos(contrato: Dict[str, Any], oid: str) -> None:
    contrato["paths"]["/items/{id}"]["get"]["x-fern-ignore"] = True
    with pytest.raises(AssertionError, match="fuera contiene operaciones no públicas"):
        verificar_cobertura(contrato, [], {**OUTSIDE, oid: "motivo"})


def test_no_se_puede_llamar_a_una_operacion_oculta(contrato: Dict[str, Any]) -> None:
    contrato["paths"]["/items/{id}"]["get"]["x-fern-ignore"] = True
    with pytest.raises(AssertionError, match="llamadas a operaciones ocultas"):
        verificar_cobertura(contrato, TRACE, OUTSIDE)


@pytest.mark.parametrize("empty", [True, False])
def test_un_contrato_sin_operaciones_publicas_no_da_verde(
    contrato: Dict[str, Any], empty: bool
) -> None:
    if empty:
        contrato["paths"] = {}
    else:
        for item in contrato["paths"].values():
            item["get"]["x-fern-ignore"] = True
    with pytest.raises(AssertionError, match="cero operaciones públicas"):
        verificar_cobertura(contrato, [], {})


def test_un_mapa_parcial_sigue_disponible_para_diagnostico(contrato: Dict[str, Any]) -> None:
    assert medir_mapa(contrato, [], {}) == {"mapeadas": {}, "fuera": {}}
    assert verificar_cobertura(contrato, [], {**OUTSIDE, "readItem": "no se usa"}) == {
        "mapeadas": {}, "fuera": {"readItem": "no se usa", **OUTSIDE},
    }


@pytest.mark.parametrize("method", ["", " \t", None])
def test_grabacion_sin_nombre_no_da_cobertura(contrato: Dict[str, Any], method: Any) -> None:
    with pytest.raises(ValueError, match="método grabado sin nombre"):
        verificar_cobertura(contrato, [(method, CALL)], OUTSIDE)


def test_colisiones_de_metodos_e_ids_no_se_pierden(contrato: Dict[str, Any]) -> None:
    with pytest.raises(AssertionError, match="lo llaman"):
        verificar_cobertura(contrato, TRACE + [("anotherMethod", CALL)], OUTSIDE)
    contrato["paths"]["/status"]["get"]["operationId"] = "readItem"
    with pytest.raises(ValueError, match="operationId repetido"):
        operaciones(contrato)


def test_schemas_solo_2xx_transitivos_con_ciclos(contrato: Dict[str, Any]) -> None:
    responses = contrato["paths"]["/items/{id}"]["get"]["responses"]
    responses["400"] = {"$ref": "#/components/schemas/Unused"}
    responses["201"] = {"items": [{"$ref": "#/components/schemas/Created"}]}
    contrato["components"]["schemas"]["Created"] = {}
    mapa = verificar_cobertura(contrato, TRACE, OUTSIDE)
    assert schemas_vigilados(contrato, mapa) == {"Item", "Child", "Created"}
    assert schemas_vigilados(contrato, {"mapeadas": {}, "fuera": OUTSIDE}) == set()


@pytest.mark.parametrize("ref", ["#/components/schemas/Missing", "external.json#/Item"])
def test_un_ref_irresoluble_no_se_ignora(contrato: Dict[str, Any], ref: str) -> None:
    contrato["components"]["schemas"]["Child"] = {"$ref": ref}
    with pytest.raises(ValueError, match="no resuelve"):
        schemas_vigilados(contrato, verificar_cobertura(contrato, TRACE, OUTSIDE))


def test_un_contrato_inline_no_necesita_components(contrato: Dict[str, Any]) -> None:
    del contrato["components"]
    contrato["paths"]["/items/{id}"]["get"]["responses"] = {"200": {"type": "string"}}
    assert schemas_vigilados(contrato, verificar_cobertura(contrato, TRACE, OUTSIDE)) == set()


OVERLAY = '''overlay: 1.0.0
info:
  title: Catálogo # comentario
  version: 1.0.0
actions:
  - target: "$.paths['/status'].get"
    update: {x-fern-ignore: true}
'''


def test_overlay_no_muta_el_contrato_y_respeta_comentarios(contrato: Dict[str, Any]) -> None:
    overlay = leer_overlay(OVERLAY)
    aplicado = aplicar_overlay(contrato, overlay)
    assert overlay["info"]["title"] == "Catálogo"
    assert "x-fern-ignore" not in contrato["paths"]["/status"]["get"]
    assert verificar_cobertura(aplicado, TRACE, {})["fuera"] == {}


@pytest.mark.parametrize("text, message", [
    (OVERLAY + "extra: true", "fuera de la gramática"),
    (OVERLAY.replace("    update:", "  update:"), "se esperaba"),
    (OVERLAY[:OVERLAY.index("    update:")], "sin `update:`"),
    (OVERLAY.replace("x-fern-ignore: true", "x-fern-ignore: true, x-fern-ignore: false"), "par"),
    (OVERLAY.replace("x-fern-ignore: true", "x-fern-ignore: []"), "par"),
])
def test_el_lector_restringido_rechaza_otra_gramatica(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        leer_overlay(text)


@pytest.mark.parametrize("text, message", [
    (OVERLAY.replace("overlay: 1.0.0", "overlay: 1.1.0"), "se esperaba 1.0.0"),
    (OVERLAY.replace("/status", "/absent"), "targets sin nodo"),
    (OVERLAY.replace(".get", ".parameters"), "target fuera de la forma"),
    (OVERLAY.replace(".get", ".GET"), "target fuera de la forma"),
])
def test_overlay_inaplicable_es_rojo(
    contrato: Dict[str, Any], text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        aplicar_overlay(contrato, leer_overlay(text))


def test_importable_sin_pytest_httpx_sdk_ni_cwd_del_repo(tmp_path: Path) -> None:
    outside = tmp_path / "consumidor con espacios y ñ"
    outside.mkdir()
    source = Path(__file__).resolve().parents[1] / "src"
    code = '''import sys
sys.path.insert(0, sys.argv[1])
from uvd_sdk_map import verificar_cobertura
spec = {"servers": [{"url": "https://example.test"}],
        "paths": {"/": {"get": {"operationId": "ping"}}}}
assert verificar_cobertura(spec, [("ping", ("GET", "example.test", "/", ()))], {}) == {
    "mapeadas": {"ping": "ping"}, "fuera": {}}
assert not {"pytest", "httpx", "uvd_describe_sdk"}.intersection(sys.modules)
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(source)],
        cwd=outside, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_el_PR_de_cobertura_no_dispara_el_smoke_vivo() -> None:
    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    # Este job es el último del workflow. El if debe estar a nivel del job,
    # no en un comentario ni dentro de steps.
    job = workflow.read_text(encoding="utf-8").split("  contra-la-api-viva:", 1)[1]
    assert "    if: github.event_name == 'push'" in job.splitlines()
