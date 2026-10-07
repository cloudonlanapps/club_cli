"""Evaluations as items and answers (club_cli_old#59, club_server#535).

A template is a name, a layout and items; an evaluation is front matter plus
answers, written one at a time. These tests pin which endpoint each command
calls and with what body.
"""
from __future__ import annotations

import json

from club_client.cli import local_iso_to_utc_ms

from .fakes import FakeHttp, run

T = "/v1/evaluations/templates"
E = "/v1/evaluations"

RATING = {"type": "rating", "question": "Edges", "isRequired": True}


def refusal(code: str, message: str, **details) -> dict:
    detail: dict = {"code": code, "message": message}
    if details:
        detail["details"] = details
    return {"detail": detail}


# ── Templates ───────────────────────────────────────────────────────────


def test_issue_59_template_create_sends_name_and_layout(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", T, {"id": 1}, status=201)
    body = {"name": "Skating", "layout": [RATING, {"section": "Notes", "items": [{"type": "qa", "question": "Why"}]}]}

    res = run("evaluations", "templates", "create", json.dumps(body))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("POST", T)[0].json == body


def test_issue_59_template_update_sends_name_and_layout(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{T}/by_id/1", {"id": 1})

    res = run("evaluations", "templates", "update", "1", '{"name": "v2", "layout": [3, {"section": "S", "items": [4]}]}')

    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == {"name": "v2", "layout": [3, {"section": "S", "items": [4]}]}


def test_issue_59_template_apply_is_gone():
    res = run("evaluations", "templates", "apply", "1", "{}")

    assert res.exit_code != 0
    assert "No such command 'apply'" in res.output


def test_issue_59_items_add_wraps_the_item_and_names_the_section(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{T}/by_id/1/items", {"id": 1}, status=201)

    assert run("evaluations", "templates", "items", "add", "1", json.dumps(RATING)).exit_code == 0
    assert run("evaluations", "templates", "items", "add", "1", json.dumps(RATING), "--section", "Skating").exit_code == 0

    first, second = fake.calls_to("POST", f"{T}/by_id/1/items")
    assert first.json == {"item": RATING}
    assert second.json == {"item": RATING, "section": "Skating"}


def test_issue_59_items_replace_puts_the_whole_variant(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PUT", f"{T}/by_id/1/items/7", {"id": 1})

    res = run("evaluations", "templates", "items", "replace", "1", "7", json.dumps(RATING))

    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == RATING


def test_issue_59_items_remove_deletes_the_item(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("DELETE", f"{T}/by_id/1/items/7", {"id": 1})

    assert run("evaluations", "templates", "items", "remove", "1", "7").exit_code == 0
    assert fake.calls_to("DELETE", f"{T}/by_id/1/items/7")


def test_issue_59_items_search_passes_its_filters(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{T}/items", {"items": []})

    assert run("evaluations", "templates", "items", "search").exit_code == 0
    assert run("evaluations", "templates", "items", "search", "--search", "edge", "--type", "rating").exit_code == 0
    assert run("evaluations", "templates", "items", "search", "--type", "essay").exit_code != 0

    bare, filtered = fake.calls_to("GET", f"{T}/items")
    assert bare.params == {"offset": 0, "limit": 50}
    assert filtered.params == {"offset": 0, "limit": 50, "search": "edge", "type": "rating"}


# ── Evaluations ─────────────────────────────────────────────────────────


def test_issue_59_create_sends_template_member_and_period(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", E, {"id": 5}, status=201)
    body = {
        "templateId": 1, "createdFor": "u1", "eventId": 9,
        "periodStart": "2026-09-01T00:00:00", "periodEnd": "2026-09-30T00:00:00",
    }

    res = run("evaluations", "create", json.dumps(body))

    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == {
        "templateId": 1, "createdFor": "u1", "eventId": 9,
        "periodStartUtc": local_iso_to_utc_ms("2026-09-01T00:00:00"),
        "periodEndUtc": local_iso_to_utc_ms("2026-09-30T00:00:00"),
    }


def test_issue_59_update_sends_the_period_only(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{E}/by_id/5", {"id": 5})

    res = run("evaluations", "update", "5", '{"periodStartUtc": null, "periodEndUtc": null}')
    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == {"periodStartUtc": None, "periodEndUtc": None}

    res = run("evaluations", "update", "5", '{"comment": "Good"}')
    assert res.exit_code != 0
    assert "comment" in res.output
    assert len(fake.calls) == 1


def test_issue_59_update_sends_the_event_alone(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{E}/by_id/5", {"id": 5})

    res = run("evaluations", "update", "5", '{"eventId": 9}')

    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == {"eventId": 9}


def test_issue_59_update_with_a_null_event_makes_the_draft_general(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{E}/by_id/5", {"id": 5})

    res = run("evaluations", "update", "5", '{"eventId": null}')

    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == {"eventId": None}


def test_issue_59_update_sends_the_event_with_the_period(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{E}/by_id/5", {"id": 5})
    body = {"eventId": 9, "periodStart": "2026-09-01T00:00:00", "periodEnd": "2026-09-01T00:00:00"}

    res = run("evaluations", "update", "5", json.dumps(body))

    assert res.exit_code == 0, res.output
    day = local_iso_to_utc_ms("2026-09-01T00:00:00")
    assert fake.calls[0].json == {"eventId": 9, "periodStartUtc": day, "periodEndUtc": day}


def test_issue_59_update_refuses_the_member(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    res = run("evaluations", "update", "5", '{"createdFor": "u2", "eventId": 9}')

    assert res.exit_code != 0
    assert "createdFor" in res.output
    assert fake.calls == []


def test_issue_59_list_filters(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", E, {"items": []})

    res = run(
        "evaluations", "list", "--status", "draft", "--created-for", "u1", "--event-id", "9", "--general",
    )
    assert res.exit_code == 0, res.output
    assert run("evaluations", "list", "--no-general").exit_code == 0
    assert run("evaluations", "list").exit_code == 0

    filtered, event_only, bare = fake.calls_to("GET", E)
    assert filtered.params == {
        "offset": 0, "limit": 50, "status": "draft", "createdFor": "u1", "eventId": 9, "general": "true",
    }
    assert event_only.params == {"offset": 0, "limit": 50, "general": "false"}
    assert bare.params == {"offset": 0, "limit": 50}


def test_issue_59_list_drops_the_old_filters():
    for option in ("--subject", "--author", "--scope-type"):
        res = run("evaluations", "list", option, "x")
        assert res.exit_code != 0
        assert "No such option" in res.output


def test_issue_59_transfer_sends_the_owner_and_takes_no_content(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{E}/by_id/5/transfer", status=204)

    res = run("evaluations", "transfer", "5", "coach_b")

    assert res.exit_code == 0, res.output
    assert fake.calls[0].json == {"owner": "coach_b"}
    assert "Success (no content)" in res.output


def test_issue_59_answers_put_and_clear(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    path = f"{E}/by_id/5/answers/7"
    fake.on("PUT", path, {"id": 5})
    fake.on("DELETE", path, {"id": 5})
    answer = {"valueNum": 4, "coachNote": "Strong edges"}

    assert run("evaluations", "answers", "put", "5", "7", json.dumps(answer)).exit_code == 0
    assert run("evaluations", "answers", "clear", "5", "7").exit_code == 0

    assert fake.calls_to("PUT", path)[0].json == answer
    assert fake.calls_to("DELETE", path)


def test_issue_59_pdf_writes_the_preview(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    pdf = b"%PDF-1.7 preview"
    fake.on("GET", f"{E}/by_id/5/pdf", responder=lambda _call: _pdf(pdf))
    out = tmp_path / "copy.pdf"

    res = run("evaluations", "pdf", "5", "-o", str(out))

    assert res.exit_code == 0, res.output
    assert out.read_bytes() == pdf


def test_issue_59_pdf_default_file_is_named_after_the_evaluation(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{E}/by_id/5/pdf", responder=lambda _call: _pdf(b"%PDF"))
    monkeypatch.chdir(tmp_path)

    res = run("evaluations", "pdf", "5")

    assert res.exit_code == 0, res.output
    assert (tmp_path / "evaluation-5.pdf").read_bytes() == b"%PDF"


def test_issue_59_pdf_refusal_is_printed(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    body = refusal("EVALUATION_NOT_FOUND", "Evaluation 5 not found")
    fake.on("GET", f"{E}/by_id/5/pdf", body, status=404)

    res = run("evaluations", "pdf", "5", "-o", str(tmp_path / "x.pdf"))

    assert res.exit_code == 1
    assert json.loads(res.stdout) == body
    assert not (tmp_path / "x.pdf").exists()


def test_issue_59_duplicate_template_name_is_explained(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", T, refusal("TEMPLATE_NAME_TAKEN", "A live template is already named 'Skating'"), status=422)

    res = run("evaluations", "templates", "create", '{"name": "skating ", "layout": []}')

    assert res.exit_code == 1
    assert res.stderr.startswith("TEMPLATE_NAME_TAKEN: ")
    assert "templates list" in res.stderr


def test_issue_59_duplicate_evaluation_is_explained(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{E}/by_id/5/transfer", refusal("DUPLICATE_EVALUATION", "refused"), status=422)

    res = run("evaluations", "transfer", "5", "coach_b")

    assert res.exit_code == 1
    assert res.stderr.startswith("DUPLICATE_EVALUATION: ")
    assert "period" in res.stderr


def test_issue_59_future_period_is_explained(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{E}/by_id/5", refusal("PERIOD_IN_FUTURE", "refused"), status=422)

    res = run("evaluations", "update", "5", '{"periodStartUtc": 1, "periodEndUtc": 9999999999999}')

    assert res.exit_code == 1
    assert res.stderr.startswith("PERIOD_IN_FUTURE: ")


def _pdf(content: bytes):
    from .fakes import FakeResp

    return FakeResp(200, headers={"content-type": "application/pdf"}, content=content)


# ── Error codes ─────────────────────────────────────────────────────────


def test_issue_59_incomplete_names_the_missing_items(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{E}/by_id/5/save", refusal(
        "INCOMPLETE", "Evaluation 5 is missing required answers", itemIds=[7, 9],
    ), status=422)

    res = run("evaluations", "save", "5")

    assert res.exit_code == 1
    assert "INCOMPLETE:" in res.stderr
    assert "7, 9" in res.stderr


def test_issue_59_new_error_codes_are_explained(monkeypatch):
    codes = (
        "INVALID_ANSWER", "INVALID_LAYOUT", "ITEM_TYPE_FIXED", "ITEM_NOT_FOUND",
        "ORIGIN_MISMATCH", "INVALID_EVIDENCE",
        "TEMPLATE_NAME_TAKEN", "DUPLICATE_EVALUATION", "PERIOD_IN_FUTURE",
    )
    for code in codes:
        fake = FakeHttp().install(monkeypatch)
        fake.on("PUT", f"{E}/by_id/5/answers/7", refusal(code, "refused"), status=422)

        res = run("evaluations", "answers", "put", "5", "7", '{"valueNum": 9}')

        assert res.exit_code == 1
        assert res.stderr.startswith(f"{code}: "), (code, res.stderr)


# ── Evidence (club_server#535, R56d) ────────────────────────────────────

PNG = b"\x89PNG\r\n\x1a\nfake"


def test_issue_59_evidence_upload_posts_the_file_for_the_question(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    path = f"{E}/by_id/5/evidence/7"
    view = {"id": 5, "answers": [{"itemId": 7, "evidence": [{"mediaUuid": "u-1", "metadata": "edge.png"}]}]}
    fake.on("POST", path, view, status=201)
    photo = tmp_path / "edge.png"
    photo.write_bytes(PNG)

    res = run("evaluations", "evidence", "upload", "5", "7", str(photo))

    assert res.exit_code == 0, res.output
    (call,) = fake.calls_to("POST", path)
    assert call.files == {"file": ("edge.png", PNG, "image/png")}
    assert call.json is None and not call.data
    assert json.loads(res.stdout) == view


def test_issue_59_evidence_upload_guesses_the_content_type_from_the_name(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    path = f"{E}/by_id/5/evidence/7"
    fake.on("POST", path, {"id": 5}, status=201)
    for name in ("report.pdf", "clip.mp4", "notes.zzunknown"):
        (tmp_path / name).write_bytes(b"x")
        assert run("evaluations", "evidence", "upload", "5", "7", str(tmp_path / name)).exit_code == 0

    sent = [c.files["file"][2] for c in fake.calls_to("POST", path)]
    assert sent == ["application/pdf", "video/mp4", "application/octet-stream"]


def test_issue_59_evidence_upload_of_a_missing_file_calls_nothing(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)

    res = run("evaluations", "evidence", "upload", "5", "7", str(tmp_path / "nope.png"))

    assert res.exit_code == 1
    assert "File not found" in res.output
    assert fake.calls == []


def test_issue_59_evidence_upload_refusals_are_printed(monkeypatch, tmp_path):
    photo = tmp_path / "edge.png"
    photo.write_bytes(PNG)
    cases = (
        (422, "INVALID_EVIDENCE"),
        (422, "INVALID_STATE"),
        (404, "EVALUATION_NOT_FOUND"),
        (413, "FILE_TOO_LARGE"),
    )
    for status, code in cases:
        fake = FakeHttp().install(monkeypatch)
        body = refusal(code, "refused")
        fake.on("POST", f"{E}/by_id/5/evidence/7", body, status=status)

        res = run("evaluations", "evidence", "upload", "5", "7", str(photo))

        assert res.exit_code == 1, code
        assert json.loads(res.stdout) == body


def test_issue_59_evidence_refusal_is_explained(monkeypatch, tmp_path):
    photo = tmp_path / "notes.txt"
    photo.write_bytes(b"text")
    fake = FakeHttp().install(monkeypatch)
    fake.on("POST", f"{E}/by_id/5/evidence/7", refusal("INVALID_EVIDENCE", "refused"), status=422)

    res = run("evaluations", "evidence", "upload", "5", "7", str(photo))

    assert res.exit_code == 1
    assert res.stderr.startswith("INVALID_EVIDENCE: ")
