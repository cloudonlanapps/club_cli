"""Applications against a live server (club_server#93, cli #38).

cli_test.conf turns applications on (with identity verification, which they
need); cli_test_modules_off.conf turns both off, and every route answers 503.
A template's texts are language maps, built here from `fill-clause` output
(club_server#107, cli #45).
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


CAMP = {"type": "date_range", "from": day(2026, 11, 16), "to": day(2026, 11, 17)}
MATCHES = {"type": "date_range", "from": day(2026, 11, 19), "to": day(2026, 11, 21)}


def fill(live: Live, clause: str, facts: dict | None = None) -> dict:
    """One library clause filled by the server: {clause, heading, text}."""
    args = ["applications", "fill-clause", clause]
    if facts:
        args += ["--facts", json.dumps(facts)]
    return live.ok(*args)


def template(live: Live, title: str) -> dict:
    """A complete English + Marathi template, its texts filled from library clauses."""
    rule = fill(live, "rule.identity_proof")
    return {
        "languages": ["en", "mr"],
        "facts": {"camp": CAMP, "matches": MATCHES},
        "organisation": {"en": "EXAMPLE CLUB", "mr": "उदाहरण क्लब"},
        "title": {"en": title, "mr": "नोंदणी अर्ज"},
        "subtitle": {"en": "16–17 November 2026", "mr": "१६–१७ नोव्हेंबर २०२६"},
        "header": [
            {"en": "City Rink", "mr": "सिटी रिंक"},
            [
                fill(live, "header.training_camp", {"dates": CAMP})["text"],
                fill(live, "header.matches", {"dates": MATCHES})["text"],
            ],
        ],
        "selections": [fill(live, "selection.attend_training_camp", {"dates": CAMP})["text"]],
        "declaration": fill(live, "declaration.trial")["text"],
        "formFooter": {"en": "Play fair, play hard.", "mr": "खिलाडूवृत्तीने खेळा."},
        "terms": [
            {"heading": rule["heading"], "body": rule["text"]},
            {"heading": {"en": "Kit", "mr": "साहित्य"}, "body": {"en": "Bring a helmet.", "mr": "हेल्मेट आणा."}},
        ],
        "termsNotes": [{"text": fill(live, "note.follow_rules")["text"]}],
    }


def _create(live: Live, body: dict) -> dict:
    created = live.ok("applications", "create", "--json", json.dumps(body, ensure_ascii=False))
    assert created["status"] == "draft", created
    return created


def _published(live: Live, body: dict | None = None) -> int:
    app_id = _create(live, body or template(live, live.unique("Trial")))["id"]
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
    ("fill-clause", "declaration.trial"),
])
def test_issue_38_every_command_answers_503_while_off(live: Live, args):
    res = live.refused("applications", *args)

    assert "APPLICATIONS_DISABLED" in res.output


@pytest.mark.modules_on
def test_issue_38_a_template_goes_through_its_whole_lifecycle(live: Live, tmp_path):
    title = live.unique("Trial")
    created = _create(live, template(live, title))
    app_id = str(created["id"])
    assert created["layout"] is None

    updated = live.ok("applications", "update", app_id, "--json", '{"subtitle": null}')
    assert "subtitle" not in updated or updated["subtitle"] is None
    path = tmp_path / "t.json"
    body = template(live, title)
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    assert live.ok("applications", "update", app_id, "--file", str(path))["subtitle"] == body["subtitle"]

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
    app_id = str(_create(live, template(live, live.unique("Trial")))["id"])

    res = live.refused("applications", "publish", app_id)

    assert "INVALID_TRANSITION" in res.output
    assert live.ok("applications", "show", app_id)["status"] == "draft"


@pytest.mark.modules_on
def test_issue_38_save_names_the_texts_missing_a_language(live: Live):
    body = template(live, live.unique("Trial"))
    body["formFooter"] = {"en": "Play fair."}
    app_id = str(_create(live, body)["id"])

    res = live.refused("applications", "save", app_id)

    assert "APPLICATION_INCOMPLETE" in res.output
    assert "Missing: formFooter (mr)." in res.stderr  # the details' shape since club_server#101 (cli #50)
    assert live.ok("applications", "show", app_id)["status"] == "draft"


@pytest.mark.modules_on
def test_issue_45_fill_clause_gives_the_text_in_every_library_language(live: Live):
    languages = live.ok("applications", "clauses")["languages"]

    filled = fill(live, "rule.fee", {"amount": {"type": "amount", "value": 1500}})

    assert filled["clause"] == "rule.fee"
    assert set(filled["text"]) == set(languages) and {"en", "mr", "hi"} <= set(languages)
    assert all(filled["text"].values())
    assert set(filled["heading"]) == set(languages)
    assert "1500" in filled["text"]["en"].replace(",", "")
    assert fill(live, "declaration.trial")["heading"] is None


@pytest.mark.modules_on
def test_issue_45_fill_clause_refuses_an_unknown_clause_or_a_missing_fact(live: Live):
    unknown = live.refused("applications", "fill-clause", "rule.no_such_clause")
    missing = live.refused("applications", "fill-clause", "rule.fee")

    for res in (unknown, missing):
        assert "APPLICATION_INVALID_REFERENCE" in res.output
        assert "applications clauses" in res.stderr


@pytest.mark.modules_on
def test_issue_45_a_clause_reference_in_a_template_is_refused(live: Live):
    body = template(live, live.unique("Trial"))
    body["declaration"] = {"clause": "declaration.trial"}

    res = live.refused("applications", "create", "--json", json.dumps(body, ensure_ascii=False))

    assert "declaration" in res.output


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
    app_id = str(_create(live, template(live, live.unique("Trial")))["id"])

    res = live.refused("applications", "form", app_id, "--user", member, "-o", str(tmp_path / "x.pdf"))

    assert "APPLICATION_NOT_PUBLISHED" in res.output
    assert not (tmp_path / "x.pdf").exists()


@pytest.mark.modules_on
def test_issue_38_a_member_outside_every_age_category_is_refused(live: Live, tmp_path):
    body = template(live, live.unique("Seniors"))
    # live.member_json is born 2012; this category is for those born in 2000 or earlier.
    body["ageCategories"] = [{"label": {"en": "Seniors", "mr": "वरिष्ठ"}, "bornOnOrBeforeUtc": day(2000, 1, 1)}]
    app_id = _published(live, body)
    member = _member_with_document(live)

    res = live.refused("applications", "form", str(app_id), "-o", str(tmp_path / "x.pdf"), user=member, pw=MEMBER_PASSWORD)

    assert "APPLICATION_NOT_ELIGIBLE" in res.output
    assert not (tmp_path / "x.pdf").exists()
