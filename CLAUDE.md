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

.venv/Scripts/python -m pytest                     # 125 tests, ~0,4 s, SIN RED
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

⚠️ **B no pone rojo el test de firmas** (`test_la_tabla_de_nullabilidad_...`),
porque quitar el `except` no toca la anotación. Está anotado porque es la clase
de hueco que hace creer que un test cubre más de lo que cubre: el que ata el
comportamiento es el parametrizado, el de firmas sólo evita que la anotación
publicada mienta.

---

## Lo que este SDK NO hace, y no es un olvido

- **No cachea.** El TTL correcto depende de para qué se lee (mesh usa 12 min
  para un canal; un perfil quiere el valor caliente). Un caché adentro del SDK
  con un default equivocado es peor que ninguno. `refreshed_at` viaja para que
  quien llama decida.
- **No tiene API async.** Ver riesgos abajo — es la deuda más concreta.
- **No reintenta un pago.** El nonce se consume en el settlement: reenviar la
  misma credencial no vuelve a pagar. Un `retries=` quemaría credenciales.
- **No lee env vars.** Todo entra por constructor. Es lo que lo hace testeable
  sin entorno y embebible en cualquier proceso. **El riel de partner no es una
  excepción**: recibe un objeto que firma, no una clave ni el nombre de la
  variable donde vive.
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
   de política**. No está escrito.

---

## Git

- **Commits en español**, conventional (`feat(scope):`), con la evidencia medida
  en el cuerpo y el trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.
- **Nunca `git add -A`** — staging por archivo.
- **Push sólo con OK explícito de Saul, por push.**
