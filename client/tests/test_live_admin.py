"""club_admin's hard deletes, and restore, against a live server (club_cli_old#55)."""
from __future__ import annotations

import json

from click.testing import CliRunner, Result

from club_admin.cli import main as admin_main

from .live import Live


def admin(live: Live, *args: str) -> Result:
    return CliRunner().invoke(admin_main, ["--base-url", live.base_url, "-u", live.admin, "--pw", live.admin_pw, *args])


def code_of(output: str) -> str:
    return json.loads(output)["detail"]["code"]


def test_hard_delete_needs_a_soft_delete_first(live: Live):
    venue_id = str(live.venue())

    res = admin(live, "venue", "hard-delete", venue_id)
    assert res.exit_code == 1, res.output
    assert code_of(res.output) == "HARD_DELETE_NEEDS_SOFT_DELETE"

    live.ok("venue", "delete", venue_id)
    res = admin(live, "venue", "hard-delete", venue_id)
    assert res.exit_code == 0, res.output
    assert live.refused("venue", "get", venue_id)


def test_restore_of_a_live_item_has_nothing_to_restore(live: Live):
    venue_id = str(live.venue())

    res = live.refused("trash", "restore", "venue", venue_id)
    assert code_of(res.stdout) == "NOTHING_TO_RESTORE"

    live.ok("venue", "delete", venue_id)
    live.ok("trash", "restore", "venue", venue_id)
    res = live.refused("trash", "restore", "venue", venue_id)
    assert code_of(res.stdout) == "NOTHING_TO_RESTORE"
