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

.venv/Scripts/python -m pytest                     # 105 tests, ~0,3 s, SIN RED
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

**Huecos abiertos upstream (reportar, no parchear):**
- `uvd-x402-sdk` 0.70.0 **no publica `py.typed`** (medido 2026-08-30). Todo
  consumidor tipado pierde su firma. Declarado en un override de mypy.

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
rojo. Los cuatro que ya se verificaron así (2026-08-30) están anotados en sus
docstrings:

| Mutación inyectada | Qué se puso rojo |
|---|---|
| `get_score(x: float) -> float` exportado | `test_ningun_publico_devuelve_un_numero` → `assert not ['get_score() -> float']` |
| `_opt_float` → `float(value or 0)` | 7 de 10 tests de R1 |
| `assert_recipient` movido DESPUÉS de firmar | los 2 tests de `DO_NOT_PAY` |
| `chain_name_for` → `None` (SDK ausente) | el mensaje de error nombraba la causa equivocada — **bug real, arreglado** |

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
  sin entorno y embebible en cualquier proceso.

---

## Estado, y lo que NO está decidido

- 🔴 **Nada está publicado.** Ni PyPI, ni GitHub. Sólo commits locales.
- **El nombre `uvd-describe-sdk` es hipótesis a ratificar.** Saul nunca lo dijo.
- **Saul dijo «UN repositorio», singular.** Esto son dos (uno por lenguaje,
  siguiendo el precedente de la casa). Le toca a él ratificarlo.

### Preguntas abiertas — NO las resuelvas por tu cuenta

1. **El «riel gratis» para los productos propios** (EM / mesh / KK) que Saul
   pidió el 2026-08-14. El servicio no tiene cuentas ni API keys —«el pago es la
   autenticación»— así que no hay forma obvia de distinguirlos. **No inventes un
   header de partner.**
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
