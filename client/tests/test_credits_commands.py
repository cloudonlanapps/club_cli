"""Commands for the endpoints added since release 0.5 (club_cli_old#20).

capabilities, users count, the credit account operations and the member's
own credit views. Evaluations are deliberately left to the app SDK, which the
contract test records.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from club_client.cli import local_iso_to_utc_ms, main

from .fakes import FakeHttp, run

ACCOUNT = {"accountId": "AB12CD34", "membername": "m1", "kind": "event", "balance": 10}
PAGE = {"items": [ACCOUNT], "total": 1, "offset": 0, "limit": 20}
FROM, UNTIL = "2026-05-01T00:00:00", "2026-08-01T00:00:00"
FROM_MS, UNTIL_MS = local_iso_to_utc_ms(FROM), local_iso_to_utc_ms(UNTIL)


def test_issue_20_capabilities(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/capabilities", {"creditSystem": True, "evaluations": False, "eventMarketing": True})

    res = run("capabilities")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["creditSystem"] is True


def test_issue_48_capabilities_without_login(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/capabilities", {"creditSystem": True, "evaluations": False, "eventMarketing": True})

    res = CliRunner().invoke(main, ["capabilities"])

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["creditSystem"] is True
    (call,) = fake.calls_to("GET", "/v1/capabilities")
    assert "Authorization" not in (call.headers or {})


def test_issue_20_users_count(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/users/count", {"byStatus": {"active": 3}, "total": 3})

    res = run("users", "count")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["total"] == 3


def test_issue_20_credits_open_converts_validity_window(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/credits/accounts", ACCOUNT, status=201)

    res = run("credits", "open", json.dumps({
        "membername": "m1", "credits": 10, "validFrom": FROM, "validUntil": UNTIL,
        "reason": "Paid term", "eventId": 7,
    }))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/credits/accounts")
    assert call.json == {
        "membername": "m1", "credits": 10, "validFromUtc": FROM_MS, "validUntilUtc": UNTIL_MS,
        "reason": "Paid term", "eventId": 7,
    }


def test_issue_20_credits_get(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/credits/accounts/AB12CD34", ACCOUNT)

    res = run("credits", "get", "AB12CD34")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["accountId"] == "AB12CD34"


def test_issue_20_credits_list_filters(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/credits/accounts", PAGE)

    res = run("credits", "list", "--membername", "m1", "--event-id", "7", "--kind", "event",
              "--state", "usable", "--trial", "--expiring-before-local", UNTIL, "--limit", "5")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", "/v1/credits/accounts")
    assert call.params == {
        "membername": "m1", "eventId": 7, "kind": "event", "state": "usable", "isTrial": True,
        "expiringBeforeUtc": UNTIL_MS, "offset": 0, "limit": 5,
    }


def test_issue_20_credits_list_omits_unset_filters(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/credits/accounts", PAGE)

    assert run("credits", "list").exit_code == 0
    assert fake.calls_to("GET", "/v1/credits/accounts")[0].params == {"offset": 0, "limit": 20}


def test_issue_20_credits_entries_filters(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/credits/entries", {"items": [], "total": 0, "offset": 0, "limit": 20})

    res = run("credits", "entries", "--membername", "m1", "--account-id", "AB12CD34", "--event-id", "7",
              "--entry-type", "sessionDeduction", "--from", FROM, "--to", UNTIL)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", "/v1/credits/entries")
    assert call.params == {
        "membername": "m1", "accountId": "AB12CD34", "eventId": 7, "entryType": "sessionDeduction",
        "fromTs": FROM_MS, "toTs": UNTIL_MS, "offset": 0, "limit": 20,
    }


def test_issue_47_credits_entries_order(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/credits/entries", {"items": [], "total": 0, "offset": 0, "limit": 20})

    assert run("credits", "entries", "--membername", "m1", "--order", "desc").exit_code == 0
    assert run("credits", "entries", "--membername", "m1").exit_code == 0
    assert run("credits", "entries", "--order", "sideways").exit_code == 2

    ordered, unset = fake.calls_to("GET", "/v1/credits/entries")
    assert ordered.params == {"membername": "m1", "order": "desc", "offset": 0, "limit": 20}
    assert unset.params == {"membername": "m1", "offset": 0, "limit": 20}


def test_issue_47_mycredits_entries_order(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/mycredits/by_id/sudo/entries", {"items": [], "total": 0, "offset": 0, "limit": 20})

    assert run("mycredits", "entries", "--order", "asc").exit_code == 0
    assert run("mycredits", "entries").exit_code == 0

    ordered, unset = fake.calls_to("GET", "/v1/mycredits/by_id/sudo/entries")
    assert ordered.params == {"order": "asc", "offset": 0, "limit": 20}
    assert unset.params == {"offset": 0, "limit": 20}


def test_issue_47_entries_help_documents_running_totals():
    for group in ("credits", "mycredits"):
        out = CliRunner().invoke(main, [group, "entries", "--help"]).output
        assert "balanceAfter" in out and "totalAfter" in out, group


def test_issue_20_credits_extend(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/credits/accounts/AB12CD34/extend", ACCOUNT)

    res = run("credits", "extend", "AB12CD34", "--valid-until-local", UNTIL, "--reason", "Injury")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/credits/accounts/AB12CD34/extend")[0].json == {
        "validUntilUtc": UNTIL_MS, "reason": "Injury",
    }


def test_issue_20_credits_reverse(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/credits/accounts/AB12CD34/reverse", ACCOUNT)

    assert run("credits", "reverse", "AB12CD34", "--reason", "Typo").exit_code == 0
    assert run("credits", "reverse", "AB12CD34", "--reason", "Partial", "--credits", "3").exit_code == 0

    whole, partial = fake.calls_to("POST", "/v1/credits/accounts/AB12CD34/reverse")
    assert whole.json == {"reason": "Typo"}
    assert partial.json == {"reason": "Partial", "credits": 3}


def test_issue_20_credits_transfer(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/credits/accounts/AB12CD34/transfer", {"source": ACCOUNT, "created": None})

    res = run("credits", "transfer", "AB12CD34", "--penalty", "2", "--valid-from-local", FROM,
              "--valid-until-local", UNTIL, "--reason", "Left programme")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", "/v1/credits/accounts/AB12CD34/transfer")[0].json == {
        "penalty": 2, "validFromUtc": FROM_MS, "validUntilUtc": UNTIL_MS, "reason": "Left programme",
    }


def test_issue_20_event_credits(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/events/by_id/7/credits", {"items": [], "total": 0, "offset": 0, "limit": 20})

    res = run("event", "credits", "7", "--state", "blocked", "--expiring-before-local", UNTIL)

    assert res.exit_code == 0, res.output
    assert fake.calls_to("GET", "/v1/events/by_id/7/credits")[0].params == {
        "state": "blocked", "expiringBeforeUtc": UNTIL_MS, "offset": 0, "limit": 20,
    }


def test_issue_45_event_credits_help_documents_bound_credits():
    res = CliRunner().invoke(main, ["event", "credits", "--help"])

    assert res.exit_code == 0, res.output
    assert "boundCredits" in res.output
    assert "withdrawRequested" in res.output
    assert "[blocked|expiringSoon]" in res.output


def test_issue_45_event_credits_state_is_checked_locally(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/events/by_id/7/credits", {"items": [], "total": 0, "offset": 0, "limit": 20})

    # The server ignores a state it does not know and returns the whole
    # roster, so a typo must fail here rather than look like an answer.
    refused = run("event", "credits", "7", "--state", "usable")
    assert refused.exit_code == 2
    assert "usable" in refused.output
    assert fake.calls_to("GET", "/v1/events/by_id/7/credits") == []

    res = run("event", "credits", "7", "--state", "expiringSoon", "--expiring-before-local", UNTIL)
    assert res.exit_code == 0, res.output
    assert fake.calls_to("GET", "/v1/events/by_id/7/credits")[0].params == {
        "state": "expiringSoon", "expiringBeforeUtc": UNTIL_MS, "offset": 0, "limit": 20,
    }


def test_issue_20_mycredits(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/mycredits/by_id/sudo", [ACCOUNT])
    fake.on("GET", "/v1/mycredits/by_id/sudo/accounts/AB12CD34", ACCOUNT)
    fake.on("GET", "/v1/mycredits/by_id/sudo/entries", {"items": [], "total": 0, "offset": 0, "limit": 20})

    assert run("mycredits", "list", "--state", "usable", "--include-closed").exit_code == 0
    assert run("mycredits", "get", "AB12CD34").exit_code == 0
    assert run("mycredits", "entries", "--account-id", "AB12CD34", "--from", FROM).exit_code == 0

    assert fake.calls_to("GET", "/v1/mycredits/by_id/sudo")[0].params == {"state": "usable", "includeClosed": True}
    assert len(fake.calls_to("GET", "/v1/mycredits/by_id/sudo/accounts/AB12CD34")) == 1
    assert fake.calls_to("GET", "/v1/mycredits/by_id/sudo/entries")[0].params == {
        "accountId": "AB12CD34", "fromTs": FROM_MS, "offset": 0, "limit": 20,
    }


def test_issue_20_disabled_module_exits_nonzero(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/credits/accounts", status=503,
            payload={"detail": {"code": "CREDIT_SYSTEM_DISABLED", "message": "off"}})

    res = run("credits", "list")

    assert res.exit_code != 0
    assert "CREDIT_SYSTEM_DISABLED" in res.output
