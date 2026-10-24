"""Pruebas del simulador.  Ejecutar con:  python -m pytest -q"""

import pytest

from servidor import motor
from servidor.solicitudes import generar
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
    assert round(sum(s["duracion"] for s in lote), 3) == 10.0
