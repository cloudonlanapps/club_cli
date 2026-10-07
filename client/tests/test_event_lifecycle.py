"""Per-type lifecycle verbs after club_server#306/#374 (club_cli_old#17).

cancel / undo-cancel are camp-only and cancel needs a cutoff; a programme is
terminated, extended or extended indefinitely; a one-off is dropped or
reinstated.
"""
from __future__ import annotations

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run

EVENT = "/v1/events/by_id/7"
CUTOFF = "2026-10-05T06:00:00"
CUTOFF_MS = local_iso_to_utc_ms(CUTOFF)


def _fake(monkeypatch, *verbs: str) -> FakeHttp:
    fake = FakeHttp().install(monkeypatch)
    for v in verbs:
        fake.on("POST", f"{EVENT}/{v}", {"id": 7, "version": 2})
    return fake


def test_issue_17_cancel_sends_reason_and_cutoff(monkeypatch):
    fake = _fake(monkeypatch, "cancel")

    res = run("event", "cancel", "7", "Rink closed", "--effective-time-local", CUTOFF)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/cancel")
    assert call.json == {"reason": "Rink closed", "effectiveDateTimeUtc": CUTOFF_MS}


def test_issue_17_cancel_requires_a_cutoff(monkeypatch):
    fake = _fake(monkeypatch, "cancel")

    res = run("event", "cancel", "7", "Rink closed")

    assert res.exit_code != 0
    assert "--effective-time-local" in res.output
    assert fake.calls == []


def test_issue_17_cancel_help_says_camp_only():
    res = run("event", "cancel", "--help")
    assert "camp" in res.output.lower()
    assert "terminate" in res.output and "drop" in res.output


def test_issue_17_undo_cancel(monkeypatch):
    fake = _fake(monkeypatch, "undo-cancel")

    res = run("event", "undo-cancel", "7")

    assert res.exit_code == 0, res.output
    assert len(fake.calls_to("POST", f"{EVENT}/undo-cancel")) == 1


def test_issue_17_terminate(monkeypatch):
    fake = _fake(monkeypatch, "terminate")

    res = run("event", "terminate", "7", "--cutoff-local", "20261005 0600", "--reason", "Season over")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/terminate")
    assert call.json == {"reason": "Season over", "cutoffTimeUtc": CUTOFF_MS}


def test_issue_17_terminate_requires_reason_and_cutoff(monkeypatch):
    fake = _fake(monkeypatch, "terminate")

    assert run("event", "terminate", "7", "--reason", "x").exit_code != 0
    assert run("event", "terminate", "7", "--cutoff-local", CUTOFF).exit_code != 0
    assert fake.calls == []


def test_issue_17_extend_with_and_without_reason(monkeypatch):
    fake = _fake(monkeypatch, "extend")

    assert run("event", "extend", "7", "--cutoff-local", CUTOFF).exit_code == 0
    assert run("event", "extend", "7", "--cutoff-local", CUTOFF, "--reason", "More ice").exit_code == 0

    first, second = fake.calls_to("POST", f"{EVENT}/extend")
    assert first.json == {"cutoffTimeUtc": CUTOFF_MS}
    assert second.json == {"cutoffTimeUtc": CUTOFF_MS, "reason": "More ice"}


def test_issue_17_extend_indefinitely(monkeypatch):
    fake = _fake(monkeypatch, "extend-indefinitely")

    assert run("event", "extend-indefinitely", "7").exit_code == 0
    assert run("event", "extend-indefinitely", "7", "--reason", "Open-ended").exit_code == 0

    first, second = fake.calls_to("POST", f"{EVENT}/extend-indefinitely")
    assert first.json == {}
    assert second.json == {"reason": "Open-ended"}


START_MS = local_iso_to_utc_ms("2026-10-10T06:00:00")


def _oneoff(fake: FakeHttp, occurrence_version: int = 3) -> FakeHttp:
    """A one-off whose single occurrence sits at its start, at a version."""
    fake.on("GET", EVENT, {"id": 7, "type": "oneOff", "version": 9, "startTimeUtc": START_MS})
    fake.on("GET", f"{EVENT}/occurrences/{START_MS}", {"version": occurrence_version})
    return fake


def test_issue_17_drop_requires_reason(monkeypatch):
    fake = _oneoff(_fake(monkeypatch, "drop"))

    assert run("event", "drop", "7").exit_code != 0
    res = run("event", "drop", "7", "--reason", "No coach")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/drop")
    assert call.json == {"reason": "No coach", "version": 3}


def test_issue_17_reinstate(monkeypatch):
    fake = _oneoff(_fake(monkeypatch, "reinstate"))

    res = run("event", "reinstate", "7")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/reinstate")
    assert call.json == {"version": 3}


def test_issue_33_drop_and_reinstate_send_the_occurrence_version_not_the_events(monkeypatch):
    """The event is at 9 and its occurrence at 3: drop carries 3 (club_server#430)."""
    fake = _oneoff(_fake(monkeypatch, "drop", "reinstate"), occurrence_version=3)

    assert run("event", "drop", "7", "--reason", "x").exit_code == 0
    assert run("event", "reinstate", "7").exit_code == 0

    assert fake.calls_to("POST", f"{EVENT}/drop")[0].json["version"] == 3
    assert fake.calls_to("POST", f"{EVENT}/reinstate")[0].json["version"] == 3


def test_issue_33_version_option_skips_the_read(monkeypatch):
    fake = _fake(monkeypatch, "drop", "reinstate")

    assert run("event", "drop", "7", "--reason", "x", "--version", "1").exit_code == 0
    assert run("event", "reinstate", "7", "--version", "2").exit_code == 0

    assert [c.method for c in fake.calls] == ["POST", "POST"]
    assert fake.calls[0].json == {"reason": "x", "version": 1}
    assert fake.calls[1].json == {"version": 2}


def test_issue_17_server_refusal_exits_nonzero(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{EVENT}/drop", status=422,
            payload={"detail": {"code": "PAST_OCCURRENCE", "message": "started"}})

    res = run("event", "drop", "7", "--reason", "late", "--version", "1")

    assert res.exit_code != 0
    assert "PAST_OCCURRENCE" in res.output
