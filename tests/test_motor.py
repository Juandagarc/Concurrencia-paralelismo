"""Pruebas del simulador.  Ejecutar con:  python -m pytest -q"""

import pytest

from servidor import motor
from servidor.solicitudes import MENU_CPU, MENU_IO, generar
from servidor.trabajo import calibrar

OPS = calibrar(muestra=100_000, intentos=1)


@pytest.mark.parametrize("modo", motor.MODOS)
def test_cada_modo_atiende_las_10_solicitudes(modo):
    lote = generar("io", 0.05, OPS)
    r = motor.ejecutar(modo, lote, trabajadores=3)
    tipos = [e["tipo"] for e in r["eventos"]]
    assert tipos.count("llegada") == 10
    assert tipos.count("inicio") == 10
    assert tipos.count("fin") == 10
    assert tipos.count("progreso") == 100
    assert len(r["solicitudes"]) == 10
    assert all(s["respuesta"] >= s["espera"] for s in r["solicitudes"])


def test_secuencial_atiende_en_orden_con_un_hilo():
    r = motor.ejecutar("secuencial", generar("io", 0.03, OPS))
    inicios = [e["id"] for e in r["eventos"] if e["tipo"] == "inicio"]
    assert inicios == list(range(1, 11))
    assert r["trabajadores_usados"] == 1


def test_hilos_aceleran_la_espera_de_es():
    lote = generar("io", 0.1, OPS)
    sec = motor.ejecutar("secuencial", lote)["total"]
    con = motor.ejecutar("concurrente", lote, trabajadores=5)["total"]
    assert con < sec / 2.5          # ideal: 5 veces más rápido


def test_paralelo_usa_procesos_distintos():
    r = motor.ejecutar("paralelo", generar("cpu", 0.05, OPS), trabajadores=2)
    assert r["procesos_usados"] == 2
    assert len({s["pid"] for s in r["solicitudes"]}) == 2


def test_generar_alterna_en_mixto():
    lote = generar("mixto", 1.0, OPS)
    assert [s["trabajo"] for s in lote[:4]] == ["io", "cpu", "io", "cpu"]
    assert sum(s["duracion"] for s in lote) == pytest.approx(10.0, abs=0.01)
    assert {s["producto"] for s in lote[:2]} <= {p for p, _ in MENU_IO + MENU_CPU}


@pytest.mark.parametrize("modo", ["concurrente", "paralelo", "distribuido"])
def test_tiempo_real_la_caja_atiende_uno_a_la_vez(modo):
    """La caja es un Lock compartido: dos cobros nunca se solapan."""
    rapidas = {"pedido": 0.05, "camino_caja": 0.02, "cobro": 0.05, "entrega": 0.05}
    r = motor.ejecutar(modo, generar("io", 0.05, OPS), trabajadores=4, realista=True, etapas=rapidas)
    ev = r["eventos"]
    cobro = {e["id"]: e["t"] for e in ev if e.get("etapa") == "caja_cobro"}
    salida = {e["id"]: e["t"] for e in ev if e.get("etapa") == "entrega"}
    assert len(cobro) == 10
    tramos = sorted((cobro[i], salida[i]) for i in cobro)
    # 5 ms de tolerancia: en distribuido cada nodo marca la hora con su propio
    # reloj, corregido con Cristian (error ≤ RTT/2)
    assert all(b[0] >= a[1] - 5e-3 for a, b in zip(tramos, tramos[1:]))
    etapas = [e["etapa"] for e in ev if e["tipo"] == "etapa" and e["id"] == 1]
    assert etapas == ["pedido", "preparar", "caja_camino", "caja_espera", "caja_cobro", "entrega"]


def test_llegadas_escalonadas_se_respetan():
    lote = generar("io", 0.05, OPS, llegadas="escalonadas")
    assert lote[0]["llegada"] == 0 and lote[-1]["llegada"] > 1
    r = motor.ejecutar("concurrente", lote, trabajadores=4)
    llegada = {e["id"]: e["t"] for e in r["eventos"] if e["tipo"] == "llegada"}
    for s in lote:
        assert llegada[s["id"]] >= s["llegada"] - 0.01      # no llega antes de su hora
    for f in r["solicitudes"]:
        assert f["espera"] >= 0 and f["respuesta"] >= f["servicio"]


def test_distribuido_reparte_entre_nodos_y_corrige_relojes():
    r = motor.ejecutar("distribuido", generar("io", 0.1, OPS), trabajadores=3, latencia=0.02)
    assert r["nodos_usados"] == 3 and r["procesos_usados"] == 3
    altas = [e for e in r["eventos"] if e["tipo"] == "nodo_alta"]
    assert len(altas) == 3
    for a in altas:   # Cristian: el ajuste compensa el reloj desfasado del nodo
        assert a["ajuste"] == pytest.approx(-a["desfase"], abs=0.01)
    asignadas = [e for e in r["eventos"] if e["tipo"] == "asignada"]
    assert len(asignadas) == 10 and not any(e["reintento"] for e in asignadas)
    # Los tiempos de todos los nodos quedan en el reloj del coordinador
    for s in r["solicitudes"]:
        assert 0 <= s["espera"] < 5 and 0 < s["servicio"] < 5


def test_distribuido_tolera_la_caida_de_un_nodo():
    r = motor.ejecutar("distribuido", generar("io", 0.3, OPS), trabajadores=3, caida=True)
    ev = r["eventos"]
    caidos = [e for e in ev if e["tipo"] == "nodo_caido"]
    assert len(caidos) == 1 and caidos[0]["id"] is not None
    perdida = caidos[0]["id"]
    reintento = [e for e in ev if e["tipo"] == "asignada" and e["reintento"]]
    assert [e["id"] for e in reintento] == [perdida]
    assert reintento[0]["nodo"] != caidos[0]["nodo"]
    # Las 10 se atienden una sola vez y ninguna la termina el nodo caído
    fines = [e for e in ev if e["tipo"] == "fin"]
    assert sorted(e["id"] for e in fines) == list(range(1, 11))
    assert all(e["nodo"] != caidos[0]["nodo"] for e in fines)


# ------------------------------------------------ nodos del navegador (WebSocket)
import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from servidor import distribuido, ws  # noqa: E402


@pytest.fixture(scope="module")
def url_ws():
    """Un servidor HTTP mínimo que solo atiende el WebSocket de los nodos."""
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            canal = ws.aceptar(self)
            distribuido.obtener().atender_navegador(canal)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"ws://127.0.0.1:{srv.server_address[1]}/ws/nodo"
    srv.shutdown()


def nodo_navegador(url, nombre, caer_en=None):
    """Imita web/nodo-trabajador.js: Cristian, registro, latidos y eventos JSON."""
    c = ws.conectar(url)
    t1 = time.time()
    c.enviar_json({"tipo": "hora", "c": t1})
    r = c.recibir_json()
    rtt = time.time() - t1
    ajuste = r["s"] + rtt / 2 - time.time()
    c.enviar_json({"tipo": "hola", "nombre": nombre, "hilo": 1, "nucleos": 4,
                   "rtt": rtt, "ajuste": ajuste})
    alta = c.recibir_json()
    vivo = threading.Event()
    vivo.set()

    def latir():
        while vivo.is_set():
            c.enviar_json({"tipo": "latido"})
            time.sleep(0.2)

    def trabajar():
        try:
            while vivo.is_set():
                m = c.recibir_json()
                if m.get("tipo") != "solicitud":
                    continue
                s, t0 = m["solicitud"], m["t0"]
                ev = lambda tipo, **x: c.enviar_json(  # noqa: E731
                    {"tipo": "evento", "ev": {"tipo": tipo, "id": s["id"], "t": time.time() - t0, **x}})
                ev("inicio")
                # Basura que el coordinador debe descartar
                c.enviar_json({"tipo": "evento", "ev": {"tipo": "fin", "id": 999, "t": 0}})
                c.enviar_json({"tipo": "evento", "ev": {"tipo": "hackear", "id": s["id"]}})
                for paso in range(1, 11):
                    time.sleep(s["duracion"] / 10)
                    ev("progreso", progreso=paso / 10)
                    if caer_en is not None and paso == caer_en:
                        vivo.clear()          # deja de latir y de responder: se "cae"
                        return
                ev("fin")
        except (OSError, ValueError):
            pass

    threading.Thread(target=latir, daemon=True).start()
    threading.Thread(target=trabajar, daemon=True).start()
    return alta, vivo, c


def esperar_externos(n):
    for _ in range(50):
        if len(distribuido.obtener().remotos()) >= n:
            return
        time.sleep(0.05)
    raise AssertionError("el nodo del navegador no se registró")


def test_navegador_atiende_solicitudes_validadas(url_ws):
    alta, vivo, c = nodo_navegador(url_ws, "Ana<script>")
    try:
        assert alta["tipo"] == "bienvenido" and alta["nombre"].startswith("Anascript")
        esperar_externos(1)
        r = motor.ejecutar("distribuido", generar("io", 0.1, OPS), trabajadores=1)
        fines = [e for e in r["eventos"] if e["tipo"] == "fin"]
        assert sorted(e["id"] for e in fines) == list(range(1, 11))
        del_navegador = [e for e in fines if e.get("navegador")]
        assert del_navegador and all(e["trabajador"] == alta["nombre"] for e in del_navegador)
        assert all(e["tipo"] != "hackear" for e in r["eventos"])
        assert r["nodos_usados"] == 2
    finally:
        vivo.clear()
        c.cerrar()


def test_navegador_que_se_cae_se_reasigna(url_ws):
    alta, vivo, c = nodo_navegador(url_ws, "Bruno", caer_en=3)
    try:
        esperar_externos(1)
        r = motor.ejecutar("distribuido", generar("io", 0.3, OPS), trabajadores=1)
        caidos = [e for e in r["eventos"] if e["tipo"] == "nodo_caido"]
        assert [e["nodo"] for e in caidos] == [alta["nombre"]]
        fines = [e for e in r["eventos"] if e["tipo"] == "fin"]
        assert sorted(e["id"] for e in fines) == list(range(1, 11))
        assert all(e["nodo"] != alta["nombre"] for e in fines)
    finally:
        vivo.clear()
        c.cerrar()


# ------------------------------------- versión web: siempre distribuida (red)
import queue as _queue  # noqa: E402


class MeseroFalso:
    """Imita un computador que entra como mesero (web/nodo-trabajador.js)."""

    def __init__(self, url, nombre, tramos=None, caer_al_pedirlo=True, identidad=None):
        self.c = ws.conectar(url)
        self.vivo = threading.Event()
        self.vivo.set()
        self.entrantes: _queue.Queue = _queue.Queue()
        self.llaves: _queue.Queue = _queue.Queue()
        self.candados: _queue.Queue = _queue.Queue()
        self.reinicios = 0
        self.tramos = tramos            # lista compartida: (inicio, fin) de cada cálculo con la llave
        self.caer_al_pedirlo = caer_al_pedirlo
        self.c.enviar_json({"tipo": "hora", "c": time.time()})
        self.c.recibir_json()
        self.c.enviar_json({"tipo": "hola", "nombre": nombre, "hilo": 1, "rtt": 0, "ajuste": 0,
                            "identidad": identidad})
        self.bienvenida = self.c.recibir_json()
        self.nombre = self.bienvenida["nombre"]
        for f in (self._leer, self._latir, self._trabajar):
            threading.Thread(target=f, daemon=True).start()

    def _leer(self):
        try:
            while self.vivo.is_set():
                m = self.c.recibir_json()
                if m["tipo"] == "llave_ok":
                    self.llaves.put(m)
                elif m["tipo"] == "candado":
                    self.candados.put(m)
                elif m["tipo"] in ("reiniciar", "cancelar"):
                    self.reinicios += 1             # como el Worker: suelta lo que hacía
                elif m["tipo"] == "caer" and self.caer_al_pedirlo:
                    self.vivo.clear()       # deja de latir y de trabajar sin avisar
                else:
                    self.entrantes.put(m)
        except (OSError, ValueError):
            pass

    def _esperar(self, cola, epoca, limite=20.0):
        """Espera la respuesta del coordinador; None si hubo un reinicio o se cayó."""
        fin = time.time() + limite
        while time.time() < fin and self.vivo.is_set() and self.reinicios == epoca:
            try:
                return cola.get(timeout=0.05)
            except _queue.Empty:
                continue
        return None

    def _latir(self):
        while self.vivo.is_set():
            self.c.enviar_json({"tipo": "latido"})
            time.sleep(0.2)

    def _trabajar(self):
        while self.vivo.is_set():
            try:
                m = self.entrantes.get(timeout=0.1)
            except _queue.Empty:
                continue
            if m.get("tipo") != "solicitud":
                continue
            s, t0 = m["solicitud"], m["t0"]
            epoca = self.reinicios

            def ev(tipo, **x):
                self.c.enviar_json({"tipo": "evento",
                                    "ev": {"tipo": tipo, "id": s["id"], "t": time.time() - t0, **x}})
            ev("inicio")
            for paso in range(1, 6):
                if not self.vivo.is_set():
                    return
                gil = m.get("modo") == "concurrente" and s["trabajo"] == "cpu"
                if gil:
                    self.c.enviar_json({"tipo": "llave"})
                    if self._esperar(self.llaves, epoca) is None:
                        break
                a = time.time()
                time.sleep(s["duracion"] / 5)
                if gil:
                    self.tramos.append((a, time.time()))
                    self.c.enviar_json({"tipo": "soltar_llave"})
                ev("progreso", progreso=paso / 5)
            if self.reinicios != epoca:
                continue                            # pedido abandonado: el siguiente
            et = m.get("etapas")
            if et and self.vivo.is_set():          # la caja: Lock distribuido
                ev("etapa", etapa="caja_espera")
                self.c.enviar_json({"tipo": "tomar"})
                if self._esperar(self.candados, epoca) is None:
                    continue                        # se cortó la ejecución o se cayó
                ev("etapa", etapa="caja_cobro", dur=et["cobro"])
                time.sleep(et["cobro"])
                self.c.enviar_json({"tipo": "soltar"})
                ev("etapa", etapa="entrega", dur=et["entrega"])
            if self.vivo.is_set() and self.reinicios == epoca:
                ev("fin")

    def cerrar(self):
        self.vivo.clear()
        self.c.cerrar()

    def apagar_sin_avisar(self):
        """Como el botón de la página: el Worker muere sin despedirse."""
        self.vivo.clear()
        self.c.sock.close()


@pytest.fixture
def meseros(url_ws):
    creados = []

    def crear(n, **kw):
        for i in range(n):
            creados.append(MeseroFalso(url_ws, f"M{i + 1}", **kw))
        esperar_externos(len(creados))
        return creados

    yield crear
    for m in creados:
        m.cerrar()
    for _ in range(40):                     # que el coordinador los dé de baja
        if not distribuido.obtener().remotos():
            break
        time.sleep(0.05)


def test_red_sin_meseros_no_ejecuta():
    with pytest.raises(RuntimeError, match="No hay meseros"):
        motor.ejecutar("paralelo", generar("io", 0.05, OPS), red=True)


def test_red_secuencial_pasa_el_testigo_en_anillo(meseros):
    ms_ = meseros(3)
    r = motor.ejecutar("secuencial", generar("io", 0.05, OPS), red=True)
    ev = r["eventos"]
    inicio = {e["id"]: e["t"] for e in ev if e["tipo"] == "inicio"}
    fin = {e["id"]: e["t"] for e in ev if e["tipo"] == "fin"}
    tramos = sorted((inicio[i], fin[i]) for i in fin)
    assert len(tramos) == 10
    assert all(b[0] >= a[1] - 5e-3 for a, b in zip(tramos, tramos[1:]))   # nunca dos a la vez
    turnos = [e["nodo"] for e in ev if e["tipo"] == "asignada"]
    nombres = [m.nombre for m in ms_]
    assert turnos[:6] == nombres + nombres                                # el testigo rota


def test_red_concurrente_turna_la_cpu_con_la_llave(meseros):
    tramos = []
    meseros(3, tramos=tramos)
    r = motor.ejecutar("concurrente", generar("cpu", 0.1, OPS), red=True)
    assert len({e["nodo"] for e in r["eventos"] if e["tipo"] == "inicio"}) == 3   # todos atienden
    tramos.sort()
    assert len(tramos) == 50                                                      # 10 pedidos × 5 tramos
    assert all(b[0] >= a[1] - 5e-3 for a, b in zip(tramos, tramos[1:]))           # pero calculan de a uno


def test_red_paralelo_reparte_y_tolera_la_caida(meseros):
    meseros(3)
    r = motor.ejecutar("paralelo", generar("io", 0.3, OPS), red=True, caida=True)
    ev = r["eventos"]
    caidos = [e for e in ev if e["tipo"] == "nodo_caido"]
    assert len(caidos) == 1
    fines = [e for e in ev if e["tipo"] == "fin"]
    assert sorted(e["id"] for e in fines) == list(range(1, 11))
    assert all(e["nodo"] != caidos[0]["nodo"] for e in fines)


def test_solo_el_coordinador_con_contrasena_ejecuta():
    from servidor.web import Laboratorio
    lab = Laboratorio(red=True, clave="clave-de-prueba")
    assert not lab.autorizado(None) and not lab.autorizado("inventada")
    assert lab.entrar("otra") is None
    sesion = lab.entrar("clave-de-prueba")
    assert sesion and lab.autorizado(sesion)
    assert Laboratorio(red=False).autorizado(None)        # el laboratorio local no pide clave


def test_red_mesero_que_vuelve_recupera_su_puesto_y_los_pedidos(url_ws):
    """Un solo mesero se apaga a mitad de la ejecución y vuelve a entrar."""
    ana = MeseroFalso(url_ws, "Ana", identidad="pestana-de-ana-123")
    esperar_externos(1)
    resultado = {}

    def correr():
        resultado["r"] = motor.ejecutar("paralelo", generar("io", 0.2, OPS), red=True)

    hilo = threading.Thread(target=correr)
    hilo.start()
    time.sleep(0.8)
    ana.apagar_sin_avisar()                          # Apagar sin avisar…
    time.sleep(3.2)                                  # …el coordinador nota que faltan latidos…
    ana2 = MeseroFalso(url_ws, "Ana", identidad="pestana-de-ana-123")   # …y vuelve a entrar
    try:
        assert ana2.bienvenida["en_ejecucion"] is True
        assert ana2.nombre == ana.nombre
        hilo.join(timeout=30)
        ev = resultado["r"]["eventos"]
        assert [e["tipo"] for e in ev].count("sin_meseros") == 1
        vuelta = [e for e in ev if e["tipo"] == "nodo_alta" and e.get("vuelve")]
        assert len(vuelta) == 1 and vuelta[0]["nodo_idx"] == 0          # mismo puesto
        fines = [e for e in ev if e["tipo"] == "fin"]
        assert sorted(e["id"] for e in fines) == list(range(1, 11))    # nada se perdió
    finally:
        ana2.cerrar()
        hilo.join(timeout=5)


def test_red_falsa_alarma_el_mesero_atrasado_se_readmite(url_ws):
    """Un mesero cuyos latidos se atrasan (pestaña en segundo plano) se da por
    caído, pero cuando vuelven sus latidos el coordinador lo readmite."""
    ana = MeseroFalso(url_ws, "Lenta", identidad="pestana-lenta-123")
    beto = MeseroFalso(url_ws, "Beto")
    esperar_externos(2)
    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(
        r=motor.ejecutar("paralelo", generar("io", 0.8, OPS), red=True)))
    hilo.start()
    time.sleep(0.6)
    ana.vivo.clear()                        # se congela: ni latidos ni trabajo…
    time.sleep(3.0)                         # …el coordinador la da por caída…
    ana.c.enviar_json({"tipo": "latido"})   # …y su latido vuelve: falsa alarma
    try:
        hilo.join(timeout=30)
        ev = resultado["r"]["eventos"]
        assert any(e["tipo"] == "nodo_caido" and e["nodo"] == ana.nombre for e in ev)
        assert any(e["tipo"] == "nodo_alta" and e.get("vuelve") and e["nodo"] == ana.nombre for e in ev)
        fines = [e for e in ev if e["tipo"] == "fin"]
        assert sorted(e["id"] for e in fines) == list(range(1, 11))
    finally:
        ana.cerrar()
        beto.cerrar()


def test_red_caida_en_tiempo_real_no_bloquea_la_caja(meseros):
    """La caída de un mesero no puede dejar a los demás esperando la caja."""
    meseros(3)
    rapidas = {"pedido": 0.05, "camino_caja": 0.02, "cobro": 0.1, "entrega": 0.05}
    r = motor.ejecutar("paralelo", generar("cpu", 0.3, OPS), red=True, caida=True,
                       realista=True, etapas=rapidas)
    ev = r["eventos"]
    assert sum(e["tipo"] == "nodo_caido" for e in ev) == 1
    assert sorted(e["id"] for e in ev if e["tipo"] == "fin") == list(range(1, 11))


def test_red_detener_y_reiniciar_deja_todo_limpio(meseros):
    """El coordinador detiene una ejecución a mitad de la caja; la siguiente corre normal."""
    meseros(2)
    lentas = {"pedido": 0.05, "camino_caja": 0.02, "cobro": 0.6, "entrega": 0.05}
    hilo = threading.Thread(target=lambda: pytest.raises(
        RuntimeError, motor.ejecutar, "paralelo", generar("io", 0.1, OPS), red=True,
        realista=True, etapas=lentas))
    hilo.start()
    time.sleep(0.8)                                   # alguien está cobrando en la caja
    assert distribuido.obtener().detener()
    hilo.join(timeout=10)
    assert not hilo.is_alive()
    r = motor.ejecutar("paralelo", generar("io", 0.05, OPS), red=True, realista=True,
                       etapas={"pedido": 0.02, "camino_caja": 0.01, "cobro": 0.02, "entrega": 0.02})
    assert sorted(e["id"] for e in r["eventos"] if e["tipo"] == "fin") == list(range(1, 11))


def test_generar_hasta_100_mesas():
    lote = generar("mixto", 1.0, OPS, n=100, llegadas="escalonadas")
    assert len(lote) == 100 and len({s["usuario"] for s in lote}) == 100   # nombres únicos
    assert lote[-1]["llegada"] < 15                                          # llegan en pocos segundos
    assert sum(s["duracion"] for s in lote) == pytest.approx(100.0, abs=0.05)


def test_red_30_meseros_atienden_100_mesas(meseros):
    meseros(30)
    r = motor.ejecutar("paralelo", generar("io", 0.05, OPS, n=100), red=True)
    assert r["trabajadores"] == 30 and r["nodos_usados"] == 30
    assert sorted(e["id"] for e in r["eventos"] if e["tipo"] == "fin") == list(range(1, 101))
