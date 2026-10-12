"""Regresión REF-DNSDK-10: contratos SINTÉTICOS con rutas que se solapan.

La selección no depende del orden del JSON ni de la query que acepte cada
operación. Una llamada a /items/search nunca cubre por accidente getItem.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from uvd_sdk_map import resolver, verificar_cobertura


def contrato_rutas(paths: Dict[str, Any], prefijo: str = "") -> Dict[str, Any]:
    return {"servers": [{"url": "https://api.example" + prefijo}], "paths": paths}


@pytest.mark.parametrize("invertir", [False, True])
@pytest.mark.parametrize("prefijo", ["", "/api/v2"])
def test_la_literal_no_cubre_la_plantilla(invertir: bool, prefijo: str) -> None:
    paths = {
        "/items/{id}": {"get": {"operationId": "getItem", "responses": {}}},
        "/items/search": {"get": {"operationId": "searchItems", "responses": {}}},
    }
    if invertir:
        paths = dict(reversed(list(paths.items())))
    spec = contrato_rutas(paths, prefijo)
    llamada = ("GET", "api.example", prefijo + "/items/search", ())
    grabado = [("search", llamada)]
    assert resolver(spec, llamada) == "searchItems"
    assert verificar_cobertura(spec, grabado, {"getItem": "sin método"}) == {
        "mapeadas": {"searchItems": "search"}, "fuera": {"getItem": "sin método"},
    }
    with pytest.raises(AssertionError, match="sin clasificar.*getItem"):
        verificar_cobertura(spec, grabado, {})
    # Reproducción del falso verde del refutador: este fuera es incorrecto.
    with pytest.raises(AssertionError, match="mapeadas y fuera a la vez.*searchItems"):
        verificar_cobertura(spec, grabado, {"searchItems": "sin método"})
    grabado.append(("read", ("GET", "api.example", prefijo + "/items/42", ())))
    assert verificar_cobertura(spec, grabado, {})["mapeadas"] == {
        "getItem": "read", "searchItems": "search",
    }


@pytest.mark.parametrize("invertir", [False, True])
@pytest.mark.parametrize("generica,especifica,ruta", [
    ("/{entity}/{id}", "/items/{id}", "/items/42"),
    ("/{entity}/{id}/details", "/items/{id}/details", "/items/42/details"),
    # Se cuentan segmentos literales, no caracteres ni número de parámetros.
    ("/{entity}/{id}.json", "/items/{name}.{ext}", "/items/café.json"),
    ("/files/x{id}/{child}", "/files/{id}/details", "/files/x42/details"),
])
def test_gana_la_plantilla_con_mas_segmentos_literales(
    invertir: bool, generica: str, especifica: str, ruta: str
) -> None:
    paths = {
        generica: {"get": {"operationId": "generic"}},
        especifica: {"get": {"operationId": "specific"}},
    }
    if invertir:
        paths = dict(reversed(list(paths.items())))
    assert resolver(contrato_rutas(paths), ("GET", "api.example", ruta, ())) == "specific"


@pytest.mark.parametrize("invertir", [False, True])
@pytest.mark.parametrize("otra", ["/{entity}/search", "/items/{name}"])
def test_empate_de_rutas_es_error_aunque_la_query_desempate(invertir: bool, otra: str) -> None:
    paths = {
        "/items/{id}": {"get": {"operationId": "first"}},
        otra: {"get": {"operationId": "second", "parameters": [{"in": "query", "name": "q"}]}},
    }
    if invertir:
        paths = dict(reversed(list(paths.items())))
    spec = contrato_rutas(paths)
    with pytest.raises(ValueError, match="rutas ambiguas") as exc:
        verificar_cobertura(spec, [("search", ("GET", "api.example", "/items/search", ("q",)))], {})
    assert "/items/{id}" in str(exc.value)
    assert otra in str(exc.value)
    # Un empate entre candidatas inferiores no invalida una ganadora literal.
    spec["paths"]["/items/search"] = {
        "get": {"operationId": "search", "parameters": [{"in": "query", "name": "q"}]},
    }
    assert resolver(spec, ("GET", "api.example", "/items/search", ("q",))) == "search"


@pytest.mark.parametrize("invertir", [False, True])
@pytest.mark.parametrize("literal", ["search", "café", "0Xab", "a.b"])
def test_la_query_de_la_plantilla_no_rescata_la_literal(invertir: bool, literal: str) -> None:
    paths = {
        "/items/{id}": {
            "get": {"operationId": "getItem", "parameters": [{"in": "query", "name": "lang"}]},
        },
        "/items/" + literal: {"get": {"operationId": "literal"}},
    }
    if invertir:
        paths = dict(reversed(list(paths.items())))
    spec = contrato_rutas(paths)
    ruta = "/items/" + literal
    assert resolver(spec, ("GET", "api.example", ruta, ())) == "literal"
    assert resolver(spec, ("GET", "api.example", ruta, ("lang",))) is None


@pytest.mark.parametrize("item", [{}, {"post": {"operationId": "searchItems"}}])
def test_literal_sin_el_verbo_no_cae_en_la_plantilla(item: Dict[str, Any]) -> None:
    spec = contrato_rutas({
        "/items/{id}": {"get": {"operationId": "getItem"}},
        "/items/search": item,
    })
    assert resolver(spec, ("GET", "api.example", "/items/search", ())) is None
    assert resolver(spec, ("GET", "api.example", "/items/42", ())) == "getItem"


def test_prefijo_mas_largo_y_server_sin_candidatas() -> None:
    spec = contrato_rutas({
        "/items/{id}": {"get": {"operationId": "getItem"}},
        "/v1/items/search": {"get": {"operationId": "rootSearch"}},
    })
    spec["servers"].append({"url": "https://api.example/v1"})
    # Se preserva la prioridad preexistente del prefijo de servidor más largo.
    assert resolver(spec, ("GET", "api.example", "/v1/items/search", ())) == "getItem"
    # Si ese prefijo no da ninguna candidata, se prueba el siguiente servidor.
    spec["paths"] = {"/v1/items/search": {"get": {"operationId": "rootSearch"}}}
    assert resolver(spec, ("GET", "api.example", "/v1/items/search", ())) == "rootSearch"


def test_una_ruta_sin_verbo_no_se_rescata_con_otro_server() -> None:
    spec = contrato_rutas({
        "/items/search": {"post": {"operationId": "postSearch"}},
        "/api/items/search": {"get": {"operationId": "rootSearch"}},
    })
    spec["servers"].append({"url": "https://api.example/api"})
    assert resolver(spec, ("GET", "api.example", "/api/items/search", ())) is None
