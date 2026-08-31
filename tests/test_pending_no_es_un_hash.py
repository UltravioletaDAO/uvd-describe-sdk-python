"""`pending` es legítimo, y sigue sin ser un hash.

EL BUG, medido contra el paquete PUBLICADO 0.1.0
================================================

    >>> r = httpx.Response(200, headers={"X-Payment-Receipt": "pending"})
    >>> DescribeClient._receipt(r).transaction_hash
    'pending'

El campo se llama `transaction_hash` y contenía la palabra `pending`. El chequeo
más natural que escribe un consumidor —``if receipt.transaction_hash:``— daba
**True** para un pago cuyo hash nadie conoce todavía.

`looks_like_settlement_receipt` hace bien su trabajo: `pending` NO es basura y
marcarlo como malformado convertiría el camino feliz de todo pago recién
liquidado en una alarma. El error no estaba ahí, estaba en **dónde aterrizaba**
la respuesta: son dos preguntas distintas —¿se liquidó? y ¿puedo nombrar la
transacción?— y un solo campo no puede contestar las dos.

POR QUÉ ESTE SDK ES EL LUGAR PARA ARREGLARLO
============================================

Está aguas arriba de la base de datos de cada consumidor: lo que ponga en
`transaction_hash` es lo que termina en esa columna.

En Execution Market (INC-2026-08-26) un placeholder —``"timeout-verified-onchain"``—
vivía en la columna del hash de pago. Seis executors quedaron registrados como
pagados y **no se había movido un peso**; tres sitios distintos leyeron el campo
como verdadero y coincidieron. La regla que escribieron en su esquema después es
la que se aplica acá: un pago sólo se registra cuando se puede nombrar la
transacción que lo hizo, y cuando no, el valor honesto es NULL.
"""

import httpx

from uvd_describe_sdk.client import DescribeClient
from uvd_describe_sdk.hashes import SETTLEMENT_PENDING

HASH_REAL = "0x" + "ab" * 32


def _recibo(valor):
    headers = {} if valor is None else {"X-Payment-Receipt": valor}
    return DescribeClient._receipt(httpx.Response(200, headers=headers))


def test_pending_no_aterriza_en_transaction_hash():
    """El corazón del arreglo, y el assert que antes fallaba."""
    r = _recibo(SETTLEMENT_PENDING)

    assert r.transaction_hash is None, (
        "el campo llamado transaction_hash volvió a contener una palabra en vez "
        "de un hash: `if receipt.transaction_hash:` lee como pagado algo cuyo "
        "hash nadie conoce"
    )
    assert r.settlement_pending is True
    # Y sigue SIN ser malformado: ésa parte estaba bien y no se toca.
    assert r.malformed_hashes == ()


def test_un_hash_real_sigue_entrando_igual():
    """El arreglo no puede romper a quien ya funcionaba."""
    r = _recibo(HASH_REAL)

    assert r.transaction_hash == HASH_REAL
    assert r.settlement_pending is False
    assert r.malformed_hashes == ()


def test_basura_sigue_marcandose_malformada():
    r = _recibo("no-soy-un-hash")

    assert r.transaction_hash is None
    assert r.malformed_hashes == ("transaction_hash",)
    # Y NO se confunde con pendiente: son estados distintos.
    assert r.settlement_pending is False


def test_las_TRES_ausencias_son_distinguibles():
    """La razón de ser del campo nuevo.

    ``transaction_hash is None`` ahora ocurre en tres casos, y colapsarlos es
    volver al problema con otro disfraz. Un consumidor tiene que poder decir
    "todavía no" y "nunca lo hubo" sin adivinar.
    """
    pendiente = _recibo(SETTLEMENT_PENDING)
    ausente = _recibo(None)
    basura = _recibo("xx")

    assert (pendiente.settlement_pending, pendiente.malformed_hashes) == (True, ())
    assert (ausente.settlement_pending, ausente.malformed_hashes) == (False, ())
    assert (basura.settlement_pending, basura.malformed_hashes) == (
        False,
        ("transaction_hash",),
    )
    # Los tres tienen transaction_hash None y NINGUNO se confunde con otro.
    assert len({
        (p.settlement_pending, p.malformed_hashes)
        for p in (pendiente, ausente, basura)
    }) == 3


def test_el_centinela_se_exporta_desde_la_raiz():
    """El gemelo TS ya lo exporta; que Python no lo hiciera era asimetría.

    Sin esto, un consumidor que quiera comparar contra el centinela lo tiene que
    escribir a mano como `"pending"` — un literal duplicado que se desincroniza
    el día que el servicio cambie la palabra.
    """
    import uvd_describe_sdk

    assert uvd_describe_sdk.SETTLEMENT_PENDING == "pending"


def test_DISCRIMINANCIA_la_forma_vieja_no_pasaria():
    """Sin esto, los de arriba pasan igual contra cualquier implementación.

    Se reconstruye el comportamiento anterior —meter el centinela derecho en
    `transaction_hash`— y se exige que sea distinguible del arreglado.
    """
    viejo_transaction_hash = SETTLEMENT_PENDING  # lo que devolvía 0.1.0
    nuevo = _recibo(SETTLEMENT_PENDING)

    assert bool(viejo_transaction_hash) is True, "el fixture perdió sentido"
    assert bool(nuevo.transaction_hash) is False, (
        "el arreglo dejó de cambiar la respuesta al chequeo que importa"
    )
