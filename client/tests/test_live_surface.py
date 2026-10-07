"""Every command wrapped for club_server@main, run once against a live server.

The endpoint contract proves each server operation has a call site; these
prove the call sites send what the server accepts, in either module mode.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from .live import MEMBER_PASSWORD, Live, days_from_today, slot

PHOTO = Path(__file__).resolve().parent / "fixtures" / "id_proof.png"


# ── Users ───────────────────────────────────────────────────────────────


def test_user_private_record_for_self_and_admin(live: Live):
    member = live.active_member()

    assert live.ok("user", "private", member)["email"] == f"{member}@example.com"
    assert live.ok("user", "private", member, user=member, pw=MEMBER_PASSWORD)["username"] == member


def test_superadmin_transfer_needs_yes_and_can_be_handed_back(live: Live):
    heir = live.active_member()

    res = live.refused("user", "transfer-superadmin", heir)
    assert "--yes" in res.output
    assert live.ok("user", "transfer-superadmin", heir, "--yes")["isSuperAdmin"] is True
    try:
        assert live.ok("user", "get", live.admin)["isSuperAdmin"] is False
    finally:
        live.ok("user", "transfer-superadmin", live.admin, "--yes", user=heir, pw=MEMBER_PASSWORD)
    assert live.ok("user", "get", live.admin)["isSuperAdmin"] is True


def test_auth_refresh_exchanges_the_login_refresh_token(live: Live):
    pair = live.ok("auth", "refresh")
    assert pair["accessToken"] and pair["refreshToken"]

    res = live.refused("auth", "refresh", anonymous=True)
    assert "refreshToken" in res.output


# ── Events, groups, attendance ──────────────────────────────────────────


def test_event_eligible_lists_members_the_event_admits(live: Live):
    member = live.active_member()
    camp = live.event("camp", days_from_today(20), rrule="FREQ=DAILY;COUNT=2")

    eligible = live.ok("event", "eligible", str(camp["id"]))

    assert member in [u["username"] for u in eligible]


def test_mygroups_get_one_group(live: Live):
    member = live.active_member()
    group = live.ok("groups", "create", json.dumps({"name": live.unique("Group")}))
    live.ok("group", "add-member", str(group["id"]), member)

    got = live.ok("mygroups", "get", str(group["id"]), user=member, pw=MEMBER_PASSWORD)

    assert got["id"] == group["id"]


def test_attendance_mark_is_cleared(live: Live):
    member = live.active_member()
    start = days_from_today(-1)
    camp = live.event("camp", start, rrule="FREQ=DAILY;COUNT=5")
    eid, occurrence = str(camp["id"]), slot(start)
    live.ok("enrollment", "assign", eid, member)
    live.ok("occurrences", "mark-attendance", eid, occurrence,
            json.dumps({"records": [{"membername": member, "status": "present"}]}))

    live.ok("occurrences", "clear-attendance", eid, occurrence, member)

    records = live.ok("occurrences", "attendance", eid, occurrence)
    assert member not in [r.get("membername") for r in records]


# ── Notifications and broadcasts ────────────────────────────────────────


def test_notification_round_trip(live: Live):
    member = live.active_member()
    as_member = {"user": member, "pw": MEMBER_PASSWORD}
    created = live.ok("notifications", "create", json.dumps({
        "username": member, "type": "custom", "channel": "in_app", "payload": {"title": "Hello"},
    }))
    nid = str(created["id"])

    assert live.ok("notifications", "unread-count", **as_member)["count"] >= 1
    unread = live.ok("notifications", "list", "--unread-only", **as_member)
    assert created["id"] in [n["id"] for n in unread["items"]]
    live.ok("notifications", "pending-actions", **as_member)
    live.ok("notifications", "read", nid, **as_member)
    live.ok("notifications", "read-all", **as_member)
    assert live.ok("notifications", "unread-count", **as_member)["count"] == 0
    live.refused("notifications", "delete", nid, **as_member)  # admin only
    live.ok("notifications", "delete", nid)

    live.ok("notifications", "set-preferences", "--no-email", "--push", **as_member)
    prefs = live.ok("notifications", "preferences", **as_member)
    assert (prefs["emailEnabled"], prefs["pushEnabled"]) == (False, True)
    assert live.refused("notifications", "set-preferences", **as_member).exit_code == 2


def test_broadcast_round_trip(live: Live):
    member = live.active_member()
    sent = live.ok("broadcasts", "create", json.dumps({
        "audienceSelector": {"kind": "users", "usernames": [member]},
        "payload": {"title": "Rink closed"},
        "expiresAt": (days_from_today(3)).isoformat(),
    }))
    bid = str(sent["id"])

    assert sent["id"] in [b["id"] for b in live.ok("broadcasts", "list")["items"]]
    assert live.ok("broadcasts", "get", bid)["id"] == sent["id"]
    recipients = live.ok("broadcasts", "recipients", bid, "--status", "unread")
    assert [r["username"] for r in recipients["items"]] == [member]
    live.ok("broadcasts", "revoke", bid)


def test_audit_log_finds_a_change(live: Live):
    venue = live.venue()

    entries = live.ok("audit-log", "--resource-type", "venue", "--resource-id", str(venue), "--actor", live.admin)

    assert entries["rows"], entries


# ── Public ──────────────────────────────────────────────────────────────


def test_public_reads_need_no_sign_in(live: Live):
    camp = live.event("camp", days_from_today(25), rrule="FREQ=DAILY;COUNT=2", visibility="public")
    public_id = live.public_event_id(camp["title"])

    assert live.ok("public", "event", public_id, anonymous=True)["title"] == camp["title"]
    events = live.ok("public", "events", "--type", "camp", "--from", "20200101 0000", anonymous=True)
    assert public_id in [e["publicId"] for e in events["items"]]
    venue_id = live.ok("public", "event", public_id, anonymous=True)["venue"]["publicId"]
    assert live.ok("public", "venue", venue_id, anonymous=True)["publicId"] == venue_id
    assert venue_id in [v["publicId"] for v in live.ok("public", "venues", anonymous=True)]
    assert "clubInfo" in live.ok("public", "club-info", anonymous=True)


def test_public_staff_and_profile(live: Live):
    guest = live.unique("guest")
    live.ok("users", "create", json.dumps(live.member_json(guest)), "--guest")
    live.ok("staff", "set", guest, "--position", "1", "--guest", "--no-hidden")

    public_id = live.ok("user", "get", guest)["publicId"]

    staff = live.ok("public", "staff", "--include-guests", anonymous=True)
    assert public_id in [s["publicId"] for s in staff]
    assert public_id not in [s["publicId"] for s in live.ok("public", "staff", anonymous=True)]
    assert live.ok("public", "profile", public_id, anonymous=True)["publicId"] == public_id


def test_public_inquiry_reaches_the_admin_inbox(live: Live):
    email = f"{live.unique('asker')}@example.com"
    assert "token" in live.ok("public", "inquiry-token", anonymous=True)

    live.ok("public", "inquiry-submit", json.dumps(
        {"kind": "contact", "name": "Asker", "email": email, "message": "Is the rink open?"}), anonymous=True)

    inbox = live.ok("inquiries", "list")
    assert email in [i["email"] for i in inbox["items"]]


# ── Media ───────────────────────────────────────────────────────────────


def _upload(live: Live, *extra: str) -> tuple[int, str]:
    """(id, uuid) of a fresh upload: the URL on stdout, the id on stderr."""
    res = live.run("uploads", "add-file", str(PHOTO), *extra)
    assert res.exit_code == 0, res.output
    uuid = res.stdout.strip().split("/by_id/")[1].split("/")[0]
    media_id = int(res.stderr.split("id ")[1].split(":")[0])
    return media_id, uuid


def test_link_get_and_metadata(live: Live):
    venue = str(live.venue())
    _, uuid = _upload(live)
    live.ok("media", "link", "venue", venue, uuid, "--tag", "hero")

    assert live.ok("media", "get", "venue", venue, "hero", uuid)["media"]["uuid"] == uuid
    live.ok("media", "set-metadata", "venue", venue, "hero", uuid, "Front entrance")
    assert live.ok("media", "get", "venue", venue, "hero", uuid)["metadata"] == "Front entrance"
    live.ok("media", "set-metadata", "venue", venue, "hero", uuid, "--clear")
    assert live.ok("media", "get", "venue", venue, "hero", uuid)["metadata"] is None


def test_restricted_file_needs_the_token_to_download(live: Live, tmp_path: Path):
    media_id, uuid = _upload(live, "--access-role", "admin", "--encrypt")

    assert live.ok("upload", "download", uuid, "--head")["contentType"].startswith("image/")
    res = live.refused("upload", "download", uuid, anonymous=True)
    assert "AUTHENTICATION_REQUIRED" in res.output
    out = tmp_path / "photo"
    live.ok("upload", "download", uuid, "-o", str(out))
    assert out.stat().st_size > 0

    live.ok("upload", "set-access", str(media_id), "--role", "public")
    live.ok("upload", "download", uuid, anonymous=True)


def test_admin_uploads_a_file_that_belongs_to_a_member(live: Live, tmp_path: Path):
    """--owner (club_server#18): `self` is the member, and so is the right to delete."""
    member, other = live.active_member(), live.active_member()
    as_member = {"user": member, "pw": MEMBER_PASSWORD}
    media_id, uuid = _upload(live, "--owner", member, "--access-role", "self")

    assert live.ok("upload", "get", str(media_id))["uploadedBy"] == member
    assert media_id in [m["id"] for m in live.ok("media", "myfiles", "--limit", "100", **as_member)["items"]]
    out = tmp_path / "photo"
    live.ok("upload", "download", uuid, "-o", str(out), **as_member)
    assert out.stat().st_size > 0
    live.refused("upload", "download", uuid, user=other, pw=MEMBER_PASSWORD)

    # Anyone but an admin may name only themselves.
    res = live.refused("uploads", "add-file", str(PHOTO), "--owner", other, **as_member)
    assert "FORBIDDEN" in res.output
    live.ok("uploads", "add-file", str(PHOTO), "--owner", member, **as_member)
    res = live.refused("uploads", "add-file", str(PHOTO), "--owner", live.unique("nobody"))
    assert "USER_NOT_FOUND" in res.output

    live.ok("upload", "delete", str(media_id), **as_member)


def test_upload_refuses_unknown_roles_and_head_matches_get(live: Live):
    res = live.refused("uploads", "add-file", str(PHOTO), "--access-role", "member")
    assert "Invalid value" in res.output
    # A direct GET confirms the served file matches what `--head` reports.
    _, uuid = _upload(live)
    head = live.ok("upload", "download", uuid, "--head")
    got = httpx.get(f"{live.base_url}/v1/media/by_id/{uuid}/download")
    assert got.headers["content-type"] == head["contentType"]
