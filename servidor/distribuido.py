"""Modo distribuido: un coordinador y varios nodos que solo se hablan por la red.

    Cliente (navegador) --HTTP--> Servidor web + COORDINADOR --TCP--> NODOS

El coordinador vive en el mismo proceso que el servidor web y cumple cinco papeles:

    1. Balanceador de carga: guarda las solicitudes que llegan y se las da al
       nodo libre que menos ha trabajado.
    2. Lock distribuido: la caja vive aquí; los nodos piden y devuelven el
       candado con mensajes (exclusión mutua centralizada, en orden FIFO).
    3. Reloj de referencia: los nodos le piden la hora para corregir el suyo
       (algoritmo de Cristian), porque en un sistema distribuido no hay un
       reloj común.
    4. Detector de fallos: cada nodo envía un latido cada 0,25 s. Si pasa
       LATIDO_MAX sin latido, el nodo se da por caído, se le revoca el candado
       y su solicitud se reasigna a otro nodo (tolerancia a fallos).
    5. Recolector de eventos: los nodos envían cada evento a la cola `entrada`;
       se descartan los de nodos caídos (un nodo "zombi" no puede responder dos veces).

Hay dos clases de nodos, y el coordinador los trata igual:

    Nodos del servidor (servidor/nodo.py): procesos aparte lanzados con
        subprocess en cada ejecución, sin memoria compartida. Hablan con el
        coordinador por multiprocessing.managers: cada llamada remota (put,
        get, tomar, latido…) es un mensaje por un socket TCP local con clave.
    Nodos del navegador (web/nodo-trabajador.js): cualquier persona abre
        /nodo.html y su equipo presta núcleos de CPU, un Web Worker por nodo.
        Hablan por WebSocket con mensajes JSON (servidor/ws.py) que el
        coordinador valida campo por campo: nada de lo que llega se ejecuta.
"""

from __future__ import annotations

import itertools
import os
import re
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from multiprocessing.managers import BaseManager
from pathlib import Path
from typing import Callable

RAIZ = Path(__file__).resolve().parent.parent
PUERTO = 50500
LATIDO_MAX = 2.5          # segundos sin latido para dar un nodo por caído. Con margen:
                          # un navegador en segundo plano puede atrasar algunos latidos
ESPERA_REGISTRO = 20.0    # segundos máximos para que arranque al menos un nodo
GRACIA = 30.0             # si caen todos los nodos, cuánto se espera a que alguno vuelva
SIN_AVANCE = 45.0         # si nadie avanza en este tiempo, la ejecución se cancela sola
MAX_NODOS = 30            # nodos (meseros) por ejecución
POLITICAS = ("distribuido", "secuencial", "concurrente", "paralelo")
# Cómo se coordinan los meseros en la versión web (siempre distribuida):
#   secuencial  -> anillo con testigo: un solo mesero trabaja a la vez y el
#                  turno pasa al siguiente (token ring).
#   concurrente -> todos atienden, pero para calcular hay que tener la única
#                  llave del sistema (un GIL distribuido): se turnan la CPU y
#                  solo las esperas se superponen.
#   paralelo    -> todos atienden y calculan a la vez, cada uno con su CPU.
#   distribuido -> como paralelo (el modo del laboratorio local).
MAX_NAVEGADORES = 64      # nodos del navegador conectados a la vez
TIPOS_NAVEGADOR = {"inicio", "progreso", "etapa", "fin"}
ETAPAS_VALIDAS = {"pedido", "preparar", "caja_camino", "caja_espera", "caja_cobro", "entrega"}

# Cada nodo local simula una red y un reloj distintos, para que se vean los
# problemas propios de un sistema distribuido. Los valores son fijos para que
# cada prueba sea repetible.
FACTOR_LATENCIA = (1.0, 0.6, 1.5, 0.8, 1.25, 0.7, 1.35, 0.9)
DESFASE_RELOJ = (0.0, 0.18, -0.12, 0.35, -0.27, 0.08, -0.40, 0.22)   # segundos


class CandadoDistribuido:
    """Exclusión mutua centralizada: el coordinador presta el candado a un nodo
    a la vez, en el orden en que llegaron las peticiones (FIFO)."""

    def __init__(self) -> None:
        self._c = threading.Condition()
        self.dueno: int | None = None
        self.fila: list[int] = []
        self.epoca = 0                # cambia en cada reinicio: las esperas viejas se anulan

    def tomar(self, nodo_id: int) -> bool:
        with self._c:
            epoca = self.epoca
            self.fila.append(nodo_id)
            while not (self.dueno is None and self.fila and self.fila[0] == nodo_id):
                if nodo_id not in self.fila or self.epoca != epoca:   # revocado o reiniciado
                    return False
                self._c.wait()
            self.fila.pop(0)
            self.dueno = nodo_id
            return True

    def soltar(self, nodo_id: int) -> None:
        with self._c:
            if self.dueno == nodo_id:
                self.dueno = None
                self._c.notify_all()

    def reiniciar(self) -> None:
        """Entre ejecuciones: nadie conserva el candado ni su lugar en la fila."""
        with self._c:
            self.dueno, self.fila = None, []
            self.epoca += 1
            self._c.notify_all()

    def revocar(self, nodo_id: int) -> None:
        """Si un nodo cae con el candado o en la fila, se lo quita para no bloquear a todos."""
        with self._c:
            self.fila = [n for n in self.fila if n != nodo_id]
            if self.dueno == nodo_id:
                self.dueno = None
            self._c.notify_all()


class Control:
    """Lo que un nodo puede pedirle al coordinador por la red."""

    def __init__(self, coordinador: "Coordinador") -> None:
        self._c = coordinador

    def hora(self) -> float:
        return time.time()

    def registrar(self, info: dict) -> dict:
        return self._c._registrar(info)

    def latido(self, nodo_id: int) -> None:
        self._c._latido(nodo_id)


@dataclass
class Nodo:
    id: int
    nombre: str
    info: dict
    local: bool
    ultimo: float
    vivo: bool = True
    idx: int | None = None          # puesto en la visualización durante la ejecución
    tarea: dict | None = None
    atendidas: int = 0
    canal: object = None            # WebSocket si es un nodo del navegador

    @property
    def navegador(self) -> bool:
        return self.canal is not None


@dataclass
class _Ejecucion:
    id: int
    t0: float
    emitir: Callable[[dict], None]
    latencia: float
    etapas: dict | None
    caida: bool
    politica: str = "distribuido"
    turno: int = -1                 # secuencial: índice del último mesero que tuvo el testigo
    participantes: dict = field(default_factory=dict)    # id -> Nodo
    pendientes: deque = field(default_factory=deque)
    asignadas: set = field(default_factory=set)
    hechas: set = field(default_factory=set)
    procesos: dict = field(default_factory=dict)         # índice local -> Popen
    activa: bool = True
    sin_vivos: float | None = None    # desde cuándo no queda ningún nodo vivo
    ultimo_avance: float = 0.0        # última vez que un nodo informó algo
    detener: bool = False             # el coordinador pidió detenerla

    def t(self) -> float:
        return round(time.time() - self.t0, 4)


class Coordinador:
    def __init__(self, host: str = "127.0.0.1", puerto: int = PUERTO,
                 clave: str | None = None) -> None:
        self.clave = clave or os.environ.get("LAB_CLAVE") or secrets.token_hex(8)
        self.entrada: queue.Queue = queue.Queue()
        self.candado = CandadoDistribuido()      # la caja
        self.llave = CandadoDistribuido()        # el GIL distribuido del modo concurrente
        self.buzones: dict[int, queue.Queue] = {}
        self.nodos: dict[int, Nodo] = {}
        self.cond = threading.Condition()
        self._ids = itertools.count(1)
        self._ejecuciones = itertools.count(1)
        self._ej: _Ejecucion | None = None
        self.al_cambio: Callable[[dict], None] | None = None   # la web lo usa para avisar
        control = Control(self)

        class _Servidor(BaseManager):
            pass

        _Servidor.register("control", callable=lambda: control)
        _Servidor.register("entrada", callable=lambda: self.entrada)
        _Servidor.register("candado", callable=lambda: self.candado)
        _Servidor.register("buzon", callable=lambda nid: self.buzones[nid])

        error = None
        for p in range(puerto, puerto + 20):
            try:
                self._servidor = _Servidor(address=(host, p), authkey=self.clave.encode()).get_server()
                break
            except OSError as e:
                error = e
        else:
            raise OSError(f"No hay un puerto libre para el coordinador desde {puerto}: {error}")
        self.host, self.puerto = self._servidor.address[0], self._servidor.address[1]

        # El servidor de multiprocessing termina con sys.exit() el hilo de cada
        # conexión cuando el nodo se desconecta (pensado para correr en su propio
        # proceso). Aquí comparte proceso con la web: basta con terminar el hilo.
        atender_conexion = self._servidor.serve_client

        def serve_client(conexion) -> None:
            try:
                atender_conexion(conexion)
            except SystemExit:
                pass

        self._servidor.serve_client = serve_client
        threading.Thread(target=self._servidor.serve_forever, name="coordinador",
                         daemon=True).start()

    # ------------------------------------------------------------ direcciones
    @property
    def direccion_local(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"{host}:{self.puerto}"

    def direccion_red(self) -> str:
        """Dirección que deben usar los nodos de otros computadores."""
        if self.host not in ("0.0.0.0", ""):
            return f"{self.host}:{self.puerto}"
        try:                       # IP de la interfaz que sale a la red local
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))
                return f"{s.getsockname()[0]}:{self.puerto}"
        except OSError:
            return f"{socket.gethostname()}:{self.puerto}"

    # ---------------------------------------------- llamadas de los nodos
    def _registrar(self, info: dict) -> dict:
        with self.cond:
            nid = next(self._ids)
            local = info.get("indice") is not None
            nombre = (f"Nodo {info['indice'] + 1}" if local
                      else f"Remoto {sum(not n.local for n in self.nodos.values()) + 1} · {info.get('host', '?')}")
            self.buzones[nid] = queue.Queue()
            n = Nodo(nid, nombre, info, local, time.time())
            self.nodos[nid] = n
            ej = self._ej
            if local and ej and ej.activa and info.get("ej") == ej.id:
                n.idx = info["indice"]
                ej.participantes[nid] = n
                self._alta(ej, n)
            elif local:                # nodo de una ejecución que ya terminó: que se cierre
                self.buzones[nid].put({"cerrar": True})
            self.cond.notify_all()
            return {"id": nid, "nombre": nombre}

    def _latido(self, nid: int) -> None:
        with self.cond:
            n = self.nodos.get(nid)
            if n:
                n.ultimo = time.time()

    def _latido_navegador(self, n: Nodo) -> None:
        """Latido de un navegador. Si se le había dado por caído, era una falsa
        alarma (latidos atrasados, no un equipo apagado): se le readmite."""
        with self.cond:
            n.ultimo = time.time()
            if n.vivo:
                return
            n.vivo, n.tarea = True, None
            self.nodos[n.id] = n          # pudo haberse borrado al terminar la ejecución
            ej = self._ej
            en_curso = False
            if ej and ej.activa and n.id in ej.participantes:
                ocupado = any(p.vivo and p.idx == n.idx and p.id != n.id
                              for p in ej.participantes.values())
                if ocupado:                # otro tomó su puesto: entra en la siguiente
                    del ej.participantes[n.id]
                else:
                    en_curso = True
                    self._alta(ej, n, vuelve=True)
                    self.cond.notify_all()
            elif ej and ej.activa:
                en_curso = self._incorporar(n, None)
        # Su pedido ya se le dio a otro: que lo abandone para no trabajar en vano
        n.canal.enviar_json({"tipo": "cancelar", "en_ejecucion": en_curso})
        self._avisar()

    # ---------------------------------------------------------- consultas
    def remotos(self, maximo: int = MAX_NODOS) -> list[Nodo]:
        """Nodos externos (navegadores u otros computadores) conectados y vivos.

        Si hay más que `maximo`, entran primero los que menos han trabajado,
        para que en cada ejecución participen equipos distintos.
        """
        ahora = time.time()
        with self.cond:
            for n in list(self.nodos.values()):
                if not n.local and n.vivo and ahora - n.ultimo > LATIDO_MAX:
                    n.vivo = False
                if not n.local and not n.vivo and not (self._ej and n.id in self._ej.participantes):
                    del self.nodos[n.id]
                    self.buzones.pop(n.id, None)
            vivos = sorted((n for n in self.nodos.values() if not n.local and n.vivo),
                           key=lambda n: (n.atendidas, n.id))
            return sorted(vivos[:max(0, maximo)], key=lambda n: n.id)

    def info(self) -> dict:
        externos = self.remotos(MAX_NAVEGADORES)
        return {"direccion": self.direccion_red(),
                "remotos": [n.nombre for n in externos],
                "navegadores": sum(n.navegador for n in externos),
                "max_nodos": MAX_NODOS}

    def _avisar(self) -> None:
        if self.al_cambio:
            try:
                self.al_cambio(self.info())
            except Exception:  # noqa: BLE001  avisar nunca debe tumbar al coordinador
                pass

    # ------------------------------------------------ nodos del navegador
    def atender_navegador(self, canal) -> None:
        """Sesión de un nodo del navegador; corre en el hilo de su conexión.

        Protocolo (JSON): hora (Cristian) → hola (registro) → latidos cada
        0,25 s; recibe solicitud y responde evento, tomar y soltar.
        """
        n: Nodo | None = None
        try:
            while True:
                msg = canal.recibir_json()
                tipo = msg.get("tipo")
                if tipo == "hora":
                    canal.enviar_json({"tipo": "hora", "c": _num(msg.get("c")), "s": time.time()})
                elif tipo == "hola" and n is None:
                    n = self._registrar_navegador(canal, msg)
                    if n is None:
                        return
                elif n is None:
                    continue
                elif tipo == "latido":
                    self._latido_navegador(n)
                elif tipo == "evento":
                    self._evento_navegador(n, msg.get("ev"))
                elif tipo == "tomar":
                    threading.Thread(target=self._prestar_candado, args=(n,), daemon=True).start()
                elif tipo == "soltar":
                    self.candado.soltar(n.id)
                elif tipo == "llave":
                    threading.Thread(target=self._prestar_llave, args=(n,), daemon=True).start()
                elif tipo == "soltar_llave":
                    self.llave.soltar(n.id)
        except (OSError, ValueError, UnicodeDecodeError):
            pass                      # se cerró o mandó basura: el detector de fallos decide
        finally:
            canal.cerrar()
            if n is not None:
                with self.cond:
                    if not (self._ej and n.id in self._ej.participantes):
                        n.vivo = False      # fuera de una ejecución no hay nada que reasignar
                self._avisar()

    def _registrar_navegador(self, canal, msg: dict) -> Nodo | None:
        persona = re.sub(r"[^\w .-]", "", str(msg.get("nombre", "")))[:18].strip() or "Invitado"
        hilo = int(min(max(_num(msg.get("hilo"), 1), 1), 32))
        # La identidad la guarda la pestaña del mesero: si vuelve a entrar tras
        # caerse, el coordinador lo reconoce (como un equipo que se reinicia).
        identidad = str(msg.get("identidad", ""))
        identidad = identidad if re.fullmatch(r"[A-Za-z0-9-]{8,64}", identidad) else None
        with self.cond:
            if sum(x.navegador and x.vivo for x in self.nodos.values()) >= MAX_NAVEGADORES:
                canal.enviar_json({"tipo": "lleno", "mensaje": "Ya hay demasiados equipos conectados"})
                return None
            previo = None
            if identidad:
                previo = max((x for x in self.nodos.values() if x.info.get("identidad") == identidad),
                             key=lambda x: x.id, default=None)
            if previo is not None and previo.vivo:
                # La instancia anterior ya no existe aunque aún no haya faltado a sus latidos
                self._dar_de_baja(previo, "reinicio")
            nid = next(self._ids)
            nombre = previo.nombre if previo else (persona if hilo == 1 else f"{persona}·{hilo}")
            if any(x.nombre == nombre and x.vivo for x in self.nodos.values()):
                nombre = f"{nombre} #{nid}"
            n = Nodo(nid, nombre, {
                "host": persona, "pid": f"web-{nid}", "hilo": hilo,
                "nucleos": int(min(max(_num(msg.get("nucleos"), 1), 1), 256)),
                "rtt": min(max(_num(msg.get("rtt")), 0.0), 10.0),
                "ajuste": min(max(_num(msg.get("ajuste")), -86400.0), 86400.0),
                "desfase": 0.0, "navegador": True, "identidad": identidad,
            }, local=False, ultimo=time.time(), canal=canal)
            self.nodos[nid] = n
            en_curso = self._incorporar(n, previo)
        canal.enviar_json({"tipo": "bienvenido", "id": nid, "nombre": nombre, "en_ejecucion": en_curso})
        self._avisar()
        return n

    def _prestar_llave(self, n: Nodo) -> None:
        if self.llave.tomar(n.id):
            if not n.canal.enviar_json({"tipo": "llave_ok"}):
                self.llave.soltar(n.id)

    def _prestar_candado(self, n: Nodo) -> None:
        if self.candado.tomar(n.id):
            if not n.canal.enviar_json({"tipo": "candado"}):
                self.candado.soltar(n.id)

    def _evento_navegador(self, n: Nodo, ev) -> None:
        """Valida un evento que llega del navegador y lo pasa al recolector."""
        if not isinstance(ev, dict) or ev.get("tipo") not in TIPOS_NAVEGADOR:
            return
        with self.cond:
            ej, tarea = self._ej, n.tarea
            if ej is None or tarea is None or _num(ev.get("id")) != tarea["id"]:
                return
            limite = time.time() - ej.t0 + 0.5      # un nodo no puede informar el futuro
            limpio = {
                "tipo": ev["tipo"], "id": tarea["id"],
                "t": round(min(max(_num(ev.get("t")), 0.0), limite), 4),
                "trabajador": n.nombre, "pid": n.info["pid"], "tid": n.info["hilo"],
                "nucleo": None, "nodo": n.nombre, "nodo_id": n.id, "nodo_idx": n.idx,
                "ej": ej.id, "host": n.info["host"], "remoto": True, "navegador": True,
            }
        if ev["tipo"] == "inicio":
            limpio |= {"trabajo": tarea["trabajo"], "realista": ej.etapas is not None}
        elif ev["tipo"] == "progreso":
            limpio["progreso"] = round(min(max(_num(ev.get("progreso")), 0.0), 1.0), 3)
        elif ev["tipo"] == "etapa":
            if ev.get("etapa") not in ETAPAS_VALIDAS:
                return
            limpio["etapa"] = ev["etapa"]
            if ev.get("dur") is not None:
                limpio["dur"] = round(min(max(_num(ev.get("dur")), 0.0), 30.0), 3)
        self.entrada.put(limpio)

    # ---------------------------------------------------------- ejecución
    def ejecutar(self, orden: list[dict], recibir: Callable[[dict], None], t0: float,
                 emitir: Callable[[dict], None], locales: int,
                 etapas: dict | None = None, latencia: float = 0.0,
                 caida: bool = False, remotos: list[Nodo] | None = None,
                 politica: str = "distribuido") -> None:
        """Reparte `orden` entre los nodos y bloquea hasta que las atienden todas.

        recibir(s) espera la hora de llegada del cliente y registra su llegada.
        politica: cómo se coordinan los nodos (ver POLITICAS).
        """
        if politica not in POLITICAS:
            raise ValueError(f"Política desconocida: {politica}")
        ej = _Ejecucion(next(self._ejecuciones), t0, emitir, latencia, etapas,
                        caida and locales + len(remotos or []) >= 2, politica)
        ej.ultimo_avance = time.time()
        # Pizarra limpia: ni candados ni trabajos viejos de una ejecución anterior
        self.candado.reiniciar()
        self.llave.reiniciar()
        with self.cond:
            self._ej = ej
            for n in self.nodos.values():
                if n.navegador and n.vivo:
                    n.canal.enviar_json({"tipo": "reiniciar"})
            for k, n in enumerate(remotos or []):
                n.idx, n.tarea = locales + k, None
                ej.participantes[n.id] = n
                self._alta(ej, n)

        for i in range(locales):
            ej.procesos[i] = self._lanzar(i, ej.id)

        reenvio = threading.Thread(target=self._reenviar, args=(ej,), name="reenvio", daemon=True)
        reenvio.start()

        def recepcion() -> None:
            for s in orden:
                recibir(s)
                with self.cond:
                    ej.pendientes.append(s)
                    self.cond.notify_all()

        threading.Thread(target=recepcion, name="recepcion", daemon=True).start()

        try:
            with self.cond:
                while len(ej.hechas) < len(orden):
                    if ej.detener:
                        raise RuntimeError("El coordinador detuvo la ejecución")
                    if time.time() - ej.ultimo_avance > SIN_AVANCE:
                        raise RuntimeError(f"Nadie avanzó en {SIN_AVANCE:.0f} s: la ejecución se canceló "
                                           "para no bloquear la cafetería")
                    self._vigilar(ej)
                    self._repartir(ej)
                    self._comprobar(ej)
                    self.cond.wait(0.05)
        finally:
            with self.cond:
                ej.activa = False
                self._ej = None
                for n in ej.participantes.values():
                    if n.local:
                        self.buzones[n.id].put({"cerrar": True})
                    elif n.navegador and n.vivo and len(ej.hechas) < len(orden):
                        n.canal.enviar_json({"tipo": "reiniciar"})   # se cortó: que suelten lo que hacían
            self.candado.reiniciar()
            self.llave.reiniciar()
            for proc in ej.procesos.values():
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            reenvio.join(timeout=2)
            with self.cond:
                for n in list(self.nodos.values()):
                    if n.local or not n.vivo:
                        del self.nodos[n.id]
                        self.buzones.pop(n.id, None)
                    else:
                        n.tarea = None
            self._avisar()

    def detener(self) -> bool:
        """Lo pide el coordinador desde la web: corta la ejecución en curso."""
        with self.cond:
            if self._ej is None:
                return False
            self._ej.detener = True
            self.cond.notify_all()
            return True

    def _lanzar(self, indice: int, ej_id: int) -> subprocess.Popen:
        """Arranca un nodo local: otro programa de Python, sin memoria compartida."""
        cmd = [sys.executable, "-m", "servidor.nodo", "--coordinador", self.direccion_local,
               "--indice", str(indice), "--ej", str(ej_id),
               "--desfase", str(DESFASE_RELOJ[indice % len(DESFASE_RELOJ)])]
        return subprocess.Popen(cmd, cwd=RAIZ, env={**os.environ, "LAB_CLAVE": self.clave})

    def _incorporar(self, n: Nodo, previo: Nodo | None) -> bool:
        """Mete en la ejecución en curso a un nodo del navegador que llega tarde.

        Un mesero que vuelve recupera su mismo puesto; uno nuevo ocupa el puesto
        de un nodo caído. Si no hay puesto libre, entra en la siguiente ejecución.
        """
        ej = self._ej
        if ej is None or not ej.activa:
            return False
        if previo is not None and previo.id in ej.participantes:
            puesto = previo.idx
        else:
            libres = sorted({p.idx for p in ej.participantes.values()}
                            - {p.idx for p in ej.participantes.values() if p.vivo})
            if not libres:
                return False
            puesto = libres[0]
        n.idx = puesto
        ej.participantes[n.id] = n
        self._alta(ej, n, vuelve=previo is not None)
        self.cond.notify_all()        # que el coordinador le reparta pedidos ya
        return True

    def _dar_de_baja(self, n: Nodo, motivo: str = "latidos") -> None:
        """El nodo se da por caído: se le revocan candado y llave y su pedido vuelve a la fila."""
        ahora = time.time()
        n.vivo = False
        self.candado.revocar(n.id)
        self.llave.revocar(n.id)
        tarea, n.tarea = n.tarea, None
        ej = self._ej
        if ej is None or n.id not in ej.participantes:
            return
        if tarea and tarea["id"] not in ej.hechas:
            ej.pendientes.appendleft(tarea)       # se reasigna primero
        ej.emitir({"tipo": "nodo_caido", "t": ej.t(), "nodo": n.nombre,
                   "nodo_idx": n.idx, "id": tarea["id"] if tarea else None,
                   "silencio": round(ahora - n.ultimo, 3), "motivo": motivo})

    def _alta(self, ej: _Ejecucion, n: Nodo, vuelve: bool = False) -> None:
        if n.local:
            ej_lat = ej.latencia * FACTOR_LATENCIA[n.idx % len(FACTOR_LATENCIA)]
        else:
            ej_lat = 0.0               # un nodo remoto ya tiene la latencia real de la red
        n.info["latencia"] = round(ej_lat, 4)
        ej.emitir({"tipo": "nodo_alta", "t": ej.t(), "nodo": n.nombre, "nodo_idx": n.idx,
                   "nodo_id": n.id, "host": n.info.get("host"), "pid": n.info.get("pid"),
                   "remoto": not n.local, "rtt": n.info.get("rtt"), "ajuste": n.info.get("ajuste"),
                   "desfase": n.info.get("desfase"), "latencia": n.info["latencia"],
                   "vuelve": vuelve})

    def _repartir(self, ej: _Ejecucion) -> None:
        """Balanceo de carga: cada solicitud va al nodo libre que menos ha atendido.

        En secuencial hay un testigo (token ring): solo se asigna cuando nadie
        trabaja, y el turno pasa al siguiente mesero del anillo.
        """
        vivos = [n for n in ej.participantes.values() if n.vivo]
        if ej.politica == "secuencial":
            if any(n.tarea for n in vivos) or not vivos:
                return
            anillo = sorted(vivos, key=lambda n: n.idx)
            siguiente = next((n for n in anillo if n.idx > ej.turno), anillo[0])
            libres = [siguiente]
        else:
            libres = sorted((n for n in vivos if n.tarea is None),
                            key=lambda n: (n.atendidas, n.idx))
        while libres and ej.pendientes:
            n, s = libres.pop(0), ej.pendientes.popleft()
            ej.turno = n.idx
            n.tarea = s
            ej.ultimo_avance = time.time()
            reintento = s["id"] in ej.asignadas
            ej.asignadas.add(s["id"])
            self._enviar(n, {"tipo": "solicitud", "solicitud": s, "t0": ej.t0,
                             "etapas": ej.etapas, "ej": ej.id, "nodo_idx": n.idx,
                             "modo": ej.politica,
                             "latencia": n.info.get("latencia", 0.0)})
            ej.emitir({"tipo": "asignada", "id": s["id"], "t": ej.t(), "nodo": n.nombre,
                       "nodo_idx": n.idx, "reintento": reintento, "politica": ej.politica,
                       "latencia": n.info.get("latencia", 0.0)})

    def _enviar(self, n: Nodo, msg: dict) -> None:
        """Mismo mensaje, dos transportes: WebSocket (navegador) o buzón (TCP local)."""
        if n.navegador:
            n.canal.enviar_json(msg)       # si falla, los latidos dirán que cayó
        else:
            self.buzones[n.id].put(msg)

    def _vigilar(self, ej: _Ejecucion) -> None:
        """Detector de fallos por latidos: sin latido reciente = nodo caído."""
        ahora = time.time()
        for n in list(ej.participantes.values()):
            if n.vivo and ahora - n.ultimo > LATIDO_MAX:
                self._dar_de_baja(n)

    def _comprobar(self, ej: _Ejecucion) -> None:
        vivos = any(n.vivo for n in ej.participantes.values())
        registrados = {n.idx for n in ej.participantes.values() if n.local}
        arrancando = any(p.poll() is None for i, p in ej.procesos.items() if i not in registrados)
        if vivos or arrancando:
            ej.sin_vivos = None
            if not ej.participantes and time.time() - ej.t0 > ESPERA_REGISTRO:
                raise RuntimeError("Ningún nodo se registró en el coordinador")
            return
        # Cayeron todos: se espera un rato a que vuelva alguno (o entre otro) antes de rendirse
        if ej.sin_vivos is None:
            ej.sin_vivos = time.time()
            ej.emitir({"tipo": "sin_meseros", "t": ej.t(), "espera": GRACIA})
        if time.time() - ej.sin_vivos > GRACIA:
            raise RuntimeError("No quedan nodos vivos para atender las solicitudes")

    def _reenviar(self, ej: _Ejecucion) -> None:
        """Recolecta los eventos de los nodos y descarta los que ya no valen."""
        while ej.activa:
            try:
                ev = self.entrada.get(timeout=0.05)
            except queue.Empty:
                continue
            if ev.get("ej") != ej.id:
                continue                              # de una ejecución anterior
            matar = None
            with self.cond:
                n = ej.participantes.get(ev.get("nodo_id"))
                # Mensaje de un nodo dado por caído, o de una solicitud que ya no
                # es suya: se descarta para que nadie reciba dos respuestas.
                if n is None or not n.vivo or n.tarea is None or n.tarea["id"] != ev["id"]:
                    continue
                ej.ultimo_avance = time.time()
                if ev["tipo"] == "fin":
                    n.tarea = None
                    n.atendidas += 1
                    ej.hechas.add(ev["id"])
                    self.cond.notify_all()
                elif (ej.caida and ev["tipo"] == "progreso" and ev["progreso"] >= 0.4
                      and sum(x.vivo for x in ej.participantes.values()) >= 2):
                    ej.caida = False                  # se simula una sola caída
                    matar = n
            ej.emitir(ev)
            if matar:
                if matar.navegador:                   # el navegador deja de latir y responder
                    matar.canal.enviar_json({"tipo": "caer"})
                elif ej.procesos.get(matar.idx):
                    ej.procesos[matar.idx].kill()     # el proceso se apaga sin avisar
                ej.emitir({"tipo": "falla", "t": ej.t(), "nodo": matar.nombre,
                           "nodo_idx": matar.idx, "id": ev["id"]})


def _num(valor, defecto: float = 0.0) -> float:
    """Número finito o `defecto`: los mensajes del navegador no son de confianza."""
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return defecto
    return x if x == x and abs(x) != float("inf") else defecto


_coordinador: Coordinador | None = None
_candado_global = threading.Lock()


def obtener(host: str = "127.0.0.1", puerto: int = PUERTO) -> Coordinador:
    """El coordinador es único y se crea la primera vez que se necesita."""
    global _coordinador
    with _candado_global:
        if _coordinador is None:
            _coordinador = Coordinador(host, puerto)
        return _coordinador
