"""Local guards that no longer match the server (club_cli_old#22)."""
from __future__ import annotations

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run


def test_issue_22_check_conflict_accepts_every_type(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/events/check-conflict", {"venueConflicts": [], "organizerConflicts": [], "coachConflicts": []})

    for event_type in ("programme", "camp", "oneoff"):
        res = run("events", "check-conflict",
                  f'{{"type": "{event_type}", "venueId": 1, "startTime": "2026-05-01T06:00:00", "endTime": "2026-05-01T08:00:00"}}')
        assert res.exit_code == 0, res.output

    calls = fake.calls_to("POST", "/v1/events/check-conflict")
    assert [c.json["type"] for c in calls] == ["programme", "camp", "oneoff"]
    assert calls[0].json["startTimeUtc"] == local_iso_to_utc_ms("2026-05-01T06:00:00")


def test_issue_22_conflict_help_describes_block_vs_report():
    for args in (("events", "check-conflict"), ("event", "check-user-conflicts")):
        out = run(*args, "--help").output
        assert "camp-only" not in out.lower() and "camps only" not in out.lower()
        assert "EVENT_TYPE_NOT_SUPPORTED" not in out
        assert "programme" in out and "block" in out and "report" in out


def test_issue_22_check_user_conflicts_any_event(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/events/by_id/7/check-user-conflicts", {"userConflicts": []})

    res = run("event", "check-user-conflicts", "7", "u1", "u2")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/events/by_id/7/check-user-conflicts")[0].json == {"usernames": ["u1", "u2"]}


def test_issue_22_role_argument_is_a_choice(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/users/by_id/u1/roles", {"username": "u1", "roles": ["coach"]})
    fake.on("DELETE", "/v1/users/by_id/u1/roles/admin", {"username": "u1", "roles": []})

    assert run("user", "add-role", "u1", "coach").exit_code == 0
    assert run("user", "remove-role", "u1", "admin").exit_code == 0
    for args in (("user", "add-role", "u1", "member"), ("user", "remove-role", "u1", "member"),
                 ("user", "add-role", "u1", "owner")):
        res = run(*args)
        assert res.exit_code != 0
        assert "Invalid value" in res.output, res.output
    assert len(fake.calls) == 2


def test_issue_22_role_help_lists_the_enum():
    out = run("user", "add-role", "--help").output
    assert "admin" in out and "coach" in out
    assert "etc." not in out


def test_issue_54_super_admin_is_not_a_role(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    for verb in ("add-role", "remove-role"):
        assert "super_admin" not in run("user", verb, "--help").output
        res = run("user", verb, "u1", "super_admin")
        assert res.exit_code == 2
        assert "Invalid value" in res.output, res.output
    assert fake.calls == []
