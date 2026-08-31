"""`recovery` — qué hacer EN VEZ DE, y el vacío como respuesta legítima.

Aporte de **Execution Market** (`#agents`, 2026-08-30): su 502 pasa a traer
`detail.code`, `detail.retryable` y `detail.recovery`, y lo publicó textual
*«para que lo codifiquen de su lado»*. Su argumento, que es el que justifica el
campo: *«SIETE de los diez son TERMINALES (retryable:false) … contra
AUTHORIZATION_EXPIRED reintentar es quemar llamadas contra una ventana cerrada
hace 317 HORAS.»*

Se absorbió el PATRÓN y no su tabla: sus diez códigos son de SU API (escrow,
release al worker, wallets de payout) y este SDK envuelve la de describe. El
porqué completo vive en la cabecera de `errors.py`; este archivo ata cuatro
propiedades, y ninguna es la redacción:

1. **Toda clase de error declara su `recovery` en su propio cuerpo**, también
   cuando vale `None`. Es lo que separa «decidí que no hay ruta» de «me olvidé».
2. **`DescribeTimeout.recovery` es `None`, y eso está FIJADO.** Es el test que
   impide que alguien complete la tabla por prolijidad con un «reintentá», que
   es un booleano redactado en prosa y exactamente el error que EM señala.
3. **Cada consejo nombra OTRA cosa que hacer** — la ruta gratis vecina, un
   parámetro del constructor, un campo del propio error. Se fijan los ANCLAJES,
   no la redacción: el texto canónico vive UNA vez, en el cuerpo de su clase, y
   copiarlo acá sería la segunda copia que se pudre.
4. 🔴 **`recovery` no puede filtrar un secreto**, porque es una constante de
   clase que nosotros escribimos y no interpola nada. Es la versión SDK del
   `_redact` de `describenet/chain/rpc.py`, que del lado del servicio borra la
   URL del proveedor (la API key vive en su path) de todo lo que levanta o
   loguea. EM avisó el mismo día que un error sin clasificar «puede traer una
   URL de RPC con su API key adentro» y que le pusieron un test con un secreto
   de mentira; éste es el nuestro.

⚠️ `DescribeError` (la base) queda FUERA de estas tablas a propósito: nadie la
levanta —verificado, no hay un solo `raise DescribeError(` en `src/`— y existe
como ancla de tipo del atributo. Su `kind` es `"unreachable"`, heredado de antes
de que este campo existiera; si algún día se levantara, publicaría ese `kind`
con `recovery=None` mientras `DescribeUnreachable` publica texto. Se deja
anotado en vez de tapado: es un detalle preexistente de `kind`, no de
`recovery`.
"""

from __future__ import annotations

from typing import List, Optional, Type

import pytest

from uvd_describe_sdk import (
    DescribeError,
    DescribeHTTPError,
    DescribeMalformedHash,
    DescribeTimeout,
    DescribeUnparseable,
    DescribeUnreachable,
    DoNotPayError,
    PartnerRejectedError,
    PartnerSigningError,
    PaymentRequiredError,
)

# ---------------------------------------------------------------------------
# El inventario de errores, deducido y no tipeado
# ---------------------------------------------------------------------------


def _todas_las_subclases(base: Type[DescribeError]) -> List[Type[DescribeError]]:
    """Toda subclase de `DescribeError`, en profundidad.

    Se recorre el árbol en vez de escribir una lista: una clase nueva tiene que
    entrar SOLA a estos tests. Una lista a mano dejaría al error nuevo sin
    revisar justo el día que se agrega, que es cuando la decisión se toma.
    """
    encontradas: List[Type[DescribeError]] = []
    for hija in base.__subclasses__():
        encontradas.append(hija)
        encontradas.extend(_todas_las_subclases(hija))
    return encontradas


#: Las nueve de hoy (2026-08-30). El número no se afirma en ningún assert: lo
#: que se exige es que TODAS pasen, sean las que sean.
SUBCLASES = _todas_las_subclases(DescribeError)


def _construir(cls: Type[DescribeError], mensaje: str) -> DescribeError:
    """Una instancia de cualquiera de las clases, con el mensaje que se le pide.

    Las firmas divergen (unas piden `status_code`, otras `wallet`), así que hay
    que ramificar. Se ramifica por `issubclass` y con un `else` que NO adivina:
    una clase nueva con firma propia rompe acá y obliga a decidir, en vez de
    quedar silenciosamente fuera de los tests de filtración.
    """
    if issubclass(cls, DescribeHTTPError):
        return cls(mensaje, status_code=503)
    if issubclass(cls, PartnerRejectedError):
        return cls(mensaje, wallet="0x00000000000000000000000000000000000000aa")
    if issubclass(cls, PaymentRequiredError):
        return cls(mensaje, challenge={"price_usd": "0.01"})
    if issubclass(cls, PartnerSigningError):
        return cls(mensaje, wallet=None)
    if issubclass(cls, DoNotPayError):
        return cls(mensaje, expected="base", offered="(vacía)")
    if issubclass(cls, DescribeMalformedHash):
        return cls(mensaje, fields=["ratings[3].tx_hash"])
    return cls(mensaje)


# ---------------------------------------------------------------------------
# 1. Declarar es obligatorio; `None` es una declaración
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", SUBCLASES, ids=lambda c: c.__name__)
def test_cada_error_declara_su_recovery_en_su_propio_cuerpo(
    cls: Type[DescribeError],
) -> None:
    """Heredar la `recovery` de otro error es siempre un bug.

    🔴 El chequeo es `"recovery" in cls.__dict__` y no `cls.recovery is not
    None`, y la diferencia ES la tarea: `None` tiene que poder significar «no
    hay ruta de recuperación real», así que no se puede exigir texto. Lo que sí
    se exige es que alguien lo haya ESCRITO en esa clase — es lo único que
    separa un vacío decidido de un olvido.

    El caso que esto caza de verdad es `PartnerRejectedError`, que hereda de
    `PaymentRequiredError`: sin declarar la suya publicaría «configurá `payer=`»
    ante un riel roto, que es el consejo que su propio docstring existe para
    prohibir.
    """
    assert "recovery" in cls.__dict__, (
        f"{cls.__name__} no declara `recovery` en su cuerpo: hereda la de "
        f"{cls.__mro__[1].__name__}. Escribila —aunque sea `None`— para que la "
        "decisión quede tomada y no heredada."
    )


@pytest.mark.parametrize("cls", SUBCLASES, ids=lambda c: c.__name__)
def test_recovery_es_texto_o_none_y_nunca_una_cadena_vacia(
    cls: Type[DescribeError],
) -> None:
    """`""` sería el peor de los dos mundos: ni consejo ni ausencia declarada.

    Es la misma regla que R1 un nivel más abajo — *sin datos* no es *cero*—
    aplicada al texto: el vacío se dice con `None`, que un `if` distingue.
    """
    valor = cls.__dict__["recovery"]
    assert valor is None or (isinstance(valor, str) and valor.strip())


# ---------------------------------------------------------------------------
# 2. El vacío honesto, fijado
# ---------------------------------------------------------------------------


def test_el_timeout_NO_tiene_recovery_y_eso_esta_decidido() -> None:
    """🔴 El test que protege el vacío. Sin él, «completar la tabla» pasa verde.

    Un timeout es el fallo más común del SDK y aun así no hay OTRA cosa que
    hacer: la misma pregunta contra el mismo índice no tiene segunda puerta.
    Las dos salidas que parecen recuperaciones se descartaron con su medición y
    están en el docstring de la clase — subir el timeout choca contra los 29 s
    del API Gateway del proveedor, y «reintentá» es un booleano, no una
    recuperación.
    """
    assert DescribeTimeout.recovery is None


def test_la_tabla_de_quien_tiene_ruta_de_recuperacion_y_quien_no() -> None:
    """El inventario completo, por `kind`. Es la decisión, no la redacción.

    Se afirma `bool(recovery)` y nunca el texto: el texto canónico vive UNA sola
    vez, en el cuerpo de su clase, y una copia acá sería la segunda superficie
    que se desincroniza (la regla de la casa que nació de un umbral escrito con
    dos valores distintos en cuatro archivos). Lo que esta tabla congela es
    cuáles fallos tienen salida y cuáles no — que es lo que un consumidor
    ramifica.
    """
    tabla = {cls.kind: bool(cls.__dict__["recovery"]) for cls in SUBCLASES}
    assert tabla == {
        # Sin ruta: la única pregunta es la misma pregunta, más tarde.
        "timeout": False,
        # Con ruta:
        "http_error": True,
        "unreachable": True,
        "unparseable": True,
        "malformed_hash": True,
        "payment_required": True,
        "partner_signing": True,
        "partner_rejected": True,
        "do_not_pay": True,
    }


# ---------------------------------------------------------------------------
# 3. Cada consejo nombra OTRA cosa que hacer
# ---------------------------------------------------------------------------

#: Los ANCLAJES: qué tiene que nombrar cada consejo para ser accionable. Cada
#: entrada es un símbolo que existe en otro lado del ecosistema (una ruta gratis,
#: un parámetro del constructor, un campo del propio error, una cabecera viva),
#: nunca un fragmento de la redacción. Si alguien reescribe el texto, esto
#: sobrevive; si alguien le saca la salida, esto se pone rojo.
ANCLAJES = {
    DescribeHTTPError: ["status_code", "RateLimit-Policy", "jitter="],
    DescribeUnreachable: ["base_url"],
    DescribeUnparseable: ["base_url"],
    DescribeMalformedHash: ["raw", "fields"],
    # 🔴 El más importante de todos: la ruta GRATIS que contesta la pregunta
    # vecina a la paga que falló. Es la recuperación más útil que este SDK puede
    # dar, y el 402 mismo la trae en `free_preview`.
    PaymentRequiredError: ["wallet()", "payer=", "free_preview"],
    PartnerSigningError: ["wallet()", "partner="],
    PartnerRejectedError: ["allowlist", "wallet()", "base_url"],
    DoNotPayError: ["expected", "offered", "pay_network="],
}


@pytest.mark.parametrize(
    "cls,anclajes", list(ANCLAJES.items()), ids=lambda v: getattr(v, "__name__", "")
)
def test_cada_recovery_nombra_otra_cosa_que_hacer(
    cls: Type[DescribeError], anclajes: List[str]
) -> None:
    """Una recuperación que no nombra nada es una descripción del error.

    «Reintentá» ya lo diría un booleano. Lo que este campo aporta es un nombre
    propio: otra ruta, otro parámetro, otro campo del error, otra cabecera.
    """
    texto = cls.__dict__["recovery"]
    faltan = [a for a in anclajes if a not in texto]
    assert not faltan, f"{cls.__name__}.recovery dejó de nombrar {faltan}"


def test_todos_los_anclajes_estan_cubiertos() -> None:
    """La tabla de anclajes tiene que cubrir a TODA clase con texto.

    Sin esto, una clase nueva con un `recovery` inventado y sin salida real
    entraría sin que nadie mirara si nombra algo. El vacío honesto sí puede
    faltar de la tabla: no hay anclaje que exigirle a un `None`.
    """
    con_texto = {cls for cls in SUBCLASES if cls.__dict__["recovery"]}
    assert con_texto == set(ANCLAJES)


# ---------------------------------------------------------------------------
# 4. 🔴 El guard de secretos — la versión SDK de `rpc.py::_redact`
# ---------------------------------------------------------------------------

#: Un secreto de MENTIRA, con la forma de los dos que de verdad podrían llegar
#: acá: la URL de un RPC con su clave en el path (lo que EM avisó hoy) y un DSN
#: con contraseña. No es ninguna clave real y no tiene forma de private key
#: (nada de `0x` + 64 hex): un fixture que la tuviera sería el bug que este test
#: persigue, escrito en el test.
SECRETO_DE_MENTIRA = (
    "https://rpc.invalid/v2/CLAVE-FALSA-DE-TEST-NO-ES-REAL "
    "postgresql://usuario:CONTRASENA-FALSA@db.invalid:5432/describenet"
)


@pytest.mark.parametrize("cls", SUBCLASES, ids=lambda c: c.__name__)
def test_ninguna_recovery_filtra_lo_que_venia_en_el_mensaje(
    cls: Type[DescribeError],
) -> None:
    """El mensaje puede traer basura ajena; `recovery` nunca la toca.

    El servicio resuelve esto del otro lado borrando la URL del proveedor de
    todo lo que levanta (`describenet/chain/rpc.py::_redact`, porque la API key
    vive en el path). Acá el equivalente es estructural: el texto lo escribimos
    nosotros y es una constante, así que no hay nada que redactar.

    ⚠️ Esto ata `recovery`, **no** el mensaje: el de `PartnerSigningError` sí
    interpola la excepción del firmante a propósito (`partner.py:249`, para
    nombrar la causa real), y por eso el assert mira un solo atributo.
    """
    exc = _construir(cls, f"falló contra {SECRETO_DE_MENTIRA}")
    if exc.recovery is None:
        return
    for fragmento in ("CLAVE-FALSA-DE-TEST", "CONTRASENA-FALSA", "rpc.invalid"):
        assert fragmento not in exc.recovery


@pytest.mark.parametrize("cls", SUBCLASES, ids=lambda c: c.__name__)
def test_recovery_es_una_constante_de_clase_y_no_se_arma_por_instancia(
    cls: Type[DescribeError],
) -> None:
    """El guard de verdad: si no se puede interpolar, no puede filtrar.

    🔴 Este es el que se pone rojo ante la mutación realista — alguien convierte
    `recovery` en una `property` o la asigna en `__init__` con un f-string del
    mensaje «para que sea más útil». Ahí el atributo deja de ser el mismo objeto
    que el de la clase, y el test de filtración de arriba pasaría a depender de
    qué mensaje se le mandó en vez de ser imposible por construcción.

    Dos instancias con mensajes distintos tienen que compartir el MISMO objeto.
    """
    declarado = cls.__dict__["recovery"]
    assert isinstance(declarado, str) or declarado is None, (
        f"{cls.__name__}.recovery no es una constante (es "
        f"{type(declarado).__name__}): un `recovery` calculado puede interpolar "
        "el mensaje de una excepción ajena, y ahí es donde viaja el secreto."
    )
    una = _construir(cls, f"a {SECRETO_DE_MENTIRA}")
    otra = _construir(cls, "b")
    assert una.recovery is otra.recovery is declarado


# ---------------------------------------------------------------------------
# El contrato de siempre no se movió
# ---------------------------------------------------------------------------


def test_recovery_no_toca_kind_ni_payment_sent() -> None:
    """El campo se AGREGA; no reemplaza ni desplaza a nada.

    Se ramifica por `kind` (o por la clase) y por `payment_sent`; `recovery` se
    LEE. Es el mismo par que `caveats[].code` / `caveats[].text`: decidir por el
    código, leer el texto. Un `if "allowlist" in exc.recovery` es el bug que
    este test recuerda que no hay que escribir.
    """
    exc: Optional[DescribeError] = PartnerRejectedError(
        "402 pese a firmar", wallet="0x00000000000000000000000000000000000000aa"
    )
    assert exc is not None
    assert exc.kind == "partner_rejected"
    assert exc.payment_sent is False
    assert exc.payment is None
    assert isinstance(exc.recovery, str)
