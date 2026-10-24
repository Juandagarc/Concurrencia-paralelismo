"""Los tres modelos de ejecución del servidor.

    secuencial  -> el hilo principal atiende una solicitud tras otra.
    concurrente -> un pool de hilos dentro del mismo proceso (ThreadPoolExecutor).
    paralelo    -> un pool de procesos, uno por núcleo (ProcessPoolExecutor).

Las tres versiones usan exactamente la misma función de trabajo
(`trabajo.atender`) y las mismas solicitudes; solo cambia cómo se ejecutan.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Callable

from .trabajo import atender

try:  # psutil es opcional: permite mostrar el uso real de cada núcleo
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

MODOS = ("secuencial", "concurrente", "paralelo")

# "spawn" funciona igual en Windows, macOS y Linux, y evita copiar los hilos
# del servidor web al crear procesos. Crear procesos así tiene un costo de
# arranque real, que se mide y se muestra.
_CONTEXTO = mp.get_context("spawn")


def gil_activo() -> bool:
    """False solo en la compilación experimental sin GIL (python3.13t)."""
    fn = getattr(sys, "_is_gil_enabled", None)
    return True if fn is None else bool(fn())


def _muestrear_cpu(detener: threading.Event, emitir: Callable, t0: float) -> None:
    """Emite el uso real (%) de cada núcleo cada 200 ms."""
    psutil.cpu_percent(percpu=True)  # la primera lectura siempre es 0
    while not detener.wait(0.2):
        emitir({
            "tipo": "cpu",
            "t": round(time.time() - t0, 4),
            "uso": psutil.cpu_percent(percpu=True),
        })


def resumir(modo: str, trabajadores: int, solicitudes: list[dict],
            eventos: list[dict], total: float) -> dict:
    """Calcula las métricas de una ejecución a partir de sus eventos reales."""
    inicio = {e["id"]: e for e in eventos if e["tipo"] == "inicio"}
    fin = {e["id"]: e for e in eventos if e["tipo"] == "fin"}
    filas = []
    for s in solicitudes:
        i, f = inicio[s["id"]], fin[s["id"]]
        filas.append({
            "id": s["id"],
            "usuario": s["usuario"],
            "trabajo": s["trabajo"],
            "trabajador": i["trabajador"],
            "pid": i["pid"],
            "espera": i["t"],              # tiempo en cola antes de ser atendida
            "servicio": round(f["t"] - i["t"], 4),
            "respuesta": f["t"],           # todas llegan en t = 0
        })
    nucleos = {e["nucleo"] for e in eventos
               if e.get("nucleo") is not None and e["tipo"] in ("inicio", "progreso")}
    return {
        "modo": modo,
        "trabajadores": trabajadores,
        "total": round(total, 4),
        "arranque": round(min(r["espera"] for r in filas), 4),
        "respuesta_promedio": round(sum(r["respuesta"] for r in filas) / len(filas), 4),
        "respuesta_max": round(max(r["respuesta"] for r in filas), 4),
        "trabajadores_usados": len({r["trabajador"] for r in filas}),
        "procesos_usados": len({r["pid"] for r in filas}),
        "nucleos_usados": sorted(nucleos),
        "solicitudes": filas,
    }


def ejecutar(modo: str, solicitudes: list[dict], trabajadores: int = 4,
             al_evento: Callable[[dict], None] | None = None,
             muestrear_cpu: bool = False) -> dict:
    """Atiende `solicitudes` con el modelo `modo` y devuelve el resumen.

    al_evento: se llama en el proceso principal con cada evento real, en orden
               de llegada (lo usa el servidor web para transmitirlos en vivo).
    """
    if modo not in MODOS:
        raise ValueError(f"Modo desconocido: {modo}")
    if modo == "secuencial":
        trabajadores = 1

    eventos: list[dict] = []
    gestor = None
    if modo == "paralelo":
        # Los procesos no comparten memoria: los eventos viajan por una cola
        # gestionada (IPC). Se crea antes de medir porque es parte del
        # instrumento de observación, no del modelo que se compara.
        gestor = _CONTEXTO.Manager()
        cola = gestor.Queue()
    else:
        cola = queue.Queue()

    def puente() -> None:
        while (ev := cola.get()) is not None:
            eventos.append(ev)
            if al_evento:
                al_evento(ev)

    hilo_puente = threading.Thread(target=puente, name="puente-eventos", daemon=True)
    hilo_puente.start()

    t0 = time.time()
    cola.put({
        "tipo": "ejecucion_inicio", "t": 0.0, "modo": modo,
        "trabajadores": trabajadores, "pid_servidor": os.getpid(),
        "nucleos": os.cpu_count(), "solicitudes": solicitudes,
    })
    for s in solicitudes:
        cola.put({"tipo": "llegada", "id": s["id"], "t": 0.0,
                  "usuario": s["usuario"], "trabajo": s["trabajo"]})

    detener = threading.Event()
    muestreador = None
    if muestrear_cpu and psutil is not None:
        muestreador = threading.Thread(
            target=_muestrear_cpu, args=(detener, cola.put, t0), daemon=True)
        muestreador.start()

    try:
        if modo == "secuencial":
            for s in solicitudes:
                atender(s, cola, t0)
        elif modo == "concurrente":
            with ThreadPoolExecutor(max_workers=trabajadores,
                                    thread_name_prefix="Hilo") as pool:
                list(pool.map(lambda s: atender(s, cola, t0), solicitudes))
        else:
            with ProcessPoolExecutor(max_workers=trabajadores,
                                     mp_context=_CONTEXTO) as pool:
                futuros = [pool.submit(atender, s, cola, t0) for s in solicitudes]
                for f in futuros:
                    f.result()
        total = time.time() - t0
    finally:
        detener.set()
        if muestreador:
            muestreador.join()

    cola.put({"tipo": "ejecucion_fin", "t": round(total, 4)})
    cola.put(None)
    hilo_puente.join()
    if gestor:
        gestor.shutdown()

    return resumir(modo, trabajadores, solicitudes, eventos, total) | {"eventos": eventos}
