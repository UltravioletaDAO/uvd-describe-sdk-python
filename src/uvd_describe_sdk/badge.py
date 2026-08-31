"""`badge_url()` — the one function of the SDK that **does not touch the network**.

It builds the `GET /badge/{wallet}.svg` URL and nothing else. It makes no request,
does not validate against the index, does not say whether the wallet exists. That
is on purpose: the URL is meant to go into an `<img>` so the browser of whoever is
looking at the page does the fetch, not the process that generated it.

Why this is the piece Saul cares about most, verbatim (2026-08-29):

    *«investiguen cómo fue el like button de Facebook […] era como un pedacito
     de JavaScript que se copiaba y se pegaba […] algo que la gente solamente
     con un pedacito pueda copiar y pegar. Ya solo con eso, inmediatamente es
     como el like button que está por todo lado, pero con su reputación»*

    [translation] "look into how Facebook's like button worked […] it was like a
    little piece of JavaScript you copied and pasted […] something people can copy
    and paste with just a little piece. With that alone it is immediately like the
    like button that is everywhere, but with its reputation."

The route is already live. Measured 2026-08-30:
`GET /badge/0x97cd…0996.svg` → **HTTP 200, `image/svg+xml`, 602 bytes**, with
`Cache-Control: public, max-age=3600, stale-if-error=604800`. That week-long
`stale-if-error` is Saul's fallback ("put a fallback in if describe is down")
solved at the edge: the badge keeps painting the last known value even with the
origin down, without a line of code from whoever embeds it.

⚠️ **A badge does not replace a read.** It is an image: it does not carry
`caveats[]`, it does not distinguish `[]` from `null` and you cannot branch on it.
To decide you read `wallet()`; the badge is to SHOW. The published documentation
says it in those words — the free endpoint "answers what the badge cannot:
per-chain split, `caveats[]`, `[]` vs `null`".
"""

from __future__ import annotations

from urllib.parse import quote

#: No trailing slash. The client normalises it anyway, but it is made explicit
#: here because `badge_url` is the only surface used without instantiating
#: anything.
DEFAULT_BASE_URL = "https://api.describe.net"


def badge_url(wallet: str, *, base_url: str = DEFAULT_BASE_URL) -> str:
    """The SVG badge URL of a wallet. **Zero network.**

    >>> badge_url("0x97cd97cfe21799bacbf39d0a53469e5f82f30996")
    'https://api.describe.net/badge/0x97cd97cfe21799bacbf39d0a53469e5f82f30996.svg'

    The wallet travels **verbatim**, with no `lower()`. That is not an oversight:
    an EVM one is normalised by the index server-side (case-insensitive), but a
    Solana id is **case-SENSITIVE base58** and lowercasing it names a different key
    — silently, with a 200 and an empty badge. The service's own schema declares
    it: *"a Solana base58 id (case-SENSITIVE — lowercasing it silently names a
    different key)"*.

    It is URL-escaped because the value comes from the caller's data and ends up in
    an `<img src=...>`. `safe=""` escapes the slash too: without that, a made-up
    wallet containing `/` could point the `<img>` at another route of the index.
    """
    return f"{base_url.rstrip('/')}/badge/{quote(str(wallet), safe='')}.svg"


def badge_img_tag(
    wallet: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    alt: str = "describe reputation",
    height: int = 20,
) -> str:
    """The copy-paste-ready `<img>`. Also **zero network**.

    It is the little piece that gets copied and pasted, in its smallest form: an
    `<img>` needs no JavaScript, needs no permission, and works in a forum and in a
    GitHub Markdown file.

    The `alt` is not decoration: a badge with no alternative text is a number a
    screen reader cannot read, and the number is the entire content.
    """
    return (
        f'<img src="{badge_url(wallet, base_url=base_url)}" '
        f'alt="{alt}" height="{int(height)}" loading="lazy">'
    )
