"""Drive the CLI in-process against a live server (the `just test` stacks).

The offline tests prove which endpoint a command calls; these prove the server
agrees. Every call goes through ``club_client.cli.main`` exactly as a user's
would, so a request the server refuses fails here the way it would for them.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest
from click.testing import CliRunner, Result

from club_client.cli import main

# The password every user a live test registers is given.
MEMBER_PASSWORD = "LiveTest123!"


def local(dt: datetime) -> str:
    """A datetime as the local ISO string the CLI's JSON inputs take."""
    return dt.replace(microsecond=0).isoformat()


def slot(dt: datetime) -> str:
    """A datetime as the 'YYYYMMDD HHMM' occurrence key the CLI takes."""
    return dt.strftime("%Y%m%d %H%M")


def days_from_today(days: int, hour: int = 6) -> datetime:
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


@dataclass
class Live:
    base_url: str
    admin: str
    admin_pw: str
    caps: dict[str, bool] = field(default_factory=dict)

    # -- running commands -------------------------------------------------

    def run(self, *args: str, user: str | None = None, pw: str | None = None, anonymous: bool = False) -> Result:
        creds: list[str] = [] if anonymous else ["-u", user or self.admin, "--pw", pw or self.admin_pw]
        return CliRunner().invoke(main, ["--base-url", self.base_url, *creds, *args])

    def ok(self, *args: str, **kwargs: Any) -> Any:
        """Run a command that must succeed; its stdout as JSON when it is JSON."""
        res = self.run(*args, **kwargs)
        if res.exit_code != 0:
            pytest.fail(f"`{' '.join(args)}` exited {res.exit_code}:\n{res.output}")
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            return res.stdout

    def refused(self, *args: str, **kwargs: Any) -> Result:
        """Run a command that must fail."""
        res = self.run(*args, **kwargs)
        assert res.exit_code != 0, f"`{' '.join(args)}` should have failed:\n{res.output}"
        return res

    # -- fixtures built through the CLI -----------------------------------

    @staticmethod
    def unique(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def venue(self) -> int:
        return self.ok("venues", "create", json.dumps({"name": self.unique("Rink")}))["id"]

    @staticmethod
    def member_json(username: str, **fields: Any) -> dict[str, Any]:
        return {
            "username": username, "password": MEMBER_PASSWORD,
            "email": f"{username}@example.com", "firstName": "Live", "lastName": "Test",
            "gender": "male", "dateOfBirth": "2012-05-03T00:00:00Z", "phone": "9000000000",
            **fields,
        }

    def active_member(self, **fields: Any) -> str:
        """A member created by the admin, which makes them active at once."""
        username = self.unique("member")
        created = self.ok("users", "create", json.dumps(self.member_json(username, **fields)))
        assert created["status"] == "active", created
        return username

    def coach(self) -> str:
        username = self.active_member()
        self.ok("user", "add-role", username, "coach")
        return username

    def event(self, type_: str, start: datetime, *, hours: int = 1, **fields: Any) -> dict[str, Any]:
        """An event with a venue and organizer of its own, so no two tests conflict."""
        body: dict[str, Any] = {
            "title": self.unique(f"Live {type_}"),
            "type": type_,
            "venueId": fields.pop("venueId", None) or self.venue(),
            "organizerName": fields.pop("organizerName", None) or self.coach(),
            "startTime": local(start),
            "endTime": local(start + timedelta(hours=hours)),
            **fields,
        }
        return self.ok("events", "create", json.dumps(body))

    def public_event_id(self, title: str) -> str:
        """An event's public id, which only the public listing carries."""
        items = self.ok("public", "events", "--limit", "100", anonymous=True)["items"]
        (match,) = [e for e in items if e["title"] == title]
        return match["publicId"]
