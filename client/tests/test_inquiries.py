"""inquiries list/get/handle/unhandle/delete (club_cli_old#13)."""
from __future__ import annotations

import json

from .fakes import FakeHttp, run

INQ = "/v1/admin/inquiries"
ROW = {"id": 7, "kind": "contact", "name": "N", "email": "n@x", "phone": None, "message": "hi",
       "extra": None, "createdAtUtc": 1, "handledAt": None, "handledBy": None}


def test_issue_13_list_filters(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", INQ, {"items": [ROW], "total": 1, "offset": 0, "limit": 20})

    assert run("inquiries", "list").exit_code == 0
    assert run("inquiries", "list", "--kind", "interest", "--unhandled", "--limit", "5").exit_code == 0
    assert run("inquiries", "list", "--handled").exit_code == 0

    params = [c.params for c in fake.calls_to("GET", INQ)]
    assert params == [
        {"offset": 0, "limit": 20},
        {"kind": "interest", "handled": False, "offset": 0, "limit": 5},
        {"handled": True, "offset": 0, "limit": 20},
    ]


def test_issue_13_list_rejects_unknown_kind():
    res = run("inquiries", "list", "--kind", "spam")
    assert res.exit_code != 0 and "Invalid value" in res.output


def test_issue_13_get_finds_the_row_across_pages(monkeypatch):
    """There is no GET by id on the server; get pages through the listing."""
    fake = FakeHttp().install(monkeypatch)
    page1 = {"items": [{**ROW, "id": i} for i in range(1, 101)], "total": 102, "offset": 0, "limit": 100}
    page2 = {"items": [{**ROW, "id": 101}, {**ROW, "id": 102}], "total": 102, "offset": 100, "limit": 100}
    fake.on("GET", INQ, responder=lambda call: _page(call, page1, page2))

    res = run("inquiries", "get", "102")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["id"] == 102


def _page(call, page1, page2):
    from .fakes import FakeResp
    return FakeResp(200, page2 if call.params.get("offset") else page1)


def test_issue_13_get_unknown_id_fails(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", INQ, {"items": [ROW], "total": 1, "offset": 0, "limit": 100})

    res = run("inquiries", "get", "99")

    assert res.exit_code != 0
    assert "99" in res.output


def test_issue_13_handle_and_unhandle(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{INQ}/7", {**ROW, "handledAt": 5, "handledBy": "sudo"})

    assert run("inquiries", "handle", "7").exit_code == 0
    assert run("inquiries", "unhandle", "7").exit_code == 0

    assert [c.json for c in fake.calls_to("PATCH", f"{INQ}/7")] == [{"handled": True}, {"handled": False}]


def test_issue_13_delete_asks_first_and_yes_skips_the_prompt(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("DELETE", f"{INQ}/7", status=204)

    from click.testing import CliRunner
    from club_client.cli import main

    declined = CliRunner().invoke(main, ["--user", "sudo", "--pw", "x", "inquiries", "delete", "7"], input="n\n")
    assert declined.exit_code != 0
    assert fake.calls_to("DELETE", f"{INQ}/7") == []

    confirmed = run("inquiries", "delete", "7", "--yes")
    assert confirmed.exit_code == 0, confirmed.output
    assert len(fake.calls_to("DELETE", f"{INQ}/7")) == 1
