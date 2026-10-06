"""CoChem unified core package."""

import os
from pathlib import Path
from pkgutil import extend_path
import sys

# Record whether h5py was imported before cochem package initialization [AC1, AC6]
if not hasattr(sys, "_cochem_h5py_preloaded"):
    sys._cochem_h5py_preloaded = "h5py" in sys.modules

# Pre-set HDF5 lock bypass prior to importing h5py across all entrypoints [AC1]
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

__path__ = extend_path(__path__, __name__)

_src_cochem = Path(__file__).resolve().parent.parent / "src" / "cochem"
if _src_cochem.is_dir():
    if str(_src_cochem) in __path__:
        __path__.remove(str(_src_cochem))
    __path__.insert(0, str(_src_cochem))

__version__ = "4.2.4"

__all__ = ["__version__"]
