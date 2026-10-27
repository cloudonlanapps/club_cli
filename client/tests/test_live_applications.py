"""Applications against a live server (club_server#93, cli #38).

cli_test.conf turns applications on (with identity verification, which they
need); cli_test_modules_off.conf turns both off, and every route answers 503.
A template's texts are language maps, built here from the clause list's texts
as written, `{fact}` gaps included, which the server fills at print
(club_server#126, cli #56).
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


def clause(live: Live, key: str, **renamed: str) -> dict:
    """One seeded built-in clause, by its key, from `applications clauses`, its gaps renamed ({old: new}) to the template's facts."""
    (found,) = [c for c in live.ok("applications", "clauses")["clauses"] if c["key"] == key]

    def rename(text: dict | None) -> dict | None:
        if text is None:
            return None
        out = {}
        for lang, value in text.items():
            for old, new in renamed.items():
                value = value.replace("{" + old + "}", "{" + new + "}")
            out[lang] = value
        return out

    return {**found, "text": rename(found["text"]), "heading": rename(found["heading"])}


def template(live: Live, title: str) -> dict:
    """A complete English + Marathi template, its texts taken from built-in clauses, gaps and all."""
    rule = clause(live, "rule.identity_proof")
    return {
        "languages": ["en", "mr"],
        "facts": {"camp": CAMP, "matches": MATCHES},
        "organisation": {"en": "EXAMPLE CLUB", "mr": "उदाहरण क्लब"},
        "title": {"en": title, "mr": "नोंदणी अर्ज"},
        "subtitle": {"en": "16–17 November 2026", "mr": "१६–१७ नोव्हेंबर २०२६"},
        "header": [
            {"en": "City Rink", "mr": "सिटी रिंक"},
            [
                clause(live, "header.training_camp", dates="camp")["text"],
                clause(live, "header.matches", dates="matches")["text"],
            ],
        ],
        "selections": [clause(live, "selection.attend_training_camp", dates="camp")["text"]],
        "declaration": clause(live, "declaration.trial")["text"],
        "formFooter": {"en": "Play fair, play hard.", "mr": "खिलाडूवृत्तीने खेळा."},
        "terms": [
            {"heading": rule["heading"], "body": rule["text"]},
            {"heading": {"en": "Kit", "mr": "साहित्य"}, "body": {"en": "Bring a helmet.", "mr": "हेल्मेट आणा."}},
        ],
        "termsNotes": [{"text": clause(live, "note.follow_rules")["text"]}],
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
    ("library", "list"), ("library", "get", "1"), ("library", "delete", "1"),
    ("library", "create", "--kind", "note", "--name", "n", "--text", "en", "x"),
    ("library", "update", "1", "--name", "n"),
    ("labels", "set", "field.full_name", "--text", "en", "Name"),
    ("blank-form", "1", "-o", "blank.pdf"),
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
def test_issue_56_save_names_each_gap_that_names_no_fact(live: Live):
    body = template(live, live.unique("Trial"))
    body["selections"] = [clause(live, "selection.attend_training_camp")["text"]]  # {dates}: no such fact
    app_id = str(_create(live, body)["id"])

    res = live.refused("applications", "save", app_id)

    assert "APPLICATION_UNKNOWN_FACT" in res.output
    assert "Gaps: selections[0] (en): {dates}; selections[0] (mr): {dates}" in res.stderr
    assert live.ok("applications", "show", app_id)["status"] == "draft"


@pytest.mark.modules_on
def test_issue_56_a_club_text_goes_through_its_whole_lifecycle(live: Live):
    name = live.unique("Fee")
    created = live.ok(
        "applications", "library", "create", "--kind", "rule", "--name", name,
        "--text", "en", "Pay ₹{amount}.", "--text", "mr", "₹{amount} भरा.", "--heading", "en", "Fee",
    )
    text_id = str(created["id"])
    assert created["kind"] == "rule" and created["name"] == name
    assert created["text"] == {"en": "Pay ₹{amount}.", "mr": "₹{amount} भरा."}
    assert created["heading"] == {"en": "Fee"} and created["createdBy"] == live.admin

    assert live.ok("applications", "library", "get", text_id) == created
    assert created["id"] in {t["id"] for t in live.ok("applications", "library", "list")}
    (listed,) = [c for c in live.ok("applications", "clauses")["clauses"] if c["id"] == created["id"]]
    assert listed == created and listed["key"] is None

    renamed = live.ok("applications", "library", "update", text_id, "--name", name + "_2", "--text", "en", "Pay {amount}.")
    assert renamed["name"] == name + "_2" and renamed["text"] == {"en": "Pay {amount}."}
    assert renamed["heading"] == {"en": "Fee"}

    res = live.refused("applications", "library", "update", text_id, "--kind", "note")
    assert "only a rule has a heading" in res.output
    note = live.ok("applications", "library", "update", text_id, "--kind", "note", "--clear-heading")
    assert note["kind"] == "note" and note["heading"] is None

    live.ok("applications", "library", "delete", text_id)
    res = live.refused("applications", "library", "get", text_id)
    assert "APPLICATION_LIBRARY_TEXT_NOT_FOUND" in res.output
    assert "applications library list" in res.stderr


@pytest.mark.modules_on
def test_issue_56_the_library_refuses_a_heading_off_a_rule_and_an_unknown_language(live: Live):
    heading = live.refused(
        "applications", "library", "create", "--kind", "note", "--name", live.unique("n"),
        "--text", "en", "x", "--heading", "en", "H",
    )
    language = live.refused(
        "applications", "library", "create", "--kind", "note", "--name", live.unique("n"), "--text", "fr", "x",
    )

    assert "only a rule has a heading" in heading.output
    # fr is a language code, but not one of the club's (club_server#133, cli #60).
    assert "APPLICATION_LANGUAGE_NOT_OFFERED" in language.output and '"fr"' in language.output
    assert "profile-options" in language.stderr


@pytest.mark.modules_on
def test_issue_56_only_an_admin_reads_or_writes_the_library(live: Live):
    member = live.active_member()

    for args in (("list",), ("create", "--kind", "note", "--name", "n", "--text", "en", "x")):
        res = live.refused("applications", "library", *args, user=member, pw=MEMBER_PASSWORD)
        assert "INSUFFICIENT_PERMISSION" in res.output, (args, res.output)


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
    assert clauses["languages"] == labels["languages"]
    # The built-in clauses are seeded library texts, each with its key (club_server#133, cli #60).
    builtin = [c for c in clauses["clauses"] if c["key"] is not None]
    assert "declaration.trial" in {c["key"] for c in builtin}
    assert all(isinstance(c["id"], int) and c["name"] for c in builtin)
    (fee,) = [c for c in builtin if c["key"] == "rule.fee"]
    assert "{amount}" in fee["text"]["en"] and "source" not in fee and "gaps" not in fee
    assert fee == live.ok("applications", "library", "get", str(fee["id"]))


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
def test_issue_58_admin_writes_the_blank_form_of_a_saved_and_a_published_template(
    live: Live, tmp_path, monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    saved = _create(live, template(live, live.unique("Trial")))["id"]
    live.ok("applications", "save", str(saved))
    published = _published(live)

    out = live.ok("applications", "blank-form", str(saved))
    written = tmp_path / f"application-{saved}-blank.pdf"
    assert written.name in out
    assert written.read_bytes().startswith(b"%PDF")

    out_file = tmp_path / "published.pdf"
    out = live.ok("applications", "blank-form", str(published), "-o", str(out_file))
    assert str(out_file) in out
    assert out_file.read_bytes().startswith(b"%PDF")


@pytest.mark.modules_on
def test_issue_58_the_blank_form_of_a_draft_is_refused_with_the_hint(live: Live, tmp_path):
    draft = str(_create(live, template(live, live.unique("Trial")))["id"])

    res = live.refused("applications", "blank-form", draft, "-o", str(tmp_path / "x.pdf"))

    assert res.stderr.strip().splitlines()[-1].startswith("APPLICATION_NOT_SAVED:"), res.output
    assert not (tmp_path / "x.pdf").exists()
    res = live.refused("applications", "blank-form", "999999", "-o", str(tmp_path / "x.pdf"))
    assert "APPLICATION_NOT_FOUND" in res.output


@pytest.mark.modules_on
def test_issue_58_a_member_cannot_download_the_blank_form(live: Live, tmp_path):
    member = live.active_member()
    app_id = _published(live)

    res = live.refused(
        "applications", "blank-form", str(app_id), "-o", str(tmp_path / "b.pdf"),
        user=member, pw=MEMBER_PASSWORD,
    )

    assert "INSUFFICIENT_PERMISSION" in res.output
    assert not list(tmp_path.iterdir())


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


@pytest.mark.modules_on
def test_issue_46_a_member_fitting_two_categories_chooses_one(live: Live, tmp_path):
    body = template(live, live.unique("Overlap"))
    # live.member_json is born 2012: it fits the first two, not the third.
    body["ageCategories"] = [
        {"label": {"en": "Under 17", "mr": "१७ वर्षांखालील"}, "bornOnOrAfterUtc": day(2010, 1, 1)},
        {"label": {"en": "Juniors", "mr": "कनिष्ठ"}, "bornOnOrBeforeUtc": day(2014, 12, 31)},
        {"label": {"en": "Seniors", "mr": "वरिष्ठ"}, "bornOnOrBeforeUtc": day(2000, 1, 1)},
    ]
    app_id = str(_published(live, body))
    member = _member_with_document(live)
    as_member = {"user": member, "pw": MEMBER_PASSWORD}

    assert live.ok("applications", "show", app_id, **as_member)["fittingCategories"] == [0, 1]
    assert live.ok("applications", "show", app_id)["fittingCategories"] is None

    required = live.refused("applications", "form", app_id, "-o", str(tmp_path / "x.pdf"), **as_member)
    assert "APPLICATION_CATEGORY_REQUIRED" in required.output
    assert "Fits categories: 0, 1." in required.stderr
    not_fitting = live.refused("applications", "form", app_id, "--category", "2", "-o", str(tmp_path / "x.pdf"), **as_member)
    assert "APPLICATION_CATEGORY_NOT_FITTING" in not_fitting.output
    assert not (tmp_path / "x.pdf").exists()

    own = tmp_path / "own.pdf"
    live.ok("applications", "form", app_id, "--category", "1", "-o", str(own), **as_member)
    assert own.read_bytes().startswith(b"%PDF")
    theirs = tmp_path / "theirs.pdf"
    live.ok("applications", "form", app_id, "--user", member, "--category", "0", "-o", str(theirs))
    assert theirs.read_bytes().startswith(b"%PDF")


# ── Editable labels and the club's languages (club_server#133, cli #60) ──


@pytest.mark.modules_on
def test_issue_60_an_admin_edits_a_label_and_the_edit_is_read_back(live: Live):
    before = live.ok("applications", "labels")["labels"]["field.full_name"]
    try:
        edited = live.ok(
            "applications", "labels", "set", "field.full_name", "--text", "en", "Full name of the player",
            "--text", "mr", "खेळाडूचे पूर्ण नाव",
        )
        assert edited == {"key": "field.full_name", "text": {"en": "Full name of the player", "mr": "खेळाडूचे पूर्ण नाव"}}
        assert live.ok("applications", "labels")["labels"]["field.full_name"] == edited["text"]
    finally:
        args = [a for lang, text in before.items() for a in ("--text", lang, text)]
        live.ok("applications", "labels", "set", "field.full_name", *args)


@pytest.mark.modules_on
def test_issue_60_label_edits_are_refused_with_a_hint(live: Live):
    unknown = live.refused("applications", "labels", "set", "field.nope", "--text", "en", "x")
    language = live.refused("applications", "labels", "set", "field.full_name", "--text", "en", "x", "--text", "gu", "y")
    member = live.active_member()
    by_member = live.refused(
        "applications", "labels", "set", "field.full_name", "--text", "en", "x", user=member, pw=MEMBER_PASSWORD,
    )

    assert "APPLICATION_LABEL_NOT_FOUND" in unknown.output and "applications labels" in unknown.stderr
    assert "APPLICATION_LANGUAGE_NOT_OFFERED" in language.output and "Languages: gu." in language.stderr
    assert "INSUFFICIENT_PERMISSION" in by_member.output


@pytest.mark.modules_on
def test_issue_60_a_template_printing_in_a_language_the_club_lacks_is_refused(live: Live):
    body = {"languages": ["en", "gu"], "title": {"en": live.unique("Trial")}}

    res = live.refused("applications", "create", "--json", json.dumps(body))

    assert "APPLICATION_LANGUAGE_NOT_OFFERED" in res.output
    assert "Languages: gu." in res.stderr
