"""`capabilities` reports the club's country calling code (club_server#15)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

MODULES = {"creditSystem": True, "evaluations": True, "eventMarketing": True, "identityVerification": True}


@pytest.mark.parametrize("code", ["91", None])
def test_issue_2_capabilities_prints_default_country_code(monkeypatch, code):
    FakeHttp().on("GET", "/v1/capabilities", {**MODULES, "defaultCountryCode": code}).install(monkeypatch)

    res = run("capabilities")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["defaultCountryCode"] == code


def test_issue_2_capabilities_help_describes_default_country_code():
    res = CliRunner().invoke(main, ["capabilities", "--help"])

    text = " ".join(res.output.split())  # click wraps the help
    assert res.exit_code == 0
    assert "defaultCountryCode" in text
    assert "country calling code" in text
    assert "null when the deployment sets none" in text
