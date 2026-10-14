"""Event edits against a live server: occurrence versions, sessions, reschedule.

The offline tests in test_event_edits / test_occurrence_reschedule /
test_event_lifecycle pin down which endpoint each command calls; these check
the server accepts what the CLI sends (cli #33, #26, #25).
"""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner

import club_client.cli as cli
from club_client.cli import main

from .live import Live, days_from_today, local, slot

HOUR = [{"name": "Skating", "periodMinutes": 45}, {"name": "Drills", "periodMinutes": 15}]


# ── Occurrence versions (club_server#430, cli #33) ─────────────────────


def test_occurrence_changes_read_and_send_the_occurrence_version(live: Live):
    start = days_from_today(10)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=3")
    eid, second = str(camp["id"]), slot(start + timedelta(days=1))

    assert live.ok("occurrences", "get", eid, second)["version"] == 1
    live.ok("occurrences", "cancel", eid, second, "--reason", "Rink closed")
    assert live.ok("occurrences", "get", eid, second)["version"] == 2
    live.ok("occurrences", "undo-cancel", eid, second)
    assert live.ok("occurrences", "get", eid, second)["version"] == 3
    live.ok("occurrences", "reschedule", eid, second, "--duration-minutes", "90")
    occurrence = live.ok("occurrences", "get", eid, second)
    assert occurrence["version"] == 4
    assert occurrence["updatedBy"] == live.admin
    listed = live.ok("occurrences", "list", "--event-id", eid, "--from", local(start), "--days", "5")
    assert {o["version"] for o in listed} == {1, 4}


def test_stale_occurrence_version_is_refused_and_explained(live: Live):
    start = days_from_today(10)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=2")
    eid, first = str(camp["id"]), slot(start)
    live.ok("occurrences", "cancel", eid, first, "--reason", "r")

    for args in (
        ("occurrences", "undo-cancel", eid, first, "--version", "1"),
        ("occurrences", "cancel", eid, slot(start + timedelta(days=1)), "--reason", "r", "--version", "7"),
    ):
        res = live.refused(*args)
        assert "STALE_VERSION" in res.stdout
        assert "retry with --version" in res.stderr


def test_one_off_drop_and_reinstate_carry_the_occurrence_version(live: Live):
    start = days_from_today(12)
    oneoff = live.event("oneOff", start)
    eid = str(oneoff["id"])

    live.ok("event", "drop", eid, "--reason", "No ice")
    assert live.ok("occurrences", "get", eid, slot(start))["status"] == "cancelled"
    res = live.refused("event", "reinstate", eid, "--version", "1")
    assert "STALE_VERSION" in res.stdout
    live.ok("event", "reinstate", eid)
    occurrence = live.ok("occurrences", "get", eid, slot(start))
    assert (occurrence["status"], occurrence["version"]) == ("scheduled", 3)
    # The event itself is untouched by occurrence changes.
    assert live.ok("event", "get", eid)["version"] == oneoff["version"]


# ── Sessions (club_server#423, cli #26) ────────────────────────────────


def test_started_programme_sessions_are_corrected_in_place(live: Live):
    programme = live.event("programme", days_from_today(-14), rrule="FREQ=WEEKLY;BYDAY=MO,TH")
    eid = str(programme["id"])

    updated = live.ok("event", "update", eid, json.dumps({"sessions": HOUR}))

    assert updated["sessions"] == HOUR
    (schedule,) = live.ok("event", "schedules", eid)
    assert schedule["sessions"] == HOUR


def test_schedule_id_corrects_an_earlier_schedule(live: Live):
    programme = live.event("programme", days_from_today(-14), rrule="FREQ=WEEKLY;BYDAY=MO")
    eid = str(programme["id"])
    cutoff = days_from_today(7 - days_from_today(0).weekday() + 7)  # a Monday ahead
    live.ok("event", "update", eid, '{"rrule": "FREQ=WEEKLY;BYDAY=TU"}', "--effective-time-local", local(cutoff))
    first, latest = live.ok("event", "schedules", eid)

    live.ok("event", "update", eid, json.dumps({"sessions": HOUR}), "--schedule-id", str(first["id"]))

    first_after, latest_after = live.ok("event", "schedules", eid)
    assert first_after["sessions"] == HOUR
    assert latest_after["sessions"] == latest["sessions"]


def test_bad_sessions_and_schedule_ids_are_refused_and_explained(live: Live):
    programme = live.event("programme", days_from_today(-7), rrule="FREQ=WEEKLY;BYDAY=WE")
    eid = str(programme["id"])

    res = live.refused("event", "update", eid, json.dumps({"sessions": HOUR[:1]}))
    assert "INVALID_SESSIONS_TOTAL" in res.stdout and "add up" in res.stderr
    res = live.refused("event", "update", eid, json.dumps({"sessions": HOUR}), "--schedule-id", "999999")
    assert "SCHEDULE_NOT_FOUND" in res.stdout and "event schedules" in res.stderr


def test_started_camp_sessions_go_through_the_plain_update(live: Live):
    camp = live.event("camp", days_from_today(-2), rrule="FREQ=DAILY;COUNT=10")

    updated = live.ok("event", "update", str(camp["id"]), json.dumps({"sessions": HOUR}))

    assert updated["sessions"] == HOUR


# ── Reschedule (cli #25) ────────────────────────────────────────────────


def test_future_camp_is_rescheduled_through_event_update(live: Live):
    start = days_from_today(15)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=3", sessions=HOUR)
    new_start = start + timedelta(days=7, hours=1)
    venue = live.venue()

    moved = live.ok("event", "update", str(camp["id"]), json.dumps({
        "title": "Moved camp",
        "startTime": local(new_start), "endTime": local(new_start + timedelta(hours=2)),
        "venueId": venue, "sessions": None,
    }))

    got = live.ok("event", "get", str(camp["id"]))
    assert (got["title"], got["venueId"], got["sessions"]) == ("Moved camp", venue, None)
    assert got["startTimeUtc"] == moved["startTimeUtc"]
    assert live.ok("occurrences", "get", str(camp["id"]), slot(new_start))["occurrenceTimeUtc"]


def test_overrides_block_a_reschedule_until_reset(live: Live):
    start = days_from_today(15)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=3")
    eid = str(camp["id"])
    live.ok("occurrences", "cancel", eid, slot(start + timedelta(days=1)), "--reason", "r")
    later = json.dumps({"startTime": local(start + timedelta(hours=2)), "endTime": local(start + timedelta(hours=3))})

    res = live.refused("event", "update", eid, later)
    assert "OCCURRENCE_OVERRIDES_PRESENT" in res.stdout and "--reset-overrides" in res.stderr
    live.ok("event", "update", eid, later, "--reset-overrides")


def test_stale_version_on_a_reschedule_is_refused(live: Live):
    """club_server#434: a reschedule is versioned like the other edits (cli #35)."""
    start = days_from_today(15)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=2")
    eid = str(camp["id"])
    live.ok("event", "update", eid, json.dumps({"venueId": live.venue()}))

    res = live.refused("event", "update", eid, json.dumps({"venueId": live.venue()}), "--version", "1")

    assert "STALE_VERSION" in res.stdout
    assert "retry with --version 2" in res.stderr


def test_started_camp_cannot_move_and_says_what_to_do(live: Live):
    camp = live.event("camp", days_from_today(-2), rrule="FREQ=DAILY;COUNT=10")

    res = live.refused("event", "update", str(camp["id"]), json.dumps({"venueId": live.venue()}))

    assert "EVENT_ALREADY_STARTED" in res.stdout
    assert "occurrences reschedule" in res.stderr


def test_one_off_is_rescheduled_through_event_update(live: Live):
    start = days_from_today(20)
    oneoff = live.event("oneOff", start)
    new_start = start + timedelta(days=1)

    live.ok("event", "update", str(oneoff["id"]), json.dumps(
        {"startTime": local(new_start), "endTime": local(new_start + timedelta(hours=1))}))

    assert live.ok("occurrences", "get", str(oneoff["id"]), slot(new_start))["status"] == "scheduled"


# ── The rule examples of `events create --help` (cli #17) ──────────────


def _rule_examples() -> dict[str, dict]:
    """The `events create` help examples that carry a rule, by event type."""
    res = CliRunner().invoke(main, ["events", "create", "--help"])
    assert res.exit_code == 0, res.output
    text = " ".join(res.output.split())  # click wraps the help
    examples = [part.strip() for part in text.split("Options:")[0].split("Example:")[1:]]
    bodies = [json.loads(shlex.split(e)[2]) for e in examples]
    return {b["type"]: b for b in bodies if "rrule" in b}


def _create_as_written(live: Live, body: dict) -> str:
    """The example as written, at a venue and with an organizer of its own."""
    body = {**body, "title": live.unique(body["title"]), "venueId": live.venue(), "organizerName": live.coach()}
    return str(live.ok("events", "create", json.dumps(body))["id"])


def _local_starts(live: Live, eid: str, type_: str, days: int) -> list[str]:
    listed = live.ok(
        "occurrences", "list", "--event-id", eid, "--type", type_,
        "--from", "2026-11-02T00:00:00", "--days", str(days),
    )
    return sorted(o["occurrenceTimeLocal"] for o in listed)


@pytest.fixture
def ist(monkeypatch):
    """The examples are written in IST; the CLI reads local times in LOCAL_TZ."""
    monkeypatch.setattr(cli, "LOCAL_TZ", ZoneInfo("Asia/Kolkata"))


def test_issue_17_the_byday_example_of_the_help_runs_on_local_mondays(live: Live, ist):
    eid = _create_as_written(live, _rule_examples()["programme"])

    starts = _local_starts(live, eid, "programme", 14)

    assert starts == ["20261102 0500", "20261109 0500"]
    assert {datetime.strptime(s, "%Y%m%d %H%M").strftime("%A") for s in starts} == {"Monday"}


def test_issue_17_the_rest_day_example_of_the_help_holds_seven_days_across_ten(live: Live, ist):
    eid = _create_as_written(live, _rule_examples()["camp"])

    starts = _local_starts(live, eid, "camp", 14)

    assert starts == [f"202611{day:02d} 0900" for day in (2, 4, 6, 8, 9, 10, 11)]
