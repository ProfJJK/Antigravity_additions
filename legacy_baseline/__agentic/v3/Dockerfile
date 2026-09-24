FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# RDKit drawing backends need these X11 render libraries at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir rdkit pytest mendeleev h5py numpy pyyaml

RUN groupadd -g 1000 cochem && useradd -u 1000 -g cochem -m cochem

# Static health root served by the mcp-server service: GET /healthz -> 200 "OK".
# Baked into the image so it stays readable under a read-only rootfs.
RUN mkdir -p /opt/cochem/healthroot /workspace/data \
    && printf 'OK' > /opt/cochem/healthroot/healthz \
    && chown -R 1000:1000 /workspace /opt/cochem \
    && chmod -R 775 /workspace \
    && chmod -R 755 /opt/cochem

WORKDIR /workspace
VOLUME ["/workspace"]

USER 1000
