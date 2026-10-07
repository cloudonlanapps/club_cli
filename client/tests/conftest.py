"""Shared pytest fixtures for the integration suite.

Most tests run offline against ``fakes.FakeHttp``. The ``test_live_*`` modules
and the endpoint contract run against a real server (the `just`-managed stack),
configured with:

  CLUB_API_BASE_URL     default http://127.0.0.1:8400
  CLUB_USERNAME         default 'sudo'
  CLUB_PASSWORD         required for live tests
  CLUB_REQUIRE_SERVER   '1' turns "no server" from a skip into a failure;
                        `just test` sets it, so a stack that failed to start
                        cannot pass as a green run of skipped tests
  CLUB_EXPECT_MODULES   'on' | 'off': what the stack's optional modules are
                        expected to be, checked against `capabilities`
  CLUB_LIVE_WRITES      '1' lets the test_live_* modules run. They create
                        users, events, uploads — and briefly hand super admin
                        to another user — so they are for a throwaway stack;
                        `just test` sets it, `just test_to` does not

Without CLUB_REQUIRE_SERVER, a missing server skips the live tests so offline
runs stay green.

Tests that mean something in one module mode only are marked `modules_on` or
`modules_off`. With CLUB_EXPECT_MODULES set, the other mode's tests are
deselected at collection, so a run reports what it did not run as
deselected rather than skipped.
"""
from __future__ import annotations

import os

import httpx
import pytest

from .live import Live


BASE_URL = os.environ.get("CLUB_API_BASE_URL", "http://127.0.0.1:8400").rstrip("/")
USERNAME = os.environ.get("CLUB_USERNAME", "sudo")
PASSWORD = os.environ.get("CLUB_PASSWORD")
REQUIRE_SERVER = os.environ.get("CLUB_REQUIRE_SERVER") == "1"
EXPECT_MODULES = os.environ.get("CLUB_EXPECT_MODULES")
LIVE_WRITES = os.environ.get("CLUB_LIVE_WRITES") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect the other module mode's tests when the stack's mode is declared."""
    if EXPECT_MODULES not in ("on", "off"):
        return
    other = "modules_off" if EXPECT_MODULES == "on" else "modules_on"
    dropped = [item for item in items if item.get_closest_marker(other)]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = [item for item in items if not item.get_closest_marker(other)]


def unavailable(reason: str) -> None:
    """Skip, or fail when the run was promised a server."""
    if REQUIRE_SERVER:
        pytest.fail(f"CLUB_REQUIRE_SERVER=1 but {reason}")
    pytest.skip(reason)


def _server_reachable() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
    except httpx.HTTPError:
        return False
    return r.status_code == 200


@pytest.fixture(scope="session")
def server_config() -> dict[str, str]:
    """Single source of truth for server target + credentials."""
    if not PASSWORD:
        unavailable("CLUB_PASSWORD not set")
    if not _server_reachable():
        unavailable(f"no server at {BASE_URL}")
    return {"base_url": BASE_URL, "username": USERNAME, "password": PASSWORD}


@pytest.fixture(scope="session")
def live(server_config: dict[str, str]) -> Live:
    """The CLI, driven in-process against the live server."""
    if not LIVE_WRITES:
        pytest.skip("live tests write data; run them on a throwaway stack (`just test`) or set CLUB_LIVE_WRITES=1")
    session = Live(server_config["base_url"], server_config["username"], server_config["password"])
    session.caps = session.ok("capabilities")
    return session
