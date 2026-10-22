"""An admin cannot block, mark as left or delete their own account (club_server#62, cli #27).

The server refuses it with 422 CANNOT_ACT_ON_SELF and refuses the super
admin as a target with 403 SUPER_ADMIN_PROTECTION. The CLI passes both
through; the help names them.
"""
from __future__ import annotations

import pytest

from .fakes import FakeHttp, run

# (command, method, path) for each user verb the server guards.
VERBS = [
    ("block", "POST", "/v1/users/by_id/alice/block"),
    ("mark-left", "POST", "/v1/users/by_id/alice/mark-left"),
    ("delete", "DELETE", "/v1/users/by_id/alice"),
]


def _help(*command: str) -> str:
    res = run(*command, "--help")
    assert res.exit_code == 0, res.output
    return " ".join(res.output.split())  # click wraps the help


@pytest.mark.parametrize("verb", [v[0] for v in VERBS])
def test_issue_27_help_names_the_self_and_super_admin_refusals(verb):
    text = _help("user", verb)

    assert "your own account" in text
    assert "422 CANNOT_ACT_ON_SELF" in text
    assert "403 SUPER_ADMIN_PROTECTION" in text


@pytest.mark.parametrize(("verb", "method", "path"), VERBS)
def test_issue_27_the_self_refusal_is_shown_and_exits_nonzero(monkeypatch, verb, method, path):
    fake = FakeHttp().install(monkeypatch)
    body = {"detail": {"code": "CANNOT_ACT_ON_SELF", "message": "Cannot act on your own account"}}
    fake.on(method, path, body, status=422)

    res = run("user", verb, "alice")

    assert res.exit_code != 0
    assert "CANNOT_ACT_ON_SELF" in res.output
