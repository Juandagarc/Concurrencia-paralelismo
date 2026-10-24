"""Generación del lote de 10 solicitudes que recibe el servidor."""

from __future__ import annotations

USUARIOS = [
    "Ana", "Bruno", "Carla", "Diego", "Elena",
    "Felipe", "Gabriela", "Hugo", "Isabel", "Julián",
]

# Cada solicitud dura un poco distinto, como en la vida real.
# Suman exactamente 10, así el tiempo secuencial ideal es 10 × duración.
FACTORES = [1.0, 0.6, 1.4, 0.8, 1.2, 0.7, 1.3, 0.9, 1.1, 1.0]

ESCENARIOS = ("io", "cpu", "mixto")


def generar(escenario: str, duracion: float, ops_por_seg: int, n: int = 10) -> list[dict]:
    """Crea `n` solicitudes.

    escenario: "io"    -> todas esperan (consulta a base de datos, red, disco)
               "cpu"   -> todas calculan (uso intensivo del procesador)
               "mixto" -> se alternan
    duracion:  segundos que tarda, en promedio, una solicitud atendida sola.
    ops_por_seg: resultado de trabajo.calibrar(), para convertir segundos en
                 iteraciones de cálculo en este equipo.
    """
    if escenario not in ESCENARIOS:
        raise ValueError(f"Escenario desconocido: {escenario}")
    lote = []
    for i in range(n):
        if escenario == "mixto":
            trabajo = "io" if i % 2 == 0 else "cpu"
        else:
            trabajo = escenario
        d = round(duracion * FACTORES[i % len(FACTORES)], 3)
        lote.append({
            "id": i + 1,
            "usuario": USUARIOS[i % len(USUARIOS)],
            "trabajo": trabajo,
            "duracion": d,
            "iteraciones": int(d * ops_por_seg),
        })
    return lote
