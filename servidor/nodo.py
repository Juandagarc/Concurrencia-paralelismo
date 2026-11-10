"""Nodo trabajador del modo distribuido.

Un nodo es un programa aparte (otro proceso, y puede correr en otro computador)
que no comparte memoria con nadie. Todo lo que sabe le llega por la red:

    1. Se conecta por TCP al coordinador, con una clave compartida.
    2. Sincroniza su reloj con el del coordinador (algoritmo de Cristian).
    3. Se registra y envía latidos (heartbeats) para avisar que sigue vivo.
    4. Recibe solicitudes en su buzón, las atiende con la misma función de
       siempre (trabajo.atender) y devuelve cada evento al coordinador.
    5. Para usar la caja pide el candado al coordinador (Lock distribuido).

El servidor web lanza estos nodos en cada ejecución. El público se suma desde
el navegador (web/nodo-trabajador.js), sin instalar nada. Para sumar un nodo de
Python de la red local (el servidor debe iniciarse con --host 0.0.0.0 --nodos-lan):

    python -m servidor.nodo --coordinador 192.168.1.10:50500 --clave <clave>

La clave y la dirección exactas se imprimen al iniciar  python -m servidor.
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from multiprocessing.managers import BaseManager

from .trabajo import atender

LATIDO = 0.25          # cada cuánto avisa "sigo vivo" (s)


class _Cliente(BaseManager):
    """Lado cliente: los mismos nombres que publica el coordinador."""


for _nombre in ("control", "entrada", "candado", "buzon"):
    _Cliente.register(_nombre)


def conectar(direccion: tuple[str, int], clave: str, intentos: int = 50) -> _Cliente:
    ultimo = None
    for _ in range(intentos):
        m = _Cliente(address=direccion, authkey=clave.encode())
        try:
            m.connect()
            return m
        except OSError as e:          # el coordinador todavía no escucha: reintenta
            ultimo = e
            time.sleep(0.1)
    raise SystemExit(f"No se pudo conectar al coordinador {direccion}: {ultimo}")


def sincronizar(control, desfase: float, muestras: int = 5) -> tuple[float, float]:
    """Algoritmo de Cristian: pide la hora al coordinador varias veces.

    ajuste = hora_coordinador + RTT/2 - hora_local. Se usa la muestra con el
    menor RTT (ida y vuelta), porque es la de menor error (± RTT/2).
    `desfase` simula un reloj local adelantado o atrasado.
    """
    mejor = None
    for _ in range(muestras):
        t1 = time.time() + desfase
        hora = control.hora()
        t2 = time.time() + desfase
        rtt = t2 - t1
        if mejor is None or rtt < mejor[0]:
            mejor = (rtt, hora + rtt / 2 - t2)
    return mejor


class CajaRemota:
    """La caja vive en el coordinador: tomarla y soltarla son mensajes por la red.

    Es exclusión mutua centralizada: el coordinador presta el candado a un nodo
    a la vez, en el orden en que llegan las peticiones.
    """

    def __init__(self, candado, nodo_id: int, latencia: float) -> None:
        self.candado, self.nodo_id, self.latencia = candado, nodo_id, latencia

    def __enter__(self):
        time.sleep(self.latencia)          # la petición viaja hasta el coordinador
        self.candado.tomar(self.nodo_id)   # bloquea hasta que el coordinador se lo presta
        return self

    def __exit__(self, *_):
        self.candado.soltar(self.nodo_id)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Nodo trabajador del modo distribuido")
    p.add_argument("--coordinador", required=True, help="host:puerto del coordinador")
    p.add_argument("--clave", default=os.environ.get("LAB_CLAVE"),
                   help="clave compartida (la imprime el servidor al iniciar)")
    p.add_argument("--indice", type=int, default=None,
                   help="uso interno: número del nodo local lanzado por el coordinador")
    p.add_argument("--ej", type=int, default=None, help="uso interno: ejecución")
    p.add_argument("--desfase", type=float, default=0.0,
                   help="simula un reloj local desfasado (segundos)")
    a = p.parse_args(argv)
    if not a.clave:
        raise SystemExit("Falta la clave: --clave <clave> (la imprime el servidor al iniciar)")

    host, puerto = a.coordinador.rsplit(":", 1)
    m = conectar((host, int(puerto)), a.clave)
    control = m.control()
    rtt, ajuste = sincronizar(control, a.desfase)
    alta = control.registrar({
        "indice": a.indice, "ej": a.ej, "host": socket.gethostname(),
        "pid": os.getpid(), "nucleos": os.cpu_count(),
        "rtt": rtt, "ajuste": ajuste, "desfase": a.desfase,
    })
    nid, nombre = alta["id"], alta["nombre"]
    buzon, entrada, candado = m.buzon(nid), m.entrada(), m.candado()
    if a.indice is None:
        print(f"{nombre} conectado a {a.coordinador} · RTT {rtt * 1000:.2f} ms · "
              f"ajuste de reloj {ajuste * 1000:+.1f} ms. Esperando solicitudes…", flush=True)

    detener = threading.Event()

    def latir() -> None:
        while not detener.wait(LATIDO):
            try:
                control.latido(nid)
            except (OSError, EOFError):
                return

    threading.Thread(target=latir, name="latidos", daemon=True).start()

    try:
        while True:
            msg = buzon.get()
            if msg.get("cerrar"):
                break
            lat = msg.get("latencia", 0.0)
            time.sleep(lat)                # la solicitud viaja por la red hasta este nodo
            etapas = msg.get("etapas")
            atender(
                msg["solicitud"], entrada,
                # t0 del coordinador expresado en el reloj de este nodo (ya corregido)
                msg["t0"] - ajuste - a.desfase,
                CajaRemota(candado, nid, lat) if etapas else None, etapas,
                trabajador=nombre,
                marca={"nodo": nombre, "nodo_id": nid, "nodo_idx": msg["nodo_idx"],
                       "ej": msg["ej"], "host": socket.gethostname(),
                       "remoto": a.indice is None},
                latencia=lat,
            )
    except (OSError, EOFError, KeyboardInterrupt):
        pass                               # se perdió el coordinador o se pidió salir
    finally:
        detener.set()


if __name__ == "__main__":
    main()
