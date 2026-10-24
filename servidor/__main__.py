"""Punto de entrada:  python -m servidor  [--puerto 8000] [--host 127.0.0.1]"""

import argparse

from .web import iniciar

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Laboratorio de concurrencia y paralelismo")
    p.add_argument("--host", default="127.0.0.1",
                   help="Usa 0.0.0.0 para verlo desde otros equipos de la red")
    p.add_argument("--puerto", type=int, default=8000)
    a = p.parse_args()
    iniciar(a.host, a.puerto)
