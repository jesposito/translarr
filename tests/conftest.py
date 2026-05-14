"""Session-wide pytest fixtures.

The Settings page work added a runtime override layer that mutates the
shared ``server.config.settings`` instance. Without this autouse
fixture, a test that calls ``set_override`` (or PATCHes /config) leaks
the override into every subsequent test in the same pytest run, since
the module-level ``settings`` object is a singleton.

This hook resets every registered field back to the env-baseline at the
end of each test. The DB-level temp fixtures still own per-test isolation
of SQLite rows.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_settings_after_each_test():
    yield
    from server.settings_store import reset_settings_for_tests

    reset_settings_for_tests()
