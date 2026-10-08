"""A media record is looked up by its uuid (club_server#27).

The link commands show a file's uuid; the commands that change a file take
its numeric id. `upload by-uuid` turns the one into the other.
"""
from __future__ import annotations

from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

UUID = "0f3c0000-0000-0000-0000-000000000001"
MEDIA = {
    "id": 7,
    "uuid": UUID,
    "filename": "photo.png",
    "mediaType": "image",
    "accessRoles": ["self"],
    "uploadedBy": "alice",
    "deletedAtUtc": None,
}


def test_issue_14_by_uuid_reads_the_record_from_the_by_uuid_route(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"/v1/media/by_uuid/{UUID}", MEDIA)

    res = run("upload", "by-uuid", UUID)

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", f"/v1/media/by_uuid/{UUID}")
    assert call.headers["Authorization"] == "Bearer tok"
    assert '"id": 7' in res.output
    assert '"uploadedBy": "alice"' in res.output


def test_issue_14_by_uuid_exits_non_zero_when_the_file_is_not_found(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on(
        "GET",
        f"/v1/media/by_uuid/{UUID}",
        {"detail": {"code": "MEDIA_NOT_FOUND", "message": "Media not found"}},
        status=404,
    )

    res = run("upload", "by-uuid", UUID)

    assert res.exit_code == 1
    assert "MEDIA_NOT_FOUND" in res.output


def test_issue_14_help_says_what_the_id_is_for():
    res = CliRunner().invoke(main, ["upload", "by-uuid", "--help"])
    text = " ".join(res.output.split())  # click wraps the help

    assert res.exit_code == 0
    assert "uuid" in text
    assert "upload set-access" in text and "upload delete" in text
