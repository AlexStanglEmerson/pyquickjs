"""Pytest configuration and fixtures for pyquickjs tests."""

import pytest

from pyquickjs.runtime import JSRuntime
from pyquickjs.context import JSContext


@pytest.fixture
def runtime():
    """Create a fresh JSRuntime for each test."""
    return JSRuntime()


@pytest.fixture
def ctx(runtime):
    """Create a fresh JSContext for each test."""
    return JSContext(runtime)
