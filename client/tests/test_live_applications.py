"""Applications against a live server (club_server#93, cli #38).

cli_test.conf turns applications on (with identity verification, which they
need); cli_test_modules_off.conf turns both off, and every route answers 503.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from .conftest import EXPECT_MODULES
from .live import MEMBER_PASSWORD, Live

ID_DOCUMENT = Path(__file__).resolve().parent / "fixtures" / "id_proof.png"


def day(y: int, m: int, d: int) -> int:
    """A calendar date as the API takes it: UTC midnight, ms."""
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def template(title: str) -> dict:
    """A complete English + Marathi template, built from library clauses and facts."""
    return {
        "languages": ["en", "mr"],
        "facts": {
            "venue": {"type": "name", "value": {"en": "City Rink", "mr": "सिटी रिंक"}},
            "camp": {"type": "date_range", "from": day(2026, 11, 16), "to": day(2026, 11, 17)},
            "matches": {"type": "date_range", "from": day(2026, 11, 19), "to": day(2026, 11, 21)},
        },
        "organisation": {"en": "EXAMPLE CLUB", "mr": "उदाहरण क्लब"},
        "title": {"en": title, "mr": "नोंदणी अर्ज"},
        "subtitle": {"fact": "camp"},
        "header": [
            {"fact": "venue"},
            [
                {"clause": "header.training_camp", "args": {"dates": "camp"}},
                {"clause": "header.matches", "args": {"dates": "matches"}},
            ],
        ],
        "selections": [{"clause": "selection.attend_training_camp", "args": {"dates": "camp"}}],
        "declaration": {"clause": "declaration.trial"},
        "formFooter": {"en": "Play fair, play hard.", "mr": "खिलाडूवृत्तीने खेळा."},
        "terms": [
            {"clause": "rule.identity_proof"},
            {"heading": {"en": "Kit", "mr": "साहित्य"}, "body": {"en": "Bring a helmet.", "mr": "हेल्मेट आणा."}},
        ],
        "termsNotes": [{"clause": "note.follow_rules"}],
    }


def _create(live: Live, body: dict) -> dict:
    created = live.ok("applications", "create", "--json", json.dumps(body, ensure_ascii=False))
    assert created["status"] == "draft", created
    return created


def _published(live: Live, body: dict | None = None) -> int:
    app_id = _create(live, body or template(live.unique("Trial")))["id"]
    live.ok("applications", "save", str(app_id))
    assert live.ok("applications", "publish", str(app_id))["status"] == "published"
    return app_id


def _member_with_document(live: Live) -> str:
    member = live.active_member()
    live.ok(
        "me", "gallery", "add-file", str(ID_DOCUMENT), "--tag", "identity_document",
        user=member, pw=MEMBER_PASSWORD,
    )
    return member


def _ids(page: dict) -> set[int]:
    return {a["id"] for a in page["items"]}


def test_issue_38_capabilities_report_applications(live: Live):
    assert "applications" in live.caps, live.caps
    if EXPECT_MODULES is None:
        pytest.skip("CLUB_EXPECT_MODULES not set; nothing to compare against")
    assert live.caps["applications"] is (EXPECT_MODULES == "on")


@pytest.mark.modules_off
@pytest.mark.parametrize("args", [
    ("list",), ("show", "1"), ("create", "--json", "{}"), ("save", "1"), ("labels",), ("clauses",),
])
def test_issue_38_every_command_answers_503_while_off(live: Live, args):
    res = live.refused("applications", *args)

    assert "APPLICATIONS_DISABLED" in res.output


@pytest.mark.modules_on
def test_issue_38_a_template_goes_through_its_whole_lifecycle(live: Live, tmp_path):
    title = live.unique("Trial")
    created = _create(live, template(title))
    app_id = str(created["id"])
    assert created["layout"] is None

    updated = live.ok("applications", "update", app_id, "--json", '{"subtitle": null}')
    assert "subtitle" not in updated or updated["subtitle"] is None
    path = tmp_path / "t.json"
    path.write_text(json.dumps(template(title), ensure_ascii=False), encoding="utf-8")
    assert live.ok("applications", "update", app_id, "--file", str(path))["subtitle"] == {"fact": "camp"}

    preview = tmp_path / "preview.pdf"
    out = live.ok("applications", "preview", app_id, "-o", str(preview))
    assert str(preview) in out
    assert preview.read_bytes().startswith(b"%PDF")

    saved = live.ok("applications", "save", app_id)
    assert saved["status"] == "saved"
    assert saved["layout"]["name"]
    res = live.refused("applications", "update", app_id, "--json", '{"subtitle": null}')
    assert "INVALID_STATE" in res.output

    assert live.ok("applications", "publish", app_id)["status"] == "published"
    assert created["id"] in _ids(live.ok("applications", "list", "--status", "published", "--limit", "100"))
    assert created["id"] not in _ids(live.ok("applications", "list", "--status", "draft", "--limit", "100"))
    res = live.refused("applications", "delete", app_id)
    assert "INVALID_STATE" in res.output

    assert live.ok("applications", "unpublish", app_id)["status"] == "saved"
    reverted = live.ok("applications", "revert", app_id)
    assert reverted["status"] == "draft" and reverted["layout"] is None

    copy = live.ok("applications", "duplicate", app_id)
    assert copy["status"] == "draft" and copy["id"] != created["id"]
    assert copy["title"] == created["title"]

    live.ok("applications", "delete", app_id)
    res = live.refused("applications", "show", app_id)
    assert "APPLICATION_NOT_FOUND" in res.output
    assert live.ok("applications", "show", str(copy["id"]))["status"] == "draft"


@pytest.mark.modules_on
def test_issue_38_a_move_from_the_wrong_state_is_refused(live: Live):
    app_id = str(_create(live, template(live.unique("Trial")))["id"])

    res = live.refused("applications", "publish", app_id)

    assert "INVALID_TRANSITION" in res.output
    assert live.ok("applications", "show", app_id)["status"] == "draft"


@pytest.mark.modules_on
def test_issue_38_save_names_the_texts_missing_a_language(live: Live):
    body = template(live.unique("Trial"))
    body["formFooter"] = {"en": "Play fair."}
    app_id = str(_create(live, body)["id"])

    res = live.refused("applications", "save", app_id)

    assert "APPLICATION_INCOMPLETE" in res.output
    assert "Missing in 'mr': formFooter" in res.stderr
    assert live.ok("applications", "show", app_id)["status"] == "draft"


@pytest.mark.modules_on
def test_issue_38_save_refuses_an_unknown_clause(live: Live):
    body = template(live.unique("Trial"))
    body["declaration"] = {"clause": "declaration.no_such_clause"}
    app_id = str(_create(live, body)["id"])

    res = live.refused("applications", "save", app_id)

    assert "APPLICATION_INVALID_REFERENCE" in res.output
    assert "applications clauses" in res.stderr


@pytest.mark.modules_on
def test_issue_38_admin_reads_the_labels_and_the_clauses(live: Live):
    labels = live.ok("applications", "labels")
    clauses = live.ok("applications", "clauses")

    assert "en" in labels["languages"] and labels["labels"]
    assert "declaration.trial" in clauses["clauses"]


@pytest.mark.modules_on
def test_issue_38_a_member_downloads_their_own_form(live: Live, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_id = _published(live)
    member = live.active_member()

    # No identity document yet: nothing is offered.
    assert app_id not in _ids(live.ok("applications", "list", "--limit", "100", user=member, pw=MEMBER_PASSWORD))
    res = live.refused("applications", "form", str(app_id), user=member, pw=MEMBER_PASSWORD)
    assert "IDENTITY_DOCUMENT_REQUIRED" in res.output

    live.ok(
        "me", "gallery", "add-file", str(ID_DOCUMENT), "--tag", "identity_document",
        user=member, pw=MEMBER_PASSWORD,
    )
    listed = live.ok("applications", "list", "--limit", "100", user=member, pw=MEMBER_PASSWORD)
    assert app_id in _ids(listed)
    assert {a["status"] for a in listed["items"]} == {"published"}

    out = live.ok("applications", "form", str(app_id), user=member, pw=MEMBER_PASSWORD)
    written = tmp_path / f"application-{app_id}-{member}.pdf"
    assert written.name in out
    assert written.read_bytes().startswith(b"%PDF")


@pytest.mark.modules_on
def test_issue_38_a_member_cannot_write_or_preview(live: Live, tmp_path):
    member = _member_with_document(live)
    app_id = _published(live)

    for args in (
        ("create", "--json", "{}"),
        ("unpublish", str(app_id)),
        ("preview", str(app_id), "-o", str(tmp_path / "p.pdf")),
        ("form", str(app_id), "--user", live.admin, "-o", str(tmp_path / "f.pdf")),
    ):
        res = live.refused("applications", *args, user=member, pw=MEMBER_PASSWORD)
        assert "INSUFFICIENT_PERMISSION" in res.output, (args, res.output)
    assert not list(tmp_path.iterdir())


@pytest.mark.modules_on
def test_issue_38_admin_downloads_a_members_form(live: Live, tmp_path):
    member = _member_with_document(live)
    app_id = _published(live)
    out_file = tmp_path / "member.pdf"

    out = live.ok("applications", "form", str(app_id), "--user", member, "-o", str(out_file))

    assert str(out_file) in out
    assert out_file.read_bytes().startswith(b"%PDF")
    res = live.refused("applications", "form", str(app_id), "--user", live.unique("nobody"), "-o", str(tmp_path / "x.pdf"))
    assert "USER_NOT_FOUND" in res.output


@pytest.mark.modules_on
def test_issue_38_a_form_of_an_unpublished_template_is_refused(live: Live, tmp_path):
    member = _member_with_document(live)
    app_id = str(_create(live, template(live.unique("Trial")))["id"])

    res = live.refused("applications", "form", app_id, "--user", member, "-o", str(tmp_path / "x.pdf"))

    assert "APPLICATION_NOT_PUBLISHED" in res.output
    assert not (tmp_path / "x.pdf").exists()


@pytest.mark.modules_on
def test_issue_38_a_member_outside_every_age_category_is_refused(live: Live, tmp_path):
    body = template(live.unique("Seniors"))
    # live.member_json is born 2012; this category is for those born in 2000 or earlier.
    body["ageCategories"] = [{"label": {"en": "Seniors", "mr": "वरिष्ठ"}, "bornOnOrBeforeUtc": day(2000, 1, 1)}]
    app_id = _published(live, body)
    member = _member_with_document(live)

    res = live.refused("applications", "form", str(app_id), "-o", str(tmp_path / "x.pdf"), user=member, pw=MEMBER_PASSWORD)

    assert "APPLICATION_NOT_ELIGIBLE" in res.output
    assert not (tmp_path / "x.pdf").exists()
