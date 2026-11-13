"""Bridge to the shared partner operation registry in the Lambda source tree.

The generator runs with ``PYTHONPATH=source`` (``make seed-data``), where ``shared.auth`` is not
importable; the Lambda packages and tests resolve it from ``source/lambda``. Re-export from the
single definition so seeded ``allowed_endpoints`` can never drift from what the gateway and the
Lambda allow-list filter enforce.
"""

import sys
from pathlib import Path

_LAMBDA_SRC = str(Path(__file__).resolve().parents[1] / "lambda")
if _LAMBDA_SRC not in sys.path:
    sys.path.append(_LAMBDA_SRC)

from shared.auth.partner_operations import KEY_GATED_OPERATION_IDS  # noqa: E402

__all__ = ["KEY_GATED_OPERATION_IDS"]
