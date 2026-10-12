# Cobertura contrato ↔ SDK con `uvd_sdk_map`

El paquete `src/uvd_sdk_map` extrae el motor de `tests/test_sdk_map.py`.
Se distribuye dentro del wheel y sdist de **uvd-describe-sdk**; no tiene una
publicación PyPI independiente. Sólo usa la biblioteca estándar de Python ≥3.9.
Importarlo no carga el cliente describe, pytest, httpx ni fixtures de este repo.

La API recomendada para CI es `verificar_cobertura(spec, grabado, fuera)`.
Devuelve el mapa ordenado `{"mapeadas": {operationId: metodo}, "fuera":
{operationId: motivo}}` o levanta una excepción. Exige que cada operación pública
esté mapeada o tenga un motivo textual no vacío; rechaza llamadas desconocidas,
operaciones ocultas, exclusiones ajenas, solapamientos y contratos sin operaciones
públicas. `medir_mapa` sólo mide: permite cobertura parcial para diagnóstico.

## Instalación reproducible para los otros SDK

Hasta una publicación que incluya este paquete, usar un checkout fijado al commit
revisado de este PR o una referencia Git con **SHA completo**. No asumir que la
versión 0.7.0 actualmente publicada lo contiene. En un venv dedicado de herramientas:

```powershell
python -m pip install --no-deps "uvd-describe-sdk @ git+https://github.com/UltravioletaDAO/uvd-describe-sdk-python.git@<SHA-completo-revisado>"
python -c "from uvd_sdk_map import verificar_cobertura"
```

Reemplazar el marcador por el commit elegido. `--no-deps` es suficiente para
**este paquete de verificación**; para usar el cliente describe instalar también
sus dependencias. La alternativa local es `python -m pip install --no-deps
../uvd-describe-sdk-python`. No copiar el motor ni importar `tests.test_sdk_map`.
Instalar las herramientas antes de cerrar la red del paso de tests.

## Adaptador de cada consumidor

1. Versionar su propio contrato OpenAPI JSON y, si corresponde, el overlay, con
   origen, commit y hashes. No descargar un spec durante los tests.
2. Enumerar la superficie pública desde el SDK y exigir que cada miembro tenga
   un conductor o una declaración explícita de que no hace HTTP. Probar que esos
   miembros locales graban cero llamadas. Este paso depende del lenguaje.
3. Ejecutar cada conductor contra un transporte falso y exigir al menos una
   llamada. Comprobar que llegó al resultado esperado (también errores o `None`
   si ése es el contrato del método). Probar variantes de argumentos que cambien
   ruta, verbo o query. La librería no ejecuta ni descubre métodos por sí sola.
4. Grabar pares `[nombre_del_metodo, [VERBO, host_con_puerto, ruta, claves_query]]`.
   No guardar valores, cuerpos, firmas ni credenciales. Por ejemplo:

   ```json
   [["items.read", ["GET", "catalog.example:8443", "/api/v2/items/42", ["lang"]]]]
   ```

5. Mantener aparte `fuera.json`, un objeto de `operationId` a motivo. Medir el mapa
   desde las llamadas, verificar la cobertura y compararlo con `sdk-map.json`.
   Actualizar el mapa debe ser una decisión explícita fuera del test de CI.

En Python se pueden pasar las tuplas directamente. En TypeScript el transporte
falso puede registrar `request.method`, `new URL(request.url).host`, `.pathname`
y `[...url.searchParams.keys()]`, y exportar el JSON desde los tests. El paso de
Python importa el **mismo paquete** y consume ese archivo; no hay una traducción
del verificador a TypeScript. Fallar también si la generación de la grabación
falla: no reutilizar una grabación vieja como sustituto de conducir el SDK actual.

Ejemplo completo de `scripts/check_sdk_map.py` en el repo consumidor, ejecutado
con `python scripts/check_sdk_map.py` después de sus tests de grabación:

```python
import json
from pathlib import Path

from uvd_sdk_map import schemas_vigilados, verificar_cobertura

root = Path(__file__).resolve().parents[1]

def read(relative):
    return json.loads((root / relative).read_text(encoding="utf-8"))

spec = read("schema/openapi.json")
# Si se usa el overlay restringido:
# from uvd_sdk_map import aplicar_overlay, leer_overlay
# spec = aplicar_overlay(spec, leer_overlay(
#     (root / "schema/sdk.overlay.yaml").read_text(encoding="utf-8")))
mapa = verificar_cobertura(spec, read("build/sdk-calls.json"), read("fuera.json"))
if mapa != read("sdk-map.json"):
    raise AssertionError("sdk-map.json difiere de las llamadas del SDK actual")
print(json.dumps(mapa, ensure_ascii=False, indent=2))
print("Schemas vigilados:", sorted(schemas_vigilados(spec, mapa)))
```

`pathlib` resuelve los archivos respecto del script, no del directorio de trabajo;
funciona en Windows y POSIX, incluidas rutas con espacios y Unicode. Las rutas
HTTP de las grabaciones siempre usan `/`; no son rutas del sistema de archivos.

## Adopción en los otros cuatro repos

Este PR sólo integra describe Python. Los pasos de esta tabla son trabajo de
adopción posterior, no afirmaciones de que sus contratos o adaptadores ya existan.

| Repositorio | Adaptación local |
|---|---|
| `uvd-describe-sdk-typescript` | Fijar el contrato/overlay de describe; conducir sus métodos camelCase con transporte falso; exportar llamadas JSON y ejecutar el verificador Python. Mantener sus propios motivos de exclusión. |
| `uvd-em-sdk-python` | Fijar el contrato REST de EM; conducir los métodos HTTP con fixtures locales e importar `verificar_cobertura` desde sus tests. No consultar servicios vivos para generar el mapa. |
| `uvd-x402-sdk-python` | Fijar los contratos REST de las superficies que exponga; separar firmantes, algoritmos y helpers locales de los métodos HTTP. Verificar cada contrato por separado y con su propio `fuera`. |
| `uvd-x402-sdk-typescript` | La misma partición REST/local que su gemelo, con nombres propios, transporte falso y JSON hacia el paso Python. No ejecutar firmas ni pagos para medir rutas HTTP. |

Si una superficie x402 no tiene OpenAPI, este verificador no inventa uno ni
verifica el protocolo de pagos: primero hace falta un contrato mantenido por su
dueño. El transporte falso debe cortar toda salida no prevista. En este repo
`tests/conftest.py` corta DNS y sockets externos y falla incluso si el SDK atrapa
el error; además corremos con `HTTPS_PROXY`/`HTTP_PROXY=http://127.0.0.1:9` y
`NO_PROXY=127.0.0.1,localhost`. Los proxys solos no sustituyen ese guard.

## API y límites heredados

- `leer_overlay(texto)` entiende exclusivamente la gramática del overlay
  vendoreado: cabecera `overlay: 1.0.0`, `info` con `title`/`version`, lista de
  targets `$.paths['ruta'].verbo` entre comillas dobles, y `update: {clave: valor}`
  en una línea. Valores simples alfanuméricos con `_`, `.` o `-`; `true`/`false`
  son booleanos. Rechaza líneas desconocidas y pares repetidos. **No es YAML ni
  una implementación general de OpenAPI Overlay**. Otros consumidores pueden
  aplicar su propio overlay previamente y pasar el diccionario resultante.
- `aplicar_overlay` copia el spec, exige versión 1.0.0, targets de operaciones
  existentes y sólo updates. `x-fern-ignore is True` oculta una operación.
  El gate compartido no exige nombres de grupo Fern; describe los exige en su test.
- `operaciones` exige IDs únicos; `publicas` devuelve las no ocultas.
- `resolver` compara verbo, host (incluido puerto), prefijos de `servers` raíz,
  plantilla de ruta y nombres de query declarados en la operación. El verbo debe
  llegar en mayúsculas; host debe coincidir con la autoridad del spec; ruta y
  claves de query son sensibles a mayúsculas. No elimina espacios ni normaliza
  nombres. Los segmentos de plantilla son opacos (incluidos Unicode, `0x`/`0X`).
  Para cada servidor se elige la ruta con más segmentos enteramente literales:
  una ruta literal precede a una plantilla, independientemente del orden del
  contrato, como exige [OpenAPI 3.1, Paths Object](https://spec.openapis.org/oas/v3.1.0.html#paths-object).
  Entre plantillas, ese conteo es la política de desempate de esta herramienta.
  Si las mejores candidatas empatan, levanta `ValueError` con las rutas ambiguas.
  Sólo después valida verbo y query: si no corresponden a la ruta elegida,
  devuelve `None`, sin caer en una plantilla menos específica.
- El subconjunto heredado requiere servidores raíz absolutos sin variables,
  rutas con una ganadora según la precedencia anterior y parámetros query inline
  en cada operación. No resuelve overrides de servidores, parámetros heredados/por
  `$ref`, ni contratos con rutas ambiguas. Preparar esos contratos antes de usarlos aquí. La tupla no
  distingue esquemas HTTP/HTTPS, valores ni parámetros obligatorios; tampoco
  valida cuerpos, tipos de respuesta o semántica del protocolo.
- Un `operationId` puede grabarse varias veces por el mismo método. Dos métodos
  distintos que lo llamen se rechazan: la forma actual del mapa es uno a uno.
- `sin_clasificar` sirve para diagnóstico; un motivo vacío, Unicode en blanco o
  no textual no clasifica. Usar `verificar_cobertura` para exigir también que
  `mapeadas`/`fuera` sean públicas y disjuntas.
- `schemas_vigilados` sigue los `$ref` de respuestas 2xx transitivamente, con
  detección de ciclos. Sólo admite referencias a `#/components/schemas/Nombre`;
  una referencia externa o inexistente falla. No necesita `components` cuando
  todo está inline. No equivale a validar las respuestas del SDK.

## Verificación aquí

`python -m pytest tests/test_sdk_map.py tests/test_sdk_map_library.py` comprueba
el adaptador real de describe y un contrato sintético con otro host, prefijo,
puerto, nombres, exclusiones y referencias cíclicas. El import aislado corre
sin site-packages desde un directorio con espacios y Unicode. `sdk-map.json` y
los archivos vendoreados conservan sus bytes.
