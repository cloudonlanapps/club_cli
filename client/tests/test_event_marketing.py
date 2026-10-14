"""event marketing get/set/clear (club_cli_old#14)."""
from __future__ import annotations

import json

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run

MK = "/v1/events/by_id/7/marketing"
BLOCK = {
    "durationText": "6:00 AM - 7:45 AM",
    "scheduleText": "Every Saturday & Sunday",
    "eligibilityText": "5+ years to Adults",
    "eligibilityNote": "All skill levels welcome",
    "registrationDeadline": "2026-10-01T00:00:00",
    "hasOpenSlots": True,
    "urgencyText": "Limited seats per batch!",
    "contactNumber": None,
    "fee": 12272,
    "feeStructure": [{"name": "Monthly", "amount": 12272, "period": "month"}],
    "packageOffers": [{"name": "Quarter", "price": 33000, "features": ["3 months"]}],
    "offers": [{"title": "Early bird", "validUntil": "2026-09-15T00:00:00"}],
    "clubMembership": {"title": "Club member", "benefits": ["Discounts"]},
    "facilities": [{"name": "Skates", "iconName": "skate"}],
}
EXPECTED = {
    **{k: v for k, v in BLOCK.items() if k not in ("registrationDeadline", "offers")},
    "registrationDeadlineUtc": local_iso_to_utc_ms("2026-10-01T00:00:00"),
    "offers": [{"title": "Early bird", "validUntilUtc": local_iso_to_utc_ms("2026-09-15T00:00:00")}],
}


def test_issue_14_get(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", MK, {**EXPECTED, "eventId": 7})

    res = run("event", "marketing", "get", "7")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["eventId"] == 7


def test_issue_14_set_from_file_converts_local_times(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", MK, {**EXPECTED, "eventId": 7})
    path = tmp_path / "marketing.json"
    path.write_text(json.dumps(BLOCK))

    res = run("event", "marketing", "set", "7", "--file", str(path), "--version", "2")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PUT", MK)[0].json == {**EXPECTED, "version": 2}


def test_issue_14_set_from_json(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", MK, {"eventId": 7})

    res = run("event", "marketing", "set", "7", "--json", '{"fee": 500, "hasOpenSlots": false}', "--version", "2")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PUT", MK)[0].json == {"fee": 500, "hasOpenSlots": False, "version": 2}


def test_issue_14_set_refuses_unknown_fields_locally(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    res = run("event", "marketing", "set", "7", "--json", '{"tagline": "x", "themeColor": 1, "fee": 1}')

    assert res.exit_code != 0
    assert "tagline" in res.output and "themeColor" in res.output
    assert fake.calls == []


def test_issue_14_set_requires_one_source(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    assert run("event", "marketing", "set", "7").exit_code != 0
    assert fake.calls == []


def test_issue_14_clear(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("DELETE", MK, status=204)

    res = run("event", "marketing", "clear", "7", "--version", "4")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("DELETE", MK)
    assert call.params == {"version": 4}


def test_issue_14_module_off_is_said_plainly(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", MK, status=503, payload={"detail": {"code": "EVENT_MARKETING_DISABLED", "message": "off"}})

    res = run("event", "marketing", "set", "7", "--json", '{"fee": 1}', "--version", "1")

    assert res.exit_code != 0
    assert "EVENT_MARKETING_ENABLED" in res.output


def test_issue_1_set_and_clear_send_the_version_read_from_the_block(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", MK, {"eventId": 7, "version": 3})
    fake.on("PUT", MK, {"eventId": 7, "version": 4})
    fake.on("DELETE", MK, status=204)

    assert run("event", "marketing", "set", "7", "--json", '{"fee": 1}').exit_code == 0
    assert run("event", "marketing", "clear", "7").exit_code == 0

    assert fake.calls_to("PUT", MK)[0].json == {"fee": 1, "version": 3}
    assert fake.calls_to("DELETE", MK)[0].params == {"version": 3}


def test_issue_1_an_event_with_no_block_is_at_version_1(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", MK, status=404, payload={"detail": {"code": "EVENT_MARKETING_NOT_FOUND", "message": "none"}})
    fake.on("PUT", MK, {"eventId": 7, "version": 2})

    res = run("event", "marketing", "set", "7", "--json", '{"fee": 1}')

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PUT", MK)[0].json == {"fee": 1, "version": 1}


def test_issue_1_a_missing_event_is_not_read_as_version_1(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", MK, status=404, payload={"detail": {"code": "EVENT_NOT_FOUND", "message": "none"}})

    res = run("event", "marketing", "clear", "7")

    assert res.exit_code != 0
    assert "EVENT_NOT_FOUND" in res.output
    assert fake.calls_to("DELETE", MK) == []


def test_issue_1_a_stale_block_says_who_changed_it(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", MK, status=409, payload={"detail": {
        "code": "STALE_VERSION", "message": "The event marketing was changed since you last loaded it",
        "version": 5, "updatedAt": 0, "updatedBy": "coach_a",
    }})

    res = run("event", "marketing", "set", "7", "--json", '{"fee": 1}', "--version", "4")

    assert res.exit_code != 0
    assert "STALE_VERSION: it is at version 5" in res.output
    assert "by coach_a" in res.output and "--version 5" in res.output
