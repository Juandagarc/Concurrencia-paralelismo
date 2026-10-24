"""Trabajo que realiza el servidor para atender una solicitud.

Este módulo debe poder importarse desde procesos hijos (ProcessPoolExecutor
usa el método "spawn"), por eso solo contiene funciones de nivel superior.

Cada solicitud se procesa en PASOS partes; después de cada parte se emite un
evento real con la hora, el proceso, el hilo y el núcleo de CPU que la atiende.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time

PASOS = 10


def nucleo_actual() -> int | None:
    """Núcleo de CPU donde corre el hilo actual (solo Linux, vía /proc).

    En Windows y macOS no existe una forma estándar de leerlo; se devuelve None.
    """
    try:
        tid = threading.get_native_id()
        with open(f"/proc/self/task/{tid}/stat", encoding="ascii") as f:
            datos = f.read()
        # El campo 39 de stat es el procesador. Tras el nombre "(comm)"
        # el primer campo es el 3, así que el 39 queda en el índice 36.
        campos = datos.rsplit(")", 1)[1].split()
        return int(campos[36])
    except (OSError, IndexError, ValueError):
        return None


def nombre_trabajador() -> str:
    """Etiqueta legible de quién atiende: proceso hijo o hilo."""
    if mp.current_process().name != "MainProcess":
        return f"Proceso {os.getpid()}"
    nombre = threading.current_thread().name
    return "Hilo principal" if nombre == "MainThread" else nombre


def quemar_cpu(iteraciones: int) -> int:
    """Cálculo puro en Python: ocupa la CPU y retiene el GIL."""
    x = 0
    for i in range(iteraciones):
        x += (i * i) % 7
    return x


def calibrar(muestra: int = 300_000, intentos: int = 3) -> int:
    """Iteraciones de quemar_cpu por segundo en este equipo (un solo hilo)."""
    mejor = 0.0
    for _ in range(intentos):
        t = time.perf_counter()
        quemar_cpu(muestra)
        dt = time.perf_counter() - t
        mejor = max(mejor, muestra / dt)
    return int(mejor)


def atender(solicitud: dict, cola, t0: float) -> dict:
    """Procesa una solicitud y emite sus eventos a `cola`.

    `cola` es una queue.Queue (secuencial / hilos) o una cola de Manager
    (procesos). `t0` es la hora (time.time) en que arrancó la ejecución, común
    a todos los procesos, para que los tiempos sean comparables.
    """
    pid = os.getpid()
    tid = threading.get_native_id()
    trabajador = nombre_trabajador()

    def emitir(tipo: str, **extra) -> None:
        cola.put({
            "tipo": tipo,
            "id": solicitud["id"],
            "t": round(time.time() - t0, 4),
            "trabajador": trabajador,
            "pid": pid,
            "tid": tid,
            "nucleo": nucleo_actual(),
            **extra,
        })

    emitir("inicio", trabajo=solicitud["trabajo"])
    for paso in range(1, PASOS + 1):
        if solicitud["trabajo"] == "io":
            # Simula esperar a una base de datos o a otro servicio:
            # el hilo se bloquea y libera el GIL y la CPU.
            time.sleep(solicitud["duracion"] / PASOS)
        else:
            # Simula un cálculo pesado: la CPU trabaja todo el tiempo.
            quemar_cpu(solicitud["iteraciones"] // PASOS)
        emitir("progreso", progreso=paso / PASOS)
    emitir("fin")
    return {"id": solicitud["id"], "trabajador": trabajador, "pid": pid}
