"""Resource-root resolution for the legacy Python implementation.

The active runtime is the C++ binary in ``cpp/``.  This helper keeps the legacy
Python entry points working both from the repository layout (``python/`` with
``web/`` and ``image/`` one level up) and from a flat deployment directory such
as ``/root/python-dump``.
"""

import os


def project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isdir(os.path.join(here, "web")):
        return here
    parent = os.path.dirname(here)
    if os.path.isdir(os.path.join(parent, "web")):
        return parent
    return here
