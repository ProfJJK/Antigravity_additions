FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends libxrender1 libxext6 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir rdkit

RUN groupadd -g 1000 cochem && useradd -u 1000 -g cochem -m cochem
RUN mkdir -p /workspace/data && chown -R 1000:1000 /workspace && chmod -R 775 /workspace

WORKDIR /workspace
VOLUME ["/workspace"]

USER 1000
