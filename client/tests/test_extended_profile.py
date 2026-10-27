"""The extended profile and the club's profile options (club_server#92, cli #38).

`user update` and `me update` take an option for every extended field, a
`--clear` for each, and `--membership-date` (admin); `profile-options get` /
`set` read and replace the club's positions and languages. Preferred positions
and languages are lists (club_server#106, cli #44), and the options are
written through the super admin's preference PATCH (club_server#98).
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

ALICE = "/v1/users/by_id/alice"
OPTIONS = "/v1/system/preferences/profile_options"
OPTIONS_WRITE = "/v1/admin/preferences/profile_options"
APRIL_1_2026_UTC_MS = 1775001600000

TEXT_OPTIONS = [
    ("--club-district", "clubDistrict", "Pune District"),
    ("--past-experience", "pastExperience", "Two seasons\nat the rink"),
    ("--nationality", "nationality", "Indian"),
]

CLEARS = [
    ("club-district", "clubDistrict"),
    ("preferred-position", "preferredPositions"),
    ("past-experience", "pastExperience"),
    ("nationality", "nationality"),
    ("preferred-language", "preferredLanguages"),
    ("guardian", "guardian"),
    ("membership-date", "membershipDateUtc"),
]


def _patched(fake: FakeHttp, path: str = ALICE) -> dict:
    (call,) = fake.calls_to("PATCH", path)
    return call.json


def test_issue_38_user_update_sends_every_extended_text_field(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})
    args = [a for option, _, value in TEXT_OPTIONS for a in (option, value)]

    res = run("user", "update", "alice", *args)

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {field: value for _, field, value in TEXT_OPTIONS}
    assert fake.calls_to("GET", f"{ALICE}/private") == []


@pytest.mark.parametrize("name,field", CLEARS)
def test_issue_38_user_update_clear_sends_null(monkeypatch, name, field):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", "--clear", name)

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {field: None}


def test_issue_38_user_update_clear_takes_several_fields(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", "--clear", "nationality", "--clear", "guardian", "--club-district", "Pune")

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"nationality": None, "guardian": None, "clubDistrict": "Pune"}


def test_issue_38_user_update_membership_date_is_utc_midnight_ms(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", "--membership-date", "2026-04-01")

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"membershipDateUtc": APRIL_1_2026_UTC_MS}


@pytest.mark.parametrize("bad", ["2026-13-01", "01-04-2026", "2026-04-01T00:00:00", "tomorrow"])
def test_issue_38_user_update_refuses_a_malformed_membership_date(monkeypatch, bad):
    fake = FakeHttp().install(monkeypatch)

    res = run("user", "update", "alice", "--membership-date", bad)

    assert res.exit_code != 0
    assert "YYYY-MM-DD" in res.output
    assert fake.calls == []


def test_issue_38_guardian_parts_merge_into_the_stored_guardian(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{ALICE}/private", {"username": "alice", "guardian": {"name": "Ravi", "relation": "Father", "phone": "9000000001"}})
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", "--guardian-phone", "9000000002")

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"guardian": {"name": "Ravi", "relation": "Father", "phone": "9000000002"}}


def test_issue_38_guardian_parts_start_a_guardian_when_none_is_stored(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{ALICE}/private", {"username": "alice", "guardian": None})
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run(
        "user", "update", "alice",
        "--guardian-name", "Meera", "--guardian-relation", "Mother", "--guardian-phone", "+919000000003",
    )

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"guardian": {"name": "Meera", "relation": "Mother", "phone": "+919000000003"}}


def test_issue_38_an_empty_guardian_part_clears_that_part(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{ALICE}/private", {"username": "alice", "guardian": {"name": "Ravi", "relation": "Father", "phone": "9000000001"}})
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", "--guardian-relation", "")

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"guardian": {"name": "Ravi", "relation": None, "phone": "9000000001"}}


def test_issue_38_guardian_parts_merge_into_a_guardian_given_in_the_json(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", '{"guardian": {"name": "Ravi"}}', "--guardian-phone", "9000000001")

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"guardian": {"name": "Ravi", "phone": "9000000001"}}
    assert fake.calls_to("GET", f"{ALICE}/private") == []


def test_issue_38_options_join_the_json_body(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", '{"firstName": "Alice"}', "--nationality", "Indian")

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"firstName": "Alice", "nationality": "Indian"}


def test_issue_38_json_alone_still_updates(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run("user", "update", "alice", '{"firstName": "Alice"}')

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {"firstName": "Alice"}


@pytest.mark.parametrize("args", [
    ['{"nationality": "Indian"}', "--nationality", "Indian"],
    ["--nationality", "Indian", "--clear", "nationality"],
    ["--guardian-name", "Ravi", "--clear", "guardian"],
    ['{"membershipDateUtc": 0}', "--clear", "membership-date"],
])
def test_issue_38_a_field_given_twice_is_refused(monkeypatch, args):
    fake = FakeHttp().install(monkeypatch)

    res = run("user", "update", "alice", *args)

    assert res.exit_code != 0
    assert "twice" in res.output
    assert fake.calls == []


def test_issue_38_user_update_with_nothing_is_refused(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    res = run("user", "update", "alice")

    assert res.exit_code != 0
    assert "Nothing to update" in res.output
    assert fake.calls == []


def test_issue_38_me_update_takes_the_same_options_for_self(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/auth/me", {"username": "sudo", "guardian": None})
    fake.on("PATCH", "/v1/users/by_id/sudo", {"username": "sudo"})

    res = run(
        "me", "update", "--preferred-language", "Hindi", "--guardian-name", "Ravi",
        "--clear", "past-experience", "--membership-date", "2026-04-01",
    )

    assert res.exit_code == 0, res.output
    assert _patched(fake, "/v1/users/by_id/sudo") == {
        "preferredLanguages": ["Hindi"],
        "guardian": {"name": "Ravi"},
        "pastExperience": None,
        "membershipDateUtc": APRIL_1_2026_UTC_MS,
    }


def test_issue_38_me_update_still_converts_json_timestamps(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", "/v1/users/by_id/sudo", {"username": "sudo"})

    res = run("me", "update", '{"dateOfBirth": "2012-05-03T00:00:00"}', "--nationality", "Indian")

    assert res.exit_code == 0, res.output
    body = _patched(fake, "/v1/users/by_id/sudo")
    assert set(body) == {"dateOfBirthUtc", "nationality"}


@pytest.mark.parametrize("command", [["user", "update", "--help"], ["me", "update", "--help"]])
def test_issue_38_update_help_names_every_extended_option(command):
    res = CliRunner().invoke(main, command)

    text = " ".join(res.output.split())
    assert res.exit_code == 0
    for option in (
        "--club-district", "--preferred-position", "--past-experience", "--nationality",
        "--preferred-language", "--guardian-name", "--guardian-relation", "--guardian-phone",
        "--membership-date", "--clear",
    ):
        assert option in text
    assert "INVALID_PROFILE_OPTION" in text
    assert "PROTECTED_FIELDS" in text


# ── preferred positions and languages as lists (club_server#106, cli #44) ──


def test_issue_44_user_update_sends_positions_and_languages_as_lists(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"username": "alice"})

    res = run(
        "user", "update", "alice",
        "--preferred-position", "Forward", "--preferred-position", "Goalie",
        "--preferred-language", "English", "--preferred-language", "Marathi",
    )

    assert res.exit_code == 0, res.output
    assert _patched(fake) == {
        "preferredPositions": ["Forward", "Goalie"],
        "preferredLanguages": ["English", "Marathi"],
    }


def test_issue_44_one_position_is_still_a_list(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", "/v1/users/by_id/sudo", {"username": "sudo"})

    res = run("me", "update", "--preferred-position", "Forward")

    assert res.exit_code == 0, res.output
    assert _patched(fake, "/v1/users/by_id/sudo") == {"preferredPositions": ["Forward"]}


def test_issue_44_positions_given_and_cleared_are_refused(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    res = run("user", "update", "alice", "--preferred-position", "Forward", "--clear", "preferred-position")

    assert res.exit_code != 0
    assert "preferredPositions" in res.output
    assert fake.calls == []


def test_issue_44_positions_in_the_json_and_as_an_option_are_refused(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    res = run("user", "update", "alice", '{"preferredPositions": ["Goalie"]}', "--preferred-position", "Forward")

    assert res.exit_code != 0
    assert "given twice" in res.output
    assert fake.calls == []


@pytest.mark.parametrize("command", [["user", "update", "--help"], ["me", "update", "--help"]])
def test_issue_44_update_help_says_the_choices_repeat(command):
    res = CliRunner().invoke(main, command)

    text = " ".join(res.output.split())
    assert res.exit_code == 0
    assert "preferredPositions" in text and "preferredLanguages" in text
    assert "repeatable" in text
    assert "preferredPosition " not in text and "preferredLanguage " not in text


def test_issue_44_private_and_profile_options_help_name_the_lists():
    private = " ".join(CliRunner().invoke(main, ["user", "private", "--help"]).output.split())
    options = " ".join(CliRunner().invoke(main, ["profile-options", "set", "--help"]).output.split())

    assert "preferredPositions" in private and "preferredLanguages" in private
    assert "preferredPositions" in options
    assert "super admin" in options


# ── profile-options ─────────────────────────────────────────────────────

DOCUMENT = {
    "positions": [{"value": "Forward", "label": {"en": "Forward", "mr": "फॉरवर्ड"}}],
    "languages": ["English", "Marathi"],
}


def test_issue_38_profile_options_get(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", OPTIONS, DOCUMENT)

    res = run("profile-options", "get")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output) == DOCUMENT


def test_issue_44_profile_options_set_from_file_patches_the_preference(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", OPTIONS_WRITE, {"key": "profile_options", "value": DOCUMENT})
    path = tmp_path / "profile_options.json"
    path.write_text(json.dumps(DOCUMENT, ensure_ascii=False), encoding="utf-8")

    res = run("profile-options", "set", "--file", str(path))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", OPTIONS_WRITE)[0].json == {"value": DOCUMENT}
    assert fake.calls_to("PUT", OPTIONS) == []


def test_issue_44_profile_options_set_from_inline_json(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", OPTIONS_WRITE, {"key": "profile_options", "value": {"positions": [], "languages": []}})

    res = run("profile-options", "set", "--json", '{"positions": [], "languages": []}')

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", OPTIONS_WRITE)[0].json == {"value": {"positions": [], "languages": []}}


def test_issue_38_profile_options_set_needs_exactly_one_source(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    path = tmp_path / "o.json"
    path.write_text("{}")

    none = run("profile-options", "set")
    both = run("profile-options", "set", "--json", "{}", "--file", str(path))

    assert none.exit_code != 0 and "--json" in none.output and "--file" in none.output
    assert both.exit_code != 0
    assert fake.calls == []


def test_issue_38_profile_options_set_explains_the_disabled_module(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", OPTIONS_WRITE, {"detail": {"code": "EXTENDED_PROFILE_DISABLED", "message": "off"}}, status=503)

    res = run("profile-options", "set", "--json", '{"positions": [], "languages": []}')

    assert res.exit_code != 0
    assert "EXTENDED_PROFILE_DISABLED: " in res.output
    assert "capabilities" in res.output


def test_issue_38_an_invalid_option_points_at_profile_options(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", ALICE, {"detail": {"code": "INVALID_PROFILE_OPTION", "message": "no"}}, status=422)

    res = run("user", "update", "alice", "--preferred-position", "Striker")

    assert res.exit_code != 0
    assert "INVALID_PROFILE_OPTION: " in res.output
    assert "profile-options get" in res.output


# ── capabilities and the private profile ───────────────────────────────


def test_issue_38_capabilities_help_names_extended_profile():
    res = CliRunner().invoke(main, ["capabilities", "--help"])

    text = " ".join(res.output.split())
    assert res.exit_code == 0
    assert "extendedProfile" in text


def test_issue_38_capabilities_prints_extended_profile(monkeypatch):
    FakeHttp().on("GET", "/v1/capabilities", {"extendedProfile": True}).install(monkeypatch)

    res = run("capabilities")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["extendedProfile"] is True


@pytest.mark.parametrize("command", [["user", "private", "--help"], ["user", "get", "--help"]])
def test_issue_38_private_profile_help_names_the_extended_fields(command):
    res = CliRunner().invoke(main, command)

    text = " ".join(res.output.split())
    assert res.exit_code == 0
    for field in ("clubDistrict", "preferredPosition", "guardian", "membershipDateUtc"):
        assert field in text
