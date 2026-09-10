"""`freshness`: CUANDO describieron al sujeto, y por que son dos fechas.

Lo que este archivo defiende es que el SDK no colapse la distincion que el
indice acaba de abrir. `activity.last_rating_at` contesta *de cuando es este
score*; `freshness.last_received_feedback_at` contesta *describieron a este
sujeto hace poco*. Cuando el ultimo rating recibido fue revocado las dos
difieren, y un cliente que lea la primera creyendo que es la segunda decide con
un dato que nadie le mintio explicitamente.

Corre sin red, como toda la suite: `python -m pytest tests/test_freshness.py`.
"""

from __future__ import annotations

from uvd_describe_sdk.models import Freshness, parse_breakdown

from .conftest import BREAKDOWN


def test_las_dos_fechas_llegan_separadas() -> None:
    b = parse_breakdown(BREAKDOWN)
    assert b.freshness is not None
    assert b.freshness.last_received_feedback_at == "2026-09-05T00:00:00Z"
    assert b.freshness.last_eligible_rating_at == "2026-08-30T00:00:00Z"
    assert (
        b.freshness.last_received_feedback_at != b.freshness.last_eligible_rating_at
    ), "el fixture las tiene distintas a proposito"


def test_activity_conserva_su_semantica_de_alias() -> None:
    """`activity.last_rating_at` ES el ultimo ELEGIBLE, y sigue siendolo.

    Es el compromiso de compatibilidad del servidor y el SDK lo hereda: un
    consumidor que ya lo lee tiene que seguir leyendo lo mismo. Este test es lo
    que impide que alguien lo "mejore" apuntandolo al recibido.
    """
    b = parse_breakdown(BREAKDOWN)
    assert b.activity.last_rating_at == b.freshness.last_eligible_rating_at


def test_el_ambito_llega_entero() -> None:
    b = parse_breakdown(BREAKDOWN)
    assert b.freshness.scope.kind == "wallet"
    assert b.freshness.scope.direction == "received"
    assert b.freshness.scope.id == BREAKDOWN["wallet"]


def test_la_cobertura_parcial_viaja_con_su_denominador() -> None:
    """`partial` significa que la fecha publicada es un PISO.

    Sin los dos sumandos no se puede saber cuanto falta, y un consumidor que
    pinte "ultima descripcion" sin decir que faltan fechas afirma de mas.
    """
    fr = parse_breakdown(BREAKDOWN).freshness
    assert fr.timestamp_coverage == "partial"
    assert fr.dated_feedback_count == 7548
    assert fr.undated_feedback_count == 12
    # El subconjunto elegible tiene su PROPIO denominador y aca esta completo:
    # mezclarlos publicaria una cobertura que no es la de ninguno de los dos.
    assert fr.eligible_timestamp_coverage == "complete"


def test_sin_bloque_es_None_y_NUNCA_un_Freshness_vacio() -> None:
    """R1 aplicado al tiempo: un default que parece dato es como un cliente
    deja de poder distinguir "el servidor no lo publica" de "no tiene fechas"."""
    sin = dict(BREAKDOWN)
    sin.pop("freshness")
    assert parse_breakdown(sin).freshness is None


def test_un_bloque_basura_no_rompe_el_parseo() -> None:
    """La frescura es advisory: no puede tumbar una respuesta paga."""
    roto = dict(BREAKDOWN, freshness="hace 3 dias")
    b = parse_breakdown(roto)
    assert b.freshness is None
    assert b.final_score == BREAKDOWN["final_score"], "el dato principal sigue ahi"


def test_el_campo_crudo_sobrevive_en_raw() -> None:
    """Tolerancia aditiva: lo que el SDK no tipa igual llega al consumidor."""
    b = parse_breakdown(BREAKDOWN)
    assert b.raw["freshness"]["freshness_version"] == "freshness@1"


def test_el_sdk_no_calcula_texto_relativo() -> None:
    """La API publica UTC y nada mas, a proposito: un "hace 3 dias" serializado
    se congela en el primer cache. El SDK NO lo deriva -- lo hace el borde, con
    el reloj de quien mira. Este test es la promesa escrita como codigo.
    """
    campos = set(Freshness.__dataclass_fields__)
    assert not {c for c in campos if "ago" in c or "relative" in c}
