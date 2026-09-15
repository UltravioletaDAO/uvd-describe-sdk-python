# Handoff — `caveats_not_computed`, `author_class` y `require_full_caveats()` (2026-09-15)

> Worker `dn-sdk-caveats-py` (task `task_f0061a369ec8`, dispatch `ctx_9302e2d94108`),
> despachado por c0der master-4. Es la mitad Python de la fila upstream-first
> `describe-net/docs/BACKLOG.md:19`. Rama `0xultravioleta/dn-sdk-caveats-py`, que
> sale de `origin/main` `d085bb6`. **Sin tag y sin merge**: los dos los hace
> c0der.

## Qué quedó

| Pedido del encargo | Qué se hizo | Dónde |
|---|---|---|
| (1) `caveats_not_computed` como `list[str] \| None`, sin confundir `None` con `[]` | `WalletReputation.caveats_not_computed: Optional[List[str]]`. Una clave ausente o `null` da `None`, y cualquier forma ilegible también (nunca una lista filtrada). Va como último campo, después de `raw` | `src/uvd_describe_sdk/models.py` (`_parse_not_computed`) |
| (2) `author_class` literal en cada `Rating`, tolerando lo desconocido | `Rating.author_class: Optional[str]`. Una clase desconocida llega entera; si falta, es `None` y nunca `rater-authored`. Además `AuthorClass`, `KnownAuthorClass` (`Literal`), `KNOWN_AUTHOR_CLASSES` e `is_known_author_class()` | `models.py` (`_author_class`), `caveats.py` |
| (3) code `facilitator-authored` | `CaveatCode.FACILITATOR_AUTHORED`: `KNOWN_CAVEAT_CODES` pasa de 8 a 9 | `caveats.py` |
| (4) `require_full_caveats(resp)` + error tipado | `require_full_caveats(result: WalletReputation) -> WalletReputation` y `CaveatsNotComputedError` (`.wallet`, `.not_computed`, `recovery` como constante de clase) | `caveats.py`, exportado en `__init__.py` |
| (5) fixtures copiados de la API viva y del `/openapi.json` | Dos capturas `curl -o` de `/wallets/{w}/chains` y los esquemas `WalletChains` y `Rating` copiados enteros | `tests/fixtures/` |
| (6) tests con `None` contra `[]` y el helper en todos sus estados | 48 tests nuevos | `tests/test_caveats_no_calculados.py`, `tests/test_author_class.py`, `tests/test_r2_r3_contrato.py` |
| (7) bump minor + CHANGELOG | `0.5.0` → `0.6.0`. `CHANGELOG.md` nace con esta versión: hasta la 0.5.0 el registro vive en los mensajes de commit | `version.py`, `CHANGELOG.md` |

También se tocaron `README.md` (§3: el gate y `author_class`), `CLAUDE.md` (la
sección de la superficie nueva, las mutaciones O–V y la cifra de tests) y
`examples/smoke_gratis.py` (imprime el campo y falla si la puerta gratis deja de
declararlo).

## Nombres públicos — paridad con TypeScript

| Python (este PR) | TypeScript (worker `dn-sdk-caveats-ts`, su árbol a las 04:16Z) |
|---|---|
| `WalletReputation.caveats_not_computed: Optional[List[str]]` | `WalletReputation.caveatsNotComputed: CaveatCode[] \| null` |
| `Rating.author_class: Optional[str]` | `Rating.authorClass: AuthorClass \| null` |
| `CaveatCode.FACILITATOR_AUTHORED` en `KNOWN_CAVEAT_CODES` (9 codes) | `'facilitator-authored'` en `CAVEAT_CODES` (9 codes) |
| `AuthorClass`, `KnownAuthorClass`, `KNOWN_AUTHOR_CLASSES`, `is_known_author_class()` | `AuthorClass`, `KnownAuthorClass`, `AUTHOR_CLASSES`, `isKnownAuthorClass()` |
| `require_full_caveats(result: WalletReputation)` | `requireFullCaveats(result: WalletReputation)` |
| `CaveatsNotComputedError(Exception)` con `.wallet` y `.not_computed` | `CaveatsNotComputedError extends Error` con `.wallet` y `.notComputed` |

Cada lado sigue su convención: snake_case en Python, camelCase en TS (la misma
que ya separaba `global_score` de `globalScore`).

⚠️ **Hubo un cruce, y c0der tiene que verificar su resultado.** El worker TS
commiteó primero otra tabla (`6242b75`: diez codes y
`DescribeCaveatsNotComputed extends DescribeError` con `kind`), y c0der la copió
a este worktree como `PARIDAD-TS-c0der.md`. Para esa hora Python ya estaba en
`edca736`. A las ~04:15Z el worker TS adoptó `edca736` en su árbol de trabajo
mientras yo empezaba a ir hacia su tabla vieja. Lo detecté, revertí lo mío sin
commitearlo y le dejé `PARIDAD-PY-c0der.md`, sin trackear, en la raíz de su
worktree. **Python no cambió desde `edca736`.**

Dos diferencias de borde quedan declaradas, y las dos fallan cerradas:
1. Una entrada ilegible dentro de la lista (`[null]`): en Python todo el campo es
   `None`; en TS sale `String()` por entrada. En los dos el gate levanta.
2. El texto de `recovery` no es byte a byte igual: Python nombra
   `wallet_breakdown()`, `payer=`, `partner=` y `fallback_reader` (los anclajes
   de `test_recovery.py` son spellings Python); TS nombra la ruta HTTP.

## Las decisiones, con su fuente

- 🔴 **`require_full_caveats()` con `caveats_not_computed is None` LEVANTA**, y
  sólo `[]` pasa. La fila del 2026-08-31 (`describe-net/docs/BACKLOG.md:221`,
  `origin/main` `01f6c4a`) es la del gate de calidad que pasa en verde sin saber
  qué no se calculó, y su posición vigente fue *«la línea gratis/pago es regla de
  costo y el scope es la señal; el SDK puede ganar `require_full_caveats()`»*.
  Un `None` que pasa es ese gate de vuelta, en silencio, con cada API vieja y cada
  respuesta de un `fallback_reader`. El servicio aplica la misma regla en su tool
  MCP (`describenet/mcp_server.py:896-901`: *«`[]` afirmaría que lo calculó
  todo»*), y su comentario nombra este helper como la razón de que la
  declaración sea una lista (`describenet/caveats.py:491-497`).
- **`CaveatsNotComputedError` no es un `DescribeError`.** El `except
  DescribeError` que un consumidor escribió para tolerar caídas convertiría un
  rechazo en «describe no contestó», y un gate tolerante deja pasar. Tampoco
  entra a la taxonomía R4: el cliente no lo levanta nunca.
- **El gate sólo acepta `WalletReputation`.** `None` (R5: no hubo respuesta),
  `Breakdown`, `AgentReputation` o un dict crudo dan `TypeError`, nunca un pase.
- **`author_class` tolerante**: un `str` abierto, igual que `Caveat.code`. El
  esquema vivo lo declara `enum` de dos, pero eso es la promesa del servidor para
  hoy, no permiso para romper la lectura de un pago el día que aparezca una
  tercera clase.
- **`thin-chain` NO entra**, ni acá ni en TS. El servicio sirve diez codes
  (`describenet/caveats.py:177-192`); el encargo pedía sólo
  `facilitator-authored`, y agregarlo de un lado rompe la paridad. Por eso
  `CAVEAT_CODES_MEASURED_AT` sigue en `2026-08-30`, con la corrección escrita al
  lado.

## Evidencia (Mac mini, sin credenciales)

- **Capturas vivas** con `curl -o` y `/health.build_sha` `a29d5ef`:
  `/wallets/0x715dc035…4e6d/chains` a las 04:01:35Z (648 reviews, 471 raters) y
  `/wallets/0x…beef/chains` a las 04:01:36Z (sin identidad). Las dos declaran los
  mismos siete codes y `caveats: []`. `/openapi.json` 2.0.0 a las 04:03:30Z:
  `caveats_not_computed` está en `WalletChains.required`, y
  `Rating.author_class.enum` es `["facilitator-authored", "rater-authored"]`.
- **Suite:** `origin/main` `d085bb6` da 258 passed; con el cambio, 306 passed.
- **Los tests nuevos contra `origin/main`** dan `ImportError` en la colección
  (`CaveatsNotComputedError`, `KNOWN_AUTHOR_CLASSES`).
- **Mutaciones, las 8 en rojo** (script `mutar.py` en el scratchpad de la sesión:
  muta en sitio, corre la suite y restaura verificando el sha256):

| Mutación | Rojos |
|---|---|
| O. `None` colapsado en `[]` al parsear | 6 (y los tests de la captura viva quedaron verdes) |
| P. el gate deja pasar `None` | 5 |
| Q. el error hereda de `DescribeError` | 4 (1 propio + 3 de `test_recovery.py`) |
| R. `author_class` ausente → `rater-authored` | 5 |
| S. `author_class` con set cerrado | 1 |
| T. declaración ilegible filtrada en vez de `None` | 5 |
| U. sin el guard de tipo del gate | 3 |
| V. `facilitator-authored` fuera de `KNOWN_CAVEAT_CODES` | 2 |

- **Pre-CI** con los comandos literales de `.github/workflows/ci.yml` (el CI está
  apagado), en venvs nuevos y sobre un `git archive` del commit de código
  `edca736`:

| Job (workflow) | Python | Comando literal | Resultado |
|---|---|---|---|
| `test` py3.9 (`ci.yml`) | 3.9.25 | `pip install -e ".[dev]"` | exit 0 (resuelve `uvd-x402-sdk` 0.83.1) |
| | | `python -c "import uvd_x402_sdk, uvd_x402_sdk.networks.base; print('uvd-x402-sdk OK')"` | `uvd-x402-sdk OK` |
| | | `python -m pytest` | **306 passed**, 16,6 s |
| `test` py3.12 (`ci.yml`) | 3.12.12 | los mismos tres | exit 0 · `uvd-x402-sdk OK` · **306 passed**, 16,2 s |
| `lint` (`ci.yml`) | 3.12.12 | `pip install -e ".[dev]"` · `python -m ruff check src tests` · `python -m mypy src/uvd_describe_sdk` | exit 0 · `All checks passed!` · `Success: no issues found in 11 source files` |
| `contra-la-api-viva` (`ci.yml`, `continue-on-error`) | 3.12.12 | `python examples/smoke_gratis.py` | `✅ smoke OK` (corrido con el venv de desarrollo, no con `pip install -e "."` a secas) |
| `publish` hasta antes de subir (`publish.yml`) | 3.11.14 | `pip install build twine` · `python -m build` · `python -m twine check dist/*` · tag contra `version.py` | exit 0 · se construyen `uvd_describe_sdk-0.6.0.tar.gz` y `-py3-none-any.whl` · `PASSED` ×2 · `0.6.0` (el `grep -oP` del workflow se emuló con Python: el grep de macOS no tiene `-P`) |

- **Smoke vivo** (`python examples/smoke_gratis.py`, sólo rutas gratis): `✅ smoke
  OK`, con `caveats_not_computed=['campaign-per-rater', 'concentration-degraded',
  'few-raters', 'no-score', 'self-rated', 'single-rater', 'top-client-share']`
  para el #1 del leaderboard.
- **Cierre por `git grep`** en `edca736 -- src`: los cuatro símbolos aparecen, en
  52 líneas (`__init__.py` 8, `caveats.py` 29, `models.py` 15).

## Lo que NO se tocó

- **Ningún consumidor** (EM, KK, MeshRelay, karma-hello): upstream-first.
- **describe-net**: sólo lectura.
- **El repo TS**: sólo el archivo sin trackear `PARIDAD-PY-c0der.md` en la raíz
  de su worktree, que se puede borrar.
- **Ningún tag.** `SPEC-c0der.txt`, `PREGUNTA-c0der.md` y `PARIDAD-TS-c0der.md`
  quedan sin commitear en la raíz de este worktree.

## Para c0der

1. **Revisar y mergear el PR de esta rama.** Después, taggear `v0.6.0`:
   `publish.yml` rechaza un tag que no coincida con `version.py`, y la versión ya
   es `0.6.0`.
2. **Antes de mergear cualquiera de los dos**, confirmar que el PR de TS lleva lo
   que su árbol tenía a las 04:16Z y no su commit `6242b75` (ver «Hubo un cruce»).
3. **Verificar en vivo después de publicar**, en un venv limpio:

   ```bash
   python -m venv /tmp/v060 && /tmp/v060/bin/pip install "uvd-describe-sdk==0.6.0"
   /tmp/v060/bin/python - <<'EOF'
   from uvd_describe_sdk import DescribeClient, CaveatsNotComputedError, require_full_caveats, __version__
   with DescribeClient(product="c0der-verify") as d:
       rep = d.wallet("0x715dc035ffb97dd7bb4095c6670138ba05bb4e6d")
       print(__version__, rep.caveats_not_computed)   # 0.6.0 y siete codes
       try:
           require_full_caveats(rep)
           print("PASÓ: inesperado mientras la puerta gratis declare codes")
       except CaveatsNotComputedError as exc:
           print("levanta, como debe:", exc.not_computed)
   EOF
   ```

   `author_class` sólo viaja por la ruta paga `agent()`: con el riel de partner o
   pagando, `[r.author_class for r in d.agent("base", "1197").ratings]` tiene que
   dar valores del set o `None`, nunca otra cosa rota.
4. **Fila de seguimiento para los dos SDK**: `thin-chain` en `KNOWN_CAVEAT_CODES`
   y en `FREE_GATE_CAVEAT_CODES` (Python), en `CAVEAT_CODES` (TS), y la fecha del
   espejo movida. Es chica y tiene que ir junta.
5. **Recién con 0.6.0 publicado**: despachar la adopción en EM (que hoy lee
   `author_class` a mano en el dashboard), KK, MeshRelay y karma-hello.
6. **El CLI de Orca no corre en esta Mac** (`/usr/local/bin/orca` es un symlink
   `root:wheel` con modo `0700`). No salió ni un heartbeat ni el `worker_done`;
   este handoff y el PR son el cierre.
