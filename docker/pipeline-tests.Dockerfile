# syntax=docker/dockerfile:1
# Build with scripts/build_pipeline_sandbox.py. The emitted immutable image ID
# must be copied into protected docker.image AND docker.allowed_images.
ARG BASE_IMAGE=python:3.12.11-slim-bookworm
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir pytest==8.4.2 && python -m pip check
RUN mkdir -p /work /tmp && chmod 1777 /work /tmp
# All project dependencies must be baked into a reviewed derivative image at
# build time. Tests run offline and have no package-manager network fallback.
LABEL org.cochem.sandbox="4.2.5" org.cochem.execution="offline-tests"
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["python", "-I", "-c", "import time; time.sleep(86400)"]
