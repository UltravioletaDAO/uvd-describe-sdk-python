"""El formato canónico al mostrar un score — R8. Una línea, y costó una medición.

    f"{round(x, 2):g}"

**Dos decimales, ceros finales recortados.** `86.65`, `84.7`, `87` — nunca
`82.0`, nunca `86.653045`.

────────────────────────────────────────────────────────────────────────────
POR QUÉ ESTA REGLA EXISTE (no es gusto, es medición)
────────────────────────────────────────────────────────────────────────────
El 2026-08-29 los tres consumidores del ecosistema renderizaban **el mismo
número de tres formas distintas**: `86.653045`, `86.7`, `86`. Un campo, tres
strings. Se resolvió midiendo sobre **47 scores reales distintos** del índice:

    redondear a 0 decimales fusiona 23 pares de agentes DISTINTOS en el mismo string
    redondear a 1 decimal   fusiona  4 pares
    redondear a 2 decimales fusiona  1 par

Y el recorte importa tanto como la cantidad: dos superficies «de acuerdo en 1
decimal» igual imprimían `82.0` y `82` para el mismo agente.

**Caso testigo, el que separa las tres reglas candidatas:** el agente con score
`83.0` sale `83` — donde `toFixed(2)`/`:.2f` imprimiría `83.00` y `toFixed(1)`
imprimiría `83.0`. Es el primer caso que hay que testear, y está verificado en
tres superficies independientes (docs.describe.net §«Displaying a score»).

El gemelo en JavaScript, byte-idéntico en resultado:

    String(parseFloat(x.toFixed(2)))

────────────────────────────────────────────────────────────────────────────
LO QUE ESTO **NO** ES
────────────────────────────────────────────────────────────────────────────
Es una convención de **display**. La API sigue sirviendo el número con toda su
precisión (seis decimales), y **con lo que se calcula es el número, nunca el
string**. Si alguien compara scores comparando strings, ordenó lexicográfico y
`"9"` le quedó arriba de `"86.65"`.

🔴 **Y la trampa que este módulo existe para no dejar pasar:** `0.0` formatea
como `"0"`. Un score que no existe (`None`) formateado como `"0"` sería
exactamente la mentira de R1 —«no hay evidencia» impreso como «lo calificaron
pésimo»— y es un error de una línea. Por eso `format_score(None)` **no
devuelve un número**: devuelve el placeholder, y el default es un guion largo,
no un cero.
"""

from __future__ import annotations

from typing import Optional

#: Lo que se muestra cuando no hay score. **Nunca un dígito.** Un `"0"` acá
#: convertiría «sin datos» en «malo», que es la invariante 7 del servicio rota
#: en la última línea del camino.
NO_SCORE_PLACEHOLDER = "—"


def format_score(score: Optional[float], *, placeholder: str = NO_SCORE_PLACEHOLDER) -> str:
    """Formatear un score para mostrarlo. `None` → `placeholder`, nunca `"0"`.

    >>> format_score(86.653045)
    '86.65'
    >>> format_score(84.7)
    '84.7'
    >>> format_score(87)
    '87'
    >>> format_score(82.0)
    '82'
    >>> format_score(83.0)        # el caso testigo
    '83'
    >>> format_score(0.0)         # un cero MEDIDO sí es "0"
    '0'
    >>> format_score(None)        # la ausencia NO
    '—'

    Nota medida (2026-08-30): `:g` cae en notación científica arriba de seis
    dígitos significativos (`1234567.891` → `'1.23457e+06'`). Es irrelevante
    para un score, que vive en `[0, 100]`, y se deja escrito para que nadie
    reuse esta función como formateador de propósito general.
    """
    if score is None:
        return placeholder
    return f"{round(score, 2):g}"
