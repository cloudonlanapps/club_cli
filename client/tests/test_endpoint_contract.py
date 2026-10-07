"""Contract tests: the CLI's URLs must exist on the server.

The CLI has no generated SDK layer — every endpoint is a literal f-string at
its call site. Nothing else notices when the server moves an endpoint, so a
stale URL simply 404s at runtime inside whichever command happens to use it.
That is how `me gallery list`, `myevents stats` and the whole `club_admin
uploads` group went dead during the /uploaded → /media migration without a
single failing test.

These tests read the live schema and compare it against the call sites,
method by method: a path the CLI reads with GET says nothing about the PATCH
next to it.
"""
from __future__ import annotations

import collections
import re
from pathlib import Path

import httpx
import pytest

from .conftest import BASE_URL, unavailable

CLIENT_ROOT = Path(__file__).resolve().parent.parent

# Server operations the CLI does not call, each with its reason. The CLI wraps
# every feature the server offers, so this holds only what is not a feature.
UNCOVERED_BY_DESIGN = {
    # Infrastructure, not API surface.
    ("GET", "/"),
    ("GET", "/health"),
    # An alias of `/v1/media/by_id/{}/download` carrying a decorative filename
    # (club_server#424). The CLI *emits* this shape from `download_url` so the
    # URLs it prints end in a real extension; `upload download` fetches
    # through the plain path, which is the same handler.
    ("GET", "/v1/media/by_id/{}/download/{}"),
    ("HEAD", "/v1/media/by_id/{}/download/{}"),
}

# Operations an open issue already owns a command for. Each entry names its
# issue; the entry goes when that issue lands (a test below fails once the
# CLI covers it), so nothing here can quietly become permanent.
CLAIMED_BY_OPEN_ISSUES: set[tuple[str, str]] = set()

VERBS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
ANY = "*"


def _normalise(path: str) -> str:
    """Collapse path parameters so {venue_id} and {id} compare equal."""
    return re.sub(r"\{[^}]+\}", "{}", path.split("?")[0].rstrip("/")) or "/"


def _matches(call_site: str, server: str) -> bool:
    """Compare paths segment-wise, treating {} as a single-segment wildcard."""
    a, b = call_site.split("/"), server.split("/")
    if len(a) != len(b):
        return False
    return all(x == y or "{}" in (x, y) for x, y in zip(a, b))


@pytest.fixture(scope="module")
def server_ops() -> set[tuple[str, str]]:
    try:
        r = httpx.get(f"{BASE_URL}/openapi.json", timeout=15)
    except httpx.HTTPError as exc:
        unavailable(f"server schema unreachable at {BASE_URL}: {exc}")
    if r.status_code != 200:
        unavailable(f"server schema returned {r.status_code}")
    return {
        (method.upper(), _normalise(path))
        for path, item in r.json()["paths"].items()
        for method in item
        if method.upper() in VERBS
    }


# Collections a call site names through a variable, and what the variable can
# hold. Expanding them keeps `/v1/{owner}/...` from matching half the API.
def _collection_values() -> dict[str, list[str]]:
    from club_client.cli import OWNER_PATHS, RESTORE_PATHS, TRASH_PATHS

    return {
        "OWNER_PATHS[owner_type]": sorted(OWNER_PATHS.values()),
        "owner": sorted(OWNER_PATHS.values()),
        "coll": sorted(TRASH_PATHS.values()),
        "restore_coll": sorted(RESTORE_PATHS.values()),
    }


# Helpers that build an endpoint from a literal argument: (method, template).
# Their definitions are skipped and each call is expanded instead, since a
# definition alone (`/v1/events/by_id/{id}/{verb}`) would match every verb.
HELPERS = {
    "patch_event": ("PATCH", "/v1/events/by_id/{}{arg}"),
    "post_event_verb": ("POST", "/v1/events/by_id/{}/{arg}"),
    "_evaluation_verb": ("POST", "/v1/evaluations/by_id/{}/{arg}"),
}


def _statement_methods(lines: list[str], i: int) -> set[str]:
    """The HTTP method(s) of the call on or around line i, or {ANY}."""
    window = "\n".join(lines[max(0, i - 4): i + 1])
    if "httpx.head if head else httpx.get" in window:
        return {"HEAD", "GET"}
    line = lines[i]
    assigned = re.match(r"\s*(\w+)\s*=\s*f", line)
    if assigned:
        # `url = f"..."`, used by the first httpx call on that name after it.
        for later in lines[i + 1: i + 6]:
            m = re.search(rf"httpx\.(\w+)\(\s*{assigned.group(1)}\b", later)
            if m:
                return {m.group(1).upper()}
        return {ANY}
    if "_post_file(" in line:
        return {"POST"}
    found = re.findall(r"httpx\.(get|head|post|put|patch|delete)\(", window)
    return {found[-1].upper()} if found else {ANY}


def _url_helpers(text: str) -> dict[str, tuple[int, list[str]]]:
    """Functions that return an endpoint URL (`-> str`): required args, paths built.

    A helper with an optional id builds two paths, e.g. the collection and
    one member of it; a call that passes the optional argument gets the
    longer one.
    """
    helpers: dict[str, tuple[int, list[str]]] = {}
    for m in re.finditer(r"^def (\w+)\(([^)]*)\)\s*->\s*str:\n((?:[ \t]+.*\n|\n)+)", text, re.M):
        paths = re.findall(r"\{(?:ctx\.|self\.)?base_url\}(/[^\"'\s]*)", m.group(3))
        if paths:
            required = sum(1 for p in m.group(2).split(",") if p.strip() and "=" not in p)
            helpers[m.group(1)] = (required, sorted(set(paths), key=lambda p: p.count("/")))
    return helpers


def _call_sites() -> dict[tuple[str, str], list[str]]:
    """Every endpoint the client source calls, as (method, path) → sites.

    Anchoring on the base_url interpolation keeps prose out: docstrings
    mention endpoints constantly, and matching bare "/v1/" swept them all in.
    """
    collections_ = _collection_values()
    found: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for file in CLIENT_ROOT.rglob("*.py"):
        if "tests" in file.parts:
            continue
        rel = file.relative_to(CLIENT_ROOT)
        text = file.read_text()
        lines = text.splitlines()
        for helper, (method, template) in HELPERS.items():
            # Calls often wrap onto the next line, so match across newlines.
            for m in re.finditer(rf"\b{helper}\(\s*ctx,\s*\w+,\s*\"([^\"]*)\"", text):
                lineno = text.count("\n", 0, m.start()) + 1
                found[(method, _normalise(template.replace("{arg}", m.group(1))))].append(f"{rel}:{lineno}")
        url_helpers = _url_helpers(text)
        for name, (required, paths) in url_helpers.items():
            # The method is at the call: httpx.put(_marketing_url(...)).
            for m in re.finditer(rf"httpx\.(\w+)\(\s*{name}\(([^)]*)\)", text):
                lineno = text.count("\n", 0, m.start()) + 1
                passed = sum(1 for a in m.group(2).split(",") if a.strip())
                path = paths[-1] if passed > required else paths[0]
                for variant in _expand(path, collections_):
                    found[(m.group(1).upper(), _normalise(variant))].append(f"{rel}:{lineno}")
        in_helper = False
        for lineno, line in enumerate(lines, 1):
            if line.startswith("def "):
                in_helper = any(line.startswith(f"def {h}(") for h in (*HELPERS, *url_helpers))
            if in_helper:
                continue
            for m in re.finditer(r"\{(?:ctx\.|self\.)?base_url\}(/[^\"'\s]*)", line):
                for method in _statement_methods(lines, lineno - 1):
                    for path in _expand(m.group(1), collections_):
                        found[(method, _normalise(path))].append(f"{rel}:{lineno}")
    return found


def _expand(raw: str, collections_: dict[str, list[str]]) -> list[str]:
    """A path naming its collection through a variable, once per value."""
    for var, values in collections_.items():
        if f"/v1/{{{var}}}/" in raw:
            return [raw.replace(f"{{{var}}}", v) for v in values]
    return [raw]


def _covers(site: tuple[str, str], op: tuple[str, str]) -> bool:
    return site[0] in (op[0], ANY) and _matches(site[1], op[1])


def test_call_sites_were_found() -> None:
    """Guard the scanner itself: a regex that matches nothing passes vacuously."""
    sites = _call_sites()
    assert len(sites) > 150, f"scanner found only {len(sites)} call sites; regex likely broken"
    unknown = sorted(p for m, p in sites if m == ANY)
    assert len(unknown) < 5, f"too many call sites with no recognisable method: {unknown}"


def test_no_dead_endpoints(server_ops: set[tuple[str, str]]) -> None:
    """Every (method, endpoint) the CLI calls must exist on the server."""
    dead = {
        site: where
        for site, where in _call_sites().items()
        if not any(_covers(site, op) for op in server_ops)
    }
    assert not dead, "CLI calls operations the server does not serve:\n" + "\n".join(
        f"  {m} {p}\n      {', '.join(where)}" for (m, p), where in sorted(dead.items())
    )


def test_every_server_operation_is_covered(server_ops: set[tuple[str, str]]) -> None:
    """The CLI wraps every feature the server offers, method by method."""
    sites = set(_call_sites())
    skipped = {(m, _normalise(p)) for m, p in (*UNCOVERED_BY_DESIGN, *CLAIMED_BY_OPEN_ISSUES)}
    missing = sorted(
        op for op in server_ops
        if op not in skipped and not any(_covers(site, op) for site in sites)
    )
    assert not missing, (
        "server operations with no CLI command (add a command, or add to "
        "UNCOVERED_BY_DESIGN with a reason):\n" + "\n".join(f"  {m} {p}" for m, p in missing)
    )


def test_skip_lists_hold_only_uncovered_endpoints() -> None:
    """An entry the CLI now covers must be removed, so the lists never go stale.

    Exact matches only: a wildcard call site says nothing about a command existing.
    """
    sites = set(_call_sites())
    covered = sorted(
        (m, p) for m, p in (*UNCOVERED_BY_DESIGN, *CLAIMED_BY_OPEN_ISSUES)
        if (m, _normalise(p)) in sites
    )
    assert not covered, "listed as uncovered but a command calls it:\n" + "\n".join(
        f"  {m} {p}" for m, p in covered
    )


def test_skip_lists_name_only_operations_the_server_serves(server_ops: set[tuple[str, str]]) -> None:
    """An entry the server has dropped must go too, so the list cannot rot.

    The SDK's contract test holds the same rule (its test 0.03).
    """
    gone = sorted(
        (m, p) for m, p in (*UNCOVERED_BY_DESIGN, *CLAIMED_BY_OPEN_ISSUES)
        if (m, _normalise(p)) not in server_ops
    )
    assert not gone, "listed as uncovered but the server no longer serves it:\n" + "\n".join(
        f"  {m} {p}" for m, p in gone
    )
