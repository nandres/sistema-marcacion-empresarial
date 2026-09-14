FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# OpenCV necesita las bibliotecas de sistema aunque el contenedor no tenga
# cámara: el módulo se importa igual al arrancar el servidor.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY data/ /app/data/

# El proceso no necesita privilegios: una ejecución como root convierte
# cualquier lectura de archivo arbitraria en acceso total al contenedor.
RUN useradd --system --create-home --uid 10001 marcacion \
    && mkdir -p /app/reportes \
    && chown -R marcacion:marcacion /app
USER marcacion

WORKDIR /app/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" || exit 1

# Las migraciones corren una sola vez, antes de levantar los workers: el DDL
# toma locks exclusivos y cuatro procesos aplicándolo a la vez se bloquean
# entre sí.
CMD ["sh", "-c", "python migrate.py && gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:${PORT:-8000} web_server:app"]
