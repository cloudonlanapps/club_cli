"""club_admin — sudo-only destructive operations.

Sibling tool to club_client. Exposes hard-delete endpoints and
orphan-upload cleanup. Not for general use; kept as a separate binary
so the destructive surface is one explicit step away from normal admin
work.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click
import httpx


class Context:
    base_url: str
    username: str | None
    password: str | None

    def __init__(self, base_url: str, username: str | None, password: str | None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None

    @property
    def token(self) -> str:
        if self._token is None:
            if not self.username:
                raise click.UsageError("--user is required")
            if not self.password:
                raise click.UsageError("--pw is required")
            r = httpx.post(
                f"{self.base_url}/v1/auth/login",
                json={"username": self.username, "password": self.password},
            )
            if r.status_code != 200:
                raise click.ClickException(f"login failed: {r.status_code} {r.text}")
            self._token = r.json()["accessToken"]
        return self._token

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


pass_context = click.make_pass_decorator(Context)


def _print(response: httpx.Response) -> None:
    if response.status_code == 204:
        click.echo("Success (no content)")
        return
    try:
        click.echo(json.dumps(response.json(), indent=2))
    except json.JSONDecodeError:
        click.echo(response.text)
    if response.status_code >= 400:
        sys.exit(1)


@click.group()
@click.option("--user", "-u", default=None, required=True, help="Admin username (must have sudo privileges)")
@click.option("--password", "--pw", default=None, required=True, help="Admin password")
@click.option("--base-url", default="http://127.0.0.1:8001", help="API base URL")
@click.pass_context
def main(ctx: click.Context, user: str, password: str, base_url: str) -> None:
    """club_admin — sudo-only hard-delete and cleanup operations."""
    ctx.obj = Context(base_url, user, password)


# ── Hard-delete ──────────────────────────────────────────────────────────


@main.group()
def user() -> None:
    """User-level destructive ops."""


@user.command("hard-delete")
@click.argument("username")
@pass_context
def user_hard_delete(ctx: Context, username: str) -> None:
    """DELETE /v1/users/by_id/<username>/hard"""
    r = httpx.delete(f"{ctx.base_url}/v1/users/by_id/{username}/hard", headers=ctx.headers)
    _print(r)


@main.group()
def venue() -> None:
    """Venue-level destructive ops."""


@venue.command("hard-delete")
@click.argument("venue_id", type=int)
@pass_context
def venue_hard_delete(ctx: Context, venue_id: int) -> None:
    """DELETE /v1/venues/by_id/<id>/hard"""
    r = httpx.delete(f"{ctx.base_url}/v1/venues/by_id/{venue_id}/hard", headers=ctx.headers)
    _print(r)


@main.group()
def group() -> None:
    """Group-level destructive ops."""


@group.command("hard-delete")
@click.argument("group_id", type=int)
@pass_context
def group_hard_delete(ctx: Context, group_id: int) -> None:
    """DELETE /v1/groups/by_id/<id>/hard"""
    r = httpx.delete(f"{ctx.base_url}/v1/groups/by_id/{group_id}/hard", headers=ctx.headers)
    _print(r)


@main.group()
def event() -> None:
    """Event-level destructive ops."""


@event.command("hard-delete")
@click.argument("event_id", type=int)
@pass_context
def event_hard_delete(ctx: Context, event_id: int) -> None:
    """DELETE /v1/events/by_id/<id>/hard"""
    r = httpx.delete(f"{ctx.base_url}/v1/events/by_id/{event_id}/hard", headers=ctx.headers)
    _print(r)


@main.group()
def evaluation() -> None:
    """Evaluation-level destructive ops (the evaluations module must be on)."""


@evaluation.command("hard-delete")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluation_hard_delete(ctx: Context, evaluation_id: int) -> None:
    """DELETE /v1/evaluations/by_id/<id>/hard"""
    r = httpx.delete(f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/hard", headers=ctx.headers)
    _print(r)


@main.group("evaluation-template")
def evaluation_template() -> None:
    """Evaluation-template destructive ops (the evaluations module must be on)."""


@evaluation_template.command("hard-delete")
@click.argument("template_id", type=int)
@pass_context
def evaluation_template_hard_delete(ctx: Context, template_id: int) -> None:
    """DELETE /v1/evaluations/templates/by_id/<id>/hard"""
    r = httpx.delete(f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}/hard", headers=ctx.headers)
    _print(r)


# ── Media ────────────────────────────────────────────────────────────────
#
# These target the media API (/v1/media).


@main.group()
def uploads() -> None:
    """Media destructive ops + orphan finder."""


def _list_media(ctx: Context) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        r = httpx.get(
            f"{ctx.base_url}/v1/media",
            params={"limit": 100, "offset": offset},
            headers=ctx.headers,
        )
        if r.status_code != 200:
            raise click.ClickException(f"media list failed: {r.status_code} {r.text}")
        page = r.json().get("items", [])
        if not page:
            break
        items.extend(page)
        if len(page) < 100:
            break
        offset += 100
    return items


def _user_status(ctx: Context, username: str) -> tuple[bool, bool]:
    """Returns (exists, soft_deleted). Exists=False means 404."""
    r = httpx.get(f"{ctx.base_url}/v1/users/by_id/{username}", headers=ctx.headers)
    if r.status_code == 404:
        return False, False
    if r.status_code != 200:
        raise click.ClickException(f"user get {username}: {r.status_code} {r.text}")
    return True, r.json().get("deletedAtUtc") is not None


def _is_orphan(ctx: Context, item: dict[str, Any], cache: dict[str, tuple[bool, bool]]) -> bool:
    """Media is orphaned when its uploader is absent or soft-deleted."""
    owner = item.get("uploadedBy")
    if not owner:
        return True
    if owner not in cache:
        cache[owner] = _user_status(ctx, owner)
    exists, deleted = cache[owner]
    return (not exists) or deleted


@uploads.command("orphans")
@pass_context
def uploads_orphans(ctx: Context) -> None:
    """List media whose uploadedBy user is missing or soft-deleted."""
    cache: dict[str, tuple[bool, bool]] = {}
    for m in _list_media(ctx):
        if not _is_orphan(ctx, m, cache):
            continue
        click.echo(json.dumps({
            "id": m.get("id"),
            "uuid": m.get("uuid"),
            "uploadedBy": m.get("uploadedBy"),
            "mediaType": m.get("mediaType"),
            "originalFilename": m.get("originalFilename"),
        }))


@uploads.command("delete")
@click.argument("media_id", type=int)
@click.option("--hard", is_flag=True, help="Permanently remove rather than soft-delete.")
@pass_context
def uploads_delete(ctx: Context, media_id: int, hard: bool) -> None:
    """DELETE /v1/media/by_id/<id>, or .../hard with --hard."""
    if hard:
        r = httpx.delete(f"{ctx.base_url}/v1/media/by_id/{media_id}/hard", headers=ctx.headers)
    else:
        r = httpx.delete(f"{ctx.base_url}/v1/media/by_id/{media_id}", headers=ctx.headers)
    _print(r)


@uploads.command("cleanup")
@click.option("--soft", is_flag=True, help="Soft-delete instead of removing permanently.")
@pass_context
def uploads_cleanup(ctx: Context, soft: bool) -> None:
    """Find orphans (uploadedBy missing/soft-deleted) and remove each.

    Defaults to a hard delete: an orphan's owner is already gone, so a
    soft delete would leave it orphaned and still listed on the next run.
    """
    cache: dict[str, tuple[bool, bool]] = {}
    deleted = 0
    for m in _list_media(ctx):
        if not _is_orphan(ctx, m, cache):
            continue
        mid = m["id"]
        # A hard delete is refused unless the media is already soft-deleted,
        # so orphans that are still live need both steps.
        if not soft and not m.get("deletedAtUtc"):
            pre = httpx.delete(f"{ctx.base_url}/v1/media/by_id/{mid}", headers=ctx.headers)
            if pre.status_code not in (200, 204):
                click.echo(f"FAIL id={mid} (soft): {pre.status_code} {pre.text}", err=True)
                continue
        if soft:
            r = httpx.delete(f"{ctx.base_url}/v1/media/by_id/{mid}", headers=ctx.headers)
        else:
            r = httpx.delete(f"{ctx.base_url}/v1/media/by_id/{mid}/hard", headers=ctx.headers)
        if r.status_code not in (200, 204):
            click.echo(f"FAIL id={mid}: {r.status_code} {r.text}", err=True)
            continue
        click.echo(f"deleted id={mid} uuid={m.get('uuid')} owner={m.get('uploadedBy')}")
        deleted += 1
    click.echo(f"\norphans cleaned: {deleted}")


if __name__ == "__main__":
    main()
