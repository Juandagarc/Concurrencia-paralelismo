"""Punto de entrada:  python -m servidor  [--puerto 8000] [--host 127.0.0.1] [--nodos-lan]"""

import argparse

from .web import iniciar

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Laboratorio de concurrencia y paralelismo")
    p.add_argument("--host", default="127.0.0.1",
                   help="Usa 0.0.0.0 para verlo desde otros equipos de la red")
    p.add_argument("--puerto", type=int, default=8000)
    p.add_argument("--red", action="store_true",
                   help="versión web, siempre distribuida: atienden los meseros que entran "
                        "desde el navegador y solo el coordinador (COORDINADOR_CLAVE) ejecuta")
    p.add_argument("--nodos-lan", action="store_true",
                   help="abre el puerto del coordinador a la red local para nodos de Python "
                        "(usa pickle con clave: solo en una red de confianza)")
    a = p.parse_args()
    iniciar(a.host, a.puerto, a.nodos_lan, a.red)
