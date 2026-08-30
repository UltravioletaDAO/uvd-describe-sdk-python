"""`badge_url()` — la única función del SDK que **no toca la red**.

Construye la URL de `GET /badge/{wallet}.svg` y nada más. No hace request, no
valida contra el índice, no dice si la wallet existe. Es a propósito: la URL
sirve para meterla en un `<img>` y que el navegador de quien mira la página
haga el fetch, no el proceso que la genera.

Por qué esto es la pieza que más le importa a Saul, textual (2026-08-29):

    «investiguen cómo fue el like button de Facebook […] era como un pedacito
     de JavaScript que se copiaba y se pegaba […] algo que la gente solamente
     con un pedacito pueda copiar y pegar. Ya solo con eso, inmediatamente es
     como el like button que está por todo lado, pero con su reputación»

La ruta ya está viva. Medida el 2026-08-30:
`GET /badge/0x97cd…0996.svg` → **HTTP 200, `image/svg+xml`, 602 bytes**, con
`Cache-Control: public, max-age=3600, stale-if-error=604800`. Ese
`stale-if-error` de una semana es el fallback de Saul («poné un fallback si es
que describe está caído») resuelto por el borde: el badge sigue pintando el
último valor conocido aunque el origen esté caído, sin una línea de código de
quien lo embebe.

⚠️ **Un badge no reemplaza una lectura.** Es una imagen: no trae `caveats[]`, no
distingue `[]` de `null` y no se puede ramificar sobre él. Para decidir se lee
`wallet()`; el badge es para MOSTRAR. La documentación publicada lo dice con
esas palabras — el endpoint libre «answers what the badge cannot: per-chain
split, `caveats[]`, `[]` vs `null`».
"""

from __future__ import annotations

from urllib.parse import quote

#: Sin barra final. El cliente lo normaliza igual, pero acá se deja explícito
#: porque `badge_url` es la única superficie que se usa sin instanciar nada.
DEFAULT_BASE_URL = "https://api.describe.net"


def badge_url(wallet: str, *, base_url: str = DEFAULT_BASE_URL) -> str:
    """La URL del badge SVG de una wallet. **Cero red.**

    >>> badge_url("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
    'https://api.describe.net/badge/0x97cd97cfe21799bacbf39d0a53469e5f82f30996.svg'

    La wallet viaja **verbatim**, sin `lower()`. No es un descuido: una EVM el
    índice la normaliza del lado del servidor (case-insensitive), pero un id
    Solana es **base58 case-SENSITIVE** y bajarlo a minúsculas nombra una clave
    distinta — en silencio, con un 200 y un badge vacío. Lo declara el propio
    schema del servicio: *«a Solana base58 id (case-SENSITIVE — lowercasing it
    silently names a different key)»*.

    Se escapa para URL porque el valor sale de datos de quien llama y termina en
    un `<img src=...>`. `safe=""` escapa también la barra: sin eso, una wallet
    con `/` inventada podría apuntar el `<img>` a otra ruta del índice.
    """
    return f"{base_url.rstrip('/')}/badge/{quote(str(wallet), safe='')}.svg"


def badge_img_tag(
    wallet: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    alt: str = "Reputación en describe",
    height: int = 20,
) -> str:
    """El `<img>` listo para copiar y pegar. También **cero red**.

    Es el pedacito que se copia y se pega, en su forma más chica: un `<img>`
    no necesita JavaScript, no necesita permiso, funciona en un foro y en un
    Markdown de GitHub.

    El `alt` no es decoración: un badge sin texto alternativo es un número que
    un lector de pantalla no puede leer, y el número es todo el contenido.
    """
    return (
        f'<img src="{badge_url(wallet, base_url=base_url)}" '
        f'alt="{alt}" height="{int(height)}" loading="lazy">'
    )
