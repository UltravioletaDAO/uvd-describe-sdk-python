"""La cadena de publicación a PyPI: sólo a mano, sólo desde main, con revisor.

De dónde sale: DN-PUB-02 de c0der, 2026-09-30. Hasta 0.7.0,
`.github/workflows/publish.yml` se disparaba también por tag `v*` y subía a PyPI
sin que nadie lo aprobara: los runs de v0.1.0, v0.2.0, v0.4.0, v0.5.0, v0.6.1 y
v0.7.0 publicaron solos (`gh api .../actions/workflows/publish.yml/runs`). La
regla del stack es que ningún evento de git despliega ni publica solo. La forma
nueva es la del molde, `uvd-x402-sdk-python/.github/workflows/publish.yml`.

Lo que se ata, cada cosa en su test:

* **Ningún disparador salvo el manual.** No sólo el tag: `release`, `schedule` o
  `workflow_run` también publicarían sin que nadie lo pida, y un `grep push:` no
  los ve. Por eso se leen las CLAVES de `on:`, además del texto.
* **Ningún `push:`, `tags:` ni `secrets` en el archivo**, comentarios incluidos:
  es el `grep` de la verificación del encargo, y `secrets` va como palabra
  suelta porque `secrets['X']` y `toJSON(secrets)` no tienen punto (mutación
  EM). Se publica por OIDC, y un token de PyPI guardado en el repo es una
  credencial de larga vida que publica desde cualquier lado.
* **Un job espera en el environment `pypi`** (el que tiene revisor), **y el
  id-token vive en ESE job y en ningún otro**, con o sin comillas en la clave o
  el valor (mutaciones EN y EO). Un id-token a nivel de workflow, o en el job que
  corre los tests con deps sin pinnear, es una credencial de publicar al alcance
  de código de terceros. `write-all` también lo da.
* **Ese job sólo baja el artefacto y lo sube con LA action de pypa**, por SHA y
  sin ningún `run:`: cualquier paso de ese job puede pedir el token (EU), y un
  «algún SHA de 40 hex» deja pasar otro repo con el mismo formato (ES).
* **Publicar exige pasar por el job que sólo corre en main**, sin una función de
  estado en el `if:` que salte el gate. Cualquiera de las cuatro (`success()`
  incluida, mutación EP) le quita al `if:` el `success()` implícito: GitHub,
  `expressions.md:322`, "A default status check of `success()` is applied unless
  you include one of these functions".
* **La versión pedida se compara con `version.py`, y el que no coincide sale con
  `exit 1`** (EQ): es lo que reemplaza al chequeo «tag == `__version__`» del
  workflow viejo.
* **`build` exige que dist/ tenga exactamente esa versión** (ER), **y `twine`
  corre después de subir el artefacto** (ET): sus dependencias no están
  pinneadas y no pueden correr con el wheel todavía sin subir.
* **Toda action va pinneada por SHA**: un tag de action es mutable, y este
  workflow tiene permiso de publicar.

Se lee como TEXTO porque PyYAML no es dependencia del SDK ni del extra `dev`.
Las prohibiciones miran el archivo CRUDO, que es más estricto; las presencias
miran sólo las líneas de código, porque un comentario que dice
`environment: pypi` no protege nada (mutación EK).

Las mutaciones que se vieron rojas están en la tabla de `CLAUDE.md` (EA–EU; de
EM a ES son las R1–R7 del refutador de la ronda 1 del PR 9).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

import pytest

PUBLISH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"

MAIN = "github.ref == 'refs/heads/main'"
ID_TOKEN = re.compile(r"""["']?id-token["']?\s*:\s*["']?write\b""")
SALTA_EL_GATE = re.compile(r"always\(\)|cancelled\(\)|failure\(\)|success\(\)")
PINNEADA = re.compile(r"@[0-9a-f]{40}$")
DESCARGA = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
SUBIDA = "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
VERSION_DEL_INPUT = "INPUT_VERSION: ${{ inputs.version }}"


def _crudo() -> str:
    return PUBLISH.read_text(encoding="utf-8")


def _codigo() -> List[str]:
    """Las líneas de código: sin comentarios (enteros ni al final) y sin vacías."""
    lineas: List[str] = []
    for linea in _crudo().splitlines():
        if linea.lstrip().startswith("#"):
            continue
        sin_comentario = re.sub(r"\s+#.*$", "", linea).rstrip()
        if sin_comentario.strip():
            lineas.append(sin_comentario)
    return lineas


def _sangria(linea: str) -> int:
    return len(linea) - len(linea.lstrip(" "))


def _lista(valor: str) -> List[str]:
    """`a`, `[a, b]` o `"a"` → `["a", "b"]`."""
    return [p.strip().strip("\"'") for p in valor.strip("[]").split(",") if p.strip()]


def _disparadores() -> Set[str]:
    """Las claves de `on:`, en cualquiera de sus tres formas (escalar, lista, mapa)."""
    lineas = _codigo()
    for i, linea in enumerate(lineas):
        encabezado = re.match(r"""^["']?on["']?:\s*(.*)$""", linea)
        if not encabezado:
            continue
        if encabezado.group(1).strip():
            return set(_lista(encabezado.group(1).strip()))
        claves: Set[str] = set()
        for siguiente in lineas[i + 1:]:
            if _sangria(siguiente) == 0:
                break
            clave = re.match(r"^  (?:- )?([A-Za-z_]+)", siguiente)
            if clave:
                claves.add(clave.group(1))
        return claves
    return set()


def _jobs() -> Dict[str, List[str]]:
    """`{nombre del job: sus líneas de código}`."""
    lineas = _codigo()
    if "jobs:" not in lineas:
        return {}
    jobs: Dict[str, List[str]] = {}
    actual = None
    for linea in lineas[lineas.index("jobs:") + 1:]:
        if _sangria(linea) == 0:
            break
        nombre = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", linea)
        if nombre:
            actual = nombre.group(1)
            jobs[actual] = []
        elif actual is not None:
            jobs[actual].append(linea)
    return jobs


def _clave(cuerpo: List[str], clave: str) -> str:
    """El valor de una clave de primer nivel del job (`    clave: valor`)."""
    for linea in cuerpo:
        encontrada = re.match(rf"^    {re.escape(clave)}:\s*(.*)$", linea)
        if encontrada:
            valor = encontrada.group(1).strip()
            return re.sub(r"^\$\{\{\s*(.*?)\s*\}\}$", r"\1", valor)
    return ""


def _needs(cuerpo: List[str]) -> List[str]:
    """`needs: a`, `needs: [a, b]` o la lista en bloque debajo de `needs:`."""
    for i, linea in enumerate(cuerpo):
        if re.match(r"^    needs:", linea):
            valor = linea.split(":", 1)[1].strip()
            if valor:
                return _lista(valor)
            bloque = []
            for siguiente in cuerpo[i + 1:]:
                item = re.match(r"^      - (\S+)$", siguiente)
                if not item:
                    break
                bloque.append(item.group(1).strip("\"'"))
            return bloque
    return []


def _paso(cuerpo: List[str], contiene: str) -> List[str]:
    """Las líneas (sin sangría) del primer paso del job que contiene `contiene`."""
    pasos: List[List[str]] = []
    for linea in cuerpo:
        if re.match(r"^      - ", linea):
            pasos.append([])
        if pasos:
            pasos[-1].append(linea.strip())
    for paso in pasos:
        if any(contiene in linea for linea in paso):
            return paso
    return []


def _usos(cuerpo: List[str]) -> List[str]:
    """Lo que dice cada `uses:` del job, en orden."""
    return [
        linea.split("uses:", 1)[1].strip()
        for linea in cuerpo
        if re.match(r"^\s*(?:- )?uses:", linea)
    ]


def _jobs_en_pypi() -> List[str]:
    return [nombre for nombre, cuerpo in _jobs().items() if _clave(cuerpo, "environment") == "pypi"]


def _job_que_contiene(texto: str) -> List[str]:
    for cuerpo in _jobs().values():
        if any(texto in linea for linea in cuerpo):
            return cuerpo
    return []


def test_solo_se_dispara_a_mano() -> None:
    disparadores = _disparadores()
    assert disparadores == {"workflow_dispatch"}, (
        f"publish.yml se dispara con {sorted(disparadores)}: cualquier evento que no sea el "
        "manual publica a PyPI sin que nadie lo pida (hasta 0.7.0, un tag `v*` lo hacía)"
    )


@pytest.mark.parametrize(
    "prohibido",
    [
        pytest.param(r"push:", id="push:"),
        pytest.param(r"tags:", id="tags:"),
        pytest.param(r"\bsecrets\b", id="secrets"),
    ],
)
def test_el_archivo_no_nombra_push_tags_ni_secretos(prohibido: str) -> None:
    lineas = [
        f"{numero}: {linea.strip()}"
        for numero, linea in enumerate(_crudo().splitlines(), 1)
        if re.search(prohibido, linea)
    ]
    assert not lineas, (
        f"`{prohibido}` volvió a publish.yml (se publica sólo a mano y por OIDC, sin "
        f"credenciales guardadas en el repo): {lineas}"
    )


def test_un_job_espera_en_el_environment_pypi() -> None:
    assert _jobs_en_pypi(), (
        "ningún job declara `environment: pypi`: la subida no espera la aprobación del "
        "revisor, y el trusted publisher de PyPI con Environment = pypi la rechazaría"
    )


def test_el_id_token_vive_en_un_solo_job_y_es_el_de_pypi() -> None:
    codigo = _codigo()
    assert not [linea for linea in codigo if "write-all" in linea], (
        "`write-all` da id-token de escritura a quien lo tenga"
    )
    total = sum(1 for linea in codigo if ID_TOKEN.search(linea))
    con_token = [
        nombre
        for nombre, cuerpo in _jobs().items()
        if any(ID_TOKEN.search(linea) for linea in cuerpo)
    ]
    assert total == 1 and len(con_token) == 1, (
        f"el id-token de escritura aparece {total} vez/veces, en los jobs {con_token}: "
        "tiene que estar UNA vez, dentro del job que publica"
    )
    assert con_token == _jobs_en_pypi(), (
        f"el id-token está en {con_token} y el environment `pypi` en {_jobs_en_pypi()}: "
        "un id-token fuera del job que espera al revisor publica sin revisor"
    )


def test_el_job_con_id_token_solo_baja_el_artefacto_y_lo_sube_con_pypa() -> None:
    en_pypi = _jobs_en_pypi()
    assert en_pypi, "no hay job de publicación en `pypi` (ver el test del environment)"
    cuerpo = _jobs()[en_pypi[0]]
    assert _usos(cuerpo) == [DESCARGA, SUBIDA], (
        f"el job que tiene el id-token corre {_usos(cuerpo)}: tiene que ser exactamente "
        f"{[DESCARGA, SUBIDA]}. Cualquier otra action ahí puede pedir el token"
    )
    corridas = [linea.strip() for linea in cuerpo if re.match(r"^\s*(?:- )?run:", linea)]
    assert not corridas, (
        f"el job que tiene el id-token corre comandos propios {corridas}: cualquier paso "
        "de ese job puede pedir el token OIDC"
    )


def test_publicar_exige_pasar_por_el_job_que_solo_corre_en_main() -> None:
    jobs = _jobs()
    en_pypi = _jobs_en_pypi()
    assert en_pypi, "no hay job de publicación en `pypi` (ver el test del environment)"
    vistos: Set[str] = set()
    pendientes = list(en_pypi)
    gate: List[str] = []
    while pendientes:
        nombre = pendientes.pop()
        if nombre in vistos:
            continue
        vistos.add(nombre)
        cuerpo = jobs.get(nombre, [])
        condicion = _clave(cuerpo, "if")
        assert not SALTA_EL_GATE.search(condicion), (
            f"el job `{nombre}` corre con `if: {condicion}`: una función de estado le quita "
            "el success() implícito, y corre aunque el gate de main se haya salteado"
        )
        if condicion == MAIN:
            gate.append(nombre)
        pendientes.extend(_needs(cuerpo))
    assert gate, (
        f"ningún job de la cadena {sorted(vistos)} tiene `if: {MAIN}`: se podría publicar "
        "despachando desde otra rama"
    )


def test_la_version_pedida_se_compara_con_version_py_y_si_no_coincide_falla() -> None:
    en_main = [
        linea for cuerpo in _jobs().values() if _clave(cuerpo, "if") == MAIN for linea in cuerpo
    ]
    paso = _paso(en_main, "src/uvd_describe_sdk/version.py")
    assert paso, (
        "el job de main no lee `src/uvd_describe_sdk/version.py`: pedir una versión sobre "
        "un árbol que dice otra subiría la que no se pidió, y un número subido a PyPI queda "
        "quemado"
    )
    assert VERSION_DEL_INPUT in paso, f"el paso de la versión no recibe `{VERSION_DEL_INPUT}`"
    assert 'if [ -z "$VER" ] || [ "$INPUT_VERSION" != "$VER" ]; then' in paso, (
        f"el paso de la versión no compara el input con `version.py`: {paso}"
    )
    assert "exit 1" in paso, (
        f"el paso de la versión no falla cuando no coinciden (sin `exit 1`): {paso}"
    )


def test_build_exige_que_dist_tenga_exactamente_esa_version() -> None:
    paso = _paso(_job_que_contiene("python -m build"), "python -m build")
    esperadas = [
        VERSION_DEL_INPUT,
        'test -f "dist/uvd_describe_sdk-$INPUT_VERSION.tar.gz"',
        'test -f "dist/uvd_describe_sdk-$INPUT_VERSION-py3-none-any.whl"',
        'test "$(ls dist/ | wc -l)" -eq 2',
    ]
    faltan = [linea for linea in esperadas if linea not in paso]
    assert not faltan, (
        f"el paso que arma dist/ no exige exactamente la versión pedida; faltan {faltan}. "
        "Sin eso se sube lo que haya en dist/, se llame como se llame"
    )


def test_twine_corre_despues_de_subir_el_artefacto() -> None:
    cuerpo = _job_que_contiene("python -m build")
    indices = {
        clave: next((i for i, linea in enumerate(cuerpo) if texto in linea), -1)
        for clave, texto in (
            ("build", "python -m build"),
            ("upload", "actions/upload-artifact@"),
            ("twine", "twine"),
        )
    }
    assert min(indices.values()) >= 0, f"falta un paso en el job que arma dist/: {indices}"
    assert indices["build"] < indices["upload"] < indices["twine"], (
        f"orden {indices}: twine (con dependencias sin pinnear) tiene que correr DESPUÉS de "
        "subir el artefacto, o podría cambiar el wheel que aprueba el revisor"
    )


def test_toda_action_esta_pinneada_por_sha() -> None:
    usos = [linea.strip() for linea in _codigo() if re.match(r"^\s*(?:- )?uses:", linea)]
    sueltas = [uso for uso in usos if not PINNEADA.search(uso)]
    assert usos and not sueltas, (
        f"actions sin pinnear por SHA en un workflow que publica: {sueltas}"
    )
