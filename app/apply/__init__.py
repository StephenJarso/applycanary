"""Submission backends.

Importing this package registers every submitter.
"""

from __future__ import annotations

from app.apply import manual, smartrecruiters  # noqa: F401,E402
from app.apply.base import (  # noqa: F401
    BaseSubmitter,
    GateResult,
    SubmitGate,
    SubmitResult,
    auto_submittable_platforms,
    get_submitter,
    register_submitter,
)

__all__ = [
    "BaseSubmitter", "GateResult", "SubmitGate", "SubmitResult",
    "auto_submittable_platforms", "get_submitter", "register_submitter",
]
