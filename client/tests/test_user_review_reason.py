"""`user approve` and `user block` record why a review request was closed (club_cli#7)."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

USER = {"username": "alice", "status": "active"}


@pytest.mark.parametrize("verb", ["approve", "block"])
def test_issue_7_reason_is_sent_as_resolution_reason(monkeypatch, verb):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"/v1/users/by_id/alice/{verb}", USER)

    res = run("user", verb, "alice", "--reason", "Documents checked")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"/v1/users/by_id/alice/{verb}")
    assert call.json == {"resolutionReason": "Documents checked"}


@pytest.mark.parametrize("verb", ["approve", "block"])
def test_issue_7_without_reason_no_body_is_sent(monkeypatch, verb):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"/v1/users/by_id/alice/{verb}", USER)

    res = run("user", verb, "alice")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"/v1/users/by_id/alice/{verb}")
    assert call.json is None


@pytest.mark.parametrize("verb", ["approve", "block"])
def test_issue_7_help_says_what_the_reason_is_attached_to(verb):
    res = CliRunner().invoke(main, ["user", verb, "--help"])
    text = " ".join(res.output.split())  # click wraps the help

    assert res.exit_code == 0
    assert "--reason" in text
    assert "Optional" in text
    assert "closing note on the user's open review request" in text
    assert "500 characters" in text
