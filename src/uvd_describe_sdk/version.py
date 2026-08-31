"""The version, defined ONCE and in the code. `pyproject.toml` reads it from here.

The house's centralised-configuration rule (2026-08-26, born out of a case
measured in describe.net: the same threshold written with **two different values
across four surfaces**). A version written by hand in `pyproject.toml` **and**
another in `__version__` is that same disease in its cheapest-to-catch form: one
gets bumped, the other is forgotten, and the published package lies about itself.

Here the number lives in `__version__` and `pyproject.toml` imports it:

    [project]
    dynamic = ["version"]
    [tool.setuptools.dynamic]
    version = {attr = "uvd_describe_sdk.version.__version__"}

That is why this module **imports nothing**: setuptools reads it while building the
package, before any dependency exists installed.

And that is also why the User-Agent is assembled here. The lesson is MeshRelay's,
who read their version from `package.json` instead of typing it, with the reason
written down: **"a User-Agent that lies about the version is worse than none"**
(`meshrelay/meshrelayserv/describenet.js:39`). The UA is the only thing the
provider can log to attribute traffic — their rate limit is SHARED across every
consumer and there is no per-partner bucket — so an anonymous UA against a shared
limit is free-riding, and one that lies is worse: it sends you to look at the
wrong version.
"""

from __future__ import annotations

from typing import Optional

__version__ = "0.3.0"

#: The name that travels in the User-Agent. The `-py` tells this SDK apart from
#: its TypeScript twin in the provider's logs: they are two different clients with
#: the same routes, and separating them is half of what attribution is for.
USER_AGENT_NAME = "uvd-describe-sdk-py"


def default_user_agent(product: Optional[str] = None) -> str:
    """`uvd-describe-sdk-py/0.1.0` or `uvd-describe-sdk-py/0.1.0 (+karmakadabra)`.

    `product` is who is consuming (rule 7 of `F0-describe-sdk.md`:
    `uvd-describe-sdk-{ts|py}/x.y.z (+<product>)`). Passing it is not cosmetic:
    without it the whole ecosystem shows up as a single client in the index's logs,
    and the day somebody eats the shared rate limit there is no way to know who it
    was.

    It is sanitised into a header-safe token — a `\\n` in the UA is header
    splitting, and this string comes from the caller's configuration.
    """
    base = f"{USER_AGENT_NAME}/{__version__}"
    if not product:
        return base
    safe = "".join(ch for ch in str(product) if ch.isalnum() or ch in "-_./")[:40]
    return f"{base} (+{safe})" if safe else base
