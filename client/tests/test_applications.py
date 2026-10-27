"""Application templates and their forms (club_server#93, cli #38).

`applications` wraps every /v1/applications route: the templates (list, show,
create, update, delete, duplicate), the four state moves, the label catalogue
and clause library, and the PDFs (preview, own form, a member's form).
A template's printed texts are language maps that may hold `{fact}` gaps, filled
at print; `library` keeps the club's own texts (club_server#126, cli #56).
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


# ── The blank form (club_server#129, cli #58) ──────────────────────────


def test_issue_58_blank_form_writes_the_pdf_to_output(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/blank-form", responder=lambda _c: _pdf(b"%PDF blank", "application-7-blank.pdf"))
    out = tmp_path / "b.pdf"

    res = run("applications", "blank-form", "7", "-o", str(out))

    assert res.exit_code == 0, res.output
    assert out.read_bytes() == b"%PDF blank"
    assert str(out) in res.output
    assert fake.calls_to("GET", f"{A}/7/preview") == []


def test_issue_58_blank_form_defaults_to_application_id_blank(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/blank-form", responder=lambda _c: _pdf(b"%PDF blank"))

    res = run("applications", "blank-form", "7")

    assert res.exit_code == 0, res.output
    assert (tmp_path / "application-7-blank.pdf").read_bytes() == b"%PDF blank"
    assert "application-7-blank.pdf" in res.output


def test_issue_58_blank_form_takes_the_servers_file_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/blank-form", responder=lambda _c: _pdf(b"%PDF", "application-7-blank.pdf"))

    res = run("applications", "blank-form", "7")

    assert res.exit_code == 0, res.output
    assert (tmp_path / "application-7-blank.pdf").read_bytes() == b"%PDF"


def test_issue_58_a_refused_blank_form_writes_nothing_and_says_save_first(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/blank-form", _refusal("APPLICATION_NOT_SAVED"), status=422)

    res = run("applications", "blank-form", "7")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_NOT_SAVED:")
    assert "save" in hint and "applications save" in hint
    assert list(tmp_path.iterdir()) == []


def test_issue_58_group_help_names_the_blank_form():
    res = CliRunner().invoke(main, ["applications", "--help"])

    assert res.exit_code == 0
    assert "`blank-form`" in res.output
    assert "drawn per request" not in res.output


def test_issue_58_blank_form_help_says_admin_and_no_member():
    res = CliRunner().invoke(main, ["applications", "blank-form", "--help"])

    assert res.exit_code == 0
    assert "admin" in res.output
    assert "APPLICATION_NOT_SAVED" in res.output
    assert "--output" in res.output


# ── The age-category choice on download (club_server#108, cli #46) ──────


def test_issue_46_own_form_sends_the_category(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form", responder=lambda _c: _pdf(b"%PDF mine"))

    res = run("applications", "form", "7", "--category", "1")

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("GET", f"{A}/7/form")
    assert call.params == {"category": 1}
    assert (tmp_path / "application-7-sudo.pdf").read_bytes() == b"%PDF mine"


def test_issue_46_a_members_form_sends_the_category(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form/alice", responder=lambda _c: _pdf(b"%PDF alice"))

    res = run("applications", "form", "7", "--user", "alice", "--category", "0", "-o", str(tmp_path / "a.pdf"))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("GET", f"{A}/7/form/alice")[0].params == {"category": 0}


def test_issue_46_without_a_category_none_is_sent(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form", responder=lambda _c: _pdf(b"%PDF"))

    res = run("applications", "form", "7", "-o", str(tmp_path / "f.pdf"))

    assert res.exit_code == 0, res.output
    assert not fake.calls_to("GET", f"{A}/7/form")[0].params


def test_issue_46_a_negative_category_is_refused(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)

    res = run("applications", "form", "7", "--category", "-1", "-o", str(tmp_path / "f.pdf"))

    assert res.exit_code != 0
    assert fake.calls == []


@pytest.mark.parametrize("code", ["APPLICATION_CATEGORY_REQUIRED", "APPLICATION_CATEGORY_NOT_FITTING"])
def test_issue_46_a_category_refusal_names_the_fitting_ones(monkeypatch, tmp_path, code):
    monkeypatch.chdir(tmp_path)
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7/form", _refusal(code, categories=[0, 2]), status=422)

    res = run("applications", "form", "7")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith(f"{code}: ")
    assert "--category" in hint
    assert hint.endswith("Fits categories: 0, 2.")
    assert list(tmp_path.iterdir()) == []


def test_issue_46_form_and_show_help_explain_the_choice():
    form = " ".join(CliRunner().invoke(main, ["applications", "form", "--help"]).output.split())
    show = " ".join(CliRunner().invoke(main, ["applications", "show", "--help"]).output.split())

    for word in ("--category", "APPLICATION_CATEGORY_REQUIRED", "APPLICATION_CATEGORY_NOT_FITTING", "fittingCategories"):
        assert word in form, word
    assert "fittingCategories" in show


# ── Refusals ────────────────────────────────────────────────────────────


def test_issue_50_incomplete_hint_names_each_text_with_its_languages(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on(
        "POST", f"{A}/7/save",
        _refusal("APPLICATION_INCOMPLETE", missing=[
            {"field": "formFooter", "languages": ["mr"]},
            {"field": "terms[3].body", "languages": ["en", "mr"]},
        ]),
        status=422,
    )

    res = run("applications", "save", "7")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_INCOMPLETE:")
    assert hint.endswith("Missing: formFooter (mr); terms[3].body (en, mr).")
    assert "None" not in hint


def test_issue_50_incomplete_hint_without_details_is_just_the_hint(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{A}/7/save", _refusal("APPLICATION_INCOMPLETE"), status=422)

    res = run("applications", "save", "7")

    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_INCOMPLETE:")
    assert "Missing" not in hint


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
    "APPLICATION_NOT_SAVED",
    "APPLICATION_NOT_ELIGIBLE",
    "APPLICATION_INVALID_REFERENCE",
    "APPLICATION_UNKNOWN_FACT",
    "APPLICATION_LIBRARY_TEXT_NOT_FOUND",
])
def test_issue_38_application_refusals_get_a_hint(monkeypatch, code):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{A}/7", _refusal(code), status=422)

    res = run("applications", "show", "7")

    assert res.exit_code != 0
    assert res.stderr.strip().splitlines()[-1].startswith(f"{code}:")


# ── Gaps naming no fact (club_server#126, cli #56) ─────────────────────


def test_issue_56_unknown_fact_hint_names_each_place_language_and_gap(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on(
        "POST", f"{A}/7/save",
        _refusal("APPLICATION_UNKNOWN_FACT", gaps=[
            {"place": "selections[0]", "language": "mr", "gap": "camp_dates"},
            {"place": "terms[1].body", "language": "en", "gap": "venue"},
        ]),
        status=422,
    )

    res = run("applications", "save", "7")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_UNKNOWN_FACT: ")
    assert "facts" in hint
    assert hint.endswith("Gaps: selections[0] (mr): {camp_dates}; terms[1].body (en): {venue}.")


def test_issue_56_unknown_fact_hint_without_details_is_just_the_hint(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{A}/7/save", _refusal("APPLICATION_UNKNOWN_FACT"), status=422)

    res = run("applications", "save", "7")

    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_UNKNOWN_FACT: ")
    assert "Gaps:" not in hint


def test_issue_56_save_help_names_the_unknown_fact_refusal():
    text = " ".join(CliRunner().invoke(main, ["applications", "save", "--help"]).output.split())

    assert "APPLICATION_UNKNOWN_FACT" in text
    assert "details.gaps" in text


def test_issue_56_clause_help_describes_the_list_and_fill_clause_is_gone():
    clauses = " ".join(CliRunner().invoke(main, ["applications", "clauses", "--help"]).output.split())
    gone = CliRunner().invoke(main, ["applications", "fill-clause", "--help"])

    # club_server#133 (cli #60): one list of library texts, no source or gaps.
    for word in ("key", "id", "applications library"):
        assert word in clauses, word
    assert gone.exit_code != 0


def test_issue_56_template_help_explains_gaps_and_facts():
    text = " ".join(CliRunner().invoke(main, ["applications", "create", "--help"]).output.split())

    assert "fill-clause" not in text
    assert "{name}" in text and "APPLICATION_UNKNOWN_FACT" in text
    assert '"heading": {lang: text}, "body": {lang: text}' in text


# ── The club's library of texts (club_server#126, cli #56) ──────────────

LIB = f"{A}/library"
LIB_TEXT = {
    "id": 4, "kind": "rule", "name": "Fee", "heading": {"en": "Fee"},
    "text": {"en": "The fee is ₹{amount}.", "mr": "शुल्क ₹{amount} आहे."},
    "createdBy": "sudo", "createdAtUtc": 1, "updatedAtUtc": 1,
}


def test_issue_56_library_list_reads_every_club_text(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", LIB, [LIB_TEXT])

    res = run("applications", "library", "list")

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == [LIB_TEXT]
    (call,) = fake.calls_to("GET", LIB)
    assert not call.params


def test_issue_56_library_get_reads_one_text(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{LIB}/4", LIB_TEXT)

    res = run("applications", "library", "get", "4")

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout) == LIB_TEXT


def test_issue_56_library_create_sends_kind_name_heading_and_each_language(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", LIB, LIB_TEXT, status=201)

    res = run(
        "applications", "library", "create", "--kind", "rule", "--name", "Fee",
        "--text", "en", "The fee is ₹{amount}.", "--text", "mr", "शुल्क ₹{amount} आहे.",
        "--heading", "en", "Fee",
    )

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", LIB)
    assert call.json == {
        "kind": "rule", "name": "Fee", "heading": {"en": "Fee"},
        "text": {"en": "The fee is ₹{amount}.", "mr": "शुल्क ₹{amount} आहे."},
    }


def test_issue_56_library_create_without_heading_sends_none(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", LIB, {**LIB_TEXT, "kind": "note", "heading": None}, status=201)

    res = run("applications", "library", "create", "--kind", "note", "--name", "Rules", "--text", "en", "Follow them.")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", LIB)[0].json == {"kind": "note", "name": "Rules", "text": {"en": "Follow them."}}


@pytest.mark.parametrize("args", [
    ("--name", "Fee", "--text", "en", "x"),                         # no kind
    ("--kind", "rule", "--text", "en", "x"),                        # no name
    ("--kind", "rule", "--name", "Fee"),                            # no text
    ("--kind", "footer", "--name", "Fee", "--text", "en", "x"),     # unknown kind
    ("--kind", "rule", "--name", "Fee", "--text", "en", "x", "--text", "en", "y"),  # a language twice
])
def test_issue_56_library_create_refuses_a_bad_command_line(monkeypatch, args):
    fake = FakeHttp().install(monkeypatch)

    res = run("applications", "library", "create", *args)

    assert res.exit_code != 0
    assert fake.calls == []


def test_issue_56_library_update_sends_only_the_fields_given(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{LIB}/4", LIB_TEXT)

    res = run("applications", "library", "update", "4", "--name", "Registration fee")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{LIB}/4")[0].json == {"name": "Registration fee"}


def test_issue_56_library_update_sends_every_field(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{LIB}/4", LIB_TEXT)

    res = run(
        "applications", "library", "update", "4", "--kind", "rule", "--name", "Fee",
        "--text", "en", "Pay ₹{amount}.", "--text", "hi", "₹{amount} दें।", "--heading", "hi", "शुल्क",
    )

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{LIB}/4")[0].json == {
        "kind": "rule", "name": "Fee", "heading": {"hi": "शुल्क"},
        "text": {"en": "Pay ₹{amount}.", "hi": "₹{amount} दें।"},
    }


def test_issue_56_library_update_clears_the_heading(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{LIB}/4", {**LIB_TEXT, "kind": "note", "heading": None})

    res = run("applications", "library", "update", "4", "--clear-heading", "--kind", "note")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{LIB}/4")[0].json == {"kind": "note", "heading": None}


@pytest.mark.parametrize("args", [
    (),                                                     # nothing to change
    ("--heading", "en", "Fee", "--clear-heading"),          # set and clear
    ("--kind", "banner",),                                  # unknown kind
])
def test_issue_56_library_update_refuses_a_bad_command_line(monkeypatch, args):
    fake = FakeHttp().install(monkeypatch)

    res = run("applications", "library", "update", "4", *args)

    assert res.exit_code != 0
    assert fake.calls == []


def test_issue_56_library_delete_deletes_one_text(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("DELETE", f"{LIB}/4", status=204)

    res = run("applications", "library", "delete", "4")

    assert res.exit_code == 0, res.output
    assert len(fake.calls_to("DELETE", f"{LIB}/4")) == 1


def test_issue_56_an_unknown_library_text_gets_a_hint(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{LIB}/99", _refusal("APPLICATION_LIBRARY_TEXT_NOT_FOUND"), status=404)

    res = run("applications", "library", "get", "99")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_LIBRARY_TEXT_NOT_FOUND: ")
    assert "applications library list" in hint


def test_issue_56_library_help_lists_its_commands_and_options():
    group = CliRunner().invoke(main, ["applications", "library", "--help"]).output
    create = " ".join(CliRunner().invoke(main, ["applications", "library", "create", "--help"]).output.split())
    update = " ".join(CliRunner().invoke(main, ["applications", "library", "update", "--help"]).output.split())

    for command in ("list", "get", "create", "update", "delete"):
        assert f"  {command} " in group, command
    for word in ("--kind", "--name", "--text", "--heading", "header", "selection", "declaration", "rule", "note"):
        assert word in create, word
    for word in ("--kind", "--name", "--text", "--heading", "--clear-heading"):
        assert word in update, word


# ── Help ────────────────────────────────────────────────────────────────


def test_issue_38_group_help_lists_every_command():
    res = CliRunner().invoke(main, ["applications", "--help"])

    assert res.exit_code == 0
    for command in (
        "list", "show", "create", "update", "delete", "duplicate", "save", "publish",
        "unpublish", "revert", "preview", "blank-form", "labels", "clauses", "library", "form",
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


# ── Library texts with keys, editable labels, the club's languages (club_server#133, cli #60) ──

LABELS = f"{A}/labels"


def test_issue_60_labels_set_patches_one_label_in_each_language(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{LABELS}/field.full_name", {"key": "field.full_name", "text": {"en": "Name", "mr": "नाव"}})

    res = run("applications", "labels", "set", "field.full_name", "--text", "en", "Name", "--text", "mr", "नाव")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{LABELS}/field.full_name")[0].json == {"text": {"en": "Name", "mr": "नाव"}}
    assert json.loads(res.stdout)["key"] == "field.full_name"


@pytest.mark.parametrize("args", [
    ("field.full_name",),                                                   # no text
    ("--text", "en", "Name"),                                               # no key
    ("field.full_name", "--text", "en", "Name", "--text", "en", "Again"),  # a language twice
])
def test_issue_60_labels_set_refuses_a_bad_command_line(monkeypatch, args):
    fake = FakeHttp().install(monkeypatch)

    res = run("applications", "labels", "set", *args)

    assert res.exit_code != 0
    assert fake.calls == []


def test_issue_60_labels_alone_still_reads_the_labels(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", LABELS, {"languages": ["en", "mr"], "labels": {}})

    res = run("applications", "labels")

    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["languages"] == ["en", "mr"]
    assert fake.calls_to("PATCH", LABELS) == []


def test_issue_60_labels_help_names_set_and_its_refusals():
    group = " ".join(CliRunner().invoke(main, ["applications", "labels", "--help"]).output.split())
    set_help = " ".join(CliRunner().invoke(main, ["applications", "labels", "set", "--help"]).output.split())

    assert "set" in group and "club's languages" in group
    for word in ("--text", "English", "APPLICATION_LABEL_NOT_FOUND", "APPLICATION_LANGUAGE_NOT_OFFERED"):
        assert word in set_help, word


def test_issue_60_an_unknown_label_gets_a_hint(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{LABELS}/field.nope", _refusal("APPLICATION_LABEL_NOT_FOUND"), status=404)

    res = run("applications", "labels", "set", "field.nope", "--text", "en", "x")

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_LABEL_NOT_FOUND: ")
    assert "applications labels" in hint


def test_issue_60_a_language_the_club_lacks_gets_a_hint_naming_it(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", A, _refusal("APPLICATION_LANGUAGE_NOT_OFFERED", languages=["hi"]), status=422)

    res = run("applications", "create", "--json", '{"languages": ["en", "hi"]}')

    assert res.exit_code != 0
    hint = res.stderr.strip().splitlines()[-1]
    assert hint.startswith("APPLICATION_LANGUAGE_NOT_OFFERED: ")
    assert "profile-options" in hint
    assert "Languages: hi." in hint


def test_issue_60_incomplete_hint_points_a_label_at_labels_set(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on(
        "POST", f"{A}/7/save",
        _refusal("APPLICATION_INCOMPLETE", missing=[{"field": "labels.field.full_name", "languages": ["mr"]}]),
        status=422,
    )

    res = run("applications", "save", "7")

    hint = res.stderr.strip().splitlines()[-1]
    assert "labels.field.full_name (mr)" in hint
    assert "applications labels set" in hint


def test_issue_60_clauses_and_library_help_describe_one_list_of_library_texts():
    clauses = " ".join(CliRunner().invoke(main, ["applications", "clauses", "--help"]).output.split())
    library = " ".join(CliRunner().invoke(main, ["applications", "library", "--help"]).output.split())
    create = " ".join(CliRunner().invoke(main, ["applications", "library", "create", "--help"]).output.split())

    for word in ("key", "applications library", "club's languages"):
        assert word in clauses, word
    for gone in ("source", "builtin", "gaps"):
        assert gone not in clauses, gone
    assert "built-in" in library and "edited and deleted" in library
    assert "key" in create and "club's languages" in create
    assert "(en, mr, hi)" not in create
