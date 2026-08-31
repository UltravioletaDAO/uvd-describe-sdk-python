"""La versión, definida UNA vez y en el código. `pyproject.toml` la lee de acá.

Regla de configuración centralizada de la casa (2026-08-26, nacida de un caso
medido en describe.net: el mismo umbral escrito con **dos valores distintos en
cuatro superficies**). Una versión escrita a mano en `pyproject.toml` **y**
otra en `__version__` es esa misma enfermedad en su forma más barata de
contraer: se bumpea una, se olvida la otra, y el paquete publicado miente sobre
sí mismo.

Acá el número vive en `__version__` y `pyproject.toml` lo importa:

    [project]
    dynamic = ["version"]
    [tool.setuptools.dynamic]
    version = {attr = "uvd_describe_sdk.version.__version__"}

Por eso este módulo **no importa nada**: setuptools lo lee al construir el
paquete, antes de que exista ninguna dependencia instalada.

Y por eso también el User-Agent se arma acá. La lección es de MeshRelay, que
lee su versión del `package.json` en vez de tipearla, con la razón escrita:
**«a User-Agent that lies about the version is worse than none»**
(`meshrelay/meshrelayserv/describenet.js:39`). El UA es lo único que el
proveedor puede loguear para atribuir tráfico —su rate limit es COMPARTIDO
entre todos los consumidores y no hay bucket por partner— así que un UA anónimo
contra un límite compartido es free-riding, y uno que miente es peor: manda a
mirar la versión equivocada.
"""

from __future__ import annotations

from typing import Optional

__version__ = "0.1.0"

#: El nombre que viaja en el User-Agent. `-py` distingue este SDK de su gemelo
#: TypeScript en los logs del proveedor: son dos clientes distintos con las
#: mismas rutas, y separarlos es la mitad de para qué sirve la atribución.
USER_AGENT_NAME = "uvd-describe-sdk-py"


def default_user_agent(product: Optional[str] = None) -> str:
    """`uvd-describe-sdk-py/0.1.0` o `uvd-describe-sdk-py/0.1.0 (+karmakadabra)`.

    `product` es quién está consumiendo (regla 7 de `F0-describe-sdk.md`:
    `uvd-describe-sdk-{ts|py}/x.y.z (+<producto>)`). Pasarlo no es cosmética:
    sin él, todo el ecosistema aparece como un solo cliente en los logs del
    índice y el día que alguien se coma el rate limit compartido no hay forma
    de saber quién fue.

    Se sanea a un token seguro para un header — un `\\n` en el UA es splitting
    de headers, y este string sale de configuración de quien llama.
    """
    base = f"{USER_AGENT_NAME}/{__version__}"
    if not product:
        return base
    safe = "".join(ch for ch in str(product) if ch.isalnum() or ch in "-_./")[:40]
    return f"{base} (+{safe})" if safe else base
