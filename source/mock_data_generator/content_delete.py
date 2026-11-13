"""Bridge to the shared content soft delete in the Lambda source tree.

The generator runs with ``PYTHONPATH=source`` (``make seed-data``), where ``shared`` is not
importable; the Lambda packages and tests resolve it from ``source/lambda``. Re-export from the
single definition so the seed step's clear emits exactly the ``content.deleted`` event the
dashboard backend emits, and the deindexer completes both the same way.
"""

import sys
from pathlib import Path

_LAMBDA_SRC = str(Path(__file__).resolve().parents[1] / "lambda")
if _LAMBDA_SRC not in sys.path:
    sys.path.append(_LAMBDA_SRC)

from shared.content_delete import ContentNotFound, soft_delete  # noqa: E402

__all__ = ["ContentNotFound", "soft_delete"]
