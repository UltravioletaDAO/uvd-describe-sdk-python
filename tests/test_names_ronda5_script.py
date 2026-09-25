"""El script que graba las fixtures sigue sirviendo (P2 de la ronda 5 del PR #6).

Desde la ronda 4 el script estaba roto y ningún test lo veía: importaba
`run_sync` (borrado) —`ImportError` hasta en `--help`— y su `Grabadora` era un
transporte sólo-sync, que `NameResolver` rechaza. El cuerpo del PR decía que
«queda en el repo» usable.

Correrlo de verdad sale a la red, así que acá se lo corre contra la propia
grabación: la `Grabadora` envuelve al reproductor en vez de a la red, con las
URLs de mentira de `names_replay` y sin pausa. Regrabar una fixture desde sí
misma tiene que dar la MISMA fixture —los mismos intercambios y el mismo
resultado—, y eso prueba el script entero salvo el socket. Mutación CF (la
`Grabadora` vuelta a `httpx.BaseTransport`): todos rojos.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

import httpx
import pytest

from .names_replay import FAKE_RPC, Replay, all_fixture_names, load

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "grabar_fixtures_names.py"


def _importar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grabar_fixtures_names", SCRIPT)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class _Reloj:
    """El `time` del script con el reloj de la grabación (el vencimiento de un
    nombre se evalúa contra `now`, como en `resolver_for`)."""

    monotonic = staticmethod(time.monotonic)  # antes de `def time`, que la tapa

    def __init__(self, ahora: float) -> None:
        self._ahora = ahora

    def time(self) -> float:
        return self._ahora


def test_el_script_se_importa_y_su_grabadora_le_sirve_al_motor() -> None:
    script = _importar()
    assert issubclass(script.Grabadora, httpx.AsyncBaseTransport)
    assert {nombre for nombre, *_ in script.ESCENARIOS} >= set(all_fixture_names())


@pytest.mark.parametrize("nombre", all_fixture_names())
def test_regrabar_una_fixture_desde_si_misma_da_la_misma_fixture(
    monkeypatch: pytest.MonkeyPatch, nombre: str
) -> None:
    script = _importar()
    fixture = load(nombre)
    monkeypatch.setattr(script, "RPC_PUBLICOS", dict(FAKE_RPC))
    monkeypatch.setattr(script, "CADENA_DE_URL", {url: chain for chain, url in FAKE_RPC.items()})
    monkeypatch.setattr(script, "PAUSA_S", 0.0)
    monkeypatch.setattr(script, "time", _Reloj(float(fixture["now"])))

    replay = Replay(fixture["exchanges"])
    grabadora = script.Grabadora(real=httpx.MockTransport(replay))
    opciones: Dict[str, Any] = {
        clave: tuple(valor) if isinstance(valor, list) else valor
        for clave, valor in (fixture.get("options") or {}).items()
    }
    salida = script.correr(fixture["op"], fixture["args"], opciones, grabadora)

    replay.assert_consumed()
    assert grabadora.intercambios == fixture["exchanges"]
    assert salida["result"] == fixture["result"]
