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
