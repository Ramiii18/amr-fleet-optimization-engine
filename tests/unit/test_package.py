"""Smoke test: the package is installed in the active environment and importable.

This checks the environment, not the algorithms. If it fails, look first at which
interpreter is running and whether `pip install -e ".[dev]"` was run in it.
"""

from importlib.metadata import version

import amr_optimizer


def test_package_is_installed_and_reports_its_version() -> None:
    assert amr_optimizer.__version__ == version("amr-fleet-optimization-engine")
    assert amr_optimizer.__version__ != ""
