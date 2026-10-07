"""auth register / users register send RegisterRequest and nothing else (club_cli_old#19).

Seed files carry the onboarding follow-ups (profile, identity-document,
staffListing) beside the registration fields; the server refuses unknown
fields since club_server#325, so both commands must strip them.
"""
from __future__ import annotations

import json

from club_client.cli import SEED_ONLY_KEYS, local_iso_to_utc_ms, split_seed_keys

from .fakes import FakeHttp, run

SEED = {
    "username": "coach_x", "password": "pw", "firstName": "X", "lastName": "Y",
    "gender": "male", "dateOfBirth": "1990-01-01T00:00:00", "phone": "+91-1",
    "profile": {"nickname": "X", "bio": "..."},
    "identity-document": ["identity/coach_x/id.png"],
    "staffListing": {"position": 1},
}
REGISTER_BODY = {
    "username": "coach_x", "password": "pw", "firstName": "X", "lastName": "Y",
    "gender": "male", "dateOfBirthUtc": local_iso_to_utc_ms("1990-01-01T00:00:00"), "phone": "+91-1",
}


def test_issue_19_split_seed_keys_returns_user_and_follow_ups():
    user, follow_ups = split_seed_keys(dict(SEED))

    assert set(user) == (set(REGISTER_BODY) - {"dateOfBirthUtc"}) | {"dateOfBirth"}
    assert follow_ups == {
        "profile": {"nickname": "X", "bio": "..."},
        "identity-document": ["identity/coach_x/id.png"],
        "staffListing": {"position": 1},
    }
    assert set(follow_ups) == SEED_ONLY_KEYS


def test_issue_19_auth_register_strips_seed_only_keys(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/auth/register", {"username": "coach_x"}, status=201)

    res = run("auth", "register", json.dumps(SEED))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/auth/register")[0].json == REGISTER_BODY


def test_issue_19_users_register_strips_seed_only_keys(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/auth/register", {"username": "coach_x"}, status=201)

    res = run("users", "register", json.dumps(SEED))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/auth/register")[0].json == REGISTER_BODY


def test_issue_19_users_register_help_names_real_follow_ups():
    out = run("users", "register", "--help").output
    assert "users create" not in out
    assert "--submit-for-review" not in out
    for follow_up in ("me gallery add-file", "me submit-for-review", "user approve", "me update"):
        assert follow_up in out, follow_up
