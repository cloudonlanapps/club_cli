"""The listing filters and sort options, against a live server (club_cli#6).

The offline tests prove the query string each command sends; these prove the
server narrows and orders by it.
"""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import pytest

from club_client.cli import local_iso_to_utc_ms

from .live import MEMBER_PASSWORD, Live, days_from_today, local, slot

PHOTO = Path(__file__).resolve().parent / "fixtures" / "id_proof.png"


def _pair(live: Live) -> tuple[str, str, str]:
    """Two members only this test can find: (search token, the 'Aaa' one, the 'Zzz' one)."""
    token = live.unique("Finder").replace("_", "")
    first = live.active_member(firstName=token, lastName="Aaa")
    last = live.active_member(firstName=token, lastName="Zzz")
    return token, first, last


def _usernames(page: dict) -> list[str]:
    return [u["username"] for u in page["items"]]


def test_users_list_narrows_by_search_and_role_and_sorts_both_ways(live: Live):
    token, first, last = _pair(live)
    live.ok("user", "add-role", last, "coach")

    assert sorted(_usernames(live.ok("users", "list", "--search", token))) == sorted([first, last])
    assert _usernames(live.ok("users", "list", "--search", token.upper())) != []
    assert _usernames(live.ok("users", "list", "--search", token, "--role", "coach")) == [last]
    assert _usernames(live.ok("users", "list", "--search", token, "--role", "admin")) == []

    assert _usernames(live.ok("users", "list", "--search", token, "--sort-by", "lastName")) == [first, last]
    assert _usernames(
        live.ok("users", "list", "--search", token, "--sort-by", "lastName", "--descending")
    ) == [last, first]
    # With no --sort-by the order is creation time; --descending is newest first.
    assert _usernames(live.ok("users", "list", "--search", token)) == [first, last]
    assert _usernames(live.ok("users", "list", "--search", token, "--descending")) == [last, first]

    # Live.member_json's date of birth makes both a teenager.
    assert len(_usernames(live.ok("users", "list", "--search", token, "--min-age", "10", "--max-age", "20"))) == 2
    assert _usernames(live.ok("users", "list", "--search", token, "--min-age", "60")) == []
    assert _usernames(live.ok("users", "list", "--search", token, "--max-age", "5")) == []


def test_trash_list_user_searches_and_sorts(live: Live):
    token, first, last = _pair(live)
    live.ok("user", "delete", first)
    live.ok("user", "delete", last)

    assert _usernames(
        live.ok("trash", "list", "user", "--search", token, "--sort-by", "lastName")
    ) == [first, last]
    assert _usernames(
        live.ok("trash", "list", "user", "--search", token, "--sort-by", "lastName", "--descending")
    ) == [last, first]
    res = live.refused("trash", "list", "venue", "--search", token)
    assert "applies to `trash list user` only" in res.output


def test_group_members_sorts_both_ways(live: Live):
    _, first, last = _pair(live)
    group = str(live.ok("groups", "create", f'{{"name": "{live.unique("Group")}"}}')["id"])
    live.ok("group", "add-members", group, last, first)

    def order(*options: str) -> list[str]:
        return [m["membername"] for m in live.ok("group", "members", group, *options)]

    assert order("--sort-by", "lastName") == [first, last]
    assert order("--sort-by", "lastName", "--descending") == [last, first]
    assert order("--sort-by", "membername") == sorted([first, last])


def test_enrollment_list_narrows_by_status(live: Live):
    assigned, invited = live.active_member(), live.active_member()
    camp = str(live.event("camp", days_from_today(20), rrule="FREQ=DAILY;COUNT=2")["id"])
    live.ok("enrollment", "assign", camp, assigned)
    live.ok("enrollment", "invite", camp, invited)

    def members(*options: str) -> list[str]:
        return sorted(r["membername"] for r in live.ok("enrollment", "list", camp, *options)["records"])

    assert members() == sorted([assigned, invited])
    assert members("--status", "invited") == [invited]
    assert members("--status", "assigned") == [assigned]
    assert members("--status", "removed") == []
    assert live.ok("enrollment", "list", camp, "--status", "invited")["enrollments"] == {invited: "invited"}


def test_uploads_list_shows_a_deleted_upload_only_when_asked(live: Live):
    res = live.run("uploads", "add-file", str(PHOTO))
    assert res.exit_code == 0, res.output
    media_id = int(res.stderr.split("id ")[1].split(":")[0])
    live.ok("upload", "delete", str(media_id))

    def listed(*options: str) -> dict[int, dict]:
        found: dict[int, dict] = {}
        offset = 0
        while True:
            page = live.ok("uploads", "list", "--limit", "100", "--offset", str(offset), *options)
            found.update({m["id"]: m for m in page["items"]})
            offset += 100
            if offset >= page["total"]:
                return found

    assert media_id not in listed()
    with_deleted = listed("--include-deleted")
    assert media_id in with_deleted
    assert with_deleted[media_id]["deletedAtUtc"] is not None


def test_media_myfiles_narrows_by_conversion_status(live: Live):
    member = live.active_member()
    as_member = {"user": member, "pw": MEMBER_PASSWORD}
    live.ok("uploads", "add-file", str(PHOTO), **as_member)

    (mine,) = live.ok("media", "myfiles", **as_member)["items"]
    status = mine["conversionStatus"]

    assert [m["id"] for m in live.ok("media", "myfiles", "--status", status, **as_member)["items"]] == [mine["id"]]
    assert live.ok("media", "myfiles", "--status", "no-such-status", **as_member)["items"] == []


def test_events_list_narrows_by_venue_and_window(live: Live):
    venue = live.venue()
    start = days_from_today(30)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=2", venueId=venue)

    def ids(*options: str) -> list[int]:
        return [e["id"] for e in live.ok("events", "list", "--venue-id", str(venue), "--limit", "100", *options)["items"]]

    assert ids() == [camp["id"]]
    assert ids("--from", local(days_from_today(29)), "--to", local(days_from_today(31))) == [camp["id"]]
    assert ids("--to", slot(days_from_today(29))) == []
    assert ids("--from", local(days_from_today(40))) == []


def test_events_list_includes_past_events_unasked(live: Live):
    """club_cli#11: the server lists past events unless --from says otherwise."""
    venue = live.venue()
    past = live.event("camp", days_from_today(-10), rrule="FREQ=DAILY;COUNT=2", venueId=venue)

    def ids(*options: str) -> list[int]:
        return [e["id"] for e in live.ok("events", "list", "--venue-id", str(venue), *options)["items"]]

    assert ids() == [past["id"]]
    assert ids("--from", local(days_from_today(0))) == []


@pytest.mark.modules_on
def test_credits_entries_narrows_by_occurrence(live: Live):
    """Marking a funded programme member present writes a deduction against that occurrence."""
    member = live.active_member()
    start = days_from_today(-1)
    live.ok("credits", "open", json.dumps({
        "membername": member, "credits": 5, "reason": "Live test",
        "validFrom": local(days_from_today(-2)), "validUntil": local(days_from_today(30)),
    }))
    weekday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[start.astimezone(timezone.utc).weekday()]
    programme = str(live.event("programme", start, rrule=f"FREQ=WEEKLY;BYDAY={weekday}")["id"])
    live.ok("enrollment", "assign", programme, member)
    live.ok("occurrences", "mark-attendance", programme, slot(start),
            json.dumps({"records": [{"membername": member, "status": "present"}]}))

    def entries(*options: str) -> list[dict]:
        return live.ok("credits", "entries", "--membername", member, "--limit", "100", *options)["items"]

    (deduction,) = entries("--occurrence", slot(start))
    assert deduction["entryType"] == "sessionDeduction"
    assert deduction["occurrenceTimeUtc"] == local_iso_to_utc_ms(local(start))
    assert len(entries()) > 1  # the grant is there too, bound to no occurrence
    assert entries("--occurrence", slot(days_from_today(-400))) == []
