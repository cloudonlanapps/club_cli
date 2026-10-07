"""Offline checks for the commands added to cover club_server@main.

The live suite (test_live_surface) runs each of them against a server; these
pin down the parts a server run cannot see: form fields, local refusals,
headers, and the wait before an inquiry is submitted.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from club_client.cli import local_iso_to_utc_ms, main

from .fakes import FakeHttp, run

UUID = "0f3c0000-0000-0000-0000-000000000001"


def test_add_file_sends_access_roles_encrypt_and_trim(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", {"uuid": UUID, "id": 7, "mimeType": "video/mp4", "filename": "clip.mp4"})

    res = run(
        "uploads", "add-file", str(clip), "--access-role", "coach", "--access-role", "admin",
        "--encrypt", "--start", "5", "--duration", "30",
    )

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/media")
    assert call.data == {"accessRoles": '["coach", "admin"]', "encrypt": "true", "start": "5.0", "duration": "30.0"}
    assert res.stdout.strip().endswith(f"/v1/media/by_id/{UUID}/download/clip.mp4")
    assert "id 7: video/mp4" in res.stderr


def test_download_defaults_to_original_and_sends_the_token(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"/v1/media/by_id/{UUID}/download", {"ok": True})

    res = run("upload", "download", UUID)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", f"/v1/media/by_id/{UUID}/download")
    assert call.params == {"variant": "original"}
    assert call.headers == {"Authorization": "Bearer tok"}


def test_download_without_credentials_sends_no_token(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"/v1/media/by_id/{UUID}/download", {"ok": True})

    res = CliRunner().invoke(main, ["upload", "download", UUID])

    assert res.exit_code == 0, res.output
    assert fake.calls[0].headers == {}


def test_download_head_reports_type_and_size(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("HEAD", f"/v1/media/by_id/{UUID}/download",
            headers={"content-type": "image/png", "content-length": "12"})

    res = run("upload", "download", UUID, "--head")

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["contentType"] == "image/png"


def test_download_refuses_retired_variants():
    res = run("upload", "download", UUID, "--variant", "video")
    assert res.exit_code != 0
    assert "animated" in res.output


def test_inquiry_submit_fetches_a_token_and_waits_before_sending(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("club_client.cli.time.sleep", slept.append)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/public/inquiries/token", {"token": "123.sig"})
    fake.on("POST", "/v1/public/inquiries", status=204)

    res = CliRunner().invoke(main, ["public", "inquiry-submit", '{"kind": "contact", "name": "A", "email": "a@example.com", "message": "Hi"}'])

    assert res.exit_code == 0, res.output
    assert slept and slept[0] >= 3
    assert fake.calls_to("POST", "/v1/public/inquiries")[0].json["token"] == "123.sig"


def test_inquiry_submit_with_a_token_sends_at_once(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("club_client.cli.time.sleep", slept.append)
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/public/inquiries", status=204)

    res = CliRunner().invoke(main, ["public", "inquiry-submit", '{"kind": "contact", "token": "t"}'])

    assert res.exit_code == 0, res.output
    assert slept == []


def test_public_commands_send_no_token(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/public/events", {"items": []})

    res = CliRunner().invoke(main, ["public", "events", "--from", "20260101 0000", "--featured"])

    assert res.exit_code == 0, res.output
    (call,) = fake.calls
    assert call.headers is None
    assert call.params["from"] == local_iso_to_utc_ms("2026-01-01T00:00:00")
    assert call.params["featured"] is True


def test_public_event_marketing_batches_several_ids(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/public/events/marketing", [])

    res = CliRunner().invoke(main, ["public", "event-marketing", "a", "b"])

    assert res.exit_code == 0, res.output
    assert fake.calls[0].params == {"ids": "a,b"}


def test_media_set_metadata_needs_exactly_one_of_value_or_clear(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    path = f"/v1/venues/by_id/1/media/hero/{UUID}"
    fake.on("PATCH", path, {"metadata": None})

    assert run("media", "set-metadata", "venue", "1", "hero", UUID).exit_code != 0
    assert run("media", "set-metadata", "venue", "1", "hero", UUID, "x", "--clear").exit_code != 0
    assert run("media", "set-metadata", "venue", "1", "hero", UUID, "--clear").exit_code == 0
    assert fake.calls_to("PATCH", path)[0].json == {"metadata": None}


def test_evaluation_media_uses_the_owner_commands(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/evaluations/by_id/5/media", {})

    assert run("media", "list", "evaluation", "5").exit_code == 0


def test_trash_covers_evaluations_and_templates(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/evaluations/templates/deleted", {"items": []})
    fake.on("POST", "/v1/evaluations/by_id/5/restore", {})
    fake.on("POST", "/v1/media/by_id/9/restore", {})

    assert run("trash", "list", "evaluation-template").exit_code == 0
    assert run("trash", "restore", "evaluation", "5").exit_code == 0
    assert run("trash", "restore", "media", "9").exit_code == 0
    assert run("trash", "list", "media").exit_code != 0  # media has no listing


def test_audit_log_converts_its_time_window(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/audit_log", {"rows": []})

    res = run("audit-log", "--from", "20260901 0000", "--resource-type", "event")

    assert res.exit_code == 0, res.output
    params = fake.calls[0].params
    assert params["from_ts"] == local_iso_to_utc_ms("2026-09-01T00:00:00")
    assert params["resource_type"] == "event"
    assert "to_ts" not in params


def test_disabled_module_is_explained(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", "/v1/evaluations", status=503, payload={"detail": {
        "code": "EVALUATIONS_DISABLED", "message": "Evaluations are not enabled on this deployment",
    }})

    res = run("evaluations", "list")

    assert res.exit_code != 0
    assert "EVALUATIONS_DISABLED: this deployment does not ship evaluations" in res.stderr
