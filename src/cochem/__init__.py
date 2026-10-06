"""CoChem root namespace package.

Uses ``pkgutil.extend_path`` so that ``cochem`` sub-packages distributed across
several source roots (for example ``src/`` layouts in sibling repositories) are
merged into a single importable namespace.
"""

import os
from pkgutil import extend_path

# Pre-set HDF5 lock bypass prior to importing h5py across all entrypoints [AC1]
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

__path__ = extend_path(__path__, __name__)

__version__ = "4.2.1"
