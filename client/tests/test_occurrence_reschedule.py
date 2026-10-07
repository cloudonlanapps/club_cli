"""occurrences reschedule sends only what OccurrenceRescheduleRequest declares (club_cli_old#18)."""
from __future__ import annotations

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run

SLOT = "20260503 0030"
SLOT_MS = local_iso_to_utc_ms("2026-05-03T00:30:00")
OCCURRENCE = f"/v1/events/by_id/7/occurrences/{SLOT_MS}"
PATH = f"{OCCURRENCE}/reschedule"


def _fake(monkeypatch, version: int = 4) -> FakeHttp:
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", OCCURRENCE, {"occurrenceTimeUtc": SLOT_MS, "version": version})
    return fake


def test_issue_18_reschedule_sends_start_duration_and_venue(monkeypatch):
    fake = _fake(monkeypatch)
    fake.on("POST", PATH, status=204)

    res = run(
        "occurrences", "reschedule", "7", SLOT,
        "--start-time-local", "2026-05-03T02:00:00", "--duration-minutes", "90", "--venue-id", "9",
    )

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", PATH)
    assert call.json == {
        "newStartTimeUtc": local_iso_to_utc_ms("2026-05-03T02:00:00"),
        "newDurationMinutes": 90,
        "newVenueId": 9,
        "version": 4,
    }


def test_issue_18_reschedule_omits_unset_fields(monkeypatch):
    fake = _fake(monkeypatch)
    fake.on("POST", PATH, status=204)

    res = run("occurrences", "reschedule", "7", SLOT, "--venue-id", "9")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", PATH)[0].json == {"newVenueId": 9, "version": 4}


def test_issue_18_retired_options_are_refused(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    for option in ("--end-time-local", "--organizer-name"):
        res = run("occurrences", "reschedule", "7", SLOT, option, "x")
        assert res.exit_code != 0
        assert "No such option" in res.output, res.output
    assert fake.calls == []


def test_issue_18_help_names_current_error_codes():
    out = run("occurrences", "reschedule", "--help").output
    for code in ("POSTPONE_ONLY", "RESCHEDULE_LEAD_TIME_VIOLATED", "CANCELLED_OCCURRENCE", "INVALID_SESSIONS"):
        assert code in out, code
    assert "PAST_RESCHEDULE_TIME" not in out


# ── Occurrence versions (club_server#430, club_cli_old#33) ─────────


def test_issue_33_cancel_reads_and_sends_the_occurrence_version(monkeypatch):
    fake = _fake(monkeypatch, version=2)
    fake.on("POST", f"{OCCURRENCE}/cancel", {"version": 3})

    res = run("occurrences", "cancel", "7", SLOT, "--reason", "Rain")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", f"{OCCURRENCE}/cancel")[0].json == {"reason": "Rain", "version": 2}


def test_issue_33_cancel_requires_a_reason(monkeypatch):
    """OccurrenceCancelRequest requires reason; refuse locally instead of a 422."""
    fake = _fake(monkeypatch)

    res = run("occurrences", "cancel", "7", SLOT)

    assert res.exit_code != 0
    assert "--reason" in res.output
    assert fake.calls == []


def test_issue_33_undo_cancel_sends_a_version_body(monkeypatch):
    fake = _fake(monkeypatch, version=3)
    fake.on("POST", f"{OCCURRENCE}/undo-cancel", {"version": 4})

    res = run("occurrences", "undo-cancel", "7", SLOT)

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", f"{OCCURRENCE}/undo-cancel")[0].json == {"version": 3}


def test_issue_33_version_option_is_sent_as_given_without_a_read(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    for verb in ("cancel", "undo-cancel", "reschedule"):
        fake.on("POST", f"{OCCURRENCE}/{verb}", {"version": 9})

    assert run("occurrences", "cancel", "7", SLOT, "--reason", "r", "--version", "1").exit_code == 0
    assert run("occurrences", "undo-cancel", "7", SLOT, "--version", "1").exit_code == 0
    assert run("occurrences", "reschedule", "7", SLOT, "--venue-id", "2", "--version", "1").exit_code == 0

    assert all(c.method == "POST" and c.json["version"] == 1 for c in fake.calls)


def test_issue_33_stale_occurrence_version_is_explained(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{OCCURRENCE}/cancel", status=409, payload={"detail": {
        "code": "STALE_VERSION", "version": 5, "updatedAt": SLOT_MS, "updatedBy": "coach_a",
    }})

    res = run("occurrences", "cancel", "7", SLOT, "--reason", "r", "--version", "1")

    assert res.exit_code != 0
    assert "STALE_VERSION: it is at version 5" in res.output
    assert "--version 5" in res.output
