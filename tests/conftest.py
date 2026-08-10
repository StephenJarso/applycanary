"""Pytest bootstrap: make the project root importable as `app.*`."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Redirect the database before anything imports `app.db`, which binds its engine
# to `get_settings().database_url` at import time. The default is a *relative*
# ./data/applycanary.db, so without this the suite mutates the developer's real
# data when that directory exists and dies with "unable to open database file"
# on a clean checkout where it does not -- which is how the auth tests passed
# locally but failed in CI. conftest is imported before any test module, so this
# is the last point at which the choice of database can still be changed.
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="applycanary-tests-")
os.environ.setdefault("DATA_DIR", _TMP_DATA_DIR)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_TMP_DATA_DIR) / 'test.db'}")
