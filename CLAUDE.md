# CLAUDE.md — uvd-describe-sdk (Python)

Guía para Claude Code al trabajar en este repo.

> Este archivo es el mapa, no la fuente. La fuente son los docstrings: cada
> módulo abre defendiendo **por qué** está hecho así, con la medición que lo
> decidió. Antes de tocar `client.py`, `models.py`, `payment.py` o
> `caveats.py`, leé sus primeras 60 líneas — están escritas para vos.

---

## Qué es

El cliente Python de **describe** (`api.describe.net`), el índice de reputación
ERC-8004 de Ultravioleta DAO. Es un **lector**: no escribe en ninguna cadena, no
emite calificaciones, no firma nada.

Su gemelo TypeScript vive en `uvd-describe-sdk-typescript` y los dos
implementan **el mismo contrato núcleo**. Un cambio de contrato se acuerda entre
los dos; **ninguno lo cambia por su cuenta**.

---

## Comandos

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # Windows; en Linux .venv/bin/python

.venv/Scripts/python -m pytest                     # 551 pasan, 27-33 s, SIN RED y VERIFICADO por el guard de `tests/conftest.py` (re-medido 2026-09-25 en py3.13, py3.12 y py3.9, con y sin HTTPS_PROXY/HTTP_PROXY=http://127.0.0.1:9, tras SDK-5 y SDK-6 de 0.7.0 — `test_names_sdk5.py` y `test_names_sdk6.py`; los de `test_names_ronda3.py`, `test_names_ronda4.py` y `test_names_ronda5.py` usan un servidor local en 127.0.0.1, y el de trio necesita el extra `dev`; eran 524 tras la ronda 5 del PR #6 de `names`, 495 tras la ronda 4, 487 tras la ronda 3, 476 tras la ronda 2, 448 con el módulo recién llegado, 313 el 2026-09-15 tras `thin-chain` en 0.6.1, 312 ese día tras el guard de lista de `require_full_caveats()`, 306 tras `caveats_not_computed`/`author_class`, 229 el 2026-08-31 y 215 el 2026-08-30)
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy src/uvd_describe_sdk
.venv/Scripts/python -m build
.venv/Scripts/python examples/smoke_gratis.py      # ESTO SÍ toca la API viva
```

Un test suelto: `python -m pytest tests/test_r5_fail_open.py::test_sin_on_error_igual_loguea_en_warning`

**La suite entera corre sin red.** El único seam es `transport=` del
constructor (`httpx.MockTransport`). Si un test empieza a necesitar internet,
algo se rompió en el diseño, no en el CI.

---

## Las ocho reglas duras — el contrato núcleo v0.1

Compartidas con el SDK TypeScript. **Si algo no cierra, se REPORTA, no se
desvía.**

| # | Regla | Dónde vive | Su test |
|---|---|---|---|
| R1 | `null` **nunca** `0` | `models.py::_opt_float` | `test_r1_null_nunca_cero.py` |
| R2 | Ningún método devuelve un número pelado | toda la superficie pública | `test_r2_r3_contrato.py` |
| R3 | Caveats `{code, text}`; se ramifica por `code` | `caveats.py` | `test_r2_r3_contrato.py` |
| R4 | Excepción sólo por transporte/protocolo | `errors.py`, `client.py` | `test_r4_sin_datos_no_es_error.py` |
| R5 | `fail_open=True`, **observable**, y **sólo en las rutas gratis** | `client.py::_observe` | `test_r5_fail_open.py` |
| R6 | El 402 lo hace `uvd-x402-sdk` | `payment.py` | `test_r6_pago_x402.py` |
| R7 | Timeout 30 s | `client.py::DEFAULT_TIMEOUT_S` | `test_r7_cliente_y_atribucion.py` |
| R8 | `f"{round(x,2):g}"` | `display.py` | `test_r8_formato_canonico.py` |

### 🔴 R5, EL ALCANCE — corregido el 2026-08-30, es canon en los DOS SDK

```
GRATIS  wallet() · leaderboard() · health()  → None observado. NUNCA [].
PAGAS   wallet_breakdown() · agent()         → LEVANTAN, aun con fail_open=True.
```

**La línea no es «cuántos métodos»: es si hubo dinero de por medio.** Entre
firmar el sobre x402 y recibir la respuesta el USDC ya se movió, y un `None` ahí
es una credencial gastada sin recibo. No es una preferencia del llamador sino una
propiedad del método.

⚠️ **Antes de hoy este repo implementaba la tabla de tipos del contrato v0.1 «al
pie de la letra» —`| null` sólo en `wallet()`— y lo dejaba anotado como
ambigüedad reportada. Se deja escrito porque la corrección enseña algo**: el
gemelo TypeScript leyó la otra mitad del mismo contrato (la regla R5, que no
acotaba a ningún método) y terminó haciendo **fail-open en las rutas pagas**,
devolviendo `null` tras un timeout posterior al settlement. Dos lecturas
razonables del mismo texto, y una costaba plata. Del razonamiento viejo
sobrevivió una mitad: «una lista vacía se lee como un índice vacío» era cierto, y
por eso el contrato corregido dice **nunca `[]`**.

Y una excepción del tramo pagado sale con **`payment_sent=True`** y su
`payment` (`amount_usd`, `network`, `resource`, `transaction_hash`): un
`DescribeTimeout` pelado no distingue «se cayó antes de pagar» de «se cayó
después», y sólo uno de los dos pide reconciliar. Se ramifica por el atributo,
nunca por el texto. **Límite conocido**: `payment_sent` prueba que la credencial
salió, no que el settlement ocurrió — eso sólo lo prueba un `transaction_hash`
presente, y un transporte caído no trae ninguno.

### `recovery` — la décima superficie, y TAMPOCO es una regla del contrato

`errors.py`. Aporte de **Execution Market** (`#agents`, 2026-08-30): *«SIETE de
los diez son TERMINALES … contra `AUTHORIZATION_EXPIRED` reintentar es quemar
llamadas contra una ventana cerrada hace 317 HORAS.»* Se absorbió el **patrón**,
no su tabla de códigos —que es de SU API— así que se recorrieron NUESTROS nueve
`kind` y se decidió caso por caso. No entra a las ocho reglas: agrega un
atributo, no cambia ninguna. **R5 intacta.**

Lo que hay que saber antes de tocarlo (el porqué completo, en `errors.py`):

1. 🔴 **`None` es una respuesta y está fijada por un test.** `timeout` no tiene
   ruta: subir el timeout choca con los 29 s del API GW y «reintentá» es un
   booleano en prosa. **Inventar una recuperación que no funciona es peor que no
   tener el campo.** Mutación J.
2. **Cada clase la declara en SU cuerpo**, también cuando vale `None`
   (`"recovery" in cls.__dict__`). Es lo único que separa «decidí» de «me
   olvidé». Mutación L.
3. 🔴 **Es una CONSTANTE de clase y no interpola nada** — la versión SDK del
   `_redact` del servicio. Un `recovery` armado en `__init__` puede filtrar la
   URL de un RPC con su key. Mutación K.
4. **Texto y no enum, porque el enum ya existe y es `kind`.** Se ramifica por el
   código, se lee el texto — el mismo par que `caveats[].code`/`.text`.
5. ⚠️ **La premisa del aporte era falsa acá y se dejó escrita**: llegó como «ya
   tienen `transient` y `serviceFault`». `grep -rEni "transient|servicefault"
   src/` = **0**. El hueco se reportó, no se rellenó.

### `caveats_not_computed` y `require_full_caveats()` — la undécima superficie, y TAMPOCO es una regla

`caveats.py` + `models.py`. Fila upstream-first `describe-net/docs/BACKLOG.md:19`,
2026-09-15: el servicio sirve desde el 2026-09-14 la lista de codes que la puerta
gratis NO evaluó, y `author_class` en cada `Rating`; este SDK los tipa ANTES de
que EM, KK, mesh y karma-hello los adopten. Agrega campos y un gate, no toca
ninguna de las ocho. **R5 intacta** (el gate lo llama el consumidor, no el
cliente).

Lo que hay que saber antes de tocarlo (el porqué completo, en `caveats.py`):

1. 🔴 **`None` no es `[]`, y el gate levanta con los dos que no son `[]`.**
   `None` = la respuesta no declaró (API anterior al 2026-09-14, `fallback_reader`
   o un valor ilegible). Dejarlo pasar es la fila del 2026-08-31
   (`BACKLOG.md:221`) de vuelta: el gate que pasa en verde sin saber qué no se
   calculó. Mutaciones O y P.
2. **Una declaración ilegible es `None`, nunca una lista filtrada**: `[null]`
   filtrado da `[]`, y `[]` es el único valor que hace PASAR al gate. Es la
   asimetría a propósito con `parse_caveats`. Mutación T.
3. 🔴 **`CaveatsNotComputedError` NO es un `DescribeError`.** El `except
   DescribeError` que un consumidor escribió para tolerar caídas lo volvería
   «describe no contestó», y un gate tolerante deja pasar. Mutación Q.
4. **`author_class` es `str`, no el `Literal`**: una clase nueva llega entera
   (mutación S), y la ausencia es `None`, **nunca `rater-authored`** (mutación
   R). Los dos campos nuevos van ÚLTIMOS en su dataclass, después de `raw`, para
   no correr ningún argumento posicional.
5. ~~⚠️ **Son NUEVE codes acá y el servicio sirve DIEZ.** `thin-chain` (desde
   2026-09-05) falta en los dos gemelos. No se agregó de un solo lado para no
   romper la paridad: queda como seguimiento de los dos SDK (anotado en
   `CHANGELOG.md`, 0.6.0), y por eso `CAVEAT_CODES_MEASURED_AT` sigue en
   `2026-08-30`.~~ **RESUELTO el 2026-09-15 (0.6.1)**: `thin-chain` entró en los
   dos gemelos a la vez, en `KNOWN_CAVEAT_CODES` y en `FREE_GATE_CAVEAT_CODES`
   (`burn-address` + `thin-chain`). Son los DIEZ del servicio y
   `CAVEAT_CODES_MEASURED_AT` pasó a `2026-09-15` (mutación X).

### `names` — el resolver de nombres del stack, la duodécima superficie, y TAMPOCO es una regla

`names/` (paquete, detrás del extra `[names]`) + `name_models.py` (liviano, en la
base) + `DescribeClient.names`. Encargo de c0der del 2026-09-24 (DUP-01 del
barrido de duplicación): la capacidad vivía partida y rota en tres repos
(Execution Market `mcp_server/integrations/ens/client.py`, karma-hello
`infrastructure/domain_resolver.py`, uvdweb `WalletConnect.js`). No toca ninguna
de las ocho reglas: el resolver on-chain no habla con describe, y la capa HTTP
(`DescribeClient.names`) es una ruta gratis con R5 como `wallet()`.

Lo que hay que saber antes de tocarlo (el porqué completo, en el docstring de
cada módulo de `names/`):

1. 🔴 **La dirección cero nunca sale como dirección.** Un resolver que contesta
   `0x000…0` dice «no está puesto», y un pago ahí se quema: es `not_found`. La
   fixture es REAL (`default.reverse`, el DefaultReverseResolver de ENSIP-19
   devuelve `address(0)`). Mutación Y.
2. 🔴 **El reverse se confirma forward SIEMPRE**, y un reverse que no se
   confirma (o que no está en forma normal ENSIP-15) es `reverse_mismatch` **sin
   devolver el nombre reclamado**. Mutaciones Z y AD. Fixture real:
   `0xd02a…a513` reclama `0x5e405F9e…hooks.cow.eth` con mayúsculas.
3. 🔴 **`verified_onchain` es `False` para todo lo que llega por HTTP**
   (`parse_name_resolution` lo fuerza, aunque el servidor diga `true`), y
   `require_onchain_address()` lo exige para pagar. Mutación AA.
4. **`rpc_unavailable` nunca se cachea**; los negativos viven menos que los
   positivos (60 s contra 300 s) y la caché tiene tope. Mutación AB.
5. **El vencimiento se lee ANTES de resolver**: los registros de un `.eth`
   vencido siguen on-chain y resuelven a la dirección vieja. Mutación AC.
6. **ENSIP-15, no `lower()`** — el bug de Execution Market. Mutación AE. Y
   medido con `ens-normalize` 3.0.10: las LETRAS de ancho completo se pliegan,
   los PUNTOS de ancho completo (U+FF0E, U+3002) son `DISALLOWED`.
7. **Nada de URLs de RPC en el SDK ni en sus salidas.** Las pasa el consumidor
   por constructor (claves CAIP-2), el `detail` nombra la cadena y nunca el
   endpoint, y las fixtures guardan el id CAIP-2. Los gateways CCIP y la metadata
   de NFT pasan por `check_url` (https, sin credenciales, sin IPs no globales).
   Mutación AG.
8. **Orden estricto en el reverse**: si un sistema de arriba no se pudo
   preguntar, no se contesta con uno de abajo. Mutación AH.
9. **UN motor** (`names/_proto.py`, generadores sans-IO que corre
   `run_async`): la variante `_sync` es ese mismo motor en un loop propio
   (`run_blocking`), no un motor aparte — ver el punto 12. Es la salida que la
   pregunta abierta 3 (abajo) pedía para `DescribeClient`, aplicada primero acá.
   `test_names_hechos_medidos.py` corre cada grabación por las dos variantes.
10. ⚠️ **SNS (`.sol`) es `unsupported_system` a propósito**: el `sns-sdk`
    oficial (`536f0cb`, leído el 2026-09-24) apaga la resolución legacy de `.sol`
    en el slot finalizado 452.825.395 (≈ 2026-10-15) y el camino nuevo (SRS) sigue
    deshabilitado upstream. Media implementación que deja de andar en tres
    semanas sería peor que un «todavía no» claro. Queda como seguimiento.
11. ⚠️ **El avatar NFT no tiene grabación real**: `ipfs.io` y `dweb.link`
    contestaron 429 al grabar `matoken.eth` y la grabación se detuvo (regla).
    La regla de propiedad se prueba con un doble SINTÉTICO rotulado como tal
    (`test_names_avatar_nft_sintetico.py`). Mutación AF.

**Ronda 2 del PR #6** (refutación sobre `c9d67c6`: 0 P0, 2 P1, 5 P2; cada
hallazgo se verificó contra el código antes de tocarlo):

12. 🔴 **Sync = el motor async bajo UN deadline duro** (decisión de c0der,
    ronda 4). `run_bounded` corre `run_async` bajo un solo `asyncio.wait_for`, el
    presupuesto cuenta desde que entra la llamada pública, y `resolve_sync()` lo
    corre con `run_blocking`: en un loop propio, o en un hilo propio con su loop
    si el hilo que llama ya tiene uno corriendo (los loops no se anidan).
    **No usa `asyncio.run`, y es medido**: `asyncio.run` espera al executor donde
    corre `getaddrinfo` — 0,5 s de presupuesto → 3,01 s; con el loop propio
    cerrado sin esperar, 0,5 s. Así el DNS, que en la ronda 3 quedó «sin acotar»,
    ahora también se corta. Medido con un servidor local, presupuesto 1,0 s: un
    connect sobre N=3 y N=5 direcciones que no contestan el SYN → 1,00 s (con el
    motor sync propio de la ronda 3, N=5 → 5,00 s y N=10 → 10,00 s). Casos A/B/C/D
    del verificador (cuerpo, headers, gzip) también por el motor único. Un solo
    contexto TLS por resolver (`AsyncClient()` arma uno cada vez: 0,34 s; reusado,
    0,3 ms). `transport=` tiene que servirle a un cliente async (`MockTransport`
    sí); uno sólo-sync se rechaza al construir. Los proxies del entorno SÍ se leen,
    como en `DescribeClient` (default de httpx). Mutaciones BA (el `wait_for`),
    BB (el hilo), BC (`asyncio.run`), BD (transporte sólo-sync), BE (el contexto
    TLS), AW (identity) y AY (redirect). `names/_deadline.py` se borró.
    ⚠️ **Tres correcciones, las tres escritas**: la ronda 2 dijo que leer por
    chunks hacía duro el timeout sync; la ronda 3 midió que no (headers goteando
    15,2 s, un header gzip goteando 10,15 s) y lo acotó en el socket; la ronda 4
    midió que tampoco (el connect sobre N direcciones: 5,00 s y 10,00 s). A la
    tercera ronda sin converger se dejó de parchar el síntoma y se achicó lo
    refutable: ya no hay motor sync. Y un aviso que salió de esta misma ronda: el
    primer doble de DNS del test comparaba el host con un `str`, anyio lo pasa en
    BYTES, y la consulta se fue al DNS real en verde — por eso el guard de red de
    `tests/conftest.py` es obligatorio (mutación BF).
13. 🔴 **El RPC se verifica contra su cadena**: un `eth_chainId` por cadena y por
    resolver antes del primer `eth_call`; si no coincide con la clave CAIP-2 es
    `rpc_unavailable`, nombrando la cadena servida y nunca la URL. Medido por el
    refutador: el RPC de Sepolia bajo `eip155:1` resolvía con
    `verified_onchain=True`. Las fixtures NO lo graban: `names_replay.py` lo
    contesta desde la clave (sintético, rotulado). Mutaciones AO y AX (esta no
    tenía test en el motor async hasta la ronda 3; desde la ronda 4 hay un solo
    motor y la verifican las dos variantes).
14. **Un `Reverted` en el reverse es `rpc_unavailable`** (el registro de ENS no
    revierte en `resolver()`: es un RPC que revierte todo), con el orden estricto
    del punto 8. Antes escapaba como excepción privada. Mutación AN.
15. **Cinco TLD son a la vez de UNS y de ICANN** (`UNS_ICANN_COLLISIONS`:
    graphics, gripe, guide, shiksha, travel; IANA versión 2026092400). No se elige
    en silencio: `unsupported_system` con la colisión en `detail`. Mutación AP.
    Y la FORMA se valida antes (regla UNS o ENSIP-15): `a b.travel` y `x..travel`
    son `invalid_name`, y un nombre en ancho completo sale en su forma normal,
    nunca crudo (ronda 3). Mutación AZ.
16. **`namehash` y `labelhash` públicos NORMALIZAN** (ENSIP-15) antes de
    hashear; los internos de `_hash.py` no, porque sólo ven nombres ya
    normalizados. Para que EM borre su copia (DUP-04). Mutación AQ.

**Ronda 5 del PR #6** (verificador sobre `8326ef2`: 1 P0, 0 P1, 1 P2, 5 P3; la
arquitectura convergió y NO se tocó):

17. 🔴 **El lock del resolver es REENTRANTE** (`threading.RLock`, `_resolver.py`).
    `_async_client()` lo toma y arma el cliente; el cliente por defecto pide el
    contexto TLS y `_ssl_context()` lo vuelve a tomar. Con un `Lock`, `await
    resolve()` con el transporte POR DEFECTO se colgaba para siempre, y ningún
    test corría la variante async sin `transport=`: todos pasaban un doble.
    Los tests del P0 corren en un hilo con `join(5)` y NO bajo un
    `asyncio.wait_for` externo, y es medido: un `threading.Lock` tomado dentro de
    una corrutina bloquea el hilo del loop, y el `wait_for` de 5 s seguía colgado
    a los 20 s. **Lo que enseña**: un camino que ningún test recorre con la
    configuración por defecto es el camino del usuario. Mutación CA.
18. **Al vencer se CANCELA, y se mira del lado del servidor** (la conexión
    cerrada 0,5 s después de volver). Volver a tiempo no alcanza: con
    `asyncio.wait` sin cancelar todos los tests de tiempo quedan verdes y la
    request sigue viva en el loop de quien llamó. Mutación CB.
19. **`transport=` y `async_transport=` distintos se rechazan** (antes
    `transport=` se ignoraba en silencio, en las dos variantes). El mismo objeto
    en los dos se acepta. Mutación CC.
20. **`run_blocking` le pregunta también a `sniffio`**: `resolve_sync()` dentro
    de `trio.run` daba `RuntimeError: Task got bad yield` (el loop privado de
    asyncio en el hilo de trio, y httpx eligiendo primitivas de trio). `sniffio`
    no es dependencia: si no está, ninguna librería que lo use está corriendo.
    `trio` está en el extra `dev` porque sin él el test se saltea. Mutación CD.
21. **El presupuesto cuenta desde la entrada**, ahora con test (armar el
    cliente tarda 0,8 s de 1,0 → vuelve en ~1,0 s). Mutación CE.
22. ⚠️ **El agujero negro de los tests se corrigió, y se deja escrito**: la
    receta vieja (`listen(0)` + 8 connects sin mirar) en macOS no retenía el SYN
    —1 connect en 0,00 s— y los tests pasaban igual, porque un connect que entra
    también vuelve dentro del presupuesto. Ahora se llena el backlog hasta que
    un connect de prueba de 0,3 s vence, y el test exige ≥ 2 connects. Mutación CH.
23. **El modo sync abre un cliente (TCP + TLS) por llamada**, sin keep-alive:
    medido contra un servidor keep-alive, 10 `resolve_sync()` → 10 conexiones y
    10 `await resolve()` → 1. Está en README y CHANGELOG. Y lo que el deadline no
    alcanza, escrito en `run_blocking`: un `getaddrinfo` abandonado sigue en su
    hilo hasta que el SO contesta (la llamada vuelve a tiempo; el hilo no).

**Antes del tag v0.7.0** (la revisión de las rutas de nombres de describe.net,
sobre `5ed00228`, encontró dos P1 del SDK; c0der los pidió acá antes de publicar,
2026-09-25):

24. 🔴 **Una URL elegida on-chain nunca hace LEVANTAR al resolver** (SDK-5).
    Un gateway CCIP, un `Location`, una metadata o un registro de avatar los
    escribe quien controla un resolver, un contrato o un nombre, y hacían escapar
    `ValueError` (`urlsplit` en `check_url`; `urljoin` en un redirect),
    `httpx.InvalidURL`, `RecursionError` (`json.loads` de 1.000+ `[`, que NO es un
    `ValueError`) y el `ValueError` de `int()` con más de 4.300 dígitos: un 500
    que elige el dueño del nombre. Medidos en py3.9.24, 3.12.12 y 3.13.6. Cada una
    se atrapa por su CLASE y va a lo que ese sitio ya contestaba ante una entrada
    ilegible: una URL → `Unavailable` (se prueba el gateway siguiente); una
    metadata → «not JSON»; un token id que no es uint256 → «not an ENSIP-12
    URI». **Nunca `except Exception`**: un test AST falla si un módulo de
    `names/` lo agrega (el único permitido es el `worker` de `run_blocking`, que
    re-levanta). Un `Location` que httpx mismo no parsea ya salía
    `RemoteProtocolError`, y quedó fijado como guarda. Mutaciones DA–DG y DJ.
25. **Un `not_found` de `reverse()` quiere decir que alguien contestó** (SDK-6).
    Con todos los sistemas salteados por falta de RPC es `rpc_unavailable` (no
    se cachea); sin ningún sistema de reverse habilitado, `unsupported_system`
    sin red. Antes era `not_found` con `verified_onchain=False`, cacheado 60 s.
    Mutaciones DH y DI.
26. ~~**SDK-4**, `resolved` literal en `to_dict()`~~: **descartada por c0der**
    (2026-09-25) — `error` ya lo dice. Se deja tachada para que nadie la vuelva a
    proponer sin saber que se decidió.

**Las reglas de plata de UNS, Avvy y el reverse se prueban con dobles
SINTÉTICOS** (`test_names_ronda2.py`, clase `Cadena`, rotulada): la cadena real no
ofrece a pedido un UNS que conteste la dirección cero ni un reverse de Avvy que
apunte a otra dirección. Antes de la ronda 2, sacar cualquiera de esas reglas
dejaba los 448 en verde (mutaciones AI, AJ, AK, AL).

**Las fixtures de `tests/fixtures/names/` son grabaciones** de
`scripts/grabar_fixtures_names.py` contra RPC públicos sin llave (secuencial,
pausa ≥ 1,1 s, se detiene al primer 429). `tests/names_replay.py` las reproduce
en orden y exige consumirlas todas. **No se editan a mano**: se regraban.
⚠️ En la ronda 4 el script quedó roto (importaba el `run_sync` borrado y su
`Grabadora` era sólo-sync) y nada lo vio. Desde la ronda 5 se prueba sin red
(`test_names_ronda5_script.py`): la `Grabadora` envuelve al reproductor, y
regrabar cada fixture desde sí misma tiene que dar la MISMA fixture. Mutaciones
CF y CG.

**Paridad**: el gemelo TypeScript todavía NO tiene `names`. Es una superficie
nueva, no un cambio del contrato núcleo — igual que el riel de partner — pero el
gemelo tiene que traer la misma forma de resultado (`NameResolution.to_dict()`
es el contrato de `/v1/names`). Seguimiento de los dos SDK.

### El riel de PARTNER — la novena superficie, y NO es una regla del contrato

`partner.py` + `partner=` del constructor. Entrar a las rutas medidas sin pagar,
firmando ERC-8128 con una wallet que describe tenga en su allowlist. Se agregó
el 2026-08-30 y **no entra a la tabla de arriba a propósito**: las ocho reglas
son el contrato núcleo compartido con el gemelo TypeScript; esto es un riel de
acceso que los dos implementan igual pero que no cambia ninguna de las ocho.

Lo que hay que saber antes de tocarlo (el porqué completo, en `partner.py`):

1. 🔴 **Acá no vive ninguna clave, y no puede empezar a vivir.** `partner=`
   recibe un OBJETO (`PartnerSigner`: `get_address()` + `sign_message()`). Ni
   env var, ni parámetro, ni default. Hay un test que falla si aparece un
   parámetro con «key» o «secret» en el nombre de `DescribeClient.__init__`.
2. **La firma la hace el `uvd-x402-sdk`** (`erc8128.sign_request`). Medido el
   2026-08-30: el primitivo YA existía en 0.70.0, así que no hubo nada que subir
   upstream. No lo reimplementes acá.
3. **Un riel roto LEVANTA, no degrada.** `PartnerSigningError` (el firmante
   falló, antes de la primera request) y `PartnerRejectedError` (402 pese a
   firmar). Las dos con `payment_sent is False`, y **el `payer` no se usa aunque
   esté configurado**. Es la decisión entera: un partner que cae al camino de
   pago en silencio no se entera hasta la factura.
4. **Se firma la URL que `httpx` construyó**, no una rearmada. La base cubre
   `@query` cuando existe, y firmar otra URL da un 402 que nadie entiende.
5. **Sólo las rutas medidas.** `paywall.py:772` del servicio decide «gratis»
   ANTES de mirar el partner (:794): firmar `/health` no cambia nada, y con un
   firmante remoto costaría una ida al KMS por cada lectura gratis.
6. **El riel NO mueve R5.** Que una ruta paga te salga gratis no la convierte en
   gratis: sigue levantando. El criterio es si hubo dinero de por medio.

### 🔴 Las tres que más fácil se rompen

1. **R5 se rompe rompiendo R1.** Un fail-open que devuelva `None` sin más hace
   indistinguible «describe está caído» de «esta wallet no tiene reputación». Se
   sostiene con DOS mecanismos: la distinción vive en el TIPO (`None` = no hubo
   respuesta; objeto con `global_score is None` = hubo respuesta y no hay
   evidencia) **y** ningún `None` sale sin pasar por `_observe()`. Si tocás
   `client.py`, no rompas ninguno de los dos. Y hay un tercer eje desde el
   2026-08-30: **no lo extiendas a las pagas «por simetría»**.

2. **R6 se rompe reordenando dos líneas.** `assert_recipient()` va **antes** de
   `create_authorization()`. Al revés existiría, aunque sea por un instante, una
   autorización firmada hacia una dirección no verificada. El test que lo ata
   usa un payer que explota si lo llaman.

3. **R3 se rompe "mejorando" `Caveat.code` a un `Enum`.** No lo hagas: un código
   nuevo del servicio tiene que llegar entero, y descartar un caveat es
   descartar la advertencia.

---

## Upstream-first — regla absoluta

El pago x402 lo resuelve `uvd-x402-sdk` (PyPI). **Acá no se reimplementa
EIP-3009, no se arma el sobre a mano, no se toca una clave.** Si al SDK de pagos
le falta algo, se sube ALLÁ primero y después se consume.

Corolario que ya se aplicó: **no hay una tabla `nombre de red → chain id` en
este repo**. La traducción se le pregunta a `uvd_x402_sdk.networks.base`, que es
su dueño. Una tabla local sería una copia que se pudre.

Segundo corolario, 2026-08-30: **la firma ERC-8128 del riel de partner tampoco
se escribe acá.** `uvd_x402_sdk.erc8128.sign_request` ya la hace, con los
vectores dorados de la flota de EM adentro del paquete. La regla se cumplió
midiendo ANTES de escribir: el mejor desenlace de upstream-first es descubrir
que el primitivo ya estaba.

**Huecos abiertos upstream (reportar, no parchear):**
- `uvd-x402-sdk` 0.70.0 **no publica `py.typed`** (medido 2026-08-30). Todo
  consumidor tipado pierde su firma. Declarado en un override de mypy.
- **Lo que NO es un hueco, y se anota para no volver a medirlo:** 0.70.0 firma
  ERC-8128 con `DEFAULT_CHAIN_ID = 8453` y `DEFAULT_VALIDITY_SEC = 300`, o sea
  exactamente lo que el gate del servicio exige (`describenet/partner.py`:
  `CHAIN_ID = 8453`, `MAX_VALIDITY_SEC = 300`). El `chain_id` se pasa EXPLÍCITO
  igual: heredar un default ajeno para un valor que el servidor compara es
  firmar contra lo que otro repo decida mañana.

---

## Cómo se escribe un comentario acá

Igual que en el repo del servicio: **una afirmación va con la medición que la
produjo, con su fecha.** «El timeout es 30 s porque el cold start midió 15,2 s y
el API Gateway corta a 29» es una razón; «30 s parece razonable» es una
sensación.

Y si corregís una afirmación vieja: **dejá la corrección escrita al lado, nunca
la borres.** Quien vuelva con el síntoma viejo merece saber por qué la receta
que recuerda ya no está.

Cifras: **o se leen vivas o llevan fecha.** Ningún total del índice se tipea a
mano; `GET /health` es la autoridad.

---

## Verificación discriminante — no es opcional

Un chequeo verde que **también habría estado verde con el bug** no prueba nada.
Antes de decir que un test ata algo: montá el estado malo y confirmá que se pone
rojo. Los nueve que ya se verificaron así (2026-08-30) están anotados en sus
docstrings:

| Mutación inyectada | Qué se puso rojo |
|---|---|
| `get_score(x: float) -> float` exportado | `test_ningun_publico_devuelve_un_numero` → `assert not ['get_score() -> float']` |
| `_opt_float` → `float(value or 0)` | 7 de 10 tests de R1 |
| `assert_recipient` movido DESPUÉS de firmar | los 2 tests de `DO_NOT_PAY` |
| `chain_name_for` → `None` (SDK ausente) | el mensaje de error nombraba la causa equivocada — **bug real, arreglado** |
| **A.** `wallet_breakdown()` metido en el fail-open (`-> Optional[Breakdown]` + `except`) | **14 rojos**: los 3 de `test_las_rutas_pagas_no_hacen_fail_open_ni_con_fail_open_true` (`DID NOT RAISE`), el de firmas, y 10 de R6 que ya existían |
| **B.** `leaderboard()`/`health()` fuera del fail-open (la regla vieja) | **7 rojos**: los 6 de `test_las_dos_gratis_hacen_fail_open_y_lo_reportan` + el de «nunca `[]`» |
| **C.** `return []` en vez de `return None` en el `except` de `leaderboard()` | **4 rojos**, incluido `assert [] is None` en `test_un_fallo_de_leaderboard_NUNCA_devuelve_una_lista_vacia` |
| **D.** marcar TODO `_paid` y no sólo el tramo posterior a la firma | **1 rojo**: `test_un_timeout_ANTES_de_firmar_NO_marca_ningun_pago` (`assert True is False`) — y el de DESPUÉS quedó verde |
| **E.** sacar `mark_payment_sent()` (excepción pelada) | **7 rojos**: los 4 de `payment_sent` + los 3 de rutas pagas — y el de ANTES quedó verde |
| **F.** 🔴 sacar la rama de `PartnerRejectedError` (el partner cae al camino de pago) | **5 rojos**, y el que importa falla DENTRO del payer: `AssertionError: EL CLIENTE PAGÓ`, con `amount_usd=Decimal('0.01')` en el traceback |
| **G.** firmar una URL rearmada a mano (`f"{base}{path}"`, sin la query) | **1 rojo**: `assert None == '?snapshot=true'` — la línea `@query` de la base firmada contra la que salió |
| **H.** firmar también las rutas GRATIS («por simetría») | **3 rojos**: los 3 casos de `test_las_rutas_GRATIS_no_se_firman` |
| **I.** que `sign_partner_headers` devuelva headers vacíos en vez de levantar | **1 rojo**: `DID NOT RAISE PartnerSigningError` |
| **J.** 🔴 «completar la tabla»: darle a `DescribeTimeout` un `recovery` que dice «Reintentá…» | **3 rojos**, y el que importa es `assert 'Reintenta en unos segundos…' is None` — el vacío honesto está fijado |
| **K.** que `PartnerSigningError` arme su `recovery` en `__init__` interpolando el mensaje | **2 rojos**: el secreto de mentira aparece dentro del consejo (`'CLAVE-FALSA-DE-TEST' is contained here`) y `una.recovery is otra.recovery` deja de valer |
| **L.** sacarle a `PartnerRejectedError` su `recovery` (hereda la de `PaymentRequiredError`) | **6 rojos**, con el mensaje que nombra el bug: «hereda la de PaymentRequiredError» — publicaría «configurá `payer=`» ante un riel roto |
| **M.** `exc.status_code == 404` → `== 4040` en el guard que saca al 404 antes del respaldo (`client.py:687`) | **2 rojos**: `test_un_404_NO_dispara_el_respaldo` y —el que prueba que no es redundante— `test_r4_sin_datos_no_es_error.py::test_404_no_lanza_ni_con_fail_open_apagado`. El 404 se cae a la vez del respaldo y de R4 |
| **N.** sacar `replace(respaldo, source="fallback")` (`client.py:719`), o sea devolver el respaldo sin marcar | **1 rojo**: `test_el_respaldo_se_marca_y_no_se_hace_pasar_por_el_indice` |
| **O.** 🔴 colapsar el `None` en `[]` al parsear (`body.get("caveats_not_computed") or []`) — 2026-09-15 | **6 rojos**: los 3 de `test_una_API_que_no_declara_da_None_y_NUNCA_lista_vacia` y los 3 de `test_el_gate_NO_pasa_con_una_API_que_no_declaro`. Los tests de la captura viva quedaron VERDES, porque la viva siempre trae la lista |
| **P.** que `require_full_caveats()` deje pasar el `None` | **5 rojos**: los 3 del gate con API vieja, el de la declaración ilegible y `test_un_respaldo_no_declara_y_el_gate_no_lo_deja_pasar` |
| **Q.** que `CaveatsNotComputedError` herede de `DescribeError` | **4 rojos**: `test_el_error_NO_es_un_DescribeError_y_el_fail_open_del_consumidor_no_lo_traga`, y 3 de `test_recovery.py`, que la recorre SOLA como subclase nueva (anclajes, filtración, constante) |
| **R.** `author_class` ausente o basura → `"rater-authored"` | **5 rojos**: `test_una_fila_sin_la_clase_es_None_y_NO_rater_authored` + los 4 de basura |
| **S.** `author_class` con set cerrado (lo desconocido se descarta) | **1 rojo**: `test_una_clase_desconocida_llega_entera_y_no_tumba_la_lectura` |
| **T.** filtrar las entradas ilegibles de `caveats_not_computed` en vez de dar `None` | **5 rojos**: 4 de `test_una_declaracion_ilegible_es_None_y_no_una_lista_filtrada` + `test_el_gate_con_una_declaracion_ilegible_no_pasa`. Las 3 formas que no son lista quedaron verdes: la mutación sólo toca el loop |
| **U.** sacar el guard `isinstance(result, WalletReputation)` del gate | **3 rojos**: los 3 de `test_el_gate_solo_acepta_un_WalletReputation` (salía `AttributeError`, no el `TypeError` que nombra el error) |
| **V.** sacar `FACILITATOR_AUTHORED` de `KNOWN_CAVEAT_CODES` | **2 rojos**: `test_las_nueve_estan_y_son_nueve` (hoy `test_las_diez_estan_y_son_diez`) y `test_el_code_facilitator_authored_es_conocido_y_no_es_de_la_puerta_gratis` |
| **W.** 🔴 sacar el guard `isinstance(declared, list)` del gate (queda sólo `if not declared`) — 2026-09-15 | **6 rojos**: los 6 de `test_el_gate_deja_pasar_la_lista_vacia_y_NINGUN_otro_vacio` (`()`, `""`, `0`, `False`, `{}`, `set()` → `DID NOT RAISE`), y el resto de la suite VERDE: el parser nunca arma esas formas, así que sólo un `WalletReputation` construido a mano muestra el bug |
| **X.** sacar `THIN_CHAIN` de `FREE_GATE_CAVEAT_CODES` (queda sólo `burn-address`) — 2026-09-15 | **1 rojo**: `test_el_subset_de_la_puerta_gratis`, y el resto de la suite VERDE |
| **Y.** 🔴 sacar el guard de la dirección cero en `names/_ens.py::forward` — 2026-09-24 | **3 rojos**: la grabación de `default.reverse` en sync y en async, y `test_un_resolver_real_que_contesta_la_direccion_cero_da_not_found` |
| **Z.** 🔴 sacar la comparación `pointed != address` de `_ens.confirm` | **1 rojo**: `test_un_nombre_que_apunta_a_OTRA_direccion_es_reverse_mismatch` — con el forward GRABADO de `0xultravioleta.eth` confrontado con la dirección de Jesse |
| **AA.** 🔴 que `parse_name_resolution` herede `verified_onchain` del cuerpo | **2 rojos**: `test_la_api_http_devuelve_verified_onchain_False_aunque_el_servidor_diga_True` y `test_un_destino_de_pago_exige_verified_onchain` |
| **AB.** cachear los errores que no son respuesta de la cadena (`rpc_unavailable`…) | **4 rojos**: los 3 del parametrizado de `NameCache` y `test_un_fallo_de_transporte_no_se_cachea_y_la_siguiente_pregunta_de_nuevo`. Re-medido en la ronda 2: **9 rojos** (se suman los que miran la caché vacía tras un `rpc_unavailable`); en la ronda 3: **10** |
| **AC.** saltear `check_expiry` antes de resolver | **24 rojos**: toda grabación ENS/Basenames deja de coincidir con lo que se pidió en vivo — el vencimiento es la primera lectura. Re-medido en la ronda 2: **25**; en la ronda 4: **27** |
| **AD.** saltear el chequeo ENSIP-15 del nombre reclamado en `confirm` | **3 rojos**: la grabación del hook de CoW en sync y async, y `test_un_reverse_no_normalizado_es_reverse_mismatch` |
| **AE.** normalizar con `typed.lower()` en vez de ENSIP-15 (el bug de EM) | **6 rojos**: 5 nombres inválidos que pasaban y `test_la_normalizacion_es_ENSIP15_y_no_lower` |
| **AF.** sacar el chequeo `ownerOf == dueño` del avatar NFT | **1 rojo**: `test_erc721_que_el_nombre_NO_posee_no_da_url` (doble SINTÉTICO: no hubo grabación, ver arriba) |
| **AG.** que `check_url` deje pasar IPs no globales | **5 rojos**: 4 del guard (`127.0.0.1`, `10.0.0.8`, `169.254.169.254`, `[::1]`) y la metadata en IP privada |
| **AH.** en el reverse, que un sistema caído no corte (`except Unavailable` → otra excepción) | **1 rojo**: `test_reverse_con_un_sistema_de_arriba_caido_no_contesta_con_uno_de_abajo`. ⚠️ Antes de ese test la mutación daba **0 rojos**: la regla no estaba atada, y se ató el mismo día. Re-medido en la ronda 2 (ahora el `except` es `(Unavailable, Reverted)`): **3 rojos**; en la ronda 4: **2** |
| **AI.** 🔴 sacar `pointed != address` del reverse de UNS/Avvy (`_resolver.py::_reverse_one`) — ronda 2 | **2 rojos**: el reverse UNS y el de Avvy cuyo nombre apunta a otra dirección (dobles SINTÉTICOS). Antes: 448 verdes |
| **AJ.** 🔴 aceptar la dirección cero en `_uns.forward` | **2 rojos**: `0x000…0` y `0X000…0`. Antes: 448 verdes |
| **AK.** 🔴 aceptar la dirección cero en `_avvy.forward` | **2 rojos**: `0x000…0` y `0X000…0`. Antes: 448 verdes |
| **AL.** sacar el chequeo de forma normal del reverse de UNS/Avvy | **1 rojo**: `Evil.crypto`, que apunta de vuelta a la dirección y aun así no se muestra. Antes: 448 verdes |
| **AM.** 🔴 no mirar el plazo entre chunks (`_proto._read_capped`) | **2 rojos**: el gateway CCIP que gotea (grabación real de `jesse.base.eth` con el goteo sintético) y el RPC que gotea. **Retirada en la ronda 4**: `_read_capped` era del motor sync, que se borró; esos dos tests los cuida ahora BA |
| **AN.** que el reverse no atrape `Reverted` | **2 rojos**: el RPC que revierte todo, sync y async (levantaba una excepción privada) |
| **AO.** 🔴 no verificar `eth_chainId` contra la clave CAIP-2 | **1 rojo**: `test_un_RPC_de_otra_cadena_bajo_la_clave_de_mainnet_es_rpc_unavailable`. En la ronda 3 (el test pasó a sync + async): **2** |
| **AP.** mandar las cinco colisiones ICANN/UNS a UNS | **5 rojos**: una por TLD. En la ronda 3: **6** (se suma el de ancho completo) |
| **AQ.** `namehash` público sin normalizar | **1 rojo**: `test_namehash_publico_normaliza_antes_de_hashear` |
| **AR.** convertir un error JSON-RPC (`-32005 limit exceeded`) en `Reverted` | **1 rojo**: pasaba a `not_found` y se cacheaba. Antes: 448 verdes |
| **AS.** seguir un `OffchainLookup` cuyo sender no es el resolver | **1 rojo**: el doble del gateway registra que SÍ se le pidió |
| **AT.** que un 4xx del gateway pase a la URL siguiente | **2 rojos**: 404 y 403 |
| **AU.** que `require_onchain_address` acepte la dirección cero | **1 rojo**: un `NameResolution` armado a mano con `0x000…0` |
| **AV.** 🔴 que el backend de `_deadline.py` no acote el socket al plazo — ronda 3 | **4 rojos**: el RPC local que gotea headers y el que gotea un gzip (FCOMMENT), el gateway local que gotea headers, y el tope del backend. Servidor REAL en 127.0.0.1: sin la cota, headers goteando = 11,43 s. **Retirada en la ronda 4**: `_deadline.py` se borró (sync = el motor async); esos casos los cuida ahora BA |
| **AW.** leer el cuerpo gzip de un gateway (sacar `_refuse_encoded`) | **1 rojo**: `test_un_gateway_pide_identity_y_un_cuerpo_gzip_no_se_lee`. En la ronda 4 (el test corre sync y async, P3-b): **2** |
| **AX.** 🔴 sacar el `eth_chainId` del motor ASYNC | **1 rojo**: el caso `[async]` del test de Sepolia. Antes de la ronda 3: 476 verdes. En la ronda 4 (un solo motor): **2**, sync y async |
| **AY.** seguir un redirect pasado el plazo | **1 rojo**: `test_un_redirect_no_se_sigue_pasado_el_presupuesto`. Antes: 476 verdes |
| **AZ.** decidir la colisión antes que la forma | **4 rojos**: `a b.travel`, `x..travel`, `a_b.guide` y el de ancho completo, que salía crudo de `normalize()` |
| **BA.** 🔴 quitar EL `asyncio.wait_for` de `run_bounded` — ronda 4, la que pidió la decisión | **8 rojos**: los casos A/B/C/D contra el servidor local (RPC con cuerpo, headers y gzip goteando; gateway con cuerpo y headers goteando), los dos goteos de la ronda 2 y `test_async_el_timeout_es_duro_aunque_el_servidor_no_conteste`. En la ronda 5: **11** (se suman el de cancelación y los dos del presupuesto desde la entrada). ⚠️ En la ronda 5 esta mutación **colgó la suite 20 min**: el doble del test del presupuesto dormía 3600 s y, sin el deadline, los esperaba; ahora duerme 5 s y falla |
| **BB.** que `run_blocking` no use un hilo propio cuando el hilo que llama ya corre un loop | **1 rojo**: `test_un_sync_llamado_desde_codigo_async_da_lo_mismo` (los loops no se anidan). En la ronda 5: **2** (se suma el de trio) |
| **BC.** usar `asyncio.run` en vez del loop propio | **1 rojo**: `test_sync_un_DNS_lento_no_retiene_la_llamada` — `asyncio.run` espera al `getaddrinfo` del executor |
| **BD.** aceptar un transporte sólo-sync | **1 rojo**: `test_un_transporte_solo_sync_se_rechaza_al_construir`. En la ronda 5: **2** (se suma el de dos transportes) |
| **BE.** un contexto TLS por llamada | **1 rojo**: `test_un_solo_contexto_TLS_por_resolver` (0,34 s cada uno, medido) |
| **BF.** 🔴 el guard de red: el doble de DNS de la ronda 4 vuelve a comparar bytes con `str` (el bug que tuvo) | **1 ERROR de sesión**: el test pasa en VERDE —la consulta se va al DNS real y falla rápido— y el guard de `tests/conftest.py` hace fallar la sesión con el intento anotado (el único rojo de la corrida es ese error de teardown). En la ronda 5: **2 rojos + el error de sesión** — el test ya no pasa en verde: cuenta 0 connects (CH) |
| **CA.** 🔴 volver al `threading.Lock` (el P0 de la ronda 5) | **5 rojos**: las cuatro operaciones async con el transporte por defecto (`resolve`, `reverse`, `text`, `avatar`) y el de cancelación, que también lo usa. Antes: 495 verdes. Cada uno falla a los 5 s del `join`, no cuelga |
| **CB.** 🔴 no cancelar al vencer (`asyncio.wait` sin cancel en vez de `wait_for`) | **1 rojo**: `test_al_vencer_se_CANCELA_y_el_servidor_ve_cerrarse_la_conexion`. Antes: 495 verdes — todos los tests de TIEMPO quedan verdes, porque la llamada vuelve igual |
| **CC.** aceptar `transport=` y `async_transport=` distintos | **1 rojo**: `test_dos_transportes_distintos_se_rechazan_y_el_mismo_se_acepta` |
| **CD.** que `run_blocking` sólo le pregunte a asyncio (sin `sniffio`) | **1 rojo**: `test_un_sync_dentro_de_trio_no_revienta` (`Task got bad yield`) |
| **CE.** `left = self._timeout` (el presupuesto no cuenta desde la entrada) | **2 rojos**: el test del presupuesto, sync y async (~1,8 s contra 1,0). Antes: 495 verdes |
| **CF.** 🔴 el script de `8326ef2` entero (`git show`) | **20 rojos**: los 20 de `test_names_ronda5_script.py` (`ImportError: run_sync`). Antes: ningún test lo importaba |
| **CG.** la `Grabadora` hereda de `httpx.BaseTransport` (con el import ya arreglado) | **2 rojos**: el de importación (`issubclass`) y el de `poseidon_tld_avax` (`AsyncClient` le pide `__aenter__`). ⚠️ Los otros 18 quedan VERDES: el resolver no mira el tipo de `async_transport=` y la `Grabadora` igual tiene `handle_async_request` — el que ata el tipo es el `issubclass` |
| **CH.** el agujero no retiene el SYN (`listen(64)`, sin rellenos: lo que pasaba en macOS) | **2 rojos**: N=3 y N=5 cuentan 1 connect. El tiempo quedó VERDE en los dos — el connect entra, el servidor no contesta, y el presupuesto corta igual: sin contar connects el test no probaba el agujero |
| **M0.** los tres archivos de `names/` (`_proto`, `_avatar`, `_resolver`) como en `5ed00228` — 2026-09-25, SDK-5 y SDK-6 | **20 rojos**, todos de `test_names_sdk5.py` / `test_names_sdk6.py`: los tests reproducen los bugs. Quedan VERDES 7, y es lo esperado: los 4 de los `Location` que httpx ya rechazaba, los 2 del reverse que sí contesta `not_found` y el test AST |
| **DA.** 🔴 `check_url` sin el `try` alrededor de `urlsplit` | **7 rojos**: los gateways `https://[x/…` y `https://[zzz]/…` (sync y async), el de «se saltea y el siguiente contesta» y la metadata en `https://[x/…` (sync y async) |
| **DB.** que `_do_async` no atrape `httpx.InvalidURL` | **2 rojos**: el gateway `https://gw.example/\x01…`, sync y async |
| **DC.** que `_redirect_target` no atrape el `ValueError` de `urljoin` | **2 rojos**: `Location: https://[x/`, sync y async. Los `Location` que httpx mismo rechaza (`//[zzz]/a`, `\x01`) quedan VERDES: ya salían `RemoteProtocolError` |
| **DD.** sacar `RecursionError` de la tupla de `_ccip_fetch` | **2 rojos**: el gateway que contesta 200.000 `[` anidados, sync y async |
| **DE.** sacar `RecursionError` de la metadata https (`_avatar._metadata`) | **1 rojo**: `test_una_metadata_https_con_JSON_anidado_sin_fin_no_da_URL` |
| **DF.** sacarlo de la rama `data:` | **1 rojo**: `test_una_metadata_data_URI_con_JSON_anidado_sin_fin_no_da_URL` |
| **DG.** no contar los dígitos antes de `int()` en `nft_reference` | **2 rojos**: el avatar con token id de 5.000 dígitos y el de los bordes del uint256 |
| **DH.** 🔴 el final de `_reverse_steps` como en `5ed00228` (SDK-6 deshecho) | **2 rojos**: el reverse sin ningún RPC, sync y async. El de «uno contesta que no hay nombre → `not_found`» queda VERDE: es el borde que no cambió |
| **DI.** sin la respuesta temprana de «ningún sistema de reverse habilitado» | **1 rojo**: `systems=("ens-dns",)` sale `rpc_unavailable` en vez de `unsupported_system` |
| **DJ.** `except Exception` en vez de `except ValueError` en `check_url` | **1 rojo**: el test AST. El comportamiento no cambia y los otros 26 tests nuevos quedan VERDES: sólo el de estructura ve la violación |

**M y N son el par que sostiene el respaldo** (`fallback_reader`, PR #2 de
KarmaKadabra), y cada una fija un borde distinto. **M** fija *cuándo* corre: un
404 es una RESPUESTA, no un fallo, y consultar una segunda fuente para
contradecirlo convertiría el respaldo en una forma de buscar el número que a uno
le gusta más. Su segundo rojo, el de R4, es la parte que importa: prueba que el
guard del 404 es uno solo y que el respaldo se colgó del lado correcto.
**N** fija *qué devuelve*: sin la marca, un consumidor no puede distinguir el
índice de su plan B y terminaría publicando como canónico un número que
describe.net no firmó — la misma enfermedad que R1 persigue con `None` vs `0`.

**O y P son el par que sostiene el `None` de `caveats_not_computed`**, uno por
capa: O en el parser (el `None` no llega al gate), P en el gate (llega y se
deja pasar). Las dos dejan verdes a TODOS los tests de la captura viva, y ése es
el aprendizaje: la captura viva nunca muestra una API vieja, así que un test que
sólo mire lo que hoy manda el servicio no puede ver este bug. **R y S son el par
por borde de `author_class`**, igual que A y B: R se pone rojo si la ausencia se
lee como una clase, S si el set se cierra.

**A y B son el par que sostiene la R5 corregida**, uno por borde: A se pone rojo
si alguien mete las pagas adentro, B si alguien saca a las gratis. **D y E son el
par que sostiene `payment_sent`**: el mismo timeout, el mismo cliente, y la única
diferencia es de qué lado de la firma ocurre — cada mutación pone rojo un lado y
deja verde el otro, que es lo que prueba que la distinción existe y no es
decorativa.

**F es la que justifica el archivo entero del riel de partner**, y su rojo lo
dice mejor que cualquier docstring: sin esa rama el cliente no falla, no avisa y
no rompe nada — **paga**, y el traceback muestra el `Decimal('0.01')` que estaba
por gastar. Un test que sólo mirara «el partner recibe su breakdown» habría
quedado verde con el bug adentro, porque un cliente que paga religiosamente
también devuelve el breakdown. **F y H son el par por borde**, igual que A y B:
F se pone roja si el riel deja de proteger las rutas donde hay plata, H si
alguien lo extiende a las rutas donde no la hay.

**J, K y L son las tres del `recovery`, y cada una cuida una propiedad
distinta**: J el **vacío honesto** (que nadie complete la tabla con un
«reintentá», que es un booleano redactado en prosa); K el **guard de secretos**,
que es estructural y no defensivo —el texto es una constante de clase, así que
no puede arrastrar la URL de un RPC con su key— y su rojo lo prueba enseñando el
secreto de mentira DENTRO del consejo; L que **declarar sea obligatorio**, con
el caso real: `PartnerRejectedError` hereda de `PaymentRequiredError` y sin
declarar la suya le diría «configurá `payer=`» a alguien con el riel roto, que
es exactamente el consejo que su docstring existe para prohibir. Un test de
`recovery` que sólo mirara «los textos no están vacíos» habría quedado verde con
las tres adentro.

⚠️ **B no pone rojo el test de firmas** (`test_la_tabla_de_nullabilidad_...`),
porque quitar el `except` no toca la anotación. Está anotado porque es la clase
de hueco que hace creer que un test cubre más de lo que cubre: el que ata el
comportamiento es el parametrizado, el de firmas sólo evita que la anotación
publicada mienta.

---

## Lo que este SDK NO hace, y no es un olvido

- **No cachea reputación.** El TTL correcto depende de para qué se lee (mesh usa
  12 min para un canal; un perfil quiere el valor caliente). Un caché adentro del
  SDK con un default equivocado es peor que ninguno. `refreshed_at` viaja para que
  quien llama decida. ⚠️ Acotado el 2026-09-24: el resolver de NOMBRES sí cachea
  (`NameCache`), porque las dos copias que reemplaza ya cacheaban y el default
  está medido (los 300 s de EM); tiene tope, TTL negativo más corto y
  `cache=False` lo apaga.
- **`DescribeClient` no tiene API async.** Ver riesgos abajo — es la deuda más
  concreta. ⚠️ El resolver de nombres sí la tiene, sobre los mismos pasos que la
  sync (`names/_proto.py`).
- **No reintenta un pago.** El nonce se consume en el settlement: reenviar la
  misma credencial no vuelve a pagar. Un `retries=` quemaría credenciales.
- **No lee env vars.** Todo entra por constructor. Es lo que lo hace testeable
  sin entorno y embebible en cualquier proceso. **El riel de partner no es una
  excepción**: recibe un objeto que firma, no una clave ni el nombre de la
  variable donde vive. ⚠️ Precisión del 2026-09-24 (ronda 4 del PR #6): el código
  del SDK no lee env vars, pero **httpx sí lee los proxies del entorno**
  (`HTTP(S)_PROXY`, `NO_PROXY`) por default, en `DescribeClient` y en `names` por
  igual. En la ronda 3 `names` los apagó llamándolo «regla del SDK», y no lo era:
  se volvió al default, igual que `DescribeClient`.
- **No custodia ninguna clave.** ⚠️ Corregido el 2026-08-30 y se deja escrito:
  hasta hoy esto se decía como «no firma», y con el riel de partner eso ya no es
  exacto — el SDK **sí produce una firma ERC-8128**. Lo que la frase quería
  decir sigue intacto y es lo que importa: la firma la hace el `uvd-x402-sdk`
  con un objeto que inyecta el consumidor, y acá no vive, no se lee y no se
  guarda ninguna clave privada.

---

## Estado, y lo que NO está decidido

- 🔴 **Nada está publicado.** Ni PyPI, ni GitHub. Sólo commits locales.
- **El nombre `uvd-describe-sdk` es hipótesis a ratificar.** Saul nunca lo dijo.
- **Saul dijo «UN repositorio», singular.** Esto son dos (uno por lenguaje,
  siguiendo el precedente de la casa). Le toca a él ratificarlo.

### Preguntas abiertas — NO las resuelvas por tu cuenta

1. ~~**El «riel gratis» para los productos propios** (EM / mesh / KK) que Saul
   pidió el 2026-08-14. (…) **No inventes un header de partner.**~~
   **RESUELTO el 2026-08-30** — ver §«El riel de PARTNER» arriba y
   `src/uvd_describe_sdk/partner.py`.

   ⚠️ **Se deja tachado y no borrado, y esta corrección enseña algo.** La
   entrada vieja razonaba: *el servicio no tiene cuentas ni API keys, así que no
   hay forma obvia de distinguirlos* ⇒ «no inventes un header de partner». **La
   premisa era correcta y la orden también** —inventar un header habría sido el
   error— **pero la conclusión implícita, que no había forma, era falsa**: la
   forma existía y no era un token sino una **firma con la wallet**, el mismo
   primitivo de identidad que la cara paga ya usaba; divergen sólo en política
   (allowlist contra pago), no en mecanismo. Lo que salvó a este repo fue el «no
   lo decidas solo», y lo que lo resolvió fue que **el servicio lo construyó
   primero** (`describe-net/describenet/partner.py`, 2026-08-28): este SDK sólo
   lo habla.

   Y lo que SIGUE abierto del hilo, que es la parte que no le toca a este repo:
   **quién entra en la allowlist lo decide Saul.** Acá no hay ninguna lista, ni
   puede haberla — al 2026-08-30 Execution Market está dado de alta; KK y mesh
   NO.
2. ~~**El alcance de R5.**~~ **RESUELTO el 2026-08-30** — ver §«R5, el alcance»
   arriba: gratis degradan, pagas levantan. Se deja tachado y no borrado: quien
   vuelva con la pregunta merece encontrar la respuesta donde la dejó.
   Lo que SÍ queda abierto de ese hilo: **confirmar un settlement desde el
   cliente no se puede** con lo que este SDK tiene. Necesitaría preguntarle al
   facilitator o a la cadena, y eso es otra dependencia. No lo inventes.
3. **Sync vs async.** El cliente de Execution Market es `async` y este SDK es
   sync: para adoptarlo tendría que envolverlo en un thread. La salida sería un
   `aio.py` de transporte fino reusando estos parsers, **sin duplicar una línea
   de política**. No está escrito. ⚠️ 2026-09-24: el patrón ya existe en el repo
   — `names/_proto.py` (pasos como generadores sans-IO, UN motor `run_async`
   bajo `run_bounded`, y `run_blocking` para la variante sync) — y es el molde si
   algún día se hace para `DescribeClient`. Ojo con lo que costó llegar ahí: tres
   rondas del PR #6 encontraron agujeros en un motor sync aparte.

---

## Git

- **Commits en español**, conventional (`feat(scope):`), con la evidencia medida
  en el cuerpo y el trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Nunca `git add -A`** — staging por archivo.
- **Push sólo con OK explícito de Saul, por push.**
