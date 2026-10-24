"""Condición de carrera: por qué los hilos necesitan sincronización.

Diez hilos hacen 100 depósitos de $1 cada uno en la misma cuenta.
El saldo final debería ser $1.000.

Sin candado, dos hilos leen el mismo saldo antes de que el otro lo escriba y
uno de los depósitos se pierde. Con un candado (threading.Lock) solo un hilo a
la vez ejecuta la sección crítica leer → sumar → escribir.

Uso:  python demo_carrera.py
"""

from __future__ import annotations

import threading
import time

HILOS = 10
DEPOSITOS = 100


class Cuenta:
    def __init__(self) -> None:
        self.saldo = 0
        self.candado = threading.Lock()

    def depositar_sin_candado(self) -> None:
        leido = self.saldo          # 1. leer
        time.sleep(0)               # el sistema puede cambiar de hilo justo aquí
        self.saldo = leido + 1      # 2. escribir (puede pisar a otro hilo)

    def depositar_con_candado(self) -> None:
        with self.candado:          # sección crítica: un hilo a la vez
            leido = self.saldo
            time.sleep(0)
            self.saldo = leido + 1


def probar(con_candado: bool) -> tuple[int, float]:
    cuenta = Cuenta()
    operacion = cuenta.depositar_con_candado if con_candado else cuenta.depositar_sin_candado

    def cliente() -> None:
        for _ in range(DEPOSITOS):
            operacion()

    hilos = [threading.Thread(target=cliente) for _ in range(HILOS)]
    t = time.perf_counter()
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    return cuenta.saldo, time.perf_counter() - t


def main() -> None:
    esperado = HILOS * DEPOSITOS
    print(f"{HILOS} hilos × {DEPOSITOS} depósitos de $1  →  saldo esperado ${esperado:,}\n")
    for con in (False, True):
        saldo, dt = probar(con)
        perdidos = esperado - saldo
        estado = "correcto" if perdidos == 0 else f"se perdieron {perdidos} depósitos"
        nombre = "Con candado (Lock)" if con else "Sin candado       "
        print(f"{nombre}  saldo ${saldo:>5,}  ·  {estado}  ·  {dt * 1000:.0f} ms")
    print("\nEl candado garantiza el resultado correcto a cambio de que los hilos "
          "esperen su turno en la sección crítica.")


if __name__ == "__main__":
    main()
