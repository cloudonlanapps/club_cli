"""Eligibility by age, against a live server (club_server#16, #17).

The offline tests prove the bodies the commands send; these prove the server
takes them, and that the listings show what it works out from them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .live import MEMBER_PASSWORD, Live, days_from_today

# Live.member_json gives every member this date of birth.
MEMBER_BORN = "2012-05-03"
# A date of birth no band in this file admits.
BORN_TOO_EARLY = "1990-01-01T00:00:00Z"


def years_before(day_utc_ms: int, years: int) -> int:
    """The same calendar day `years` earlier, as UTC-midnight milliseconds."""
    day = datetime.fromtimestamp(day_utc_ms / 1000, tz=timezone.utc)
    try:
        earlier = day.replace(year=day.year - years)
    except ValueError:  # 29 February in a year that has none
        earlier = day.replace(year=day.year - years, day=28)
    return int(earlier.timestamp() * 1000)


def age_today(born: str) -> int:
    """Whole years since `born` (YYYY-MM-DD), counted in UTC."""
    today = datetime.now(timezone.utc).date()
    then = datetime.fromisoformat(born).date()
    return today.year - then.year - ((today.month, today.day) < (then.month, then.day))


def test_event_listing_shows_the_window_for_the_ages_given(live: Live):
    camp = live.event(
        "camp", days_from_today(20), rrule="FREQ=DAILY;COUNT=2",
        minAge=10, maxAge={"years": 12}, strictAge=True,
    )
    eid = str(camp["id"])

    got = live.ok("event", "get", eid)

    assert got["minAge"] == {"years": 10, "months": 0, "days": 0}
    assert got["maxAge"] == {"years": 12, "months": 0, "days": 0}
    assert got["strictAge"] is True
    day = got["eligibilityReferenceDayUtc"]
    # Strict: aged exactly 12 down to exactly 10 on the reference day.
    assert got["dobOnOrAfterUtc"] == years_before(day, 12)
    assert got["dobOnOrBeforeUtc"] == years_before(day, 10)
    (listed,) = [e for e in live.ok("events", "list", "--limit", "100")["items"] if e["id"] == camp["id"]]
    assert {k: listed[k] for k in ("minAge", "maxAge", "strictAge", "dobOnOrAfterUtc", "dobOnOrBeforeUtc")} == {
        k: got[k] for k in ("minAge", "maxAge", "strictAge", "dobOnOrAfterUtc", "dobOnOrBeforeUtc")
    }

    # Relaxed widens each end by a year less a day; null clears a bound.
    relaxed = live.ok("event", "update", eid, '{"strictAge": false}')
    assert relaxed["dobOnOrAfterUtc"] < got["dobOnOrAfterUtc"]
    assert relaxed["dobOnOrBeforeUtc"] > got["dobOnOrBeforeUtc"]
    cleared = live.ok("event", "update", eid, '{"maxAge": null}')
    assert cleared["maxAge"] is None and cleared["dobOnOrAfterUtc"] is None
    assert cleared["minAge"] == {"years": 10, "months": 0, "days": 0}


def test_event_refuses_a_minimum_age_above_the_maximum(live: Live):
    venue, organizer, start = live.venue(), live.coach(), days_from_today(21)
    body = {
        "title": live.unique("Live camp"), "type": "camp", "venueId": venue, "organizerName": organizer,
        "startTime": start.isoformat(), "endTime": start.replace(hour=start.hour + 1).isoformat(),
        "minAge": 13, "maxAge": 12,
    }

    res = live.refused("events", "create", json.dumps(body))

    assert "INVALID_STATE" in res.output


def test_semi_auto_member_outside_the_band_is_listed_not_eligible(live: Live):
    age = age_today(MEMBER_BORN)
    member = live.active_member()
    group = live.ok("groups", "create", json.dumps({
        "name": live.unique("Group"), "semiAuto": True,
        "minAge": age - 1, "maxAge": {"years": age + 1}, "strictAge": True,
    }))
    gid = str(group["id"])
    assert group["kind"] == "semi_auto"
    day = group["eligibilityReferenceDayUtc"]
    assert group["dobOnOrAfterUtc"] == years_before(day, age + 1)
    assert group["dobOnOrBeforeUtc"] == years_before(day, age - 1)
    live.ok("group", "add-member", gid, member)

    def row() -> dict:
        (found,) = [m for m in live.ok("group", "members", gid) if m["membername"] == member]
        return found

    assert row()["eligible"] is True
    assert live.ok("group", "get", gid)["ineligibleMemberCount"] == 0

    # The date of birth is corrected to one the band does not admit.
    live.ok("user", "update", member, json.dumps({"dateOfBirthUtc": _utc_ms(BORN_TOO_EARLY)}))

    assert row()["eligible"] is False
    detail = live.ok("group", "get", gid)
    assert detail["ineligibleMemberCount"] == 1
    assert [m["eligible"] for m in detail["members"] if m["membername"] == member] == [False]
    (listed,) = [g for g in live.ok("groups", "list", "--limit", "100")["items"] if g["id"] == group["id"]]
    assert listed["ineligibleMemberCount"] == 1

    # Clearing the upper bound admits them again.
    updated = live.ok("group", "update", gid, '{"maxAge": null}')
    assert updated["maxAge"] is None and updated["dobOnOrAfterUtc"] is None
    assert row()["eligible"] is True


def _utc_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def test_enrolled_member_outside_the_window_is_listed_not_eligible(live: Live):
    """club_server#19: an assigned member whose date of birth is corrected out of the window."""
    age = age_today(MEMBER_BORN)
    member = live.active_member()
    camp = live.event(
        "camp", days_from_today(20), rrule="FREQ=DAILY;COUNT=2",
        minAge=age - 1, maxAge=age + 2, strictAge=True,
    )
    eid = str(camp["id"])
    live.ok("enrollment", "assign", eid, member)

    def rows() -> tuple[dict, dict]:
        (staff_view,) = [r for r in live.ok("enrollment", "list", eid)["records"] if r["membername"] == member]
        own_view = live.ok("myevents", "enrollment", eid, user=member, pw=MEMBER_PASSWORD)
        return staff_view, own_view

    assert [r["eligible"] for r in rows()] == [True, True]

    live.ok("user", "update", member, json.dumps({"dateOfBirthUtc": _utc_ms(BORN_TOO_EARLY)}))

    staff_view, own_view = rows()
    assert staff_view["eligible"] is False and own_view["eligible"] is False
    # Nobody is removed automatically.
    assert staff_view["status"] == "assigned"
