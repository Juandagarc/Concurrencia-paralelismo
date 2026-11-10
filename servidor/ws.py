"""WebSocket mínimo (RFC 6455) con la biblioteca estándar.

Lo usan los nodos que se conectan desde el navegador: cada mensaje es un
texto JSON. No se ejecuta nada de lo que llega (no hay pickle): el servidor
solo acepta un puñado de tipos de mensaje y valida cada campo.

Incluye también un cliente pequeño, que usan las pruebas automáticas.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
from urllib.parse import urlsplit

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MENSAJE = 64 * 1024          # un mensaje del protocolo nunca necesita más


class Cerrada(ConnectionError):
    """El otro lado cerró la conexión."""


def _aceptacion(clave: str) -> str:
    return base64.b64encode(hashlib.sha1((clave + GUID).encode()).digest()).decode()


def aceptar(manejador, espera: float = 10.0) -> "Conexion | None":
    """Convierte una petición HTTP GET en un WebSocket (respuesta 101)."""
    h = manejador.headers
    clave = h.get("Sec-WebSocket-Key")
    if "websocket" not in (h.get("Upgrade") or "").lower() or not clave:
        return None
    manejador.wfile.write((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {_aceptacion(clave)}\r\n\r\n").encode())
    manejador.wfile.flush()
    manejador.close_connection = True
    manejador.connection.settimeout(espera)   # sin datos en `espera` s: conexión muerta
    return Conexion(manejador.connection, manejador.rfile)


def conectar(url: str, espera: float = 10.0) -> "Conexion":
    """Cliente: abre ws://host:puerto/ruta (lo usan las pruebas)."""
    u = urlsplit(url)
    s = socket.create_connection((u.hostname, u.port or 80), timeout=espera)
    clave = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {clave}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    rfile = s.makefile("rb")
    estado = rfile.readline()
    if b" 101 " not in estado:
        raise ConnectionError(f"El servidor no aceptó el WebSocket: {estado!r}")
    while rfile.readline() not in (b"\r\n", b""):
        pass
    return Conexion(s, rfile, cliente=True)


class Conexion:
    def __init__(self, sock: socket.socket, rfile, cliente: bool = False) -> None:
        self.sock, self.rfile, self.cliente = sock, rfile, cliente
        self._enviando = threading.Lock()     # varios hilos pueden enviar a la vez
        self.abierta = True

    # ------------------------------------------------------------ lectura
    def _leer(self, n: int) -> bytes:
        datos = self.rfile.read(n)
        if datos is None or len(datos) < n:
            raise Cerrada("conexión cerrada")
        return datos

    def recibir(self) -> str:
        """Devuelve el próximo mensaje de texto (responde los ping solo)."""
        partes: list[bytes] = []
        while True:
            b1, b2 = self._leer(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            enmascarado, largo = b2 & 0x80, b2 & 0x7F
            if largo == 126:
                largo = struct.unpack(">H", self._leer(2))[0]
            elif largo == 127:
                largo = struct.unpack(">Q", self._leer(8))[0]
            if largo > MAX_MENSAJE or sum(map(len, partes)) + largo > MAX_MENSAJE:
                self.cerrar(1009)
                raise Cerrada("mensaje demasiado grande")
            mascara = self._leer(4) if enmascarado else None
            datos = self._leer(largo) if largo else b""
            if mascara:
                datos = bytes(b ^ mascara[i % 4] for i, b in enumerate(datos))
            if opcode == 0x8:                      # cierre
                self.cerrar()
                raise Cerrada("el otro lado cerró")
            if opcode == 0x9:                      # ping -> pong
                self._enviar_marco(0xA, datos)
                continue
            if opcode == 0xA:                      # pong
                continue
            if opcode in (0x1, 0x0):               # texto o continuación
                partes.append(datos)
                if fin:
                    return b"".join(partes).decode("utf-8")
                continue
            self.cerrar(1003)                      # binario u otro: no se usa
            raise Cerrada("tipo de mensaje no soportado")

    def recibir_json(self) -> dict:
        datos = json.loads(self.recibir())
        if not isinstance(datos, dict):
            raise ValueError("se esperaba un objeto JSON")
        return datos

    # ------------------------------------------------------------- envío
    def _enviar_marco(self, opcode: int, datos: bytes) -> None:
        cab = bytes([0x80 | opcode])
        m = 0x80 if self.cliente else 0     # el cliente siempre enmascara (RFC 6455)
        n = len(datos)
        if n < 126:
            cab += bytes([m | n])
        elif n < 65536:
            cab += bytes([m | 126]) + struct.pack(">H", n)
        else:
            cab += bytes([m | 127]) + struct.pack(">Q", n)
        if self.cliente:
            mascara = os.urandom(4)
            datos = bytes(b ^ mascara[i % 4] for i, b in enumerate(datos))
            cab += mascara
        with self._enviando:
            self.sock.sendall(cab + datos)

    def enviar(self, texto: str) -> None:
        self._enviar_marco(0x1, texto.encode("utf-8"))

    def enviar_json(self, datos: dict) -> bool:
        """Envía sin lanzar error: si la conexión ya murió, devuelve False."""
        if not self.abierta:
            return False
        try:
            self.enviar(json.dumps(datos, ensure_ascii=False))
            return True
        except OSError:
            self.abierta = False
            return False

    def cerrar(self, codigo: int = 1000) -> None:
        if not self.abierta:
            return
        self.abierta = False
        try:
            self._enviar_marco(0x8, struct.pack(">H", codigo))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
