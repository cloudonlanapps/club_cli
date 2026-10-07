"""`auth refresh` sends the refresh token from the run's own login (club_cli_old#53)."""
from __future__ import annotations

from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, run

NEW_PAIR = {"accessToken": "tok2", "refreshToken": "rtok2", "tokenType": "bearer"}


def test_issue_53_refresh_sends_the_login_refresh_token(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/auth/refresh", NEW_PAIR)

    res = run("auth", "refresh")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", "/v1/auth/refresh")
    assert call.json == {"refreshToken": "rtok"}
    assert fake.logins == 1


def test_issue_53_refresh_without_login_sends_no_token(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", "/v1/auth/refresh", {"detail": [{"loc": ["body", "refreshToken"], "msg": "Field required"}]},
            status=422)

    res = CliRunner().invoke(main, ["auth", "refresh"])

    assert res.exit_code == 1, res.output
    assert "requires authentication" not in res.output
    assert fake.calls_to("POST", "/v1/auth/refresh")[0].json == {}
    assert fake.logins == 0


def test_issue_53_refresh_takes_no_token_from_the_user():
    res = run("auth", "refresh", "sometoken")

    assert res.exit_code == 2
    assert "Got unexpected extra argument" in res.output
