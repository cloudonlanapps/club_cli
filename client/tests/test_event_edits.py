"""Event edits after club_server#292/#365/#388 (club_cli_old#16).

The three PATCH bodies require ``version``; a programme's metadata goes to
``/correction`` and its timetable to ``/future``; the chain endpoints are gone
and ``/schedules`` replaces them; a stale version is explained, not dumped.
"""
from __future__ import annotations

import json

from club_client.cli import local_iso_to_utc_ms, main

from .fakes import FakeHttp, run

EVENT = "/v1/events/by_id/7"
PROGRAMME = {"id": 7, "type": "programme", "version": 3, "title": "U12"}
CAMP = {"id": 7, "type": "camp", "version": 5, "title": "Camp"}


def _fake(monkeypatch, event: dict) -> FakeHttp:
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", EVENT, event)
    fake.on("PATCH", EVENT, {**event, "version": event["version"] + 1})
    fake.on("PATCH", f"{EVENT}/correction", {**event, "version": event["version"] + 1})
    fake.on("PATCH", f"{EVENT}/future", {**event, "version": event["version"] + 1})
    return fake


# ---------------------------------------------------------------- update


def test_issue_16_update_programme_metadata_goes_to_correction_with_version(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "update", "7", '{"title": "New Title"}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"title": "New Title", "version": 3}
    assert fake.calls_to("PATCH", f"{EVENT}/future") == []
    assert fake.calls_to("PATCH", EVENT) == []


def test_issue_16_update_programme_schedule_goes_to_future_with_cutoff(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run(
        "event", "update", "7", '{"venueId": 9, "coachNames": ["c1"]}',
        "--effective-time-local", "2026-10-05T06:00:00",
    )

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/future")
    assert call.json == {
        "venueId": 9,
        "coachNames": ["c1"],
        "version": 3,
        "effectiveDateTimeUtc": local_iso_to_utc_ms("2026-10-05T06:00:00"),
    }
    assert fake.calls_to("PATCH", f"{EVENT}/correction") == []


def test_issue_16_update_programme_schedule_requires_effective_time(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "update", "7", '{"startTime": "2026-10-05T06:00:00"}')

    assert res.exit_code != 0
    assert "--effective-time-local" in res.output
    assert fake.calls_to("PATCH", f"{EVENT}/future") == []


def test_issue_16_update_programme_mixed_fields_correct_then_split(monkeypatch):
    """Metadata first; the split carries the version the correction returned."""
    fake = _fake(monkeypatch, PROGRAMME)

    res = run(
        "event", "update", "7", '{"title": "T", "venueId": 9}',
        "--effective-time-local", "2026-10-05T06:00:00",
    )

    assert res.exit_code == 0, res.output
    (correction,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    (future,) = fake.calls_to("PATCH", f"{EVENT}/future")
    assert correction.json == {"title": "T", "version": 3}
    assert future.json["venueId"] == 9
    assert future.json["version"] == 4


def test_issue_16_update_camp_patches_directly_with_version(monkeypatch):
    fake = _fake(monkeypatch, CAMP)

    res = run("event", "update", "7", '{"title": "T", "coachNames": ["c1"]}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", EVENT)
    assert call.json == {"title": "T", "coachNames": ["c1"], "version": 5}


def test_issue_16_update_camp_refuses_a_cutoff(monkeypatch):
    """A cutoff splits a programme; a camp has nothing to split."""
    fake = _fake(monkeypatch, CAMP)

    res = run("event", "update", "7", '{"venueId": 9}', "--effective-time-local", "2026-10-05T06:00:00")

    assert res.exit_code != 0
    assert "reschedule" in res.output
    assert fake.calls == [fake.calls[0]] and fake.calls[0].method == "GET"


# ---------------------------------------------------- sessions (cli #26, club_server#423)

SESSIONS = [{"name": "Skating", "periodMinutes": 45}, {"name": "Drills", "periodMinutes": 15}]


def test_issue_26_programme_sessions_alone_are_corrected_in_place(monkeypatch):
    """No cutoff, no split: a started programme's timetable can be fixed."""
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "update", "7", json.dumps({"sessions": SESSIONS}))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"sessions": SESSIONS, "version": 3}
    assert fake.calls_to("PATCH", f"{EVENT}/future") == []


def test_issue_26_schedule_id_names_the_schedule_to_correct(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "update", "7", json.dumps({"sessions": SESSIONS, "title": "T"}), "--schedule-id", "4")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"sessions": SESSIONS, "title": "T", "scheduleId": 4, "version": 3}


def test_issue_26_sessions_null_clears_the_timetable(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "update", "7", '{"sessions": null}')

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{EVENT}/correction")[0].json == {"sessions": None, "version": 3}


def test_issue_26_sessions_with_a_timetable_change_still_split(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run(
        "event", "update", "7", json.dumps({"sessions": SESSIONS, "rrule": "FREQ=WEEKLY;BYDAY=MO"}),
        "--effective-time-local", "2026-10-05T06:00:00",
    )

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/future")
    assert call.json["sessions"] == SESSIONS and call.json["rrule"] == "FREQ=WEEKLY;BYDAY=MO"
    assert fake.calls_to("PATCH", f"{EVENT}/correction") == []


def test_issue_26_sessions_alone_with_a_cutoff_split(monkeypatch):
    """A cutoff says 'from then on', so sessions alone go to /future too."""
    fake = _fake(monkeypatch, PROGRAMME)

    res = run(
        "event", "update", "7", json.dumps({"sessions": SESSIONS}),
        "--effective-time-local", "2026-10-05T06:00:00",
    )

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{EVENT}/future")[0].json["sessions"] == SESSIONS
    assert fake.calls_to("PATCH", f"{EVENT}/correction") == []


def test_issue_26_schedule_id_is_refused_off_the_correction_route(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    for body, extra in (
        ('{"title": "T"}', []),
        (json.dumps({"sessions": SESSIONS, "venueId": 9}), ["--effective-time-local", "2026-10-05T06:00:00"]),
    ):
        res = run("event", "update", "7", body, "--schedule-id", "4", *extra)
        assert res.exit_code != 0
        assert "--schedule-id" in res.output
    assert [c.method for c in fake.calls] == ["GET", "GET"]


def test_issue_26_camp_sessions_go_to_the_direct_patch(monkeypatch):
    """Accepted at any time, even once the camp has started."""
    fake = _fake(monkeypatch, CAMP)

    res = run("event", "update", "7", json.dumps({"sessions": SESSIONS}))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", EVENT)[0].json == {"sessions": SESSIONS, "version": 5}


def test_issue_26_camp_refuses_schedule_id(monkeypatch):
    fake = _fake(monkeypatch, CAMP)

    res = run("event", "update", "7", json.dumps({"sessions": SESSIONS}), "--schedule-id", "4")

    assert res.exit_code != 0
    assert fake.calls_to("PATCH", EVENT) == []


# ---------------------------------------------------- reschedule (cli #25)

ONEOFF = {"id": 7, "type": "oneOff", "version": 2, "title": "Match"}


def _fake_reschedule(monkeypatch, event: dict, status: int = 200, payload=None) -> FakeHttp:
    fake = _fake(monkeypatch, event)
    fake.on("POST", f"{EVENT}/reschedule", payload if payload is not None else event, status=status)
    return fake


def test_issue_25_camp_window_goes_to_reschedule(monkeypatch):
    fake = _fake_reschedule(monkeypatch, CAMP)

    res = run(
        "event", "update", "7",
        '{"startTime": "2026-11-01T06:00:00", "endTime": "2026-11-05T08:00:00", "venueId": 9}',
    )

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/reschedule")
    assert call.json == {
        "startTimeUtc": local_iso_to_utc_ms("2026-11-01T06:00:00"),
        "endTimeUtc": local_iso_to_utc_ms("2026-11-05T08:00:00"),
        "venueId": 9,
        "version": 5,
    }
    assert fake.calls_to("PATCH", EVENT) == []


def test_issue_25_sessions_travel_with_the_window(monkeypatch):
    """Validated against the new window, atomically (club_server#248)."""
    fake = _fake_reschedule(monkeypatch, ONEOFF)

    res = run("event", "update", "7", json.dumps({"rrule": "FREQ=DAILY;COUNT=1", "sessions": None}))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", f"{EVENT}/reschedule")[0].json == {
        "rrule": "FREQ=DAILY;COUNT=1", "sessions": None, "version": 2,
    }
    assert fake.calls_to("PATCH", EVENT) == []


def test_issue_25_mixed_edit_patches_then_reschedules(monkeypatch):
    """The reschedule carries the version the PATCH returned (club_server#434, cli #35)."""
    fake = _fake_reschedule(monkeypatch, CAMP)

    res = run("event", "update", "7", '{"title": "T", "coachNames": ["c1"], "venueId": 9}', "--reset-overrides")

    assert res.exit_code == 0, res.output
    patch, post = [c for c in fake.calls if c.method != "GET"]
    assert (patch.method, patch.json) == ("PATCH", {"title": "T", "coachNames": ["c1"], "version": 5})
    assert (post.path, post.json) == (
        f"{EVENT}/reschedule", {"venueId": 9, "version": 6, "resetOverrides": True},
    )


def test_issue_35_version_option_is_sent_on_the_reschedule(monkeypatch):
    fake = _fake_reschedule(monkeypatch, CAMP)

    res = run("event", "update", "7", '{"venueId": 9}', "--version", "3")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", f"{EVENT}/reschedule")[0].json == {"venueId": 9, "version": 3}


def test_issue_25_failed_patch_stops_before_the_reschedule(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", EVENT, CAMP)
    fake.on("PATCH", EVENT, status=409, payload={"detail": {"code": "STALE_VERSION", "version": 6}})

    res = run("event", "update", "7", '{"title": "T", "venueId": 9}')

    assert res.exit_code != 0
    assert "reschedule was not attempted" in res.output
    assert fake.calls_to("POST", f"{EVENT}/reschedule") == []


def test_issue_25_reset_overrides_needs_a_reschedule(monkeypatch):
    for event, body in ((CAMP, '{"title": "T"}'), (PROGRAMME, '{"title": "T"}')):
        fake = _fake(monkeypatch, event)
        res = run("event", "update", "7", body, "--reset-overrides")
        assert res.exit_code != 0
        assert "--reset-overrides" in res.output
        assert [c.method for c in fake.calls] == ["GET"]


def test_issue_25_started_event_is_explained(monkeypatch):
    fake = _fake_reschedule(monkeypatch, CAMP, status=422, payload={"detail": {
        "code": "EVENT_ALREADY_STARTED", "message": "Event 7 has started",
    }})

    res = run("event", "update", "7", '{"venueId": 9}')

    assert res.exit_code != 0
    assert "EVENT_ALREADY_STARTED" in res.output
    assert "occurrences reschedule" in res.output


def test_issue_16_update_version_override_skips_nothing_but_wins(monkeypatch):
    fake = _fake(monkeypatch, CAMP)

    res = run("event", "update", "7", '{"title": "T"}', "--version", "11")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", EVENT)
    assert call.json["version"] == 11


# ---------------------------------------------------------------- correct


def test_issue_16_correct_sends_version_from_get(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "correct", "7", '{"isFeatured": true, "gender": "female"}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"isFeatured": True, "gender": "female", "version": 3}


def test_issue_16_correct_version_override(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "correct", "7", '{"title": "T"}', "--version", "8")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{EVENT}/correction")[0].json["version"] == 8
    assert fake.calls_to("GET", EVENT) == []


def test_issue_16_correct_refuses_coach_names_locally(monkeypatch):
    fake = _fake(monkeypatch, PROGRAMME)

    res = run("event", "correct", "7", '{"coachNames": ["c1"]}')

    assert res.exit_code != 0
    assert "coachNames" in res.output
    assert fake.calls_to("PATCH", f"{EVENT}/correction") == []


# ---------------------------------------------------------------- stale version


def test_issue_16_stale_version_is_explained(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", EVENT, PROGRAMME)
    fake.on(
        "PATCH", f"{EVENT}/correction", status=409,
        payload={"detail": {
            "code": "STALE_VERSION",
            "message": "The event was changed since you last loaded it",
            "version": 4, "updatedAt": 1_760_000_000_000, "updatedBy": "coach_a",
        }},
    )

    res = run("event", "correct", "7", '{"title": "T"}', "--version", "3")

    assert res.exit_code != 0
    assert "STALE_VERSION" in res.output
    assert "version 4" in res.output
    assert "coach_a" in res.output
    assert "--version 4" in res.output


# ---------------------------------------------------------------- schedules / chain


def test_issue_16_event_schedules(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{EVENT}/schedules", [{"id": 1, "eventId": 7, "effectiveFromUtc": 0}])

    res = run("event", "schedules", "7")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[0]["eventId"] == 7


def test_issue_16_myevents_schedules(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/myevents/by_id/sudo/7/schedules", [{"id": 1, "eventId": 7}])

    res = run("myevents", "schedules", "7")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)[0]["eventId"] == 7


def test_issue_16_chain_commands_are_gone():
    assert "chain" not in main.commands["event"].commands
    assert "chain" not in main.commands["myevents"].commands
