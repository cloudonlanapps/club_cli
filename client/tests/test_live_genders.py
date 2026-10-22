"""Eligibility by gender, against a live server (club_server#25, #26, #54).

The offline tests in test_genders prove the bodies the commands send; these
prove the server stores the list, clears it, and refuses the single gender
it no longer takes.
"""
from __future__ import annotations

import json
from datetime import timedelta

from click.testing import Result

from .live import Live, days_from_today, local

THREE = ["male", "other", "prefer_not_to_say"]


def test_issue_13_event_genders_read_back_and_an_empty_list_clears_them(live: Live):
    created = live.event("oneOff", days_from_today(20), genders=THREE)
    eid = str(created["id"])
    assert created["genders"] == THREE

    got = live.ok("event", "get", eid)
    assert got["genders"] == THREE
    assert "gender" not in got, "a response carries genders only"

    renamed = live.ok("event", "update", eid, json.dumps({"title": live.unique("Renamed")}))
    assert renamed["genders"] == THREE, "left out, left alone"

    live.ok("event", "update", eid, '{"genders": []}')
    assert live.ok("event", "get", eid)["genders"] == []


def test_issue_13_programme_genders_are_corrected(live: Live):
    programme = live.event("programme", days_from_today(20), rrule="FREQ=WEEKLY;BYDAY=MO")
    eid = str(programme["id"])

    live.ok("event", "correct", eid, '{"genders": ["female", "other"]}')

    assert live.ok("event", "get", eid)["genders"] == ["female", "other"]


def test_issue_13_group_genders_read_back_and_an_empty_list_clears_them(live: Live):
    created = live.ok("groups", "create", json.dumps({"name": live.unique("Mixed"), "genders": THREE}))
    gid = str(created["id"])
    assert created["genders"] == THREE
    assert created["kind"] == "auto", "a non-empty genders is a criterion"

    assert live.ok("group", "get", gid)["genders"] == THREE

    cleared = live.ok("group", "update", gid, '{"genders": []}')
    assert cleared["genders"] == []
    assert live.ok("group", "get", gid)["genders"] == []


def _refuses_gender(res: Result) -> None:
    """The server's 422: gender is a field it does not take."""
    detail = json.loads(res.output)["detail"]
    assert any(e["type"] == "extra_forbidden" and e["loc"][-1] == "gender" for e in detail), res.output


def test_issue_25_a_group_refuses_the_single_gender(live: Live):
    _refuses_gender(live.refused("groups", "create", json.dumps({"name": live.unique("Girls"), "gender": "female"})))

    created = live.ok("groups", "create", json.dumps({"name": live.unique("Girls"), "genders": ["female"]}))
    assert "gender" not in created, "a response carries genders only"
    gid = str(created["id"])
    _refuses_gender(live.refused("group", "update", gid, '{"gender": "male"}'))
    assert live.ok("group", "get", gid)["genders"] == ["female"], "a refused update changes nothing"


def test_issue_25_an_event_refuses_the_single_gender(live: Live):
    created = live.event("oneOff", days_from_today(20), genders=["female"])
    eid = str(created["id"])
    assert "gender" not in created, "a response carries genders only"

    _refuses_gender(live.refused("event", "update", eid, '{"gender": "male"}'))
    assert live.ok("event", "get", eid)["genders"] == ["female"], "a refused update changes nothing"

    start = days_from_today(21)
    body = {
        "title": live.unique("Live oneOff"), "type": "oneOff", "venueId": live.venue(),
        "organizerName": live.coach(), "startTime": local(start),
        "endTime": local(start + timedelta(hours=1)), "gender": "female",
    }
    _refuses_gender(live.refused("events", "create", json.dumps(body)))


def test_issue_13_unknown_or_repeated_genders_are_refused(live: Live):
    for body, says in (
        ({"genders": ["male", "boy"]}, "prefer_not_to_say"),
        ({"genders": ["male", "male"]}, "must not repeat"),
    ):
        res = live.refused("groups", "create", json.dumps({"name": live.unique("Bad"), **body}))
        assert says in res.output, (body, res.output)
