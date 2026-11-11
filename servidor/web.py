"""Servidor web del laboratorio (solo biblioteca estándar).

- Sirve la visualización 3D de la carpeta web/.
- POST /api/ejecutar lanza una ejecución real del simulador.
- GET  /api/eventos transmite cada evento en vivo (Server-Sent Events).
- GET  /ws/nodo es el WebSocket por el que un navegador se suma como nodo.
- Cada ejecución se guarda en resultados/ejecuciones/ como evidencia.
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import platform
import queue
import secrets
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import distribuido, motor, ws
from .solicitudes import ESCENARIOS, LLEGADAS, generar
from .trabajo import calibrar, nucleo_actual

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
EJECUCIONES = RAIZ / "resultados" / "ejecuciones"
MAX_GUARDADAS = 300        # en un servidor público no se llena el disco
# Versión web: trabajo de CPU fijo, medido en iteraciones del mismo cálculo en
# JavaScript. A 15 millones por segundo (un portátil promedio) un pedido de 1 s
# tarda 1 s; un computador más rápido lo termina antes y un celular, después.
REFERENCIA_JS = 15_000_000
MODOS_RED = ("secuencial", "concurrente", "paralelo")

mimetypes.add_type("text/javascript", ".js")


class Laboratorio:
    """Estado compartido: clientes conectados, ejecución en curso e historial."""

    def __init__(self, red: bool = False, clave: str | None = None) -> None:
        self.red = red
        self.clave = clave            # contraseña del coordinador (solo en la versión web)
        self.sesiones: set[str] = set()
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
            "topologia": motor.topologia_cpu()[0],
            "topologia_exacta": motor.topologia_cpu()[1],
            "uso_cpu_disponible": con_psutil,
            "ocupado": self.ocupado.locked(),
            "etapas": motor.ETAPAS,
            "coordinador": distribuido.obtener().info(),
            "red": self.red,
            "referencia_js": REFERENCIA_JS,
        }

    # --- coordinador (versión web) ---------------------------------------
    def entrar(self, clave: str) -> str | None:
        if not self.clave or not hmac.compare_digest(str(clave).encode(), self.clave.encode()):
            time.sleep(1.0)           # frena a quien intente adivinar
            return None
        sesion = secrets.token_urlsafe(24)
        self.sesiones.add(sesion)
        return sesion

    def autorizado(self, sesion: str | None) -> bool:
        return not self.red or (sesion is not None and sesion in self.sesiones)

    def lanzar(self, params: dict) -> dict:
        modo = params.get("modo", "secuencial")
        escenario = params.get("escenario", "io")
        modos = MODOS_RED if self.red else motor.MODOS
        if modo not in modos or escenario not in ESCENARIOS:
            raise ValueError("Modo o escenario no válido")
        trabajadores = max(1, min(int(params.get("trabajadores", 4)), 8))
        duracion = max(0.1, min(float(params.get("duracion", 1.0)), 5.0))
        realista = bool(params.get("realista", False))
        latencia = max(0.0, min(float(params.get("latencia", 30)), 300.0)) / 1000
        caida = bool(params.get("caida", False))
        llegadas = params.get("llegadas", "juntas")
        if llegadas not in LLEGADAS:
            raise ValueError("Tipo de llegada no válido")
        if not self.ocupado.acquire(blocking=False):
            raise RuntimeError("Ya hay una ejecución en curso")
        hilo = threading.Thread(
            target=self._correr,
            args=(modo, escenario, trabajadores, duracion, realista, llegadas, latencia, caida),
            # En modo secuencial este hilo es el único que atiende solicitudes
            name="Hilo principal", daemon=True)
        hilo.start()
        return {"modo": modo, "escenario": escenario, "trabajadores": trabajadores,
                "duracion": duracion, "realista": realista, "llegadas": llegadas,
                "latencia": latencia, "caida": caida}

    def _correr(self, modo: str, escenario: str, trabajadores: int, duracion: float,
                realista: bool, llegadas: str, latencia: float, caida: bool) -> None:
        try:
            ops = REFERENCIA_JS if self.red else self.ops_por_seg
            lote = generar(escenario, duracion, ops, llegadas=llegadas)

            def al_evento(ev: dict) -> None:
                if ev["tipo"] == "ejecucion_inicio":
                    ev = ev | {"escenario": escenario, "duracion": duracion, "llegadas": llegadas}
                self.difundir(ev)

            r = motor.ejecutar(modo, lote, trabajadores, al_evento=al_evento,
                               muestrear_cpu=True, realista=realista,
                               latencia=latencia, caida=caida, red=self.red)
            eventos = r.pop("eventos")
            r |= {"escenario": escenario, "duracion": duracion, "llegadas": llegadas,
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
        for viejo in sorted(EJECUCIONES.glob("*.json"))[:-MAX_GUARDADAS]:
            viejo.unlink(missing_ok=True)


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
            if ruta == "/ws/nodo":
                canal = ws.aceptar(self)
                if canal is None:
                    return self._json({"error": "Se esperaba un WebSocket"}, HTTPStatus.BAD_REQUEST)
                return distribuido.obtener().atender_navegador(canal)
            return self._estatico(ruta)

        def do_POST(self):
            if self.path not in ("/api/ejecutar", "/api/entrar"):
                return self._json({"error": "No encontrado"}, HTTPStatus.NOT_FOUND)
            try:
                largo = min(int(self.headers.get("Content-Length", 0)), 16_384)
                params = json.loads(self.rfile.read(largo) or b"{}")
                if not isinstance(params, dict):
                    raise ValueError("Se esperaba un objeto JSON")
                if self.path == "/api/entrar":
                    sesion = lab.entrar(params.get("clave", ""))
                    if sesion is None:
                        return self._json({"error": "Contraseña incorrecta"}, HTTPStatus.UNAUTHORIZED)
                    return self._json({"sesion": sesion})
                if not lab.autorizado(self.headers.get("X-Sesion")):
                    return self._json({"error": "Solo el coordinador puede ejecutar"},
                                      HTTPStatus.UNAUTHORIZED)
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


def iniciar(host: str = "127.0.0.1", puerto: int = 8000, nodos_lan: bool = False,
            red: bool = False) -> None:
    print("Calibrando la CPU de este equipo…", flush=True)
    # El puerto TCP del coordinador (pickle con clave) solo escucha en este equipo,
    # salvo que se pida --nodos-lan para sumar nodos de Python de la red local.
    # Los navegadores entran siempre por el WebSocket de la web, que es seguro.
    coordinador = distribuido.obtener("0.0.0.0" if nodos_lan else "127.0.0.1")
    clave = None
    if red:
        # La contraseña nunca va en el repositorio (es público): se lee del entorno
        clave = os.environ.get("COORDINADOR_CLAVE") or secrets.token_urlsafe(9)
        if not os.environ.get("COORDINADOR_CLAVE"):
            print(f"  COORDINADOR_CLAVE no está definida: contraseña temporal {clave}")
    lab = Laboratorio(red, clave)
    coordinador.al_cambio = lambda info: lab.difundir({"tipo": "red", **info})
    servidor, puerto = _abrir(host, puerto, crear_manejador(lab))
    servidor.daemon_threads = True
    info = lab.info()
    print(f"  {info['nucleos']} núcleos · Python {info['python']} · "
          f"GIL {'activo' if info['gil'] else 'desactivado'} · "
          f"{lab.ops_por_seg:,} iteraciones/s")
    print(f"  Nodos del navegador: abre http://{host}:{puerto}/nodo.html en cualquier equipo")
    if nodos_lan:
        print("  Nodos de Python en la red local:\n"
              f"    python -m servidor.nodo --coordinador {coordinador.direccion_red()} "
              f"--clave {coordinador.clave}")
    print(f"\nLaboratorio listo en  http://{host}:{puerto}\n(Ctrl+C para salir)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
    finally:
        servidor.server_close()
        time.sleep(0.1)
