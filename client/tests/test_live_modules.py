"""The optional modules, on and off, against a live server.

`just test` runs this suite twice: against a stack with every optional module
on and identity verification required (cli_test.conf), and against one with
all of them off (cli_test_modules_off.conf). Each test asserts the behaviour
of whichever mode the stack reports, so both halves are covered by one file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import EXPECT_MODULES
from .live import MEMBER_PASSWORD, Live, days_from_today, local

REPO = Path(__file__).resolve().parents[2]
ID_DOCUMENT = Path(__file__).resolve().parent / "fixtures" / "id_proof.png"
MODULES = ("creditSystem", "evaluations", "eventMarketing", "identityVerification")


def test_capabilities_match_the_stack(live: Live):
    """Guard the two-stack run itself: each half must really be in its mode."""
    assert set(MODULES) <= set(live.caps), live.caps
    if EXPECT_MODULES is None:
        pytest.skip("CLUB_EXPECT_MODULES not set; nothing to compare against")
    expected = EXPECT_MODULES == "on"
    assert {m: live.caps[m] for m in MODULES} == {m: expected for m in MODULES}


def test_capabilities_report_the_stacks_country_code(live: Live):
    """cli_test.conf sets default_country_code; the modules-off conf sets none (club_server#15)."""
    assert "defaultCountryCode" in live.caps, live.caps
    if EXPECT_MODULES is None:
        pytest.skip("CLUB_EXPECT_MODULES not set; nothing to compare against")
    assert live.caps["defaultCountryCode"] == ("91" if EXPECT_MODULES == "on" else None)


# ── Identity verification (club_server#428, cli #34) ────────────────────


def _register(live: Live) -> str:
    username = live.unique("reg")
    live.ok("users", "register", json.dumps(live.member_json(username)), anonymous=True)
    return username


def test_registration_lands_in_the_state_the_stack_verifies(live: Live):
    username = _register(live)
    status = live.ok("user", "get", username)["status"]
    assert status == ("registered" if live.caps["identityVerification"] else "pending")


def test_member_reaches_active_through_the_stacks_review_flow(live: Live):
    username = _register(live)
    if live.caps["identityVerification"]:
        res = live.refused("me", "submit-for-review", user=username, pw=MEMBER_PASSWORD)
        assert "IDENTITY_DOCUMENT_REQUIRED" in res.output
        live.ok(
            "me", "gallery", "add-file", str(ID_DOCUMENT), "--tag", "identity_document",
            user=username, pw=MEMBER_PASSWORD,
        )
        live.ok("me", "submit-for-review", user=username, pw=MEMBER_PASSWORD)
    else:
        # Already pending: there is nothing to submit.
        res = live.refused("me", "submit-for-review", user=username, pw=MEMBER_PASSWORD)
        assert "INVALID_STATE" in res.output
    assert live.ok("user", "get", username)["status"] == "pending"
    live.ok("user", "approve", username)
    assert live.ok("user", "get", username)["status"] == "active"


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed")
def test_member_recipe_seeds_an_active_member_in_either_mode(live: Live, tmp_path: Path):
    """`just member` skips the document and submit where nothing verifies them (cli #34)."""
    username = live.unique("seeded")
    (tmp_path / "identity").mkdir()
    shutil.copy(ID_DOCUMENT, tmp_path / "identity" / "id.png")
    seed = tmp_path / f"{username}.json"
    seed.write_text(json.dumps({**live.member_json(username), "identity-document": ["identity/id.png"]}))

    proc = subprocess.run(
        ["just", "member", str(seed)],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        env={**os.environ, "CLUB_URL": live.base_url,
             "CLUB_USER": live.admin, "CLUB_ADMIN_PW": live.admin_pw},
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert live.ok("user", "get", username)["status"] == "active"
    skipped = "skipping document and submit" in proc.stdout
    assert skipped == (not live.caps["identityVerification"]), proc.stdout


# ── Credits ─────────────────────────────────────────────────────────────


def test_credits_follow_the_module(live: Live):
    member = live.active_member()
    account = json.dumps({
        "membername": member, "credits": 5, "reason": "Live test",
        "validFrom": local(days_from_today(-1)), "validUntil": local(days_from_today(30)),
    })
    if live.caps["creditSystem"]:
        opened = live.ok("credits", "open", account)
        listed = live.ok("credits", "list", "--membername", member)
        assert [a["accountId"] for a in listed["items"]] == [opened["accountId"]]
        assert live.ok("mycredits", "list", user=member, pw=MEMBER_PASSWORD)
    else:
        for args in (("credits", "open", account), ("credits", "list")):
            res = live.refused(*args)
            assert "CREDIT_SYSTEM_DISABLED" in res.output
            assert "capabilities" in res.stderr


# ── Event marketing ─────────────────────────────────────────────────────


def test_event_marketing_follows_the_module(live: Live):
    event = live.event("camp", days_from_today(20), rrule="FREQ=DAILY;COUNT=2", visibility="public")
    public_id = live.public_event_id(event["title"])
    block = json.dumps({"fee": 5000, "hasOpenSlots": True})
    if live.caps["eventMarketing"]:
        live.ok("event", "marketing", "set", str(event["id"]), "--json", block)
        assert live.ok("event", "marketing", "get", str(event["id"]))["fee"] == 5000
        public = live.ok("public", "event-marketing", public_id, anonymous=True)
        assert public["fee"] == 5000
        live.ok("event", "marketing", "clear", str(event["id"]))
    else:
        res = live.refused("event", "marketing", "set", str(event["id"]), "--json", block)
        assert "EVENT_MARKETING_DISABLED" in res.output
        res = live.refused("public", "event-marketing", public_id, anonymous=True)
        assert "EVENT_MARKETING_DISABLED" in res.output


# ── Evaluations (club_server#535, cli #59) ──────────────────────────────


def _template(name: str) -> dict:
    """A template with a required rating, an info block and a private question."""
    return {
        "name": name,
        "layout": [
            {"type": "rating", "question": "Edges", "isRequired": True, "allowEvidence": True},
            {"type": "info", "markdown": "Scored on the day."},
            {"section": "Coach only", "items": [
                {"type": "qa", "question": "Private remarks", "isPrivate": True},
            ]},
        ],
    }


def _item_ids(template: dict) -> dict[str, int]:
    """Each item's id, by its question (or, for an info block, its markdown)."""
    return {item.get("question") or item["markdown"]: item["id"] for item in template["items"]}


def _evaluation(live: Live, coach: str, template_id: int, member: str) -> dict:
    return live.ok("evaluations", "create", json.dumps({"templateId": template_id, "createdFor": member}),
                   user=coach, pw=MEMBER_PASSWORD)


@pytest.mark.modules_off
def test_evaluations_are_refused_when_off(live: Live):
    if live.caps["evaluations"]:
        pytest.skip("evaluations are on in this stack")  # only when the mode was not declared
    for args in (
        ("evaluations", "list"),
        ("evaluations", "templates", "list"),
        ("evaluations", "templates", "create", json.dumps(_template("Skating basics"))),
        ("evaluations", "templates", "items", "search"),
    ):
        res = live.refused(*args)
        assert "EVALUATIONS_DISABLED" in res.output


@pytest.mark.modules_on
def test_evaluation_lifecycle_when_on(live: Live, tmp_path: Path):
    """A coach creates and answers; save waits for the required answer; the member reads it."""
    if not live.caps["evaluations"]:
        pytest.skip("evaluations are off in this stack")  # only when the mode was not declared
    member = live.active_member()
    coach = live.coach()
    template = live.ok("evaluations", "templates", "create", json.dumps(_template(live.unique("Tpl"))))
    ids = _item_ids(template)
    edges, private = str(ids["Edges"]), str(ids["Private remarks"])
    assert live.ok("evaluations", "templates", "get", str(template["id"]))["items"] == template["items"]

    # An admin evaluates no one.
    res = live.refused("evaluations", "create", json.dumps({"templateId": template["id"], "createdFor": member}))
    assert "INSUFFICIENT_PERMISSION" in res.output

    ev = _evaluation(live, coach, template["id"], member)
    eid = str(ev["id"])
    assert (ev["status"], ev["createdBy"], ev["createdFor"]) == ("draft", coach, member)
    as_coach = {"user": coach, "pw": MEMBER_PASSWORD}
    # A period ends in the past (R4): yesterday, not today at 06:00, which is
    # still ahead before dawn.
    period = json.dumps({"periodStart": local(days_from_today(-7)), "periodEnd": local(days_from_today(-1))})
    assert live.ok("evaluations", "update", eid, period, **as_coach)["periodStartUtc"] is not None

    # The private answer alone leaves the required rating unanswered.
    live.ok("evaluations", "answers", "put", eid, private, '{"valueText": "Watch the left edge"}', **as_coach)
    res = live.refused("evaluations", "save", eid, **as_coach)
    assert "INCOMPLETE" in res.stderr and edges in res.stderr, res.output
    res = live.refused("evaluations", "answers", "put", eid, edges, '{"valueNum": 9}', **as_coach)
    assert "INVALID_ANSWER" in res.stderr
    live.ok("evaluations", "answers", "put", eid, edges, '{"valueNum": 2}', **as_coach)
    cleared = live.ok("evaluations", "answers", "clear", eid, edges, **as_coach)
    assert [a["itemId"] for a in cleared["answers"]] == [int(private)]
    answered = live.ok("evaluations", "answers", "put", eid, edges, '{"valueNum": 4, "coachNote": "Strong"}', **as_coach)
    assert {a["itemId"] for a in answered["answers"]} == {int(edges), int(private)}

    # Evidence: a file uploaded for the rating, stored for the member and staff (R56d).
    photo = tmp_path / "edge.png"
    shutil.copy(ID_DOCUMENT, photo)
    with_evidence = live.ok("evaluations", "evidence", "upload", eid, edges, str(photo), **as_coach)
    (evidence,) = next(a for a in with_evidence["answers"] if a["itemId"] == int(edges))["evidence"]
    assert evidence["metadata"] == "edge.png"
    res = live.refused("evaluations", "evidence", "upload", eid, private, str(photo), **as_coach)
    assert "INVALID_EVIDENCE" in res.stderr
    notes = tmp_path / "notes.txt"
    notes.write_text("not evidence")
    res = live.refused("evaluations", "evidence", "upload", eid, edges, str(notes), **as_coach)
    assert "INVALID_EVIDENCE" in res.stderr

    live.ok("evaluations", "save", eid, **as_coach)
    res = live.refused("evaluations", "evidence", "upload", eid, edges, str(photo), **as_coach)
    assert "INVALID_STATE" in res.output
    live.ok("evaluations", "revert", eid, **as_coach)
    live.ok("evaluations", "save", eid, **as_coach)
    preview = tmp_path / "preview.pdf"
    live.ok("evaluations", "pdf", eid, "-o", str(preview), **as_coach)
    assert preview.read_bytes().startswith(b"%PDF")
    live.ok("evaluations", "publish", eid, **as_coach)
    assert live.ok("evaluations", "get", eid, **as_coach)["status"] == "published"
    listed = live.ok("evaluations", "list", "--created-for", member, "--status", "published", **as_coach)
    assert [e["id"] for e in listed["items"]] == [ev["id"]]
    assert live.ok("evaluations", "list", "--general", **as_coach)["total"] == 1

    # Admins see no evaluations, on either surface.
    assert ev["id"] not in [e["id"] for e in live.ok("evaluations", "list")["items"]]
    assert "EVALUATION_NOT_FOUND" in live.refused("evaluations", "get", eid).output
    live.refused("myevaluations", "list", "--username", member)

    # The member reads the member view, without the private question, and the stored copy.
    as_member = {"user": member, "pw": MEMBER_PASSWORD}
    mine = live.ok("myevaluations", "list", **as_member)
    assert [e["id"] for e in mine["items"]] == [ev["id"]]
    view = live.ok("myevaluations", "get", eid, **as_member)
    assert [a["itemId"] for a in view["answers"]] == [int(edges)]
    assert [e["mediaUuid"] for e in view["answers"][0]["evidence"]] == [evidence["mediaUuid"]]
    assert int(private) not in [i["id"] for i in view["template"]["items"]]
    (copy,) = live.ok("myevaluations", "media", eid, **as_member)["member_copy"]
    assert copy["media"]["mimeType"] == "application/pdf"
    stored = tmp_path / "member_copy.pdf"
    live.ok("upload", "download", copy["media"]["uuid"], "-o", str(stored), **as_member)
    assert stored.read_bytes().startswith(b"%PDF")
    # The member downloads the evidence; another member is refused it.
    downloaded = tmp_path / "evidence.png"
    live.ok("upload", "download", evidence["mediaUuid"], "-o", str(downloaded), **as_member)
    assert downloaded.read_bytes() == ID_DOCUMENT.read_bytes()
    stranger = live.active_member()
    live.refused("upload", "download", evidence["mediaUuid"], "-o", str(tmp_path / "x.png"),
                 user=stranger, pw=MEMBER_PASSWORD)
    assert not (tmp_path / "x.png").exists()
    # A coach reads it too.
    assert live.ok("myevaluations", "get", eid, "--username", member, **as_coach)["id"] == ev["id"]

    live.ok("evaluations", "unpublish", eid, **as_coach)
    assert live.ok("myevaluations", "list", **as_member)["items"] == []


@pytest.mark.modules_on
def test_template_items_when_on(live: Live):
    """Items are added, replaced, found and removed one at a time; the layout is reordered whole."""
    if not live.caps["evaluations"]:
        pytest.skip("evaluations are off in this stack")  # only when the mode was not declared
    template = live.ok("evaluations", "templates", "create", json.dumps(_template(live.unique("Tpl"))))
    tid = str(template["id"])

    question = live.unique("Stops both ways")
    added = live.ok("evaluations", "templates", "items", "add", tid,
                    json.dumps({"type": "yesNo", "question": question}), "--section", "Coach only")
    item = str(_item_ids(added)[question])
    (section,) = [entry for entry in added["layout"] if isinstance(entry, dict)]
    assert section["items"][-1] == int(item)

    renamed = live.unique("Stops on both edges")
    live.ok("evaluations", "templates", "items", "replace", tid, item, json.dumps({"type": "yesNo", "question": renamed}))
    res = live.refused("evaluations", "templates", "items", "replace", tid, item, json.dumps({"type": "qa", "question": "x"}))
    assert "ITEM_TYPE_FIXED" in res.stderr
    hits = live.ok("evaluations", "templates", "items", "search", "--search", renamed, "--type", "yesNo")["items"]
    assert [(h["templateId"], h["item"]["id"]) for h in hits] == [(template["id"], int(item))]

    live.ok("evaluations", "templates", "items", "remove", tid, item)
    current = live.ok("evaluations", "templates", "get", tid)
    assert int(item) not in [i["id"] for i in current["items"]]
    res = live.refused("evaluations", "templates", "items", "remove", tid, item)
    assert "ITEM_NOT_FOUND" in res.stderr

    reordered = list(reversed(current["layout"]))
    name = live.unique("Tpl v2")
    updated = live.ok("evaluations", "templates", "update", tid, json.dumps({"name": name, "layout": reordered}))
    assert (updated["name"], updated["layout"]) == (name, reordered)
    res = live.refused("evaluations", "templates", "update", tid, json.dumps({"layout": reordered[:1]}))
    assert "INVALID_LAYOUT" in res.stderr


@pytest.mark.modules_on
def test_evaluation_transfer_and_trash_when_on(live: Live):
    if not live.caps["evaluations"]:
        pytest.skip("evaluations are off in this stack")  # only when the mode was not declared
    member = live.active_member()
    coach, other = live.coach(), live.coach()
    template = live.ok("evaluations", "templates", "create", json.dumps(_template(live.unique("Tpl"))))
    ev = _evaluation(live, coach, template["id"], member)
    eid = str(ev["id"])
    as_other = {"user": other, "pw": MEMBER_PASSWORD}

    assert "Success (no content)" in live.ok("evaluations", "transfer", eid, other, user=coach, pw=MEMBER_PASSWORD)
    assert live.ok("evaluations", "get", eid, **as_other)["owner"] == other
    res = live.refused("evaluations", "get", eid, user=coach, pw=MEMBER_PASSWORD)
    assert "EVALUATION_NOT_FOUND" in res.output

    live.ok("evaluations", "delete", eid, **as_other)
    assert ev["id"] in [e["id"] for e in live.ok("evaluations", "list", "--deleted", **as_other)["items"]]
    live.ok("trash", "restore", "evaluation", eid, **as_other)
    live.ok("evaluations", "delete", eid, **as_other)

    # A template an evaluation uses cannot be deleted (TEMPLATE_IN_USE); use a fresh one.
    template = live.ok("evaluations", "templates", "create", json.dumps(_template(live.unique("Tpl"))))
    tid = str(template["id"])
    live.ok("evaluations", "templates", "delete", tid)
    deleted = live.ok("evaluations", "templates", "list", "--deleted")["items"]
    assert template["id"] in [t["id"] for t in deleted]
    assert template["id"] in [t["id"] for t in live.ok("trash", "list", "evaluation-template")["items"]]
    live.ok("trash", "restore", "evaluation-template", tid)
    assert live.ok("evaluations", "templates", "get", tid)["id"] == template["id"]


@pytest.mark.modules_on
def test_evaluation_rules_when_on(live: Live):
    """Coaches write templates under unique names; a review looks back, is one per key, and may change event."""
    if not live.caps["evaluations"]:
        pytest.skip("evaluations are off in this stack")  # only when the mode was not declared
    member = live.active_member()
    coach = live.coach()
    other_coach = live.coach()
    as_coach = {"user": coach, "pw": MEMBER_PASSWORD}
    as_other = {"user": other_coach, "pw": MEMBER_PASSWORD}

    # A coach writes a template (R46); a member cannot.
    name = live.unique("Tpl")
    template = live.ok("evaluations", "templates", "create", json.dumps(_template(name)), **as_coach)
    assert template["createdBy"] == coach
    tid = str(template["id"])
    renamed = live.ok("evaluations", "templates", "update", tid, json.dumps({"name": f"{name}_v2"}), **as_coach)
    assert renamed["name"] == f"{name}_v2"
    res = live.refused("evaluations", "templates", "create", json.dumps(_template(live.unique("Tpl"))),
                       user=member, pw=MEMBER_PASSWORD)
    assert "INSUFFICIENT_PERMISSION" in res.output

    # A live template's name is unique, ignoring case and outer spaces (R49a).
    res = live.refused("evaluations", "templates", "create", json.dumps(_template(f"  {name.upper()}_V2 ")),
                       **as_coach)
    assert "TEMPLATE_NAME_TAKEN" in res.stderr

    # A period ends in the past (R4); a one-day period is fine.
    future = {"templateId": template["id"], "createdFor": member,
              "periodStart": local(days_from_today(-1)), "periodEnd": local(days_from_today(1))}
    res = live.refused("evaluations", "create", json.dumps(future), **as_coach)
    assert "PERIOD_IN_FUTURE" in res.stderr
    day = local(days_from_today(-3))
    one_day = {"templateId": template["id"], "createdFor": member, "periodStart": day, "periodEnd": day}
    ev = live.ok("evaluations", "create", json.dumps(one_day), **as_coach)
    eid = str(ev["id"])
    assert ev["periodStartUtc"] == ev["periodEndUtc"]

    # One live review per coach, member, template and period (R7).
    res = live.refused("evaluations", "create", json.dumps(one_day), **as_coach)
    assert "DUPLICATE_EVALUATION" in res.stderr
    live.ok("evaluations", "create", json.dumps(one_day), **as_other)
    res = live.refused("evaluations", "transfer", eid, other_coach, **as_coach)
    assert "DUPLICATE_EVALUATION" in res.stderr
    assert live.ok("evaluations", "get", eid, **as_coach)["owner"] is None

    # A draft's event changes, eligibility checked again (R23).
    event = live.event("oneOff", days_from_today(2))
    res = live.refused("evaluations", "update", eid, json.dumps({"eventId": event["id"]}), **as_coach)
    assert "NOT_ELIGIBLE" in res.output
    res = live.refused("evaluations", "update", eid, '{"eventId": 999999999}', **as_coach)
    assert "EVENT_NOT_FOUND" in res.output
    general = live.ok("evaluations", "update", eid, '{"eventId": null}', **as_coach)
    assert general["eventId"] is None
    assert general["periodStartUtc"] == ev["periodStartUtc"]
