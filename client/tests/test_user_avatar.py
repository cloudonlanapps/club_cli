"""A user has one avatar (club_server#28): a new one replaces the old.

The server does the replacing when a file is linked under `user_avatar`, so
no command gains an option. The help names the tag the server knows.
"""
from __future__ import annotations

from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

UUID = "0f3c0000-0000-0000-0000-000000000001"
MEDIA = {"uuid": UUID, "id": 7, "mimeType": "image/png", "filename": "photo.png"}


def _help(*command: str) -> str:
    res = CliRunner().invoke(main, [*command, "--help"])
    assert res.exit_code == 0
    return " ".join(res.output.split())  # click wraps the help


def test_issue_15_attach_help_names_the_avatar_tag_the_server_knows():
    text = _help("media", "attach")

    assert "media attach user alice ./alice.jpg --tag user_avatar --owner alice" in text
    assert "--tag avatar " not in text


def test_issue_15_attach_and_link_help_say_a_new_avatar_replaces_the_old():
    for command in (("media", "attach"), ("media", "link")):
        text = _help(*command)

        assert "user_avatar" in text
        assert "replaces" in text


def test_issue_15_the_help_example_sends_the_avatar_tag(monkeypatch, tmp_path):
    photo = tmp_path / "alice.jpg"
    photo.write_bytes(b"x")
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/media", MEDIA)
    fake.on("POST", "/v1/users/by_id/alice/media", {"tag": "user_avatar"}, status=201)

    res = run("media", "attach", "user", "alice", str(photo), "--tag", "user_avatar", "--owner", "alice")

    assert res.exit_code == 0, res.output
    (link,) = fake.calls_to("POST", "/v1/users/by_id/alice/media")
    assert link.json == {"tag": "user_avatar", "mediaUuid": UUID}
    assert len(fake.calls) == 2  # upload and link: nothing is detached or deleted
