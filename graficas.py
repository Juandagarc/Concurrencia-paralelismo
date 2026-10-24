"""Genera las gráficas para el documento y la presentación a partir de medir.py.

    resultados/graficas/tiempos.png         tiempo total por modo y escenario
    resultados/graficas/gantt_<esc>.png     línea de tiempo real de cada solicitud

Uso:  python graficas.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RES = Path(__file__).resolve().parent / "resultados"
OUT = RES / "graficas"
MODOS = ["secuencial", "concurrente", "paralelo"]
COLOR_MODO = {"secuencial": "#64748b", "concurrente": "#0ea5e9", "paralelo": "#f97316"}
NOMBRES = {"io": "E/S (espera)", "cpu": "CPU (cálculo)", "mixto": "Mixto"}
PALETA = ["#0ea5e9", "#f97316", "#22c55e", "#a855f7", "#eab308",
          "#ec4899", "#14b8a6", "#ef4444", "#6366f1", "#84cc16"]


def barras() -> None:
    with open(RES / "resumen.csv", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    escenarios = list(dict.fromkeys(r["escenario"] for r in filas))
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    ancho = 0.26
    for i, modo in enumerate(MODOS):
        vals = [next(float(r["total_promedio_s"]) for r in filas
                     if r["escenario"] == e and r["modo"] == modo) for e in escenarios]
        acel = [next(float(r["aceleracion"]) for r in filas
                     if r["escenario"] == e and r["modo"] == modo) for e in escenarios]
        xs = [j + (i - 1) * ancho for j in range(len(escenarios))]
        barras_ = ax.bar(xs, vals, ancho, label=modo.capitalize(), color=COLOR_MODO[modo])
        for b, v, s in zip(barras_, vals, acel):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f} s\n{s:.1f}×",
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(escenarios)), [NOMBRES[e] for e in escenarios])
    ax.set_ylabel("Tiempo total para 10 solicitudes (s)")
    ax.set_title("Tiempo total por modelo de ejecución (menos es mejor)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    ax.margins(y=0.2)
    fig.tight_layout()
    fig.savefig(OUT / "tiempos.png")
    plt.close(fig)


def gantt(escenario: str) -> None:
    fig, ejes = plt.subplots(3, 1, figsize=(9, 7.5), dpi=150, sharex=True)
    tmax = 0.0
    for ax, modo in zip(ejes, MODOS):
        ruta = RES / f"eventos_{escenario}_{modo}.json"
        if not ruta.exists():
            continue
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        ev = datos["eventos"]
        ini = {e["id"]: e for e in ev if e["tipo"] == "inicio"}
        fin = {e["id"]: e for e in ev if e["tipo"] == "fin"}
        trabajadores = list(dict.fromkeys(e["trabajador"] for e in ini.values()))
        for sid in sorted(ini):
            i, f = ini[sid], fin[sid]
            color = PALETA[trabajadores.index(i["trabajador"]) % len(PALETA)]
            ax.barh(sid, i["t"], left=0, color="#e2e8f0", height=0.6)
            ax.barh(sid, f["t"] - i["t"], left=i["t"], color=color, height=0.6)
            tmax = max(tmax, f["t"])
        ax.set_yticks(range(1, 11))
        ax.invert_yaxis()
        ax.set_ylabel("Solicitud")
        total = datos["resumen"]["total"]
        ax.set_title(f"{modo.capitalize()} · {total:.2f} s · "
                     f"{len(trabajadores)} trabajadores", loc="left", fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
    ejes[-1].set_xlabel("Tiempo desde que llegan las solicitudes (s)  ·  "
                        "gris = en cola, color = trabajador que la atiende")
    for ax in ejes:
        ax.set_xlim(0, tmax * 1.03)
    fig.suptitle(f"Línea de tiempo real · escenario {NOMBRES[escenario]}", fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / f"gantt_{escenario}.png")
    plt.close(fig)


def main() -> None:
    if not (RES / "resumen.csv").exists():
        raise SystemExit("Primero ejecuta:  python medir.py")
    OUT.mkdir(parents=True, exist_ok=True)
    barras()
    for esc in sorted({p.stem.split("_")[1] for p in RES.glob("eventos_*_*.json")}):
        gantt(esc)
    print(f"Gráficas guardadas en {OUT}")


if __name__ == "__main__":
    main()
