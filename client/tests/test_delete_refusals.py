"""Hard delete and restore show the server's wrong-state refusal (club_cli_old#55).

club_server#526 refuses a hard delete of a live item with HARD_DELETE_NEEDS_SOFT_DELETE
and a restore of an item that is not deleted with NOTHING_TO_RESTORE.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from club_admin.cli import main as admin_main

from .fakes import FakeHttp, run


def refusal(code: str, message: str) -> dict:
    return {"detail": {"code": code, "message": message}}


def test_issue_55_hard_delete_prints_the_refusal(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    body = refusal("HARD_DELETE_NEEDS_SOFT_DELETE", "Venue 3 must be soft-deleted before it is hard-deleted")
    fake.on("DELETE", "/v1/venues/by_id/3/hard", body, status=409)

    res = CliRunner().invoke(admin_main, ["-u", "sudo", "--pw", "x", "venue", "hard-delete", "3"])

    assert res.exit_code == 1, res.output
    assert json.loads(res.output) == body


def test_issue_55_restore_prints_the_refusal(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    body = refusal("NOTHING_TO_RESTORE", "Venue 3 is not deleted; there is nothing to restore")
    fake.on("POST", "/v1/venues/by_id/3/restore", body, status=409)

    res = run("trash", "restore", "venue", "3")

    assert res.exit_code == 1, res.output
    assert json.loads(res.stdout) == body
