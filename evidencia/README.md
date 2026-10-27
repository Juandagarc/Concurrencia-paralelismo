# Evidencia de las mediciones

Cada carpeta guarda una corrida completa, con la fecha y el equipo en el nombre.
Los resultados dependen del equipo (núcleos, sistema, versión de Python): ver `equipo.json`.

| Archivo | Contenido |
| --- | --- |
| `resumen.csv` | Promedio, desviación y aceleración por modelo y carga (`medir.py`) |
| `mediciones.csv` | Cada repetición por separado |
| `barrido.csv` | Aceleración con 1, 2, 4 y 8 trabajadores, y el modo tiempo real (`barrido.py`) |
| `equipo.json` | Equipo de prueba: sistema, núcleos, Python, GIL, calibración |
| `eventos_<carga>_<modelo>.json` | Todos los eventos reales de la última repetición |
| `graficas/*.png` | Tiempos por modelo y líneas de tiempo (Gantt) por carga (`graficas.py`) |
| `*.txt` | Salida de consola de cada script, incluida `demo_carrera.py` |

Para agregar las mediciones de su propio equipo:

```bash
python medir.py --repeticiones 3 --duracion 0.5 --trabajadores 4 --escenarios io cpu mixto
python barrido.py
python graficas.py
```

y copien `resultados/` a una carpeta nueva aquí, por ejemplo `evidencia/AAAA-MM-DD_portatil-jorge_8-nucleos/`.
