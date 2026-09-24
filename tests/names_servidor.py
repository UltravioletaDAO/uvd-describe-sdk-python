"""Un servidor HTTP/1.1 LOCAL (127.0.0.1) que gotea lo que se le pida.

Para los tests del deadline: lo que se prueba es que el presupuesto de una
llamada corta de verdad cuando un servidor tarda, y un doble en memoria
(`MockTransport`) no tiene sockets ni lecturas que tarden. El servidor vive en
este proceso y en loopback: no sale a ninguna red (y `conftest.py` lo verifica).

`sin_proxies`: `names` respeta los proxies del entorno como `DescribeClient`
(ronda 4 del PR #6), así que estos tests los sacan del entorno para que un
`HTTPS_PROXY` de la corrida no desvíe una conexión que tiene que ir al servidor.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Iterator, List

import pytest

GOTEO = 0.2
_PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

#: gzip: ID1 ID2 CM=8 FLG=0x10 (FCOMMENT) MTIME(4) XFL OS — y después el
#: comentario, que nunca termina. zlib no produce un solo byte decodificado.
_GZIP_FCOMMENT = bytes([0x1F, 0x8B, 8, 0x10, 0, 0, 0, 0, 0, 255])

Responder = Callable[[socket.socket, bytes], None]


class ServidorLocal:
    """Acepta conexiones en 127.0.0.1 y contesta cada una con `responder`."""

    def __init__(self, responder: Responder) -> None:
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


def headers_goteando(conn: socket.socket, _: bytes) -> None:
    """Caso C: el status line y los headers, de a un byte."""
    _gotea(
        conn, b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
    )


def cuerpo_goteando(conn: socket.socket, _: bytes) -> None:
    """Casos A/B: headers enseguida, el cuerpo de a un byte."""
    cuerpo = b'{"jsonrpc": "2.0", "id": 1, "result": "0x' + b"00" * 32 + b'"}'
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(cuerpo)).encode()
        + b"\r\n\r\n"
    )
    _gotea(conn, cuerpo)


def gzip_goteando(conn: socket.socket, _: bytes) -> None:
    """Caso D: un gzip con FLG=FCOMMENT cuyo comentario gotea sin terminar."""
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nConnection: close\r\n\r\n")
    conn.sendall(_GZIP_FCOMMENT)
    _gotea(conn, b"a" * 200)


@pytest.fixture
def servidor(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Responder], ServidorLocal]]:
    for variable in _PROXIES:
        monkeypatch.delenv(variable, raising=False)
    abiertos: List[ServidorLocal] = []

    def crear(responder: Responder) -> ServidorLocal:
        nuevo = ServidorLocal(responder)
        abiertos.append(nuevo)
        return nuevo

    yield crear
    for abierto in abiertos:
        abierto.cerrar()


@pytest.fixture
def sin_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in _PROXIES:
        monkeypatch.delenv(variable, raising=False)


def cronometrar(correr: Callable[[], object]) -> float:
    inicio = time.monotonic()
    correr()
    return time.monotonic() - inicio
