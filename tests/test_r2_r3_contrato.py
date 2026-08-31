"""R2 y R3 — el sello de composición y los códigos de caveat, atados por introspección.

R2 no se puede testear mirando una respuesta: se testea mirando **la superficie
pública del SDK**. Por eso este archivo recorre `__all__` y falla si aparece una
función que devuelva un número pelado.

Verificación discriminante hecha a mano al escribirlo (2026-08-30): se agregó
temporalmente

    def get_score(w: str) -> float: ...

a `uvd_describe_sdk/display.py`, se exportó, y `test_ningun_publico_devuelve_un_numero`
se puso **ROJO** con el mensaje que nombra la función. Después se removió. Un
test de contrato que nunca se vio rojo no prueba que el contrato exista.
"""

from __future__ import annotations

import inspect

import pytest

import uvd_describe_sdk as sdk
from uvd_describe_sdk import (
    CAVEAT_CODES_MEASURED_AT,
    FREE_GATE_CAVEAT_CODES,
    KNOWN_CAVEAT_CODES,
    CaveatCode,
    is_known,
)
from uvd_describe_sdk.models import parse_caveats, parse_wallet_reputation

from .conftest import WALLET_CON_REPUTACION

# ---------------------------------------------------------------------------
# R2 — ningún método devuelve un número pelado
# ---------------------------------------------------------------------------

#: Anotaciones de retorno que serían una violación de R2 **en cualquier lado**.
#: Se comparan como STRING porque todo el SDK usa `from __future__ import
#: annotations` y las anotaciones llegan sin evaluar.
#:
#: Son los tipos en los que se expresa un SCORE (y el dinero): un `float` pelado
#: es exactamente el `get_score()` que R2 existe para impedir.
_RETORNOS_PROHIBIDOS = {
    "float",
    "Optional[float]",
    "float | None",
    "Decimal",
    "Optional[Decimal]",
}

#: Enteros: prohibidos en una función SUELTA, permitidos como método/propiedad
#: de un modelo.
#:
#: ⚠️ CORRECCIÓN 2026-08-30, y se deja escrita en vez de borrarse. Hasta hoy
#: `int` y `Optional[int]` estaban en el set de arriba, o sea prohibidos en todas
#: partes — **contradiciendo el docstring de este mismo test**, que ya decía
#: «se permiten los `@property` de contadores/booleanos de los modelos: `int` y
#: `bool` no son scores». La contradicción nunca se vio porque ninguna superficie
#: devolvía un entero, hasta que llegó `WalletReputation.resolve_distinct_raters()`
#: (aporte de MeshRelay). La regla escrita era la correcta y la lista estaba de
#: más; se alinea la lista, no el docstring.
#:
#: Por qué la distinción es la correcta y no una excusa para pasar el test: R2
#: prohíbe **entregar el número en lugar del objeto que lo contextualiza**. Un
#: método de instancia no puede hacer eso — para llamarlo hay que TENER el objeto,
#: con su `policy_version` y sus `caveats` en la mano. Una función suelta sí:
#: `distinct_raters_of("0x…") -> int` devolvería el número y nada más, y sigue
#: prohibida. Y `distinct_raters` no es un score: es uno de los CALIFICADORES
#: cuya ausencia es la que vuelve rumor a un score.
_ENTEROS = {"int", "Optional[int]", "int | None"}


def _publicos():
    """Cada superficie pública, con si es miembro de una clase o función suelta."""
    for nombre in sdk.__all__:
        objeto = getattr(sdk, nombre)
        if inspect.isfunction(objeto):
            yield f"{nombre}()", objeto, False
        elif inspect.isclass(objeto):
            for metodo, fn in vars(objeto).items():
                if metodo.startswith("_"):
                    continue
                if inspect.isfunction(fn):
                    yield f"{nombre}.{metodo}()", fn, True
                elif isinstance(fn, property) and fn.fget is not None:
                    yield f"{nombre}.{metodo}", fn.fget, True


def test_ningun_publico_devuelve_un_numero() -> None:
    """El sello de composición, cableado.

    «La tesis del producto es que un score sin sus calificadores es un rumor; un
    SDK con un `get_score()` que devuelve un float la borra.» Quien quiera el
    número lo saca del objeto a mano — y ese gesto queda escrito en SU código.

    Se permiten los `@property` de contadores/booleanos de los modelos: `int` y
    `bool` no son scores. Lo que R2 prohíbe es una función que **entregue el
    número** en lugar del objeto que lo contextualiza.
    """
    prohibidos = {p.replace(" ", "") for p in _RETORNOS_PROHIBIDOS}
    enteros = {p.replace(" ", "") for p in _ENTEROS}
    violaciones = []
    for etiqueta, fn, es_miembro in _publicos():
        anotacion = inspect.signature(fn).return_annotation
        if anotacion is inspect.Signature.empty:
            continue
        texto = anotacion if isinstance(anotacion, str) else getattr(anotacion, "__name__", "")
        limpio = texto.replace(" ", "")
        if limpio in prohibidos or (limpio in enteros and not es_miembro):
            violaciones.append(f"{etiqueta} -> {texto}")
    assert not violaciones, (
        "R2 violada: estas superficies públicas devuelven un número pelado, sin "
        f"policy_version ni caveats: {violaciones}"
    )


def test_todo_resultado_lleva_su_policy_version_y_sus_caveats() -> None:
    """El otro lado de R2: los objetos SÍ traen el sello."""
    rep = parse_wallet_reputation(WALLET_CON_REPUTACION)
    assert rep.policy_version == "equal-weight-per-chain@2"
    assert rep.caveats == []
    assert rep.source == "chain_rankings_mv"
    assert rep.refreshed_at is not None
    # Y el score no se puede obtener sin el objeto que lo acompaña.
    assert rep.global_score == 100.0


def test_no_existe_un_atajo_que_devuelva_solo_el_score() -> None:
    """Ni `get_score`, ni `score_of`, ni un `__float__` en los modelos.

    Un `__float__` sería la puerta trasera perfecta: `float(rep)` devolvería el
    número pelado sin que ninguna anotación de retorno lo delatara.
    """
    for nombre in sdk.__all__:
        assert "get_score" not in nombre and "score_of" not in nombre, nombre
    for modelo in (sdk.WalletReputation, sdk.Breakdown, sdk.AgentReputation):
        assert not hasattr(modelo, "__float__"), modelo


# ---------------------------------------------------------------------------
# R3 — los caveat codes, como contrato exportado
# ---------------------------------------------------------------------------


def test_las_ocho_estan_y_son_ocho() -> None:
    """docs.describe.net: «The eight codes are the whole set, and it is frozen
    by a test — adding or renaming one is deliberately red.»

    Este es el espejo de aquel test, del lado del cliente. Si el servicio agrega
    una novena, esto se pone rojo y hay que venir a leer qué corte nombra.
    """
    assert len(KNOWN_CAVEAT_CODES) == 8
    assert KNOWN_CAVEAT_CODES == {
        "no-score",
        "concentration-degraded",
        "single-rater",
        "few-raters",
        "top-client-share",
        "campaign-per-rater",
        "self-rated",
        "burn-address",
    }
    assert CAVEAT_CODES_MEASURED_AT == "2026-08-30"


def test_el_subset_de_la_puerta_gratis() -> None:
    """En `/wallets/{w}/chains` la lista es un SUBSET (hoy sólo `burn-address`).

    Una lista vacía ahí **no promete** que la descomposición paga esté limpia, y
    publicarlo es lo que impide leer ese silencio como un veredicto.
    """
    assert FREE_GATE_CAVEAT_CODES == {CaveatCode.BURN_ADDRESS}
    assert FREE_GATE_CAVEAT_CODES < KNOWN_CAVEAT_CODES


def test_un_codigo_nuevo_del_servicio_llega_entero_y_no_rompe() -> None:
    """🔴 La razón por la que `code` es `str` y NO un `Enum`.

    Un `Enum` cerrado haría que un código nuevo del servicio **rompa o
    desaparezca**, y descartar un caveat es descartar la advertencia. El
    precedente es del propio servicio, del otro lado del cable: `Facet.direction`
    viajaba y FastAPI lo descartaba en silencio por no estar declarado — HTTP
    200, forma correcta, dato ausente.
    """
    caveats = parse_caveats(
        [{"code": "codigo-del-futuro", "text": "algo que este SDK no conoce"}]
    )
    assert len(caveats) == 1
    assert caveats[0].code == "codigo-del-futuro"  # llegó ENTERO
    assert is_known("codigo-del-futuro") is False  # y se sabe que es nuevo
    assert is_known(CaveatCode.BURN_ADDRESS) is True


def test_se_ramifica_por_code_no_por_text() -> None:
    """El `text` puede cambiar sin aviso; el `code` no. Lo declara su schema."""
    a = parse_caveats([{"code": "burn-address", "text": "Esta wallet es una direccion…"}])
    b = parse_caveats([{"code": "burn-address", "text": "TEXTO REESCRITO EN INGLÉS"}])
    assert a[0].code == b[0].code == CaveatCode.BURN_ADDRESS
    assert a[0].text != b[0].text  # y al código no le pasó nada


def test_caveat_malformado_se_descarta_sin_tumbar_la_lectura() -> None:
    """Un advisory roto no es razón para perder una lectura que llegó bien."""
    caveats = parse_caveats([{"text": "sin code"}, None, {"code": "self-rated"}, 42])
    assert [c.code for c in caveats] == ["self-rated"]


def test_formato_viejo_de_strings_pelados_sigue_siendo_legible() -> None:
    """Antes del 2026-08-28 los caveats eran strings. Un índice viejo o un mock
    que copie ese formato no puede quedar sin advertencias."""
    caveats = parse_caveats(["burn-address"])
    assert caveats[0].code == "burn-address"
    assert caveats[0].text == ""


def test_caveat_code_no_se_instancia() -> None:
    """Es un namespace de constantes. Instanciarlo sugeriría un Enum, que es
    justo lo que no es."""
    with pytest.raises(TypeError):
        CaveatCode()
