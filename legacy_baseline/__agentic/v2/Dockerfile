# syntax=docker/dockerfile:1
# CoChem Pipeline v2 Worker Base Image
# read-only rootfs posture except /workspace

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir rdkit

RUN useradd -u 1000 -m -s /bin/bash cochem

RUN mkdir -p /workspace/data /opt/cochem/healthroot \
    && echo "OK" > /opt/cochem/healthroot/healthz \
    && chown -R 1000:1000 /workspace /opt/cochem

WORKDIR /workspace

USER 1000
