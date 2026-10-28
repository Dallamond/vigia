# Imagen de Vigia con CUDA. Para CPU usa:
#   docker build --build-arg BASE_IMAGE=python:3.12-slim -t vigia:cpu .
ARG BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIGIA_HOST=0.0.0.0

# OpenCV necesita libGL; el resto son utilidades mínimas del sistema.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libsm6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Las dependencias se instalan antes de copiar el código para aprovechar caché.
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY . /app

# Usuario sin privilegios. El UID 1000 suele coincidir con el usuario del host;
# si no, arranca con `user: "${UID}:${GID}"` en docker-compose.yml.
RUN groupadd --gid 1000 vigia \
    && useradd --uid 1000 --gid vigia --create-home --shell /bin/bash vigia \
    && chown -R vigia:vigia /app

USER vigia

EXPOSE 8000

# `/` es público (no exige token): sirve para el healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)"

CMD ["python", "-m", "vigia.web"]
