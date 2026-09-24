"""Ronda 3 del PR #6: el timeout sync, duro también donde el cuerpo no llega.

El verificador de la ronda 2 midió, con un servidor LOCAL y el transporte httpx
real (presupuesto 1,0 s): headers goteando de a 1 byte cada 0,5 s → 15,2 s; un
gzip con FLG=FCOMMENT y el comentario goteando → 10,15 s. Re-medido acá antes
de arreglarlo (`cbc93a0`): 11,43 s y 10,15 s — el segundo, encima, «sin error» y
con el cuerpo vacío. Después del arreglo: 1,00 s y rechazo inmediato.

Estos tests usan un servidor de verdad en 127.0.0.1 (no un `MockTransport`),
porque lo que se prueba es el socket: un doble en memoria no tiene lecturas que
bloquear. No salen a ninguna red: el servidor vive en este proceso, y los
clientes de `names` no leen proxies del entorno.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Iterator, List

import httpx
import pytest

from uvd_describe_sdk.names import InvalidNameError, NameResolver, _proto
from uvd_describe_sdk.names._deadline import DeadlineTransport, _cap, deadline_scope
from uvd_describe_sdk.names._proto import Fetch, Unavailable, run_sync

PRESUPUESTO = 0.6
MARGEN = 0.6
GOTEO = 0.2

#: gzip: ID1 ID2 CM=8 FLG=0x10 (FCOMMENT) MTIME(4) XFL OS — y después el
#: comentario, que nunca termina. zlib no produce un solo byte decodificado.
_GZIP_FCOMMENT = bytes([0x1F, 0x8B, 8, 0x10, 0, 0, 0, 0, 0, 255])


class ServidorLocal:
    """Un servidor HTTP/1.1 mínimo en 127.0.0.1 que gotea lo que se le pida."""

    def __init__(self, responder: Callable[[socket.socket, bytes], None]) -> None:
        self._responder = responder
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.puerto = self._sock.getsockname()[1]
        self.pedidos: List[bytes] = []
        threading.Thread(target=self._aceptar, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.puerto}/"

    def _aceptar(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._uno, args=(conn,), daemon=True).start()

    def _uno(self, conn: socket.socket) -> None:
        try:
            pedido = conn.recv(65536)
            self.pedidos.append(pedido)
            self._responder(conn, pedido)
        except OSError:
            pass
        finally:
            conn.close()

    def cerrar(self) -> None:
        self._sock.close()


def _gotea(conn: socket.socket, datos: bytes) -> None:
    for byte in datos:
        conn.sendall(bytes([byte]))
        time.sleep(GOTEO)


def _headers_goteando(conn: socket.socket, _: bytes) -> None:
    _gotea(
        conn, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
    )


def _gzip_goteando(conn: socket.socket, _: bytes) -> None:
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nConnection: close\r\n\r\n")
    conn.sendall(_GZIP_FCOMMENT)
    _gotea(conn, b"a" * 200)


@pytest.fixture
def servidor() -> Iterator[Callable[[Callable[[socket.socket, bytes], None]], ServidorLocal]]:
    abiertos: List[ServidorLocal] = []

    def crear(responder: Callable[[socket.socket, bytes], None]) -> ServidorLocal:
        nuevo = ServidorLocal(responder)
        abiertos.append(nuevo)
        return nuevo

    yield crear
    for abierto in abiertos:
        abierto.cerrar()


def _cronometrar(correr: Callable[[], object]) -> float:
    inicio = time.monotonic()
    correr()
    return time.monotonic() - inicio


# ---------------------------------------------------------------------------
# El camino del RPC, por el resolver entero y su transporte por defecto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("responder", [_headers_goteando, _gzip_goteando], ids=["headers", "gzip"])
def test_sync_un_RPC_que_gotea_headers_o_un_gzip_se_corta_en_el_presupuesto(
    servidor: Callable[..., ServidorLocal], responder: Callable[[socket.socket, bytes], None]
) -> None:
    rpc = servidor(responder)
    resultado = []
    with NameResolver(rpc={"eip155:1": rpc.url}, cache=False, timeout=PRESUPUESTO) as resolver:
        duro = _cronometrar(lambda: resultado.append(resolver.resolve_sync("ultravioletadao.eth")))
    assert resultado[0].error == "rpc_unavailable"
    assert duro < PRESUPUESTO + MARGEN, f"presupuesto {PRESUPUESTO} s, tardó {duro:.2f} s"
    assert "127.0.0.1" not in repr(resultado[0]), "el detalle nombra la cadena, nunca la URL"


# ---------------------------------------------------------------------------
# El camino de un gateway (Fetch), con `check_url` parcheado SÓLO acá
# ---------------------------------------------------------------------------


def _fetch(url: str, timeout: float) -> object:
    def pasos() -> _proto.Step[object]:
        respuesta = yield Fetch(url)
        return respuesta

    with httpx.Client(transport=DeadlineTransport(), trust_env=False) as client:
        return run_sync(pasos(), rpc={}, client=client, timeout=timeout)


def test_sync_un_gateway_que_gotea_los_headers_se_corta_en_el_presupuesto(
    servidor: Callable[..., ServidorLocal], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_proto, "check_url", lambda url: None)
    gateway = servidor(_headers_goteando)
    inicio = time.monotonic()
    with pytest.raises(Unavailable):
        _fetch(gateway.url, PRESUPUESTO)
    duro = time.monotonic() - inicio
    assert duro < PRESUPUESTO + MARGEN, f"presupuesto {PRESUPUESTO} s, tardó {duro:.2f} s"


def test_un_gateway_pide_identity_y_un_cuerpo_gzip_no_se_lee(
    servidor: Callable[..., ServidorLocal], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_proto, "check_url", lambda url: None)
    gateway = servidor(_gzip_goteando)
    inicio = time.monotonic()
    with pytest.raises(Unavailable, match="gzip-encoded"):
        _fetch(gateway.url, 5.0)
    assert time.monotonic() - inicio < 1.0, "se rechaza en los headers, sin esperar el cuerpo"
    assert b"accept-encoding: identity" in gateway.pedidos[0].lower()


# ---------------------------------------------------------------------------
# Un redirect no se sigue pasado el plazo (P3-1)
# ---------------------------------------------------------------------------


def test_un_redirect_no_se_sigue_pasado_el_presupuesto() -> None:
    """Con `MockTransport` el plazo del socket no aplica: lo que corta es el
    chequeo del reloj antes de cada redirect. Sin él, el segundo host se pide."""
    pedidas: List[str] = []

    def lento_y_redirige(request: httpx.Request) -> httpx.Response:
        pedidas.append(str(request.url))
        if request.url.host == "uno.example":
            time.sleep(0.3)
            return httpx.Response(302, headers={"location": "https://dos.example/x"})
        return httpx.Response(200, json={"data": "0x"})

    def pasos() -> _proto.Step[object]:
        respuesta = yield Fetch("https://uno.example/x")
        return respuesta

    with httpx.Client(transport=httpx.MockTransport(lento_y_redirige)) as client:
        with pytest.raises(Unavailable, match="before a redirect"):
            run_sync(pasos(), rpc={}, client=client, timeout=0.2)
    assert pedidas == ["https://uno.example/x"]


# ---------------------------------------------------------------------------
# El backend: fuera de una llamada no toca nada; adentro, acota y corta
# ---------------------------------------------------------------------------


def test_el_tope_del_backend() -> None:
    import httpcore

    assert _cap(5.0, httpcore.ReadTimeout) == 5.0, "fuera de una llamada, el timeout es el suyo"
    with deadline_scope(time.monotonic() + 0.5):
        tope = _cap(5.0, httpcore.ReadTimeout)
        assert tope is not None and tope <= 0.5
    with deadline_scope(time.monotonic() - 1):
        with pytest.raises(httpcore.ReadTimeout):
            _cap(5.0, httpcore.ReadTimeout)


# ---------------------------------------------------------------------------
# La forma se valida ANTES de la colisión (P3-2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("malo", ["a b.travel", "x..travel", "a_b.guide"])
def test_un_nombre_mal_formado_bajo_una_colision_es_invalid_name(malo: str) -> None:
    resolver = NameResolver(rpc={})
    with pytest.raises(InvalidNameError):
        resolver.normalize(malo)
    assert resolver.resolve_sync(malo).error == "invalid_name"


def test_un_nombre_en_ancho_completo_bajo_una_colision_se_normaliza_y_sigue_ambiguo() -> None:
    ancho = chr(0xFF58) + ".travel"  # «ｘ.travel»
    resolver = NameResolver(rpc={})
    assert resolver.normalize(ancho) == "x.travel", "nunca el texto crudo en ancho completo"
    assert resolver.resolve_sync(ancho).error == "unsupported_system"
