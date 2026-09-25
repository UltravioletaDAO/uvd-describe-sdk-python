"""Graba las fixtures del resolver de nombres contra la cadena REAL.

    .venv/Scripts/python scripts/grabar_fixtures_names.py            # graba todo
    .venv/Scripts/python scripts/grabar_fixtures_names.py --probar resolve jesse.base.eth
    .venv/Scripts/python scripts/grabar_fixtures_names.py --solo resolve_jesse_base_eth

Por qué existe: una fixture escrita a mano testea contra la idea de quien la
escribió; una grabada testea contra lo que la cadena contesta. Cada archivo de
`tests/fixtures/names/` sale de acá, con la respuesta real de cada `eth_call` y
de cada gateway CCIP, en el orden en que el resolver los pidió.

Las reglas de la grabación (encargo del 2026-09-24):

* Sólo lecturas públicas a RPC públicos SIN llave (las URLs están abajo y son
  las mismas que cualquiera usa). Ninguna URL de RPC se escribe en una fixture:
  se guarda el id CAIP-2 de la cadena, y el test la mapea a una URL de mentira.
* Secuencial, con una pausa de al menos 1,1 s antes de CADA request.
* Al primer HTTP 429 el script se detiene sin guardar ese escenario.

Se graba con `cache=False` (cada escenario pide todo) y con el reloj del
momento de la grabación guardado en la fixture (`now`), para que el vencimiento
de un nombre se evalúe en el test igual que se evaluó en vivo.

⚠️ Desde la ronda 4 del PR #6 hay UN motor, el async, y el script quedó roto
sin que nada lo viera: importaba `run_sync` (borrado) y su `Grabadora` era un
transporte sólo-sync, que `NameResolver` ahora rechaza. Corregido en la ronda 5:
la `Grabadora` es un `httpx.AsyncBaseTransport` y `poseidon_tld_avax` corre por
el mismo motor (`run_blocking` + `run_bounded`). `tests/test_names_ronda5_script.py`
lo importa y lo corre contra las fixtures, sin red.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uvd_describe_sdk.names import NameResolver, _abi, _avvy  # noqa: E402
from uvd_describe_sdk.names._proto import (  # noqa: E402
    AVALANCHE,
    Call,
    Step,
    run_blocking,
    run_bounded,
)

#: RPC públicos, sin llave. Las mismas URLs de la documentación de cada red.
RPC_PUBLICOS: Dict[str, str] = {
    "eip155:1": "https://ethereum-rpc.publicnode.com",
    "eip155:8453": "https://base-rpc.publicnode.com",
    "eip155:137": "https://polygon-bor-rpc.publicnode.com",
    "eip155:43114": "https://api.avax.network/ext/bc/C/rpc",
}
CADENA_DE_URL = {url: chain for chain, url in RPC_PUBLICOS.items()}

PAUSA_S = 1.1
DESTINO = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "names"

#: (archivo, operación, argumentos, opciones del resolver). Cada nombre salió de
#: una medición del 2026-09-24, no de la memoria: los de UNS y Avvy se
#: descubrieron leyendo sus contratos, y el reverse inválido entre las 32
#: direcciones que emitieron `ReverseClaimed` en los últimos 5000 bloques de L1.
ESCENARIOS: List[Tuple[str, str, List[str], Dict[str, Any]]] = [
    # ENS en L1: los tres hechos del encargo
    ("resolve_ultravioletadao_eth", "resolve", ["ultravioletadao.eth"], {}),
    ("resolve_0xultravioletadao_eth_no_existe", "resolve", ["0xultravioletadao.eth"], {}),
    ("reverse_0xe4dc_ultravioleta", "reverse", ["0xe4dc963c56979E0260fc146b87eE24F18220e545"], {}),
    # El forward del nombre principal de arriba, suelto: con él se prueba la
    # rama «el nombre apunta a OTRA dirección» sin inventar una respuesta
    ("resolve_0xultravioleta_eth", "resolve", ["0xultravioleta.eth"], {}),
    # Basenames: forward por L1 + CCIP, reverse por ENSIP-19 en Base
    ("resolve_jesse_base_eth", "resolve", ["jesse.base.eth"], {}),
    (
        "reverse_jesse_solo_basenames",
        "reverse",
        ["0x2211d1D0020DAEA8039E46Cf1367962070d77DA9"],
        {"systems": ("basenames",)},
    ),
    # Un resolver REAL que contesta la dirección cero (el DefaultReverseResolver)
    ("resolve_default_reverse_direccion_cero", "resolve", ["default.reverse"], {}),
    # DNS importado a ENS (DNSSEC, por CCIP)
    ("resolve_gregskril_com_dns", "resolve", ["gregskril.com"], {}),
    # Un reverse que NO se muestra: `0x…hooks.cow.eth` con la dirección en
    # mayúsculas no está en forma normal ENSIP-15
    (
        "reverse_cow_hook_no_normalizado",
        "reverse",
        ["0xd02abdf2fb37a50d574292b0625ca2591922a513"],
        {"systems": ("ens",)},
    ),
    # Registros de texto (ENSIP-5), uno por CCIP y uno sin valor
    ("text_jesse_base_eth_description", "text", ["jesse.base.eth", "description"], {}),
    ("text_ultravioletadao_eth_url_sin_valor", "text", ["ultravioletadao.eth", "url"], {}),
    # Avatar ENSIP-12 directo, por CCIP
    ("avatar_jesse_base_eth", "avatar", ["jesse.base.eth"], {}),
    # Unstoppable
    ("resolve_brad_crypto", "resolve", ["brad.crypto"], {}),
    ("resolve_uns_no_existe", "resolve", ["uvd-no-existe-20260924.crypto"], {}),
    (
        "reverse_brad_solo_unstoppable",
        "reverse",
        ["0x8aaD44321A86b170879d7A244c1e8d360c99DdA8"],
        {"systems": ("unstoppable",)},
    ),
    # Avvy, on-chain
    ("resolve_miniholder_avax", "resolve", ["miniholder.avax"], {}),
    ("resolve_avvy_avax_vencido", "resolve", ["avvy.avax"], {}),
    (
        "reverse_miniholder_solo_avvy",
        "reverse",
        ["0x8f1AE22f6f28C954a03e1EAB3d6ba6b3E33d8853"],
        {"systems": ("avvy",)},
    ),
    # El hash de la TLD `avax` que los clientes oficiales pre-cachean, leído del
    # contrato `Poseidon` (no pasa por el resolver: ver `poseidon_tld_avax`)
    ("poseidon_tld_avax", "poseidon", [], {}),
    # Avatar NFT (ERC-721 en L1, metadata en IPFS). Va ÚLTIMO y con `dweb.link`:
    # el 2026-09-24 `ipfs.io` contestó 429 a la primera prueba.
    ("avatar_matoken_eth_nft", "avatar", ["matoken.eth"], {"ipfs_gateway": "https://dweb.link"}),
]


class Detenido(Exception):
    """El RPC o un gateway contestó 429: se para todo."""


class Grabadora(httpx.AsyncBaseTransport):
    """Transporte que hace el request real, pausa antes, y anota el intercambio.

    Async porque el motor es uno solo y es async (también bajo `*_sync`). Cada
    escenario hace UNA llamada con su propia `Grabadora`: el modo sync abre y
    cierra un cliente por llamada, y cerrarlo cierra este transporte.
    """

    def __init__(self, real: httpx.AsyncBaseTransport | None = None) -> None:
        self._real = real if real is not None else httpx.AsyncHTTPTransport()
        self._ultimo = 0.0
        self.intercambios: List[Dict[str, Any]] = []

    async def aclose(self) -> None:
        await self._real.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        espera = PAUSA_S - (time.monotonic() - self._ultimo)
        if espera > 0:
            await asyncio.sleep(espera)
        self._ultimo = time.monotonic()
        response = await self._real.handle_async_request(request)
        try:
            await response.aread()
        finally:
            await response.aclose()
        if response.status_code == 429:
            raise Detenido(f"429 de {request.url.host}")
        url = str(request.url)
        chain = CADENA_DE_URL.get(url)
        if chain is not None and json.loads(request.content).get("method") == "eth_chainId":
            # El chequeo de cadena del motor (ronda 2 del PR #6) NO se graba: el
            # reproductor lo contesta desde la clave CAIP-2 de la fixture. Así
            # las fixtures siguen siendo sólo los `eth_call` del resolver.
            pass
        elif chain is not None:
            cuerpo = json.loads(request.content)
            llamada = cuerpo["params"][0]
            self.intercambios.append(
                {
                    "kind": "rpc",
                    "chain": chain,
                    "to": llamada["to"],
                    "data": llamada["data"],
                    "status": response.status_code,
                    "response": json.loads(response.content),
                }
            )
        else:
            self.intercambios.append(
                {
                    "kind": "http",
                    "method": request.method,
                    "url": url,
                    "body": request.content.decode() if request.content else None,
                    "status": response.status_code,
                    "headers": {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() in ("location", "content-type")
                    },
                    "response_text": response.content.decode("utf-8", errors="replace"),
                }
            )
        # El cuerpo ya viene descomprimido: reenviar `content-encoding` haría que
        # httpx lo descomprima dos veces (DecodingError, medido en la 1.ª corrida).
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        }
        return httpx.Response(
            response.status_code, headers=headers, content=response.content, request=request
        )


def poseidon_tld_avax(grabadora: Grabadora) -> Dict[str, Any]:
    """Lee `poseidon([0, 2019653217, 0])` del contrato, sin la constante."""

    async def una_llamada() -> int:
        async with httpx.AsyncClient(transport=grabadora) as client:
            return await run_bounded(
                _poseidon_en_vivo(), rpc=RPC_PUBLICOS, client=client, timeout=60.0
            )

    salida = run_blocking(una_llamada)
    return {"now": time.time(), "result": {"poseidon": str(salida)}}


def _poseidon_en_vivo() -> Step[int]:
    out: bytes = yield Call(
        AVALANCHE,
        _avvy.POSEIDON,
        _avvy._SEL_POSEIDON + _abi.encode(["uint256[3]"], [[0, 2019653217, 0]]),
    )
    (valor,) = _abi.decode(["uint256"], out)
    resultado: int = valor
    return resultado


def correr(
    op: str, args: Sequence[str], opciones: Dict[str, Any], grabadora: Grabadora
) -> Dict[str, Any]:
    if op == "poseidon":
        return poseidon_tld_avax(grabadora)
    ahora = time.time()
    resolver = NameResolver(
        rpc=RPC_PUBLICOS,
        cache=False,
        timeout=180.0,
        async_transport=grabadora,
        clock=lambda: ahora,
        **opciones,
    )
    with resolver:
        resultado = getattr(resolver, f"{op}_sync")(*args)
    return {"now": ahora, "result": resultado.to_dict()}


def grabar(nombre: str, op: str, args: Sequence[str], opciones: Dict[str, Any]) -> None:
    grabadora = Grabadora()
    salida = correr(op, args, opciones, grabadora)
    fixture = {
        "scenario": nombre,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "op": op,
        "args": list(args),
        "options": {k: list(v) if isinstance(v, tuple) else v for k, v in opciones.items()},
        "chains": sorted(RPC_PUBLICOS),
        "now": salida["now"],
        "result": salida["result"],
        "exchanges": grabadora.intercambios,
    }
    DESTINO.mkdir(parents=True, exist_ok=True)
    ruta = DESTINO / f"{nombre}.json"
    # LF siempre (el repo normaliza a LF): regrabar en Windows o en Linux da los
    # mismos bytes. `open(newline=)` y no `write_text(newline=)`, que es de 3.10.
    with open(ruta, "w", encoding="utf-8", newline="\n") as salida_json:
        salida_json.write(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n")
    r = salida["result"]
    print(
        f"{nombre}: {len(grabadora.intercambios)} intercambios · "
        f"error={r.get('error')} address={r.get('address')} normalized={r.get('normalized')} "
        f"value={r.get('value')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probar", nargs="+", metavar=("OP", "ARG"))
    parser.add_argument("--sistemas", nargs="*")
    parser.add_argument("--solo", nargs="*", help="graba sólo estos escenarios")
    parser.add_argument("--nombre", help="con --probar: graba el resultado con este nombre")
    opts = parser.parse_args()
    try:
        if opts.probar:
            op, *args = opts.probar
            opciones: Dict[str, Any] = {"systems": tuple(opts.sistemas)} if opts.sistemas else {}
            if opts.nombre:
                grabar(opts.nombre, op, args, opciones)
                return 0
            grabadora = Grabadora()
            salida = correr(op, args, opciones, grabadora)
            print(json.dumps(salida["result"], indent=1, ensure_ascii=False))
            print(f"({len(grabadora.intercambios)} intercambios, nada guardado)")
            return 0
        for nombre, op, args, opciones in ESCENARIOS:
            if opts.solo and nombre not in opts.solo:
                continue
            grabar(nombre, op, args, opciones)
    except Detenido as alto:
        print(f"DETENIDO: {alto}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
