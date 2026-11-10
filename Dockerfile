# Cafetería distribuida: servidor web + coordinador + nodos locales en un contenedor.
# Los navegadores se suman como nodos por WebSocket en /ws/nodo (mismo puerto 8000).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Solo psutil: matplotlib es para las gráficas del documento, no para el servidor
RUN pip install --no-cache-dir "psutil>=5.9"

COPY servidor/ servidor/
COPY web/ web/
RUN mkdir -p resultados && useradd --create-home cafeteria && chown -R cafeteria /app
USER cafeteria

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/info', timeout=4)"

# El puerto del coordinador (50500, pickle con clave) queda solo dentro del contenedor
CMD ["python", "-m", "servidor", "--host", "0.0.0.0", "--puerto", "8000"]
