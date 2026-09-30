"""La cadena de publicación a PyPI: sólo a mano, sólo desde main, con revisor.

De dónde sale: DN-PUB-02 de c0der, 2026-09-30. Hasta 0.7.0,
`.github/workflows/publish.yml` se disparaba también por tag `v*` y subía a PyPI
sin que nadie lo aprobara: los runs de v0.5.0, v0.6.1 y v0.7.0 publicaron solos
(`gh api .../actions/runs`). La regla del stack es que ningún evento de git
despliega ni publica solo. La forma nueva es la del molde,
`uvd-x402-sdk-python/.github/workflows/publish.yml`.

Lo que se ata, cada cosa en su test:

* **Ningún disparador salvo el manual.** No sólo el tag: `release`, `schedule` o
  `workflow_run` también publicarían sin que nadie lo pida, y un `grep push:` no
  los ve. Por eso se leen las CLAVES de `on:`, además del texto.
* **Ningún `push:`, `tags:` ni `secrets.` en el archivo**, comentarios incluidos:
  es exactamente el `grep` de la verificación del encargo. Se publica por OIDC, y
  un token de PyPI guardado en el repo es una credencial de larga vida que
  publica desde cualquier lado.
* **Un job espera en el environment `pypi`** (el que tiene revisor), **y el
  id-token vive en ESE job y en ningún otro.** Un id-token a nivel de workflow, o
  en el job que corre los tests con deps sin pinnear, es una credencial de
  publicar al alcance de código de terceros. `write-all` también lo da.
* **Publicar exige pasar por el job que sólo corre en main**, sin un `always()`
  que salte el gate.
* **La versión pedida se compara con `version.py`**: es lo que reemplaza al
  chequeo «tag == `__version__`» del workflow viejo.
* **Toda action va pinneada por SHA**: un tag de action es mutable, y este
  workflow tiene permiso de publicar.

Se lee como TEXTO porque PyYAML no es dependencia del SDK ni del extra `dev`.
Las prohibiciones miran el archivo CRUDO, que es más estricto; las presencias
miran sólo las líneas de código, porque un comentario que dice
`environment: pypi` no protege nada (mutación EK).

Las mutaciones que se vieron rojas están en la tabla de `CLAUDE.md` (EA–EL).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

import pytest

PUBLISH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"

MAIN = "github.ref == 'refs/heads/main'"
ID_TOKEN = re.compile(r"id-token\s*:\s*write")
SALTA_EL_GATE = re.compile(r"always\(\)|cancelled\(\)|failure\(\)")
PINNEADA = re.compile(r"@[0-9a-f]{40}$")


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


def _jobs_en_pypi() -> List[str]:
    return [nombre for nombre, cuerpo in _jobs().items() if _clave(cuerpo, "environment") == "pypi"]


def test_solo_se_dispara_a_mano() -> None:
    disparadores = _disparadores()
    assert disparadores == {"workflow_dispatch"}, (
        f"publish.yml se dispara con {sorted(disparadores)}: cualquier evento que no sea el "
        "manual publica a PyPI sin que nadie lo pida (hasta 0.7.0, un tag `v*` lo hacía)"
    )


@pytest.mark.parametrize("prohibido", ["push:", "tags:", "secrets."])
def test_el_archivo_no_nombra_push_tags_ni_secretos(prohibido: str) -> None:
    lineas = [
        f"{numero}: {linea.strip()}"
        for numero, linea in enumerate(_crudo().splitlines(), 1)
        if prohibido in linea
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
            f"el job `{nombre}` corre con `if: {condicion}`: corre aunque el gate de main "
            "se haya salteado"
        )
        if condicion == MAIN:
            gate.append(nombre)
        pendientes.extend(_needs(cuerpo))
    assert gate, (
        f"ningún job de la cadena {sorted(vistos)} tiene `if: {MAIN}`: se podría publicar "
        "despachando desde otra rama"
    )


def test_la_version_pedida_se_compara_con_version_py() -> None:
    jobs = _jobs()
    en_main = [nombre for nombre, cuerpo in jobs.items() if _clave(cuerpo, "if") == MAIN]
    texto = "\n".join(linea for nombre in en_main for linea in jobs[nombre])
    assert "inputs.version" in texto and "src/uvd_describe_sdk/version.py" in texto, (
        "el job de main no compara `inputs.version` con `src/uvd_describe_sdk/version.py`: "
        "pedir una versión sobre un árbol que dice otra subiría la que no se pidió, y un "
        "número subido a PyPI queda quemado"
    )


def test_toda_action_esta_pinneada_por_sha() -> None:
    usos = [linea.strip() for linea in _codigo() if re.match(r"^\s*(?:- )?uses:", linea)]
    sueltas = [uso for uso in usos if not PINNEADA.search(uso)]
    assert usos and not sueltas, (
        f"actions sin pinnear por SHA en un workflow que publica: {sueltas}"
    )
