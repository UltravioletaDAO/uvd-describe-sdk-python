"""Lo que la cadena contestó el 2026-09-24, reproducido — sync y async.

Cada fixture de `tests/fixtures/names/` es una grabación real
(`scripts/grabar_fixtures_names.py`). Este archivo corre cada una por las DOS
variantes del resolver y exige tres cosas:

1. el resultado es EL MISMO que dio en vivo (`result` de la fixture);
2. se pidieron exactamente los intercambios grabados, en orden, y todos;
3. la variante async y la sync coinciden — la política vive una sola vez
   (`names/_proto.py`) y este test es lo que lo sostiene.

Y arriba de todo, los cuatro hechos que el encargo pidió ver en los tests:

    jesse.base.eth        → 0x2211d1D0020DAEA8039E46Cf1367962070d77DA9 (L1 + CCIP)
    ultravioletadao.eth   → 0xe4dc963c56979E0260fc146b87eE24F18220e545
    reverse de 0xe4dc…545 → 0xultravioleta.eth (confirmado forward)
    0xultravioletadao.eth → not_found, sin excepción
"""

from __future__ import annotations

import pytest

from .names_replay import Replay, all_fixture_names, call_op, load, resolver_for

_RESOLVER_FIXTURES = [n for n in all_fixture_names() if load(n)["op"] != "poseidon"]


def _replay(name: str, *, asynchronous: bool) -> dict:
    fixture = load(name)
    replay = Replay(fixture["exchanges"])
    resolver = resolver_for(fixture, replay)
    result = call_op(resolver, fixture["op"], fixture["args"], asynchronous=asynchronous)
    replay.assert_consumed()
    return result.to_dict()


@pytest.mark.parametrize("name", _RESOLVER_FIXTURES)
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_cada_grabacion_da_lo_mismo_que_dio_en_vivo(name: str, asynchronous: bool) -> None:
    assert _replay(name, asynchronous=asynchronous) == load(name)["result"]


def test_jesse_base_eth_resuelve_por_L1_y_CCIP() -> None:
    fixture = load("resolve_jesse_base_eth")
    result = _replay("resolve_jesse_base_eth", asynchronous=False)
    assert result["address"] == "0x2211d1D0020DAEA8039E46Cf1367962070d77DA9"
    assert result["system"] == "basenames"
    assert result["verified_onchain"] is True
    # Por L1 + CCIP: el registro de L1, el gateway de Coinbase, y el callback que
    # verifica la firma — nada del registro de ENS «dentro de Base», que no existe
    # (el bug de karma-hello).
    cadenas = {e["chain"] for e in fixture["exchanges"] if e["kind"] == "rpc"}
    gateways = [e["url"] for e in fixture["exchanges"] if e["kind"] == "http"]
    assert "eip155:1" in cadenas
    assert gateways and all(u.startswith("https://api.coinbase.com/") for u in gateways)
    lecturas_en_base = [
        e for e in fixture["exchanges"] if e["kind"] == "rpc" and e["chain"] == "eip155:8453"
    ]
    # Lo ÚNICO que se lee en Base es el vencimiento del registro de Basenames.
    assert len(lecturas_en_base) == 1


def test_ultravioletadao_eth_y_su_reverse() -> None:
    forward = _replay("resolve_ultravioletadao_eth", asynchronous=False)
    assert forward["address"] == "0xe4dc963c56979E0260fc146b87eE24F18220e545"
    assert forward["error"] is None

    reverse = _replay("reverse_0xe4dc_ultravioleta", asynchronous=True)
    assert reverse["normalized"] == "0xultravioleta.eth"
    assert reverse["address"] == "0xe4dc963c56979E0260fc146b87eE24F18220e545"
    assert reverse["error"] is None and reverse["verified_onchain"] is True


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_0xultravioletadao_eth_no_existe_y_no_es_una_excepcion(asynchronous: bool) -> None:
    result = _replay("resolve_0xultravioletadao_eth_no_existe", asynchronous=asynchronous)
    assert result["error"] == "not_found"
    assert result["address"] is None
    assert result["tried"] == ["ens"]
