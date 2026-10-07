"""An admin uploads a file that belongs to another user (club_server#18)."""
from __future__ import annotations

from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

UUID = "0f3c0000-0000-0000-0000-000000000001"
MEDIA = {"uuid": UUID, "id": 7, "mimeType": "image/png", "filename": "photo.png"}


def _photo(tmp_path):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"x")
    return str(photo)


def test_issue_4_add_file_sends_the_owner_as_a_form_field(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", MEDIA)

    res = run("uploads", "add-file", _photo(tmp_path), "--owner", "alice", "--access-role", "self")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/media")
    assert call.data == {"accessRoles": '["self"]', "ownerUsername": "alice"}


def test_issue_4_add_file_without_owner_sends_no_owner_field(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", MEDIA)

    res = run("uploads", "add-file", _photo(tmp_path))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/media")
    assert "ownerUsername" not in call.data


def test_issue_4_attach_uploads_for_the_owner_and_links_as_the_caller(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", MEDIA)
    fake.on("POST", "/v1/users/by_id/alice/media", {"tag": "avatar", "mediaUuid": UUID}, status=201)

    res = run("media", "attach", "user", "alice", _photo(tmp_path), "--tag", "avatar", "--owner", "alice")

    assert res.exit_code == 0, res.output
    (upload,) = fake.calls_to("POST", "/v1/media")
    assert upload.data == {"ownerUsername": "alice"}
    (link,) = fake.calls_to("POST", "/v1/users/by_id/alice/media")
    assert link.json == {"tag": "avatar", "mediaUuid": UUID}


def test_issue_4_attach_without_owner_sends_no_owner_field(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", MEDIA)
    fake.on("POST", "/v1/venues/by_id/1/media", {"tag": "hero", "mediaUuid": UUID}, status=201)

    res = run("media", "attach", "venue", "1", _photo(tmp_path), "--tag", "hero")

    assert res.exit_code == 0, res.output
    (upload,) = fake.calls_to("POST", "/v1/media")
    assert "ownerUsername" not in upload.data


def test_issue_4_help_says_who_may_use_owner_and_what_it_changes():
    for command in (("uploads", "add-file"), ("media", "attach")):
        res = CliRunner().invoke(main, [*command, "--help"])
        text = " ".join(res.output.split())  # click wraps the help

        assert res.exit_code == 0
        assert "--owner" in text
        assert "Admins only" in text
        assert "`self`" in text and "change or delete" in text
