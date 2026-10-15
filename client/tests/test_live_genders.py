"""Eligibility by gender, against a live server (club_server#25, #26).

The offline tests in test_genders prove the bodies the commands send; these
prove the server stores the list, clears it, and keeps the old single gender
as a list of one.
"""
from __future__ import annotations

import json

from .live import Live, days_from_today

THREE = ["male", "other", "prefer_not_to_say"]


def test_issue_13_event_genders_read_back_and_an_empty_list_clears_them(live: Live):
    created = live.event("oneOff", days_from_today(20), genders=THREE)
    eid = str(created["id"])
    assert created["genders"] == THREE

    got = live.ok("event", "get", eid)
    assert got["genders"] == THREE
    assert got["gender"] is None, "three genders have no single one"

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


def test_issue_13_the_old_single_gender_is_stored_as_a_list_of_one(live: Live):
    created = live.ok("groups", "create", json.dumps({"name": live.unique("Girls"), "gender": "female"}))

    assert created["genders"] == ["female"]
    assert created["gender"] == "female"


def test_issue_13_unknown_repeated_or_both_forms_are_refused(live: Live):
    for body, says in (
        ({"genders": ["male", "boy"]}, "prefer_not_to_say"),
        ({"genders": ["male", "male"]}, "must not repeat"),
        ({"genders": ["male"], "gender": "male"}, "not both"),
    ):
        res = live.refused("groups", "create", json.dumps({"name": live.unique("Bad"), **body}))
        assert says in res.output, (body, res.output)
