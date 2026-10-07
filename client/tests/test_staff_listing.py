"""staff list/set/clear over the public staff listing curation (club_cli_old#11)."""
from __future__ import annotations

import json

from .fakes import FakeHttp, run

STAFF = "/v1/admin/staff-listing"
ROWS = [
    {"username": "coach_a", "displayName": "A", "isPublicProfile": True, "position": 1, "isGuest": False, "isHidden": False},
    {"username": "guest_b", "displayName": "B", "isPublicProfile": True, "position": 5, "isGuest": True, "isHidden": False},
    {"username": "coach_c", "displayName": "C", "isPublicProfile": True, "position": None, "isGuest": False, "isHidden": True},
]


def test_issue_11_list_hides_guests_and_hidden_by_default(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", STAFF, ROWS)

    res = run("staff", "list")

    assert res.exit_code == 0, res.output
    assert [r["username"] for r in json.loads(res.output)] == ["coach_a"]


def test_issue_11_list_include_flags(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", STAFF, ROWS)

    guests = run("staff", "list", "--include-guests")
    everyone = run("staff", "list", "--include-guests", "--include-hidden")

    assert [r["username"] for r in json.loads(guests.output)] == ["coach_a", "guest_b"]
    assert [r["username"] for r in json.loads(everyone.output)] == ["coach_a", "guest_b", "coach_c"]


def test_issue_11_set_sends_only_given_fields(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", f"{STAFF}/coach_a", ROWS[0])

    assert run("staff", "set", "coach_a", "--position", "2").exit_code == 0
    assert run("staff", "set", "coach_a", "--guest", "--hidden").exit_code == 0
    assert run("staff", "set", "coach_a", "--no-guest", "--no-hidden", "--position", "1").exit_code == 0

    bodies = [c.json for c in fake.calls_to("PUT", f"{STAFF}/coach_a")]
    assert bodies == [
        {"position": 2},
        {"isGuest": True, "isHidden": True},
        {"isGuest": False, "isHidden": False, "position": 1},
    ]


def test_issue_11_set_requires_a_field(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    res = run("staff", "set", "coach_a")

    assert res.exit_code != 0
    assert "--position" in res.output
    assert fake.calls == []


def test_issue_11_set_not_a_coach_is_surfaced(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", f"{STAFF}/member_x", status=422, payload={"detail": {"code": "NOT_A_COACH", "message": "no"}})

    res = run("staff", "set", "member_x", "--position", "1")

    assert res.exit_code != 0 and "NOT_A_COACH" in res.output


def test_issue_11_clear(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("DELETE", f"{STAFF}/coach_a", status=204)

    res = run("staff", "clear", "coach_a")

    assert res.exit_code == 0, res.output
    assert len(fake.calls_to("DELETE", f"{STAFF}/coach_a")) == 1
