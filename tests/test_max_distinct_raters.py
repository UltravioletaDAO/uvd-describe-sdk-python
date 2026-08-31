"""② `max_distinct_raters()` — las DOS trampas medidas por MeshRelay.

Aporte de **MeshRelay** (`#agents`, **2026-08-30**). Lo que vale no son las
siete líneas del helper sino las dos trampas que lo rodean, y este archivo
existe para que ninguna de las dos se pueda «simplificar» sin ponerse rojo:

    TRAMPA 1  SUMAR por cadena DOBLE-CUENTA a quien calificó en dos redes.
              Medido por MeshRelay: la wallet de karma-hello lee 9 distinct
              raters global y 11 sumando las cadenas.

    TRAMPA 2  El MÁXIMO SUBESTIMA. Medido por MeshRelay: 3 raters en base + 4
              DISTINTOS en avalanche = 7 reales, y el máximo dice 4.

Las dos formas obvias de derivarlo están mal, en direcciones opuestas. De ahí
la regla: **el máximo es SÓLO cota inferior / fallback; el global es la
respuesta.**

Corroborado de nuestro lado el 2026-08-30 contra `api.describe.net`: en las 3
de 3 wallets multi-cadena del `GET /leaderboard` las dos trampas disparan
(0xcc28cee3… global 129 / suma 134 / máx 113 · 0xf9d1d63f… 1542 / 1555 / 1513 ·
0x0d68a153… 40 / 41 / 35). No es un caso de borde: es todas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from uvd_describe_sdk.models import WalletReputation, parse_wallet_reputation


def _wallet(global_raters: Optional[int], por_cadena: List[int]) -> Any:
    """Una respuesta de `/wallets/{w}/chains` con los raters que se le pidan.

    `global_raters=None` monta el caso en que el índice NO mandó el agregado —
    el único en el que el fallback tiene derecho a existir.
    """
    body: Dict[str, Any] = {
        "wallet": "0x00000000000000000000000000000000000000aa",
        "chains": [
            {
                "network": red,
                "agent_count": 1,
                "agent_ids": ["1"],
                "final_score": 90.0,
                "total_reviews": n,
                "distinct_raters": n,
            }
            for red, n in zip(("base", "avalanche", "celo"), por_cadena)
        ],
        "identity_count": 1,
        "chains_with_identity": len(por_cadena),
        "total_reviews": sum(por_cadena),
        "global_score": 90.0,
        "policy_version": "equal-weight-per-chain@2",
    }
    if global_raters is not None:
        body["distinct_raters"] = global_raters
    return parse_wallet_reputation(body)


# ---------------------------------------------------------------------------
# 🔴 TRAMPA 1 — la suma doble-cuenta, y el helper NO suma
# ---------------------------------------------------------------------------


def test_prefiere_el_global_y_NO_suma_las_cadenas() -> None:
    """🔴 DISCRIMINANTE de la trampa 1, con los números de MeshRelay.

    karma-hello: **9** global, **11** sumando. Los dos números están vivos en la
    misma respuesta, así que un helper que sume pasa cualquier test que sólo
    compruebe «devolvió un int > 0». Este afirma las dos mitades: que da 9 **y
    que no da 11**. Se pone rojo con `sum(...)`, que es la implementación que
    parece obvia.
    """
    rep = _wallet(global_raters=9, por_cadena=[6, 5])  # suma 11, global 9

    assert rep.max_distinct_raters() == 9
    assert rep.max_distinct_raters() != 11, (
        "sumar por cadena doble-cuenta a quien calificó en dos redes "
        "(MeshRelay, 2026-08-30: karma-hello lee 9 global y 11 sumando)"
    )


def test_el_global_gana_aunque_el_maximo_sea_mayor() -> None:
    """El global manda SIEMPRE que esté, no sólo cuando conviene.

    Un `max(global, máximo)` daría el mismo resultado en el caso de arriba y
    sería igual de falso: lo que hace autoridad al global no es ser el más
    grande sino ser el único contado sobre el conjunto de calificadores.
    """
    rep = _wallet(global_raters=9, por_cadena=[40])

    assert rep.max_distinct_raters() == 9


# ---------------------------------------------------------------------------
# 🔴 TRAMPA 2 — el máximo subestima, y sólo vale como cota inferior
# ---------------------------------------------------------------------------


def test_sin_global_el_maximo_es_una_COTA_INFERIOR_que_subestima() -> None:
    """🔴 DISCRIMINANTE de la trampa 2, con el caso de MeshRelay.

    3 raters en `base` + 4 DISTINTOS en `avalanche` son **7** reales y el máximo
    dice **4**. El test afirma el 4 —porque es lo que el fallback tiene que
    devolver— y deja escrito, ejecutable, que 4 < 7: el valor es una cota
    inferior, jamás la respuesta.

    Rojo si alguien «arregla» el fallback sumando (daría 7 y parecería mejor,
    pero sólo acierta cuando los conjuntos son disjuntos, que es justo lo que
    nadie sabe desde acá).
    """
    rep = _wallet(global_raters=None, por_cadena=[3, 4])

    cota = rep.max_distinct_raters()
    assert cota == 4
    assert cota is not None and cota < 7, (
        "el máximo SUBESTIMA cuando los calificadores de cada cadena son "
        "distintos (MeshRelay, 2026-08-30): 3 en base + 4 en avalanche = 7"
    )


def test_el_fallback_solo_entra_cuando_el_global_no_vino() -> None:
    """La condición de entrada del fallback, afirmada sola.

    Es el contraste que hace que los dos tests de arriba prueben algo distinto:
    mismo `por_cadena`, y el resultado cambia SÓLO por si vino el global.
    """
    assert _wallet(global_raters=9, por_cadena=[3, 4]).max_distinct_raters() == 9
    assert _wallet(global_raters=None, por_cadena=[3, 4]).max_distinct_raters() == 4


# ---------------------------------------------------------------------------
# R1 — sin datos no es cero, tampoco acá
# ---------------------------------------------------------------------------


def test_sin_global_y_sin_cadenas_es_None_nunca_cero() -> None:
    """🔴 R1 aplicada al helper.

    Sin global y sin cadenas no sabemos nada, y un `0` afirmaría «nadie la
    calificó» sobre una wallet de la que no tenemos un solo dato — la misma
    mentira que R1 persigue en `global_score`. Se pone rojo con
    `max(..., default=0)`, que es la forma en que Python te invita a escribir
    este bug.
    """
    rep = _wallet(global_raters=None, por_cadena=[])

    assert rep.max_distinct_raters() is None


def test_un_global_en_cero_SI_es_un_cero_legitimo() -> None:
    """El contraste de R1: acá el 0 sí es un hecho contado, y se respeta.

    Sin este test, «devolvé None cuando el número sea 0» pasaría en verde y
    borraría un dato real — la sobre-corrección de la regla de arriba.
    """
    rep = _wallet(global_raters=0, por_cadena=[0])

    assert rep.max_distinct_raters() == 0


def test_el_docstring_deja_escritas_las_dos_trampas() -> None:
    """Las siete líneas sin las dos trampas son un helper que se usa mal.

    El aporte de MeshRelay fue explícito en que lo que vale es la advertencia, y
    una advertencia que vive sólo en un canal de chat se pierde. Esto ata la
    procedencia al código: rojo si alguien recorta el docstring a «devuelve los
    raters distintos».
    """
    doc = WalletReputation.max_distinct_raters.__doc__
    assert doc is not None
    for esperado in ("MeshRelay", "2026-08-30", "COTA INFERIOR", "doble-cuenta", "SUBESTIMA"):
        assert esperado.lower() in doc.lower(), f"falta «{esperado}» en el docstring"
