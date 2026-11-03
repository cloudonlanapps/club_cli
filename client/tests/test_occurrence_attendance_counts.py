"""occurrences list carries each session's attendance counts (club_cli#62, club_server#142).

The server gives every occurrence in the staff list `attendanceExpected` (the
members whose enrollment covers it) and `attendanceMarked` (how many of them
have a recorded status). The help names both; the live test proves they come
through.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .fakes import run
from .live import Live, local, slot


def test_issue_62_occurrences_list_help_names_the_attendance_counts():
    out = run("occurrences", "list", "--help").output

    assert "attendanceExpected" in out
    assert "attendanceMarked" in out


def test_issue_62_occurrences_list_counts_expected_and_marked_members(live: Live):
    # Soon enough that attendance can be marked, yet still ahead, so the
    # assigned members' enrollment covers it.
    start = (datetime.now() + timedelta(minutes=15)).replace(second=0, microsecond=0)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=2")
    camp_id = str(camp["id"])
    members = [live.active_member(), live.active_member()]
    for member in members:
        live.ok("enrollment", "assign", camp_id, member)

    def counts() -> tuple[int, int]:
        listed = live.ok(
            "occurrences", "list", "--from", local(start - timedelta(minutes=1)),
            "--days", "1", "--event-id", camp_id,
        )
        (first,) = [o for o in listed if o["occurrenceTimeLocal"] == slot(start)]
        return first["attendanceExpected"], first["attendanceMarked"]

    assert counts() == (2, 0)

    live.ok("occurrences", "mark-attendance", camp_id, slot(start),
            json.dumps({"records": [{"membername": members[0], "status": "present"}]}))

    assert counts() == (2, 1)


def test_issue_66_occurrences_list_help_names_the_pending_request_counts():
    out = run("occurrences", "list", "--help").output

    assert "leaveRequestsPending" in out
    assert "enrollmentRequestsPending" in out
    assert "withdrawalRequestsPending" in out
