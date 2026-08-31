"""③ VALIDACIÓN DE FORMA DE LOS HASHES — «el 200 sin tx», de KarmaKadabra.

Aporte de **KarmaKadabra** (`#agents`, **2026-08-30**), textual:

    «Un 200 que no hizo la cosa es peor que un 503, porque el cliente lo toma
    por bueno: si nosotros no chequeáramos el tx, habríamos contado 14 ratings
    que no existen. Nosotros lo cazamos con un sello que exige forma de hash —
    pero el contrato debería decirlo, no cada cliente descubrirlo.»

════════════════════════════════════════════════════════════════════════════
LOS HASHES BUENOS DE ACÁ SON CAPTURAS EN VIVO; LOS MALOS ESTÁN CONSTRUIDOS
════════════════════════════════════════════════════════════════════════════
Y tiene que ser así: los buenos salen de `GET /feed` de `api.describe.net` el
**2026-08-30** (por eso prueban algo — un fixture inventado testea contra la
idea del que lo escribió), y los malos no se pueden capturar porque el servicio
hoy no los emite. Ese es justamente el punto del aporte: es el 200 que **algún
día** miente, y el cliente tiene que estar listo antes.

════════════════════════════════════════════════════════════════════════════
🔴 LO QUE HACE DISCRIMINANTE A ESTE ARCHIVO
════════════════════════════════════════════════════════════════════════════
Un validador de hashes tiene DOS formas de estar mal y sólo una es la obvia:

    demasiado flojo   deja pasar la basura         → la caza `test_..._se_marca`
    demasiado estricto grita sobre datos BUENOS    → la cazan los de Solana,
                                                     el digest pelado y `pending`

La segunda es la peligrosa, porque una alarma que suena en el camino feliz se
aprende a ignorar y el día que suene de verdad nadie mira. Por eso el contraste
—`test_una_respuesta_impecable_no_produce_NINGUNA_observacion`— es el test más
importante del archivo: sin él, «marcá todo como malformado» pasaría verde.
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
import pytest

from uvd_describe_sdk import (
    DescribeError,
    DescribeMalformedHash,
    looks_like_onchain_id,
    looks_like_settlement_receipt,
)
from uvd_describe_sdk.models import parse_agent_reputation

from .conftest import json_response

# ── Capturas EN VIVO de `GET /feed`, api.describe.net, 2026-08-30 ────────────
TX_BASE = "0x3768f3d40807148dd29b47457dfc2a37ee5c4846d70e46a7ed0d11013a4f1b2e"
TX_AVALANCHE = "0x785f14ba19210296fbe5f771f6c38cd66a695258dee871c9b2915484b778a015"
TX_CELO = "0x5f7c6e788b68f5732f928d2f997b172f782cf8b4f64504c8b08a820aa5d1f813"

#: 🔴 88 chars, base58, SIN `0x`. Una regex de hash EVM la marcaría como basura.
TX_SOLANA_88 = (
    "2va3P3Q664cT3Zae88Fi3LMvpKWZt1J7c7TyzJHNd8pLczbwgLbwA6hELp7eAjdnwgES2cQoq42wCPtHSy9CTXjJ"
)
#: 87 chars — la otra longitud que Solana produce, también medida en vivo.
TX_SOLANA_87 = (
    "kvqERVGQjpGTemZe6rWzn4hvTuDDHMvUvhsEz31Hu2jbUmwxUigmoFPoKissZahDyNn8fRZ5TiSAJ9ftXou8JLy"
)
#: `inputs_digest`: sha256 hexdigest PELADO, sin `0x` (`aggregate.py:1920`).
DIGEST_PELADO = "9f2c4a1b8e7d6053f4a2b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8"

#: Los que mordieron a KK: un 200 que trae cualquier cosa donde prometió un hash.
BASURA = ["", "null", "undefined", "0x", "0x0", "pending", TX_BASE[:-4], "<error>"]


def _agente(ratings: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "network": "base",
        "agent_id": "25975",
        "score": 91.0,
        "review_count": len(ratings),
        "ratings": ratings,
        "policy_version": "equal-weight-per-chain@2",
    }


def _rating(**extra: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "client": "0x6b51d0d67ff41dab76e499546abe6b8b03cf8732",
        "feedback_index": 1,
        "value": 100,
        "value_decimals": 2,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# El predicado puro: la UNIÓN de formas, no la intersección
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    [TX_BASE, TX_AVALANCHE, TX_CELO, TX_SOLANA_88, TX_SOLANA_87, DIGEST_PELADO],
)
def test_las_cuatro_formas_VIVAS_del_indice_son_validas(valor: str) -> None:
    """🔴 EL DISCRIMINANTE QUE EVITA LA ALARMA FALSA.

    Se pone rojo —en las dos filas de Solana— con la primera versión obvia de
    este validador, `^0x[0-9a-f]{64}$`, que habría marcado como malformada
    **toda calificación de Solana del índice**. Y rojo en `DIGEST_PELADO` si
    alguien le exige el prefijo `0x` al `inputs_digest`, que sale de un
    `hexdigest()` y no lo lleva: eso marcaría cada snapshot citable.
    """
    assert looks_like_onchain_id(valor)


@pytest.mark.parametrize("valor", BASURA)
def test_la_basura_que_mordio_a_KK_no_pasa(valor: str) -> None:
    """La otra mitad: el validador tiene que servir para algo.

    `pending` entra acá a propósito: es basura **en un campo de rating**. Sólo
    es legítimo en la cabecera del recibo, y esa distinción tiene su test abajo.
    """
    assert not looks_like_onchain_id(valor)


def test_pending_es_LEGITIMO_solo_en_el_recibo_de_settlement() -> None:
    """🔴 DISCRIMINANTE del falso positivo más caro.

    El OpenAPI vivo declara que `X-Payment-Receipt` vale el hash **o `pending`
    si el settlement todavía no reportó uno**. Marcarlo dispararía una alarma en
    cada pago recién liquidado, o sea en el camino feliz. Se pone rojo si
    alguien unifica las dos reglas «por simetría».
    """
    assert looks_like_settlement_receipt("pending")
    assert looks_like_settlement_receipt(TX_BASE)
    assert not looks_like_settlement_receipt("undefined")
    # Y al revés: en un rating, `pending` sigue siendo basura.
    assert not looks_like_onchain_id("pending")


# ---------------------------------------------------------------------------
# 🔴 R1 — AUSENTE y MALFORMADO no son lo mismo
# ---------------------------------------------------------------------------


def test_ausente_y_malformado_NO_son_lo_mismo() -> None:
    """🔴 EL TEST QUE SOSTIENE R1 UN NIVEL MÁS ABAJO.

    Los dos ratings terminan con `tx_hash is None`, así que un test que sólo
    mirara ese campo estaría verde con el bug. Lo que los separa es
    `malformed_hashes`:

        no vino     → normal y esperable («null until the log scan reaches
                      this entry»). NO se marca y NO se avisa.
        vino basura → se marca. Ese es el que grita.

    Rojo si alguien marca también el ausente (una alarma en cada rating que el
    backfill no alcanzó todavía = ruido puro) o si no marca ninguno.
    """
    agente = parse_agent_reputation(
        _agente(
            [
                _rating(feedback_index=1),  # sin `tx_hash`: AUSENTE
                _rating(feedback_index=2, tx_hash="undefined"),  # MALFORMADO
            ]
        )
    )
    ausente, malformado = agente.ratings

    assert ausente.tx_hash is None and malformado.tx_hash is None
    assert ausente.malformed_hashes == ()
    assert malformado.malformed_hashes == ("tx_hash",)


def test_el_valor_crudo_sobrevive_en_raw() -> None:
    """Anular el campo tipado no puede ser destruir la evidencia.

    El campo se anula para que nadie arme un link de explorador con basura; el
    crudo queda en `raw`, que es donde se investiga qué mandó el servicio.
    """
    agente = parse_agent_reputation(_agente([_rating(tx_hash="<error>")]))

    assert agente.ratings[0].tx_hash is None
    assert agente.ratings[0].raw["tx_hash"] == "<error>"


def test_los_TRES_campos_de_hash_del_rating_se_validan() -> None:
    """No sólo `tx_hash`: `feedback_hash` y `revoked_tx` son hashes también.

    Y se reportan juntos, en una fila: quien investigue quiere ver el daño
    completo del rating, no descubrirlo de a uno.
    """
    agente = parse_agent_reputation(
        _agente(
            [
                _rating(
                    tx_hash="0x0",
                    feedback_hash="null",
                    revoked_tx=TX_CELO,  # éste SÍ es bueno y tiene que sobrevivir
                )
            ]
        )
    )
    r = agente.ratings[0]

    assert set(r.malformed_hashes) == {"tx_hash", "feedback_hash"}
    assert r.revoked_tx == TX_CELO, "un campo bueno no se cae con sus vecinos podridos"


# ---------------------------------------------------------------------------
# 🔴 Ni levanta ni pasa callado: se observa
# ---------------------------------------------------------------------------


def test_un_hash_podrido_NO_tumba_la_lectura_y_SI_se_observa(make_client) -> None:
    """🔴 LA DECISIÓN DE DISEÑO ENTERA, EN UN TEST.

    Las dos mitades se afirman juntas porque cada una sola dejaría pasar una
    versión mala:

      * sólo «devolvió el objeto» ⇒ verde con el bug de KK (anular en silencio);
      * sólo «avisó» ⇒ verde con una versión que levanta y tira a la basura una
        respuesta PAGADA por un campo accesorio.

    Y se exige que el resto de la respuesta llegue INTACTO: el score, el
    `policy_version` y el rating bueno. Romper todo por un `tx_hash` sería peor
    que el bug que se está cazando.
    """
    payload = _agente(
        [_rating(feedback_index=1, tx_hash=TX_BASE), _rating(feedback_index=2, tx_hash="null")]
    )
    visto: List[DescribeError] = []
    with make_client(lambda _r: json_response(payload), on_error=visto.append) as c:
        agente = c.agent("base", "25975")

    # 1) La lectura llegó entera.
    assert agente.score == 91.0
    assert agente.policy_version == "equal-weight-per-chain@2"
    assert agente.ratings[0].tx_hash == TX_BASE
    # 2) El hecho se observó, exactamente una vez, y dice DÓNDE.
    assert len(visto) == 1
    assert isinstance(visto[0], DescribeMalformedHash)
    assert visto[0].kind == "malformed_hash"
    assert visto[0].fields == ["ratings[1].tx_hash"]
    # 3) Y es una ruta que puede haber costado plata: no se marca como pago
    #    porque acá el servicio contestó 200 sin cobrar.
    assert visto[0].payment_sent is False


def test_el_aviso_no_reusa_el_texto_del_fail_open(make_client, caplog) -> None:
    """Compartir el canal no es compartir el mensaje.

    El WARNING del fail-open termina con «se devuelve None, que NO significa sin
    reputación». Acá eso sería falso —la respuesta llegó y se devuelve entera— y
    mandaría a investigar al lugar equivocado. Rojo si alguien enruta esto por
    `_observe` «para no duplicar código».
    """
    import logging

    payload = _agente([_rating(tx_hash="undefined")])
    with caplog.at_level(logging.WARNING, logger="uvd_describe_sdk"):
        with make_client(lambda _r: json_response(payload)) as c:
            c.agent("base", "25975")

    mensaje = " ".join(r.getMessage() for r in caplog.records)
    assert "shaped like an on-chain identifier" in mensaje
    assert "no reputation" not in mensaje


# ---------------------------------------------------------------------------
# 🔴 El recibo de settlement: donde una mentira cuesta plata
# ---------------------------------------------------------------------------


def test_un_recibo_basura_no_se_toma_como_prueba_de_settlement(make_client) -> None:
    """🔴 EL «200 SIN TX» DENTRO DEL CAMINO DEL DINERO.

    `mark_payment_sent` dice, textual, «HAY RECIBO … el settlement ocurrió, el
    gasto está confirmado y es citable». Sobre una cabecera con basura esa frase
    es una afirmación fuerte y FALSA sobre plata — el bug de KK donde miente más
    caro. Con el recibo invalidado, la excepción usa su otra rama, la honesta:
    «no hay prueba en ninguno de los dos sentidos».

    Rojo si alguien vuelve a pasar la cabecera cruda a `mark_payment_sent`.
    """
    from .conftest import CHALLENGE_402

    class _Payer:
        def create_authorization(self, *_a: Any, **_k: Any) -> str:
            return "BASE64-DE-MENTIRA"

    def handler(request: httpx.Request) -> httpx.Response:
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response(
            {"detail": "boom"}, status=500, headers={"X-Payment-Receipt": "undefined"}
        )

    with make_client(handler, payer=_Payer(), jitter=0) as c:
        with pytest.raises(DescribeError) as exc:
            c.wallet_breakdown("0xdead")

    assert exc.value.payment_sent is True, "la credencial salió: eso no cambia"
    assert exc.value.payment is not None
    assert exc.value.payment["transaction_hash"] is None
    assert "NO `X-Payment-Receipt` arrived" in str(exc.value) or "no proof" in str(
        exc.value
    )


def test_pending_tampoco_prueba_un_settlement(make_client) -> None:
    """`pending` es legítimo **y** no es prueba: significa lo contrario.

    Es el caso que separa «forma válida» de «afirmación sostenible». Un recibo
    `pending` no se marca como malformado (no lo es) pero tampoco autoriza a
    decir que el gasto está confirmado.
    """
    from .conftest import CHALLENGE_402

    class _Payer:
        def create_authorization(self, *_a: Any, **_k: Any) -> str:
            return "BASE64-DE-MENTIRA"

    def handler(request: httpx.Request) -> httpx.Response:
        if "X-PAYMENT" not in request.headers:
            return json_response(CHALLENGE_402, status=402)
        return json_response(
            {"detail": "boom"}, status=500, headers={"X-Payment-Receipt": "pending"}
        )

    with make_client(handler, payer=_Payer(), jitter=0) as c:
        with pytest.raises(DescribeError) as exc:
            c.wallet_breakdown("0xdead")

    assert exc.value.payment["transaction_hash"] is None
    assert "settlement happened" not in str(exc.value)


def test_un_recibo_pending_en_un_200_no_se_marca_como_malformado(make_client) -> None:
    """El camino feliz de un pago recién liquidado, sin una sola alarma."""
    visto: List[DescribeError] = []
    with make_client(
        lambda _r: json_response(
            {"wallet": "0xdead", "final_score": 80.0},
            headers={"X-Payment-Receipt": "pending"},
        ),
        on_error=visto.append,
        jitter=0,
    ) as c:
        b = c.wallet_breakdown("0xdead")

    assert b.receipt is not None
    assert b.receipt.malformed_hashes == ()
    assert visto == []


def test_pending_ya_no_viaja_dentro_de_transaction_hash(make_client) -> None:
    """`pending` legítimo, sí — pero en SU campo, no en el asiento de la prueba.

    Hasta 0.1.0 el literal viajaba ADENTRO de `PaymentReceipt.transaction_hash`
    en un 200: indistinguible de un hash citable sin re-chequear el string, que
    es la familia exacta del INC-2026-08-26 de Execution Market (un placeholder
    ocupando el lugar de la prueba). Desde 0.2.0 el centinela se separa:
    `transaction_hash=None` + `settlement_pending=True`, y sigue SIN marcarse
    como malformado — es el servicio hablando, no basura.
    """
    with make_client(
        lambda _r: json_response(
            {"wallet": "0xdead", "final_score": 80.0},
            headers={"X-Payment-Receipt": "pending"},
        ),
        jitter=0,
    ) as c:
        b = c.wallet_breakdown("0xdead")

    assert b.receipt is not None
    assert b.receipt.transaction_hash is None
    assert b.receipt.settlement_pending is True
    assert b.receipt.malformed_hashes == ()


def test_un_hash_real_no_enciende_settlement_pending(make_client) -> None:
    """El contraste del de arriba: con hash citable, la bandera queda apagada."""
    with make_client(
        lambda _r: json_response(
            {"wallet": "0xdead", "final_score": 80.0},
            headers={"X-Payment-Receipt": TX_BASE},
        ),
        jitter=0,
    ) as c:
        b = c.wallet_breakdown("0xdead")

    assert b.receipt is not None
    assert b.receipt.transaction_hash == TX_BASE
    assert b.receipt.settlement_pending is False
    assert b.receipt.malformed_hashes == ()


# ---------------------------------------------------------------------------
# 🔴 EL CONTRASTE — el test más importante del archivo
# ---------------------------------------------------------------------------


def test_una_respuesta_impecable_no_produce_NINGUNA_observacion(make_client) -> None:
    """🔴 SIN ESTE TEST, «MARCÁ TODO» PASARÍA EN VERDE.

    Se monta el estado BUENO —cuatro ratings con hashes reales capturados hoy,
    de dos familias de cadena distintas, más un snapshot con su digest pelado— y
    se exige lo contrario en las dos mitades: cero campos marcados y **cero**
    observaciones.

    Es el mismo mecanismo que `test_el_caso_legitimo_no_produce_ninguna_
    observacion` de R5, y por la misma razón: un validador demasiado estricto es
    peor que ninguno, porque enseña a ignorar la alarma.
    """
    payload = _agente(
        [
            _rating(feedback_index=1, tx_hash=TX_BASE, feedback_hash=TX_AVALANCHE),
            _rating(feedback_index=2, tx_hash=TX_SOLANA_88),
            _rating(feedback_index=3, tx_hash=TX_SOLANA_87),
            _rating(feedback_index=4, tx_hash=TX_CELO, revoked_tx=TX_BASE),
        ]
    )
    visto: List[DescribeError] = []
    with make_client(lambda _r: json_response(payload), on_error=visto.append) as c:
        agente = c.agent("base", "25975")

    assert all(r.malformed_hashes == () for r in agente.ratings)
    assert [r.tx_hash for r in agente.ratings] == [
        TX_BASE,
        TX_SOLANA_88,
        TX_SOLANA_87,
        TX_CELO,
    ]
    assert visto == [], "una alarma en el camino feliz se aprende a ignorar"


def test_el_snapshot_con_digest_pelado_esta_limpio(make_client) -> None:
    """El `inputs_digest` real —64 hex sin `0x`— no puede leerse como basura:
    es lo único que hace citable un snapshot que se compró para citarlo."""
    visto: List[DescribeError] = []
    with make_client(
        lambda _r: json_response(
            {
                "wallet": "0xdead",
                "final_score": 80.0,
                "snapshot": {
                    "id": 7,
                    "inputs_digest": DIGEST_PELADO,
                    "policy_version": "equal-weight-per-chain@2",
                    "computed_at": "2026-08-30T20:50:02Z",
                },
            }
        ),
        on_error=visto.append,
        jitter=0,
    ) as c:
        b = c.wallet_breakdown("0xdead")

    assert b.snapshot is not None
    assert b.snapshot.inputs_digest == DIGEST_PELADO
    assert b.snapshot.malformed_hashes == ()
    assert visto == []


def test_un_digest_podrido_se_marca_y_se_ubica(make_client) -> None:
    """Y la otra mitad del snapshot: si el digest es basura, se dice dónde."""
    visto: List[DescribeError] = []
    with make_client(
        lambda _r: json_response(
            {
                "wallet": "0xdead",
                "final_score": 80.0,
                "snapshot": {"id": 7, "inputs_digest": "0x", "policy_version": "p@1"},
            }
        ),
        on_error=visto.append,
        jitter=0,
    ) as c:
        b = c.wallet_breakdown("0xdead")

    assert b.snapshot is not None and b.snapshot.inputs_digest is None
    assert b.snapshot.malformed_hashes == ("inputs_digest",)
    assert len(visto) == 1
    assert visto[0].fields == ["snapshot.inputs_digest"]
