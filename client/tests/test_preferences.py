"""preferences list/get/set/set-key over system preferences (club_cli_old#10)."""
from __future__ import annotations

import json

from .fakes import FakeHttp, run

PREFS = "/v1/admin/preferences"


def test_issue_10_list_and_get(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", PREFS, {"items": [{"key": "club_info", "value": {"name": "LTC"}}]})
    fake.on("GET", f"{PREFS}/club_info", {"key": "club_info", "value": {"name": "LTC"}})

    assert run("preferences", "list").exit_code == 0
    res = run("preferences", "get", "club_info")

    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["value"] == {"name": "LTC"}


def test_issue_10_set_from_json_sends_value_envelope(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{PREFS}/club_info", {"key": "club_info", "value": {"name": "LTC"}})

    res = run("preferences", "set", "club_info", "--json", '{"name": "LTC"}')

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{PREFS}/club_info")[0].json == {"value": {"name": "LTC"}}


def test_issue_10_set_accepts_any_json_value(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{PREFS}/max_members", {"key": "max_members", "value": 40})
    fake.on("PATCH", f"{PREFS}/motto", {"key": "motto", "value": "Train hard"})

    assert run("preferences", "set", "max_members", "--json", "40").exit_code == 0
    assert run("preferences", "set", "motto", "--json", '"Train hard"').exit_code == 0

    assert fake.calls_to("PATCH", f"{PREFS}/max_members")[0].json == {"value": 40}
    assert fake.calls_to("PATCH", f"{PREFS}/motto")[0].json == {"value": "Train hard"}


def test_issue_10_set_from_file(monkeypatch, tmp_path):
    fake = FakeHttp().install(monkeypatch)
    fake.on("PATCH", f"{PREFS}/club_info", {"key": "club_info", "value": {}})
    path = tmp_path / "club_info.json"
    path.write_text('{"history": {"paragraphs": ["a"]}}')

    res = run("preferences", "set", "club_info", "--file", str(path))

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{PREFS}/club_info")[0].json == {"value": {"history": {"paragraphs": ["a"]}}}


def test_issue_10_set_rejects_non_json_and_missing_source(monkeypatch):
    fake = FakeHttp().install(monkeypatch)

    bad = run("preferences", "set", "motto", "--json", "Train hard")
    none = run("preferences", "set", "motto")

    assert bad.exit_code != 0 and "JSON" in bad.output
    assert none.exit_code != 0 and "--json" in none.output and "--file" in none.output
    assert fake.calls == []


def test_issue_10_set_key_merges_into_existing_map(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{PREFS}/site_media", {"key": "site_media", "value": {"page_hero_default": "u1"}})
    fake.on("PATCH", f"{PREFS}/site_media", {"key": "site_media", "value": {}})

    res = run("preferences", "set-key", "site_media", "landing_background", "u2")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{PREFS}/site_media")[0].json == {
        "value": {"page_hero_default": "u1", "landing_background": "u2"},
    }


def test_issue_10_set_key_starts_from_empty_map_when_unset(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{PREFS}/site_media", status=404, payload={"detail": {"code": "PREFERENCE_NOT_FOUND"}})
    fake.on("PATCH", f"{PREFS}/site_media", {"key": "site_media", "value": {}})

    res = run("preferences", "set-key", "site_media", "landing_background", "u2")

    assert res.exit_code == 0, res.output
    assert fake.calls_to("PATCH", f"{PREFS}/site_media")[0].json == {"value": {"landing_background": "u2"}}


def test_issue_10_set_key_refuses_non_object_value(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", f"{PREFS}/motto", {"key": "motto", "value": "Train hard"})

    res = run("preferences", "set-key", "motto", "x", "y")

    assert res.exit_code != 0
    assert "not a JSON object" in res.output
    assert fake.calls_to("PATCH", f"{PREFS}/motto") == []


def test_issue_10_lesser_role_error_is_surfaced(monkeypatch):
    fake = FakeHttp().install(monkeypatch)
    fake.on("GET", PREFS, status=403, payload={"detail": {"code": "FORBIDDEN", "message": "super admin only"}})

    res = run("preferences", "list")

    assert res.exit_code != 0
    assert "super admin only" in res.output
