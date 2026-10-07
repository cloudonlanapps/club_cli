"""Eligibility on events and groups is an age band (club_server#16, #17).

`minAge` and `maxAge` are each {years, months?, days?}, `strictAge` says
whether the band is exact, and null clears a bound. The two date-of-birth
bounds are read-only: the server works them out and refuses them on a write.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

EVENT = "/v1/events/by_id/7"
GROUP = "/v1/groups/by_id/3"
PROGRAMME = {"id": 7, "type": "programme", "version": 3, "title": "U12"}
CAMP = {"id": 7, "type": "camp", "version": 5, "title": "Camp"}
BAND = {"minAge": {"years": 10}, "maxAge": {"years": 12, "months": 6, "days": 15}, "strictAge": True}


def _events(monkeypatch, event: dict) -> FakeHttp:
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/events", {"id": 7})
    fake.on("GET", EVENT, event)
    fake.on("PATCH", EVENT, event)
    fake.on("PATCH", f"{EVENT}/correction", event)
    return fake


def _groups(monkeypatch) -> FakeHttp:
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/groups", {"id": 3})
    fake.on("PATCH", GROUP, {"id": 3})
    return fake


# ---------------------------------------------------------------- events


def test_issue_3_events_create_sends_the_age_band(monkeypatch):
    fake = _events(monkeypatch, CAMP)

    res = run("events", "create", json.dumps({"title": "Camp", "type": "camp", "venueId": 1, **BAND}))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/events")
    assert {k: call.json[k] for k in BAND} == BAND


def test_issue_3_a_whole_number_of_years_is_sent_as_an_age(monkeypatch):
    fake = _events(monkeypatch, CAMP)

    res = run("events", "create", '{"title": "Camp", "type": "camp", "venueId": 1, "minAge": 10, "maxAge": 12}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/events")
    assert call.json["minAge"] == {"years": 10}
    assert call.json["maxAge"] == {"years": 12}


def test_issue_3_update_camp_patches_the_age_band(monkeypatch):
    fake = _events(monkeypatch, CAMP)

    res = run("event", "update", "7", json.dumps(BAND))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", EVENT)
    assert call.json == {**BAND, "version": 5}


def test_issue_3_update_programme_corrects_the_age_band(monkeypatch):
    fake = _events(monkeypatch, PROGRAMME)

    res = run("event", "update", "7", '{"minAge": 8, "strictAge": false}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"minAge": {"years": 8}, "strictAge": False, "version": 3}


def test_issue_3_null_clears_a_bound_on_an_event(monkeypatch):
    fake = _events(monkeypatch, PROGRAMME)

    res = run("event", "correct", "7", '{"minAge": null, "maxAge": null}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"minAge": None, "maxAge": None, "version": 3}


# ---------------------------------------------------------------- groups


def test_issue_3_groups_create_sends_the_age_band(monkeypatch):
    fake = _groups(monkeypatch)

    res = run("groups", "create", json.dumps({"name": "U12", "semiAuto": True, **BAND}))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/groups")
    assert call.json == {"name": "U12", "semiAuto": True, **BAND}


def test_issue_3_group_update_sends_the_age_band_and_clears_a_bound(monkeypatch):
    fake = _groups(monkeypatch)

    res = run("group", "update", "3", '{"minAge": 9, "maxAge": null, "strictAge": false}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", GROUP)
    assert call.json == {"minAge": {"years": 9}, "maxAge": None, "strictAge": False}


# ------------------------------------------- the date-of-birth bounds are gone


@pytest.mark.parametrize("field", ["dobOnOrAfter", "dobOnOrBefore", "dobOnOrAfterUtc", "dobOnOrBeforeUtc"])
@pytest.mark.parametrize("command", [
    ("events", "create"), ("event", "update", "7"), ("event", "correct", "7"),
    ("groups", "create"), ("group", "update", "3"),
])
def test_issue_3_date_of_birth_bounds_are_refused_before_any_call(monkeypatch, command, field):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", EVENT, CAMP)

    res = run(*command, json.dumps({field: "2012-01-01T00:00:00"}))

    assert res.exit_code != 0
    assert field in res.output and "minAge" in res.output and "maxAge" in res.output
    assert [c for c in fake.calls if c.method != "GET"] == []


def test_issue_3_removed_group_fields_point_at_the_age_band(monkeypatch):
    FakeHttp().install(monkeypatch)

    res = run("groups", "create", '{"name": "U12", "ageMin": 10}')

    assert res.exit_code != 0
    assert "minAge" in res.output and "dobOnOrAfter" not in res.output


# ---------------------------------------------------------------- help


def _help(*command: str) -> str:
    res = CliRunner().invoke(main, [*command, "--help"])
    assert res.exit_code == 0, res.output
    return " ".join(res.output.split())  # click wraps the help


@pytest.mark.parametrize("command", [
    ("events", "create"), ("event", "update"), ("event", "correct"), ("groups", "create"), ("group", "update"),
])
def test_issue_3_write_help_names_the_age_band_and_not_the_dates(command):
    text = _help(*command)

    assert "minAge" in text and "maxAge" in text and "strictAge" in text
    assert "dobOnOrAfter," not in text and "dobOnOrBefore," not in text


@pytest.mark.parametrize("command", [("events", "list"), ("event", "get"), ("groups", "list"), ("group", "get")])
def test_issue_3_read_help_describes_the_band_the_reference_day_and_the_window(command):
    text = _help(*command)

    for field in ("minAge", "maxAge", "strictAge", "eligibilityReferenceDayUtc", "dobOnOrAfterUtc", "dobOnOrBeforeUtc"):
        assert field in text, field


@pytest.mark.parametrize("command", [("groups", "list"), ("group", "get")])
def test_issue_3_group_help_describes_the_ineligible_count(command):
    assert "ineligibleMemberCount" in _help(*command)


@pytest.mark.parametrize("command", [("group", "members"), ("group", "get")])
def test_issue_3_member_listing_help_describes_eligible(command):
    text = _help(*command)

    assert "eligible" in text and "semi-auto" in text
