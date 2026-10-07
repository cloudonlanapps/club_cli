"""Listing commands send every filter and sort option the server takes (club_cli#6).

Each option is sent only when given: a listing run bare asks the server for
its defaults.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from club_client.cli import local_iso_to_utc_ms, main

from .fakes import FakeHttp, run

PAGE = {"items": [], "total": 0, "offset": 0, "limit": 20}


def _params(monkeypatch, path: str, *command: str, payload=PAGE) -> dict:
    """The query string one command sends to `path`."""
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", path, payload)

    res = run(*command)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", path)
    return dict(call.params or {})


# ---------------------------------------------------------------- users list


def test_issue_6_users_list_sends_every_filter_and_the_sort(monkeypatch):
    params = _params(
        monkeypatch, "/v1/users",
        "users", "list", "--status", "active", "--role", "coach", "--search", "ann",
        "--sort-by", "lastName", "--descending", "--min-age", "10", "--max-age", "14",
    )

    assert params == {
        "offset": 0, "limit": 20, "status": "active", "role": "coach", "searchTerm": "ann",
        "sortBy": "lastName", "descending": True, "minAge": 10, "maxAge": 14,
    }


def test_issue_6_users_list_bare_sends_only_the_page(monkeypatch):
    assert _params(monkeypatch, "/v1/users", "users", "list") == {"offset": 0, "limit": 20}


def test_issue_6_users_list_sends_an_age_of_zero(monkeypatch):
    params = _params(monkeypatch, "/v1/users", "users", "list", "--min-age", "0")

    assert params["minAge"] == 0


# ---------------------------------------------------------------- trash list


def test_issue_6_trash_list_user_sends_search_and_sort(monkeypatch):
    params = _params(
        monkeypatch, "/v1/users/deleted",
        "trash", "list", "user", "--search", "ann", "--sort-by", "username", "--descending",
    )

    assert params == {"offset": 0, "limit": 100, "searchTerm": "ann", "sortBy": "username", "descending": True}


def test_issue_6_trash_list_bare_sends_only_the_page(monkeypatch):
    assert _params(monkeypatch, "/v1/users/deleted", "trash", "list", "user") == {"offset": 0, "limit": 100}


@pytest.mark.parametrize("option", [("--search", "ann"), ("--sort-by", "username"), ("--descending",)])
@pytest.mark.parametrize("owner_type", ["venue", "group", "event", "evaluation", "evaluation-template"])
def test_issue_6_trash_list_refuses_user_options_for_other_kinds(monkeypatch, owner_type, option):
    fake = FakeHttp().install(monkeypatch)

    res = run("trash", "list", owner_type, *option)

    assert res.exit_code != 0
    assert option[0] in res.output and "applies to `trash list user` only" in res.output
    assert fake.calls == []


# ---------------------------------------------------------------- group members


def test_issue_6_group_members_sends_the_sort(monkeypatch):
    params = _params(
        monkeypatch, "/v1/groups/by_id/3/members",
        "group", "members", "3", "--sort-by", "nickname", "--descending", payload=[],
    )

    assert params == {"sortBy": "nickname", "descending": True}


def test_issue_6_group_members_bare_sends_no_sort(monkeypatch):
    assert _params(monkeypatch, "/v1/groups/by_id/3/members", "group", "members", "3", payload=[]) == {}


# ---------------------------------------------------------------- enrollment list


def test_issue_6_enrollment_list_sends_the_status(monkeypatch):
    params = _params(
        monkeypatch, "/v1/events/by_id/7/enrollments",
        "enrollment", "list", "7", "--status", "assignedTrial", payload={"enrollments": {}, "records": []},
    )

    assert params == {"status": "assignedTrial"}


def test_issue_6_enrollment_list_bare_sends_no_status(monkeypatch):
    params = _params(
        monkeypatch, "/v1/events/by_id/7/enrollments",
        "enrollment", "list", "7", payload={"enrollments": {}, "records": []},
    )

    assert params == {}


# ---------------------------------------------------------------- uploads


def test_issue_6_uploads_list_sends_include_deleted(monkeypatch):
    params = _params(monkeypatch, "/v1/media", "uploads", "list", "--include-deleted")

    assert params == {"offset": 0, "limit": 20, "includeDeleted": True}


def test_issue_6_uploads_list_bare_does_not_ask_for_deleted(monkeypatch):
    assert _params(monkeypatch, "/v1/media", "uploads", "list") == {"offset": 0, "limit": 20}


def test_issue_6_media_myfiles_sends_the_conversion_status(monkeypatch):
    params = _params(monkeypatch, "/v1/media/myfiles", "media", "myfiles", "--status", "completed")

    assert params == {"offset": 0, "limit": 100, "conversionStatus": "completed"}


def test_issue_6_media_myfiles_bare_sends_no_status(monkeypatch):
    assert _params(monkeypatch, "/v1/media/myfiles", "media", "myfiles") == {"offset": 0, "limit": 100}


# ---------------------------------------------------------------- events, credits


def test_issue_6_events_list_sends_the_window_and_the_venue(monkeypatch):
    params = _params(
        monkeypatch, "/v1/events",
        "events", "list", "--from", "2026-07-01T00:00:00", "--to", "20260731 2359", "--venue-id", "4",
    )

    assert params["fromTimeUtc"] == local_iso_to_utc_ms("2026-07-01T00:00:00")
    assert params["toTimeUtc"] == local_iso_to_utc_ms("2026-07-31T23:59:00")
    assert params["venueId"] == 4


def test_issue_6_events_list_bare_sends_no_window_or_venue(monkeypatch):
    params = _params(monkeypatch, "/v1/events", "events", "list")

    assert not {"fromTimeUtc", "toTimeUtc", "venueId"} & set(params)


def test_issue_6_credits_entries_sends_the_occurrence(monkeypatch):
    params = _params(monkeypatch, "/v1/credits/entries", "credits", "entries", "--occurrence", "20260503 0030")

    assert params == {
        "occurrenceTimeUtc": local_iso_to_utc_ms("2026-05-03T00:30:00"), "offset": 0, "limit": 20,
    }


def test_issue_6_credits_entries_bare_sends_no_occurrence(monkeypatch):
    assert _params(monkeypatch, "/v1/credits/entries", "credits", "entries") == {"offset": 0, "limit": 20}


# ---------------------------------------------------------------- help


@pytest.mark.parametrize("command, expected", [
    (("users", "list"), ["--role", "admin, coach", "--search", "username, first name, last name, nickname or email",
                         "--sort-by [username|firstName|lastName]", "--descending", "--min-age", "--max-age",
                         "creation time"]),
    (("trash", "list"), ["--search", "--sort-by [username|firstName|lastName]", "--descending", "user only",
                         "deletion time"]),
    (("group", "members"), ["--sort-by [membername|firstName|lastName|nickname]", "--descending"]),
    (("enrollment", "list"), ["--status", "invited", "assignedTrial", "withdrawRequested", "removed"]),
    (("uploads", "list"), ["--include-deleted", "soft-deleted"]),
    (("media", "myfiles"), ["--status", "conversion status"]),
    (("events", "list"), ["--from", "--to", "--venue-id"]),
    (("credits", "entries"), ["--occurrence"]),
])
def test_issue_6_help_describes_each_option_and_its_values(command, expected):
    res = CliRunner().invoke(main, [*command, "--help"])
    text = " ".join(res.output.split())  # click wraps the help

    assert res.exit_code == 0
    for phrase in expected:
        assert phrase in text, phrase


# ------------------------------------------- options the server never took (club_cli#11)


def test_issue_11_events_list_sends_only_what_the_server_takes(monkeypatch):
    params = _params(
        monkeypatch, "/v1/events",
        "events", "list", "--type", "camp", "--visibility", "public", "--venue-id", "4",
        "--from", "2026-07-01T00:00:00", "--to", "2026-07-31T23:59:00",
    )

    assert set(params) == {"fromTimeUtc", "toTimeUtc", "type", "visibility", "venueId", "offset", "limit"}


def test_issue_11_events_list_bare_sends_only_the_page(monkeypatch):
    assert _params(monkeypatch, "/v1/events", "events", "list") == {"offset": 0, "limit": 20}


@pytest.mark.parametrize("option", [("--organizer", "coach_a"), ("--include-past",)])
def test_issue_11_removed_options_are_refused(monkeypatch, option):
    fake = FakeHttp().install(monkeypatch)

    res = run("events", "list", *option)

    assert res.exit_code != 0
    assert f"No such option: {option[0]}" in res.output
    assert fake.calls == []
