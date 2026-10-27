"""Generación del lote de 10 solicitudes que recibe el servidor."""

from __future__ import annotations

import random

USUARIOS = [
    "Ana", "Bruno", "Carla", "Diego", "Elena",
    "Felipe", "Gabriela", "Hugo", "Isabel", "Julián",
]

# Menú de la cafetería: cada producto tarda distinto (factor sobre la duración elegida).
# Las bebidas se preparan en la máquina (carga de CPU); la comida se espera en la
# cocina (E/S, como una consulta a una base de datos).
MENU_CPU = [("Espresso", 0.5), ("Americano", 0.8), ("Capuchino", 1.2),
            ("Latte", 1.4), ("Mocaccino", 1.6)]
MENU_IO = [("Croissant", 0.6), ("Empanada", 0.9), ("Arepa", 1.0),
           ("Sándwich", 1.3), ("Brownie caliente", 1.5)]
VARIACION = 0.15         # ±15 %: el mismo producto nunca sale exactamente igual

ESCENARIOS = ("io", "cpu", "mixto")
LLEGADAS = ("juntas", "escalonadas")
SEPARACION_MEDIA = 0.6   # segundos promedio entre un cliente y el siguiente (escalonadas)


def generar(escenario: str, duracion: float, ops_por_seg: int, n: int = 10,
            llegadas: str = "juntas", semilla: int = 7) -> list[dict]:
    """Crea `n` solicitudes.

    escenario: "io"    -> todas esperan (consulta a base de datos, red, disco)
               "cpu"   -> todas calculan (uso intensivo del procesador)
               "mixto" -> se alternan
    duracion:  segundos que tarda, en promedio, una solicitud atendida sola.
    ops_por_seg: resultado de trabajo.calibrar(), para convertir segundos en
                 iteraciones de cálculo en este equipo.
    llegadas:  "juntas"      -> los 10 clientes llegan en t = 0
               "escalonadas" -> llegan de a uno, con separaciones aleatorias
                                (exponenciales, como en una fila real). La semilla
                                fija hace que el patrón sea el mismo en cada prueba,
                                para comparar los modelos en igualdad de condiciones.
    """
    if escenario not in ESCENARIOS:
        raise ValueError(f"Escenario desconocido: {escenario}")
    if llegadas not in LLEGADAS:
        raise ValueError(f"Tipo de llegada desconocido: {llegadas}")
    azar = random.Random(semilla)
    t = 0.0
    lote = []
    factores = []
    for i in range(n):
        if llegadas == "escalonadas" and i > 0:
            t += min(1.5, max(0.1, azar.expovariate(1 / SEPARACION_MEDIA)))
        if escenario == "mixto":
            trabajo = "io" if i % 2 == 0 else "cpu"
        else:
            trabajo = escenario
        producto, base = azar.choice(MENU_IO if trabajo == "io" else MENU_CPU)
        factores.append(base * azar.uniform(1 - VARIACION, 1 + VARIACION))
        lote.append({
            "id": i + 1,
            "usuario": USUARIOS[i % len(USUARIOS)],
            "producto": producto,
            "trabajo": trabajo,
            "llegada": round(t, 3),
        })
    # Se normaliza para que el promedio sea 1: diez pedidos suman 10 × duración,
    # y así el tiempo secuencial ideal no depende de qué se pidió.
    escala = n / sum(factores)
    for s, f in zip(lote, factores):
        s["duracion"] = round(duracion * f * escala, 3)
        s["iteraciones"] = int(s["duracion"] * ops_por_seg)
    return lote
