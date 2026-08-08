"""Source connectors.

Importing this package registers every connector via the @register decorator.
"""

from __future__ import annotations

# Imported for registration side effects.
from app.sources import (  # noqa: F401,E402
    ashby,
    greenhouse,
    lever,
    remoteok,
    smartrecruiters,
)
from app.sources.base import (  # noqa: F401
    BaseSource,
    RawJob,
    all_sources,
    get_source,
    register,
)

__all__ = ["BaseSource", "RawJob", "all_sources", "get_source", "register"]
