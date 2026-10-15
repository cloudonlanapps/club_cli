"""Eligibility by gender on events and groups is a list (club_server#25, #26).

`genders` lists the genders admitted; an empty list or null clears the
criterion. The CLI sends it as written. The single `gender` is the server's
old form, kept for one release, and the help says so.
"""
from __future__ import annotations

import json
import re

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

EVENT = "/v1/events/by_id/7"
GROUP = "/v1/groups/by_id/3"
PROGRAMME = {"id": 7, "type": "programme", "version": 3, "title": "U12"}
CAMP = {"id": 7, "type": "camp", "version": 5, "title": "Camp"}
THREE = ["male", "other", "prefer_not_to_say"]


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


def test_issue_13_events_create_sends_the_genders(monkeypatch):
    fake = _events(monkeypatch, CAMP)

    res = run("events", "create", json.dumps({"title": "Camp", "type": "camp", "venueId": 1, "genders": THREE}))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/events")
    assert call.json["genders"] == THREE
    assert "gender" not in call.json


@pytest.mark.parametrize("genders", [[], None])
def test_issue_13_event_update_sends_an_empty_or_null_genders_to_clear(monkeypatch, genders):
    fake = _events(monkeypatch, CAMP)

    res = run("event", "update", "7", json.dumps({"genders": genders}))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", EVENT)
    assert call.json == {"genders": genders, "version": 5}


def test_issue_13_event_correct_sends_the_genders(monkeypatch):
    fake = _events(monkeypatch, PROGRAMME)

    res = run("event", "correct", "7", '{"genders": ["female", "other"]}', "--version", "4")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", f"{EVENT}/correction")
    assert call.json == {"genders": ["female", "other"], "version": 4}


@pytest.mark.parametrize("command", [("events", "create"), ("event", "update", "7")])
def test_issue_13_aux_info_guard_names_genders(monkeypatch, command):
    fake = _events(monkeypatch, CAMP)

    res = run(*command, '{"auxInfo": {"gender": "male"}}')

    assert res.exit_code != 0
    assert "genders" in res.output
    assert [c for c in fake.calls if c.method != "GET"] == []


# ---------------------------------------------------------------- groups


def test_issue_13_groups_create_sends_the_genders(monkeypatch):
    fake = _groups(monkeypatch)

    res = run("groups", "create", json.dumps({"name": "Mixed", "genders": THREE}))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/groups")
    assert call.json == {"name": "Mixed", "genders": THREE}


def test_issue_13_group_update_sends_an_empty_genders_to_clear(monkeypatch):
    fake = _groups(monkeypatch)

    res = run("group", "update", "3", '{"genders": []}')

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("PATCH", GROUP)
    assert call.json == {"genders": []}


# ---------------------------------------------------------------- help


def _help(*command: str) -> str:
    res = CliRunner().invoke(main, [*command, "--help"])
    assert res.exit_code == 0, res.output
    return " ".join(res.output.split())  # click wraps the help


@pytest.mark.parametrize("command", [
    ("events", "create"), ("event", "update"), ("event", "correct"), ("groups", "create"), ("group", "update"),
])
def test_issue_13_write_help_names_genders_and_marks_gender_as_the_old_form(command):
    text = _help(*command)

    assert "genders" in text
    for value in ("male", "female", "other", "prefer_not_to_say"):
        assert value in text, value
    assert "An empty list or null clears the criterion" in text
    assert "gender, a single value, is the old form" in text
    assert '"gender":' not in text, "no example sets the old single gender"
    assert "{GENDERS" not in text


@pytest.mark.parametrize("command", [("events", "list"), ("event", "get"), ("groups", "list"), ("group", "get")])
def test_issue_13_read_help_describes_genders_and_the_old_gender(command):
    text = _help(*command)

    assert "genders lists the genders admitted" in text
    assert "gender is the old form" in text
    assert "{GENDERS" not in text


@pytest.mark.parametrize("command", [
    ("enrollment", "list"), ("myevents", "enrollment"), ("groups", "list"), ("group", "get"),
    ("group", "members"), ("event", "eligible"), ("notifications", "list"),
])
def test_issue_13_eligibility_notes_say_genders(command):
    text = _help(*command)

    assert "genders" in text
    # Only the reading note's two phrases may name the single gender.
    rest = text.replace("gender is the old form", "").replace("no gender criterion", "")
    assert re.search(r"\bgender\b", rest) is None
