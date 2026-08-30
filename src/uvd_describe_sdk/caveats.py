"""Los códigos de caveat, publicados como contrato — R3.

**Se ramifica por `code`, JAMÁS por `text`.** No es una preferencia de estilo:
lo declara el schema del servicio en la descripción de su propio tipo `Caveat`
(leído vivo el 2026-08-30 en `api.describe.net/openapi.json`):

    code: "Stable identifier of the trap that fired. Branch on THIS, never on
           `text`. Codes are permanent; text is not."
    text: "Spanish prose meant to be shown to whoever is deciding. May be
           rewritten, re-measured or translated without notice."

Y la guía publicada agrega el porqué: un caveat es **advisory por
construcción** — nombra un corte, no mueve un score ni un precio, y por eso no
bumpea `policy_version` (docs.describe.net §«caveats[] — the same rules,
already fired»).

Se exportan acá para que el consumidor **no los tipee**. Un `if c.code ==
"burn-adress"` con un typo no falla: simplemente nunca entra, y el caso que el
código creía cubrir queda descubierto en silencio. Esa es la clase de bug que
una constante importada mata.

────────────────────────────────────────────────────────────────────────────
🔴 POR QUÉ ESTO **NO** ES UN `Enum`, Y ES LA DECISIÓN QUE MÁS IMPORTA ACÁ
────────────────────────────────────────────────────────────────────────────
`Caveat.code` se tipa `str`, no `CaveatCode`. Un `Enum` cerrado haría que el
día que describe agregue un código nuevo, el SDK **rompa o lo descarte** — y
descartar un caveat es descartar la advertencia, que es literalmente lo
contrario de para qué existe el campo.

El precedente es medido y es del propio servicio: `Facet.direction` viajaba en
la respuesta y FastAPI lo descartaba en silencio por no estar declarado — HTTP
200, forma correcta, dato ausente (regla 5 de `F0-describe-sdk.md:206-211`). Un
`Enum` acá reproduce ese bug del lado del cliente.

Entonces el contrato es de dos piezas:
  * `CaveatCode.*` — las ocho constantes, para no tipear strings a mano.
  * `KNOWN_CAVEAT_CODES` — el set congelado, para PREGUNTAR si un código es
    conocido. Uno desconocido no es un error: es un caveat nuevo que igual hay
    que mostrar.

────────────────────────────────────────────────────────────────────────────
LAS OCHO, Y DÓNDE ESTÁ EL SET COMPLETO
────────────────────────────────────────────────────────────────────────────
Copiadas de docs.describe.net (leído el 2026-08-30), que dice: *«The eight
codes are the whole set, and it is frozen by a test — adding or renaming one is
deliberately red»*. O sea: el set vive del lado del servicio y allá hay un test
que lo congela. Acá se refleja, con su fecha, y `test_caveats.py` compara este
espejo contra el conteo declarado para que una copia a medias se ponga roja.
"""

from __future__ import annotations

from typing import FrozenSet

#: Fecha en que este espejo se leyó de la fuente. Toda cifra o se lee viva o
#: lleva fecha (regla de la casa) — y un set de códigos es una cifra.
CAVEAT_CODES_MEASURED_AT = "2026-08-30"


class CaveatCode:
    """Las ocho constantes. Contenedor de strings, **no** un Enum (ver módulo).

    No se instancia: es un namespace para que el import sea explícito y el
    autocompletado los ofrezca.
    """

    #: No hay score que leer. **null, nunca cero** — invariante 7 del servicio.
    NO_SCORE = "no-score"

    #: `concentration` volvió `null`: la señal está caída, no ausente. La
    #: diferencia importa — «no lo pude medir» no es «no está concentrado».
    CONCENTRATION_DEGRADED = "concentration-degraded"

    #: Exactamente un calificador distinto.
    SINGLE_RATER = "single-rater"

    #: Por debajo de `reading_policy.min_raters` (vivo el 2026-08-30: 3, leído
    #: de `GET /health` — nunca se tipea acá, ver `IndexHealth.reading_policy`).
    FEW_RATERS = "few-raters"

    #: En o por encima de `reading_policy.top_share`.
    TOP_CLIENT_SHARE = "top-client-share"

    #: En o por encima de `reading_policy.campaign_per_rater` ratings por
    #: calificador.
    CAMPAIGN_PER_RATER = "campaign-per-rater"

    #: El sujeto se calificó a sí mismo. El gap se publica, no se juzga.
    SELF_RATED = "self-rated"

    #: El sujeto es una dirección de quema conocida: calificaciones on-chain
    #: reales sobre algo que nadie controla. Es el ÚNICO que hoy dispara en la
    #: puerta gratis `GET /wallets/{w}/chains`.
    BURN_ADDRESS = "burn-address"

    def __init__(self) -> None:  # pragma: no cover - defensa, no lógica
        raise TypeError("CaveatCode es un namespace de constantes, no se instancia")


#: El set congelado. Se PREGUNTA, no se valida contra él: un código fuera de
#: acá es un caveat nuevo del servicio, y hay que mostrarlo igual.
KNOWN_CAVEAT_CODES: FrozenSet[str] = frozenset(
    {
        CaveatCode.NO_SCORE,
        CaveatCode.CONCENTRATION_DEGRADED,
        CaveatCode.SINGLE_RATER,
        CaveatCode.FEW_RATERS,
        CaveatCode.TOP_CLIENT_SHARE,
        CaveatCode.CAMPAIGN_PER_RATER,
        CaveatCode.SELF_RATED,
        CaveatCode.BURN_ADDRESS,
    }
)

#: Subconjunto que la puerta GRATIS puede disparar. La guía publicada avisa que
#: en `GET /wallets/{w}/chains` la lista es un SUBSET —hoy sólo
#: `burn-address`— y que **una lista vacía ahí no promete que la
#: descomposición paga esté limpia**. Se publica para que nadie lea el silencio
#: del preview como un veredicto.
FREE_GATE_CAVEAT_CODES: FrozenSet[str] = frozenset({CaveatCode.BURN_ADDRESS})


def is_known(code: str) -> bool:
    """¿Este código estaba en el set del `CAVEAT_CODES_MEASURED_AT`?

    `False` **no** significa inválido: significa «más nuevo que este SDK».
    Mostralo igual; lo que no se puede hacer es ramificar lógica sobre él sin
    saber qué corte nombra.
    """
    return code in KNOWN_CAVEAT_CODES
