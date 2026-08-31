"""Validación de FORMA de los campos de hash — «el 200 sin tx», de KarmaKadabra.

Puro: sin red, sin reloj, sin SQL. Una función de `str` a `bool` y nada más.

════════════════════════════════════════════════════════════════════════════
DE DÓNDE SALE ESTO, Y QUÉ COSTÓ NO TENERLO
════════════════════════════════════════════════════════════════════════════
Aporte de **KarmaKadabra**, canal `#agents` de MeshRelay, **2026-08-30**. Su
frase, textual:

    «Un 200 que no hizo la cosa es peor que un 503, porque el cliente lo toma
    por bueno: si nosotros no chequeáramos el tx, habríamos contado 14 ratings
    que no existen. Nosotros lo cazamos con un sello que exige forma de hash —
    pero el contrato debería decirlo, no cada cliente descubrirlo.»

Esa última mitad es la razón de que esto viva en el SDK y no en cada consumidor:
la comprobación es del CONTRATO. Verificado antes de escribir una línea: ni este
SDK ni su gemelo TypeScript validaban la forma de ningún campo de hash.

════════════════════════════════════════════════════════════════════════════
🔴 POR QUÉ NO ALCANZA UN `^0x[0-9a-f]{64}$`, Y ESTÁ MEDIDO
════════════════════════════════════════════════════════════════════════════
La primera versión obvia de este archivo —una regex de hash EVM— habría
marcado como malformada **toda calificación de Solana del índice**. Medido en
vivo contra `GET /feed?network=…` el **2026-08-30**:

    avalanche  0x785f14ba…a015   66 chars   `0x` + 64 hex
    celo       0x5f7c6e78…f813   66 chars   `0x` + 64 hex
    solana     2va3P3Q6…CTXjJ    88 chars   base58, SIN `0x`
    solana     kvqERVGQ…8JLy     87 chars   base58, SIN `0x`

Un validador que sólo conociera la forma EVM habría gritado sobre datos
perfectamente buenos, y una alarma que grita sobre lo bueno es peor que ninguna:
se aprende a ignorarla, y el día que suene por el bug de verdad nadie mira. Por
eso acá se valida **la unión** de las formas legítimas, no la intersección.

Las cuatro formas que el índice emite hoy, cada una con su fuente:

1. **Hash EVM** — `0x` + 64 hex (66 chars). El servicio lo escribe así:
   `describe-net/describenet/chain/decode.py:253` hace `"0x" + …hex()` sobre un
   bytes32. Es la forma de `tx_hash`, `revoked_tx` y `feedback_hash` en las 10
   cadenas EVM.
2. **Firma Solana** — base58 (alfabeto Bitcoin, sin `0`/`O`/`I`/`l`), 86–88
   chars: 64 bytes en base58 dan 87–88, y 86 es posible con bytes cero a la
   izquierda. Medidas en vivo las dos longitudes altas (arriba). En Solana
   `feedback_hash` va **NULL a propósito** —lo dice el docstring de
   `solana_indexer.py:27`— así que un hueco ahí es correcto, no una falta.
3. **Digest pelado** — 64 hex **sin** `0x`. Es `Snapshot.inputs_digest`, que sale
   de `hashlib.sha256(...).hexdigest()` en `aggregate.py:1920`. Exigirle el
   prefijo `0x` habría marcado como malformado **cada snapshot citable**.
4. **El centinela `pending`** — sólo en la cabecera `X-Payment-Receipt`. El
   propio OpenAPI vivo lo declara: *«the on-chain settlement transaction hash …
   or `pending` if settlement has not reported one»*. Es un valor LEGÍTIMO, y
   por eso `looks_like_settlement_receipt()` existe aparte: tratarlo como
   malformado dispararía una alarma en cada pago cuyo settlement todavía no
   reportó, o sea en el camino feliz.

════════════════════════════════════════════════════════════════════════════
QUÉ PREGUNTA CONTESTA ESTE MÓDULO — Y CUÁL NO
════════════════════════════════════════════════════════════════════════════
Contesta **«¿esto tiene forma de identificador on-chain?»**. No contesta «¿esta
transacción existe?» ni «¿dice lo que dice el índice?»: eso se verifica en un
explorador, que es exactamente el punto del campo (el schema del servicio lo
dice: *«go verify it in an explorer, which is the point»*). Un validador de
forma no puede probar existencia y no pretende hacerlo.

Lo que sí caza, que es lo que mordió a KK: el string vacío, `"null"`,
`"undefined"`, `"0x"`, `"0x0"`, un hash truncado, un mensaje de error metido en
el campo. Todo eso pasa hoy por un `if tx_hash:` sin que nada chille.

🔴 **Deliberadamente NO se valida por cadena.** Un `feedback_hash` con forma
base58 se acepta aunque hoy Solana no emita ninguno. Ser estricto por campo
cambiaría un falso negativo barato (dejar pasar una forma rara pero válida) por
un falso positivo caro (gritar sobre un dato bueno el día que el servicio
extienda un campo), y eso contradice la tolerancia aditiva que `models.py`
defiende: se tipa lo conocido y se conserva lo desconocido.
"""

from __future__ import annotations

import re

#: `0x` + 64 hex. Insensible a mayúsculas: el índice sirve minúsculas, pero un
#: hash con checksum EIP-55 mezclado sigue siendo el mismo hash y marcarlo sería
#: un falso positivo.
_EVM_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")

#: 64 hex SIN prefijo — `hashlib.sha256(...).hexdigest()`, o sea `inputs_digest`.
_BARE_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")

#: base58 del alfabeto Bitcoin (sin `0`, `O`, `I`, `l`), 86–88 chars: una firma
#: de transacción de Solana. Medidas en vivo 87 y 88 el 2026-08-30.
_BASE58_SIGNATURE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{86,88}$")

#: El único valor no-hash que la cabecera `X-Payment-Receipt` declara legítimo.
#: Lo dice el OpenAPI vivo, no una suposición nuestra. Ver el §4 de la cabecera.
SETTLEMENT_PENDING = "pending"


def looks_like_onchain_id(value: str) -> bool:
    """¿`value` tiene forma de identificador on-chain que este índice emita?

    La UNIÓN de las tres formas medidas (hash EVM, firma Solana, digest pelado),
    nunca la intersección: ver la cabecera del módulo para por qué una regex de
    hash EVM sola habría marcado como malformada toda calificación de Solana.

    No prueba que la transacción exista — eso es trabajo del explorador.
    """
    return bool(
        _EVM_HASH.match(value)
        or _BASE58_SIGNATURE.match(value)
        or _BARE_DIGEST.match(value)
    )


def looks_like_settlement_receipt(value: str) -> bool:
    """Igual que `looks_like_onchain_id`, más el centinela `pending`.

    Sólo para `X-Payment-Receipt`. `pending` no es basura: es el servicio
    diciendo «cobré y el settlement todavía no me reportó el hash», y está en su
    OpenAPI. Marcarlo como malformado convertiría el camino feliz de todo pago
    recién liquidado en una alarma.
    """
    return value == SETTLEMENT_PENDING or looks_like_onchain_id(value)
