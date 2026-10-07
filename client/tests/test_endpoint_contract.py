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

A command can call the right endpoint and still leave out what the endpoint
takes: `users list` reached GET /v1/users for months without the role filter,
the search term or the sort order (club_cli#6). So the second half of this
file goes one level down: every query parameter an operation takes, and every
field of a request body a command builds itself, must be sent by a command.
"""
from __future__ import annotations

import ast
import collections
import functools
import re
from dataclasses import dataclass, field
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

# Query parameters and body fields no command sends, each with its reason:
# (method, path, name). A request the CLI wraps in full has no entry here.
UNSENT_BY_DESIGN: dict[tuple[str, str, str], str] = {
    # The download handler also serves `/download/{filename}`, where the
    # filename is a path segment; on the plain path the same argument shows
    # up as a query parameter. It is decorative on both (club_server#424).
    ("GET", "/v1/media/by_id/{}/download", "filename"): "decorative filename of the alias route",
    ("HEAD", "/v1/media/by_id/{}/download", "filename"): "decorative filename of the alias route",
}

# Parameters and fields an open issue already owns an option for, each naming
# its issue. The entry goes when the issue lands (a test below fails once a
# command sends it), so nothing here can quietly become permanent.
PARAMETERS_CLAIMED_BY_OPEN_ISSUES: dict[tuple[str, str, str], str] = {}

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
def server_schema() -> dict:
    try:
        r = httpx.get(f"{BASE_URL}/openapi.json", timeout=15)
    except httpx.HTTPError as exc:
        unavailable(f"server schema unreachable at {BASE_URL}: {exc}")
    if r.status_code != 200:
        unavailable(f"server schema returned {r.status_code}")
    return r.json()


@pytest.fixture(scope="module")
def server_ops(server_schema: dict) -> set[tuple[str, str]]:
    return {
        (method.upper(), _normalise(path))
        for path, item in server_schema["paths"].items()
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


@functools.cache
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


# ── Parameters and body fields ──────────────────────────────────────────
#
# A call site is one line; what the request carries is decided by the command
# around it. Each call site is placed in its function, each function in the
# commands that run it, and a command is taken to send every name it writes
# as a string: the key of a dict, `params["name"]`, a ("name", value) pair in
# a loop, or a keyword handed to `.update()` or to a helper taking **fields.
# A name only read (`media["filename"]`, `data.get("status")`) is not sent.
# That still reads a little more than it should (a name written for any
# other reason counts as sent), which errs toward passing;
# `test_parameter_scanner_sees_what_commands_send` pins what it must see and
# must not.

# Functions that hand the caller's own JSON to the server. A body built this
# way carries whatever the user wrote, so its fields are not checked.
PASSES_JSON_THROUGH = {"parse_json_input", "_parse_preference_value"}


@dataclass
class _Function:
    file: str
    name: str
    first_line: int
    last_line: int
    written: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    takes_any_keyword: bool = False
    click: tuple[str, str, str] | None = None  # ("command" | "group", parent's function, name)

    @property
    def passes_json_through(self) -> bool:
        return bool(self.calls & PASSES_JSON_THROUGH)


def _read_function(file: str, node: ast.FunctionDef) -> _Function:
    fn = _Function(file, node.name, node.lineno, node.end_lineno or node.lineno)
    fn.takes_any_keyword = node.args.kwarg is not None
    for dec in node.decorator_list:
        if (
            isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in ("command", "group") and isinstance(dec.func.value, ast.Name)
        ):
            named = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else None
            fn.click = (dec.func.attr, dec.func.value.id, named or node.name.replace("_", "-"))
    docstring = node.body[0].value if ast.get_docstring(node) is not None else None
    in_fstring = {id(part) for n in ast.walk(node) if isinstance(n, ast.JoinedStr) for part in ast.walk(n)}
    # `x["name"]` read, `x.get("name")`, `x.pop("name")`: looked up, not sent.
    read_only = {
        id(n.slice) for n in ast.walk(node)
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load)
    } | {
        id(n.args[0]) for n in ast.walk(node)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) in ("get", "pop") and n.args
    }
    keyword_calls: list[tuple[str, list[str]]] = []
    for body_node in node.body:
        for n in ast.walk(body_node):
            if (
                isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not docstring
                and id(n) not in in_fstring and id(n) not in read_only
            ):
                fn.written.add(n.value)
            elif isinstance(n, ast.Call):
                callee = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None)
                if callee:
                    fn.calls.add(callee)
                    keyword_calls.append((callee, [k.arg for k in n.keywords if k.arg]))
    fn._keyword_calls = keyword_calls  # type: ignore[attr-defined]
    return fn


@functools.cache
def _functions() -> dict[str, list[_Function]]:
    """Every function in the client source, by name."""
    found: dict[str, list[_Function]] = collections.defaultdict(list)
    for file in CLIENT_ROOT.rglob("*.py"):
        if "tests" in file.parts or ".venv" in file.parts:
            continue
        rel = str(file.relative_to(CLIENT_ROOT))
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.FunctionDef):
                found[node.name].append(_read_function(rel, node))
    # A keyword counts as written when it lands in a dict: `.update(a=1)`,
    # `dict(a=1)`, or a helper that takes **fields and builds the params.
    for fns in list(found.values()):
        for fn in fns:
            for callee, keywords in fn._keyword_calls:  # type: ignore[attr-defined]
                if callee in ("update", "dict") or any(t.takes_any_keyword for t in found.get(callee, [])):
                    fn.written.update(keywords)
    return found


def _command_name(fn: _Function) -> str:
    """`users list`, `event marketing set`: the words a user types."""
    assert fn.click is not None
    _, parent, name = fn.click
    if parent == "main":
        return name
    (owner,) = [f for f in _functions()[parent] if f.file == fn.file and f.click]
    return f"{_command_name(owner)} {name}"


@dataclass
class _Sender:
    """One command that reaches an operation, and what it writes on the way."""
    command: str
    written: set[str]
    passes_json_through: bool


def _senders(where: str) -> list[_Sender]:
    """The commands behind a call site (`cli.py:812`).

    The line sits in a command, or in a helper (`MediaApi.list_uploads`)
    that commands call; each command is read together with the helpers it
    calls, since either may be the one that names a parameter.
    """
    functions = _functions()
    file, line = where.rsplit(":", 1)
    inside = [
        f for fns in functions.values() for f in fns
        if f.file == file and f.first_line <= int(line) <= f.last_line
    ]
    if not inside:
        return []
    start = max(inside, key=lambda f: f.first_line)

    commands: list[_Function] = []
    seen: set[int] = set()
    todo = [start]
    while todo:
        fn = todo.pop()
        if id(fn) in seen:
            continue
        seen.add(id(fn))
        if fn.click and fn.click[0] == "command":
            commands.append(fn)
        elif not fn.click:
            todo += [f for fns in functions.values() for f in fns if fn.name in f.calls]

    senders = []
    for command in commands:
        run: list[_Function] = []
        todo = [command]
        while todo:
            fn = todo.pop()
            if fn in run:
                continue
            run.append(fn)
            todo += [t for callee in fn.calls for t in functions.get(callee, []) if not t.click]
        senders.append(_Sender(
            _command_name(command),
            set().union(*(f.written for f in run)),
            any(f.passes_json_through for f in run),
        ))
    return senders


def _body_fields(schema: dict, operation: dict) -> list[str]:
    """Top-level fields of the operation's request body, JSON or form."""
    def resolve(node: dict | None) -> dict:
        if not node:
            return {}
        if "$ref" in node:
            return resolve(schema["components"]["schemas"][node["$ref"].split("/")[-1]])
        for alternative in node.get("anyOf", []):
            resolved = resolve(alternative)
            if resolved.get("properties"):
                return resolved
        return node

    fields: list[str] = []
    for content in (operation.get("requestBody") or {}).get("content", {}).values():
        fields += [name for name in resolve(content.get("schema")).get("properties", {}) if name not in fields]
    return fields


@dataclass
class _Parameter:
    method: str
    path: str  # normalised
    name: str
    kind: str  # "query parameter" | "body field"
    commands: list[str]
    sent: bool

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.method, self.path, self.name)


def _parameters(schema: dict) -> list[_Parameter]:
    """Every query parameter and built-body field the server takes, and whether a command sends it.

    Several commands may reach one operation (`uploads add-file` and `media
    attach` both post a file); a parameter is sent when any of them sends it.
    """
    sites = _call_sites()
    found: list[_Parameter] = []
    for raw_path, item in schema["paths"].items():
        for verb, operation in item.items():
            method, path = verb.upper(), _normalise(raw_path)
            if method not in VERBS:
                continue
            senders = {
                sender.command: sender
                for site, wheres in sites.items() if _covers(site, (method, path))
                for where in wheres
                for sender in _senders(where)
            }
            if not senders:
                continue  # no command at all: the path tests above report that
            written = set().union(*(s.written for s in senders.values()))
            commands = sorted(senders)
            for parameter in operation.get("parameters", []):
                if parameter["in"] == "query":
                    name = parameter["name"]
                    found.append(_Parameter(method, path, name, "query parameter", commands, name in written))
            if not any(s.passes_json_through for s in senders.values()):
                for name in _body_fields(schema, operation):
                    found.append(_Parameter(method, path, name, "body field", commands, name in written))
    return found


def _skipped_parameters() -> dict[tuple[str, str, str], str]:
    return {
        (m, _normalise(p), name): why
        for (m, p, name), why in (*UNSENT_BY_DESIGN.items(), *PARAMETERS_CLAIMED_BY_OPEN_ISSUES.items())
    }


def test_parameter_scanner_sees_what_commands_send(server_schema: dict) -> None:
    """Guard the scanner itself: one that reads everything as sent, or nothing, proves nothing."""
    parameters = {(p.method, p.path, p.name): p for p in _parameters(server_schema)}
    queries = [p for p in parameters.values() if p.kind == "query parameter"]
    fields = [p for p in parameters.values() if p.kind == "body field"]
    assert len(queries) > 100, f"only {len(queries)} query parameters were placed in a command"
    assert len(fields) > 50, f"only {len(fields)} body fields were placed in a command"

    def sent(method: str, path: str, name: str) -> bool:
        return parameters[(method, _normalise(path), name)].sent

    # A dict literal, a ("name", value) loop, a helper taking **fields, a
    # helper class in another file, and a body built from options.
    assert sent("GET", "/v1/users", "limit")
    assert sent("GET", "/v1/audit_log", "resource_type")
    assert sent("GET", "/v1/credits/entries", "entryType")
    assert sent("GET", "/v1/media", "conversionStatus")
    assert sent("PATCH", "/v1/notifications/preferences", "pushEnabled")
    # Not sent, and listed as such.
    assert not sent("GET", "/v1/media/by_id/{}/download", "filename")
    # A command that passes the user's JSON through has no fields to check.
    assert ("POST", "/v1/events", "title") not in parameters
    # The failure message can name the command.
    assert parameters[("GET", "/v1/users/deleted", "limit")].commands == ["trash list"]
    assert "uploads add-file" in parameters[("POST", "/v1/media", "encrypt")].commands


def test_every_parameter_and_built_body_field_is_sent(server_schema: dict) -> None:
    """The CLI wraps every option the server offers, not only every endpoint."""
    skipped = _skipped_parameters()
    missing = [p for p in _parameters(server_schema) if not p.sent and p.key not in skipped]
    assert not missing, (
        "the server takes these and no command sends them (add an option, or add to "
        "UNSENT_BY_DESIGN with a reason):\n" + "\n".join(
            f"  {p.method} {p.path}: {p.kind} `{p.name}` is not sent by "
            + " / ".join(f"`{c}`" for c in p.commands)
            for p in sorted(missing, key=lambda p: (p.path, p.method, p.name))
        )
    )


def test_parameter_skip_lists_hold_only_unsent_parameters(server_schema: dict) -> None:
    """An entry a command now sends must be removed, so the lists never go stale."""
    skipped = _skipped_parameters()
    sent = sorted(p.key for p in _parameters(server_schema) if p.sent and p.key in skipped)
    assert not sent, "listed as unsent but a command sends it:\n" + "\n".join(
        f"  {m} {p}: {name}" for m, p, name in sent
    )


def test_parameter_skip_lists_name_only_parameters_the_server_takes(server_schema: dict) -> None:
    """An entry the server has dropped must go too, so the list cannot rot."""
    known = {p.key for p in _parameters(server_schema)}
    gone = sorted(key for key in _skipped_parameters() if key not in known)
    assert not gone, "listed as unsent but the server no longer takes it:\n" + "\n".join(
        f"  {m} {p}: {name}" for m, p, name in gone
    )
