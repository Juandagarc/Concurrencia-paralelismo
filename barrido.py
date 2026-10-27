"""Barrido de trabajadores: cómo cambia el tiempo al poner más meseros.

Para cada modelo (concurrente y paralelo) y cada carga (E/S y CPU) ejecuta las
10 solicitudes con 1, 2, 4 y 8 trabajadores y guarda la aceleración frente al
secuencial. Sirve para ver dónde se estanca el paralelismo (núcleos del equipo)
y el límite de la ley de Amdahl en el modo tiempo real (caja con Lock).

    resultados/barrido.csv

Uso:  python barrido.py [--duracion 0.5] [--repeticiones 2]
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path

from servidor import motor
from servidor.solicitudes import generar
from servidor.trabajo import calibrar

SALIDA = Path(__file__).resolve().parent / "resultados"
TRABAJADORES = (1, 2, 4, 8)


def medir(modo, lote, n, repeticiones, **extra):
    return statistics.mean(motor.ejecutar(modo, lote, n, **extra)["total"]
                           for _ in range(repeticiones))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duracion", type=float, default=0.5)
    p.add_argument("--repeticiones", type=int, default=2)
    a = p.parse_args()
    SALIDA.mkdir(exist_ok=True)
    ops = calibrar()
    filas = []
    for esc in ("io", "cpu"):
        lote = generar(esc, a.duracion, ops)
        base = medir("secuencial", lote, 1, a.repeticiones)
        for modo in ("concurrente", "paralelo"):
            for n in TRABAJADORES:
                t = medir(modo, lote, n, a.repeticiones)
                filas.append({"escenario": esc, "modo": modo, "trabajadores": n,
                              "total_s": round(t, 3), "secuencial_s": round(base, 3),
                              "aceleracion": round(base / t, 2)})
                print(f"{esc:3} {modo:11} {n} trabajadores: {t:6.2f} s  ({base / t:.2f}×)")
    # Tiempo real: la caja (Lock) es la parte secuencial de Amdahl
    lote = generar("io", a.duracion, ops)
    base = medir("secuencial", lote, 1, 1, realista=True)
    for n in TRABAJADORES:
        t = medir("concurrente", lote, n, 1, realista=True)
        filas.append({"escenario": "io_tiempo_real", "modo": "concurrente", "trabajadores": n,
                      "total_s": round(t, 3), "secuencial_s": round(base, 3),
                      "aceleracion": round(base / t, 2)})
        print(f"tiempo real concurrente {n} trabajadores: {t:6.2f} s  ({base / t:.2f}×)")
    with open(SALIDA / "barrido.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    print(f"Guardado en {SALIDA / 'barrido.csv'}")


if __name__ == "__main__":
    main()
