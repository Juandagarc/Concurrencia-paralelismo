"""Servidor web del laboratorio (solo biblioteca estándar).

- Sirve la visualización 3D de la carpeta web/.
- POST /api/ejecutar lanza una ejecución real del simulador.
- GET  /api/eventos transmite cada evento en vivo (Server-Sent Events).
- Cada ejecución se guarda en resultados/ejecuciones/ como evidencia.
"""

from __future__ import annotations

import json
import mimetypes
import os
import platform
import queue
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import motor
from .solicitudes import ESCENARIOS, generar
from .trabajo import calibrar, nucleo_actual

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
EJECUCIONES = RAIZ / "resultados" / "ejecuciones"

mimetypes.add_type("text/javascript", ".js")


class Laboratorio:
    """Estado compartido: clientes conectados, ejecución en curso e historial."""

    def __init__(self) -> None:
        self.ops_por_seg = calibrar()
        self.clientes: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.ocupado = threading.Lock()
        self.historial: list[dict] = []

    # --- difusión de eventos -------------------------------------------
    def suscribir(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=5000)
        with self.lock:
            self.clientes.append(q)
        return q

    def desuscribir(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clientes:
                self.clientes.remove(q)

    def difundir(self, evento: dict) -> None:
        with self.lock:
            for q in self.clientes:
                try:
                    q.put_nowait(evento)
                except queue.Full:
                    pass

    # --- ejecuciones ----------------------------------------------------
    def info(self) -> dict:
        try:
            import psutil  # noqa: F401
            con_psutil = True
        except ImportError:
            con_psutil = False
        return {
            "nucleos": os.cpu_count(),
            "ops_por_seg": self.ops_por_seg,
            "plataforma": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "gil": motor.gil_activo(),
            "nucleo_detectable": nucleo_actual() is not None,
            "uso_cpu_disponible": con_psutil,
            "ocupado": self.ocupado.locked(),
            "etapas": motor.ETAPAS,
        }

    def lanzar(self, params: dict) -> dict:
        modo = params.get("modo", "secuencial")
        escenario = params.get("escenario", "io")
        if modo not in motor.MODOS or escenario not in ESCENARIOS:
            raise ValueError("Modo o escenario no válido")
        trabajadores = max(1, min(int(params.get("trabajadores", 4)), 32))
        duracion = max(0.1, min(float(params.get("duracion", 1.0)), 5.0))
        realista = bool(params.get("realista", False))
        if not self.ocupado.acquire(blocking=False):
            raise RuntimeError("Ya hay una ejecución en curso")
        hilo = threading.Thread(
            target=self._correr, args=(modo, escenario, trabajadores, duracion, realista),
            # En modo secuencial este hilo es el único que atiende solicitudes
            name="Hilo principal", daemon=True)
        hilo.start()
        return {"modo": modo, "escenario": escenario, "trabajadores": trabajadores,
                "duracion": duracion, "realista": realista}

    def _correr(self, modo: str, escenario: str, trabajadores: int, duracion: float,
                realista: bool) -> None:
        try:
            lote = generar(escenario, duracion, self.ops_por_seg)

            def al_evento(ev: dict) -> None:
                if ev["tipo"] == "ejecucion_inicio":
                    ev = ev | {"escenario": escenario, "duracion": duracion}
                self.difundir(ev)

            r = motor.ejecutar(modo, lote, trabajadores, al_evento=al_evento,
                               muestrear_cpu=True, realista=realista)
            eventos = r.pop("eventos")
            r |= {"escenario": escenario, "duracion": duracion,
                  "fecha": datetime.now().isoformat(timespec="seconds")}
            self.historial.append(r)
            self.difundir({"tipo": "resumen", **r})
            self._guardar(r, eventos)
        except Exception as e:  # se informa a la interfaz en vez de morir en silencio
            self.difundir({"tipo": "error", "mensaje": str(e)})
        finally:
            self.ocupado.release()

    @staticmethod
    def _guardar(resumen: dict, eventos: list[dict]) -> None:
        EJECUCIONES.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d-%H%M%S")
        sufijo = "_tiempo-real" if resumen.get("realista") else ""
        nombre = f"{marca}_{resumen['modo']}_{resumen['escenario']}{sufijo}.json"
        (EJECUCIONES / nombre).write_text(
            json.dumps({"resumen": resumen, "eventos": eventos},
                       ensure_ascii=False, indent=1),
            encoding="utf-8")


def crear_manejador(lab: Laboratorio):
    class Manejador(BaseHTTPRequestHandler):
        server_version = "LabConcurrencia/1.0"

        def log_message(self, fmt, *args):  # silenciar el log por petición
            pass

        def _json(self, datos, estado=HTTPStatus.OK):
            cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
            self.send_response(estado)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            ruta = self.path.split("?", 1)[0]
            if ruta == "/api/info":
                return self._json(lab.info())
            if ruta == "/api/historial":
                return self._json(lab.historial)
            if ruta == "/api/eventos":
                return self._sse()
            return self._estatico(ruta)

        def do_POST(self):
            if self.path != "/api/ejecutar":
                return self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)
            try:
                largo = int(self.headers.get("Content-Length", 0))
                params = json.loads(self.rfile.read(largo) or b"{}")
                return self._json(lab.lanzar(params), HTTPStatus.ACCEPTED)
            except RuntimeError as e:
                return self._json({"error": str(e)}, HTTPStatus.CONFLICT)
            except (ValueError, TypeError) as e:
                return self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)

        def _sse(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = lab.suscribir()
            try:
                self.wfile.write(b": conectado\n\n")
                self.wfile.flush()
                while True:
                    try:
                        ev = q.get(timeout=15)
                        datos = json.dumps(ev, ensure_ascii=False)
                        self.wfile.write(f"data: {datos}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": latido\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                lab.desuscribir(q)

        def _estatico(self, ruta: str):
            relativa = "index.html" if ruta in ("", "/") else ruta.lstrip("/")
            archivo = (WEB / relativa).resolve()
            if WEB not in archivo.parents or not archivo.is_file():
                return self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)
            tipo = mimetypes.guess_type(archivo.name)[0] or "application/octet-stream"
            cuerpo = archivo.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(cuerpo)

    return Manejador


def _abrir(host: str, puerto: int, manejador, intentos: int = 20):
    """Abre el servidor en `puerto`; si está ocupado o reservado, prueba los siguientes.

    En Windows es común que Hyper-V, WSL o Docker reserven rangos de puertos
    (WinError 10013), y que otro programa ya use el 8000 (WinError 10048).
    """
    for p in range(puerto, puerto + intentos):
        try:
            return ThreadingHTTPServer((host, p), manejador), p
        except OSError as e:
            print(f"  Puerto {p} no disponible ({e.strerror or e}); probando {p + 1}…")
    raise SystemExit(f"No se encontró un puerto libre entre {puerto} y {puerto + intentos - 1}. "
                     "Prueba con:  python -m servidor --puerto 5050")


def iniciar(host: str = "127.0.0.1", puerto: int = 8000) -> None:
    print("Calibrando la CPU de este equipo…", flush=True)
    lab = Laboratorio()
    servidor, puerto = _abrir(host, puerto, crear_manejador(lab))
    servidor.daemon_threads = True
    info = lab.info()
    print(f"  {info['nucleos']} núcleos · Python {info['python']} · "
          f"GIL {'activo' if info['gil'] else 'desactivado'} · "
          f"{lab.ops_por_seg:,} iteraciones/s")
    print(f"\nLaboratorio listo en  http://{host}:{puerto}\n(Ctrl+C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        servidor.server_close()
        time.sleep(0.1)
