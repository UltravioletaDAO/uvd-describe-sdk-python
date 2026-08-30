"""Fixtures compartidos. **La suite entera corre sin red.**

El único seam que hace falta es `transport=` del constructor: `httpx.MockTransport`
recibe la request y devuelve lo que le digamos. No hay monkeypatch de `httpx`, no
hay un servidor de mentira, no hay `sleep`.

Los payloads de abajo **no son inventados**: son capturas literales de
`api.describe.net` del 2026-08-30. Un fixture inventado testea contra la idea
que tenía quien lo escribió; uno capturado testea contra lo que el servicio
manda. La diferencia ya se pagó una vez en este ecosistema —
`WalletChains.distinct_raters` viaja vivo y **no existe** en la implementación
de referencia de Execution Market.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from uvd_describe_sdk import DescribeClient

# ---------------------------------------------------------------------------
# Capturas literales de api.describe.net — 2026-08-30
# ---------------------------------------------------------------------------

#: `GET /wallets/0x97cd…0996/chains` → 200, 453 bytes. Una wallet CON reputación.
WALLET_CON_REPUTACION: Dict[str, Any] = {
    "wallet": "0x97cd97cfe21799bacbf39d0a53469e5f82f30996",
    "chains": [
        {
            "network": "monad",
            "agent_count": 1,
            "agent_ids": ["182"],
            "final_score": 100.0,
            "total_reviews": 7548,
            "distinct_raters": 7548,
        }
    ],
    "caveats": [],
    "identity_count": 1,
    "chains_with_identity": 1,
    "chains_with_reputation": 1,
    "total_reviews": 7548,
    # 🔴 Este campo viaja VIVO y NO existe en `types.py` de Execution Market.
    # Está acá para que el test de passthrough tenga un caso real y no uno
    # inventado.
    "distinct_raters": 7548,
    "global_score": 100.0,
    "policy_version": "equal-weight-per-chain@2",
    "source": "chain_rankings_mv",
    "refreshed_at": "2026-08-30T20:50:02.839788Z",
}

#: Una wallet REGISTRADA y todavía SIN CALIFICAR. Misma forma, `final_score` y
#: `global_score` en `null` — el hecho que R1 protege.
WALLET_REGISTRADA_SIN_CALIFICAR: Dict[str, Any] = {
    "wallet": "0x00000000000000000000000000000000000000aa",
    "chains": [
        {
            "network": "base",
            "agent_count": 1,
            "agent_ids": ["9001"],
            "final_score": None,
            "total_reviews": 0,
            "distinct_raters": 0,
        }
    ],
    "caveats": [{"code": "no-score", "text": "No hay score que leer."}],
    "identity_count": 1,
    "chains_with_identity": 1,
    "chains_with_reputation": 0,
    "total_reviews": 0,
    "distinct_raters": 0,
    "global_score": None,
    "policy_version": "equal-weight-per-chain@2",
    "source": "chain_rankings_mv",
    "refreshed_at": "2026-08-30T20:50:02.839788Z",
}

#: Una wallet que el índice NO conoce: 200 con todo en cero y `chains: []`.
WALLET_NO_REGISTRADA: Dict[str, Any] = {
    "wallet": "0x00000000000000000000000000000000000000bb",
    "chains": [],
    "caveats": [],
    "identity_count": 0,
    "chains_with_identity": 0,
    "chains_with_reputation": 0,
    "total_reviews": 0,
    "distinct_raters": 0,
    "global_score": None,
    "policy_version": "equal-weight-per-chain@2",
    "source": "chain_rankings_mv",
    "refreshed_at": "2026-08-30T20:50:02.839788Z",
}

#: Fila 1 de `GET /leaderboard` (200, 22.235 bytes, 100 filas).
LEADERBOARD: List[Dict[str, Any]] = [
    {
        "rank": 1,
        "wallet": "0x97cd97cfe21799bacbf39d0a53469e5f82f30996",
        "final_score": 100.0,
        "shrunk_score": 99.982977,
        "distinct_raters": 7548,
        "chain_count": 1,
        "total_reviews": 7548,
        "networks": ["monad"],
        "declared_types": [None],
    },
    {
        "rank": 2,
        "wallet": "0x00000000000000000000000000000000000000cc",
        "final_score": None,
        "shrunk_score": None,
        "distinct_raters": 0,
        "chain_count": 1,
        "total_reviews": 0,
        "networks": ["base"],
        "declared_types": [None],
    },
]

#: `GET /health` → 200, 4.087 bytes. Recortado a las claves que el SDK tipa,
#: más una fila de cadena literal.
HEALTH: Dict[str, Any] = {
    "status": "ok",
    "policy_version": "equal-weight-per-chain@2",
    "ordering_policy": "bayesian-shrinkage@1",
    "rater_weight_policy": "log-diversity@1",
    "confidence_policy": "wilson@1",
    "confidence_thresholds": {"no_ratings": 0, "low": 1, "medium": 3, "high": 6},
    "reading_policy": {
        "min_raters": 3,
        "campaign_per_rater": 20,
        "top_share": 0.5,
        "self_gap": None,
        "combine": "independent",
        "facet_min_distinct_agents": 4,
    },
    "build_sha": "737bc1e2964599f533415a6a1910aa5ddbbc29cd",
    "agents": 470193,
    "feedback_entries": 552416,
    "indexer_period_seconds": 3600,
    "chains": [
        {
            "network": "arbitrum",
            "last_scanned_block": 500064997,
            "head_at_last_sync": 500065009,
            "started_at": "2026-08-11T16:25:31.996475Z",
            "updated_at": "2026-08-30T20:48:38.100848Z",
            "last_error": None,
            "newest_signature": None,
            "oldest_signature": None,
            "backfill_complete": False,
            "next_sync_at": "2026-08-30T21:48:38.100848Z",
        }
    ],
}

#: El challenge 402 de `GET /reputation/wallet/{w}`, capturado entero (las 6
#: entradas de `accepts[]` recortadas a 2 para que el fixture se lea).
CHALLENGE_402: Dict[str, Any] = {
    "error": "payment_required",
    "recipient": "0xe4dc963c56979E0260fc146b87eE24F18220e545",
    "recipients": {"evm": "0xe4dc963c56979E0260fc146b87eE24F18220e545"},
    "amount": "0.01",
    "token": "USDC",
    "supportedChains": [8453, 43114, 42161, 10, 137, 42220],
    "x402Version": 2,
    "scheme": "exact",
    "resource": "GET /reputation/wallet/0x97cd97cfe21799bacbf39d0a53469e5f82f30996",
    "description": "EL COBRO BASE.",
    "mimeType": "application/json",
    "maxTimeoutSeconds": 120,
    "price_usd": "0.01",
    "pricing": {"version": "cost-tiered@5", "tier": "wallet-lookup"},
    "free_preview": {"endpoint": "GET /wallets/{wallet}/chains"},
    "accepts": [
        {
            "scheme": "exact",
            "network": "eip155:8453",
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "amount": "10000",
            "payTo": "0xe4dc963c56979E0260fc146b87eE24F18220e545",
            "maxTimeoutSeconds": 120,
            "extra": {"name": "USD Coin", "version": "2"},
        },
        {
            "scheme": "exact",
            "network": "eip155:43114",
            "asset": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
            "amount": "10000",
            "payTo": "0xe4dc963c56979E0260fc146b87eE24F18220e545",
            "maxTimeoutSeconds": 120,
            "extra": {"name": "USDC", "version": "2"},
        },
    ],
}

#: `GET /reputation/wallet/{w}` pagado. Recortado, con las claves requeridas.
BREAKDOWN: Dict[str, Any] = {
    "wallet": "0x97cd97cfe21799bacbf39d0a53469e5f82f30996",
    "final_score": 86.653045,
    "weighted_score": None,
    "rater_weight_policy": "log-diversity@1",
    "chain_count": 1,
    "total_reviews": 7548,
    "per_chain": {"monad": {"score": 100.0, "review_count": 7548, "agent_ids": ["182"]}},
    "facets": {
        "delivery": {
            "score": 91.5,
            "count": 12,
            "distinct_raters": 5,
            "revoked_count": 0,
            "out_of_domain_count": 0,
            "self_rated_count": 0,
            "direction": "about",
            "direction_category": "subject",
            "direction_meaning": "sobre el sujeto",
        }
    },
    "self_rated": {"count": 0, "score": None, "gap": None},
    "concentration": {"distinct_raters": 7548, "top_client_share": 0.02, "top_client": "0xab"},
    "confidence": {
        "band": "high",
        "distinct_raters": 7548,
        "interval": {"lower": 99.1, "upper": 100.0},
        "thresholds": {"no_ratings": 0, "low": 1, "medium": 3, "high": 6},
        "advice": "suficientes calificadores distintos",
        "confidence_policy": "wilson@1",
    },
    "activity": {
        "first_rating_at": "2026-07-01T00:00:00Z",
        "last_rating_at": "2026-08-30T00:00:00Z",
    },
    "caveats": [{"code": "campaign-per-rater", "text": "…"}],
    "policy_version": "equal-weight-per-chain@2",
    "snapshot": None,
}


# ---------------------------------------------------------------------------
# El seam: un transporte de mentira, sin red
# ---------------------------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]


def json_response(
    payload: Any, status: int = 200, headers: Optional[Dict[str, str]] = None
) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
    )


@pytest.fixture
def make_client() -> Callable[..., DescribeClient]:
    """Un `DescribeClient` cableado a un handler, sin tocar la red.

        client = make_client(lambda req: json_response(WALLET_CON_REPUTACION))
    """

    def _make(handler: Handler, **kwargs: Any) -> DescribeClient:
        return DescribeClient(transport=httpx.MockTransport(handler), **kwargs)

    return _make


@pytest.fixture
def recorded_errors() -> List[Any]:
    """Lista donde el `on_error` del cliente deja lo que el fail-open tragó."""
    return []
