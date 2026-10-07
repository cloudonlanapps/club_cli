"""users create [--guest] (club_cli_old#15)."""
from __future__ import annotations

import json

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run

SEED = {
    "username": "guest_x", "password": "pw", "firstName": "X", "lastName": "Y", "gender": "female",
    "dateOfBirth": "1990-01-01T00:00:00",
    "profile": {"nickname": "X", "useNamePublicly": True, "bio": "b", "achievements": "a"},
    "identity-document": [],
    "staffListing": {"guest": True, "position": 5},
}
EXPECTED = {
    "username": "guest_x", "passwordHash": "pw", "firstName": "X", "lastName": "Y", "gender": "female",
    "dateOfBirthUtc": local_iso_to_utc_ms("1990-01-01T00:00:00"),
    "nickname": "X", "useNamePublicly": True, "bio": "b", "achievements": "a",
}


def test_issue_15_users_create_merges_profile_and_sends_admin_body(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/users", {"username": "guest_x", "isGuest": False}, status=201)

    res = run("users", "create", json.dumps(SEED))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/users")[0].json == EXPECTED


def test_issue_15_users_create_guest_flag(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/users", {"username": "guest_x", "isGuest": True}, status=201)

    res = run("users", "create", json.dumps(SEED), "--guest")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/users")[0].json == {**EXPECTED, "isGuest": True}


def test_issue_15_users_create_help_explains_guest():
    out = run("users", "create", "--help").output
    assert "--guest" in out and "coach" in out
