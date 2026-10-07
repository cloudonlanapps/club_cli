"""Unit tests for the media encryption-at-rest CLI (club_cli_old#9).

These do not touch a server: ``httpx.get`` / ``httpx.post`` are monkeypatched
with a tiny fake so the routing, pagination/de-dup, dry-run, and 503-abort
logic can be exercised offline. (The integration suite in this directory is
skipped without a live stack.)
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from club_client.cli import main


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (
            json.dumps(payload) if payload is not None else ""
        )

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._payload


def _base_args(*rest: str) -> list[str]:
    return [
        "--user", "sudo", "--pw", "x", "media", *rest
    ]


def _install_fakes(monkeypatch, *, links_items, encrypt_status=200, encrypt_body=None):
    encrypt_calls: list[str] = []

    def fake_post(url, **kwargs):
        if url.endswith("/v1/auth/login"):
            return FakeResp(200, {"accessToken": "tok"})
        if "/encrypt" in url:
            encrypt_calls.append(url)
            return FakeResp(
                encrypt_status,
                encrypt_body if encrypt_body is not None else {"isEncrypted": True},
                text="" if encrypt_status == 200 else "err",
            )
        raise AssertionError(f"unexpected POST {url}")

    def fake_get(url, **kwargs):
        if "/v1/media/links" in url:
            return FakeResp(
                200,
                {"items": links_items, "total": len(links_items), "offset": 0, "limit": 100},
            )
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)
    return encrypt_calls


def test_issue_9_encrypt_existing_dedups_and_encrypts(monkeypatch):
    # u1 is linked twice (different owners/tags) — it must be encrypted once.
    items = [
        {"mediaUuid": "u1", "tag": "identity_document"},
        {"mediaUuid": "u2", "tag": "identity_document"},
        {"mediaUuid": "u1", "tag": "id_back"},
    ]
    encrypt_calls = _install_fakes(monkeypatch, links_items=items)

    res = CliRunner().invoke(main, _base_args("encrypt-existing"))

    assert res.exit_code == 0, res.output
    assert len(encrypt_calls) == 2  # u1 deduped
    assert any(c.endswith("/v1/media/by_id/u1/encrypt") for c in encrypt_calls)
    assert any(c.endswith("/v1/media/by_id/u2/encrypt") for c in encrypt_calls)
    assert "encrypted=2 failed=0" in res.output


def test_issue_9_encrypt_existing_dry_run_makes_no_calls(monkeypatch):
    items = [{"mediaUuid": "u1", "tag": "identity_document"}]
    encrypt_calls = _install_fakes(monkeypatch, links_items=items)

    res = CliRunner().invoke(main, _base_args("encrypt-existing", "--dry-run"))

    assert res.exit_code == 0, res.output
    assert encrypt_calls == []
    assert "[dry-run] would encrypt u1" in res.output


def test_issue_9_encrypt_existing_empty_is_clean_noop(monkeypatch):
    _install_fakes(monkeypatch, links_items=[])

    res = CliRunner().invoke(main, _base_args("encrypt-existing"))

    assert res.exit_code == 0, res.output
    assert "Nothing to encrypt" in res.output


def test_issue_9_encrypt_existing_aborts_on_503(monkeypatch):
    items = [{"mediaUuid": "u1", "tag": "identity_document"}]
    _install_fakes(
        monkeypatch, links_items=items, encrypt_status=503,
        encrypt_body={"detail": {"code": "ENCRYPTION_NOT_CONFIGURED"}},
    )

    res = CliRunner().invoke(main, _base_args("encrypt-existing"))

    assert res.exit_code != 0
    assert "aborting sweep" in res.output


def test_issue_9_encrypt_single_uuid(monkeypatch):
    encrypt_calls = _install_fakes(monkeypatch, links_items=[])

    res = CliRunner().invoke(main, _base_args("encrypt", "abc-uuid"))

    assert res.exit_code == 0, res.output
    assert encrypt_calls == ["http://127.0.0.1:8001/v1/media/by_id/abc-uuid/encrypt"]

