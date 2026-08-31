"""REAL smoke against `api.describe.net`. **FREE routes only.**

It is the only file in the repo that touches the network. It exists because the
suite runs with a fake transport — which is fine and fast — but a mock only proves
the SDK reads correctly what I BELIEVE the service sends. This script proves the
service sends that.

It is the pattern of the service's `scripts/test_sdk_paridad.py` with the axis
rotated: over there it is vendored-vs-installed, here it is
**SDK-vs-deployed-API**. The failure mode it catches is a client's real one: the
API adds or renames a field and the SDK silently discards it, with a green 200.

🔴 **It NEVER calls a metered route.** `wallet_breakdown()` and `agent()` cost real
USDC, and a smoke test that spends money is a smoke test nobody runs. For the
metered route it only verifies that the **402 arrives and parses** — asking for the
challenge is free and is the first move the service expects.

    python examples/smoke_gratis.py
"""

from __future__ import annotations

import sys

# La consola de Windows arranca en cp1252 y revienta con `→`, `✅` o un acento
# (`UnicodeEncodeError: 'charmap' codec can't encode character '→'`).
# Medido acá mismo el 2026-08-30: el script hizo todo su trabajo y murió
# imprimiendo el resultado. Un smoke test que se cae en su propia salida es un
# smoke test que nadie corre.
if hasattr(sys.stdout, "reconfigure"):  # Python 3.7+
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from uvd_describe_sdk import (
    DescribeClient,
    PaymentRequiredError,
    __version__,
    format_score,
)

# Wallet #1 del leaderboard al 2026-08-30. Se relee del leaderboard en vivo más
# abajo en vez de confiar en esta constante: toda cifra o se lee viva o lleva
# fecha, y un ranking se mueve.
SEMILLA = "0x97cd97cfe21799bacbf39d0a53469e5f82f30996"


def main() -> int:
    print(f"uvd-describe-sdk {__version__} — smoke against api.describe.net\n")
    fallas = []

    with DescribeClient(product="smoke", fail_open=False) as d:
        # ── /health ────────────────────────────────────────────────────────
        h = d.health()
        # Con `fail_open=False` estas dos no pueden devolver `None`: o contestan
        # o levantan. El guard está para que el ejemplo se lea como se escribe un
        # consumidor con el default (`fail_open=True`), donde `None` SÍ llega.
        assert h is not None
        print(f"  health          status={h.status} policy={h.policy_version}")
        print(f"                  agents={h.agents:,} feedback={h.feedback_entries:,}")
        print(f"                  build_sha={(h.build_sha or '')[:12]} chains={len(h.chains)}")
        print(f"                  reading_policy={h.reading_policy}")
        if h.status != "ok":
            fallas.append(f"health.status = {h.status}")
        if not h.reading_policy:
            fallas.append("health.reading_policy empty: the SDK is not reading it")

        # ── /leaderboard ───────────────────────────────────────────────────
        filas = d.leaderboard()
        assert filas is not None  # ídem: `fail_open=False`
        print(f"\n  leaderboard     {len(filas)} rows")
        top = filas[0]
        print(
            f"                  #1 {top.wallet[:12]}… "
            f"final={format_score(top.final_score)} "
            f"shrunk={format_score(top.shrunk_score)} "
            f"raters={top.distinct_raters:,}"
        )
        if not filas:
            fallas.append("leaderboard empty")
        # El orden es por la media bayesiana, no por el promedio: se comprueba
        # que `shrunk_score` viaje, que es lo que permite recomputarlo a mano.
        if top.shrunk_score is None:
            fallas.append("row #1 does not carry shrunk_score")

        # ── /wallets/{w}/chains ────────────────────────────────────────────
        rep = d.wallet(top.wallet)
        if rep is None:
            fallas.append("wallet() returned None on the leaderboard #1")
        else:
            print(
                f"\n  wallet          {rep.wallet[:12]}… "
                f"score={format_score(rep.global_score)} "
                f"identities={rep.identity_count} "
                f"reviews={rep.total_reviews:,}"
            )
            print(f"                  policy={rep.policy_version} source={rep.source}")
            print(f"                  refreshed_at={rep.refreshed_at}")
            print(f"                  caveats={rep.caveat_codes}")
            # R2: el sello de composición tiene que venir SIEMPRE.
            for campo in ("policy_version", "source", "refreshed_at"):
                if getattr(rep, campo) is None:
                    fallas.append(f"wallet(): missing {campo} — R2")
            # Campos que la API sirve y el SDK podría estar tirando.
            desconocidos = set(rep.raw) - {
                "wallet", "chains", "caveats", "identity_count",
                "chains_with_identity", "chains_with_reputation", "total_reviews",
                "distinct_raters", "global_score", "policy_version", "source",
                "refreshed_at",
            }
            if desconocidos:
                print(f"  ⚠️  NEW fields in the response (via .raw): {sorted(desconocidos)}")

        # ── una wallet que el índice no conoce ──────────────────────────────
        # R1 en vivo: la ausencia tiene que ser un OBJETO, no un None ni un 0.
        vacia = d.wallet("0x000000000000000000000000000000000000beef")
        if vacia is None:
            fallas.append("an unknown wallet returned None (it should be an object)")
        else:
            print(
                f"\n  empty wallet    identities={vacia.identity_count} "
                f"score={vacia.global_score!r} → shown as «{format_score(vacia.global_score)}»"
            )
            if vacia.global_score == 0:
                fallas.append("🔴 R1 BROKEN: a wallet with no data came back with score 0")

        # ── /badge/{w}.svg ─────────────────────────────────────────────────
        # `badge_url` no toca la red; acá se pide a mano para confirmar que la
        # URL que arma es la que el servicio sirve.
        url = d.badge_url(top.wallet)
        r = d._http.get(url)
        print(f"\n  badge           {r.status_code} {r.headers.get('content-type')} "
              f"{len(r.content)} bytes")
        print(f"                  cache-control={r.headers.get('cache-control')}")
        if r.status_code != 200 or "svg" not in (r.headers.get("content-type") or ""):
            fallas.append(f"badge: {r.status_code} {r.headers.get('content-type')}")

        # ── el 402 de una ruta paga: se PIDE, no se paga ───────────────────
        try:
            d.wallet_breakdown(SEMILLA)
            fallas.append("🔴 a metered route answered WITHOUT paying — was anything charged?")
        except PaymentRequiredError as exc:
            ch = exc.challenge
            print(
                f"\n  402 (unpaid)    price_usd={exc.price_usd} token={ch.get('token')} "
                f"accepts={len(ch.get('accepts') or [])}"
            )
            print(f"                  recipient={ch.get('recipient')}")
            print(f"                  free_preview={(ch.get('free_preview') or {}).get('endpoint')}")
            from uvd_describe_sdk import TREASURY_EVM

            if str(ch.get("recipient", "")).lower() != TREASURY_EVM.lower():
                fallas.append(
                    "🔴 the LIVE treasury does not match the one pinned in the SDK: "
                    f"live={ch.get('recipient')} pinned={TREASURY_EVM}"
                )
            else:
                print("                  ✅ the live treasury == the one pinned in the SDK")

    print()
    if fallas:
        print("❌ FAILURES:")
        for f in fallas:
            print(f"   · {f}")
        return 1
    print("✅ smoke OK — every free route answered and the SDK read them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
