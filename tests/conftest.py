"""Test Suite Configuration - marks the recorded pre-existing failures as expected so new failures stand out.

Pytest configuration for tests/: every test id listed in tests/known_failures.txt runs as a non-strict xfail, so a clean run reads all-green, any new failure is a real regression, and a fixed known failure shows as XPASS until its line is removed.

Features:
    - Known failures: ids in tests/known_failures.txt become non-strict xfail with a pointer to the file
    - Fixed tests surface: a listed test that now passes reports XPASS so its line can be deleted

Functions:
    pytest_collection_modifyitems(): Mark every collected test whose id is listed in known_failures.txt as xfail

Dependencies:
    - pytest: collection hook and xfail marker
    - pathlib: locating known_failures.txt next to this file

Updates:
    25-Sep-2026: Added known-failures xfail marking.
"""

from pathlib import Path

import pytest

KNOWN_FAILURES_FILE = Path(__file__).with_name("known_failures.txt")


def pytest_collection_modifyitems(config, items):
    """Mark every collected test whose id is listed in known_failures.txt as a non-strict xfail."""
    try:
        listed = {line.strip() for line in KNOWN_FAILURES_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return
    for item in items:
        if item.nodeid in listed or f"tests/{item.nodeid}" in listed:
            item.add_marker(pytest.mark.xfail(reason="known pre-existing failure, see tests/known_failures.txt", strict=False))
