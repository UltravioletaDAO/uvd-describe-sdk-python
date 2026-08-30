"""R8 — el formato canónico, con sus cuatro casos y el testigo.

Los cuatro que la guía publicada manda pinear, y el testigo que separa las tres
reglas candidatas (`83.0 → "83"`, no `"83.00"` ni `"83.0"`).
"""

from __future__ import annotations

import pytest

from uvd_describe_sdk import format_score


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (86.653045, "86.65"),
        (84.7, "84.7"),
        (87, "87"),
        (82.0, "82"),
    ],
)
def test_los_cuatro_casos_de_la_guia(valor, esperado) -> None:
    assert format_score(valor) == esperado


def test_el_caso_testigo_83() -> None:
    """🔴 El único valor que distingue las tres reglas candidatas.

    Verificado en tres superficies independientes el 2026-08-29:
        `:.2f`  daría "83.00"
        `:.1f`  daría "83.0"
        el canónico da "83"
    """
    assert format_score(83.0) == "83"
    assert f"{83.0:.2f}" == "83.00"  # lo que NO se hace
    assert f"{83.0:.1f}" == "83.0"  # lo que TAMPOCO


def test_dos_decimales_y_no_uno() -> None:
    """La cantidad salió de una medición, no de un gusto.

    Sobre 47 scores reales distintos: 0 decimales fusiona 23 pares de agentes
    DISTINTOS en el mismo string, 1 decimal fusiona 4, 2 decimales fusiona 1.
    Este par lo demuestra en chico: con 1 decimal serían el mismo string.
    """
    assert format_score(86.653045) == "86.65"
    assert format_score(86.6512) == "86.65"
    assert format_score(86.649) == "86.65"
    # con 1 decimal, 86.65 y 86.71 colapsarían a "86.7" los dos
    assert format_score(86.71) == "86.71"
    assert format_score(86.65) != format_score(86.71)


def test_gemelo_de_javascript() -> None:
    """El mismo resultado que `String(parseFloat(x.toFixed(2)))`.

    Se reimplementa la regla JS en Python y se compara: si algún día alguien
    "mejora" la fórmula de un lado, este test dice que los dos SDK dejaron de
    imprimir lo mismo — que es el bug original que R8 vino a cerrar.
    """

    def js_equivalente(x: float) -> str:
        # `toFixed(2)` → string con 2 decimales; `parseFloat` recorta ceros;
        # `String` lo vuelve string.
        return repr(float(f"{x:.2f}")).rstrip("0").rstrip(".") or "0"

    for valor in (86.653045, 84.7, 87.0, 82.0, 83.0, 0.0, 99.982977, 100.0):
        assert format_score(valor) == js_equivalente(valor), valor


def test_none_no_es_cero() -> None:
    """R1 en el formateador. Un `"0"` acá sería la invariante rota en un carácter."""
    assert format_score(None) == "—"
    assert format_score(None, placeholder="sin datos") == "sin datos"
    assert format_score(0.0) == "0"


def test_boundary_medido_de_g() -> None:
    """`:g` cae en notación científica arriba de 6 dígitos significativos.

    Irrelevante para un score (vive en [0,100]) y se deja escrito para que nadie
    reuse `format_score` como formateador de propósito general.
    """
    assert format_score(1234567.891) == "1.23457e+06"
    assert format_score(100.0) == "100"
    assert format_score(0.005) == "0.01"
