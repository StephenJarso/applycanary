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
#
# These are *assignments*, not setdefault: DATABASE_URL/DATA_DIR are commonly
# exported in a shell that runs the live app, and a setdefault would silently
# let the suite point at the real database -- the auth fixture then deletes
# every profile and invite code from production data before the foreign-key
# check stops the user deletion. Tests always run on a throwaway database.
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="applycanary-tests-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TMP_DATA_DIR) / 'test.db'}"
