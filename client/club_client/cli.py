"""CLI client for club server API."""

import json
import mimetypes
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click
import httpx

# System local timezone
LOCAL_TZ = datetime.now(timezone.utc).astimezone().tzinfo

# Map local time field names to API field names (with Utc suffix)
# These fields are converted from ISO local time to UTC milliseconds
TIMESTAMP_FIELD_MAP = {
    "dateOfBirth": "dateOfBirthUtc",
    "startTime": "startTimeUtc",
    "endTime": "endTimeUtc",
    "untilTime": "untilTimeUtc",
    "effectiveTime": "effectiveTimeUtc",
    "expiresAt": "expiresAtUtc",
    "validFrom": "validFromUtc",
    "validUntil": "validUntilUtc",
    "registrationDeadline": "registrationDeadlineUtc",
    "periodStart": "periodStartUtc",
    "periodEnd": "periodEndUtc",
}


def local_iso_to_utc_ms(local_iso: str) -> int:
    """Convert ISO 8601 local time string to UTC milliseconds."""
    dt = datetime.fromisoformat(local_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return int(dt.timestamp() * 1000)


def utc_ms_to_local_str(utc_ms: int) -> str:
    """Convert UTC milliseconds to local time as 'YYYYMMDD HHMM' string."""
    utc_dt = datetime.fromtimestamp(utc_ms / 1000, tz=timezone.utc)
    local_dt = utc_dt.astimezone(LOCAL_TZ)
    return local_dt.strftime("%Y%m%d %H%M")


def add_local_times(occurrences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add localTime field to each occurrence for display."""
    for o in occurrences:
        if "occurrenceTimeUtc" in o:
            o["occurrenceTimeLocal"] = utc_ms_to_local_str(o["occurrenceTimeUtc"])
    return occurrences


def parse_local_time(value: str) -> str:
    """Parse 'YYYYMMDD HHMM' or ISO 8601 string into ISO 8601 format."""
    try:
        dt = datetime.strptime(value, "%Y%m%d %H%M")
        return dt.isoformat()
    except ValueError:
        return value


def today_midnight_iso() -> str:
    """Return today's date at midnight as ISO 8601 string."""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def add_days_iso(local_iso: str, days: int) -> str:
    """Add N days to a local ISO 8601 string and return as ISO 8601."""
    dt = datetime.fromisoformat(local_iso) + timedelta(days=days)
    return dt.isoformat()


def convert_timestamps(data: dict[str, Any]) -> dict[str, Any]:
    """Convert ISO local time strings to UTC milliseconds and rename to API field names.

    Example: "dateOfBirth": "1985-01-01T05:30:00" -> "dateOfBirthUtc": 473365800000
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in TIMESTAMP_FIELD_MAP and isinstance(value, str):
            # Convert and rename: dateOfBirth -> dateOfBirthUtc
            api_key = TIMESTAMP_FIELD_MAP[key]
            result[api_key] = local_iso_to_utc_ms(value)
        else:
            result[key] = value
    return result


# Eligibility on events and groups is an age band (club_server#16). The two
# date-of-birth bounds are what the server works out from it, and it refuses
# them on a write.
AGE_FIELDS = ("minAge", "maxAge")
REMOVED_DOB_FIELDS = ("dobOnOrAfter", "dobOnOrBefore", "dobOnOrAfterUtc", "dobOnOrBeforeUtc")

# Shared by the event and group write commands' help.
AGE_BAND_HELP = """Eligibility by age: minAge and maxAge are each {"years", "months", "days"}
    (years 0-150, months 0-11, days 0-30; months and days default to 0), or
    a whole number of years; null clears the bound. strictAge true admits
    ages exactly minAge to maxAge on the reference day; false (the default)
    widens each end by a year less a day. minAge above maxAge is refused
    (422 INVALID_STATE). The date-of-birth window is worked out by the
    server and cannot be written."""

# Shared by the event and group read commands' help.
AGE_WINDOW_HELP = """Eligibility: minAge and maxAge ({years, months, days}, or null for no
    bound) and strictAge are the age band as set. eligibilityReferenceDayUtc
    is the day ages are counted on, and dobOnOrAfterUtc / dobOnOrBeforeUtc
    are the dates of birth the band comes to on that day, both inclusive."""


def age_help(command):
    """Fill {AGE_BAND_HELP} / {AGE_WINDOW_HELP} in a command's docstring.

    Goes directly above the `def`, so the text is in place before click reads it.
    """
    command.__doc__ = (
        command.__doc__.replace("{AGE_BAND_HELP}", AGE_BAND_HELP).replace("{AGE_WINDOW_HELP}", AGE_WINDOW_HELP)
    )
    return command


def convert_age_band(data: dict[str, Any]) -> dict[str, Any]:
    """Refuse the removed date-of-birth bounds; send a bare number of years as an age."""
    for field in REMOVED_DOB_FIELDS:
        if field in data:
            raise click.ClickException(
                f"{field!r} is removed: eligibility is an age band. Use minAge / maxAge "
                "/ strictAge (see --help)."
            )
    result = dict(data)
    for field in AGE_FIELDS:
        value = result.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            result[field] = {"years": value}
    return result


def parse_json_input(json_input: str) -> dict[str, Any]:
    """Parse JSON from file path, stdin (-), or inline string."""
    if json_input == "-":
        return json.load(sys.stdin)
    if json_input.endswith(".json") and Path(json_input).exists():
        with open(json_input) as f:
            return json.load(f)
    try:
        return json.loads(json_input)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON: {e}")


def _parse_preference_value(json_value: str | None, file: str | None) -> Any:
    """The value to store, from --json (any JSON literal) or --file."""
    if (json_value is None) == (file is None):
        raise click.UsageError("Pass exactly one of --json '<value>' or --file <path>.")
    if file is not None:
        with open(file) as f:
            return json.load(f)
    try:
        return json.loads(json_value)  # type: ignore[arg-type]
    except json.JSONDecodeError as e:
        raise click.UsageError(f"--json must be a JSON value (quote a string as '\"text\"'): {e}")


def get_tokens(base_url: str, username: str | None, password: str) -> tuple[str, str | None]:
    """Authenticate and return the (access, refresh) token pair."""
    if not username:
        raise click.UsageError(
            "This command requires authentication. Pass --user / -u <username>."
        )
    response = httpx.post(
        f"{base_url}/v1/auth/login",
        json={"username": username, "password": password},
    )
    if response.status_code != 200:
        raise click.ClickException(f"Login failed: {response.text}")
    data = response.json()
    return data["accessToken"], data.get("refreshToken")


def explain_stale_version(data: Any) -> str | None:
    """Turn a 409 STALE_VERSION detail into one line an operator can act on.

    The detail carries the record's current `version`, `updatedAt` and
    `updatedBy` — an event's (club_server#292) or an occurrence's
    (club_server#430); the fix is to reload, or to retry with
    `--version <current>` once the other change has been reviewed.
    """
    detail = data.get("detail") if isinstance(data, dict) else None
    if not isinstance(detail, dict) or detail.get("code") != "STALE_VERSION":
        return None
    when = detail.get("updatedAt")
    when_local = utc_ms_to_local_str(when) if isinstance(when, int) else str(when)
    return (
        f"STALE_VERSION: it is at version {detail.get('version')}, "
        f"changed at {when_local} by {detail.get('updatedBy')}. "
        f"Reload it, or retry with --version {detail.get('version')} to overwrite."
    )


# One line an operator can act on, for refusals whose JSON alone does not say
# what to do next. Printed on stderr after the response itself.
ERROR_HINTS = {
    "EVENT_ALREADY_STARTED": (
        "it has started, so its window can no longer move. Fix its timetable with "
        "`event update <id> '{\"sessions\": [...]}'`, or move single occurrences "
        "with `occurrences reschedule`."
    ),
    "EVENT_TYPE_NOT_SUPPORTED": (
        "this change does not apply to this event type; `event update` picks the "
        "right endpoint for each type."
    ),
    "OCCURRENCE_OVERRIDES_PRESENT": (
        "some occurrences were changed one by one; pass --reset-overrides to "
        "discard those changes and reschedule."
    ),
    "SCHEDULE_NOT_FOUND": "that schedule is not one of this event's; list them with `event schedules <id>`.",
    "INVALID_SESSIONS_TOTAL": "the sessions do not add up to the schedule's occurrence length.",
    "IDENTITY_DOCUMENT_REQUIRED": (
        "upload one first: `me gallery add-file <file> --tag identity_document`."
    ),
    "CREDIT_SYSTEM_DISABLED": "this deployment does not run the credit system (see `capabilities`).",
    "EVALUATIONS_DISABLED": "this deployment does not ship evaluations (see `capabilities`).",
    "EVENT_MARKETING_DISABLED": "this deployment does not ship event marketing (see `capabilities`).",
    "INVALID_ANSWER": (
        "the answer does not fit its question; `evaluations templates get <template>` "
        "shows each item's type, scale and choices."
    ),
    "INCOMPLETE": (
        "required answers, or coach notes their answers require, are missing; "
        "add them with `evaluations answers put`, then save again."
    ),
    "INVALID_LAYOUT": (
        "a layout names each of the template's items exactly once; "
        "`evaluations templates get <template>` lists them."
    ),
    "ITEM_TYPE_FIXED": "an item keeps its type; remove it and add a new item instead.",
    "ITEM_NOT_FOUND": (
        "no such item on this template (or no such originItemId); "
        "`evaluations templates get <template>` lists its items."
    ),
    "ORIGIN_MISMATCH": (
        "a copy keeps its origin's type and answers; drop originItemId to add it as a new question."
    ),
    "INVALID_EVIDENCE": (
        "evidence is an image, video or PDF, tagged with the id of a question that allows evidence."
    ),
    "TEMPLATE_NAME_TAKEN": (
        "a live template already has that name (case and outer spaces ignored); "
        "pick another, or see `evaluations templates list`."
    ),
    "DUPLICATE_EVALUATION": (
        "that coach already has a live review of this member on this template over the same "
        "period (or with no period); change the period, or use the existing one."
    ),
    "PERIOD_IN_FUTURE": "a review period looks back: end it no later than now.",
}


def explain_error(data: Any) -> str | None:
    """A known refusal as one actionable line, or None."""
    stale = explain_stale_version(data)
    if stale:
        return stale
    detail = data.get("detail") if isinstance(data, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    hint = ERROR_HINTS.get(code) if isinstance(code, str) else None
    if not hint:
        return None
    item_ids = (detail.get("details") or {}).get("itemIds") if code == "INCOMPLETE" else None
    if item_ids:
        hint += f" Items: {', '.join(str(i) for i in item_ids)}."
    return f"{code}: {hint}"


# Shared by the commands that upload a file (club_server#18).
OWNER_OPTION_HELP = (
    "Upload on behalf of this user. Admins only; anyone else may name only "
    "themselves. The file is recorded as theirs: they are who `self` means "
    "in its access roles, they may change or delete it, and it shows in "
    "their own file listing."
)


def _echo_media_type(media: dict) -> None:
    """Say what the uploaded file actually is, on stderr (club_server#424).

    A uuid tells a reader nothing, and the type used to be invisible in CLI
    output — so seeding a video and seeding a photo looked identical.

    Both types are shown when the server converted the file: what it stores
    and serves, and what was sent. Stderr, because `uploads add-file` prints
    one URL on stdout and scripts read it.
    """
    stored = media.get("mimeType")
    if not stored:
        return
    uploaded = media.get("originalMimeType")
    line = f"  {stored}" if media.get("id") is None else f"  id {media['id']}: {stored}"
    if uploaded and uploaded != stored:
        line += f" (uploaded as {uploaded})"
    click.echo(line, err=True)


def print_response(response: httpx.Response) -> None:
    """Print response as formatted JSON."""
    if response.status_code == 204:
        click.echo("Success (no content)")
        return
    try:
        data = response.json()
        click.echo(json.dumps(data, indent=2))
        hint = explain_error(data)
        if hint:
            click.echo(hint, err=True)
    except json.JSONDecodeError:
        click.echo(response.text)
    if response.status_code >= 400:
        sys.exit(1)


from .media_api import MediaApi


class Context:
    """CLI context holding base URL and auth token (lazy loaded)."""

    base_url: str
    username: str | None
    password: str | None
    upload: MediaApi

    def __init__(
        self,
        base_url: str,
        username: str | None,
        password: str | None,
    ):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.upload = MediaApi()
        self._token: str | None = None
        self._refresh_token: str | None = None

    @property
    def token(self) -> str:
        if self._token is None:
            if not self.password:
                raise click.UsageError(
                    "This command requires authentication. Pass --pw / --password."
                )
            self._token, self._refresh_token = get_tokens(self.base_url, self.username, self.password)
        return self._token

    @property
    def refresh_token(self) -> str | None:
        """The refresh token from this run's login; None when the run is not logged in."""
        if not self.password:
            return None
        self.token  # logs in if not yet done
        return self._refresh_token

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def optional_headers(self) -> dict[str, str]:
        """Auth when credentials were given, none otherwise (public reads)."""
        return self.headers if self.password else {}


pass_context = click.make_pass_decorator(Context)


@click.group()
@click.option("--user", "-u", default=None, help="Username for authentication (required for authenticated commands)")
@click.option("--password", "--pw", default=None, help="Password for authentication (omit for public endpoints like 'auth register')")
@click.option("--base-url", default="http://127.0.0.1:8001", help="API base URL")
@click.pass_context
def main(
    ctx: click.Context,
    user: str | None,
    password: str | None,
    base_url: str,
) -> None:
    """Club server CLI client for developers."""
    ctx.obj = Context(base_url, user, password)


@main.command("capabilities")
@pass_context
def capabilities(ctx: Context):
    """What this deployment can do: {creditSystem, evaluations, eventMarketing, identityVerification, defaultCountryCode}.

    Anyone may ask, before login too. The optional modules' routes exist on
    every deployment and answer 503 while off, so this is the way to find out.
    identityVerification false means registration lands in pending, with no
    identity document to upload before review.
    defaultCountryCode is the club's country calling code, one to three
    digits without "+" (e.g. "91"): the club apps put it in front of a phone
    number typed without one. It is null when the deployment sets none.

    Example: capabilities
    """
    response = httpx.get(f"{ctx.base_url}/v1/capabilities", headers=ctx.optional_headers)
    print_response(response)


@main.group()
def venues():
    """Venue management commands (plural for list/create)."""
    pass


@main.group()
def venue():
    """Single venue commands (singular for delete)."""
    pass


@main.group()
def users():
    """User management commands (plural for list/create)."""
    pass


@main.group()
def user():
    """Single user commands (singular for delete/approve/roles)."""
    pass


@main.group()
def me():
    """Self-service commands for the authenticated user (/users/me/...)."""
    pass


@me.command("submit-for-review")
@pass_context
def me_submit_for_review(ctx: Context):
    """Submit your account for admin review (registered → pending).

    Example: --user alice --pw s3cret me submit-for-review
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/users/me/submit-for-review",
        headers=ctx.headers,
    )
    print_response(response)


@me.command("update")
@click.argument("json_input")
@pass_context
def me_update(ctx: Context, json_input: str):
    """Update your own profile (PATCH /v1/users/by_id/<self>).

    Example: --user alice --pw s3cret me update profile.json
    """
    data = parse_json_input(json_input)
    data = convert_timestamps(data)
    response = httpx.patch(
        f"{ctx.base_url}/v1/users/by_id/{ctx.username}",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@me.command("reapply")
@click.argument("json_input")
@pass_context
def me_reapply(ctx: Context, json_input: str):
    """Resubmit registration fields after an admin reconsider (self only).

    After calling this, run 'me submit-for-review' to re-notify admins.

    Example: --user alice --pw s3cret me reapply patch.json
    """
    data = parse_json_input(json_input)
    data = convert_timestamps(data)
    response = httpx.patch(
        f"{ctx.base_url}/v1/users/by_id/{ctx.username}/reapply",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@me.group("gallery")
def me_gallery():
    """Manage your own gallery items (documents)."""
    pass


@me_gallery.command("list")
@pass_context
def me_gallery_list(ctx: Context):
    """List all gallery items belonging to you."""
    response = ctx.upload.list_user_gallery(ctx.base_url, ctx.headers, ctx.username)
    print_response(response)


# An identity document is personal data: the app uploads it readable by the
# user and admins only, and encrypted at rest (#42). Seeded ones match.
_IDENTITY_DOCUMENT_FORM = {"accessRoles": json.dumps(["self", "admin"]), "encrypt": "true"}


@me_gallery.command("add-file")
@click.argument("filepath")
@click.option("--tag", required=True, help="Gallery tag, e.g. identity_document")
@pass_context
def me_gallery_add_file(ctx: Context, filepath: str, tag: str):
    """Upload a local file as yourself and attach it to your gallery.

    Two-step: upload bytes → link to your gallery by uuid.

    An `identity_document` is uploaded as the app does it: readable by you
    and admins only, and encrypted. The server must have an encryption key.

    Example: me gallery add-file ./id.png --tag identity_document
    """
    form = _IDENTITY_DOCUMENT_FORM if tag == "identity_document" else None
    up = ctx.upload.add_file(ctx.base_url, ctx.headers, filepath, form=form)
    if up.status_code not in (200, 201):
        print_response(up)
        return
    uuid = up.json().get("uuid")
    if not uuid:
        raise click.ClickException(f"Upload response missing uuid: {up.text[:200]}")
    media = up.json()
    download_url = ctx.upload.download_url(ctx.base_url, uuid, media.get("filename"))
    click.echo(f"Uploaded {Path(filepath).name} → {download_url}")
    _echo_media_type(media)
    gal = ctx.upload.link_to_user_gallery(
        ctx.base_url, ctx.headers, ctx.username, tag=tag, uuid=uuid,
    )
    print_response(gal)


@me_gallery.command("remove")
@click.option("--tag", required=True, help="Gallery tag")
@click.option("--id", "item_id", required=True, help="Gallery item id (uuid)")
@pass_context
def me_gallery_remove(ctx: Context, tag: str, item_id: str):
    """Remove a gallery item by tag + id.

    Example: me gallery remove --tag id_proof --id <uuid>
    """
    response = ctx.upload.remove_from_user_gallery(
        ctx.base_url, ctx.headers, ctx.username, tag=tag, item_id=item_id,
    )
    print_response(response)


@main.group()
def media():
    """Media-at-rest operations."""
    pass


def _collect_unencrypted(
    ctx: Context, *, tag: str | None, limit: int
) -> list[dict[str, Any]]:
    """Page through GET /v1/media/links with isEncrypted=false.

    Returns the link rows (a media linked under several owners/tags appears
    once per link); callers that act per-media should de-dup on ``mediaUuid``.
    """
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        r = ctx.upload.search_links(
            ctx.base_url, ctx.headers, tag=tag, is_encrypted=False,
            offset=offset, limit=limit,
        )
        if r.status_code != 200:
            raise click.ClickException(
                f"links search failed: {r.status_code} {r.text}"
            )
        body = r.json()
        page = body.get("items", [])
        items.extend(page)
        total = body.get("total", len(items))
        offset += limit
        if not page or offset >= total:
            break
    return items


@media.command("unencrypted")
@click.option(
    "--tag", default="identity_document", show_default=True,
    help="Link tag to scope by; pass --tag '' to list every tag.",
)
@click.option("--limit", default=100, show_default=True, help="Links page size.")
@pass_context
def media_unencrypted(ctx: Context, tag: str, limit: int):
    """List media links whose media is still plaintext (isEncrypted=false)."""
    items = _collect_unencrypted(ctx, tag=tag or None, limit=limit)
    click.echo(json.dumps(items, indent=2))
    click.echo(f"\n{len(items)} unencrypted link(s).", err=True)


@media.command("encrypt")
@click.argument("uuid")
@pass_context
def media_encrypt(ctx: Context, uuid: str):
    """Encrypt one existing media artifact in place (super-admin).

    POST /v1/media/by_id/<uuid>/encrypt — idempotent (a no-op 200 if the
    media is already encrypted).
    """
    response = ctx.upload.encrypt_media(ctx.base_url, ctx.headers, uuid)
    print_response(response)


@media.command("encrypt-existing")
@click.option(
    "--tag", default="identity_document", show_default=True,
    help="Link tag to scope by; pass --tag '' to sweep every tag.",
)
@click.option(
    "--dry-run", is_flag=True, help="List the candidates without encrypting.",
)
@click.option("--limit", default=100, show_default=True, help="Links page size.")
@pass_context
def media_encrypt_existing(ctx: Context, tag: str, dry_run: bool, limit: int):
    """Sweep: encrypt every still-plaintext media (idempotent, restartable).

    Enumerates unencrypted media via the links search, de-duplicates on
    mediaUuid, and POSTs the encrypt action for each. Safe to re-run — the
    endpoint is a no-op on already-encrypted rows.
    """
    items = _collect_unencrypted(ctx, tag=tag or None, limit=limit)
    uuids: list[str] = []
    seen: set[str] = set()
    for it in items:
        u = it.get("mediaUuid")
        if u and u not in seen:
            seen.add(u)
            uuids.append(u)

    if not uuids:
        click.echo("Nothing to encrypt — no plaintext media found.")
        return

    if dry_run:
        for u in uuids:
            click.echo(f"[dry-run] would encrypt {u}")
        click.echo(f"\n{len(uuids)} candidate(s).")
        return

    encrypted = 0
    failed = 0
    for u in uuids:
        r = ctx.upload.encrypt_media(ctx.base_url, ctx.headers, u)
        if r.status_code == 200:
            click.echo(f"encrypted {u}")
            encrypted += 1
        else:
            click.echo(f"FAIL {u}: {r.status_code} {r.text}", err=True)
            failed += 1
            # No KEK on the server → every call will fail; stop early.
            if r.status_code == 503:
                raise click.ClickException(
                    "Encryption not configured on the server (503); aborting sweep."
                )
    click.echo(
        f"\nencrypted={encrypted} failed={failed} (of {len(uuids)} unique media)"
    )


@main.group()
def events():
    """Event management commands (plural for list/create)."""
    pass


@main.group()
def event():
    """Single event commands (singular for delete/update)."""
    pass


@venues.command("create")
@click.argument("json_input")
@pass_context
def venues_create(ctx: Context, json_input: str):
    """Create a new venue from JSON file or inline JSON."""
    data = parse_json_input(json_input)
    response = httpx.post(
        f"{ctx.base_url}/v1/venues",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@venues.command("list")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def venues_list(ctx: Context, offset: int, limit: int):
    """List all venues."""
    response = httpx.get(
        f"{ctx.base_url}/v1/venues",
        params={"offset": offset, "limit": limit},
        headers=ctx.headers,
    )
    print_response(response)


@venue.command("get")
@click.argument("venue_id", type=int)
@pass_context
def venue_get(ctx: Context, venue_id: int):
    """Get venue details by ID."""
    response = httpx.get(
        f"{ctx.base_url}/v1/venues/by_id/{venue_id}",
        headers=ctx.headers,
    )
    print_response(response)


@venue.command("delete")
@click.argument("venue_id", type=int)
@pass_context
def venue_delete(ctx: Context, venue_id: int):
    """Delete a venue by ID."""
    response = httpx.delete(
        f"{ctx.base_url}/v1/venues/by_id/{venue_id}",
        headers=ctx.headers,
    )
    print_response(response)


@venue.command("update")
@click.argument("venue_id", type=int)
@click.argument("json_input")
@pass_context
def venue_update(ctx: Context, venue_id: int, json_input: str):
    """Update a venue from JSON file or inline JSON."""
    data = parse_json_input(json_input)
    response = httpx.patch(
        f"{ctx.base_url}/v1/venues/by_id/{venue_id}",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# User Commands
# ============================================================================


# A user seed file carries, beside the RegisterRequest fields, what the seed
# recipes apply after registration. The server refuses unknown body fields
# (club_server#325), so registration must send none of these.
SEED_ONLY_KEYS = frozenset({
    "profile",            # -> `me update` (just me_update)
    "identity-document",  # -> `me gallery add-file` (just me_identity_upload)
    "staffListing",       # -> `staff set` {position, guest, hidden} (just staff_set)
})


def split_seed_keys(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a seed into the registration body and the follow-up blocks."""
    follow_ups = {k: data.pop(k) for k in list(data) if k in SEED_ONLY_KEYS}
    return data, follow_ups


def register_user(ctx: Context, json_input: str) -> None:
    """POST /v1/auth/register with the seed's registration fields only."""
    data = convert_timestamps(parse_json_input(json_input))
    user_data, _ = split_seed_keys(data)
    response = httpx.post(
        f"{ctx.base_url}/v1/auth/register",
        json=user_data,
    )
    print_response(response)


@users.command("register")
@click.argument("json_input")
@pass_context
def users_register(ctx: Context, json_input: str) -> None:
    """Register a new user from JSON file or inline JSON.

    Thin wrapper over POST /v1/auth/register, equivalent to `auth register`,
    kept here for discoverability under the `users` group. A seed file's
    seed-only blocks are stripped here and applied by the follow-up commands,
    which the justfile's `member` / `coach` recipes chain:

    \b
      identity-document -> me gallery add-file <file> --tag identity_document
                           me submit-for-review, then user approve <username>
      profile           -> me update <profile-json>
      staffListing      -> staff set <username> --position N [--guest] [--hidden]
      roles             -> user add-role <username> coach|admin
    """
    register_user(ctx, json_input)


@users.command("create")
@click.argument("json_input")
@click.option("--guest", is_flag=True, help="A guest coach: created active with the coach role and a public profile, withheld from the public listing unless asked for")
@pass_context
def users_create(ctx: Context, json_input: str, guest: bool) -> None:
    """Create a user as an admin (POST /v1/users): active at once, no onboarding.

    Takes the same seed shape as `users register`; the `profile` block is
    merged into the body since the admin route accepts those fields directly,
    and identity-document / staffListing are left to the follow-ups
    (`just staff_set`). With --guest the server sets isGuest, the coach role
    and a public profile on the admin's authority.

    Example: users create seeds/users/guest_coach.json --guest
    """
    data = convert_timestamps(parse_json_input(json_input))
    user_data, follow_ups = split_seed_keys(data)
    user_data.update(follow_ups.get("profile") or {})
    if "password" in user_data:
        # The admin route names the field passwordHash; the server hashes it.
        user_data["passwordHash"] = user_data.pop("password")
    if guest:
        user_data["isGuest"] = True
    response = httpx.post(f"{ctx.base_url}/v1/users", json=user_data, headers=ctx.headers)
    print_response(response)


@users.command("list")
@click.option("--status", "status_filter", help="Filter by status: registered, pending, active, blocked, left")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def users_list(ctx: Context, status_filter: str | None, offset: int, limit: int) -> None:
    """List all users (admin only)."""
    params: dict[str, str | int] = {"offset": offset, "limit": limit}
    if status_filter:
        params["status"] = status_filter
    response = httpx.get(
        f"{ctx.base_url}/v1/users",
        params=params,
        headers=ctx.headers,
    )
    print_response(response)


@users.command("count")
@pass_context
def users_count(ctx: Context) -> None:
    """Live user counts by status (admin/coach): {byStatus: {registered, pending, active, blocked, left}, total}.

    Soft-deleted users and super admins are counted nowhere; total includes
    the registered bucket that `users list` omits by default.
    """
    response = httpx.get(f"{ctx.base_url}/v1/users/count", headers=ctx.headers)
    print_response(response)


@user.command("get")
@click.argument("username")
@click.option("--public-only", is_flag=True, help="Get public profile only")
@pass_context
def user_get(ctx: Context, username: str, public_only: bool):
    """Get user details by username.

    Example: user get coach_member
    Example: user get coach_member --public-only
    """
    endpoint = f"{ctx.base_url}/v1/users/by_id/{username}"
    if not public_only:
        endpoint += "/private"
    response = httpx.get(endpoint, headers=ctx.headers)
    print_response(response)


@user.command("update")
@click.argument("username")
@click.argument("json_input")
@pass_context
def user_update(ctx: Context, username: str, json_input: str):
    """Update a user profile from JSON file or inline JSON."""
    data = parse_json_input(json_input)
    response = httpx.patch(
        f"{ctx.base_url}/v1/users/by_id/{username}",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@user.command("block")
@click.argument("username")
@pass_context
def user_block(ctx: Context, username: str):
    """Block a user."""
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/block",
        headers=ctx.headers,
    )
    print_response(response)


@user.command("delete")
@click.argument("username")
@pass_context
def user_delete(ctx: Context, username: str):
    """Soft delete a user."""
    response = httpx.delete(
        f"{ctx.base_url}/v1/users/by_id/{username}",
        headers=ctx.headers,
    )
    print_response(response)


@user.command("approve")
@click.argument("username")
@pass_context
def user_approve(ctx: Context, username: str):
    """Approve a pending user (admin, pending → active)."""
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/approve",
        headers=ctx.headers,
    )
    print_response(response)


@user.command("reconsider")
@click.argument("username")
@click.option("--reason", required=True, help="Reason shown to the user (required)")
@pass_context
def user_reconsider(ctx: Context, username: str, reason: str):
    """Send a pending user back to 'registered' for corrections (admin).

    Example: user reconsider alice --reason "Missing identity document"
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/reconsider",
        json={"reason": reason},
        headers=ctx.headers,
    )
    print_response(response)


@user.command("unblock")
@click.argument("username")
@pass_context
def user_unblock(ctx: Context, username: str):
    """Unblock a blocked user."""
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/unblock",
        headers=ctx.headers,
    )
    print_response(response)


@user.command("mark-left")
@click.argument("username")
@pass_context
def user_mark_left(ctx: Context, username: str):
    """Mark a user as left."""
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/mark-left",
        headers=ctx.headers,
    )
    print_response(response)


@user.command("reactivate")
@click.argument("username")
@pass_context
def user_reactivate(ctx: Context, username: str):
    """Reactivate a user who left."""
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/reactivate",
        headers=ctx.headers,
    )
    print_response(response)


# Super admin is a flag moved only by transfer-superadmin, never a role (club_server#514).
ROLE_CHOICE = click.Choice(["admin", "coach"])


@user.command("add-role")
@click.argument("username")
@click.argument("role", type=ROLE_CHOICE)
@pass_context
def user_add_role(ctx: Context, username: str, role: str):
    """Assign a role to a user: admin or coach.

    There is no member role; any registered user may be enrolled. Super
    admin is not a role: hand it over with `user transfer-superadmin`.
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/roles",
        json={"role": role},
        headers=ctx.headers,
    )
    print_response(response)


@user.command("remove-role")
@click.argument("username")
@click.argument("role", type=ROLE_CHOICE)
@pass_context
def user_remove_role(ctx: Context, username: str, role: str):
    """Remove a role (admin or coach) from a user."""
    response = httpx.delete(
        f"{ctx.base_url}/v1/users/by_id/{username}/roles/{role}",
        headers=ctx.headers,
    )
    print_response(response)


@user.command("groups")
@click.argument("username")
@pass_context
def user_groups(ctx: Context, username: str):
    """List groups a user belongs to.

    Example: user groups coach_member
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/users/by_id/{username}/groups",
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# Event Commands
# ============================================================================


@events.command("create")
@click.argument("json_input")
@pass_context
@age_help
def events_create(ctx: Context, json_input: str):
    """Create a new event from JSON file or inline JSON.

    EventCreate now carries: title, description, type, visibility, venueId,
    organizerName, coachNames, startTime, endTime, rrule, untilTime, gender,
    minAge, maxAge, strictAge, isFeatured, imageUri, galleryUris, sessions.
    Legacy auxInfo is no longer supported.

    {AGE_BAND_HELP}

    Example: events create '{"title": "U12 Camp", "type": "camp", "venueId": 1, "startTime": "2026-07-01T06:00:00", "endTime": "2026-07-01T08:00:00", "minAge": 10, "maxAge": {"years": 12, "months": 6}, "strictAge": true}'
    """
    data = parse_json_input(json_input)
    if "auxInfo" in data:
        raise click.ClickException(
            "auxInfo is removed; move imageUri/isFeatured/galleryUris/sessions/gender/minAge/maxAge/strictAge to top-level event fields."
        )
    data = convert_timestamps(convert_age_band(data))
    response = httpx.post(
        f"{ctx.base_url}/v1/events",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@events.command("list")
@click.option("--type", "event_type", help="Filter by event type: oneOff, programme, camp")
@click.option("--visibility", help="Filter by visibility: public, private")
@click.option("--organizer", help="Filter by organizer username")
@click.option("--include-past", is_flag=True, help="Include past events")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
@age_help
def events_list(
    ctx: Context,
    event_type: str | None,
    visibility: str | None,
    organizer: str | None,
    include_past: bool,
    offset: int,
    limit: int,
):
    """List events with optional filters.

    {AGE_WINDOW_HELP}
    The reference day is the day a camp or one-off starts, and a programme's
    next occurrence that is not cancelled (today when it has none).
    """
    params: dict[str, str | int | bool] = {"offset": offset, "limit": limit, "includePast": include_past}
    if event_type:
        params["type"] = event_type
    if visibility:
        params["visibility"] = visibility
    if organizer:
        params["organizerName"] = organizer
    response = httpx.get(
        f"{ctx.base_url}/v1/events",
        params=params,
        headers=ctx.headers,
    )
    print_response(response)


@events.command("check-conflict")
@click.argument("json_input")
@pass_context
def events_check_conflict(ctx: Context, json_input: str):
    """Report scheduling overlaps for a planned event of any type.

    Required: type, venueId, startTime, endTime.
    Optional: rrule, untilTime, organizerName, coachNames, excludeEventId.

    Returns {venueConflicts, organizerConflicts, coachConflicts} with
    per-occurrence overlap pairs. Only a programme-against-programme clash
    blocks creation (409); every other overlap is reported to the admins and
    creation proceeds, so this is how to see them before creating.

    Example: events check-conflict '{"type":"camp","venueId":2226,"startTime":"2026-05-01T06:00:00","endTime":"2026-05-01T08:00:00","rrule":"FREQ=DAILY;COUNT=5"}'
    """
    data = convert_timestamps(parse_json_input(json_input))
    response = httpx.post(
        f"{ctx.base_url}/v1/events/check-conflict",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@event.command("check-user-conflicts")
@click.argument("event_id", type=int)
@click.argument("usernames", nargs=-1, required=True)
@pass_context
def event_check_user_conflicts(ctx: Context, event_id: int, usernames: tuple[str, ...]):
    """Report each user's enrollment overlaps against an event of any type (admin/coach).

    Only a programme-against-programme overlap blocks a join (409
    TIME_CONFLICT); the rest is a report for the admin to weigh.

    Example: event check-user-conflicts 6034 user_a user_b
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/check-user-conflicts",
        json={"usernames": list(usernames)},
        headers=ctx.headers,
    )
    print_response(response)


@event.command("get")
@click.argument("event_id", type=int)
@pass_context
@age_help
def event_get(ctx: Context, event_id: int):
    """Get event details by ID.

    Response includes gender, isFeatured, imageUri, galleryUris and sessions
    inline (the aux-info endpoint is gone).

    {AGE_WINDOW_HELP}
    The reference day is the day a camp or one-off starts, and a programme's
    next occurrence that is not cancelled (today when it has none), so a
    programme's window moves forward.
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}",
        headers=ctx.headers,
    )
    print_response(response)


@event.command("delete")
@click.argument("event_id", type=int)
@pass_context
def event_delete(ctx: Context, event_id: int):
    """Soft delete an event by ID."""
    response = httpx.delete(
        f"{ctx.base_url}/v1/events/by_id/{event_id}",
        headers=ctx.headers,
    )
    print_response(response)


# Which fields of an event edit are a change of timetable (a programme split,
# PATCH .../future) and which are a correction of what the event *is*
# (PATCH .../correction). The server refuses either set on the other endpoint.
SCHEDULE_FIELDS = frozenset(
    {"venueId", "organizerName", "coachNames", "startTimeUtc", "endTimeUtc", "rrule", "sessions"}
)
# Of the schedule fields, the ones a direct PATCH (camp / one-off) still accepts.
# sessions is one of them at any time, even once started (club_server#423).
DIRECT_PATCH_SCHEDULE_FIELDS = frozenset({"organizerName", "coachNames", "sessions"})
# A camp or one-off's window, moved by POST .../reschedule before it starts.
RESCHEDULE_FIELDS = frozenset({"startTimeUtc", "endTimeUtc", "rrule", "venueId"})


def fetch_event(ctx: Context, event_id: int) -> dict[str, Any]:
    """GET the event, or print the failure and exit."""
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}",
        headers=ctx.headers,
    )
    if response.status_code != 200:
        print_response(response)
        sys.exit(1)
    return response.json()


def resolve_version(ctx: Context, event_id: int, version: int | None) -> int:
    """The version to send: the override, else the event's current one."""
    if version is not None:
        return version
    return int(fetch_event(ctx, event_id)["version"])


def patch_event(ctx: Context, event_id: int, suffix: str, data: dict[str, Any]) -> httpx.Response:
    return httpx.patch(
        f"{ctx.base_url}/v1/events/by_id/{event_id}{suffix}",
        json=data,
        headers=ctx.headers,
    )


@event.command("update")
@click.argument("event_id", type=int)
@click.argument("json_input")
@click.option("--effective-time-local", help="Cutoff of a programme timetable change (local ISO or 'YYYYMMDD HHMM'); must be an occurrence start >= 30 min ahead.")
@click.option("--schedule-id", type=int, help="Programme sessions-only correction: the schedule to correct (default: the latest). Ids from `event schedules`.")
@click.option("--reset-overrides", is_flag=True, default=False, help="Camp / one-off reschedule: discard per-occurrence overrides that would otherwise block it.")
@click.option("--version", "version", type=int, help="Event version to send (default: read from the event).")
@pass_context
@age_help
def event_update(
    ctx: Context,
    event_id: int,
    json_input: str,
    effective_time_local: str | None,
    schedule_id: int | None,
    reset_overrides: bool,
    version: int | None,
):
    """Update an event from JSON file or inline JSON; the CLI picks the endpoint.

    Every edit, reschedule included, carries the event's version (read from
    the event unless --version is given); 409 STALE_VERSION means someone
    changed it first.

    \b
    Programme:
      metadata (title, description, visibility, gender, minAge, maxAge,
      strictAge, isFeatured, galleryUris, shortDescription, stamp,
      highlights, includes)                → PATCH .../correction
      sessions alone                       → PATCH .../correction, in place,
                                             no cutoff; --schedule-id picks
                                             the schedule (default latest)
      timetable (venueId, organizerName, coachNames, startTime, endTime,
      rrule; sessions with any of them, or
      with --effective-time-local)         → PATCH .../future, needs
                                             --effective-time-local
    Camp / one-off:
      metadata, organizerName, coachNames,
      sessions (even once started)         → PATCH .../{id}
      startTime, endTime, rrule, venueId
      (sessions travel with them)          → POST .../reschedule, only before
                                             it starts; --reset-overrides
    When both kinds are present the PATCH goes first and the second call
    follows it. If the second fails, the first has already been applied.

    {AGE_BAND_HELP}

    Example: event update 123 '{"title": "New Title"}'
    Example: event update 123 '{"minAge": 10, "maxAge": {"years": 12, "months": 6}, "strictAge": true}'
    Example: event update 123 '{"maxAge": null}'
    Example: event update 123 '{"coachNames": ["coach_a"]}' --effective-time-local "2026-06-01T06:00:00"
    Example: event update 123 '{"sessions": [...]}' --schedule-id 4
    Example: event update 123 '{"startTime": "2026-07-01T06:00:00", "endTime": "2026-07-05T08:00:00"}'
    """
    ev = fetch_event(ctx, event_id)
    event_type = ev.get("type")
    current_version = version if version is not None else int(ev["version"])

    data = parse_json_input(json_input)
    if "auxInfo" in data:
        raise click.ClickException(
            "auxInfo is removed; move imageUri/isFeatured/galleryUris/sessions/gender/minAge/maxAge/strictAge to top-level event fields."
        )
    data.pop("version", None)
    data = convert_timestamps(convert_age_band(data))
    if not data:
        raise click.ClickException("Nothing to update: the JSON has no event fields.")

    if event_type != "programme":
        update_camp_or_oneoff(ctx, event_id, event_type, data, current_version,
                              effective_time_local, schedule_id, reset_overrides)
        return

    if reset_overrides:
        raise click.UsageError("--reset-overrides is for a camp or one-off reschedule; a programme is split instead.")
    schedule = {k: v for k, v in data.items() if k in SCHEDULE_FIELDS}
    metadata = {k: v for k, v in data.items() if k not in SCHEDULE_FIELDS}
    # sessions alone is corrected in place (club_server#423) unless a cutoff
    # asks for it to change from then on.
    if set(schedule) == {"sessions"} and not effective_time_local:
        metadata["sessions"] = schedule.pop("sessions")
        if schedule_id is not None:
            metadata["scheduleId"] = schedule_id
    elif schedule_id is not None:
        raise click.UsageError(
            "--schedule-id goes with a sessions-only correction: pass sessions and no "
            "other timetable field, without --effective-time-local."
        )

    if schedule and not effective_time_local:
        raise click.ClickException(
            f"{sorted(schedule)} change the programme's timetable: pass "
            "--effective-time-local (an occurrence start at least 30 minutes ahead)."
        )
    if metadata:
        response = patch_event(ctx, event_id, "/correction", {**metadata, "version": current_version})
        print_response_or_exit(response, then="the split" if schedule else None)
        current_version = int(response.json()["version"])
    if schedule:
        cutoff = local_iso_to_utc_ms(parse_local_time(effective_time_local))
        print_response(patch_event(
            ctx, event_id, "/future",
            {**schedule, "version": current_version, "effectiveDateTimeUtc": cutoff},
        ))


def update_camp_or_oneoff(
    ctx: Context,
    event_id: int,
    event_type: str | None,
    data: dict[str, Any],
    version: int,
    effective_time_local: str | None,
    schedule_id: int | None,
    reset_overrides: bool,
) -> None:
    """A camp or one-off: PATCH what it accepts, /reschedule the window."""
    if effective_time_local:
        raise click.UsageError(
            f"--effective-time-local splits a programme; a {event_type} is rescheduled "
            "in place (send startTime / endTime / rrule / venueId)."
        )
    if schedule_id is not None:
        raise click.UsageError(f"--schedule-id corrects a programme's schedule; a {event_type} has one timetable.")
    moves = RESCHEDULE_FIELDS & set(data)
    reschedule = {k: data[k] for k in (*sorted(moves), "sessions") if k in data} if moves else {}
    direct = {k: v for k, v in data.items() if k not in reschedule}
    if reset_overrides and not reschedule:
        raise click.UsageError(
            "--reset-overrides goes with a reschedule: include startTime, endTime, rrule or venueId."
        )

    if direct:
        response = patch_event(ctx, event_id, "", {**direct, "version": version})
        print_response_or_exit(response, then="the reschedule" if reschedule else None)
        if reschedule:
            version = int(response.json()["version"])
    if reschedule:
        # Versioned like the PATCHes (club_server#434): a reschedule rewrites
        # the window another editor may be looking at.
        reschedule["version"] = version
        if reset_overrides:
            reschedule["resetOverrides"] = True
        print_response(httpx.post(
            f"{ctx.base_url}/v1/events/by_id/{event_id}/reschedule",
            json=reschedule,
            headers=ctx.headers,
        ))


def print_response_or_exit(response: httpx.Response, then: str | None) -> None:
    """The first of up to two calls. Printed alone; followed by a second, only on failure.

    When a second call follows, its response is the event as it now stands and
    is what gets printed, so stdout stays one JSON document a script can read.
    """
    if then is None:
        print_response(response)
    elif response.status_code >= 400:
        click.echo(f"Stopped: {then} was not attempted.", err=True)
        print_response(response)


def post_event_verb(ctx: Context, event_id: int, verb: str, data: dict[str, Any] | None = None) -> None:
    """POST a lifecycle verb on an event and print the result."""
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/{verb}",
        json=data if data is not None else {},
        headers=ctx.headers,
    )
    print_response(response)


def cutoff_ms(value: str) -> int:
    """A cutoff given as local ISO or 'YYYYMMDD HHMM', as UTC milliseconds."""
    return local_iso_to_utc_ms(parse_local_time(value))


@event.command("cancel")
@click.argument("event_id", type=int)
@click.argument("reason")
@click.option("--effective-time", type=int, help="Cutoff as a UTC ms timestamp")
@click.option("--effective-time-local", help="Cutoff (local ISO or 'YYYYMMDD HHMM'); occurrences at or after it are cancelled")
@pass_context
def event_cancel(ctx: Context, event_id: int, reason: str, effective_time: int | None, effective_time_local: str | None):
    """Cancel a camp from a cutoff (camp-only).

    Occurrences at or after the cutoff are cancelled and their attendance
    refunded; `event undo-cancel` reverses it. A programme answers 400
    INVALID_EVENT_TYPE (use `event terminate`); a one-off answers 422
    INVALID_STATE (use `event drop`).

    Example: event cancel 123 "Rink closed" --effective-time-local "2026-04-01T06:00:00"
    """
    if effective_time_local:
        resolved = cutoff_ms(effective_time_local)
    elif effective_time is not None:
        resolved = effective_time
    else:
        raise click.UsageError("A cutoff is required: pass --effective-time-local (or --effective-time).")
    post_event_verb(ctx, event_id, "cancel", {"reason": reason, "effectiveDateTimeUtc": resolved})


@event.command("undo-cancel")
@click.argument("event_id", type=int)
@pass_context
def event_undo_cancel(ctx: Context, event_id: int):
    """Reverse a camp cancellation (camp-only).

    Example: event undo-cancel 123
    """
    post_event_verb(ctx, event_id, "undo-cancel")


@event.command("terminate")
@click.argument("event_id", type=int)
@click.option("--cutoff-local", required=True, help="Cutoff (local ISO or 'YYYYMMDD HHMM'): an occurrence start at least 30 min ahead")
@click.option("--reason", required=True, help="Told to the members (event.terminated)")
@pass_context
def event_terminate(ctx: Context, event_id: int, cutoff_local: str, reason: str):
    """End a programme at a cutoff (programme-only).

    Occurrences from the cutoff on are cancelled; members are notified; once
    the cutoff has passed their bound credit is released to a general account.
    422 INVALID_STATE once the cutoff has passed; 400 EFFECTIVE_TIME_NOT_SESSION_BOUNDARY
    / 422 CUTOFF_TOO_SOON for a cutoff that is not a session start >= 30 min ahead.

    Example: event terminate 123 --cutoff-local "2026-06-01T06:00:00" --reason "Season over"
    """
    post_event_verb(ctx, event_id, "terminate", {"reason": reason, "cutoffTimeUtc": cutoff_ms(cutoff_local)})


@event.command("extend")
@click.argument("event_id", type=int)
@click.option("--cutoff-local", required=True, help="New cutoff (local ISO or 'YYYYMMDD HHMM'): an occurrence start at least 30 min ahead")
@click.option("--reason", help="Told to the members when the cutoff moves earlier (event.extended)")
@pass_context
def event_extend(ctx: Context, event_id: int, cutoff_local: str, reason: str | None):
    """Move a terminated programme's cutoff later or earlier (programme-only).

    Example: event extend 123 --cutoff-local "2026-07-01T06:00:00"
    """
    data: dict[str, Any] = {"cutoffTimeUtc": cutoff_ms(cutoff_local)}
    if reason:
        data["reason"] = reason
    post_event_verb(ctx, event_id, "extend", data)


@event.command("extend-indefinitely")
@click.argument("event_id", type=int)
@click.option("--reason", help="Optional note for the audit log")
@pass_context
def event_extend_indefinitely(ctx: Context, event_id: int, reason: str | None):
    """Remove a terminated programme's cutoff so it runs open-ended (programme-only).

    Example: event extend-indefinitely 123 --reason "Renewed"
    """
    post_event_verb(ctx, event_id, "extend-indefinitely", {"reason": reason} if reason else {})


def oneoff_occurrence_version(ctx: Context, event_id: int, version: int | None) -> int:
    """The version of a one-off's single occurrence: the override, else read.

    Drop and reinstate change that occurrence, so they carry its version, not
    the event's (club_server#430). The occurrence sits at the event's start.
    """
    if version is not None:
        return version
    slot = int(fetch_event(ctx, event_id)["startTimeUtc"])
    return int(fetch_occurrence(ctx, event_id, slot)["version"])


@event.command("drop")
@click.argument("event_id", type=int)
@click.option("--reason", required=True, help="Shown as cancelReason on the occurrence")
@click.option("--version", "version", type=int, help="Occurrence version to send (default: read from the occurrence).")
@pass_context
def event_drop(ctx: Context, event_id: int, reason: str, version: int | None):
    """Call off a one-off by cancelling its single occurrence (one-off only).

    Carries the occurrence's version (read unless --version is given);
    409 STALE_VERSION means someone changed the occurrence first.
    400 CANCELLATION_LEAD_TIME_VIOLATED inside the 30-minute lead time;
    422 PAST_OCCURRENCE once started; 422 CANCELLED_OCCURRENCE when already dropped.

    Example: event drop 123 --reason "No coach available"
    """
    post_event_verb(ctx, event_id, "drop", {
        "reason": reason, "version": oneoff_occurrence_version(ctx, event_id, version),
    })


@event.command("reinstate")
@click.argument("event_id", type=int)
@click.option("--version", "version", type=int, help="Occurrence version to send (default: read from the occurrence).")
@pass_context
def event_reinstate(ctx: Context, event_id: int, version: int | None):
    """Restore a dropped one-off while it has not started (422 INVALID_STATE afterwards).

    Carries the occurrence's version (read unless --version is given).

    Example: event reinstate 123
    """
    post_event_verb(ctx, event_id, "reinstate", {
        "version": oneoff_occurrence_version(ctx, event_id, version),
    })


@event.command("correct")
@click.argument("event_id", type=int)
@click.argument("json_input")
@click.option("--version", "version", type=int, help="Event version to send (default: read from the event).")
@pass_context
@age_help
def event_correct(ctx: Context, event_id: int, json_input: str, version: int | None):
    """Correct what a programme is, across all its occurrences (PATCH .../correction).

    Fields: title, description, visibility, gender, minAge, maxAge, strictAge,
    isFeatured, galleryUris, shortDescription, stamp, highlights, includes,
    and sessions with an optional scheduleId: the named schedule's timetable
    (default the latest) is replaced in place, at any time, and must fit that
    schedule's occurrence length (422 INVALID_SESSIONS_TOTAL; 404
    SCHEDULE_NOT_FOUND; 422 for scheduleId without sessions). Schedule ids
    come from `event schedules`.
    Coaching is a timetable change: use `event update ... --effective-time-local`.

    {AGE_BAND_HELP}

    Example: event correct 123 '{"title": "Updated Title"}'
    Example: event correct 123 '{"minAge": 8, "maxAge": null}'
    Example: event correct 123 '{"isFeatured": true, "gender": "female"}' --version 4
    Example: event correct 123 '{"sessions": [...], "scheduleId": 2}'
    """
    data = parse_json_input(json_input)
    if "coachNames" in data:
        raise click.ClickException(
            "coachNames is not a correction; change coaches with "
            "`event update <id> '{\"coachNames\": [...]}' --effective-time-local ...`."
        )
    data.pop("version", None)
    data = convert_timestamps(convert_age_band(data))
    data["version"] = resolve_version(ctx, event_id, version)
    print_response(patch_event(ctx, event_id, "/correction", data))


@event.command("schedules")
@click.argument("event_id", type=int)
@pass_context
def event_schedules(ctx: Context, event_id: int):
    """List an event's schedules in timeline order (admin/coach).

    Each split closes one schedule and opens the next on the same event;
    the last one's effectiveUntilUtc is the event's cutoff (null = open-ended).

    Example: event schedules 123
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/schedules",
        headers=ctx.headers,
    )
    print_response(response)


@event.command("credits")
@click.argument("event_id", type=int)
@click.option(
    "--state",
    type=click.Choice(["blocked", "expiringSoon"]),
    help="blocked: no usable credit. expiringSoon: next expiry at or before --expiring-before-local "
    "(which it needs). The server knows no other state and would return the whole roster.",
)
@click.option("--expiring-before-local", help="Cut-off for --state expiringSoon, local time")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def event_credits(ctx: Context, event_id: int, state: str | None, expiring_before_local: str | None, offset: int, limit: int):
    """Per-member credit standing on a programme (admin/coach; credit system only).

    Items: {membername, usableCredits, boundCredits, payingAccountId, blocked,
    nextExpiryUtc}. boundCredits is the unsettled credit bound to this
    programme, expired accounts included: when it is non-zero, `enrollment remove`
    and `enrollment approve-withdraw` for that member need --credit-disposition.

    The roster is everyone enrolled, members with a pending withdrawal
    (withdrawRequested) included: they stay chargeable until it is approved.

    Example: event credits 123 --state blocked
    """
    params: dict[str, Any] = {}
    if state:
        params["state"] = state
    if expiring_before_local:
        params["expiringBeforeUtc"] = local_iso_to_utc_ms(expiring_before_local)
    params.update(offset=offset, limit=limit)
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/credits",
        params=params,
        headers=ctx.headers,
    )
    print_response(response)


# ---------------------------------------------------------------------------
# Event marketing (EVENT_MARKETING_ENABLED module; 503 EVENT_MARKETING_DISABLED where off)
# ---------------------------------------------------------------------------

# EventMarketingWrite, as the server declares it; anything else is refused
# before the request is made so a stale seed fails with the field's name.
MARKETING_FIELDS = frozenset({
    "durationText", "scheduleText", "eligibilityText", "eligibilityNote",
    "registrationDeadlineUtc", "hasOpenSlots", "urgencyText", "contactNumber",
    "fee", "feeStructure", "packageOffers", "offers", "clubMembership", "facilities",
})


def marketing_block(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a marketing block locally and convert its local times."""
    data = convert_timestamps(data)
    if isinstance(data.get("offers"), list):
        data["offers"] = [convert_timestamps(o) if isinstance(o, dict) else o for o in data["offers"]]
    unknown = sorted(set(data) - MARKETING_FIELDS)
    if unknown:
        raise click.ClickException(
            f"not marketing fields: {unknown}. Allowed: {sorted(MARKETING_FIELDS)} "
            "(registrationDeadline and offers[].validUntil may be given in local time)."
        )
    return data


def _marketing_url(ctx: Context, event_id: int) -> str:
    return f"{ctx.base_url}/v1/events/by_id/{event_id}/marketing"


def print_marketing_response(response: httpx.Response) -> None:
    if response.status_code == 503:
        click.echo(
            "The event marketing module is off on this deployment (EVENT_MARKETING_ENABLED).", err=True,
        )
    print_response(response)


@event.group("marketing")
def event_marketing():
    """The event's marketing block: fees, schedule text, offers, facilities."""
    pass


@event_marketing.command("get")
@click.argument("event_id", type=int)
@pass_context
def event_marketing_get(ctx: Context, event_id: int):
    """Read the block (admin/coach); 404 EVENT_MARKETING_NOT_FOUND when none.

    Example: event marketing get 123
    """
    print_marketing_response(httpx.get(_marketing_url(ctx, event_id), headers=ctx.headers))


@event_marketing.command("set")
@click.argument("event_id", type=int)
@click.option("--json", "json_value", help="The block as inline JSON")
@click.option("--file", type=click.Path(exists=True, dir_okay=False), help="The block from a JSON file")
@pass_context
def event_marketing_set(ctx: Context, event_id: int, json_value: str | None, file: str | None):
    """Replace the whole block (admin or organizer).

    Fields: durationText, scheduleText, eligibilityText, eligibilityNote,
    registrationDeadline (local) or registrationDeadlineUtc, hasOpenSlots,
    urgencyText, contactNumber, fee, feeStructure[{name, amount, period}],
    packageOffers[{name, price, description, features}], offers[{title,
    description, validUntil|validUntilUtc}], clubMembership{title, description,
    benefits}, facilities[{name, description, iconName}].

    Example: event marketing set 123 --file marketing.json
    """
    data = _parse_preference_value(json_value, file)
    if not isinstance(data, dict):
        raise click.ClickException("the marketing block must be a JSON object")
    print_marketing_response(
        httpx.put(_marketing_url(ctx, event_id), json=marketing_block(data), headers=ctx.headers)
    )


@event_marketing.command("clear")
@click.argument("event_id", type=int)
@pass_context
def event_marketing_clear(ctx: Context, event_id: int):
    """Delete the block (admin or organizer).

    Example: event marketing clear 123
    """
    print_marketing_response(httpx.delete(_marketing_url(ctx, event_id), headers=ctx.headers))


# ============================================================================
# Occurrence Commands (admin/coach)
# ============================================================================


@main.group()
def occurrences():
    """Occurrence listing commands (admin/coach)."""
    pass


@occurrences.command("list")
@click.option("--from", "from_time", help="From time (local ISO, e.g. 2026-04-30T00:00:00). Default: today midnight")
@click.option("--days", default=30, help="Number of days from start (default: 30)")
@click.option("--event-id", type=int, help="Filter to a specific event")
@click.option("--type", "event_type", help="Filter by event type (programme, camp, oneOff)")
@click.option("--visibility", help="Filter by visibility (public, private)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=100, help="Pagination limit (max 100)")
@pass_context
def occurrences_list(
    ctx: Context,
    from_time: str | None,
    days: int,
    event_id: int | None,
    event_type: str | None,
    visibility: str | None,
    offset: int,
    limit: int,
):
    """List occurrences across all events (admin/coach).

    Example: occurrences list
    Example: occurrences list --from "2026-05-01T00:00:00" --days 7
    Example: occurrences list --event-id 6034
    Example: occurrences list --type programme
    """
    from_iso = from_time or today_midnight_iso()
    to_iso = add_days_iso(from_iso, days)
    params: dict[str, str | int] = {
        "fromTimeUtc": local_iso_to_utc_ms(from_iso),
        "toTimeUtc": local_iso_to_utc_ms(to_iso),
        "offset": offset,
        "limit": limit,
    }
    if event_type:
        params["type"] = event_type
    if visibility:
        params["visibility"] = visibility
    response = httpx.get(
        f"{ctx.base_url}/v1/events/occurrences",
        params=params,
        headers=ctx.headers,
    )
    if response.status_code == 200:
        data = response.json()
        if event_id is not None:
            data = [o for o in data if o.get("eventId") == event_id]
        click.echo(json.dumps(add_local_times(data), indent=2))
    else:
        print_response(response)


def fetch_occurrence(ctx: Context, event_id: int, occurrence_time_utc: int) -> dict[str, Any]:
    """GET one occurrence, or print the failure and exit."""
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}",
        headers=ctx.headers,
    )
    if response.status_code != 200:
        print_response(response)
        sys.exit(1)
    return response.json()


def resolve_occurrence_version(
    ctx: Context, event_id: int, occurrence_time_utc: int, version: int | None
) -> int:
    """The occurrence version to send: the override, else the current one.

    Every occurrence change carries it (club_server#430); an occurrence never
    changed is at version 1.
    """
    if version is not None:
        return version
    return int(fetch_occurrence(ctx, event_id, occurrence_time_utc)["version"])


OCCURRENCE_VERSION_OPTION = click.option(
    "--version", "version", type=int,
    help="Occurrence version to send (default: read from the occurrence).",
)


@occurrences.command("get")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@pass_context
def occurrences_get(ctx: Context, event_id: int, occurrence_time_local: str):
    """Get a specific occurrence by event ID and local time.

    Shows its version, updatedAt and updatedBy — the version the change
    commands below send.

    Example: occurrences get 6034 "20260530 0630"
    Example: occurrences get 6034 "2026-05-30T06:30:00"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}",
        headers=ctx.headers,
    )
    print_response(response)


@occurrences.command("cancel")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.option("--reason", required=True, help="Cancellation reason (required by the server)")
@OCCURRENCE_VERSION_OPTION
@pass_context
def occurrences_cancel(ctx: Context, event_id: int, occurrence_time_local: str, reason: str, version: int | None):
    """Cancel a specific occurrence.

    Carries the occurrence's version (read unless --version is given);
    409 STALE_VERSION means someone changed the occurrence first.

    Example: occurrences cancel 6031 "20260503 0030" --reason "Rain"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    data: dict[str, Any] = {
        "reason": reason,
        "version": resolve_occurrence_version(ctx, event_id, occurrence_time_utc, version),
    }
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/cancel",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@occurrences.command("undo-cancel")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@OCCURRENCE_VERSION_OPTION
@pass_context
def occurrences_undo_cancel(ctx: Context, event_id: int, occurrence_time_local: str, version: int | None):
    """Restore a cancelled occurrence.

    Carries the occurrence's version (read unless --version is given).

    Example: occurrences undo-cancel 6031 "20260503 0030"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/undo-cancel",
        json={"version": resolve_occurrence_version(ctx, event_id, occurrence_time_utc, version)},
        headers=ctx.headers,
    )
    print_response(response)


@occurrences.command("reschedule")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.option("--start-time-local", help="New start time (local ISO); may only move the occurrence later")
@click.option("--duration-minutes", type=int, help="New duration in minutes")
@click.option("--venue-id", type=int, help="New venue ID")
@OCCURRENCE_VERSION_OPTION
@pass_context
def occurrences_reschedule(
    ctx: Context,
    event_id: int,
    occurrence_time_local: str,
    start_time_local: str | None,
    duration_minutes: int | None,
    venue_id: int | None,
    version: int | None,
):
    """Reschedule one occurrence: later start, new duration and/or venue.

    422 POSTPONE_ONLY when the new start is earlier; 400 RESCHEDULE_LEAD_TIME_VIOLATED
    inside the 30 minutes before the effective start; 422 CANCELLED_OCCURRENCE when
    the slot is cancelled or past the event's cutoff; 422 INVALID_SESSIONS for a
    duration change on a schedule that carries a timetable.

    Example: occurrences reschedule 6031 "20260503 0030" --start-time-local "2026-05-03T02:00:00"
    Carries the occurrence's version (read unless --version is given);
    409 STALE_VERSION means someone changed the occurrence first.

    Example: occurrences reschedule 6031 "20260503 0030" --duration-minutes 90 --venue-id 2227
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    data: dict[str, Any] = {
        "version": resolve_occurrence_version(ctx, event_id, occurrence_time_utc, version),
    }
    if start_time_local:
        data["newStartTimeUtc"] = local_iso_to_utc_ms(start_time_local)
    if duration_minutes is not None:
        data["newDurationMinutes"] = duration_minutes
    if venue_id is not None:
        data["newVenueId"] = venue_id
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/reschedule",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@occurrences.command("attendance")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@pass_context
def occurrences_attendance(ctx: Context, event_id: int, occurrence_time_local: str):
    """Get attendance records for an occurrence.

    Example: occurrences attendance 6031 "20260503 0030"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers=ctx.headers,
    )
    print_response(response)


@occurrences.command("attendance-list")
@click.option("--from", "from_time", help="From time (local ISO, e.g. 2026-04-30T00:00:00). Default: today midnight")
@click.option("--days", default=30, help="Number of days from start (default: 30)")
@click.option("--event-id", type=int, help="Filter to a specific event")
@click.option("--membername", help="Filter to a specific member")
@click.option("--status", "status_filter", help="Filter by status (present, absent, on_leave, ...)")
@pass_context
def occurrences_attendance_list(
    ctx: Context,
    from_time: str | None,
    days: int,
    event_id: int | None,
    membername: str | None,
    status_filter: str | None,
):
    """List attendance records across all events in a time window (admin/coach).

    Returns AttendanceRecordResponse items: eventId, occurrenceTimeUtc,
    membername, status, notes, previousStatus, leaveReason, recordedAtUtc.

    Example: occurrences attendance-list
    Example: occurrences attendance-list --from "2026-05-01T00:00:00" --days 7
    Example: occurrences attendance-list --event-id 6034 --status absent
    """
    from_iso = from_time or today_midnight_iso()
    to_iso = add_days_iso(from_iso, days)
    params: dict[str, str | int] = {
        "fromTimeUtc": local_iso_to_utc_ms(from_iso),
        "toTimeUtc": local_iso_to_utc_ms(to_iso),
    }
    response = httpx.get(
        f"{ctx.base_url}/v1/events/occurrences/attendance",
        params=params,
        headers=ctx.headers,
    )
    if response.status_code == 200:
        data = response.json()
        if event_id is not None:
            data = [r for r in data if r.get("eventId") == event_id]
        if membername:
            data = [r for r in data if r.get("membername") == membername]
        if status_filter:
            data = [r for r in data if r.get("status") == status_filter]
        click.echo(json.dumps(add_local_times(data), indent=2))
    else:
        print_response(response)


@occurrences.command("mark-attendance")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.argument("json_input")
@pass_context
def occurrences_mark_attendance(ctx: Context, event_id: int, occurrence_time_local: str, json_input: str):
    """Mark attendance for an occurrence.

    The server settles members one by one and answers 200 with
    {marked: [{membername, status}], refused: [{membername, code, message}],
    trialEnded: [{membername}]}.
    A refused member (e.g. INSUFFICIENT_CREDIT on a credit-enabled deployment)
    is reported and the command exits 1, so a script cannot mistake a partial
    result for success. trialEnded names the members whose mark spent the last
    of their trial credit, which removed them from the programme; they are
    also in `marked`, so this is reported on stderr ("N trial(s) ended: ...")
    but is not a failure. Both lists are always empty without the credit system.

    Example: occurrences mark-attendance 6031 "20260503 0030" '{"records":[{"membername":"coach_member","status":"present"}]}'
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    data = parse_json_input(json_input)
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)
    body = response.json() if response.status_code == 200 else {}
    trial_ended = body.get("trialEnded") or []
    if trial_ended:
        names = ", ".join(t.get("membername") for t in trial_ended)
        click.echo(f"{len(trial_ended)} trial(s) ended: {names}", err=True)
    refused = body.get("refused") or []
    if refused:
        marked = body.get("marked") or []
        click.echo(f"{len(marked)} marked, {len(refused)} refused:", err=True)
        for r in refused:
            click.echo(f"  {r.get('membername')}: {r.get('code')} - {r.get('message')}", err=True)
        sys.exit(1)


@occurrences.command("approve-leave")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.argument("membernames", nargs=-1, required=True)
@pass_context
def occurrences_approve_leave(ctx: Context, event_id: int, occurrence_time_local: str, membernames: tuple[str, ...]):
    """Approve leave requests for an occurrence.

    Example: occurrences approve-leave 6031 "20260503 0030" coach_member
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/approve",
        json={"membernames": list(membernames)},
        headers=ctx.headers,
    )
    print_response(response)


@occurrences.command("reject-leave")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.argument("membernames", nargs=-1, required=True)
@click.option("--reason", help="Rejection reason")
@pass_context
def occurrences_reject_leave(ctx: Context, event_id: int, occurrence_time_local: str, membernames: tuple[str, ...], reason: str | None):
    """Reject leave requests for an occurrence.

    Example: occurrences reject-leave 6031 "20260503 0030" coach_member --reason "Important session"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    data: dict[str, Any] = {"membernames": list(membernames)}
    if reason:
        data["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/leave/reject",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# Enrollment Commands (admin/organizer)
# ============================================================================


@main.group()
def enrollment():
    """Enrollment management commands (admin/organizer actions on event members)."""
    pass


@enrollment.command("list")
@click.argument("event_id", type=int)
@pass_context
def enrollment_list(ctx: Context, event_id: int):
    """List enrollments for an event.

    Example: enrollment list 5
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments",
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("invite")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@pass_context
def enrollment_invite(ctx: Context, event_id: int, membernames: tuple[str, ...]):
    """Invite members to an event.

    Example: enrollment invite 5 member_a member_b
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/invite",
        json={"membernames": list(membernames)},
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("assign")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@pass_context
def enrollment_assign(ctx: Context, event_id: int, membernames: tuple[str, ...]):
    """Directly assign members to an event (bypasses invitation flow).

    Example: enrollment assign 5 member_a member_b
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/assign",
        json={"membernames": list(membernames)},
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("assign-trial")
@click.argument("event_id", type=int)
@click.argument("membername")
@pass_context
def enrollment_assign_trial(ctx: Context, event_id: int, membername: str):
    """Assign a trial membership to an event.

    Example: enrollment assign-trial 5 member_only
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/assign-trial",
        json={"membername": membername},
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("approve")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@pass_context
def enrollment_approve(ctx: Context, event_id: int, membernames: tuple[str, ...]):
    """Approve join requests for an event.

    Example: enrollment approve 5 member_only
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/approve",
        json={"membernames": list(membernames)},
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("reject")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@click.option("--reason", help="Reason for rejection")
@pass_context
def enrollment_reject(ctx: Context, event_id: int, membernames: tuple[str, ...], reason: str | None):
    """Reject join requests for an event.

    Example: enrollment reject 5 member_only --reason "Batch full"
    """
    data: dict[str, Any] = {"membernames": list(membernames)}
    if reason:
        data["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/reject",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


CREDIT_DISPOSITION_HELP = (
    "JSON {penalty, validFrom|validFromUtc, validUntil|validUntilUtc, reason} settling the member's "
    "credit bound to this programme (credit-enabled deployments: required when they hold one, "
    "422 CREDIT_DISPOSITION_REQUIRED; refused where it does not apply, 422 CREDIT_DISPOSITION_NOT_APPLICABLE)."
)


def parse_credit_disposition(json_input: str | None) -> dict[str, Any] | None:
    """The --credit-disposition JSON with local validity times converted to UTC ms."""
    if json_input is None:
        return None
    return convert_timestamps(parse_json_input(json_input))


@enrollment.command("remove")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@click.option("--reason", help="Reason for removal")
@click.option("--credit-disposition", help=CREDIT_DISPOSITION_HELP)
@pass_context
def enrollment_remove(ctx: Context, event_id: int, membernames: tuple[str, ...], reason: str | None, credit_disposition: str | None):
    """Remove enrolled members from an event.

    The remainder after the flat penalty moves to a new general account with
    the given validity window; settlement waits for a running occurrence to end.

    Example: enrollment remove 5 member_only --reason "Non-attendance"
    Example: enrollment remove 5 member_only --credit-disposition '{"penalty":2,"validFrom":"2026-05-01T00:00:00","validUntil":"2026-08-01T00:00:00","reason":"Left mid-term"}'
    """
    data: dict[str, Any] = {"membernames": list(membernames)}
    if reason:
        data["reason"] = reason
    disposition = parse_credit_disposition(credit_disposition)
    if disposition is not None:
        data["creditDisposition"] = disposition
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/remove",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("approve-withdraw")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@click.option("--credit-disposition", help=CREDIT_DISPOSITION_HELP)
@pass_context
def enrollment_approve_withdraw(ctx: Context, event_id: int, membernames: tuple[str, ...], credit_disposition: str | None):
    """Approve withdrawal requests.

    Example: enrollment approve-withdraw 5 member_only
    Example: enrollment approve-withdraw 5 member_only --credit-disposition '{"penalty":0,"validFrom":"2026-05-01T00:00:00","validUntil":"2026-08-01T00:00:00","reason":"Moved away"}'
    """
    data: dict[str, Any] = {"membernames": list(membernames)}
    disposition = parse_credit_disposition(credit_disposition)
    if disposition is not None:
        data["creditDisposition"] = disposition
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/approve-withdraw",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@enrollment.command("reject-withdraw")
@click.argument("event_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@click.option("--reason", help="Reason for rejecting withdrawal")
@pass_context
def enrollment_reject_withdraw(ctx: Context, event_id: int, membernames: tuple[str, ...], reason: str | None):
    """Reject withdrawal requests.

    Example: enrollment reject-withdraw 5 member_only --reason "Season not over"
    """
    data: dict[str, Any] = {"membernames": list(membernames)}
    if reason:
        data["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/enrollments/reject-withdraw",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# MyEvents Commands (member/self)
# ============================================================================


@main.group()
def myevents():
    """My events commands (member actions on own events)."""
    pass


@myevents.command("list")
@click.option("--from", "from_time", help="From time (local ISO, e.g. 2026-01-01T00:00:00)")
@click.option("--to", "to_time", help="To time (local ISO, e.g. 2026-12-31T23:59:00)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def myevents_list(ctx: Context, from_time: str | None, to_time: str | None, offset: int, limit: int):
    """List events for the authenticated user.

    Example: myevents list
    Example: myevents list --from "2026-01-01T00:00:00" --to "2026-06-30T23:59:00"
    """
    params: dict[str, str | int] = {"offset": offset, "limit": limit}
    if from_time:
        params["fromTimeUtc"] = local_iso_to_utc_ms(from_time)
    if to_time:
        params["toTimeUtc"] = local_iso_to_utc_ms(to_time)
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}",
        params=params,
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("stats")
@click.option("--from", "from_time", help="From time (local ISO, e.g. 2026-04-30T00:00:00). Default: today midnight")
@click.option("--days", default=30, help="Number of days from start (default: 30)")
@pass_context
def myevents_stats(ctx: Context, from_time: str | None, days: int):
    """Get my attendance over a time window.

    The window is required by the server, so it is always sent; --from and
    --days set it.

    Example: myevents stats
    Example: myevents stats --from "2026-05-01T00:00:00" --days 7
    """
    from_iso = from_time or today_midnight_iso()
    to_iso = add_days_iso(from_iso, days)
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/attendance",
        params={
            "fromTimeUtc": local_iso_to_utc_ms(from_iso),
            "toTimeUtc": local_iso_to_utc_ms(to_iso),
        },
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("occurrences")
@click.option("--from", "from_time", help="From time (local ISO, e.g. 2026-04-30T00:00:00). Default: today midnight")
@click.option("--days", default=30, help="Number of days from start (default: 30)")
@click.option("--event-id", type=int, help="Filter to a specific event")
@click.option("--type", "event_type", help="Filter by event type (programme, camp, oneOff)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=100, help="Pagination limit (max 100)")
@pass_context
def myevents_occurrences(
    ctx: Context,
    from_time: str | None,
    days: int,
    event_id: int | None,
    event_type: str | None,
    offset: int,
    limit: int,
):
    """List occurrences for my enrolled events.

    Example: myevents occurrences
    Example: myevents occurrences --from "2026-05-01T00:00:00" --days 7
    Example: myevents occurrences --event-id 6034
    Example: myevents occurrences --type programme
    """
    from_iso = from_time or today_midnight_iso()
    to_iso = add_days_iso(from_iso, days)
    params: dict[str, str | int] = {
        "fromTimeUtc": local_iso_to_utc_ms(from_iso),
        "toTimeUtc": local_iso_to_utc_ms(to_iso),
        "offset": offset,
        "limit": limit,
    }
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/occurrences",
        params=params,
        headers=ctx.headers,
    )
    if response.status_code == 200:
        data = response.json()
        if event_id is not None:
            data = [o for o in data if o.get("eventId") == event_id]
        if event_type is not None:
            data = [o for o in data if o.get("eventType") == event_type]
        click.echo(json.dumps(add_local_times(data), indent=2))
    else:
        print_response(response)


@myevents.command("occurrence")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@pass_context
def myevents_occurrence_get(ctx: Context, event_id: int, occurrence_time_local: str):
    """Get a specific occurrence for one of my events.

    Example: myevents occurrence 6034 "20260530 0630"
    Example: myevents occurrence 6034 "2026-05-30T06:30:00"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/occurrences/{occurrence_time_utc}",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("attendance-list")
@click.option("--from", "from_time", help="From time (local ISO, e.g. 2026-04-30T00:00:00). Default: today midnight")
@click.option("--days", default=30, help="Number of days from start (default: 30)")
@click.option("--event-id", type=int, help="Filter to a specific event")
@click.option("--status", "status_filter", help="Filter by status (present, absent, on_leave, ...)")
@click.argument("username", required=False)
@pass_context
def myevents_attendance_list(
    ctx: Context,
    from_time: str | None,
    days: int,
    event_id: int | None,
    status_filter: str | None,
    username: str | None,
):
    """List the user's own attendance records in a time window.

    Defaults username to the authenticated user. Returns AttendanceRecordResponse
    items (same shape as `occurrences attendance-list`).

    Example: myevents attendance-list
    Example: myevents attendance-list --from "2026-05-01T00:00:00" --days 14
    Example: myevents attendance-list coach_member --event-id 6034
    """
    target = username or ctx.username
    from_iso = from_time or today_midnight_iso()
    to_iso = add_days_iso(from_iso, days)
    params: dict[str, str | int] = {
        "fromTimeUtc": local_iso_to_utc_ms(from_iso),
        "toTimeUtc": local_iso_to_utc_ms(to_iso),
    }
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{target}/attendance",
        params=params,
        headers=ctx.headers,
    )
    if response.status_code == 200:
        data = response.json()
        if event_id is not None:
            data = [r for r in data if r.get("eventId") == event_id]
        if status_filter:
            data = [r for r in data if r.get("status") == status_filter]
        click.echo(json.dumps(add_local_times(data), indent=2))
    else:
        print_response(response)


@myevents.command("attendance")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@pass_context
def myevents_attendance(ctx: Context, event_id: int, occurrence_time_local: str):
    """Get my attendance record for an occurrence.

    Example: myevents attendance 6034 "20260730 0630"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/occurrences/{occurrence_time_utc}/attendance",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("leave-request")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.option("--reason", help="Leave reason")
@pass_context
def myevents_leave_request(ctx: Context, event_id: int, occurrence_time_local: str, reason: str | None):
    """Request leave for an occurrence.

    Example: myevents leave-request 6034 "20260730 0630" --reason "Travel"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    data: dict[str, Any] = {}
    if reason:
        data["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/occurrences/{occurrence_time_utc}/leave/request",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("leave-cancel")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@pass_context
def myevents_leave_cancel(ctx: Context, event_id: int, occurrence_time_local: str):
    """Cancel a pending leave request.

    Example: myevents leave-cancel 6034 "20260730 0630"
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/occurrences/{occurrence_time_utc}/leave/cancel",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("get")
@click.argument("event_id", type=int)
@pass_context
def myevents_get(ctx: Context, event_id: int):
    """Get details for one of my events.

    Example: myevents get 5
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("schedules")
@click.argument("event_id", type=int)
@pass_context
def myevents_schedules(ctx: Context, event_id: int):
    """List an event's schedules in timeline order, as the signed-in member.

    Example: myevents schedules 6031
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/schedules",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("enrollment")
@click.argument("event_id", type=int)
@pass_context
def myevents_enrollment(ctx: Context, event_id: int):
    """Get my enrollment status for an event.

    Example: myevents enrollment 5
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/enrollments",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("accept")
@click.argument("event_id", type=int)
@pass_context
def myevents_accept(ctx: Context, event_id: int):
    """Accept an event invitation.

    Example: myevents accept 5
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/enrollments/accept",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("decline")
@click.argument("event_id", type=int)
@pass_context
def myevents_decline(ctx: Context, event_id: int):
    """Decline an event invitation.

    Example: myevents decline 5
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/enrollments/decline",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("request")
@click.argument("event_id", type=int)
@pass_context
def myevents_request(ctx: Context, event_id: int):
    """Request to join an event.

    Example: myevents request 5
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/enrollments/request",
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("withdraw")
@click.argument("event_id", type=int)
@click.option("--reason", help="Reason for withdrawal")
@pass_context
def myevents_withdraw(ctx: Context, event_id: int, reason: str | None):
    """Request withdrawal from an event.

    Example: myevents withdraw 5 --reason "Schedule conflict"
    """
    data: dict[str, Any] = {}
    if reason:
        data["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/enrollments/withdraw",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@myevents.command("cancel-withdraw")
@click.argument("event_id", type=int)
@pass_context
def myevents_cancel_withdraw(ctx: Context, event_id: int):
    """Cancel a pending withdrawal request.

    Example: myevents cancel-withdraw 5
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/myevents/by_id/{ctx.username}/{event_id}/enrollments/cancel-withdraw",
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# Credit Commands (credit system; every route answers 503 CREDIT_SYSTEM_DISABLED where off)
# ============================================================================


@main.group()
def credits():
    """Credit accounts and ledger (admin; list/entries also coach). Ids are 8-character codes."""
    pass


@main.group()
def mycredits():
    """The signed-in member's own credit accounts and statement."""
    pass


def credit_window_params(
    from_local: str | None = None,
    to_local: str | None = None,
    **fixed: Any,
) -> dict[str, Any]:
    """Query params for a ledger/account listing: unset filters are omitted."""
    params: dict[str, Any] = {k: v for k, v in fixed.items() if v is not None}
    if from_local:
        params["fromTs"] = local_iso_to_utc_ms(from_local)
    if to_local:
        params["toTs"] = local_iso_to_utc_ms(to_local)
    return params


@credits.command("open")
@click.argument("json_input")
@pass_context
def credits_open(ctx: Context, json_input: str):
    """Open an account from JSON (admin).

    Fields: membername, credits, validFrom, validUntil (local ISO; or the *Utc
    forms in ms), reason; optional eventId binds it to that programme, else the
    account is general; optional isTrial.

    Example: credits open '{"membername":"m1","credits":10,"validFrom":"2026-05-01T00:00:00","validUntil":"2026-08-01T00:00:00","reason":"Paid term","eventId":123}'
    """
    data = convert_timestamps(parse_json_input(json_input))
    response = httpx.post(f"{ctx.base_url}/v1/credits/accounts", json=data, headers=ctx.headers)
    print_response(response)


@credits.command("get")
@click.argument("account_id")
@pass_context
def credits_get(ctx: Context, account_id: str):
    """Look up an account by its code (admin)."""
    response = httpx.get(f"{ctx.base_url}/v1/credits/accounts/{account_id}", headers=ctx.headers)
    print_response(response)


@credits.command("list")
@click.option("--membername", help="Filter by member")
@click.option("--event-id", type=int, help="Filter by the programme an account is bound to")
@click.option("--kind", type=click.Choice(["event", "general"]), help="Bound to a programme, or general")
@click.option("--state", type=click.Choice(["usable", "empty", "expired", "closed"]), help="Derived state")
@click.option("--trial/--no-trial", "is_trial", default=None, help="Trial accounts only / none")
@click.option("--expiring-before-local", help="Accounts whose validity ends before this local time")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def credits_list(
    ctx: Context,
    membername: str | None,
    event_id: int | None,
    kind: str | None,
    state: str | None,
    is_trial: bool | None,
    expiring_before_local: str | None,
    offset: int,
    limit: int,
):
    """List accounts (admin/coach).

    Example: credits list --membername m1 --state usable
    """
    params = credit_window_params(
        membername=membername, eventId=event_id, kind=kind, state=state, isTrial=is_trial,
    )
    if expiring_before_local:
        params["expiringBeforeUtc"] = local_iso_to_utc_ms(expiring_before_local)
    params.update(offset=offset, limit=limit)
    response = httpx.get(f"{ctx.base_url}/v1/credits/accounts", params=params, headers=ctx.headers)
    print_response(response)


@credits.command("entries")
@click.option("--membername", help="Filter by member")
@click.option("--account-id", help="Filter by account code")
@click.option("--event-id", type=int, help="Filter by programme")
@click.option("--entry-type", help="grant, grantReversal, sessionDeduction, sessionRefund, penalty, transferOut, transferIn, validityExtended")
@click.option("--from", "from_local", help="Entries created at or after this local time")
@click.option("--to", "to_local", help="Entries created before this local time")
@click.option("--order", type=click.Choice(["asc", "desc"]), help="Oldest first (asc) or newest first (desc); omitted, the server's default (asc)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def credits_entries(
    ctx: Context,
    membername: str | None,
    account_id: str | None,
    event_id: int | None,
    entry_type: str | None,
    from_local: str | None,
    to_local: str | None,
    order: str | None,
    offset: int,
    limit: int,
):
    """The ledger (admin/coach): every movement, append-only.

    Items: {id, accountId, membername, amount, entryType, eventId,
    occurrenceTimeUtc, reason, actorUsername, createdAtUtc, offsetsEntryId,
    balanceAfter, totalAfter}. balanceAfter is the account's balance after
    the entry; totalAfter is the member's credit across all their accounts
    after it (a transfer between their own accounts leaves it unchanged).
    Neither depends on the filters or page asked for.

    Example: credits entries --account-id AB12CD34 --from "2026-05-01T00:00:00"
    """
    params = credit_window_params(
        from_local, to_local,
        membername=membername, accountId=account_id, eventId=event_id, entryType=entry_type,
        order=order,
    )
    params.update(offset=offset, limit=limit)
    response = httpx.get(f"{ctx.base_url}/v1/credits/entries", params=params, headers=ctx.headers)
    print_response(response)


@credits.command("extend")
@click.argument("account_id")
@click.option("--valid-until-local", required=True, help="New end of validity (local ISO)")
@click.option("--reason", required=True, help="Recorded on the statement (validityExtended)")
@pass_context
def credits_extend(ctx: Context, account_id: str, valid_until_local: str, reason: str):
    """Move an account's validity end later (admin).

    Example: credits extend AB12CD34 --valid-until-local "2026-09-01T00:00:00" --reason "Injury"
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/credits/accounts/{account_id}/extend",
        json={"validUntilUtc": local_iso_to_utc_ms(valid_until_local), "reason": reason},
        headers=ctx.headers,
    )
    print_response(response)


@credits.command("reverse")
@click.argument("account_id")
@click.option("--reason", required=True, help="Why the grant is taken back")
@click.option("--credits", "amount", type=int, help="Reverse only this many credits (default: the whole grant)")
@pass_context
def credits_reverse(ctx: Context, account_id: str, reason: str, amount: int | None):
    """Take back a grant, wholly or in part (admin).

    Example: credits reverse AB12CD34 --reason "Entered twice"
    Example: credits reverse AB12CD34 --reason "Overpaid" --credits 3
    """
    data: dict[str, Any] = {"reason": reason}
    if amount is not None:
        data["credits"] = amount
    response = httpx.post(
        f"{ctx.base_url}/v1/credits/accounts/{account_id}/reverse",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@credits.command("transfer")
@click.argument("account_id")
@click.option("--penalty", type=int, required=True, help="Flat number of credits kept back")
@click.option("--valid-from-local", required=True, help="Start of the new general account's validity (local ISO)")
@click.option("--valid-until-local", required=True, help="End of the new general account's validity (local ISO)")
@click.option("--reason", required=True, help="Recorded on both statements")
@pass_context
def credits_transfer(ctx: Context, account_id: str, penalty: int, valid_from_local: str, valid_until_local: str, reason: str):
    """Close an account and move the remainder after a penalty into a new general account (admin).

    Returns {source, created}; created is null when the penalty consumed the balance.

    Example: credits transfer AB12CD34 --penalty 2 --valid-from-local "2026-05-01T00:00:00" --valid-until-local "2026-08-01T00:00:00" --reason "Left programme"
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/credits/accounts/{account_id}/transfer",
        json={
            "penalty": penalty,
            "validFromUtc": local_iso_to_utc_ms(valid_from_local),
            "validUntilUtc": local_iso_to_utc_ms(valid_until_local),
            "reason": reason,
        },
        headers=ctx.headers,
    )
    print_response(response)


@mycredits.command("list")
@click.option("--state", type=click.Choice(["usable", "empty", "expired", "closed"]), help="Derived state")
@click.option("--include-closed", is_flag=True, help="Include closed accounts")
@pass_context
def mycredits_list(ctx: Context, state: str | None, include_closed: bool):
    """The signed-in member's accounts.

    Example: mycredits list --include-closed
    """
    params = credit_window_params(state=state, includeClosed=include_closed or None)
    response = httpx.get(f"{ctx.base_url}/v1/mycredits/by_id/{ctx.username}", params=params, headers=ctx.headers)
    print_response(response)


@mycredits.command("get")
@click.argument("account_id")
@pass_context
def mycredits_get(ctx: Context, account_id: str):
    """One of the signed-in member's accounts by code."""
    response = httpx.get(
        f"{ctx.base_url}/v1/mycredits/by_id/{ctx.username}/accounts/{account_id}",
        headers=ctx.headers,
    )
    print_response(response)


@mycredits.command("entries")
@click.option("--account-id", help="Filter by account code")
@click.option("--event-id", type=int, help="Filter by programme")
@click.option("--from", "from_local", help="Entries created at or after this local time")
@click.option("--to", "to_local", help="Entries created before this local time")
@click.option("--order", type=click.Choice(["asc", "desc"]), help="Oldest first (asc) or newest first (desc); omitted, the server's default (asc)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def mycredits_entries(
    ctx: Context,
    account_id: str | None,
    event_id: int | None,
    from_local: str | None,
    to_local: str | None,
    order: str | None,
    offset: int,
    limit: int,
):
    """The signed-in member's statement.

    Items: {id, accountId, membername, amount, entryType, eventId,
    occurrenceTimeUtc, reason, actorUsername, createdAtUtc, offsetsEntryId,
    balanceAfter, totalAfter}. balanceAfter is the account's balance after
    the entry; totalAfter is the member's credit across all their accounts
    after it (a transfer between their own accounts leaves it unchanged).
    Neither depends on the filters or page asked for.

    Example: mycredits entries --from "2026-05-01T00:00:00"
    """
    params = credit_window_params(from_local, to_local, accountId=account_id, eventId=event_id, order=order)
    params.update(offset=offset, limit=limit)
    response = httpx.get(
        f"{ctx.base_url}/v1/mycredits/by_id/{ctx.username}/entries",
        params=params,
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# Preference Commands (system preferences, super-admin)
# ============================================================================


@main.group()
def preferences():
    """System preferences: any JSON under a key, super-admin only. The website's club_info and site_media live here."""
    pass


def _preference_url(ctx: Context, key: str | None = None) -> str:
    if key is None:
        return f"{ctx.base_url}/v1/admin/preferences"
    return f"{ctx.base_url}/v1/admin/preferences/{key}"


@preferences.command("list")
@pass_context
def preferences_list(ctx: Context):
    """Every stored preference with its value."""
    print_response(httpx.get(_preference_url(ctx), headers=ctx.headers))


@preferences.command("get")
@click.argument("key")
@pass_context
def preferences_get(ctx: Context, key: str):
    """One preference: {key, value, updatedAtUtc, updatedBy}.

    Example: preferences get club_info
    """
    print_response(httpx.get(_preference_url(ctx, key), headers=ctx.headers))


@preferences.command("set")
@click.argument("key")
@click.option("--json", "json_value", help="The value as a JSON literal: object, array, string, number or boolean")
@click.option("--file", type=click.Path(exists=True, dir_okay=False), help="Read the JSON value from a file")
@pass_context
def preferences_set(ctx: Context, key: str, json_value: str | None, file: str | None):
    """Store a value under KEY and print the stored row.

    Example: preferences set club_info --file club_info.json
    Example: preferences set max_members --json 40
    """
    value = _parse_preference_value(json_value, file)
    print_response(httpx.patch(_preference_url(ctx, key), json={"value": value}, headers=ctx.headers))


@preferences.command("set-key")
@click.argument("key")
@click.argument("subkey")
@click.argument("value")
@pass_context
def preferences_set_key(ctx: Context, key: str, subkey: str, value: str):
    """Set one entry of a map-valued preference, keeping the rest.

    Reads the current map (an unset preference starts as {}), sets
    SUBKEY to VALUE (a string) and writes the map back.

    Example: preferences set-key site_media landing_background <media-uuid>
    """
    current = httpx.get(_preference_url(ctx, key), headers=ctx.headers)
    if current.status_code == 404:
        stored: Any = {}
    elif current.status_code == 200:
        stored = current.json().get("value")
    else:
        print_response(current)
        return
    if not isinstance(stored, dict):
        raise click.ClickException(f"preference {key!r} is not a JSON object; use `preferences set` to replace it.")
    stored[subkey] = value
    print_response(httpx.patch(_preference_url(ctx, key), json={"value": stored}, headers=ctx.headers))


# ============================================================================
# Staff Listing Commands (curation of the public coach listing, admin)
# ============================================================================


@main.group()
def staff():
    """Curate the public staff listing: per coach a position, guest flag and hidden flag."""
    pass


@staff.command("list")
@click.option("--include-guests", is_flag=True, help="Show guest coaches too (withheld from the public list by default)")
@click.option("--include-hidden", is_flag=True, help="Show hidden coaches too (never public)")
@pass_context
def staff_list(ctx: Context, include_guests: bool, include_hidden: bool):
    """The curated rows: {username, displayName, isPublicProfile, position, isGuest, isHidden}.

    Mirrors what the public listing shows unless the flags widen it.
    """
    response = httpx.get(f"{ctx.base_url}/v1/admin/staff-listing", headers=ctx.headers)
    if response.status_code != 200:
        print_response(response)
        return
    rows = [
        r for r in response.json()
        if (include_guests or not r.get("isGuest")) and (include_hidden or not r.get("isHidden"))
    ]
    click.echo(json.dumps(rows, indent=2))


@staff.command("set")
@click.argument("username")
@click.option("--position", type=int, help="Sort position on the public listing (lower first)")
@click.option("--guest/--no-guest", "is_guest", default=None, help="Mark as a guest coach (withheld unless asked for)")
@click.option("--hidden/--no-hidden", "is_hidden", default=None, help="Hide from the public listing")
@pass_context
def staff_set(ctx: Context, username: str, position: int | None, is_guest: bool | None, is_hidden: bool | None):
    """Set any subset of position / guest / hidden for a coach (422 NOT_A_COACH otherwise).

    Example: staff set coach_one --position 1
    Example: staff set guest_coach --guest
    """
    data: dict[str, Any] = {}
    if position is not None:
        data["position"] = position
    if is_guest is not None:
        data["isGuest"] = is_guest
    if is_hidden is not None:
        data["isHidden"] = is_hidden
    if not data:
        raise click.UsageError("Nothing to set: pass --position, --guest/--no-guest or --hidden/--no-hidden.")
    response = httpx.put(f"{ctx.base_url}/v1/admin/staff-listing/{username}", json=data, headers=ctx.headers)
    print_response(response)


@staff.command("clear")
@click.argument("username")
@pass_context
def staff_clear(ctx: Context, username: str):
    """Remove a coach's curation row: they become public and uncurated.

    Example: staff clear coach_one
    """
    response = httpx.delete(f"{ctx.base_url}/v1/admin/staff-listing/{username}", headers=ctx.headers)
    print_response(response)


# ============================================================================
# Inquiry Commands (the website's contact form inbox, admin)
# ============================================================================


@main.group()
def inquiries():
    """Read and handle website inquiries (contact / interest) before the app has an inbox."""
    pass


def _inquiries_url(ctx: Context, inquiry_id: int | None = None) -> str:
    if inquiry_id is None:
        return f"{ctx.base_url}/v1/admin/inquiries"
    return f"{ctx.base_url}/v1/admin/inquiries/{inquiry_id}"


@inquiries.command("list")
@click.option("--kind", type=click.Choice(["contact", "interest"]), help="Only this kind")
@click.option("--handled/--unhandled", "handled", default=None, help="Only handled, or only still open")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def inquiries_list(ctx: Context, kind: str | None, handled: bool | None, offset: int, limit: int):
    """List inquiries, newest first: {id, kind, name, email, phone, message, extra, createdAtUtc, handledAt, handledBy}.

    Example: inquiries list --unhandled
    """
    params: dict[str, Any] = {}
    if kind:
        params["kind"] = kind
    if handled is not None:
        params["handled"] = handled
    params.update(offset=offset, limit=limit)
    print_response(httpx.get(_inquiries_url(ctx), params=params, headers=ctx.headers))


@inquiries.command("get")
@click.argument("inquiry_id", type=int)
@pass_context
def inquiries_get(ctx: Context, inquiry_id: int):
    """One inquiry by id (the server lists only, so this pages through the listing).

    Example: inquiries get 7
    """
    offset, page = 0, 100
    while True:
        response = httpx.get(
            _inquiries_url(ctx), params={"offset": offset, "limit": page}, headers=ctx.headers,
        )
        if response.status_code != 200:
            print_response(response)
            return
        body = response.json()
        for row in body.get("items", []):
            if row.get("id") == inquiry_id:
                click.echo(json.dumps(row, indent=2))
                return
        offset += page
        if offset >= body.get("total", 0) or not body.get("items"):
            raise click.ClickException(f"inquiry {inquiry_id} not found")


@inquiries.command("handle")
@click.argument("inquiry_id", type=int)
@pass_context
def inquiries_handle(ctx: Context, inquiry_id: int):
    """Mark an inquiry handled (records who and when)."""
    print_response(httpx.patch(_inquiries_url(ctx, inquiry_id), json={"handled": True}, headers=ctx.headers))


@inquiries.command("unhandle")
@click.argument("inquiry_id", type=int)
@pass_context
def inquiries_unhandle(ctx: Context, inquiry_id: int):
    """Reopen a handled inquiry."""
    print_response(httpx.patch(_inquiries_url(ctx, inquiry_id), json={"handled": False}, headers=ctx.headers))


@inquiries.command("delete")
@click.argument("inquiry_id", type=int)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@pass_context
def inquiries_delete(ctx: Context, inquiry_id: int, yes: bool):
    """Delete an inquiry for good (there is no soft delete); asks first unless --yes.

    Example: inquiries delete 7 --yes
    """
    if not yes:
        click.confirm(f"Permanently delete inquiry {inquiry_id}?", abort=True)
    print_response(httpx.delete(_inquiries_url(ctx, inquiry_id), headers=ctx.headers))


# ============================================================================
# Group Commands
# ============================================================================


@main.group()
def groups():
    """Group management commands (plural for list/create)."""
    pass


@main.group()
def group():
    """Single group commands (get/update/delete/members)."""
    pass


@groups.command("list")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
@age_help
def groups_list(ctx: Context, offset: int, limit: int):
    """List all groups.

    {AGE_WINDOW_HELP}
    A group's reference day is today, so its window moves forward each day.
    ineligibleMemberCount is how many members of a semi-auto group no longer
    meet its age band or gender (`group members` says which); nobody is
    removed automatically.

    Example: groups list
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/groups",
        params={"offset": offset, "limit": limit},
        headers=ctx.headers,
    )
    print_response(response)


@groups.command("create")
@click.argument("json_input")
@pass_context
@age_help
def groups_create(ctx: Context, json_input: str):
    """Create a new group from JSON.

    Fields: name (required), description, minAge, maxAge, strictAge, gender,
    semiAuto. Response carries kind = manual | semi_auto | auto:
    no criteria -> manual; criteria + semiAuto=true -> semi_auto; criteria only -> auto.
    A criterion is gender or an age bound.

    {AGE_BAND_HELP}

    Example: groups create '{"name":"U14 Auto","minAge":12,"maxAge":14}'
    Example: groups create '{"name":"U14 SemiAuto","semiAuto":true,"minAge":12,"maxAge":{"years":14,"months":6},"strictAge":true}'
    """
    data = parse_json_input(json_input)
    for legacy in ("isAuto", "ageMin", "ageMax", "cutoffDateUtc"):
        if legacy in data:
            raise click.ClickException(
                f"{legacy!r} is removed; use minAge / maxAge / strictAge / semiAuto (see --help)."
            )
    data = convert_timestamps(convert_age_band(data))
    response = httpx.post(
        f"{ctx.base_url}/v1/groups",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@group.command("get")
@click.argument("group_id", type=int)
@pass_context
@age_help
def group_get(ctx: Context, group_id: int):
    """Get group details with members.

    {AGE_WINDOW_HELP}
    A group's reference day is today, so its window moves forward each day.
    Each member row carries eligible: false for a semi-auto member who no
    longer meets the group's age band or gender, worked out when read; staff,
    and members of manual and auto groups, are always true.
    ineligibleMemberCount counts the false ones; nobody is removed
    automatically. Admins get the notification group.member_ineligible once
    a day for each member who has newly stopped matching.
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("update")
@click.argument("group_id", type=int)
@click.argument("json_input")
@pass_context
@age_help
def group_update(ctx: Context, group_id: int, json_input: str):
    """Update a group from JSON.

    Same field set as create. Manual->auto conversion is rejected when the group has
    members (422 MEMBERS_EXIST); manual->semi_auto with ineligible existing members
    is rejected (422 MEMBERS_INELIGIBLE).

    {AGE_BAND_HELP}

    Example: group update 1 '{"name": "Advanced"}'
    Example: group update 1 '{"semiAuto":true,"minAge":12,"strictAge":true}'
    Example: group update 1 '{"maxAge":null}'
    """
    data = parse_json_input(json_input)
    for legacy in ("isAuto", "ageMin", "ageMax", "cutoffDateUtc"):
        if legacy in data:
            raise click.ClickException(
                f"{legacy!r} is removed; use minAge / maxAge / strictAge / semiAuto (see --help)."
            )
    data = convert_timestamps(convert_age_band(data))
    response = httpx.patch(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}",
        json=data,
        headers=ctx.headers,
    )
    print_response(response)


@group.command("delete")
@click.argument("group_id", type=int)
@pass_context
def group_delete(ctx: Context, group_id: int):
    """Delete a group (soft delete)."""
    response = httpx.delete(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("members")
@click.argument("group_id", type=int)
@pass_context
def group_members(ctx: Context, group_id: int):
    """List members of a group: {membername, firstName, lastName, nickname, eligible}.

    eligible is false for a semi-auto member who no longer meets the group's
    age band or gender, worked out when read; staff, and members of manual
    and auto groups, are always true. Nobody is removed automatically.

    Example: group members 1
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/members",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("add-member")
@click.argument("group_id", type=int)
@click.argument("membername")
@pass_context
def group_add_member(ctx: Context, group_id: int, membername: str):
    """Add a single member to a group.

    Example: group add-member 1 coach_member
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/members/byname/{membername}",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("add-members")
@click.argument("group_id", type=int)
@click.argument("membernames", nargs=-1, required=True)
@pass_context
def group_add_members(ctx: Context, group_id: int, membernames: tuple[str, ...]):
    """Add multiple members to a group.

    Example: group add-members 1 coach_member member_only
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/members/bulk",
        json={"membernames": list(membernames)},
        headers=ctx.headers,
    )
    print_response(response)


@group.command("remove-member")
@click.argument("group_id", type=int)
@click.argument("membername")
@pass_context
def group_remove_member(ctx: Context, group_id: int, membername: str):
    """Remove a member from a group.

    Example: group remove-member 1 coach_member
    """
    response = httpx.delete(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/members/{membername}",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("eligible")
@click.argument("group_id", type=int)
@pass_context
def group_eligible(ctx: Context, group_id: int):
    """List users who can be added to (or request to join) a group (admin/coach).

    For auto groups returns 422 AUTO_GROUP_NOT_JOINABLE on the server.

    Example: group eligible 1
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/eligible",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("requests")
@click.argument("group_id", type=int)
@click.option("--status", "status_filter", help="Filter by status (pending, approved, rejected, cancelled)")
@pass_context
def group_requests(ctx: Context, group_id: int, status_filter: str | None):
    """List join requests for a group (admin/coach).

    Example: group requests 1
    Example: group requests 1 --status pending
    """
    params: dict[str, str] = {}
    if status_filter:
        params["status"] = status_filter
    response = httpx.get(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/requests",
        params=params,
        headers=ctx.headers,
    )
    print_response(response)


@group.command("approve-request")
@click.argument("group_id", type=int)
@click.argument("request_id", type=int)
@pass_context
def group_approve_request(ctx: Context, group_id: int, request_id: int):
    """Approve a pending join request (admin). Re-checks semi-auto eligibility.

    Example: group approve-request 1 42
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/requests/{request_id}/approve",
        headers=ctx.headers,
    )
    print_response(response)


@group.command("reject-request")
@click.argument("group_id", type=int)
@click.argument("request_id", type=int)
@click.option("--reason", help="Rejection reason")
@pass_context
def group_reject_request(ctx: Context, group_id: int, request_id: int, reason: str | None):
    """Reject a pending join request (admin).

    Example: group reject-request 1 42 --reason "Wrong age group"
    """
    data: dict[str, Any] = {}
    if reason:
        data["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/groups/by_id/{group_id}/requests/{request_id}/reject",
        json=data if data else None,
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# MyGroups Commands (user side: list, eligible, join, requests)
# ============================================================================


@main.group()
def mygroups():
    """My-groups commands (user actions on own group memberships)."""
    pass


@mygroups.command("list")
@click.argument("username", required=False)
@pass_context
def mygroups_list(ctx: Context, username: str | None):
    """List groups the user belongs to (explicit + matching auto groups).

    Defaults to the authenticated user.

    Example: mygroups list
    Example: mygroups list coach_member
    """
    target = username or ctx.username
    response = httpx.get(
        f"{ctx.base_url}/v1/mygroups/by_id/{target}",
        headers=ctx.headers,
    )
    print_response(response)


@mygroups.command("eligible")
@click.argument("username", required=False)
@pass_context
def mygroups_eligible(ctx: Context, username: str | None):
    """List groups the user can request to join.

    Defaults to the authenticated user.

    Example: mygroups eligible
    """
    target = username or ctx.username
    response = httpx.get(
        f"{ctx.base_url}/v1/mygroups/by_id/{target}/eligible",
        headers=ctx.headers,
    )
    print_response(response)


@mygroups.command("join")
@click.argument("group_id", type=int)
@click.argument("username", required=False)
@click.option("--reason", help="Optional reason to attach to the join request")
@pass_context
def mygroups_join(ctx: Context, group_id: int, username: str | None, reason: str | None):
    """Submit a join request to a group.

    Defaults username to the authenticated user.

    Example: mygroups join 1
    Example: mygroups join 1 coach_member --reason "Recommended by coach"
    """
    target = username or ctx.username
    body: dict[str, Any] = {}
    if reason:
        body["reason"] = reason
    response = httpx.post(
        f"{ctx.base_url}/v1/mygroups/by_id/{target}/join/{group_id}",
        json=body if body else None,
        headers=ctx.headers,
    )
    print_response(response)


@mygroups.command("requests")
@click.argument("username", required=False)
@pass_context
def mygroups_requests(ctx: Context, username: str | None):
    """List the user's own join requests.

    Defaults to the authenticated user.

    Example: mygroups requests
    """
    target = username or ctx.username
    response = httpx.get(
        f"{ctx.base_url}/v1/mygroups/by_id/{target}/requests",
        headers=ctx.headers,
    )
    print_response(response)


@mygroups.command("cancel-request")
@click.argument("request_id", type=int)
@click.argument("username", required=False)
@pass_context
def mygroups_cancel_request(ctx: Context, request_id: int, username: str | None):
    """Cancel a still-pending join request.

    Defaults username to the authenticated user.

    Example: mygroups cancel-request 42
    """
    target = username or ctx.username
    response = httpx.delete(
        f"{ctx.base_url}/v1/mygroups/by_id/{target}/requests/{request_id}",
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# Auth Commands (self-service + admin password reset)
# ============================================================================


@main.group()
def auth():
    """Authentication helpers (me/refresh/logout, password reset, availability)."""
    pass


@auth.command("me")
@pass_context
def auth_me(ctx: Context):
    """Get the authenticated user's profile.

    Example: auth me
    """
    response = httpx.get(f"{ctx.base_url}/v1/auth/me", headers=ctx.headers)
    print_response(response)


@auth.command("username-available")
@click.argument("username")
@pass_context
def auth_username_available(ctx: Context, username: str):
    """Public endpoint: check whether a username is free for registration.

    Example: auth username-available newcomer
    """
    response = httpx.get(
        f"{ctx.base_url}/v1/auth/username-available",
        params={"username": username},
    )
    print_response(response)


@auth.command("change-password")
@click.option("--current", "current_password", help="Current password (omit to use --pw)")
@click.option("--new", "new_password", required=True, help="New password")
@pass_context
def auth_change_password(ctx: Context, current_password: str | None, new_password: str):
    """Change the authenticated user's password.

    The current password defaults to the one supplied at the top-level --pw flag.

    Example: auth change-password --new s3cr3t
    Example: auth change-password --current old --new new
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/auth/change-password",
        json={
            "currentPassword": current_password or ctx.password,
            "newPassword": new_password,
        },
        headers=ctx.headers,
    )
    print_response(response)


@auth.command("reset-password")
@click.argument("email")
@pass_context
def auth_reset_password(ctx: Context, email: str):
    """Public endpoint: request a password reset email (no auth needed).

    Example: auth reset-password user@example.com
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/auth/reset-password",
        json={"email": email},
    )
    print_response(response)


@auth.command("register")
@click.argument("json_input")
@pass_context
def auth_register(ctx: Context, json_input: str):
    """Public endpoint: self-register a new user account (no auth needed).

    User is created in 'registered' status. Registered and pending users
    can log in to manage their onboarding: upload documents (gallery),
    call 'me submit-for-review' (registered → pending), and 'me reapply'
    after an admin reconsider. Active-only endpoints (events, groups,
    enrollments) reject non-active users with ACCOUNT_NOT_ACTIVE.
    A seed file's profile / identity-document / staffListing blocks are
    stripped and left to the follow-up commands (see `users register`).

    Required fields: username, password, plus server-enforced profile
    fields (firstName or lastName, gender, dateOfBirth, phone).

    Example: auth register registration.json
    Example: auth register '{"username":"newcomer","password":"s3cret",...}'
    """
    register_user(ctx, json_input)


@auth.command("refresh")
@pass_context
def auth_refresh(ctx: Context):
    """Exchange the refresh token from this run's login for a new token pair.

    The token is never taken from the user: the command logs in with -u/--pw
    and sends the refresh token that login returned. Without credentials it
    sends no token, and the server answers 422 saying one is required.

    Example: -u alice --pw s3cret auth refresh
    """
    refresh_token = ctx.refresh_token
    body = {"refreshToken": refresh_token} if refresh_token else {}
    response = httpx.post(f"{ctx.base_url}/v1/auth/refresh", json=body)
    print_response(response)


@auth.command("logout")
@pass_context
def auth_logout(ctx: Context):
    """Invalidate the current access token server-side.

    Example: auth logout
    """
    response = httpx.post(f"{ctx.base_url}/v1/auth/logout", headers=ctx.headers)
    print_response(response)


@user.command("reset-password")
@click.argument("username")
@pass_context
def user_reset_password(ctx: Context, username: str):
    """Admin: reset a user's password to a server-generated value.

    The new password is returned in the response. Notifies the target user via
    account.password_changed_by_admin.

    Example: user reset-password coach_member
    """
    response = httpx.post(
        f"{ctx.base_url}/v1/admin/reset-password/{username}",
        headers=ctx.headers,
    )
    print_response(response)


# ============================================================================
# Upload Commands
# ============================================================================


# ── Entity media ─────────────────────────────────────────────────────────
#
# Media attaches to an owner in two steps: upload the bytes to /v1/media,
# then link the returned uuid under /v1/<owner>/by_id/<id>/media with a tag.
# One command set covers every owner type rather than four near-identical
# ones.

OWNER_PATHS = {
    "venue": "venues",
    "group": "groups",
    "event": "events",
    "user": "users",
    "evaluation": "evaluations",
}


def _owner_base(ctx: "Context", owner_type: str, owner_id: str) -> str:
    return f"{ctx.base_url}/v1/{OWNER_PATHS[owner_type]}/by_id/{owner_id}/media"


@media.command("attach")
@click.argument("owner_type", type=click.Choice(sorted(OWNER_PATHS)))
@click.argument("owner_id")
@click.argument("filepath")
@click.option("--tag", required=True, help="Link tag, e.g. hero, gallery, logo")
@click.option("--metadata", default=None, help="Free-text metadata stored alongside the link")
@click.option("--preserve-original", is_flag=True, help="Keep the original file as uploaded")
@click.option("--owner", "owner_username", help=OWNER_OPTION_HELP)
@pass_context
def media_attach(
    ctx: Context,
    owner_type: str,
    owner_id: str,
    filepath: str,
    tag: str,
    metadata: str | None,
    preserve_original: bool,
    owner_username: str | None,
) -> None:
    """Upload a local file and link it to an owner under TAG.

    --owner names the user the uploaded file belongs to. It is separate from
    OWNER_TYPE / OWNER_ID, which say what the file is linked to: an admin
    setting a member's photo names the member in both.

    Example: media attach venue 1 ./rink.jpg --tag hero
    Example: media attach event 6034 ./poster.png --tag gallery
    Example: media attach user alice ./alice.jpg --tag avatar --owner alice
    """
    up = ctx.upload.add_file(
        ctx.base_url, ctx.headers, filepath, preserve_original=preserve_original,
        form={"ownerUsername": owner_username} if owner_username else None,
    )
    # 202: a video, accepted and queued for conversion; it can be linked at once.
    if up.status_code not in (200, 201, 202):
        print_response(up)
        sys.exit(1)
    uuid = up.json().get("uuid")
    if not uuid:
        raise click.ClickException(f"Upload response missing uuid: {up.text[:200]}")
    click.echo(f"Uploaded {Path(filepath).name} → {uuid}")

    # The server stores metadata as an opaque string, not a JSON object.
    payload: dict[str, Any] = {"tag": tag, "mediaUuid": uuid}
    if metadata:
        payload["metadata"] = metadata
    response = httpx.post(
        _owner_base(ctx, owner_type, owner_id), json=payload, headers=ctx.headers,
    )
    print_response(response)


@media.command("link")
@click.argument("owner_type", type=click.Choice(sorted(OWNER_PATHS)))
@click.argument("owner_id")
@click.argument("media_uuid")
@click.option("--tag", required=True, help="Link tag")
@pass_context
def media_link(ctx: Context, owner_type: str, owner_id: str, media_uuid: str, tag: str) -> None:
    """Link already-uploaded media to an owner, by uuid.

    Example: media link venue 1 <uuid> --tag hero
    """
    response = httpx.post(
        _owner_base(ctx, owner_type, owner_id),
        json={"tag": tag, "mediaUuid": media_uuid},
        headers=ctx.headers,
    )
    print_response(response)


@media.command("list")
@click.argument("owner_type", type=click.Choice(sorted(OWNER_PATHS)))
@click.argument("owner_id")
@click.option("--tag", default=None, help="Restrict to one tag")
@pass_context
def media_list(ctx: Context, owner_type: str, owner_id: str, tag: str | None) -> None:
    """List media linked to an owner.

    Example: media list venue 1
    Example: media list event 6034 --tag gallery
    """
    owner = OWNER_PATHS[owner_type]
    if tag:
        url = f"{ctx.base_url}/v1/{owner}/by_id/{owner_id}/media/{tag}"
    else:
        url = f"{ctx.base_url}/v1/{owner}/by_id/{owner_id}/media"
    print_response(httpx.get(url, headers=ctx.headers))


@media.command("get")
@click.argument("owner_type", type=click.Choice(sorted(OWNER_PATHS)))
@click.argument("owner_id")
@click.argument("tag")
@click.argument("media_uuid")
@pass_context
def media_get(ctx: Context, owner_type: str, owner_id: str, tag: str, media_uuid: str) -> None:
    """One link: the media under TAG on an owner, with its metadata.

    Example: media get venue 1 hero 0f3c...
    """
    owner = OWNER_PATHS[owner_type]
    print_response(httpx.get(
        f"{ctx.base_url}/v1/{owner}/by_id/{owner_id}/media/{tag}/{media_uuid}", headers=ctx.headers,
    ))


@media.command("set-metadata")
@click.argument("owner_type", type=click.Choice(sorted(OWNER_PATHS)))
@click.argument("owner_id")
@click.argument("tag")
@click.argument("media_uuid")
@click.argument("metadata", required=False)
@click.option("--clear", is_flag=True, default=False, help="Remove the link's metadata")
@pass_context
def media_set_metadata(
    ctx: Context, owner_type: str, owner_id: str, tag: str, media_uuid: str,
    metadata: str | None, clear: bool,
) -> None:
    """Replace the free-text metadata on one link, or --clear it.

    Example: media set-metadata event 6034 gallery 0f3c... "Opening day"
    Example: media set-metadata event 6034 gallery 0f3c... --clear
    """
    if (metadata is None) == (not clear):
        raise click.UsageError("Pass METADATA, or --clear to remove it.")
    owner = OWNER_PATHS[owner_type]
    print_response(httpx.patch(
        f"{ctx.base_url}/v1/{owner}/by_id/{owner_id}/media/{tag}/{media_uuid}",
        json={"metadata": None if clear else metadata},
        headers=ctx.headers,
    ))


@media.command("myfiles")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=100, help="Pagination limit")
@click.option("--media-type", "media_type", default=None, help="Filter by media type")
@pass_context
def media_myfiles(ctx: Context, offset: int, limit: int, media_type: str | None) -> None:
    """List media you uploaded.

    Example: media myfiles
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if media_type:
        params["mediaType"] = media_type
    print_response(httpx.get(f"{ctx.base_url}/v1/media/myfiles", params=params, headers=ctx.headers))


@media.command("owners")
@click.argument("media_uuid")
@pass_context
def media_owners(ctx: Context, media_uuid: str) -> None:
    """Show every owner one media is linked to.

    Example: media owners <uuid>
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/media/by_id/{media_uuid}/links", headers=ctx.headers))


@media.command("detach")
@click.argument("owner_type", type=click.Choice(sorted(OWNER_PATHS)))
@click.argument("owner_id")
@click.option("--tag", required=True, help="Link tag")
@click.option("--uuid", "media_uuid", default=None,
              help="Detach one media; omit to detach everything under the tag")
@pass_context
def media_detach(
    ctx: Context, owner_type: str, owner_id: str, tag: str, media_uuid: str | None,
) -> None:
    """Detach media from an owner, by tag or by tag + uuid.

    Example: media detach venue 1 --tag hero --uuid <uuid>
    Example: media detach venue 1 --tag hero
    """
    owner = OWNER_PATHS[owner_type]
    if media_uuid:
        url = f"{ctx.base_url}/v1/{owner}/by_id/{owner_id}/media/{tag}/{media_uuid}"
    else:
        url = f"{ctx.base_url}/v1/{owner}/by_id/{owner_id}/media/{tag}"
    print_response(httpx.delete(url, headers=ctx.headers))


# ── Soft-delete lifecycle ────────────────────────────────────────────────
#
# Deletes here are reversible until hard-deleted. Seeding is a wipe-and-reload
# cycle, so being able to see and undo a soft delete matters.

TRASH_PATHS = {
    "user": "users",
    "venue": "venues",
    "group": "groups",
    "event": "events",
    "evaluation": "evaluations",
    "evaluation-template": "evaluations/templates",
}
# Media is restored like the rest but has no listing of its own.
RESTORE_PATHS = {**TRASH_PATHS, "media": "media"}


@main.group()
def trash() -> None:
    """List and restore soft-deleted records."""


@trash.command("list")
@click.argument("owner_type", type=click.Choice(sorted(TRASH_PATHS)))
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=100, help="Pagination limit")
@pass_context
def trash_list(ctx: Context, owner_type: str, offset: int, limit: int) -> None:
    """List soft-deleted records of one kind.

    Example: trash list venue
    """
    coll = TRASH_PATHS[owner_type]
    response = httpx.get(
        f"{ctx.base_url}/v1/{coll}/deleted",
        params={"offset": offset, "limit": limit},
        headers=ctx.headers,
    )
    print_response(response)


@trash.command("restore")
@click.argument("owner_type", type=click.Choice(sorted(RESTORE_PATHS)))
@click.argument("record_id")
@pass_context
def trash_restore(ctx: Context, owner_type: str, record_id: str) -> None:
    """Restore one soft-deleted record.

    Example: trash restore venue 2
    Example: trash restore user alice
    """
    restore_coll = RESTORE_PATHS[owner_type]
    response = httpx.post(
        f"{ctx.base_url}/v1/{restore_coll}/by_id/{record_id}/restore", headers=ctx.headers,
    )
    print_response(response)


@main.group()
def uploads():
    """Upload management commands (plural for list/create)."""
    pass


@main.group()
def upload():
    """Single upload commands (get/delete/download)."""
    pass


@uploads.command("list")
@click.option("--media-type", help="Filter by media type (image, video)")
@click.option("--status", "conversion_status", help="Filter by conversion status")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=20, help="Pagination limit")
@pass_context
def uploads_list(ctx: Context, media_type: str | None, conversion_status: str | None, offset: int, limit: int):
    """List uploaded media.

    Example: uploads list
    Example: uploads list --media-type image
    """
    response = ctx.upload.list_uploads(
        ctx.base_url, ctx.headers,
        offset=offset, limit=limit,
        media_type=media_type, conversion_status=conversion_status,
    )
    print_response(response)


@uploads.command("add-file")
@click.argument("filepath")
@click.option("--preserve-original", is_flag=True, help="Preserve original file")
@click.option(
    "--access-role", "access_roles", multiple=True,
    type=click.Choice(["public", "self", "admin", "coach"]),
    help="Who may see the file (repeatable; default public).",
)
@click.option("--encrypt", is_flag=True, default=False, help="Store the file encrypted at rest")
@click.option("--start", type=float, help="Video: where the converted clip starts, in seconds")
@click.option("--duration", type=float, help="Video: how long the converted clip runs, in seconds")
@click.option("--owner", "owner_username", help=OWNER_OPTION_HELP)
@pass_context
def uploads_add_file(
    ctx: Context,
    filepath: str,
    preserve_original: bool,
    access_roles: tuple[str, ...],
    encrypt: bool,
    start: float | None,
    duration: float | None,
    owner_username: str | None,
):
    """Upload a media file. Prints the fully-qualified download URL on stdout.

    Without --owner the file belongs to whoever uploads it. A non-admin
    naming someone else is refused (403 FORBIDDEN); an admin naming a user
    who does not exist gets 404 USER_NOT_FOUND.

    Example: uploads add-file photo.jpg
    Example: uploads add-file video.mp4 --preserve-original --start 5 --duration 30
    Example: uploads add-file id.png --access-role self --access-role admin --encrypt
    Example: uploads add-file alice.jpg --owner alice --access-role self --access-role admin
    """
    form: dict[str, str] = {}
    if access_roles:
        form["accessRoles"] = json.dumps(list(access_roles))
    if encrypt:
        form["encrypt"] = "true"
    if start is not None:
        form["start"] = str(start)
    if duration is not None:
        form["duration"] = str(duration)
    if owner_username:
        form["ownerUsername"] = owner_username
    response = ctx.upload.add_file(
        ctx.base_url, ctx.headers, filepath,
        preserve_original=preserve_original, form=form,
    )
    if response.status_code not in (200, 201):
        print_response(response)
        return
    uuid = response.json().get("uuid")
    if not uuid:
        raise click.ClickException(f"Upload response missing uuid: {response.text[:200]}")
    media = response.json()
    click.echo(ctx.upload.download_url(ctx.base_url, uuid, media.get("filename")))
    _echo_media_type(media)


@upload.command("get")
@click.argument("upload_id", type=int)
@pass_context
def upload_get(ctx: Context, upload_id: int):
    """Get upload metadata."""
    response = ctx.upload.get_upload(ctx.base_url, ctx.headers, upload_id)
    print_response(response)


@upload.command("delete")
@click.argument("upload_id", type=int)
@pass_context
def upload_delete(ctx: Context, upload_id: int):
    """Delete an upload."""
    response = ctx.upload.delete_upload(ctx.base_url, ctx.headers, upload_id)
    print_response(response)


@upload.command("download")
@click.argument("uuid")
@click.option(
    "--variant", default="original", type=click.Choice(["original", "poster", "animated"]),
    help="original (every type), poster (video, pdf), animated (video). Default: original.",
)
@click.option("--output", "-o", help="Output file path (default: stdout)")
@click.option("--head", is_flag=True, default=False, help="Only report the type and size (HEAD); fetch nothing.")
@pass_context
def upload_download(ctx: Context, uuid: str, variant: str, output: str | None, head: bool):
    """Download an uploaded file.

    Sends the token when -u/--pw are given, so files restricted by access
    roles download too; without them only public files do (401 otherwise).

    Example: upload download abc-123-def -o photo.jpg
    Example: upload download abc-123-def --variant poster -o poster.jpg
    Example: upload download abc-123-def --head
    """
    response = ctx.upload.download(ctx.base_url, ctx.optional_headers, uuid, variant=variant, head=head)
    if head:
        if response.status_code >= 400:
            click.echo(f"HTTP {response.status_code}", err=True)
            sys.exit(1)
        click.echo(json.dumps({
            "contentType": response.headers.get("content-type"),
            "contentLength": response.headers.get("content-length"),
            "contentDisposition": response.headers.get("content-disposition"),
        }, indent=2))
        return
    if response.status_code == 200:
        if output:
            with open(output, "wb") as f:
                f.write(response.content)
            click.echo(f"Downloaded to {output}")
        else:
            click.echo(f"Downloaded {len(response.content)} bytes (use -o to save to file)")
    else:
        print_response(response)


# ── Users: private record, super-admin transfer ─────────────────────────


@user.command("private")
@click.argument("username")
@pass_context
def user_private(ctx: Context, username: str):
    """A user's private record: contact details, address, status (self or admin).

    Example: user private alice
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/users/by_id/{username}/private", headers=ctx.headers))


@user.command("transfer-superadmin")
@click.argument("username")
@click.option("--yes", is_flag=True, default=False, help="Confirm: the caller stops being super admin.")
@pass_context
def user_transfer_superadmin(ctx: Context, username: str, yes: bool):
    """Hand the super-admin role from the caller to USERNAME (super admin only).

    The caller loses the role in the same step, so it needs --yes.

    Example: user transfer-superadmin new_owner --yes
    """
    if not yes:
        raise click.UsageError(
            f"This makes {username} super admin and removes the role from {ctx.username}; pass --yes."
        )
    print_response(httpx.post(
        f"{ctx.base_url}/v1/users/by_id/{username}/transfer-superadmin", headers=ctx.headers,
    ))


# ── Event eligibility, attendance clearing, one group of mine ──────────


@event.command("eligible")
@click.argument("event_id", type=int)
@pass_context
def event_eligible(ctx: Context, event_id: int):
    """Members eligible to enrol in an event (its gender and date-of-birth rules).

    Example: event eligible 123
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/events/by_id/{event_id}/eligible", headers=ctx.headers))


@occurrences.command("clear-attendance")
@click.argument("event_id", type=int)
@click.argument("occurrence_time_local", type=str)
@click.argument("membername")
@pass_context
def occurrences_clear_attendance(ctx: Context, event_id: int, occurrence_time_local: str, membername: str):
    """Remove one member's attendance mark from an occurrence.

    Example: occurrences clear-attendance 6031 "20260503 0030" alice
    """
    occurrence_time_utc = local_iso_to_utc_ms(parse_local_time(occurrence_time_local))
    print_response(httpx.delete(
        f"{ctx.base_url}/v1/events/by_id/{event_id}/occurrences/{occurrence_time_utc}/attendance/{membername}",
        headers=ctx.headers,
    ))


@mygroups.command("get")
@click.argument("group_id", type=int)
@click.argument("username", required=False)
@pass_context
def mygroups_get(ctx: Context, group_id: int, username: str | None):
    """One group the user belongs to. Defaults to the authenticated user.

    Example: mygroups get 12
    """
    target = username or ctx.username
    print_response(httpx.get(
        f"{ctx.base_url}/v1/mygroups/by_id/{target}/group/{group_id}", headers=ctx.headers,
    ))


# ── Notifications ───────────────────────────────────────────────────────


@main.group()
def notifications():
    """The signed-in user's notifications and delivery preferences; admins create them."""
    pass


@notifications.command("list")
@click.option("--unread-only", is_flag=True, default=False, help="Only unread notifications")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def notifications_list(ctx: Context, unread_only: bool, offset: int, limit: int):
    """List my notifications, newest first.

    Example: notifications list --unread-only
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if unread_only:
        params["unreadOnly"] = True
    print_response(httpx.get(f"{ctx.base_url}/v1/notifications", params=params, headers=ctx.headers))


@notifications.command("pending-actions")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def notifications_pending_actions(ctx: Context, offset: int, limit: int):
    """Notifications that still wait on me to act (approve, accept, ...).

    Example: notifications pending-actions
    """
    print_response(httpx.get(
        f"{ctx.base_url}/v1/notifications/pending-actions",
        params={"offset": offset, "limit": limit},
        headers=ctx.headers,
    ))


@notifications.command("unread-count")
@pass_context
def notifications_unread_count(ctx: Context):
    """How many of my notifications are unread.

    Example: notifications unread-count
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/notifications/unread-count", headers=ctx.headers))


@notifications.command("read")
@click.argument("notification_id", type=int)
@pass_context
def notifications_read(ctx: Context, notification_id: int):
    """Mark one notification read.

    Example: notifications read 42
    """
    print_response(httpx.post(
        f"{ctx.base_url}/v1/notifications/by_id/{notification_id}/read", headers=ctx.headers,
    ))


@notifications.command("read-all")
@pass_context
def notifications_read_all(ctx: Context):
    """Mark every notification of mine read.

    Example: notifications read-all
    """
    print_response(httpx.post(f"{ctx.base_url}/v1/notifications/read-all", headers=ctx.headers))


@notifications.command("delete")
@click.argument("notification_id", type=int)
@pass_context
def notifications_delete(ctx: Context, notification_id: int):
    """Delete a notification (admin).

    Example: notifications delete 42
    """
    print_response(httpx.delete(
        f"{ctx.base_url}/v1/notifications/by_id/{notification_id}", headers=ctx.headers,
    ))


@notifications.command("create")
@click.argument("json_input")
@pass_context
def notifications_create(ctx: Context, json_input: str):
    """Send one user a notification (admin only).

    Fields: username, type, channel, payload (object); optional
    pendingActionType, pendingActionId, pendingActionKey.

    Example: notifications create '{"username": "u1", "type": "custom", "channel": "in_app", "payload": {"title": "Hi"}}'
    """
    print_response(httpx.post(
        f"{ctx.base_url}/v1/notifications", json=parse_json_input(json_input), headers=ctx.headers,
    ))


@notifications.command("preferences")
@pass_context
def notifications_preferences(ctx: Context):
    """My delivery channels: {emailEnabled, pushEnabled, smsEnabled}.

    Example: notifications preferences
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/notifications/preferences", headers=ctx.headers))


@notifications.command("set-preferences")
@click.option("--email/--no-email", default=None, help="Email delivery")
@click.option("--push/--no-push", default=None, help="Push delivery")
@click.option("--sms/--no-sms", default=None, help="SMS delivery")
@pass_context
def notifications_set_preferences(ctx: Context, email: bool | None, push: bool | None, sms: bool | None):
    """Turn my delivery channels on or off; channels not named are left alone.

    Example: notifications set-preferences --no-email --push
    """
    data = {
        k: v for k, v in (("emailEnabled", email), ("pushEnabled", push), ("smsEnabled", sms))
        if v is not None
    }
    if not data:
        raise click.UsageError("Name at least one of --email/--no-email, --push/--no-push, --sms/--no-sms.")
    print_response(httpx.patch(
        f"{ctx.base_url}/v1/notifications/preferences", json=data, headers=ctx.headers,
    ))


# ── Broadcasts (admin) ──────────────────────────────────────────────────


@main.group()
def broadcasts():
    """Messages to an audience of members (admin)."""
    pass


@broadcasts.command("create")
@click.argument("json_input")
@pass_context
def broadcasts_create(ctx: Context, json_input: str):
    """Send a broadcast.

    \b
    Fields: audienceSelector, payload (object); optional expiresAt (local
    ISO) or expiresAtUtc, and email (bool) with emailSubject and emailBody
    (markdown), used only for the email copy. audienceSelector is one of:
      {"kind": "all_users"}
      {"kind": "role", "role": "admin" | "coach"}
      {"kind": "group", "groupId": N}
      {"kind": "event_members", "eventId": N}
      {"kind": "event_staff", "eventId": N}
      {"kind": "users", "usernames": [...]}

    Example: broadcasts create '{"audienceSelector": {"kind": "all_users"}, "payload": {"title": "Rink closed"}}'
    """
    data = convert_timestamps(parse_json_input(json_input))
    print_response(httpx.post(f"{ctx.base_url}/v1/broadcasts", json=data, headers=ctx.headers))


@broadcasts.command("list")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def broadcasts_list(ctx: Context, offset: int, limit: int):
    """List broadcasts, newest first.

    Example: broadcasts list
    """
    print_response(httpx.get(
        f"{ctx.base_url}/v1/broadcasts", params={"offset": offset, "limit": limit}, headers=ctx.headers,
    ))


@broadcasts.command("get")
@click.argument("broadcast_id", type=int)
@pass_context
def broadcasts_get(ctx: Context, broadcast_id: int):
    """One broadcast.

    Example: broadcasts get 3
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/broadcasts/by_id/{broadcast_id}", headers=ctx.headers))


@broadcasts.command("revoke")
@click.argument("broadcast_id", type=int)
@pass_context
def broadcasts_revoke(ctx: Context, broadcast_id: int):
    """Withdraw a broadcast from its recipients.

    Example: broadcasts revoke 3
    """
    print_response(httpx.delete(f"{ctx.base_url}/v1/broadcasts/by_id/{broadcast_id}", headers=ctx.headers))


@broadcasts.command("recipients")
@click.argument("broadcast_id", type=int)
@click.option("--status", "read_status", type=click.Choice(["read", "unread"]), help="Only readers or non-readers")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=100, help="Pagination limit")
@pass_context
def broadcasts_recipients(ctx: Context, broadcast_id: int, read_status: str | None, offset: int, limit: int):
    """Who a broadcast reached, and whether they read it.

    Example: broadcasts recipients 3 --status unread
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if read_status:
        params["status"] = read_status
    print_response(httpx.get(
        f"{ctx.base_url}/v1/broadcasts/by_id/{broadcast_id}/recipients", params=params, headers=ctx.headers,
    ))


# ── Audit log (admin) ───────────────────────────────────────────────────


@main.command("audit-log")
@click.option("--actor", help="Who did it")
@click.option("--action", help="Action name, e.g. RESCHEDULE_EVENT")
@click.option("--username", help="The user it was done to")
@click.option("--resource-type", help="e.g. event, venue, user")
@click.option("--resource-id", help="Id of that resource")
@click.option("--from", "from_time", help="From (local ISO or 'YYYYMMDD HHMM')")
@click.option("--to", "to_time", help="To (local ISO or 'YYYYMMDD HHMM')")
@click.option("--verbose", type=int, help="Detail level of each entry")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def audit_log(
    ctx: Context,
    actor: str | None,
    action: str | None,
    username: str | None,
    resource_type: str | None,
    resource_id: str | None,
    from_time: str | None,
    to_time: str | None,
    verbose: int | None,
    offset: int,
    limit: int,
):
    """Search the audit log (admin).

    Example: audit-log --resource-type event --resource-id 123
    Example: audit-log --actor sudo --from "20260901 0000"
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    for key, value in (
        ("actor", actor), ("action", action), ("username", username),
        ("resource_type", resource_type), ("resource_id", resource_id), ("verbose", verbose),
    ):
        if value is not None:
            params[key] = value
    if from_time:
        params["from_ts"] = local_iso_to_utc_ms(parse_local_time(from_time))
    if to_time:
        params["to_ts"] = local_iso_to_utc_ms(parse_local_time(to_time))
    print_response(httpx.get(f"{ctx.base_url}/v1/audit_log", params=params, headers=ctx.headers))


# ── Public (no sign-in): what the website reads ─────────────────────────


@main.group()
def public():
    """The unauthenticated API the website reads; no -u/--pw needed."""
    pass


@public.command("club-info")
@pass_context
def public_club_info(ctx: Context):
    """The club's name, contact details and settings the website shows.

    Example: public club-info
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/public/club-info"))


@public.command("staff")
@click.option("--include-guests", is_flag=True, default=False, help="Include guest coaches")
@pass_context
def public_staff(ctx: Context, include_guests: bool):
    """The public staff listing.

    Example: public staff --include-guests
    """
    print_response(httpx.get(
        f"{ctx.base_url}/v1/public/staff", params={"include_guests": include_guests},
    ))


@public.command("profile")
@click.argument("public_id")
@pass_context
def public_profile(ctx: Context, public_id: str):
    """A member's public profile, by public id.

    Example: public profile a1b2c3
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/public/profile/by_id/{public_id}"))


@public.command("venues")
@pass_context
def public_venues(ctx: Context):
    """Public venues.

    Example: public venues
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/public/venues"))


@public.command("venue")
@click.argument("public_id")
@pass_context
def public_venue(ctx: Context, public_id: str):
    """One public venue, by public id.

    Example: public venue a1b2c3
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/public/venues/{public_id}"))


@public.command("events")
@click.option("--type", "event_type", type=click.Choice(["programme", "camp", "oneOff"]), help="Event type")
@click.option("--from", "from_time", help="From (local ISO or 'YYYYMMDD HHMM')")
@click.option("--to", "to_time", help="To (local ISO or 'YYYYMMDD HHMM')")
@click.option("--featured/--not-featured", default=None, help="Only featured, or only not featured")
@click.option("--venue-id", help="A venue's public id")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def public_events(
    ctx: Context,
    event_type: str | None,
    from_time: str | None,
    to_time: str | None,
    featured: bool | None,
    venue_id: str | None,
    offset: int,
    limit: int,
):
    """Public events.

    Example: public events --type camp --featured
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if event_type:
        params["type"] = event_type
    if from_time:
        params["from"] = local_iso_to_utc_ms(parse_local_time(from_time))
    if to_time:
        params["to"] = local_iso_to_utc_ms(parse_local_time(to_time))
    if featured is not None:
        params["featured"] = featured
    if venue_id:
        params["venueId"] = venue_id
    print_response(httpx.get(f"{ctx.base_url}/v1/public/events", params=params))


@public.command("event")
@click.argument("public_id")
@pass_context
def public_event(ctx: Context, public_id: str):
    """One public event, by public id.

    Example: public event a1b2c3
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/public/events/{public_id}"))


@public.command("event-marketing")
@click.argument("public_ids", nargs=-1, required=True)
@pass_context
def public_event_marketing(ctx: Context, public_ids: tuple[str, ...]):
    """Marketing blocks of public events: one id, or up to 50 for listing cards.

    503 EVENT_MARKETING_DISABLED where the module is off.

    Example: public event-marketing a1b2c3
    Example: public event-marketing a1b2c3 d4e5f6
    """
    if len(public_ids) == 1:
        response = httpx.get(f"{ctx.base_url}/v1/public/events/{public_ids[0]}/marketing")
    else:
        response = httpx.get(
            f"{ctx.base_url}/v1/public/events/marketing", params={"ids": ",".join(public_ids)},
        )
    print_response(response)


@public.command("inquiry-token")
@pass_context
def public_inquiry_token(ctx: Context):
    """A contact-form token, as the website fetches one when it renders the form.

    Example: public inquiry-token
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/public/inquiries/token"))


# The server silently discards a submission sent sooner than this after its
# token was issued (a bot's speed); the CLI waits it out rather than lose it.
INQUIRY_MIN_FILL_SECONDS = 3.2


@public.command("inquiry-submit")
@click.argument("json_input")
@pass_context
def public_inquiry_submit(ctx: Context, json_input: str):
    """Submit the website's form: kind (contact | interest), name, email, message; optional phone, extra.

    Fetches a form token unless the JSON carries one, then waits the few
    seconds the server requires between token and submission — sooner, and
    the server accepts it but silently discards it as a bot's.

    Example: public inquiry-submit '{"kind": "contact", "name": "A", "email": "a@example.com", "message": "Hi"}'
    """
    data = parse_json_input(json_input)
    if "token" not in data:
        token = httpx.get(f"{ctx.base_url}/v1/public/inquiries/token")
        if token.status_code != 200:
            print_response(token)
        data["token"] = token.json()["token"]
        time.sleep(INQUIRY_MIN_FILL_SECONDS)
    print_response(httpx.post(f"{ctx.base_url}/v1/public/inquiries", json=data))


# ── Evaluations (optional module: 503 EVALUATIONS_DISABLED when off) ────
#
# A template is a name, a layout and items (club_server#535). An item is one
# variant selected by `type` — rating, yesNo, singleChoice, multipleChoice,
# number, qa or info — and a layout entry is an item or {section, items}. An
# evaluation is front matter (templateId, createdFor, eventId, period) plus
# answers, written one at a time while it is a draft. Templates are written
# by any staff member, an admin or a coach.

EVALUATION_ITEM_TYPES = ["rating", "yesNo", "singleChoice", "multipleChoice", "number", "qa", "info"]
# What `evaluations update` may change on a draft: its event and its period.
EVALUATION_DRAFT_FIELDS = {"eventId", "periodStart", "periodEnd", "periodStartUtc", "periodEndUtc"}


@main.group()
def evaluations():
    """Coach evaluations of members, and their templates (optional module)."""
    pass


@evaluations.group("templates")
def evaluation_templates():
    """Evaluation templates: a name, a layout and the items it asks.

    Any staff member, an admin or a coach, writes them. A live template's name
    is unique, ignoring case and outer spaces (TEMPLATE_NAME_TAKEN).
    """
    pass


@evaluation_templates.command("list")
@click.option("--deleted", is_flag=True, default=False, help="List soft-deleted templates instead")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def evaluation_templates_list(ctx: Context, deleted: bool, offset: int, limit: int):
    """List templates.

    Example: evaluations templates list
    """
    params = {"offset": offset, "limit": limit}
    if deleted:
        response = httpx.get(f"{ctx.base_url}/v1/evaluations/templates/deleted", params=params, headers=ctx.headers)
    else:
        response = httpx.get(f"{ctx.base_url}/v1/evaluations/templates", params=params, headers=ctx.headers)
    print_response(response)


@evaluation_templates.command("get")
@click.argument("template_id", type=int)
@pass_context
def evaluation_templates_get(ctx: Context, template_id: int):
    """One template: its layout (item ids and sections) and its items in layout order.

    inUse says whether any evaluation, soft-deleted included, is written
    against it: its items and layout are then frozen (TEMPLATE_IN_USE), and
    only its name may change.

    Example: evaluations templates get 1
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}", headers=ctx.headers))


@evaluation_templates.command("create")
@click.argument("json_input")
@pass_context
def evaluation_templates_create(ctx: Context, json_input: str):
    """Create a template (staff): {name, layout}, the items inline in the layout.

    A layout entry is an item or {"section": "...", "items": [...]}, one level
    deep; the template needs at least one question (an item other than info).

    Example: evaluations templates create template.json
    Example: evaluations templates create '{"name": "Skating", "layout": [{"type": "rating", "question": "Edges"}]}'
    """
    data = parse_json_input(json_input)
    print_response(httpx.post(f"{ctx.base_url}/v1/evaluations/templates", json=data, headers=ctx.headers))


@evaluation_templates.command("update")
@click.argument("template_id", type=int)
@click.argument("json_input")
@pass_context
def evaluation_templates_update(ctx: Context, template_id: int, json_input: str):
    """Rename or re-lay out a template (staff): {name?, layout?}.

    The layout names every item id exactly once, as ids and
    {"section": "...", "items": [ids]}; items themselves change with
    `evaluations templates items`.

    Example: evaluations templates update 1 '{"name": "Skating v2"}'
    Example: evaluations templates update 1 '{"layout": [3, {"section": "Notes", "items": [4, 5]}]}'
    """
    data = parse_json_input(json_input)
    print_response(httpx.patch(
        f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}", json=data, headers=ctx.headers,
    ))


@evaluation_templates.command("delete")
@click.argument("template_id", type=int)
@pass_context
def evaluation_templates_delete(ctx: Context, template_id: int):
    """Soft-delete a template (staff; `trash restore evaluation-template` undoes it).

    Example: evaluations templates delete 1
    """
    print_response(httpx.delete(f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}", headers=ctx.headers))


@evaluation_templates.group("items")
def evaluation_template_items():
    """A template's items, one at a time, and the search across templates."""


@evaluation_template_items.command("add")
@click.argument("template_id", type=int)
@click.argument("item_json")
@click.option("--section", help="Append to this section (default: the end of the layout)")
@pass_context
def evaluation_template_items_add(ctx: Context, template_id: int, item_json: str, section: str | None):
    """Add one item to a template (staff); a copy names its source as originItemId.

    Example: evaluations templates items add 1 '{"type": "yesNo", "question": "Stops both ways"}'
    Example: evaluations templates items add 1 item.json --section Notes
    """
    body: dict[str, Any] = {"item": parse_json_input(item_json)}
    if section is not None:
        body["section"] = section
    print_response(httpx.post(
        f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}/items", json=body, headers=ctx.headers,
    ))


@evaluation_template_items.command("replace")
@click.argument("template_id", type=int)
@click.argument("item_id", type=int)
@click.argument("item_json")
@pass_context
def evaluation_template_items_replace(ctx: Context, template_id: int, item_id: int, item_json: str):
    """Replace one item in place with a whole item of the same type (staff).

    Example: evaluations templates items replace 1 7 '{"type": "yesNo", "question": "Stops on both edges"}'
    """
    print_response(httpx.put(
        f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}/items/{item_id}",
        json=parse_json_input(item_json), headers=ctx.headers,
    ))


@evaluation_template_items.command("remove")
@click.argument("template_id", type=int)
@click.argument("item_id", type=int)
@pass_context
def evaluation_template_items_remove(ctx: Context, template_id: int, item_id: int):
    """Remove one item, and its place in the layout (staff).

    Example: evaluations templates items remove 1 7
    """
    print_response(httpx.delete(
        f"{ctx.base_url}/v1/evaluations/templates/by_id/{template_id}/items/{item_id}", headers=ctx.headers,
    ))


@evaluation_template_items.command("search")
@click.option("--search", "text", help="Text to look for")
@click.option("--type", "item_type", type=click.Choice(EVALUATION_ITEM_TYPES), help="Item type")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def evaluation_template_items_search(
    ctx: Context, text: str | None, item_type: str | None, offset: int, limit: int,
):
    """Items of every live template, with templateId and templateName (staff).

    Example: evaluations templates items search --search edges --type rating
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if text is not None:
        params["search"] = text
    if item_type is not None:
        params["type"] = item_type
    print_response(httpx.get(f"{ctx.base_url}/v1/evaluations/templates/items", params=params, headers=ctx.headers))


@evaluations.command("list")
@click.option("--deleted", is_flag=True, default=False, help="List soft-deleted evaluations instead (other filters ignored)")
@click.option("--status", "eval_status", type=click.Choice(["draft", "saved", "published"]), help="Lifecycle state")
@click.option("--created-for", help="The member evaluated")
@click.option("--event-id", type=int, help="Event of an event-scoped evaluation")
@click.option("--general/--no-general", default=None, help="Only general evaluations (or, with --no-general, only event ones)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def evaluations_list(
    ctx: Context,
    deleted: bool,
    eval_status: str | None,
    created_for: str | None,
    event_id: int | None,
    general: bool | None,
    offset: int,
    limit: int,
):
    """List my evaluations (coach). An admin owns none, so sees none.

    Example: evaluations list --created-for alice --status draft
    """
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if deleted:
        print_response(httpx.get(f"{ctx.base_url}/v1/evaluations/deleted", params=params, headers=ctx.headers))
        return
    for key, value in (("status", eval_status), ("createdFor", created_for), ("eventId", event_id)):
        if value is not None:
            params[key] = value
    if general is not None:
        params["general"] = "true" if general else "false"
    print_response(httpx.get(f"{ctx.base_url}/v1/evaluations", params=params, headers=ctx.headers))


@evaluations.command("get")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluations_get(ctx: Context, evaluation_id: int):
    """One of my evaluations, whole: front matter and every answer, private ones included.

    Example: evaluations get 5
    """
    print_response(httpx.get(f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}", headers=ctx.headers))


@evaluations.command("create")
@click.argument("json_input")
@pass_context
def evaluations_create(ctx: Context, json_input: str):
    """Start a draft (coach): templateId, createdFor; optional eventId, periodStart, periodEnd.

    No eventId means a general evaluation. periodStart / periodEnd are local
    ISO times (or periodStartUtc / periodEndUtc in ms), both or neither; the
    end may equal the start but not lie in the future (PERIOD_IN_FUTURE). A
    coach holds one live review per member, template and period
    (DUPLICATE_EVALUATION).

    Example: evaluations create '{"templateId": 1, "createdFor": "u1"}'
    Example: evaluations create '{"templateId": 1, "createdFor": "u1", "eventId": 9}'
    """
    data = convert_timestamps(parse_json_input(json_input))
    print_response(httpx.post(f"{ctx.base_url}/v1/evaluations", json=data, headers=ctx.headers))


@evaluations.command("update")
@click.argument("evaluation_id", type=int)
@click.argument("json_input")
@pass_context
def evaluations_update(ctx: Context, evaluation_id: int, json_input: str):
    """Change a draft's event, its period, or both: eventId, periodStart, periodEnd.

    An omitted field keeps its value. eventId null makes the draft general;
    periodStart and periodEnd go together, both null clearing the period.
    Eligibility is checked again against the resulting event and period
    (NOT_ELIGIBLE, EVENT_NOT_FOUND). The member is fixed; answers change with
    `evaluations answers`.

    Example: evaluations update 5 '{"periodStart": "2026-09-01T00:00", "periodEnd": "2026-09-30T00:00"}'
    Example: evaluations update 5 '{"periodStartUtc": null, "periodEndUtc": null}'
    Example: evaluations update 5 '{"eventId": 9}'
    Example: evaluations update 5 '{"eventId": null}'
    """
    raw = parse_json_input(json_input)
    other = sorted(set(raw) - EVALUATION_DRAFT_FIELDS)
    if other:
        raise click.UsageError(
            f"only the event and the period can change ({', '.join(other)} cannot); "
            "answers change with `evaluations answers put`."
        )
    print_response(httpx.patch(
        f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}", json=convert_timestamps(raw), headers=ctx.headers,
    ))


@evaluations.command("delete")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluations_delete(ctx: Context, evaluation_id: int):
    """Soft-delete a draft (`trash restore evaluation` undoes it).

    Example: evaluations delete 5
    """
    print_response(httpx.delete(f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}", headers=ctx.headers))


@evaluations.command("pdf")
@click.argument("evaluation_id", type=int)
@click.option("--output", "-o", help="Output file path (default: evaluation-<id>.pdf)")
@pass_context
def evaluations_pdf(ctx: Context, evaluation_id: int, output: str | None):
    """Preview the member copy as a PDF, at any status (owner); never stored.

    The copy the member keeps is stored on publish under the media tag
    member_copy (`myevaluations media <id>`).

    Example: evaluations pdf 5 -o preview.pdf
    """
    response = httpx.get(f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/pdf", headers=ctx.headers)
    if response.status_code != 200:
        print_response(response)
        return
    path = output or f"evaluation-{evaluation_id}.pdf"
    with open(path, "wb") as f:
        f.write(response.content)
    click.echo(f"Downloaded to {path}")


@evaluations.group("answers")
def evaluation_answers():
    """A draft's answers, written and cleared one item at a time."""


@evaluation_answers.command("put")
@click.argument("evaluation_id", type=int)
@click.argument("item_id", type=int)
@click.argument("answer_json")
@pass_context
def evaluation_answers_put(ctx: Context, evaluation_id: int, item_id: int, answer_json: str):
    """Write one answer, replacing any earlier one: valueNum, valueText, choices, coachNote.

    Which value applies is the item's type: valueNum for rating, number and
    yesNo (1 yes, 0 no), valueText for a single choice (the choice's value)
    and for qa, choices for multiple choice.

    Example: evaluations answers put 5 7 '{"valueNum": 4, "coachNote": "Strong edges"}'
    """
    print_response(httpx.put(
        f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/answers/{item_id}",
        json=parse_json_input(answer_json), headers=ctx.headers,
    ))


@evaluation_answers.command("clear")
@click.argument("evaluation_id", type=int)
@click.argument("item_id", type=int)
@pass_context
def evaluation_answers_clear(ctx: Context, evaluation_id: int, item_id: int):
    """Clear one answer, detaching its evidence.

    Example: evaluations answers clear 5 7
    """
    print_response(httpx.delete(
        f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/answers/{item_id}", headers=ctx.headers,
    ))


@evaluations.group("evidence")
def evaluation_evidence():
    """Files uploaded as evidence for one question of a draft."""


@evaluation_evidence.command("upload")
@click.argument("evaluation_id", type=int)
@click.argument("item_id", type=int)
@click.argument("filepath")
@pass_context
def evaluation_evidence_upload(ctx: Context, evaluation_id: int, item_id: int, filepath: str):
    """Upload a file as evidence for one question of a draft (owner); prints the evaluation.

    The question must allow evidence, and the file be an image, a video or a
    PDF (its content type is guessed from its name). The server stores it as
    the member's, readable by the member and staff only, never public; the
    member sees it once the evaluation is published, under the question's id
    in `myevaluations media`.

    Example: evaluations evidence upload 5 7 ./edges.png
    """
    path = Path(filepath)
    if not path.is_file():
        raise click.ClickException(f"File not found: {filepath}")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    print_response(httpx.post(
        f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/evidence/{item_id}",
        files={"file": (path.name, path.read_bytes(), content_type)},
        headers=ctx.headers,
    ))


def _evaluation_verb(ctx: Context, evaluation_id: int, verb: str) -> None:
    print_response(httpx.post(
        f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/{verb}", headers=ctx.headers,
    ))


@evaluations.command("save")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluations_save(ctx: Context, evaluation_id: int):
    """Draft → saved, once every required answer is in. Not yet visible to the member.

    Example: evaluations save 5
    """
    _evaluation_verb(ctx, evaluation_id, "save")


@evaluations.command("publish")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluations_publish(ctx: Context, evaluation_id: int):
    """Saved → published: the only step that shows it to the member, who is notified.

    Stores the member copy, a PDF, under the media tag member_copy.

    Example: evaluations publish 5
    """
    _evaluation_verb(ctx, evaluation_id, "publish")


@evaluations.command("unpublish")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluations_unpublish(ctx: Context, evaluation_id: int):
    """Published → saved: withdrawn from the member, who is told.

    Example: evaluations unpublish 5
    """
    _evaluation_verb(ctx, evaluation_id, "unpublish")


@evaluations.command("revert")
@click.argument("evaluation_id", type=int)
@pass_context
def evaluations_revert(ctx: Context, evaluation_id: int):
    """Saved → draft.

    Example: evaluations revert 5
    """
    _evaluation_verb(ctx, evaluation_id, "revert")


@evaluations.command("transfer")
@click.argument("evaluation_id", type=int)
@click.argument("owner")
@pass_context
def evaluations_transfer(ctx: Context, evaluation_id: int, owner: str):
    """Hand an unpublished evaluation to another coach, its new owner.

    Its owner may, or an admin naming it by id. Nothing is returned: after a
    transfer only the new owner sees it. A coach who already holds a live
    review of the same member, template and period cannot receive it
    (DUPLICATE_EVALUATION).

    Example: evaluations transfer 5 coach_b
    """
    print_response(httpx.post(
        f"{ctx.base_url}/v1/evaluations/by_id/{evaluation_id}/transfer",
        json={"owner": owner},
        headers=ctx.headers,
    ))


@main.group()
def myevaluations():
    """Evaluations published about me (or, for a coach, about USERNAME)."""
    pass


@myevaluations.command("list")
@click.option("--username", help="Whose evaluations (default: the signed-in user; coaches only)")
@click.option("--offset", default=0, help="Pagination offset")
@click.option("--limit", default=50, help="Pagination limit")
@pass_context
def myevaluations_list(ctx: Context, username: str | None, offset: int, limit: int):
    """List published evaluations about me, as the member sees them.

    Example: myevaluations list
    """
    target = username or ctx.username
    print_response(httpx.get(
        f"{ctx.base_url}/v1/myevaluations/by_id/{target}",
        params={"offset": offset, "limit": limit},
        headers=ctx.headers,
    ))


@myevaluations.command("get")
@click.argument("evaluation_id", type=int)
@click.option("--username", help="Whose evaluation (default: the signed-in user; coaches only)")
@pass_context
def myevaluations_get(ctx: Context, evaluation_id: int, username: str | None):
    """One published evaluation about me: its public items and their answers.

    Example: myevaluations get 5
    """
    target = username or ctx.username
    print_response(httpx.get(
        f"{ctx.base_url}/v1/myevaluations/by_id/{target}/{evaluation_id}", headers=ctx.headers,
    ))


@myevaluations.command("media")
@click.argument("evaluation_id", type=int)
@click.option("--username", help="Whose evaluation (default: the signed-in user; coaches only)")
@pass_context
def myevaluations_media(ctx: Context, evaluation_id: int, username: str | None):
    """Media of one published evaluation about me, by tag: evidence and member_copy.

    Evidence is tagged with its question's id; member_copy is the stored PDF
    (`upload download <uuid> -o copy.pdf`).

    Example: myevaluations media 5
    """
    target = username or ctx.username
    print_response(httpx.get(
        f"{ctx.base_url}/v1/myevaluations/by_id/{target}/{evaluation_id}/media", headers=ctx.headers,
    ))


# ── Media access roles ──────────────────────────────────────────────────


@upload.command("set-access")
@click.argument("media_id", type=int)
@click.option(
    "--role", "roles", multiple=True, required=True,
    type=click.Choice(["public", "self", "admin", "coach"]),
    help="Who may see the file (repeatable). public wins over the rest; self is the uploader.",
)
@pass_context
def upload_set_access(ctx: Context, media_id: int, roles: tuple[str, ...]):
    """Who may see a file: public, or any of self, admin, coach.

    MEDIA_ID is the numeric id (`uploads add-file` prints it on stderr;
    `uploads list` and `media myfiles` show it next to the uuid).

    Example: upload set-access 42 --role coach --role admin
    Example: upload set-access 42 --role public
    """
    print_response(httpx.patch(
        f"{ctx.base_url}/v1/media/by_id/{media_id}",
        json={"accessRoles": list(roles)},
        headers=ctx.headers,
    ))


if __name__ == "__main__":
    main()
