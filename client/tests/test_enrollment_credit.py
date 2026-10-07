"""creditDisposition on departures and the mark-attendance result body (club_cli_old#21)."""
from __future__ import annotations

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run

EVENT = "/v1/events/by_id/7"
DISPOSITION = '{"penalty": 2, "validFrom": "2026-05-01T00:00:00", "validUntil": "2026-08-01T00:00:00", "reason": "Left mid-term"}'
EXPECTED_DISPOSITION = {
    "penalty": 2,
    "validFromUtc": local_iso_to_utc_ms("2026-05-01T00:00:00"),
    "validUntilUtc": local_iso_to_utc_ms("2026-08-01T00:00:00"),
    "reason": "Left mid-term",
}


def test_issue_21_remove_sends_credit_disposition(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{EVENT}/enrollments/remove", status=204)

    res = run("enrollment", "remove", "7", "m1", "--reason", "gone", "--credit-disposition", DISPOSITION)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/enrollments/remove")
    assert call.json == {"membernames": ["m1"], "reason": "gone", "creditDisposition": EXPECTED_DISPOSITION}


def test_issue_21_remove_without_disposition_sends_none(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{EVENT}/enrollments/remove", status=204)

    res = run("enrollment", "remove", "7", "m1")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", f"{EVENT}/enrollments/remove")[0].json == {"membernames": ["m1"]}


def test_issue_21_approve_withdraw_sends_credit_disposition(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{EVENT}/enrollments/approve-withdraw", status=204)

    res = run("enrollment", "approve-withdraw", "7", "m1", "--credit-disposition", DISPOSITION)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{EVENT}/enrollments/approve-withdraw")
    assert call.json == {"membernames": ["m1"], "creditDisposition": EXPECTED_DISPOSITION}


def test_issue_21_disposition_accepts_utc_fields_verbatim(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{EVENT}/enrollments/remove", status=204)

    res = run("enrollment", "remove", "7", "m1", "--credit-disposition",
              '{"penalty": 0, "validFromUtc": 1, "validUntilUtc": 2, "reason": "r"}')

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", f"{EVENT}/enrollments/remove")[0].json["creditDisposition"] == {
        "penalty": 0, "validFromUtc": 1, "validUntilUtc": 2, "reason": "r",
    }


def test_issue_21_disposition_required_error_is_surfaced(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{EVENT}/enrollments/remove", status=422,
            payload={"detail": {"code": "CREDIT_DISPOSITION_REQUIRED", "message": "bound balance"}})

    res = run("enrollment", "remove", "7", "m1")

    assert res.exit_code != 0
    assert "CREDIT_DISPOSITION_REQUIRED" in res.output


SLOT = "20260503 0030"
ATTENDANCE = f"/v1/events/by_id/7/occurrences/{local_iso_to_utc_ms('2026-05-03T00:30:00')}/attendance"
RECORDS = '{"records": [{"membername": "m1", "status": "present"}, {"membername": "m2", "status": "present"}]}'


def test_issue_21_mark_attendance_all_marked_exits_zero(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", ATTENDANCE, {"marked": [{"membername": "m1", "status": "present"},
                                            {"membername": "m2", "status": "present"}], "refused": []})

    res = run("occurrences", "mark-attendance", "7", SLOT, RECORDS)

    assert res.exit_code == 0, res.output
    assert '"marked"' in res.output


def test_issue_21_mark_attendance_refusals_exit_nonzero_and_name_members(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", ATTENDANCE, {
        "marked": [{"membername": "m1", "status": "present"}],
        "refused": [{"membername": "m2", "code": "INSUFFICIENT_CREDIT", "message": "no usable credit"}],
    })

    res = run("occurrences", "mark-attendance", "7", SLOT, RECORDS)

    assert res.exit_code != 0
    assert "m2" in res.output and "INSUFFICIENT_CREDIT" in res.output and "no usable credit" in res.output
    assert "1 marked, 1 refused" in res.output


def test_issue_21_mark_attendance_help_describes_result_body():
    out = run("occurrences", "mark-attendance", "--help").output
    assert "refused" in out and "marked" in out


def test_issue_46_mark_attendance_reports_trial_ended_and_exits_zero(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", ATTENDANCE, {
        "marked": [{"membername": "m1", "status": "present"}, {"membername": "m2", "status": "present"}],
        "refused": [],
        "trialEnded": [{"membername": "m1"}, {"membername": "m2"}],
    })

    res = run("occurrences", "mark-attendance", "7", SLOT, RECORDS)

    assert res.exit_code == 0, res.output
    assert "2 trial(s) ended: m1, m2" in res.stderr
    assert "trial(s) ended" not in res.stdout


def test_issue_46_mark_attendance_trial_ended_with_refusals_still_exits_nonzero(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", ATTENDANCE, {
        "marked": [{"membername": "m1", "status": "present"}],
        "refused": [{"membername": "m2", "code": "INSUFFICIENT_CREDIT", "message": "no usable credit"}],
        "trialEnded": [{"membername": "m1"}],
    })

    res = run("occurrences", "mark-attendance", "7", SLOT, RECORDS)

    assert res.exit_code == 1
    assert "1 trial(s) ended: m1" in res.stderr
    assert "1 marked, 1 refused" in res.stderr


def test_issue_46_mark_attendance_no_trial_line_when_none_ended(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", ATTENDANCE, {"marked": [{"membername": "m1", "status": "present"}],
                                 "refused": [], "trialEnded": []})

    res = run("occurrences", "mark-attendance", "7", SLOT, RECORDS)

    assert res.exit_code == 0, res.output
    assert "trial" not in res.stderr


def test_issue_46_mark_attendance_help_shows_trial_ended():
    assert "trialEnded" in run("occurrences", "mark-attendance", "--help").output
