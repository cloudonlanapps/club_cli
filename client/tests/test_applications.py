"""Application templates and their forms (club_server#93, cli #38).

`applications` wraps every /v1/applications route: the templates (list, show,
create, update, delete, duplicate), the four state moves, the label catalogue
and clause library, and the PDFs (preview, own form, a member's form).
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from club_client.cli import main

from .fakes import FakeHttp, FakeResp, run

A = "/v1/applications"
TEMPLATE = {"languages": ["en"], "title": {"en": "Registration"}}
STORED = {"id": 7, "status": "draft", **TEMPLATE}


def _pdf(content: bytes, filename: str | None = None) -> FakeResp:
    headers = {"content-type": "application/pdf"}
    if filename:
        headers["content-disposition"] = f'inline; filename="{filename}"'
    return FakeResp(200, headers=headers, content=content)


def _refusal(code: str, **details) -> dict:
    detail: dict = {"code": code, "message": code.lower()}
    if details:
        detail["details"] = details
    return {"detail": detail}


# ── Templates ───────────────────────────────────────────────────────────


def test_issue_38_list_sends_status_offset_and_limit(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", A, {"items": [], "total": 0, "offset": 40, "limit": 10})

    res = run("applications", "list", "--status", "saved", "--offset", "40", "--limit", "10")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", A)
    assert call.params == {"status": "saved", "offset": 40, "limit": 10}


def test_issue_38_list_without_status_sends_none(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", A, {"items": [], "total": 0, "offset": 0, "limit": 20})

    res = run("applications", "list")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", A)
    assert call.params == {"offset": 0, "limit": 20}


def test_issue_38_list_refuses_an_unknown_status(monkeypatch):
    FakeHttp().install(monkeypatch)

    res = run("applications", "list", "--status", "archived")

    assert res.exit_code != 0
    assert "archived" in res.output


def test_issue_38_show_reads_one_template(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7", STORED)

    res = run("applications", "show", "7")

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == STORED


def test_issue_38_create_posts_the_template_from_a_file(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", A, STORED, status=201)
    path = tmp_path / "template.json"
    path.write_text(json.dumps(TEMPLATE), encoding="utf-8")

    res = run("applications", "create", "--file", str(path))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", A)[0].json == TEMPLATE


def test_issue_38_create_takes_inline_json(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", A, {"id": 8, "status": "draft"}, status=201)

    res = run("applications", "create", "--json", "{}")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", A)[0].json == {}


def test_issue_38_create_needs_exactly_one_source(monkeypatch, tmp_path):
    FakeHttp().install(monkeypatch)
    path = tmp_path / "t.json"
    path.write_text("{}", encoding="utf-8")

    assert run("applications", "create").exit_code != 0
    assert run("applications", "create", "--json", "{}", "--file", str(path)).exit_code != 0


def test_issue_38_update_patches_the_fields_given(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{A}/7", STORED)

    res = run("applications", "update", "7", "--json", '{"subtitle": null, "languages": ["en", "mr"]}')

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{A}/7")[0].json == {"subtitle": None, "languages": ["en", "mr"]}


def test_issue_38_delete_deletes_one_template(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("DELETE", f"{A}/7", STORED)

    res = run("applications", "delete", "7")

    assert res.exit_code == 0, res.output
    assert len(fake.calls_to("DELETE", f"{A}/7")) == 1


@pytest.mark.parametrize("verb", ["duplicate", "save", "publish", "unpublish", "revert"])
def test_issue_38_each_move_posts_its_verb(monkeypatch, verb):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{A}/7/{verb}", STORED)

    res = run("applications", verb, "7")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", f"{A}/7/{verb}")
    assert call.json is None


@pytest.mark.parametrize("command", ["labels", "clauses"])
def test_issue_38_catalogues_are_read(monkeypatch, command):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/{command}", {"languages": ["en", "mr", "hi"]})

    res = run("applications", command)

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["languages"] == ["en", "mr", "hi"]


# ── PDFs ────────────────────────────────────────────────────────────────


def test_issue_38_preview_writes_the_pdf_to_output(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/preview", responder=lambda _c: _pdf(b"%PDF preview", "application-7-demo.player.pdf"))
    out = tmp_path / "p.pdf"

    res = run("applications", "preview", "7", "-o", str(out))

    assert res.exit_code == 0, res.output
    assert out.read_bytes() == b"%PDF preview"
    assert str(out) in res.output


def test_issue_38_preview_defaults_to_the_servers_file_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/preview", responder=lambda _c: _pdf(b"%PDF", "application-7-demo.player.pdf"))

    res = run("applications", "preview", "7")

    assert res.exit_code == 0, res.output
    assert (tmp_path / "application-7-demo.player.pdf").read_bytes() == b"%PDF"
    assert "application-7-demo.player.pdf" in res.output


def test_issue_38_own_form_defaults_to_application_id_username(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form", responder=lambda _c: _pdf(b"%PDF mine"))

    res = run("applications", "form", "7")

    assert res.exit_code == 0, res.output
    # `run` signs in as sudo.
    assert (tmp_path / "application-7-sudo.pdf").read_bytes() == b"%PDF mine"


def test_issue_38_form_for_a_member_uses_the_admin_route(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form/alice", responder=lambda _c: _pdf(b"%PDF alice"))

    res = run("applications", "form", "7", "--user", "alice")

    assert res.exit_code == 0, res.output
    assert (tmp_path / "application-7-alice.pdf").read_bytes() == b"%PDF alice"
    assert fake.calls_to("GET", f"{A}/7/form") == []


def test_issue_38_a_server_file_name_cannot_leave_the_directory(monkeypatch, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form", responder=lambda _c: _pdf(b"%PDF", "../escape.pdf"))

    res = run("applications", "form", "7")

    assert res.exit_code == 0, res.output
    assert not (tmp_path / "escape.pdf").exists()
    assert (work / "escape.pdf").exists()


def test_issue_38_a_refused_form_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form", _refusal("APPLICATION_NOT_ELIGIBLE"), status=422)

    res = run("applications", "form", "7", "-o", "x.pdf")

    assert res.exit_code != 0
    assert "APPLICATION_NOT_ELIGIBLE" in res.output
    assert list(tmp_path.iterdir()) == []


# ── Refusals ────────────────────────────────────────────────────────────


def test_issue_38_incomplete_hint_names_the_missing_texts(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on(
        "POST", f"{A}/7/save",
        _refusal("APPLICATION_INCOMPLETE", missing=["formFooter", "terms[3].body"], language="mr"),
        status=422,
    )

    res = run("applications", "save", "7")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_INCOMPLETE:")
    assert "formFooter, terms[3].body" in hint
    assert "mr" in hint


def test_issue_38_does_not_fit_hint_gives_the_overflow(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{A}/7/save", _refusal("APPLICATION_DOES_NOT_FIT", overflowPt=42), status=422)

    res = run("applications", "save", "7")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_DOES_NOT_FIT:")
    assert "42 pt" in hint


@pytest.mark.parametrize("code", [
    "APPLICATIONS_DISABLED",
    "APPLICATION_NOT_FOUND",
    "APPLICATION_NOT_PUBLISHED",
    "APPLICATION_NOT_ELIGIBLE",
    "APPLICATION_INVALID_REFERENCE",
])
def test_issue_38_application_refusals_get_a_hint(monkeypatch, code):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7", _refusal(code), status=422)

    res = run("applications", "show", "7")

    assert res.exit_code != 0
    assert res.stderr.strip().splitlines()[-1].startswith(f"{code}:")


# ── Help ────────────────────────────────────────────────────────────────


def test_issue_38_group_help_lists_every_command():
    res = CliRunner().invoke(main, ["applications", "--help"])

    assert res.exit_code == 0
    for command in (
        "list", "show", "create", "update", "delete", "duplicate", "save", "publish",
        "unpublish", "revert", "preview", "labels", "clauses", "form",
    ):
        assert f"  {command} " in res.output, command


def test_issue_38_create_help_shows_the_template_shape():
    res = CliRunner().invoke(main, ["applications", "create", "--help"])

    text = " ".join(res.output.split())
    assert res.exit_code == 0
    for field in ("languages", "facts", "ageCategories", "declaration", "formFooter", "labelOverrides"):
        assert field in text


def test_issue_38_capabilities_help_names_applications():
    res = CliRunner().invoke(main, ["capabilities", "--help"])

    text = " ".join(res.output.split())
    assert res.exit_code == 0
    assert "applications" in text
    assert "APPLICATIONS_DISABLED" in text
