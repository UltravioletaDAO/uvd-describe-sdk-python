# uvd-describe-sdk (Python)

Cliente Python del índice de reputación ERC-8004 de **describe** — `api.describe.net`.

```bash
pip install uvd-describe-sdk            # el camino gratis: una sola dependencia (httpx)
pip install "uvd-describe-sdk[x402]"    # + pagar las rutas medidas
pip install "uvd-describe-sdk[partner]" # + el riel de partner (firma, no paga)
```

```python
from uvd_describe_sdk import DescribeClient, format_score

with DescribeClient(product="mi-app") as describe:
    rep = describe.wallet("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")

    if rep is None:
        print("el índice no contestó")           # ← NO es «sin reputación»
    elif not rep.has_identity:
        print("no registrada")
    elif rep.global_score is None:
        print("registrada, todavía sin calificar")
    else:
        print(format_score(rep.global_score), "·", rep.policy_version)
```

---

## Las tres cosas que hay que saber antes de usarlo

### 1. `None` nunca es cero, y nunca es «no tiene reputación»

Hay **tres hechos distintos** y el tipo los mantiene distintos:

| Situación | Cómo se ve |
|---|---|
| No se pudo leer el índice | `rep is None` |
| Wallet no registrada | `rep.has_identity is False` |
| Registrada y sin calificar | `rep.global_score is None` |

Un `0` en un score afirmaría *«lo calificaron pésimo»* sobre alguien a quien
nadie calificó. Medido en producción: un prior de 50 pintaba badge *silver* a
ejecutores sin historia, y la conclusión escrita fue **«el 50 es peor que un
hueco»**.

### 2. Ningún método devuelve un número pelado

Todo resultado trae `policy_version`, `caveats[]` y su fuente. Si querés el
número solo, lo sacás del objeto a mano — **y eso es a propósito**: la tesis del
producto es que *un score sin sus calificadores es un rumor*, y el gesto de
sacarlo deja escrito en tu código que decidiste tirar el contexto.

Un test de contrato recorre `__all__` y falla si aparece una función que
devuelva `float`. Está verificado por mutación: se inyectó un
`get_score(x: float) -> float`, se puso rojo nombrándolo, y se removió.

### 3. Se ramifica por `caveats[].code`, jamás por `caveats[].text`

Lo declara el schema del servicio: *«Codes are permanent; text is not.»* Los
ocho están exportados para que no los tipees:

```python
from uvd_describe_sdk import CaveatCode, is_known

if CaveatCode.BURN_ADDRESS in rep.caveat_codes:
    ...  # nadie controla esta wallet: cualquiera puede calificarla
```

`Caveat.code` es un `str`, **no un `Enum`**. Un enum cerrado haría que un código
nuevo del servicio rompa o desaparezca — y descartar un caveat es descartar la
advertencia. `is_known(code)` dice si es de los ocho conocidos; uno desconocido
igual llega entero y hay que mostrarlo.

---

## La superficie

| Método | Precio | Ruta |
|---|---|---|
| `wallet(address)` → `WalletReputation \| None` | **gratis** | `GET /wallets/{w}/chains` |
| `leaderboard()` → `list[LeaderboardRow] \| None` | **gratis** | `GET /leaderboard` |
| `health()` → `IndexHealth \| None` | **gratis** | `GET /health` |
| `badge_url(address)` → `str` | **sin red** | construye la URL, no la pide |
| `wallet_breakdown(address)` → `Breakdown` | $0,01 ($0,05 con `snapshot=True`) | `GET /reputation/wallet/{w}` |
| `agent(network, agent_id)` → `AgentReputation` | $0,02 | `GET /reputation/agent/{n}/{id}` |

**Las tres gratis son nullables; las dos pagas no lo son nunca.** No es un
accidente de la tabla: es la regla del fallback, abajo.

**Y si describe dio de alta tu wallet, las dos pagas te salen $0** sin dejar de
ser «pagas» para todo lo demás. Es el riel de partner, abajo.

**Gratis primero, y no por cortesía.** El propio 402 lo dice en su
`free_preview`: *«Si no hay reputación ahí, este cobro no devuelve nada.»*
`wallet()` es la puerta; la descomposición paga se pide después.

---

## El fallback (R5) — lo más fácil de hacer mal

Saul lo pidió textual el 2026-08-28: *«pon un fallback si es que describe está
caído»*. El default es `fail_open=True`.

Pero un fail-open ingenuo **rompe la regla 1**: si «el índice está caído» y
«esta wallet no tiene reputación» devolvieran lo mismo, el fallback habría
fabricado exactamente la confusión que la regla 1 existe para impedir. Y no es
hipotético — le costó un reporte equivocado a KarmaKadabra el 2026-08-28, en el
gate que decide con quién se comercia.

Se resuelve con **dos mecanismos, no con un comentario**:

1. **La distinción vive en el tipo.** Una wallet que el índice sí pudo leer
   vuelve como objeto, aunque no tenga ni una calificación. `None` significa una
   sola cosa: *no hubo respuesta*.
2. **Ningún `None` sale callado.** Siempre pasa por el observador y siempre
   loguea en WARNING. No existe el modo silencioso.

```python
def a_mi_metrica(err):
    metricas.incr("describe.caido", tags={"kind": err.kind})

DescribeClient(product="mi-app", on_error=a_mi_metrica)
```

**Lo que el fail-open NO tapa:** `PaymentRequiredError` y `DoNotPayError`. Es
para la *disponibilidad del índice*, no para tu configuración ni para un desvío
de fondos.

### 🔴 Qué cubre y qué no — la línea es si hubo dinero de por medio

| Ruta | Precio | Ante un fallo de servicio |
|---|---|---|
| `wallet()` · `leaderboard()` · `health()` | gratis | `None`, siempre observado. **Nunca `[]`.** |
| `wallet_breakdown()` · `agent()` | $0,01 / $0,02 | **LEVANTAN. Siempre.** |

Las pagas levantan **incluso con `fail_open=True` explícito**, y la razón es
dinero, no simetría: entre firmar el sobre x402 y recibir la respuesta hay una
ventana en la que el USDC ya se movió. Devolver `None` ahí te oculta que
gastaste — es una credencial gastada sin recibo, y nada distingue *«pagué y se
cayó»* de *«no había nada que traer»*. Un fallo ruidoso después de pagar es
recuperable (reintentás, registrás, reclamás); un `None` silencioso no lo es.
No es una preferencia tuya: es una propiedad del método. Un flag de
disponibilidad no puede comprar el derecho a tragar un recibo.

Y las gratis sí, las **tres** — no sólo `wallet()`: un fallo ruidoso en algo
gratis te obliga a escribir tu propio `try/except` para algo que el SDK ya sabe
hacer, que es justo la duplicación que este SDK viene a borrar.

`None` y **nunca** `[]`: una lista vacía afirma que *el índice está vacío*, que
es una afirmación falsa sobre el mundo. `None` dice *no pude preguntar*.

### Si una ruta paga falla, ¿gastaste?

```python
try:
    br = describe.wallet_breakdown("0x97cd…0996")
except DescribeError as err:
    if err.payment_sent:
        # La autorización EIP-3009 ya estaba firmada y despachada: el USDC PUDO
        # haberse movido. Te toca reconciliar.
        reconciliar(err.payment)   # amount_usd, network, resource, transaction_hash
    else:
        # Se cayó antes de firmar. No salió una credencial, no gastaste nada.
        reintentar()
```

**Se ramifica por el atributo, nunca por el texto** — igual que `err.kind` y que
`caveats[].code`. El mensaje también lo dice, porque quien lee un traceback en un
log a las 3 AM no tiene el objeto a mano; pero el texto es para leer y el
atributo es para decidir.

⚠️ **Límite conocido, y está escrito porque importa:** `payment_sent=True`
prueba que la credencial **salió**, no que el settlement ocurrió. Lo segundo sólo
se prueba si `payment["transaction_hash"]` viene lleno — o sea, si el servidor
alcanzó a contestar con su `X-Payment-Receipt`. Cuando se cae el transporte no
hay forma, desde el cliente, de saber si el facilitator liquidó: haría falta
consultarlo a él o a la cadena, y este SDK es un lector del índice, no del
settlement. `payment_sent=False`, en cambio, **sí** es una afirmación fuerte: no
se firmó nada.

> ✅ **La ambigüedad del contrato quedó resuelta el 2026-08-30.** R5 decía «ante
> fallo devuelve `null`» sin acotar y la tabla de tipos acotaba a `wallet()`;
> este SDK había seguido la tabla y el gemelo TypeScript había seguido la regla,
> terminando con **fail-open en las rutas pagas** — `null` tras un timeout
> posterior al settlement. La regla corregida de arriba es canon y los dos SDK la
> implementan igual. De la versión vieja sobrevivió la observación de que una
> lista vacía se lee como un índice vacío: por eso el contrato dice «nunca `[]`».

---

## Pagar (R6) — una sola caseta de peaje

Este SDK **jamás firma, custodia ni deriva una clave**. El 402 lo resuelve
`uvd-x402-sdk`, y acá sólo se verifica a quién se paga, se elige la red y se
delega.

```python
import os
from uvd_x402_sdk import X402Client
from uvd_describe_sdk import DescribeClient, TREASURY_EVM

payer = X402Client(recipient_address=TREASURY_EVM)
payer.connect_with_private_key(os.environ["MI_CLAVE"], chain="base")  # nunca en un archivo

with DescribeClient(payer=payer, pay_network="base", product="mi-app") as describe:
    br = describe.wallet_breakdown("0x97cd…0996")
    print(br.final_score, br.caveat_codes, br.receipt.transaction_hash)
```

**El chequeo que no se puede desactivar:** si el 402 nombra un `payTo` que no es
la tesorería pinneada, es `DoNotPayError` — **no un retry**. Reintentar ahí
convierte un desvío de fondos en un desvío de fondos con reintentos.

Y se verifica **antes** de firmar. El test que lo ata usa un payer que explota si
lo llaman: se comprobó por mutación que mover la verificación después de la
firma pone rojo ese test.

**`result.receipt`** expone `X-Payment-Receipt` y `X-Payment-Reused` — las
cabeceras que el paywall emite desde siempre y que **ningún cliente leía**. Es la
diferencia entre «pagué» y «puedo probar que pagué».

---

## El riel de partner — entrar a las medidas sin gastar un centavo

Si describe dio de alta tu wallet, las rutas medidas dejan de cobrarte. **No es
un token: es una firma.**

```python
from uvd_x402_sdk.wallet import EnvKeyAdapter    # lee WALLET_PRIVATE_KEY
from uvd_describe_sdk import DescribeClient

with DescribeClient(product="meshrelay", partner=EnvKeyAdapter()) as describe:
    br = describe.wallet_breakdown("0x97cd…0996")   # $0,01 para un tercero, $0 acá
```

### Por qué una firma y no una API key

describe **no custodia ningún secreto tuyo**. Su allowlist son DIRECCIONES
PÚBLICAS —se pueden commitear, loguear y publicar sin filtrar nada— y vos
firmás cada request con una wallet dedicada. **Una brecha de describe no
compromete tu acceso**, porque allá no vive ninguna credencial tuya.

Y el default es cobrar, estructuralmente, del lado del servidor: env ausente,
vacía o con JSON inválido ⇒ allowlist vacía ⇒ 402 para todos. Ninguna
configuración rota significa «pasa todo».

### 🔴 Este SDK sigue sin tocar tu clave

`partner=` recibe un **objeto que firma**, no una clave: dos métodos,
`get_address()` y `sign_message()`. Es el mismo par que el `WalletAdapter` del
`uvd-x402-sdk`, así que entra su `EnvKeyAdapter` (la clave en **tu** entorno),
un KMS, un HSM o una Ledger, sin heredar nada de este paquete.

Usá una wallet **dedicada y sin fondos**: lo único que hace es firmar. Eso es lo
que hace barato el peor caso — una firma filtrada sirve contra el mismo método y
la misma URL, y sólo por 300 segundos. No es una credencial permanente y no
puede mover plata.

> **Nunca** escribas una private key en un archivo, ni «temporalmente». Hay bots
> barriendo GitHub por `0x`+64 hex que drenan en minutos.

### Si el riel se cae, el cliente LEVANTA — no paga

| Qué pasó | Qué sale | ¿Gastaste? |
|---|---|---|
| el firmante rompe (KMS caído, extra sin instalar) | `PartnerSigningError`, **antes de la primera request** | no, y es afirmación fuerte |
| describe contesta 402 pese a la firma | `PartnerRejectedError` (hereda de `PaymentRequiredError`) | no: **el `payer` no se usa aunque esté** |

Es la decisión entera del modo. Un partner con `payer=` y el riel caído tiene un
camino obvio y silencioso —pagar— y ahí el bug no se ve nunca: la respuesta
llega igual, el código funciona, y la factura de USDC aparece semanas después.
Las dos excepciones salen con **`payment_sent is False`**: te enterás de que
perdiste el riel gratis **sin haber gastado** el USDC que el riel te ahorraba.
Si de verdad querés pagar, construí el cliente **sin** `partner=`.

Las cuatro causas de un `PartnerRejectedError`, todas fail-closed del lado del
servicio: la wallet no está en la allowlist · firmaste contra otro host (tu
`base_url` no es `api.describe.net`) · el reloj se corrió más de 300 s · la
firma no cubría la URL que salió. La excepción trae `wallet` — la dirección
pública con la que firmaste, que es lo que hay que citarle a describe.

### Dos detalles que se pagan caro

- **Se firma lo que se manda, byte a byte, incluida la query.** La base cubre
  `@query` sólo cuando la URL tiene una, así que firmar una URL rearmada a mano
  y mandar otra da un 402 que nadie entiende. El SDK firma la URL que `httpx` ya
  construyó: son la misma cadena **por construcción**.
- **Sólo se firman las rutas medidas.** Está medido del otro lado: el paywall
  decide «gratis» *antes* de mirar el partner, así que una firma en `/health` no
  cambia nada — y con un firmante remoto costaría una ida al KMS por cada
  lectura. La atribución de consumo, que es lo otro que un partner debe, sale
  del User-Agent: pasá `product=`.

**El riel no mueve la regla R5.** Que `wallet_breakdown()` te salga gratis no la
convierte en ruta gratis: sigue levantando ante cualquier fallo, con
`fail_open=True` explícito incluido. El criterio nunca fue el precio que pagaste
sino si hubo dinero de por medio.

---

## Mostrar un score (R8)

```python
format_score(86.653045)  # '86.65'
format_score(83.0)       # '83'   ← el caso testigo, no '83.00' ni '83.0'
format_score(None)       # '—'    ← NUNCA '0'
```

Dos decimales, ceros finales recortados. Salió de una medición sobre 47 scores
reales: 0 decimales fusiona 23 pares de agentes *distintos* en el mismo string,
1 decimal fusiona 4, 2 decimales fusiona 1. El gemelo JS es
`String(parseFloat(x.toFixed(2)))` y un test compara los dos.

---

## El badge — el pedacito que se copia y se pega

```python
from uvd_describe_sdk import badge_url, badge_img_tag

badge_url("0x97cd…0996")      # https://api.describe.net/badge/0x97cd….svg
badge_img_tag("0x97cd…0996")  # <img src="…" alt="…" height="20" loading="lazy">
```

**Cero red**: sólo arma el string. El fetch lo hace el navegador de quien mira la
página, y el borde sirve el badge con `stale-if-error=604800` — o sea que sigue
pintando el último valor conocido aunque el origen esté caído, sin una línea de
código de quien lo embebe.

⚠️ **Un badge no reemplaza una lectura.** Es una imagen: no trae `caveats[]`, no
distingue `[]` de `null` y no se puede ramificar sobre él. Para *decidir* se usa
`wallet()`; el badge es para *mostrar*.

---

## Configuración

```python
DescribeClient(
    base_url="https://api.describe.net",
    timeout=30.0,          # R7 — ver abajo
    product="mi-app",      # → User-Agent. Pasalo.
    fail_open=True,
    on_error=None,         # se llama con la excepción tragada
    payer=None,            # sólo para las rutas medidas
    pay_network="base",
    treasury=TREASURY_EVM,
    partner=None,          # el firmante del riel — un OBJETO, nunca una clave
    transport=None,        # httpx.MockTransport, para tests
)
```

**El timeout de 30 s está razonado, no elegido**: el cold start de la Lambda del
proveedor midió 15,2 s, su API Gateway corta a 29 s, y 30 es *deliberadamente
distinto* de los 45 s del facilitator para que los dos relojes nunca expiren el
mismo segundo (INC-2026-08-19).

**Pasá `product`.** El rate limit son **20 rps compartidos** entre todos los
consumidores y no hay bucket por partner: sin atribución en el User-Agent, nadie
puede saber quién se lo gastó. Un request anónimo contra un límite compartido es
free-riding.

---

## Lo que este SDK NO hace

- No escribe en ninguna cadena ni emite calificaciones. Es un **lector**.
- **No custodia claves ni implementa criptografía.** ⚠️ Corregido el
  2026-08-30, y la corrección se deja escrita porque la línea vieja —«no
  firma»— ya no es exacta: con el riel de partner el SDK **sí produce una firma
  ERC-8128**, pero la hace el `uvd-x402-sdk` con un objeto firmante que le
  inyecta el consumidor. Lo que nunca cambió, y es lo que la frase quería
  decir, es que **acá no vive ninguna clave**: ni en un default, ni en una env
  var, ni en un parámetro. Tampoco implementa EIP-3009, RFC 9421 ni EIP-191.
- No cachea. Es una decisión, no un olvido: el TTL correcto depende de para qué
  se lee (mesh usa 12 min para un canal; un perfil quiere el valor caliente) y un
  caché adentro del SDK con un default equivocado es peor que ninguno. La
  frescura viaja en `refreshed_at` para que quien llama decida.
- No tiene API async. Ver riesgos.

---

## Compatibilidad y estado

- Python **3.9+**. El type check corre contra 3.10 porque mypy ≥ 2.0 rehúsa
  analizar 3.9; la compatibilidad con 3.9 la garantiza el CI corriendo la suite
  ahí, que es evidencia de ejecución.
- **Nada de este paquete está publicado todavía.** Ni en PyPI, ni en GitHub.
- Nombre `uvd-describe-sdk`: **hipótesis a ratificar**. Saul nunca lo nombró.

### Lo que le falta al SDK de pagos (upstream, para reportar allá)

- **`uvd-x402-sdk` no publica `py.typed`** (medido en 0.70.0, 2026-08-30): mypy
  lo trata como sin tipos y todo consumidor tipado pierde su firma entera. Acá se
  declara el hueco en un override de mypy, no se parchea — se arregla upstream.
- **Y lo que NO le faltaba**, medido antes de escribir una línea del riel de
  partner: `uvd-x402-sdk` 0.70.0 **ya firma ERC-8128**
  (`uvd_x402_sdk.erc8128.sign_request`, con los vectores dorados de la flota de
  EM adentro del paquete), con `DEFAULT_CHAIN_ID = 8453` y
  `DEFAULT_VALIDITY_SEC = 300` — o sea justo lo que el gate del servicio exige.
  No hubo nada que subir upstream: la regla *upstream-first* se cumplió
  midiendo y encontrando el primitivo ya hecho, que es el mejor de sus
  desenlaces. Se anota igual porque la próxima vez la pregunta se contesta
  leyendo esto en vez de volviendo a medir.

### Riesgos y preguntas abiertas

- **Este SDK es sync y el cliente de Execution Market es `async`.** Para
  adoptarlo, EM tendría que envolverlo en un thread. Es la pregunta de adopción
  más concreta que queda; un `aio.py` de transporte fino (reusando estos parsers,
  sin duplicar política) sería la salida, y no se escribió todavía.
- ~~**El «riel gratis» para los productos propios** (EM / mesh / KK) que Saul
  pidió el 2026-08-14 no está resuelto y no se inventó acá.~~ **RESUELTO el
  2026-08-30** — ver §«El riel de partner» arriba. Se deja tachado y no borrado
  porque la corrección enseña algo: la línea vieja decía que *«el servicio no
  tiene cuentas ni API keys, así que no hay forma obvia de distinguirlos»* y de
  ahí sacaba «no inventes un header de partner». La premisa era correcta y la
  conclusión no: la forma existía y no era un header inventado sino **una firma
  con la wallet**, que es el mismo primitivo de identidad que la cara paga ya
  usaba — divergen sólo en política (allowlist contra pago), no en mecanismo. Lo
  que salvó a este repo de inventar algo peor fue el «no lo decidas solo», y lo
  que lo resolvió fue que el servicio lo construyó primero: este SDK sólo lo
  habla. **La decisión de quién entra en la allowlist sigue siendo de Saul**, y
  eso no cambió: acá no hay ninguna lista.
- ~~El alcance del fail-open~~ **resuelto el 2026-08-30** (arriba): las gratis
  degradan, las pagas levantan. Se deja tachado y no borrado: quien recuerde la
  pregunta merece encontrar la respuesta donde la dejó.
- **No se puede confirmar un settlement desde el cliente.** `payment_sent` dice
  que la credencial salió; sólo un `X-Payment-Receipt` prueba que se liquidó, y
  un transporte caído no trae ninguno. Cerrar ese hueco pide consultarle al
  facilitator o a la cadena, y eso es otra dependencia y otro producto.
- El SDK se probó contra la API viva sólo en sus **rutas gratis**. Las pagas se
  ejercitan con un payer mockeado; nunca se gastó un centavo de USDC. **Por eso
  el fallo posterior a la firma está probado con un transporte de mentira y no
  con un pago real**: se sabe que el SDK marca la excepción, no se midió una
  liquidación de verdad interrumpida.

---

## Desarrollo

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest        # 125 tests, ~0,4 s, SIN RED
.venv/Scripts/python -m ruff check src tests
.venv/Scripts/python -m mypy src/uvd_describe_sdk
.venv/Scripts/python examples/smoke_gratis.py   # esto SÍ toca la API viva
```

La suite entera corre sin red: el seam es `transport=` del constructor
(`httpx.MockTransport`). Los payloads de los fixtures **no son inventados** — son
capturas literales de `api.describe.net` del 2026-08-30. Un fixture inventado
testea contra la idea de quien lo escribió; uno capturado testea contra lo que el
servicio manda, y esa diferencia ya se pagó una vez en este ecosistema.

MIT · [describe.net](https://describe.net) · [docs](https://docs.describe.net)
