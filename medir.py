"""Mediciones definitivas para el documento y la presentación.

Ejecuta los 3 modelos × los escenarios elegidos, repite cada prueba y guarda:

    resultados/mediciones.csv   una fila por repetición
    resultados/resumen.csv      promedios y aceleración (speedup)
    resultados/equipo.json      características del equipo de prueba
    resultados/eventos_<escenario>_<modo>.json   eventos de la última repetición

Uso:
    python medir.py
    python medir.py --repeticiones 5 --duracion 1 --trabajadores 4
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
from datetime import datetime
from pathlib import Path

from servidor import motor
from servidor.solicitudes import ESCENARIOS, generar
from servidor.trabajo import calibrar

SALIDA = Path(__file__).resolve().parent / "resultados"
NOMBRES = {"io": "E/S (espera)", "cpu": "CPU (cálculo)", "mixto": "Mixto"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repeticiones", type=int, default=3)
    p.add_argument("--duracion", type=float, default=0.5,
                   help="segundos promedio por solicitud (default 0.5)")
    p.add_argument("--trabajadores", type=int, default=os.cpu_count() or 4,
                   help="hilos / procesos del pool (default: núcleos del equipo)")
    p.add_argument("--escenarios", nargs="+", default=["io", "cpu"],
                   choices=ESCENARIOS)
    a = p.parse_args()

    SALIDA.mkdir(exist_ok=True)
    print("Calibrando CPU…")
    ops = calibrar()
    equipo = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "sistema": f"{platform.system()} {platform.release()}",
        "procesador": platform.processor() or platform.machine(),
        "nucleos_logicos": os.cpu_count(),
        "python": platform.python_version(),
        "gil_activo": motor.gil_activo(),
        "iteraciones_por_segundo": ops,
        "duracion_por_solicitud": a.duracion,
        "trabajadores": a.trabajadores,
        "repeticiones": a.repeticiones,
    }
    (SALIDA / "equipo.json").write_text(json.dumps(equipo, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    print(f"{equipo['nucleos_logicos']} núcleos · Python {equipo['python']} · "
          f"{a.trabajadores} trabajadores · {a.repeticiones} repeticiones\n")

    filas, resumen = [], []
    for esc in a.escenarios:
        lote = generar(esc, a.duracion, ops)
        base = None
        for modo in motor.MODOS:
            tiempos, respuestas = [], []
            for rep in range(1, a.repeticiones + 1):
                r = motor.ejecutar(modo, lote, a.trabajadores)
                tiempos.append(r["total"])
                respuestas.append(r["respuesta_promedio"])
                filas.append({"escenario": esc, "modo": modo, "repeticion": rep,
                              "trabajadores": r["trabajadores"], "total_s": r["total"],
                              "respuesta_promedio_s": r["respuesta_promedio"],
                              "arranque_s": r["arranque"],
                              "procesos_usados": r["procesos_usados"],
                              "nucleos_usados": len(r["nucleos_usados"])})
                print(f"  {NOMBRES[esc]:14} {modo:12} rep {rep}: {r['total']:.3f} s")
            (SALIDA / f"eventos_{esc}_{modo}.json").write_text(
                json.dumps({"resumen": {k: v for k, v in r.items() if k != "eventos"},
                            "eventos": r["eventos"]}, ensure_ascii=False),
                encoding="utf-8")
            prom = statistics.mean(tiempos)
            base = base or prom
            resumen.append({
                "escenario": esc, "modo": modo, "trabajadores": r["trabajadores"],
                "total_promedio_s": round(prom, 4),
                "desviacion_s": round(statistics.pstdev(tiempos), 4),
                "respuesta_promedio_s": round(statistics.mean(respuestas), 4),
                "aceleracion": round(base / prom, 2),
            })
        print()

    for nombre, datos in (("mediciones.csv", filas), ("resumen.csv", resumen)):
        with open(SALIDA / nombre, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(datos[0]))
            w.writeheader()
            w.writerows(datos)

    print(f"{'Escenario':15} {'Modo':12} {'Total (s)':>10} {'Aceleración':>12}")
    print("-" * 52)
    for r in resumen:
        print(f"{NOMBRES[r['escenario']]:15} {r['modo']:12} "
              f"{r['total_promedio_s']:>10.3f} {r['aceleracion']:>11.2f}x")
    print(f"\nResultados en {SALIDA}  ·  siguiente paso: python graficas.py")


if __name__ == "__main__":
    main()
