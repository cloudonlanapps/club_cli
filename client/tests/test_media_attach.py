"""`media attach` against a faked HTTP layer.

Split out of test_website_seed.py when the website's seed content and its
importer moved to the club's website repo: these two exercise the CLI's own upload and
link behaviour, not the seed data, so they stay with the CLI.
"""
from __future__ import annotations

def test_issue_12_media_attach_links_a_video_accepted_for_conversion(monkeypatch, tmp_path):
    """A video upload answers 202 (conversion pending); the link must still be made."""
    from .fakes import FakeHttp, FakeResp, run

    file = tmp_path / "clip.mp4"
    file.write_bytes(b"\x00" * 16)
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", responder=lambda call: FakeResp(202, {"uuid": "u-1", "conversionStatus": "pending"}))
    fake.on("POST", "/v1/events/by_id/1/media", {"tag": "event_gallery", "mediaUuid": "u-1"}, status=201)

    res = run("media", "attach", "event", "1", str(file), "--tag", "event_gallery", "--metadata", "videos/clip.mp4")

    assert res.exit_code == 0, res.output
    (link,) = fake.calls_to("POST", "/v1/events/by_id/1/media")
    assert link.json == {"tag": "event_gallery", "mediaUuid": "u-1", "metadata": "videos/clip.mp4"}


def test_issue_12_media_attach_fails_loudly_when_upload_is_refused(monkeypatch, tmp_path):
    from .fakes import FakeHttp, FakeResp, run

    file = tmp_path / "clip.mp4"
    file.write_bytes(b"\x00" * 16)
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", responder=lambda call: FakeResp(413, {"detail": {"code": "TOO_LARGE"}}))

    res = run("media", "attach", "event", "1", str(file), "--tag", "event_gallery")

    assert res.exit_code != 0
    assert fake.calls_to("POST", "/v1/events/by_id/1/media") == []


def test_issue_42_identity_document_uploads_private_and_encrypted(monkeypatch, tmp_path):
    """An identity document is personal data: self and admin only, encrypted, as the app sends it."""
    import json

    from .fakes import FakeHttp, run

    file = tmp_path / "id.png"
    file.write_bytes(b"\x89PNG")
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", {"uuid": "u-1", "filename": "u-1-id.webp"}, status=201)
    fake.on("POST", "/v1/users/by_id/sudo/media", {"tag": "identity_document", "mediaUuid": "u-1"}, status=201)

    res = run("me", "gallery", "add-file", str(file), "--tag", "identity_document")

    assert res.exit_code == 0, res.output
    (up,) = fake.calls_to("POST", "/v1/media")
    assert json.loads(up.data["accessRoles"]) == ["self", "admin"]
    assert up.data["encrypt"] == "true"


def test_issue_42_other_gallery_tags_keep_the_server_defaults(monkeypatch, tmp_path):
    from .fakes import FakeHttp, run

    file = tmp_path / "pic.png"
    file.write_bytes(b"\x89PNG")
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", {"uuid": "u-2", "filename": "u-2-pic.webp"}, status=201)
    fake.on("POST", "/v1/users/by_id/sudo/media", {"tag": "user_avatar", "mediaUuid": "u-2"}, status=201)

    res = run("me", "gallery", "add-file", str(file), "--tag", "user_avatar")

    assert res.exit_code == 0, res.output
    (up,) = fake.calls_to("POST", "/v1/media")
    assert "accessRoles" not in up.data and "encrypt" not in up.data
