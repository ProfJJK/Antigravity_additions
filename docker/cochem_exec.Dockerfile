# CoChem offline execution sandbox (Task 195.01; SRS Section 2.1, FR-1, AC-19).
#
# Build (from repository root):
#   docker build -f docker/cochem_exec.Dockerfile -t cochem_exec:latest .
# Run (strict offline):
#   docker run --rm --network none cochem_exec:latest python -c "import pytest; import mendeleev; import ase"
#
# All Python dependencies are installed at build time, so the container needs no
# network access at runtime.
# This Dockerfile COPYs nothing from the build context.

FROM python:3.14.7-slim

# Pin manifest, visible through `docker inspect cochem_exec:latest`.
# These labels are not proof on their own. The build-time self-check at the end
# of this file fails the build if any installed version differs from these pins.
LABEL org.cochem.image="cochem_exec" \
      org.cochem.task="195.01" \
      org.cochem.python="3.14.7" \
      org.cochem.pin.numpy="2.4.6" \
      org.cochem.pin.scipy="1.18.0" \
      org.cochem.pin.ase="3.29.0" \
      org.cochem.pin.mendeleev="1.2.0" \
      org.cochem.pin.pydantic="2.13.4" \
      org.cochem.pin.h5py="3.16.0" \
      org.cochem.pin.hdf5plugin="7.1.0" \
      org.cochem.pin.filelock="3.32.2" \
      org.cochem.pin.pytest="9.0.2"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PIP_ROOT_USER_ACTION=ignore

# Pinned scientific stack plus pytest, installed in several small pip
# transactions instead of one large one. A single transaction that unpacked
# ~38 packages (numpy, scipy, pandas, matplotlib, h5py, pillow, ...) crashed the
# pip process with SIGSEGV (exit 139) during "Installing collected packages".
# Smaller transactions reduce peak memory per pip run, and --no-compile skips
# pip's post-unpack bytecode compilation of the large packages (the image sets
# PYTHONDONTWRITEBYTECODE=1 and runs non-root, so .pyc files are not used anyway).
#
# Compiled packages must come from prebuilt wheels. The slim image has no
# compiler, so a missing wheel fails the build immediately instead of
# attempting a source build.

# Layer 1: numpy (base of the compiled stack).
RUN python -m pip install --no-compile \
        --only-binary=numpy \
        numpy==2.4.6

# Layer 2: compiled packages that depend on numpy.
RUN python -m pip install --no-compile \
        --only-binary=numpy,scipy,h5py,hdf5plugin \
        numpy==2.4.6 \
        scipy==1.18.0 \
        h5py==3.16.0 \
        hdf5plugin==7.1.0

# Layer 3: lightweight pure-Python pins and the test runner.
RUN python -m pip install --no-compile \
        filelock==3.32.2 \
        pydantic==2.13.4 \
        pytest==9.0.2

# Layer 4: chemistry libraries. These pull in pandas, matplotlib, sqlalchemy,
# pint, pillow, etc. as dependencies; numpy/scipy/pydantic pins are restated so
# the resolver cannot move them.
RUN python -m pip install --no-compile \
        --only-binary=numpy,scipy,h5py,hdf5plugin \
        numpy==2.4.6 \
        scipy==1.18.0 \
        pydantic==2.13.4 \
        ase==3.29.0 \
        mendeleev==1.2.0

# Dependency consistency check.
RUN python -m pip check

# Standard non-root identity: cochem (UID/GID 1000).
RUN groupadd --gid 1000 cochem \
    && useradd --uid 1000 --gid cochem --create-home --shell /bin/sh cochem

# Writable working directory owned by the non-root user.
# It is created and chowned before privileges are dropped.
WORKDIR /work
RUN chown -R cochem:cochem /work && chmod 775 /work

USER cochem:cochem

ENV HOME=/home/cochem

# Build-time self-check, run as the non-root user.
# Every package must import, the interpreter must be 3.14.7, and installed
# versions must match the pins exactly.
RUN python -c "import importlib.metadata as md, platform; import pytest, mendeleev, ase, numpy, scipy, h5py, pydantic, hdf5plugin, filelock; assert platform.python_version() == '3.14.7', platform.python_version(); expected = {'numpy': '2.4.6', 'scipy': '1.18.0', 'ase': '3.29.0', 'mendeleev': '1.2.0', 'pydantic': '2.13.4', 'h5py': '3.16.0', 'hdf5plugin': '7.1.0', 'filelock': '3.32.2', 'pytest': '9.0.2'}; bad = {k: md.version(k) for k in expected if md.version(k) != expected[k]}; assert not bad, 'version mismatch: %r' % bad; print('cochem_exec pins verified:', expected)"

CMD ["python"]
