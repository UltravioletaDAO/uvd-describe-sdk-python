# uvd-describe-sdk (Python)

Cliente Python del índice de reputación ERC-8004 de **describe** — `api.describe.net`.

```bash
pip install uvd-describe-sdk            # el camino gratis: una sola dependencia (httpx)
pip install "uvd-describe-sdk[x402]"    # + pagar las rutas medidas
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
| `leaderboard()` → `list[LeaderboardRow]` | **gratis** | `GET /leaderboard` |
| `health()` → `IndexHealth` | **gratis** | `GET /health` |
| `badge_url(address)` → `str` | **sin red** | construye la URL, no la pide |
| `wallet_breakdown(address)` → `Breakdown` | $0,01 ($0,05 con `snapshot=True`) | `GET /reputation/wallet/{w}` |
| `agent(network, agent_id)` → `AgentReputation` | $0,02 | `GET /reputation/agent/{n}/{id}` |

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

> ⚠️ **Pregunta abierta del contrato, no resuelta acá.** La regla dice «ante
> fallo devuelve `null`» sin acotar; la tabla de tipos marca `| null` **sólo** en
> `wallet()`. Se implementó la tabla —es la afirmación más específica— y
> `leaderboard()` / `health()` levantan. Si el veredicto cambia, cambia en los
> tres frentes a la vez.

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
- No firma, no custodia claves, no implementa EIP-3009.
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

### Riesgos y preguntas abiertas

- **Este SDK es sync y el cliente de Execution Market es `async`.** Para
  adoptarlo, EM tendría que envolverlo en un thread. Es la pregunta de adopción
  más concreta que queda; un `aio.py` de transporte fino (reusando estos parsers,
  sin duplicar política) sería la salida, y no se escribió todavía.
- **El «riel gratis» para los productos propios** (EM / mesh / KK) que Saul pidió
  el 2026-08-14 **no está resuelto y no se inventó acá**. El servicio no tiene
  cuentas ni API keys —*«el pago es la autenticación»*— así que no hay forma
  obvia de distinguirlos. Es pregunta para Saul, no una decisión de este repo.
- **El alcance del fail-open** (arriba): ambigüedad del contrato, reportada.
- El SDK se probó contra la API viva sólo en sus **rutas gratis**. Las pagas se
  ejercitan con un payer mockeado; nunca se gastó un centavo de USDC.

---

## Desarrollo

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest        # 88 tests, ~0,3 s, SIN RED
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
