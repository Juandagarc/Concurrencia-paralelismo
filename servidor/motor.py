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

# Duración (s) de las etapas del modo "tiempo real" (cafetería completa).
# La etapa "preparar" usa la duración propia de cada solicitud.
# Están pensadas para que los meseros de la visualización caminen a un paso natural.
ETAPAS = {
    "pedido": 1.8,        # ir a la mesa, tomar el pedido y llevarlo (E/S: recibir la solicitud)
    "camino_caja": 0.8,   # llevar la cuenta a la caja (E/S)
    "cobro": 0.5,         # usar la caja: sección crítica protegida con un Lock
    "entrega": 1.6,       # llevar la taza a la mesa (E/S: enviar la respuesta)
}

# "spawn" funciona igual en Windows, macOS y Linux, y evita copiar los hilos
# del servidor web al crear procesos. Crear procesos así tiene un costo de
# arranque real, que se mide y se muestra.
_CONTEXTO = mp.get_context("spawn")


def topologia_cpu() -> tuple[list[list[int]], bool]:
    """Agrupa los núcleos lógicos por núcleo físico.

    Devuelve (grupos, exacta). Cada grupo es un núcleo físico con sus núcleos
    lógicos (hilos de hardware, p. ej. Hyper-Threading). En Linux se lee la
    topología real; en otros sistemas se asume la numeración habitual de
    Windows (los lógicos de un mismo núcleo físico son consecutivos).
    """
    n = os.cpu_count() or 1
    grupos: dict[str, list[int]] = {}
    try:
        for i in range(n):
            with open(f"/sys/devices/system/cpu/cpu{i}/topology/thread_siblings_list",
                      encoding="ascii") as f:
                grupos.setdefault(f.read().strip(), []).append(i)
        lista = sorted(grupos.values(), key=lambda g: g[0])
        if sum(len(g) for g in lista) == n:
            return lista, True
    except OSError:
        pass
    fisicos = psutil.cpu_count(logical=False) if psutil else None
    if not fisicos or n % fisicos:
        fisicos = n
    k = n // fisicos
    return [list(range(i * k, (i + 1) * k)) for i in range(fisicos)], False


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
    llegada = {e["id"]: e["t"] for e in eventos if e["tipo"] == "llegada"}
    inicio = {e["id"]: e for e in eventos if e["tipo"] == "inicio"}
    fin = {e["id"]: e for e in eventos if e["tipo"] == "fin"}
    filas = []
    for s in solicitudes:
        i, f = inicio[s["id"]], fin[s["id"]]
        lleg = llegada.get(s["id"], 0.0)
        filas.append({
            "id": s["id"],
            "usuario": s["usuario"],
            "trabajo": s["trabajo"],
            "trabajador": i["trabajador"],
            "pid": i["pid"],
            "llegada": lleg,
            "espera": round(i["t"] - lleg, 4),      # en cola desde que llegó hasta que la toman
            "servicio": round(f["t"] - i["t"], 4),
            "respuesta": round(f["t"] - lleg, 4),   # desde que llegó hasta que recibe la respuesta
        })
    nucleos = {e["nucleo"] for e in eventos
               if e.get("nucleo") is not None and e["tipo"] in ("inicio", "progreso")}
    return {
        "modo": modo,
        "trabajadores": trabajadores,
        "total": round(total, 4),
        "arranque": round(min(r["espera"] for r in filas), 4),
        "espera_promedio": round(sum(r["espera"] for r in filas) / len(filas), 4),
        "respuesta_promedio": round(sum(r["respuesta"] for r in filas) / len(filas), 4),
        "respuesta_max": round(max(r["respuesta"] for r in filas), 4),
        "trabajadores_usados": len({r["trabajador"] for r in filas}),
        "procesos_usados": len({r["pid"] for r in filas}),
        "nucleos_usados": sorted(nucleos),
        "solicitudes": filas,
    }


def ejecutar(modo: str, solicitudes: list[dict], trabajadores: int = 4,
             al_evento: Callable[[dict], None] | None = None,
             muestrear_cpu: bool = False, realista: bool = False,
             etapas: dict | None = None) -> dict:
    """Atiende `solicitudes` con el modelo `modo` y devuelve el resumen.

    al_evento: se llama en el proceso principal con cada evento real, en orden
               de llegada (lo usa el servidor web para transmitirlos en vivo).
    realista:  modo "tiempo real": cada solicitud pasa por pedido, preparar,
               caja (un único Lock compartido) y entrega. Ver trabajo.atender.
    etapas:    duraciones propias para el modo tiempo real (por defecto ETAPAS).
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

    # La única caja de la cafetería: un recurso compartido que solo un
    # trabajador puede usar a la vez. Entre procesos tiene que ser un Lock
    # gestionado, porque los procesos no comparten memoria.
    caja = None
    etapas = dict(etapas or ETAPAS) if realista else None
    if realista:
        caja = gestor.Lock() if gestor else threading.Lock()

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
        "realista": realista, "etapas": etapas,
    })

    def recibir(s: dict) -> None:
        """Espera la hora de llegada del cliente y registra su llegada real."""
        falta = s.get("llegada", 0.0) - (time.time() - t0)
        if falta > 0:
            time.sleep(falta)
        cola.put({"tipo": "llegada", "id": s["id"], "t": round(time.time() - t0, 4),
                  "usuario": s["usuario"], "trabajo": s["trabajo"]})

    orden = sorted(solicitudes, key=lambda s: (s.get("llegada", 0.0), s["id"]))

    detener = threading.Event()
    muestreador = None
    if muestrear_cpu and psutil is not None:
        muestreador = threading.Thread(
            target=_muestrear_cpu, args=(detener, cola.put, t0), daemon=True)
        muestreador.start()

    try:
        if modo == "secuencial":
            # Un hilo "recepcionista" anota las llegadas; el único trabajador
            # (este hilo) atiende la fila en orden, una solicitud tras otra.
            fila: queue.Queue = queue.Queue()
            recepcion = threading.Thread(
                target=lambda: [recibir(s) or fila.put(s) for s in orden],
                name="recepcion", daemon=True)
            recepcion.start()
            for _ in orden:
                atender(fila.get(), cola, t0, caja, etapas)
            recepcion.join()
        else:
            Pool = ThreadPoolExecutor if modo == "concurrente" else ProcessPoolExecutor
            opciones = ({"thread_name_prefix": "Hilo"} if modo == "concurrente"
                        else {"mp_context": _CONTEXTO})
            with Pool(max_workers=trabajadores, **opciones) as pool:
                futuros = []
                for s in orden:            # cada solicitud entra al pool cuando llega
                    recibir(s)
                    futuros.append(pool.submit(atender, s, cola, t0, caja, etapas))
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

    return resumir(modo, trabajadores, solicitudes, eventos, total) | {
        "realista": realista, "eventos": eventos}
