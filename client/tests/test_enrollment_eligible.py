"""Enrolment rows say whether the member still meets the event's eligibility (club_server#19)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run


def _row(membername: str, eligible: bool) -> dict:
    return {"id": 1, "eventId": 7, "membername": membername, "status": "assigned", "isTrial": False, "eligible": eligible}


def test_issue_5_enrollment_list_shows_eligible(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/events/by_id/7/enrollments", {
        "enrollments": {"m1": "assigned", "m2": "assigned"},
        "records": [_row("m1", True), _row("m2", False)],
    })

    res = run("enrollment", "list", "7")

    assert res.exit_code == 0, res.output
    assert {r["membername"]: r["eligible"] for r in json.loads(res.output)["records"]} == {"m1": True, "m2": False}


def test_issue_5_myevents_enrollment_shows_eligible(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/myevents/by_id/sudo/7/enrollments", _row("sudo", False))

    res = run("myevents", "enrollment", "7")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["eligible"] is False


@pytest.mark.parametrize("command", [("enrollment", "list"), ("myevents", "enrollment")])
def test_issue_5_enrolment_help_describes_eligible(command):
    res = CliRunner().invoke(main, [*command, "--help"])
    text = " ".join(res.output.split())  # click wraps the help

    assert res.exit_code == 0
    assert "eligible" in text
    assert "no longer meets" in text
    assert "Nobody is removed automatically" in text


@pytest.mark.parametrize("command", [("enrollment", "list"), ("notifications", "list")])
def test_issue_5_help_names_the_member_ineligible_notification(command):
    res = CliRunner().invoke(main, [*command, "--help"])

    assert res.exit_code == 0
    assert "enrollment.member_ineligible" in " ".join(res.output.split())
