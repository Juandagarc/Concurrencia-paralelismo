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


@pytest.mark.parametrize("modo", ["concurrente", "paralelo"])
def test_tiempo_real_la_caja_atiende_uno_a_la_vez(modo):
    """La caja es un Lock compartido: dos cobros nunca se solapan."""
    rapidas = {"pedido": 0.05, "camino_caja": 0.02, "cobro": 0.05, "entrega": 0.05}
    r = motor.ejecutar(modo, generar("io", 0.05, OPS), trabajadores=4, realista=True, etapas=rapidas)
    ev = r["eventos"]
    cobro = {e["id"]: e["t"] for e in ev if e.get("etapa") == "caja_cobro"}
    salida = {e["id"]: e["t"] for e in ev if e.get("etapa") == "entrega"}
    assert len(cobro) == 10
    tramos = sorted((cobro[i], salida[i]) for i in cobro)
    assert all(b[0] >= a[1] - 1e-3 for a, b in zip(tramos, tramos[1:]))
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
