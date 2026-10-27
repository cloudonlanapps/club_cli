"""The extended profile and profile options against a live server (club_server#92, cli #38).

cli_test.conf turns the extended profile on and cli_test_modules_off.conf
turns it off. The fields are stored either way; only `profile-options set`
answers 503 while it is off. Preferred positions and languages are lists
(club_server#106, cli #44).
"""
from __future__ import annotations

import json

import pytest

from .conftest import EXPECT_MODULES
from .live import MEMBER_PASSWORD, Live

APRIL_1_2026_UTC_MS = 1775001600000
OPTIONS = {
    "positions": [
        {"value": "Forward", "label": {"en": "Forward", "mr": "फॉरवर्ड"}},
        {"value": "Goalie", "label": {"en": "Goalie"}},
    ],
    "languages": [{"code": "en", "name": "English"}, {"code": "mr", "name": "Marathi"}],
}


def _private(live: Live, username: str) -> dict:
    return live.ok("user", "private", username)


def test_issue_38_capabilities_report_extended_profile(live: Live):
    assert "extendedProfile" in live.caps, live.caps
    if EXPECT_MODULES is None:
        pytest.skip("CLUB_EXPECT_MODULES not set; nothing to compare against")
    assert live.caps["extendedProfile"] is (EXPECT_MODULES == "on")


@pytest.mark.modules_on
def test_issue_38_member_sets_and_clears_every_own_extended_field(live: Live, tmp_path):
    path = tmp_path / "profile_options.json"
    path.write_text(json.dumps(OPTIONS, ensure_ascii=False), encoding="utf-8")
    assert live.ok("profile-options", "set", "--file", str(path))["value"] == OPTIONS
    member = live.active_member()
    assert live.ok("profile-options", "get", user=member, pw=MEMBER_PASSWORD) == OPTIONS

    live.ok(
        "me", "update",
        "--club-district", "Pune District", "--preferred-position", "Forward",
        "--past-experience", "Two seasons\nat the rink", "--nationality", "Indian",
        "--preferred-language", "Marathi",
        "--guardian-name", "Ravi", "--guardian-relation", "Father", "--guardian-phone", "9000000001",
        user=member, pw=MEMBER_PASSWORD,
    )
    stored = _private(live, member)
    assert {k: stored[k] for k in (
        "clubDistrict", "preferredPositions", "pastExperience", "nationality", "preferredLanguages", "guardian",
    )} == {
        "clubDistrict": "Pune District", "preferredPositions": ["Forward"],
        "pastExperience": "Two seasons\nat the rink", "nationality": "Indian",
        "preferredLanguages": ["Marathi"],
        "guardian": {"name": "Ravi", "relation": "Father", "phone": "9000000001"},
    }

    # One guardian part changes; the others are kept.
    live.ok("me", "update", "--guardian-phone", "9000000002", user=member, pw=MEMBER_PASSWORD)
    assert _private(live, member)["guardian"] == {"name": "Ravi", "relation": "Father", "phone": "9000000002"}

    live.ok("me", "update", "--clear", "past-experience", "--clear", "guardian", user=member, pw=MEMBER_PASSWORD)
    cleared = _private(live, member)
    assert cleared["pastExperience"] is None and cleared["guardian"] is None
    assert cleared["nationality"] == "Indian"


@pytest.mark.modules_off
def test_issue_38_extended_fields_are_stored_while_the_module_is_off(live: Live):
    res = live.refused("profile-options", "set", "--json", json.dumps(OPTIONS))
    assert "EXTENDED_PROFILE_DISABLED" in res.output
    member = live.active_member()

    live.ok("me", "update", "--nationality", "Indian", "--guardian-name", "Ravi", user=member, pw=MEMBER_PASSWORD)

    stored = _private(live, member)
    assert stored["nationality"] == "Indian"
    assert stored["guardian"]["name"] == "Ravi"


def test_issue_38_a_choice_outside_the_club_options_is_refused(live: Live):
    member = live.active_member()

    res = live.refused("me", "update", "--preferred-position", live.unique("Nowhere"), user=member, pw=MEMBER_PASSWORD)

    assert "INVALID_PROFILE_OPTION" in res.output
    assert _private(live, member)["preferredPositions"] == []


def test_issue_38_admin_sets_and_clears_the_membership_date(live: Live):
    member = live.active_member()

    live.ok("user", "update", member, "--membership-date", "2026-04-01")
    assert _private(live, member)["membershipDateUtc"] == APRIL_1_2026_UTC_MS

    live.ok("user", "update", member, "--clear", "membership-date")
    assert _private(live, member)["membershipDateUtc"] is None


def test_issue_38_a_member_cannot_set_their_own_membership_date(live: Live):
    member = live.active_member()

    res = live.refused("me", "update", "--membership-date", "2026-04-01", user=member, pw=MEMBER_PASSWORD)

    assert "PROTECTED_FIELDS" in res.output


def test_issue_38_an_unapproved_user_sets_their_own_extended_fields(live: Live):
    username = live.unique("reg")
    live.ok("users", "register", json.dumps(live.member_json(username)), anonymous=True)
    assert live.ok("user", "get", username)["status"] in ("registered", "pending")

    live.ok(
        "me", "update", "--nationality", "Indian", "--guardian-relation", "Mother",
        user=username, pw=MEMBER_PASSWORD,
    )

    stored = _private(live, username)
    assert stored["nationality"] == "Indian"
    assert stored["guardian"] == {"name": None, "relation": "Mother", "phone": None}


@pytest.mark.modules_on
def test_issue_44_several_positions_and_languages_are_stored_as_lists(live: Live):
    live.ok("profile-options", "set", "--json", json.dumps(OPTIONS, ensure_ascii=False))
    member = live.active_member()

    live.ok(
        "user", "update", member,
        "--preferred-position", "Forward", "--preferred-position", "Goalie",
        "--preferred-language", "English", "--preferred-language", "Marathi",
    )

    stored = _private(live, member)
    assert stored["preferredPositions"] == ["Forward", "Goalie"]
    assert stored["preferredLanguages"] == ["English", "Marathi"]

    live.ok("user", "update", member, "--clear", "preferred-position")
    assert _private(live, member)["preferredPositions"] == []


@pytest.mark.modules_on
def test_issue_44_one_unknown_value_among_several_is_refused(live: Live):
    live.ok("profile-options", "set", "--json", json.dumps(OPTIONS, ensure_ascii=False))
    member = live.active_member()

    res = live.refused(
        "user", "update", member, "--preferred-position", "Forward", "--preferred-position", live.unique("Nowhere"),
    )

    assert "INVALID_PROFILE_OPTION" in res.output
    assert _private(live, member)["preferredPositions"] == []


@pytest.mark.modules_on
def test_issue_44_profile_options_set_is_the_preference_write(live: Live):
    row = live.ok("profile-options", "set", "--json", json.dumps(OPTIONS, ensure_ascii=False))

    assert row["key"] == "profile_options" and row["value"] == OPTIONS
    assert live.ok("preferences", "get", "profile_options")["value"] == OPTIONS


# ── the club's languages as {code, name} (club_server#133, cli #60) ─────


@pytest.mark.modules_on
def test_issue_60_a_language_written_by_name_takes_its_code(live: Live):
    named = {**OPTIONS, "languages": ["English", "Marathi"]}

    row = live.ok("profile-options", "set", "--json", json.dumps(named, ensure_ascii=False))

    assert row["value"] == OPTIONS
    assert live.ok("profile-options", "get")["languages"] == OPTIONS["languages"]


@pytest.mark.modules_on
def test_issue_60_set_languages_replaces_the_languages_and_keeps_the_positions(live: Live):
    live.ok("profile-options", "set", "--json", json.dumps(OPTIONS, ensure_ascii=False))
    try:
        row = live.ok("profile-options", "set-languages", "en=English", "Marathi", "kok=Konkani")

        assert row["value"]["positions"] == OPTIONS["positions"]
        assert row["value"]["languages"] == [
            {"code": "en", "name": "English"}, {"code": "mr", "name": "Marathi"}, {"code": "kok", "name": "Konkani"},
        ]
        assert "kok" in live.ok("applications", "labels")["languages"]
    finally:
        live.ok("profile-options", "set", "--json", json.dumps(OPTIONS, ensure_ascii=False))


@pytest.mark.modules_on
def test_issue_60_the_languages_need_english(live: Live):
    res = live.refused("profile-options", "set-languages", "mr=Marathi")

    assert "English" in res.output
