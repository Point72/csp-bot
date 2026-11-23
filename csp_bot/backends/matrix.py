"""Matrix backend integration using chatom.

This module provides Matrix-specific types and adapters through chatom.
"""

__all__ = (
    "MatrixAdapter",
    "MatrixConfig",
    "MatrixMessage",
    "MatrixUser",
)

try:
    from chatom.matrix import (
        MatrixConfig,
        MatrixMessage,
        MatrixUser,
    )
    from csp_adapter_matrix import MatrixAdapter
except ImportError:
    from chatom import Message as ChatomMessage, User as ChatomUser
    from pydantic import BaseModel

    class MatrixConfig(BaseModel):
        """Placeholder when chatom.matrix is not available."""

    MatrixAdapter = None
    MatrixMessage = ChatomMessage
    MatrixUser = ChatomUser
